import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import api from "@/lib/api";
import { apiError, okResponse } from "@/test/fixtures";
import {
  ALI,
  LOAD,
  ORG_ID,
  OWNER_ROW,
  SARA,
  chooseOption,
  grant,
  member,
  mockOrgReads,
  optionNames,
  orgUrl,
  renderOrg,
  setupUser,
  workspaceRow,
} from "@/components/org/org-test-kit";

const BASE = `/organizations/${ORG_ID}`;
const ROYA = member({ id: "om-4", user_id: "user-4", email: "roya@example.com", display_name: "Roya", role: "executive" });

function rowFor(text: string) {
  const row = screen.getAllByRole("row").find((candidate) => candidate.textContent?.includes(text));
  if (!row) throw new Error(`no row with ${text}`);
  return row;
}

async function openAdd() {
  fireEvent.click(await screen.findByRole("button", { name: "Add scope grant" }, LOAD));
  return screen.findByRole("dialog", undefined, LOAD);
}

describe("scope grants page", { timeout: 60_000 }, () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("names members and workspaces, falls back to raw ids, and shows the scope and status", async () => {
    mockOrgReads({
      members: [OWNER_ROW, ALI],
      workspaces: [workspaceRow()],
      grants: [
        grant(),
        grant({ id: "sg-2", membership_id: "om-gone", workspace_id: "ws-gone", team_id: "team-7", revoked_at: "2026-10-01T12:00:00Z" }),
      ],
    });
    renderOrg(orgUrl("/scope-grants"));
    expect(await screen.findByRole("heading", { level: 1, name: "Scope grants" }, LOAD)).toBeInTheDocument();
    expect(screen.getByText("Analysts and executives see only the workspaces granted to them here.")).toBeInTheDocument();
    await screen.findByRole("grid", { name: "Scope grants" }, LOAD);

    const named = rowFor("Ali");
    expect(within(named).getByText("ali@example.com")).toBeInTheDocument();
    expect(within(named).getByText("Tehran office")).toBeInTheDocument();
    expect(within(named).getByText("Whole workspace")).toBeInTheDocument();
    expect(within(named).getByText("Active")).toBeInTheDocument();
    expect(within(named).getByRole("button", { name: "Revoke grant for Ali" })).toBeInTheDocument();

    const raw = rowFor("om-gone");
    expect(within(raw).getByText("om-gone")).toHaveAttribute("dir", "ltr");
    expect(within(raw).getByText("ws-gone")).toBeInTheDocument();
    expect(within(raw).getByText("team-7")).toHaveAttribute("dir", "ltr");
    expect(raw.textContent).toContain("Team");
    expect(within(raw).getByText("Revoked")).toBeInTheDocument();
    // History has no action.
    expect(within(raw).queryAllByRole("button")).toEqual([]);
  });

  it("says when there is no grant yet", async () => {
    mockOrgReads({ grants: [] });
    renderOrg(orgUrl("/scope-grants"));
    expect(await screen.findByText("No scope grants yet.", undefined, LOAD)).toBeInTheDocument();
  });

  it("offers only effective analysts and executives, and sends membership and workspace only", async () => {
    const user = setupUser();
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse(grant({ id: "sg-9" }), 201));
    const expired = member({ id: "om-x", user_id: "user-x", display_name: "Old", role: "analyst", is_effective: false });
    mockOrgReads({ members: [OWNER_ROW, ALI, SARA, ROYA, expired], workspaces: [workspaceRow(), workspaceRow({ id: "ws-2", name: "Shiraz" })] });
    renderOrg(orgUrl("/scope-grants"));
    const dialog = await openAdd();
    const memberTrigger = within(dialog).getByRole("button", { name: /Member/ });
    const names = await optionNames(user, memberTrigger);
    // Each option holds the name, then the role and the email.
    expect(names).toEqual([expect.stringMatching(/^Ali/), expect.stringMatching(/^Roya/)]);

    await chooseOption(user, memberTrigger, /Roya/);
    await chooseOption(user, within(dialog).getByRole("button", { name: /Workspace/ }), "Shiraz");
    fireEvent.click(within(dialog).getByRole("button", { name: "Grant scope" }));
    await waitFor(
      () => expect(post).toHaveBeenCalledWith(`${BASE}/scope-grants`, { membership_id: "om-4", workspace_id: "ws-2" }),
      LOAD,
    );
    expect(await screen.findByText("Scope granted.", undefined, LOAD)).toBeInTheDocument();
  });

  it("explains that an analyst or executive is needed first, with no submit", async () => {
    mockOrgReads({ members: [OWNER_ROW, SARA] });
    renderOrg(orgUrl("/scope-grants"));
    const dialog = await openAdd();
    expect(within(dialog).getByText("Add an analyst or an executive first.")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Grant scope" })).not.toBeInTheDocument();
  });

  it.each([
    ["scope_grant_exists", 409, "This member already has this workspace."],
    ["invalid_scope_target", 422, "Only active analysts and executives can get a scope grant."],
  ] as const)("keeps the dialog open with the text of %s", async (code, status, text) => {
    const user = setupUser();
    vi.spyOn(api, "post").mockRejectedValue(apiError(status, code));
    mockOrgReads({ members: [OWNER_ROW, ALI] });
    renderOrg(orgUrl("/scope-grants"));
    const dialog = await openAdd();
    await chooseOption(user, within(dialog).getByRole("button", { name: /Member/ }), /Ali/);
    await chooseOption(user, within(dialog).getByRole("button", { name: /Workspace/ }), "Tehran office");
    fireEvent.click(within(dialog).getByRole("button", { name: "Grant scope" }));
    expect(await within(dialog).findByRole("alert", undefined, LOAD)).toHaveTextContent(text);
  });

  it("revokes an active grant after the confirmation", async () => {
    const remove = vi.spyOn(api, "delete").mockResolvedValue(okResponse(undefined, 204));
    mockOrgReads({ members: [OWNER_ROW, ALI], grants: [grant()] });
    renderOrg(orgUrl("/scope-grants"));
    fireEvent.click(await screen.findByRole("button", { name: "Revoke grant for Ali" }, LOAD));
    const dialog = await screen.findByRole("alertdialog", undefined, LOAD);
    expect(within(dialog).getByRole("heading", { name: "Revoke scope grant?" })).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Ali stops seeing Tehran office now.");
    // Each name picks its own direction; the sentence does not.
    for (const name of ["Ali", "Tehran office"]) {
      expect(within(dialog).getByText(name).tagName).toBe("BDI");
      expect(within(dialog).getByText(name)).toHaveAttribute("dir", "auto");
    }
    expect(within(dialog).getByText("Ali").parentElement).not.toHaveAttribute("dir");
    fireEvent.click(within(dialog).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith(`${BASE}/scope-grants/sg-1`), LOAD);
    expect(await screen.findByText("Scope grant revoked.", undefined, LOAD)).toBeInTheDocument();
  });
});
