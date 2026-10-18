import { expect, test, type Browser, type Locator, type Page } from "@playwright/test";
import { consolePath, DEMO_DISPLAY_NAMES, resumeSession } from "./demo";

/**
 * The teams page (HOM-182) against the demo seed: the seeded `Sales` team
 * (admin as supervisor, member as agent) and `Support` team (owner as
 * supervisor, viewer as agent).
 *
 * `afterAll` puts the seed back whatever happened: the `Support` name
 * and its active flag, and any assignment this run added. A revoked row
 * stays as history; the server has no delete.
 */

const SUPPORT = "Support";
const SUPPORT_E2E = "Support e2e";
const VIEWER_NAME = DEMO_DISPLAY_NAMES.viewer; // Ali Rahimi

function teamRow(page: Page, name: string): Locator {
  return page.getByRole("row").filter({ hasText: name });
}

/** The row whose rowheader is exactly `name`: "Support" must not match "Support e2e". */
function teamRowExact(page: Page, name: string): Locator {
  return page.getByRole("row").filter({ has: page.getByRole("rowheader", { name, exact: true }) });
}

/** The member-count cell: the name is a rowheader, so this is gridcell 2. */
function countCell(row: Locator): Locator {
  return row.getByRole("gridcell").nth(1);
}

async function openTeams(page: Page) {
  await resumeSession(page, "owner");
  await page.goto(consolePath("/admin/teams"));
  await expect(page.getByRole("heading", { name: "Teams", level: 1 })).toBeVisible();
  await expect(teamRow(page, "Sales")).toBeVisible();
}

/** Renames through the dialog, waiting for the list to show the new name. */
async function renameTeam(page: Page, from: string, to: string) {
  await teamRow(page, from).getByRole("button", { name: `Rename team ${from}` }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Team name").fill(to);
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(teamRowExact(page, to)).toBeVisible();
  await expect(teamRowExact(page, from)).toHaveCount(0);
}

/** Puts the demo workspace back the way the seed left it. Idempotent. */
async function restoreSeed(browser: Browser) {
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    await openTeams(page);

    // A run that died between the two renames leaves the e2e name behind.
    if ((await teamRowExact(page, SUPPORT_E2E).count()) > 0) {
      await renameTeam(page, SUPPORT_E2E, SUPPORT);
    }
    expect(await teamRowExact(page, SUPPORT).count()).toBe(1);

    // Deactivation is undone only when it stuck.
    if (await teamRow(page, SUPPORT).getByText("Inactive").isVisible().catch(() => false)) {
      await teamRow(page, SUPPORT).getByRole("button", { name: `Reactivate team ${SUPPORT}` }).click();
      await page.getByRole("alertdialog").getByRole("button", { name: "Reactivate" }).click();
      await expect(teamRow(page, SUPPORT).getByText("Active")).toBeVisible();
    }

    // Any still-effective viewer assignment on Sales came from this run.
    // Earlier runs left revoked rows behind; they carry no revoke button.
    await teamRow(page, "Sales").getByRole("button", { name: "Open team Sales" }).click();
    const drawer = page.getByRole("dialog", { name: "Sales" });
    const revokeViewer = drawer.getByRole("button", { name: `Revoke ${VIEWER_NAME} as Agent` });
    let remaining = await revokeViewer.count();
    while (remaining > 0) {
      await revokeViewer.first().click();
      await page.getByRole("alertdialog").getByRole("button", { name: "Revoke" }).click();
      remaining -= 1;
      await expect(revokeViewer).toHaveCount(remaining);
    }
    await expect(countCell(teamRow(page, "Sales"))).toHaveText("2");
  } finally {
    await context.close();
  }
}

test.describe.serial("the teams page", () => {
  test.afterAll(async ({ browser }) => {
    // A repair after a failed run signs in and walks the page again.
    test.setTimeout(180_000);
    await restoreSeed(browser);
  });

  test("an owner reads the teams and walks every write", async ({ page }) => {
    await openTeams(page);

    // The seeded list, with its counts and the scope explainer.
    for (const name of ["Sales", "Support"]) {
      await expect(teamRow(page, name).getByText("Active")).toBeVisible();
    }
    await expect(countCell(teamRow(page, "Sales"))).toHaveText("2");
    await expect(countCell(teamRow(page, "Support"))).toHaveText("2");
    await expect(page.getByText("Record access: whole workspace.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Change in settings" })).toHaveAttribute(
      "href",
      /\/admin\/settings$/,
    );

    // The drawer lists the assignment history of one team.
    await teamRow(page, "Sales").getByRole("button", { name: "Open team Sales" }).click();
    const drawer = page.getByRole("dialog", { name: "Sales" });
    await expect(drawer.getByRole("heading", { name: "Sales", level: 2 })).toBeVisible();
    await expect(drawer.getByRole("grid", { name: "Team assignments" })).toBeVisible();
    const adminRow = drawer.getByRole("row").filter({ hasText: DEMO_DISPLAY_NAMES.admin });
    await expect(adminRow).toContainText("Supervisor");
    await expect(adminRow).toContainText("Effective");
    // The table behind the drawer has to be reachable for the steps below.
    await drawer.getByRole("button", { name: "Close" }).click();
    await expect(drawer).toHaveCount(0);

    // A rename shows in the list and survives a reload.
    await renameTeam(page, SUPPORT, SUPPORT_E2E);
    await page.reload();
    await expect(teamRowExact(page, SUPPORT_E2E)).toBeVisible();
    await renameTeam(page, SUPPORT_E2E, SUPPORT);

    // Deactivation and reactivation flip the chip.
    await teamRow(page, SUPPORT).getByRole("button", { name: `Deactivate team ${SUPPORT}` }).click();
    const confirm = page.getByRole("alertdialog");
    await expect(confirm.getByRole("heading", { name: `Deactivate ${SUPPORT}?` })).toBeVisible();
    await confirm.getByRole("button", { name: "Deactivate" }).click();
    await expect(teamRow(page, SUPPORT).getByText("Inactive")).toBeVisible();

    await teamRow(page, SUPPORT).getByRole("button", { name: `Reactivate team ${SUPPORT}` }).click();
    await expect(page.getByRole("alertdialog").getByRole("heading", { name: `Reactivate ${SUPPORT}?` })).toBeVisible();
    await page.getByRole("alertdialog").getByRole("button", { name: "Reactivate" }).click();
    await expect(teamRow(page, SUPPORT).getByText("Active")).toBeVisible();

    // An assignment without a period starts now and counts; revoking keeps the
    // row as history, so earlier runs' revoked rows sit next to the new one.
    await teamRow(page, "Sales").getByRole("button", { name: "Open team Sales" }).click();
    await expect(countCell(teamRow(page, "Sales"))).toHaveText("2");
    const memberPicker = drawer.getByRole("combobox", { name: "Member" });
    await memberPicker.click();
    await memberPicker.fill(VIEWER_NAME);
    await page.getByRole("option", { name: VIEWER_NAME }).click();
    await drawer.getByRole("button", { name: "Assign" }).click();

    const viewerRows = drawer.getByRole("row").filter({ hasText: VIEWER_NAME });
    // Server order: the row this run just created is the last one.
    await expect(viewerRows.last()).toContainText("Agent");
    await expect(viewerRows.filter({ hasText: "Effective" })).toHaveCount(1);
    await expect(countCell(teamRow(page, "Sales"))).toHaveText("3");

    await drawer.getByRole("button", { name: `Revoke ${VIEWER_NAME} as Agent` }).click();
    await expect(page.getByRole("alertdialog").getByRole("heading", { name: "Revoke this assignment?" })).toBeVisible();
    await page.getByRole("alertdialog").getByRole("button", { name: "Revoke" }).click();
    await expect(drawer.getByRole("button", { name: `Revoke ${VIEWER_NAME} as Agent` })).toHaveCount(0);
    await expect(countCell(teamRow(page, "Sales"))).toHaveText("2");
  });

  test("mirrors the page in Persian", async ({ page }) => {
    await resumeSession(page, "owner", "fa");
    await page.goto(consolePath("/admin/teams"));
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.getByRole("heading", { name: "تیم‌ها", level: 1 })).toBeVisible();
    await expect(countCell(teamRow(page, "Sales"))).toHaveText("۲");
  });

  test("keeps a member read only", async ({ page }) => {
    await resumeSession(page, "member");
    await page.goto(consolePath("/admin/teams"));
    await expect(page.getByRole("heading", { name: "Teams", level: 1 })).toBeVisible();

    await expect(teamRow(page, "Sales")).toBeVisible();
    await expect(page.getByRole("button", { name: "Create team" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Rename team Sales" })).toHaveCount(0);
    await expect(page.getByText("Record access:")).toHaveCount(0);

    await teamRow(page, "Sales").getByRole("button", { name: "Open team Sales" }).click();
    const drawer = page.getByRole("dialog", { name: "Sales" });
    await expect(drawer.getByRole("grid", { name: "Team assignments" })).toBeVisible();
    await expect(drawer.getByRole("button", { name: "Assign" })).toHaveCount(0);
    await expect(drawer.getByRole("button", { name: /^Revoke / })).toHaveCount(0);
  });
});
