import { expect, test, type Page } from "@playwright/test";
import { consolePath, resumeSession } from "./demo";

/**
 * HOM-241: the MFA screens of the console. The CI e2e job runs with the
 * privileged MFA gate off (`PRIVILEGED_MFA_REQUIRED=false`) and no MFA
 * keyring, so every `/api/v1/auth/mfa**` request is answered here from
 * in-memory state and any other unmocked API write is aborted in the
 * browser. The owner's real session (`resumeSession`) is kept: `/auth/me`
 * and the reads of `/admin` stay real, so nothing is written to the demo
 * backend and no demo user is really enrolled.
 */

/** A base32-shaped secret, only ever known to this spec and the mocked answers. */
const MOCK_SECRET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
const GROUPED_SECRET = "ABCD EFGH IJKL MNOP QRST UVWX YZ23 4567";
const MOCK_OTPAUTH = `otpauth://totp/Homayar:e2e-owner?secret=${MOCK_SECRET}&issuer=Homayar`;
const MOCK_RECOVERY_CODES = [
  "aaaaa-bbbbb",
  "ccccc-ddddd",
  "eeeee-fffff",
  "ggggg-hhhhh",
  "iiiii-jjjjj",
  "kkkkk-lllll",
  "mmmmm-nnnnn",
  "ooooo-ppppp",
  "qqqqq-rrrrr",
  "sssss-ttttt",
];
/** Normalized the way the backend accepts recovery codes (case, hyphens, spaces). */
const normalized = (code: string) => code.toUpperCase().replace(/[\s-]/g, "");

interface MfaState {
  totp_enrolled: boolean;
  recovery_codes_remaining: number;
  session_verified: boolean;
  required: boolean;
}

interface RecordedPost {
  path: string;
  body: unknown;
}

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

/**
 * Answers every `/api/v1/auth/mfa**` request from in-memory state: the
 * status, the enrolment (secret shown once), the confirm (only the ASCII
 * test code is correct, Persian digits stay as typed and are refused) and
 * the verify (a TOTP code or the first recovery code). Every POST is
 * recorded so the tests can count exact requests.
 */
async function mockMfa(page: Page, state: MfaState) {
  const enrollPosts: RecordedPost[] = [];
  const confirmPosts: RecordedPost[] = [];
  const verifyPosts: RecordedPost[] = [];
  await page.route("**/api/v1/auth/mfa**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const json = (status: number, body: unknown) => route.fulfill({ status, json: body });
    if (path === "/api/v1/auth/mfa" && method === "GET") {
      await json(200, state);
      return;
    }
    if (path === "/api/v1/auth/mfa/totp/enroll" && method === "POST") {
      enrollPosts.push({ path, body: request.postDataJSON() });
      await json(200, { secret: MOCK_SECRET, otpauth_uri: MOCK_OTPAUTH });
      return;
    }
    if (path === "/api/v1/auth/mfa/totp/confirm" && method === "POST") {
      const body = request.postDataJSON() as { code: string };
      confirmPosts.push({ path, body });
      if (body.code === "123456") {
        state.totp_enrolled = true;
        state.session_verified = true;
        await json(200, { recovery_codes: MOCK_RECOVERY_CODES });
        return;
      }
      await json(400, { error: { code: "mfa_code_invalid", message: "wrong code", request_id: "e2e" } });
      return;
    }
    if (path === "/api/v1/auth/mfa/verify" && method === "POST") {
      const body = request.postDataJSON() as { code: string };
      verifyPosts.push({ path, body });
      if (body.code === "123456") {
        state.session_verified = true;
        await json(200, { verified: true, method: "totp", recovery_codes_remaining: state.recovery_codes_remaining });
        return;
      }
      if (normalized(body.code) === normalized(MOCK_RECOVERY_CODES[0])) {
        state.session_verified = true;
        state.recovery_codes_remaining = Math.max(0, state.recovery_codes_remaining - 1);
        await json(200, { verified: true, method: "recovery_code", recovery_codes_remaining: state.recovery_codes_remaining });
        return;
      }
      await json(400, { error: { code: "mfa_code_invalid", message: "wrong code", request_id: "e2e" } });
      return;
    }
    await route.fallback();
  });
  return { enrollPosts, confirmPosts, verifyPosts };
}

/** Collects every console message and every visited URL, to prove no secret reached them (AC8). */
function watchLeaks(page: Page) {
  const consoleTexts: string[] = [];
  const urls: string[] = [];
  page.on("console", (message) => consoleTexts.push(message.text()));
  page.on("framenavigated", (frame) => urls.push(frame.url()));
  function expectClean() {
    for (const needle of [MOCK_SECRET, GROUPED_SECRET, ...MOCK_RECOVERY_CODES]) {
      for (const text of consoleTexts) expect(text, `a console message held ${needle}`).not.toContain(needle);
      for (const url of urls) expect(url, `a page URL held ${needle}`).not.toContain(needle);
    }
  }
  return { expectClean };
}

/** Neither storage may hold the mocked secret or any mocked recovery code (AC8). */
async function expectStorageClean(page: Page) {
  const dump = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
  expect(dump, "localStorage/sessionStorage held the mocked secret").not.toContain(MOCK_SECRET);
  for (const code of MOCK_RECOVERY_CODES) {
    expect(dump, `localStorage/sessionStorage held ${code}`).not.toContain(code);
  }
}

test("a privileged owner without a factor is led through enrolment and back", async ({ page }) => {
  const blocked = await blockApiWrites(page);
  const leaks = watchLeaks(page);
  const mfa = await mockMfa(page, { totp_enrolled: false, recovery_codes_remaining: 0, session_verified: false, required: true });

  await resumeSession(page, "owner");
  await page.goto(consolePath("/admin"));

  // The proactive check (AC2) redirects before the admin page renders.
  await expect(page).toHaveURL(/\/app\/mfa\/setup\?next=%2Fadmin$/);
  await expect(page.getByRole("heading", { name: "Set up two-step verification", level: 1 })).toBeVisible();
  expect(mfa.enrollPosts).toEqual([]);

  // Start setup sends exactly one enroll POST; the QR is drawn in the browser.
  await page.getByRole("button", { name: "Start setup" }).click();
  await expect(page.getByRole("img", { name: "QR code for your authenticator app" })).toBeVisible();
  await expect(page.getByText(GROUPED_SECRET)).toBeVisible();
  expect(mfa.enrollPosts).toHaveLength(1);

  await page.getByLabel("Six-digit code").fill("123456");
  await page.getByRole("button", { name: "Confirm" }).click();

  // The recovery codes are shown exactly once (AC6).
  await expect(page.getByRole("heading", { name: "Save your recovery codes" })).toBeVisible();
  await expect(page.getByRole("listitem")).toHaveCount(10);
  const continueButton = page.getByRole("button", { name: "Continue" });
  await expect(continueButton).toBeDisabled();
  await page.getByRole("checkbox", { name: "I saved my recovery codes" }).check();
  await continueButton.click();

  // Back on /admin, which renders again.
  await expect(page).toHaveURL(/\/app\/admin$/);
  await expect(page.getByRole("region", { name: "Voice capacity" })).toBeVisible();

  expect(blocked).toEqual([]);
  leaks.expectClean();
  await expectStorageClean(page);
});

test("a 403 mfa_required from a privileged page leads to verification (reactive)", async ({ page }) => {
  const blocked = await blockApiWrites(page);
  const leaks = watchLeaks(page);
  const mfa = await mockMfa(page, { totp_enrolled: true, recovery_codes_remaining: 10, session_verified: true, required: false });

  // One privileged GET that /admin makes (the voice capacity card's read) is
  // answered 403 mfa_required once; everything else stays real.
  let refusalsLeft = 1;
  await page.route("**/api/v1/voice/capacity", async (route) => {
    const request = route.request();
    if (request.method() === "GET" && refusalsLeft > 0) {
      refusalsLeft -= 1;
      await route.fulfill({ status: 403, json: { error: { code: "mfa_required", message: "verify", request_id: "e2e" } } });
      return;
    }
    await route.fallback();
  });

  await resumeSession(page, "owner");
  await page.goto(consolePath("/admin"));

  await expect(page.getByRole("heading", { name: "Verify it's you", level: 1 })).toBeVisible();
  await expect(page).toHaveURL(/\/app\/mfa\/verify\?next=%2Fadmin$/);

  // A wrong code shows the error and stays.
  await page.getByLabel("Six-digit code").fill("000000");
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("alert")).toHaveText("That code is not correct. Try the newest code from your app.");

  // A recovery code verifies and shows the remaining count (AC5).
  await page.getByRole("button", { name: "Use a recovery code" }).click();
  await page.getByLabel("Recovery code").fill(MOCK_RECOVERY_CODES[0]);
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByText("You used a recovery code. 9 recovery codes left.")).toBeVisible();

  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page).toHaveURL(/\/app\/admin$/);
  await expect(page.getByRole("region", { name: "Voice capacity" })).toBeVisible();

  expect(mfa.verifyPosts).toHaveLength(2);
  expect(blocked).toEqual([]);
  leaks.expectClean();
  await expectStorageClean(page);
});

test("the Persian setup page is right to left and sends Persian digits as typed", async ({ page }) => {
  const blocked = await blockApiWrites(page);
  const mfa = await mockMfa(page, { totp_enrolled: false, recovery_codes_remaining: 0, session_verified: false, required: true });

  await resumeSession(page, "owner", "fa");
  // A direct visit: the MFA pages are outside the gate, so no redirect fires.
  await page.goto(consolePath("/mfa/setup"));

  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("heading", { name: "راه‌اندازی ورود دومرحله‌ای", level: 1 })).toBeVisible();

  await page.getByRole("button", { name: "شروع راه‌اندازی" }).click();
  await expect(page.getByRole("img", { name: "کد QR برای برنامهٔ احراز هویت" })).toBeVisible();
  await expect(page.getByText("کلید راه‌اندازی")).toBeVisible();

  // The code typed with Persian digits reaches the request exactly as typed
  // (the server normalizes; the mock only accepts ASCII, so the alert shows).
  await page.getByLabel("کد شش‌رقمی").fill("۱۲۳۴۵۶");
  await page.getByRole("button", { name: "تأیید" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  expect(mfa.confirmPosts).toEqual([{ path: "/api/v1/auth/mfa/totp/confirm", body: { code: "۱۲۳۴۵۶" } }]);

  expect(blocked).toEqual([]);
});

test("the account security page shows the mocked status for the owner", async ({ page }) => {
  const blocked = await blockApiWrites(page);
  await mockMfa(page, { totp_enrolled: true, recovery_codes_remaining: 7, session_verified: true, required: true });

  await resumeSession(page, "owner");
  await page.goto(consolePath("/account/security"));

  await expect(page.getByRole("heading", { name: "Account security", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Two-step verification" })).toBeVisible();
  await expect(page.getByText("On")).toBeVisible();
  await expect(page.getByText("Required for your role")).toBeVisible();
  await expect(page.getByText("7 recovery codes left")).toBeVisible();
  await expect(page.getByRole("button", { name: "Make new recovery codes" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Use a new authenticator app" })).toBeVisible();
  // The sidebar footer link (AC7) is there for the owner.
  await expect(page.getByRole("link", { name: "Account security" })).toHaveAttribute("href", "/account/security");

  expect(blocked).toEqual([]);
});
