import { Suspense, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useLocation } from "react-router-dom";
import api from "@/lib/api";
import { AuthContext, useAuth } from "@/lib/auth-context";
import { ADMIN_PAGES } from "@/components/admin/admin-pages";
import { ADMIN_NAV } from "@/lib/navigation";
import { AppRoutes } from "@/AppRoutes";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { AuthWorkspace, WorkspaceRole } from "@/types/auth";

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
  return vi.spyOn(api, "get").mockImplementation(() => new Promise(() => undefined));
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
      expect(await screen.findByRole("heading", { level: 1, name: "Settings and audit" })).toBeInTheDocument();
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
      expect(screen.queryByRole("heading", { name: "Settings and audit" })).not.toBeInTheDocument();
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
    expect(await screen.findByRole("heading", { level: 1, name: "Settings and audit" })).toBeInTheDocument();
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
    expect(await screen.findByRole("heading", { level: 1, name: "Settings and audit" })).toBeInTheDocument();
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

  it("says no access in Persian", async () => {
    pendingReads();
    renderApp("/admin/settings", workspace("viewer"), "fa");
    expect(await screen.findByRole("heading", { level: 1, name: "دسترسی ندارید" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "بازگشت به نمای کلی" })).toHaveAttribute("href", "/");
  });
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
    renderApp("/admin/settings");
    expect(await screen.findByRole("heading", { level: 1, name: "Settings and audit" })).toBeInTheDocument();
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
    expect(await screen.findByRole("heading", { level: 1, name: "Settings and audit" })).toBeInTheDocument();
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
