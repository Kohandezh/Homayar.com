import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router";

import api from "@/lib/api";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { AuthWorkspace, WorkspaceRole } from "@/types/auth";
import type { ReviewItem, ReviewItemListResponse } from "@/types/reviews";
import type { Team, WorkspaceMember } from "@/types/platform";
import ReviewQueuePage from "@/components/reviews/ReviewQueuePage";

const T = "2026-11-10T12:00:00Z";
const LOAD = { timeout: 5000 };

function item(overrides: Partial<ReviewItem> = {}): ReviewItem {
  return {
    id: "rev-1",
    meeting_id: "meet-1",
    team_id: "team-1",
    scorecard_result_id: null,
    source: "manual",
    source_details: {},
    status: "open",
    assignee_user_id: "user-2",
    assigned_by_user_id: null,
    assigned_at: null,
    due_at: "2026-11-12T09:00:00Z",
    is_overdue: true,
    created_by_user_id: null,
    created_at: T,
    updated_at: T,
    started_at: null,
    completed_at: null,
    cancelled_at: null,
    cancel_reason: null,
    revision: 1,
    ...overrides,
  };
}

const MEMBER: WorkspaceMember = {
  id: "mem-2",
  user_id: "user-2",
  email: "ali@example.com",
  phone_e164: null,
  display_name: "Ali",
  role: "member",
  joined_at: T,
};

const TEAM: Team = {
  id: "team-1",
  workspace_id: "ws-1",
  name: "Sales",
  slug: "sales",
  is_active: true,
  created_at: T,
  updated_at: T,
};

interface Reads {
  pages?: ReviewItemListResponse[];
  members?: WorkspaceMember[];
  teams?: Team[];
}

/**
 * Serves the queue list (one entry per cursor, the first without one) and
 * the name lists; anything else is a 404.
 */
function mockReads(reads: Reads = {}) {
  const pages = reads.pages ?? [{ items: [item()], next_cursor: null }];
  const members = reads.members ?? [MEMBER];
  const teams = reads.teams ?? [TEAM];
  const get = vi.spyOn(api, "get").mockImplementation(async (url: string, config?: unknown) => {
    if (url === "/review-items") {
      const cursor = (config as { params?: { cursor?: string } } | undefined)?.params?.cursor ?? null;
      const index = cursor === null ? 0 : pages.findIndex((page) => page.next_cursor === cursor) + 1;
      return okResponse(pages[Math.min(index, pages.length - 1)]);
    }
    if (url === "/members") return okResponse({ items: members, total: members.length });
    if (url === "/teams") return okResponse({ items: teams, total: teams.length });
    throw apiError(404, "not_found");
  });
  return get;
}

function workspace(role: WorkspaceRole, permissions?: string[]): AuthWorkspace {
  return { ...FIXTURE_WORKSPACE, role, ...(permissions ? { permissions } : {}) };
}

function UrlProbe() {
  const location = useLocation();
  return <output data-testid="url">{`${location.pathname}${location.search}`}</output>;
}

function renderPage(route = "/reviews", selected: AuthWorkspace = workspace("owner"), lang: "fa" | "en" = "en") {
  window.localStorage.removeItem("homayar_lang");
  return renderConsole(
    <>
      <ReviewQueuePage />
      <UrlProbe />
    </>,
    {
      route,
      lang,
      auth: { status: "authenticated", user: FIXTURE_USER, workspace: selected, workspaces: [selected] },
    },
  );
}

/** The row of one item, found by its item link's href (the same in both languages). */
async function findRow(id: string) {
  const link = await waitFor(() => {
    const found = document.querySelector(`a[href="/reviews/${id}"]`);
    if (!found) throw new Error(`no row for ${id}`);
    return found;
  }, LOAD);
  const row = link.closest("tr");
  if (!row) throw new Error("no row");
  return row;
}

// React Aria backs every select with a hidden native one, in render order.
const STATUS_FILTER = 0;
const ASSIGNEE_FILTER = 1;

function filterSelect(index: number): HTMLSelectElement {
  const select = screen.getAllByTestId("hidden-select-container")[index].querySelector("select");
  if (!select) throw new Error("no filter select");
  return select;
}

describe("ReviewQueuePage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("loads the queue and shows every column with the names from the workspace lists", async () => {
    const get = mockReads();
    renderPage();

    const row = await findRow("rev-1");
    expect(within(row).getByText("View")).toBeInTheDocument();
    expect(within(row).getByText("Overdue")).toBeInTheDocument();
    expect(within(row).getByText("Ali")).toBeInTheDocument();
    expect(within(row).getByText("Sales")).toBeInTheDocument();
    expect(within(row).getByText("Manual")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Open call report" })).toHaveAttribute("href", "/meetings/meet-1");
    expect(within(row).getByRole("link", { name: "Open review item" })).toHaveAttribute("href", "/reviews/rev-1");
    // Due time formatted by the shared helper; no raw ISO string on the page.
    expect(within(row).queryByText("2026-11-12T09:00:00Z")).not.toBeInTheDocument();
    expect(get).toHaveBeenCalledWith(
      "/review-items",
      expect.objectContaining({
        params: expect.objectContaining({ limit: 50, status: undefined, assignee: undefined, overdue: undefined }),
      }),
    );
  });

  it("shows Unassigned and No team, and the raw id only when no name is known", async () => {
    mockReads({
      members: [],
      teams: [],
      pages: [
        {
          items: [item({ id: "rev-2", assignee_user_id: null, team_id: null, is_overdue: false })],
          next_cursor: null,
        },
      ],
    });
    renderPage();

    const row = await findRow("rev-2");
    expect(within(row).getByText("Unassigned")).toBeInTheDocument();
    expect(within(row).getByText("No team")).toBeInTheDocument();
    expect(within(row).queryByText("Overdue")).not.toBeInTheDocument();
  });

  it("turns the filters in the URL into query parameters", async () => {
    const get = mockReads();
    renderPage("/reviews?status=completed&assignee=me&overdue=true");

    await findRow("rev-1");
    expect(get).toHaveBeenCalledWith(
      "/review-items",
      expect.objectContaining({
        params: expect.objectContaining({ status: "completed", assignee: "me", overdue: "true", limit: 50 }),
      }),
    );
  });

  it("puts a chosen filter into the URL and into the next request", async () => {
    const get = mockReads();
    renderPage();

    await findRow("rev-1");
    fireEvent.change(filterSelect(STATUS_FILTER), { target: { value: "completed" } });
    await waitFor(() => expect(screen.getByTestId("url")).toHaveTextContent("status=completed"), LOAD);
    await waitFor(
      () =>
        expect(get).toHaveBeenCalledWith(
          "/review-items",
          expect.objectContaining({ params: expect.objectContaining({ status: "completed" }) }),
        ),
      LOAD,
    );

    fireEvent.change(filterSelect(ASSIGNEE_FILTER), { target: { value: "unassigned" } });
    await waitFor(() => expect(screen.getByTestId("url")).toHaveTextContent("assignee=unassigned"), LOAD);

    const user = userEvent.setup();
    await user.click(screen.getByRole("switch", { name: "Overdue only" }));
    await waitFor(() => expect(screen.getByTestId("url")).toHaveTextContent("overdue=true"), LOAD);
    await waitFor(
      () =>
        expect(get).toHaveBeenCalledWith(
          "/review-items",
          expect.objectContaining({
            params: expect.objectContaining({ status: "completed", assignee: "unassigned", overdue: "true" }),
          }),
        ),
      LOAD,
    );

    // Active sends no status at all.
    fireEvent.change(filterSelect(STATUS_FILTER), { target: { value: "active" } });
    await waitFor(() => expect(screen.getByTestId("url")).not.toHaveTextContent("status="), LOAD);
    await waitFor(
      () =>
        expect(get).toHaveBeenCalledWith(
          "/review-items",
          expect.objectContaining({ params: expect.objectContaining({ status: undefined }) }),
        ),
      LOAD,
    );
  });

  it("ignores a filter value the API does not know", async () => {
    const get = mockReads();
    renderPage("/reviews?status=archived&assignee=nobody");

    await findRow("rev-1");
    expect(get).toHaveBeenCalledWith(
      "/review-items",
      expect.objectContaining({
        params: expect.objectContaining({ status: undefined, assignee: undefined, overdue: undefined }),
      }),
    );
  });

  it("appends the next page through Load more, which sends the cursor", async () => {
    const get = mockReads({
      pages: [
        { items: [item()], next_cursor: "cursor-2" },
        { items: [item({ id: "rev-2", assignee_user_id: null, is_overdue: false })], next_cursor: null },
      ],
    });
    renderPage();

    await findRow("rev-1");
    const more = screen.getByRole("button", { name: "Load more" });
    expect(more).toBeInTheDocument();
    fireEvent.click(more);

    await waitFor(
      () =>
        expect(get).toHaveBeenCalledWith(
          "/review-items",
          expect.objectContaining({ params: expect.objectContaining({ cursor: "cursor-2" }) }),
        ),
      LOAD,
    );
    await waitFor(() => expect(document.querySelectorAll('a[href^="/reviews/"]')).toHaveLength(2), LOAD);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument(), LOAD);
  });

  it("shows the empty state with the supervision line", async () => {
    mockReads({ pages: [{ items: [], next_cursor: null }] });
    renderPage();

    expect(await screen.findByText("No review items match these filters.", undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByText("Items appear only for the teams you supervise.")).toBeInTheDocument();
  });

  it("describes a failed list read with a retry", async () => {
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/members" || url === "/teams") return okResponse({ items: [], total: 0 });
      throw apiError(400, "review_list_failed");
    });
    renderPage();

    const alert = await screen.findByRole("alert", undefined, LOAD);
    expect(alert).toHaveTextContent("The review items could not be loaded.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("mirrors the page in Persian", async () => {
    mockReads();
    renderPage("/reviews", workspace("owner"), "fa");

    expect(await screen.findByRole("heading", { level: 1, name: "صف بازبینی" }, LOAD)).toBeInTheDocument();
    const row = await findRow("rev-1");
    expect(within(row).getByText("Ali")).toBeInTheDocument();
    expect(within(row).getByText("Sales")).toBeInTheDocument();
    expect(within(row).getByText("از موعد گذشته")).toBeInTheDocument();
    expect(within(row).getByText("دستی")).toBeInTheDocument();
    expect(within(row).getByText("باز")).toBeInTheDocument();
  });
});
