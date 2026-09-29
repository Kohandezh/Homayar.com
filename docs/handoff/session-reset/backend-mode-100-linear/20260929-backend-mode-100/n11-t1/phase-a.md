# n11-t1 oracle, phase A (n11-t1-rev)

- SPEC: /root/foreman/20260929-backend-mode-100/n11-t1/SPEC.md
- Checkout: /root/foreman/20260929-backend-mode-100/wt/n11-t1-check (detached at 692affb, clean except the oracle file)
- Oracle: /root/foreman/20260929-backend-mode-100/wt/n11-t1-check/Homayar/backend/tests/test_oracle_n11.py
- Written from SPEC.md and the base source only. I did not read the maker worktree, handoffs, or any diff.
- Lint: `uv run ruff check` and `uv run ruff format --check` on the oracle both pass.

## Command and real output (base 692affb, before any fix)

```
cd /root/foreman/20260929-backend-mode-100/wt/n11-t1-check/Homayar
source /root/foreman/20260929-backend-mode-100/testenv.sh
flock /root/foreman/20260929-backend-mode-100/heavy.lock uv run pytest backend/tests/test_oracle_n11.py -q -p no:cacheprovider
```

```
FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF.............FFFFFFFFFFFFFFFFFF..... [ 36%]
....FFFFFFFFFFFFFFF..............F.FFFF......FFFFFFFFFFFFFFFFFFFFFFFFFFF [ 72%]
FFF..F..FF...........FFFFFFFFFFFFFFFFFFFFFF............                  [100%]
...
129 failed, 70 passed in 5.17s
```

The same command with `-rA` gave the same result (129 failed, 70 passed in 5.16s) and the per-case breakdown below.

## Red on base (129): the acceptance tests

| Case | Count | Reason on base (checked in the failure output) |
|---|---|---|
| test_retired_module_has_no_import_spec | 18 | find_spec is not None (the files exist) |
| test_importing_retired_module_fails_on_the_module_itself | 18 | 12 import cleanly (DID NOT RAISE). The 5 routers raise ModuleNotFoundError for `backend.middleware`, and user_service for `passlib`, which is the wrong module. This `exc.name` check is what stops those 6 from being falsely green. |
| test_forbidden_legacy_guard_lists_retired_module | 18 | not in REMOVED_MODULES |
| test_dead_setting_is_not_a_settings_field | 15 | field present |
| test_no_backend_code_reads_a_dead_setting | 1 | config.py (validator clause) plus legacy modules reference dead settings |
| test_production_accepts_retired_setting_keywords | 4 | ValidationError (the enable_voip_webhook clause, or null for a typed legacy field) |
| test_legacy_environment_variable_is_ignored | 10 | 5 names x {development, production}: the attribute is still exposed. Production ENABLE_VOIP_WEBHOOK=true fails with ValidationError from the clause. |
| test_legacy_environment_spelling_is_ignored | 18 | 9 spellings x 2 environments: lower case, mixed case, non-bool/non-int values, empty strings. Each is either exposed or a ValidationError. |
| test_all_legacy_environment_variables_together_are_ignored | 2 | exposed |
| test_production_from_environment_alone_ignores_legacy_variables | 1 | ValidationError (clause) |
| test_legacy_keys_in_dotenv_file_are_ignored | 2 | exposed (development) or ValidationError (production) |
| test_retirement_inventory_exists / _lists_retired_path (17) / _records_status_evidence_and_replacement / _records_unsafe_secret_comparison / _defers_dependency_pruning | 21 | doc absent |
| test_adr_draft_supersession_note | 1 | README absent |

## Green on base (70): regression controls. Each must stay green on the patch.

| Control | Count | Pairs with |
|---|---|---|
| test_live_module_still_imports | 13 | module deny cases. Covers the 4 SPEC live modules plus the closest siblings: backend.main, routers, routers.v1, routers.v1.auth, services, services.auth_service, database, database.relational, database.saas_models. |
| test_live_setting_is_still_a_settings_field | 12 | dead-field deny. Covers llm_model and redis_url, plus the adjacent live fields: llm_provider, zai_*, gapgpt_*, neo4j_*, enable_neo4j_projection. |
| test_every_other_base_setting_is_kept | 1 | dead-field deny. The frozen base field set (171) minus the 15 dead fields must all remain, which catches over-deletion. |
| test_no_setting_is_introduced | 1 | config.py ownership: removals only |
| test_live_setting_reference_is_still_detected | 1 | source-scan deny. Proves the scanner still finds `.llm_model`. |
| test_production_with_only_live_settings_is_valid | 1 | production accepts legacy keywords |
| test_production_still_rejects_insecure_configuration | 5 | legacy env accepted in production. The validator must still reject insecure cookies, short JWT, mock providers, sqlite, auto-schema. |
| test_live_environment_variables_are_still_read_next_to_legacy_ones | 2 | legacy env ignored: LLM_MODEL and REDIS_URL are still read (development and production) |
| test_production_from_environment_alone_is_valid_without_legacy_variables / _still_rejects_insecure_cookies | 2 | env-only production deny |
| test_application_serves_exactly_the_base_routes | 1 | AC5 frozen literal, 127 (method, path) pairs from the test-environment app at 692affb |
| test_production_application_serves_the_base_routes_without_documentation | 1 | AC5: the same set minus the 6 docs/openapi pairs, 121 |
| test_legacy_endpoint_is_not_served | 8 | webhook/copilot/dashboard/graph/ldap paths return 404 |
| test_liveness_endpoint_is_served | 1 | /healthz returns 200 |
| test_forbidden_legacy_guard_keeps_existing_entry | 7 | existing REMOVED_MODULES entries stay |
| test_forbidden_legacy_guard_only_appends | 1 | existing 7 entries stay as the prefix, no duplicates |
| test_random_number_guard_is_unchanged | 1 | RANDOM_ALLOWLIST and RANDOM_PATTERN unchanged |
| test_adr_draft_is_kept_unedited | 6 | frozen sha256 of the drafts 0026-0031, which must not be edited or deleted |
| test_accepted_adr_is_unchanged | 6 | frozen sha256 of the accepted 0026-0031, which must not change |

## Acceptance criteria to cases

1. AC1 (legacy modules gone): test_retired_module_has_no_import_spec[18], test_importing_retired_module_fails_on_the_module_itself[18]. Control: test_live_module_still_imports.
2. AC2 (guard covers them, existing entries and random guard unchanged): test_forbidden_legacy_guard_lists_retired_module[18]. Controls: test_forbidden_legacy_guard_keeps_existing_entry[7], test_forbidden_legacy_guard_only_appends, test_random_number_guard_is_unchanged.
3. AC3 (dead fields gone, clause gone, llm_model kept): test_dead_setting_is_not_a_settings_field[15], test_production_accepts_retired_setting_keywords[4] (clause), test_no_backend_code_reads_a_dead_setting. Controls: test_live_setting_is_still_a_settings_field[12], test_every_other_base_setting_is_kept, test_no_setting_is_introduced, test_live_setting_reference_is_still_detected.
4. AC4 (legacy env vars tolerated in development and production, not exposed): test_legacy_environment_variable_is_ignored[10], test_legacy_environment_spelling_is_ignored[18], test_all_legacy_environment_variables_together_are_ignored[2] (sentinel values absent from model_dump_json and repr), test_production_from_environment_alone_ignores_legacy_variables, test_legacy_keys_in_dotenv_file_are_ignored[2]. Controls: test_live_environment_variables_are_still_read_next_to_legacy_ones[2], test_production_with_only_live_settings_is_valid, test_production_still_rejects_insecure_configuration[5], test_production_from_environment_alone_is_valid_without_legacy_variables, test_production_from_environment_alone_still_rejects_insecure_cookies.
5. AC5 (live surface unchanged): test_application_serves_exactly_the_base_routes, test_production_application_serves_the_base_routes_without_documentation, test_legacy_endpoint_is_not_served[8], test_liveness_endpoint_is_served, test_live_module_still_imports[13]. The "test_authorization_matrix.py passes" part has no oracle case. It is covered by running that file (base: see below).
6. AC6 (retirement inventory): test_retirement_inventory_exists, test_retirement_inventory_lists_retired_path[17], test_retirement_inventory_records_status_evidence_and_replacement, test_retirement_inventory_records_unsafe_secret_comparison, test_retirement_inventory_defers_dependency_pruning.
7. AC7 (supersession note, drafts untouched): test_adr_draft_supersession_note. Controls: test_adr_draft_is_kept_unedited[6], test_accepted_adr_is_unchanged[6].
8. AC8 (full suite, no new failure): no oracle case. It is covered only by the SPEC full-suite command at the gate.

## Notes for phase C

- AC4 production construction follows test_api.py and test_provider_factory.py: explicit kwargs with environment=production, a postgres URL, 48-character jwt_secret and token_pepper, secure_cookies=True, and `_env_file=None`. I also set allow_mock_providers=False, auto_create_schema=False, enable_demo_account/google/phone=False and storage_backend=local explicitly, because testenv.sh exports ALLOW_MOCK_PROVIDERS=true. Without that, env would leak into the production validator. One case configures production entirely through environment variables (`Settings(_env_file=None)`), and one uses a dotenv file.
- An autouse fixture removes every dead setting name (upper and lower case) and LLM_MODEL from the ambient environment. It also sets sys.dont_write_bytecode.
- The first run created `Homayar/backend/websocket/__pycache__/` (find_spec of `backend.websocket.manager` imports the parent package). I removed it and moved dont_write_bytecode into the autouse fixture. The re-run does not recreate it. **Evaluate AC1 on a checkout with no leftover `backend/websocket/` directory.** A directory holding only `__pycache__` makes `backend.websocket` a namespace package, so find_spec is not None. That would be a real, if local, failure of AC1 and of the maker's own REMOVED_MODULES guard.
- AC6 and AC7 checks are substring checks and do not depend on the doc's layout. Each retired path must appear either as `<dir>/<file>.py` or as a dotted module; websocket is accepted as `websocket/` or `backend.websocket`. The inventory must contain "RETIRED", "mounted", "replacement", "capability not offered", "!=" with one of constant-time / compare_digest / timing, and ldap3, reportlab, WP01-T04 and HOM-28. The supersession README must contain "supersede", "docs/architecture/decisions", "0026" and "0031".
- Focused base baseline: `test_config.py` does not exist, so it is dropped from the SPEC focused command. `flock ... uv run pytest backend/tests/test_truthful_outputs.py backend/tests/test_authorization_matrix.py -q -p no:cacheprovider` gives 14 passed, 129 warnings in 9.99s.

## Out-of-scope observations (not tested)

- `Homayar/README.md:95-123` still documents LLM_MODEL_QC/PROBLEM/INSIGHT/TEACHER, LDAP_* and VOIP_WEBHOOK_SECRET as env settings, and describes the legacy 7-router API. README is not in the maker's file ownership, so after this change it will document settings that no longer exist.
- `backend/prompts/` (load_prompt plus copilot/qc/problem/insight prompt files) is used only by `services/copilot_service.py`. It is not in the SPEC legacy set, so it will be orphaned but still importable after retirement.
