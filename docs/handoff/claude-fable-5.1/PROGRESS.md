# Homayar implementation progress

## 2026-09-07 — Handoff prepared; product implementation not started

- Scope: documentation/folder organization, 24 package checklists, machine-readable backlog, code/planning graph artifacts and Claude prompts.
- Product source/configuration changes: none in this preparation phase.
- Commits, merges, pushes, deployment, credential rotation, paid actions: none.
- Existing backend test result: 97 passed during the prior isolated audit; not rerun or claimed as new implementation validation.
- Required scope: 95 catalogue features; 64 P0/P1 acceptance criteria; 24 packages; 161 implementation subtasks.
- Existing partial code: retained; see REMAINING_WORK.md and the dated source snapshot.
- Next implementation action: WP01-T01 inventory and WP01-T02 clean-checkout source contract; WP02 owner inventory and WP13 data-rights/annotation protocol can proceed independently.
- External prerequisites: credential owners, authorized pilot corpus, actual deployment/test resources and commercial/customer decisions as documented.
- Setup/graph validation: see PREPARATION_REPORT.md and graphify/BUILD_SUMMARY.json once generated.

## 2026-09-07 — Session 1: WP01-T01 validated, WP01-T02 partial; WP02/WP13 drafts in progress

- Date / environment: 2026-09-07, Claude Code (Fable 5.1) in Git worktree `.claude/worktrees/homayar-commercial-v1-a266f5`, branch `claude/homayar-commercial-v1-a266f5` from `main` @ `8f4ba6a`. Parallel drafting delegated to `opencode` with `zai-coding-plan/glm-5.2` (Gemini models returned quota 0 / empty output on this machine and were not used).
- Package / subtask: WP01-T01 (inventory) and WP01-T02 (clean-checkout source contract). WP02-T01/T02/T04 and WP13-T01/T02 drafts requested from GLM workers in isolated scratch directories (pending QA at the time of writing).
- Baseline repo/ref/dirty state: website `main` @ `8f4ba6a`, 27 M / 31 D / 177 untracked files; product `dev` @ `605cb074`, 34 M / 171 untracked files. Both match `CONTEXT_SNAPSHOT.json`. Main checkout and nested clone untouched (read only). Legacy `master` `3766bc90` is not present in the local product clone.
- Scope and files (all inside the worktree branch; nothing committed):
  - Copied from the main checkout (untracked there, secret-pattern screened, no matches): `docs/`, `infra/`, `.github/`, modified `.gitignore`. Excluded: website `src/`/`content/`/`tests/`/config overlay, ZIPs, loose root Markdown duplicates, `OPENCODE.md`, everything under `Homayar/`.
  - New: `docs/handoff/claude-fable-5.1/inventory/2026-09-07-source-state.{md,json}` (T01 deliverable); `infra/product-source.lock`; `infra/ci/check-source-contract.sh`; `infra/ci/tests/test-check-source-contract.sh`; `.github/actions/product-source/action.yml`; `docs/architecture/decisions/0002-product-source-contract.md`.
  - Modified: `.github/workflows/ci.yml` (new `source-contract` job; `console-web`, `backend`, `container-config` now `needs: source-contract` and check out the pinned product source; new scripts added to the ShellCheck step); `.gitignore` (`/Homayar/`, generated Graphify artifacts); `docs/operations/ci.md`; `docs/planning/backlog.json`, `docs/planning/work-packages/WP01/{tasks.json,README.md}` (status only).
- Change and reason: the outer CI/Dockerfiles/Compose read `Homayar/pyproject.toml`, `uv.lock`, `alembic.ini`, `backend/`, `frontend/` from an untracked independent clone, and the committed product HEAD does not even contain the packaging/migration/v1 files. The contract is now explicit (lock file), testable (checker + tests) and wired into CI, and it fails honestly until the product overlay is committed and the pin moved (WP01-T03).
- Commands/results newly executed:
  - `bash infra/ci/tests/test-check-source-contract.sh` → `23 passed, 0 failed` (bash 3.2.57, macOS; synthetic repositories, no network).
  - `infra/ci/check-source-contract.sh --mode local --product-dir /Users/emperor/Documents/AI/Homayar.com/Homayar` → exit 3: 5 violations (`pyproject.toml`, `uv.lock`, `alembic.ini`, `backend/migrations/env.py`, `backend/migrations/versions` exist only as uncommitted overlay), 3 warnings (`homayar-dev.db` and `graphify-out` unignored; 98 dirty entries).
  - ShellCheck 0.10.0 (`--severity=warning`, via Docker) on `check-source-contract.sh`, `tests/test-check-source-contract.sh`, `check-migrations.sh` → exit 0, no findings. actionlint 1.7.7 (via Docker) on `.github/workflows/ci.yml` → exit 0, no findings. Both YAML files parse.
  - Graph freshness: all 285 files in `graphify/source-manifest.json` have unchanged SHA-256; `.github/workflows/ci.yml` hash matches the snapshot.
  - Historical, not rerun: the 97 backend tests (no product behaviour changed this session). Not run: `pnpm`/`uv` suites, GitHub Actions (would require a push).
- Acceptance satisfied: none of F01/F02 fully. T01 acceptance (inventory with provenance, no secrets or runtime data imported) satisfied. T02: ownership recorded (ADR 0002), untracked-checkout dependence replaced by an explicit pinned-commit contract with a failing-by-design gate; "pinned release artifacts" remains the end state for T04–T06.
- Acceptance pending: F01 fresh-checkout build (blocked until the product overlay is committed in `Kohandezh/HomaYar` and the pin moved); F02 CI green run (needs a push and, for a private product repository, the `PRODUCT_SOURCE_TOKEN` secret); real PostgreSQL/queue/browser gates (WP01-T05).
- Review: an automated read-only review workflow completed its inventory-classification and blocker-verification phases (findings folded into `inventory/2026-09-07-source-state.md` §2, §4 and §7: tracked `.claude/settings.local.json`, `frontend/.env`, cache-ignore behaviour, dependency drift, `(site)` move statistics, nine blocker claims verified with file:line evidence). It was stopped before the adversarial contract-critique phase on the user's cost-control instruction; that critique is pending manual review. Self-review of the diff: checker hardened for bash 3.2, whitespace/CRLF locks and non-GitHub URLs; ShellCheck/actionlint clean.
- Parallel drafting: two `opencode` GLM workers ran in parallel in isolated directories and produced no output in 25 minutes (the failure mode documented in `OPENCODE.md` §2.2); both were stopped. A single sequential retry for the WP13 draft succeeded in about 5 minutes (2,426 words); after QA (thresholds cross-checked against the inputs, section order, no invented facts) it was placed at `docs/planning/work-packages/WP13/annotation-protocol.md` with a provenance header, and WP13-T01/T02 were set to blocked_external/partial. The WP02 containment-record draft was regenerated the same way (1,943 words), screened for credential-shaped strings (none), corrected in two places and placed at `docs/security/credential-containment-record.md`; WP02-T01 set to blocked_external, T02/T04 to partial. Gemini models were unusable (quota 0 / empty output). No further subagents or workflows will be started without an explicit request.
- External blockers: owner approval to commit the baseline capture on this branch; owner decision on committing the product overlay (WP01-T03) and on a read-only token for the product repository in CI; credential owners (WP02); authorized corpus rights (WP13).
- Next exact action: (1) owners review the WP02 and WP13 drafts and answer their open decisions; (2) with owner approval, commit the capture on this branch; (3) WP01-T03: review the product overlay in `Homayar/` (after adding `homayar-dev.db`, `graphify-out/`, `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/` to its `.gitignore`), commit it on a feature branch from `dev`, run `uv run pytest backend/tests`, then move `infra/product-source.lock` to that commit and re-run the checker in `ci` mode; (4) then WP04-T01 state contract.

## Template for next session

- Date / environment:
- Package / subtask:
- Baseline repo/ref/dirty state:
- Scope and files:
- Change and reason:
- Commands/results newly executed:
- Acceptance satisfied:
- Acceptance pending:
- Review:
- External blockers:
- Next exact action:
