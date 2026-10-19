/**
 * The privacy requests and hooks (HOM-190): the data subject export, the
 * legal-hold register and the media objects of a meeting. Kept out of
 * platform-api.ts like the other admin pages; the keys still sit under
 * platformQueryKeys, so the existing prefixes refresh them.
 */
import { isAxiosError } from "axios";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "@/lib/api";
import { apiErrorInfo } from "@/lib/api-errors";
import { filenameFromContentDisposition, platformQueryKeys, revokeShare } from "@/lib/platform-api";
import type { DownloadPayload } from "@/types/platform";
import type {
  AdminShareListParams,
  AdminShareListResponse,
  LegalHoldListParams,
  LegalHoldListResponse,
  MediaObject,
  MediaObjectListResponse,
  PlaceLegalHoldInput,
  ShareStatus,
  SubjectExportInput,
} from "@/types/privacy";

export const LEGAL_HOLD_PAGE_SIZE = 50;
export const SHARE_PAGE_SIZE = 50;
const EXPORT_FALLBACK_FILENAME = "homayar-subject-export.json";
const DAY_MS = 24 * 60 * 60 * 1000;

export const privacyQueryKeys = {
  legalHoldsRoot: () => [...platformQueryKeys.admin(), "legal-holds"] as const,
  legalHolds: (dueBefore: string | undefined) =>
    [...platformQueryKeys.admin(), "legal-holds", { due_before: dueBefore ?? null }] as const,
  meetingMedia: (meetingId: string) => [...platformQueryKeys.meeting(meetingId), "media"] as const,
  sharesRoot: () => [...platformQueryKeys.admin(), "shares"] as const,
  shares: (status: ShareStatus | undefined) =>
    [...platformQueryKeys.admin(), "shares", { status: status ?? null }] as const,
};

/** The register filter: every hold, holds due now, or holds due within 30 days. */
export type LegalHoldFilter = "all" | "review_due" | "due_30_days";

/** The shared-links filter: one status, or every link. */
export type ShareFilter = "all" | ShareStatus;

/** The `due_before` value of a filter, from the moment it was chosen. */
export function dueBeforeFor(filter: LegalHoldFilter, now: Date = new Date()): string | undefined {
  if (filter === "review_due") return now.toISOString();
  if (filter === "due_30_days") return new Date(now.getTime() + 30 * DAY_MS).toISOString();
  return undefined;
}

/** A Gregorian day (`YYYY-MM-DD`) as 00:00:00 UTC, with a `Z`. */
export function reviewAtFromDay(day: string): string {
  return `${day}T00:00:00.000Z`;
}

/**
 * A blob request gets its error body as a Blob too. The body is read as JSON
 * and put back, so the error code decides the message like on any request.
 * A body that is not JSON stays as it was.
 */
async function withJsonErrorBody(error: unknown): Promise<unknown> {
  if (!isAxiosError(error) || !(error.response?.data instanceof Blob)) return error;
  try {
    error.response.data = JSON.parse(await error.response.data.text());
  } catch {
    // Not JSON (a proxy page, an empty body): the status alone describes it.
  }
  return error;
}

/**
 * The export file of one data subject. It holds original text, so it is
 * never cached, logged or stored: the caller hands it to the browser as a
 * download and drops it.
 */
export async function exportSubjectData(input: SubjectExportInput): Promise<DownloadPayload> {
  try {
    const response = await api.post<Blob>(
      "/privacy/subject-exports",
      { identifiers: input.identifiers, reason: input.reason },
      { responseType: "blob" },
    );
    const disposition = response.headers["content-disposition"];
    return {
      blob: response.data,
      filename: filenameFromContentDisposition(
        typeof disposition === "string" ? disposition : undefined,
        EXPORT_FALLBACK_FILENAME,
      ),
      contentType: response.data.type || "application/json",
    };
  } catch (error) {
    throw await withJsonErrorBody(error);
  }
}

/** One page of the workspace's legal holds, review date first (owner only). */
export async function listLegalHolds(
  params: LegalHoldListParams,
  signal?: AbortSignal,
): Promise<LegalHoldListResponse> {
  const response = await api.get<LegalHoldListResponse>("/admin/legal-holds", {
    params,
    ...(signal ? { signal } : {}),
  });
  return response.data;
}

export async function listMeetingMedia(meetingId: string, signal?: AbortSignal): Promise<MediaObjectListResponse> {
  const path = `/meetings/${meetingId}/media`;
  const response = signal
    ? await api.get<MediaObjectListResponse>(path, { signal })
    : await api.get<MediaObjectListResponse>(path);
  return response.data;
}

/** Holds every live object of the meeting. */
export async function placeMeetingLegalHold(
  meetingId: string,
  input: PlaceLegalHoldInput,
): Promise<MediaObjectListResponse> {
  const response = await api.post<MediaObjectListResponse>(`/meetings/${meetingId}/legal-hold`, input);
  return response.data;
}

export async function releaseLegalHold(mediaId: string, input: { reason: string }): Promise<MediaObject> {
  const response = await api.post<MediaObject>(`/media/${mediaId}/legal-hold/release`, input);
  return response.data;
}

export function useLegalHolds(dueBefore: string | undefined) {
  return useInfiniteQuery({
    queryKey: privacyQueryKeys.legalHolds(dueBefore),
    queryFn: ({ pageParam, signal }) =>
      listLegalHolds(
        {
          limit: LEGAL_HOLD_PAGE_SIZE,
          ...(dueBefore ? { due_before: dueBefore } : {}),
          ...(pageParam ? { cursor: pageParam } : {}),
        },
        signal,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  });
}

export function useMeetingMedia(meetingId: string) {
  return useQuery({
    queryKey: privacyQueryKeys.meetingMedia(meetingId),
    queryFn: ({ signal }) => listMeetingMedia(meetingId, signal),
  });
}

/** One page of the workspace's share links, newest first (owner only). */
export async function listAdminShares(
  params: AdminShareListParams,
  signal?: AbortSignal,
): Promise<AdminShareListResponse> {
  const response = await api.get<AdminShareListResponse>("/admin/shares", {
    params,
    ...(signal ? { signal } : {}),
  });
  return response.data;
}

export function useAdminShares(status: ShareStatus | undefined) {
  return useInfiniteQuery({
    queryKey: privacyQueryKeys.shares(status),
    queryFn: ({ pageParam, signal }) =>
      listAdminShares(
        {
          limit: SHARE_PAGE_SIZE,
          ...(status ? { status } : {}),
          ...(pageParam ? { cursor: pageParam } : {}),
        },
        signal,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  });
}

/** After a link changes, the register and the meeting's own share list are read again. */
async function refreshShares(
  queryClient: ReturnType<typeof useQueryClient>,
  meetingId: string,
): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: privacyQueryKeys.sharesRoot() }),
    queryClient.invalidateQueries({ queryKey: platformQueryKeys.shares(meetingId) }),
  ]);
}

export function useRevokeAdminShare() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ meetingId, shareId }: { meetingId: string; shareId: string }) =>
      revokeShare(meetingId, shareId),
    onSuccess: (_result, variables) => refreshShares(queryClient, variables.meetingId),
    onError: (error, variables) => {
      // Gone on the server: the register on screen is stale.
      const code = apiErrorInfo(error).code;
      if (code === "share_not_found" || code === "meeting_not_found")
        void refreshShares(queryClient, variables.meetingId);
    },
  });
}

/** After a hold changes, the register and the meeting's objects are read again. */
async function refreshHolds(
  queryClient: ReturnType<typeof useQueryClient>,
  meetingId: string | null,
): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: privacyQueryKeys.legalHoldsRoot() }),
    meetingId
      ? queryClient.invalidateQueries({ queryKey: privacyQueryKeys.meetingMedia(meetingId) })
      : Promise.resolve(),
  ]);
}

export function usePlaceLegalHold() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ meetingId, input }: { meetingId: string; input: PlaceLegalHoldInput }) =>
      placeMeetingLegalHold(meetingId, input),
    onSuccess: (_data, variables) => refreshHolds(queryClient, variables.meetingId),
  });
}

export function useReleaseLegalHold() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ mediaId, reason }: { mediaId: string; meetingId: string | null; reason: string }) =>
      releaseLegalHold(mediaId, { reason }),
    onSuccess: (_data, variables) => refreshHolds(queryClient, variables.meetingId),
    onError: (error, variables) => {
      // Someone released it already: the lists on screen are stale.
      if (apiErrorInfo(error).code === "not_held") void refreshHolds(queryClient, variables.meetingId);
    },
  });
}
