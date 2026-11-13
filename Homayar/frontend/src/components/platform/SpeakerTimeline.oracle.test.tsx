import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router";
import api from "@/lib/api";
import { forgetMeetingContent } from "@/lib/platform-api";
import { formatDuration } from "@/lib/format";
import MeetingDetailPage from "@/components/platform/MeetingDetailPage";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole, testQueryClient } from "@/test/fixtures";
import { callReport } from "@/test/fixtures-report";
import { playbackDescriptor } from "@/test/fixtures-playback";
import { currentRevision, memberList, transcriptRevisions, transcriptWithRevision } from "@/test/fixtures-transcript";
import type { MeetingDetail, MeetingStatus, Transcript, TranscriptSegment } from "@/types/platform";
import type { WorkspaceRole } from "@/types/auth";

type Lang = "fa" | "en";
type Json = Record<string, unknown>;

const T = "2026-09-24T12:00:00Z";
const TIMELINE_URL = "/meetings/m-1/transcript/timeline";
const TIMELINE_KEY = ["platform", "meetings", "m-1", "transcript-timeline"];

const MEETING: MeetingDetail = {
  id: "m-1",
  workspace_id: "ws-1",
  title: "Call",
  language: "en",
  status: "completed",
  duration_seconds: 33,
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
    duration_seconds: 33,
    channel_count: 1,
    sample_rate_hz: 16000,
  },
  latest_job: null,
  quality: null,
};

const COPY = {
  en: {
    heading: "Speaker timeline",
    loading: "Loading the speaker timeline",
    failed: "The speaker timeline could not be loaded.",
    empty: "This meeting has no speaker turns yet.",
    unknownLane: "Unknown speaker",
    kinds: { identified: "Identified", anonymous: "Unnamed", unknown: "Unknown" },
    reasons: { unknown_speaker: "unknown speaker", overlap: "overlapping speech", outside_recording: "after the recording ends" },
    overlapList: "Overlapping speech",
    basis: "Overlap is measured from turn times.",
    demo: "Demo data",
    review: "Turns to review",
    sep: ", ",
    turn: (label: string, start: string, end: string, reasons: string[]) =>
      `${label}, ${start} to ${end}${reasons.length ? `, needs review: ${reasons.join(", ")}` : ""}`,
    overlap: (start: string, end: string, labels: string[]) => `Overlap from ${start} to ${end}: ${labels.join(", ")}`,
    goTo: (start: string) => `Go to turn at ${start}`,
    correct: (start: string) => `Correct speaker of turn at ${start}`,
  },
  fa: {
    heading: "خط زمانی گوینده‌ها",
    loading: "در حال دریافت خط زمانی",
    failed: "خط زمانی گوینده‌ها دریافت نشد.",
    empty: "هنوز نوبت گفت‌وگویی برای این جلسه ثبت نشده است.",
    unknownLane: "گویندهٔ نامشخص",
    kinds: { identified: "شناخته‌شده", anonymous: "بی‌نام", unknown: "نامشخص" },
    reasons: { unknown_speaker: "گوینده نامشخص", overlap: "هم‌زمانی گفتار", outside_recording: "بعد از پایان فایل صوتی" },
    overlapList: "هم‌زمانی گفتار",
    basis: "هم‌زمانی از روی زمان نوبت‌ها حساب شده است.",
    demo: "دادهٔ نمایشی",
    review: "نوبت‌های نیازمند بررسی",
    sep: "، ",
    turn: (label: string, start: string, end: string, reasons: string[]) =>
      `${label}، ${start} تا ${end}${reasons.length ? `، نیاز به بررسی: ${reasons.join("، ")}` : ""}`,
    overlap: (start: string, end: string, labels: string[]) => `هم‌زمانی از ${start} تا ${end}: ${labels.join("، ")}`,
    goTo: (start: string) => `رفتن به نوبت ${start}`,
    correct: (start: string) => `اصلاح گویندهٔ نوبت ${start}`,
  },
} as const;

type Reason = keyof (typeof COPY)["en"]["reasons"];

const t = (ms: number, lang: Lang = "en") => formatDuration(ms / 1000, lang);

function lane(speaker_key: string, display_name: string | null, kind: string, turn_count: number, speech_ms: number, overlap_ms: number): Json {
  return {
    speaker_key,
    display_name,
    kind,
    identity_type: kind === "identified" ? "person" : null,
    speaker_confidence: null,
    turn_count,
    speech_ms,
    overlap_ms,
  };
}

function turn(segment_id: string, sequence: number, speaker_key: string, start_ms: number, end_ms: number, review_reasons: Reason[] = [], overlap_ids: number[] = []): Json {
  return {
    segment_id,
    sequence,
    speaker_key,
    start_ms,
    end_ms,
    confidence: 0.9,
    attribution: speaker_key === "UNKNOWN" ? "none" : "diarization",
    overlap_ids,
    review_reasons,
  };
}

function overlap(index: number, start_ms: number, end_ms: number, kind: string, speaker_keys: string[], segment_ids: string[]): Json {
  return { index, start_ms, end_ms, kind, speaker_keys, segment_ids, includes_unknown: speaker_keys.includes("UNKNOWN") };
}

function timeline(overrides: Json = {}): Json {
  return {
    meeting_id: "m-1",
    revision: {
      id: "rev-4",
      revision_number: 4,
      source: "correction",
      is_current: true,
      diarization_status: "completed",
      diarization_provider: "mock",
      is_mock: false,
    },
    overlap_basis: "turns",
    recording_duration_ms: 33000,
    timeline_end_ms: 34000,
    lanes: [
      lane("S1", "Agent", "identified", 5, 19000, 2000),
      lane("UNKNOWN", null, "unknown", 1, 5000, 1000),
      lane("SPEAKER_01", null, "anonymous", 1, 6000, 1000),
    ],
    turns: [
      turn("seg-1", 1, "S1", 0, 4000, ["overlap"], [0]),
      turn("seg-2", 2, "SPEAKER_01", 3000, 9000, ["overlap"], [0]),
      turn("seg-3", 3, "S1", 10000, 16000, ["overlap"], [1]),
      turn("seg-4", 4, "UNKNOWN", 15000, 20000, ["unknown_speaker", "overlap"], [1]),
      turn("seg-5", 5, "S1", 21000, 25000, [], [2]),
      turn("seg-6", 6, "S1", 24000, 26000, [], [2]),
      turn("seg-7", 7, "GHOST", 27000, 28000),
      turn("seg-8", 8, "S1", 31000, 34000, ["outside_recording"]),
    ],
    overlaps: [
      overlap(0, 3000, 4000, "cross_speaker", ["S1", "SPEAKER_01"], ["seg-1", "seg-2"]),
      overlap(1, 15000, 16000, "cross_speaker", ["S1", "UNKNOWN"], ["seg-3", "seg-4"]),
      overlap(2, 24000, 25000, "same_speaker", ["S1"], ["seg-5", "seg-6"]),
    ],
    overlap_ms: 2000,
    unknown_speech_ms: 5000,
    confidence: { origin: "asr", calibrated: false },
    correction: { base_revision_id: "rev-4", correctable: true, speaker_choices: ["S1", "SPEAKER_01"] },
    ...overrides,
  };
}

function correctedTimeline(): Json {
  return timeline({
    revision: { id: "rev-5", revision_number: 5, source: "correction", is_current: true, diarization_status: "completed", diarization_provider: "mock", is_mock: false },
    lanes: [lane("S1", "Agent", "identified", 6, 24000, 1000), lane("SPEAKER_01", null, "anonymous", 1, 6000, 1000)],
    turns: [
      turn("seg-1", 1, "S1", 0, 4000, ["overlap"], [0]),
      turn("seg-2", 2, "SPEAKER_01", 3000, 9000, ["overlap"], [0]),
      turn("seg-3", 3, "S1", 10000, 16000),
      turn("seg-4b", 4, "S1", 15000, 20000),
      turn("seg-5", 5, "S1", 21000, 25000),
      turn("seg-6", 6, "S1", 24000, 26000),
      turn("seg-8", 8, "S1", 31000, 34000, ["outside_recording"]),
    ],
    overlaps: [overlap(0, 3000, 4000, "cross_speaker", ["S1", "SPEAKER_01"], ["seg-1", "seg-2"])],
    overlap_ms: 1000,
    unknown_speech_ms: 0,
    correction: { base_revision_id: "rev-5", correctable: true, speaker_choices: ["S1", "SPEAKER_01"] },
  });
}

function segment(id: string, sequence: number, speaker_key: string, speaker_name: string, start_ms: number, end_ms: number): TranscriptSegment {
  return { id, sequence, speaker_key, speaker_name, start_ms, end_ms, text: `Turn ${sequence} text`, confidence: null, language: "en" };
}

const SPEAKERS = [
  { id: "sp-1", speaker_key: "S1", display_name: "Agent", identity_type: "person", confidence: null },
  { id: "sp-2", speaker_key: "SPEAKER_01", display_name: null, identity_type: "inferred", confidence: null },
  { id: "sp-3", speaker_key: "UNKNOWN", display_name: null, identity_type: "inferred", confidence: null },
];

function transcript(): Transcript {
  return transcriptWithRevision({
    speakers: SPEAKERS,
    segments: [
      segment("seg-1", 1, "S1", "Agent", 0, 4000),
      segment("seg-2", 2, "SPEAKER_01", "SPEAKER_01", 3000, 9000),
      segment("seg-3", 3, "S1", "Agent", 10000, 16000),
      segment("seg-4", 4, "UNKNOWN", "UNKNOWN", 15000, 20000),
      segment("seg-5", 5, "S1", "Agent", 21000, 25000),
      segment("seg-6", 6, "S1", "Agent", 24000, 26000),
      segment("seg-8", 8, "S1", "Agent", 31000, 34000),
    ],
  });
}

function correctedTranscript(): Transcript {
  const base = transcript();
  return {
    ...base,
    revision: currentRevision({ id: "rev-5", revision_number: 5, parent_revision_id: "rev-4" }),
    segments: base.segments.map((item) => (item.id === "seg-4" ? { ...item, id: "seg-4b", speaker_key: "S1", speaker_name: "Agent" } : item)),
  };
}

let timelineNow: Json = timeline();
let transcriptNow: Transcript = transcript();

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="location">{location.search}</output>;
}

function renderPage({
  meeting = MEETING,
  lang = "en",
  role = "owner",
  answer,
}: { meeting?: MeetingDetail; lang?: Lang; role?: WorkspaceRole; answer?: () => unknown } = {}) {
  const get = vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url === "/meetings/m-1") return okResponse(meeting);
    if (url === "/meetings/m-1/report") return okResponse(callReport());
    if (url === "/meetings/m-1/playback") return okResponse(playbackDescriptor());
    if (url === TIMELINE_URL) return (answer ? answer() : okResponse(timelineNow)) as never;
    if (url === "/meetings/m-1/transcript") return okResponse(transcriptNow);
    if (url === "/meetings/m-1/transcript/revisions") return okResponse(transcriptRevisions());
    if (url === "/members") return okResponse(memberList());
    if (url === "/meetings/m-1/actions") return okResponse([]);
    if (url === "/meetings/m-1/shares") return okResponse([]);
    throw apiError(404, "not_found");
  });
  const workspace = { ...FIXTURE_WORKSPACE, role };
  const view = renderConsole(
    <Routes>
      <Route path="meetings/:meetingId" element={<><MeetingDetailPage /><LocationProbe /></>} />
    </Routes>,
    { route: "/meetings/m-1", lang, auth: { status: "authenticated", user: FIXTURE_USER, workspace, workspaces: [workspace] } },
  );
  const timelineCalls = () => get.mock.calls.filter(([url]) => url === TIMELINE_URL).length;
  return { ...view, get, timelineCalls };
}

async function loadedRegion(lang: Lang = "en") {
  const region = await screen.findByRole("region", { name: COPY[lang].heading });
  await within(region).findByRole("group", { name: "Agent" });
  return region;
}

const before = (first: Element, second: Element) =>
  Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);

function placed(element: HTMLElement): HTMLElement | null {
  let node: HTMLElement | null = element;
  while (node && !node.style.left) node = node.parentElement;
  return node;
}

function turnName(lang: Lang, label: string, start: number, end: number, reasons: Reason[] = []) {
  return COPY[lang].turn(label, t(start, lang), t(end, lang), reasons.map((reason) => COPY[lang].reasons[reason]));
}

const EN_TURNS = {
  t1: turnName("en", "Agent", 0, 4000, ["overlap"]),
  t2: turnName("en", "SPEAKER_01", 3000, 9000, ["overlap"]),
  t3: turnName("en", "Agent", 10000, 16000, ["overlap"]),
  t4: turnName("en", "Unknown speaker", 15000, 20000, ["unknown_speaker", "overlap"]),
  t5: turnName("en", "Agent", 21000, 25000),
  t6: turnName("en", "Agent", 24000, 26000),
  t8: turnName("en", "Agent", 31000, 34000, ["outside_recording"]),
};

describe("Speaker timeline oracle (HOM-264)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    timelineNow = timeline();
    transcriptNow = transcript();
  });

  describe("where it shows (B1, B2, B3, A1)", () => {
    for (const status of ["queued", "processing", "failed"] as MeetingStatus[]) {
      it(`sends no timeline GET and shows no section for status ${status}`, async () => {
        const { timelineCalls } = renderPage({ meeting: { ...MEETING, status } });
        await screen.findByRole("region", { name: "Summary" });
        await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
        expect(timelineCalls()).toBe(0);
        expect(screen.queryByRole("region", { name: "Speaker timeline" })).not.toBeInTheDocument();
      });
    }

    for (const status of ["completed", "partial"] as MeetingStatus[]) {
      it(`sends the timeline GET and shows the section for status ${status}`, async () => {
        const { timelineCalls } = renderPage({ meeting: { ...MEETING, status } });
        const region = await loadedRegion();
        expect(timelineCalls()).toBeGreaterThanOrEqual(1);
        expect(within(region).getByRole("heading", { level: 2, name: "Speaker timeline" })).toBeInTheDocument();
        expect(screen.getAllByRole("region", { name: "Speaker timeline" })).toHaveLength(1);
      });
    }

    it("keeps the answer under the transcript-timeline query key", async () => {
      const { queryClient } = renderPage();
      await loadedRegion();
      expect(queryClient.getQueryData(TIMELINE_KEY)).toBeDefined();
    });

    it("sits after the player and shortcuts, before the call report, outside the closed advanced area", async () => {
      renderPage();
      const region = await loadedRegion();
      await screen.findByRole("region", { name: "Summary" });
      const ordered = [
        screen.getByRole("region", { name: "Recording" }),
        screen.getByRole("region", { name: "Keyboard shortcuts" }),
        region,
        screen.getByRole("region", { name: "Summary" }),
        screen.getByRole("button", { name: "Transcript and tools" }),
      ];
      for (let index = 1; index < ordered.length; index += 1) expect(before(ordered[index - 1], ordered[index])).toBe(true);
      expect(screen.getByRole("button", { name: "Transcript and tools" })).toHaveAttribute("aria-expanded", "false");
      expect(region.tagName).toBe("SECTION");
    });

    it("shows the Persian heading on the right-to-left page", async () => {
      renderPage({ lang: "fa" });
      const region = await loadedRegion("fa");
      expect(within(region).getByRole("heading", { level: 2, name: "خط زمانی گوینده‌ها" })).toBeInTheDocument();
      expect(document.documentElement).toHaveAttribute("dir", "rtl");
    });
  });

  describe("states (C1, C2, C3, E2 guard)", () => {
    for (const lang of ["en", "fa"] as Lang[]) {
      it(`shows the loading text inside the section (${lang})`, async () => {
        renderPage({ lang, answer: () => new Promise(() => undefined) });
        const region = await screen.findByRole("region", { name: COPY[lang].heading });
        expect(within(region).getByText(COPY[lang].loading)).toBeInTheDocument();
      });

      it(`shows an inline status, not an alert, when the GET fails, and keeps the report (${lang})`, async () => {
        renderPage({ lang, answer: () => { throw apiError(500, "internal_error"); } });
        const region = await screen.findByRole("region", { name: COPY[lang].heading });
        const message = await within(region).findByText(COPY[lang].failed);
        expect(message.closest('[role="status"]')).not.toBeNull();
        expect(message.closest('[role="alert"]')).toBeNull();
        expect(await screen.findByRole("region", { name: lang === "en" ? "Summary" : "خلاصه" })).toBeInTheDocument();
        expect(within(region).queryByRole("group")).not.toBeInTheDocument();
      });

      it(`shows the empty text and no lanes when there is no revision (${lang})`, async () => {
        timelineNow = timeline({ revision: null, lanes: [], turns: [], overlaps: [], overlap_ms: 0, unknown_speech_ms: 0, timeline_end_ms: 0 });
        renderPage({ lang });
        const region = await screen.findByRole("region", { name: COPY[lang].heading });
        expect(await within(region).findByText(COPY[lang].empty)).toBeInTheDocument();
        expect(within(region).queryByRole("group")).not.toBeInTheDocument();
        expect(within(region).queryByRole("button")).not.toBeInTheDocument();
      });
    }

    it("shows the empty text when the revision has no turns", async () => {
      timelineNow = timeline({ turns: [], overlaps: [], overlap_ms: 0, unknown_speech_ms: 0 });
      renderPage();
      const region = await screen.findByRole("region", { name: "Speaker timeline" });
      expect(await within(region).findByText(COPY.en.empty)).toBeInTheDocument();
      expect(within(region).queryByRole("button", { name: /to 0:/ })).not.toBeInTheDocument();
    });

    it("draws nothing and shows the empty text when timeline_end_ms is 0", async () => {
      timelineNow = timeline({ timeline_end_ms: 0 });
      renderPage();
      const region = await screen.findByRole("region", { name: "Speaker timeline" });
      expect(await within(region).findByText(COPY.en.empty)).toBeInTheDocument();
      expect(within(region).queryByRole("button", { name: EN_TURNS.t1 })).not.toBeInTheDocument();
      expect(within(region).queryByRole("listitem", { name: /^Overlap from/ })).not.toBeInTheDocument();
    });

    it("draws the lanes when the GET succeeds", async () => {
      renderPage();
      const region = await loadedRegion();
      expect(within(region).queryByText(COPY.en.empty)).not.toBeInTheDocument();
      expect(within(region).queryByText(COPY.en.failed)).not.toBeInTheDocument();
    });
  });

  describe("lanes (D1, D2)", () => {
    it("draws one named group per lane in the response order, never inventing a speaker name", async () => {
      renderPage();
      const region = await loadedRegion();
      const groups = ["Agent", "Unknown speaker", "SPEAKER_01"].map((name) => within(region).getByRole("group", { name }));
      expect(before(groups[0], groups[1])).toBe(true);
      expect(before(groups[1], groups[2])).toBe(true);
      expect(within(region).queryByRole("group", { name: /^Speaker \d/ })).not.toBeInTheDocument();
      expect(within(region).queryByText(/Speaker 1|Speaker 2/)).not.toBeInTheDocument();
    });

    it("labels an anonymous lane with its display name when one is sent", async () => {
      timelineNow = timeline({
        lanes: [
          lane("S1", "Agent", "identified", 5, 19000, 2000),
          lane("UNKNOWN", null, "unknown", 1, 5000, 1000),
          lane("SPEAKER_01", "Customer", "anonymous", 1, 6000, 1000),
        ],
      });
      renderPage();
      const region = await loadedRegion();
      const group = within(region).getByRole("group", { name: "Customer" });
      expect(within(group).getByRole("button", { name: turnName("en", "Customer", 3000, 9000, ["overlap"]) })).toBeInTheDocument();
      expect(within(region).queryByRole("group", { name: "SPEAKER_01" })).not.toBeInTheDocument();
    });

    it("labels the lanes in Persian", async () => {
      renderPage({ lang: "fa" });
      const region = await loadedRegion("fa");
      expect(within(region).getByRole("group", { name: "گویندهٔ نامشخص" })).toBeInTheDocument();
      expect(within(region).getByRole("group", { name: "SPEAKER_01" })).toBeInTheDocument();
    });

    for (const lang of ["en", "fa"] as Lang[]) {
      it(`shows each lane's kind and speaking time (${lang})`, async () => {
        renderPage({ lang });
        const region = await loadedRegion(lang);
        expect(within(region).getByText(COPY[lang].kinds.identified)).toBeInTheDocument();
        expect(within(region).getByText(COPY[lang].kinds.anonymous)).toBeInTheDocument();
        expect(within(region).getByText(COPY[lang].kinds.unknown)).toBeInTheDocument();
        expect(region.textContent).toContain(t(19000, lang));
        expect(region.textContent).toContain(t(6000, lang));
      });
    }
  });

  describe("turns (E1, E2, E3, E4, E5)", () => {
    it("draws every turn as a native button inside its own lane, and skips a turn with no lane", async () => {
      renderPage();
      const region = await loadedRegion();
      const lanes: [string, string[]][] = [
        ["Agent", [EN_TURNS.t1, EN_TURNS.t3, EN_TURNS.t5, EN_TURNS.t6, EN_TURNS.t8]],
        ["SPEAKER_01", [EN_TURNS.t2]],
        ["Unknown speaker", [EN_TURNS.t4]],
      ];
      for (const [label, names] of lanes) {
        const group = within(region).getByRole("group", { name: label });
        for (const name of names) {
          const button = within(group).getByRole("button", { name });
          expect(button.tagName).toBe("BUTTON");
          expect(button).toHaveAttribute("type", "button");
        }
      }
      expect(within(within(region).getByRole("group", { name: "Agent" })).queryByRole("button", { name: EN_TURNS.t2 })).not.toBeInTheDocument();
      expect(within(region).queryByRole("button", { name: /0:27/ })).not.toBeInTheDocument();
    });

    it("positions a turn by start and length over timeline_end_ms", async () => {
      renderPage();
      const region = await loadedRegion();
      const box = placed(within(region).getByRole("button", { name: EN_TURNS.t3 }));
      expect(box).not.toBeNull();
      expect(box!.style.left).toMatch(/%$/);
      expect(parseFloat(box!.style.left)).toBeCloseTo((10000 / 34000) * 100, 1);
      expect(parseFloat(box!.style.width)).toBeCloseTo((6000 / 34000) * 100, 1);
      const first = placed(within(region).getByRole("button", { name: EN_TURNS.t1 }));
      expect(parseFloat(first!.style.left)).toBeCloseTo(0, 1);
      expect(parseFloat(first!.style.width)).toBeCloseTo((4000 / 34000) * 100, 1);
    });

    for (const lang of ["en", "fa"] as Lang[]) {
      it(`names each turn button with its lane, times and review reasons in API order (${lang})`, async () => {
        renderPage({ lang });
        const region = await loadedRegion(lang);
        const unknown = COPY[lang].unknownLane;
        const names = [
          turnName(lang, "Agent", 0, 4000, ["overlap"]),
          turnName(lang, "SPEAKER_01", 3000, 9000, ["overlap"]),
          turnName(lang, unknown, 15000, 20000, ["unknown_speaker", "overlap"]),
          turnName(lang, "Agent", 21000, 25000),
          turnName(lang, "Agent", 31000, 34000, ["outside_recording"]),
        ];
        for (const name of names) expect(within(region).getByRole("button", { name })).toBeInTheDocument();
        expect(within(region).queryByRole("button", { name: turnName(lang, unknown, 15000, 20000, ["overlap", "unknown_speaker"]) })).not.toBeInTheDocument();
      });
    }

    it("spells the Persian turn name exactly", async () => {
      renderPage({ lang: "fa" });
      const region = await loadedRegion("fa");
      expect(within(region).getByRole("button", { name: "گویندهٔ نامشخص، ۰:۱۵ تا ۰:۲۰، نیاز به بررسی: گوینده نامشخص، هم‌زمانی گفتار" })).toBeInTheDocument();
      expect(within(region).getByRole("button", { name: "Agent، ۰:۲۱ تا ۰:۲۵" })).toBeInTheDocument();
    });

    it("spells the English turn name exactly", async () => {
      renderPage();
      const region = await loadedRegion();
      expect(within(region).getByRole("button", { name: "Unknown speaker, 0:15 to 0:20, needs review: unknown speaker, overlapping speech" })).toBeInTheDocument();
      expect(within(region).getByRole("button", { name: "Agent, 0:21 to 0:25" })).toBeInTheDocument();
      expect(within(region).getByRole("button", { name: "Agent, 0:31 to 0:34, needs review: after the recording ends" })).toBeInTheDocument();
    });

    it("lays the track out left to right on the Persian page and keeps the section itself in page direction", async () => {
      renderPage({ lang: "fa" });
      const region = await loadedRegion("fa");
      const button = within(region).getByRole("button", { name: turnName("fa", "Agent", 0, 4000, ["overlap"]) });
      const track = button.closest('[dir="ltr"]');
      expect(track).not.toBeNull();
      expect(region.contains(track)).toBe(true);
      expect(region.closest('[dir="ltr"]')).toBeNull();
      const visibleTimes = within(region)
        .queryAllByText(/^[0-9۰-۹]+:[0-9۰-۹]{2}$/)
        .filter((element) => element.closest('[aria-hidden="true"]') === null);
      for (const element of visibleTimes) expect(element.closest('bdi[dir="ltr"], time[dir="ltr"]')).not.toBeNull();
    });

    it("draws a decorative time axis with the start and end ticks", async () => {
      renderPage();
      const region = await loadedRegion();
      const hidden = Array.from(region.querySelectorAll('[aria-hidden="true"]'));
      expect(hidden.some((element) => element.textContent?.includes("0:00") && element.textContent.includes("0:34"))).toBe(true);
    });
  });

  describe("keyboard (E6)", () => {
    it("gives each lane one tab stop and moves through a lane in time order with the arrow keys, Home and End", async () => {
      renderPage();
      const region = await loadedRegion();
      const agent = within(region).getByRole("group", { name: "Agent" });
      const [t1, t3, t5, t6, t8] = [EN_TURNS.t1, EN_TURNS.t3, EN_TURNS.t5, EN_TURNS.t6, EN_TURNS.t8].map((name) => within(agent).getByRole("button", { name }));
      expect(t1.tabIndex).toBe(0);
      for (const other of [t3, t5, t6, t8]) expect(other.tabIndex).toBe(-1);
      expect(within(region).getByRole("button", { name: EN_TURNS.t2 }).tabIndex).toBe(0);
      expect(within(region).getByRole("button", { name: EN_TURNS.t4 }).tabIndex).toBe(0);

      t1.focus();
      fireEvent.keyDown(t1, { key: "ArrowRight" });
      expect(t3).toHaveFocus();
      expect(t3.tabIndex).toBe(0);
      expect(t1.tabIndex).toBe(-1);
      fireEvent.keyDown(t3, { key: "ArrowRight" });
      expect(t5).toHaveFocus();
      fireEvent.keyDown(t5, { key: "ArrowLeft" });
      expect(t3).toHaveFocus();
      fireEvent.keyDown(t3, { key: "End" });
      expect(t8).toHaveFocus();
      expect(t8.tabIndex).toBe(0);
      fireEvent.keyDown(t8, { key: "ArrowRight" });
      expect(t8).toHaveFocus();
      fireEvent.keyDown(t8, { key: "Home" });
      expect(t1).toHaveFocus();
      fireEvent.keyDown(t1, { key: "ArrowLeft" });
      expect(t1).toHaveFocus();
      expect(screen.getByRole("button", { name: "Transcript and tools" })).toHaveAttribute("aria-expanded", "false");
    });

    it("moves to the next turn in time with ArrowRight on the Persian page too", async () => {
      renderPage({ lang: "fa" });
      const region = await loadedRegion("fa");
      const agent = within(region).getByRole("group", { name: "Agent" });
      const t1 = within(agent).getByRole("button", { name: turnName("fa", "Agent", 0, 4000, ["overlap"]) });
      const t3 = within(agent).getByRole("button", { name: turnName("fa", "Agent", 10000, 16000, ["overlap"]) });
      t1.focus();
      fireEvent.keyDown(t1, { key: "ArrowRight" });
      expect(t3).toHaveFocus();
      fireEvent.keyDown(t3, { key: "ArrowLeft" });
      expect(t1).toHaveFocus();
    });
  });

  describe("overlaps (F1, F2, F3)", () => {
    for (const lang of ["en", "fa"] as Lang[]) {
      it(`lists only cross-speaker overlaps, named with times and lane labels (${lang})`, async () => {
        renderPage({ lang });
        const region = await loadedRegion(lang);
        const list = within(region).getByRole("list", { name: COPY[lang].overlapList });
        const items = within(list).getAllByRole("listitem");
        expect(items).toHaveLength(2);
        expect(items[0]).toHaveAccessibleName(COPY[lang].overlap(t(3000, lang), t(4000, lang), ["Agent", "SPEAKER_01"]));
        expect(items[1]).toHaveAccessibleName(COPY[lang].overlap(t(15000, lang), t(16000, lang), ["Agent", COPY[lang].unknownLane]));
        expect(within(list).queryByRole("listitem", { name: new RegExp(t(24000, lang)) })).not.toBeInTheDocument();
      });
    }

    it("spells the overlap names exactly in both languages", async () => {
      const first = renderPage();
      let region = await loadedRegion();
      expect(within(region).getByRole("listitem", { name: "Overlap from 0:15 to 0:16: Agent, Unknown speaker" })).toBeInTheDocument();
      first.unmount();
      vi.restoreAllMocks();
      window.localStorage.clear();
      renderPage({ lang: "fa" });
      region = await loadedRegion("fa");
      expect(within(region).getByRole("listitem", { name: "هم‌زمانی از ۰:۰۳ تا ۰:۰۴: Agent، SPEAKER_01" })).toBeInTheDocument();
    });

    it("draws an overlap band with the same left and width rule as a turn", async () => {
      renderPage();
      const region = await loadedRegion();
      const item = within(region).getByRole("listitem", { name: COPY.en.overlap("0:15", "0:16", ["Agent", "Unknown speaker"]) });
      const box = placed(item);
      expect(box).not.toBeNull();
      expect(parseFloat(box!.style.left)).toBeCloseTo((15000 / 34000) * 100, 1);
      expect(parseFloat(box!.style.width)).toBeCloseTo((1000 / 34000) * 100, 1);
    });

    it("renders no overlap row when every overlap is same-speaker", async () => {
      timelineNow = timeline({ overlaps: [overlap(2, 24000, 25000, "same_speaker", ["S1"], ["seg-5", "seg-6"])], overlap_ms: 0 });
      renderPage();
      const region = await loadedRegion();
      expect(within(region).queryByRole("list", { name: "Overlapping speech" })).not.toBeInTheDocument();
      expect(within(region).queryByRole("listitem", { name: /^Overlap from/ })).not.toBeInTheDocument();
    });

    it("renders no overlap row when there are no overlaps at all", async () => {
      timelineNow = timeline({ overlaps: [], overlap_ms: 0 });
      renderPage();
      const region = await loadedRegion();
      expect(within(region).queryByRole("list", { name: "Overlapping speech" })).not.toBeInTheDocument();
    });

    it("summarises the overlap time, the unknown speech time and the basis, without a demo marker for real data", async () => {
      timelineNow = timeline({ overlap_ms: 2000, unknown_speech_ms: 7000 });
      renderPage();
      const region = await loadedRegion();
      expect(region.textContent).toContain("0:02");
      expect(region.textContent).toContain("0:07");
      expect(within(region).getByText(COPY.en.basis)).toBeInTheDocument();
      expect(within(region).queryByText(COPY.en.demo)).not.toBeInTheDocument();
    });

    for (const lang of ["en", "fa"] as Lang[]) {
      it(`marks demo data when the revision is a mock (${lang})`, async () => {
        timelineNow = timeline({
          revision: { id: "rev-4", revision_number: 4, source: "asr", is_current: true, diarization_status: "completed", diarization_provider: "mock", is_mock: true },
        });
        renderPage({ lang });
        const region = await loadedRegion(lang);
        expect(within(region).getByText(COPY[lang].demo)).toBeInTheDocument();
        expect(within(region).getByText(COPY[lang].basis)).toBeInTheDocument();
      });
    }
  });

  describe("jump to the transcript and the audio (G1, G2)", () => {
    it("opens the transcript at the turn and seeks the audio without playing or writing ?at=", async () => {
      const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
      renderPage();
      const audio = (await screen.findByLabelText("Recording", { selector: "audio" })) as HTMLAudioElement;
      const region = await loadedRegion();
      fireEvent.click(within(region).getByRole("button", { name: EN_TURNS.t3 }));
      expect(await screen.findByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");
      await waitFor(() => expect(document.getElementById("segment-seg-3")).toHaveFocus());
      expect(audio.currentTime).toBe(10);
      expect(play).not.toHaveBeenCalled();
      expect(screen.getByRole("status", { name: "location" })).toHaveTextContent(/^$/);
      expect(screen.getByRole("button", { name: "Transcript and tools" })).toHaveAttribute("aria-expanded", "true");
    });

    it("does the same on Enter and on Space", async () => {
      const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
      const user = userEvent.setup();
      renderPage();
      const audio = (await screen.findByLabelText("Recording", { selector: "audio" })) as HTMLAudioElement;
      const region = await loadedRegion();
      within(region).getByRole("button", { name: EN_TURNS.t2 }).focus();
      await user.keyboard("{Enter}");
      await waitFor(() => expect(document.getElementById("segment-seg-2")).toHaveFocus());
      expect(audio.currentTime).toBe(3);
      within(screen.getByRole("region", { name: "Speaker timeline" })).getByRole("button", { name: EN_TURNS.t8 }).focus();
      await user.keyboard(" ");
      await waitFor(() => expect(document.getElementById("segment-seg-8")).toHaveFocus());
      expect(audio.currentTime).toBe(31);
      expect(play).not.toHaveBeenCalled();
      expect(screen.getByRole("status", { name: "location" })).toHaveTextContent(/^$/);
    });

    it("still opens the transcript when the page cannot play, and touches no audio", async () => {
      const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
      renderPage({ meeting: { ...MEETING, recording: null } });
      const region = await loadedRegion();
      fireEvent.click(within(region).getByRole("button", { name: EN_TURNS.t4 }));
      expect(await screen.findByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");
      await waitFor(() => expect(document.getElementById("segment-seg-4")).toHaveFocus());
      expect(screen.queryByLabelText("Recording", { selector: "audio" })).not.toBeInTheDocument();
      expect(play).not.toHaveBeenCalled();
      expect(screen.getByRole("status", { name: "location" })).toHaveTextContent(/^$/);
    });
  });

  describe("turns to review (H1, H2, H3, A2)", () => {
    it("lists every turn with a review reason in sequence order, with lane, time and reasons", async () => {
      renderPage();
      const region = await loadedRegion();
      expect(within(region).getByRole("heading", { name: COPY.en.review })).toBeInTheDocument();
      expect(within(region).getAllByRole("button", { name: /^Go to turn at / })).toHaveLength(5);
      const go = ["0:00", "0:03", "0:10", "0:15", "0:31"].map((time) => within(region).getByRole("button", { name: COPY.en.goTo(time) }));
      for (let index = 1; index < go.length; index += 1) expect(before(go[index - 1], go[index])).toBe(true);
      const unknownItem = go[3].closest('li, [role="listitem"]');
      expect(unknownItem).not.toBeNull();
      expect(unknownItem!.textContent).toContain("Unknown speaker");
      expect(unknownItem!.textContent).toContain("0:15");
      expect(unknownItem!.textContent).toContain("unknown speaker");
      expect(unknownItem!.textContent).toContain("overlapping speech");
      const lastItem = go[4].closest('li, [role="listitem"]');
      expect(lastItem!.textContent).toContain("Agent");
      expect(lastItem!.textContent).toContain("after the recording ends");
      expect(within(region).queryByRole("button", { name: COPY.en.goTo("0:21") })).not.toBeInTheDocument();
    });

    it("hides the review list when no turn needs review", async () => {
      timelineNow = timeline({
        turns: [turn("seg-1", 1, "S1", 0, 4000), turn("seg-2", 2, "SPEAKER_01", 5000, 9000)],
        overlaps: [],
        overlap_ms: 0,
        unknown_speech_ms: 0,
      });
      renderPage();
      const region = await loadedRegion();
      expect(within(region).queryByRole("heading", { name: COPY.en.review })).not.toBeInTheDocument();
      expect(within(region).queryByRole("button", { name: /^Go to turn at / })).not.toBeInTheDocument();
      expect(within(region).queryByRole("button", { name: /^Correct speaker of turn at / })).not.toBeInTheDocument();
    });

    it("jumps from Go to turn like a turn button", async () => {
      const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
      renderPage();
      const audio = (await screen.findByLabelText("Recording", { selector: "audio" })) as HTMLAudioElement;
      const region = await loadedRegion();
      fireEvent.click(within(region).getByRole("button", { name: COPY.en.goTo("0:15") }));
      expect(await screen.findByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");
      await waitFor(() => expect(document.getElementById("segment-seg-4")).toHaveFocus());
      expect(audio.currentTime).toBe(15);
      expect(play).not.toHaveBeenCalled();
      expect(screen.getByRole("status", { name: "location" })).toHaveTextContent(/^$/);
    });

    it("names the review buttons in Persian", async () => {
      renderPage({ lang: "fa" });
      const region = await loadedRegion("fa");
      expect(within(region).getByRole("heading", { name: COPY.fa.review })).toBeInTheDocument();
      expect(within(region).getByRole("button", { name: "رفتن به نوبت ۰:۱۵" })).toBeInTheDocument();
      expect(within(region).getByRole("button", { name: "اصلاح گویندهٔ نوبت ۰:۱۵" })).toBeInTheDocument();
    });

    it("shows the correction button for an owner when the timeline is correctable", async () => {
      renderPage({ role: "owner" });
      const region = await loadedRegion();
      expect(within(region).getAllByRole("button", { name: /^Correct speaker of turn at / })).toHaveLength(5);
      expect(within(region).getByRole("button", { name: COPY.en.correct("0:15") })).toBeInTheDocument();
    });

    it("hides the correction button when the timeline is not correctable, but keeps Go to turn", async () => {
      timelineNow = timeline({ correction: { base_revision_id: "rev-4", correctable: false, speaker_choices: [] } });
      renderPage({ role: "owner" });
      const region = await loadedRegion();
      expect(within(region).getByRole("button", { name: COPY.en.goTo("0:15") })).toBeInTheDocument();
      expect(within(region).queryByRole("button", { name: /^Correct speaker of turn at / })).not.toBeInTheDocument();
    });

    it("hides the correction button from a role that cannot update the meeting", async () => {
      renderPage({ role: "viewer" });
      const region = await loadedRegion();
      expect(within(region).getByRole("button", { name: COPY.en.goTo("0:15") })).toBeInTheDocument();
      expect(within(region).queryByRole("button", { name: /^Correct speaker of turn at / })).not.toBeInTheDocument();
    });

    it("opens the existing correction form for the turn, and refetches the timeline after the save", async () => {
      const post = vi.spyOn(api, "post").mockImplementation(async () => {
        timelineNow = correctedTimeline();
        transcriptNow = correctedTranscript();
        return okResponse(correctedTranscript(), 201);
      });
      const { timelineCalls } = renderPage();
      const region = await loadedRegion();
      fireEvent.click(within(region).getByRole("button", { name: COPY.en.correct("0:15") }));
      expect(await screen.findByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");
      const article = await waitFor(() => {
        const node = document.getElementById("segment-seg-4");
        expect(node).not.toBeNull();
        expect(within(node!).getByLabelText("Turn text")).toBeInTheDocument();
        return node!;
      });
      expect(document.getElementById("segment-seg-3")!.querySelector("textarea, input")).toBeNull();
      const calls = timelineCalls();
      fireEvent.change(within(article).getByLabelText("Speaker"), { target: { value: "S1" } });
      fireEvent.change(within(article).getByLabelText("Reason for the correction"), { target: { value: "This is the agent" } });
      await act(async () => {
        fireEvent.click(within(article).getByRole("button", { name: "Save correction" }));
      });
      expect(post).toHaveBeenCalledWith("/meetings/m-1/transcript/corrections", {
        base_revision_id: "rev-4",
        reason: "This is the agent",
        edits: [{ segment_id: "seg-4", speaker_key: "S1" }],
      });
      await waitFor(() => expect(timelineCalls()).toBeGreaterThan(calls));
      const updated = screen.getByRole("region", { name: "Speaker timeline" });
      await waitFor(() => expect(within(updated).queryByRole("group", { name: "Unknown speaker" })).not.toBeInTheDocument());
      expect(within(within(updated).getByRole("group", { name: "Agent" })).getByRole("button", { name: turnName("en", "Agent", 15000, 20000) })).toBeInTheDocument();
    });

    it("refetches the timeline after a speaker rename", async () => {
      const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse({ ...SPEAKERS[0], display_name: "Agent Smith" }));
      const { timelineCalls } = renderPage();
      await loadedRegion();
      fireEvent.click(screen.getByRole("button", { name: "Transcript and tools" }));
      const input = await screen.findByLabelText("Rename Agent");
      const calls = timelineCalls();
      fireEvent.change(input, { target: { value: "Agent Smith" } });
      await act(async () => {
        fireEvent.click(within(input.closest("form")!).getByRole("button", { name: "Save speaker name" }));
      });
      expect(patch).toHaveBeenCalled();
      await waitFor(() => expect(timelineCalls()).toBeGreaterThan(calls));
    });
  });

  describe("cache cleanup (A3)", () => {
    it("forgets the timeline answer with the rest of the meeting content, and keeps another meeting's", () => {
      const queryClient = testQueryClient();
      const otherKey = ["platform", "meetings", "m-2", "transcript-timeline"];
      queryClient.setQueryData(TIMELINE_KEY, timeline());
      queryClient.setQueryData(otherKey, timeline({ meeting_id: "m-2" }));
      forgetMeetingContent(queryClient, "m-1");
      expect(queryClient.getQueryData(TIMELINE_KEY)).toBeUndefined();
      expect(queryClient.getQueryData(otherKey)).toBeDefined();
    });
  });
});
