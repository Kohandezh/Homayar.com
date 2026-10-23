import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import SystemHealthPage from "@/components/admin/SystemHealthPage";
import {
  SYSTEM_HEALTH_QUERY_KEY,
  SYSTEM_HEALTH_REFRESH_MS,
} from "@/lib/system-health-api";
import api from "@/lib/api";
import { describeApiError } from "@/lib/error-messages";
import { formatDate, isolateLtr } from "@/lib/format";
import { platformQueryKeys } from "@/lib/platform-api";
import { FIXTURE_OPERATOR, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { SystemHealthResponse } from "@/types/system-health";

const NOW = Date.parse("2026-10-23T12:00:00Z");
// React Aria builds the cards after the read answers; a loaded machine needs more than 1 s.
const LOAD = { timeout: 5000 };
const SLOW = 20_000;

function ago(seconds: number) {
  return new Date(NOW - seconds * 1000).toISOString();
}

const HEALTHY: SystemHealthResponse = {
  status: "ok",
  checked_at: ago(10),
  dependencies: {
    database: { status: "up", probe_seconds: 0.004 },
    redis: { status: "up", probe_seconds: 0.002 },
    storage: { status: "up", probe_seconds: 0.01 },
  },
  schema: { status: "up_to_date", code_head: "e6c3a9f1d4b8", database_revision: "e6c3a9f1d4b8" },
  queue: { status: "ok", queued_due: 0, oldest_queued_due_age_seconds: 0, retry_scheduled: 2, broker_queue_length: 7 },
  sweeps: {
    status: "ok",
    tasks: [
      {
        task: "dispatch_outbox",
        status: "ok",
        expected_interval_seconds: 30,
        last_run_at: ago(20),
        // 300 s before checked_at, the age P6 names in the deny-case table.
        last_success_at: ago(310),
        last_failure_at: null,
        last_failure_class: null,
        last_duration_seconds: 0.12,
      },
    ],
  },
  backup: { status: "ok", targets: { postgres: ago(3610), objects: ago(1810), offsite: ago(7210) } },
  audit_sink: { status: "ok", last_verify_at: ago(310), last_verify_success_at: ago(310), last_failure_class: null },
  build: {
    version: "0.5.0",
    source_sha: "e6c3a9f1d4b8",
    migration_head: "e6c3a9f1d4b8",
    profile: "standard",
    python_version: "3.12.4",
  },
};

const DEGRADED: SystemHealthResponse = {
  status: "degraded",
  checked_at: ago(10),
  dependencies: {
    database: { status: "down", probe_seconds: 0.001 },
    redis: { status: "up", probe_seconds: 0.002 },
    storage: { status: "timeout", probe_seconds: null },
  },
  schema: { status: "behind", code_head: "f1d4b8e6c3a9", database_revision: "e6c3a9f1d4b8" },
  queue: {
    // Not a value the SPEC's table knows: P5 says it must be shown as itself.
    status: "rebuilding" as SystemHealthResponse["queue"]["status"],
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
        last_run_at: ago(310),
        last_success_at: ago(3610),
        last_failure_at: ago(310),
        last_failure_class: "connection_error",
        last_duration_seconds: 0.12,
      },
      {
        task: "sweep_pii",
        status: "never_run",
        expected_interval_seconds: 900,
        last_run_at: null,
        last_success_at: null,
        last_failure_at: null,
        last_failure_class: null,
        last_duration_seconds: null,
      },
    ],
  },
  backup: { status: "not_configured", targets: { postgres: null, objects: null, offsite: null } },
  audit_sink: { status: "failing", last_verify_at: ago(610), last_verify_success_at: ago(3610), last_failure_class: "smtp_error" },
  build: {
    version: "0.5.0",
    source_sha: "f1d4b8e6c3a9",
    migration_head: "e6c3a9f1d4b8",
    profile: "airgapped",
    python_version: "3.12.4",
  },
};

/** P8: the realistic outage, one source down and everything else unknown. */
const OUTAGE: SystemHealthResponse = {
  status: "down",
  checked_at: ago(10),
  dependencies: {
    database: { status: "down", probe_seconds: 0.001 },
    redis: { status: "unknown" as SystemHealthResponse["dependencies"]["database"]["status"], probe_seconds: null },
    storage: { status: "unknown" as SystemHealthResponse["dependencies"]["database"]["status"], probe_seconds: null },
  },
  schema: { status: "unknown", code_head: null, database_revision: null },
  queue: { status: "unknown", queued_due: null, oldest_queued_due_age_seconds: null, retry_scheduled: null, broker_queue_length: null },
  sweeps: { status: "unknown", tasks: [] },
  backup: { status: "unknown", targets: { postgres: null, objects: null, offsite: null } },
  audit_sink: { status: "unknown", last_verify_at: null, last_verify_success_at: null, last_failure_class: null },
  build: { version: null, source_sha: null, migration_head: null, profile: null, python_version: null },
};

type Answer = SystemHealthResponse | Error | "pending";

/** Serves GET /admin/system-health from `health` in order; the last answer repeats. */
function mockApi({ health }: { health: Answer[] }) {
  const answers = [...health];
  const get = vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url !== "/admin/system-health") throw apiError(404, "not_found");
    const next = answers.length > 1 ? answers.shift()! : answers[0];
    if (next === "pending") return new Promise(() => undefined);
    if (next instanceof Error) throw next;
    return okResponse(next);
  });
  return { get };
}

function renderPage(lang: "fa" | "en" = "en") {
  if (lang === "fa") window.localStorage.removeItem("homayar_lang");
  const workspace = { ...FIXTURE_WORKSPACE, role: "admin" as const };
  return renderConsole(<SystemHealthPage />, {
    route: "/admin/system",
    lang,
    auth: { status: "authenticated", user: FIXTURE_OPERATOR, workspace, workspaces: [workspace] },
  });
}

function region(name: string) {
  return screen.getByRole("region", { name });
}

/** The <dd> that belongs to the <dt> with text `term` inside `scope`. */
function definitionOf(scope: HTMLElement, term: string) {
  const dt = within(scope).getByText(term);
  return within(dt.parentElement as HTMLElement).getByRole("definition");
}

/** The list item of one sweep task, found by its task name (isolated, P11). */
function taskBlock(name: string) {
  return within(region("Periodic sweeps")).getByText(isolateLtr(name)).closest("li") as HTMLElement;
}

/** The chip element whose label text is `label` inside `scope`. */
function chipIn(scope: HTMLElement, label: string) {
  return within(scope).getByText(label).closest(".chip") as HTMLElement;
}

const RUNBOOK_BASE = "https://github.com/Kohandezh/Homayar.com/blob/main/docs/operations/";

describe("SystemHealthPage", () => {
  beforeEach(() => {
    // Only the clock is fixed, so the ages do not drift; timers stay real.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("requests /admin/system-health under the admin query key and refreshes every 30 seconds", () => {
    expect(SYSTEM_HEALTH_QUERY_KEY).toEqual([...platformQueryKeys.admin(), "system-health"]);
    expect(SYSTEM_HEALTH_REFRESH_MS).toBe(30_000);
  });

  it("shows the heading, the overall status line and the six cards", async () => {
    const { get } = mockApi({ health: [HEALTHY] });
    renderPage();
    expect(screen.getByRole("heading", { level: 1, name: "System health" })).toBeInTheDocument();
    await screen.findByRole("region", { name: "Dependencies" }, LOAD);

    expect(get).toHaveBeenCalledWith("/admin/system-health");
    expect(screen.getByText("Checked 10 seconds ago")).toBeInTheDocument();
    for (const name of ["Dependencies", "Processing queue", "Periodic sweeps", "Backup", "Audit sink", "Build"]) {
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
    }
  }, SLOW);

  it("lists each dependency with its status and probe time, and the schema row", async () => {
    mockApi({ health: [HEALTHY] });
    renderPage();
    const card = await screen.findByRole("region", { name: "Dependencies" }, LOAD);

    expect(within(definitionOf(card, "Database")).getByText("Up")).toBeInTheDocument();
    expect(definitionOf(card, "Database")).toHaveTextContent("0.004 s");
    expect(definitionOf(card, "Redis")).toHaveTextContent("0.002 s");
    expect(definitionOf(card, "Object storage")).toHaveTextContent("0.01 s");
    expect(within(definitionOf(card, "Schema")).getByText("Up to date")).toBeInTheDocument();
    expect(definitionOf(card, "Code head")).toHaveTextContent(isolateLtr("e6c3a9f1d4b8"));
    expect(definitionOf(card, "Database revision")).toHaveTextContent(isolateLtr("e6c3a9f1d4b8"));
  }, SLOW);

  it("colors the status chips by tone, like the voice operations page", async () => {
    mockApi({ health: [DEGRADED] });
    renderPage();
    await screen.findByRole("region", { name: "Dependencies" }, LOAD);

    expect(chipIn(region("Dependencies"), "Down")).toHaveClass("chip--danger");
    expect(chipIn(region("Dependencies"), "Timed out")).toHaveClass("chip--warning");
    expect(chipIn(region("Dependencies"), "Behind")).toHaveClass("chip--warning");
    expect(chipIn(taskBlock("dispatch_outbox"), "Stale")).toHaveClass("chip--warning");
    expect(chipIn(region("Backup"), "Not configured")).toHaveClass("chip--default");
    expect(chipIn(region("Audit sink"), "Failing")).toHaveClass("chip--warning");
    expect(chipIn(region("Processing queue"), isolateLtr("rebuilding"))).toHaveClass("chip--default");
    // The overall chip sits next to the checked line, outside any card.
    expect(chipIn(screen.getByText("Checked 10 seconds ago").parentElement as HTMLElement, "Degraded")).toHaveClass("chip--warning");
  }, SLOW);

  it("never invents a state: an unknown status is the raw string, a null value is Not reported", async () => {
    mockApi({ health: [DEGRADED] });
    renderPage();
    await screen.findByRole("region", { name: "Dependencies" }, LOAD);

    // The raw status string, isolated left to right, never "OK" or another label.
    const queueStatus = definitionOf(region("Processing queue"), "Status");
    expect(within(queueStatus).getByText(isolateLtr("rebuilding"))).toBeInTheDocument();
    expect(within(queueStatus).queryByText("OK")).not.toBeInTheDocument();

    // Null values a failed source could not give.
    expect(definitionOf(region("Dependencies"), "Object storage")).toHaveTextContent("Not reported");
    const backup = region("Backup");
    for (const target of ["PostgreSQL", "Objects", "Offsite"]) {
      expect(definitionOf(backup, target)).toHaveTextContent("Not reported");
    }
    // A missing timestamp is Not reported, never "0 seconds ago".
    expect(definitionOf(taskBlock("sweep_pii"), "Last success")).toHaveTextContent("Not reported");
  }, SLOW);

  it("shows every card age as time before checked_at, with the absolute date on the time element", async () => {
    mockApi({ health: [HEALTHY] });
    renderPage();
    await screen.findByRole("region", { name: "Dependencies" }, LOAD);

    const sweep = definitionOf(taskBlock("dispatch_outbox"), "Last success");
    expect(sweep).toHaveTextContent("5 minutes ago");
    const time = within(sweep).getByText("5 minutes ago");
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("dateTime", ago(310));
    expect(time).toHaveAttribute("title", formatDate(ago(310), "en"));

    const backup = region("Backup");
    expect(definitionOf(backup, "PostgreSQL")).toHaveTextContent("1 hour ago");
    expect(definitionOf(backup, "Objects")).toHaveTextContent("30 minutes ago");
    expect(definitionOf(backup, "Offsite")).toHaveTextContent("2 hours ago");
    const audit = region("Audit sink");
    expect(definitionOf(audit, "Last verify")).toHaveTextContent("5 minutes ago");
    expect(definitionOf(audit, "Last successful verify")).toHaveTextContent("5 minutes ago");
  }, SLOW);

  it("shows the queue numbers and the oldest due age as a duration", async () => {
    mockApi({ health: [DEGRADED] });
    renderPage();
    const queue = await screen.findByRole("region", { name: "Processing queue" }, LOAD);

    expect(definitionOf(queue, "Queued due")).toHaveTextContent("3");
    expect(definitionOf(queue, "Oldest queued age")).toHaveTextContent("5 min");
    expect(definitionOf(queue, "Retry scheduled")).toHaveTextContent("1");
    expect(definitionOf(queue, "Broker queue length")).toHaveTextContent("11");
  }, SLOW);

  it("lists the sweep tasks in API order with name, age, interval and failure class", async () => {
    mockApi({ health: [DEGRADED] });
    renderPage();
    await screen.findByRole("region", { name: "Periodic sweeps" }, LOAD);

    const card = region("Periodic sweeps");
    const names = within(card)
      .getAllByRole("listitem")
      .map((item) => item.querySelector("span")?.textContent);
    expect(names).toEqual([isolateLtr("dispatch_outbox"), isolateLtr("sweep_pii")]);

    const dispatch = taskBlock("dispatch_outbox");
    expect(definitionOf(dispatch, "Last success")).toHaveTextContent("1 hour ago");
    expect(definitionOf(dispatch, "Expected interval")).toHaveTextContent("30 s");
    expect(definitionOf(dispatch, "Last failure class")).toHaveTextContent(isolateLtr("connection_error"));
    // never_run has no age and no failure class to show.
    expect(within(taskBlock("sweep_pii")).queryByText("Last failure class")).not.toBeInTheDocument();
  }, SLOW);

  it("puts a Runbook link on every warning and danger status, and none on a healthy one", async () => {
    mockApi({ health: [DEGRADED] });
    renderPage();
    await screen.findByRole("region", { name: "Dependencies" }, LOAD);

    // Overall degraded, database down, storage timed out, schema behind, one
    // stale sweep task, failing audit sink: six links.
    const links = screen.getAllByRole("link", { name: "Runbook" });
    expect(links).toHaveLength(6);
    expect(links[0]).toHaveAttribute("href", `${RUNBOOK_BASE}health-and-recovery.md#first-response`);
    const database = definitionOf(region("Dependencies"), "Database");
    expect(within(database).getByRole("link", { name: "Runbook" })).toHaveAttribute(
      "href",
      `${RUNBOOK_BASE}alerts.md#homayardatabaseunavailable`,
    );
    const runbook = within(database).getByRole("link", { name: "Runbook" });
    expect(runbook).toHaveAttribute("target", "_blank");
    expect(runbook).toHaveAttribute("rel", "noreferrer");
    // The raw neutral queue status, the not_configured backup and the unknown
    // sweeps card have no link.
    expect(within(definitionOf(region("Processing queue"), "Status")).queryByRole("link")).not.toBeInTheDocument();
    expect(within(region("Backup")).queryByRole("link")).not.toBeInTheDocument();
    expect(within(definitionOf(region("Periodic sweeps"), "Status")).queryByRole("link")).not.toBeInTheDocument();
    expect(within(taskBlock("sweep_pii")).queryByRole("link")).not.toBeInTheDocument();
  }, SLOW);

  it("shows no runbook link anywhere on a healthy answer", async () => {
    mockApi({ health: [HEALTHY] });
    renderPage();
    await screen.findByRole("region", { name: "Dependencies" }, LOAD);
    expect(screen.queryByRole("link", { name: "Runbook" })).not.toBeInTheDocument();
  }, SLOW);

  it("renders the realistic outage without crashing: overall Down, database Down with its runbook", async () => {
    mockApi({ health: [OUTAGE] });
    renderPage();
    await screen.findByRole("region", { name: "Dependencies" }, LOAD);

    for (const name of ["Dependencies", "Processing queue", "Periodic sweeps", "Backup", "Audit sink", "Build"]) {
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
    }
    const header = screen.getByText("Checked 10 seconds ago").parentElement as HTMLElement;
    expect(chipIn(header, "Down")).toHaveClass("chip--danger");
    expect(within(header).getByRole("link", { name: "Runbook" })).toHaveAttribute(
      "href",
      `${RUNBOOK_BASE}health-and-recovery.md#first-response`,
    );
    const database = definitionOf(region("Dependencies"), "Database");
    expect(within(database).getByText("Down")).toBeInTheDocument();
    expect(within(database).getByRole("link", { name: "Runbook" })).toHaveAttribute(
      "href",
      `${RUNBOOK_BASE}alerts.md#homayardatabaseunavailable`,
    );
    // Nulls everywhere still read as Not reported, not as zeros.
    const build = region("Build");
    for (const field of ["Version", "Source SHA", "Migration head", "Profile", "Python version"]) {
      expect(definitionOf(build, field)).toHaveTextContent("Not reported");
    }
  }, SLOW);

  it("shows the loading state, then the first-load error with a working Try again", async () => {
    mockApi({ health: ["pending"] });
    const loading = renderPage();
    expect(screen.getByRole("status")).toHaveTextContent("Loading system health");
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
    loading.unmount();
    vi.restoreAllMocks();

    const failure = apiError(500, "internal_error");
    const { get } = mockApi({ health: [failure, HEALTHY] });
    const user = userEvent.setup();
    renderPage();
    const alert = await screen.findByRole("alert", undefined, LOAD);
    expect(alert).toHaveTextContent(describeApiError(failure, { lang: "en" }).text);
    // No data, no status label at all.
    expect(screen.queryByText("Checked")).not.toBeInTheDocument();
    expect(screen.queryByText("OK")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "System health" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("region", { name: "Dependencies" }, LOAD)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
  }, SLOW);

  it("reads the health again when Refresh is pressed", async () => {
    const { get } = mockApi({ health: [HEALTHY] });
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("region", { name: "Dependencies" }, LOAD);
    expect(get).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("region", { name: "Dependencies" })).toBeInTheDocument();
  }, SLOW);
});

describe("SystemHealthPage refresh", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("asks for the health again every 30 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { get } = mockApi({ health: [HEALTHY] });
    renderPage();
    await screen.findByRole("region", { name: "Dependencies" }, LOAD);
    expect(get).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(29_000);
    });
    expect(get).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_100);
    });
    expect(get).toHaveBeenCalledTimes(2);
  }, SLOW);

  it("keeps the last data and its checked line, and shows the alert, when a background refresh fails", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(NOW);
    mockApi({ health: [HEALTHY, apiError(503, "service_unavailable")] });
    renderPage();
    await screen.findByRole("region", { name: "Dependencies" }, LOAD);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_100);
    });
    expect(screen.getByRole("region", { name: "Dependencies" })).toBeInTheDocument();
    expect(screen.getByText(/Checked \d+ seconds? ago/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  }, SLOW);
});

describe("SystemHealthPage in Persian", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("uses Persian text and Persian digits everywhere, and keeps identifiers left to right", async () => {
    mockApi({ health: [HEALTHY] });
    renderPage("fa");
    expect(screen.getByRole("heading", { level: 1, name: "سلامت سامانه" })).toBeInTheDocument();
    await screen.findByRole("region", { name: "وابستگی‌ها" }, LOAD);

    for (const name of ["وابستگی‌ها", "صف پردازش", "کارهای دوره‌ای", "پشتیبان‌گیری", "مقصد گزارش رویدادها", "نسخهٔ نصب‌شده"]) {
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
    }
    expect(screen.getByText("بررسی‌شده ۱۰ ثانیه پیش")).toBeInTheDocument();

    const dependencies = region("وابستگی‌ها");
    expect(within(definitionOf(dependencies, "پایگاه داده")).getByText("در دسترس")).toBeInTheDocument();
    expect(definitionOf(dependencies, "پایگاه داده")).toHaveTextContent("۰٫۰۰۴ ثانیه");
    expect(definitionOf(dependencies, "فضای ذخیره‌سازی")).toHaveTextContent("۰٫۰۱ ثانیه");
    expect(within(definitionOf(dependencies, "اسکیما")).getByText("به‌روز")).toBeInTheDocument();
    expect(definitionOf(dependencies, "SHA کد")).toHaveTextContent(isolateLtr("e6c3a9f1d4b8"));
    expect(definitionOf(dependencies, "SHA پایگاه داده")).toHaveTextContent(isolateLtr("e6c3a9f1d4b8"));

    const queue = region("صف پردازش");
    expect(definitionOf(queue, "در صف اجرا")).toHaveTextContent("۰");
    expect(definitionOf(queue, "قدیمی‌ترین مورد صف")).toHaveTextContent("۰ ثانیه");
    expect(definitionOf(queue, "طول صف کارگزار")).toHaveTextContent("۷");

    const sweeps = region("کارهای دوره‌ای");
    const dispatch = within(sweeps).getByText(isolateLtr("dispatch_outbox")).closest("li") as HTMLElement;
    expect(within(dispatch).getByText(isolateLtr("dispatch_outbox"))).toBeInTheDocument();
    expect(within(dispatch).getByText("۵ دقیقه پیش")).toBeInTheDocument();
    expect(within(dispatch).getByText("۳۰ ثانیه")).toBeInTheDocument();

    const backup = region("پشتیبان‌گیری");
    expect(definitionOf(backup, "PostgreSQL")).toHaveTextContent("۱ ساعت پیش");
    expect(definitionOf(backup, "اشیاء")).toHaveTextContent("۳۰ دقیقه پیش");
    expect(definitionOf(backup, "خارج از سایت")).toHaveTextContent("۲ ساعت پیش");

    const build = region("نسخهٔ نصب‌شده");
    expect(definitionOf(build, "نسخه")).toHaveTextContent(isolateLtr("0.5.0"));
    expect(definitionOf(build, "نسخهٔ پایتون")).toHaveTextContent(isolateLtr("3.12.4"));
    expect(screen.getByRole("button", { name: "بازخوانی" })).toBeInTheDocument();
  }, SLOW);

  it("shows the Persian runbook link and failure class on a degraded answer", async () => {
    mockApi({ health: [DEGRADED] });
    renderPage("fa");
    await screen.findByRole("region", { name: "وابستگی‌ها" }, LOAD);

    const header = screen.getByText("بررسی‌شده ۱۰ ثانیه پیش").parentElement as HTMLElement;
    expect(within(header).getByText("دچار اختلال")).toBeInTheDocument();
    expect(within(header).getByRole("link", { name: "راهنمای رفع مشکل" })).toHaveAttribute(
      "href",
      `${RUNBOOK_BASE}health-and-recovery.md#first-response`,
    );
    const database = definitionOf(region("وابستگی‌ها"), "پایگاه داده");
    expect(within(database).getByText("از کار افتاده")).toBeInTheDocument();
    expect(within(database).getByRole("link", { name: "راهنمای رفع مشکل" })).toHaveAttribute(
      "href",
      `${RUNBOOK_BASE}alerts.md#homayardatabaseunavailable`,
    );
    const dispatch = within(region("کارهای دوره‌ای")).getByText(isolateLtr("dispatch_outbox")).closest("li") as HTMLElement;
    expect(definitionOf(dispatch, "آخرین ردهٔ خطا")).toHaveTextContent(isolateLtr("connection_error"));
    expect(screen.getAllByText("گزارش نشده").length).toBeGreaterThanOrEqual(5);
  }, SLOW);

  it("shows the Persian loading and first-load error texts", async () => {
    mockApi({ health: ["pending"] });
    const loading = renderPage("fa");
    expect(screen.getByRole("status")).toHaveTextContent("در حال بارگذاری سلامت سامانه");
    loading.unmount();
    vi.restoreAllMocks();

    mockApi({ health: [apiError(500, "internal_error")] });
    renderPage("fa");
    const alert = await screen.findByRole("alert", undefined, LOAD);
    expect(alert).toHaveTextContent(describeApiError(apiError(500, "internal_error"), { lang: "fa" }).text);
    expect(screen.getByRole("button", { name: "تلاش دوباره" })).toBeInTheDocument();
  }, SLOW);
});
