/* Investigation-workspace E2E checklist (UI half).
 *
 * The backend half (contract fidelity, edge states, validation errors) lives
 * in tests/test_investigation_page.py. Here we prove the workspace itself:
 * replay to fully-rendered in under 5 s, panels showing contract values
 * verbatim at the stage they belong to, suspect interaction, time scrubbing,
 * stage navigation, and the airplane-mode run with every non-localhost
 * request blocked.
 *
 * Every API route needs a session, so each test signs in first through the
 * page's own request context (same cookie jar as the browser). Point the
 * suite at another stack with E2E_BASE_URL / E2E_EMAIL / E2E_PASSWORD; the
 * defaults match the documented dev servers and the pytest fixture account.
 */
import { test, expect } from "@playwright/test";

/* Every request goes through the page's origin (the dev/preview proxy), so
 * the session cookie the sign-in sets is the one the page itself sends. */
const API = "";
const EMAIL = process.env.E2E_EMAIL || "test-admin@example.invalid";
const PASSWORD = process.env.E2E_PASSWORD || "suite-fixture-password";

async function signIn(page) {
  const r = await page.request.post(`${API}/api/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
  });
  expect(r.ok(), `sign-in failed: ${r.status()}`).toBeTruthy();
}

async function makeInvestigation(page) {
  const r = await page.request.post(`${API}/api/investigations`, {
    data: { name: "e2e workspace", scene_meta_path: "contracts/mocks/scene_meta.json" },
  });
  expect(r.ok()).toBeTruthy();
  return (await r.json()).id;
}

/* Replay may borrow a complete run of the same scene from a sibling
 * investigation, so contract layers are compared run-scoped. */
async function replayRunId(page, inv) {
  const r = await page.request.post(`${API}/api/investigations/${inv}/replay`);
  expect(r.ok()).toBeTruthy();
  return (await r.json()).run_id;
}

const CHIPS = ["detection", "geometry", "drift", "vessels", "attribution"];

/* "Replay analysis" renders the run's files at once -- the instrument's
 * budget. The presentation of the same run is a separate act (Play replay,
 * test 6) and is never started here. */
async function replayAndAwaitRender(page) {
  // Let the app settle before starting the clock: the 5 s budget measures
  // click-to-rendered, not chromium start-up noise from earlier tests.
  await page.getByTestId("run-btn").waitFor();
  await page.waitForLoadState("networkidle").catch(() => {});
  const t0 = Date.now();
  await page.getByTestId("run-btn").click();
  // every stage chip reads done (ok/fallback/mock all count as rendered)
  for (const chip of CHIPS) {
    await expect(page.getByTestId(`chip-${chip}`)).toHaveClass(/tl-chip-done/, { timeout: 5000 });
  }
  await expect(page.locator("canvas").first()).toBeVisible();
  return Date.now() - t0;
}

test("1+10: replay renders all stages and layers in under 5 s", async ({ page }) => {
  await signIn(page);
  const inv = await makeInvestigation(page);
  await page.goto(`/investigations/${inv}`);
  await expect(page.getByTestId("run-btn")).toBeEnabled();
  const ms = await replayAndAwaitRender(page);
  expect(ms).toBeLessThan(5000);
  // the workspace lands on the furthest analytical stage, and the
  // presentation of the run is offered, not forced
  await expect(page.getByTestId("workspace")).toHaveAttribute("data-stage", "attribution");
  await expect(page.getByTestId("cine-play")).toBeEnabled();
  await expect(page.getByTestId("overall-status")).toHaveText(/COMPLETE|RUNNING/);
  // every layer toggle enabled (all files present in a complete run)
  await page.getByTestId("map-layers-toggle").click();
  for (const k of ["sar", "slick", "forecast", "hindcast", "origin", "vessels", "lookalikes"]) {
    await expect(page.getByTestId(`layer-${k}`)).toHaveAttribute("data-disabled", "false");
  }
  await expect(page.getByTestId("map-legend")).toBeVisible();          // the layer panel's legend
  await expect(page.getByTestId("map-legend-box")).toBeVisible();      // the attribution map key
});

test("2: characterisation panel numbers match the run's slick.geojson", async ({ page }) => {
  await signIn(page);
  const inv = await makeInvestigation(page);
  await page.goto(`/investigations/${inv}`);
  await replayAndAwaitRender(page);
  await page.getByTestId("chip-geometry").click();
  await expect(page.getByTestId("workspace")).toHaveAttribute("data-stage", "geometry");

  const rid = await replayRunId(page, inv);
  const slick = await (await page.request.get(`${API}/api/layers/${rid}/slick`)).json();
  const p = slick.features[0].properties;
  await expect(page.getByTestId("spill-area")).toHaveText(`${p.area_km2.toFixed(2)} km²`);
  await expect(page.getByTestId("spill-perimeter")).toHaveText(`${p.perimeter_km.toFixed(2)} km`);
  await expect(page.getByTestId("spill-orientation")).toHaveText(`${p.orientation_deg.toFixed(1)}°`);
  await expect(page.getByTestId("spill-confidence"))
    .toHaveText(`${(p.confidence * 100).toFixed(1)}%`);
});

test("3: candidate ranking matches suspects.json; click rank 1 opens breakdown", async ({ page }) => {
  await signIn(page);
  const inv = await makeInvestigation(page);
  await page.goto(`/investigations/${inv}`);
  await replayAndAwaitRender(page);
  await page.getByTestId("chip-vessels").click();
  await expect(page.getByTestId("workspace")).toHaveAttribute("data-stage", "ais");
  await expect(page.getByTestId("suspects-disclaimer")).toBeVisible();
  // provenance of the AIS bytes is stated beside the ranking
  await expect(page.getByTestId("ais-source")).toHaveText(/HISTORICAL|SYNTHETIC|REAL|CACHED|UNRECORDED/);

  const rid = await replayRunId(page, inv);
  const sus = await (await page.request.get(`${API}/api/layers/${rid}/suspects`)).json();
  for (const s of sus.suspects.slice(0, 3)) {
    await expect(page.getByTestId(`score-${s.rank}`)).toHaveText(s.total_score.toFixed(2));
  }
  await page.getByTestId("suspect-1").click();
  await expect(page.getByTestId("suspect-detail")).toBeVisible();
  await expect(page.getByTestId("suspect-reason"))
    .toContainText(sus.suspects[0].reason.slice(0, 40));

  // weights are auditable on screen: legend shows the run's actual weights,
  // and each factor bar repeats its own ×w
  await expect(page.getByTestId("weights-legend")).toBeVisible();
  for (const [k, w] of Object.entries(sus.weights)) {
    await expect(page.getByTestId(`weight-${k}`).first()).toHaveText(`×${w.toFixed(2)}`);
  }

  const ev = sus.suspects[0].evidence || {};
  if (ev.closest_approach_km != null) {
    await expect(page.getByTestId("evidence-closest_approach_km")).toContainText("km");
  }
  if (ev.ais_gap_minutes != null) {
    await expect(page.getByTestId("evidence-ais_gap_minutes")).toContainText("min");
  }

  if ((sus.filtered_out || []).length) {
    await expect(page.getByTestId("filtered-toggle"))
      .toContainText(`Filtered out (${sus.filtered_out.length}`);
    await page.getByTestId("filtered-toggle").click();
    const f0 = sus.filtered_out[0];
    await expect(page.getByTestId(`filtered-${f0.mmsi}`)).toContainText(String(f0.mmsi));
    await expect(page.getByTestId(`filtered-${f0.mmsi}`)).toContainText(f0.reason.slice(0, 20));
  }

  // the attribution stage names the highest-ranked candidate, never a culprit
  await page.getByTestId("chip-attribution").click();
  await expect(page.getByTestId("attr-rank-badge")).toHaveText(/Highest-Ranked Candidate/);
  await expect(page.getByTestId("intel-attribution")).not.toContainText(/guilty|responsible vessel/i);
});

test("3b: export bundle downloads a zip of the run's contract artefacts", async ({ page }) => {
  await signIn(page);
  const inv = await makeInvestigation(page);
  await page.goto(`/investigations/${inv}`);
  await replayAndAwaitRender(page);
  const rid = await replayRunId(page, inv);
  const r = await page.request.get(`${API}/api/runs/${rid}/export`);
  expect(r.ok()).toBeTruthy();
  expect(r.headers()["content-type"]).toContain("application/zip");
  expect((await r.body()).length).toBeGreaterThan(1000);
  await expect(page.getByTestId("export-bundle")).toBeVisible();
  await expect(page.getByTestId("export-report")).toBeVisible();
});

test("3c: printable report renders run metadata, weights and suspects", async ({ page }) => {
  await signIn(page);
  const inv = await makeInvestigation(page);
  const rid = await replayRunId(page, inv);
  const sus = await (await page.request.get(`${API}/api/layers/${rid}/suspects`)).json();

  await page.goto(`/reports/print/${rid}`);
  await expect(page.locator("h1")).toContainText("Investigation report");
  await expect(page.locator(".rp-sub")).toContainText(rid);
  for (const w of Object.values(sus.weights)) {
    await expect(page.locator(".rp-weights")).toContainText(w.toFixed(2));
  }
  const top = sus.suspects[0];
  await expect(page.locator(".rp-suspect").first()).toContainText(top.total_score.toFixed(3));
  await expect(page.locator(".rp-suspect").first()).toContainText(top.reason.slice(0, 30));
  await expect(page.getByTestId("report-print")).toBeVisible();
});

test("4: time rail scrub changes the clock and keeps the page alive", async ({ page }) => {
  await signIn(page);
  const inv = await makeInvestigation(page);
  await page.goto(`/investigations/${inv}`);
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

test("5: stage navigation walks the workspace and survives a reload", async ({ page }) => {
  await signIn(page);
  const inv = await makeInvestigation(page);
  await page.goto(`/investigations/${inv}`);
  await replayAndAwaitRender(page);
  await page.getByTestId("stage-prev").click();
  await expect(page.getByTestId("workspace")).toHaveAttribute("data-stage", "ais");
  // a chip opens its group's first stage; clicking it again walks the group
  await page.getByTestId("chip-detection").click();
  await expect(page.getByTestId("workspace")).toHaveAttribute("data-stage", "preprocess");
  await page.getByTestId("chip-detection").click();
  await expect(page.getByTestId("workspace")).toHaveAttribute("data-stage", "tiling");
  await page.getByTestId("chip-detection").click();
  await expect(page.getByTestId("workspace")).toHaveAttribute("data-stage", "detection");
  // the detection panel is on screen with the run's own metrics row (a mock
  // detect response may legitimately carry no confidence)
  await expect(page.getByTestId("det-confidence")).toBeVisible();
  // the stage rides in the URL, so a reload restores it
  expect(new URL(page.url()).pathname).toBe(`/investigations/${inv}/detection`);
  await page.reload();
  await expect(page.getByTestId("workspace")).toHaveAttribute("data-stage", "detection", { timeout: 10000 });
});

/* The whole workflow as one continuous presentation (spec §2, §31): every
 * beat is reached in order, each one shows what the system is doing, the
 * clock runs backward through the hindcast and forward through the forecast,
 * the AIS funnel quotes the run's own vessel count, and the workspace
 * settles on the complete analytical state. Nothing is asserted that the
 * run's artefacts do not contain. */
test("6: the analysis plays beat by beat from the run's real artefacts", async ({ page }) => {
  test.setTimeout(180_000);
  await signIn(page);
  const inv = await makeInvestigation(page);
  const rid = await replayRunId(page, inv);
  const vessels = await (await page.request.get(`${API}/api/runs/${rid}/vessels_geojson`)).json();
  const sus = await (await page.request.get(`${API}/api/layers/${rid}/suspects`)).json();

  await page.goto(`/investigations/${inv}`);
  await replayAndAwaitRender(page);
  await page.getByTestId("cine-play").click();
  const ws = page.getByTestId("workspace");
  await expect(ws).toHaveAttribute("data-beat", "globe");
  await expect(page.getByTestId("cine-hud")).toBeVisible();
  await page.getByTestId("cine-speed-4").click();

  // the globe flies to the AOI, then the scene footprint and the SAR fade in
  await expect(ws).toHaveAttribute("data-beat", "footprint", { timeout: 20_000 });
  await expect(page.getByTestId("cine-line")).toContainText(/Searching Sentinel-1 scenes|Scene found|Loading SAR scene/);
  await expect(ws).toHaveAttribute("data-beat", "sar", { timeout: 15_000 });
  await expect(ws).toHaveAttribute("data-stage", "scene");

  // pre-processing rows complete one by one; tiling; then the sweep
  await expect(ws).toHaveAttribute("data-beat", "preprocess", { timeout: 15_000 });
  await expect(page.getByTestId("intel-processing")).toBeVisible();
  await expect(ws).toHaveAttribute("data-beat", "tiling", { timeout: 15_000 });
  await expect(ws).toHaveAttribute("data-beat", "scan", { timeout: 15_000 });
  await expect(page.getByTestId("scan-overlay")).toBeVisible();
  await expect(page.getByTestId("cine-line")).toContainText("anomalous dark formations");

  // detection → segmentation: the callout carries the run's own numbers
  await expect(ws).toHaveAttribute("data-beat", "detect", { timeout: 15_000 });
  await expect(ws).toHaveAttribute("data-beat", "segment", { timeout: 15_000 });
  await expect(page.getByTestId("tile-bar")).toContainText(/SEGMENTING|SEGMENTATION COMPLETE/);
  await expect(ws).toHaveAttribute("data-beat", "validate", { timeout: 15_000 });
  await expect(page.getByTestId("intel-validation")).toBeVisible();

  // wind / currents are soft beats (a run without a forcing grid skips them
  // and says so); hindcast and origin are mandatory
  await expect(ws).toHaveAttribute("data-beat", /wind|currents|hindcast/, { timeout: 20_000 });
  await expect(ws).toHaveAttribute("data-beat", "hindcast", { timeout: 30_000 });
  await expect(page.getByTestId("cine-clock")).toContainText(/T − |NOW/);
  await expect(ws).toHaveAttribute("data-stage", "drift");
  await expect(ws).toHaveAttribute("data-beat", "origin", { timeout: 20_000 });
  await expect(page.getByTestId("right-panel")).toHaveAttribute("data-context", "hindcast");
  await expect(page.getByTestId("hind-lat")).not.toHaveText("—");
  await expect(ws).toHaveAttribute("data-beat", /forecast|ais/, { timeout: 15_000 });

  // AIS: the tracks draw by timestamp, then the gates quote the real funnel
  // (read at 1×: the funnel's last line is on screen for a second or so)
  await expect(ws).toHaveAttribute("data-beat", "ais", { timeout: 25_000 });
  await page.getByTestId("cine-speed-1").click();
  await expect(page.getByTestId("cine-line")).toContainText(`${vessels.features.length} vessels found`, { timeout: 15_000 });
  await expect(ws).toHaveAttribute("data-beat", "filter", { timeout: 20_000 });
  const funnel = await (await page.request.get(`${API}/api/runs/${rid}/funnel`)).json();
  await expect(page.getByTestId("cine-line")).toContainText(`${funnel.indexed} considered`);
  await expect(page.getByTestId("cine-line")).toContainText(`${sus.suspects.length} candidates`, { timeout: 15_000 });

  // ranking lights candidates in rank order; attribution names the top one
  await expect(ws).toHaveAttribute("data-beat", "ranking", { timeout: 15_000 });
  await page.getByTestId("cine-speed-4").click();
  if (sus.suspects.length) {
    await expect(page.getByTestId("right-panel")).toHaveAttribute("data-context", "ranking");
    await expect(page.getByTestId("rank-1")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("rank-score-1")).toHaveText(sus.suspects[0].total_score.toFixed(2));
  }
  await expect(ws).toHaveAttribute("data-beat", "attribution", { timeout: 15_000 });
  await expect(ws).toHaveAttribute("data-stage", "attribution");
  if (sus.suspects.length) await expect(page.getByTestId("attr-rank-badge")).toHaveText(/Highest-Ranked Candidate/);
  await expect(page.getByTestId("intel-attribution")).not.toContainText(/guilty|responsible vessel/i);

  // the record, then the settled state: every layer on, the clock at the
  // scene time, the rail scrubbable, the analyst back in control
  await expect(ws).toHaveAttribute("data-beat", "evidence", { timeout: 15_000 });
  await expect(ws).toHaveAttribute("data-beat", "finished", { timeout: 15_000 });
  await expect(ws).toHaveAttribute("data-stage", "evidence");
  await expect(page.getByTestId("map-legend-box")).toBeVisible();
  await expect(page.getByTestId("cine-play")).toBeVisible();
  await expect(page.getByTestId("time-value")).toContainText("NOW");

  // scrubbing a beat afterwards replays just that beat, from the same files
  await page.getByTestId("beat-hindcast").click();
  await expect(ws).toHaveAttribute("data-beat", "hindcast");
  await expect(ws).toHaveAttribute("data-stage", "drift");
  await page.getByTestId("cine-stop").click();
  await expect(ws).toHaveAttribute("data-stage", "drift");
});

test("9: airplane mode — every non-localhost request blocked, page still works", async ({ page }) => {
  await signIn(page);
  const inv = await makeInvestigation(page);
  // Intercept ONLY non-localhost URLs: routing **/* would drag every API
  // call through Playwright's IPC and measure the harness, not the page.
  await page.route(/^https?:\/\/(?!localhost|127\.0\.0\.1)/, (route) => route.abort());
  await page.goto(`/investigations/${inv}`);
  const ms = await replayAndAwaitRender(page);
  expect(ms).toBeLessThan(5000);
  await page.getByTestId("chip-vessels").click();
  await expect(page.getByTestId("suspect-1")).toBeVisible();
});
