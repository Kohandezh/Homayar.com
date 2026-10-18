import { expect, test, type Page } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";

/**
 * The AI policy page (HOM-188) against the demo seed. The versions are
 * append-only, so the owner flow writes one version; `afterEach` puts the
 * policy document back by writing one more version with the starting document.
 */

/** The AiPolicy document of backend/providers/policy.py, as the API sends it. */
interface AiPolicyDocument {
  [stage: string]: {
    endpoint_kinds: string[];
    regions: string[];
    manifests: string[];
    fallback: boolean;
    cloud_text?: string;
  };
}

interface AiPolicyResponse {
  version: number;
  source: "default" | "workspace";
  policy: AiPolicyDocument;
}

interface AiPolicyVersionsResponse {
  current_version: number;
  presets: { local_only: AiPolicyDocument; private: AiPolicyDocument };
}

/** The document the test started from, set once the owner flow has read it. */
let seeded: AiPolicyDocument | null = null;

/** The headers a signed-in console request carries (src/lib/api.ts). */
async function apiHeaders(page: Page): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((cookie) => cookie.name === "homayar_csrf")
    ?.value;
  const workspaceId = await page.evaluate(() =>
    window.localStorage.getItem("homayar_workspace_id"),
  );
  return {
    ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    ...(workspaceId ? { "X-Workspace-ID": workspaceId } : {}),
  };
}

async function readPolicy(page: Page): Promise<AiPolicyResponse> {
  const response = await page.request.get("/api/v1/admin/ai-policy", {
    headers: await apiHeaders(page),
  });
  expect(response.ok(), "GET /admin/ai-policy with the page's session").toBeTruthy();
  return (await response.json()) as AiPolicyResponse;
}

async function readPresets(page: Page): Promise<AiPolicyVersionsResponse["presets"]> {
  const response = await page.request.get("/api/v1/admin/ai-policy/versions", {
    headers: await apiHeaders(page),
  });
  expect(response.ok(), "GET /admin/ai-policy/versions with the page's session").toBeTruthy();
  return ((await response.json()) as AiPolicyVersionsResponse).presets;
}

test.afterEach(async ({ page }) => {
  if (seeded === null) return;
  const starting = seeded;
  seeded = null;
  // Even when a step failed: read the fresh version, then write the starting
  // document back. That restore itself appends one more version.
  const current = await readPolicy(page);
  if (JSON.stringify(current.policy) === JSON.stringify(starting)) return;
  const response = await page.request.put("/api/v1/admin/ai-policy", {
    headers: await apiHeaders(page),
    data: {
      policy: starting,
      expected_version: current.version,
      reason: "e2e: restore the AI policy after the test",
    },
  });
  expect(response.ok(), "PUT /admin/ai-policy restoring the starting document").toBeTruthy();
});

test("an owner edits the policy, sees the saved version in the history and compares it", async ({ page }) => {
  await resumeSession(page, "owner");
  await page.goto(consolePath("/admin/ai-policy"));

  await expect(page.getByRole("heading", { name: "AI policy", level: 1 })).toBeVisible();
  for (const stage of ["Speech to text", "Speaker separation", "Summary and analysis"]) {
    await expect(page.getByRole("heading", { name: stage, level: 2, exact: true })).toBeVisible();
  }
  // The badge is the built-in default until a version is stored, custom after that.
  await expect(page.getByText(/^(Built-in default|Custom, version \d+)$/)).toBeVisible();

  const current = await readPolicy(page);
  const presets = await readPresets(page);
  seeded = current.policy;

  await page.getByRole("button", { name: "Edit policy" }).click();
  // Pick a preset that really differs from the current document, so the save
  // is always a change (a leftover "Local only" policy must not make the save
  // a no-op). If both presets match, flip one switch instead.
  const differs = (document: AiPolicyDocument) =>
    JSON.stringify(document) !== JSON.stringify(current.policy);
  if (differs(presets.local_only)) {
    await page.getByRole("button", { name: "Local only" }).click();
  } else if (differs(presets.private)) {
    await page.getByRole("button", { name: "Private" }).click();
  } else {
    await page
      .getByRole("group", { name: "Speech to text" })
      .getByRole("switch", { name: "Allow fallback" })
      .click();
  }
  await page.getByRole("textbox", { name: "Reason for the change" }).fill("e2e: policy edit");
  await page.getByRole("button", { name: "Save policy" }).click();

  const status = page.getByText(/AI policy saved as version \d+\./);
  await expect(status).toBeVisible();
  const version = Number((/version (\d+)\./.exec((await status.textContent()) ?? "") ?? [])[1]);
  expect(Number.isInteger(version)).toBeTruthy();

  const history = page.getByRole("grid", { name: "Version history" });
  await expect(history.getByRole("row", { name: String(version) })).toBeVisible();

  await history.getByRole("button", { name: `Compare version ${version}` }).click();
  await expect(
    page.getByRole("heading", { name: `Version ${version} compared with the current policy` }),
  ).toBeVisible();
  // The saved version is the one now in force, so nothing differs.
  await expect(page.getByText(`Version ${version} is the same as the current policy.`)).toBeVisible();
});

test("an admin reads the policy and the history but cannot edit", async ({ page }) => {
  await resumeSession(page, "admin");
  await page.goto(consolePath("/admin/ai-policy"));

  await expect(page.getByRole("heading", { name: "AI policy", level: 1 })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Speech to text", level: 2, exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Only the workspace owner can change the AI policy.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit policy" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Save policy" })).toHaveCount(0);
  // The owner flows keep versions around; on a fresh seed the history is empty.
  await expect(
    page
      .getByText("No saved versions yet. The built-in default is in use.")
      .or(page.getByRole("grid", { name: "Version history" })),
  ).toBeVisible();
});

test("the page lays out right to left in Persian", async ({ page }) => {
  await resumeSession(page, "owner", "fa");
  await page.goto(consolePath("/admin/ai-policy"));

  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("heading", { name: "سیاست هوش مصنوعی", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "تفکیک گوینده‌ها", level: 2, exact: true })).toBeVisible();
  await expect(page.getByText("ارائه‌دهنده‌های ابری متن پنهان‌شده دریافت می‌کنند")).toBeVisible();
});
