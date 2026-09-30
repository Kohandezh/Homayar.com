# SPEC — team stab02-t1 (mission 20260929-backend-mode-100)  [DRAFT — drafted by stab02-drafter, base 7112407]

Orchestration mode: `foreman_native_adapted`. Role separation, blind oracle, bare-diff review, two-round cap.
Lane: L4 (`LANE=4`: PostgreSQL 127.0.0.1:5436, Redis db 8 for the app and db 9 = `TEST_REDIS_URL`, which the drill flushes,
lock `/root/foreman/20260929-backend-mode-100/lane-4.lock`).
Final-reviewer trigger: **none** (test-only change: no authz, RLS, migration, billing/entitlement, secret or PII code is touched
and no production file changes; if the maker finds it must touch production code, stop and send a Lock request, see below).

## Task
- Linear: HOM-163 [STAB-02] (child of HOM-8). Stabilization of
  `Homayar/backend/tests/test_transport_drills.py::test_visibility_timeout_redelivery_during_a_live_attempt_is_inert`, which fails
  intermittently (seen in full-suite chunk 03; reproduced on BASE).
- Type: test stabilization (test-only). One root cause: the drill stops both worker processes as soon as the job row is COMPLETED and
  Redis shows nothing unacknowledged, but neither event means the held attempt's task has returned. The stop signals the whole
  process group, so it kills the pool child while the task is still finishing and the result line the drill asserts on is never logged.
- The test stays in the suite: never skipped, quarantined, retried or weakened. The fix waits on the worker's own completion
  signal (its task-result log line), which is the value the test asserts afterwards.

## Base
`BASE_SHA` = `7112407` (integration head, HOM-32 landed; `/home/user/Homayar.com` is at it).

## Context (verified by the drafter against source at 7112407)
Paths are relative to `Homayar/backend/` unless stated; `SP` = `Homayar/.venv/lib/python3.12/site-packages`.

1. **The test** is `tests/test_transport_drills.py:387-424`. It dispatches (`:396`); waits until the attempt is held at the ASR gate
   (`:397`); waits up to `RESTORE_DEADLINE` = 200 s (`:76`) for the first task result logged by either worker (`:403-407`), which must be
   the redelivered copy's, in `INERT_COPY_STATUSES = {already_running, busy}` (`:94`, `:408-409`), with the job RUNNING and
   `(attempt_count, lease_generation) == (1, 1)` (`:410-412`); opens the gate (`:413`); waits for job state COMPLETED (`:414`);
   waits for `drill.redis() == {"queued": 0, "unacked": 0}` (`:415-417`); stops both workers (`:418-419`); then asserts
   `_completed_once(...)` (`:421`), `_received(holder) + _received(other) == 2` (`:422`) and
   `sorted(_statuses(holder, other)) == sorted([copy_status, "completed"])` (`:424`).
2. **Polling.** `_wait` polls every 0.1 s (`:107-112`). `_statuses` reads only worker log lines that contain `backend.jobs.process_meeting[`
   and `] succeeded in` and extracts `'status': '<x>'` (`:79`, `:104`, `:161-170`).
3. **The durable outcome is committed before the task returns.** `_persist_success` sets `job.state = COMPLETED` (`jobs/processor.py:1205`)
   and commits (`:1220`). `process_job` then still runs `heartbeat.stop()` (`:1975`), `_delete_audio_after_success` (`:1977-1981`),
   `_run_voicemail_hook` (`:1982`; its own tenant-bound session and commit, `:1426-1432`) and only then returns the result dict
   (`:1983`; `jobs/tasks.py:32`). The `succeeded in ...` line is written by the pool child (`SP/celery/app/trace.py:533`; the failure logs
   show `ForkPoolWorker-1`); the parent acknowledges afterwards (`SP/celery/worker/request.py:552-553`; `acks_late` and
   `reject_on_worker_lost`, `celery_app/__init__.py:45-47`).
4. **`TransportProcess.stop()` is not a warm stop for the pool child.** It calls `os.killpg(self.pid, SIGTERM)` (`tests/_transport.py:289-293`),
   i.e. the whole process group. Celery resets SIGTERM to its default disposition in the pool child (`SP/celery/concurrency/prefork.py:24-27`,
   `:47`), so the child dies at once (`Process 'ForkPoolWorker-1' ... exited with 'signal 15 (SIGTERM)'`, then `WorkerLostError ... Job: 0`)
   while only the parent logs `Warm shutdown`.
5. **Observed failure.** `/root/foreman/20260929-backend-mode-100/wp02t03-t1/handoffs/impl-suite-turn-3/full-03-run1-flake.txt` (the path in the task
   block, `.../lanes-proof-v1-l1-full-03.txt`, does not exist; `.../lanes-proof/l1/full-03.txt` is a truncated 3-line log):
   `test_transport_drills.py:424: assert ['already_running'] == ['already_run..., 'completed']`. Line `:421` (`_completed_once`: COMPLETED, one
   attempt, one transcript row, one settlement, no invariant violation) had **passed**: the database outcome was complete. Holder log:
   `received` at 00:35:45, no `succeeded` line, `Warm shutdown`, `ForkPoolWorker-1 exited with 'signal 15 (SIGTERM)'` and `WorkerLostError`
   at 00:37:12.8; the other worker logged the copy's `already_running` at 00:37:12.0. All three chunk-03 runs of that handoff failed the
   same way (`base-chunk-03.txt` at BASE, `full-03-run1-flake.txt`, `full-03-run2.txt`: each `1 failed, 329 passed, 43 skipped`).
6. **Measured by the drafter on lane 4** (scratch copies of `Homayar/backend` at 7112407, run with the checkout's interpreter; the repository was
   not touched; logs in `/root/foreman/20260929-backend-mode-100/stab02-t1/scratch/evidence/`, `results.txt` is the index, the two trees are `/root/foreman/20260929-backend-mode-100/stab02-t1/scratch/base` and `/root/foreman/20260929-backend-mode-100/stab02-t1/scratch/fix`, the stress runs
   are in `/root/foreman/20260929-backend-mode-100/stab02-t1/scratch/stress-fix/`).
   The test alone, one run takes 97-105 s:
   - BASE, no load: 2 of 3 runs failed (same assertion at `:424`, same holder log shape). "Passes alone" is not a reliable property of BASE.
   - BASE, the pool children pinned to a CPU that 3 busy loops also occupy while the drill process stays elsewhere (`stress.sh`, V4):
     4 of 5 runs failed at `:424` (`base-childload-1..3`: 3 of 3; `stress.sh` itself at BASE: run 1 passed, run 2 failed,
     `consecutive passes: 1 of 3`, exit non-zero).
   - BASE, generic CPU load does **not** reproduce: the whole drill (test process, workers, children) pinned with 3 busy loops to one CPU, 0 of 4 failed;
     8 unpinned busy loops on 4 CPUs, 0 of 3 failed. (Interpretation, not verified further: a starved drill process polls late, so the worker wins the race.)
     The load must slow the pool child, not the drill's own polling process.
   - Fixed test (Recommended change, scratch copy): no load 2 of 2 passed; the whole-drill-pinned load 6 of 6 passed; with the 3 s post-commit delay it passed (`101.55s`); with a
     75 s delay it failed with `held attempt returned its result: not reached within 60.0s` (`157.78s`); `stress.sh` x8: `consecutive passes: 8 of 8`, exit 0, 97.95-98.75 s per
     run (`stress-fix/run-1..8.txt`; an earlier attempt of the same script reached 5 of 5 before the environment recreated the scratch directories at 02:17:38 and broke the
     working directory of run 6/7, so it was discarded and re-run in full); whole file `4 passed, 1 skipped in 139.04s`.
7. **Why the Redis wait cannot see the holder finish.** kombu assigns the delivery tag once, at publish (`SP/kombu/transport/virtual/base.py:616-624`);
   the visibility restore re-pushes the same payload (`SP/kombu/transport/redis.py:424-435`, `:790-805`), so the redelivered copy carries the original's
   tag; an ack removes that tag from the `unacked` hash and index (`redis.py:383-385`, `:402-405`). Probe against Redis db 15 (kombu 5.5.4): consumer A
   takes the message and does not ack; after `restore_visible` consumer B takes the copy; `same tag: True`; after B's ack the state is
   `{'queued': 0, 'unacked': 0}` while A has not acked; A's later ack is a no-op (`/root/foreman/20260929-backend-mode-100/stab02-t1/scratch/probe_tag.py`). Measured in the drill (scratch copy, 1 s
   after the copy's result, gate still closed, job RUNNING): `{'queued': 0, 'unacked': 0}`. So the wait at `:415-417` is already true when the gate opens and is
   no evidence about the holder; the holder's own ack is unobservable in Redis and its observable completion signal is its `succeeded` log line.
8. **Race window.** The drill sees COMPLETED as soon as `:1220` commits, polls at 0.1 s and stops at once; the child still needs `:1975-1983` (thread join, a
   voicemail-hook session and commit, result serialisation, trace logging). Deterministic proof, probe P1 (V3; scratch only, never committed): a 3 s
   sleep injected after the commit makes BASE fail with the exact assertion (measured: `1 failed in 95.66s`, holder `ForkPoolWorker-1 exited with 'signal 15'`
   0.74 s after the copy's answer) and the fixed test pass (`1 passed in 101.55s`).
9. **Other drills do not share this weakness** (read, not stress-tested): kill-child waits for the recovery delivery's ack, a fresh publish with its own tag
   (`:331-334`); kill-all waits for two `succeeded` lines before `healthy.stop()` (`:377-379`); beat waits for `unacked == 0` of fresh deliveries (`:512`).
   Measured with the fix: the whole file runs `4 passed, 1 skipped in 139 s` (the skip is the broker-restart drill, `:432-433`, `requires TEST_REDIS_CONTAINER and the docker CLI`).
10. **The race is not in production code.** The behaviour the drill asserts about production (copy inert, one attempt, one settlement) held in the failing run
    (`:408-412`, `:421`). Lock request: none.

## Acceptance criteria (observable)
1. **The held attempt's completion is awaited on a real signal before any worker is stopped.** In the redelivery test, after the COMPLETED wait and before
   `holder.stop()` / `other.stop()`, the test polls (bounded by the existing `DEADLINE`, with a `what=` message that names the awaited event) until a worker log shows
   the task result with status `completed`, e.g. `_wait(lambda: "completed" in _statuses(holder, other), what="held attempt returned its result")`. No fixed sleep
   is added and no deadline, lease, visibility or restore constant is raised. Observable through AC2, AC3 and AC5.
2. **Deterministic red/green (probe P1, V3).** With `STAB02_POST_COMMIT_DELAY=3` injected in the worker after the COMPLETED commit (scratch copy only):
   the BASE test **fails** at the `sorted(_statuses(...))` assertion with `assert ['already_running'] == ['already_run..., 'completed']`, and the fixed test
   **passes** (its final assertion requires the `succeeded in ... 'status': 'completed'` line, so a pass proves the line exists). The worker logs of a run are kept as
   `<tmp_path>/holder.log` and `<tmp_path>/other.log` (`tests/test_transport_drills.py:254-263`; `/tmp/pytest-of-<user>/pytest-<N>/test_visibility_timeout_redeli0/`).
3. **Stress (V4).** `stress.sh` on the fixed tree prints `consecutive passes: 8 of 8` and exits 0. Any failed run ends the attempt (the counter restarts
   at 0 after a root-cause note; no re-run-until-green). The Foreman/oracle control: `stress.sh <BASE tree root> <out> 3` exits non-zero (a failure within its 3 runs; measured 4 of 5 BASE runs failed under this load, so a control that
   passes 3 of 3 is repeated once before it is doubted).
4. **No assertion weakened, no skip.** From `<W>`: `git diff -U0 7112407..HEAD -- Homayar/backend/tests/test_transport_drills.py | grep -E '^-[^-]' | grep -E 'assert|_wait\('`
   prints nothing; `git diff -U0 7112407..HEAD -- Homayar/backend/tests | grep -E '^\+' | grep -iE 'skip|xfail|flaky|rerun|deselect'` prints nothing (a reworded comment that
   trips a grep is listed and judged by the reviewer; an assertion or wait line is never an acceptable hit). The test still ends with
   `_completed_once(job_id, workspace_id, drill.settings, attempts=1)`, `assert _received(holder) + _received(other) == 2` and
   `assert sorted(_statuses(holder, other)) == sorted([copy_status, "completed"])`; the `copy_status in INERT_COPY_STATUSES`, RUNNING, `(1, 1)` checks and the Redis
   `{"queued": 0, "unacked": 0}` wait remain. The node id is unchanged and, under the lane environment, `-rs` lists no SKIPPED line for it and its outcome is PASSED.
5. **A missing completion signal fails loudly (probe P2, V5).** With `STAB02_POST_COMMIT_DELAY=75` (longer than `DEADLINE` = 60 s) the fixed test **fails**
   with an `AssertionError` raised by `_wait`, whose message names the awaited event and ends `not reached within 60.0s` (measured: `held attempt returned its result:
   not reached within 60.0s`); it never passes silently.
6. **The other drills keep their outcome.** `tests/test_transport_drills.py` as a whole file: 4 passed, 1 skipped (`requires TEST_REDIS_CONTAINER and the docker CLI`), 0 failed.
7. **Chunk gate.** The full-suite chunk that contains `test_transport_drills.py` (chunk 03 at BASE; confirm with `grep -n transport_drills <outdir>/chunk-0*`) has 0 failed and
   the same skip count as BASE for that chunk.
8. **Scope and lint.** `git diff --name-only 7112407..HEAD` lists only paths from "File ownership"; nothing under `Homayar/backend/` outside `tests/` changes;
   `ruff check` and `ruff format --check` pass on the changed files.

## Deny-cases and closest allow-controls
- DENY stopping a worker before the held attempt's result line exists (P1: 3 s delay, holder killed mid-return). ALLOW-CONTROL: same drill with no delay: the line is
  awaited (already present) and the test passes; with the 3 s delay it passes after about 3 s.
- DENY dropping or loosening `sorted(_statuses(holder, other)) == sorted([copy_status, "completed"])` (or `>=`, `in`, or removing `"completed"`). ALLOW-CONTROL: the assertion is
  textually unchanged and `busy` stays an accepted copy status.
- DENY silence when the attempt never returns (P2: 75 s delay -> `not reached within 60.0s`). ALLOW-CONTROL: 3 s delay -> passes.
- DENY skip/xfail/quarantine/retry of this test. ALLOW-CONTROL: the broker-restart drill keeps its existing environment skip (the only skip in the file).
- DENY a fixed `time.sleep` (or a larger `DEADLINE`/`VISIBILITY_SECONDS`/lease constant) as the fix. ALLOW-CONTROL: `_wait` polling on the result log line or on durable
  database state, bounded by the existing `DEADLINE`.
- DENY treating `drill.redis() == {"queued": 0, "unacked": 0}` as proof that the holder acknowledged. ALLOW-CONTROL: the wait may stay (assertion kept), with a comment saying it is
  already satisfied by the copy's ack (shared delivery tag).
- DENY 7-of-8 stress passes counted as success. ALLOW-CONTROL: 8 of 8.
- DENY a "stress" that cannot fail BASE (generic load: 0 of 7 BASE failures). ALLOW-CONTROL: `stress.sh` (pool children on a loaded CPU) fails BASE.

## File ownership
Maker may change (both locked to HOM-163 in `locks.tsv`):
- `Homayar/backend/tests/test_transport_drills.py` (the redelivery test body and its comments; a small helper next to `_statuses` is allowed).
- `Homayar/backend/tests/_transport.py` (only if a helper is genuinely needed; this SPEC requires no change there).
Must not change: any production code under `Homayar/backend/` outside `tests/`, `Homayar/backend/tests/_transport_worker.py`, `conftest.py`, `pyproject.toml`, CI workflow, migrations,
`saas_models.py`, `rls.py`, `authorization.py`, `config.py`, docs, other tests.
Checker (blind oracle): writes no file in the repository; it runs V3-V5 on scratch copies of the maker's tree (and BASE) and records under
`/root/foreman/20260929-backend-mode-100/stab02-t1/{oracle,reviews}/`. Same allowed/forbidden actions as the earlier team SPECs: no commit/push/branch/PR/Linear, no
migrations, no DB reset, no network, nothing outside the worktree and the team directory.

### Lock request
None. (Not recommended: `Homayar/backend/tests/_transport_worker.py` for a committed delay seam like probe P1; it would need a second ~100 s drill to be used, so P1 stays a scratch probe.)

## Recommended change (the maker may deviate if every acceptance criterion holds)
In the redelivery test, after the COMPLETED wait, add the result-line wait of AC1 and a comment: COMPLETED is committed inside the task, the task still has post-commit work, and
`TransportProcess.stop()` signals the whole process group, so the drill waits for the task's own result line before it stops anything. Keep the Redis wait (assertion unchanged)
and note in its comment why it is already satisfied. Do not assume which worker holds the original delivery: either may (`_statuses(holder, other)` covers both). Do not use
`_received(...)` counts or Redis state as the completion signal (Context 2, 7).

## Verification commands
Lane runner only. `<W>` = maker worktree. Never run two drills at once on a lane (the drill `FLUSHDB`s Redis db 9; `flock "$LANE_LOCK"` serializes them). One run of this drill lasts
about 100 s (kombu's restore cadence), so use `run_in_background` for anything beyond a couple of runs and read the output file.

### V1 Focused single run (from `<W>/Homayar`)
```bash
source /root/foreman/20260929-backend-mode-100/testenv-l4.sh && flock "$LANE_LOCK" uv run pytest backend/tests/test_transport_drills.py::test_visibility_timeout_redelivery_during_a_live_attempt_is_inert -q -p no:cacheprovider -rs
```

### V2 Whole drill file (about 2.5 min)
The V1 command with `backend/tests/test_transport_drills.py` instead of the node id.

### V3 P1, deterministic red/green (Foreman/oracle and maker; scratch copy, the worktree is never edited)
`<TREE>` = the tree under test (the BASE checkout for red, `<W>` for green). Expected: BASE tree -> 1 failed at `:424`; fixed tree -> 1 passed.
```bash
S=$(mktemp -d); mkdir -p "$S/copy"
T=backend/tests/test_transport_drills.py::test_visibility_timeout_redelivery_during_a_live_attempt_is_inert
(cd <TREE>/Homayar && tar --exclude=__pycache__ -cf - backend pyproject.toml alembic.ini) | tar -xf - -C "$S/copy"
cat > "$S/hook.txt" <<'EOF2'
        _delay = float(os.environ.get("STAB02_POST_COMMIT_DELAY", "0"))
        if _delay:
            _original_hook = processor._run_voicemail_hook

            def _slow_hook(*args, **kwargs):
                time.sleep(_delay)
                return _original_hook(*args, **kwargs)

            processor._run_voicemail_hook = _slow_hook
EOF2
python3 - "$S/copy" "$S/hook.txt" <<'PY'
import sys
p = sys.argv[1] + "/backend/tests/_transport_worker.py"
s = open(p).read()
anchor = "        processor.create_provider_bundle = _gated_bundle\n"
assert anchor in s
open(p, "w").write(s.replace(anchor, anchor + open(sys.argv[2]).read()))
PY
source /root/foreman/20260929-backend-mode-100/testenv-l4.sh; cd "$S/copy"
STAB02_POST_COMMIT_DELAY=3 PYTHONDONTWRITEBYTECODE=1 flock "$LANE_LOCK" <W>/Homayar/.venv/bin/python -m pytest "$T" -q -p no:cacheprovider
```
The copy imports `backend` from its own directory (`pythonpath = ["."]`); the interpreter is the worktree's. The hook sleeps after the COMMIT, before the task returns
(`_run_voicemail_hook` is called at `jobs/processor.py:1982`).

### V4 Stress (AC3)
Save the script below outside the repository (for example `/root/foreman/20260929-backend-mode-100/stab02-t1/handoffs/stress.sh`) and run `bash stress.sh <W> <outdir> 8` in the
background (about 14 min, 8 x ~98 s). It pins 3 busy loops to the last CPU and moves every pool child of the drill's workers onto that CPU; the drill process and the other CPUs are not loaded.
Accept only the last line `consecutive passes: 8 of 8` and exit 0; afterwards `pgrep -fx 'sh -c while :; do :; done'` prints nothing (exit 1). Control at BASE: `bash stress.sh <BASE tree root> <outdir> 3`
must exit non-zero (`<tree root>` is the directory that contains `Homayar/`). `PYTEST` may override the runner (default `uv run pytest`).
```bash
#!/usr/bin/env bash
# usage: stress.sh <worktree> <outdir> [runs=8]     (PYTEST overrides the runner, default "uv run pytest")
# Loads the CPU that the drill's pool children run on, so that the task's return path is slow while the
# drill's own polling process is not: the situation of the observed failure (HOM-163).
set -u
W=$1; OUT=$2; RUNS=${3:-8}
mkdir -p "$OUT"; cd "$W/Homayar" || exit 2
source /root/foreman/20260929-backend-mode-100/testenv-l4.sh
T=backend/tests/test_transport_drills.py::test_visibility_timeout_redelivery_during_a_live_attempt_is_inert
CPU=$(( $(nproc) - 1 )); bg=()
for _ in 1 2 3; do taskset -c "$CPU" sh -c 'while :; do :; done' & bg+=($!); done
( while :; do
    for p in $(pgrep -f 'backend.tests._transport_worker'); do
      for c in $(pgrep -P "$p"); do taskset -a -pc "$CPU" "$c" >/dev/null 2>&1; done
    done
    sleep 0.2
  done ) & bg+=($!)
trap 'kill "${bg[@]}" 2>/dev/null' EXIT
pass=0
for i in $(seq 1 "$RUNS"); do
  flock "$LANE_LOCK" ${PYTEST:-uv run pytest} "$T" -q -p no:cacheprovider -rs > "$OUT/run-$i.txt" 2>&1 || break
  pass=$((pass + 1))
done
echo "consecutive passes: $pass of $RUNS"
[ "$pass" -eq "$RUNS" ]
```

### V5 P2 (AC5)
V3 on the fixed tree with `STAB02_POST_COMMIT_DELAY=75` (about 160 s); expect 1 failed with the `_wait` message.

### V6 Chunk gate
`LANE=4 bash /root/foreman/20260929-backend-mode-100/suite.sh <W> <outdir> 03` (one chunk per foreground command; confirm the chunk with `grep -n transport_drills <outdir>/chunk-0*`).

### V7 Lint (from `<W>`)
```bash
uv run ruff check Homayar/backend/tests/test_transport_drills.py Homayar/backend/tests/_transport.py && uv run ruff format --check Homayar/backend/tests/test_transport_drills.py Homayar/backend/tests/_transport.py
```

## Known baseline
At `7112407` the full suite is 0 failed / 1278 passed / 47 skipped when this drill happens to pass; it fails intermittently (HOM-163). Chunk 03 at BASE in the wp02t03 handoff:
`1 failed, 329 passed, 43 skipped` (three runs, all failing at `:424`).

## Out of scope findings (recorded, not fixed)
1. `Homayar/backend/tests/_transport.py:289-293`: `TransportProcess.stop()` documents a "Warm shutdown" but signals the whole process group, which kills an in-flight pool child
   (Context 4). Not needed for this fix (the drill now waits for the result line first). A later unit could signal only `self.pid` and let the parent drain, at the cost of up to `timeout`
   (30 s) per worker in failing teardowns with a closed gate.
2. `Homayar/backend/jobs/processor.py:1975-1982` (candidate, UNVERIFIED end to end; read from source only): the post-success audio deletion (`delete_audio_after_processing`) and the
   voicemail hook run after the COMPLETED commit (`:1220`). If a worker dies in that window, a redelivery meets a terminal job (`jobs/leases.py:142-143`; `_claim_job` returns before any hook)
   and nothing re-runs them; no sweep references `delete_audio_after_processing` (grep of non-test code: only `processor.py:208,337,1445`, the model and admin service). A possible retention gap
   (audio kept despite the workspace setting). Needs its own ticket and a probe.
3. Evidence hygiene: `/root/foreman/20260929-backend-mode-100/lanes-proof/l1/full-03.txt` is a truncated 3-line log (no summary); the usable failure logs are in
   `wp02t03-t1/handoffs/impl-suite-turn-3/`.

## Models
Implementer: sonnet (test-only, one test body; the concurrency analysis is in Context). Reviewer: opus. Final reviewer: none.

## Foreman rulings (activation, 2026-09-30)
- BASE_SHA = `7112407`. Lane L4 (`testenv-l4.sh`, port 5436, Redis dbs 8/9, `LANE=4 suite.sh`, `flock "$LANE_LOCK"`).
- Harness: the authoritative stress runner is /root/foreman/20260929-backend-mode-100/stab02-t1/stress.sh (Foreman copy;
  it now pins ONLY this worktree's transport-worker children, so other lanes are not throttled). Use that path.
- Process variant (test-only unit): no separate blind oracle. The observable acceptance checks are this SPEC's P1/P2
  probes, `stress.sh` 8/8 on the fix and the BASE control failing; Foreman runs them as gates, then an independent
  reviewer reviews the bare diff (phase C) against this SPEC.
- Q1 (`TransportProcess.stop()` not a warm stop): out of scope; recorded. Q2 (retention gap, UNVERIFIED): filed as a
  separate Linear issue needing a probe. Q3: the BASE stress control may be repeated once before being doubted.
- Parallel harness: temporary files only under /root/foreman/20260929-backend-mode-100/stab02-t1/scratch/; never delete
  anything outside your worktree and that scratch dir; never touch another lane's Redis/PostgreSQL.
- Worktrees: maker /root/foreman/20260929-backend-mode-100/wt/stab02-t1 (branch mission/stab02-t1); checker
  /root/foreman/20260929-backend-mode-100/wt/stab02-t1-check (detached).
