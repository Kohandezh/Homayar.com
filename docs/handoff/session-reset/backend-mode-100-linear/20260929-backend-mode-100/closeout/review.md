# Close-out review (closeout-rev), mission 20260929-backend-mode-100

Scope: `smallfix.diff` (a770e5b HOM-158, d3e48bf HOM-162, 38c88dd AppVision N03; base 20820e7) and `stab02.diff` (HOM-163, base 7112407).
Both diffs apply cleanly (`git apply --check`) to the repository head. The repository was not modified; scratch copies are in `closeout-review/scratch/`.
No test or drill was run (Foreman gates: P1/P2/stress, full suite). Measurements quoted from the STAB-02 SPEC are the SPEC's, not mine.

Verdict rule used: CHANGES_REQUESTED only for a defect the diff introduces, or a false claim the diff itself makes, at medium or above. Low and info findings are listed with a smallest fix and do not block.

---

## 1. smallfix.diff — ACCEPTED

### HOM-158 (README residue, `jwt_expiry_hours`)

Verified:
- No consumer of `jwt_expiry_hours` / `JWT_EXPIRY_HOURS` remains. Repo-wide case-insensitive grep (hidden dirs included; `node_modules`, `.venv`, `.git` excluded) leaves only: the diff's own hunks, `Homayar/AppVision.md:582` (a status sentence, see info below) and historical `docs/handoff/...` notes. Nothing in backend code or tests, `frontend/`, `.github/`, `docker-compose.yml`, or `Homayar/.env.example` (which never had it).
- Removal cannot break an existing deployment: `Settings` uses `extra="ignore"` (`backend/config.py:247`), so a leftover `JWT_EXPIRY_HOURS` in `.env` is dropped. The real token lifetime settings are `access_token_minutes` / `refresh_token_days` (`config.py:278-279` after the diff).
- The retirement pin is sound: `test_legacy_retirement.py` `RETIRED_SETTINGS` gains `jwt_expiry_hours`, and `test_retired_setting_is_not_a_field` asserts `name not in Settings.model_fields`.
- Statements the diff keeps in the README are true where they touch its edits: `/api/v1` pointer and `docs/security/authorization-matrix.md` §6 ("Route inventory") exist; `docs/architecture/legacy-retirement.md` exists and lists dashboard, graph, Copilot, VoIP webhook and LDAP as retired; the `.env` snippet still shows only live settings (`JWT_SECRET`, `NEO4J_*`, `LLM_PROVIDER`, `ZAI_API_KEY`, `GAPGPT_API_KEY` are all real fields).
- The removed README lines are the ones recorded as HOM-158 residue in `n11-t1/review-turn-2.md` and `handoff-turn-2.md` (Dashboard and PDF Export features, Cypher and webhook security bullets, the `/api/auth` and `/api/calls` table), plus `JWT_EXPIRY_HOURS`.

Findings:

1. low, `Homayar/README.md:119-127` (line numbers of the post-diff file), Project Structure tree still lists `middleware/auth.py`, `routers/auth.py`, `routers/calls.py`, `services/analysis_service.py` (none exist; `backend/routers/` holds only `v1/`) and "22 tests". This is a recorded HOM-158 item (handoff-turn-2: "the `routers/` tree that ends on `├── calls.py`") that a770e5b did not address, so HOM-158 is not fully closed by this commit. Smallest fix: replace the tree with the real layout (`routers/v1/`, `services/`, `jobs/`, ...) or a pointer to the architecture docs; otherwise keep HOM-158 open (or file the remainder) rather than marking it Done.
2. low, `Homayar/README.md:83`, "Default login: `admin@homayar.io` / `admin123`" is false. The bootstrap admin was retired with `ADMIN_DEFAULT_PASSWORD` (`docs/architecture/legacy-retirement.md:86,136`) and the string exists nowhere in code. Smallest fix: delete the line, or point to the real first-user path.
3. low, `Homayar/README.md:153`, "Rate limiting (60 req/min default)". No such default exists: limits are per scope per hour (`config.py:317` `api_key_rate_limit_per_hour=600`, `:333` `upload_rate_limit_per_hour=30`; `rate_limit_service`). Smallest fix: reword to "Per-scope rate limits (upload, API key, provider)" or drop the number.
4. info, `Homayar/README.md:47`, "Python 3.9+" contradicts `pyproject.toml:5` (`>=3.12,<3.14`) and `.python-version` (3.12). Smallest fix: ">=3.12".
5. info, `Homayar/AppVision.md:582`, the N11 status still lists "README residue and the unused `jwt_expiry_hours` setting (HOM-158)" as open. After this lands the second half is stale and the first is partial (findings 1-3). Smallest fix: Foreman updates the sentence when it records HOM-158's outcome (not a change to this diff).
6. info, `Homayar/backend/tests/test_legacy_retirement.py:41-47` and `Homayar/docs/architecture/legacy-retirement.md:120-136`. The field pin is enough, but `LEGACY_ENVIRONMENT` has no `JWT_EXPIRY_HOURS` entry (so the "an existing deployment still boots" property is unpinned for it) and the settings inventory the test module names does not list the removed setting. Smallest fix, optional: one `LEGACY_ENVIRONMENT` entry and one inventory row.

### HOM-162 (Makefile)

Verified by `make -n` on the post-fix Makefile (`git show 38c88dd:Homayar/Makefile`, run from `Homayar/`) and by import checks with `PYTHONDONTWRITEBYTECODE=1`:
- `seed` runs `.venv/bin/python -m backend.database.seed`; `backend` runs `.venv/bin/uvicorn backend.main:app ...`; `celery` and `worker` run `.venv/bin/celery -A backend.celery_app worker --queues processing ...`; `up` uses the same paths. `Homayar/.venv/bin/{python,uvicorn,celery}` exist; `backend.database.seed`, `backend.celery_app` and `backend.main` import; `celery.app.utils.find_app('backend.celery_app')` returns the `homayar` Celery app; `seed.py` has its `__main__` entry and its docstring says "Run from `Homayar/` with `python -m backend.database.seed`", matching the new target.
- Working directory change is safe: `.env` is resolved from `PROJECT_ROOT` (`config.py:23,244`), not the cwd, and `config.py` has no cwd-relative defaults. The old targets could not have worked (no `backend/.venv`; `backend.*` not importable from `backend/`), so there is no regression.
- `up`: in the shell line the backgrounded `uvicorn ... &` no longer needs `cd`, and the foreground tail `cd frontend && npx vite` is right (`Homayar/frontend` exists). The old `cd ../frontend` resolved to the repository root, which has no `frontend/`. Line continuations and `&` grouping are unchanged.
- `frontend`, `neo4j-*`, `redis`, `down`, `repo-*` are untouched and correct from `Homayar/`.

Findings:

7. info, `Homayar/Makefile` `status` target (unchanged, outside this diff), queries `http://localhost:8001/api/provider-status`, which is not mounted (`backend/main.py:195-196` mounts only `/healthz` and `/api/health`), so the "LLM Providers" line is always empty or "backend not running". No action for this unit; file separately if wanted.

### AppVision N03 (row/record dependency consistency)

Verified:
- Exactly two lines change (matrix row at `AppVision.md:303`, record `Dependencies` at `:412`); nothing else in the file.
- Row now lists `GR12; GR14; GR25; F15`, a subset of the record's dependency list, matching how other rows abbreviate their records (compare N02 row `:264` vs record `:392`).
- Consumers named in the record (N02, N06, N07) do list N03 as their own dependency (`:264`, `:317`, `:392`, `:472`, `:492`). N03 appears in no other dependency list (`grep N03`), and none of N03's prerequisites (GR12, GR14, GR25, F15, F17, F70, V01) lists N02, N06, N07 or N03 as a dependency (V01 row `:251`). The removed row-level V02-V04 entry was the only place where N03 both depended on and was consumed by the same items; no new cycle.
- The record's new "enforcement points" wording for V03 and C01-C06 agrees with the record's "Commercial and usage implications" line (V03, C01-C06 cannot be offered without an approved policy) and with acceptance criterion 1 ("every entry point ... outbound").

Findings:

8. info, `Homayar/AppVision.md:412`, the reclassification is partial. The record keeps "ingestion paths (uploads, voicemail and V01 imports)", "the processing claim (F17)" and "exports (F70)" under Dependencies although acceptance criterion 1 calls the same places entry points where N03 is enforced (the V03/C01-C06 logic). Not a cycle (none of them depends on N03 in the matrix). Smallest fix: leave, or move those three into the "enforcement points" clause for the same reason as V03.
9. info, `Homayar/AppVision.md:252-254`, the record says consumers include V02-V04, but the V02, V03 and V04 rows do not list N03 as a dependency (reciprocity gap; N02, N06 and N07 do). No cycle. Smallest fix, if wanted later: add N03 to those three rows.

---

## 2. stab02.diff (HOM-163) — ACCEPTED

The diff adds one `_wait(...)` (plus a 3-line comment) to `test_visibility_timeout_redelivery_during_a_live_attempt_is_inert` in `Homayar/backend/tests/test_transport_drills.py`, after the `COMPLETED` wait and before the Redis wait and both `stop()` calls. Only that file changes.

Checked against `stab02-t1/SPEC.md`:
- No assertion weakened or removed: the diff has no removed lines at all (`grep '^-'` on the diff prints nothing). The final `_completed_once(...)`, `_received(...) == 2` and `sorted(_statuses(...)) == sorted([copy_status, "completed"])` assertions, the `copy_status in INERT_COPY_STATUSES`, RUNNING and `(1, 1)` checks, and the Redis `{"queued": 0, "unacked": 0}` wait are byte-identical.
- No skip/xfail/retry/sleep: added lines contain none of `skip|xfail|flaky|rerun|deselect|sleep|retry` (AC4 grep, run on the diff text). No constant (`DEADLINE`, `VISIBILITY_SECONDS`, lease values) is touched.
- The wait uses a real signal: `_statuses` (`test_transport_drills.py:161-170`) extracts `'status'` only from worker log lines containing `backend.jobs.process_meeting[` and `] succeeded in`, i.e. the pool child's task-result line, which is written after `process_job` returns (`jobs/processor.py:1975-1983`, after the COMPLETED commit at `:1205`). `TransportProcess.lines()` (`_transport.py:241-243`) reads the live reader-thread buffer. `"completed" in list` is exact element membership, not a substring. The redelivered copy's status is asserted earlier to be in `INERT_COPY_STATUSES`, so `"completed"` can only come from the held attempt. Neither worker is assumed to be the holder (`_statuses(holder, other)`).
- Fails loudly: `_wait` default `timeout=DEADLINE` (60 s) raises `AssertionError("held attempt returned its result: not reached within 60.0s")` (`test_transport_drills.py:107-112`); this is the P2 message in the SPEC. It cannot pass silently.
- The comment's claims are accurate: `job.state = COMPLETED` is committed inside the task before `heartbeat.stop()`, audio deletion and the voicemail hook, and `stop()` does `os.killpg(self.pid, SIGTERM)` (`_transport.py:289-293`), so the pool child is killed with the parent. The comment is what the SPEC asked for and matches the file's existing comment style.
- Test-only: nothing under `Homayar/backend/` outside `tests/` changes; `_transport.py` and `_transport_worker.py` are untouched. `ruff format --check` passes on the patched file copy; `ruff check` passes on the in-repo base file and the patch adds no import (a scratch-location import-order hit appears identically on the unpatched copy, so it is an artifact of the scratch path, not of the diff).

Findings:

10. info, `Homayar/backend/tests/test_transport_drills.py:422-424` (post-diff numbering), the retained Redis wait `drill.redis() == {"queued": 0, "unacked": 0}` is already true when the gate opens (shared delivery tag; SPEC Context 7) and is no evidence about the holder. The SPEC allowed keeping it and suggested a one-line note; the diff adds none, so a later reader may still take it for a completion signal. Smallest fix, optional: one comment on that wait, or accept as is (the new wait carries the real signal).
11. info, same region, the new wait proves the pool child logged the task result, not that the parent has processed and acknowledged it; a sub-millisecond window between the log line and the result reaching the parent remains before `stop()` kills the group. It is bounded by the drill's polling cadence and cannot change any asserted value in practice; it is the SPEC's recorded out-of-scope item 1 (`TransportProcess.stop()` is not a warm stop for the pool child). Smallest fix: none for this unit.

---

CANDIDATES critical=0 high=0 medium=0 low=3 info=8
