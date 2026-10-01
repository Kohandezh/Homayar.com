# Homayar — AppVision Backend Implementation Progress

Operational index for backend work. `AppVision.md` defines intended behaviour; this file records
what the repository actually implements, with evidence. Statuses: PLANNED, IN_PROGRESS, PARTIAL,
VALIDATED, FROZEN, BLOCKED_EXTERNAL, NEEDS_RECONCILIATION, DEFERRED, SUPERSEDED, EXCLUDED.
VALIDATED requires passing evidence on the recorded SHA. Historical WP statuses come from
`docs/handoff/session-reset/PROJECT_STATE.md` and are carried as evidence leads.

## A. Current repository state

| Field | Value |
| --- | --- |
| Updated (UTC) | 2026-09-30 (backend mission 20260929-backend-mode-100; see §I) |
| Repository | github.com/Kohandezh/Homayar.com (monorepo; product in `Homayar/`, ADR 0033) |
| Working branch | `feat/backend-p0-wave` from main `88073c8` (merge of PR #6) |
| Migration head | `a1c3e5f7b9d2` (P0 wave; 31 linear migrations; main before merge: `f5b1d7c3a9e2`) |
| Runtime | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Celery 5 + Redis |
| CI services | PostgreSQL 17, Redis 7.4, ffmpeg 6.1.1 (Ubuntu 24.04) |
| Last validated main | `fd2d298`/PR #6 run 36294427694: 716 passed, 46 skipped |

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
| Privacy | `privacy/*` | transcript_redactions, redacted_segments, pii_mappings | `/privacy/subject-exports` | — | test_text_pii | VALIDATED (WP07 local) |
| Retention | `services/retention_policy.py`, `conversation_purge_service.py`, `tombstone_journal.py`, `jobs/retention_purge.py` | conversation_purges | legal-hold routes | run_retention_purge | test_retention_*, test_legal_hold, test_tombstone_reapply | VALIDATED (WP07 local) |
| Usage/billing | `services/entitlement_service.py`, `jobs/usage_reconciliation.py`, `routers/v1/billing.py` | plans, subscriptions, entitlements, usage_reservations, usage_events, provider_usage | `/billing/*` | reconcile_usage | test_usage_ledger, test_entitlements, test_billing_api | PARTIAL (F18 foundation; F19 PLANNED) |
| Approvals/actions | `action_items` via insights router | action_items | `/meetings/{id}/actions` | — | test_insights_shares_api | PARTIAL (no approval workflow) |
| Integrations/webhooks | models only | webhook_endpoints, webhook_deliveries, api_keys | `/api-keys` | — | test_api_keys | PARTIAL (WP16-T02) |
| Odoo | none | — | — | — | — | PLANNED |
| Voice runtime | `voice/{models,admission,state,metrics,loadtest,recovery}.py`, `routers/v1/voice.py` | voice_call_sessions (tenant), voice_workspace_limits (tenant), voice_media_nodes / voice_admission_locks / voice_capacity_slots (deployment-wide) | `/voice/calls*`, `/voice/capacity`, `/voice/nodes*` (platform operator), `/voice/recover` | beat `voice-recovery` | test_voice_admission, test_voice_api | PARTIAL (control plane; no SIP/WebRTC media) |
| Voicemail | `voicemail/{models,ingest,classify,pipeline,search}.py`, `routers/v1/voicemail.py` | voicemail_messages | `/voicemails*` | `jobs/processor._run_voicemail_hook` | test_voicemail_* | PARTIAL (rule classifier; LLM classifier pluggable) |
| Locations | `locations/{gazetteer,extract,normalize,validate,routing,service,models}.py`, `routers/v1/locations.py` | service_locations | `/locations*`, `/locations/resolve`, `/locations/nearest` | — | test_locations_* | PARTIAL (approx. centroids; no geocoder) |
| Speech/language | `speech/{text_normalize,dates,entities,audio_profile,languages,benchmark}.py` | — | — | — | test_speech_* | PARTIAL (library; not yet wired into processor) |
| Workflows | none | — | — | — | — | PLANNED |
| Notifications | model only | notifications | — | — | — | PARTIAL |
| Exports | `services/export_service.py`, `privacy/subject_export.py` | — | `/meetings/{id}/export` | — | — | PARTIAL |
| Licensing/capabilities | model-license check in `providers/routing.py`; API-key capability policy | — | — | — | — | PLANNED (X01/X02/X09) |
| Support bundle | none | — | — | — | — | PLANNED (X15) |
| Observability | `services/audit_service.py` | audit_events | `/admin/audit-events` | — | — | PARTIAL (WP20 not started) |
| Deployment | `infra/`, `docker-compose*`, egress proxy | — | — | — | infra/egress tests | PARTIAL |

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
F15–F16 PARTIAL (WP07); F17 VALIDATED (WP06); F18 PARTIAL; F19 PLANNED; F20–F22 PARTIAL (WP08);
F23–F24 VALIDATED (WP04); F25–F28, F30–F31, F33–F38, F41–F47, F49, F57–F61, F63–F64, F68–F70,
F72, F75, F78–F79, F86, F92 PLANNED; F80–F81 BLOCKED_EXTERNAL/PARTIAL (WP13); P2/P3 expansion rows
DEFERRED by AppVision §11.2 ("not authorized by initial coding kickoff").

## E. Active work wave

Wave P0-1 (2026-09-27), integrator Session A; five workers with exclusive ownership:
A `voice/`, `routers/v1/voice.py`; B `providers/` (TTS, local runtimes, artifacts, hardware);
C `speech/`; D `locations/`, `routers/v1/locations.py`, `schemas/locations.py`;
E `voicemail/`, `routers/v1/voicemail.py`, `schemas/voicemail.py`.
Integrator-exclusive: migrations, `saas_models.py`, `rls.py`, `authorization.py`, `config.py`,
`main.py`, `celery_app/`, `jobs/processor.py`, lock files.

## F. Frozen backend sections

None. Wave P0-1 is PARTIAL: repository-controlled foundations landed; live qualification external.

## G. External blockers

- Real PBX/SIP carrier and 200-call media infrastructure (X21/V01–V07 live qualification).
- Persian annotated telephone corpus with rights (WP13-T01; F27/F80 accuracy).
- Local model weights/hardware qualification (F74, K-rows).
- Homayar AI API Gateway contract (WP08).
- Live S3 bucket (WP05-T05); credential owners (WP02-T01).

## H. Next safe work (dependency order)

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

