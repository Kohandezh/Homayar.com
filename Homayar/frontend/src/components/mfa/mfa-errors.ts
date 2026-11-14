import { apiErrorInfo, minutesUntil } from "@/lib/api-errors";
import { describeApiError } from "@/lib/error-messages";
import type { Lang } from "@/lib/i18n-context";

/**
 * What an MFA form shows after a failed enrol, confirm or verify call. The
 * HTTP status and error codes come from the API table of HOM-241 (ADR 0076).
 */
export type MfaFormError =
  | { kind: "empty" }
  | { kind: "code_invalid" }
  | { kind: "rate_limited"; minutes: number }
  | { kind: "rate_limited_later" }
  | { kind: "reauthentication" }
  | { kind: "expired" }
  | { kind: "unavailable" }
  | { kind: "generic"; text: string };

/**
 * Map an API error to a form error. `mfa_required` is deliberately absent:
 * it means the user already has a factor and must verify instead, so the
 * pages navigate to /mfa/verify before they ever call this.
 */
export function mfaFormError(caught: unknown, lang: Lang, fallback: string): MfaFormError {
  const info = apiErrorInfo(caught);
  switch (info.code) {
    case "mfa_code_invalid":
      return { kind: "code_invalid" };
    case "rate_limited":
      return info.retryAfterSeconds !== null
        ? { kind: "rate_limited", minutes: minutesUntil(info.retryAfterSeconds) }
        : { kind: "rate_limited_later" };
    case "mfa_reauthentication_required":
      return { kind: "reauthentication" };
    case "mfa_enrollment_not_started":
      return { kind: "expired" };
    case "mfa_unavailable":
      return { kind: "unavailable" };
    default:
      return { kind: "generic", text: describeApiError(caught, { lang, fallback }).text };
  }
}
