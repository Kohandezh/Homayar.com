# ADR 0022 — Tombstone journal outside the backups and reapply after restore

Date: 2026-09-23 · Status: accepted · Package: WP07-T04

## Context

ADR 0021 made deletion tombstone-first and durable, but both authoritative stores
are backed up as point-in-time copies (`pg_dump` sets and MinIO mirrors). Restoring
a set taken before a deletion brings the conversation back: alive in PostgreSQL
(content rows, live media rows, valid share links, no purge row) and with its bytes
in MinIO. A restore taken after the tombstone but before the purge finished, or a
MinIO restore older than the database, brings back content or bytes of an already
closed purge. The purge rows themselves live in the restored database, so they
cannot be the record of what must be deleted again. Backups cannot be edited
either; promising instant erasure from them would be false.

## Decision

1. **A journal outside both backups.** After its commit every tombstone is
   appended to `TOMBSTONE_JOURNAL_DIR`: per-UTC-day JSON-lines files, mode 0600,
   written with `O_APPEND` and `fsync`, one line per tombstone with `v`,
   `purge_id`, `workspace_id`, `meeting_id`, `reason`, `tombstoned_at`,
   `object_keys` and a SHA-256 of the canonical payload. No titles, filenames or
   text; held objects are omitted from `object_keys`. Deployments bind the
   directory to storage outside the PostgreSQL and MinIO failure domains,
   replicate it continuously and keep it at least as long as the oldest backup.
2. **Outbox marker, never blocking.** `conversation_purges.journaled_at` is set
   only after the append succeeded. The delete route journals inline; the
   retention sweep writes any backlog before it purges and reports
   `journal_backlog`. A journal failure is logged and never blocks deletion; the
   API warns at startup when staging/production runs without a journal.
3. **Reapply before traffic.** `python -m backend.jobs.tombstone_reapply reapply`
   (Compose service `tombstone-reapply`, script `infra/scripts/reapply-tombstones.sh`,
   writers stopped) reads the journal and, per record in its tenant-bound session:
   re-tombstones a conversation that is alive again (queued/running jobs
   cancelled, links revoked, media deletion requested, audit
   `meeting.tombstone_reapplied`); ensures a purge row and reopens it
   (`PENDING`, fresh budget, `content_purged_at` cleared, `reapplied_at` set);
   runs one attempt. With `reapplied_at` set, an attempt also removes bytes of
   rows already `deleted` and stage outputs already marked removed, because an
   object-store restore may have brought them back (idempotent; storage errors
   counted, never read as gone).
4. **Verify gates the restart.** `verify` exits non-zero while any journaled
   conversation is alive in the database or any journal line fails its
   checksum. The runbook allows `api`/`worker` to start only after it succeeds;
   purges still open are completed by the sweep and visible in its counts.
5. **Unknown conversations.** A record whose conversation is absent from the
   restored database (the backup predates it) changes nothing in the database;
   its journaled keys are removed only with `--remove-unknown-keys`, and never a
   key still referenced by a live media row or checkpoint.
6. **Backups expire, they are not erased.** Deleted data stays inside existing
   backup sets until they expire under the deployment's backup retention (an
   owner decision recorded outside this repository). Every restore of any set
   runs the reapply step.

## Consequences

- Restores no longer resurrect deletions that were journaled; the guarantee is
  bounded by the journal: tombstones written while no journal was configured,
  or lost with a journal copy older than the tombstone, are not reapplied.
- Holds and hold releases made after the backup are not journaled; after a
  restore the hold state is the backup's (reapply respects holds present in the
  restored database). The runbook requires an owner review of holds.
- Reapply is idempotent and safe to repeat; it reopens purges it has seen, which
  costs one extra purge attempt per journaled conversation.
- The drill is automated against a real SQLite file backup/restore and a
  simulated PostgreSQL restore; a `pg_dump`/`pg_restore` drill against the
  Compose stack is still an operator exercise (WP20 restore rehearsal).
