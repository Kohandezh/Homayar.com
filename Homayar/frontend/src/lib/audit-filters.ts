/**
 * The audit log's filters: how they are read from and written to the URL, and
 * how they become query parameters for GET /admin/audit-events
 * (backend/routers/v1/admin.py list_audit_events).
 *
 * The filters live in the URL so a filtered view can be shared. The cursor
 * does not: it is opaque, it expires, and a shared page-three link would only
 * answer `invalid_cursor`.
 */
import { parseDate, type CalendarDate } from "@internationalized/date";
import type { AuditEventListParams } from "@/types/platform";

/** Rows per request. The API allows 1 to 100. */
export const AUDIT_PAGE_SIZE = 25;

export interface AuditFilters {
  eventType: string;
  outcome: string;
  actorUserId: string;
  targetType: string;
  targetId: string;
  /** First day to show, as a Gregorian "YYYY-MM-DD" date; inclusive. */
  from: string;
  /** Last day to show, as a Gregorian "YYYY-MM-DD" date; inclusive. */
  to: string;
}

export const NO_AUDIT_FILTERS: AuditFilters = {
  eventType: "",
  outcome: "",
  actorUserId: "",
  targetType: "",
  targetId: "",
  from: "",
  to: "",
};

/** URL names, kept close to the API names so a shared link reads like the request. */
const QUERY_KEYS: Record<keyof AuditFilters, string> = {
  eventType: "event_type",
  outcome: "outcome",
  actorUserId: "actor",
  targetType: "target_type",
  targetId: "target_id",
  from: "from",
  to: "to",
};

const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/**
 * A "YYYY-MM-DD" day as a calendar date, or null when the text is not one.
 * The date is always Gregorian, even while the interface shows the Persian
 * calendar, so a shared link means the same day for everybody.
 */
export function parseAuditDay(value: string): CalendarDate | null {
  if (!DAY_PATTERN.test(value)) return null;
  try {
    return parseDate(value);
  } catch {
    return null;
  }
}

/** The filters a URL holds; anything missing or malformed counts as "not filtered". */
export function readAuditFilters(search: URLSearchParams): AuditFilters {
  const text = (field: keyof AuditFilters) => search.get(QUERY_KEYS[field])?.trim() ?? "";
  const day = (field: "from" | "to") => (parseAuditDay(text(field)) ? text(field) : "");
  return {
    eventType: text("eventType"),
    outcome: text("outcome"),
    actorUserId: text("actorUserId"),
    targetType: text("targetType"),
    targetId: text("targetId"),
    from: day("from"),
    to: day("to"),
  };
}

/**
 * The filters as URL query parameters. Empty filters are removed instead of
 * written as blanks, and any other parameter in `base` is kept.
 */
export function writeAuditFilters(filters: AuditFilters, base?: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(base);
  for (const field of Object.keys(QUERY_KEYS) as Array<keyof AuditFilters>) {
    const value = filters[field].trim();
    if (value) next.set(QUERY_KEYS[field], value);
    else next.delete(QUERY_KEYS[field]);
  }
  return next;
}

export function hasAuditFilters(filters: AuditFilters): boolean {
  return Object.values(filters).some((value) => value !== "");
}

export interface AuditQueryOptions {
  /** The opaque cursor of the page to load; absent for the first page. */
  cursor?: string;
  /** IANA zone the chosen days are read in, e.g. "Asia/Tehran". */
  timeZone: string;
  limit?: number;
}

/**
 * The request for one page. A chosen day counts in the reader's own time zone,
 * and `occurred_before` is exclusive (`<`), so the last day is included by
 * asking for the midnight that follows it.
 */
export function auditQueryParams(
  filters: AuditFilters,
  { cursor, timeZone, limit = AUDIT_PAGE_SIZE }: AuditQueryOptions,
): AuditEventListParams {
  const params: AuditEventListParams = { limit };
  if (cursor) params.cursor = cursor;
  if (filters.eventType) params.event_type = filters.eventType;
  if (filters.outcome) params.outcome = filters.outcome;
  if (filters.actorUserId) params.actor_user_id = filters.actorUserId;
  if (filters.targetType) params.target_type = filters.targetType;
  if (filters.targetId) params.target_id = filters.targetId;
  const from = parseAuditDay(filters.from);
  if (from) params.occurred_after = from.toDate(timeZone).toISOString();
  const to = parseAuditDay(filters.to);
  if (to) params.occurred_before = to.add({ days: 1 }).toDate(timeZone).toISOString();
  return params;
}
