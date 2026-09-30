# SPEC — team rec02-t1 (mission 20260929-backend-mode-100)

Orchestration mode: `foreman_native_adapted`. Role separation, blind oracle, bare-diff review, two-round cap.
Trigger check: concurrency and locking on the shared database; a database specialist (database-optimizer) is the
final reviewer if the unit changes locking code in `backend/voice/admission.py` (Foreman expansion rule).

## Task
- Linear: HOM-157 [REC-02] Prove voice admission invariants on PostgreSQL (250 -> 200 exact, per-node limit, fencing).
  Unblocks V02, V05, X21. Dependency group 0. Priority P1 (stabilization flag S).
- Type: Verification with conditional fix. Work unit: one root cause — the admission invariants are proven only on
  SQLite and only through a single in-process control plane.

## Base
`BASE_SHA` = `7112407` (integration head after HOM-32). Lane: **L1** (own PostgreSQL cluster on port 5433, Redis dbs 2/3).

## Context (verified by Foreman at 3d64130)
- `backend/voice/admission.py` `VoiceControlPlane._serialized` wraps admission in a process-local `threading.Lock`
  before `_retrying`; `_take_admission_lock` bumps the `voice_admission_locks` row (`UPDATE ... version+1`, insert on
  first use; `IntegrityError` retried up to `policy.max_retries` = 200). Contention errors recognised by
  `_is_lock_contention` (SQLite locked, "deadlock detected", "could not serialize").
- `backend/voice/loadtest.py` `run_load` drives all threads through ONE `VoiceControlPlane`, so every admission is
  serialized by the in-process lock and the database lock is never contended. `tests/test_voice_admission.py:40-44`
  uses `sqlite_session_factory` (WAL file). The 250 -> 200 proof (`:227`) and the per-node proof (`:257`) are SQLite,
  single-plane.
- Production: `infra/compose/compose.{cloud,onprem}.yaml` run `uvicorn --workers "$UVICORN_WORKERS"`; each worker
  process builds its own plane (`routers/v1/voice.py:36-50`), so across workers only the database row lock serializes.
- RLS: `voice_media_nodes`, `voice_admission_locks`, `voice_capacity_slots` are deployment-wide (no RLS,
  `database/rls.py:279-283`); `voice_call_sessions` is tenant-scoped (`workspace_id = tenant OR service scope
  'voice_recovery'`, `rls.py:143-145`). Production sessions run as the app role via `build_engine(...)` `SET ROLE`
  (`database/relational.py:23-75`); tests use `settings.database_app_role = TEST_APP_ROLE` (`tests/conftest.py:108-135`).
- PostgreSQL `max_connections` default is 100 (local and CI service image); a harness must bound its pool below it.
- Test markers: `pytest.mark.postgres` = skipped unless `TEST_DATABASE_URL` is set (`pyproject.toml:51-56`); CI sets it
  (`.github/workflows/ci.yml:170`).

## Acceptance criteria (observable)
1. **Global cap on PostgreSQL across independent planes.** A `postgres`-marked test drives 250 simultaneous admissions
   (barrier release) through at least 4 independent `VoiceControlPlane` instances (no shared in-process lock; a
   stand-in for separate worker processes), on the migrated schema, with application SQL running as the app role
   (RLS enforced) and each admission bound to its workspace's tenant context as production does. Result, exactly:
   200 admitted, 50 rejected with the global-capacity reason, 200 slots held at the peak, 0 held after every call
   ends, no errors.
2. **Per-node cap on PostgreSQL across planes.** 60 simultaneous admissions over 2 nodes of capacity 20 (global limit
   high): exactly 40 admitted, 20 rejected with the node-capacity reason, 40 held at the peak, no errors.
3. **Per-workspace cap on PostgreSQL across planes.** Simultaneous admissions into one workspace with limit L (global
   and node limits high) admit exactly L; the rest are rejected with the workspace-capacity reason.
4. **Fencing on PostgreSQL.** The node-loss recovery scenario (lapsed node's calls failed with `node_lost`, its slots
   released, stale-generation transition and heartbeat rejected, re-registration bumps the generation, recovery
   idempotent, survivor unaffected) holds on PostgreSQL with the app role; recovery runs under the `voice_recovery`
   service scope and a tenant-bound session cannot see another workspace's call.
5. **Contention is absorbed, not surfaced.** Under AC1–AC3 load no admission raises; PostgreSQL lock waits,
   deadlocks or serialization failures (if any) are retried within `max_retries`. If the proof exposes a real defect
   (over-admission, surfaced contention error, exhausted retries, pool exhaustion), fix its root cause in
   `backend/voice/admission.py` or `backend/voice/loadtest.py` in this unit and state it in the handoff.
6. **SQLite proofs unchanged.** Existing `test_voice_admission.py` and `test_voice_api.py` cases keep passing
   unmodified in meaning (fixture refactors allowed; no assertion weakened, no case deleted).
7. **Bounded runtime and connections.** The PostgreSQL cases hold fewer than 90 database connections at once and
   the whole new PostgreSQL module finishes in under 120 s locally.
8. Full backend suite on PostgreSQL: no new failure relative to `BASE_SHA`.

## Deny-cases and closest allow-controls
- DENY over-admission: 250 across planes -> never more than 200 held. ALLOW-CONTROL: exactly 200 are admitted (not fewer).
- DENY node overflow: never more than 20 per node. ALLOW-CONTROL: both nodes fill to exactly 20.
- DENY stale generation after recovery. ALLOW-CONTROL: survivor's transition with its current generation succeeds.
- DENY cross-tenant read of a call session under tenant context. ALLOW-CONTROL: same-tenant read succeeds.

## File ownership
Maker may change: `Homayar/backend/voice/loadtest.py`, `Homayar/backend/voice/admission.py` (only for an AC5 root-cause
fix), new `Homayar/backend/tests/test_voice_admission_postgres.py`, `Homayar/backend/tests/test_voice_admission.py`
(fixture refactor only). Must not change: migrations, `rls.py`, `saas_models.py`, `authorization.py`, `config.py`,
CI workflow, any frontend file.
Checker: only `Homayar/backend/tests/test_oracle_rec02.py` in the checker worktree and records under
`/root/foreman/20260929-backend-mode-100/rec02-t1/{oracle,reviews}/`.

## Verification commands (from `<worktree>/Homayar`, after `source /root/foreman/20260929-backend-mode-100/testenv-l1.sh`)
EVERY PostgreSQL-touching command runs under the LANE lock: `flock "$LANE_LOCK" ...` (lane 1 has its own cluster;
never use the shared heavy.lock database or another lane's port).
Focused: `flock "$LANE_LOCK" uv run pytest backend/tests/test_voice_admission.py backend/tests/test_voice_admission_postgres.py backend/tests/test_voice_api.py -q -p no:cacheprovider -rs`
Full: `LANE=1 bash /root/foreman/20260929-backend-mode-100/suite.sh <worktree> <outdir> <00|01|02|03>` (one chunk per
foreground command). Baseline at 7112407: 0 failed / 1278 passed / 47 skipped (intermittent drill HOM-163 excepted).

## Worktrees
- Maker: /root/foreman/20260929-backend-mode-100/wt/rec02-t1 (branch mission/rec02-t1 at 7112407)
- Checker: /root/foreman/20260929-backend-mode-100/wt/rec02-t1-check (detached at 7112407)

## Allowed and forbidden actions
As in stab01-t1: no commit/push/branch/PR/Linear, no migrations, no network, no contact with other agents, no
reading the other role's worktree or files. Parallel mode: never touch files outside File ownership (other teams own
them; see /root/foreman/20260929-backend-mode-100/locks.tsv) and never use another lane's database.

## Communication map, envelope, turn protocol, team directory
As in stab01-t1/SPEC.md, team `rec02-t1`, names `rec02-t1-impl`, `rec02-t1-rev`, final `rec02-t1-final`.

## Models
Implementer: opus (concurrency). Reviewer: opus. Final reviewer (if admission.py changes): database-optimizer.

## Amendments (Foreman, after phase A, before phase B)
- A1 (AC1 precision): the independent planes are separate `VoiceControlPlane` instances in the TEST process (the SPEC's
  stand-in for worker processes); do not move planes into child processes. The new module is `pytest.mark.postgres`
  and drives the public `VoiceControlPlane` API.
- A2 (expectation, from phase A evidence): Foreman's and the reviewer's base runs indicate the base admission code
  already holds the invariants on PostgreSQL across independent planes. The deliverable is therefore primarily the
  committed PostgreSQL proof (and any harness support in `voice/loadtest.py`). Change `voice/admission.py` ONLY if your
  proof exposes a real defect (AC5), and then state the failing evidence first.
