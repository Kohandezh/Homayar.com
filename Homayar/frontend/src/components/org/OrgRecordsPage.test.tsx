import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";

import { apiError } from "@/test/fixtures";
import {
  LOAD,
  mockOrgReads,
  orgUrl,
  recordsResponse,
  renderOrg,
  scopedRecord,
  workspaceRow,
} from "@/components/org/org-test-kit";

function rowOf(name: string) {
  const row = screen.getByRole("rowheader", { name: new RegExp(name) }).closest("tr");
  if (!row) throw new Error(`no row for ${name}`);
  return row;
}

describe("organization records page", { timeout: 60_000 }, () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists the records with workspace, title, status, language, duration and created date", async () => {
    mockOrgReads({
      workspaces: [workspaceRow(), workspaceRow({ id: "ws-2", name: "Shiraz office" })],
      records: recordsResponse([
        scopedRecord(),
        scopedRecord({
          meeting_id: "mt-2",
          workspace_id: "ws-2",
          title: "Sales review",
          status: "processing",
          language: "en",
          duration_seconds: 61,
        }),
      ]),
    });
    renderOrg(orgUrl("/records"));
    expect(await screen.findByRole("grid", { name: "Organization records" }, LOAD)).toBeInTheDocument();
    const sync = rowOf("Tehran office");
    expect(within(sync).getByText("Weekly sync")).toBeInTheDocument();
    expect(within(sync).getByText("Completed")).toBeInTheDocument();
    expect(within(sync).getByText("Persian")).toBeInTheDocument();
    expect(within(sync).getByText("2:05")).toBeInTheDocument();
    const sales = rowOf("Shiraz office");
    expect(within(sales).getByText("Sales review")).toBeInTheDocument();
    expect(within(sales).getByText("English")).toBeInTheDocument();
    // No link into a meeting: an organization grant gives no content access.
    expect(document.querySelectorAll('a[href*="/meetings/"]')).toHaveLength(0);
    expect(document.documentElement).toHaveAttribute("dir", "ltr");
  });

  it("shows the total and says when the list is cut", async () => {
    mockOrgReads({ records: recordsResponse([scopedRecord()], { total: 500, truncated: true }) });
    renderOrg(orgUrl("/records"));
    expect(await screen.findByRole("grid", { name: "Organization records" }, LOAD)).toBeInTheDocument();
    expect(screen.getByText("Total: 500 records")).toBeInTheDocument();
    expect(screen.getByText("The list is cut: only part of the records is shown.")).toBeInTheDocument();
  });

  it("shows the total without the cut note when nothing was truncated", async () => {
    mockOrgReads({ records: recordsResponse([scopedRecord()], { total: 1, truncated: false }) });
    renderOrg(orgUrl("/records"));
    expect(await screen.findByText("Total: 1 record", undefined, LOAD)).toBeInTheDocument();
    expect(screen.queryByText(/only part of the records/i)).not.toBeInTheDocument();
  });

  it("falls back to the workspace id when the name is unknown", async () => {
    mockOrgReads({
      workspaces: [],
      records: recordsResponse([scopedRecord()]),
    });
    renderOrg(orgUrl("/records"));
    expect(await screen.findByRole("grid", { name: "Organization records" }, LOAD)).toBeInTheDocument();
    expect(rowOf("ws-1")).toBeInTheDocument();
  });

  it("says when nothing is shared yet", async () => {
    mockOrgReads({ records: recordsResponse([]) });
    renderOrg(orgUrl("/records"));
    expect(await screen.findByText("No records are shared with you yet.", undefined, LOAD)).toBeInTheDocument();
  });

  it("shows the read error with a retry", async () => {
    mockOrgReads({ records: apiError(500, "server_error") });
    renderOrg(orgUrl("/records"));
    expect(await screen.findByRole("alert", undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("is right to left in Persian with Persian headings", async () => {
    mockOrgReads({
      records: recordsResponse([scopedRecord()], { total: 1, truncated: true }),
    });
    renderOrg(orgUrl("/records"), "fa");
    expect(await screen.findByRole("heading", { level: 1, name: "سوابق" }, LOAD)).toBeInTheDocument();
    expect(await screen.findByRole("grid", { name: "سوابق سازمان" }, LOAD)).toBeInTheDocument();
    expect(screen.getByText(/فهرست کامل نیست/)).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
  });
});
