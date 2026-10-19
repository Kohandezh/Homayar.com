# Workspace structure and source ownership

Status: workspace inventory and migration plan, 2026-09-07. This document describes the filesystem; it does not change Git ownership or make a release checkout reproducible.

> **Update 2026-09-26 ([ADR 0033](decisions/0033-product-source-in-this-repository.md)):** the product source is now tracked in the outer repository under `Homayar/` as ordinary files (imported from `Kohandezh/HomaYar` `e7eeef53`). Before that, the workspace held two independent Git repositories. A workspace may still hold the former clone's `Homayar/.git`; Git commands run inside `Homayar/` then address that clone, and updating the outer checkout overwrites the previously ignored files there (see the ADR's consequences).

## Current workspace

The repository root holds no application. It holds only shared parts and two application directories:

- `Homayar/` is the product. Its API, workers, services, and tests are under `Homayar/backend/`. Its React/Vite product console is under `Homayar/frontend/`.
- `website/` is the public website (homayar.com). It is a Next.js 16 app deployed to Cloudflare Workers with OpenNext. See `website/DEPLOY.md`.
- `infra/` holds deployment and operations files (Compose, Docker, nginx, egress, backups, CI checks).
- `.github/` holds the CI and release workflows.
- `.claude/` holds shared Claude Code skills and agents.
- `docs/` holds architecture, operations, security, and planning documents.
- `tools/` holds workspace tools, for example `tools/linear-agent/`.

The old Next.js website at the root (`src/`, `content/`, `public/`, `scripts/`, `drizzle/`, root `package.json`) was removed. Its `public-web` image and Compose service were removed too. A self-hosted install now serves only the console and the API.

```text
workspace/
  .github/                         # CI and release workflows
  .claude/                         # shared Claude Code skills and agents
  infra/                           # shared deployment and operations
  docs/
    architecture/
    operations/
    market/
  tools/                           # workspace tools
  Homayar/                         # product
    pyproject.toml
    uv.lock
    alembic.ini
    backend/                       # API, workers, migrations, tests
    frontend/                      # product console
  website/                         # public website (Next.js, Cloudflare Workers)
```

See [ADR 0001](decisions/0001-preserve-runtime-roots.md) for the original decision to keep runtime roots in place, and [ADR 0033](decisions/0033-product-source-in-this-repository.md) for the product source in this repository.

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

Loose release archives or duplicate assets may move only after their consumers and references are checked. In particular, `website/public/` assets can be referenced by URL and should not be moved only because a filename looks untidy.

## Path coupling that prevents a cosmetic source move

| Consumer | Current dependency |
| --- | --- |
| `infra/docker/api.Dockerfile` | Copies `Homayar/pyproject.toml`, `Homayar/uv.lock`, `Homayar/alembic.ini`, and `Homayar/backend`; launches `backend.main:app` |
| `infra/docker/api.Dockerfile.dockerignore` | Explicitly includes the product backend paths and excludes environment files and caches |
| `infra/docker/console-web.Dockerfile` | Copies `Homayar/frontend/`; publishes its Vite `dist/` under the `/app/` console route |
| `infra/docker/console-web.Dockerfile.dockerignore` | Uses an explicit allowlist rooted in `Homayar/frontend/` |
| `.github/workflows/ci.yml` | Product jobs run in `Homayar/frontend` and `Homayar`; verify product manifests exist; use nested lockfiles for cache keys |
| `.github/workflows/ci.yml` | Backend migration check invokes `../infra/ci/check-migrations.sh` from `Homayar/` |
| `.github/workflows/ci.yml`, `.github/workflows/release-website.yml` | The website job and the website release run in `website/` |
| `Homayar/pyproject.toml` | Configures tests at `backend/tests`, Python path at the product root, and migration-specific lint rules |
| `Homayar/Makefile` | Refers to `backend`, `frontend`, the product Compose file, and Python module names |
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
