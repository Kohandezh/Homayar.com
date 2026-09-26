# Workspace structure and source ownership

Status: workspace inventory and migration plan, 2026-09-07. This document describes the filesystem; it does not change Git ownership or make a release checkout reproducible.

> **Update 2026-09-26 ([ADR 0033](decisions/0033-product-source-in-this-repository.md)):** the product source is now tracked in the outer repository under `Homayar/` as ordinary files (imported from `Kohandezh/HomaYar` `e7eeef53`); the "two independent Git repositories" description below is historical. A workspace may still hold the former clone's `Homayar/.git`; Git commands run inside `Homayar/` then address that clone, and updating the outer checkout overwrites the previously ignored files there (see the ADR's consequences).

## Current workspace

The workspace contains two independent Git repositories:

- The outer repository at the workspace root owns the Next.js commercial website. Its application source is `src/`; editorial and product content is `content/`; static assets are `public/`.
- `Homayar/` is an embedded product repository with its own Git history and dirty working tree. Its API, workers, services, and tests are under `Homayar/backend/`. Its React/Vite product console is under `Homayar/frontend/`.
- Root `infra/` and `.github/` contain locally authored deployment and CI work that expects both applications in the same filesystem checkout.

At inspection, outer `git ls-files Homayar` returned no tracked files and outer status classified `Homayar/` as untracked. No configured submodule contract was established. Merely nesting the directory does not deliver its source to GitHub Actions, a clean clone, or a release builder. Both repositories also contain uncommitted work that must be preserved and reviewed independently.

```text
workspace/                         # outer website repository
  .git/
  src/                             # Next.js website; preserve path
  content/                         # website content; preserve path
  public/                          # website assets; preserve path
  scripts/                         # current website/build scripts
  drizzle/                         # website database migrations
  tests/                           # website tests
  infra/                           # shared deployment and operations
  .github/                         # current CI definition
  Homayar/                         # separate embedded product repository
    .git/
    pyproject.toml
    uv.lock
    alembic.ini
    backend/                       # API, workers, migrations, tests
    frontend/                      # product console
  docs/
    architecture/
    operations/
    market/
```

The nesting is a current arrangement, not a declaration that the product has been imported into the outer repository. See [ADR 0001](decisions/0001-preserve-runtime-roots.md).

## Documentation and support-file organization

Organize plans and handoff materials without moving runtime source. These are intended responsibilities; a directory's presence does not mean its contents or tooling are complete.

| Path | Responsibility |
| --- | --- |
| `docs/planning/` | Commercial blueprint, roadmap, feature inventory, prioritization, and release gates |
| `docs/planning/work-packages/` | Individually implementable work packages with dependencies and acceptance criteria |
| `docs/architecture/` | Current/target architecture, data and service boundaries |
| `docs/architecture/decisions/` | Numbered decisions recording context, alternatives, and consequences |
| `docs/operations/` | Installation, deployment, CI, backup, restore, upgrades, and incident procedures |
| `docs/market/` | Dated primary-source competitor, pricing, and customer research |
| `docs/handoff/` | Coding-agent entry points, current state, execution rules, and verification record |
| `docs/security/` | Redacted findings, threat model, remediation tracking, and security acceptance criteria |
| `tooling/` | Workspace maintenance and handoff utilities; separate from application runtime |
| `artifacts/releases/` | Generated release archives and manifests, with a deliberate version-control policy |
| `artifacts/graphify/` | Generated code-graph outputs; never a substitute for the production knowledge graph |
| `artifacts/setup/` | Dated workspace setup inventories and verification results |

Keep existing architecture, operations, and market documents at their current paths. Update indexes and cross-links instead of creating competing copies. Do not place secrets, production databases, customer recordings, or personal data in documentation or generated artifacts.

Loose release archives or duplicate assets may move only after their consumers and references are checked. In particular, `public/` assets can be referenced by URL and should not be moved merely because a filename looks untidy. Current generated build destinations such as `out/` remain build contracts until the scripts are deliberately changed.

## Path coupling that prevents a cosmetic source move

| Consumer | Current dependency |
| --- | --- |
| `infra/docker/api.Dockerfile` | Copies `Homayar/pyproject.toml`, `Homayar/uv.lock`, `Homayar/alembic.ini`, and `Homayar/backend`; launches `backend.main:app` |
| `infra/docker/api.Dockerfile.dockerignore` | Explicitly includes the product backend paths and excludes environment files and caches |
| `infra/docker/console-web.Dockerfile` | Copies `Homayar/frontend/`; publishes its Vite `dist/` under the `/app/` console route |
| `infra/docker/console-web.Dockerfile.dockerignore` | Uses an explicit allowlist rooted in `Homayar/frontend/` |
| `infra/docker/public-web.Dockerfile` | Copies root `src/`, `content/`, and other website build inputs |
| `.github/workflows/ci.yml` | Product jobs run in `Homayar/frontend` and `Homayar`; verify product manifests exist; use nested lockfiles for cache keys |
| `.github/workflows/ci.yml` | Backend migration check invokes `../infra/ci/check-migrations.sh` from `Homayar/` |
| `Homayar/pyproject.toml` | Configures tests at `backend/tests`, Python path at the product root, and migration-specific lint rules |
| `Homayar/Makefile` | Refers to `backend`, `frontend`, the product Compose file, and Python module names |
| `tsconfig.json` | Maps `@/*` to root `src/*` and explicitly excludes `Homayar/**` |
| `drizzle.config.ts` | References `src/db/schema.ts` and writes root `drizzle/` |
| `scripts/build-cpanel.mjs` | Temporarily parks `src/app/api` and writes `out/` |
| `infra/compose/`, `infra/scripts/`, `infra/nginx/` | Depend on shared configuration locations, build context, relative script paths, and `/app/` plus `/api/v1/` routing |

A migration to an `apps/` layout would therefore require coordinated Docker, CI, package, module-import, migration, build-script, documentation, and test updates. That is an engineering change with deployment consequences, not folder housekeeping.

## Migration order

1. Record branch tips, both working-tree states, and current source ownership without exposing environment values. Preserve both histories and dirty files.
2. Create documentation indexes, work-package and handoff structure, and support-artifact locations. Check references before any selected non-runtime file move.
3. Resolve the release source contract explicitly: either a separately checked-out product repository pinned to a reviewed commit, a properly configured pinned submodule, or a reviewed import into a monorepo. Do not accidentally stage the embedded repository as an unexplained Gitlink.
4. Review and commit the existing website and product changes through their respective owners and repositories. A dirty working directory cannot be the only copy of a release candidate.
5. Validate a clean checkout in CI: website and product source must exist, lockfiles must resolve, backend migrations must run, tests must pass, and container builds and route smoke checks must succeed.
6. Consider moving runtime roots only after that baseline is reproducible and the maintenance benefit outweighs the migration cost. Use a separate change with a full consumer/path checklist and rollback plan.

## Unresolved release risk

The existing deployment and CI documentation already acknowledges the missing embedded-repository source contract. This structure plan preserves working software paths and makes work easier to navigate. It does **not** resolve clean-checkout source delivery, prove CI passes, make local changes production-ready, or select a coding model.
