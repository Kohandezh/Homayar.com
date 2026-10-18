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

/** The page's selects speak in role labels; this flow needs two of them. */
const ROLE_LABEL = { admin: "Administrator", member: "Member" } as const;

type InvitationTab = "pending" | "accepted";

/** The accessible name of a state's tab, count included: "Pending (1)". */
const TAB_NAME: Record<InvitationTab, RegExp> = {
  pending: /^Pending \(/,
  accepted: /^Accepted \(/,
};

function memberRow(page: Page): Locator {
  return page.getByRole("row").filter({ hasText: TARGET });
}

/**
 * The role cell. The directory is a React Aria grid, so cells are
 * `gridcell`; the member's name sits in a rowheader, which is not one.
 */
function roleCell(row: Locator): Locator {
  return row.getByRole("gridcell").first();
}

function invitationsRegion(page: Page): Locator {
  return page.getByRole("region", { name: "Invitations" });
}

/** The panel of one invitation state. Only the selected tab's panel is mounted. */
function invitationsPanel(page: Page, tab: InvitationTab): Locator {
  return invitationsRegion(page).getByRole("tabpanel", { name: TAB_NAME[tab] });
}

function invitationsTab(page: Page, tab: InvitationTab): Locator {
  return invitationsRegion(page).getByRole("tab", { name: TAB_NAME[tab] });
}

/** A row sits in its state's tab, so that tab has to be selected first. */
async function selectInvitationsTab(page: Page, tab: InvitationTab) {
  await invitationsTab(page, tab).click();
  await expect(invitationsPanel(page, tab)).toBeVisible();
}

/**
 * The count a state's tab shows, read before a step that must change it: the
 * later assertion polls, but a count that has to grow needs the old value.
 */
async function invitationCount(page: Page, tab: InvitationTab): Promise<number> {
  const label = (await invitationsTab(page, tab).textContent()) ?? "";
  const count = Number(/\((\d+)\)/.exec(label)?.[1]);
  expect(Number.isInteger(count), `Tab label carries a count: ${label}`).toBe(true);
  return count;
}

function invitationRow(page: Page, tab: InvitationTab): Locator {
  return invitationsPanel(page, tab)
    .getByRole("listitem")
    .filter({ hasText: TARGET })
    .first();
}

/**
 * A HeroUI Select is a button trigger plus a popover portaled to the page, so
 * the option is never inside the scope that owns the trigger.
 */
async function chooseOption(page: Page, trigger: Locator, option: string) {
  await trigger.click();
  await page.getByRole("option", { name: option, exact: true }).click();
}

/**
 * Both lists have answered. `count()` on a list that is still loading reports
 * zero, which would send the repair below down the wrong path.
 */
async function waitForLists(page: Page) {
  // The directory header's count renders only after the members query answered.
  await expect(page.getByText(/^\d+ members?$/)).toBeVisible();
  // The tabs replace the invitations loading (and error) state, so a panel
  // proves that query answered; it is settled once the selected panel shows
  // either a row or the empty state.
  const panel = invitationsRegion(page).getByRole("tabpanel");
  const settled = panel.getByRole("listitem").or(panel.getByText("No invitation in this state"));
  await expect(settled.first()).toBeVisible();
}

async function openMembers(page: Page) {
  await signIn(page, "owner");
  await page.goto(consolePath("/admin/members"));
  await expect(page.getByRole("heading", { name: "Members and access", level: 1 })).toBeVisible();
  await waitForLists(page);
}

/** Picks a role in the target row's select: a draft until Apply (QA M-01). */
async function chooseRowRole(page: Page, role: keyof typeof ROLE_LABEL) {
  const row = memberRow(page);
  await chooseOption(page, row.getByRole("button", { name: `Role for ${TARGET_NAME}` }), ROLE_LABEL[role]);
}

async function applyRole(page: Page, role: keyof typeof ROLE_LABEL) {
  await chooseRowRole(page, role);
  await memberRow(page).getByRole("button", { name: "Apply" }).click();
}

async function sendInvite(page: Page) {
  const region = invitationsRegion(page);
  await region.getByRole("button", { name: "Invite member" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Email address").fill(TARGET);
  // The form defaults to Member; this invitation has to carry Administrator.
  await chooseOption(page, dialog.getByRole("button", { name: /Role$/ }), ROLE_LABEL.admin);
  await dialog.getByRole("button", { name: "Send invite" }).click();
  // The confirmation is a toast, which renders outside the region.
  await expect(page.getByText("Invitation sent.")).toBeVisible();
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
  await selectInvitationsTab(page, "pending");
  const pending = invitationsPanel(page, "pending")
    .getByRole("listitem")
    .filter({ hasText: TARGET });
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
    await chooseRowRole(page, "member");
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

    // The confirmation is a portaled alert dialog, not buttons in the row.
    await row.getByRole("button", { name: `Remove ${TARGET_NAME}`, exact: true }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeVisible();
    await dialog.getByRole("button", { name: "Remove member" }).click();

    await expect(memberRow(page)).toHaveCount(0);
    await page.reload();
    // The list has to answer first: an empty count during loading proves nothing.
    await waitForLists(page);
    await expect(memberRow(page)).toHaveCount(0);
  });

  test("invites the member back", async ({ page }) => {
    await openMembers(page);
    await sendInvite(page);

    await selectInvitationsTab(page, "pending");
    const row = invitationRow(page, "pending");
    await expect(row).toContainText("Pending");
    await expect(row).toContainText("Administrator");
  });

  test("the invited account accepts and is a member again", async ({ page, browser }) => {
    await openMembers(page);
    // The accept step below must move exactly one invitation into Accepted.
    await selectInvitationsTab(page, "accepted");
    const acceptedBefore = await invitationCount(page, "accepted");

    await acceptInvite(browser);

    await page.reload();
    await waitForLists(page);
    await expect(memberRow(page)).toHaveCount(1);
    await expect(roleCell(memberRow(page))).toHaveText("Administrator");

    // Accepting closed the invitation: the target has no row left Pending,
    // and its invitation now sits in Accepted, whose count grew by one.
    await selectInvitationsTab(page, "pending");
    await expect(invitationRow(page, "pending")).toHaveCount(0);
    await selectInvitationsTab(page, "accepted");
    await expect(invitationRow(page, "accepted")).toContainText("Accepted");
    await expect(invitationsTab(page, "accepted")).toHaveText(`Accepted (${acceptedBefore + 1})`);
  });
});
