# Session B handoff — website, console and customer journey

Session B owns the public website, the product console (`Homayar/frontend`), their tests and
this directory. It does not change backend code, migrations, Python dependencies, the provider
registry, the source lock, CI, Compose/nginx, global planning files or Graphify artifacts.

## Bases (verified 2026-09-24)

| Repository | Base | How verified |
| --- | --- | --- |
| Website `Kohandezh/Homayar.com` | `cfcc2c400e7edbc4ed7a165872cebfa4cdf73cf4` (`Merge pull request #1`) | `git fetch origin`; `origin/main` = HEAD |
| Product `Kohandezh/HomaYar` | `d1cc5d464c6ef51620dd445bd86726ef9280e473` (WP07-T07) | pinned by `infra/product-source.lock` at the website base; **not on the GitHub remote** (see G8), fetched read-only from `.claude/worktrees/homayar-commercial-v1-a266f5/Homayar` |

Observed but not used: `42543aa` (WP08-T01, backend only) on top of `d1cc5d4` in the Session A
clone, and remote product branches `dev` `605cb07`, `main` `ecff665`, `master` `3766bc9` (none
contains `d1cc5d4`). The original checkout `/Users/emperor/Documents/AI/Homayar.com/Homayar`
(`dev`, dirty) was not touched.

## Session B working copies

| Repository | Path | Branch |
| --- | --- | --- |
| Website | `.claude/worktrees/homayar-continuation-db5c83` | `claude/homayar-commercial-session-b-25d3d3` |
| Product | `.claude/worktrees/homayar-continuation-db5c83/Homayar` (independent clone, ignored by the website repo) | `session-b/frontend-journey` |

Nothing was pushed or merged. Commits are local.

## Records

- [`CONTRACT_NOTE.md`](CONTRACT_NOTE.md) — product API routes, fields, errors and states the
  website and console consume, verified at `d1cc5d4`.
- [`BACKEND_GAPS.md`](BACKEND_GAPS.md) — missing backend/deployment capabilities with proposed
  (unaccepted) contracts and acceptance tests.
- [`PROGRESS.md`](PROGRESS.md) — Session B commits, files, requirement mapping and verification
  evidence.
