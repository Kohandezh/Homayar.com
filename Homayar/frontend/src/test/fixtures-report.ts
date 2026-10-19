/**
 * Test-only call report answers (GET /meetings/{id}/report, HOM-59). Every
 * key of the contract is present, as the API always sends them all.
 */
import type {
  CallReportResponse,
  MeetingQuality,
  ReportEvidenceRef,
  ReportFinding,
  ReportReviewAction,
} from "@/types/platform";

export const REPORT_QUALITY: MeetingQuality = {
  transcription: "complete",
  speaker_attribution: "inferred",
  analysis: "complete",
  evidence: "sufficient",
  analysis_stale: false,
  transcript_revision_id: "rev-1",
  analysed_revision_id: "rev-1",
  analysis_run_id: "run-1",
  segment_count: 2,
  speaker_count: 2,
  speech_coverage_ratio: 0.8,
  is_mock: false,
  degradations: [],
  usage: null,
};

export function reportEvidence(overrides: Partial<ReportEvidenceRef> = {}): ReportEvidenceRef {
  return {
    id: "ev-1",
    transcript_revision_id: "rev-1",
    is_current_revision: true,
    segment_id: "seg-2",
    speaker_key: "S2",
    text_start: 0,
    text_end: 9,
    text: "My number",
    start_ms: 5000,
    end_ms: 9000,
    evidence_type: "turn_reference",
    ...overrides,
  };
}

export function reportFinding(overrides: Partial<ReportFinding> = {}): ReportFinding {
  return {
    finding_type: "insight",
    finding_id: null,
    finding_index: 0,
    category: null,
    text: "Customer asked for a callback",
    evidence_status: "sufficient",
    supported: true,
    evidence: [reportEvidence()],
    ...overrides,
  };
}

const ACTION_KEYS = ["correct_transcript", "rename_speaker", "edit_action_items", "share", "export"] as const;

/** All five review actions available, in the API's order; `overrides` replaces single rows by key. */
export function reviewActions(
  overrides: Partial<Record<(typeof ACTION_KEYS)[number], Omit<ReportReviewAction, "action">>> = {},
): ReportReviewAction[] {
  return ACTION_KEYS.map((action) => ({ action, available: true, reason: null, ...overrides[action] }));
}

export function callReport(overrides: Partial<CallReportResponse> = {}): CallReportResponse {
  return {
    meeting_id: "m-1",
    report_version: 1,
    summary: {
      overview: "Follow up with the customer.",
      evidence_status: "sufficient",
      supported: true,
      evidence: [reportEvidence()],
      key_points: [],
      topics: ["callback"],
      sentiment: null,
      analysis_run_id: "run-1",
      provider: "p",
      model: "m",
    },
    outcome: {
      state: "explicit",
      decisions: [
        {
          text: "Call back tomorrow",
          key_point_index: null,
          confidence: null,
          evidence_status: "sufficient",
          supported: true,
          evidence: [reportEvidence()],
        },
      ],
    },
    qa: {
      quality: REPORT_QUALITY,
      findings: [reportFinding()],
      counts: { total: 1, supported: 1, unsupported: 0, sufficient: 1, partial: 0, insufficient: 0 },
    },
    review_actions: reviewActions(),
    completeness: {
      is_complete: true,
      notices: [],
      meeting_status: "completed",
      transcription: "complete",
      speaker_attribution: "inferred",
      analysis: "complete",
      evidence: "sufficient",
    },
    view: "original",
    privacy_enforced: false,
    redaction: null,
    ...overrides,
  };
}
