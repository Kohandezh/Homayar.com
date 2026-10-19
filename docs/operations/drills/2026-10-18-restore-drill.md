# Restore drill — 2026-10-18 (HOM-118)

Real execution of `infra/scripts/dr-drill.sh` against the on-prem Compose profile: a seeded
"production" project is backed up, changed, destroyed with its volumes, and recovered from
the off-site copy into an isolated project; afterwards every diagnostic the drill produced is
scanned for secrets and transcript text. Objectives:
[`disaster-recovery-objectives.md`](../disaster-recovery-objectives.md); runbook:
[`backup-restore.md`](../backup-restore.md); design: ADR 0035. Every number below comes from
the recorded evidence in [`2026-10-18-evidence/`](2026-10-18-evidence/); nothing is estimated.

**Recorded run:** run 9, 2026-10-18 19:46–20:00 UTC, on code **`b9836ae`** (PR #42 head after
the integrator's review) with the drill harness of **`92f6769`** (same harness content).

**What was drilled, and what was not.** The drill ran on `b9836ae`, **not on current `main`**.
Main has since changed `tombstone_journal.py`, `retention_purge.py`,
`checkpoints.discover_workspaces` (HOM-210 rotation), `conversation_purge_service.py` and
`demo_seed.py`; none of those changes was drilled. PR #75 (`4cdead9`) also removed `public-web`
from `compose.onprem.yaml`, from nginx `depends_on` and from `dr-drill.sh`: the measured setup
(ingress 15.2 s, the procedure in §3 and the scan list in §8) still includes `public-web`, so it
differs from the topology now shipped. A re-run on main is needed before these numbers stand
for main.

**Result** (one local Docker host, 4,901 rows / 441 MB; detection, provisioning, secrets,
image pulls and DNS/TLS excluded): recovery usable **237.1 s (3 min 57 s)** after the recovery
decision (target ≤ 8 h). Measured data-loss window **181.4 s (3 min 01 s)**. Reconciliation **PASS**, smoke
tests **13/13**, all 5 restore-gate probes as expected, diagnostics leakage scan **clean
(0 matches in 45 files)**. No product or tool bug was found; the harness needed fixes (§10).

## 1. Gates

| Gate | Result | Evidence |
| --- | --- | --- |
| Backup created, deep-verified, off-site copies complete | PASS — `backup.sh` exit 0 (not 3 = degraded, not 4 = gauge); object copy `integrity: complete` | `manifest.json` → `stores.objects`; console |
| Freshness gauges written | PASS — `postgres`, `objects` and `offsite` all at the snapshot time (1792353012.944) | `homayar_backup.prom` |
| Fetch from the S3 off-site with checksum verification | PASS — 19.6 s | `summary.json` → `recovery.fetch_seconds` |
| Reconciliation | **PASS** (9 × PASS, `post_backup_state` WARN, non-blocking by design); no owner overrides | `reconciliation-report.json` (sealed) |
| Journal identity | PASS — `anchored`: the replica contains the journal file the backup fingerprinted | report → `checks.tombstones.details.journal_identity` |
| Promote only with a PASS report for this staging database | PASS — 5/5 probes as expected | `gate-probes.jsonl` |
| Smoke tests through nginx on the restored system | PASS — 13/13 | `smoke.json` |
| No secret or transcript text in diagnostics | PASS — 0 matches in every category | `leakage-scan.json` |
| RTO ≤ 8 h | **met for the drilled scope** — 237.1 s (one local Docker host, 4,901 rows / 441 MB; detection, provisioning, secrets, image pulls, DNS/TLS excluded) | `summary.json` → `achieved_rto_seconds` |
| RPO ≤ 24 h | **not demonstrated**; the 181.4 s window is only the gap the drill set up; the schedule-based bound (§5) exceeds the target; D03 open | `summary.json` → `data_loss_window_seconds` |
| Deletions re-applied before use | PASS — 5 journaled deletions (2 before, 3 after the backup) stay deleted | §7 |

## 2. Environment

| Item | Value |
| --- | --- |
| Host | MacBook Pro, Intel Core i9-8950HK (6 cores / 12 threads), 32 GB RAM, macOS 15.8.2 |
| Container runtime | Docker Desktop, Engine 29.5.3, Compose v5.1.4; Linux VM with 12 vCPU and 7.75 GiB RAM |
| Host load | Shared with other sessions. 1-min load average 4.3 at the start (19:46Z), 11.8 during the backup (19:51Z), 26.6 at the end (20:00Z); no sample inside the recovery window. Earlier attempts ran at 150–740 (§10). |
| Code | `b9836ae` (branch `claude/lane-c-backup-restore-recovery-86b88f`, PR #42). API image `homayar/api:lane-c-drill` `sha256:9adeba87…`, built 2026-10-18 18:42Z from this branch, whose product files are identical to `b9836ae`. Harness `92f6769`. |
| Web images | `homayar-ci/public-web:local` (`sha256:4a3036d1…`, 2026-09-25), `homayar-ci/console-web:local` (`sha256:9a541fb0…`, 2026-09-25): CI builds, not of this commit; health-checked only |
| Stores | `postgres:17-alpine` (17.11), `redis:7.4-alpine`, `nginx:1.28-alpine`, `quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z`, `quay.io/minio/mc:RELEASE.2025-04-16T18-13-26Z`, all local |
| Application mode | `APP_ENVIRONMENT=production`, `DEPLOYMENT_MODE=onprem`, `SECURE_COOKIES=true`, internal-hop TLS opt-outs as shipped (HOM-196) |
| Data volume | 4 tenants, 120 conversations seeded (30 per tenant; 2 deleted before the backup), 61 tables, 4,901 rows, 376 objects = 441,205,368 B of object bytes; encrypted set 443,687,587 B |

Data comes from the demo seed (`backend.database.demo_seed`, once per tenant via
`infra/scripts/drill/seed_tenants.py`): verified accounts per role, meetings processed by the
real job processor with scripted providers, share links, API keys, audit history; recordings
are 20–59 s of 48 kHz mono silence; no legal holds.

## 3. Procedure

`DRILL_DIR=<new scratch dir> API_IMAGE=homayar/api:lane-c-drill DRILL_TENANTS=4
PUBLIC_WEB_IMAGE=homayar-ci/public-web:local CONSOLE_WEB_IMAGE=homayar-ci/console-web:local
infra/scripts/dr-drill.sh` (projects `homayar-drill-prod` / `homayar-drill-restore`, off-site
container `homayar-drill-offsite`, ports 127.0.0.1:18080/18081/19000):

1. **Production:** stores, `minio-bootstrap`, `migrate`, full `onprem` profile; seed 4
   tenants; delete one conversation of tenants 2 and 3 through the API (so the backup
   fingerprints a non-empty tombstone journal), 30 s for their purge jobs.
2. **Backup:** `infra/scripts/backup.sh` (textfile check → create → deep verify → replicate to
   the marked off-site directory → prune local and off-site), then `replicate` to
   `s3://homayar-offsite/site-a`.
3. **After the backup** (60 s gap), through nginx as a browser would: delete two
   conversations of tenant 0 and one of tenant 1, revoke one share link of tenant 1, upload
   one recording to tenant 0. The tombstone journal is then copied to its replica.
4. **Failure:** every service log of the production project is saved, then
   `down --volumes --remove-orphans`, and its journal, backup root and textfile directory are
   removed. Only the off-site copies and the journal replica survive.
5. **Recovery, timed from T0:** `fetch` from S3 → `infra/scripts/restore-isolated.sh
   --start-app` in the new project (stores → verify → load into `homayar_restoring` →
   reconcile → promote → gate → api/worker/scheduler without ingress, `/readyz`) → open ingress
   → smoke tests through nginx. **READY = smoke tests passed.** RTO = READY − T0.
6. **After READY (not timed):** restore-gate probes on a second, database-only staging restore
   of the same set (`homayar_probe_restoring`), then that database is dropped.
7. **After READY (not timed):** diagnostics leakage scan (§8).

## 4. Timeline (UTC)

Drill marks from the epoch column of `drill-timeline.log` (millisecond precision; its ISO
column is a `date` call made just before and reads up to a second earlier), restore phases
from `restore-timeline.log` (1 s precision).

| Time (UTC) | Event | T − T0 (s) |
| --- | --- | --- |
| 19:46:05.581 → 19:47:27.551 | production up | −433.4 → −351.5 |
| 19:47:27.630 → 19:49:24.483 | seed 4 tenants | |
| 19:49:24.596 → 19:49:57.551 | deletions before the backup (tombstones 19:49:25–:27) + purge wait | |
| 19:49:57.606 | `backup.sh` start | −201.4 |
| 19:50:12.873 | set started (`started_at`) | |
| **19:50:12.944** | **snapshot time** (`created_at`) | −186.1 |
| 19:50:25.245 | set complete (create 12.4 s: database 0.7 s, objects 11.6 s) | |
| 19:51:14.035 | `backup.sh` end (deep verify, off-site directory copy, prune done) | −125.0 |
| 19:51:14.195 → 19:52:08.754 | replicate to the S3 off-site (54.6 s) | |
| 19:53:08.843 → 19:53:12.033 | post-backup changes | |
| **19:53:14.347** | **failure** (logs saved, production destroyed) — start | −4.7 |
| 19:53:18.961 | failure — end | −0.1 |
| **19:53:19.022** | **T0: recovery decision** | 0 |
| 19:53:19.079 → 19:53:38.667 | fetch from S3 + checksum verify (19.6 s) | +0.1 → +19.6 |
| 19:53:39 → 19:53:55 | stores up (postgres, redis, minio, bootstrap) | +20 → +36 |
| 19:53:55 → 19:54:10 | verify (MAC + every checksum) | +36 → +51 |
| 19:54:10 → 19:55:20 | load into `homayar_restoring` (schema, rows, objects) | +51 → +121 |
| 19:55:20 → 19:55:49 | reconcile (tool 12.8 s: integrity 1.95 s, tombstones 1.75 s, objects re-hash and rest 9.04 s) → **PASS** | +121 → +150 |
| 19:55:49 → 19:56:24 | promote (`homayar_restoring` → `homayar`) + gate open | +150 → +185 |
| 19:56:24 → 19:56:57 | api, worker, scheduler up; `/readyz` OK (**app ready**) | +185 → +218 |
| 19:56:57.263 → 19:57:12.507 | ingress opened (proxy, public-web, console-web; `public-web` no longer shipped, #75) | +218.2 → +233.5 |
| 19:57:12.576 → 19:57:15.992 | smoke tests, 13/13 | +233.6 → +237.0 |
| **19:57:16.088** | **READY** | **+237.1** |
| 19:57:16.176 → 20:00:00.464 | restore-gate probes (not timed) | |
| 20:00:06.765 → 20:00:07.366 | leakage scan (not timed) | |

## 5. Measured numbers against the targets

**RTO (target ≤ 8 h = 28,800 s): 237.1 s, met.** Breakdown: fetch 19.6 s, isolated restore
198.5 s (stores 16 s, verify 15 s, load 70 s, reconcile 29 s, promote 35 s, app start to
`/readyz` 33 s), ingress 15.2 s, smoke 3.4 s. The measured path starts at the decision; it
does **not** include detecting the failure, provisioning a host, retrieving secrets and the
backup key, pulling images, or DNS/TLS cutover (all were already in place).

**RPO (target ≤ 24 h).**

- Measured data-loss window = failure − snapshot = 19:53:14.347 − 19:50:12.944 = **181.4 s**.
  The post-backup upload is absent after the restore, as expected.
- Schedule-based bound (not measured): with one successful backup every 24 h, the surviving
  set is at most one interval plus the time from its snapshot until its off-site copy is
  complete — here 61.1 s to the off-site directory (end of `backup.sh`) and 115.8 s to the S3
  copy the restore used, i.e. about **24 h + 2 min**. A missed or failed run is noticed by the
  26 h backup-age alert once it exists (Lane C/D contract; not merged until #47), so the bound
  then becomes about 26 h plus repair (objectives §2). The `rpo_note` in the recorded
  `summary.json` ("interval plus one backup duration") is superseded by this paragraph; the
  evidence file is kept as generated and `summarize.py` is corrected.
  Keeping 24 h strictly needs a shorter interval or alert (owner decision D03).
- Deletions after the backup: re-applied from the journal replica (§7); the window equals the
  replica's sync interval, which this drill did not measure (one copy right after the
  changes).

## 6. Restore-gate evidence

- **Staging state before reconcile:** the sealed report records the marker it found:
  `checks.marker.details.state = "loaded"`; staging was `homayar_restoring`, the application
  connects only to `homayar`, the empty database the new project's PostgreSQL created.
- **Tombstones re-applied before promotion:** `reconcile` ended 19:55:49 with PASS;
  `restore-isolated.sh` runs `promote` only after `reconcile` exited 0. Journal identity
  `anchored`: the backup fingerprinted 1 journal file with 2 records, the replica holds that
  file with 5 records (`stale_or_foreign_journal []`). Replay counts
  (`checks.tombstones.details.replay`): `records 5, reapplied 3, already_tombstoned 2,
  meeting_absent 0, purges_reopened 2, purges_completed 5, jobs_cancelled 0,
  unknown_keys_removed 0, unknown_keys_failed 0, rejected_lines 0`; verification after the
  replay: `alive 0, purges_open 0, purges_failed 0, rejected_lines 0`.
- **Promote refused before PASS** (`gate-probes.jsonl`, second staging restore of the same set):

  | Probe | Expected | Observed |
  | --- | --- | --- |
  | `restore … --staging-db homayar_probe_restoring --no-objects` | exit 0 | exit 0, state `loaded` |
  | `gate --live-db homayar_probe_restoring` | closed | exit 1, `gate: closed`, marker `state: loaded` |
  | `promote … --staging-db homayar_probe_restoring` with its own (empty) work dir | refused | exit 1, `report_unreadable` |
  | `promote … --staging-db homayar_probe_restoring` with the drill's PASS report | refused | exit 1, `report_mismatch` ("the report belongs to another staging database") |
  | `gate --live-db homayar` | open | exit 0, `gate: open`, backup `20261018T195012Z-252a044c` |

- **Promotion:** the empty `homayar` was dropped (`dropped_empty_database`), no retired
  database was kept; `homayar_restoring` became `homayar` with the `promoted` marker.
- Other checks: schema at the code's single head `d8a2f4c6b1e3`; RLS enabled and forced with
  the Homayar policy on 48 policy tables; as the application role, 4 tenants checked on 48
  tables with no cross-tenant visibility (120 own conversation rows visible in total); 46
  per-tenant tables, all foreign keys and 77 tenant foreign keys verified; `TOKEN_PEPPER`
  fingerprint matches.

## 7. Deletions and objects

- **Before the backup** (`pre-backup-changes.json`): `7dc28694…` (tenant 2) and `ad5c5a6a…`
  (tenant 3) — in the backup as tombstoned rows; replayed as `already_tombstoned`, their
  purges reopened and completed.
- **After the backup** (`changes.json`): `c006b031…`, `7a1d6efd…` (tenant 0), `90407d22…`
  (tenant 1) — alive in the backup, re-deleted by the replay.
- Smoke: all five answer **404** to their own tenant; tenants list 28 / 29 / 29 / 29
  conversations.
- Report `non_resurrection`: 5 journaled, `alive []`, `content_rows []`,
  `active_share_links []`, `object_bytes_left []`, nothing held.
- Report `objects`: 376 objects in the set; after the replay 364 stored = 376 − 12 (the 4 object
  keys of each of the three post-backup deletions); 364 required, **364 re-hashed against the
  rows' SHA-256 with 0 mismatches**, 0 missing, 0 orphans, 0 removed again; 384 keys
  referenced by rows (the 364 required plus 20 of the five tombstoned conversations).

## 8. Diagnostics leakage scan

`infra/scripts/drill/leak_scan.py` searched every diagnostic of the run for each secret the
drill generated and for the seeded transcript text; the result lists file and category only
([`leakage-scan.json`](2026-10-18-evidence/leakage-scan.json)).

- **Scanned (45 files, 381,576 B):** the Compose logs of every service of both projects
  (production saved before it was destroyed, restore after READY: api, worker, scheduler,
  proxy, public-web, console-web, postgres, redis, minio, minio-bootstrap), the off-site MinIO
  log, the drill's whole console output (which also carries every one-shot container:
  migrate, seed, each `backup-tool` step of `backup.sh` and `restore-isolated.sh`), the
  backup/restore/fetch/replicate outputs, `restore-state.json`, the reconciliation report, the
  timelines, the manifest, smoke results, gate probes and the restore's textfile.
- **Searched for:** `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `JWT_SECRET`, `TOKEN_PEPPER`,
  `MINIO_ROOT_PASSWORD`, `STORAGE_SECRET_KEY` (values of the drill's env file), the backup
  key (base64 and hex), the off-site MinIO secret, the demo password, and 43 transcript
  phrases of `demo_seed.SCRIPTS` (every turn, summary, action and decision ≥ 12 characters,
  whole and as a 20-character prefix, raw and JSON-escaped: 124 patterns).
- **Result: 0 matches in every category.** Excluded as the sources themselves: the env file,
  the backup key file, the off-site env file and `tenants.json` (demo logins). The scanner was
  checked beforehand on synthetic files, where it found every planted secret and phrase
  (raw, hex, JSON-escaped in either case) and nothing in a clean file.
- The off-site MinIO secret reaches its container through an env file, not `docker run -e`
  (no secret on any command line of the harness).

## 9. Post-backup state (not replayed, by design)

- Report `post_backup_state` (WARN), counted at reconciliation after the replay: 8 active
  share links, 4 active API keys, 0 pending invitations; `--revoke-share-links` was not used.
- The share link of tenant 1 revoked after the backup (`80d030d4…`) is **active again** on the
  restored system (`smoke.json` → `observations`). This is the documented limit
  (objectives §6); its mitigations (`--revoke-share-links`, `JWT_SECRET` rotation, owner
  review) were not exercised.

## 10. Deviations from production, attempts and harness fixes

Deviations:

- **Single host.** Production, off-site copies and restore ran on one Docker host one after
  another; the restore project reused no volume of the destroyed one.
- **Off-site = stand-ins on the same host:** a separate MinIO container (own volume) for
  `s3://`, and a directory on the same disk with the `.homayar-offsite` marker (`backup.sh`
  warned that it shares the backup root's filesystem). Transfer to a real remote host was not
  measured.
- **S3 replicate and fetch ran from the host** (`python -m backend.backup` in the repository
  virtualenv), not from `backup-tool` through the egress proxy, which `backup.sh` and
  `restore-isolated.sh --fetch` require for `s3://`. That route is not covered.
- **Seeded with the demo seed** (its process in `ENVIRONMENT=development`; the services in
  production mode). Silent audio, scripted providers.
- **HTTP on loopback, no TLS at nginx;** `SECURE_COOKIES=true` (production refuses `false`);
  the drill client sends Secure cookies to the loopback origin, as browsers do.
- Journal replication was a single `cp -a` right after the changes; images and the backup key
  were already on the host; the manifest records `release_sha`/`version` as `unknown` (the
  harness does not set `HOMAYAR_RELEASE_SHA`/`_VERSION`).

Attempts (each from a new drill directory and clean projects): runs 1–3 stopped at harness
bugs; **run 4** completed on `5b6d07b` (before PR #42's review fixes, without pre-backup
deletions or leakage scan): RTO 378.1 s, loss window 318.9 s, reconciliation PASS, smoke 11/11
([`run-4-summary.json`](2026-10-18-evidence/run-4-summary.json)), recorded for reference only.
Runs 5–8 never reached T0: public-web (run 5) and the worker (runs 7, 8) missed their health
windows at host load of roughly 100–570, and in run 6 an application log line on stdout corrupted the seed
result. Run 9 is the recorded run.

Harness fixes (`infra/scripts/dr-drill.sh`, `infra/scripts/drill/*`; no product file changed):
off-site bucket creation ran outside `Homayar/` and failed silently; `--profile migrate` alone
made Compose reject `migrate`'s dependency on `postgres`; `SECURE_COOKIES=false` was refused;
the demo seed's fixed job keys collided across tenants (now prefixed per tenant); the seed's
result shared stdout with application logs; the MinIO secret was on `docker run` argv; SC2155;
adaptation to PR #42 (off-site marker, `NODE_EXPORTER_TEXTFILE_DIR` outside the backup root
and set for every `backup-tool` run, staging names with `restoring`, pre-backup tombstones so
the journal check runs anchored); a bounded health wait before T0 instead of `up --wait`
alone.

## 11. Honest limits

- One recorded run on the final code and one earlier full run on older code: no variance is
  known. The database is small (4,901 rows), so restore time is dominated by fixed costs
  (container starts, health checks); object bytes (441 MB here, fetched in 20 s from a local
  MinIO) and the off-site link dominate at production size. Re-measure, do not extrapolate.
- The RTO excludes detection, decision, host provisioning, secret and key retrieval, image
  pulls and DNS/TLS cutover; T0 was taken right after the failure.
- **Observation for the product:** on this host at load 100–300 the worker's health check
  (`celery inspect ping`, 10 s timeout) repeatedly timed out during start-up (runs 7–8, before
  T0). In run 9 the restore's app step passed in 33 s; if it fails during a real recovery,
  `restore-isolated.sh --start-app` stops after promotion and the operator has to start the
  application again by hand. Not changed here.
- Not exercised: interrupted backup or restore and `--resume`, degraded sets (exit 3), gauge
  failure (exit 4), a REVIEW_REQUIRED path (legal hold), key rotation or loss, the egress-proxy
  route, `JWT_SECRET` rotation and `--revoke-share-links`, `drop-retired`, the hosted-cloud
  profile.
- The leakage scan covers the diagnostics listed in §8 with exact (and prefix) matching; it
  does not prove absence of paraphrased or partially redacted content, and it does not look at
  data stores (database, MinIO, encrypted sets), which hold customer content by design.
- Web front ends were CI images of 2026-09-25 and were only health-checked; `public-web` has
  since been removed from the on-prem profile (#75).
- Known harness defects, left for a follow-up ticket (not this PR): (a) no `trap EXIT/ERR`
  cleanup, so a failed run leaves the drill projects, the off-site MinIO and the 0600
  `drill.env`/`backup.key`/`offsite.env`, and leftover volumes are silently reused;
  (b) `wait_healthy` runs `compose ps` without `--all`, so exited services count as healthy;
  (c) a failing `config --services` collects no logs and the leak scan still passes, because
  `leak_scan.py` does not assert the expected log set.

## 12. Evidence files

[`2026-10-18-evidence/`](2026-10-18-evidence/) (run 9 unless noted): `summary.json`,
`drill-timeline.log`, `restore-timeline.log`, `reconciliation-report.json` (sealed with the
backup key), `smoke.json`, `manifest.json`, `changes.json`, `pre-backup-changes.json`,
`gate-probes.jsonl`, `leakage-scan.json`, `homayar_backup.prom` (production freshness gauges), and
`run-4-summary.json` (run 4, code `5b6d07b`). No
keys, environment files, passwords, tenant logins or raw logs are included.
