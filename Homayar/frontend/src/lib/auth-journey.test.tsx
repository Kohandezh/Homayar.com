import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import api, { AUTH_EXPIRED_EVENT } from "@/lib/api";
import { apiErrorInfo, parseRetryAfter } from "@/lib/api-errors";
import AuthProvider from "@/lib/AuthProvider";
import { useAuth } from "@/lib/auth-context";
import { I18nProvider } from "@/lib/i18n";
import { safeReturnPath } from "@/lib/return-path";
import RegisterPage from "@/components/auth/RegisterPage";
import { FIXTURE_WORKSPACE, apiError, networkError, okResponse, renderConsole, testQueryClient } from "@/test/fixtures";

describe("api error parsing", () => {
  it("reads the product error envelope and Retry-After", () => {
    const info = apiErrorInfo(apiError(429, "audio_quota_exceeded", { headers: { "Retry-After": "30" }, details: { remaining: 0 } }));
    expect(info).toMatchObject({
      status: 429,
      code: "audio_quota_exceeded",
      requestId: "req-fixture",
      retryAfterSeconds: 30,
      details: { remaining: 0 },
      network: false,
    });
    expect(apiErrorInfo(networkError())).toMatchObject({ status: null, network: true });
    expect(apiErrorInfo(new Error("plain"))).toMatchObject({ code: null, network: false });
  });

  it("parses delta-seconds and HTTP-date Retry-After values", () => {
    const now = Date.parse("2026-09-24T12:00:00Z");
    expect(parseRetryAfter("120", now)).toBe(120);
    expect(parseRetryAfter("Thu, 24 Sep 2026 12:01:30 GMT", now)).toBe(90);
    expect(parseRetryAfter("soon", now)).toBeNull();
    expect(parseRetryAfter(null, now)).toBeNull();
  });
});

describe("safeReturnPath", () => {
  it("keeps an in-app location with its query and fragment", () => {
    expect(safeReturnPath({ from: { pathname: "/meetings/m-1", search: "?tab=summary", hash: "#s-2" } })).toBe(
      "/meetings/m-1?tab=summary#s-2",
    );
  });

  it("refuses other origins, protocol-relative paths and auth pages", () => {
    expect(safeReturnPath({ from: { pathname: "//evil.example/x" } })).toBe("/");
    expect(safeReturnPath({ from: { pathname: "https://evil.example" } })).toBe("/");
    expect(safeReturnPath({ from: { pathname: "/\\evil.example" } })).toBe("/");
    expect(safeReturnPath({ from: { pathname: "/login" } })).toBe("/");
    expect(safeReturnPath({ from: { pathname: "/phone/verify" } })).toBe("/");
    expect(safeReturnPath(null)).toBe("/");
  });
});

function SessionProbe() {
  const { status, endReason } = useAuth();
  return <p>{`${status}:${endReason ?? "none"}`}</p>;
}

function renderProvider() {
  return render(
    <QueryClientProvider client={testQueryClient()}>
      <I18nProvider>
        <AuthProvider>
          <SessionProbe />
        </AuthProvider>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

const SESSION = {
  user: {
    id: "user-1",
    email: "person@example.com",
    phone_e164: null,
    display_name: "Person",
    locale: "fa",
    email_verified: true,
    phone_verified: false,
    is_active: true,
  },
  workspaces: [FIXTURE_WORKSPACE],
  access_expires_in: 900,
  requires_verification: false,
};

describe("session end tracking", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("marks a live session as expired and clears the stored workspace", async () => {
    vi.spyOn(api, "get").mockResolvedValue(okResponse(SESSION));
    renderProvider();
    expect(await screen.findByText("authenticated:none")).toBeInTheDocument();
    expect(window.localStorage.getItem("homayar_workspace_id")).toBe("ws-1");

    act(() => {
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
    });
    expect(screen.getByText("anonymous:expired")).toBeInTheDocument();
    expect(window.localStorage.getItem("homayar_workspace_id")).toBeNull();
  });

  it("does not call a first anonymous visit an expired session", async () => {
    vi.spyOn(api, "get").mockRejectedValue(apiError(401, "authentication_required"));
    renderProvider();
    act(() => {
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
    });
    await waitFor(() => expect(screen.getByText("anonymous:none")).toBeInTheDocument());
  });
});

describe("RegisterPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("registers in the chosen language, clears the password and explains verification honestly", async () => {
    const user = userEvent.setup();
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse({ status: "pending_verification" }, 202));
    renderConsole(<RegisterPage />, { lang: "en" });

    await user.type(screen.getByLabelText("Full name"), "Person");
    await user.type(screen.getByLabelText("Work email"), "person@example.com");
    await user.type(screen.getByLabelText("Password"), "strong-password1");
    await user.type(screen.getByLabelText("Confirm password"), "strong-password1");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(post).toHaveBeenCalledWith("/auth/register", {
      display_name: "Person",
      email: "person@example.com",
      password: "strong-password1",
      locale: "en",
    });
    expect(await screen.findByText(/Account created for/)).toBeInTheDocument();
    expect(screen.getByText(/sign in and request a new link/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
  }, 15_000); // types four fields with userEvent; slower CI machines exceed 5 s

  it("maps an existing account and a weak password to specific messages", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "post")
      .mockRejectedValueOnce(apiError(409, "account_exists"))
      .mockRejectedValueOnce(
        apiError(422, "validation_error", { details: [{ field: "body.password", message: "Password must…" }] }),
      );
    renderConsole(<RegisterPage />);

    await user.type(screen.getByLabelText("نام و نام خانوادگی"), "شخص");
    await user.type(screen.getByLabelText("ایمیل کاری"), "person@example.com");
    await user.type(screen.getByLabelText("گذرواژه"), "strong-password1");
    await user.type(screen.getByLabelText("تکرار گذرواژه"), "strong-password1");
    await user.click(screen.getByRole("button", { name: "ساخت حساب" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("برای این ایمیل قبلاً حساب ساخته شده است");

    await user.click(screen.getByRole("button", { name: "ساخت حساب" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("دست‌کم ۱۰ نویسه");
  });

  it("stops a mismatched confirmation before calling the API", async () => {
    const user = userEvent.setup();
    const post = vi.spyOn(api, "post");
    renderConsole(<RegisterPage />, { lang: "en" });
    await user.type(screen.getByLabelText("Full name"), "Person");
    await user.type(screen.getByLabelText("Work email"), "person@example.com");
    await user.type(screen.getByLabelText("Password"), "strong-password1");
    await user.type(screen.getByLabelText("Confirm password"), "strong-password2");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(screen.getByRole("alert")).toHaveTextContent("The passwords do not match.");
    expect(post).not.toHaveBeenCalled();
  });
});
