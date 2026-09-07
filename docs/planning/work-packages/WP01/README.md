# WP01 — Reproducible source and release baseline

Status: IN PROGRESS since 2026-09-07 (T01 validated, T02 partial; see [PROGRESS.md](../../../handoff/claude-fable-5.1/PROGRESS.md) and [ADR 0002](../../../architecture/decisions/0002-product-source-contract.md)). Existing partial code must be reviewed and reused where suitable.

Completion dependencies: none; external prerequisites still apply.

## Implementation instructions

Features F01, F02. Owner: engineering lead. Dependencies: none; coordinate credential containment before distributing artifacts. Start with both Git trees, existing CI, pyproject, migrations and infrastructure references.

1. Inventory tracked/dirty/untracked changes and preserve authorized source provenance without importing secrets or runtime data.
2. Agree product/website ownership and replace build dependence on an untracked nested checkout with pinned release artifacts.
3. Reconcile the v1 overlay into reviewable changes; keep legacy routes disabled and document compatibility boundaries.
4. Lock dependencies and generate reproducible API, worker and console builds from a clean checkout.
5. Add PostgreSQL migration, queue integration, frontend build and critical browser-flow jobs to existing CI.
6. Publish a release manifest with source SHA, image digests, schema/model versions and linked validation results.

Done when an independent clean checkout builds the same release without local-only files and both fresh and upgrade migrations pass. No broad repository move is required to begin.

## Feature-level acceptance

### F01 — Release/source provenance (P0)

Current audited state: W uncommitted. Target: Clean reproducible release.

- [ ] Fresh checkout from the selected release ref builds API, console and workers with no dirty/untracked dependency; release manifest identifies every source/image/model revision.

### F02 — CI, migrations and regression suites (P0)

Current audited state: S partial. Target: Clean-checkout release gates.

- [ ] Required unit, PostgreSQL integration, queue and browser flows pass; fresh database and upgrade from the previous supported schema preserve seeded tenant/usage/evidence records.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
