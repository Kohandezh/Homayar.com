/**
 * The voicemail inbox's filters: how they are read from and written to the
 * URL, and how they become query parameters for GET /voicemails
 * (backend/routers/v1/voicemail.py).
 *
 * The filters live in the URL so a filtered view can be shared and survives a
 * reload. The cursor does not: it is a `received_at` value, it expires as new
 * voicemails arrive, and a shared page-three link would silently skip rows.
 */
import type {
  VoicemailCategory,
  VoicemailListParams,
  VoicemailPriority,
} from "@/types/voicemail";

/** Rows per request. The API allows 1 to 100. */
export const VOICEMAIL_PAGE_SIZE = 25;

export const VOICEMAIL_PRIORITIES: readonly VoicemailPriority[] = [
  "urgent",
  "high",
  "normal",
  "low",
];

export const VOICEMAIL_CATEGORIES: readonly VoicemailCategory[] = [
  "complaint",
  "sales_inquiry",
  "support",
  "appointment",
  "callback_request",
  "billing",
  "spam",
  "empty",
  "other",
];

export interface VoicemailFilters {
  /** Chosen priority; null means every priority. */
  priority: VoicemailPriority | null;
  category: VoicemailCategory | null;
}

export const NO_VOICEMAIL_FILTERS: VoicemailFilters = { priority: null, category: null };

const FILTERABLE_PRIORITIES: ReadonlySet<string> = new Set<string>(VOICEMAIL_PRIORITIES);
const FILTERABLE_CATEGORIES: ReadonlySet<string> = new Set<string>(VOICEMAIL_CATEGORIES);

/**
 * True for the priorities the page offers. An unknown value from a shared URL
 * counts as "not filtered": the select cannot show it, so the list must not be
 * shortened by a filter the reader cannot see.
 */
export function isVoicemailPriority(value: unknown): value is VoicemailPriority {
  return typeof value === "string" && FILTERABLE_PRIORITIES.has(value);
}

export function isVoicemailCategory(value: unknown): value is VoicemailCategory {
  return typeof value === "string" && FILTERABLE_CATEGORIES.has(value);
}

/** The filters a URL holds; anything missing or unknown counts as "not filtered". */
export function readVoicemailFilters(search: URLSearchParams): VoicemailFilters {
  const priority = search.get("priority")?.trim() ?? "";
  const category = search.get("category")?.trim() ?? "";
  return {
    priority: isVoicemailPriority(priority) ? priority : null,
    category: isVoicemailCategory(category) ? category : null,
  };
}

/**
 * The filters as URL query parameters. Empty filters are removed instead of
 * written as blanks, and any other parameter in `base` (such as the open
 * drawer's `voicemail` id) is kept.
 */
export function writeVoicemailFilters(
  filters: VoicemailFilters,
  base?: URLSearchParams,
): URLSearchParams {
  const next = new URLSearchParams(base);
  next.delete("priority");
  next.delete("category");
  if (filters.priority) next.set("priority", filters.priority);
  if (filters.category) next.set("category", filters.category);
  return next;
}

export function hasVoicemailFilters(filters: VoicemailFilters): boolean {
  return filters.priority !== null || filters.category !== null;
}

export interface VoicemailQueryOptions {
  /** The `received_at` cursor of the page to load; absent for the first page. */
  before?: string;
  limit?: number;
}

/** The request for one page. */
export function voicemailQueryParams(
  filters: VoicemailFilters,
  { before, limit = VOICEMAIL_PAGE_SIZE }: VoicemailQueryOptions = {},
): VoicemailListParams {
  const params: VoicemailListParams = { limit };
  if (before) params.before = before;
  if (filters.priority) params.priority = filters.priority;
  if (filters.category) params.category = filters.category;
  return params;
}
