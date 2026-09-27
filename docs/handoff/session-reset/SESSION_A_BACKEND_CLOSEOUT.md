# Session A — backend stabilization and CI closeout (2026-09-26/27)

Scope: restore a verified green `main` after PR #5 and finish the R-02 media-validation
correction. No WP09 or feature work. Single agent; no subagents or external reviewers. The product
remains **NOT PRODUCTION READY**: green CI validates this change only.

Working copy: an isolated clone `/Users/emperor/Dev/homayar-com-session-a` (branch
`fix/r02-media-diagnostics-and-drill` from `main` `1b34472`). The clean clone
`/Users/emperor/Dev/homayar-com`, the original checkout, its nested repositories and the backup
under `/Users/emperor/Backups/` were not modified.

## 1. Starting point (re-verified, not taken from earlier reports)

- PR #5 (`ci/r02-ffmpeg-backend-tests`) was merged by the owner on 2026-09-26 15:54:55Z at head
  `88a77c2`; merge commit `main` `1b34472`. PR run
  [36252829293](https://github.com/Kohandezh/Homayar.com/actions/runs/36252829293) passed all six
  jobs on `88a77c2`.
- `main` run [36253589355](https://github.com/Kohandezh/Homayar.com/actions/runs/36253589355) on
  `1b34472`: backend job 108436100006 failed; the other five jobs passed.
- `e29de4d` (temporary-file stderr, `-nostats`, records) and `87d3323` (R-02 wording) were pushed
  to the branch **after** PR #5 had merged. A merged pull request's head never advances, so the
  unchanged head was expected — not GitHub event lag. Neither commit is in `main`, and no CI run
  validated `e29de4d`: the green run above tested `88a77c2` only.
- No corrective pull request or newer `main` existed when this session started.

## 2. Why `main` failed

**Failing test:** `backend/tests/test_transport_drills.py::test_visibility_timeout_redelivery_during_a_live_attempt_is_inert`
— `AssertionError: redelivered copy rejected by the live lease: not reached within 200.0s`
(1 failed, 702 passed, 46 skipped). Not the media change and not the kill-child drill fixed in
`2a30213`.

**Environment (from the job log):** GitHub `ubuntu-24.04` runner, Python 3.12.14, PostgreSQL 17.11
(alpine service), Redis 7.4.11 (no persistence), ffmpeg 6.1.1-3ubuntu5, real Celery prefork
workers. The application runs as `homayar_app` (`SET ROLE`; not superuser, no `BYPASSRLS`); the
job's PostgreSQL log shows the RLS probes of the suite being refused, as expected for that role.
Every segment of the suite ran about 1.4× slower than in the green PR run on the same code.

**Mechanism (from the code, then demonstrated):** the drill holds one attempt at a gate and waits
for kombu to restore the unacknowledged delivery to a second, idle worker; it accepted only the
answer `already_running`. Two other answers are possible, and each is acknowledged, which leaves
nothing for kombu to restore, so the drill can only time out:

1. `busy` — the claim locks the job row with `FOR UPDATE SKIP LOCKED`; a copy that arrives while
   the live attempt's heartbeat renewal (or another protected write) holds the row lock gets
   `busy`. This is the documented, inert answer for competing claimants (`backend/jobs/leases.py`).
2. `lease_expired` — the drill's lease was 4 s renewed every 2 s, i.e. 2 s of slack held for
   ~100 s. One renewal delayed past that lapses the lease; the heartbeat then stops (`EXPIRED`)
   and the copy answers `lease_expired` (only the recovery sweep may fence an expired attempt).

**Classification: timing-dependent.** The same code passed on the PR runner and in the replica
baseline below. Which of the two answers occurred on the runner is **not in the retained evidence**:
the worker logs stayed on the runner (no artifacts), so this record does not claim either. The
unnamed transport-drill timeout at PR #2 (`b68803d`) may have been the same drill; not proven.

**Evidence (Linux replica: `ubuntu:24.04`, apt ffmpeg 6.1.1-3ubuntu5, Python 3.12.3, uv 0.11.24,
`postgres:17-alpine`, `redis:7.4-alpine`, the `backend` job's environment, migrations applied by
`check-migrations.sh`):**

| Check | Result |
| --- | --- |
| Effective role of a worker session | `current_user` `homayar_app`, `session_user` `homayar_test`; `homayar_app` not superuser, no `BYPASSRLS` |
| Original drill on `1b34472`, once (baseline) | passed |
| E0: copy restored with a live lease and a free row | `already_running` |
| E1: copy restored while the job row is locked | `busy`; acknowledged; Redis `unacked` 0 (nothing left to restore); attempt 1, generation 1 |
| E2: renewing process stopped for 6 s (lease 4 s) | holder logs `JOB_LEASE_LOST … outcome=expired`; copy answers `lease_expired` |
| X1: `main`'s drill body unchanged, job row locked while kombu redelivers | **exact CI message** `redelivered copy rejected by the live lease: not reached within 200.0s`; the copy answered `busy`; Redis drained |
| X2: corrected drill body, same injection | passed; copy `busy`, attempt 1, generation 1, one completion |

The injections (E1, E2, X1, X2) are controlled experiments in a scratch test file that was never
committed; they show that each answer produces the CI signature, not which one the runner saw.

## 3. Drill correction (test only; product behaviour unchanged)

Commit `c45e798`:

- A copy of a live attempt must answer `already_running` or `busy` (both inert); any other answer
  fails immediately with the answer in the message instead of after 200 s.
- The drill additionally asserts the lease generation is unchanged (no fence) and that exactly two
  results were logged: the inert copy and one completed attempt. `_completed_once` (one
  transcript row, one usage settlement, no invariant violations) is unchanged.
- Its workers run with a 30 s lease renewed every 10 s (20 s of slack) instead of 4 s/2 s; the
  other drills keep their leases.
- Every drill prints the last 40 lines of each worker log at teardown; pytest shows them only for a
  failed test, so a future CI failure names the answer the workers gave.

## 4. Media decode diagnostics (R-02 completion)

History: the first CI run with real binaries
([36250360273](https://github.com/Kohandezh/Homayar.com/actions/runs/36250360273), `80ae521`) showed
that ffmpeg 6.1.1 exits 0 on `corrupt.mp3`, `corrupt.flac` and `tail.mp3` despite `-xerror`
(4 failures, including the upload-route case). `88a77c2` (merged) refused any error-level stderr but
read it with `subprocess.run(stderr=PIPE)`; `e29de4d` (never merged) wrote it to an anonymous
temporary file.

Measured with a stand-in decoder that writes diagnostics without end (macOS, 10 s timeout):

| Implementation | Outcome | Written by the decoder | Test process peak RSS |
| --- | --- | --- | --- |
| `88a77c2` / `main` (`PIPE`) | `MediaDecodeTimeoutError` (transient) after 13 s | ~396 MB, all buffered in memory | 860 MB |
| `e29de4d` (temporary file) | `MediaDecodeTimeoutError` (transient) after 10 s | ~403 MB, stored on disk | 19 MB |
| `4428756` (this change) | `MediaDecodeError` (terminal) after 0.9 s | ≤ one pipe buffer | 20 MB |

So a timeout is not a byte cap, and diagnostics written to a temporary file *are* stored (on disk,
unlinked). Earlier wording that said otherwise is corrected in `VALIDATION.md`.

Final behaviour (`Homayar/backend/media/validation.py`, commit `4428756`):

- `ffmpeg -v error -nostats -xerror …` with stdin and stdout on the null device and stderr on a
  pipe. A watcher thread reads **at most one byte** (a blocking read on a thread works for pipes on
  every platform; `select` does not support pipes on Windows).
- The first diagnostic byte refuses the file (`media_decode_failed`, terminal) and the decoder is
  killed: it can write no more than the kernel pipe buffer (a fixed size, 64 KiB by default on
  Linux) and nothing accumulates in memory or on disk. A decoder that has reported an error is
  refused even if it would also have run past the timeout (previously that case was a transient
  timeout). Truncated diagnostics can never make a file valid.
- A silent decoder that does not finish within the timeout: `media_decode_timeout` (transient).
  Missing binary: `media_decoder_unavailable` (transient). Non-zero exit: `media_decode_failed`.
- The decoder is killed if still running and always reaped, and the pipe closed, on success,
  refusal, timeout and on an exception raised while waiting (e.g. a task time limit).
- Diagnostics are never read beyond that byte, logged, stored or returned; error messages are fixed.
- No platform-specific resource limit is used.

Tests (`test_media_validation.py`, `test_media_fixtures.py`): a real stand-in decoder process for
argv/stdin/stdout, exit codes, exit 0 with a diagnostic, timeout, a diagnostic flood (bounded,
refused in seconds, process reaped), an interrupted wait (process reaped), no leak of path or
diagnostic text, stdout output not mistaken for a diagnostic; real ffmpeg: every valid fixture
leaves stderr empty under the exact decode command, a 60 s file decoded over the deployed 30 s
window is accepted, and the corrupt/truncated fixtures are refused.

## 5. Verification

Local and remote results are separate; counts of overlapping selections are not added.

**Local, on `4428756` (the final code revision; later commits change only records), run one at a
time in the Linux replica of §2 unless noted:**

| Step | Result |
| --- | --- |
| `ruff check` / `ruff format --check` on the four touched Python files (macOS) | clean |
| Media unit and fixture tests, macOS ffmpeg 8.1.2 | 80 passed (three runs; the first attempt exposed a race in the new flood test's own counter file, fixed before commit) |
| Media validation + fixtures + upload-route adversarial tests, ffmpeg 6.1.1-3ubuntu5 | **89 passed, 0 skipped** |
| Same with ffmpeg removed, `HOMAYAR_REQUIRE_MEDIA_TOOLS=1` | usage error, pytest exit 4 (the CI step fails) |
| `test_transport_drills.py` | 4 passed, 1 skipped (broker restart: needs `TEST_REDIS_CONTAINER`); redelivery drill 98.6 s |
| Fresh PostgreSQL 17 + Redis 7.4 containers → `check-migrations.sh` → full suite (CI order) | migrations OK; **716 passed, 46 skipped, 0 failed** (703 + 13 new tests) |

**Remote (GitHub Actions):** see §7 — recorded after the pull request's run.

## 6. Remaining skips and limitations

- CI skips (explicit reasons in `pytest -rs`): 41 live S3 (opt-in, no bucket), 3 Padyar AI Gateway
  (contract missing), 1 SQLite-only restore drill, 1 broker-restart drill (needs
  `TEST_REDIS_CONTAINER`).
- The ffmpeg version on the runner follows Ubuntu 24.04 security updates, not an exact pin.
- A decoder that ignores SIGKILL (uninterruptible I/O) would still block its reaping, as with
  `subprocess.run`.
- The ffprobe call still reads its (small, field-limited) JSON through `subprocess.run`; its size is
  checked after the fact (256 KiB), not streamed. Not changed here.
- The transport drills remain timing-sensitive by nature (real processes, real broker); the
  teardown log tails exist so a recurrence can be diagnosed instead of re-run.

## 7. Where things are

- Branch `fix/r02-media-diagnostics-and-drill`: `c45e798` (drill), `4428756` (media decode), then
  records (this file, `VALIDATION.md` §1/§2a/§2b, `REMAINING_WORK.md` R-02, `PROJECT_STATE.md`,
  prompts, `docs/operations/ci.md`, PROGRESS Session 18, Graphify code map refreshed once at
  `4428756`).
- The corrective pull request, its CI runs, the merge commit and the resulting `main` run are
  listed on the pull request; `main` is not described as green in this file because that result
  can only exist after this file is merged. The final report of the session states it.
- Stale branch `ci/r02-ffmpeg-backend-tests` (tip `87d3323`): left as is (deleting branches is not
  authorized for this session); do not build on it.
- Local only: the isolated clone, the replica image `sa-runner:noble`, scratch experiments and
  logs under `/tmp/claude/sa/`; the replica containers and network are removed at the end.

## 8. Next bounded task

WP09-T01 (channel metadata, silent/duplicated channels, trusted PBX mapping) —
`docs/handoff/session-reset/WP09_T01_SESSION_PROMPT.md`. Not started in this session.
