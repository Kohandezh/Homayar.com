/**
 * Test-only helpers for the organization pages. Nothing here is imported by
 * application code. The answers mirror the contract in backend/schemas/
 * organizations.py; every page is rendered through <AppRoutes /> with a
 * mocked `api`, the way src/AppRoutes.test.tsx does.
 */
import { Suspense } from "react";
import { screen, within } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { useLocation } from "react-router";
import { vi } from "vitest";

import { AppRoutes } from "@/AppRoutes";
import api from "@/lib/api";
import type { AuthContextValue } from "@/lib/auth-context";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type {
  OrgMember,
  OrgWorkspace,
  Organization,
  OrganizationSummary,
  ScopeGrant,
  WorkspaceAggregate,
} from "@/types/organizations";

export const ORG_ID = "org-1";
export const ME = FIXTURE_USER.id;
export const T = "2026-09-24T12:00:00Z";
/** Pages wait for the guard and then their own reads; the machine may be slow. */
export const LOAD = { timeout: 10_000 };

export function organization(overrides: Partial<Organization> = {}): Organization {
  return {
    id: ORG_ID,
    name: "Acme Group",
    slug: "acme",
    status: "active",
    role: "owner",
    unrestricted: true,
    workspace_count: 1,
    created_at: T,
    ...overrides,
  };
}

export function summary(overrides: Partial<OrganizationSummary> = {}): OrganizationSummary {
  return { id: ORG_ID, name: "Acme Group", slug: "acme", status: "active", role: "owner", valid_until: null, ...overrides };
}

export function member(overrides: Partial<OrgMember> = {}): OrgMember {
  return {
    id: "om-1",
    organization_id: ORG_ID,
    user_id: ME,
    email: FIXTURE_USER.email,
    display_name: "Person",
    role: "owner",
    is_active: true,
    valid_from: "2026-01-01T00:00:00Z",
    valid_until: null,
    revoked_at: null,
    is_effective: true,
    granted_by_user_id: null,
    created_at: T,
    ...overrides,
  };
}

export const OWNER_ROW = member();
export const ALI = member({ id: "om-2", user_id: "user-2", email: "ali@example.com", display_name: "Ali", role: "analyst" });
export const SARA = member({ id: "om-3", user_id: "user-3", email: "sara@example.com", display_name: "Sara", role: "admin" });

export function workspaceRow(overrides: Partial<OrgWorkspace> = {}): OrgWorkspace {
  return {
    id: "ws-1",
    organization_id: ORG_ID,
    name: "Tehran office",
    slug: "tehran",
    status: "active",
    record_scope: "workspace",
    my_workspace_role: "owner",
    scoped_team_ids: null,
    created_at: T,
    ...overrides,
  };
}

export function grant(overrides: Partial<ScopeGrant> = {}): ScopeGrant {
  return {
    id: "sg-1",
    organization_id: ORG_ID,
    membership_id: ALI.id,
    user_id: ALI.user_id,
    workspace_id: "ws-1",
    team_id: null,
    granted_by_user_id: ME,
    revoked_at: null,
    created_at: T,
    ...overrides,
  };
}

export function aggregate(overrides: Partial<WorkspaceAggregate> = {}): WorkspaceAggregate {
  return {
    workspace_id: "ws-1",
    workspace_name: "Tehran office",
    team_ids: null,
    meetings_total: 30,
    meetings_by_status: { completed: 25, failed: 2 },
    audio_seconds_total: 1165,
    action_items_total: 27,
    ...overrides,
  };
}

type Answer<T> = T | Error | "pending";

export interface OrgReads {
  /** The organization id the reads answer for (default ORG_ID). */
  id?: string;
  list?: Answer<OrganizationSummary[]>;
  detail?: Answer<Organization>;
  /** The guard's answer from its second read on (a membership that ended meanwhile). */
  detailLater?: Answer<Organization>;
  members?: Answer<OrgMember[]>;
  workspaces?: Answer<OrgWorkspace[]>;
  grants?: Answer<ScopeGrant[]>;
  aggregates?: Answer<WorkspaceAggregate[]>;
}

function answer<T>(value: Answer<T>, wrap: (data: T) => unknown) {
  if (value === "pending") return new Promise<never>(() => undefined);
  if (value instanceof Error) return Promise.reject(value);
  return Promise.resolve(okResponse(wrap(value)));
}

/**
 * Serves every org read from `reads`; the detail role decides access. The
 * switcher list defaults to the same org with the same role. Anything else
 * is a 404, like the other console tests.
 */
export function mockOrgReads(reads: OrgReads = {}) {
  const detail = reads.detail ?? organization();
  const role = detail instanceof Error || detail === "pending" ? "owner" : detail.role;
  const base = `/organizations/${encodeURIComponent(reads.id ?? ORG_ID)}`;
  let detailReads = 0;
  return vi.spyOn(api, "get").mockImplementation((url: string) => {
    if (url === "/organizations") {
      const list = reads.list ?? [summary({ role })];
      return answer(list, (items) => ({ items, total: items.length }));
    }
    if (url === base) {
      detailReads += 1;
      return answer(detailReads > 1 && reads.detailLater ? reads.detailLater : detail, (data) => data);
    }
    if (url === `${base}/members`) {
      return answer(reads.members ?? [OWNER_ROW, ALI], (items) => ({ items, total: items.length }));
    }
    if (url === `${base}/workspaces`) {
      return answer(reads.workspaces ?? [workspaceRow()], (items) => ({ items, total: items.length }));
    }
    if (url === `${base}/scope-grants`) {
      return answer(reads.grants ?? [], (items) => ({ items, total: items.length }));
    }
    if (url === `${base}/aggregates`) {
      return answer(reads.aggregates ?? [aggregate()], (items) => ({
        organization_id: ORG_ID,
        since: null,
        until: null,
        generated_at: T,
        items,
      }));
    }
    return Promise.reject(apiError(404, "not_found"));
  });
}

export function requestedUrls(spy: ReturnType<typeof mockOrgReads>) {
  return spy.mock.calls.map(([url]) => url);
}

/** The org page reads (not the switcher list, not the guard read). */
export function pageReads(spy: ReturnType<typeof mockOrgReads>) {
  const base = `/organizations/${ORG_ID}/`;
  return requestedUrls(spy).filter((url) => url.startsWith(base));
}

export function location() {
  return screen.getByTestId("location").textContent;
}

/** Renders the whole console at `route`, signed in, with the fixture workspace selected unless `auth` says otherwise. */
export function renderOrg(route: string, lang: "fa" | "en" = "en", auth: Partial<AuthContextValue> = {}) {
  // Inside the function, like the providers of renderConsole: this file exports no component.
  function LocationProbe() {
    const location = useLocation();
    return <output data-testid="location">{location.pathname}</output>;
  }
  window.localStorage.removeItem("homayar_lang");
  return renderConsole(
    <Suspense fallback={<p>Loading page</p>}>
      <AppRoutes />
      <LocationProbe />
    </Suspense>,
    {
      route,
      lang,
      auth: {
        status: "authenticated",
        user: FIXTURE_USER,
        workspace: FIXTURE_WORKSPACE,
        workspaces: [FIXTURE_WORKSPACE],
        ...auth,
      },
    },
  );
}

export function orgUrl(path = "") {
  return `/org/${ORG_ID}${path}`;
}

/** Opens a HeroUI Select by its trigger and picks one option. */
export async function chooseOption(user: UserEvent, trigger: HTMLElement, optionName: string | RegExp) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name: optionName }, LOAD));
}

/** The option names of a HeroUI Select, then closes it with Escape. */
export async function optionNames(user: UserEvent, trigger: HTMLElement) {
  await user.click(trigger);
  const listbox = await screen.findByRole("listbox", undefined, LOAD);
  const names = Array.from(listbox.querySelectorAll('[role="option"]')).map((option) => option.textContent?.trim());
  await user.keyboard("{Escape}");
  return names;
}

export function setupUser() {
  return userEvent.setup();
}

/** The local midnight that starts `day` ("2026-11-01"), as ISO: what a start day sends. */
export function startOfDayIso(day: string) {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(year, month - 1, date).toISOString();
}

/** The local midnight that starts the day after `day`, as ISO: what an included end day sends. */
export function endOfDayIso(day: string) {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(year, month - 1, date + 1).toISOString();
}

/**
 * Types a Gregorian day into the HeroUI date field named `label` inside
 * `container` (English pages: month, day, year).
 */
export async function typeDay(user: UserEvent, container: HTMLElement, label: string, day: string) {
  const [year, month, date] = day.split("-");
  const group = within(container).getByRole("group", { name: label });
  await user.click(within(group).getAllByRole("spinbutton")[0]);
  await user.keyboard(`${month}${date}${year}`);
}
