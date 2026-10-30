import { expect, test, type Page } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";

/**
 * The scorecards page (HOM-248) against the demo backend; the scorecard API
 * is not mocked. Versions are append-only and there is no delete, so every
 * run creates its own scorecard under a per-run key and leaves it behind.
 * The tests share that key, so they run in order.
 */
const KEY = `e2e_${Date.now().toString(36)}`;
const NAME = `E2E scorecard ${KEY}`;

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

/** A valid `scorecards/1` definition with one criterion that carries all the weight. */
function backgroundDefinition(name: string) {
  return {
    name,
    description: null,
    pass_threshold: 70,
    criteria: [
      {
        criterion_id: "greeting",
        label: "Greets the caller",
        weight: 100,
        auto_fail: false,
        na_allowed: false,
        applies_to: null,
        check: { check_id: "greeting", version: 1, kind: "required_phrase", phrases: ["hello"] },
      },
    ],
  };
}

async function latestVersion(page: Page): Promise<number> {
  const response = await page.request.get(`/api/v1/scorecards/${KEY}/versions`, {
    headers: await apiHeaders(page),
  });
  expect(response.ok(), `GET /scorecards/${KEY}/versions with the page's session`).toBeTruthy();
  const body = (await response.json()) as { items: { version: number }[] };
  return body.items[0].version;
}

/** The version number in a status such as "Published version 3." */
async function publishedVersion(page: Page): Promise<number> {
  const status = page.getByText(/^Published version \d+\.$/);
  await expect(status).toBeVisible();
  const version = Number((/version (\d+)\./.exec((await status.textContent()) ?? "") ?? [])[1]);
  expect(Number.isInteger(version)).toBeTruthy();
  return version;
}

test.describe.serial("the scorecards page", () => {
  test("an owner creates a scorecard with two criteria and publishes it", async ({ page }) => {
    await resumeSession(page, "owner");
    await page.goto(consolePath("/admin/scorecards"));
    await expect(page.getByRole("heading", { name: "Scorecards", level: 1 })).toBeVisible();

    await page.getByRole("button", { name: "New scorecard" }).click();
    await page.getByRole("textbox", { name: "Scorecard key" }).fill(KEY);
    await page.getByRole("textbox", { name: "Name", exact: true }).fill(NAME);

    const first = page.getByRole("group", { name: "Criterion 1" });
    await first.getByRole("textbox", { name: "Criterion ID" }).fill("greeting");
    await first.getByRole("textbox", { name: "Label" }).fill("Greets the caller");
    await first.getByRole("textbox", { name: "Weight" }).fill("50");
    await first.getByRole("textbox", { name: "Phrases" }).fill("hello\ngood morning");

    await page.getByRole("button", { name: "Add criterion" }).click();
    const second = page.getByRole("group", { name: "Criterion 2" });
    await second.getByRole("textbox", { name: "Criterion ID" }).fill("closing");
    await second.getByRole("textbox", { name: "Label" }).fill("Says goodbye");
    await second.getByRole("textbox", { name: "Weight" }).fill("40");
    await second.getByRole("textbox", { name: "Phrases" }).fill("goodbye");
    await page.getByRole("textbox", { name: "Reason for this version" }).fill("e2e: first version");

    await expect(page.getByText("Total weight: 90 / 100")).toBeVisible();
    await expect(page.getByText("Weights must add up to 100.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Publish" })).toBeDisabled();

    await second.getByRole("textbox", { name: "Weight" }).fill("50");
    await expect(page.getByText("Total weight: 100 / 100")).toBeVisible();
    await expect(page.getByRole("button", { name: "Publish" })).toBeEnabled();
    await page.getByRole("button", { name: "Publish" }).click();

    expect(await publishedVersion(page)).toBe(await latestVersion(page));
    await expect(page).toHaveURL(new RegExp(`\\?key=${KEY}$`));
    const history = page.getByRole("grid", { name: "Version history" });
    await expect(history.getByRole("row").filter({ hasText: "e2e: first version" })).toBeVisible();
    await expect(page.getByRole("link", { name: new RegExp(NAME) })).toBeVisible();
  });

  test("a version published elsewhere during an edit shows the conflict and loads it", async ({ page }) => {
    await resumeSession(page, "owner");
    await page.goto(consolePath(`/admin/scorecards?key=${KEY}`));
    await page.getByRole("button", { name: "Edit" }).click();
    await expect(page.getByRole("group", { name: "Criterion 1" })).toBeVisible();

    // Someone else publishes while the editor is open, on the version the page read.
    const background = `${NAME} (published elsewhere)`;
    const response = await page.request.post(`/api/v1/scorecards/${KEY}/versions`, {
      headers: await apiHeaders(page),
      data: {
        definition: backgroundDefinition(background),
        reason: "e2e: a concurrent version",
        expected_version: await latestVersion(page),
      },
    });
    expect(response.status(), "POST /scorecards/{key}/versions from the side").toBe(201);
    const concurrent = ((await response.json()) as { version: number }).version;

    await page.getByRole("textbox", { name: "Reason for this version" }).fill("e2e: my edit");
    await page.getByRole("button", { name: "Publish" }).click();
    await expect(
      page.getByRole("alert").filter({
        hasText: `Someone else published version ${concurrent} while you were editing.`,
      }),
    ).toBeVisible();
    // The draft is untouched until the reload.
    await expect(page.getByRole("textbox", { name: "Name", exact: true })).toHaveValue(NAME);

    await page.getByRole("button", { name: `Load version ${concurrent}` }).click();
    await expect(page.getByRole("textbox", { name: "Name", exact: true })).toHaveValue(background);
    await expect(page.getByRole("textbox", { name: "Reason for this version" })).toHaveValue("e2e: my edit");
    await expect(page.getByRole("group", { name: "Criterion 2" })).toHaveCount(0);

    await page.getByRole("button", { name: "Publish" }).click();
    expect(await publishedVersion(page)).toBeGreaterThan(concurrent);
  });

  test("a member reads the scorecard with no edit controls", async ({ page }) => {
    await resumeSession(page, "member");
    await page.goto(consolePath(`/admin/scorecards?key=${KEY}`));

    await expect(page.getByRole("heading", { name: "Scorecards", level: 1 })).toBeVisible();
    await expect(page.getByRole("grid", { name: "Version history" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Preview on a sample call" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Edit" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "New scorecard" })).toHaveCount(0);
  });

  test("the page lays out right to left in Persian", async ({ page }) => {
    await resumeSession(page, "owner", "fa");
    await page.goto(consolePath(`/admin/scorecards?key=${KEY}`));

    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.getByRole("heading", { name: "کارت‌های امتیازدهی", level: 1 })).toBeVisible();
    await expect(page.getByRole("grid", { name: "تاریخچه نسخه‌ها" })).toBeVisible();
  });
});
