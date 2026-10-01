# Backend plan: mission 20260929-backend-mode-100 (planner turn 1)

- **Base:** `682ffee` (branch `claude/inspiring-tesla-ie06gi` = `origin/main` `2e834ed` + one skill-only commit). **Alembic head:** `a1c3e5f7b9d2`.
- **Companion:** `coverage-ledger.json` has 222 capability rows (F01–F95, the 62 §6 rows, GR01–GR40, RG01–RG10, N01–N15), all 161 backlog subtasks with a verified status, and the 160-item queue in machine-readable form. D01–D18 are listed under `owner_decisions`.
- **Status meaning:** VALIDATED means *local scope*. The module is present, wired into `main.py` / routers / Celery, and has tests, and historical passing evidence exists at `fd2d298` (CI run 36294427694, 716 passed, 46 skipped). Under GR34 each such row still has to be proven again on `682ffee` by REC-01.
- **Scope:** backend only. Frontend UI, the website (WP21), docs work and UX studies are marked `backend_relevant:false`, with the reason.

| Kind | VALIDATED | PARTIAL | PLANNED | BLOCKED_EXTERNAL | DEFERRED | SUPERSEDED | EXCLUDED | Total |
|---|---|---|---|---|---|---|---|---|
| F01–F95 | 9 | 28 | 27 | 1 | 28 | 1 | 1 | 95 |
| §6 V/P/A/C/G/K/X | 0 | 18 | 40 | 4 | 0 | 0 | 0 | 62 |
| GR01–GR40 | 5 | 24 | 11 | 0 | 0 | 0 | 0 | 40 |
| RG01–RG10 | 0 | 7 | 1 | 2 | 0 | 0 | 0 | 10 |
| N01–N15 | 0 | 9 | 6 | 0 | 0 | 0 | 0 | 15 |
| **All** | **14** | **86** | **85** | **7** | **28** | **1** | **1** | **222** |

- **Backlog subtasks, verified (161):** 42 VALIDATED, 20 PARTIAL, 97 PLANNED, 2 BLOCKED_EXTERNAL.
- **The same subtasks as the backlog records them:** 42 validated, 5 partial, 112 not_started, 2 blocked_external.
- **Rows left NEEDS_RECONCILIATION:** none. Every historical "validated" claim was confirmed in source.
- **DEFERRED:** used only for §11.2 "expansion" F-rows, citing §11.2 plus the last bullet of §11.1.
- **EXCLUDED / SUPERSEDED:** F40 is EXCLUDED and F66 is SUPERSEDED, both citing §11.1.

## 1. Current state: verified against claimed

**Verified in source at 682ffee**

- **Routers:** 16 v1 routers are mounted (`Homayar/backend/main.py:178-193`): auth, admin, ai_policy, api_keys, billing, ingestions, meetings, insights, members, organizations, privacy, shares, teams, locations, voice, voicemail.
- **Route inventory:** it is checked against `Homayar/docs/security/authorization-matrix.md`, and every route needs a policy (`tests/test_authorization_matrix.py:226`).
- **Celery:** one queue (`processing`), 8 tasks, 7 beat sweeps (`celery_app/__init__.py:15-140`, `jobs/tasks.py`).
- **Tables:** 57 ORM tables. 50 live on `database/saas_models.Base` and 7 in `voice/`, `voicemail/`, `locations/`; `database/model_registry.py` registers all of them.
- **Row-level security:** covers all workspace tables, including the P0-wave ones (`database/rls.py:60-82`, `:143`).
- **Tests:** 66 test modules and 593 top-level test functions. Parametrization expands them; the last recorded full run had 716 cases.
- **Foundations:** WP03, WP04 and WP06, plus WP05-T01..T04/T06, WP07-T01..T07 and WP08-T01..T06. All 42 validated subtasks are present, wired and tested (per-row evidence is in `work_items`).

**Discrepancies with the historical reports (evidence)**

| # | Claim (source) | Verified at 682ffee | Consequence |
|---|---|---|---|
| D-1 | Last validated main `fd2d298` (ReportAppvisionProgress.md:19; VALIDATION.md §1) | P0 wave `c4e34d0` (77 files, +12.6k lines) touched shared contracts (`rls.py`, `tenant_context.py`, `jobs/processor.py`, `providers/egress.py`, `providers/registry.py`, `config.py`, `main.py`, `celery_app`). It was merged in PR #10 (`c847f21`) with no CI record in the repository. | REC-01 must re-run the CI-equivalent suite before any VALIDATED row counts |
| D-2 | Report §A: "working branch feat/backend-p0-wave" | Already merged to main (`c847f21`). The 31 linear migrations and head `a1c3e5f7b9d2` are confirmed. | Records only |
| D-3 | Report §D: voice admission "250 → exactly 200 admitted" | Proven only on SQLite (`tests/test_voice_admission.py:38` `sqlite_session_factory`, `:223`). PostgreSQL locking is unproven. | REC-02 |
| D-4 | Report §B: Notifications "model only" | Half-wired. `voicemail/pipeline.py:252` writes `notifications` rows, but there is no read, ack or delivery path. | WP24-T02, N07 |
| D-5 | backlog: WP12-T05 `not_started` | Corrections are implemented: `services/transcript_service.py:354-420` (stale base returns 409), `routers/v1/insights.py:351,930`, `tests/test_transcript_revisions.py` | PARTIAL (reason and reviewer semantics remain) |
| D-6 | backlog: WP12-T06 `not_started` | Stale marking exists (`services/quality_service.py:189`, `tests/test_meeting_quality.py:202`). Recompute is missing. | PARTIAL |
| D-7 | backlog: WP13-T05, WP09-T02/T03/T06, WP19-T01/T02/T03 `not_started` | Library code exists but is not wired in: `speech/benchmark.py`, `speech/audio_profile.py`, `speech/languages.py`, `providers/hardware.py`, `providers/artifacts.py`, `jobs/model_preflight.py --offline` | PARTIAL |
| D-8 | backlog: WP18-T02, WP20-T02/T05/T06, WP24-T01/T05 `not_started` | Foundations exist: the usage ledger (WP06-T06), `audit_service`, `infra/scripts/backup-*.sh` + `reapply-tombstones.sh`, action items with source turns, and txt/json/srt/vtt exports | PARTIAL |
| D-9 | Report §D2: "P2/P3 expansion rows DEFERRED by §11.2" | Holds for the F-rows. Under the last bullet of §11.1, the §6 re-selections stay in vision scope, so F65→P04/P05, F89→V06, F93→C01–C05/N07, F88→A01, F53→C07, F62→X19 and F76→X21 are tracked as PLANNED §6 rows. F77 is not deferred (§11.1 lists hybrid as selected). | Ledger follows §11.1 |
| D-10 | AppVision §3 profiles S/P/H/O | Runtime only knows `deployment_mode: cloud\|onprem` (`config.py:26`). `providers/offline_profile.py:40` hard-codes profile "O". | X01 (it becomes a P0 prerequisite of N12) |
| D-11 | AppVision §7.6 retention states | `RetentionMode` has INDEFINITE/FINITE/IMMEDIATE/LEGACY_UNRESOLVED (`saas_models.py:255-270`). KEEP_FOR_NOW and RETIRED are missing, and the ADR mapping that §7.6 requires does not exist. | X13 |
| D-12 | F19 acceptance: "startup does not overwrite negotiated entitlements" | `ensure_plan_catalog` rewrites the plan name, `is_public` and entitlements on every start (`services/tenancy_service.py:79-82`, called from `main.py:95`) | WP18-T03 (stabilization) |
| D-13 | AppVision K05: "Homayar AI API Gateway" | Source calls it "Padyar AI Gateway" (`providers/padyar.py:1-21`). The naming has not been reconciled. | D07, K05 |
| D-14 | Mission baseline: "migrations exit 1", pytest mostly E | This is an environment defect (**ENV-00**). `homayar_test` has no SUPERUSER, CREATEROLE or BYPASSRLS. `c4e8b2a7d519` → `rls.py:310` runs CREATE ROLE, and `tests/_database.py:47-57` requires a superuser or BYPASSRLS inspection role. CI uses the postgres image's superuser `POSTGRES_USER` (`.github/workflows/ci.yml:144-149`). | Leader: `ALTER ROLE homayar_test SUPERUSER`, recreate the DB, rerun (REC-01) |

## 2. Alembic heads and migration-chain health

- **One head:** `a1c3e5f7b9d2` (`uv run alembic heads`).
- **Chain:** 31 revision files, strictly linear (`ada201e253df` → … → `f5b1d7c3a9e2` → `a1c3e5f7b9d2`). There are no branches or merges, and every `down_revision` resolves.
- **CI gate:** `infra/ci/check-migrations.sh` enforces a single head, runs upgrade, then `alembic check` (no drift).
- **Risk: migrations call live code.** 18 migrations call `backend.database.rls.apply_row_level_security` (for example `c4e8b2a7d519_row_level_security.py:34`, `a1c3e5f7b9d2…py:371`). That call is idempotent and only touches tables that exist (`rls.py:344-389`).
  - Consequence: an old migration's effect depends on the current `rls.py`.
  - Contract for every later change: any edit to a policy expression or to `APPEND_ONLY_TABLES` must ship a new migration that calls `apply_row_level_security`. Otherwise deployed databases never receive it; only fresh databases do.
- **Risk: role creation.** Revision `c4e8b2a7d519` needs a migration role that has CREATEROLE, or a pre-created `homayar_app`. This is ENV-00, and the production runbook needs a note.
- **Not yet proven:** a downgrade/upgrade round trip was recorded only for the last two revisions (`e7eeef5` era, VALIDATION.md §3). An upgrade from a previous release with seeded data does not exist (WP19-T07). A PostgreSQL restore drill does not exist either; the real drill runs only on SQLite (`tests/test_tombstone_reapply.py:204-209`).

## 3. Duplicated or disconnected implementations

| Area | Evidence (file:line) | Owner / fix item |
|---|---|---|
| Persian/Arabic letter and digit normalization, six copies | `speech/text_normalize.py:30,101`; `locations/textnorm.py:16,53`; `voicemail/classify.py:30,55`; `privacy/text_pii.py:53,60`; `security/normalization.py:8-9`; `routers/v1/auth.py:617` | Make `speech/text_normalize.py` the single owner (WP22-T02). Golden no-behaviour-change tests are required; the privacy detectors and the offset-preserving location stream need care. |
| Qualification state, three models | `providers/registry.py:67-70` ManifestStatus; `providers/hardware.py:178` QualificationRecord; `speech/benchmark.py:129` QualificationRecord | The registry becomes the owner (N15; K07) |
| `speech/` package not wired in | No production importer outside `speech/`: `entities.py`, `dates.py`, `languages.py`, `audio_profile.py`, `benchmark.py` | WP09-T02/T03, WP11-T03, WP13-T05 |
| Local AI runtime pieces not wired in | `providers/hardware.py`, `providers/local_llm.py` (tests only); the TTS factory `providers/factory.py:237` has no runtime consumer | WP09-T06, K03, V02 |
| Webhook tables with no code | `webhook_endpoints` and `webhook_deliveries` (`saas_models.py:2201,2214`) have RLS but no service, router or worker | WP16-T05 |
| Notifications written but never read | `saas_models.py:2262`; the only writer is `voicemail/pipeline.py:252` | WP24-T02, N07 |
| Legacy stack (~2.5k lines), unmounted | `routers/{copilot,dashboard,graph,ldap_sync,webhook}.py`; `services/{copilot,dashboard,graph,issue_aggregation,call,ad,user,pdf,redis}_service.py`; `services/llm_router.py`; `database/models.py`; `websocket/manager.py`. The webhook compares its secret with `!=` (`routers/webhook.py:46-48,107-109`). Dead flags remain in `config.py:262-270,404`. `Settings` uses `extra="ignore"` (`config.py:21`), so removing fields is safe. | N11 |
| Two copies of the LLM path | `providers/` (canonical) and `services/llm_router.py` (legacy) | N11 |
| Two copies of ADRs 0026–0031 | `Homayar/docs/handoff/session-a/adr/0026…0031` differ from `docs/architecture/decisions/0026…0031` (drafts) | N11: supersession note only |
| Phone normalization | Converged on `security/normalization.normalize_iranian_mobile`, which `voicemail/ingest.py:91` reuses. The exception is `privacy/text_pii.py:162` `_canonical_phone`. | Keep separate (detector), document it |
| Quality signals | `services/quality_service.py` (stage and coverage) is wired; `speech/audio_profile.estimate_quality` is not | N04 |

## 4. Shared contracts and their single owner

The P0-wave rule "integrator-exclusive" (ReportAppvisionProgress §E) is kept. Each team edits these files only through its own queue item, in series.

- **Schema:** `database/saas_models.py` holds `Base` plus the core tables. Domain packages add tables on the same `Base` and must be imported in `database/model_registry.py`. Migrations under `migrations/versions/` are append-only and written by one integrator.
- **Tenancy/RLS:** `database/rls.py` is the only source of policy DDL, used by both migrations and the test schema. `WORKSPACE_TABLES`/`POLICIES`/`APPEND_ONLY_TABLES` must list every new table. `database/tenant_context.py` is the only place context gets bound.
- **Authorization:** `security/authorization.py` (Permission, ROLE_PERMISSIONS, GRANTABLE_ROLES) must stay in step with `Homayar/docs/security/authorization-matrix.md`. `test_authorization_matrix.py` enforces that; every new route needs an entry.
- **Provider registry:** `providers/registry.py` + `providers/manifests/builtin.json` (manifests and preflight), `providers/policy.py` (AI policy), `providers/routing.py` (eligibility), `providers/resilience.py`, `providers/egress.py`, `providers/factory.py` (construction). `providers/padyar.py` stays a boundary only.
- **Config:** `config.py` `Settings` is the single object, with production validators at `config.py:389-405`.
- **Error envelope:** `main.py` `_error_payload` (`code`, `message`, `request_id`, `details[field]`). N13/WP22-T01 add localization keyed by `code`.
- **Durable execution:** `celery_app/__init__.py` (queue and beat), `jobs/processor.py` (the only processing authority), `jobs/outbox.py`, `jobs/leases.py`, `jobs/checkpoints.py`, `jobs/failures.py`, `jobs/replay.py`. New async work reuses the outbox and lease pattern. It does not get a second orchestrator (§10.7).
- **Usage ledger:** `services/entitlement_service.py` with `usage_reservations`/`usage_events`. N08, WP18 and X11 extend it and never fork it.
- **Audit:** `services/audit_service.record_audit_event`.
- **Ingestion:** `services/ingestion_service.py`. Uploads, voicemail, PBX and N02 imports all go through it.
- **Storage:** `storage/base.py` contract plus `factory.py`.
- **Media validation:** `media/validation.py`.
- **Text normalization:** `speech/text_normalize.py`, to become canonical through WP22-T02.

## 5. Dependency graph (who blocks whom)

REC-01 blocks all 159 other items. After it, the heaviest blockers (the count is transitive dependents) are:

- WP22-T02: 36
- WP11-T04: 31
- WP16-T05: 28
- WP24-T03: 23
- WP11-T05: 23
- WP11-T06: 22
- WP09-T01: 21
- WP11-T02: 20
- X01: 18
- WP24-T04: 17
- WP18-T03: 16
- WP24-T02: 16
- WP15-T01: 13
- WP18-T02: 12
- WP15-T02: 12
- WP14-T01: 11

Main chains (A → B means B needs A):

- **Stabilization:** REC-01 → {N11, WP02-T03, REC-02, WP22-T02, WP18-T03}
  - WP02-T03 → {K01, WP02-T05, WP02-T06}
  - REC-02 → V02
  - WP22-T02 → {WP11-T03, WP11-T05, WP17-T02}
  - WP18-T03 → {WP18-T04, WP19-T04, WP22-T04, N01}
- **Trust:** X01 → X02 → {X06, WP19-T04}
  - X01 → {N12, N14, N10, K03, K06, WP19-T01, X08}
  - WP24-T03 (approvals, G04/GR05) → {WP24-T04, G03, G05, C05, N05}
  - G01 → {G03, K02}
  - N03 → {V02, V03, N06}
- **Durable effects:** WP16-T05 (signed delivery ledger) → {WP16-T06, WP24-T04, N07, WP22-T04, WP16-T07}
  - WP24-T04 → {X05, A01, A03, A04, A06, C01–C04, C06, V03, WP24-T07}
- **Speech pipeline:** WP09-T01 → WP09-T02 → {WP10-T01, N04, WP11-T01, WP09-T04} and WP09-T01 → WP09-T03 → WP09-T05 → WP09-T06 → WP19-T03
  - WP10-T01 → WP10-T02 → WP10-T04 → {WP10-T05, WP10-T06}
  - WP09-T01 → {WP16-T03 → P01 → P02/P03/V04, N02}
- **Quality:** WP11-T04 → WP11-T05 → WP11-T06 → {WP14-T01 → WP14-T02 → WP14-T03 → {WP14-T04/T05/T06, N06}, WP15-T02, WP12-T02}
  - WP11-T02 → {WP11-T03 → WP12-T04 → {WP12-T06 → N05, WP24-T01}, WP15-T01}
- **Evaluation and models:** WP13-T05 → WP13-T06 → WP13-T07 → N15 → {N05, X24}; WP13-T07 → N06
- **Commercial:** WP18-T02 → {N08 → {V02, X11}, WP18-T04} → WP18-T05 → WP18-T06 → WP18-T07; WP19-T04 → N09 → X16
- **Operations:** WP20-T05 → WP20-T06 → {N12 → WP20-T07, WP19-T05}; WP20-T01/T02 → X15 → {N09, WP19-T05, WP20-T07, WP02-T04}
- **Retrieval:** WP22-T02 → WP17-T02 → WP17-T03 → {V02, WP17-T04}; WP15-T02 → WP17-T01 → WP17-T05 → {WP17-T06, V06, X04}
- **Integration:** WP24-T02 → {N07 → {A02, C01–C04, C06, X19, V03}, WP14-T05/T06, WP18-T04}; X06 → N01 → X07

## 6. The queue

**Ordering rule.** Topological over prerequisites, with each prerequisite inheriting the most urgent priority of anything that depends on it. Ties are broken in this order:

1. Verified stabilization or security flag (S).
2. Effective priority (`P` column; `P1>P0` means the item inherited P0).
3. Dependency group (`G`): 0 reconcile, 1 trust, 2 durable, 3 AI/voice, 4 lifecycle, 5 commercial, 6 operations, 7 integration, 8 hardening.
4. Stable ID.

**Column key.**

- `Cloud` answers whether the item is fully source-implementable in a container with PostgreSQL 16, Redis and ffmpeg but no GPU, PBX, real providers or S3: `yes`, or `partial` (local code plus fakes, with qualification external).
- `b/` stands for `Homayar/backend/`.
- Items that have no local source work at all are in §8, not in the queue. The queue holds 160 items: 116 `yes` and 44 `partial`.

| # | ID | Scope | Prereqs | Expected files | External need | Cloud | P | G | Stab |
|---|---|---|---|---|---|---|---|---|---|
| 0 | REC-01 | LEADER: CI-parity revalidation of 682ffee (make homayar_test SUPERUSER like CI POSTGRES_USER, then check-migrations.sh + full pytest -rs); demote any failing VALIDATED row | - | infra/ci/check-migrations.sh; b/tests/ | - | yes | P0 | 0 | S |
| 1 | N11 | (-> F01) Legacy inventory + forbidden-path guard + removal of unmounted legacy routers/services/config flags | REC-01 | b/routers/{copilot,dashboard,graph,ldap_sync,webhook}.py; b/services/{copilot,dashboard,graph,issue_aggregation,call,ad,user,pdf,redis}_service.py; b/services/llm_router.py; b/database/models.py | - | yes | P0 | 0 | S |
| 2 | WP02-T03 | Secret references (*_FILE/secret-store refs) and mandatory secure production settings with tests | REC-01 | b/config.py; infra/compose/compose.onprem.yaml; infra/compose/compose.cloud.yaml; Homayar/.env.example | - | yes | P0 | 1 | S |
| 3 | REC-02 | Prove voice admission invariants on PostgreSQL (250->200 exact, per-node limit, fencing) - today SQLite-only (tests/test_voice_admission.py:38) | REC-01 | b/tests/test_voice_admission.py; b/voice/loadtest.py; b/voice/admission.py | - | yes | P1 | 0 | S |
| 4 | WP22-T02 | Single normalization owner (speech/text_normalize.py) + locale-aware dates/numbers; migrate duplicates with golden no-behaviour-change tests; raw evidence untouched | REC-01 | b/speech/text_normalize.py; b/locations/textnorm.py; b/voicemail/classify.py; b/privacy/text_pii.py | - | yes | P1 | 0 | S |
| 5 | WP18-T03 | Versioned plans with real entitlements; stop startup overwrite of tenant commercial configuration | REC-01 | b/services/tenancy_service.py; b/database/saas_models.py; b/migrations/versions/; b/main.py | - | yes | P1 | 5 | S |
| 6 | WP01-T04 | Reproducible API/worker image builds from clean checkout with locked deps and recorded digests | REC-01 | infra/docker/api.Dockerfile; .github/workflows/ci.yml; Homayar/uv.lock; infra/ci/ | Docker daemon/buildx (and a registry to publish digests) | partial | P0 | 0 |  |
| 7 | WP01-T06 | Release manifest: source SHA, Alembic head, model-manifest digests, image digests, linked validation results | WP01-T04 | infra/ci/release-manifest.py (new); b/jobs/model_preflight.py; .github/workflows/ci.yml | Image digests need WP01-T04 builds | yes | P0 | 0 |  |
| 8 | N03 | Consent policy engine: versioned jurisdiction policies, receipts, withdrawal, enforcement at ingest/process/export/outbound | REC-01 | b/(new consent module); b/routers/v1/meetings.py; b/routers/v1/voicemail.py; b/jobs/processor.py | Legal content of jurisdiction rules (engineering defaults allowed) | yes | P0 | 1 |  |
| 9 | X01 | Capability catalog with stable IDs, dependencies, availability per deployment profile S/P/H/O (replace cloud\|onprem) | REC-01 | b/config.py; b/(new capability registry); b/providers/offline_profile.py | - | yes | P1>P0 | 1 |  |
| 10 | WP13-T02 | Local part: machine-readable annotation/label schema consumed by evaluators | REC-01 | b/speech/benchmark.py; docs/planning/work-packages/WP13/ | Annotators/linguists to finalize guidelines | partial | P0 | 3 |  |
| 11 | WP13-T04 | Local part: split manifests + speaker/customer/time leakage checker | WP13-T02 | b/speech/benchmark.py (or new eval package) | Annotated data for real splits | partial | P0 | 3 |  |
| 12 | WP13-T05 | Evaluators: consolidate WER/CER, add DER/JER, entity/intent, QA agreement, factual-support, retrieval metrics | REC-01 | b/speech/benchmark.py; b/tests/test_speech_benchmark.py | - | yes | P0 | 3 |  |
| 13 | WP02-T05 | Local part: TLS/ingress templates, DB/object/backup encryption configuration and key-rotation drill on local Compose | WP02-T03 | infra/nginx/; infra/compose/; infra/scripts/ | Real ingress, certificates, KMS, production topology | partial | P0 | 6 |  |
| 14 | WP20-T01 | Correlation IDs through jobs + stage latency/error/queue/usage-cost metrics with redaction | REC-01 | b/main.py; b/jobs/processor.py; b/services/logging_service.py | - | yes | P1>P0 | 6 |  |
| 15 | WP20-T02 | Append-only enforcement (add audit_events to rls.APPEND_ONLY_TABLES + migration) + audit coverage inventory test for privileged actions | REC-01 | b/services/audit_service.py; b/database/rls.py; b/migrations/versions/ | - | yes | P1>P0 | 6 |  |
| 16 | WP20-T05 | Consistent encrypted backups: DB + object manifest + key/config refs + deletion state | REC-01 | infra/scripts/; b/services/tombstone_journal.py | - | yes | P0 | 6 |  |
| 17 | WP20-T06 | Isolated PostgreSQL restore, tombstone reapply before traffic, DB/object integrity + access verification | WP20-T05 | infra/scripts/; b/jobs/tombstone_reapply.py; b/tests/test_tombstone_reapply.py | - | yes | P0 | 6 |  |
| 18 | N12 | (-> F79) Versioned RPO/RTO per profile, backup scope manifest, restore verification incl. isolation/legal hold | WP20-T06, X01 | infra/scripts/; b/jobs/tombstone_reapply.py; b/(dr objectives registry) | Owner decision D03 | yes | P0 | 6 |  |
| 19 | X15 | Diagnostic bundle: preview, redaction, encryption, checksum, consent, offline export | WP20-T01, WP20-T02 | b/(support bundle module); b/routers/v1/admin.py | - | yes | P1>P0 | 6 |  |
| 20 | WP20-T07 | Local part: RPO/RTO measurement on local topology + incident/support drills with leakage checks | N12, X15 | infra/scripts/; b/tests/ | Supported production topology | partial | P0 | 6 |  |
| 21 | WP02-T04 | Secret scanning of release artifacts, image layers and support bundles | WP01-T04, X15 | .github/workflows/ci.yml; infra/ci/ | Docker for image-layer scans; scanner images | partial | P0 | 8 |  |
| 22 | WP02-T06 | Invalid production configuration + log redaction tests (local); exposed ports/cert renewal on topology later | WP02-T03 | b/tests/; b/services/logging_service.py; infra/ci/ | Supported topology for port/cert checks | partial | P0 | 8 |  |
| 23 | G01 | Field-level access policy across API/export/search/share | REC-01 | b/security/context.py; b/security/authorization.py; b/services/export_service.py | - | yes | P1 | 1 |  |
| 24 | WP16-T02 | Residual: key rotation (overlap window) and per-key usage metering | REC-01 | b/services/api_key_service.py; b/routers/v1/api_keys.py; b/database/saas_models.py | - | yes | P1 | 1 |  |
| 25 | WP23-T01 | OIDC federation (+SAML via broker) login/logout/linking | REC-01 | b/routers/v1/auth.py; b/services/auth_service.py; b/config.py | Customer IdP; local Keycloak container for development | partial | P1 | 1 |  |
| 26 | WP23-T02 | Group->membership/role mapping and deprovisioning (no SCIM pretence) | WP23-T01 | b/services/auth_service.py; b/services/member_service.py | - | yes | P1 | 1 |  |
| 27 | WP23-T03 | Privileged MFA, disabled users, revoked sessions, group removal, break-glass | REC-01 | b/services/auth_service.py; b/security/tokens.py; b/migrations/versions/ | - | yes | P1 | 1 |  |
| 28 | WP23-T04 | Dependency/container/SBOM/secret scans with triage | WP01-T04 | .github/workflows/ci.yml; infra/ci/ | Scanner vulnerability DBs (network) and Docker | partial | P1 | 1 |  |
| 29 | WP24-T03 | Preview/approve/reject workflow with immutable binding to action/recipient/payload/cost (G04, GR05) | REC-01 | b/database/saas_models.py; b/services/ (approvals); b/routers/v1/ (approvals); b/security/authorization.py | - | yes | P1 | 1 |  |
| 30 | X02 | Module enable/disable enforced in API and workers, dependency preview, drain, data preservation (GR01/GR33) | X01 | b/security/authorization.py; b/jobs/processor.py; b/(capability registry) | - | yes | P1 | 1 |  |
| 31 | WP16-T05 | Outbound webhook endpoints + delivery ledger via outbox; HMAC over raw body with timestamp and key id | REC-01 | b/database/saas_models.py; b/services/ (webhooks); b/jobs/outbox.py; b/celery_app/__init__.py | - | yes | P1 | 2 |  |
| 32 | WP16-T06 | Retry/dead-letter/admin diagnostics without credentials or transcript text | WP16-T05 | b/services/ (webhooks); b/routers/v1/admin.py | - | yes | P1 | 2 |  |
| 33 | WP24-T04 | Execute approved actions via scoped connectors: idempotency, recheck authorization, retry, cancel, delivery/acceptance/completion states | WP24-T03, WP16-T05 | b/services/ (actions); b/jobs/outbox.py; b/celery_app/__init__.py | Real connectors (Odoo etc.) for qualification | yes | P1 | 2 |  |
| 34 | WP24-T07 | Tests: rejected/revoked/duplicate approvals, stale revisions, connector failure without duplicate effects | WP24-T04 | b/tests/ | - | yes | P1 | 2 |  |
| 35 | WP09-T01 | Channel metadata/layout, silent/duplicated channel flags, per-source trusted channel->role mapping, attribution state, migration | REC-01 | b/media/validation.py; b/database/saas_models.py; b/database/rls.py; b/security/authorization.py | - | yes | P1 | 3 |  |
| 36 | WP09-T02 | Channel-preserving normalized derivatives, VAD intervals, reversible original-time mapping; wire speech/audio_profile.py into processing | WP09-T01 | b/speech/audio_profile.py; b/jobs/processor.py; b/jobs/checkpoints.py | Silero VAD (or equivalent) weights for model VAD; energy heuristic must be labeled as such | partial | P1 | 3 |  |
| 37 | N04 | (-> F26) Quality history/thresholds/per-tenant aggregation; audio metrics wired | WP09-T02 | b/services/quality_service.py; b/speech/audio_profile.py; b/migrations/versions/ | - | yes | P1 | 3 |  |
| 38 | WP09-T03 | Whisper model profiles + language policy via manifests; comparison harness | WP09-T01 | b/providers/whisper.py; b/speech/languages.py; b/providers/manifests/builtin.json | Model weights, GPU and held-out data for the comparison | partial | P1 | 3 |  |
| 39 | WP09-T04 | Long-call chunking with checkpoints, overlap dedup, no-speech handling (mock ASR + synthetic audio) | WP09-T02, WP09-T03 | b/jobs/processor.py; b/jobs/checkpoints.py | - | yes | P1 | 3 |  |
| 40 | WP09-T05 | Persistent warmed ASR worker lifecycle, memory-aware concurrency, bounded batching | WP09-T03 | b/providers/whisper.py; b/celery_app/__init__.py; b/jobs/tasks.py | GPU for memory/latency measurement | partial | P1 | 3 |  |
| 41 | WP09-T06 | CPU/GPU readiness preflight and routing only to qualified workers | WP09-T05 | b/providers/hardware.py; b/jobs/model_preflight.py; b/celery_app/__init__.py | GPU hosts for real routing tests | partial | P1 | 3 |  |
| 42 | WP10-T01 | Real mono diarization adapter behind the typed contract (pinned community-1 or better) | WP09-T02 | b/providers/diarization.py; b/providers/factory.py; b/providers/manifests/builtin.json | pyannote community-1 gated weights (HF access conditions), CPU/GPU | partial | P1 | 3 |  |
| 43 | WP10-T02 | Store anonymous speaker clusters and overlap intervals separately from display turns (schema + API) | WP10-T01 | b/database/saas_models.py; b/migrations/versions/; b/services/transcript_service.py | - | yes | P1 | 3 |  |
| 44 | WP10-T03 | Reconcile speaker clusters across long-call chunks; constrain counts only with evidence | WP10-T01, WP09-T04 | b/jobs/processor.py | - | yes | P1 | 3 |  |
| 45 | WP10-T04 | Align words/turns to speaker intervals and original offsets; keep ambiguous/unknown | WP10-T02, WP09-T02 | b/services/transcript_service.py; b/services/evidence_service.py | - | yes | P1 | 3 |  |
| 46 | WP10-T05 | Calibration framework + calibrated/uncalibrated labels (fitting needs held-out data) | WP10-T04, WP13-T05 | b/providers/schemas.py; b/services/quality_service.py | Held-out annotations | partial | P1 | 3 |  |
| 47 | WP10-T06 | Timeline API for overlapping speech and correction-ready turn attribution (backend part) | WP10-T02, WP10-T04 | b/routers/v1/insights.py; b/schemas/insights.py | - | yes | P1 | 3 |  |
| 48 | WP11-T01 | Deterministic speech/silence/talk-ratio/overlap/rate metrics from intervals with defined denominators | WP09-T02, WP10-T02 | b/services/quality_service.py; b/services/ (new metrics module) | - | yes | P1 | 3 |  |
| 49 | WP11-T02 | Intent/topic/entity/outcome taxonomy + schema with evidence and unknown values | REC-01 | b/providers/schemas.py; b/database/saas_models.py; b/migrations/versions/ | Owner taxonomy content (engineering default allowed) | yes | P1 | 3 |  |
| 50 | WP11-T03 | Structured extraction validation, abstention, critical-entity review routing (wire speech/entities.py) | WP11-T02, WP22-T02 | b/speech/entities.py; b/jobs/processor.py; b/providers/openai_compatible.py | Qualified model for quality evidence | yes | P1 | 3 |  |
| 51 | WP11-T04 | Versioned scorecards: weights, applicability, thresholds, pass/fail/NA/insufficient evidence | REC-01 | b/database/saas_models.py; b/migrations/versions/; b/services/ (new scorecard service); b/routers/v1/ | - | yes | P1 | 3 |  |
| 52 | WP11-T05 | Deterministic keyword/script checks; contextual criteria via qualified model with review routing | WP11-T04, WP22-T02 | b/services/ (scorecard evaluation); b/speech/text_normalize.py | Qualified model for contextual criteria | partial | P1 | 3 |  |
| 53 | WP11-T06 | Persist criterion evidence, calculation version, denominator; forbid inferred NPS/churn | WP11-T04, WP11-T05 | b/database/saas_models.py; b/migrations/versions/ | - | yes | P1 | 3 |  |
| 54 | WP11-T07 | Regression fixtures (negation, missing speakers, low ASR confidence, contradictions); human-QA comparison when data exists | WP11-T06 | b/tests/ | Adjudicated human QA set | partial | P1 | 3 |  |
| 55 | WP13-T06 | Slice report generator: sample sizes, uncertainty, abstention, coverage, latency, cost | WP13-T05 | b/speech/benchmark.py | Real data for real results | yes | P1 | 3 |  |
| 56 | WP13-T07 | Promotion gate on frozen criteria + rollback manifest; separate production-correction collection (anchor for N15/N06) | WP13-T06 | b/providers/registry.py; b/jobs/model_preflight.py; b/services/model_registry_service.py | - | yes | P1 | 3 |  |
| 57 | N15 | (-> F20) Active-version pointers, staged promotion, rollback, lineage query; unify qualification records | WP13-T07 | b/providers/registry.py; b/providers/hardware.py; b/speech/benchmark.py; b/services/model_registry_service.py | - | yes | P1 | 3 |  |
| 58 | WP12-T01 | Compatible Conversation model/API facade over meetings without losing IDs | REC-01 | b/database/saas_models.py; b/routers/v1/meetings.py; b/schemas/meetings.py | - | yes | P1 | 4 |  |
| 59 | WP12-T04 | Summary/decision/key-moment fields with evidence and abstention (mock provider; quality external) | WP11-T02, WP11-T03 | b/providers/schemas.py; b/jobs/processor.py; b/database/saas_models.py | Qualified model + audited claim set | yes | P1 | 4 |  |
| 60 | WP24-T01 | Commitments vs proposed next steps with evidence; unset missing owner/date | WP11-T03, WP12-T04 | b/providers/schemas.py; b/jobs/processor.py; b/database/saas_models.py | Held-out examples for precision gate | yes | P1 | 3 |  |
| 61 | WP12-T05 | Residual: mandatory reason/reason code, reviewer identity semantics, turn split/merge rules | REC-01 | b/services/transcript_service.py; b/routers/v1/insights.py; b/schemas/insights.py | - | yes | P1 | 4 |  |
| 62 | WP12-T06 | Selective recompute of dependent summaries/scores/search projections against the corrected revision | WP12-T05, WP12-T04 | b/jobs/processor.py; b/services/transcript_service.py; b/jobs/outbox.py | - | yes | P1 | 4 |  |
| 63 | N05 | Reprocessing workflow: version selection, estimate, approval, idempotent submit, compare, accept/reject, rollback | WP12-T06, N15, WP24-T03 | b/jobs/replay.py; b/jobs/processor.py; b/(new reprocessing service/router) | - | yes | P1 | 2 |  |
| 64 | WP24-T05 | CSV/PDF report exports with version/provenance and ACL | REC-01 | b/services/export_service.py; b/routers/v1/insights.py | - | yes | P1 | 4 |  |
| 65 | WP24-T06 | Persian shaping/fonts, CSV formula-injection neutralization, export limits, retention, authorized downloads | WP24-T05 | b/services/export_service.py | Vendored Persian font (Vazirmatn OFL) | partial | P1 | 4 |  |
| 66 | X13 | KEEP_FOR_NOW and RETIRED retention states with ADR mapping to existing enums (§7.6) | REC-01 | b/database/saas_models.py; b/services/retention_policy.py; b/migrations/versions/; docs/architecture/decisions/ | Owner decision D02 | yes | P1 | 4 |  |
| 67 | WP18-T01 | Versioned billable-completion/partial/rounding/reanalysis/overage/refund rule engine (X12) | REC-01 | b/services/entitlement_service.py; b/services/billing_service.py; b/database/saas_models.py | Owner rule values D04/R-17 | yes | P1 | 5 |  |
| 68 | WP18-T02 | Residual: PostgreSQL 100-concurrent reservation/completion/cancel acceptance; idempotent stage completion | REC-01 | b/services/entitlement_service.py; b/tests/test_entitlements.py | - | yes | P1 | 5 |  |
| 69 | N08 | (-> F18) Provider/model/workflow budgets with reservation before provider calls, hard stop, override | WP18-T02 | b/services/entitlement_service.py; b/jobs/processor.py; b/providers/resilience.py | - | yes | P1 | 5 |  |
| 70 | WP19-T01 | Local part: supported topology/capacity profile schema tied to deployment profiles | X01 | b/providers/hardware.py | Hardware + owner topology decisions | partial | P1 | 6 |  |
| 71 | WP19-T02 | Signed offline bundle manifest (images/models, pinned hashes, licenses) | WP01-T06 | b/providers/artifacts.py; infra/ | Model weights and Docker images for real bundles; signing key custody | partial | P1 | 6 |  |
| 72 | WP19-T03 | Extend preflight: driver/VRAM/disk/ports/TLS, local model readiness, no-internet config | WP09-T06, WP19-T01 | b/jobs/model_preflight.py; b/providers/offline_profile.py | GPU host for VRAM checks | partial | P1 | 6 |  |
| 73 | WP19-T04 | Signed offline entitlements (Ed25519), expiry/grace/read-export, operator counters (X09) | X02, WP18-T03 | b/(new licensing module); b/config.py; b/database/saas_models.py | Owner decision D10 | yes | P1 | 6 |  |
| 74 | N09 | (-> X16) Support entitlement: tier, SLA metadata, bundle eligibility, expiry/grace, enforcement | WP19-T04, X15 | b/(licensing module); b/(support bundle module) | Owner decision D13 | yes | P1 | 5 |  |
| 75 | WP19-T05 | Install/upgrade/backup/restore/rollback/support-bundle commands and runbooks | X15, WP20-T06 | infra/scripts/; b/(support bundle CLI) | - | yes | P1 | 6 |  |
| 76 | WP19-T07 | Upgrade rehearsal from prior release with seeded data + failed-migration recovery | WP01-T06 | infra/ci/; b/tests/ | - | yes | P1 | 6 |  |
| 77 | WP20-T03 | Protected audit sink with hash chain, integrity verification drill | WP20-T02 | b/services/audit_service.py; b/jobs/ | - | yes | P1 | 6 |  |
| 78 | WP20-T04 | Alerts for stopped workers, lease backlog, provider failure, purge failure, capacity | WP20-T01 | b/jobs/; b/(metrics); infra/ | - | yes | P1 | 6 |  |
| 79 | N14 | (-> F12) Versioned client support policy + read API (R-07 G3) + capability negotiation + history | X01 | b/media/validation.py; b/routers/v1/meetings.py; b/(policy document) | - | yes | P1 | 7 |  |
| 80 | WP12-T02 | Backend part: call report aggregate endpoint (summary, outcome, QA/evidence, review action) | WP12-T04, WP11-T06 | b/routers/v1/insights.py; b/schemas/insights.py | - | yes | P1 | 7 |  |
| 81 | WP12-T03 | Backend part: transcript speaker/time filters and explicit incomplete-analysis notices | WP12-T01 | b/routers/v1/insights.py | - | yes | P1 | 7 |  |
| 82 | WP14-T01 | Separate ingestion/processing/evaluation/review coverage counters; random/stratified/risk sampling | WP11-T06 | b/services/quality_service.py; b/services/ (review) | - | yes | P1 | 7 |  |
| 83 | WP14-T02 | Supervisor review queues with assignment, due time, record-level authorization | WP14-T01 | b/database/saas_models.py; b/routers/v1/ (reviews); b/security/authorization.py | - | yes | P1 | 7 |  |
| 84 | WP14-T03 | Criterion override with reason/evidence; machine and human scores kept separately | WP14-T02, WP11-T06 | b/services/ (reviews); b/migrations/versions/ | - | yes | P1 | 7 |  |
| 85 | N06 | (-> F81) Correction reason codes, dataset eligibility, versioned train/eval datasets, deletion propagation | WP13-T07, WP14-T03, N03 | b/services/transcript_service.py; b/(new dataset module) | - | yes | P1 | 4 |  |
| 86 | WP14-T04 | Blinded calibration batches + agreement reporting | WP14-T03, WP13-T05 | b/services/ (calibration) | Adjudicated examples | yes | P1 | 7 |  |
| 87 | WP15-T01 | Issue taxonomy, dedup policy, filters, minimum sample thresholds | WP11-T02 | b/services/ (trends) | - | yes | P1 | 7 |  |
| 88 | WP15-T02 | Scoped SQL aggregates for reasons/outcomes/QA/complaints | WP15-T01, WP11-T06 | b/services/ (trends); b/routers/v1/ | - | yes | P1 | 7 |  |
| 89 | WP15-T03 | Denominators, coverage, date range, scorecard version, missing-data indicators | WP15-T02, WP14-T01 | b/services/ (trends) | - | yes | P1 | 7 |  |
| 90 | WP15-T04 | Executive/analyst/supervisor/agent aggregate APIs | WP15-T03 | b/routers/v1/organizations.py; b/services/organization_service.py | - | yes | P1 | 7 |  |
| 91 | WP14-T07 | Backend part: forbid cohort/rubric mixing in comparison APIs | WP15-T04 | b/services/ (aggregates) | - | yes | P1 | 7 |  |
| 92 | WP15-T05 | Link trends to authorized conversations; correlation vs confirmed cause | WP15-T02 | b/services/ (trends) | - | yes | P1 | 7 |  |
| 93 | WP15-T06 | Reference-dataset reconciliation, filter/permission tests, measured query performance (synthetic 100k) | WP15-T04, WP15-T05 | b/tests/ | - | yes | P1 | 7 |  |
| 94 | WP16-T01 | Published versioned OpenAPI + minimal Python client/example with retries and idempotency | REC-01 | b/main.py; Homayar/docs/; Homayar/clients/python (new) | - | yes | P1 | 7 |  |
| 95 | WP16-T03 | Local part: Asterisk/FreePBX/Issabel recording+CDR import adapter against fixtures, source-ID/channel mapping | WP09-T01 | b/services/ingestion_service.py; b/(new pbx import module) | Named PBX deployment for qualification | partial | P1 | 7 |  |
| 96 | WP16-T04 | Configurable CRM/ticket/customer/agent/outcome mappings with reconciliation and late records | REC-01 | b/database/saas_models.py; b/services/ (mapping); b/migrations/versions/ | - | yes | P1 | 7 |  |
| 97 | N02 | (-> V01) Import framework: sources, mapping, preview/dry run, rejection report, resume, rollback, accounting | WP16-T04, WP09-T01 | b/services/ingestion_service.py; b/jobs/checkpoints.py; b/(new import module) | - | yes | P1 | 4 |  |
| 98 | WP16-T07 | Contract fixtures: duplicates, transfers, missing recordings, clock skew, stale signatures, revoked identities | WP16-T03, WP16-T05, WP16-T06, WP16-T02 | b/tests/ | - | yes | P1 | 7 |  |
| 99 | WP17-T01 | Query-intent contract: exact SQL aggregates vs evidence retrieval | WP15-T02 | b/services/ (copilot) | - | yes | P1 | 7 |  |
| 100 | WP17-T02 | Lexical (PostgreSQL FTS, normalized) + versioned embedding index with tenant/ACL/revision metadata; purge coverage | WP22-T02 | b/database/saas_models.py; b/migrations/versions/; b/services/conversation_purge_service.py | pgvector extension + embedding model weights | partial | P1 | 7 |  |
| 101 | WP17-T03 | Authorization before retrieval and again on evidence access; filtered recall tests | WP17-T02 | b/routers/v1/insights.py; b/security/context.py | - | yes | P1 | 7 |  |
| 102 | WP17-T04 | Bounded hybrid search/reranking with source/time/quality filters | WP17-T03 | b/services/ (search) | Reranker model | partial | P1 | 7 |  |
| 103 | WP17-T05 | Read-only allowlisted analytical tools with limits, deterministic numbers, citations | WP17-T01, WP15-T02 | b/services/ (copilot); b/routers/v1/ | Qualified LLM | partial | P1 | 7 |  |
| 104 | WP17-T06 | Abstention, unsupported-question handling, prompt-injection defenses (GR09) | WP17-T05 | b/services/ (copilot) | - | yes | P1 | 7 |  |
| 105 | WP17-T07 | Local part: adversarial tenant/team, deletion/reindex cases | WP17-T06 | b/tests/ | 100 owner-approved business questions + data | partial | P1 | 7 |  |
| 106 | WP22-T01 | Backend part (+N13): localized message catalog keyed by error code, Accept-Language/users.locale | REC-01 | b/main.py; b/(new i18n catalog); b/routers/v1/* | - | yes | P1 | 7 |  |
| 107 | N13 | (-> F72) Locale/direction metadata in API and exports, accessible export metadata, evidence records | WP22-T01, WP24-T05 | b/main.py; b/services/export_service.py | - | yes | P1 | 7 |  |
| 108 | WP22-T04 | Backend part: validated admin settings APIs for providers/scorecards/retention/quotas/integrations with secret masking | WP11-T04, WP18-T03, WP16-T05 | b/routers/v1/admin.py; b/services/admin_service.py | - | yes | P1 | 7 |  |
| 109 | WP22-T05 | Versioned settings with preview/impact, permission checks, rollback, audit (GR39) | WP22-T04 | b/services/admin_service.py; b/services/ai_policy_service.py; b/migrations/versions/ | - | yes | P1 | 7 |  |
| 110 | X06 | Governance policy profiles with versioned overrides and effective-policy explanation | X02, WP22-T05 | b/(policy profiles) | - | yes | P2>P1 | 1 |  |
| 111 | N01 | Onboarding orchestration: resumable idempotent provisioning state machine, rollback, audit | X01, X06, WP18-T03 | b/services/tenancy_service.py; b/services/member_service.py; b/(new onboarding service/router); b/migrations/versions/ | - | yes | P1 | 7 |  |
| 112 | WP24-T02 | Alert rules, dedup windows, severity, recipient scope, digest/suppression; notification read/ack API | REC-01 | b/database/saas_models.py; b/services/ (alerts); b/routers/v1/ (notifications); b/voicemail/pipeline.py | - | yes | P1 | 7 |  |
| 113 | WP18-T04 | Period rollover, subscription changes, quota alerts, concurrency admission, ledger reconciliation | WP18-T03, WP18-T02, WP24-T02 | b/services/entitlement_service.py; b/jobs/usage_reconciliation.py | - | yes | P1 | 5 |  |
| 114 | WP18-T05 | Annual/manual invoices and payment status | WP18-T01, WP18-T04 | b/services/billing_service.py; b/routers/v1/billing.py | Owner price book | yes | P1 | 5 |  |
| 115 | WP18-T06 | Authenticated/deduplicated payment events; activation separate from callback content | WP18-T05 | b/services/billing_service.py | Payment provider account/contract | partial | P1 | 5 |  |
| 116 | WP18-T07 | Reconcile usage reports/invoices with ledger; duplicate/cancel/rollover/plan-change tests | WP18-T06 | b/tests/ | - | yes | P1 | 5 |  |
| 117 | N07 | (-> F59) Preferences, quiet hours/timezone, escalation, ack, delivery attempts, fallback, unsubscribe | WP24-T02, WP16-T05 | b/services/ (notifications); b/migrations/versions/ | - | yes | P1 | 7 |  |
| 118 | WP14-T05 | Agent appeal, supervisor disposition, audit, notifications | WP14-T03, WP24-T02 | b/services/ (appeals); b/routers/v1/ | - | yes | P1 | 7 |  |
| 119 | WP14-T06 | Coaching tasks from accepted findings with owner/due/ack/follow-up | WP14-T03, WP24-T02 | b/services/ (coaching); b/routers/v1/ | - | yes | P1 | 7 |  |
| 120 | G02 | Verified channel identity binding/unlink/recovery | REC-01 | b/services/auth_service.py; b/(identity links) | - | yes | P2 | 1 |  |
| 121 | G03 | Revocable delegation grants enforced before execution (GR04) | WP24-T03, G01 | b/(delegation module) | - | yes | P2 | 1 |  |
| 122 | G05 | Multi-person approvals with distinct approvers and SoD | WP24-T03 | b/services/ (approvals) | - | yes | P2 | 1 |  |
| 123 | K01 | Org/workspace provider accounts and encrypted key references with revocation and billing attribution | WP02-T03 | b/(key store); b/providers/factory.py; b/config.py | - | yes | P2 | 1 |  |
| 124 | K02 | Personal keys constrained by org policy | K01, G01 | b/(key store) | - | yes | P2 | 1 |  |
| 125 | X05 | Workflow sandbox/test run with fixture connectors and labeled estimates | WP24-T04 | b/(workflow module) | - | yes | P2 | 2 |  |
| 126 | K03 | TTS/voice stages in AI policy; all-local profile O stage bundle | X01 | b/providers/policy.py; b/providers/factory.py; b/providers/local_tts.py | Owner decision on policy-digest change | yes | P2 | 3 |  |
| 127 | V02 | Voice runtime contract + orchestration (STT->LLM->TTS, interruption, fallback) with fake media adapter | REC-02, N03, N08, WP17-T03 | b/voice/; b/providers/local_tts.py; b/routers/v1/voice.py | SIP/WebRTC media, qualified models | partial | P2 | 3 |  |
| 128 | V03 | Outbound calls with contact policy, approval, attempt ledger, ambiguous outcomes | V02, N03, WP24-T04, N07 | b/voice/ | Carrier | partial | P2 | 3 |  |
| 129 | V05 | Browser voice via WebRTC adapter | V02 | b/voice/ | LiveKit/TURN | partial | P2 | 3 |  |
| 130 | V06 | Live suggestions channel visible only to the representative | V02, WP17-T05 | b/voice/ | Streaming speech | partial | P2 | 3 |  |
| 131 | X24 | Rights inventory for model/voice/dataset artifacts (GR32) | N15 | b/providers/registry.py; b/providers/artifacts.py | Owner license approvals D17 | yes | P2 | 3 |  |
| 132 | X10 | Plans/BYOK/wallet/credits commercial policy | WP18-T05, K01 | b/services/billing_service.py | Owner decision D04 | yes | P2 | 5 |  |
| 133 | X11 | Cost/usage reports with measured/reported/estimated separation and upstream cost vs charge | N08 | b/routers/v1/billing.py; b/services/billing_service.py | - | yes | P2 | 5 |  |
| 134 | N10 | Privacy-preserving telemetry: event schema, opt-out, deployment-mode policy, pseudonymous IDs, retention | WP20-T01, X01 | b/(new telemetry module); b/config.py | - | yes | P2 | 6 |  |
| 135 | X16 | Remote support sessions: expiring scoped access, audit, disconnect | N09, G03 | b/(support sessions) | - | yes | P2 | 6 |  |
| 136 | A01 | Calendar connector (CalDAV first) with TZ/DST, conflicts, idempotent booking | WP24-T04, G03 | b/(connectors) | Calendar server for live tests | yes | P2 | 7 |  |
| 137 | A03 | Email summary/reply drafts and approved sending with thread/recipient checks | WP24-T04 | b/services/email_service.py; b/(connectors) | Mail server for live tests | yes | P2 | 7 |  |
| 138 | A04 | Odoo activity connector with company/user mapping, one activity per duplicate delivery | WP24-T04, WP16-T04 | b/(odoo connector) | Odoo instance (local Community possible) / customer Odoo | partial | P2 | 7 |  |
| 139 | A05 | Follow-up until accepted/completed/blocked with distinct events | A04, X05 | b/(workflows) | - | yes | P2 | 7 |  |
| 140 | A06 | Scoped autonomy inside approved rules with escalation | G03, G05, WP24-T04 | b/(workflows) | - | yes | P2 | 7 |  |
| 141 | C01 | Telegram connector contract + fake (identity, replay-safe callbacks) | G02, WP24-T04, N07 | b/(channel connectors) | Telegram bot token | partial | P2 | 7 |  |
| 142 | C02 | WhatsApp Business connector contract + fake | G02, WP24-T04, N07 | b/(channel connectors) | WhatsApp Business account | partial | P2 | 7 |  |
| 143 | C03 | Bale connector contract + fake | G02, WP24-T04, N07 | b/(channel connectors) | Bale API access | partial | P2 | 7 |  |
| 144 | C04 | Eita connector for confirmed operations only | G02, WP24-T04, N07 | b/(channel connectors) | Confirmed Eita API | partial | P2 | 7 |  |
| 145 | C05 | Single-use scoped SMS/email confirmation links | WP24-T03 | b/providers/asanak.py; b/services/email_service.py | Asanak/mail relay for live tests | yes | P2 | 7 |  |
| 146 | A02 | Multi-stage reminders with stop on ack/cancel/suppression | A01, N07, C05 | b/(workflows) | - | yes | P2 | 7 |  |
| 147 | C06 | Social drafts/scheduling with approval before publish | G02, WP24-T04, N07 | b/(channel connectors) | Official social APIs | partial | P2 | 7 |  |
| 148 | C07 | Shared customer timeline with authorized identity links and dedup | G02, WP16-T04, A04 | b/(timeline) | - | yes | P2 | 7 |  |
| 149 | K06 | Provider settings API: endpoint/model/key-ref/region/budget, safe connectivity check, no secret echo | K01, X01 | b/routers/v1/ai_policy.py; b/services/ai_policy_service.py | - | yes | P2 | 7 |  |
| 150 | P01 | Asterisk ARI/AMI adapter + shared PBX contract suite with fakes | WP16-T03 | b/(pbx adapters) | Named PBX version | partial | P2 | 7 |  |
| 151 | V04 | Human transfer with permitted context | V02, P01 | b/voice/ | PBX transfer | partial | P2 | 3 |  |
| 152 | P02 | FreeSWITCH ESL adapter on the PBX contract | P01 | b/(pbx adapters) | FreeSWITCH deployment | partial | P2 | 7 |  |
| 153 | P03 | Generic SIP trunk profile (auth/transport/codec/DTMF/transfer/hangup) | P01 | b/(pbx adapters) | SIP trunk | partial | P2 | 7 |  |
| 154 | V07 | Call-flow graph schema, validation, dry run, rollback | X05, WP22-T05 | b/(call flow module) | PBX apply | partial | P2 | 7 |  |
| 155 | X03 | Configuration via manual/text/voice channels with identical authorization and §7.2 state machine | WP22-T05, X06 | b/(configuration service) | - | yes | P2 | 7 |  |
| 156 | X04 | AI-drafted configuration from qualified choices only | X03, WP17-T05 | b/(configuration service) | Qualified LLM | partial | P2 | 7 |  |
| 157 | X07 | Backend RFP/profile intake -> onboarding, delivery ledger, sales-Odoo record | N01, A03, A04 | b/(rfp intake) | Sales Odoo | partial | P2 | 7 |  |
| 158 | X08 | Selected-capability package manifest/export | X01, WP01-T06, WP19-T02 | infra/; b/(capability registry) | - | yes | P2 | 7 |  |
| 159 | X19 | Scheduled role-filtered reports | WP15-T04, N07 | b/(reports) | - | yes | P2 | 7 |  |

## 7. First eligible item and the next five

**Precondition (leader, in progress): REC-01.** Fix ENV-00 by running `ALTER ROLE homayar_test SUPERUSER`, which matches the superuser `POSTGRES_USER` that CI uses. Then run `infra/ci/check-migrations.sh backend` and `pytest backend/tests -rs` on `682ffee`. Any VALIDATED row whose tests fail gets demoted to NEEDS_RECONCILIATION in the ledger.

**First coder item: N11 (reuses F01), legacy inventory and retirement.**

Why it comes first:

- It is the only P0, group-0 item that is also a verified stabilization/security item (see the ordering rule).
- **Isolated and fully implementable here:**
  - The legacy modules are not mounted (`main.py:178-193`).
  - Their only importers are other legacy modules. `database/neo4j_connection.py` is the exception: `main.py:100,118` still uses it, so it stays.
  - `Settings` ignores unknown environment variables (`config.py:21`), so removing dead fields cannot break existing env files.
- **It removes a trap before the speech and PBX work starts.** `routers/webhook.py` looks like a PBX producer but compares a shared secret with `!=` (`:46-48,107-109`). The WP09-T01 prompt already warns agents not to reuse it.
- **Deliverables:**
  - A per-path inventory with its replacement and evidence.
  - Extending `REMOVED_MODULES` in `tests/test_truthful_outputs.py:20-29` into a forbidden-legacy-path guard.
  - Deleting the dead routers and services, plus the `enable_voip_webhook`/`voip_webhook_secret`/LDAP/`llm_model_*` settings. Check that each has no other consumer first.
  - A supersession note on the duplicate ADR drafts.
- **Acceptance:** the full suite is green on PostgreSQL as `homayar_app`, and the route inventory is unchanged.

**The next five, in order:**

1. **WP02-T03 (P0, security).** Add secret references (`*_FILE` or secret-store refs) and make secure production settings mandatory. Today secrets are raw environment strings (`config.py:83,168,252`). The production validator (`config.py:389-405`) requires neither SMTP TLS nor a PII mapping keyring; without a keyring, `MappingCipher.from_settings` returns `None` (`privacy/mapping_crypto.py:56-59`). This unblocks K01, WP02-T05 and WP02-T06.
2. **REC-02 (stabilization).** Run the voice-admission invariants (250 → 200 exact, per-node limit, fencing) on PostgreSQL. They are proven only on SQLite today (`tests/test_voice_admission.py:38`). This unblocks V02, V05 and X21.
3. **WP22-T02 (stabilization; heaviest blocker, 36 dependents).** Make one owner for Persian normalization and migrate the six copies (§3), with golden no-behaviour-change tests. Raw evidence text must stay unchanged, and the detector semantics in `privacy/text_pii.py` must be preserved.
4. **WP18-T03 (verified defect).** Stop `ensure_plan_catalog` from overwriting plans at startup (`services/tenancy_service.py:79-82`) and introduce versioned plans. It has 16 dependents, including WP19-T04, WP22-T04 and N01.
5. **WP01-T04 (P0).** Reproducible API and worker image builds with recorded digests. This needs a working Docker daemon. If the container does not have one, take WP01-T06 locally without digests and then **N03**, the P0 consent-enforcement engine, which is fully implementable here.

## 8. Items blocked externally end-to-end, and the local work still possible

| Item | External prerequisite | Local source work still possible |
|---|---|---|
| WP02-T01 (F03) | Credential owners rotate CF-01..CF-07 and revoke `PRODUCT_SOURCE_TOKEN` | None; keep Gitleaks clean |
| WP02-T02 (F03) | Owner decides on history cleanup of the legacy `Kohandezh/HomaYar` master | None (not backend) |
| WP02-T05 production part (F10) | Real ingress, certificates, KMS, topology | Queue item WP02-T05: local TLS and encryption config plus a rotation drill |
| WP05-T05 (F14) | Real S3-compatible bucket | Run the `live_s3` marker against a local SeaweedFS container and record it as non-production qualification |
| WP09-T07, WP10-T07, F27/F30/F31/F33 gates, RG03 | Held-out Persian corpus, GPU, model weights | Evaluators and reports (WP13-T05/T06); adapters behind contracts with fakes (WP09-T03, WP10-T01) |
| WP13-T01, WP13-T03 (F80) | Consented corpus with rights records; 20 h assembly | Label schema (WP13-T02) and leakage checker (WP13-T04) |
| WP13-T02 / WP13-T04 guidelines and adjudication | Annotators | Machine-readable schema and split tooling (queued) |
| WP16-T03 / P01–P03 qualification, RG07 | Named PBX/version, SIP trunk | CDR/recording importer and PBX contract suite with fixtures (queued) |
| P04, P05, P06 | 3CX, Cisco/Avaya, Teams Phone vendor licences and SDKs | Only the shared PBX contract (through P01). A vendor adapter needs the vendor API. |
| K05 | Gateway API contract (D07) | None. Keep the `provider_contract_unavailable` boundary and do not invent an API. |
| C01–C04, C06 | Telegram, WhatsApp, Bale, Eita, social platform accounts | Connector contracts with fakes, identity binding (G02), delivery ledger (queued, `partial`) |
| V02–V06 media | SIP/WebRTC media (LiveKit/Asterisk), TURN, carrier, qualified STT/LLM/TTS | Runtime contract, orchestration state and fake media adapter (queued, `partial`) |
| X21 HA | HA topology and load hardware | PostgreSQL admission proof (REC-02) |
| WP17-T07 full evaluation | 100 owner-approved questions plus data | Adversarial tenant/team and deletion/reindex tests (queued) |
| WP19-T06 | Offline install environment with local models | The CI air-gap/egress drill already exists; the preflight extension is WP19-T03 |
| WP19-T01 capacity envelope | Real hardware | Topology/profile schema (queued) |
| WP20-T07 production RPO/RTO | Supported topology | Local measurement and leakage drills (queued) |
| WP23-T05, WP23-T06 (F82) | Independent penetration tester | Scans and triage (WP23-T04) |
| Live auth integrations (F04) | SMTP relay, Google OAuth client, Asanak account | None beyond the existing explicit 503 failure classes |

The 18 owner decisions D01–D18 are listed under `owner_decisions` in the ledger, each with the items it blocks. Where a decision is still open, the engineering default goes behind a draft ADR marked "proposal", following the WP09-T01 prompt §3.

**Top risks**

1. The shared contracts were changed by the P0 wave without a recorded CI run (D-1). Until REC-01 passes on `682ffee`, no VALIDATED row is release evidence.
2. RLS is applied by migrations that call live `rls.py` code (§2). Any policy change that ships without a new migration is silently skipped on databases that are already deployed.
3. The duplicated normalizers (six copies) and three separate qualification models will diverge further as WP11, WP17 and N15 build on them. Consolidating them (WP22-T02, N15) must happen before those consumers land.
