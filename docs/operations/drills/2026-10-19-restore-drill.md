# Restore drill — 2026-10-19, re-run on main (HOM-118)

Re-run of [`2026-10-18-restore-drill.md`](2026-10-18-restore-drill.md) on current `main`, after
the integrator's review of PR #76. Same harness design (`infra/scripts/dr-drill.sh`), same
4-tenant scope; procedure, gate design and leakage-scan design are described there and are
not repeated here. Objectives: [`disaster-recovery-objectives.md`](../disaster-recovery-objectives.md);
runbook: [`backup-restore.md`](../backup-restore.md). Every number below comes from the
evidence in [`2026-10-19-evidence/`](2026-10-19-evidence/).

**Recorded run:** run 11, 2026-10-19 09:04–09:16 UTC. Product code **`main` `935c41c`** (PR #77
merged; includes the HOM-210 journal/purge changes and the #75 removal of `public-web` that
the 2026-10-18 run did not cover). API image `homayar/api:lane-c-drill-main`
`sha256:df9056f0…`, built from `399268b` = `935c41c` plus harness-only commits (no diff under
`Homayar/`, `infra/compose`, `infra/docker`). Harness of **`b7b4c1a`**.

**Result** (one local Docker host, 4,925 rows / 441 MB; detection, provisioning, secrets, image
pulls and DNS/TLS excluded): recovery usable **172.3 s (2 min 52 s)** after the recovery
decision (target ≤ 8 h). Data-loss window set up by the drill **163.9 s**; the RPO is **not
demonstrated** (§3). Reconciliation **PASS**, smoke **13/13**, restore-gate probes **5/5**,
leakage scan **clean (0 matches in 45 files, no expected log missing)**, cleanup left **0**
containers and volumes. No product or tool bug was found.

## 1. Gates

| Gate | Result | Evidence |
| --- | --- | --- |
| Backup created, deep-verified, off-site copies complete | PASS — `backup.sh` exit 0; object copy `integrity: complete` (376 objects, 0 missing or mismatched at copy) | `manifest.json`, console |
| Freshness gauges | PASS — `postgres`, `objects`, `offsite` at the snapshot time (1792400971.984) | `homayar_backup.prom` |
| Fetch from the S3 off-site with checksum verification | PASS — 19.3 s | `summary.json` |
| Reconciliation | **PASS** (9 × PASS, `post_backup_state` WARN by design); no owner overrides | `reconciliation-report.json` (sealed) |
| Journal identity | PASS — `anchored` (backup fingerprinted 2 records; replica holds 5) | report → `checks.tombstones` |
| Promote only with this staging database's PASS report | PASS — 5/5 probes as expected | `gate-probes.jsonl` |
| Smoke tests through nginx on the restored system | PASS — 13/13 | `smoke.json` |
| No secret or transcript text in diagnostics; expected log set complete | PASS — 0 matches, `missing []` | `leakage-scan.json` |
| Cleanup | PASS — 0 containers, 0 volumes of `homayar-drill-*` afterwards; secret files removed | console |
| RTO ≤ 8 h | **met for the drilled scope** — 172.3 s (one local Docker host, 4,925 rows / 441 MB; detection, provisioning, secrets, image pulls, DNS/TLS excluded) | `summary.json` → `achieved_rto_seconds` |
| RPO ≤ 24 h | **not demonstrated**; 163.9 s is only the gap the drill set up; the schedule-based bound (§3) exceeds the target; D03 open | `summary.json` → `data_loss_window_seconds` |
| Deletions re-applied before use | PASS — 5 journaled deletions (2 before, 3 after the backup) stay deleted | §4 |

## 2. Timeline (UTC)

Drill marks from the epoch column of `drill-timeline.log`, restore phases from
`restore-timeline.log` (1 s precision). Host 1-min load average about 9–12 during the run.

| Time (UTC) | Event | T − T0 (s) |
| --- | --- | --- |
| 09:04:04.889 → 09:06:48.503 | production up (`onprem` profile: api, worker, scheduler, proxy, console-web, stores) | −495.5 → −331.9 |
| 09:06:48.568 → 09:08:45.763 | seed 4 tenants | |
| 09:08:45.848 → 09:09:18.617 | deletions before the backup + purge wait | |
| 09:09:18.677 | `backup.sh` start | −181.7 |
| **09:09:31.984** | **snapshot time** (`created_at`) | −168.4 |
| 09:10:41.526 | `backup.sh` end (deep verify, off-site directory, prune) | −98.8 |
| 09:10:41.636 → 09:11:10.772 | replicate to the S3 off-site (29.1 s) | |
| 09:12:10.889 → 09:12:13.672 | post-backup changes (3 deletions, 1 revocation, 1 upload) | |
| **09:12:15.931** | **failure** (logs saved, production destroyed with volumes) | −4.4 |
| **09:12:20.373** | **T0: recovery decision** | 0 |
| 09:12:20.449 → 09:12:39.709 | fetch from S3 + checksum verify (19.3 s) | +0.1 → +19.3 |
| 09:12:40 → 09:12:55 | stores up | +20 → +35 |
| 09:12:55 → 09:13:06 | verify (MAC + every checksum) | +35 → +46 |
| 09:13:06 → 09:13:50 | load into `homayar_restoring` | +46 → +90 |
| 09:13:50 → 09:14:06 | reconcile (tool 7.1 s) → **PASS** | +90 → +106 |
| 09:14:06 → 09:14:21 | promote + gate open | +106 → +121 |
| 09:14:21 → 09:14:52 | api, worker, scheduler up; `/readyz` OK | +121 → +152 |
| 09:14:52.631 → 09:15:09.250 | ingress opened (proxy, console-web) | +152.3 → +168.9 |
| 09:15:09.322 → 09:15:12.564 | smoke tests, 13/13 | +169.0 → +172.2 |
| **09:15:12.655** | **READY** | **+172.3** |
| 09:15:12.745 → 09:16:17.409 | restore-gate probes (not timed) | |
| 09:16:20.221 → 09:16:20.440 | leakage scan (not timed) | |

## 3. Measured numbers against the targets

**RTO (target ≤ 8 h): 172.3 s, met for the drilled scope.** Fetch 19.3 s, isolated restore
132.8 s (stores 15, verify 11, load 44, reconcile 16, promote 15, app to `/readyz` 31), ingress
16.6 s, smoke 3.2 s. Not included: detecting the failure, provisioning a host, retrieving
secrets and the backup key, pulling images, DNS/TLS cutover.

**RPO (target ≤ 24 h): not demonstrated.**

- Window set up by the drill = failure − snapshot = 09:12:15.931 − 09:09:31.984 = **163.9 s**.
  The post-backup upload is absent after the restore, as expected.
- Schedule-based bound (not measured): one interval plus snapshot-to-off-site time — here
  69.5 s to the off-site directory and 98.8 s to the S3 copy the restore used — i.e. about
  **24 h + 2 min**, which exceeds the 24 h target. After a missed or failed run it is about
  26 h plus repair, once the 26 h backup-age alert exists (Lane C/D contract, #47). Owner
  decision D03 is open.
- Deletions after the backup: re-applied from the journal replica (§4); that window equals the
  replica's sync interval, not measured here.

## 4. Restore gate, deletions and objects

- Staging state before reconcile `loaded`; `reconcile` ended before `promote` started; the
  empty `homayar` database of the new project was dropped on promotion, no retired database
  kept.
- Replay: `records 5, reapplied 3, already_tombstoned 2, purges_reopened 2,
  purges_completed 5, rejected_lines 0`; verification `alive 0, purges_open 0,
  purges_failed 0`. `non_resurrection`: 5 journaled, nothing alive, no content rows, active
  share links or object bytes left.
- Probes: staging restore (db only) exit 0 `loaded`; gate on it `closed`; promote without its
  report `report_unreadable`; promote with the drill's PASS report `report_mismatch`; live
  gate `open`.
- Objects: 376 in the set, 364 stored after the replay (376 − 12 keys of the three
  post-backup deletions), **364 required, 364 re-hashed, 0 mismatches**, 0 missing, 0 orphans;
  384 referenced.
- Schema at head `d8a2f4c6b1e3`; RLS enabled and forced on 48 policy tables; 4 tenants checked
  as the application role on 48 tables with no cross-tenant visibility; 46 per-tenant tables
  and 77 tenant foreign keys verified; `TOKEN_PEPPER` fingerprint matches.
- `post_backup_state` (WARN): 8 active share links, 4 active API keys; the share link revoked
  after the backup is active again (documented limit, objectives §7).

## 5. Leakage scan

45 files, 375,450 B: the Compose logs of every service of both projects (minio,
minio-bootstrap, postgres, redis, api, console-web, proxy, worker, scheduler), the off-site
MinIO log, the console, tool outputs, reports and evidence. Searched: the six env secrets, the
backup key (base64, hex), the off-site secret, the demo password and 43 transcript phrases
(135 patterns). **0 matches.** New in this run: `--expect-logs` checked that the service lists
of both projects were recorded and every listed log exists (`missing []`).

## 6. Harness changes since the 2026-10-18 run

The three defects the integrator found in PR #76 are fixed:

- **(a)** The drill refuses to start when containers, volumes or networks of `homayar-drill-*`
  remain. An EXIT trap removes both projects (by Compose label), the off-site stand-in and the
  0600 `drill.env`/`backup.key`/`offsite.env`, and fails if anything remains (`DRILL_KEEP=1`
  keeps them). Run 10 (same code, RTO 138.6 s, loss window 118.5 s, all gates PASS) is **not
  recorded**: its trap used `compose down`, which could not interpolate the restore project's
  file and left it running; fixed in `b7b4c1a`, then run 11 was recorded.
- **(b)** `wait_healthy` uses `compose ps --all` and fails on a stopped service other than an
  exit-0 one-shot (`minio-bootstrap`, `migrate`).
- **(c)** `collect_logs` fails without a service list and records it; `leak_scan.py
  --expect-logs/--expect-files` reports missing logs and is then not clean.

## 7. Honest limits

As in the 2026-10-18 record §10–11, unchanged: single shared host; off-site stand-ins on the
same host; S3 replicate and fetch from the host, not through the egress proxy; demo seed,
small data volume; RTO excludes detection, provisioning, secrets, image pulls and DNS/TLS.
Two complete runs on this code (runs 10 and 11: 138.6 s and 172.3 s), so variance is only
roughly known. Not exercised: `--revoke-share-links`, `JWT_SECRET` rotation, degraded or
gauge-failure exits, resume, legal hold, the hosted-cloud profile. The console-web image is a
CI build of 2026-09-25, health-checked only. The manifest records `release_sha`/`version` as
unknown (harness does not set them).

## 8. Evidence files

[`2026-10-19-evidence/`](2026-10-19-evidence/) (run 11): `summary.json`, `drill-timeline.log`,
`restore-timeline.log`, `reconciliation-report.json` (sealed), `smoke.json`, `manifest.json`,
`changes.json`, `pre-backup-changes.json`, `gate-probes.jsonl`, `leakage-scan.json`,
`homayar_backup.prom`. No keys, environment files, passwords, tenant logins or raw logs.
