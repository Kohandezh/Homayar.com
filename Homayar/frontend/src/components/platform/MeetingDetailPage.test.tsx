import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes, useLocation } from "react-router";
import api from "@/lib/api";
import MeetingDetailPage from "@/components/platform/MeetingDetailPage";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import { callReport, reviewActions } from "@/test/fixtures-report";
import { playbackDescriptor } from "@/test/fixtures-playback";
import type { CallReportResponse, MeetingDetail, Transcript } from "@/types/platform";

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

const COMPLETED: MeetingDetail = { ...MEETING, status: "completed" };

const TRANSCRIPT: Transcript = {
  meeting_id: "m-1",
  language: "en",
  speakers: [{ id: "sp-2", speaker_key: "S2", display_name: "Customer", identity_type: "inferred", confidence: null }],
  segments: [
    { id: "seg-2", sequence: 1, speaker_key: "S2", speaker_name: "Customer", start_ms: 5000, end_ms: 9000, text: "My number", confidence: null, language: "en" },
  ],
};

/** Shows the router location, so a test can read what the page wrote to the URL and history entry. */
function LocationProbe() {
  const location = useLocation();
  return <output aria-label="location" data-state={JSON.stringify(location.state ?? null)}>{location.search}</output>;
}

function renderDetail(
  route: string | { pathname: string; search?: string; state?: unknown },
  meeting: MeetingDetail = MEETING,
  { report = () => okResponse(callReport()), lang = "en" }: { report?: () => unknown; lang?: "fa" | "en" } = {},
) {
  const get = vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url === "/meetings/m-1") return okResponse(meeting);
    if (url === "/meetings/m-1/report") return report() as never;
    // HOM-247: the player plays only after the playback descriptor answers.
    if (url === "/meetings/m-1/playback") return okResponse(playbackDescriptor());
    if (url === "/meetings/m-1/transcript") return okResponse(TRANSCRIPT);
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

const before = (first: Element, second: Element) =>
  Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);

describe("MeetingDetailPage playback and ?at=", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("plays through the workspace-scoped route with no credential in the URL (gap G11)", async () => {
    renderDetail("/meetings/m-1");
    const audio = (await screen.findByLabelText("Recording", { selector: "audio" })) as HTMLAudioElement;
    // SPEC Amendment A1: the descriptor's media_url, resolved against the API origin.
    expect(audio.getAttribute("src")).toBe("http://localhost:3000/api/v1/workspaces/ws-1/meetings/m-1/recording");
    expect(audio.getAttribute("src")).not.toMatch(/token|key|sig/i);
    expect(audio).toHaveAttribute("preload", "none");
    fireEvent.error(audio);
    expect(screen.getByText(/The recording cannot be played/)).toBeInTheDocument();
  });

  it("cues the player for a valid ?at= and reports an invalid one instead of guessing (QA Q-01)", async () => {
    const first = renderDetail("/meetings/m-1?at=15000");
    expect(await screen.findByText(/Opened at 0:15\./)).toBeInTheDocument();
    // HOM-247 turn 2, item 4: "Opened at" shows at once; this ending follows when the playback descriptor answers.
    expect(await screen.findByText(/The player is set to this time/)).toBeInTheDocument();
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
    // The report still quotes the evidence, with nothing to play.
    expect(await screen.findByRole("region", { name: "QA findings" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Play evidence at/ })).not.toBeInTheDocument();
  });
});

describe("MeetingDetailPage call report (HOM-245)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("asks for the report for a meeting that is not done yet", async () => {
    const report = callReport({
      summary: null,
      outcome: { state: "unavailable", decisions: [] },
      completeness: { ...callReport().completeness, is_complete: false, notices: ["processing_in_progress"], meeting_status: "queued" },
    });
    const { get } = renderDetail("/meetings/m-1", { ...MEETING, status: "queued" }, { report: () => okResponse(report) });
    const notices = await screen.findByRole("region", { name: "Report completeness" });
    expect(within(notices).getByText("Processing is still running. This report is not final.")).toBeInTheDocument();
    expect(get.mock.calls.some(([url]) => url === "/meetings/m-1/report")).toBe(true);
    // The pipeline view stays, below the summary.
    expect(before(screen.getByRole("region", { name: "Summary" }), screen.getByRole("region", { name: "Processing" }))).toBe(true);
    // No results yet: the advanced area holds only the owner's data panel, no transcript tabs.
    fireEvent.click(screen.getByRole("button", { name: "Transcript and tools" }));
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(get.mock.calls.some(([url]) => url === "/meetings/m-1/transcript")).toBe(false);
  });

  it("shows a loading state, then an error state that keeps the header, facts and player", async () => {
    let fail: (error: unknown) => void = () => undefined;
    const pending = new Promise((_resolve, reject) => { fail = reject; });
    renderDetail("/meetings/m-1", COMPLETED, { report: () => pending });
    expect(await screen.findByText("Loading the call report")).toBeInTheDocument();
    fail(apiError(500, "internal_error"));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Call" })).toBeInTheDocument();
    expect(screen.getByText("Duration")).toBeInTheDocument();
    expect(screen.getByLabelText("Recording", { selector: "audio" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Summary" })).not.toBeInTheDocument();
  });

  it("shows the error state, not a crash, for a body that is not a report", async () => {
    renderDetail("/meetings/m-1", COMPLETED, { report: () => okResponse("<html>proxy page</html>") });
    expect(await screen.findByText("The call report could not be loaded.", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Call" })).toBeInTheDocument();
  });

  it("puts the report after the player and before the closed advanced area", async () => {
    renderDetail("/meetings/m-1", COMPLETED, {
      report: () => okResponse(callReport({ completeness: { ...callReport().completeness, is_complete: false, notices: ["mock_analysis"] } })),
    });
    const sections = ["Report completeness", "Summary", "Outcome", "QA findings", "Review actions"];
    await screen.findByRole("region", { name: "Summary" });
    const ordered = [
      screen.getByRole("region", { name: "Recording" }),
      ...sections.map((name) => screen.getByRole("region", { name })),
      screen.getByRole("button", { name: "Transcript and tools" }),
    ];
    for (let index = 1; index < ordered.length; index += 1) expect(before(ordered[index - 1], ordered[index])).toBe(true);
    // The top-level quality panel is gone: the only one is inside QA findings.
    expect(screen.getAllByRole("region", { name: "Quality and coverage" })).toHaveLength(1);
    expect(within(screen.getByRole("region", { name: "QA findings" })).getByRole("region", { name: "Quality and coverage" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Transcript and tools" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByText("My number", { selector: ".meeting-segment p" })).not.toBeInTheDocument();
  });

  it("plays evidence from its start, writes ?at= and leaves the advanced area closed", async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    renderDetail("/meetings/m-1", COMPLETED);
    const qa = await screen.findByRole("region", { name: "QA findings" });
    const button = within(qa).getByRole("button", { name: "Play evidence at 0:05" });
    button.focus();
    fireEvent.click(button);
    const audio = screen.getByLabelText("Recording", { selector: "audio" }) as HTMLAudioElement;
    expect(audio.currentTime).toBe(5);
    expect(play).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByRole("status", { name: "location" })).toHaveTextContent("?at=5000"));
    expect(screen.getByRole("button", { name: "Transcript and tools" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "QA findings" })).getByRole("button", { name: "Play evidence at 0:05" })).toHaveFocus();
  });

  it("opens the transcript for a played ?at= after the page is opened again", async () => {
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const first = renderDetail("/meetings/m-1", COMPLETED);
    fireEvent.click(within(await screen.findByRole("region", { name: "QA findings" })).getByRole("button", { name: "Play evidence at 0:05" }));
    await waitFor(() => expect(screen.getByRole("status", { name: "location" })).toHaveTextContent("?at=5000"));
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    // A reload or Back restores the history entry as the click left it, state included.
    const state: unknown = JSON.parse(screen.getByRole("status", { name: "location" }).dataset.state ?? "null");
    first.unmount();
    vi.restoreAllMocks();

    renderDetail({ pathname: "/meetings/m-1", search: "?at=5000", state }, COMPLETED);
    expect(await screen.findByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(document.getElementById("segment-seg-2")).toHaveFocus());
  });

  it("starts on the first tab again after the advanced area was closed", async () => {
    renderDetail("/meetings/m-1", COMPLETED);
    const actions = await screen.findByRole("region", { name: "Review actions" });
    fireEvent.click(within(actions).getByRole("button", { name: "Edit action items" }));
    expect(await screen.findByRole("tab", { name: "Action items" })).toHaveAttribute("aria-selected", "true");
    const trigger = screen.getByRole("button", { name: "Transcript and tools" });
    fireEvent.click(trigger);
    await waitFor(() => expect(screen.queryByRole("tablist")).not.toBeInTheDocument());
    fireEvent.click(trigger);
    expect(await screen.findByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Action items" })).toHaveAttribute("aria-selected", "false");
  });

  it("explains an available review action whose panel needs results", async () => {
    const report = callReport({
      completeness: { ...callReport().completeness, is_complete: false, notices: ["processing_failed"], meeting_status: "failed" },
    });
    renderDetail("/meetings/m-1", { ...MEETING, status: "failed" }, { report: () => okResponse(report) });
    const actions = await screen.findByRole("region", { name: "Review actions" });
    const button = within(actions).getByRole("button", { name: "Edit action items" });
    expect(button).toBeEnabled();
    expect(within(actions).queryByText("This opens when the meeting has results.")).not.toBeInTheDocument();
    fireEvent.click(button);
    expect(within(actions).getByText("This opens when the meeting has results.")).toBeInTheDocument();
    // The owner's advanced area (data panel only) stays closed, and no tab appears.
    expect(screen.getByRole("button", { name: "Transcript and tools" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it("opens the transcript for a ?at= link and keeps the report above it", async () => {
    renderDetail("/meetings/m-1?at=6000", COMPLETED);
    expect(await screen.findByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(document.getElementById("segment-seg-2")).toHaveFocus());
    expect(before(screen.getByRole("region", { name: "Review actions" }), screen.getByRole("tablist", { name: "Meeting results" }))).toBe(true);
  });

  it("opens the panel of each available review action", async () => {
    renderDetail("/meetings/m-1", COMPLETED);
    const actions = await screen.findByRole("region", { name: "Review actions" });
    const panels: [string, string][] = [
      ["Edit action items", "Action items"],
      ["Share", "Share links"],
      ["Export", "Export"],
      ["Correct transcript", "Transcript"],
      ["Rename speakers", "Transcript"],
    ];
    for (const [action, tab] of panels) {
      fireEvent.click(within(actions).getByRole("button", { name: action }));
      expect(await screen.findByRole("tab", { name: tab })).toHaveAttribute("aria-selected", "true");
      await waitFor(() => expect(screen.getByRole("tab", { name: tab })).toHaveFocus());
    }
  });

  it("keeps a review action disabled for an owner when the report says no", async () => {
    const report: CallReportResponse = callReport({
      review_actions: reviewActions({ correct_transcript: { available: false, reason: "permission_denied" } }),
    });
    renderDetail("/meetings/m-1", COMPLETED, { report: () => okResponse(report) });
    const actions = await screen.findByRole("region", { name: "Review actions" });
    const button = within(actions).getByRole("button", { name: "Correct transcript" });
    expect(button).toBeDisabled();
    expect(within(actions).getByText("Your role cannot do this.")).toBeInTheDocument();
    fireEvent.click(button);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it("shows the Persian headings on the right-to-left page", async () => {
    renderDetail("/meetings/m-1", COMPLETED, { lang: "fa" });
    expect(await screen.findByRole("region", { name: "خلاصه" })).toBeInTheDocument();
    for (const name of ["نتیجه", "یافته‌های کنترل کیفیت", "اقدام‌های بازبینی"]) {
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
    }
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
  });
});
