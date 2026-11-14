/** The MFA endpoints' answers (backend/routers/v1/auth_mfa.py, ADR 0076 D2-D5). */

/** GET /auth/mfa: the session's MFA state. Holds no secret, so it may be cached. */
export interface MfaStatus {
  totp_enrolled: boolean;
  recovery_codes_remaining: number;
  session_verified: boolean;
  /** True only when the server gate is on and the user is privileged. */
  required: boolean;
}

/** POST /auth/mfa/totp/enroll: the pending secret and its otpauth URI, shown once. */
export interface TotpEnrollment {
  secret: string;
  otpauth_uri: string;
}

/** POST /auth/mfa/totp/confirm and POST /auth/mfa/recovery-codes: a fresh set, shown once. */
export interface RecoveryCodes {
  recovery_codes: string[];
}

/** POST /auth/mfa/verify. */
export interface MfaVerifyResult {
  verified: true;
  method: "totp" | "recovery_code";
  recovery_codes_remaining: number;
}
