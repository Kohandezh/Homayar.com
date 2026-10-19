import { expect, test, type Locator, type Page } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";

/**
 * HOM-187: the API keys page against the real backend and the demo seed.
 * The sessions come from the setup project (`resumeSession`, HOM-209), so the
 * spec never signs in through the form. The keys it creates all carry the
 * `e2e key ` prefix and are put back in afterEach (A2); the seeded
 * `CRM integration (demo)` key is never touched.
 */
const E2E_KEY_PREFIX = "e2e key ";

interface ApiKeyRow {
  id: string;
  name: string;
  revoked_at: string | null;
  created_at: string;
}

/** The exact key this run created (by name), and whether this run may read the list. */
let createdKeyName: string | null = null;
let canManageKeys = false;

/**
 * React Aria hides the native input under its label, so a click on the input
 * lands on the label. Click the visible label instead (as the settings spec
 * does).
 */
async function setChecked(control: Locator, checked: boolean) {
  if ((await control.isChecked()) === checked) return;
  await control.locator("xpath=ancestor::label[1]").click();
  await expect(control).toBeChecked({ checked });
}

async function deleteKey(page: Page, keyId: string, csrf: string) {
  const response = await page.request.delete(`/api/v1/api-keys/${encodeURIComponent(keyId)}`, {
    headers: { "X-CSRF-Token": csrf },
  });
  expect([204, 404]).toContain(response.status());
}

/**
 * Puts the workspace back (A2): the exact key this run created is revoked by
 * name, and still-active `e2e key ...` keys older than ten minutes are swept
 * as leftovers of a crashed run. The seeded key is never touched. Anything
 * unexpected fails loudly instead of slipping through.
 */
async function cleanup(page: Page) {
  const csrf = (await page.context().cookies()).find((cookie) => cookie.name === "homayar_csrf")?.value;
  if (!csrf) throw new Error("cleanup: the owner session has no CSRF cookie");
  const list = await page.request.get("/api/v1/api-keys");
  expect(list.ok(), `cleanup: GET /api-keys answered ${list.status()}`).toBeTruthy();
  const { items } = (await list.json()) as { items: ApiKeyRow[] };

  for (const key of items) {
    if (createdKeyName && key.name === createdKeyName && !key.revoked_at) {
      await deleteKey(page, key.id, csrf);
    }
  }
  const staleCutoff = Date.now() - 10 * 60 * 1000;
  for (const key of items) {
    if (!key.name.startsWith(E2E_KEY_PREFIX) || key.revoked_at) continue;
    if (Date.parse(key.created_at) > staleCutoff) continue;
    await deleteKey(page, key.id, csrf);
  }
}

test.afterEach(async ({ page }) => {
  // A member session may not even read the key list; only owner runs clean up.
  if (canManageKeys) await cleanup(page);
});

test("an owner reads the seeded key, creates one that shows once, and revokes it", async ({ page }) => {
  canManageKeys = true;
  await resumeSession(page, "owner");
  await page.goto(consolePath("/admin/api-keys"));

  // The seeded key: both scope chips, active, created by the seed's owner.
  const seeded = page.getByRole("row", { name: /CRM integration \(demo\)/ });
  await expect(seeded).toBeVisible();
  await expect(seeded.getByText("meeting:read")).toBeVisible();
  await expect(seeded.getByText("meeting:create")).toBeVisible();
  await expect(seeded.getByText("Active")).toBeVisible();
  await expect(seeded.getByText("سارا احمدی")).toBeVisible();

  // Create a key with one scope and the shortest expiry. The name is claimed
  // before the create, so a failure halfway still leaves a cleanup target.
  const name = `${E2E_KEY_PREFIX}${Date.now()}`;
  createdKeyName = name;
  await page.getByRole("button", { name: "Create API key" }).click();
  const form = page.getByRole("dialog", { name: "Create API key" });
  await form.getByRole("textbox", { name: "Name" }).fill(name);
  await setChecked(form.getByRole("checkbox", { name: "Read meetings, transcripts and summaries" }), true);
  // HeroUI's radio hides its native input too, so the click goes to the
  // option's visible text; the label around it drives the input.
  await form.getByText("30 days", { exact: true }).click();
  await expect(form.getByRole("radio", { name: "30 days", exact: true })).toBeChecked();
  await form.getByRole("button", { name: "Create key" }).click();

  // The created view shows the raw key exactly once, in the hk_<hex8>.<secret> shape.
  const created = page.getByRole("dialog", { name: "API key created" });
  await expect(created).toBeVisible();
  const keyInput = created.getByRole("textbox", { name: "Your new API key" });
  const rawKey = await keyInput.inputValue();
  expect(rawKey).toMatch(/^hk_[0-9a-f]{8}\./);

  await created.getByRole("button", { name: "Done" }).click();
  await expect(created).toHaveCount(0);
  // The secret left the page with the modal; the list keeps only the prefix.
  await expect(page.getByText(rawKey)).toHaveCount(0);

  const newRow = page.getByRole("row", { name: new RegExp(name) });
  await expect(newRow).toBeVisible();
  await expect(newRow).toHaveAttribute("data-status", "active");
  await expect(newRow.getByText(`hk_${rawKey.slice(3, 11)}…`)).toBeVisible();

  // Reopening the create form starts empty.
  await page.getByRole("button", { name: "Create API key" }).click();
  const reopened = page.getByRole("dialog", { name: "Create API key" });
  await expect(reopened.getByRole("textbox", { name: "Name" })).toHaveValue("");
  await reopened.getByRole("button", { name: "Cancel" }).click();

  // Revoke through the confirmation dialog.
  await newRow.getByRole("button", { name: `Revoke ${name}` }).click();
  const confirm = page.getByRole("alertdialog");
  await expect(confirm.getByText("Revoke API key?")).toBeVisible();
  await confirm.getByRole("button", { name: "Revoke key" }).click();
  await expect(newRow.getByText("Revoked")).toBeVisible();
  await expect(newRow).toHaveAttribute("data-status", "revoked");
});

test("the page is right to left in Persian", async ({ page }) => {
  canManageKeys = true;
  await resumeSession(page, "owner", "fa");
  await page.goto(consolePath("/admin/api-keys"));

  await expect(page.getByRole("heading", { level: 1, name: "کلیدهای API" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
});

test("a member is blocked from the API keys page", async ({ page }) => {
  canManageKeys = false;
  await resumeSession(page, "member");
  await page.goto(consolePath("/admin/api-keys"));

  await expect(page.getByRole("heading", { name: "No access" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "API keys" })).toHaveCount(0);
});
