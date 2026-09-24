# ADR 0018 — Retry-safe finalize/abort and reconciliation of objects and rows

Date: 2026-09-11 · Status: accepted · Package: WP05-T04

## Context

An upload stores its object before the database transaction that records it
commits, and a deletion commits its state before the bytes are removed
(ADR 0017: state first, bytes second). Every one of those two-step paths has
a window in which the object store and the database disagree: an object
without a row (the request died between `put_stream` and the commit, or the
route's abort could not remove the object), a `deletion_pending` row whose
removal failed, a live row whose bytes are gone, and backend-private
leftovers of writes that never completed (the local backend's temporaries).
The stage-checkpoint sweep, the usage reconciliation and the outbox sweeper
(WP06) already reconcile their own domains; media objects had no backstop,
and the storage contract had no way to enumerate what a tenant actually
holds. F14 requires that partial failures reconcile without accessible
orphan objects and F13 that repeating one finalization a hundred times
creates one conversation and one usage effect.

## Decision

1. **The request path stays as it is; its guarantees are proven, not
   redesigned.** One accepted ingestion yields at most one conversation, one
   root job and one reservation chain because the ledger identity
   (`uq_ingestion_event`), the job idempotency key and the reservation
   idempotency keys are unique indexes inside one transaction; a commit
   failure after the object was stored rolls every row back, the route's
   abort path removes the object, and the identity is free for the retry.
   Tests now prove the abort, the retry and a hundredfold replay (one
   conversation, one job, one media row, one reservation per metric).
2. **The storage contract can enumerate a tenant.** `ObjectStorage` gains
   `iter_objects(tenant_id)` (key, size, last-modified, completeness; only
   keys under the tenant's own prefix that pass the ownership check) and
   `purge_incomplete(tenant_id, older_than)` (backend-private leftovers with
   no valid key; the local backend removes its temporaries, S3 has none
   because uploads are single-request `put_object` calls — there are no
   multipart uploads to abort). Both join the WP05-T05 provider contract.
3. **One periodic reconciliation sweep** (`backend/jobs/media_reconciliation`,
   Celery beat, operator entrypoint) per workspace under the tenant context,
   bounded per pass: (a) `deletion_pending` rows past the grace period are
   retried through the existing `delete_objects` path (closing an ORIGINAL
   retires its recording); (b) objects under the tenant prefix without a
   live row (media rows not `deleted`, checkpoint artifacts not yet removed,
   pre-ledger recordings) and older than the grace period are **reported**
   by default (one audited count per workspace) and **removed** only when
   `MEDIA_RECONCILIATION_REMOVE_ORPHANS` is enabled — the same
   report-then-repair stance as the usage reconciliation; (c) live rows
   whose key is absent from the listing are re-verified through
   `verify_integrity` and flagged `missing`, never fabricated or removed;
   (d) incomplete leftovers are purged. A listing failure skips the orphan
   and missing steps for that pass (the pending retry, already committed,
   stands); a backend read failure while re-verifying one row skips only
   that row and counts it as unavailable. Local filesystem errors are
   wrapped as `StorageError` (`local_list_failed`, `local_purge_failed`)
   so the sweep never dies on one tenant. Audit details carry counts, sizes
   and ages, never keys.
4. **Grace before judgement.** Nothing younger than the grace period
   (default one hour) is treated as an orphan or as missing: an object may
   precede its row by the length of one request, and a listing may lag a
   write on eventually consistent stores.

## Consequences

- Operators enable orphan removal deliberately; until then the audit trail
  shows what would be removed. The empty-transcript billing question is
  untouched: reconciliation never creates or settles usage.
- WP05-T06 / WP07 add the retention sweep that moves rows to
  `retention_pending` and then `deletion_pending`; this sweep already drains
  the latter. WP05-T05 qualifies `iter_objects` against a real S3-compatible
  bucket (BLOCKED_EXTERNAL until one exists); the fake-client tests cover
  pagination and prefix scoping only.
- Listing is one prefix walk per workspace per pass; very large tenants may
  need an inventory-based listing later (not designed here).
