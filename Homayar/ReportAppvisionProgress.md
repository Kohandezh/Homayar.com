# Homayar — AppVision Backend Implementation Progress

Operational index for backend work. `AppVision.md` defines intended behaviour; this file records
what the repository actually implements, with evidence. Statuses: PLANNED, IN_PROGRESS, PARTIAL,
VALIDATED, FROZEN, BLOCKED_EXTERNAL, NEEDS_RECONCILIATION, DEFERRED, SUPERSEDED, EXCLUDED.
VALIDATED requires passing evidence on the recorded SHA. Historical WP statuses come from
`docs/handoff/session-reset/PROJECT_STATE.md` and are carried as evidence leads.

## A. Current repository state

| Field | Value |
| --- | --- |
| Updated (UTC) | 2026-10-17 (integrator reconciliation after Lanes A/B and the console backend tickets; §M; infrastructure-freeze status in §N) |
| Repository | github.com/Kohandezh/Homayar.com (monorepo; product in `Homayar/`, ADR 0033) |
| Current main | `fcc4962` (merge of PR #39, the Phase 0 privacy-test fix; §M) |
| Working sessions | Integrator, plus Lane C (backup/restore, HOM-153/116/117/118) and Lane D (audit/observability/release, HOM-113/114/115/126/28/29); Lanes A and B retired (§E) |
| Migration head | `d8a2f4c6b1e3` (HOM-195 text views); 33 migrations, one Alembic head |
| Runtime | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Celery 5 + Redis |
| CI services | PostgreSQL 17, Redis 7.4, ffmpeg 6.1.1 (Ubuntu 24.04); GitHub Actions runs again since 2026-10-10 |
| Last validated main | `fcc4962`: its tree was validated by PR #39's run 39079278356 (7 of 7 checks; backend 2806 passed / 0 failed / 46 skipped; main had not moved, so the merge ref equals `fcc4962`). The push run on main is 39080861815. Last green main before Phase 0: `05c16ad` (2026-10-14). Core Baseline (local) `ecd77cc`, §K |

## B. Authoritative repository map (backend = `Homayar/backend`)

| Subsystem | Canonical path | Tables | API (`/api/v1`) | Workers | Tests | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Auth/session | `routers/v1/auth.py`, `services/auth_service.py`, `security/tokens.py` | users, auth_identities, login_sessions, one_time_tokens, oauth_challenges, otp_challenges | `/auth/*` | — | test_auth_contract | VALIDATED (WP03, local) |
| Tenants/orgs | `services/tenancy_service.py`, `organization_service.py` | organizations, workspaces, memberships, organization_memberships, organization_scope_grants | `/organizations/*`, `/members/*` | — | test_organizations, test_members_api, test_tenant_scope | VALIDATED (WP03) |
| Teams/roles | `security/authorization.py` (Permission, ROLE_PERMISSIONS), `services/team_service.py` | teams, team_assignments | `/teams/*` | — | test_authorization_matrix | VALIDATED (WP03) |
| RLS | `database/rls.py`, `database/tenant_context.py`; role `homayar_app` | all workspace tables | — | — | test_row_level_security, test_postgres_guarantees | VALIDATED (WP03) |
| Conversations | `services/meeting_service.py`, `routers/v1/meetings.py`, `insights.py` | meetings, recordings, meeting_speakers, transcript_* , analysis_runs, meeting_summaries, action_items | `/meetings/*`, `/search` | processing pipeline | test_meetings_api, test_transcript_revisions | PARTIAL |
| Media | `media/validation.py`, `services/ingestion_service.py`, `upload_service.py`, `media_lifecycle_service.py` | media_objects, ingestion_records | `/uploads`, `/ingestions` | reconcile_media_objects | test_media_*, test_uploads_api, test_ingestion_ledger | VALIDATED (WP05 local; live S3 BLOCKED_EXTERNAL) |
| Storage | `storage/{base,local,s3,factory}.py` | media_objects | — | sweep_abandoned_artifacts | test_storage_* | PARTIAL (live S3 external) |
| Processing | `jobs/{processor,leases,recovery,outbox,dispatcher,checkpoints,failures,replay}.py`, `celery_app/` | processing_jobs, job_dispatches, processing_stage_runs, stage_checkpoints | `/meetings/{id}/job`, `/admin/jobs/{id}/replay` | process_meeting_job, sweep_* | test_job_*, test_dispatch_outbox, test_stage_checkpoints, test_transport_drills | VALIDATED (WP06) |
| Evidence | `services/evidence_service.py` | evidence_spans, transcript_revisions | `/meetings/{id}/evidence` | — | test_evidence_spans | VALIDATED (WP04) |
| Models/providers | `providers/{base,schemas,factory,registry,routing,policy,resilience,egress,whisper,openai_compatible,padyar}.py` | model_manifests, provider_health, ai_policy_versions, run_policy_snapshots | `/admin/ai-policy*` | model_preflight | test_provider_*, test_model_registry, test_ai_policy, test_egress_denial | PARTIAL (WP08; Gateway contract external) |
| Privacy | `privacy/*` | transcript_redactions, redacted_segments, pii_mappings | `/privacy/subject-exports` | — | test_text_pii, test_privacy_routing | VALIDATED (WP07 local; detector `text-pii/4`, HOM-171, PR #25) |
| Retention | `services/retention_policy.py`, `conversation_purge_service.py`, `tombstone_journal.py`, `jobs/retention_purge.py` | conversation_purges | legal-hold routes | run_retention_purge | test_retention_*, test_legal_hold, test_tombstone_reapply | VALIDATED (WP07 local) |
| Usage/billing | `services/entitlement_service.py`, `jobs/usage_reconciliation.py`, `routers/v1/billing.py` | plans, plan_versions, subscriptions, entitlements, usage_reservations, usage_events, provider_usage | `/billing/*` | reconcile_usage | test_usage_ledger, test_entitlements, test_billing_api, test_plan_catalog, test_admin_seats | PARTIAL (F18 foundation; F19 versioned plans, pinned grants and owner/admin seats (§L); plan change/rollover WP18-T04 and paid onboarding N01 open) |
| Approvals/actions | `action_items` via insights router | action_items | `/meetings/{id}/actions` | — | test_insights_shares_api | PARTIAL (no approval workflow) |
| Integrations/webhooks | models only | webhook_endpoints, webhook_deliveries, api_keys | `/api-keys` | — | test_api_keys | PARTIAL (WP16-T02) |
| Odoo | none | — | — | — | — | PLANNED |
| Voice runtime | `voice/{models,admission,state,metrics,loadtest,recovery}.py`, `routers/v1/voice.py` | voice_call_sessions (tenant), voice_workspace_limits (tenant), voice_media_nodes / voice_admission_locks / voice_capacity_slots (deployment-wide) | `/voice/calls*`, `/voice/capacity`, `/voice/nodes*` (platform operator), `/voice/recover` | beat `voice-recovery` | test_voice_admission, test_voice_api | PARTIAL (control plane; no SIP/WebRTC media) |
| Voicemail | `voicemail/{models,ingest,classify,pipeline,search}.py`, `routers/v1/voicemail.py` | voicemail_messages | `/voicemails*` | `jobs/processor._run_voicemail_hook` | test_voicemail_* | PARTIAL (rule classifier; LLM classifier pluggable) |
| Locations | `locations/{gazetteer,extract,normalize,validate,routing,service,models}.py`, `routers/v1/locations.py` | service_locations | `/locations*`, `/locations/resolve`, `/locations/nearest` | — | test_locations_* | PARTIAL (approx. centroids; no geocoder) |
| Speech/language | `speech/{text_normalize,dates,entities,audio_profile,languages,benchmark}.py` | — | — | — | test_speech_*, test_text_view | PARTIAL (derived normalized view + entities wired into the processor, HOM-195, PR #23, ADR 0034; accuracy needs the corpus) |
| Workflows | none | — | — | — | — | PLANNED |
| Notifications | model only | notifications | — | — | — | PARTIAL |
| Exports | `services/export_service.py`, `privacy/subject_export.py` | — | `/meetings/{id}/export` | — | — | PARTIAL |
| Licensing/capabilities | model-license check in `providers/routing.py`; API-key capability policy | — | — | — | — | PLANNED (X01/X02/X09) |
| Support bundle | none | — | — | — | — | PLANNED (X15) |
| Observability | `services/audit_service.py` | audit_events | `/admin/audit-events` | — | — | PARTIAL (WP20 in progress: Lane D, HOM-113/114/115/126) |
| Deployment | `infra/`, `docker-compose*`, egress proxy | — | — | — | infra/egress tests, test_transport_policy | PARTIAL (production TLS on internal hops, HOM-161, PR #24; bundled-service certificates HOM-196 open) |

Legacy, unmounted: `routers/{copilot,dashboard,graph,ldap_sync,webhook}.py`, `services/llm_router.py`,
`database/models.py` (Pydantic), `websocket/manager.py` (stub). Do not extend them.

## C. Database ownership rules

All ORM models share `database/saas_models.Base`. Tenant key is `workspace_id` (plus
`organization_id` where noted); RLS policy `homayar_tenant_isolation` reads
`homayar.workspace_id` etc. set by `database/tenant_context.py`. New tables must be added to
`database/rls.py` coverage and created by one Alembic migration written by the integrator
(exclusive migration lock). Per-domain tables: see §B.

## D. AppVision backend matrix (Priority-0 subset first; remaining rows in §D2)

| ID | Capability | Canonical code | Status | Evidence SHA | Blocker / next |
| --- | --- | --- | --- | --- | --- |
| X21 / V02 / V05 | Voice control plane: capacity admission (global/node/workspace), idempotent admit, fencing, node-loss recovery, drain, metrics, load harness | `voice/` | PARTIAL | wave P0-1 | Live SIP/WebRTC media, real 200-call load and HA: BLOCKED_EXTERNAL. Local: 250 concurrent → exactly 200 admitted (SQLite; PG not measured) |
| F20/F21/F74 K01–K07 | TTS contract + Piper CLI / local HTTP adapters, local LLM templates, offline artifact checksums, profile-O evaluation, hardware profile & qualification records, preflight `--offline` | `providers/` | PARTIAL | wave P0-1 | Real model qualification on hardware BLOCKED_EXTERNAL; TTS not in AiPolicy stages (owner decision) |
| F26/F27/F29/F36/F72 | Persian normalization, Jalali dates & spoken numbers, entities, telephone audio profile + quality flags, fa/en/az registry, WER/CER + qualification gate | `speech/` | PARTIAL | wave P0-1 | Wire into processor; accuracy needs corpus (WP13 BLOCKED_EXTERNAL) |
| Owner P0 | Location extraction, normalization, nearest-branch ranking, needs_clarification | `locations/` | PARTIAL | wave P0-1 | Consolidate normalizer with `speech/`; precise gazetteer data |
| Owner P0 / V01 / F59 | Voicemail import via existing ingestion, rule classifier, priority, idempotent follow-up action/notification, search, analytics | `voicemail/` | PARTIAL | wave P0-1 | `processing` status hook; LLM classifier; rejected-media ledger reuse |

### D2. Remaining F-rows (from PROJECT_STATE WP status; NEEDS_RECONCILIATION per row until re-verified)

F01–F03 WP01/02 PARTIAL; F04–F06 VALIDATED (WP03); F12–F14 VALIDATED (WP05, live S3 external);
F15–F16 PARTIAL (WP07); F17 VALIDATED (WP06); F18 PARTIAL; F19 PARTIAL (HOM-101, HOM-167, §L); F20–F22 PARTIAL (WP08);
F23–F24 VALIDATED (WP04); F25–F28, F30–F31, F33–F38, F41–F47, F49, F57–F61, F63–F64, F68–F70,
F72, F75, F78–F79, F86, F92 PLANNED; F80–F81 BLOCKED_EXTERNAL/PARTIAL (WP13); P2/P3 expansion rows
DEFERRED by AppVision §11.2 ("not authorized by initial coding kickoff").

## E. Active work wave

**Infrastructure-freeze wave (from 2026-10-17, owner master prompt).** Lanes A and B (2026-10-10) are retired; their work merged (§M). Three sessions, with disjoint file ownership set by the integrator:

| Session | Linear (in order) | Owns | Local PostgreSQL |
| --- | --- | --- | --- |
| Integrator | Phase 0 (red main), HOM-33 → 34 → 35 → 196 after both lanes | `Homayar/ReportAppvisionProgress.md`, Linear housekeeping, merges, the integration gate | 54335 |
| Lane C, backup/restore/recovery | HOM-153 → 116 → 117 → 118 | `backend/backup/` (new), `infra/scripts/*`, `infra/compose/compose.onprem.yaml` (one `ops` backup-tool service), `docs/operations/backup-restore.md` and the DR documents, ADR 0035; no migration | 54333 |
| Lane D, audit/observability/release | HOM-113 → 114 → 115 → 126 → 28 → 29 | `backend/observability/` (new), `main.py`, `services/audit_service.py`, `services/logging_service.py`, `config.py` (additive settings), new `ci.yml` steps, `infra/docker/api.Dockerfile`, `.env.example`, `infra/observability/`, `infra/release/`, ADR 0036/0037; the single migration slot of the wave | 54334 |

Owner decisions recorded for this wave (2026-10-17):

- **Backup objectives (provisional, owner-approved infrastructure defaults):**
  - RPO 24 h, RTO 8 h.
  - Retention: 30 daily and 12 monthly backups.
  - At least one encrypted copy outside the primary application host.
  - A restored environment stays unavailable to users until tombstone and deletion state is re-applied, tenant boundaries are verified, and integrity and reconciliation checks pass.
- **Audit protection (option B):**
  - `audit_events` is append-only for the app role (UPDATE and DELETE revoked).
  - Free-text detail values are masked with the text-PII detector at write time (120-character cap).
  - A historical audit row can be erased only through a documented, owner-approved DBA procedure that the protected sink's verifier flags.
- **Backup alert contract (between Lanes C and D):** the gauge `homayar_backup_last_success_timestamp_seconds{target}` is written to a node_exporter textfile. It alerts when its age exceeds 26 h or when it is absent.

*Superseded:* Wave P0-1 (2026-09-27), integrator Session A; five workers with exclusive ownership:
A `voice/`, `routers/v1/voice.py`; B `providers/` (TTS, local runtimes, artifacts, hardware);
C `speech/`; D `locations/`, `routers/v1/locations.py`, `schemas/locations.py`;
E `voicemail/`, `routers/v1/voicemail.py`, `schemas/voicemail.py`.
Integrator-exclusive: migrations, `saas_models.py`, `rls.py`, `authorization.py`, `config.py`,
`main.py`, `celery_app/`, `jobs/processor.py`, lock files.

## F. Frozen backend sections

Core Baseline at main `ecd77cc` (§K), on local evidence only (not CI-validated). Wave P0-1 is PARTIAL: repository-controlled foundations landed; live qualification external. **Infrastructure is not frozen** (§N).

## G. External blockers

Status 2026-10-17. None of these is supplied, and none is replaced by a substitute:

| Blocker | Blocks | Status |
| --- | --- | --- |
| Persian call corpus with rights (HOM-65) | WP13 evaluation (HOM-66 to 71), F27/F80 accuracy claims | not supplied |
| GPU host and model weights (AppVision D09) | WP09-T03 to T07, WP10 diarization, F74 | not supplied |
| Homayar AI Gateway contract (D07) | WP08 Gateway adapter; 3 contract tests skipped | not supplied |
| Live S3 qualification bucket (HOM-36) | F14 live qualification; 41 live-S3 tests opt-in and skipped | not supplied |
| PBX test system: Asterisk, FreePBX or Issabel (HOM-87) | F63, live voice and SIP qualification (X21, V01 to V07) | not supplied |
| Credential rotation by the credential owners (HOM-30, HOM-31) | WP02-T01/T02; not done by the team | owner action open |
| GitHub/CI account | none: Actions runs again since 2026-10-10; branch protection needs GitHub Pro on this private repository | no blocker |

## H. Next safe work (dependency order)

Owner order from 2026-10-17 (infrastructure first; no product features before the freeze):

1. Phase 0: make main green (§M); reconcile this report and Linear.
2. Lane C: backup and restore (HOM-153 → 116 → 117 → 118). Lane D, in parallel: audit, observability and release (HOM-113 → 114 → 115 → 126 → 28 → 29).
3. Integration gate after both lanes merge: migrations, auth/session, tenant/RLS, privacy, retention, audit, queue/recovery, backup/restore, health, observability, build reproducibility, full backend suite.
4. Deployment security: HOM-33 → 34 → 35 → 196.
5. Infrastructure Freeze Gate (§N). Only then resume product work (WP12 call report, WP11 signals, WP18 billing, WP24 actions and the rest).

*Superseded list (2026-10-10).* Item 1 is done (HOM-195, PR #23); item 3 is done (HOM-157, HOM-169).

1. Wire `speech.text_normalize`/`entities` into the processor output (transcript post-processing) and into `locations.extract` and `voicemail.classify` (remove their local normalizers). Parallel-safe per package.
2. Voice runtime media bridge contract (SIP/WebRTC adapter interface, LiveKit/Asterisk ARI) behind `voice/` — interfaces + fakes; live qualification BLOCKED_EXTERNAL.
3. ~~Measure voice admission on PostgreSQL~~ done by HOM-157 (§I); next: fix the recover()/transition() race HOM-169.
4. Voice-specific permissions (VOICE_CALL_*), operator route for workspace call limits.
5. TTS in AiPolicy stages (owner decision on stored-policy digest change).
6. WP09-T01 channel-aware speech (see `docs/handoff/session-reset/WP09_T01_SESSION_PROMPT.md`).
7. X01/X02 capability registry + module enable/disable enforcement (licensing primitives).
8. WP18 quotas/plans (F18) on the existing usage ledger; WP24 approvals/governed actions.
9. WP16 signed webhooks delivery ledger (tables exist).
10. WP20 observability/audit export.

## I. Backend mission 20260929-backend-mode-100 (2026-09-29 .. 2026-09-30)

Integration branch `claude/inspiring-tesla-ie06gi`, PR #13. GitHub Actions could not start jobs during the mission
(account billing limit), so nothing below is CI-validated; evidence is local CI-equivalent runs (PostgreSQL 16,
Redis 7, ffmpeg, `ci.yml` values). Linear project "Homayar Backend"; no issue is Done (Done needs merge + green CI).

| Linear | Item | Status | Commit | Evidence |
| --- | --- | --- | --- | --- |
| HOM-140 | STAB-01: main was red (voice admission imported `random`) | PARTIAL (not CI-validated) | d4daf6e | blind oracle 56/56; suite 0 failed |
| HOM-152 | N11 legacy retirement (29 files, unsafe webhook secret compare) | PARTIAL | d5bbe5d | oracle 199/199; live route set unchanged |
| HOM-32 | WP02-T03 secret references (`*_FILE`), secret redaction, production SMTP/S3 rules | PARTIAL | 7112407 | oracle 584/584; 2 final security reviews |
| HOM-141 | AppVision N01-N15 approved capabilities | PARTIAL (docs) | 6b311e0 | scripted oracle PASS, 0 FAIL |
| HOM-157 | REC-02 voice admission caps proven on PostgreSQL across 6 planes | PARTIAL | fa241da | 250->200/50, node 20+20, workspace 15 exact; app role + RLS |
| HOM-163 | STAB-02 transport-drill race (test-only) | PARTIAL | 3e3a2f2 | stress: base fails, fix 8/8 |
| HOM-158, HOM-162 | README/settings residue; Makefile venv paths | PARTIAL | c8a8899, 54a60f0, 21d8289, c34d124 | focused 253 passed; make seed red->green |
| HOM-101 | WP18-T03 versioned plans | IN_PROGRESS (unreviewed WIP patch) | - | docs/handoff/.../wip/wp18t03-t1 |
| HOM-120 | WP22-T02 single normalization owner | IN_PROGRESS (unreviewed WIP patch) | - | docs/handoff/.../wip/wp22t02-t1 |

Measured (local, not qualification): 250-call burst on 6 planes, admission p50 1.16-1.38 s, p95 2.0-2.3 s.
Open defects filed: HOM-159 (S3 endpoint bypass via AWS_ENDPOINT_URL), HOM-160 (model_dump exposes secrets),
HOM-161 (plaintext internal transports), HOM-164 (postal-code redaction leak), HOM-165, HOM-166, HOM-167 (entitlement
gaps), HOM-168 (retention gap, unverified), HOM-169 (voice recover/transition race).
Continuation point: `docs/handoff/session-reset/backend-mode-100-linear/20260929-backend-mode-100/NEXT_SESSION_MASTER_PROMPT.md`.


## J. Phase 1 infrastructure checkpoint (branch `backend/infra-freeze`, 2026-10-02)

```
PHASE 1 IMPLEMENTATION: COMPLETE
PHASE 1 REVIEW: COMPLETE
MIGRATIONS / DRIFT: PASS
FUNCTIONAL TESTS: PASS
FULL SUITE: 2344 passed / 1 performance-gate failure / 46 skipped
OPEN P0: HOM-172
CORE BASELINE FROZEN: NO
FREEZE BLOCKER: HOM-120 30-second normalization performance gate requires controlled-load validation
```

Validated SHA: `742efda2f4c27fe3fcbb0aff84c87f6a713d6730` (base `e67ffaf` = main). Not CI-validated (GitHub
Actions billing limit); not production-ready. A checkpoint merge of this branch is not a Core Baseline Freeze.

Environment: local macOS (i9, 12 threads, heavily shared host, load 5-89 during the run), Python 3.12 venv,
native PostgreSQL 16 (fsync off, test-only), Redis 7.4 (docker), ffmpeg, `ci.yml` env values. Alembic single head
`a1c3e5f7b9d2` (31 revisions), upgrade/downgrade and model drift check PASS.

| Linear | Item | Commit | Review |
| --- | --- | --- | --- |
| HOM-170 | media decode tests: stand-in decoder pid recorded before Python starts (the 2 baseline failures) | bf979d0 | approved |
| HOM-169 | voice recovery: lock calls before nodes/slots, re-judge leases, never overwrite an ended call | e9c3ce9 | major finding fixed, approved |
| HOM-159 | object store ignores ambient AWS endpoint overrides; production refuses insecure ones | 7e259d5 | approved |
| HOM-160 | settings `model_dump`/`model_dump_json` redact secrets (explicit reveal context only) | e301dbb | approved |
| HOM-168 | delete-after-processing request committed with COMPLETED (kill-safe) | 10f3967 | approved, minors applied |
| HOM-120 | `speech/text_normalize` single normalization owner (490-case golden + oracle) | 77b9daa | approved |
| HOM-164 | postal-code redaction with Arabic letters / ZWNJ (`text-pii/2`) | 56d3787 | approved |
| HOM-165 | fullwidth digits folded in PII and caller numbers (`text-pii/3`) | 742efda | approved; minors -> HOM-171 (non-P0) |

Skips (46): 37 live-S3 opt-in tests, 3 Padyar AI Gateway contract tests (contract not supplied), 1 SQLite file drill,
1 `TEST_REDIS_CONTAINER` drill, remainder environment opt-ins; none hides a failing test.

Only failure: `test_text_normalization_owner.py::TestDeliverables::test_golden_module_is_green_fast_and_independent_of_the_registry`.
All 490 normalization cases pass; only the 30 s wall-clock budget fails. Evidence: quiet host ~28 s PASS; one
load-gated rerun (1-min load 1.96) PASS, two further reruns as load returned FAIL (63 s, 115 s); same host at load 5-89: 48-140 s FAIL. The 30 s limit was NOT changed.
Filed HOM-172 (P0, owner decision on controlled-load validation). Phase 2 (local/on-prem) has NOT started and stays
blocked until HOM-172 passes the existing gate under controlled load or CI and the freeze is recorded.
Secret scan: gitleaks over origin/main..HEAD reports 4 generic-api-key hits, all false positives (test fixture name, SHA-256 digests of reference functions, a function signature).
External blockers unchanged: CI billing, live S3, Padyar contract, Persian corpus, GPU/models, SIP.

## K. Core Baseline Freeze (main `ecd77cc`, 2026-10-09)

```
CORE BASELINE FROZEN: YES (local evidence; not CI-validated)
FROZEN SHA: ecd77ccac3622368a0eaafcd77feb1f1a9515647 (main, merge of PR #16)
HOM-172 GATE: PASS under controlled load (existing 30 s limit unchanged)
FULL SUITE: 2346 passed / 0 failed / 45 skipped
MIGRATIONS / DRIFT: PASS (single head a1c3e5f7b9d2; upgrade, alembic check, downgrade -1 / upgrade round trip)
PHASE 2 (local/on-prem): UNBLOCKED by this record
```

This closes the §J freeze blocker: HOM-172 asked for the existing gate to pass under controlled load, and the
freeze to be recorded. Not production-ready; GitHub Actions is still billing-blocked, so nothing here is CI-validated.

Environment: local macOS (i9, 12 threads), Python 3.12.13 venv, native PostgreSQL 16.15 (fsync off, test-only),
Redis 7.4 in docker (AOF always; `TEST_REDIS_CONTAINER` set, so the broker restart drill ran), ffmpeg on PATH,
`ci.yml` backend env values. One pytest process over `backend/tests`, as in CI: 15 min 54 s, 1-min load 3.8-6.5.

HOM-172 evidence (`test_text_normalization_owner.py::TestDeliverables::test_golden_module_is_green_fast_and_independent_of_the_registry`):

| Run | 1-min load | Result |
| --- | --- | --- |
| golden module alone, run 1 | 2.0 -> 4.3 | 490 passed in 18.75 s |
| golden module alone, run 2 | 4.3 -> 6.1 | 490 passed in 19.95 s |
| golden module alone, run 3 | 6.1 -> 6.8 | 490 passed in 21.37 s |
| gate test alone | 6.8 -> 7.5 | PASS |
| gate test inside the full suite | 3.8-6.5 | PASS |

Earlier runs the same day, while an unrelated docker workload drove the 1-min load to 79-154, are discarded as
uncontrolled (gate: 2 PASS, 1 FAIL; golden alone 31-140 s). They confirm the gate is a wall-clock gate that
depends on host load; CI or a quiet host remains the place to run it.

Skips (45): 41 live-S3 opt-in (BLOCKED_EXTERNAL), 3 Padyar AI Gateway contract tests (contract not supplied),
1 SQLite file-level backup drill (PostgreSQL uses the simulated restore). None hides a failing test.

Run hygiene: the working tree held 18 untracked paths byte-identical to the legacy files HOM-152 (`d5bbe5d`)
removed; they were moved out of the tree before the run (on disk they make `test_truthful_outputs` fail).
External blockers unchanged (§G): CI billing, live S3, Padyar contract, Persian corpus, GPU/models, SIP.

## L. WP18 plans and entitlements (branch `backend/core-freeze-wp18`, PR #18, 2026-10-10)

| Linear | Item | Commit | Review |
| --- | --- | --- | --- |
| HOM-101 | versioned plan terms; startup and sign-up never overwrite the catalog (D-12); migration `c5e7a9b1d3f6` | 728c7a3 | five lenses + adversarial verification; upheld findings fixed; blind oracle 125/126 (remaining case pins the mission-base hash of `config.py`, edited on purpose) |
| HOM-167 F3 | a retired demo plan is not granted at sign-up | 38ae948 | three lenses (F2-F4) |
| HOM-167 F2 | the catalog hides the demo while `ENABLE_FREE_DEMO` is off | 48b55b5 | as above |
| HOM-167 F4 | `GET /billing/subscription` shows the pinned version; `subscription.plan_version` | e477ca3 | as above |
| HOM-167 F1 | owner/admin memberships consume the plan's `seats` (owner decision 2026-10-10); 409 `seat_limit_reached` | 5cb4aa3, b935487 | four lenses; one critical finding (stale clock reading could exceed the limit) fixed in b935487 with regressions that fail on 5cb4aa3 |
| CI (pre-existing on main) | the backend job checks out the full history; HOM-172 digest lines are allowlisted in the secret scan | 2f5f666 | actionlint clean; gitleaks verified locally with the CI image (no leaks; probe 3 of 3 still reported) |

Evidence (local, PostgreSQL 16, `ci.yml` env; not CI-validated):

- full suite on 728c7a3: 2377 passed / 0 failed / 45 skipped;
- full suite on 5cb4aa3: 2380 passed / 1 failed / 50 skipped. The failure was the HOM-172 wall-clock gate (70 s) while a review workflow loaded the host. The 5 extra skips are Redis drills, because Docker was down after a host reboot;
- final full suite on b935487: 2393 passed / 1 failed / 50 skipped. The failure was the HOM-172 wall-clock gate (46.4 s; 1-minute host load 48 from unrelated work); the 5 extra skips are the Redis drills (no Redis during the run). With a disposable Redis afterwards, 4 of the 5 drills passed and the broker-restart drill timed out at 60 s under host load 54-89; the branch changes no transport code;
- GitHub Actions on b935487 (CI runs again since 2026-10-10): backend 2397 passed / 2 failed / 46 skipped, all Redis drills included. Both failures, and the 4 secret-scan findings, are identical on main since ecd77cc (runs 37031662707 and 38047281319), so they are not regressions of this branch:
  - `test_golden_module_uses_pure_functions_and_base_names_only` reads files at the base commit 7112407, which the shallow checkout lacked: fixed in 2f5f666 (the backend job checks out the full history);
  - the 4 gitleaks findings are SHA-256 digests and a fixture key in HOM-172 tests (commit 77b9daa): fixed in 2f5f666 with a whole-line allowlist that mirrors the wp22t02 entry. Locally: no leaks over the 175 commits of the GitHub branches, and a probe commit with three random secret-shaped lines in those files is still reported (3 of 3);
  - the HOM-172 30 s gate takes 37.35 s on the GitHub runner (38.12 s on main 2ae3314, 51.14 s on ecd77cc). It is not changed here: the bound is an owner decision (raise it for the runner, a larger runner, or speed work);
- GitHub Actions on 2f5f666: backend 2398 passed / 1 failed / 46 skipped (run 38077164530; pytest 22 min against the job's 25-minute timeout); the only failure is the HOM-172 30 s gate (76.51 s on that runner), unchanged from main; the secret scan and the other four checks pass;
- migrations: single head `c5e7a9b1d3f6`; upgrade, `alembic check` and the downgrade/upgrade round trip are clean.

Release notes:

- Migration `c5e7a9b1d3f6` is not backward-compatible with the prior image: stop the API before migrating. To roll the app back, export `plan_versions` and downgrade first (runbook "Upgrade and Rollback").
- Free demo workspaces have one seat, which their owner holds: they cannot add admins, only members and viewers.
- The frontend contract changes are listed in the PR #18 description.

## M. Lanes A/B, console backend tickets and Phase 0 (2026-10-10 .. 2026-10-17)

Backend PRs merged since `8234d08`, with GitHub Actions evidence on each PR's final head:

| PR | Linear | Result | Backend CI on the final head |
| --- | --- | --- | --- |
| #21 (Lane A) | HOM-172 | Documented CI bound for the golden-module gate (`HOMAYAR_GOLDEN_BUDGET_SECONDS=120`, honoured only with `CI=true`, range 30–300 s; 30 s stays the default); backend job timeout 40 min | run 38081359582: 2413 passed / 0 failed / 46 skipped |
| #23 (Lane B) | HOM-195 | Derived normalized text view and entities per transcript revision; raw evidence unchanged; RLS and purge coverage; ADR 0034; migration `d8a2f4c6b1e3` | run 38088234130: 2434 / 0 / 46 |
| #25 (Lane B) | HOM-171 | PII detector `text-pii/4`: the postal keyword is matched with harakat and tatweel; owner-approved golden re-baseline of version/digest pins only | run 38089871487: 2446 / 0 / 46 |
| #24 (Lane A) | HOM-161 | Production requires TLS to PostgreSQL, Redis, Neo4j and object storage; on-prem has audited, same-host-only, per-hop opt-outs; cloud has none | run 38108005671 (head `16fb67f`): 2733 / 0 / 46 |
| #22 (Sina) | HOM-175 | Demo workspace seed for the console | run 38084797435: 2413 / **1 failed** / 46. The failure is the HOM-172 30 s gate: the run started at 20:42, before #21's CI bound merged at 20:54. Merged on that check; main was green afterwards (`cfed7ac`) |
| #26 (Sina) | HOM-183 | Admin jobs list and per-state counts | run 38090572273: 2451 / 0 / 46 |
| #27 (Sina) | HOM-184 | `system_role` and workspace permissions in the session payload | run 38090852784: 2443 / 0 / 46 |

The console PRs #19, #20, #28, #30 to #35 (HOM-173, 174, 176, 177, 178, 180, 186, 189) and the Linear agent worker #29 (HOM-197) are frontend and tooling, outside this backend record.

Main CI since 2026-10-10:
- green on `7670e0c`, `7fa0dcf`, `d868836`, `d876a2a`, `cfed7ac`, `6fc62f7`, `2c7c7aa`, `57516f2` and `05c16ad`;
- red on `8234d08`: the HOM-172 30 s gate (52.58 s), before #21's CI bound;
- red on `0937918`: a console Vitest `window` failure, fixed by #34;
- red on `19d2d5d`: Phase 0, below;
- the other runs were cancelled by a newer push.

**Phase 0 (P0 privacy test, PR #39, merged 2026-10-17 as `fcc4962`).**
- **Symptom.** Main `19d2d5d` failed `test_privacy_routing.py::test_reads_default_to_redacted_and_the_original_needs_the_owner` (run 38598299491: 1 failed / 2773 passed / 46 skipped). That merge changed only console files, and the same backend code passed on `05c16ad`.
- **Cause: a test false positive, not a leak.** The helper `_no_raw` searched the whole response for short raw markers such as `0912`, and random UUIDs, digests and timestamp microseconds can contain them by chance. A captured run (40 checks) shows the redacted bodies hold only placeholders. Its 15 random hex tokens (387 four-character windows) and 4 timestamps give about a 0.7% chance per run of that one test.
- **Fix (test-only).** Generated values are blanked by their exact shape before the check: canonical UUIDs, standalone 24–128-character hex digests that contain a letter, and ISO timestamps. All markers and all 19 call sites stay, and raw digit runs are never blanked. Regression tests cover the false positive, and every marker in five placements still being reported.
- **Evidence.**
  - Local PostgreSQL 16 with the `ci.yml` env: 13 privacy, RLS, retention, legal-hold, transcript, text-view and API files, 222 passed / 0 failed / 0 skipped.
  - CI on the PR head `98f4401` (run 39079278356): 7 of 7 checks green; backend 2806 passed / 0 failed / 46 skipped.
  - Main after the merge: `fcc4962`, the same tree; push run 39080861815.

**Linear housekeeping (2026-10-17).** HOM-161 is Done, with its evidence comment. HOM-196 (certificates for the bundled on-prem services) stays open. The optional caller-number backfill from HOM-171 is tracked as HOM-199 and scheduled after the freeze.

## N. Infrastructure freeze status

```
CURRENT MAIN: fcc4962
MIGRATION HEAD: d8a2f4c6b1e3 (33 migrations, one head)
MAIN GREEN: YES (tree validated by run 39079278356: 2806 passed / 0 failed / 46 skipped; push run 39080861815)

LANE C:
- issues: HOM-153, HOM-116, HOM-117, HOM-118
- status: started 2026-10-17 (design; implementation after Phase 0 merges)
- PRs: none yet
- evidence: none yet

LANE D:
- issues: HOM-113, HOM-114, HOM-115, HOM-126, HOM-28, HOM-29
- status: started 2026-10-17 (owner decision on audit erasure taken: option B)
- PRs: none yet
- evidence: none yet

BACKUP: not delivered (legacy infra/scripts only; Lane C)
RESTORE: not delivered (Lane C)
RPO/RTO: provisional owner defaults 24 h / 8 h; no drill recorded yet
AUDIT: audit_events exists; append-only protection and protected sink not delivered (Lane D)
OBSERVABILITY: `/healthz`, `/readyz` and JSON logs exist; metrics, correlation IDs and alerts not delivered (Lane D)
REPRODUCIBLE BUILD: not delivered (HOM-28, Lane D)
RELEASE MANIFEST: not delivered (HOM-29, Lane D)
DEPLOYMENT SECURITY: production TLS on internal hops done (HOM-161); HOM-33, 34, 35 and 196 open; HOM-30/31 need the credential owners

INFRASTRUCTURE FROZEN: NO

OPEN P0: none repository-controlled beyond the freeze-gate items above (the Phase 0 privacy-test failure is closed by PR #39)
EXTERNAL BLOCKERS: §G (corpus, GPU and models, Gateway contract, live S3 bucket, PBX test system, credential rotation)
NEXT ACTION: Lanes C and D implement and open PRs; integrator reviews and merges each, then runs the integration gate
```
