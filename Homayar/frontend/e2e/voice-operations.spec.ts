import { expect, test, type Page } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";

/**
 * HOM-194: the voice capacity card and the operator-only voice operations
 * page. The demo seed has no platform operator and no media node, so:
 *
 * - test 1 reads the real backend as the demo owner (`system_role` "user");
 * - test 2 keeps the owner's real session but mocks `/auth/me` (operator)
 *   and every voice request, and drives drain and resume through the dialog.
 *
 * Neither test may write to the shared backend. Every route below is
 * registered before the first navigation, and any write to `/api/v1/` that
 * no mock answers is aborted in the browser and recorded, so a bug fails
 * the test instead of changing the demo data.
 */

/**
 * A node id as the dialog heading and the toast show it: between the
 * invisible left-to-right isolate marks of `isolateLtr` (src/lib/format.ts).
 */
const ltr = (value: string) => `\u2066${value}\u2069`;

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

test("an owner who is not a platform operator sees the voice capacity card but not voice operations", async ({ page }) => {
  const blocked = await blockApiWrites(page);
  let capacityReads = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.endsWith("/api/v1/voice/capacity")) capacityReads += 1;
  });

  await resumeSession(page, "owner");
  await page.goto(consolePath("/admin"));

  const card = page.getByRole("region", { name: "Voice capacity", exact: true });
  await expect(card).toBeVisible();
  await expect(card).not.toHaveAttribute("aria-busy", "true");
  await expect(card.getByRole("alert")).toHaveCount(0);
  await expect(card.getByText("0 / 200", { exact: true })).toBeVisible();
  await expect(card.getByRole("meter", { name: "Concurrent calls" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Settings", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Voice operations" })).toHaveCount(0);

  capacityReads = 0;
  await page.goto(consolePath("/admin/voice"));
  await expect(page.getByRole("heading", { name: "No access", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Voice operations" })).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(`${consolePath("/admin/voice")}$`));
  await page.waitForLoadState("networkidle");
  // The guard never mounts the page, so the route asks for no capacity.
  expect(capacityReads).toBe(0);
  expect(blocked).toEqual([]);
});

interface MockNode {
  node_id: string;
  max_concurrent: number;
  held: number;
  draining: boolean;
  healthy: boolean;
  generation: number;
  heartbeat_at: string;
}

function mockNode(nodeId: string, overrides: Partial<MockNode> = {}): MockNode {
  return {
    node_id: nodeId,
    max_concurrent: 20,
    held: 3,
    draining: false,
    healthy: true,
    generation: 4,
    heartbeat_at: new Date(Date.now() - 30_000).toISOString(),
    ...overrides,
  };
}

test("a platform operator drains and resumes nodes through the confirm dialog (voice API mocked)", async ({ page }) => {
  const blocked = await blockApiWrites(page);

  // The node states the mocked server holds; a drain call changes them, so
  // the read after it shows the new state.
  const nodes = new Map<string, MockNode>([
    ["media-a", mockNode("media-a")],
    // A colon is legal in a node id and has to be URL encoded.
    ["media:b", mockNode("media:b", { max_concurrent: 10, held: 0, draining: true, healthy: false, generation: 2 })],
  ]);
  const drainPosts: { path: string; body: unknown }[] = [];
  let capacityReads = 0;

  // Playwright runs the newest matching route first, so these sit in front of
  // the write blocker above. Any other voice request is aborted.
  await page.route("**/api/v1/voice/**", async (route) => {
    const request = route.request();
    blocked.push(`${request.method()} ${new URL(request.url()).pathname}`);
    await route.abort();
  });
  await page.route("**/api/v1/voice/capacity", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    capacityReads += 1;
    const list = [...nodes.values()];
    await route.fulfill({
      json: {
        workspace: { workspace_id: "e2e-workspace", held: 3, limit: 200 },
        deployment: {
          profile: "e2e",
          held: list.reduce((sum, node) => sum + node.held, 0),
          limit: 50,
          nodes: list,
        },
        metrics: {},
      },
    });
  });
  await page.route("**/api/v1/voice/nodes/*/drain", async (route) => {
    const request = route.request();
    if (request.method() !== "POST") {
      await route.fallback();
      return;
    }
    const path = new URL(request.url()).pathname;
    const body = request.postDataJSON() as { draining: boolean };
    drainPosts.push({ path, body });
    const nodeId = decodeURIComponent(path.split("/").at(-2) ?? "");
    const node = nodes.get(nodeId);
    if (!node) {
      await route.fulfill({ status: 404, json: { error: { code: "not_found", message: "no node", request_id: "e2e" } } });
      return;
    }
    const updated = { ...node, draining: body.draining, generation: node.generation + 1 };
    nodes.set(nodeId, updated);
    // The answer shape of the real endpoint (NodeResponse in backend/routers/v1/voice.py).
    await route.fulfill({
      json: {
        node_id: updated.node_id,
        max_concurrent: updated.max_concurrent,
        draining: updated.draining,
        generation: updated.generation,
        heartbeat_at: updated.heartbeat_at,
      },
    });
  });
  // The real session answer, read-only, with the platform operator role added.
  await page.route("**/api/v1/auth/me", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    const response = await route.fetch();
    const session = await response.json();
    await route.fulfill({ response, json: { ...session, user: { ...session.user, system_role: "operator" } } });
  });

  await resumeSession(page, "owner");
  await page.goto(consolePath("/admin/voice"));

  await expect(page.getByRole("heading", { name: "Voice operations", level: 1 })).toBeVisible();
  await expect(page.getByRole("link", { name: "Voice operations" })).toBeVisible();
  const table = page.getByRole("grid", { name: "Media nodes" });
  await expect(table).toBeVisible();
  await expect(page.getByRole("meter")).toHaveCount(2);
  await expect(table.getByRole("row")).toHaveCount(3);
  await expect(table.getByRole("rowheader", { name: "media-a" })).toBeVisible();
  await expect(table.getByText("Unhealthy", { exact: true })).toBeVisible();
  await expect(table.getByText("Draining", { exact: true })).toBeVisible();

  // Cancel sends nothing.
  await page.getByRole("button", { name: "Drain media-a" }).click();
  const drainDialog = page.getByRole("alertdialog", { name: `Drain ${ltr("media-a")}?` });
  await expect(drainDialog).toBeVisible();
  await drainDialog.getByRole("button", { name: "Cancel" }).click();
  await expect(drainDialog).toHaveCount(0);
  expect(drainPosts).toEqual([]);

  // Confirm sends one POST, and the read after it shows the node draining.
  const readsBeforeDrain = capacityReads;
  await page.getByRole("button", { name: "Drain media-a" }).click();
  await expect(drainDialog).toBeVisible();
  await drainDialog.getByRole("button", { name: "Drain node" }).click();
  await expect(page.getByText(`${ltr("media-a")} is draining.`)).toBeVisible();
  await expect(drainDialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Resume media-a" })).toBeVisible();
  expect(capacityReads).toBeGreaterThan(readsBeforeDrain);
  expect(drainPosts).toEqual([{ path: "/api/v1/voice/nodes/media-a/drain", body: { draining: true } }]);

  // Resume sends draining false to the encoded id.
  await page.getByRole("button", { name: "Resume media:b" }).click();
  const resumeDialog = page.getByRole("alertdialog", { name: `Resume ${ltr("media:b")}?` });
  await resumeDialog.getByRole("button", { name: "Resume node" }).click();
  await expect(page.getByText(`${ltr("media:b")} takes calls again.`)).toBeVisible();
  await expect(page.getByRole("button", { name: "Drain media:b" })).toBeVisible();
  expect(drainPosts).toHaveLength(2);
  expect(drainPosts[1]).toEqual({ path: "/api/v1/voice/nodes/media%3Ab/drain", body: { draining: false } });

  expect(blocked).toEqual([]);
});
