# Handoff — stab01-t1, phase B, turn 1 (stab01-t1-impl)

Mission 20260929-backend-mode-100 · Linear HOM-140 [STAB-01] · maker worktree
`/root/foreman/20260929-backend-mode-100/wt/stab01-t1` (branch `mission/stab01-t1` at 682ffee).
Nothing was committed. The change is left uncommitted in the worktree.

## Root cause
`Homayar/backend/voice/admission.py` imported `random` (line 27) to jitter the sleep between
lock-contention retries in `VoiceControlPlane._retrying` (line 165:
`time.sleep(min(0.05, 0.001 * attempt) * (0.5 + random.random()))`). The WP04/F23 guard
`test_no_random_numbers_outside_the_allowlist` flags any production module other than
`database/seed.py` that imports `random`, so it fails with offender `voice/admission.py`.
(The SPEC calls the class `VoiceAdmissionService`. In the code it is `VoiceControlPlane`. The line
numbers and the code match the SPEC exactly, so I treated this as a naming slip, not a contradiction.)

## What changed
### `Homayar/backend/voice/admission.py` (+21 / -2)
- Removed `import random` and added `hashlib`, `os` and `platform` (all stdlib).
- New `_contention_backoff(attempt, retrier) -> float`. The base delay is unchanged:
  `min(0.05, 0.001 * attempt)`. It is multiplied by a factor in [0.5, 1.5). The factor comes from
  a blake2b hash of `f"{retrier}:{attempt}"`, keeping 52 bits, so it lies in [0, 1) + 0.5 and never
  reaches 1.5. The function is pure: the same retrier on the same attempt always gets the same
  delay.
- New `_retrier_identity() -> str` returns `f"{platform.node()}:{os.getpid()}:{threading.get_ident()}"`,
  the host, process and thread of the caller. It is read when the retry happens, not at import, so
  forked workers (Celery prefork, gunicorn) get their own pid.
  - The thread part separates threads within one process. The in-process `_local_lock` covers only
    `admit`. `transition`, `heartbeat` and the other methods retry concurrently, as in the load
    harness.
  - The pid separates processes on one host.
  - The host separates replicas that share a pid, such as pid 1 in containers.
- The retry line is now `time.sleep(_contention_backoff(attempt, _retrier_identity()))`. It still
  goes through `time.sleep`, so tests that patch the module's `time.sleep` keep working. The stale
  `# noqa: S311` was removed.
- Unchanged:
  - The loop structure, the `attempt > max_retries` bound and `_is_lock_contention`.
  - A non-contention `OperationalError` still re-raises immediately without sleeping.
  - `IntegrityError` handling (bounded, no sleep).
  - `BaseException` rollback.
  - The `_retrying(db, work)` signature.
- Why deterministic de-synchronization instead of dropping jitter: under SQLite WAL, the load
  harness's 200 concurrent `transition` calls do hit immediate `database is locked` on read-to-write
  upgrades. Without a per-retrier factor, retriers that failed together would retry in lockstep.
- Why this is not substitute randomness (AC3):
  - It uses no `random`, `secrets`, `numpy.random`, `os.urandom`, `SystemRandom`, `uuid` or clock
    bits.
  - Its only inputs are the caller's identity and the attempt number, and the hash is a fixed
    mixing function.
  - A grep for `random|secrets|urandom|SystemRandom|uuid` in the file returns nothing, including
    comments.

### `Homayar/backend/tests/test_voice_admission.py` (+101)
The existing tests did not pin AC4. I added five unit tests. They use a fake session that counts
commits and rollbacks, a `sleeps` fixture that patches `admission.time.sleep`, and SQLAlchemy
`OperationalError`/`IntegrityError` wrapping sqlite3 errors.
- `test_persistent_lock_contention_backs_off_then_raises`: with `max_retries=5` and a lock that
  stays contended, the loop raises after 6 work calls and 5 sleeps. Every sleep is in (0, 0.075],
  and the session sees 0 commits and 6 rollbacks. (DENY)
- `test_lock_contention_clearing_at_max_retries_succeeds`: contention clears after 5 failures, the
  loop returns "done" after 5 sleeps within bounds, and the session sees 1 commit. (ALLOW-CONTROL)
- `test_non_contention_operational_error_is_not_retried`: "no such table" raises on the first call
  with no sleep. (DENY)
- `test_integrity_error_retries_are_bounded_without_sleeping`: with `max_retries=2`, the loop raises
  after 3 calls with no sleep. This shows the `IntegrityError` behavior is unchanged.
- `test_contention_backoff_is_bounded_deterministic_and_desynchronized`: for attempts 1..250 and 8
  retrier identities, each delay is in [0.5*base, 1.5*base) and in (0, 0.075], and repeat calls give
  the same value. The 8 identities give 8 distinct delays at attempt 1.

The first four tests are characterization tests. They were written first and **passed on the
unmodified base**, which confirms they pin existing behavior. The fifth was red on the base before
the fix.

## Verification (all run from `/root/foreman/20260929-backend-mode-100/wt/stab01-t1/Homayar`
after `source /root/foreman/20260929-backend-mode-100/testenv.sh`)

### 1. Reproduction on the base (before any change)
```
$ uv run pytest backend/tests/test_truthful_outputs.py backend/tests/test_voice_admission.py backend/tests/test_voice_api.py -q -p no:cacheprovider
.......F...........                                                      [100%]
=================================== FAILURES ===================================
_________________ test_no_random_numbers_outside_the_allowlist _________________

    def test_no_random_numbers_outside_the_allowlist() -> None:
        offenders = []
        for path in BACKEND_ROOT.rglob("*.py"):
            relative = path.relative_to(BACKEND_ROOT).as_posix()
            if relative.startswith(("tests/", ".venv/")) or "/.venv/" in relative:
                continue
            if relative in RANDOM_ALLOWLIST:
                continue
            if RANDOM_PATTERN.search(path.read_text(encoding="utf-8")):
                offenders.append(relative)
>       assert offenders == [], f"random-number use outside the allowlist: {offenders}"
E       AssertionError: random-number use outside the allowlist: ['voice/admission.py']
E       assert ['voice/admission.py'] == []
E         
E         Left contains one more item: 'voice/admission.py'
E         Use -v to get more diff

backend/tests/test_truthful_outputs.py:56: AssertionError
=========================== short test summary info ============================
FAILED backend/tests/test_truthful_outputs.py::test_no_random_numbers_outside_the_allowlist
1 failed, 18 passed, 6 warnings in 8.15s
```

### 2. AC4 characterization tests on the base code (tests added, source not yet changed)
```
$ uv run pytest backend/tests/test_voice_admission.py -q -p no:cacheprovider -k "contention or integrity"
....                                                                     [100%]
4 passed, 9 deselected in 0.06s
```

### 3. New backoff test, red on the base
```
$ uv run pytest backend/tests/test_voice_admission.py -q -p no:cacheprovider -k "backoff"
>               delay = admission_module._contention_backoff(attempt, retrier)
                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E               AttributeError: module 'backend.voice.admission' has no attribute '_contention_backoff'

backend/tests/test_voice_admission.py:361: AttributeError
=========================== short test summary info ============================
FAILED backend/tests/test_voice_admission.py::test_contention_backoff_is_bounded_deterministic_and_desynchronized
1 failed, 13 deselected in 0.12s
```

### 4. Focused command after the fix
```
$ uv run pytest backend/tests/test_truthful_outputs.py backend/tests/test_voice_admission.py backend/tests/test_voice_api.py -q -p no:cacheprovider
........................                                                 [100%]
24 passed, 6 warnings in 8.20s
```
The 6 warnings are the same count as on the base. The concurrent cases were repeated 3 times:
```
$ uv run pytest backend/tests/test_voice_admission.py -q -p no:cacheprovider -k "concurrent"   (x3)
2 passed, 12 deselected in 4.01s
2 passed, 12 deselected in 3.89s
2 passed, 12 deselected in 3.98s
```

### 5. Lint on changed files
```
$ uv run ruff check backend/voice/admission.py backend/tests/test_voice_admission.py && uv run ruff format --check backend/voice/admission.py backend/tests/test_voice_admission.py
All checks passed!
2 files already formatted
```
Extra check, not a gate here: `uv run mypy backend/voice/admission.py` reports "Found 28 errors in 5 files" both
before (stashed) and after the change. The 4 hits in `voice/admission.py` are the existing
`held_by_node` annotations on lines that were not touched, now at lines 328/329/610/611 (base
308/309/590/591). The change adds no new mypy error.

### 6. AC2 / AC3 / allow-control
```
$ git diff --quiet -- backend/tests/test_truthful_outputs.py && echo "test_truthful_outputs.py unchanged"
test_truthful_outputs.py unchanged
$ grep -niE "random|secrets|urandom|SystemRandom|uuid" backend/voice/admission.py || echo "AC3: no randomness-source tokens in admission.py"
AC3: no randomness-source tokens in admission.py
$ grep -n "^import random\|^from random" backend/database/seed.py
3:import random
```

### 7. Full suite under the mission lock (PostgreSQL)
```
$ flock /root/foreman/20260929-backend-mode-100/heavy.lock uv run pytest backend/tests -q -p no:cacheprovider -rs
... (all dots / s) ...
=========================== short test summary info ============================
SKIPPED [3] backend/tests/test_provider_contracts.py: Padyar AI Gateway API contract not supplied (see padyar.py)
SKIPPED [19] backend/tests/test_storage_contract.py: NOT_RUN/BLOCKED_EXTERNAL: HOMAYAR_LIVE_S3_TEST is not 1 (live S3 run is opt-in)
SKIPPED [2] backend/tests/test_storage_contract.py:639: NOT_RUN/BLOCKED_EXTERNAL: HOMAYAR_LIVE_S3_TEST is not 1 (live S3 run is opt-in)
SKIPPED [2] backend/tests/test_storage_contract.py:654: NOT_RUN/BLOCKED_EXTERNAL: HOMAYAR_LIVE_S3_TEST is not 1 (live S3 run is opt-in)
SKIPPED [2] backend/tests/test_storage_contract.py:682: NOT_RUN/BLOCKED_EXTERNAL: HOMAYAR_LIVE_S3_TEST is not 1 (live S3 run is opt-in)
SKIPPED [2] backend/tests/test_storage_contract.py:766: NOT_RUN/BLOCKED_EXTERNAL: HOMAYAR_LIVE_S3_TEST is not 1 (live S3 run is opt-in)
SKIPPED [8] backend/tests/test_storage_contract.py:838: NOT_RUN/BLOCKED_EXTERNAL: HOMAYAR_LIVE_S3_TEST is not 1 (live S3 run is opt-in)
SKIPPED [6] backend/tests/test_storage_contract.py:864: NOT_RUN/BLOCKED_EXTERNAL: HOMAYAR_LIVE_S3_TEST is not 1 (live S3 run is opt-in)
SKIPPED [1] backend/tests/test_tombstone_reapply.py:209: file-level backup/restore drill runs on SQLite; PostgreSQL uses the simulated restore
SKIPPED [1] backend/tests/test_transport_drills.py:433: requires TEST_REDIS_CONTAINER and the docker CLI
1054 passed, 46 skipped, 1411 warnings in 574.48s (0:09:34)
```
exit=0. **0 failed, 1054 passed, 46 skipped.** The base was 1 failed, 1048 passed, 46 skipped. The
difference is 1048 + 1 (the guard now passes) + 5 new tests = 1054. The skips are unchanged.

## Acceptance criteria status
- AC1: the guard passes.
- AC2: `test_truthful_outputs.py` is untouched (git diff is empty).
- AC3: no substitute randomness source (see step 6).
- AC4: pinned by 4 behavior tests and 1 helper test. The maximum delay is
  `0.05 * 1.4999999999999998 == 0.075`, which is ≤ 0.075.
  - A 53-bit spread would round `0.5 + spread` to 1.5, giving `0.07500000000000001`. That is why the
    code keeps 52 bits.
  - The base's `random.random()` could in principle hit the same 1-in-2^53 edge.
- AC5: the voice suites pass (24/24 focused).
- AC6: 0 failed, 1054 passed.

## Risks / notes for review
- Retriers can still coincide only if host name, pid, thread ident and attempt all match, or if the
  64-bit hash collides. Both are negligible.
- The delay is now reproducible for a given retrier. That is intended (truthful outputs), and the
  bounds match the base.
