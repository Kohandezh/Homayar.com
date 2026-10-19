# Restore drill — 2026-10-19, re-run on main (HOM-118)

Re-run of [`2026-10-18-restore-drill.md`](2026-10-18-restore-drill.md) on current `main`, after
the integrator's reviews of PRs #76 and #92. Same harness design (`infra/scripts/dr-drill.sh`),
same 4-tenant scope; the procedure, gate design and leakage-scan design are described there
and not repeated here. Objectives: [`disaster-recovery-objectives.md`](../disaster-recovery-objectives.md);
runbook: [`backup-restore.md`](../backup-restore.md). Every number below comes from the
evidence in [`2026-10-19-evidence/`](2026-10-19-evidence/).

**Recorded run:** run 12, 2026-10-19 10:43–10:50 UTC. Product code **`main` `91a760c`**
(includes the HOM-210 journal/purge changes, the #75 removal of `public-web` and the #47
alert rules, none of which the 2026-10-18 run covered). API image
`homayar/api:lane-c-drill-main` `sha256:f621d820…`, built from `4988331` = `91a760c` merged
with this branch's harness-only commits (no diff under `Homayar/`, `infra/compose`,
`infra/docker`). Harness of `4988331` (fixes in `defdec0`).

**Result** (one local Docker host, 4,901 rows / 441 MB; detection, provisioning, secrets, image
pulls and DNS/TLS excluded): recovery usable **116.1 s (1 min 56 s)** after the recovery
decision (target ≤ 8 h). Data-loss window set up by the drill **115.4 s**; the RPO is **not
demonstrated** (§3). Reconciliation **PASS**, smoke **13/13**, restore-gate probes **5/5**,
leakage scan **clean (0 matches in 45 files, no expected log missing or empty)**, cleanup
left **nothing** (§1, `cleanup.log`). No product or tool bug was found.

## 1. Gates

| Gate | Result | Evidence |
| --- | --- | --- |
| Backup created, deep-verified, off-site copies complete | PASS — object copy `integrity: complete` (376 objects, 441,205,368 B) | `manifest.json` |
| Freshness gauges | PASS — `postgres`, `objects`, `offsite` at the snapshot time (1792406751.779) | `homayar_backup.prom` |
| Fetch from the S3 off-site with checksum verification | PASS — 11.8 s | `summary.json` |
| Reconciliation | **PASS** (9 × PASS, `post_backup_state` WARN by design); no owner overrides | `reconciliation-report.json` (sealed) |
| Journal identity | PASS — `anchored` (backup fingerprinted 2 records; replica holds 5) | `summary.json` → `restore_gate` |
| Promote only with this staging database's PASS report | PASS — 5/5 probes as expected | `gate-probes.jsonl` |
| Smoke tests through nginx on the restored system | PASS — 13/13 | `smoke.json` |
| No secret or transcript text in diagnostics; expected log set complete and non-empty | PASS — 0 matches, `missing []` | `leakage-scan.json` |
| Cleanup | PASS — 0 containers, volumes and networks of both projects; off-site container and its named volume `homayar-drill-offsite-data` absent; 0 anonymous volumes created since the run started; secret files removed | `cleanup.log` |
| RTO ≤ 8 h | **met for the drilled scope** — 116.1 s (one local Docker host, 4,901 rows / 441 MB; detection, provisioning, secrets, image pulls, DNS/TLS excluded) | `summary.json` → `achieved_rto_seconds` |
| RPO ≤ 24 h | **not demonstrated**; 115.4 s is only the gap the drill set up; the schedule-based bound (§3) exceeds the target; D03 open | `summary.json` → `data_loss_window_seconds` |
| Deletions re-applied before use | PASS — 5 journaled deletions (2 before, 3 after the backup) stay deleted | §4 |

## 2. Timeline (UTC)

Drill marks from the epoch column of `drill-timeline.log`, restore phases from
`restore-timeline.log` (1 s precision).

| Time (UTC) | Event | T − T0 (s) |
| --- | --- | --- |
| 10:43:20.984 → 10:44:16.109 | production up (`onprem` profile: api, worker, scheduler, proxy, console-web, stores) | −270.0 → −214.9 |
| 10:44:16.152 → 10:45:14.141 | seed 4 tenants | |
| 10:45:14.187 → 10:45:45.529 | deletions before the backup + purge wait | |
| 10:45:45.567 | `backup.sh` start | −125.4 |
| **10:45:51.779** | **snapshot time** (`created_at`) | −119.2 |
| 10:46:21.607 | `backup.sh` end (deep verify, off-site directory, prune) | −89.4 |
| 10:46:21.724 → 10:46:42.762 | replicate to the S3 off-site (21.0 s) | |
| 10:47:42.826 → 10:47:45.266 | post-backup changes (3 deletions, 1 revocation, 1 upload) | |
| **10:47:47.202** | **failure** (logs saved, production destroyed with volumes) | −3.8 |
| **10:47:50.984** | **T0: recovery decision** | 0 |
| 10:47:51.028 → 10:48:02.824 | fetch from S3 + checksum verify (11.8 s) | +0.0 → +11.8 |
| 10:48:03 → 10:48:12 | stores up | +12 → +21 |
| 10:48:12 → 10:48:17 | verify (MAC + every checksum) | +21 → +26 |
| 10:48:17 → 10:48:47 | load into `homayar_restoring` | +26 → +56 |
| 10:48:47 → 10:49:00 | reconcile (tool 6.2 s) → **PASS** | +56 → +69 |
| 10:49:00 → 10:49:12 | promote + gate open | +69 → +81 |
| 10:49:12 → 10:49:32 | api, worker, scheduler up; `/readyz` OK | +81 → +101 |
| 10:49:32.628 → 10:49:45.455 | ingress opened (proxy, console-web) | +101.6 → +114.5 |
| 10:49:45.499 → 10:49:47.029 | smoke tests, 13/13 | +114.5 → +116.0 |
| **10:49:47.073** | **READY** | **+116.1** |
| 10:49:47.116 → 10:50:28.697 | restore-gate probes (not timed) | |
| 10:50:31.068 → 10:50:31.251 | leakage scan (not timed) | |

## 3. Measured numbers against the targets

**RTO (target ≤ 8 h): 116.1 s, met for the drilled scope.** Fetch 11.8 s, isolated restore
89.7 s (stores 9, verify 5, load 30, reconcile 13, promote 12, app to `/readyz` 20), ingress
12.8 s, smoke 1.5 s. Not included: detecting the failure, provisioning a host, retrieving
secrets and the backup key, pulling images, DNS/TLS cutover.

**RPO (target ≤ 24 h): not demonstrated.**

- Window set up by the drill = failure − snapshot = 10:47:47.202 − 10:45:51.779 = **115.4 s**.
  The post-backup upload is absent after the restore, as expected.
- Schedule-based bound (not measured): one interval plus snapshot-to-off-site time — here
  29.8 s to the off-site directory and 51.0 s to the S3 copy the restore used — i.e. about
  **24 h + 1 min**, which exceeds the 24 h target. After a missed or failed run it is about
  26 h plus repair: `HomayarBackupTooOld` fires at 26 h (93,600 s; merged with #47). Owner
  decision D03 is open.
- Deletions after the backup: re-applied from the journal replica (§4); that window equals the
  replica's sync interval, not measured here.

## 4. Restore gate, deletions and objects

- Staging state before reconcile `loaded`; `reconcile` ended before `promote` started.
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

45 files, 349,837 B: the Compose logs of every service of both projects (minio,
minio-bootstrap, postgres, redis, api, console-web, proxy, scheduler, worker), the off-site
MinIO log, the console, tool outputs, reports and evidence. Searched: the six env secrets, the
backup key (base64, hex), the off-site secret, the demo password and 43 transcript phrases
(135 patterns). **0 matches.** `--expect-logs` checked that both service lists were recorded
and every listed log exists and is non-empty; each log came from a `logs` call that
succeeded (the drill stops otherwise).

## 6. Harness changes since the 2026-10-18 run

Defects found by the integrator in #76 and #92, all fixed:

- **Leftovers and cleanup.** The drill refuses to start when containers, volumes or networks of
  either project, the off-site container or its volume remain. The off-site MinIO now uses a
  named volume (`homayar-drill-offsite-data`): its image declares `VOLUME /data`, so with
  `docker rm -f` alone an anonymous volume holding an encrypted backup set survived every
  earlier run. The EXIT trap removes the secret files first, then runs without errexit:
  containers with `rm -fv`, volumes and networks by Compose label, the off-site container and
  volume; it exits non-zero if anything, or a failed lookup, remains (`DRILL_KEEP=1` keeps
  everything). The anonymous off-site volumes of all earlier runs (2026-10-18 and runs 10–11,
  each holding only the `homayar-offsite` bucket) were checked and removed.
- **Health wait.** `wait_healthy` uses `compose ps --all`, fails on a stopped service other
  than an exit-0 one-shot (`minio-bootstrap`, `migrate`), and fails on a service of
  `config --services` that was never created.
- **Log set.** `collect_logs` fails without a service list, records it, and fails when a
  `logs` call fails; `leak_scan.py --expect-logs/--expect-files` reports a missing or empty
  log and is then not clean.

Earlier runs on this branch's way here (runs 10 and 11, before these fixes) are not recorded:
their cleanup left containers or the off-site volume behind.

## 7. Honest limits

As in the 2026-10-18 record §10–11: single shared host; off-site stand-ins on the same host;
S3 replicate and fetch from the host, not through the egress proxy; demo seed, small data
volume; RTO excludes detection, provisioning, secrets, image pulls and DNS/TLS. **One recorded
run on this code: no variance is known.** Not exercised: `--revoke-share-links`, `JWT_SECRET`
rotation, degraded or gauge-failure exits, resume, legal hold, the hosted-cloud profile. The
console-web image is a CI build of 2026-09-25, health-checked only. The manifest records
`release_sha`/`version` as unknown (the harness does not set them). The trap's behaviour with
a failing `docker` was reviewed but not tested here with a shim.

## 8. Evidence files

[`2026-10-19-evidence/`](2026-10-19-evidence/) (run 12): `summary.json`, `drill-timeline.log`,
`restore-timeline.log`, `reconciliation-report.json` (sealed), `smoke.json`, `manifest.json`,
`changes.json`, `pre-backup-changes.json`, `gate-probes.jsonl`, `leakage-scan.json`,
`homayar_backup.prom`, `cleanup.log` (the trap's last line and a Docker check taken after the
run). No keys, environment files, passwords, tenant logins or raw logs.
