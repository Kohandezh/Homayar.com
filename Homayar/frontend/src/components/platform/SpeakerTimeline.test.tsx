import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes, useLocation } from "react-router";
import api from "@/lib/api";
import { platformQueryKeys } from "@/lib/platform-api";
import MeetingDetailPage from "@/components/platform/MeetingDetailPage";
import {
  FIXTURE_USER,
  FIXTURE_WORKSPACE,
  apiError,
  okResponse,
  renderConsole,
  testQueryClient,
} from "@/test/fixtures";
import { callReport } from "@/test/fixtures-report";
import { playbackDescriptor } from "@/test/fixtures-playback";
import { correctedTranscript, transcriptWithRevision } from "@/test/fixtures-transcript";
import { speakerTimeline, timelineRevision, timelineTurn } from "@/test/fixtures-timeline";
import type { MeetingDetail } from "@/types/platform";

const T = "2026-09-24T12:00:00Z";

const MEETING: MeetingDetail = {
  id: "m-1",
  workspace_id: "ws-1",
  title: "Call",
  language: "en",
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

function renderDetail(
  route = "/meetings/m-1",
  meeting: MeetingDetail = MEETING,
  {
    report = () => okResponse(callReport()),
    timeline = () => okResponse(speakerTimeline()),
    lang = "en",
    role = "owner",
    queryClient,
  }: {
    report?: () => unknown;
    timeline?: () => unknown;
    lang?: "fa" | "en";
    role?: "owner" | "member" | "viewer";
    queryClient?: ReturnType<typeof testQueryClient>;
  } = {},
) {
  const workspace = { ...FIXTURE_WORKSPACE, role };
  const get = vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url === "/meetings/m-1") return okResponse(meeting);
    if (url === "/meetings/m-1/report") return report() as never;
    if (url === "/meetings/m-1/playback") return okResponse(playbackDescriptor());
    if (url === "/meetings/m-1/transcript/timeline") return timeline() as never;
    if (url === "/meetings/m-1/transcript") return okResponse(transcriptWithRevision());
    if (url === "/meetings/m-1/actions") return okResponse([]);
    if (url === "/meetings/m-1/shares") return okResponse([]);
    throw apiError(404, "not_found");
  });
  const view = renderConsole(
    <Routes>
      <Route path="meetings/:meetingId" element={<><MeetingDetailPage /><LocationProbe /></>} />
    </Routes>,
    { route, lang, queryClient, auth: { status: "authenticated", user: FIXTURE_USER, workspace, workspaces: [workspace] } },
  );
  return { ...view, get };
}

function timelineReads(get: ReturnType<typeof renderDetail>["get"]) {
  return get.mock.calls.filter(([url]) => url === "/meetings/m-1/transcript/timeline").length;
}

const before = (first: Element, second: Element) =>
  Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);

const AGENT_TURN_1 = "Agent, 0:00 to 0:06";
const AGENT_TURN_2 = "Agent, 0:20 to 0:26, needs review: after the recording ends";
const ANON_TURN = "SPEAKER_00, 0:05 to 0:11, needs review: overlapping speech";
const UNKNOWN_TURN = "Unknown speaker, 0:09 to 0:10, needs review: unknown speaker, overlapping speech";

describe("MeetingDetailPage speaker timeline (HOM-264)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("sends no timeline request and shows no section before the meeting has results", async () => {
    const { get } = renderDetail("/meetings/m-1", { ...MEETING, status: "processing" });
    await screen.findByLabelText("Recording", { selector: "audio" });
    expect(timelineReads(get)).toBe(0);
    expect(screen.queryByRole("region", { name: "Speaker timeline" })).not.toBeInTheDocument();
  });

  it("shows the section between the player and the call report for a completed meeting", async () => {
    const { get } = renderDetail("/meetings/m-1");
    const section = await screen.findByRole("region", { name: "Speaker timeline" });
    // The section appears at once; its lanes once the answer lands.
    await screen.findByRole("group", { name: "Agent" });
    expect(timelineReads(get)).toBeGreaterThanOrEqual(1);
    expect(before(screen.getByRole("region", { name: "Recording" }), section)).toBe(true);
    expect(before(section, screen.getByRole("region", { name: "Summary" }))).toBe(true);
  });

  it("shows the section for a partial meeting too", async () => {
    renderDetail("/meetings/m-1", { ...MEETING, status: "partial" });
    expect(await screen.findByRole("region", { name: "Speaker timeline" })).toBeInTheDocument();
  });

  it("renders one lane per speaker with its label, kind, speaking time and turn count", async () => {
    renderDetail("/meetings/m-1");
    const agent = await screen.findByRole("group", { name: "Agent" });
    expect(within(agent).getByText("Identified")).toBeInTheDocument();
    expect(within(agent).getByText("0:12")).toBeInTheDocument();
    expect(within(agent).getByText("2 turns")).toBeInTheDocument();

    const anon = screen.getByRole("group", { name: "SPEAKER_00" });
    expect(within(anon).getByText("Unnamed")).toBeInTheDocument();
    expect(within(anon).getByText("0:06")).toBeInTheDocument();
    expect(within(anon).getByText("1 turn")).toBeInTheDocument();

    const unknown = screen.getByRole("group", { name: "Unknown speaker" });
    expect(within(unknown).getByText("Unknown", { exact: true })).toBeInTheDocument();
    expect(within(unknown).getByText("0:01")).toBeInTheDocument();
    // The unknown lane is marked for the warning styling (dashed bar, warning color).
    expect(unknown.classList.contains("is-unknown")).toBe(true);
  });

  it("draws every turn as a button inside its lane, and none for a speaker without a lane", async () => {
    const ghost = timelineTurn({ segment_id: "seg-9", sequence: 5, speaker_key: "GHOST", start_ms: 27_000, end_ms: 28_000 });
    renderDetail("/meetings/m-1", MEETING, {
      timeline: () => okResponse(speakerTimeline({ turns: [...speakerTimeline().turns, ghost] })),
    });
    const agent = await screen.findByRole("group", { name: "Agent" });
    expect(within(agent).getByRole("button", { name: AGENT_TURN_1 })).toBeInTheDocument();
    expect(within(agent).getByRole("button", { name: AGENT_TURN_2 })).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "SPEAKER_00" })).getByRole("button", { name: ANON_TURN })).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Unknown speaker" })).getByRole("button", { name: UNKNOWN_TURN })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^GHOST/ })).not.toBeInTheDocument();
    // A turn with a review reason carries the visible warning marker class.
    const reviewTurn = within(screen.getByRole("group", { name: "SPEAKER_00" })).getByRole("button", { name: ANON_TURN });
    expect(reviewTurn.classList.contains("needs-review")).toBe(true);
  });

  it("positions each bar by its time range with a minimum visible width", async () => {
    renderDetail("/meetings/m-1");
    const agent = await screen.findByRole("group", { name: "Agent" });
    const first = within(agent).getByRole("button", { name: AGENT_TURN_1 });
    expect(first.style.left).toBe("0%");
    expect(first.style.width).toBe("20%");
    expect(first.style.minWidth).toBe("3px");
    const anon = within(screen.getByRole("group", { name: "SPEAKER_00" })).getByRole("button", { name: ANON_TURN });
    expect(anon.style.left).toBe(`${(5_000 / 30_000) * 100}%`);
    expect(anon.style.width).toBe(`${(6_000 / 30_000) * 100}%`);
  });

  it("keeps the track left to right with bidi times on the Persian page", async () => {
    renderDetail("/meetings/m-1", MEETING, { lang: "fa" });
    await screen.findByRole("group", { name: "Agent" });
    const section = screen.getByRole("region", { name: "خط زمانی گوینده‌ها" });
    // Time 0 stays on the left, the same side as the native audio progress bar.
    const chart = section.querySelector(".speaker-timeline-chart");
    expect(chart?.getAttribute("dir")).toBe("ltr");
    // The horizontal scroller also starts at time 0 on the RTL page: its
    // scroll origin is the left, not the page's right edge.
    expect(section.querySelector(".speaker-timeline-scroll")?.getAttribute("dir")).toBe("ltr");
    expect(chart?.querySelector(".speaker-timeline-axis time")?.getAttribute("dir")).toBe("ltr");
    // Labels and copy stay Persian; an anonymous lane is called by its key.
    expect(within(section).getByRole("group", { name: "گویندهٔ نامشخص" })).toBeInTheDocument();
    expect(within(section).getByRole("group", { name: "SPEAKER_00" })).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "SPEAKER_00، ۰:۰۵ تا ۰:۱۱، نیاز به بررسی: هم‌زمانی گفتار" })).toBeInTheDocument();
  });

  it("shows the loading state inside the section", async () => {
    renderDetail("/meetings/m-1", MEETING, { timeline: () => new Promise(() => undefined) });
    expect(await screen.findByText("Loading the speaker timeline")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Call" })).toBeInTheDocument();
  });

  it("shows an inline error and keeps the rest of the page when the timeline fails", async () => {
    renderDetail("/meetings/m-1", MEETING, { timeline: () => { throw apiError(500, "internal_error"); } });
    const message = await screen.findByText("The speaker timeline could not be loaded.");
    expect(message.closest('[role="status"]')).not.toBeNull();
    expect(screen.getByRole("region", { name: "Summary" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Agent" })).not.toBeInTheDocument();
  });

  it("shows the error state, not a crash, for a body that is not a timeline", async () => {
    for (const bad of [() => okResponse("<html>proxy page</html>"), () => okResponse({ nope: true })]) {
      const view = renderDetail("/meetings/m-1", MEETING, { timeline: bad });
      const message = await view.findByText("The speaker timeline could not be loaded.");
      expect(message.closest('[role="status"]')).not.toBeNull();
      expect(view.getByRole("heading", { level: 1, name: "Call" })).toBeInTheDocument();
      expect(view.getByRole("region", { name: "Summary" })).toBeInTheDocument();
      expect(view.queryByRole("group", { name: "Agent" })).not.toBeInTheDocument();
      view.unmount();
      vi.restoreAllMocks();
      window.localStorage.clear();
    }
  });

  it("keeps a loaded timeline on screen when a background refetch fails", async () => {
    let answer: () => unknown = () => okResponse(speakerTimeline());
    const { queryClient } = renderDetail("/meetings/m-1", MEETING, { timeline: () => answer() });
    await screen.findByRole("group", { name: "Agent" });
    answer = () => { throw apiError(503, "service_unavailable"); };
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: platformQueryKeys.transcriptTimeline("m-1") });
      // React Query tells the page on a later tick; let that render happen.
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.getByRole("group", { name: "Agent" })).toBeInTheDocument();
    expect(screen.queryByText("The speaker timeline could not be loaded.")).not.toBeInTheDocument();
  });

  it("shows the empty state when there is no revision, no turn, or no time span", async () => {
    for (const overrides of [
      { revision: null },
      { turns: [], overlaps: [] },
      { timeline_end_ms: 0 },
    ]) {
      const view = renderDetail("/meetings/m-1", MEETING, { timeline: () => okResponse(speakerTimeline(overrides)) });
      expect(await screen.findByText("This meeting has no speaker turns yet.")).toBeInTheDocument();
      expect(screen.queryByRole("group", { name: "Agent" })).not.toBeInTheDocument();
      view.unmount();
      vi.restoreAllMocks();
    }
  });

  it("marks only cross-speaker overlaps and names each band by its speakers", async () => {
    renderDetail("/meetings/m-1");
    await screen.findByRole("group", { name: "Agent" });
    const section = screen.getByRole("region", { name: "Speaker timeline" });
    const list = within(section).getByRole("list", { name: "Overlapping speech" });
    const bands = within(list).getAllByRole("listitem");
    expect(bands).toHaveLength(2);
    expect(bands[0]).toHaveAccessibleName("Overlap from 0:05 to 0:06: Agent, SPEAKER_00");
    expect(bands[1]).toHaveAccessibleName("Overlap from 0:09 to 0:10: SPEAKER_00, Unknown speaker");
    // The same-speaker band at 0:20 is never drawn (ADR 0069 D3).
    expect(within(list).queryByRole("listitem", { name: /0:20/ })).not.toBeInTheDocument();
  });

  it("hides the overlap row when there is no cross-speaker overlap", async () => {
    renderDetail("/meetings/m-1", MEETING, {
      timeline: () => okResponse(speakerTimeline({ overlaps: [speakerTimeline().overlaps[1]] })),
    });
    await screen.findByRole("group", { name: "Agent" });
    expect(screen.queryByRole("list", { name: "Overlapping speech" })).not.toBeInTheDocument();
  });

  it("sums overlap and unknown speech with the basis note and the demo marker", async () => {
    const view = renderDetail("/meetings/m-1");
    await view.findByRole("group", { name: "Agent" });
    const section = view.getByRole("region", { name: "Speaker timeline" });
    const summary = section.querySelector(".speaker-timeline-summary");
    expect(summary?.textContent).toContain("0:02");
    expect(summary?.textContent).toContain("0:01");
    expect(summary?.textContent).toContain("Overlap is measured from turn times.");
    expect(summary?.textContent).not.toContain("Demo data");
    view.unmount();
    vi.restoreAllMocks();
    window.localStorage.clear();

    const demo = renderDetail("/meetings/m-1", MEETING, {
      timeline: () => okResponse(speakerTimeline({ revision: timelineRevision({ is_mock: true }) })),
    });
    const marker = await demo.findByText("Demo data");
    expect(marker.closest(".speaker-timeline-summary")).not.toBeNull();
  });

  it("activating a turn opens the transcript on that segment and cues the audio without playing", async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    renderDetail("/meetings/m-1");
    const turn = await screen.findByRole("button", { name: ANON_TURN });
    fireEvent.click(turn);
    expect(await screen.findByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(document.getElementById("segment-seg-2")).toHaveFocus());
    const audio = screen.getByLabelText("Recording", { selector: "audio" }) as HTMLAudioElement;
    expect(audio.currentTime).toBe(5);
    expect(play).not.toHaveBeenCalled();
    // The timeline never writes ?at= itself.
    expect(screen.getByLabelText("location").textContent).toBe("");
  });

  it("activating a turn without a playable recording still opens the transcript", async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    renderDetail("/meetings/m-1", { ...MEETING, recording: null });
    fireEvent.click(await screen.findByRole("button", { name: AGENT_TURN_1 }));
    expect(await screen.findByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(document.getElementById("segment-seg-1")).toHaveFocus());
    expect(screen.queryByLabelText("Recording", { selector: "audio" })).not.toBeInTheDocument();
    expect(play).not.toHaveBeenCalled();
  });

  it("moves focus between a lane's turns with the arrow keys, one tab stop per lane", async () => {
    renderDetail("/meetings/m-1");
    const agent = await screen.findByRole("group", { name: "Agent" });
    const first = within(agent).getByRole("button", { name: AGENT_TURN_1 });
    const last = within(agent).getByRole("button", { name: AGENT_TURN_2 });
    expect(first).toHaveAttribute("tabindex", "0");
    expect(last).toHaveAttribute("tabindex", "-1");
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(last).toHaveFocus();
    // The roving tab stop follows the last focused turn.
    expect(last).toHaveAttribute("tabindex", "0");
    expect(first).toHaveAttribute("tabindex", "-1");
    fireEvent.keyDown(last, { key: "Home" });
    expect(first).toHaveFocus();
    fireEvent.keyDown(first, { key: "End" });
    expect(last).toHaveFocus();
    fireEvent.keyDown(last, { key: "ArrowRight" });
    expect(last).toHaveFocus();
  });

  it("keeps exactly one tab stop per lane when a refetch leaves the lane with fewer turns", async () => {
    let answer: () => unknown = () => okResponse(speakerTimeline());
    const { queryClient } = renderDetail("/meetings/m-1", MEETING, { timeline: () => answer() });
    const agent = await screen.findByRole("group", { name: "Agent" });
    // Move the lane's tab stop to its last turn.
    fireEvent.keyDown(within(agent).getByRole("button", { name: AGENT_TURN_1 }), { key: "ArrowRight" });
    expect(within(agent).getByRole("button", { name: AGENT_TURN_2 })).toHaveFocus();
    // A correction moves the lane's last turn to another speaker.
    const moved = speakerTimeline();
    moved.turns = moved.turns.map((turn) =>
      turn.segment_id === "seg-4" ? { ...turn, speaker_key: "SPEAKER_00" } : turn,
    );
    answer = () => okResponse(moved);
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: platformQueryKeys.transcriptTimeline("m-1") });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    const buttons = within(screen.getByRole("group", { name: "Agent" })).getAllByRole("button");
    expect(buttons).toHaveLength(1);
    expect(buttons.filter((button) => button.getAttribute("tabindex") === "0")).toHaveLength(1);
  });

  it("orders a lane's bars by time even when the sequence order differs", async () => {
    const shuffled = speakerTimeline();
    shuffled.turns = shuffled.turns
      .map((turn) => {
        if (turn.segment_id === "seg-1") return { ...turn, sequence: 4 };
        if (turn.segment_id === "seg-4") return { ...turn, sequence: 1 };
        return turn;
      })
      .sort((a, b) => a.sequence - b.sequence);
    renderDetail("/meetings/m-1", MEETING, { timeline: () => okResponse(shuffled) });
    const agent = await screen.findByRole("group", { name: "Agent" });
    const names = within(agent).getAllByRole("button").map((button) => button.getAttribute("aria-label"));
    expect(names).toEqual([AGENT_TURN_1, AGENT_TURN_2]);
    expect(within(agent).getAllByRole("button")[0]).toHaveAttribute("tabindex", "0");
  });

  it("draws the live playback position as a thin line outside the tab order", async () => {
    renderDetail("/meetings/m-1");
    const audio = (await screen.findByLabelText("Recording", { selector: "audio" })) as HTMLAudioElement;
    expect(document.querySelector(".speaker-timeline-position")).toBeNull();
    audio.currentTime = 3;
    fireEvent.play(audio);
    const line = await waitFor(() => {
      const element = document.querySelector(".speaker-timeline-position");
      expect(element).not.toBeNull();
      return element as Element;
    });
    expect(line.getAttribute("aria-hidden")).toBe("true");
    expect((line as HTMLElement).style.left).toBe("10%");
  });

  it("keeps every axis tick inside the track and away from the end tick", async () => {
    const lefts = () =>
      Array.from(document.querySelectorAll(".speaker-timeline-axis span")).map((span) =>
        Number((span as HTMLElement).style.left.replace("%", "")),
      );
    const crowded = renderDetail("/meetings/m-1", MEETING, {
      timeline: () => okResponse(speakerTimeline({ timeline_end_ms: 31_000 })),
    });
    await crowded.findByRole("group", { name: "Agent" });
    const axis = document.querySelector(".speaker-timeline-axis") as HTMLElement;
    for (const left of lefts()) expect(left).toBeLessThanOrEqual(100);
    // A step tick one second from the end is dropped, not drawn next to it.
    expect(within(axis).queryByText("0:30")).not.toBeInTheDocument();
    expect(within(axis).getByText("0:31")).toBeInTheDocument();
    crowded.unmount();
    vi.restoreAllMocks();
    window.localStorage.clear();

    // A track under half a second still ends inside the track.
    const tiny = renderDetail("/meetings/m-1", MEETING, {
      timeline: () => okResponse(speakerTimeline({ timeline_end_ms: 400, turns: speakerTimeline().turns.slice(0, 1), overlaps: [] })),
    });
    await tiny.findByRole("group", { name: "Agent" });
    for (const left of lefts()) expect(left).toBeLessThanOrEqual(100);
  });

  it("lists the turns to review in order with go-to and correct buttons for an owner", async () => {
    renderDetail("/meetings/m-1");
    await screen.findByRole("group", { name: "Agent" });
    const section = screen.getByRole("region", { name: "Speaker timeline" });
    const review = within(section).getByRole("region", { name: "Turns to review" });
    const go = within(review).getAllByRole("button", { name: /^Go to turn at/ });
    expect(go.map((button) => button.getAttribute("aria-label") ?? button.textContent)).toEqual([
      "Go to turn at 0:05",
      "Go to turn at 0:09",
      "Go to turn at 0:20",
    ]);
    expect(within(review).getByRole("button", { name: "Correct speaker of turn at 0:05" })).toBeInTheDocument();
    expect(within(review).getByRole("button", { name: "Correct speaker of turn at 0:09" })).toBeInTheDocument();
    expect(within(review).getByRole("button", { name: "Correct speaker of turn at 0:20" })).toBeInTheDocument();
    // Each item names the lane, the start time and the reason words.
    expect(within(review).getAllByText("overlapping speech")).toHaveLength(2);
    expect(within(review).getAllByText("unknown speaker")).toHaveLength(1);
    expect(within(review).getByText("after the recording ends")).toBeInTheDocument();
    // Go-to behaves like a turn click: transcript open, segment focused, audio cued.
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    fireEvent.click(within(review).getByRole("button", { name: "Go to turn at 0:05" }));
    expect(await screen.findByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(document.getElementById("segment-seg-2")).toHaveFocus());
    expect((screen.getByLabelText("Recording", { selector: "audio" }) as HTMLAudioElement).currentTime).toBe(5);
    expect(play).not.toHaveBeenCalled();
  });

  it("hides the review list when no turn needs review", async () => {
    const clean = speakerTimeline();
    clean.turns = clean.turns.map((turn) => ({ ...turn, review_reasons: [] }));
    renderDetail("/meetings/m-1", MEETING, { timeline: () => okResponse(clean) });
    await screen.findByRole("group", { name: "Agent" });
    expect(screen.queryByRole("region", { name: "Turns to review" })).not.toBeInTheDocument();
  });

  it("hides the correct button when the revision is not correctable or the role cannot update", async () => {
    const view = renderDetail("/meetings/m-1", MEETING, {
      timeline: () => okResponse(speakerTimeline({ correction: { base_revision_id: null, correctable: false, speaker_choices: [] } })),
    });
    const blocked = await view.findByRole("region", { name: "Turns to review" });
    expect(within(blocked).queryByRole("button", { name: /^Correct speaker of turn at/ })).not.toBeInTheDocument();
    expect(within(blocked).getAllByRole("button", { name: /^Go to turn at/ })).toHaveLength(3);
    view.unmount();
    vi.restoreAllMocks();
    window.localStorage.clear();

    const viewerView = renderDetail("/meetings/m-1", MEETING, { role: "viewer" });
    const viewer = await viewerView.findByRole("region", { name: "Turns to review" });
    expect(within(viewer).queryByRole("button", { name: /^Correct speaker of turn at/ })).not.toBeInTheDocument();
    expect(within(viewer).getAllByRole("button", { name: /^Go to turn at/ })).toHaveLength(3);
  });

  it("opens the existing inline correction form for the requested segment", async () => {
    renderDetail("/meetings/m-1");
    fireEvent.click(await screen.findByRole("button", { name: "Correct speaker of turn at 0:05" }));
    expect(await screen.findByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");
    const text = await screen.findByLabelText("Turn text");
    expect(text).toHaveValue("Second line of the call");
  });

  it("fetches the timeline again after a saved correction and shows the new speakers", async () => {
    const renamed = speakerTimeline();
    renamed.lanes = renamed.lanes.map((lane) =>
      lane.speaker_key === "SPEAKER_00" ? { ...lane, display_name: "Named customer" } : lane,
    );
    let answered = 0;
    const { get } = renderDetail("/meetings/m-1", MEETING, {
      timeline: () => {
        answered += 1;
        return okResponse(answered === 1 ? speakerTimeline() : renamed);
      },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Correct speaker of turn at 0:05" }));
    // seg-2 already belongs to S2 in the transcript; moving it to S1 is a change.
    fireEvent.change(await screen.findByLabelText("Speaker"), { target: { value: "S1" } });
    fireEvent.change(screen.getByLabelText("Reason for the correction"), { target: { value: "Wrong speaker" } });
    vi.spyOn(api, "post").mockResolvedValue(okResponse(correctedTranscript(), 201));
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await screen.findByRole("group", { name: "Named customer" });
    await waitFor(() => expect(timelineReads(get)).toBeGreaterThanOrEqual(2));
  });

  it("fetches the timeline again after a speaker rename", async () => {
    const { get } = renderDetail("/meetings/m-1");
    fireEvent.click(await screen.findByRole("button", { name: "Transcript and tools" }));
    const renameInput = await screen.findByLabelText("Rename Agent");
    fireEvent.change(renameInput, { target: { value: "Senior agent" } });
    vi.spyOn(api, "patch").mockResolvedValue(
      okResponse({ id: "sp-1", speaker_key: "S1", display_name: "Senior agent", identity_type: "inferred", confidence: null }),
    );
    // One rename form per speaker: save the Agent one.
    const agentForm = renameInput.closest("form") as HTMLFormElement;
    fireEvent.click(within(agentForm).getByRole("button", { name: "Save speaker name" }));
    await waitFor(() => expect(timelineReads(get)).toBeGreaterThanOrEqual(2));
  });

  it("drops the timeline cache when the meeting becomes inaccessible", async () => {
    const queryClient = testQueryClient();
    const first = renderDetail("/meetings/m-1", MEETING, { queryClient });
    await first.findByRole("region", { name: "Speaker timeline" });
    expect(queryClient.getQueryData(platformQueryKeys.transcriptTimeline("m-1"))).toBeDefined();
    first.unmount();
    vi.restoreAllMocks();

    vi.spyOn(api, "get").mockRejectedValue(apiError(404, "meeting_not_found"));
    renderConsole(
      <Routes>
        <Route path="meetings/:meetingId" element={<MeetingDetailPage />} />
      </Routes>,
      { route: "/meetings/m-1", lang: "en", queryClient, auth: { status: "authenticated", user: FIXTURE_USER, workspace: FIXTURE_WORKSPACE } },
    );
    expect(await screen.findByText(/deleted or you no longer have access/)).toBeInTheDocument();
    expect(queryClient.getQueryData(platformQueryKeys.transcriptTimeline("m-1"))).toBeUndefined();
  });
});
