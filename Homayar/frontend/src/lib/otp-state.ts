import type { OtpChallenge } from "@/types/auth";

/**
 * Router state handed from the phone request step to the code step. Times are
 * epoch milliseconds computed from the server's `expires_in` / `resend_after`.
 * The one-time code itself is never stored.
 */
export interface OtpRouteState {
  phoneNumber: string;
  challengeId: string;
  expiresAt: number;
  resendAt: number;
}

export function otpRouteState(phoneNumber: string, challenge: OtpChallenge, now = Date.now()): OtpRouteState {
  return {
    phoneNumber,
    challengeId: challenge.challenge_id,
    expiresAt: now + challenge.expires_in * 1000,
    resendAt: now + challenge.resend_after * 1000,
  };
}

export function readOtpRouteState(state: unknown): OtpRouteState | null {
  const candidate = state as Partial<OtpRouteState> | null;
  if (
    !candidate ||
    typeof candidate.phoneNumber !== "string" ||
    typeof candidate.challengeId !== "string" ||
    !candidate.challengeId ||
    typeof candidate.expiresAt !== "number" ||
    typeof candidate.resendAt !== "number"
  ) {
    return null;
  }
  return {
    phoneNumber: candidate.phoneNumber,
    challengeId: candidate.challengeId,
    expiresAt: candidate.expiresAt,
    resendAt: candidate.resendAt,
  };
}
