# Homayar infrastructure freeze: handover report

2026-10-19 · Integrator (Lanes C and D)

**Status: INFRASTRUCTURE FROZEN: NO. NOT PRODUCTION READY.**

All Lane C (backup, restore, recovery) and Lane D (audit, observability, release) work is merged into `main`. The freeze gate itself has not been run yet; see section 5.

## 1. Merged pull requests

| PR | Scope | Linear |
|---|---|---|
| #42 | Backup, restore and recovery tool: encrypted sets, isolated restore, reconcile and promote gate | HOM-153, HOM-116, HOM-117: Done |
| #60 | Sweeps visit every workspace in rotating order (integrator fix) | HOM-210: Done |
| #67 | The tombstone journal logs a bounded reason code, never the exception text | — |
| #77 | Journal identity evidence, verified off-site copies, held checkpoints, S3 prune and versioning | HOM-212, HOM-213, HOM-214: Done |
| #93 | S3 follow-ups: reported backfill rejections, best-effort backfill, 12-month Object Lock on monthly sets (D15) | — |
| #76, #92 | Restore drill measured end to end; re-run on main with harness fixes | HOM-118: Done |
| #78 | Secret-scan allowlist for the backup key fingerprint in drill evidence | — |
| #43 | Correlation IDs, Prometheus metrics, sweep heartbeats | HOM-113: Done |
| #47, #95 | Prometheus alert rules with promtool tests and a runbook; follow-ups | HOM-126: Done |
| #50 | Reproducible build identity and release manifest (an integrity checksum, not signed) | HOM-28, HOM-29: Done |
| #55 | Audit event catalogue, login, logout and refresh-replay events, write-time PII masking, security alerts | HOM-114: Done |
| #66 | Log privacy: route templates in the access log, class-only exception summaries, Celery logging; fixed a worker logging-recursion bug | — |
| #69 | `audit_events` made append-only; NOLOGIN reader role (migration `e6c3a9f1d4b8`) | HOM-115a |
| #70 | Protected audit sink: export, verify, erasure records, legacy-review gate | HOM-115b |

Every merge went through these steps:
1. diff inspection;
2. independent review agents, usually two or three rounds;
3. focused local tests;
4. a migration check (upgrade, downgrade, upgrade and `alembic check` on PG16 and PG17);
5. a security and privacy review;
6. green CI, except #70 below.

There were no force-pushes, no skipped tests and no RLS weakening.

## 2. Measured evidence

- **Restore drill, run 12 on main.** RTO was 116 s for the drilled scope: one local Docker host, 4,901 rows and 441 MB. Detection, provisioning, secrets and DNS were excluded.
  - Reconciliation passed.
  - Smoke tests passed: 13 of 13.
  - Promote-gate probes passed: 5 of 5.
  - The diagnostics leakage scan was clean.
- **The RPO of 24 h is NOT demonstrated.** On schedule, the bound is about 24 h + 2 min. Decision D03 is still open.
- **Backend suite on CI:** about 3,400 tests pass.

## 3. Owner decisions recorded

- Backup: RPO 24 h, RTO 8 h, retention of 30 daily and 12 monthly sets, at least one encrypted off-site copy.
- Audit, option B: append-only, with masking at write time.
- Merging on local evidence is allowed when CI is unavailable.
- Approved fingerprint re-baselines in `auth.py`: `login`, `refresh`, `logout` and `otp_request`.
- If the audit write for a successful login fails, the login is refused (fail-closed). Failed-login audit is best-effort.
- Audit rows written before HOM-114 must be reviewed before the first export (the `--legacy-review` gate).
- D15: off-site S3 Object Lock in governance mode. The backup credential never gets the bypass.

## 4. Open items and known gaps

1. **#70 was merged without CI validation.** Its backend CI job was still running at merge time. Sina was asked on #70 to check main's CI and to fix any failure in a follow-up PR without skipping tests.
2. **Low items from the #70 review:**
   - `verify` does not check the export watermark against the last segment.
   - Deeper downgrades re-apply append-only (documented).
3. **Masking limitation (documented, fail-closed):** about 0.16% of UUIDs written inside free-text audit reasons are partly masked.
4. **Release:** `verify` skips `installed_set_sha256`; uv is installed by version, not hash; `PYTHON_IMAGE` in compose is a tag; the build is not bit-for-bit reproducible.
5. **Drill harness:** `wait_healthy` passes silently when the `config --services` call itself fails.
6. **Linear:**
   - The workspace is at its free plan's issue limit, so new tickets can't be created; follow-ups are recorded as comments.
   - Some issues were moved to the "Homayar Archive" team. For example, HOM-113 is now ARC-91 and HOM-118 is ARC-95.
7. **Local only:** `test_admin_legal_holds_api` fails on macOS (no `time.tzset`), and the golden-module 30 s timing test is sensitive to host load. Neither fails on CI runners.

## 5. Next steps, in order

1. Check that main's CI is green after #70.
2. Run the combined integration gate on main (migrations, the full backend suite, the container and compose checks, secrets).
3. Deployment security: HOM-33, then HOM-34, then HOM-35, then HOM-196.
4. Infrastructure freeze gate (17 criteria) and the final report.
5. Only after the freeze: product work (speech and text, call analysis, AI). Estimates were given earlier.

## 6. Sessions

- All Lane C sessions and the Lane D side sessions are deleted.
- No work is left unpushed: every lane branch is merged or 0 commits ahead of main.
- Lane D (lead) and the integrator session can be closed.
