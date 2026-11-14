import { Suspense } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router";
import api from "@/lib/api";
import { AppRoutes } from "@/AppRoutes";
import { mfaQueryKeys } from "@/lib/mfa-api";
import {
  FIXTURE_USER,
  FIXTURE_WORKSPACE,
  apiError,
  networkError,
  okResponse,
  renderConsole,
} from "@/test/fixtures";

const SECRET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
const GROUPED = "ABCD EFGH IJKL MNOP QRST UVWX YZ23 4567";
const OTPAUTH = "otpauth://totp/Homayar:person%40example.com?secret=ABCDEFGHIJKLMNOPQRSTUVWXYZ234567&issuer=Homayar";
const RECOVERY_CODES = [
  "aaaaa-bbbbb", "ccccc-ddddd", "eeeee-fffff", "ggggg-hhhhh", "iiiii-jjjjj",
  "kkkkk-lllll", "mmmmm-nnnnn", "ooooo-ppppp", "qqqqq-rrrrr", "sssss-ttttt",
];

const NOT_REQUIRED = { totp_enrolled: false, recovery_codes_remaining: 0, session_verified: false, required: false };

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{`${location.pathname}${location.search}${location.hash}`}</output>;
}

function location() {
  return screen.getByTestId("location").textContent;
}

/** Everything a storage holds, read through the API (stringify of the setup's stub storage would be empty). */
function storageText(storage: Storage): string {
  const parts: string[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (key !== null) parts.push(key, storage.getItem(key) ?? "");
  }
  return parts.join("\n");
}

/** Renders /mfa/setup through the real route tree, signed in as an owner. */
function renderSetup(route = "/mfa/setup?next=/admin", lang: "fa" | "en" = "en", signOut = vi.fn().mockResolvedValue(undefined)) {
  window.localStorage.removeItem("homayar_lang");
  return renderConsole(
    <Suspense fallback={<p>Loading page</p>}>
      <AppRoutes />
      <LocationProbe />
    </Suspense>,
    {
      route,
      lang,
      auth: {
        status: "authenticated",
        user: FIXTURE_USER,
        workspace: FIXTURE_WORKSPACE,
        workspaces: [FIXTURE_WORKSPACE],
        signOut,
      },
    },
  );
}

/** api.post answers enroll and confirm; every call is recorded. */
function mockMfaPost(options: { enroll?: Error; confirm?: Error } = {}) {
  const calls: { url: string; body: unknown }[] = [];
  vi.spyOn(api, "post").mockImplementation(async (url: string, body?: unknown) => {
    calls.push({ url, body });
    if (url === "/auth/mfa/totp/enroll") {
      if (options.enroll) throw options.enroll;
      return okResponse({ secret: SECRET, otpauth_uri: OTPAUTH });
    }
    if (url === "/auth/mfa/totp/confirm") {
      if (options.confirm) throw options.confirm;
      return okResponse({ recovery_codes: RECOVERY_CODES });
    }
    throw apiError(500, "unexpected_post");
  });
  return calls;
}

/** api.get answers only the status re-read after a successful confirm. */
function mockStatusReads(status: unknown = { ...NOT_REQUIRED, totp_enrolled: true, session_verified: true, recovery_codes_remaining: 0 }) {
  const urls: string[] = [];
  vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    urls.push(url);
    if (url === "/auth/mfa") return okResponse(status);
    throw apiError(404, "not_found");
  });
  return urls;
}

function stubClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(window.navigator, "clipboard", { value: { writeText }, configurable: true });
  return writeText;
}

async function renderEnrolled(options: { confirm?: Error } = {}) {
  const calls = mockMfaPost(options);
  const view = renderSetup();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Start setup" }));
  expect(await screen.findByRole("img", { name: "QR code for your authenticator app" })).toBeInTheDocument();
  return { calls, user, view };
}

afterEach(() => {
  vi.restoreAllMocks();
  Reflect.deleteProperty(window.navigator, "clipboard");
});

describe("MFA setup page (AC4)", () => {
  it("shows the heading and the explanation, and sends nothing until Start setup", async () => {
    const calls = mockMfaPost();
    renderSetup();

    expect(await screen.findByRole("heading", { level: 1, name: "Set up two-step verification" })).toBeInTheDocument();
    expect(screen.getByText(/six-digit code every 30 seconds/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start setup" })).toBeEnabled();
    expect(screen.queryByRole("img", { name: "QR code for your authenticator app" })).not.toBeInTheDocument();
    expect(calls).toEqual([]);
  });

  it("sends exactly one enroll POST, then shows the in-browser QR and the setup key", async () => {
    const calls = mockMfaPost();
    const user = userEvent.setup();
    renderSetup();
    await user.click(await screen.findByRole("button", { name: "Start setup" }));

    expect(await screen.findByRole("img", { name: "QR code for your authenticator app" })).toBeInTheDocument();
    // The QR is an inline SVG in the document, not an image URL (AC4).
    expect(document.querySelector("svg[role='img']")).toBeInTheDocument();
    expect(screen.getByText("Setup key")).toBeInTheDocument();
    const key = screen.getByText(GROUPED);
    expect(key).toHaveAttribute("dir", "ltr");
    expect(calls).toEqual([{ url: "/auth/mfa/totp/enroll", body: undefined }]);
  });

  it("copies the plain secret, not the grouped text", async () => {
    mockMfaPost();
    const user = userEvent.setup();
    // After setup: user-event would otherwise install its own clipboard stub.
    const writeText = stubClipboard();
    renderSetup();
    await user.click(await screen.findByRole("button", { name: "Start setup" }));
    await user.click(await screen.findByRole("button", { name: "Copy setup key" }));

    expect(writeText).toHaveBeenCalledWith(SECRET);
    expect(writeText).not.toHaveBeenCalledWith(GROUPED);
  });

  it("has the code field with numeric input attributes, and Enter submits", async () => {
    const { calls, user } = await renderEnrolled();
    const field = screen.getByLabelText("Six-digit code");
    expect(field).toHaveAttribute("inputmode", "numeric");
    expect(field).toHaveAttribute("autocomplete", "one-time-code");

    await user.type(field, "123456");
    fireEvent.submit(field.closest("form") as HTMLFormElement);
    await waitFor(() => expect(calls.some((call) => call.url === "/auth/mfa/totp/confirm")).toBe(true));
    expect(calls.find((call) => call.url === "/auth/mfa/totp/confirm")?.body).toEqual({ code: "123456" });
  });

  it("trims the code but keeps Persian digits as typed", async () => {
    mockStatusReads();
    const { calls, user } = await renderEnrolled();
    await user.type(screen.getByLabelText("Six-digit code"), "  ۱۲۳۴۵۶  ");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(calls.find((call) => call.url === "/auth/mfa/totp/confirm")).toBeTruthy());
    expect(calls.find((call) => call.url === "/auth/mfa/totp/confirm")?.body).toEqual({ code: "۱۲۳۴۵۶" });
  });

  it("sends nothing for an empty code and explains what to enter", async () => {
    const { calls, user } = await renderEnrolled();
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter the six-digit code from your app.");
    expect(calls).toEqual([{ url: "/auth/mfa/totp/enroll", body: undefined }]);
  });

  it("shows the recovery codes once after a successful confirm, and the secret leaves the screen", async () => {
    mockStatusReads();
    const { user } = await renderEnrolled();
    await user.type(screen.getByLabelText("Six-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    expect(await screen.findByRole("heading", { name: "Save your recovery codes" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(10);
    expect(screen.queryByText(GROUPED)).not.toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "QR code for your authenticator app" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Six-digit code")).not.toBeInTheDocument();
  });

  it("keeps Continue disabled until the saved checkbox is on, then returns to next", async () => {
    mockStatusReads();
    const { user } = await renderEnrolled();
    await user.type(screen.getByLabelText("Six-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    const checkbox = await screen.findByRole("checkbox", { name: "I saved my recovery codes" });
    const continueButton = screen.getByRole("button", { name: "Continue" });
    expect(continueButton).toBeDisabled();

    await user.click(checkbox);
    expect(continueButton).toBeEnabled();
    await user.click(continueButton);
    await waitFor(() => expect(location()).toBe("/admin"));
  });

  it("re-reads GET /auth/mfa after a successful confirm, so the gate lets the user back", async () => {
    mockMfaPost();
    const fresh = { totp_enrolled: true, recovery_codes_remaining: 0, session_verified: true, required: true };
    const urls = mockStatusReads(fresh);
    const view = renderSetup();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Start setup" }));
    await user.type(screen.getByLabelText("Six-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await screen.findByRole("heading", { name: "Save your recovery codes" });

    // The re-read runs in the background once the code is accepted.
    await waitFor(() => expect(urls).toContain("/auth/mfa"));
    await waitFor(() => expect(view.queryClient.getQueryData(mfaQueryKeys.status())).toEqual(fresh));
  });

  it.each([
    ["mfa_code_invalid", "That code is not correct. Try the newest code from your app."],
    ["mfa_unavailable", "Two-step verification is not available on this server. Ask your administrator."],
  ])("shows the text of %s and keeps the QR, the key and the field", async (code, text) => {
    const { user } = await renderEnrolled({ confirm: apiError(code === "mfa_code_invalid" ? 400 : 503, code) });
    await user.type(screen.getByLabelText("Six-digit code"), "000000");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(text);
    expect(screen.getByText(GROUPED)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "QR code for your authenticator app" })).toBeInTheDocument();
  });

  it("keeps the field focus after a wrong code", async () => {
    const { user } = await renderEnrolled({ confirm: apiError(400, "mfa_code_invalid") });
    const field = screen.getByLabelText("Six-digit code");
    await user.type(field, "000000");
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await screen.findByRole("alert");
    await waitFor(() => expect(field).toHaveFocus());
  });

  it.each([
    ["with Retry-After", { headers: { "Retry-After": "300" } }, "Too many attempts. Try again in 5 minutes."],
    ["without Retry-After", {}, "Too many attempts. Try again later."],
  ])("shows the rate limit text %s", async (_label, headers, text) => {
    const { user } = await renderEnrolled({ confirm: apiError(429, "rate_limited", headers) });
    await user.type(screen.getByLabelText("Six-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
  });

  it("signs out from the reauthentication error", async () => {
    const signOut = vi.fn().mockResolvedValue(undefined);
    mockMfaPost({ enroll: apiError(403, "mfa_reauthentication_required") });
    window.localStorage.removeItem("homayar_lang");
    renderConsole(
      <Suspense fallback={<p>Loading page</p>}>
        <AppRoutes />
        <LocationProbe />
      </Suspense>,
      {
        route: "/mfa/setup",
        lang: "en",
        auth: { status: "authenticated", user: FIXTURE_USER, workspace: FIXTURE_WORKSPACE, workspaces: [FIXTURE_WORKSPACE], signOut },
      },
    );
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Start setup" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("For your safety, sign in again to set up two-step verification.");
    await user.click(screen.getByRole("button", { name: "Sign in again" }));
    expect(signOut).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(location()).toBe("/login"));
  });

  it("starts again after the pending secret expired, with one more enroll POST", async () => {
    // First confirm expires the secret; the retry sends a new enroll.
    let expired = true;
    const calls: { url: string; body: unknown }[] = [];
    vi.spyOn(api, "post").mockImplementation(async (url: string, body?: unknown) => {
      calls.push({ url, body });
      if (url === "/auth/mfa/totp/enroll") return okResponse({ secret: SECRET, otpauth_uri: OTPAUTH });
      if (url === "/auth/mfa/totp/confirm") {
        if (expired) {
          expired = false;
          throw apiError(409, "mfa_enrollment_not_started");
        }
        return okResponse({ recovery_codes: RECOVERY_CODES });
      }
      throw apiError(500, "unexpected_post");
    });
    const user = userEvent.setup();
    renderSetup();
    await user.click(await screen.findByRole("button", { name: "Start setup" }));
    await user.type(screen.getByLabelText("Six-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("This setup expired. Start again.");
    await user.click(screen.getByRole("button", { name: "Start again" }));
    await waitFor(() => expect(calls.filter((call) => call.url === "/auth/mfa/totp/enroll")).toHaveLength(2));
  });

  it("moves to the verify page when the user already has a factor", async () => {
    mockMfaPost({ enroll: apiError(403, "mfa_required") });
    const user = userEvent.setup();
    renderSetup("/mfa/setup?next=/admin");
    await user.click(await screen.findByRole("button", { name: "Start setup" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Verify it's you" })).toBeInTheDocument();
    expect(location()).toBe("/mfa/verify?next=%2Fadmin");
  });

  it("shows the console's generic network text when enroll cannot reach the server", async () => {
    mockMfaPost({ enroll: networkError() });
    const user = userEvent.setup();
    renderSetup();
    await user.click(await screen.findByRole("button", { name: "Start setup" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The server could not be reached; check the connection and try again.");
  });

  it("signs out from the always-present Sign out button", async () => {
    const signOut = vi.fn().mockResolvedValue(undefined);
    mockMfaPost();
    const user = userEvent.setup();
    renderSetup("/mfa/setup", "en", signOut);
    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(signOut).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(location()).toBe("/login"));
  });

  it("stays on the page and explains when signing out fails", async () => {
    // A non-5xx failure, so the text is the sign-out fallback the Sidebar shows.
    const signOut = vi.fn().mockRejectedValue(apiError(400, "logout_failed"));
    mockMfaPost();
    const user = userEvent.setup();
    renderSetup("/mfa/setup?next=/admin", "en", signOut);
    await user.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to sign out; try again.");
    expect(location()).toBe("/mfa/setup?next=/admin");
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });
});

describe("MFA setup page in Persian (AC9)", () => {
  it("is right to left with the Persian heading and controls", async () => {
    mockMfaPost();
    const user = userEvent.setup();
    renderSetup("/mfa/setup", "fa");

    expect(await screen.findByRole("heading", { level: 1, name: "راه‌اندازی ورود دومرحله‌ای" })).toBeInTheDocument();
    expect(document.documentElement.dir).toBe("rtl");
    expect(screen.getByRole("button", { name: "شروع راه‌اندازی" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "شروع راه‌اندازی" }));
    expect(await screen.findByRole("img", { name: "کد QR برای برنامهٔ احراز هویت" })).toBeInTheDocument();
    expect(screen.getByText("کلید راه‌اندازی")).toBeInTheDocument();
    expect(screen.getByLabelText("کد شش‌رقمی")).toHaveAttribute("inputmode", "numeric");
    expect(screen.getByRole("button", { name: "تأیید" })).toBeInTheDocument();
    // The secret stays left to right (AC9)...
    expect(screen.getByText(GROUPED)).toHaveAttribute("dir", "ltr");
    // ...and so does the QR: its closest [dir] ancestor is the ltr wrapper.
    expect(screen.getByRole("img", { name: "کد QR برای برنامهٔ احراز هویت" }).closest("[dir]")).toHaveAttribute("dir", "ltr");
  });
});

describe("MFA secret handling (AC8)", () => {
  it("keeps the secret and the recovery codes out of storage, cache and console", async () => {
    mockMfaPost();
    mockStatusReads();
    const consoleSpies = (["log", "info", "warn", "error"] as const).map((method) => vi.spyOn(console, method));
    const view = renderSetup();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Start setup" }));
    await user.type(screen.getByLabelText("Six-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await screen.findByRole("heading", { name: "Save your recovery codes" });

    const local = storageText(window.localStorage);
    const session = storageText(window.sessionStorage);
    const cache = JSON.stringify(view.queryClient.getQueryCache().getAll());
    for (const surface of [local, session, cache]) {
      expect(surface).not.toContain(SECRET);
      expect(surface).not.toContain(RECOVERY_CODES[0]);
    }
    expect(location()).not.toContain(SECRET);
    for (const spy of consoleSpies) {
      expect(spy).not.toHaveBeenCalled();
    }
  });

  it("shows the codes only while the view is mounted: a fresh visit starts over", async () => {
    mockStatusReads();
    const { user, view } = await renderEnrolled();
    await user.type(screen.getByLabelText("Six-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await screen.findByRole("heading", { name: "Save your recovery codes" });
    await user.click(screen.getByRole("checkbox", { name: "I saved my recovery codes" }));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(location()).toBe("/admin"));
    view.unmount();

    // A new visit to the setup page is the intro again: the codes and the
    // secret left memory with the screen (component state, AC8).
    renderSetup();
    expect(await screen.findByRole("heading", { level: 1, name: "Set up two-step verification" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start setup" })).toBeInTheDocument();
    expect(screen.queryByText(RECOVERY_CODES[0])).not.toBeInTheDocument();
    expect(screen.queryByText(GROUPED)).not.toBeInTheDocument();
  });
});
