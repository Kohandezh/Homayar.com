# rec02-t1 oracle: phase A (reviewer `rec02-t1-rev`)

- Base: `7112407` (checker worktree `/root/foreman/20260929-backend-mode-100/wt/rec02-t1-check`, detached, clean except the oracle file)
- Oracle: `/root/foreman/20260929-backend-mode-100/wt/rec02-t1-check/Homayar/backend/tests/test_oracle_rec02.py`
  (sha256 `b558d2a564e434b86544ffbcb284da7d1b49761dcf79794261293dfbf2177a2c`, ruff check and ruff format clean)
- Written from SPEC.md only. I did not read the maker worktree, handoffs or any diff.

## Command and real output (lane 1, under the lane lock)

```
cd /root/foreman/20260929-backend-mode-100/wt/rec02-t1-check/Homayar
source /root/foreman/20260929-backend-mode-100/testenv-l1.sh
flock "$LANE_LOCK" uv run pytest backend/tests/test_oracle_rec02.py -q -p no:cacheprovider -rfp
```

```
FAILED ...::test_delivered_proof_module_is_postgres_marked_and_green
FAILED ...::test_delivered_proof_drives_250_admissions_across_four_planes_to_exactly_200
FAILED ...::test_delivered_proof_fills_two_nodes_to_exactly_20_each_across_planes
FAILED ...::test_delivered_proof_caps_one_workspace_at_its_limit_across_planes
FAILED ...::test_delivered_proof_fences_node_loss_under_recovery_scope_as_app_role
FAILED ...::test_delivered_proof_bursts_surface_no_contention_errors
FAILED ...::test_delivered_proof_module_is_bounded_in_time_and_connections
PASSED ...::test_application_sessions_run_as_the_non_bypass_app_role
PASSED ...::test_global_cap_is_exact_across_independent_planes
PASSED ...::test_node_cap_is_exact_across_independent_planes
PASSED ...::test_workspace_row_limit_is_exact_across_planes_and_spares_other_tenants
PASSED ...::test_workspace_default_limit_is_exact_across_planes
PASSED ...::test_serialization_failures_under_repeatable_read_are_absorbed
PASSED ...::test_admission_writes_only_into_the_bound_tenant
PASSED ...::test_tenant_bound_session_cannot_read_another_workspace_call
PASSED ...::test_node_loss_recovery_fences_stale_writers_across_tenants
PASSED ...::test_sqlite_proofs_are_all_kept_and_pass
PASSED ...::test_sqlite_load_proofs_still_hold_exactly_on_sqlite
7 failed, 11 passed in 43.27s
```

Every red case fails on the same assertion:
`AssertionError: backend/tests/test_voice_admission_postgres.py was not delivered`.

## How the oracle is built

**Part 1: independent PostgreSQL proof of `admission.py`.** It uses only the public `VoiceControlPlane` API, and each test gets a fresh migrated schema.

- `alembic upgrade head` runs on an empty database, taking about 1.6 s.
- Tenants are seeded through the BYPASSRLS inspection session.
- Application sessions come from the production `build_engine(settings)` with `SET ROLE homayar_app`. The pool is bounded at 40 with `max_overflow=0`, and `install_session_listeners` is applied.
- Every admission uses its own session, bound with `bind_tenant_context(workspace_id, organization_id)`.
- Bursts are released from a barrier through N independent planes. Nothing is shared in-process, and each plane has its own metrics.
- A watcher polls the held-slot count and the `pg_stat_activity` backends during each burst.
- A cursor-event counter records how many transactions held or awaited the admission lock at the same time. A value of at least 2 means the database lock, not an in-process lock, did the serializing.

**Part 2: observable checks on the delivered proof and the kept SQLite proofs.** The oracle runs the delivered module (and, separately, `test_voice_admission.py` plus `test_voice_api.py`) in a subprocess with a probe plugin, `run_probed`. The probe:

- wraps `VoiceControlPlane.admit/recover/transition/heartbeat/register_node/set_workspace_limit` at class level to record plane identity, arguments, outcome and exception type;
- on each admission-lock statement and each `INSERT`/`UPDATE voice_call_sessions`, reads `current_user`, `homayar.workspace_id`, `homayar.service_scope` and `to_regclass('alembic_version')` from the same connection and transaction;
- counts live pool connections, while the parent process polls `pg_stat_activity` for `application_name = rec02-oracle-probe` (set through `PGAPPNAME`);
- records markers, outcomes and session duration for every test.

A "burst" is a maximal set of time-overlapping `admit` calls. The checks assert what the delivered module does, not how it is written.

## Red and green classification

- **Red (7), the acceptance tests for this unit:** the `test_delivered_proof_*` cases. The defect is that the invariants are proven only on SQLite through one plane. These cases go green only when a delivered PostgreSQL module actually drives the required bursts across independent planes, as the app role, tenant-bound, on the migrated schema, with the database lock contended.
- **Green (9), Part 1, regression controls:** base `admission.py` holds the invariants on PostgreSQL across independent planes. They protect against an AC5 "fix" that breaks correctness, and they independently confirm the maker's proof.
- **Green (2), AC6 controls:** the SQLite proofs are kept and still hold. They go red if a base case is deleted or fails, or if the 250 and 60 SQLite bursts stop producing their exact outcomes on SQLite.

## Validation of the oracle itself (scratchpad only, no source edits)

- **Good reference proof.** A scratch module re-exporting the Part 1 tests, run through `probed_pytest`, turns all 7 red cases green. It also stays green with an extra unmarked non-DB unit test in the module. Observed: 250 admits over 4 planes, outcomes 200/50, peak 4 lock contenders, role `homayar_app`, 42 backends, 17 s.
- **Flawed reference proof.** Red for the right reason in each case:
  - single plane: AC1 fails with "1 control plane(s) < 4; database admission lock never contended";
  - superuser engine: AC2 fails with "roles {'homayar_test'}";
  - SQLite disguised as postgres-marked: AC3 fails on dialect, role, tenant context and schema;
  - no recovery: AC4 fails.
- **Runtime mutants of `backend.voice.admission`, patched through a `-p` plugin.** Part 1 results:
  - `_take_admission_lock` as a no-op: red on global, node, workspace-row, workspace-default (3 of 3 after widening to 8 planes and 40 calls) and repeatable-read;
  - one lock shared across all planes: red on global and node (lock never contended);
  - `_is_lock_contention` always False: red on repeatable-read (surfaced serialization failure);
  - recovery without `bind_service_scope`: red on fencing (the other tenant's call was not failed).
- **Stability.** Part 1 was green on base in 3 of 3 repeat runs, and the widened default-limit case in 3 of 3.

## Acceptance criteria and covering cases

- **AC1** (global cap across at least 4 planes, app role, tenant-bound, migrated; exactly 200/50, 200 held, 0 after, no errors): `test_delivered_proof_drives_250_admissions_across_four_planes_to_exactly_200` (red), plus the control `test_global_cap_is_exact_across_independent_planes`. Supporting controls: `test_application_sessions_run_as_the_non_bypass_app_role` and `test_admission_writes_only_into_the_bound_tenant`.
- **AC2** (per-node 60 over 2×20, exactly 40/20, 40 held): `test_delivered_proof_fills_two_nodes_to_exactly_20_each_across_planes` (red), plus the control `test_node_cap_is_exact_across_independent_planes`.
- **AC3** (per-workspace limit L across planes): `test_delivered_proof_caps_one_workspace_at_its_limit_across_planes` (red), plus the controls `test_workspace_row_limit_is_exact_across_planes_and_spares_other_tenants` (limit from a `voice_workspace_limits` row, with the other tenant unaffected) and `test_workspace_default_limit_is_exact_across_planes` (limit from the policy default).
- **AC4** (fencing on PostgreSQL, app role, `voice_recovery` scope, no cross-tenant reads): `test_delivered_proof_fences_node_loss_under_recovery_scope_as_app_role` (red), plus the controls `test_node_loss_recovery_fences_stale_writers_across_tenants` and `test_tenant_bound_session_cannot_read_another_workspace_call`.
- **AC5** (contention absorbed, not surfaced): `test_delivered_proof_bursts_surface_no_contention_errors` (red), plus the control `test_serialization_failures_under_repeatable_read_are_absorbed` and the `errors == []` checks in every Part 1 burst.
- **AC6** (SQLite proofs unchanged): `test_sqlite_proofs_are_all_kept_and_pass` and `test_sqlite_load_proofs_still_hold_exactly_on_sqlite` (green controls).
- **AC7** (fewer than 90 connections, module under 120 s): `test_delivered_proof_module_is_bounded_in_time_and_connections` (red), plus `max_backends_observed < 90` in the AC1 control.
- **AC8** (full backend suite on PostgreSQL, no new failure): **no case in the oracle.** Foreman's `suite.sh` chunks cover it.
- **"A postgres-marked test"** (AC1 to AC4 wording): `test_delivered_proof_module_is_postgres_marked_and_green` (red), plus the `unmarked` check on each qualifying burst and recovery.

## Deny-cases and their allow-controls

- **DENY over-admission** (never more than 200 held): `max_held_observed <= 200` with `held_at_peak == 200`.
  - ALLOW: exactly 200 admitted, in the AC1 control and the delivered check.
- **DENY node overflow** (never more than 20 per node): `max_held_observed <= 40` and the per-node peak.
  - ALLOW: `held_by_node_at_peak == {a: 20, b: 20}`.
- **DENY a stale generation after recovery.** Rejected cases:
  - `end` with generation 1 on lost calls in both tenants;
  - heartbeat with generation 1 and with generation 2 before re-registration;
  - heartbeat with generation 1 after re-registration;
  - a lost call presenting the node's new generation 2;
  - a survivor presenting generation 2.
  - ALLOW: re-registration returns generation 2; a heartbeat with generation 2 succeeds; survivors in both tenants `answer` with generation 1 and reach ACTIVE.
- **DENY a cross-tenant read under tenant context.** Rejected cases:
  - a raw `select` or `db.get` of B's call from an A-bound session;
  - `get_session(workspace_id=B)` from an A-bound session;
  - a B transition from an A-bound session;
  - an unbound session, which sees 0 rows.
  - ALLOW: the B-bound session reads B's call; the A-bound session reads A's.
- **Extra RLS pair.** DENY: admitting into A from a B-bound or unbound app-role session admits nothing (no row, no slot). ALLOW: the A-bound admission is admitted.
- **Non-obvious spellings covered:**
  - limit from a row versus from the policy default;
  - tenant context bound before the transaction versus mid-transaction (`pre_read`);
  - recovery from a tenant-bound session (as the route does) versus an unbound session (as the Celery sweeper does);
  - READ COMMITTED versus REPEATABLE READ.

## Assumptions and risks

- The Part 2 probe observes in-process `VoiceControlPlane` instances, as the SPEC defines them ("instances ... a stand-in for separate worker processes"). A delivered proof that runs planes in child processes would not be observed and would read red.
- Lock detection matches statements containing `voice_admission_locks`, `pg_advisory` or `LOCK TABLE`. An AC5 fix that serializes some other way would fail the "lock contended" check; if that happens, tell me so I can re-check.
- Running the oracle against the patch also runs the delivered module and the two SQLite proof modules in a subprocess. Expect roughly 45 s plus the delivered module's runtime. Run it only under the lane-1 lock, because the subprocess resets the lane database.
- Not tested, because the SPEC does not require it: `recover()` binds `voice_recovery` into `session.info`, so the scope stays on that session afterwards. The route and the sweeper close the session straight after, so there is no leak in production paths today.
- Deadlock retry has no deterministic case. Serialization-failure retry is covered through REPEATABLE READ.
