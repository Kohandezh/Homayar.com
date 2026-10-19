/**
 * The ingestions page's filters: how they are read from and written to the
 * URL, how they become query parameters for GET /ingestions, and the parts
 * the console applies itself. The filters live in the URL so a filtered view
 * can be shared. The paging cursor does not: it is a raw timestamp tied to
 * the moment the pages were walked.
 *
 * The server has no status filter and no "after" bound, so `state` and
 * `from` hide rows of the loaded page, while `to` narrows the request
 * through the exclusive `before` cursor.
 */
import { parseAuditDay } from "@/lib/audit-filters";
import type { IngestionListParams } from "@/types/ingestions";

/** Rows per request. The API allows 1 to 100. */
export const INGESTION_PAGE_SIZE = 25;

/** The source types the backend writes (backend/routers/v1/ingestions.py). */
export const INGESTION_SOURCE_TYPES: readonly string[] = [
  "file_upload",
  "microphone",
  "system_audio",
  "mic_plus_system_audio",
  "sip_voip",
  "meeting_bot",
  "external_recording_url",
  "offline_folder_watcher",
  "api",
  "voicemail",
];

/** The states the page can filter by; the API knows no others. */
export const INGESTION_STATES: readonly string[] = ["accepted", "rejected"];

export interface IngestionFilters {
  /** One source type; empty means every source. */
  source: string;
  /** "accepted" or "rejected"; empty means both. */
  state: string;
  /** First day to show, as a Gregorian "YYYY-MM-DD" date; inclusive. */
  from: string;
  /** Last day to show, as a Gregorian "YYYY-MM-DD" date; inclusive. */
  to: string;
}

export const NO_INGESTION_FILTERS: IngestionFilters = {
  source: "",
  state: "",
  from: "",
  to: "",
};

const SOURCE_TYPES: ReadonlySet<string> = new Set(INGESTION_SOURCE_TYPES);
const STATES: ReadonlySet<string> = new Set(INGESTION_STATES);

/**
 * The audit page's day parser, under the ingestions name: the same Gregorian
 * "YYYY-MM-DD" contract, so a shared link means the same day for everybody.
 */
export const parseIngestionDay = parseAuditDay;

/** True for the source types this page offers; the filter bar asks the same question. */
export function isIngestionSource(value: unknown): value is string {
  return typeof value === "string" && SOURCE_TYPES.has(value);
}

/** The filters a URL holds; anything missing or unknown counts as "not filtered". */
export function readIngestionFilters(search: URLSearchParams): IngestionFilters {
  const source = search.get("source")?.trim() ?? "";
  const state = search.get("state")?.trim() ?? "";
  const day = (name: "from" | "to") => {
    const value = search.get(name)?.trim() ?? "";
    return parseIngestionDay(value) ? value : "";
  };
  return {
    source: isIngestionSource(source) ? source : "",
    state: STATES.has(state) ? state : "",
    from: day("from"),
    to: day("to"),
  };
}

/**
 * The filters as URL query parameters. Empty filters are removed instead of
 * written as blanks, and any other parameter in `base` is kept.
 */
export function writeIngestionFilters(
  filters: IngestionFilters,
  base?: URLSearchParams,
): URLSearchParams {
  const next = new URLSearchParams(base);
  const values: Record<keyof IngestionFilters, string> = {
    source: filters.source.trim(),
    state: filters.state.trim(),
    from: filters.from.trim(),
    to: filters.to.trim(),
  };
  for (const field of Object.keys(values) as Array<keyof IngestionFilters>) {
    if (values[field]) next.set(field, values[field]);
    else next.delete(field);
  }
  return next;
}

export function hasIngestionFilters(filters: IngestionFilters): boolean {
  return Object.values(filters).some((value) => value !== "");
}

/** True when the chosen start day is after the end day, so no request can be made. */
export function isFromAfterTo(filters: IngestionFilters): boolean {
  const from = parseIngestionDay(filters.from);
  const to = parseIngestionDay(filters.to);
  return from !== null && to !== null && from.compare(to) > 0;
}

export interface IngestionQueryOptions {
  /**
   * The page cursor: the `next_before` of the page before this one. Absent
   * for the first page, whose upper bound comes from the `to` day instead.
   */
  before?: string;
  /** IANA zone the chosen days are read in, e.g. "Asia/Tehran". */
  timeZone: string;
  limit?: number;
}

/**
 * The request for one page. `received_at < before` is exclusive and the last
 * day is included by asking for the midnight that follows it, in the
 * reader's own time zone; a cursor the server handed back is sent verbatim.
 */
export function ingestionQueryParams(
  filters: IngestionFilters,
  { before, timeZone, limit = INGESTION_PAGE_SIZE }: IngestionQueryOptions,
): IngestionListParams {
  const params: IngestionListParams = { limit };
  const cursor = before ?? beforeEndOfDay(filters.to, timeZone);
  if (cursor) params.before = cursor;
  if (filters.source) params.source_type = filters.source;
  return params;
}

function beforeEndOfDay(day: string, timeZone: string): string | undefined {
  const parsed = parseIngestionDay(day);
  return parsed ? parsed.add({ days: 1 }).toDate(timeZone).toISOString() : undefined;
}

/**
 * The rows of the loaded page the console itself keeps: `state` (the server
 * has no status filter) and `from` (the server has no "after" bound).
 */
export function visibleIngestionRows<T extends { status: string; received_at: string }>(
  items: T[],
  filters: IngestionFilters,
  timeZone: string,
): T[] {
  const fromStart = dayStart(filters.from, timeZone);
  return items.filter((item) => {
    if (filters.state && item.status !== filters.state) return false;
    if (fromStart !== null && Date.parse(item.received_at) < fromStart) return false;
    return true;
  });
}

/**
 * True once the oldest row of the loaded page is older than the start of the
 * `from` day: the list is newest first, so every later page would only load
 * rows the console hides again, and Next is withdrawn.
 */
export function nextBlockedByFrom(
  items: Array<{ received_at: string }>,
  filters: IngestionFilters,
  timeZone: string,
): boolean {
  const fromStart = dayStart(filters.from, timeZone);
  if (fromStart === null || items.length === 0) return false;
  return items.some((item) => Date.parse(item.received_at) < fromStart);
}

/** The start of a chosen day as a epoch value, or null without a chosen day. */
function dayStart(day: string, timeZone: string): number | null {
  const parsed = parseIngestionDay(day);
  return parsed ? parsed.toDate(timeZone).getTime() : null;
}
