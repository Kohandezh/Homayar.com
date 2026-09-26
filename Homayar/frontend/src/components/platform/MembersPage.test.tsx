import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import api from "@/lib/api";
import MembersPage from "@/components/platform/MembersPage";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { WorkspaceInvitation, WorkspaceMember } from "@/types/platform";

const T = "2026-09-24T12:00:00Z";

const OWNER: WorkspaceMember = {
  id: "mem-1",
  user_id: FIXTURE_USER.id,
  email: FIXTURE_USER.email,
  phone_e164: null,
  display_name: "Person",
  role: "owner",
  joined_at: T,
};

const ALI: WorkspaceMember = {
  id: "mem-2",
  user_id: "user-2",
  email: "ali@example.com",
  phone_e164: null,
  display_name: "Ali",
  role: "member",
  joined_at: T,
};

const PENDING: WorkspaceInvitation = {
  id: "inv-1",
  workspace_id: "ws-1",
  email: "new@example.com",
  role: "member",
  invited_by_user_id: FIXTURE_USER.id,
  status: "pending",
  expires_at: T,
  accepted_at: null,
  revoked_at: null,
  created_at: T,
};

function mockReads(members: WorkspaceMember[] = [OWNER, ALI], invitations: WorkspaceInvitation[] = [PENDING]) {
  return vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url === "/members") return okResponse({ items: members, total: members.length });
    if (url === "/members/invitations") return okResponse({ items: invitations, total: invitations.length });
    throw apiError(404, "not_found");
  });
}

function renderMembers(lang: "fa" | "en" = "en") {
  return renderConsole(<MembersPage />, {
    lang,
    auth: {
      status: "authenticated",
      user: FIXTURE_USER,
      workspace: FIXTURE_WORKSPACE,
      workspaces: [FIXTURE_WORKSPACE],
    },
  });
}

describe("MembersPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("treats the role select as a draft: choosing a role sends nothing until Apply (QA M-01)", async () => {
    mockReads();
    const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse({ ...ALI, role: "viewer" }));
    renderMembers();

    const select = await screen.findByLabelText("Role for Ali");
    fireEvent.change(select, { target: { value: "viewer" } });
    expect(patch).not.toHaveBeenCalled();
    expect(screen.getByText(/role changes to Viewer only after Apply/)).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    });
    expect(patch).toHaveBeenCalledTimes(1);
    expect(patch).toHaveBeenCalledWith("/members/mem-2", { role: "viewer" });
    expect(screen.getByRole("status", { name: "" })).toHaveTextContent("Ali is now Viewer.");
  });

  it("names the consequence of a promotion to administrator before it is applied", async () => {
    mockReads();
    renderMembers();
    fireEvent.change(await screen.findByLabelText("Role for Ali"), { target: { value: "admin" } });
    expect(screen.getByText(/An administrator can manage members, settings and the audit log\./)).toBeInTheDocument();
  });

  it("never shows a refused role as current and explains the refusal by code (QA M-01, X-01)", async () => {
    mockReads();
    vi.spyOn(api, "patch").mockRejectedValue(apiError(403, "member_operation_forbidden"));
    renderMembers();

    const select = await screen.findByLabelText("Role for Ali");
    fireEvent.change(select, { target: { value: "viewer" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("You are not allowed to make this change.");
    expect(alert).not.toHaveTextContent("fixture member_operation_forbidden");
    expect(screen.getByLabelText("Role for Ali")).toHaveValue("member");
    expect(screen.queryByRole("button", { name: "Apply" })).not.toBeInTheDocument();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Role for Ali")));
  });

  it("keeps the table inside a positioned, keyboard-reachable scroll region (QA M-02, M-05)", async () => {
    mockReads();
    renderMembers();
    const region = await screen.findByRole("region", { name: "Member list, scrolls horizontally" });
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region.className).toContain("relative");
    expect(region.className).toContain("overflow-x-auto");
    expect(within(region).getByRole("table")).toBeInTheDocument();
  });

  it("names who is removed, focuses Cancel and returns focus to the trigger (QA M-04, M-08)", async () => {
    mockReads();
    const remove = vi.spyOn(api, "delete");
    renderMembers();

    const trash = await screen.findByRole("button", { name: "Remove Ali" });
    fireEvent.click(trash);
    expect(screen.getByText("End Ali's access to this workspace?")).toBeInTheDocument();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Cancel" })));

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Remove Ali" })));
    expect(remove).not.toHaveBeenCalled();
  });

  it("explains a failed invitation delivery, keeps the address and refreshes the list (QA M-03)", async () => {
    const get = mockReads();
    vi.spyOn(api, "post").mockRejectedValue(apiError(503, "invitation_delivery_failed"));
    renderMembers();

    const email = await screen.findByLabelText("Email address");
    fireEvent.change(email, { target: { value: "someone@example.com" } });
    await act(async () => {
      fireEvent.submit(email.closest("form")!);
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The invitation email could not be sent, so the invitation was cancelled automatically.",
    );
    expect(screen.queryByText("Invitation sent.")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Email address")).toHaveValue("someone@example.com");
    await waitFor(() =>
      expect(get.mock.calls.filter(([url]) => url === "/members/invitations").length).toBeGreaterThanOrEqual(2),
    );
  });

  it("accepts Persian digits for the invitation expiry and refuses zero without sending (QA X-03)", async () => {
    mockReads();
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse({ ...PENDING, id: "inv-2" }, 201));
    renderMembers("fa");

    const email = await screen.findByLabelText("ایمیل");
    fireEvent.change(email, { target: { value: "someone@example.com" } });
    const days = screen.getByLabelText("اعتبار (روز)");

    fireEvent.change(days, { target: { value: "۰" } });
    await act(async () => {
      fireEvent.submit(email.closest("form")!);
    });
    expect(post).not.toHaveBeenCalled();
    expect(days).toHaveAttribute("aria-invalid", "true");

    fireEvent.change(days, { target: { value: "۱۴" } });
    await act(async () => {
      fireEvent.submit(email.closest("form")!);
    });
    expect(post).toHaveBeenCalledWith("/members/invitations", {
      email: "someone@example.com",
      role: "member",
      expires_in_days: 14,
    });
  });

  it("labels invitation states and roles in Persian with one term per role (QA X-02, M-06, M-07)", async () => {
    mockReads([OWNER, ALI, { ...ALI, id: "mem-3", user_id: "user-3", display_name: "Sara", role: "viewer" }]);
    renderMembers("fa");
    expect(await screen.findByText("در انتظار پذیرش")).toBeInTheDocument();
    expect(screen.getByText("۳ عضو")).toBeInTheDocument();
    expect(screen.queryByText(/مشاهده‌گر/)).not.toBeInTheDocument();
    expect(screen.getAllByText("بیننده").length).toBeGreaterThan(0);
  });
});
