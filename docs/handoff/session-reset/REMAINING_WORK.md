# Remaining work (prioritized, verified 2026-09-26)

Every item below was re-checked against the integrated source at `2a30213` (PR #3) or the current
remote state on 2026-09-26; items that turned out to be resolved are listed at the end instead of
being carried forward. Identifiers `WPnn-Tnn` are the planning tasks in `docs/planning/backlog.json`;
`R-nn` numbers exist only in this document. Kinds: **ENG** engineering (can be done in the
repository), **OWNER** owner decision or owner action, **EXT** external dependency (input from a
third party). Status vocabulary as in the planning records: `not_started`, `partial`,
`validated` (local scope), `blocked_external`. The product is **NOT PRODUCT READY** (NOT
PRODUCTION READY).

## Priority 0 — safety and truthfulness before anything is shown to customers

### R-01 · WP02-T01 · Credential containment for the legacy product history — OWNER/EXT · `blocked_external`
- Remaining: revoke/rotate the provider credentials whose key-shaped values sit in the legacy
  `master` history of `Kohandezh/HomaYar` (11 locations, source and `.pyc`), review provider usage,
  then decide on history cleanup. Also revoke the now-unused Actions secret `PRODUCT_SOURCE_TOKEN`
  in `Kohandezh/Homayar.com` (ADR 0033).
- Dependencies: the credential owners.
- Acceptance: each credential family in `docs/security/credential-containment-record.md` (CF-01…CF-07)
  has a dated revocation/rotation record; the token is revoked; no value is printed anywhere.
- Verification: owner confirmation; Gitleaks stays clean on `Kohandezh/Homayar.com` (the legacy
  history is not in this repository).
- Paths: `docs/security/REMAINING-P0.md`, `docs/security/credential-containment-record.md`, WP02.

### R-02 · WP01-T05 follow-up · CI does not run the 35 `ffmpeg` tests — ENG · implemented locally, remote CI not run
- 2026-09-26 (branch `ci/r02-ffmpeg-backend-tests`, local commit, not pushed): the backend job installs
  `ffmpeg`/`ffprobe` from the Ubuntu archive and prints both versions; `HOMAYAR_REQUIRE_MEDIA_TOOLS=1`
  makes pytest stop with a usage error instead of skipping the `ffmpeg` marker; `pytest -rs` lists every
  remaining skip. Local evidence in `VALIDATION.md` §3. Still open: a green run on the GitHub runner
  (needs push authorization) and the broker-restart drill decision below. Expected CI result on
  that runner: 702 passed, 46 skipped (41 live S3, 3 Padyar AI Gateway, 1 SQLite-only drill,
  1 broker-restart drill without `TEST_REDIS_CONTAINER`) — an expectation, not a measurement.
- Found in the closeout: the GitHub runner has no `ffmpeg`, so the backend job skips every test
  marked `ffmpeg` (media validation, adversarial media through the upload route, media lifecycle).
  CI result on `2a30213`/`main`: 81 skipped = 35 `ffmpeg` + 41 live S3 + 3 Padyar + 1 SQLite-only
  drill + 1 broker-restart drill (needs `TEST_REDIS_CONTAINER`). These tests pass locally with
  ffmpeg (702 passed at `d318f7e`), but never in CI.
- Work: install a pinned `ffmpeg` in the backend job (and decide whether the broker-restart drill
  should run in CI with a restartable Redis container).
- Acceptance: CI backend job reports 46 or fewer skips with every skip explained; the 35 media tests
  pass on the runner; no test weakened.
- Verification: the CI job log (`pytest -rs` summary); the replica recipe in `VALIDATION.md` §5 with
  ffmpeg installed.
- Paths: `.github/workflows/ci.yml` (`backend` job), `Homayar/backend/tests/conftest.py` (markers),
  `docs/operations/ci.md`.

### R-03 · WP01-T03 (website part) · Unpublished website overlay in the original checkout — OWNER + ENG
- State: the original workspace checkout (`/Users/emperor/Documents/AI/Homayar.com`, not a session)
  holds 80 uncommitted website entries from before Commercial v1: routes moved under
  `src/app/(site)/`, 31 deleted route files, new components, `src/lib/public-pricing.ts`,
  `public/.htaccess`, `scripts/build-cpanel.mjs` (cPanel export) and the Playwright suite
  `tests/site.spec.ts`. The CI `public-web` job already has conditional steps for the export and
  the browser tests and reports them as "not yet in the repository".
- Work: back up that state (see `PROJECT_STATE.md` §10), review it with the owner, port the wanted
  parts on a branch from `main`, reconcile with the pricing decision (R-15), run the export and the
  Playwright suite in CI.
- Dependencies: owner review; R-15 for any price or checkout copy.
- Acceptance: the chosen overlay is on `main`; `public-web` runs the cPanel export and browser tests
  (not the "missing" warning); the original checkout's work is either ported or explicitly discarded
  by the owner.
- Paths: original checkout (read-only until the owner decides), `.github/workflows/ci.yml`
  (`public-web`), `docs/operations/ci.md`.

### R-04 · ADR 0033 follow-up · Migrate the original checkout safely — OWNER-assisted
- Work: follow the procedure in `PROJECT_STATE.md` §10 (backup, move the nested clone aside, never
  `pull`/`checkout` over the ignored clone). Do not run it without the owner.
- Acceptance: the owner's workspace is on current `main`, `Homayar/` is a tracked directory with no
  nested `.git`, and every previous uncommitted change is either ported, archived locally or
  explicitly discarded.

## Priority 1 — next engineering on the critical path

### R-05 · WP09-T01 · Channel metadata, silent/duplicated channels and trusted PBX mapping — ENG · `not_started` · **next task**
- Scope (local, synthetic fixtures): extract channel layout, sample rate and duration per stream with
  the existing bounded `ffprobe` path; measure per-channel energy/speech presence to flag silent and
  duplicated (near-identical) channels; a per-ingestion-source channel→role mapping that is only
  **trusted** when configured by an authorized administrator for that source (stereo alone never
  establishes identity, ADR/F25); persist the results (migration) and expose them as a quality
  signal; unknown when not established.
- Dependencies: WP05 media validation (`validated` locally), WP06 stage runs (`validated`), WP08
  policies (`partial`, not needed for T01). The F25 accuracy gate (≥98 % on the agreed set) needs
  WP13 data (`blocked_external`) and stays pending.
- Acceptance: see `NEW_SESSION_MASTER_PROMPT.md` (task section).
- Paths: `Homayar/backend/media/validation.py`, `Homayar/backend/database/saas_models.py`,
  `Homayar/backend/jobs/`, `Homayar/backend/migrations/versions/`, `docs/planning/work-packages/WP09/`.

### R-06 · WP01-T04 / WP01-T06 · Reproducible builds and a release manifest — ENG · `not_started`
- Work: builds of API, worker and console images from a clean checkout of `main` with locked
  dependencies and recorded digests; a release manifest (source SHA, image digests, Alembic head,
  model manifests, validation links).
- Dependencies: none for the build part; model manifests from WP08-T01 exist.
- Acceptance: two clean builds of the same SHA produce the recorded inputs; the manifest is
  generated in CI and linked from the release notes.
- Paths: `infra/docker/`, `.github/workflows/ci.yml`, `infra/product-source.lock`.

### R-07 · Session B gaps G3, G4, G6 — ENG · open (verified: no such routes exist)
- G3 upload policy read (`GET` of the accepted types/size limits so the console can state them);
  G4 effective permissions per workspace (the console mirrors the role matrix today; the server
  enforces); G6 cancel processing (no cancel control is offered).
- Acceptance per `docs/handoff/session-b/BACKEND_GAPS.md` §G3/G4/G6, with authorization-matrix
  entries and console consumption behind tests.
- Paths: `Homayar/backend/routers/v1/`, `Homayar/docs/security/authorization-matrix.md`,
  `Homayar/frontend/src/`.

### R-08 · QA observations outside frontend scope — ENG/OWNER · open (verified in code 2026-09-26)
1. Settings the console cannot show (`record_scope`, share-link switches, `duplicate_media_policy`,
   `pii_redaction_*`): exposing them is an **OWNER** product decision, then ENG.
2. Invitation e-mail is English only (`Homayar/backend/services/member_service.py`
   `send_invitation_email`) — ENG: fa/en bodies like the verification/reset mails; verify that
   `FRONTEND_URL` includes the `/app` base in deployment (not verified).
3. Member list filters on `is_active` only (`list_workspace_members`); `valid_from`/`valid_until`
   are not applied to the listing (authorization already uses the validity window, G9) — ENG.
4. Search has no cursor/total (`limit` ≤ 50) — ENG with an API change.
- Acceptance: each item has a backend test and, where it reaches the console, a console test.

## Priority 2 — external dependencies (cannot be completed in the repository alone)

| ID | Task | State verified 2026-09-26 | Needed from | Acceptance when unblocked |
| --- | --- | --- | --- | --- |
| R-09 | WP08-T04 Padyar AI Gateway adapter | explicit `provider_contract_unavailable` boundary; 3 tests skip for the missing contract | the Padyar AI Gateway API contract (endpoints, auth, models, STT/embedding support) | adapter passes the identical typed contract suite (ADR 0029) |
| R-10 | WP13-T01/T02, WP08 qualification, WP09-T07, WP10, WP11 | only test/mock providers ever ran; no representative data | consented representative evaluation data, rights records, provider credentials | frozen baselines; F27 WER/CER gates measured on held-out sets |
| R-11 | WP05-T05 live S3-compatible storage | contract suite runs against local storage and a controlled fake; 41 live tests are opt-in and skipped | a real S3-compatible bucket and credentials (owner infra) | live run of `test_storage_contract.py` with the live marker, recorded |
| R-12 | WP08-T06 egress in production | application guard validated; on-prem network layer tested on Docker (CI and locally); cloud profile relies on a platform firewall | the deployment topology; cloud firewall rules; an on-prem SMTP relay | egress probes from the deployed containers; firewall evidence |
| R-13 | Real authentication integrations | SMTP delivery implemented (stdlib `smtplib`) and verified only against a local sink; Google sign-in and Asanak SMS OTP implemented with explicit `503` failure classes, never run live | SMTP relay, Google OAuth client, Asanak account | end-to-end sign-in/verification with the real services in a staging environment |

## Priority 3 — owner decisions

| ID | Decision | Blocks |
| --- | --- | --- |
| R-15 | **Pricing consistency:** website `/pricing` and `/catalog` show USD prices with a cart; the console says pricing is unpublished and billing manual. Choose one truthful statement (WP21-T01/T02) | release readiness, R-03 |
| R-16 | Retention defaults and backup retention (WP07, WP20) | production configuration |
| R-17 | Empty-transcript charging: today the audio-seconds reservation is committed and the result is `insufficient_evidence` (ADR 0003, isolated in `Homayar/backend/jobs/processor.py`); the owner may choose release instead — WP06/WP18 | billing (WP18) |
| R-18 | G5 members' view of the remaining allowance; M-14 confirmation before revoking an invitation; bundling a Persian console font (Vazirmatn, SIL OFL) | small console changes |
| R-19 | `Kohandezh/HomaYar`: archive or keep (its default branch `master` is disconnected legacy history; `dev` now holds the final synchronized state); revoke `PRODUCT_SOURCE_TOKEN` (R-01) | repository hygiene |

## Priority 4 — remaining release gates (not started unless noted)

WP01-T04/T06 (R-06) · WP02-T02 (`partial`), T03, T04 (`partial`), T05, T06 — secrets, TLS, network
exposure, encryption and configuration tests on the supported topology · WP05-T05 (R-11) ·
WP07/WP08 `partial` items above · WP09–WP12 speech, diarization, quality signals, call report ·
WP13 corpus (R-10) · WP14 review/calibration · WP15 dashboards · WP16 integration contracts
(T02 `partial`) · WP17 retrieval · WP18 billing · WP19 offline package · WP20 observability and
recovery · WP21 truthful website · WP22 administration/i18n/docs · WP23 federation and independent
security qualification · WP24 approved actions. Package READMEs under
`docs/planning/work-packages/` hold the acceptance criteria.

## Known uncertainties (not tasks, but keep watching)

- Console tests: one run in Session 16 had 1 of 113 failing right after two builds in the same
  container; the test name was not captured and three later runs passed 113/113. Cause unknown. If
  it recurs, capture the name before re-running.
- The broker-restart transport drill is skipped in CI (needs `TEST_REDIS_CONTAINER`); it passed
  locally against a durable (AOF) disposable Redis on 2026-09-25.
- At `b68803d` (PR #2) one backend CI attempt timed out in "a Redis transport drill" and passed on
  re-run; the drill was not named in the record. The kill-child root cause fixed in `2a30213` may
  explain it; that is not proven.

## Resolved during the closeout (do not carry forward)

- Kill-child transport drill red on `main` `a1c3d60` and PR #3: fixed in `2a30213` (test timing
  only; guarantees unchanged — `VALIDATION.md` §2).
- Product source availability (G8, `PRODUCT_SOURCE_TOKEN` for CI): superseded by ADR 0033; CI uses
  the tracked tree.
- Graphify map of the integrated tree: refreshed 2026-09-26.
- Session B QA P1/P2/P3 findings: fixed (M-14 deferred) — dispositions in `PROJECT_STATE.md` §7.
