# Restore drill — 2026-10-18 (HOM-118)

First real execution of `infra/scripts/dr-drill.sh` against the on-prem Compose profile:
a seeded "production" project is backed up, changed, destroyed with its volumes, and
recovered from the off-site copy into an isolated project. Objectives:
[`disaster-recovery-objectives.md`](../disaster-recovery-objectives.md); runbook:
[`backup-restore.md`](../backup-restore.md); design: ADR 0035. Every number below comes
from the recorded evidence in [`2026-10-18-evidence/`](2026-10-18-evidence/); nothing is
estimated.

**Result:** recovery usable after **378.1 s (6 min 18 s)** from the recovery decision
(target ≤ 8 h). Measured data-loss window **318.9 s (5 min 19 s)**. Reconciliation
**PASS**, smoke tests **11/11**, every restore-gate probe behaved as designed. No product or
tool bug was found; four harness bugs were fixed before the recorded run (see §9).

## 1. Gates

| Gate | Result | Evidence |
| --- | --- | --- |
| Backup created, MAC and every checksum verified, decryptable | PASS | `manifest.json`; `verify` in the run log: `mac_verified`, `decrypted`, 446 files |
| Off-site copies (directory + S3 stand-in), fetch checksum-verified | PASS | `summary.json` → `backup.offsite_s3_seconds`, `recovery.fetch_seconds` |
| Reconciliation | **PASS** (9 × PASS, `post_backup_state` WARN, non-blocking by design) | `reconciliation-report.json` (sealed) |
| Promote only with a PASS report for this staging database | PASS (5/5 probes as expected) | `gate-probes.jsonl` |
| Smoke tests through nginx on the restored system | PASS (11/11) | `smoke.json` |
| RTO ≤ 8 h | **met** — 378.1 s | `summary.json` → `achieved_rto_seconds` |
| RPO ≤ 24 h | measured window met (318.9 s); schedule-based worst case **24 h + 4 min 13 s**, see §5 | `summary.json` → `data_loss_window_seconds` |
| Deletions not lost (0 h) | PASS — 3 post-backup deletions re-applied, none resurrected | §7 |

## 2. Environment

| Item | Value |
| --- | --- |
| Host | MacBook Pro, Intel Core i9-8950HK (6 cores / 12 threads), 32 GB RAM, macOS 15.8.2 |
| Container runtime | Docker Desktop, Engine 29.5.3, Compose v5.1.4; Linux VM with 12 vCPU and 7.75 GiB RAM |
| Host load | Shared with other sessions. 1-min load average 133.9 at drill start (04:47Z), sampled 11.6 / 11.0 / 14.0 at 05:02, 05:04 and 05:07Z (recovery), 19.5 at the end (05:09Z). Earlier attempts ran at 150–330. |
| Code | API image `homayar/api:lane-c-drill` (`sha256:7bee6383…`) built from this branch at `5b6d07b` (`Homayar/backend/backup` at head). Harness as committed in `75d14af` (the run used that tree; only the script's header comment was added afterwards). |
| Web images | `homayar-ci/public-web:local` (`sha256:4a3036d1…`, built 2026-09-25), `homayar-ci/console-web:local` (`sha256:9a541fb0…`, 2026-09-25) — CI builds, not of this commit; only health-checked |
| Stores | `postgres:17-alpine` (server 17.11), `redis:7.4-alpine`, `nginx:1.28-alpine`, `quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z`, `quay.io/minio/mc:RELEASE.2025-04-16T18-13-26Z` — all local, no pulls |
| Application mode | `APP_ENVIRONMENT=production`, `DEPLOYMENT_MODE=onprem`, `SECURE_COOKIES=true`, internal-hop TLS opt-outs as shipped (HOM-196) |
| Data volume | 4 tenants, 120 conversations (30 per tenant, every status the console shows), 61 tables, 4,955 rows, 384 objects = 447,545,248 B of object bytes; encrypted set 450,046,149 B in 446 files |

Data comes from the demo seed (`backend.database.demo_seed`, run once per tenant by
`infra/scripts/drill/seed_tenants.py`): verified accounts per role, meetings processed by the
real job processor with scripted providers, share links, API keys, audit history; recordings
are 20–59 s of 48 kHz mono silence.

## 3. Procedure

`DRILL_DIR=<new scratch dir> API_IMAGE=homayar/api:lane-c-drill DRILL_TENANTS=4
PUBLIC_WEB_IMAGE=homayar-ci/public-web:local CONSOLE_WEB_IMAGE=homayar-ci/console-web:local
infra/scripts/dr-drill.sh` (Compose projects `homayar-drill-prod` / `homayar-drill-restore`,
off-site container `homayar-drill-offsite`, ports 127.0.0.1:18080/18081/19000):

1. **Production:** stores, `minio-bootstrap`, `migrate`, full `onprem` profile; seed 4 tenants.
2. **Backup:** `infra/scripts/backup.sh` (create → verify → replicate to the off-site
   directory → prune local and off-site), then `replicate` to `s3://homayar-offsite/site-a`.
3. **After the backup** (60 s gap), through nginx as a browser would: delete two
   conversations of tenant 0 and one of tenant 1 (journaled tombstones), revoke one share
   link of tenant 1, upload one new recording to tenant 0. The tombstone journal is then
   copied to its replica.
4. **Failure:** `down --volumes --remove-orphans` on the production project and removal of
   its journal and local backup root. Only the off-site copies and the journal replica survive.
5. **Recovery, timed from T0:** `fetch` from S3 (checksums verified) →
   `infra/scripts/restore-isolated.sh --start-app` in the new project (stores → verify →
   load into `homayar_restoring` → reconcile → promote → gate → api/worker/scheduler without
   ingress, `/readyz`) → open ingress (`proxy`, web services) → smoke tests through nginx.
   **READY = smoke tests passed.** RTO = READY − T0.
6. **After READY (not timed):** restore-gate probes on a second, database-only staging restore
   of the same set (`homayar_gateprobe`), then that database is dropped.

## 4. Timeline (UTC)

Times from the epoch column of `drill-timeline.log` (millisecond precision; its ISO column
is a `date` call made just before and reads about one second earlier) and from
`restore-timeline.log` (1 s precision).

| Time (UTC) | Event | T − T0 (s) |
| --- | --- | --- |
| 04:47:55.147 | production up — start | −814.9 |
| 04:52:56.332 | production up — end | −513.7 |
| 04:52:56.405 → 04:55:44.290 | seed 4 tenants | |
| 04:55:44.484 | `backup.sh` start | −345.6 |
| 04:56:04.937 | backup set started (`started_at`) | |
| **04:56:05.180** | **snapshot time** (`created_at`, exported snapshot, WAL 0/2C36228) | −324.9 |
| 04:56:33.450 | set complete (`finished_at`; create 28.5 s: database 1.6 s, objects 26.9 s) | |
| 04:59:05.909 | `backup.sh` end (verify, off-site directory copy, prune done) | −144.1 |
| 04:59:06.121 → 05:00:18.534 | replicate to the S3 off-site | |
| 05:01:18.805 → 05:01:24.010 | post-backup changes (tombstones at 05:01:19.4, :20.4, :22.6) | |
| **05:01:24.082** | **failure** (production destroyed) — start | −6.0 |
| 05:01:29.946 | failure — end | −0.1 |
| **05:01:30.055** | **T0: recovery decision** | 0 |
| 05:01:30.219 → 05:01:58.638 | fetch from S3 + checksum verify (28.4 s) | +0.2 → +28.6 |
| 05:01:58.722 | `restore-isolated.sh` start | +28.7 |
| 05:02:00 → 05:02:42 | stores up (postgres, redis, minio, bootstrap) | +30 → +72 |
| 05:02:42 → 05:03:13 | verify (MAC + every checksum) | +72 → +103 |
| 05:03:13 → 05:04:23 | load into `homayar_restoring` (tool: schema 18.6 s, rows 2.6 s, objects 20.2 s, total 49.1 s) | +103 → +173 |
| 05:04:22.325 | staging marker `loaded` (`restore-state.json`) | +172 |
| 05:04:23 → 05:04:39 | reconcile (tool 3.6 s: integrity 1.56 s, tombstones 1.53 s, rest 0.47 s) → **PASS** | +173 → +189 |
| 05:04:39 → 05:05:15 | promote (`homayar_restoring` → `homayar`) + gate open | +189 → +225 |
| 05:05:15 → 05:07:29 | api, worker, scheduler up; `/readyz` OK (**app ready**) | +225 → +359 |
| 05:07:29.575 → 05:07:45.560 | ingress opened (proxy, public-web, console-web) | +359.5 → +375.5 |
| 05:07:45.631 → 05:07:48.127 | smoke tests, 11/11 | +375.6 → +378.1 |
| **05:07:48.199** | **READY** | **+378.1** |
| 05:07:48.271 → 05:09:36.778 | restore-gate probes (not timed) | |

## 5. Measured numbers against the targets

**RTO (target ≤ 8 h = 28,800 s): 378.1 s, met.** Breakdown: fetch 28.4 s, isolated restore
330.8 s (stores 42 s, verify 31 s, load 70 s, reconcile 16 s, promote 36 s, app start to
`/readyz` 134 s), ingress 16.0 s, smoke 2.5 s. The largest single phase is the application
start (health-check start periods), not the data. The measured path starts at the decision;
it does **not** include detecting the failure, provisioning a host, retrieving secrets and the
backup key, pulling images, or DNS/TLS cutover (all were already in place here).

**RPO (target ≤ 24 h).**

- Measured data-loss window = failure − snapshot = 05:01:24.082 − 04:56:05.180 =
  **318.9 s**. The post-backup upload is absent after the restore, as expected.
- Schedule-based worst case: with one backup per 24 h, the surviving set can be as old as one
  interval plus the time from its snapshot until its off-site copy is complete. Measured
  here: off-site directory copy done by 180.7 s after the snapshot (inside `backup.sh`), S3
  copy the restore used done by 253.4 s. **Worst case = 24 h + 4 min 13 s** for this topology, i.e. a strict
  once-per-24 h schedule does not by itself keep the worst case within 24 h. The backup-age
  alert (26 h) bounds detection, not loss. Keeping the target strictly needs a shorter
  interval (e.g. at most 23 h 55 min) or reading the target as "one daily backup" — an owner
  decision (D03).
- Deletions: 0 h, see §7 (holds only while the journal replica is current; here it was copied
  once, right after the changes).

## 6. Restore-gate evidence

- **Staging state before reconcile:** the sealed report records the marker it found at the
  start of reconciliation: `checks.marker.details.state = "loaded"`. The staging database
  was `homayar_restoring`; the application only connects to `homayar`, which at that point
  was the empty database of the new project.
- **Tombstones re-applied before promotion:** `reconcile` ended 05:04:39 with PASS, and
  `restore-isolated.sh` runs `promote` only after `reconcile` exited 0 (promote started in
  the same second). Replay counts from the report (`checks.tombstones.details.replay`):
  `records 3, reapplied 3, already_tombstoned 0, meeting_absent 0, purges_reopened 0,
  purges_completed 3, jobs_cancelled 0, unknown_keys_removed 0, unknown_keys_failed 0,
  rejected_lines 0`; verification after the replay: `alive 0, purges_open 0,
  purges_failed 0, rejected_lines 0`. The journal held 0 records at backup time and 3
  afterwards (`journal_records_at_backup 0`, `journal_records 3`, `stale_journal []`).
- **Promote refused before PASS** (`gate-probes.jsonl`, second staging restore of the same set):

  | Probe | Expected | Observed |
  | --- | --- | --- |
  | `restore … --staging-db homayar_gateprobe --no-objects` | exit 0 | exit 0, state `loaded` |
  | `gate --live-db homayar_gateprobe` | closed | exit 1, `gate: closed`, marker `state: loaded` |
  | `promote … --staging-db homayar_gateprobe` with its own (empty) work dir | refused | exit 1, `report_unreadable` (no reconciliation report) |
  | `promote … --staging-db homayar_gateprobe` with the drill's PASS report | refused | exit 1, `report_mismatch` ("the report belongs to another staging database") |
  | `gate --live-db homayar` | open | exit 0, `gate: open`, backup `20261018T045604Z-594e6854` |

- **Promotion:** the existing empty `homayar` was kept as `homayar_retired_20261018050452`;
  `homayar_restoring` became `homayar` with the `promoted` marker.
- Other reconciliation checks: schema at the code's single head `d8a2f4c6b1e3`; RLS enabled
  and forced with the Homayar policy on 48 policy tables; as the application role, 4 tenants
  checked on 48 tables with no cross-tenant visibility (120 own conversation rows visible
  in total); 46 per-tenant tables and all foreign keys verified at load; `TOKEN_PEPPER`
  fingerprint matches.

## 7. Deletion non-resurrection

The three conversations deleted after the backup (`24e61c94…`, `46e64a72…` of tenant 0,
`1a0b1460…` of tenant 1) are in the backup and were re-deleted by the replay:

- smoke: each answers **404** to its own tenant on the restored system; tenant 0 lists 28
  conversations (30 − 2), tenant 1 lists 29;
- report `non_resurrection`: 3 journaled, `alive []`, `content_rows []`,
  `active_share_links []`, `object_bytes_left []`, nothing held for review;
- report `objects`: 384 keys referenced by restored rows, **372 stored after the replay** —
  the 12 object keys of the three journal records (4 each) are gone; 0 required objects
  missing, 0 orphans, 0 removed again.

## 8. Post-backup state (not replayed, by design)

- Report `post_backup_state` (WARN), counted at reconciliation after the replay: 8 active
  share links, 4 active API keys, 0 pending invitations in the restored database;
  `--revoke-share-links` was not used.
- The share link of tenant 1 revoked after the backup (`a8ce8321…`) is **active again** on the
  restored system (`smoke.json` → `observations.post_backup_revoked_share`: listed,
  `revoked_at` null). This is the documented limit (objectives §6); the runbook's mitigation
  (`--revoke-share-links`, rotating `JWT_SECRET`, owner review) was not exercised in this
  drill.

## 9. Deviations from production and harness fixes

Deviations:

- **Single host.** "Production", the off-site copies and the restore ran on one Docker host
  one after another; the restore project reused no volume of the destroyed one.
- **Off-site = stand-ins on the same host:** a separate MinIO container
  (`homayar-drill-offsite`, own volume) for `s3://`, and a directory on the same disk for the
  `backup.sh` directory copy. Transfer to a real remote host was not measured.
- **S3 replicate and fetch ran from the host** (`python -m backend.backup` in the repository
  virtualenv), not from `backup-tool` through the egress proxy, because `backup.sh` and
  `restore-isolated.sh --fetch` require `EGRESS_PROXY_URL` for `s3://`. The egress-proxy path
  is not covered by this drill.
- **Seeded with the demo seed** (its own process in `ENVIRONMENT=development`; the application
  services ran in production mode). Silent audio, scripted providers.
- **HTTP on loopback, no TLS at nginx.** `SECURE_COOKIES` stayed **true**: production
  settings refuse `false`, so the planned `SECURE_COOKIES=false` deviation was dropped and the
  drill client sends Secure cookies to the loopback origin, as browsers treat it as a secure
  context.
- Journal replication was a single `cp -a` right after the changes, not a continuous replica.
- Images and the backup key were already on the host; `BACKUP_UID` was the host user.
- The manifest records `release_sha`/`version` as `unknown` (the harness does not set
  `HOMAYAR_RELEASE_SHA`/`_VERSION`); the API image reference is recorded.

Harness fixes before the recorded run (commit `75d14af`; runs 1–3 stopped at these, each
from a new drill directory and clean projects): off-site bucket creation ran outside
`Homayar/` and failed silently; `--profile migrate` alone made Compose reject `migrate`'s
dependency on `postgres`; `SECURE_COOKIES=false` was refused by production settings; the demo
seed's fixed job keys collided across tenants in the database-wide unique job and
usage-reservation keys (the API scopes them per workspace; the harness now prefixes them per
tenant). No change to `Homayar/backend/backup`, `backup.sh`, `restore-isolated.sh` or the
Compose file was needed.

## 10. Honest limits

- One run, one data volume: no variance is known. The database is small (4,955 rows), so the
  restore time is dominated by fixed costs (container starts, health checks); object bytes
  (447.5 MB here, 20 s to restore, 28 s to fetch from a local MinIO) and the off-site link will
  dominate at production size. Re-measure, do not extrapolate.
- The RTO excludes detection, decision, host provisioning, secret and key retrieval, image
  pulls and DNS/TLS cutover; T0 was taken immediately after the failure.
- The host was shared and its load varied (1-min average 11–14 in three samples during
  recovery, 134 at the start); the numbers are for this host and moment.
- Not exercised: interrupted backup or restore and `--resume`, a REVIEW_REQUIRED path (legal
  hold), accepted missing objects, key rotation or loss, the egress-proxy route, `JWT_SECRET`
  rotation and `--revoke-share-links`, the hosted-cloud profile.
- Web front ends were CI images of 2026-09-25 and were only health-checked; the smoke tests
  exercise the API through nginx.

## 11. Evidence files

[`2026-10-18-evidence/`](2026-10-18-evidence/): `summary.json` (all measured numbers),
`drill-timeline.log`, `restore-timeline.log`, `reconciliation-report.json` (sealed with the
backup key), `smoke.json`, `manifest.json`, `changes.json`, `gate-probes.jsonl`. No keys,
environment file, passwords or tenant logins are included.
