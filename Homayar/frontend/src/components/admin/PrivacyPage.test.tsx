import { Suspense } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AxiosError, AxiosHeaders, type AxiosRequestConfig, type AxiosResponse } from "axios";

import { AppRoutes } from "@/AppRoutes";
import PrivacyPage from "@/components/admin/PrivacyPage";
import api from "@/lib/api";
import { privacyQueryKeys } from "@/lib/privacy-api";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { WorkspaceRole } from "@/types/auth";
import type { MemberListResponse } from "@/types/platform";
import type { LegalHoldItem, LegalHoldListResponse } from "@/types/privacy";

// The page waits for several queries; slower machines need more than the 1 s default.
const LOAD = { timeout: 5000 };
const DAY_MS = 24 * 60 * 60 * 1000;
/** A data subject that no other fixture mentions, so finding it anywhere is a leak. */
const SUBJECT = "subject.person@example.org";

const MEMBERS: MemberListResponse = {
  items: [
    {
      id: "membership-1",
      user_id: "user-1",
      email: "owner@demo.example.com",
      phone_e164: null,
      display_name: "Sara Ahmadi",
      role: "owner",
      joined_at: "2026-01-01T00:00:00Z",
    },
  ],
  total: 1,
};

function hold(overrides: Partial<LegalHoldItem> = {}): LegalHoldItem {
  return {
    media_id: "media-1",
    kind: "original",
    meeting_id: "m-1",
    meeting_title: "Quarterly pricing review",
    reason: "litigation",
    placed_by_user_id: "user-1",
    placed_at: "2026-10-01T09:00:00Z",
    review_at: new Date(Date.now() + 7 * DAY_MS).toISOString(),
    ...overrides,
  };
}

/** An axios error whose body is a Blob, the way `responseType: "blob"` delivers it. */
function blobError(status: number, code: string): AxiosError {
  const config = { headers: new AxiosHeaders() };
  const response: AxiosResponse = {
    status,
    statusText: String(status),
    headers: new AxiosHeaders(),
    config,
    data: new Blob([JSON.stringify({ error: { code, message: "x", request_id: "req-1" } })], {
      type: "application/json",
    }),
  };
  return new AxiosError(`Request failed with status code ${status}`, "ERR_BAD_REQUEST", config, {}, response);
}

/**
 * Answers the page's reads from mutable state. `pages` maps a cursor (or
 * "first") to a register page; every register request is recorded.
 */
function mockPrivacyApi() {
  const state = {
    pages: { first: { items: [hold()], next_cursor: null } } as Record<string, LegalHoldListResponse>,
    registerError: null as unknown,
    /** A cursor whose page fails, for a failed "Load more". */
    failCursor: null as string | null,
    exportResult: null as unknown,
    releaseError: null as unknown,
    sharesError: null as unknown,
  };
  const registerParams: Record<string, unknown>[] = [];
  const shareParams: Record<string, unknown>[] = [];
  const get = vi.spyOn(api, "get").mockImplementation(async (url: string, config?: AxiosRequestConfig) => {
    if (url === "/admin/legal-holds") {
      const params = (config?.params ?? {}) as Record<string, unknown>;
      registerParams.push(params);
      if (state.registerError) throw state.registerError;
      if (state.failCursor !== null && params.cursor === state.failCursor) throw apiError(500, "internal_error");
      const cursor = typeof params.cursor === "string" ? params.cursor : "first";
      return okResponse(state.pages[cursor] ?? { items: [], next_cursor: null });
    }
    if (url === "/admin/shares") {
      const params = (config?.params ?? {}) as Record<string, unknown>;
      shareParams.push(params);
      if (state.sharesError) throw state.sharesError;
      return okResponse({ items: [], next_cursor: null });
    }
    if (url === "/members") return okResponse(MEMBERS);
    throw apiError(404, "not_found");
  });
  const post = vi.spyOn(api, "post").mockImplementation(async (url: string) => {
    if (url === "/privacy/subject-exports") {
      if (state.exportResult instanceof Error) throw state.exportResult;
      return {
        ...okResponse(new Blob(['{"subject":"x"}'], { type: "application/json" })),
        headers: new AxiosHeaders({
          "content-disposition": 'attachment; filename="homayar-subject-export-20261018T101010Z.json"',
        }),
      };
    }
    if (url.startsWith("/media/") && url.endsWith("/legal-hold/release")) {
      if (state.releaseError) throw state.releaseError;
      state.pages = { first: { items: [], next_cursor: null } };
      return okResponse({ id: "media-1", lifecycle_state: "active" });
    }
    throw apiError(404, "not_found");
  });
  return { state, get, post, registerParams, shareParams };
}

function ownerAuth(role: WorkspaceRole = "owner") {
  const workspace = { ...FIXTURE_WORKSPACE, role };
  return { status: "authenticated" as const, user: FIXTURE_USER, workspace, workspaces: [workspace] };
}

function renderPage(lang: "fa" | "en" = "en") {
  return renderConsole(<PrivacyPage />, { route: "/admin/privacy", lang, auth: ownerAuth() });
}

function exportCard() {
  return screen.getByRole("region", { name: "Data subject export" });
}

function registerCard() {
  return screen.getByRole("region", { name: "Legal holds" });
}

async function addIdentifier(user: ReturnType<typeof userEvent.setup>, value: string, how: "enter" | "button" = "enter") {
  const input = within(exportCard()).getByRole("textbox", { name: "Identifier" });
  await user.clear(input);
  // Paste, not type: a test that adds ten values would be slow key by key.
  await user.click(input);
  await user.paste(value);
  if (how === "enter") await user.keyboard("{Enter}");
  else await user.click(within(exportCard()).getByRole("button", { name: "Add" }));
}

function tags() {
  const group = within(exportCard()).queryByRole("grid", { name: "Identifiers to export" });
  return group ? within(group).queryAllByRole("row").map((row) => row.textContent?.trim()) : [];
}

describe("PrivacyPage access", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("opens for the owner and asks for the register; an admin gets no access and no register request", async () => {
    const owner = mockPrivacyApi();
    const ownerView = renderConsole(
      <Suspense fallback={<p>Loading page</p>}>
        <AppRoutes />
      </Suspense>,
      { route: "/admin/privacy", lang: "en", auth: ownerAuth("owner") },
    );
    expect(await screen.findByRole("heading", { level: 1, name: "Privacy" }, LOAD)).toBeInTheDocument();
    expect(screen.queryByText("Coming soon")).not.toBeInTheDocument();
    await waitFor(() => expect(owner.registerParams).toHaveLength(1), LOAD);
    ownerView.unmount();
    vi.restoreAllMocks();

    for (const role of ["admin", "member", "viewer"] as const) {
      const denied = mockPrivacyApi();
      const view = renderConsole(
        <Suspense fallback={<p>Loading page</p>}>
          <AppRoutes />
        </Suspense>,
        { route: "/admin/privacy", lang: "en", auth: ownerAuth(role) },
      );
      expect(await screen.findByRole("heading", { level: 1, name: "No access" }, LOAD)).toBeInTheDocument();
      expect(denied.get.mock.calls.map(([url]) => url)).not.toContain("/admin/legal-holds");
      view.unmount();
      vi.restoreAllMocks();
    }
  });
});

describe("PrivacyPage subject export", () => {
  let createObjectURL: ReturnType<typeof vi.fn>;
  let revokeObjectURL: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    createObjectURL = vi.fn(() => "blob:export-1");
    revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("adds identifiers with Enter and Add, trims them, and skips empty values and duplicates", async () => {
    const user = userEvent.setup();
    mockPrivacyApi();
    renderPage();
    await screen.findByRole("heading", { level: 1, name: "Privacy" }, LOAD);

    await addIdentifier(user, "  a@example.com  ");
    await addIdentifier(user, "   ");
    await addIdentifier(user, "09121234567", "button");
    await addIdentifier(user, "a@example.com");
    expect(tags()).toEqual(["a@example.com", "09121234567"]);
    expect(within(exportCard()).getByRole("textbox", { name: "Identifier" })).toHaveValue("");
  });

  it("skips an e-mail that differs only in case and keeps the first spelling", async () => {
    const user = userEvent.setup();
    mockPrivacyApi();
    renderPage();
    await screen.findByRole("heading", { level: 1, name: "Privacy" }, LOAD);

    await addIdentifier(user, "Owner@Demo.example.com");
    await addIdentifier(user, "owner@demo.example.com");
    await addIdentifier(user, "IR062960000000100324200001");
    await addIdentifier(user, "ir062960000000100324200001");
    // Only e-mails ignore case; other kinds are compared as typed.
    expect(tags()).toEqual(["Owner@Demo.example.com", "IR062960000000100324200001", "ir062960000000100324200001"]);
  });

  it("removes a tag", async () => {
    const user = userEvent.setup();
    mockPrivacyApi();
    renderPage();
    await screen.findByRole("heading", { level: 1, name: "Privacy" }, LOAD);
    await addIdentifier(user, "a@example.com");
    await addIdentifier(user, "b@example.com");
    const group = within(exportCard()).getByRole("grid", { name: "Identifiers to export" });
    const first = within(group).getAllByRole("row")[0];
    await user.click(within(first).getByRole("button"));
    expect(tags()).toEqual(["b@example.com"]);
  });

  it("adds a 64-character identifier and refuses a 65-character one with an inline error", async () => {
    const user = userEvent.setup();
    mockPrivacyApi();
    renderPage();
    await screen.findByRole("heading", { level: 1, name: "Privacy" }, LOAD);

    await addIdentifier(user, "x".repeat(65));
    expect(tags()).toEqual([]);
    expect(within(exportCard()).getByText("An identifier can be at most 64 characters.")).toBeInTheDocument();

    await addIdentifier(user, "y".repeat(64));
    expect(tags()).toEqual(["y".repeat(64)]);
    expect(within(exportCard()).queryByText("An identifier can be at most 64 characters.")).not.toBeInTheDocument();
  });

  it("takes a 10th identifier and disables Add after it, so an 11th is not added", async () => {
    const user = userEvent.setup();
    mockPrivacyApi();
    renderPage();
    await screen.findByRole("heading", { level: 1, name: "Privacy" }, LOAD);

    for (let index = 1; index <= 10; index += 1) await addIdentifier(user, `p${index}@example.com`);
    expect(tags()).toHaveLength(10);
    expect(within(exportCard()).getByRole("button", { name: "Add" })).toBeDisabled();
    expect(within(exportCard()).getByText("You can add at most 10 identifiers.")).toBeInTheDocument();

    await addIdentifier(user, "p11@example.com");
    expect(tags()).toHaveLength(10);
    expect(tags()).not.toContain("p11@example.com");
  }, 20_000);

  it("keeps Export data disabled until there is an identifier and a reason of 3 characters", async () => {
    const user = userEvent.setup();
    mockPrivacyApi();
    renderPage();
    await screen.findByRole("heading", { level: 1, name: "Privacy" }, LOAD);
    const exportButton = within(exportCard()).getByRole("button", { name: "Export data" });
    expect(exportButton).toBeDisabled();

    await addIdentifier(user, "a@example.com");
    const reason = within(exportCard()).getByRole("textbox", { name: "Reason" });
    await user.type(reason, "ab");
    expect(exportButton).toBeDisabled();
    await user.type(reason, "c");
    expect(exportButton).toBeEnabled();
  });

  it("warns before any request, sends nothing on Cancel, and downloads the file on confirm", async () => {
    const user = userEvent.setup();
    const consoleSpies = (["log", "info", "warn", "error", "debug"] as const).map((name) =>
      vi.spyOn(console, name),
    );
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const { post } = mockPrivacyApi();
    const { queryClient } = renderPage();
    await screen.findByRole("heading", { level: 1, name: "Privacy" }, LOAD);

    await addIdentifier(user, SUBJECT);
    await user.type(within(exportCard()).getByRole("textbox", { name: "Reason" }), "court order 12");

    await user.click(within(exportCard()).getByRole("button", { name: "Export data" }));
    const dialog = await screen.findByRole("alertdialog", { name: "This file contains personal data" });
    expect(within(dialog).getByText(/original, unredacted transcript text/)).toBeInTheDocument();
    expect(within(dialog).getByText(/approved channel/)).toBeInTheDocument();
    expect(within(dialog).getByText(/written to the audit log/)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(post).not.toHaveBeenCalled();

    await user.click(within(exportCard()).getByRole("button", { name: "Export data" }));
    const again = await screen.findByRole("alertdialog", { name: "This file contains personal data" });
    await user.click(within(again).getByRole("button", { name: "Download export" }));

    expect(await screen.findByText("The export was downloaded.")).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      "/privacy/subject-exports",
      { identifiers: [SUBJECT], reason: "court order 12" },
      { responseType: "blob" },
    );
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    const anchor = click.mock.contexts[0] as HTMLAnchorElement;
    expect(anchor.download).toBe("homayar-subject-export-20261018T101010Z.json");
    expect(anchor.href).toBe("blob:export-1");
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:export-1");

    // The form is cleared, and the identifier was never kept anywhere else.
    expect(tags()).toEqual([]);
    expect(within(exportCard()).getByRole("textbox", { name: "Reason" })).toHaveValue("");
    for (const [, value] of setItem.mock.calls) expect(String(value)).not.toContain(SUBJECT);
    const cached = JSON.stringify([
      queryClient.getQueryCache().getAll().map((query) => [query.queryKey, query.state.data]),
      queryClient.getMutationCache().getAll().map((mutation) => mutation.state.variables),
    ]);
    expect(cached).not.toContain(SUBJECT);
    for (const spy of consoleSpies) {
      for (const call of spy.mock.calls) expect(JSON.stringify(call)).not.toContain(SUBJECT);
    }
  });

  it("shows the identifier message under the identifiers for invalid_subject_identifier", async () => {
    const user = userEvent.setup();
    const { state } = mockPrivacyApi();
    state.exportResult = blobError(422, "invalid_subject_identifier");
    renderPage();
    await screen.findByRole("heading", { level: 1, name: "Privacy" }, LOAD);

    await addIdentifier(user, "Ali");
    await user.type(within(exportCard()).getByRole("textbox", { name: "Reason" }), "court order");
    await user.click(within(exportCard()).getByRole("button", { name: "Export data" }));
    await user.click(await screen.findByRole("button", { name: "Download export" }));

    const group = await within(exportCard()).findByRole("grid", { name: "Identifiers to export" });
    const message = await within(exportCard()).findByText(
      "Each identifier must be exactly one e-mail, phone number, national ID, bank card or IBAN.",
    );
    expect(group.closest(".tag-group")).toContainElement(message);
    expect(createObjectURL).not.toHaveBeenCalled();
    // The form keeps what was typed, so the owner can fix it.
    expect(tags()).toEqual(["Ali"]);
  });

  it("uses the shared mapping for other export errors", async () => {
    const user = userEvent.setup();
    const { state } = mockPrivacyApi();
    state.exportResult = blobError(403, "csrf_failed");
    renderPage();
    await screen.findByRole("heading", { level: 1, name: "Privacy" }, LOAD);

    await addIdentifier(user, "a@example.com");
    await user.type(within(exportCard()).getByRole("textbox", { name: "Reason" }), "court order");
    await user.click(within(exportCard()).getByRole("button", { name: "Export data" }));
    await user.click(await screen.findByRole("button", { name: "Download export" }));
    expect(await within(exportCard()).findByText("Your session needs refreshing; reload the page.")).toBeInTheDocument();
  });
});

describe("PrivacyPage legal-hold register", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("lists the holds with links, names, dates and the review-due chip", async () => {
    const { state } = mockPrivacyApi();
    state.pages = {
      first: {
        items: [
          hold({ media_id: "media-due", review_at: new Date(Date.now() - DAY_MS).toISOString() }),
          hold({
            media_id: "media-gone",
            meeting_id: null,
            meeting_title: null,
            placed_by_user_id: "user-left",
          }),
        ],
        next_cursor: null,
      },
    };
    renderPage();

    const table = await within(registerCard()).findByRole("grid", { name: "Recordings on legal hold" }, LOAD);
    const [, dueRow, goneRow] = within(table).getAllByRole("row");
    expect(within(dueRow).getByRole("link", { name: "Quarterly pricing review" })).toHaveAttribute("href", "/meetings/m-1");
    expect(within(dueRow).getByText("Original recording")).toBeInTheDocument();
    expect(within(dueRow).getByText("litigation")).toBeInTheDocument();
    expect(await within(dueRow).findByText("Sara Ahmadi")).toBeInTheDocument();
    expect(within(dueRow).getByText("Review due")).toBeInTheDocument();

    expect(within(goneRow).getByText("Conversation deleted")).toBeInTheDocument();
    expect(within(goneRow).queryByRole("link")).not.toBeInTheDocument();
    expect(within(goneRow).getByText("Unknown member")).toBeInTheDocument();
    expect(within(goneRow).queryByText("Review due")).not.toBeInTheDocument();
  });

  it("sends no due_before for All holds, now for Review due and now plus 30 days for Due in 30 days", async () => {
    const user = userEvent.setup();
    const { registerParams, state } = mockPrivacyApi();
    state.pages = { first: { items: [], next_cursor: null } };
    renderPage();

    expect(await within(registerCard()).findByText("Nothing is on legal hold.", {}, LOAD)).toBeInTheDocument();
    expect(registerParams[0]).toEqual({ limit: 50 });
    expect(within(registerCard()).getByRole("radio", { name: "All holds" })).toBeChecked();

    const beforeDue = Date.now();
    await user.click(within(registerCard()).getByRole("radio", { name: "Review due" }));
    expect(await within(registerCard()).findByText("No hold is due for review.")).toBeInTheDocument();
    const due = registerParams.at(-1) as { due_before: string; limit: number };
    expect(due.limit).toBe(50);
    expect(Date.parse(due.due_before)).toBeGreaterThanOrEqual(beforeDue);
    expect(Date.parse(due.due_before)).toBeLessThanOrEqual(Date.now());

    const before30 = Date.now();
    await user.click(within(registerCard()).getByRole("radio", { name: "Due in 30 days" }));
    await waitFor(() => expect(registerParams).toHaveLength(3));
    const thirty = registerParams.at(-1) as { due_before: string };
    expect(Date.parse(thirty.due_before)).toBeGreaterThanOrEqual(before30 + 30 * DAY_MS);
    expect(Date.parse(thirty.due_before)).toBeLessThanOrEqual(Date.now() + 30 * DAY_MS);
    expect(thirty.due_before).toMatch(/Z$/);
  });

  it("loads the next page with the cursor and restarts paging when the filter changes", async () => {
    const user = userEvent.setup();
    const { registerParams, state } = mockPrivacyApi();
    state.pages = {
      first: { items: [hold({ media_id: "media-1" })], next_cursor: "cursor-2" },
      "cursor-2": { items: [hold({ media_id: "media-2", meeting_title: "Second page" })], next_cursor: null },
    };
    renderPage();

    await user.click(await within(registerCard()).findByRole("button", { name: "Load more" }, LOAD));
    expect(await within(registerCard()).findByRole("link", { name: "Second page" })).toBeInTheDocument();
    expect(registerParams[1]).toEqual({ limit: 50, cursor: "cursor-2" });
    expect(within(registerCard()).queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();

    await user.click(within(registerCard()).getByRole("radio", { name: "Review due" }));
    await waitFor(() => expect(registerParams).toHaveLength(3));
    expect(registerParams[2]).not.toHaveProperty("cursor");
  });

  it("names each row's button exactly Release and describes it by the conversation", async () => {
    const { state } = mockPrivacyApi();
    state.pages = {
      first: {
        items: [hold(), hold({ media_id: "media-gone", meeting_id: null, meeting_title: null })],
        next_cursor: null,
      },
    };
    renderPage();
    const table = await within(registerCard()).findByRole("grid", { name: "Recordings on legal hold" }, LOAD);
    const [, first, gone] = within(table).getAllByRole("row");
    expect(within(first).getByRole("button", { name: "Release" })).toHaveAccessibleDescription(
      "Quarterly pricing review",
    );
    expect(within(gone).getByRole("button", { name: "Release" })).toHaveAccessibleDescription(
      "Conversation deleted",
    );
  });

  it("keeps the rows and says so next to Load more when the next page fails", async () => {
    const user = userEvent.setup();
    const { state } = mockPrivacyApi();
    state.pages = {
      first: { items: [hold({ media_id: "media-1" })], next_cursor: "cursor-2" },
    };
    state.failCursor = "cursor-2";
    renderPage();

    await user.click(await within(registerCard()).findByRole("button", { name: "Load more" }, LOAD));
    expect(await within(registerCard()).findByText("More holds could not be loaded. Try again.")).toBeInTheDocument();
    // The first page is still listed, and the owner can try again.
    expect(within(registerCard()).getByRole("link", { name: "Quarterly pricing review" })).toBeInTheDocument();
    expect(within(registerCard()).getByRole("button", { name: "Load more" })).toBeEnabled();
    expect(within(registerCard()).queryByText("The request failed.")).not.toBeInTheDocument();
  });

  it("keeps the rows when a background refetch fails", async () => {
    const { state } = mockPrivacyApi();
    const { queryClient } = renderPage();
    await within(registerCard()).findByRole("link", { name: "Quarterly pricing review" }, LOAD);

    state.registerError = apiError(500, "internal_error");
    await act(() => queryClient.refetchQueries({ queryKey: privacyQueryKeys.legalHoldsRoot() }));
    expect(
      await within(registerCard()).findByText("The legal holds could not be refreshed. The list may be out of date."),
    ).toBeInTheDocument();
    expect(within(registerCard()).getByRole("link", { name: "Quarterly pricing review" })).toBeInTheDocument();
  });

  describe("west of UTC", () => {
    beforeEach(() => {
      vi.stubEnv("TZ", "America/Los_Angeles");
    });
    afterEach(() => {
      vi.unstubAllEnvs();
    });

    it("shows the review date as its UTC day, without a time", async () => {
      // The local zone is in force: midnight UTC is still the 15th here.
      expect(new Date("2027-01-16T00:00:00Z").getDate()).toBe(15);
      const { state } = mockPrivacyApi();
      state.pages = {
        first: { items: [hold({ review_at: "2027-01-16T00:00:00Z" })], next_cursor: null },
      };
      renderPage();
      const table = await within(registerCard()).findByRole("grid", { name: "Recordings on legal hold" }, LOAD);
      const review = table.querySelector('time[datetime="2027-01-16T00:00:00Z"]');
      expect(review).toHaveTextContent(/^Jan 16, 2027$/);
    });
  });

  it("shows the error state when the register cannot be read", async () => {
    const { state } = mockPrivacyApi();
    state.registerError = apiError(500, "internal_error");
    renderPage();
    expect(await within(registerCard()).findByRole("alert", {}, LOAD)).toBeInTheDocument();
  });

  it("releases a row with a reason of at least 3 characters and reads the register again", async () => {
    const user = userEvent.setup();
    const { post, registerParams } = mockPrivacyApi();
    renderPage();

    await user.click(
      await within(registerCard()).findByRole("button", { name: "Release" }, LOAD),
    );
    const dialog = await screen.findByRole("dialog", { name: "Release the legal hold" });
    const confirm = within(dialog).getByRole("button", { name: "Release hold" });
    const reason = within(dialog).getByRole("textbox", { name: "Reason" });
    await user.type(reason, "ok");
    expect(confirm).toBeDisabled();
    await user.type(reason, "!");
    expect(confirm).toBeEnabled();
    const readsBefore = registerParams.length;
    await user.click(confirm);

    expect(await screen.findByText("The legal hold was released.")).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith("/media/media-1/legal-hold/release", { reason: "ok!" });
    expect(await within(registerCard()).findByText("Nothing is on legal hold.")).toBeInTheDocument();
    expect(registerParams.length).toBeGreaterThan(readsBefore);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  const RELEASE_ERRORS: [string, unknown, string][] = [
    ["not_held", apiError(409, "not_held"), "This recording is not on legal hold. It may have been released already."],
    ["media_not_found", apiError(404, "media_not_found"), "This recording no longer exists."],
    ["permission_denied", apiError(403, "permission_denied"), "Only the workspace owner can do this."],
    ["another code", apiError(429, "rate_limited"), "Too many requests; try again shortly."],
  ];

  it.each(RELEASE_ERRORS)("keeps the release dialog open with the message for %s", async (_name, error, message) => {
    const user = userEvent.setup();
    const { state, registerParams } = mockPrivacyApi();
    state.releaseError = error;
    renderPage();

    await user.click(
      await within(registerCard()).findByRole("button", { name: "Release" }, LOAD),
    );
    const dialog = await screen.findByRole("dialog", { name: "Release the legal hold" });
    await user.type(within(dialog).getByRole("textbox", { name: "Reason" }), "matter closed");
    const readsBefore = registerParams.length;
    await user.click(within(dialog).getByRole("button", { name: "Release hold" }));

    expect(await within(dialog).findByText(message)).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Release the legal hold" })).toBeInTheDocument();
    expect(within(dialog).getByRole("textbox", { name: "Reason" })).toHaveValue("matter closed");
    if (_name === "not_held") {
      // The register is read again, since the hold is already gone.
      await waitFor(() => expect(registerParams.length).toBeGreaterThan(readsBefore));
    }
  });
});

describe("PrivacyPage dialogs", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("opens the export and release dialogs without React Aria's PressResponder warning", async () => {
    const user = userEvent.setup();
    const warn = vi.spyOn(console, "warn");
    mockPrivacyApi();
    renderPage();
    await screen.findByRole("heading", { level: 1, name: "Privacy" }, LOAD);

    await addIdentifier(user, "a@example.com");
    await user.type(within(exportCard()).getByRole("textbox", { name: "Reason" }), "court order");
    await user.click(within(exportCard()).getByRole("button", { name: "Export data" }));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());

    await user.click(await within(registerCard()).findByRole("button", { name: "Release" }, LOAD));
    await screen.findByRole("dialog", { name: "Release the legal hold" });

    const pressWarnings = warn.mock.calls.filter((call) => String(call[0]).includes("PressResponder"));
    expect(pressWarnings).toEqual([]);
  });
});

describe("PrivacyPage audit links and language", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("links each privacy event type to the audit log", async () => {
    mockPrivacyApi();
    renderPage();
    const card = await screen.findByRole("region", { name: "Audit history" }, LOAD);
    expect(within(card).getByRole("link", { name: "Export history" })).toHaveAttribute(
      "href",
      "/admin/audit?event_type=subject.exported",
    );
    expect(within(card).getByRole("link", { name: "Holds placed" })).toHaveAttribute(
      "href",
      "/admin/audit?event_type=media.legal_hold_placed",
    );
    expect(within(card).getByRole("link", { name: "Holds released" })).toHaveAttribute(
      "href",
      "/admin/audit?event_type=media.legal_hold_released",
    );
  });

  it("shows the shared-links card between the holds register and the audit links", async () => {
    const { shareParams } = mockPrivacyApi();
    renderPage();

    const links = await screen.findByRole("region", { name: "Shared links" }, LOAD);
    expect(links).toBeInTheDocument();
    await waitFor(() => expect(shareParams).toHaveLength(1), LOAD);
    expect(shareParams[0]).toEqual({ status: "active", limit: 50 });
    const headings = await screen.findAllByRole("heading", { level: 2 });
    expect(headings.map((heading) => heading.textContent)).toEqual([
      "Data subject export",
      "Legal holds",
      "Shared links",
      "Audit history",
    ]);
  });

  it("speaks Persian", async () => {
    const { state } = mockPrivacyApi();
    state.pages = { first: { items: [], next_cursor: null } };
    renderPage("fa");
    expect(await screen.findByRole("heading", { level: 1, name: "حریم خصوصی" }, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "خروجی داده‌های یک شخص" })).toBeInTheDocument();
    expect(await screen.findByText("هیچ موردی در توقیف قانونی نیست.", {}, LOAD)).toBeInTheDocument();
  });
});
