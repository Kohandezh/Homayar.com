import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useSearchParams } from "react-router";
import AuditFilterBar from "@/components/admin/audit/AuditFilterBar";
import AuditLogPage from "@/components/admin/AuditLogPage";
import api from "@/lib/api";
import { NO_AUDIT_FILTERS } from "@/lib/audit-filters";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { AuditEvent, AuditEventTypeEntry } from "@/types/platform";

const T = "2026-09-24T12:00:00Z";
// The page waits for two queries; slower machines need more than the 1 s default.
const LOAD = { timeout: 5000 };

function event(overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    id: "ev-1",
    actor_user_id: FIXTURE_USER.id,
    event_type: "member.role_changed",
    target_type: "membership",
    target_id: "mem-2",
    outcome: "failure",
    request_id: "req-7",
    details: { previous_role: "member", new_role: "viewer" },
    occurred_at: T,
    ...overrides,
  };
}

const MEMBERS = {
  items: [
    {
      id: "mem-1",
      user_id: FIXTURE_USER.id,
      email: FIXTURE_USER.email,
      phone_e164: null,
      display_name: "Person",
      role: "owner",
      joined_at: T,
    },
  ],
  total: 1,
};

type AuditAnswer = { items: AuditEvent[]; next_cursor: string | null } | Error;

/** A small catalog: one labelled group, one unknown type, one unknown group. */
const EVENT_TYPE_CATALOG: { items: AuditEventTypeEntry[] } = {
  items: [
    { event_type: "action_item.created", group: "action_item", target_type: "action_item" },
    { event_type: "brand_new.thing", group: "brand_new", target_type: null },
    { event_type: "member.role_changed", group: "member", target_type: "membership" },
  ],
};

/** The catalog answer: data, an error, or "pending" to hold the request open. */
type CatalogAnswer = { items: AuditEventTypeEntry[] } | Error | "pending";

/** Serves /members, the event-type catalog and /admin/audit-events from a queue, repeating the last answer. */
function mockReads(answers: AuditAnswer[], catalog: CatalogAnswer = EVENT_TYPE_CATALOG) {
  const calls: Array<Record<string, unknown>> = [];
  let index = 0;
  vi.spyOn(api, "get").mockImplementation(async (url: string, config?: { params?: unknown }) => {
    if (url === "/members") return okResponse(MEMBERS);
    if (url === "/admin/audit-event-types") {
      if (catalog === "pending") {
        await new Promise(() => undefined);
      } else if (catalog instanceof Error) {
        throw catalog;
      } else {
        return okResponse(catalog);
      }
    }
    if (url === "/admin/audit-events") {
      calls.push((config?.params ?? {}) as Record<string, unknown>);
      const answer = answers[Math.min(index, answers.length - 1)];
      index += 1;
      if (answer instanceof Error) throw answer;
      return okResponse(answer);
    }
    throw apiError(404, "not_found");
  });
  return { calls };
}

/** Shows the current query string, so the shareable URL can be asserted. */
function UrlProbe() {
  const [params] = useSearchParams();
  return <output data-testid="url">{params.toString()}</output>;
}

function renderAudit(route = "/admin/audit", lang: "fa" | "en" = "en") {
  return renderConsole(
    <>
      <AuditLogPage />
      <UrlProbe />
    </>,
    {
      route,
      lang,
      auth: {
        status: "authenticated",
        user: FIXTURE_USER,
        workspace: FIXTURE_WORKSPACE,
        workspaces: [FIXTURE_WORKSPACE],
      },
    },
  );
}

/** The event label also appears in each select, so rows are read from the grid. */
function findRow(label: string) {
  return screen.findByRole("gridcell", { name: label }, LOAD);
}

// React Aria backs every select with a hidden native one, in the order the
// filters are rendered; changing it is the same as choosing from the list.
const EVENT_TYPE_FILTER = 0;
const OUTCOME_FILTER = 1;

function filterSelect(index: number): HTMLSelectElement {
  const select = screen.getAllByTestId("hidden-select-container")[index].querySelector("select");
  if (!select) throw new Error("no filter select");
  return select;
}

describe("AuditLogPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("turns the filters in the URL into query parameters", async () => {
    const { calls } = mockReads([{ items: [event()], next_cursor: null }]);
    renderAudit(
      "/admin/audit?event_type=member.role_changed&outcome=failure&actor=user-1&target_type=membership&target_id=mem-2&from=2026-09-01&to=2026-09-30",
    );

    await findRow("Member role changed");
    expect(calls[0]).toMatchObject({
      limit: 25,
      event_type: "member.role_changed",
      outcome: "failure",
      actor_user_id: "user-1",
      target_type: "membership",
      target_id: "mem-2",
    });
    // The exact instants follow the reader's time zone; audit-filters.test covers them.
    expect(calls[0].occurred_after).toEqual(expect.any(String));
    expect(calls[0].occurred_before).toEqual(expect.any(String));
    expect(calls[0].cursor).toBeUndefined();
  });

  it("pages forward and back over the cursor", async () => {
    const { calls } = mockReads([
      { items: [event()], next_cursor: "cursor-2" },
      { items: [event({ id: "ev-2", event_type: "meeting.deleted" })], next_cursor: null },
      { items: [event()], next_cursor: "cursor-2" },
    ]);
    renderAudit();

    await findRow("Member role changed");
    expect(screen.getByText("Page 1")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await findRow("Meeting deleted");
    expect(screen.getByText("Page 2")).toBeInTheDocument();
    expect(calls[1]).toMatchObject({ cursor: "cursor-2" });

    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(await screen.findByText("Page 1", undefined, LOAD)).toBeInTheDocument();
    expect(calls[2].cursor).toBeUndefined();
  });

  it("mirrors the pagination arrows and counts pages in Persian digits", async () => {
    mockReads([
      { items: [event()], next_cursor: "cursor-2" },
      { items: [event({ id: "ev-2" })], next_cursor: null },
    ]);
    renderAudit("/admin/audit", "fa");

    const next = await screen.findByRole("button", { name: "بعدی" }, LOAD);
    expect(next.querySelector("svg")?.getAttribute("class")).toContain("rtl-mirror");
    expect(screen.getByText("صفحه ۱")).toBeInTheDocument();

    fireEvent.click(next);
    expect(await screen.findByText("صفحه ۲", undefined, LOAD)).toBeInTheDocument();
    // The focused button stays mounted while the next page loads.
    expect(document.body.contains(next)).toBe(true);
  });

  it("puts a chosen filter in the URL and starts the paging again", async () => {
    const { calls } = mockReads([
      { items: [event()], next_cursor: "cursor-2" },
      { items: [event({ id: "ev-2" })], next_cursor: "cursor-3" },
      { items: [event()], next_cursor: null },
    ]);
    renderAudit();

    await findRow("Member role changed");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Page 2", undefined, LOAD)).toBeInTheDocument();

    fireEvent.change(filterSelect(OUTCOME_FILTER), { target: { value: "success" } });

    await waitFor(() => expect(screen.getByTestId("url")).toHaveTextContent("outcome=success"), LOAD);
    await waitFor(() => expect(calls.at(-1)).toMatchObject({ outcome: "success" }), LOAD);
    // The old cursor belongs to the unfiltered list, so paging starts over.
    expect(calls.at(-1)?.cursor).toBeUndefined();
    expect(screen.getByText("Page 1")).toBeInTheDocument();
  });

  it("offers the first page again after an invalid cursor", async () => {
    mockReads([
      { items: [event()], next_cursor: "cursor-2" },
      apiError(400, "invalid_cursor"),
      { items: [event()], next_cursor: "cursor-2" },
    ]);
    renderAudit();

    await findRow("Member role changed");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(
      await screen.findByText("The event list has changed; start again from the first page.", undefined, LOAD),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "First page" }));
    await findRow("Member role changed");
    expect(screen.getByText("Page 1")).toBeInTheDocument();
  });

  it("clears a date range the API refuses", async () => {
    const { calls } = mockReads([
      apiError(400, "invalid_audit_range"),
      { items: [event()], next_cursor: null },
    ]);
    renderAudit("/admin/audit?from=2026-09-30&to=2026-09-01");

    expect(
      await screen.findByText("The chosen date range is not valid; clear it and choose again.", undefined, LOAD),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear the date range" }));
    await findRow("Member role changed");
    expect(calls.at(-1)?.occurred_after).toBeUndefined();
    expect(calls.at(-1)?.occurred_before).toBeUndefined();
    expect(screen.getByTestId("url")).not.toHaveTextContent("from=");
  });

  it("says when nothing matches the filters", async () => {
    mockReads([{ items: [], next_cursor: null }]);
    renderAudit("/admin/audit?outcome=success");
    expect(await screen.findByText("No events match these filters.", undefined, LOAD)).toBeInTheDocument();
  });

  it("opens one event in a drawer and copies its request id", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    mockReads([{ items: [event()], next_cursor: null }]);
    renderAudit();

    await userEvent.click(await findRow("Member role changed"));

    const drawer = await screen.findByRole("dialog", undefined, LOAD);
    expect(drawer).toHaveTextContent("member.role_changed");
    expect(drawer).toHaveTextContent("Previous role");
    expect(drawer).toHaveTextContent("Viewer");
    expect(drawer).toHaveTextContent("req-7");
    expect(drawer).toHaveTextContent("Person");

    await userEvent.click(screen.getByRole("button", { name: "Copy request id" }));
    expect(writeText).toHaveBeenCalledWith("req-7");
  });

  it("offers exactly the catalog's event types, grouped, and only those", async () => {
    const user = userEvent.setup();
    mockReads([{ items: [event()], next_cursor: null }]);
    renderAudit();

    await findRow("Member role changed");
    await user.click(screen.getByRole("button", { name: /Event type/ }));
    const listbox = await screen.findByRole("listbox", undefined, LOAD);

    // The options are the catalog's types in catalog order: a labelled type
    // shows its label, a type without one shows its raw name.
    const options = within(listbox).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "Action item created",
      "brand_new.thing",
      "Member role changed",
    ]);

    // One group per catalog group, in order of first appearance, each with
    // its header; a group without a label shows its raw name.
    const groups = within(listbox).getAllByRole("group");
    expect(groups).toHaveLength(3);
    expect(within(groups[0]).getByText("Action items")).toBeInTheDocument();
    expect(within(groups[1]).getByText("brand_new")).toBeInTheDocument();
    expect(within(groups[2]).getByText("Members")).toBeInTheDocument();

    // A type with a label but not in the catalog (meeting.exported) is not offered.
    expect(within(listbox).queryByText("Meeting exported")).not.toBeInTheDocument();
  });

  it("keeps a URL event type the catalog does not list as the selected value", async () => {
    const user = userEvent.setup();
    mockReads([{ items: [event()], next_cursor: null }]);
    renderAudit("/admin/audit?event_type=old.linked_type");

    await findRow("Member role changed");
    // The select still shows the shared link's value as chosen.
    const trigger = await screen.findByRole("button", { name: /old\.linked_type/ }, LOAD);
    await user.click(trigger);
    const listbox = await screen.findByRole("listbox", undefined, LOAD);

    // The value comes back as one extra option at the end, in a last
    // section of its own that has no header.
    const options = within(listbox).getAllByRole("option");
    expect(options.at(-1)?.textContent).toBe("old.linked_type");
    const groups = within(listbox).getAllByRole("group");
    expect(groups).toHaveLength(4);
    expect(groups[3].textContent).toBe("old.linked_type");
  });

  it("falls back to a free-text event type when the catalog fails", async () => {
    const { calls } = mockReads(
      [{ items: [event()], next_cursor: null }],
      apiError(403, "permission_denied"),
    );
    const user = userEvent.setup();
    renderAudit();

    // The table and its events still render.
    await findRow("Member role changed");

    const field = screen.getByRole("textbox", { name: "Event type" });
    expect(field).toHaveAccessibleDescription(
      "Event types could not be loaded. Type the exact event type, for example member.role_changed.",
    );

    // Typing does not change the URL.
    await user.type(field, " media.orphan_removed ");
    expect(screen.getByTestId("url")).not.toHaveTextContent("event_type=");

    // Enter commits the trimmed text to the URL and to the query.
    await user.type(field, "{Enter}");
    await waitFor(
      () => expect(screen.getByTestId("url")).toHaveTextContent("event_type=media.orphan_removed"),
      LOAD,
    );
    await waitFor(
      () => expect(calls.at(-1)).toMatchObject({ event_type: "media.orphan_removed" }),
      LOAD,
    );
    expect(await findRow("Member role changed")).toBeInTheDocument();

    // Clearing the filters elsewhere empties the field and the URL.
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(
      () => expect(screen.getByTestId("url")).not.toHaveTextContent("event_type="),
      LOAD,
    );
    expect(field).toHaveValue("");
  });

  it("disables the event-type select while the catalog loads", async () => {
    mockReads([{ items: [event()], next_cursor: null }], "pending");
    renderAudit();

    // The catalog does not block the rest of the page.
    await findRow("Member role changed");
    expect(filterSelect(EVENT_TYPE_FILTER)).toBeDisabled();
    expect(filterSelect(OUTCOME_FILTER)).toBeEnabled();
  });

  it("shows the URL's event type on the disabled trigger while the catalog loads", async () => {
    mockReads([{ items: [event()], next_cursor: null }], "pending");
    renderAudit("/admin/audit?event_type=member.role_changed");

    await findRow("Member role changed");
    // The value has no option yet, so the trigger carries it as its text.
    const trigger = screen.getByRole("button", { name: /Member role changed/ });
    expect(trigger).toBeDisabled();
  });

  it("commits the fallback field once per value change", async () => {
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/admin/audit-event-types") throw apiError(500, "server_error");
      throw apiError(404, "not_found");
    });
    const onChange = vi.fn();
    const user = userEvent.setup();
    renderConsole(
      <AuditFilterBar filters={{ ...NO_AUDIT_FILTERS }} members={[]} onChange={onChange} />,
      { lang: "en" },
    );

    const field = await screen.findByRole("textbox", { name: "Event type" }, LOAD);
    await user.type(field, "member.removed{Enter}");
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ eventType: "member.removed" }));

    // Blurring right after the commit does not change the filters again.
    fireEvent.blur(field);
    expect(onChange).toHaveBeenCalledTimes(1);

    // A changed text still commits on blur.
    await user.clear(field);
    await user.type(field, "media.deleted");
    fireEvent.blur(field);
    expect(onChange).toHaveBeenCalledTimes(2);
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ eventType: "media.deleted" }),
    );
  });
});
