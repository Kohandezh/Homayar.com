/**
 * Test-only playback descriptor answers (GET /meetings/{id}/playback, HOM-247).
 * The media_url points at the workspace-scoped recording route, the way the
 * product API answers; the player resolves it against the API origin.
 */
import type { PlaybackDescriptor, ReportCompleteness } from "@/types/platform";

const COMPLETENESS: ReportCompleteness = {
  is_complete: true,
  notices: [],
  meeting_status: "completed",
  transcription: "complete",
  speaker_attribution: "inferred",
  analysis: "complete",
  evidence: "sufficient",
};

export function playbackDescriptor(overrides: Partial<PlaybackDescriptor> = {}): PlaybackDescriptor {
  return {
    meeting_id: "m-1",
    media_url: "/api/v1/workspaces/ws-1/meetings/m-1/recording",
    url: "/api/v1/workspaces/ws-1/meetings/m-1/recording#t=0,61",
    start_ms: 0,
    end_ms: 61_000,
    duration_ms: 61_000,
    content_type: "audio/mpeg",
    size_bytes: 123_456,
    accept_ranges: "bytes",
    completeness: COMPLETENESS,
    privacy_enforced: false,
    audio_redacted: false,
    ...overrides,
  };
}
