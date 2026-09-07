# Homayar Commercial v1.0 — Engineering State Report

Prepared by: Claude Fable 5.1 (lead engineer session 1)
Date: 2026-09-07
Audience: Astra 6 and the product owner, as input for the final master prompt.
Language: English for technical content; the owner communicates in Persian.

---

## 1. One-paragraph summary

The project has two independent Git repositories: the marketing website (`Kohandezh/Homayar.com`, Next.js) and the product (`Kohandezh/HomaYar`, FastAPI + React/Vite), the latter living as an untracked nested clone at `Homayar/` inside the website checkout. No committed ref in either repository can build the product: the packaging, migrations and v1 API modules exist only as an uncommitted overlay on the product clone. Session 1 turned this hidden dependency into an explicit, tested, CI-enforced source contract (WP01-T02), produced a full provenance inventory (WP01-T01), and drafted the credential-containment record (WP02) and the corpus/annotation protocol (WP13). Nothing has been committed, pushed, deployed or rotated. The next executable step is WP01-T03: commit the product overlay on a feature branch of the product repo and move the pin.

---

## 2. Environment and ground rules that must survive into the next prompt

| Item | Value |
|---|---|
| Working directory | Git worktree `.claude/worktrees/homayar-commercial-v1-a266f5`, branch `claude/homayar-commercial-v1-a266f5` from `main` @ `8f4ba6a` |
| Main checkout | `/Users/emperor/Documents/AI/Homayar.com` (treated read-only; docs synced by copy only) |
| Product clone | `/Users/emperor/Documents/AI/Homayar.com/Homayar` on `dev` @ `605cb074`, 34 modified / 171 untracked files (read-only so far) |
| Legacy product `master` `3766bc90` | Not present in the local clone; do not merge wholesale |
| Sandbox | Bash writes only inside the worktree; Docker socket and `opencode` need sandbox bypass per command |
| Tooling | uv 0.11.24, node 26.4.0, pnpm 11.1.2, docker 29.5.3, jq, python3; gitleaks/actionlint/shellcheck via Docker only; bash 3.2 (no `${var,,}`, guard empty arrays) |
| opencode | v1.18.25; `zai-coding-plan/glm-5.2` works sequentially only (parallel runs hang); Gemini quota 0 |
| Graphify | 0.9.40; handoff maps in `docs/handoff/claude-fable-5.1/graphify/`, all 285 source hashes unchanged |

Constraints stated by the owner (verbatim intent, keep all of them):

- Never print credentials, never test exposed keys against providers, never assume history cleanup revokes a credential.
- Never delete nested `.git`, reset the worktree, blindly stash, or stage everything. Do not merge legacy `master` wholesale.
- Separate authorization required for: production deployment/public publication, customer messages, paid services, credential rotation, destructive data operations, Git history rewriting, pushing or merging remote changes.
- No P2/P3 expansion while P0/P1 foundations are unfinished.
- No production mock/random analytics, no fabricated speaker identities, no hidden cloud fallback for private data.
- Read the installed Next.js docs (`node_modules/next/dist/docs/`) before changing Next.js code (AGENTS.md).
- Cost control (highest priority): no subagents/workflows unless explicitly requested in the latest message; compact context; search before read; one focused tool call over many; warn before expensive work; stop when the outcome is reached.

---

## 3. What was delivered (all in the worktree, uncommitted)

Worktree status: ` M .gitignore`, `?? .github/`, `?? docs/`, `?? infra/` (122 entries; secret-pattern screen clean).

### 3.1 WP01-T01 Inventory — `validated`
- `docs/handoff/claude-fable-5.1/inventory/2026-09-07-source-state.md` + `.json`.
- Contents: repo A/B tables, overlay descriptions, runtime-data table, required-path provenance, discrepancies vs handoff, nine blocker claims verified with `file:line`.
- Extra findings: `.claude/settings.local.json` tracked in the product repo; `frontend/.env` present; caches only self-ignored; dependency drift (requirements.txt vs pyproject/uv.lock, two virtualenvs); `(site)` move statistics (13 identical, 18 modified).

### 3.2 WP01-T02 Clean-checkout source contract — `partial`
- `infra/product-source.lock`: repository, ref `dev`, sha `605cb074…`, 12 required paths, 15 forbidden paths.
- `infra/ci/check-source-contract.sh`: modes `local|ci`; exit 0 satisfied / 2 usage or setup error / 3 violated; checks git toplevel, origin, sha reachability, HEAD == pin, required paths committed at pin, forbidden paths untracked and ignored, dirty count.
- `infra/ci/tests/test-check-source-contract.sh`: 23 synthetic cases, `23 passed, 0 failed`.
- `.github/actions/product-source/action.yml`: parses the lock, checks out the pinned product commit into `Homayar/` with `persist-credentials: false`, runs the checker in `ci` mode.
- `.github/workflows/ci.yml`: new first job `source-contract`; `console-web`, `backend`, `container-config` now depend on it and use the action; secret `PRODUCT_SOURCE_TOKEN` (falls back to `github.token`).
- `docs/architecture/decisions/0002-product-source-contract.md` (ADR), `docs/operations/ci.md` updated, `.gitignore` gained `/Homayar/`, `.cpanel-build-api/`, generated Graphify artifacts.
- Validation: ShellCheck (warning level) and actionlint 1.7.7 clean. Against the real clone in `local` mode the checker exits 3 with 5 "uncommitted overlay" violations (`pyproject.toml`, `uv.lock`, `alembic.ini`, `backend/migrations/env.py`, `backend/migrations/versions`) and 3 warnings. This red result is correct and intended until WP01-T03.

### 3.3 WP02 Credential containment — T01 `blocked_external`, T02/T04 `partial`
- `docs/security/credential-containment-record.md` (GLM draft v0.1, QA'd, no credential-shaped strings, families CF-01..CF-07, 12 "OWNER ACTION REQUIRED" lines). Pointer added in `docs/security/REMAINING-P0.md`.
- Blocked on: credential owners must revoke/rotate; the engineer must not.

### 3.4 WP13 Corpus / annotation protocol — T01 `blocked_external`, T02 `partial`
- `docs/planning/work-packages/WP13/annotation-protocol.md` (GLM draft v0.1, 12 sections, thresholds copied from inputs, 12 open owner decisions).
- Blocked on: authorized pilot corpus rights and owner decisions.

### 3.5 Bookkeeping
- `docs/planning/backlog.json`, `work-packages/WP01|WP02|WP13/{tasks.json,README.md}` updated with statuses, `progress_note`, `updated`.
- `docs/handoff/claude-fable-5.1/PROGRESS.md` has the session-1 entry and the next-session template.
- These docs were copied to the root `docs/` as well; root tracked-file status unchanged.

---

## 4. Verified blockers in the product code (evidence, not opinion)

| # | Blocker | Evidence |
|---|---|---|
| 1 | Celery tasks routed to queue `processing` but every worker command omits `-Q` → jobs never execute | `backend/jobs/dispatcher.py:10`; `backend/celery_app/__init__.py:30-34`; Makefile/compose worker commands |
| 2 | Legacy STT alternates speakers every 30 s (fabricated diarization) | `backend/services/stt_service.py:40-41` |
| 3 | Analysis service returns random fallback values | `backend/services/analysis_service.py:27-66` |
| 4 | Diarization provider is "unavailable" only | provider registry |
| 5 | `ALLOW_MOCK_PROVIDERS` refused in production (good, but means no working provider path) | `backend/config.py:172-173` |
| 6 | Deletion: tombstone → audit → commit → best-effort object delete (no guaranteed purge) | `backend/routers/v1/meetings.py:506-551` |
| 7 | Retention `0` semantics mismatch between UI and service | `frontend/src/pages/SettingsPage.tsx:118` vs `backend/services/meeting_service.py:66,85` |
| 8 | CI declares PostgreSQL but tests hard-code SQLite | `backend/tests/conftest.py`; `.github/workflows/ci.yml` |
| 9 | Outer CI/Docker/Compose read product files from an untracked clone | `infra/docker/*.Dockerfile`, `infra/compose/*.yaml` (build context `../..`) |

---

## 5. Open decisions waiting on the owner

1. Permission to make a **local commit** of the current worktree snapshot (`docs/`, `infra/`, `.github/`, `.gitignore`) on `claude/homayar-commercial-v1-a266f5`. No push.
2. WP01-T03: permission to commit the product overlay in `Homayar/` on a feature branch from `dev` (after adding `*.db`, `graphify-out/`, `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`, `data/`, `.claude/settings.local.json` to its `.gitignore` and untracking `settings.local.json`), and the choice uv/pyproject vs requirements.txt.
3. If `Kohandezh/HomaYar` is private: create the read-only `PRODUCT_SOURCE_TOKEN` secret in the website repo (owner action).
4. Answers to the 12 open decisions in the WP13 protocol and the 12 owner actions in the WP02 record.
5. Whether the website overlay (`src/(site)` restructure, cPanel export) should be captured as a separate reviewed change now or deferred.

---

## 6. What is next, in order

1. Local commit of the capture (after decision 1).
2. **WP01-T03** — commit product overlay; run `uv run pytest backend/tests`; move `infra/product-source.lock` sha; re-run `infra/ci/check-source-contract.sh --mode ci` and expect exit 0.
3. **WP01-T05** — real PostgreSQL in tests (drop the SQLite hard-code in conftest).
4. **WP06** — worker queue fix (add `-Q processing` or remove the route) with a regression test.
5. **WP04-T01** — run/stage state contract.
6. Remaining P0 foundations per `docs/planning/backlog.json`; then P1. No P2/P3.
7. Pending manual adversarial review of the source-contract design (the automated critique phase was stopped for cost control).

Per-step loop (EXECUTION_PROTOCOL.md): read package + acceptance → inspect → query Graphify → bounded change → tests → checks → self-review → update PROGRESS.md and tasks.json → sync docs.

---

## 7. Status vocabulary in use

`not_started` · `in_progress` · `implemented_unverified` · `partial` · `blocked_external` · `validated` · `complete`

Current: WP01 `in_progress` (T01 `validated`, T02 `partial`); WP02 T01 `blocked_external`, T02/T04 `partial`; WP13 T01 `blocked_external`, T02 `partial`; all other packages `not_started`.

---

## 8. Suggested skeleton for the final master prompt

Include, in this order: (a) the two-repository model and the source contract as the single source of truth for "what builds"; (b) the constraints in §2 verbatim, cost control first; (c) the status vocabulary and the PROGRESS.md/tasks.json update duty; (d) the ordered next steps in §6 with the explicit stop points that need owner authorization; (e) the blocker table in §4 so no agent re-derives it; (f) the instruction that every claim of "done" must cite a command and its exit code.
