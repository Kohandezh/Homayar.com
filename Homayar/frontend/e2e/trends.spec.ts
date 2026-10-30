import { expect, test, type Page, type Route } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";
import type { CoverageCounter } from "../src/types/organizations";
import type { IssueTrendResponse, TrendCell, TrendLinkResponse, TrendRow } from "../src/types/trends";

/**
 * Issue trends (HOM-237). The first two tests read the real demo backend.
 * Sample policy v1 needs 30 calls per cell and the seed has fewer, so the
 * real page shows `Too few calls` warnings, not computed cells, and no count
 * is fixed here. The third test answers `/api/v1/trends/**` in the browser
 * with one computed and one withheld cell, and links the computed cell to a
 * real completed meeting found through the API.
 *
 * Nothing here may write to the shared backend: every non-GET to `/api/v1/`
 * is aborted in the browser and recorded, and the test fails if one was tried.
 */

/** Aborts every write to the API; returns the list of what it stopped. */
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

/** The headers a signed-in console request carries (src/lib/api.ts). */
async function apiHeaders(page: Page): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((cookie) => cookie.name === "homayar_csrf")?.value;
  const workspaceId = await page.evaluate(() => window.localStorage.getItem("homayar_workspace_id"));
  return {
    ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    ...(workspaceId ? { "X-Workspace-ID": workspaceId } : {}),
  };
}

/** The first completed meeting, read with the page's session (a GET). */
async function completedMeetingId(page: Page): Promise<string> {
  const response = await page.request.get("/api/v1/meetings", {
    headers: await apiHeaders(page),
    params: { limit: 20, status: "completed" },
  });
  expect(response.ok(), "GET /meetings?status=completed").toBeTruthy();
  const { items } = (await response.json()) as { items: { id: string }[] };
  expect(items.length, "a seeded completed meeting").toBeGreaterThan(0);
  return items[0].id;
}

function isTrendsRead(url: string) {
  return new URL(url).pathname === "/api/v1/trends/issues";
}

function counter(): CoverageCounter {
  return {
    definition: "coverage",
    numerator_label: "processed",
    denominator_label: "received",
    numerator: 30,
    denominator: 32,
    ratio: 0.9375,
    status: "computed",
    reason: null,
    buckets: {},
  };
}

const COMPUTED: TrendCell = { status: "computed", numerator: 12, denominator: 40, ratio: 0.3, reason: null, minimum_sample: 30 };
const WITHHELD: TrendCell = { status: "withheld", numerator: null, denominator: 40, ratio: null, reason: "small_group", minimum_sample: 30 };

function row(level: "family" | "issue", issue: string | null, cell: TrendCell): TrendRow {
  return { level, family: "call_reason", issue, cell, pending_review: 0, absent_pending_review: 0, missing: {} };
}

function trendsFixture(): IssueTrendResponse {
  const rows = [row("family", null, COMPUTED), row("issue", "intent_request_support", WITHHELD)];
  const window = { from: "2026-09-25T20:30:00+00:00", to: "2026-10-02T20:30:00+00:00", basis: "ingestion_received_at" };
  return {
    notices: ["correlation_not_cause"],
    window,
    scope: { restricted: false },
    series: { grain: "week", timezone: "Asia/Tehran", calendar: "gregorian", week_starts_on: "saturday" },
    population: { included: 40, excluded: { duplicate_media: 0 } },
    rows,
    points: [
      {
        from: "2026-09-26T00:00:00+03:30",
        to: "2026-10-03T00:00:00+03:30",
        partial: false,
        population: { included: 40, excluded: { duplicate_media: 0 } },
        rows,
      },
    ],
    coverage: { version: "coverage/1", window, basis: "trend_population", processing: counter(), evaluation: counter() },
    missing_data: { indicators: [] },
    disclosure: { status: "released", reason: null, kind: null, masked: [] },
  };
}

/** Answers the trends reads from the fixture; returns the conversation queries it saw. */
async function mockTrends(page: Page, meetingId: string) {
  const linkQueries: URLSearchParams[] = [];
  await page.route("**/api/v1/trends/**", async (route: Route) => {
    const request = route.request();
    if (request.method() !== "GET") return route.fallback();
    const url = new URL(request.url());
    const reply = (json: unknown) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(json) });
    if (url.pathname.endsWith("/trends/issues")) return reply(trendsFixture());
    if (url.pathname.endsWith("/trends/conversations")) {
      linkQueries.push(url.searchParams);
      const answer: TrendLinkResponse = {
        cell: COMPUTED,
        page: { offset: 0, limit: 25, total: 1 },
        conversations: [{ meeting_id: meetingId, state: "counted", analysis_run_id: "run-e2e", evidence_ids: ["ev-1"] }],
      };
      return reply(answer);
    }
    return route.fallback();
  });
  return linkQueries;
}

test.describe("issue trends", () => {
  let blocked: string[] = [];

  test.afterEach(() => {
    expect(blocked, "no write may reach the shared backend").toEqual([]);
  });

  test("the owner reads the real trends with a denominator next to every percent", async ({ page }) => {
    blocked = await blockApiWrites(page);
    await resumeSession(page, "owner");
    const trendsResponse = page.waitForResponse(
      (response) => isTrendsRead(response.url()) && response.request().method() === "GET",
    );
    await page.goto(consolePath("/trends"));

    expect((await trendsResponse).status()).toBe(200);
    await expect(page.getByRole("heading", { name: "Issue trends", level: 1 })).toBeVisible();
    const coverage = page.getByRole("region", { name: "Coverage" });
    await expect(coverage.getByText("Processing coverage").locator("..")).toContainText(" of ");
    await expect(page.getByRole("rowheader", { name: "Call reasons" })).toBeVisible();

    // Each percent must end an `N of D · P%` in the same cell; `2040%` with no separator fails.
    const unpaired = await page.evaluate(() =>
      [...document.querySelectorAll('[role="gridcell"], dd')]
        .map((element) => element.textContent ?? "")
        .filter((text) => (text.match(/%/g)?.length ?? 0) !== (text.match(/\d+ of \d+ · \d+%/g)?.length ?? 0)),
    );
    expect(unpaired, "a percent without its denominator").toEqual([]);
  });

  test("the trends page reads right to left in Persian", async ({ page }) => {
    blocked = await blockApiWrites(page);
    await resumeSession(page, "owner", "fa");
    await page.goto(consolePath("/trends"));

    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.getByRole("heading", { name: "روند مسائل", level: 1 })).toBeVisible();
  });

  test("a computed cell opens its supporting calls and links a real meeting", async ({ page }) => {
    blocked = await blockApiWrites(page);
    await resumeSession(page, "owner");
    await page.goto(consolePath("/meetings"));
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    const meetingId = await completedMeetingId(page);
    const linkQueries = await mockTrends(page, meetingId);

    await page.goto(consolePath("/trends"));
    const grid = page.getByRole("grid", { name: "Issue trends" });
    await expect(grid).toBeVisible();
    // The withheld cell has no way to its calls.
    await expect(page.getByRole("button", { name: /^Show calls: Support request,/ })).toHaveCount(0);

    await page.getByRole("button", { name: "Show calls: Call reasons, whole range" }).click();
    const drawer = page.getByRole("dialog", { name: "Supporting calls" });
    await expect(drawer).toBeVisible();
    await expect(drawer).toContainText("12 of 40");
    const link = drawer.getByRole("link", { name: "Open call 1" });
    await expect(link).toHaveAttribute("href", consolePath(`/meetings/${meetingId}`));
    expect(linkQueries).toHaveLength(1);
    expect(linkQueries[0].get("row")).toBe("family:call_reason");
    expect(linkQueries[0].get("trend_view")).toBe("issues");
    expect(linkQueries[0].has("point")).toBe(false);

    const meetingResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `/api/v1/meetings/${meetingId}` && response.request().method() === "GET",
    );
    await link.click();
    await expect(page).toHaveURL(new RegExp(`/app/meetings/${meetingId}`));
    expect((await meetingResponse).status()).toBe(200);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  });
});
