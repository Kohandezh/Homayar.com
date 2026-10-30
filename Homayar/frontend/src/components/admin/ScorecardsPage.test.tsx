import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router";
import ScorecardsPage from "@/components/admin/ScorecardsPage";
import api from "@/lib/api";
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
  ScorecardDefinition,
  ScorecardItem,
  ScorecardPublishInput,
  ScorecardVersionItem,
  ScorecardVersionResponse,
} from "@/types/scorecards";

const T = "2026-10-10T10:00:00Z";
// The page waits for several queries; slower machines need more than the 1 s default.
const LOAD = { timeout: 5000 };

/** A stored definition that uses every field the form hides. */
function supportDefinition(overrides: Partial<ScorecardDefinition> = {}): ScorecardDefinition {
  return {
    name: "Inbound support",
    description: null,
    pass_threshold: 75,
    criteria: [
      {
        criterion_id: "greeting",
        label: "Greets the caller",
        weight: 60,
        auto_fail: false,
        na_allowed: false,
        applies_to: null,
        check: {
          check_id: "greeting",
          version: 3,
          kind: "required_phrase",
          phrases: ["hello", "good morning"],
          model_fallback: true,
          critical: true,
          speaker_role: "agent",
          scope: { kind: "opening", max_turns: 4 },
        },
      },
      {
        criterion_id: "consent",
        label: "Asks for recording consent",
        weight: 40,
        auto_fail: true,
        na_allowed: true,
        applies_to: { conversation_types: ["call"], directions: ["inbound"] },
        check: {
          check_id: "consent",
          version: 2,
          kind: "contextual",
          question: "Did the agent ask for consent?",
          trigger_phrases: ["record"],
          context_turns: 3,
          speaker_role: "any",
          scope: { kind: "whole_call" },
        },
      },
    ],
    ...overrides,
  };
}

function stored(
  key: string,
  version: number,
  definition: ScorecardDefinition,
  reason = `reason ${version}`,
): ScorecardVersionResponse {
  return {
    scorecard_key: key,
    version,
    name: definition.name,
    digest: `digest-${key}-${version}`,
    scorecards_version: "scorecards/1",
    created_at: T,
    created_by_user_id: "user-1",
    reason,
    definition,
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

/** GET /scorecards rows carry no reason and no definition. */
function itemOf(response: ScorecardVersionResponse): ScorecardItem {
  return {
    scorecard_key: response.scorecard_key,
    version: response.version,
    name: response.name,
    digest: response.digest,
    scorecards_version: response.scorecards_version,
    created_at: response.created_at,
    created_by_user_id: response.created_by_user_id,
  };
}

/** GET /scorecards/{key}/versions rows carry the reason, not the definition. */
function versionItemOf(response: ScorecardVersionResponse): ScorecardVersionItem {
  return { ...itemOf(response), reason: response.reason };
}

/**
 * Answers the page's requests from mutable state, newest version first per
 * key. A publish appends a version, or answers 409 when expected_version is
 * not the current one, the way the server does.
 */
function mockScorecardApi() {
  const state = {
    scorecards: new Map<string, ScorecardVersionResponse[]>([
      [
        "inbound_support",
        [
          stored("inbound_support", 2, supportDefinition(), "tighten the greeting"),
          stored("inbound_support", 1, supportDefinition({ name: "Support v1", pass_threshold: 70 }), "first version"),
        ],
      ],
      ["sales_calls", [stored("sales_calls", 1, supportDefinition({ name: "Sales calls" }))]],
    ]),
    listError: false,
    versionReadError: false,
    validateError: null as unknown,
    publishError: null as unknown,
  };
  const posts: { url: string; body: unknown }[] = [];
  const get = vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url === "/scorecards") {
      if (state.listError) throw apiError(500, "internal_error");
      const items = [...state.scorecards.keys()].sort().map((key) => itemOf(state.scorecards.get(key)![0]));
      return okResponse({ items });
    }
    if (url === "/members") return okResponse(MEMBERS);
    const versions = /^\/scorecards\/([^/]+)\/versions$/.exec(url);
    if (versions) {
      const list = state.scorecards.get(versions[1]);
      if (!list) throw apiError(404, "scorecard_not_found");
      return okResponse({ items: list.map(versionItemOf) });
    }
    const one = /^\/scorecards\/([^/]+)\/versions\/(\d+)$/.exec(url);
    if (one) {
      if (state.versionReadError) throw apiError(500, "internal_error");
      const found = state.scorecards.get(one[1])?.find((entry) => entry.version === Number(one[2]));
      if (!found) throw apiError(404, "scorecard_version_not_found");
      return okResponse(found);
    }
    throw apiError(404, "not_found");
  });
  const post = vi.spyOn(api, "post").mockImplementation(async (url: string, data: unknown) => {
    posts.push({ url, body: data });
    if (url === "/scorecards/validate") {
      if (state.validateError) throw state.validateError;
      const { definition } = data as { definition: ScorecardDefinition };
      return okResponse({ valid: true, digest: "digest-check", definition });
    }
    const publish = /^\/scorecards\/([^/]+)\/versions$/.exec(url);
    if (publish) {
      if (state.publishError) throw state.publishError;
      const body = data as ScorecardPublishInput;
      const list = state.scorecards.get(publish[1]) ?? [];
      const current = list[0]?.version ?? 0;
      if (body.expected_version !== current) {
        throw apiError(409, "scorecard_version_conflict", { details: { current_version: current } });
      }
      const created = stored(publish[1], current + 1, body.definition, body.reason);
      state.scorecards.set(publish[1], [created, ...list]);
      return okResponse(created, 201);
    }
    throw apiError(404, "not_found");
  });
  /** Another person publishes a version while this page is open. */
  function publishElsewhere(key: string, definition: ScorecardDefinition) {
    const list = state.scorecards.get(key) ?? [];
    state.scorecards.set(key, [stored(key, (list[0]?.version ?? 0) + 1, definition, "someone else"), ...list]);
  }
  return { state, posts, get, post, publishElsewhere };
}

function apiErrorWithMessage(status: number, code: string, message: string) {
  const error = apiError(status, code);
  (error.response!.data as { error: { message: string } }).error.message = message;
  return error;
}

/** The router's query string, where the page keeps the selected scorecard. */
function SearchProbe() {
  return <span data-testid="search">{useLocation().search}</span>;
}

function currentSearch() {
  return screen.getByTestId("search").textContent;
}

function renderPage(role: WorkspaceRole = "owner", lang: "fa" | "en" = "en", route = "/admin/scorecards") {
  const workspace = { ...FIXTURE_WORKSPACE, role };
  return renderConsole(
    <>
      <ScorecardsPage />
      <SearchProbe />
    </>,
    {
      route,
      lang,
      auth: { status: "authenticated", user: FIXTURE_USER, workspace, workspaces: [workspace] },
    },
  );
}

const SUPPORT = "/admin/scorecards?key=inbound_support";

function previewSection() {
  return screen.getByRole("heading", { name: "Preview on a sample call" }).closest("section")!;
}

function criterion(index: number) {
  return screen.getByRole("group", { name: `Criterion ${index}` });
}

async function openEdit(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Edit" }, LOAD));
  await screen.findByRole("group", { name: "Criterion 1" }, LOAD);
}

async function replaceText(user: ReturnType<typeof userEvent.setup>, field: HTMLElement, text: string) {
  await user.clear(field);
  if (text) await user.type(field, text);
}

function publishBodies(posts: { url: string; body: unknown }[]) {
  return posts.filter((entry) => entry.url !== "/scorecards/validate");
}

// The editor tests type many fields; a loaded machine needs more than the 5 s default.
describe("ScorecardsPage", { timeout: 60_000 }, () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("lists every scorecard with its key and latest version", async () => {
    mockScorecardApi();
    renderPage();

    const support = await screen.findByRole("link", { name: /Inbound support/ }, LOAD);
    expect(screen.getByRole("heading", { level: 1, name: "Scorecards" })).toBeInTheDocument();
    expect(support).toHaveTextContent("inbound_support");
    expect(support).toHaveTextContent("Version 2");
    expect(screen.getByRole("link", { name: /Sales calls/ })).toHaveTextContent("Version 1");
    expect(screen.getByText("Choose a scorecard to see its criteria and version history.")).toBeInTheDocument();

    await userEvent.setup().click(support);
    expect(currentSearch()).toBe("?key=inbound_support");
    expect(await screen.findByRole("heading", { level: 2, name: "Version history" }, LOAD)).toBeInTheDocument();
  });

  it("shows the empty state", async () => {
    const { state } = mockScorecardApi();
    state.scorecards.clear();
    renderPage();
    expect(await screen.findByText("No scorecards yet.", undefined, LOAD)).toBeInTheDocument();
  });

  it("shows an error with Try again when the list read fails", async () => {
    const { state } = mockScorecardApi();
    state.listError = true;
    renderPage();
    const retry = await screen.findByRole("button", { name: "Try again" }, LOAD);
    // A 500 gets the common helper's text for an unavailable service.
    expect(screen.getByRole("alert")).toHaveTextContent("The service is not available right now");
    state.listError = false;
    await userEvent.setup().click(retry);
    expect(await screen.findByRole("link", { name: /Inbound support/ }, LOAD)).toBeInTheDocument();
  });

  it("shows the definition, the history and the preview", async () => {
    mockScorecardApi();
    renderPage("owner", "en", SUPPORT);

    expect(await screen.findByRole("heading", { level: 2, name: "Inbound support" }, LOAD)).toBeInTheDocument();
    expect(screen.getByText("Pass threshold: 75%")).toBeInTheDocument();
    // Once in the criteria, once in the preview.
    expect(screen.getAllByText("Asks for recording consent")).toHaveLength(2);
    expect(screen.getByText("Applies to: Call · Inbound")).toBeInTheDocument();

    const history = await screen.findByRole("grid", { name: "Version history" }, LOAD);
    const rows = within(history).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("tighten the greeting");
    expect(within(rows[0]).getByText("Current")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Sara Ahmadi")).toBeInTheDocument();
    expect(within(rows[1]).getByRole("button", { name: "View version 1" })).toBeInTheDocument();
    expect(within(rows[0]).queryByRole("button", { name: /View version/ })).not.toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Preview on a sample call" })).toBeInTheDocument();
    expect(screen.getByText("Weight that counts: 100 of 100")).toBeInTheDocument();
  });

  it("previews an unknown direction and another call type", async () => {
    const user = userEvent.setup();
    mockScorecardApi();
    renderPage("viewer", "en", SUPPORT);
    await screen.findByText("Weight that counts: 100 of 100", undefined, LOAD);

    await user.click(screen.getByRole("button", { name: /Direction/ }));
    await user.click(await screen.findByRole("option", { name: "Unknown" }));
    expect(screen.getByText("Weight that counts: 60 of 100")).toBeInTheDocument();
    const preview = previewSection();
    const results = within(preview).getAllByRole("listitem");
    expect(results[0]).toHaveTextContent("Applies");
    expect(results[1]).toHaveTextContent("Unknown");

    await user.click(screen.getByRole("button", { name: /Call type/ }));
    await user.click(await screen.findByRole("option", { name: "Meeting" }));
    expect(screen.getByText("Weight that counts: 60 of 100")).toBeInTheDocument();
    expect(within(preview).getByText("Not applicable")).toBeInTheDocument();
  });

  it.each(["member", "viewer"] as const)("is read only for a %s and sends no write", async (role) => {
    const { posts } = mockScorecardApi();
    renderPage(role, "en", SUPPORT);

    await screen.findByRole("heading", { level: 2, name: "Inbound support" }, LOAD);
    await screen.findByRole("grid", { name: "Version history" }, LOAD);
    expect(screen.getByRole("heading", { name: "Preview on a sample call" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New scorecard" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish" })).not.toBeInTheDocument();
    expect(screen.getByText("Only workspace owners and admins can change scorecards.")).toBeInTheDocument();
    expect(posts).toEqual([]);
  });

  it.each(["owner", "admin"] as const)("lets an %s open the editor", async (role) => {
    const user = userEvent.setup();
    mockScorecardApi();
    renderPage(role, "en", SUPPORT);

    expect(await screen.findByRole("button", { name: "New scorecard" }, LOAD)).toBeInTheDocument();
    await openEdit(user);
    expect(within(criterion(1)).getByRole("textbox", { name: "Criterion ID" })).toHaveValue("greeting");
    expect(within(criterion(1)).getByRole("textbox", { name: "Criterion ID" })).toHaveAttribute("dir", "ltr");
    expect(within(criterion(2)).getByRole("checkbox", { name: "Auto-fail" })).toBeChecked();
    expect(within(criterion(2)).getByRole("checkbox", { name: "Call" })).toBeChecked();
    expect(within(criterion(2)).getByRole("checkbox", { name: "Meeting" })).not.toBeChecked();
    expect(within(criterion(2)).getByRole("textbox", { name: "Question" })).toHaveValue("Did the agent ask for consent?");
    expect(within(criterion(1)).getByRole("textbox", { name: "Turns" })).toHaveValue("4");
    expect(screen.getByText("Total weight: 100 / 100")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Scorecard key" })).not.toBeInTheDocument();
  });

  it("refuses weights that do not add up to 100, while Check still asks the server", async () => {
    const user = userEvent.setup();
    const { posts } = mockScorecardApi();
    renderPage("owner", "en", SUPPORT);
    await openEdit(user);
    await user.type(screen.getByRole("textbox", { name: "Reason for this version" }), "lower the greeting");

    await replaceText(user, within(criterion(1)).getByRole("textbox", { name: "Weight" }), "50");
    expect(screen.getByText("Total weight: 90 / 100")).toBeInTheDocument();
    expect(screen.getByText("Weights must add up to 100.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Check" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].url).toBe("/scorecards/validate");
    expect(await screen.findByText("The scorecard is valid.", undefined, LOAD)).toHaveAttribute("role", "status");

    await replaceText(user, within(criterion(2)).getByRole("textbox", { name: "Weight" }), "50");
    expect(screen.getByText("Total weight: 100 / 100")).toBeInTheDocument();
    expect(screen.queryByText("Weights must add up to 100.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish" })).toBeEnabled();
  });

  it("says why Publish is disabled while the reason is blank", async () => {
    const user = userEvent.setup();
    mockScorecardApi();
    renderPage("owner", "en", SUPPORT);
    await openEdit(user);

    // The stored draft is valid; only the reason is missing.
    expect(screen.getByText("Total weight: 100 / 100")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
    expect(screen.getByText("Write a reason to publish this version.")).toBeVisible();

    await user.type(screen.getByRole("textbox", { name: "Reason for this version" }), "a reason");
    expect(screen.getByRole("button", { name: "Publish" })).toBeEnabled();
    expect(screen.queryByText("Write a reason to publish this version.")).not.toBeInTheDocument();

    // A cleared Turns value blocks Publish with its own visible error.
    await user.clear(within(criterion(1)).getByRole("textbox", { name: "Turns" }));
    expect(within(criterion(1)).getByText("Enter a whole number, 1 or more.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
  });

  it("shows the error of an added criterion's empty weight at once", async () => {
    const user = userEvent.setup();
    mockScorecardApi();
    renderPage("owner", "en", SUPPORT);
    await openEdit(user);
    await user.type(screen.getByRole("textbox", { name: "Reason for this version" }), "add a check");
    expect(screen.getByRole("button", { name: "Publish" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Add criterion" }));
    expect(within(criterion(3)).getByRole("textbox", { name: "Weight" })).toHaveValue("");
    // The other weights still add up to 100, so the total says nothing is wrong.
    expect(screen.getByText("Total weight: 100 / 100")).toBeInTheDocument();
    expect(screen.queryByText("Weights must add up to 100.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
    expect(within(criterion(3)).getByText("Enter a whole number from 0 to 100.")).toBeVisible();
  });

  it("sends nothing when a number is not a whole number, and accepts Persian digits", async () => {
    const user = userEvent.setup();
    const { posts } = mockScorecardApi();
    renderPage("owner", "en", SUPPORT);
    await openEdit(user);
    const weight = within(criterion(1)).getByRole("textbox", { name: "Weight" });

    await replaceText(user, weight, "6.5");
    await user.click(screen.getByRole("button", { name: "Check" }));
    expect(within(criterion(1)).getByText("Enter a whole number from 0 to 100.")).toBeInTheDocument();
    expect(posts).toEqual([]);

    await replaceText(user, weight, "۶۰");
    expect(screen.getByText("Total weight: 100 / 100")).toBeInTheDocument();
  });

  it("shows a 422 with its sentence and the server's message", async () => {
    const user = userEvent.setup();
    const { state } = mockScorecardApi();
    state.validateError = apiErrorWithMessage(422, "weight_zero_not_auto_fail", "criteria.0.weight: must be auto_fail");
    state.publishError = apiErrorWithMessage(422, "duplicate_criterion", "criteria.1.criterion_id: duplicate");
    renderPage("owner", "en", SUPPORT);
    await openEdit(user);

    await user.click(screen.getByRole("button", { name: "Check" }));
    const alert = await screen.findByRole("alert", undefined, LOAD);
    expect(alert).toHaveTextContent("A criterion with weight 0 must be auto-fail.");
    const detail = within(alert).getByText("criteria.0.weight: must be auto_fail");
    expect(detail.tagName).toBe("BDI");
    expect(detail).toHaveAttribute("dir", "ltr");

    await user.type(screen.getByRole("textbox", { name: "Reason for this version" }), "try");
    await user.click(screen.getByRole("button", { name: "Publish" }));
    const publishAlert = await screen.findByText("Two criteria have the same ID.", undefined, LOAD);
    expect(publishAlert.closest("[role='alert']")).toHaveTextContent("criteria.1.criterion_id: duplicate");
  });

  it("falls back to the generic error for an unknown 422 code", async () => {
    const user = userEvent.setup();
    const { state } = mockScorecardApi();
    state.validateError = apiErrorWithMessage(422, "something_new", "internal words");
    renderPage("owner", "en", SUPPORT);
    await openEdit(user);
    await user.click(screen.getByRole("button", { name: "Check" }));
    const alert = await screen.findByRole("alert", undefined, LOAD);
    expect(alert).not.toHaveTextContent("internal words");
  });

  it("sends an unchanged definition back exactly, hidden fields included", async () => {
    const user = userEvent.setup();
    const { posts } = mockScorecardApi();
    renderPage("owner", "en", SUPPORT);
    await openEdit(user);
    await user.type(screen.getByRole("textbox", { name: "Reason for this version" }), "  same rules  ");
    await user.click(screen.getByRole("button", { name: "Publish" }));

    expect(await screen.findByText("Published version 3.", undefined, LOAD)).toHaveAttribute("role", "status");
    expect(publishBodies(posts)).toEqual([
      {
        url: "/scorecards/inbound_support/versions",
        body: { definition: supportDefinition(), reason: "same rules", expected_version: 2 },
      },
    ]);
    expect(screen.queryByRole("button", { name: "Publish" })).not.toBeInTheDocument();
    expect(currentSearch()).toBe("?key=inbound_support");
    const history = await screen.findByRole("grid", { name: "Version history" }, LOAD);
    await waitFor(() => expect(within(history).getAllByRole("row")).toHaveLength(4), LOAD);
    expect(await screen.findByRole("link", { name: /Inbound support/ })).toHaveTextContent("Version 3");
  });

  it("keeps the draft on a 409, then loads the new version and publishes on it", async () => {
    const user = userEvent.setup();
    const { posts, publishElsewhere } = mockScorecardApi();
    renderPage("owner", "en", SUPPORT);
    await openEdit(user);
    publishElsewhere("inbound_support", supportDefinition({ name: "Renamed elsewhere", pass_threshold: 90 }));

    const name = screen.getByRole("textbox", { name: "Name" });
    await replaceText(user, name, "My draft name");
    await user.type(screen.getByRole("textbox", { name: "Reason for this version" }), "my change");
    await user.click(screen.getByRole("button", { name: "Publish" }));

    const alert = await screen.findByText("Someone else published version 3 while you were editing.", undefined, LOAD);
    expect(alert.closest("[role='alert']")).toHaveTextContent("Loading it replaces your draft");
    expect(name).toHaveValue("My draft name");
    expect(publishBodies(posts)[0].body).toMatchObject({ expected_version: 2 });

    await user.click(screen.getByRole("button", { name: "Load version 3" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Renamed elsewhere"), LOAD);
    expect(screen.getByRole("textbox", { name: "Pass threshold" })).toHaveValue("90");
    expect(screen.getByRole("textbox", { name: "Reason for this version" })).toHaveValue("my change");
    expect(screen.queryByText(/Someone else published/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Publish" }));
    expect(await screen.findByText("Published version 4.", undefined, LOAD)).toBeInTheDocument();
    expect(publishBodies(posts)[1].body).toMatchObject({ expected_version: 3, reason: "my change" });
  });

  it("keeps the draft and the alert when the reload fails", async () => {
    const user = userEvent.setup();
    const { state, publishElsewhere } = mockScorecardApi();
    renderPage("owner", "en", SUPPORT);
    await openEdit(user);
    publishElsewhere("inbound_support", supportDefinition({ name: "Renamed elsewhere" }));
    await replaceText(user, screen.getByRole("textbox", { name: "Name" }), "My draft name");
    await user.type(screen.getByRole("textbox", { name: "Reason for this version" }), "my change");
    await user.click(screen.getByRole("button", { name: "Publish" }));
    await screen.findByRole("button", { name: "Load version 3" }, LOAD);

    state.versionReadError = true;
    await user.click(screen.getByRole("button", { name: "Load version 3" }));
    // The shared error text for a 500; the conflict alert stays above it.
    expect(await screen.findByText(/The service is not available right now/, undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByText("Someone else published version 3 while you were editing.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("My draft name");
    expect(screen.getByRole("button", { name: "Load version 3" })).toBeInTheDocument();
  });

  it("creates a new scorecard with expected_version 0", async () => {
    const user = userEvent.setup();
    const { posts } = mockScorecardApi();
    renderPage("owner", "en");
    await user.click(await screen.findByRole("button", { name: "New scorecard" }, LOAD));

    const key = screen.getByRole("textbox", { name: "Scorecard key" });
    expect(key).toHaveAttribute("dir", "ltr");
    expect(screen.getByRole("textbox", { name: "Pass threshold" })).toHaveValue("80");
    expect(within(criterion(1)).getByRole("textbox", { name: "Weight" })).toHaveValue("100");
    expect(within(criterion(1)).getByRole("button", { name: "Remove criterion" })).toBeDisabled();
    expect(within(criterion(1)).getByRole("button", { name: /Check type/ })).toHaveTextContent("Required phrase");
    expect(within(criterion(1)).getByRole("button", { name: /Speaker/ })).toHaveTextContent("Agent");
    expect(within(criterion(1)).getByRole("button", { name: /Scope/ })).toHaveTextContent("Whole call");

    await user.type(key, "qa_calls");
    await user.type(screen.getByRole("textbox", { name: "Name" }), "QA calls");
    await user.type(within(criterion(1)).getByRole("textbox", { name: "Criterion ID" }), "greeting");
    await user.type(within(criterion(1)).getByRole("textbox", { name: "Label" }), "Greets");
    await user.type(within(criterion(1)).getByRole("textbox", { name: "Phrases" }), "hello{Enter}{Enter} hi ");
    await user.click(within(criterion(1)).getByRole("checkbox", { name: "Outbound" }));
    await user.type(screen.getByRole("textbox", { name: "Reason for this version" }), "first version");
    await user.click(screen.getByRole("button", { name: "Publish" }));

    expect(await screen.findByText("Published version 1.", undefined, LOAD)).toBeInTheDocument();
    expect(publishBodies(posts)).toEqual([
      {
        url: "/scorecards/qa_calls/versions",
        body: {
          expected_version: 0,
          reason: "first version",
          definition: {
            name: "QA calls",
            description: null,
            pass_threshold: 80,
            criteria: [
              {
                criterion_id: "greeting",
                label: "Greets",
                weight: 100,
                auto_fail: false,
                na_allowed: false,
                applies_to: { conversation_types: null, directions: ["outbound"] },
                check: {
                  check_id: "greeting",
                  version: 1,
                  kind: "required_phrase",
                  phrases: ["hello", "hi"],
                  speaker_role: "agent",
                  scope: { kind: "whole_call" },
                },
              },
            ],
          },
        },
      },
    ]);
    expect(currentSearch()).toBe("?key=qa_calls");
    expect(await screen.findByRole("link", { name: /QA calls/ }, LOAD)).toBeInTheDocument();
  });

  it("shows the errors of an empty new key and an empty threshold at once", async () => {
    const user = userEvent.setup();
    mockScorecardApi();
    renderPage("owner", "en");
    await user.click(await screen.findByRole("button", { name: "New scorecard" }, LOAD));

    await user.type(screen.getByRole("textbox", { name: "Name" }), "QA calls");
    await user.type(within(criterion(1)).getByRole("textbox", { name: "Criterion ID" }), "greeting");
    await user.type(within(criterion(1)).getByRole("textbox", { name: "Label" }), "Greets");
    await user.type(within(criterion(1)).getByRole("textbox", { name: "Phrases" }), "hello");
    await user.type(screen.getByRole("textbox", { name: "Reason for this version" }), "first version");

    // Only the key is missing.
    expect(screen.getByRole("textbox", { name: "Scorecard key" })).toHaveValue("");
    expect(
      screen.getByText("Use 2 to 64 characters: lowercase letters, digits and _, starting with a letter."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();

    await user.type(screen.getByRole("textbox", { name: "Scorecard key" }), "qa_calls");
    expect(screen.getByRole("button", { name: "Publish" })).toBeEnabled();

    await user.clear(screen.getByRole("textbox", { name: "Pass threshold" }));
    expect(screen.getByText("Enter a whole number from 1 to 100.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
  });

  it("adds and removes criteria", async () => {
    const user = userEvent.setup();
    mockScorecardApi();
    renderPage("owner", "en");
    await user.click(await screen.findByRole("button", { name: "New scorecard" }, LOAD));
    await user.click(screen.getByRole("button", { name: "Add criterion" }));
    expect(criterion(2)).toBeInTheDocument();
    expect(within(criterion(1)).getByRole("button", { name: "Remove criterion" })).toBeEnabled();
    await user.click(within(criterion(2)).getByRole("button", { name: "Remove criterion" }));
    expect(screen.queryByRole("group", { name: "Criterion 2" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: "Criterion 1" })).not.toBeInTheDocument();
  });

  it("shows an older version read only with the current version named", async () => {
    const user = userEvent.setup();
    mockScorecardApi();
    renderPage("owner", "en", SUPPORT);
    const history = await screen.findByRole("grid", { name: "Version history" }, LOAD);
    await user.click(within(history).getByRole("button", { name: "View version 1" }));

    expect(currentSearch()).toBe("?key=inbound_support&version=1");
    expect(await screen.findByText("You are viewing version 1. The current version is 2.", undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Support v1" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "View current version" }));
    expect(currentSearch()).toBe("?key=inbound_support");
    expect(await screen.findByRole("button", { name: "Edit" }, LOAD)).toBeInTheDocument();
  });

  it("treats ?version= of the latest version as the current view", async () => {
    mockScorecardApi();
    renderPage("owner", "en", `${SUPPORT}&version=2`);
    expect(await screen.findByRole("button", { name: "Edit" }, LOAD)).toBeInTheDocument();
    expect(screen.queryByText(/You are viewing version/)).not.toBeInTheDocument();
  });

  it("shows not-found messages for an unknown key or version", async () => {
    mockScorecardApi();
    const first = renderPage("owner", "en", "/admin/scorecards?key=missing_key");
    expect(await screen.findByText("This scorecard was not found.", undefined, LOAD)).toBeInTheDocument();
    first.unmount();
    renderPage("owner", "en", `${SUPPORT}&version=9`);
    expect(await screen.findByText("This version was not found.", undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("grid", { name: "Version history" })).toBeInTheDocument();
  });

  it("treats a ?key= that is not a scorecard key as not found and sends no request for it", async () => {
    const { get } = mockScorecardApi();
    renderPage("owner", "en", "/admin/scorecards?key=../members");
    expect(await screen.findByText("This scorecard was not found.", undefined, LOAD)).toBeInTheDocument();
    await screen.findByRole("link", { name: /Inbound support/ }, LOAD);

    const urls = get.mock.calls.map(([url]) => url);
    expect(urls.some((url) => url.includes("members/versions"))).toBe(false);
    expect(urls.some((url) => url.startsWith("/scorecards/"))).toBe(false);
    expect(screen.queryByRole("grid", { name: "Version history" })).not.toBeInTheDocument();
  });

  it("renders in Persian, right to left, with Persian digits", async () => {
    mockScorecardApi();
    renderPage("owner", "fa", SUPPORT);
    expect(await screen.findByRole("heading", { level: 1, name: "کارت‌های امتیازدهی" }, LOAD)).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
    expect(await screen.findByRole("link", { name: /Inbound support/ }, LOAD)).toHaveTextContent("نسخه ۲");
    const history = await screen.findByRole("grid", { name: "تاریخچه نسخه‌ها" }, LOAD);
    expect(within(history).getByRole("button", { name: "دیدن نسخه ۱" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ویرایش" })).toBeInTheDocument();
    expect(screen.getByText("حد قبولی: ۷۵٪")).toBeInTheDocument();
  });
});
