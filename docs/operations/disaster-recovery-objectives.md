# Disaster Recovery Objectives

Version 1 · 2026-10-17 · HOM-153 (N12, reuses AppVision F79) · Status: **provisional,
owner-approved defaults** (owner decision D03 still open for the final production policy).
Implementation: ADR 0035, `Homayar/backend/backup/`, runbook
[`backup-restore.md`](backup-restore.md). Measured evidence: [`drills/`](drills/).

This record states what the supported topology promises and what it does not. Every number
here is a target the drill measures, never an estimate taken from a script's duration.

## 1. Scope of this version

| Profile | Covered | How |
| --- | --- | --- |
| On-prem / local Compose (`infra/compose/compose.onprem.yaml`) | Yes | `backup-tool` service, `infra/scripts/backup.sh`, `infra/scripts/restore-isolated.sh` |
| Hosted cloud (`compose.cloud.yaml`, managed PostgreSQL / S3) | Same tool and targets, **not yet drilled** | provider snapshots/PITR may add to, never replace, this format |
| Offline profile | Out of scope here | the same tool works without egress when the off-site target is a mounted directory |

## 2. Objectives (provisional owner defaults)

| Objective | Target | What it means here |
| --- | --- | --- |
| **RPO** | ≤ 24 h | Data committed after the last successful backup's snapshot time can be lost. A daily schedule plus an alert on backup age > 26 h (Lane D rule on `homayar_backup_last_success_timestamp_seconds`) bounds it. |
| **Deletions** | 0 h | Deletions are never lost: every tombstone is journaled at once outside the backups (ADR 0022) and replayed before a restore is usable. This holds only while the journal volume survives (it is replicated separately). |
| **RTO** | ≤ 8 h | From the decision to restore to the first successful authenticated request on the restored system. Measured end to end by the drill (stores up, fetch, verify, load, migrate, reconcile, promote, app ready, ingress). Provisioning a new host and restoring secrets are outside the measured path and must be added by the operator. |
| **Retention** | 30 daily + 12 monthly | `python -m backend.backup prune`: newest set of each of the last 30 days that have a set and of each of the last 12 months; the newest set is always kept. |
| **Off-host copy** | ≥ 1 encrypted copy outside the application host | `replicate` to an `s3://` bucket on another host or to a mounted directory of another host/medium, after every backup. |

## 3. Backup scope

Included in every set (one exported `REPEATABLE READ READ ONLY` snapshot for the database; the
catalog is read in the same snapshot):

- **PostgreSQL:** the rows of every table in `public` (logical `COPY`), the Alembic revision, the
  sequences, per-table and per-tenant row counts.
- **Object storage:** every object under the tenant prefix of every tenant in the snapshot
  (recordings, derived media, stage artifacts), copied through the application's storage interface,
  with a SHA-256 per object and the database's expectation for it (`required` / `optional` /
  `gone`).
- **Configuration references:** non-secret settings (mode, storage backend and bucket, providers,
  model manifest pins, PII key ids in use) and the *names* of the secrets a restore needs;
  `TOKEN_PEPPER` and `PII_MAPPING_KEYS` are pinned by fingerprints keyed with the backup key.
- **Version metadata:** backup id, snapshot time, start/finish, release version and SHA, API image
  reference, Alembic revision and the code's heads, PostgreSQL server version.
- **Tombstone-journal watermark** (record count, newest tombstone), not the journal itself.

Excluded on purpose (recorded in every manifest under `excluded`):

| Item | Why | Recovered by |
| --- | --- | --- |
| Redis | queue/cache only | outbox and lease recovery re-dispatch from PostgreSQL |
| Neo4j | optional projection | rebuild from PostgreSQL |
| Secrets (JWT, pepper, storage, MinIO root, Redis, PII keys, backup key) | must not sit next to the data | the deployment's secret manager |
| Tombstone journal | must outlive every backup | separate replicated volume, replayed on restore |
| Container images, model weights | release artifacts | registry / release bundle by recorded reference |
| MinIO IAM and bucket policy | deployment configuration | `minio-bootstrap` |
| Upload quarantine, legacy upload dir, beat schedule | transient | recreated empty |

## 4. Encryption requirements

- Every file of a set is encrypted before it reaches disk: AES-256-GCM in 1 MiB chunks (STREAM
  construction: chunk counter and last-chunk flag in the nonce), a per-file key derived with
  HKDF-SHA256 from the backup key, the backup id and the file name. No plaintext dump is ever
  written, also not temporarily.
- The manifest is plaintext (no customer content) and sealed with an HMAC under the backup key;
  every file is listed with its size and SHA-256 so an off-site copy can be checked without the
  key.
- The backup key (`key_id:base64(32 bytes)`) lives in the secret manager and in an escrow
  separate from every backup copy. **Losing the key loses every set encrypted with it.** Rotation:
  generate a new key id (`keygen`), use it for new sets, keep old keys until their sets expire.

## 5. Restore scope and the restore gate

- A restore always goes into an **isolated** target first: a separate Compose project (or
  server) with empty stores, and the database name `<db>_restoring`. The tool refuses the live
  database, the backup's own source database, a database that is not a marked restore target
  and an object store that already holds the backup's tenants.
- Users cannot reach restored data before reconciliation: the application only connects to the
  live name, and `promote` renames the staging database only with a reconciliation report that is
  PASS and sealed with the backup key. The restored application starts without ingress; ingress
  opens after the smoke tests.
- Reconciliation must show: Alembic head, RLS enabled/forced with the Homayar policy on every
  policy table, no cross-tenant visibility as the application role, per-tenant row counts equal
  to the backup at load, consistent tenant links and key prefixes, every journaled deletion
  re-applied (content rows, share links and bytes gone), every required object present.

## 6. What is not guaranteed (honest limits)

- Data written after the last backup's snapshot is lost (≤ RPO). The drill records the window.
- **Post-backup revocations are not replayed:** share links revoked, API keys revoked, sign-outs
  and password changes made after the backup come back. The reconciliation report lists the
  counts; the runbook rotates `JWT_SECRET` (ends every session) and offers `--revoke-share-links`.
- **Legal holds** are as in the backup. A conversation held in the backup and deleted after its
  release is reported as `REVIEW_REQUIRED`; promotion needs a recorded owner decision.
- Objects are copied after the database snapshot: an object deleted in between is reported
  (`missing_at_copy`, normally a journaled deletion); one created in between is an orphan of the
  snapshot and is reported, never deleted.
- Old object versions, MinIO IAM state and presigned URLs are not part of a set.
- The measured RTO applies to the drilled data volume and host; it is re-measured, not
  extrapolated, when the volume grows (quarterly drill, and after any PostgreSQL major upgrade).

## 7. Partial-failure states

| State | Where | Meaning | Usable? |
| --- | --- | --- | --- |
| `sets/.partial-<id>` | backup root | interrupted backup (failure, ^C, kill, power loss) | never; removed by `prune` |
| `sets/<id>` | backup root | complete set (renamed after fsync + sealed manifest) | yes, after `verify` |
| off-site copy without `manifest.json` / `.partial-` dir | off-site | interrupted copy | never |
| staging `restoring` | database comment | restore started or failed | never promotable; rerun redoes it |
| staging `loaded` | database comment | data, schema, RLS and objects in place | not yet: reconcile |
| staging `reconciled` + report FAIL / REVIEW_REQUIRED | database comment + report | checks failed or need an owner decision | not promotable |
| `promoted` | database comment | renamed to the live name after a PASS | yes |
