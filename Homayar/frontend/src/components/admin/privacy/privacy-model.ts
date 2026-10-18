/** Rules the privacy forms and the register share (HOM-190). */

import { formatDay } from "@/lib/format";

export const REASON_MIN_LENGTH = 3;
export const REASON_MAX_LENGTH = 200;

/** A reason the server accepts: 3 to 200 characters once trimmed. */
export function isValidReason(reason: string): boolean {
  const length = reason.trim().length;
  return length >= REASON_MIN_LENGTH && length <= REASON_MAX_LENGTH;
}

/** A hold whose review date has come. */
export function isReviewDue(reviewAt: string, now: Date = new Date()): boolean {
  return new Date(reviewAt).getTime() <= now.getTime();
}

/**
 * A review date as a day. The day is chosen and sent as 00:00 UTC, so it is
 * read back as the UTC day: shown in the browser's zone, it would read one
 * day early west of UTC. Local noon of that day goes through the shared
 * `formatDay`, which then prints the same day in every zone.
 */
export function formatReviewDay(value: string, lang: string): string {
  const utcDay = new Date(value).toISOString().slice(0, 10);
  return formatDay(`${utcDay}T12:00:00`, lang);
}

/** E-mail identifiers are compared without case; other kinds as typed. */
export function identifierKey(value: string): string {
  return /^[^\s@]+@[^\s@]+$/.test(value) ? value.toLowerCase() : value;
}
