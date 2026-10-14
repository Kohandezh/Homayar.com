import { expect, test, type Locator } from "@playwright/test";
import { consolePath, signIn } from "./demo";

/**
 * The processing page against the demo seed (no mocks). The seed parks one
 * dead-letter job ("Quarterly pricing review #26", failure code
 * `asr_unavailable`, replayable) and fails two ("New branch onboarding #24",
 * "پیگیری سفارش مشتری #25", not replayable).
 *
 * The Replay button is never clicked here: a replay cannot be undone through
 * the API and the seed's dead-letter job allows only three, so clicking it
 * would break later runs. Vitest covers the replay and its refusals.
 */

const DEAD_LETTER_TITLE = "Quarterly pricing review #26";
const FAILED_TITLES = ["New branch onboarding #24", "پیگیری سفارش مشتری #25"];

function jobRow(grid: Locator, title: string) {
  return grid.getByRole("row", { name: new RegExp(escapeRegExp(title)) });
}

function escapeRegExp(text: string) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Opens the state select, toggles the named states, and closes it again. */
async function filterStates(page: import("@playwright/test").Page, labels: string[]) {
  // The React Aria trigger is a button whose name ends with the field label
  // ("All states State", later "Stopped; needs review State").
  await page.getByRole("button", { name: /State$/ }).click();
  for (const label of labels) {
    await page.getByRole("option", { name: label, exact: true }).click();
  }
  await page.keyboard.press("Escape");
}

test("an owner reviews failed and dead-letter jobs and can see what to replay", async ({ page }) => {
  await signIn(page, "owner");
  await page.goto(consolePath("/admin/processing"));

  await expect(page.getByRole("heading", { name: "Processing", level: 1 })).toBeVisible();

  // The unfiltered list asks for every state; the seeded jobs fill page one.
  const grid = page.getByRole("grid", { name: "Processing jobs" });
  await expect(grid).toBeVisible();
  await expect(jobRow(grid, DEAD_LETTER_TITLE)).toBeVisible();

  // Only the dead-lettered job remains when the state filter is set.
  await filterStates(page, ["Stopped; needs review"]);
  await expect(page).toHaveURL(/state=dead_letter/);
  await expect(grid.getByRole("row")).toHaveCount(2); // header plus one job
  await expect(grid.getByText("asr_unavailable")).toBeVisible();

  // Its drawer: why it stopped, the timeline section, the policy section,
  // and the replay action the seed allows.
  const deadLetterRow = jobRow(grid, DEAD_LETTER_TITLE);
  await deadLetterRow.getByRole("button", { name: "Details" }).click();
  // The select's popover is a dialog too while it animates out, so the
  // drawer is told apart by its own name.
  const drawer = page.getByRole("dialog", { name: "Job details" });
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText(
    "Speech recognition was unavailable; parked for replay (demo data)",
  );
  await expect(drawer.getByRole("heading", { name: "Stage timeline" })).toBeVisible();
  await expect(drawer.getByText("No stages have been recorded yet.")).toBeVisible();
  await expect(drawer.getByRole("heading", { name: "AI policy per attempt" })).toBeVisible();
  await expect(drawer.getByText("No policy was recorded for this job.")).toBeVisible();
  await expect(drawer.getByRole("button", { name: "Retry processing" })).toBeVisible();
  await drawer.getByRole("button", { name: "Close details" }).click();
  await expect(drawer).toHaveCount(0);

  // The failed jobs are terminal for good: no replay is offered for either.
  await filterStates(page, ["Failed", "Stopped; needs review"]);
  await expect(page).toHaveURL(/state=failed/);
  for (const title of FAILED_TITLES) {
    const row = jobRow(grid, title);
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "Details" }).click();
    const failedDrawer = page.getByRole("dialog", { name: "Job details" });
    await expect(failedDrawer).toBeVisible();
    // The code sits in the failure field and again as the asr stage's reason.
    await expect(failedDrawer.getByText("asr_input_rejected").first()).toBeVisible();
    // Unlike the parked job, this one did run: its timeline has stage entries.
    await expect(failedDrawer.getByRole("heading", { name: "Stage timeline" })).toBeVisible();
    await expect(failedDrawer.getByText("Speech to text")).toBeVisible();
    await expect(failedDrawer.getByRole("button", { name: "Retry processing" })).toHaveCount(0);
    await failedDrawer.getByRole("button", { name: "Close details" }).click();
    await expect(failedDrawer).toHaveCount(0);
  }

  // Persian flips the page to right-to-left with its own heading and table name.
  await page.getByRole("button", { name: "تغییر به فارسی" }).click();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("heading", { name: "پردازش", level: 1 })).toBeVisible();
  await expect(page.getByRole("grid", { name: "کارهای پردازش" })).toBeVisible();
});
