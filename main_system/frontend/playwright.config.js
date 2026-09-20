// Playwright config for the Investigation-workspace E2E suite.
// Assumes the dev servers are running (vite :5174 proxying FastAPI :8000);
// starts the preview server itself when it is not. Point the suite at another
// stack with E2E_BASE_URL (and E2E_API / E2E_EMAIL / E2E_PASSWORD in the spec);
// an explicit base URL means no server is started here.
//
// The workspace is two deck.gl WebGL contexts; on headless Chromium's default
// software renderer (SwiftShader) linking their shader programs alone costs
// ~5 s, which is the whole render budget of test 1. The ANGLE flags let the
// browser use the machine's GPU where there is one (measured here: 4.3 s →
// 1.4 s) and fall back to SwiftShader where there is not. E2E_CHANNEL=chromium
// runs the full browser instead of the headless shell, which is faster still
// but needs that channel installed.
import { defineConfig } from "@playwright/test";

const baseURL = process.env.E2E_BASE_URL || "http://localhost:5174";

export default defineConfig({
  testDir: "./tests-e2e",
  timeout: 45_000,
  retries: 0,
  workers: 1,                       // the suite shares one backend DB
  use: {
    baseURL,
    viewport: { width: 1600, height: 900 },
    ...(process.env.E2E_CHANNEL ? { channel: process.env.E2E_CHANNEL } : {}),
    launchOptions: { args: ["--use-gl=angle", "--use-angle=default", "--ignore-gpu-blocklist"] },
  },
  webServer: process.env.E2E_BASE_URL ? undefined : {
    command: "npm run preview -- --port 5174 --strictPort",
    url: "http://localhost:5174",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
