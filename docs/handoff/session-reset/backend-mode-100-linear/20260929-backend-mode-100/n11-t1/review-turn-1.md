# n11-t1 review, phase C turn 1 (n11-t1-rev, team reviewer)

- Patch: /root/foreman/20260929-backend-mode-100/wt/n11-t1-check/REVIEW-INPUT.diff (bare `git diff -D`: 29 deletions, 3 modified files, 3 new files)
- SPEC: /root/foreman/20260929-backend-mode-100/n11-t1/SPEC.md, including amendments A1–A4
- Oracle: /root/foreman/20260929-backend-mode-100/n11-t1/oracle/test_oracle_n11.py (phase A, 199 cases)
- Throwaway copy used for execution: `git archive 692affb`, the 29 deleted files removed, and the 6 content hunks applied with `git apply` (all clean). The two package directories were removed as a checkout would leave them. My checkout's tracked files were not touched. I did not read the maker worktree or any handoff.

## Verdict

Mergeable as-is. Every criterion is met: AC1–AC8, A1–A3, and the edits A4 enumerates. I ran my oracle and the three related suites against the patched tree under the lock, and swept imports of every remaining backend module; no execution check failed. The deletions are exactly the SPEC closure plus `backend/prompts/`. `config.py` removes exactly the 15 fields and the one validator clause. No live module, route, config key or deployment file referenced anything that was deleted. Two low findings remain, neither blocking:

- AC9 is only partly met. The README still documents the retired webhook, but in lines A4's closed edit list forbade the maker to touch. This is a SPEC gap for Foreman to decide.
- The patch adds comments to two test files, against the no-comments convention.

The info items are pre-existing or doc nits.

## Findings

1. **low, Homayar/README.md:119 (pointer line added by the diff); residual content at patched README.md:112, 160–176, 40–47.** The README now says the webhook, LDAP, Copilot, graph and dashboard stack is retired. In the same file, a "## VOIP Webhook Integration" section still tells integrators to `curl -X POST …/api/webhook/voip/call-event` with an `X-Webhook-Secret` header. The Features list still advertises Dashboard, PDF Export, VOIP Integration, LDAP/AD Sync, AI Copilot and Graph Explorer, and line 112 still says "29 endpoints across 7 routers".
   - Why it matters: AC9 (A4) says the README "must not keep documenting what this change removes". The webhook section documents exactly `routers/webhook.py`, which this patch deletes. An operator following it will wire a PBX to an endpoint that returns 404. There is no security consequence, because nothing is served.
   - This is not a maker defect. A4 lists the only lines the maker may edit, and these lines are not on it. The maker stayed inside that list. The conflict is between A4's list and AC9's headline sentence.
   - Trigger: read patched `Homayar/README.md` lines 160–176, or run `grep -n "api/webhook" Homayar/README.md`, which hits lines 166 and 172.
   - Smallest acceptable fix: Foreman either extends A4 to cover lines 160–176, and ideally the four retired Features bullets at 44–47 plus the "29 endpoints across 7 routers" line, or records them explicitly in the follow-up that A4 already mentions. Both are acceptable. I am not requesting a maker change under the current SPEC.

2. **low, Homayar/backend/tests/test_legacy_retirement.py:40, 83, 125, 150 (plus the module docstring at 1–9) and Homayar/backend/tests/test_truthful_outputs.py:29–32.** The patch adds explanatory comments: four inline or line comments in the new test and a four-line comment block inside `REMOVED_MODULES`.
   - Why it matters: the repository convention for this review is no comments. The truthful-outputs block does copy an existing precedent at base lines 24–25 (`# Legacy authentication stack …`), which is why this is low. It has no behavioural effect.
   - Trigger: `grep -n "#" Homayar/backend/tests/test_legacy_retirement.py`.
   - Smallest acceptable fix: delete the comment lines. The inventory document already carries the rationale.

3. **info, Homayar/docs/architecture/legacy-retirement.md:190–191.** "They are superseded by `docs/architecture/decisions/0026…0031` (see that directory's `README.md`)" points at a README that does not exist: `docs/architecture/decisions/README.md` is absent. The supersession note the patch adds is `Homayar/docs/handoff/session-a/adr/README.md`.
   - Why it matters: it is a wrong cross-reference in the inventory that AC6 creates. A reader will look in the wrong directory.
   - Smallest acceptable fix: write the path explicitly, e.g. "(see `Homayar/docs/handoff/session-a/adr/README.md`)".

4. **info, pre-existing, out of maker ownership: Homayar/frontend/src/components/copilot/CopilotPage.tsx:43.** The console still ships a `CopilotPage` component that calls `api.post("/copilot/chat", …)`. Nothing in `Homayar/frontend/src` imports it.
   - Why it matters: the inventory row for `routers/copilot.py` (legacy-retirement.md:76) says "none — capability not offered", but the console still carries dead UI for it. It was already dead at base because the router was never mounted. Frontend files are forbidden to the maker, so this is a follow-up.
   - Smallest fix: a separate console ticket to delete the component, its i18n keys, and the `frontend/src/components/copilot/` tree line in the README.

5. **info, pre-existing, outside the SPEC list: Homayar/backend/config.py:53.** `jwt_expiry_hours: int = 24  # Legacy API compatibility only.` has no consumer anywhere in `backend/` other than its declaration: `grep -rnw jwt_expiry_hours backend --include=*.py` finds only config.py and my oracle. `Homayar/README.md` still documents `JWT_EXPIRY_HOURS=24`.
   - Why it matters: it is the same kind of dead legacy setting this ticket retires, but the SPEC's closed list of 15 names does not include it, so the maker correctly left it.
   - Smallest fix: add it to the follow-up (HOM-28 / WP01-T04, or a new ticket).

## Confirmed by the oracle (execution-backed)

Command, run in the throwaway patched copy under the lock:

```
cd <copy>/Homayar
source /root/foreman/20260929-backend-mode-100/testenv.sh
export PYTHONDONTWRITEBYTECODE=1
flock /root/foreman/20260929-backend-mode-100/heavy.lock <checker>/Homayar/.venv/bin/python -m pytest \
  backend/tests/test_oracle_n11.py backend/tests/test_truthful_outputs.py \
  backend/tests/test_legacy_retirement.py backend/tests/test_authorization_matrix.py -q -p no:cacheprovider
```

The copy was verified to resolve `backend` from itself. Result: **267 passed, 0 failed**. That is my oracle's 199, test_truthful_outputs 27, test_legacy_retirement 35 and test_authorization_matrix 6. This matches Foreman's red-to-green record.

All 129 cases that were red on base 692affb (phase A) are green on the patch:

- AC1 (36): `test_retired_module_has_no_import_spec`[18] and `test_importing_retired_module_fails_on_the_module_itself`[18]. The ModuleNotFoundError now names the retired module itself, not `backend.middleware` or `passlib` as on base.
- AC2 (18): `test_forbidden_legacy_guard_lists_retired_module`[18].
- AC3 (20): `test_dead_setting_is_not_a_settings_field`[15], `test_production_accepts_retired_setting_keywords`[4] (clause gone), `test_no_backend_code_reads_a_dead_setting`.
- AC4 (33): `test_legacy_environment_variable_is_ignored`[10], `test_legacy_environment_spelling_is_ignored`[18] (lower and mixed case, non-bool and non-int values, empty strings), `test_all_legacy_environment_variables_together_are_ignored`[2] (sentinels absent from dump and repr), `test_production_from_environment_alone_ignores_legacy_variables`, `test_legacy_keys_in_dotenv_file_are_ignored`[2].
- AC6 (21): the inventory cases. AC7 (1): `test_adr_draft_supersession_note`.

All 70 controls stayed green:

- The 13 live and sibling modules still import.
- The 12 live settings remain, all 156 non-dead base fields are kept, and no field was added.
- The production validator still rejects insecure cookies, a short JWT secret, mock providers, sqlite and auto-schema, even with legacy env present.
- The exact 127-pair base route set is served, and 121 pairs in production. Legacy paths return 404 and `/healthz` returns 200.
- The existing REMOVED_MODULES prefix and the random guard are unchanged.
- The 6 draft and 6 accepted ADR files are byte-identical to base (sha256).

Additional execution checks on the patched copy. These are not in my oracle, because A1–A4 postdate phase A:

- A1: `importlib.util.find_spec("backend.prompts")` is `None`; `"backend.prompts" in REMOVED_MODULES` is True (26 entries).
- A2: `git ls-tree 692affb Homayar/backend/{prompts,websocket}` equals the diff's deleted list for those packages exactly, so a checkout leaves no directory. See Not verified for the maker's own worktree.
- A3: all 29 deleted files appear individually as full repository-relative paths in backticks in the inventory, along with both package-directory entries. There are 31 `Homayar/…` rows, each ending in `| RETIRED |`. No brace-compressed lists; the only `{…}` are route parameters.
- A4/AC9: the enumerated README items are gone: LLM_MODEL_QC/PROBLEM/INSIGHT/TEACHER, the LDAP_* block, the VOIP_WEBHOOK_SECRET block, the five API rows, and the retired project-structure lines. Exactly one pointer line was added, linking to `docs/architecture/legacy-retirement.md`, and the relative link resolves. The residual content is finding 1.
- Reachability sweep: I imported every non-test, non-migration module under `backend/` in both trees. Base: 181 modules, and the only failures are the 5 legacy routers (`backend.middleware`) and `user_service` (`passlib`). Patched: 162 modules, 0 failures. No live module imported anything that was deleted.
- `ruff check` and `ruff format --check` on config.py, test_truthful_outputs.py and test_legacy_retirement.py: clean.
- Repo-wide grep, excluding docs and handoffs, for the retired module names and dead settings in `*.py`, `*.toml`, `*.yml`, `*.yaml`, `*.cfg`, `*.ini`, `*.sh`, Dockerfiles, `*.lock` and Makefile: no real reference remains. The only hits are the substring false positives `download_service` and `upload_service`. No live code serialises `Settings`, so dropping the 15 keys changes no output.

## Not verified (reading only, or taken from Foreman's record)

- Full suite (AC8): I did not re-run it. Foreman's gate records 0 failed / 1109 passed / 46 skipped, against 1055 / 46 at base. The +54 matches exactly 19 new `REMOVED_MODULES` parametrizations plus the 35 tests in `test_legacy_retirement.py`, so no test appears to have been lost or skipped.
- A2 in the maker's worktree (no leftover `__pycache__`-only directories): I cannot see untracked files from a bare diff. Foreman's record states there were no leftovers, and in any git checkout the directories disappear because every tracked file in them is deleted.
- Inventory prose: I checked that every referenced replacement file and symbol exists at base (`schemas/`, `security/tokens.py`, `jobs/processor.py`, `SYSTEM_PROMPT`/`PROMPT_VERSION`, `compare_digest` in the three named files, `LoginSession.revoked_at`, the `ldap` optional extra, `reportlab`, and `passlib` undeclared). I did not check whether each "canonical replacement" is a fair functional equivalent; those mappings are editorial. The sha256 claim for the route set is not re-hashed; route-set equality is execution-verified by the oracle.
- The behaviour changes are intended by the SPEC and exercised by the oracle. Production configurations that previously refused to start now start, specifically ENABLE_VOIP_WEBHOOK=true without a secret, and LDAP_PORT/LDAP_USE_SSL with values that were invalid for the old types. The unsafe `!=` secret comparison is removed with its file. No mounted route could reach it before or after.
- One root cause: yes. The change retires the unmounted legacy closure, which carried the unsafe comparison, along with its settings, guard entries and documentation.

CANDIDATES critical=0 high=0 medium=0 low=2 info=3
