# ADR 0021 — Tombstone-first deletion and the durable conversation purge

Date: 2026-09-23 · Status: accepted · Package: WP07-T03

## Context

Before WP07-T03 a deleted conversation was a soft-deleted `meetings` row: every
read path already filtered `Meeting.deleted_at`, media objects went through the
ADR 0017 lifecycle and checkpoint objects were removed best-effort, but the
derived content (transcript revisions and segments, analysis runs, evidence,
summary, action items, speaker names, share recipients) stayed in the database
forever, nothing acted on a retention expiry (ADR 0020 only stamped it), and
failures of the best-effort steps were visible only in logs. Three races were
open: a hold committed after a deleter loaded its media rows could be
overwritten (`delete_objects` acted on the in-memory state); a hold placed while
bytes were being removed could be written over a `deleted` row; stage
publication read the meeting without a lock, so a tombstone committed between
that read and the publication commit did not stop it.

## Decision

1. **Two steps, never merged.** `tombstone_meeting` (one transaction) sets
   `deleted_at` on the meeting and recording, revokes share links, invalidates
   checkpoints, requests deletion of every media object (a held one records the
   request and keeps its bytes) and inserts a `conversation_purges` row. From
   that commit on every application-mediated path refuses the conversation.
   The purge (`run_purge_attempt`) then does the destructive work in bounded,
   retried attempts. Explicit deletion runs one attempt inline after the
   commit; the retention sweep drives the rest.
2. **Presigned URLs.** Access through the application is denied at once. A
   presigned URL issued before the tombstone stays valid until its own expiry
   (`STORAGE_PRESIGN_TTL_SECONDS`, at most the adapter maximum) or until the
   bytes are removed, whichever comes first; revocation is not instantaneous.
3. **What the purge removes and keeps.** Removed: the derived content rows
   listed above; the free-text meeting title and recording filename are
   emptied; media bytes and stage-output objects. Kept: the meeting and
   recording rows (tombstones), media and ingestion ledger rows (ids, digests,
   states; their filename columns are immutable and are a documented residual),
   usage events and reservations, provider usage, stage runs, audit events and
   the purge row, which becomes the non-content completion receipt (ids,
   counters, timestamps, error code only).
4. **Claiming without a second ownership mechanism.** An attempt locks the
   purge row with `FOR UPDATE SKIP LOCKED` and, in the same transaction, counts
   the attempt and moves `next_attempt_at` into the future; a concurrent sweeper
   skips a locked row and, after the commit, no longer finds it due. Bytes are
   removed row by row under the media row lock, so a duplicate effective
   removal cannot happen; stage-output removal is idempotent by key.
5. **Point of no return.** For content rows it is the purge commit. For bytes
   it is the locked re-read in `delete_objects`: the row must still be
   `deletion_pending` when locked; the lock is held across the storage call
   until the caller commits. A hold committed earlier wins (bytes stay); a hold
   arriving during the removal waits and is refused `already_deleted`. Hold
   routes lock the rows before deciding. A hold never restores removed data.
6. **Legal hold.** While any object of the conversation is held, the purge is
   deferred as a whole (content rows, stage outputs, held bytes) without
   spending an attempt (`last_error = legal_hold`, rechecked every
   `RETENTION_PURGE_HOLD_RECHECK_SECONDS`); the WP06 artifact sweep also skips
   stage outputs of a deleted, held conversation. Retention never tombstones a
   held conversation. A due review date is reported (WP07-T02) and never
   releases a hold. A release makes the deferred purge due at once.
7. **Failure semantics.** A storage error (denied, unavailable, failed) is
   never read as "gone": the row stays `deletion_pending`, the purge `PENDING`
   with `storage_pending` and a backoff of base × 2^(attempt−1) capped at the
   maximum. After `RETENTION_PURGE_MAX_ATTEMPTS` the purge is `FAILED`
   (terminal for the sweep), audited (`meeting.purge_failed`) and counted as
   `purges_failed_open` with a warning on every sweep until an operator calls
   `requeue_failed_purge`. A purge `PENDING` for more than 24 hours without a
   hold is counted as `purges_overdue` (F15 alert threshold). The WP05-T04
   reconciliation sweep keeps retrying `deletion_pending` rows independently.
8. **Retention eligibility.** The sweep tombstones a conversation only when,
   re-checked under the workspace (shared) and meeting (exclusive) row locks:
   the current policy is `finite` or `immediate` (`indefinite` and
   `legacy_unresolved` never act, ADR 0020); the stored expiry is at or before
   now; no job of the conversation is `queued` or `running`; no object is held.
   Terminal processing jobs (completed, failed, cancelled, dead-lettered) do not
   block and are neither mutated nor replayed; replay already refuses expired
   or deleted conversations under the meeting lock. A conversation that fails a
   check is looked at again next pass — nothing makes it permanently ineligible.
9. **Publication after a tombstone.** Stage publication (`_persist_success`,
   `publish_checkpoint`) locks the meeting row after the lease fence; a
   tombstone committed meanwhile is seen and nothing is published. Explicit
   deletion also cancels the running job (WP06 fencing).
10. **No backfill.** Migration `e6c2a9f4d1b8` does not create purge rows for
    conversations tombstoned earlier, so deploying it never removes existing
    customer content by itself; enqueueing them is an explicit operator
    decision.

## Consequences

- Deletion now completes across the database content and object stores or
  exposes a retryable (or FAILED, alerted) state per conversation.
- Frozen-package touch points, each a narrow fix for a concrete race: WP05
  `delete_objects` / `media_for_meeting` / `media_by_id` (row lock), WP06
  `processor._persist_success` and `checkpoints.publish_checkpoint` (meeting
  lock), WP06 `_cleanup_candidates` (skip held, deleted conversations).
- Restores can resurrect tombstoned data until WP07-T04 reapplies tombstones.
- Business defaults (mode, days) and the charging of empty transcripts remain
  owner decisions; tests use explicit fixture policies.
