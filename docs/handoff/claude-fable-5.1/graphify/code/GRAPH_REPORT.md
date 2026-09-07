# Current workspace code graph

Generated 2026-09-07T14:48:38.069878+00:00. Local AST extraction; no external LLM/API calls and zero billed extraction tokens. Generated relationships are static evidence, not execution proof. Planned capabilities are in the separate planning graph. Community labels are deterministic source-directory descriptions.

Integrity diagnostics: {"missing_endpoint_edges": 0, "dangling_endpoint_edges": 613, "self_loop_edges": 1, "directed_same_endpoint_collapsed_edges": 226, "undirected_same_endpoint_collapsed_edges": 229}. Raw relations are preserved in extraction.json; the rendered graph may consolidate same-endpoint relations.

# Graph Report - /Users/emperor/Documents/AI/Homayar.com  (2026-09-07)

## Corpus Check
- 285 files · ~147,956 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2580 nodes · 7652 edges · 106 communities (97 shown, 9 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 626 edges (avg confidence: 0.51)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- API media · 0
- API storage · 1
- API services · 2
- API tests · 3
- API services · 4
- API routers · 5
- API providers · 6
- Website app · 7
- Website components · 8
- Project configuration · 9
- Project configuration · 10
- Console platform.ts · 11
- API providers · 12
- API jobs · 13
- API routers · 14
- scripts · 15
- Console platform-api.ts · 16
- Website lib · 17
- Project configuration · 18
- API providers · 19
- Website components · 20
- Console ui · 21
- API tests · 22
- API services · 23
- Website app · 24
- Console platform · 25
- API services · 26
- Project configuration · 27
- Console auth.ts · 28
- API services · 29
- Console index.ts · 30
- Console auth · 31
- API services · 32
- API services · 33
- Console api.ts · 34
- Website app · 35
- Website lib · 36
- Console platform-api.ts · 37
- Deployment operations · 38
- Website components · 39
- Console layout · 40
- Console hooks.ts · 41
- Website lib · 42
- Console UI · 43
- Website components · 44
- Website lib · 45
- Website db · 46
- API services · 47
- API auth · 48
- API routers · 49
- Website components · 50
- API database · 51
- API services · 52
- Website app · 53
- API routers · 54
- API services · 55
- API routers · 56
- Console platform · 57
- Website components · 58
- Website app · 59
- API auth · 60
- API services · 61
- API services · 62
- Console platform · 63
- API auth · 64
- Website components · 65
- API services · 66
- Console platform-api.ts · 67
- Console users · 68
- scripts · 69
- API auth · 70
- API services · 71
- Website lib · 72
- API providers · 73
- API database · 74
- Website components · 75
- API auth · 76
- API routers · 77
- Project configuration · 78
- scripts · 79
- API database · 80
- API services · 81
- Console platform-api.ts · 82
- Project configuration · 83
- scripts · 84
- scripts · 85
- API auth · 86
- Console setup.ts · 90
- Website app · 91
- tests · 92
- API jobs · 93
- API schemas · 94
- API tests · 95
- Deployment operations · 96
- scripts · 97

## God Nodes (most connected - your core abstractions)
1. `Settings` - 171 edges
2. `get_settings()` - 90 edges
3. `WorkspaceContext` - 53 edges
4. `User` - 50 edges
5. `get_session_factory()` - 48 edges
6. `Workspace` - 46 edges
7. `record_audit_event()` - 46 edges
8. `useLocale()` - 46 edges
9. `ProviderError` - 39 edges
10. `Membership` - 37 edges

## Surprising Connections (you probably didn't know these)
- `Neo4jUserDatabase` --uses--> `User`  [INFERRED]
  Homayar/backend/auth/adapter.py → Homayar/backend/auth/models.py
- `Neo4jUserDatabase` --uses--> `Neo4jConnection`  [INFERRED]
  Homayar/backend/auth/adapter.py → Homayar/backend/database/neo4j_connection.py
- `_BcryptPasswordHelper` --uses--> `Neo4jUserDatabase`  [INFERRED]
  Homayar/backend/auth/manager.py → Homayar/backend/auth/adapter.py
- `_BcryptPasswordHelper` --uses--> `User`  [INFERRED]
  Homayar/backend/auth/manager.py → Homayar/backend/auth/models.py
- `_BcryptPasswordHelper` --uses--> `Neo4jConnection`  [INFERRED]
  Homayar/backend/auth/manager.py → Homayar/backend/database/neo4j_connection.py

## Import Cycles
- None detected.

## Communities (106 total, 9 thin omitted)

### Community 0 - "API media · 0"
Cohesion: 0.06
Nodes (80): CompletedProcess, Secure media inspection primitives used before object publication., AudioStreamMetadata, _build_ffprobe_command(), _codec_allowed(), EmptyMediaError, _ensure_file_unchanged(), ExtensionContentMismatchError (+72 more)

### Community 1 - "API storage · 1"
Cohesion: 0.05
Nodes (64): ChecksumMismatchError, effective_size_limit(), generate_object_key(), InvalidObjectKeyError, InvalidPresignTTL, InvalidStreamError, InvalidTenantError, iter_stream() (+56 more)

### Community 2 - "API services · 2"
Cohesion: 0.07
Nodes (92): Membership, User, Workspace, WorkspaceInvitation, WorkspaceRole, accept_member_invitation(), delete_member(), _invitation_response() (+84 more)

### Community 3 - "API tests · 3"
Cohesion: 0.07
Nodes (51): get_session_factory(), WorkspaceNotFoundError, login_user(), TestClient, register_user(), _add_membership(), admin_client(), _error_code() (+43 more)

### Community 4 - "API services · 4"
Cohesion: 0.05
Nodes (56): Celery, FastAPI, get_jwt_strategy(), get_refresh_jwt_strategy(), Auth backends — JWT bearer + refresh token support., make_celery(), Celery app — background job processing for Homayar., get_settings() (+48 more)

### Community 5 - "API routers · 5"
Cohesion: 0.09
Nodes (60): ActionItem, ActionStatus, _action_or_404(), create_action(), delete_action(), export_meeting(), get_summary(), get_transcript() (+52 more)

### Community 6 - "API providers · 6"
Cohesion: 0.07
Nodes (43): ProviderError, Exception, Provider failure safe to expose to application orchestration., Explicit response for deployments without a diarization engine., UnavailableDiarizationProvider, _compatible_endpoint(), create_asr_provider(), create_diarization_provider() (+35 more)

### Community 7 - "Website app · 7"
Cohesion: 0.06
Nodes (49): dynamic, BlogIndexPage(), metadata, BlogPostPage(), formatDate(), generateMetadata(), generateStaticParams(), MONTHS (+41 more)

### Community 8 - "Website components · 8"
Cohesion: 0.06
Nodes (32): metadata, Footer(), Header(), LangSwitcher(), LocalePersistence(), FullLogo(), Logo(), ExhibitionHero() (+24 more)

### Community 9 - "Project configuration · 9"
Cohesion: 0.08
Nodes (56): CurrentSubscriptionResponse, DeclarativeBase, ApiKey, Base, Entitlement, Notification, Organization, Plan (+48 more)

### Community 10 - "Project configuration · 10"
Cohesion: 0.08
Nodes (54): enqueue_processing_job(), process_meeting_job(), delete_meeting(), download_recording(), _enforce_upload_rate_limit(), _get_meeting(), get_meeting_job(), _job_response() (+46 more)

### Community 11 - "Console platform.ts · 11"
Cohesion: 0.06
Nodes (59): getPlanCatalog(), NONTERMINAL_JOB_STATES, NONTERMINAL_MEETING_STATUSES, platformQueryKeys, usePlanCatalog(), ActionItem, AtLeastOne, AuditEvent (+51 more)

### Community 12 - "API providers · 12"
Cohesion: 0.18
Nodes (38): ASRProvider, DiarizationProvider, EmailProvider, IntelligenceProvider, MockProviderDisabledError, ABC, Provider interfaces and sanitized execution errors., SMSProvider (+30 more)

### Community 13 - "API jobs · 13"
Cohesion: 0.14
Nodes (46): ActionItem, JobState, Meeting, MeetingSpeaker, MeetingStatus, MeetingSummary, ProcessingJob, ProviderUsage (+38 more)

### Community 14 - "API routers · 14"
Cohesion: 0.12
Nodes (49): BaseSettings, model_validator, Validated runtime configuration for cloud and on-premise deployments., Settings, _clear_session_cookies(), _client_ip(), forgot_password(), google_callback() (+41 more)

### Community 15 - "scripts · 15"
Cohesion: 0.15
Nodes (51): ACCENT_KEYS, ACCENTS, arcPath(), arcPoint(), background(), barChart(), card(), chooseAccent() (+43 more)

### Community 16 - "Console platform-api.ts · 16"
Cohesion: 0.07
Nodes (47): DetailTab, MeetingDetailPage(), timestamp(), createAction(), createShare(), deleteAction(), deleteMeeting(), downloadPayload() (+39 more)

### Community 17 - "Website lib · 17"
Cohesion: 0.09
Nodes (32): ProductDetail(), splitOutcome(), Strings, copy, PLAN_ORDER, PricingContent(), DICT, PrivacyContent() (+24 more)

### Community 18 - "Project configuration · 18"
Cohesion: 0.10
Nodes (42): AuditEventListResponse, AuditEvent, list_audit_events(), patch_workspace_settings(), _permission_denied(), datetime, get, HTTPException (+34 more)

### Community 19 - "API providers · 19"
Cohesion: 0.07
Nodes (25): AsanakSMSProvider, Any, AsyncClient, Response, Asanak SMS adapter using an injected endpoint and credential., Submit one SMS to an injected Asanak endpoint. Message bodies and the API token…, DiarizationSegment, field_validator (+17 more)

### Community 20 - "Website components · 20"
Cohesion: 0.08
Nodes (37): DICT, en, fa, Step, SuccessContent(), SuccessCopy, SuccessSummary, AddToCartButton() (+29 more)

### Community 21 - "Console ui · 21"
Cohesion: 0.09
Nodes (29): Avatar(), AvatarBadge(), AvatarFallback(), AvatarGroup(), AvatarGroupCount(), AvatarImage(), Badge(), badgeVariants (+21 more)

### Community 22 - "API tests · 22"
Cohesion: 0.11
Nodes (34): Engine, build_engine(), configure_database(), create_schema_for_test_or_development(), get_db(), get_engine(), Session, Dispose configured state. Intended for isolated tests. (+26 more)

### Community 23 - "API services · 23"
Cohesion: 0.16
Nodes (37): IdentityProvider, LoginSession, new_id(), OneTimeToken, normalize_email(), create_access_token(), hash_password(), make_refresh_token() (+29 more)

### Community 24 - "Website app · 24"
Cohesion: 0.07
Nodes (27): metadata, SUGGESTED_LINKS, AboutContent(), AboutCopy, DICT, en, fa, Value (+19 more)

### Community 25 - "Console platform · 25"
Cohesion: 0.10
Nodes (32): SearchPage, COPY, formatDate(), formatDuration(), meetingUrl(), SearchPage(), AuditPanel(), formatAuditDetail() (+24 more)

### Community 26 - "API services · 26"
Cohesion: 0.14
Nodes (29): AuthIdentity, OAuthChallenge, OtpChallenge, utcnow(), normalize_iranian_mobile(), normalize_workspace_slug(), AccountLinkRequiredError, complete_google_oauth() (+21 more)

### Community 27 - "Project configuration · 27"
Cohesion: 0.14
Nodes (28): access_share(), _active_share(), create_share(), list_shares(), _meeting_or_404(), delete, get, HTTPException (+20 more)

### Community 28 - "Console auth.ts · 28"
Cohesion: 0.15
Nodes (26): AuthContext, AuthContextValue, AuthStatus, ErrorPayload, getCurrentSession(), getCurrentUser(), login(), loginSession() (+18 more)

### Community 29 - "API services · 29"
Cohesion: 0.13
Nodes (27): aggregated_issues(), call_problems(), call_qc_details(), issues_by_category(), kpis(), get, qc_trend(), Dashboard API router — post-call metrics. (+19 more)

### Community 30 - "Console index.ts · 30"
Cohesion: 0.08
Nodes (23): severityConfig, labelColors, LabelConfig, nodeTypes, AggregatedIssue, Alert, BusinessInsight, Call (+15 more)

### Community 31 - "Console auth · 31"
Cohesion: 0.20
Nodes (20): AuthCard(), AuthCardProps, AuthDivider(), AuthField(), AuthFieldProps, FormAlert(), GoogleSignInButton(), PasswordField() (+12 more)

### Community 32 - "API services · 32"
Cohesion: 0.11
Nodes (24): load_prompt(), Prompt loader — reads prompt text files from the prompts directory., Load a prompt file by name (e.g. 'qc_fa', 'problem_en')., _get_prompt(), _mock_insight(), _mock_problem(), _mock_qc(), _mock_teacher() (+16 more)

### Community 33 - "API services · 33"
Cohesion: 0.12
Nodes (22): issue_tokens(), logout(), BaseModel, post, Additional auth routers — refresh token, logout with Redis blacklist., Exchange a valid refresh token for a new access token + refresh token., Blacklist the current access token in Redis., Create access + refresh token pair from user data. (+14 more)

### Community 34 - "Console api.ts · 34"
Cohesion: 0.10
Nodes (17): AgentStat, Message, suggestions, CallIncident, Incident, IncidentSeverity, api, API_BASE_URL (+9 more)

### Community 35 - "Website app · 35"
Cohesion: 0.08
Nodes (15): DICT, en, fa, Industry, INDUSTRY_STYLES, SolutionsContent(), SolutionsCopy, Team (+7 more)

### Community 36 - "Website lib · 36"
Cohesion: 0.12
Nodes (23): BlogIndex(), copy, formatDate(), CaseStudyIndex(), copy, gradients, BLOG_POSTS_FA, BlogPostFa (+15 more)

### Community 37 - "Console platform-api.ts · 37"
Cohesion: 0.13
Nodes (23): MembersPage, ADMIN_ASSIGNABLE_ROLES, ALL_ASSIGNABLE_ROLES, canManageTarget(), formatDate(), InvitationManagement(), InvitationRow(), MemberDirectory() (+15 more)

### Community 38 - "Deployment operations · 38"
Cohesion: 0.18
Nodes (15): backup-minio.sh script, backup-postgres.sh script, backup_relative_path(), canonical_existing_path(), compose(), compose_onprem(), die(), require_command() (+7 more)

### Community 39 - "Website components · 39"
Cohesion: 0.12
Nodes (18): CanvasErrorBoundary, hasWebGL(), HeroCanvas(), prefersReducedMotion(), TiltCard(), useActiveInView(), useMedia(), useMounted() (+10 more)

### Community 40 - "Console layout · 40"
Cohesion: 0.16
Nodes (17): RequireAuth(), AppLayout(), Header(), navItems, Sidebar(), InvitationAcceptPage(), SettingsPage(), UsagePage() (+9 more)

### Community 41 - "Console hooks.ts · 41"
Cohesion: 0.18
Nodes (15): CallsPage(), DashboardPage(), SENTIMENT_COLORS, KpiCard(), KpiCardProps, ReportPage(), useAnalyzeCall(), useCallDetail() (+7 more)

### Community 42 - "Website lib · 42"
Cohesion: 0.18
Nodes (20): bookingSchema, isUniqueViolation(), POST(), buildICS(), CalendarEvent, dateKeyInTz(), googleCalendarUrl(), HORIZON_DAYS (+12 more)

### Community 43 - "Console UI · 43"
Cohesion: 0.10
Nodes (17): App(), ForgotPasswordPage, InvitationAcceptPage, LoginPage, MeetingDetailPage, MeetingsPage, OverviewPage, PhoneOtpRequestPage (+9 more)

### Community 44 - "Website components · 44"
Cohesion: 0.11
Nodes (12): metadata, ContactContent(), metadata, Booked, BookingExperience(), dateKey(), Lang, STR (+4 more)

### Community 45 - "Website lib · 45"
Cohesion: 0.22
Nodes (15): GET(), contactSchema, POST(), demoSchema, POST(), leads, getDb(), hasDb() (+7 more)

### Community 46 - "Website db · 46"
Cohesion: 0.19
Nodes (15): checkoutSchema, POST(), POST(), blogPosts, bookings, caseStudies, categories, checkoutSessions (+7 more)

### Community 47 - "API services · 47"
Cohesion: 0.14
Nodes (17): HexColor, export_call_pdf(), get_call(), list_calls(), get, Export call report as PDF., List all calls with optional filters., Get full call detail with transcript, QC report, problems, insights. (+9 more)

### Community 48 - "API auth · 48"
Cohesion: 0.17
Nodes (9): Neo4j adapter for fastapi-users — bridges BaseUserDatabase to Neo4j graph., Auth package — fastapi-users integration with Neo4j., get_user_db(), get_user_manager(), User manager — handles password reset, email verification, lifecycle events., User models for fastapi-users — extends base with Homayar-specific fields., User model matching Neo4j User nodes + fastapi-users requirements. Attribute-…, User (+1 more)

### Community 49 - "API routers · 49"
Cohesion: 0.14
Nodes (18): _extract_extension(), _normalize_event(), BaseModel, post, Request, Receive multiple call events at once (e.g., daily sync from VOIP system)., Auto-detect VOIP source and normalize to standard format., Extract extension number from various formats. (+10 more)

### Community 50 - "Website components · 50"
Cohesion: 0.12
Nodes (6): metadata, content(), Copy, DemoExperience(), Scenario, Turn

### Community 51 - "API database · 51"
Cohesion: 0.22
Nodes (16): BusinessInsight, Call, CallAgent, CallListItem, CallReport, ChatMessage, DashboardKpis, GraphData (+8 more)

### Community 52 - "API services · 52"
Cohesion: 0.20
Nodes (15): UserCreate, UserUpdate, authenticate_ldap(), Authenticate user via LDAP bind. Returns user attrs or None., authenticate_user(), create_user(), ensure_admin_exists(), get_user_by_email() (+7 more)

### Community 53 - "Website app · 53"
Cohesion: 0.13
Nodes (12): DICT, en, fa, Item, PlatformContent(), PlatformCopy, SECURITY_ICONS, Step (+4 more)

### Community 54 - "API routers · 54"
Cohesion: 0.15
Nodes (15): LoginRequest, User, api_delete_user(), api_list_users(), me(), delete, get, Authentication API router — login, user management. (+7 more)

### Community 55 - "API services · 55"
Cohesion: 0.40
Nodes (13): ReservationState, UsageReservation, commit_usage(), get_usage_snapshot(), Session, ValueError, QuotaExceededError, release_usage() (+5 more)

### Community 56 - "API routers · 56"
Cohesion: 0.22
Nodes (14): HTTPAuthorizationCredentials, Validate JWT token and return decoded payload., require_auth(), entities(), entity_detail(), neighborhood(), get, Graph Intelligence API router. (+6 more)

### Community 57 - "Console platform · 57"
Cohesion: 0.19
Nodes (15): COPY, formatDate(), formatDuration(), formatNumber(), MeetingRow(), metricLabel(), OverviewCopy, OverviewPage() (+7 more)

### Community 58 - "Website components · 58"
Cohesion: 0.15
Nodes (12): CatalogExplorer(), en, ExplorerStrings, fa, featuredScore(), INDUSTRY_VALUES, parseList(), parseOne() (+4 more)

### Community 59 - "Website app · 59"
Cohesion: 0.15
Nodes (11): Agent, AiAgentsContent(), AiAgentsCopy, DICT, en, fa, TONES, metadata (+3 more)

### Community 60 - "API auth · 60"
Cohesion: 0.29
Nodes (5): _neo4j_to_user(), Neo4jUserDatabase, User, Convert a Neo4j query result dict to a User model., fastapi-users database adapter backed by Neo4j.

### Community 61 - "API services · 61"
Cohesion: 0.18
Nodes (12): process_call_async(), Full pipeline: STT → 4-Agent Analysis → Neo4j storage., analyze_call(), Trigger analysis pipeline. Returns job_id immediately if background=True., Update call processing status., Store transcript for a call., store_transcript(), update_call_status() (+4 more)

### Community 62 - "API services · 62"
Cohesion: 0.26
Nodes (11): ChatRequest, chat(), post, AI Copilot API router., classify_intent(), generate_response(), process_question(), AI Copilot service — post-call intelligence assistant. (+3 more)

### Community 63 - "Console platform · 63"
Cohesion: 0.22
Nodes (12): SharedMeetingPage, COPY, formatDate(), formatDuration(), InsightList(), insightText(), SharedMeetingPage(), ShareErrorPayload (+4 more)

### Community 64 - "API auth · 64"
Cohesion: 0.32
Nodes (5): Request, User, Homayar user manager with password reset + email verification., UserManager, UUIDIDMixin

### Community 65 - "Website components · 65"
Cohesion: 0.27
Nodes (11): AgentGlyph(), AnalystGlyph(), base(), CoachGlyph(), GlyphProps, GLYPHS, GuardianGlyph(), JudgeGlyph() (+3 more)

### Community 66 - "API services · 66"
Cohesion: 0.27
Nodes (7): _MemoryCounter, RuntimeError, ValueError, RateLimiter, RateLimitExceeded, RateLimitUnavailable, Distributed fixed-window limiter with an isolated test/development fallback.

### Community 67 - "Console platform-api.ts · 67"
Cohesion: 0.31
Nodes (9): idempotencyKey(), MeetingsPage(), formatDate(), formatDuration(), listMeetings(), meetingRefetchInterval(), uploadMeeting(), useMeetings() (+1 more)

### Community 68 - "Console users · 68"
Cohesion: 0.31
Nodes (8): UserFormData, UserFormModalProps, UserFormValues, UsersPage(), User, UserCreate, UserRole, UserUpdate

### Community 69 - "scripts · 69"
Cohesion: 0.20
Nodes (10): arr, byCode, c, codeIdx, decode(), deep(), e, raw (+2 more)

### Community 70 - "API auth · 70"
Cohesion: 0.22
Nodes (6): BaseUserCreate, BaseUserUpdate, Registration schema — extends fastapi-users BaseUserCreate., Update schema — extends fastapi-users BaseUserUpdate., UserCreate, UserUpdate

### Community 71 - "API services · 71"
Cohesion: 0.27
Nodes (9): aggregate_issues(), _build_aggregation_prompt(), _get_problems_from_db(), Issue aggregation service — clusters ProblemDetection data into aggregated…, Build prompt for LLM aggregation., Aggregate all problems into clustered insights using LLM., Simple aggregation without LLM — group by category., Fetch all problem detection records from Neo4j. (+1 more)

### Community 72 - "Website lib · 72"
Cohesion: 0.31
Nodes (9): Attachment, COPY, escapeHtml(), formatWhen(), fromAddress(), hasEmail(), Lang, send() (+1 more)

### Community 74 - "API database · 74"
Cohesion: 0.32
Nodes (4): Neo4jConnection, Execute multiple write queries in a single transaction. queries_and_params:…, Store full analysis results for a call in a single transaction., store_analysis()

### Community 75 - "Website components · 75"
Cohesion: 0.25
Nodes (7): ContactForm(), ContactFormCopy, DICT, en, fa, FieldErrors, TEAM_SIZE_OPTIONS

### Community 76 - "API auth · 76"
Cohesion: 0.29
Nodes (3): _BcryptPasswordHelper, Adapter so fastapi-users uses bcrypt instead of argon2., Returns (is_valid: bool, new_hash: str | None).

### Community 77 - "API routers · 77"
Cohesion: 0.29
Nodes (7): api_create_user(), login(), LoginRequest, post, UserCreate, Authenticate user — supports local and LDAP users., Create a new user. Admin only.

### Community 78 - "Project configuration · 78"
Cohesion: 0.29
Nodes (7): api_update_user(), UserUpdate, Update user. Admin can update anyone, users can update themselves., get_user_by_id(), UserUpdate, update_user(), put

### Community 79 - "scripts · 79"
Cohesion: 0.33
Nodes (4): apiDir, outputDir, parkedApiDir, repositoryRoot

### Community 80 - "API database · 80"
Cohesion: 0.50
Nodes (4): create_calls(), Seed script — post-call voice intelligence domain., Generate call records from templates., seed()

### Community 81 - "API services · 81"
Cohesion: 0.40
Nodes (4): JSONFormatter, Configure structured JSON logging., setup_logging(), LogRecord

### Community 82 - "Console platform-api.ts · 82"
Cohesion: 0.40
Nodes (5): SettingsPanel(), getWorkspaceSettings(), updateWorkspaceSettings(), useUpdateWorkspaceSettings(), useWorkspaceSettings()

### Community 83 - "Project configuration · 83"
Cohesion: 0.50
Nodes (3): csp, nextConfig, securityHeaders

### Community 84 - "scripts · 84"
Cohesion: 0.50
Nodes (3): issues, PAGES, VIEWPORTS

### Community 85 - "scripts · 85"
Cohesion: 0.67
Nodes (3): main(), readJson(), root

### Community 86 - "API auth · 86"
Cohesion: 0.67
Nodes (3): BaseModel, Response schema — what's returned to the client., UserRead

## Knowledge Gaps
- **271 isolated node(s):** `_FormatProfile`, `queryClient`, `AgentStat`, `waveHeights`, `AuthCardProps` (+266 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **9 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Are the 7 inferred relationships involving `WorkspaceContext` (e.g. with `Settings` and `LoginSession`) actually correct?**
  _`WorkspaceContext` has 7 INFERRED edges - model-reasoned connections that need verification._
- **What connects `_FormatProfile`, `queryClient`, `AgentStat` to the rest of the system?**
  _271 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `API media · 0` be split into smaller, more focused modules?**
  _Cohesion score 0.056235134216785596 - nodes in this community are weakly interconnected._
- **Should `API storage · 1` be split into smaller, more focused modules?**
  _Cohesion score 0.05025568682771998 - nodes in this community are weakly interconnected._
- **Should `API services · 2` be split into smaller, more focused modules?**
  _Cohesion score 0.07153376814643383 - nodes in this community are weakly interconnected._
- **Should `API tests · 3` be split into smaller, more focused modules?**
  _Cohesion score 0.06947368421052631 - nodes in this community are weakly interconnected._
- **Should `API services · 4` be split into smaller, more focused modules?**
  _Cohesion score 0.05004389815627744 - nodes in this community are weakly interconnected._

## Lossless relationship companion

`graph.full.json` and `graph.full.graphml` retain all 8492 raw AST relations, including parallel relations and self-loops. 134 explicitly unresolved reference nodes preserve original endpoints without claiming a source definition. All full-graph endpoints exist; resolving those references to definitions remains a static-analysis limitation. See FULL_GRAPH_INTEGRITY.json and UNRESOLVED_REFERENCES.json.
