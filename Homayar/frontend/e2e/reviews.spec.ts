import { expect, test, type APIResponse, type Browser, type Page } from "@playwright/test";
import { consolePath, DEMO_DISPLAY_NAMES, resumeSession } from "./demo";

/**
 * The review queue (HOM-265) against the demo seed. The seed's `Sales` team
 * is supervised by `admin` with `member` as its agent, and the seed has no
 * scorecard results, so:
 *
 * - the real part creates its own review item through the API (on a
 *   completed `Sales` call of the demo member, never a fixed id) and cancels
 *   any active item already on that call first, so the one-active-item rule
 *   cannot refuse this run's create; `afterAll` cancels what it created if
 *   the run died before its own last step;
 * - the criteria decisions are covered by a mocked part: one `in_review`
 *   item with a machine result of three criteria, served from in-memory
 *   state by `page.route`, so no decision write can reach the backend (the
 *   catch-all aborts every unmocked non-GET to `/api/v1/`).
 */

/** The item this run created, for the steps and the cleanup. */
let created: { itemId: string; meetingId: string } | null = null;

/** The headers a signed-in console request carries (src/lib/api.ts). */
async function apiHeaders(page: Page): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((cookie) => cookie.name === "homayar_csrf")?.value;
  const workspaceId = await page.evaluate(() => window.localStorage.getItem("homayar_workspace_id"));
  return {
    ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    ...(workspaceId ? { "X-Workspace-ID": workspaceId } : {}),
  };
}

async function apiErrorName(response: APIResponse) {
  return `${response.status()}: ${(await response.text()).slice(0, 200)}`;
}

/**
 * Finds a completed `Sales` call whose agent is the demo member, through the
 * meetings list and its fields. Puts the database back as it found it: the
 * only writes are the cancel of an active item this run did not create when
 * the one-active-item rule needs it, and the create of this run's own item.
 */
async function createItemOnMemberCall(page: Page): Promise<{ itemId: string; meetingId: string }> {
  const headers = await apiHeaders(page);
  const teams = await page.request.get("/api/v1/teams", { headers });
  expect(teams.ok(), "GET /teams").toBeTruthy();
  const salesId = ((await teams.json()) as { items: { id: string; name: string }[] }).items.find(
    (team) => team.name === "Sales",
  )?.id;
  expect(salesId, "the seeded Sales team").toBeDefined();

  const members = await page.request.get("/api/v1/members", { headers });
  expect(members.ok(), "GET /members").toBeTruthy();
  const memberId = ((await members.json()) as { items: { user_id: string; display_name: string }[] }).items.find(
    (member) => member.display_name === DEMO_DISPLAY_NAMES.member,
  )?.user_id;
  expect(memberId, "the demo member").toBeDefined();

  let meeting: { id: string } | undefined;
  let before: string | undefined;
  for (let page_number = 0; page_number < 5 && !meeting; page_number += 1) {
    const response = await page.request.get("/api/v1/meetings", {
      headers,
      params: { limit: 100, status: "completed", ...(before ? { before } : {}) },
    });
    expect(response.ok(), "GET /meetings").toBeTruthy();
    const body = (await response.json()) as {
      items: { id: string; team_id: string | null; agent_user_id: string | null }[];
      next_before: string | null;
    };
    meeting = body.items.find((item) => item.team_id === salesId && item.agent_user_id === memberId);
    before = body.next_before ?? undefined;
    if (!before) break;
  }
  expect(meeting, "a completed Sales call of the demo member").toBeDefined();

  // An active item from a crashed run would refuse this run's create.
  const active = await page.request.get("/api/v1/review-items", {
    headers,
    params: { meeting_id: meeting!.id },
  });
  expect(active.ok(), "GET /review-items?meeting_id").toBeTruthy();
  for (const item of ((await active.json()) as { items: { id: string; status: string; revision: number }[] }).items) {
    if (item.status !== "open" && item.status !== "in_review") continue;
    const cancel = await page.request.post(`/api/v1/review-items/${item.id}/cancel`, {
      headers,
      data: { expected_revision: item.revision, reason: "other" },
    });
    expect(cancel.ok(), `cancel the leftover item ${item.id}: ${await apiErrorName(cancel)}`).toBeTruthy();
  }

  const dueAt = new Date(Date.now() + 2 * 24 * 60 * 60 * 1000).toISOString();
  const create = await page.request.post("/api/v1/review-items", {
    headers,
    data: { meeting_id: meeting!.id, due_at: dueAt },
  });
  expect(create.ok(), `POST /review-items: ${await apiErrorName(create)}`).toBeTruthy();
  const item = (await create.json()) as { id: string };
  return { itemId: item.id, meetingId: meeting!.id };
}

/** Cancels this run's item when it is still active; a closed one stays closed. */
async function cancelCreatedItem(browser: Browser) {
  if (created === null) return;
  const { itemId } = created;
  created = null;
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    await resumeSession(page, "owner");
    await page.goto(consolePath("/reviews"));
    const headers = await apiHeaders(page);
    const response = await page.request.get(`/api/v1/review-items/${itemId}`, { headers });
    if (response.status() === 404) return;
    expect(response.ok(), `GET the created item: ${await apiErrorName(response)}`).toBeTruthy();
    const item = (await response.json()) as { status: string; revision: number };
    if (item.status !== "open" && item.status !== "in_review") return;
    const cancel = await page.request.post(`/api/v1/review-items/${itemId}/cancel`, {
      headers,
      data: { expected_revision: item.revision, reason: "other" },
    });
    expect([200, 409], `cancel the created item: ${await apiErrorName(cancel)}`).toContain(cancel.status());
  } finally {
    await context.close();
  }
}

test.describe.serial("the review queue (real backend)", () => {
  test.afterAll(async ({ browser }) => {
    test.setTimeout(120_000);
    await cancelCreatedItem(browser);
  });

  test("the owner queues this run's own item on a member call of Sales", async ({ page }) => {
    await resumeSession(page, "owner");
    // The API helpers read the session's storage, so the app has to load once.
    await page.goto(consolePath("/reviews"));
    created = await createItemOnMemberCall(page);
    expect(created.itemId).toBeTruthy();

    // The owner (a review admin) sees the row right away, unassigned.
    await page.goto(consolePath("/reviews"));
    const row = page.getByRole("row").filter({ has: page.locator(`a[href^="/app/reviews/${created.itemId}"]`) });
    await expect(row).toBeVisible();
    await expect(row.getByText("Unassigned")).toBeVisible();
    await expect(row.getByRole("link", { name: "Open call report" })).toHaveAttribute(
      "href",
      `/app/meetings/${created.meetingId}`,
    );
  });

  test("the Sales supervisor sees the item, the agent of the call does not", async ({ browser }) => {
    // Allow-control: the admin supervises Sales, so the row is theirs.
    const adminContext = await browser.newContext();
    const admin = await adminContext.newPage();
    try {
      await resumeSession(admin, "admin");
      await admin.goto(consolePath("/reviews"));
      const row = admin
        .getByRole("row")
        .filter({ has: admin.locator(`a[href^="/app/reviews/${created!.itemId}"]`) });
      await expect(row).toBeVisible();
    } finally {
      await adminContext.close();
    }

    // Deny: the member is the call's agent, so the API shows them nothing.
    const memberContext = await browser.newContext();
    const member = await memberContext.newPage();
    try {
      await resumeSession(member, "member");
      await member.goto(consolePath("/reviews"));
      await expect(member.getByRole("heading", { name: "Review queue", level: 1 })).toBeVisible();
      await expect(
        member.locator(`a[href^="/app/reviews/${created!.itemId}"]`),
        "the member's own call review is invisible",
      ).toHaveCount(0);
      await expect(member.getByText("No review items match these filters.")).toBeVisible();

      // The item page answers with the one not-found text, not with 403 hints.
      await member.goto(consolePath(`/reviews/${created!.itemId}`));
      await expect(
        member.getByText("This review item does not exist or you cannot see it."),
      ).toBeVisible();
    } finally {
      await memberContext.close();
    }
  });

  test("a viewer gets no access to the queue, with no review-items request", async ({ page }) => {
    const requests: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.startsWith("/api/v1/review-items")) {
        requests.push(request.url());
      }
    });
    await resumeSession(page, "viewer");
    await page.goto(consolePath("/reviews"));

    await expect(page.getByRole("heading", { name: "No access", level: 1 })).toBeVisible();
    await expect(page.getByRole("link", { name: "Review queue" })).toHaveCount(0);
    expect(requests).toEqual([]);

    await page.goto(consolePath(`/reviews/${created!.itemId}`));
    await expect(page.getByRole("heading", { name: "No access", level: 1 })).toBeVisible();
    expect(requests).toEqual([]);
  });

  test("the owner assigns, reschedules, starts and completes the item", async ({ page }) => {
    await resumeSession(page, "owner");
    await page.goto(consolePath(`/reviews/${created!.itemId}`));
    await expect(page.getByRole("link", { name: "Open call report" })).toBeVisible();

    // Assign to themself; the display name comes from the workspace members.
    await page.getByRole("button", { name: "Assign to" }).click();
    await page.getByRole("option", { name: DEMO_DISPLAY_NAMES.owner }).click();
    await page.getByRole("button", { name: "Assign", exact: true }).click();

    // Every write moves the revision; each step waits for its own revision,
    // so the next write always reads the revision the server just wrote.
    const itemRegion = page.getByRole("region", { name: "Review item" });
    const revisionValue = page.locator("dt", { hasText: /^Revision$/ }).locator("xpath=following-sibling::dd[1]");
    await expect(revisionValue).toHaveText("2");

    // Move the due time one day ahead (local time in, ISO with UTC offset out).
    const nextDue = new Date(Date.now() + 24 * 60 * 60 * 1000);
    const pad = (value: number) => String(value).padStart(2, "0");
    const localDue = `${nextDue.getFullYear()}-${pad(nextDue.getMonth() + 1)}-${pad(nextDue.getDate())}T${pad(nextDue.getHours())}:${pad(nextDue.getMinutes())}`;
    await page.getByLabel("Due time").fill(localDue);
    await page.getByRole("button", { name: "Save due time" }).click();
    await expect(revisionValue).toHaveText("3");

    // The seed has no scorecard results, so this item has no machine score.
    await expect(page.getByText("This call has no machine score to review.")).toBeVisible();

    await page.getByRole("button", { name: "Start review" }).click();
    await expect(itemRegion.getByText("In review")).toBeVisible();

    await page.getByRole("button", { name: "Complete review" }).click();
    await expect(page.getByText("Review completed.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Next item" })).toBeVisible();
  });

  test("the queue page is right to left in Persian", async ({ page }) => {
    await resumeSession(page, "admin", "fa");
    await page.goto(consolePath("/reviews"));

    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.getByRole("heading", { name: "صف بازبینی", level: 1 })).toBeVisible();
    await expect(
      page.locator(`a[href^="/app/reviews/${created!.itemId}"]`),
      "the completed item still shows under a Completed filter only",
    ).toHaveCount(0);
  });
});

test.describe("criterion decisions (mocked criteria)", () => {
  const T = "2026-11-10T12:00:00Z";

  const mockItem = {
    id: "mock-item",
    meeting_id: "meet-mock",
    team_id: null,
    scorecard_result_id: "result-mock",
    source: "sample",
    source_details: {},
    status: "in_review",
    assignee_user_id: "user-owner",
    assigned_by_user_id: "user-owner",
    assigned_at: T,
    due_at: "2026-11-12T09:00:00Z",
    is_overdue: false,
    created_by_user_id: "user-owner",
    created_at: T,
    updated_at: T,
    started_at: T,
    completed_at: null,
    cancelled_at: null,
    cancel_reason: null,
    revision: 4,
  };

  function machine(outcome: string) {
    return {
      outcome,
      reason: null,
      decided_by: null,
      review_required: true,
      review_reasons: [],
      evidence: [{ turn_index: 1 }, { turn_index: 2 }],
    };
  }

  function criteriaBody(humanScore: number, humanOutcome: string) {
    return {
      review_item_id: "mock-item",
      revision: 4,
      status: "in_review",
      scorecard_result_id: "result-mock",
      machine: {
        outcome: "failed",
        reason: null,
        complete: true,
        score: 70,
        score_min: 0,
        score_max: 100,
        pass_threshold: 80,
        applicable_weight: 100,
        earned_weight: 70,
        failed_weight: 30,
        insufficient_weight: 0,
      },
      human: {
        outcome: humanOutcome,
        reason: null,
        complete: true,
        score: humanScore,
        score_min: 0,
        score_max: 100,
        pass_threshold: 80,
        applicable_weight: 100,
        earned_weight: humanScore,
        failed_weight: 100 - humanScore,
        insufficient_weight: 0,
        final: false,
      },
      criteria: [
        { criterion_id: "greeting", position: 1, weight: 30, auto_fail: false, na_allowed: false, reviewable: true, machine: machine("passed"), decision: null },
        { criterion_id: "closing", position: 2, weight: 30, auto_fail: false, na_allowed: false, reviewable: true, machine: machine("insufficient_evidence"), decision: null },
        { criterion_id: "tone", position: 3, weight: 40, auto_fail: false, na_allowed: true, reviewable: true, machine: machine("failed"), decision: null },
      ],
    };
  }

  /** Aborts every unmocked write to the API; returns what it stopped. */
  async function blockApiWrites(page: Page) {
    const blocked: string[] = [];
    await page.route("**/api/v1/**", async (route) => {
      const request = route.request();
      if (["GET", "HEAD", "OPTIONS"].includes(request.method())) {
        await route.fallback();
        return;
      }
      blocked.push(`${request.method()} ${new URL(request.url()).pathname}`);
      await route.abort();
    });
    return blocked;
  }

  test("accept is blocked for insufficient evidence; an override sends the exact body", async ({ page }) => {
    await resumeSession(page, "owner");
    // The API helpers read the session's storage, so the app has to load once.
    await page.goto(consolePath("/reviews"));
    // The mocked item must name the signed-in owner as its assignee, or the
    // decision controls (assignee only) would stay away.
    const me = await page.request.get("/api/v1/auth/me", { headers: await apiHeaders(page) });
    expect(me.ok(), "GET /auth/me").toBeTruthy();
    const ownerId = ((await me.json()) as { user: { id: string } }).user.id;
    const item = { ...mockItem, assignee_user_id: ownerId, assigned_by_user_id: ownerId };

    const blocked = await blockApiWrites(page);
    let state = criteriaBody(60, "failed");
    const decisionPosts: { path: string; body: unknown }[] = [];

    // Registered after the write blocker, so it sits in front of it.
    await page.route("**/api/v1/review-items/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() === "GET" && path.endsWith("/review-items/mock-item/criteria")) {
        await route.fulfill({ json: state });
        return;
      }
      if (request.method() === "GET" && path.endsWith("/review-items/mock-item")) {
        await route.fulfill({ json: item });
        return;
      }
      if (request.method() === "POST" && /\/criteria\/[^/]+\/decision$/.test(path)) {
        const body = request.postDataJSON() as { decision: string };
        decisionPosts.push({ path, body });
        if (body.decision === "override") {
          // The new human preview: the criterion now passes.
          state = criteriaBody(100, "passed");
          await route.fulfill({ json: state });
          return;
        }
        await route.fulfill({ json: state });
        return;
      }
      await route.fallback();
    });

    await page.goto(consolePath("/reviews/mock-item"));

    const machineBlock = page.getByRole("region", { name: "Machine score" });
    await expect(machineBlock).toBeVisible();
    // Before the decision: the machine score is 70, the human preview is 60
    // and failed (fixture values that exist only on their own side).
    await expect(machineBlock).toContainText("70");
    await expect(machineBlock).toContainText("Failed");
    const humanBlock = page.getByRole("region", { name: "Human score" });
    await expect(humanBlock).toContainText("Preview");
    await expect(humanBlock).toContainText("60");
    await expect(humanBlock).toContainText("Failed");

    // Insufficient evidence can only be overridden, never accepted.
    const closing = page.locator("li", { hasText: "closing" });
    await expect(closing.getByRole("button", { name: "Accept" })).toBeDisabled();
    await expect(closing.getByText("Insufficient evidence can only be overridden.")).toBeVisible();

    // A blank note sends nothing.
    const tone = page.locator("li", { hasText: "tone" });
    await tone.getByRole("button", { name: "Override" }).click();
    await page.getByRole("button", { name: "Reviewer outcome" }).click();
    await page.getByRole("option", { name: "Passed" }).click();
    await page.getByRole("button", { name: "Reason code" }).click();
    await page.getByRole("option", { name: "Other" }).click();
    await page.getByLabel("Note").fill("   ");
    await page.getByLabel("Evidence turns").fill("7, 3");
    await page.getByRole("button", { name: "Save decision" }).click();
    await expect(page.getByText("Write a note explaining the override.")).toBeVisible();
    expect(decisionPosts).toEqual([]);

    // A complete override sends exactly the body the service expects.
    await page.getByLabel("Note").fill("The agent did greet the customer warmly.");
    await page.getByRole("button", { name: "Save decision" }).click();
    await expect(page.getByText("Decision saved.")).toBeVisible();

    expect(decisionPosts).toHaveLength(1);
    expect(decisionPosts[0].path).toBe("/api/v1/review-items/mock-item/criteria/tone/decision");
    expect(decisionPosts[0].body).toEqual({
      expected_revision: 4,
      decision: "override",
      reviewer_outcome: "passed",
      reason_code: "other",
      note: "The agent did greet the customer warmly.",
      evidence_turn_indexes: [3, 7],
    });

    // The machine score never changed; the human preview did: the old score
    // and outcome are gone and the new ones are in (60 -> 100, Failed -> Passed).
    await expect(machineBlock).toContainText("70");
    await expect(machineBlock).toContainText("Failed");
    await expect(humanBlock).toContainText("100");
    await expect(humanBlock).toContainText("Passed");
    await expect(humanBlock).not.toContainText("60");
    await expect(humanBlock).toContainText("Preview");

    expect(blocked, "no review write reached the real API").toEqual([]);
  });
});
