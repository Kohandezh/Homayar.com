import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import api from "@/lib/api";
import MembersPage from "@/components/platform/MembersPage";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { WorkspaceInvitation, WorkspaceMember, WorkspaceRole } from "@/types/platform";

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

const SARA: WorkspaceMember = {
  id: "mem-3",
  user_id: "user-3",
  email: "sara@example.com",
  phone_e164: null,
  display_name: "Sara",
  role: "viewer",
  joined_at: "2026-09-25T12:00:00Z",
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

function renderMembers(lang: "fa" | "en" = "en", role: WorkspaceRole = "owner") {
  return renderConsole(<MembersPage />, {
    lang,
    auth: {
      status: "authenticated",
      user: FIXTURE_USER,
      workspace: { ...FIXTURE_WORKSPACE, role },
      workspaces: [{ ...FIXTURE_WORKSPACE, role }],
    },
  });
}

/** Opens a HeroUI Select and picks one of its options. */
async function chooseOption(user: UserEvent, triggerName: RegExp | string, optionName: string) {
  await user.click(screen.getByRole("button", { name: triggerName }));
  await user.click(await screen.findByRole("option", { name: optionName }));
}

/** Opens the invite modal and returns its dialog. */
async function openInviteModal(user: UserEvent) {
  await user.click(await screen.findByRole("button", { name: /Invite member|دعوت عضو/ }));
  return screen.findByRole("dialog");
}

describe("MembersPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("treats the role select as a draft: choosing a role sends nothing until Apply (QA M-01)", async () => {
    const user = userEvent.setup();
    mockReads();
    const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse({ ...ALI, role: "viewer" }));
    renderMembers();

    await screen.findByRole("button", { name: /Role for Ali/ });
    await chooseOption(user, /Role for Ali/, "Viewer");
    expect(patch).not.toHaveBeenCalled();
    expect(screen.getByText(/role changes to Viewer only after Apply/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(patch).toHaveBeenCalledTimes(1);
    expect(patch).toHaveBeenCalledWith("/members/mem-2", { role: "viewer" });
    expect(await screen.findByText("Ali is now Viewer.")).toBeInTheDocument();
  });

  it("names the consequence of a promotion to administrator before it is applied", async () => {
    const user = userEvent.setup();
    mockReads();
    renderMembers();
    await screen.findByRole("button", { name: /Role for Ali/ });
    await chooseOption(user, /Role for Ali/, "Administrator");
    expect(screen.getByText(/An administrator can manage members, settings and the audit log\./)).toBeInTheDocument();
  });

  it("never shows a refused role as current and explains the refusal by code (QA M-01, X-01)", async () => {
    const user = userEvent.setup();
    mockReads();
    vi.spyOn(api, "patch").mockRejectedValue(apiError(403, "member_operation_forbidden"));
    renderMembers();

    await screen.findByRole("button", { name: /Role for Ali/ });
    await chooseOption(user, /Role for Ali/, "Viewer");
    await user.click(screen.getByRole("button", { name: "Apply" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("You are not allowed to make this change.");
    expect(alert).not.toHaveTextContent("fixture member_operation_forbidden");
    expect(screen.queryByRole("button", { name: "Apply" })).not.toBeInTheDocument();
    const select = screen.getByRole("button", { name: /Role for Ali/ });
    expect(select).toHaveTextContent("Member");
    await waitFor(() => expect(document.activeElement).toBe(select));
  });

  it("lists the members in a table that sorts by name and by join date", async () => {
    const user = userEvent.setup();
    mockReads([OWNER, ALI, SARA]);
    renderMembers();

    const table = await screen.findByRole("grid", { name: "Workspace members" });
    const names = () =>
      within(table)
        .getAllByRole("rowheader")
        .map((cell) => cell.textContent);
    expect(names()).toEqual(["AAliali@example.com", "PPerson(you)person@example.com", "SSarasara@example.com"]);

    await user.click(within(table).getByRole("columnheader", { name: /Joined/ }));
    expect(names()[2]).toContain("Sara");

    await user.click(within(table).getByRole("columnheader", { name: /Joined/ }));
    expect(names()[0]).toContain("Sara");
  });

  it("filters the table by a search term and by role, without a request", async () => {
    const user = userEvent.setup();
    const get = mockReads([OWNER, ALI, SARA]);
    renderMembers();

    const table = await screen.findByRole("grid", { name: "Workspace members" });
    const readsBefore = get.mock.calls.length;

    await user.type(screen.getByRole("searchbox", { name: "Search members" }), "sara");
    expect(within(table).getAllByRole("rowheader")).toHaveLength(1);
    expect(within(table).getByRole("rowheader")).toHaveTextContent("Sara");

    await user.click(screen.getByRole("button", { name: "Clear search" }));
    await chooseOption(user, /Filter by role/, "Owner");
    expect(within(table).getAllByRole("rowheader")).toHaveLength(1);
    expect(within(table).getByRole("rowheader")).toHaveTextContent("Person");

    expect(get.mock.calls.length).toBe(readsBefore);
  });

  it("keeps the table inside a keyboard-reachable scroll region (QA M-02, M-05)", async () => {
    mockReads();
    renderMembers();
    const region = await screen.findByRole("region", { name: "Member list, scrolls horizontally" });
    expect(region).toHaveAttribute("tabindex", "0");
    expect(within(region).getByRole("grid", { name: "Workspace members" })).toBeInTheDocument();
  });

  it("names who is removed and sends nothing when the confirmation is cancelled (QA M-04, M-08)", async () => {
    const user = userEvent.setup();
    mockReads();
    const remove = vi.spyOn(api, "delete");
    renderMembers();

    const trash = await screen.findByRole("button", { name: "Remove Ali" });
    await user.click(trash);
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("End Ali's access to this workspace?")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    // The table owns focus inside the grid, so it comes back to the cell the
    // trash button sits in; the button is one Tab away.
    const trashAgain = screen.getByRole("button", { name: "Remove Ali" });
    await waitFor(() => expect(document.activeElement).toContainElement(trashAgain));
    expect(remove).not.toHaveBeenCalled();
  });

  it("confirms a removal, then announces it with a toast", async () => {
    const user = userEvent.setup();
    mockReads();
    const remove = vi.spyOn(api, "delete").mockResolvedValue(okResponse(null, 204));
    renderMembers();

    await user.click(await screen.findByRole("button", { name: "Remove Ali" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Remove member" }));

    expect(remove).toHaveBeenCalledWith("/members/mem-2");
    expect(await screen.findByText("Ali was removed from the workspace.")).toBeInTheDocument();
  });

  it("keeps the confirmation open and names the refusal inside it (QA X-01)", async () => {
    const user = userEvent.setup();
    mockReads();
    vi.spyOn(api, "delete").mockRejectedValue(apiError(403, "member_operation_forbidden"));
    renderMembers();

    await user.click(await screen.findByRole("button", { name: "Remove Ali" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Remove member" }));

    // The reason has to be in the dialog: the page banner is behind the backdrop.
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("You are not allowed to make this change.");
    expect(alert).not.toHaveTextContent("fixture member_operation_forbidden");
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Remove Ali" })).not.toBeDisabled();
  });

  it("explains a failed invitation delivery, keeps the address and refreshes the list (QA M-03)", async () => {
    const user = userEvent.setup();
    const get = mockReads();
    vi.spyOn(api, "post").mockRejectedValue(apiError(503, "invitation_delivery_failed"));
    renderMembers();

    const dialog = await openInviteModal(user);
    const email = within(dialog).getByLabelText("Email address");
    await user.type(email, "someone@example.com");
    await user.click(within(dialog).getByRole("button", { name: "Send invite" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The invitation email could not be sent, so the invitation was cancelled automatically.",
    );
    expect(screen.queryByText("Invitation sent.")).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Email address")).toHaveValue("someone@example.com");
    await waitFor(() =>
      expect(get.mock.calls.filter(([url]) => url === "/members/invitations").length).toBeGreaterThanOrEqual(2),
    );
  });

  it("sends an invitation with the role and the expiry typed in Persian digits (QA X-03)", async () => {
    const user = userEvent.setup();
    mockReads();
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse({ ...PENDING, id: "inv-2" }, 201));
    renderMembers("fa");

    const dialog = await openInviteModal(user);
    await user.type(within(dialog).getByLabelText("ایمیل"), "someone@example.com");

    // The label names both the field's group and its input, so the role picks the input.
    const days = within(dialog).getByRole("textbox", { name: "اعتبار (روز)" });
    await user.clear(days);
    await user.type(days, "۱۴");
    await user.tab();

    await user.click(within(dialog).getByRole("button", { name: "ارسال دعوت" }));
    expect(post).toHaveBeenCalledWith("/members/invitations", {
      email: "someone@example.com",
      role: "member",
      expires_in_days: 14,
    });
    expect(await screen.findByText("دعوت‌نامه ارسال شد.")).toBeInTheDocument();
  });

  it("never sends an expiry outside one to thirty days (QA X-03)", async () => {
    const user = userEvent.setup();
    mockReads();
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse({ ...PENDING, id: "inv-2" }, 201));
    renderMembers();

    const dialog = await openInviteModal(user);
    await user.type(within(dialog).getByLabelText("Email address"), "someone@example.com");
    const days = within(dialog).getByRole("textbox", { name: "Expiry (days)" });

    // An empty expiry is not a number, so the form sends nothing at all.
    await user.clear(days);
    await user.click(within(dialog).getByRole("button", { name: "Send invite" }));
    expect(post).not.toHaveBeenCalled();

    // The field clamps a value under the minimum when it loses focus, so 0
    // becomes 1 and that is what the request carries.
    await user.type(days, "0");
    await user.tab();
    expect(days).toHaveValue("1");

    await user.click(within(dialog).getByRole("button", { name: "Send invite" }));
    expect(post).toHaveBeenCalledWith("/members/invitations", {
      email: "someone@example.com",
      role: "member",
      expires_in_days: 1,
    });
  });

  it("groups the invitations by state in tabs and offers a revoke only for pending ones", async () => {
    const user = userEvent.setup();
    mockReads([OWNER, ALI], [
      PENDING,
      { ...PENDING, id: "inv-2", email: "old@example.com", status: "revoked", revoked_at: T },
    ]);
    const remove = vi.spyOn(api, "delete").mockResolvedValue(okResponse(null, 204));
    renderMembers();

    const pendingPanel = await screen.findByRole("tabpanel", { name: /Pending/ });
    expect(within(pendingPanel).getByText("new@example.com")).toBeInTheDocument();
    expect(within(pendingPanel).queryByText("old@example.com")).not.toBeInTheDocument();

    await user.click(within(pendingPanel).getByRole("button", { name: /Revoke the invitation for new@example.com/ }));
    expect(remove).toHaveBeenCalledWith("/members/invitations/inv-1");

    await user.click(screen.getByRole("tab", { name: /Revoked/ }));
    const revokedPanel = await screen.findByRole("tabpanel", { name: /Revoked/ });
    expect(within(revokedPanel).getByText("old@example.com")).toBeInTheDocument();
    expect(within(revokedPanel).queryByRole("button", { name: /Revoke the invitation/ })).not.toBeInTheDocument();
  });

  it("labels invitation states and roles in Persian with one term per role (QA X-02, M-06, M-07)", async () => {
    mockReads([OWNER, ALI, SARA]);
    renderMembers("fa");
    expect(await screen.findByText("در انتظار پذیرش")).toBeInTheDocument();
    expect(screen.getByText("۳ عضو")).toBeInTheDocument();
    expect(screen.queryByText(/مشاهده‌گر/)).not.toBeInTheDocument();
    expect(screen.getAllByText("بیننده").length).toBeGreaterThan(0);
  });

  it("offers an administrator only the member and viewer roles, and nothing for the owner or for self", async () => {
    const user = userEvent.setup();
    const self: WorkspaceMember = { ...OWNER, role: "admin" };
    mockReads([self, ALI]);
    renderMembers("en", "admin");

    await screen.findByRole("button", { name: /Role for Ali/ });
    expect(screen.queryByRole("button", { name: /Role for Person/ })).not.toBeInTheDocument();
    expect(screen.getByText("Your account")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Role for Ali/ }));
    const options = (await screen.findAllByRole("option")).map((option) => option.textContent);
    expect(options).toEqual(["Member", "Viewer"]);
  });

  it("shows no management control and no invitations to a plain member", async () => {
    mockReads();
    renderMembers("en", "member");

    await screen.findByRole("grid", { name: "Workspace members" });
    expect(screen.queryByRole("button", { name: /Role for Ali/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove Ali" })).not.toBeInTheDocument();
    expect(screen.getAllByText("Read only").length).toBeGreaterThan(0);
    expect(screen.queryByRole("heading", { name: "Invitations" })).not.toBeInTheDocument();
  });
});
