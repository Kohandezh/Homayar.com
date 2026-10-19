/**
 * Test-only transcript revision answers (HOM-246): a transcript with its
 * revision, a revision list, an older read-only revision and the members the
 * "who" column is resolved against. `user-1` is FIXTURE_USER in fixtures.tsx.
 */
import type {
  MemberListResponse,
  Transcript,
  TranscriptRevision,
  TranscriptRevisionListResponse,
} from "@/types/platform";

const T = "2026-09-24T12:00:00Z";

export const CURRENT_REVISION_ID = "rev-4";
export const CURRENT_REVISION_NUMBER = 4;
export const OLD_REVISION_ID = "rev-3";

export function transcriptRevision(overrides: Partial<TranscriptRevision> = {}): TranscriptRevision {
  return {
    id: CURRENT_REVISION_ID,
    meeting_id: "m-1",
    revision_number: CURRENT_REVISION_NUMBER,
    parent_revision_id: OLD_REVISION_ID,
    source: "correction",
    // An internal code the UI must never show as the person's reason.
    reason: "manual",
    note: "Fixed the company name",
    language: "en",
    segment_count: 2,
    created_at: T,
    created_by_user_id: "user-1",
    asr_provider: null,
    asr_model: null,
    diarization_status: null,
    is_mock: false,
    is_current: true,
    superseded_at: null,
    ...overrides,
  };
}

/** The current revision, made by the signed-in user ("You"). */
export function currentRevision(overrides: Partial<TranscriptRevision> = {}): TranscriptRevision {
  return transcriptRevision(overrides);
}

/** The machine revision below it, authored by the system. */
export function machineRevision(): TranscriptRevision {
  return transcriptRevision({
    id: OLD_REVISION_ID,
    revision_number: 3,
    parent_revision_id: "rev-2",
    source: "asr",
    reason: "initial",
    note: null,
    created_at: "2026-09-24T11:00:00Z",
    created_by_user_id: null,
    is_current: false,
    superseded_at: T,
  });
}

/** A backfill revision by another member, resolvable only through /members. */
export function memberRevision(): TranscriptRevision {
  return transcriptRevision({
    id: "rev-2",
    revision_number: 2,
    parent_revision_id: "rev-1",
    source: "backfill",
    reason: "rebuild",
    note: null,
    created_at: "2026-09-24T10:00:00Z",
    created_by_user_id: "user-2",
    is_current: false,
    superseded_at: "2026-09-24T11:00:00Z",
  });
}

export function transcriptRevisions(
  overrides: Partial<TranscriptRevisionListResponse> = {},
): TranscriptRevisionListResponse {
  return {
    meeting_id: "m-1",
    current_revision_id: CURRENT_REVISION_ID,
    // Newest first, the API's order.
    items: [currentRevision(), machineRevision(), memberRevision()],
    ...overrides,
  };
}

function segments(first: string, firstId = "seg-1") {
  return [
    {
      id: firstId,
      sequence: 1,
      speaker_key: "S1",
      speaker_name: "Agent",
      start_ms: 0,
      end_ms: 4000,
      text: first,
      confidence: null,
      language: "en",
    },
    {
      id: "seg-2",
      sequence: 2,
      speaker_key: "S2",
      speaker_name: "Customer",
      start_ms: 5000,
      end_ms: 9000,
      text: "Second line of the call",
      confidence: null,
      language: "en",
    },
  ];
}

/** The current revision of the transcript, the answer to a plain GET. */
export function transcriptWithRevision(overrides: Partial<Transcript> = {}): Transcript {
  return {
    meeting_id: "m-1",
    language: "en",
    revision: currentRevision(),
    segments: segments("First line of the call"),
    speakers: [
      { id: "sp-1", speaker_key: "S1", display_name: "Agent", identity_type: "inferred", confidence: null },
      { id: "sp-2", speaker_key: "S2", display_name: "Customer", identity_type: "inferred", confidence: null },
    ],
    view: "original",
    privacy_enforced: false,
    redaction: null,
    ...overrides,
  };
}

/** The answer a `revision_id` GET gives: the same meeting, older text. */
export function oldTranscript(overrides: Partial<Transcript> = {}): Transcript {
  return transcriptWithRevision({
    revision: machineRevision(),
    segments: segments("Original first line"),
    ...overrides,
  });
}

/** The answer after one more correction: revision 5 is now current. */
export function correctedTranscript(overrides: Partial<Transcript> = {}): Transcript {
  return transcriptWithRevision({
    revision: currentRevision({
      id: "rev-5",
      revision_number: 5,
      parent_revision_id: CURRENT_REVISION_ID,
      note: "Fix a typo",
      created_by_user_id: "user-1",
    }),
    // A new revision gives every segment a new id; only `sequence` is stable.
    segments: segments("First line, corrected", "seg-1b"),
    ...overrides,
  });
}

export function memberList(): MemberListResponse {
  return {
    items: [
      {
        id: "mem-1",
        user_id: "user-1",
        email: "person@example.com",
        phone_e164: null,
        display_name: "Person",
        role: "owner",
        joined_at: T,
      },
      {
        id: "mem-2",
        user_id: "user-2",
        email: "other@example.com",
        phone_e164: null,
        display_name: "Second Member",
        role: "member",
        joined_at: T,
      },
    ],
    total: 2,
  };
}
