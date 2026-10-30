import { Suspense } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useSearchParams } from "react-router";

import { AppRoutes } from "@/AppRoutes";
import TrendsPage from "@/components/trends/TrendsPage";
import api from "@/lib/api";
import { MAIN_NAV } from "@/lib/navigation";
import {
  FIXTURE_USER,
  FIXTURE_WORKSPACE,
  apiError,
  okResponse,
  renderConsole,
} from "@/test/fixtures";
import type { CoverageCounter } from "@/types/organizations";
import type { Team } from "@/types/platform";
import type {
  IssueTrendResponse,
  TrendCell,
  TrendLinkResponse,
  TrendPoint,
  TrendRow,
} from "@/types/trends";

// Friday 2026-10-30, 12:00 in Tehran: the current week began on Saturday 2026-10-24.
const NOW = new Date("2026-10-30T08:30:00Z");
const LOAD = { timeout: 5000 };
const WEEK_STARTS = ["2026-09-26", "2026-10-03", "2026-10-10", "2026-10-17"];
const PERIODS_EN = ["Sep 26, 2026", "Oct 3, 2026", "Oct 10, 2026", "Oct 17, 2026"];

function cell(overrides: Partial<TrendCell> = {}): TrendCell {
  return {
    status: "computed",
    numerator: 12,
    denominator: 40,
    ratio: 0.3,
    reason: null,
    minimum_sample: 30,
    ...overrides,
  };
}

const INSUFFICIENT = cell({ status: "insufficient_sample", numerator: null, ratio: null, denominator: 12, reason: "insufficient_sample" });
const WITHHELD = cell({ status: "withheld", numerator: null, ratio: null, denominator: 50, reason: "small_group" });
const EMPTY = cell({ status: "not_computable", numerator: null, ratio: null, denominator: 0, reason: "empty_denominator" });
const NO_SOURCE = cell({ status: "not_computable", numerator: null, ratio: null, denominator: 40, reason: "scorecard_results_unavailable" });

function row(level: "family" | "issue", family: string, issue: string | null, value: TrendCell, pending: number | null = 0): TrendRow {
  return { level, family, issue, cell: value, pending_review: pending, absent_pending_review: 0, missing: {} };
}

function counter(overrides: Partial<CoverageCounter> = {}): CoverageCounter {
  return {
    definition: "coverage",
    numerator_label: "processed",
    denominator_label: "received",
    numerator: 18,
    denominator: 20,
    ratio: 0.9,
    status: "computed",
    reason: null,
    buckets: {},
    ...overrides,
  };
}

/** Per-period cells of each row, by row key; the window rows are built from the same table. */
interface RowSpec {
  level: "family" | "issue";
  family: string;
  issue: string | null;
  window: TrendCell;
  periods: TrendCell[];
  pending?: number | null;
}

const ROWS: RowSpec[] = [
  // Two computed periods: the trend line is drawn.
  {
    level: "family",
    family: "call_reason",
    issue: null,
    window: cell(),
    periods: [cell({ numerator: 3, denominator: 10, ratio: 0.333 }), cell({ numerator: 5, denominator: 10, ratio: 0.5 }), INSUFFICIENT, cell({ numerator: 4, denominator: 10, ratio: 0.4 })],
    pending: 2,
  },
  // Too few calls everywhere: warnings and no trend line.
  { level: "issue", family: "call_reason", issue: "intent_request_support", window: INSUFFICIENT, periods: [INSUFFICIENT, INSUFFICIENT, INSUFFICIENT, INSUFFICIENT] },
  // Exactly one computed period: still no trend line.
  { level: "issue", family: "call_reason", issue: "intent_report_dissatisfaction", window: WITHHELD, periods: [cell({ numerator: 7, denominator: 35, ratio: 0.2 }), WITHHELD, WITHHELD, WITHHELD] },
  { level: "family", family: "qa", issue: null, window: NO_SOURCE, periods: [EMPTY, EMPTY, EMPTY, EMPTY] },
];

function points(specs: RowSpec[]): TrendPoint[] {
  return WEEK_STARTS.map((day, index) => ({
    from: `${day}T00:00:00+03:30`,
    to: `${WEEK_STARTS[index + 1] ?? "2026-10-24"}T00:00:00+03:30`,
    partial: index === 3,
    population: { included: 10, excluded: { duplicate_media: 0 } },
    rows: specs.map((spec) => row(spec.level, spec.family, spec.issue, spec.periods[index])),
  }));
}

function trends(overrides: Partial<IssueTrendResponse> = {}, specs: RowSpec[] = ROWS): IssueTrendResponse {
  return {
    notices: ["correlation_not_cause"],
    window: { from: "2026-09-25T20:30:00+00:00", to: "2026-10-23T20:30:00+00:00", basis: "ingestion_received_at" },
    scope: { restricted: false },
    series: { grain: "week", timezone: "Asia/Tehran", calendar: "gregorian", week_starts_on: "saturday" },
    population: { included: 40, excluded: { duplicate_media: 1 } },
    rows: specs.map((spec) => row(spec.level, spec.family, spec.issue, spec.window, spec.pending ?? 0)),
    points: points(specs),
    coverage: {
      version: "coverage/1",
      window: { from: "2026-09-25T20:30:00+00:00", to: "2026-10-23T20:30:00+00:00", basis: "ingestion_received_at" },
      basis: "trend_population",
      processing: counter(),
      evaluation: counter({ numerator: 0, denominator: 0, ratio: null, status: "not_computable", reason: "empty_denominator" }),
    },
    missing_data: { indicators: ["evaluation_unavailable", "mystery_gap" as never] },
    disclosure: { status: "released", reason: null, kind: null, masked: [] },
    ...overrides,
  };
}

const TEAMS: Team[] = [
  { id: "team-sales", workspace_id: "ws-1", name: "Sales", slug: "sales", is_active: true, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
];

function links(meetingIds: string[], total = meetingIds.length, offset = 0): TrendLinkResponse {
  return {
    cell: cell(),
    page: { offset, limit: 25, total },
    conversations: meetingIds.map((id, index) => ({
      meeting_id: id,
      state: "counted",
      analysis_run_id: `run-${id}`,
      evidence_ids: Array.from({ length: index + 1 }, (_, n) => `ev-${id}-${n}`),
    })),
  };
}

type Answer<T> = T | Error | Promise<T>;

/** Answers the page's reads; every request is recorded with its params. */
function mockReads({
  trendAnswers = [trends()],
  linkAnswer = links(["meet-1", "meet-2"]),
}: {
  trendAnswers?: Array<Answer<IssueTrendResponse>>;
  linkAnswer?: Answer<TrendLinkResponse> | ((params: Record<string, unknown>) => Answer<TrendLinkResponse>);
} = {}) {
  const trendCalls: Array<Record<string, unknown>> = [];
  const linkCalls: Array<Record<string, unknown>> = [];
  const get = vi.spyOn(api, "get").mockImplementation(async (url: string, config?: { params?: unknown }) => {
    const params = (config?.params ?? {}) as Record<string, unknown>;
    if (url === "/trends/issues") {
      const answer = trendAnswers[Math.min(trendCalls.length, trendAnswers.length - 1)];
      trendCalls.push(params);
      const value = await answer;
      if (value instanceof Error) throw value;
      return okResponse(value);
    }
    if (url === "/trends/conversations") {
      linkCalls.push(params);
      const value = await (typeof linkAnswer === "function" ? linkAnswer(params) : linkAnswer);
      if (value instanceof Error) throw value;
      return okResponse(value);
    }
    if (url === "/teams") return okResponse({ items: TEAMS, total: TEAMS.length });
    throw apiError(404, "not_found");
  });
  return { get, trendCalls, linkCalls };
}

function UrlProbe() {
  const [params] = useSearchParams();
  return <output data-testid="url">{params.toString()}</output>;
}

function renderTrends(route = "/trends", lang: "fa" | "en" = "en") {
  return renderConsole(
    <>
      <TrendsPage />
      <UrlProbe />
    </>,
    {
      route,
      lang,
      auth: { status: "authenticated", user: FIXTURE_USER, workspace: FIXTURE_WORKSPACE, workspaces: [FIXTURE_WORKSPACE] },
    },
  );
}

const grid = () => screen.findByRole("grid", { name: "Issue trends" }, LOAD);

function rowOf(name: string) {
  const header = screen.getByRole("rowheader", { name });
  const element = header.closest('[role="row"]');
  if (!(element instanceof HTMLElement)) throw new Error(`no row for ${name}`);
  return element;
}

/** The cells of a row after the row header: window, the four periods, then the trend column. */
function cellsOf(name: string) {
  return within(rowOf(name)).getAllByRole("gridcell");
}

// React Aria backs the team select with a hidden native one.
function teamSelect(): HTMLSelectElement {
  const select = screen.getByTestId("hidden-select-container").querySelector("select");
  if (!select) throw new Error("no team select");
  return select;
}

const DEFAULT_PARAMS = {
  from: "2026-09-25T20:30:00.000Z",
  to: "2026-10-23T20:30:00.000Z",
  grain: "week",
  timezone: "Asia/Tehran",
};

describe("TrendsPage", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  describe("route and defaults", () => {
    it("is in the main navigation right after Meetings", () => {
      const index = MAIN_NAV.findIndex((entry) => entry.path === "/meetings");
      expect(MAIN_NAV[index + 1]).toMatchObject({ path: "/trends", label: { fa: "روند مسائل", en: "Issue trends" } });
    });

    it("opens at /trends inside the app layout", async () => {
      mockReads();
      window.localStorage.removeItem("homayar_lang");
      renderConsole(
        <Suspense fallback={<p>Loading page</p>}>
          <AppRoutes />
        </Suspense>,
        {
          route: "/trends",
          lang: "en",
          auth: { status: "authenticated", user: FIXTURE_USER, workspace: FIXTURE_WORKSPACE, workspaces: [FIXTURE_WORKSPACE] },
        },
      );
      expect(await screen.findByRole("heading", { level: 1, name: "Issue trends" }, { timeout: 15000 })).toBeInTheDocument();
      expect(screen.getByRole("navigation", { name: "Main navigation" })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Issue trends" })).toHaveAttribute("href", "/trends");
    }, 20000);

    it("asks once for the 4 closed Tehran weeks by week, with no team and no agent", async () => {
      const { get, trendCalls } = mockReads();
      renderTrends();
      await grid();
      expect(trendCalls).toEqual([DEFAULT_PARAMS]);
      expect(get).toHaveBeenCalledWith("/trends/issues", { params: DEFAULT_PARAMS });
      expect(trendCalls[0]).not.toHaveProperty("team_id");
      expect(trendCalls[0]).not.toHaveProperty("agent_user_id");
      expect(screen.getByRole("button", { name: "Last 4 weeks" })).toHaveAttribute("aria-pressed", "true");
      expect(screen.getByRole("button", { name: "Last 12 weeks" })).toHaveAttribute("aria-pressed", "false");
      expect(screen.getByRole("button", { name: "Week" })).toHaveAttribute("aria-pressed", "true");
    });

    it("reads Persian right to left with Persian digits", async () => {
      mockReads();
      renderTrends("/trends", "fa");
      expect(await screen.findByRole("heading", { level: 1, name: "روند مسائل" }, LOAD)).toBeInTheDocument();
      expect(document.documentElement.dir).toBe("rtl");
      await screen.findByRole("grid", { name: "روند مسائل" }, LOAD);
      const family = cellsOf("دلایل تماس");
      expect(family[0]).toHaveTextContent("۱۲ از ۴۰ · ۳۰٪");
      expect(family[0]).toHaveTextContent("۲ در انتظار بازبینی");
      expect(cellsOf("درخواست پشتیبانی")[0]).toHaveTextContent("تماس کافی نیست");
      expect(cellsOf("درخواست پشتیبانی")[0]).toHaveTextContent("۱۲ از ۳۰ لازم");
      expect(cellsOf("ثبت شکایت")[0]).toHaveTextContent("برای حفاظت از گروه کوچک پنهان شده");
      expect(cellsOf("ثبت شکایت")[0]).toHaveTextContent("۵۰ تماس");
      expect(cellsOf("کنترل کیفیت")[0]).toHaveTextContent("هنوز داده‌ای نیست");
      expect(cellsOf("کنترل کیفیت")[1]).toHaveTextContent("در این بازه تماسی نیست");
      expect(screen.getByText("پوشش پردازش")).toBeInTheDocument();
      expect(screen.getByText("۱۸ از ۲۰ · ۹۰٪")).toBeInTheDocument();
      expect(screen.getByText("۴۰ تماس شمرده شد")).toBeInTheDocument();
      expect(screen.getByRole("img", { name: "روند: دلایل تماس" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "نمایش تماس‌ها: دلایل تماس، کل بازه" })).toBeInTheDocument();
      expect(screen.getByText("این شمارش‌ها نشان می‌دهد در تماس‌ها چه گذشت، نه علت آن را.")).toBeInTheDocument();
      // The period columns carry Persian dates, never Western digits.
      const headers = screen.getAllByRole("columnheader").map((header) => header.textContent ?? "");
      expect(headers.slice(2, 6).every((text) => /[۰-۹]/.test(text) && !/[0-9]/.test(text))).toBe(true);
      expect(headers[5]).toContain("ناقص");
    });
  });

  describe("filters", () => {
    it("sends the chosen team and drops it again for All teams, keeping it in the URL", async () => {
      const { trendCalls } = mockReads();
      renderTrends();
      await grid();
      await waitFor(() => expect(teamSelect().options.length).toBeGreaterThan(1));

      fireEvent.change(teamSelect(), { target: { value: "team-sales" } });
      await waitFor(() => expect(trendCalls.at(-1)).toEqual({ ...DEFAULT_PARAMS, team_id: "team-sales" }));
      expect(screen.getByTestId("url").textContent).toContain("team_id=team-sales");

      fireEvent.change(teamSelect(), { target: { value: "__all__" } });
      await waitFor(() => expect(trendCalls.at(-1)).toEqual(DEFAULT_PARAMS));
      expect(trendCalls.at(-1)).not.toHaveProperty("team_id");
      expect(screen.getByTestId("url").textContent).not.toContain("team_id");
    });

    it("changes the range with Last 12 weeks and the grain with Month", async () => {
      const { trendCalls } = mockReads();
      renderTrends();
      await grid();

      fireEvent.click(screen.getByRole("button", { name: "Last 12 weeks" }));
      await waitFor(() => expect(trendCalls.at(-1)).toEqual({ ...DEFAULT_PARAMS, from: "2026-07-31T20:30:00.000Z" }));
      expect(screen.getByTestId("url").textContent).toBe("from=2026-08-01&to=2026-10-23&grain=week");
      expect(screen.getByRole("button", { name: "Last 12 weeks" })).toHaveAttribute("aria-pressed", "true");

      fireEvent.click(screen.getByRole("button", { name: "Month" }));
      await waitFor(() =>
        expect(trendCalls.at(-1)).toEqual({ ...DEFAULT_PARAMS, from: "2026-07-31T20:30:00.000Z", grain: "month" }),
      );
      // Pressing the chosen grain again keeps it: the choice is never empty.
      fireEvent.click(screen.getByRole("button", { name: "Month" }));
      expect(screen.getByRole("button", { name: "Month" })).toHaveAttribute("aria-pressed", "true");
      expect(screen.getByTestId("url").textContent).toContain("grain=month");
    });

    it("reads a custom range from the URL and ends it at the midnight after its last day", async () => {
      const { trendCalls } = mockReads();
      renderTrends("/trends?from=2026-10-01&to=2026-10-10&team_id=team-sales&grain=month");
      await grid();
      expect(trendCalls[0]).toEqual({
        from: "2026-09-30T20:30:00.000Z",
        to: "2026-10-10T20:30:00.000Z",
        grain: "month",
        timezone: "Asia/Tehran",
        team_id: "team-sales",
      });
      // A custom range is neither preset.
      expect(screen.getByRole("button", { name: "Last 4 weeks" })).toHaveAttribute("aria-pressed", "false");
      expect(screen.getByRole("button", { name: "Last 12 weeks" })).toHaveAttribute("aria-pressed", "false");
    });
  });

  describe("numbers", () => {
    it("shows the coverage with its denominators, and Not available for a counter it cannot compute", async () => {
      mockReads();
      renderTrends();
      await grid();
      const coverage = screen.getByRole("region", { name: "Coverage" });
      const processing = within(coverage).getByText("Processing coverage").closest("div") as HTMLElement;
      expect(processing).toHaveTextContent("18 of 20 · 90%");
      const evaluation = within(coverage).getByText("Evaluation coverage").closest("div") as HTMLElement;
      expect(evaluation).toHaveTextContent("Not available");
      expect(evaluation).not.toHaveTextContent("%");
      expect(within(coverage).getByText("40 calls counted")).toBeInTheDocument();
    });

    it("renders every cell status with its denominator and nothing more", async () => {
      mockReads();
      renderTrends();
      await grid();

      const [window, ...rest] = cellsOf("Call reasons");
      expect(window).toHaveTextContent("12 of 40 · 30%");
      expect(window).toHaveTextContent("2 waiting for review");
      // 0.333 is shown as the API's ratio rounded, not as 3 / 10 recomputed.
      expect(rest[0]).toHaveTextContent("3 of 10 · 33%");

      const insufficient = cellsOf("Support request")[0];
      expect(insufficient).toHaveTextContent("Too few calls");
      expect(insufficient).toHaveTextContent("12 of 30 needed");
      expect(insufficient).not.toHaveTextContent("%");
      expect(within(insufficient).queryByRole("button")).not.toBeInTheDocument();

      const withheld = cellsOf("Complaint")[0];
      expect(withheld).toHaveTextContent("Withheld to protect a small group");
      expect(withheld).toHaveTextContent("50 calls");
      expect(withheld).not.toHaveTextContent("%");
      expect(within(withheld).queryByRole("button")).not.toBeInTheDocument();

      const [noSource, empty] = cellsOf("Quality checks");
      expect(noSource).toHaveTextContent("Not available");
      expect(noSource).toHaveTextContent("No source data yet");
      expect(empty).toHaveTextContent("No calls in this period");
      expect(within(noSource).queryByRole("button")).not.toBeInTheDocument();
    });

    it("shows an unknown not-computable reason as its raw code", async () => {
      const odd = cell({ status: "not_computable", numerator: null, ratio: null, denominator: 5, reason: "taxonomy_retired" });
      mockReads({
        trendAnswers: [trends({}, [{ level: "family", family: "call_reason", issue: null, window: odd, periods: [odd, odd, odd, odd] }])],
      });
      renderTrends();
      await grid();
      expect(cellsOf("Call reasons")[0]).toHaveTextContent("taxonomy_retired");
    });

    it("pairs every percent on the page with its denominator, apart from it", async () => {
      mockReads();
      renderTrends();
      await grid();
      const texts = [...screen.getAllByRole("gridcell"), screen.getByRole("region", { name: "Coverage" })].map(
        (element) => element.textContent ?? "",
      );
      expect(texts.some((text) => text.includes("%"))).toBe(true);
      // Each percent must be the end of `N of D · P%`: a bare `2040%` (no separator) fails.
      for (const text of texts) {
        const percents = text.match(/%/g)?.length ?? 0;
        const paired = text.match(/\d+ of \d+ · \d+%/g)?.length ?? 0;
        expect(paired, text).toBe(percents);
      }
    });

    it("draws a trend only for a row with at least two computed periods", async () => {
      mockReads();
      renderTrends();
      await grid();
      const line = screen.getByRole("img", { name: "Trend: Call reasons" });
      expect(cellsOf("Call reasons").at(-1)).toContainElement(line);
      // Three computed periods of four: three points on the line.
      expect(line.querySelectorAll("circle")).toHaveLength(3);
      expect(screen.queryByRole("img", { name: "Trend: Support request" })).not.toBeInTheDocument();
      expect(screen.queryByRole("img", { name: "Trend: Complaint" })).not.toBeInTheDocument();
      expect(screen.queryByRole("img", { name: "Trend: Quality checks" })).not.toBeInTheDocument();
      // The row header keeps the plain name.
      expect(screen.getByRole("rowheader", { name: "Call reasons" })).toBeInTheDocument();
    });

    it("labels each period by its Tehran start day and marks the partial one", async () => {
      mockReads();
      renderTrends();
      await grid();
      const headers = screen.getAllByRole("columnheader").map((header) => header.textContent ?? "");
      expect(headers[0]).toBe("Issue");
      expect(headers[1]).toBe("Whole range");
      expect(headers.slice(2, 6).map((text) => text.replace("partial", ""))).toEqual(PERIODS_EN);
      expect(headers[5]).toContain("partial");
      expect(headers[6]).toBe("Trend");
    });
  });

  describe("notices", () => {
    it("shows the notice and the data gaps, and no scope banner when the scope is open", async () => {
      mockReads();
      renderTrends();
      await grid();
      expect(screen.getByText("These counts show what happened in calls. They do not show a cause.")).toBeInTheDocument();
      expect(screen.queryByText("You see only the calls in your record scope.")).not.toBeInTheDocument();
      const gaps = screen.getByRole("region", { name: "Data gaps" });
      expect(within(gaps).getByText("Evaluation data is not available")).toBeInTheDocument();
      expect(within(gaps).getByText("mystery_gap")).toBeInTheDocument();
    });

    it("shows the scope banner when the scope is restricted", async () => {
      mockReads({ trendAnswers: [trends({ scope: { restricted: true } })] });
      renderTrends();
      await grid();
      expect(screen.getByText("You see only the calls in your record scope.")).toBeInTheDocument();
    });

    it("replaces masked periods with one note and keeps the whole range", async () => {
      mockReads({ trendAnswers: [trends({ disclosure: { status: "masked", reason: "small_group", kind: "rate", masked: ["points"] } })] });
      renderTrends();
      await grid();
      expect(screen.getByRole("note")).toHaveTextContent("Per-period numbers are withheld to protect a small group.");
      const headers = screen.getAllByRole("columnheader").map((header) => header.textContent);
      expect(headers).toEqual(["Issue", "Whole range", "Trend"]);
      expect(cellsOf("Call reasons")[0]).toHaveTextContent("12 of 40");
      expect(screen.queryByRole("img", { name: "Trend: Call reasons" })).not.toBeInTheDocument();
    });

    it("replaces a masked whole range with its note and keeps the periods", async () => {
      mockReads({ trendAnswers: [trends({ disclosure: { status: "masked", reason: "small_group", kind: "rate", masked: ["window"] } })] });
      renderTrends();
      await grid();
      expect(screen.getByRole("note")).toHaveTextContent("Totals for the whole range are withheld to protect a small group.");
      const headers = screen.getAllByRole("columnheader").map((header) => header.textContent ?? "");
      expect(headers).not.toContain("Whole range");
      expect(headers).toHaveLength(6);
      expect(screen.queryByRole("button", { name: "Show calls: Call reasons, whole range" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: `Show calls: Call reasons, ${PERIODS_EN[0]}` })).toBeInTheDocument();
    });
  });

  describe("drill-down", () => {
    it("offers Show calls only on computed cells", async () => {
      const { linkCalls } = mockReads();
      renderTrends();
      await grid();
      const buttons = screen.getAllByRole("button", { name: /^Show calls: / }).map((button) => button.getAttribute("aria-label"));
      expect(buttons).toEqual([
        "Show calls: Call reasons, whole range",
        `Show calls: Call reasons, ${PERIODS_EN[0]}`,
        `Show calls: Call reasons, ${PERIODS_EN[1]}`,
        `Show calls: Call reasons, ${PERIODS_EN[3]}`,
        `Show calls: Complaint, ${PERIODS_EN[0]}`,
      ]);
      expect(linkCalls).toEqual([]);
    });

    it("asks for the whole-range cell's calls and links each answered meeting", async () => {
      const { linkCalls } = mockReads();
      renderTrends();
      await grid();
      fireEvent.click(screen.getByRole("button", { name: "Show calls: Call reasons, whole range" }));

      const drawer = await screen.findByRole("dialog", { name: "Supporting calls" }, LOAD);
      expect(within(drawer).getByRole("heading", { name: "Supporting calls" })).toBeInTheDocument();
      await within(drawer).findByRole("link", { name: "Open call 1" }, LOAD);
      expect(linkCalls).toEqual([{ ...DEFAULT_PARAMS, trend_view: "issues", row: "family:call_reason", offset: 0, limit: 25 }]);
      expect(linkCalls[0]).not.toHaveProperty("point");
      expect(drawer).toHaveTextContent("12 of 40");
      expect(drawer).toHaveTextContent("These counts show what happened in calls. They do not show a cause.");
      const anchors = within(drawer).getAllByRole("link");
      expect(anchors.map((link) => link.getAttribute("href"))).toEqual(["/meetings/meet-1", "/meetings/meet-2"]);
      expect(within(drawer).getByRole("link", { name: "Open call 2" })).toHaveAttribute("href", "/meetings/meet-2");
      expect(drawer).toHaveTextContent("Evidence: 2");
    });

    it("sends the point index and the chosen team for a period cell of an issue row", async () => {
      const { linkCalls } = mockReads();
      renderTrends("/trends?team_id=team-sales");
      await grid();
      fireEvent.click(screen.getByRole("button", { name: `Show calls: Complaint, ${PERIODS_EN[0]}` }));
      await screen.findByRole("dialog", { name: "Supporting calls" }, LOAD);
      await waitFor(() => expect(linkCalls).toHaveLength(1));
      expect(linkCalls[0]).toEqual({
        ...DEFAULT_PARAMS,
        team_id: "team-sales",
        trend_view: "issues",
        row: "issue:intent_report_dissatisfaction",
        point: 0,
        offset: 0,
        limit: 25,
      });
    });

    it("pages through the calls 25 at a time, numbering across pages", async () => {
      const firstPage = Array.from({ length: 25 }, (_, index) => `meet-${index + 1}`);
      const { linkCalls } = mockReads({
        linkAnswer: (params) => (params.offset === 0 ? links(firstPage, 27) : links(["meet-26", "meet-27"], 27, 25)),
      });
      renderTrends();
      await grid();
      fireEvent.click(screen.getByRole("button", { name: "Show calls: Call reasons, whole range" }));
      const drawer = await screen.findByRole("dialog", { name: "Supporting calls" }, LOAD);
      await within(drawer).findByRole("link", { name: "Open call 25" }, LOAD);
      expect(within(drawer).queryByRole("button", { name: "Previous" })).not.toBeInTheDocument();

      fireEvent.click(within(drawer).getByRole("button", { name: "Next" }));
      const call26 = await within(drawer).findByRole("link", { name: "Open call 26" }, LOAD);
      expect(call26).toHaveAttribute("href", "/meetings/meet-26");
      expect(linkCalls.at(-1)).toMatchObject({ offset: 25, limit: 25 });
      expect(within(drawer).getAllByRole("link")).toHaveLength(2);
      expect(within(drawer).queryByRole("button", { name: "Next" })).not.toBeInTheDocument();

      fireEvent.click(within(drawer).getByRole("button", { name: "Previous" }));
      await within(drawer).findByRole("link", { name: "Open call 1" }, LOAD);
      expect(linkCalls.at(-1)).toMatchObject({ offset: 0 });
    });

    it("offers no paging when every call fits on one page", async () => {
      mockReads({ linkAnswer: links(["meet-1", "meet-2"], 25) });
      renderTrends();
      await grid();
      fireEvent.click(screen.getByRole("button", { name: "Show calls: Call reasons, whole range" }));
      const drawer = await screen.findByRole("dialog", { name: "Supporting calls" }, LOAD);
      await within(drawer).findByRole("link", { name: "Open call 1" }, LOAD);
      expect(within(drawer).queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
      expect(within(drawer).queryByRole("button", { name: "Previous" })).not.toBeInTheDocument();
    });

    it("numbers the links and pages from the page the API answered", async () => {
      // The server answers a later page than the one asked for: its page wins.
      const { linkCalls } = mockReads({
        linkAnswer: (params) => (params.offset === 0 ? links(["meet-51", "meet-52"], 52, 50) : links(["meet-26"], 52, 25)),
      });
      renderTrends();
      await grid();
      fireEvent.click(screen.getByRole("button", { name: "Show calls: Call reasons, whole range" }));
      const drawer = await screen.findByRole("dialog", { name: "Supporting calls" }, LOAD);
      expect(await within(drawer).findByRole("link", { name: "Open call 51" }, LOAD)).toHaveAttribute("href", "/meetings/meet-51");
      expect(within(drawer).getByRole("link", { name: "Open call 52" })).toBeInTheDocument();
      expect(within(drawer).queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
      fireEvent.click(within(drawer).getByRole("button", { name: "Previous" }));
      await within(drawer).findByRole("link", { name: "Open call 26" }, LOAD);
      expect(linkCalls.at(-1)).toMatchObject({ offset: 25, limit: 25 });
    });

    it("closes without asking for the closed cell again", async () => {
      const { linkCalls } = mockReads();
      renderTrends();
      await grid();
      fireEvent.click(screen.getByRole("button", { name: "Show calls: Call reasons, whole range" }));
      const drawer = await screen.findByRole("dialog", { name: "Supporting calls" }, LOAD);
      await within(drawer).findByRole("link", { name: "Open call 1" }, LOAD);
      fireEvent.click(within(drawer).getByRole("button", { name: "Close supporting calls" }));
      await waitFor(() => expect(screen.queryByRole("dialog", { name: "Supporting calls" })).not.toBeInTheDocument(), LOAD);
      expect(linkCalls).toHaveLength(1);
    });

    it("says the calls are not available on a refusal, with no link", async () => {
      mockReads({ linkAnswer: apiError(403, "trend_links_withheld") });
      renderTrends();
      await grid();
      fireEvent.click(screen.getByRole("button", { name: "Show calls: Call reasons, whole range" }));
      const drawer = await screen.findByRole("dialog", { name: "Supporting calls" }, LOAD);
      expect(await within(drawer).findByText("These calls are not available.", {}, LOAD)).toBeInTheDocument();
      expect(within(drawer).queryByRole("link")).not.toBeInTheDocument();
    });
  });

  describe("states", () => {
    it("shows the loading state and no numbers until the answer arrives", async () => {
      let release: (value: IssueTrendResponse) => void = () => undefined;
      const gate = new Promise<IssueTrendResponse>((resolve) => {
        release = resolve;
      });
      mockReads({ trendAnswers: [gate] });
      renderTrends();
      expect(await screen.findByText("Loading issue trends", {}, LOAD)).toBeInTheDocument();
      expect(screen.queryByRole("grid")).not.toBeInTheDocument();
      release(trends());
      expect(await grid()).toBeInTheDocument();
    });

    it("shows the error with Try again and no table, and loads on retry", async () => {
      const { trendCalls } = mockReads({ trendAnswers: [apiError(422, "trend_grain_invalid"), trends()] });
      renderTrends();
      expect(await screen.findByRole("alert", {}, LOAD)).toBeInTheDocument();
      expect(screen.queryByRole("grid")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(await grid()).toBeInTheDocument();
      expect(trendCalls).toHaveLength(2);
    });

    it("says there is no data for a range without rows", async () => {
      mockReads({ trendAnswers: [trends({}, [])] });
      renderTrends();
      expect(await screen.findByText("No trend data for this range.", {}, LOAD)).toBeInTheDocument();
      expect(screen.queryByRole("grid")).not.toBeInTheDocument();
    });
  });
});
