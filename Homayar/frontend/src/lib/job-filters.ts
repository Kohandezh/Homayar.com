/**
 * The processing page's filters: how they are read from and written to the
 * URL, and how they become query parameters for GET /admin/jobs
 * (backend/routers/v1/admin.py list_jobs).
 *
 * The filters live in the URL so a filtered view can be shared. The cursor
 * does not: it is opaque, it expires, and a shared page-three link would only
 * answer `invalid_cursor`.
 */
import type { AdminJobListParams, JobState } from "@/types/platform";

/** Rows per request. The API allows 1 to 100. */
export const JOB_PAGE_SIZE = 25;

/**
 * The states the page can filter by. The API knows `completed` and
 * `cancelled` too, but this page is about what still needs attention, so they
 * are not offered, and they are not accepted from the URL either: an active
 * filter the select cannot show would shorten the list for no visible reason.
 * Without a state filter the list shows every state.
 */
export const JOB_STATE_FILTERS: readonly JobState[] = [
  "failed",
  "dead_letter",
  "running",
  "queued",
];

/**
 * The stage values the backend writes on a job (backend/jobs: processor.py,
 * failures.py, leases.py, recovery.py, replay.py). A job stage is a free
 * string, so an unlisted one is still accepted from the URL.
 */
export const JOB_STAGES: readonly string[] = [
  "queued",
  "processing",
  "transcribing",
  "diarizing",
  "analyzing",
  "retry_scheduled",
  "replay_scheduled",
  "recovery_scheduled",
  "completed",
  "partial",
  "failed",
  "dead_letter",
  "cancelled",
];

export interface JobFilters {
  /** Chosen states; empty means every state. */
  states: JobState[];
  stage: string;
}

export const NO_JOB_FILTERS: JobFilters = { states: [], stage: "" };

const FILTERABLE_STATES: ReadonlySet<string> = new Set<string>(JOB_STATE_FILTERS);

/** The longest stage the API accepts (`max_length=40`). */
const MAX_STAGE_LENGTH = 40;

/** True for the states this page offers; the filter bar asks the same question. */
export function isJobState(value: unknown): value is JobState {
  return typeof value === "string" && FILTERABLE_STATES.has(value);
}

/**
 * The filters a URL holds; anything missing or unknown counts as "not
 * filtered". A state repeats in the URL the same way it repeats in the
 * request (`?state=failed&state=dead_letter`), and duplicates are dropped so
 * the query key stays stable.
 */
export function readJobFilters(search: URLSearchParams): JobFilters {
  const states = search.getAll("state").map((value) => value.trim()).filter(isJobState);
  const stage = search.get("stage")?.trim() ?? "";
  return {
    states: [...new Set(states)],
    stage: stage.length > MAX_STAGE_LENGTH ? "" : stage,
  };
}

/**
 * The filters as URL query parameters. Empty filters are removed instead of
 * written as blanks, and any other parameter in `base` is kept.
 */
export function writeJobFilters(filters: JobFilters, base?: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(base);
  next.delete("state");
  for (const state of filters.states) next.append("state", state);
  if (filters.stage.trim()) next.set("stage", filters.stage.trim());
  else next.delete("stage");
  return next;
}

export function hasJobFilters(filters: JobFilters): boolean {
  return filters.states.length > 0 || filters.stage !== "";
}

export interface JobQueryOptions {
  /** The opaque cursor of the page to load; absent for the first page. */
  cursor?: string;
  limit?: number;
}

/** The request for one page. */
export function jobQueryParams(
  filters: JobFilters,
  { cursor, limit = JOB_PAGE_SIZE }: JobQueryOptions = {},
): AdminJobListParams {
  const params: AdminJobListParams = { limit };
  if (cursor) params.cursor = cursor;
  if (filters.states.length) params.state = [...filters.states];
  if (filters.stage) params.stage = filters.stage;
  return params;
}
