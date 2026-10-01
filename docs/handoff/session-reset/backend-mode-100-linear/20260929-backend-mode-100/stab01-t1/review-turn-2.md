# Review, stab01-t1 turn 2 (HOM-140 / STAB-01): re-review after fix F1

- Reviewer: stab01-t1-rev (team reviewer, phase A oracle author)
- Input: refreshed bare diff `/root/foreman/20260929-backend-mode-100/wt/stab01-t1-check/REVIEW-INPUT.diff`, against `682ffee`
- Contract: SPEC.md including Amendments A1 to A3
- Not read: the maker worktree, `handoffs/`, and `briefs/turn-2-fix.md`
- How I tested the revision: `git apply --check` passes on the checkout. I applied the diff to a throwaway copy (`git archive HEAD` extract in my scratchpad) and ran everything there with the checkout's venv. Every pytest run this turn went through `flock /root/foreman/20260929-backend-mode-100/heavy.lock` (SPEC A2), because the suite's `conftest` connects to PostgreSQL at session start. The checkout's tracked files are untouched.

## Verdict

**Mergeable as-is.** The revision closes finding 1 and changes nothing else.

- `voice/admission.py` is byte-identical to turn 1.
- The test file equals turn 1 plus one import and one new test.
- The focused suites and the full oracle pass.
- Two info notes, neither blocking:
  - A residual coverage gap on the process dimension of the retrier identity. It predates the revision and is outside the thread-level fix I asked for.
  - One new inline comment.

## Did anything else change? (verified, not read)

- **`Homayar/backend/voice/admission.py`:** blob `6533e6b9c8c3fb3912fd91380c07fa3e6a80a0c7`, the same `index 5860b4f..6533e6b` as the turn-1 diff. **Byte-identical.**
- **`Homayar/backend/tests/test_voice_admission.py`:** the new blob is `86ef798`. I removed `import threading` and the new test from the revised file and ran `git hash-object`: the result is `18afc6f2c9b4...`, exactly the turn-1 blob (`index 4cf72e8..18afc6f`). So the only changes are `+import threading` (line 5) and `test_concurrent_threads_get_distinct_identities_and_first_delays` (lines 370-390).
- No other file is touched. `test_truthful_outputs.py` is still absent from the diff (AC2).

## Finding 1 (turn 1): closed

The new test starts 12 threads behind a `threading.Barrier`, so all of them are alive at once and no thread id is reused. It then calls the real `_retrier_identity()` and asserts 12 distinct identities and 12 distinct attempt-1 delays.

This is the smallest fix I asked for. What makes it fail, verified with in-memory mutants of `_retrier_identity` run against `test_voice_admission.py` under the lock:

| Mutant | Result |
|---|---|
| unchanged | 13 passed |
| returns `"constant"` | **1 failed** (the new test) |
| drops the thread id (`host:pid`) | **1 failed** (the new test) |
| drops the pid (`host:thread`) | 13 passed (see finding T2-1) |
| drops the host (`pid:thread`) | 13 passed (cannot be observed in one process; acceptable) |

- **Flakiness:** 30 consecutive isolated runs of the new test gave 30 passed. The distinct-delay assertion needs two of 12 hash-derived 52-bit spreads to collide, about 1e-14 per run.

## Findings

### T2-1. info, pre-existing (turn-1 design, residual of F1), Homayar/backend/voice/admission.py:145 against Homayar/backend/tests/test_voice_admission.py:370-390: no test pins the pid in the identity

- **What I found.** The pid is the only thing that separates prefork workers.
- **Verified by probe.** Three children made with `os.fork()` all had the parent's `threading.get_ident()` (`140411796582528`). Only `os.getpid()` made their identities distinct (3 of 3 distinct), and it gave 3 different attempt-1 delays.
- **Why it matters.** Gunicorn or uvicorn `--workers` and Celery prefork workers are exactly this case. An edit that drops `os.getpid()` would silently bring back lockstep retries across workers, and nothing would fail: that mutant gives 13 passed.
- **Relation to this revision.** It does not come from the revision. The turn-1 design already had this dimension untested, and my turn-1 smallest fix only asked for the thread case.
- **Smallest fix, optional.** One test that collects `_retrier_identity()` from two forked children, for example `multiprocessing.get_context("fork")` with a pipe, and asserts they differ.

### T2-2. info, Homayar/backend/tests/test_voice_admission.py:377: a new inline comment

- The comment is `# every thread is alive at once, so no thread id is reused`.
- It is the same class as turn-1 finding 5, which Foreman recorded. The neighbouring style allows it.
- **Smallest fix, optional.** Drop the comment.

**Turn-1 findings 2 to 5** are closed by SPEC amendments A3, A1 and A2 and by Foreman's record. They are not re-counted here.

## Spec conformance

| AC | Status | Evidence |
|---|---|---|
| 1. Guard passes | Met | The oracle AC1 cases pass in the patched copy. |
| 2. Guard not weakened | Met | `test_truthful_outputs.py` is not in the diff, and the oracle AC2 cases pass. |
| 3. No substitute randomness | Met | The oracle AC3 cases pass. `admission.py` is unchanged from turn 1, which amendment A1 accepts. |
| 4. Bounded retries that still back off, within (0, 0.075] | Met | All oracle AC4 cases pass, and the maker's four `_retrying` tests are unchanged. |
| 5. Existing voice suites pass | Met | See the run below. |
| 6. Full suite, no new failure | Not verified by me | Foreman's turn-2 gate, run with nothing else running: 0 failed / 1055 passed / 46 skipped. That is consistent with turn 1's 1054 plus the one new test. |

The run for AC5, in the patched copy under `flock heavy.lock`:

```
pytest backend/tests/test_truthful_outputs.py backend/tests/test_voice_admission.py backend/tests/test_voice_api.py backend/tests/test_oracle_stab01.py -q -p no:cacheprovider
81 passed, 6 warnings in 11.46s
```

That is 80 from turn 1 plus the new test.

Lint on both changed files: `ruff check` gives "All checks passed!" and `ruff format --check` gives "2 files already formatted".

## Confirmed by the oracle

- The oracle is unchanged (sha256 `9576ef54...5d0d7`).
- Foreman's turn-2 record shows 56 passed on the patch.
- My independent run in the patched copy under the lock also passed all 56. They are included in the `81 passed` above.
- Red on base, green on the revision: `test_guard_passes_on_the_production_tree`, `test_only_the_seed_module_imports_random`, `test_admission_has_no_randomness_source` and `test_lock_contention_retries_draw_no_randomness`.
- The 52 controls are still green.

## Not verified

- AC6: I rely on Foreman's turn-2 gate (1055 passed). I did not run the full suite.
- The fork probe ran on this host only. I did not observe real gunicorn or Celery deployments.

CANDIDATES critical=0 high=0 medium=0 low=0 info=2
