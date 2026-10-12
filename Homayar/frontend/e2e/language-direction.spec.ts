import { expect, test } from "@playwright/test";
import { consolePath, signIn } from "./demo";

test("the language switch flips the console between RTL and LTR", async ({ page }) => {
  // Persian is the default, so the console starts right to left.
  await signIn(page, "owner", "fa");
  await page.goto(consolePath("/admin"));

  const html = page.locator("html");
  await expect(html).toHaveAttribute("dir", "rtl");
  await expect(html).toHaveAttribute("lang", "fa");
  await expect(page.getByRole("heading", { name: "نمای کلی مدیریت", level: 1 })).toBeVisible();

  await page.getByRole("button", { name: "Switch to English" }).click();

  await expect(html).toHaveAttribute("dir", "ltr");
  await expect(html).toHaveAttribute("lang", "en");
  await expect(page.getByRole("heading", { name: "Admin overview", level: 1 })).toBeVisible();

  // The choice survives a reload, so a demo does not flip back mid-walkthrough.
  await page.reload();
  await expect(html).toHaveAttribute("dir", "ltr");
  await expect(page.getByRole("heading", { name: "Admin overview", level: 1 })).toBeVisible();

  await page.getByRole("button", { name: "تغییر به فارسی" }).click();
  await expect(html).toHaveAttribute("dir", "rtl");
  await expect(html).toHaveAttribute("lang", "fa");
});
