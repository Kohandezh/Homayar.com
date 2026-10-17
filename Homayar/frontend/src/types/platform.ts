export type IsoDateTime = string;

export type JsonPrimitive = boolean | number | string | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type AtLeastOne<T, Keys extends keyof T = keyof T> = Keys extends keyof T
  ? Required<Pick<T, Keys>> & Partial<Omit<T, Keys>>
  : never;

export type MeetingStatus =
  | "uploading"
  | "uploaded"
  | "queued"
  | "processing"
  | "transcribing"
  | "diarizing"
  | "analyzing"
  | "completed"
  | "partial"
  | "failed"
  | "cancelled";

export type JobState =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "dead_letter";

export interface Meeting {
  id: string;
  workspace_id: string;
  title: string;
  language: string;
  status: MeetingStatus;
  duration_seconds: number;
  source: string;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface Recording {
  id: string;
  original_filename: string;
  media_type: string;
  container_format: string;
  size_bytes: number;
  checksum_sha256: string;
  duration_seconds: number;
  channel_count: number | null;
  sample_rate_hz: number | null;
}

export type StageState =
  | "queued"
  | "running"
  | "completed"
  | "partial"
  | "failed"
  | "unavailable"
  | "insufficient_evidence"
  | "skipped";

export interface StageRun {
  stage: string;
  state: StageState;
  reason_code: string | null;
  reason_message: string | null;
  provider: string | null;
  model: string | null;
  is_mock: boolean;
  latency_ms: number | null;
  started_at: IsoDateTime | null;
  finished_at: IsoDateTime | null;
}

export interface ProcessingJob {
  id: string;
  state: JobState;
  stage: string;
  attempt_count: number;
  max_attempts: number;
  failure_code: string | null;
  failure_message: string | null;
  /** When a failed attempt's automatic retry is due (state queued, stage retry_scheduled). */
  retry_after?: IsoDateTime | null;
  /** WP06-T05: publication | transient | permanent | abandoned | deadline | cancelled. */
  failure_class?: string | null;
  deadline_at?: IsoDateTime | null;
  replay_count?: number;
  last_replayed_at?: IsoDateTime | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  stages: StageRun[];
}

/** POST /admin/jobs/{job_id}/replay (WP06-T05). */
export interface JobReplayResponse {
  job: ProcessingJob;
  replay: number;
  max_replays: number;
  dispatch: string;
}

export interface UsageSnapshot {
  metric: string;
  limit: number | null;
  used: number;
  reserved: number;
  remaining: number | null;
}

export type TranscriptionState = "complete" | "partial" | "unavailable";
export type SpeakerAttributionState = "verified" | "inferred" | "unknown";
export type AnalysisState = "complete" | "partial" | "unavailable";
export type EvidenceStatus = "sufficient" | "partial" | "insufficient";

export interface QualityDegradation {
  stage: string;
  state: StageState;
  reason: string | null;
}

/** Quality, coverage and degradation of a meeting's results (WP04-T06). */
export interface MeetingQuality {
  transcription: TranscriptionState;
  speaker_attribution: SpeakerAttributionState;
  analysis: AnalysisState;
  evidence: EvidenceStatus;
  analysis_stale: boolean;
  transcript_revision_id: string | null;
  analysed_revision_id: string | null;
  analysis_run_id: string | null;
  segment_count: number;
  speaker_count: number;
  speech_coverage_ratio: number | null;
  is_mock: boolean;
  degradations: QualityDegradation[];
  usage: { state: string; audio_seconds: number | null } | null;
}

/** Canonical capture source of an ingestion event (WP05-T01, ADR 0016). */
export type IngestionSourceType =
  | "file_upload"
  | "microphone"
  | "system_audio"
  | "mic_plus_system_audio"
  | "sip_voip"
  | "meeting_bot"
  | "external_recording_url"
  | "offline_folder_watcher"
  | "api";
export type IngestionStatus = "accepted" | "rejected";

/** One row of the ingestion ledger: the accepted event behind a conversation. */
export interface IngestionRecord {
  id: string;
  workspace_id: string;
  organization_id: string;
  source_type: IngestionSourceType;
  source_provider: string;
  external_event_id: string;
  external_source_id: string | null;
  payload_hash: string;
  media_hash: string | null;
  media_size_bytes: number | null;
  declared_content_type: string | null;
  original_filename: string | null;
  status: IngestionStatus;
  reason: string | null;
  received_at: IsoDateTime;
  created_at: IsoDateTime;
  created_by_user_id: string | null;
  api_key_id: string | null;
  meeting_id: string | null;
  job_id: string | null;
  duplicate_of_id: string | null;
  conflict_count: number;
  last_conflict_at: IsoDateTime | null;
}

export interface MeetingDetail extends Meeting {
  recording: Recording | null;
  latest_job: ProcessingJob | null;
  quality?: MeetingQuality | null;
  ingestion?: IngestionRecord | null;
}

export interface MeetingListResponse {
  items: Meeting[];
  next_before: IsoDateTime | null;
}

export interface MeetingListParams {
  limit?: number;
  before?: IsoDateTime;
  status?: MeetingStatus;
}

export interface UploadMeetingInput {
  file: File;
  idempotencyKey: string;
  title?: string;
  language?: string;
}

export interface UploadAcceptedResponse {
  meeting: Meeting;
  recording: Recording;
  job: ProcessingJob;
  usage: UsageSnapshot;
  idempotent_replay: boolean;
  ingestion?: IngestionRecord | null;
}

export interface TranscriptSpeaker {
  id: string;
  speaker_key: string;
  display_name: string | null;
  identity_type: string;
  confidence: number | null;
}

export interface TranscriptSegment {
  id: string;
  sequence: number;
  speaker_key: string;
  speaker_name: string;
  start_ms: number;
  end_ms: number;
  text: string;
  confidence: number | null;
  language: string | null;
}

/** One immutable transcript revision (WP04-T02). */
export interface TranscriptRevision {
  id: string;
  meeting_id: string;
  revision_number: number;
  parent_revision_id: string | null;
  source: "asr" | "correction" | "backfill";
  reason: string;
  note: string | null;
  language: string;
  segment_count: number;
  created_at: IsoDateTime;
  created_by_user_id: string | null;
  asr_provider: string | null;
  asr_model: string | null;
  diarization_status: string | null;
  is_mock: boolean;
  is_current: boolean;
  superseded_at: IsoDateTime | null;
}

/** The redacted version a transcript response was rendered from (WP07-T06). */
export interface TranscriptRedaction {
  id: string | null;
  detector_version: string;
  classes: string[];
  policy_digest: string;
  entity_count: number;
  mapping_retained: boolean;
}

export type TranscriptView = "original" | "redacted";

/**
 * The view a summary, action item or evidence response was rendered in
 * (gap G10). `redaction` is present only for a redacted view: the texts went
 * through the same detector and classes as the transcript.
 */
export interface ContentRedaction {
  detector_version: string;
  classes: string[];
  policy_digest: string;
}

export interface ContentView {
  view?: TranscriptView;
  privacy_enforced?: boolean;
  redaction?: ContentRedaction | null;
}

export interface Transcript {
  meeting_id: string;
  language: string;
  revision?: TranscriptRevision | null;
  segments: TranscriptSegment[];
  speakers: TranscriptSpeaker[];
  /** Which version the texts are; "original" under an enforced policy was an audited request. */
  view?: TranscriptView;
  privacy_enforced?: boolean;
  redaction?: TranscriptRedaction | null;
}

/** POST /meetings/{id}/transcript/redactions/{redaction_id}/reveal (owner, audited). */
export interface PiiRevealResponse {
  redaction_id: string;
  placeholder: string;
  value: string;
}

/** A finding's citation resolved to transcript text (GET /meetings/{id}/evidence). */
export interface EvidenceSpan {
  id: string;
  meeting_id: string;
  analysis_run_id: string | null;
  transcript_revision_id: string;
  revision_number: number;
  is_current_revision: boolean;
  segment_id: string;
  speaker_key: string;
  finding_type: string;
  finding_id: string | null;
  finding_index: number | null;
  text_start: number;
  text_end: number;
  text: string;
  start_ms: number;
  end_ms: number;
  confidence: number | null;
  evidence_type: string;
  created_at: IsoDateTime;
}

export interface EvidenceListResponse extends ContentView {
  meeting_id: string;
  analysis_run_id: string | null;
  current_revision_id: string | null;
  items: EvidenceSpan[];
}

export interface SummaryInsight {
  category?: string;
  description?: string;
  confidence?: number | null;
  source_turn_indexes?: number[];
  evidence_ids?: string[];
  evidence_status?: EvidenceStatus;
  [key: string]: JsonValue | undefined;
}

/** Provenance of the analysis pass behind a summary (WP04-T02). */
export interface AnalysisRun {
  id: string;
  transcript_revision_id: string;
  state: string;
  provider: string | null;
  model: string | null;
  prompt_version: string | null;
  schema_version: number;
  is_mock: boolean;
  reference_count?: number;
  rejected_reference_count?: number;
  /** Which transcript text the model was given (WP07-T06). */
  input_view?: TranscriptView;
  created_at: IsoDateTime;
}

export interface MeetingSummary extends ContentView {
  meeting_id: string;
  overview: string;
  decisions: SummaryInsight[];
  key_points: SummaryInsight[];
  topics: string[];
  sentiment: string | null;
  provider: string;
  model: string;
  schema_version: number;
  analysis_run_id?: string | null;
  analysis_run?: AnalysisRun | null;
  evidence_status?: EvidenceStatus;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

/** A finding's citation into an immutable transcript segment (WP04-T03). */
export interface EvidenceRef {
  id: string;
  transcript_revision_id: string;
  segment_id: string;
  speaker_key: string;
  text_start: number;
  text_end: number;
  start_ms: number;
  end_ms: number;
  evidence_type: "turn_reference" | "human";
}

export type ActionPriority = "low" | "medium" | "high";
export type ActionStatus = "open" | "in_progress" | "done" | "cancelled";

export interface ActionItem extends ContentView {
  id: string;
  meeting_id: string;
  description: string;
  assignee_text: string | null;
  assignee_user_id: string | null;
  due_at: IsoDateTime | null;
  priority: ActionPriority;
  status: ActionStatus;
  source_segment_id: string | null;
  source_timestamp_ms: number | null;
  confidence: number | null;
  created_by: string;
  analysis_run_id?: string | null;
  evidence_status?: EvidenceStatus;
  evidence?: EvidenceRef[];
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface CreateActionInput {
  description: string;
  assignee_text?: string | null;
  assignee_user_id?: string | null;
  due_at?: IsoDateTime | null;
  priority?: ActionPriority;
  source_segment_id?: string | null;
}

interface UpdateActionShape {
  description?: string | null;
  assignee_text?: string | null;
  assignee_user_id?: string | null;
  due_at?: IsoDateTime | null;
  priority?: ActionPriority | null;
  status?: ActionStatus | null;
}

export type UpdateActionFields = AtLeastOne<UpdateActionShape>;

export interface SearchMatch {
  kind: string;
  snippet: string;
  timestamp_ms: number | null;
}

export interface SearchMeeting {
  id: string;
  title: string;
  status: MeetingStatus;
  language: string;
  duration_seconds: number;
  created_at: IsoDateTime;
  matches: SearchMatch[];
}

export interface SearchResponse {
  query: string;
  items: SearchMeeting[];
}

export interface SearchParams {
  q: string;
  created_after?: IsoDateTime;
  created_before?: IsoDateTime;
  limit?: number;
}

export type MeetingExportFormat = "txt" | "json" | "srt" | "vtt";

export interface DownloadPayload {
  blob: Blob;
  filename: string;
  contentType: string;
}

export interface CreateShareInput {
  expires_in_hours?: number | null;
  require_authentication?: boolean;
}

export interface ShareLink {
  id: string;
  meeting_id: string;
  require_authentication: boolean;
  expires_at: IsoDateTime | null;
  revoked_at: IsoDateTime | null;
  access_count: number;
  last_accessed_at: IsoDateTime | null;
  created_at: IsoDateTime;
}

export interface CreatedShareLink extends ShareLink {
  url: string;
}

export interface SharedMeeting {
  meeting_id: string;
  title: string;
  language: string;
  duration_seconds: number;
  created_at: IsoDateTime;
  transcript: Transcript | null;
  summary: MeetingSummary | null;
  actions: ActionItem[];
  quality?: MeetingQuality | null;
}

export interface Plan {
  key: string;
  name: string;
  is_public: boolean;
  entitlements: Record<string, JsonValue>;
  price_published: false;
  pricing_mode: "manual_quote";
}

export interface PlanCatalogResponse {
  items: Plan[];
}

export type SubscriptionStatus =
  | "trialing"
  | "active"
  | "past_due"
  | "cancelled"
  | "expired";

export interface Subscription {
  id: string;
  plan_key: string;
  status: SubscriptionStatus;
  provider: string;
  starts_at: IsoDateTime;
  ends_at: IsoDateTime | null;
  cancelled_at: IsoDateTime | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface CurrentSubscriptionResponse {
  workspace_id: string;
  plan: Plan | null;
  subscription: Subscription | null;
  billing_mode: "manual";
  price_published: false;
}

export interface BillingUsageSnapshot extends UsageSnapshot {
  source: string;
  period_start: IsoDateTime;
  period_end: IsoDateTime | null;
  updated_at: IsoDateTime;
}

export interface BillingUsageResponse {
  workspace_id: string;
  items: BillingUsageSnapshot[];
}

export interface UsageEvent {
  id: string;
  metric: string;
  value: number;
  event_type: string;
  subject_type: string;
  subject_id: string;
  occurred_at: IsoDateTime;
}

export interface UsageEventListResponse {
  items: UsageEvent[];
  next_before: IsoDateTime | null;
  next_before_id: string | null;
}

export interface UsageEventListParams {
  limit?: number;
  metric?: string;
  before?: IsoDateTime;
  before_id?: string;
}

export type WorkspaceRole = "owner" | "admin" | "member" | "viewer";
export type AssignableWorkspaceRole = Exclude<WorkspaceRole, "owner">;
export type InvitationStatus = "pending" | "accepted" | "revoked" | "expired";

export interface WorkspaceMember {
  id: string;
  user_id: string;
  email: string | null;
  phone_e164: string | null;
  display_name: string;
  role: WorkspaceRole;
  joined_at: IsoDateTime;
}

export interface MemberListResponse {
  items: WorkspaceMember[];
  total: number;
}

export interface WorkspaceInvitation {
  id: string;
  workspace_id: string;
  email: string;
  role: AssignableWorkspaceRole;
  invited_by_user_id: string;
  status: InvitationStatus;
  expires_at: IsoDateTime;
  accepted_at: IsoDateTime | null;
  revoked_at: IsoDateTime | null;
  created_at: IsoDateTime;
}

export interface InvitationListResponse {
  items: WorkspaceInvitation[];
  total: number;
}

export interface InviteMemberInput {
  email: string;
  role?: AssignableWorkspaceRole;
  expires_in_days?: number;
}

export interface InvitationAcceptedResponse {
  workspace_id: string;
  workspace_name: string;
  membership: WorkspaceMember;
}

export interface AuditEvent {
  id: string;
  actor_user_id: string | null;
  event_type: string;
  target_type: string | null;
  target_id: string | null;
  outcome: string;
  request_id: string | null;
  details: Record<string, JsonValue>;
  occurred_at: IsoDateTime;
}

export interface AuditEventListResponse {
  items: AuditEvent[];
  next_cursor: string | null;
}

export interface AuditEventListParams {
  limit?: number;
  cursor?: string;
  event_type?: string;
  outcome?: string;
  actor_user_id?: string;
  target_type?: string;
  target_id?: string;
  occurred_after?: IsoDateTime;
  occurred_before?: IsoDateTime;
}

/**
 * One processing job in the administrator's list
 * (GET /admin/jobs, backend/schemas/admin.py AdminJobResponse). Metadata
 * only: no transcript, recording, lease or reservation data.
 */
export interface AdminJob {
  id: string;
  meeting_id: string;
  meeting_title: string;
  state: JobState;
  stage: string;
  attempt_count: number;
  max_attempts: number;
  failure_code: string | null;
  failure_class: string | null;
  /** Already sanitized by the server: secrets redacted, cut to 500 characters. */
  failure_message: string | null;
  replay_count: number;
  max_replays: number;
  /**
   * The replay endpoint's static checks pass. Quota is not part of it, so a
   * replayable job can still be refused with 429.
   */
  replayable: boolean;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface AdminJobListResponse {
  items: AdminJob[];
  next_cursor: string | null;
}

export interface AdminJobListParams {
  limit?: number;
  cursor?: string;
  /** Repeats in the query string (`?state=failed&state=dead_letter`). */
  state?: JobState[];
  stage?: string;
}

/** Which AI policy one processing attempt ran under (GET /admin/jobs/{id}/policy-snapshots). */
export interface RunPolicySnapshot {
  attempt: number;
  policy_version: number;
  policy_digest: string;
  policy_document: Record<string, JsonValue>;
  text_privacy_digest: string | null;
  strict_manifests: boolean;
  stages: Array<Record<string, JsonValue>>;
  created_at: IsoDateTime;
}

export interface RunPolicySnapshotListResponse {
  job_id: string;
  items: RunPolicySnapshot[];
}

/**
 * Explicit retention semantics (WP07-T01, ADR 0020). `finite` keeps data
 * `retention_days` (>= 1) days after ingestion; `indefinite` never deletes by
 * age; `immediate` makes data deletion-eligible once processing is terminal.
 * `legacy_unresolved` is the migrated pre-WP07 zero: nothing is deleted by age
 * until an administrator picks an explicit mode; it can be read, never chosen.
 */
export type RetentionMode = "finite" | "indefinite" | "immediate" | "legacy_unresolved";

/** `workspace`: every member sees every record. `team`: supervisors see their teams, agents their own. */
export type RecordScope = "workspace" | "team";

/** `allow`: a file uploaded again is processed again. `reject`: it is refused. */
export type DuplicateMediaPolicy = "allow" | "reject";

/** The kinds of personal data the redaction can mask. */
export type PiiRedactionClass = "email" | "phone" | "ir_national_id" | "bank_card" | "iban" | "ir_postal_code";

export interface WorkspaceSettings {
  id: string;
  name: string;
  retention_days: number;
  retention_mode: RetentionMode;
  delete_audio_after_processing: boolean;
  record_scope: RecordScope;
  public_share_links_enabled: boolean;
  organization_share_links_enabled: boolean;
  duplicate_media_policy: DuplicateMediaPolicy;
  pii_redaction_enabled: boolean;
  /** Sorted by value by the server. */
  pii_redaction_classes: PiiRedactionClass[];
  updated_at: IsoDateTime;
}

export interface UpdateWorkspaceSettingsShape {
  name?: string;
  retention_days?: number;
  retention_mode?: Exclude<RetentionMode, "legacy_unresolved">;
  delete_audio_after_processing?: boolean;
  record_scope?: RecordScope;
  public_share_links_enabled?: boolean;
  organization_share_links_enabled?: boolean;
  duplicate_media_policy?: DuplicateMediaPolicy;
  /** Owner only (`pii:original_access`). */
  pii_redaction_enabled?: boolean;
  /** Owner only (`pii:original_access`). Never empty. */
  pii_redaction_classes?: PiiRedactionClass[];
}

export type UpdateWorkspaceSettingsInput = AtLeastOne<UpdateWorkspaceSettingsShape>;

/** One dependency check of GET /readyz (served at the server root, not under /api/v1). */
export type ReadinessCheckState = "ready" | "unavailable";

/** GET /readyz: 200 with `ready`, or 503 with `not_ready` and the same shape. */
export interface ReadinessResponse {
  status: "ready" | "not_ready";
  checks: Record<string, ReadinessCheckState>;
}
