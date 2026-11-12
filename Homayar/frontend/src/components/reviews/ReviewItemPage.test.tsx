import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";

import api from "@/lib/api";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { AuthWorkspace, WorkspaceRole } from "@/types/auth";
import type { ReviewCriteriaResponse, ReviewItem } from "@/types/reviews";
import type { Team, WorkspaceMember } from "@/types/platform";
import ReviewItemPage from "@/components/reviews/ReviewItemPage";
import { toLocalDateTimeInput } from "@/components/reviews/review-model";

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
    assignee_user_id: "user-1",
    assigned_by_user_id: null,
    assigned_at: null,
    due_at: "2026-11-12T09:00:00Z",
    is_overdue: false,
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

function criteria(overrides: Partial<ReviewCriteriaResponse> = {}): ReviewCriteriaResponse {
  return {
    review_item_id: "rev-1",
    revision: 1,
    status: "open",
    scorecard_result_id: null,
    machine: null,
    human: null,
    criteria: [],
    ...overrides,
  };
}

/** A criteria view with one reviewable criterion, for the decision flows. */
function criteriaWithMachine(revision: number): ReviewCriteriaResponse {
  return criteria({
    revision,
    status: "in_review",
    scorecard_result_id: "result-1",
    machine: {
      outcome: "failed",
      reason: null,
      complete: true,
      score: 70,
      score_min: 0,
      score_max: 100,
      pass_threshold: 80,
      applicable_weight: 100,
      earned_weight: 70,
      failed_weight: 30,
      insufficient_weight: 0,
    },
    human: {
      outcome: "failed",
      reason: null,
      complete: true,
      score: 60,
      score_min: 0,
      score_max: 100,
      pass_threshold: 80,
      applicable_weight: 100,
      earned_weight: 60,
      failed_weight: 40,
      insufficient_weight: 0,
      final: false,
    },
    criteria: [
      {
        criterion_id: "greeting",
        position: 1,
        weight: 100,
        auto_fail: false,
        na_allowed: false,
        reviewable: true,
        machine: { outcome: "passed", reason: null, decided_by: null, review_required: false, review_reasons: [], evidence: [1] },
        decision: null,
      },
    ],
  });
}

const MEMBERS: WorkspaceMember[] = [
  { id: "mem-1", user_id: "user-1", email: "person@example.com", phone_e164: null, display_name: "Person", role: "owner", joined_at: T },
  { id: "mem-2", user_id: "user-2", email: "ali@example.com", phone_e164: null, display_name: "Ali", role: "member", joined_at: T },
  { id: "mem-3", user_id: "user-3", email: "looker@example.com", phone_e164: null, display_name: "Looker", role: "viewer", joined_at: T },
];

const TEAMS: Team[] = [
  { id: "team-1", workspace_id: "ws-1", name: "Sales", slug: "sales", is_active: true, created_at: T, updated_at: T },
];

interface Reads {
  item?: ReviewItem | Error;
  criteria?: ReviewCriteriaResponse | Error;
}

/**
 * Serves the queue list (one entry per cursor, the first without one) and
 * the name lists; anything else is a 404.
 */
function mockReads(reads: Reads = {}) {
  const theItem = reads.item instanceof Error ? item() : (reads.item ?? item());
  const theCriteria = "criteria" in reads ? reads.criteria : criteria();
  const get = vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url === "/review-items/rev-1") {
      if (reads.item instanceof Error) throw reads.item;
      return okResponse(theItem);
    }
    if (url === "/review-items/rev-1/criteria") {
      if (theCriteria instanceof Error) throw theCriteria;
      return okResponse(theCriteria);
    }
    if (url === "/members") return okResponse({ items: MEMBERS, total: MEMBERS.length });
    if (url === "/teams") return okResponse({ items: TEAMS, total: TEAMS.length });
    throw apiError(404, "not_found");
  });
  return get;
}

function workspace(role: WorkspaceRole, permissions?: string[]): AuthWorkspace {
  return { ...FIXTURE_WORKSPACE, role, ...(permissions ? { permissions } : {}) };
}

function renderPage(selected: AuthWorkspace = workspace("owner"), lang: "fa" | "en" = "en") {
  window.localStorage.removeItem("homayar_lang");
  return renderConsole(
    <Routes>
      <Route path="/reviews/:itemId" element={<ReviewItemPage />} />
    </Routes>,
    {
      route: "/reviews/rev-1",
      lang,
      auth: { status: "authenticated", user: FIXTURE_USER, workspace: selected, workspaces: [selected] },
    },
  );
}

/** The hidden native select of the assign field (the first on the page). */
function assignSelect(): HTMLSelectElement {
  const select = screen.getAllByTestId("hidden-select-container")[0].querySelector("select");
  if (!select) throw new Error("no assign select");
  return select;
}

function localInput(date: Date): string {
  return toLocalDateTimeInput(date.toISOString());
}

describe("ReviewItemPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("shows the item fields, names and the call report link", async () => {
    mockReads({ item: item({ assignee_user_id: "user-2" }) });
    renderPage();

    expect(await screen.findAllByText("Ali")).not.toHaveLength(0);
    expect(screen.getByText("Sales")).toBeInTheDocument();
    expect(screen.getByText("Manual")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open call report" })).toHaveAttribute("href", "/meetings/meet-1");
    expect(screen.getByText("Open")).toBeInTheDocument();
    expect(screen.queryByText("2026-11-12T09:00:00Z")).not.toBeInTheDocument();
  });

  it("shows the one not-found text with a link back, for 404 and for no access", async () => {
    const get = mockReads({ item: apiError(404, "review_item_not_found") });
    renderPage();

    expect(
      await screen.findByText("This review item does not exist or you cannot see it.", undefined, LOAD),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to review queue" })).toHaveAttribute("href", "/reviews");
    // No criteria read for an item that is not there.
    expect(get.mock.calls.map(([url]) => url)).not.toContain("/review-items/rev-1/criteria");
  });

  it("shows the same text for a 403-shaped refusal that still says not found by code", async () => {
    mockReads({ item: apiError(403, "review_item_not_found") });
    renderPage();
    expect(
      await screen.findByText("This review item does not exist or you cannot see it.", undefined, LOAD),
    ).toBeInTheDocument();
  });

  it.each([
    ["open, assignee is me", item({ status: "open" }), "Start review"],
    ["in_review, assignee is me", item({ status: "in_review" }), "Complete review"],
  ] as const)("shows %s only its own action", async (_label, theItem, action) => {
    mockReads({ item: theItem });
    renderPage();

    await screen.findByRole("link", { name: "Open call report" }, LOAD);
    const other = action === "Start review" ? "Complete review" : "Start review";
    expect(screen.getByRole("button", { name: action })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: other })).not.toBeInTheDocument();
  });

  it("hides start and complete when the current user is not the assignee", async () => {
    mockReads({ item: item({ status: "open", assignee_user_id: "user-2" }) });
    renderPage();

    await screen.findByRole("link", { name: "Open call report" }, LOAD);
    expect(screen.queryByRole("button", { name: "Start review" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Complete review" })).not.toBeInTheDocument();
    // Cancel stays: any manager may cancel an active item.
    expect(screen.getByRole("button", { name: "Cancel item" })).toBeInTheDocument();
  });

  it("renders no write control at all without review:manage", async () => {
    mockReads({ item: item({ assignee_user_id: "user-2" }) });
    renderPage(workspace("member", ["review:read"]));

    await screen.findByRole("link", { name: "Open call report" }, LOAD);
    for (const name of ["Start review", "Complete review", "Cancel item", "Assign", "Save due time"]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
    expect(screen.queryByTestId("hidden-select-container")).not.toBeInTheDocument();
  });

  it("keeps terminal items read only and shows the completion time", async () => {
    mockReads({ item: item({ status: "completed", completed_at: "2026-11-10T15:00:00Z" }) });
    renderPage();

    await screen.findByText("Completed", {}, LOAD);
    expect(screen.queryByRole("button", { name: "Cancel item" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start review" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Review completed.");
  });

  it("shows the cancel reason of a cancelled item", async () => {
    mockReads({ item: item({ status: "cancelled", cancel_reason: "duplicate" }) });
    renderPage();

    expect(await screen.findByText("Cancelled: Duplicate", {}, LOAD)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel item" })).not.toBeInTheDocument();
  });

  it("assigns the chosen member with the revision read", async () => {
    const post = vi
      .spyOn(api, "post")
      .mockResolvedValue(okResponse(item({ assignee_user_id: "user-2", revision: 2 })));
    mockReads();
    renderPage();

    await screen.findByRole("link", { name: "Open call report" }, LOAD);
    fireEvent.change(assignSelect(), { target: { value: "user-2" } });
    fireEvent.click(screen.getByRole("button", { name: "Assign" }));

    await waitFor(
      () => expect(post).toHaveBeenCalledWith("/review-items/rev-1/assign", {
        expected_revision: 1,
        assignee_user_id: "user-2",
      }),
      LOAD,
    );
  }, 10_000);

  it("sends null when Unassigned is chosen, not the sentinel string", async () => {
    const post = vi
      .spyOn(api, "post")
      .mockResolvedValue(okResponse(item({ assignee_user_id: null, revision: 2 })));
    mockReads({ item: item({ assignee_user_id: "user-2" }) });
    renderPage();

    await screen.findByRole("link", { name: "Open call report" }, LOAD);
    fireEvent.change(assignSelect(), { target: { value: "unassigned" } });
    fireEvent.click(screen.getByRole("button", { name: "Assign" }));

    await waitFor(
      () => expect(post).toHaveBeenCalledWith("/review-items/rev-1/assign", {
        expected_revision: 1,
        assignee_user_id: null,
      }),
      LOAD,
    );
  }, 10_000);

  it("offers no viewer in the assign select", async () => {
    mockReads();
    renderPage();

    await screen.findByRole("link", { name: "Open call report" }, LOAD);
    const options = Array.from(assignSelect().options).map((option) => option.value);
    expect(options).toContain("unassigned");
    expect(options).toContain("user-1");
    expect(options).toContain("user-2");
    expect(options).not.toContain("user-3");
  });

  it("refuses a past due time client-side, with no request", async () => {
    const post = vi.spyOn(api, "post").mockImplementation(async () => {
      throw new Error("no write may happen");
    });
    mockReads();
    renderPage();

    const field = await screen.findByLabelText("Due time", {}, LOAD);
    fireEvent.change(field, { target: { value: localInput(new Date(Date.now() - 86_400_000)) } });
    fireEvent.click(screen.getByRole("button", { name: "Save due time" }));

    expect(await screen.findByText("The due time must be later than now and at most 90 days ahead.", {}, LOAD)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("refuses a due time beyond 90 days client-side, with no request", async () => {
    const post = vi.spyOn(api, "post").mockImplementation(async () => {
      throw new Error("no write may happen");
    });
    mockReads();
    renderPage();

    const field = await screen.findByLabelText("Due time", {}, LOAD);
    fireEvent.change(field, { target: { value: localInput(new Date(Date.now() + 91 * 86_400_000)) } });
    fireEvent.click(screen.getByRole("button", { name: "Save due time" }));

    expect(await screen.findByText(/at most 90 days ahead/, {}, LOAD)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("sends a valid due time as an ISO string with a UTC offset", async () => {
    const post = vi
      .spyOn(api, "post")
      .mockResolvedValue(okResponse(item({ due_at: "2026-11-13T09:00:00Z", revision: 2 })));
    mockReads();
    renderPage();

    const field = await screen.findByLabelText("Due time", {}, LOAD);
    const next = new Date(Date.now() + 86_400_000);
    fireEvent.change(field, { target: { value: localInput(next) } });
    fireEvent.click(screen.getByRole("button", { name: "Save due time" }));

    await waitFor(
      () =>
        expect(post).toHaveBeenCalledWith("/review-items/rev-1/due-time", {
          expected_revision: 1,
          due_at: expect.stringMatching(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3})?Z$/),
        }),
      LOAD,
    );
    // The server 422 also maps to the same sentence.
    post.mockRejectedValue(apiError(422, "review_due_at_invalid"));
    fireEvent.click(screen.getByRole("button", { name: "Save due time" }));
    expect(await screen.findByText(/at most 90 days ahead/, undefined, LOAD)).toBeInTheDocument();
  });

  it("shows the conflict text and reloads on a revision conflict, without retrying", async () => {
    const post = vi
      .spyOn(api, "post")
      .mockRejectedValue(apiError(409, "review_item_revision_conflict", { details: { revision: 2 } }));
    const get = mockReads();
    renderPage();

    await screen.findByRole("link", { name: "Open call report" }, LOAD);
    fireEvent.click(screen.getByRole("button", { name: "Start review" }));

    expect(await screen.findByText("Someone changed this item. The latest version is loaded.", undefined, LOAD)).toBeInTheDocument();
    await waitFor(
      () => {
        const urls = get.mock.calls.map(([url]) => url);
        const itemReads = urls.filter((url) => url === "/review-items/rev-1").length;
        const criteriaReads = urls.filter((url) => url === "/review-items/rev-1/criteria").length;
        expect(itemReads).toBeGreaterThanOrEqual(2);
        expect(criteriaReads).toBeGreaterThanOrEqual(2);
      },
      LOAD,
    );
    // Never retried by the console.
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("sends nothing when the cancel dialog is cancelled", async () => {
    const post = vi.spyOn(api, "post").mockImplementation(async () => {
      throw new Error("no write may happen");
    });
    mockReads();
    renderPage();

    await screen.findByRole("link", { name: "Open call report" }, LOAD);
    fireEvent.click(screen.getByRole("button", { name: "Cancel item" }));
    const dialog = await screen.findByRole("alertdialog", undefined, LOAD);
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(), LOAD);
    expect(post).not.toHaveBeenCalled();
  });

  it("cancels with a chosen reason, and the confirm needs one first", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse(item({ status: "cancelled", cancel_reason: "duplicate", revision: 2 })));
    mockReads();
    renderPage();

    await screen.findByRole("link", { name: "Open call report" }, LOAD);
    fireEvent.click(screen.getByRole("button", { name: "Cancel item" }));
    const dialog = await screen.findByRole("alertdialog", undefined, LOAD);
    const reasonSelect = dialog.querySelector("select");
    if (!reasonSelect) throw new Error("no reason select");

    // The dialog starts with no reason, so the confirm is off and sends nothing.
    expect(reasonSelect.value).toBe("");
    const confirm = within(dialog).getByRole("button", { name: "Cancel item" });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(post).not.toHaveBeenCalled();

    // A choice does not carry over: closing and opening again starts with none.
    fireEvent.change(reasonSelect, { target: { value: "out_of_scope" } });
    expect(confirm).toBeEnabled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(), LOAD);
    fireEvent.click(screen.getByRole("button", { name: "Cancel item" }));
    const reopened = await screen.findByRole("alertdialog", undefined, LOAD);
    const reopenedSelect = reopened.querySelector("select");
    if (!reopenedSelect) throw new Error("no reason select");
    expect(reopenedSelect.value).toBe("");
    const reopenedConfirm = within(reopened).getByRole("button", { name: "Cancel item" });
    expect(reopenedConfirm).toBeDisabled();
    expect(post).not.toHaveBeenCalled();

    fireEvent.change(reopenedSelect, { target: { value: "duplicate" } });
    expect(reopenedConfirm).toBeEnabled();
    fireEvent.click(reopenedConfirm);

    await waitFor(
      () => expect(post).toHaveBeenCalledWith("/review-items/rev-1/cancel", {
        expected_revision: 1,
        reason: "duplicate",
      }),
      LOAD,
    );
  }, 10_000);

  it("completes, offers the next own item and navigates to it", async () => {
    const post = vi
      .spyOn(api, "post")
      .mockResolvedValue(okResponse(item({ status: "completed", completed_at: T, revision: 2 })));
    const get = mockReads({ item: item({ status: "in_review" }) });
    get.mockImplementation(async (url: string, config?: unknown) => {
      if (url === "/review-items/rev-1") return okResponse(item({ status: "in_review" }));
      if (url === "/review-items/rev-1/criteria") return okResponse(criteria());
      if (url === "/members") return okResponse({ items: MEMBERS, total: MEMBERS.length });
      if (url === "/teams") return okResponse({ items: TEAMS, total: TEAMS.length });
      if (url === "/review-items") {
        // The next-item read: active items of the current user.
        expect((config as { params?: Record<string, unknown> } | undefined)?.params).toMatchObject({ assignee: "me" });
        return okResponse({
          items: [item({ id: "rev-9", status: "in_review" }), item({ id: "rev-2", status: "in_review" })],
          next_cursor: null,
        });
      }
      throw apiError(404, "not_found");
    });
    renderPage();

    await screen.findByRole("link", { name: "Open call report" }, LOAD);
    fireEvent.click(screen.getByRole("button", { name: "Complete review" }));

    expect(await screen.findByText("Review completed.", undefined, LOAD)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next item" }));
    await waitFor(
      () => expect(get).toHaveBeenCalledWith("/review-items", expect.objectContaining({
        params: expect.objectContaining({ assignee: "me" }),
      })),
      LOAD,
    );
    expect(post).toHaveBeenCalledWith("/review-items/rev-1/complete", { expected_revision: 1 });
  }, 10_000);

  it("says the queue is empty when no other own item exists", async () => {
    vi.spyOn(api, "post").mockResolvedValue(okResponse(item({ status: "completed", completed_at: T, revision: 2 })));
    const get = mockReads({ item: item({ status: "in_review" }) });
    get.mockImplementation(async (url: string) => {
      if (url === "/review-items/rev-1") return okResponse(item({ status: "in_review" }));
      if (url === "/review-items/rev-1/criteria") return okResponse(criteria());
      if (url === "/members") return okResponse({ items: MEMBERS, total: MEMBERS.length });
      if (url === "/teams") return okResponse({ items: TEAMS, total: TEAMS.length });
      if (url === "/review-items") {
        return okResponse({ items: [item({ id: "rev-1", status: "in_review" })], next_cursor: null });
      }
      throw apiError(404, "not_found");
    });
    renderPage();

    await screen.findByRole("link", { name: "Open call report" }, LOAD);
    fireEvent.click(screen.getByRole("button", { name: "Complete review" }));
    expect(await screen.findByText("Review completed.", undefined, LOAD)).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Next item" }));
    expect(await screen.findByText("Your queue is empty.", undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to review queue" })).toHaveAttribute("href", "/reviews");
  }, 10_000);

  it("moves through two Next item hops, with per-item state reset", async () => {
    // Item 1 is completed; items 2 and 3 are this user's active items. Items
    // 1 and 2 share a revision number but have different due times and calls.
    const rev1 = item({ id: "rev-1", status: "completed", completed_at: T, revision: 3, due_at: "2026-11-12T09:00:00Z", meeting_id: "meet-1" });
    const rev2 = item({ id: "rev-2", status: "in_review", started_at: T, revision: 3, due_at: "2026-11-19T18:30:00Z", meeting_id: "meet-2" });
    const rev3 = item({ id: "rev-3", status: "in_review", started_at: T, revision: 5, due_at: "2026-11-25T08:00:00Z", meeting_id: "meet-3" });
    const byId = new Map([["rev-1", rev1], ["rev-2", rev2], ["rev-3", rev3]]);
    // The next-item read returns the caller's active items only.
    const active = () => okResponse({ items: [rev2, rev3], next_cursor: null });
    const post = vi
      .spyOn(api, "post")
      .mockImplementation(async (url: string) => {
        if (url === "/review-items/rev-2/complete") {
          byId.set("rev-2", { ...rev2, status: "completed", completed_at: T, revision: 4 });
        }
        return okResponse(byId.get("rev-2"));
      });
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      const found = [...byId.entries()].find(([id]) => url === `/review-items/${id}`);
      if (found) return okResponse(found[1]);
      if (url.endsWith("/criteria")) {
        const id = url.split("/")[2];
        return okResponse(criteria({ review_item_id: id, revision: byId.get(id)?.revision ?? 1 }));
      }
      if (url === "/review-items") return active();
      if (url === "/members") return okResponse({ items: MEMBERS, total: MEMBERS.length });
      if (url === "/teams") return okResponse({ items: TEAMS, total: TEAMS.length });
      throw apiError(404, "not_found");
    });
    renderPage();

    // Hop one: the fresh page shows item 2's call, and its due field holds
    // item 2's time, although item 1 had the same revision number.
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Next item" }, LOAD));
    let report = await screen.findByRole("link", { name: "Open call report" }, LOAD);
    await waitFor(() => expect(report).toHaveAttribute("href", "/meetings/meet-2"), LOAD);
    expect(screen.getByLabelText("Due time")).toHaveValue(toLocalDateTimeInput("2026-11-19T18:30:00Z"));

    // The second page is worked to completion, which brings its own Next button.
    await user.click(screen.getByRole("button", { name: "Complete review" }));
    expect(await screen.findByText("Review completed.", undefined, LOAD)).toBeInTheDocument();

    // Hop two: a second press on a fresh page navigates again.
    await user.click(screen.getByRole("button", { name: "Next item" }));
    report = await screen.findByRole("link", { name: "Open call report" }, LOAD);
    await waitFor(() => expect(report).toHaveAttribute("href", "/meetings/meet-3"), LOAD);
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith("/review-items/rev-2/complete", { expected_revision: 3 });
  }, 10_000);

  it("shows the error of a failed next-item lookup, instead of staying silent", async () => {
    vi.spyOn(api, "post").mockResolvedValue(okResponse(item({ status: "completed", completed_at: T, revision: 2 })));
    const get = mockReads({ item: item({ status: "in_review" }) });
    get.mockImplementation(async (url: string) => {
      if (url === "/review-items/rev-1") return okResponse(item({ status: "in_review" }));
      if (url === "/review-items/rev-1/criteria") return okResponse(criteria());
      if (url === "/members") return okResponse({ items: MEMBERS, total: MEMBERS.length });
      if (url === "/teams") return okResponse({ items: TEAMS, total: TEAMS.length });
      if (url === "/review-items") throw apiError(500, "server_error");
      throw apiError(404, "not_found");
    });
    renderPage();

    await screen.findByRole("link", { name: "Open call report" }, LOAD);
    fireEvent.click(screen.getByRole("button", { name: "Complete review" }));
    expect(await screen.findByText("Review completed.", undefined, LOAD)).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Next item" }));
    expect(await screen.findByText("The service is not available right now; try again shortly.", undefined, LOAD)).toBeInTheDocument();
    // The button stays for a retry.
    expect(screen.getByRole("button", { name: "Next item" })).toBeEnabled();
  }, 10_000);

  it("reloads both views on a refused decision and retries with the new revision", async () => {
    // The first decision answers 409; the reload then serves revision 5.
    let servedCriteria = criteriaWithMachine(4);
    const post = vi
      .spyOn(api, "post")
      .mockRejectedValueOnce(apiError(409, "review_item_revision_conflict", { details: { revision: 5 } }))
      .mockResolvedValue(okResponse(criteriaWithMachine(5)));
    const get = mockReads({ item: item({ status: "in_review" }), criteria: servedCriteria });
    get.mockImplementation(async (url: string) => {
      if (url === "/review-items/rev-1") return okResponse(item({ status: "in_review", revision: 4 }));
      if (url === "/review-items/rev-1/criteria") return okResponse(servedCriteria);
      if (url === "/members") return okResponse({ items: MEMBERS, total: MEMBERS.length });
      if (url === "/teams") return okResponse({ items: TEAMS, total: TEAMS.length });
      throw apiError(404, "not_found");
    });
    renderPage();

    const accept = await screen.findByRole("button", { name: "Accept" }, LOAD);
    // From here on the reads serve the reloaded revision.
    servedCriteria = criteriaWithMachine(5);
    fireEvent.click(accept);
    expect(await screen.findByText("Someone changed this item. The latest version is loaded.", undefined, LOAD)).toBeInTheDocument();

    // The refused write reloaded the criteria view and the item.
    await waitFor(
      () => {
        const urls = get.mock.calls.map(([url]) => url);
        expect(urls.filter((url) => url === "/review-items/rev-1/criteria").length).toBeGreaterThanOrEqual(2);
        expect(urls.filter((url) => url === "/review-items/rev-1").length).toBeGreaterThanOrEqual(2);
      },
      LOAD,
    );

    // The repeated decision uses the reloaded revision; the console never retried by itself.
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(
      () => expect(post).toHaveBeenLastCalledWith("/review-items/rev-1/criteria/greeting/decision", {
        expected_revision: 5,
        decision: "accept",
      }),
      LOAD,
    );
    expect(post).toHaveBeenCalledTimes(2);
  }, 10_000);

  it("sends the fresh revision to Complete review after a decision moved it", async () => {
    // The decision answer carries revision 5; the item cache held 4. The
    // complete write must never send the revision from before the decision.
    // The server's item; the decision and the complete write move it on.
    let serverItem = item({ status: "in_review", revision: 4 });
    const post = vi
      .spyOn(api, "post")
      .mockImplementation(async (url: string) => {
        if (url === "/review-items/rev-1/criteria/greeting/decision") {
          serverItem = { ...serverItem, revision: 5 };
          return okResponse(criteriaWithMachine(5));
        }
        serverItem = { ...serverItem, status: "completed", completed_at: T, revision: serverItem.revision + 1 };
        return okResponse(serverItem);
      });
    // The item refetch the decision starts is held until Complete was
    // pressed, so the page cannot learn the new revision from that read.
    let releaseRefetch: () => void = () => {};
    const refetchHeld = new Promise<void>((resolve) => {
      releaseRefetch = resolve;
    });
    const get = mockReads({ item: item({ status: "in_review" }), criteria: criteriaWithMachine(4) });
    get.mockImplementation(async (url: string) => {
      if (url === "/review-items/rev-1") {
        if (serverItem.revision >= 5) await refetchHeld;
        return okResponse(serverItem);
      }
      if (url === "/review-items/rev-1/criteria") return okResponse(criteriaWithMachine(serverItem.revision));
      if (url === "/members") return okResponse({ items: MEMBERS, total: MEMBERS.length });
      if (url === "/teams") return okResponse({ items: TEAMS, total: TEAMS.length });
      throw apiError(404, "not_found");
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Accept" }, LOAD));
    await waitFor(
      () => expect(post).toHaveBeenCalledWith("/review-items/rev-1/criteria/greeting/decision", {
        expected_revision: 4,
        decision: "accept",
      }),
      LOAD,
    );

    // Complete reads the revision the decision wrote, without waiting for
    // the item refetch.
    await waitFor(
      () => expect(get.mock.calls.filter(([url]) => url === "/review-items/rev-1").length).toBeGreaterThanOrEqual(2),
      LOAD,
    );
    fireEvent.click(screen.getByRole("button", { name: "Complete review" }));
    await waitFor(
      () => expect(post).toHaveBeenCalledWith("/review-items/rev-1/complete", { expected_revision: 5 }),
      LOAD,
    );
    expect(post).not.toHaveBeenCalledWith("/review-items/rev-1/complete", { expected_revision: 4 });
    releaseRefetch();
    expect(await screen.findByText("Review completed.", undefined, LOAD)).toBeInTheDocument();
  }, 10_000);
});
