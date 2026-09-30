# rec02-t1 review, turn 1 (team reviewer `rec02-t1-rev`, phase C)

Inputs:
- the bare diff `wt/rec02-t1-check/REVIEW-INPUT.diff`, identical to `gates/REVIEW-INPUT-turn-1.diff`;
- SPEC.md including Amendments A1 to A3;
- `oracle/red-to-green.md` and `gates/oracle-patch-turn-1.txt`.

I did not read the maker worktree, `handoffs/`, or the maker's files in `scratch/`.

## Verdict

**Mergeable as-is.** The patch changes only `voice/loadtest.py` and adds `tests/test_voice_admission_postgres.py`. `admission.py`, the SQLite proofs, migrations and RLS are untouched. The new module proves AC1 to AC4 on the migrated PostgreSQL schema through 6 independent planes. Each plane has its own engine, pool and in-process lock, runs as `homayar_app`, and binds every call session to its workspace's tenant. The module also checks non-vacuity (lock waiters inside PostgreSQL) and the connection budget. The default single-plane path of `run_load` behaves exactly as before. I raise no material finding against the diff. The one medium candidate is a pre-existing `admission.py` race, outside the diff and not used by this unit's evidence.

## Ruling on the one red oracle case (AC3)

**This is oracle over-reach, not a patch gap. I agree with Amendment A3.**

- AC3 says: "Simultaneous admissions into one workspace with limit L (global and node limits high) admit exactly L; the rest are rejected with the workspace-capacity reason."
- The maker's `test_per_workspace_limit_is_exact_across_planes` burst sends 42 simultaneous admissions into the capped workspace (limit 15, set through the tenant-bound `set_workspace_limit`). It asserts exactly 15 admitted and 27 `capacity_workspace` for that workspace.
- "The rest" reads naturally as the rest of that workspace's admissions. Nothing in the wording forbids other tenants' calls in the same burst.
- The bystander (another organization, 42 of 42 admitted, holding 42 at the peak) adds a cross-tenant control; it does not weaken the proof.
- My oracle skipped every burst with more than one workspace (`len({workspace_id}) != 1 -> continue`). That filter exceeded the SPEC. Under A3 that case should count as satisfied.

## Findings

1. **medium (pre-existing, outside the diff, not blocking), `Homayar/backend/voice/admission.py:525-542` against `:375-410`.** A `recover()` that runs concurrently with a `transition()` can overwrite a just-ENDED call as `FAILED/node_lost`, and the two paths take their row locks in opposite orders.
   - **Why it matters:** a call that completed normally can be recorded as lost, which corrupts the call record and release metrics. The deadlock is currently absorbed by `_retrying`, since "deadlock detected" counts as contention.
   - **Trigger (lost update):**
     1. `transition(end)` locks the call row `FOR UPDATE` (`:381`).
     2. It reads the node with a plain `db.get` (`:387`), so it is not blocked by recovery's `FOR UPDATE` on the node (`:505`), and it passes the generation check.
     3. It commits ENDED.
     4. Recovery's `_fail_calls` has already selected the call as live, without `FOR UPDATE` (`:555-560`). Its ORM `UPDATE` by primary key then overwrites the call once the row lock is released.
   - **Trigger (deadlock):** recovery issues the slot `UPDATE` (`:526`) before the ORM call updates flush (`:542`, `autoflush=False`), so it locks slot, then call. `transition` locks call (`:381`), then slot (`:408`).
   - **Smallest fix (separate unit, as Foreman intends):** select the live calls `FOR UPDATE` and re-check the state after locking, or recover under the calls' row locks in the same order as `transition`.
   - **Effect on this unit:** none. The AC4 test runs recovery with no concurrent transitions, and AC1 to AC3 never recover.
   - **Verification:** traced by reading only.

2. **info (pre-existing), `Homayar/backend/voice/admission.py:495`.** `recover()` binds the `voice_recovery` scope into `session.info`, so a caller that keeps using that session afterwards sees every tenant.
   - **Effect today:** none. The route, the Celery sweeper and the new test (`with voice_db.planes[1]() as db: ... worker_b.recover(db)`) all close the session straight after.
   - **Smallest fix (separate unit):** drop the scope when the recovery transaction ends.

3. **info, `test_voice_admission_postgres.py`, `_assert_burst_hygiene`: `assert sampler.peak_lock_waiters >= 1`.** This non-vacuity check depends on sampling timing, so it could in principle flake on a heavily loaded host.
   - **Observed:** 5 waiters (all planes but one) in each of the 3 bursts, with 213 to 744 samples over 0.6 to 2.2 s. The risk is low.
   - **Smallest fix, if it ever flakes:** a lower sampling interval or a longer burst. No change needed now.

4. **info, `test_voice_admission_postgres.py` (multiple lines).** The new module adds explanatory comments, for example the pool budget block, the dataclass field comments and the `# DENY:` / `# ALLOW-CONTROL:` markers.
   - The review brief's convention is no comments. Neighbouring files such as `test_voice_admission.py` use the same style, and there is no behavioural consequence. Not blocking.

### What else changed, compared by inputs and outputs

- **`run_load` without `plane_factories` or `bind_tenant`:**
  - one plane, as before;
  - workspace `(index // 1) % n == index % n`, as before;
  - the same `global_limit` value;
  - the report dict only gains keys.
- **`main` (CLI):** unchanged.
- **Multi-plane mode:** assigns blocks of `len(planes)` consecutive calls to each workspace, and shares one lock-protected `VoiceMetrics` across the planes.
- **New helper `_held_by`:** reuses the base's `LIKE '{run_id}-node-%'` filter.
- No other callers change.

### Acceptance criteria, checked by reading plus one run

- **AC1: met.**
  - 250 calls over 6 independent planes, released from a barrier, on the Alembic-migrated schema.
  - The production `build_engine` with `SET ROLE homayar_app` and `install_session_listeners`.
  - Each call bound with `bind_tenant_context(workspace, organization)`.
  - Exactly 200 admitted and 50 `capacity_global`, 200 held at the peak, 0 held after the end, `errors == []`.
  - Database cross-checks: call states, lock version `4 + 250`, and the RLS deny/allow control.
- **AC2: met.** 60 calls over 2 nodes of 20, global limit 1000. Exactly 40 admitted and 20 `capacity_node`, with each node holding exactly 20.
- **AC3: met under A3** (see the ruling above).
- **AC4: met.**
  - The lapsed node spans two organizations, and recovery by the sweeper (no tenant bound) fails both tenants' calls.
  - The test shows directly that the scope is needed: without it the count is 0, with `voice_recovery` it is 4.
  - Covered in order: slots released; stale transition and stale heartbeat rejected; re-registration gives generation 2; recovery idempotent; survivor ACTIVE.
  - Cross-tenant deny and same-tenant allow.
- **AC5: met for this unit.** `errors == []` in every burst and no `admission.py` change, consistent with A2.
- **AC6: met.** `test_voice_admission.py` and `test_voice_api.py` are untouched. The oracle's SQLite controls are green on the patch.
- **AC7: met.**
  - Pools of 6×8, plus 2, plus 3, all with `max_overflow=0`, give 53, which is under 90.
  - My run observed a peak of 50 connections and took 11.88 s for the module.
  - The oracle observed 50 connections and 12.2 s.
- **AC8: not verified by me.** Foreman and the maker report 1 failed, 1283 passed, 47 skipped. That matches the base 1278 plus 6 new tests, with the single failure being the SPEC-excepted HOM-163 drill.

## Confirmed by the oracle

- **Red on base, green on the patch (Foreman's gate run, `gates/oracle-patch-turn-1.txt`, 17 of 18):**
  - `test_delivered_proof_module_is_postgres_marked_and_green`
  - `test_delivered_proof_drives_250_admissions_across_four_planes_to_exactly_200`
  - `test_delivered_proof_fills_two_nodes_to_exactly_20_each_across_planes`
  - `test_delivered_proof_fences_node_loss_under_recovery_scope_as_app_role`
  - `test_delivered_proof_bursts_surface_no_contention_errors`
  - `test_delivered_proof_module_is_bounded_in_time_and_connections`
- **Red on the patch:** `test_delivered_proof_caps_one_workspace_at_its_limit_across_planes`, which is oracle over-reach per the ruling above.
- **Green on base and on the patch (controls):** the 9 Part 1 PostgreSQL controls and the 2 SQLite controls.
- **My own single run (lane 1, under the lock), from a throwaway copy:**
  - The copy is base `7112407` extracted with `git archive` and patched with `git apply` in `rec02-t1/scratch/rev-c1/`.
  - Result: `test_voice_admission_postgres.py`: 6 passed in 11.88 s.
  - Global burst: 200 admitted and 50 `capacity_global`, peak 50 connections, 5 lock waiters.
  - Node burst: 40 admitted and 20 `capacity_node`.
  - Workspace burst: 57 admitted and 27 `capacity_workspace`, which is 15 plus 42 admitted.
  - The output line is in `scratch/rev-c1/run.txt`.

## Not verified

- AC8, the full suite: I rely on Foreman's and the maker's numbers.
- Finding 1 (recover/transition race and deadlock): traced by reading `admission.py` only, with no execution.
- Whether `peak_lock_waiters >= 1` can flake: judged from three observed bursts only.

CANDIDATES critical=0 high=0 medium=1 low=0 info=3
