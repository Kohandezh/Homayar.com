import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useSearchParams } from "react-router";

import VoicemailsPage from "@/components/voicemail/VoicemailsPage";
import {
  followUpStateLabel,
  voicemailCategoryLabel,
  voicemailPriorityLabel,
  voicemailStatusLabel,
  VOICEMAIL_COPY,
} from "@/components/voicemail/voicemail-copy";
import api from "@/lib/api";
import {
  FIXTURE_USER,
  FIXTURE_WORKSPACE,
  apiError,
  networkError,
  okResponse,
  renderConsole,
} from "@/test/fixtures";
import type { ActionItem } from "@/types/platform";
import type {
  FollowUpState,
  Voicemail,
  VoicemailCategory,
  VoicemailDetail,
  VoicemailPriority,
  VoicemailStatus,
} from "@/types/voicemail";
import type { AuthWorkspace, WorkspaceRole } from "@/types/auth";

const T = "2026-10-18T12:00:00Z";
// The page waits for several queries; slower machines need more than the 1 s default.
const LOAD = { timeout: 5000 };

// The suite shares the machine with parallel tickets; give the flows room.
vi.setConfig({ testTimeout: 20000 });

function voicemail(overrides: Partial<Voicemail> = {}): Voicemail {
  return {
    id: "vm-1",
    meeting_id: "meet-1",
    ingestion_id: null,
    external_message_id: "ext-1",
    mailbox: "support",
    extension: "101",
    caller_number: "+982112345678",
    caller_number_valid: true,
    received_at: T,
    duration_seconds: 95,
    status: "classified",
    review_reason: null,
    category: "billing",
    intent: "Ask about the invoice",
    topics: ["invoice", "payment"],
    priority: "urgent",
    priority_score: 87,
    priority_reasons: [],
    classification_confidence: 0.92,
    classifier_kind: "mock",
    classifier_version: "3",
    classifier_model_id: null,
    classified_revision_id: "rev-1",
    classified_at: T,
    follow_up_state: "pending",
    follow_up_action_id: "act-1",
    follow_up_done_at: null,
    assigned_team_id: null,
    assigned_user_id: null,
    created_at: T,
    ...overrides,
  };
}

function detail(overrides: Partial<VoicemailDetail> = {}): VoicemailDetail {
  return {
    ...voicemail(),
    meeting_status: "completed",
    transcript_excerpt: "The invoice number 42 was never paid.",
    transcript_revision_id: "rev-1",
    ...overrides,
  };
}

const ACTION: ActionItem = {
  id: "act-1",
  meeting_id: "meet-1",
  description: "Call the customer back about the invoice",
  assignee_text: null,
  assignee_user_id: null,
  due_at: null,
  priority: "high",
  status: "open",
  source_segment_id: null,
  source_timestamp_ms: null,
  confidence: null,
  created_by: "system",
  created_at: T,
  updated_at: T,
};

type ListAnswer = { items: Voicemail[]; next_before: string | null } | Error;

/**
 * Answers the inbox's reads from a small in-memory store, so a PATCH the test
 * drives changes what the next read returns, exactly like the server would.
 */
function mockVoicemails(listAnswers: ListAnswer[], overrides: Partial<VoicemailDetail> = {}) {
  const store = new Map<string, VoicemailDetail>([["vm-1", detail(overrides)]]);
  const listCalls: Array<Record<string, unknown>> = [];
  let index = 0;
  const get = vi.spyOn(api, "get").mockImplementation(async (url: string, config?: { params?: unknown }) => {
    if (url === "/voicemails") {
      listCalls.push((config?.params ?? {}) as Record<string, unknown>);
      const answer = listAnswers[Math.min(index, listAnswers.length - 1)];
      index += 1;
      if (answer instanceof Error) throw answer;
      return okResponse(answer);
    }
    if (url.startsWith("/voicemails/")) {
      const id = decodeURIComponent(url.slice("/voicemails/".length));
      const found = store.get(id);
      if (!found) throw apiError(404, "voicemail_not_found");
      return okResponse(found);
    }
    if (url === "/meetings/meet-1/actions") return okResponse([ACTION]);
    throw apiError(404, "not_found");
  });
  const patch = vi.spyOn(api, "patch").mockImplementation(async (url: string, body: unknown) => {
    const id = decodeURIComponent(url.slice("/voicemails/".length));
    const found = store.get(id);
    if (!found) throw apiError(404, "voicemail_not_found");
    const patchBody = body as { follow_up_state: "done" | "pending" };
    const updated: VoicemailDetail = {
      ...found,
      follow_up_state: patchBody.follow_up_state,
      follow_up_done_at: patchBody.follow_up_state === "done" ? T : null,
    };
    store.set(id, updated);
    return okResponse(updated);
  });
  return { store, listCalls, get, patch };
}

/** Shows the current query string, so the shareable URL can be asserted. */
function UrlProbe() {
  const [params] = useSearchParams();
  return <output data-testid="url">{params.toString()}</output>;
}

/** Changes the open voicemail the way the URL does (browser Back, deep link). */
function UrlVoicemailButton({ id }: { id: string }) {
  const [, setSearchParams] = useSearchParams();
  return (
    <button
      type="button"
      onClick={() =>
        setSearchParams((params) => {
          params.set("voicemail", id);
          return params;
        })
      }
    >
      {`url to ${id}`}
    </button>
  );
}

function workspace(role: WorkspaceRole): AuthWorkspace {
  return { ...FIXTURE_WORKSPACE, role };
}

function renderPage(
  route = "/voicemails",
  role: WorkspaceRole = "owner",
  lang: "fa" | "en" = "en",
) {
  const selected = workspace(role);
  return renderConsole(
    <>
      <VoicemailsPage />
      <UrlProbe />
    </>,
    {
      route,
      lang,
      auth: {
        status: "authenticated",
        user: FIXTURE_USER,
        workspace: selected,
        workspaces: [selected],
      },
    },
  );
}

async function findRow(label: RegExp | string) {
  const rowheader = await screen.findByRole("rowheader", { name: label }, LOAD);
  return rowheader.closest("tr");
}

async function openDrawer(label: RegExp | string = /Details for/) {
  const button = await screen.findByRole("button", { name: label }, LOAD);
  fireEvent.click(button);
  const drawer = await screen.findByRole("dialog", undefined, LOAD);
  // The dialog opens on its loading state; wait until the detail is in it.
  await within(drawer).findByText("Ask about the invoice", undefined, LOAD);
  return drawer;
}

// React Aria backs every select with a hidden native one, in the order the
// filters are rendered; changing it is the same as choosing from the list.
const PRIORITY_FILTER = 0;
const CATEGORY_FILTER = 1;

function filterSelect(index: number): HTMLSelectElement {
  const select = screen.getAllByTestId("hidden-select-container")[index].querySelector("select");
  if (!select) throw new Error("no filter select");
  return select;
}

describe("VoicemailsPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("renders the list from GET /voicemails with the page size and every column", async () => {
    const { listCalls } = mockVoicemails([{ items: [voicemail()], next_before: null }]);
    renderPage();

    expect(await screen.findByRole("grid", { name: "Voicemails" }, LOAD)).toBeInTheDocument();
    expect(listCalls[0]).toEqual({ limit: 25 });

    const row = await findRow(/Oct 18, 2026/);
    expect(within(row!).getByText("⁦+982112345678⁩")).toBeInTheDocument();
    expect(within(row!).getByText("Billing")).toBeInTheDocument();
    expect(within(row!).getByText("Urgent")).toBeInTheDocument();
    expect(within(row!).getByText("Classified")).toBeInTheDocument();
    expect(within(row!).getByText("Pending")).toBeInTheDocument();
    expect(within(row!).getByText("1:35")).toBeInTheDocument();
  });

  it("turns the filters in the URL into query parameters", async () => {
    const { listCalls } = mockVoicemails([{ items: [voicemail()], next_before: null }]);
    renderPage("/voicemails?priority=urgent&category=billing");

    expect(await screen.findByRole("grid", { name: "Voicemails" }, LOAD)).toBeInTheDocument();
    expect(listCalls[0]).toEqual({ limit: 25, priority: "urgent", category: "billing" });
  });

  it("ignores a filter value the API does not know", async () => {
    const { listCalls } = mockVoicemails([{ items: [voicemail()], next_before: null }]);
    renderPage("/voicemails?priority=asap&category=mystery");

    await findRow(/Oct 18, 2026/);
    expect(listCalls[0]).toEqual({ limit: 25 });
  });

  it("sends the priority chosen in the filter bar and puts it in the URL", async () => {
    const { listCalls } = mockVoicemails([{ items: [voicemail()], next_before: null }]);
    renderPage();

    await findRow(/Oct 18, 2026/);
    fireEvent.change(filterSelect(PRIORITY_FILTER), { target: { value: "urgent" } });

    await waitFor(() => expect(screen.getByTestId("url")).toHaveTextContent("priority=urgent"), LOAD);
    await waitFor(
      () => expect(listCalls.at(-1)).toEqual({ limit: 25, priority: "urgent" }),
      LOAD,
    );

    // "All" removes the parameter again.
    fireEvent.change(filterSelect(PRIORITY_FILTER), { target: { value: "all" } });
    await waitFor(() => expect(screen.getByTestId("url")).not.toHaveTextContent("priority="), LOAD);
    await waitFor(() => expect(listCalls.at(-1)).toEqual({ limit: 25 }), LOAD);
  });

  it("sends the category chosen in the filter bar and starts the paging again", async () => {
    const { listCalls } = mockVoicemails([
      { items: [voicemail()], next_before: "2026-10-18T12:00:00Z" },
      { items: [voicemail({ id: "vm-2" })], next_before: null },
      { items: [voicemail()], next_before: null },
    ]);
    renderPage();

    await findRow(/Oct 18, 2026/);
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Page 2", undefined, LOAD)).toBeInTheDocument();
    expect(listCalls[1]).toMatchObject({ before: "2026-10-18T12:00:00Z" });

    fireEvent.change(filterSelect(CATEGORY_FILTER), { target: { value: "billing" } });

    await waitFor(() => expect(screen.getByTestId("url")).toHaveTextContent("category=billing"), LOAD);
    await waitFor(
      () => expect(listCalls.at(-1)).toEqual({ limit: 25, category: "billing" }),
      LOAD,
    );
    // The old cursor belongs to the unfiltered list, so paging starts over.
    expect(screen.getByText("Page 1")).toBeInTheDocument();
  });

  it("starts paging over when a filter changes, in both directions", async () => {
    const { listCalls } = mockVoicemails([
      { items: [voicemail()], next_before: "2026-10-18T12:00:00Z" },
      { items: [voicemail()], next_before: null },
      { items: [voicemail()], next_before: null },
      { items: [voicemail()], next_before: null },
    ]);
    renderPage();

    await findRow(/Oct 18, 2026/);
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Page 2", undefined, LOAD)).toBeInTheDocument();
    expect(listCalls[1]).toMatchObject({ before: "2026-10-18T12:00:00Z" });

    fireEvent.change(filterSelect(PRIORITY_FILTER), { target: { value: "urgent" } });
    await waitFor(() => expect(screen.getByTestId("url")).toHaveTextContent("priority=urgent"), LOAD);
    await waitFor(
      () => expect(listCalls.at(-1)).toEqual({ limit: 25, priority: "urgent" }),
      LOAD,
    );
    expect(screen.getByText("Page 1")).toBeInTheDocument();

    // Back to All: the unfiltered list is a different list again, so its old
    // page-two cursor must not come back either.
    fireEvent.change(filterSelect(PRIORITY_FILTER), { target: { value: "all" } });
    await waitFor(() => expect(screen.getByTestId("url")).not.toHaveTextContent("priority="), LOAD);
    await waitFor(() => expect(listCalls.at(-1)).toEqual({ limit: 25 }), LOAD);
    expect(screen.getByText("Page 1")).toBeInTheDocument();
  });

  it("renders no Next when the page was not full", async () => {
    mockVoicemails([{ items: [voicemail()], next_before: null }]);
    renderPage();

    await findRow(/Oct 18, 2026/);
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
  });

  it("keeps Previous on an emptied later page and walks back", async () => {
    mockVoicemails([
      { items: [voicemail()], next_before: "2026-10-18T12:00:00Z" },
      { items: [], next_before: null },
      { items: [voicemail()], next_before: null },
    ]);
    renderPage();

    await findRow(/Oct 18, 2026/);
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Page 2", undefined, LOAD)).toBeInTheDocument();
    // A full page pointed here, so this is the list's end, not an empty inbox.
    expect(await screen.findByText("No more voicemails.", undefined, LOAD)).toBeInTheDocument();
    expect(screen.queryByText("No voicemails yet")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(await screen.findByText("Page 1", undefined, LOAD)).toBeInTheDocument();
    await findRow(/Oct 18, 2026/);
  });

  it("says there is nothing yet only without filters, and no match with one", async () => {
    mockVoicemails([{ items: [], next_before: null }]);
    renderPage();
    expect(await screen.findByText("No voicemails yet", undefined, LOAD)).toBeInTheDocument();

    const second = renderPage("/voicemails?priority=urgent");
    expect(
      await screen.findByText("No voicemail matches these filters", undefined, LOAD),
    ).toBeInTheDocument();
    second.unmount();
  });

  it("tries the list again after an error", async () => {
    const { listCalls, get } = mockVoicemails([
      apiError(500, "internal_error"),
      { items: [voicemail()], next_before: null },
    ]);
    renderPage();

    expect(await screen.findByRole("alert", undefined, LOAD)).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await findRow(/Oct 18, 2026/);
    expect(listCalls.length).toBe(2);
  });

  it("opens the drawer from a row button that names the caller and the date", async () => {
    mockVoicemails([{ items: [voicemail()], next_before: null }]);
    renderPage();

    await findRow(/Oct 18, 2026/);
    // The caller sits inside LTR isolate marks, so the name keeps its order.
    const drawer = await openDrawer(/Details for.*\+982112345678/);
    expect(drawer).toHaveTextContent("The invoice number 42 was never paid.");
    expect(
      await within(drawer).findByText("Call the customer back about the invoice", undefined, LOAD),
    ).toBeInTheDocument();
    // The follow-up action's status reads statusLabel, not the raw enum.
    expect(within(drawer).getByText("Open", { exact: true })).toBeInTheDocument();
    expect(within(drawer).getByRole("link", { name: "Open the call" })).toHaveAttribute(
      "href",
      "/meetings/meet-1",
    );
  });

  it("opens the drawer from the URL's voicemail id", async () => {
    const { get } = mockVoicemails([{ items: [voicemail()], next_before: null }]);
    renderPage("/voicemails?voicemail=vm-1");

    const drawer = await screen.findByRole("dialog", undefined, LOAD);
    expect(await within(drawer).findByText("⁦+982112345678⁩", undefined, LOAD)).toBeInTheDocument();
    expect(drawer).toHaveTextContent("The invoice number 42 was never paid.");
    expect(get).toHaveBeenCalledWith("/voicemails/vm-1", expect.anything());
  });

  it("treats an empty voicemail param as no drawer", async () => {
    const { get } = mockVoicemails([{ items: [voicemail()], next_before: null }]);
    renderPage("/voicemails?voicemail=");

    await findRow(/Oct 18, 2026/);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(get).not.toHaveBeenCalledWith("/voicemails/", expect.anything());
  });

  it("shows every field of the detail", async () => {
    mockVoicemails([{ items: [voicemail()], next_before: null }]);
    renderPage();

    const drawer = await openDrawer();
    expect(drawer).toHaveTextContent("support");
    expect(drawer).toHaveTextContent("101");
    expect(drawer).toHaveTextContent("1:35");
    expect(drawer).toHaveTextContent("Classified");
    expect(drawer).toHaveTextContent("Billing");
    expect(drawer).toHaveTextContent("Ask about the invoice");
    expect(drawer).toHaveTextContent("invoice");
    expect(drawer).toHaveTextContent("Urgent");
    expect(drawer).toHaveTextContent("87");
    expect(drawer).toHaveTextContent("92%");
    expect(drawer).toHaveTextContent("mock");
    // The meeting status and the action status read statusLabel, not raw enums.
    expect(drawer).toHaveTextContent("Completed");
  });

  it("says there is no transcript when the excerpt is missing", async () => {
    mockVoicemails([{ items: [voicemail()], next_before: null }], { transcript_excerpt: null });
    renderPage();

    const drawer = await openDrawer();
    expect(await within(drawer).findByText("No transcript yet", undefined, LOAD)).toBeInTheDocument();
  });

  it("says the action is not available when the id matches no action", async () => {
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/voicemails") {
        return okResponse({ items: [voicemail()], next_before: null });
      }
      if (url === "/voicemails/vm-1") return okResponse(detail());
      if (url === "/meetings/meet-1/actions") return okResponse([]);
      throw apiError(404, "not_found");
    });
    renderPage();

    const drawer = await openDrawer();
    expect(
      await within(drawer).findByText("The follow-up action is not available", undefined, LOAD),
    ).toBeInTheDocument();
  });

  it("marks a voicemail handled, shows the new state and offers reopen", async () => {
    const { patch, get } = mockVoicemails([
      { items: [voicemail()], next_before: null },
      { items: [voicemail()], next_before: null },
    ]);
    renderPage();

    const drawer = await openDrawer();
    await userEvent.click(within(drawer).getByRole("button", { name: VOICEMAIL_COPY.en.markHandled }));

    await waitFor(() => expect(patch).toHaveBeenCalledWith("/voicemails/vm-1", { follow_up_state: "done" }), LOAD);
    // The write refreshed the detail, so the drawer shows the new state.
    await waitFor(
      () =>
        expect(within(drawer).getByRole("button", { name: VOICEMAIL_COPY.en.reopen })).toBeInTheDocument(),
      LOAD,
    );
    expect(await within(drawer).findByText("Handled", undefined, LOAD)).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith("/voicemails/vm-1", expect.anything());
    // ...and the list followed the same state.
    expect(await screen.findByText("Handled", undefined, LOAD)).toBeInTheDocument();

    await userEvent.click(within(drawer).getByRole("button", { name: VOICEMAIL_COPY.en.reopen }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith("/voicemails/vm-1", { follow_up_state: "pending" }), LOAD);
    await waitFor(
      () =>
        expect(
          within(drawer).getByRole("button", { name: VOICEMAIL_COPY.en.markHandled }),
        ).toBeInTheDocument(),
      LOAD,
    );
  });

  it("offers no write control to a viewer", async () => {
    mockVoicemails([{ items: [voicemail()], next_before: null }]);
    renderPage("/voicemails?voicemail=vm-1", "viewer");

    const drawer = await screen.findByRole("dialog", undefined, LOAD);
    expect(await within(drawer).findByText("Ask about the invoice", undefined, LOAD)).toBeInTheDocument();
    expect(
      within(drawer).queryByRole("button", { name: VOICEMAIL_COPY.en.markHandled }),
    ).not.toBeInTheDocument();
    expect(within(drawer).queryByRole("button", { name: VOICEMAIL_COPY.en.reopen })).not.toBeInTheDocument();
  });

  it("shows the record-scope text and no fields for a 404 detail", async () => {
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/voicemails") {
        return okResponse({ items: [voicemail()], next_before: null });
      }
      if (url === "/voicemails/vm-1") throw apiError(404, "voicemail_not_found");
      throw apiError(404, "not_found");
    });
    renderPage("/voicemails?voicemail=vm-1");

    const drawer = await screen.findByRole("dialog", undefined, LOAD);
    expect(
      await within(drawer).findByText(
        "This voicemail was not found, or it is outside your record scope.",
        undefined,
        LOAD,
      ),
    ).toBeInTheDocument();
    expect(within(drawer).queryByText("Ask about the invoice")).not.toBeInTheDocument();
    expect(within(drawer).queryByRole("button", { name: VOICEMAIL_COPY.en.markHandled })).not.toBeInTheDocument();
  });

  it("explains a refused write with the 403 text and keeps the state", async () => {
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/voicemails") {
        return okResponse({ items: [voicemail()], next_before: null });
      }
      if (url === "/voicemails/vm-1") return okResponse(detail());
      if (url === "/meetings/meet-1/actions") return okResponse([ACTION]);
      throw apiError(404, "not_found");
    });
    vi.spyOn(api, "patch").mockRejectedValue(apiError(403, "permission_denied"));
    renderPage();

    const drawer = await openDrawer();
    await userEvent.click(within(drawer).getByRole("button", { name: VOICEMAIL_COPY.en.markHandled }));

    const alert = await within(drawer).findByRole("alert", undefined, LOAD);
    expect(alert).toHaveTextContent("You cannot change voicemails in this workspace");
    // The write was refused, so the control and the state are unchanged.
    expect(within(drawer).getByRole("button", { name: VOICEMAIL_COPY.en.markHandled })).toBeInTheDocument();
    expect(within(drawer).getByText("Pending")).toBeInTheDocument();
  });

  it("does not carry a failed write into the next voicemail", async () => {
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/voicemails") {
        return okResponse({ items: [voicemail()], next_before: null });
      }
      if (url === "/voicemails/vm-1") return okResponse(detail());
      if (url === "/voicemails/vm-2") {
        return okResponse(detail({ id: "vm-2", caller_number: "+982199999999", intent: "Second caller" }));
      }
      if (url === "/meetings/meet-1/actions") return okResponse([ACTION]);
      throw apiError(404, "not_found");
    });
    vi.spyOn(api, "patch").mockRejectedValue(apiError(403, "permission_denied"));
    // The extra button swaps the open voicemail through the URL, the way
    // browser Back (or a deep link) does, without the drawer's close trigger.
    const selected = workspace("owner");
    renderConsole(
      <>
        <VoicemailsPage />
        <UrlProbe />
        <UrlVoicemailButton id="vm-2" />
      </>,
      {
        route: "/voicemails",
        lang: "en",
        auth: { status: "authenticated", user: FIXTURE_USER, workspace: selected, workspaces: [selected] },
      },
    );

    const first = await openDrawer(/Details for.*\+982112345678/);
    await userEvent.click(within(first).getByRole("button", { name: VOICEMAIL_COPY.en.markHandled }));
    expect(await within(first).findByRole("alert", undefined, LOAD)).toBeInTheDocument();

    // The URL can swap the open voicemail without closing the drawer, so the
    // write state must follow the id it belongs to, not survive into the next.
    fireEvent.click(screen.getByText("url to vm-2"));
    expect(await within(first).findByText("Second caller", undefined, LOAD)).toBeInTheDocument();
    expect(within(first).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("explains a write that never reached the server", async () => {
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/voicemails") {
        return okResponse({ items: [voicemail()], next_before: null });
      }
      if (url === "/voicemails/vm-1") return okResponse(detail());
      if (url === "/meetings/meet-1/actions") return okResponse([ACTION]);
      throw apiError(404, "not_found");
    });
    vi.spyOn(api, "patch").mockRejectedValue(networkError());
    renderPage();

    const drawer = await openDrawer();
    await userEvent.click(within(drawer).getByRole("button", { name: VOICEMAIL_COPY.en.markHandled }));

    const alert = await within(drawer).findByRole("alert", undefined, LOAD);
    expect(alert).toHaveTextContent(VOICEMAIL_COPY.en.updateFailed);
  });

  it("renders in Persian, right to left", async () => {
    mockVoicemails([{ items: [voicemail()], next_before: null }]);
    renderPage("/voicemails", "owner", "fa");

    expect(await screen.findByRole("heading", { name: "پیام‌های صوتی", level: 1 }, LOAD)).toBeInTheDocument();
    expect(document.documentElement.dir).toBe("rtl");
    const grid = await screen.findByRole("grid", { name: "پیام‌های صوتی" }, LOAD);
    expect(screen.getByText(VOICEMAIL_COPY.fa.recordScope)).toBeInTheDocument();
    expect(await within(grid).findByText("صورت‌حساب", undefined, LOAD)).toBeInTheDocument();
    expect(within(grid).getByText("فوری")).toBeInTheDocument();
    expect(within(grid).getByText("در انتظار")).toBeInTheDocument();
  });
});

describe("voicemail enum labels", () => {
  it("labels every known value in both languages", () => {
    expect(voicemailPriorityLabel("urgent", "en")).toBe("Urgent");
    expect(voicemailPriorityLabel("urgent", "fa")).toBe("فوری");
    expect(voicemailCategoryLabel("billing", "en")).toBe("Billing");
    expect(voicemailCategoryLabel("billing", "fa")).toBe("صورت‌حساب");
    expect(voicemailStatusLabel("needs_review", "en")).toBe("Needs review");
    expect(voicemailStatusLabel("needs_review", "fa")).toBe("نیازمند بررسی");
    expect(followUpStateLabel("action_created", "en")).toBe("Action created");
    expect(followUpStateLabel("action_created", "fa")).toBe("اقدام ثبت شد");
  });

  it("shows an unknown value as sent, and never a prototype member", () => {
    // `"constructor"` resolves to an inherited function on a plain object;
    // the guards must return the raw value instead.
    expect(voicemailPriorityLabel("constructor" as VoicemailPriority, "en")).toBe("constructor");
    expect(voicemailCategoryLabel("constructor" as VoicemailCategory, "fa")).toBe("constructor");
    expect(voicemailStatusLabel("constructor" as VoicemailStatus, "en")).toBe("constructor");
    expect(followUpStateLabel("constructor" as FollowUpState, "fa")).toBe("constructor");
    expect(voicemailPriorityLabel("asap" as VoicemailPriority, "en")).toBe("asap");
    expect(voicemailCategoryLabel("mystery" as VoicemailCategory, "fa")).toBe("mystery");
    expect(voicemailStatusLabel("archived" as VoicemailStatus, "en")).toBe("archived");
    expect(followUpStateLabel("lost" as FollowUpState, "fa")).toBe("lost");
  });
});
