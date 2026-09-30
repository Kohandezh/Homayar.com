# HOMAYAR BACKEND — CONTINUATION MASTER PROMPT (session 2)

You are continuing mission 20260929-backend-mode-100 on the Homayar backend (repo Kohandezh/Homayar.com, product in
`Homayar/`, backend only). Linear: workspace Homayar, team HOM, project "Homayar Backend".

## 0. Read first (in this order, before any work)
1. `docs/handoff/session-reset/backend-mode-100-linear/20260929-backend-mode-100/BACKEND_MODE_100_FINAL_REPORT.md`
2. `.../20260929-backend-mode-100/ledger.md` (decisions, lessons, harness) and `PLAN.md` (160-item queue)
3. `Homayar/ReportAppvisionProgress.md` §I and `Homayar/vision/AppVision-Status-Notes.md`
4. `AGENTS.md`, `.claude/skills/foreman/SKILL.md`

## 0b. Environment: LOCAL Claude Code on the owner's machine (not Claude web)
- Clone/pull Kohandezh/Homayar.com. Session 1's cloud mission directory (`/root/foreman/...`) no longer exists; every
  record it held is in `docs/handoff/session-reset/backend-mode-100-linear/20260929-backend-mode-100/` (ledger, SPECs,
  oracles, reviews, `wip/` patches, `locks.tsv`, `suite.sh`, `services.sh`, `testenv-l<k>.sh`).
- Create a fresh local mission directory (e.g. `~/foreman/<new-mission-id>/`) and copy the harness scripts there; edit
  their paths. Test services: PostgreSQL 16/17 and Redis 7 (Docker or local install). The test role must be a
  SUPERUSER like CI's postgres image (`homayar_test`, see `.github/workflows/ci.yml`). ffmpeg on PATH.
- `cd Homayar && uv sync --frozen`; CI-equivalent env values come from `.github/workflows/ci.yml` (test-only secrets).
- Locally, real Foreman with Herdr panes (claudepersonal configuration) may be available: if `herdr` and HERDR_ENV
  exist, use the real Foreman mode; otherwise use the native subagent adaptation described in the ledger.
- Docker is available locally, so WP01-T04 (image builds) is no longer blocked by the environment.

## 1. Preconditions (stop and report if any fails)
- GitHub Actions can run jobs. Check the latest CI run: if jobs still end in 2-4 s with log 404, STOP and tell the owner
  (billing/spending limit). Without CI no issue can reach Done.
- Start from `main` after PR #13 was merged. Create a new integration branch from main; open ONE draft PR for it.
- Record a budget cap from the owner before any fan-out. Default: stop starting new issues at 80% of the cap; finish the
  active one, write the handover, stop.

## 2. Process (lessons from session 1: quality was high, cost per item was too high)
- ONE active issue at a time by default. At most TWO parallel lanes, only when file sets are disjoint (use locks.tsv,
  one PostgreSQL cluster per lane: `services.sh`, `testenv-l<k>.sh`, `LANE=k suite.sh`, per-team scratch dir).
- Tiered rigor:
  - FULL Foreman loop (blind oracle written before the patch, bare-diff team review, specialist final reviewer) ONLY for
    security, tenancy/RLS, migrations, billing/entitlements, concurrency, privacy/PII.
  - LEAN loop for everything else: maker + Foreman gates (focused tests, ruff, full suite in 4 chunks) + one
    independent sonnet reviewer on the bare diff. No blind oracle.
- Contract (SPEC) before code: verified file:line context, observable acceptance criteria, deny/allow pairs, file
  ownership. Pin interpretations in the SPEC before the maker starts (most rework in session 1 came from SPEC gaps).
- Review cap: two rounds, then escalate. Landed diff must equal reviewed diff (script). Run every PostgreSQL command
  under the lane lock. After a container restart: `services.sh`, then resume agents.
- Commits: author `kohandezh <kohandezh@hotmail.com>`, trailer `Co-Authored-By: Mohammad Kohandezh
  <kohandezh@hotmail.com>`. Never use the Claude name in commits, PRs or comments.
- Never: merge with red CI (unless the owner explicitly says so), weaken tests/RLS/authorization, print or commit
  secrets, touch frontend/website, deploy, call paid providers, send real messages.

## 3. Queue (in this order)
1. HOM-101 WP18-T03 versioned plans (stop startup/sign-up overwrite). Resume from `wip/wp18t03-t1/maker-wip.patch`
   (unreviewed; oracle `wip/wp18t03-t1/test_oracle_wp18t03.py`, base 98 red / 28 green). FULL loop; final reviewer
   database-optimizer. One migration after the current head.
2. HOM-164 PRIV-01 postal-code redaction leak (Arabic letters/ZWNJ). FULL loop (privacy); detector version bump.
3. HOM-169 VOICE-01 recover()/transition() race (overwrites ENDED calls; lock-order deadlock). FULL loop (concurrency).
4. HOM-120 WP22-T02 single normalization owner. Resume from `wip/wp22t02-t1/maker-wip.patch` (oracle base 22 red /
   374 green, exhaustive BMP sweep). FULL loop; final reviewer security-vulnerability-scanner.
5. HOM-159 S3 endpoint rule bypass via AWS_ENDPOINT_URL (security, small).
6. HOM-167 entitlement integrity gaps (after HOM-101).
7. HOM-168 retention gap: write the probe first; fix only if confirmed.
8. HOM-160 model_dump secrets, HOM-161 internal TLS (needs owner decision per deployment mode), HOM-165, HOM-166.
9. Continue PLAN.md queue from item 6 (WP01-T04 partial: Docker unavailable in cloud; WP01-T06; N03 consent; X01 ...).

## 4. Deliverables at the end
- Per issue: Linear comment with commit, evidence, confirmed state; Done only after merge + green CI.
- Update `ReportAppvisionProgress.md` §I, `vision/AppVision-Status-Notes.md`, AppVision statuses (evidence only).
- Usage report (cost_usd snapshots, tokens, per-issue subagent tokens) and the 27-section final report.
- Honest percentages: queue items done / 160; backlog subtasks locally validated / 161; CI-validated; production-ready.
