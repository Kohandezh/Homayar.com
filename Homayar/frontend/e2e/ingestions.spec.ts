import { expect, test, type Page, type Route } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";
import type { IngestionRecord } from "../src/types/ingestions";

/**
 * The ingestions ledger (HOM-251). The first and last tests run against the
 * real demo backend (read-only). The middle one answers
 * `/api/v1/ingestions**` from in-memory fixtures, because it needs a full
 * page of rows in every state (rejected, duplicate) to page through and
 * filter, which the demo seed's accepted uploads do not give: auth and every
 * other request stay real. Any non-GET to that route is aborted
 * in the browser and fails the test, so nothing is ever written.
 */

const MOCK_HEADER = "x-e2e-ing-mock";
const PAGE_SIZE = 25;

/** A row's time, so page 1 runs from `at(0)` (newest) down to `at(24)`. */
function at(minutesAgo: number) {
  return new Date(Date.parse("2026-10-10T12:00:00.000Z") - minutesAgo * 60_000).toISOString();
}

function record(id: string, minutesAgo: number, overrides: Partial<IngestionRecord> = {}): IngestionRecord {
  return {
    id,
    source_type: "file_upload",
    source_provider: "web-console",
    external_event_id: `evt-${id}`,
    media_size_bytes: 1234567,
    declared_content_type: "audio/webm",
    original_filename: `${id}.webm`,
    status: "accepted",
    reason: null,
    received_at: at(minutesAgo),
    created_at: at(minutesAgo),
    meeting_id: null,
    duplicate_of_id: null,
    conflict_count: 0,
    last_conflict_at: null,
    // The server sends these; the console type does not carry them, and the
    // page must never render them.
    payload_hash: `payload-hash-${id}`,
    media_hash: `media-hash-${id}`,
    workspace_id: `ws-${id}`,
    organization_id: `org-${id}`,
    api_key_id: `key-${id}`,
    created_by_user_id: `user-${id}`,
    job_id: `job-${id}`,
    ...overrides,
  } as IngestionRecord;
}

/** Page 1 is exactly full, so the server names a `next_before`; page 2 ends the list. */
function ledgerFixture() {
  const pageOne: IngestionRecord[] = [
    record("ing-first", 0, { original_filename: "original-first.webm", meeting_id: "meet-e2e" }),
    record("ing-rej", 1, {
      original_filename: "too-big.webm",
      status: "rejected",
      reason: "upload_too_large",
    }),
    record("ing-dup", 2, {
      original_filename: "same-file.webm",
      duplicate_of_id: "ing-first",
      conflict_count: 3,
      last_conflict_at: at(2),
    }),
    ...Array.from({ length: PAGE_SIZE - 3 }, (_, index) => record(`ing-fill-${index}`, 3 + index)),
  ];
  const pageTwo: IngestionRecord[] = [
    record("ing-old-1", 25),
    record("ing-old-2", 26),
    record("ing-old-3", 27),
  ];
  const details = new Map<string, IngestionRecord>(
    [...pageOne, ...pageTwo].map((row) => [row.id, row]),
  );
  return { pageOne, pageTwo, details, nextBefore: at(24) };
}

interface MockLedger {
  writes: string[];
  /** The query strings of every list GET, so filters can be asserted on them. */
  listQueries: string[];
}

async function mockIngestions(page: Page, fixture: ReturnType<typeof ledgerFixture>): Promise<MockLedger> {
  const state: MockLedger = { writes: [], listQueries: [] };
  await page.route("**/api/v1/ingestions**", (route: Route) => {
    const request = route.request();
    if (request.method() !== "GET") {
      state.writes.push(`${request.method()} ${request.url()}`);
      return route.abort();
    }
    const reply = (json: unknown) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: { [MOCK_HEADER]: "1" },
        body: JSON.stringify(json),
      });
    const url = new URL(request.url());
    if (url.pathname.endsWith("/ingestions")) {
      state.listQueries.push(url.searchParams.toString());
      const secondPage = url.searchParams.get("before") === fixture.nextBefore;
      return reply(
        secondPage
          ? { items: fixture.pageTwo, next_before: null }
          : { items: fixture.pageOne, next_before: fixture.nextBefore },
      );
    }
    const id = decodeURIComponent(url.pathname.split("/").pop() ?? "");
    const found = fixture.details.get(id);
    return found
      ? reply(found)
      : route.fulfill({
          status: 404,
          contentType: "application/json",
          headers: { [MOCK_HEADER]: "1" },
          body: JSON.stringify({ error: { code: "ingestion_not_found", message: "no", request_id: "e2e-mock" } }),
        });
  });
  return state;
}

test("an owner opens the real ledger and sees the table or the empty state", async ({ page }) => {
  await resumeSession(page, "owner");
  const listResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/ingestions" && response.request().method() === "GET",
  );
  await page.goto(consolePath("/admin/ingestions"));

  expect((await listResponse).status()).toBe(200);
  await expect(page.getByRole("heading", { name: "Ingestions", level: 1 })).toBeVisible();
  // The shared backend may gain rows at any time, so both outcomes are fine.
  const grid = page.getByRole("grid", { name: "Ingestions" });
  const empty = page.getByText("No uploads or imports yet.");
  await expect(grid.or(empty).first()).toBeVisible();
});

test("the mocked ledger pages, filters and opens its drawers", async ({ page }) => {
  const fixture = ledgerFixture();
  const mock = await mockIngestions(page, fixture);
  await resumeSession(page, "owner");
  await page.goto(consolePath("/admin/ingestions"));

  const grid = page.getByRole("grid", { name: "Ingestions" });
  await expect(grid).toBeVisible();
  await expect(grid.getByRole("row")).toHaveCount(PAGE_SIZE + 1); // header plus 25 rows

  // The second page walks the server's cursor; Previous returns.
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByText("Page 2")).toBeVisible();
  await expect(grid.getByRole("row")).toHaveCount(4); // header plus 3 rows
  await expect
    .poll(() => mock.listQueries.at(-1))
    .toContain(`before=${encodeURIComponent(fixture.nextBefore)}`);
  await page.getByRole("button", { name: "Previous" }).click();
  await expect(page.getByText("Page 1")).toBeVisible();

  // The state filter is the console's own: only the rejected row stays.
  await page.getByRole("button", { name: /State$/ }).click();
  await page.getByRole("option", { name: "Rejected", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/state=rejected/);
  await expect(grid.getByRole("row")).toHaveCount(2);
  expect(mock.listQueries.at(-1)).not.toContain("state");

  // The rejected row's drawer shows its reason label and raw code.
  const rejectedRow = grid.getByRole("row", { name: /too-big\.webm/ });
  await rejectedRow.getByRole("button", { name: "Details" }).click();
  const drawer = page.getByRole("dialog", { name: "Ingestion details" });
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("File too large");
  await expect(drawer.getByText("upload_too_large").first()).toBeVisible();
  await drawer.getByRole("button", { name: "Close details" }).click();
  await expect(drawer).toHaveCount(0);

  // Back to every state; the source filter reaches the server.
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(grid.getByRole("row")).toHaveCount(PAGE_SIZE + 1);
  await page.getByRole("button", { name: /Source$/ }).click();
  await page.getByRole("option", { name: "File upload", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/source=file_upload/);
  await expect.poll(() => mock.listQueries.at(-1)).toContain("source_type=file_upload");

  // The duplicate row links to the earlier ingestion of the same file.
  const duplicateRow = grid.getByRole("row", { name: /same-file\.webm/ });
  await duplicateRow.getByRole("button", { name: "Details" }).click();
  await expect(drawer).toContainText("Same file as an earlier ingestion.");
  await expect(drawer).toContainText("Sent again with different content: 3 times");
  await drawer.getByRole("button", { name: "Open the earlier ingestion" }).click();
  await expect(drawer).toContainText("original-first.webm");
  await expect(drawer.getByRole("link", { name: "Open meeting" })).toHaveAttribute(
    "href",
    "/app/meetings/meet-e2e",
  );

  // Nothing the server sent but the page must not show: the whole document,
  // attributes included, with the drawer open and again after it closes.
  const secrets = ["payload-hash", "media-hash", "ws-", "org-", "key-", "user-", "job-"].flatMap(
    (prefix) => [`ing-first`, `ing-dup`].map((id) => `${prefix}${id}`),
  );
  for (const secret of secrets) expect(await page.content()).not.toContain(secret);

  await drawer.getByRole("button", { name: "Close details" }).click();
  await expect(drawer).toHaveCount(0);
  for (const secret of secrets) expect(await page.content()).not.toContain(secret);

  expect(mock.writes, "no ingestion request may reach the real API").toEqual([]);
});

test("the ledger reads right to left in Persian", async ({ page }) => {
  await resumeSession(page, "owner", "fa");
  await page.goto(consolePath("/admin/ingestions"));

  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("heading", { name: "ورودی‌ها", level: 1 })).toBeVisible();
});

test("a member gets no access to the ledger and no list is asked", async ({ page }) => {
  const listRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/v1/ingestions")) {
      listRequests.push(request.url());
    }
  });
  await resumeSession(page, "member");
  await page.goto(consolePath("/admin/ingestions"));

  await expect(page.getByRole("heading", { name: "No access", level: 1 })).toBeVisible();
  expect(listRequests).toEqual([]);
});
