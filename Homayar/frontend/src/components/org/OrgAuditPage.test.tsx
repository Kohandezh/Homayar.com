import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import { apiError } from "@/test/fixtures";
import {
  LOAD,
  ORG_ID,
  auditEvent,
  chooseOption,
  mockOrgReads,
  orgUrl,
  paramsSentTo,
  renderOrg,
  requestedUrls,
  setupUser,
  typeDay,
} from "@/components/org/org-test-kit";

const BASE = `/organizations/${ORG_ID}`;

/** The event label also appears in the filter select, so rows are read from their cells. */
async function rowOf(name: string) {
  const cell = await screen.findByRole("gridcell", { name: new RegExp(name) }, LOAD);
  const row = cell.closest("tr");
  if (!row) throw new Error(`no row for ${name}`);
  return row;
}

describe("organization audit page", { timeout: 60_000 }, () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists the organization's events with time, event, actor, target and outcome", async () => {
    mockOrgReads({
      auditEvents: {
        items: [
          auditEvent(),
          auditEvent({
            id: "ev-2",
            event_type: "organization.workspace.created",
            target_type: "workspace",
            target_id: "ws-9",
            actor_user_id: null,
            details: { organization_id: ORG_ID, name: "Sales", slug: "sales" },
          }),
          auditEvent({
            id: "ev-3",
            event_type: "organization.scope.revoked",
            target_type: "organization_scope_grant",
            actor_user_id: "user-gone",
          }),
        ],
        next_cursor: null,
      },
    });
    renderOrg(orgUrl("/audit"));
    expect(await screen.findByRole("grid", { name: "Organization audit events" }, LOAD)).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("dir", "ltr");
    const granted = await rowOf("Organization member added");
    expect(within(granted).getByText("Person")).toBeInTheDocument();
    expect(within(granted).getByText("Organization membership")).toBeInTheDocument();
    expect(within(granted).getByText("Succeeded")).toBeInTheDocument();
    const created = await rowOf("Organization workspace created");
    expect(within(created).getByText("System")).toBeInTheDocument();
    expect(within(created).getByText("Workspace")).toBeInTheDocument();
    // An actor who is no member of the organization shows as the raw id.
    const revoked = await rowOf("Organization scope revoked");
    expect(within(revoked).getByText("user-gone")).toBeInTheDocument();
  });

  it("takes the event type options from the organization catalog and sends the filter as a query param", async () => {
    const user = setupUser();
    const get = mockOrgReads({ auditEvents: { items: [auditEvent()], next_cursor: null } });
    renderOrg(orgUrl("/audit"));
    await screen.findByRole("grid", { name: "Organization audit events" }, LOAD);
    // The catalog came from the organization route, never the workspace one.
    expect(requestedUrls(get)).toContain(`${BASE}/audit-event-types`);
    expect(requestedUrls(get)).not.toContain("/admin/audit-event-types");

    await chooseOption(user, screen.getByRole("button", { name: /Event type/ }), "Organization member added");
    await waitFor(
      () =>
        expect(paramsSentTo(get, `${BASE}/audit-events`)).toContainEqual(
          expect.objectContaining({ event_type: "organization.member.granted" }),
        ),
      LOAD,
    );
  });

  it("sends the actor filter and the date range as query params", async () => {
    const user = setupUser();
    const get = mockOrgReads({ auditEvents: { items: [auditEvent()], next_cursor: null } });
    renderOrg(orgUrl("/audit"));
    await screen.findByRole("grid", { name: "Organization audit events" }, LOAD);
    await chooseOption(user, screen.getByRole("button", { name: /Actor/ }), "Ali");
    await waitFor(
      () =>
        expect(paramsSentTo(get, `${BASE}/audit-events`)).toContainEqual(
          expect.objectContaining({ actor_user_id: "user-2" }),
        ),
      LOAD,
    );

    // The chosen days count in the reader's time zone; the end day is included.
    await typeDay(user, document.body, "Date range", "2026-10-01");
    await user.keyboard("10052026");
    await waitFor(
      () =>
        expect(paramsSentTo(get, `${BASE}/audit-events`)).toContainEqual(
          expect.objectContaining({
            actor_user_id: "user-2",
            occurred_after: new Date(2026, 9, 1).toISOString(),
            occurred_before: new Date(2026, 9, 6).toISOString(),
          }),
        ),
      LOAD,
    );
  });

  it("opens the drawer with every field of a row", async () => {
    mockOrgReads({
      auditEvents: {
        items: [auditEvent({ request_id: "req-42", details: { organization_id: ORG_ID, role: "analyst" } })],
        next_cursor: null,
      },
    });
    renderOrg(orgUrl("/audit"));
    await screen.findByRole("grid", { name: "Organization audit events" }, LOAD);
    fireEvent.click(await rowOf("Organization member added"));
    const dialog = await screen.findByRole("dialog", undefined, LOAD);
    expect(within(dialog).getByText("organization.member.granted")).toBeInTheDocument();
    expect(within(dialog).getByText("req-42")).toBeInTheDocument();
    expect(within(dialog).getByText("Role")).toBeInTheDocument();
  });

  it("walks the pages with next_cursor and back again", async () => {
    const get = mockOrgReads({
      auditEvents: {
        items: [auditEvent({ id: "ev-a", event_type: "organization.scope.granted", target_type: "organization_scope_grant" })],
        next_cursor: "cur-1",
      },
    });
    renderOrg(orgUrl("/audit"));
    expect(await screen.findByText("Page 1", undefined, LOAD)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    await waitFor(
      () =>
        expect(paramsSentTo(get, `${BASE}/audit-events`)).toContainEqual(
          expect.objectContaining({ cursor: "cur-1" }),
        ),
      LOAD,
    );
    expect(await screen.findByText("Page 2", undefined, LOAD)).toBeInTheDocument();
    // The second page's answer is the same mock, so Next is still offered;
    // going back returns to the cursor-less first page.
    fireEvent.click(screen.getByRole("button", { name: /Previous/ }));
    await waitFor(
      () =>
        expect(paramsSentTo(get, `${BASE}/audit-events`).filter((params) => params?.cursor)).toHaveLength(1),
      LOAD,
    );
    expect(await screen.findByText("Page 1", undefined, LOAD)).toBeInTheDocument();
  });

  it("offers no next page when next_cursor is null", async () => {
    mockOrgReads({ auditEvents: { items: [auditEvent()], next_cursor: null } });
    renderOrg(orgUrl("/audit"));
    await screen.findByRole("grid", { name: "Organization audit events" }, LOAD);
    expect(screen.getByRole("button", { name: /Next/ })).toHaveAttribute("aria-disabled", "true");
  });

  it("shows the empty state, and a different one under filters", async () => {
    mockOrgReads({ auditEvents: { items: [], next_cursor: null } });
    renderOrg(orgUrl("/audit"));
    expect(await screen.findByText("No events have been recorded yet.", undefined, LOAD)).toBeInTheDocument();

    const view = renderOrg(orgUrl("/audit?event_type=organization.scope.revoked"));
    expect(await screen.findByText("No events match these filters.", undefined, LOAD)).toBeInTheDocument();
    view.unmount();
  });

  it("shows the error state when the audit log cannot be read", async () => {
    const get = mockOrgReads({ auditEvents: apiError(500, "server_error") });
    renderOrg(orgUrl("/audit"));
    expect(await screen.findByRole("alert", undefined, LOAD)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "First page" })).not.toBeInTheDocument();
    expect(get).toHaveBeenCalled();
  });

  it("leads back to the first page when a cursor is no longer valid", async () => {
    const get = mockOrgReads({ auditEvents: { items: [auditEvent()], next_cursor: "cur-1" } });
    const answerOrgReads = get.getMockImplementation();
    if (!answerOrgReads) throw new Error("mockOrgReads gave no implementation");
    get.mockImplementation((url: string, ...rest: unknown[]) => {
      const cursor = (rest[0] as { params?: { cursor?: string } } | undefined)?.params?.cursor;
      if (url === `${BASE}/audit-events` && cursor === "cur-1") {
        return Promise.reject(apiError(400, "invalid_cursor"));
      }
      return answerOrgReads(url, ...(rest as []));
    });
    const firstPageCalls = () =>
      paramsSentTo(get, `${BASE}/audit-events`).filter((params) => !params?.cursor).length;

    renderOrg(orgUrl("/audit"));
    await screen.findByRole("grid", { name: "Organization audit events" }, LOAD);
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    expect(await screen.findByText(/start again from the first page/i, undefined, LOAD)).toBeInTheDocument();
    expect(screen.queryByRole("grid", { name: "Organization audit events" })).not.toBeInTheDocument();
    const before = firstPageCalls();

    fireEvent.click(screen.getByRole("button", { name: "First page" }));
    // The way out asks for the first page again: one more call without a cursor.
    await waitFor(() => expect(firstPageCalls()).toBeGreaterThan(before), LOAD);
    expect(await screen.findByRole("grid", { name: "Organization audit events" }, LOAD)).toBeInTheDocument();
    expect(screen.getByText("Page 1")).toBeInTheDocument();
  });

  it("is right to left in Persian with Persian headings", async () => {
    mockOrgReads({
      auditEvents: { items: [auditEvent()], next_cursor: null },
    });
    renderOrg(orgUrl("/audit"), "fa");
    expect(await screen.findByRole("heading", { level: 1, name: "گزارش رویدادها" }, LOAD)).toBeInTheDocument();
    expect(await screen.findByRole("grid", { name: "رویدادهای ممیزی سازمان" }, LOAD)).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
  });
});
