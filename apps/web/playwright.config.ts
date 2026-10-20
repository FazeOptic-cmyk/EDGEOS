import { defineConfig, devices } from "@playwright/test";

const webOrigin = "http://127.0.0.1:3100";
const apiOrigin = "http://127.0.0.1:8100";

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  workers: 1,
  use: {
    baseURL: webOrigin,
    trace: "retain-on-failure",
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
      : {},
  },
  webServer: [
    {
      command: "uv run --locked uvicorn edgeos.main:app --host 127.0.0.1 --port 8100",
      cwd: "../api",
      url: `${apiOrigin}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        EDGEOS_MODE: "demo", EDGEOS_ENV: "test", EDGEOS_DEMO_MODE: "true",
        WEB_ORIGIN: webOrigin, EDGEOS_CORS_ORIGINS: webOrigin,
      },
    },
    {
      command: "npm run dev -- --hostname 127.0.0.1 --port 3100",
      url: webOrigin,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        NEXT_PUBLIC_APP_MODE: "demo",
        NEXT_PUBLIC_API_URL: "/api/edgeos",
        EDGEOS_INTERNAL_API_ORIGIN: apiOrigin,
      },
    },
  ],
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["iPhone 13"], browserName: "chromium" } },
  ],
});
