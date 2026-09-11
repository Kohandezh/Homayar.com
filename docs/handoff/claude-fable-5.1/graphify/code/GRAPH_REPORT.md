Local static code map; consult diagnostics.json and lossless raw extraction.json. No feature completion or definition-resolution guarantee.

# Graph Report - /Users/emperor/Documents/AI/Homayar.com/.claude/worktrees/homayar-commercial-v1-a266f5  (2026-09-11)

## Corpus Check
- 333 files · ~226,765 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3879 nodes · 13821 edges · 137 communities (126 shown, 11 thin omitted)
- Extraction: 87% EXTRACTED · 13% INFERRED · 0% AMBIGUOUS · INFERRED: 1835 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Homayar/backend/jobs · 0
- Homayar/backend/jobs · 1
- Homayar/frontend/src/types · 2
- Homayar/backend/media · 3
- Homayar/backend/tests · 4
- Homayar/backend/services · 5
- Homayar/frontend/src/components/platform · 6
- src/lib · 7
- Homayar/backend/jobs · 8
- Homayar/frontend/src/components/auth · 9
- src/lib · 10
- Homayar/backend/security · 11
- Homayar/backend/migrations/versions · 12
- Homayar/backend/schemas · 13
- Homayar/backend/services · 14
- . · 15
- Homayar/backend/services · 16
- Homayar/backend/routers/v1 · 17
- Homayar/frontend/src/types · 18
- scripts · 19
- Homayar/backend/services · 20
- Homayar/backend/tests · 21
- Homayar/backend/tests · 22
- Homayar/backend/providers · 23
- Homayar/backend/tests · 24
- Homayar/backend/tests · 25
- Homayar/backend/routers/v1 · 26
- Homayar/backend/schemas · 27
- Homayar/backend/tests · 28
- Homayar/frontend/src/components/ui · 29
- src/app/about · 30
- Homayar/backend/services · 31
- Homayar/backend/services · 32
- Homayar/backend/database · 33
- . · 34
- src/lib · 35
- Homayar/backend/services · 36
- Homayar/backend/tests · 37
- Homayar/backend/services · 38
- Homayar/backend/routers/v1 · 39
- src/components · 40
- Homayar/frontend/src/lib · 41
- Homayar/backend/services · 42
- Homayar/backend/routers/v1 · 43
- Homayar/backend/providers · 44
- Homayar/backend/security · 45
- Homayar/frontend/src/lib · 46
- . · 47
- Homayar/backend/services · 48
- Homayar/backend/tests · 49
- Homayar/backend/tests · 50
- Homayar/backend/tests · 51
- Homayar/backend/tests · 52
- Homayar/backend/tests · 53
- Homayar/backend/services · 54
- Homayar/backend/tests · 55
- Homayar/backend/services · 56
- Homayar/backend/services · 57
- Homayar/frontend/src · 58
- src/components/platform · 59
- Homayar/backend/storage · 60
- src/app/solutions · 61
- Homayar/backend/database · 62
- Homayar/backend/storage · 63
- Homayar/backend/tests · 64
- infra/scripts/lib · 65
- src/components/site · 66
- src/app/blog/[slug] · 67
- Homayar/backend/schemas · 68
- Homayar/backend/jobs · 69
- Homayar/backend/storage · 70
- src/lib · 71
- Homayar/backend/providers · 72
- Homayar/backend/tests · 73
- Homayar/backend/routers · 74
- src/components/catalog · 75
- Homayar/backend/services · 76
- Homayar/backend/tests · 77
- src/lib · 78
- Homayar/backend/services · 79
- Homayar/frontend/src/components/platform · 80
- src/app/contact · 81
- Homayar/backend/services · 82
- Homayar/backend/tests · 83
- src/components/site · 84
- src/app/platform · 85
- Homayar/backend/services · 86
- Homayar/backend/services · 87
- Homayar/backend/storage · 88
- src/components/site · 89
- Homayar/backend/tests · 90
- Homayar/backend/services · 91
- src/db · 92
- src/app/success · 93
- infra/ci/tests · 94
- Homayar/backend/providers · 95
- Homayar/backend/services · 96
- Homayar/backend/services · 97
- Homayar/backend/services · 98
- scripts · 99
- Homayar/backend/services · 100
- Homayar/backend/storage · 101
- src/lib · 102
- Homayar/backend/tests · 103
- Homayar/backend/providers · 104
- Homayar/backend/storage · 105
- infra/ci · 106
- Homayar/backend/storage · 107
- Homayar/backend/storage · 108
- Homayar/backend/providers · 109
- Homayar/backend/database · 110
- Homayar/backend/services · 111
- Homayar/backend/tests · 112
- Homayar/backend/migrations/versions · 113
- Homayar/backend/migrations/versions · 114
- . · 115
- scripts · 116
- Homayar/backend/tests · 122
- Homayar/frontend/src/test · 123
- . · 124
- Homayar/backend/schemas · 125
- infra/ci · 126
- . · 127
- . · 128
- scripts · 129

## God Nodes (most connected - your core abstractions)
1. `Settings` - 320 edges
2. `inspection_session_factory()` - 162 edges
3. `Meeting` - 110 edges
4. `get_settings()` - 94 edges
5. `Workspace` - 93 edges
6. `Membership` - 92 edges
7. `WorkspaceContext` - 89 edges
8. `process_job()` - 87 edges
9. `User` - 86 edges
10. `ProcessingJob` - 79 edges

## Surprising Connections (you probably didn't know these)
- `StageContext` --uses--> `Settings`  [INFERRED]
  Homayar/backend/jobs/checkpoints.py → Homayar/backend/config.py
- `StageContext` --uses--> `ProviderIdentity`  [INFERRED]
  Homayar/backend/jobs/checkpoints.py → Homayar/backend/providers/base.py
- `StageContext` --uses--> `ASRResult`  [INFERRED]
  Homayar/backend/jobs/checkpoints.py → Homayar/backend/providers/schemas.py
- `StageContext` --uses--> `DiarizationResult`  [INFERRED]
  Homayar/backend/jobs/checkpoints.py → Homayar/backend/providers/schemas.py
- `StageContext` --uses--> `IntelligenceResult`  [INFERRED]
  Homayar/backend/jobs/checkpoints.py → Homayar/backend/providers/schemas.py

## Import Cycles
- None detected.

## Communities (137 total, 11 thin omitted)

### Community 0 - "Homayar/backend/jobs · 0"
Cohesion: 0.04
Nodes (132): get_session_factory(), Truthful per-stage outcome of one processing attempt (WP04-T01)., StageState, bind_job_context(), bind_tenant_context(), discover_workspaces(), Workspaces to sweep (read-only service scope on entitlements; ids only)., Remove objects of abandoned/invalidated checkpoints (bounded, conservative).… (+124 more)

### Community 1 - "Homayar/backend/jobs · 1"
Cohesion: 0.05
Nodes (120): DeclarativeBase, Event, AuditEvent, Base, CheckpointState, DispatchState, Entitlement, JobState (+112 more)

### Community 2 - "Homayar/frontend/src/types · 2"
Cohesion: 0.03
Nodes (115): acceptInvitation(), changeMemberRole(), downloadPayload(), downloadRecording(), exportMeeting(), filenameFromContentDisposition(), getCurrentSubscription(), getMeeting() (+107 more)

### Community 3 - "Homayar/backend/media · 3"
Cohesion: 0.06
Nodes (80): CompletedProcess, Secure media inspection primitives used before object publication., AudioStreamMetadata, _build_ffprobe_command(), _codec_allowed(), EmptyMediaError, _ensure_file_unchanged(), ExtensionContentMismatchError (+72 more)

### Community 4 - "Homayar/backend/tests · 4"
Cohesion: 0.09
Nodes (93): process_job(), Recover every expired RUNNING attempt (bounded batch). Returns counts., sweep_expired_leases(), create_object_storage(), configured_database_url(), inspection_session_factory(), is_postgres_url(), Session (+85 more)

### Community 5 - "Homayar/backend/services · 5"
Cohesion: 0.11
Nodes (76): ActionItem, new_id(), Organization, OrganizationMembership, OrganizationRole, OrganizationScopeGrant, Effective-dated organization role of a user (WP03-T03, ADR 0012)., Explicit analytics scope of an analyst/executive: one workspace, optionally one… (+68 more)

### Community 6 - "Homayar/frontend/src/components/platform · 6"
Cohesion: 0.07
Nodes (65): RequireAuth(), AppLayout(), Header(), navItems, Sidebar(), InvitationAcceptPage(), idempotencyKey(), MeetingsPage() (+57 more)

### Community 7 - "src/lib · 7"
Cohesion: 0.05
Nodes (43): inter, metadata, spaceGrotesk, vazirmatn, Footer(), Header(), LangSwitcher(), FullLogo() (+35 more)

### Community 8 - "Homayar/backend/jobs · 8"
Cohesion: 0.06
Nodes (62): JobDispatch, Transactional outbox row: the intent to publish one job to the broker. Written…, bind_service_scope(), Grant a session one narrow service scope (e.g. the dispatch outbox)., enqueue_processing_job(), publish_processing_job(), Broker publication primitive (WP06-T01 queue contract). Application code does…, Publish one message; raise on broker failure; return the message id.… (+54 more)

### Community 9 - "Homayar/frontend/src/components/auth · 9"
Cohesion: 0.09
Nodes (48): AuthCard(), AuthCardProps, AuthDivider(), AuthField(), AuthFieldProps, FormAlert(), GoogleSignInButton(), PasswordField() (+40 more)

### Community 10 - "src/lib · 10"
Cohesion: 0.07
Nodes (52): BILLING_EXPLANATIONS, ProductDetail(), splitOutcome(), Strings, en, fa, PLAN_ORDER, PlanCard() (+44 more)

### Community 11 - "Homayar/backend/security · 11"
Cohesion: 0.08
Nodes (47): ColumnElement, A named recipient of a ``recipients`` share, bound to a user once proven.…, Who may open a share link (WP03-T06)., ShareAudience, ShareLink, ShareRecipient, assignment_effective_clause(), effective_assignments() (+39 more)

### Community 12 - "Homayar/backend/migrations/versions · 12"
Cohesion: 0.06
Nodes (44): apply_row_level_security(), disable_policy_statements(), enable_policy_statements(), ensure_app_role_statements(), existing_policy_tables(), PostgreSQL row-level security policies (WP03-T05). Single source of the DDL…, Statements enabling, forcing and (re)creating the policy of every table.…, Policy tables that exist in the current schema (migrations add tables over… (+36 more)

### Community 13 - "Homayar/backend/schemas · 13"
Cohesion: 0.12
Nodes (55): _audit(), create_organization_workspace(), create_scope_grant(), grant_organization_member(), _grant_response(), install_workspace_member(), list_my_organizations(), list_organization_members() (+47 more)

### Community 14 - "Homayar/backend/services · 14"
Cohesion: 0.14
Nodes (51): Membership, Effective-dated operational role of a workspace member inside a team., TeamAssignment, TeamRole, TimestampMixin, User, Workspace, WorkspaceInvitation (+43 more)

### Community 15 - ". · 15"
Cohesion: 0.09
Nodes (48): CurrentSubscriptionResponse, Plan, Subscription, SubscriptionStatus, get_billing_management(), get_plan_catalog(), get_subscription(), get_usage() (+40 more)

### Community 16 - "Homayar/backend/services · 16"
Cohesion: 0.08
Nodes (44): ApiKey, create_workspace_api_key(), list_workspace_api_keys(), delete, post, Request, Response, Session (+36 more)

### Community 17 - "Homayar/backend/routers/v1 · 17"
Cohesion: 0.11
Nodes (51): _action_or_404(), correct_transcript(), create_action(), delete_action(), _evidence_response(), _export_action(), export_meeting(), get_meeting_evidence() (+43 more)

### Community 18 - "Homayar/frontend/src/types · 18"
Cohesion: 0.06
Nodes (38): CallsPage(), severityConfig, DashboardPage(), SENTIMENT_COLORS, KpiCard(), KpiCardProps, labelColors, LabelConfig (+30 more)

### Community 19 - "scripts · 19"
Cohesion: 0.15
Nodes (51): ACCENT_KEYS, ACCENTS, arcPath(), arcPoint(), background(), barChart(), card(), chooseAccent() (+43 more)

### Community 20 - "Homayar/backend/services · 20"
Cohesion: 0.12
Nodes (46): _as_utc(), assign_member(), _assignment_response(), create_workspace_team(), list_workspace_teams(), patch_team(), datetime, delete (+38 more)

### Community 21 - "Homayar/backend/tests · 21"
Cohesion: 0.11
Nodes (45): Durable background job orchestration., Reconcile every entitlement (bounded batch). Returns outcome counts., reconcile_usage(), _session_cookies(), _meeting_of(), _audit(), broker(), _committed_usage() (+37 more)

### Community 22 - "Homayar/backend/tests · 22"
Cohesion: 0.09
Nodes (35): apply_context(), bind_capability_context(), bind_organization_context(), bind_user_context(), clear_context(), current_context(), install_session_listeners(), _is_postgres() (+27 more)

### Community 23 - "Homayar/backend/providers · 23"
Cohesion: 0.22
Nodes (34): Asanak SMS adapter using an injected endpoint and credential., ASRProvider, DiarizationProvider, EmailProvider, _IdentifiedProvider, IntelligenceProvider, MockProviderDisabledError, ABC (+26 more)

### Community 24 - "Homayar/backend/tests · 24"
Cohesion: 0.08
Nodes (28): asyncio, AsanakSMSProvider, Any, Response, Submit one SMS to an injected Asanak endpoint. Message bodies and the API token…, ProviderIssue, LocalWhisperProvider, Any (+20 more)

### Community 25 - "Homayar/backend/tests · 25"
Cohesion: 0.10
Nodes (29): BaseSettings, model_validator, Validated runtime configuration for cloud and on-premise deployments., Settings, OAuthChallenge, _send_asanak_code(), _runtime_secret(), safe_fingerprint() (+21 more)

### Community 26 - "Homayar/backend/routers/v1 · 26"
Cohesion: 0.15
Nodes (43): _clear_session_cookies(), _client_ip(), forgot_password(), google_callback(), google_link_start(), google_start(), login(), logout() (+35 more)

### Community 27 - "Homayar/backend/schemas · 27"
Cohesion: 0.12
Nodes (39): ActionStatus, ActionItemCreateRequest, ActionItemResponse, ActionItemUpdateRequest, AnalysisRunResponse, EvidenceListResponse, EvidenceRefResponse, EvidenceSpanResponse (+31 more)

### Community 28 - "Homayar/backend/tests · 28"
Cohesion: 0.09
Nodes (31): _changed_columns(), RuntimeError, An ORM update tried to change committed transcript or analysis provenance., _refuse_provenance_update(), _refuse_segment_update(), TranscriptImmutableError, code_identity(), ProviderIdentity (+23 more)

### Community 29 - "Homayar/frontend/src/components/ui · 29"
Cohesion: 0.09
Nodes (29): Avatar(), AvatarBadge(), AvatarFallback(), AvatarGroup(), AvatarGroupCount(), AvatarImage(), Badge(), badgeVariants (+21 more)

### Community 30 - "src/app/about · 30"
Cohesion: 0.07
Nodes (30): AboutContent(), AboutCopy, DICT, en, fa, Value, metadata, BlogIndexPage() (+22 more)

### Community 31 - "Homayar/backend/services · 31"
Cohesion: 0.16
Nodes (39): get_settings(), IdentityProvider, LoginSession, OneTimeToken, datetime, utcnow(), normalize_email(), AccessTokenError (+31 more)

### Community 32 - "Homayar/backend/services · 32"
Cohesion: 0.13
Nodes (32): ReservationState, UsageReservation, Exception, Operator replay of dead-lettered jobs (WP06-T05). Replay is the only way a…, ReplayRefused, discover_entitlements(), EntitlementRef, _live_reservation_ids() (+24 more)

### Community 33 - "Homayar/backend/database · 33"
Cohesion: 0.11
Nodes (34): Engine, FastAPI, build_engine(), configure_database(), create_schema_for_test_or_development(), get_engine(), sessionmaker, _quote_identifier() (+26 more)

### Community 34 - ". · 34"
Cohesion: 0.13
Nodes (37): accept_member_invitation(), delete_member(), _invitation_response(), invite_member(), list_invitations(), list_members(), _member_response(), delete (+29 more)

### Community 35 - "src/lib · 35"
Cohesion: 0.10
Nodes (30): CaseStudiesPage(), AVATAR_GRADIENTS, CaseStudyPage(), generateMetadata(), generateStaticParams(), CatalogPage(), HERO, metadata (+22 more)

### Community 36 - "Homayar/backend/services · 36"
Cohesion: 0.13
Nodes (34): AnalysisRun, EvidenceStatus, MeetingSpeaker, MeetingSummary, One immutable version of a meeting's transcript (WP04-T02, ADR 0014). Every…, Provenance of one analysis (intelligence) pass over one transcript revision…, How well a finding is grounded in the transcript it was made from (WP04-T03).…, TranscriptRevision (+26 more)

### Community 37 - "Homayar/backend/tests · 37"
Cohesion: 0.08
Nodes (22): configured_redis_container(), docker_container(), flush_test_database(), process_environment(), Path, queue_state(), Real-transport test support (WP06-T07 transport drills). The drills in…, ``docker <action> <name>`` against the disposable broker container only. (+14 more)

### Community 38 - "Homayar/backend/services · 38"
Cohesion: 0.09
Nodes (32): load_prompt(), Prompt loader — reads prompt text files from the prompts directory., Load a prompt file by name (e.g. 'qc_fa', 'problem_en')., classify_intent(), generate_response(), process_question(), AI Copilot service — post-call intelligence assistant., run_intent_query() (+24 more)

### Community 39 - "Homayar/backend/routers/v1 · 39"
Cohesion: 0.13
Nodes (35): _export_revision(), access_share(), _authorized_grant(), create_share(), download_shared_recording(), edit_share_recipients(), export_share(), list_shares() (+27 more)

### Community 40 - "src/components · 40"
Cohesion: 0.10
Nodes (27): metadata, metadata, DICT, en, fa, Step, SuccessContent(), SuccessCopy (+19 more)

### Community 41 - "Homayar/frontend/src/lib · 41"
Cohesion: 0.09
Nodes (23): AgentStat, Message, suggestions, CallIncident, Incident, IncidentSeverity, UserFormData, UserFormModalProps (+15 more)

### Community 42 - "Homayar/backend/services · 42"
Cohesion: 0.14
Nodes (32): Who produced a transcript revision (WP04-T02)., TranscriptSource, correct_revision(), create_revision(), current_analysis_run(), current_revision(), EngineProvenance, ensure_speakers() (+24 more)

### Community 43 - "Homayar/backend/routers/v1 · 43"
Cohesion: 0.14
Nodes (33): cancel_dispatch(), Withdraw a pending publication (same transaction as the cancellation)., delete_meeting(), download_recording(), _enforce_upload_rate_limit(), _get_meeting(), get_meeting_job(), _job_response() (+25 more)

### Community 44 - "Homayar/backend/providers · 44"
Cohesion: 0.14
Nodes (27): _compatible_endpoint(), create_asr_provider(), create_diarization_provider(), create_intelligence_provider(), create_provider_bundle(), _provider_name(), ProviderFactory, AsyncClient (+19 more)

### Community 45 - "Homayar/backend/security · 45"
Cohesion: 0.14
Nodes (31): organization_role_has(), OrganizationPermission, Enum, str, Actor, _api_key_not_accepted(), optional_actor(), _parse_scope() (+23 more)

### Community 46 - "Homayar/frontend/src/lib · 46"
Cohesion: 0.09
Nodes (30): MeetingDetailPage, DetailTab, MeetingDetailPage(), QUALITY_LABELS, QualityPanel(), STAGE_LABELS, timestamp(), formatDate() (+22 more)

### Community 47 - ". · 47"
Cohesion: 0.14
Nodes (29): AuditEventListResponse, get_db(), Session, How far a member/viewer sees inside a workspace (owners/admins see all)., RecordScope, list_audit_events(), patch_workspace_settings(), datetime (+21 more)

### Community 48 - "Homayar/backend/services · 48"
Cohesion: 0.08
Nodes (25): Celery, make_celery(), Celery app — background job processing for Homayar. Queue contract (WP06-T01):…, Run migrations in 'offline' mode. This configures the context with just a URL…, Run migrations in 'online' mode. In this scenario we need to create an Engine…, run_migrations_offline(), run_migrations_online(), Validation and tenant namespacing for client idempotency keys. (+17 more)

### Community 49 - "Homayar/backend/tests · 49"
Cohesion: 0.16
Nodes (12): login_user(), TestClient, register_user(), TestClient, test_deactivated_user_session_is_rejected(), test_refresh_session_is_stored_as_hash(), TestEmailAuthentication, TestPhoneAuthentication (+4 more)

### Community 50 - "Homayar/backend/tests · 50"
Cohesion: 0.20
Nodes (27): _csrf(), _grant_organization_role(), _insert_membership(), _organization_id(), _phone_login(), postgres, WorkspaceRole, WP03-T06 follow-up: organization audience, phone recipients, recipient editing.… (+19 more)

### Community 51 - "Homayar/backend/tests · 51"
Cohesion: 0.10
Nodes (26): Config, Test helper for isolated environment overrides., reset_settings_cache(), client(), email_outbox(), fixture, MonkeyPatch, Path (+18 more)

### Community 52 - "Homayar/backend/tests · 52"
Cohesion: 0.13
Nodes (27): assert_production_posture(), describe_current_role(), RuntimeError, Effective privileges of ``current_user`` and RLS state of ``meetings``., Refuse to serve when the runtime role could bypass tenant isolation., RowLevelSecurityPostureError, _admin_execute(), _bind() (+19 more)

### Community 53 - "Homayar/backend/tests · 53"
Cohesion: 0.16
Nodes (11): ProviderError, Exception, Provider failure safe to expose to application orchestration., MockASRProvider, ASRRequest, ASRResult, _asr(), FlakyASRProvider (+3 more)

### Community 54 - "Homayar/backend/services · 54"
Cohesion: 0.11
Nodes (26): aggregated_issues(), call_problems(), call_qc_details(), issues_by_category(), kpis(), qc_trend(), Dashboard API router — post-call metrics., AI-clustered issue aggregation. (+18 more)

### Community 55 - "Homayar/backend/tests · 55"
Cohesion: 0.17
Nodes (23): _completed_once(), _dispatch(), Drill, _holding_asr(), _lease_lapsed(), fixture, WP06-T07 transport drills on the real Redis/Celery stack.…, The attempt is running and its ASR stage is held at the gate. (+15 more)

### Community 56 - "Homayar/backend/services · 56"
Cohesion: 0.15
Nodes (22): AuthIdentity, OtpChallenge, normalize_iranian_mobile(), normalize_workspace_slug(), Any, AuditEvent, Session, record_audit_event() (+14 more)

### Community 57 - "Homayar/backend/services · 57"
Cohesion: 0.13
Nodes (22): EvidenceSpan, One machine-readable citation: finding → revision → segment → text span → audio…, evidence_by_id(), evidence_status_for(), human_evidence(), list_evidence(), AnalysisRun, datetime (+14 more)

### Community 58 - "Homayar/frontend/src · 58"
Cohesion: 0.08
Nodes (22): App(), ForgotPasswordPage, InvitationAcceptPage, LoginPage, MeetingsPage, MembersPage, PhoneOtpRequestPage, PhoneOtpVerifyPage (+14 more)

### Community 59 - "src/components/platform · 59"
Cohesion: 0.10
Nodes (22): Agent, AiAgentsContent(), AiAgentsCopy, DICT, en, fa, TONES, metadata (+14 more)

### Community 60 - "Homayar/backend/storage · 60"
Cohesion: 0.17
Nodes (14): InvalidObjectKeyError, LocalObjectStorage, BinaryIO, Path, ReadableStream, stat_result, Stores private objects below a tenant-scoped root with atomic publication., Path (+6 more)

### Community 61 - "src/app/solutions · 61"
Cohesion: 0.08
Nodes (15): DICT, en, fa, Industry, INDUSTRY_STYLES, SolutionsContent(), SolutionsCopy, Team (+7 more)

### Community 62 - "Homayar/backend/database · 62"
Cohesion: 0.15
Nodes (23): BusinessInsight, Call, CallAgent, CallListItem, CallReport, ChatMessage, ChatRequest, DashboardKpis (+15 more)

### Community 63 - "Homayar/backend/storage · 63"
Cohesion: 0.13
Nodes (12): InvalidPresignTTL, ObjectNotFoundError, Exception, Base storage error with a stable machine-readable code., StorageError, validate_owned_object_key(), BinaryIO, Exception (+4 more)

### Community 64 - "Homayar/backend/tests · 64"
Cohesion: 0.14
Nodes (18): APIRoute, test_api_key_scope_document_matches_code(), _cookies(), _doc_block(), _headers(), _insert_meeting(), _listed_titles(), _Persona (+10 more)

### Community 65 - "infra/scripts/lib · 65"
Cohesion: 0.18
Nodes (15): backup-minio.sh script, backup-postgres.sh script, backup_relative_path(), canonical_existing_path(), compose(), compose_onprem(), die(), require_command() (+7 more)

### Community 66 - "src/components/site · 66"
Cohesion: 0.12
Nodes (18): CanvasErrorBoundary, hasWebGL(), HeroCanvas(), prefersReducedMotion(), TiltCard(), useActiveInView(), useMedia(), useMounted() (+10 more)

### Community 67 - "src/app/blog/[slug] · 67"
Cohesion: 0.15
Nodes (15): BlogPostPage(), formatDate(), generateMetadata(), generateStaticParams(), MONTHS, DICT, PrivacyContent(), metadata (+7 more)

### Community 68 - "Homayar/backend/schemas · 68"
Cohesion: 0.14
Nodes (19): computed_field, DegradationResponse, DispatchResponse, LeaseResponse, MeetingDetailResponse, MeetingListResponse, MeetingResponse, MeetingUpdateRequest (+11 more)

### Community 69 - "Homayar/backend/jobs · 69"
Cohesion: 0.13
Nodes (18): delete_artifact_objects(), Best-effort object deletion; returns the keys that are now absent., check_all(), check_workspace(), discover_workspaces(), datetime, ProcessingJob, Session (+10 more)

### Community 70 - "Homayar/backend/storage · 70"
Cohesion: 0.20
Nodes (17): ChecksumMismatchError, effective_size_limit(), iter_stream(), normalize_expected_checksum(), ObjectTooLargeError, ABC, Enum, str (+9 more)

### Community 71 - "src/lib · 71"
Cohesion: 0.18
Nodes (20): bookingSchema, isUniqueViolation(), POST(), buildICS(), CalendarEvent, dateKeyInTz(), googleCalendarUrl(), HORIZON_DAYS (+12 more)

### Community 72 - "Homayar/backend/providers · 72"
Cohesion: 0.15
Nodes (11): params_digest(), Short, stable digest of a prompt/template/transcript (never the text itself)., text_digest(), _chat_completions_url(), OpenAICompatibleIntelligenceProvider, Any, AsyncClient, Response (+3 more)

### Community 73 - "Homayar/backend/tests · 73"
Cohesion: 0.16
Nodes (13): Explicit response for deployments without a diarization engine., UnavailableDiarizationProvider, ProviderBundle, MockIntelligenceProvider, DiarizationSegment, Test double for the fully successful path: MockDiarizationProvider is honestly…, SingleSpeakerDiarizationProvider, test_unavailable_diarization_yields_partial_outcome() (+5 more)

### Community 74 - "Homayar/backend/routers · 74"
Cohesion: 0.15
Nodes (19): _extract_extension(), _normalize_event(), BaseModel, post, Request, VOIP webhook router — receives call events from Asterisk/3CX/FreePBX., Receive multiple call events at once (e.g., daily sync from VOIP system)., Auto-detect VOIP source and normalize to standard format. (+11 more)

### Community 75 - "src/components/catalog · 75"
Cohesion: 0.12
Nodes (17): BILLING_VALUES, CatalogExplorer(), en, ExplorerStrings, fa, featuredScore(), INDUSTRY_VALUES, isCustomPriced() (+9 more)

### Community 76 - "Homayar/backend/services · 76"
Cohesion: 0.21
Nodes (19): AuditEventPage, _decode_audit_cursor(), _encode_audit_cursor(), get_workspace_settings(), InvalidAuditCursor, InvalidAuditRange, InvalidWorkspaceSettings, _is_sensitive_key() (+11 more)

### Community 77 - "Homayar/backend/tests · 77"
Cohesion: 0.26
Nodes (18): _processed_meeting(), test_share_links_are_hashed_expiring_auth_optional_and_revocable(), test_transcript_actions_search_and_exports_are_tenant_scoped(), _write_headers(), test_background_processing_writes_only_into_the_owning_workspace(), _correct(), WP04-T02: immutable transcript revisions and analysis-run provenance (ADR…, Put the completed job back in the queue as an operator would for a re-run. (+10 more)

### Community 78 - "src/lib · 78"
Cohesion: 0.22
Nodes (15): GET(), contactSchema, POST(), demoSchema, POST(), leads, getDb(), hasDb() (+7 more)

### Community 79 - "Homayar/backend/services · 79"
Cohesion: 0.15
Nodes (17): ldap_sync(), ldap_test(), post, LDAP sync router — AD user synchronization endpoints., Test LDAP connectivity., Full sync: fetch users from AD and upsert into Neo4j., authenticate_ldap(), fetch_ad_users() (+9 more)

### Community 80 - "Homayar/frontend/src/components/platform · 80"
Cohesion: 0.16
Nodes (18): OverviewPage, COPY, formatDate(), formatDuration(), formatNumber(), MeetingRow(), metricLabel(), OverviewCopy (+10 more)

### Community 81 - "src/app/contact · 81"
Cohesion: 0.12
Nodes (14): ContactContent(), ContactCopy, DICT, en, Expectation, fa, metadata, ContactForm() (+6 more)

### Community 82 - "Homayar/backend/services · 82"
Cohesion: 0.18
Nodes (17): UserCreate, authenticate_user(), create_user(), ensure_admin_exists(), get_user_by_email(), get_user_by_id(), list_users(), User service — Neo4j-backed user management with bcrypt password hashing. (+9 more)

### Community 83 - "Homayar/backend/tests · 83"
Cohesion: 0.27
Nodes (16): api_key_digest(), Homayar backend test suite., _allow_uploads(), _bearer(), _error(), _key_upload(), _mint(), _owner() (+8 more)

### Community 84 - "src/components/site · 84"
Cohesion: 0.12
Nodes (6): metadata, content(), Copy, DemoExperience(), Scenario, Turn

### Community 85 - "src/app/platform · 85"
Cohesion: 0.13
Nodes (12): DICT, en, fa, Item, PlatformContent(), PlatformCopy, SECURITY_ICONS, Step (+4 more)

### Community 86 - "Homayar/backend/services · 86"
Cohesion: 0.16
Nodes (10): Neo4jConnection, Execute multiple write queries in a single transaction. queries_and_params:…, Update call processing status., Store transcript for a call., Store full analysis results for a call in a single transaction., store_analysis(), store_transcript(), update_call_status() (+2 more)

### Community 87 - "Homayar/backend/services · 87"
Cohesion: 0.24
Nodes (12): entities(), entity_detail(), neighborhood(), Graph Intelligence API router., stats(), list_calls(), List calls with optional filters., get_entities() (+4 more)

### Community 88 - "Homayar/backend/storage · 88"
Cohesion: 0.13
Nodes (5): InvalidStreamError, ObjectConflictError, ObjectIncompleteError, An object with the requested identity exists with *different* content., An object with the requested identity exists but its write never completed.

### Community 89 - "src/components/site · 89"
Cohesion: 0.15
Nodes (9): metadata, Booked, BookingExperience(), dateKey(), Lang, STR, BUSINESS_TZ, Slot (+1 more)

### Community 90 - "Homayar/backend/tests · 90"
Cohesion: 0.26
Nodes (13): WorkspaceNotFoundError, _add_membership(), admin_client(), _error_code(), fixture, WorkspaceRole, _register_and_login(), test_audit_events_are_tenant_scoped_filtered_sanitized_and_paginated() (+5 more)

### Community 91 - "Homayar/backend/services · 91"
Cohesion: 0.22
Nodes (12): ByteRange, _content_disposition(), deliver_recording(), _iter_reader(), parse_range(), HTTPException, Response, Bounded recording delivery with HTTP Range support (WP03-T06). Two delivery… (+4 more)

### Community 92 - "src/db · 92"
Cohesion: 0.15
Nodes (12): main(), readJson(), root, blogPosts, bookings, caseStudies, categories, demoRequests (+4 more)

### Community 93 - "src/app/success · 93"
Cohesion: 0.31
Nodes (10): checkoutSchema, POST(), POST(), SuccessSummary, getSessionSummary(), metadata, SuccessPage(), checkoutSessions (+2 more)

### Community 94 - "infra/ci/tests · 94"
Cohesion: 0.22
Nodes (12): commit_all(), GIT_AUTHOR_EMAIL, GIT_AUTHOR_NAME, GIT_COMMITTER_EMAIL, GIT_COMMITTER_NAME, GIT_CONFIG_NOSYSTEM, HOME, mkrepo() (+4 more)

### Community 95 - "Homayar/backend/providers · 95"
Cohesion: 0.17
Nodes (4): AsyncClient, ActionItem, field_validator, SecretStr

### Community 96 - "Homayar/backend/services · 96"
Cohesion: 0.36
Nodes (11): ExportRevision, MeetingExport, _quality_lines(), Deterministic meeting export renderers for text-based formats., Which immutable transcript revision the export was rendered from., _render_json(), render_meeting_export(), _render_subtitles() (+3 more)

### Community 97 - "Homayar/backend/services · 97"
Cohesion: 0.24
Nodes (10): HexColor, create_call_record(), get_call_detail(), Get full call detail with transcript, QC, problems, insights., Create a Call node in Neo4j., generate_call_pdf(), PDF report generation for call reports., Generate a PDF report for a call. Returns bytes. (+2 more)

### Community 98 - "Homayar/backend/services · 98"
Cohesion: 0.24
Nodes (10): _send_verification_message(), Email service — SMTP via FastAPI-Mail + Mailpit for dev., Synchronous email send for Celery tasks., Send password reset email with token., Send email verification with token., Send email using configured SMTP server., send_email(), send_email_sync() (+2 more)

### Community 99 - "scripts · 99"
Cohesion: 0.20
Nodes (10): arr, byCode, c, codeIdx, decode(), deep(), e, raw (+2 more)

### Community 100 - "Homayar/backend/services · 100"
Cohesion: 0.27
Nodes (7): Path, UploadFile, ValueError, Bounded staging for multipart uploads before media inspection., stage_upload(), StagedUpload, UploadRejectedError

### Community 101 - "Homayar/backend/storage · 101"
Cohesion: 0.24
Nodes (9): generate_object_key(), InvalidTenantError, object_key_for(), KeyFactory, A caller-chosen object token (WP06-T03 attempt-scoped artifacts). Same alphabet…, Deterministic key for a caller-chosen token, else a fresh random key., tenant_prefix(), validate_object_token() (+1 more)

### Community 102 - "src/lib · 102"
Cohesion: 0.31
Nodes (9): Attachment, COPY, escapeHtml(), formatWhen(), fromAddress(), hasEmail(), Lang, send() (+1 more)

### Community 103 - "Homayar/backend/tests · 103"
Cohesion: 0.53
Nodes (7): test_rolled_back_upload_leaves_no_job_and_no_intent(), _authenticated_workspace(), test_upload_enforces_csrf_verification_media_and_idempotency(), test_upload_is_durable_private_and_idempotent(), test_upload_reservations_enforce_concurrency_and_audio_quota(), _upload(), _valid_metadata()

### Community 105 - "Homayar/backend/storage · 105"
Cohesion: 0.25
Nodes (5): PresignedAccess, PresignedAccessNotSupportedError, BaseModel, Short-lived read access produced by a storage backend., Private tenant-scoped object storage implementations.

### Community 106 - "infra/ci · 106"
Cohesion: 0.36
Nodes (6): config_error(), info(), check-source-contract.sh script, usage(), violation(), warn()

### Community 107 - "Homayar/backend/storage · 107"
Cohesion: 0.32
Nodes (5): field_validator, ReadableStream, Metadata returned only after a complete, checksum-verified write., Store a complete, checksum-verified object and return its metadata. Without…, StoredObject

### Community 108 - "Homayar/backend/storage · 108"
Cohesion: 0.36
Nodes (5): StorageConfigurationError, validate_chunk_size(), KeyFactory, Any, KeyFactory

### Community 110 - "Homayar/backend/database · 110"
Cohesion: 0.50
Nodes (4): create_calls(), Seed script — post-call voice intelligence domain., Generate call records from templates., seed()

### Community 111 - "Homayar/backend/services · 111"
Cohesion: 0.40
Nodes (4): JSONFormatter, Configure structured JSON logging., setup_logging(), LogRecord

### Community 112 - "Homayar/backend/tests · 112"
Cohesion: 0.40
Nodes (3): parametrize, No fabricated analytics may exist on any importable backend path…, test_fabricated_analytics_modules_are_gone()

### Community 115 - ". · 115"
Cohesion: 0.50
Nodes (3): csp, nextConfig, securityHeaders

### Community 116 - "scripts · 116"
Cohesion: 0.50
Nodes (3): issues, PAGES, VIEWPORTS

### Community 122 - "Homayar/backend/tests · 122"
Cohesion: 0.67
Nodes (3): broker(), fixture, Deterministic publisher: recovery re-arms intents and publishes through it.

## Knowledge Gaps
- **294 isolated node(s):** `_FormatProfile`, `queryClient`, `AgentStat`, `waveHeights`, `AuthCardProps` (+289 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **11 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `process_job()` connect `Homayar/backend/tests · 4` to `Homayar/backend/jobs · 0`, `Homayar/backend/jobs · 1`, `Homayar/backend/jobs · 69`, `Homayar/backend/providers · 72`, `Homayar/backend/tests · 73`, `Homayar/backend/providers · 44`, `Homayar/backend/tests · 53`, `Homayar/backend/providers · 23`, `Homayar/backend/tests · 25`, `Homayar/backend/services · 31`?**
  _High betweenness centrality (0.001) - this node is a cross-community bridge._
- **Why does `_commit_stage()` connect `Homayar/backend/jobs · 1` to `Homayar/backend/jobs · 0`, `Homayar/backend/tests · 25`, `Homayar/backend/jobs · 69`, `Homayar/backend/providers · 23`?**
  _High betweenness centrality (0.001) - this node is a cross-community bridge._
- **Why does `ProviderBundle` connect `Homayar/backend/tests · 73` to `Homayar/backend/providers · 72`, `Homayar/backend/tests · 53`, `Homayar/backend/providers · 23`, `Homayar/backend/tests · 24`, `Homayar/backend/tests · 25`?**
  _High betweenness centrality (0.001) - this node is a cross-community bridge._
- **What connects `_FormatProfile`, `queryClient`, `AgentStat` to the rest of the system?**
  _294 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Homayar/backend/jobs · 0` be split into smaller, more focused modules?**
  _Cohesion score 0.0393253191687634 - nodes in this community are weakly interconnected._
- **Should `Homayar/backend/jobs · 1` be split into smaller, more focused modules?**
  _Cohesion score 0.05228758169934641 - nodes in this community are weakly interconnected._
- **Should `Homayar/frontend/src/types · 2` be split into smaller, more focused modules?**
  _Cohesion score 0.030622418458908986 - nodes in this community are weakly interconnected._