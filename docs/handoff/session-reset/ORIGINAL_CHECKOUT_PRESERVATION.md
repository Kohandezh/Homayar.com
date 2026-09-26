# Original checkout — preservation record (2026-09-26)

Sanitized record. No file contents, secret values or private data are reproduced here.

## Backup

- Location (local only, mode 0700, never commit or upload):
  `/Users/emperor/Backups/homayar-20260926-safe-recovery/` — outside the original checkout, every
  session worktree and every scratchpad. Its `BACKUP_MANIFEST.md` has the full inventory, exclusions
  and restore steps.
- Contents: `git bundle --all` of the website repo, the nested product clone and the nested legacy
  `sample/` repo; a tar of the whole checkout including both `.git` directories (index, refs,
  `.git/session-c-backups/`), uncommitted, untracked and ignored files; per-level status, refs and
  binary diffs; a SHA-256 inventory of all 12,938 files.
- Excluded as reproducible: `.claude/worktrees/`, `node_modules/`, `.next/`, `.venv/`, `.pnpm-store/`,
  Python caches.
- Verified in a separate temporary directory: archive and bundle checksums, `git bundle verify`,
  all 12,938 file hashes equal, `git status` byte-identical for the website root (308 entries) and the
  product clone (205), no staged changes at any level, `git fsck` clean. The legacy `sample/` repo
  differs only by excluded tracked `.pyc` files, recoverable from its history.
- The tar holds private material (`.env` files, local SQLite databases, the legacy `master` history
  with credential-shaped values): keep it local.

## What was found (read-only inventory; nothing changed in the original)

| Level | HEAD | Uncommitted | Local-only commits |
| --- | --- | --- | --- |
| Website root `/Users/emperor/Documents/AI/Homayar.com` | `main` `8f4ba6a` | 27 modified, 31 deleted, 250 untracked files; nothing staged | 0 |
| Nested product clone `…/Homayar` | `dev` `605cb07` | 34 modified, 171 untracked files; nothing staged | 0 |
| Nested legacy repo `…/Homayar/sample` (not previously listed) | `master` `3766bc9` | modified and untracked files, incl. local databases | 0 |

## Overlap with current `main` (`620f14c`), by file hash — for review only, not applied

- Website root (outside `Homayar/`): 78 files identical to `main`, 67 differ, 131 absent from `main`,
  31 deleted locally but present on `main`. The unpublished overlay (R-03): routes moved into
  `src/app/(site)/` (33 new, 31 old route files deleted), `scripts/build-cpanel.mjs`,
  `tests/site.spec.ts`, `playwright.config.ts`, `public/.htaccess`, `src/lib/public-pricing.ts`,
  changed `next.config.ts`, `package.json`, `pnpm-lock.yaml`; none of these overlay paths exist on
  `main`. Also present and unrelated to Homayar: `docs/blogyar/` (67 files), `OPENCODE.md`, local
  archives (`homayar-cpanel.zip`, handoff zips). Older copies of `docs/`, `infra/`, `.github/` mostly
  predate `main` (differences there are expected to be stale, not new work).
- Product clone vs `main:Homayar/`: 48 identical, 94 differ, 63 absent (61 of them a local
  `graphify-out/` cache, plus `.claude/settings.local.json` and a local SQLite dev database). The
  94 differing files are the pre-import state; whether any of their changes are missing from `main`
  has not been proven file by file (`PROJECT_STATE.md` §10 step 4).

## Before deleting old sessions

The session worktrees hold no unpublished commits (0 local-only commits across all website
branches, re-checked 2026-09-26). The original checkout and `.git/session-c-backups/` are now also
preserved in the backup above, but **keep the original checkout itself** until the owner has
reviewed the overlay (R-03) and the product-clone differences; the backup is a safety copy, not a
review. Do not delete the backup directory.

## Security follow-up (separate; owner action)

Credential-shaped material in the legacy `Kohandezh/HomaYar` history is tracked as `REMAINING_WORK.md`
R-01 (`docs/security/credential-containment-record.md`, families CF-01…CF-07). A clean scan of
`Kohandezh/Homayar.com` does not resolve exposure in the other repository's history. No value was
displayed or tested; nothing was rotated, revoked or rewritten. The backup's product and `sample/`
bundles contain that history and must stay local.
