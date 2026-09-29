# SPEC — team stab01-t1 (mission 20260929-backend-mode-100)

Orchestration mode: `foreman_native_adapted` (owner-authorized 2026-09-29). Foreman skill loaded from
Kohandezh/kohandezh-live-avatar@df81a9d, vendored at Homayar.com `.claude/skills/foreman`. Herdr is not
available in this cloud container; each role is a native Claude Code subagent. Role separation, the blind
first artifact, the bare-diff review and the two-round cap are unchanged.

## Task
- Linear: HOM-140 [STAB-01] https://linear.app/homayar/issue/HOM-140 (child of HOM-8 / WP04)
- Type: Implementation (bug fix). Work unit: one root cause.
- Dependency group: 0 (reconcile and stabilize).

## Context (enough to work without oral history)
Repository `Kohandezh/Homayar.com`; the product backend lives in `Homayar/backend` (FastAPI, SQLAlchemy 2,
Alembic, PostgreSQL RLS, Celery/Redis). Base commit `682ffee` = origin/main `2e834ed` + one commit that only
adds `.claude/skills/foreman`.

`backend/tests/test_truthful_outputs.py::test_no_random_numbers_outside_the_allowlist` scans every production
module under `backend/` (excluding `tests/` and `.venv/`) for `^\s*(import random\b|from random import)` and
fails for any module not in `RANDOM_ALLOWLIST = {"database/seed.py"}`. It is the WP04/F23 guard that production
code never fabricates numbers. It currently FAILS on the base with offender `voice/admission.py`.

`backend/voice/admission.py` imports `random` (line 27) and uses it once, at line 165, inside
`VoiceAdmissionService._retrying`, to jitter the sleep between retries when an `OperationalError` is lock
contention:
`time.sleep(min(0.05, 0.001 * attempt) * (0.5 + random.random()))`.
The retry loop serializes admissions; `_take_admission_lock` bumps a version row in `voice_admission_locks`.
Introduced by c4e34d0 (PR #10). Merged without CI (GitHub Actions blocked by billing).

## Acceptance criteria (observable)
1. `test_no_random_numbers_outside_the_allowlist` passes.
2. The guard is not weakened: `RANDOM_ALLOWLIST` and `RANDOM_PATTERN` in
   `backend/tests/test_truthful_outputs.py` are byte-identical to the base.
3. `backend/voice/admission.py` introduces no substitute randomness source: no import of `random`,
   `secrets`, `numpy.random`, no `os.urandom`, no `SystemRandom`, no `uuid`-derived numeric jitter.
4. Lock-contention retries stay bounded and still back off: for lock contention, the loop still raises after
   `policy.max_retries` attempts, still sleeps between attempts, and each sleep stays within the base's
   bounds `0 < delay <= 0.075` seconds (base: `min(0.05, 0.001*attempt) * [0.5, 1.5)`). Non-contention
   `OperationalError` still re-raises immediately without sleeping. `IntegrityError` handling unchanged.
5. Existing voice suites pass: `backend/tests/test_voice_admission.py`, `backend/tests/test_voice_api.py`
   (including the concurrent admission cap cases).
6. Full backend suite on PostgreSQL: no new failure relative to the base (base = 1 failed [this defect],
   1048 passed, 46 skipped). Expected after fix: 0 failed, >= 1049 passed.

## Deny-cases and closest allow-controls
- DENY: a production module other than `database/seed.py` importing `random` -> guard fails.
  ALLOW-CONTROL: `database/seed.py` still imports `random` and the guard still passes.
- DENY: non-lock-contention `OperationalError` -> re-raised on first occurrence, no sleep.
  ALLOW-CONTROL: lock-contention `OperationalError` -> retried with a sleep, succeeds when contention clears.
- DENY: lock contention persisting beyond `max_retries` -> raises.
  ALLOW-CONTROL: contention clearing at attempt `max_retries` -> succeeds.

## File ownership
Maker (implementer) may change:
- `Homayar/backend/voice/admission.py`
- a new or extended voice-admission test under `Homayar/backend/tests/` (only if it is needed to cover AC4)
Maker must not change: `Homayar/backend/tests/test_truthful_outputs.py`, any migration, `saas_models.py`,
`rls.py`, `authorization.py`, `config.py`, `main.py`, any other production module, any frontend or website file.
Checker (reviewer) may create only: its oracle test file at
`Homayar/backend/tests/test_oracle_stab01.py` inside the CHECKER worktree, and its phase records under
`/root/foreman/20260929-backend-mode-100/stab01-t1/oracle/` and `/reviews/`. It never edits source.

## Allowed and forbidden actions
Allowed: read the repository; run the commands below; create the files above.
Forbidden for every worker: git commit, git push, branch creation, PR or Linear actions, migrations,
service resets, dropping or recreating the test database, network calls, reading another role's worktree or
files, contacting any agent other than Foreman.

## Worktrees
- Maker: `/root/foreman/20260929-backend-mode-100/wt/stab01-t1` (branch `mission/stab01-t1` at 682ffee)
- Checker: `/root/foreman/20260929-backend-mode-100/wt/stab01-t1-check` (detached at 682ffee, clean)
Each has its own `Homayar/.venv` (uv sync --frozen done).

## Verification commands (run from `<worktree>/Homayar`)
Services are already running (PostgreSQL 16 on 127.0.0.1:5432, Redis on 6379). Always:
```
source /root/foreman/20260929-backend-mode-100/testenv.sh
```
Focused (lightweight, no lock needed):
```
uv run pytest backend/tests/test_truthful_outputs.py backend/tests/test_voice_admission.py backend/tests/test_voice_api.py -q -p no:cacheprovider
```
Full suite (heavy; shared database — MUST hold the mission lock):
```
flock /root/foreman/20260929-backend-mode-100/heavy.lock uv run pytest backend/tests -q -p no:cacheprovider -rs
```
Lint on changed Python files:
```
uv run ruff check <changed files> && uv run ruff format --check <changed files>
```

## Known baselines (base 682ffee, local PG 16.13)
- Full suite: 1 failed (`test_no_random_numbers_outside_the_allowlist`), 1048 passed, 46 skipped.
- Migration check: pass. (No schema change is expected in this unit.)

## Communication map (native adaptation)
- Leader: Foreman (the session that launched you). Your only contact. Report by your final answer, using the
  envelope below. Makers and checkers never contact each other and never read each other's files.
- Implementer name: `stab01-t1-impl`. Reviewer name: `stab01-t1-rev`.
Envelope:
```
MISSION: 20260929-backend-mode-100
TEAM: stab01-t1
TURN: <n>
FROM: <agent>
TO: foreman
TYPE: ACK | STATUS | BLOCKED | EXPAND_REQUEST | REVIEW_NEEDED | CHANGES_REQUESTED | ACCEPTED | FAILED
STATE: ready | working | blocked | awaiting_review | changes_requested | accepted | failed
SUMMARY: <concise factual update>
ARTIFACTS: <absolute paths, commands, or none>
NEXT: <requested action or none>
RISKS: <unresolved risks or none>
```
A worker that finds this contract incomplete or contradictory sends BLOCKED instead of guessing.

## Turn protocol (implementation)
- Phase A: reviewer writes the oracle from this SPEC only, runs it on the clean base, records red/green,
  writes `/root/foreman/20260929-backend-mode-100/stab01-t1/oracle/phase-a.md`.
- Phase B turn N: implementer works in the maker worktree test-first, writes
  `/root/foreman/20260929-backend-mode-100/stab01-t1/handoffs/turn-N.md` (what changed, root cause, every
  verification command with real output), returns REVIEW_NEEDED. Does not commit.
- Foreman runs the oracle against the patch and records red->green.
- Phase C: reviewer reviews `REVIEW-INPUT.diff` (bare diff) against this SPEC, writes
  `/root/foreman/20260929-backend-mode-100/stab01-t1/reviews/turn-N.md`, returns ACCEPTED or CHANGES_REQUESTED.
- Cap: two review rounds, then Escalation.

## Team directory
`/root/foreman/20260929-backend-mode-100/stab01-t1/` — SPEC.md, briefs/, handoffs/, reviews/, oracle/.

## Models
Implementer: opus (concurrency path -> no sonnet downgrade). Reviewer: opus.
