# Backup and Restore Runbook

## Policy

PostgreSQL is the transactional source of truth. MinIO contains private recordings and
exports. Redis is ephemeral and is not backed up. Neo4j is a rebuildable projection; export
it separately only when recomputation time exceeds the accepted recovery objective.

Set the production RPO, RTO, retention, encryption, and off-site replication policy outside
this repository. Store backup data on encrypted storage that is not the same disk or failure
domain as the Compose volumes. Restrict backup directories because metadata and objects can
contain customer information.

The scripts use the on-premise Compose project. They inherit deployment variables from the
current shell. `COMPOSE_ENV_FILE` may point to an operator-owned, mode-`0600` environment
file, and `BACKUP_ROOT` may select an encrypted mounted destination.

## PostgreSQL Backup

```bash
BACKUP_ROOT=/secure/homayar-backups infra/scripts/backup-postgres.sh
```

Each set contains a compressed custom-format `database.dump`, a table of contents, metadata,
SHA-256 files, and a `.complete` marker. The dump excludes ownership and grants so database
roles and credentials remain deployment-managed. `pg_dump` provides a transactionally
consistent database snapshot without stopping application traffic.

After every scheduled backup:

1. Require the script to exit successfully and the `.complete` marker to exist.
2. Replicate the full backup set to the protected off-site destination.
3. Alert on age, size anomalies, missing checksums, or replication failure.
4. Perform a restore drill to an isolated database at least quarterly and after PostgreSQL
   major-version changes.

## PostgreSQL Restore

A restore cleans objects in the existing target database inside one transaction. The target
database and role must already exist. Stop all writers and confirm the exact database name:

```bash
docker compose --file infra/compose/compose.onprem.yaml --profile onprem stop api worker
BACKUP_ROOT=/secure/homayar-backups \
  infra/scripts/restore-postgres.sh \
  --backup /secure/homayar-backups/postgres/20260818T120000Z \
  --confirm homayar
```

The script refuses to continue when `api` or `worker` is running, when the confirmation does
not equal `POSTGRES_DB`, when checked metadata identifies a different source database, or
when checksums fail. `ALLOW_SOURCE_DATABASE_MISMATCH=1` is required for an approved database
rename or migration. `ALLOW_UNVERIFIED_BACKUP=1` and `ALLOW_INCOMPLETE_BACKUP=1` are
break-glass controls for independently verified legacy media; record approval and evidence
before using any of them.

After restore, apply only migrations that are newer than the backup, reapply deletion
tombstones (see below) while `api` and `worker` are still stopped, then start API before the
worker, wait for `/readyz`, and verify tenant counts, entitlements, jobs, audit events, and
sample object references before reopening traffic.

## MinIO Backup

```bash
BACKUP_ROOT=/secure/homayar-backups infra/scripts/backup-minio.sh
```

The script mirrors the current version of every object in `MINIO_BUCKET`, writes a versioned
inventory, metadata, checksums, and a `.complete` marker. This portable mirror does not
preserve deleted object versions, bucket IAM configuration, KMS keys, lifecycle rules, or
replication state. Production installations that require version-level recovery must add
storage-native replication or volume snapshots and test those procedures independently.

MinIO root credentials are used only by backup/bootstrap tooling. The application uses the
dedicated `STORAGE_ACCESS_KEY` policy created during bootstrap.

## MinIO Restore

Stop writers, restore PostgreSQL first, and then restore objects. The default operation
overwrites matching keys but leaves extra target keys in place:

```bash
docker compose --file infra/compose/compose.onprem.yaml --profile onprem stop api worker
BACKUP_ROOT=/secure/homayar-backups \
  infra/scripts/restore-minio.sh \
  --backup /secure/homayar-backups/minio/20260818T120000Z \
  --confirm-bucket homayar-private
```

Use `--delete-extra` only for an approved exact mirror restore; it removes target keys that
are absent from the backup. The script requires the backup directory to be below
`BACKUP_ROOT`, validates metadata and inventory checksums, confirms the recorded source
bucket, and rejects active writers. `ALLOW_SOURCE_BUCKET_MISMATCH=1` is required for an
approved bucket rename or migration.

After restore, sample object sizes and checksums through the storage API, verify private
access controls, run an authorized download, and run an unauthorized access test. Rebuild
the optional Neo4j projection from PostgreSQL after both authoritative stores are healthy.

## Deletion Tombstones and Backup Expiry

Backups are point-in-time copies and are never edited. A conversation deleted after a backup
was taken still exists in that backup, and restoring it would bring the conversation back.
Homayar therefore does not promise instant erasure from backups; it promises that restored
data is deleted again before anyone can reach it:

1. Every deletion (explicit or retention-driven) is written, right after it commits, to the
   tombstone journal in `TOMBSTONE_JOURNAL_DIR` (`/app/tombstones`, volume
   `TOMBSTONE_JOURNAL_PATH`). The journal holds ids, reasons, timestamps and opaque object
   keys only. Bind it to storage outside the PostgreSQL and MinIO failure domains, replicate
   it off-site continuously, and never restore it from an older copy. Keep journal files at
   least as long as the oldest backup that may still be restored.
2. The retention sweep writes any tombstone the journal missed and logs
   `TOMBSTONE_JOURNAL_BACKLOG`; alert on it. A deployment without a journal logs
   `TOMBSTONE_JOURNAL_NOT_CONFIGURED` at startup and cannot reapply deletions after a restore.
3. After restoring PostgreSQL (and MinIO) and applying newer migrations, with `api` and
   `worker` stopped and `postgres`/`minio` running:

   ```bash
   infra/scripts/reapply-tombstones.sh
   ```

   The script re-tombstones every journaled conversation that came back, reopens its purge
   so restored transcripts, analysis and objects are removed again, and then verifies. It
   exits non-zero while any journaled conversation is still alive or a journal line fails
   its checksum; do not start `api` or `worker` until it succeeds. `--remove-unknown-keys`
   also removes journaled objects of conversations the restored database does not know
   (only after an approved review; referenced objects are never removed).
4. Legal holds and hold releases made after the backup are not journaled: after a restore,
   the hold state is the one in the backup. Review holds with the owner before reapplying.
5. Deleted data remains inside existing backup sets until those sets expire under the
   deployment's backup retention (owner decision; set it outside this repository and state
   it in customer terms). Expire backup sets on schedule and record the expiry; a restore
   of any set still runs step 3.

## Disaster Recovery Order

1. Provision clean infrastructure and restore deployment secrets from the secret manager.
2. Start PostgreSQL, Redis, and MinIO only.
3. Restore PostgreSQL, then MinIO, and verify both independently.
4. Run forward migrations required by the selected application image.
5. Reapply deletion tombstones (`infra/scripts/reapply-tombstones.sh`) and require success.
6. Start API, verify readiness, then start workers and verify Celery health.
7. Rebuild optional search or graph projections.
8. Start both web services and nginx, run smoke tests, then reopen traffic.
