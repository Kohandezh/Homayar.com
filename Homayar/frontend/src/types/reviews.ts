/**
 * The supervisor review queue API shapes (HOM-73/HOM-74, HOM-265), copied
 * from backend/schemas/review_items.py. Dates are ISO strings. The machine
 * score and the human score are two separate facts (ADR 0053): neither type
 * ever holds both.
 */
import type { IsoDateTime } from "@/types/platform";

export type ReviewItemStatus = "open" | "in_review" | "completed" | "cancelled";

export type ReviewItemSource = "manual" | "sample";

/** The four cancel codes (a code, never free text, so nothing personal is logged). */
export type ReviewCancelReason = "duplicate" | "out_of_scope" | "meeting_unusable" | "other";

/** One queue entry, as `GET /review-items` and every write answer it. */
export interface ReviewItem {
  id: string;
  meeting_id: string;
  /** The conversation's current team; the item itself stores none. */
  team_id: string | null;
  scorecard_result_id: string | null;
  source: ReviewItemSource | string;
  source_details: Record<string, unknown>;
  status: ReviewItemStatus | string;
  assignee_user_id: string | null;
  assigned_by_user_id: string | null;
  assigned_at: IsoDateTime | null;
  due_at: IsoDateTime;
  /** Active and `due_at <= now`, as the server read it. */
  is_overdue: boolean;
  created_by_user_id: string | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  started_at: IsoDateTime | null;
  completed_at: IsoDateTime | null;
  cancelled_at: IsoDateTime | null;
  cancel_reason: ReviewCancelReason | string | null;
  revision: number;
}

/** Keyset pages: `due_at` then `id` ascending; `next_cursor` continues the list. */
export interface ReviewItemListResponse {
  items: ReviewItem[];
  next_cursor: string | null;
}

/** The machine's own judgement of one criterion, frozen in the stored result. */
export interface ReviewCriterionMachine {
  outcome: string;
  reason: string | null;
  decided_by: string | null;
  review_required: boolean;
  review_reasons: string[];
  evidence: unknown[];
}

/** The human decision on one criterion, or null while nobody has judged it. */
export interface ReviewCriterionDecision {
  decision: "accept" | "override" | string;
  reviewer_outcome: string;
  reason_code: string | null;
  note: string | null;
  evidence: unknown[];
  decided_by_user_id: string | null;
  updated_at: IsoDateTime;
}

export interface ReviewCriterionEntry {
  criterion_id: string;
  position: number;
  weight: number;
  auto_fail: boolean;
  na_allowed: boolean;
  /** False only for `not_applicable_by_conversation`. */
  reviewable: boolean;
  machine: ReviewCriterionMachine;
  decision: ReviewCriterionDecision | null;
}

/** A score the engine computed: the stored machine result, or the human one. */
export interface ReviewScore {
  outcome: string;
  reason: string | null;
  complete: boolean;
  score: number | null;
  score_min: number | null;
  score_max: number | null;
  pass_threshold: number;
  applicable_weight: number;
  earned_weight: number;
  failed_weight: number;
  insufficient_weight: number;
}

/** The human score: a preview while the item is active, the stored row after completion. */
export interface ReviewHumanScore extends ReviewScore {
  final: boolean;
}

/** The answer of the criteria read and of every decision write. */
export interface ReviewCriteriaResponse {
  review_item_id: string;
  revision: number;
  status: ReviewItemStatus | string;
  scorecard_result_id: string | null;
  machine: ReviewScore | null;
  human: ReviewHumanScore | null;
  /** In `position` order. */
  criteria: ReviewCriterionEntry[];
}

/** Reviewer outcomes; the machine also has `insufficient_evidence`. */
export type ReviewerOutcome = "passed" | "failed" | "not_applicable";

/** The frozen list of override reason codes (ADR 0053 D1). */
export type OverrideReasonCode =
  | "evidence_missed"
  | "evidence_misread"
  | "transcript_error"
  | "context_outside_call"
  | "policy_exception"
  | "other";

/** The filters of `GET /review-items` the console sends; omitted keys are not sent. */
export interface ReviewItemListParams {
  status?: "open" | "in_review" | "completed" | "cancelled";
  assignee?: "me" | "unassigned";
  overdue?: boolean;
  meeting_id?: string;
  limit?: number;
  cursor?: string;
}

/** `{ expected_revision, assignee_user_id }` of POST /review-items/{id}/assign. */
export interface ReviewAssignInput {
  expected_revision: number;
  assignee_user_id: string | null;
}

/** `{ expected_revision, due_at }` of POST /review-items/{id}/due-time. */
export interface ReviewDueTimeInput {
  expected_revision: number;
  due_at: string;
}

/** `{ expected_revision, reason }` of POST /review-items/{id}/cancel. */
export interface ReviewCancelInput {
  expected_revision: number;
  reason: ReviewCancelReason;
}

/** The body of POST /review-items/{id}/criteria/{criterion_id}/decision. */
export interface ReviewDecisionInput {
  expected_revision: number;
  decision: "accept" | "override";
  reviewer_outcome?: ReviewerOutcome;
  reason_code?: OverrideReasonCode;
  note?: string;
  evidence_turn_indexes?: number[];
}
