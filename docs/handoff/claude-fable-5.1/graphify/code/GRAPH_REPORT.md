Local static code map; consult diagnostics.json and lossless raw extraction.json. No feature completion or definition-resolution guarantee.

# Graph Report - /Users/emperor/Documents/AI/Homayar.com/.claude/worktrees/homayar-commercial-v1-a266f5  (2026-09-10)

## Corpus Check
- 310 files · ~195,838 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3317 nodes · 11309 edges · 138 communities (125 shown, 13 thin omitted)
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 1324 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Homayar/backend/media · 0
- Homayar/frontend/src/types · 1
- Homayar/backend/services · 2
- Homayar/backend/jobs · 3
- Homayar/frontend/src/components/platform · 4
- src/lib · 5
- Homayar/frontend/src/lib · 6
- Homayar/backend/services · 7
- src/lib · 8
- Homayar/frontend/src/types · 9
- scripts · 10
- Homayar/backend/jobs · 11
- Homayar/backend/jobs · 12
- src/lib · 13
- Homayar/frontend/src/components/auth · 14
- Homayar/backend/jobs · 15
- Homayar/backend/routers/v1 · 16
- Homayar/backend/tests · 17
- Homayar/backend/tests · 18
- Homayar/backend/providers · 19
- Homayar/frontend/src/components/ui · 20
- Homayar/backend/tests · 21
- Homayar/frontend/src/lib · 22
- Homayar/backend/tests · 23
- . · 24
- Homayar/backend/database · 25
- Homayar/backend/services · 26
- Homayar/backend/services · 27
- Homayar/backend/tests · 28
- src/components · 29
- Homayar/backend/tests · 30
- Homayar/backend/services · 31
- src/app/about · 32
- Homayar/backend/migrations/versions · 33
- Homayar/backend/routers/v1 · 34
- Homayar/backend/tests · 35
- Homayar/backend/jobs · 36
- Homayar/backend/services · 37
- Homayar/backend/providers · 38
- Homayar/backend/services · 39
- Homayar/backend/tests · 40
- Homayar/backend/services · 41
- src/lib · 42
- Homayar/backend/providers · 43
- Homayar/backend/jobs · 44
- Homayar/backend/tests · 45
- Homayar/backend/schemas · 46
- Homayar/backend/services · 47
- Homayar/backend/routers/v1 · 48
- Homayar/frontend/src/components/platform · 49
- . · 50
- Homayar/backend/schemas · 51
- Homayar/backend/services · 52
- src/app/solutions · 53
- Homayar/backend/tests · 54
- Homayar/backend/schemas · 55
- Homayar/backend/storage · 56
- infra/scripts/lib · 57
- src/components/site · 58
- Homayar/backend/tests · 59
- Homayar/backend/tests · 60
- Homayar/backend/services · 61
- Homayar/backend/storage · 62
- src/lib · 63
- Homayar/backend/database · 64
- Homayar/backend/security · 65
- Homayar/backend/database · 66
- Homayar/backend/storage · 67
- src/lib · 68
- Homayar/backend/storage · 69
- src/app/contact · 70
- Homayar/backend/routers · 71
- src/components/site · 72
- . · 73
- Homayar/backend/security · 74
- src/app/platform · 75
- Homayar/backend/storage · 76
- src/app/ai-agents · 77
- src/lib · 78
- Homayar/backend/providers · 79
- src/components/site · 80
- Homayar/backend/tests · 81
- Homayar/backend/services · 82
- Homayar/backend/tests · 83
- Homayar/backend/routers · 84
- infra/ci/tests · 85
- src/app/success · 86
- Homayar/backend/providers · 87
- Homayar/backend/tests · 88
- Homayar/backend/tests · 89
- src/db · 90
- Homayar/backend/services · 91
- Homayar/backend/services · 92
- Homayar/frontend/src/components/users · 93
- scripts · 94
- src/components/platform · 95
- Homayar/backend/services · 96
- Homayar/backend/storage · 97
- Homayar/backend/tests · 98
- src/lib · 99
- Homayar/backend/services · 100
- Homayar/backend/providers · 101
- Homayar/backend/services · 102
- Homayar/backend/storage · 103
- infra/ci · 104
- Homayar/backend/jobs · 105
- Homayar/backend/database · 106
- Homayar/backend/schemas · 107
- Homayar/backend/providers · 108
- Homayar/backend/services · 109
- Homayar/backend/jobs · 110
- Homayar/backend/database · 111
- Homayar/backend/tests · 112
- Homayar/backend/migrations/versions · 113
- Homayar/backend/migrations/versions · 114
- . · 115
- scripts · 116
- scripts · 117
- Homayar/backend/storage · 122
- Homayar/frontend/src/test · 123
- . · 124
- Homayar/backend/schemas · 125
- Homayar/backend/tests · 126
- infra/ci · 127
- . · 128
- . · 129
- scripts · 130

## God Nodes (most connected - your core abstractions)
1. `Settings` - 276 edges
2. `inspection_session_factory()` - 125 edges
3. `get_settings()` - 90 edges
4. `process_job()` - 81 edges
5. `_queued_upload()` - 77 edges
6. `WorkspaceContext` - 73 edges
7. `Meeting` - 72 edges
8. `ProcessingJob` - 70 edges
9. `JobState` - 66 edges
10. `ASRResult` - 66 edges

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

## Communities (138 total, 13 thin omitted)

### Community 0 - "Homayar/backend/media · 0"
Cohesion: 0.06
Nodes (80): CompletedProcess, Secure media inspection primitives used before object publication., AudioStreamMetadata, _build_ffprobe_command(), _codec_allowed(), EmptyMediaError, _ensure_file_unchanged(), ExtensionContentMismatchError (+72 more)

### Community 1 - "Homayar/frontend/src/types · 1"
Cohesion: 0.03
Nodes (102): SettingsPanel(), acceptInvitation(), changeMemberRole(), downloadPayload(), downloadRecording(), exportMeeting(), filenameFromContentDisposition(), getBillingUsage() (+94 more)

### Community 2 - "Homayar/backend/services · 2"
Cohesion: 0.05
Nodes (77): Engine, FastAPI, get_settings(), build_engine(), configure_database(), create_schema_for_test_or_development(), get_db(), get_engine() (+69 more)

### Community 3 - "Homayar/backend/jobs · 3"
Cohesion: 0.07
Nodes (76): CheckpointState, ProviderUsage, Lifecycle of one stage checkpoint / artifact (WP06-T03). ``PENDING``: the…, A committed stage result with the provenance needed to reuse it (WP06-T03).…, Recording, StageCheckpoint, abandon_intent(), artifact_token() (+68 more)

### Community 4 - "Homayar/frontend/src/components/platform · 4"
Cohesion: 0.07
Nodes (52): SearchPage, Header(), navItems, Sidebar(), InvitationAcceptPage(), idempotencyKey(), MeetingsPage(), ADMIN_ASSIGNABLE_ROLES (+44 more)

### Community 5 - "src/lib · 5"
Cohesion: 0.07
Nodes (54): BILLING_EXPLANATIONS, ProductDetail(), splitOutcome(), Strings, en, fa, PLAN_ORDER, PlanCard() (+46 more)

### Community 6 - "Homayar/frontend/src/lib · 6"
Cohesion: 0.07
Nodes (43): AgentStat, Message, suggestions, CallIncident, Incident, IncidentSeverity, api, API_BASE_URL (+35 more)

### Community 7 - "Homayar/backend/services · 7"
Cohesion: 0.10
Nodes (51): A named recipient of a ``recipients`` share, bound to a user once proven., Who may open a share link (WP03-T06)., ShareAudience, ShareLink, ShareRecipient, access_share(), _authorized_grant(), create_share() (+43 more)

### Community 8 - "src/lib · 8"
Cohesion: 0.08
Nodes (42): BlogIndexPage(), formatDate(), metadata, MONTHS, BlogPostPage(), formatDate(), generateMetadata(), generateStaticParams() (+34 more)

### Community 9 - "Homayar/frontend/src/types · 9"
Cohesion: 0.06
Nodes (38): CallsPage(), severityConfig, DashboardPage(), SENTIMENT_COLORS, KpiCard(), KpiCardProps, labelColors, LabelConfig (+30 more)

### Community 10 - "scripts · 10"
Cohesion: 0.15
Nodes (51): ACCENT_KEYS, ACCENTS, arcPath(), arcPoint(), background(), barChart(), card(), chooseAccent() (+43 more)

### Community 11 - "Homayar/backend/jobs · 11"
Cohesion: 0.09
Nodes (47): Event, get_session_factory(), sessionmaker, MeetingSpeaker, ProcessingStageRun, Truthful per-stage outcome of one processing attempt (WP04-T01)., One stage of one processing attempt with its typed outcome and provenance., StageState (+39 more)

### Community 12 - "Homayar/backend/jobs · 12"
Cohesion: 0.08
Nodes (42): timedelta, Back-off before the attempt that follows ``attempt`` (1-based)., RetryPolicy, as_utc(), claim_verdict(), database_now(), grant_lease(), lease_expiry() (+34 more)

### Community 13 - "src/lib · 13"
Cohesion: 0.06
Nodes (27): DICT, PrivacyContent(), metadata, DICT, TermsContent(), metadata, LangSwitcher(), featureIcons (+19 more)

### Community 14 - "Homayar/frontend/src/components/auth · 14"
Cohesion: 0.09
Nodes (38): App(), ForgotPasswordPage, InvitationAcceptPage, LoginPage, MeetingsPage, MembersPage, PhoneOtpRequestPage, PhoneOtpVerifyPage (+30 more)

### Community 15 - "Homayar/backend/jobs · 15"
Cohesion: 0.09
Nodes (41): DispatchState, JobDispatch, JobState, Meeting, ProcessingJob, str, Transactional-outbox state of a job's broker publication (WP06-T02)., Transactional outbox row: the intent to publish one job to the broker. Written… (+33 more)

### Community 16 - "Homayar/backend/routers/v1 · 16"
Cohesion: 0.14
Nodes (44): _clear_session_cookies(), _client_ip(), forgot_password(), google_callback(), google_link_start(), google_start(), login(), logout() (+36 more)

### Community 17 - "Homayar/backend/tests · 17"
Cohesion: 0.17
Nodes (31): ActionItem, AuditEvent, MeetingSummary, ASRProvider, DiarizationProvider, Diarization provider implementations., Explicit response for deployments without a diarization engine., UnavailableDiarizationProvider (+23 more)

### Community 18 - "Homayar/backend/tests · 18"
Cohesion: 0.10
Nodes (44): _backoff(), dispatch_job(), timedelta, Publish one pending row now if it is due; safe to call repeatedly., Publish every due PENDING row (bounded batch). Returns outcome counts., sweep_dispatch_outbox(), process_job(), Path (+36 more)

### Community 19 - "Homayar/backend/providers · 19"
Cohesion: 0.17
Nodes (29): EmailProvider, _IdentifiedProvider, IntelligenceProvider, MockProviderDisabledError, ABC, Provider interfaces and sanitized execution errors., Mixin: the identity a checkpoint records for the adapter (override it)., SMSProvider (+21 more)

### Community 20 - "Homayar/frontend/src/components/ui · 20"
Cohesion: 0.09
Nodes (29): Avatar(), AvatarBadge(), AvatarFallback(), AvatarGroup(), AvatarGroupCount(), AvatarImage(), Badge(), badgeVariants (+21 more)

### Community 21 - "Homayar/backend/tests · 21"
Cohesion: 0.09
Nodes (25): AsanakSMSProvider, Any, AsyncClient, Response, Asanak SMS adapter using an injected endpoint and credential., Submit one SMS to an injected Asanak endpoint. Message bodies and the API token…, ProviderIssue, SMSRequest (+17 more)

### Community 22 - "Homayar/frontend/src/lib · 22"
Cohesion: 0.08
Nodes (39): MeetingDetailPage, DetailTab, MeetingDetailPage(), STAGE_LABELS, timestamp(), formatDate(), formatDuration(), createAction() (+31 more)

### Community 23 - "Homayar/backend/tests · 23"
Cohesion: 0.11
Nodes (24): BaseSettings, model_validator, Validated runtime configuration for cloud and on-premise deployments., Settings, OAuthChallenge, AccountLinkRequiredError, complete_google_oauth(), GoogleOAuthError (+16 more)

### Community 24 - ". · 24"
Cohesion: 0.12
Nodes (39): accept_member_invitation(), delete_member(), _invitation_response(), invite_member(), list_invitations(), list_members(), _member_response(), delete (+31 more)

### Community 25 - "Homayar/backend/database · 25"
Cohesion: 0.12
Nodes (35): DeclarativeBase, ApiKey, Base, Entitlement, new_id(), Notification, Organization, Plan (+27 more)

### Community 26 - "Homayar/backend/services · 26"
Cohesion: 0.15
Nodes (35): utcnow(), Validation and tenant namespacing for client idempotency keys., scoped_idempotency_key(), validate_idempotency_key(), normalize_email(), create_access_token(), hash_password(), make_refresh_token() (+27 more)

### Community 27 - "Homayar/backend/services · 27"
Cohesion: 0.08
Nodes (35): load_prompt(), Prompt loader — reads prompt text files from the prompts directory., Load a prompt file by name (e.g. 'qc_fa', 'problem_en')., chat(), post, AI Copilot API router., classify_intent(), generate_response() (+27 more)

### Community 28 - "Homayar/backend/tests · 28"
Cohesion: 0.17
Nodes (38): _stage_context(), create_object_storage(), _make_due(), Bring a scheduled retry forward instead of waiting for the back-off., Billable settlements only: the ledger also records reserve/release rows…, _settlements(), broker(), _checkpoints() (+30 more)

### Community 29 - "src/components · 29"
Cohesion: 0.09
Nodes (29): metadata, metadata, DICT, en, fa, Step, SuccessCopy, CartClearer() (+21 more)

### Community 30 - "Homayar/backend/tests · 30"
Cohesion: 0.12
Nodes (37): expected_counters(), _live_reservation_ids(), (used, reserved) implied by the reservation rows of one entitlement., Release orphaned reservations and check (optionally repair) the counters., reconcile_entitlement(), pytest_collection_modifyitems(), configured_database_url(), inspection_session_factory() (+29 more)

### Community 31 - "Homayar/backend/services · 31"
Cohesion: 0.15
Nodes (34): Effective-dated operational role of a workspace member inside a team., Team, TeamAssignment, TeamRole, Resolved record-level visibility for one actor inside one workspace., May this actor attribute a record to ``team_id``?, RecordAccess, _as_utc() (+26 more)

### Community 32 - "src/app/about · 32"
Cohesion: 0.07
Nodes (25): AboutContent(), AboutCopy, DICT, en, fa, Value, metadata, AVATAR_GRADIENTS (+17 more)

### Community 33 - "Homayar/backend/migrations/versions · 33"
Cohesion: 0.10
Nodes (28): apply_row_level_security(), disable_policy_statements(), enable_policy_statements(), ensure_app_role_statements(), existing_policy_tables(), PostgreSQL row-level security policies (WP03-T05). Single source of the DDL…, Policy tables that exist in the current schema (migrations add tables over…, Idempotently create the app role, grants and policies (PostgreSQL only).… (+20 more)

### Community 34 - "Homayar/backend/routers/v1 · 34"
Cohesion: 0.16
Nodes (34): _action_or_404(), create_action(), delete_action(), export_meeting(), get_summary(), get_transcript(), _like_pattern(), list_actions() (+26 more)

### Community 35 - "Homayar/backend/tests · 35"
Cohesion: 0.13
Nodes (17): login_user(), TestClient, register_user(), TestClient, test_deactivated_user_session_is_rejected(), test_refresh_session_is_stored_as_hash(), TestEmailAuthentication, TestPhoneAuthentication (+9 more)

### Community 36 - "Homayar/backend/jobs · 36"
Cohesion: 0.13
Nodes (31): MeetingStatus, _as_utc(), cancel_job(), deadline_passed(), FailureClass, park_job(), datetime, Meeting (+23 more)

### Community 37 - "Homayar/backend/services · 37"
Cohesion: 0.22
Nodes (33): Membership, Workspace, WorkspaceInvitation, WorkspaceRole, can_grant(), can_manage_member(), WorkspaceRole, A caller manages a membership only if it could have granted its role. Self-… (+25 more)

### Community 38 - "Homayar/backend/providers · 38"
Cohesion: 0.14
Nodes (27): _compatible_endpoint(), create_asr_provider(), create_diarization_provider(), create_intelligence_provider(), create_provider_bundle(), _provider_name(), ProviderFactory, AsyncClient (+19 more)

### Community 39 - "Homayar/backend/services · 39"
Cohesion: 0.17
Nodes (27): Append-only usage ledger (WP06-T06). One row per reservation transition…, ReservationState, UsageEvent, UsageReservation, discover_entitlements(), EntitlementRef, Usage ledger reconciliation (WP06-T06). Processing is delivered at least once,…, Entitlements across tenants (read-only service scope; no content). (+19 more)

### Community 40 - "Homayar/backend/tests · 40"
Cohesion: 0.18
Nodes (32): _session_cookies(), _meeting_of(), _audit(), broker(), _committed_usage(), _dead_letter_after_one_attempt(), _edit_job(), _error() (+24 more)

### Community 41 - "Homayar/backend/services · 41"
Cohesion: 0.12
Nodes (29): aggregated_issues(), call_problems(), call_qc_details(), issues_by_category(), kpis(), get, qc_trend(), Dashboard API router — post-call metrics. (+21 more)

### Community 42 - "src/lib · 42"
Cohesion: 0.11
Nodes (21): inter, metadata, spaceGrotesk, vazirmatn, Footer(), Header(), FullLogo(), Logo() (+13 more)

### Community 43 - "Homayar/backend/providers · 43"
Cohesion: 0.12
Nodes (13): asyncio, ProviderError, Exception, Provider failure safe to expose to application orchestration., ASRRequest, ASRResult, LocalWhisperProvider, Any (+5 more)

### Community 44 - "Homayar/backend/jobs · 44"
Cohesion: 0.09
Nodes (26): enqueue_processing_job(), Best-effort publish without outbox bookkeeping (smoke tests, tooling)., Durable background job orchestration., A broker message may *assert* a workspace; the persisted job decides. Reads…, verify_job_payload(), process_meeting_job(), Periodic recovery: publish every due outbox row (bounded batch)., Periodic recovery: fence and requeue abandoned RUNNING attempts. (+18 more)

### Community 45 - "Homayar/backend/tests · 45"
Cohesion: 0.15
Nodes (29): Recover every expired RUNNING attempt (bounded batch). Returns counts., sweep_expired_leases(), broker(), _claim(), _completed_records(), _count(), _expire_lease(), _forged() (+21 more)

### Community 46 - "Homayar/backend/schemas · 46"
Cohesion: 0.14
Nodes (28): _enforce_upload_rate_limit(), _meeting_response(), Exception, HTTPException, patch, post, UploadFile, _require_verified_identity() (+20 more)

### Community 47 - "Homayar/backend/services · 47"
Cohesion: 0.11
Nodes (28): UserCreate, Update call processing status., Store transcript for a call., store_transcript(), update_call_status(), authenticate_user(), create_user(), create_user_from_voip() (+20 more)

### Community 48 - "Homayar/backend/routers/v1 · 48"
Cohesion: 0.18
Nodes (28): _as_utc(), assign_member(), _assignment_response(), create_workspace_team(), list_workspace_teams(), patch_team(), datetime, delete (+20 more)

### Community 49 - "Homayar/frontend/src/components/platform · 49"
Cohesion: 0.11
Nodes (28): OverviewPage, UsagePage, COPY, formatDate(), formatDuration(), formatNumber(), MeetingRow(), metricLabel() (+20 more)

### Community 50 - ". · 50"
Cohesion: 0.19
Nodes (24): AuditEventListResponse, How far a member/viewer sees inside a workspace (owners/admins see all)., RecordScope, list_audit_events(), patch_workspace_settings(), datetime, get, HTTPException (+16 more)

### Community 51 - "Homayar/backend/schemas · 51"
Cohesion: 0.18
Nodes (24): CurrentSubscriptionResponse, get_billing_management(), get_plan_catalog(), get_subscription(), get_usage(), get_usage_events(), _plan_response(), datetime (+16 more)

### Community 52 - "Homayar/backend/services · 52"
Cohesion: 0.22
Nodes (23): AuthIdentity, IdentityProvider, LoginSession, OneTimeToken, OtpChallenge, User, normalize_iranian_mobile(), AuthenticationError (+15 more)

### Community 53 - "src/app/solutions · 53"
Cohesion: 0.08
Nodes (15): DICT, en, fa, Industry, INDUSTRY_STYLES, SolutionsContent(), SolutionsCopy, Team (+7 more)

### Community 54 - "Homayar/backend/tests · 54"
Cohesion: 0.16
Nodes (24): assert_production_posture(), describe_current_role(), RuntimeError, Effective privileges of ``current_user`` and RLS state of ``meetings``., Refuse to serve when the runtime role could bypass tenant isolation., RowLevelSecurityPostureError, _admin_execute(), _bind() (+16 more)

### Community 55 - "Homayar/backend/schemas · 55"
Cohesion: 0.18
Nodes (21): ActionStatus, ActionItemCreateRequest, ActionItemUpdateRequest, BaseModel, field_validator, model_validator, Share contract (WP03-T06). ``audience`` decides who may open the link;…, SearchMatchResponse (+13 more)

### Community 56 - "Homayar/backend/storage · 56"
Cohesion: 0.20
Nodes (18): ChecksumMismatchError, InvalidPresignTTL, InvalidStreamError, iter_stream(), normalize_expected_checksum(), ObjectTooLargeError, Enum, str (+10 more)

### Community 57 - "infra/scripts/lib · 57"
Cohesion: 0.18
Nodes (15): backup-minio.sh script, backup-postgres.sh script, backup_relative_path(), canonical_existing_path(), compose(), compose_onprem(), die(), require_command() (+7 more)

### Community 58 - "src/components/site · 58"
Cohesion: 0.12
Nodes (18): CanvasErrorBoundary, hasWebGL(), HeroCanvas(), prefersReducedMotion(), TiltCard(), useActiveInView(), useMedia(), useMounted() (+10 more)

### Community 59 - "Homayar/backend/tests · 59"
Cohesion: 0.15
Nodes (17): APIRoute, _cookies(), _doc_block(), _headers(), _insert_meeting(), _listed_titles(), _Persona, WP03-T03: the authorization matrix is explicit, deny-by-default and enforced.… (+9 more)

### Community 60 - "Homayar/backend/tests · 60"
Cohesion: 0.12
Nodes (21): Config, Test helper for isolated environment overrides., reset_settings_cache(), Session, pytest_report_header(), pytest_sessionstart(), describe_database_url(), dispose_inspection_engine() (+13 more)

### Community 61 - "Homayar/backend/services · 61"
Cohesion: 0.19
Nodes (21): AuditEventPage, _decode_audit_cursor(), _encode_audit_cursor(), get_workspace_settings(), InvalidAuditCursor, InvalidAuditRange, InvalidWorkspaceSettings, _is_sensitive_key() (+13 more)

### Community 62 - "Homayar/backend/storage · 62"
Cohesion: 0.17
Nodes (12): InvalidObjectKeyError, ObjectNotFoundError, Exception, Base storage error with a stable machine-readable code., StorageError, validate_owned_object_key(), BinaryIO, BinaryIO (+4 more)

### Community 63 - "src/lib · 63"
Cohesion: 0.18
Nodes (20): bookingSchema, isUniqueViolation(), POST(), buildICS(), CalendarEvent, dateKeyInTz(), googleCalendarUrl(), HORIZON_DAYS (+12 more)

### Community 64 - "Homayar/backend/database · 64"
Cohesion: 0.18
Nodes (20): BusinessInsight, Call, CallAgent, CallListItem, CallReport, ChatMessage, ChatRequest, DashboardKpis (+12 more)

### Community 65 - "Homayar/backend/security · 65"
Cohesion: 0.16
Nodes (17): ColumnElement, assignment_effective_clause(), effective_assignments(), membership_effective_clause(), Permission, datetime, Enum, Session (+9 more)

### Community 66 - "Homayar/backend/database · 66"
Cohesion: 0.26
Nodes (18): apply_context(), bind_capability_context(), bind_job_context(), bind_service_scope(), bind_tenant_context(), bind_user_context(), clear_context(), current_context() (+10 more)

### Community 67 - "Homayar/backend/storage · 67"
Cohesion: 0.18
Nodes (12): LocalObjectStorage, Path, ReadableStream, stat_result, Stores private objects below a tenant-scoped root with atomic publication., Path, test_local_storage_delete_is_tenant_scoped_and_presign_is_unsupported(), test_local_storage_refuses_symlink_deletion() (+4 more)

### Community 68 - "src/lib · 68"
Cohesion: 0.22
Nodes (15): GET(), contactSchema, POST(), demoSchema, POST(), leads, getDb(), hasDb() (+7 more)

### Community 69 - "Homayar/backend/storage · 69"
Cohesion: 0.13
Nodes (12): ObjectStorage, PresignedAccess, ABC, BaseModel, BinaryIO, field_validator, ReadableStream, Metadata returned only after a complete, checksum-verified write. (+4 more)

### Community 70 - "src/app/contact · 70"
Cohesion: 0.12
Nodes (14): ContactContent(), ContactCopy, DICT, en, Expectation, fa, metadata, ContactForm() (+6 more)

### Community 71 - "Homayar/backend/routers · 71"
Cohesion: 0.15
Nodes (16): _extract_extension(), _normalize_event(), BaseModel, post, Request, VOIP webhook router — receives call events from Asterisk/3CX/FreePBX., Receive multiple call events at once (e.g., daily sync from VOIP system)., Auto-detect VOIP source and normalize to standard format. (+8 more)

### Community 72 - "src/components/site · 72"
Cohesion: 0.12
Nodes (6): metadata, content(), Copy, DemoExperience(), Scenario, Turn

### Community 73 - ". · 73"
Cohesion: 0.18
Nodes (17): delete_artifact_objects(), Best-effort object deletion; returns the keys that are now absent., delete_meeting(), download_recording(), _get_meeting(), get_meeting_job(), _job_response(), list_meetings() (+9 more)

### Community 74 - "Homayar/backend/security · 74"
Cohesion: 0.27
Nodes (16): Actor, optional_actor(), HTTPException, Request, Session, Resolve the caller's *current* membership from server-side state. The header…, require_actor(), require_csrf() (+8 more)

### Community 75 - "src/app/platform · 75"
Cohesion: 0.13
Nodes (12): DICT, en, fa, Item, PlatformContent(), PlatformCopy, SECURITY_ICONS, Step (+4 more)

### Community 76 - "Homayar/backend/storage · 76"
Cohesion: 0.13
Nodes (4): ObjectConflictError, ObjectIncompleteError, An object with the requested identity exists with *different* content., An object with the requested identity exists but its write never completed.

### Community 77 - "src/app/ai-agents · 77"
Cohesion: 0.14
Nodes (12): Agent, AiAgentsContent(), AiAgentsCopy, DICT, en, fa, TONES, metadata (+4 more)

### Community 78 - "src/lib · 78"
Cohesion: 0.17
Nodes (15): BLOG_POSTS_FA, BlogPostFa, CASE_STUDIES_FA, CaseStudyFa, CATEGORIES_FA, CategoryFa, localizeBlogPost(), localizeBlogPosts() (+7 more)

### Community 79 - "Homayar/backend/providers · 79"
Cohesion: 0.14
Nodes (7): code_identity(), params_digest(), ProviderIdentity, What a provider *declares* determines its output (WP06-T03 checkpoints).…, Import path of the adapter class: the default provider identity., Short, stable digest of a prompt/template/transcript (never the text itself)., text_digest()

### Community 80 - "src/components/site · 80"
Cohesion: 0.15
Nodes (9): metadata, Booked, BookingExperience(), dateKey(), Lang, STR, BUSINESS_TZ, Slot (+1 more)

### Community 81 - "Homayar/backend/tests · 81"
Cohesion: 0.37
Nodes (11): test_rolled_back_upload_leaves_no_job_and_no_intent(), _processed_meeting(), test_share_links_are_hashed_expiring_auth_optional_and_revocable(), test_transcript_actions_search_and_exports_are_tenant_scoped(), _write_headers(), _authenticated_workspace(), test_upload_enforces_csrf_verification_media_and_idempotency(), test_upload_is_durable_private_and_idempotent() (+3 more)

### Community 82 - "Homayar/backend/services · 82"
Cohesion: 0.22
Nodes (12): ByteRange, _content_disposition(), deliver_recording(), _iter_reader(), parse_range(), HTTPException, Response, Bounded recording delivery with HTTP Range support (WP03-T06). Two delivery… (+4 more)

### Community 83 - "Homayar/backend/tests · 83"
Cohesion: 0.37
Nodes (13): _anonymous(), _cookies(), _headers(), _owner(), WP03-T06: share contract — audiences, separate grants, revocation, downloads.…, _settings(), _share(), _signup() (+5 more)

### Community 84 - "Homayar/backend/routers · 84"
Cohesion: 0.29
Nodes (11): entities(), entity_detail(), neighborhood(), get, Graph Intelligence API router., stats(), get_entities(), get_entity_detail() (+3 more)

### Community 85 - "infra/ci/tests · 85"
Cohesion: 0.22
Nodes (12): commit_all(), GIT_AUTHOR_EMAIL, GIT_AUTHOR_NAME, GIT_COMMITTER_EMAIL, GIT_COMMITTER_NAME, GIT_CONFIG_NOSYSTEM, HOME, mkrepo() (+4 more)

### Community 86 - "src/app/success · 86"
Cohesion: 0.29
Nodes (10): checkoutSchema, POST(), POST(), SuccessContent(), SuccessSummary, getSessionSummary(), metadata, SuccessPage() (+2 more)

### Community 87 - "Homayar/backend/providers · 87"
Cohesion: 0.26
Nodes (6): _chat_completions_url(), OpenAICompatibleIntelligenceProvider, Any, AsyncClient, Response, Requests strict JSON from an injected or internally managed async client.

### Community 88 - "Homayar/backend/tests · 88"
Cohesion: 0.32
Nodes (11): _add_membership(), admin_client(), _error_code(), fixture, WorkspaceRole, _register_and_login(), test_audit_events_are_tenant_scoped_filtered_sanitized_and_paginated(), test_missing_workspace_uses_stable_not_found_error() (+3 more)

### Community 89 - "Homayar/backend/tests · 89"
Cohesion: 0.35
Nodes (10): billing_client(), fixture, TestClient, _register_session(), _session_cookies(), test_billing_catalog_subscription_and_usage_are_tenant_scoped(), test_billing_rbac_and_manual_management_are_explicitly_read_only(), test_current_subscription_can_be_empty_without_fabricating_a_plan() (+2 more)

### Community 90 - "src/db · 90"
Cohesion: 0.21
Nodes (10): blogPosts, bookings, caseStudies, categories, checkoutSessions, demoRequests, orderItems, orders (+2 more)

### Community 91 - "Homayar/backend/services · 91"
Cohesion: 0.22
Nodes (8): _rate_limit_share(), _MemoryCounter, RuntimeError, ValueError, RateLimiter, RateLimitExceeded, RateLimitUnavailable, Distributed fixed-window limiter with an isolated test/development fallback.

### Community 92 - "Homayar/backend/services · 92"
Cohesion: 0.25
Nodes (10): blacklist_token(), get_job_status(), _get_redis(), is_token_blacklisted(), Redis service — blacklist, caching, job status., Add token to Redis blacklist with TTL., Check if token is in Redis blacklist., Store job status in Redis. (+2 more)

### Community 93 - "Homayar/frontend/src/components/users · 93"
Cohesion: 0.31
Nodes (8): UserFormData, UserFormModalProps, UserFormValues, UsersPage(), User, UserCreate, UserRole, UserUpdate

### Community 94 - "scripts · 94"
Cohesion: 0.20
Nodes (10): arr, byCode, c, codeIdx, decode(), deep(), e, raw (+2 more)

### Community 95 - "src/components/platform · 95"
Cohesion: 0.31
Nodes (10): AnalystGlyph(), base(), CoachGlyph(), GlyphProps, GLYPHS, GuardianGlyph(), JudgeGlyph(), ListenerGlyph() (+2 more)

### Community 96 - "Homayar/backend/services · 96"
Cohesion: 0.27
Nodes (7): Path, UploadFile, ValueError, Bounded staging for multipart uploads before media inspection., stage_upload(), StagedUpload, UploadRejectedError

### Community 97 - "Homayar/backend/storage · 97"
Cohesion: 0.24
Nodes (9): generate_object_key(), InvalidTenantError, object_key_for(), KeyFactory, A caller-chosen object token (WP06-T03 attempt-scoped artifacts). Same alphabet…, Deterministic key for a caller-chosen token, else a fresh random key., tenant_prefix(), validate_object_token() (+1 more)

### Community 98 - "Homayar/backend/tests · 98"
Cohesion: 0.22
Nodes (3): FaultyStorage, BinaryIO, Delegating object storage that fails on demand. ``fail_reads_per_key`` fails…

### Community 99 - "src/lib · 99"
Cohesion: 0.31
Nodes (9): Attachment, COPY, escapeHtml(), formatWhen(), fromAddress(), hasEmail(), Lang, send() (+1 more)

### Community 100 - "Homayar/backend/services · 100"
Cohesion: 0.31
Nodes (8): HexColor, get_call_detail(), Get full call detail with transcript, QC, problems, insights., generate_call_pdf(), PDF report generation for call reports., Generate a PDF report for a call. Returns bytes., _score_color(), _severity_color()

### Community 102 - "Homayar/backend/services · 102"
Cohesion: 0.50
Nodes (8): MeetingExport, Deterministic meeting export renderers for text-based formats., _render_json(), render_meeting_export(), _render_subtitles(), _render_text(), RenderedExport, _timestamp()

### Community 103 - "Homayar/backend/storage · 103"
Cohesion: 0.36
Nodes (6): effective_size_limit(), StorageConfigurationError, validate_chunk_size(), KeyFactory, Any, KeyFactory

### Community 104 - "infra/ci · 104"
Cohesion: 0.36
Nodes (6): config_error(), info(), check-source-contract.sh script, usage(), violation(), warn()

### Community 105 - "Homayar/backend/jobs · 105"
Cohesion: 0.29
Nodes (6): Celery, make_celery(), Celery app — background job processing for Homayar. Queue contract (WP06-T01):…, publish_processing_job(), Broker publication primitive (WP06-T01 queue contract). Application code does…, Publish one message; raise on broker failure; return the message id.…

### Community 106 - "Homayar/backend/database · 106"
Cohesion: 0.32
Nodes (4): Neo4jConnection, Execute multiple write queries in a single transaction. queries_and_params:…, Store full analysis results for a call in a single transaction., store_analysis()

### Community 107 - "Homayar/backend/schemas · 107"
Cohesion: 0.24
Nodes (5): computed_field, Any, model_validator, LeaseResponse, Execution ownership of the current/last attempt (WP06-T04).

### Community 109 - "Homayar/backend/services · 109"
Cohesion: 0.33
Nodes (5): JSONFormatter, Structured JSON logging for Homayar., Configure structured JSON logging., setup_logging(), LogRecord

### Community 110 - "Homayar/backend/jobs · 110"
Cohesion: 0.40
Nodes (6): check_workspace(), datetime, ProcessingJob, Session, Read-only inspection of every job, reservation, entitlement and checkpoint.…, _reservation_states()

### Community 111 - "Homayar/backend/database · 111"
Cohesion: 0.50
Nodes (4): create_calls(), Seed script — post-call voice intelligence domain., Generate call records from templates., seed()

### Community 112 - "Homayar/backend/tests · 112"
Cohesion: 0.40
Nodes (3): parametrize, No fabricated analytics may exist on any importable backend path…, test_fabricated_analytics_modules_are_gone()

### Community 115 - ". · 115"
Cohesion: 0.50
Nodes (3): csp, nextConfig, securityHeaders

### Community 116 - "scripts · 116"
Cohesion: 0.50
Nodes (3): issues, PAGES, VIEWPORTS

### Community 117 - "scripts · 117"
Cohesion: 0.67
Nodes (3): main(), readJson(), root

## Knowledge Gaps
- **285 isolated node(s):** `_FormatProfile`, `queryClient`, `AgentStat`, `waveHeights`, `AuthCardProps` (+280 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **13 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `process_job()` connect `Homayar/backend/tests · 18` to `Homayar/backend/services · 2`, `Homayar/backend/jobs · 3`, `Homayar/backend/database · 66`, `Homayar/backend/storage · 69`, `Homayar/backend/providers · 38`, `Homayar/backend/jobs · 11`, `Homayar/backend/jobs · 12`, `Homayar/backend/tests · 45`, `Homayar/backend/providers · 43`, `Homayar/backend/tests · 17`, `Homayar/backend/providers · 19`, `Homayar/backend/tests · 23`, `Homayar/backend/tests · 28`?**
  _High betweenness centrality (0.002) - this node is a cross-community bridge._
- **Why does `ProviderBundle` connect `Homayar/backend/tests · 17` to `Homayar/backend/providers · 43`, `Homayar/backend/providers · 19`, `Homayar/backend/providers · 87`, `Homayar/backend/tests · 23`?**
  _High betweenness centrality (0.001) - this node is a cross-community bridge._
- **Why does `ClaimedJob` connect `Homayar/backend/jobs · 11` to `Homayar/backend/jobs · 3`, `Homayar/backend/jobs · 36`, `Homayar/backend/services · 37`, `Homayar/backend/services · 39`, `Homayar/backend/providers · 43`, `Homayar/backend/jobs · 12`, `Homayar/backend/jobs · 15`, `Homayar/backend/tests · 17`, `Homayar/backend/providers · 19`, `Homayar/backend/tests · 23`?**
  _High betweenness centrality (0.001) - this node is a cross-community bridge._
- **What connects `_FormatProfile`, `queryClient`, `AgentStat` to the rest of the system?**
  _285 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Homayar/backend/media · 0` be split into smaller, more focused modules?**
  _Cohesion score 0.056235134216785596 - nodes in this community are weakly interconnected._
- **Should `Homayar/frontend/src/types · 1` be split into smaller, more focused modules?**
  _Cohesion score 0.0348607367475292 - nodes in this community are weakly interconnected._
- **Should `Homayar/backend/services · 2` be split into smaller, more focused modules?**
  _Cohesion score 0.0451505016722408 - nodes in this community are weakly interconnected._