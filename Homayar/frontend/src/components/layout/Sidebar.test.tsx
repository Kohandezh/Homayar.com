import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, within } from "@testing-library/react";
import Sidebar from "@/components/layout/Sidebar";
import { ADMIN_NAV, MAIN_NAV } from "@/lib/navigation";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, renderConsole } from "@/test/fixtures";
import type { AuthContextValue } from "@/lib/auth-context";
import type { AuthWorkspace, WorkspaceRole } from "@/types/auth";

function workspace(role: WorkspaceRole, permissions?: string[]): AuthWorkspace {
  return { ...FIXTURE_WORKSPACE, role, ...(permissions ? { permissions } : {}) };
}

function renderSidebar(selected: AuthWorkspace | null, lang: "fa" | "en" = "en", auth: Partial<AuthContextValue> = {}) {
  return renderConsole(<Sidebar />, {
    lang,
    auth: { status: "authenticated", user: FIXTURE_USER, workspace: selected, workspaces: selected ? [selected] : [], ...auth },
  });
}

function adminLabels() {
  const group = screen.queryByRole("group", { name: "Administration" });
  return group ? within(group).getAllByRole("link").map((link) => link.textContent) : [];
}

const ALL_ADMIN = ADMIN_NAV.map((entry) => entry.label.en);

describe("Sidebar navigation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows every admin entry to an owner, in order, under its own group", () => {
    renderSidebar(workspace("owner"));
    expect(adminLabels()).toEqual(ALL_ADMIN);
    for (const entry of [...MAIN_NAV, ...ADMIN_NAV]) {
      expect(screen.getByRole("link", { name: entry.label.en }).getAttribute("href")).toMatch(new RegExp(`${entry.path}$`));
    }
  });

  it("shows an admin everything except Privacy", () => {
    renderSidebar(workspace("admin"));
    expect(adminLabels()).toEqual(ALL_ADMIN.filter((label) => label !== "Privacy"));
    expect(screen.queryByRole("link", { name: "Privacy" })).not.toBeInTheDocument();
  });

  it.each(["member", "viewer"] as const)("shows a %s only Members and Teams", (role) => {
    renderSidebar(workspace(role));
    expect(adminLabels()).toEqual(["Members", "Teams"]);
    for (const hidden of ["Settings", "Usage", "Audit log", "Admin overview", "API keys"]) {
      expect(screen.queryByRole("link", { name: hidden })).not.toBeInTheDocument();
    }
    for (const entry of MAIN_NAV) expect(screen.getByRole("link", { name: entry.label.en })).toBeInTheDocument();
  });

  it("follows a present permissions array instead of the role", () => {
    const owner = renderSidebar(workspace("owner", ["meeting:read", "member:read"]));
    expect(adminLabels()).toEqual(["Members"]);
    owner.unmount();
    renderSidebar(workspace("viewer", ["workspace:manage", "admin:*"]));
    expect(adminLabels()).toEqual(["Admin overview", "Settings", "Processing", "AI policy", "Locations"]);
  });

  it("drops the Administration group when no admin entry is visible", () => {
    const empty = renderSidebar(workspace("owner", []));
    expect(screen.queryByRole("group", { name: "Administration" })).not.toBeInTheDocument();
    expect(screen.queryByText("Administration")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Overview" })).toBeInTheDocument();
    empty.unmount();
    renderSidebar(null);
    expect(screen.queryByRole("group", { name: "Administration" })).not.toBeInTheDocument();
  });

  it("labels links and the group in Persian", () => {
    renderSidebar(workspace("viewer"), "fa");
    const group = screen.getByRole("group", { name: "مدیریت" });
    expect(within(group).getAllByRole("link").map((link) => link.textContent)).toEqual(["اعضا", "تیم‌ها"]);
    expect(screen.getByRole("link", { name: "نمای کلی" })).toBeInTheDocument();
  });

  it("marks the current page", () => {
    renderConsole(<Sidebar />, {
      route: "/admin/members",
      lang: "en",
      auth: { status: "authenticated", user: FIXTURE_USER, workspace: workspace("owner"), workspaces: [workspace("owner")] },
    });
    expect(screen.getByRole("link", { name: "Members" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });

  it("signs out with a pending state and reports a failure", async () => {
    let fail: (error: unknown) => void = () => undefined;
    const signOut = vi.fn<AuthContextValue["signOut"]>(
      () =>
        new Promise((_resolve, reject) => {
          fail = reject;
        }),
    );
    renderSidebar(workspace("owner"), "en", { signOut });
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(signOut).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("button", { name: "Signing out" })).toHaveAttribute("aria-disabled", "true");
    await act(async () => {
      fail(apiError(503, "service_unavailable"));
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });
});
