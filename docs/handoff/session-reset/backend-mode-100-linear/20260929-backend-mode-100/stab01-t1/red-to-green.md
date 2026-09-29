# Oracle red -> green record (Foreman), turn 1
Oracle: stab01-t1/oracle/test_oracle_stab01.py (sha256 9576ef54fe451d9044c76aa23a52f0eb8d88075507298dafa4a54a9c5255d0d7)
- Base 682ffee (checker worktree, Foreman re-run): 4 failed, 52 passed.
- Patch (maker worktree, uncommitted diff, Foreman run): 56 passed, 0 failed.
- Red on base -> green on patch:
  - test_guard_passes_on_the_production_tree (AC1)
  - test_only_the_seed_module_imports_random (AC1)
  - test_admission_has_no_randomness_source (AC3)
  - test_lock_contention_retries_draw_no_randomness (AC3)
- Green on base, still green on patch: 52 regression/allow-control cases (AC2, AC4).
- Oracle removed from maker tree after the run; git status hash unchanged.

## Turn 2 (after fix F1)
- Patch (maker worktree, Foreman run under heavy.lock): oracle 56 passed, 0 failed; tree restored.
- Gates (Foreman, nothing else running): ruff clean; full suite 0 failed / 1055 passed / 46 skipped (gates/turn-2/).
- Turn-1 Foreman full-suite run is VOID (overlapped an unlocked reviewer run; SPEC A2).
