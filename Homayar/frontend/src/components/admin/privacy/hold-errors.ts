import { HOLD_ERROR_CODES } from "@/components/admin/privacy/privacy-copy";
import { describeApiError } from "@/lib/error-messages";
import type { Lang } from "@/lib/i18n-context";

/**
 * The message of a failed hold or release (SPEC section 4), and where it
 * goes: a bad review date is shown under the date field, anything else in
 * the dialog body. Codes outside the table use the shared mapping.
 */
export function describeHoldError(
  error: unknown,
  lang: Lang,
  fallback: string,
): { text: string; onReviewDate: boolean } {
  const described = describeApiError(error, { lang, fallback, codes: HOLD_ERROR_CODES });
  return { text: described.text, onReviewDate: described.code === "hold_review_invalid" };
}
