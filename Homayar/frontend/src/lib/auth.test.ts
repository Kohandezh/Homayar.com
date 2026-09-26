import { afterEach, describe, expect, it, vi } from "vitest";
import api from "@/lib/api";
import {
  login,
  logout,
  register,
  requestPasswordReset,
  requestPhoneOtp,
  resetPassword,
  verifyEmail,
  verifyPhoneOtp,
} from "@/lib/auth";

describe("auth API", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("uses the expected auth endpoints and request payloads", async () => {
    const response = {
      data: {
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
        workspaces: [],
        access_expires_in: 900,
        requires_verification: false,
      },
    } as Awaited<ReturnType<typeof api.post>>;
    const post = vi.spyOn(api, "post").mockResolvedValue(response);

    await login({ email: "person@example.com", password: "strong-password1" });
    await logout();
    await register({ name: "Person", email: "person@example.com", password: "strong-password1" });
    await verifyEmail("verify-token");
    await requestPasswordReset("person@example.com");
    await resetPassword("reset-token", "new-password1");
    await requestPhoneOtp("09121234567");
    await verifyPhoneOtp("challenge-1", "123456");

    expect(post).toHaveBeenNthCalledWith(1, "/auth/login", {
      identifier: "person@example.com",
      password: "strong-password1",
    });
    expect(post).toHaveBeenNthCalledWith(2, "/auth/logout");
    expect(post).toHaveBeenNthCalledWith(3, "/auth/register", {
      display_name: "Person",
      email: "person@example.com",
      password: "strong-password1",
      locale: "fa",
    });
    expect(post).toHaveBeenNthCalledWith(4, "/auth/verify-email", { token: "verify-token" });
    expect(post).toHaveBeenNthCalledWith(5, "/auth/forgot-password", { email: "person@example.com" });
    expect(post).toHaveBeenNthCalledWith(6, "/auth/reset-password", {
      token: "reset-token",
      password: "new-password1",
    });
    expect(post).toHaveBeenNthCalledWith(7, "/auth/otp/request", { phone: "09121234567" });
    expect(post).toHaveBeenNthCalledWith(8, "/auth/otp/verify", {
      challenge_id: "challenge-1",
      code: "123456",
    });
  });
});
