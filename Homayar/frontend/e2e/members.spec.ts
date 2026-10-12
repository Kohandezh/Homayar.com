import { expect, test, type Browser, type Locator, type Page } from "@playwright/test";
import { consolePath, DEMO_DISPLAY_NAMES, demoEmail, signIn } from "./demo";
import { invitationTokenFor } from "./smtp-sink";

/**
 * One member through the whole lifecycle: role change, removal, a fresh
 * invitation, and acceptance.
 *
 * The seeded administrator is the subject because no other flow depends on
 * them. The steps build on each other, hence `serial`. `afterAll` repairs the
 * workspace whatever happened, so a run that fails in the middle does not
 * leave the next run without a member or with a pending invitation.
 */
const TARGET = demoEmail("admin");
const TARGET_NAME = DEMO_DISPLAY_NAMES.admin;

const NO_MAIL = `No invitation mail for ${TARGET} was captured. Start the backend with
SMTP_HOST and SMTP_PORT pointing at the sink (see e2e/README.md).`;

function memberRow(page: Page): Locator {
  return page.getByRole("row").filter({ hasText: TARGET });
}

/** The role cell. The name sits in a `<th>`, which is a rowheader, not a cell. */
function roleCell(row: Locator): Locator {
  return row.getByRole("cell").first();
}

function invitationsRegion(page: Page): Locator {
  return page.getByRole("region", { name: "Invitations" });
}

function invitationRow(page: Page): Locator {
  return invitationsRegion(page).locator("li").filter({ hasText: TARGET }).first();
}

/**
 * Both lists have answered. `count()` on a list that is still loading reports
 * zero, which would send the repair below down the wrong path.
 */
async function waitForLists(page: Page) {
  // The directory header's count renders only after the members query answered.
  await expect(page.getByText(/^\d+ members?$/)).toBeVisible();
  const region = invitationsRegion(page);
  const settled = region.getByRole("listitem").or(region.getByText("No invitations yet"));
  await expect(settled.first()).toBeVisible();
}

async function openMembers(page: Page) {
  await signIn(page, "owner");
  await page.goto(consolePath("/admin/members"));
  await expect(page.getByRole("heading", { name: "Members and access", level: 1 })).toBeVisible();
  await waitForLists(page);
}

/** The select is only a draft until Apply (QA M-01). */
async function applyRole(page: Page, role: "admin" | "member") {
  const row = memberRow(page);
  await row.getByRole("combobox").selectOption(role);
  await row.getByRole("button", { name: "Apply" }).click();
}

async function sendInvite(page: Page) {
  const region = invitationsRegion(page);
  await region.getByLabel("Email address").fill(TARGET);
  await region.getByLabel("Role", { exact: true }).selectOption("admin");
  await region.getByRole("button", { name: "Send invite" }).click();
  await expect(region.getByText("Invitation sent.")).toBeVisible();
}

/**
 * Accept the newest invitation as the invited account, in its own browser
 * context so the owner stays signed in.
 *
 * The URL is rebuilt from the token instead of followed from the mail: the
 * mail points at the backend's FRONTEND_URL (port 5173), not at the server
 * Playwright started.
 */
async function acceptInvite(browser: Browser) {
  // The one-time token only exists in the invitation mail; the SMTP sink
  // started by the global setup captured it.
  const token = invitationTokenFor(TARGET);
  expect(token, NO_MAIL).not.toBeNull();

  const invited = await browser.newContext();
  try {
    const page = await invited.newPage();
    await signIn(page, "admin");
    await page.goto(consolePath(`/invitations/accept?token=${encodeURIComponent(token!)}`));
    await page.getByRole("button", { name: "Accept invitation" }).click();
    await expect(page.getByRole("heading", { name: "Invitation accepted" })).toBeVisible();
  } finally {
    await invited.close();
  }
}

/** A leftover pending invitation would make the next invite fail with 409. */
async function revokePendingInvite(page: Page) {
  const pending = invitationsRegion(page)
    .locator("li")
    .filter({ hasText: TARGET })
    .filter({ hasText: "Pending" });
  if ((await pending.count()) === 0) return;
  await pending.first().getByRole("button", { name: "Revoke" }).click();
  await expect(pending).toHaveCount(0);
}

/**
 * Put the demo workspace back the way the seed left it: the target is an
 * administrator, and no invitation of theirs is pending. Only the steps that
 * are actually needed run, so after a clean flow this is three assertions.
 */
async function restoreSeededMember(browser: Browser) {
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    await openMembers(page);
    await revokePendingInvite(page);

    if ((await memberRow(page).count()) === 0) {
      await sendInvite(page);
      await acceptInvite(browser);
      await page.reload();
      await waitForLists(page);
    }

    await expect(memberRow(page)).toHaveCount(1);
    const role = (await roleCell(memberRow(page)).textContent())?.trim();
    if (role !== "Administrator") {
      await applyRole(page, "admin");
      await expect(roleCell(memberRow(page))).toHaveText("Administrator");
    }
  } finally {
    await context.close();
  }
}

test.describe.serial("an owner manages a workspace member", () => {
  test.afterAll(async ({ browser }) => {
    // A repair after a failed run signs in twice, invites and accepts.
    test.setTimeout(180_000);
    await restoreSeededMember(browser);
  });

  test("changes a member's role", async ({ page }) => {
    await openMembers(page);
    const row = memberRow(page);
    await expect(roleCell(row)).toHaveText("Administrator");

    // Nothing is sent until Apply.
    await row.getByRole("combobox").selectOption("member");
    await expect(roleCell(row)).toHaveText("Administrator");

    await row.getByRole("button", { name: "Apply" }).click();
    await expect(roleCell(memberRow(page))).toHaveText("Member");

    await page.reload();
    await expect(roleCell(memberRow(page))).toHaveText("Member");

    // Back to the seeded role, so the removal below removes an administrator.
    await applyRole(page, "admin");
    await expect(roleCell(memberRow(page))).toHaveText("Administrator");
  });

  test("removes a member", async ({ page }) => {
    await openMembers(page);
    const row = memberRow(page);
    await expect(row).toHaveCount(1);

    await row.getByRole("button", { name: `Remove ${TARGET_NAME}`, exact: true }).click();
    await expect(row.getByRole("button", { name: "Cancel" })).toBeVisible();
    await row.getByRole("button", { name: "Remove member" }).click();

    await expect(memberRow(page)).toHaveCount(0);
    await page.reload();
    // The list has to answer first: an empty count during loading proves nothing.
    await waitForLists(page);
    await expect(memberRow(page)).toHaveCount(0);
  });

  test("invites the member back", async ({ page }) => {
    await openMembers(page);
    await sendInvite(page);

    const row = invitationRow(page);
    await expect(row).toContainText("Pending");
    await expect(row).toContainText("Administrator");
  });

  test("the invited account accepts and is a member again", async ({ page, browser }) => {
    await acceptInvite(browser);

    await openMembers(page);
    await expect(memberRow(page)).toHaveCount(1);
    await expect(roleCell(memberRow(page))).toHaveText("Administrator");

    // No invitation is left over: accepting closes it.
    await expect(invitationRow(page)).toContainText("Accepted");
  });
});
