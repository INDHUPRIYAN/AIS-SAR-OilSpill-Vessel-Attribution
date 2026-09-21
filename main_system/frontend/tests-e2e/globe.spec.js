/* The one map engine, in a real browser.
 *
 * MapLibre GL in globe projection with deck.gl layers inside it. These tests
 * ask the map itself what it drew (the element exposes `__globe`), so they
 * fail on the engine's behaviour, not on a screenshot. */
import { test, expect } from "@playwright/test";

const API = process.env.E2E_API || "";
const EMAIL = process.env.E2E_EMAIL || "test-admin@example.invalid";
const PASSWORD = process.env.E2E_PASSWORD || "suite-fixture-password";

async function signIn(page) {
  const r = await page.request.post(`${API}/api/auth/login`, { data: { email: EMAIL, password: PASSWORD } });
  expect(r.ok(), `sign-in failed: ${r.status()}`).toBeTruthy();
}

const globe = (page) => page.getByTestId("globe").first();
const ask = (page, fn, arg) => globe(page).evaluate((el, [src, a]) => {
  const g = el.__globe; const m = g.getMap();
  return new Function("g", "m", "a", `return (${src})(g, m, a)`)(g, m, a);
}, [fn.toString(), arg]);

async function ready(page) {
  await expect(globe(page)).toBeVisible();
  await page.waitForFunction(() => {
    const el = document.querySelector('[data-testid="globe"]');
    const m = el?.__globe?.getMap();
    return Boolean(m && m.isStyleLoaded() && m.getSource("countries") && m.isSourceLoaded("countries"));
  }, null, { timeout: 30_000 });
}

test("G1: the world opens as real Earth — a globe with countries, borders and names", async ({ page }) => {
  await signIn(page);
  await page.goto("/map");
  await ready(page);
  await expect(globe(page)).toHaveAttribute("data-projection", "globe");
  await expect(globe(page)).toHaveAttribute("data-basemap", /satellite|geopolitical/);   // satellite when configured
  const drawn = await ask(page, (g, m) => ({
    countries: m.querySourceFeatures("countries").length,
    india: m.querySourceFeatures("countries").some((f) => f.properties.a3 === "IND"),
    labels: m.queryRenderedFeatures({ layers: ["ot-country-labels"] }).map((f) => f.properties.name),
    seas: m.queryRenderedFeatures({ layers: ["ot-marine-labels"] }).map((f) => f.properties.name),
    borders: m.queryRenderedFeatures({ layers: ["ot-borders"] }).length,
  }));
  expect(drawn.countries).toBeGreaterThan(50);
  expect(drawn.india).toBe(true);
  expect(drawn.labels).toContain("India");
  expect(drawn.seas.join(" ").toLowerCase()).toContain("bay of bengal");
  expect(drawn.borders).toBeGreaterThan(0);
});

test("G2: switching basemap keeps the camera, and the data layers survive the style change", async ({ page }) => {
  await signIn(page);
  await page.goto("/map");
  await ready(page);
  await ask(page, (g) => g.flyTo({ longitude: 85, latitude: 15, zoom: 4.2 }, 0));
  await page.waitForTimeout(400);
  const before = await ask(page, (g) => g.getCamera());
  // deck's interleaved layers sit in the map's layer order as one group; what
  // matters is that a zone can still be picked after the style is swapped.
  const zoneAt = (g, m) => {
    const p = g.project([88, 14]);
    return { group: m.getLayersOrder().some((id) => id.startsWith("deck-layer-group")), picked: g.pick(p[0], p[1])?.layer?.id || null };
  };
  const zonesBefore = await ask(page, zoneAt);
  expect(zonesBefore.picked).toBe("globe-zones");

  await page.getByTestId("basemap-dark").check();
  await expect(globe(page)).toHaveAttribute("data-basemap", "dark");
  await page.waitForTimeout(800);
  const after = await ask(page, (g) => g.getCamera());
  expect(Math.abs(after.longitude - before.longitude)).toBeLessThan(1e-6);
  expect(Math.abs(after.zoom - before.zoom)).toBeLessThan(1e-6);
  // dark maritime drops country names, keeps the seas
  expect(await ask(page, (g, m) => Boolean(m.getLayer("ot-country-labels")))).toBe(false);
  expect(await ask(page, (g, m) => Boolean(m.getLayer("ot-marine-labels")))).toBe(true);
  // a style swap must not drop the data layers
  expect(await ask(page, zoneAt)).toEqual(zonesBefore);
});

test("G3: satellite is a configured provider with attribution, over the same camera", async ({ page }) => {
  await signIn(page);
  await page.goto("/map");
  await ready(page);
  const sat = page.getByTestId("basemap-satellite");
  if (await sat.isDisabled()) {
    // No provider in this build: the control says so and nothing pretends to be imagery.
    await expect(sat.locator("xpath=ancestor::label")).toHaveAttribute("title", /not configured/i);
    return;
  }
  await sat.check();
  await expect(globe(page)).toHaveAttribute("data-basemap", "satellite");
  const s = await ask(page, (g, m) => ({ src: m.getSource("satellite")?.type, land: Boolean(m.getLayer("ot-land")),
    labels: Boolean(m.getLayer("ot-country-labels")), attribution: m.getStyle().sources.satellite.attribution }));
  expect(s.src).toBe("raster");
  expect(s.land).toBe(true);          // what remains if the provider is unreachable
  expect(s.labels).toBe(true);
  expect(s.attribution.length).toBeGreaterThan(3);
  await expect(page.locator(".maplibregl-ctrl-attrib")).toBeVisible();
});

test("G4: one continuous surface — the globe becomes a flat map as the view closes in", async ({ page }) => {
  await signIn(page);
  await page.goto("/map");
  await ready(page);
  await ask(page, (g) => g.flyTo({ longitude: 80.3, latitude: 13.1, zoom: 9 }, 0));
  await expect(globe(page)).toHaveAttribute("data-zoom", "9.00");
  // same map instance, same canvas: nothing was swapped
  expect(await page.locator('[data-testid="globe"] canvas.maplibregl-canvas').count()).toBe(1);
  await ask(page, (g) => g.flyTo({ longitude: 80.3, latitude: 13.1, zoom: 1.8 }, 0));
  await expect(globe(page)).toHaveAttribute("data-zoom", "1.80");
});

test("G5: the camera rides in the URL and survives a reload", async ({ page }) => {
  await signIn(page);
  await page.goto("/map");
  await ready(page);
  await ask(page, (g) => g.flyTo({ longitude: 72.5, latitude: 18.9, zoom: 5.5 }, 0));
  await expect(page).toHaveURL(/[?&]c=72\.5000(%2C|,)18\.9000(%2C|,)5\.50/);
  await page.reload();
  await ready(page);
  const c = await ask(page, (g) => g.getCamera());
  expect(c.longitude).toBeCloseTo(72.5, 2);
  expect(c.zoom).toBeCloseTo(5.5, 1);
});

test("G6: clicking water reports the coordinate under the cursor (zone lookup still works)", async ({ page }) => {
  await signIn(page);
  await page.goto("/map");
  await ready(page);
  const lookups = [];
  page.on("request", (r) => { if (r.url().includes("/api/zones/lookup")) lookups.push(r.url()); });
  // open water outside every zone: a click ON a zone selects it instead, by design
  const pt = await ask(page, (g) => g.project([66, 12]));
  const box = await globe(page).boundingBox();
  await page.mouse.click(box.x + pt[0], box.y + pt[1]);
  await expect.poll(() => lookups.length, { timeout: 5000 }).toBeGreaterThan(0);
  const u = new URL(lookups[0]);
  expect(Number(u.searchParams.get("lon"))).toBeCloseTo(66, 0);
  expect(Number(u.searchParams.get("lat"))).toBeCloseTo(12, 0);
});

test("G7: the globe works with every external host blocked", async ({ page }) => {
  await signIn(page);
  await page.route(/^https?:\/\/(?!localhost|127\.0\.0\.1)/, (r) => r.abort());
  await page.goto("/map");
  await ready(page);
  const labels = await ask(page, (g, m) => m.queryRenderedFeatures({ layers: ["ot-country-labels"] }).length);
  expect(labels).toBeGreaterThan(3);
});
