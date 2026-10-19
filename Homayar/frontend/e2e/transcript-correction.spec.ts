import { expect, test, type Page } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";

/**
 * Transcript and speaker correction (HOM-246) against the demo seed. The
 * meeting is `Quarterly pricing review #8`, which no other spec uses; its id
 * is looked up at run time, and no revision number is assumed: each test reads
 * the current revision first and asserts relative to it.
 *
 * A correction creates a new revision whose segments have new ids, so the
 * tests track turns by `sequence` (stable across revisions). Revisions are
 * immutable: the cleanup cannot undo a correction, so it posts one more
 * correction per run that puts the touched turns' text and speaker back, and
 * the history keeps growing (like `ai-policy.spec.ts`).
 */

const MEETING_TITLE = "Quarterly pricing review #8";

interface RevisionInfo {
  id: string;
  revision_number: number;
  is_current: boolean;
}

interface SegmentInfo {
  id: string;
  sequence: number;
  speaker_key: string;
  text: string;
  start_ms: number;
}

interface TranscriptResponse {
  revision: RevisionInfo | null;
  segments: SegmentInfo[];
  speakers: { speaker_key: string; display_name: string | null }[];
}

/** The meeting this run works on, and the turn states it must restore. */
let meetingId = "";
const originals = new Map<number, { text: string; speakerKey: string }>();

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
  expect(items.length, "completed meetings in the seed").toBeGreaterThan(0);
  // The spec's guard: if #8 is items[0], this mission must stop and re-plan,
  // not silently pick another meeting.
  expect(items[0]!.title, "#8 must not be the first completed meeting").not.toBe(MEETING_TITLE);
  const meeting = items.find((item) => item.title === MEETING_TITLE);
  expect(meeting, `the seeded meeting "${MEETING_TITLE}"`).toBeTruthy();
  return meeting!.id;
}

async function readTranscript(page: Page): Promise<TranscriptResponse> {
  const response = await page.request.get(`/api/v1/meetings/${meetingId}/transcript`, {
    headers: await apiHeaders(page),
  });
  expect(response.ok(), "GET the transcript with the page's session").toBeTruthy();
  return (await response.json()) as TranscriptResponse;
}

/** Puts the text and speaker of every touched turn back, over the API. */
async function restoreOriginals(page: Page) {
  if (originals.size === 0) return;
  const applied = [...originals.entries()];
  const edits: { segment_id: string; text?: string; speaker_key?: string }[] = [];
  const current = await readTranscript(page);
  for (const [sequence, original] of applied) {
    const segment = current.segments.find((item) => item.sequence === sequence);
    if (!segment) continue;
    const edit: { segment_id: string; text?: string; speaker_key?: string } = { segment_id: segment.id };
    if (segment.text !== original.text) edit.text = original.text;
    if (segment.speaker_key !== original.speakerKey) edit.speaker_key = original.speakerKey;
    if (Object.keys(edit).length > 1) edits.push(edit);
  }
  originals.clear();
  if (edits.length === 0 || !current.revision) return;
  const response = await page.request.post(`/api/v1/meetings/${meetingId}/transcript/corrections`, {
    headers: await apiHeaders(page),
    data: {
      base_revision_id: current.revision.id,
      reason: "e2e: restore the transcript after the test",
      edits,
    },
  });
  expect(response.ok(), "POST the restore correction").toBeTruthy();
  // The cleanup's own proof: every touched turn is exactly as it started.
  const restored = await readTranscript(page);
  for (const [sequence, original] of applied) {
    const segment = restored.segments.find((item) => item.sequence === sequence);
    expect(segment, `the turn at sequence ${sequence} exists after the restore`).toBeTruthy();
    expect(segment!.text, `the turn at sequence ${sequence} has its text back`).toBe(original.text);
    expect(segment!.speaker_key, `the turn at sequence ${sequence} has its speaker back`).toBe(original.speakerKey);
  }
}

/** The turn's row in the transcript tab, by the sequence the API keeps stable. */
function turnRow(page: Page, sequence: number) {
  return page.locator(`.meeting-segment[data-sequence="${sequence}"]`);
}

test.afterEach(async ({ page }) => {
  // Runs even when a step failed, so the shared transcript text stays as it was.
  await restoreOriginals(page);
});

test("the owner corrects a turn, walks the history and sees the stale marker", async ({ page }) => {
  await resumeSession(page, "owner");
  await page.goto(consolePath("/meetings"));
  meetingId = await findMeeting(page);
  await page.goto(consolePath(`/meetings/${meetingId}`));

  const transcript = await readTranscript(page);
  expect(transcript.revision, "the transcript names its current revision").not.toBeNull();
  const revisionNumber = transcript.revision!.revision_number;
  const segment = transcript.segments[0]!;
  originals.set(segment.sequence, { text: segment.text, speakerKey: segment.speaker_key });
  const row = turnRow(page, segment.sequence);

  await page.getByRole("button", { name: "Transcript and tools" }).click();
  await expect(row).toBeVisible();

  // The Edit button is found inside the turn's own row, so the UI's rounding of
  // the start time never has to be recomputed here.
  await turnRow(page, segment.sequence).getByRole("button", { name: /^Edit turn at / }).click();
  await page.getByLabel("Turn text").fill(`${segment.text} (corrected)`);
  await page.getByLabel("Reason for the correction").fill("e2e: fix a wording error");
  await page.getByRole("button", { name: "Save correction" }).click();
  await expect(page.getByText(`Saved as revision ${revisionNumber + 1}.`, { exact: true })).toBeVisible();
  await expect(row).toContainText(`${segment.text} (corrected)`);

  await page.getByRole("button", { name: "Revision history" }).click();
  const drawer = page.getByRole("dialog", { name: "Revision history" });
  await expect(drawer).toBeVisible();
  // `(?!\d)` so revision 6 never matches revision 61 as the history grows.
  const newRow = drawer.locator(".revision-row").filter({ hasText: new RegExp(`Revision ${revisionNumber + 1}(?!\\d)`) });
  await expect(newRow).toContainText("Correction");
  await expect(newRow).toContainText("You");
  await expect(newRow).toContainText("e2e: fix a wording error");
  await expect(newRow.getByText("Current")).toBeVisible();

  await drawer.getByRole("button", { name: `View revision ${revisionNumber}` }).click();
  await expect(page.getByText(`You are viewing revision ${revisionNumber}. It is read only.`)).toBeVisible();
  await expect(row).toContainText(segment.text);
  await expect(page.getByRole("button", { name: /^Edit turn at/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Back to current" }).click();
  await expect(row).toContainText("(corrected)");

  // The report refetched after the correction, so its stale markers show without a reload.
  await expect(page.getByText("Based on an earlier transcript").first()).toBeVisible();
});

test("the owner moves one turn to another speaker", async ({ page }) => {
  await resumeSession(page, "owner");
  await page.goto(consolePath("/meetings"));
  meetingId = await findMeeting(page);
  await page.goto(consolePath(`/meetings/${meetingId}`));

  const transcript = await readTranscript(page);
  const segment = transcript.segments.find((item) => item.speaker_key === "SPEAKER_00") ?? transcript.segments[0]!;
  const other = transcript.speakers.find((speaker) => speaker.speaker_key !== segment.speaker_key);
  expect(other, "a second speaker to move the turn to").toBeTruthy();
  originals.set(segment.sequence, { text: segment.text, speakerKey: segment.speaker_key });
  const row = turnRow(page, segment.sequence);

  await page.getByRole("button", { name: "Transcript and tools" }).click();
  await expect(row).toBeVisible();

  await turnRow(page, segment.sequence).getByRole("button", { name: /^Edit turn at / }).click();
  await page.getByLabel("Speaker", { exact: true }).selectOption(other!.speaker_key);
  await page.getByLabel("Reason for the correction").fill("e2e: this turn belongs to the other speaker");
  await page.getByRole("button", { name: "Save correction" }).click();
  const revisionNumber = transcript.revision!.revision_number;
  await expect(page.getByText(`Saved as revision ${revisionNumber + 1}.`, { exact: true })).toBeVisible();
  await expect(row.locator("strong")).toHaveText(other!.display_name ?? other!.speaker_key);
});

test("a viewer reads the transcript and its history but cannot correct", async ({ page }) => {
  await resumeSession(page, "viewer");
  await page.goto(consolePath("/meetings"));
  meetingId = await findMeeting(page);
  await page.goto(consolePath(`/meetings/${meetingId}`));

  await page.getByRole("button", { name: "Transcript and tools" }).click();
  await expect(page.locator(".meeting-segment").first()).toBeVisible();
  await expect(page.getByRole("button", { name: /^Edit turn at/ })).toHaveCount(0);

  await page.getByRole("button", { name: "Revision history" }).click();
  const drawer = page.getByRole("dialog", { name: "Revision history" });
  await expect(drawer.getByText(/^(Automatic transcription|Correction|Backfill)$/).first()).toBeVisible();
});

test("the correction UI is Persian and the page is right to left", async ({ page }) => {
  await resumeSession(page, "owner", "fa");
  await page.goto(consolePath("/meetings"));
  meetingId = await findMeeting(page);
  await page.goto(consolePath(`/meetings/${meetingId}`));
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");

  await page.getByRole("button", { name: "متن گفت‌وگو و ابزارها" }).click();
  const segment = (await readTranscript(page)).segments[0]!;
  await expect(turnRow(page, segment.sequence)).toBeVisible();

  // Read only: the form and the drawer are opened, nothing is saved.
  await turnRow(page, segment.sequence).getByRole("button", { name: /^ویرایش بخش در / }).click();
  await expect(page.getByLabel("متن بخش")).toBeVisible();
  await expect(page.getByLabel("دلیل اصلاح")).toBeVisible();
  await expect(page.getByRole("button", { name: "ذخیرهٔ اصلاح" })).toBeVisible();
  await page.getByRole("button", { name: "انصراف" }).click();

  await page.getByRole("button", { name: "تاریخچهٔ نسخه‌ها" }).click();
  await expect(page.getByRole("dialog", { name: "تاریخچهٔ نسخه‌ها" })).toBeVisible();
});
