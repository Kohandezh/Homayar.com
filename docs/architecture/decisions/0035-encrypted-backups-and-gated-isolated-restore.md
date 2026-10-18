# ADR 0035 — Encrypted, snapshot-consistent backups and a gated isolated restore

Date: 2026-10-17 · Status: proposed (Lane C, HOM-153/116/117/118) · Packages: WP20 (F79), N12 · Builds on: ADR 0017, ADR 0021, ADR 0022, ADR 0023

## Context

The repository had `pg_dump`/`mc mirror` scripts (plaintext dumps, a database dump and an object
mirror taken at different moments, no shared manifest) and an in-place restore followed by
`reapply-tombstones.sh`. HOM-153 asks for versioned objectives, a scope manifest without
credentials, encrypted metadata and drill evidence; HOM-116 for a consistent backup of data,
object manifests, configuration references and deletion state; HOM-117 for a restore into an
isolated environment that nobody can use before tombstones are re-applied and tenant boundaries,
RLS and integrity are verified. No schema change was allowed in this wave (Lane D holds the
migration slot), and CI must run the tests without new tooling.

## Decision

1. **Logical, snapshot-consistent dump through psycopg.** One `REPEATABLE READ READ ONLY`
   transaction with `ACCESS SHARE` locks on every `public` table exports the snapshot, reads the
   catalog (columns, row counts, per-tenant counts, Alembic revision, sequences, tenants, object
   references and expectations, PII key ids in use) and `COPY`s every table. Catalog and data are
   therefore the same instant. No `pg_dump`: the API image, CI and an operator host need no
   version-matched client binaries, and RLS is handled explicitly (`row_security = off`, a
   superuser/`BYPASSRLS` role is required).
2. **Schema from migrations, data from the set.** A restore creates an empty database, runs
   `alembic upgrade <backup revision>`, truncates seeded rows, loads every table in one
   transaction with triggers disabled (self-references arrive in storage order), then checks row
   counts, per-tenant counts and every foreign key with anti-joins before committing, and finally
   migrates to the code's head and re-applies the RLS grants and policies. The restored schema is
   always the migration-defined one; a backup newer than the code is refused.
3. **Objects through the application storage interface.** Copies list and read only keys that
   pass tenant-prefix validation, under the snapshot's tenants, after the snapshot; restores write
   with `object_token` and the expected SHA-256, which makes a resumed restore idempotent and a
   conflicting key fail. Expectations from the snapshot (`required`/`optional`/`gone`) drive the
   reconciliation.
4. **Encryption.** Every file is AES-256-GCM in 1 MiB chunks (STREAM nonces with counter and
   last-chunk flag), key per file via HKDF-SHA256 from the operator's backup key, the backup id
   and the file name. The manifest is plaintext, lists every file's size and SHA-256 (verifiable
   off-site without the key) and is sealed by an HMAC under the key. Secrets are never in a set;
   `TOKEN_PEPPER` and PII keys are pinned by keyed fingerprints.
5. **Atomic sets.** Work happens in `sets/.partial-<id>` under an `flock`; the rename to
   `sets/<id>` after fsync is the completion marker. Partials are never listed, verified as
   complete or restored, and `prune` removes stale ones.
6. **The restore gate is the database name.** The application only connects to `POSTGRES_DB`.
   A restore writes into `<db>_restoring`, marked with `COMMENT ON DATABASE` (`restoring` →
   `loaded` → `reconciled` → `promoted`). `promote` renames it only with a reconciliation report
   that is PASS, sealed with the backup key and identical (SHA-256) to the one recorded on the
   database. No application change and no migration were needed.
7. **Reconciliation before promotion.** Tombstone-journal replay (ADR 0022), then schema head,
   RLS structure, RLS behaviour as `homayar_app` in a rolled-back transaction, tenant links and
   key prefixes, non-resurrection of every journaled conversation, required objects and orphans,
   secret fingerprints, post-backup revocation counts. Held conversations and missing objects
   need an explicit, recorded owner decision.
8. **Freshness metric.** Successful steps rewrite a node_exporter textfile
   (`homayar_backup_last_success_timestamp_seconds{target}`) atomically; Lane D owns the alert.

9. **Positive evidence for the deletion journal.** The backup fingerprints every tombstone
   journal file (name, size, SHA-256); the files are append-only, so reconcile requires the
   replica to hold each with the same leading bytes (else stale or foreign: FAIL, not
   acceptable). A backup taken before the first deletion only accepts a replica whose records
   all belong to its tenants. An absent or empty journal never passes on its own; the single
   override, `--accept-no-journal REF`, is recorded in the sealed report.
10. **Reconcile is bound to what restore verified.** Restore records the manifest MAC in its
    state and in the staging marker; reconcile re-checks the MAC and requires the same one.
    Restore, reconcile and promote hold a PostgreSQL advisory lock per staging database, marker
    changes are compare-and-set, and promote accepts only the latest report of the database.
    Staging names must contain `restoring`; the live and source names are refused by name.
11. **Content, not just presence.** Reconcile re-hashes every required object in the target
    store against its restored row. The backup explains an object missing at copy time only by
    the row that set its expectation, deleted or marked for deletion after the snapshot; a
    legally held object is never explained (neither at backup nor at reconcile). Unexplained
    loss or mismatching bytes mark the set's object copy `degraded`
    (kept, but the `objects` gauge does not move and the scheduled run fails).
12. **Gauges follow verification.** Only a deep `verify` moves `postgres`/`objects` and only a
    verified off-site copy moves `offsite`; values never move backwards. A directory counts as
    off-site only with the operator's `.homayar-offsite` marker on the remote medium: operator
    attestation, since the tool cannot see the medium (`backup.sh` checks the host paths).
13. **Retention prefers full verified sets** per bucket, and the 12 monthly sets lie beyond the
    30-day window; manifest-less S3 copies are swept after 24 h.
14. **Retired databases carry an operator-enforced deadline.** Promote renames the live-name
    database it replaces and marks it `homayar-retired` with a 7-day `drop_after` in the rename
    transaction, then drops it only if it holds no relation (judged on the retired database).
    One with data stays until the operator runs `drop-retired`; `retired` flags it overdue, and
    nothing drops it or alerts. `drop-retired` trusts the name and the live configuration, not
    the comment: it refuses the live, a `restoring` and an in-use database. Promotes onto one
    live name hold an advisory lock on that name.

## Consequences

- Restores are slower than a physical restore of large databases (row-by-row `COPY` plus index
  builds by the migrations); the drill measures it, and a deployment that outgrows the RTO adds
  physical/PITR backups (pgBackRest) next to this format, which stays the portable, verified one.
- A hand-edited production schema (tables or columns the migrations do not create) makes a set
  unrestorable until the drift is fixed; the restore names the table.
- Post-backup revocations, password changes and hold releases are not replayed; they are reported
  and covered by `JWT_SECRET` rotation, `--revoke-share-links` and owner decisions.
- Losing the backup key loses the sets; the key needs an escrow outside every backup location.
- The legacy scripts remain only for reading old sets.
- The application host can still delete off-site copies; immutability (object lock,
  off-site-side expiry) is **open owner decision AppVision D15** (pending).
- Deletions newer than the journal replica's last sync are lost on a restore; the journal
  replication interval needs its own alert.
