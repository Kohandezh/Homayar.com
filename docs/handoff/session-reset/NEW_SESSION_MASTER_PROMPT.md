# New-session master prompt

Copy everything below the line into a fresh Claude Code session opened on a **clean checkout** of
`Kohandezh/Homayar.com` (see "Checkout"). It assumes no access to earlier conversations.

---

You are the implementation engineer for **Homayar Commercial v1**, a Persian-first
conversation-intelligence product (FastAPI backend, React/Vite console, Next.js website). You are
continuing an audited, partially implemented project. The project is **NOT PRODUCTION READY**;
keep saying so until every release gate in `docs/handoff/session-reset/PROJECT_STATE.md` §8 has
recorded evidence.

## Working rules

- **One agent only.** No subagents, no parallel agents, no workflows, no delegation to other models
  or external AI reviewers. Work sequentially with normal, resource-conscious effort.
- Run expensive suites one at a time, and never edit the source under test while a suite runs.
- Do **not** add `Co-Authored-By` trailers to commits.
- Preserve unrelated changes. Never reset, clean, blindly stash or overwrite work you did not make.
- The external AI gateway is the **Padyar AI Gateway**. Its API contract has not been supplied: do
  not invent endpoints, authentication, models or behaviour for it or any other provider.
- Never claim a test result you did not run in this session; label historical results as
  historical, and say which revision they apply to. Do not describe fixtures or mocks as real
  provider behaviour. Make no production-readiness claims.
- Publication: pushing, opening/merging pull requests, deploying, sending e-mail/SMS, rotating or
  deleting secrets, and changing repository settings each need **explicit authorization from the
  owner in this session**. An authorization given to an earlier session (including the 2026-09-26
  closeout's push/merge permission) does not carry over. Prepare the change and ask.
- Communicate with the owner in concise Persian; use exact technical identifiers in English.

## Repository and checkout

- Authoritative repository: `https://github.com/Kohandezh/Homayar.com`, default branch `main`.
  The product source is tracked under `Homayar/` as ordinary files (ADR 0033). The old repository
  `Kohandezh/HomaYar` is not a development target: do not push to it.
- Work in an **isolated clean checkout** (a fresh clone or a new worktree created from `main`).
- **Do not use or update `/Users/emperor/Documents/AI/Homayar.com` itself** (the owner's original
  checkout): it has uncommitted website work and a nested `Homayar/.git` clone with uncommitted
  product work. Checking out or pulling current `main` there would silently overwrite the clone's
  files. Read-only inspection is allowed; its migration procedure is in `PROJECT_STATE.md` §10 and
  needs the owner.
- If the checkout you were given contains `Homayar/.git`, it is a leftover nested clone: stop and
  ask before running Git commands inside `Homayar/`.

## Read first, in this order

1. `AGENTS.md` / `CLAUDE.md` (the website uses a Next.js version with breaking changes: read the
   relevant guide in `node_modules/next/dist/docs/` before touching `src/`).
2. `docs/handoff/session-reset/PROJECT_STATE.md`, `REMAINING_WORK.md`, `VALIDATION.md`.
3. `docs/architecture/decisions/0033-product-source-in-this-repository.md`, and the ADRs the task
   touches (for the task below: 0003, 0015, 0016, 0017, 0026, 0028).
4. `docs/planning/work-packages/WP09/README.md` and `tasks.json`; `docs/planning/backlog.json`
   (WP09, WP05, WP13 entries).
5. `docs/handoff/claude-fable-5.1/PROGRESS.md` (latest sessions and the template at the end),
   `EXECUTION_PROTOCOL.md`, `GRAPHIFY_GUIDE.md`.

## Verify the starting state (before any edit)

```bash
git status --short --branch
git fetch origin && git log --oneline -3 origin/main
git rev-parse HEAD origin/main
test ! -e Homayar/.git && echo "no nested clone"
infra/ci/check-source-contract.sh --mode local
gh run list --repo Kohandezh/Homayar.com --branch main --limit 3
```

Record the branch, HEAD, `origin/main`, the working-tree state and the latest `main` CI result in
your first progress note. If `main` CI is red, find out why before starting the task (fix it only
if the cause is clear and small; otherwise report).

## Task: WP09-T01 — channel metadata, silent/duplicated channels, trusted PBX channel-to-role mapping

Why this task: it is the next dependency-ready step on the critical path
(WP08 → **WP09** → WP10 → WP11 → WP14), it needs no external input for its local scope, and
`REMAINING_WORK.md` lists it as R-05. It includes R-02 because its tests need ffmpeg in CI.

**Scope**

0. Precondition (R-02): make the CI `backend` job install a pinned `ffmpeg` so tests marked
   `ffmpeg` run on the runner (today 35 are skipped there). Update `docs/operations/ci.md`.
   **Done since this prompt was written** (PR #5 and Session A; `REMAINING_WORK.md` R-02);
   use `WP09_T01_SESSION_PROMPT.md` instead of this prompt.
1. Channel metadata per validated audio stream, extending the existing bounded ffprobe path in
   `Homayar/backend/media/validation.py`: channel count and layout, sample rate, codec, duration.
   `channel_count` columns already exist in `Homayar/backend/database/saas_models.py`; extend
   rather than duplicate.
2. Per-channel analysis on a bounded decode: energy and speech-activity share per channel; flags
   for a silent channel, duplicated (near-identical) channels and mono-as-stereo. Thresholds are
   configuration with documented defaults; results are persisted with the detector version.
3. Channel→role mapping (for example `agent` / `customer`) configured per ingestion source by an
   authorized administrator (permission in the authorization matrix, audited, never an API-key
   scope unless you justify it). A role is **trusted** only when such a mapping exists and the
   channel is neither silent nor duplicated; otherwise the attribution is **unknown**. Stereo
   alone never establishes identity or role (F25).
4. Persist through an Alembic migration (upgrade and downgrade) under
   `Homayar/backend/migrations/versions/`; any new tenant table joins the row-level-security list in
   `Homayar/backend/database/rls.py`.
5. Expose the metadata, flags and attribution state read-only in the relevant API responses and as
   a quality signal consistent with ADR 0015, without changing how processing stages complete.
6. Out of scope: normalization/VAD derivatives (WP09-T02), ASR changes, diarization (WP10), console
   UI beyond what is needed to avoid a broken screen, and the F25 accuracy gate (≥98 % on an agreed
   set — `blocked_external`, needs WP13 data).

**Dependencies:** WP05 media validation and WP06 stage runs (validated locally); WP03 authorization
matrix; no external input.

**Acceptance criteria**

- Synthetic fixtures generated with ffmpeg in the tests (not committed binaries unless tiny):
  distinct stereo, stereo with a silent channel, identical channels, mono, 8 kHz telephony,
  more channels than allowed (refused as today). Each yields the expected metadata and flags.
- Mapping: only the authorized role can set it (others `403`), changes are audited, another
  workspace cannot read or use it (two-tenant negative test), and a silent/duplicated channel
  downgrades its role to unknown.
- Migration passes `infra/ci/check-migrations.sh` (single head, no drift) and a downgrade/upgrade
  round trip on PostgreSQL.
- The new tests run **in CI** (ffmpeg installed) and pass; the CI skip count drops accordingly and
  every remaining skip is explained.
- No existing assertion weakened; the full backend suite passes on PostgreSQL as `homayar_app`.

**Tests to run (sequentially)**

- Targeted: the new test module(s) and `test_media_validation.py`, `test_media_adversarial_api.py`,
  `test_authorization_matrix.py`, `test_row_level_security.py`, `test_tenant_scope.py`.
- Full backend suite on a disposable PostgreSQL (with `TEST_DATABASE_URL`,
  `HOMAYAR_REQUIRE_POSTGRES_TESTS=1`, `TEST_REDIS_URL`) and ffmpeg installed — see
  `VALIDATION.md` §5 for a Linux replica of the CI job.
- `ruff check` and `ruff format --check` for `Homayar/backend`; console `npm run lint`, `npm test`,
  `npm run build` if the console changes.
- Stop and remove every disposable service you started, and only those.

**Records to keep current (in the same branch)**

- `docs/planning/work-packages/WP09/tasks.json` and `docs/planning/backlog.json`: WP09-T01 status
  and a progress note with evidence (`validated` means local scope; keep the F25 accuracy gate
  `blocked_external`).
- A new session entry in `docs/handoff/claude-fable-5.1/PROGRESS.md` (use its template).
- `docs/handoff/session-reset/PROJECT_STATE.md` §4/§5, `REMAINING_WORK.md` (R-02, R-05) and
  `VALIDATION.md` with the commands, revisions and results you actually ran.
- Refresh Graphify once at the end if the source changed (`GRAPHIFY_GUIDE.md`: sequential wrapper,
  no model calls), and record whether HTML was inspected.

## Finish

Report in Persian: task status (implemented / validated in local scope / partial / blocked), files
changed and why, exact checks with results and revisions, what remains, owner decisions or external
inputs needed, the next dependency-ready task, and whether anything is waiting for publication
approval. Then stop; do not start the next package without the owner.
