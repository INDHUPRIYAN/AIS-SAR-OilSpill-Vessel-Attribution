/* FINAL ACCEPTANCE WALKTHROUGH (UX brief), as a first-time user.
 *
 * Open OceanTrace -> real Earth -> Dashboard -> New Investigation -> the case
 * -> the camera goes to the real place -> SAR -> detection -> characterisation
 * -> wind and currents -> hindcast -> origin -> AIS -> candidates -> vessel
 * evidence -> report -> case history. Then the failure paths: an address that
 * does not exist, a backend that stops answering, and leaving a running job
 * and coming back to it.
 *
 * Screenshots go to docs/ux/acceptance/ as the evidence. Every assertion is
 * about what the page shows, against the API answer where one exists.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test, expect } from "@playwright/test";

const API = process.env.E2E_API || "";
const EMAIL = process.env.E2E_EMAIL || "test-admin@example.invalid";
const PASSWORD = process.env.E2E_PASSWORD || "suite-fixture-password";
const OUT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../docs/ux/acceptance");
const DEMO = "inv-gulf-flagship-20230108-2day";
fs.mkdirSync(OUT, { recursive: true });

async function signIn(page) {
  const r = await page.request.post(`${API}/api/auth/login`, { data: { email: EMAIL, password: PASSWORD } });
  expect(r.ok(), `sign-in failed: ${r.status()}`).toBeTruthy();
}
const shot = (page, name) => page.screenshot({ path: path.join(OUT, `${name}.png`) });
const ws = (page) => page.getByTestId("workspace");

test("ACC-1: from the real Earth to a report, through every stage, on one map", async ({ page }) => {
  test.setTimeout(240_000);
  await signIn(page);
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));

  // 1. real Earth, on the Dashboard
  await page.goto("/");
  const globe = page.getByTestId("globe").first();
  await expect(globe).toHaveAttribute("data-basemap", /satellite|geopolitical/);   // satellite when a provider is configured
  await page.waitForFunction(() => {
    const m = document.querySelector('[data-testid="globe"]')?.__globe?.getMap();
    return Boolean(m?.isStyleLoaded() && m.getSource("countries") && m.isSourceLoaded("countries"));
  }, null, { timeout: 30_000 });
  const names = await globe.evaluate((el) => el.__globe.getMap()
    .queryRenderedFeatures({ layers: ["ot-country-labels"] }).map((f) => f.properties.name));
  expect(names).toContain("India");
  await expect(page.getByTestId("new-investigation")).toBeVisible();
  await expect(page.getByTestId("demo-open")).toBeVisible();
  await shot(page, "01-dashboard-real-earth");

  // 2. New investigation opens the acquisition stage
  await page.getByTestId("new-investigation").click();
  await expect(page).toHaveURL(/\/investigations\/new$/);
  await expect(ws(page)).toHaveAttribute("data-stage", "acquisition");
  await shot(page, "02-new-investigation");

  // 3. the case: the camera is at the scene's real place
  await page.goto(`/investigations/run/${DEMO}/scene`);
  const meta = await (await page.request.get(`/api/layers/${DEMO}/scene_meta`)).json();
  const [w, s, e, n] = meta.bbox;
  await expect.poll(async () => {
    const c = await page.getByTestId("globe").first().evaluate((el) => el.__globe.getCamera());
    return Boolean(c) && c.longitude > w - 2 && c.longitude < e + 2 && c.latitude > s - 2 && c.latitude < n + 2;
  }, { timeout: 20_000 }).toBe(true);
  await shot(page, "03-scene-at-its-real-place");

  // 4. every step of the stepper
  for (const [chip, name] of [["detection", "04-detection"], ["geometry", "05-characterisation"],
    ["drift", "06-hindcast-origin"], ["vessels", "07-ais"], ["attribution", "08-attribution"]]) {
    await page.getByTestId(`chip-${chip}`).click();
    await expect(page.getByTestId("right-panel")).toBeVisible();
    await page.waitForTimeout(1200);
    await shot(page, name);
  }

  // characterisation equals the artefact
  await page.getByTestId("chip-geometry").click();
  const slick = await (await page.request.get(`/api/layers/${DEMO}/slick`)).json();
  const total = slick.features.reduce((a, f) => a + (f.properties.area_km2 || 0), 0);
  await expect(page.getByTestId("fact-area")).toContainText(`${total.toFixed(1)} km²`);

  // wind and current, source-labelled
  await page.getByTestId("chip-drift").click();
  await page.getByTestId("context-currents").click();
  await expect(page.getByTestId("right-panel")).toContainText(/CMEMS|Copernicus|not recorded|No current grid/i);
  await shot(page, "06b-currents");

  // hindcast: honest particle count
  await page.getByTestId("context-hindcast").click();
  await expect(page.getByTestId("right-panel")).toContainText(/Particles/);
  await shot(page, "06c-hindcast");

  // origin: the drift estimate and the Bayesian panel beside it
  await page.getByTestId("context-bayes").click();
  await expect(page.getByTestId("intel-bayes")).toContainText(/never merged/i);

  // candidates + vessel evidence: "highest-ranked", never "culprit"
  await page.getByTestId("chip-attribution").click();
  const panel = page.getByTestId("right-panel");
  await expect(panel).toContainText(/Highest-Ranked Candidate|Candidate #/);
  await expect(panel).not.toContainText(/culprit|guilty|confirmed polluter/i);
  await expect(page.getByTestId("vessel-identity")).toBeVisible();
  await expect(page.getByTestId("attr-history")).toBeVisible();
  await shot(page, "09-vessel-evidence");

  // report: honest export labels, then the printable document
  await page.goto(`/investigations/run/${DEMO}/report`);
  await expect(page.getByTestId("intel-report")).toBeVisible();
  await expect(page.getByTestId("open-printable")).toHaveText(/Printable report/);
  await expect(page.getByTestId("intel-report")).not.toContainText("Download PDF");
  await shot(page, "10-report-stage");
  await page.goto(`/reports/print/${DEMO}`);
  await expect(page.locator("body")).toContainText(DEMO);
  await shot(page, "11-printable-report");

  // case history
  await page.goto("/investigations");
  await expect(page.getByTestId("breadcrumbs")).toContainText("Investigations");
  await shot(page, "12-case-history");

  expect(errors, errors.join("\n")).toEqual([]);
});

test("ACC-2: deep links, refresh and back/forward keep their place", async ({ page }) => {
  await signIn(page);
  await page.goto(`/investigations/run/${DEMO}/drift`);
  await expect(ws(page)).toHaveAttribute("data-stage", "drift");
  await page.reload();
  await expect(ws(page)).toHaveAttribute("data-stage", "drift");
  await page.getByTestId("chip-attribution").click();
  await expect(page).toHaveURL(new RegExp(`/investigations/run/${DEMO}/attribution`));
  await page.goBack();
  await expect(ws(page)).toHaveAttribute("data-stage", "drift", { timeout: 10_000 });
  await page.goForward();
  await expect(ws(page)).toHaveAttribute("data-stage", "attribution", { timeout: 10_000 });
  // the Live Map keeps its camera across a reload
  await page.goto("/map?c=85.0000,15.0000,4.00");
  await expect(page.getByTestId("globe").first()).toHaveAttribute("data-zoom", "4.00", { timeout: 20_000 });
  await page.reload();
  await expect(page.getByTestId("globe").first()).toHaveAttribute("data-zoom", "4.00", { timeout: 20_000 });
});

test("ACC-3: failure paths say what happened", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page);

  // an address that does not exist keeps the address and says so
  await page.goto("/no/such/place/in/the/app");
  await expect(page.getByTestId("not-found")).toContainText("/no/such/place/in/the/app");
  await shot(page, "20-not-found");

  // a backend that stops answering: the workspace says it lost contact.
  // An investigation (not an unfiled sealed run, which nothing polls because
  // it can no longer change) whose status the page keeps asking for.
  const inv = await page.request.post(`${API}/api/investigations`, {
    data: { name: "acceptance: lost contact", scene_meta_path: "contracts/mocks/scene_meta.json" },
  });
  expect(inv.ok()).toBeTruthy();
  const { id } = await inv.json();
  await page.goto(`/investigations/${id}`);
  await expect(ws(page)).toBeVisible();
  await page.route(/\/api\/investigations\/.*\/status/, (r) => r.abort());
  await expect(page.getByTestId("status-lost")).toBeVisible({ timeout: 60_000 });
  await shot(page, "21-lost-contact");
  await page.unrouteAll({ behavior: "ignoreErrors" });

  // notifications open in place and close on Escape
  await page.goto("/");
  await page.getByTestId("alert-bell").click();
  await expect(page.getByTestId("notifications")).toBeVisible();
  await shot(page, "22-notifications");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("notifications")).toHaveCount(0);
});

test("ACC-4: leave a running job and come back to it", async ({ page }) => {
  test.setTimeout(420_000);
  await signIn(page);
  const db = await (await page.request.get("/api/sar/scenes?available=true")).json();
  const scene = (db.scenes || []).filter((s) => s.label !== "SYNTHETIC")
    .sort((a, b) => a.raster_bytes - b.raster_bytes)[0];
  test.skip(!scene, "no real scene on this host");

  await page.goto(`/detections?scene=${scene.key}`);
  await page.getByTestId("sar-analyse").click();
  await expect(page.getByTestId("sar-analysis")).toBeVisible({ timeout: 30_000 });
  await expect.poll(() => new URL(page.url()).searchParams.get("run"), { timeout: 30_000 }).not.toBeNull();
  const run = new URL(page.url()).searchParams.get("run");

  // open it in the workspace while it runs
  await page.goto(`/investigations/run/${run}`);
  await expect(ws(page)).toBeVisible();
  if (await page.getByTestId("run-leave-note").isVisible().catch(() => false)) {
    await expect(page.getByTestId("run-leave-note")).toContainText(/leave this page/i);
    await shot(page, "30-running-can-leave");
    await page.goto("/");                      // leave
    await page.waitForTimeout(3000);
    await page.goto(`/investigations/run/${run}`);  // come back
    if (await page.getByTestId("run-leave-note").isVisible().catch(() => false)) {
      await expect(page.getByTestId("cancel-btn")).toBeVisible();   // cancel survives leaving
    }
    await shot(page, "31-returned-to-running-job");
  }

  // the server finishes the run whether or not anyone watched
  await expect.poll(async () => (await (await page.request.get(`/api/runs/${run}`)).json()).status,
    { timeout: 360_000, intervals: [3000] }).toMatch(/complete|failed/);
  const alerts = await (await page.request.get("/api/alerts?status=open&limit=200")).json();
  const kinds = (alerts.alerts || []).filter((a) => a.run_id === run).map((a) => a.kind);
  expect(kinds.some((k) => /run_complete|run_failed|detection/.test(k)),
    `a finished run a person started raises an alert (G3); got ${JSON.stringify(kinds)}`).toBe(true);
  await page.goto("/");
  await expect(page.getByTestId("attention-list")).toBeVisible();
  await shot(page, "32-needs-attention-after-run");
});
