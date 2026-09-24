# ADR 0017 — Original and derived media objects with a private lifecycle

Date: 2026-09-11 · Status: accepted · Package: WP05-T03

## Context

Until now a conversation's bytes were represented by one `recordings` row
(location, digest, size, type, duration) with a single `deleted_at`, and
three code paths removed objects directly: meeting deletion, the
delete-after-processing workspace policy and the stage-checkpoint sweep. The
bytes were deleted *before* the row said so, a failed removal left the
recording available, nothing could hold bytes for a legal reason, nothing
recorded who produced an object from what, and there was no place to put the
normalised, enhanced, per-channel, clipped or exported media that WP09,
WP14 and WP16 will create. The master prompt (§20 T03, §21) requires the
original to be preserved, derived artifacts to be separate, and every object
to carry hash, size, type, duration, location, tenant, provenance, retention
state, deletion state and legal hold.

## Decision

1. **One row per byte stream.** `media_objects` holds every stored object of
   a conversation: `kind` is `original` (at most one per conversation —
   partial unique index) or a derived kind (`normalized`, `enhanced`,
   `channel_derived`, `clip`, `export`) that must name the object it was made
   from (`derived_from_id`, check constraint). Each row carries the storage
   backend and key, content type, container, size, SHA-256, duration,
   channels, sample rate, original file name, `producer` (the adapter or
   tool that wrote it), the ingestion row and job it belongs to, and the
   organization, workspace and conversation.
2. **Content is immutable; lifecycle is not.** An ORM guard refuses changes
   to everything except `lifecycle_state`, `retention_expires_at`,
   `deletion_requested_at`, `deletion_reason`, `deleted_at`, the legal-hold
   columns, `integrity` and `last_verified_at`. Rows are never removed; the
   storage layer never overwrites a published object (WP05-T01 storage
   contract). The `recordings` row stays as the conversation's primary
   recording view and now points at its ORIGINAL (`media_object_id`).
3. **Lifecycle.** `active` → (`retention_pending`, entered by the retention
   sweep of WP05-T06/WP07) → `deletion_pending` → `deleted`; `legal_hold`
   overrides deletion. Deletion is *state first, bytes second*: a request
   marks the row pending (or, on a held row, only records the request and
   keeps the bytes), the caller commits, then removes the object and closes
   the row with `deleted_at`. A failed removal leaves the row pending for a
   sweep; a repeated request is idempotent. Releasing a hold with a recorded
   request makes the object pending again.
4. **One path for every deletion, two visibility rules.** Meeting deletion
   and the delete-after-processing policy both go through
   `retire_meeting_media` / `request_deletion` / `delete_objects` /
   `mark_deleted`. On meeting deletion the conversation is gone, so the
   recording is unavailable at once whatever happens to the bytes. Under the
   delete-after-processing policy the conversation stays, and the WP06
   invariant holds: the recording is marked deleted only once the bytes are
   actually gone — a failed removal leaves the recording available and the
   media row `deletion_pending`; a legal hold keeps both and is audited as
   `meeting.recording.retained_after_processing`. Closing an ORIGINAL
   (`mark_deleted`) retires its recording whichever path removed the bytes,
   so a request resumed by a released hold and closed by a sweep never
   leaves a recording that claims bytes which are gone. Recordings that
   predate the media ledger and were not backfilled fall back to the direct
   removal.
5. **Integrity.** `verify_integrity` re-reads the bytes through the storage
   abstraction and records `verified`, `corrupt` (digest or size differ) or
   `missing`, audited; exposed as `POST /meetings/{id}/media/{media_id}/verify`
   (`meeting:update` + CSRF).
6. **Reads.** `GET /meetings/{id}/media` lists the conversation's objects
   under `meeting:read` and record scope; responses never carry the storage
   key (access to bytes goes through the recording download route only).
   `media_objects` joins the tenant-only RLS policy set.
7. **Backfill.** Migration `b9d5f3a7c2e8` creates one ORIGINAL row per
   existing recording (`producer = backfill`, linked to the conversation's
   ingestion row and earliest job, `deleted` when the recording was already
   soft-deleted) and links the recording to it; the backend recorded is the
   one configured at migration time. The downgrade drops the column and the
   table (lossy: lifecycle, holds and integrity results).

## Consequences

- WP09 (normalisation, enhancement, channel separation), WP14 (clips) and
  WP16 (stored exports) register their outputs with `register_derived`,
  inheriting tenant, conversation, ingestion and retention from the source;
  a derived object cannot be registered from a non-live source.
- WP05-T06 / WP07 add the retention sweep (`retention_pending`), the retry
  sweep for `deletion_pending` rows, and the legal-hold routes with their
  permission; the service functions and audit events already exist.
- WP05-T05 formalises the provider contract (`conditional_put`, `metadata`,
  `integrity_check`, `signed_read_url`) that `verify_integrity` and the
  registration paths call through today's `ObjectStorage` methods.
- Stage-checkpoint artifacts (JSON stage results, WP06-T03) stay on their own
  table: they are attempt-scoped processing state, not media.
