import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes, useLocation } from "react-router";
import api, { API_BASE_URL } from "@/lib/api";
import { platformQueryKeys } from "@/lib/platform-api";
import MeetingDetailPage from "@/components/platform/MeetingDetailPage";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import { callReport } from "@/test/fixtures-report";
import { playbackDescriptor } from "@/test/fixtures-playback";
import { transcriptWithRevision } from "@/test/fixtures-transcript";
import type { MeetingDetail, Transcript } from "@/types/platform";

const T = "2026-09-24T12:00:00Z";

const MEETING: MeetingDetail = {
  id: "m-1",
  workspace_id: "ws-1",
  title: "Call",
  language: "fa",
  status: "completed",
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

/** Shows the router location, so a test can read what the page wrote to the URL. */
function LocationProbe() {
  const location = useLocation();
  return <output aria-label="location">{location.search}</output>;
}

/** The absolute URL SPEC 2.2 resolves for the descriptor's media_url. */
function resolvedMediaUrl(mediaUrl: string): string {
  return new URL(mediaUrl, new URL(API_BASE_URL, window.location.origin)).toString();
}

function renderDetail(
  route = "/meetings/m-1",
  {
    meeting = MEETING,
    getMeeting = () => meeting,
    playback = () => okResponse(playbackDescriptor()),
    transcript = transcriptWithRevision(),
    lang = "en",
  }: {
    meeting?: MeetingDetail;
    /** Answers each meeting read; a test can change the status between reads. */
    getMeeting?: () => MeetingDetail;
    playback?: () => unknown;
    transcript?: Transcript;
    lang?: "fa" | "en";
  } = {},
) {
  const get = vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url === "/meetings/m-1") return okResponse(getMeeting());
    if (url === "/meetings/m-1/playback") return playback() as never;
    if (url === "/meetings/m-1/report") return okResponse(callReport());
    if (url === "/meetings/m-1/transcript") return okResponse(transcript);
    if (url === "/meetings/m-1/actions") return okResponse([]);
    if (url === "/meetings/m-1/shares") return okResponse([]);
    throw apiError(404, "not_found");
  });
  const view = renderConsole(
    <Routes>
      <Route path="meetings/:meetingId" element={<><MeetingDetailPage /><LocationProbe /></>} />
    </Routes>,
    { route, lang, auth: { status: "authenticated", user: FIXTURE_USER, workspace: FIXTURE_WORKSPACE, workspaces: [FIXTURE_WORKSPACE] } },
  );
  return { ...view, get };
}

const SHORTCUTS: [string, string][] = [
  ["K", "Play or pause"],
  ["J", "Back 5 seconds"],
  ["L", "Forward 5 seconds"],
  ["N", "Next turn"],
  ["P", "Previous turn"],
];

describe("MeetingDetailPage playback descriptor (HOM-247)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("requests the playback descriptor once and plays the resolved media_url", async () => {
    const { get } = renderDetail();
    const audio = (await screen.findByLabelText("Recording", { selector: "audio" })) as HTMLAudioElement;
    await waitFor(() => {
      expect(get.mock.calls.filter(([url]) => url === "/meetings/m-1/playback")).toHaveLength(1);
    });
    const descriptor = playbackDescriptor();
    expect(audio.getAttribute("src")).toBe(resolvedMediaUrl(descriptor.media_url));
    // The fragment-bearing `url` field is never the element source.
    expect(audio.getAttribute("src")).not.toContain("#");
  });

  it("shows a loading state before the descriptor resolves, and no audio meanwhile", async () => {
    let resolvePlayback: (value: unknown) => void = () => undefined;
    const pending = new Promise((resolve) => { resolvePlayback = resolve; });
    renderDetail("/meetings/m-1", { playback: () => pending });
    const loading = await screen.findByText("Loading the recording");
    expect(loading.closest('[role="status"]')).not.toBeNull();
    expect(screen.queryByLabelText("Recording", { selector: "audio" })).not.toBeInTheDocument();
    resolvePlayback(okResponse(playbackDescriptor()));
    expect(await screen.findByLabelText("Recording", { selector: "audio" })).toBeInTheDocument();
  });

  it("shows the retention message for a 410, with no audio and no evidence play buttons", async () => {
    renderDetail("/meetings/m-1?at=5000", { playback: () => { throw apiError(410, "recording_retention_expired"); } });
    const status = await screen.findByText("This recording is no longer available. The workspace retention period has passed.");
    expect(status.closest('[role="status"]')).not.toBeNull();
    expect(screen.queryByLabelText("Recording", { selector: "audio" })).not.toBeInTheDocument();
    const qa = await screen.findByRole("region", { name: "QA findings" });
    expect(within(qa).queryByRole("button", { name: /Play evidence at/ })).not.toBeInTheDocument();
    // The page does not crash, and ?at= falls back to the no-recording wording.
    expect(screen.getByRole("region", { name: "Summary" })).toBeInTheDocument();
    expect(screen.getByText(/This meeting has no playable recording\./)).toBeInTheDocument();
    expect(screen.queryByText(/The player is set to this time/)).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Keyboard shortcuts" })).not.toBeInTheDocument();
  });

  it("shows the deletion message for a 404, with no audio", async () => {
    renderDetail("/meetings/m-1", { playback: () => { throw apiError(404, "recording_not_found"); } });
    const status = await screen.findByText("This recording is being deleted and can no longer be played.");
    expect(status.closest('[role="status"]')).not.toBeNull();
    expect(screen.queryByLabelText("Recording", { selector: "audio" })).not.toBeInTheDocument();
    const qa = await screen.findByRole("region", { name: "QA findings" });
    expect(within(qa).queryByRole("button", { name: /Play evidence at/ })).not.toBeInTheDocument();
  });

  it("keeps the existing wording for any other playback error", async () => {
    renderDetail("/meetings/m-1", { playback: () => { throw apiError(500, "internal_error"); } });
    await screen.findByRole("region", { name: "Summary" });
    expect(screen.getByText(/The recording cannot be played/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Recording", { selector: "audio" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Keyboard shortcuts" })).not.toBeInTheDocument();
  });

  it("lists the descriptor's incomplete notices with their report labels", async () => {
    const incomplete = () =>
      okResponse(
        playbackDescriptor({
          completeness: {
            ...playbackDescriptor().completeness,
            is_complete: false,
            notices: ["processing_in_progress", "a_new_code"],
          },
        }),
      );
    renderDetail("/meetings/m-1", { playback: incomplete });
    const notices = await screen.findByRole("list", { name: "Recording and transcript status" });
    expect(within(notices).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Processing is still running. This report is not final.",
      "a_new_code",
    ]);
  });

  it("lists nothing when the descriptor says the recording is complete", async () => {
    renderDetail();
    await screen.findByLabelText("Recording", { selector: "audio" });
    expect(screen.queryByRole("list", { name: "Recording and transcript status" })).not.toBeInTheDocument();
  });

  it("shows the keyboard shortcut list under the player", async () => {
    renderDetail();
    const shortcuts = await screen.findByRole("region", { name: "Keyboard shortcuts" });
    const player = screen.getByRole("region", { name: "Recording" });
    expect(player.compareDocumentPosition(shortcuts) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    for (const [key, label] of SHORTCUTS) {
      expect(within(shortcuts).getByText(label)).toBeInTheDocument();
      expect(within(shortcuts).getByText(key, { exact: true }).tagName).toBe("KBD");
    }
  });

  it("shows the shortcut list in Persian on the right-to-left page", async () => {
    renderDetail("/meetings/m-1", { lang: "fa" });
    const shortcuts = await screen.findByRole("region", { name: "کلیدهای میان‌بر" });
    for (const label of ["پخش یا توقف", "۵ ثانیه عقب", "۵ ثانیه جلو", "بخش بعدی", "بخش قبلی"]) {
      expect(within(shortcuts).getByText(label)).toBeInTheDocument();
    }
    for (const key of ["K", "J", "L", "N", "P"]) {
      expect(within(shortcuts).getByText(key, { exact: true })).toHaveAttribute("dir", "ltr");
    }
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
  });
});

describe("MeetingDetailPage keyboard shortcuts (HOM-247)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  async function renderWithAudio(options: Parameters<typeof renderDetail>[1] = {}) {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
    let paused = true;
    vi.spyOn(HTMLMediaElement.prototype, "paused", "get").mockImplementation(() => paused);
    const view = renderDetail("/meetings/m-1", options);
    const audio = (await screen.findByLabelText("Recording", { selector: "audio" })) as HTMLAudioElement;
    return {
      ...view,
      audio,
      play,
      pause,
      setPaused: (value: boolean) => { paused = value; },
    };
  }

  it("K toggles play and pause", async () => {
    const { audio, play, pause, setPaused } = await renderWithAudio();
    fireEvent.keyDown(document.body, { code: "KeyK" });
    expect(play).toHaveBeenCalledTimes(1);
    setPaused(false);
    fireEvent.keyDown(document.body, { code: "KeyK" });
    expect(pause).toHaveBeenCalledTimes(1);
    expect(play).toHaveBeenCalledTimes(1);
    expect(audio.paused).toBe(false);
  });

  it("J and L seek by five seconds, clamped to the recording's duration", async () => {
    const { audio } = await renderWithAudio();
    audio.currentTime = 10;
    fireEvent.keyDown(document.body, { code: "KeyJ" });
    expect(audio.currentTime).toBe(5);
    // The descriptor says 61 s; a seek past it is clamped, not overshot.
    audio.currentTime = 59;
    fireEvent.keyDown(document.body, { code: "KeyL" });
    expect(audio.currentTime).toBe(61);
    audio.currentTime = 2;
    fireEvent.keyDown(document.body, { code: "KeyJ" });
    expect(audio.currentTime).toBe(0);
  });

  it("ignores K with Ctrl held and while typing in an input", async () => {
    const { play } = await renderWithAudio();
    fireEvent.keyDown(document.body, { code: "KeyK", ctrlKey: true });
    fireEvent.keyDown(document.body, { code: "KeyK", metaKey: true });
    fireEvent.keyDown(document.body, { code: "KeyK", altKey: true });
    expect(play).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Rename meeting" }));
    const input = screen.getByLabelText("Meeting title");
    fireEvent.keyDown(input, { code: "KeyK" });
    expect(play).not.toHaveBeenCalled();
  });

  it("ignores K when the player is not available", async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    renderDetail("/meetings/m-1", { playback: () => { throw apiError(404, "recording_not_found"); } });
    await screen.findByText(/This recording is being deleted/);
    fireEvent.keyDown(document.body, { code: "KeyK" });
    expect(play).not.toHaveBeenCalled();
  });

  it("marks the transcript turn at the audio's time after a seek", async () => {
    const { audio } = await renderWithAudio();
    fireEvent.click(screen.getByRole("button", { name: "Transcript and tools" }));
    await screen.findByText("First line of the call");
    // Before any play or seek nothing is the current turn.
    expect(document.querySelectorAll(".meeting-segment[aria-current]").length).toBe(0);
    audio.currentTime = 6;
    fireEvent.seeked(audio);
    await waitFor(() => {
      expect(document.getElementById("segment-seg-2")).toHaveAttribute("aria-current", "true");
    });
    expect(document.getElementById("segment-seg-2")).toHaveClass("is-playing");
    expect(document.getElementById("segment-seg-1")).not.toHaveAttribute("aria-current");
    // A later time moves the mark.
    audio.currentTime = 2;
    fireEvent.seeked(audio);
    await waitFor(() => {
      expect(document.getElementById("segment-seg-1")).toHaveAttribute("aria-current", "true");
    });
    expect(document.getElementById("segment-seg-2")).not.toHaveAttribute("aria-current");
  });

  it("plays from a transcript turn: seeks, plays and writes ?at= without reopening or refocusing", async () => {
    const { audio, play } = await renderWithAudio();
    fireEvent.click(screen.getByRole("button", { name: "Transcript and tools" }));
    await screen.findByText("First line of the call");
    const playButton = screen.getByRole("button", { name: "Play from 0:05" });
    playButton.focus();
    fireEvent.click(playButton);
    expect(audio.currentTime).toBe(5);
    expect(play).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByLabelText("location")).toHaveTextContent("?at=5000"));
    // A play is not a navigation: no "Showing the transcript segment" note, focus stays on the button.
    expect(screen.queryByText(/Showing the transcript segment at/)).not.toBeInTheDocument();
    expect(document.activeElement).toBe(playButton);

    // A click on a turn's text plays from that turn's start too.
    fireEvent.click(screen.getByText("First line of the call"));
    expect(audio.currentTime).toBe(0);
    expect(play).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(screen.getByLabelText("location")).toHaveTextContent("?at=0"));
  });

  it("N reads the audio's live time, so two quick presses walk two turns (turn 2, item 3)", async () => {
    const base = transcriptWithRevision();
    const third = { ...base.segments[1], id: "seg-3", sequence: 3, start_ms: 10_000, end_ms: 14_000, text: "Third line" };
    const { audio } = await renderWithAudio({ transcript: { ...base, segments: [...base.segments, third] } });
    fireEvent.click(screen.getByRole("button", { name: "Transcript and tools" }));
    await screen.findByText("Third line");
    audio.currentTime = 3;
    fireEvent.seeked(audio);
    await waitFor(() => expect(document.getElementById("segment-seg-1")).toHaveAttribute("aria-current", "true"));
    fireEvent.keyDown(document.body, { code: "KeyN" });
    expect(audio.currentTime).toBe(5);
    // No media event in between: the second N still knows the audio is at 5 s.
    fireEvent.keyDown(document.body, { code: "KeyN" });
    expect(audio.currentTime).toBe(10);
  });
});

describe("MeetingDetailPage playback descriptor refetches (HOM-247, turn 2)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("keeps a loaded player when a background refetch fails (item 2)", async () => {
    let answer: () => unknown = () => okResponse(playbackDescriptor());
    const { queryClient } = renderDetail("/meetings/m-1", { playback: () => answer() });
    const audio = await screen.findByLabelText("Recording", { selector: "audio" });
    answer = () => { throw apiError(503, "service_unavailable"); };
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: platformQueryKeys.playback("m-1") });
      // React Query tells the page on a later tick; let that render happen.
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(queryClient.getQueryState(platformQueryKeys.playback("m-1"))?.status).toBe("error");
    expect(screen.getByLabelText("Recording", { selector: "audio" })).toBe(audio);
    expect(screen.queryByText(/The recording cannot be played/)).not.toBeInTheDocument();
  });

  it("removes the player when a refetch says the retention passed (item 2 control)", async () => {
    let answer: () => unknown = () => okResponse(playbackDescriptor());
    const { queryClient } = renderDetail("/meetings/m-1", { playback: () => answer() });
    await screen.findByLabelText("Recording", { selector: "audio" });
    answer = () => { throw apiError(410, "recording_retention_expired"); };
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: platformQueryKeys.playback("m-1") });
      // React Query tells the page on a later tick; let that render happen.
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(await screen.findByText("This recording is no longer available. The workspace retention period has passed.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Recording", { selector: "audio" })).not.toBeInTheDocument();
  });

  it("shows Opened at m:ss while the descriptor is still loading (item 4)", async () => {
    renderDetail("/meetings/m-1?at=15000", { playback: () => new Promise(() => undefined) });
    expect(await screen.findByText(/Opened at 0:15\./)).toBeInTheDocument();
    expect(screen.getByText("Loading the recording")).toBeInTheDocument();
    // Neither ending yet: it is not known whether the player holds this time.
    expect(screen.queryByText(/The player is set to this time/)).not.toBeInTheDocument();
    expect(screen.queryByText(/This meeting has no playable recording/)).not.toBeInTheDocument();
  });

  it("asks for the descriptor again when the meeting status changes (item 5)", async () => {
    let meeting: MeetingDetail = { ...MEETING, status: "processing" };
    const { get, queryClient } = renderDetail("/meetings/m-1", { getMeeting: () => meeting });
    await screen.findByLabelText("Recording", { selector: "audio" });
    const playbackReads = () => get.mock.calls.filter(([url]) => url === "/meetings/m-1/playback").length;
    expect(playbackReads()).toBe(1);
    meeting = { ...MEETING, status: "completed" };
    await act(async () => {
      // Only the meeting itself, as its poll would: not the prefix of every meeting query.
      await queryClient.refetchQueries({ queryKey: platformQueryKeys.meeting("m-1"), exact: true });
    });
    await waitFor(() => expect(playbackReads()).toBe(2));
  });
});
