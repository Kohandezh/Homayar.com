/**
 * The speaker timeline contract of ADR 0069 (HOM-49): field names, types and
 * enum values are fixed by the decision. The response carries no transcript
 * text; turns join to text by `segment_id` through GET /transcript.
 */

export interface TimelineRevisionInfo {
  id: string;
  revision_number: number;
  source: string;
  is_current: boolean;
  diarization_status: string | null;
  diarization_provider: string | null;
  is_mock: boolean;
}

export interface TimelineLane {
  speaker_key: string;
  display_name: string | null;
  kind: "identified" | "anonymous" | "unknown";
  identity_type: string | null;
  speaker_confidence: number | null;
  turn_count: number;
  speech_ms: number;
  overlap_ms: number;
}

export type TimelineReviewReason = "unknown_speaker" | "overlap" | "outside_recording";

export interface TimelineTurn {
  segment_id: string;
  sequence: number;
  speaker_key: string;
  start_ms: number;
  end_ms: number;
  confidence: number | null;
  attribution: "diarization" | "person" | "backfill" | "none";
  overlap_ids: number[];
  review_reasons: TimelineReviewReason[];
}

export interface TimelineOverlap {
  index: number;
  start_ms: number;
  end_ms: number;
  kind: "cross_speaker" | "same_speaker";
  speaker_keys: string[];
  segment_ids: string[];
  includes_unknown: boolean;
}

export interface SpeakerTimeline {
  meeting_id: string;
  revision: TimelineRevisionInfo | null;
  overlap_basis: "turns";
  recording_duration_ms: number | null;
  timeline_end_ms: number;
  lanes: TimelineLane[];
  /** Ordered by `sequence`. */
  turns: TimelineTurn[];
  /** Ordered by `start_ms`, then `end_ms`. */
  overlaps: TimelineOverlap[];
  overlap_ms: number;
  unknown_speech_ms: number;
  confidence: { origin: "asr"; calibrated: false };
  correction: { base_revision_id: string | null; correctable: boolean; speaker_choices: string[] };
}
