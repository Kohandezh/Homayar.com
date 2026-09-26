import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import LoginPage from "@/components/auth/LoginPage";
import type { AuthContextValue } from "@/lib/auth-context";
import { FIXTURE_USER, apiError, networkError, renderConsole, withAuthMethods } from "@/test/fixtures";

function renderLogin(options: Parameters<typeof renderConsole>[1] = {}) {
  return renderConsole(
    <Routes>
      <Route path="login" element={<LoginPage />} />
      <Route path="meetings/:id" element={<div>Meeting page</div>} />
      <Route index element={<div>Workspace overview</div>} />
    </Routes>,
    { route: "/login", ...options },
  );
}

describe("LoginPage", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("starts blank in Persian and navigates only after the server-backed sign-in resolves", async () => {
    const user = userEvent.setup();
    const signIn = vi.fn<AuthContextValue["signIn"]>().mockResolvedValue(FIXTURE_USER);
    renderLogin({ auth: { signIn } });

    const email = screen.getByLabelText("نشانی ایمیل");
    const password = screen.getByLabelText("گذرواژه");
    expect(email).toHaveValue("");
    expect(password).toHaveValue("");
    expect(document.documentElement.dir).toBe("rtl");

    await user.type(email, "person@example.com");
    await user.type(password, "strong-password1");
    await user.click(screen.getByRole("button", { name: "ورود" }));

    expect(signIn).toHaveBeenCalledWith({ email: "person@example.com", password: "strong-password1" });
    expect(await screen.findByText("Workspace overview")).toBeInTheDocument();
  });

  it("returns to the page RequireAuth recorded, never to another origin", async () => {
    const user = userEvent.setup();
    const signIn = vi.fn<AuthContextValue["signIn"]>().mockResolvedValue(FIXTURE_USER);
    renderLogin({
      auth: { signIn },
      route: { pathname: "/login", state: { from: { pathname: "/meetings/m-1", search: "?tab=transcript" } } },
    });

    await user.type(screen.getByLabelText("نشانی ایمیل"), "person@example.com");
    await user.type(screen.getByLabelText("گذرواژه"), "strong-password1");
    await user.click(screen.getByRole("button", { name: "ورود" }));

    expect(await screen.findByText("Meeting page")).toBeInTheDocument();
  });

  it("maps invalid credentials, rate limits and network failures to localized messages", async () => {
    const user = userEvent.setup();
    const signIn = vi
      .fn<AuthContextValue["signIn"]>()
      .mockRejectedValueOnce(apiError(401, "invalid_credentials"))
      .mockRejectedValueOnce(apiError(429, "rate_limited", { headers: { "Retry-After": "300" } }))
      .mockRejectedValueOnce(networkError());
    renderLogin({ auth: { signIn }, lang: "en" });

    await user.type(screen.getByLabelText("Email address"), "person@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong-password1");
    const submit = screen.getByRole("button", { name: "Sign in" });

    await user.click(submit);
    expect(await screen.findByRole("alert")).toHaveTextContent("The email or password is incorrect.");
    await user.click(submit);
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Try again in 5 min.");
    await user.click(submit);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the server.");
    expect(screen.queryByText(/fixture/)).not.toBeInTheDocument();
  });

  it("explains an expired session and Google callback errors", () => {
    renderLogin({ auth: { endReason: "expired" }, lang: "en" });
    expect(screen.getByRole("status")).toHaveTextContent("Your session ended. Sign in again to continue.");
  });

  it("shows the account-link message for a Google callback that could not be merged", () => {
    renderLogin({ route: "/login?error=account_link_required", lang: "en" });
    expect(screen.getByRole("alert")).toHaveTextContent("already belongs to a Homayar account");
  });

  it("offers Google and phone sign-in only when the server reports them available", () => {
    const { unmount } = renderLogin({ lang: "en", queryClient: withAuthMethods({}) });
    expect(screen.queryByRole("button", { name: "Continue with Google" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Sign in with a phone code" })).not.toBeInTheDocument();
    unmount();

    renderLogin({ lang: "en", queryClient: withAuthMethods({ google: true, phone: true }) });
    expect(screen.getByRole("button", { name: "Continue with Google" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in with a phone code" })).toBeInTheDocument();
  });
});
