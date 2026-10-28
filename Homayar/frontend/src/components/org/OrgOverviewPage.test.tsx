import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";

import api from "@/lib/api";
import { formatAudioHours } from "@/lib/format";
import { apiError, okResponse } from "@/test/fixtures";
import {
  LOAD,
  ORG_ID,
  aggregate,
  aggregatesResponse,
  chooseOption,
  mockOrgReads,
  optionNames,
  orgUrl,
  organization,
  renderOrg,
  setupUser,
  withheld,
} from "@/components/org/org-test-kit";

const AGGREGATES = `/organizations/${ORG_ID}/aggregates`;
const NOW = new Date("2026-10-28T10:00:00.000Z");
const DIGIT = /[0-9۰-۹]/;

const TWO = [
  aggregate(),
  aggregate({
    workspace_id: "ws-2",
    workspace_name: "Shiraz",
    meetings_total: 5,
    meetings_by_status: {},
    audio_seconds_total: 3600,
    action_items_total: 3,
  }),
];
const SMALL_TEAM = withheld("small_group", { workspace_id: "ws-3", workspace_name: "Small team" });
const MASHHAD = withheld("complementary_disclosure", { workspace_id: "ws-4", workspace_name: "Mashhad" });
// `masked` should not reach this route; it is hidden like withheld, with the generic sentence.
const KARAJ = withheld(null, {
  workspace_id: "ws-5",
  workspace_name: "Karaj",
  disclosure: { status: "masked", reason: null, kind: null, masked: ["points"] },
});

/** Every visible string the tests read, verbatim from SPEC section 5, in both languages. */
const TEXT = {
  en: {
    period: /Period/,
    grid: "Workspace totals",
    chart: /Meetings per workspace/,
    workspaces: "Workspaces",
    meetings: "Meetings",
    audio: "Audio",
    actionItems: "Action items",
    options: ["Last 4 closed weeks (default)", "Last 13 closed weeks", "Last 26 closed weeks", "Last 52 closed weeks"],
    help: "Counts use whole closed weeks, Saturday to Friday, Tehran time. The current week is not counted until it ends.",
    defaultWindow: "Showing Sep 26, 2026 to Oct 23, 2026.",
    snappedWindow: "Showing Aug 22, 2026 to Oct 16, 2026.",
    askedFrom: "Jul 25, 2026",
    hidden: "Hidden",
    chartHidden: (name: string) => `${name}: hidden to protect privacy`,
    smallGroup:
      "Small team: hidden to protect privacy. In at least one week of this period, fewer than 5 agents had calls in this workspace.",
    complementary:
      "Mashhad: hidden to protect privacy. Showing these counts could reveal the counts of a smaller group by subtraction.",
    generic: "Karaj: hidden to protect privacy.",
    leaveOutOne: "Totals leave out 1 workspace hidden to protect privacy.",
    leaveOutTwo: "Totals leave out 2 workspaces hidden to protect privacy.",
    noData: "No workspace data for this period.",
    totalsFailed: "The totals could not be loaded.",
    retry: "Try again",
    numbers: { workspacesTwo: "2", meetings: "35", actionItems: "30", tehran: "30", zero: "0" },
  },
  fa: {
    period: /بازه/,
    grid: "جمع هر فضای کاری",
    chart: /جلسه‌ها به تفکیک فضای کاری/,
    workspaces: "فضاهای کاری",
    meetings: "جلسه‌ها",
    audio: "صدا",
    actionItems: "اقدام‌ها",
    options: ["۴ هفتهٔ کامل گذشته (پیش‌فرض)", "۱۳ هفتهٔ کامل گذشته", "۲۶ هفتهٔ کامل گذشته", "۵۲ هفتهٔ کامل گذشته"],
    help: "شمارش‌ها فقط هفته‌های کامل را در بر می‌گیرند، از شنبه تا جمعه به وقت تهران. هفتهٔ جاری تا تمام نشود شمرده نمی‌شود.",
    defaultWindow: "نمایش از ۴ مهر ۱۴۰۵ تا ۱ آبان ۱۴۰۵.",
    snappedWindow: "نمایش از ۳۱ مرداد ۱۴۰۵ تا ۲۴ مهر ۱۴۰۵.",
    askedFrom: "۳ مرداد ۱۴۰۵",
    hidden: "پنهان",
    chartHidden: (name: string) => `${name}: پنهان برای حفظ حریم خصوصی`,
    smallGroup:
      "Small team: برای حفظ حریم خصوصی پنهان است. در دست‌کم یک هفته از این بازه، کمتر از ۵ کارشناس در این فضای کاری تماس داشته‌اند.",
    complementary:
      "Mashhad: برای حفظ حریم خصوصی پنهان است. نمایش این شمارش‌ها می‌تواند با یک تفریق ساده، شمارش‌های یک گروه کوچک‌تر را آشکار کند.",
    generic: "Karaj: برای حفظ حریم خصوصی پنهان است.",
    leaveOutOne: "جمع‌ها ۱ فضای کاری را که برای حفظ حریم خصوصی پنهان است در بر نمی‌گیرند.",
    leaveOutTwo: "جمع‌ها ۲ فضای کاری را که برای حفظ حریم خصوصی پنهان است در بر نمی‌گیرند.",
    noData: "برای این بازه داده‌ای از فضاهای کاری نیست.",
    totalsFailed: "آمار بارگیری نشد.",
    retry: "تلاش دوباره",
    numbers: { workspacesTwo: "۲", meetings: "۳۵", actionItems: "۳۰", tehran: "۳۰", zero: "۰" },
  },
} as const;

function cardValue(label: string) {
  const term = screen.getAllByText(label).find((element) => element.tagName === "DT");
  if (!term) throw new Error(`no card ${label}`);
  return term.parentElement?.querySelector("dd")?.textContent;
}

function aggregateCalls(get: ReturnType<typeof mockOrgReads>) {
  return get.mock.calls.filter(([url]) => url === AGGREGATES);
}

function tableRows(name: string) {
  const rows = within(screen.getByRole("grid", { name })).getAllByRole("row").slice(1);
  return rows.map((row) => Array.from(row.querySelectorAll("th, td")).map((cell) => cell.textContent));
}

/** The bar fill of one chart row; null when the row draws no bar. */
function barOf(chart: HTMLElement, name: string) {
  return within(chart).getByText(name).parentElement?.querySelector<HTMLElement>("[style]") ?? null;
}

function listLines() {
  return screen.queryAllByRole("listitem").map((item) => item.textContent);
}

/** A window the server may answer with instead of the asked one: Aug 22 to Oct 16, 2026, Tehran. */
const SNAPPED_WINDOW = {
  from: "2026-08-21T20:30:00.000Z",
  to: "2026-10-16T20:30:00.000Z",
  basis: "ingestion_received_at",
} as const;

/**
 * Answers an aggregates read that carries `since` with SNAPPED_WINDOW; a read
 * without params keeps the default window. So the snapped line can only
 * appear after a period is really chosen.
 */
function snapWhenAsked(get: ReturnType<typeof mockOrgReads>) {
  const original = get.getMockImplementation()!;
  get.mockImplementation((url: string, ...rest: unknown[]) => {
    const since = (rest[0] as { params?: { since?: string } } | undefined)?.params?.since;
    if (url === AGGREGATES && since) {
      return Promise.resolve(okResponse(aggregatesResponse([aggregate()], { since, coverage_window: SNAPPED_WINDOW })));
    }
    return original(url, ...(rest as []));
  });
}

function useFixedClock() {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
}

describe("organization overview", { timeout: 60_000 }, () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  describe.each(["en", "fa"] as const)("in %s", (lang) => {
    const text = TEXT[lang];
    const audio = (seconds: number) => formatAudioHours(seconds, lang);

    it("shows a released cohort: counts in the chart and the table, totals summed", async () => {
      mockOrgReads({ aggregates: TWO });
      renderOrg(orgUrl(""), lang);
      await screen.findByRole("grid", { name: text.grid }, LOAD);

      expect(cardValue(text.workspaces)).toBe(text.numbers.workspacesTwo);
      expect(cardValue(text.meetings)).toBe(text.numbers.meetings);
      expect(cardValue(text.audio)).toBe(audio(4765));
      expect(cardValue(text.actionItems)).toBe(text.numbers.actionItems);
      expect(screen.queryByText(text.leaveOutOne)).not.toBeInTheDocument();
      expect(screen.queryByText(text.hidden)).not.toBeInTheDocument();

      const chart = screen.getByRole("img", { name: text.chart });
      expect(chart.getAttribute("aria-label")).toContain(`Tehran office: ${text.numbers.tehran}`);
      expect(barOf(chart, "Tehran office")?.style.width).toBe("100%");
      expect(within(chart).getByText(text.numbers.tehran)).toBeInTheDocument();

      const shirazRow = tableRows(text.grid)[1];
      expect(shirazRow[0]).toBe("Shiraz");
      // A missing status is a real 0 for a released workspace.
      expect(shirazRow[2]).toBe(text.numbers.zero);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("hides a withheld workspace: Hidden cells, no bar, a reason line and a totals note", async () => {
      mockOrgReads({ aggregates: [aggregate(), SMALL_TEAM] });
      renderOrg(orgUrl(""), lang);
      await screen.findByRole("grid", { name: text.grid }, LOAD);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();

      const [released, hidden] = tableRows(text.grid);
      expect(released[0]).toBe("Tehran office");
      expect(released[1]).toBe(text.numbers.tehran);
      expect(hidden).toEqual(["Small team", text.hidden, text.hidden, text.hidden, text.hidden, text.hidden]);
      for (const cell of hidden.slice(1)) expect(cell).not.toMatch(DIGIT);

      const chart = screen.getByRole("img", { name: text.chart });
      const label = chart.getAttribute("aria-label") ?? "";
      expect(label).toContain(text.chartHidden("Small team"));
      expect(label).not.toMatch(new RegExp(`Small team: [^${lang === "fa" ? "،" : ","}]*${DIGIT.source}`));
      expect(barOf(chart, "Small team")).toBeNull();
      expect(barOf(chart, "Tehran office")?.style.width).toBe("100%");
      expect(within(chart).getByText(text.hidden)).toBeInTheDocument();

      expect(listLines()).toContain(text.smallGroup);

      // The workspace count includes the hidden one; the sums leave it out.
      expect(cardValue(text.workspaces)).toBe(text.numbers.workspacesTwo);
      expect(cardValue(text.meetings)).toBe(text.numbers.tehran);
      expect(cardValue(text.audio)).toBe(audio(1165));
      expect(screen.getByText(text.leaveOutOne)).toBeInTheDocument();
    });

    it("shows Hidden, never 0, on the sum cards when every workspace is hidden", async () => {
      mockOrgReads({ aggregates: [MASHHAD, KARAJ] });
      renderOrg(orgUrl(""), lang);
      await screen.findByRole("grid", { name: text.grid }, LOAD);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();

      expect(cardValue(text.workspaces)).toBe(text.numbers.workspacesTwo);
      expect(cardValue(text.meetings)).toBe(text.hidden);
      expect(cardValue(text.audio)).toBe(text.hidden);
      expect(cardValue(text.actionItems)).toBe(text.hidden);
      expect(screen.getByText(text.leaveOutTwo)).toBeInTheDocument();

      expect(tableRows(text.grid)).toEqual([
        ["Mashhad", text.hidden, text.hidden, text.hidden, text.hidden, text.hidden],
        ["Karaj", text.hidden, text.hidden, text.hidden, text.hidden, text.hidden],
      ]);
      const chart = screen.getByRole("img", { name: text.chart });
      expect(chart.getAttribute("aria-label")).toContain(text.chartHidden("Mashhad"));
      expect(chart.getAttribute("aria-label")).toContain(text.chartHidden("Karaj"));
      expect(chart.querySelector("[style]")).toBeNull();

      const lines = listLines();
      expect(lines).toContain(text.complementary);
      expect(lines).toContain(text.generic);
    });

    it("hides a released workspace that still has a null count", async () => {
      mockOrgReads({ aggregates: [aggregate({ workspace_name: "Odd", action_items_total: null })] });
      renderOrg(orgUrl(""), lang);
      await screen.findByRole("grid", { name: text.grid }, LOAD);
      expect(tableRows(text.grid)[0]).toEqual(["Odd", text.hidden, text.hidden, text.hidden, text.hidden, text.hidden]);
      expect(cardValue(text.meetings)).toBe(text.hidden);
      expect(screen.getByText(text.leaveOutOne)).toBeInTheDocument();
    });

    it("starts on the default 4 closed weeks: four options, no params, the server window shown", async () => {
      useFixedClock();
      const user = setupUser();
      const get = mockOrgReads();
      renderOrg(orgUrl(""), lang);
      await screen.findByRole("grid", { name: text.grid }, LOAD);

      expect(aggregateCalls(get)[0][1]).not.toHaveProperty("params");
      const trigger = screen.getByRole("button", { name: text.period });
      expect(trigger).toHaveTextContent(text.options[0]);
      expect(screen.getByText(text.help)).toBeInTheDocument();
      expect(screen.getByText(text.defaultWindow)).toBeInTheDocument();
      expect(await optionNames(user, trigger)).toEqual(text.options);
    });

    it("sends since for 13, 26 and 52 closed weeks (Saturday, Tehran), no until, and none for 4", async () => {
      useFixedClock();
      const user = setupUser();
      const get = mockOrgReads();
      snapWhenAsked(get);
      renderOrg(orgUrl(""), lang);
      await screen.findByRole("grid", { name: text.grid }, LOAD);
      const trigger = screen.getByRole("button", { name: text.period });

      const expected: [string, string][] = [
        [text.options[1], "2026-07-24T20:30:00.000Z"],
        [text.options[2], "2026-04-24T20:30:00.000Z"],
        [text.options[3], "2025-10-24T20:30:00.000Z"],
      ];
      for (const [index, [option, since]] of expected.entries()) {
        await chooseOption(user, trigger, option);
        await waitFor(() => expect(aggregateCalls(get).length).toBe(index + 2), LOAD);
        expect(aggregateCalls(get)[index + 1][1]).toMatchObject({ params: { since } });
        expect((aggregateCalls(get)[index + 1][1] as { params: object }).params).not.toHaveProperty("until");
        expect(await screen.findByText(text.snappedWindow, undefined, LOAD)).toBeInTheDocument();
      }

      await chooseOption(user, trigger, text.options[0]);
      // The default window comes back only from an answer to a read without params (fresh or cached).
      expect(await screen.findByText(text.defaultWindow, undefined, LOAD)).toBeInTheDocument();
      expect(screen.queryByText(text.snappedWindow)).not.toBeInTheDocument();
      for (const [, options] of aggregateCalls(get).slice(4)) expect(options).not.toHaveProperty("params");
    });

    it("shows the window the server used, not the one asked for", async () => {
      useFixedClock();
      const user = setupUser();
      snapWhenAsked(mockOrgReads());
      renderOrg(orgUrl(""), lang);
      await screen.findByRole("grid", { name: text.grid }, LOAD);
      expect(screen.getByText(text.defaultWindow)).toBeInTheDocument();
      expect(screen.queryByText(text.snappedWindow)).not.toBeInTheDocument();

      // 13 weeks asks from Jul 25; the server answers with its own window.
      await chooseOption(user, screen.getByRole("button", { name: text.period }), text.options[1]);
      expect(await screen.findByText(text.snappedWindow, undefined, LOAD)).toBeInTheDocument();
      expect(screen.queryByText(text.defaultWindow)).not.toBeInTheDocument();
      expect(screen.queryByText(new RegExp(text.askedFrom))).not.toBeInTheDocument();
    });

    it("keeps the empty state, with the window line", async () => {
      mockOrgReads({ aggregates: [] });
      renderOrg(orgUrl(""), lang);
      expect(await screen.findByText(text.noData, undefined, LOAD)).toBeInTheDocument();
      expect(screen.getByText(text.defaultWindow)).toBeInTheDocument();
      expect(screen.queryByText(text.leaveOutOne)).not.toBeInTheDocument();
    });

    it("keeps the error state: an alert, no window line, and Try again reads again", async () => {
      let fail = true;
      const reads = mockOrgReads();
      const original = reads.getMockImplementation();
      reads.mockImplementation((url: string, ...rest: unknown[]) => {
        // A 4xx: a 5xx would get the generic "not available" text instead of the fallback.
        if (url === AGGREGATES && fail) return Promise.reject(apiError(400, "aggregates_read_failed"));
        return original!(url, ...(rest as []));
      });
      renderOrg(orgUrl(""), lang);
      expect(await screen.findByRole("alert", undefined, LOAD)).toHaveTextContent(text.totalsFailed);
      expect(screen.queryByText(text.defaultWindow)).not.toBeInTheDocument();
      expect(screen.getByText(text.help)).toBeInTheDocument();
      fail = false;
      (await screen.findByRole("button", { name: text.retry })).click();
      expect(await screen.findByRole("grid", { name: text.grid }, LOAD)).toBeInTheDocument();
      expect(screen.getByText(text.defaultWindow)).toBeInTheDocument();
      expect(vi.mocked(api.get)).toHaveBeenCalled();
    });
  });

  it("shows the name, the role and the full released table", async () => {
    mockOrgReads({ aggregates: TWO });
    renderOrg(orgUrl(""));
    expect(await screen.findByRole("heading", { level: 1, name: "Acme Group" }, LOAD)).toHaveAttribute("dir", "auto");
    expect(screen.getByText("Owner")).toBeInTheDocument();
    await screen.findByRole("grid", { name: "Workspace totals" }, LOAD);
    expect(tableRows("Workspace totals")).toEqual([
      ["Tehran office", "30", "25", "2", formatAudioHours(1165, "en"), "27"],
      ["Shiraz", "5", "0", "0", formatAudioHours(3600, "en"), "3"],
    ]);
    expect(screen.getByRole("img", { name: /Meetings per workspace/ }).getAttribute("aria-label")).toContain("Shiraz: 5");
  });

  it("says when nothing is shared with a restricted role", async () => {
    mockOrgReads({ detail: organization({ role: "analyst", unrestricted: false }), aggregates: [] });
    renderOrg(orgUrl(""));
    expect(
      await screen.findByText("No workspace is shared with you yet. An organization administrator can grant you one.", undefined, LOAD),
    ).toBeInTheDocument();
    expect(screen.queryByText("No workspace data for this period.")).not.toBeInTheDocument();
  });

  it("renders right to left in Persian with Persian copy and digits", async () => {
    mockOrgReads();
    renderOrg(orgUrl(""), "fa");
    expect(await screen.findByRole("heading", { level: 1, name: "Acme Group" }, LOAD)).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
    expect(await screen.findByRole("grid", { name: "جمع هر فضای کاری" }, LOAD)).toBeInTheDocument();
    expect(screen.getByText("مالک")).toBeInTheDocument();
    expect(cardValue("جلسه‌ها")).toBe("۳۰");
  });
});
