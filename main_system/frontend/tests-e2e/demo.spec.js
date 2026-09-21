/* The demo case: one click from the dashboard to a real run playing. */
import { test, expect } from "@playwright/test";

const API = process.env.E2E_API || "";
const EMAIL = process.env.E2E_EMAIL || "test-admin@example.invalid";
const PASSWORD = process.env.E2E_PASSWORD || "suite-fixture-password";
const DEMO = "inv-gulf-flagship-20230108-2day";

async function signIn(page) {
  const r = await page.request.post(`${API}/api/auth/login`, { data: { email: EMAIL, password: PASSWORD } });
  expect(r.ok(), `sign-in failed: ${r.status()}`).toBeTruthy();
}

test("D1: the demo case is one click from the dashboard, labelled, and plays from its own artefacts", async ({ page }) => {
  test.setTimeout(90_000);
  await signIn(page);
  const run = await page.request.get(`/api/runs/${DEMO}`);
  test.skip(!run.ok(), "the demo run is not installed on this host");

  await page.goto("/");
  await expect(page.getByTestId("demo-case")).toContainText(DEMO);
  await page.getByTestId("demo-open").click();
  await expect(page).toHaveURL(new RegExp(`/investigations/run/${DEMO}/`));
  const ws = page.getByTestId("workspace");
  await expect(page.getByTestId("demo-badge")).toBeVisible();
  // the presentation starts on its own and walks forward
  await expect(ws).toHaveAttribute("data-beat", /globe|footprint|sar/, { timeout: 30_000 });
  await expect(ws).toHaveAttribute("data-beat", /preprocess|tiling|scan|detect/, { timeout: 45_000 });
  // the numbers on the case strip are the run's own
  const slick = await (await page.request.get(`/api/layers/${DEMO}/slick`)).json();
  const total = slick.features.reduce((a, f) => a + (f.properties.area_km2 || 0), 0);
  await expect(page.getByTestId("fact-area")).toContainText(`${total.toFixed(1)} km²`);
  // and nothing on screen invents a scene fact the record and the name do not give
  await expect(page.getByTestId("control-panel")).not.toContainText("SAR (GRD)");
});

test("D2: a reload of the demo does not replay it; the address still opens the case", async ({ page }) => {
  await signIn(page);
  const run = await page.request.get(`/api/runs/${DEMO}`);
  test.skip(!run.ok(), "the demo run is not installed on this host");
  await page.goto(`/investigations/run/${DEMO}/scene?present=1`);
  await expect(page.getByTestId("workspace")).toHaveAttribute("data-beat", /.+/, { timeout: 30_000 });
  await expect(page).not.toHaveURL(/present=1/, { timeout: 30_000 });   // the flag is consumed
  await page.reload();
  await expect(page.getByTestId("demo-badge")).toBeVisible();
});
