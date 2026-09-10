Local static code map; consult diagnostics.json and lossless raw extraction.json. No feature completion or definition-resolution guarantee.

# Graph Report - /Users/emperor/Documents/AI/Homayar.com/.claude/worktrees/homayar-commercial-v1-a266f5  (2026-09-10)

## Corpus Check
- 325 files · ~214,926 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3676 nodes · 12927 edges · 135 communities (122 shown, 13 thin omitted)
- Extraction: 87% EXTRACTED · 13% INFERRED · 0% AMBIGUOUS · INFERRED: 1628 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Homayar/frontend/src/lib · 0
- Homayar/backend/storage · 1
- Homayar/backend/media · 2
- Homayar/backend/services · 3
- Homayar/frontend/src/components/auth · 4
- src/lib · 5
- Homayar/backend/jobs · 6
- Homayar/backend/jobs · 7
- src/lib · 8
- Homayar/frontend/src/components/platform · 9
- Homayar/backend/tests · 10
- Homayar/backend/tests · 11
- Homayar/backend/services · 12
- Homayar/backend/schemas · 13
- Homayar/backend/security · 14
- src/lib · 15
- Homayar/backend/jobs · 16
- . · 17
- Homayar/frontend/src/types · 18
- scripts · 19
- Homayar/backend/tests · 20
- Homayar/backend/providers · 21
- Homayar/backend/tests · 22
- Homayar/backend/jobs · 23
- Homayar/backend/providers · 24
- Homayar/backend/services · 25
- Homayar/backend/tests · 26
- Homayar/backend/routers/v1 · 27
- Homayar/backend/tests · 28
- Homayar/frontend/src/components/ui · 29
- . · 30
- Homayar/backend/services · 31
- Homayar/backend/providers · 32
- Homayar/backend/services · 33
- Homayar/backend/migrations/versions · 34
- Homayar/backend/jobs · 35
- src/components · 36
- . · 37
- Homayar/frontend/src/lib · 38
- src/app/about · 39
- Homayar/backend/jobs · 40
- Homayar/backend/security · 41
- Homayar/backend/providers · 42
- Homayar/backend/schemas · 43
- Homayar/backend/services · 44
- Homayar/backend/routers/v1 · 45
- Homayar/backend/services · 46
- Homayar/backend/services · 47
- Homayar/backend/services · 48
- Homayar/backend/services · 49
- Homayar/backend/tests · 50
- Homayar/backend/providers · 51
- Homayar/backend/tests · 52
- src/components/platform · 53
- . · 54
- Homayar/backend/database · 55
- Homayar/backend/tests · 56
- src/app/solutions · 57
- Homayar/backend/tests · 58
- Homayar/backend/jobs · 59
- Homayar/backend/tests · 60
- Homayar/backend/services · 61
- Homayar/backend/tests · 62
- Homayar/frontend/src/lib · 63
- infra/scripts/lib · 64
- src/components/site · 65
- Homayar/backend/services · 66
- src/lib · 67
- Homayar/backend/database · 68
- Homayar/backend/services · 69
- src/lib · 70
- Homayar/backend/tests · 71
- src/app/contact · 72
- Homayar/backend/schemas · 73
- Homayar/backend/routers/v1 · 74
- Homayar/backend/routers · 75
- Homayar/backend/tests · 76
- src/components/site · 77
- src/app/platform · 78
- Homayar/frontend/src/lib · 79
- Homayar/backend/tests · 80
- src/components/site · 81
- src/app/privacy · 82
- Homayar/backend/routers · 83
- infra/ci/tests · 84
- src/app/success · 85
- Homayar/backend/services · 86
- Homayar/backend/tests · 87
- Homayar/backend/services · 88
- Homayar/backend/tests · 89
- src/db · 90
- Homayar/backend/services · 91
- Homayar/backend/services · 92
- scripts · 93
- Homayar/backend/providers · 94
- Homayar/backend/services · 95
- src/lib · 96
- Homayar/backend/services · 97
- Homayar/backend/tests · 98
- Homayar/backend/services · 99
- Homayar/backend/providers · 100
- infra/ci · 101
- Homayar/backend/database · 102
- Homayar/backend/tests · 103
- Homayar/backend/migrations/versions · 104
- Homayar/backend/tests · 105
- Homayar/backend/database · 106
- Homayar/backend/tests · 107
- Homayar/backend/migrations/versions · 108
- Homayar/backend/migrations/versions · 109
- Homayar/backend/schemas · 110
- . · 111
- scripts · 112
- scripts · 113
- . · 119
- Homayar/backend/schemas · 120
- Homayar/frontend/src/test · 121
- . · 122
- Homayar/backend/schemas · 123
- infra/ci · 124
- . · 125
- . · 126
- scripts · 127

## God Nodes (most connected - your core abstractions)
1. `Settings` - 319 edges
2. `inspection_session_factory()` - 150 edges
3. `Meeting` - 95 edges
4. `get_settings()` - 94 edges
5. `Workspace` - 93 edges
6. `Membership` - 92 edges
7. `User` - 86 edges
8. `WorkspaceContext` - 84 edges
9. `process_job()` - 83 edges
10. `_queued_upload()` - 79 edges

## Surprising Connections (you probably didn't know these)
- `StageContext` --uses--> `Settings`  [INFERRED]
  Homayar/backend/jobs/checkpoints.py → Homayar/backend/config.py
- `StageContext` --uses--> `Entitlement`  [INFERRED]
  Homayar/backend/jobs/checkpoints.py → Homayar/backend/database/saas_models.py
- `StageContext` --uses--> `JobState`  [INFERRED]
  Homayar/backend/jobs/checkpoints.py → Homayar/backend/database/saas_models.py
- `StageContext` --uses--> `Meeting`  [INFERRED]
  Homayar/backend/jobs/checkpoints.py → Homayar/backend/database/saas_models.py
- `StageContext` --uses--> `ProcessingJob`  [INFERRED]
  Homayar/backend/jobs/checkpoints.py → Homayar/backend/database/saas_models.py

## Import Cycles
- None detected.

## Communities (135 total, 13 thin omitted)

### Community 0 - "Homayar/frontend/src/lib · 0"
Cohesion: 0.03
Nodes (129): MeetingDetailPage, DetailTab, MeetingDetailPage(), STAGE_LABELS, timestamp(), idempotencyKey(), MeetingsPage(), SettingsPanel() (+121 more)

### Community 1 - "Homayar/backend/storage · 1"
Cohesion: 0.04
Nodes (69): Config, Bounded recording delivery with HTTP Range support (WP03-T06). Two delivery…, ChecksumMismatchError, effective_size_limit(), generate_object_key(), InvalidObjectKeyError, InvalidPresignTTL, InvalidStreamError (+61 more)

### Community 2 - "Homayar/backend/media · 2"
Cohesion: 0.06
Nodes (80): CompletedProcess, Secure media inspection primitives used before object publication., AudioStreamMetadata, _build_ffprobe_command(), _codec_allowed(), EmptyMediaError, _ensure_file_unchanged(), ExtensionContentMismatchError (+72 more)

### Community 3 - "Homayar/backend/services · 3"
Cohesion: 0.11
Nodes (77): ActionItem, new_id(), Organization, OrganizationMembership, OrganizationRole, OrganizationScopeGrant, Effective-dated organization role of a user (WP03-T03, ADR 0012)., Explicit analytics scope of an analyst/executive: one workspace, optionally one… (+69 more)

### Community 4 - "Homayar/frontend/src/components/auth · 4"
Cohesion: 0.07
Nodes (60): App(), ForgotPasswordPage, InvitationAcceptPage, LoginPage, MeetingsPage, PhoneOtpRequestPage, PhoneOtpVerifyPage, queryClient (+52 more)

### Community 5 - "src/lib · 5"
Cohesion: 0.05
Nodes (67): BILLING_EXPLANATIONS, ProductDetail(), splitOutcome(), Strings, en, fa, PLAN_ORDER, PlanCard() (+59 more)

### Community 6 - "Homayar/backend/jobs · 6"
Cohesion: 0.07
Nodes (71): CheckpointState, ProviderUsage, Lifecycle of one stage checkpoint / artifact (WP06-T03). ``PENDING``: the…, A committed stage result with the provenance needed to reuse it (WP06-T03).…, Recording, StageCheckpoint, bind_tenant_context(), abandon_intent() (+63 more)

### Community 7 - "Homayar/backend/jobs · 7"
Cohesion: 0.07
Nodes (64): Event, MeetingSpeaker, MeetingStatus, ProcessingStageRun, str, Truthful per-stage outcome of one processing attempt (WP04-T01)., One stage of one processing attempt with its typed outcome and provenance., StageState (+56 more)

### Community 8 - "src/lib · 8"
Cohesion: 0.05
Nodes (42): inter, metadata, spaceGrotesk, vazirmatn, Footer(), Header(), LangSwitcher(), FullLogo() (+34 more)

### Community 9 - "Homayar/frontend/src/components/platform · 9"
Cohesion: 0.06
Nodes (62): OverviewPage, SearchPage, SettingsPage, SharedMeetingPage, InvitationAcceptPage(), COPY, formatDate(), formatDuration() (+54 more)

### Community 10 - "Homayar/backend/tests · 10"
Cohesion: 0.07
Nodes (46): _completed_once(), _dispatch(), Drill, _holding_asr(), _lease_lapsed(), fixture, WP06-T07 transport drills on the real Redis/Celery stack.…, The attempt is running and its ASR stage is held at the gate. (+38 more)

### Community 11 - "Homayar/backend/tests · 11"
Cohesion: 0.08
Nodes (62): expected_counters(), _live_reservation_ids(), (used, reserved) implied by the reservation rows of one entitlement., Release orphaned reservations and check (optionally repair) the counters., Reconcile every entitlement (bounded batch). Returns outcome counts., reconcile_entitlement(), reconcile_usage(), pytest_collection_modifyitems() (+54 more)

### Community 12 - "Homayar/backend/services · 12"
Cohesion: 0.10
Nodes (59): Effective-dated operational role of a workspace member inside a team., TeamAssignment, TeamRole, _as_utc(), assign_member(), _assignment_response(), create_workspace_team(), list_workspace_teams() (+51 more)

### Community 13 - "Homayar/backend/schemas · 13"
Cohesion: 0.12
Nodes (56): _audit(), create_organization_workspace(), create_scope_grant(), grant_organization_member(), _grant_response(), install_workspace_member(), list_my_organizations(), list_organization_members() (+48 more)

### Community 14 - "Homayar/backend/security · 14"
Cohesion: 0.08
Nodes (46): ColumnElement, A named recipient of a ``recipients`` share, bound to a user once proven.…, ShareLink, ShareRecipient, assignment_effective_clause(), can_grant(), can_manage_member(), effective_assignments() (+38 more)

### Community 15 - "src/lib · 15"
Cohesion: 0.08
Nodes (44): BlogIndexPage(), formatDate(), metadata, MONTHS, BlogPostPage(), formatDate(), generateMetadata(), generateStaticParams() (+36 more)

### Community 16 - "Homayar/backend/jobs · 16"
Cohesion: 0.07
Nodes (45): enqueue_processing_job(), publish_processing_job(), Broker publication primitive (WP06-T01 queue contract). Application code does…, Publish one message; raise on broker failure; return the message id.…, Best-effort publish without outbox bookkeeping (smoke tests, tooling)., Durable background job orchestration., dispatch_job(), Publish one pending row now if it is due; safe to call repeatedly. (+37 more)

### Community 17 - ". · 17"
Cohesion: 0.10
Nodes (50): accept_member_invitation(), delete_member(), _invitation_response(), invite_member(), list_invitations(), list_members(), _member_response(), delete (+42 more)

### Community 18 - "Homayar/frontend/src/types · 18"
Cohesion: 0.06
Nodes (38): CallsPage(), severityConfig, DashboardPage(), SENTIMENT_COLORS, KpiCard(), KpiCardProps, labelColors, LabelConfig (+30 more)

### Community 19 - "scripts · 19"
Cohesion: 0.15
Nodes (51): ACCENT_KEYS, ACCENTS, arcPath(), arcPoint(), background(), barChart(), card(), chooseAccent() (+43 more)

### Community 20 - "Homayar/backend/tests · 20"
Cohesion: 0.12
Nodes (48): Recover every expired RUNNING attempt (bounded batch). Returns counts., sweep_expired_leases(), _session_cookies(), _user_and_workspace(), _meeting_of(), _audit(), _committed_usage(), _dead_letter_after_one_attempt() (+40 more)

### Community 21 - "Homayar/backend/providers · 21"
Cohesion: 0.15
Nodes (31): AsanakSMSProvider, Any, Response, Asanak SMS adapter using an injected endpoint and credential., Submit one SMS to an injected Asanak endpoint. Message bodies and the API token…, ASRProvider, EmailProvider, _IdentifiedProvider (+23 more)

### Community 22 - "Homayar/backend/tests · 22"
Cohesion: 0.10
Nodes (30): BaseSettings, model_validator, Validated runtime configuration for cloud and on-premise deployments., Settings, OAuthChallenge, _backoff(), timedelta, _send_asanak_code() (+22 more)

### Community 23 - "Homayar/backend/jobs · 23"
Cohesion: 0.10
Nodes (39): JobDispatch, JobState, Meeting, ProcessingJob, Transactional outbox row: the intent to publish one job to the broker. Written…, timedelta, Back-off before the attempt that follows ``attempt`` (1-based)., RetryPolicy (+31 more)

### Community 24 - "Homayar/backend/providers · 24"
Cohesion: 0.08
Nodes (23): code_identity(), params_digest(), ProviderIdentity, What a provider *declares* determines its output (WP06-T03 checkpoints).…, Import path of the adapter class: the default provider identity., Short, stable digest of a prompt/template/transcript (never the text itself)., text_digest(), MockIntelligenceProvider (+15 more)

### Community 25 - "Homayar/backend/services · 25"
Cohesion: 0.09
Nodes (40): ApiKey, create_workspace_api_key(), list_workspace_api_keys(), delete, get, post, Request, Response (+32 more)

### Community 26 - "Homayar/backend/tests · 26"
Cohesion: 0.10
Nodes (32): apply_context(), bind_capability_context(), bind_organization_context(), bind_user_context(), clear_context(), current_context(), _is_postgres(), Any (+24 more)

### Community 27 - "Homayar/backend/routers/v1 · 27"
Cohesion: 0.15
Nodes (41): _clear_session_cookies(), _client_ip(), forgot_password(), google_callback(), google_link_start(), google_start(), login(), logout() (+33 more)

### Community 28 - "Homayar/backend/tests · 28"
Cohesion: 0.16
Nodes (42): _materialize_recording(), process_job(), Path, _stage_context(), create_object_storage(), _make_due(), Bring a scheduled retry forward instead of waiting for the back-off., Billable settlements only: the ledger also records reserve/release rows… (+34 more)

### Community 29 - "Homayar/frontend/src/components/ui · 29"
Cohesion: 0.09
Nodes (29): Avatar(), AvatarBadge(), AvatarFallback(), AvatarGroup(), AvatarGroupCount(), AvatarImage(), Badge(), badgeVariants (+21 more)

### Community 30 - ". · 30"
Cohesion: 0.12
Nodes (38): CurrentSubscriptionResponse, Plan, SubscriptionStatus, get_billing_management(), get_plan_catalog(), get_subscription(), get_usage(), get_usage_events() (+30 more)

### Community 31 - "Homayar/backend/services · 31"
Cohesion: 0.16
Nodes (37): get_settings(), datetime, utcnow(), normalize_email(), create_access_token(), decode_access_token(), hash_password(), make_refresh_token() (+29 more)

### Community 32 - "Homayar/backend/providers · 32"
Cohesion: 0.17
Nodes (30): MeetingSummary, DiarizationProvider, Diarization provider implementations., Explicit response for deployments without a diarization engine., UnavailableDiarizationProvider, ProviderBundle, MockASRProvider, ActionItem (+22 more)

### Community 33 - "Homayar/backend/services · 33"
Cohesion: 0.08
Nodes (35): load_prompt(), Prompt loader — reads prompt text files from the prompts directory., Load a prompt file by name (e.g. 'qc_fa', 'problem_en')., chat(), post, AI Copilot API router., classify_intent(), generate_response() (+27 more)

### Community 34 - "Homayar/backend/migrations/versions · 34"
Cohesion: 0.09
Nodes (29): apply_row_level_security(), disable_policy_statements(), enable_policy_statements(), ensure_app_role_statements(), existing_policy_tables(), PostgreSQL row-level security policies (WP03-T05). Single source of the DDL…, Statements enabling, forcing and (re)creating the policy of every table.…, Policy tables that exist in the current schema (migrations add tables over… (+21 more)

### Community 35 - "Homayar/backend/jobs · 35"
Cohesion: 0.11
Nodes (35): get_session_factory(), bind_service_scope(), Grant a session one narrow service scope (e.g. the dispatch outbox)., discover_workspaces(), Workspaces to sweep (read-only service scope on entitlements; ids only)., _as_utc(), cancel_job(), deadline_passed() (+27 more)

### Community 36 - "src/components · 36"
Cohesion: 0.10
Nodes (28): metadata, metadata, DICT, en, fa, Step, SuccessCopy, CartClearer() (+20 more)

### Community 37 - ". · 37"
Cohesion: 0.13
Nodes (36): delete_artifact_objects(), Best-effort object deletion; returns the keys that are now absent., delete_meeting(), download_recording(), _enforce_upload_rate_limit(), _get_meeting(), get_meeting_job(), _job_response() (+28 more)

### Community 38 - "Homayar/frontend/src/lib · 38"
Cohesion: 0.08
Nodes (25): AgentStat, Message, suggestions, CallIncident, Incident, IncidentSeverity, UserFormData, UserFormModalProps (+17 more)

### Community 39 - "src/app/about · 39"
Cohesion: 0.07
Nodes (25): AboutContent(), AboutCopy, DICT, en, fa, Value, metadata, AVATAR_GRADIENTS (+17 more)

### Community 40 - "Homayar/backend/jobs · 40"
Cohesion: 0.12
Nodes (31): bind_job_context(), as_utc(), claim_verdict(), database_now(), grant_lease(), lease_expiry(), LeaseHeartbeat, LeasePolicy (+23 more)

### Community 41 - "Homayar/backend/security · 41"
Cohesion: 0.13
Nodes (33): organization_role_has(), OrganizationPermission, Enum, str, Actor, _api_key_not_accepted(), optional_actor(), _parse_scope() (+25 more)

### Community 42 - "Homayar/backend/providers · 42"
Cohesion: 0.13
Nodes (28): asyncio, _compatible_endpoint(), create_asr_provider(), create_diarization_provider(), create_intelligence_provider(), create_provider_bundle(), _provider_name(), ProviderFactory (+20 more)

### Community 43 - "Homayar/backend/schemas · 43"
Cohesion: 0.14
Nodes (31): ActionStatus, create_share(), edit_share_recipients(), list_shares(), _meeting_or_404(), Meeting, patch, post (+23 more)

### Community 44 - "Homayar/backend/services · 44"
Cohesion: 0.19
Nodes (27): Entitlement, Append-only usage ledger (WP06-T06). One row per reservation transition…, ReservationState, UsageEvent, UsageReservation, EntitlementRef, Usage ledger reconciliation (WP06-T06). Processing is delivered at least once,…, ReconciliationOutcome (+19 more)

### Community 45 - "Homayar/backend/routers/v1 · 45"
Cohesion: 0.16
Nodes (32): _action_or_404(), create_action(), delete_action(), export_meeting(), get_summary(), get_transcript(), _like_pattern(), list_actions() (+24 more)

### Community 46 - "Homayar/backend/services · 46"
Cohesion: 0.08
Nodes (25): Celery, make_celery(), Celery app — background job processing for Homayar. Queue contract (WP06-T01):…, Run migrations in 'offline' mode. This configures the context with just a URL…, Run migrations in 'online' mode. In this scenario we need to create an Engine…, run_migrations_offline(), run_migrations_online(), Validation and tenant namespacing for client idempotency keys. (+17 more)

### Community 47 - "Homayar/backend/services · 47"
Cohesion: 0.12
Nodes (29): aggregated_issues(), call_problems(), call_qc_details(), issues_by_category(), kpis(), get, qc_trend(), Dashboard API router — post-call metrics. (+21 more)

### Community 48 - "Homayar/backend/services · 48"
Cohesion: 0.30
Nodes (27): DeclarativeBase, Base, Membership, Notification, Subscription, TimestampMixin, User, WebhookDelivery (+19 more)

### Community 49 - "Homayar/backend/services · 49"
Cohesion: 0.11
Nodes (28): UserCreate, Update call processing status., Store transcript for a call., store_transcript(), update_call_status(), authenticate_user(), create_user(), create_user_from_voip() (+20 more)

### Community 50 - "Homayar/backend/tests · 50"
Cohesion: 0.11
Nodes (22): FakeConditionalS3Client, FakeS3Client, FakeS3NotFound, PreconditionFailed, Any, Exception, parametrize, Path (+14 more)

### Community 51 - "Homayar/backend/providers · 51"
Cohesion: 0.14
Nodes (12): ProviderError, Exception, Provider failure safe to expose to application orchestration., ASRRequest, ASRResult, ASRSegment, LocalWhisperProvider, Any (+4 more)

### Community 52 - "Homayar/backend/tests · 52"
Cohesion: 0.20
Nodes (27): _csrf(), _grant_organization_role(), _insert_membership(), _organization_id(), _phone_login(), postgres, WorkspaceRole, WP03-T06 follow-up: organization audience, phone recipients, recipient editing.… (+19 more)

### Community 53 - "src/components/platform · 53"
Cohesion: 0.10
Nodes (22): Agent, AiAgentsContent(), AiAgentsCopy, DICT, en, fa, TONES, metadata (+14 more)

### Community 54 - ". · 54"
Cohesion: 0.19
Nodes (24): AuditEventListResponse, How far a member/viewer sees inside a workspace (owners/admins see all)., RecordScope, list_audit_events(), patch_workspace_settings(), datetime, get, HTTPException (+16 more)

### Community 55 - "Homayar/backend/database · 55"
Cohesion: 0.15
Nodes (22): Engine, FastAPI, build_engine(), configure_database(), create_schema_for_test_or_development(), get_db(), get_engine(), Session (+14 more)

### Community 56 - "Homayar/backend/tests · 56"
Cohesion: 0.18
Nodes (9): login_user(), TestClient, register_user(), TestClient, test_deactivated_user_session_is_rejected(), test_refresh_session_is_stored_as_hash(), TestEmailAuthentication, TestPhoneAuthentication (+1 more)

### Community 57 - "src/app/solutions · 57"
Cohesion: 0.08
Nodes (15): DICT, en, fa, Industry, INDUSTRY_STYLES, SolutionsContent(), SolutionsCopy, Team (+7 more)

### Community 58 - "Homayar/backend/tests · 58"
Cohesion: 0.16
Nodes (24): assert_production_posture(), describe_current_role(), RuntimeError, Effective privileges of ``current_user`` and RLS state of ``meetings``., Refuse to serve when the runtime role could bypass tenant isolation., RowLevelSecurityPostureError, Fail closed in production when the runtime role could bypass RLS., _verify_database_posture() (+16 more)

### Community 59 - "Homayar/backend/jobs · 59"
Cohesion: 0.12
Nodes (21): DispatchState, Transactional-outbox state of a job's broker publication (WP06-T02)., ArtifactEvidence, check_all(), check_workspace(), discover_workspaces(), datetime, ProcessingJob (+13 more)

### Community 60 - "Homayar/backend/tests · 60"
Cohesion: 0.14
Nodes (18): APIRoute, test_api_key_scope_document_matches_code(), _cookies(), _doc_block(), _headers(), _insert_meeting(), _listed_titles(), _Persona (+10 more)

### Community 61 - "Homayar/backend/services · 61"
Cohesion: 0.20
Nodes (21): AuthIdentity, IdentityProvider, LoginSession, OneTimeToken, OtpChallenge, normalize_iranian_mobile(), AuthenticationError, InvalidOneTimeTokenError (+13 more)

### Community 62 - "Homayar/backend/tests · 62"
Cohesion: 0.20
Nodes (19): test_rolled_back_upload_leaves_no_job_and_no_intent(), _processed_meeting(), test_share_links_are_hashed_expiring_auth_optional_and_revocable(), _write_headers(), _session_cookies(), test_meeting_reads_files_and_mutations_are_tenant_scoped(), _workspace_for_user(), _cookies() (+11 more)

### Community 63 - "Homayar/frontend/src/lib · 63"
Cohesion: 0.13
Nodes (23): MembersPage, ADMIN_ASSIGNABLE_ROLES, ALL_ASSIGNABLE_ROLES, canManageTarget(), formatDate(), InvitationManagement(), InvitationRow(), MemberDirectory() (+15 more)

### Community 64 - "infra/scripts/lib · 64"
Cohesion: 0.18
Nodes (15): backup-minio.sh script, backup-postgres.sh script, backup_relative_path(), canonical_existing_path(), compose(), compose_onprem(), die(), require_command() (+7 more)

### Community 65 - "src/components/site · 65"
Cohesion: 0.12
Nodes (18): CanvasErrorBoundary, hasWebGL(), HeroCanvas(), prefersReducedMotion(), TiltCard(), useActiveInView(), useMedia(), useMounted() (+10 more)

### Community 66 - "Homayar/backend/services · 66"
Cohesion: 0.19
Nodes (22): AuditEvent, AuditEventPage, _decode_audit_cursor(), _encode_audit_cursor(), get_workspace_settings(), InvalidAuditCursor, InvalidAuditRange, InvalidWorkspaceSettings (+14 more)

### Community 67 - "src/lib · 67"
Cohesion: 0.18
Nodes (20): bookingSchema, isUniqueViolation(), POST(), buildICS(), CalendarEvent, dateKeyInTz(), googleCalendarUrl(), HORIZON_DAYS (+12 more)

### Community 68 - "Homayar/backend/database · 68"
Cohesion: 0.18
Nodes (20): BusinessInsight, Call, CallAgent, CallListItem, CallReport, ChatMessage, ChatRequest, DashboardKpis (+12 more)

### Community 69 - "Homayar/backend/services · 69"
Cohesion: 0.14
Nodes (18): ldap_sync(), ldap_test(), get, post, LDAP sync router — AD user synchronization endpoints., Test LDAP connectivity., Full sync: fetch users from AD and upsert into Neo4j., authenticate_ldap() (+10 more)

### Community 70 - "src/lib · 70"
Cohesion: 0.22
Nodes (15): GET(), contactSchema, POST(), demoSchema, POST(), leads, getDb(), hasDb() (+7 more)

### Community 71 - "Homayar/backend/tests · 71"
Cohesion: 0.17
Nodes (17): email_outbox(), fixture, Path, Session, pytest_report_header(), pytest_sessionstart(), settings(), sms_outbox() (+9 more)

### Community 72 - "src/app/contact · 72"
Cohesion: 0.12
Nodes (14): ContactContent(), ContactCopy, DICT, en, Expectation, fa, metadata, ContactForm() (+6 more)

### Community 73 - "Homayar/backend/schemas · 73"
Cohesion: 0.16
Nodes (15): computed_field, DispatchResponse, LeaseResponse, MeetingDetailResponse, MeetingListResponse, MeetingResponse, MeetingUpdateRequest, BaseModel (+7 more)

### Community 74 - "Homayar/backend/routers/v1 · 74"
Cohesion: 0.24
Nodes (18): access_share(), _authorized_grant(), download_shared_recording(), export_share(), ActionItem, delete, get, Request (+10 more)

### Community 75 - "Homayar/backend/routers · 75"
Cohesion: 0.15
Nodes (16): _extract_extension(), _normalize_event(), BaseModel, post, Request, VOIP webhook router — receives call events from Asterisk/3CX/FreePBX., Receive multiple call events at once (e.g., daily sync from VOIP system)., Auto-detect VOIP source and normalize to standard format. (+8 more)

### Community 76 - "Homayar/backend/tests · 76"
Cohesion: 0.27
Nodes (16): api_key_digest(), Homayar backend test suite., _allow_uploads(), _bearer(), _error(), _key_upload(), _mint(), _owner() (+8 more)

### Community 77 - "src/components/site · 77"
Cohesion: 0.12
Nodes (6): metadata, content(), Copy, DemoExperience(), Scenario, Turn

### Community 78 - "src/app/platform · 78"
Cohesion: 0.13
Nodes (12): DICT, en, fa, Item, PlatformContent(), PlatformCopy, SECURITY_ICONS, Step (+4 more)

### Community 79 - "Homayar/frontend/src/lib · 79"
Cohesion: 0.19
Nodes (11): RequireAuth(), AppLayout(), Header(), navItems, Sidebar(), useAuth(), I18nContext, I18nContextValue (+3 more)

### Community 80 - "Homayar/backend/tests · 80"
Cohesion: 0.27
Nodes (14): _cookies(), email_outbox(), login_user(), _mount_router(), fixture, Path, TestClient, _register_login() (+6 more)

### Community 81 - "src/components/site · 81"
Cohesion: 0.15
Nodes (9): metadata, Booked, BookingExperience(), dateKey(), Lang, STR, BUSINESS_TZ, Slot (+1 more)

### Community 82 - "src/app/privacy · 82"
Cohesion: 0.21
Nodes (7): DICT, PrivacyContent(), metadata, DICT, TermsContent(), metadata, renderMarkdown()

### Community 83 - "Homayar/backend/routers · 83"
Cohesion: 0.29
Nodes (11): entities(), entity_detail(), neighborhood(), get, Graph Intelligence API router., stats(), get_entities(), get_entity_detail() (+3 more)

### Community 84 - "infra/ci/tests · 84"
Cohesion: 0.22
Nodes (12): commit_all(), GIT_AUTHOR_EMAIL, GIT_AUTHOR_NAME, GIT_COMMITTER_EMAIL, GIT_COMMITTER_NAME, GIT_CONFIG_NOSYSTEM, HOME, mkrepo() (+4 more)

### Community 85 - "src/app/success · 85"
Cohesion: 0.29
Nodes (10): checkoutSchema, POST(), POST(), SuccessContent(), SuccessSummary, getSessionSummary(), metadata, SuccessPage() (+2 more)

### Community 86 - "Homayar/backend/services · 86"
Cohesion: 0.21
Nodes (11): _send_reset_message(), _send_verification_message(), Email service — SMTP via FastAPI-Mail + Mailpit for dev., Synchronous email send for Celery tasks., Send password reset email with token., Send email verification with token., Send email using configured SMTP server., send_email() (+3 more)

### Community 87 - "Homayar/backend/tests · 87"
Cohesion: 0.30
Nodes (10): _add_membership(), admin_client(), _error_code(), fixture, WorkspaceRole, _register_and_login(), test_audit_events_are_tenant_scoped_filtered_sanitized_and_paginated(), test_missing_workspace_uses_stable_not_found_error() (+2 more)

### Community 88 - "Homayar/backend/services · 88"
Cohesion: 0.20
Nodes (11): ByteRange, _content_disposition(), deliver_recording(), _iter_reader(), parse_range(), HTTPException, Response, Authorize first, then call this with the exact recording to deliver. (+3 more)

### Community 89 - "Homayar/backend/tests · 89"
Cohesion: 0.35
Nodes (10): billing_client(), fixture, TestClient, _register_session(), _session_cookies(), test_billing_catalog_subscription_and_usage_are_tenant_scoped(), test_billing_rbac_and_manual_management_are_explicitly_read_only(), test_current_subscription_can_be_empty_without_fabricating_a_plan() (+2 more)

### Community 90 - "src/db · 90"
Cohesion: 0.21
Nodes (10): blogPosts, bookings, caseStudies, categories, checkoutSessions, demoRequests, orderItems, orders (+2 more)

### Community 91 - "Homayar/backend/services · 91"
Cohesion: 0.36
Nodes (10): ExportAction, ExportSegment, MeetingExport, Deterministic meeting export renderers for text-based formats., _render_json(), render_meeting_export(), _render_subtitles(), _render_text() (+2 more)

### Community 92 - "Homayar/backend/services · 92"
Cohesion: 0.27
Nodes (7): _MemoryCounter, RuntimeError, ValueError, RateLimiter, RateLimitExceeded, RateLimitUnavailable, Distributed fixed-window limiter with an isolated test/development fallback.

### Community 93 - "scripts · 93"
Cohesion: 0.20
Nodes (10): arr, byCode, c, codeIdx, decode(), deep(), e, raw (+2 more)

### Community 94 - "Homayar/backend/providers · 94"
Cohesion: 0.20
Nodes (3): AsyncClient, field_validator, SecretStr

### Community 95 - "Homayar/backend/services · 95"
Cohesion: 0.27
Nodes (7): Path, UploadFile, ValueError, Bounded staging for multipart uploads before media inspection., stage_upload(), StagedUpload, UploadRejectedError

### Community 96 - "src/lib · 96"
Cohesion: 0.31
Nodes (9): Attachment, COPY, escapeHtml(), formatWhen(), fromAddress(), hasEmail(), Lang, send() (+1 more)

### Community 97 - "Homayar/backend/services · 97"
Cohesion: 0.31
Nodes (8): HexColor, get_call_detail(), Get full call detail with transcript, QC, problems, insights., generate_call_pdf(), PDF report generation for call reports., Generate a PDF report for a call. Returns bytes., _score_color(), _severity_color()

### Community 98 - "Homayar/backend/tests · 98"
Cohesion: 0.28
Nodes (8): Test helper for isolated environment overrides., reset_settings_cache(), MonkeyPatch, PostgreSQL-only guarantees (WP01-T05). These tests are skipped on the SQLite…, ``alembic upgrade head`` on an empty PostgreSQL database must produce exactly…, Two workers must never claim the same job: a row held under ``FOR UPDATE`` by…, test_job_claim_skips_rows_locked_by_another_worker(), test_migrations_upgrade_head_matches_models()

### Community 99 - "Homayar/backend/services · 99"
Cohesion: 0.25
Nodes (8): Who may open a share link (WP03-T06)., ShareAudience, issue_share(), Meeting, Policy checks at creation time (the caller already holds share:manage)., share_token_hash(), ShareSpec, validate_share_spec()

### Community 101 - "infra/ci · 101"
Cohesion: 0.36
Nodes (6): config_error(), info(), check-source-contract.sh script, usage(), violation(), warn()

### Community 102 - "Homayar/backend/database · 102"
Cohesion: 0.32
Nodes (4): Neo4jConnection, Execute multiple write queries in a single transaction. queries_and_params:…, Store full analysis results for a call in a single transaction., store_analysis()

### Community 103 - "Homayar/backend/tests · 103"
Cohesion: 0.33
Nodes (6): Dispose configured state. Intended for isolated tests., reset_database_state(), client(), MonkeyPatch, Deployment shape: DATABASE_URL logs in as a member of homayar_app, no SET ROLE., test_api_works_when_logged_in_directly_as_the_low_privilege_role()

### Community 104 - "Homayar/backend/migrations/versions · 104"
Cohesion: 0.38
Nodes (6): backfill_owner_memberships(), downgrade(), _is_postgres(), Organization memberships and scope grants (WP03-T03, ADR 0012). Revision ID:…, Grant the organization owner role to the one active owner of each organization.…, upgrade()

### Community 105 - "Homayar/backend/tests · 105"
Cohesion: 0.33
Nodes (3): FaultyStorage, BinaryIO, Delegating object storage that fails on demand. ``fail_reads_per_key`` fails…

### Community 106 - "Homayar/backend/database · 106"
Cohesion: 0.50
Nodes (4): create_calls(), Seed script — post-call voice intelligence domain., Generate call records from templates., seed()

### Community 107 - "Homayar/backend/tests · 107"
Cohesion: 0.40
Nodes (3): parametrize, No fabricated analytics may exist on any importable backend path…, test_fabricated_analytics_modules_are_gone()

### Community 111 - ". · 111"
Cohesion: 0.50
Nodes (3): csp, nextConfig, securityHeaders

### Community 112 - "scripts · 112"
Cohesion: 0.50
Nodes (3): issues, PAGES, VIEWPORTS

### Community 113 - "scripts · 113"
Cohesion: 0.67
Nodes (3): main(), readJson(), root

## Knowledge Gaps
- **285 isolated node(s):** `_FormatProfile`, `queryClient`, `AgentStat`, `waveHeights`, `AuthCardProps` (+280 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **13 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `process_job()` connect `Homayar/backend/tests · 28` to `Homayar/backend/providers · 32`, `Homayar/backend/storage · 1`, `Homayar/backend/jobs · 6`, `Homayar/backend/jobs · 7`, `Homayar/backend/jobs · 40`, `Homayar/backend/providers · 42`, `Homayar/backend/providers · 51`, `Homayar/backend/tests · 20`, `Homayar/backend/tests · 22`, `Homayar/backend/providers · 24`, `Homayar/backend/services · 31`?**
  _High betweenness centrality (0.001) - this node is a cross-community bridge._
- **Why does `ProviderBundle` connect `Homayar/backend/providers · 32` to `Homayar/backend/providers · 24`, `Homayar/backend/providers · 51`, `Homayar/backend/providers · 21`, `Homayar/backend/tests · 22`?**
  _High betweenness centrality (0.001) - this node is a cross-community bridge._
- **Why does `process_meeting_job()` connect `Homayar/backend/jobs · 16` to `Homayar/backend/tests · 28`?**
  _High betweenness centrality (0.001) - this node is a cross-community bridge._
- **What connects `_FormatProfile`, `queryClient`, `AgentStat` to the rest of the system?**
  _285 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Homayar/frontend/src/lib · 0` be split into smaller, more focused modules?**
  _Cohesion score 0.03231000429368828 - nodes in this community are weakly interconnected._
- **Should `Homayar/backend/storage · 1` be split into smaller, more focused modules?**
  _Cohesion score 0.04376100799349682 - nodes in this community are weakly interconnected._
- **Should `Homayar/backend/media · 2` be split into smaller, more focused modules?**
  _Cohesion score 0.056235134216785596 - nodes in this community are weakly interconnected._