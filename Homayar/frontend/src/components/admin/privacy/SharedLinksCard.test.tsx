import { afterEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient } from "@tanstack/react-query";
import type { AxiosRequestConfig, AxiosResponse } from "axios";

import SharedLinksCard from "@/components/admin/privacy/SharedLinksCard";
import api from "@/lib/api";
import { formatDate, formatNumber } from "@/lib/format";
import { privacyQueryKeys } from "@/lib/privacy-api";
import { apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { MemberListResponse } from "@/types/platform";
import type { AdminShareItem, AdminShareListResponse } from "@/types/privacy";

// The card waits for two queries; slower machines need more than the 1 s default.
const LOAD = { timeout: 5000 };
/** A token no fixture mentions, so finding it anywhere is a leak. */
const SECRET = "tok-SECRET";

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

function share(overrides: Partial<AdminShareItem> = {}): AdminShareItem {
  return {
    id: "share-1",
    meeting_id: "m-1",
    meeting_title: "Customer portal login issue #10",
    audience: "workspace",
    status: "active",
    created_by_user_id: "user-1",
    created_at: "2026-10-01T09:00:00Z",
    expires_at: "2026-10-02T09:00:00Z",
    access_count: 1234,
    last_accessed_at: null,
    revoked_at: null,
    ...overrides,
  };
}

/**
 * Answers the card's reads from mutable state. `pages` maps a key
 * `<status>:<cursor>` (or the bare `"first"` for the default filter's first
 * page) to a links page; every register request is recorded. A revoke
 * removes its share from every page, the way the server does.
 */
function mockSharesApi() {
  const state = {
    pages: { first: { items: [share()], next_cursor: null } } as Record<string, AdminShareListResponse>,
    listError: null as unknown,
    /** A cursor whose page fails, for a failed "Load more". */
    failCursor: null as string | null,
    /** The error code a failing cursor answers with (default: internal_error). */
    failCursorCode: null as string | null,
    revokeError: null as unknown,
    /** A delay in ms before each page-one read answers, for a slow re-read. */
    firstPageDelay: 0,
  };
  const listParams: Record<string, unknown>[] = [];
  const deletes: string[] = [];
  const get = vi.spyOn(api, "get").mockImplementation(async (url: string, config?: AxiosRequestConfig) => {
    if (url === "/admin/shares") {
      const params = (config?.params ?? {}) as Record<string, unknown>;
      listParams.push(params);
      if (state.listError) throw state.listError;
      if (state.failCursor !== null && params.cursor === state.failCursor) {
        throw apiError(400, state.failCursorCode ?? "internal_error");
      }
      const cursor = typeof params.cursor === "string" ? params.cursor : "first";
      if (cursor === "first" && state.firstPageDelay > 0) {
        await new Promise((resolve) => setTimeout(resolve, state.firstPageDelay));
      }
      const key = `${typeof params.status === "string" ? params.status : "all"}:${cursor}`;
      // A status-specific page wins; otherwise the bare cursor (or "first")
      // page serves every filter, as in the earlier tests.
      const page = state.pages[key] ?? state.pages[cursor];
      return okResponse(page ?? { items: [], next_cursor: null });
    }
    if (url === "/members") return okResponse(MEMBERS);
    throw apiError(404, "not_found");
  });
  const remove = vi.spyOn(api, "delete").mockImplementation(async (url: string) => {
    if (url.startsWith("/meetings/") && url.includes("/shares/")) {
      if (state.revokeError) throw state.revokeError;
      deletes.push(url);
      const shareId = url.split("/").pop() as string;
      state.pages = Object.fromEntries(
        Object.entries(state.pages).map(([cursor, page]) => [
          cursor,
          { ...page, items: page.items.filter((item) => item.id !== shareId) },
        ]),
      );
      return okResponse(undefined, 204);
    }
    throw apiError(404, "not_found");
  });
  return { state, get, remove, listParams, deletes };
}

function renderCard(lang: "fa" | "en" = "en") {
  return renderConsole(<SharedLinksCard />, { lang });
}

function card(lang: "fa" | "en" = "en") {
  return screen.getByRole("region", { name: lang === "fa" ? "لینک‌های اشتراک" : "Shared links" });
}

async function sharesTable() {
  return within(card()).findByRole("grid", { name: "Shared links of the workspace" }, LOAD);
}

describe("SharedLinksCard", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("asks for the active links first and shows every column of a row", async () => {
    const { state, listParams } = mockSharesApi();
    state.pages = {
      first: {
        items: [share(), share({ id: "share-2", meeting_id: "m-2", meeting_title: "", expires_at: null, created_by_user_id: "user-left", access_count: 0 })],
        next_cursor: null,
      },
    };
    renderCard();

    expect(await within(card()).findByRole("radio", { name: "Active" }, LOAD)).toBeChecked();
    expect(listParams[0]).toEqual({ status: "active", limit: 50 });

    const table = await sharesTable();
    const [, titled, untitled] = within(table).getAllByRole("row");
    expect(within(titled).getByRole("link", { name: "Customer portal login issue #10" })).toHaveAttribute(
      "href",
      "/meetings/m-1",
    );
    expect(within(titled).getByText("Organization members")).toBeInTheDocument();
    expect(within(titled).getByText("Active")).toBeInTheDocument();
    expect(within(titled).getByText("Sara Ahmadi")).toBeInTheDocument();
    expect(within(titled).getByText(formatDate("2026-10-01T09:00:00Z", "en"))).toBeInTheDocument();
    expect(within(titled).getByText(formatDate("2026-10-02T09:00:00Z", "en"))).toBeInTheDocument();
    expect(within(titled).getByText(formatNumber(1234, "en"))).toBeInTheDocument();

    expect(within(untitled).getByRole("link", { name: "Untitled conversation" })).toHaveAttribute(
      "href",
      "/meetings/m-2",
    );
    expect(within(untitled).getByText("Never")).toBeInTheDocument();
    expect(within(untitled).getByText("Unknown member")).toBeInTheDocument();
  });

  it("labels each audience kind and shows an unknown one as it is", async () => {
    const { state } = mockSharesApi();
    state.pages = {
      first: {
        items: [
          share({ id: "share-link", audience: "link" }),
          share({ id: "share-recipients", audience: "recipients" }),
          share({ id: "share-other", audience: "signed", meeting_title: "Signed kind" }),
        ],
        next_cursor: null,
      },
    };
    renderCard();

    const table = await sharesTable();
    const [, link, recipients, other] = within(table).getAllByRole("row");
    expect(within(link).getByText("Public link")).toBeInTheDocument();
    expect(within(recipients).getByText("Named recipients")).toBeInTheDocument();
    expect(within(other).getByText("signed")).toBeInTheDocument();
  });

  it.each([
    ["Expired", { status: "expired", limit: 50 }, "No link has expired."],
    ["Revoked", { status: "revoked", limit: 50 }, "No link has been revoked."],
    ["All links", { limit: 50 }, "No conversation has been shared."],
  ] as const)("changes the filter to %s from page one", async (name, params, emptyText) => {
    const user = userEvent.setup();
    const { state, listParams } = mockSharesApi();
    state.pages = { first: { items: [], next_cursor: null } };
    renderCard();

    expect(await within(card()).findByText("No link is shared right now.", {}, LOAD)).toBeInTheDocument();
    await user.click(within(card()).getByRole("radio", { name }));
    expect(await within(card()).findByText(emptyText)).toBeInTheDocument();
    expect(listParams.at(-1)).toEqual(params);
    expect(listParams.at(-1)).not.toHaveProperty("cursor");
  });

  it("loads the next page with the cursor and stops when there is none", async () => {
    const user = userEvent.setup();
    const { state, listParams } = mockSharesApi();
    state.pages = {
      first: { items: [share()], next_cursor: "cursor-2" },
      "cursor-2": { items: [share({ id: "share-2", meeting_title: "Second page" })], next_cursor: null },
    };
    renderCard();

    await user.click(await within(card()).findByRole("button", { name: "Load more" }, LOAD));
    expect(await within(card()).findByRole("link", { name: "Second page" })).toBeInTheDocument();
    expect(listParams[1]).toEqual({ status: "active", limit: 50, cursor: "cursor-2" });
    expect(within(card()).queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("keeps the rows and says so next to Load more when the next page fails", async () => {
    const user = userEvent.setup();
    const { state } = mockSharesApi();
    state.pages = { first: { items: [share()], next_cursor: "cursor-2" } };
    state.failCursor = "cursor-2";
    state.failCursorCode = "internal_error";
    renderCard();

    await user.click(await within(card()).findByRole("button", { name: "Load more" }, LOAD));
    expect(await within(card()).findByText("More links could not be loaded. Try again.")).toBeInTheDocument();
    expect(within(card()).getByRole("link", { name: "Customer portal login issue #10" })).toBeInTheDocument();
    expect(within(card()).getByRole("button", { name: "Load more" })).toBeEnabled();
  });

  it("restarts the list from page one when a next page's cursor is stale", async () => {
    const user = userEvent.setup();
    const { state, listParams } = mockSharesApi();
    state.pages = { first: { items: [share()], next_cursor: "bad" } };
    state.failCursor = "bad";
    state.failCursorCode = "invalid_cursor";
    renderCard();
    await within(card()).findByRole("button", { name: "Load more" }, LOAD);

    // A real server takes time, so the page-one re-read answers late.
    state.firstPageDelay = 50;
    await user.click(within(card()).getByRole("button", { name: "Load more" }));

    // The register is read again from page one, so the next request carries no cursor.
    await waitFor(() => expect(listParams).toHaveLength(3));
    expect(listParams[2]).toEqual({ status: "active", limit: 50 });
    expect(listParams.filter((params) => params.cursor === "bad")).toHaveLength(1);
    // Wait for the re-read to answer; the notice must still be there after it.
    expect(await within(card()).findByRole("link", { name: "Customer portal login issue #10" }, LOAD)).toBeInTheDocument();
    expect(within(card()).getByRole("alert")).toHaveTextContent("The list changed. Choose the filter again.");
  });

  it("shows a later Load more failure, not the list-changed notice", async () => {
    const user = userEvent.setup();
    const { state } = mockSharesApi();
    state.pages = { first: { items: [share()], next_cursor: "bad" } };
    state.failCursor = "bad";
    state.failCursorCode = "invalid_cursor";
    renderCard();
    await within(card()).findByRole("button", { name: "Load more" }, LOAD);

    // After the restart, page one points to a fresh cursor whose page fails.
    state.pages = { first: { items: [share()], next_cursor: "cursor-2" } };
    await user.click(within(card()).getByRole("button", { name: "Load more" }));
    expect(await within(card()).findByText("The list changed. Choose the filter again.", {}, LOAD)).toBeInTheDocument();
    state.failCursor = "cursor-2";
    state.failCursorCode = "internal_error";

    await user.click(await within(card()).findByRole("button", { name: "Load more" }, LOAD));
    await waitFor(() =>
      expect(within(card()).getByRole("alert")).toHaveTextContent("More links could not be loaded. Try again."),
    );
    expect(within(card()).queryByText("The list changed. Choose the filter again.")).not.toBeInTheDocument();
  });

  it("shows a later background refetch failure, not the list-changed notice", async () => {
    const user = userEvent.setup();
    const { state } = mockSharesApi();
    state.pages = { first: { items: [share()], next_cursor: "bad" } };
    state.failCursor = "bad";
    state.failCursorCode = "invalid_cursor";
    const { queryClient } = renderCard();
    await user.click(await within(card()).findByRole("button", { name: "Load more" }, LOAD));
    expect(await within(card()).findByText("The list changed. Choose the filter again.", {}, LOAD)).toBeInTheDocument();
    await within(card()).findByRole("link", { name: "Customer portal login issue #10" }, LOAD);

    state.listError = apiError(500, "internal_error");
    await act(() => queryClient.refetchQueries({ queryKey: privacyQueryKeys.sharesRoot() }));
    await waitFor(() =>
      expect(within(card()).getByRole("alert")).toHaveTextContent(
        "The shared links could not be refreshed. The list may be out of date.",
      ),
    );
    expect(within(card()).getByRole("link", { name: "Customer portal login issue #10" })).toBeInTheDocument();
  });

  it("reads a filter's list from page one again, not its cached pages", async () => {
    const user = userEvent.setup();
    const { state, listParams } = mockSharesApi();
    state.pages = {
      first: { items: [share()], next_cursor: "cursor-2" },
      "cursor-2": { items: [share({ id: "share-2", meeting_title: "Second page" })], next_cursor: null },
      // Expired has no links of its own.
      "expired:first": { items: [], next_cursor: null },
    };
    renderConsole(<SharedLinksCard />, {
      lang: "en",
      queryClient: new QueryClient({ defaultOptions: { queries: { staleTime: 10000, retry: false } } }),
    });

    await user.click(await within(card()).findByRole("button", { name: "Load more" }, LOAD));
    expect(await within(card()).findByRole("link", { name: "Second page" })).toBeInTheDocument();

    await user.click(within(card()).getByRole("radio", { name: "Expired" }));
    expect(await within(card()).findByText("No link has expired.")).toBeInTheDocument();

    const readsBefore = listParams.length;
    await user.click(within(card()).getByRole("radio", { name: "Active" }));
    expect(await within(card()).findByRole("link", { name: "Customer portal login issue #10" })).toBeInTheDocument();
    expect(within(card()).queryByRole("link", { name: "Second page" })).not.toBeInTheDocument();
    await waitFor(() => expect(listParams.length).toBeGreaterThan(readsBefore));
    expect(listParams.at(-1)).toEqual({ status: "active", limit: 50 });
  });

  it("shows the invalid-cursor message when the list cannot be read", async () => {
    const { state } = mockSharesApi();
    state.listError = apiError(400, "invalid_cursor");
    renderCard();

    expect(await within(card()).findByRole("alert", {}, LOAD)).toHaveTextContent(
      "The list changed. Choose the filter again.",
    );
  });

  it("keeps the rows when a background refetch fails", async () => {
    const { state } = mockSharesApi();
    const { queryClient } = renderCard();
    await within(card()).findByRole("link", { name: "Customer portal login issue #10" }, LOAD);

    state.listError = apiError(500, "internal_error");
    await act(() => queryClient.refetchQueries({ queryKey: privacyQueryKeys.sharesRoot() }));
    expect(
      await within(card()).findByText("The shared links could not be refreshed. The list may be out of date."),
    ).toBeInTheDocument();
    expect(within(card()).getByRole("link", { name: "Customer portal login issue #10" })).toBeInTheDocument();
  });

  it("revokes only active links, sends nothing on Cancel and one DELETE on confirm", async () => {
    const user = userEvent.setup();
    const { state, remove, listParams } = mockSharesApi();
    state.pages = {
      first: {
        items: [
          share({ id: "share-active" }),
          share({ id: "share-expired", status: "expired", meeting_title: "An expired link" }),
          share({ id: "share-revoked", status: "revoked", meeting_title: "A revoked link" }),
          share({ id: "share-paused", status: "paused", meeting_title: "An unknown status" }),
        ],
        next_cursor: null,
      },
    };
    renderCard();

    const table = await sharesTable();
    const [, active, expired, revoked, paused] = within(table).getAllByRole("row");
    expect(within(active).getByRole("button", { name: "Revoke" })).toHaveAccessibleDescription(
      "Customer portal login issue #10",
    );
    for (const row of [expired, revoked, paused]) {
      expect(within(row).queryByRole("button", { name: "Revoke" })).not.toBeInTheDocument();
    }

    await user.click(within(active).getByRole("button", { name: "Revoke" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Revoke this link?" });
    const title = within(dialog).getByText("Customer portal login issue #10", { selector: "bdi" });
    expect(title.closest("p")).toBeInstanceOf(HTMLParagraphElement);
    const warning = within(dialog).getByText(/can no longer open it/);
    expect(warning.closest("p")).toBeInstanceOf(HTMLParagraphElement);
    // The title and the warning are two paragraphs, not one run-in sentence.
    expect(warning.closest("p")).not.toContainElement(title);
    expect(warning.closest("p")).toHaveTextContent(
      /^People who have the link can no longer open it\. This cannot be undone\.$/,
    );
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog", { name: "Revoke this link?" })).not.toBeInTheDocument(),
    );
    expect(remove).not.toHaveBeenCalled();
    expect(within(card()).getByRole("link", { name: "Customer portal login issue #10" })).toBeInTheDocument();

    const readsBefore = listParams.length;
    await user.click(within(active).getByRole("button", { name: "Revoke" }));
    await user.click(within(await screen.findByRole("alertdialog", { name: "Revoke this link?" })).getByRole("button", { name: "Revoke link" }));

    expect(await screen.findByText("Link revoked.")).toBeInTheDocument();
    expect(remove).toHaveBeenCalledTimes(1);
    expect(remove).toHaveBeenCalledWith("/meetings/m-1/shares/share-active");
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog", { name: "Revoke this link?" })).not.toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(within(card()).queryByRole("link", { name: "Customer portal login issue #10" })).not.toBeInTheDocument(),
    );
    expect(listParams.length).toBeGreaterThan(readsBefore);
  });

  it("shows the not-found text and refreshes the list when the link is gone", async () => {
    const user = userEvent.setup();
    const { state, listParams } = mockSharesApi();
    state.revokeError = apiError(404, "share_not_found");
    renderCard();

    await user.click(await within(card()).findByRole("button", { name: "Revoke" }, LOAD));
    const readsBefore = listParams.length;
    await user.click(within(await screen.findByRole("alertdialog", { name: "Revoke this link?" })).getByRole("button", { name: "Revoke link" }));

    expect(await screen.findByText("This link no longer exists. The list has been refreshed.")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog", { name: "Revoke this link?" })).not.toBeInTheDocument(),
    );
    await waitFor(() => expect(listParams.length).toBeGreaterThan(readsBefore));
  });

  it("keeps the dialog open with a failure text on any other error", async () => {
    const user = userEvent.setup();
    const { state } = mockSharesApi();
    state.revokeError = apiError(418, "teapot");
    renderCard();

    await user.click(await within(card()).findByRole("button", { name: "Revoke" }, LOAD));
    await user.click(within(await screen.findByRole("alertdialog", { name: "Revoke this link?" })).getByRole("button", { name: "Revoke link" }));

    const dialog = screen.getByRole("alertdialog", { name: "Revoke this link?" });
    expect(await within(dialog).findByText(/^Revoking the link failed/)).toBeInTheDocument();
  });

  it("cannot close the dialog while the revoke is pending", async () => {
    const user = userEvent.setup();
    mockSharesApi();
    // A no-op until the revoke request starts; the test releases it later.
    let resolveRevoke: (response: AxiosResponse<undefined>) => void = () => {};
    const remove = vi.spyOn(api, "delete").mockImplementation(
      () =>
        new Promise<AxiosResponse<undefined>>((resolve) => {
          resolveRevoke = resolve;
        }),
    );
    renderCard();

    await user.click(await within(card()).findByRole("button", { name: "Revoke" }, LOAD));
    const dialog = await screen.findByRole("alertdialog", { name: "Revoke this link?" });
    const confirm = within(dialog).getByRole("button", { name: "Revoke link" });
    await user.click(confirm);

    expect(confirm).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("alertdialog", { name: "Revoke this link?" })).toBeInTheDocument();

    resolveRevoke(okResponse(undefined, 204));
    expect(await screen.findByText("Link revoked.")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog", { name: "Revoke this link?" })).not.toBeInTheDocument(),
    );
    expect(remove).toHaveBeenCalledWith("/meetings/m-1/shares/share-1");
  });

  it("opens the revoke dialog without React Aria's PressResponder warning", async () => {
    const user = userEvent.setup();
    const warn = vi.spyOn(console, "warn");
    mockSharesApi();
    renderCard();

    await user.click(await within(card()).findByRole("button", { name: "Revoke" }, LOAD));
    await user.click(within(await screen.findByRole("alertdialog", { name: "Revoke this link?" })).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog", { name: "Revoke this link?" })).not.toBeInTheDocument(),
    );
    // A second open, as in the e2e, must not warn either.
    await user.click(within(card()).getByRole("button", { name: "Revoke" }));
    await screen.findByRole("alertdialog", { name: "Revoke this link?" });

    const pressWarnings = warn.mock.calls.filter((call) => String(call[0]).includes("PressResponder"));
    expect(pressWarnings).toEqual([]);
  });

  it("never renders a token or share URL the server sends", async () => {
    const { state } = mockSharesApi();
    // The server must not send these; if it does, they stay out of the DOM.
    const leaked = { token: SECRET, url: `https://x/s/${SECRET}` } as unknown as Partial<AdminShareItem>;
    state.pages = { first: { items: [share(leaked)], next_cursor: null } };
    renderCard();

    expect(await within(card()).findByRole("link", { name: "Customer portal login issue #10" }, LOAD)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(SECRET);
    expect(document.body.innerHTML).not.toContain(SECRET);
  });

  it("speaks Persian", async () => {
    const { state } = mockSharesApi();
    state.pages = { first: { items: [share({ expires_at: null, access_count: 1234 })], next_cursor: null } };
    renderCard("fa");

    expect(await within(card("fa")).findByRole("link", { name: "Customer portal login issue #10" }, LOAD)).toBeInTheDocument();
    for (const name of ["فعال", "منقضی‌شده", "لغوشده", "همهٔ لینک‌ها"]) {
      expect(within(card("fa")).getByRole("radio", { name })).toBeInTheDocument();
    }
    const table = within(card("fa")).getByRole("grid", { name: "لینک‌های اشتراک فضای کاری" });
    const [, row] = within(table).getAllByRole("row");
    expect(within(row).getByText("اعضای سازمان")).toBeInTheDocument();
    expect(within(row).getByText("فعال")).toBeInTheDocument();
    expect(within(row).getByText("بدون انقضا")).toBeInTheDocument();
    expect(within(row).getByText(formatDate("2026-10-01T09:00:00Z", "fa"))).toBeInTheDocument();
    expect(within(row).getByText(formatNumber(1234, "fa"))).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "لغو" })).toBeInTheDocument();
  });
});
