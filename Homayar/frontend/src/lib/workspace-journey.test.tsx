import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router";
import api, { WORKSPACE_DENIED_EVENT, type WorkspaceDeniedDetail } from "@/lib/api";
import AuthProvider from "@/lib/AuthProvider";
import { useAuth, type AuthContextValue } from "@/lib/auth-context";
import { I18nProvider } from "@/lib/i18n";
import { resolveWorkspaceSelection } from "@/lib/workspace-selection";
import AppLayout from "@/components/layout/AppLayout";
import { FIXTURE_USER, FIXTURE_WORKSPACE, okResponse, renderConsole, testQueryClient } from "@/test/fixtures";
import type { AuthWorkspace } from "@/types/auth";

const A: AuthWorkspace = { id: "ws-a", organization_id: "org-a", name: "Alpha", role: "owner" };
const B: AuthWorkspace = { id: "ws-b", organization_id: "org-b", name: "Beta", role: "viewer" };

describe("resolveWorkspaceSelection", () => {
  it("selects a single workspace and asks when there are several", () => {
    expect(resolveWorkspaceSelection([A], null, new Set())).toEqual({ selected: A, lost: false });
    expect(resolveWorkspaceSelection([A, B], null, new Set())).toEqual({ selected: null, lost: false });
    expect(resolveWorkspaceSelection([], null, new Set())).toEqual({ selected: null, lost: false });
  });

  it("keeps a remembered workspace and reports one that disappeared instead of switching", () => {
    expect(resolveWorkspaceSelection([A, B], "ws-b", new Set())).toEqual({ selected: B, lost: false });
    expect(resolveWorkspaceSelection([B], "ws-a", new Set())).toEqual({ selected: null, lost: true });
    expect(resolveWorkspaceSelection([A, B], "ws-a", new Set(["ws-a"]))).toEqual({ selected: null, lost: true });
  });
});

function session(workspaces: AuthWorkspace[]) {
  const user = { ...FIXTURE_USER, role: undefined };
  return okResponse({ user, workspaces, access_expires_in: 900, requires_verification: false });
}

function Probe() {
  const { workspace, workspaceNotice } = useAuth();
  return (
    <p>
      {`workspace=${workspace?.id ?? "none"} role=${workspace?.role ?? "none"} notice=${workspaceNotice?.workspaceName ?? "none"}`}
    </p>
  );
}

function renderProvider() {
  const queryClient = testQueryClient();
  queryClient.setQueryData(["platform", "meetings", "m-1", "transcript"], { secret: "protected" });
  render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </I18nProvider>
    </QueryClientProvider>,
  );
  return queryClient;
}

function deny(detail: WorkspaceDeniedDetail) {
  act(() => {
    window.dispatchEvent(new CustomEvent(WORKSPACE_DENIED_EVENT, { detail }));
  });
}

describe("workspace access during a session", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("clears content and asks for a choice when the active membership is removed", async () => {
    window.localStorage.setItem("homayar_workspace_id", "ws-a");
    const get = vi.spyOn(api, "get").mockResolvedValueOnce(session([A, B])).mockResolvedValueOnce(session([B]));
    const queryClient = renderProvider();
    expect(await screen.findByText("workspace=ws-a role=owner notice=none")).toBeInTheDocument();

    deny({ code: "workspace_access_denied", workspaceId: "ws-a" });

    expect(await screen.findByText("workspace=none role=none notice=Alpha")).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
    expect(queryClient.getQueryData(["platform", "meetings", "m-1", "transcript"])).toBeUndefined();
    expect(window.localStorage.getItem("homayar_workspace_id")).toBeNull();
  });

  it("treats a still-listed but refused workspace as unavailable (expired membership)", async () => {
    window.localStorage.setItem("homayar_workspace_id", "ws-a");
    vi.spyOn(api, "get").mockResolvedValue(session([A, B]));
    renderProvider();
    expect(await screen.findByText("workspace=ws-a role=owner notice=none")).toBeInTheDocument();

    deny({ code: "workspace_access_denied", workspaceId: "ws-a" });
    expect(await screen.findByText("workspace=none role=none notice=Alpha")).toBeInTheDocument();
  });

  it("refreshes the role after a permission refusal without leaving the workspace", async () => {
    window.localStorage.setItem("homayar_workspace_id", "ws-a");
    vi.spyOn(api, "get")
      .mockResolvedValueOnce(session([A]))
      .mockResolvedValueOnce(session([{ ...A, role: "viewer" }]));
    renderProvider();
    expect(await screen.findByText("workspace=ws-a role=owner notice=none")).toBeInTheDocument();

    deny({ code: "permission_denied", workspaceId: "ws-a" });
    expect(await screen.findByText("workspace=ws-a role=viewer notice=none")).toBeInTheDocument();
  });
});

describe("workspace gate", () => {
  function renderLayout(auth: Partial<AuthContextValue>, route = "/") {
    return renderConsole(
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<div>Overview content</div>} />
          <Route path="invitations/accept" element={<div>Invitation content</div>} />
        </Route>
      </Routes>,
      { auth: { status: "authenticated", user: FIXTURE_USER, ...auth }, route, lang: "en" },
    );
  }

  it("asks a member of several workspaces to choose and never renders workspace pages first", async () => {
    const user = userEvent.setup();
    const { context } = renderLayout({ workspaces: [A, B], workspace: null });
    expect(screen.queryByText("Overview content")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Choose a workspace" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: /Beta/ }));
    expect(context.setWorkspace).toHaveBeenCalledWith("ws-b");
  });

  it("explains a lost workspace and hides the refused one", () => {
    renderLayout({
      workspaces: [A, B],
      workspace: null,
      unavailableWorkspaceIds: ["ws-a"],
      workspaceNotice: { kind: "access_lost", workspaceName: "Alpha" },
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Your access to “Alpha” was removed, expired or suspended.");
    expect(screen.queryByRole("button", { name: /Alpha/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Beta/ })).toBeInTheDocument();
  });

  it("shows an honest empty state with no workspace, but still allows invitation acceptance", () => {
    const { unmount } = renderLayout({ workspaces: [], workspace: null });
    expect(screen.getByRole("heading", { name: "No workspace available" })).toBeInTheDocument();
    unmount();
    renderLayout({ workspaces: [], workspace: null }, "/invitations/accept?token=x");
    expect(screen.getByText("Invitation content")).toBeInTheDocument();
  });

  it("renders the selected workspace normally", () => {
    renderLayout({ workspaces: [FIXTURE_WORKSPACE], workspace: FIXTURE_WORKSPACE });
    expect(screen.getByText("Overview content")).toBeInTheDocument();
  });
});

describe("verification notice", () => {
  it("tells an unverified account that uploads are blocked", async () => {
    renderConsole(
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<div>Overview content</div>} />
        </Route>
      </Routes>,
      {
        auth: {
          status: "authenticated",
          user: { ...FIXTURE_USER, email_verified: false },
          workspaces: [FIXTURE_WORKSPACE],
          workspace: FIXTURE_WORKSPACE,
        },
      },
    );
    await waitFor(() => expect(screen.getByText(/ایمیل شما هنوز تأیید نشده است/)).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "وارد کردن کد تأیید" })).toBeInTheDocument();
  });
});
