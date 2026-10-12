import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests for the product console (HOM-189).
 *
 * The console is a Vite app served under the `/app/` base, and it talks to the
 * FastAPI backend through the dev server's `/api` proxy. The tests therefore
 * need three things running: the backend, a database holding the demo seed
 * (HOM-175), and this dev server. Playwright starts the dev server itself; the
 * backend and the seed are yours. See `e2e/README.md`.
 *
 * Environment variables:
 *   E2E_BASE_URL    an already running console; skips the dev server
 *   E2E_PORT        port for the dev server Playwright starts (default 4173)
 *   E2E_API_URL     the backend (default http://127.0.0.1:8001, what `make backend` starts)
 *   E2E_SMTP_PORT   port of the SMTP sink that captures invitation mail (default 1025)
 */
const port = Number(process.env.E2E_PORT ?? 4173);
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${port}`;
const apiURL = process.env.E2E_API_URL ?? "http://127.0.0.1:8001";

export default defineConfig({
  testDir: "./e2e",
  // `.test.ts` next to the helpers is Vitest's; only the flows run here.
  testMatch: "**/*.spec.ts",
  // The flows share one demo workspace, so they must not overlap.
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  timeout: 60_000,
  expect: { timeout: 10_000 },
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  use: {
    baseURL,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  ...(process.env.E2E_BASE_URL
    ? {}
    : {
        webServer: {
          command: `npm run dev -- --port ${port} --strictPort`,
          url: `${baseURL}/app/`,
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
          env: { VITE_API_PROXY_TARGET: apiURL },
        },
      }),
});
