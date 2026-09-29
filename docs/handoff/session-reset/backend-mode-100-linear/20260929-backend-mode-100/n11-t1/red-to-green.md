# Oracle red -> green record (Foreman), turn 1
Oracle: n11-t1/oracle/test_oracle_n11.py (sha256 8b5cf08a6c7100df5cf8729c99151c9c890f8b752d54eba98cdf7bd1ea911969)
- Base 692affb (checker worktree, Foreman re-run under heavy.lock): 129 failed, 70 passed.
- Patch (maker worktree, uncommitted diff, Foreman run under heavy.lock): 199 passed, 0 failed. Tree restored.
- All 129 red cases (AC1 x36, AC2 x18, AC3 x20, AC4 x33, AC6 x21, AC7 x1) -> green. All 70 controls stayed green.
- No leftover backend/websocket or backend/prompts directories after the run.
Gates (Foreman, 4 foreground chunks under heavy.lock, nothing else running):
- ruff check + format --check on changed/new Python files: clean.
- Full suite: 165 + 285 + 358(+3 skipped) + 301(+43 skipped) = 0 failed / 1109 passed / 46 skipped (base 1055 passed / 46 skipped).

## Turn 2 (A5 docs fix)
- Python diffs byte-identical to turn 1 (Foreman script) -> turn-1 full-suite gate (0 failed / 1109 / 46) remains valid.
- Oracle on patch (Foreman, heavy.lock): 199 passed.
