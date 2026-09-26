import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import PhoneOtpRequestPage from "@/components/auth/PhoneOtpRequestPage";
import PhoneOtpVerifyPage from "@/components/auth/PhoneOtpVerifyPage";
import * as auth from "@/lib/auth";
import type { AuthContextValue } from "@/lib/auth-context";
import { FIXTURE_USER, apiError, renderConsole, withAuthMethods } from "@/test/fixtures";

const NOW = new Date("2026-09-24T12:00:00Z").getTime();

function verifyRoute(overrides: Partial<{ expiresAt: number; resendAt: number }> = {}) {
  return {
    pathname: "/phone/verify",
    state: {
      phoneNumber: "+989121234567",
      challengeId: "challenge-1",
      expiresAt: NOW + 180_000,
      resendAt: NOW + 60_000,
      ...overrides,
    },
  };
}

function renderVerify(route = verifyRoute(), auth: Partial<AuthContextValue> = {}) {
  return renderConsole(
    <Routes>
      <Route path="phone/verify" element={<PhoneOtpVerifyPage />} />
      <Route path="phone" element={<div>Phone request</div>} />
      <Route index element={<div>Workspace overview</div>} />
    </Routes>,
    { route, lang: "en", auth },
  );
}

describe("phone sign-in", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("shows an honest unavailable state when the server does not offer phone sign-in", () => {
    const request = vi.spyOn(auth, "requestPhoneOtp");
    renderConsole(<PhoneOtpRequestPage />, { lang: "en", queryClient: withAuthMethods({ phone: false }) });
    expect(screen.getByRole("heading", { name: "Phone sign-in is not available" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Mobile number")).not.toBeInTheDocument();
    expect(request).not.toHaveBeenCalled();
  });

  it("reports an unavailable SMS provider (503) as unavailable, not as a wrong number", async () => {
    vi.spyOn(auth, "requestPhoneOtp").mockRejectedValue(apiError(503, "phone_auth_unavailable"));
    renderConsole(<PhoneOtpRequestPage />, { lang: "en", queryClient: withAuthMethods({ phone: true }) });

    fireEvent.change(screen.getByLabelText("Mobile number"), { target: { value: "۰۹۱۲۱۲۳۴۵۶۷" } });
    await act(async () => {
      fireEvent.submit(screen.getByRole("button", { name: "Send code" }).closest("form")!);
    });

    expect(auth.requestPhoneOtp).toHaveBeenCalledWith("09121234567");
    expect(screen.getByRole("alert")).toHaveTextContent("Phone sign-in is not available right now.");
  });

  it("reports a refused SMS delivery (503 otp_delivery_failed) as a retryable sending failure", async () => {
    vi.spyOn(auth, "requestPhoneOtp").mockRejectedValue(
      apiError(503, "otp_delivery_failed", { headers: { "Retry-After": "30" } }),
    );
    renderConsole(<PhoneOtpRequestPage />, { lang: "en", queryClient: withAuthMethods({ phone: true }) });
    fireEvent.change(screen.getByLabelText("Mobile number"), { target: { value: "09121234567" } });
    await act(async () => {
      fireEvent.submit(screen.getByRole("button", { name: "Send code" }).closest("form")!);
    });
    expect(screen.getByRole("alert")).toHaveTextContent("The code could not be sent.");
    expect(screen.getByRole("alert")).not.toHaveTextContent(/number/i);
  });

  it("counts down the server expiry, normalizes Persian digits and signs in", async () => {
    const verify = vi.spyOn(auth, "verifyPhoneOtp").mockResolvedValue(FIXTURE_USER);
    const refreshUser = vi.fn<AuthContextValue["refreshUser"]>().mockResolvedValue(FIXTURE_USER);
    renderVerify(verifyRoute(), { refreshUser });

    expect(screen.getByText("The code expires in 3:00.")).toBeInTheDocument();
    expect(screen.getByText("You can request a new code in 1:00.")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(screen.getByText("The code expires in 2:55.")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("One-time code"), { target: { value: "۱۲۳۴۵۶" } });
    expect(screen.getByLabelText("One-time code")).toHaveValue("123456");
    await act(async () => {
      fireEvent.submit(screen.getByRole("button", { name: "Verify code" }).closest("form")!);
    });

    expect(verify).toHaveBeenCalledWith("challenge-1", "123456");
    expect(refreshUser).toHaveBeenCalled();
    expect(screen.getByText("Workspace overview")).toBeInTheDocument();
  });

  it("disables verification once the code has expired", () => {
    const verify = vi.spyOn(auth, "verifyPhoneOtp");
    renderVerify(verifyRoute({ expiresAt: NOW + 2_000 }));
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(screen.getByRole("alert")).toHaveTextContent("This code has expired. Request a new one.");
    expect(screen.getByRole("button", { name: "Verify code" })).toBeDisabled();
    expect(verify).not.toHaveBeenCalled();
  });

  it("offers a resend only after the cooldown and honours a server cooldown", async () => {
    vi.spyOn(auth, "requestPhoneOtp").mockRejectedValue(
      apiError(429, "otp_cooldown", { headers: { "Retry-After": "45" } }),
    );
    renderVerify(verifyRoute({ resendAt: NOW + 1_000 }));
    expect(screen.queryByRole("button", { name: "Send a new code" })).not.toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(2_000);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Send a new code" }));
    });
    expect(auth.requestPhoneOtp).toHaveBeenCalledWith("+989121234567");
    expect(screen.getByRole("alert")).toHaveTextContent("Too many attempts. Try again in 1 min.");
    expect(screen.getByText("You can request a new code in 0:45.")).toBeInTheDocument();
  });

  it("shows the invalid-code message without echoing the code", async () => {
    vi.spyOn(auth, "verifyPhoneOtp").mockRejectedValue(apiError(400, "invalid_otp"));
    renderVerify();
    fireEvent.change(screen.getByLabelText("One-time code"), { target: { value: "000000" } });
    await act(async () => {
      fireEvent.submit(screen.getByRole("button", { name: "Verify code" }).closest("form")!);
    });
    expect(screen.getByRole("alert")).toHaveTextContent("The code is wrong, expired, or replaced by a newer code.");
    expect(screen.getByRole("alert")).not.toHaveTextContent("000000");
  });

  it("sends a visitor without a challenge back to the request step", () => {
    renderVerify({ pathname: "/phone/verify", state: undefined } as never);
    expect(screen.getByRole("link", { name: "Request a code" })).toBeInTheDocument();
  });
});
