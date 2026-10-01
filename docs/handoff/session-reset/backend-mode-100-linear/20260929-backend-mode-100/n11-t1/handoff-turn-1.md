# Handoff: n11-t1, phase B, turn 1

- FROM: n11-t1-impl. TO: foreman.
- Linear HOM-152 (N11, reuses F01).
- Worktree `/root/foreman/20260929-backend-mode-100/wt/n11-t1`, branch `mission/n11-t1` at `692affb`. Nothing is committed or staged.
- The container restarted mid-turn and killed the background full-suite run. All file changes survived (checked with `git status`). After the restart I re-ran the focused command, the new tests, ruff, the route diff and the import probe, and ran the full suite in the foreground (see "Full suite" below).

## What changed

`git status --short`: 29 `D`, 3 `M`, 3 `??`. Total diff: 32 tracked files, +25 / -2321 lines, plus 3 new untracked files.

Deleted (AC1). These are plain `rm`, not `git rm`, so `git diff` shows them. Both package directories were removed entirely, including `__pycache__` (A2):

- `Homayar/backend/routers/copilot.py`
- `Homayar/backend/routers/dashboard.py`
- `Homayar/backend/routers/graph.py`
- `Homayar/backend/routers/ldap_sync.py`
- `Homayar/backend/routers/webhook.py`
- `Homayar/backend/services/copilot_service.py`
- `Homayar/backend/services/dashboard_service.py`
- `Homayar/backend/services/graph_service.py`
- `Homayar/backend/services/issue_aggregation_service.py`
- `Homayar/backend/services/call_service.py`
- `Homayar/backend/services/ad_service.py`
- `Homayar/backend/services/user_service.py`
- `Homayar/backend/services/pdf_service.py`
- `Homayar/backend/services/redis_service.py`
- `Homayar/backend/services/llm_router.py`
- `Homayar/backend/database/models.py`
- `Homayar/backend/websocket/` (`__init__.py`, `manager.py`; directory gone)
- `Homayar/backend/prompts/` (`__init__.py` plus 10 `.txt` files; directory gone), per A1

Modified:

- `Homayar/backend/config.py` (AC3). Removed exactly the 15 listed fields: `llm_model_qc`, `llm_model_problem`, `llm_model_insight`, `llm_model_teacher`, `llm_model_copilot`, `ldap_server`, `ldap_port`, `ldap_use_ssl`, `ldap_bind_dn`, `ldap_bind_password`, `ldap_search_base`, `ldap_user_ou`, `voip_webhook_secret`, `enable_voip_webhook`, `admin_default_password`. Also removed the production validator clause `if self.enable_voip_webhook and not self.voip_webhook_secret: raise ...`. That is 17 deleted lines and no other change. `llm_model` and `redis_url` stay. `extra="ignore"` is unchanged.
- `Homayar/backend/tests/test_truthful_outputs.py` (AC2). This is an append to `REMOVED_MODULES` only: a 4-line comment and 19 module names (the 18 from AC1 plus `backend.prompts`, per A1). The existing entries, `RANDOM_ALLOWLIST`, `RANDOM_PATTERN` and both tests are byte-identical.
- `Homayar/README.md` (AC9 / A4). Only the listed lines changed:
  - the "Optional: per-agent model override" block;
  - the "LDAP / Active Directory" block;
  - the "VOIP Webhook" env block;
  - the API rows for `/api/dashboard`, `/api/graph`, `/api/copilot`, `/api/webhook` and `/api/ldap`;
  - the project-structure lines for `models.py`, the five routers, the eight retired services and `prompts/`;
  - one added pointer line, plus a blank separator line: `...are retired: see [docs/architecture/legacy-retirement.md](docs/architecture/legacy-retirement.md).`

New (untracked; Foreman needs them in REVIEW-INPUT.diff, for example via `git add -N` or an untracked-inclusive diff):

- `Homayar/backend/tests/test_legacy_retirement.py` (AC3–AC5, 35 tests):
  - the 15 retired names are not in `Settings.model_fields`;
  - `llm_model` and `redis_url` still are;
  - each of `LDAP_SERVER`, `VOIP_WEBHOOK_SECRET`, `ENABLE_VOIP_WEBHOOK`, `LLM_MODEL_QC` and `ADMIN_DEFAULT_PASSWORD`, set alone and all together, is tolerated in development and production. The attribute is absent, it is not in `model_dump()`, and `model_extra` is empty;
  - production with `ENABLE_VOIP_WEBHOOK=true` and no secret is accepted, so the validator clause is gone;
  - the 4 live modules import;
  - no `/dashboard|graph|copilot|webhook|ldap|voip|ws` route is mounted, with `/api/v1/meetings` as the allow-control.
- `Homayar/docs/architecture/legacy-retirement.md` (AC6 / A3). One row per full repo-relative path: 5 routers, 10 services, `models.py`, the `websocket/` directory and its 2 files, and the `prompts/` directory and its 11 files. Each row has what it was, evidence, the canonical replacement or "none — capability not offered", and status `RETIRED`. The doc also has a settings table (15 fields plus the validator clause), the webhook unsafe-pattern note, and the deferral of `ldap3` (optional extra) and `reportlab` pruning to WP01-T04 (HOM-28).
- `Homayar/docs/handoff/session-a/adr/README.md` (AC7). It says the drafts 0026–0031 are superseded and maps each one to `docs/architecture/decisions/0026…0031` as authoritative. The drafts themselves are untouched.

No file outside the maker's ownership was changed. `main.py`, `neo4j_connection.py`, `seed.py`, migrations, `pyproject.toml`, `uv.lock`, the ADRs and the frontend are untouched.

## Dead-code evidence, re-proved in this worktree before any deletion (`692affb`)

1. **Route table.** `backend/main.py:178-193` includes only the 16 `backend.routers.v1` routers. The app built by `create_app()` serves 127 `(method, path)` pairs, and 0 of them match `copilot|dashboard|graph|ldap|webhook`. Script: `scratchpad/routes.py`; output: `routes-base.txt`, sha256 `7e7af36a2863581c67b53459e9dacd4961b346ad07365f7f1dc2ec89d79243aa`.
2. **Importers.** I grepped `Homayar/` for each module in the dotted form (`backend\.<pkg>\.<mod>\b`), the `from backend.<pkg> import <mod>` form and the relative form. File types: `*.py`, toml, cfg, ini, yml, yaml, sh, Dockerfile and txt. Every importer found is itself in the retired set:

   | Module | Importers at `692affb` |
   |---|---|
   | `routers.copilot`, `routers.dashboard`, `routers.graph`, `routers.ldap_sync`, `routers.webhook` | none |
   | `services.copilot_service` | `routers/copilot.py` |
   | `services.dashboard_service` | `routers/dashboard.py`, `services/copilot_service.py` |
   | `services.graph_service` | `routers/graph.py` |
   | `services.issue_aggregation_service` | `routers/dashboard.py` |
   | `services.call_service` | `routers/webhook.py`, `services/pdf_service.py` (lazy) |
   | `services.ad_service` | `routers/ldap_sync.py`, `services/user_service.py` (lazy) |
   | `services.user_service` | `routers/webhook.py` |
   | `services.pdf_service`, `services.redis_service` | none |
   | `services.llm_router` | `services/copilot_service.py`, `services/issue_aggregation_service.py` |
   | `database.models` | `routers/copilot.py`, `services/user_service.py` |
   | `websocket`, `websocket.manager` | none |
   | `prompts` | `services/copilot_service.py` (`load_prompt("copilot_intent")` and `load_prompt("copilot_response")` only; the qc, problem, insight and teacher `.txt` files had no reader at all) |

   A repo-wide grep over everything except `node_modules`, `.venv` and `.git` found no reference outside docs. That covered `infra/`, `.github/`, the Dockerfiles, `infra/product-source.lock` (its required paths do not include any retired path), `Homayar/Makefile`, `docker-compose.yml` and `.env.example`. There are no dynamic imports either: `importlib`, `__import__` and `find_spec` appear only in `test_truthful_outputs.py`.
3. **Dead settings.** The only consumers of the 15 fields were `services/llm_router.py`, `services/ad_service.py`, `routers/webhook.py`, `services/user_service.py` and the `config.py:404` validator. `enable_voip_webhook` was never read by `routers/webhook.py`. No env example, compose file or CI file names any of them; only `Homayar/README.md` did (fixed under AC9).
4. **Importability at base** (`scratchpad/probe.py`): `find_spec` found all 19 modules. The five routers raised `ModuleNotFoundError: No module named 'backend.middleware'` on import, because `middleware.auth` was removed in WP03-T01. `services.user_service` raised `ModuleNotFoundError: No module named 'passlib'`. The rest imported but had no live caller. The probe created `__pycache__` in `websocket/` and `prompts/`; both directories were then deleted whole.

## Test-first record

- **Red, before deletion.** After appending to `REMOVED_MODULES` and adding `test_legacy_retirement.py`, I ran `flock ... uv run pytest backend/tests/test_truthful_outputs.py backend/tests/test_legacy_retirement.py -q -p no:cacheprovider`. Result: **47 failed, 15 passed**. The failures were:
  - the 19 new `test_fabricated_analytics_modules_are_gone[...]` parameters;
  - the 15 `test_retired_setting_is_not_a_field[...]` cases;
  - the 10 single-variable env cases;
  - the 2 combined env cases;
  - `test_production_no_longer_requires_a_webhook_secret`, which failed with `ValidationError: VOIP_WEBHOOK_SECRET is required when the webhook is enabled`.

  The 15 passes were the pre-existing guards plus the live-settings, live-module and route allow-controls.
- **Green, after deleting the modules and editing `config.py`.** The same command gave **62 passed**.

## Verification (all PostgreSQL-touching commands under `flock /root/foreman/20260929-backend-mode-100/heavy.lock`, from `wt/n11-t1/Homayar` after `source testenv.sh`)

Focused. `backend/tests/test_config.py` does not exist (`ls: cannot access 'backend/tests/test_config.py'`), so it is dropped:
```
$ flock /root/foreman/20260929-backend-mode-100/heavy.lock uv run pytest backend/tests/test_truthful_outputs.py backend/tests/test_authorization_matrix.py -q -p no:cacheprovider
33 passed, 129 warnings in 11.19s
$ flock /root/foreman/20260929-backend-mode-100/heavy.lock uv run pytest backend/tests/test_legacy_retirement.py -q -p no:cacheprovider
35 passed in 0.55s
```
Lint on the changed Python files:
```
$ uv run ruff check backend/config.py backend/tests/test_truthful_outputs.py backend/tests/test_legacy_retirement.py && uv run ruff format --check backend/config.py backend/tests/test_truthful_outputs.py backend/tests/test_legacy_retirement.py
All checks passed!
3 files already formatted
```

Full suite (AC8). The SPEC command `flock ... uv run pytest backend/tests -q -p no:cacheprovider -rs` takes about 11 minutes (the `stab01-t1/gates/turn-2` baseline ran for 679.89 s). That exceeds the 600 s limit on one foreground tool call, and background jobs do not survive restarts. I therefore ran the identical flags over `backend/tests` as five consecutive foreground chunks, each under the lock. The chunks are the 67 `test_*.py` files sorted and split into five, an exact cover with no duplicates (checked with `diff`).

Coverage proof: `flock ... uv run pytest backend/tests --collect-only -q -p no:cacheprovider` gives `1155 tests collected`, and the chunks collect 133 + 267 + 174 + 413 + 168 = 1155.
```
$ flock ... uv run pytest <test_admin_api.py .. test_insights_shares_api.py, 15 files> -q -p no:cacheprovider -rs
133 passed, 719 warnings in 131.65s (0:02:11)
$ flock ... uv run pytest <test_job_failure_policy.py .. test_media_validation.py, 13 files> -q -p no:cacheprovider -rs
267 passed, 295 warnings in 105.96s (0:01:45)
$ flock ... uv run pytest <test_meeting_quality.py .. test_retention_lifecycle.py, 13 files> -q -p no:cacheprovider -rs
SKIPPED [3] backend/tests/test_provider_contracts.py: Padyar AI Gateway API contract not supplied (see padyar.py)
171 passed, 3 skipped, 364 warnings in 88.85s (0:01:28)
$ flock ... uv run pytest <test_retention_policy.py .. test_storage_providers.py, 13 files> -q -p no:cacheprovider -rs
SKIPPED [41 in total] backend/tests/test_storage_contract.py...: NOT_RUN/BLOCKED_EXTERNAL: HOMAYAR_LIVE_S3_TEST is not 1 (live S3 run is opt-in)
372 passed, 41 skipped, 206 warnings in 80.97s (0:01:20)
$ flock ... uv run pytest <test_tenant_scope.py .. test_voicemail_pipeline.py, 13 files> -q -p no:cacheprovider -rs
SKIPPED [1] backend/tests/test_tombstone_reapply.py:209: file-level backup/restore drill runs on SQLite; PostgreSQL uses the simulated restore
SKIPPED [1] backend/tests/test_transport_drills.py:433: requires TEST_REDIS_CONTAINER and the docker CLI
166 passed, 2 skipped, 253 warnings in 212.47s (0:03:32)
```
**Total: 0 failed, 1109 passed, 46 skipped, 0 errors.** The base (`d4daf6e`, same code as `692affb`) was 0 failed, 1055 passed, 46 skipped. The +54 passed are the 19 new guard parameters plus the 35 tests in `test_legacy_retirement.py`. The skip set is identical to the base gate log (46 skips, the same reasons, compared with `diff`). Chunk file lists and outputs are in `/tmp/claude-0/-home-user-Homayar-com/de27f501-1327-54de-980e-22778b73f1ba/scratchpad/n11/` (`part00`–`part04`, `full-part00.txt`–`full-part04.txt`).

AC5 route identity. `scratchpad/routes.py` run after the change, and again after the restart, produces a byte-identical route list: 127 pairs, sha256 `7e7af36a…d79243aa`, and `diff` is empty. `test_authorization_matrix.py` passes (above).

AC1 probe after the change: all 19 modules give `find_spec=None` and `ModuleNotFoundError`, and `backend/websocket` and `backend/prompts` do not exist. Live modules: `neo4j_connection`, `seed`, `providers.factory` and `rate_limit_service` import OK.

Extra, informational: whole-backend ruff and mypy compared with a `git archive 692affb` export.
- ruff: 0 new findings. 418 findings at base, 220 after; the removed ones are in the deleted files. The migration lines differ only because of the path-relative per-file ignore on the exported copy.
- mypy: 0 new errors. 525 errors in 87 files at base, 487 in 75 files after. My test's one finding, `BaseRoute.path`, was fixed with `getattr`. The `tenancy_service.py` lines are the same errors with different note wording.

## Notes for review and follow-ups (not fixed; outside the file ownership)

1. **AC9 scope versus its goal sentence.** A4 lists the README edits exhaustively, so I left these still-stale README parts, which also document removed code:
   - the "VOIP Webhook Integration" section (curl examples for `/api/webhook/voip/call-event`, README lines 160–177 after the edit);
   - the Security bullets "Webhook secret verification" and "Cypher injection protection";
   - "29 endpoints across 7 routers";
   - the `routers/` tree, which now ends on `├── calls.py` because the `calls.py` line was not in the edit list.

   The pre-existing stale lines for `middleware/auth.py`, `routers/auth.py`, `routers/calls.py`, `analysis_service.py` and `/api/auth` / `/api/calls` are also untouched. If Foreman reads AC9's first sentence as covering the webhook section, that needs a scope extension.
2. Other docs still describe the retired files as present: `docs/architecture/auth-session-contract.md` §Removed ("kept only as reference"), `Homayar/ReportAppvisionProgress.md:53-54`, ADR 0028/0031 remarks, and `.claude/skills/implement/SKILL.md:38` (it says prompts live in `backend/prompts/`). ADRs are not editable here; the rest is follow-up.
3. The product console still has pages that call the unmounted endpoints: `frontend/src/lib/hooks.ts` (`/dashboard/*`), `components/graph/GraphPage.tsx`, `components/copilot/CopilotPage.tsx`, `components/incidents/IncidentsPage.tsx` and `components/analytics/AnalyticsPage.tsx`. These calls already failed at base, and the live surface is unchanged. Frontend follow-up.
4. The dependency pruning of `ldap3` (optional extra) and `reportlab`, which now have 0 importers in `backend/`, is deferred to WP01-T04 (HOM-28), as the inventory records.
