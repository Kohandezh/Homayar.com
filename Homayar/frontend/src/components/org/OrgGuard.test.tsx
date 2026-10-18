import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";

import api from "@/lib/api";
import { apiError, okResponse } from "@/test/fixtures";
import {
  ALI,
  LOAD,
  ORG_ID,
  OWNER_ROW,
  SARA,
  grant,
  location,
  mockOrgReads,
  orgUrl,
  organization,
  pageReads,
  renderOrg,
  requestedUrls,
  setupUser,
  summary,
  typeDay,
} from "@/components/org/org-test-kit";

const ROUTES = [
  { path: "", heading: "Acme Group", read: "aggregates" },
  { path: "/members", heading: "Organization members", read: "members" },
  { path: "/workspaces", heading: "Workspaces", read: "workspaces" },
  { path: "/scope-grants", heading: "Scope grants", read: "scope-grants" },
] as const;

/** Which routes each org role may open (backend ORGANIZATION_ROLE_PERMISSIONS). */
const OPENS: Record<string, readonly string[]> = {
  owner: ["", "/members", "/workspaces", "/scope-grants"],
  admin: ["", "/members", "/workspaces", "/scope-grants"],
  analyst: ["", "/workspaces"],
  executive: ["", "/workspaces"],
};

async function expectNoAccess() {
  expect(await screen.findByRole("heading", { level: 1, name: "No access" }, LOAD)).toBeInTheDocument();
  expect(screen.getByText("This organization does not exist or you are not a member.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Back to overview" })).toHaveAttribute("href", "/");
}

describe("organization guard", { timeout: 60_000 }, () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the loading state while the organization is read", async () => {
    mockOrgReads({ detail: "pending" });
    renderOrg(orgUrl("/members"));
    expect(await screen.findByText("Loading organization", undefined, LOAD)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1, name: "Organization members" })).not.toBeInTheDocument();
  });

  it.each(Object.entries(OPENS))("opens exactly the allowed routes for the %s role", async (role, allowed) => {
    for (const route of ROUTES) {
      const get = mockOrgReads({ detail: organization({ role, unrestricted: role === "owner" || role === "admin" }) });
      renderOrg(orgUrl(route.path));
      if (allowed.includes(route.path)) {
        expect(await screen.findByRole("heading", { level: 1, name: route.heading }, LOAD)).toBeInTheDocument();
        await waitFor(() => expect(pageReads(get).some((url) => url.endsWith(`/${route.read}`))).toBe(true), LOAD);
      } else {
        await expectNoAccess();
        // The page under the guard never mounted, so none of its reads started.
        expect(pageReads(get)).toEqual([]);
        expect(location()).toBe(orgUrl(route.path));
      }
      cleanup();
      vi.restoreAllMocks();
    }
  });

  it.each(["owner", "admin"])("gives the %s every write control", async (role) => {
    const reads = { detail: organization({ role }), members: [OWNER_ROW, ALI], grants: [grant()] };
    mockOrgReads(reads);
    const members = renderOrg(orgUrl("/members"));
    expect(await screen.findByRole("button", { name: "Edit Ali" }, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke Ali" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add member" })).toBeInTheDocument();
    members.unmount();

    const workspaces = renderOrg(orgUrl("/workspaces"));
    expect(await screen.findByRole("button", { name: "Add member to Tehran office" }, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create workspace" })).toBeInTheDocument();
    workspaces.unmount();

    renderOrg(orgUrl("/scope-grants"));
    expect(await screen.findByRole("button", { name: "Revoke grant for Ali" }, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add scope grant" })).toBeInTheDocument();
  });

  it.each(["analyst", "executive"])("renders no write control for the %s", async (role) => {
    const post = vi.spyOn(api, "post");
    mockOrgReads({ detail: organization({ role, unrestricted: false }) });
    const workspaces = renderOrg(orgUrl("/workspaces"));
    expect(await screen.findByRole("rowheader", { name: /Tehran office/ }, LOAD)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create workspace" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add member to/ })).not.toBeInTheDocument();
    workspaces.unmount();

    renderOrg(orgUrl(""));
    expect(await screen.findByRole("grid", { name: "Workspace totals" }, LOAD)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add|Create|Edit|Revoke/ })).not.toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("opens nothing for an unknown role string", async () => {
    for (const route of ROUTES) {
      const get = mockOrgReads({ detail: organization({ role: "superuser" }) });
      renderOrg(orgUrl(route.path));
      await expectNoAccess();
      expect(pageReads(get)).toEqual([]);
      cleanup();
      vi.restoreAllMocks();
    }
  });

  it("takes the role from the organization answer, not from the switcher list", async () => {
    const get = mockOrgReads({ detail: organization({ role: "executive" }), list: [summary({ role: "owner" })] });
    renderOrg(orgUrl("/members"));
    await expectNoAccess();
    expect(pageReads(get)).toEqual([]);
  });

  it("opens an owner-only page when the answer says owner, even if the switcher list says executive", async () => {
    const get = mockOrgReads({ detail: organization({ role: "owner" }), list: [summary({ role: "executive" })] });
    renderOrg(orgUrl("/members"));
    expect(await screen.findByRole("heading", { level: 1, name: "Organization members" }, LOAD)).toBeInTheDocument();
    await waitFor(() => expect(pageReads(get)).toContain(`/organizations/${ORG_ID}/members`), LOAD);
    expect(screen.queryByRole("heading", { level: 1, name: "No access" })).not.toBeInTheDocument();
  });

  it("opens org pages without a selected workspace, and keeps the workspace gate on /admin", async () => {
    const noWorkspace = { workspace: null, workspaces: [] };
    mockOrgReads({ detail: organization({ role: "analyst", unrestricted: false }) });
    const org = renderOrg(orgUrl(""), "en", noWorkspace);
    expect(await screen.findByRole("heading", { level: 1, name: "Acme Group" }, LOAD)).toBeInTheDocument();
    expect(screen.queryByText("No workspace available")).not.toBeInTheDocument();
    org.unmount();

    // Routes match without case, so the exemption does too.
    const upper = renderOrg(`/ORG/${ORG_ID}`, "en", noWorkspace);
    expect(await screen.findByRole("heading", { level: 1, name: "Acme Group" }, LOAD)).toBeInTheDocument();
    expect(screen.queryByText("No workspace available")).not.toBeInTheDocument();
    upper.unmount();

    renderOrg("/admin", "en", noWorkspace);
    expect(await screen.findByText("No workspace available", undefined, LOAD)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1, name: "Admin overview" })).not.toBeInTheDocument();
  });

  it("shows no access on any 404 of the guard read, keeps the URL and starts no page read", async () => {
    for (const code of ["organization_not_found", "not_found"]) {
      const get = mockOrgReads({ detail: apiError(404, code) });
      renderOrg(orgUrl("/members"));
      await expectNoAccess();
      expect(screen.queryByRole("heading", { name: "Organization members" })).not.toBeInTheDocument();
      expect(pageReads(get)).toEqual([]);
      expect(location()).toBe(orgUrl("/members"));
      // The layout stays around the state.
      expect(screen.getByRole("navigation", { name: "Main navigation" })).toBeInTheDocument();
      cleanup();
      vi.restoreAllMocks();
    }
  });

  it("describes another guard failure and reads again on retry", async () => {
    let fail = true;
    const get = vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === `/organizations/${ORG_ID}`) {
        // A 5xx gets the generic "not available" text; a 4xx shows the page fallback.
        if (fail) throw apiError(400, "organization_read_failed");
        return okResponse(organization());
      }
      if (url === "/organizations") return okResponse({ items: [], total: 0 });
      if (url.endsWith("/members")) return okResponse({ items: [OWNER_ROW], total: 1 });
      throw apiError(404, "not_found");
    });
    renderOrg(orgUrl("/members"));
    const alert = await screen.findByRole("alert", undefined, LOAD);
    expect(alert).toHaveTextContent("The organization could not be loaded.");
    expect(requestedUrls(get)).not.toContain(`/organizations/${ORG_ID}/members`);

    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Organization members" }, LOAD)).toBeInTheDocument();
  });

  it("sends /org without an id to the overview", async () => {
    mockOrgReads();
    renderOrg("/org");
    await waitFor(() => expect(location()).toBe("/"), LOAD);
  });

  it("says no access in Persian", async () => {
    mockOrgReads({ detail: apiError(404, "organization_not_found") });
    renderOrg(orgUrl("/members"), "fa");
    expect(await screen.findByRole("heading", { level: 1, name: "بدون دسترسی" }, LOAD)).toBeInTheDocument();
    expect(screen.getByText("این سازمان وجود ندارد یا شما عضو آن نیستید.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "بازگشت به نمای کلی" })).toHaveAttribute("href", "/");
  });
});

describe("organization lost during use", { timeout: 60_000 }, () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows no access when a page read answers organization_not_found, and re-checks the guard", async () => {
    const get = mockOrgReads({ members: apiError(404, "organization_not_found") });
    renderOrg(orgUrl("/members"));
    await expectNoAccess();
    expect(screen.queryByText("The members could not be loaded.")).not.toBeInTheDocument();
    // The org queries were invalidated, so the guard read again.
    await waitFor(
      () => expect(requestedUrls(get).filter((url) => url === `/organizations/${ORG_ID}`).length).toBeGreaterThan(1),
      LOAD,
    );
    // The re-check succeeded, so the page tried once more; its second
    // refusal is final. The requests stop: no endless re-checking.
    const counts = () => [
      requestedUrls(get).filter((url) => url === `/organizations/${ORG_ID}`).length,
      pageReads(get).filter((url) => url.endsWith("/members")).length,
    ];
    await waitFor(() => expect(counts()[1]).toBeGreaterThan(1), LOAD);
    let settled = counts();
    await waitFor(async () => {
      const before = counts();
      await new Promise((resolve) => setTimeout(resolve, 300));
      settled = counts();
      expect(settled).toEqual(before);
    }, LOAD);
    expect(settled[0]).toBeLessThanOrEqual(3);
    expect(settled[1]).toBeLessThanOrEqual(4);
    expect(screen.getByRole("heading", { level: 1, name: "No access" })).toBeInTheDocument();
  });

  it("shows the page again when the re-check succeeds and the page read then works", async () => {
    let membersCalls = 0;
    const reads = mockOrgReads();
    const original = reads.getMockImplementation();
    reads.mockImplementation((url: string, ...rest: unknown[]) => {
      if (url === `/organizations/${ORG_ID}/members`) {
        membersCalls += 1;
        // Only the first read is refused, as when a membership was renewed meanwhile.
        if (membersCalls === 1) return Promise.reject(apiError(404, "organization_not_found"));
        return Promise.resolve(okResponse({ items: [OWNER_ROW, ALI], total: 2 }));
      }
      return original!(url, ...(rest as []));
    });
    renderOrg(orgUrl("/members"));
    expect(await screen.findByRole("grid", { name: "Organization members" }, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit Ali" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1, name: "No access" })).not.toBeInTheDocument();
    // The refused read, then the read after the successful re-check.
    expect(membersCalls).toBeGreaterThanOrEqual(2);
  });

  it("shows no access when a write answers organization_not_found, instead of an error or a toast", async () => {
    // The membership ended: the write and the guard's re-check both say so.
    mockOrgReads({ members: [OWNER_ROW, ALI], detailLater: apiError(404, "organization_not_found") });
    vi.spyOn(api, "delete").mockRejectedValue(apiError(404, "organization_not_found"));
    renderOrg(orgUrl("/members"));
    fireEvent.click(await screen.findByRole("button", { name: "Revoke Ali" }, LOAD));
    const dialog = await screen.findByRole("alertdialog", undefined, LOAD);
    fireEvent.click(within(dialog).getByRole("button", { name: "Revoke" }));
    await expectNoAccess();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByText("Membership revoked.")).not.toBeInTheDocument();
  });

  it("keeps another 404 code as the mapped error inside the dialog", async () => {
    mockOrgReads({ members: [OWNER_ROW, ALI] });
    vi.spyOn(api, "patch").mockRejectedValue(apiError(404, "organization_member_not_found"));
    renderOrg(orgUrl("/members"));
    fireEvent.click(await screen.findByRole("button", { name: "Edit Ali" }, LOAD));
    const dialog = await screen.findByRole("dialog", undefined, LOAD);
    await typeDay(setupUser(), dialog, "Valid until", "2027-01-01");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    expect(await within(dialog).findByRole("alert", undefined, LOAD)).toHaveTextContent(
      "This person is no longer an organization member.",
    );
    expect(screen.queryByRole("heading", { level: 1, name: "No access" })).not.toBeInTheDocument();
  });

  it("shows no access when a scope grant revoke answers organization_not_found", async () => {
    mockOrgReads({ members: [OWNER_ROW, ALI, SARA], grants: [grant()], detailLater: apiError(404, "organization_not_found") });
    vi.spyOn(api, "delete").mockRejectedValue(apiError(404, "organization_not_found"));
    renderOrg(orgUrl("/scope-grants"));
    fireEvent.click(await screen.findByRole("button", { name: "Revoke grant for Ali" }, LOAD));
    const dialog = await screen.findByRole("alertdialog", undefined, LOAD);
    fireEvent.click(within(dialog).getByRole("button", { name: "Revoke" }));
    await expectNoAccess();
  });
});
