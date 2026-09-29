---
name: commit
description: Use when creating git commits in the Homayar repo. Runs the checks for the surface you changed (Next.js site, product console, FastAPI backend), writes a commit message in this repo's style, and suggests splitting a diff that spans more than one root cause.
---

# Commit

Commit only when the user asks. If you are on `main`, branch first (`scoped-pr` owns branch naming and PR boundaries).

## 1. Run the checks for what changed

These are the commands `.github/workflows/ci.yml` runs, so a local pass is a real signal.

Public site (repo root):

```bash
pnpm lint
pnpm build
pnpm test:e2e      # when a user flow changed
```

Product console (`Homayar/frontend`):

```bash
npm --prefix Homayar/frontend run lint
npm --prefix Homayar/frontend test
npm --prefix Homayar/frontend run build -- --base=/app/
```

Product backend (`Homayar/backend`, run from `Homayar/`):

```bash
uv run ruff check backend
uv run pytest backend/tests -q -rs
uv run ../infra/ci/check-migrations.sh backend   # when a migration changed
```

Anything under `Homayar/` moved or renamed: `infra/ci/check-source-contract.sh`. A new outbound call: `python infra/ci/airgap_egress_check.py`.

## 2. Stage deliberately

Never `git add -A` / `git add .`. Keep out `.env*` (except `.env.example`), `node_modules/`, `.next/`, `dist/`, `.venv/`, `playwright-report/`, `test-results/`, `graphify-out/`, uploads, and scratch files. Gitleaks runs in CI; a secret in history is a rotation, not a revert.

## 3. Read the staged diff

`git diff --staged`. One change or several? If it spans more than one root cause, stop and split with `scoped-pr`.

## 4. Write the message

This repo does **not** use conventional-commit prefixes. Match `git log --oneline -20`: an area, a colon, then a plain sentence that says what the change does and why it matters.

```
<Area>: <what changed, in plain words>

<body: the why, the root cause, evidence (test counts, CI run) when useful>
```

Areas seen in history: `Media validation`, `Media decode`, `Transport drill`, `CI`, `Website CSS`, `Gitleaks`, `Records`, `Handover`, `Research`, `Source contract`. Use the subsystem name (`Voicemail`, `Locations`, `Shares`, `Billing`, `Console auth`, `Site checkout`, …).

Rules:

- Imperative or descriptive present tense, subject under ~72 characters when possible.
- No em dashes; use a period, comma, or parentheses.
- **No `Co-Authored-By` trailer.** The owner does not want AI attribution on Homayar commits.
- Reference an issue in the body when one exists.

Example:

```
Media validation: refuse a decode that reports errors even when ffmpeg exits 0

ffmpeg returned success on a truncated AAC stream, so the upload was accepted
and transcription produced empty segments. The validator now fails on the first
decoder diagnostic. Covered by test_media_validation (ffmpeg marker).
```

## Areas that must move together

- **Endpoint change**: router, Pydantic schema, service, tests, and the console caller (`Homayar/frontend/src/lib/*api*.ts`) in the same PR.
- **Schema change**: a new Alembic revision in `Homayar/backend/migrations/versions/`. Never edit a revision that has run anywhere.
- **Site DB change**: `src/db/schema.ts` plus the generated `drizzle/` SQL (`pnpm db:generate`).
- **Dependency**: backend `pyproject.toml` + `uv.lock`; console `Homayar/frontend/package.json` + its lockfile; site root `package.json` + `pnpm-lock.yaml`.
- **User-facing text**: both `fa` and `en`.
- **Boundary decision**: an ADR in `docs/architecture/decisions/` (next number).

## Split on

Different root causes, different surfaces with unrelated changes, or a refactor mixed with a fix. A contract change that needs backend and console together is one commit.
