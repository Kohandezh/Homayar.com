import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import TeamsPage from "@/components/admin/TeamsPage";
import api from "@/lib/api";
import {
  FIXTURE_USER,
  FIXTURE_WORKSPACE,
  apiError,
  okResponse,
  renderConsole,
} from "@/test/fixtures";
import type { AuthWorkspace, WorkspaceRole } from "@/types/auth";
import type { Team, TeamAssignment, TeamDetail, WorkspaceMember, WorkspaceSettings } from "@/types/platform";

const T = "2026-09-24T12:00:00Z";
// The page waits for the list plus one detail read per team.
const LOAD = { timeout: 5000 };

function team(overrides: Partial<Team> = {}): Team {
  return {
    id: "team-1",
    workspace_id: "ws-1",
    name: "Sales",
    slug: "sales",
    is_active: true,
    created_at: T,
    updated_at: T,
    ...overrides,
  };
}

function assignment(overrides: Partial<TeamAssignment> = {}): TeamAssignment {
  return {
    id: "assignment-1",
    team_id: "team-1",
    user_id: "user-2",
    display_name: "Ali",
    email: "ali@example.com",
    role: "agent",
    valid_from: "2026-01-01T00:00:00Z",
    valid_until: null,
    revoked_at: null,
    is_effective: true,
    created_at: T,
    ...overrides,
  };
}

function detail(of: Team, assignments: TeamAssignment[]): TeamDetail {
  return { ...of, assignments };
}

const MEMBER: WorkspaceMember = {
  id: "mem-2",
  user_id: "user-2",
  email: "ali@example.com",
  phone_e164: null,
  display_name: "Ali",
  role: "member",
  joined_at: T,
};

function settings(overrides: Partial<WorkspaceSettings> = {}): WorkspaceSettings {
  return {
    id: "ws-1",
    name: "Fixture workspace",
    retention_days: 90,
    retention_mode: "finite",
    delete_audio_after_processing: false,
    record_scope: "workspace",
    public_share_links_enabled: true,
    organization_share_links_enabled: false,
    duplicate_media_policy: "allow",
    pii_redaction_enabled: false,
    pii_redaction_classes: [],
    updated_at: T,
    ...overrides,
  };
}

function workspace(role: WorkspaceRole, permissions?: string[]): AuthWorkspace {
  return { ...FIXTURE_WORKSPACE, role, ...(permissions ? { permissions } : {}) };
}

interface Reads {
  teams?: Team[];
  details?: Record<string, TeamAssignment[]>;
  settings?: WorkspaceSettings | Error;
  members?: WorkspaceMember[] | Error;
}

/** Serves the four reads of the page; anything else is a 404. */
function mockReads(reads: Reads = {}) {
  const teams = reads.teams ?? [team()];
  const details = reads.details ?? {};
  const get = vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url === "/teams") return okResponse({ items: teams, total: teams.length });
    if (url.startsWith("/teams/")) {
      const id = url.slice("/teams/".length);
      const found = teams.find((candidate) => candidate.id === id);
      if (!found) throw apiError(404, "team_not_found");
      return okResponse(detail(found, details[id] ?? []));
    }
    if (url === "/admin/workspace-settings") {
      if (reads.settings instanceof Error) throw reads.settings;
      return okResponse(reads.settings ?? settings());
    }
    if (url === "/members") {
      if (reads.members instanceof Error) throw reads.members;
      return okResponse({ items: reads.members ?? [MEMBER], total: (reads.members ?? [MEMBER]).length });
    }
    throw apiError(404, "not_found");
  });
  return get;
}

function requestedUrls(spy: ReturnType<typeof mockReads>) {
  return spy.mock.calls.map(([url]) => url);
}

function renderPage(selected: AuthWorkspace = workspace("owner"), lang: "fa" | "en" = "en") {
  window.localStorage.removeItem("homayar_lang");
  return renderConsole(<TeamsPage />, {
    route: "/admin/teams",
    lang,
    auth: { status: "authenticated", user: FIXTURE_USER, workspace: selected, workspaces: [selected] },
  });
}

/** Waits for the list and every member count to have answered. */
async function findRow(name: string) {
  return screen.findByRole("rowheader", { name }, LOAD);
}

/** Re-queries the row on every try: React Aria can replace its cells. */
async function expectInRow(name: string, text: string) {
  await waitFor(
    () => {
      const row = screen.getByRole("rowheader", { name }).closest("tr");
      if (!row) throw new Error(`no row for ${name}`);
      expect(within(row).getByText(text)).toBeInTheDocument();
    },
    LOAD,
  );
}

describe("TeamsPage", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("counts distinct effective members only, and tolerates a failed detail read", async () => {
    mockReads({
      teams: [team(), team({ id: "team-2", name: "Support", slug: "support" })],
      details: {
        // Two roles of one user, a second user, and rows that must not count.
        "team-1": [
          assignment({ user_id: "user-2", role: "agent", is_effective: true }),
          assignment({ id: "a2", user_id: "user-2", role: "supervisor", is_effective: true }),
          assignment({ id: "a3", user_id: "user-3", display_name: "Sara", is_effective: true }),
          assignment({ id: "a4", user_id: "user-4", is_effective: false, revoked_at: T }),
          assignment({
            id: "a5",
            user_id: "user-5",
            is_effective: false,
            valid_until: "2026-06-01T00:00:00Z",
          }),
        ],
        "team-2": [],
      },
    });
    renderPage();

    await expectInRow("Sales", "2");
    await expectInRow("Support", "0");
  });

  it("shows a dash when a team's detail read fails, without breaking the table", async () => {
    const get = vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/teams") {
        return okResponse({ items: [team()], total: 1 });
      }
      if (url === "/admin/workspace-settings") return okResponse(settings());
      throw apiError(500, "server_error");
    });
    renderPage();

    await expectInRow("Sales", "—");
    expect(get).toHaveBeenCalled();
  });

  it("marks the table busy only while the list is loading", async () => {
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/teams") {
        await new Promise((resolve) => setTimeout(resolve, 50));
        return okResponse({ items: [team()], total: 1 });
      }
      if (url.startsWith("/teams/")) return okResponse(detail(team(), []));
      return okResponse(settings());
    });
    renderPage();

    expect(document.querySelector('[aria-busy="true"]')).not.toBeNull();
    await findRow("Sales");
    await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeNull(), LOAD);
  });

  it("shows the team record scope with the settings link", async () => {
    mockReads({ settings: settings({ record_scope: "team" }) });
    renderPage();
    await findRow("Sales");
    const link = await screen.findByRole("link", { name: "Change in settings" }, LOAD);
    expect(link).toHaveAttribute("href", "/admin/settings");
    expect(screen.getByText(/Record access: by team\./)).toBeInTheDocument();
  });

  it("shows the workspace scope text", async () => {
    mockReads();
    renderPage();
    await findRow("Sales");
    expect(await screen.findByText(/Record access: whole workspace\./, undefined, LOAD)).toBeInTheDocument();
  });

  it("keeps the table when the settings request fails", async () => {
    mockReads({ settings: apiError(503, "unavailable") });
    renderPage();
    const alert = await screen.findByRole("alert", undefined, LOAD);
    expect(alert).toHaveTextContent("The record access setting could not be loaded.");
    expect(await findRow("Sales")).toBeInTheDocument();
  });

  it("creates a team with the trimmed name", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse(team({ id: "team-9", name: "Field Ops" })));
    mockReads();

    renderPage();
    await findRow("Sales");
    fireEvent.click(screen.getByRole("button", { name: "Create team" }));

    const dialog = await screen.findByRole("dialog", undefined, LOAD);
    expect(within(dialog).getByRole("heading", { name: "Create team" })).toBeInTheDocument();
    const field = within(dialog).getByLabelText("Team name");
    await userEvent.type(field, "  Field Ops  ");
    // Empty or whitespace-only names never submit.
    expect(within(dialog).getByRole("button", { name: "Create" })).toBeEnabled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/teams", { name: "Field Ops" }), LOAD);
    expect(await screen.findByText("Team created.", undefined, LOAD)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(), LOAD);
  });

  it("renames a team, refusing an empty and an unchanged name", async () => {
    const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse(team({ name: "Sales team" })));
    mockReads();

    renderPage();
    await findRow("Sales");
    fireEvent.click(screen.getByRole("button", { name: "Rename team Sales" }));

    const dialog = await screen.findByRole("dialog", undefined, LOAD);
    const field = within(dialog).getByLabelText("Team name");
    expect(field).toHaveValue("Sales");
    // The current name is not a change, so Save stays off.
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled();

    await userEvent.clear(field);
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.type(field, " Sales team ");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patch).toHaveBeenCalledWith("/teams/team-1", { name: "Sales team" }), LOAD);
    expect(await screen.findByText("Team renamed.", undefined, LOAD)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(), LOAD);
  });

  it("keeps the create dialog open with the typed name on a team_conflict", async () => {
    vi.spyOn(api, "post").mockRejectedValue(apiError(409, "team_conflict"));
    mockReads();

    renderPage();
    await findRow("Sales");
    fireEvent.click(screen.getByRole("button", { name: "Create team" }));
    const dialog = await screen.findByRole("dialog", undefined, LOAD);
    await userEvent.type(within(dialog).getByLabelText("Team name"), "Sales");
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));

    expect(await within(dialog).findByRole("alert", undefined, LOAD)).toHaveTextContent(
      "A team with this name already exists.",
    );
    expect(within(dialog).getByLabelText("Team name")).toHaveValue("Sales");
    expect(within(dialog).getByRole("button", { name: "Create" })).toBeEnabled();
  });

  it("deactivates a team with only the is_active key", async () => {
    const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse(team({ is_active: false })));
    mockReads();

    renderPage();
    await findRow("Sales");
    fireEvent.click(screen.getByRole("button", { name: "Deactivate team Sales" }));

    const dialog = await screen.findByRole("alertdialog", undefined, LOAD);
    expect(within(dialog).getByRole("heading", { name: "Deactivate Sales?" })).toBeInTheDocument();
    expect(within(dialog).getByText(/Its assignments stop giving access to records\./)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Deactivate" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith("/teams/team-1", { is_active: false }), LOAD);
    expect(await screen.findByText("Team deactivated.", undefined, LOAD)).toBeInTheDocument();
  });

  it("reactivates an inactive team with only the is_active key", async () => {
    const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse(team()));
    mockReads({ teams: [team({ is_active: false })] });

    renderPage();
    await findRow("Sales");
    fireEvent.click(screen.getByRole("button", { name: "Reactivate team Sales" }));

    const dialog = await screen.findByRole("alertdialog", undefined, LOAD);
    expect(within(dialog).getByRole("heading", { name: "Reactivate Sales?" })).toBeInTheDocument();
    expect(within(dialog).getByText(/Its current assignments give access to records again\./)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Reactivate" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith("/teams/team-1", { is_active: true }), LOAD);
    expect(await screen.findByText("Team reactivated.", undefined, LOAD)).toBeInTheDocument();
  });

  it("keeps a failed status change in its dialog", async () => {
    vi.spyOn(api, "patch").mockRejectedValue(apiError(500, "server_error"));
    mockReads();

    renderPage();
    await findRow("Sales");
    fireEvent.click(screen.getByRole("button", { name: "Deactivate team Sales" }));
    const dialog = await screen.findByRole("alertdialog", undefined, LOAD);
    fireEvent.click(within(dialog).getByRole("button", { name: "Deactivate" }));

    expect(await within(dialog).findByRole("alert", undefined, LOAD)).toBeInTheDocument();
    expect(dialog).toBeInTheDocument();
  });

  it("shows every assignment status in the drawer, with revoke only where it applies", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(Date.parse(T));
    mockReads({
      details: {
        "team-1": [
          assignment({ id: "a1", display_name: "Ali", role: "supervisor", is_effective: true }),
          assignment({ id: "a2", display_name: "Sara", role: "agent", revoked_at: T, is_effective: false }),
          assignment({
            id: "a3",
            display_name: "Mary",
            is_effective: false,
            valid_from: "2027-01-01T00:00:00Z",
          }),
          assignment({
            id: "a4",
            display_name: "Old",
            is_effective: false,
            valid_until: "2026-06-01T00:00:00Z",
          }),
        ],
      },
    });
    renderPage();
    await findRow("Sales");

    fireEvent.click(screen.getByRole("button", { name: "Open team Sales" }));
    const drawer = await screen.findByRole("dialog", { name: "Sales" }, LOAD);

    expect(within(drawer).getByRole("heading", { level: 2, name: "Sales" })).toBeInTheDocument();
    expect(within(drawer).getByText("Active")).toBeInTheDocument();
    const table = within(drawer).getByRole("grid", { name: "Team assignments" });
    expect(within(table).getByText("Supervisor")).toBeInTheDocument();
    expect(within(table).getAllByText("Agent").length).toBeGreaterThan(0);
    expect(within(table).getAllByText("No end date").length).toBeGreaterThan(0);
    expect(within(table).getByText("Effective")).toBeInTheDocument();
    expect(within(table).getByText("Revoked")).toBeInTheDocument();
    expect(within(table).getByText("Starts later")).toBeInTheDocument();
    expect(within(table).getByText("Ended")).toBeInTheDocument();

    expect(within(table).getByRole("button", { name: "Revoke Ali as Supervisor" })).toBeInTheDocument();
    // A future row can still be revoked; history cannot.
    expect(within(table).getByRole("button", { name: "Revoke Mary as Agent" })).toBeInTheDocument();
    expect(within(table).queryByRole("button", { name: /Revoke Sara/ })).not.toBeInTheDocument();
    expect(within(table).queryByRole("button", { name: /Revoke Old/ })).not.toBeInTheDocument();
  });

  it("revokes an assignment and reports it", async () => {
    const remove = vi.spyOn(api, "delete").mockResolvedValue(okResponse(undefined, 204));
    mockReads({ details: { "team-1": [assignment()] } });

    renderPage();
    await findRow("Sales");
    fireEvent.click(screen.getByRole("button", { name: "Open team Sales" }));
    const drawer = await screen.findByRole("dialog", { name: "Sales" }, LOAD);
    fireEvent.click(within(drawer).getByRole("button", { name: "Revoke Ali as Agent" }));

    const confirm = await screen.findByRole("alertdialog", undefined, LOAD);
    expect(within(confirm).getByRole("heading", { name: "Revoke this assignment?" })).toBeInTheDocument();
    expect(within(confirm).getByText(/Ali stops being agent of Sales now\./)).toBeInTheDocument();
    expect(within(confirm).getByText(/The row stays in the history\./)).toBeInTheDocument();

    fireEvent.click(within(confirm).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith("/teams/team-1/assignments/assignment-1"), LOAD);
    expect(await screen.findByText("Assignment revoked.", undefined, LOAD)).toBeInTheDocument();
  });

  it("never shows one row's refused revoke in another row's confirmation", async () => {
    vi.spyOn(api, "delete").mockRejectedValue(apiError(500, "server_error"));
    mockReads({
      details: {
        "team-1": [
          assignment(),
          assignment({ id: "a2", user_id: "user-3", display_name: "Sara" }),
        ],
      },
    });

    renderPage();
    await findRow("Sales");
    fireEvent.click(screen.getByRole("button", { name: "Open team Sales" }));
    const drawer = await screen.findByRole("dialog", { name: "Sales" }, LOAD);

    // The first refusal leaves its reason inside the open confirmation.
    fireEvent.click(within(drawer).getByRole("button", { name: "Revoke Ali as Agent" }));
    let confirm = await screen.findByRole("alertdialog", undefined, LOAD);
    fireEvent.click(within(confirm).getByRole("button", { name: "Revoke" }));
    expect(await within(confirm).findByRole("alert", undefined, LOAD)).toBeInTheDocument();

    // Cancelling and asking about another row starts a clean confirmation.
    fireEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(), LOAD);
    fireEvent.click(within(drawer).getByRole("button", { name: "Revoke Sara as Agent" }));
    confirm = await screen.findByRole("alertdialog", undefined, LOAD);
    expect(within(confirm).queryByRole("alert")).not.toBeInTheDocument();
    expect(within(confirm).getByText(/Sara stops being agent of Sales now\./)).toBeInTheDocument();
  });

  it("assigns a member with no period, so no date keys are sent", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse(assignment()));
    mockReads();

    renderPage();
    await findRow("Sales");
    fireEvent.click(screen.getByRole("button", { name: "Open team Sales" }));
    const drawer = await screen.findByRole("dialog", { name: "Sales" }, LOAD);

    expect(within(drawer).getByRole("heading", { name: "Assign a member" })).toBeInTheDocument();
    // Nothing to send without a member.
    expect(within(drawer).getByRole("button", { name: "Assign" })).toBeDisabled();

    const combo = within(drawer).getByRole("combobox", { name: "Member" });
    const user = userEvent.setup();
    await user.click(combo);
    await user.click(await screen.findByRole("option", { name: /Ali/ }, LOAD));

    fireEvent.click(within(drawer).getByRole("button", { name: "Assign" }));
    await waitFor(
      () =>
        expect(post).toHaveBeenCalledWith("/teams/team-1/assignments", {
          user_id: "user-2",
          role: "agent",
        }),
      LOAD,
    );
    expect(await screen.findByText("Member assigned.", undefined, LOAD)).toBeInTheDocument();
    // The form resets: the role select falls back to Agent and the member is cleared.
    await waitFor(() => expect(within(drawer).getByRole("button", { name: "Assign" })).toBeDisabled(), LOAD);
  });

  it("describes each refused assign by its error code, keeping the choices", async () => {
    const codes: Array<[string, RegExp]> = [
      ["assignment_conflict", /This member already has this role in the team for an overlapping period\./],
      ["assignee_not_member", /This person is no longer an active member of the workspace\./],
      ["invalid_validity_window", /The end date must be after the start date\./],
    ];
    for (const [code, text] of codes) {
      cleanup();
      vi.restoreAllMocks();
      const post = vi.spyOn(api, "post").mockRejectedValue(apiError(422, code));
      mockReads();

      renderPage();
      await findRow("Sales");
      fireEvent.click(screen.getByRole("button", { name: "Open team Sales" }));
      const drawer = await screen.findByRole("dialog", { name: "Sales" }, LOAD);
      const user = userEvent.setup();
      await user.click(within(drawer).getByRole("combobox", { name: "Member" }));
      await user.click(await screen.findByRole("option", { name: /Ali/ }, LOAD));
      fireEvent.click(within(drawer).getByRole("button", { name: "Assign" }));

      expect(await within(drawer).findByRole("alert", undefined, LOAD)).toHaveTextContent(text);
      // The chosen member stays, so the alert can be read against it.
      expect(within(drawer).getByRole("combobox", { name: "Member" })).toHaveValue("Ali");
      expect(post).toHaveBeenCalledTimes(1);
    }
  });

  it("keeps Assign off and explains when the member list fails", async () => {
    mockReads({ members: apiError(400, "members_read_failed") });

    renderPage();
    await findRow("Sales");
    fireEvent.click(screen.getByRole("button", { name: "Open team Sales" }));
    const drawer = await screen.findByRole("dialog", { name: "Sales" }, LOAD);

    expect(await within(drawer).findByRole("alert", undefined, LOAD)).toHaveTextContent(
      "The member list could not be loaded.",
    );
    expect(within(drawer).getByRole("button", { name: "Assign" })).toBeDisabled();
  });

  it("says there are no teams yet, with the first-team hint for managers only", async () => {
    mockReads({ teams: [] });
    renderPage(workspace("owner"));
    expect(await screen.findByText("No teams yet.", undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByText("Create the first team to organize your people.")).toBeInTheDocument();
  });

  it("hides the first-team hint from a member", async () => {
    mockReads({ teams: [] });
    renderPage(workspace("member"));
    expect(await screen.findByText("No teams yet.", undefined, LOAD)).toBeInTheDocument();
    expect(screen.queryByText("Create the first team to organize your people.")).not.toBeInTheDocument();
  });

  it("describes a failed list read", async () => {
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/admin/workspace-settings") return okResponse(settings());
      throw apiError(400, "teams_read_failed");
    });
    renderPage();
    const alert = await screen.findByRole("alert", undefined, LOAD);
    expect(alert).toHaveTextContent("The teams could not be loaded.");
  });

  it("explains a missing team in the drawer", async () => {
    const get = vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/teams") return okResponse({ items: [team()], total: 1 });
      if (url === "/admin/workspace-settings") return okResponse(settings());
      throw apiError(404, "team_not_found");
    });
    renderPage();
    await findRow("Sales");

    fireEvent.click(screen.getByRole("button", { name: "Open team Sales" }));
    const drawer = await screen.findByRole("dialog", { name: "Sales" }, LOAD);
    expect(await within(drawer).findByRole("alert", undefined, LOAD)).toHaveTextContent(
      "This team no longer exists.",
    );
    expect(get).toHaveBeenCalled();
  });

  it("gives owners and administrators every control and exactly the allowed reads", async () => {
    for (const role of ["owner", "admin"] as const) {
      const get = mockReads({ details: { "team-1": [assignment()] } });
      renderPage(workspace(role));

      await findRow("Sales");
      expect(screen.getByRole("button", { name: "Create team" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Rename team Sales" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Deactivate team Sales" })).toBeInTheDocument();
      expect(screen.getByText(/Record access: whole workspace\./)).toBeInTheDocument();

      const urls = requestedUrls(get);
      expect(urls).toContain("/teams");
      expect(urls).toContain("/teams/team-1");
      expect(urls).toContain("/admin/workspace-settings");
      // The member list waits for an open drawer.
      expect(urls).not.toContain("/members");

      await findRow("Sales");
      fireEvent.click(screen.getByRole("button", { name: "Open team Sales" }));
      const drawer = await screen.findByRole("dialog", { name: "Sales" }, LOAD);
      await waitFor(() => expect(requestedUrls(get)).toContain("/members"), LOAD);
      expect(within(drawer).getByRole("button", { name: "Assign" })).toBeInTheDocument();
      cleanup();
      vi.restoreAllMocks();
    }
  });

  it("keeps members and viewers read only, with no extra requests and no writes", async () => {
    for (const role of ["member", "viewer"] as const) {
      const post = vi.spyOn(api, "post").mockImplementation(async () => {
        throw new Error("no write may happen");
      });
      const patch = vi.spyOn(api, "patch").mockImplementation(async () => {
        throw new Error("no write may happen");
      });
      const remove = vi.spyOn(api, "delete").mockImplementation(async () => {
        throw new Error("no write may happen");
      });
      const get = mockReads({ details: { "team-1": [assignment()] } });
      renderPage(workspace(role));

      await findRow("Sales");
      expect(screen.getByRole("button", { name: "Open team Sales" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Create team" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Rename team Sales" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Deactivate team Sales" })).not.toBeInTheDocument();
      expect(screen.queryByText(/Record access:/)).not.toBeInTheDocument();
      const urls = requestedUrls(get);
      expect(urls).not.toContain("/admin/workspace-settings");
      expect(urls).not.toContain("/members");

      await findRow("Sales");
      fireEvent.click(screen.getByRole("button", { name: "Open team Sales" }));
      const drawer = await screen.findByRole("dialog", { name: "Sales" }, LOAD);
      const table = within(drawer).getByRole("grid", { name: "Team assignments" });
      expect(within(table).queryByRole("columnheader", { name: "Actions" })).not.toBeInTheDocument();
      expect(within(drawer).queryByRole("heading", { name: "Assign a member" })).not.toBeInTheDocument();
      expect(within(drawer).queryByRole("button", { name: "Assign" })).not.toBeInTheDocument();
      expect(within(drawer).queryByRole("button", { name: "Revoke Ali as Agent" })).not.toBeInTheDocument();
      // Opening the drawer added no member read either.
      expect(requestedUrls(get)).not.toContain("/members");
      expect(post).not.toHaveBeenCalled();
      expect(patch).not.toHaveBeenCalled();
      expect(remove).not.toHaveBeenCalled();
      cleanup();
      vi.restoreAllMocks();
    }
  });

  it("treats a present permissions array as the only source for team:manage", async () => {
    mockReads();
    renderPage(workspace("owner", ["team:read", "workspace:manage"]));
    await findRow("Sales");

    expect(screen.queryByRole("button", { name: "Create team" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rename team Sales" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Deactivate team Sales" })).not.toBeInTheDocument();
    // The owner without a permissions field saw them all in the tests above.
  });

  it("mirrors the page in Persian", async () => {
    mockReads();
    renderPage(workspace("owner"), "fa");

    expect(await screen.findByRole("heading", { level: 1, name: "تیم‌ها" }, LOAD)).toBeInTheDocument();
    expect(screen.getByText("افراد را در تیم‌ها گروه‌بندی کنید و به آن‌ها نقش سرپرست یا کارشناس بدهید.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ساخت تیم" })).toBeInTheDocument();
    expect(await screen.findByText(/دسترسی به رکوردها: کل فضای کاری\./, undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "تغییر در تنظیمات" })).toHaveAttribute("href", "/admin/settings");
    expect(await findRow("Sales")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "باز کردن تیم Sales" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "تغییر نام تیم Sales" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "غیرفعال کردن تیم Sales" })).toBeInTheDocument();
    expect(screen.getByText("فعال")).toBeInTheDocument();
  });
});
