import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import api from "@/lib/api";
import MeetingDetailPage from "@/components/platform/MeetingDetailPage";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { MeetingDetail } from "@/types/platform";

const T = "2026-09-24T12:00:00Z";

const MEETING: MeetingDetail = {
  id: "m-1",
  workspace_id: "ws-1",
  title: "Call",
  language: "fa",
  status: "processing",
  duration_seconds: 60,
  source: "file_upload",
  created_at: T,
  updated_at: T,
  recording: {
    id: "rec-1",
    original_filename: "call.wav",
    media_type: "audio/wav",
    container_format: "wav",
    size_bytes: 1000,
    checksum_sha256: "0".repeat(64),
    duration_seconds: 60,
    channel_count: 1,
    sample_rate_hz: 16000,
  },
  latest_job: null,
  quality: null,
};

function renderDetail(route: string, meeting: MeetingDetail = MEETING) {
  vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url === "/meetings/m-1") return okResponse(meeting);
    throw apiError(404, "not_found");
  });
  return renderConsole(
    <Routes>
      <Route path="meetings/:meetingId" element={<MeetingDetailPage />} />
    </Routes>,
    { route, lang: "en", auth: { status: "authenticated", user: FIXTURE_USER, workspace: FIXTURE_WORKSPACE, workspaces: [FIXTURE_WORKSPACE] } },
  );
}

describe("MeetingDetailPage playback and ?at=", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("plays through the workspace-scoped route with no credential in the URL (gap G11)", async () => {
    renderDetail("/meetings/m-1");
    const audio = (await screen.findByLabelText("Recording", { selector: "audio" })) as HTMLAudioElement;
    expect(audio.getAttribute("src")).toBe("/api/v1/workspaces/ws-1/meetings/m-1/recording");
    expect(audio.getAttribute("src")).not.toMatch(/token|key|sig/i);
    expect(audio).toHaveAttribute("preload", "none");
    fireEvent.error(audio);
    expect(screen.getByText(/The recording cannot be played/)).toBeInTheDocument();
  });

  it("cues the player for a valid ?at= and reports an invalid one instead of guessing (QA Q-01)", async () => {
    const first = renderDetail("/meetings/m-1?at=15000");
    expect(await screen.findByText(/Opened at 0:15\./)).toBeInTheDocument();
    expect(screen.getByText(/The player is set to this time/)).toBeInTheDocument();
    expect(screen.getByLabelText("Recording", { selector: "audio" })).toHaveAttribute("preload", "metadata");
    first.unmount();
    vi.restoreAllMocks();

    for (const bad of ["-5", "abc", "999999", "1e3"]) {
      const view = renderDetail(`/meetings/m-1?at=${bad}`);
      expect(await screen.findByText("The requested time is not valid for this meeting; the meeting opens from the start.")).toBeInTheDocument();
      expect(screen.queryByText(/Opened at/)).not.toBeInTheDocument();
      view.unmount();
      vi.restoreAllMocks();
    }
  });

  it("says when a meeting has no playable recording", async () => {
    renderDetail("/meetings/m-1?at=1000", { ...MEETING, recording: null });
    expect(await screen.findByText(/This meeting has no playable recording\./)).toBeInTheDocument();
    expect(screen.queryByLabelText("Recording", { selector: "audio" })).not.toBeInTheDocument();
  });
});
