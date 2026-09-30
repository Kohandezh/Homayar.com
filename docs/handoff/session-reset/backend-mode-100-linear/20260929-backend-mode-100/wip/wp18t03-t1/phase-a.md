# Phase A oracle record: wp18t03-t1 (HOM-101 / WP18-T03)

- Mission: 20260929-backend-mode-100. Author: wp18t03-t1-rev (independent reviewer), phase A, turn 1.
- Source: `/root/foreman/20260929-backend-mode-100/wp18t03-t1/SPEC.md`, including the Foreman rulings (Q1 rls.py lock GRANTED, so the "permission denied" clause of AC4 and the rls.py clause of AC11 are in force).
- I did not read the maker worktree, any branch, any diff, `handoffs/`, or another pane.
- Checkout: `/root/foreman/20260929-backend-mode-100/wt/wp18t03-t1-check`, detached at `7112407`. `git status --short` shows only `?? Homayar/backend/tests/test_oracle_wp18t03.py`.
- Oracle file: `/root/foreman/20260929-backend-mode-100/wt/wp18t03-t1-check/Homayar/backend/tests/test_oracle_wp18t03.py`
  - 60 test functions, 126 collected cases.
  - `ruff check` and `ruff format --check` are clean.
  - No comments except two `noqa` pragmas that ruff requires: S608 on the snapshot `SELECT`, and S105 on the throwaway probe password.
- Lane: L3 only (`testenv-l3.sh`, port 5435). Every PostgreSQL run was made under `flock "$LANE_LOCK"`.

## Command and real output (clean base 7112407)

```
cd /root/foreman/20260929-backend-mode-100/wt/wp18t03-t1-check/Homayar
source /root/foreman/20260929-backend-mode-100/testenv-l3.sh
flock "$LANE_LOCK" uv run pytest backend/tests/test_oracle_wp18t03.py -q -p no:cacheprovider --tb=line -rfEp
```

Tail of the real output:

```
backend/tests/test_oracle_wp18t03.py:341: AssertionError: assert {'business', ...professional'} == {'demo', 'professional'}
backend/tests/test_oracle_wp18t03.py:411: assert {'business': ...)), ...}, ...} == {'business': ...)), ...}, ...}
backend/tests/test_oracle_wp18t03.py:465: assert {'business': ...)), ...}, ...} == {'business': ...)), ...}, ...}
backend/tests/test_oracle_wp18t03.py:914: AssertionError: assert [] == [('demo', 2, 'config')]
backend/tests/test_oracle_wp18t03.py:1121: assert [IntegrityErr...ady exists.')] == []
backend/tests/test_oracle_wp18t03.py:1634: AssertionError: assert 'f5b1d7c3a9e2' == 'a1c3e5f7b9d2'
backend/tests/test_oracle_wp18t03.py:1674: AssertionError: assert 'plan_versions' in frozenset({'alembic_version', 'auth_identities', ...})
.../psycopg/cursor.py:97: sqlalchemy.exc.ProgrammingError: (psycopg.errors.UndefinedTable) relation "plan_versions" does not exist   (x51)
backend/tests/test_oracle_wp18t03.py:260: AttributeError: module 'backend.services.tenancy_service' has no attribute 'publish_plan_version'   (x20)
backend/tests/test_oracle_wp18t03.py:478|1163|1381: KeyError: 'current_version'   (x5)
backend/tests/test_oracle_wp18t03.py:972|1035|1045: KeyError: 'plan_version'   (x3)
.../sqlalchemy/engine/default.py:951: sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) no such column: current_version
98 failed, 28 passed, 318 warnings in 140.41s (0:02:20)
```

The same result appeared on an earlier full run: 98 failed, 28 passed, 0 errors.

## Red on base (98)

### Red because of the defect itself (D-12 rewrite, sign-up rewrite, seed race), not only missing names

| Case | Base behaviour it catches |
|---|---|
| `test_restart_keeps_operator_edits_visible_in_the_public_catalog` | After a DBA edit and a restart, owner `GET /billing/plans` lists `business` again. The restart reset `is_public` (D-12). |
| `test_signup_keeps_dba_edited_plan_rows` | A plain register+verify rewrites the DBA-edited rows. |
| `test_development_demo_account_path_keeps_dba_edited_rows` | `ensure_development_demo_account` rewrites the edited rows. |
| `test_direct_catalog_call_without_settings_keeps_edited_rows[dba,app]` | `ensure_plan_catalog(db)` reverts the edits. Red on base at the snapshot. |
| `test_concurrent_first_seeding_converges_on_one_catalog` | Two barrier-released seeders on an empty catalog raise `IntegrityError ... already exists`. |
| `test_config_version_is_logged_and_no_drift_is_reported` | A restart with a changed quota logs no `PLAN_VERSION_PUBLISHED ... source=config`. Base overwrites silently. |

### Red because the contract is absent on base

These fail on base because the contract they check is missing: the `plan_versions` table, `current_version`, `plan_version`, `publish_plan_version`, `PlanCatalogError`, `PlanVersion`, `PlanVersionImmutableError`, the new revision, and the rls classification. Each asserts the SPEC behaviour behind that name, as follows.

- **AC1**
  - `test_restart_keeps_operator_edited_plan_rows_and_reports_drift`: every plan row is identical after the restart. The snapshot includes `xmin` and the raw JSON text. The version count is unchanged, and the public catalog is correct before and after. There is exactly one `PLAN_CATALOG_DRIFT` per edited plan with exactly the drifted fields, none for `on_premise` or `demo`, and no values in the log text.
  - `test_idle_restart_appends_no_version_and_logs_no_drift`
  - `test_restart_reports_a_missing_current_version_row_as_drift`
- **AC2:** `test_signup_after_a_publish_leaves_plans_and_versions_unchanged` and `test_development_demo_account_leaves_plans_and_versions_unchanged`. Both are also allow-controls: the new subscription is pinned to the current demo version.
- **AC3:** `test_first_startup_seeds_exactly_the_catalog_with_version_one[1800,2400]` and `test_restart_recreates_only_a_deleted_catalog_key[business,demo]`.
- **AC4:**
  - `test_publish_appends_the_next_version_and_moves_the_row_to_it`
  - `test_publish_defaults_name_and_visibility_to_the_current_row`
  - `test_publish_stores_the_complete_dict_and_numbers_consecutively`
  - `test_publish_flushes_but_leaves_the_commit_to_the_caller`
  - `test_orm_update_of_a_plan_version_is_refused[6 columns]`
  - `test_orm_delete_of_a_plan_version_is_refused`
  - `test_orm_read_of_plan_versions_and_a_publish_are_allowed` (allow-control)
  - `test_app_role_cannot_update_or_delete_plan_versions_but_can_read_and_insert`
  - `test_publish_refuses_invalid_input_and_writes_nothing[33 spellings]`
  - `test_publish_accepts_valid_input[8]` (allow-controls)
- **AC5:**
  - `test_operator_publish_cannot_change_the_demo_allowance[5]`
  - `test_operator_publish_of_demo_concurrency_succeeds_and_survives_restarts` (allow)
  - `test_changed_demo_quota_appends_one_config_version_on_restart[3600,600]`
  - `test_config_version_carries_the_row_as_it_was_before_the_restart`
- **AC6:**
  - `test_signup_pins_the_subscription_to_the_current_demo_version`
  - `test_signup_after_a_demo_publish_gets_the_new_limits_and_pin`
  - `test_signup_maps_a_null_limit_to_unlimited`
  - `test_signup_creates_no_row_for_an_absent_metered_or_an_unmetered_key`
  - `test_signup_grants_the_pinned_version_not_a_drifted_row`
  - `test_signup_after_a_config_version_gets_the_new_quota_and_pin`
- **AC7:** `test_tenant_rows_survive_a_publish_and_a_config_version`.
- **AC8:** `test_concurrent_restarts_with_a_changed_demo_quota_append_one_version` and `test_concurrent_publishes_get_consecutive_versions`. Each runs 3 rounds.
- **AC9:** `test_upgrade_snapshots_an_existing_deployment_losslessly` (superuser, the CI shape) and `test_upgrade_behaves_the_same_for_a_plain_table_owner` (a NOSUPERUSER, NOBYPASSRLS login role that owns every table).
- **AC10:**
  - `test_downgrade_drops_only_history_and_pins_and_upgrade_restores`, which ends with `alembic check`
  - `test_exactly_one_new_revision_follows_the_base_head`
  - `test_head_revision_docstring_states_what_downgrade_drops`
- **AC11:** `test_plan_versions_is_classified_as_a_global_append_only_table`, `test_plan_versions_has_no_row_policy_and_tenant_tables_stay_forced` and `test_a_tenant_reads_its_own_pinned_subscription_and_never_another`.
- **AC12:** `test_owner_reads_published_terms_and_a_member_is_refused`.
- **AC13:** `test_sqlite_fallback_creates_the_schema_seeds_publishes_and_signs_up`.

## Green on base (28): regression controls, all intended

- **AC1/AC3 allow-controls, where base already behaves:**
  - `test_idle_restart_leaves_every_plan_row_untouched`: base's ORM compares equal values and emits no UPDATE, so `xmin` is unchanged.
  - `test_first_startup_seeds_the_catalog_rows_with_code_defaults`
  - `test_restart_recreates_a_deleted_catalog_key_with_code_defaults[business,demo]`
  - `test_changed_demo_quota_restart_updates_the_demo_allowance`: the demo row follows `DEMO_QUOTA_SECONDS`, which is the allow side of AC5(a).
- **AC6 controls:** `test_default_signup_keeps_the_tenant_visible_demo_allowance` (the tenant-visible 1800/1 is unchanged) and `test_signup_with_the_free_demo_disabled_creates_no_subscription`.
- **AC7(a) control:** `test_tenant_rows_survive_a_restart`.
- **AC10/AC11 "only the allowed change" controls:**
  - `test_existing_revision_files_are_byte_identical_to_base`: a digest of the 31 base revision files.
  - `test_rls_change_is_limited_to_classifying_plan_versions`: the base lists plus only `plan_versions`, and POLICIES / PRE_ORGANIZATION_POLICIES digests.
- **AC12 deny-cases and the member control:**
  - `test_billing_plans_refuses_writes_and_changes_nothing[POST,PUT,PATCH,DELETE]` (405, rows unchanged)
  - `test_owner_reads_the_seeded_catalog_and_a_member_is_refused`
  - `test_billing_routes_stay_read_only_in_the_application`
  - `test_publish_plan_version_is_not_reachable_from_request_handling_code`
  - `test_locked_file_is_byte_identical_to_base[11 files]`: the authorization matrix doc, the billing router/schemas/service, `entitlement_service.py`, `security/authorization.py`, `config.py`, and the four tests the SPEC says must pass unchanged.

## Pre-checks of the oracle's own expectations (no source file touched)

Each check ran on lane 3 through a stdin Python script (no file written).

1. **The SPEC-shaped migration as a plain table owner.** I ran the SPEC's migration steps as the probe role in a transaction, on an `a1c3e5f7b9d2` schema seeded like AC9:
   - create `plan_versions`;
   - add `current_version` with DEFAULT 1;
   - backfill;
   - add `subscriptions.plan_version` NOT NULL DEFAULT 1;
   - drop both defaults;
   - add `fk_subscription_plan_version`;
   - call `apply_row_level_security` with `plan_versions` in APPEND_ONLY.

   Results:
   - `subscriptions` and `entitlements` keep their `xmin`, so there was no DML, and every `plan_version` is 1.
   - `homayar_app` privileges on `plan_versions` are `(SELECT, INSERT, UPDATE, DELETE) = (True, True, False, False)`.
   - `pg_get_constraintdef` is `FOREIGN KEY (plan_key, plan_version) REFERENCES plan_versions(plan_key, version)`.
   - The column default is `(None, 'NO')`.
   - As a counter-example, `UPDATE subscriptions SET plan_version = 1` as that owner under FORCE touches 0 rows.

   So the AC9 owner-probe case is achievable, and it discriminates between the two approaches.
2. **`xmin` semantics on PG 17.** `SELECT ... FOR UPDATE` keeps `xmin`, and a no-op `UPDATE ... SET v = v` changes it. So the "writes nothing" snapshots tolerate row locks and still catch same-value rewrites.
3. **Alembic `Script.module`** exposes the revision module, so `backfill_plan_versions` is reached through the real head revision.

## Interpretations and limits (for Foreman and phase C)

- **Drift field names** are the SPEC's column names: `name`, `is_public`, `entitlements_json`. They are parsed as a comma-separated set, in any order and with optional spaces. An implementation that logs `entitlements` instead of `entitlements_json` goes red.
- **The `homayar` logger.** Records are captured by a handler on the `homayar` logger, so child loggers (`homayar.*`) are also accepted. A module `__name__` logger is not.
- **Config versions log `PLAN_VERSION_PUBLISHED ... source=config`.** The Names section defines one INFO line with `source=<source>`. This case is kept separate: `test_config_version_is_logged_and_no_drift_is_reported`.
- **The config version carries the row, not the current version** (`test_config_version_carries_the_row_as_it_was_before_the_restart`). This follows the AC5(a) wording ("carried from the row as it was before the restart") and "the row equals the new version". With a DBA-drifted demo row, the new version holds the drifted name and keys, with `audio_seconds` replaced.
- **Sign-up grants the pinned version, not the row** (`test_signup_grants_the_pinned_version_not_a_drifted_row`). This follows AC6 ("limit_value equal to that version's values"). A DBA drift of demo `concurrent_jobs` to 5 must still grant 1 (v1).
- **Validation spellings beyond the SPEC's list.** Phase C should weigh these:
  - a non-string key `{1: 1}` must raise `PlanCatalogError`, not `TypeError`;
  - dict and list values are refused, reading "ints within range, or None are accepted" as the whole accepted domain;
  - an integral float `10.0` is refused ("a value that is a float");
  - `"Professional"` is an unknown key (no case folding).
- **"Writes nothing" for a refused publish** is checked by committing the session after the exception, then comparing snapshots. Pending partial changes would therefore count as writes.
- **ORM immutability** is checked through unit-of-work flushes: attribute changes to name, is_public, entitlements_json, source, version (PK) and created_at, plus `session.delete`. Bulk `update()` statements bypass mapper events by design. The DB-level REVOKE covers them for the app role.
- **"Unreachable from HTTP"** is checked statically: the identifier `publish_plan_version` appears only in `services/tenancy_service.py`, `main.py`, tests and migrations. The 405 deny-cases and the GET-only route inventory back this up.
- **The docstring check is lenient:** the head module docstring must mention downgrade and (`plan_versions` or `history`). Phase C should read the actual text.
- **The AC9 "first start writes nothing" case** runs with `auto_create_schema=False` and `DEMO_QUOTA_SECONDS`=1800, which equals the legacy demo value.
- **Log capture after alembic.** Alembic's `fileConfig` in-process disables existing loggers. The oracle re-enables `homayar` only while it captures, and attaches its own handler.
- **Environment note.** The session scratchpad directory is shared with other agents. One interim run output of mine, `scratchpad/phase-a-run.txt`, was interleaved with another team's run (rec02) under the same name. Later runs were read from stdout only, and I removed my own `scratchpad/alembic-sqlite.db`. No repository or lane database state outside lane 3 was touched.

## Acceptance criteria and covering cases

1. **AC1**, startup never overwrites existing plans:
   - `test_restart_keeps_operator_edited_plan_rows_and_reports_drift`
   - `test_restart_keeps_operator_edits_visible_in_the_public_catalog`
   - `test_idle_restart_leaves_every_plan_row_untouched`
   - `test_idle_restart_appends_no_version_and_logs_no_drift`
   - `test_direct_catalog_call_without_settings_keeps_edited_rows[dba,app]`
   - `test_restart_reports_a_missing_current_version_row_as_drift`
2. **AC2**, sign-up never modifies the catalog:
   - `test_signup_keeps_dba_edited_plan_rows`
   - `test_signup_after_a_publish_leaves_plans_and_versions_unchanged`
   - `test_development_demo_account_leaves_plans_and_versions_unchanged`
   - `test_development_demo_account_path_keeps_dba_edited_rows`
3. **AC3**, missing keys are inserted and nothing else:
   - `test_first_startup_seeds_exactly_the_catalog_with_version_one[1800,2400]`
   - `test_first_startup_seeds_the_catalog_rows_with_code_defaults`
   - `test_restart_recreates_only_a_deleted_catalog_key[business,demo]`
   - `test_restart_recreates_a_deleted_catalog_key_with_code_defaults[business,demo]`
4. **AC4**, versions are immutable and numbered, with validation:
   - `test_publish_appends_the_next_version_and_moves_the_row_to_it`
   - `test_publish_defaults_name_and_visibility_to_the_current_row`
   - `test_publish_stores_the_complete_dict_and_numbers_consecutively`
   - `test_publish_flushes_but_leaves_the_commit_to_the_caller`
   - `test_orm_update_of_a_plan_version_is_refused[*]`
   - `test_orm_delete_of_a_plan_version_is_refused`
   - `test_orm_read_of_plan_versions_and_a_publish_are_allowed`
   - `test_app_role_cannot_update_or_delete_plan_versions_but_can_read_and_insert`
   - `test_publish_refuses_invalid_input_and_writes_nothing[*]`
   - `test_publish_accepts_valid_input[*]`
5. **AC5**, the demo allowance is owned by `DEMO_QUOTA_SECONDS`:
   - (a) `test_changed_demo_quota_appends_one_config_version_on_restart[3600,600]`, `test_config_version_carries_the_row_as_it_was_before_the_restart`, `test_config_version_is_logged_and_no_drift_is_reported` and `test_changed_demo_quota_restart_updates_the_demo_allowance`
   - (b) `test_operator_publish_cannot_change_the_demo_allowance[*]`
   - (c) `test_operator_publish_of_demo_concurrency_succeeds_and_survives_restarts`
6. **AC6**, real entitlements pinned at sign-up:
   - `test_signup_pins_the_subscription_to_the_current_demo_version`
   - `test_default_signup_keeps_the_tenant_visible_demo_allowance`
   - `test_signup_after_a_demo_publish_gets_the_new_limits_and_pin`
   - `test_signup_maps_a_null_limit_to_unlimited`
   - `test_signup_creates_no_row_for_an_absent_metered_or_an_unmetered_key`
   - `test_signup_grants_the_pinned_version_not_a_drifted_row`
   - `test_signup_after_a_config_version_gets_the_new_quota_and_pin`
   - `test_signup_with_the_free_demo_disabled_creates_no_subscription`
   - The clause "`test_billing_api.py` passes unmodified" has no oracle case. It is covered by the focused command, and the file may be extended additively, so it is not hashed.
7. **AC7**, tenant rows are never touched: `test_tenant_rows_survive_a_restart` and `test_tenant_rows_survive_a_publish_and_a_config_version`. Both include a DBA limit of 5000 and compare every column plus `xmin`.
8. **AC8**, concurrency: (a) `test_concurrent_first_seeding_converges_on_one_catalog`, (b) `test_concurrent_restarts_with_a_changed_demo_quota_append_one_version`, (c) `test_concurrent_publishes_get_consecutive_versions`.
9. **AC9**, the upgrade preserves a deployment: `test_upgrade_snapshots_an_existing_deployment_losslessly` and `test_upgrade_behaves_the_same_for_a_plain_table_owner`. The first also covers the first-start no-op, backfill returning 0 and then 1/0, and `alembic check`.
10. **AC10**, downgrade and round trip:
    - `test_downgrade_drops_only_history_and_pins_and_upgrade_restores`
    - `test_exactly_one_new_revision_follows_the_base_head`
    - `test_head_revision_docstring_states_what_downgrade_drops`
    - `test_existing_revision_files_are_byte_identical_to_base`
    - `test_locked_file_is_byte_identical_to_base[test_retention_policy, test_ingestion_ledger, test_media_lifecycle]`
    - The clauses "`test_migrations_upgrade_head_matches_models` passes" and "the existing downgrade tests pass" are covered by the focused command, not by an oracle case. The oracle runs its own `alembic check` after a round trip and after an upgrade from `a1c3e5f7b9d2`.
11. **AC11**, RLS and grants:
    - `test_plan_versions_is_classified_as_a_global_append_only_table`
    - `test_rls_change_is_limited_to_classifying_plan_versions`
    - `test_plan_versions_has_no_row_policy_and_tenant_tables_stay_forced`
    - `test_a_tenant_reads_its_own_pinned_subscription_and_never_another`
    - grants after a real `alembic upgrade`, inside the AC9/AC10 cases
    - sign-up as `homayar_app` through the composite FK, in every PG sign-up case
12. **AC12**, authorization unchanged:
    - `test_billing_plans_refuses_writes_and_changes_nothing[*]`
    - `test_owner_reads_published_terms_and_a_member_is_refused`
    - `test_owner_reads_the_seeded_catalog_and_a_member_is_refused`
    - `test_billing_routes_stay_read_only_in_the_application`
    - `test_publish_plan_version_is_not_reachable_from_request_handling_code`
    - `test_locked_file_is_byte_identical_to_base[authorization-matrix.md, test_authorization_matrix.py, ...]`
13. **AC13**, no regression:
    - `test_sqlite_fallback_creates_the_schema_seeds_publishes_and_signs_up` covers the SQLite clause.
    - The full lane-3 suite and ruff on changed files have **no oracle case**. The SPEC's full-suite and lint commands cover them.

### SPEC deny-case and allow-control pairs

| Deny-case | Allow-control |
|---|---|
| Restart reverting edits: `test_restart_keeps_operator_edits_visible_in_the_public_catalog` and `test_restart_keeps_operator_edited_plan_rows_and_reports_drift` | `test_restart_recreates_only_a_deleted_catalog_key[*]` |
| Sign-up rewriting: `test_signup_keeps_dba_edited_plan_rows` | `test_signup_after_a_publish_leaves_plans_and_versions_unchanged`, which also checks the pin |
| ORM or raw UPDATE/DELETE: `test_orm_*_refused` and `test_app_role_cannot_update_or_delete_*` | `test_orm_read_of_plan_versions_and_a_publish_are_allowed`, plus INSERT/SELECT in the raw case |
| Invalid publish inputs: `test_publish_refuses_invalid_input_and_writes_nothing[*]` | `test_publish_accepts_valid_input[zero-and-unlimited]` and the others |
| Operator changing demo `audio_seconds`: `test_operator_publish_cannot_change_the_demo_allowance[*]` | `test_operator_publish_of_demo_concurrency_succeeds_and_survives_restarts` and `test_changed_demo_quota_appends_one_config_version_on_restart[*]` |
| Publish or config changing tenant rows: `test_tenant_rows_survive_a_publish_and_a_config_version` | `test_signup_after_a_demo_publish_gets_the_new_limits_and_pin` and `test_signup_after_a_config_version_gets_the_new_quota_and_pin` |
| Writes to `/billing/plans`: `test_billing_plans_refuses_writes_and_changes_nothing[*]` | Owner GET 200 with published values, member 403: `test_owner_reads_published_terms_and_a_member_is_refused` |
| Cross-tenant read | Own read with pin: both in `test_a_tenant_reads_its_own_pinned_subscription_and_never_another` |
| Upgrade changing edited or non-catalog rows | An unedited `demo` also gets v1 equal to itself: both in `test_upgrade_snapshots_an_existing_deployment_losslessly` |
| Second backfill inserting (returns 0) | First call on an unversioned plan returns 1: same case |
