/* The shell: one navigation, canonical addresses, and the tripwire for every
 * screen that has no spec of its own.
 *
 * Runs against the same stack as the other specs (see playwright.config.js). */
import { test, expect } from "@playwright/test";

const API = process.env.E2E_API || "";
const EMAIL = process.env.E2E_EMAIL || "test-admin@example.invalid";
const PASSWORD = process.env.E2E_PASSWORD || "suite-fixture-password";

async function signIn(page) {
  const r = await page.request.post(`${API}/api/auth/login`, { data: { email: EMAIL, password: PASSWORD } });
  expect(r.ok(), `sign-in failed: ${r.status()}`).toBeTruthy();
}

// Every sidebar destination. `/investigations/latest` stands in for the
// workspace; the run-scoped pages are covered by their own specs.
const SCREENS = [
  "/", "/investigations", "/investigations/registry", "/investigations/new", "/map", "/detections",
  "/detections/viewer", "/vessels", "/reports", "/operations/desk", "/operations/incidents",
  "/operations/alerts", "/operations/replay", "/system/engines", "/system/data-sources",
  "/system/api-monitor", "/system/zones", "/system/health", "/system/models", "/system/analytics",
  "/system/environment", "/system/audit", "/system/users", "/system/credentials", "/help",
];

test("S1: every screen renders inside the shell without a script error", async ({ page }) => {
  test.setTimeout(240_000);
  await signIn(page);
  const errors = [];
  page.on("pageerror", (e) => errors.push(`${page.url()} :: ${e.message}`));
  for (const path of SCREENS) {
    await page.goto(path);
    await expect(page.getByTestId("left-nav")).toBeVisible();
    await expect(page.getByTestId("breadcrumbs")).toBeVisible();
    await expect(page.getByTestId("not-found"), path).toHaveCount(0);
    await expect(page.locator("#main")).not.toBeEmpty();
    expect(new URL(page.url()).pathname, "a canonical address does not redirect").toBe(path);
  }
  expect(errors, errors.join("\n")).toEqual([]);
});

test("S2: every address the app ever answered to still lands", async ({ page }) => {
  await signIn(page);
  const moved = [
    ["/globe?zone=z1", "/map?zone=z1"],
    ["/sar-database", "/detections"],
    ["/hindcast", "/system/engines"],
    ["/zones", "/system/zones"],
    ["/keys", "/system/credentials"],
    ["/system?tab=workers", "/system/health?tab=runtime"],      // broken at baseline
    ["/incidents?incident=INC-1", "/operations/incidents?focus=INC-1"], // what the backend search emits
    ["/investigation?scene=S1A_TEST", "/detections?scene=S1A_TEST"],    // likewise
    ["/investigation?new=1", "/investigations/new"],
    ["/report", "/reports"],                                     // blank page at baseline
    ["/vessels?mmsi=419000123", "/vessels/419000123"],
  ];
  for (const [from, to] of moved) {
    await page.goto(from);
    await expect(page, from).toHaveURL(new RegExp(`${to.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`));
    await expect(page.getByTestId("not-found")).toHaveCount(0);
  }
});

test("S3: an unknown address says so and keeps the address", async ({ page }) => {
  await signIn(page);
  await page.goto("/no/such/page?x=1");
  await expect(page.getByTestId("not-found")).toContainText("/no/such/page?x=1");
  expect(new URL(page.url()).pathname).toBe("/no/such/page");
});

test("S4: the sidebar is the navigation; back and forward retrace it", async ({ page }) => {
  await signIn(page);
  await page.goto("/");
  await expect(page.getByTestId("primary-nav")).toHaveCount(0);           // the header nav is gone
  await page.getByTestId("nav-investigations").click();
  await expect(page).toHaveURL(/\/investigations$/);
  await page.getByTestId("nav-group-system").click();
  await page.getByTestId("nav-zones").click();
  await expect(page).toHaveURL(/\/system\/zones$/);
  await expect(page.getByTestId("breadcrumbs")).toContainText("System");
  await expect(page.getByTestId("breadcrumbs")).toContainText("Zones");
  await page.goBack();
  await expect(page).toHaveURL(/\/investigations$/);
  await page.goForward();
  await expect(page).toHaveURL(/\/system\/zones$/);
  // collapsed: labels go, the group becomes a flyout
  await page.getByTestId("nav-toggle").click();
  await page.getByTestId("nav-group-system").click();
  await expect(page.getByTestId("nav-flyout-system")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("nav-flyout-system")).toHaveCount(0);
  await page.getByTestId("nav-toggle").click();
});

test("S5: a page tab rides in the URL and survives a reload", async ({ page }) => {
  await signIn(page);
  await page.goto("/system/health");
  await page.getByTestId("sysops-tab-logs").click();
  await expect(page).toHaveURL(/tab=logs/);
  await page.reload();
  await expect(page.getByTestId("sysops-tab-logs")).toHaveAttribute("aria-selected", "true");
});

test("S6: at tablet width the sidebar is a drawer", async ({ page }) => {
  await signIn(page);
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto("/");
  await expect(page.getByTestId("left-nav")).not.toBeInViewport();
  await page.getByTestId("nav-toggle").click();
  await expect(page.getByTestId("left-nav")).toBeInViewport();
  await page.getByTestId("nav-reports").click();
  await expect(page).toHaveURL(/\/reports$/);
  await expect(page.getByTestId("left-nav")).not.toBeInViewport();
});

test("S7: in-page links are canonical — nothing relies on a redirect", async ({ page }) => {
  await signIn(page);
  const legacy = ["/globe", "/sar-database", "/satellite", "/dashboard", "/alerts", "/my-desk",
    "/incident", "/hindcast", "/monitoring", "/catalog", "/models", "/analytics", "/audit",
    "/officers", "/keys", "/zones", "/environment", "/about", "/investigation", "/report", "/incidents"];
  const offenders = [];
  for (const path of ["/", "/investigations", "/map", "/detections", "/vessels", "/reports",
    "/operations/incidents", "/operations/alerts", "/operations/desk", "/system/zones", "/system/health"]) {
    await page.goto(path);
    await expect(page.getByTestId("left-nav")).toBeVisible();
    const hrefs = await page.locator("#main a[href^='/']").evaluateAll((as) => as.map((a) => a.getAttribute("href")));
    for (const h of hrefs) {
      const p = h.split("?")[0].replace(/\/$/, "") || "/";
      if (legacy.includes(p)) offenders.push(`${path} → ${h}`);
    }
  }
  expect(offenders, offenders.join("\n")).toEqual([]);
});
