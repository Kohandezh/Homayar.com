# Phase A oracle record: stab01-t1 (HOM-140 / STAB-01)

- Mission: 20260929-backend-mode-100
- Author: stab01-t1-rev (independent reviewer), phase A, turn 1
- Derived from: `/root/foreman/20260929-backend-mode-100/stab01-t1/SPEC.md` only. I did not read the maker worktree, any other branch, or `handoffs/`.
- Checkout: `/root/foreman/20260929-backend-mode-100/wt/stab01-t1-check`, detached at `682ffee`. The only change in the tree is the untracked oracle file (`git status --short` prints `?? Homayar/backend/tests/test_oracle_stab01.py`).
- Oracle file: `/root/foreman/20260929-backend-mode-100/wt/stab01-t1-check/Homayar/backend/tests/test_oracle_stab01.py` (56 cases; passes `ruff check` and `ruff format --check`)

## Command and real output (clean base 682ffee)

```
cd /root/foreman/20260929-backend-mode-100/wt/stab01-t1-check/Homayar
source /root/foreman/20260929-backend-mode-100/testenv.sh
uv run pytest backend/tests/test_oracle_stab01.py -q -p no:cacheprovider
```

```
FF........F.................F...........................                 [100%]
...
E       assert ['voice/admission.py'] == []
E         Left contains one more item: 'voice/admission.py'
...
E       AssertionError: assert ['database/se...admission.py'] == ['database/seed.py']
E         Left contains one more item: 'voice/admission.py'
...
E       AssertionError: assert ['module rand...'name random'] == []
E         Left contains 4 more items, first extra item: 'module random'
...
backend/voice/admission.py:165: in _retrying
    time.sleep(min(0.05, 0.001 * attempt) * (0.5 + random.random()))  # noqa: S311
E       AssertionError: lock-contention retry drew randomness from random.random
=========================== short test summary info ============================
FAILED backend/tests/test_oracle_stab01.py::test_guard_passes_on_the_production_tree
FAILED backend/tests/test_oracle_stab01.py::test_only_the_seed_module_imports_random
FAILED backend/tests/test_oracle_stab01.py::test_admission_has_no_randomness_source
FAILED backend/tests/test_oracle_stab01.py::test_lock_contention_retries_draw_no_randomness
4 failed, 52 passed in 0.82s
exit=1
```

On the base, the scan of `voice/admission.py` finds `['module random', 'alias random', 'attribute random', 'name random']`.

## Red on base (4): the acceptance cases for the defect

| Case | Why it is red on base |
|---|---|
| `test_guard_passes_on_the_production_tree` | Calls the real guard `test_no_random_numbers_outside_the_allowlist`. Offender: `voice/admission.py`. |
| `test_only_the_seed_module_imports_random` | Runs its own scan with the base literals for the pattern and allowlist, so it does not depend on the guard file. Result is `['database/seed.py', 'voice/admission.py']`; expected `['database/seed.py']`. |
| `test_admission_has_no_randomness_source` | An AST scan of `admission.py` for `random`, `secrets`, `numpy.random` and `uuid` imports, including dynamic `import_module` and `__import__` strings, and for the names or attributes `random`, `urandom`, `SystemRandom`, `getrandbits`, `randbelow`, `randbits`, `token_*`, `uuid1` and `uuid4`. |
| `test_lock_contention_retries_draw_no_randomness` | Behavioural. Patches the `random`, `random.Random`, `random.SystemRandom`, `secrets`, `os.urandom` and `uuid.uuid1/4` draw functions to raise, then drives two lock-contention failures and a success through `_retrying`. The base calls `random.random` at `admission.py:165`. |

## Green on base (52): regression controls, all intended

- **Guard not weakened (AC2 and the SPEC's guard deny/allow pair):**
  - `test_guard_constants_are_unchanged_from_base` checks the AST source segments and the runtime values of `RANDOM_ALLOWLIST` and `RANDOM_PATTERN`.
  - `test_seed_module_still_imports_random` is the allow-control on the real tree.
  - `test_guard_allows_random_in_the_seed_module_only` is the allow-control: a synthetic tree with `seed.py` and `tests/` importing random still passes.
  - `test_guard_rejects_random_outside_the_seed_module[import, import-as, from-import, indented-import, other-module]` are the deny cases: the guard function, pointed at a synthetic tree, must fail and name the offender. This also catches a guard-body weakening such as skipping `voice/`, and it catches randomness moved into a new helper module.
- **Scanner self-validation (so the AC3 scan cannot pass vacuously):**
  - `test_randomness_scan_flags_each_forbidden_spelling[16 spellings]` are the deny cases.
  - `test_randomness_scan_allows_deterministic_backoff` is the allow-control.
- **Retry policy (AC4). The base already satisfies it, so these guard against a wrong fix:**
  - `test_lock_contention_is_retried_after_a_sleep[7 spellings]` is the allow-control: the four markers, upper and mixed case, and a multi-line PostgreSQL message. It asserts exactly one sleep between the two attempts and `0 < delay <= 0.075`.
  - `test_non_contention_operational_error_is_raised_at_once_without_sleep[4]` is the deny case: server closed, connection refused, no such table, and `orig=None`. It asserts the same exception object is raised, `work` runs once, and there is no sleep.
  - `test_contention_clearing_at_the_last_allowed_retry_succeeds[0,1,7]` is the allow-control: `max_retries` contention failures, then success, with one sleep before every retry and none before the first attempt.
  - `test_contention_beyond_max_retries_raises[0,1,7]` is the deny case: `max_retries+1` failures raise after exactly `max_retries+1` attempts and `max_retries` sleeps, with no sleep after the last failure.
  - `test_default_policy_keeps_every_contention_delay_in_bounds` drives the default policy (`max_retries`=200) through all 200 delays and asserts each one is in `(0, 0.075]` and numeric.
  - `test_integrity_errors_within_budget_retry_without_sleep[0,1,7]` and `test_integrity_errors_beyond_budget_raise[0,1,7]` check that IntegrityError handling is unchanged: it shares the budget and never sleeps.
- **Public-surface AC4, through `VoiceControlPlane.admit` on SQLite, with `commit` failing:**
  - `test_admission_rides_out_transient_lock_contention` is the allow case: the call is admitted, with 2 sleeps in bounds and exactly 1 slot held.
  - `test_admission_gives_up_when_lock_contention_outlasts_the_budget` is a deny case: `max_retries=3` gives 4 attempts, 3 sleeps, a raise, and 0 slots held.
  - `test_admission_does_not_retry_a_non_contention_error` is a deny case: 1 attempt, no sleep, a raise, and 0 slots held.

## Oracle validation against in-memory mutants (no files written)

I did not touch any source file for this. A stdin Python harness `exec`ed a mutated copy of `admission.py` into `sys.modules`, then ran the oracle with the three disk-scan cases deselected. The scanner and the base pattern were also run on the mutated text.

| Mutant | Result |
|---|---|
| `fix`: drop `import random`, sleep `min(0.05, 0.001*attempt)` | 53 passed. Scan `[]`, pattern no match. **No false red.** |
| `fix_from_time`: `from time import sleep`, deterministic attempt-based spread | 53 passed. The sleep fixture catches module aliases of `time.sleep`. |
| `nosleep` | 15 failed (every contention and bounds case) |
| `secrets`: `secrets.randbelow` jitter | 1 failed (runtime randomness). Scan: `['module secrets', 'attribute randbelow']` |
| `toolong`: `time.sleep(0.1)` | 15 failed (bounds) |
| `offbyone`: `>=` on the OperationalError budget | 6 failed (boundaries at 1 and 7, the default policy, public give-up) |
| `retry_all`: contention check removed | 5 failed (all non-contention denies) |
| `integrity_sleep`: sleep added to the IntegrityError branch | 4 failed (integrity budgets 1 and 7) |

## Acceptance criteria and covering cases

1. AC1, the guard passes: `test_guard_passes_on_the_production_tree` and `test_only_the_seed_module_imports_random` (both red on base).
2. AC2, the guard is not weakened: `test_guard_constants_are_unchanged_from_base`, `test_guard_rejects_random_outside_the_seed_module[*]` and `test_guard_allows_random_in_the_seed_module_only` (green controls).
3. AC3, no substitute randomness in `admission.py`: `test_admission_has_no_randomness_source` (red) and `test_lock_contention_retries_draw_no_randomness` (red), backed by `test_randomness_scan_flags_each_forbidden_spelling[*]` and `test_randomness_scan_allows_deterministic_backoff`.
4. AC4, retries are bounded, still back off, and each delay is in (0, 0.075]; non-contention errors re-raise at once; IntegrityError is unchanged: `test_lock_contention_is_retried_after_a_sleep[*]`, `test_contention_clearing_at_the_last_allowed_retry_succeeds[*]`, `test_contention_beyond_max_retries_raises[*]`, `test_default_policy_keeps_every_contention_delay_in_bounds`, `test_non_contention_operational_error_is_raised_at_once_without_sleep[*]`, `test_integrity_errors_*[*]`, and the three `test_admission_*` public-surface cases (all green on base, which already satisfies it).
5. AC5, the existing voice suites pass: **no oracle case.** This is covered by the SPEC's focused command (`test_voice_admission.py`, `test_voice_api.py`), which Foreman or the maker runs.
6. AC6, no new failure in the full PostgreSQL suite: **no oracle case.** This is covered by the SPEC's full-suite command under `heavy.lock`.

SPEC deny-case and allow-control pairs:
- Guard: `test_guard_rejects_random_outside_the_seed_module[*]` is paired with `test_guard_allows_random_in_the_seed_module_only` and `test_seed_module_still_imports_random`.
- Non-contention OperationalError: `test_non_contention_*` and `test_admission_does_not_retry_*` are paired with `test_lock_contention_is_retried_after_a_sleep[*]` and `test_admission_rides_out_*`.
- Budget: `test_contention_beyond_max_retries_raises[*]` and `test_admission_gives_up_*` are paired with `test_contention_clearing_at_the_last_allowed_retry_succeeds[*]`.

## Interpretations and limits (for Foreman and phase C)

- **Boundary.** "Raises after `policy.max_retries` attempts" is read as the base semantics: `max_retries` retries after the first attempt, so `max_retries+1` calls to `work` and `max_retries` sleeps. This matches the SPEC's allow-control, "contention clearing at attempt `max_retries` -> succeeds".
- **"Still back off"** is read as the SPEC's own list: a sleep before every retry, each within `(0, 0.075]`. The oracle does not require delays to grow or to be jittered, so a constant delay inside the bound passes. Whether dropping jitter re-synchronises retries across processes is a design point for phase C, not an AC.
- **`uuid` check.** Any `uuid` import in `admission.py` is flagged, which is stricter than "uuid-derived numeric jitter". Jitter derived from an existing id value (for example `call.id`) without an import cannot be detected statically; phase C must read the diff for it. The same applies to other entropy sources the SPEC does not list, such as `hash()`, thread ids or `perf_counter_ns`.
- **Surface used.** 25 retry-policy cases drive `VoiceControlPlane._retrying(db, work)`, as the task block suggests. If the maker renames it, those cases error rather than fail, but the 3 `admit` public-surface cases still cover AC4. The sleep capture covers `time.sleep` and any module-level alias of it in `admission.py`. A switch to `threading.Event.wait` would show up as "no sleep" (red).
