# ADR 0081: Backend CI in four timed parts behind two gate checks

Date: 2026-11-14 · Status: Proposed (HOM-292, reopened) · Builds on: the HOM-223 branch protection rule (`docs/operations/ci.md`)

## Context

The backend CI test step is too slow. On run 43498411851 (`main`) the step "Run backend tests"
took 27:10 in shard 1/2 and 35:55 in shard 2/2. Twelve recent runs show both shards at 26 to 36
minutes. The job timeout is 45 minutes, and shard 2 hit it three times.

Each shard is one GitHub runner (`ubuntu-24.04`, 4 vCPU) with its own PostgreSQL and Redis and
runs `pytest -n 4 --dist loadfile`. The four xdist workers already use about 68 % of the CPU next
to PostgreSQL. So even a perfect two-way split would take about 31 minutes per shard. The goal is
a test step under 25 minutes, and that needs more runners.

The shard of a file was CRC-32 of a fixed prefix and the file name, modulo the count. The prefix
was chosen by hand with measured times when the suite was smaller. Nothing kept the shards
balanced as the suite grew.

Branch protection on `main` requires the check names `Backend tests and migrations (shard 1/2)`
and `Backend tests and migrations (shard 2/2)`. Only the owner account can change the rule.

## Decision

### D1. Four test parts, not required by branch protection

A new matrix job `backend-tests` (`Backend tests part 1/4` to `part 4/4`) runs the suite in four
parts. Each part has the steps of the old `backend` job: services, migrations check, ffmpeg, and
the test step with `--test-shard=<part>/4`. The MinIO contract step runs in part 1 only, so it
runs once per CI run, as before.

### D2. The two required names become gate jobs

The job id `backend` keeps the name `Backend tests and migrations (shard ${{ matrix.shard }}/2)`
with the matrix `shard: [1, 2]`. It runs no tests. It needs `source-contract` and
`backend-tests`, runs with `if: always()`, and fails unless both results are exactly `success`.
GitHub reports one result for the whole part matrix, so each gate checks all four parts. A
failed, cancelled or skipped part fails both gates. `if: always()` matters: without it a failed
part would skip the gate, and GitHub counts a skipped required check as passed.

The results reach the script through `env:`, never inline `${{ }}` in the script body.

### D3. The part of a file comes from a checked-in timing map

`backend/tests/shard_timings.json` holds measured seconds per test file name. The duration plan
sorts the map entries by seconds (largest first, then by name) and gives each entry to the part
with the smallest total so far (ties to the lowest part number). `part_of` in
`backend/tests/_shard.py` returns that part. The plan is cached per count and map content.

### D4. The plan reads the map only

The plan is a pure function of the count and the map. It never looks at what a run collected.
The four part jobs run on different runners, and each part has four xdist workers that each run
the collection. None of them talk to each other. Because they all read the same map, they all
agree on the part of each file, whatever subset of the suite one of them collects. So every test
runs in exactly one part.

### D5. A file missing from the map uses the CRC fallback

A file that is not in the map gets `shard_of(name, count)`, the old CRC function. A new test file
therefore runs in exactly one part without a map update. The old prefix and its pinned values
stay valid.

### D6. The drift check and the refresh

`backend/tests/test_shard_assignment.py` fails when:

- the map names a file that no longer exists (a stale entry, for example after a rename);
- more than 25 collected test files (`MAX_MISSING_FILES`) are missing from the map;
- the plan for four parts gives the heaviest part more than 1.10 times the seconds of the
  lightest one.

Each part writes its measured seconds per file with the pytest option `--shard-timings-out=<file>`
(the sum of setup, call and teardown durations, summed on the xdist controller) and uploads them
as the artifact `backend-shard-timings-part-<n>`. The command
`python -m backend.tests._shard merge <out> <in>...` builds the map from the four files. It
refuses a file name that is in two inputs, because parts never share a file. The refresh steps
are in `docs/operations/ci.md`. Both pytest options are written as `--opt=value`: pytest reads
the command line before the conftest adds them, and only that form works in any order.

### D7. The scheduler uses the same map

Under `--dist loadfile`, `make_scheduler` in `backend/tests/_parallel.py` hands out the files
with the most seconds in the map first. Files not in the map keep xdist's order after them. This
replaces the hand-written list `SLOW_FILES_FIRST`.

### D8. The first map (CI run 43539360099)

The first measurement ran the four parts on the CRC fallback, with an empty map. The test
steps took 14:29, 20:24, 19:26 and 19:43. Pytest measured 14 278 s over 340 files (setup, call
and teardown, summed per file). The merge of the four artifacts is the first checked-in map.
Its plan for four parts gives each part 3 570 s (largest over smallest: 1.000). A part's test
step took 0.29 to 0.34 s per measured second in that run, so a part should take about 18 to 19
minutes, under the 25 minute goal.

The 15 slowest files of that map:

| Seconds | Test file |
| ---: | --- |
| 494.0 | `test_capabilities_oracle.py` |
| 387.6 | `test_hom284_partial_pii_oracle.py` |
| 315.9 | `test_demo_seed.py` |
| 313.5 | `test_backup_restore.py` |
| 296.4 | `test_coaching_tasks_api.py` |
| 275.7 | `test_hom53_extraction_oracle.py` |
| 265.7 | `test_call_attribution_lock.py` |
| 258.6 | `test_review_appeals_api.py` |
| 247.9 | `test_review_appeals_oracle.py` |
| 245.9 | `test_copilot_tools_oracle.py` |
| 226.7 | `test_review_decisions_api.py` |
| 222.5 | `test_system_health.py` |
| 212.6 | `test_coaching_tasks_oracle.py` |
| 205.7 | `test_hom288_correction_placeholder_oracle.py` |
| 188.8 | `test_hom53_report_oracle.py` |

No single file is near a part's 3 570 s, so the plan can balance the parts closely.

## Alternatives

- **A: balance two shards only.** A better split of two shards would still take about 31
  minutes per shard (the CPU is already busy). It misses the 25 minute goal.
- **C: four required checks.** Rename the backend checks to the four part names. This needs the
  owner to edit branch protection in the same moment the PR merges, or every open PR waits for
  names that are no longer reported. The gates give the same protection with no change to the
  rule.

## Consequences

- Each CI run uses four backend runners plus two short gate runners, instead of two runners.
- The map must be refreshed from CI artifacts when files are renamed, deleted or added beyond
  the drift limit, or when the balance guard fails. A stale entry fails the suite until the map
  is refreshed.
- Test file names must stay unique under `backend/tests`, because the map is keyed by name. A
  test checks this.
- The part count is free to change: the parts are not required checks. The gate job's `shard`
  matrix must stay `[1, 2]` while branch protection requires those two names.
- No product code changes. Only the CI workflow, the test plumbing in `backend/tests/` and the
  docs change.
