import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ApiKeysPage from "@/components/admin/ApiKeysPage";
import api from "@/lib/api";
import {
  FIXTURE_USER,
  FIXTURE_WORKSPACE,
  apiError,
  okResponse,
  renderConsole,
  testQueryClient,
} from "@/test/fixtures";
import type { QueryClient } from "@tanstack/react-query";
import type { ApiKey, CreatedApiKey, MemberListResponse } from "@/types/platform";

const T = "2026-09-24T12:00:00Z";

/** A raw key whose secret part never appears anywhere but the created view. */
const RAW_KEY = "hk_abcd1234.SHOW-ONCE-SECRET";

function key(overrides: Partial<ApiKey> = {}): ApiKey {
  return {
    id: "key-1",
    name: "CRM integration",
    key_prefix: "abcd1234",
    scopes: ["meeting:read"],
    expires_at: null,
    revoked_at: null,
    last_used_at: null,
    created_by_user_id: FIXTURE_USER.id,
    created_at: T,
    ...overrides,
  };
}

function created(overrides: Partial<CreatedApiKey> = {}): CreatedApiKey {
  return { ...key({ id: "key-2", name: "Office PBX", key_prefix: "ffff0000" }), key: RAW_KEY, ...overrides };
}

const MEMBERS: MemberListResponse = {
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

type KeysAnswer = { items: ApiKey[] } | Error;

interface MockOptions {
  /** One answer per GET /api-keys; the last one repeats for refetches. */
  keys?: KeysAnswer[];
  members?: MemberListResponse | Error;
  post?: CreatedApiKey | Error;
  delete?: Error | "ok";
}

/** Serves /api-keys and /members, and records every POST and DELETE. */
function mockApi(options: MockOptions = {}) {
  const calls = { post: [] as unknown[][], delete: [] as string[], get: [] as string[] };
  const answers = options.keys ?? [{ items: [] }];
  let index = 0;
  vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    calls.get.push(url);
    if (url === "/members") {
      const answer = options.members ?? MEMBERS;
      if (answer instanceof Error) throw answer;
      return okResponse(answer);
    }
    if (url === "/api-keys") {
      const answer = answers[Math.min(index, answers.length - 1)];
      index += 1;
      if (answer instanceof Error) throw answer;
      return okResponse(answer);
    }
    throw apiError(404, "not_found");
  });
  vi.spyOn(api, "post").mockImplementation(async (url: string, body?: unknown) => {
    calls.post.push([url, body]);
    if (options.post === undefined) throw apiError(500, "unexpected_post");
    if (options.post instanceof Error) throw options.post;
    return okResponse(options.post, 201);
  });
  vi.spyOn(api, "delete").mockImplementation(async (url: string) => {
    calls.delete.push(url);
    if (options.delete instanceof Error) throw options.delete;
    return okResponse(undefined, 204);
  });
  return calls;
}

function renderPage(options: { lang?: "fa" | "en"; queryClient?: QueryClient } = {}) {
  return renderConsole(<ApiKeysPage />, {
    route: "/admin/api-keys",
    lang: options.lang ?? "en",
    auth: {
      status: "authenticated",
      user: FIXTURE_USER,
      workspace: FIXTURE_WORKSPACE,
      workspaces: [FIXTURE_WORKSPACE],
    },
    queryClient: options.queryClient,
  });
}

/** Everything a storage holds, as one string, so a leaked key shows up. */
function storageText(storage: Storage): string {
  const parts: string[] = [];
  for (let i = 0; i < storage.length; i += 1) {
    const storageKey = storage.key(i);
    if (storageKey !== null) parts.push(storageKey, storage.getItem(storageKey) ?? "");
  }
  return parts.join("\n");
}

async function openCreateForm(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Create API key" }));
  return await screen.findByRole("dialog", { name: "Create API key" });
}

/** Fills the form with a valid draft, leaving the submit to the caller. */
async function fillForm(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("textbox", { name: "Name" }));
  await user.keyboard("Office PBX");
  await user.click(screen.getByRole("checkbox", { name: "Read meetings, transcripts and summaries" }));
}

describe("ApiKeysPage list", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the heading, the intro, the docs link and the create button", async () => {
    mockApi();
    renderPage();
    expect(await screen.findByRole("heading", { level: 1, name: "API keys" })).toBeInTheDocument();
    expect(screen.getByText(/A key acts for the person who created it/)).toBeInTheDocument();
    const docs = screen.getByRole("link", { name: "API documentation" });
    expect(docs).toHaveAttribute("href", "/api/docs");
    expect(docs).toHaveAttribute("target", "_blank");
    expect(docs.getAttribute("rel")).toContain("noopener");
    expect(docs.getAttribute("rel")).toContain("noreferrer");
    expect(screen.getByRole("button", { name: "Create API key" })).toBeEnabled();
  });

  it("loads while the list is pending", async () => {
    const never = new Promise(() => undefined);
    vi.spyOn(api, "get").mockImplementation(() => never as never);
    renderPage();
    expect(await screen.findByText("Loading API keys")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows a role=alert through ErrorState when GET /api-keys fails, and the create button stays usable", async () => {
    mockApi({ keys: [apiError(500, "internal_error")] });
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert).toBeInTheDocument();
    expect(alert.textContent).not.toContain("fixture internal_error");
    expect(screen.getByRole("button", { name: "Create API key" })).toBeEnabled();
  });

  it("shows the empty state when there are no keys", async () => {
    mockApi({ keys: [{ items: [] }] });
    renderPage();
    expect(await screen.findByText("No API keys yet")).toBeInTheDocument();
  });

  it("renders one row per key in server order, with prefix, scopes, creator, dates and status", async () => {
    const days = (n: number) => new Date(Date.now() + n * 86_400_000).toISOString();
    mockApi({
      keys: [
        {
          items: [
            key({ id: "a", name: "Active key", last_used_at: T, expires_at: days(90) }),
            key({ id: "b", name: "Soon key", expires_at: days(13), created_by_user_id: "user-gone" }),
            key({ id: "c", name: "Old key", expires_at: days(-1) }),
            key({ id: "d", name: "Dead key", revoked_at: days(-2) }),
          ],
        },
      ],
    });
    renderPage();
    const table = await screen.findByRole("grid", { name: "Workspace API keys" });
    expect(table).toBeInTheDocument();
    // Every column is named, including the actions column (React Aria drops
    // aria-label on a column, so its text has to be inside it).
    expect(screen.getByRole("columnheader", { name: "Name" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Prefix" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Scopes" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Created by" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Last used" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Expires" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Status" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Actions" })).toBeInTheDocument();
    expect(screen.getAllByText("hk_abcd1234…").length).toBe(4);
    expect(screen.getAllByText("meeting:read").length).toBe(4);
    // Dates: null is "Never", a value is formatted.
    expect(screen.getAllByText("Never").length).toBeGreaterThanOrEqual(1);
    // Creator resolution: the member's name, and the fallback for a removed creator.
    expect(screen.getAllByText("Person").length).toBe(3);
    expect(screen.getByText("Former member")).toBeInTheDocument();
    // Status chips and data-status, in server order.
    const active = screen.getByRole("row", { name: /Active key/ });
    expect(active).toHaveAttribute("data-status", "active");
    expect(within(active).getByText("Active")).toBeInTheDocument();
    const soon = screen.getByRole("row", { name: /Soon key/ });
    expect(within(soon).getByText("Expires in 13 days")).toBeInTheDocument();
    const expired = screen.getByRole("row", { name: /Old key/ });
    expect(expired).toHaveAttribute("data-status", "expired");
    expect(within(expired).getByText("Expired")).toBeInTheDocument();
    const revoked = screen.getByRole("row", { name: /Dead key/ });
    expect(revoked).toHaveAttribute("data-status", "revoked");
    expect(within(revoked).getByText("Revoked")).toBeInTheDocument();
    // Only the active and soon-to-expire keys can be revoked.
    expect(within(active).getByRole("button", { name: "Revoke Active key" })).toBeInTheDocument();
    expect(within(soon).getByRole("button", { name: "Revoke Soon key" })).toBeInTheDocument();
    expect(within(expired).queryByRole("button", { name: /Revoke/ })).not.toBeInTheDocument();
    expect(within(revoked).queryByRole("button", { name: /Revoke/ })).not.toBeInTheDocument();
    // A far expiry shows no warning chip.
    expect(within(active).queryByText(/Expires in/)).not.toBeInTheDocument();
  });

  it("shows — as creator while /members fails or loads, and never blanks the key list", async () => {
    mockApi({ keys: [{ items: [key()] }], members: apiError(500, "internal_error") });
    renderPage();
    expect(await screen.findByText("hk_abcd1234…")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("carries the Persian texts in fa", async () => {
    mockApi({ keys: [{ items: [key()] }] });
    renderPage({ lang: "fa" });
    expect(await screen.findByRole("heading", { level: 1, name: "کلیدهای API" })).toBeInTheDocument();
    expect(screen.getByText(/فقط با دسترسی‌هایی که انتخاب می‌کنید/)).toBeInTheDocument();
    const docs = screen.getByRole("link", { name: "مستندات API" });
    expect(docs).toHaveAttribute("href", "/api/docs");
    expect(screen.getByRole("button", { name: "ساخت کلید API" })).toBeInTheDocument();
    expect(await screen.findByRole("grid", { name: "کلیدهای API فضای کاری" })).toBeInTheDocument();
    expect(screen.getByText("فعال")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "کارها" })).toBeInTheDocument();
  });
});

describe("ApiKeysPage create modal", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("opens with an empty form: empty name, no scope, 90 days", async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();
    const dialog = await openCreateForm(user);
    expect(within(dialog).getByRole("textbox", { name: "Name" })).toHaveValue("");
    const scopes = within(dialog).getByRole("group", { name: "Scopes" });
    for (const checkbox of within(scopes).getAllByRole("checkbox")) {
      expect(checkbox).not.toBeChecked();
    }
    const expiry = within(dialog).getByRole("radiogroup", { name: "Expires after" });
    expect(within(expiry).getByRole("radio", { name: "90 days" })).toBeChecked();
  });

  it("sends no POST for an empty name, a whitespace name, or no scope", async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();
    const dialog = await openCreateForm(user);

    await user.click(within(dialog).getByRole("button", { name: "Create key" }));
    expect(within(dialog).getByText("Enter a name")).toBeInTheDocument();
    expect(within(dialog).getByText("Choose at least one scope")).toBeInTheDocument();

    await user.click(screen.getByRole("textbox", { name: "Name" }));
    await user.keyboard("   ");
    await user.click(screen.getByRole("checkbox", { name: "Read meetings, transcripts and summaries" }));
    await user.click(within(dialog).getByRole("button", { name: "Create key" }));
    expect(within(dialog).getByText("Enter a name")).toBeInTheDocument();

    expect(vi.mocked(api.post)).not.toHaveBeenCalled();
  });

  it("sends exactly one POST with the trimmed name, K4-ordered scopes and the chosen days", async () => {
    const calls = mockApi({ post: created() });
    const user = userEvent.setup();
    renderPage();
    const dialog = await openCreateForm(user);

    await user.click(screen.getByRole("textbox", { name: "Name" }));
    await user.keyboard("  Office PBX  ");
    await user.click(screen.getByRole("checkbox", { name: "See teams" }));
    await user.click(screen.getByRole("checkbox", { name: "Read meetings, transcripts and summaries" }));
    await user.click(within(dialog).getByRole("radio", { name: "30 days" }));
    await user.click(within(dialog).getByRole("button", { name: "Create key" }));

    await waitFor(() => expect(calls.post.length).toBe(1));
    expect(calls.post[0][0]).toBe("/api-keys");
    expect(calls.post[0][1]).toEqual({
      name: "Office PBX",
      scopes: ["meeting:read", "team:read"],
      expires_in_days: 30,
    });
  });

  it.each([
    ["too_many_api_keys", "This workspace already has the most active keys allowed. Revoke a key you no longer use, then try again."],
    ["scope_grant_denied", "You can only give a key the access you have yourself."],
    ["invalid_scopes", "One of the chosen scopes is not allowed for API keys."],
    ["invalid_expiry", "Choose a shorter expiry."],
  ])("shows the text of %s and keeps the modal open with its values", async (code, text) => {
    mockApi({ post: apiError(422, code) });
    const user = userEvent.setup();
    renderPage();
    const dialog = await openCreateForm(user);
    await fillForm(user);
    await user.click(within(dialog).getByRole("button", { name: "Create key" }));

    expect(await within(dialog).findByText(text)).toBeInTheDocument();
    expect(within(dialog).getByRole("textbox", { name: "Name" })).toHaveValue("Office PBX");
    expect(
      within(dialog).getByRole("checkbox", { name: "Read meetings, transcripts and summaries" }),
    ).toBeChecked();
    expect(screen.getByRole("dialog", { name: "Create API key" })).toBeInTheDocument();
  });

  it("shows the shared text for any other error, never the server's message", async () => {
    mockApi({ post: apiError(500, "internal_error") });
    const user = userEvent.setup();
    renderPage();
    const dialog = await openCreateForm(user);
    await fillForm(user);
    await user.click(within(dialog).getByRole("button", { name: "Create key" }));

    const alert = await within(dialog).findByRole("alert");
    expect(alert).toBeInTheDocument();
    expect(alert.textContent).not.toContain("fixture internal_error");
  });

  it("cancels without sending anything, and a reopen starts empty with no old error", async () => {
    mockApi({ post: apiError(500, "internal_error") });
    const user = userEvent.setup();
    renderPage();
    let dialog = await openCreateForm(user);
    await fillForm(user);
    await user.click(within(dialog).getByRole("button", { name: "Create key" }));
    await within(dialog).findByRole("alert");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(vi.mocked(api.post)).toHaveBeenCalledTimes(1);

    dialog = await openCreateForm(user);
    expect(within(dialog).getByRole("textbox", { name: "Name" })).toHaveValue("");
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(
      within(dialog).getByRole("checkbox", { name: "Read meetings, transcripts and summaries" }),
    ).not.toBeChecked();
    expect(within(dialog).getByRole("radiogroup", { name: "Expires after" })).toBeInTheDocument();
  });

  it("shows the key exactly once: gone from DOM and every cache after Done, and the list refreshes", async () => {
    const calls = mockApi({ keys: [{ items: [] }, { items: [key({ id: "key-2", name: "Office PBX", key_prefix: "ffff0000" })] }], post: created() });
    const queryClient = testQueryClient();
    const user = userEvent.setup();
    renderPage({ queryClient });
    const dialog = await openCreateForm(user);
    await fillForm(user);
    await user.click(within(dialog).getByRole("button", { name: "Create key" }));

    const createdDialog = await screen.findByRole("dialog", { name: "API key created" });
    expect(within(createdDialog).getByText(/we show it only once/)).toBeInTheDocument();
    expect(within(createdDialog).getByRole("textbox", { name: "Your new API key" })).toHaveValue(RAW_KEY);

    await user.click(within(createdDialog).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // The DOM keeps only the prefix, never the secret: neither as text nor as
    // an input value (textContent alone cannot see a value left in a field).
    expect(document.body.textContent).not.toContain("SHOW-ONCE-SECRET");
    expect(screen.queryByDisplayValue(RAW_KEY)).toBeNull();
    expect(screen.getByText("hk_ffff0000…")).toBeInTheDocument();
    // The refetch brought the new row in as active.
    const row = await screen.findByRole("row", { name: /Office PBX/ });
    expect(row).toHaveAttribute("data-status", "active");

    // The raw key never lands in the query cache or the mutation cache.
    await waitFor(() => {
      for (const query of queryClient.getQueryCache().getAll()) {
        expect(JSON.stringify(query.state.data)).not.toContain("SHOW-ONCE-SECRET");
      }
      for (const mutation of queryClient.getMutationCache().getAll()) {
        expect(JSON.stringify(mutation.state.data)).not.toContain("SHOW-ONCE-SECRET");
      }
    });
    expect(storageText(window.localStorage)).not.toContain("SHOW-ONCE-SECRET");
    expect(storageText(window.sessionStorage)).not.toContain("SHOW-ONCE-SECRET");

    // A reopen shows the empty form, never the old key.
    const reopened = await openCreateForm(user);
    expect(within(reopened).getByRole("textbox", { name: "Name" })).toHaveValue("");
    expect(document.body.textContent).not.toContain("SHOW-ONCE-SECRET");
    expect(screen.queryByDisplayValue(RAW_KEY)).toBeNull();
    expect(calls.get.filter((url) => url === "/api-keys").length).toBeGreaterThanOrEqual(2);
  });

  it("copies the key and confirms; a refusal shows the copy-failed alert", async () => {
    mockApi({ post: created() });
    const user = userEvent.setup();
    renderPage();
    const dialog = await openCreateForm(user);
    await fillForm(user);
    await user.click(within(dialog).getByRole("button", { name: "Create key" }));
    const createdDialog = await screen.findByRole("dialog", { name: "API key created" });

    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockRejectedValueOnce(new Error("no"));
    await user.click(within(createdDialog).getByRole("button", { name: "Copy key" }));
    expect(await within(createdDialog).findByRole("alert")).toHaveTextContent(
      "Copy failed. Select the key and copy it by hand.",
    );

    writeText.mockResolvedValueOnce(undefined);
    await user.click(within(createdDialog).getByRole("button", { name: "Copy key" }));
    expect(await within(createdDialog).findByRole("button", { name: "Copied" })).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith(RAW_KEY);
  });
});

describe("ApiKeysPage revoke", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("asks for confirmation and sends nothing on Cancel", async () => {
    const calls = mockApi({ keys: [{ items: [key()] }] });
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("row", { name: /CRM integration/ });
    await user.click(screen.getByRole("button", { name: "Revoke CRM integration" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Revoke API key?")).toBeInTheDocument();
    expect(
      within(dialog).getByText(/stops working at once. Systems that use it can no longer connect/),
    ).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(calls.delete.length).toBe(0);
  });

  it("sends exactly one DELETE to the key's id, toasts, and the row turns revoked after the refresh", async () => {
    const calls = mockApi({
      keys: [{ items: [key()] }, { items: [key({ revoked_at: new Date().toISOString() })] }],
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("row", { name: /CRM integration/ });
    await user.click(screen.getByRole("button", { name: "Revoke CRM integration" }));
    await user.click(await screen.findByRole("button", { name: "Revoke key" }));

    expect(await screen.findByText("API key revoked")).toBeInTheDocument();
    await waitFor(() => expect(calls.delete.length).toBe(1));
    expect(calls.delete[0]).toBe("/api-keys/key-1");
    const row = await screen.findByRole("row", { name: /CRM integration/ });
    expect(row).toHaveAttribute("data-status", "revoked");
    expect(within(row).getByText("Revoked")).toBeInTheDocument();
  });

  it("closes with the refreshed message on a 404", async () => {
    mockApi({
      keys: [{ items: [key()] }, { items: [key({ revoked_at: new Date().toISOString() })] }],
      delete: apiError(404, "api_key_not_found"),
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("row", { name: /CRM integration/ });
    await user.click(screen.getByRole("button", { name: "Revoke CRM integration" }));
    await user.click(await screen.findByRole("button", { name: "Revoke key" }));

    expect(await screen.findByText("This key no longer exists. The list has been refreshed.")).toBeInTheDocument();
    // The toast itself also has role="alertdialog", so the check is named.
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog", { name: "Revoke API key?" })).not.toBeInTheDocument(),
    );
  });

  it("returns focus to the Revoke button after Cancel", async () => {
    mockApi({ keys: [{ items: [key()] }] });
    const user = userEvent.setup();
    renderPage();
    const revoke = await screen.findByRole("button", { name: "Revoke CRM integration" });
    await user.click(revoke);
    await screen.findByRole("alertdialog");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog", { name: "Revoke API key?" })).not.toBeInTheDocument(),
    );
    // With arrow-key navigation the table restores focus to the button that
    // opened the dialog, not its cell; the list fallback must not steal it on
    // a plain cancel either way. The restore lands a tick later, so the check
    // waits for it.
    await waitFor(() => expect(revoke).toHaveFocus());
  });

  it("moves focus to the list when the revoke succeeded and the button is gone", async () => {
    mockApi({
      keys: [{ items: [key()] }, { items: [key({ revoked_at: new Date().toISOString() })] }],
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("button", { name: "Revoke CRM integration" });
    await user.click(screen.getByRole("button", { name: "Revoke CRM integration" }));
    await user.click(await screen.findByRole("button", { name: "Revoke key" }));
    await screen.findByText("API key revoked");
    // The Revoke button is gone with the revoked row, so focus falls back to
    // the list region instead of the page body.
    await waitFor(() => {
      expect(document.activeElement).not.toBe(document.body);
      expect(document.activeElement).toContainElement(
        screen.getByRole("region", { name: "API key list, scrolls horizontally" }),
      );
    });
  });

  it("keeps the dialog open with a shared alert on any other error", async () => {
    mockApi({ keys: [{ items: [key()] }], delete: apiError(500, "boom") });
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("row", { name: /CRM integration/ });
    await user.click(screen.getByRole("button", { name: "Revoke CRM integration" }));
    await user.click(await screen.findByRole("button", { name: "Revoke key" }));

    const dialog = await screen.findByRole("alertdialog");
    const alert = await within(dialog).findByRole("alert");
    expect(alert.textContent).not.toContain("fixture boom");
    expect(dialog).toBeInTheDocument();
  });
});
