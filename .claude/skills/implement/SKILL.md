---
name: implement
description: Full Research → Plan → Implement → Verify workflow for features, refactors, or bugfixes in Homayar. Use when asked to implement, build, refactor, or make a non-trivial change that benefits from structured phases.
disable-model-invocation: true
allowed-tools: Bash(git *) Bash(pnpm *) Bash(npm *) Bash(npx *) Bash(uv *) Bash(python3 *) Bash(cat *) Bash(ls *) Bash(find *) Bash(grep *) Bash(sed *) Bash(curl *) Read Edit Write Grep Glob Agent
argument-hint: "[description of what to implement or refactor]"
---

# Implement: $ARGUMENTS

A four-phase workflow for: **$ARGUMENTS**. Finish each phase and print its summary before the next.

## Current Context

- **Branch:** !`git branch --show-current`
- **Changed files:** !`git diff --name-only`
- **Recent commits:** !`git log --oneline -5`

## The three surfaces

| Surface | Path | Stack | Gates (same as `.github/workflows/ci.yml`) |
| --- | --- | --- | --- |
| Public website | repo root (`src/`, `drizzle/`) | Next.js 16 App Router, Tailwind v4, Drizzle + Neon, Stripe test mode, Playwright | `pnpm lint`, `pnpm build`, `pnpm build:cpanel`, `pnpm test:e2e` |
| Product console | `Homayar/frontend` | Vite + React 19, TanStack Query, react-router, shadcn/base-ui, Vitest | `npm run lint`, `npm run build -- --base=/app/`, `npm test` |
| Product backend | `Homayar/backend` | FastAPI, SQLAlchemy 2 + Alembic, PostgreSQL (RLS), Celery/Redis, Neo4j projection, pytest | `uv run ../infra/ci/check-migrations.sh backend`, `uv run pytest backend/tests -q -rs`, `uv run ruff check`, `uv run mypy backend` |

Next.js 16 is not the version in training data. Read `node_modules/next/dist/docs/` before writing site code (`AGENTS.md`).

## Phase 1: Research

1. **Read the rules and the contract.** `AGENTS.md`, `Homayar/AppVision.md` (canonical product contract), `docs/architecture/decisions/` (ADRs 0001+), and `docs/security/`. Anything that touches a boundary has an ADR; read it.
2. **Search wide, then narrow.** Grep for existing services (`backend/services/`), routers (`backend/routers/v1/`), schemas, and console helpers (`frontend/src/lib/`). Reuse before create.
3. **Name the surface(s)** this touches and why.
4. **Tenancy and authorization.** Which roles may call it (`backend/security/authorization.py`, ADR 0004)? Is the data tenant-scoped under RLS (ADR 0005)?
5. **Deployment modes.** Cloud and on-prem/air-gapped both exist. Any new outbound call must pass the egress policy (`backend/providers/egress.py`, `infra/ci/airgap_egress_check.py`).
6. **Dependencies.** Backend deps in `Homayar/pyproject.toml` + `uv.lock`; console in `Homayar/frontend/package.json`; site in the root `package.json`. Do not add one the stack already covers.
7. **Tests.** Find neighbours in `Homayar/backend/tests/`, `Homayar/frontend/src/**/*.test.tsx`, root `tests/` (Playwright).
8. **i18n.** Persian first: the console has `src/lib/i18n.tsx`; prompts exist in `fa` and `en` (`backend/prompts/`).

```
### RESEARCH SUMMARY
- Surfaces touched:
- Files found / patterns to follow:
- ADRs that apply:
- Auth, tenancy, egress implications:
- Existing tests:
- Files that will change (with reasons):
```

## Phase 2: Plan

1. Every file to create or modify, one line each.
2. Order: migration → model → service → router/schema → console → site. Backend contract before UI.
3. **Migrations are append-only.** New Alembic revision in `backend/migrations/versions/`; never edit one that has run anywhere.
4. User-facing states: loading, success, empty, error, 401, 403, partial/degraded (ADR 0015).
5. Risks: RLS, idempotency (`backend/security/idempotency.py`), job leases/outbox, usage ledger (ADR 0010), privacy/redaction (ADRs 0023–0028), RTL.
6. Docs: a new or changed boundary needs an ADR under `docs/architecture/decisions/`.
7. Tests to add, at which layer, including the refusal paths.

```
### IMPLEMENTATION PLAN
| Step | File | Action | Description |
**Risks:** / **Tests:** / **Order rationale:**
```

**Stop here and wait for the user's go-ahead before Phase 3.**

## Phase 3: Implement

- Follow the plan order; match surrounding code.
- Backend: Pydantic request/response models, the standard error envelope, service layer holds logic (routers stay thin), ruff line length 100.
- Console: server state through TanStack Query, requests through `src/lib/api.ts` / `platform-api.ts`, errors through `api-errors.ts`, strings in both languages, logical CSS (`ms-*`, `pe-*`, `text-start`) for RTL.
- Site: RSC by default, `"use client"` only where needed; must still work with no env vars.
- Pause and tell the user before deviating from the plan. Suggest commits; only commit when asked (`commit` skill).

## Phase 4: Verify

Run the gates for every surface touched (table above). For backend, also run against PostgreSQL when the change touches RLS, constraints, or locking:

```bash
TEST_DATABASE_URL=postgresql+psycopg://... HOMAYAR_REQUIRE_POSTGRES_TESTS=1 uv run pytest backend/tests -q -rs
```

A visible change: run the app and walk the flow, including `fa`/RTL and phone width. Reading the source is not verification. Report failures honestly.

```
## IMPLEMENTATION COMPLETE
Task / Research / Plan / Files changed / Gates (per surface) / Manual check / ADR or docs updated / Left out and why
```
