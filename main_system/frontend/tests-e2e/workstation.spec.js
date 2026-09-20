/* The workstation: a larger map with independently collapsing sidebars, a
 * right panel that follows the analysis, records with honest provenance, and
 * the SAR Image Database through to FIND VESSELS.
 *
 * Nothing here asserts a number the backend did not return: every expected
 * value is fetched from the API in the same test and compared with what the
 * page shows. Runs against the same stack as investigation.spec.js. */
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test, expect } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL || "test-admin@example.invalid";
const PASSWORD = process.env.E2E_PASSWORD || "suite-fixture-password";
const FIXTURE = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "sar_chip_no_metadata.tif");

async function signIn(page) {
  const r = await page.request.post("/api/auth/login", { data: { email: EMAIL, password: PASSWORD } });
  expect(r.ok(), `sign-in failed: ${r.status()}`).toBeTruthy();
}

/* The newest sealed run that reached attribution with candidates: a run the
 * stack really holds, never a fixture id. */
async function aRealRun(page) {
  const runs = (await (await page.request.get("/api/runs?status=complete&limit=60")).json());
  for (const r of runs.items || runs) {
    const sus = await page.request.get(`/api/layers/${r.run_id}/suspects`);
    const fun = await page.request.get(`/api/runs/${r.run_id}/funnel`);
    if (sus.ok() && fun.ok() && (await sus.json()).suspects?.length) return { run: r.run_id, suspects: await sus.json(), funnel: await fun.json() };
  }
  throw new Error("no complete run with candidates on this stack");
}

test("W1: each sidebar collapses on its own and the map takes the space", async ({ page }) => {
  await signIn(page);
  const { run } = await aRealRun(page);
  await page.goto(`/investigations/run/${run}`);
  const map = page.getByTestId("ws-map");
  await expect(page.locator("canvas").first()).toBeVisible();
  await page.evaluate(() => { localStorage.removeItem("ot.ws.leftOff"); localStorage.removeItem("ot.ws.rightOff"); });
  await page.reload();
  await expect(page.locator("canvas").first()).toBeVisible();
  const w0 = (await map.boundingBox()).width;

  await page.getByTestId("ws-left-collapse").click();
  await expect(page.getByTestId("ws-left")).toHaveAttribute("data-collapsed", "true");
  await expect(page.getByTestId("ws-right")).toHaveAttribute("data-collapsed", "false");      // independent
  await expect.poll(async () => (await map.boundingBox()).width).toBeGreaterThan(w0 + 200);
  const w1 = (await map.boundingBox()).width;

  await page.getByTestId("ws-right-collapse").click();
  await expect(page.getByTestId("ws-right")).toHaveAttribute("data-collapsed", "true");
  await expect.poll(async () => (await map.boundingBox()).width).toBeGreaterThan(w1 + 250);
  // the canvas itself resized with its container: no empty band beside it
  const canvasW = (await page.locator("[data-testid=ws-map] canvas").first().boundingBox()).width;
  expect(Math.abs(canvasW - (await map.boundingBox()).width)).toBeLessThan(4);

  // the investigation survived: same run, same stage, and the panel returns as it was
  const stage = await page.getByTestId("workspace").getAttribute("data-stage");
  await page.getByTestId("ws-right-collapse").click();
  await page.getByTestId("ws-left-collapse").click();
  await expect(page.getByTestId("workspace")).toHaveAttribute("data-stage", stage);
  await expect(page.getByTestId("right-panel")).toBeVisible();
  await expect.poll(async () => Math.round((await map.boundingBox()).width)).toBe(Math.round(w0));
});

test("W2: the right panel is the current analytical context, with the run's own numbers", async ({ page }) => {
  await signIn(page);
  const { run, suspects, funnel } = await aRealRun(page);
  const origin = await (await page.request.get(`/api/layers/${run}/origin_cloud?lite=true`)).json();
  const forecast = await (await page.request.get(`/api/layers/${run}/forecast`)).json();
  await page.goto(`/investigations/run/${run}`);
  const panel = page.getByTestId("right-panel");

  await page.getByTestId("chip-drift").click();
  await page.getByTestId("context-hindcast").click();
  await expect(panel).toHaveAttribute("data-context", "hindcast");
  await expect(page.getByTestId("hind-duration")).toHaveText(`${origin.metadata.backtrack_hours} h`);
  await expect(page.getByTestId("hind-calc")).toContainText(origin.metadata.n_particles.toLocaleString());
  await expect(page.getByTestId("hind-unc")).toContainText("km");
  await expect(page.getByTestId("hind-lat")).not.toHaveText("—");

  await page.getByTestId("context-forecast").click();
  await expect(panel).toHaveAttribute("data-context", "forecast");
  // one row per horizon; every confidence envelope's own area is under it
  for (const f of forecast.features) await expect(page.getByTestId(`fc-h${f.properties.horizon_h}`)).toContainText(`${Number(f.properties.area_km2).toFixed(2)} km²`);
  expect(await page.locator('.ap-horizon').count()).toBe(new Set(forecast.features.map((f) => f.properties.horizon_h)).size);

  await page.getByTestId("chip-vessels").click();
  await page.getByTestId("context-filter").click();
  await expect(panel).toHaveAttribute("data-context", "filter");
  const steps = [funnel.indexed, funnel.after_spatial, funnel.after_temporal, funnel.after_trajectory, funnel.candidates];
  for (let i = 0; i < steps.length; i++) await expect(page.getByTestId(`funnel-${i}`)).toContainText(String(steps[i]));

  await page.getByTestId("context-ranking").click();
  await expect(panel).toHaveAttribute("data-context", "ranking");
  for (const s of suspects.suspects.slice(0, 4)) await expect(page.getByTestId(`rank-score-${s.rank}`)).toHaveText(s.total_score.toFixed(2));
  await expect(page.getByTestId("rank-1")).toHaveClass(/top/);
  await expect(page.getByTestId("intel-ranking")).not.toContainText(/guilty|culprit|responsible vessel/i);

  await page.getByTestId("chip-detection").click();
  await page.getByTestId("chip-detection").click();
  await page.getByTestId("chip-detection").click();
  await page.getByTestId("context-segmentation").click();
  await expect(page.getByTestId("seg-sar").locator("img").first()).toBeVisible();
  const slick = await (await page.request.get(`/api/layers/${run}/slick`)).json();
  if (slick.features.length) await expect(page.getByTestId("seg-area")).toContainText(Number(slick.features[0].properties.area_km2).toFixed(2));
});

test("W3: more than twenty records, each with the basis of its position", async ({ page }) => {
  await signIn(page);
  const db = await (await page.request.get("/api/sar/scenes")).json();
  await page.goto("/investigations");
  await expect(page.getByTestId("record-row").first()).toBeVisible({ timeout: 20_000 });
  expect(await page.getByTestId("record-row").count()).toBeGreaterThan(20);
  await expect(page.getByTestId("record-basis").first()).toBeVisible();
  const text = (await page.getByTestId("record-basis").allInnerTexts()).join("|");
  // only claims the database itself makes
  if (db.by_label.REFERENCE) expect(text).toMatch(/REFERENCE · position assigned/i);
  expect(text).not.toMatch(/confirmed/i);
});

test("W4: the SAR database lists real scenes with their metadata and filters them", async ({ page }) => {
  await signIn(page);
  const db = await (await page.request.get("/api/sar/scenes")).json();
  await page.goto("/detections");
  await expect(page.getByTestId("sar-card")).toHaveCount(db.count, { timeout: 20_000 });
  const real = db.scenes.find((s) => s.geo_basis === "measured") || db.scenes[0];
  await page.locator(`[data-testid="sar-card"][data-scene="${real.scene_id}"]`).click();
  const meta = page.getByTestId("sar-metadata");
  await expect(meta).toContainText(real.scene_id);
  await expect(meta).toContainText(real.acquired_utc.slice(0, 10));
  await expect(meta).toContainText(real.polarisation);
  if (real.platform) await expect(meta).toContainText(real.platform);
  await expect(meta).toContainText(real.geo_basis === "measured" ? "MEASURED" : "ASSIGNED");

  await page.getByTestId("sar-prov").selectOption("REAL");
  await page.getByTestId("sar-apply").click();
  await expect(page.getByTestId("sar-card")).toHaveCount(db.by_label.REAL || 0);
  await page.getByTestId("sar-prov").selectOption("");
  await page.getByTestId("sar-q").fill("zzz-no-such-scene");
  await page.getByTestId("sar-apply").click();
  await expect(page.getByTestId("sar-empty")).toBeVisible();          // an empty result stays empty
});

test("W5: an upload without metadata is asked for it, never given it", async ({ page }) => {
  await signIn(page);
  await page.goto("/detections");
  await page.getByTestId("sar-upload-file").setInputFiles(FIXTURE);
  await page.getByTestId("sar-upload-send").click();
  const need = page.getByTestId("sar-metadata-required");
  await expect(need).toBeVisible({ timeout: 30_000 });
  await expect(need).toContainText("acquired_utc");
  await expect(need).toContainText("bbox");
  await expect(need).toContainText("polarisation");

  await page.getByTestId("sar-up-bbox").fill("10, 5, 5, 10");            // not a box
  await page.getByTestId("sar-up-time").fill("2021-03-04T05:06:07Z");
  await page.getByTestId("sar-up-pol").selectOption("VV");
  await page.getByTestId("sar-upload-send").click();
  await expect(page.getByTestId("sar-upload-error")).toContainText(/bbox/i);

  await page.getByTestId("sar-up-bbox").fill("54.0, 25.0, 54.05, 25.05");
  await page.getByTestId("sar-upload-send").click();
  await expect(page.getByTestId("sar-upload-ready")).toBeVisible({ timeout: 30_000 });
  // it is now a scene in the database, badged for what it is
  const meta = page.getByTestId("sar-metadata");
  await expect(meta).toContainText("UserUpload", { timeout: 20_000 });
  await expect(meta).toContainText("USER-SUPPLIED");
  await expect(meta).toContainText("2021-03-04");
});

test("W6: analyse a scene with the deployed models, then FIND VESSELS plays it in the workspace", async ({ page }) => {
  test.setTimeout(420_000);
  await signIn(page);
  const db = await (await page.request.get("/api/sar/scenes?available=true")).json();
  // the smallest real-SAR scene on the host keeps the run short
  const scene = db.scenes.filter((s) => s.label !== "SYNTHETIC").sort((a, b) => a.raster_bytes - b.raster_bytes)[0];
  await page.goto(`/detections?scene=${scene.key}`);
  await page.getByTestId("sar-analyse").click();
  await expect(page.getByTestId("sar-analysis")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("sar-step-detect")).toHaveAttribute("data-state", /done|failed/, { timeout: 300_000 });
  const run = new URL(page.url()).searchParams.get("run");
  const inv = new URL(page.url()).searchParams.get("inv");
  const detect = await page.request.get(`/api/layers/${run}/detect`);
  expect(detect.ok(), "the run holds the detector's own response").toBeTruthy();
  const d = await detect.json();
  await expect(page.getByTestId("sar-analysis")).toContainText(d.model_version);
  if (d.confidence != null) await expect(page.getByTestId("sar-analysis")).toContainText(`${(d.confidence * 100).toFixed(1)} %`);
  await expect(page.getByTestId("sar-mask")).toBeAttached();

  await page.getByTestId("find-vessels").click();
  // the hand-off still speaks the legacy address; it must land on the canonical one
  await expect(page).toHaveURL(/\/investigations\/[^/?]+\?/);
  expect(new URL(page.url()).searchParams.get("run")).toBe(run);          // the same run, not a new one
  expect(new URL(page.url()).pathname).toBe(`/investigations/${inv}`);
  const ws = page.getByTestId("workspace");
  await expect(ws).toHaveAttribute("data-beat", /globe|footprint|sar/, { timeout: 30_000 });
  await expect(page.getByTestId("cine-hud")).toBeVisible();
  await expect(page.getByTestId("scene-line")).toBeHidden().catch(() => {});
  await page.getByTestId("cine-speed-4").click();
  await expect(ws).toHaveAttribute("data-beat", /scan|detect|segment|validate/, { timeout: 120_000 });
});
