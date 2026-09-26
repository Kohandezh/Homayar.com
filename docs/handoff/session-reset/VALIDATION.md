# Validation record (closeout, 2026-09-26)

Tested source revisions are named per row. `2a30213` is the final PR #3 head; `main` `edfcb78` is its
merge and has the **same tree** (`b57b3520`), so results on `2a30213` apply to `main` as merged.
The handover commit that adds this file changes only documentation and Graphify records.

## 1. Remote CI (`.github/workflows/ci.yml`, GitHub-hosted `ubuntu-latest`)

| Run | Revision | Result |
| --- | --- | --- |
| [36234124732](https://github.com/Kohandezh/Homayar.com/actions/runs/36234124732) | PR #3 `2a30213` (final) | **all six jobs passed**: source contract · secret scan (Gitleaks, full history) · public web lint/build · console lint/build/tests · backend migrations + tests (**667 passed, 81 skipped, 0 failed**) · container configuration |
| [36234814693](https://github.com/Kohandezh/Homayar.com/actions/runs/36234814693) | `main` `edfcb78` (merge of PR #3) | see the handover pull request for the final result of this run |
| [36218268005](https://github.com/Kohandezh/Homayar.com/actions/runs/36218268005) | PR #3 `1d03edd` | backend failed: `test_killed_pool_child_requeues_inertly_and_recovery_settles_once` (1 failed, 666 passed, 81 skipped); five other jobs passed |
| [36210838431](https://github.com/Kohandezh/Homayar.com/actions/runs/36210838431) | `main` `a1c3d60` (PR #2 merge) | backend failed on the same drill (1 failed, 665 passed, 81 skipped) |
| [36128541895](https://github.com/Kohandezh/Homayar.com/actions/runs/36128541895) | PR #2 `b68803d` | all six passed after one re-run of the backend job (a Redis transport drill timed out once; which drill was not recorded) |

**CI skip breakdown (81, derived from markers and confirmed by count):** 35 `ffmpeg` (the runner
has no ffmpeg — a CI gap, `REMAINING_WORK.md` R-02) · 41 `live_s3` (opt-in; no bucket) · 3 Padyar AI
Gateway (contract missing) · 1 SQLite-only restore drill (inapplicable on PostgreSQL) · 1
broker-restart drill (needs `TEST_REDIS_CONTAINER`).

**What the backend job exercises:** PostgreSQL 17 service; `check-migrations.sh` (single head,
upgrade, no drift); the application engine runs as the non-bypass role `homayar_app` through
`SET ROLE` (the test login only seeds/inspects); Redis 7.4 service with real Celery prefork workers
and beat processes for the transport drills; mock providers (`ALLOW_MOCK_PROVIDERS=true`) for
ASR/analysis.

**What the container job exercises:** actionlint; Compose on-prem, on-prem + egress proxy, cloud,
both with Neo4j; Neo4j health smoke; buildx checks; four image builds; hardened runtime smoke
tests; network-level egress denial and proxy allowlist test; air-gap gate of the model preflight;
nginx (reverse proxy and console); ShellCheck; Hadolint.

**Public web job:** lint and build only. The cPanel export and the Playwright browser tests are
reported as missing from the repository (they exist only in the original checkout, R-03).

**R-02 on the GitHub runner (PR #5):**

| Run | Revision | Result |
| --- | --- | --- |
| [36250360273](https://github.com/Kohandezh/Homayar.com/actions/runs/36250360273) | `80ae521` | ffmpeg/ffprobe `6.1.1-3ubuntu5` installed; media tests executed; backend **4 failed**, 698 passed, 46 skipped; other five jobs passed |
| [36252829293](https://github.com/Kohandezh/Homayar.com/actions/runs/36252829293) | `88a77c2` | all six jobs passed (fix below, first version: stderr piped) |

**Finding (security-relevant):** on ffmpeg 6.1.1 (Ubuntu 24.04), the bounded decode
`ffmpeg -xerror … -f null -` exits 0 for `corrupt.mp3`, `corrupt.flac` and `tail.mp3`: the
decoder conceals the errors despite `-xerror`. ffmpeg 5.1.9 (Debian) and 8.1.2 (macOS) exit
non-zero. So upload validation on an Ubuntu 24.04 host would have accepted corrupted media that
local runs refused. Fix: the decode also fails when ffmpeg writes any error-level diagnostic
(`-v error -nostats`); stderr goes to an anonymous temporary file whose size alone is checked (no
diagnostic read into memory, stored, logged or returned; volume bounded by the `-t` window and the
timeout); timeout/unavailable classification unchanged. Tests: the unit test pins the temp-file
stderr and `-nostats`; a new unit test covers exit 0 with a diagnostic (refused, message not
leaked); all valid fixtures still pass and the corruption cases are refused with real binaries.
The first CI run is the evidence that the media tests really execute on the runner.

## 2. Transport drill fix (`2a30213`)

- **Failure:** `test_killed_pool_child_requeues_inertly_and_recovery_settles_once` — the requeued
  copy of a killed pool child's delivery reported `lease_expired` instead of `already_running`.
- **Reproduced:** 3/3 failures in a Linux replica of the CI backend job (below). Cause: on Linux
  Celery's prefork parent notices a SIGKILLed child through its pool-maintenance timer
  (`AsynPool.timers`, every 5 s); with `acks_late` + `reject_on_worker_lost` the delivery is
  requeued about 4.4–5 s after the kill, after the drill's 4 s lease (renewed every 2 s) had
  lapsed. On macOS the child's sentinel fires at once, which is why the drill had passed locally.
- **Fix (test timing only):** this drill's worker runs with a 12 s lease renewed every 6 s, and the
  kill waits until at least 7 s of lease remain (`POOL_MAINTENANCE_SECONDS + 2`). The other drills
  keep the 4 s lease.
- **Guarantees kept (assertions unchanged):** the duplicate delivery is inert while the lease is
  valid (`already_running`, attempt 1, lease generation 1, queue drained); recovery fences the
  expired attempt (sweep `recovered: 1`, stage run `(1, asr, failed, lease_expired)`, generation 3);
  exactly one transcript row and one usage settlement, no invariant violations
  (`_completed_once`); three deliveries, two results. Stale-writer fencing and single settlement
  are also covered by the unchanged `test_stale_claim_is_rejected_at_every_protected_write`,
  `test_stale_worker_resuming_after_recovery_cannot_overwrite_the_new_attempt`,
  `test_stale_worker_cannot_publish_its_artifact_or_checkpoint`,
  `test_worker_killed_mid_stage_is_recovered_and_the_late_writer_is_fenced`,
  `test_database_refuses_a_second_settlement_of_the_same_work` and
  `test_concurrent_settlement_of_one_reservation_commits_once` — all passed in the runs below.
- **Evidence after the fix:** drill 3/3 in the Linux replica; full backend suite in the replica
  (migrations, then pytest): 667 passed, 81 skipped, 0 failed, exit 0; CI run 36234124732:
  667 passed, 81 skipped, 0 failed. The drill was not re-run until green: it passed on the first
  CI run after the fix.

## 3. Other checks (historical results; tested source unchanged since, unless noted)

| Check | Revision | Result | Recorded in |
| --- | --- | --- | --- |
| Source contract `--mode ci` on a clean clone; checker tests | `1d03edd` (contract unchanged since) | satisfied; 29/29 | Session C README § Monorepo import |
| Gitleaks over the full branch history (+ negative test with a planted secret) | `1d03edd` | clean (73 commits); planted secret reported | same |
| Website: frozen install, lint, build; CSS identical with/without `Homayar/` | `1f431bc`–`1d03edd` | 0 errors (2 pre-existing warnings); byte-identical CSS | same |
| Console `dist/` identical between monorepo and product-only layout; lint; tests | `1d03edd` | identical (54 files); lint clean; 113/113 in three runs — one earlier run 1/113 failed, name not captured, not reproduced | same |
| Backend full suite, PostgreSQL 16, `homayar_app`, `TEST_REDIS_URL`, with ffmpeg | product `d318f7e` | 702 passed, 45 skipped, 1 failed (broker-restart drill against a non-persistent Redis; passed against an AOF Redis) | Session C README § Tests |
| R-02: `ffmpeg`-marked tests with real binaries (`-m ffmpeg`), macOS ffmpeg 8.1.2 | `620f14c` + R-02 working tree | 35 passed (`test_media_fixtures.py`, `test_media_adversarial_api.py`) | `REMAINING_WORK.md` R-02 |
| R-02: same tests in a Debian bookworm container, distro ffmpeg 5.1.9 (Ubuntu 24.04 image could not be pulled; registry unreachable) | same | 35 passed | same |
| R-02: fail-closed check — binaries removed from `PATH` | same | with `HOMAYAR_REQUIRE_MEDIA_TOOLS=1`: usage error, exit 4; without it: 35 skipped with the reason (local opt-out kept) | same |
| R-02: CI-equivalent backend run: `check-migrations.sh` + full suite, PostgreSQL 16 (superuser, not `homayar_app` login), Redis 7.4 AOF with `TEST_REDIS_CONTAINER`, `HOMAYAR_REQUIRE_POSTGRES_TESTS=1 HOMAYAR_REQUIRE_MEDIA_TOOLS=1`, ffmpeg 8.1.2 | `620f14c` + R-02 working tree | migrations OK; **703 passed, 45 skipped, 0 failed** (0 `ffmpeg` skips; 41 live S3, 3 Padyar, 1 SQLite-only drill); broker-restart drill ran because `TEST_REDIS_CONTAINER` was set | same |
| Migration round trip of the last two revisions | product `e7eeef5` | clean | same |
| Container job replica from a clean `git archive` | website `e5fd51e` / product `e7eeef5` | all passed; air-gap step corrected in `34bc228` | same |
| Customer journey against the integrated backend through the real nginx config | 2026-09-25 tree (`e7eeef5` product) | 62/63 API checks (the one failure was a harness-made state, analysed); dead letter + replay 8/8; browser fa/en at 375/768/1280 px | same § Customer journey |
| Network egress probes (Docker Desktop) | `e5fd51e` | 33 passed (twice) | same § Network egress |
| Graphify code map refresh | `2a30213` | 440 files, hashes 440/440; HTML not generated/inspected | `GRAPHIFY_GUIDE.md` |

Overlapping selections are not added together: the CI backend run, the replica run and the
PostgreSQL 16 local run are the same test suite on different environments.

## 4. Boundaries of the evidence

| Kind | What |
| --- | --- |
| Real backend integration | PostgreSQL (16 locally, 17 in CI) with RLS as `homayar_app`; Redis; real Celery prefork workers and beat; real ffprobe/ffmpeg locally; real nginx configuration in the journey; real SMTP client against a local sink |
| Fixture or fake-provider tests | mock ASR/analysis (development only); diarization "unavailable"; S3 adapter over a controlled fake client; provider contract tests with fakes; Session B's QA audit (fixture API, superseded by Session C re-checks) |
| Live external-service validation | **none**: no model, the Padyar AI Gateway, Google, Asanak, a mail server, an S3 bucket, or a deployed topology was used |
| Not run | ffmpeg tests in CI (R-02); live S3 (41); Padyar (3); broker-restart drill in CI; website browser tests (not in the repository); real devices, Safari/Firefox, screen readers; load tests; restore rehearsal on real infrastructure |

## 5. How to reproduce the CI backend job locally (Linux replica)

Disposable, all names prefixed per session; no shared services.

1. Docker network plus `postgres:17-alpine` (user/password/db as in the `backend` job of `ci.yml`)
   and `redis:7.4-alpine` on it.
2. Clean clone of the revision (never the working checkout), mounted into `python:3.12-slim`.
3. Inside: `apt-get install procps` (and `ffmpeg` to include the 35 media tests),
   `pip install uv==<UV_VERSION from ci.yml>`, `cd Homayar && uv sync --frozen`, export the `env:`
   block of the `backend` job (host names of the service containers instead of `127.0.0.1`), run
   `uv run ../infra/ci/check-migrations.sh backend` on a fresh database, then
   `uv run pytest backend/tests -rs`.
4. Remove the containers (`docker rm -f -v`) and the network afterwards.

On macOS, if the Docker CLI hangs on the keychain credential helper, point `DOCKER_CONFIG` at a
scratch directory containing a `config.json` without `credsStore` (plus the `contexts` link) —
Session C's workaround.

## 6. Remaining validation gaps

See `REMAINING_WORK.md`: R-02 (ffmpeg in CI), R-03 (website tests), R-09…R-13 (external services),
R-06 (reproducible builds), and every production gate in `PROJECT_STATE.md` §8.
