import { expect, test } from "@playwright/test";
import { consolePath, signIn } from "./demo";

test("a viewer is blocked from workspace settings", async ({ page }) => {
  await signIn(page, "viewer");
  await page.goto(consolePath("/admin/settings"));

  await expect(page.getByRole("heading", { name: "No access" })).toBeVisible();
  // The guard never mounts the page, so neither panel and none of their
  // requests appear, and the URL still points at the page that was asked for.
  await expect(page.getByRole("heading", { name: "Settings and audit" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Workspace settings" })).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(`${consolePath("/admin/settings")}$`));
});

test("a viewer is blocked from the admin overview", async ({ page }) => {
  await signIn(page, "viewer");
  await page.goto(consolePath("/admin"));

  await expect(page.getByRole("heading", { name: "No access" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Admin overview" })).toHaveCount(0);
});

test("a viewer can still open the pages their role allows", async ({ page }) => {
  await signIn(page, "viewer");
  await page.goto(consolePath("/admin/members"));

  await expect(page.getByRole("heading", { name: "Members and access", level: 1 })).toBeVisible();
  // Read only: no invite form for a role without member:manage.
  await expect(page.getByRole("region", { name: "Invitations" })).toHaveCount(0);
});
