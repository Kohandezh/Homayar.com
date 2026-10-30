/**
 * The QA scorecard API (HOM-54, schema `scorecards/1`). The server refuses
 * unknown keys at every level, and integers must be real JSON integers, so
 * every request body carries exactly these fields.
 */

export type ConversationType = "call" | "meeting" | "voicemail" | "other";
export type ConversationDirection = "inbound" | "outbound" | "internal";
export type SpeakerRole = "agent" | "customer" | "any";

/** Which conversations a criterion applies to; a null group means "all" for that group. */
export interface ScorecardApplicability {
  conversation_types: ConversationType[] | null;
  directions: ConversationDirection[] | null;
}

export type ScorecardCheckScope =
  | { kind: "whole_call" }
  | { kind: "opening" | "closing"; max_turns: number };

interface ScorecardCheckCommon {
  /** Always equal to the criterion's `criterion_id`. */
  check_id: string;
  version: number;
  critical?: boolean;
  speaker_role?: SpeakerRole;
  scope?: ScorecardCheckScope;
}

export interface ScorecardPhraseCheck extends ScorecardCheckCommon {
  kind: "required_phrase" | "forbidden_phrase";
  /** Any one of them counts. */
  phrases: string[];
  model_fallback?: boolean;
}

export interface ScorecardContextualCheck extends ScorecardCheckCommon {
  kind: "contextual";
  question: string;
  trigger_phrases?: string[];
  context_turns?: number;
}

export type ScorecardCheck = ScorecardPhraseCheck | ScorecardContextualCheck;
export type ScorecardCheckKind = ScorecardCheck["kind"];
export type ScorecardScopeKind = ScorecardCheckScope["kind"];

export interface ScorecardCriterion {
  criterion_id: string;
  label: string;
  /** 0..100; all weights of a scorecard add up to exactly 100. */
  weight: number;
  auto_fail: boolean;
  na_allowed: boolean;
  /** Null: every conversation. */
  applies_to: ScorecardApplicability | null;
  check: ScorecardCheck;
}

export interface ScorecardDefinition {
  name: string;
  description: string | null;
  /** Percent, 1..100. */
  pass_threshold: number;
  criteria: ScorecardCriterion[];
}

export interface ScorecardItem {
  scorecard_key: string;
  version: number;
  name: string;
  digest: string;
  scorecards_version: string;
  created_at: string;
  created_by_user_id: string | null;
}

export interface ScorecardVersionItem extends ScorecardItem {
  reason: string;
}

export interface ScorecardVersionResponse extends ScorecardVersionItem {
  definition: ScorecardDefinition;
}

export interface ScorecardListResponse {
  items: ScorecardItem[];
}

export interface ScorecardVersionListResponse {
  /** Newest first. */
  items: ScorecardVersionItem[];
}

export interface ScorecardPublishInput {
  definition: ScorecardDefinition;
  reason: string;
  /** The version the caller read, 0 for a new key; a different current version is a 409. */
  expected_version: number;
}

export interface ScorecardValidateResponse {
  valid: true;
  digest: string;
  /** The definition with the server defaults filled in. */
  definition: ScorecardDefinition;
}
