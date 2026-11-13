import { Suspense } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router";
import api from "@/lib/api";
import { AppRoutes } from "@/AppRoutes";
import { mfaQueryKeys } from "@/lib/mfa-api";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";

const NOT_REQUIRED = { totp_enrolled: true, recovery_codes_remaining: 9, session_verified: false, required: false };

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{`${location.pathname}${location.search}${location.hash}`}</output>;
}

function location() {
  return screen.getByTestId("location").textContent;
}

function renderVerify(route = "/mfa/verify?next=/admin", lang: "fa" | "en" = "en", signOut = vi.fn().mockResolvedValue(undefined)) {
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

function mockVerify(answer: unknown | Error) {
  const calls: { url: string; body: unknown }[] = [];
  vi.spyOn(api, "post").mockImplementation(async (url: string, body?: unknown) => {
    calls.push({ url, body });
    if (url === "/auth/mfa/verify") {
      if (answer instanceof Error) throw answer;
      return okResponse(answer);
    }
    throw apiError(500, "unexpected_post");
  });
  return calls;
}

function mockStatusReads(status: unknown = { ...NOT_REQUIRED, session_verified: true }) {
  const urls: string[] = [];
  vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    urls.push(url);
    if (url === "/auth/mfa") return okResponse(status);
    throw apiError(404, "not_found");
  });
  return urls;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("MFA verify page (AC5)", () => {
  it("opens in authenticator mode with the numeric code field", async () => {
    mockVerify(apiError(400, "mfa_code_invalid"));
    renderVerify();

    expect(await screen.findByRole("heading", { level: 1, name: "Verify it's you" })).toBeInTheDocument();
    const field = screen.getByLabelText("Six-digit code");
    expect(field).toHaveAttribute("inputmode", "numeric");
    expect(field).toHaveAttribute("autocomplete", "one-time-code");
    expect(screen.getByRole("button", { name: "Verify" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use a recovery code" })).toBeInTheDocument();
  });

  it("sends nothing for an empty code, in both modes", async () => {
    const calls = mockVerify(apiError(400, "mfa_code_invalid"));
    const user = userEvent.setup();
    renderVerify();

    await user.click(await screen.findByRole("button", { name: "Verify" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter the six-digit code from your app.");
    expect(calls).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Use a recovery code" }));
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter one of your recovery codes.");
    expect(calls).toEqual([]);
  });

  it("sends the trimmed code to POST /auth/mfa/verify", async () => {
    const calls = mockVerify(apiError(400, "mfa_code_invalid"));
    const user = userEvent.setup();
    renderVerify();
    await user.type(await screen.findByLabelText("Six-digit code"), " 123456 ");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ url: "/auth/mfa/verify", body: { code: "123456" } });
  });

  it("switches to recovery mode with plain-text field attributes, and back", async () => {
    mockVerify(apiError(400, "mfa_code_invalid"));
    const user = userEvent.setup();
    renderVerify();
    await screen.findByRole("heading", { level: 1, name: "Verify it's you" });

    await user.click(screen.getByRole("button", { name: "Use a recovery code" }));
    const recovery = screen.getByLabelText("Recovery code");
    expect(recovery).toHaveAttribute("autocomplete", "off");
    expect(recovery).toHaveAttribute("autocapitalize", "off");
    expect(recovery).toHaveAttribute("spellcheck", "false");
    expect(recovery).not.toHaveAttribute("inputmode");
    expect(screen.getByRole("button", { name: "Use authenticator code" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Use authenticator code" }));
    expect(screen.getByLabelText("Six-digit code")).toBeInTheDocument();
    expect(screen.queryByLabelText("Recovery code")).not.toBeInTheDocument();
  });

  it("clears the field and any error when switching modes", async () => {
    mockVerify(apiError(400, "mfa_code_invalid"));
    const user = userEvent.setup();
    renderVerify();
    await user.type(await screen.findByLabelText("Six-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That code is not correct. Try the newest code from your app.");

    await user.click(screen.getByRole("button", { name: "Use a recovery code" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Recovery code")).toHaveValue("");
  });

  it("returns to next at once when a TOTP code verifies, with the status re-read", async () => {
    const calls = mockVerify({ verified: true, method: "totp", recovery_codes_remaining: 9 });
    const urls = mockStatusReads();
    const view = renderVerify();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Six-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    await waitFor(() => expect(location()).toBe("/admin"));
    expect(urls).toContain("/auth/mfa");
    expect(view.queryClient.getQueryData(mfaQueryKeys.status())).toEqual({ ...NOT_REQUIRED, session_verified: true });
    expect(calls).toHaveLength(1);
  });

  it("shows the remaining count after a recovery code verifies, then Continue returns", async () => {
    mockVerify({ verified: true, method: "recovery_code", recovery_codes_remaining: 9 });
    mockStatusReads();
    const user = userEvent.setup();
    renderVerify();
    await user.click(await screen.findByRole("button", { name: "Use a recovery code" }));
    await user.type(screen.getByLabelText("Recovery code"), "aaaaa-bbbbb");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByText("You used a recovery code. 9 recovery codes left.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Make new recovery codes" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(location()).toBe("/admin"));
  });

  it("offers new recovery codes when three or fewer are left", async () => {
    mockVerify({ verified: true, method: "recovery_code", recovery_codes_remaining: 2 });
    mockStatusReads();
    const user = userEvent.setup();
    renderVerify();
    await user.click(await screen.findByRole("button", { name: "Use a recovery code" }));
    await user.type(screen.getByLabelText("Recovery code"), "aaaaa-bbbbb");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByText("You used a recovery code. 2 recovery codes left.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Make new recovery codes" })).toHaveAttribute("href", "/account/security");
  });

  it.each([
    ["authenticator", "mfa_code_invalid", "That code is not correct. Try the newest code from your app.", false],
    ["recovery", "mfa_code_invalid", "That code is not correct.", true],
  ])("shows the %s text for a wrong code", async (_mode, code, text, recovery) => {
    mockVerify(apiError(400, code));
    const user = userEvent.setup();
    renderVerify();
    if (recovery) await user.click(await screen.findByRole("button", { name: "Use a recovery code" }));
    const field = await screen.findByLabelText(recovery ? "Recovery code" : "Six-digit code");
    await user.type(field, "000000");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
  });

  it.each([
    ["rate limited with a header", apiError(429, "rate_limited", { headers: { "Retry-After": "120" } }), "Too many attempts. Try again in 2 minutes."],
    ["rate limited without a header", apiError(429, "rate_limited"), "Too many attempts. Try again later."],
    ["unavailable", apiError(503, "mfa_unavailable"), "Two-step verification is not available on this server. Ask your administrator."],
  ])("shows the text when %s", async (_label, error, text) => {
    mockVerify(error);
    const user = userEvent.setup();
    renderVerify();
    await user.type(await screen.findByLabelText("Six-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
  });

  it("moves to the setup page when the user has no factor", async () => {
    mockVerify(apiError(409, "mfa_not_enrolled"));
    const user = userEvent.setup();
    renderVerify("/mfa/verify?next=/admin");
    await user.type(await screen.findByLabelText("Six-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Set up two-step verification" })).toBeInTheDocument();
    expect(location()).toBe("/mfa/setup?next=%2Fadmin");
  });

  it("signs out", async () => {
    mockVerify(apiError(400, "mfa_code_invalid"));
    const signOut = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderVerify("/mfa/verify", "en", signOut);
    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(signOut).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(location()).toBe("/login"));
  });

  it("stays on the page and explains when signing out fails", async () => {
    mockVerify(apiError(400, "mfa_code_invalid"));
    const signOut = vi.fn().mockRejectedValue(apiError(400, "logout_failed"));
    const user = userEvent.setup();
    renderVerify("/mfa/verify?next=/admin", "en", signOut);
    await user.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to sign out; try again.");
    expect(location()).toBe("/mfa/verify?next=/admin");
    expect(screen.getByRole("heading", { level: 1, name: "Verify it's you" })).toBeInTheDocument();
  });
});

describe("MFA verify page in Persian (AC9)", () => {
  it("is right to left, with Persian labels and Persian digits in the remaining count", async () => {
    mockVerify({ verified: true, method: "recovery_code", recovery_codes_remaining: 2 });
    mockStatusReads();
    const user = userEvent.setup();
    renderVerify("/mfa/verify", "fa");

    expect(await screen.findByRole("heading", { level: 1, name: "هویت خود را تأیید کنید" })).toBeInTheDocument();
    expect(document.documentElement.dir).toBe("rtl");
    await user.click(screen.getByRole("button", { name: "استفاده از کد بازیابی" }));
    await user.type(screen.getByLabelText("کد بازیابی"), "aaaaa-bbbbb");
    await user.click(screen.getByRole("button", { name: "بررسی کد" }));

    expect(await screen.findByText("شما از یک کد بازیابی استفاده کردید. ۲ کد بازیابی باقی مانده است.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "ساخت کدهای بازیابی تازه" })).toHaveAttribute("href", "/account/security");
    expect(screen.getByRole("button", { name: "ادامه" })).toBeInTheDocument();
  });
});
