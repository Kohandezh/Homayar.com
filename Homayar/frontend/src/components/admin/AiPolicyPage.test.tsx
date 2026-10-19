import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AiPolicyPage from "@/components/admin/AiPolicyPage";
import api from "@/lib/api";
import { aiPolicyQueryKeys } from "@/lib/ai-policy-api";
import { formatDate } from "@/lib/format";
import {
  FIXTURE_USER,
  FIXTURE_WORKSPACE,
  apiError,
  okResponse,
  renderConsole,
} from "@/test/fixtures";
import type { WorkspaceRole } from "@/types/auth";
import type { MemberListResponse } from "@/types/platform";
import type {
  AiPolicy,
  AiPolicyResponse,
  AiPolicyUpdateInput,
  AiPolicyVersionListResponse,
} from "@/types/ai-policy";

const T = "2026-10-10T10:00:00Z";
// The page waits for several queries; slower machines need more than the 1 s default.
const LOAD = { timeout: 5000 };

function stagePolicy(overrides: Partial<AiPolicy["asr"]> = {}): AiPolicy["asr"] {
  return {
    endpoint_kinds: ["in_process", "private"],
    regions: [],
    manifests: [],
    fallback: false,
    ...overrides,
  };
}

/** The demo default: every stage in-process and private, redacted cloud text. */
function makePolicy(overrides: Partial<AiPolicy> = {}): AiPolicy {
  return {
    asr: stagePolicy(),
    diarization: stagePolicy(),
    intelligence: { ...stagePolicy(), cloud_text: "redacted" },
    ...overrides,
  };
}

function localOnlyPolicy(): AiPolicy {
  return makePolicy({
    asr: stagePolicy({ endpoint_kinds: ["in_process"] }),
    diarization: stagePolicy({ endpoint_kinds: ["in_process"] }),
    intelligence: { ...stagePolicy({ endpoint_kinds: ["in_process"] }), cloud_text: "redacted" },
  });
}

/**
 * Fixture presets that differ from the backend's, so the test proves the form
 * is filled from the versions read and not from a hard-coded document.
 */
function presetLocalOnly(): AiPolicy {
  return makePolicy({
    asr: stagePolicy({ endpoint_kinds: ["in_process"], fallback: true }),
    diarization: stagePolicy({ endpoint_kinds: ["in_process"] }),
    intelligence: { ...stagePolicy({ endpoint_kinds: ["in_process"] }), cloud_text: "original" },
  });
}

function presetPrivate(): AiPolicy {
  return makePolicy({
    diarization: stagePolicy({ fallback: true, manifests: ["diar-v2"] }),
  });
}

function makeCurrent(overrides: Partial<AiPolicyResponse> = {}): AiPolicyResponse {
  return {
    version: 0,
    source: "default",
    policy_schema: "homayar.ai-policy/1",
    digest: "digest-0",
    policy: makePolicy(),
    reason: null,
    created_by_user_id: null,
    created_at: null,
    ...overrides,
  };
}

function makeVersions(overrides: Partial<AiPolicyVersionListResponse> = {}): AiPolicyVersionListResponse {
  return {
    current_version: 0,
    items: [],
    presets: { local_only: presetLocalOnly(), private: presetPrivate() },
    ...overrides,
  };
}

const MEMBERS: MemberListResponse = {
  items: [
    {
      id: "membership-1",
      user_id: "user-1",
      email: "owner@demo.example.com",
      phone_e164: null,
      display_name: "Sara Ahmadi",
      role: "owner",
      joined_at: T,
    },
  ],
  total: 1,
};

/**
 * Answers the four requests of the page from mutable state, so a test can
 * change the world between calls (a concurrent writer, a failing read).
 * A save appends the new version, the way the server does.
 */
function mockAiPolicyApi() {
  const state = {
    current: makeCurrent(),
    versions: makeVersions(),
    members: MEMBERS,
    putError: null as unknown,
    putChanged: true,
    currentError: false,
    versionsError: false,
    membersError: false,
  };
  const puts: AiPolicyUpdateInput[] = [];
  vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url === "/admin/ai-policy") {
      if (state.currentError) throw apiError(500, "internal_error");
      return okResponse(state.current);
    }
    if (url === "/admin/ai-policy/versions") {
      if (state.versionsError) throw apiError(500, "internal_error");
      return okResponse(state.versions);
    }
    if (url === "/members") {
      if (state.membersError) throw apiError(500, "internal_error");
      return okResponse(state.members);
    }
    throw apiError(404, "not_found");
  });
  vi.spyOn(api, "put").mockImplementation(async (_url: string, data: unknown) => {
    const body = data as AiPolicyUpdateInput;
    puts.push(body);
    if (state.putError) throw state.putError;
    if (!state.putChanged) return okResponse({ changed: false, current: state.current });
    const current: AiPolicyResponse = {
      ...state.current,
      version: state.current.version + 1,
      source: "workspace",
      digest: `digest-${state.current.version + 1}`,
      policy: body.policy,
      reason: body.reason,
      created_by_user_id: "user-1",
      created_at: T,
    };
    state.current = current;
    state.versions = {
      ...state.versions,
      current_version: current.version,
      items: [current, ...state.versions.items],
    };
    return okResponse({ changed: true, current });
  });
  return { state, puts };
}

function renderPage(role: WorkspaceRole = "owner", lang: "fa" | "en" = "en") {
  const workspace = { ...FIXTURE_WORKSPACE, role };
  return renderConsole(<AiPolicyPage />, {
    route: "/admin/ai-policy",
    lang,
    auth: {
      status: "authenticated",
      user: FIXTURE_USER,
      workspace,
      workspaces: [workspace],
    },
  });
}

async function openEditForm() {
  fireEvent.click(await screen.findByRole("button", { name: "Edit policy" }, LOAD));
  await screen.findByRole("group", { name: "Presets" }, LOAD);
  // The preset buttons wait for the versions read; a click before it lands is lost.
  await waitFor(
    () => expect(screen.getByRole("button", { name: "Local only" })).toBeEnabled(),
    LOAD,
  );
}

/** The stage fieldset of the edit form, named by its legend. */
function stageGroup(name: string) {
  return screen.getByRole("group", { name });
}

const ASR = "Speech to text";
const SPEAKER = "Speaker separation";
const INTELLIGENCE = "Summary and analysis";

describe("AiPolicyPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("shows the three stages of the default policy with the built-in badge", async () => {
    mockAiPolicyApi();
    renderPage();

    // The stage headings only exist once the policy read has landed.
    await screen.findByRole("heading", { level: 2, name: ASR}, LOAD);
    expect(screen.getByRole("heading", { level: 1, name: "AI policy" })).toBeInTheDocument();
    expect(screen.getByText("Built-in default")).toBeInTheDocument();
    expect(screen.queryByText(/Custom, version/)).not.toBeInTheDocument();

    for (const name of [ASR, "Speaker separation", INTELLIGENCE]) {
      expect(screen.getByRole("heading", { level: 2, name})).toBeInTheDocument();
    }
    // Two stages on one card would find each twice, so the exact count is part of the check.
    expect(screen.getAllByText("On your servers only")).toHaveLength(3);
    expect(screen.getAllByText("Private endpoint")).toHaveLength(3);
    expect(screen.queryByText("Cloud provider")).not.toBeInTheDocument();
    expect(screen.queryByText("Turned off")).not.toBeInTheDocument();
    expect(screen.getAllByText("Fallback: not allowed")).toHaveLength(3);
    expect(screen.getAllByText("Any approved model")).toHaveLength(3);
    expect(screen.getByText("Cloud providers receive redacted text")).toBeInTheDocument();
  });

  it("shows a custom version with its reason, date and version badge", async () => {
    const { state } = mockAiPolicyApi();
    state.current = makeCurrent({
      version: 3,
      source: "workspace",
      reason: "move processing to private endpoints only",
      created_by_user_id: "user-1",
      created_at: T,
    });
    renderPage();

    expect(await screen.findByText("Custom, version 3", undefined, LOAD)).toBeInTheDocument();
    expect(screen.queryByText("Built-in default")).not.toBeInTheDocument();
    expect(screen.getByText("move processing to private endpoints only")).toBeInTheDocument();
    expect(screen.getByText(formatDate(T, "en"))).toBeInTheDocument();
  });

  it("shows cloud regions and manifest ids left to right", async () => {
    const { state } = mockAiPolicyApi();
    state.current = makeCurrent({
      source: "workspace",
      policy: makePolicy({
        asr: stagePolicy({
          endpoint_kinds: ["cloud", "in_process"],
          regions: ["eu-west-1", "me-central-1"],
          manifests: ["asr-v1"],
        }),
      }),
    });
    renderPage();

    await screen.findByRole("heading", { level: 2, name: ASR }, LOAD);
    for (const id of ["eu-west-1", "me-central-1", "asr-v1"]) {
      const element = screen.getByText(id);
      expect(element.closest("bdi")).toHaveAttribute("dir", "ltr");
    }
    // The regions sit next to the kind with a colon, not an em dash.
    const cloudLine = screen.getByText(/^Cloud provider:/);
    expect(cloudLine).toHaveTextContent(/^Cloud provider:/);
    expect(cloudLine.textContent).not.toContain("—");
    // The asr stage shows its manifest instead; the other two keep the "any" line.
    expect(screen.getAllByText("Any approved model")).toHaveLength(2);
  });

  it("keeps the edit form for the owner and throws the draft away on cancel", async () => {
    const user = userEvent.setup();
    mockAiPolicyApi();
    renderPage();

    await openEditForm();
    expect(screen.getByRole("button", { name: "Local only" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Private" })).toBeEnabled();
    expect(screen.getByRole("textbox", { name: "Reason for the change" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save policy" })).toBeDisabled();

    await user.click(within(stageGroup(ASR)).getByRole("checkbox", { name: "Cloud provider" }));
    await user.type(screen.getByRole("textbox", { name: "Reason for the change" }), "try cloud");

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("group", { name: "Presets" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save policy" })).not.toBeInTheDocument();
    // The read view still shows the untouched policy.
    expect(screen.queryByText("Cloud provider")).not.toBeInTheDocument();
  });

  it("hides the edit controls from an admin and explains why", async () => {
    mockAiPolicyApi();
    renderPage("admin");

    await screen.findByRole("heading", { level: 2, name: ASR}, LOAD);
    for (const name of [ASR, "Speaker separation", INTELLIGENCE]) {
      expect(screen.getByRole("heading", { level: 2, name})).toBeInTheDocument();
    }
    expect(screen.getByText("Only the workspace owner can change the AI policy.")).toBeInTheDocument();
    for (const gone of ["Edit policy", "Save policy", "Local only", "Presets"]) {
      expect(screen.queryByRole("button", { name: gone })).not.toBeInTheDocument();
    }
    expect(screen.queryByRole("group", { name: "Presets" })).not.toBeInTheDocument();
    expect(screen.getByText("No saved versions yet. The built-in default is in use.")).toBeInTheDocument();
  });

  it("fills the form from a preset and sends the exact PUT body", async () => {
    const user = userEvent.setup();
    const { state, puts } = mockAiPolicyApi();
    renderPage();

    await openEditForm();
    // The presets come from GET /admin/ai-policy/versions; the fixture values
    // differ from the backend's, so only a real read fills the form with them.
    expect(state.versions.presets.local_only).toEqual(presetLocalOnly());
    await user.click(screen.getByRole("button", { name: "Local only" }));

    const asr = stageGroup(ASR);
    expect(within(asr).getByRole("checkbox", { name: "On your servers only" })).toBeChecked();
    expect(within(asr).getByRole("checkbox", { name: "Private endpoint" })).not.toBeChecked();
    expect(within(asr).getByRole("switch", { name: "Allow fallback" })).toBeChecked();
    const intelligence = stageGroup(INTELLIGENCE);
    expect(within(intelligence).getByRole("radio", { name: "Original text" })).toBeChecked();

    await user.click(screen.getByRole("button", { name: "Private" }));
    expect(within(stageGroup(SPEAKER)).getByRole("switch", { name: "Allow fallback" })).toBeChecked();
    expect(within(asr).getByRole("switch", { name: "Allow fallback" })).not.toBeChecked();
    expect(within(intelligence).getByRole("radio", { name: "Redacted text" })).toBeChecked();
    expect(within(stageGroup(SPEAKER)).getByRole("checkbox", { name: "Private endpoint" })).toBeChecked();

    await user.click(screen.getByRole("button", { name: "Local only" }));
    await user.type(screen.getByRole("textbox", { name: "Reason for the change" }), "  local only  ");
    await user.click(screen.getByRole("button", { name: "Save policy" }));

    await waitFor(() => expect(puts).toHaveLength(1), LOAD);
    expect(puts[0]).toEqual({
      policy: presetLocalOnly(),
      expected_version: 0,
      reason: "local only",
    });

    const status = await screen.findByText("AI policy saved as version 1.", undefined, LOAD);
    expect(status).toHaveAttribute("role", "status");
    expect(screen.queryByRole("group", { name: "Presets" })).not.toBeInTheDocument();
    expect(screen.getByText("Custom, version 1")).toBeInTheDocument();

    // The history refresh shows the new version with its author.
    const history = await screen.findByRole("grid", { name: "Version history" }, LOAD);
    const row = within(history).getByRole("row", { name: /^1/ });
    expect(within(row).getByText("local only")).toBeInTheDocument();
    expect(within(row).getByText("Sara Ahmadi")).toBeInTheDocument();
    expect(within(row).getByText(formatDate(T, "en"))).toBeInTheDocument();
  }, 15_000);

  it("sends a preset document with its manifests unchanged", async () => {
    const user = userEvent.setup();
    const { puts } = mockAiPolicyApi();
    renderPage();

    await openEditForm();
    // The Private fixture differs from the current policy only in fields the
    // form does not edit, so the saved document must carry them as the read
    // sent them (fallback on, one manifest).
    await user.click(screen.getByRole("button", { name: "Private" }));
    await user.type(screen.getByRole("textbox", { name: "Reason for the change" }), "private with fallback");
    await user.click(screen.getByRole("button", { name: "Save policy" }));

    await waitFor(() => expect(puts).toHaveLength(1), LOAD);
    expect(puts[0]).toEqual({
      policy: presetPrivate(),
      expected_version: 0,
      reason: "private with fallback",
    });
  }, 15_000);

  it("keeps Save policy disabled until the policy itself changes", async () => {
    const user = userEvent.setup();
    mockAiPolicyApi();
    renderPage();

    await openEditForm();
    const save = screen.getByRole("button", { name: "Save policy" });
    expect(save).toBeDisabled();
    // A reason alone is not a change.
    await user.type(screen.getByRole("textbox", { name: "Reason for the change" }), "a reason");
    expect(save).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Local only" }));
    await waitFor(() => expect(save).toBeEnabled());
  });

  it("shows the validation errors in the failing stage and sends nothing", async () => {
    const user = userEvent.setup();
    const { puts } = mockAiPolicyApi();
    renderPage();

    await openEditForm();
    const asr = stageGroup(ASR);
    await user.click(within(asr).getByRole("checkbox", { name: "Cloud provider" }));
    await user.click(screen.getByRole("button", { name: "Save policy" }));

    expect(
      await screen.findByText("Add at least one region to allow a cloud provider.", undefined, LOAD),
    ).toBeInTheDocument();
    expect(screen.getByText("Write a reason of at least 3 characters.")).toBeInTheDocument();
    expect(puts).toHaveLength(0);

    await user.type(within(asr).getByRole("textbox", { name: "Regions" }), "EU-west-1");
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    expect(
      screen.getByText("Use lowercase letters, digits and dashes for a region, for example eu-west-1."),
    ).toBeInTheDocument();
    expect(puts).toHaveLength(0);

    // Allow-controls: a valid region under cloud, and a stage with only
    // in_process and no region; both go into one save.
    await user.clear(within(asr).getByRole("textbox", { name: "Regions" }));
    await user.type(within(asr).getByRole("textbox", { name: "Regions" }), "eu-west-1");
    await user.type(screen.getByRole("textbox", { name: "Reason for the change" }), "allow eu cloud");
    expect(screen.queryByText("Add at least one region to allow a cloud provider.")).not.toBeInTheDocument();
    expect(screen.queryByText("Use lowercase letters, digits and dashes for a region, for example eu-west-1.")).not.toBeInTheDocument();
    const diarization = stageGroup(SPEAKER);
    await user.click(within(diarization).getByRole("checkbox", { name: "Private endpoint" }));
    await user.click(screen.getByRole("button", { name: "Save policy" }));

    await waitFor(() => expect(puts).toHaveLength(1), LOAD);
    expect(puts[0]).toEqual({
      policy: makePolicy({
        asr: stagePolicy({ endpoint_kinds: ["cloud", "in_process", "private"], regions: ["eu-west-1"] }),
        diarization: stagePolicy({ endpoint_kinds: ["in_process"] }),
      }),
      expected_version: 0,
      reason: "allow eu cloud",
    });
    expect(await screen.findByText("AI policy saved as version 1.", undefined, LOAD)).toBeInTheDocument();
  }, 15_000);

  it("keeps the draft through a version conflict and saves against the reloaded policy", async () => {
    const user = userEvent.setup();
    const { state, puts } = mockAiPolicyApi();
    renderPage();

    await openEditForm();
    await user.click(screen.getByRole("button", { name: "Local only" }));
    await user.type(screen.getByRole("textbox", { name: "Reason for the change" }), "local only please");
    state.putError = apiError(409, "policy_version_conflict", { details: { current_version: 1 } });
    await user.click(screen.getByRole("button", { name: "Save policy" }));

    const alert = await screen.findByRole("alert", undefined, LOAD);
    expect(alert).toHaveTextContent("Someone else changed the AI policy while you were editing.");
    expect(screen.getByRole("button", { name: "Reload policy" })).toBeInTheDocument();
    // The draft stays visible until the reload.
    expect(within(stageGroup(ASR)).getByRole("checkbox", { name: "Private endpoint" })).not.toBeChecked();

    // Someone else saved the same change while the form was open.
    state.putError = null;
    state.current = makeCurrent({
      version: 1,
      source: "workspace",
      policy: localOnlyPolicy(),
      reason: "someone else was faster",
      created_by_user_id: "user-1",
      created_at: T,
    });
    state.versions = makeVersions({
      current_version: 1,
      items: [state.current],
    });

    await user.click(screen.getByRole("button", { name: "Reload policy" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument(), LOAD);
    // The form is refilled from the new current policy; the typed reason stays.
    expect(screen.getByRole("textbox", { name: "Reason for the change" })).toHaveValue("local only please");
    expect(within(stageGroup(ASR)).getByRole("checkbox", { name: "Private endpoint" })).not.toBeChecked();
    // Equal to the reloaded policy, so the next save needs a real change.
    expect(screen.getByRole("button", { name: "Save policy" })).toBeDisabled();

    await user.click(within(stageGroup(ASR)).getByRole("checkbox", { name: "Private endpoint" }));
    await user.click(screen.getByRole("button", { name: "Save policy" }));

    await waitFor(() => expect(puts).toHaveLength(2), LOAD);
    expect(puts[1]?.expected_version).toBe(1);
    expect(await screen.findByText("AI policy saved as version 2.", undefined, LOAD)).toBeInTheDocument();
  }, 20_000);

  it("keeps the edit form when a background refetch of the policy fails", async () => {
    const user = userEvent.setup();
    const { state } = mockAiPolicyApi();
    const { queryClient } = renderPage();

    await openEditForm();
    await user.click(within(stageGroup(ASR)).getByRole("checkbox", { name: "Cloud provider" }));

    // A failed refetch keeps the cached data; only a read with no data at all
    // may swap the page for the error state. The query state is the proof the
    // error really landed: React Query delivers it one tick after the awaited
    // invalidateQueries returns.
    state.currentError = true;
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: aiPolicyQueryKeys.current() });
    });
    await waitFor(() =>
      expect(queryClient.getQueryState(aiPolicyQueryKeys.current())?.status).toBe("error"),
    );

    expect(screen.getByRole("group", { name: "Presets" })).toBeInTheDocument();
    expect(within(stageGroup(ASR)).getByRole("checkbox", { name: "Cloud provider" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Save policy" })).toBeInTheDocument();
  });

  it("keeps the draft and the old version when Reload policy fails", async () => {
    const user = userEvent.setup();
    const { state, puts } = mockAiPolicyApi();
    renderPage();

    await openEditForm();
    await user.click(screen.getByRole("button", { name: "Local only" }));
    await user.type(screen.getByRole("textbox", { name: "Reason for the change" }), "still mine");
    state.putError = apiError(409, "policy_version_conflict", { details: { current_version: 1 } });
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    expect(
      await screen.findByText("Someone else changed the AI policy while you were editing.", undefined, LOAD),
    ).toBeInTheDocument();

    // The reload read fails: the alert stays, the draft stays, and an error
    // appears next to the alert.
    state.currentError = true;
    state.putError = null;
    await user.click(screen.getByRole("button", { name: "Reload policy" }));
    expect(await screen.findByText(/not available right now/, undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByText("Someone else changed the AI policy while you were editing.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Reason for the change" })).toHaveValue("still mine");
    expect(within(stageGroup(ASR)).getByRole("checkbox", { name: "Private endpoint" })).not.toBeChecked();

    // The next save still targets the version the form was opened from.
    state.currentError = false;
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    await waitFor(() => expect(puts).toHaveLength(2), LOAD);
    expect(puts[1]?.expected_version).toBe(0);
  }, 20_000);

  it("explains a refused save without the server's own words", async () => {
    const user = userEvent.setup();
    const { state } = mockAiPolicyApi();
    renderPage();

    await openEditForm();
    await user.click(screen.getByRole("button", { name: "Local only" }));
    await user.type(screen.getByRole("textbox", { name: "Reason for the change" }), "reason");

    state.putError = apiError(403, "permission_denied");
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    expect(await screen.findByText("Only the workspace owner can change the AI policy.", undefined, LOAD)).toBeInTheDocument();

    // A 403 from the session or the workspace switch is not about ownership.
    state.putError = apiError(403, "csrf_failed");
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    expect(
      await screen.findByText("Your session needs refreshing; reload the page.", undefined, LOAD),
    ).toBeInTheDocument();
    expect(screen.queryByText("Only the workspace owner can change the AI policy.")).not.toBeInTheDocument();

    state.putError = apiError(422, "validation_error");
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    expect(
      await screen.findByText("The server did not accept this policy. Check the values and try again.", undefined, LOAD),
    ).toBeInTheDocument();

    state.putError = apiError(500, "internal_error");
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    // Any other failure gets the helper's generic text by kind, never the server's words.
    const generic = await screen.findByText(/not available right now/, undefined, LOAD);
    expect(generic).not.toHaveTextContent("internal_error");
    expect(generic).not.toHaveTextContent("fixture internal_error");
  }, 20_000);

  it("reports a save that changed nothing and keeps the form open", async () => {
    const user = userEvent.setup();
    const { state } = mockAiPolicyApi();
    state.putChanged = false;
    renderPage();

    await openEditForm();
    await user.click(within(stageGroup(ASR)).getByRole("switch", { name: "Allow fallback" }));
    await user.type(screen.getByRole("textbox", { name: "Reason for the change" }), "fallback on");
    await user.click(screen.getByRole("button", { name: "Save policy" }));

    expect(await screen.findByText("Nothing changed. The policy is already like this.", undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Presets" })).toBeInTheDocument();
  }, 15_000);

  it("compares a version with the current policy field by field", async () => {
    const user = userEvent.setup();
    const { state } = mockAiPolicyApi();
    const current = makeCurrent({
      version: 2,
      source: "workspace",
      reason: "local only",
      created_by_user_id: "user-1",
      created_at: T,
      policy: localOnlyPolicy(),
    });
    const older = makeCurrent({
      version: 1,
      source: "workspace",
      reason: "allow eu cloud",
      created_by_user_id: "user-1",
      created_at: T,
      policy: makePolicy({
        asr: stagePolicy({
          endpoint_kinds: ["cloud", "in_process"],
          regions: ["eu-west-1"],
          fallback: true,
        }),
        intelligence: { ...stagePolicy({ manifests: ["intel-9"] }), cloud_text: "original" },
      }),
    });
    state.current = current;
    state.versions = makeVersions({ current_version: 2, items: [current, older] });
    renderPage();

    const history = await screen.findByRole("grid", { name: "Version history" }, LOAD);
    expect(within(history).getAllByRole("row")[1]).toHaveTextContent("2");
    expect(within(history).getAllByRole("row")[2]).toHaveTextContent("1");

    await user.click(within(history).getByRole("button", { name: "Compare version 1" }));
    expect(
      screen.getByRole("heading", { name: "Version 1 compared with the current policy" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Version 1 is the same as the current policy.")).not.toBeInTheDocument();
    const asrRow = screen.getByText("On your servers only, Cloud provider").closest("li");
    expect(asrRow).not.toBeNull();
    expect(asrRow).toHaveTextContent("Speech to text");
    expect(asrRow).toHaveTextContent("Where it may run");
    expect(within(asrRow as HTMLElement).getByText("On your servers only")).toBeInTheDocument();
    const fallbackRow = screen.getByText("Fallback", { exact: true }).closest("li");
    expect(fallbackRow).not.toBeNull();
    expect(within(fallbackRow as HTMLElement).getByText("allowed", { exact: true })).toBeInTheDocument();
    expect(within(fallbackRow as HTMLElement).getByText("not allowed", { exact: true })).toBeInTheDocument();
    const regionsRow = screen.getByText("eu-west-1").closest("li");
    expect(within(regionsRow as HTMLElement).getByText("—")).toBeInTheDocument();
    // An empty model list reads like the read view, not as a dash.
    const manifestsRow = screen.getByText("intel-9").closest("li");
    expect(within(manifestsRow as HTMLElement).getByText("Any approved model")).toBeInTheDocument();
    expect(screen.getByText("original", { exact: true })).toBeInTheDocument();

    // The current version equals itself.
    await user.click(within(history).getByRole("button", { name: "Compare version 2" }));
    expect(screen.getByText("Version 2 is the same as the current policy.")).toBeInTheDocument();
  });

  it("shows the fallback dash for an unknown author and survives a members failure", async () => {
    const { state } = mockAiPolicyApi();
    state.current = makeCurrent({
      version: 1,
      source: "workspace",
      reason: "saved once",
      created_by_user_id: "user-9",
      created_at: T,
    });
    state.versions = makeVersions({ current_version: 1, items: [state.current] });
    state.membersError = true;
    renderPage();

    const history = await screen.findByRole("grid", { name: "Version history" }, LOAD);
    // The author is unknown, not missing: the fallback dash, never the raw user id.
    expect(within(history).getAllByRole("row")[1]).toHaveTextContent("—");
    expect(within(history).getAllByRole("row")[1]).not.toHaveTextContent("user-9");
  });

  it("shows its own error for a failed history read, with the read view standing", async () => {
    const { state } = mockAiPolicyApi();
    state.versionsError = true;
    renderPage();

    // A 500 is described by its kind, so only the alert itself is asserted.
    const alert = await screen.findByRole("alert", undefined, LOAD);
    expect(alert).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: ASR})).toBeInTheDocument();
  });

  it("tries the policy again after a failed read", async () => {
    const { state } = mockAiPolicyApi();
    state.currentError = true;
    renderPage();

    expect(await screen.findByRole("alert", undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();

    state.currentError = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { level: 2, name: ASR }, LOAD)).toBeInTheDocument();
  });

  it("renders in Persian, right to left", async () => {
    mockAiPolicyApi();
    renderPage("owner", "fa");

    expect(
      await screen.findByRole("heading", { level: 2, name: "تفکیک گوینده‌ها"}, LOAD),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "سیاست هوش مصنوعی" })).toBeInTheDocument();
    expect(document.documentElement.dir).toBe("rtl");
    expect(screen.getByText("پیش‌فرض داخلی")).toBeInTheDocument();
    expect(screen.getByText("ارائه‌دهنده‌های ابری متن پنهان‌شده دریافت می‌کنند")).toBeInTheDocument();
    // The default policy has no stored versions, so the history stays empty here.
    expect(screen.queryByRole("grid", { name: "تاریخچهٔ نسخه‌ها" })).not.toBeInTheDocument();
    expect(screen.getByText("هنوز نسخه‌ای ذخیره نشده است. سیاست پیش‌فرض داخلی در حال استفاده است.")).toBeInTheDocument();
  });
});
