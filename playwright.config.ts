import { defineConfig, devices } from "@playwright/test";

/**
 * QA runs in two modes:
 *  - Local:    pnpm test:e2e            (starts the production build automatically)
 *  - Deployed: PLAYWRIGHT_BASE_URL=https://<deployment> pnpm test:e2e
 */
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000";
const isRemote = Boolean(process.env.PLAYWRIGHT_BASE_URL);

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  retries: isRemote ? 2 : 1,
  reporter: [["list"], ["html", { open: "never" }]],
  timeout: 45_000,
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chromium", use: { ...devices["Pixel 7"] } },
  ],
  ...(isRemote
    ? {}
    : {
        webServer: {
          command: "pnpm start",
          url: "http://localhost:3000",
          reuseExistingServer: true,
          timeout: 60_000,
        },
      }),
});
