import { expect, test, type Locator } from "@playwright/test";
import { consolePath, signIn } from "./demo";

/**
 * Every card the owner's permissions allow, with one thing only its data
 * state renders. The card keeps its heading while it loads and when its
 * request fails, so a visible card proves nothing on its own.
 */
const CARDS: { title: string; data: (card: Locator) => Locator }[] = [
  // The status breakdown of the seeded meetings.
  { title: "Processing", data: (card) => card.getByText("Completed", { exact: true }) },
  // The seed fails two meetings, so this list has rows.
  { title: "Needs attention", data: (card) => card.getByRole("listitem").first() },
  // One row per quota metric.
  { title: "Quota", data: (card) => card.getByRole("listitem").first() },
  // The subscription row of the seeded paid plan.
  { title: "Plan", data: (card) => card.getByText("Status", { exact: true }) },
  { title: "Members", data: (card) => card.getByText("Total members", { exact: true }) },
  { title: "Recent activity", data: (card) => card.getByRole("listitem").first() },
];

test("an owner signs in and the admin overview is filled with workspace data", async ({ page }) => {
  await signIn(page, "owner");
  await page.goto(consolePath("/admin"));

  await expect(page.getByRole("heading", { name: "Admin overview", level: 1 })).toBeVisible();

  for (const { title, data } of CARDS) {
    const card = page.getByRole("region", { name: title, exact: true });
    await expect(card).toBeVisible();
    // Each card owns its request. Wait for it to finish, then check it did not
    // fail: a failed request replaces the data with an error alert.
    await expect(card).not.toHaveAttribute("aria-busy", "true");
    await expect(card.getByRole("alert")).toHaveCount(0);
    await expect(data(card)).toBeVisible();
  }

  const members = page.getByRole("region", { name: "Members", exact: true });
  // The seed has four accounts; any count above zero means real data.
  await expect(members.locator("dd").first()).toHaveText(/^[1-9][0-9]*$/);
  await expect(members.getByRole("link", { name: "Manage members" })).toBeVisible();
});
