# Disaster Recovery Objectives

Version 1 · 2026-10-18 · HOM-153 (N12, reuses AppVision F79) · Status: **provisional,
owner-approved defaults** (owner decision D03 still open for the final production policy).
Implementation: ADR 0035, `Homayar/backend/backup/`, runbook
[`backup-restore.md`](backup-restore.md). Measured drill evidence: HOM-118,
[`drills/2026-10-19-restore-drill.md`](drills/2026-10-19-restore-drill.md) (run on `main` `935c41c`;
RTO measured for the drilled scope; RPO not demonstrated; earlier run on `b9836ae`:
[`drills/2026-10-18-restore-drill.md`](drills/2026-10-18-restore-drill.md)).

This record states what the supported topology promises and what it does not. Every number
here is a target for the drill to measure, never an estimate taken from a script's duration.

## 1. Scope of this version

| Profile | Status | How |
| --- | --- | --- |
| On-prem / local Compose (`infra/compose/compose.onprem.yaml`) | Implemented and tested (unit + PostgreSQL tests); restore drill run 2026-10-19 on `main` `935c41c` (HOM-118) | `backup-tool` service, `infra/scripts/backup.sh`, `infra/scripts/restore-isolated.sh` |
| Hosted cloud (`compose.cloud.yaml`, managed PostgreSQL / S3) | Same tool and targets; not drilled | provider snapshots/PITR may add to, never replace, this format |
| Offline profile | Out of scope here | the tool works without egress when the off-site target is a mounted directory |

## 2. Objectives (provisional owner defaults)

| Objective | Target | What the implementation can and cannot guarantee |
| --- | --- | --- |
| **RPO** | ≤ 24 h | A daily backup gives a data-loss window of up to 24 h *when every run succeeds*. The backup-age alert (Lane D, on `homayar_backup_last_success_timestamp_seconds`) fires at 26 h, so a missed or failed run is noticed about 2 h *after* the 24 h target is exceeded: the worst case is about 26 h plus the time to repair. Meeting 24 h strictly needs an alert below 24 h or a more frequent schedule (owner / Lane D decision). |
| **Deletions after the backup** | re-applied before use | Every tombstone is journaled at once outside the backups (ADR 0022) and replayed from the journal **replica** before a restore can be promoted. Deletions newer than the replica's last sync are lost, so this window equals the journal replication interval. Nothing measures or alerts on replication age yet; adding a replication-age alert is recommended. |
| **RTO** | ≤ 8 h | From the decision to restore to the first successful authenticated request on the restored system: stores up, fetch, verify, load, migrate, reconcile, promote, app ready, ingress. Measured (HOM-118, 2026-10-19, `main` `935c41c`): 172.3 s on one local Docker host with 4,925 rows / 441 MB; detection, provisioning, secrets, image pulls and DNS/TLS excluded. Provisioning a new host and restoring secrets are outside this path and must be added by the operator. |
| **Retention** | 30 daily + 12 monthly | See §3.1. Applies to the backup root and to every off-site copy. |
| **Off-host copy** | ≥ 1 encrypted copy outside the application host | `replicate` to an `s3://` bucket on another host (recommended) or to a mounted directory of another host or medium that carries the operator's `.homayar-offsite` marker (§3.2). |

## 3. Retention and off-site copies

### 3.1 Retention, precisely

- A set *qualifies* when it includes objects, its object copy is not `degraded`, and it passed a
  deep verify (the verify step writes `sets/<id>.verified`).
- **Daily:** the last 30 UTC days that have a set keep one set each, the newest qualifying set
  of the day (else the day's newest set).
- **Monthly:** the 12 most recent months *older than the daily window* keep one set each, the
  newest qualifying set of the month (else its newest). History therefore reaches about
  12 months beyond the 30 days, not 12 months counted from today.
- The newest set is always kept. Interrupted (`.partial-*`) sets are removed after 6 h; S3 copies
  without a manifest (interrupted uploads or prunes) and interrupted multipart uploads after 24 h.
- Off-site copies follow the same choice: the copy of a deep-verified set carries its verified
  marker (`sets/<id>.verified` in a directory, `<prefix>/<id>.verified` in S3), so off-site
  retention prefers the same full verified sets (copies made before markers were carried get one
  when their manifest is byte-identical to a verified root set). In a versioned bucket, prune
  deletes each removed set's versions and delete markers right after the set, and also sweeps
  versions an earlier interrupted prune left; versions an object lock still protects expire with
  the lock, so keep the lock period within the retention. A bucket whose versioning setting the
  credential cannot read is never swept, and any denied listing fails the run after it finishes.

### 3.2 Off-site copies

- An `s3://` target is used as configured; it should be a bucket on another host.
- A directory target counts as off-site only if the operator wrote `.homayar-offsite` on the
  remote medium, while mounted, when provisioning it. An unmounted share shows up as an empty
  local directory (Docker even creates a missing bind source); without the marker the copy is
  refused. The marker is **operator attestation**: the tool cannot see the medium. `backup.sh`
  compares the host paths, refuses an off-site path inside the backup root (or containing it)
  and warns when both share a filesystem; the operator confirms the medium with
  `df -P "$BACKUP_ROOT" "$BACKUP_OFFSITE_PATH"`. The tool alone sees only container paths.
- **Immutability: AppVision D15, DECIDED 2026-10-18 by the owner.** The S3 off-site bucket uses
  Object Lock in GOVERNANCE mode, enabled when the bucket is created (versioning on), with a
  default retention that matches the backup retention. Bypassing it needs a separately held,
  privileged break-glass credential; the backup tool's credential never has
  `s3:BypassGovernanceRetention`. An intruder on the application host can therefore not
  destroy locked copies before their retention ends. Deletions made after a backup still win,
  because every restore replays the tombstone journal before use. Known limit: the tool sets no
  per-object retention, so monthly sets are locked only for the default period. A directory
  off-site target has no such protection. Prune counts versions that are still locked, never as
  failures, and retries them on later runs.

## 4. Backup scope

Included in every set (one exported `REPEATABLE READ READ ONLY` snapshot, taken after
`ACCESS SHARE` locks on every table; the catalog is read in the same snapshot):

- **PostgreSQL:** the rows of every table in `public` (`COPY (SELECT …)`, which also reads
  partitioned tables), the Alembic revision, the sequences, per-table and per-tenant row counts.
- **Object storage:** every object under the tenant prefix of every tenant in the snapshot,
  copied through the application's storage interface, with a SHA-256 per object and the
  database's expectation for it (`required` / `optional` / `gone`); the stage outputs a legal
  hold keeps for a deleted meeting are `required` and held. Objects are copied after the
  snapshot. A required object missing at copy time is explained only by the row that set the
  expectation, when that row was removed, deleted or marked for deletion after the snapshot
  (e.g. a purge or "delete audio after processing"; the removal time may precede the snapshot
  by at most 15 minutes of clock skew, since the snapshot already shows the row live); another
  table's row with the same key never explains it, a legally held object is never explained,
  and a checkpoint the application found missing or corrupt stays a loss even once swept. An
  unexplained missing object, or bytes that do not match their row, mark the set's object copy
  `degraded`: the set is still kept (its database part is good), but the `objects` freshness
  gauge does not move and `backup.sh` exits non-zero.
- **Configuration references:** non-secret settings (mode, storage backend and bucket, providers,
  model manifest pins, PII key ids in use) and the *names* of the secrets a restore needs;
  `TOKEN_PEPPER` and `PII_MAPPING_KEYS` are pinned by fingerprints keyed with the backup key.
- **Version metadata:** backup id, snapshot time, start/finish, release version and SHA, API image
  reference, Alembic revision and the code's heads, PostgreSQL server version.
- **Tombstone-journal fingerprint:** name, size and SHA-256 of every journal file at backup
  time, record count and newest tombstone. The journal itself is not in the set.

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

## 5. Encryption requirements

- Every file of a set is encrypted before it reaches disk: AES-256-GCM in 1 MiB chunks (STREAM
  construction: chunk counter and last-chunk flag in the nonce), a per-file key derived with
  HKDF-SHA256 from the backup key, the backup id and the file name. No plaintext dump is ever
  written, not even temporarily.
- The manifest is plaintext (no customer content) and sealed with an HMAC under the backup key;
  every file is listed with its size and SHA-256, so an off-site copy can be checked without the
  key.
- The backup key (`key_id:base64(32 bytes)`) lives in the secret manager and in an escrow
  separate from every backup copy. **Losing the key loses every set encrypted with it.** Rotation:
  generate a new key id (`keygen`), use it for new sets, keep old keys until their sets expire.

## 6. Restore scope and the restore gate

- A restore always goes into an **isolated** target first: a separate Compose project (or
  server) with empty stores, and a staging database whose name contains `restoring`
  (`<db>_restoring`). The tool refuses the live database and the backup's source database by
  name (host strings are not trusted), a database that is not a marked restore target, an object
  store that already holds the backup's tenants, and a second concurrent run on the same staging
  database (advisory lock; `staging_busy`). Marker changes are compare-and-set.
- Users cannot reach restored data before reconciliation: the application only connects to the
  live name, and `promote` renames the staging database only with a reconciliation report that
  is PASS, sealed with the backup key, identical to the one recorded on the database, and made
  against the set (manifest MAC) the restore verified. The restored application starts without
  ingress; ingress opens after the smoke tests.
- Reconciliation must show:
  - the schema is at the Alembic head;
  - RLS is enabled and forced, with the Homayar policy, on every policy table;
  - RLS behaves as `homayar_app`: no tenant row is visible without a context, and a bound tenant
    sees no foreign rows;
  - tenant verification (scope, exactly):
    - per-tenant row counts equal the backup's at load time;
    - every foreign key between two tenant-keyed tables joins rows of the same workspace;
    - every `meeting_id` / `organization_id` link agrees on the tenant;
    - every object key sits under its own tenant prefix;
  - the journal replica is this deployment's journal and at least as new as the backup (§2,
    ADR 0035): it matches the backup's fingerprint of the journal files, or, if the backup
    predates every deletion, holds records of tenants the backup knows (records of tenants
    created later are skipped by the replay);
  - every journaled deletion is re-applied: its content rows, active share links and bytes are
    gone;
  - every required object is present and hashes to its restored row's SHA-256.
- `promote` renames the database under the live name to `<live>_retired_<ts>` and marks it with
  a `drop_after` deadline 7 days out, in the rename transaction. Right after, it is dropped if it
  holds no relation (the empty one the postgres image creates). One with data is kept: it is
  outside backups, retention and tombstone replay, so it must be dropped (`drop-retired`) by the
  deadline. The deadline is **operator-enforced**: `retired` flags overdue databases, but nothing
  drops them or alerts. Promotes onto one live name are serialised by an advisory lock.

## 7. What is not guaranteed (honest limits)

- Data written after the last backup's snapshot is lost (see the RPO row in §2).
- Deletions newer than the journal replica's last sync are lost (see §2).
- With no journal at all, a restore passes only with an explicit owner override that is recorded
  in the sealed report. Deletions made after the backup then come back and must be repeated.
- **Post-backup revocations are not replayed:** share links revoked, API keys revoked, sign-outs
  and password changes made after the backup come back. The reconciliation report lists the
  counts; the runbook rotates `JWT_SECRET` (ends every session) and offers `--revoke-share-links`.
- **Legal holds** are as in the backup. A conversation held in the backup and deleted after its
  release is reported as `REVIEW_REQUIRED`; promotion needs a recorded owner decision.
- Old object versions, MinIO IAM state and presigned URLs are not part of a set.
- A `restore --no-objects` is for inspection only and cannot be reconciled or promoted.
- Off-site copies are not immutable (§3.2).
- The RTO will be measured for the drilled data volume and host (HOM-118) and re-measured, not
  extrapolated, when the volume grows (quarterly drill, and after any PostgreSQL major upgrade).

## 8. Partial-failure states

| State | Where | Meaning | Usable? |
| --- | --- | --- | --- |
| `sets/.partial-<id>` | backup root | interrupted backup (failure, ^C, kill, power loss) | never; removed by `prune` |
| `sets/<id>` without `<id>.verified` | backup root | complete set not yet deep-verified | restorable after `verify`; does not move gauges |
| `sets/<id>` with object integrity `degraded` | manifest | database good, object copy missing/mismatching bytes | database yes; objects need an owner decision at restore |
| off-site copy without `manifest.json` / `.partial-` dir | off-site | interrupted copy | never; swept by `prune` |
| staging `restoring` | database comment | restore started or failed | never promotable; rerun redoes it |
| staging `loaded` | database comment | data, schema, RLS and objects in place | not yet: reconcile |
| staging `reconciling` | database comment | a reconcile is running or died | not promotable; rerun reconcile |
| staging `reconciled` + report FAIL / REVIEW_REQUIRED | database comment + report | checks failed or need an owner decision | not promotable |
| `promoted` | database comment | renamed to the live name after a PASS | yes |
| `<live>_retired_<ts>` | database comment `homayar-retired` | the database the promotion replaced | drop by `drop_after` (7 days, operator-enforced; `retired` flags overdue) |
