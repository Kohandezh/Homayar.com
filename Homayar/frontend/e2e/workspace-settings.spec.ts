import { expect, test, type Locator, type Page } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";

type DuplicatePolicy = "Allow" | "Reject";

const SECTIONS = {
  en: ["General", "Data retention", "Sharing", "Access scope", "Duplicates", "Privacy"],
  fa: ["عمومی", "نگهداری داده", "اشتراک‌گذاری", "دامنهٔ دسترسی", "فایل‌های تکراری", "حریم خصوصی"],
};

const publicLinks = (page: Page) => page.getByRole("switch", { name: "Public share links" });
const duplicateOption = (page: Page, name: DuplicatePolicy) => page.getByRole("radio", { name, exact: true });

/**
 * React Aria hides the native input under its label, so a click on the input
 * lands on the label. Click the visible label instead.
 */
async function setChecked(control: Locator, checked: boolean) {
  if ((await control.isChecked()) === checked) return;
  await control.locator("xpath=ancestor::label[1]").click();
  await expect(control).toBeChecked({ checked });
}

async function openSettings(page: Page) {
  await page.goto(consolePath("/admin/settings"));
  await expect(page.getByRole("region", { name: "Workspace settings" })).toBeVisible();
  await expect(publicLinks(page)).toBeVisible();
}

async function save(page: Page) {
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByText("Settings saved.")).toBeVisible();
}

/**
 * The seeded values, set once a test has read them. `afterEach` puts them
 * back, so a failure in the middle of a test does not leave the demo
 * workspace changed for the next run.
 */
let seeded: { publicLinks: boolean; duplicates: DuplicatePolicy } | null = null;

test.afterEach(async ({ page }) => {
  if (seeded === null) return;
  const values = seeded;
  seeded = null;
  await openSettings(page);
  await setChecked(publicLinks(page), values.publicLinks);
  await setChecked(duplicateOption(page, values.duplicates), true);
  const saveButton = page.getByRole("button", { name: "Save settings" });
  if (await saveButton.isEnabled()) await save(page);
  await page.reload();
  await expect(publicLinks(page)).toBeChecked({ checked: values.publicLinks });
  await expect(duplicateOption(page, values.duplicates)).toBeChecked();
});

test("an owner changes sharing and duplicates, the values persist, and the audit log records it", async ({ page }) => {
  await resumeSession(page, "owner");
  await openSettings(page);
  await expect(page.getByRole("heading", { name: "Workspace settings", level: 1 })).toBeVisible();
  for (const name of SECTIONS.en) {
    await expect(page.getByRole("heading", { name, level: 2, exact: true })).toBeVisible();
  }

  const originalPublic = await publicLinks(page).isChecked();
  const originalDuplicates: DuplicatePolicy = (await duplicateOption(page, "Allow").isChecked()) ? "Allow" : "Reject";
  seeded = { publicLinks: originalPublic, duplicates: originalDuplicates };
  const editedDuplicates: DuplicatePolicy = originalDuplicates === "Allow" ? "Reject" : "Allow";

  await setChecked(publicLinks(page), !originalPublic);
  await setChecked(duplicateOption(page, editedDuplicates), true);
  await save(page);

  // Only a value that survives the reload really reached the API.
  await page.reload();
  await expect(publicLinks(page)).toBeChecked({ checked: !originalPublic });
  await expect(duplicateOption(page, editedDuplicates)).toBeChecked();

  // The audit log is the product's record of the change, not a side effect.
  await page.goto(consolePath("/admin/audit"));
  await page.getByRole("button", { name: /Event type/ }).click();
  await page.getByRole("option", { name: "Workspace settings changed" }).click();
  await expect(page).toHaveURL(/workspace\.settings\.updated/);

  const newest = page.getByLabel("Audit events").getByRole("row").nth(1);
  await expect(newest).toContainText("Workspace settings changed");
  await expect(newest).toContainText("Succeeded");
});

test("an admin sees the privacy controls locked and the other settings editable", async ({ page }) => {
  await resumeSession(page, "admin");
  await openSettings(page);

  await expect(page.getByRole("switch", { name: "PII redaction" })).toBeDisabled();
  const classes = page.getByRole("group", { name: "Redacted data types" });
  for (const name of ["Email", "Phone", "National ID", "Bank card", "IBAN", "Postal code"]) {
    await expect(classes.getByRole("checkbox", { name, exact: true })).toBeDisabled();
  }
  await expect(page.getByText("Only the workspace owner can change these settings.")).toBeVisible();

  for (const name of ["Public share links", "Organization share links", "Delete audio after processing"]) {
    await expect(page.getByRole("switch", { name })).toBeEnabled();
  }
  await expect(duplicateOption(page, "Reject")).toBeEnabled();
  await expect(page.getByRole("radio", { name: "By team" })).toBeEnabled();
});

test("an owner who cancels the immediate-retention warning saves nothing", async ({ page }) => {
  await resumeSession(page, "owner");
  await openSettings(page);

  const policy = page.getByRole("radiogroup", { name: "Data retention policy" });
  const immediate = policy.getByRole("radio", { name: /^Immediate/ });
  await expect(immediate).not.toBeChecked();
  const radios = policy.getByRole("radio");
  const before: boolean[] = [];
  for (let index = 0; index < (await radios.count()); index += 1) before.push(await radios.nth(index).isChecked());

  await setChecked(immediate, true);
  await page.getByRole("button", { name: "Save settings" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Delete data and save" })).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("Settings saved.")).toHaveCount(0);

  await page.reload();
  await expect(publicLinks(page)).toBeVisible();
  for (const [index, checked] of before.entries()) {
    await expect(radios.nth(index)).toBeChecked({ checked });
  }
});

test("the settings page lays out right to left in Persian", async ({ page }) => {
  await resumeSession(page, "owner", "fa");
  await page.goto(consolePath("/admin/settings"));

  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("heading", { name: "تنظیمات فضای کاری", level: 1 })).toBeVisible();
  for (const name of SECTIONS.fa) {
    await expect(page.getByRole("heading", { name, level: 2, exact: true })).toBeVisible();
  }
});
