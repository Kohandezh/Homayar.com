# Execution and resume protocol

This package prepares future implementation. Product code was not changed by the preparation task. The user will start implementation by giving the master prompt to Claude.

## Status meanings

| State | Required evidence |
|---|---|
| not_started | No implementation completed in the current work log; existing partial source may still exist |
| in_progress | Named subtask, bounded file ownership, intended tests and current diff |
| implemented_unverified | Code exists but the required checks have not passed |
| partial | Some feature gates pass; missing layers and tests are named |
| blocked_external | Specific missing credential-owner/customer/hardware/procurement input; independent work continues |
| validated | Relevant tests/evaluation executed on the current code and evidence recorded |
| complete | All task/feature acceptance criteria and required review/documentation are satisfied |

A work package is complete only when all its feature criteria pass. A full release additionally needs cross-feature scenarios, supported topology, security/operations and customer acceptance. Do not mark complete because a mock test, a screenshot, a diagram or a version label exists.

## Session start

1. Read `PROGRESS.md`, current package and applicable root/product instructions.
2. Inspect both Git states. Preserve user changes and record the baseline. Never run a blind clean/reset/stash.
3. Query the current graph with an explicit `--graph` path; compare source hashes before relying on it.
4. Read actual files in the selected change area and current framework documentation.
5. Write the next task ID, expected result, files/ownership and appropriate validation into the progress entry.

## Required task evidence

Each task entry records date, agent/environment, repository/ref, scope, changed paths, migration/schema impact, executed commands, exit/results, model/data versions if relevant, acceptance criteria satisfied, criteria still pending, external dependencies, review and next action. Never record secrets or customer conversation content.

No commit, PR or release identifier should be invented. If no commit was made, say so and preserve the diff. Branch changes must not discard local work. External communications, production deployment, destructive cleanup and credential changes need the relevant task authorization.

## Suggested test selection

- Text-only handoff edits: link/JSON/checklist consistency; no product suite needed.
- Backend behavior: narrow regression tests, relevant integration tests and type/lint checks.
- Tenant/usage concurrency: real PostgreSQL tests, not SQLite alone.
- Queue/recovery: real broker and worker with fault injection and idempotency assertions.
- Speech/QA: actual qualified models and frozen authorized evaluation corpus.
- Frontend: affected build/type checks and browser workflow, including Persian RTL and errors.
- Deployment: configuration/build checks, supported-profile smoke and install/upgrade/restore as required.

Read the current manifests rather than blindly pasting old commands. Run backend tests from `Homayar/` with its environment; marketing commands from root; console commands from `Homayar/frontend/`. Keep real data and `.env` out of disposable test snapshots.

## Dependency nuance

`backlog.json` lists completion dependencies. WP02 credential containment and WP13 corpus rights/protocol can start immediately. WP20 instrumentation, WP21 claim inventory and WP22 documentation can start incrementally even though their completed deliverables depend on later packages. Avoid a false circular block between baseline data collection and models evaluated on that data.

## Resume after context loss

Read the newest progress entry, then inspect its exact files/diff and evidence. Continue its stated next task. Do not rerun the whole research audit, reinstall the toolchain or regenerate all plans. If the current state differs from the log, preserve the newer state and reconcile before editing.
