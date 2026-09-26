# Session deletion checklist (inventory 2026-09-26)

What could be lost when the old Claude sessions (A, B, C) and their worktrees are removed, checked
read-only on 2026-09-26 from Session C. "Remote" means reachable from a ref on GitHub
(`Kohandezh/Homayar.com` or `Kohandezh/HomaYar`). Nothing listed here was deleted; deleting sessions
and worktrees is the owner's action.

**Verdict:** every commit of Sessions A, B and C is reachable from a remote ref, and the handover is
published in `Kohandezh/Homayar.com` (`main`). The three session worktrees and their scratchpads hold
nothing that is needed and not published. **Exception — do not delete or overwrite:** the original
workspace checkout `/Users/emperor/Documents/AI/Homayar.com` (not a session worktree) with its dirty
website files (including the unpublished website overlay: cPanel export and Playwright suite) and its
dirty nested product clone; and the local backups under
`/Users/emperor/Documents/AI/Homayar.com/.git/session-c-backups/`. Removing a session worktree does
not touch the root checkout, but the root's uncommitted work exists nowhere else.

## 1. Git state (commits and branches)

Checked with `git rev-list --count <ref> --not --remotes` (website) and
`git merge-base --is-ancestor <tip> f29d04a` (product; `f29d04a` is on `Kohandezh/HomaYar`
`feat/commercial-v1-overlay` and merged into `dev`).

| Location | Branch / head | Local-only commits | Remote? | Disposable? |
| --- | --- | --- | --- | --- |
| Website repo, all local branches (`main`, `integration/commercial-v1`, `claude/homayar-commercial-v1-a266f5`, `claude/homayar-commercial-session-b-25d3d3`, `claude/homayar-v1-integration-d5beaf`, `claude/homayar-commercial-v1-handover-809177`, `claude/homayar-continuation-db5c83`, `claude/homayar-voice-orchestrator-d87b52`, `claude/homayar-wp06-t04-9cc506`) | various | 0 for every branch | yes | branch refs are local conveniences; the commits are on GitHub |
| Website repo stash | — | 0 entries | — | — |
| Session A nested clone `…/homayar-commercial-v1-a266f5/Homayar` | `feat/commercial-v1-overlay` `c5bf752`; also `session-a/wp08-backend` `18a6282`, `session-b/frontend-journey` `71e36aa`, `dev` | 0 (all contained in `f29d04a`) | yes | yes |
| Session A product worktree `…/homayar-commercial-v1-a266f5/HomaYar-session-a` (linked worktree of the clone above) | `session-a/wp08-backend` `18a6282`, clean | 0 | yes | yes |
| Session A scratchpad website worktree `/private/tmp/claude-501/…homayar-commercial-v1-a266f5/f6baf0a1-…/scratchpad/web` (registered worktree of the website repo) and its `Homayar/` worktree | `integration/commercial-v1` `f12b273`, clean; product `c5bf752` detached | 0 | yes | yes — after deleting the scratchpad run `git -C /Users/emperor/Documents/AI/Homayar.com worktree prune` to drop the stale entry |
| Session B nested clone `…/homayar-continuation-db5c83/Homayar` | `session-b/frontend-journey` `71e36aa`, clean; also legacy `master` `3766bc9` | 0 | yes (`master` is the remote legacy branch) | yes |
| Session C nested clone `…/homayar-v1-integration-d5beaf/Homayar/.git` | `session-c/integration` `f29d04a`, clean | 0 | yes | yes — the product files themselves are tracked in Homayar.com (ADR 0033) |
| Original checkout nested clone `/Users/emperor/Documents/AI/Homayar.com/Homayar` | `dev` `605cb07` + **98 uncommitted entries** | 0 commits, but uncommitted work | commits yes; **uncommitted work no** | **NO — preserve** (see §4) |

## 2. Uncommitted changes

| Location | State | Action |
| --- | --- | --- |
| Session A worktree, Session A scratchpad worktree, Session B worktree | clean (tracked and untracked, apart from ignored files below) | none |
| Session C worktree | clean after the handover commit | none |
| Original checkout (website root, `main` `8f4ba6a`) | **80 uncommitted entries** (27 modified, 31 deleted, 22 untracked): the pre-Commercial-v1 **website overlay** — routes moved under `src/app/(site)/`, new components, `src/lib/public-pricing.ts`, `public/.htaccess`, `scripts/build-cpanel.mjs` (cPanel export) and the Playwright suite `tests/site.spec.ts` that the CI `public-web` job waits for — plus older untracked copies of `docs/`, `infra/`, `.github/`, and local archives (`homayar-cpanel.zip`, handoff zips) | **preserve; not published anywhere** (WP01-T03 website overlay, `REMAINING_WORK.md` R-03); never reset/clean; see the migration procedure in `PROJECT_STATE.md` §10 |
| Original checkout nested product clone | **98 uncommitted entries** (includes `.claude/settings.local.json`, `.env.example`, backend files) | **preserve**; the Commercial v1 overlay was imported from this state in WP01-T03, but it has not been proven that every change there is represented in the final tree |

## 3. Ignored files that exist only in session worktrees

| Location | What | Needed? |
| --- | --- | --- |
| Session A worktree `docs/handoff/claude-fable-5.1/graphify/code/{graph.json,graph.html,extraction.json}`, `refreshes/2026090*…20260924*` | superseded Graphify snapshots | no — the canonical map was refreshed on 2026-09-26 and is reproducible with `tools/refresh_graph.py` |
| Session A worktree `graphify/planning/{graph.json,graph.html,graph.graphml,extraction.json,analysis.json}`, `graphify/vendor/vis-network-9.1.6.min.js` | planning graph (built 2026-09-07) and its renderer | not unique: byte-identical copies (SHA-256 checked) are in the original checkout's `docs/handoff/claude-fable-5.1/graphify/` (untracked there); the planning report and diagnostics are tracked |
| Session C worktree `graphify/code/graph.json`, `graphify/refreshes/20260925…`, `20260926T095535832212Z` | current code map JSON and its raw snapshot | reproducible (same helper; source hashes in the tracked `source-manifest.json`) |
| Session B worktree `.pnpm-store/` | package cache | no |
| All worktrees: `.venv`, `node_modules`, caches, `.claude/` | dependencies and per-worktree tool settings | no |

## 4. Knowledge that must not live only in a session

| Knowledge | Where it is now (published) |
| --- | --- |
| Project state, backlog, validation, next task | `docs/handoff/session-reset/*` |
| Session A/B/C records | `docs/handoff/session-b/*`, `docs/handoff/session-c/README.md`, `Homayar/docs/handoff/*`, `docs/handoff/claude-fable-5.1/PROGRESS.md` |
| Architecture and decisions | `docs/architecture/decisions/0001…0033` |
| Environment recipes (disposable PostgreSQL with the `homayar_app` role, Redis for drills, Docker credential-helper workaround, CI replica) | `VALIDATION.md` §5 and `docs/handoff/session-c/README.md` |
| Overwrite hazard for old nested clones | ADR 0033, `PROJECT_STATE.md` §10 |

Chat transcripts and Claude memory files are not project records; nothing above depends on them.

## 5. Scratchpads

| Scratchpad | Content | Needed? |
| --- | --- | --- |
| Session A `/private/tmp/claude-501/…homayar-commercial-v1-a266f5/f6baf0a1-…` (2.4 GB) | CI helper scripts, disposable PostgreSQL cluster `pg16d`, Gitleaks work dirs, the `web` worktree | no (scripts' results are recorded; see §1 for the worktree prune) |
| Session B `/private/tmp/claude-501/…homayar-continuation-db5c83/*` | `sb-preview.mjs`, `sb-qa-preview.mjs`, `qa-followup.html` (QA fixture API) | no — the QA findings were re-verified against the integrated backend; optional to keep for history |
| Session C `/private/tmp/claude-501/…homayar-v1-integration-d5beaf/f4d24a61-…` | CI replica scripts, logs, disposable databases | no — the reproduction recipe is in `VALIDATION.md` §5 |
| Other session directories under `/private/tmp/claude-501/` for this project | empty scratchpads | no |

The contents of other sessions' chat histories and any state outside the paths above could not be
inspected from Session C.

## 6. Local backups (not in any session; keep)

`/Users/emperor/Documents/AI/Homayar.com/.git/session-c-backups/` (109 MB, local only, never
uploaded): `20260925-a/` `--all` bundles of the Session A product clone, the Session B product clone,
the original checkout's product clone and the website repository; `20260925-b/` the Session B QA
bundle; each with `SHA256SUMS`. Bundles hold commits only — **not** the uncommitted work of the
original checkout. The product bundles can contain the legacy `master` history with
credential-shaped material: keep them local.

## 7. Services and temporary databases

| Service | Owner | State after the closeout |
| --- | --- | --- |
| PostgreSQL 16 `pg16c` (port 54351), containers `homayar-sc-ci-pg`, `homayar-sc-ci-redis`, `homayar-sc-drill-redis`, network `homayar-sc-drillnet` | Session C | stopped and removed at the end of the closeout |
| Earlier Session C journey services (`homayar-sc-redis`, `homayar-sc-proxy`, …) | Session C | removed on 2026-09-25 |
| `homayar-redis`, `homayar-neo4j` (exited months ago), network `homayar_default`, volumes `homayar_neo4j_*` | original checkout's Compose (not a Claude session) | not touched |
| `kohandezh_*`, `blogyar*`, `supabase-*`, `abryar-*`, `kohandezh-live-avatar-*` | other projects | not touched |

## 8. Before deleting the old sessions

1. Confirm `main` of `Kohandezh/Homayar.com` contains `docs/handoff/session-reset/` (this file).
2. Leave `/Users/emperor/Documents/AI/Homayar.com` (root checkout and its `Homayar/`) and `.git/session-c-backups/` alone.
3. Delete sessions/worktrees through the app, then run `git -C /Users/emperor/Documents/AI/Homayar.com worktree prune`.
4. Optional: delete the local branch refs listed in §1 except `main` (their commits are on GitHub); not required.
