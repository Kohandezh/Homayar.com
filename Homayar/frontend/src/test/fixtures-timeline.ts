/**
 * Test-only speaker timeline answers (HOM-264, ADR 0069): one revision, three
 * lanes (identified, anonymous without a display name, unknown), four turns of
 * which three carry review reasons, and both overlap kinds so the drawn
 * cross-speaker bands and the ignored same-speaker band are both covered.
 * `m-1` is the meeting id the other fixtures use.
 */
import type {
  SpeakerTimeline,
  TimelineLane,
  TimelineOverlap,
  TimelineRevisionInfo,
  TimelineTurn,
} from "@/types/timeline";

export function timelineRevision(overrides: Partial<TimelineRevisionInfo> = {}): TimelineRevisionInfo {
  return {
    id: "rev-4",
    revision_number: 4,
    source: "asr",
    is_current: true,
    diarization_status: "available",
    diarization_provider: "provider-x",
    is_mock: false,
    ...overrides,
  };
}

export function timelineLane(overrides: Partial<TimelineLane> = {}): TimelineLane {
  return {
    speaker_key: "S1",
    display_name: "Agent",
    kind: "identified",
    identity_type: "inferred",
    speaker_confidence: null,
    turn_count: 2,
    speech_ms: 12_000,
    overlap_ms: 1_000,
    ...overrides,
  };
}

export function timelineTurn(overrides: Partial<TimelineTurn> = {}): TimelineTurn {
  return {
    segment_id: "seg-1",
    sequence: 1,
    speaker_key: "S1",
    start_ms: 0,
    end_ms: 6_000,
    confidence: null,
    attribution: "diarization",
    overlap_ids: [],
    review_reasons: [],
    ...overrides,
  };
}

export function timelineOverlap(overrides: Partial<TimelineOverlap> = {}): TimelineOverlap {
  return {
    index: 0,
    start_ms: 5_000,
    end_ms: 6_000,
    kind: "cross_speaker",
    speaker_keys: ["S1", "SPEAKER_00"],
    segment_ids: ["seg-1", "seg-2"],
    includes_unknown: false,
    ...overrides,
  };
}

export function speakerTimeline(overrides: Partial<SpeakerTimeline> = {}): SpeakerTimeline {
  return {
    meeting_id: "m-1",
    revision: timelineRevision(),
    overlap_basis: "turns",
    recording_duration_ms: 25_000,
    timeline_end_ms: 30_000,
    lanes: [
      timelineLane(),
      timelineLane({
        speaker_key: "SPEAKER_00",
        display_name: null,
        kind: "anonymous",
        identity_type: null,
        turn_count: 1,
        speech_ms: 6_000,
        overlap_ms: 1_500,
      }),
      timelineLane({
        speaker_key: "speaker_unknown",
        display_name: null,
        kind: "unknown",
        identity_type: null,
        turn_count: 1,
        speech_ms: 1_000,
        overlap_ms: 500,
      }),
    ],
    turns: [
      timelineTurn(),
      timelineTurn({
        segment_id: "seg-2",
        sequence: 2,
        speaker_key: "SPEAKER_00",
        start_ms: 5_000,
        end_ms: 11_000,
        overlap_ids: [0],
        review_reasons: ["overlap"],
      }),
      timelineTurn({
        segment_id: "seg-3",
        sequence: 3,
        speaker_key: "speaker_unknown",
        start_ms: 9_000,
        end_ms: 10_000,
        attribution: "none",
        overlap_ids: [2],
        review_reasons: ["unknown_speaker", "overlap"],
      }),
      timelineTurn({
        segment_id: "seg-4",
        sequence: 4,
        speaker_key: "S1",
        start_ms: 20_000,
        end_ms: 26_000,
        confidence: 0.9,
        attribution: "person",
        review_reasons: ["outside_recording"],
      }),
    ],
    overlaps: [
      timelineOverlap(),
      // Same-speaker bands are never drawn (ADR 0069 D3).
      timelineOverlap({
        index: 1,
        start_ms: 20_500,
        end_ms: 21_000,
        kind: "same_speaker",
        speaker_keys: ["S1"],
        segment_ids: ["seg-4"],
        includes_unknown: false,
      }),
      timelineOverlap({
        index: 2,
        start_ms: 9_000,
        end_ms: 10_000,
        speaker_keys: ["SPEAKER_00", "speaker_unknown"],
        segment_ids: ["seg-2", "seg-3"],
        includes_unknown: true,
      }),
    ],
    overlap_ms: 2_000,
    unknown_speech_ms: 1_000,
    confidence: { origin: "asr", calibrated: false },
    correction: { base_revision_id: "rev-4", correctable: true, speaker_choices: ["S1", "SPEAKER_00", "speaker_unknown"] },
    ...overrides,
  };
}
