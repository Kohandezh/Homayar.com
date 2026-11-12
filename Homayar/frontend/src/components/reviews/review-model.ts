import { apiErrorInfo } from "@/lib/api-errors";
import { toAsciiDigits } from "@/lib/format";
import type { ReviewsCopy } from "@/components/reviews/reviews-copy";
import type { OverrideReasonCode, ReviewerOutcome } from "@/types/reviews";

/** The maximum distance of a due time (ADR 0051: later than now, at most 90 days). */
export const DUE_AT_MAX_DAYS = 90;

/** `datetime-local` value (local time, minutes) of an ISO string. */
export function toLocalDateTimeInput(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** The rule the server also checks: later than now, at most 90 days ahead. */
export function isDueAtInRange(value: string, now = Date.now()): boolean {
  const due = new Date(value).getTime();
  if (Number.isNaN(due)) return false;
  return due > now && due <= now + DUE_AT_MAX_DAYS * 24 * 60 * 60 * 1000;
}

/** An invisible item and a missing one answer the same 404 (ADR 0051 D4). */
export function isReviewItemNotFound(error: unknown): boolean {
  const info = apiErrorInfo(error);
  return info.code === "review_item_not_found" || info.status === 404;
}

/** The compare-and-set refused the write; the latest revision must be loaded. */
export function isRevisionConflict(error: unknown): boolean {
  return apiErrorInfo(error).code === "review_item_revision_conflict";
}

/** A machine or reviewer outcome, with the one label the console knows. */
export function outcomeLabel(outcome: string, copy: ReviewsCopy): string {
  if (outcome === "passed") return copy.outcomes.passed;
  if (outcome === "failed") return copy.outcomes.failed;
  if (outcome === "not_applicable") return copy.outcomes.not_applicable;
  if (outcome === "insufficient_evidence") return copy.machineOutcomeInsufficient;
  return copy.unknownOutcome(outcome);
}

export function overrideReasonLabel(code: string, copy: ReviewsCopy): string {
  const reasons = copy.overrideReasons as Record<string, string | undefined>;
  return reasons[code] ?? copy.unknownOutcome(code);
}

export const REVIEWER_OUTCOMES: readonly ReviewerOutcome[] = ["passed", "failed", "not_applicable"];

export const OVERRIDE_REASON_CODES: readonly OverrideReasonCode[] = [
  "evidence_missed",
  "evidence_misread",
  "transcript_error",
  "context_outside_call",
  "policy_exception",
  "other",
];

/**
 * The evidence turns of an override: comma-separated whole numbers of 0 or
 * more, 1 to 20 distinct values. The Persian digits and the Persian comma
 * `،` the fa hint itself shows are accepted (normalized before the check).
 * Null when the text is not acceptable; the caller shows which rule broke.
 */
export function parseEvidenceTurns(
  text: string,
): { values: number[] | null; error: "required" | "invalid" | "duplicate" | "too_many" | null } {
  const parts = toAsciiDigits(text)
    .split(/[,،]/)
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
  if (parts.length === 0) return { values: null, error: "required" };
  const numbers: number[] = [];
  for (const part of parts) {
    // Whole numbers written in ASCII digits only; the console never guesses.
    if (!/^\d+$/.test(part)) return { values: null, error: "invalid" };
    const value = Number(part);
    if (!Number.isSafeInteger(value)) return { values: null, error: "invalid" };
    if (numbers.includes(value)) return { values: null, error: "duplicate" };
    numbers.push(value);
  }
  if (numbers.length > 20) return { values: null, error: "too_many" };
  return { values: numbers.sort((a, b) => a - b), error: null };
}
