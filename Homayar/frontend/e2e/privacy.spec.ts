import { expect, test, type Page } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";

/**
 * The privacy page and the meeting's data panel (HOM-190) against the demo
 * seed. The owner flow places a legal hold and releases it again. It notes
 * the holds that were already placed before it did anything; `afterEach`
 * releases only the holds the run added, through the API with the same owner
 * session. It never requests a deletion: releasing a hold that has a pending
 * deletion removes the file.
 */

/** A completed meeting of `backend/database/demo_seed.py` (index 9, plan "completed"). */
const MEETING_TITLE = "Customer portal login issue #10";
const DAY_MS = 24 * 60 * 60 * 1000;

interface LegalHoldPage {
  items: { media_id: string }[];
  next_cursor: string | null;
}

/**
 * Set once the owner flow has a signed-in page: the cleanup runs on that page
 * and leaves alone the holds that were placed before the run.
 */
let ownerSession: { page: Page; heldBefore: ReadonlySet<string> } | null = null;

/** The headers a signed-in console request carries (src/lib/api.ts). */
async function apiHeaders(page: Page): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((cookie) => cookie.name === "homayar_csrf")
    ?.value;
  const workspaceId = await page.evaluate(() =>
    window.localStorage.getItem("homayar_workspace_id"),
  );
  return {
    ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    ...(workspaceId ? { "X-Workspace-ID": workspaceId } : {}),
  };
}

async function meetingIdByTitle(page: Page): Promise<string> {
  const response = await page.request.get("/api/v1/meetings", {
    headers: await apiHeaders(page),
    params: { limit: 100, status: "completed" },
  });
  expect(response.ok(), "GET /meetings with the page's session").toBeTruthy();
  const { items } = (await response.json()) as { items: { id: string; title: string }[] };
  const meeting = items.find((item) => item.title === MEETING_TITLE);
  expect(meeting, `the seeded meeting "${MEETING_TITLE}"`).toBeDefined();
  return meeting!.id;
}

/** Every hold of the workspace, all pages. */
async function listHolds(page: Page): Promise<string[]> {
  const ids: string[] = [];
  let cursor: string | null = null;
  do {
    const response = await page.request.get("/api/v1/admin/legal-holds", {
      headers: await apiHeaders(page),
      params: { limit: 100, ...(cursor ? { cursor } : {}) },
    });
    expect(response.ok(), "GET /admin/legal-holds with the owner session").toBeTruthy();
    const body = (await response.json()) as LegalHoldPage;
    ids.push(...body.items.map((item) => item.media_id));
    cursor = body.next_cursor;
  } while (cursor);
  return ids;
}

test.afterEach(async () => {
  if (ownerSession === null) return;
  const { page, heldBefore } = ownerSession;
  ownerSession = null;
  // Even when a step failed: release every hold this run added. A failed
  // release is noted and the loop goes on, so one failure leaves no other
  // hold behind; the test fails once, after the loop.
  const failures: string[] = [];
  for (const mediaId of await listHolds(page)) {
    if (heldBefore.has(mediaId)) continue;
    const response = await page.request.post(`/api/v1/media/${mediaId}/legal-hold/release`, {
      headers: await apiHeaders(page),
      data: { reason: "e2e: release the hold after the test" },
    });
    if (!response.ok()) failures.push(`${mediaId}: HTTP ${response.status()}`);
  }
  expect(failures, "POST /media/{id}/legal-hold/release for the holds this run placed").toEqual([]);
});

/** Sets a DateField to a Gregorian day by typing into its segments (en-US: month, day, year). */
async function typeDay(page: Page, groupName: string, day: Date) {
  const group = page.getByRole("group", { name: groupName });
  await group.getByRole("spinbutton", { name: /month/i }).click();
  await page.keyboard.type(String(day.getUTCMonth() + 1).padStart(2, "0"));
  await page.keyboard.type(String(day.getUTCDate()).padStart(2, "0"));
  await page.keyboard.type(String(day.getUTCFullYear()));
}

async function chooseFilter(page: Page, name: string) {
  const register = page.getByRole("region", { name: "Legal holds" });
  await register.getByRole("radio", { name }).click();
  await expect(register.getByRole("radio", { name })).toBeChecked();
}

test("an owner places a legal hold, finds it in the register, releases it and exports a subject", async ({
  page,
}) => {
  await resumeSession(page, "owner");
  await page.goto(consolePath("/admin/privacy"));
  // The holds that exist before this run did anything; the cleanup keeps them.
  const heldBefore = await listHolds(page);
  ownerSession = { page, heldBefore: new Set(heldBefore) };
  // On the seeded workspace the register starts empty. A hold someone else
  // left behind is not this test's to touch, so the "empty" texts are only
  // checked when the run started from an empty register.
  const startedEmpty = heldBefore.length === 0;

  await expect(page.getByRole("heading", { level: 1, name: "Privacy" })).toBeVisible();
  const register = page.getByRole("region", { name: "Legal holds" });
  const row = register.getByRole("row").filter({ has: page.getByRole("link", { name: MEETING_TITLE }) });
  if (startedEmpty) await expect(register.getByText("Nothing is on legal hold.")).toBeVisible();
  else await expect(register.getByRole("grid", { name: "Recordings on legal hold" })).toBeVisible();
  await expect(row, "the meeting is not on hold before the run").toHaveCount(0);

  // Place a hold on the seeded meeting, reviewed in about a week.
  const meetingId = await meetingIdByTitle(page);
  await page.goto(consolePath(`/meetings/${meetingId}`));
  await expect(page.getByRole("heading", { level: 1, name: MEETING_TITLE })).toBeVisible();
  const panel = page.getByRole("region", { name: "Data and retention" });
  await expect(panel.getByRole("grid", { name: "Stored recordings" })).toBeVisible();
  await panel.getByRole("button", { name: "Place legal hold" }).click();

  const holdDialog = page.getByRole("dialog", { name: "Place a legal hold" });
  await holdDialog.getByRole("textbox", { name: "Reason" }).fill("e2e: litigation hold");
  await typeDay(page, "Review date", new Date(Date.now() + 7 * DAY_MS));
  await holdDialog.getByRole("button", { name: "Place hold" }).click();
  await expect(page.getByText("The legal hold was placed.")).toBeVisible();
  await expect(holdDialog).toHaveCount(0);
  // Every live object of the meeting is held now.
  await expect(panel.getByText("On legal hold").first()).toBeVisible();
  await expect(panel.getByText("Active")).toHaveCount(0);

  // The register lists it under All holds and Due in 30 days, not under Review due.
  await page.goto(consolePath("/admin/privacy"));
  await expect(row.first()).toBeVisible();
  await expect(row.first().getByText("e2e: litigation hold")).toBeVisible();

  await chooseFilter(page, "Due in 30 days");
  await expect(row.first()).toBeVisible();

  await chooseFilter(page, "Review due");
  if (startedEmpty) await expect(register.getByText("No hold is due for review.")).toBeVisible();
  await expect(row).toHaveCount(0);

  // Release every held object from the register.
  await chooseFilter(page, "All holds");
  await expect(row.first()).toBeVisible();
  const held = await row.count();
  for (let index = 0; index < held; index += 1) {
    await row.first().getByRole("button", { name: "Release" }).click();
    const releaseDialog = page.getByRole("dialog", { name: "Release the legal hold" });
    await releaseDialog.getByRole("textbox", { name: "Reason" }).fill("e2e: matter closed");
    await releaseDialog.getByRole("button", { name: "Release hold" }).click();
    await expect(releaseDialog).toHaveCount(0);
    await expect(row).toHaveCount(held - index - 1);
  }
  if (startedEmpty) await expect(register.getByText("Nothing is on legal hold.")).toBeVisible();

  // Subject export: the warning comes first, then the file downloads.
  const exportCard = page.getByRole("region", { name: "Data subject export" });
  await exportCard.getByRole("textbox", { name: "Identifier" }).fill("owner@demo.example.com");
  await exportCard.getByRole("textbox", { name: "Identifier" }).press("Enter");
  await expect(exportCard.getByRole("grid", { name: "Identifiers to export" })).toContainText(
    "owner@demo.example.com",
  );
  await exportCard.getByRole("textbox", { name: "Reason" }).fill("e2e: data subject request");
  await exportCard.getByRole("button", { name: "Export data" }).click();
  const warning = page.getByRole("alertdialog", { name: "This file contains personal data" });
  await expect(warning).toBeVisible();
  const download = page.waitForEvent("download");
  await warning.getByRole("button", { name: "Download export" }).click();
  expect((await download).suggestedFilename()).toMatch(/^homayar-subject-export-.*\.json$/);
  await expect(page.getByText("The export was downloaded.")).toBeVisible();
});

test("an admin gets no access to privacy and sees no data panel on a meeting", async ({ page }) => {
  await resumeSession(page, "admin");
  const mediaRequests: string[] = [];
  page.on("request", (request) => {
    if (/\/api\/v1\/meetings\/[^/]+\/media(\?|$)/.test(request.url())) mediaRequests.push(request.url());
  });

  await page.goto(consolePath("/admin/privacy"));
  await expect(page.getByRole("heading", { name: "No access" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Privacy" })).toHaveCount(0);

  const meetingId = await meetingIdByTitle(page);
  await page.goto(consolePath(`/meetings/${meetingId}`));
  await expect(page.getByRole("heading", { level: 1, name: MEETING_TITLE })).toBeVisible();
  // Every read of the page has settled, so a panel or its request would be here by now.
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("region", { name: "Data and retention" })).toHaveCount(0);
  await expect(page.getByText("Data and retention")).toHaveCount(0);
  expect(mediaRequests).toEqual([]);
});
