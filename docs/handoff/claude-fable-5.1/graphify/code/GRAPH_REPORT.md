Local static code map; consult diagnostics.json and lossless raw extraction.json. No feature completion or definition-resolution guarantee.

# Graph Report - /Users/emperor/Documents/AI/Homayar.com/.claude/worktrees/homayar-commercial-v1-a266f5  (2026-09-09)

## Corpus Check
- 307 files · ~185,940 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3139 nodes · 10289 edges · 129 communities (115 shown, 14 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 1012 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Homayar/backend/media · 0
- Homayar/frontend/src/types · 1
- Homayar/backend/jobs · 2
- src/lib · 3
- . · 4
- Homayar/backend/providers · 5
- Homayar/frontend/src/components/platform · 6
- . · 7
- Homayar/backend/tests · 8
- Homayar/backend/jobs · 9
- Homayar/frontend/src/lib · 10
- Homayar/backend/schemas · 11
- Homayar/backend/services · 12
- Homayar/backend/tests · 13
- src/components/catalog · 14
- Homayar/frontend/src/types · 15
- scripts · 16
- Homayar/frontend/src/components/auth · 17
- Homayar/backend/routers/v1 · 18
- Homayar/backend/tests · 19
- Homayar/backend/services · 20
- src/app/about · 21
- Homayar/frontend/src/components/ui · 22
- . · 23
- Homayar/backend/database · 24
- Homayar/frontend/src/lib · 25
- Homayar/backend/services · 26
- . · 27
- src/components · 28
- src/lib · 29
- Homayar/backend/tests · 30
- Homayar/backend/services · 31
- Homayar/backend/tests · 32
- Homayar/backend/services · 33
- Homayar/backend/routers/v1 · 34
- Homayar/backend/tests · 35
- Homayar/backend/jobs · 36
- Homayar/backend/providers · 37
- Homayar/backend/tests · 38
- Homayar/backend/services · 39
- Homayar/backend/migrations/versions · 40
- Homayar/backend/services · 41
- Homayar/frontend/src/components/platform · 42
- Homayar/backend/jobs · 43
- Homayar/backend/storage · 44
- Homayar/backend/providers · 45
- src/app/solutions · 46
- Homayar/backend/tests · 47
- Homayar/backend/tests · 48
- infra/scripts/lib · 49
- src/components/site · 50
- Homayar/backend/tests · 51
- src/lib · 52
- src/lib · 53
- Homayar/backend/database · 54
- Homayar/backend/storage · 55
- . · 56
- Homayar/backend/database · 57
- Homayar/backend/services · 58
- Homayar/backend/routers · 59
- Homayar/backend/storage · 60
- src/components/site · 61
- src/app/platform · 62
- src/lib · 63
- Homayar/backend/services · 64
- Homayar/backend/tests · 65
- src/app/ai-agents · 66
- src/app/blog/[slug] · 67
- Homayar/backend/database · 68
- Homayar/backend/security · 69
- src/components/site · 70
- Homayar/backend/services · 71
- Homayar/backend/services · 72
- Homayar/backend/tests · 73
- Homayar/backend/routers · 74
- Homayar/backend/services · 75
- Homayar/backend/tests · 76
- Homayar/backend/tests · 77
- infra/ci/tests · 78
- src/app/api/contact · 79
- src/app/success · 80
- Homayar/backend/tests · 81
- Homayar/backend/storage · 82
- Homayar/backend/tests · 83
- src/db · 84
- Homayar/backend/services · 85
- Homayar/backend/services · 86
- Homayar/frontend/src/components/users · 87
- scripts · 88
- src/components/platform · 89
- Homayar/backend/providers · 90
- Homayar/backend/services · 91
- Homayar/backend/storage · 92
- src/lib · 93
- Homayar/backend/services · 94
- Homayar/backend/providers · 95
- Homayar/backend/storage · 96
- Homayar/backend/storage · 97
- infra/ci · 98
- Homayar/backend/database · 99
- src/lib · 100
- Homayar/backend/security · 101
- Homayar/backend/database · 102
- Homayar/backend/tests · 103
- Homayar/backend/migrations/versions · 104
- Homayar/backend/migrations/versions · 105
- . · 106
- scripts · 107
- Homayar/backend/jobs · 108
- Homayar/backend/providers · 113
- Homayar/frontend/src/test · 114
- . · 115
- Homayar/backend/schemas · 116
- Homayar/backend/tests · 117
- infra/ci · 118
- . · 119
- . · 120
- scripts · 121

## God Nodes (most connected - your core abstractions)
1. `Settings` - 260 edges
2. `inspection_session_factory()` - 113 edges
3. `get_settings()` - 88 edges
4. `WorkspaceContext` - 73 edges
5. `User` - 63 edges
6. `Workspace` - 63 edges
7. `_queued_upload()` - 62 edges
8. `Meeting` - 61 edges
9. `process_job()` - 61 edges
10. `Membership` - 60 edges

## Surprising Connections (you probably didn't know these)
- `FailureClass` --uses--> `Settings`  [INFERRED]
  Homayar/backend/jobs/failures.py → Homayar/backend/config.py
- `FailureClass` --uses--> `JobState`  [INFERRED]
  Homayar/backend/jobs/failures.py → Homayar/backend/database/saas_models.py
- `FailureClass` --uses--> `Meeting`  [INFERRED]
  Homayar/backend/jobs/failures.py → Homayar/backend/database/saas_models.py
- `FailureClass` --uses--> `MeetingStatus`  [INFERRED]
  Homayar/backend/jobs/failures.py → Homayar/backend/database/saas_models.py
- `FailureClass` --uses--> `ProcessingJob`  [INFERRED]
  Homayar/backend/jobs/failures.py → Homayar/backend/database/saas_models.py

## Import Cycles
- None detected.

## Communities (129 total, 14 thin omitted)

### Community 0 - "Homayar/backend/media · 0"
Cohesion: 0.06
Nodes (80): CompletedProcess, Secure media inspection primitives used before object publication., AudioStreamMetadata, _build_ffprobe_command(), _codec_allowed(), EmptyMediaError, _ensure_file_unchanged(), ExtensionContentMismatchError (+72 more)

### Community 1 - "Homayar/frontend/src/types · 1"
Cohesion: 0.03
Nodes (102): SettingsPanel(), acceptInvitation(), changeMemberRole(), downloadPayload(), downloadRecording(), exportMeeting(), filenameFromContentDisposition(), getBillingUsage() (+94 more)

### Community 2 - "Homayar/backend/jobs · 2"
Cohesion: 0.08
Nodes (71): Event, JobState, Meeting, MeetingStatus, ProcessingJob, str, Truthful per-stage outcome of one processing attempt (WP04-T01)., Recording (+63 more)

### Community 3 - "src/lib · 3"
Cohesion: 0.04
Nodes (53): ContactContent(), ContactCopy, DICT, en, Expectation, fa, metadata, inter (+45 more)

### Community 4 - ". · 4"
Cohesion: 0.06
Nodes (62): computed_field, cancel_dispatch(), Withdraw a pending publication (same transaction as the cancellation)., delete_meeting(), download_recording(), _enforce_upload_rate_limit(), _get_meeting(), get_meeting_job() (+54 more)

### Community 5 - "Homayar/backend/providers · 5"
Cohesion: 0.14
Nodes (43): Asanak SMS adapter using an injected endpoint and credential., ASRProvider, DiarizationProvider, EmailProvider, IntelligenceProvider, MockProviderDisabledError, ABC, Provider interfaces and sanitized execution errors. (+35 more)

### Community 6 - "Homayar/frontend/src/components/platform · 6"
Cohesion: 0.07
Nodes (52): SearchPage, Header(), navItems, Sidebar(), InvitationAcceptPage(), idempotencyKey(), MeetingsPage(), ADMIN_ASSIGNABLE_ROLES (+44 more)

### Community 7 - ". · 7"
Cohesion: 0.08
Nodes (58): AuditEventListResponse, AuditEvent, How far a member/viewer sees inside a workspace (owners/admins see all)., RecordScope, list_audit_events(), patch_workspace_settings(), datetime, get (+50 more)

### Community 8 - "Homayar/backend/tests · 8"
Cohesion: 0.10
Nodes (52): get_session_factory(), Entitlement, Append-only usage ledger (WP06-T06). One row per reservation transition…, ReservationState, UsageEvent, UsageReservation, Violation, Operator replay of dead-lettered jobs (WP06-T05). Replay is the only way a… (+44 more)

### Community 9 - "Homayar/backend/jobs · 9"
Cohesion: 0.06
Nodes (51): DispatchState, JobDispatch, Transactional-outbox state of a job's broker publication (WP06-T02)., Transactional outbox row: the intent to publish one job to the broker. Written…, enqueue_processing_job(), publish_processing_job(), Broker publication primitive (WP06-T01 queue contract). Application code does…, Publish one message; raise on broker failure; return the message id.… (+43 more)

### Community 10 - "Homayar/frontend/src/lib · 10"
Cohesion: 0.07
Nodes (43): AgentStat, Message, suggestions, CallIncident, Incident, IncidentSeverity, api, API_BASE_URL (+35 more)

### Community 11 - "Homayar/backend/schemas · 11"
Cohesion: 0.11
Nodes (51): ActionStatus, _action_or_404(), create_action(), delete_action(), get_summary(), get_transcript(), _like_pattern(), list_actions() (+43 more)

### Community 12 - "Homayar/backend/services · 12"
Cohesion: 0.09
Nodes (52): Who may open a share link (WP03-T06)., A named recipient of a ``recipients`` share, bound to a user once proven., ShareAudience, ShareLink, ShareRecipient, access_share(), _authorized_grant(), create_share() (+44 more)

### Community 13 - "Homayar/backend/tests · 13"
Cohesion: 0.10
Nodes (49): Durable background job orchestration., process_job(), Path, create_object_storage(), pytest_collection_modifyitems(), configured_database_url(), inspection_session_factory(), is_postgres_url() (+41 more)

### Community 14 - "src/components/catalog · 14"
Cohesion: 0.07
Nodes (46): BILLING_EXPLANATIONS, ProductDetail(), splitOutcome(), Strings, en, fa, PLAN_ORDER, PlanCard() (+38 more)

### Community 15 - "Homayar/frontend/src/types · 15"
Cohesion: 0.06
Nodes (38): CallsPage(), severityConfig, DashboardPage(), SENTIMENT_COLORS, KpiCard(), KpiCardProps, labelColors, LabelConfig (+30 more)

### Community 16 - "scripts · 16"
Cohesion: 0.15
Nodes (51): ACCENT_KEYS, ACCENTS, arcPath(), arcPoint(), background(), barChart(), card(), chooseAccent() (+43 more)

### Community 17 - "Homayar/frontend/src/components/auth · 17"
Cohesion: 0.09
Nodes (38): App(), ForgotPasswordPage, InvitationAcceptPage, LoginPage, MeetingsPage, MembersPage, PhoneOtpRequestPage, PhoneOtpVerifyPage (+30 more)

### Community 18 - "Homayar/backend/routers/v1 · 18"
Cohesion: 0.14
Nodes (45): _clear_session_cookies(), _client_ip(), forgot_password(), google_callback(), google_link_start(), google_start(), login(), logout() (+37 more)

### Community 19 - "Homayar/backend/tests · 19"
Cohesion: 0.09
Nodes (42): Extend the lease if, and only if, ``lease`` is still the valid owner., renew_lease(), Recover every expired RUNNING attempt (bounded batch). Returns counts., sweep_expired_leases(), broker(), FakeBroker, fixture, Records publications; can be told to fail N times or to block. (+34 more)

### Community 20 - "Homayar/backend/services · 20"
Cohesion: 0.12
Nodes (39): ColumnElement, Effective-dated operational role of a workspace member inside a team., Team, TeamAssignment, TeamRole, assignment_effective_clause(), effective_assignments(), membership_effective_clause() (+31 more)

### Community 21 - "src/app/about · 21"
Cohesion: 0.06
Nodes (31): AboutContent(), AboutCopy, DICT, en, fa, Value, metadata, BlogIndexPage() (+23 more)

### Community 22 - "Homayar/frontend/src/components/ui · 22"
Cohesion: 0.09
Nodes (29): Avatar(), AvatarBadge(), AvatarFallback(), AvatarGroup(), AvatarGroupCount(), AvatarImage(), Badge(), badgeVariants (+21 more)

### Community 23 - ". · 23"
Cohesion: 0.12
Nodes (39): CurrentSubscriptionResponse, Plan, Subscription, SubscriptionStatus, get_billing_management(), get_plan_catalog(), get_subscription(), get_usage() (+31 more)

### Community 24 - "Homayar/backend/database · 24"
Cohesion: 0.13
Nodes (38): DeclarativeBase, ApiKey, AuthIdentity, Base, IdentityProvider, LoginSession, MeetingSpeaker, Notification (+30 more)

### Community 25 - "Homayar/frontend/src/lib · 25"
Cohesion: 0.08
Nodes (39): MeetingDetailPage, DetailTab, MeetingDetailPage(), STAGE_LABELS, timestamp(), formatDate(), formatDuration(), createAction() (+31 more)

### Community 26 - "Homayar/backend/services · 26"
Cohesion: 0.08
Nodes (35): load_prompt(), Prompt loader — reads prompt text files from the prompts directory., Load a prompt file by name (e.g. 'qc_fa', 'problem_en')., chat(), post, AI Copilot API router., classify_intent(), generate_response() (+27 more)

### Community 27 - ". · 27"
Cohesion: 0.13
Nodes (37): accept_member_invitation(), delete_member(), _invitation_response(), invite_member(), list_invitations(), list_members(), _member_response(), delete (+29 more)

### Community 28 - "src/components · 28"
Cohesion: 0.11
Nodes (27): metadata, metadata, AddToCartButton(), LABELS, CartClearer(), CartTotals(), CartView(), CheckoutView() (+19 more)

### Community 29 - "src/lib · 29"
Cohesion: 0.11
Nodes (30): AVATAR_GRADIENTS, CaseStudyPage(), generateMetadata(), generateStaticParams(), CatalogPage(), HERO, metadata, generateMetadata() (+22 more)

### Community 30 - "Homayar/backend/tests · 30"
Cohesion: 0.12
Nodes (20): BaseSettings, model_validator, Validated runtime configuration for cloud and on-premise deployments., Settings, SessionTokens, AccountLinkRequiredError, GoogleOAuthError, ValueError (+12 more)

### Community 31 - "Homayar/backend/services · 31"
Cohesion: 0.19
Nodes (36): Membership, Workspace, WorkspaceInvitation, WorkspaceRole, can_grant(), can_manage_member(), WorkspaceRole, A caller manages a membership only if it could have granted its role. Self-… (+28 more)

### Community 32 - "Homayar/backend/tests · 32"
Cohesion: 0.15
Nodes (35): _session_cookies(), _user_and_workspace(), _meeting_of(), _drive(), Publish, deliver (twice when asked), recover and retry until every job is…, _states(), _audit(), _committed_usage() (+27 more)

### Community 33 - "Homayar/backend/services · 33"
Cohesion: 0.19
Nodes (32): get_settings(), create_access_token(), decode_access_token(), hash_password(), make_refresh_token(), parse_refresh_session_id(), Any, datetime (+24 more)

### Community 34 - "Homayar/backend/routers/v1 · 34"
Cohesion: 0.15
Nodes (34): _as_utc(), assign_member(), _assignment_response(), create_workspace_team(), list_workspace_teams(), patch_team(), datetime, delete (+26 more)

### Community 35 - "Homayar/backend/tests · 35"
Cohesion: 0.14
Nodes (17): login_user(), TestClient, register_user(), TestClient, test_deactivated_user_session_is_rejected(), test_refresh_session_is_stored_as_hash(), TestEmailAuthentication, TestPhoneAuthentication (+9 more)

### Community 36 - "Homayar/backend/jobs · 36"
Cohesion: 0.11
Nodes (32): check_all(), check_workspace(), discover_workspaces(), datetime, ProcessingJob, Session, Invariants of accepted processing jobs (WP06-T07). One reusable checker for…, Read-only inspection of every job, reservation and entitlement of a workspace. (+24 more)

### Community 37 - "Homayar/backend/providers · 37"
Cohesion: 0.14
Nodes (27): _compatible_endpoint(), create_asr_provider(), create_diarization_provider(), create_intelligence_provider(), create_provider_bundle(), _provider_name(), ProviderFactory, AsyncClient (+19 more)

### Community 38 - "Homayar/backend/tests · 38"
Cohesion: 0.11
Nodes (18): asyncio, AsanakSMSProvider, Any, Response, Submit one SMS to an injected Asanak endpoint. Message bodies and the API token…, ProviderIssue, FakeS3Client, FakeS3NotFound (+10 more)

### Community 39 - "Homayar/backend/services · 39"
Cohesion: 0.12
Nodes (29): aggregated_issues(), call_problems(), call_qc_details(), issues_by_category(), kpis(), get, qc_trend(), Dashboard API router — post-call metrics. (+21 more)

### Community 40 - "Homayar/backend/migrations/versions · 40"
Cohesion: 0.11
Nodes (25): apply_row_level_security(), disable_policy_statements(), enable_policy_statements(), ensure_app_role_statements(), existing_policy_tables(), PostgreSQL row-level security policies (WP03-T05). Single source of the DDL…, Policy tables that exist in the current schema (migrations add tables over…, Idempotently create the app role, grants and policies (PostgreSQL only).… (+17 more)

### Community 41 - "Homayar/backend/services · 41"
Cohesion: 0.11
Nodes (28): UserCreate, Update call processing status., Store transcript for a call., store_transcript(), update_call_status(), authenticate_user(), create_user(), create_user_from_voip() (+20 more)

### Community 42 - "Homayar/frontend/src/components/platform · 42"
Cohesion: 0.11
Nodes (28): OverviewPage, UsagePage, COPY, formatDate(), formatDuration(), formatNumber(), MeetingRow(), metricLabel() (+20 more)

### Community 43 - "Homayar/backend/jobs · 43"
Cohesion: 0.13
Nodes (26): _as_utc(), cancel_job(), deadline_passed(), FailureClass, park_job(), datetime, Meeting, ProcessingJob (+18 more)

### Community 44 - "Homayar/backend/storage · 44"
Cohesion: 0.16
Nodes (23): ChecksumMismatchError, generate_object_key(), InvalidTenantError, iter_stream(), normalize_expected_checksum(), ObjectTooLargeError, Enum, field_validator (+15 more)

### Community 45 - "Homayar/backend/providers · 45"
Cohesion: 0.15
Nodes (13): ProviderError, Exception, Provider failure safe to expose to application orchestration., ASRRequest, ASRResult, ASRSegment, LocalWhisperProvider, Any (+5 more)

### Community 46 - "src/app/solutions · 46"
Cohesion: 0.08
Nodes (15): DICT, en, fa, Industry, INDUSTRY_STYLES, SolutionsContent(), SolutionsCopy, Team (+7 more)

### Community 47 - "Homayar/backend/tests · 47"
Cohesion: 0.16
Nodes (24): assert_production_posture(), describe_current_role(), RuntimeError, Effective privileges of ``current_user`` and RLS state of ``meetings``., Refuse to serve when the runtime role could bypass tenant isolation., RowLevelSecurityPostureError, Fail closed in production when the runtime role could bypass RLS., _verify_database_posture() (+16 more)

### Community 48 - "Homayar/backend/tests · 48"
Cohesion: 0.23
Nodes (20): ActionItem, MeetingSummary, ProcessingStageRun, ProviderUsage, One stage of one processing attempt with its typed outcome and provenance., TranscriptSegment, _speaker_for_segment(), _transcript_turns() (+12 more)

### Community 49 - "infra/scripts/lib · 49"
Cohesion: 0.18
Nodes (15): backup-minio.sh script, backup-postgres.sh script, backup_relative_path(), canonical_existing_path(), compose(), compose_onprem(), die(), require_command() (+7 more)

### Community 50 - "src/components/site · 50"
Cohesion: 0.12
Nodes (18): CanvasErrorBoundary, hasWebGL(), HeroCanvas(), prefersReducedMotion(), TiltCard(), useActiveInView(), useMedia(), useMounted() (+10 more)

### Community 51 - "Homayar/backend/tests · 51"
Cohesion: 0.15
Nodes (17): APIRoute, _cookies(), _doc_block(), _headers(), _insert_meeting(), _listed_titles(), _Persona, WP03-T03: the authorization matrix is explicit, deny-by-default and enforced.… (+9 more)

### Community 52 - "src/lib · 52"
Cohesion: 0.17
Nodes (21): bookingSchema, isUniqueViolation(), POST(), bookings, buildICS(), CalendarEvent, dateKeyInTz(), googleCalendarUrl() (+13 more)

### Community 53 - "src/lib · 53"
Cohesion: 0.13
Nodes (15): Footer(), Header(), FullLogo(), Logo(), SiteChrome(), FOOTER_GROUPS, FA_FOOTER_TITLES, FA_NAV_LABELS (+7 more)

### Community 54 - "Homayar/backend/database · 54"
Cohesion: 0.22
Nodes (20): apply_context(), bind_capability_context(), bind_job_context(), bind_service_scope(), bind_tenant_context(), bind_user_context(), clear_context(), current_context() (+12 more)

### Community 55 - "Homayar/backend/storage · 55"
Cohesion: 0.19
Nodes (12): InvalidObjectKeyError, LocalObjectStorage, BinaryIO, Path, stat_result, Stores private objects below a tenant-scoped root with atomic publication., Path, test_local_storage_delete_is_tenant_scoped_and_presign_is_unsupported() (+4 more)

### Community 56 - ". · 56"
Cohesion: 0.14
Nodes (18): FastAPI, create_schema_for_test_or_development(), ``create_all`` plus the same row-level security the migrations install. Runs on…, Dispose configured state. Intended for isolated tests., reset_database_state(), create_app(), ensure_plan_catalog(), Session (+10 more)

### Community 57 - "Homayar/backend/database · 57"
Cohesion: 0.18
Nodes (20): BusinessInsight, Call, CallAgent, CallListItem, CallReport, ChatMessage, ChatRequest, DashboardKpis (+12 more)

### Community 58 - "Homayar/backend/services · 58"
Cohesion: 0.14
Nodes (18): ldap_sync(), ldap_test(), get, post, LDAP sync router — AD user synchronization endpoints., Test LDAP connectivity., Full sync: fetch users from AD and upsert into Neo4j., authenticate_ldap() (+10 more)

### Community 59 - "Homayar/backend/routers · 59"
Cohesion: 0.15
Nodes (16): _extract_extension(), _normalize_event(), BaseModel, post, Request, VOIP webhook router — receives call events from Asterisk/3CX/FreePBX., Receive multiple call events at once (e.g., daily sync from VOIP system)., Auto-detect VOIP source and normalize to standard format. (+8 more)

### Community 60 - "Homayar/backend/storage · 60"
Cohesion: 0.20
Nodes (12): InvalidPresignTTL, ObjectNotFoundError, Exception, Base storage error with a stable machine-readable code., StorageError, validate_owned_object_key(), BinaryIO, Exception (+4 more)

### Community 61 - "src/components/site · 61"
Cohesion: 0.12
Nodes (6): metadata, content(), Copy, DemoExperience(), Scenario, Turn

### Community 62 - "src/app/platform · 62"
Cohesion: 0.13
Nodes (12): DICT, en, fa, Item, PlatformContent(), PlatformCopy, SECURITY_ICONS, Step (+4 more)

### Community 63 - "src/lib · 63"
Cohesion: 0.16
Nodes (16): BLOG_POSTS_FA, BlogPostFa, CASE_STUDIES_FA, CaseStudyFa, CATEGORIES_FA, CategoryFa, localizeBlogPost(), localizeBlogPosts() (+8 more)

### Community 64 - "Homayar/backend/services · 64"
Cohesion: 0.15
Nodes (11): Celery, make_celery(), Celery app — background job processing for Homayar. Queue contract (WP06-T01):…, Validation and tenant namespacing for client idempotency keys., scoped_idempotency_key(), validate_idempotency_key(), Call service — upload, list, detail, analysis., Save uploaded file to disk. Returns the file path. (+3 more)

### Community 65 - "Homayar/backend/tests · 65"
Cohesion: 0.17
Nodes (14): Config, Test helper for isolated environment overrides., reset_settings_cache(), pytest_report_header(), dispose_inspection_engine(), Test database selection (WP01-T05). The suite runs against PostgreSQL when…, Drop every model table (and the Alembic stamp) so a test starts clean., reset_postgres_schema() (+6 more)

### Community 66 - "src/app/ai-agents · 66"
Cohesion: 0.14
Nodes (12): Agent, AiAgentsContent(), AiAgentsCopy, DICT, en, fa, TONES, metadata (+4 more)

### Community 67 - "src/app/blog/[slug] · 67"
Cohesion: 0.22
Nodes (11): BlogPostPage(), formatDate(), generateMetadata(), generateStaticParams(), MONTHS, DICT, TermsContent(), metadata (+3 more)

### Community 68 - "Homayar/backend/database · 68"
Cohesion: 0.23
Nodes (13): Engine, build_engine(), configure_database(), get_db(), get_engine(), Session, sessionmaker, _quote_identifier() (+5 more)

### Community 69 - "Homayar/backend/security · 69"
Cohesion: 0.31
Nodes (14): Actor, optional_actor(), HTTPException, Request, Session, Resolve the caller's *current* membership from server-side state. The header…, require_actor(), require_csrf() (+6 more)

### Community 70 - "src/components/site · 70"
Cohesion: 0.15
Nodes (9): metadata, Booked, BookingExperience(), dateKey(), Lang, STR, BUSINESS_TZ, Slot (+1 more)

### Community 71 - "Homayar/backend/services · 71"
Cohesion: 0.25
Nodes (11): new_id(), normalize_email(), normalize_workspace_slug(), complete_google_oauth(), _pkce_challenge(), AsyncClient, Session, User (+3 more)

### Community 72 - "Homayar/backend/services · 72"
Cohesion: 0.22
Nodes (12): ByteRange, _content_disposition(), deliver_recording(), _iter_reader(), parse_range(), HTTPException, Response, Bounded recording delivery with HTTP Range support (WP03-T06). Two delivery… (+4 more)

### Community 73 - "Homayar/backend/tests · 73"
Cohesion: 0.37
Nodes (13): _anonymous(), _cookies(), _headers(), _owner(), WP03-T06: share contract — audiences, separate grants, revocation, downloads.…, _settings(), _share(), _signup() (+5 more)

### Community 74 - "Homayar/backend/routers · 74"
Cohesion: 0.29
Nodes (11): entities(), entity_detail(), neighborhood(), get, Graph Intelligence API router., stats(), get_entities(), get_entity_detail() (+3 more)

### Community 75 - "Homayar/backend/services · 75"
Cohesion: 0.32
Nodes (12): export_meeting(), Response, ExportAction, ExportSegment, MeetingExport, Deterministic meeting export renderers for text-based formats., _render_json(), render_meeting_export() (+4 more)

### Community 76 - "Homayar/backend/tests · 76"
Cohesion: 0.23
Nodes (12): email_outbox(), fixture, Path, Session, pytest_sessionstart(), settings(), sms_outbox(), describe_database_url() (+4 more)

### Community 77 - "Homayar/backend/tests · 77"
Cohesion: 0.42
Nodes (11): test_rolled_back_upload_leaves_no_job_and_no_intent(), _processed_meeting(), test_share_links_are_hashed_expiring_auth_optional_and_revocable(), test_transcript_actions_search_and_exports_are_tenant_scoped(), _write_headers(), _authenticated_workspace(), test_upload_enforces_csrf_verification_media_and_idempotency(), test_upload_is_durable_private_and_idempotent() (+3 more)

### Community 78 - "infra/ci/tests · 78"
Cohesion: 0.22
Nodes (12): commit_all(), GIT_AUTHOR_EMAIL, GIT_AUTHOR_NAME, GIT_COMMITTER_EMAIL, GIT_COMMITTER_NAME, GIT_CONFIG_NOSYSTEM, HOME, mkrepo() (+4 more)

### Community 79 - "src/app/api/contact · 79"
Cohesion: 0.33
Nodes (9): GET(), contactSchema, POST(), demoSchema, POST(), demoRequests, leads, getDb() (+1 more)

### Community 80 - "src/app/success · 80"
Cohesion: 0.35
Nodes (9): checkoutSchema, POST(), POST(), getSessionSummary(), metadata, SuccessPage(), checkoutSessions, getStripe() (+1 more)

### Community 81 - "Homayar/backend/tests · 81"
Cohesion: 0.36
Nodes (10): _cookies(), login_user(), _mount_router(), TestClient, _register_login(), register_user(), test_invitation_acceptance_is_scoped_hashed_and_audited(), test_member_roles_removal_csrf_and_hierarchy() (+2 more)

### Community 82 - "Homayar/backend/storage · 82"
Cohesion: 0.20
Nodes (6): ObjectStorage, ABC, BinaryIO, ReadableStream, Tenant-scoped private object storage interface., Runtime object-storage construction.

### Community 83 - "Homayar/backend/tests · 83"
Cohesion: 0.35
Nodes (10): billing_client(), fixture, TestClient, _register_session(), _session_cookies(), test_billing_catalog_subscription_and_usage_are_tenant_scoped(), test_billing_rbac_and_manual_management_are_explicitly_read_only(), test_current_subscription_can_be_empty_without_fabricating_a_plan() (+2 more)

### Community 84 - "src/db · 84"
Cohesion: 0.18
Nodes (10): main(), readJson(), root, blogPosts, caseStudies, categories, orderItems, orders (+2 more)

### Community 85 - "Homayar/backend/services · 85"
Cohesion: 0.27
Nodes (7): _MemoryCounter, RuntimeError, ValueError, RateLimiter, RateLimitExceeded, RateLimitUnavailable, Distributed fixed-window limiter with an isolated test/development fallback.

### Community 86 - "Homayar/backend/services · 86"
Cohesion: 0.25
Nodes (10): blacklist_token(), get_job_status(), _get_redis(), is_token_blacklisted(), Redis service — blacklist, caching, job status., Add token to Redis blacklist with TTL., Check if token is in Redis blacklist., Store job status in Redis. (+2 more)

### Community 87 - "Homayar/frontend/src/components/users · 87"
Cohesion: 0.31
Nodes (8): UserFormData, UserFormModalProps, UserFormValues, UsersPage(), User, UserCreate, UserRole, UserUpdate

### Community 88 - "scripts · 88"
Cohesion: 0.20
Nodes (10): arr, byCode, c, codeIdx, decode(), deep(), e, raw (+2 more)

### Community 89 - "src/components/platform · 89"
Cohesion: 0.31
Nodes (10): AnalystGlyph(), base(), CoachGlyph(), GlyphProps, GLYPHS, GuardianGlyph(), JudgeGlyph(), ListenerGlyph() (+2 more)

### Community 90 - "Homayar/backend/providers · 90"
Cohesion: 0.20
Nodes (3): AsyncClient, field_validator, SecretStr

### Community 91 - "Homayar/backend/services · 91"
Cohesion: 0.27
Nodes (9): Email service — SMTP via FastAPI-Mail + Mailpit for dev., Synchronous email send for Celery tasks., Send password reset email with token., Send email verification with token., Send email using configured SMTP server., send_email(), send_email_sync(), send_password_reset_email() (+1 more)

### Community 93 - "src/lib · 93"
Cohesion: 0.31
Nodes (9): Attachment, COPY, escapeHtml(), formatWhen(), fromAddress(), hasEmail(), Lang, send() (+1 more)

### Community 94 - "Homayar/backend/services · 94"
Cohesion: 0.31
Nodes (8): HexColor, get_call_detail(), Get full call detail with transcript, QC, problems, insights., generate_call_pdf(), PDF report generation for call reports., Generate a PDF report for a call. Returns bytes., _score_color(), _severity_color()

### Community 96 - "Homayar/backend/storage · 96"
Cohesion: 0.36
Nodes (6): effective_size_limit(), StorageConfigurationError, validate_chunk_size(), KeyFactory, Any, KeyFactory

### Community 97 - "Homayar/backend/storage · 97"
Cohesion: 0.25
Nodes (5): PresignedAccess, PresignedAccessNotSupportedError, BaseModel, Short-lived read access produced by a storage backend., Private tenant-scoped object storage implementations.

### Community 98 - "infra/ci · 98"
Cohesion: 0.36
Nodes (6): config_error(), info(), check-source-contract.sh script, usage(), violation(), warn()

### Community 99 - "Homayar/backend/database · 99"
Cohesion: 0.32
Nodes (4): Neo4jConnection, Execute multiple write queries in a single transaction. queries_and_params:…, Store full analysis results for a call in a single transaction., store_analysis()

### Community 100 - "src/lib · 100"
Cohesion: 0.36
Nodes (7): Bucket, clientIp(), enforceRateLimit(), rateLimit(), RateLimitResult, store, sweep()

### Community 101 - "Homayar/backend/security · 101"
Cohesion: 0.38
Nodes (6): Permission, Enum, str, role_has(), require_permission(), require_write_permission()

### Community 102 - "Homayar/backend/database · 102"
Cohesion: 0.50
Nodes (4): create_calls(), Seed script — post-call voice intelligence domain., Generate call records from templates., seed()

### Community 103 - "Homayar/backend/tests · 103"
Cohesion: 0.40
Nodes (3): parametrize, No fabricated analytics may exist on any importable backend path…, test_fabricated_analytics_modules_are_gone()

### Community 106 - ". · 106"
Cohesion: 0.50
Nodes (3): csp, nextConfig, securityHeaders

### Community 107 - "scripts · 107"
Cohesion: 0.50
Nodes (3): issues, PAGES, VIEWPORTS

## Knowledge Gaps
- **285 isolated node(s):** `_FormatProfile`, `queryClient`, `AgentStat`, `waveHeights`, `AuthCardProps` (+280 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **14 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `process_job()` connect `Homayar/backend/tests · 13` to `Homayar/backend/services · 33`, `Homayar/backend/jobs · 2`, `Homayar/backend/jobs · 36`, `Homayar/backend/providers · 37`, `Homayar/backend/providers · 5`, `Homayar/backend/jobs · 43`, `Homayar/backend/providers · 45`, `Homayar/backend/tests · 48`, `Homayar/backend/storage · 82`, `Homayar/backend/tests · 19`, `Homayar/backend/database · 54`, `Homayar/backend/tests · 30`?**
  _High betweenness centrality (0.001) - this node is a cross-community bridge._
- **Why does `recover_expired_lease()` connect `Homayar/backend/jobs · 43` to `Homayar/backend/services · 33`, `Homayar/backend/jobs · 2`, `Homayar/backend/jobs · 36`, `Homayar/backend/tests · 8`, `Homayar/backend/jobs · 9`, `Homayar/backend/database · 54`, `Homayar/backend/tests · 30`?**
  _High betweenness centrality (0.001) - this node is a cross-community bridge._
- **Why does `process_meeting_job()` connect `Homayar/backend/jobs · 9` to `Homayar/backend/tests · 13`?**
  _High betweenness centrality (0.000) - this node is a cross-community bridge._
- **Are the 8 inferred relationships involving `WorkspaceContext` (e.g. with `Settings` and `LoginSession`) actually correct?**
  _`WorkspaceContext` has 8 INFERRED edges - model-reasoned connections that need verification._
- **What connects `_FormatProfile`, `queryClient`, `AgentStat` to the rest of the system?**
  _285 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Homayar/backend/media · 0` be split into smaller, more focused modules?**
  _Cohesion score 0.056235134216785596 - nodes in this community are weakly interconnected._
- **Should `Homayar/frontend/src/types · 1` be split into smaller, more focused modules?**
  _Cohesion score 0.0348607367475292 - nodes in this community are weakly interconnected._