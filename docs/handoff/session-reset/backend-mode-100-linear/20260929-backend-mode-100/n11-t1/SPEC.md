# SPEC — team n11-t1 (mission 20260929-backend-mode-100)

Orchestration mode: `foreman_native_adapted` (see ledger). Role separation, blind oracle, bare-diff review and the
two-round cap apply unchanged.

## Task
- Linear: HOM-152 [N11] Migration and legacy retirement (reuses F01) https://linear.app/homayar/issue/HOM-152
  (parent HOM-141 [CAP]).
- Type: Implementation (retirement / dead-code removal with guard). Work unit: one root cause — an unmounted legacy
  stack that is still importable and still carries an unsafe secret comparison.
- Dependency group 0. Priority P0.

## Base
`BASE_SHA` = the accepted integration revision after HOM-140 (filled in by Foreman when the worktrees are created;
see the "Worktrees" section).

## Context (verified by Foreman against the source)
- `backend/main.py:178-193` mounts only the 16 v1 routers. None of the modules below is mounted or imported by live code.
- The legacy set is closed: its members import only each other.
  - Routers: `backend/routers/{copilot,dashboard,graph,ldap_sync,webhook}.py`
  - Services: `backend/services/{copilot,dashboard,graph,issue_aggregation,call,ad,user,pdf,redis}_service.py`,
    `backend/services/llm_router.py`
  - Other: `backend/database/models.py` (Pydantic), `backend/websocket/` (`__init__.py` + a 2-line `manager.py` stub)
- `backend/routers/webhook.py:46-48,107-109` compares a shared secret with `!=` (not constant-time). Unmounted today;
  it must never be re-activated.
- Dead settings in `backend/config.py`: `llm_model_qc`, `llm_model_problem`, `llm_model_insight`, `llm_model_teacher`,
  `llm_model_copilot` (lines 241-245; only consumer `services/llm_router.py`), `ldap_server`, `ldap_port`,
  `ldap_use_ssl`, `ldap_bind_dn`, `ldap_bind_password`, `ldap_search_base`, `ldap_user_ou` (262-268; only consumer
  `services/ad_service.py`), `voip_webhook_secret`, `enable_voip_webhook` (269-270; only consumer
  `routers/webhook.py`), `admin_default_password` (271; only consumer `services/user_service.py`), and the production
  validator clause at line 404 that references `enable_voip_webhook`. `llm_model` (240) is LIVE
  (`providers/factory.py:121`) and stays. `Settings` uses `extra="ignore"` (`config.py:21`), so environment variables
  for removed fields are ignored rather than rejected.
- `backend/database/neo4j_connection.py` is LIVE (`main.py:100,118` optional projection) and stays.
  `backend/database/seed.py` stays (development seed; allow-listed in the truthful-output guard).
- `backend/tests/test_truthful_outputs.py:20-29` has `REMOVED_MODULES`, parametrizing
  `test_fabricated_analytics_modules_are_gone` (asserts `importlib.util.find_spec(module) is None`).
- No test, env example or compose file references the legacy set or the dead settings.
- Duplicate ADR drafts: `Homayar/docs/handoff/session-a/adr/0026…0031` differ from the accepted
  `docs/architecture/decisions/0026…0031`.

## Acceptance criteria (observable)
1. Every module in the legacy set is removed: `importlib.util.find_spec(<module>)` is `None` for each of
   `backend.routers.{copilot,dashboard,graph,ldap_sync,webhook}`,
   `backend.services.{copilot_service,dashboard_service,graph_service,issue_aggregation_service,call_service,ad_service,user_service,pdf_service,redis_service,llm_router}`,
   `backend.database.models`, `backend.websocket`, `backend.websocket.manager`.
2. The forbidden-legacy-path guard covers them: each of those module names is in `REMOVED_MODULES` in
   `backend/tests/test_truthful_outputs.py`, and the existing entries stay. `RANDOM_ALLOWLIST`/`RANDOM_PATTERN` unchanged.
3. The dead settings are gone from `Settings` (`Settings.model_fields` has none of the 15 names above), the validator
   clause referencing `enable_voip_webhook` is gone, and `llm_model` remains a field.
4. Unknown legacy environment variables are tolerated: constructing `Settings` with any of `LDAP_SERVER`,
   `VOIP_WEBHOOK_SECRET`, `ENABLE_VOIP_WEBHOOK`, `LLM_MODEL_QC`, `ADMIN_DEFAULT_PASSWORD` set (in the environment)
   succeeds in development AND production configuration, and the values are not exposed as attributes.
5. The live surface is unchanged: the set of `(method, path)` routes on the application is identical to `BASE_SHA`,
   `test_authorization_matrix.py` passes, and the live modules `backend.database.neo4j_connection`,
   `backend.database.seed`, `backend.providers.factory`, `backend.services.rate_limit_service` still import.
6. Retirement inventory: a backend architecture document `Homayar/docs/architecture/legacy-retirement.md` lists every
   retired path with: what it was, evidence it was dead (not mounted; importers), its canonical replacement (or "none —
   capability not offered"), status `RETIRED`, and the unsafe-pattern note for the webhook secret comparison. It also
   records that dependency pruning (`ldap3` optional extra, `reportlab`) is deferred to WP01-T04 (HOM-28).
7. Supersession note: `Homayar/docs/handoff/session-a/adr/README.md` (new) states that 0026–0031 there are superseded
   drafts and names `docs/architecture/decisions/0026…0031` as authoritative. Drafts themselves are not edited or deleted.
8. Full backend suite on PostgreSQL: no new failure relative to `BASE_SHA` (expected 0 failed); passed count may rise by
   the new guard parametrizations.

## Deny-cases and closest allow-controls
- DENY: `import backend.routers.webhook` (and every retired module) -> ModuleNotFoundError / find_spec None.
  ALLOW-CONTROL: `import backend.database.neo4j_connection`, `backend.providers.factory`, `backend.services.rate_limit_service`.
- DENY: `Settings.model_fields` contains `voip_webhook_secret` (or any removed name).
  ALLOW-CONTROL: `Settings.model_fields` contains `llm_model` and `redis_url`.
- DENY: a new mounted route appears or a live route disappears.
  ALLOW-CONTROL: the exact base route set is still served.

## File ownership
Maker may change/delete: the files in the legacy set (delete only); `backend/config.py` (remove the listed fields and
the one validator clause only); `backend/tests/test_truthful_outputs.py` (append to `REMOVED_MODULES` only);
new tests under `backend/tests/` for AC3–AC5 if needed; new docs `Homayar/docs/architecture/legacy-retirement.md` and
`Homayar/docs/handoff/session-a/adr/README.md`.
Maker must not change: `main.py`, `neo4j_connection.py`, `seed.py`, migrations, `saas_models.py`, `rls.py`,
`authorization.py`, `pyproject.toml`, `uv.lock`, any ADR file, any frontend/website file.
Checker: only its oracle file `Homayar/backend/tests/test_oracle_n11.py` in the CHECKER worktree and its records
under `/root/foreman/20260929-backend-mode-100/n11-t1/{oracle,reviews}/`.

## Allowed and forbidden actions
Same as stab01-t1: no commit/push/branch/PR/Linear, no migrations, no DB reset, no network, no contact with other
agents, no reading the other role's worktree or files.

## Verification commands (run from `<worktree>/Homayar` after `source /root/foreman/20260929-backend-mode-100/testenv.sh`)
EVERY command that touches PostgreSQL runs under the mission lock.
Focused:
```
flock /root/foreman/20260929-backend-mode-100/heavy.lock uv run pytest backend/tests/test_truthful_outputs.py backend/tests/test_authorization_matrix.py backend/tests/test_config.py -q -p no:cacheprovider
```
(if `test_config.py` does not exist, drop it from the command and say so)
Full suite:
```
flock /root/foreman/20260929-backend-mode-100/heavy.lock uv run pytest backend/tests -q -p no:cacheprovider -rs
```
Lint on changed Python files: `uv run ruff check <files> && uv run ruff format --check <files>`

## Known baseline
Filled in at worktree creation (expected: the HOM-140 accepted revision, 0 failed / 1055 passed / 46 skipped).

## Communication map, envelope, turn protocol, team directory
As in stab01-t1/SPEC.md, with team `n11-t1`, names `n11-t1-impl` and `n11-t1-rev`, directory
`/root/foreman/20260929-backend-mode-100/n11-t1/`.

## Models
Implementer: opus (removal touching config/security guard). Reviewer: opus.
