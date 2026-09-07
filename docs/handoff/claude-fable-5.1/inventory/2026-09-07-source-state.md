# WP01-T01 — Source and dirty-state inventory (2026-09-07)

Machine-readable companion: [2026-09-07-source-state.json](2026-09-07-source-state.json) (generated from `git` commands; no secret content was read or recorded). Baseline for comparison: `../CONTEXT_SNAPSHOT.json`.

Environment: Claude Code session in the Git worktree `.claude/worktrees/homayar-commercial-v1-a266f5` (branch `claude/homayar-commercial-v1-a266f5`, created from `main` @ `8f4ba6a`). The main checkout at the workspace root and the nested product clone were **read only**; no file there was modified, staged, stashed or reset.

## 1. Repository A — website / infrastructure (`Kohandezh/Homayar.com`)

| Item | Value |
|---|---|
| Path | `/Users/emperor/Documents/AI/Homayar.com` |
| Branch / HEAD | `main` @ `8f4ba6ad0c31fe9c2656b4aa45ce826dad21df28` = `origin/main` |
| Other refs | `claude/homayar-commercial-v1-a266f5` (this session's worktree branch, same commit); 2 `refs/codex/turn-diffs/...` checkpoint refs from an earlier Codex session |
| Tracked / modified / deleted / untracked | tracked at HEAD; **27 modified, 31 deleted, 177 untracked files** (`git status --untracked-files=all`) |
| Diff size | `58 files changed, 672 insertions(+), 5829 deletions(-)` |
| Comparison with handoff snapshot | identical status list; `.github/workflows/ci.yml` SHA-256 matches the snapshot's protected hash; all 285 Graphify manifest source files unchanged |

Uncommitted website overlay (purpose inferred from the diff, not yet reviewed line by line):

- Route restructure: the 31 deleted `src/app/<page>/{page,content}.tsx` files reappear as untracked `src/app/(site)/...`; `src/app/language/`, `locale-persistence.tsx`, `blog-index.tsx`, `case-study-index.tsx`, `static-contact.tsx`, `exhibition-hero.tsx`, `public-pricing.ts` are new.
- Build/deploy: `scripts/build-cpanel.mjs`, `public/.htaccess`, `public/hero-poster-mobile.webp`, changed `next.config.ts`, `package.json` (adds `build:cpanel`), `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `playwright.config.ts`, `eslint.config.mjs`, `tsconfig.json`, `.gitignore`, `AGENTS.md`.
- Content: `content/i18n/products.fa.json`, `src/data/products.json`, `src/lib/content.ts`, `src/lib/locale.tsx`, `src/lib/cart.tsx`, cart/catalog/header/product-card/site-chrome/landing/scroll-video-hero components, `src/app/api/checkout/route.ts`.
- Tests: `tests/` (Playwright) is untracked.
- Move detection for the `(site)` restructure (HEAD blob hash vs new file): 13 files byte-identical, 18 moved with edits (largest: `pricing/content.tsx` 391→181 lines, `blog/page.tsx` 148→16, `case-studies/page.tsx` 130→16). Stage deletions and additions together so Git records renames.
- `AGENTS.md` gains a four-line "Imported Claude Cowork project instructions" section containing a one-sentence project description; review the wording before committing.
- Credential-shape scan over modified and untracked website text files: only CI/Compose placeholders (`ci-only-...` fixture values pointing at localhost, `${VAR:?}` substitutions, `PGPASSWORD="$POSTGRES_PASSWORD"` exports). No literal secrets in `OPENCODE.md`, `docs/`, `src/`, `tests/`, `scripts/`. `homayar-cpanel.zip` contains no `.env`/key-named entries; the handoff ZIP contains only `docs/` plus a `START_HERE.txt`.
- Infrastructure and CI (untracked): `.github/workflows/ci.yml`, `infra/` (Compose profiles, Dockerfiles, nginx, backup/restore scripts, `ci/check-migrations.sh`).
- Documentation (untracked): `docs/` (handoff, planning, work packages, architecture, operations, security, market).

Untracked items that must **not** enter Git:

| Path | Size | Classification |
|---|---:|---|
| `homayar-cpanel.zip` | 25.8 MB | deployment bundle; artifact inventory item for WP02-T02 |
| `homayar-claude-fable-5.1-handoff.zip` + `.sha256` | 1.6 MB | transfer package of `docs/`; duplicate |
| `homayar-commercial-blueprint.md`, `homayar-claude-single-file.md` (root) | 230 KB, 258 KB | loose duplicates of `docs/planning/...` and `docs/handoff/.../CLAUDE_HANDOFF_SINGLE_FILE.md`; the `docs/` copies are newer (10:52 vs 10:33) and authoritative |
| `docs/handoff/claude-fable-5.1/graphify/**` (`graph*.json/graphml/html`, `extraction.json`, `analysis.json`, `cache/`, `vendor/*.min.js`) | ≈24 MB | generated graph artifacts; rebuildable with `tools/refresh_graph.py`; now ignored in this branch |
| `OPENCODE.md` | 13 KB | personal tooling notes; owner decision whether to track |

Ignored-but-present runtime paths (names only): `.next/`, `out/`, `node_modules/`, `.pnpm-store/`, `playwright-report/`, `test-results/`, `graphify-out/`, `.codex/`, `.gstack/`, `.claude/`, `tsconfig.tsbuildinfo`, `next-env.d.ts`. No `.env` file exists at the website root (`.env.example` only).

## 2. Repository B — product (`Kohandezh/HomaYar`)

| Item | Value |
|---|---|
| Path | `/Users/emperor/Documents/AI/Homayar.com/Homayar` (independent clone; **not** a submodule, no `.gitmodules`, not tracked by A) |
| Branch / HEAD | `dev` @ `605cb074d1dbbb83e00ff259529bac06a229f3b7` = `origin/dev` |
| Other refs | `main` @ `ecff6657` = `origin/main` (ancestor of `dev`, one commit behind) |
| Legacy `master` `3766bc90` | **not present** in this clone (no local or remote-tracking ref); the blueprint inspected it through a separate mirror. Any WP02 history work must fetch it deliberately. |
| Tracked / modified / untracked | 109 tracked files; **34 modified, 171 untracked files** |
| Diff size | `34 files changed, 4013 insertions(+), 1076 deletions(-)` |
| Comparison with handoff snapshot | identical status list; HEAD unchanged |

Uncommitted product overlay ("W" in the audit):

- Backend v1 platform (all untracked): `backend/routers/v1/` (admin, auth, billing, insights, meetings, members, shares), `backend/providers/` (base, factory, mocks, openai_compatible, whisper, diarization, asanak, schemas), `backend/jobs/` (dispatcher, processor, tasks), `backend/security/` (context, tokens, idempotency, normalization), `backend/storage/` (base, factory, local, s3), `backend/schemas/`, `backend/media/validation.py`, `backend/database/{relational,saas_models}.py`, 13 new `backend/services/*_service.py`, `backend/migrations/` with 3 Alembic revisions, 11 new `backend/tests/test_*.py`.
- Backend modified: `backend/config.py` (+229 lines settings), `backend/main.py` (v1 composition), `backend/celery_app/__init__.py` (adds `task_routes`), `backend/database/__init__.py`, `backend/tests/{conftest,test_api}.py`, `.env.example`.
- Packaging (untracked): `pyproject.toml` (`homayar-platform` 0.5.0, uv-managed), `uv.lock`, `alembic.ini`, `.python-version`.
- Console: untracked `frontend/src/components/platform/*` (meetings, members, overview, search, settings, shared meeting, usage, invitation), auth pages, `platform-api.ts`, vitest config and tests; modified `App.tsx`, layout, `api.ts`, `auth.ts`, `i18n.tsx`, `index.css`, `package.json`/`package-lock.json`, `vite.config.ts`.
- Tooling: modified `.claude/settings.local.json` (permission configuration; not inspected for values).
- Dependency-management drift: `pyproject.toml` + `uv.lock` + `.python-version` are untracked while `backend/requirements.txt` stays tracked, and the tracked `Makefile`, `docker-compose.yml` and `README.md` still reference `backend/.venv` and contain no `uv`/Alembic references. WP01-T03/T04 must declare `uv` + `pyproject.toml` canonical and retire or regenerate `requirements.txt`.
- Alembic chain (overlay): `ada201e253df` (initial SaaS domain) → `e93c65725e9b` (job concurrency reservation) → `b54cb5d2a981` (action priority); `alembic.ini` points `script_location` at `backend/migrations`.
- Independent credential-shape scan of all modified and untracked product source/test/config files (values never printed): 0 findings; `.env.example` files contain no long token-like values.

Runtime data present in the product tree:

| Path | Ignored by product `.gitignore`? | Note |
|---|---|---|
| `.env`, `backend/.env`, `frontend/.env` | yes (`.gitignore:19`) | environment files with non-empty settings (mode 600); never read here |
| `uploads/` (one `.m4a` recording), `.venv/` (≈228 MB) and `backend/.venv/` (≈423 MB), `sample/` (≈131 MB reference project), `frontend/node_modules/`, `frontend/dist/` | yes | data, two virtualenvs, reference project, dependencies, build output |
| `homayar-dev.db` | **no** | 635 KB SQLite development database at the configured default `sqlite:///./homayar-dev.db`; untracked and unignored |
| `graphify-out/` | **no** | historical graph output (≈2.1 MB, 61 files); untracked and unignored |
| `.mypy_cache/`, `.pytest_cache/`, `backend/.pytest_cache/`, `.ruff_cache/` | only by each tool's self-generated `.gitignore` | not listed in the repository `.gitignore`; add explicit rules so ignoring does not depend on tool behaviour |
| `.claude/settings.local.json` | **tracked** and modified | per-user Claude Code permission allow-list committed to product history; recommend `git rm --cached` plus an ignore rule in WP01-T03 (values not inspected) |

## 3. Required-path provenance (what the outer CI/Docker build reads)

| Path in product checkout | State at `605cb074` |
|---|---|
| `pyproject.toml`, `uv.lock`, `alembic.ini` | uncommitted overlay |
| `backend/migrations/env.py`, `backend/migrations/versions/` | uncommitted overlay |
| `backend/routers/v1/`, `backend/providers/`, `backend/jobs/`, `backend/security/`, `backend/storage/`, `backend/schemas/`, `backend/database/saas_models.py`, `backend/database/relational.py` | uncommitted overlay |
| `backend/main.py` | committed (but the committed version composes the legacy app; the overlay version is the v1 composition root) |
| `frontend/package.json`, `package-lock.json`, `index.html`, `vite.config.ts`, `src/` | committed (modified locally; the console `test` script exists only in the overlay `package.json`) |

Consumers: `.github/workflows/ci.yml` (`console-web`, `backend`, `container-config`), `infra/docker/api.Dockerfile` (`COPY Homayar/pyproject.toml Homayar/uv.lock`, `Homayar/alembic.ini`, `Homayar/backend`), `infra/docker/console-web.Dockerfile` (`COPY Homayar/frontend/...`), `infra/compose/compose.onprem.yaml` (`context: ../..`), `infra/ci/check-migrations.sh`.

**Conclusion (F01/B04 verified):** no committed ref in either repository can build the product. The outer repository's CI, Dockerfiles and Compose templates depend on files that exist only as an uncommitted overlay in an untracked nested clone. `infra/ci/check-source-contract.sh --mode local` now reports exactly this (exit 3, five "uncommitted overlay" findings).

## 4. Discrepancies between the dated handoff and the current source

1. Blueprint Section 3 cites legacy `master` `3766bc90`; it is not fetchable from the local product clone. Treat WP02 history work as requiring an explicit mirror fetch.
2. `backend/celery_app/__init__.py` (overlay) already declares `task_routes` sending `backend.jobs.process_meeting` to queue `processing` (and `stt`/`analysis` for legacy tasks), and `backend/jobs/dispatcher.py:10` also passes `queue="processing"`. The blocker remains valid on the consumer side: every worker command (`Homayar/Makefile:23`, `infra/compose/compose.onprem.yaml:197`, `infra/docker/api.Dockerfile` has no worker CMD) starts `celery ... worker` without `-Q processing` or a `task_queues` declaration, so the default worker consumes only the `celery` queue. WP06 must fix the worker subscription and prove delivery with a real broker.
3. The website overlay is larger than "content edits": it deletes and recreates every marketing route under a `(site)` route group and adds a cPanel static-export path. It has not been reviewed or captured in this branch (WP01-T03 scope).
4. Everything else checked (status lists, HEAD ids, CI file hash, 285 graph source hashes) matches the snapshot.

## 5. Provenance preserved in this session

- Copied the untracked, secret-screened `docs/`, `infra/` and `.github/` trees plus the root's modified `.gitignore` from the main checkout into this worktree branch (source: main checkout working tree, 2026-09-07). Pattern screen for key-shaped strings over those trees: no matches; `VALIDATION.json` credential pattern counts: 0/0/0.
- Not copied: the website `src/`, `content/`, `tests/`, config overlay (T03), any ZIP, the loose root Markdown duplicates, `OPENCODE.md`, and anything from the product clone (its capture belongs to `Kohandezh/HomaYar`, T03).
- Added to `.gitignore`: `/Homayar/` (prevents an accidental gitlink), the generated Graphify artifacts, and `.cpanel-build-api/` (transient directory created by `scripts/build-cpanel.mjs`).
- Independent review (automated inventory agents, read-only) reproduced the counts above and added the cache-ignore, `frontend/.env`, tracked `settings.local.json`, dependency-drift and move-detection observations recorded in this document.

## 6. Recommended capture plan (for owner approval; nothing committed yet)

| Item | Recommendation |
|---|---|
| Website `docs/`, `infra/`, `.github/`, `.gitignore` | commit on a reviewed branch of `Homayar.com` (this worktree) as "WP01 baseline capture" |
| Website `src/`, `content/`, `tests/`, config overlay | separate reviewed change (WP01-T03); run `pnpm lint`, `pnpm build`, `pnpm build:cpanel`, `pnpm test:e2e` first |
| Product overlay | review and commit in `Kohandezh/HomaYar` on a feature branch from `dev` (WP01-T03); first add `*.db`, `graphify-out/`, `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`, `data/` and `.claude/settings.local.json` to its `.gitignore` and untrack `settings.local.json`; decide `uv`/`pyproject.toml` versus `requirements.txt`; then move `infra/product-source.lock` to the new commit |
| ZIPs, loose Markdown duplicates, `OPENCODE.md`, databases, environments | keep out of Git; ZIPs go on the WP02-T02 artifact inventory |

## 7. Handoff blocker claims verified against current source (read-only, 2026-09-07)

| Claim | Verdict | Evidence |
|---|---|---|
| Jobs are published to queue `processing` but no worker subscribes to it | partially verified (operational conclusion holds) | `backend/jobs/dispatcher.py:10` (`queue="processing"`), `backend/celery_app/__init__.py:30-34` (`task_routes` also routes there); every worker command omits `-Q` (`Homayar/Makefile:23`, `infra/compose/compose.onprem.yaml:197`) |
| Legacy Whisper path fabricates speaker roles by time window | verified | `backend/services/stt_service.py:40-41` (alternates speakers every 30 s), used by `backend/celery_app/tasks.py:24-27` |
| Legacy AI failure becomes random/default business values | verified | `backend/services/analysis_service.py:12,27-66` (`random.uniform(55,95)` scores, random issue counts) fires on any `None` from the provider, not only on a missing key |
| v1 diarization is an explicit "unavailable" provider; no real model | verified | `backend/providers/diarization.py:14-29`, `backend/providers/factory.py:60-67`, `backend/config.py:97` default `unavailable` |
| Production configuration rejects mock providers | verified (active refusal) | `backend/config.py:95,155-173` raises `ValueError` in production when `ALLOW_MOCK_PROVIDERS` is true; the provider factory refuses as a second gate |
| v1 deletion commits tombstones before object deletion and only logs storage failure | verified (location correction: logic lives in the router) | `backend/routers/v1/meetings.py:506-551` (tombstone → audit → commit → best-effort object delete) |
| Retention `0` shown as "no retention" but computed as immediate expiry | verified | `frontend/src/components/platform/SettingsPage.tsx:55,118`; `backend/services/meeting_service.py:66,85` |
| CI declares PostgreSQL but pytest runs on SQLite | verified | `.github/workflows/ci.yml:112-138,182`; `backend/tests/conftest.py` fixtures hard-code a SQLite `database_url` with `_env_file=None` |
| Committed product HEAD lacks packaging/migrations/v1 modules | verified | `git ls-tree -r 605cb074` contains none of `pyproject.toml`, `uv.lock`, `alembic.ini`, `backend/migrations`, `backend/routers/v1` |

These verifications feed WP04 (states/mock exclusion), WP06 (worker queues), WP07 (retention/deletion) and WP01-T05 (real PostgreSQL tests). No code in those areas was changed in this session.
