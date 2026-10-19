/**
 * The voicemail answers of the product API (backend/routers/v1/voicemail.py,
 * HOM-252). Every request goes through the shared axios client `@/lib/api`,
 * which adds `X-Workspace-ID`; the backend filters everything by the caller's
 * record scope, so the console only ever shows what these answers return.
 */

export type VoicemailStatus =
  | "received"
  | "processing"
  | "transcribed"
  | "classified"
  | "needs_review"
  | "failed";

export type VoicemailCategory =
  | "complaint"
  | "sales_inquiry"
  | "support"
  | "appointment"
  | "callback_request"
  | "billing"
  | "spam"
  | "empty"
  | "other";

export type VoicemailPriority = "urgent" | "high" | "normal" | "low";

export type FollowUpState = "none" | "pending" | "action_created" | "done";

export interface Voicemail {
  id: string;
  meeting_id: string;
  ingestion_id: string | null;
  external_message_id: string;
  mailbox: string | null;
  extension: string | null;
  caller_number: string | null;
  caller_number_valid: boolean;
  received_at: string;
  duration_seconds: number;
  status: VoicemailStatus;
  review_reason: string | null;
  category: VoicemailCategory | null;
  intent: string | null;
  topics: string[];
  priority: VoicemailPriority | null;
  priority_score: number | null;
  priority_reasons: Record<string, unknown>[];
  classification_confidence: number | null;
  classifier_kind: string | null;
  classifier_version: string | null;
  classifier_model_id: string | null;
  classified_revision_id: string | null;
  classified_at: string | null;
  follow_up_state: FollowUpState;
  follow_up_action_id: string | null;
  follow_up_done_at: string | null;
  assigned_team_id: string | null;
  assigned_user_id: string | null;
  created_at: string;
}

/** GET /voicemails/{id}: the row plus the transcript excerpt and the meeting status. */
export interface VoicemailDetail extends Voicemail {
  meeting_status: string;
  transcript_excerpt: string | null;
  transcript_revision_id: string | null;
}

/**
 * The list query the console sends. The API knows more filters (status,
 * follow_up_state, q, ...), but the inbox page only offers priority and
 * category, plus the `before` cursor and the page size.
 */
export interface VoicemailListParams {
  limit?: number;
  before?: string;
  priority?: VoicemailPriority;
  category?: VoicemailCategory;
}

export interface VoicemailListResponse {
  items: Voicemail[];
  /** `received_at` of the last item when the page was full; send it back as `before`. */
  next_before: string | null;
}

/**
 * PATCH /voicemails/{id}. The body forbids extra keys, and the console sends
 * exactly one: mark handled (`done`) or reopen (`pending`).
 */
export interface VoicemailUpdateInput {
  follow_up_state: "done" | "pending";
}

/** GET /voicemails/analytics; without params the window is the last 30 days. */
export interface VoicemailAnalytics {
  received_from: string;
  received_to: string;
  total: number;
  by_category: Record<string, number>;
  by_priority: Record<string, number>;
  by_status: Record<string, number>;
  by_follow_up_state: Record<string, number>;
  by_day: { day: string; count: number }[];
  followed_up: number;
  median_follow_up_seconds: number | null;
}
