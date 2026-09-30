# Red -> green record (Foreman), team wp02t03-t1

Oracle v2 (sha256 731339af85cfcca57381a51e9b0b639a691b22c901bea48e04e5f9f74e23c4ed, 1100 lines), archived at
oracle/test_oracle_wp02t03.v2.py. v1 (c051cdb0…) superseded by SPEC A7/A8.

| Run | Tree | Result |
|---|---|---|
| Foreman, v1 on base 3d64130 | checker worktree | 350 failed / 171 passed |
| Foreman, v2 on base 3d64130 | checker worktree | 392 failed / 192 passed (matches reviewer) |
| Foreman, v2 on patch (maker turn 1b) | maker worktree (oracle copied in, removed after) | 584 passed / 0 failed |

Command (from `<worktree>/Homayar`, after `source testenv.sh`):
`flock /root/foreman/20260929-backend-mode-100/heavy.lock uv run pytest backend/tests/test_oracle_wp02t03.py -q -p no:cacheprovider -rs`

## Foreman gates on maker turn 1b
- AC5 literal scan (booleans only; literals derived from base 3d64130 seed.py AUTH tuple, docker-compose NEO4J_AUTH and
  README NEO4J_PASSWORD, length >= 6): 807 worktree files (tracked + untracked) -> 0 hits; team directory -> 0 hits.
- ruff check + format --check on backend/config.py and backend/tests/test_config_secrets.py: clean.
  backend/database/seed.py: 58 pre-existing findings on patch vs 59 on base (CI does not run ruff on the backend).
- Full suite, 4 foreground chunks under the lock (gates/suite-turn-1b): 00 308 passed/1 skipped; 01 282 passed;
  02 339 passed/3 skipped; 03 330 passed/43 skipped => 0 failed / 1259 passed / 47 skipped
  (base 0 / 1109 / 46; +150 passed / +1 skipped from the maker's new test file).
- File ownership: 7 modified files + 1 new test file, all within SPEC ownership incl. A6 (README one line) and A8
  (one compose setting line + comment).

## Maker turn 2 (documentation only, after team review ACCEPTED with 1 low)
- Per-file patch comparison turn 1b vs turn 2: only `docs/operations/deployment.md` and `Homayar/README.md` (line 94)
  changed; config.py and test file hashes identical. Full-suite result from turn 1b stands (no Python change).
- Oracle v2 on turn-2 tree: see gates/oracle-patch-turn-2.txt.
- REVIEW-INPUT.diff for the final reviewer = gates/maker-turn-2.patch (sha256 prefix d65e0270dfec4647).
584 passed, 4 warnings in 2.35s

## Maker turn 3 (A9/A10, after the final security review)
- Per-file compare turn 2 -> turn 3: changed config.py, seed.py, test_config_secrets.py, deployment.md; all else same.
- Oracle v2 on turn-3 tree: 584 passed (gates/oracle-patch-turn-3.txt). ruff/format clean on changed Python files;
  seed.py 58 pre-existing findings (base 59).
- Full suite (Foreman, 4 chunks, gates/suite-turn-3): 00 327 passed/1 skipped; 01 282 passed; 02 339 passed/3 skipped;
  03 330 passed/43 skipped => 0 failed / 1278 passed / 47 skipped (base 1109 + 169 new).
  The maker saw test_transport_drills::test_visibility_timeout_redelivery_during_a_live_attempt_is_inert fail twice in
  chunk 03 and reproduced it on BASE code with the same chunk; Foreman's chunk-03 run passed. Intermittent, pre-existing
  (worker SIGTERM before its completion log line) -> follow-up issue, not this unit.
- Literal scan on turn-3 tree: 0 hits.
- Final round 2 inputs: REVIEW-INPUT.diff (full, sha 3745215b…), REVIEW-DELTA.diff (turn 2 -> 3, sha d7873f37…).
