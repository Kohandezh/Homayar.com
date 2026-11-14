import { Suspense, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router";
import api, { MFA_REQUIRED_EVENT } from "@/lib/api";
import { AuthContext, useAuth } from "@/lib/auth-context";
import { ADMIN_PAGES } from "@/components/admin/admin-pages";
import { ADMIN_NAV } from "@/lib/navigation";
import { AppRoutes } from "@/AppRoutes";
import { FIXTURE_OPERATOR, FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { AuthUser, AuthWorkspace, WorkspaceRole } from "@/types/auth";

const T = "2026-09-24T12:00:00Z";

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{`${location.pathname}${location.search}${location.hash}`}</output>;
}

function workspace(role: WorkspaceRole, permissions?: string[]): AuthWorkspace {
  return { ...FIXTURE_WORKSPACE, role, ...(permissions ? { permissions } : {}) };
}

/** Page data never arrives, so a page shows its header and stays loading. */
function pendingReads() {
  return vi.spyOn(api, "get").mockImplementation((url: string) => {
    // The MFA gate must answer before the shell renders (HOM-241): an
    // unanswered status read would block every existing case below.
    if (url === "/auth/mfa") {
      return Promise.resolve(
        okResponse({ totp_enrolled: false, recovery_codes_remaining: 0, session_verified: false, required: false }),
      );
    }
    return new Promise(() => undefined);
  });
}

function requestedUrls(get: ReturnType<typeof pendingReads>) {
  return get.mock.calls.map(([url]) => url);
}

function renderApp(route: string, selected: AuthWorkspace = workspace("owner"), lang: "fa" | "en" = "en") {
  // renderConsole only stores "en"; an earlier English render in the same test must not leak.
  window.localStorage.removeItem("homayar_lang");
  return renderConsole(
    <Suspense fallback={<p>Loading page</p>}>
      <AppRoutes />
      <LocationProbe />
    </Suspense>,
    {
      route,
      lang,
      auth: { status: "authenticated", user: FIXTURE_USER, workspace: selected, workspaces: [selected] },
    },
  );
}

function location() {
  return screen.getByTestId("location").textContent;
}

describe("admin route guard", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("opens the settings page for owners and administrators without a permissions field", async () => {
    for (const role of ["owner", "admin"] as const) {
      const get = pendingReads();
      const view = renderApp("/admin/settings", workspace(role));
      expect(await screen.findByRole("heading", { level: 1, name: "Workspace settings" })).toBeInTheDocument();
      expect(requestedUrls(get)).toContain("/admin/workspace-settings");
      expect(location()).toBe("/admin/settings");
      view.unmount();
      vi.restoreAllMocks();
    }
  });

  it("shows the shared no-access page in the layout for viewers and members, on the same URL, with no page request", async () => {
    for (const role of ["viewer", "member"] as const) {
      const get = pendingReads();
      const view = renderApp("/admin/settings", workspace(role));
      expect(await screen.findByRole("heading", { level: 1, name: "No access" })).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Workspace settings" })).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Back to overview" })).toHaveAttribute("href", "/");
      // The layout is still there: navigation and header.
      expect(screen.getByRole("navigation", { name: "Main navigation" })).toBeInTheDocument();
      expect(screen.getByRole("searchbox", { name: "Search meetings" })).toBeInTheDocument();
      expect(location()).toBe("/admin/settings");
      expect(requestedUrls(get)).not.toContain("/admin/workspace-settings");
      expect(requestedUrls(get)).not.toContain("/admin/audit-events");
      view.unmount();
      vi.restoreAllMocks();
    }
  });

  it("uses a present permissions array as the only source", async () => {
    pendingReads();
    const owner = renderApp("/admin/settings", workspace("owner", ["meeting:read", "member:read"]));
    expect(await screen.findByRole("heading", { level: 1, name: "No access" })).toBeInTheDocument();
    owner.unmount();

    renderApp("/admin/settings", workspace("viewer", ["workspace:manage"]));
    expect(await screen.findByRole("heading", { level: 1, name: "Workspace settings" })).toBeInTheDocument();
  });

  it("grants nothing for an empty permissions array, not even the member list", async () => {
    const get = pendingReads();
    renderApp("/admin/members", workspace("owner", []));
    expect(await screen.findByRole("heading", { level: 1, name: "No access" })).toBeInTheDocument();
    expect(requestedUrls(get)).not.toContain("/members");
  });

  it("ignores unknown permission strings without crashing, and keeps the known ones", async () => {
    pendingReads();
    const unknownOnly = renderApp("/admin/settings", workspace("viewer", ["admin:*"]));
    expect(await screen.findByRole("heading", { level: 1, name: "No access" })).toBeInTheDocument();
    unknownOnly.unmount();

    renderApp("/admin/settings", workspace("viewer", ["admin:*", "workspace:manage"]));
    expect(await screen.findByRole("heading", { level: 1, name: "Workspace settings" })).toBeInTheDocument();
  });

  it("never reads a role from the user object", async () => {
    pendingReads();
    const userWithRole = { ...FIXTURE_USER, role: "owner" } as typeof FIXTURE_USER;
    const viewer = workspace("viewer");
    renderConsole(
      <Suspense fallback={<p>Loading page</p>}>
        <AppRoutes />
      </Suspense>,
      { route: "/admin/settings", lang: "en", auth: { status: "authenticated", user: userWithRole, workspace: viewer, workspaces: [viewer] } },
    );
    expect(await screen.findByRole("heading", { level: 1, name: "No access" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Settings" })).not.toBeInTheDocument();
  });

  it("keeps Privacy for owners only", async () => {
    pendingReads();
    const admin = renderApp("/admin/privacy", workspace("admin"));
    expect(await screen.findByRole("heading", { level: 1, name: "No access" })).toBeInTheDocument();
    admin.unmount();

    renderApp("/admin/privacy", workspace("owner"));
    expect(await screen.findByRole("heading", { level: 1, name: "Privacy" })).toBeInTheDocument();
  });

  it("opens the API keys page for owners and administrators, who then request the key list", async () => {
    for (const role of ["owner", "admin"] as const) {
      const get = pendingReads();
      const view = renderApp("/admin/api-keys", workspace(role));
      expect(await screen.findByRole("heading", { level: 1, name: "API keys" })).toBeInTheDocument();
      expect(requestedUrls(get).some((url) => url.startsWith("/api-keys"))).toBe(true);
      view.unmount();
      vi.restoreAllMocks();
    }
  });

  it("blocks the API keys page for members and viewers, and an owner whose permissions lack api_key:manage, with no request", async () => {
    for (const selected of [workspace("member"), workspace("viewer"), workspace("owner", ["meeting:read", "member:read"])]) {
      const get = pendingReads();
      const view = renderApp("/admin/api-keys", selected);
      expect(await screen.findByRole("heading", { level: 1, name: "No access" })).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "API keys" })).not.toBeInTheDocument();
      expect(requestedUrls(get).filter((url) => url.startsWith("/api-keys"))).toEqual([]);
      view.unmount();
      vi.restoreAllMocks();
    }
  });

  it("says no access in Persian", async () => {
    pendingReads();
    renderApp("/admin/settings", workspace("viewer"), "fa");
    expect(await screen.findByRole("heading", { level: 1, name: "دسترسی ندارید" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "بازگشت به نمای کلی" })).toHaveAttribute("href", "/");
  });
});

describe("review queue routes", () => {
  const LOAD = { timeout: 5000 };

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each(["/reviews", "/reviews/rev-1"])(
    "shows a viewer the shared no-access page on %s, with no review-items request and no sidebar entry",
    async (route) => {
      const get = pendingReads();
      const view = renderApp(route, workspace("viewer"));
      expect(await screen.findByRole("heading", { level: 1, name: "No access" }, LOAD)).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Review queue" })).not.toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "Review queue" })).not.toBeInTheDocument();
      expect(location()).toBe(route);
      expect(requestedUrls(get).filter((url) => url.startsWith("/review-items"))).toEqual([]);
      view.unmount();
    },
  );

  it("opens the queue page for a member, who also sees the sidebar entry", async () => {
    const get = pendingReads();
    renderApp("/reviews", workspace("member"));
    expect(await screen.findByRole("heading", { level: 1, name: "Review queue" }, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review queue" })).toHaveAttribute("href", "/reviews");
    expect(requestedUrls(get).some((url) => url.startsWith("/review-items"))).toBe(true);
  });

  it("follows a present permissions array for the guard", async () => {
    pendingReads();
    const withoutReview = renderApp("/reviews", workspace("owner", ["meeting:read"]));
    expect(await screen.findByRole("heading", { level: 1, name: "No access" }, LOAD)).toBeInTheDocument();
    withoutReview.unmount();

    pendingReads();
    renderApp("/reviews", workspace("viewer", ["review:read"]));
    expect(await screen.findByRole("heading", { level: 1, name: "Review queue" }, LOAD)).toBeInTheDocument();
  });
});

describe("voice operations route (operator only)", () => {
  // The lazy page can take more than 1 s to load on a loaded machine, and
  // each case renders the routes up to three times.
  const LOAD = { timeout: 5000 };
  const SLOW = 20_000;

  afterEach(() => {
    vi.restoreAllMocks();
  });

  /** A user object as the server could send it, including values the type does not allow. */
  function withSystemRole(systemRole: unknown): AuthUser {
    return { ...FIXTURE_USER, system_role: systemRole } as AuthUser;
  }

  function renderVoice(user: AuthUser, selected: AuthWorkspace = workspace("admin"), lang: "fa" | "en" = "en") {
    window.localStorage.removeItem("homayar_lang");
    return renderConsole(
      <Suspense fallback={<p>Loading page</p>}>
        <AppRoutes />
        <LocationProbe />
      </Suspense>,
      { route: "/admin/voice", lang, auth: { status: "authenticated", user, workspace: selected, workspaces: [selected] } },
    );
  }

  async function expectOpens(user: AuthUser, selected: AuthWorkspace) {
    const get = pendingReads();
    const view = renderVoice(user, selected);
    expect(await screen.findByRole("heading", { level: 1, name: "Voice operations" }, LOAD)).toBeInTheDocument();
    expect(requestedUrls(get)).toContain("/voice/capacity");
    expect(screen.getByRole("link", { name: "Voice operations" })).toBeInTheDocument();
    view.unmount();
    vi.restoreAllMocks();
  }

  async function expectBlocked(user: AuthUser, selected: AuthWorkspace) {
    const get = pendingReads();
    const view = renderVoice(user, selected);
    expect(await screen.findByRole("heading", { level: 1, name: "No access" }, LOAD)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Voice operations" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Voice operations" })).not.toBeInTheDocument();
    expect(location()).toBe("/admin/voice");
    expect(requestedUrls(get)).not.toContain("/voice/capacity");
    view.unmount();
    vi.restoreAllMocks();
  }

  it("mounts the page for an admin who is a platform operator", async () => {
    await expectOpens(FIXTURE_OPERATOR, workspace("admin"));
    await expectOpens(FIXTURE_OPERATOR, workspace("owner"));
  }, SLOW);

  // Each deny case is the allow case above with one field changed.
  it.each([
    ["user", withSystemRole("user")],
    ["missing", FIXTURE_USER],
    ["Operator (other case)", withSystemRole("Operator")],
  ])("shows no access, with no capacity request, to an admin whose system_role is %s", async (_label, user) => {
    await expectOpens(FIXTURE_OPERATOR, workspace("admin"));
    await expectBlocked(user, workspace("admin"));
  }, SLOW);

  it("shows no access to an operator whose workspace role lacks workspace:manage", async () => {
    await expectOpens(FIXTURE_OPERATOR, workspace("admin"));
    await expectBlocked(FIXTURE_OPERATOR, workspace("member"));
    await expectBlocked(FIXTURE_OPERATOR, workspace("owner", ["meeting:read", "member:read"]));
  }, SLOW);

  it("names the page in Persian for an operator", async () => {
    pendingReads();
    renderVoice(FIXTURE_OPERATOR, workspace("admin"), "fa");
    expect(await screen.findByRole("heading", { level: 1, name: "عملیات صوتی" }, LOAD)).toBeInTheDocument();
  }, SLOW);
});

describe("admin pages", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("puts the existing members, usage and settings pages behind their routes", async () => {
    pendingReads();
    const members = renderApp("/admin/members");
    expect(await screen.findByRole("heading", { level: 1, name: "Members and access" })).toBeInTheDocument();
    members.unmount();
    const usage = renderApp("/admin/usage");
    expect(await screen.findByRole("heading", { level: 1, name: "Usage and subscription" })).toBeInTheDocument();
    usage.unmount();
    const settings = renderApp("/admin/settings");
    expect(await screen.findByRole("heading", { level: 1, name: "Workspace settings" })).toBeInTheDocument();
    settings.unmount();
    renderApp("/admin/audit");
    expect(await screen.findByRole("heading", { level: 1, name: "Audit log" })).toBeInTheDocument();
  });

  it("asks for billing data on the usage page only for a role with billing:read", async () => {
    const billingUrls = ["/billing/subscription", "/billing/usage", "/billing/usage/events", "/billing/plans"];
    const get = pendingReads();
    const owner = renderApp("/admin/usage", workspace("owner"));
    expect(await screen.findByRole("heading", { level: 1, name: "Usage and subscription" })).toBeInTheDocument();
    for (const url of billingUrls) expect(requestedUrls(get)).toContain(url);
    owner.unmount();
    vi.restoreAllMocks();

    const memberGet = pendingReads();
    renderApp("/admin/usage", workspace("member"));
    expect(await screen.findByRole("heading", { level: 1, name: "No access" })).toBeInTheDocument();
    for (const url of billingUrls) expect(requestedUrls(memberGet)).not.toContain(url);
  });

  it("renders the teams page behind its route, not the coming-soon placeholder", async () => {
    pendingReads();
    renderApp("/admin/teams");
    expect(await screen.findByRole("heading", { level: 1, name: "Teams" })).toBeInTheDocument();
    expect(screen.queryByText("Coming soon")).not.toBeInTheDocument();
  });

  it("shows the shared coming-soon page for every admin path without a page, in both languages", async () => {
    pendingReads();
    const placeholders = ADMIN_NAV.filter((entry) => !(entry.path in ADMIN_PAGES));
    for (const entry of placeholders) {
      for (const lang of ["en", "fa"] as const) {
        const view = renderApp(entry.path, workspace("owner"), lang);
        const main = await screen.findByRole("main");
        expect(await within(main).findByRole("heading", { level: 1, name: entry.label[lang] })).toBeInTheDocument();
        expect(within(main).getByText(lang === "fa" ? "به‌زودی" : "Coming soon")).toBeInTheDocument();
        view.unmount();
      }
    }
  });

  it("lets a viewer read the member list without any management control", async () => {
    const get = vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/members") {
        return okResponse({
          items: [
            { id: "mem-1", user_id: FIXTURE_USER.id, email: FIXTURE_USER.email, phone_e164: null, display_name: "Person", role: "viewer", joined_at: T },
            { id: "mem-2", user_id: "user-2", email: "ali@example.com", phone_e164: null, display_name: "Ali", role: "member", joined_at: T },
          ],
          total: 2,
        });
      }
      throw apiError(404, "not_found");
    });
    renderApp("/admin/members", workspace("viewer"));
    expect(await screen.findByText("Ali")).toBeInTheDocument();
    expect(screen.queryByLabelText("Role for Ali")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove Ali" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Invite member" })).not.toBeInTheDocument();
    expect(requestedUrls(get)).not.toContain("/members/invitations");
  });

  it("keeps member management for a workspace with member:manage", async () => {
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/members") {
        return okResponse({
          items: [
            { id: "mem-1", user_id: FIXTURE_USER.id, email: FIXTURE_USER.email, phone_e164: null, display_name: "Person", role: "admin", joined_at: T },
            { id: "mem-2", user_id: "user-2", email: "ali@example.com", phone_e164: null, display_name: "Ali", role: "member", joined_at: T },
          ],
          total: 2,
        });
      }
      if (url === "/members/invitations") return okResponse({ items: [], total: 0 });
      throw apiError(404, "not_found");
    });
    renderApp("/admin/members", workspace("admin"));
    expect(await screen.findByLabelText("Role for Ali")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove Ali" })).toBeInTheDocument();
    // The invite form lives in a modal, so only its trigger is on the page.
    expect(screen.getByRole("button", { name: "Invite member" })).toBeInTheDocument();
  });

  it("hides management for an owner role whose permissions lack member:manage", async () => {
    const get = vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/members") {
        return okResponse({
          items: [{ id: "mem-2", user_id: "user-2", email: "ali@example.com", phone_e164: null, display_name: "Ali", role: "member", joined_at: T }],
          total: 1,
        });
      }
      throw apiError(404, "not_found");
    });
    renderApp("/admin/members", workspace("owner", ["member:read"]));
    expect(await screen.findByText("Ali")).toBeInTheDocument();
    expect(screen.queryByLabelText("Role for Ali")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove Ali" })).not.toBeInTheDocument();
    expect(requestedUrls(get)).not.toContain("/members/invitations");
  });
});

describe("old admin paths", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each([
    ["/members?tab=x#y", "/admin/members?tab=x#y"],
    ["/usage?period=month#events", "/admin/usage?period=month#events"],
    ["/settings?tab=privacy#pii", "/admin/settings?tab=privacy#pii"],
  ])("redirects %s to %s, keeping the query and the hash", async (from, to) => {
    pendingReads();
    renderApp(from);
    await waitFor(() => expect(location()).toBe(to));
  });

  it("sends a viewer from /settings through the guard to the no-access page", async () => {
    pendingReads();
    renderApp("/settings", workspace("viewer"));
    expect(await screen.findByRole("heading", { level: 1, name: "No access" })).toBeInTheDocument();
    expect(location()).toBe("/admin/settings");
  });

  it("keeps unknown paths falling back to the overview", async () => {
    pendingReads();
    renderApp("/admin/unknown");
    await waitFor(() => expect(location()).toBe("/"));
  });
});

describe("MFA routes, proactive gate and reactive redirect (HOM-241)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  /** GET /auth/mfa answered (or failed); every other read stays pending. */
  function mfaReads(answer: unknown, options: { fail?: boolean } = {}) {
    return vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/auth/mfa") {
        if (options.fail) throw apiError(404, "not_found");
        return okResponse(answer);
      }
      return new Promise(() => undefined);
    });
  }

  const UNENROLLED = { totp_enrolled: false, recovery_codes_remaining: 0, session_verified: false, required: true };
  const ENROLLED = { totp_enrolled: true, recovery_codes_remaining: 4, session_verified: false, required: true };
  const FREE = { totp_enrolled: false, recovery_codes_remaining: 0, session_verified: false, required: false };

  it.each([
    ["mfa/setup", "Set up two-step verification"],
    ["mfa/verify", "Verify it's you"],
  ])("sends an anonymous visitor from /%s to the login page, as for other pages (AC1)", async (path, heading) => {
    const get = mfaReads(FREE);
    renderConsole(
      <Suspense fallback={<p>Loading page</p>}>
        <AppRoutes />
        <LocationProbe />
      </Suspense>,
      { route: `/${path}`, lang: "en", auth: { status: "anonymous", user: null } },
    );

    await waitFor(() => expect(location()).toBe("/login"));
    expect(screen.queryByRole("heading", { name: heading })).not.toBeInTheDocument();
    // The MFA page never mounted, so no MFA status read happened.
    expect(requestedUrls(get)).not.toContain("/auth/mfa");
  });

  it("sends a privileged unenrolled session to the setup page with the location as next (AC2)", async () => {
    const get = mfaReads(UNENROLLED);
    renderApp("/admin/teams");

    expect(await screen.findByRole("heading", { level: 1, name: "Set up two-step verification" })).toBeInTheDocument();
    expect(location()).toBe("/mfa/setup?next=%2Fadmin%2Fteams");
    // The shell never rendered, so nothing else was requested.
    expect(requestedUrls(get)).toEqual(["/auth/mfa"]);
  });

  it("sends a privileged enrolled session to the verify page (AC2)", async () => {
    mfaReads(ENROLLED);
    renderApp("/admin/teams");

    expect(await screen.findByRole("heading", { level: 1, name: "Verify it's you" })).toBeInTheDocument();
    expect(location()).toBe("/mfa/verify?next=%2Fadmin%2Fteams");
  });

  it("keeps the search and the hash of the current location in next (AC2)", async () => {
    mfaReads(UNENROLLED);
    renderApp("/admin/teams?tab=x#top");
    await waitFor(() => expect(location()).toBe(`/mfa/setup?next=${encodeURIComponent("/admin/teams?tab=x#top")}`));
  });

  it("renders no app shell while the check is pending, so no workspace request fires (AC2)", async () => {
    // Its own mock: pendingReads answers /auth/mfa, and this case needs that
    // read pending too (the review's item 8).
    const get = vi.spyOn(api, "get").mockImplementation(() => new Promise(() => undefined));
    renderApp("/admin/teams");

    expect(await screen.findByText("Checking two-step verification")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main navigation" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Teams" })).not.toBeInTheDocument();
    expect(requestedUrls(get)).toEqual(["/auth/mfa"]);
  });

  it.each([
    ["not required", FREE],
    ["already verified", { totp_enrolled: true, recovery_codes_remaining: 4, session_verified: true, required: true }],
  ])("renders the app as before when the status says %s (AC2 allow-control)", async (_label, answer) => {
    mfaReads(answer);
    renderApp("/admin/teams");
    expect(await screen.findByRole("heading", { level: 1, name: "Teams" })).toBeInTheDocument();
  });

  it("renders the app as before when GET /auth/mfa fails, with no error screen (AC2 allow-control)", async () => {
    mfaReads(null, { fail: true });
    renderApp("/admin/teams");
    expect(await screen.findByRole("heading", { level: 1, name: "Teams" })).toBeInTheDocument();
  });

  it("does not run the check on the MFA pages themselves (AC2)", async () => {
    const get = pendingReads();
    renderApp("/mfa/setup");

    expect(await screen.findByRole("heading", { level: 1, name: "Set up two-step verification" })).toBeInTheDocument();
    expect(requestedUrls(get)).toEqual([]);
  });

  it("moves to the verify page when a request answers 403 mfa_required (AC3)", async () => {
    mfaReads({ ...ENROLLED, session_verified: true, required: false });
    renderApp("/admin/teams");
    expect(await screen.findByRole("heading", { level: 1, name: "Teams" })).toBeInTheDocument();

    window.dispatchEvent(new CustomEvent(MFA_REQUIRED_EVENT, { detail: { code: "mfa_required" } }));

    expect(await screen.findByRole("heading", { level: 1, name: "Verify it's you" })).toBeInTheDocument();
    expect(location()).toBe("/mfa/verify?next=%2Fadmin%2Fteams");
  });

  it("moves to the setup page on mfa_enrollment_required (AC3)", async () => {
    mfaReads(FREE);
    renderApp("/admin/teams");
    expect(await screen.findByRole("heading", { level: 1, name: "Teams" })).toBeInTheDocument();

    window.dispatchEvent(new CustomEvent(MFA_REQUIRED_EVENT, { detail: { code: "mfa_enrollment_required" } }));

    expect(await screen.findByRole("heading", { level: 1, name: "Set up two-step verification" })).toBeInTheDocument();
    expect(location()).toBe("/mfa/setup?next=%2Fadmin%2Fteams");
  });

  it("does not navigate again on an MFA page (AC3, no loop)", async () => {
    pendingReads();
    renderApp("/mfa/verify");
    expect(await screen.findByRole("heading", { level: 1, name: "Verify it's you" })).toBeInTheDocument();

    window.dispatchEvent(new CustomEvent(MFA_REQUIRED_EVENT, { detail: { code: "mfa_required" } }));
    expect(screen.getByRole("heading", { level: 1, name: "Verify it's you" })).toBeInTheDocument();
    expect(location()).toBe("/mfa/verify");
  });

  it.each([
    ["upper case", "/MFA/verify"],
    ["percent escapes", "/%6Dfa/verify"],
  ])("does not navigate again on an MFA page spelled with %s either (AC3)", async (_label, route) => {
    // The router matches without case and decodes escapes, so these mount the
    // verify page; the listener must recognize them as MFA pages too.
    pendingReads();
    renderApp(route);
    expect(await screen.findByRole("heading", { level: 1, name: "Verify it's you" })).toBeInTheDocument();

    window.dispatchEvent(new CustomEvent(MFA_REQUIRED_EVENT, { detail: { code: "mfa_required" } }));
    expect(screen.getByRole("heading", { level: 1, name: "Verify it's you" })).toBeInTheDocument();
    expect(location()).toBe(route);
  });

  it("lets a verified user back even when the status re-read fails after verify (turn 2 item 3)", async () => {
    let statusReads = 0;
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/auth/mfa") {
        statusReads += 1;
        // The first read redirects to /mfa/verify; the re-read after the
        // successful verify fails.
        if (statusReads === 1) return okResponse(ENROLLED);
        throw apiError(503, "mfa_unavailable");
      }
      return new Promise(() => undefined);
    });
    vi.spyOn(api, "post").mockImplementation(async (url: string) => {
      if (url === "/auth/mfa/verify") return okResponse({ verified: true, method: "totp", recovery_codes_remaining: 9 });
      throw apiError(500, "unexpected_post");
    });
    const user = userEvent.setup();
    renderApp("/admin");

    expect(await screen.findByRole("heading", { level: 1, name: "Verify it's you" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("Six-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    // The optimistic verified cache lets the gate wave the user through.
    await waitFor(() => expect(location()).toBe("/admin"));
    expect(screen.queryByRole("heading", { name: "Verify it's you" })).not.toBeInTheDocument();
    expect(statusReads).toBeGreaterThanOrEqual(2);
  });

  it("after a workspace switch: the shell stays and the next navigation re-reads and still redirects", async () => {
    const answers = [
      FREE,
      { totp_enrolled: true, recovery_codes_remaining: 4, session_verified: false, required: true },
    ];
    let reads = 0;
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/auth/mfa") {
        const answer = answers[Math.min(reads, answers.length - 1)];
        reads += 1;
        return okResponse(answer);
      }
      return new Promise(() => undefined);
    });
    const user = userEvent.setup();
    const view = renderApp("/account/security");
    expect(await screen.findByRole("heading", { level: 1, name: "Account security" })).toBeInTheDocument();

    // The workspace switch clears the cache while the gate stays mounted.
    act(() => view.queryClient.clear());

    // The shell stays: no "Checking two-step verification" replacement, and
    // no status read fires at clear time.
    expect(screen.getByRole("navigation", { name: "Main navigation" })).toBeInTheDocument();
    expect(screen.queryByText("Checking two-step verification")).not.toBeInTheDocument();
    expect(reads).toBe(1);

    // The next navigation re-reads the status, which now needs MFA.
    await user.click(screen.getByRole("link", { name: "Meetings" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Verify it's you" })).toBeInTheDocument();
    await waitFor(() => expect(location()).toBe(`/mfa/verify?next=${encodeURIComponent("/meetings")}`));
    expect(reads).toBe(2);
  });

  it("keeps the shell after a workspace switch when the gate first mounted with a cached status", async () => {
    // Coming back from /mfa/verify, the status is already in the cache when
    // the shell mounts, so the gate's first render is already settled. A
    // later workspace switch must still keep the shell during the re-read.
    let reads = 0;
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/auth/mfa") {
        reads += 1;
        if (reads === 1) return okResponse(ENROLLED);
        if (reads === 2) return okResponse({ ...ENROLLED, session_verified: true });
        // The re-read after the workspace switch never answers.
        return new Promise(() => undefined);
      }
      return new Promise(() => undefined);
    });
    vi.spyOn(api, "post").mockImplementation(async (url: string) => {
      if (url === "/auth/mfa/verify") return okResponse({ verified: true, method: "totp", recovery_codes_remaining: 9 });
      throw apiError(500, "unexpected_post");
    });
    const user = userEvent.setup();
    const view = renderApp("/admin");

    expect(await screen.findByRole("heading", { level: 1, name: "Verify it's you" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("Six-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => expect(location()).toBe("/admin"));
    expect(await screen.findByRole("navigation", { name: "Main navigation" })).toBeInTheDocument();
    await waitFor(() => expect(reads).toBe(2));

    // The workspace switch clears the cache; the next navigation re-reads.
    act(() => view.queryClient.clear());
    await user.click(screen.getByRole("link", { name: "Meetings" }));
    await waitFor(() => expect(reads).toBe(3));

    // The re-read is still pending: the shell stays, no blocking screen.
    expect(screen.getByRole("navigation", { name: "Main navigation" })).toBeInTheDocument();
    expect(screen.queryByText("Checking two-step verification")).not.toBeInTheDocument();
    expect(location()).toBe("/meetings");
  });

  it("sends no status read when the cache is cleared on sign-out, while the gate is still mounted", async () => {
    let reads = 0;
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/auth/mfa") {
        reads += 1;
        return okResponse(FREE);
      }
      return new Promise(() => undefined);
    });
    const view = renderApp("/account/security");
    expect(await screen.findByRole("heading", { level: 1, name: "Account security" })).toBeInTheDocument();
    expect(reads).toBe(1);

    // markAnonymous clears the query cache before the shell unmounts; the
    // gate must not fire a session-less GET /auth/mfa for it.
    act(() => view.queryClient.clear());
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(reads).toBe(1);
  });

  it.each([
    ["en", "Account security"],
    ["fa", "امنیت حساب"],
  ])("links every role to the account security page from the sidebar footer, in %s (AC7)", async (lang, label) => {
    mfaReads(FREE);
    renderApp("/", workspace("member"), lang as "fa" | "en");
    const link = await screen.findByRole("link", { name: label });
    expect(link).toHaveAttribute("href", "/account/security");
  });
});

describe("workspace switch", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  const OWNER_WS: AuthWorkspace = { id: "ws-a", organization_id: "org-a", name: "Alpha", role: "owner" };
  const VIEWER_WS: AuthWorkspace = { id: "ws-b", organization_id: "org-b", name: "Beta", role: "viewer" };

  /** A real-looking auth context whose selected workspace can change. */
  function SwitchingAuth() {
    const base = useAuth();
    const [selected, setSelected] = useState<AuthWorkspace>(OWNER_WS);
    const value = {
      ...base,
      workspace: selected,
      workspaces: [OWNER_WS, VIEWER_WS],
      setWorkspace: (id: string) => setSelected(id === VIEWER_WS.id ? VIEWER_WS : OWNER_WS),
    };
    return (
      <AuthContext.Provider value={value}>
        <Suspense fallback={<p>Loading page</p>}>
          <AppRoutes />
          <LocationProbe />
          <button type="button" onClick={() => value.setWorkspace(VIEWER_WS.id)}>
            switch to viewer
          </button>
        </Suspense>
      </AuthContext.Provider>
    );
  }

  it("changes the menu and the guard on the next render, with no reload and no extra request", async () => {
    const get = pendingReads();
    renderConsole(<SwitchingAuth />, {
      route: "/admin/settings",
      lang: "en",
      auth: { status: "authenticated", user: FIXTURE_USER },
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Workspace settings" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument();
    const requestsBefore = get.mock.calls.length;

    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "switch to viewer" }));
    });

    expect(screen.getByRole("heading", { level: 1, name: "No access" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Settings" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Members" })).toBeInTheDocument();
    expect(location()).toBe("/admin/settings");
    expect(get.mock.calls.length).toBe(requestsBefore);
  });
});

describe("navigation drawer", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each([
    ["left", "en", "Open navigation"],
    ["right", "fa", "باز کردن منو"],
  ] as const)("passes placement %s and start alignment to the drawer props in %s, and closes on navigation", async (side, lang, openLabel) => {
    // jsdom has no layout: this checks the props only. The rendered side is checked in a real browser.
    pendingReads();
    renderApp("/admin/teams", workspace("owner"), lang);
    fireEvent.click(await screen.findByRole("button", { name: openLabel }));
    const drawer = await screen.findByRole("dialog");
    expect(drawer).toHaveAttribute("data-placement", side);
    expect(drawer.closest('[data-slot="drawer-content"]')).toHaveClass("justify-start");

    const settings = lang === "fa" ? "تنظیمات" : "Settings";
    fireEvent.click(within(drawer).getByRole("link", { name: settings }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(location()).toBe("/admin/settings");
  });

  it("closes on Escape", async () => {
    pendingReads();
    renderApp("/admin/teams");
    fireEvent.click(await screen.findByRole("button", { name: "Open navigation" }));
    const drawer = await screen.findByRole("dialog");
    fireEvent.keyDown(drawer, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
