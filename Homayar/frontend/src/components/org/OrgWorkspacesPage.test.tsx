import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import api from "@/lib/api";
import { apiError, okResponse } from "@/test/fixtures";
import {
  ALI,
  LOAD,
  ORG_ID,
  OWNER_ROW,
  chooseOption,
  member,
  mockOrgReads,
  optionNames,
  orgUrl,
  renderOrg,
  requestedUrls,
  setupUser,
  workspaceRow,
} from "@/components/org/org-test-kit";

const BASE = `/organizations/${ORG_ID}`;

function rowOf(name: string) {
  const row = screen.getByRole("rowheader", { name: new RegExp(name) }).closest("tr");
  if (!row) throw new Error(`no row for ${name}`);
  return row;
}

async function openInstall(user = setupUser()) {
  fireEvent.click(await screen.findByRole("button", { name: "Add member to Tehran office" }, LOAD));
  const dialog = await screen.findByRole("dialog", undefined, LOAD);
  expect(within(dialog).getByRole("heading", { name: "Add member to workspace" })).toBeInTheDocument();
  return { dialog, user };
}

describe("organization workspaces page", { timeout: 60_000 }, () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists the workspaces with slug, status, your role and created date", async () => {
    mockOrgReads({ workspaces: [workspaceRow(), workspaceRow({ id: "ws-2", name: "Shiraz", slug: "shiraz", my_workspace_role: null })] });
    renderOrg(orgUrl("/workspaces"));
    expect(await screen.findByRole("heading", { level: 1, name: "Workspaces" }, LOAD)).toBeInTheDocument();
    await screen.findByRole("grid", { name: "Organization workspaces" }, LOAD);
    expect(within(rowOf("Tehran office")).getByText("tehran")).toHaveAttribute("dir", "ltr");
    expect(within(rowOf("Tehran office")).getByText("Owner")).toBeInTheDocument();
    expect(within(rowOf("Tehran office")).getByText("Active")).toBeInTheDocument();
    expect(within(rowOf("Shiraz")).getByText("—")).toBeInTheDocument();
  });

  it("says when there is no workspace yet", async () => {
    mockOrgReads({ workspaces: [] });
    renderOrg(orgUrl("/workspaces"));
    expect(await screen.findByText("No workspaces yet.", undefined, LOAD)).toBeInTheDocument();
  });

  it("creates a workspace with the trimmed name and refreshes the list and the overview", async () => {
    const user = setupUser();
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse(workspaceRow({ id: "ws-9", name: "Isfahan" }), 201));
    const get = mockOrgReads();
    const view = renderOrg(orgUrl("/workspaces"));
    await screen.findByRole("grid", { name: "Organization workspaces" }, LOAD);
    // A cached overview must be marked stale too.
    view.queryClient.setQueryData(["organizations", "detail", ORG_ID, "aggregates", { since: null }], { items: [] });

    fireEvent.click(screen.getByRole("button", { name: "Create workspace" }));
    const dialog = await screen.findByRole("dialog", undefined, LOAD);
    await user.type(within(dialog).getByLabelText("Name"), "  Isfahan  ");
    fireEvent.click(within(dialog).getByRole("button", { name: "Create workspace" }));

    await waitFor(() => expect(post).toHaveBeenCalledWith(`${BASE}/workspaces`, { name: "Isfahan" }), LOAD);
    expect(await screen.findByText("Workspace created.", undefined, LOAD)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(), LOAD);
    await waitFor(() => expect(requestedUrls(get).filter((url) => url === `${BASE}/workspaces`).length).toBe(2), LOAD);
    expect(
      view.queryClient.getQueryState(["organizations", "detail", ORG_ID, "aggregates", { since: null }])?.isInvalidated,
    ).toBe(true);
  });

  it("never sends an empty name", async () => {
    const user = setupUser();
    const post = vi.spyOn(api, "post");
    mockOrgReads();
    renderOrg(orgUrl("/workspaces"));
    fireEvent.click(await screen.findByRole("button", { name: "Create workspace" }, LOAD));
    const dialog = await screen.findByRole("dialog", undefined, LOAD);
    await user.type(within(dialog).getByLabelText("Name"), "   ");
    fireEvent.click(within(dialog).getByRole("button", { name: "Create workspace" }));
    expect(await within(dialog).findByText("Enter a name", undefined, LOAD)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("installs an effective organization member in a workspace", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue(
      okResponse({ membership_id: "wm-1", workspace_id: "ws-1", user_id: "user-2", role: "viewer", is_active: true }, 201),
    );
    const expired = member({ id: "om-x", user_id: "user-x", display_name: "Old", role: "analyst", is_effective: false });
    const get = mockOrgReads({ members: [OWNER_ROW, ALI, expired] });
    renderOrg(orgUrl("/workspaces"));
    await screen.findByRole("grid", { name: "Organization workspaces" }, LOAD);
    // The member list is read only when the dialog opens.
    expect(requestedUrls(get)).not.toContain(`${BASE}/members`);

    const { dialog, user } = await openInstall();
    const memberTrigger = await within(dialog).findByRole("button", { name: /Member/ }, LOAD);
    const names = await optionNames(user, memberTrigger);
    expect(names.some((name) => name?.includes("Ali"))).toBe(true);
    expect(names.some((name) => name?.includes("Old"))).toBe(false);
    expect(await optionNames(user, within(dialog).getByRole("button", { name: /Role/ }))).toEqual([
      "Administrator",
      "Member",
      "Viewer",
    ]);

    await chooseOption(user, memberTrigger, /Ali/);
    await chooseOption(user, within(dialog).getByRole("button", { name: /Role/ }), "Viewer");
    fireEvent.click(within(dialog).getByRole("button", { name: "Add member" }));
    await waitFor(
      () => expect(post).toHaveBeenCalledWith(`${BASE}/workspaces/ws-1/members`, { user_id: "user-2", role: "viewer" }),
      LOAD,
    );
    expect(await screen.findByText("Ali was added to Tehran office.", undefined, LOAD)).toBeInTheDocument();
  });

  it.each([
    ["seat_limit_reached", 409, "The workspace has no free seat."],
    ["delegation_denied", 403, "Your role cannot give this workspace role."],
    ["organization_member_not_found", 404, "This person is no longer an organization member."],
  ] as const)("keeps the dialog open with the text of %s", async (code, status, text) => {
    vi.spyOn(api, "post").mockRejectedValue(apiError(status, code));
    mockOrgReads({ members: [OWNER_ROW, ALI] });
    renderOrg(orgUrl("/workspaces"));
    const { dialog, user } = await openInstall();
    await chooseOption(user, await within(dialog).findByRole("button", { name: /Member/ }, LOAD), /Ali/);
    await chooseOption(user, within(dialog).getByRole("button", { name: /Role/ }), "Member");
    fireEvent.click(within(dialog).getByRole("button", { name: "Add member" }));
    expect(await within(dialog).findByRole("alert", undefined, LOAD)).toHaveTextContent(text);
    expect(screen.queryByRole("heading", { level: 1, name: "No access" })).not.toBeInTheDocument();
  });
});
