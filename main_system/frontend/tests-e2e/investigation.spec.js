/* Investigation-page E2E checklist (UI half).
 *
 * The backend half (contract fidelity, edge states, validation errors) lives
 * in tests/test_investigation_page.py. Here we prove the page itself:
 * replay to fully-rendered in under 5 s, panels showing contract values
 * verbatim, suspect interaction, time scrubbing, and the airplane-mode run
 * with every non-localhost request blocked.
 */
import { test, expect } from "@playwright/test";

const API = "http://localhost:8000";

async function makeInvestigation(request) {
  const r = await request.post(`${API}/api/investigations`, {
    data: { name: "e2e workspace", scene_meta_path: "contracts/mocks/scene_meta.json" },
  });
  expect(r.ok()).toBeTruthy();
  return (await r.json()).id;
}

/* Replay may borrow a complete run of the same scene from a sibling
 * investigation, so contract layers are compared run-scoped. */
async function replayRunId(request, inv) {
  const r = await request.post(`${API}/api/investigations/${inv}/replay`);
  expect(r.ok()).toBeTruthy();
  return (await r.json()).run_id;
}

async function replayAndAwaitRender(page) {
  // Let the app settle before starting the clock: the 5 s budget measures
  // click-to-rendered, not chromium start-up noise from earlier tests.
  await page.getByTestId("run-btn").waitFor();
  await page.waitForLoadState("networkidle").catch(() => {});
  const t0 = Date.now();
  await page.getByTestId("run-btn").click();
  // all six stepper rows green (ok/fallback/mock all count as rendered)
  for (const key of ["detect", "characterise", "drift_hindcast",
                     "drift_forecast", "ais", "attribution"]) {
    await expect(page.getByTestId(`stage-${key}`))
      .toHaveAttribute("data-status", /ok|fallback|mock/, { timeout: 5000 });
  }
  await expect(page.getByTestId("spill-area")).not.toHaveText("—", { timeout: 5000 });
  await expect(page.locator("canvas").first()).toBeVisible();
  return Date.now() - t0;
}

test("1+10: replay renders all stages and layers in under 5 s", async ({ page, request }) => {
  const inv = await makeInvestigation(request);
  await page.goto(`/investigation?inv=${inv}`);
  await expect(page.getByTestId("run-btn")).toBeEnabled();
  const ms = await replayAndAwaitRender(page);
  expect(ms).toBeLessThan(5000);
  // every layer toggle enabled (all files present in a complete run)
  for (const k of ["sar", "slick", "forecast", "hindcast", "origin", "vessels"]) {
    await expect(page.getByTestId(`layer-${k}`)).toHaveAttribute("data-disabled", "false");
  }
  await expect(page.getByTestId("map-legend")).toBeVisible();
  await expect(page.getByTestId("overall-status")).toHaveText(/COMPLETE|RUNNING/);
});

test("2: spill panel numbers match the run's slick.geojson", async ({ page, request }) => {
  const inv = await makeInvestigation(request);
  await page.goto(`/investigation?inv=${inv}`);
  await replayAndAwaitRender(page);

  const rid = await replayRunId(request, inv);
  const slick = await (await request.get(`${API}/api/layers/${rid}/slick`)).json();
  const p = slick.features[0].properties;
  await expect(page.getByTestId("spill-area")).toHaveText(`${p.area_km2.toFixed(2)} km²`);
  await expect(page.getByTestId("spill-perimeter")).toHaveText(`${p.perimeter_km.toFixed(2)} km`);
  await expect(page.getByTestId("spill-orientation")).toHaveText(`${p.orientation_deg.toFixed(1)}°`);
  await expect(page.getByTestId("spill-confidence"))
    .toHaveText(`${(p.confidence * 100).toFixed(1)}%`);
});

test("3: suspects order matches suspects.json; click rank 1 opens breakdown", async ({ page, request }) => {
  const inv = await makeInvestigation(request);
  await page.goto(`/investigation?inv=${inv}`);
  await replayAndAwaitRender(page);
  await page.getByTestId("tab-suspects").click();
  await expect(page.getByTestId("suspects-disclaimer")).toBeVisible();

  const rid = await replayRunId(request, inv);
  const sus = await (await request.get(`${API}/api/layers/${rid}/suspects`)).json();
  for (const s of sus.suspects.slice(0, 3)) {
    await expect(page.getByTestId(`score-${s.rank}`))
      .toHaveText(s.total_score.toFixed(2));
  }
  await page.getByTestId("suspect-1").click();
  await expect(page.getByTestId("suspect-detail")).toBeVisible();
  await expect(page.getByTestId("suspect-reason"))
    .toContainText(sus.suspects[0].reason.slice(0, 40));
});

test("4: time slider scrub changes the clock and keeps the page alive", async ({ page, request }) => {
  const inv = await makeInvestigation(request);
  await page.goto(`/investigation?inv=${inv}`);
  await replayAndAwaitRender(page);

  const clock = page.getByTestId("time-value");
  const before = await clock.textContent();
  const rail = page.getByTestId("time-rail");
  const box = await rail.boundingBox();
  await page.mouse.click(box.x + box.width * 0.25, box.y + box.height / 2);
  const after25 = await clock.textContent();
  expect(after25).not.toEqual(before);
  await page.mouse.click(box.x + box.width * 0.9, box.y + box.height / 2);
  const after90 = await clock.textContent();
  expect(after90).not.toEqual(after25);
  await expect(page.locator("canvas").first()).toBeVisible();
});

test("9: airplane mode — every non-localhost request blocked, page still works", async ({ page, request }) => {
  const inv = await makeInvestigation(request);
  // Intercept ONLY non-localhost URLs: routing **/* would drag every API
  // call through Playwright's IPC and measure the harness, not the page.
  await page.route(/^https?:\/\/(?!localhost|127\.0\.0\.1)/, (route) =>
    route.abort());                            // OSM tiles, fonts, everything
  await page.goto(`/investigation?inv=${inv}`);
  const ms = await replayAndAwaitRender(page);
  expect(ms).toBeLessThan(5000);
  await page.getByTestId("tab-suspects").click();
  await expect(page.getByTestId("suspect-1")).toBeVisible();
});
