import { expect, test, type Browser, type Page } from "@playwright/test";
import { consolePath, DEMO_DISPLAY_NAMES, resumeSession } from "./demo";

/**
 * The organization admin area (HOM-193) against the real demo backend, read
 * only. The demo seed gives the owner one organization with one member (the
 * owner), one workspace and no scope grants; the other demo roles belong to
 * no organization.
 *
 * Nothing here may change the shared backend: every request to an
 * organization route that is not a GET is aborted in the browser, and the
 * test fails if one was even tried. The write flows live in
 * `organization-writes.spec.ts`, with a mocked organization API.
 */

const ORG_NAME = "شرکت نمونه همایار";
const OWNER_NAME = DEMO_DISPLAY_NAMES.owner; // سارا احمدی

/**
 * The seed gives the organization a new id in every database
 * (`backend/database/demo_seed.py`), so the id is read at run time with the
 * owner's session. The lookup is a GET, like every request this spec sends.
 */
async function demoOrganizationId(browser: Browser): Promise<string> {
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    await resumeSession(page, "owner");
    const response = await page.request.get("/api/v1/organizations");
    expect(response.status(), "GET /organizations with the owner's session").toBe(200);
    const { items } = (await response.json()) as { items: { id: string; name: string }[] };
    const organization = items.find((item) => item.name === ORG_NAME);
    expect(organization, `the seeded organization "${ORG_NAME}"`).toBeDefined();
    return organization!.id;
  } finally {
    await context.close();
  }
}

/** Lets only GETs reach the organization API, and returns what else was tried. */
async function readOnly(page: Page) {
  const refused: string[] = [];
  await page.route("**/api/v1/organizations**", async (route) => {
    const request = route.request();
    if (request.method() === "GET") return route.continue();
    refused.push(`${request.method()} ${request.url()}`);
    return route.abort();
  });
  return refused;
}

test.describe("organization admin area (real backend, read only)", () => {
  let refused: string[] = [];
  let orgId = "";

  test.beforeAll(async ({ browser }) => {
    orgId = await demoOrganizationId(browser);
  });

  test.afterEach(() => {
    expect(refused, "no organization write may reach the shared backend").toEqual([]);
  });

  test("the owner chooses the organization and reads every page", async ({ page }) => {
    refused = await readOnly(page);
    await resumeSession(page, "owner");
    const list = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/v1/organizations");
    await page.goto(consolePath("/meetings"));
    expect((await list).status()).toBe(200);

    // The switcher, then the overview.
    const switcher = page.getByRole("button", { name: /Organization/ });
    await expect(switcher).toBeVisible();
    await switcher.click();
    await page.getByRole("option", { name: ORG_NAME }).click();
    await expect(page).toHaveURL(new RegExp(`/app/org/${orgId}$`));
    await expect(page.getByRole("heading", { level: 1, name: ORG_NAME })).toBeVisible();
    await expect(page.getByRole("main").getByText("Owner", { exact: true })).toBeVisible();

    const totals = page.getByRole("grid", { name: "Workspace totals" });
    await expect(totals).toBeVisible();
    const workspaceRow = totals.getByRole("row").filter({ hasText: ORG_NAME });
    // HOM-320: the demo seed writes no ledger rows (HOM-326)
    await expect(workspaceRow.getByRole("gridcell").first()).toHaveText("0");
    await expect(page.getByRole("img", { name: /Meetings per workspace/ })).toHaveAttribute("aria-label", /: 0/);

    // The organization group in the sidebar leads to the other pages.
    const group = page.getByRole("group", { name: "Organization" });
    await group.getByRole("link", { name: "Organization members" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Organization members" })).toBeVisible();
    const members = page.getByRole("grid", { name: "Organization members" });
    const ownerRow = members.getByRole("row").filter({ hasText: OWNER_NAME });
    await expect(ownerRow).toHaveCount(1);
    await expect(ownerRow.getByText("owner@demo.example.com")).toBeVisible();
    await expect(ownerRow.getByText("Active")).toBeVisible();
    // The owner cannot manage their own row.
    await expect(ownerRow.getByRole("button")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Add member" })).toBeVisible();

    await group.getByRole("link", { name: "Workspaces" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Workspaces" })).toBeVisible();
    const workspaces = page.getByRole("grid", { name: "Organization workspaces" });
    const seeded = workspaces.getByRole("row").filter({ hasText: "default" });
    await expect(seeded).toHaveCount(1);
    await expect(seeded.getByText("Owner", { exact: true })).toBeVisible();

    await group.getByRole("link", { name: "Scope grants" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Scope grants" })).toBeVisible();
    await expect(page.getByText("No scope grants yet.")).toBeVisible();
  });

  test("an organization page is right to left in Persian", async ({ page }) => {
    refused = await readOnly(page);
    await resumeSession(page, "owner", "fa");
    await page.goto(consolePath(`/org/${orgId}/members`));
    await expect(page.getByRole("heading", { level: 1, name: "اعضای سازمان" })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.getByRole("grid", { name: "اعضای سازمان" }).getByText(OWNER_NAME)).toBeVisible();
    await expect(page.getByRole("group", { name: "سازمان" })).toBeVisible();
  });

  test("a member of no organization sees no switcher and gets no access", async ({ page }) => {
    refused = await readOnly(page);
    await resumeSession(page, "member");
    const list = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/v1/organizations");
    await page.goto(consolePath("/meetings"));
    const answer = await list;
    expect(answer.status()).toBe(200);
    expect(((await answer.json()) as { items: unknown[] }).items).toEqual([]);
    await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
    await expect(page.getByRole("button", { name: /Organization/ })).toHaveCount(0);

    await page.goto(consolePath(`/org/${orgId}`));
    await expect(page.getByRole("heading", { level: 1, name: "No access" })).toBeVisible();
    await expect(page.getByText("This organization does not exist or you are not a member.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Back to overview" })).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`/app/org/${orgId}$`));
  });
});
