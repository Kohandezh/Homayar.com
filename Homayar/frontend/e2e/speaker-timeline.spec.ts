import { expect, test, type Page } from "@playwright/test";
import { consolePath, resumeSession, type DemoRole, type Lang } from "./demo";
import type { SpeakerTimeline } from "../src/types/timeline";

/**
 * The speaker timeline on the meeting page (HOM-264), read only. The meeting
 * is `Quarterly pricing review #254` (index 253 of the console plan, HOM-365),
 * which no other spec uses; its id is looked up by title at run time.
 *
 * The first test reads the real `GET .../transcript/timeline`. The demo seed
 * only has anonymous lanes, no overlap and no unknown speaker, so the other
 * tests answer that route in the browser with a body in the ADR 0069 shape,
 * built from the meeting's real transcript segment ids. The transcript, the
 * player and the correction form stay real.
 *
 * Nothing here may write to the shared backend: every API request that is not
 * a GET is aborted in the browser and recorded, and the test fails if one was
 * tried. The correction form is opened but never saved.
 */

const MEETING_TITLE = "Quarterly pricing review #254";

/** The identified speaker's stored name in the mocked timeline. */
const IDENTIFIED_NAME = "Neda Karimi";

interface TranscriptResponse {
  revision: { id: string; is_mock: boolean } | null;
  segments: { id: string; sequence: number }[];
}

/** Aborts every write to the API; returns the list of what it stopped. */
async function blockApiWrites(page: Page) {
  const blocked: string[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    if (request.method() === "GET" || request.method() === "HEAD" || request.method() === "OPTIONS") {
      await route.fallback();
      return;
    }
    blocked.push(`${request.method()} ${new URL(request.url()).pathname}`);
    await route.abort();
  });
  return blocked;
}

/** The headers a signed-in console request carries (src/lib/api.ts). */
async function apiHeaders(page: Page): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((cookie) => cookie.name === "homayar_csrf")?.value;
  const workspaceId = await page.evaluate(() => window.localStorage.getItem("homayar_workspace_id"));
  return {
    ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    ...(workspaceId ? { "X-Workspace-ID": workspaceId } : {}),
  };
}

/** Finds the seeded meeting by its exact title through the completed list. */
async function findMeeting(page: Page): Promise<string> {
  const response = await page.request.get("/api/v1/meetings", {
    headers: await apiHeaders(page),
    params: { limit: 50, status: "completed" },
  });
  expect(response.ok(), "GET /meetings?status=completed").toBeTruthy();
  const { items } = (await response.json()) as { items: { id: string; title: string }[] };
  // Other specs read the first completed meeting; this one must stay apart.
  expect(items[0]?.title, `${MEETING_TITLE} must not be the first completed meeting`).not.toBe(MEETING_TITLE);
  const meeting = items.find((item) => item.title === MEETING_TITLE);
  expect(meeting, `the seeded meeting "${MEETING_TITLE}"`).toBeTruthy();
  return meeting!.id;
}

async function getJson<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`/api/v1${path}`, { headers: await apiHeaders(page) });
  expect(response.ok(), `GET ${path}`).toBeTruthy();
  return (await response.json()) as T;
}

/**
 * A timeline in the ADR 0069 shape on the meeting's first four real segments:
 * an identified lane with two turns that overlap each other (`same_speaker`,
 * never drawn), an anonymous lane, and an unknown lane whose turn overlaps
 * the anonymous one (`cross_speaker`). Times are whole seconds so the shown
 * `m:ss` values are exact.
 */
function mockedTimeline(meetingId: string, transcript: TranscriptResponse): SpeakerTimeline {
  const ids = [...transcript.segments].sort((first, second) => first.sequence - second.sequence).map((segment) => segment.id);
  expect(ids.length, "the meeting has at least four transcript segments").toBeGreaterThanOrEqual(4);
  expect(transcript.revision, "the transcript has a current revision").not.toBeNull();
  const [s0, s1, s2, s3] = ids as [string, string, string, string];
  const turn = (
    segmentId: string,
    sequence: number,
    speakerKey: string,
    startMs: number,
    endMs: number,
    overlapIds: number[],
    reviewReasons: SpeakerTimeline["turns"][number]["review_reasons"],
  ): SpeakerTimeline["turns"][number] => ({
    segment_id: segmentId,
    sequence,
    speaker_key: speakerKey,
    start_ms: startMs,
    end_ms: endMs,
    confidence: speakerKey === "speaker_unknown" ? null : 0.9,
    attribution: speakerKey === "speaker_unknown" ? "none" : "diarization",
    overlap_ids: overlapIds,
    review_reasons: reviewReasons,
  });
  return {
    meeting_id: meetingId,
    revision: {
      id: transcript.revision!.id,
      revision_number: 1,
      source: "asr",
      is_current: true,
      diarization_status: "available",
      diarization_provider: "demo_seed_diarization",
      is_mock: true,
    },
    overlap_basis: "turns",
    recording_duration_ms: 31_000,
    timeline_end_ms: 31_000,
    lanes: [
      {
        speaker_key: "SPEAKER_00",
        display_name: IDENTIFIED_NAME,
        kind: "identified",
        identity_type: "employee",
        speaker_confidence: 0.8,
        turn_count: 2,
        speech_ms: 15_000,
        overlap_ms: 0,
      },
      {
        speaker_key: "SPEAKER_01",
        display_name: null,
        kind: "anonymous",
        identity_type: "unknown",
        speaker_confidence: null,
        turn_count: 1,
        speech_ms: 8_000,
        overlap_ms: 1_000,
      },
      {
        speaker_key: "speaker_unknown",
        display_name: null,
        kind: "unknown",
        identity_type: null,
        speaker_confidence: null,
        turn_count: 1,
        speech_ms: 9_000,
        overlap_ms: 1_000,
      },
    ],
    turns: [
      turn(s0, 0, "SPEAKER_00", 0, 8_000, [0], []),
      turn(s1, 1, "SPEAKER_00", 7_000, 15_000, [0], []),
      turn(s2, 2, "SPEAKER_01", 15_000, 23_000, [1], ["overlap"]),
      turn(s3, 3, "speaker_unknown", 22_000, 31_000, [1], ["unknown_speaker", "overlap"]),
    ],
    overlaps: [
      {
        index: 0,
        start_ms: 7_000,
        end_ms: 8_000,
        kind: "same_speaker",
        speaker_keys: ["SPEAKER_00"],
        segment_ids: [s0, s1],
        includes_unknown: false,
      },
      {
        index: 1,
        start_ms: 22_000,
        end_ms: 23_000,
        kind: "cross_speaker",
        speaker_keys: ["SPEAKER_01", "speaker_unknown"],
        segment_ids: [s2, s3],
        includes_unknown: true,
      },
    ],
    overlap_ms: 1_000,
    unknown_speech_ms: 9_000,
    confidence: { origin: "asr", calibrated: false },
    correction: {
      base_revision_id: transcript.revision!.id,
      correctable: true,
      speaker_choices: ["SPEAKER_00", "SPEAKER_01", "speaker_unknown"],
    },
  };
}

/**
 * Signs `role` in, blocks API writes, finds the meeting and, when `mock` is
 * set, answers its timeline route with `mockedTimeline`. Returns what the
 * test needs, including the list of blocked writes.
 */
async function openMeeting(page: Page, role: DemoRole, lang: Lang, mock: boolean) {
  await resumeSession(page, role, lang);
  const blocked = await blockApiWrites(page);
  await page.goto(consolePath("/meetings"));
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const meetingId = await findMeeting(page);
  const transcript = await getJson<TranscriptResponse>(page, `/meetings/${meetingId}/transcript`);
  const timeline = mock
    ? mockedTimeline(meetingId, transcript)
    : await getJson<SpeakerTimeline>(page, `/meetings/${meetingId}/transcript/timeline`);
  if (mock) {
    // Registered after blockApiWrites, so it answers the timeline GET first.
    await page.route(`**/api/v1/meetings/${meetingId}/transcript/timeline*`, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(timeline) })
        : route.fallback(),
    );
  }
  await page.goto(consolePath(`/meetings/${meetingId}`));
  return { blocked, timeline };
}

let blocked: string[] = [];

test.beforeEach(() => {
  blocked = [];
});

test.afterEach(() => {
  expect(blocked, "no API write may reach the shared backend").toEqual([]);
});

test("the real timeline shows one lane per speaker on an RTL page with an LTR track", async ({ page }) => {
  const opened = await openMeeting(page, "owner", "fa", false);
  blocked = opened.blocked;
  const { timeline } = opened;
  expect(timeline.lanes.length, "the real timeline has lanes").toBeGreaterThan(0);

  const region = page.getByRole("region", { name: "خط زمانی گوینده‌ها", exact: true });
  await expect(region).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(region.getByRole("group")).toHaveCount(timeline.lanes.length);
  for (const lane of timeline.lanes) {
    // The seed's lanes are anonymous: the label is the key as sent (ADR 0069 D4).
    const label = lane.kind === "unknown" ? "گویندهٔ نامشخص" : (lane.display_name ?? lane.speaker_key);
    await expect(region.getByRole("group", { name: label, exact: true })).toBeVisible();
  }
  await expect(region.locator("button.speaker-timeline-turn")).toHaveCount(timeline.turns.length);
  await expect(region.locator(".speaker-timeline-scroll")).toHaveAttribute("dir", "ltr");
});

test("overlap, unknown and review turns render from the contract in Persian", async ({ page }) => {
  ({ blocked } = await openMeeting(page, "owner", "fa", true));
  const region = page.getByRole("region", { name: "خط زمانی گوینده‌ها", exact: true });
  await expect(region).toBeVisible();

  await expect(region.getByRole("group")).toHaveCount(3);
  await expect(region.getByRole("group", { name: IDENTIFIED_NAME, exact: true })).toBeVisible();
  await expect(region.getByRole("group", { name: "SPEAKER_01", exact: true })).toBeVisible();
  const unknownLane = region.getByRole("group", { name: "گویندهٔ نامشخص", exact: true });
  await expect(unknownLane).toBeVisible();
  await expect(unknownLane.getByRole("button")).toHaveAccessibleName(
    "گویندهٔ نامشخص، ۰:۲۲ تا ۰:۳۱، نیاز به بررسی: گوینده نامشخص، هم‌زمانی گفتار",
  );

  // Only the cross_speaker overlap is drawn; the same_speaker one is not (ADR 0069 D3).
  const overlaps = region.getByRole("list", { name: "هم‌زمانی گفتار", exact: true });
  await expect(overlaps.getByRole("listitem")).toHaveCount(1);
  await expect(overlaps.getByRole("listitem")).toHaveAccessibleName("هم‌زمانی از ۰:۲۲ تا ۰:۲۳: SPEAKER_01، گویندهٔ نامشخص");

  const review = region.getByRole("region", { name: "نوبت‌های نیازمند بررسی", exact: true });
  await expect(review.getByRole("listitem")).toHaveCount(2);
  await expect(review.getByRole("button", { name: "رفتن به نوبت ۰:۱۵", exact: true })).toBeVisible();
  await expect(review.getByRole("button", { name: "رفتن به نوبت ۰:۲۲", exact: true })).toBeVisible();
  await expect(region.getByText("دادهٔ نمایشی", { exact: true })).toBeVisible();
});

test("the keyboard walks one lane's turns and Enter opens the transcript on the turn", async ({ page }) => {
  const opened = await openMeeting(page, "owner", "fa", true);
  blocked = opened.blocked;
  const s1 = opened.timeline.turns[1]!;
  const region = page.getByRole("region", { name: "خط زمانی گوینده‌ها", exact: true });
  const lane = region.getByRole("group", { name: IDENTIFIED_NAME, exact: true });
  const first = lane.getByRole("button", { name: `${IDENTIFIED_NAME}، ۰:۰۰ تا ۰:۰۸`, exact: true });
  const second = lane.getByRole("button", { name: `${IDENTIFIED_NAME}، ۰:۰۷ تا ۰:۱۵`, exact: true });
  const nextLaneTurn = region
    .getByRole("group", { name: "SPEAKER_01", exact: true })
    .getByRole("button", { name: "SPEAKER_01، ۰:۱۵ تا ۰:۲۳، نیاز به بررسی: هم‌زمانی گفتار", exact: true });
  await expect(first).toBeVisible();

  // Start the Tab order at the heading, so the next Tab enters the first lane.
  await region.getByRole("heading", { name: "خط زمانی گوینده‌ها", exact: true }).click();
  await page.keyboard.press("Tab");
  await expect(first).toBeFocused();
  // One tab stop per lane: the next Tab skips the lane's second turn.
  await page.keyboard.press("Tab");
  await expect(nextLaneTurn).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(first).toBeFocused();

  await page.keyboard.press("ArrowRight");
  await expect(second).toBeFocused();
  await page.keyboard.press("ArrowLeft");
  await expect(first).toBeFocused();
  await page.keyboard.press("End");
  await expect(second).toBeFocused();
  await page.keyboard.press("Home");
  await expect(first).toBeFocused();

  await page.keyboard.press("ArrowRight");
  await expect(second).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("tab", { name: "متن گفت‌وگو" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(`[id="segment-${s1.segment_id}"]`)).toBeFocused();
});

test("the timeline is in English with English turn and overlap names", async ({ page }) => {
  ({ blocked } = await openMeeting(page, "owner", "en", true));
  const region = page.getByRole("region", { name: "Speaker timeline", exact: true });
  await expect(region).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");

  const reviewTurn = region.getByRole("button", {
    name: "Unknown speaker, 0:22 to 0:31, needs review: unknown speaker, overlapping speech",
    exact: true,
  });
  await expect(reviewTurn).toBeVisible();
  await expect(reviewTurn).toHaveAccessibleName(
    "Unknown speaker, 0:22 to 0:31, needs review: unknown speaker, overlapping speech",
  );
  const overlaps = region.getByRole("list", { name: "Overlapping speech", exact: true });
  await expect(overlaps.getByRole("listitem")).toHaveCount(1);
  await expect(overlaps.getByRole("listitem")).toHaveAccessibleName("Overlap from 0:22 to 0:23: SPEAKER_01, Unknown speaker");
  await expect(region.getByRole("group", { name: "Unknown speaker", exact: true })).toBeVisible();
});

test("the owner opens the existing correction form from a review turn, without saving", async ({ page }) => {
  const opened = await openMeeting(page, "owner", "en", true);
  blocked = opened.blocked;
  const unknownTurn = opened.timeline.turns[3]!;
  const review = page
    .getByRole("region", { name: "Speaker timeline", exact: true })
    .getByRole("region", { name: "Turns to review", exact: true });
  await review.getByRole("button", { name: "Correct speaker of turn at 0:22", exact: true }).click();

  await expect(page.getByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");
  const segment = page.locator(`[id="segment-${unknownTurn.segment_id}"]`);
  await expect(segment.getByLabel("Turn text")).toBeVisible();
  await expect(segment.getByLabel("Reason for the correction")).toBeVisible();
  await expect(segment.getByRole("button", { name: "Save correction" })).toBeVisible();
  // Read only: the form is closed again, nothing is saved.
  await segment.getByRole("button", { name: "Cancel" }).click();
  await expect(segment.getByLabel("Turn text")).toHaveCount(0);
});

test("a viewer sees the review turns but no Correct speaker button", async ({ page }) => {
  ({ blocked } = await openMeeting(page, "viewer", "en", true));
  const review = page
    .getByRole("region", { name: "Speaker timeline", exact: true })
    .getByRole("region", { name: "Turns to review", exact: true });
  await expect(review.getByRole("button", { name: "Go to turn at 0:22", exact: true })).toBeVisible();
  await expect(review.getByRole("button", { name: /^Correct speaker of turn at/ })).toHaveCount(0);
});
