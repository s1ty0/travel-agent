import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  use: { baseURL: "http://127.0.0.1:3000", trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    {
      name: "mobile",
      use: { ...devices["iPhone 13"], defaultBrowserType: "chromium" },
    },
  ],
  webServer: [
    {
      command:
        "uv run --no-sync --project .. uvicorn travel_agent.api:app --host 127.0.0.1 --port 8000",
      url: "http://127.0.0.1:8000/health",
      env: {
        TRAVEL_DEMO_MODE: "1",
        TRAVEL_WEB_LIVE: "0",
        TRAVEL_WEB_TOKEN: "synthetic-e2e-bridge-only-000000000000",
        TRAVEL_TASK_WORKER_EXTERNAL: "0",
        TRAVEL_TASK_DB: "../artifacts/e2e-tasks.sqlite3",
      },
      reuseExistingServer: !process.env.CI,
      timeout: 60000,
    },
    {
      command: "npm run start",
      url: "http://127.0.0.1:3000",
      env: { TRAVEL_WEB_TOKEN: "synthetic-e2e-bridge-only-000000000000" },
      reuseExistingServer: !process.env.CI,
      timeout: 60000,
    },
  ],
});
