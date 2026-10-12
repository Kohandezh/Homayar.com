import { expect, test, type Page } from "@playwright/test";
import { consolePath, signIn } from "./demo";

/** The form remounts with the saved values, so it has to be found again. */
function nameField(page: Page) {
  return page.getByLabel("Workspace name");
}

/** Save, then reload: only a value that survives the reload really reached the API. */
async function saveAndReload(page: Page, expected: string) {
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByText("Settings saved.")).toBeVisible();
  await page.reload();
  await expect(nameField(page)).toHaveValue(expected);
}

/**
 * The seeded name, set once the test has read it. `afterEach` puts it back,
 * so a failure in the middle of the test does not leave the demo workspace
 * renamed for the next run.
 */
let seededName: string | null = null;

test.afterEach(async ({ page }) => {
  if (seededName === null) return;
  const name = seededName;
  seededName = null;
  await page.goto(consolePath("/admin/settings"));
  await nameField(page).fill(name);
  await saveAndReload(page, name);
});

test("an owner edits a workspace setting and the audit log records it", async ({ page }) => {
  await signIn(page, "owner");
  await page.goto(consolePath("/admin/settings"));

  await expect(page.getByRole("heading", { name: "Settings and audit", level: 1 })).toBeVisible();
  await expect(page.getByRole("region", { name: "Workspace settings" })).toBeVisible();

  const original = await nameField(page).inputValue();
  expect(original).not.toBe("");
  seededName = original;
  const edited = `${original} (e2e ${Date.now()})`;

  await nameField(page).fill(edited);
  await saveAndReload(page, edited);

  // The audit log is the product's record of the change, not a side effect.
  await page.getByLabel("Event type").selectOption("workspace.settings.updated");
  await page.getByRole("button", { name: "Apply filters" }).click();

  const newest = page.getByRole("region", { name: "Audit log" }).locator("li").first();
  await expect(newest).toBeVisible();
  await expect(newest.locator("code")).toHaveText("workspace.settings.updated");
  await expect(newest).toContainText("Workspace settings changed");
  await expect(newest).toContainText("Succeeded");
});
