import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router";
import InvitationAcceptPage from "@/components/platform/InvitationAcceptPage";
import { renderConsole } from "@/test/fixtures";

const TOKEN = "i".repeat(43);

function RegisterProbe() {
  const location = useLocation();
  return <p data-testid="register-probe">{location.search}</p>;
}

function renderAccept(route: string, options: Parameters<typeof renderConsole>[1] = {}) {
  return renderConsole(
    <Routes>
      <Route path="invitations/accept" element={<InvitationAcceptPage />} />
      <Route path="login" element={<div>Login page</div>} />
      <Route path="register" element={<RegisterProbe />} />
    </Routes>,
    { route, ...options },
  );
}

describe("InvitationAcceptPage (signed out)", () => {
  it("offers sign-in and account creation with a valid token", async () => {
    const user = userEvent.setup();
    renderAccept(`/invitations/accept?token=${TOKEN}`, { lang: "en" });

    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create an account" }));
    expect(screen.getByTestId("register-probe")).toHaveTextContent(
      `invitation=${TOKEN}`,
    );
  });

  it("offers account creation in Persian too", () => {
    renderAccept(`/invitations/accept?token=${TOKEN}`);

    expect(screen.getByRole("button", { name: "ورود" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ساخت حساب" })).toBeInTheDocument();
  });

  it("offers no account creation when the token is not plausible", () => {
    renderAccept("/invitations/accept?token=short", { lang: "en" });

    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create an account" }),
    ).not.toBeInTheDocument();
  });
});
