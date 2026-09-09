Local static code map; consult diagnostics.json and lossless raw extraction.json. No feature completion or definition-resolution guarantee.

# Graph Report - /Users/emperor/Documents/AI/Homayar.com/.claude/worktrees/homayar-commercial-v1-a266f5  (2026-09-09)

## Corpus Check
- 302 files · ~179,914 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3053 nodes · 9844 edges · 134 communities (121 shown, 13 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 978 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Homayar/backend/media · 0
- Homayar/frontend/src/types · 1
- Homayar/backend/schemas · 2
- Homayar/frontend/src/components/platform · 3
- . · 4
- Homayar/backend/services · 5
- Homayar/frontend/src/lib · 6
- src/lib · 7
- Homayar/frontend/src/types · 8
- scripts · 9
- Homayar/backend/routers/v1 · 10
- Homayar/frontend/src/components/auth · 11
- Homayar/backend/providers · 12
- Homayar/backend/jobs · 13
- Homayar/backend/tests · 14
- Homayar/backend/tests · 15
- . · 16
- Homayar/backend/jobs · 17
- Homayar/backend/jobs · 18
- Homayar/frontend/src/components/ui · 19
- Homayar/frontend/src/lib · 20
- Homayar/backend/services · 21
- Homayar/backend/services · 22
- Homayar/backend/providers · 23
- Homayar/backend/services · 24
- Homayar/backend/jobs · 25
- src/app/about · 26
- src/lib · 27
- Homayar/backend/providers · 28
- src/components · 29
- src/lib · 30
- . · 31
- Homayar/backend/security · 32
- Homayar/backend/services · 33
- Homayar/backend/services · 34
- Homayar/backend/routers/v1 · 35
- Homayar/backend/tests · 36
- Homayar/backend/services · 37
- Homayar/backend/tests · 38
- Homayar/frontend/src/components/platform · 39
- src/lib · 40
- Homayar/backend/tests · 41
- . · 42
- src/lib · 43
- src/components/catalog · 44
- src/components/platform · 45
- Homayar/backend/jobs · 46
- Homayar/backend/migrations/versions · 47
- Homayar/backend/storage · 48
- src/app/solutions · 49
- Homayar/backend/tests · 50
- Homayar/backend/services · 51
- Homayar/backend/tests · 52
- . · 53
- Homayar/backend/storage · 54
- infra/scripts/lib · 55
- src/lib · 56
- src/components/site · 57
- Homayar/backend/tests · 58
- Homayar/backend/schemas · 59
- Homayar/backend/security · 60
- src/app/blog/[slug] · 61
- Homayar/backend/database · 62
- Homayar/backend/database · 63
- Homayar/backend/database · 64
- Homayar/backend/tests · 65
- Homayar/backend/services · 66
- Homayar/backend/tests · 67
- src/lib · 68
- Homayar/backend/routers · 69
- Homayar/backend/services · 70
- src/components/site · 71
- Homayar/backend/tests · 72
- src/app/platform · 73
- Homayar/backend/providers · 74
- Homayar/backend/jobs · 75
- Homayar/backend/storage · 76
- Homayar/backend/services · 77
- Homayar/backend/tests · 78
- src/db · 79
- Homayar/backend/routers · 80
- Homayar/backend/tests · 81
- Homayar/backend/storage · 82
- infra/ci/tests · 83
- Homayar/backend/tests · 84
- Homayar/backend/services · 85
- Homayar/backend/tests · 86
- Homayar/frontend/src/components/users · 87
- scripts · 88
- Homayar/backend/providers · 89
- Homayar/backend/services · 90
- Homayar/backend/storage · 91
- Homayar/backend/tests · 92
- src/lib · 93
- src/app/success · 94
- src/lib · 95
- Homayar/backend/services · 96
- Homayar/backend/tests · 97
- Homayar/backend/providers · 98
- infra/ci · 99
- Homayar/backend/database · 100
- Homayar/backend/services · 101
- Homayar/backend/storage · 102
- Homayar/backend/storage · 103
- src/lib · 104
- Homayar/backend/providers · 105
- Homayar/backend/storage · 106
- Homayar/backend/database · 107
- Homayar/backend/services · 108
- Homayar/backend/tests · 109
- Homayar/backend/migrations/versions · 110
- Homayar/backend/migrations/versions · 111
- . · 112
- scripts · 113
- scripts · 114
- Homayar/frontend/src/test · 119
- . · 120
- Homayar/backend/schemas · 121
- Homayar/backend/tests · 122
- infra/ci · 123
- . · 124
- . · 125
- scripts · 126

## God Nodes (most connected - your core abstractions)
1. `Settings` - 249 edges
2. `inspection_session_factory()` - 96 edges
3. `get_settings()` - 84 edges
4. `WorkspaceContext` - 73 edges
5. `User` - 63 edges
6. `Workspace` - 61 edges
7. `Membership` - 60 edges
8. `record_audit_event()` - 57 edges
9. `Meeting` - 56 edges
10. `ProcessingError` - 56 edges

## Surprising Connections (you probably didn't know these)
- `FailureClass` --uses--> `Settings`  [INFERRED]
  Homayar/backend/jobs/failures.py → Homayar/backend/config.py
- `FailureClass` --uses--> `ReservationState`  [INFERRED]
  Homayar/backend/jobs/failures.py → Homayar/backend/database/saas_models.py
- `FailureClass` --uses--> `UsageReservation`  [INFERRED]
  Homayar/backend/jobs/failures.py → Homayar/backend/database/saas_models.py
- `RetryPolicy` --uses--> `Settings`  [INFERRED]
  Homayar/backend/jobs/failures.py → Homayar/backend/config.py
- `RetryPolicy` --uses--> `ReservationState`  [INFERRED]
  Homayar/backend/jobs/failures.py → Homayar/backend/database/saas_models.py

## Import Cycles
- None detected.

## Communities (134 total, 13 thin omitted)

### Community 0 - "Homayar/backend/media · 0"
Cohesion: 0.06
Nodes (80): CompletedProcess, Secure media inspection primitives used before object publication., AudioStreamMetadata, _build_ffprobe_command(), _codec_allowed(), EmptyMediaError, _ensure_file_unchanged(), ExtensionContentMismatchError (+72 more)

### Community 1 - "Homayar/frontend/src/types · 1"
Cohesion: 0.03
Nodes (102): SettingsPanel(), acceptInvitation(), changeMemberRole(), downloadPayload(), downloadRecording(), exportMeeting(), filenameFromContentDisposition(), getBillingUsage() (+94 more)

### Community 2 - "Homayar/backend/schemas · 2"
Cohesion: 0.08
Nodes (62): ActionStatus, _action_or_404(), create_action(), delete_action(), export_meeting(), get_summary(), get_transcript(), _like_pattern() (+54 more)

### Community 3 - "Homayar/frontend/src/components/platform · 3"
Cohesion: 0.07
Nodes (52): SearchPage, Header(), navItems, Sidebar(), InvitationAcceptPage(), idempotencyKey(), MeetingsPage(), ADMIN_ASSIGNABLE_ROLES (+44 more)

### Community 4 - ". · 4"
Cohesion: 0.09
Nodes (56): Who may open a share link (WP03-T06)., A named recipient of a ``recipients`` share, bound to a user once proven., ShareAudience, ShareLink, ShareRecipient, access_share(), _authorized_grant(), create_share() (+48 more)

### Community 5 - "Homayar/backend/services · 5"
Cohesion: 0.07
Nodes (47): Engine, FastAPI, get_settings(), build_engine(), configure_database(), create_schema_for_test_or_development(), get_db(), get_engine() (+39 more)

### Community 6 - "Homayar/frontend/src/lib · 6"
Cohesion: 0.07
Nodes (43): AgentStat, Message, suggestions, CallIncident, Incident, IncidentSeverity, api, API_BASE_URL (+35 more)

### Community 7 - "src/lib · 7"
Cohesion: 0.05
Nodes (34): ContactContent(), ContactCopy, DICT, en, Expectation, fa, metadata, ContactForm() (+26 more)

### Community 8 - "Homayar/frontend/src/types · 8"
Cohesion: 0.06
Nodes (38): CallsPage(), severityConfig, DashboardPage(), SENTIMENT_COLORS, KpiCard(), KpiCardProps, labelColors, LabelConfig (+30 more)

### Community 9 - "scripts · 9"
Cohesion: 0.15
Nodes (51): ACCENT_KEYS, ACCENTS, arcPath(), arcPoint(), background(), barChart(), card(), chooseAccent() (+43 more)

### Community 10 - "Homayar/backend/routers/v1 · 10"
Cohesion: 0.13
Nodes (47): _clear_session_cookies(), _client_ip(), forgot_password(), google_callback(), google_link_start(), google_start(), login(), logout() (+39 more)

### Community 11 - "Homayar/frontend/src/components/auth · 11"
Cohesion: 0.09
Nodes (38): App(), ForgotPasswordPage, InvitationAcceptPage, LoginPage, MeetingsPage, MembersPage, PhoneOtpRequestPage, PhoneOtpVerifyPage (+30 more)

### Community 12 - "Homayar/backend/providers · 12"
Cohesion: 0.09
Nodes (35): ProviderError, Exception, Provider failure safe to expose to application orchestration., _compatible_endpoint(), create_asr_provider(), create_diarization_provider(), create_intelligence_provider(), create_provider_bundle() (+27 more)

### Community 13 - "Homayar/backend/jobs · 13"
Cohesion: 0.12
Nodes (40): DispatchState, JobState, Meeting, MeetingStatus, ProcessingJob, str, Transactional-outbox state of a job's broker publication (WP06-T02)., FailureClass (+32 more)

### Community 14 - "Homayar/backend/tests · 14"
Cohesion: 0.13
Nodes (44): process_job(), Recover every expired RUNNING attempt (bounded batch). Returns counts., sweep_expired_leases(), FakeBroker, Records publications; can be told to fail N times or to block., test_recovery_parks_an_abandoned_attempt_past_the_job_deadline(), broker(), _claim() (+36 more)

### Community 15 - "Homayar/backend/tests · 15"
Cohesion: 0.11
Nodes (28): BaseSettings, model_validator, Validated runtime configuration for cloud and on-premise deployments., Settings, OAuthChallenge, _send_asanak_code(), _set_session_cookies(), SessionTokens (+20 more)

### Community 16 - ". · 16"
Cohesion: 0.12
Nodes (40): CurrentSubscriptionResponse, Entitlement, Plan, Subscription, SubscriptionStatus, get_billing_management(), get_plan_catalog(), get_subscription() (+32 more)

### Community 17 - "Homayar/backend/jobs · 17"
Cohesion: 0.12
Nodes (38): Event, Truthful per-stage outcome of one processing attempt (WP04-T01)., StageState, TranscriptSegment, _action_due_at(), ClaimedJob, _classify_exception(), _finalize_failure() (+30 more)

### Community 18 - "Homayar/backend/jobs · 18"
Cohesion: 0.10
Nodes (37): as_utc(), claim_verdict(), database_now(), grant_lease(), Lease, lease_expiry(), LeaseHeartbeat, LeasePolicy (+29 more)

### Community 19 - "Homayar/frontend/src/components/ui · 19"
Cohesion: 0.09
Nodes (29): Avatar(), AvatarBadge(), AvatarFallback(), AvatarGroup(), AvatarGroupCount(), AvatarImage(), Badge(), badgeVariants (+21 more)

### Community 20 - "Homayar/frontend/src/lib · 20"
Cohesion: 0.08
Nodes (39): MeetingDetailPage, DetailTab, MeetingDetailPage(), STAGE_LABELS, timestamp(), formatDate(), formatDuration(), createAction() (+31 more)

### Community 21 - "Homayar/backend/services · 21"
Cohesion: 0.20
Nodes (38): Membership, Workspace, WorkspaceInvitation, WorkspaceRole, can_grant(), can_manage_member(), WorkspaceRole, A caller manages a membership only if it could have granted its role. Self-… (+30 more)

### Community 22 - "Homayar/backend/services · 22"
Cohesion: 0.08
Nodes (35): load_prompt(), Prompt loader — reads prompt text files from the prompts directory., Load a prompt file by name (e.g. 'qc_fa', 'problem_en')., chat(), post, AI Copilot API router., classify_intent(), generate_response() (+27 more)

### Community 23 - "Homayar/backend/providers · 23"
Cohesion: 0.22
Nodes (30): Asanak SMS adapter using an injected endpoint and credential., EmailProvider, IntelligenceProvider, MockProviderDisabledError, ABC, Provider interfaces and sanitized execution errors., SMSProvider, Typed external-provider interfaces and implementations. (+22 more)

### Community 24 - "Homayar/backend/services · 24"
Cohesion: 0.16
Nodes (35): AuthIdentity, IdentityProvider, LoginSession, OneTimeToken, OtpChallenge, utcnow(), normalize_email(), normalize_iranian_mobile() (+27 more)

### Community 25 - "Homayar/backend/jobs · 25"
Cohesion: 0.10
Nodes (37): get_session_factory(), bind_job_context(), bind_tenant_context(), publish_processing_job(), Publish one message; raise on broker failure; return the message id.…, _as_utc(), _backoff(), dispatch_job() (+29 more)

### Community 26 - "src/app/about · 26"
Cohesion: 0.07
Nodes (26): AboutContent(), AboutCopy, DICT, en, fa, Value, metadata, BlogIndexPage() (+18 more)

### Community 27 - "src/lib · 27"
Cohesion: 0.11
Nodes (30): BILLING_EXPLANATIONS, Strings, en, fa, PLAN_ORDER, PlanCard(), Qa, Strings (+22 more)

### Community 28 - "Homayar/backend/providers · 28"
Cohesion: 0.23
Nodes (27): ActionItem, MeetingSummary, ProcessingStageRun, ProviderUsage, One stage of one processing attempt with its typed outcome and provenance., _speaker_for_segment(), _transcript_turns(), ASRProvider (+19 more)

### Community 29 - "src/components · 29"
Cohesion: 0.10
Nodes (26): metadata, metadata, DICT, en, fa, Step, SuccessCopy, CartClearer() (+18 more)

### Community 30 - "src/lib · 30"
Cohesion: 0.10
Nodes (24): inter, metadata, spaceGrotesk, vazirmatn, Footer(), Header(), LangSwitcher(), FullLogo() (+16 more)

### Community 31 - ". · 31"
Cohesion: 0.12
Nodes (33): accept_member_invitation(), delete_member(), _invitation_response(), invite_member(), list_invitations(), list_members(), _member_response(), delete (+25 more)

### Community 32 - "Homayar/backend/security · 32"
Cohesion: 0.13
Nodes (27): ColumnElement, assignment_effective_clause(), effective_assignments(), membership_effective_clause(), datetime, Enum, Session, Authorization policy: roles, permissions, grant hierarchy and record scope.… (+19 more)

### Community 33 - "Homayar/backend/services · 33"
Cohesion: 0.12
Nodes (29): aggregated_issues(), call_problems(), call_qc_details(), issues_by_category(), kpis(), get, qc_trend(), Dashboard API router — post-call metrics. (+21 more)

### Community 34 - "Homayar/backend/services · 34"
Cohesion: 0.22
Nodes (30): Effective-dated operational role of a workspace member inside a team., Team, TeamAssignment, TeamRole, User, WorkspaceContext, _as_utc(), AssigneeNotMemberError (+22 more)

### Community 35 - "Homayar/backend/routers/v1 · 35"
Cohesion: 0.17
Nodes (29): _as_utc(), assign_member(), _assignment_response(), create_workspace_team(), list_workspace_teams(), patch_team(), datetime, delete (+21 more)

### Community 36 - "Homayar/backend/tests · 36"
Cohesion: 0.11
Nodes (17): asyncio, AsanakSMSProvider, Any, Response, Submit one SMS to an injected Asanak endpoint. Message bodies and the API token…, FakeS3Client, FakeS3NotFound, Any (+9 more)

### Community 37 - "Homayar/backend/services · 37"
Cohesion: 0.11
Nodes (28): UserCreate, Update call processing status., Store transcript for a call., store_transcript(), update_call_status(), authenticate_user(), create_user(), create_user_from_voip() (+20 more)

### Community 38 - "Homayar/backend/tests · 38"
Cohesion: 0.17
Nodes (29): _session_cookies(), _meeting_of(), _audit(), broker(), _committed_usage(), _dead_letter_after_one_attempt(), _edit_job(), _error() (+21 more)

### Community 39 - "Homayar/frontend/src/components/platform · 39"
Cohesion: 0.11
Nodes (28): OverviewPage, UsagePage, COPY, formatDate(), formatDuration(), formatNumber(), MeetingRow(), metricLabel() (+20 more)

### Community 40 - "src/lib · 40"
Cohesion: 0.11
Nodes (28): ProductDetail(), splitOutcome(), generateMetadata(), generateStaticParams(), ProductDetailPage(), relatedProducts(), PricingContent(), getProductBySlug() (+20 more)

### Community 41 - "Homayar/backend/tests · 41"
Cohesion: 0.13
Nodes (25): Config, client(), email_outbox(), fixture, MonkeyPatch, Path, Session, pytest_collection_modifyitems() (+17 more)

### Community 42 - ". · 42"
Cohesion: 0.15
Nodes (28): delete_meeting(), download_recording(), _enforce_upload_rate_limit(), _get_meeting(), get_meeting_job(), list_meetings(), _meeting_response(), datetime (+20 more)

### Community 43 - "src/lib · 43"
Cohesion: 0.12
Nodes (21): metadata, Booked, BookingExperience(), dateKey(), Lang, STR, BUSINESS_TZ, dateKeyInTz() (+13 more)

### Community 44 - "src/components/catalog · 44"
Cohesion: 0.09
Nodes (21): HERO, metadata, BILLING_VALUES, CatalogExplorer(), en, ExplorerStrings, fa, featuredScore() (+13 more)

### Community 45 - "src/components/platform · 45"
Cohesion: 0.10
Nodes (22): Agent, AiAgentsContent(), AiAgentsCopy, DICT, en, fa, TONES, metadata (+14 more)

### Community 46 - "Homayar/backend/jobs · 46"
Cohesion: 0.12
Nodes (20): Celery, make_celery(), Celery app — background job processing for Homayar. Queue contract (WP06-T01):…, enqueue_processing_job(), Broker publication primitive (WP06-T01 queue contract). Application code does…, Best-effort publish without outbox bookkeeping (smoke tests, tooling)., process_meeting_job(), Periodic recovery: publish every due outbox row (bounded batch). (+12 more)

### Community 47 - "Homayar/backend/migrations/versions · 47"
Cohesion: 0.13
Nodes (20): apply_row_level_security(), disable_policy_statements(), enable_policy_statements(), ensure_app_role_statements(), existing_policy_tables(), PostgreSQL row-level security policies (WP03-T05). Single source of the DDL…, Policy tables that exist in the current schema (migrations add tables over…, Idempotently create the app role, grants and policies (PostgreSQL only).… (+12 more)

### Community 48 - "Homayar/backend/storage · 48"
Cohesion: 0.19
Nodes (21): ChecksumMismatchError, effective_size_limit(), generate_object_key(), InvalidTenantError, iter_stream(), normalize_expected_checksum(), ObjectTooLargeError, Enum (+13 more)

### Community 49 - "src/app/solutions · 49"
Cohesion: 0.08
Nodes (15): DICT, en, fa, Industry, INDUSTRY_STYLES, SolutionsContent(), SolutionsCopy, Team (+7 more)

### Community 50 - "Homayar/backend/tests · 50"
Cohesion: 0.16
Nodes (24): assert_production_posture(), describe_current_role(), RuntimeError, Effective privileges of ``current_user`` and RLS state of ``meetings``., Refuse to serve when the runtime role could bypass tenant isolation., RowLevelSecurityPostureError, _admin_execute(), _bind() (+16 more)

### Community 51 - "Homayar/backend/services · 51"
Cohesion: 0.19
Nodes (24): AuditEvent, How far a member/viewer sees inside a workspace (owners/admins see all)., RecordScope, AuditEventPage, _decode_audit_cursor(), _encode_audit_cursor(), get_workspace_settings(), InvalidAuditCursor (+16 more)

### Community 52 - "Homayar/backend/tests · 52"
Cohesion: 0.19
Nodes (8): login_user(), TestClient, register_user(), TestClient, test_deactivated_user_session_is_rejected(), test_refresh_session_is_stored_as_hash(), TestEmailAuthentication, TestPhoneAuthentication

### Community 53 - ". · 53"
Cohesion: 0.13
Nodes (21): AuditEventListResponse, computed_field, list_audit_events(), patch_workspace_settings(), datetime, get, HTTPException, patch (+13 more)

### Community 54 - "Homayar/backend/storage · 54"
Cohesion: 0.17
Nodes (13): InvalidObjectKeyError, LocalObjectStorage, BinaryIO, KeyFactory, Path, stat_result, Stores private objects below a tenant-scoped root with atomic publication., Path (+5 more)

### Community 55 - "infra/scripts/lib · 55"
Cohesion: 0.18
Nodes (15): backup-minio.sh script, backup-postgres.sh script, backup_relative_path(), canonical_existing_path(), compose(), compose_onprem(), die(), require_command() (+7 more)

### Community 56 - "src/lib · 56"
Cohesion: 0.12
Nodes (19): AVATAR_GRADIENTS, CaseStudiesPage(), metadata, AVATAR_GRADIENTS, CaseStudyPage(), generateMetadata(), generateStaticParams(), sitemap() (+11 more)

### Community 57 - "src/components/site · 57"
Cohesion: 0.12
Nodes (18): CanvasErrorBoundary, hasWebGL(), HeroCanvas(), prefersReducedMotion(), TiltCard(), useActiveInView(), useMedia(), useMounted() (+10 more)

### Community 58 - "Homayar/backend/tests · 58"
Cohesion: 0.15
Nodes (17): APIRoute, _cookies(), _doc_block(), _headers(), _insert_meeting(), _listed_titles(), _Persona, WP03-T03: the authorization matrix is explicit, deny-by-default and enforced.… (+9 more)

### Community 59 - "Homayar/backend/schemas · 59"
Cohesion: 0.15
Nodes (20): cancel_dispatch(), Withdraw a pending publication (same transaction as the cancellation)., DispatchResponse, LeaseResponse, MeetingDetailResponse, MeetingListResponse, MeetingResponse, MeetingUpdateRequest (+12 more)

### Community 60 - "Homayar/backend/security · 60"
Cohesion: 0.18
Nodes (19): Validation and tenant namespacing for client idempotency keys., scoped_idempotency_key(), validate_idempotency_key(), AccessTokenError, create_access_token(), decode_access_token(), make_refresh_token(), parse_refresh_session_id() (+11 more)

### Community 61 - "src/app/blog/[slug] · 61"
Cohesion: 0.15
Nodes (14): BlogPostPage(), formatDate(), generateMetadata(), generateStaticParams(), MONTHS, DICT, PrivacyContent(), metadata (+6 more)

### Community 62 - "Homayar/backend/database · 62"
Cohesion: 0.18
Nodes (20): BusinessInsight, Call, CallAgent, CallListItem, CallReport, ChatMessage, ChatRequest, DashboardKpis (+12 more)

### Community 63 - "Homayar/backend/database · 63"
Cohesion: 0.22
Nodes (18): apply_context(), bind_capability_context(), bind_service_scope(), bind_user_context(), clear_context(), current_context(), install_session_listeners(), _is_postgres() (+10 more)

### Community 64 - "Homayar/backend/database · 64"
Cohesion: 0.20
Nodes (17): DeclarativeBase, ApiKey, Base, JobDispatch, MeetingSpeaker, Notification, Organization, datetime (+9 more)

### Community 65 - "Homayar/backend/tests · 65"
Cohesion: 0.21
Nodes (18): Dispose configured state. Intended for isolated tests., reset_database_state(), client(), _cookies(), email_outbox(), login_user(), _mount_router(), fixture (+10 more)

### Community 66 - "Homayar/backend/services · 66"
Cohesion: 0.35
Nodes (15): ReservationState, UsageEvent, UsageReservation, commit_usage(), get_usage_snapshot(), Session, ValueError, QuotaExceededError (+7 more)

### Community 67 - "Homayar/backend/tests · 67"
Cohesion: 0.27
Nodes (17): inspection_session_factory(), Session, sessionmaker, Sessions for test *setup and inspection*, not for exercising the app. The…, test_mismatched_payload_is_rejected_before_any_processing(), test_rolled_back_upload_leaves_no_job_and_no_intent(), _processed_meeting(), test_share_links_are_hashed_expiring_auth_optional_and_revocable() (+9 more)

### Community 68 - "src/lib · 68"
Cohesion: 0.26
Nodes (14): GET(), checkoutSchema, POST(), contactSchema, POST(), POST(), CatalogPage(), metadata (+6 more)

### Community 69 - "Homayar/backend/routers · 69"
Cohesion: 0.15
Nodes (16): _extract_extension(), _normalize_event(), BaseModel, post, Request, VOIP webhook router — receives call events from Asterisk/3CX/FreePBX., Receive multiple call events at once (e.g., daily sync from VOIP system)., Auto-detect VOIP source and normalize to standard format. (+8 more)

### Community 70 - "Homayar/backend/services · 70"
Cohesion: 0.14
Nodes (15): JSONFormatter, Structured JSON logging for Homayar., Configure structured JSON logging., setup_logging(), blacklist_token(), get_job_status(), _get_redis(), is_token_blacklisted() (+7 more)

### Community 71 - "src/components/site · 71"
Cohesion: 0.12
Nodes (6): metadata, content(), Copy, DemoExperience(), Scenario, Turn

### Community 72 - "Homayar/backend/tests · 72"
Cohesion: 0.18
Nodes (15): Durable background job orchestration., broker(), _dispatch_row(), _job_payload(), datetime, fixture, postgres, WP06-T02: transactional outbox and durable dispatch. Failure boundaries… (+7 more)

### Community 73 - "src/app/platform · 73"
Cohesion: 0.13
Nodes (12): DICT, en, fa, Item, PlatformContent(), PlatformCopy, SECURITY_ICONS, Step (+4 more)

### Community 74 - "Homayar/backend/providers · 74"
Cohesion: 0.21
Nodes (8): _chat_completions_url(), OpenAICompatibleIntelligenceProvider, Any, AsyncClient, Response, OpenAI-compatible structured conversation-intelligence provider., Requests strict JSON from an injected or internally managed async client., IntelligenceRequest

### Community 75 - "Homayar/backend/jobs · 75"
Cohesion: 0.22
Nodes (15): _as_utc(), cancel_job(), deadline_passed(), park_job(), datetime, Meeting, ProcessingJob, Session (+7 more)

### Community 76 - "Homayar/backend/storage · 76"
Cohesion: 0.19
Nodes (10): ObjectStorage, ABC, BinaryIO, Tenant-scoped private object storage interface., create_object_storage(), Runtime object-storage construction., Private tenant-scoped object storage implementations., S3 storage using an injected client or a lazily constructed boto3 client. The… (+2 more)

### Community 77 - "Homayar/backend/services · 77"
Cohesion: 0.22
Nodes (12): ByteRange, _content_disposition(), deliver_recording(), _iter_reader(), parse_range(), HTTPException, Response, Bounded recording delivery with HTTP Range support (WP03-T06). Two delivery… (+4 more)

### Community 78 - "Homayar/backend/tests · 78"
Cohesion: 0.37
Nodes (13): _anonymous(), _cookies(), _headers(), _owner(), WP03-T06: share contract — audiences, separate grants, revocation, downloads.…, _settings(), _share(), _signup() (+5 more)

### Community 79 - "src/db · 79"
Cohesion: 0.20
Nodes (11): demoSchema, blogPosts, caseStudies, categories, checkoutSessions, demoRequests, leads, orderItems (+3 more)

### Community 80 - "Homayar/backend/routers · 80"
Cohesion: 0.29
Nodes (11): entities(), entity_detail(), neighborhood(), get, Graph Intelligence API router., stats(), get_entities(), get_entity_detail() (+3 more)

### Community 81 - "Homayar/backend/tests · 81"
Cohesion: 0.28
Nodes (11): _add_membership(), admin_client(), _error_code(), fixture, WorkspaceRole, _register_and_login(), test_audit_events_are_tenant_scoped_filtered_sanitized_and_paginated(), test_missing_workspace_uses_stable_not_found_error() (+3 more)

### Community 82 - "Homayar/backend/storage · 82"
Cohesion: 0.27
Nodes (8): InvalidPresignTTL, ObjectNotFoundError, Exception, Base storage error with a stable machine-readable code., StorageError, validate_owned_object_key(), BinaryIO, Exception

### Community 83 - "infra/ci/tests · 83"
Cohesion: 0.22
Nodes (12): commit_all(), GIT_AUTHOR_EMAIL, GIT_AUTHOR_NAME, GIT_COMMITTER_EMAIL, GIT_COMMITTER_NAME, GIT_CONFIG_NOSYSTEM, HOME, mkrepo() (+4 more)

### Community 84 - "Homayar/backend/tests · 84"
Cohesion: 0.30
Nodes (4): ASRRequest, ASRResult, _asr(), test_whisper_uses_injected_model_and_never_fabricates_speakers()

### Community 85 - "Homayar/backend/services · 85"
Cohesion: 0.24
Nodes (8): _rate_limit_share(), _MemoryCounter, RuntimeError, ValueError, RateLimiter, RateLimitExceeded, RateLimitUnavailable, Distributed fixed-window limiter with an isolated test/development fallback.

### Community 86 - "Homayar/backend/tests · 86"
Cohesion: 0.35
Nodes (10): billing_client(), fixture, TestClient, _register_session(), _session_cookies(), test_billing_catalog_subscription_and_usage_are_tenant_scoped(), test_billing_rbac_and_manual_management_are_explicitly_read_only(), test_current_subscription_can_be_empty_without_fabricating_a_plan() (+2 more)

### Community 87 - "Homayar/frontend/src/components/users · 87"
Cohesion: 0.31
Nodes (8): UserFormData, UserFormModalProps, UserFormValues, UsersPage(), User, UserCreate, UserRole, UserUpdate

### Community 88 - "scripts · 88"
Cohesion: 0.20
Nodes (10): arr, byCode, c, codeIdx, decode(), deep(), e, raw (+2 more)

### Community 89 - "Homayar/backend/providers · 89"
Cohesion: 0.20
Nodes (3): AsyncClient, field_validator, SecretStr

### Community 90 - "Homayar/backend/services · 90"
Cohesion: 0.27
Nodes (7): Path, UploadFile, ValueError, Bounded staging for multipart uploads before media inspection., stage_upload(), StagedUpload, UploadRejectedError

### Community 92 - "Homayar/backend/tests · 92"
Cohesion: 0.38
Nodes (8): _session_cookies(), test_meeting_reads_files_and_mutations_are_tenant_scoped(), _workspace_for_user(), _cookies(), _headers(), Two-tenant negative suite across API resources (WP03-T04 / F05). An intruder…, test_intruder_cannot_touch_another_workspaces_resources(), test_removed_member_loses_access_on_the_next_request()

### Community 93 - "src/lib · 93"
Cohesion: 0.29
Nodes (9): bookingSchema, isUniqueViolation(), POST(), bookings, buildICS(), CalendarEvent, googleCalendarUrl(), icsEscape() (+1 more)

### Community 94 - "src/app/success · 94"
Cohesion: 0.36
Nodes (8): POST(), SuccessContent(), SuccessSummary, getSessionSummary(), metadata, SuccessPage(), getStripe(), hasStripe()

### Community 95 - "src/lib · 95"
Cohesion: 0.31
Nodes (9): Attachment, COPY, escapeHtml(), formatWhen(), fromAddress(), hasEmail(), Lang, send() (+1 more)

### Community 96 - "Homayar/backend/services · 96"
Cohesion: 0.31
Nodes (8): HexColor, get_call_detail(), Get full call detail with transcript, QC, problems, insights., generate_call_pdf(), PDF report generation for call reports., Generate a PDF report for a call. Returns bytes., _score_color(), _severity_color()

### Community 97 - "Homayar/backend/tests · 97"
Cohesion: 0.28
Nodes (8): Test helper for isolated environment overrides., reset_settings_cache(), MonkeyPatch, PostgreSQL-only guarantees (WP01-T05). These tests are skipped on the SQLite…, ``alembic upgrade head`` on an empty PostgreSQL database must produce exactly…, Two workers must never claim the same job: a row held under ``FOR UPDATE`` by…, test_job_claim_skips_rows_locked_by_another_worker(), test_migrations_upgrade_head_matches_models()

### Community 99 - "infra/ci · 99"
Cohesion: 0.36
Nodes (6): config_error(), info(), check-source-contract.sh script, usage(), violation(), warn()

### Community 100 - "Homayar/backend/database · 100"
Cohesion: 0.32
Nodes (4): Neo4jConnection, Execute multiple write queries in a single transaction. queries_and_params:…, Store full analysis results for a call in a single transaction., store_analysis()

### Community 101 - "Homayar/backend/services · 101"
Cohesion: 0.43
Nodes (7): new_id(), normalize_workspace_slug(), create_personal_workspace(), ensure_plan_catalog(), list_user_workspaces(), Session, User

### Community 102 - "Homayar/backend/storage · 102"
Cohesion: 0.29
Nodes (4): PresignedAccess, PresignedAccessNotSupportedError, BaseModel, Short-lived read access produced by a storage backend.

### Community 103 - "Homayar/backend/storage · 103"
Cohesion: 0.38
Nodes (4): field_validator, ReadableStream, Metadata returned only after a complete, checksum-verified write., StoredObject

### Community 104 - "src/lib · 104"
Cohesion: 0.33
Nodes (6): Bucket, clientIp(), rateLimit(), RateLimitResult, store, sweep()

### Community 106 - "Homayar/backend/storage · 106"
Cohesion: 0.53
Nodes (4): StorageConfigurationError, validate_chunk_size(), Any, KeyFactory

### Community 107 - "Homayar/backend/database · 107"
Cohesion: 0.50
Nodes (4): create_calls(), Seed script — post-call voice intelligence domain., Generate call records from templates., seed()

### Community 108 - "Homayar/backend/services · 108"
Cohesion: 0.40
Nodes (5): Synchronous email send for Celery tasks., Send email using configured SMTP server., send_email(), send_email_sync(), send_invitation_email()

### Community 109 - "Homayar/backend/tests · 109"
Cohesion: 0.40
Nodes (3): parametrize, No fabricated analytics may exist on any importable backend path…, test_fabricated_analytics_modules_are_gone()

### Community 112 - ". · 112"
Cohesion: 0.50
Nodes (3): csp, nextConfig, securityHeaders

### Community 113 - "scripts · 113"
Cohesion: 0.50
Nodes (3): issues, PAGES, VIEWPORTS

### Community 114 - "scripts · 114"
Cohesion: 0.67
Nodes (3): main(), readJson(), root

## Knowledge Gaps
- **285 isolated node(s):** `_FormatProfile`, `queryClient`, `AgentStat`, `waveHeights`, `AuthCardProps` (+280 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **13 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ClaimedJob` connect `Homayar/backend/jobs · 17` to `Homayar/backend/database · 64`, `Homayar/backend/services · 66`, `Homayar/backend/providers · 74`, `Homayar/backend/providers · 12`, `Homayar/backend/jobs · 13`, `Homayar/backend/tests · 15`, `Homayar/backend/jobs · 18`, `Homayar/backend/tests · 84`, `Homayar/backend/services · 21`, `Homayar/backend/providers · 23`, `Homayar/backend/providers · 28`?**
  _High betweenness centrality (0.001) - this node is a cross-community bridge._
- **Why does `ProviderBundle` connect `Homayar/backend/providers · 28` to `Homayar/backend/providers · 74`, `Homayar/backend/providers · 23`, `Homayar/backend/providers · 12`, `Homayar/backend/tests · 15`?**
  _High betweenness centrality (0.000) - this node is a cross-community bridge._
- **Why does `_providers()` connect `Homayar/backend/tests · 14` to `Homayar/backend/providers · 28`?**
  _High betweenness centrality (0.000) - this node is a cross-community bridge._
- **Are the 8 inferred relationships involving `WorkspaceContext` (e.g. with `Settings` and `LoginSession`) actually correct?**
  _`WorkspaceContext` has 8 INFERRED edges - model-reasoned connections that need verification._
- **What connects `_FormatProfile`, `queryClient`, `AgentStat` to the rest of the system?**
  _285 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Homayar/backend/media · 0` be split into smaller, more focused modules?**
  _Cohesion score 0.056235134216785596 - nodes in this community are weakly interconnected._
- **Should `Homayar/frontend/src/types · 1` be split into smaller, more focused modules?**
  _Cohesion score 0.0348607367475292 - nodes in this community are weakly interconnected._