/**
 * The supervisor review queue requests and hooks (HOM-265), on the API of
 * HOM-73/HOM-74. Every write carries `expected_revision` = the revision the
 * caller last read (ADR 0051 D3); the console never retries a refused write
 * by itself. Kept out of platform-api.ts like scorecards-api.ts, with its
 * own query-key root.
 */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "@/lib/api";
import type {
  ReviewAssignInput,
  ReviewCancelInput,
  ReviewCriteriaResponse,
  ReviewDecisionInput,
  ReviewDueTimeInput,
  ReviewItem,
  ReviewItemListParams,
  ReviewItemListResponse,
} from "@/types/reviews";

/** The page size of every queue list read (the API caps a page at 100). */
export const REVIEW_PAGE_SIZE = 50;

export const reviewQueryKeys = {
  all: () => ["reviews"] as const,
  /** Every list page, whatever its filters. */
  lists: () => ["reviews", "list"] as const,
  list: (params: ReviewItemListParams) => ["reviews", "list", params] as const,
  item: (id: string) => ["reviews", "item", id] as const,
  criteria: (id: string) => ["reviews", "item", id, "criteria"] as const,
};

function itemPath(id: string) {
  return `/review-items/${encodeURIComponent(id)}`;
}

/**
 * One page of the queue, `due_at` then `id`. A param that is undefined is
 * not sent; the server then applies its own default (no `status` means the
 * active statuses). The console never filters items by team itself.
 */
export async function listReviewItems(
  params: ReviewItemListParams = {},
  signal?: AbortSignal,
): Promise<ReviewItemListResponse> {
  const response = await api.get<ReviewItemListResponse>("/review-items", {
    params: {
      status: params.status,
      assignee: params.assignee,
      overdue: params.overdue === true ? "true" : undefined,
      meeting_id: params.meeting_id,
      limit: params.limit,
      cursor: params.cursor,
    },
    ...(signal ? { signal } : {}),
  });
  return response.data;
}

export async function getReviewItem(id: string, signal?: AbortSignal): Promise<ReviewItem> {
  const response = signal
    ? await api.get<ReviewItem>(itemPath(id), { signal })
    : await api.get<ReviewItem>(itemPath(id));
  return response.data;
}

export async function assignReviewItem(id: string, input: ReviewAssignInput): Promise<ReviewItem> {
  const response = await api.post<ReviewItem>(`${itemPath(id)}/assign`, input);
  return response.data;
}

export async function changeReviewDueTime(id: string, input: ReviewDueTimeInput): Promise<ReviewItem> {
  const response = await api.post<ReviewItem>(`${itemPath(id)}/due-time`, input);
  return response.data;
}

export async function startReviewItem(id: string, expectedRevision: number): Promise<ReviewItem> {
  const response = await api.post<ReviewItem>(`${itemPath(id)}/start`, { expected_revision: expectedRevision });
  return response.data;
}

export async function completeReviewItem(id: string, expectedRevision: number): Promise<ReviewItem> {
  const response = await api.post<ReviewItem>(`${itemPath(id)}/complete`, { expected_revision: expectedRevision });
  return response.data;
}

export async function cancelReviewItem(id: string, input: ReviewCancelInput): Promise<ReviewItem> {
  const response = await api.post<ReviewItem>(`${itemPath(id)}/cancel`, input);
  return response.data;
}

export async function getReviewCriteria(id: string, signal?: AbortSignal): Promise<ReviewCriteriaResponse> {
  const response = signal
    ? await api.get<ReviewCriteriaResponse>(`${itemPath(id)}/criteria`, { signal })
    : await api.get<ReviewCriteriaResponse>(`${itemPath(id)}/criteria`);
  return response.data;
}

export async function decideReviewCriterion(
  id: string,
  criterionId: string,
  input: ReviewDecisionInput,
): Promise<ReviewCriteriaResponse> {
  const response = await api.post<ReviewCriteriaResponse>(
    `${itemPath(id)}/criteria/${encodeURIComponent(criterionId)}/decision`,
    input,
  );
  return response.data;
}

/** The caller's next queue item: their own active items, earliest due first. */
export async function nextReviewItem(currentId: string, signal?: AbortSignal): Promise<ReviewItem | null> {
  const page = await listReviewItems({ assignee: "me", limit: REVIEW_PAGE_SIZE }, signal);
  return page.items.find((item) => item.id !== currentId) ?? null;
}

/**
 * After an item write: the answer is the fresh item (into its own cache, so
 * the page shows the new revision at once) and the queue list and the
 * criteria view are stale (the revision moved).
 */
function useItemWriteOptions<TVars>(
  mutationFn: (variables: TVars) => Promise<ReviewItem>,
) {
  const queryClient = useQueryClient();
  return {
    mutationFn,
    onSuccess: async (item: ReviewItem) => {
      queryClient.setQueryData(reviewQueryKeys.item(item.id), item);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: reviewQueryKeys.lists() }),
        queryClient.invalidateQueries({ queryKey: reviewQueryKeys.criteria(item.id) }),
      ]);
    },
  };
}

/** The queue list as keyset pages; `fetchNextPage` appends the next one. */
export function useReviewItems(params: ReviewItemListParams = {}) {
  return useInfiniteQuery({
    queryKey: reviewQueryKeys.list(params),
    queryFn: ({ pageParam, signal }) =>
      listReviewItems(
        { ...params, limit: REVIEW_PAGE_SIZE, ...(pageParam ? { cursor: pageParam } : {}) },
        signal,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page: ReviewItemListResponse) => page.next_cursor,
  });
}

export function useReviewItem(id: string | null) {
  return useQuery({
    queryKey: reviewQueryKeys.item(id ?? ""),
    queryFn: ({ signal }) => getReviewItem(id ?? "", signal),
    enabled: id !== null,
  });
}

/** The criteria view; pass `false` until the page knows the item exists. */
export function useReviewCriteria(id: string | null, enabled = true) {
  return useQuery({
    queryKey: reviewQueryKeys.criteria(id ?? ""),
    queryFn: ({ signal }) => getReviewCriteria(id ?? "", signal),
    enabled: id !== null && enabled,
  });
}

export function useAssignReviewItem() {
  return useMutation(useItemWriteOptions(({ id, input }: { id: string; input: ReviewAssignInput }) =>
    assignReviewItem(id, input),
  ));
}

export function useChangeReviewDueTime() {
  return useMutation(useItemWriteOptions(({ id, input }: { id: string; input: ReviewDueTimeInput }) =>
    changeReviewDueTime(id, input),
  ));
}

export function useStartReviewItem() {
  return useMutation(useItemWriteOptions(({ id, expectedRevision }: { id: string; expectedRevision: number }) =>
    startReviewItem(id, expectedRevision),
  ));
}

export function useCompleteReviewItem() {
  return useMutation(useItemWriteOptions(({ id, expectedRevision }: { id: string; expectedRevision: number }) =>
    completeReviewItem(id, expectedRevision),
  ));
}

export function useCancelReviewItem() {
  return useMutation(useItemWriteOptions(({ id, input }: { id: string; input: ReviewCancelInput }) =>
    cancelReviewItem(id, input),
  ));
}

/**
 * A criterion decision: the answer is the fresh criteria view (into its own
 * cache, no extra GET) and the item query is stale, because the write moved
 * the item's revision. `exact`, so the criteria cache under the same prefix
 * is not refetched. The answer's own revision and status also go into the
 * item cache at once: a write clicked before the item refetch lands (for
 * example `Complete review`) must never send the revision from before the
 * decision.
 */
export function useDecideReviewCriterion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, criterionId, input }: { id: string; criterionId: string; input: ReviewDecisionInput }) =>
      decideReviewCriterion(id, criterionId, input),
    onSuccess: async (criteria: ReviewCriteriaResponse) => {
      queryClient.setQueryData(reviewQueryKeys.item(criteria.review_item_id), (existing: ReviewItem | undefined) =>
        existing ? { ...existing, revision: criteria.revision, status: criteria.status } : existing,
      );
      queryClient.setQueryData(reviewQueryKeys.criteria(criteria.review_item_id), criteria);
      await queryClient.invalidateQueries({
        queryKey: reviewQueryKeys.item(criteria.review_item_id),
        exact: true,
      });
    },
  });
}
