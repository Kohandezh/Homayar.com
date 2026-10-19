import { expect, test, type Page } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";

/**
 * The meeting page as a call report (HOM-245, WP12-T02) against the demo
 * seed, read only. The seed has 20 completed meetings (each with a recording,
 * evidence-backed findings and the `mock_analysis` notice) and 3 failed ones.
 * Ids differ in every database, so the meetings are looked up at run time.
 *
 * Nothing here may change the shared backend: every meeting request that is
 * not a GET is aborted in the browser, and the test fails if one was tried.
 */

const SECTIONS = ["Report completeness", "Summary", "Outcome", "QA findings", "Review actions"];

/** The headers a signed-in console request carries (src/lib/api.ts). */
async function apiHeaders(page: Page): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((cookie) => cookie.name === "homayar_csrf")?.value;
  const workspaceId = await page.evaluate(() => window.localStorage.getItem("homayar_workspace_id"));
  return {
    ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    ...(workspaceId ? { "X-Workspace-ID": workspaceId } : {}),
  };
}

/** The first meeting of one status, read with the page's session (a GET). */
async function firstMeetingId(page: Page, status: "completed" | "failed"): Promise<string> {
  const response = await page.request.get("/api/v1/meetings", {
    headers: await apiHeaders(page),
    params: { limit: 20, status },
  });
  expect(response.ok(), `GET /meetings?status=${status}`).toBeTruthy();
  const { items } = (await response.json()) as { items: { id: string }[] };
  expect(items.length, `a seeded ${status} meeting`).toBeGreaterThan(0);
  return items[0].id;
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
async function start(page: Page, role: "owner" | "viewer", lang: "fa" | "en" = "en") {
  await resumeSession(page, role, lang);
  await page.goto(consolePath("/meetings"));
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

test.describe("call report on the meeting page (real backend, read only)", () => {
  let refused: string[] = [];

  test.afterEach(() => {
    expect(refused, "no meeting write may reach the shared backend").toEqual([]);
  });

  test("the owner reads a completed meeting's report and plays its evidence", async ({ page }) => {
    refused = await readOnly(page);
    await start(page, "owner");
    const meetingId = await firstMeetingId(page, "completed");
    await page.goto(consolePath(`/meetings/${meetingId}`));

    const regions = SECTIONS.map((name) => page.getByRole("region", { name, exact: true }));
    for (const region of regions) await expect(region).toBeVisible();
    const inOrder = await page.evaluate((names) => {
      const found = names.map((name) =>
        [...document.querySelectorAll("section[aria-labelledby]")].find(
          (section) => document.getElementById(section.getAttribute("aria-labelledby")!)?.textContent === name,
        ),
      );
      return found.every((section, index) =>
        index === 0 || Boolean(found[index - 1]!.compareDocumentPosition(section!) & Node.DOCUMENT_POSITION_FOLLOWING),
      );
    }, SECTIONS);
    expect(inOrder, "the five sections in report order").toBe(true);
    await expect(regions[0].getByText("This analysis was made by a test (mock) provider.")).toBeVisible();

    // The advanced panels stay closed on first load.
    await expect(page.getByRole("button", { name: "Transcript and tools" })).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByRole("tablist", { name: "Meeting results" })).toHaveCount(0);

    const qa = page.getByRole("region", { name: "QA findings", exact: true });
    const play = qa.getByRole("button", { name: /^Play evidence at \d+:\d{2}$/ }).first();
    await expect(play).toBeVisible();
    await play.click();
    await expect(page).toHaveURL(/[?&]at=\d+$/);
    const atMs = Number(new URL(page.url()).searchParams.get("at"));
    const audio = page.getByLabel("Recording", { exact: true }).and(page.locator("audio"));
    await expect
      .poll(async () => audio.evaluate((element: HTMLAudioElement) => element.currentTime), { timeout: 10_000 })
      .toBeGreaterThanOrEqual(atMs / 1000 - 0.5);
    expect(await audio.evaluate((element: HTMLAudioElement) => element.currentTime)).toBeLessThan(atMs / 1000 + 5);
    // A play click keeps the user at the finding: the advanced area stays closed.
    await expect(page.getByRole("tablist", { name: "Meeting results" })).toHaveCount(0);

    await qa.getByRole("button", { name: "Show in transcript" }).first().click();
    await expect(page.getByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");
    await expect(page.locator(".meeting-segment:focus")).toHaveCount(1);
  });

  test("a failed meeting says its report is incomplete", async ({ page }) => {
    refused = await readOnly(page);
    await start(page, "owner");
    const meetingId = await firstMeetingId(page, "failed");
    await page.goto(consolePath(`/meetings/${meetingId}`));
    const notices = page.getByRole("region", { name: "Report completeness" });
    await expect(notices.getByText("Processing failed. This report is incomplete.")).toBeVisible();
    await expect(page.getByRole("region", { name: "Summary", exact: true })).toBeVisible();
  });

  test("the viewer cannot correct the transcript", async ({ page }) => {
    refused = await readOnly(page);
    await start(page, "viewer");
    const meetingId = await firstMeetingId(page, "completed");
    await page.goto(consolePath(`/meetings/${meetingId}`));
    const actions = page.getByRole("region", { name: "Review actions" });
    await expect(actions.getByRole("button", { name: "Correct transcript" })).toBeDisabled();
    await expect(actions.getByText("Your role cannot do this.").first()).toBeVisible();
  });

  test("the report is right to left in Persian", async ({ page }) => {
    refused = await readOnly(page);
    await start(page, "owner", "fa");
    const meetingId = await firstMeetingId(page, "completed");
    await page.goto(consolePath(`/meetings/${meetingId}`));
    await expect(page.getByRole("region", { name: "خلاصه", exact: true })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.getByRole("region", { name: "یافته‌های کنترل کیفیت" })).toBeVisible();
  });
});
