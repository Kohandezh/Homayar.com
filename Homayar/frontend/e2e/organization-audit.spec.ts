import { expect, test, type Browser, type Page, type Route } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";
import type { AuditEvent, AuditEventTypeEntry } from "../src/types/organizations";

/**
 * The organization audit log and records pages (HOM-243). The real-backend
 * tests are read-only against the demo seed and never assume the seed wrote
 * organization audit events; the interaction test answers the two audit
 * routes from fixtures, with auth and everything else real. Any non-GET to
 * an organization route is aborted in the browser and fails the test.
 */

const ORG_NAME = "شرکت نمونه همایار";
const SECOND_PAGE_CURSOR = "e2e-cursor-page-2";

/** The seed gives the organization a new id in every database, so it is read at run time. */
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

function auditEvent(id: string, minutesAgo: number, overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    id,
    actor_user_id: null,
    event_type: "organization.member.granted",
    target_type: "organization_membership",
    target_id: `om-${id}`,
    outcome: "success",
    request_id: `req-${id}`,
    details: { organization_id: "org", role: "analyst" },
    occurred_at: new Date(Date.parse("2026-10-10T12:00:00.000Z") - minutesAgo * 60_000).toISOString(),
    ...overrides,
  };
}

function typeCatalog(): AuditEventTypeEntry[] {
  return [
    ["organization.member.changed", "organization_membership"],
    ["organization.member.granted", "organization_membership"],
    ["organization.member.revoked", "organization_membership"],
    ["organization.scope.granted", "organization_scope_grant"],
    ["organization.scope.revoked", "organization_scope_grant"],
    ["organization.workspace.created", "workspace"],
    ["organization.workspace.member_installed", "membership"],
    ["organization.workspace.member_removed", "membership"],
  ].map(([event_type, target_type]) => ({ event_type, group: "organization", target_type }));
}

interface MockAudit {
  writes: string[];
  /** The query strings of every audit-events GET, so filters can be asserted on them. */
  listQueries: string[];
}

/** Answers the two audit routes from fixtures; everything else stays real. */
async function mockOrgAudit(page: Page): Promise<MockAudit> {
  const state: MockAudit = { writes: [], listQueries: [] };
  const pageOne = [
    auditEvent("ev-first", 0),
    auditEvent("ev-created", 5, {
      event_type: "organization.workspace.created",
      target_type: "workspace",
      target_id: "ws-e2e",
      details: { organization_id: "org", name: "Sales", slug: "sales" },
    }),
  ];
  const pageTwo = [auditEvent("ev-old", 60, { event_type: "organization.scope.granted", target_type: "organization_scope_grant" })];
  const reply = (route: Route, json: unknown) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(json),
    });
  await page.route("**/api/v1/organizations/*/audit-events**", (route) => {
    const request = route.request();
    if (request.method() !== "GET") {
      state.writes.push(`${request.method()} ${request.url()}`);
      return route.abort();
    }
    const url = new URL(request.url());
    state.listQueries.push(url.searchParams.toString());
    return url.searchParams.get("cursor") === SECOND_PAGE_CURSOR
      ? reply(route, { items: pageTwo, next_cursor: null })
      : reply(route, { items: pageOne, next_cursor: SECOND_PAGE_CURSOR });
  });
  await page.route("**/api/v1/organizations/*/audit-event-types", (route) => {
    if (route.request().method() !== "GET") {
      state.writes.push(`${route.request().method()} ${route.request().url()}`);
      return route.abort();
    }
    return reply(route, { items: typeCatalog() });
  });
  return state;
}

test.describe("organization audit log and records (HOM-243)", () => {
  let refused: string[] = [];
  let orgId = "";

  test.beforeAll(async ({ browser }) => {
    orgId = await demoOrganizationId(browser);
  });

  test.afterEach(() => {
    expect(refused, "no organization write may reach the shared backend").toEqual([]);
  });

  test("the owner opens the audit page and sees rows or the empty state", async ({ page }) => {
    refused = await readOnly(page);
    await resumeSession(page, "owner");
    await page.goto(consolePath(`/org/${orgId}/audit`));
    await expect(page.getByRole("heading", { level: 1, name: "Audit log" })).toBeVisible();
    const grid = page.getByRole("grid", { name: "Organization audit events" });
    const empty = page.getByText("No events have been recorded yet.");
    await expect(grid.or(empty).first()).toBeVisible();
  });

  test("with the audit routes mocked: rows, drawer, the event type filter and cursor paging", async ({ page }) => {
    // Registered first, so the fixture routes below (matched first) answer
    // the two audit paths and this guard still refuses every non-GET.
    refused = await readOnly(page);
    const mock = await mockOrgAudit(page);
    await resumeSession(page, "owner");
    await page.goto(consolePath(`/org/${orgId}/audit`));

    const grid = page.getByRole("grid", { name: "Organization audit events" });
    await expect(grid).toBeVisible();
    await expect(grid.getByRole("row").filter({ hasText: "Organization member added" })).toHaveCount(1);
    await expect(grid.getByRole("row").filter({ hasText: "Organization workspace created" })).toHaveCount(1);

    // A row opens the drawer with its raw event type and request id.
    await page.getByRole("gridcell", { name: "Organization member added" }).click();
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    await expect(drawer).toContainText("organization.member.granted");
    await expect(drawer).toContainText("req-ev-first");
    await page.keyboard.press("Escape");
    await expect(drawer).not.toBeVisible();

    // The event type filter reaches the request as event_type=...
    await page.getByRole("button", { name: /Event type/ }).click();
    await page.getByRole("option", { name: "Organization scope granted" }).click();
    await expect
      .poll(() => mock.listQueries.some((query) => query.includes("event_type=organization.scope.granted")))
      .toBe(true);

    // The next page loads with cursor=...
    await page.getByRole("button", { name: /Next/ }).click();
    await expect.poll(() => mock.listQueries.some((query) => query.includes(`cursor=${SECOND_PAGE_CURSOR}`))).toBe(
      true,
    );
    await expect(grid.getByRole("row").filter({ hasText: "Organization scope granted" })).toHaveCount(1);
    await expect(page.getByText("Page 2")).toBeVisible();
    expect(mock.writes, "the mocked routes are read-only too").toEqual([]);
  });

  test("the owner sees the seeded meetings in records, counted from the API", async ({ page }) => {
    refused = await readOnly(page);
    await resumeSession(page, "owner");
    const counted = await page.request.get(`/api/v1/organizations/${orgId}/records`);
    expect(counted.status(), "GET /records with the owner's session").toBe(200);
    const { total } = (await counted.json()) as { total: number };
    expect(total).toBeGreaterThan(0);

    await page.goto(consolePath(`/org/${orgId}/records`));
    const grid = page.getByRole("grid", { name: "Organization records" });
    await expect(grid).toBeVisible();
    await expect(grid.getByRole("row").filter({ hasText: ORG_NAME })).not.toHaveCount(0);
    await expect(page.getByText(`Total: ${total} ${total === 1 ? "record" : "records"}`)).toBeVisible();
  });

  test("the records page is right to left in Persian", async ({ page }) => {
    refused = await readOnly(page);
    await resumeSession(page, "owner", "fa");
    await page.goto(consolePath(`/org/${orgId}/records`));
    await expect(page.getByRole("heading", { level: 1, name: "سوابق" })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.getByRole("grid", { name: "سوابق سازمان" })).toBeVisible();
  });

  test("a member of no organization gets no access on the audit URL, with no audit request", async ({ page }) => {
    // A listener, not a route: it sees every request the page sends, whichever
    // route handler answers it.
    const auditRequests: string[] = [];
    page.on("request", (request) => {
      if (/\/api\/v1\/organizations\/[^/]+\/audit-event/.test(request.url())) auditRequests.push(request.url());
    });
    refused = await readOnly(page);
    await resumeSession(page, "member");
    await page.goto(consolePath(`/org/${orgId}/audit`));
    await expect(page.getByRole("heading", { level: 1, name: "No access" })).toBeVisible();
    await expect(page.getByText("This organization does not exist or you are not a member.")).toBeVisible();
    expect(auditRequests, "the page under the guard never mounted, so nothing was asked").toEqual([]);
  });
});
