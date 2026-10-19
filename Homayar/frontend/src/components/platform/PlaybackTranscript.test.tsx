import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import type { AxiosRequestConfig } from "axios";
import api from "@/lib/api";
import MeetingResults from "@/components/platform/MeetingResults";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import { callReport } from "@/test/fixtures-report";
import {
  correctedTranscript,
  memberList,
  oldTranscript,
  transcriptRevisions,
  transcriptWithRevision,
} from "@/test/fixtures-transcript";
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
  recording: null,
  latest_job: null,
  quality: null,
};

/** The transcript GET answers with this until a correction changes it. */
let currentTranscript: Transcript = transcriptWithRevision();

/** The redacted view under an enforced policy, whose original needs the audited view. */
function redactedTranscript(): Transcript {
  return transcriptWithRevision({
    view: "redacted",
    privacy_enforced: true,
    redaction: { id: "red-1", detector_version: "1", classes: ["email"], policy_digest: "d", entity_count: 1, mapping_retained: true },
  });
}

/**
 * Answers the transcript routes, honouring the server-side filter params
 * (speaker_key, start_ms, end_ms) the way the product API does: kept segments
 * overlap the range, `speakers` stays the full list and `total_segments`
 * counts before filtering.
 */
function mockApi({
  redacted = false,
  filteredAnswer,
}: {
  redacted?: boolean;
  /** Overrides the answer to a filtered read (pending, failing); undefined keeps the default. */
  filteredAnswer?: (params: Record<string, unknown>) => Promise<unknown> | undefined;
} = {}) {
  return vi.spyOn(api, "get").mockImplementation(async (url: string, config?: AxiosRequestConfig) => {
    if (url === "/meetings/m-1/transcript") {
      if (config?.params?.view === "original") return okResponse(transcriptWithRevision());
      const revisionId = config?.params?.revision_id;
      if (revisionId === "rev-3") return okResponse(oldTranscript());
      if (revisionId) throw apiError(404, "revision_not_found");
      const base = redacted ? redactedTranscript() : currentTranscript;
      const keys = config?.params?.speaker_key as string[] | undefined;
      const startMs = config?.params?.start_ms as number | undefined;
      const endMs = config?.params?.end_ms as number | undefined;
      if (!keys && startMs === undefined && endMs === undefined) return okResponse(base);
      const override = filteredAnswer?.(config?.params ?? {});
      if (override) return override as never;
      const kept = base.segments.filter((segment) => {
        if (keys && !keys.includes(segment.speaker_key)) return false;
        if (startMs !== undefined && segment.end_ms <= startMs) return false;
        if (endMs !== undefined && segment.start_ms >= endMs) return false;
        return true;
      });
      return okResponse({
        ...base,
        segments: kept,
        total_segments: base.segments.length,
        filters: { speaker_keys: keys ?? [], start_ms: startMs ?? null, end_ms: endMs ?? null },
      });
    }
    if (url === "/meetings/m-1/transcript/revisions") return okResponse(transcriptRevisions());
    if (url === "/members") return okResponse(memberList());
    if (url === "/meetings/m-1/actions") return okResponse([]);
    if (url === "/meetings/m-1/shares") return okResponse([]);
    if (url === "/meetings/m-1/report") return okResponse(callReport());
    throw apiError(404, "not_found");
  });
}

interface PlaybackProps {
  canPlay?: boolean;
  playbackMs?: number | null;
  onPlayFrom?: (startMs: number) => void;
  onSeek?: (ms: number) => void;
}

function renderResults(
  props: PlaybackProps = {},
  { redacted = false, filteredAnswer }: Parameters<typeof mockApi>[0] = {},
) {
  const get = mockApi({ redacted, filteredAnswer });
  const onPlayFrom = props.onPlayFrom ?? vi.fn();
  const onSeek = props.onSeek ?? vi.fn();
  const view = renderConsole(
    <MeetingResults meeting={MEETING} canPlay={props.canPlay} playbackMs={props.playbackMs} onPlayFrom={onPlayFrom} onSeek={onSeek} />,
    { lang: "en", auth: { status: "authenticated", user: FIXTURE_USER, workspace: FIXTURE_WORKSPACE } },
  );
  return { ...view, get, onPlayFrom, onSeek };
}

const transcriptCalls = (get: ReturnType<typeof mockApi>, match: (params: Record<string, unknown>) => boolean = () => true) =>
  get.mock.calls.filter(
    ([url, config]) =>
      url === "/meetings/m-1/transcript" && match(((config as AxiosRequestConfig | undefined)?.params ?? {}) as Record<string, unknown>),
  );

const filteredCalls = (get: ReturnType<typeof mockApi>) =>
  transcriptCalls(get, (params) => params.speaker_key !== undefined || params.start_ms !== undefined || params.end_ms !== undefined);

describe("MeetingResults playback sync (HOM-247)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    currentTranscript = transcriptWithRevision();
  });

  it("offers Play from m:ss on each turn only when the player is available", async () => {
    const first = renderResults({ canPlay: false });
    await screen.findByText("First line of the call");
    expect(screen.queryByRole("button", { name: "Play from 0:00" })).not.toBeInTheDocument();
    expect(screen.getByText("0:00").tagName).toBe("TIME");
    first.unmount();
    vi.restoreAllMocks();

    const { onPlayFrom } = renderResults({ canPlay: true });
    await screen.findByText("First line of the call");
    fireEvent.click(screen.getByRole("button", { name: "Play from 0:05" }));
    expect(onPlayFrom).toHaveBeenCalledWith(5000);
    expect(onPlayFrom).toHaveBeenCalledTimes(1);
  });

  it("plays from a turn's start on a text click, but not a selection or a control", async () => {
    const { onPlayFrom } = renderResults({ canPlay: true });
    const paragraph = (await screen.findByText("Second line of the call")).closest("p")!;
    fireEvent.click(paragraph);
    expect(onPlayFrom).toHaveBeenCalledWith(5000);

    // A person selecting text is not pressing play.
    const selection = window.getSelection()!;
    const range = document.createRange();
    range.selectNodeContents(paragraph);
    selection.addRange(range);
    fireEvent.click(paragraph);
    selection.removeAllRanges();
    expect(onPlayFrom).toHaveBeenCalledTimes(1);
  });

  it("keeps the PII placeholder buttons working next to the text click", async () => {
    currentTranscript = redactedTranscript();
    currentTranscript.segments = currentTranscript.segments.map((segment) =>
      segment.id === "seg-2" ? { ...segment, text: "Call me at [PHONE_1]" } : segment,
    );
    const { onPlayFrom } = renderResults({ canPlay: true });
    const paragraph = (await screen.findByText(/Call me at/)).closest("p")!;
    // A click on the masked value's button opens the reveal form, not the player.
    fireEvent.click(screen.getByRole("button", { name: "Reveal [PHONE_1]" }));
    expect(onPlayFrom).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/Reason for revealing this value/)).toBeInTheDocument();
    // A click on the plain text still plays.
    fireEvent.click(paragraph);
    expect(onPlayFrom).toHaveBeenCalledWith(5000);
  });

  it("marks exactly one turn at the audio's time and none before any play or seek", async () => {
    const { rerender } = renderResults({ canPlay: true, playbackMs: null });
    await screen.findByText("First line of the call");
    expect(document.querySelectorAll(".meeting-segment[aria-current]").length).toBe(0);

    rerender(
      <MeetingResults meeting={MEETING} canPlay playbackMs={6000} onPlayFrom={vi.fn()} onSeek={vi.fn()} />,
    );
    expect(document.getElementById("segment-seg-2")).toHaveAttribute("aria-current", "true");
    expect(document.getElementById("segment-seg-2")).toHaveClass("is-playing");
    expect(document.querySelectorAll(".meeting-segment[aria-current]").length).toBe(1);

    rerender(
      <MeetingResults meeting={MEETING} canPlay playbackMs={2000} onPlayFrom={vi.fn()} onSeek={vi.fn()} />,
    );
    expect(document.getElementById("segment-seg-1")).toHaveAttribute("aria-current", "true");
    expect(document.getElementById("segment-seg-2")).not.toHaveAttribute("aria-current");
  });

  it("follows the current turn with the nearest scroll, only while checked and no form is open", async () => {
    const scrolled: Element[] = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = vi.fn(function scrollSpy(this: Element) { scrolled.push(this); });
    try {
      mockApi();
      const props = { meeting: MEETING, canPlay: true, onPlayFrom: vi.fn(), onSeek: vi.fn() } as const;
      const { rerender } = renderConsole(<MeetingResults {...props} playbackMs={null} />, {
        lang: "en",
        auth: { status: "authenticated", user: FIXTURE_USER, workspace: FIXTURE_WORKSPACE },
      });
      await screen.findByText("First line of the call");
      expect(screen.getByRole("checkbox", { name: "Follow the audio" })).toBeChecked();

      rerender(<MeetingResults {...props} playbackMs={6000} />);
      await waitFor(() => expect(scrolled).toHaveLength(1));
      expect(scrolled[0]).toBe(document.getElementById("segment-seg-2"));
      expect((Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.calls.at(-1)).toEqual([{ block: "nearest" }]);
      // The same turn stays current: no repeat scrolls.
      rerender(<MeetingResults {...props} playbackMs={7000} />);
      expect(scrolled).toHaveLength(1);

      // An open correction form stops the following.
      fireEvent.click(screen.getByRole("button", { name: "Edit turn at 0:05" }));
      rerender(<MeetingResults {...props} playbackMs={2000} />);
      expect(scrolled).toHaveLength(1);

      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      fireEvent.click(screen.getByRole("checkbox", { name: "Follow the audio" }));
      rerender(<MeetingResults {...props} playbackMs={5000} />);
      expect(scrolled).toHaveLength(1);
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });
});

describe("MeetingResults server-side transcript filters (HOM-247)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    currentTranscript = transcriptWithRevision();
  });

  async function applyTimeRange(from: string, to: string) {
    fireEvent.change(screen.getByLabelText("From"), { target: { value: from } });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: to } });
    fireEvent.click(screen.getByRole("button", { name: "Apply time range" }));
  }

  it("sends no filtered request without a filter", async () => {
    const { get } = renderResults({ canPlay: true });
    await screen.findByText("First line of the call");
    await waitFor(() => expect(transcriptCalls(get)).toHaveLength(1));
    expect(filteredCalls(get)).toHaveLength(0);
    expect(screen.queryByText(/^Showing /)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("From")).toBeInTheDocument();
  });

  it("sends the speaker key through the server and shows the counts", async () => {
    const { get } = renderResults({ canPlay: true });
    await screen.findByText("First line of the call");
    fireEvent.change(screen.getByLabelText("Show speaker"), { target: { value: "S2" } });
    await screen.findByText("Showing 1 of 2 turns");
    expect(filteredCalls(get)).toHaveLength(1);
    expect(filteredCalls(get).at(-1)).toEqual([
      "/meetings/m-1/transcript",
      { params: { speaker_key: ["S2"] }, paramsSerializer: { indexes: null } },
    ]);
    expect(screen.getByText("Second line of the call")).toBeInTheDocument();
    expect(screen.queryByText("First line of the call")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeInTheDocument();
  });

  it("sends a parsed time range, and only the bound that was filled", async () => {
    const { get } = renderResults({ canPlay: true });
    await screen.findByText("First line of the call");
    await applyTimeRange("0:05", "0:09");
    await screen.findByText("Showing 1 of 2 turns");
    expect(filteredCalls(get).at(-1)).toEqual([
      "/meetings/m-1/transcript",
      { params: { start_ms: 5000, end_ms: 9000 }, paramsSerializer: { indexes: null } },
    ]);
    // 1:05 is 65 s, past every seeded turn; only start_ms is sent.
    await applyTimeRange("1:05", "");
    expect(await screen.findByText("No turns match these filters.")).toBeInTheDocument();
    expect(screen.getByText("Showing 0 of 2 turns")).toBeInTheDocument();
  });

  it("refuses an unparseable time and an inverted range, sending nothing", async () => {
    const { get } = renderResults({ canPlay: true });
    await screen.findByText("First line of the call");
    await applyTimeRange("abc", "");
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a time like 1:05.");
    await applyTimeRange("0:09", "0:05");
    expect(screen.getByRole("alert")).toHaveTextContent("The end time must be after the start time.");
    expect(filteredCalls(get)).toHaveLength(0);
    expect(screen.getByText("First line of the call")).toBeInTheDocument();
  });

  it("clears every filter back to the unfiltered list", async () => {
    const { get } = renderResults({ canPlay: true });
    await screen.findByText("First line of the call");
    fireEvent.change(screen.getByLabelText("Show speaker"), { target: { value: "S2" } });
    await screen.findByText("Showing 1 of 2 turns");
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "0:05" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply time range" }));
    await waitFor(() => expect(filteredCalls(get)).toHaveLength(2));

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await screen.findByText("First line of the call");
    expect(screen.getByText("Second line of the call")).toBeInTheDocument();
    expect(screen.queryByText(/turns/)).not.toBeInTheDocument();
    expect((screen.getByLabelText("Show speaker") as HTMLSelectElement).value).toBe("");
    expect((screen.getByLabelText("From") as HTMLInputElement).value).toBe("");
    expect(filteredCalls(get)).toHaveLength(2);
  });

  it("resets the filters for a new ?at= and for a show-in-transcript request", async () => {
    mockApi();
    const base = { meeting: MEETING, canPlay: true, onPlayFrom: vi.fn(), onSeek: vi.fn() } as const;
    const { rerender } = renderConsole(<MeetingResults {...base} atMs={null} />, {
      lang: "en",
      auth: { status: "authenticated", user: FIXTURE_USER, workspace: FIXTURE_WORKSPACE },
    });
    await screen.findByText("First line of the call");
    fireEvent.change(screen.getByLabelText("Show speaker"), { target: { value: "S1" } });
    await screen.findByText("Showing 1 of 2 turns");

    rerender(<MeetingResults {...base} atMs={6200} />);
    await waitFor(() => expect((screen.getByLabelText("Show speaker") as HTMLSelectElement).value).toBe(""));
    expect(screen.queryByText(/turns/)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Show speaker"), { target: { value: "S1" } });
    await screen.findByText("Showing 1 of 2 turns");
    rerender(<MeetingResults {...base} atMs={null} open={{ panel: "transcript", segmentId: "seg-2", seq: 1 }} />);
    await waitFor(() => expect((screen.getByLabelText("Show speaker") as HTMLSelectElement).value).toBe(""));
  });

  it("hides the filter controls in the old revision and audited original views", async () => {
    currentTranscript = redactedTranscript();
    renderResults({ canPlay: true, playbackMs: null }, { redacted: true });
    await screen.findByText("First line of the call");
    fireEvent.change(screen.getByLabelText("Show speaker"), { target: { value: "S1" } });
    await screen.findByText("Showing 1 of 2 turns");

    fireEvent.click(screen.getByRole("button", { name: "Revision history" }));
    fireEvent.click(await screen.findByRole("button", { name: "View revision 3" }));
    await screen.findByText("You are viewing revision 3. It is read only.");
    expect(screen.queryByLabelText("Show speaker")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("From")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Apply time range" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Back to current" }));
    await screen.findByText("First line of the call");
    fireEvent.click(screen.getByRole("button", { name: "Show original transcript" }));
    fireEvent.click(screen.getByRole("button", { name: "Show original" }));
    await screen.findByText(/You are viewing the original transcript/);
    expect(screen.queryByLabelText("Show speaker")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("From")).not.toBeInTheDocument();
    // Leaving the original view brings the unfiltered list back.
    fireEvent.click(screen.getByRole("button", { name: "Hide original" }));
    await screen.findByText("First line of the call");
    expect((screen.getByLabelText("Show speaker") as HTMLSelectElement).value).toBe("");
  });

  it("keeps the same speaker select, focused, while the filtered request loads (turn 2, item 1a)", async () => {
    const { get } = renderResults({ canPlay: true }, { filteredAnswer: () => new Promise(() => undefined) });
    await screen.findByText("First line of the call");
    const select = screen.getByLabelText("Show speaker");
    select.focus();
    fireEvent.change(select, { target: { value: "S1" } });
    // The filtered read is pending: the turn list shows its loading state.
    await waitFor(() => expect(filteredCalls(get)).toHaveLength(1));
    await waitFor(() => expect(screen.queryByText("First line of the call")).not.toBeInTheDocument());
    expect(screen.getByLabelText("Show speaker")).toBe(select);
    expect(select).toBeInTheDocument();
    expect(document.activeElement).toBe(select);
  });

  it("offers the time form while the speaker filter loads, and combines both (turn 2, item 1b)", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const { get } = renderResults({ canPlay: true }, {
      // The first filtered read hangs until the time range is applied.
      filteredAnswer: (params) => (params.start_ms === undefined ? gate.then(() => undefined as never) : undefined),
    });
    await screen.findByText("First line of the call");
    fireEvent.change(screen.getByLabelText("Show speaker"), { target: { value: "S1" } });
    expect(screen.getByLabelText("From")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "1:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply time range" }));
    await waitFor(() =>
      expect(filteredCalls(get).at(-1)).toEqual([
        "/meetings/m-1/transcript",
        { params: { speaker_key: ["S1"], start_ms: 60000 }, paramsSerializer: { indexes: null } },
      ]),
    );
    release();
  });

  it("still offers Clear filters when the filtered request fails (turn 2, item 1c)", async () => {
    const { get } = renderResults({ canPlay: true }, { filteredAnswer: () => Promise.reject(apiError(500, "internal_error")) });
    await screen.findByText("First line of the call");
    fireEvent.change(screen.getByLabelText("Show speaker"), { target: { value: "S2" } });
    await waitFor(() => expect(screen.queryByText("First line of the call")).not.toBeInTheDocument());
    const clear = await screen.findByRole("button", { name: "Clear filters" });
    const before = transcriptCalls(get).length;
    fireEvent.click(clear);
    await screen.findByText("First line of the call");
    expect(screen.getByText("Second line of the call")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
    // Nothing new was asked with filter params.
    expect(transcriptCalls(get).slice(before).every(([, config]) => config === undefined)).toBe(true);
  });

  it("refuses a single digit after a colon (turn 2, item 6)", async () => {
    const { get } = renderResults({ canPlay: true });
    await screen.findByText("First line of the call");
    await applyTimeRange("1:5", "");
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a time like 1:05.");
    expect(filteredCalls(get)).toHaveLength(0);
  });

  it("refetches the filtered list after a correction under a filter", async () => {
    const { get } = renderResults({ canPlay: true });
    await screen.findByText("First line of the call");
    fireEvent.change(screen.getByLabelText("Show speaker"), { target: { value: "S1" } });
    await screen.findByText("Showing 1 of 2 turns");
    expect(filteredCalls(get)).toHaveLength(1);

    vi.spyOn(api, "post").mockImplementation(async () => {
      currentTranscript = correctedTranscript();
      return okResponse(correctedTranscript(), 201);
    });
    fireEvent.click(screen.getByRole("button", { name: "Edit turn at 0:00" }));
    fireEvent.change(screen.getByLabelText("Turn text"), { target: { value: "First line, corrected" } });
    fireEvent.change(screen.getByLabelText("Reason for the correction"), { target: { value: "Fix a typo" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    });
    expect(await screen.findByText("Saved as revision 5.")).toBeInTheDocument();
    // The filtered view is read again and shows the corrected turn, still filtered.
    await waitFor(() => expect(screen.getByText("First line, corrected")).toBeInTheDocument());
    expect(filteredCalls(get).length).toBeGreaterThan(1);
    expect(filteredCalls(get).every(([, config]) => (config as AxiosRequestConfig).params.speaker_key[0] === "S1")).toBe(true);
    expect(screen.getByText("Showing 1 of 2 turns")).toBeInTheDocument();
  });
});

describe("MeetingResults turn navigation keys (HOM-247)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    currentTranscript = transcriptWithRevision();
  });

  it("seeks to the next and previous turn without playing", async () => {
    mockApi();
    const onPlayFrom = vi.fn();
    const seek = vi.fn();
    const props = (playbackMs: number | null) => (
      <MeetingResults meeting={MEETING} canPlay playbackMs={playbackMs} onPlayFrom={onPlayFrom} onSeek={seek} />
    );
    const { rerender } = renderConsole(props(null), {
      lang: "en",
      auth: { status: "authenticated", user: FIXTURE_USER, workspace: FIXTURE_WORKSPACE },
    });
    await screen.findByText("First line of the call");
    // No current turn yet: both keys go to the first turn.
    fireEvent.keyDown(document.body, { code: "KeyN" });
    fireEvent.keyDown(document.body, { code: "KeyP" });
    expect(seek).toHaveBeenNthCalledWith(1, 0);
    expect(seek).toHaveBeenNthCalledWith(2, 0);

    rerender(props(6000));
    fireEvent.keyDown(document.body, { code: "KeyP" });
    expect(seek).toHaveBeenNthCalledWith(3, 0);

    rerender(props(2000));
    fireEvent.keyDown(document.body, { code: "KeyN" });
    expect(seek).toHaveBeenNthCalledWith(4, 5000);
    // A navigation key never starts playback.
    expect(onPlayFrom).not.toHaveBeenCalled();
  });

  it("ignores N without a player, in an input, in the drawer and off the transcript tab", async () => {
    const first = renderResults({ canPlay: false, onSeek: vi.fn() });
    await screen.findByText("First line of the call");
    fireEvent.keyDown(document.body, { code: "KeyN" });
    expect(first.onSeek).not.toHaveBeenCalled();
    first.unmount();
    vi.restoreAllMocks();

    const { onSeek } = renderResults({ canPlay: true, onSeek: vi.fn() });
    await screen.findByText("First line of the call");
    // While typing the correction reason, the letter belongs to the input.
    fireEvent.click(screen.getByRole("button", { name: "Edit turn at 0:00" }));
    fireEvent.keyDown(screen.getByLabelText("Reason for the correction"), { code: "KeyN" });
    expect(onSeek).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    // Inside the revision history drawer.
    fireEvent.click(screen.getByRole("button", { name: "Revision history" }));
    const drawer = await screen.findByRole("dialog", { name: "Revision history" });
    fireEvent.keyDown(drawer, { code: "KeyN" });
    expect(onSeek).not.toHaveBeenCalled();
    fireEvent.click(within(drawer).getByRole("button", { name: "Close revision history" }));

    // On another tab the turn list is not on screen.
    fireEvent.click(screen.getByRole("tab", { name: "Export" }));
    fireEvent.keyDown(document.body, { code: "KeyN" });
    expect(onSeek).not.toHaveBeenCalled();
  });
});
