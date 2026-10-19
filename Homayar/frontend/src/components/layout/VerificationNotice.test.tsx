import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import { Route, Routes } from "react-router";
import api from "@/lib/api";
import type { AuthContextValue } from "@/lib/auth-context";
import AppLayout from "@/components/layout/AppLayout";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";

const UNVERIFIED = { ...FIXTURE_USER, email_verified: false, phone_verified: false };

function renderLayout(refreshUser = vi.fn<AuthContextValue["refreshUser"]>().mockResolvedValue(FIXTURE_USER)) {
  return renderConsole(
    <Routes>
      <Route path="/" element={<AppLayout />}>
        <Route index element={<div>Overview</div>} />
      </Route>
    </Routes>,
    {
      lang: "en",
      auth: { status: "authenticated", user: UNVERIFIED, workspace: FIXTURE_WORKSPACE, workspaces: [FIXTURE_WORKSPACE], refreshUser },
    },
  );
}

describe("VerificationNotice (gap G2)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("requests a new link once and holds the button during the server's cooldown", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse({ status: "accepted", resend_after: 60 }, 202));
    renderLayout();
    const button = screen.getByRole("button", { name: "Resend verification email" });
    await act(async () => {
      fireEvent.click(button);
    });
    expect(post).toHaveBeenCalledWith("/auth/verify-email/resend");
    expect(screen.getByText(/A new verification link was sent to your email; earlier links no longer work\./)).toBeInTheDocument();
    expect(button).toHaveAttribute("aria-disabled", "true");
    await act(async () => {
      fireEvent.click(button);
    });
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("explains the cooldown from Retry-After and refreshes the session when already verified", async () => {
    vi.spyOn(api, "post").mockRejectedValueOnce(
      apiError(429, "verification_cooldown", { headers: { "Retry-After": "45" } }),
    );
    const refreshUser = vi.fn<AuthContextValue["refreshUser"]>().mockResolvedValue(FIXTURE_USER);
    const view = renderLayout(refreshUser);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Resend verification email" }));
    });
    expect(screen.getByText(/A link was sent recently; try again in 1 minute\(s\)\./)).toBeInTheDocument();
    view.unmount();

    vi.spyOn(api, "post").mockResolvedValueOnce(okResponse({ status: "already_verified", resend_after: 0 }, 202));
    renderLayout(refreshUser);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Resend verification email" }));
    });
    expect(refreshUser).toHaveBeenCalled();
  });
});
