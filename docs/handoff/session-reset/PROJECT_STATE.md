# Homayar — project state (session reset, 2026-09-26)

**Overall status: NOT PRODUCTION READY.** No mandatory release gate has passed; nothing has been
deployed; no real speech, diarization or analysis model has been qualified; no real customer data
has been processed.

This document is the entry point for a fresh session. It states what is verified, where the
evidence is, and what is only recorded history. Companion files in this directory:
[REMAINING_WORK.md](REMAINING_WORK.md) (prioritized backlog), [VALIDATION.md](VALIDATION.md)
(commands, revisions, results), [NEW_SESSION_MASTER_PROMPT.md](NEW_SESSION_MASTER_PROMPT.md)
(prompt for the next session), [SESSION_DELETION_CHECKLIST.md](SESSION_DELETION_CHECKLIST.md).

Status vocabulary (from `docs/planning/backlog.json`): `not_started`, `in_progress`, `partial`,
`validated` (always **local scope** unless a production check is named), `blocked_external`.
"Validated" never means production-validated in this project so far.

## 1. Product

Homayar is a Persian-first conversation-intelligence product for contact-center quality review:
authorized ingestion of recorded calls/meetings → validated audio → transcript with speaker/role
attribution → evidence-backed QA findings → supervisor correction and review → reports and trends,
offered as SaaS and as a qualified private/offline deployment. The dated commercial blueprint is
`docs/planning/homayar-commercial-blueprint.md` (95 features, 24 work packages, 161 subtasks).

Architecture (as implemented):

- **Backend** `Homayar/backend/`: FastAPI API, Celery workers and beat (Redis broker), PostgreSQL
  as the system of record with row-level security for a non-bypass application role
  (`homayar_app`), Alembic migrations (`Homayar/backend/migrations/`), private object storage
  (local or S3-compatible adapter), provider layer (`Homayar/backend/providers/`: local Whisper,
  OpenAI-compatible analysis endpoints, an explicit Padyar AI Gateway boundary, Asanak SMS,
  diarization "unavailable", development-only mocks), policy-based routing with egress guards.
  Neo4j is an optional, disabled-by-default projection.
- **Console** `Homayar/frontend/`: React/Vite single-page app served under `/app/` (fa/en, RTL).
- **Website** `src/`: Next.js marketing site (read `node_modules/next/dist/docs/` before changing
  it — `AGENTS.md`).
- **Infrastructure** `infra/`: Compose profiles (on-prem, cloud, optional Neo4j), Dockerfiles for
  API/worker, console, public web and the egress proxy, nginx reverse proxy, egress proxy with an
  allowlist, CI helpers. CI in `.github/workflows/ci.yml` (`docs/operations/ci.md`).

## 2. Repositories and source layout

| Repository | Role | Default branch | State on 2026-09-26 |
| --- | --- | --- | --- |
| `Kohandezh/Homayar.com` | **authoritative** for all future development: website, product (`Homayar/`), infrastructure, CI, records | `main` | `main` = `edfcb78` (PR #3 merge) + this handover |
| `Kohandezh/HomaYar` | former product repository; history before the import | `master` (disconnected legacy history, no common ancestor with the product line) | `dev` = `c567a91` (PR #1 merge), tree identical to `main:Homayar`; no further changes |

- **ADR 0033 (accepted 2026-09-26)** — `docs/architecture/decisions/0033-product-source-in-this-repository.md`:
  the product tree is tracked under `Homayar/` as ordinary files (never a gitlink), imported from
  `Kohandezh/HomaYar` `e7eeef53` without history (`b9e94bf`). `infra/product-source.lock` +
  `infra/ci/check-source-contract.sh` enforce the in-repository contract; CI builds from the
  repository checkout; `PRODUCT_SOURCE_TOKEN` is no longer used (revocation is an owner action).
  ADR 0002 (external pin) is superseded.
- **Final synchronization:** the only product change made after the import (the transport drill
  fix `2a30213`) was committed once to `Kohandezh/HomaYar` (`f29d04a`) and merged into `dev`. At
  that point `main:Homayar` and `HomaYar dev^{tree}` are the same tree `4b81c5dd`; there are no
  intentional differences. There is no dual-write workflow: later product changes go only to
  `Kohandezh/Homayar.com`.
- **Legacy `master` of `Kohandezh/HomaYar`** holds credential-shaped material in its history
  (`docs/security/REMAINING-P0.md`); it was not merged anywhere and must not be.

## 3. Published and merged pull requests

| Repository | PR | Head → base | Merged | Merge commit |
| --- | --- | --- | --- | --- |
| Homayar.com | [#1](https://github.com/Kohandezh/Homayar.com/pull/1) Commercial v1: WP03–WP07 decisions, infrastructure, pinned product source | `claude/homayar-commercial-v1-a266f5` → `main` | 2026-09-24 | `cfcc2c4` |
| Homayar.com | [#2](https://github.com/Kohandezh/Homayar.com/pull/2) Integrate Session A (WP08) and Session B (console journey); repair CI | `integration/commercial-v1` `b68803d` → `main` | 2026-09-26 (owner) | `a1c3d60` |
| Homayar.com | [#3](https://github.com/Kohandezh/Homayar.com/pull/3) Product source tracked here (ADR 0033) + Session C network egress + drill fix | `claude/homayar-v1-integration-d5beaf` `2a30213` → `main` | 2026-09-26 | `edfcb78` |
| Homayar.com | this handover (session-reset records, Graphify refresh) | `claude/session-reset-handover` → `main` | see the PR | — |
| HomaYar | [#1](https://github.com/Kohandezh/HomaYar/pull/1) Commercial v1 final synchronization | `feat/commercial-v1-overlay` `f29d04a` → `dev` | 2026-09-26 | `c567a91` |

Relevant source SHAs: product import `e7eeef53` (HomaYar) → `b9e94bf` (Homayar.com); Session A
product range `42543aa..18a6282`; Session B product `d1cc5d4..71e36aa`, website `3834f5e..5c02cb6`,
QA report `5bb7a01` (merged as `73e0253`); Session C product `d3a52f9 7be3abe 39b816d 663fc52
57dafa2 f530b41 d318f7e e7eeef5` and website `b68803d 73e0253 bbf8844 e5fd51e 34bc228 fbcc517`;
closeout `b9e94bf 15adf6d b7f263f 1f431bc daa738a 1d03edd 2a30213`. All verified as ancestors of
`2a30213` (website) and `f29d04a` (product) on 2026-09-26. Validation references: `VALIDATION.md`.

## 4. Work packages (from `docs/planning/backlog.json`, 2026-09-26)

| WP | Title | Status | Subtasks |
| --- | --- | --- | --- |
| WP01 | Reproducible source and release baseline | in_progress | T01–T03, T05 validated; T04 reproducible builds, T06 release manifest not_started |
| WP02 | Credential containment and deployment security | in_progress | T01 blocked_external (credential owners); T02, T04 partial; T03, T05, T06 not_started |
| WP03 | Identity, tenancy and operational permissions | validated (local) | T01–T07 |
| WP04 | Truthful states and evidence contracts | validated (local) | T01–T06 |
| WP05 | Ingestion and private object lifecycle | partial | T01–T04, T06 validated; T05 partial (live S3 blocked_external) |
| WP06 | Durable processing and recovery | validated (local) | T01–T07 |
| WP07 | Retention, deletion and text privacy | partial | T01–T07 validated in local scope; open: business retention defaults (owner), F16 accuracy on an annotated representative set (blocked_external), restore rehearsal and alerting (WP20) |
| WP08 | Provider registry and privacy routing | partial | T01–T06 validated (local; T06 network layer on the on-prem profile); Padyar AI Gateway contract, model qualification, production egress remain |
| WP09 | Channel-aware speech and persistent inference | not_started | next: T01 |
| WP10 | Diarization, overlap and alignment | not_started | |
| WP11 | Measured signals and quality evaluation | not_started | |
| WP12 | Call report, summaries and correction | not_started | |
| WP13 | Corpus and promotion evaluation | in_progress | T01 blocked_external (rights/data); T02 partial (protocol draft) |
| WP14 | Review, calibration, appeals, coaching | not_started | |
| WP15 | Issue trends and role dashboards | not_started | |
| WP16 | Ingestion/API/integration contracts | in_progress | T02 partial (scoped API keys) |
| WP17 | Scoped retrieval and Copilot | not_started | |
| WP18 | Usage, plans and billing | not_started | (usage ledger foundations exist from WP06-T06) |
| WP19 | Offline package, licensing, upgrades | not_started | |
| WP20 | Observability, audit and recovery | not_started | |
| WP21 | Truthful website and sales funnel | not_started | pricing decision pending |
| WP22 | Administration, i18n and documentation | not_started | |
| WP23 | Enterprise federation and security qualification | not_started | |
| WP24 | Approved actions, alerts and exports | not_started | |

No package is complete in production terms. WP03, WP04 and WP06 are validated in local scope only.

## 5. Status by area

| Area | Implemented and verified (scope) | Not verified / missing |
| --- | --- | --- |
| Authentication | Password accounts, e-mail verification with resend (cooldown, supersession), password reset, session rotation/revocation, CSRF, method discovery `GET /auth/methods`; SMTP sender (stdlib) — backend tests, and the Session C journey through the real nginx config with an SMTP sink | Google sign-in and Asanak SMS OTP never run against the real services (explicit `503` classes when unavailable); no SMTP relay tested; enterprise SSO (WP23) |
| Tenant isolation | Authorization matrix with deny-by-default route inventory; row-level security with a non-bypass role, per-transaction context; two-tenant negative suites — PostgreSQL tests in CI | managed PostgreSQL/PgBouncer, penetration test |
| Privacy | Retention modes, legal hold, tombstone-first deletion, durable purge, tombstone journal/reapply, text PII redaction with restricted mappings, redacted-by-default views with audited reveal, subject-scope export — tests; redaction and reveal seen in the journey | retention/backup policy decisions (owner); legal review |
| Storage | Local object storage and S3 adapter over a contract suite (controlled fake client); media validation with ffprobe and adversarial fixtures | live S3-compatible run (41 opt-in tests); **the 35 ffmpeg tests do not run in CI** (runner lacks ffmpeg; fix on local branch `ci/r02-ffmpeg-backend-tests`, remote CI not run — R-02) |
| Processing | Outbox dispatch, leases with fencing, heartbeat, checkpoints, failure classes, dead letter and replay, recovery sweeps, invariant checker, transport drills with real Celery prefork workers and Redis — in CI | deployed stack; broker-restart drill skipped in CI |
| Usage | Append-only usage ledger, single settlement enforced by the database, reconciliation, quota refusal with details — tests and journey | plans/billing (WP18); empty-transcript charging is an owner decision |
| Provider routing | Versioned manifests, per-workspace AI policies with snapshots, eligibility filtering before ranking, deadlines/circuits/fallback, egress guard; on-prem network egress denial and allowlist proxy tested in CI; air-gap gate | Padyar AI Gateway contract (blocked_external); no real model qualified; cloud firewall and production egress unverified |
| Speech and AI quality | Local Whisper adapter and analysis adapters exist behind the contract suite | no representative data, no measured WER/diarization/QA quality; real diarization unavailable; WP09–WP12 not started |
| Console | Customer journey pages (auth, workspace gate, upload, processing states, meeting page with player, transcript/redaction, correction, members, settings/audit, search, usage), fa/en with RTL; 113 tests; lint; build | real devices, Safari/Firefox, screen readers; four QA observations (§7) |
| Website | Next.js marketing site, lint and build in CI | **no website tests in the repository** (Playwright suite and cPanel export exist only uncommitted in the original checkout, R-03); pricing statements inconsistent with the console (owner, R-15) |
| Infrastructure | Compose profiles validated, four images built and smoke-tested with hardened runtimes, nginx configs, egress proxy — in CI | no deployment; TLS, secrets management, backups and monitoring on a real topology not done (WP02, WP20) |

## 6. Customer journeys

Verified on 2026-09-25 against the integrated backend (details: `docs/handoff/session-c/README.md`
§ Customer journey): register → verification e-mail (SMTP sink) → unverified upload refused →
resend and verify → upload with ffprobe duration → idempotent replay and conflict → malformed media
refused → real Celery worker → truthful `partial` (diarization unavailable) → human correction →
redaction enforced and audited reveal → usage settled once → quota exhaustion `429` → invitation →
member in two workspaces → workspace-scoped playback (`206`, and `404/403/401` for the wrong
workspace, an outsider, anonymous) → expired membership removed → delete → `404` everywhere →
logout/login. Dead letter and owner replay (settles once). Browser: fa 375 px, en 1280 px,
fa 768 px through the real nginx configuration.

**Test doubles in that journey:** mock ASR and analysis (development only), diarization
"unavailable", SMTP sink. Nothing reached Google, Asanak, the Padyar AI Gateway, a mail server or a
model. It demonstrates the platform flow, not transcription or analysis quality.

Known gaps: real speech/analysis quality (WP09–WP12, blocked by data); G3 upload policy read,
G4 permission list, G6 cancel processing (`REMAINING_WORK.md` R-07); G5 members' allowance view
(owner); QA observations (§7); website pricing/checkout claims (R-15).

## 7. Session B QA follow-up — current dispositions

Source: `docs/handoff/session-b/QA_FOLLOWUP.md` (audit of `71e36aa` against a **fixture** API);
fixes in Session C product commits `39b816d`, `f530b41`, `d318f7e` (now in `Homayar/frontend/`).
When Session C re-checked the findings on the integrated tree, none had already been resolved by
the integration (there was no console change after `71e36aa`), so every finding was treated as current.

| Disposition | Findings |
| --- | --- |
| Fixed and verified against the **integrated backend** in the browser, plus console tests | M-01, M-02, X-02, Q-01 (with back/forward), Q-02, Q-03, Q-07, S-03, X-03 (display), Q-09 (search error state with a real nginx 502; NOT_RUN in Session B) |
| Fixed, verified by **console unit tests only** (not browser-checked against the real backend) | M-03…M-13, S-01, S-02, S-04…S-10, Q-04…Q-06, Q-08, Q-10, Q-11, X-01, X-04, X-05 |
| Deferred (owner preference) | M-14 — confirmation before revoking an invitation |
| Still open (outside the console; verified in code on 2026-09-26) | Observation 1 settings the console cannot show (owner decision); 2 invitation e-mail English only; 3 member list ignores `valid_from`/`valid_until`; 4 search has no paging — `REMAINING_WORK.md` R-08 |
| Not tested | real devices, Safari/Firefox, screen readers (NVDA/VoiceOver) — never run in any session |

Counts: P1 6/6 fixed, P2 16/16 fixed, P3 17/18 fixed (M-14 deferred).

## 8. Production-readiness gates (none passed)

From the blueprint (§38, go/no-go) and the package acceptance criteria: reproducible release
artifacts and manifest (WP01-T04/T06); credential containment and deployment security (WP02);
measured Persian speech quality on held-out data (F27, WP09/WP13); speaker/role attribution
(WP09/WP10); QA scoring with evidence and calibration (WP11/WP14); review workflow; correct billing
(WP18); private/offline qualification with egress denied (WP19, WP08-T06 production part); cloud
qualification; observability/backup/restore on the supported topology (WP20); security retest
(WP23); truthful website claims (WP21); operator documentation (WP22); customer acceptance. Keep
**NOT PRODUCTION READY** until every one of these has recorded evidence.

## 9. External blockers and owner decisions

External: Padyar AI Gateway API contract; representative evaluation data with rights records;
provider credentials; a live S3-compatible bucket; SMTP relay, Google OAuth client and Asanak
account for live sign-in tests; production topology (cloud firewall, on-prem mail relay).

Owner: pricing statement (website vs console); retention defaults and backup retention;
empty-transcript charging (today the reservation is committed, ADR 0003); G5; M-14; Persian
console font; exposing workspace settings in the console; `Kohandezh/HomaYar` archive decision;
revoking `PRODUCT_SOURCE_TOKEN` and rotating legacy credentials (WP02-T01); review of the
unpublished website overlay in the original checkout (R-03).

## 10. The original workspace checkout — safe future migration (not executed)

`/Users/emperor/Documents/AI/Homayar.com` is the owner's original checkout: website `main` at
`8f4ba6a` (old) with **80 uncommitted entries** (the unpublished website overlay, R-03), and
`Homayar/` — a separate clone of `Kohandezh/HomaYar` on `dev` `605cb07` with **98 uncommitted
entries** and its own `.git`. On current `main`, `Homayar/` is a tracked directory. Because
`/Homayar/` used to be ignored, **`git checkout`, `git merge` or `git pull` of current `main` in
that checkout would silently overwrite the clone's files.** Nothing was changed there. When the
owner decides to migrate:

1. **Stop all tools** working in that directory. Do not run `pull`, `checkout`, `reset`, `clean`
   or `stash` in the root or in `Homayar/`.
2. **Back up both levels, including uncommitted work** (outside the checkout, local only, never
   uploaded; the `.env` files may contain secrets):
   - `git -C <root>/Homayar bundle create <backup>/product-clone.bundle --all`
   - `git -C <root> bundle create <backup>/website.bundle --all`
   - `tar -C <root> -czf <backup>/original-checkout.tgz --exclude=node_modules --exclude=.venv --exclude=.next .`
     (captures uncommitted, untracked and ignored files of both levels).
3. **Do not update in place.** Clone `Kohandezh/Homayar.com` into a new directory and work there.
   The old directory stays as it is for reference.
4. **Port the uncommitted work deliberately**, on branches of the new clone:
   - website overlay (root): compare with `main`, review with the owner (R-03), port the wanted
     files;
   - product clone changes: diff the clone's working tree against the tracked `Homayar/` of `main`
     (`diff -r` excluding `.git`, `.venv`, `node_modules`) and port only what is not already there;
     much of it was imported in WP01-T03, but that has not been proven file by file.
5. **Only then** retire the old directory (owner decision; keep the backups).
6. If an in-place update is unavoidable: move `Homayar/` aside first
   (`mv Homayar Homayar.pre-adr0033`), commit or bundle the root's changes on a local branch, then
   update with `git checkout --no-overwrite-ignore` / `git merge --no-overwrite-ignore`, which
   refuse instead of overwriting ignored files.

## 11. Graphify

Code map refreshed once on 2026-09-26 at `2a30213` (tree identical to `main` `edfcb78`):
440 files, 5,480 nodes, 20,858 edges, 181 communities; lossless extraction 5,482 nodes / 22,719
relations; 163 explicitly unresolved references (`docs/handoff/claude-fable-5.1/graphify/code/UNRESOLVED_REFERENCES.json`);
diagnostics 0 missing endpoints, 1,224 dangling, 1 self-loop. Sequential local AST extraction
(graphify 0.9.40, `tools/refresh_graph.py` through a wrapper forcing `parallel=False`), no model or
API call, source hashes 440/440 equal to the committed blobs. **HTML rendering was not inspected**
(no HTML is generated above 5,000 nodes; `VISUAL_CHECK.json` records `not_run`). The planning graph
(2026-09-07) was not rebuilt (no planning relationship changed). Guide:
`docs/handoff/claude-fable-5.1/GRAPHIFY_GUIDE.md`. Static edges are navigation aids, not runtime proof.

## 12. Known unverified behavior

- Anything involving real models, providers, e-mail/SMS services, S3, a deployed topology, TLS,
  backups/restore on real infrastructure, load, or GPU workers.
- Media validation in CI (35 ffmpeg tests skipped on the runner, R-02).
- The website's own behavior beyond lint and build (no tests in the repository).
- Browser coverage beyond Chromium in the built-in browser pane.
- One console test run with 1/113 failing (name not captured, not reproduced; `REMAINING_WORK.md`
  "Known uncertainties").
- Whether every uncommitted change in the original checkout is represented in the final tree (§10).

## 13. Estimate (judgment, not a measurement)

Roughly a quarter to a third of the v1 engineering scope, **as an estimate**. Basis: the
platform foundations that everything depends on (tenancy, durable processing, privacy, routing,
CI) are implemented and tested in local scope, and a platform-level customer journey runs end to
end; the product's value chain (real speech, attribution, QA scoring, review, billing, offline
package) and every production gate are not started or blocked on external inputs, and those are
the larger and riskier part of the plan. This figure is not derived from commit or test counts.
