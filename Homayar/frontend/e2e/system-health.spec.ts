import { expect, test, type Page } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";

/**
 * HOM-232: the operator-only system health page and the sidebar version
 * line. The demo seed has no platform operator, so:
 *
 * - test 1 reads the real backend as the demo owner (`system_role` "user"):
 *   no System health link, No access on the URL, the version line in the
 *   footer, no request to /admin/system-health from the page, and a direct
 *   GET of the real endpoint (HOM-227 is merged) answering 403
 *   platform_operator_required;
 * - tests 2 and 3 keep the owner's real session but mock `/auth/me`
 *   (operator) and `/admin/system-health` with a degraded payload (one
 *   dependency down, one sweep stale, backup not configured, one unknown
 *   raw status): test 2 reads the English page, test 3 the Persian one.
 *
 * No test may write to the shared backend. Every route below is registered
 * before the first navigation, and any write to `/api/v1/` that no mock
 * answers is aborted in the browser and recorded, so a bug fails the test
 * instead of changing the demo data.
 */

/** An identifier as the page shows it: between the invisible isolates of `isolateLtr` (src/lib/format.ts). */
const ltr = (value: string) => `\u2066${value}\u2069`;

const RUNBOOK_BASE = "https://github.com/Kohandezh/Homayar.com/blob/main/docs/operations/";

/** Aborts every unmocked write to the API; returns the list of what it stopped. */
async function blockApiWrites(page: Page) {
  const blocked: string[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    if (request.method() === "GET" || request.method() === "HEAD" || request.method() === "OPTIONS") {
      await route.fallback();
      return;
    }
    blocked.push(`${request.method()} ${new URL(request.url()).pathname}`);
    await route.abort();
  });
  return blocked;
}

/** The real session answer, read-only, with the platform operator role added. */
async function mockOperatorSession(page: Page) {
  await page.route("**/api/v1/auth/me", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    const response = await route.fetch();
    const session = await response.json();
    await route.fulfill({ response, json: { ...session, user: { ...session.user, system_role: "operator" } } });
  });
}

/** The degraded answer of HOM-227: database down, storage timed out, schema behind, one stale sweep, backup not configured, a queue status the console does not know. */
function degradedHealth() {
  const checkedAt = new Date(Date.now() - 10_000).toISOString();
  return {
    status: "degraded",
    checked_at: checkedAt,
    dependencies: {
      database: { status: "down", probe_seconds: 0.001 },
      redis: { status: "up", probe_seconds: 0.002 },
      storage: { status: "timeout", probe_seconds: null },
    },
    schema: { status: "behind", code_head: "f1d4b8e6c3a9", database_revision: "e6c3a9f1d4b8" },
    queue: {
      status: "rebuilding",
      queued_due: 3,
      oldest_queued_due_age_seconds: 300,
      retry_scheduled: 1,
      broker_queue_length: 11,
    },
    sweeps: {
      status: "unknown",
      tasks: [
        {
          task: "dispatch_outbox",
          status: "stale",
          expected_interval_seconds: 30,
          last_run_at: checkedAt,
          // 300 s before checked_at, so the card shows "5 minutes ago".
          last_success_at: new Date(Date.parse(checkedAt) - 300_000).toISOString(),
          last_failure_at: null,
          last_failure_class: "connection_error",
          last_duration_seconds: 0.12,
        },
      ],
    },
    backup: { status: "not_configured", targets: { postgres: null, objects: null, offsite: null } },
    audit_sink: { status: "failing", last_verify_at: checkedAt, last_verify_success_at: checkedAt, last_failure_class: "smtp_error" },
    build: { version: "0.5.0", source_sha: "f1d4b8e6c3a9", migration_head: "e6c3a9f1d4b8", profile: "airgapped", python_version: "3.12.4" },
  };
}

/** Serves the mocked health read and records any write attempt on the endpoint. */
async function mockSystemHealth(page: Page, blocked: string[], health: () => unknown) {
  await page.route("**/api/v1/admin/system-health", async (route) => {
    if (route.request().method() !== "GET") {
      blocked.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
      await route.abort();
      return;
    }
    await route.fulfill({ json: health() as object });
  });
}

test("an owner who is not a platform operator gets No access, the version line, and no health request", async ({ page }) => {
  const blocked = await blockApiWrites(page);
  let healthReads = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.endsWith("/api/v1/admin/system-health")) healthReads += 1;
  });

  await resumeSession(page, "owner");
  await page.goto(consolePath("/admin"));

  // V4: anyone who sees the Administration section reads the build version
  // line, isolated left to right inside the (possibly RTL) footer.
  const versionLine = new RegExp(`^\u2066Homayar 0\\.5\\.0 · (unreleased|[0-9a-f]{7})\u2069$`);
  await expect(page.getByText(versionLine)).toBeVisible();
  await expect(page.getByRole("link", { name: "Settings", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "System health" })).toHaveCount(0);

  healthReads = 0;
  await page.goto(consolePath("/admin/system"));
  await expect(page.getByRole("heading", { name: "No access", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "System health" })).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(`${consolePath("/admin/system")}$`));
  await page.waitForLoadState("networkidle");
  // The guard never mounts the page, so the route asks for no health.
  expect(healthReads).toBe(0);

  // The real endpoint (HOM-227) refuses the owner: the same GET the page
  // would send, through the page's own request context (same cookies, so it
  // goes through the app origin and its /api proxy, exactly like the axios
  // client) and with the X-Workspace-ID header src/lib/api.ts takes from
  // localStorage. No fixed workspace id.
  const workspaceId = await page.evaluate(() => window.localStorage.getItem("homayar_workspace_id"));
  expect(workspaceId).toBeTruthy();
  const denied = await page.request.get("/api/v1/admin/system-health", {
    headers: { "X-Workspace-ID": workspaceId ?? "" },
  });
  expect(denied.status()).toBe(403);
  expect(await denied.json()).toMatchObject({ error: { code: "platform_operator_required" } });

  expect(blocked).toEqual([]);
});

test("a platform operator reads the degraded system health (system health API mocked)", async ({ page }) => {
  const blocked = await blockApiWrites(page);
  await mockSystemHealth(page, blocked, degradedHealth);
  await mockOperatorSession(page);

  await resumeSession(page, "owner");
  await page.goto(consolePath("/admin/system"));

  await expect(page.getByRole("heading", { name: "System health", level: 1 })).toBeVisible();
  await expect(page.getByRole("link", { name: "System health" })).toBeVisible();
  await expect(page.getByText(/^Checked /)).toBeVisible();

  for (const name of ["Dependencies", "Processing queue", "Periodic sweeps", "Backup", "Audit sink", "Build"]) {
    await expect(page.getByRole("region", { name, exact: true })).toBeVisible();
  }

  // The overall chip and its first-response runbook.
  await expect(page.getByText("Degraded", { exact: true })).toBeVisible();
  const runbookWithHref = (href: string) =>
    page.getByRole("link", { name: "Runbook", exact: true }).and(page.locator(`a[href="${href}"]`));
  await expect(runbookWithHref(`${RUNBOOK_BASE}health-and-recovery.md#first-response`)).toBeVisible();

  // The database row: Down with its own runbook anchor, the probe next to it.
  const dependencies = page.getByRole("region", { name: "Dependencies", exact: true });
  await expect(dependencies.getByText("Down", { exact: true })).toBeVisible();
  await expect(runbookWithHref(`${RUNBOOK_BASE}alerts.md#homayardatabaseunavailable`)).toBeVisible();
  await expect(dependencies.getByText("Timed out", { exact: true })).toBeVisible();
  await expect(dependencies.getByText("Not reported", { exact: true })).toBeVisible();

  // The raw unknown status is the string itself, never OK.
  const queue = page.getByRole("region", { name: "Processing queue", exact: true });
  await expect(queue.getByText(ltr("rebuilding"))).toBeVisible();
  await expect(queue.getByText("OK", { exact: true })).toHaveCount(0);
  await expect(queue.getByText("5 min", { exact: true })).toBeVisible();

  // The stale sweep: the task name carries the isolate marks (P11), the age
  // counts from checked_at, and the sweeps runbook anchor is on the task.
  const sweeps = page.getByRole("region", { name: "Periodic sweeps", exact: true });
  await expect(sweeps.getByText(ltr("dispatch_outbox"))).toBeVisible();
  await expect(sweeps.getByText("Stale", { exact: true })).toBeVisible();
  await expect(sweeps.getByText("5 minutes ago", { exact: true })).toBeVisible();
  await expect(sweeps.getByRole("link", { name: "Runbook", exact: true })).toHaveAttribute(
    "href",
    `${RUNBOOK_BASE}alerts.md#homayarperiodicsweepstale`,
  );

  // Backup not configured is a clear neutral state, not an error: no alert
  // anywhere on the page, and the chip text says what it is.
  await expect(page.getByRole("region", { name: "Backup", exact: true }).getByText("Not configured", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Audit sink", exact: true }).getByText("Failing", { exact: true })).toBeVisible();

  expect(blocked).toEqual([]);
});

test("the Persian system health page is right to left (system health API mocked)", async ({ page }) => {
  const blocked = await blockApiWrites(page);
  await mockSystemHealth(page, blocked, degradedHealth);
  await mockOperatorSession(page);

  await resumeSession(page, "owner", "fa");
  await page.goto(consolePath("/admin/system"));

  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("heading", { name: "سلامت سامانه", level: 1 })).toBeVisible();
  await expect(page.getByText(/^بررسی‌شده /)).toBeVisible();

  for (const name of ["وابستگی‌ها", "صف پردازش", "کارهای دوره‌ای", "پشتیبان‌گیری", "مقصد گزارش رویدادها", "نسخهٔ نصب‌شده"]) {
    await expect(page.getByRole("region", { name, exact: true })).toBeVisible();
  }

  const dependencies = page.getByRole("region", { name: "وابستگی‌ها", exact: true });
  await expect(dependencies.getByText("از کار افتاده", { exact: true })).toBeVisible();
  const persianRunbook = page
    .getByRole("link", { name: "راهنمای رفع مشکل", exact: true })
    .and(page.locator(`a[href="${RUNBOOK_BASE}alerts.md#homayardatabaseunavailable"]`));
  await expect(persianRunbook).toBeVisible();

  const sweeps = page.getByRole("region", { name: "کارهای دوره‌ای", exact: true });
  await expect(sweeps.getByText("قدیمی", { exact: true })).toBeVisible();
  await expect(sweeps.getByText("۵ دقیقه پیش", { exact: true })).toBeVisible();
  await expect(sweeps.getByText("گزارش نشده", { exact: true })).toHaveCount(0);

  // Persian digits in the queue numbers, the task name still isolated left to right.
  const queue = page.getByRole("region", { name: "صف پردازش", exact: true });
  await expect(queue.getByText(ltr("rebuilding"))).toBeVisible();
  await expect(queue.getByText("۱۱", { exact: true })).toBeVisible();
  await expect(sweeps.getByText(ltr("dispatch_outbox"))).toBeVisible();

  expect(blocked).toEqual([]);
});
