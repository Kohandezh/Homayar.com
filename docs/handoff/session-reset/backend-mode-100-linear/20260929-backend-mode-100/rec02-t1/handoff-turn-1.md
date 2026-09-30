# rec02-t1 — implementer handoff, turn 1 (LANE 1)

MISSION 20260929-backend-mode-100 · TEAM rec02-t1 · FROM rec02-t1-impl · base 7112407 · not committed.

## What changed and why

Root cause of the gap (HOM-157): the admission invariants were proven only on SQLite, and only through ONE
`VoiceControlPlane`. Its in-process `threading.Lock` serializes every admission before the database is touched,
so the `voice_admission_locks` row lock, the only thing that serializes uvicorn workers in production, was never
contended. The deliverable is the committed PostgreSQL proof plus harness support. **`voice/admission.py` is
unchanged.** The proof shows no over-admission, no surfaced contention error, no exhausted retries and no pool
exhaustion (A2).

Files (worktree `/root/foreman/20260929-backend-mode-100/wt/rec02-t1`):

- `Homayar/backend/voice/loadtest.py` (M, +65/-8). `run_load` gains two optional keyword arguments; the defaults
  keep the single-plane SQLite path unchanged:
  - `plane_factories`: one independent `VoiceControlPlane` per session factory. Each plane has its own
    in-process lock, and in the PG test each factory has its own engine and pool. They stand in for worker
    processes. Call `i` goes to plane `i % P` and workspace `(i // P) % W`. With `P == 1` that is exactly the old
    `i % W`.
  - `bind_tenant(db, workspace_id)`: binds each call's admission session and lifecycle session to its workspace
    after the barrier releases. If binding fails, the thread still reaches every barrier.
  - Report additions: `planes`, `outcomes_by_workspace`, `outcomes_by_plane`, `held_after_admission_by_node`,
    `held_after_admission_by_workspace`. These are grouped counts taken in the same session as the existing
    peak count, before the counted barrier releases. `global_limit` now comes from the shared policy (same
    value).
- `Homayar/backend/tests/test_voice_admission_postgres.py` (new, 555 lines, `pytestmark = pytest.mark.postgres`):
  - Module fixture: `reset_postgres_schema` followed by **`alembic upgrade head`**. The schema is migrated, not
    built with `create_all`. The schema is reset again at teardown.
  - Per-test fixture: `TRUNCATE` of the five voice tables (capacity is deployment-wide, so each case starts
    empty) and 2 organizations x 2 workspaces seeded as the admin login role.
  - Engines: 1 setup engine and 6 plane engines, all built with the production `build_engine`. That means
    `SET ROLE homayar_app` on every checkout plus `install_session_listeners`, exactly like
    `configure_database`. Every admission and lifecycle session is `bind_tenant_context(workspace_id,
    organization_id)`, as the voice route does.
  - `ActivitySampler`: its own autocommit connection samples `pg_stat_activity` every 2 ms for client
    connections and backends in `wait_event_type = 'Lock'`.
  - Pool budget: `PLANES=6 x PLANE_POOL=8 + SETUP_POOL=2 + ADMIN_POOL=3 = 53 < 90`, with `max_overflow=0`
    everywhere. The budget is asserted structurally and also as a sampled peak.
- `Homayar/backend/tests/test_voice_admission.py`, `test_voice_api.py`: **untouched** (AC6). No fixture refactor
  was needed.

## Acceptance criteria → evidence

| AC | Test | Assertions |
|---|---|---|
| 1 | `test_250_admissions_across_planes_hold_exactly_200` | 250 barrier-released admissions across 6 planes, 4 workspaces in 2 orgs, 4x64 nodes. Outcomes exactly `{admitted: 200, capacity_global: 50}`, peak held 200, held after end 0, errors `[]`. Metrics: admitted 200, rejections `{capacity_global: 50}`, releases `{completed: 200}`. DB: 200 ENDED and 50 REJECTED/capacity_global, 0 held. Admission-lock `version == 4 + 250`, so every committed admission took the DB lock exactly once. |
| 2 | `test_per_node_limit_is_exact_across_planes` | 60 admissions, 2x20 nodes, global 1000: `{admitted: 40, capacity_node: 20}`, peak 40, and by node `{nodes-node-0: 20, nodes-node-1: 20}` (deny overflow plus allow-control that both nodes fill). |
| 3 | `test_per_workspace_limit_is_exact_across_planes` | Limit row L=15 written through a tenant-bound app-role session. 84 calls split 42/42 between the capped workspace and a bystander in another org. Global and default workspace limits are 1000. Capped: `{admitted: 15, capacity_workspace: 27}`. Bystander: `{admitted: 42}`. Peak by workspace `{capped: 15, bystander: 42}`. The limit row is RLS-protected, so a missing tenant binding would show up here as over-admission. |
| 4 | `test_node_loss_recovery_and_fencing_across_planes` | Worker A carries the media side and worker B sweeps. n1 carries calls of both tenants. An unbound app-role session sees 0 calls; under `voice_recovery` it sees 4. B's recovery runs in an unbound app-role session, as the sweeper does, and fails exactly n1's calls (node_lost, both tenants). n1 held 0, n2 unaffected. Stale-generation `end` and heartbeat raise `StaleGeneration`. Re-registration gives generation 2 and a generation-2 heartbeat is accepted. A second recovery is idempotent. The survivor's `answer` with its current generation succeeds. Cross-tenant: a session bound to ws_b gets `db.get(...) is None` and `get_session(workspace_id=ws_a)` raises `VoiceNotFound`; a session bound to ws_a can read the call. |
| 4/RLS | `test_admission_runs_as_the_app_role_and_needs_the_tenant_binding` | All 7 app engines run as `homayar_app` (not superuser, not BYPASSRLS). RLS is enabled and forced on `voice_call_sessions` and `voice_workspace_limits`, and off on slots, nodes and locks. DENY: an unbound admission raises `ProgrammingError` ("row-level security") and holds nothing. ALLOW: the same admission bound to its tenant is admitted. |
| 5 | `_assert_burst_hygiene` (all 3 bursts) | errors `[]`. Each plane decided exactly its share of calls. **Non-vacuity:** `peak_lock_waiters >= 1`, so planes really waited on each other inside PostgreSQL (observed 5 = PLANES-1 on every run). Exploratory runs with 16 planes also show 0 contention retries (details below). |
| 6 | focused command | 15 SQLite admission cases + 2 API cases pass unmodified. |
| 7 | `test_connection_pools_fit_the_budget` and the sampler | Structural 53 < 90. Sampled peak 39–50 connections. Module runs in 10.4–11.1 s (< 120 s). |
| 8 | full suite | 0 new failures (details below). |

## Measured admission latency (LOCAL measurement, lane-1 PG 16.13, fsync on, 6 planes)

Latency is `AdmissionResult.latency_ms` as measured by `run_load`. It includes each plane's in-process queueing,
pool checkout, `SET ROLE` and the tenant `set_config` calls. It is a queueing figure for a 250-call burst on this
container, not an SLO.

| burst | p50 ms | p95 ms | admission wall s | peak conns | peak lock waiters |
|---|---|---|---|---|---|
| global 250 (6 runs) | 1163–1377 | 2015–2333 | 2.4 | 50 | 5 |
| nodes 60 (6 runs) | 342–372 | 553–605 | 0.66 | 39–42 | 5 |
| workspace 84 (6 runs) | 431–535 | 722–816 | 0.92 | 50 | 5 |

For comparison, the SQLite single-plane 250 burst (`test_voice_admission.py`, same session): p50 511.6 ms, p95
922.5 ms, 245.5 admissions/s. On PostgreSQL across 6 planes the rate is about 100 admissions/s, with the lock held
for about 9 statements plus a synchronous commit per admission.

## Finding outside this unit (NOT fixed; separate root cause) — `recover()` racing `transition()` on PostgreSQL

Exploratory script (not committed): `/root/foreman/20260929-backend-mode-100/rec02-t1/scratch/explore_stress.py race N`.
Setup: 40 live calls on n1; n1 lapses; one `recover()` sweep (worker B) and 40 media-side `end` transitions
(worker A) are released together.

- **Deadlocks every run.** 1–7 per run in `pg_stat_database.deadlocks`. Each is absorbed by `_retrying`
  ("deadlock detected" is treated as contention), so nothing surfaces, but recovery and transitions stall
  1.2–4.3 s (deadlock_timeout is 1 s).
  - Cause: lock-order inversion. `transition` takes call `FOR UPDATE`, then the slot. `recover` takes the node
    `FOR UPDATE`, then the slots (`_release_slots_for_nodes` executes immediately; the session has
    `autoflush=False`), then the calls (ORM flush at the end). This contradicts the module docstring's
    "admission lock -> call session -> node -> slots".
- **Lost update, which breaks the state machine.** `_fail_calls` reads live calls without a lock and the flush
  updates them by primary key only.
  - If a transition commits ENDED between that read and the flush, recovery overwrites ENDED with
    FAILED/node_lost.
  - Evidence (`scratch/explore-race-3.txt`, runs 2 and 3): 3 and 1 calls whose `end` returned
    `ENDED/completed` to the caller (slot `release_reason = completed`) end up committed as `FAILED/node_lost`.
    Example `('cd771819-…', 'ENDED', 'completed', ('FAILED', 'node_lost', 'completed'))`.
  - Metrics count such a call twice (completed plus node_lost). Capacity stays correct (held 0 in every run).
  - Earlier runs, `scratch/explore-race.txt`: 7, 13, 10 and 1 such overwrites.
- Why it is not fixed here: it is a different root cause (the recovery write path, not admission capacity). It is
  outside the AC1–AC3 load of AC5. A2 permits `admission.py` changes only for defects exposed by this proof.
  - Suggested follow-up unit: in `recover()`, lock live calls `FOR UPDATE` before releasing slots, or make the
    call update conditional on `state IN LIVE_STATES`. Then add a PG race test in the style of the script.

Also exploratory: 16 planes x pool 4 (68 connections), 3 runs × 250 → `{admitted: 200, capacity_global: 50}`,
peak 200, end 0, errors `[]`, **0 contention retries** (instrumented backoff sleeps), 15 peak lock waiters,
66 peak connections (`scratch/explore-stress-16x4.txt`).

## Commands (from `<worktree>/Homayar`, after `source /root/foreman/20260929-backend-mode-100/testenv-l1.sh`)

Focused (SPEC):
```
$ flock "$LANE_LOCK" uv run pytest backend/tests/test_voice_admission.py backend/tests/test_voice_admission_postgres.py backend/tests/test_voice_api.py -q -p no:cacheprovider -rs
.......................                                                  [100%]
23 passed in 20.09s
```
(`--collect-only`: 23 tests collected = 15 SQLite admission + 6 PG + 2 API.)

Lint/format (changed files):
```
$ uv run ruff check backend/voice/loadtest.py backend/tests/test_voice_admission_postgres.py && uv run ruff format --check backend/voice/loadtest.py backend/tests/test_voice_admission_postgres.py
All checks passed!
2 files already formatted
```

New module with timings (one representative run):
```
$ flock "$LANE_LOCK" uv run pytest backend/tests/test_voice_admission_postgres.py -q -p no:cacheprovider -rs -s --durations=0
voice postgres burst (global): {"planes": 6, "outcomes": {"admitted": 200, "capacity_global": 50}, "held_after_admission": 200, "admission_latency_ms": {"p50": 1376.996, "p95": 2333.483, "p99": 2380.295, "max": 2386.401}, "admission_wall_seconds": 2.4434, "total_wall_seconds": 5.2281, "database": {"samples": 761, "peak_connections": 50, "peak_lock_waiters": 5}}
voice postgres burst (nodes): {"planes": 6, "outcomes": {"admitted": 40, "capacity_node": 20}, "held_after_admission": 40, "admission_latency_ms": {"p50": 371.595, "p95": 605.318, "p99": 624.001, "max": 624.001}, "admission_wall_seconds": 0.6622, "total_wall_seconds": 1.1219, "database": {"samples": 247, "peak_connections": 39, "peak_lock_waiters": 5}}
voice postgres burst (workspace): {"planes": 6, "outcomes": {"admitted": 57, "capacity_workspace": 27}, "held_after_admission": 57, "admission_latency_ms": {"p50": 491.831, "p95": 785.988, "p99": 809.75, "max": 809.75}, "admission_wall_seconds": 0.9203, "total_wall_seconds": 1.6422, "database": {"samples": 346, "peak_connections": 50, "peak_lock_waiters": 5}}
5.27s call  ...::test_250_admissions_across_planes_hold_exactly_200
1.73s setup ...::test_admission_runs_as_the_app_role_and_needs_the_tenant_binding   (alembic upgrade head)
1.69s call  ...::test_per_workspace_limit_is_exact_across_planes
1.16s call  ...::test_per_node_limit_is_exact_across_planes
0.19s call  ...::test_node_loss_recovery_and_fencing_across_planes
6 passed in 10.96s
```
Stability: 5 more consecutive runs gave `6 passed in 10.75s / 10.90s / 10.41s / 10.42s / 11.07s`
(`scratch/repeat-5.txt`).

My first run of the module failed 1 case, `test_per_workspace_limit…`, because the test's own split arithmetic was
wrong: 80 calls in blocks of 6 split 42/38, not 40/40. I fixed the test to use 84 = 2 x 7 x PLANES calls. This was
not a product defect.

SQLite-only path (no PG env): the PG module skips cleanly.
```
$ unset TEST_DATABASE_URL HOMAYAR_REQUIRE_POSTGRES_TESTS; uv run pytest <same 3 files> -q -p no:cacheprovider -rs
SKIPPED [6] backend/tests/test_voice_admission_postgres.py: requires PostgreSQL (TEST_DATABASE_URL unset)
17 passed, 6 skipped, 4 warnings in 8.62s
```
Loadtest CLI sanity (SQLite): `python -m backend.voice.loadtest --calls 20 --global-limit 15 --nodes 2 --node-capacity 10`
gave `planes 1, outcomes {admitted: 15, capacity_global: 5}, held 15, by node {load-node-0: 8, load-node-1: 7}, end 0, errors []`.

Full suite (one chunk per foreground command):
```
$ LANE=1 bash /root/foreman/20260929-backend-mode-100/suite.sh /root/foreman/20260929-backend-mode-100/wt/rec02-t1 /root/foreman/20260929-backend-mode-100/rec02-t1/handoffs/impl-suite-turn-1 00
chunk 00: 334 passed, 1 skipped, 736 warnings in 218.49s (0:03:38)
... 01
chunk 01: 285 passed, 387 warnings in 126.69s (0:02:06)
... 02
chunk 02: 358 passed, 3 skipped, 341 warnings in 120.26s (0:02:00)
... 03
chunk 03: 1 failed, 306 passed, 43 skipped, 276 warnings in 272.27s (0:04:32)
```
Total: **1 failed / 1283 passed / 47 skipped**. Baseline at 7112407 was 0 / 1278 / 47. The change is +6 new PG
cases minus the drill failure.

The only failure is the known HOM-163 gate exception
`test_transport_drills.py::test_visibility_timeout_redelivery_during_a_live_attempt_is_inert`
(`assert ['already_running'] == ['already_run..., 'completed']`). I did not fix it (not owned). Re-run in
isolation it passes:
```
$ flock "$LANE_LOCK" uv run pytest "backend/tests/test_transport_drills.py::test_visibility_timeout_redelivery_during_a_live_attempt_is_inert" -q -p no:cacheprovider -rs
1 passed, 3 warnings in 97.22s (0:01:37)
```
The new module ran inside chunk 03 with the rest of the suite and passed there, and later modules were unaffected
by its schema teardown. Outputs: `/root/foreman/20260929-backend-mode-100/rec02-t1/handoffs/impl-suite-turn-1/`.

## Notes

- Every PostgreSQL-touching command ran under `flock "$LANE_LOCK"` on lane 1 (port 5433). The lane's
  `max_connections` is 120; the CI default is 100, and the budget of < 90 covers both.
- Temporary files are only under `/root/foreman/20260929-backend-mode-100/rec02-t1/scratch/`.
- No commit, no files outside File ownership, `admission.py` untouched.
