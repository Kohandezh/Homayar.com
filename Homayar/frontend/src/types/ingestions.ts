/**
 * The ingestion ledger rows the console renders (GET /api/v1/ingestions,
 * backend/routers/v1/ingestions.py). Only the fields the page shows are
 * declared: the record also carries hashes and internal ids the console must
 * never render, and leaving them off the type keeps any component from
 * reading them.
 */
export interface IngestionRecord {
  id: string;
  source_type: string;
  /** Adapter name, a free string. */
  source_provider: string;
  /** Client-sent event identity; for uploads this is the Idempotency-Key. */
  external_event_id: string;
  media_size_bytes: number | null;
  declared_content_type: string | null;
  /** User content. */
  original_filename: string | null;
  /** "accepted" or "rejected"; a free string, so an unknown value is still shown as sent. */
  status: string;
  /** A media refusal code, set only on rejected rows. */
  reason: string | null;
  received_at: string;
  created_at: string;
  /** Set when a meeting was created from this ingestion. */
  meeting_id: string | null;
  /** Set when an earlier ingestion already carried the same media. */
  duplicate_of_id: string | null;
  /** Replays of the same event identity with different content. */
  conflict_count: number;
  last_conflict_at: string | null;
}

/** Query parameters of GET /ingestions; all but `limit` are optional. */
export interface IngestionListParams {
  limit: number;
  /** Exclusive upper bound on `received_at`; the paging cursor. */
  before?: string;
  source_type?: string;
}

export interface IngestionListResponse {
  items: IngestionRecord[];
  /** The `received_at` of the last item when the page was full, else null. */
  next_before: string | null;
}
