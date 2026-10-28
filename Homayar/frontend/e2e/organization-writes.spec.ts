import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";
import type { OrgMember, OrgWorkspace, ScopeGrant } from "../src/types/organizations";

/**
 * The organization write flows (HOM-193) in a real browser. The session is
 * real (the saved owner session), but every request to
 * `/api/v1/organizations**` is answered here, from in-memory state: the demo
 * backend is shared with other teams and must not change, and its seed has
 * no analyst to grant a scope to. Each test asserts that no organization
 * request reached the real API.
 */

const ORG_ID = "e2e-org-0001";
const MOCK_HEADER = "x-e2e-org-mock";
const DAY = 86_400_000;

interface Write {
  method: string;
  path: string;
  body: unknown;
}

interface MockOrg {
  role: string;
  members: OrgMember[];
  workspaces: OrgWorkspace[];
  grants: ScopeGrant[];
  writes: Write[];
}

/** Verified accounts the mocked `POST /members` knows (otherwise `invalid_user`). */
const DIRECTORY: Record<string, { user_id: string; display_name: string }> = {
  "ana@e2e.example.com": { user_id: "u-ana", display_name: "Ana Karimi" },
};

function member(overrides: Partial<OrgMember>): OrgMember {
  const now = new Date().toISOString();
  return {
    id: "om-x",
    organization_id: ORG_ID,
    user_id: "u-x",
    email: null,
    display_name: "Someone",
    role: "analyst",
    is_active: true,
    valid_from: new Date(Date.now() - 30 * DAY).toISOString(),
    valid_until: null,
    revoked_at: null,
    is_effective: true,
    granted_by_user_id: null,
    created_at: now,
    ...overrides,
  };
}

function seed(role = "owner"): MockOrg {
  return {
    role,
    members: [
      member({ id: "om-owner", user_id: "u-owner", email: "boss@e2e.example.com", display_name: "Boss Owner", role: "owner" }),
      member({ id: "om-nima", user_id: "u-nima", email: "nima@e2e.example.com", display_name: "Nima Rahimi", role: "analyst" }),
    ],
    workspaces: [
      {
        id: "ws-main",
        organization_id: ORG_ID,
        name: "Main office",
        slug: "main",
        status: "active",
        record_scope: "workspace",
        my_workspace_role: "owner",
        scoped_team_ids: null,
        created_at: new Date().toISOString(),
      },
    ],
    grants: [],
    writes: [],
  };
}

/** The server's `is_effective`, recomputed on every read. */
function withEffect(row: OrgMember): OrgMember {
  const now = Date.now();
  const effective =
    row.revoked_at === null &&
    row.is_active &&
    Date.parse(row.valid_from) <= now &&
    (row.valid_until === null || Date.parse(row.valid_until) > now);
  return { ...row, is_effective: effective };
}

async function answer(route: Route, state: MockOrg) {
  const request = route.request();
  const url = new URL(request.url());
  const method = request.method();
  const raw = request.postData();
  const body: Record<string, unknown> | null = raw ? JSON.parse(raw) : null;
  if (method !== "GET") state.writes.push({ method, path: url.pathname, body });

  const reply = (status: number, json?: unknown) =>
    route.fulfill({
      status,
      headers: { [MOCK_HEADER]: "1" },
      contentType: "application/json",
      body: json === undefined ? "" : JSON.stringify(json),
    });
  const refuse = (status: number, code: string) =>
    reply(status, { error: { code, message: code, request_id: "e2e-mock" } });

  const [orgId, collection, id, sub] = url.pathname
    .replace(/^\/api\/v1\/organizations\/?/, "")
    .split("/")
    .filter(Boolean)
    .map(decodeURIComponent);
  const now = new Date().toISOString();
  const list = <T>(items: T[]) => reply(200, { items, total: items.length });

  if (!orgId && method === "GET") {
    return list([{ id: ORG_ID, name: "E2E Holding", slug: "e2e", status: "active", role: state.role, valid_until: null }]);
  }
  if (orgId !== ORG_ID) return refuse(404, "organization_not_found");

  if (!collection && method === "GET") {
    return reply(200, {
      id: ORG_ID,
      name: "E2E Holding",
      slug: "e2e",
      status: "active",
      role: state.role,
      unrestricted: true,
      workspace_count: state.workspaces.length,
      created_at: now,
    });
  }

  if (collection === "members") {
    if (method === "GET" && !id) return list(state.members.map(withEffect));
    if (method === "POST" && !id && body) {
      const account = DIRECTORY[String(body.email)];
      if (!account) return refuse(422, "invalid_user");
      const created = withEffect(
        member({
          id: `om-${state.members.length + 1}`,
          user_id: account.user_id,
          email: String(body.email),
          display_name: account.display_name,
          role: String(body.role),
          valid_from: typeof body.valid_from === "string" ? body.valid_from : now,
          valid_until: typeof body.valid_until === "string" ? body.valid_until : null,
        }),
      );
      state.members.push(created);
      return reply(201, created);
    }
    const index = state.members.findIndex((row) => row.id === id);
    if (index === -1) return refuse(404, "organization_member_not_found");
    if (method === "PATCH" && body) {
      const next = { ...state.members[index] };
      if (typeof body.role === "string") next.role = body.role;
      if (typeof body.valid_until === "string") next.valid_until = body.valid_until;
      if (body.clear_valid_until === true) next.valid_until = null;
      state.members[index] = next;
      return reply(200, withEffect(next));
    }
    if (method === "DELETE") {
      state.members[index] = { ...state.members[index], revoked_at: now };
      return reply(204);
    }
  }

  if (collection === "workspaces") {
    if (method === "GET" && !id) return list(state.workspaces);
    if (method === "POST" && !id && body) {
      const created: OrgWorkspace = {
        ...state.workspaces[0],
        id: `ws-${state.workspaces.length + 1}`,
        name: String(body.name),
        slug: `e2e-${state.workspaces.length + 1}`,
        created_at: now,
      };
      state.workspaces.push(created);
      return reply(201, created);
    }
    if (method === "POST" && id && sub === "members" && body) {
      const target = state.members.map(withEffect).find((row) => row.user_id === body.user_id && row.is_effective);
      if (!target) return refuse(404, "organization_member_not_found");
      return reply(201, { membership_id: "wm-1", workspace_id: id, user_id: target.user_id, role: body.role, is_active: true });
    }
  }

  if (collection === "scope-grants") {
    if (method === "GET" && !id) return list(state.grants);
    if (method === "POST" && !id && body) {
      const target = state.members.find((row) => row.id === body.membership_id);
      if (!target) return refuse(404, "organization_member_not_found");
      const created: ScopeGrant = {
        id: `sg-${state.grants.length + 1}`,
        organization_id: ORG_ID,
        membership_id: target.id,
        user_id: target.user_id,
        workspace_id: String(body.workspace_id),
        team_id: null,
        granted_by_user_id: "u-owner",
        revoked_at: null,
        created_at: now,
      };
      state.grants.push(created);
      return reply(201, created);
    }
    const index = state.grants.findIndex((row) => row.id === id);
    if (method === "DELETE" && index !== -1) {
      state.grants[index] = { ...state.grants[index], revoked_at: now };
      return reply(204);
    }
    return refuse(404, "scope_grant_not_found");
  }

  if (collection === "aggregates" && method === "GET") {
    return reply(200, {
      organization_id: ORG_ID,
      since: null,
      until: null,
      generated_at: now,
      items: state.workspaces.map((workspace) => ({
        workspace_id: workspace.id,
        workspace_name: workspace.name,
        team_ids: null,
        meetings_total: 0,
        meetings_by_status: {},
        audio_seconds_total: 0,
        action_items_total: 0,
        coverage: null,
        scorecard: { status: "not_available", version: null, digest: null, reason: "scorecard_results_unavailable" },
        missing_data: { indicators: ["empty_population"] },
        disclosure: { status: "released", reason: null, kind: null, masked: [] },
      })),
      disclosure_policy: {
        version: "disclosure/1",
        min_cohort_agents: 5,
        grid: { unit: "week", timezone: "Asia/Tehran", week_starts_on: "saturday", closed_weeks_only: true },
      },
      coverage_window: { from: "2026-09-25T20:30:00.000Z", to: "2026-10-23T20:30:00.000Z", basis: "ingestion_received_at" },
    });
  }

  return refuse(404, "not_found");
}

function isOrgRequest(url: string) {
  return new URL(url).pathname.startsWith("/api/v1/organizations");
}

/**
 * Answers every organization request from `state`, and records any
 * organization response that did not come from this mock (so it would have
 * reached the real API).
 */
async function mockOrganizations(page: Page, state: MockOrg) {
  const notMocked: string[] = [];
  let seen = 0;
  page.on("request", (request) => {
    if (isOrgRequest(request.url())) seen += 1;
  });
  page.on("response", (response) => {
    if (isOrgRequest(response.url()) && response.headers()[MOCK_HEADER] !== "1") {
      notMocked.push(`${response.request().method()} ${response.url()}`);
    }
  });
  await page.route("**/api/v1/organizations**", (route) => answer(route, state));
  return { notMocked, seen: () => seen };
}

/** A day `days` from today, as a date input takes it, in the browser's own time zone. */
function dayFromToday(days: number) {
  const date = new Date(Date.now() + days * DAY);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/**
 * What the console sends for a picked day, asked of the browser (its time
 * zone): a start day is the midnight that starts it, an end day is included,
 * so it is the midnight that starts the next day.
 */
function dayIso(page: Page, day: string, edge: "start" | "end") {
  return page.evaluate(
    ([value, which]) => {
      const [year, month, date] = value.split("-").map(Number);
      return new Date(year, month - 1, which === "end" ? date + 1 : date).toISOString();
    },
    [day, edge] as const,
  );
}

/** Types a day into the HeroUI date field named `label` (English pages: month, day, year). */
async function typeDay(page: Page, scope: Locator, label: string, day: string) {
  const [year, month, date] = day.split("-");
  await scope.getByRole("group", { name: label }).getByRole("spinbutton").first().click();
  await page.keyboard.type(`${month}${date}${year}`);
}

function lastWrite(state: MockOrg) {
  return state.writes[state.writes.length - 1];
}

async function choose(page: Page, trigger: Locator, option: string | RegExp) {
  await trigger.click();
  await page.getByRole("option", { name: option }).click();
}

test.describe("organization write flows (mocked organization API)", () => {
  let mock: Awaited<ReturnType<typeof mockOrganizations>> | null = null;

  test.afterEach(() => {
    expect(mock?.seen() ?? 0, "the page asked the organization API").toBeGreaterThan(0);
    expect(mock?.notMocked, "no organization request may reach the real API").toEqual([]);
    mock = null;
  });

  test("adds an analyst with a validity window, edits the end date and revokes", async ({ page }) => {
    const state = seed();
    mock = await mockOrganizations(page, state);
    await resumeSession(page, "owner");
    await page.goto(consolePath(`/org/${ORG_ID}/members`));
    await expect(page.getByRole("heading", { level: 1, name: "Organization members" })).toBeVisible();

    await page.getByRole("button", { name: "Add member" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("heading", { name: "Add organization member" })).toBeVisible();
    await dialog.getByLabel("Email").fill("ana@e2e.example.com");
    await choose(page, dialog.getByRole("button", { name: /Role/ }), "Analyst");
    const fromDay = dayFromToday(14);
    const untilDay = dayFromToday(380);
    await typeDay(page, dialog, "Valid from", fromDay);
    await typeDay(page, dialog, "Valid until", untilDay);
    await dialog.getByRole("button", { name: "Add member" }).click();

    await expect(page.getByText("Member added.")).toBeVisible();
    await expect(dialog).toHaveCount(0);
    expect(lastWrite(state)).toEqual({
      method: "POST",
      path: `/api/v1/organizations/${ORG_ID}/members`,
      body: {
        email: "ana@e2e.example.com",
        role: "analyst",
        valid_from: await dayIso(page, fromDay, "start"),
        valid_until: await dayIso(page, untilDay, "end"),
      },
    });
    const table = page.getByRole("grid", { name: "Organization members" });
    const row = table.getByRole("row").filter({ hasText: "Ana Karimi" });
    await expect(row.getByText("Scheduled")).toBeVisible();

    // A new end date: the PATCH holds only valid_until.
    await row.getByRole("button", { name: "Edit Ana Karimi" }).click();
    const edit = page.getByRole("dialog");
    await expect(edit.getByRole("heading", { name: "Edit membership" })).toBeVisible();
    await expect(edit.getByRole("button", { name: "Save changes" })).toBeDisabled();
    const newUntil = dayFromToday(200);
    // The field opens on the last included day the member was given (month, day, year).
    const segments = edit.getByRole("group", { name: "Valid until" }).getByRole("spinbutton");
    const [untilYear, untilMonth, untilDate] = untilDay.split("-").map((part) => String(Number(part)));
    await expect(segments.nth(0)).toHaveAttribute("aria-valuenow", untilMonth);
    await expect(segments.nth(1)).toHaveAttribute("aria-valuenow", untilDate);
    await expect(segments.nth(2)).toHaveAttribute("aria-valuenow", untilYear);
    await typeDay(page, edit, "Valid until", newUntil);
    await edit.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Membership updated.")).toBeVisible();
    expect(lastWrite(state)).toEqual({
      method: "PATCH",
      path: `/api/v1/organizations/${ORG_ID}/members/om-3`,
      body: { valid_until: await dayIso(page, newUntil, "end") },
    });

    await row.getByRole("button", { name: "Revoke Ana Karimi" }).click();
    const confirm = page.getByRole("alertdialog");
    await expect(confirm.getByRole("heading", { name: "Revoke membership?" })).toBeVisible();
    await confirm.getByRole("button", { name: "Revoke" }).click();
    await expect(page.getByText("Membership revoked.")).toBeVisible();
    expect(lastWrite(state)).toEqual({ method: "DELETE", path: `/api/v1/organizations/${ORG_ID}/members/om-3`, body: null });
    await expect(row.getByText("Revoked")).toBeVisible();
    await expect(row.getByRole("button")).toHaveCount(0);
  });

  test("creates a workspace", async ({ page }) => {
    const state = seed();
    mock = await mockOrganizations(page, state);
    await resumeSession(page, "owner");
    await page.goto(consolePath(`/org/${ORG_ID}/workspaces`));
    await expect(page.getByRole("heading", { level: 1, name: "Workspaces" })).toBeVisible();

    await page.getByRole("button", { name: "Create workspace" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Name").fill("  E2E Branch  ");
    await dialog.getByRole("button", { name: "Create workspace" }).click();
    await expect(page.getByText("Workspace created.")).toBeVisible();
    expect(lastWrite(state)).toEqual({
      method: "POST",
      path: `/api/v1/organizations/${ORG_ID}/workspaces`,
      body: { name: "E2E Branch" },
    });
    await expect(page.getByRole("grid", { name: "Organization workspaces" }).getByRole("row").filter({ hasText: "E2E Branch" })).toHaveCount(1);
  });

  test("adds an organization member to a workspace", async ({ page }) => {
    const state = seed();
    mock = await mockOrganizations(page, state);
    await resumeSession(page, "owner");
    await page.goto(consolePath(`/org/${ORG_ID}/workspaces`));

    await page.getByRole("button", { name: "Add member to Main office" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("heading", { name: "Add member to workspace" })).toBeVisible();
    await choose(page, dialog.getByRole("button", { name: /Member/ }), /Nima Rahimi/);
    await choose(page, dialog.getByRole("button", { name: /Role/ }), "Viewer");
    await dialog.getByRole("button", { name: "Add member" }).click();
    await expect(page.getByText("Nima Rahimi was added to Main office.")).toBeVisible();
    expect(lastWrite(state)).toEqual({
      method: "POST",
      path: `/api/v1/organizations/${ORG_ID}/workspaces/ws-main/members`,
      body: { user_id: "u-nima", role: "viewer" },
    });
  });

  test("adds a scope grant and revokes it", async ({ page }) => {
    const state = seed();
    mock = await mockOrganizations(page, state);
    await resumeSession(page, "owner");
    await page.goto(consolePath(`/org/${ORG_ID}/scope-grants`));
    await expect(page.getByText("No scope grants yet.")).toBeVisible();

    await page.getByRole("button", { name: "Add scope grant" }).click();
    const dialog = page.getByRole("dialog");
    await choose(page, dialog.getByRole("button", { name: /Member/ }), /Nima Rahimi/);
    await choose(page, dialog.getByRole("button", { name: /Workspace/ }), "Main office");
    await dialog.getByRole("button", { name: "Grant scope" }).click();
    await expect(page.getByText("Scope granted.")).toBeVisible();
    expect(lastWrite(state)).toEqual({
      method: "POST",
      path: `/api/v1/organizations/${ORG_ID}/scope-grants`,
      body: { membership_id: "om-nima", workspace_id: "ws-main" },
    });

    const row = page.getByRole("grid", { name: "Scope grants" }).getByRole("row").filter({ hasText: "Nima Rahimi" });
    await expect(row.getByText("Main office")).toBeVisible();
    await expect(row.getByText("Whole workspace")).toBeVisible();
    await row.getByRole("button", { name: "Revoke grant for Nima Rahimi" }).click();
    const confirm = page.getByRole("alertdialog");
    await expect(confirm.getByRole("heading", { name: "Revoke scope grant?" })).toBeVisible();
    await confirm.getByRole("button", { name: "Revoke" }).click();
    await expect(page.getByText("Scope grant revoked.")).toBeVisible();
    expect(lastWrite(state)).toEqual({ method: "DELETE", path: `/api/v1/organizations/${ORG_ID}/scope-grants/sg-1`, body: null });
    await expect(row.getByText("Revoked")).toBeVisible();
  });

  test("offers an administrator no Administrator role to grant", async ({ page }) => {
    const state = seed("admin");
    mock = await mockOrganizations(page, state);
    await resumeSession(page, "owner");
    await page.goto(consolePath(`/org/${ORG_ID}/members`));
    await page.getByRole("button", { name: "Add member" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: /Role/ }).click();
    const options = page.getByRole("option");
    await expect(options).toHaveText(["Analyst", "Executive"]);
    await expect(page.getByRole("option", { name: "Administrator" })).toHaveCount(0);
    expect(state.writes).toEqual([]);
  });
});
