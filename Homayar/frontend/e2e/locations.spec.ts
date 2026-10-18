import { expect, test, type Page } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";

/**
 * The service locations page (HOM-191) against the demo workspace. Every
 * location this run creates carries this run's unique code prefix;
 * `afterAll` deletes, over the API, anything with that prefix a failed step
 * left behind — and nothing else.
 */
const RUN_PREFIX = `e2e-loc-${Date.now()}-`;
const NAME = "E2E location";
const RENAMED = "E2E location renamed";

function locationRow(page: Page, name: string) {
  return page.getByRole("row").filter({ hasText: name });
}

/** The headers a signed-in console request carries (src/lib/api.ts). */
async function apiHeaders(page: Page): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((cookie) => cookie.name === "homayar_csrf")?.value;
  const workspaceId = await page.evaluate(() => window.localStorage.getItem("homayar_workspace_id"));
  return {
    ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    ...(workspaceId ? { "X-Workspace-ID": workspaceId } : {}),
  };
}

test.describe.serial("the locations page", () => {
  test.afterAll(async ({ browser }) => {
    // A repair after a failed run signs in and walks the API again.
    test.setTimeout(120_000);
    const context = await browser.newContext();
    try {
      const page = await context.newPage();
      await resumeSession(page, "owner");
      await page.goto(consolePath("/admin/locations"));
      const headers = await apiHeaders(page);
      const response = await page.request.get("/api/v1/locations", { headers });
      expect(response.ok(), "GET /locations with the page's session").toBeTruthy();
      const body = (await response.json()) as { items: { id: string; code: string }[] };
      for (const item of body.items) {
        if (!item.code.startsWith(RUN_PREFIX)) continue;
        const removed = await page.request.delete(`/api/v1/locations/${item.id}`, { headers });
        expect(removed.ok(), `DELETE /locations/${item.id} cleaning up ${item.code}`).toBeTruthy();
      }
    } finally {
      await context.close();
    }
  });

  test("an owner walks a location through create, filter, edit and delete", async ({ page }) => {
    const code = `${RUN_PREFIX}dealer`;
    await resumeSession(page, "owner");
    await page.goto(consolePath("/admin/locations"));
    await expect(page.getByRole("heading", { name: "Service locations", level: 1 })).toBeVisible();

    // A dealer in a city the gazetteer does not know, with coordinates far
    // outside Iran: the save succeeds and answers with both warnings.
    await page.getByRole("button", { name: "Add location" }).click();
    const drawer = page.getByRole("dialog", { name: "Add location" });
    await drawer.getByRole("button", { name: "Kind", exact: true }).click();
    await page.getByRole("option", { name: "Dealer" }).click();
    await drawer.getByLabel("Name", { exact: true }).fill(NAME);
    await drawer.getByLabel("Code", { exact: true }).fill(code);
    await drawer.getByLabel("City", { exact: true }).fill("Atlantis");
    await drawer.getByLabel("Latitude", { exact: true }).fill("48.85");
    await drawer.getByLabel("Longitude", { exact: true }).fill("2.35");
    await drawer.getByRole("button", { name: "Save" }).click();

    await expect(page.getByText(`Saved “${NAME}” with warnings`)).toBeVisible();
    await expect(page.getByText("The coordinates are outside Iran.")).toBeVisible();
    await expect(page.getByText("The city is not in the place list. Check the spelling.")).toBeVisible();
    await expect(locationRow(page, NAME)).toBeVisible();

    // Each filter narrows the list; the active row survives the first two.
    await page.getByRole("button", { name: /Kind/ }).click();
    await page.getByRole("option", { name: "Dealer" }).click();
    await expect(locationRow(page, NAME)).toBeVisible();

    await page.getByRole("textbox", { name: "City" }).fill("Atlantis");
    await expect(locationRow(page, NAME)).toBeVisible();

    await page.getByRole("button", { name: /Status/ }).click();
    await page.getByRole("option", { name: "Inactive" }).click();
    await expect(locationRow(page, NAME)).toHaveCount(0);

    // Reset the filters so the row is reachable again.
    await page.getByRole("button", { name: /Status/ }).click();
    await page.getByRole("option", { name: "All statuses" }).click();
    await page.getByRole("button", { name: /Kind/ }).click();
    await page.getByRole("option", { name: "All kinds" }).click();
    await page.getByRole("textbox", { name: "City" }).fill("");
    await expect(locationRow(page, NAME)).toBeVisible();

    // A rename through the drawer shows in the list.
    await locationRow(page, NAME).getByRole("button", { name: `Edit ${NAME}` }).click();
    const editDrawer = page.getByRole("dialog", { name: "Edit location" });
    await expect(editDrawer.getByLabel("Name", { exact: true })).toHaveValue(NAME);
    await editDrawer.getByLabel("Name", { exact: true }).fill(RENAMED);
    await editDrawer.getByRole("button", { name: "Save" }).click();
    await expect(locationRow(page, RENAMED)).toBeVisible();

    // The delete asks once, then the row is gone.
    await locationRow(page, RENAMED).getByRole("button", { name: `Delete ${RENAMED}` }).click();
    const confirm = page.getByRole("alertdialog");
    await expect(confirm.getByRole("heading", { name: "Delete location?" })).toBeVisible();
    await expect(confirm.getByText(/This cannot be undone\./)).toBeVisible();
    await confirm.getByRole("button", { name: "Delete" }).click();
    await expect(locationRow(page, RENAMED)).toHaveCount(0);
  });

  test("a member gets No access", async ({ page }) => {
    await resumeSession(page, "member");
    await page.goto(consolePath("/admin/locations"));

    await expect(page.getByRole("heading", { name: "No access", level: 1 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Service locations" })).toHaveCount(0);
  });
});
