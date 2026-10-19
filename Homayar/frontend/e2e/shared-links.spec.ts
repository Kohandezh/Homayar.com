import { expect, test, type Page } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";

/**
 * The shared-links register of the privacy page (HOM-259) against the demo
 * seed. Each run creates its own workspace share on the seeded meeting
 * through the API and revokes it again in `afterEach`; the route is
 * idempotent, so a share the test revoked itself answers 204 once more. It
 * never touches a share it did not create: the seed's own links stay as
 * they are.
 */

/** A completed meeting of `backend/database/demo_seed.py` (index 9, plan "completed"). */
const MEETING_TITLE = "Customer portal login issue #10";

interface CreatedShare {
  meetingId: string;
  shareId: string;
  token: string;
}

/** Set once the owner flow created its share: the cleanup runs on that page. */
let owner: { page: Page; share: CreatedShare } | null = null;

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

/**
 * Creates this run's own workspace share (24 h) on the seeded meeting and
 * registers it for cleanup at once: the id is stored as soon as it is
 * parsed, so a later step that throws cannot strand an active link.
 */
async function createWorkspaceShare(page: Page): Promise<CreatedShare> {
  const response = await page.request.get("/api/v1/meetings", {
    headers: await apiHeaders(page),
    params: { limit: 100, status: "completed" },
  });
  expect(response.ok(), "GET /meetings with the page's session").toBeTruthy();
  const { items } = (await response.json()) as { items: { id: string; title: string }[] };
  const meeting = items.find((item) => item.title === MEETING_TITLE);
  expect(meeting, `the seeded meeting "${MEETING_TITLE}"`).toBeDefined();
  const create = await page.request.post(`/api/v1/meetings/${meeting!.id}/shares`, {
    headers: await apiHeaders(page),
    data: { audience: "workspace", expires_in_hours: 24 },
  });
  expect(create.ok(), "POST /meetings/{id}/shares").toBeTruthy();
  const body = (await create.json()) as { id: string; url: string };
  const share: CreatedShare = {
    meetingId: meeting!.id,
    shareId: body.id,
    // The token is only an assertion helper; a missing url must not stop the cleanup.
    token: typeof body.url === "string" ? (body.url.split("/").filter(Boolean).pop() ?? "") : "",
  };
  owner = { page, share };
  return share;
}

test.afterEach(async () => {
  if (owner === null) return;
  const { page, share } = owner;
  owner = null;
  // Even when a step failed: revoke the share this run created. The route is
  // idempotent (204 also for an already revoked link); 404 means it is gone.
  const response = await page.request.delete(
    `/api/v1/meetings/${share.meetingId}/shares/${share.shareId}`,
    { headers: await apiHeaders(page) },
  );
  expect([204, 404], "DELETE the share this run created").toContain(response.status());
});

test("an owner finds the workspace share in the register and revokes it after a confirm", async ({
  page,
}) => {
  await resumeSession(page, "owner");
  // The API helpers read the session's storage, so the app has to load once.
  await page.goto(consolePath("/admin/privacy"));
  const share = await createWorkspaceShare(page);
  expect(share.token, "the share token, the last path segment of the url").toBeTruthy();

  // The register opens fresh, so the new link is on it from the start.
  await page.goto(consolePath("/admin/privacy"));
  await expect(page.getByRole("heading", { level: 1, name: "Privacy" })).toBeVisible();
  const card = page.getByRole("region", { name: "Shared links" });
  await expect(card).toBeVisible();
  // "Active" is the default filter; the new link is the row of this run.
  await expect(card.getByRole("radio", { name: "Active" })).toBeChecked();
  const row = card.locator(`[data-key="${share.shareId}"]`);
  await expect(row).toBeVisible();
  await expect(row.getByRole("link", { name: MEETING_TITLE })).toBeVisible();
  await expect(row.getByText("Organization members")).toBeVisible();
  await expect(row.getByText("Active")).toBeVisible();
  await expect(page.getByText(share.token), "the share token is never on the page").toHaveCount(0);
  expect(await page.content(), "the share token is not even in the page source").not.toContain(share.token);

  // Cancel sends nothing; the row stays.
  await row.getByRole("button", { name: "Revoke" }).click();
  const dialog = page.getByRole("alertdialog", { name: "Revoke this link?" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("bdi", { hasText: MEETING_TITLE })).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(row).toBeVisible();

  // Confirm revokes; the row leaves the Active list.
  await row.getByRole("button", { name: "Revoke" }).click();
  await dialog.getByRole("button", { name: "Revoke link" }).click();
  await expect(page.getByText("Link revoked.")).toBeVisible();
  await expect(dialog).toHaveCount(0);
  await expect(row, "the row leaves the Active list").toHaveCount(0);

  // The revoked link is in the register under Revoked, with no way back.
  await card.getByRole("radio", { name: "Revoked" }).click();
  const revokedRow = card.locator(`[data-key="${share.shareId}"]`);
  await expect(revokedRow).toBeVisible();
  await expect(revokedRow.getByText("Revoked")).toBeVisible();
  await expect(revokedRow.getByRole("button", { name: "Revoke" })).toHaveCount(0);
});

test("the shared-links card is right to left in Persian", async ({ page }) => {
  await resumeSession(page, "owner", "fa");
  await page.goto(consolePath("/admin/privacy"));

  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  const card = page.getByRole("region", { name: "لینک‌های اشتراک" });
  await expect(card).toBeVisible();
  for (const name of ["فعال", "منقضی‌شده", "لغوشده", "همهٔ لینک‌ها"]) {
    await expect(card.getByRole("radio", { name })).toBeVisible();
  }
});
