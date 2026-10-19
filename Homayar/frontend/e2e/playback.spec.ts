import { expect, test, type Page } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";

/**
 * Synced audio and transcript playback on the meeting page (HOM-247) against
 * the demo seed, read only. Seeded completed meetings each have a recording
 * and a transcript; ids differ in every database, so they are looked up at
 * run time.
 *
 * Nothing here may change the shared backend: every meeting request that is
 * not a GET is aborted in the browser, and the test fails if one was tried.
 */

interface Turn {
  id: string;
  speaker_key: string;
  start_ms: number;
  end_ms: number;
}

interface TranscriptAnswer {
  segments: Turn[];
  speakers: { speaker_key: string }[];
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

/** The first completed meeting, read with the page's session (a GET). */
async function completedMeetingId(page: Page): Promise<string> {
  const response = await page.request.get("/api/v1/meetings", {
    headers: await apiHeaders(page),
    params: { limit: 20, status: "completed" },
  });
  expect(response.ok(), "GET /meetings?status=completed").toBeTruthy();
  const { items } = (await response.json()) as { items: { id: string }[] };
  expect(items.length, "a seeded completed meeting").toBeGreaterThan(0);
  return items[0].id;
}

/** The meeting's unfiltered transcript (a GET), for turn ids and times. */
async function transcriptOf(page: Page, meetingId: string): Promise<TranscriptAnswer> {
  const response = await page.request.get(`/api/v1/meetings/${meetingId}/transcript`, { headers: await apiHeaders(page) });
  expect(response.ok(), "GET /meetings/{id}/transcript").toBeTruthy();
  const transcript = (await response.json()) as TranscriptAnswer;
  expect(transcript.segments.length, "a seeded transcript with turns").toBeGreaterThan(1);
  return transcript;
}

/** Lets only GETs reach the meetings API, and returns what else was tried. */
async function readOnly(page: Page) {
  const refused: string[] = [];
  await page.route("**/api/v1/meetings**", async (route) => {
    const request = route.request();
    if (request.method() === "GET") return route.continue();
    refused.push(`${request.method()} ${request.url()}`);
    return route.abort();
  });
  return refused;
}

/** Signs `role` in from its saved session and opens the meetings list, so the workspace is chosen. */
async function start(page: Page, lang: "fa" | "en" = "en") {
  await resumeSession(page, "owner", lang);
  await page.goto(consolePath("/meetings"));
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

const isTranscriptRequest = (url: string, meetingId: string) =>
  new URL(url).pathname === `/api/v1/meetings/${meetingId}/transcript`;

test.describe("synced playback on the meeting page (real backend, read only)", () => {
  let refused: string[] = [];

  test.afterEach(() => {
    expect(refused, "no meeting write may reach the shared backend").toEqual([]);
  });

  test("the owner plays from a turn, follows the audio, filters and uses the keys", async ({ page }) => {
    refused = await readOnly(page);
    await start(page);
    const meetingId = await completedMeetingId(page);
    const transcript = await transcriptOf(page, meetingId);
    await page.goto(consolePath(`/meetings/${meetingId}`));

    const audio = page.getByLabel("Recording", { exact: true }).and(page.locator("audio"));
    await expect(audio).toBeAttached();
    const shortcuts = page.getByRole("region", { name: "Keyboard shortcuts" });
    await expect(shortcuts).toBeVisible();
    for (const label of ["Play or pause", "Back 5 seconds", "Forward 5 seconds", "Next turn", "Previous turn"]) {
      await expect(shortcuts.getByText(label)).toBeVisible();
    }

    await page.getByRole("button", { name: "Transcript and tools" }).click();
    await expect(page.getByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");

    // Play from a turn that does not start at zero, so the seek is visible.
    const target = transcript.segments.find((turn) => turn.start_ms >= 1000) ?? transcript.segments[1];
    await page.locator(`#segment-${target.id}`).getByRole("button", { name: /^Play from \d+:\d{2}/ }).click();
    await expect(page).toHaveURL(new RegExp(`[?&]at=${target.start_ms}$`));
    await expect
      .poll(async () => audio.evaluate((element: HTMLAudioElement) => element.currentTime), { timeout: 10_000 })
      .toBeGreaterThanOrEqual(target.start_ms / 1000 - 0.5);
    expect(await audio.evaluate((element: HTMLAudioElement) => element.currentTime)).toBeLessThan(target.start_ms / 1000 + 5);

    // A seek to another turn's start moves the current-turn mark there.
    const other = transcript.segments.find((turn) => turn.id !== target.id && turn.end_ms - turn.start_ms >= 500)!;
    await audio.evaluate((element: HTMLAudioElement, ms: number) => {
      element.pause();
      element.currentTime = ms / 1000 + 0.2;
    }, other.start_ms);
    await expect(page.locator(`#segment-${other.id}`)).toHaveAttribute("aria-current", "true");
    await expect(page.locator(".meeting-segment[aria-current]")).toHaveCount(1);

    // K plays and pauses (the page body has focus, not a field).
    await page.locator("h1").click();
    const pausedBefore = await audio.evaluate((element: HTMLAudioElement) => element.paused);
    await page.keyboard.press("KeyK");
    await expect.poll(async () => audio.evaluate((element: HTMLAudioElement) => element.paused)).toBe(!pausedBefore);
    await page.keyboard.press("KeyK");
    await expect.poll(async () => audio.evaluate((element: HTMLAudioElement) => element.paused)).toBe(pausedBefore);

    // The speaker filter goes to the server; every turn shown is that speaker's.
    const speakerKey = transcript.speakers[transcript.speakers.length - 1].speaker_key;
    const filtered = page.waitForResponse(
      (response) =>
        isTranscriptRequest(response.url(), meetingId) &&
        new URL(response.url()).searchParams.getAll("speaker_key").join() === speakerKey,
    );
    await page.getByLabel("Show speaker").selectOption(speakerKey);
    const answer = (await (await filtered).json()) as TranscriptAnswer;
    expect(answer.segments.every((turn) => turn.speaker_key === speakerKey)).toBe(true);
    await expect(page.locator(".meeting-segment")).toHaveCount(answer.segments.length);
    const names = await page.locator(".meeting-segment strong").allTextContents();
    expect(new Set(names).size).toBeLessThanOrEqual(1);
    await expect(page.getByText(`Showing ${answer.segments.length} of ${transcript.segments.length} turns`)).toBeVisible();

    // A time range sends start_ms and end_ms.
    await page.getByRole("button", { name: "Clear filters" }).click();
    const ranged = page.waitForRequest((request) => {
      if (!isTranscriptRequest(request.url(), meetingId)) return false;
      const params = new URL(request.url()).searchParams;
      return params.get("start_ms") === "5000" && params.get("end_ms") === "30000";
    });
    await page.getByLabel("From", { exact: true }).fill("0:05");
    await page.getByLabel("To", { exact: true }).fill("0:30");
    await page.getByRole("button", { name: "Apply time range" }).click();
    expect(new URL((await ranged).url()).searchParams.has("speaker_key")).toBe(false);
    await expect(page.getByText(/^Showing \d+ of \d+ turns$/)).toBeVisible();

    // An inverted range sends nothing and says why.
    await page.getByLabel("From", { exact: true }).fill("0:30");
    await page.getByLabel("To", { exact: true }).fill("0:05");
    await page.getByRole("button", { name: "Apply time range" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "The end time must be after the start time." })).toBeVisible();
  });

  test("the Persian page is right to left with Persian shortcut labels", async ({ page }) => {
    refused = await readOnly(page);
    await start(page, "fa");
    const meetingId = await completedMeetingId(page);
    await page.goto(consolePath(`/meetings/${meetingId}`));
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    const shortcuts = page.getByRole("region", { name: "کلیدهای میان‌بر" });
    await expect(shortcuts).toBeVisible();
    for (const label of ["پخش یا توقف", "۵ ثانیه عقب", "۵ ثانیه جلو", "بخش بعدی", "بخش قبلی"]) {
      await expect(shortcuts.getByText(label)).toBeVisible();
    }
    await expect(shortcuts.locator("kbd[dir=ltr]")).toHaveCount(5);
  });

  for (const [status, code, message] of [
    [410, "recording_retention_expired", "This recording is no longer available. The workspace retention period has passed."],
    [404, "recording_not_found", "This recording is being deleted and can no longer be played."],
  ] as const) {
    test(`a ${status} playback answer shows its message and no player`, async ({ page }) => {
      refused = await readOnly(page);
      // Registered after readOnly, so it answers the playback route first.
      await page.route("**/api/v1/meetings/*/playback", (route) =>
        route.fulfill({ status, contentType: "application/json", body: JSON.stringify({ detail: { code, message: code } }) }),
      );
      await start(page);
      const meetingId = await completedMeetingId(page);
      await page.goto(consolePath(`/meetings/${meetingId}`));
      await expect(page.getByRole("region", { name: "Summary", exact: true })).toBeVisible();
      await expect(page.getByRole("status").filter({ hasText: message })).toBeVisible();
      await expect(page.locator("audio")).toHaveCount(0);
      await expect(page.getByRole("button", { name: /^Play evidence at / })).toHaveCount(0);
      await expect(page.getByRole("region", { name: "Keyboard shortcuts" })).toHaveCount(0);
    });
  }
});
