// Screenshot helper for visual checks during UI work (not a test).
//   node tests-e2e/tools/shot.mjs <outDir> <path>[@WxH][#testid-to-click] ...
// Uses the same stack and credentials as the e2e suite.
import { chromium } from "@playwright/test";

const base = process.env.E2E_BASE_URL || "http://localhost:5174";
const [outDir, ...targets] = process.argv.slice(2);
const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=default", "--ignore-gpu-blocklist"] });
const ctx = await browser.newContext({ baseURL: base, viewport: { width: 1600, height: 900 } });
const page = await ctx.newPage();
const login = await page.request.post("/api/auth/login", {
  data: { email: process.env.E2E_EMAIL, password: process.env.E2E_PASSWORD },
});
if (!login.ok()) throw new Error(`sign-in failed: ${login.status()}`);
let n = 0;
for (const t of targets) {
  const [rest, click] = t.split("#");
  const [path, size] = rest.split("@");
  if (size) { const [w, h] = size.split("x").map(Number); await page.setViewportSize({ width: w, height: h }); }
  await page.goto(path);
  await page.waitForLoadState("networkidle").catch(() => {});
  for (const id of (click ? click.split(",") : [])) await page.getByTestId(id).click();
  await page.waitForTimeout(Number(process.env.SHOT_WAIT || 1500));
  n += 1;
  const file = `${outDir}/${String(n).padStart(2, "0")}${path.replace(/[^a-z0-9]+/gi, "_") || "_home"}.png`;
  await page.screenshot({ path: file });
  console.log(file);
}
await browser.close();
