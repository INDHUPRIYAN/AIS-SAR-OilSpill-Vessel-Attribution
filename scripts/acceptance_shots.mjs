/* P20 acceptance screenshots: the honest-labels set, captured from the real UI
 * against the live registry. Companion to scripts/acceptance_e2e.py -- that
 * script proves the API answers; this one proves the screen shows them.
 *
 *   node scripts/acceptance_shots.mjs [http://localhost:5175]
 *
 * Writes PNGs into acceptance_evidence/<E2E-ID>/ui-*.png and a transcript of
 * what each screen actually displayed (text read back from the DOM, not
 * asserted from memory) into acceptance_evidence/ui_transcript.txt.
 */
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync, existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const REPO = join(here, "..");
const req = createRequire(join(REPO, "main_system", "frontend", "package.json"));
const { chromium } = req("@playwright/test");

const BASE = process.argv[2] || "http://localhost:5175";
const OUT = join(REPO, "acceptance_evidence");
const FLAGSHIP = "inv-gulf-flagship-20230108-2day";
const SYNTHETIC = "inv-gulf-rehearsal-20230108";
const EMAIL = "operator@oceantrace.local";
const PASSWORD = "p20-acceptance-operator";

const log = [];
const note = (s) => { log.push(s); console.log(s); };
const dir = (id) => { const d = join(OUT, id); mkdirSync(d, { recursive: true }); return d; };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
page.on("console", (m) => { if (m.type() === "error" && !/40[14]/.test(m.text())) note(`  console error: ${m.text()}`); });

const text = async (sel) => (await page.locator(sel).first().innerText().catch(() => "(absent)")).replace(/\n/g, " | ");
const shot = async (id, name, sel) => {
  const target = sel ? page.locator(sel).first() : page;
  const path = join(dir(id), `ui-${name}.png`);
  await target.screenshot({ path });
  note(`[${id}] captured ui-${name}.png`);
};

/* ---- sign in (RBAC visible) --------------------------------------------- */
await page.goto(BASE, { waitUntil: "domcontentloaded" });
await shot("E2E-01", "signin-form", null);
await page.locator("#email").fill(EMAIL);
await page.locator("#password").fill(PASSWORD);
await page.locator('button[type="submit"], form button').first().click();
await page.waitForSelector(".topbar", { timeout: 20000 });
note("signed in as operator (admin)");

/* ---- E2E-04: the flagship through its deep link -------------------------- */
await page.goto(`${BASE}/investigation?run=${FLAGSHIP}`, { waitUntil: "domcontentloaded" });
await page.waitForSelector('[data-testid="chip-stages"]', { timeout: 30000 });
await page.waitForTimeout(4000);
await shot("E2E-04", "flagship-workspace", null);
await shot("E2E-04", "flagship-topbar", ".topbar");
note(`[E2E-04] title: ${await text('[data-testid="ws-title"]')}`);
note(`[E2E-04] unfiled badge: ${await text('[data-testid="unfiled-run"]')}`);
note(`[E2E-04] chips: ${await text('[data-testid="provenance-chips"]')}`);
note(`[E2E-04] scene source badge: ${await text('[data-testid="scene-source"]')}`);
note(`[E2E-04] overall: ${await text('[data-testid="overall-status"]')}`);
note(`[E2E-04] AIS chip title: ${await page.getByTestId("chip-ais").getAttribute("title").catch(() => "(absent)")}`);
note(`[E2E-04] index chip title: ${await page.getByTestId("chip-registry-source").getAttribute("title").catch(() => "(absent)")}`);
// suspects tab: rank/score language, no names invented
await page.getByTestId("tab-suspects").click().catch(() => {});
await page.waitForTimeout(800);
await shot("E2E-04", "flagship-suspects", ".ws-right");
note(`[E2E-04] suspects panel: ${(await text(".ws-right")).slice(0, 600)}`);
await page.getByTestId("tab-spill").click().catch(() => {});

/* ---- E2E-04: findable in ⌘K, listed in Runs ------------------------------ */
await page.keyboard.press("Control+k");
await page.getByTestId("palette-input").fill("flagship");
await page.waitForTimeout(800);
await shot("E2E-04", "palette-flagship", ".cp");
note(`[E2E-04] palette rows: ${await page.locator(".cp-row").count()} · first: ${await text(".cp-row")}`);
await page.keyboard.press("Escape");
await page.goto(`${BASE}/dashboard`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
const dash = await page.locator("body").innerText();
note(`[E2E-04] dashboard mentions flagship: ${dash.includes(FLAGSHIP)}`);
await shot("E2E-04", "runs-list", null);

/* ---- E2E-05: synthetic run, gold chips everywhere ------------------------ */
await page.goto(`${BASE}/investigation?run=${SYNTHETIC}`, { waitUntil: "domcontentloaded" });
await page.waitForSelector('[data-testid="chip-stages"]', { timeout: 30000 });
await page.waitForTimeout(4000);
await shot("E2E-05", "synthetic-workspace", null);
await shot("E2E-05", "synthetic-topbar", ".topbar");
note(`[E2E-05] chips: ${await text('[data-testid="provenance-chips"]')}`);
const layerBadges = await page.locator(".ws-left .badge").allInnerTexts().catch(() => []);
note(`[E2E-05] layer badges: ${layerBadges.join(" · ")}`);
await page.getByTestId("tab-suspects").click().catch(() => {});
await page.waitForTimeout(800);
await shot("E2E-05", "synthetic-suspects", ".ws-right");
note(`[E2E-05] suspects panel: ${(await text(".ws-right")).slice(0, 500)}`);

/* ---- E2E-06: hindcast / origin panel on the flagship --------------------- */
await page.goto(`${BASE}/investigation?run=${FLAGSHIP}`, { waitUntil: "domcontentloaded" });
await page.waitForSelector('[data-testid="chip-stages"]', { timeout: 30000 });
await page.waitForTimeout(4000);
await shot("E2E-06", "flagship-spill-panel", ".ws-right");
note(`[E2E-06] spill panel: ${(await text(".ws-right")).slice(0, 700)}`);

/* ---- E2E-07: analytics (metrics truth) ---------------------------------- */
await page.goto(`${BASE}/analytics`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3500);
await shot("E2E-07", "analytics", null);
const an = await page.locator("body").innerText();
note(`[E2E-07] analytics shows unet-r34-fullcorpus-e48: ${an.includes("unet-r34-fullcorpus-e48")} · 0.5723: ${an.includes("0.5723")} · 0.4445: ${an.includes("0.4445")} · EXPERIMENTAL: ${/EXPERIMENTAL/i.test(an)} · YOLOv8: ${/yolov8/i.test(an)}`);
await page.goto(`${BASE}/catalog`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
await shot("E2E-07", "catalog-models", null);
const cat = await page.locator("body").innerText();
note(`[E2E-07] catalog shows NOT DEPLOYED: ${/NOT[ _]DEPLOYED/i.test(cat)} · sha256 shown: ${/[0-9a-f]{12,}/.test(cat)}`);

/* ---- E2E-10: report page for the flagship ------------------------------ */
await page.goto(`${BASE}/report?run=${FLAGSHIP}`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(4000);
await shot("E2E-10", "report", null);
const rep = await page.locator("body").innerText();
note(`[E2E-10] report mentions digest prefix: ${rep.includes("fd42e078")} · "Rank": ${rep.includes("Rank")} · guilt words: ${/guilty|culprit|offender|polluter/i.test(rep)} · LOW confidence: ${/LOW/.test(rep)}`);

/* ---- E2E-12: alerts page ------------------------------------------------ */
await page.goto(`${BASE}/alerts`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);
await shot("E2E-12", "alerts", null);
note(`[E2E-12] alerts page: ${(await page.locator("body").innerText()).slice(0, 300).replace(/\n/g, " | ")}`);

/* ---- E2E-17-ish: monitoring / provider truth (supports E2E-02 degraded) -- */
await page.goto(`${BASE}/monitoring`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3500);
await shot("E2E-02", "monitoring-providers", null);
const mon = await page.locator("body").innerText();
note(`[E2E-02] monitoring shows REACHABLE/WORKING split: ${/REACHABLE/.test(mon)} / ${/WORKING/.test(mon)} · NOT DEPLOYED rows: ${/NOT[ _]DEPLOYED/i.test(mon)}`);

/* ---- E2E-14: the cancelled run's honest state --------------------------- */
const startFile = join(OUT, "E2E-14", "start.json");
if (existsSync(startFile)) {
  const { run_id } = JSON.parse(readFileSync(startFile, "utf-8"));
  await page.goto(`${BASE}/investigation?run=${run_id}`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4000);
  await shot("E2E-14", "cancelled", null);
  note(`[E2E-14] overall: ${await text('[data-testid="overall-status"]')} · chips: ${await text('[data-testid="provenance-chips"]')}`);
} else {
  note("[E2E-14] start.json not present yet -- cancelled-run screenshot skipped");
}

/* ---- E2E-02: the UI-launched run, if it finished ------------------------- */
const journey = join(OUT, "E2E-02", "start.json");
if (existsSync(journey)) {
  const { run_id } = JSON.parse(readFileSync(journey, "utf-8"));
  await page.goto(`${BASE}/investigation?run=${run_id}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[data-testid="chip-stages"]', { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(5000);
  await shot("E2E-02", "journey-workspace", null);
  note(`[E2E-02] overall: ${await text('[data-testid="overall-status"]')} · chips: ${await text('[data-testid="provenance-chips"]')}`);
  await shot("E2E-03", "ui-run-topbar", ".topbar");
}

await browser.close();
writeFileSync(join(OUT, "ui_transcript.txt"), log.join("\n") + "\n", "utf-8");
console.log("\nwrote acceptance_evidence/ui_transcript.txt");
