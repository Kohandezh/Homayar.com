import { expect, test, type Page } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";

/**
 * HOM-252: the voicemail inbox and its analytics card. The demo seed has no
 * voicemails (checked 2026-10-19: list `{"items":[]}`, analytics `total: 0`),
 * so:
 *
 * - test 1 reads the real backend as the demo owner and sees the two empty
 *   states;
 * - tests 2 and 3 keep the owner's real session but mock every voicemail
 *   request (and the meeting's actions) from in-memory state, so the flow can
 *   filter, open, read and mark handled without any write reaching the
 *   backend;
 * - test 4 gives the viewer the same mocked detail and checks the write
 *   control stays away.
 *
 * Nothing here may write to the shared backend: the catch-all below aborts
 * every unmocked non-GET to `/api/v1/` in the browser and records it, so a bug
 * fails the test instead of changing the demo data.
 */

/** Aborts every unmocked write to the API; returns the list of what it stopped. */
async function blockApiWrites(page: Page) {
  const blocked: string[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    if (request.method() === "GET" || request.method() === "HEAD" || request.method() === "OPTIONS") {
      await route.fallback();
      return;
    }
    blocked.push(`${request.method()} ${new URL(request.url()).pathname}`);
    await route.abort();
  });
  return blocked;
}

const T = "2026-10-18T12:00:00Z";

interface MockVoicemail {
  id: string;
  meeting_id: string;
  follow_up_action_id: string | null;
  follow_up_state: "none" | "pending" | "action_created" | "done";
  follow_up_done_at: string | null;
  transcript_excerpt: string | null;
  priority: string;
  [key: string]: unknown;
}

/** One row (and detail) the mocked server holds; a PATCH changes it. */
function mockVoicemail(): MockVoicemail {
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
    topics: ["invoice"],
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
    meeting_status: "completed",
    transcript_excerpt: "The invoice number 42 was never paid.",
    transcript_revision_id: "rev-1",
  };
}

/**
 * Serves every voicemail read and write from `state`, plus the meeting's
 * actions. Register after `blockApiWrites`: Playwright runs the newest
 * matching route first, so these sit in front of the write blocker, and the
 * analytics route (registered last) wins over the detail one.
 */
async function mockVoicemailApi(page: Page, state: { voicemail: MockVoicemail }) {
  const listQueries: string[] = [];
  const patches: { path: string; body: unknown }[] = [];

  await page.route("**/api/v1/voicemails*", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    const url = new URL(route.request().url());
    listQueries.push(url.search);
    const priority = url.searchParams.get("priority");
    const matches = !priority || state.voicemail.priority === priority;
    await route.fulfill({ json: { items: matches ? [state.voicemail] : [], next_before: null } });
  });
  await page.route("**/api/v1/voicemails/*", async (route) => {
    const request = route.request();
    const id = decodeURIComponent(new URL(request.url()).pathname.split("/").at(-1) ?? "");
    if (request.method() === "GET") {
      if (id !== state.voicemail.id) {
        await route.fulfill({ status: 404, json: { error: { code: "voicemail_not_found", message: "no voicemail", request_id: "e2e" } } });
        return;
      }
      await route.fulfill({ json: state.voicemail });
      return;
    }
    if (request.method() !== "PATCH") {
      await route.fallback();
      return;
    }
    const body = request.postDataJSON() as { follow_up_state: "done" | "pending" };
    patches.push({ path: new URL(request.url()).pathname, body });
    state.voicemail = {
      ...state.voicemail,
      follow_up_state: body.follow_up_state,
      follow_up_done_at: body.follow_up_state === "done" ? T : null,
    };
    await route.fulfill({ json: state.voicemail });
  });
  await page.route("**/api/v1/voicemails/analytics", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      json: {
        received_from: "2026-09-19T00:00:00Z",
        received_to: "2026-10-19T00:00:00Z",
        total: 1,
        by_category: { billing: 1 },
        by_priority: { urgent: 1 },
        by_status: { classified: 1 },
        by_follow_up_state: { [state.voicemail.follow_up_state]: 1 },
        by_day: [{ day: "2026-10-18", count: 1 }],
        followed_up: 0,
        median_follow_up_seconds: null,
      },
    });
  });
  await page.route("**/api/v1/meetings/*/actions", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      json: [
        {
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
        },
      ],
    });
  });

  return { listQueries, patches };
}

test("the owner reads the real, empty inbox and the analytics card's empty text", async ({ page }) => {
  const blocked = await blockApiWrites(page);

  await resumeSession(page, "owner");
  await page.goto(consolePath("/voicemails"));

  await expect(page.getByRole("heading", { name: "Voicemail", level: 1 })).toBeVisible();
  await expect(page.getByText("You see only the voicemails your record scope allows.")).toBeVisible();
  await expect(page.getByText("No voicemails yet")).toBeVisible();

  await page.goto(consolePath("/admin"));
  const card = page.getByRole("region", { name: "Voicemail", exact: true });
  await expect(card).toBeVisible();
  await expect(card).not.toHaveAttribute("aria-busy", "true");
  await expect(card.getByText("No voicemails in the last 30 days")).toBeVisible();

  expect(blocked).toEqual([]);
});

test("the owner filters, opens and handles a voicemail (voicemail API mocked)", async ({ page }) => {
  const blocked = await blockApiWrites(page);
  const state = { voicemail: mockVoicemail() };
  const { listQueries, patches } = await mockVoicemailApi(page, state);

  await resumeSession(page, "owner");
  await page.goto(consolePath("/voicemails"));

  // The row names the caller and the date; the table follows the record scope.
  const table = page.getByRole("grid", { name: "Voicemails" });
  await expect(table).toBeVisible();
  await expect(page.getByRole("button", { name: /Details for.*\+982112345678/ })).toBeVisible();

  // Filter by priority: the value lands in the URL and in the request.
  await page.getByRole("button", { name: /Priority/ }).click();
  await page.getByRole("option", { name: "Urgent", exact: true }).click();
  await expect(page).toHaveURL(/priority=urgent/);
  await expect
    .poll(() => listQueries.at(-1))
    .toContain("priority=urgent");

  // Open the row: the detail, its transcript and the follow-up action.
  await page.getByRole("button", { name: /Details for/ }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("The invoice number 42 was never paid.");
  await expect(drawer).toContainText("Call the customer back about the invoice");
  await expect(drawer.getByRole("link", { name: "Open the call" })).toHaveAttribute(
    "href",
    "/app/meetings/meet-1",
  );

  // Mark handled: one PATCH, then the drawer and the list show the new state.
  await drawer.getByRole("button", { name: "Mark handled" }).click();
  await expect(drawer.getByRole("button", { name: "Reopen" })).toBeVisible();
  await expect(drawer.getByText("Handled", { exact: true })).toBeVisible();
  await expect(table.getByText("Handled", { exact: true })).toBeVisible();
  expect(patches).toEqual([
    { path: "/api/v1/voicemails/vm-1", body: { follow_up_state: "done" } },
  ]);

  expect(blocked).toEqual([]);
});

test("the inbox is right to left in Persian (voicemail API mocked)", async ({ page }) => {
  const blocked = await blockApiWrites(page);
  const state = { voicemail: mockVoicemail() };
  await mockVoicemailApi(page, state);

  await resumeSession(page, "owner", "fa");
  await page.goto(consolePath("/voicemails"));

  await expect(page.getByRole("heading", { name: "پیام‌های صوتی", level: 1 })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("grid", { name: "پیام‌های صوتی" })).toBeVisible();
  await expect(page.getByText("فقط پیام‌های صوتی در محدودهٔ دسترسی رکورد شما نمایش داده می‌شود.")).toBeVisible();

  expect(blocked).toEqual([]);
});

test("a viewer reads the detail but gets no write control (voicemail API mocked)", async ({ page }) => {
  const blocked = await blockApiWrites(page);
  const state = { voicemail: mockVoicemail() };
  await mockVoicemailApi(page, state);

  await resumeSession(page, "viewer");
  await page.goto(consolePath("/voicemails?voicemail=vm-1"));

  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("The invoice number 42 was never paid.");
  await expect(drawer.getByRole("button", { name: "Mark handled" })).toHaveCount(0);
  await expect(drawer.getByRole("button", { name: "Reopen" })).toHaveCount(0);

  expect(blocked).toEqual([]);
});
