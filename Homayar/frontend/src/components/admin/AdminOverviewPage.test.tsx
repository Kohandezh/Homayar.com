import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import AdminOverviewPage from "@/components/admin/AdminOverviewPage";
import {
  useAuditEvents,
  useBillingUsage,
  useCurrentSubscription,
  useInvitations,
  useMeetings,
  useMembers,
  useReadiness,
} from "@/lib/platform-api";
import { useVoiceCapacity } from "@/lib/voice-api";
import { formatAudioSeconds, formatDate } from "@/lib/format";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, networkError, renderConsole } from "@/test/fixtures";
import type {
  AuditEvent,
  BillingUsageSnapshot,
  CurrentSubscriptionResponse,
  Meeting,
  MeetingListParams,
  MeetingStatus,
  WorkspaceInvitation,
} from "@/types/platform";

vi.mock("@/lib/platform-api", () => ({
  useMeetings: vi.fn(),
  useBillingUsage: vi.fn(),
  useCurrentSubscription: vi.fn(),
  useMembers: vi.fn(),
  useInvitations: vi.fn(),
  useAuditEvents: vi.fn(),
  useReadiness: vi.fn(),
}));

vi.mock("@/lib/voice-api", () => ({
  useVoiceCapacity: vi.fn(),
}));

const T = "2026-09-24T12:00:00Z";

type QueryState = { isPending: boolean; isError: boolean; error: unknown; data: unknown };

const pending = (): QueryState => ({ isPending: true, isError: false, error: null, data: undefined });
const failed = (error: unknown = apiError(500, "internal_error")): QueryState => ({
  isPending: false,
  isError: true,
  error,
  data: undefined,
});
const ok = (data: unknown): QueryState => ({ isPending: false, isError: false, error: null, data });

function meeting(id: string, status: MeetingStatus, title = `Meeting ${id}`): Meeting {
  return {
    id,
    workspace_id: "ws-1",
    title,
    language: "fa",
    status,
    duration_seconds: 60,
    source: "upload",
    created_at: T,
    updated_at: T,
  };
}

function usage(metric: string, used: number, limit: number | null, reserved = 0): BillingUsageSnapshot {
  return {
    metric,
    used,
    reserved,
    limit,
    remaining: limit === null ? null : limit - used - reserved,
    source: "plan",
    period_start: T,
    period_end: null,
    updated_at: T,
  };
}

function subscription(extra: Partial<CurrentSubscriptionResponse> = {}): CurrentSubscriptionResponse {
  return {
    workspace_id: "ws-1",
    plan: {
      key: "team",
      name: "Team plan",
      is_public: true,
      entitlements: {},
      price_published: false,
      pricing_mode: "manual_quote",
    },
    subscription: {
      id: "sub-1",
      plan_key: "team",
      status: "active",
      provider: "manual",
      starts_at: T,
      ends_at: "2026-12-31T00:00:00Z",
      cancelled_at: null,
      created_at: T,
      updated_at: T,
    },
    billing_mode: "manual",
    price_published: false,
    ...extra,
  };
}

function invitation(id: string, status: WorkspaceInvitation["status"]): WorkspaceInvitation {
  return {
    id,
    workspace_id: "ws-1",
    email: `${id}@example.com`,
    role: "member",
    invited_by_user_id: "user-1",
    status,
    expires_at: T,
    accepted_at: null,
    revoked_at: null,
    created_at: T,
  };
}

function auditEvent(id: string, eventType: string): AuditEvent {
  return {
    id,
    actor_user_id: "user-1",
    event_type: eventType,
    target_type: null,
    target_id: null,
    outcome: "success",
    request_id: null,
    details: {},
    occurred_at: T,
  };
}

interface Answers {
  processing: QueryState;
  attention: QueryState;
  usage: QueryState;
  subscription: QueryState;
  members: QueryState;
  invitations: QueryState;
  audit: QueryState;
  readiness: QueryState;
  voice: QueryState;
}

const ALL_DATA: Answers = {
  processing: ok({
    items: [
      meeting("m1", "completed"),
      meeting("m2", "completed"),
      meeting("m3", "completed"),
      meeting("m4", "queued"),
      meeting("m5", "transcribing"),
      meeting("m6", "failed"),
      meeting("m7", "partial"),
      meeting("m8", "cancelled"),
    ],
    next_before: null,
  }),
  attention: ok({ items: [meeting("f1", "failed", "Board review"), meeting("f2", "failed", "Weekly sync")], next_before: null }),
  usage: ok({
    workspace_id: "ws-1",
    items: [usage("seats", 4, 5), usage("audio_seconds", 600, 1800, 90), usage("concurrent_jobs", 1, null)],
  }),
  subscription: ok(subscription()),
  members: ok({ items: [], total: 12 }),
  invitations: ok({
    items: [invitation("a", "pending"), invitation("b", "pending"), invitation("c", "accepted"), invitation("d", "revoked")],
    total: 4,
  }),
  audit: ok({
    items: [auditEvent("e1", "member.removed"), auditEvent("e2", "custom.unknown_event")],
    next_cursor: null,
  }),
  readiness: ok({ status: "ready", checks: { database: "ready", redis: "ready" } }),
  voice: ok({ workspace: { workspace_id: "ws-1", held: 0, limit: 200 } }),
};

function answer(overrides: Partial<Answers> = {}) {
  const answers = { ...ALL_DATA, ...overrides };
  vi.mocked(useMeetings).mockImplementation(
    (params?: MeetingListParams) => (params?.status === "failed" ? answers.attention : answers.processing) as never,
  );
  vi.mocked(useBillingUsage).mockReturnValue(answers.usage as never);
  vi.mocked(useCurrentSubscription).mockReturnValue(answers.subscription as never);
  vi.mocked(useMembers).mockReturnValue(answers.members as never);
  vi.mocked(useInvitations).mockReturnValue(answers.invitations as never);
  vi.mocked(useAuditEvents).mockReturnValue(answers.audit as never);
  vi.mocked(useReadiness).mockReturnValue(answers.readiness as never);
  vi.mocked(useVoiceCapacity).mockReturnValue(answers.voice as never);
}

function renderPage(lang: "fa" | "en" = "en", permissions?: string[]) {
  const workspace = { ...FIXTURE_WORKSPACE, ...(permissions ? { permissions } : {}) };
  // Persian is the default; drop an English choice left by an earlier render in the same test.
  if (lang === "fa") window.localStorage.removeItem("homayar_lang");
  return renderConsole(<AdminOverviewPage />, {
    lang,
    auth: { status: "authenticated", user: FIXTURE_USER, workspace, workspaces: [workspace] },
  });
}

const CARD_NAMES_EN = ["Processing", "Needs attention", "Voice capacity", "Quota", "Plan", "Members", "Recent activity"];
const CARD_NAMES_FA = ["پردازش", "نیازمند بررسی", "ظرفیت تماس صوتی", "سهمیه", "طرح اشتراک", "اعضا", "رویدادهای اخیر"];

function region(name: string) {
  return screen.getByRole("region", { name });
}

/** The <dd> right after the <dt> with this text. */
function definition(scope: HTMLElement, term: string) {
  const dt = within(scope).getByText(term, { selector: "dt" });
  const dd = dt.nextElementSibling;
  expect(dd?.tagName).toBe("DD");
  return dd?.textContent;
}

beforeEach(() => {
  vi.clearAllMocks();
  answer();
});

describe("AdminOverviewPage", () => {
  it("shows the heading and every card with data for an owner", () => {
    renderPage("en");
    expect(screen.getByRole("heading", { level: 1, name: "Admin overview" })).toBeInTheDocument();
    for (const name of CARD_NAMES_EN) {
      expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
      expect(region(name)).not.toHaveAttribute("aria-busy");
    }
    expect(screen.queryByText("Coming soon")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("sends each request with the agreed parameters", () => {
    renderPage("en");
    const meetingParams = vi.mocked(useMeetings).mock.calls.map(([params]) => params);
    expect(meetingParams).toContainEqual({ limit: 100 });
    expect(meetingParams).toContainEqual({ status: "failed", limit: 5 });
    expect(vi.mocked(useAuditEvents).mock.calls.map(([params]) => params)).toContainEqual({ limit: 10 });
    for (const hook of [useBillingUsage, useCurrentSubscription, useMembers, useInvitations, useReadiness, useVoiceCapacity]) {
      expect(hook).toHaveBeenCalled();
    }
  });

  it("shows the workspace voice capacity to an admin who is not a platform operator", () => {
    renderPage("en");
    const card = region("Voice capacity");
    expect(within(card).getByText("0 / 200")).toBeInTheDocument();
    expect(within(card).getByRole("meter", { name: "Concurrent calls" })).toHaveAttribute("aria-valuenow", "0");
  });

  it("counts meetings by group and says which sample the numbers cover", () => {
    renderPage("en");
    const card = region("Processing");
    expect(definition(card, "Completed")).toBe("3");
    expect(definition(card, "In progress")).toBe("2");
    expect(definition(card, "Failed")).toBe("1");
    expect(definition(card, "Partial")).toBe("1");
    expect(within(card).getByText("All meetings")).toBeInTheDocument();
  });

  it("labels the counts as the last 100 meetings when there are more", () => {
    answer({ processing: ok({ items: [meeting("m1", "completed")], next_before: T }) });
    renderPage("en");
    expect(within(region("Processing")).getByText("Last 100 meetings")).toBeInTheDocument();
    expect(within(region("Processing")).queryByText("All meetings")).not.toBeInTheDocument();
  });

  it("lists failed meetings with links and a way to the processing page", () => {
    renderPage("en");
    const card = region("Needs attention");
    expect(within(card).getByRole("link", { name: "Board review" })).toHaveAttribute("href", "/meetings/f1");
    expect(within(card).getByRole("link", { name: "Weekly sync" })).toHaveAttribute("href", "/meetings/f2");
    expect(within(card).getAllByText(formatDate(T, "en"))).toHaveLength(2);
    expect(within(card).getByRole("link", { name: "View processing" })).toHaveAttribute("href", "/admin/processing");
  });

  it("shows quota rows in order with meters, near-limit text and unlimited rows", () => {
    renderPage("en");
    const card = region("Quota");
    const meters = within(card).getAllByRole("meter");
    expect(meters.map((meter) => meter.getAttribute("aria-valuenow"))).toEqual(["38", "80"]);
    expect(within(card).getByRole("meter", { name: "Audio processing" })).toHaveAttribute("aria-valuenow", "38");
    expect(within(card).getByRole("meter", { name: "Seats" })).toHaveAttribute("aria-valuenow", "80");
    expect(within(card).queryByRole("meter", { name: "Concurrent jobs" })).not.toBeInTheDocument();
    expect(within(card).getByText("Unlimited")).toBeInTheDocument();
    expect(within(card).getAllByText("Near limit")).toHaveLength(1);
    expect(within(card).getByText(new RegExp(formatAudioSeconds(690, "en")))).toBeInTheDocument();
    const labels = within(card)
      .getAllByText(/^(Audio processing|Concurrent jobs|Seats)$/)
      .map((element) => element.textContent);
    expect(labels).toEqual(["Audio processing", "Concurrent jobs", "Seats"]);
  });

  it("shows near limit at 80 percent but not at 79, and a zero limit as full", () => {
    answer({ usage: ok({ workspace_id: "ws-1", items: [usage("seats", 79, 100)] }) });
    const first = renderPage("en");
    expect(within(region("Quota")).queryByText("Near limit")).not.toBeInTheDocument();
    first.unmount();

    answer({ usage: ok({ workspace_id: "ws-1", items: [usage("seats", 80, 100)] }) });
    const second = renderPage("en");
    expect(within(region("Quota")).getByText("Near limit")).toBeInTheDocument();
    second.unmount();

    answer({ usage: ok({ workspace_id: "ws-1", items: [usage("concurrent_jobs", 0, 0)] }) });
    renderPage("en");
    expect(within(region("Quota")).getByRole("meter", { name: "Concurrent jobs" })).toHaveAttribute("aria-valuenow", "100");
    expect(within(region("Quota")).getByText("Near limit")).toBeInTheDocument();
  });

  it("shows the plan, its status and end date", () => {
    renderPage("en");
    const card = region("Plan");
    expect(within(card).getByText("Team plan")).toBeInTheDocument();
    expect(within(card).getByText("Active")).toBeInTheDocument();
    expect(within(card).getByText(formatDate("2026-12-31T00:00:00Z", "en"))).toBeInTheDocument();
  });

  it("says there is no end date when the subscription has none", () => {
    const base = subscription();
    answer({ subscription: ok({ ...base, subscription: { ...base.subscription!, ends_at: null } }) });
    renderPage("en");
    expect(within(region("Plan")).getByText("No end date")).toBeInTheDocument();
  });

  it("counts members and pending invitations", () => {
    renderPage("en");
    const card = region("Members");
    expect(definition(card, "Total members")).toBe("12");
    expect(definition(card, "Pending invitations")).toBe("2");
    expect(within(card).getByRole("link")).toHaveAttribute("href", "/admin/members");
  });

  it("lists recent events in order, with a label or the raw type", () => {
    renderPage("en");
    const card = region("Recent activity");
    const items = within(card).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Member removed");
    expect(items[1]).toHaveTextContent("custom.unknown_event");
    expect(items[0]).toHaveTextContent(formatDate(T, "en"));
    expect(within(card).getByRole("link", { name: "View audit log" })).toHaveAttribute("href", "/admin/audit");
  });

  it("shows at most 10 recent events", () => {
    const items = Array.from({ length: 12 }, (_, index) => auditEvent(`e${index}`, "meeting.uploaded"));
    answer({ audit: ok({ items, next_cursor: null }) });
    renderPage("en");
    expect(within(region("Recent activity")).getAllByRole("listitem")).toHaveLength(10);
  });
});

describe("AdminOverviewPage states", () => {
  it("shows every card busy, without data, while loading", () => {
    answer({
      processing: pending(),
      attention: pending(),
      usage: pending(),
      subscription: pending(),
      members: pending(),
      invitations: pending(),
      audit: pending(),
      readiness: pending(),
      voice: pending(),
    });
    renderPage("en");
    for (const name of CARD_NAMES_EN) {
      expect(region(name)).toHaveAttribute("aria-busy", "true");
      expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
    }
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
    expect(screen.queryByText("Completed")).not.toBeInTheDocument();
    expect(screen.queryByText("Total members")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("Checking status")).toBeInTheDocument();
  });

  it("shows an alert in every card when every request fails, and keeps the headings", () => {
    answer({
      processing: failed(),
      attention: failed(),
      usage: failed(),
      subscription: failed(),
      members: failed(),
      invitations: failed(),
      audit: failed(),
      readiness: failed(networkError()),
      voice: failed(),
    });
    renderPage("en");
    expect(screen.getByRole("heading", { level: 1, name: "Admin overview" })).toBeInTheDocument();
    for (const name of CARD_NAMES_EN) {
      expect(within(region(name)).getByRole("alert")).toBeInTheDocument();
      expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
    }
    expect(screen.getByText("Status unknown")).toBeInTheDocument();
  });

  it("keeps the other cards and the heading when one request fails", () => {
    answer({ usage: failed() });
    renderPage("en");
    expect(screen.getByRole("heading", { level: 1, name: "Admin overview" })).toBeInTheDocument();
    expect(within(region("Quota")).getByRole("alert")).toBeInTheDocument();
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(definition(region("Processing"), "Completed")).toBe("3");
    expect(within(region("Plan")).getByText("Team plan")).toBeInTheDocument();
    expect(definition(region("Members"), "Total members")).toBe("12");
    expect(within(region("Recent activity")).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("System ready")).toBeInTheDocument();
  });

  it("shows the members error when either members request fails", () => {
    answer({ invitations: failed() });
    const first = renderPage("en");
    expect(within(region("Members")).getByRole("alert")).toBeInTheDocument();
    expect(within(region("Members")).queryByText("Total members")).not.toBeInTheDocument();
    first.unmount();

    answer({ members: failed(), invitations: pending() });
    renderPage("en");
    expect(within(region("Members")).getByRole("alert")).toBeInTheDocument();
  });

  it("shows each card's empty text", () => {
    answer({
      processing: ok({ items: [], next_before: null }),
      attention: ok({ items: [], next_before: null }),
      usage: ok({ workspace_id: "ws-1", items: [usage("storage_bytes", 1, 10)] }),
      subscription: ok(subscription({ plan: null, subscription: null })),
      members: ok({ items: [], total: 0 }),
      invitations: ok({ items: [], total: 0 }),
      audit: ok({ items: [], next_cursor: null }),
    });
    renderPage("en");
    expect(within(region("Processing")).getByText("No meetings yet")).toBeInTheDocument();
    expect(region("Processing").querySelector("dl")).toBeNull();
    expect(within(region("Needs attention")).getByText("Nothing needs attention")).toBeInTheDocument();
    expect(within(region("Needs attention")).getByRole("link", { name: "View processing" })).toBeInTheDocument();
    expect(within(region("Quota")).getByText("No usage data")).toBeInTheDocument();
    expect(within(region("Plan")).getByText("No plan")).toBeInTheDocument();
    expect(definition(region("Members"), "Total members")).toBe("0");
    expect(definition(region("Members"), "Pending invitations")).toBe("0");
    expect(within(region("Recent activity")).getByText("No activity yet")).toBeInTheDocument();
  });
});

describe("AdminOverviewPage system status chip", () => {
  it("names the checks that are not ready", () => {
    answer({ readiness: ok({ status: "not_ready", checks: { database: "ready", redis: "unavailable" } }) });
    const first = renderPage("en");
    expect(screen.getByText("Degraded: Redis")).toBeInTheDocument();
    expect(screen.queryByText("System ready")).not.toBeInTheDocument();
    first.unmount();

    answer({ readiness: ok({ status: "not_ready", checks: { redis: "unavailable", database: "unavailable" } }) });
    const second = renderPage("en");
    expect(screen.getByText("Degraded: Database, Redis")).toBeInTheDocument();
    second.unmount();

    renderPage("fa");
    expect(screen.getByText("اختلال: پایگاه داده، Redis")).toBeInTheDocument();
  });

  it("names other failing checks by their raw key, after database and redis", () => {
    answer({ readiness: ok({ status: "not_ready", checks: { queue: "unavailable", redis: "unavailable", database: "ready" } }) });
    const first = renderPage("en");
    expect(screen.getByText("Degraded: Redis, queue")).toBeInTheDocument();
    first.unmount();

    renderPage("fa");
    expect(screen.getByText("اختلال: Redis، queue")).toBeInTheDocument();
  });

  it("says only Degraded when no check is named", () => {
    answer({ readiness: ok({ status: "not_ready", checks: { database: "ready", redis: "ready" } }) });
    const first = renderPage("en");
    expect(screen.getByText("Degraded")).toBeInTheDocument();
    expect(screen.queryByText(/Degraded:/)).not.toBeInTheDocument();
    first.unmount();

    answer({ readiness: ok({ status: "not_ready", checks: {} }) });
    renderPage("fa");
    expect(screen.getByText("اختلال")).toBeInTheDocument();
    expect(screen.queryByText(/اختلال:/)).not.toBeInTheDocument();
  });

  it("is outside the cards and never blanks them when it fails", () => {
    answer({ readiness: failed(networkError()) });
    renderPage("en");
    const chip = screen.getByText("Status unknown");
    expect(chip.closest("section")).toBeNull();
    expect(screen.queryByText(/Degraded/)).not.toBeInTheDocument();
    expect(screen.queryByText("System ready")).not.toBeInTheDocument();
    expect(screen.getAllByRole("region")).toHaveLength(7);
  });
});

describe("AdminOverviewPage permissions", () => {
  it("renders only the cards the workspace may read and never asks for the others", () => {
    renderPage("en", ["workspace:manage", "meeting:read"]);
    expect(screen.getAllByRole("region")).toHaveLength(3);
    expect(region("Processing")).toBeInTheDocument();
    expect(region("Needs attention")).toBeInTheDocument();
    expect(region("Voice capacity")).toBeInTheDocument();
    for (const name of ["Quota", "Plan", "Members", "Recent activity"]) {
      expect(screen.queryByRole("region", { name })).not.toBeInTheDocument();
    }
    for (const hook of [useBillingUsage, useCurrentSubscription, useMembers, useInvitations, useAuditEvents]) {
      expect(hook).not.toHaveBeenCalled();
    }
    expect(useReadiness).toHaveBeenCalled();
  });

  it("hides the meeting cards without meeting:read", () => {
    renderPage("en", ["workspace:manage", "billing:read", "member:read", "audit:read"]);
    expect(screen.queryByRole("region", { name: "Processing" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Needs attention" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Voice capacity" })).not.toBeInTheDocument();
    expect(useMeetings).not.toHaveBeenCalled();
    expect(useVoiceCapacity).not.toHaveBeenCalled();
    expect(screen.getAllByRole("region")).toHaveLength(4);
  });
});

describe("AdminOverviewPage in Persian", () => {
  it("uses Persian text and only Persian digits", () => {
    const { container } = renderPage("fa");
    expect(screen.getByRole("heading", { level: 1, name: "نمای کلی مدیریت" })).toBeInTheDocument();
    for (const name of CARD_NAMES_FA) expect(region(name)).toBeInTheDocument();
    expect(definition(region("پردازش"), "تکمیل‌شده")).toBe("۳");
    expect(definition(region("پردازش"), "در حال پردازش")).toBe("۲");
    expect(definition(region("اعضا"), "تعداد اعضا")).toBe("۱۲");
    expect(definition(region("اعضا"), "دعوت‌های در انتظار")).toBe("۲");
    expect(within(region("پردازش")).getByText("همهٔ جلسه‌ها")).toBeInTheDocument();
    expect(within(region("سهمیه")).getByText("نزدیک به سقف")).toBeInTheDocument();
    expect(within(region("سهمیه")).getByText("نامحدود")).toBeInTheDocument();
    expect(within(region("طرح اشتراک")).getByText("فعال")).toBeInTheDocument();
    expect(screen.getByText("سامانه آماده است")).toBeInTheDocument();
    expect(within(region("ظرفیت تماس صوتی")).getByText("۰ / ۲۰۰")).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/[0-9]/);
  });

  it("shows Persian empty and state texts", () => {
    answer({
      processing: ok({ items: [], next_before: null }),
      attention: ok({ items: [], next_before: null }),
      usage: ok({ workspace_id: "ws-1", items: [] }),
      subscription: ok(subscription({ plan: null, subscription: null })),
      audit: ok({ items: [], next_cursor: null }),
      readiness: pending(),
    });
    renderPage("fa");
    expect(screen.getByText("هنوز جلسه‌ای نیست")).toBeInTheDocument();
    expect(screen.getByText("موردی برای بررسی نیست")).toBeInTheDocument();
    expect(screen.getByText("داده‌ای از مصرف نیست")).toBeInTheDocument();
    expect(screen.getByText("طرحی فعال نیست")).toBeInTheDocument();
    expect(screen.getByText("هنوز رویدادی ثبت نشده")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "مشاهدهٔ پردازش" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "مشاهدهٔ گزارش رویدادها" })).toBeInTheDocument();
    expect(screen.getByText("در حال بررسی وضعیت")).toBeInTheDocument();
  });
});
