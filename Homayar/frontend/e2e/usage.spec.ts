import { expect, test } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";

/**
 * The usage and subscription page (HOM-181), read-only against the demo seed:
 * plan `business` and `active`, audio limit 36000 s, concurrent jobs limit 10,
 * and more than 100 usage events. Nothing here writes data.
 */

test("an owner reads the workspace's usage, subscription and plan catalog", async ({ page }) => {
  await resumeSession(page, "owner");
  await page.goto(consolePath("/admin/usage"));

  await expect(page.getByRole("heading", { level: 1, name: "Usage and subscription" })).toBeVisible();

  for (const title of ["Current plan", "Quota usage", "Usage events", "Plan catalog"]) {
    const section = page.getByRole("region", { name: title, exact: true });
    await expect(section).toBeVisible();
    // Each section owns its request: wait for it, then check it did not fail.
    await expect(section).not.toHaveAttribute("aria-busy", "true");
    await expect(section.getByRole("alert")).toHaveCount(0);
  }

  const plan = page.getByRole("region", { name: "Current plan", exact: true });
  await expect(plan.getByText("Business", { exact: true })).toBeVisible();
  await expect(plan.getByText("Active", { exact: true })).toBeVisible();

  const quotas = page.getByRole("region", { name: "Quota usage", exact: true });
  await expect(quotas.getByRole("meter", { name: "Audio processing" })).toBeVisible();

  const events = page.getByRole("region", { name: "Usage events", exact: true });
  const eventsTable = events.getByRole("grid", { name: "Usage event history" });
  // Body rows only: the header row has no grid cells.
  await expect(eventsTable.getByRole("gridcell").first()).toBeVisible();

  const catalog = page.getByRole("region", { name: "Plan catalog", exact: true });
  await expect(catalog.getByRole("link", { name: "Contact us" })).toHaveAttribute("href", "/contact");
  // Billing is manual: no amount of money anywhere on the page.
  await expect(page.getByText(/\$|€|price|pricing|تومان|ریال|قیمت/i)).toHaveCount(0);
});

test("the events table filters by metric and pages through the seed's history", async ({ page }) => {
  await resumeSession(page, "owner");
  await page.goto(consolePath("/admin/usage"));

  const events = page.getByRole("region", { name: "Usage events", exact: true });
  await expect(events).not.toHaveAttribute("aria-busy", "true");

  await page.getByRole("button", { name: "Filter by metric" }).click();
  await page.getByRole("option", { name: "Audio processing" }).click();
  const table = events.getByRole("grid", { name: "Usage event history" });
  // Wait for the filtered page to land, then read its body cells.
  await expect(events).not.toHaveAttribute("aria-busy", "true");
  await expect(table.getByRole("gridcell", { name: "Audio processing", exact: true })).not.toHaveCount(0);
  await expect(table.getByRole("gridcell", { name: "Concurrent jobs", exact: true })).toHaveCount(0);

  // The seed has more than 25 events, so page two exists for every metric.
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByText("Page 2")).toBeVisible();
  await page.getByRole("button", { name: "Previous page" }).click();
  await expect(page.getByText("Page 1")).toBeVisible();
});

test("the page is right to left with Persian texts in Persian", async ({ page }) => {
  await resumeSession(page, "owner", "fa");
  await page.goto(consolePath("/admin/usage"));

  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("heading", { level: 1, name: "مصرف و اشتراک" })).toBeVisible();
  for (const title of ["طرح فعلی", "سهمیه‌ها", "رویدادهای مصرف", "طرح‌های اشتراک"]) {
    await expect(page.getByRole("region", { name: title, exact: true })).toBeVisible();
  }
  await expect(page.getByRole("link", { name: "با ما تماس بگیرید" })).toHaveAttribute("href", "/contact");
});

test("a member cannot read billing data", async ({ page }) => {
  await resumeSession(page, "member");
  await page.goto(consolePath("/admin/usage"));
  await expect(page.getByRole("heading", { level: 1, name: "No access" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Usage and subscription" })).toHaveCount(0);
});
