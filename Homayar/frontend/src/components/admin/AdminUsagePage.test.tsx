import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdminUsagePage from "@/components/admin/AdminUsagePage";
import {
  useBillingUsage,
  useCurrentSubscription,
  usePlanCatalog,
  useUsageEvents,
} from "@/lib/platform-api";
import { formatDate } from "@/lib/format";
import { formatDateOnly } from "@/components/admin/usage/usage-model";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, renderConsole } from "@/test/fixtures";
import type {
  BillingUsageSnapshot,
  CurrentSubscriptionResponse,
  JsonValue,
  Plan,
  UsageEvent,
  UsageEventListParams,
} from "@/types/platform";

vi.mock("@/lib/platform-api", () => ({
  useBillingUsage: vi.fn(),
  useCurrentSubscription: vi.fn(),
  usePlanCatalog: vi.fn(),
  useUsageEvents: vi.fn(),
}));

const T = "2026-09-24T12:00:00Z";
const T_END = "2026-12-31T00:00:00Z";

type QueryState = { isPending: boolean; isError: boolean; isFetching: boolean; error: unknown; data: unknown };

const pending = (): QueryState => ({ isPending: true, isError: false, isFetching: true, error: null, data: undefined });
const failed = (error: unknown = apiError(500, "internal_error")): QueryState => ({
  isPending: false,
  isError: true,
  isFetching: false,
  error,
  data: undefined,
});
const ok = (data: unknown): QueryState => ({ isPending: false, isError: false, isFetching: false, error: null, data });

function usage(metric: string, used: number, limit: number | null, reserved = 0): BillingUsageSnapshot {
  return {
    metric,
    used,
    reserved,
    limit,
    remaining: limit === null ? null : Math.max(0, limit - used - reserved),
    source: "plan",
    period_start: T,
    period_end: null,
    updated_at: T,
  };
}

function plan(key: string, name: string, entitlements: Record<string, JsonValue>): Plan {
  return { key, name, is_public: true, entitlements, price_published: false, pricing_mode: "manual_quote" };
}

function subscription(extra: Partial<CurrentSubscriptionResponse> = {}): CurrentSubscriptionResponse {
  return {
    workspace_id: "ws-1",
    plan: plan("team", "Team plan", { audio_seconds: 10800, seats: 5 }),
    subscription: {
      id: "sub-1",
      plan_key: "team",
      status: "active",
      provider: "manual",
      starts_at: T,
      ends_at: T_END,
      cancelled_at: null,
      created_at: T,
      updated_at: T,
    },
    billing_mode: "manual",
    price_published: false,
    ...extra,
  };
}

function event(id: string, metric: string, eventType: string, value: number, subjectId: string): UsageEvent {
  return { id, metric, value, event_type: eventType, subject_type: "meeting", subject_id: subjectId, occurred_at: T };
}

const LONG_ID = "meeting-alpha-0123456789abcdef";
const SHORT_ID = "meeting-beta";

interface Answers {
  usage: QueryState;
  subscription: QueryState;
  events: QueryState;
  plans: QueryState;
}

const ALL_DATA: Answers = {
  usage: ok({
    workspace_id: "ws-1",
    items: [
      usage("seats", 4, 5),
      usage("audio_seconds", 5400, 10800, 600),
      usage("concurrent_jobs", 1, null),
    ],
  }),
  subscription: ok(subscription()),
  events: ok({
    items: [
      event("e1", "audio_seconds", "committed", 5400, LONG_ID),
      event("e2", "seats", "reserved", 1, SHORT_ID),
    ],
    next_before: T,
    next_before_id: "e2",
  }),
  plans: ok({
    items: [
      plan("demo", "Free Demo", { audio_seconds: 1800, concurrent_jobs: 1, seats: 1 }),
      plan("team", "Team plan", { audio_seconds: 10800, concurrent_jobs: null, seats: 5 }),
      plan("enterprise", "Enterprise", {}),
    ],
  }),
};

function answer(overrides: Partial<Answers> = {}) {
  const answers = { ...ALL_DATA, ...overrides };
  vi.mocked(useBillingUsage).mockReturnValue(answers.usage as never);
  vi.mocked(useCurrentSubscription).mockReturnValue(answers.subscription as never);
  vi.mocked(useUsageEvents).mockReturnValue(answers.events as never);
  vi.mocked(usePlanCatalog).mockReturnValue(answers.plans as never);
}

function renderPage(lang: "fa" | "en" = "en") {
  // renderConsole only stores "en"; drop an English choice left by an earlier render in the same test.
  if (lang === "fa") window.localStorage.removeItem("homayar_lang");
  return renderConsole(<AdminUsagePage />, {
    lang,
    auth: { status: "authenticated", user: FIXTURE_USER, workspace: FIXTURE_WORKSPACE, workspaces: [FIXTURE_WORKSPACE] },
  });
}

const SECTIONS_EN = ["Current plan", "Quota usage", "Usage events", "Plan catalog"];
const SECTIONS_FA = ["طرح فعلی", "سهمیه‌ها", "رویدادهای مصرف", "طرح‌های اشتراک"];

function region(name: string) {
  return screen.getByRole("region", { name });
}

/** The <dd> right after the <dt> with this text. */
function definition(scope: HTMLElement, term: string, selector = "dt") {
  const dt = within(scope).getByText(term, { selector });
  const dd = dt.nextElementSibling;
  expect(dd?.tagName).toBe("DD");
  return dd?.textContent;
}

/** The params of the most recent usage events request. */
function lastEventParams(): UsageEventListParams {
  const calls = vi.mocked(useUsageEvents).mock.calls;
  return calls[calls.length - 1][0] as UsageEventListParams;
}

beforeEach(() => {
  vi.clearAllMocks();
  answer();
});

describe("AdminUsagePage layout (U1)", () => {
  it("shows one h1, the workspace eyebrow and four named sections with data", () => {
    renderPage("en");
    expect(screen.getByRole("heading", { level: 1, name: "Usage and subscription" })).toBeInTheDocument();
    expect(screen.getByText("Fixture workspace")).toBeInTheDocument();
    for (const name of SECTIONS_EN) {
      expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
      expect(region(name)).not.toHaveAttribute("aria-busy");
    }
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("marks every section busy, without data, while its request loads", () => {
    answer({ usage: pending(), subscription: pending(), events: pending(), plans: pending() });
    renderPage("en");
    for (const name of SECTIONS_EN) {
      expect(region(name)).toHaveAttribute("aria-busy", "true");
      expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
    }
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
    expect(screen.queryByRole("grid")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // The paging nav stays mounted while a page loads, so focus is never dropped.
    const events = region("Usage events");
    expect(within(events).getByRole("button", { name: "Previous page" })).toHaveAttribute("aria-disabled", "true");
    expect(within(events).getByRole("button", { name: "Next page" })).toHaveAttribute("aria-disabled", "true");
    expect(within(events).getByText("Page 1")).toBeInTheDocument();
  });

  it("shows one section's error inside that section only, and the others still show data", () => {
    answer({ plans: failed() });
    renderPage("en");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(within(region("Plan catalog")).getByRole("alert")).toBeInTheDocument();
    expect(within(region("Current plan")).getByText("Team plan")).toBeInTheDocument();
    expect(within(region("Quota usage")).getByRole("meter", { name: "Audio processing" })).toBeInTheDocument();
    expect(within(region("Usage events")).getByRole("grid", { name: "Usage event history" })).toBeInTheDocument();
  });
});

describe("AdminUsagePage current plan (U3)", () => {
  it("shows the plan name, status, dates and manual billing", () => {
    renderPage("en");
    const card = region("Current plan");
    expect(within(card).getByText("Team plan")).toBeInTheDocument();
    expect(within(card).getByText("Active")).toBeInTheDocument();
    expect(definition(card, "Started")).toBe(formatDateOnly(T, "en"));
    expect(definition(card, "Ends")).toBe(formatDateOnly(T_END, "en"));
    expect(definition(card, "Billing")).toBe("Manual");
  });

  it("shows Started and Ends as dates without a time", () => {
    renderPage("en");
    const card = region("Current plan");
    // The events table keeps the time; the subscription dates do not.
    expect(definition(card, "Started")).not.toMatch(/:/);
    expect(definition(card, "Ends")).not.toMatch(/:/);
  });

  it("falls back to the plan key when the response has no plan object", () => {
    const base = subscription();
    answer({ subscription: ok({ ...base, plan: null }) });
    renderPage("en");
    expect(within(region("Current plan")).getByText("team")).toBeInTheDocument();
  });

  it("says there is no end date when the subscription has none", () => {
    const base = subscription();
    answer({ subscription: ok({ ...base, subscription: { ...base.subscription!, ends_at: null } }) });
    renderPage("en");
    expect(definition(region("Current plan"), "Ends")).toBe("No end date");
  });

  it("states the missing subscription instead of empty fields", () => {
    answer({ subscription: ok(subscription({ plan: null, subscription: null })) });
    renderPage("en");
    expect(within(region("Current plan")).getByText("No active subscription")).toBeInTheDocument();
  });

  it("treats a plan without a subscription as no active subscription (U3)", () => {
    answer({ subscription: ok(subscription({ subscription: null })) });
    renderPage("en");
    expect(within(region("Current plan")).getByText("No active subscription")).toBeInTheDocument();
  });

  it("names the 409 billing configuration error", () => {
    answer({ subscription: failed(apiError(409, "billing_configuration_error")) });
    renderPage("en");
    expect(within(region("Current plan")).getByRole("alert")).toHaveTextContent(
      "Billing for this workspace is not set up yet. Contact us to finish the setup.",
    );
  });
});

describe("AdminUsagePage quota usage (U4)", () => {
  it("meters used plus reserved, shows audio in hours and minutes and the amount line with of", () => {
    renderPage("en");
    const card = region("Quota usage");
    const audio = within(card).getByRole("meter", { name: "Audio processing" });
    // (5400 + 600) of 10800 seconds = 55.55 percent, floored.
    expect(audio).toHaveAttribute("aria-valuenow", "55");
    expect(within(card).getByText("55%")).toBeInTheDocument();
    expect(within(card).getByText("1 h 40 min of 3 h")).toBeInTheDocument();
    const audioRow = audio.closest("li")!;
    expect(definition(audioRow, "Used")).toBe("1 h 30 min");
    expect(definition(audioRow, "Reserved")).toBe("10 min");
    expect(definition(audioRow, "Remaining")).toBe("1 h 20 min");
    expect(definition(audioRow, "Period")).toBe(formatDateOnly(T, "en"));
    // One visible metric name: the meter is named through aria-label, not a second label.
    expect(within(audioRow).getAllByText("Audio processing")).toHaveLength(1);
  });

  it("orders the known metrics first and keeps the server order after them", () => {
    answer({
      usage: ok({
        workspace_id: "ws-1",
        items: [usage("storage_bytes", 1, 10), usage("seats", 1, 5), usage("audio_seconds", 1, 10), usage("concurrent_jobs", 1, 10)],
      }),
    });
    renderPage("en");
    const items = within(region("Quota usage")).getAllByRole("listitem");
    expect(items).toHaveLength(4);
    expect(items[0]).toHaveTextContent("Audio processing");
    expect(items[1]).toHaveTextContent("Concurrent jobs");
    expect(items[2]).toHaveTextContent("Seats");
    expect(items[3]).toHaveTextContent("storage bytes");
  });

  it("warns from exactly 80 percent and turns danger at the limit, from the exact ratio", () => {
    const cases: Array<{ used: number; percent: string; level: string; text: string | null }> = [
      { used: 79, percent: "79", level: "normal", text: null },
      { used: 79.6, percent: "79", level: "normal", text: null },
      { used: 80, percent: "80", level: "warning", text: "Near limit" },
      { used: 99.9, percent: "99", level: "warning", text: "Near limit" },
      { used: 100, percent: "100", level: "danger", text: "Limit reached" },
      { used: 130, percent: "100", level: "danger", text: "Limit reached" },
    ];
    for (const testCase of cases) {
      answer({ usage: ok({ workspace_id: "ws-1", items: [usage("seats", testCase.used, 100)] }) });
      const view = renderPage("en");
      const card = region("Quota usage");
      const meter = within(card).getByRole("meter", { name: "Seats" });
      expect(meter, `used ${testCase.used}`).toHaveAttribute("aria-valuenow", testCase.percent);
      expect(meter.closest("li"), `used ${testCase.used}`).toHaveAttribute("data-level", testCase.level);
      if (testCase.text) {
        expect(within(card).getByText(testCase.text), `used ${testCase.used}`).toBeInTheDocument();
      } else {
        expect(within(card).queryByText("Near limit")).not.toBeInTheDocument();
        expect(within(card).queryByText("Limit reached")).not.toBeInTheDocument();
      }
      view.unmount();
    }
  });

  it("counts a zero limit as reached and shows it as 100 percent", () => {
    answer({ usage: ok({ workspace_id: "ws-1", items: [usage("concurrent_jobs", 0, 0)] }) });
    renderPage("en");
    const card = region("Quota usage");
    const meter = within(card).getByRole("meter", { name: "Concurrent jobs" });
    expect(meter).toHaveAttribute("aria-valuenow", "100");
    expect(meter.closest("li")).toHaveAttribute("data-level", "danger");
    expect(within(card).getByText("Limit reached")).toBeInTheDocument();
  });

  it("shows a quota without a limit as unlimited, with no meter and no percent", () => {
    renderPage("en");
    const card = region("Quota usage");
    expect(within(card).queryByRole("meter", { name: "Concurrent jobs" })).not.toBeInTheDocument();
    const row = within(card).getAllByRole("listitem").find((item) => within(item).queryByText("Concurrent jobs"))!;
    expect(within(row).getAllByText("Unlimited").length).toBeGreaterThan(0);
    expect(within(row).queryByText(/%|٪/)).not.toBeInTheDocument();
    expect(row).toHaveAttribute("data-level", "normal");
    expect(definition(row, "Remaining")).toBe("Unlimited");
  });

  it("joins the period end with an en dash when the server sends one", () => {
    answer({
      usage: ok({
        workspace_id: "ws-1",
        items: [{ ...usage("seats", 1, 5), period_end: T_END }],
      }),
    });
    renderPage("en");
    const card = region("Quota usage");
    expect(definition(card, "Period")).toBe(`${formatDateOnly(T, "en")} – ${formatDateOnly(T_END, "en")}`);
  });

  it("shows the empty text when the workspace has no quotas", () => {
    answer({ usage: ok({ workspace_id: "ws-1", items: [] }) });
    renderPage("en");
    expect(within(region("Quota usage")).getByText("No quotas configured for this workspace")).toBeInTheDocument();
  });
});

describe("AdminUsagePage usage events (U5)", () => {
  it("lists the events with their time, metric, type, amount and subject", () => {
    renderPage("en");
    const table = within(region("Usage events")).getByRole("grid", { name: "Usage event history" });
    const columns = within(table).getAllByRole("columnheader").map((cell) => cell.textContent);
    expect(columns).toEqual(["Time", "Metric", "Event", "Amount", "Subject"]);
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Audio processing");
    expect(rows[0]).toHaveTextContent("Committed");
    expect(rows[0]).toHaveTextContent("1 h 30 min");
    expect(rows[0]).toHaveTextContent(formatDate(T, "en"));
    expect(rows[0]).toHaveTextContent("Meeting");
    expect(rows[1]).toHaveTextContent("Reserved");
    expect(rows[1]).toHaveTextContent("1");
  });

  it("shortens a long subject id, keeps it left to right and titles the whole value", () => {
    renderPage("en");
    const card = region("Usage events");
    const short = within(card).getByText("meeting-…cdef");
    expect(short).toHaveAttribute("dir", "ltr");
    expect(short).toHaveAttribute("title", LONG_ID);
  });

  it("requests the first page with only the limit", () => {
    renderPage("en");
    expect(lastEventParams()).toEqual({ limit: 25 });
    expect(screen.getByText("Page 1")).toBeInTheDocument();
  });

  it("offers the fixed metric options behind a trigger named Filter by metric", async () => {
    const user = userEvent.setup();
    renderPage("en");
    const card = region("Usage events");
    await user.click(within(card).getByRole("button", { name: "Filter by metric" }));
    // The listbox portals to the document body, outside the section.
    const options = (await screen.findAllByRole("option")).map((option) => option.textContent);
    expect(options).toEqual(["All metrics", "Audio processing", "Concurrent jobs", "Seats"]);
  });

  it("offers the same four options with the exact Persian texts", async () => {
    const user = userEvent.setup();
    renderPage("fa");
    const card = region("رویدادهای مصرف");
    await user.click(within(card).getByRole("button", { name: "فیلتر سهمیه" }));
    const options = (await screen.findAllByRole("option")).map((option) => option.textContent);
    expect(options).toEqual(["همه سهمیه‌ها", "پردازش صوت", "پردازش‌های هم‌زمان", "ظرفیت اعضا"]);
  });

  it("filters by metric from the first page and drops the cursor", async () => {
    const user = userEvent.setup();
    renderPage("en");
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(lastEventParams()).toMatchObject({ before: T });

    await user.click(screen.getByRole("button", { name: "Filter by metric" }));
    await user.click(await screen.findByRole("option", { name: "Audio processing" }));
    expect(lastEventParams()).toEqual({ limit: 25, metric: "audio_seconds" });
    expect(screen.getByText("Page 1")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Filter by metric" }));
    await user.click(await screen.findByRole("option", { name: "All metrics" }));
    expect(lastEventParams()).toEqual({ limit: 25 });
  });

  it("walks the keyset cursor forward and back", async () => {
    const user = userEvent.setup();
    renderPage("en");
    expect(screen.getByRole("button", { name: "Previous page" })).toHaveAttribute("aria-disabled", "true");

    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(lastEventParams()).toEqual({ limit: 25, before: T, before_id: "e2" });
    expect(screen.getByText("Page 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous page" })).not.toHaveAttribute("aria-disabled", "true");

    await user.click(screen.getByRole("button", { name: "Previous page" }));
    expect(lastEventParams()).toEqual({ limit: 25 });
    expect(screen.getByText("Page 1")).toBeInTheDocument();
  });

  it("disables Next page when the last page has no cursor", () => {
    answer({ events: ok({ items: [event("e1", "seats", "reserved", 1, SHORT_ID)], next_before: null, next_before_id: null }) });
    renderPage("en");
    expect(screen.getByRole("button", { name: "Next page" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("button", { name: "Previous page" })).toHaveAttribute("aria-disabled", "true");
  });

  it("keeps the filter visible in the empty state, with the unfiltered empty text", () => {
    answer({ events: ok({ items: [], next_before: null, next_before_id: null }) });
    renderPage("en");
    const card = region("Usage events");
    expect(within(card).getByText("No usage events recorded")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Filter by metric" })).toBeInTheDocument();
  });

  it("names the metric in the empty text when a filter is chosen", async () => {
    const user = userEvent.setup();
    vi.mocked(useUsageEvents).mockImplementation(((params: UsageEventListParams) =>
      params.metric
        ? ok({ items: [], next_before: null, next_before_id: null })
        : ok({
            items: [event("e1", "seats", "reserved", 1, SHORT_ID)],
            next_before: null,
            next_before_id: null,
          })
    ) as never);
    renderPage("en");
    await user.click(screen.getByRole("button", { name: "Filter by metric" }));
    await user.click(await screen.findByRole("option", { name: "Seats" }));
    const card = region("Usage events");
    expect(within(card).getByText("No usage events for this metric")).toBeInTheDocument();
    expect(within(card).queryByText("No usage events recorded")).not.toBeInTheDocument();
  });

  it("explains an invalid cursor and keeps Previous page usable", async () => {
    const user = userEvent.setup();
    vi.mocked(useUsageEvents).mockImplementation(((params: UsageEventListParams) => {
      if (params.before !== undefined) {
        return failed(apiError(400, "invalid_usage_cursor")) as never;
      }
      return ok({
        items: [event("e1", "seats", "reserved", 1, SHORT_ID)],
        next_before: T,
        next_before_id: "e1",
      }) as never;
    }) as never);
    renderPage("en");
    await user.click(screen.getByRole("button", { name: "Next page" }));

    const card = region("Usage events");
    expect(within(card).getByRole("alert")).toHaveTextContent(
      "The event list has changed; start again from the first page.",
    );
    expect(within(card).getByRole("button", { name: "Previous page" })).toBeEnabled();
    await user.click(within(card).getByRole("button", { name: "Previous page" }));
    expect(within(region("Usage events")).queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("AdminUsagePage plan catalog (U6)", () => {
  it("compares every plan by quota and marks the current one", () => {
    renderPage("en");
    const card = region("Plan catalog");
    const table = within(card).getByRole("grid", { name: "Plan comparison" });
    const headers = within(table).getAllByRole("columnheader");
    expect(headers.map((cell) => cell.textContent)).toEqual(["Quota", "Free Demo", "Team planCurrent plan", "Enterprise"]);
    expect(within(headers[2]).getByText("Current plan")).toBeInTheDocument();

    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getAllByRole("rowheader")[0]?.textContent)).toEqual([
      "Audio processing",
      "Concurrent jobs",
      "Seats",
    ]);
    expect(within(rows[0]).getAllByRole("gridcell").map((cell) => cell.textContent)).toEqual([
      "30 min",
      "3 h",
      "By agreement",
    ]);
    expect(within(rows[1]).getAllByRole("gridcell").map((cell) => cell.textContent)).toEqual([
      "1",
      "Unlimited",
      "By agreement",
    ]);
  });

  it("always lists the three known quotas, even when no plan names one", () => {
    answer({ plans: ok({ items: [plan("team", "Team plan", {}), plan("enterprise", "Enterprise", { storage_bytes: 5 })] }) });
    renderPage("en");
    const table = within(region("Plan catalog")).getByRole("grid", { name: "Plan comparison" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getAllByRole("rowheader")[0]?.textContent)).toEqual([
      "Audio processing",
      "Concurrent jobs",
      "Seats",
      "storage bytes",
    ]);
  });

  it("asks the reader to contact us and shows no price anywhere", () => {
    renderPage("en");
    const card = region("Plan catalog");
    expect(within(card).getByRole("link", { name: "Contact us" })).toHaveAttribute("href", "/contact");
    expect(within(card).getByText(/Plans are set up by our team/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\$|€|price|pricing|تومان|ریال|قیمت/i);
  });

  it("says when the catalog is empty", () => {
    answer({ plans: ok({ items: [] }) });
    renderPage("en");
    expect(within(region("Plan catalog")).getByText("No plans to show")).toBeInTheDocument();
  });
});

describe("AdminUsagePage in Persian", () => {
  it("uses the Persian texts and only Persian digits", () => {
    const { container } = renderPage("fa");
    expect(screen.getByRole("heading", { level: 1, name: "مصرف و اشتراک" })).toBeInTheDocument();
    for (const name of SECTIONS_FA) expect(region(name)).toBeInTheDocument();
    expect(within(region("طرح فعلی")).getByText("فعال")).toBeInTheDocument();
    expect(within(region("طرح فعلی")).getByText("دستی")).toBeInTheDocument();
    expect(within(region("سهمیه‌ها")).getByText("۱ ساعت و ۴۰ دقیقه از ۳ ساعت")).toBeInTheDocument();
    expect(within(region("سهمیه‌ها")).getByText("۵۵٪")).toBeInTheDocument();
    expect(within(region("سهمیه‌ها")).getByText("نزدیک به سقف")).toBeInTheDocument();
    expect(within(region("رویدادهای مصرف")).getByText("ثبت قطعی")).toBeInTheDocument();
    expect(within(region("رویدادهای مصرف")).getByText("صفحهٔ ۱")).toBeInTheDocument();
    expect(within(region("طرح‌های اشتراک")).getByRole("link", { name: "با ما تماس بگیرید" })).toHaveAttribute(
      "href",
      "/contact",
    );
    // Everything the reader sees as a number is in Persian digits.
    expect(container.textContent).not.toMatch(/[0-9]/);
  });
});
