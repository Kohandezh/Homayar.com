# Review, stab01-t1 turn 1 (HOM-140 / STAB-01)

- Reviewer: stab01-t1-rev (team reviewer; I wrote the phase A oracle blind)
- Input: `/root/foreman/20260929-backend-mode-100/wt/stab01-t1-check/REVIEW-INPUT.diff`, a bare diff against `682ffee`
- Contract: `/root/foreman/20260929-backend-mode-100/stab01-t1/SPEC.md`
- Not read: the maker worktree and `handoffs/`. From `briefs/impl-turn-1.md` I read only lines 22-28, to locate the "call-specific inputs" wording that the task block attributes to the SPEC.
- How I tested the patch: `git apply --check` passes on the checkout. I applied the diff to a throwaway copy (`git archive HEAD` extracted under my scratchpad) and ran tests there with the checkout's venv. The checkout's tracked files are untouched; `git status` shows only `?? Homayar/backend/tests/test_oracle_stab01.py` and `?? REVIEW-INPUT.diff`.

## Verdict

**Mergeable as-is.** The patch is one root cause in the two files the maker may change.

- It removes `import random` from `backend/voice/admission.py`.
- It replaces the jitter with a deterministic factor in [0.5, 1.5). The factor is a blake2b hash of the retrier's identity (host, pid, thread id) and the attempt number.
- AC1 to AC4 hold. They are backed by all 4 of my red-on-base oracle cases going green, with all 52 controls still green.
- The retry policy is unchanged apart from the delay value: same budget, same classification, same IntegrityError path. The new delay stays inside the base bounds, and is in fact strictly tighter at the floating-point edge.
- What remains is two low findings and three info findings. None of them blocks the merge.

## Findings

### 1. low, Homayar/backend/tests/test_voice_admission.py:356-366: the de-synchronization that justifies `_retrier_identity` is not covered by any test

- **Why it matters.** The only reason for the new identity plumbing (`platform`, `os`, `threading.get_ident()` at admission.py:143-145) is that concurrent retriers should not wake together. `test_contention_backoff_is_bounded_deterministic_and_desynchronized` feeds hand-made strings (`"a:1:7"` and so on) to `_contention_backoff`, and no test calls `_retrier_identity()`. A later edit that collapses the identity would silently bring back lockstep retries.
- **Trigger, verified.** I executed an in-memory mutant where `_retrier_identity` returns `"constant"`. The maker's tests plus my oracle ran with the two load tests and the three disk-scan cases deselected: `65 passed, 5 deselected`. Nothing catches it.
- **The behaviour itself is correct today, verified by probe:**
  - 16 threads released together by a barrier gave 16 distinct identities and 16 distinct attempt-1 delays, from 0.00057 to 0.00136 s.
  - 4 separate processes gave 4 distinct identities and delays.
- **Smallest fix.** Add one test that calls `_retrier_identity()` from N threads released together by a `threading.Barrier`, and asserts N distinct identities, or N distinct `_contention_backoff(1, identity)` values.

### 2. low, Homayar/backend/voice/admission.py:143-145, 185: the delay is now a pure function of (host, pid, thread id, attempt), so retriers that share all three retry in lockstep

- **Why it matters.** This is a behaviour change the SPEC never mentions. The base drew a fresh `random.random()` on every sleep, so it had no collision mode. Two retriers with the same host name, the same pid (for example PID 1 in two containers) and the same thread id now get the same delay sequence and wake together on every attempt.
- **How bad it gets.**
  - Collision needs an identical `pthread_self` address as well. That address is ASLR-randomised: in my probe, main-thread ids differed in all 4 processes.
  - The router endpoints are sync `def` handlers (routers/v1/voice.py:175 onward), so they run on threadpool threads with distinct ids.
  - Even in a collision the loop stays bounded by `max_retries`. The worst case is an `OperationalError` surfacing after `max_retries` retries. Correctness is unaffected: the capacity counts are still guarded by the admission lock.
- **Trigger.** Replicas started with an operator-pinned identical hostname, running the app as the same pid, with ASLR disabled (`kernel.randomize_va_space=0`).
- **Smallest fix.** None required for this ticket. If you want the collision mode closed, the options are to accept it on the record or to add a per-process distinguisher that is not an RNG. Filed so the trade-off is explicit.

### 3. info, SPEC.md (AC3) against the review task block: the "call-specific inputs" allowance is not in the SPEC

- **What I found.** The task block says the SPEC reads "a deterministic de-synchronization derived from call-specific inputs is acceptable". SPEC.md contains no such sentence. It appears only in the maker's brief (`briefs/impl-turn-1.md:24-26`).
- **My AC3 judgment.** The patch passes AC3 on the SPEC's own list: no `random`, `secrets`, `numpy.random`, `os.urandom`, `SystemRandom` or `uuid`. `os` is imported for `getpid` only (admission.py:28, 145).
  - I do not count hashing host, pid and thread id as a substitute randomness source. It is a deterministic function (blake2b) of execution-context identifiers, not an RNG API, and the value is only ever a sleep duration. It is never returned, stored or shown as data, which is the WP04/F23 concern.
  - The inputs are retrier-specific, not call-specific. I judge that within the brief's stated purpose ("de-synchronize concurrent retriers within one process and across processes"). It is also the better key: `_retrying` is shared by `admit`, `transition`, `heartbeat`, `recover` and `register_node`, and two processes can retry the same call or node at once, so a call-derived key could collide where a retrier key cannot.
  - Caveat: thread ids carry ASLR entropy, so delay sequences are not reproducible across restarts. That is harmless for a sleep, but the docstring's "same inputs always give the same delay" holds only at function level.
- **Smallest fix.** Foreman records the allowance, and this reading of it, in SPEC.md or the decision record, so the AC3 acceptance does not rest on a brief the reviewer contract never contained.

### 4. info, pre-existing, not in the diff (SPEC "Verification commands"): the "focused, no lock needed" command hits the shared PostgreSQL database

- **What happened.**
  - `test_voice_api.py` runs against `TEST_DATABASE_URL`.
  - My first, unlocked focused run in the patched copy (about 10:18 UTC) overlapped Foreman's locked full-suite run (pid 588, maker worktree). It failed `test_voice_call_lifecycle_capacity_and_nodes` with `psycopg.errors.UniqueViolation: duplicate key value violates unique constraint "pg_type_typname_nsp_index" ... (users, 2200)` during `CREATE TABLE users`. That is a schema-creation race, not the patch.
  - My re-run under `heavy.lock` is recorded in the AC table below.
- **Why it matters.** The overlap may also have disturbed Foreman's own gate run during that window.
- **Smallest fix.** Run the focused command under `flock heavy.lock` too, or split its PostgreSQL-backed file out of it. Foreman should check its turn-1 full-suite result for failures in that window, and re-run it if there are any.

### 5. info, Homayar/backend/voice/admission.py:131-136, 139, 144 and Homayar/backend/tests/test_voice_admission.py:271, 322, 365: new docstrings and inline comments

- The patch adds two docstrings, one inline comment in production code (`# 52 bits: [0, 1), never 1.0`), a section banner and two inline comments in the test.
- Both files already use the same style (`# -- infrastructure ---`, `# both empty: tie -> ...`), and the Python guidance for this mission is to follow the neighbours. So this is recorded against the "no comments" convention, not raised as a defect.
- **Smallest fix, optional.** Drop the three inline comments.

## Spec conformance

| AC | Status | Evidence |
|---|---|---|
| 1. Guard passes | Met | Oracle `test_guard_passes_on_the_production_tree` and `test_only_the_seed_module_imports_random` went red on base, green on patch. Re-run in the patched copy: green. |
| 2. Guard not weakened | Met | `test_truthful_outputs.py` is not in the diff. Oracle `test_guard_constants_are_unchanged_from_base`, the `test_guard_rejects_*` cases and the allow cases stay green. |
| 3. No substitute randomness | Met, with the reading in finding 3 | Oracle `test_admission_has_no_randomness_source` and `test_lock_contention_retries_draw_no_randomness` went red to green. My reading of the diff agrees. |
| 4. Bounded retries that still back off, within (0, 0.075] | Met | All 27 oracle AC4 cases green: spellings, non-contention, budget boundaries at 0/1/7, 200 default-policy delays, IntegrityError, and 3 public `admit` cases. By arithmetic, `(x>>12)/2**52 <= 1-2**-52`, so the worst case is `0.05*(1.5-2**-52) == 0.075` exactly (computed). The base's own worst case, `0.05*1.5 == 0.07500000000000001`, could exceed the bound, so the patch is strictly tighter. Probe over 40,000 samples: 0.000504 to 0.074998 s. |
| 5. Existing voice suites pass | Met | In the patched copy, under `flock heavy.lock`, `pytest backend/tests/test_truthful_outputs.py backend/tests/test_voice_admission.py backend/tests/test_voice_api.py backend/tests/test_oracle_stab01.py -q -p no:cacheprovider` gave `80 passed, 6 warnings in 9.84s`, exit 0. That covers the concurrent cap cases (250 admissions, per-node exact) and the 5 new maker tests. An earlier unlocked run showed 1 failure, a PostgreSQL schema race caused by the overlap (finding 4). |
| 6. Full suite, no new failure | Not verified by me | Maker-reported 0 failed / 1054 passed / 46 skipped, which matches the arithmetic: 1048 + 1 fixed + 5 new tests. Foreman re-runs the gate. See finding 4 about the overlap. |

**What else changed** (inputs to outputs):

- For a lock-contention `OperationalError` on attempt k, the base slept `min(0.05, 0.001k) * U[0.5, 1.5)` and the patch sleeps `min(0.05, 0.001k) * f(identity, k)`, where f lies in [0.5, 1.5). Same envelope; the only difference is determinism (finding 2).
- Budget, classification (`_is_lock_contention` untouched), rollback, the IntegrityError path and the `BaseException` path are all untouched.
- No schema, config, router or public API change. `ruff check` and `ruff format --check` pass on both changed files in the patched copy ("All checks passed!", "2 files already formatted").

**Can it be walked around?** The guard pattern still catches every `random` import spelling, per the oracle synthetic-tree cases. Moving randomness into another module would be caught by `test_only_the_seed_module_imports_random`. Randomness via `secrets`, `os.urandom` or `uuid` inside admission.py would be caught by the oracle scanner and the runtime patch test; I confirmed this with a phase A mutant.

**One root cause?** Yes. It removes the only offender and adds retry-policy tests. File ownership is respected: only `voice/admission.py` and `tests/test_voice_admission.py` change.

**Evidence quality of the maker's tests.**

- The four `_retrying` tests pin the budget boundary, the sleep count and bounds, no retry for non-contention errors, and IntegrityError without sleeping. They mirror the SPEC deny/allow pairs and would fail on the phase A mutants.
- The fifth test checks bounds and determinism for synthetic identities only (finding 1).

## Confirmed by the oracle

- Foreman's record `oracle/red-to-green.md` uses an oracle whose sha256 `9576ef54...5d0d7` matches my checkout copy byte for byte. It shows base 4 failed / 52 passed and patch 56 passed.
- **Red on base, green on patch:**
  - `test_guard_passes_on_the_production_tree` (AC1)
  - `test_only_the_seed_module_imports_random` (AC1)
  - `test_admission_has_no_randomness_source` (AC3)
  - `test_lock_contention_retries_draw_no_randomness` (AC3)
- **Green on both:** 52 controls (AC2 guard strength, scanner self-validation, AC4 retry policy through `_retrying` and through `admit`).
- **Independent re-run in my patched copy:** under `heavy.lock`, all 56 oracle cases passed inside the `80 passed` focused run.

## Not verified

- AC6 (full PostgreSQL suite): I did not run it, and I rely on Foreman's gate. The 1054 figure is the maker's claim.
- The collision likelihood in finding 2 (same hostname, pid and thread id) is reasoned from ASLR behaviour plus a 4-process probe on this host. I did not test it on real container deployments.
- I did not measure whether identity-based de-synchronization performs as well as random jitter under real PostgreSQL deadlock storms. AC4 does not require it.
- I did not re-run `test_voice_api.py` under a real concurrent multi-process PostgreSQL workload beyond the suite's own cases.

CANDIDATES critical=0 high=0 medium=0 low=2 info=3
