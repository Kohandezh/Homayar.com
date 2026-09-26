# Session C — integration of Session A (WP08 backend) and Session B (console journey)

Single writer for the shared integration records. Single agent, strictly sequential,
2026-09-25. **Overall status: NOT PRODUCTION READY.**

## Working copies, branches and backups

| Repository | Working copy | Branch | Base | Head (local) |
| --- | --- | --- | --- | --- |
| Website `Kohandezh/Homayar.com` | `.claude/worktrees/homayar-v1-integration-d5beaf` | `claude/homayar-v1-integration-d5beaf` | `cfcc2c4` (`origin/main`) | see `git log`; published tip `b68803d` = `integration/commercial-v1` (PR #2) |
| Product `Kohandezh/HomaYar` | `…/homayar-v1-integration-d5beaf/Homayar` (independent clone from a verified bundle; ignored by the website) | `session-c/integration` | `c5bf752` | pinned in `infra/product-source.lock`; published tip `7be3abe` on `feat/commercial-v1-overlay` |

Backups (local only, outside every worktree, not committed): `.git/session-c-backups/20260925-a/`
(`--all` bundles of the Session A clone, the Session B clone, the website and the original
checkout, each `git bundle verify`: complete history) and `20260925-b/` (Session B QA commit
`5bb7a01`), with `SHA256SUMS`. The original checkout `/Users/emperor/Documents/AI/Homayar.com/Homayar`
(`dev`, dirty) was not touched.

## Provenance and integrated ranges

- Session A product: `session-a/wp08-backend` `42543aa..18a6282` (on `d1cc5d4`); Session B product:
  `session-b/frontend-journey` `d1cc5d4..71e36aa`. Merge base `d1cc5d4`; `42543aa` contains
  `d1cc5d4`. No file overlap (46 vs 56 files).
- Both were already merged when Session C started: Session A, acting as the integrator before this
  session, made `fc803e3` (verified to equal the automatic merge tree `820dcc7` of the two tips — no
  hand edits), `c5bf752` (test Redis isolation) and website `integration/commercial-v1`
  (`80e5e90` lint, `3c4dae1` Gitleaks, `83d45df` CI, `5a0710a` records, `f12b273`) and published
  them (PR #2). Session C reviewed those commits and built on them instead of merging again.
- Session B website: `3834f5e..5c02cb6` (in PR #2) and the QA report `5bb7a01` (merged here as
  `73e0253`).
- Session C product commits on `c5bf752`: `d3a52f9` console PostCSS isolation · `7be3abe` backend
  gaps G1/G2/G7/G9/G10/G11 and SMTP delivery · `39b816d` console QA fixes and contract consumption ·
  `663fc52` preflight egress allowlist · `57dafa2` worker logging · `f530b41` outage ≠ sign-out and
  labels · `d318f7e` proxy 429 on upload · `e7eeef5` test waits.
- Session C website commits on `f12b273`: `b68803d` public-web image without `next-env.d.ts` ·
  `73e0253` QA report · `bbf8844` network egress and Compose settings · `e5fd51e` nginx auth zone ·
  `34bc228`/`fbcc517` CI air-gap check · records and the lock pin (this commit series).

## CI

| Run | Website SHA | Result |
| --- | --- | --- |
| 36115417386 attempt 2 (reported by Session A) | `f12b273` | console build and container images failed |
| 36128541895 | `b68803d` (product `7be3abe`) | all six jobs passed after one re-run of the backend job (a Redis transport drill timed out once on the runner; it passes locally, see below) |

Fixed: the console loaded the website's `postcss.config.mjs` (Vite searches up to the
`pnpm-workspace.yaml` root) → inline PostCSS config in `vite.config.ts`; reproduced failing and
passing in a clean `node:22-alpine` container with the website files as parents. The public-web
image copied the ignored `next-env.d.ts` → removed (the installed Next.js docs: `next build`
generates it); the stages that had not run (console/API images, smoke tests, nginx, ShellCheck,
Hadolint) were run from a clean `git archive` context and passed. `PRODUCT_SOURCE_TOKEN` is
configured (the source-contract job passed); the Gitleaks allowlist was reviewed (the flagged value
is the file's SHA-256, checked) and not changed.

Not yet on the remote: everything after `7be3abe` / `b68803d` (publication needs approval).

## Backend gaps (Session B `BACKEND_GAPS.md`)

| Gap | Resolution |
| --- | --- |
| G1 method discovery | `GET /auth/methods` (enabled **and** configured); console build flags removed (ADR 0032) |
| G2 verification resend | `POST /auth/verify-email/resend` (session + CSRF, 60 s cooldown, 5/h, supersedes older links, audited); console notice button |
| G3 upload policy read | open (proposal; the console prints no numeric limits) |
| G4 permission list | open (role matrix in the console; the server enforces) |
| G5 member allowance | open — **owner decision** (members' view of the allowance) |
| G6 cancel processing | open (no cancel control is offered) |
| G7 OTP failure class | `503 phone_auth_unavailable` / `503 otp_delivery_failed`, nothing committed |
| G8 product source | resolved: product published, token configured, contract satisfied in CI |
| G9 expired memberships in `/auth/me` | one predicate for listing and authorizing |
| G10 view of summary/actions/evidence | `view`, `privacy_enforced`, `redaction` on each response; per-tab console notices |
| G11 workspace-scoped playback | `GET /workspaces/{id}/meetings/{id}/recording`, cookie + path membership, per-Range re-authorization, no credential in the URL |

Token usage: `provider_usage.input_tokens/output_tokens` are `NULL` when not reported (the
integrated database holds only NULLs from the test providers); no API or console view exposes them
yet, so nothing can show them as zero. The downgrade of `e3a7c5f9b2d4` rewrites NULL to 0 (lossy);
the round trip `downgrade -2 → upgrade → check` was re-run on PostgreSQL 16 (clean).

## Integration defects found and fixed

1. No e-mail could ever be delivered: the SMTP sender imported `fastapi_mail`, not a dependency
   (every send failed inside a broad `except`). Standard-library `smtplib`, no address in logs,
   correct reset-link lifetime (30 min, the e-mail said 1 hour), fa/en bodies.
2. nginx limited all of `/api/v1/auth/` to 5 requests/min per address; the console's own session
   reads (`/auth/me` on every start, `/auth/methods`, `/auth/refresh`, `/auth/logout`) received
   nginx 429 pages. Those four use the general API zone now.
3. Celery worker logs after the first task were lost (root handler on Celery's stdout proxy).
4. A page load during an API outage showed the sign-in form as if signed out, and `refreshUser`
   signed out on any error; now an `unavailable` state with Try again, only 401 signs out.
5. Renaming a workspace whose retention is `legacy_unresolved` silently sent `finite`/90 days
   (the form preselected them); no retention change is sent until a mode is chosen.
6. nginx's own upload 429 was an "unknown" failure in the console; it is a rate limit.
7. Scheduler joined the default network (route out) in both Compose files.
8. Compose passed none of the SMTP, Google, Asanak, PII key, AI provider or WP08 settings.

## Session B QA follow-up (`QA_FOLLOWUP.md`, reviewed at `71e36aa` with a fixture API)

Re-checked on the integrated tree (no console change since `71e36aa`, so every CODE finding
applied). All 6 P1, all 16 P2 and 17 of 18 P3 are fixed with tests (33 new console tests in total this session):
M-01…M-13, S-01…S-10, Q-01…Q-11, X-01…X-05. **Deferred:** M-14 (confirmation for revoking an
invitation — owner preference). Checked in the browser **against the integrated backend**
(not the fixture): M-01 (ArrowDown sends nothing, Apply sends one PATCH), M-02 (375 px:
document 375 = viewport, table scrolls in its region), X-02, Q-01 with back/forward, Q-02/Q-03/Q-07,
S-03, X-03 display, and the search error state Q-09 (NOT_RUN in B) with a real nginx 502 — in a
hidden browser pane whose visibility was overridden to "visible" so React Query would not pause.
Other items are covered by unit tests only. QA "observations outside frontend scope" 1 (settings
the console cannot show), 2 (English invitation e-mail), 3 (member list shows memberships outside
their window) and 4 (search paging) remain open.

## Customer journey against the integrated backend

Environment (disposable, Session C only): PostgreSQL 16 `127.0.0.1:54351` DB `homayar_sc_live`
migrated to head by the owner role, runtime login `homayar_sc_login` (NOSUPERUSER, NOBYPASSRLS,
member of `homayar_app`); Redis container `homayar-sc-redis`; local object storage and tombstone
journal in the scratchpad; uvicorn + a Celery worker (solo pool) + Celery beat; the production
console image and public-web image behind **the real `infra/nginx` configuration** (nginx
container, `127.0.0.1:8680`); an SMTP sink on `127.0.0.1:2525`. **Test doubles:** mock ASR and
analysis (development only), diarization "unavailable", SMTP sink. Nothing reached Google, Asanak,
Padyar, a mail server or a model.

- API journey through nginx (62 of 63 checks passed): website links → register → e-mail in the
  sink → login unverified → upload refused 403 → resend (cooldown 429, old link refused, new link
  verifies) → upload (ffprobe duration) → same-key replay (same meeting and job) → same key with
  other bytes 409 → corrupted file 422 `malformed_media` → real worker → truthful `partial`
  (diarization unavailable) → human correction → redaction enforced → audited reveal → usage
  settled once → quota exhaustion 429 with details → invitation by e-mail → member in two
  workspaces → playback 206 through the workspace path, 404/403/401 for the wrong workspace,
  an outsider and anonymous → expired membership absent from `/auth/me` and playback stops on the
  next Range → member cannot replay → delete → 404 everywhere → logout 401 → login with persisted
  state → wrong password 401. The one failure: a replay of a job the harness had relabelled from
  completed to dead-letter; the database refused a second settlement
  (`uq_reservation_settled_per_meeting`) and the job dead-lettered again with its reservation
  released (no double charge). A genuine dead letter was then produced by moving a queued job's
  deadline into the past: `job_deadline_exceeded`, no usage committed, member 403, owner replay 202,
  second replay 409, completes, usage committed exactly once.
- A local analysis endpoint without a manifest was refused by the default workspace policy as
  cloud (`provider_not_permitted`, result `partial`) — nothing was sent.
- Browser (built-in, Persian 375 px, English 1280 px, Persian 768 px; no horizontal page scroll):
  sign-in (password only), overview, meeting page with the player (native `<audio>`, 206 through
  nginx), search → `?at=` (segment focused, player cued, back/forward), members M-01/M-02, redacted
  transcript, per-tab notices, audited reveal and hide, registration, resend, verification link,
  UI upload → queued → partial with polling stopped at the terminal state, usage page (5 s, one
  committed event), sign-out. Every other supported website language was not tested.

## Network egress

Application guard tests (Session A, still passing); proxy policy and container-network denial:
`infra/egress/tests/test-egress-network.sh` (33 probes passed on Docker Desktop, once during
development and again in the final clean replica); air-gapped gate: `infra/ci/airgap_egress_check.py`
(private-only passes, cloud refused with exit 3). Production deployment validation: **not done**;
the cloud profile relies on the platform firewall (not verified); on-prem SMTP needs a relay.

## Tests (sequential, one worker)

| Scope | Revision | Result |
| --- | --- | --- |
| Backend, PostgreSQL 16, app role, `TEST_REDIS_URL` | product `d318f7e` | 702 passed, 45 skipped, 1 failed — the broker-restart drill, because the disposable Redis ran without persistence; the 5 transport drills then passed against a durable (AOF) disposable Redis |
| Skips | | 41 live S3 (opt-in, BLOCKED_EXTERNAL), 3 Padyar (contract missing, BLOCKED_EXTERNAL), 1 file-level restore drill (SQLite only; inapplicable on PostgreSQL) |
| New backend tests | `7be3abe`, `663fc52` | `test_customer_journey_gaps.py` 10 (SQLite and PostgreSQL), egress allowlist mode 1 |
| Migrations | `e7eeef5` | `check-migrations.sh` clean (one head, upgrade, no drift); round trip of the last two revisions clean |
| Console | `e7eeef5` | 113 tests, lint, `tsc`, `/app/` build — locally and in a clean `node:22-alpine` |
| Website | `e5fd51e` | frozen pnpm install, lint (0 errors, 2 pre-existing warnings), build — clean `node:22-alpine` |
| Container job replica | website `e5fd51e` / product `e7eeef5` | actionlint, Compose ×5, buildx checks ×4, four image builds, hardened smoke and users, egress test, nginx ×2, ShellCheck, Hadolint passed; the air-gap step was then corrected (`34bc228`) and verified in the built API image |

## Owner decisions still needed

1. **Pricing:** the website `/pricing` and `/catalog` show USD prices with a cart; the console states
   pricing is unpublished and billing manual. Choose one truthful commercial statement (WP21-T01/T02);
   release readiness stays blocked on it.
2. G5: may members see the remaining allowance?
3. M-14: confirm before revoking an invitation?
4. Persian console font: the console uses Geist (no Persian glyphs; system fallback). A locally
   bundled Vazirmatn (`@fontsource-variable/vazirmatn`, SIL OFL 1.1, offline) would match the website;
   it adds a dependency and was not added.
5. On-prem e-mail in the isolated topology (a mail relay) and the cloud platform firewall.
6. Unchanged: retention defaults, backup retention, empty-transcript charging, live S3,
   representative evaluation data, provider credentials, the Padyar AI Gateway API contract.

## Disposable services (Session C)

PostgreSQL `scratchpad/pg16c` (port 54351), containers `homayar-sc-redis`, `homayar-sc-redis-drill`,
`homayar-sc-proxy`, `homayar-sc-public-web`, `homayar-sc-console-web`, `homayar-sc-api-forward`,
network `homayar-sc-journey`, uvicorn/Celery/beat and the SMTP sink processes. Stopped and removed
at the end of the session (see PROGRESS). Other sessions' services (`homayar-ci-a-*`) were not
touched.

## Publication plan (superseded 2026-09-26)

PR #2 (`integration/commercial-v1` at `b68803d`) was merged into `main` (`a1c3d60`) by the owner.
The owner then asked for the `/Homayar/` ignore rule to be removed, the product source to be
committed in this repository, the product branch to be pushed first and a **draft** pull request
from `claude/homayar-v1-integration-d5beaf` to `main` (see below). Merging stays a separate
authorization.

## Monorepo import (2026-09-26, ADR 0033)

- `b9e94bf` commits the product tree of `Kohandezh/HomaYar` `e7eeef53` (378 files, no history)
  under `Homayar/` via `read-tree --prefix` (index only; the local clone was not touched) and drops
  `/Homayar/` from `.gitignore`. `git ls-tree HEAD Homayar` is `040000 tree` and equals
  `e7eeef53^{tree}`.
- `15adf6d` rewrites the contract for an in-repository tree: `product-source.lock` keeps the
  required/forbidden lists, adds `product_dir` and informational import provenance;
  `check-source-contract.sh` rejects an untracked directory, a gitlink or `.gitmodules` entry,
  uncommitted required paths, tracked forbidden paths, and (ci mode) a dirty directory or nested
  `.git`; 29 synthetic cases. CI runs it on the checkout; the `product-source` action and
  `PRODUCT_SOURCE_TOKEN` are retired. ADR 0002 marked superseded.
- `b7f263f` Gitleaks: the history scan found 8 `generic-api-key` false positives in the imported
  tests and one product record: hyphenated fixture and idempotency keys (for example
  purge-exhaust-0001), the keyword text of an idempotency argument, the name of the live-S3
  secret environment variable, and an Alembic revision id.
  Allowlists are scoped to path and value shape; a planted high-entropy secret in the same paths
  is still reported.
- `1f431bc` Tailwind v4 source detection follows `.gitignore`: without the rule the website CSS
  absorbed console classes (82,180 → 110,530 bytes). `@source not "../../Homayar"` restores a
  byte-identical stylesheet (same SHA-256 with and without `Homayar/` present).
- `daa738a` the website rule `.env*` matched the product's `.env.example` templates; a narrow
  negation keeps them visible while real `.env` files stay ignored by the product rules.
- Verification (clean clones, `node:22-alpine`, sequential): contract `--mode ci` satisfied and
  29/29 checker tests; actionlint and ShellCheck clean; Gitleaks over the branch history clean
  (73 commits); website lint 0 errors (2 pre-existing warnings) and build; console `dist/`
  byte-identical between the monorepo and the product-only layout (54 files), console lint, and
  tests 113/113 in three consecutive runs — a first run right after two builds in the same
  container had 1 of 113 failing (name not captured; not reproduced).
- **Existing workspaces:** a checkout whose `Homayar/` is still the old ignored clone gets those
  ignored files **overwritten** by `git checkout`/`merge`/`pull` of this branch. Back up or commit
  the clone's uncommitted work first, or update with `--no-overwrite-ignore` (ADR 0033). The
  original workspace checkout (repository root, on `main`) has pre-existing dirty state in its
  `Homayar/` clone and was not touched.

## Closeout (2026-09-26)

Owner authorization for the closeout: publish and merge the A/B/C integration after the required
checks pass, synchronize the product repository once, and write a durable handover. ADR 0033 was
approved by the owner (status now accepted).

- **CI failure on PR #3 and on `main` `a1c3d60`:** `test_killed_pool_child_requeues_inertly_and_recovery_settles_once`
  reported `lease_expired` for the requeued copy. Reproduced 3/3 in a Linux replica of the CI job:
  Celery's prefork parent notices a killed child through its 5 s maintenance timer on Linux, so
  the redelivery arrives after the drill's 4 s lease. Fixed in the test (`2a30213`: 12 s lease for
  this drill, kill only with ≥ 7 s of lease left); every assertion unchanged. Evidence: drill 3/3
  and the full suite (667 passed, 81 skipped, 0 failed) in the replica; CI run 36234124732 green on
  all six jobs.
- **Merged:** PR #3 → `main` `edfcb78` (merge commit; tree identical to `2a30213`).
  `Kohandezh/HomaYar` PR #1 (`feat/commercial-v1-overlay` `f29d04a` → `dev`) → `c567a91`; its tree
  equals `main:Homayar` (`4b81c5dd`). `master` there is disconnected legacy history and was not
  touched. No further product changes go to `Kohandezh/HomaYar`.
- **Found:** the CI runner has no ffmpeg, so the 35 `ffmpeg` tests are skipped in CI (81 skips in
  total); recorded as R-02 in `docs/handoff/session-reset/REMAINING_WORK.md`.
- **Records:** `docs/handoff/session-reset/` (project state, remaining work, validation, the next
  session's master prompt, the session deletion checklist); Graphify refreshed at `2a30213`;
  WP01-T02/T05 notes updated.
- **Disposable services:** the services restarted for the drill investigation (PostgreSQL
  `scratchpad/pg16c`, containers `homayar-sc-ci-pg`, `homayar-sc-ci-redis`,
  `homayar-sc-drill-redis`, network `homayar-sc-drillnet`) were stopped and removed at the end.

