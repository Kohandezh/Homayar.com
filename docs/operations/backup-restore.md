# Backup and Restore Runbook

Objectives, scope and limits: [`disaster-recovery-objectives.md`](disaster-recovery-objectives.md)
(provisional owner defaults: RPO 24 h, RTO 8 h, 30 daily + 12 monthly, one encrypted copy off
the application host). Design: ADR 0035. Restore drill (HOM-118):
[`drills/2026-10-19-restore-drill.md`](drills/2026-10-19-restore-drill.md), run on `main`
`935c41c`. It measured the RTO for the drilled scope (172.3 s on one local Docker host;
detection, provisioning, secret retrieval and image pulls excluded). It did **not**
demonstrate the RPO. Earlier run on `b9836ae`:
[`drills/2026-10-18-restore-drill.md`](drills/2026-10-18-restore-drill.md).

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
   calling user; export both when running `docker compose ... run backup-tool` directly), mode
   0700.
3. **Off-site target** (at least one): an `s3://bucket/prefix` on another host
   (`BACKUP_OFFSITE`, credentials `BACKUP_OFFSITE_S3_ENDPOINT` / `_ACCESS_KEY` / `_SECRET_KEY` /
   `_REGION`; the host must be on the egress allowlist and `EGRESS_PROXY_URL` set; recommended),
   or a mounted directory of another host or medium (`BACKUP_OFFSITE_PATH`). When you provision a
   directory target, write the marker file `.homayar-offsite` **on the remote medium while it is
   mounted**: without it the copy is refused, because an unmounted share is just an empty local
   directory (Docker even creates a missing bind source). The marker is your attestation that
   the directory is another host or medium; nothing in the tool can check the medium. Before you
   write it, confirm on the host that `df -P "$BACKUP_ROOT" "$BACKUP_OFFSITE_PATH"` shows two
   different filesystems, the second being the remote share or medium. `backup.sh` compares the
   host paths: it refuses an off-site path inside `BACKUP_ROOT` (or containing it) and warns when
   both are on one filesystem. The tool itself sees only the container paths (`/backups`,
   `/offsite`), so a direct `replicate` does not catch a host path nested in the backup root.
   The copy never needs the key.

   **Immutability: AppVision D15, DECIDED 2026-10-18 by the owner.** The S3 off-site bucket uses
   Object Lock in **GOVERNANCE** mode, with retention matching the backup retention. Shortening
   or removing a lock needs a separate, privileged break-glass credential that holds
   `s3:BypassGovernanceRetention`, kept outside the application host and used only in an
   emergency. A deletion made after a backup is still honoured, because every restore replays
   the tombstone journal before the data is used. Bucket settings:

   - Object Lock is enabled **when the bucket is created** (it cannot be added later on most
     stores). This turns on versioning, and versioning must stay on.
   - Retention is a bucket default rule (GOVERNANCE, 30 days, matching the 30-day daily window),
     or per-object retention set by the store. The tool sets no per-object retention, so a
     monthly set is locked only for the default period, not for the full 12 months it is kept.
     A lock longer than the retention is safe: prune counts those versions and retries them
     later (see below).
   - The backup tool's credential **never** has `s3:BypassGovernanceRetention`.

   **S3 permissions** the off-site credential needs, on the bucket (`arn:aws:s3:::BUCKET`) and
   its objects under the prefix (`arn:aws:s3:::BUCKET/PREFIX/*`):

   | Step | Actions |
   |---|---|
   | `replicate` | `s3:PutObject`, `s3:GetObject` (checksum HEAD and read-back), `s3:ListBucket` (verified-marker backfill) |
   | `fetch` | `s3:GetObject`, `s3:ListBucket` |
   | `prune --destination s3://…` | `s3:ListBucket`, `s3:GetObject`, `s3:DeleteObject`, `s3:ListBucketMultipartUploads`, `s3:AbortMultipartUpload`, `s3:GetBucketVersioning`; in a versioned bucket also `s3:ListBucketVersions`, `s3:DeleteObjectVersion`, `s3:GetBucketObjectLockConfiguration`, `s3:GetObjectRetention`, `s3:GetObjectLegalHold` |
   | never | `s3:BypassGovernanceRetention` (only the separately held break-glass credential) |

   A prune whose credential lacks a listing permission still applies the retention, reports
   each denied call in `errors` and exits non-zero at the end, so the nightly run fails
   visibly. When it cannot read the bucket's versioning (`versioning: "unknown"`) it never
   sweeps versions. A refused version delete counts in `versions_locked` only when the bucket has
   Object Lock and that version has an unexpired retention or a legal hold (`locked_until`
   reports the latest date). That key keeps its delete marker, so the expired set stays hidden,
   and prune retries it once the newest marker is older than 24 hours, until the lock expires.
   Every other refusal (for example a missing `s3:DeleteObjectVersion`) counts in
   `versions_failed` and fails the run. Versions left with no current data are swept on a later
   run only after the 24-hour grace period, and never while the set has a current version, so a
   copy that is being uploaded again is not touched.
4. **Tombstone journal**: bind `TOMBSTONE_JOURNAL_PATH` to a host directory outside the
   PostgreSQL and MinIO failure domains and replicate it continuously off the host (rsync/zfs
   send/object replication). Never restore it from an older copy; keep its files at least as long
   as the oldest set that may still be restored. Deletions newer than the replica's last sync are
   lost on a restore: the replication interval is that window. Nothing measures or alerts on it
   yet; add a replication-age check.
5. **Alerting** (contract with Lane D): the node_exporter textfile
   `$NODE_EXPORTER_TEXTFILE_DIR/homayar_backup.prom` is rewritten under a lock, through a unique
   temporary file and an atomic rename (file 0644; the directory's mode is left alone), with

   ```text
   homayar_backup_last_success_timestamp_seconds{target="postgres"} <snapshot unix time>
   homayar_backup_last_success_timestamp_seconds{target="objects"}  <snapshot unix time>
   homayar_backup_last_success_timestamp_seconds{target="offsite"}  <snapshot unix time of the copied set>
   ```

   Only a successful deep `verify` moves `postgres` (and `objects`, when the set includes objects
   and its copy is not `degraded`); only a verified off-site copy moves `offsite`. A value never
   moves backwards. A failed, interrupted or degraded run leaves the previous values, so the age
   alert (> 26 h, or absent) fires.

   `NODE_EXPORTER_TEXTFILE_DIR` is **required** by `backup.sh` and must be **outside** the 0700
   backup root, because a non-root node_exporter cannot traverse a 0700 directory. The container
   writes it as `BACKUP_UID:BACKUP_GID` (by default the uid and gid of whoever runs `backup.sh`:
   0:0 from root's crontab) **without** `CAP_DAC_OVERRIDE` (the service drops every capability),
   so even root in the container can only write a directory it owns. Create it once, owned by
   exactly that pair, mode 0755, for example `/var/lib/node_exporter/textfile_collector`, as the
   account that will run the schedule:

   ```bash
   export BACKUP_UID="${BACKUP_UID:-$(id -u)}" BACKUP_GID="${BACKUP_GID:-$(id -g)}"
   sudo install -d -o "$BACKUP_UID" -g "$BACKUP_GID" -m 0755 /var/lib/node_exporter/textfile_collector
   ```

   Every run first probes the directory **as that container user** (`textfile-check`). If the
   gauges cannot be written, the backup, the off-site copy and prune still run, the stale or
   missing gauge raises the alert, and the run exits 4.

   Then start node_exporter with `--collector.textfile.directory` pointing at it. In a container,
   bind-mount exactly that directory read-only, e.g.
   `-v /var/lib/node_exporter/textfile_collector:/textfile:ro --collector.textfile.directory=/textfile`.
   `backup.sh` refuses a directory inside `BACKUP_ROOT` and never changes its mode. An isolated
   restore writes its own textfile under `restores/<id>/metrics` and never the production one.

Settings used by the tool (documented here; the application `.env.example` belongs to Lane D):

| Variable | Default | Purpose |
| --- | --- | --- |
| `BACKUP_ROOT` | `infra/backups` | sets, restore work dirs |
| `BACKUP_KEY_FILE` | — (required) | mounted as `HOMAYAR_BACKUP_KEY_FILE` |
| `NODE_EXPORTER_TEXTFILE_DIR` | — (required by `backup.sh`; outside `BACKUP_ROOT`; `BACKUP_TEXTFILE_DIR` is accepted as an alias) | node_exporter's textfile directory, mounted at `/textfile` |
| `BACKUP_DATABASE_URL` / `BACKUP_DATABASE_URL_FILE` | `DATABASE_URL` | superuser / BYPASSRLS DSN; never passed on the command line |
| `BACKUP_OFFSITE` / `BACKUP_OFFSITE_PATH` | — | off-site target |
| `BACKUP_OFFSITE_S3_*` | — | S3 off-site credentials and endpoint |
| `HOMAYAR_RELEASE_SHA` / `HOMAYAR_RELEASE_VERSION` | — | recorded in the manifest (else `unknown`) |
| `BACKUP_UID` / `BACKUP_GID` | calling user | owner of the files the tool writes |

## 2. Scheduled backup

```bash
BACKUP_ROOT=/secure/homayar-backups BACKUP_KEY_FILE=/etc/homayar/backup.key \
BACKUP_OFFSITE=s3://homayar-offsite/site-a COMPOSE_ENV_FILE=/etc/homayar/onprem.env \
NODE_EXPORTER_TEXTFILE_DIR=/var/lib/node_exporter/textfile_collector \
  infra/scripts/backup.sh
```

Run it daily (systemd timer or cron) while the stack runs, as the account that owns
`NODE_EXPORTER_TEXTFILE_DIR` (or with `BACKUP_UID`/`BACKUP_GID` set to its owner); it does not
stop traffic. Use absolute paths: a relative `BACKUP_ROOT`, `BACKUP_KEY_FILE`,
`BACKUP_OFFSITE_PATH` or `NODE_EXPORTER_TEXTFILE_DIR` is resolved against the Compose file's
directory (`infra/compose/`), the same base Compose uses for its bind mounts. Steps:
`textfile-check` → `create` → `verify` (full decryption; marks the set verified and moves the
gauges) → `replicate` → `prune` (the backup root and the off-site copy). Exit codes: 0 done;
3 the set's object copy is `degraded` (a required object missing for no recorded reason, or
bytes that do not match their row: the set is still verified and copied off-site, but the
`objects` gauge does not move; investigate the primary object store); 4 the backup, copy and
prune completed but the freshness textfile could not be written (fix its owner); anything else:
a step failed and the steps after it did not run.

What `create` does: takes the backup lock (a second concurrent run is refused), opens one
`REPEATABLE READ READ ONLY` snapshot with `ACCESS SHARE` locks on every table (a migration
cannot run underneath; a lock wait over 60 s fails the run), reads the catalog and every table's
rows in that snapshot (`COPY (SELECT …)`, so partitioned tables work), copies the objects of the
snapshot's tenants, explains a required object it did not find only by the row that set the
expectation, when that row was deleted or marked for deletion after the snapshot (a legally held
object, including the stage outputs a hold keeps for a deleted meeting, is never explained, and a
checkpoint found missing or corrupt by the application stays a loss), fingerprints the tombstone journal, writes the encrypted
catalog and the sealed manifest, fsyncs and renames `sets/.partial-<id>` to `sets/<id>`. It
moves no gauge. The database connection must be a superuser or `BYPASSRLS` role (the bundled
`POSTGRES_USER` is); a role subject to RLS is refused.

Each set's `manifest.json` identifies: `backup_id`, `created_at` (snapshot time), start/finish
and durations, `application` (version, release SHA, image), `database` (Alembic revision, code
heads, server version, tables, rows), `stores` (objects, bytes, `integrity`, missing (explained /
unexplained), vanished and mismatched at copy), `excluded`, `tombstone_journal` (fingerprint of
every journal file, record count, newest tombstone), `encryption` (algorithm, key id, key
fingerprint, `plaintext_written_to_disk: false`) and every file with size and SHA-256, sealed by
`mac`.

Other commands (all through `docker compose ... --profile onprem --profile ops run --rm --no-deps -T backup-tool`):

| Command | Use |
| --- | --- |
| `list` | complete sets and interrupted partials |
| `verify [ID] [--no-key]` | full check (MAC, checksums, decryption), then marks the set verified and moves the gauges; `--no-key` = checksums only (at an off-site copy), marks nothing |
| `prune [--dry-run] [--destination DIR\|s3://…]` | retention (see the objectives, §3.1) of the root or an off-site copy, preferring full verified sets; removes stale partials, manifest-less S3 copies and interrupted multipart uploads older than 24 h; in a versioned bucket also each removed set's versions right after the set, and versions an earlier interrupted prune left (`leftover_sets`); a partial copy with a live multipart upload is not stale. Counts `versions_locked` (object lock) and `versions_failed`; listing or delete errors are reported in `errors` and exit 1 after the run |
| `replicate ID s3://…` or `replicate ID /offsite` | off-site copy, every file verified by SHA-256 (directory: re-hashed before the rename; S3: the store's SHA-256 checksum, else read back) before the `offsite` gauge moves; carries the set's verified marker, and backfills it for earlier copies whose manifest is byte-identical to a verified root set (`backfilled_markers`) (directory: needs `.homayar-offsite`) |
| `fetch ID s3://…` | pull an off-site copy back into `BACKUP_ROOT`, checksums verified before it is renamed into place |
| `retired` / `drop-retired NAME --confirm NAME [--live-db NAME]` | databases a promotion replaced, each with a 7-day `drop_after` deadline and flagged `overdue` past it; drop them by that deadline (operator-enforced: nothing drops them or alerts). `drop-retired` refuses the live database, a `restoring` database, a name not of the form `<live>_retired_<ts>` and a database with open sessions, whatever its comment says |

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
   - every step holds an advisory lock on the staging database (a second concurrent run is
     refused with `staging_busy`) and changes its marker only compare-and-set;
   - `restore`: verifies the set, creates `<POSTGRES_DB>_restoring` (marked `restoring` in a
     `COMMENT ON DATABASE`, together with the set's manifest MAC), rebuilds the schema with
     `alembic upgrade <backup revision>`, loads
     every table in one transaction (row counts, per-tenant counts and all foreign keys checked),
     migrates to the code's head, re-applies the RLS grants and policies, restores the objects
     with checksum-verified writes; state `loaded`. It refuses the live or source database (by
     name), a staging name without `restoring`, a database that is not a restore target, a backup
     newer than the code, and an object store that
     already holds the backup's tenants (use `--resume` to finish an interrupted restore of the
     same set into the same store: identical bytes are accepted, different bytes fail). A
     `--no-objects` restore is for inspection only: it cannot be reconciled or promoted;
   - `reconcile`: verifies the manifest MAC and that it is the set the restore loaded; checks
     that the journal replica is this deployment's journal and at least as new as the backup
     (every journal file fingerprinted by the backup must be present with the same leading
     bytes; a backup taken before the first deletion only accepts a replica whose records all
     belong to its tenants); replays it (re-tombstone, cancel jobs, revoke shares, purge content
     rows and bytes, remove journaled bytes of conversations the snapshot did not know); then
     checks schema head, RLS structure, RLS behaviour as `homayar_app`, tenant verification
     (per-tenant counts at load, every foreign key between tenant-keyed tables, meeting and
     organization links, key prefixes), non-resurrection of every journaled conversation,
     required objects (present **and** hashing to their row's SHA-256; an object the backup
     recorded as deleted after the snapshot is a WARN, anything else missing or different
     FAILs), orphans (reported only), secret fingerprints and post-backup revocations. It writes
     `reconciliation-report.json`, sealed with the backup key and listing every owner override;
   - `promote` (PASS only, the latest report of this database): renames the staging database to
     `POSTGRES_DB`. The database under that name is renamed `<POSTGRES_DB>_retired_<ts>` and
     marked with a 7-day `drop_after` deadline in the same transaction; right after, it is
     dropped if it holds no relation (the empty one the postgres image creates). One **with
     data** is kept: it is outside backups, retention and tombstone replay, so drop it with
     `drop-retired` by its deadline (`retired` flags it `overdue` after that; nothing drops it
     for you). Promotes onto one live name are serialised (`live_busy`). `gate` confirms;
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
| `FAIL` objects (required object missing or corrupt) | `--accept-missing REF` | accept losing or keeping damaged bytes (rows stay; the media is flagged by the reconciliation sweep) |
| `FAIL` tombstones, no evidence (no journal files, files without records, or records only of tenants the backup does not know) | `--accept-no-journal REF` | the only owner override for the journal; it is recorded in the sealed report and the restore timeline. Any replica present is still replayed (unknown tenants are skipped); with no replica, deletions after the backup come back and must be repeated by the owner. A replica that holds records of tenants created after the backup **and** of known tenants passes without it |
| `FAIL` tombstones, stale or foreign replica | none | cannot be accepted: mount the current replica, or remove it and decide on `--accept-no-journal` |
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
   daily + 12 monthly, provisional) — in the backup root and in every off-site copy that is
   pruned (or expired by its bucket lifecycle); any restore of a set still in retention replays
   the journal. A database retired by `promote` must be dropped by its 7-day `drop_after`
   deadline for the same reason; the deadline is operator-enforced (`retired` lists overdue
   ones).
