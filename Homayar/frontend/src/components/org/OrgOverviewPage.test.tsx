import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";

import api from "@/lib/api";
import { formatAudioHours } from "@/lib/format";
import { apiError } from "@/test/fixtures";
import {
  LOAD,
  ORG_ID,
  aggregate,
  chooseOption,
  mockOrgReads,
  orgUrl,
  organization,
  renderOrg,
  setupUser,
} from "@/components/org/org-test-kit";

const AGGREGATES = `/organizations/${ORG_ID}/aggregates`;
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

function cardValue(label: string) {
  const term = screen.getAllByText(label).find((element) => element.tagName === "DT");
  if (!term) throw new Error(`no card ${label}`);
  return term.parentElement?.querySelector("dd")?.textContent;
}

function aggregateCalls(get: ReturnType<typeof mockOrgReads>) {
  return get.mock.calls.filter(([url]) => url === AGGREGATES);
}

describe("organization overview", { timeout: 60_000 }, () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the name, the role, the totals, the chart and the table", async () => {
    const get = mockOrgReads({ aggregates: TWO });
    renderOrg(orgUrl(""));
    expect(await screen.findByRole("heading", { level: 1, name: "Acme Group" }, LOAD)).toHaveAttribute("dir", "auto");
    expect(screen.getByText("Owner")).toBeInTheDocument();
    await screen.findByRole("grid", { name: "Workspace totals" }, LOAD);

    expect(cardValue("Workspaces")).toBe("2");
    expect(cardValue("Meetings")).toBe("35");
    expect(cardValue("Audio")).toBe(formatAudioHours(4765, "en"));
    expect(cardValue("Action items")).toBe("30");

    const chart = screen.getByRole("img", { name: /Meetings per workspace/ });
    expect(chart.getAttribute("aria-label")).toContain("Tehran office: 30");
    expect(chart.getAttribute("aria-label")).toContain("Shiraz: 5");
    expect(within(chart).getByText("30")).toBeInTheDocument();
    expect(within(chart).getByText("Shiraz")).toBeInTheDocument();

    const rows = within(screen.getByRole("grid", { name: "Workspace totals" })).getAllByRole("row").slice(1);
    expect(rows.map((row) => Array.from(row.querySelectorAll("th, td")).map((cell) => cell.textContent))).toEqual([
      ["Tehran office", "30", "25", "2", formatAudioHours(1165, "en"), "27"],
      ["Shiraz", "5", "0", "0", formatAudioHours(3600, "en"), "3"],
    ]);
    // All time sends no since and no until.
    expect(aggregateCalls(get)[0][1]).not.toHaveProperty("params");
  });

  it("sends since for a chosen period, and no until", async () => {
    const user = setupUser();
    const get = mockOrgReads();
    renderOrg(orgUrl(""));
    await screen.findByRole("grid", { name: "Workspace totals" }, LOAD);
    // `since` is "now minus 30 days" at the moment of the choice, which lies
    // between these two clock reads, however slow the machine is.
    const before = Date.now();
    await chooseOption(user, screen.getByRole("button", { name: /Period/ }), "Last 30 days");
    await waitFor(() => expect(aggregateCalls(get).length).toBe(2), LOAD);
    const after = Date.now();
    const params = (aggregateCalls(get)[1][1] as { params: { since: string; until?: string } }).params;
    expect(params.until).toBeUndefined();
    const since = Date.parse(params.since);
    expect(since).toBeGreaterThanOrEqual(before - 30 * 86_400_000);
    expect(since).toBeLessThanOrEqual(after - 30 * 86_400_000);
    expect(params.since).toBe(new Date(since).toISOString());
  });

  it("says when the period has no data, and when nothing is shared with a restricted role", async () => {
    mockOrgReads({ aggregates: [] });
    const open = renderOrg(orgUrl(""));
    expect(await screen.findByText("No workspace data for this period.", undefined, LOAD)).toBeInTheDocument();
    open.unmount();
    vi.restoreAllMocks();

    mockOrgReads({ detail: organization({ role: "analyst", unrestricted: false }), aggregates: [] });
    renderOrg(orgUrl(""));
    expect(
      await screen.findByText("No workspace is shared with you yet. An organization administrator can grant you one.", undefined, LOAD),
    ).toBeInTheDocument();
    expect(screen.queryByText("No workspace data for this period.")).not.toBeInTheDocument();
  });

  it("describes a failed totals read and reads again on retry", async () => {
    let fail = true;
    const reads = mockOrgReads();
    const original = reads.getMockImplementation();
    reads.mockImplementation((url: string, ...rest: unknown[]) => {
      // A 4xx: a 5xx would get the generic "not available" text instead of the fallback.
      if (url === AGGREGATES && fail) return Promise.reject(apiError(400, "aggregates_read_failed"));
      return original!(url, ...(rest as []));
    });
    renderOrg(orgUrl(""));
    expect(await screen.findByRole("alert", undefined, LOAD)).toHaveTextContent("The totals could not be loaded.");
    fail = false;
    (await screen.findByRole("button", { name: "Try again" })).click();
    expect(await screen.findByRole("grid", { name: "Workspace totals" }, LOAD)).toBeInTheDocument();
    expect(vi.mocked(api.get)).toHaveBeenCalled();
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
