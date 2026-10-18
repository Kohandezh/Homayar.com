import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { isAxiosError, type AxiosResponse } from "axios";

import api, { API_BASE_URL } from "@/lib/api";
import { apiErrorInfo } from "@/lib/api-errors";
import { NONTERMINAL_MEETING_STATUSES as NONTERMINAL_STATUSES } from "@/lib/status-labels";
import type {
  ActionItem,
  AdminJobListParams,
  AdminJobListResponse,
  ApiKeyListResponse,
  AuditEventListParams,
  AuditEventListResponse,
  AuditEventTypeListResponse,
  BillingUsageResponse,
  CreateActionInput,
  CreatedApiKey,
  CreatedShareLink,
  CreateApiKeyInput,
  CreateShareInput,
  CreateTeamInput,
  CurrentSubscriptionResponse,
  DownloadPayload,
  EvidenceListResponse,
  InvitationAcceptedResponse,
  InvitationListResponse,
  InviteMemberInput,
  JobReplayResponse,
  JobState,
  Meeting,
  MeetingCountsResponse,
  MeetingDetail,
  MeetingExportFormat,
  MeetingListParams,
  MeetingListResponse,
  MeetingStatus,
  MeetingSummary,
  MemberListResponse,
  PiiRevealResponse,
  PlanCatalogResponse,
  ReadinessResponse,
  ProcessingJob,
  RunPolicySnapshotListResponse,
  SearchParams,
  SearchResponse,
  ShareLink,
  SharedMeeting,
  Team,
  TeamAssignment,
  TeamDetail,
  TeamListResponse,
  AssignTeamMemberInput,
  UpdateTeamInput,
  Transcript,
  TranscriptSpeaker,
  TranscriptView,
  UpdateActionFields,
  UpdateWorkspaceSettingsInput,
  UploadAcceptedResponse,
  UploadMeetingInput,
  UsageEventListParams,
  UsageEventListResponse,
  WorkspaceInvitation,
  WorkspaceMember,
  WorkspaceRole,
  WorkspaceSettings,
} from "@/types/platform";

export const PLATFORM_POLL_INTERVAL_MS = 2_500;
export const MAX_POLL_INTERVAL_MS = 30_000;

/**
 * Polling slows down while nothing changes: 2.5 s for the first 30 s of an
 * unchanged status, then 5 s up to 2 min, 15 s up to 10 min, 30 s after that.
 */
export function pollDelay(unchangedForMs: number): number {
  if (unchangedForMs < 30_000) return PLATFORM_POLL_INTERVAL_MS;
  if (unchangedForMs < 120_000) return 5_000;
  if (unchangedForMs < 600_000) return 15_000;
  return MAX_POLL_INTERVAL_MS;
}

const unchanged = new Map<string, { signature: string; since: number }>();

/** How long `key` has shown the same `signature` in this browser tab. */
function unchangedFor(key: string, signature: string, now = Date.now()): number {
  const current = unchanged.get(key);
  if (!current || current.signature !== signature) {
    unchanged.set(key, { signature, since: now });
    return 0;
  }
  return now - current.since;
}

/**
 * After a failed poll: stop when the resource or access is gone (401, 403,
 * 404, 410), otherwise wait at least the server's Retry-After.
 */
export function pollDelayAfterError(error: unknown): number | false {
  const info = apiErrorInfo(error);
  if (info.status !== null && [401, 403, 404, 410].includes(info.status)) return false;
  if (info.retryAfterSeconds !== null) {
    return Math.min(Math.max(info.retryAfterSeconds * 1000, PLATFORM_POLL_INTERVAL_MS), 5 * MAX_POLL_INTERVAL_MS);
  }
  return 15_000;
}

const NONTERMINAL_MEETING_STATUSES = new Set<MeetingStatus>([
  "uploading",
  "uploaded",
  "queued",
  "processing",
  "transcribing",
  "diarizing",
  "analyzing",
]);

const NONTERMINAL_JOB_STATES = new Set<JobState>(["queued", "running"]);

export function meetingRefetchInterval(
  meeting: Pick<Meeting, "status"> | null | undefined,
  unchangedForMs = 0,
): number | false {
  return meeting && NONTERMINAL_MEETING_STATUSES.has(meeting.status)
    ? pollDelay(unchangedForMs)
    : false;
}

export function jobRefetchInterval(
  job: Pick<ProcessingJob, "state"> | null | undefined,
  unchangedForMs = 0,
): number | false {
  return job && NONTERMINAL_JOB_STATES.has(job.state)
    ? pollDelay(unchangedForMs)
    : false;
}

export const platformQueryKeys = {
  all: ["platform"] as const,
  meetings: () => ["platform", "meetings"] as const,
  meetingLists: () => ["platform", "meetings", "list"] as const,
  meetingList: (params: MeetingListParams) =>
    ["platform", "meetings", "list", params] as const,
  /** Server-side per-status totals (GET /meetings/counts), under the meetings() prefix. */
  meetingCounts: () => ["platform", "meetings", "counts"] as const,
  meeting: (meetingId: string) => ["platform", "meetings", meetingId] as const,
  meetingJob: (meetingId: string) =>
    ["platform", "meetings", meetingId, "job"] as const,
  transcript: (meetingId: string) =>
    ["platform", "meetings", meetingId, "transcript"] as const,
  /** Audited original under an enforced redaction policy; never kept after use. */
  transcriptOriginal: (meetingId: string) =>
    ["platform", "meetings", meetingId, "transcript", "original"] as const,
  evidence: (meetingId: string) =>
    ["platform", "meetings", meetingId, "evidence"] as const,
  summary: (meetingId: string) =>
    ["platform", "meetings", meetingId, "summary"] as const,
  actions: (meetingId: string) =>
    ["platform", "meetings", meetingId, "actions"] as const,
  shares: (meetingId: string) =>
    ["platform", "meetings", meetingId, "shares"] as const,
  sharedMeeting: (shareToken: string) =>
    ["platform", "shares", shareToken] as const,
  search: (params: SearchParams) => ["platform", "search", params] as const,
  searchRoot: () => ["platform", "search"] as const,
  billing: () => ["platform", "billing"] as const,
  plans: () => ["platform", "billing", "plans"] as const,
  subscription: () => ["platform", "billing", "subscription"] as const,
  billingUsage: () => ["platform", "billing", "usage"] as const,
  usageEvents: (params: UsageEventListParams) =>
    ["platform", "billing", "usage-events", params] as const,
  usageEventsRoot: () => ["platform", "billing", "usage-events"] as const,
  members: () => ["platform", "members"] as const,
  invitations: () => ["platform", "members", "invitations"] as const,
  teams: () => ["platform", "teams"] as const,
  /** The prefix above also matches this key, so one invalidation refreshes both. */
  team: (id: string) => ["platform", "teams", id] as const,
  admin: () => ["platform", "admin"] as const,
  auditEvents: (params: AuditEventListParams) =>
    ["platform", "admin", "audit-events", params] as const,
  auditEventsRoot: () => ["platform", "admin", "audit-events"] as const,
  auditEventTypes: () => ["platform", "admin", "audit-event-types"] as const,
  adminJobs: (params: AdminJobListParams) =>
    ["platform", "admin", "jobs", params] as const,
  adminJobsRoot: () => ["platform", "admin", "jobs"] as const,
  jobPolicySnapshots: (jobId: string) =>
    ["platform", "admin", "jobs", jobId, "policy-snapshots"] as const,
  workspaceSettings: () => ["platform", "admin", "workspace-settings"] as const,
  apiKeys: () => ["platform", "admin", "api-keys"] as const,
  readiness: () => ["platform", "readiness"] as const,
};

export async function listMeetings(
  params: MeetingListParams = {},
  signal?: AbortSignal,
): Promise<MeetingListResponse> {
  const response = await api.get<MeetingListResponse>("/meetings", signal ? { params, signal } : { params });
  return response.data;
}

export async function getMeetingCounts(signal?: AbortSignal): Promise<MeetingCountsResponse> {
  const response = await api.get<MeetingCountsResponse>("/meetings/counts", signal ? { signal } : undefined);
  return response.data;
}

export async function getMeeting(meetingId: string, signal?: AbortSignal): Promise<MeetingDetail> {
  const path = `/meetings/${meetingId}`;
  const response = signal ? await api.get<MeetingDetail>(path, { signal }) : await api.get<MeetingDetail>(path);
  return response.data;
}

export async function getMeetingJob(meetingId: string, signal?: AbortSignal): Promise<ProcessingJob> {
  const path = `/meetings/${meetingId}/job`;
  const response = signal ? await api.get<ProcessingJob>(path, { signal }) : await api.get<ProcessingJob>(path);
  return response.data;
}

/** Requeue a dead-lettered job (owner/admin; POST /admin/jobs/{id}/replay). */
export async function replayJob(jobId: string): Promise<JobReplayResponse> {
  const response = await api.post<JobReplayResponse>(`/admin/jobs/${jobId}/replay`);
  return response.data;
}

export interface UploadTransferOptions {
  /** Bytes sent so far; this is the transfer, not processing progress. */
  onUploadProgress?: (loaded: number, total: number | null) => void;
  signal?: AbortSignal;
}

export async function uploadMeeting(
  input: UploadMeetingInput,
  options: UploadTransferOptions = {},
): Promise<UploadAcceptedResponse> {
  const form = new FormData();
  form.append("file", input.file, input.file.name);
  if (input.title !== undefined) form.append("title", input.title);
  form.append("language", input.language ?? "fa");

  const response = await api.post<UploadAcceptedResponse>("/uploads", form, {
    headers: { "Idempotency-Key": input.idempotencyKey },
    ...(options.signal ? { signal: options.signal } : {}),
    ...(options.onUploadProgress
      ? {
          onUploadProgress: (event: { loaded: number; total?: number }) =>
            options.onUploadProgress?.(event.loaded, event.total ?? null),
        }
      : {}),
  });
  return response.data;
}

export async function updateMeetingTitle(
  meetingId: string,
  title: string,
): Promise<Meeting> {
  const response = await api.patch<Meeting>(`/meetings/${meetingId}`, { title });
  return response.data;
}

export async function deleteMeeting(meetingId: string): Promise<void> {
  await api.delete(`/meetings/${meetingId}`);
}

function headerString(response: AxiosResponse<Blob>, name: string): string | undefined {
  const value = response.headers[name];
  if (value === undefined || value === null) return undefined;
  return Array.isArray(value) ? value.join(", ") : String(value);
}

export function filenameFromContentDisposition(
  contentDisposition: string | undefined,
  fallback: string,
): string {
  if (!contentDisposition) return fallback;

  const encoded = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(contentDisposition)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded.trim().replace(/^"|"$/g, ""));
    } catch {
      return fallback;
    }
  }

  const plain = /filename\s*=\s*"([^"]+)"/i.exec(contentDisposition)?.[1]
    ?? /filename\s*=\s*([^;\s]+)/i.exec(contentDisposition)?.[1];
  return plain?.trim() || fallback;
}

function downloadPayload(
  response: AxiosResponse<Blob>,
  fallbackFilename: string,
): DownloadPayload {
  return {
    blob: response.data,
    filename: filenameFromContentDisposition(
      headerString(response, "content-disposition"),
      fallbackFilename,
    ),
    contentType: headerString(response, "content-type") ?? response.data.type,
  };
}

export async function downloadRecording(meetingId: string): Promise<DownloadPayload> {
  const response = await api.get<Blob>(`/meetings/${meetingId}/recording`, {
    responseType: "blob",
  });
  return downloadPayload(response, `homayar-${meetingId}-recording`);
}

export async function exportMeeting(
  meetingId: string,
  format: MeetingExportFormat,
): Promise<DownloadPayload> {
  const response = await api.get<Blob>(`/meetings/${meetingId}/export`, {
    params: { format },
    responseType: "blob",
  });
  return downloadPayload(response, `homayar-${meetingId}.${format}`);
}

export function saveDownload(payload: DownloadPayload): void {
  const url = URL.createObjectURL(payload.blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = payload.filename;
  anchor.style.display = "none";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export async function getTranscript(meetingId: string, view?: TranscriptView): Promise<Transcript> {
  const path = `/meetings/${meetingId}/transcript`;
  const response = view ? await api.get<Transcript>(path, { params: { view } }) : await api.get<Transcript>(path);
  return response.data;
}

export async function getEvidence(meetingId: string): Promise<EvidenceListResponse> {
  const response = await api.get<EvidenceListResponse>(`/meetings/${meetingId}/evidence`);
  return response.data;
}

/** Decrypts one redacted value; owner only, reason required, audited as pii.revealed. */
export async function revealRedactedValue(
  meetingId: string,
  redactionId: string,
  placeholder: string,
  reason: string,
): Promise<PiiRevealResponse> {
  const response = await api.post<PiiRevealResponse>(
    `/meetings/${meetingId}/transcript/redactions/${redactionId}/reveal`,
    { placeholder, reason },
  );
  return response.data;
}

export async function getMeetingSummary(meetingId: string): Promise<MeetingSummary> {
  const response = await api.get<MeetingSummary>(`/meetings/${meetingId}/summary`);
  return response.data;
}

export async function listActions(meetingId: string): Promise<ActionItem[]> {
  const response = await api.get<ActionItem[]>(`/meetings/${meetingId}/actions`);
  return response.data;
}

export async function createAction(
  meetingId: string,
  input: CreateActionInput,
): Promise<ActionItem> {
  const response = await api.post<ActionItem>(`/meetings/${meetingId}/actions`, input);
  return response.data;
}

export async function updateAction(
  meetingId: string,
  actionId: string,
  input: UpdateActionFields,
): Promise<ActionItem> {
  const response = await api.patch<ActionItem>(
    `/meetings/${meetingId}/actions/${actionId}`,
    input,
  );
  return response.data;
}

export async function deleteAction(meetingId: string, actionId: string): Promise<void> {
  await api.delete(`/meetings/${meetingId}/actions/${actionId}`);
}

export async function renameSpeaker(
  meetingId: string,
  speakerId: string,
  displayName: string | null,
): Promise<TranscriptSpeaker> {
  const response = await api.patch<TranscriptSpeaker>(
    `/meetings/${meetingId}/speakers/${speakerId}`,
    { display_name: displayName },
  );
  return response.data;
}

export async function searchMeetings(params: SearchParams): Promise<SearchResponse> {
  const response = await api.get<SearchResponse>("/search", { params });
  return response.data;
}

export async function createShare(
  meetingId: string,
  input: CreateShareInput,
): Promise<CreatedShareLink> {
  const response = await api.post<CreatedShareLink>(
    `/meetings/${meetingId}/shares`,
    input,
  );
  return response.data;
}

export async function listShares(meetingId: string): Promise<ShareLink[]> {
  const response = await api.get<ShareLink[]>(`/meetings/${meetingId}/shares`);
  return response.data;
}

export async function revokeShare(meetingId: string, shareId: string): Promise<void> {
  await api.delete(`/meetings/${meetingId}/shares/${shareId}`);
}

export async function getSharedMeeting(shareToken: string): Promise<SharedMeeting> {
  const response = await api.get<SharedMeeting>(`/shares/${shareToken}`);
  return response.data;
}

export async function getPlanCatalog(): Promise<PlanCatalogResponse> {
  const response = await api.get<PlanCatalogResponse>("/billing/plans");
  return response.data;
}

export async function getCurrentSubscription(): Promise<CurrentSubscriptionResponse> {
  const response = await api.get<CurrentSubscriptionResponse>("/billing/subscription");
  return response.data;
}

export async function getBillingUsage(): Promise<BillingUsageResponse> {
  const response = await api.get<BillingUsageResponse>("/billing/usage");
  return response.data;
}

export async function listUsageEvents(
  params: UsageEventListParams = {},
): Promise<UsageEventListResponse> {
  const response = await api.get<UsageEventListResponse>("/billing/usage/events", {
    params,
  });
  return response.data;
}

export async function listMembers(): Promise<MemberListResponse> {
  const response = await api.get<MemberListResponse>("/members");
  return response.data;
}

export async function listInvitations(): Promise<InvitationListResponse> {
  const response = await api.get<InvitationListResponse>("/members/invitations");
  return response.data;
}

export async function inviteMember(input: InviteMemberInput): Promise<WorkspaceInvitation> {
  const response = await api.post<WorkspaceInvitation>("/members/invitations", input);
  return response.data;
}

export async function revokeInvitation(invitationId: string): Promise<void> {
  await api.delete(`/members/invitations/${invitationId}`);
}

export async function acceptInvitation(
  token: string,
): Promise<InvitationAcceptedResponse> {
  const response = await api.post<InvitationAcceptedResponse>(
    "/members/invitations/accept",
    { token },
  );
  return response.data;
}

export async function changeMemberRole(
  membershipId: string,
  role: Exclude<WorkspaceRole, "owner">,
): Promise<WorkspaceMember> {
  const response = await api.patch<WorkspaceMember>(`/members/${membershipId}`, { role });
  return response.data;
}

export async function removeMember(membershipId: string): Promise<void> {
  await api.delete(`/members/${membershipId}`);
}

export async function listTeams(signal?: AbortSignal): Promise<TeamListResponse> {
  const response = signal ? await api.get<TeamListResponse>("/teams", { signal }) : await api.get<TeamListResponse>("/teams");
  return response.data;
}

export async function getTeam(teamId: string, signal?: AbortSignal): Promise<TeamDetail> {
  const path = `/teams/${teamId}`;
  const response = signal ? await api.get<TeamDetail>(path, { signal }) : await api.get<TeamDetail>(path);
  return response.data;
}

export async function createTeam(input: CreateTeamInput): Promise<Team> {
  const response = await api.post<Team>("/teams", { name: input.name });
  return response.data;
}

export async function updateTeam(teamId: string, patch: UpdateTeamInput): Promise<Team> {
  const response = await api.patch<Team>(`/teams/${teamId}`, patch);
  return response.data;
}

export async function assignTeamMember(teamId: string, input: AssignTeamMemberInput): Promise<TeamAssignment> {
  // The date keys are absent, not null, when no period was chosen.
  const body = {
    user_id: input.user_id,
    role: input.role,
    ...(input.valid_from !== undefined ? { valid_from: input.valid_from } : {}),
    ...(input.valid_until !== undefined ? { valid_until: input.valid_until } : {}),
  };
  const response = await api.post<TeamAssignment>(`/teams/${teamId}/assignments`, body);
  return response.data;
}

export async function revokeTeamAssignment(teamId: string, assignmentId: string): Promise<void> {
  await api.delete(`/teams/${teamId}/assignments/${assignmentId}`);
}

export async function listAuditEvents(
  params: AuditEventListParams = {},
): Promise<AuditEventListResponse> {
  const response = await api.get<AuditEventListResponse>("/admin/audit-events", { params });
  return response.data;
}

/** The catalog behind the audit event-type filter (GET /admin/audit-event-types). */
export async function listAuditEventTypes(
  signal?: AbortSignal,
): Promise<AuditEventTypeListResponse> {
  const path = "/admin/audit-event-types";
  const response = signal
    ? await api.get<AuditEventTypeListResponse>(path, { signal })
    : await api.get<AuditEventTypeListResponse>(path);
  return response.data;
}

/**
 * One page of the workspace's processing jobs (HOM-183). `state` repeats in
 * the query string, so the array is serialized without indexes
 * (`?state=failed&state=dead_letter`), which is what FastAPI reads.
 */
export async function listAdminJobs(
  params: AdminJobListParams = {},
): Promise<AdminJobListResponse> {
  const response = await api.get<AdminJobListResponse>("/admin/jobs", {
    params,
    paramsSerializer: { indexes: null },
  });
  return response.data;
}

export async function getJobPolicySnapshots(
  jobId: string,
): Promise<RunPolicySnapshotListResponse> {
  const response = await api.get<RunPolicySnapshotListResponse>(
    `/admin/jobs/${jobId}/policy-snapshots`,
  );
  return response.data;
}

export async function getWorkspaceSettings(): Promise<WorkspaceSettings> {
  const response = await api.get<WorkspaceSettings>("/admin/workspace-settings");
  return response.data;
}

export async function updateWorkspaceSettings(
  input: UpdateWorkspaceSettingsInput,
): Promise<WorkspaceSettings> {
  const response = await api.patch<WorkspaceSettings>("/admin/workspace-settings", input);
  return response.data;
}

export async function listApiKeys(signal?: AbortSignal): Promise<ApiKeyListResponse> {
  const config = signal ? { signal } : undefined;
  const response = await api.get<ApiKeyListResponse>("/api-keys", config);
  return response.data;
}

export async function createApiKey(input: CreateApiKeyInput): Promise<CreatedApiKey> {
  const response = await api.post<CreatedApiKey>("/api-keys", input);
  return response.data;
}

export async function revokeApiKey(keyId: string): Promise<void> {
  await api.delete(`/api-keys/${encodeURIComponent(keyId)}`);
}

function meetingIdOrEmpty(meetingId: string | null | undefined): string {
  return meetingId ?? "";
}

async function invalidateMeetingIntelligence(
  queryClient: QueryClient,
  meetingId: string,
): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: platformQueryKeys.actions(meetingId) }),
    queryClient.invalidateQueries({ queryKey: platformQueryKeys.searchRoot() }),
  ]);
}

export function useMeetings(params: MeetingListParams = {}, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: platformQueryKeys.meetingList(params),
    queryFn: ({ signal }) => listMeetings(params, signal),
    enabled: options?.enabled,
    refetchInterval: (query) => {
      if (query.state.status === "error") return pollDelayAfterError(query.state.error);
      const active = query.state.data?.items.filter((meeting) => meetingRefetchInterval(meeting) !== false) ?? [];
      if (!active.length) return false;
      const signature = active.map((meeting) => `${meeting.id}:${meeting.status}`).join(",");
      return pollDelay(unchangedFor(`list:${JSON.stringify(params)}`, signature));
    },
  });
}

export function useMeetingCounts() {
  return useQuery({
    queryKey: platformQueryKeys.meetingCounts(),
    queryFn: ({ signal }) => getMeetingCounts(signal),
    // A 404 (older backend without the endpoint) is not retried, so the sample fallback is not delayed.
    retry: (failureCount, error) => pollDelayAfterError(error) !== false && failureCount < 1,
    refetchInterval: (query) => {
      if (query.state.status === "error") return pollDelayAfterError(query.state.error);
      const counts = query.state.data?.counts;
      if (!counts) return false;
      let inProgress = 0;
      for (const status of NONTERMINAL_STATUSES) inProgress += counts[status] ?? 0;
      if (inProgress <= 0) return false;
      return pollDelay(unchangedFor("counts", JSON.stringify(counts)));
    },
  });
}

export function useMeeting(meetingId: string | null | undefined) {
  const id = meetingIdOrEmpty(meetingId);
  return useQuery({
    queryKey: platformQueryKeys.meeting(id),
    queryFn: ({ signal }) => getMeeting(id, signal),
    enabled: Boolean(meetingId),
    // A deleted or inaccessible meeting is not retried.
    retry: (failureCount, error) => pollDelayAfterError(error) !== false && failureCount < 1,
    refetchInterval: (query) => {
      if (query.state.status === "error") return pollDelayAfterError(query.state.error);
      const meeting = query.state.data;
      if (!meeting) return false;
      const job = meeting.latest_job;
      const signature = `${meeting.status}|${job?.state ?? ""}|${job?.stage ?? ""}|${job?.attempt_count ?? ""}`;
      return meetingRefetchInterval(meeting, unchangedFor(`meeting:${id}`, signature));
    },
  });
}

export function useMeetingJob(meetingId: string | null | undefined) {
  const id = meetingIdOrEmpty(meetingId);
  return useQuery({
    queryKey: platformQueryKeys.meetingJob(id),
    queryFn: ({ signal }) => getMeetingJob(id, signal),
    enabled: Boolean(meetingId),
    refetchInterval: (query) => {
      if (query.state.status === "error") return pollDelayAfterError(query.state.error);
      const job = query.state.data;
      return jobRefetchInterval(job, job ? unchangedFor(`job:${id}`, `${job.state}|${job.stage}`) : 0);
    },
  });
}

export function useReplayJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ jobId }: { jobId: string; meetingId: string }) => replayJob(jobId),
    onSuccess: async (_result, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.meeting(variables.meetingId) }),
        // The job is queued again, so its stages, its row and its `replayable`
        // flag change, on the meeting page and in the admin list.
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.meetingJob(variables.meetingId) }),
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.meetingLists() }),
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.meetingCounts() }),
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.adminJobsRoot() }),
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.billing() }),
      ]);
    },
  });
}

/**
 * Drops a meeting's cached content (transcript, summary, actions, shares and
 * search hits) when it is deleted, tombstoned or no longer accessible. The
 * meeting query itself is left to its observer so a mounted page does not
 * refetch in a loop.
 */
export function forgetMeetingContent(queryClient: QueryClient, meetingId: string) {
  for (const key of [
    platformQueryKeys.transcript(meetingId),
    platformQueryKeys.summary(meetingId),
    platformQueryKeys.actions(meetingId),
    platformQueryKeys.shares(meetingId),
    platformQueryKeys.meetingJob(meetingId),
    platformQueryKeys.evidence(meetingId),
  ]) {
    queryClient.removeQueries({ queryKey: key });
  }
  queryClient.removeQueries({ queryKey: platformQueryKeys.searchRoot() });
}

export function useUploadMeeting() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ transfer, ...input }: UploadMeetingInput & { transfer?: UploadTransferOptions }) =>
      uploadMeeting(input, transfer),
    onSuccess: async (response) => {
      queryClient.setQueryData<MeetingDetail>(
        platformQueryKeys.meeting(response.meeting.id),
        {
          ...response.meeting,
          recording: response.recording,
          latest_job: response.job,
        },
      );
      queryClient.setQueryData(
        platformQueryKeys.meetingJob(response.meeting.id),
        response.job,
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.meetingLists() }),
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.meetingCounts() }),
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.billingUsage() }),
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.usageEventsRoot() }),
      ]);
    },
  });
}

export function useUpdateMeetingTitle() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ meetingId, title }: { meetingId: string; title: string }) =>
      updateMeetingTitle(meetingId, title),
    onSuccess: async (meeting) => {
      queryClient.setQueryData<MeetingDetail>(
        platformQueryKeys.meeting(meeting.id),
        (current) => (current ? { ...current, ...meeting } : current),
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.meetingLists() }),
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.meetingCounts() }),
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.searchRoot() }),
      ]);
    },
  });
}

export function useDeleteMeeting() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteMeeting,
    onSuccess: async (_result, meetingId) => {
      forgetMeetingContent(queryClient, meetingId);
      queryClient.removeQueries({ queryKey: platformQueryKeys.meeting(meetingId), exact: true });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.meetingLists() }),
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.meetingCounts() }),
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.searchRoot() }),
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.billing() }),
      ]);
    },
  });
}

export function useDownloadRecording() {
  return useMutation({ mutationFn: downloadRecording });
}

export function useMeetingExport() {
  return useMutation({
    mutationFn: ({ meetingId, format }: { meetingId: string; format: MeetingExportFormat }) =>
      exportMeeting(meetingId, format),
  });
}

export function useTranscript(meetingId: string | null | undefined) {
  const id = meetingIdOrEmpty(meetingId);
  return useQuery({
    queryKey: platformQueryKeys.transcript(id),
    queryFn: () => getTranscript(id),
    enabled: Boolean(meetingId),
  });
}

/**
 * The original transcript under an enforced redaction policy. Every fetch is
 * an audited read (transcript.original_accessed), so it is fetched once per
 * explicit request, never refetched in the background, and dropped from the
 * cache as soon as nothing shows it.
 */
export function useOriginalTranscript(meetingId: string | null | undefined) {
  const id = meetingIdOrEmpty(meetingId);
  return useQuery({
    queryKey: platformQueryKeys.transcriptOriginal(id),
    queryFn: () => getTranscript(id, "original"),
    enabled: Boolean(meetingId),
    gcTime: 0,
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
  });
}

export function useEvidence(meetingId: string | null | undefined) {
  const id = meetingIdOrEmpty(meetingId);
  return useQuery({
    queryKey: platformQueryKeys.evidence(id),
    queryFn: () => getEvidence(id),
    enabled: Boolean(meetingId),
  });
}

/** Reveals are never cached: the value lives only in the caller's component state. */
export function useRevealValue() {
  return useMutation({
    mutationFn: ({
      meetingId,
      redactionId,
      placeholder,
      reason,
    }: {
      meetingId: string;
      redactionId: string;
      placeholder: string;
      reason: string;
    }) => revealRedactedValue(meetingId, redactionId, placeholder, reason),
  });
}

export function useMeetingSummary(meetingId: string | null | undefined) {
  const id = meetingIdOrEmpty(meetingId);
  return useQuery({
    queryKey: platformQueryKeys.summary(id),
    queryFn: () => getMeetingSummary(id),
    enabled: Boolean(meetingId),
  });
}

export function useActions(meetingId: string | null | undefined) {
  const id = meetingIdOrEmpty(meetingId);
  return useQuery({
    queryKey: platformQueryKeys.actions(id),
    queryFn: () => listActions(id),
    enabled: Boolean(meetingId),
  });
}

export function useCreateAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ meetingId, input }: { meetingId: string; input: CreateActionInput }) =>
      createAction(meetingId, input),
    onSuccess: (_action, variables) =>
      invalidateMeetingIntelligence(queryClient, variables.meetingId),
  });
}

export function useUpdateAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      meetingId,
      actionId,
      input,
    }: {
      meetingId: string;
      actionId: string;
      input: UpdateActionFields;
    }) => updateAction(meetingId, actionId, input),
    onSuccess: (_action, variables) =>
      invalidateMeetingIntelligence(queryClient, variables.meetingId),
  });
}

export function useDeleteAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ meetingId, actionId }: { meetingId: string; actionId: string }) =>
      deleteAction(meetingId, actionId),
    onSuccess: (_result, variables) =>
      invalidateMeetingIntelligence(queryClient, variables.meetingId),
  });
}

export function useRenameSpeaker() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      meetingId,
      speakerId,
      displayName,
    }: {
      meetingId: string;
      speakerId: string;
      displayName: string | null;
    }) => renameSpeaker(meetingId, speakerId, displayName),
    onSuccess: async (_speaker, variables) => {
      await Promise.all([
        // Exact: the audited original view must not be re-read in the background.
        queryClient.invalidateQueries({
          queryKey: platformQueryKeys.transcript(variables.meetingId),
          exact: true,
        }),
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.searchRoot() }),
      ]);
    },
  });
}

export function useMeetingSearch(params: SearchParams | null) {
  const queryParams = params ?? { q: "" };
  return useQuery({
    queryKey: platformQueryKeys.search(queryParams),
    queryFn: () => searchMeetings(queryParams),
    enabled: queryParams.q.trim().length >= 2,
  });
}

export function useShares(meetingId: string | null | undefined) {
  const id = meetingIdOrEmpty(meetingId);
  return useQuery({
    queryKey: platformQueryKeys.shares(id),
    queryFn: () => listShares(id),
    enabled: Boolean(meetingId),
  });
}

export function useCreateShare() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ meetingId, input }: { meetingId: string; input: CreateShareInput }) =>
      createShare(meetingId, input),
    onSuccess: (_share, variables) =>
      queryClient.invalidateQueries({ queryKey: platformQueryKeys.shares(variables.meetingId) }),
  });
}

export function useRevokeShare() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ meetingId, shareId }: { meetingId: string; shareId: string }) =>
      revokeShare(meetingId, shareId),
    onSuccess: (_result, variables) =>
      queryClient.invalidateQueries({ queryKey: platformQueryKeys.shares(variables.meetingId) }),
  });
}

export function useSharedMeeting(shareToken: string | null | undefined) {
  const token = shareToken ?? "";
  return useQuery({
    queryKey: platformQueryKeys.sharedMeeting(token),
    queryFn: () => getSharedMeeting(token),
    enabled: Boolean(shareToken),
  });
}

export function usePlanCatalog() {
  return useQuery({ queryKey: platformQueryKeys.plans(), queryFn: getPlanCatalog });
}

export function useCurrentSubscription() {
  return useQuery({
    queryKey: platformQueryKeys.subscription(),
    queryFn: getCurrentSubscription,
  });
}

/** `billing:read` only (owner, admin); pass `enabled: false` for other roles (gap G5). */
export function useBillingUsage(enabled = true) {
  return useQuery({
    queryKey: platformQueryKeys.billingUsage(),
    queryFn: getBillingUsage,
    enabled,
  });
}

export function useUsageEvents(params: UsageEventListParams = {}) {
  return useQuery({
    queryKey: platformQueryKeys.usageEvents(params),
    queryFn: () => listUsageEvents(params),
  });
}

export function useMembers() {
  return useQuery({ queryKey: platformQueryKeys.members(), queryFn: listMembers });
}

export function useInvitations() {
  return useQuery({
    queryKey: platformQueryKeys.invitations(),
    queryFn: listInvitations,
  });
}

export function useInviteMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: inviteMember,
    // Also after a failure: on invitation_delivery_failed the server created
    // the invitation and then revoked it, so the list has a new revoked row.
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: platformQueryKeys.invitations() }),
  });
}

export function useRevokeInvitation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: revokeInvitation,
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: platformQueryKeys.invitations() }),
  });
}

export function useAcceptInvitation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: acceptInvitation,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.members() }),
        queryClient.invalidateQueries({ queryKey: platformQueryKeys.invitations() }),
      ]);
    },
  });
}

export function useChangeMemberRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      membershipId,
      role,
    }: {
      membershipId: string;
      role: Exclude<WorkspaceRole, "owner">;
    }) => changeMemberRole(membershipId, role),
    // A refusal (e.g. member_not_found after a concurrent removal) refreshes
    // the list too, so the page never keeps showing a stale role.
    onSettled: () => queryClient.invalidateQueries({ queryKey: platformQueryKeys.members() }),
  });
}

export function useRemoveMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: removeMember,
    onSettled: () => queryClient.invalidateQueries({ queryKey: platformQueryKeys.members() }),
  });
}

export function useTeams() {
  return useQuery({
    queryKey: platformQueryKeys.teams(),
    queryFn: ({ signal }) => listTeams(signal),
  });
}

/** One team with its assignments; disabled until a team is actually opened. */
export function useTeam(teamId: string | null | undefined) {
  const id = teamId ?? "";
  return useQuery({
    queryKey: platformQueryKeys.team(id),
    queryFn: ({ signal }) => getTeam(id, signal),
    enabled: Boolean(teamId),
  });
}

/**
 * Every team mutation invalidates the whole `teams` prefix on settle: the
 * list, each team's assignments and the member counts all come from it.
 */
function useInvalidateTeamsOnSettle() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: platformQueryKeys.teams() });
}

export function useCreateTeam() {
  const invalidateTeams = useInvalidateTeamsOnSettle();
  return useMutation({
    mutationFn: createTeam,
    onSettled: invalidateTeams,
  });
}

export function useUpdateTeam() {
  const invalidateTeams = useInvalidateTeamsOnSettle();
  return useMutation({
    mutationFn: ({ teamId, patch }: { teamId: string; patch: UpdateTeamInput }) => updateTeam(teamId, patch),
    onSettled: invalidateTeams,
  });
}

export function useAssignTeamMember() {
  const invalidateTeams = useInvalidateTeamsOnSettle();
  return useMutation({
    mutationFn: ({ teamId, input }: { teamId: string; input: AssignTeamMemberInput }) =>
      assignTeamMember(teamId, input),
    onSettled: invalidateTeams,
  });
}

export function useRevokeTeamAssignment() {
  const invalidateTeams = useInvalidateTeamsOnSettle();
  return useMutation({
    mutationFn: ({ teamId, assignmentId }: { teamId: string; assignmentId: string }) =>
      revokeTeamAssignment(teamId, assignmentId),
    onSettled: invalidateTeams,
  });
}

export function useAuditEvents(params: AuditEventListParams = {}) {
  return useQuery({
    queryKey: platformQueryKeys.auditEvents(params),
    queryFn: () => listAuditEvents(params),
    // Paging keeps the current page (and the focused button) while the next
    // one loads (QA S-01).
    placeholderData: keepPreviousData,
  });
}

/**
 * The catalog behind the event-type filter. It changes only with a backend
 * release, so it is read once per session, not once per page visit.
 */
export function useAuditEventTypes() {
  return useQuery({
    queryKey: platformQueryKeys.auditEventTypes(),
    queryFn: ({ signal }) => listAuditEventTypes(signal),
    staleTime: Infinity,
    gcTime: Infinity,
  });
}

export function useAdminJobs(params: AdminJobListParams = {}) {
  return useQuery({
    queryKey: platformQueryKeys.adminJobs(params),
    queryFn: () => listAdminJobs(params),
    // Paging keeps the current page (and the focused button) while the next
    // one loads, the same as the audit log.
    placeholderData: keepPreviousData,
  });
}

/** The AI policy behind each attempt of one job; only fetched for an open row. */
export function useJobPolicySnapshots(jobId: string | null | undefined) {
  const id = jobId ?? "";
  return useQuery({
    queryKey: platformQueryKeys.jobPolicySnapshots(id),
    queryFn: () => getJobPolicySnapshots(id),
    enabled: Boolean(jobId),
  });
}

export function useWorkspaceSettings() {
  return useQuery({
    queryKey: platformQueryKeys.workspaceSettings(),
    queryFn: getWorkspaceSettings,
  });
}

export function useUpdateWorkspaceSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: updateWorkspaceSettings,
    onSuccess: (settings) => {
      queryClient.setQueryData(platformQueryKeys.workspaceSettings(), settings);
    },
  });
}

export function useApiKeys() {
  return useQuery({
    queryKey: platformQueryKeys.apiKeys(),
    queryFn: ({ signal }) => listApiKeys(signal),
  });
}

/**
 * The 201 answer holds the one and only copy of the raw key, so the mutation
 * is dropped from the cache at once (`gcTime: 0` plus `reset()` when the
 * modal closes) and its success only invalidates the list; the key itself is
 * never written into any query, the way `useRevealValue` never caches a
 * revealed value.
 */
export function useCreateApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createApiKey,
    gcTime: 0,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: platformQueryKeys.apiKeys() });
    },
  });
}

export function useRevokeApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: revokeApiKey,
    // Also on a 404: the key is gone on the server, so the list must not keep
    // showing it as active (the same reason the member mutations invalidate
    // onSettled).
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: platformQueryKeys.apiKeys() });
    },
  });
}

/** The server root for `/readyz`: the API base without its `/api/v1` suffix. */
export function readinessBaseUrl(apiBaseUrl: string = API_BASE_URL): string {
  return apiBaseUrl.replace(/\/api\/v1$/, "");
}

function isReadinessBody(value: unknown): value is ReadinessResponse {
  if (!value || typeof value !== "object") return false;
  const { status, checks } = value as { status?: unknown; checks?: unknown };
  return (status === "ready" || status === "not_ready") && Boolean(checks) && typeof checks === "object";
}

/**
 * GET /readyz at the server root. A 503 carries the same body with
 * `status: "not_ready"`, so it is an answer, not a failure; it is not ready
 * whatever its body says. A network error, any other status or a body
 * without a known `status` stays an error.
 */
export async function getReadiness(signal?: AbortSignal): Promise<ReadinessResponse> {
  const config = signal ? { baseURL: readinessBaseUrl(), signal } : { baseURL: readinessBaseUrl() };
  let data: unknown;
  try {
    data = (await api.get<unknown>("/readyz", config)).data;
  } catch (error) {
    if (isAxiosError(error) && error.response?.status === 503 && isReadinessBody(error.response.data)) {
      return { ...error.response.data, status: "not_ready" };
    }
    throw error;
  }
  if (!isReadinessBody(data)) throw new Error("Unexpected /readyz response body");
  return data;
}

/** No retries: a failed check shows "status unknown" at once instead of after the retry delays. */
export function useReadiness() {
  return useQuery({
    queryKey: platformQueryKeys.readiness(),
    queryFn: ({ signal }) => getReadiness(signal),
    retry: false,
  });
}
