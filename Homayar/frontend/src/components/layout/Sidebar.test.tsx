import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, within } from "@testing-library/react";
import Sidebar from "@/components/layout/Sidebar";
import { ADMIN_NAV, MAIN_NAV } from "@/lib/navigation";
import { FIXTURE_OPERATOR, FIXTURE_USER, FIXTURE_WORKSPACE, apiError, renderConsole } from "@/test/fixtures";
import type { AuthContextValue } from "@/lib/auth-context";
import type { AuthUser, AuthWorkspace, WorkspaceRole } from "@/types/auth";

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
/** What a workspace owner sees without the platform operator role. */
const NON_OPERATOR_ADMIN = ADMIN_NAV.filter((entry) => !("operatorOnly" in entry && entry.operatorOnly)).map(
  (entry) => entry.label.en,
);

/** A user object as the server could send it, including values the type does not allow. */
function withSystemRole(systemRole: unknown): AuthUser {
  return { ...FIXTURE_USER, system_role: systemRole } as AuthUser;
}

describe("Sidebar navigation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows every admin entry to an owner who is a platform operator, in order, under its own group", () => {
    renderSidebar(workspace("owner"), "en", { user: FIXTURE_OPERATOR });
    expect(adminLabels()).toEqual(ALL_ADMIN);
    for (const entry of [...MAIN_NAV, ...ADMIN_NAV]) {
      expect(screen.getByRole("link", { name: entry.label.en }).getAttribute("href")).toMatch(new RegExp(`${entry.path}$`));
    }
  });

  it("shows an owner who is not an operator every admin entry except Voice operations", () => {
    renderSidebar(workspace("owner"));
    expect(NON_OPERATOR_ADMIN).not.toContain("Voice operations");
    expect(adminLabels()).toEqual(NON_OPERATOR_ADMIN);
    expect(screen.queryByRole("link", { name: "Voice operations" })).not.toBeInTheDocument();
  });

  it("shows an admin everything except Privacy and Voice operations", () => {
    renderSidebar(workspace("admin"));
    expect(adminLabels()).toEqual(NON_OPERATOR_ADMIN.filter((label) => label !== "Privacy"));
    expect(screen.queryByRole("link", { name: "Privacy" })).not.toBeInTheDocument();
  });

  it("adds Voice operations, pointing at /admin/voice, for an admin who is a platform operator", () => {
    renderSidebar(workspace("admin"), "en", { user: FIXTURE_OPERATOR });
    expect(adminLabels()).toEqual(ALL_ADMIN.filter((label) => label !== "Privacy"));
    expect(screen.getByRole("link", { name: "Voice operations" }).getAttribute("href")).toMatch(/\/admin\/voice$/);
  });

  // Each deny case changes one field of the allow case above it.
  it.each([
    ["user", withSystemRole("user")],
    ["missing", FIXTURE_USER],
    ["Operator (other case)", withSystemRole("Operator")],
  ])("hides Voice operations from an admin whose system_role is %s", (_label, user) => {
    const allowed = renderSidebar(workspace("admin"), "en", { user: FIXTURE_OPERATOR });
    expect(screen.getByRole("link", { name: "Voice operations" })).toBeInTheDocument();
    allowed.unmount();

    renderSidebar(workspace("admin"), "en", { user });
    expect(screen.queryByRole("link", { name: "Voice operations" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument();
  });

  it("hides Voice operations from an operator whose workspace role lacks workspace:manage", () => {
    const allowed = renderSidebar(workspace("admin"), "en", { user: FIXTURE_OPERATOR });
    expect(screen.getByRole("link", { name: "Voice operations" })).toBeInTheDocument();
    allowed.unmount();

    renderSidebar(workspace("member"), "en", { user: FIXTURE_OPERATOR });
    expect(adminLabels()).toEqual(["Members", "Teams"]);
    expect(screen.queryByRole("link", { name: "Voice operations" })).not.toBeInTheDocument();
  });

  it("names the operator entry in Persian", () => {
    renderSidebar(workspace("owner"), "fa", { user: FIXTURE_OPERATOR });
    const group = screen.getByRole("group", { name: "مدیریت" });
    expect(within(group).getByRole("link", { name: "عملیات صوتی" })).toBeInTheDocument();
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
