# Backup and Restore Runbook

Objectives, scope and limits: [`disaster-recovery-objectives.md`](disaster-recovery-objectives.md)
(provisional owner defaults: RPO 24 h, RTO 8 h, 30 daily + 12 monthly, one encrypted copy off
the application host). Design: ADR 0035. Drill records: [`drills/`](drills/).

PostgreSQL is the transactional source of truth; MinIO holds recordings, derived media and stage
artifacts. Redis is ephemeral and Neo4j is a rebuildable projection; neither is backed up. The
tombstone journal is deliberately **not** in the backups: it is replicated separately and replayed
on every restore.

## 1. One-time setup

1. **Backup key.** Generate it once per key period and store it in the secret manager plus an
   escrow that is not on the application host and not next to any backup copy:

   ```bash
   docker compose --file infra/compose/compose.onprem.yaml --profile onprem --profile ops \
     run --rm --no-deps backup-tool keygen site-a-2026
   ```

   Write the printed line to a root-owned mode-0600 file on the host (`BACKUP_KEY_FILE`). Losing
   the key loses every set made with it; rotation = new key id for new sets, keep old keys until
   their sets expire.
2. **Backup root** (`BACKUP_ROOT`): encrypted storage on a different disk / failure domain than
   the Compose volumes, owned by the operator account (`BACKUP_UID`/`BACKUP_GID`, default the
   calling user), mode 0700.
3. **Off-site target** (at least one): an `s3://bucket/prefix` on another host
   (`BACKUP_OFFSITE`, credentials `BACKUP_OFFSITE_S3_ENDPOINT` / `_ACCESS_KEY` / `_SECRET_KEY` /
   `_REGION`; the host must be on the egress allowlist and `EGRESS_PROXY_URL` set), or a mounted
   directory of another host or medium (`BACKUP_OFFSITE_PATH`). The copy never needs the key.
4. **Tombstone journal**: bind `TOMBSTONE_JOURNAL_PATH` to a host directory outside the
   PostgreSQL and MinIO failure domains and replicate it continuously off the host (rsync/zfs
   send/object replication). Never restore it from an older copy; keep its files at least as long
   as the oldest set that may still be restored.
5. **Alerting** (contract with Lane D): every successful step rewrites the node_exporter textfile
   `${BACKUP_TEXTFILE_DIR:-$BACKUP_ROOT/metrics}/homayar_backup.prom` atomically (temp file +
   rename) with

   ```text
   homayar_backup_last_success_timestamp_seconds{target="postgres"} <snapshot unix time>
   homayar_backup_last_success_timestamp_seconds{target="objects"}  <snapshot unix time>
   homayar_backup_last_success_timestamp_seconds{target="offsite"}  <snapshot unix time of the copied set>
   ```

   A failed or interrupted run leaves the previous values, so the age alert (> 26 h, or absent)
   fires. Point node_exporter's `--collector.textfile.directory` at that directory.

Settings used by the tool (documented here; the application `.env.example` belongs to Lane D):

| Variable | Default | Purpose |
| --- | --- | --- |
| `BACKUP_ROOT` | `infra/backups` | sets, restore work dirs, default textfile dir |
| `BACKUP_KEY_FILE` | — (required) | mounted as `HOMAYAR_BACKUP_KEY_FILE` |
| `BACKUP_TEXTFILE_DIR` | `$BACKUP_ROOT/metrics` | node_exporter textfile directory |
| `BACKUP_OFFSITE` / `BACKUP_OFFSITE_PATH` | — | off-site target |
| `BACKUP_OFFSITE_S3_*` | — | S3 off-site credentials and endpoint |
| `HOMAYAR_RELEASE_SHA` / `HOMAYAR_RELEASE_VERSION` | — | recorded in the manifest (else `unknown`) |
| `BACKUP_UID` / `BACKUP_GID` | calling user | owner of the files the tool writes |

## 2. Scheduled backup

```bash
BACKUP_ROOT=/secure/homayar-backups BACKUP_KEY_FILE=/etc/homayar/backup.key \
BACKUP_OFFSITE=s3://homayar-offsite/site-a COMPOSE_ENV_FILE=/etc/homayar/onprem.env \
  infra/scripts/backup.sh
```

Run it daily (systemd timer or cron) while the stack runs; it does not stop traffic. Steps:
`create` → `verify` (full decryption) → `replicate` → `prune`. Any failing step exits non-zero.

What `create` does: takes the backup lock (a second concurrent run is refused), opens one
`REPEATABLE READ READ ONLY` snapshot with `ACCESS SHARE` locks on every table (a migration
cannot run underneath; a lock wait over 60 s fails the run), reads the catalog and every table's
rows in that snapshot, copies the objects of the snapshot's tenants, writes the encrypted catalog
and the sealed manifest, fsyncs and renames `sets/.partial-<id>` to `sets/<id>`. The database
connection must be a superuser or `BYPASSRLS` role (the bundled `POSTGRES_USER` is).

Each set's `manifest.json` identifies: `backup_id`, `created_at` (snapshot time), start/finish
and durations, `application` (version, release SHA, image), `database` (Alembic revision, code
heads, server version, tables, rows), `stores` (objects, bytes, missing/vanished/mismatched at
copy), `excluded`, `tombstone_journal` watermark, `encryption` (algorithm, key id, key
fingerprint, `plaintext_written_to_disk: false`) and every file with size and SHA-256, sealed by
`mac`.

Other commands (all through `docker compose ... --profile onprem --profile ops run --rm --no-deps -T backup-tool`):

| Command | Use |
| --- | --- |
| `list` | complete sets and interrupted partials |
| `verify [ID] [--no-key]` | full check (MAC, checksums, decryption); `--no-key` = checksums only (at an off-site copy) |
| `prune [--dry-run]` | retention 30 daily + 12 monthly, stale partials |
| `replicate ID s3://…` or `replicate ID /offsite` | off-site copy, verified before it is renamed into place |
| `fetch ID s3://…` | pull an off-site copy back into `BACKUP_ROOT` and verify it |

## 3. Disaster recovery (isolated restore)

Never restore over the running production project. Recovery happens in a **separate Compose
project** (new host or same host), with empty volumes, and the result only becomes usable after
reconciliation.

1. Provision the host, install the release (images by the manifest's `application.api_image` /
   release SHA or newer), restore deployment secrets from the secret manager — the **same**
   `TOKEN_PEPPER` and `PII_MAPPING_KEYS` (the report warns if their fingerprints differ) and a
   **new** `JWT_SECRET` (ends every session issued before the restore).
2. Make the replicated tombstone journal available as a host directory.
3. Run the isolated restore (timed phases go to `$BACKUP_ROOT/restores/<id>/timeline.log`):

   ```bash
   COMPOSE_PROJECT_NAME=homayar-restore COMPOSE_ENV_FILE=/etc/homayar/onprem.env \
   BACKUP_ROOT=/secure/homayar-backups BACKUP_KEY_FILE=/etc/homayar/backup.key \
   TOMBSTONE_JOURNAL_PATH=/replica/homayar-tombstones \
     infra/scripts/restore-isolated.sh --backup 20261017T020000Z-0a1b2c3d \
       --fetch s3://homayar-offsite/site-a --start-app
   ```

   The script starts only postgres, redis and minio (+ bootstrap), then:
   - `restore`: verifies the set, creates `<POSTGRES_DB>_restoring` (marked `restoring` in a
     `COMMENT ON DATABASE`), rebuilds the schema with `alembic upgrade <backup revision>`, loads
     every table in one transaction (row counts, per-tenant counts and all foreign keys checked),
     migrates to the code's head, re-applies the RLS grants and policies, restores the objects
     with checksum-verified writes; state `loaded`. It refuses the live or source database, a
     database that is not a restore target, a backup newer than the code, and an object store that
     already holds the backup's tenants (use `--resume` to finish an interrupted restore of the
     same set: identical bytes are accepted, different bytes fail);
   - `reconcile`: replays the tombstone journal (re-tombstone, cancel jobs, revoke shares, purge
     content rows and bytes), then checks schema head, RLS structure, RLS behaviour as
     `homayar_app` (no tenant row without context; no foreign rows with a bound tenant), tenant
     links and key prefixes, non-resurrection of every journaled conversation, required objects
     (missing = FAIL) and orphans (reported only), secret fingerprints, and post-backup
     revocations. It writes `reconciliation-report.json` sealed with the backup key;
   - `promote` (PASS only): renames the empty database the image created to
     `<POSTGRES_DB>_retired_<ts>` and the staging database to `POSTGRES_DB`; `gate` confirms;
   - `--start-app`: starts api, worker and scheduler **without ingress** and waits for `/readyz`.
4. Smoke tests through `docker compose ... exec api` (or a port-forward that is not public): an
   owner signs in, lists conversations, a conversation deleted after the backup returns 404, a
   user of another tenant gets 404 for it, a recording downloads.
5. Open ingress: `docker compose ... --profile onprem up -d --wait proxy`. Rebuild Neo4j if used.
6. Record the timeline, the report status and the measured RTO / data-loss window in a drill or
   incident record.

Owner decisions the report may ask for (they are recorded in the sealed report):

| Report status | Flag | When |
| --- | --- | --- |
| `REVIEW_REQUIRED` (held conversation in the journal) | `--accept-held REF` | the hold in the backup was released before the deletion; decide with the owner, then delete again after promotion if needed |
| `FAIL` objects (required object missing) | `--accept-missing REF` | accept losing those bytes (rows stay, the media is flagged missing by the reconciliation sweep) |
| `FAIL` tombstones (no journal) | `--accept-no-journal REF` | only for a deployment that never had a journal; deletions after the backup come back |
| revocations after the backup | `--revoke-share-links` | revoke every share link of the restored data (owners re-share) |

## 4. Legacy scripts

`backup-postgres.sh`, `backup-minio.sh`, `restore-postgres.sh`, `restore-minio.sh` and
`reapply-tombstones.sh` predate ADR 0035 (plaintext `pg_dump` / `mc mirror`, in-place restore).
They stay only to read sets made with them; new backups use `backup.sh`, and any restore goes
through the isolated procedure above (after a legacy in-place restore, `reapply-tombstones.sh`
must still pass before `api`/`worker` start).

## 5. Deletion tombstones and backup expiry

Backups are point-in-time copies and are never edited. A conversation deleted after a backup is
still inside that set; Homayar therefore does not promise instant erasure from backups, it
promises that restored data is deleted again before anyone can reach it:

1. Every deletion (explicit or retention-driven) is written, right after it commits, to the
   journal in `TOMBSTONE_JOURNAL_DIR` (ids, reasons, timestamps, opaque object keys only). The
   retention sweep writes any tombstone the journal missed (`TOMBSTONE_JOURNAL_BACKLOG`); a
   deployment without a journal logs `TOMBSTONE_JOURNAL_NOT_CONFIGURED` and cannot pass
   reconciliation without an explicit owner decision.
2. Reconciliation replays the journal before promotion and verifies that no journaled
   conversation is alive and none of its content rows, active share links or object bytes remain.
3. Legal holds and releases made after the backup are not journaled: the hold state is the one in
   the backup, and held conversations in the journal need an owner decision.
4. Deleted data remains inside existing sets until they expire under the retention policy (30
   daily + 12 monthly, provisional); `prune` deletes expired sets, and any restore of a set still
   in retention replays the journal.
