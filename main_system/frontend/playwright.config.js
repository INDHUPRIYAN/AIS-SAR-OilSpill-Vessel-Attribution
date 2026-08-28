// Playwright config for the Investigation-page E2E suite.
// Assumes the dev servers are running (vite :5173 proxying FastAPI :8000);
// starts them itself when they are not.
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests-e2e",
  timeout: 45_000,
  retries: 0,
  workers: 1,                       // the suite shares one backend DB
  use: {
    baseURL: "http://localhost:5174",
    viewport: { width: 1600, height: 900 },
  },
  webServer: {
    command: "npm run preview -- --port 5174 --strictPort",
    url: "http://localhost:5174",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
