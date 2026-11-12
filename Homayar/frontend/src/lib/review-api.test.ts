import { createElement, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { AxiosHeaders, type AxiosResponse } from "axios";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "@/lib/api";
import {
  REVIEW_PAGE_SIZE,
  assignReviewItem,
  cancelReviewItem,
  changeReviewDueTime,
  completeReviewItem,
  decideReviewCriterion,
  getReviewCriteria,
  getReviewItem,
  listReviewItems,
  nextReviewItem,
  reviewQueryKeys,
  startReviewItem,
  useDecideReviewCriterion,
  useStartReviewItem,
} from "@/lib/review-api";
import { testQueryClient } from "@/test/fixtures";
import type { ReviewCriteriaResponse, ReviewItem, ReviewItemListResponse } from "@/types/reviews";

function axiosResponse<T>(data: T): AxiosResponse<T> {
  return {
    data,
    status: 200,
    statusText: "OK",
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
  };
}

const T = "2026-11-10T12:00:00Z";

function item(overrides: Partial<ReviewItem> = {}): ReviewItem {
  return {
    id: "rev-1",
    meeting_id: "meet-1",
    team_id: "team-1",
    scorecard_result_id: null,
    source: "manual",
    source_details: {},
    status: "open",
    assignee_user_id: null,
    assigned_by_user_id: null,
    assigned_at: null,
    due_at: T,
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
    revision: 2,
    status: "in_review",
    scorecard_result_id: "result-1",
    machine: null,
    human: null,
    criteria: [],
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("review queue endpoints", () => {
  it("maps the router contracts: reads, writes and the decision path", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(axiosResponse({}));
    const post = vi.spyOn(api, "post").mockResolvedValue(axiosResponse({}));

    await getReviewItem("rev-1");
    await getReviewCriteria("rev-1");
    await assignReviewItem("rev-1", { expected_revision: 3, assignee_user_id: null });
    await changeReviewDueTime("rev-1", { expected_revision: 3, due_at: T });
    await startReviewItem("rev-1", 3);
    await completeReviewItem("rev-1", 4);
    await cancelReviewItem("rev-1", { expected_revision: 3, reason: "other" });
    await decideReviewCriterion("rev-1", "crit/greeting", {
      expected_revision: 5,
      decision: "accept",
    });

    expect(get).toHaveBeenNthCalledWith(1, "/review-items/rev-1");
    expect(get).toHaveBeenNthCalledWith(2, "/review-items/rev-1/criteria");
    expect(post).toHaveBeenNthCalledWith(1, "/review-items/rev-1/assign", {
      expected_revision: 3,
      assignee_user_id: null,
    });
    expect(post).toHaveBeenNthCalledWith(2, "/review-items/rev-1/due-time", {
      expected_revision: 3,
      due_at: T,
    });
    expect(post).toHaveBeenNthCalledWith(3, "/review-items/rev-1/start", { expected_revision: 3 });
    expect(post).toHaveBeenNthCalledWith(4, "/review-items/rev-1/complete", { expected_revision: 4 });
    expect(post).toHaveBeenNthCalledWith(5, "/review-items/rev-1/cancel", {
      expected_revision: 3,
      reason: "other",
    });
    // A criterion id is a path segment, so it is encoded.
    expect(post).toHaveBeenNthCalledWith(6, "/review-items/rev-1/criteria/crit%2Fgreeting/decision", {
      expected_revision: 5,
      decision: "accept",
    });
  });

  it("sends only the filters that were chosen; overdue goes as the string true", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(axiosResponse({ items: [], next_cursor: null }));

    await listReviewItems();
    await listReviewItems({ status: "completed" });
    await listReviewItems({ assignee: "me" });
    await listReviewItems({ assignee: "unassigned", overdue: true });
    await listReviewItems({ meeting_id: "meet-1", cursor: "c-given", limit: 25 });

    expect(get).toHaveBeenNthCalledWith(1, "/review-items", {
      params: {
        status: undefined,
        assignee: undefined,
        overdue: undefined,
        meeting_id: undefined,
        limit: undefined,
        cursor: undefined,
      },
    });
    expect(get).toHaveBeenNthCalledWith(2, "/review-items", {
      params: {
        status: "completed",
        assignee: undefined,
        overdue: undefined,
        meeting_id: undefined,
        limit: undefined,
        cursor: undefined,
      },
    });
    expect(get).toHaveBeenNthCalledWith(3, "/review-items", {
      params: {
        status: undefined,
        assignee: "me",
        overdue: undefined,
        meeting_id: undefined,
        limit: undefined,
        cursor: undefined,
      },
    });
    expect(get).toHaveBeenNthCalledWith(4, "/review-items", {
      params: {
        status: undefined,
        assignee: "unassigned",
        overdue: "true",
        meeting_id: undefined,
        limit: undefined,
        cursor: undefined,
      },
    });
    expect(get).toHaveBeenNthCalledWith(5, "/review-items", {
      params: {
        status: undefined,
        assignee: undefined,
        overdue: undefined,
        meeting_id: "meet-1",
        limit: 25,
        cursor: "c-given",
      },
    });
  });

  it("keeps the query keys under one reviews root", () => {
    expect(reviewQueryKeys.lists()).toEqual(["reviews", "list"]);
    expect(reviewQueryKeys.list({ status: "open" })).toEqual(["reviews", "list", { status: "open" }]);
    expect(reviewQueryKeys.item("rev-1")).toEqual(["reviews", "item", "rev-1"]);
    expect(reviewQueryKeys.criteria("rev-1")).toEqual(["reviews", "item", "rev-1", "criteria"]);
  });

  it("finds the next own item, skipping the current one", async () => {
    const page: ReviewItemListResponse = {
      items: [item({ id: "rev-1" }), item({ id: "rev-2", due_at: "2026-11-11T12:00:00Z" })],
      next_cursor: null,
    };
    const get = vi.spyOn(api, "get").mockResolvedValue(axiosResponse(page));

    await expect(nextReviewItem("rev-1")).resolves.toMatchObject({ id: "rev-2" });
    expect(get).toHaveBeenCalledWith("/review-items", {
      params: {
        status: undefined,
        assignee: "me",
        overdue: undefined,
        meeting_id: undefined,
        limit: REVIEW_PAGE_SIZE,
        cursor: undefined,
      },
    });

    const alone: ReviewItemListResponse = { items: [item({ id: "rev-1" })], next_cursor: null };
    get.mockResolvedValue(axiosResponse(alone));
    await expect(nextReviewItem("rev-1")).resolves.toBeNull();
  });
});

describe("review queue hooks", () => {
  function wrapperOf(queryClient: QueryClient) {
    return ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);
  }

  it("an item write puts the answer in the item cache and stales list and criteria", async () => {
    const started = item({ status: "in_review", revision: 2 });
    const post = vi.spyOn(api, "post").mockResolvedValue(axiosResponse(started));
    const get = vi.spyOn(api, "get").mockResolvedValue(axiosResponse({}));
    const queryClient = testQueryClient();
    queryClient.setQueryData(reviewQueryKeys.item("rev-1"), item());
    queryClient.setQueryData(reviewQueryKeys.criteria("rev-1"), criteria());
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    const start = renderHook(() => useStartReviewItem(), { wrapper: wrapperOf(queryClient) });
    await act(async () => {
      await start.result.current.mutateAsync({ id: "rev-1", expectedRevision: 1 });
    });

    expect(post).toHaveBeenCalledWith("/review-items/rev-1/start", { expected_revision: 1 });
    expect(queryClient.getQueryData(reviewQueryKeys.item("rev-1"))).toMatchObject({ revision: 2 });
    const invalidated = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(invalidated).toContainEqual(reviewQueryKeys.lists());
    expect(invalidated).toContainEqual(reviewQueryKeys.criteria("rev-1"));
    expect(get).not.toHaveBeenCalled();
  });

  it("a decision write puts the answer in the criteria cache and stales only the item", async () => {
    const answered = criteria({ revision: 6, status: "in_review" });
    const post = vi.spyOn(api, "post").mockResolvedValue(axiosResponse(answered));
    const queryClient = testQueryClient();
    queryClient.setQueryData(reviewQueryKeys.criteria("rev-1"), criteria());
    queryClient.setQueryData(reviewQueryKeys.item("rev-1"), item({ revision: 4, status: "in_review" }));
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    const decide = renderHook(() => useDecideReviewCriterion(), { wrapper: wrapperOf(queryClient) });
    await act(async () => {
      await decide.result.current.mutateAsync({
        id: "rev-1",
        criterionId: "greeting",
        input: { expected_revision: 2, decision: "accept" },
      });
    });

    expect(post).toHaveBeenCalledWith("/review-items/rev-1/criteria/greeting/decision", {
      expected_revision: 2,
      decision: "accept",
    });
    expect(queryClient.getQueryData(reviewQueryKeys.criteria("rev-1"))).toMatchObject({ revision: 6 });
    // The answer's revision also lands in the item cache at once, so a write
    // clicked before the item refetch never sends the pre-decision revision.
    expect(queryClient.getQueryData(reviewQueryKeys.item("rev-1"))).toMatchObject({
      revision: 6,
      status: "in_review",
    });
    expect(invalidate).toHaveBeenCalledTimes(1);
    const [firstCall] = invalidate.mock.calls;
    expect(firstCall?.[0]).toMatchObject({
      queryKey: reviewQueryKeys.item("rev-1"),
      exact: true,
    });
  });
});
