# Complete remaining-work report

Prepared 2026-09-07. No product implementation was performed during preparation.

## Audited baseline



Scores are engineering judgments about the inspected evidence, not certification or benchmark results. Ten means repeatably demonstrated with release evidence.

| Dimension | Score / 10 | Verdict |
|---|---:|---|
| Product coherence | 4 | Three generations coexist: legacy Flask, post-call FastAPI/Neo4j, and an uncommitted relational meeting platform. |
| Demonstration readiness | 5 | A supervised demonstration with explicitly labeled fixtures is feasible; a faithful, unsupervised end-to-end product demonstration is not established. |
| Paid pilot readiness | 3 | Appropriate only after P0 containment and a measured audio pipeline; do not ingest sensitive production recordings yet. |
| Enterprise readiness | 2 | Isolation, identity, audit and deployment work exists locally, but operational, AI-quality and security gates remain. |
| SaaS readiness | 3 | The local workspace/quota/session foundations are substantial. Committed branches do not deliver that architecture. |
| On-premises readiness | 3 | Local inference ports and Compose templates exist; the default package does not provide a working, tested offline intelligence stack. |
| AI evidence and quality | 2 | Real Whisper exists, but real diarization, calibrated QA, Persian gold evaluations and reliable evidence presentation are missing. |
| Commercial opportunity | 6 | Plausible wedge in private Persian contact centers; demand, willingness to pay, win rates and margins remain unvalidated. |

**The biggest opportunity is not a larger feature list. It is earning trust in a narrow workflow: upload or ingest a recorded call, show what was said by whom, explain the score with evidence, let a supervisor correct it, and prove that review work becomes faster.**


## Immediate blockers


P0 means a blocker to handling real customer data, truthful selling or repeatable deployment. Scope matters: old-branch defects do not automatically imply default v1 exposure.

| ID | Blocker | Evidence / confidence | Required disposition |
|---|---|---|---|
| B01 | Credential material in master history | OpenAI project-key-shaped literals in master app.py:17, chat_intell.py:6, org_intell.py:7; no credential validation attempted. High confidence embedded material, unknown validity. | Owner-led revocation/rotation and access-log review; record affected services; then authorized history cleanup and secret scanning. Never print values. |
| B02 | Legacy authorization and graph exposure | Global graph node/property returns; no tenant scope. Old auth stacks have divergent revocation/role behavior. High confidence code exposure, no live exploit attempted. | Default deny legacy surfaces; migrate functionality through scoped v1 APIs; explicit property allowlists; negative tests. |
| B03 | Fabricated business intelligence | Time-window speakers; random/default analysis values; mock provenance lost in persistence. Verified. | Eliminate production fallbacks; migrate old results as untrusted; visible partial/unavailable states; no fabricated QA. |
| B04 | Non-reproducible source and release | Most advanced code uncommitted; infra in another dirty repo; disconnected master. Verified. | Review/preserve changes, define source ownership, clean-checkout CI, pinned release artifacts. |
| B05 | Broken or unproven processing wiring | processing queue versus default worker subscription; missing local AI dependency by default; no real diarization. Verified config/source, broker E2E not run. | Correct worker queues; bundle providers/models; fail readiness if mandatory capabilities unavailable. |
| B06 | Tenant isolation not enterprise-complete | W membership checks exist; no DB RLS or team-scoped record policies found; legacy graph unscoped. | Add enforced scoped repositories plus DB policies and cross-surface adversarial tests. |
| B07 | Retention and confidentiality promises exceed implementation | Fields/UI without reliable purge lifecycle; object failure only logged; public sharing enabled for members. | Enforced retention, restrictive sharing defaults, purge reconciliation, legal-hold and export policy. |
| B08 | Unsupported marketing/security/customer claims | Live enterprise page promises SSO/SCIM, SLA/audit assurances and named results without supporting release/customer evidence. | Withdraw or label unverified claims; implement a versioned claim-evidence register. |
| B09 | No qualified real-model/Persian evaluation | Existing tests use test providers; no representative gold corpus or measured diarization/QA quality. | Establish consented evaluation corpus and release gates before selling automated scoring. |

Two additional committed-path correctness defects warrant retirement rather than piecemeal product expansion: call_service passes named parameters to execute_write even though the wrapper accepts a parameter dictionary, and structured training-tip dictionaries are assigned as Neo4j property values. The former is a code-demonstrable signature mismatch; the latter is incompatible with Neo4j property types if the prompt's structured output is returned. Neither was tested against a live Neo4j database during this audit. [Call service](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/services/call_service.py:69), [driver wrapper](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/database/neo4j_connection.py:37)


## Current implementation inventory


No end-to-end business capability earns “Production Ready” from this audit. Some individual primitives are functional and well tested; deployment and real-model evidence remain insufficient.

| Capability | Committed product | Local overlay | Classification / evidence |
|---|---|---|---|
| Audio upload | Base64, shared local file path, weak validation | Streaming upload, quarantine, size/signature/ffprobe duration checks, private storage | D: MVP/unsafe; W: Functional but immature. [Validation](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/media/validation.py) |
| Codecs | Limited filename-oriented handling | WAV, MP3, OGG, M4A, AAC, FLAC, MP4, WebM allowlist; decoder inspection | Partial: allowed extension does not prove every codec profile. Test actual PBX recordings. |
| Normalization/noise | Decoder behavior, no measured preprocessing contract | Container/channel/sample-rate metadata | Partial; no validated denoise policy or explicit channel-preserving preprocessing stage. |
| VAD | faster-whisper VAD enabled | ASR adapter VAD | Functional but immature; VAD output not a complete acoustic timeline. |
| Persian/English/multilingual STT | Real Whisper base CPU, auto language supported | Configurable local Whisper | MVP; no Persian benchmark, dialect scores, glossary evaluation or measured GPU throughput. |
| Speaker diarization | Time-window alternation | Explicit unavailable provider; unknown identity | D: Mock attribution; W: Missing real diarization. |
| Stereo/PBX channel attribution | Not implemented | Channel count stored | Partial metadata only; no trusted role mapping. |
| Speaker identity/embeddings | Agent/customer labels assumed | Per-meeting speakers and display-name correction | Prototype; speaker rename is not turn reassignment or known-agent verification. |
| Timestamps/confidence | Segment times, no useful confidence chain | Typed turn times/confidence fields | Partial; confidence not calibrated or sufficient for downstream trust. |
| Overlap/interruption/talk ratio/speed | No verified acoustic pipeline | No implemented metric engine found | Missing; any text-derived estimates must not be called acoustic measurement. |
| Silence/hold/transfer | LLM/default silence values | No PBX event model | Missing real measurement; hold and transfer need events or clearly labeled inference. |
| Sentiment/emotion | Prompt fields/random fallback | Generic insights | Prototype; no validated Persian sentiment/emotion model. |
| QA score | Fixed prompt and normalized numeric fields | No equivalent custom QA workflow | D: Prototype/Mock fallback; W: Missing commercial QA. |
| Root causes/issues/incidents | Per-call extraction and aggregate views | Generic insight model, old UI unrouted | Partial; no grounded cross-call causal analysis. |
| Churn/NPS/conversion | Prompt-generated values, defaults/fallback | Generic insights | Prototype; no trained/calibrated prediction or actual NPS measurement. |
| Summaries/actions | Prompt pipeline and coaching advice | Typed JSON summary/actions with turn references and action CRUD | Functional but immature; citation presence alone does not validate semantic support. |
| Coaching/calibration/appeals | Teacher prompt and training tips | No complete workflow | Prototype/Missing; not a coaching program. |
| Copilot | Intent classifier selects predefined Cypher; LLM verbalizes results | Old component not active | Prototype; no user-time filters/ACL-aware RAG/evidence guarantee. |
| Graph browser | Node/neighborhood browser | Optional connection flag; no verified projection pipeline | D: Prototype/unsafe exposure; W: Missing business graph. |
| Transactional database/migrations | Neo4j; no relational migration scheme | SQLAlchemy, Alembic revisions, PostgreSQL driver; SQLite default | W: Functional but immature. [Models](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/database/saas_models.py) |
| Organizations/workspaces/RBAC | Global roles only | Membership-scoped owner/admin/member/viewer permissions | W: Functional but immature; team/agent row restrictions missing. |
| Auth/session/reset/verification | Two overlapping stacks | Unified v1 hashed refresh sessions, CSRF, email verification/reset, Google/phone options | W: Functional but immature; enterprise SSO/MFA/SCIM not established. |
| LDAP/AD | Legacy synchronization/auth code | Legacy-only flag; identity enum is not OIDC/LDAP integration | D: Partial; W: no modern enterprise provisioning path. |
| Metering/quota | Missing | Atomic reservations, usage events, concurrency reservations | Functional but immature; billing periods, reconciliation and broker crash recovery remain. |
| Subscription/payment | Missing product billing | Plans/subscriptions/manual billing reads | MVP; not a payment, invoicing, tax or dunning system. |
| Storage/deletion/retention | Local disk dependence | Local/S3 adapter; soft-delete plus best-effort object delete; retention fields | Partial: no reliable retention sweeper/legal hold/purge ledger. |
| Search/export/sharing | Basic lists/PDF | SQL LIKE search; TXT/JSON/SRT/VTT; revocable expiring share links | Functional but immature; v1 PDF/CSV/Excel and semantic search absent. |
| API keys/webhooks | Legacy PBX webhook | Models/permission labels for some platform features | Partial: model existence is not an exposed, tested integration. |
| Dashboards/report UI | Calls, report, analytics, graph, incidents, users, Copilot | Overview/meeting detail/usage/admin settings | D: MVP views; W: working console skeleton with major call-center regressions. |
| i18n | Persian/English UI/prompts | Persian/English strings and RTL support | Functional but immature: hardcoded English/loading labels and locale/date consistency remain. |
| On-prem/cloud package | Neo4j Compose, local Makefile | Outer-repo Docker/Compose/nginx/runbooks | Prototype package; no proven clean offline installation or GPU profile. |
| Observability/tests/CI | Logs, small test suite | Request IDs, health/readiness, 97 passing backend tests; local CI/backup scripts | Partial: no demonstrated production SLOs, restore, multi-process isolation, or AI evals. |

The current v1 deletion route commits database tombstones before trying object deletion and only logs a storage failure. Retention zero is described in UI as “no retention period,” while upload creation sets expiry to now plus zero days. Both semantics and execution must be resolved before promising deletion. [Delete path](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/routers/v1/meetings.py:507), [expiry assignment](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/services/meeting_service.py:77), [settings UI](/Users/emperor/Documents/AI/Homayar.com/Homayar/frontend/src/components/platform/SettingsPage.tsx:117)


## Required work

All 64 P0/P1 target capabilities remain open for implementation/review and release qualification. Existing partial implementations are described above; this is not a claim that all code is absent. Use the [24-package index](../../planning/work-packages/README.md).


NOW: secure the source/history, reconcile one release, make processing and failure states truthful, prove tenancy/storage/recovery, and build the Persian baseline. NEXT: deliver real speech, configurable QA and the supervisor workflow; complete private packaging, integrations, billing and enterprise controls. LATER: add advanced VOC/graph, named connectors, calibrated sales/risk models or a meeting module only when paid demand and evidence justify them.

A planning envelope of **18–26 weeks to a qualified v1 candidate** assumes a focused team of roughly 4–6 contributors: backend/platform ownership, ML/audio ownership, frontend/product ownership, QA/operations capacity and a product/customer lead, with external security/legal expertise. Some roles can overlap. This is not a schedule commitment, and independent package estimates cannot simply be summed because of shared dependencies and review. A smaller team or delayed corpus/procurement should extend the calendar, not remove gates.

| Phase | Objective and scope | Architecture changes / dependencies | Exit criteria and commercial impact | Main risk |
|---|---|---|---|---|
| 0, weeks 1–2 | Contain history exposure, source reconciliation, claim register, corpus/data-rights setup | WP01/02/04/13/21; preserve W, establish release baseline | Reproducible build; credential-owner response; no hidden mocks/unsupported claims in proposed pilot; clear benchmark plan | Unknown credential ownership or lost local work. |
| 1, weeks 2–6 | Trustworthy platform and ingest | WP03/05/06/07/08/20; PostgreSQL authority, outbox, private objects, policy boundaries | Two-tenant/fault/deletion/restore tests; explicit stage states; supports safe internal data trials | Queue/concurrency bugs or privacy leakage. |
| 2, weeks 5–10 | Measured real conversation pipeline | WP09/10/11/12/13; warm speech/LLM providers, evidence revisions and deterministic signals | Persian/stereo/mono benchmark, real end-to-end call, reviewed QA prototype; supports scoped paid pilot | Audio quality or inference cost below target. |
| 3, weeks 9–15 | Complete QA buyer workflow | WP14/15/16/17/22/24; calibration, trends, scoped search, approved actions | Supervisor can ingest, evaluate, correct, coach and report without developer help; pilot ROI measured | Workflow usability and noisy alerts. |
| 4, weeks 13–21 | Commercial delivery and enterprise qualification | WP18/19/20/23; billing, offline bundle, SSO, support and penetration test | Cloud and private profiles independently qualified; clean install/upgrade/restore; supports annual contracts | On-prem hardware/identity/support variation. |
| 5, weeks 20–26 | Release candidate, buyer acceptance and rollout | All P0/P1, documentation, pricing and claim evidence | Section 38 gates pass; two paid pilots recommended, one including private deployment; v1 release decision | A passed demo mistaken for completed operational acceptance. |
| After v1 | Paid expansion | Selected P2 packages from F08/F11/F29/F35/F39/F48/F50–55/F62/F65–67/F71/F73/F76–77/F90–91/F93–95 | Each has buyer, benchmark, margin and support case | Uncontrolled breadth. |

Start the corpus and private installation design early; neither is a final-week checkbox. Security and evaluation continue through all phases. Forecast work using actual package throughput after two weeks; update scope and calendar with evidence. Preserve the mandatory private deployment path even if the first cloud pilot is easier to demonstrate.


## Remaining risks


Probability and impact use 1–5, with 5 highest; these are prospective planning judgments. Owners are proposed roles, not assigned personnel. Security findings already observed remain release blockers irrespective of likelihood scores.

| Risk | P | I | Mitigation / owner | Trigger or stop condition |
|---|---:|---:|---|---|
| Secret misuse from reachable history | 4 | 5 | Rotate/revoke through credential owners, access review, redacted scan; security lead | Do not expose a pilot using potentially affected credentials before containment. |
| Cross-tenant/private data exposure | 3 | 5 | RLS plus app/object/job/search controls and adversarial tests; backend/security | Any unauthorized read/write/export blocks release. |
| Persian ASR fails accents/entities | 4 | 5 | Representative corpus, stereo handling, measured models and review flags; ML lead | Critical slice below agreed gate; narrow scope or change provider. |
| Diarization assigns wrong role | 4 | 5 | Trusted channels first, qualified mono adapter, uncertainty and correction; ML lead | QA attribution not defensible; no automatic role-specific scoring. |
| LLM invents facts/scores | 4 | 5 | Evidence schema, deterministic rules, validation and abstention; ML/QA lead | Unsupported critical claim or hidden mock output. |
| Lost, duplicated or stuck jobs | 4 | 4 | Outbox, leases/fencing, recovery drill, idempotent effects; backend lead | Lost accepted job or duplicate billing in fault test. |
| GPU throughput/economics miss plan | 4 | 4 | Measure exact workload, capacity envelope and pricing floor; ML/platform/finance | Margin below floor or backlog beyond contracted latency. |
| On-prem support becomes bespoke | 4 | 4 | Supported topology matrix, preflight and partner responsibilities; platform lead | Installation/upgrade repeatedly needs core developer intervention. |
| Integration takes longer than pilot | 4 | 4 | Version discovery, sample CDR/recording contract and paid change control; integration lead | No reliable source IDs or authorized recording access. |
| Buyers resist monitoring or scores | 3 | 4 | Transparent rubric, evidence, appeals, fair cohorts and policy review; customer sponsor | Agents/reviewers cannot contest demonstrably wrong conclusions. |
| Long enterprise procurement | 5 | 4 | Mix midmarket paid pilots and selective lighthouse accounts; founder/sales | No budget owner or next procurement milestone after agreed period. |
| Privacy/regulatory/procurement limits | 4 | 5 | Country/customer legal review, local option, supplier eligibility; legal/security | Required processing or provider contract not authorized. |
| Vendor/model license or supply shock | 3 | 4 | Adapter boundaries, pinned manifests, eligible alternatives; platform/legal | Model redistribution/support or cloud access unavailable. |
| Graph adds cost without useful answers | 4 | 3 | SQL baseline, three paid use cases, projection-only design; product/architecture | No measurable answer/decision benefit over simpler analytics. |
| Feature sprawl delays core product | 5 | 4 | P0/P1 gates, one ICP, explicit P2/P3 backlog; product owner | Team starts bots/real-time/emotion before pilot QA works. |
| Website undermines buyer trust | 4 | 5 | Claim evidence register and honest demos/case studies; marketing/product | Public outcome, certification or feature lacks substantiation. |
| Test suite gives false confidence | 4 | 4 | Real PostgreSQL/queue/GPU/offline/restore gates; QA lead | Unit tests pass while a supported deployment cannot process a call. |
| Retention/backup resurrects deleted data | 3 | 5 | Durable purge, tombstones and restore replay; security/platform | Deleted recording or derived record reappears after restore. |
| Poor willingness to pay | 4 | 5 | Paid pilots, price tests and measured ROI; founder | No conversions despite technical success; revisit buyer/problem before adding features. |
| Team capacity below roadmap assumption | 4 | 4 | Limit work in progress and sequence by dependency; engineering manager | Core domain work cannot receive review/QA support. |


## Release gates


These are **proposed implementation acceptance tests**, not results of this audit. Freeze quality thresholds with the design partner after baseline and before tuning/release evaluation. A change to the frozen gate requires an explicit documented scope decision, not silent lowering after failure. Security/privacy gates cannot be bypassed by marking them statistically insignificant.

Common release fixture: a rights-cleared 30-minute two-channel Persian call plus representative mono, English/mixed, noisy and invalid inputs; two organizations, two teams per organization, all operational roles; a real PostgreSQL database, supported object store, real broker/worker and actual qualified models. Use synthetic data for adversarial fixtures and consented data for authentic acoustic evaluation. Tests must record source SHA, topology/hardware, model/prompt/data revisions and elapsed time.

| Feature / package | Measurable acceptance criterion |
|---|---|
| F01 / WP01 — Source provenance | Fresh checkout from the selected release ref builds API, console and workers with no dirty/untracked dependency; release manifest identifies every source/image/model revision. |
| F02 / WP01 — CI/migrations | Required unit, PostgreSQL integration, queue and browser flows pass; fresh database and upgrade from the previous supported schema preserve seeded tenant/usage/evidence records. |
| F03 / WP02 — Secrets | Credential-owner containment is recorded for each suspected credential family; new source/history-distribution/image/support-bundle scans have zero unreviewed credential findings; no values enter reports/logs. |
| F04 / WP03 — Sessions | Expired/revoked/disabled/reused refresh credentials and password-reset-invalidated sessions fail; legitimate rotation succeeds once; cookie CSRF and account-linking negative cases pass. |
| F05 / WP03 — Tenant isolation | Two-tenant negative suite covers all API/object/job/share/export/search/projection operations with zero unauthorized reads/writes; pooled PostgreSQL connections never retain another tenant's context. |
| F06 / WP03 — Operational roles | Supervisor sees assigned teams only, agent sees authorized self records, analyst sees approved data and executive sees allowed aggregates; all matrix-denied actions fail server-side. |
| F07 / WP23 — Federation/MFA | OIDC and the supported SAML-broker profile complete login/logout/linking; privileged MFA enforced; disabled/group-removed users lose access within the documented maximum, proposed ≤15 minutes, with immediate admin revocation available. |
| F09 / WP20 — Audit | Every tested privileged configuration, export, review override, action and deletion records actor, scope, time, reason and result; protected sink detects a deliberately altered/missing event in an integrity drill. |
| F10 / WP02 — Deployment encryption | External access uses validated TLS; DB/broker/object consoles are unreachable from public ingress; encrypted data/backup access fails without authorized keys; renewal/rotation drill succeeds. |
| F12 / WP05 — Media validation | Supported codec fixtures pass; forged, malformed, oversized and duration-expansion inputs fail within configured time/memory bounds and never reach inference; original checksums remain intact. |
| F13 / WP05 — Idempotent ingest | Repeating one source event/upload finalization 100 times creates one logical conversation and one eligible usage effect; same key with changed payload returns a conflict. |
| F14 / WP05 — Private objects | Unauthorized/expired downloads fail; multipart abort and DB/storage partial failure reconcile without accessible orphan objects; supported storage contract suite passes. |
| F15 / WP07 — Retention/deletion | Finite expiry and explicit immediate/indefinite settings behave as documented; access denied immediately on accepted deletion; active-store purge completes within 24h under normal conditions or raises retry alert; held data remains; restore reapplies tombstones. |
| F16 / WP07 — Text PII | On a separately annotated supported-class Persian/English set, proposed recall ≥95% and precision ≥90%; unsupported classes and residual risk disclosed; protected originals never enter a redacted-only provider/export path. |
| F17 / WP06 — Durable processing | Worker kill, broker outage, lost ACK, duplicate delivery and storage fault leave no lost accepted job; abandoned work resumes within two configured sweeper intervals after recovery; stale worker commits rejected; completed stages and financial/action effects are not duplicated. |
| F18 / WP18 — Metering/quotas | 100 concurrent reservation/completion attempts cannot exceed a hard configured quota or double charge; cancellation/partial/retry/rollover reconcile exactly with ledger and customer usage report. |
| F19 / WP18 — Billing | Invoice line totals match approved rate/usage/credits; replayed payment events do not duplicate activation/credit; unpaid/expired/grace transitions follow policy; startup does not overwrite negotiated entitlements. |
| F20 / WP08 — Registry | Every production run records stage model/prompt/schema/license-policy identifiers; an unknown/unqualified model fails preflight; a prior approved manifest can be restored without rewriting historical results. |
| F21 / WP08 — Privacy routing | With cloud credentials present and the local provider unavailable, a local-only tenant emits zero unauthorized outbound requests and returns a truthful failure/partial state; forbidden region/provider choices are rejected. |
| F22 / WP08 — Provider resilience | Inject timeout, 429, Retry-After, 5xx, invalid schema and permanent auth errors; transient retries stay within budget, permanent errors stop, circuits open/recover, and no fallback violates tenant policy. |
| F23 / WP04 — Honest states | Each forced missing/failing stage is represented in API/UI/export; unavailable diarization or failed QA cannot appear fully successful; production mock configuration is rejected and no random/default analytics are substituted. |
| F24 / WP04 — Evidence | 100% of returned citations resolve to an authorized immutable transcript revision and valid original audio range; correction/reanalysis preserves old provenance and explicitly supersedes dependent results. |
| F25 / WP09 — Channels/roles | Verified separated-channel role accuracy ≥98% on the agreed set; swapped, silent, duplicated and transfer-affected channels are detected or marked unknown; stereo alone never establishes identity. |
| F26 / WP09 — Normalize/VAD | Original timeline mapping remains correct through resampling/chunking; no speech loss >250ms at boundaries on annotated regression fixtures; empty/music/noise cases do not fabricate transcript; quality flags exposed. |
| F27 / WP09 — STT | Persian held-out WER ≤20%, critical supported slice ≤30%; CER and entity accuracy reported; critical entities ≥95% exact match or routed to review; English/mixed slices measured separately and supported scope stated. |
| F28 / WP09 — GPU workers | Ten sequential jobs reuse the warmed model per configured worker process; configured concurrent load stays within memory budget without OOM; queue fairness and latency measured on the exact qualified hardware. |
| F30 / WP10 — Diarization | Mono DER ≤15% on agreed held-out contact-center set, with overlap/collar policy and JER/count errors published; ambiguous clusters remain anonymous/unknown rather than fabricated agent identities. |
| F31 / WP10 — Overlap | Overlap intervals survive storage/UI/export; proposed overlap detection F1 ≥0.80 on an adequately annotated supported set; display-exclusive turns do not erase measured overlapping speech. |
| F33 / WP10 — Alignment/confidence | ≥95% audited key-moment starts within ±1 second and all spans in recording bounds; confidence origin and calibrated/uncalibrated status visible; ambiguous alignment routes to review. |
| F34 / WP11 — Acoustic metrics | Known synthetic interval fixtures reconcile duration/silence/talk/overlap exactly to defined rounding; on annotated real clips, talk/silence ratio MAE ≤5 percentage points; no LLM-generated acoustic measurements. |
| F36 / WP11 — Structured extraction | Supported intent taxonomy macro-F1 ≥0.85; critical high-cost findings precision ≥0.95 or human confirmation; every field has evidence/unknown semantics and contradictory fixtures abstain correctly. |
| F37 / WP12 — Summary/key moments | ≥95% audited atomic claims supported by cited evidence, 100% structural citation validity and no invented severe allegation in release set; summaries distinguish explicit outcome from inference. |
| F38 / WP24 — Action items | Explicit owner/date extraction precision ≥95% on held-out examples; unspecified values remain unset; every item links evidence and revision; proposed next steps are labeled separately from commitments. |
| F41 / WP11 — Scorecards | Weights/applicability/NA/insufficient evidence calculations pass fixed examples; adjudicated QA kappa ≥0.75 and score MAE ≤8/100; scorecard versions remain reproducible and cannot silently change past scores. |
| F42 / WP11 — Rules/compliance | Exact phrase rules are deterministic; contextual critical findings precision ≥0.95 or require review; negation, quoted speech, wrong speaker and poor ASR regression cases do not create automatic confirmed violations. |
| F43 / WP14 — Coverage/sampling | Ingested/valid/transcribed/fully-evaluated/reviewed counts reconcile independently; random/stratified samples respect configured sizes and scopes; failed calls cannot inflate evaluation coverage. |
| F44 / WP14 — Review/override | Authorized reviewer can accept/override criterion with required reason; machine score preserved; unauthorized or stale-revision write rejected; audit and recalculated aggregate match approved result. |
| F45 / WP12 — Corrections | Text and speaker-turn corrections create a new revision; playback/evidence remain navigable; dependent outputs become stale then recompute; old export and decision retain original provenance. |
| F46 / WP14 — Calibration/appeals | Blind calibration computes agreement against adjudicated set; agent appeal reaches authorized reviewer and records disposition; agent cannot edit machine/human decision directly. |
| F47 / WP14 — Coaching | Accepted finding creates assigned task with due date; agent acknowledgement and follow-up status visible to allowed roles; overdue/reminder deduplication and closure audit pass. |
| F49 / WP15 — Trends | Counts/rates and drill-down match reference dataset exactly across time/team/product filters; insufficient samples and processing gaps are visible; correlation is not labeled confirmed cause. |
| F57 / WP17 — Search | Approved relevant-evidence benchmark meets proposed recall@10 ≥0.90; all returned evidence is authorized/current or explicitly historical; deletion and membership changes remove access immediately even before reindex. |
| F58 / WP17 — Copilot | At least 95 of 100 frozen approved business questions answered correctly; deterministic counts 100% exact; unsupported questions abstain; zero cross-tenant/team leaks under prompt-injection and guessed-ID tests. |
| F59 / WP24 — Alerts | Repeated same-condition events create one alert per configured deduplication window; only authorized recipients notified; suppressed/acknowledged/escalated states and failed delivery visible. |
| F60 / WP24 — Approved actions | Rejected/unapproved/revoked/stale proposals cause zero external writes; repeated approved delivery causes one logical CRM/ticket effect; connector failures retry with complete audit. |
| F61 / WP15 — Dashboards | Every role's dashboard respects scope and source denominators; proposed p95 normal API response <1 second on 100k-conversation reference dataset at documented load; cohort/rubric differences are shown. |
| F63 / WP16 — PBX | One named supported PBX/version imports 100 representative recordings/CDRs including transfers/duplicates/missing files; IDs and channel roles reconcile; unsupported versions are documented. |
| F64 / WP16 — CRM/outcomes | Generic mapping links known call/customer/ticket/agent/outcome fixtures correctly; missing/changed/late IDs enter reconciliation rather than false joins; no phone-number-only global identity merge. |
| F68 / WP16 — API/keys | Published OpenAPI/client example completes upload→202 job→results; expired/revoked/out-of-scope key fails; rate limits and idempotency behavior verified through contract tests. |
| F69 / WP16 — Webhooks | Raw-body HMAC/timestamp verification examples pass; altered/stale/replayed messages rejected or deduplicated; retry/dead-letter records allow safe redelivery without duplicate recipient effects. |
| F70 / WP24 — Exports | PDF/CSV/JSON and existing subtitle/text exports match authorized revision; Persian font/shaping and RTL manually checked; CSV formula injection neutralized; revoked access and retention apply to exported objects. |
| F72 / WP22 — i18n/accessibility | All critical auth/upload/report/review/settings/error flows complete in Persian RTL and English LTR; keyboard-only flow succeeds, focus/labels are usable and 200% zoom does not hide required controls. |
| F74 / WP19 — Offline processing | Fresh qualified installation with outbound network denied performs login, 30-minute Persian processing, local summary/QA/search, export and deletion; no hidden downloads/cloud calls; actual hardware/profile recorded. |
| F75 / WP19 — License/upgrade/support | Valid/expired/tampered offline license tests follow documented grace/export behavior; prior-version upgrade and failed-upgrade recovery preserve data; default support bundle contains no canary secret, transcript or identity. |
| F78 / WP20 — Observability/SLO | A failed/stuck job is discoverable by correlation ID; alert fires within 5 minutes for configured worker/purge outage; 24-hour mixed-load run achieves ≥99% valid supported processing and proposed p95 ≤15 minutes for 30-minute calls on reference GPU at admitted load. |
| F79 / WP20 — Backup/restore | Restore rehearsal on isolated supported topology meets proposed RPO ≤24h/RTO ≤8h; DB/object manifests reconcile; authorization works and deletion tombstones are reapplied before access. |
| F80 / WP13 — Gold corpus | Rights/access/split manifests exist; ≥20 Persian hours with frozen ≥5-hour held-out subset and separate English/mixed coverage; ≥20% double annotation/adjudication; no split leakage detected by identity/source checks. |
| F81 / WP13 — AI promotion | Evaluation command reproduces metrics within documented stochastic tolerance; model/prompt change cannot promote without held-out quality/slice/cost report and approved rollback manifest. |
| F82 / WP23 — Security test | Independent scoped test completed on release candidate; zero unresolved critical/high findings, retest evidence for fixes, and no assurance statement exceeds actual test scope. |
| F83 / WP21 — Claim register | Every public feature, accuracy, integration, customer result, deployment and assurance claim has an evidence owner and status; unreleased/unverified claims cannot be published as available/proven. |
| F84 / WP21 — Demo/funnel | Simulation clearly labeled; real demo identifies pipeline version and allowed sample; test lead reaches designated owner and receives acknowledgement; failures surface without collecting unnecessary sensitive data. |
| F85 / WP21 — ROI | Calculator matches independently computed fixtures, including zero/negative ROI; costs and assumptions visible; no double-counting labor/AHT/churn benefits; pilot report distinguishes observed and projected outcomes. |
| F86 / WP22 — Documentation | New operator installs/administers/restores supported profile using released docs, and new supervisor completes review/correction/coaching with no undocumented developer step; broken command/API references corrected. |
| F92 / WP22 — Tenant administration | Authorized admins can preview/apply/version/rollback supported provider/scorecard/retention settings; invalid or privacy-violating configuration rejected; secrets masked and changes audited; other tenants unaffected. |

Quality gates such as overlap F1, PII recall, retrieval recall and acoustic metric error are initial engineering targets added to make implementation reviewable. Baseline them on representative data and record any unsupported slice. They must not be pasted into marketing as current accuracy. The 24-hour processing/load run is a release test, not evidence of a long-term 99.9% availability SLA.

Cross-feature commercial release scenarios:

1. **Cloud path:** authorized tenant ingests a 30-minute stereo Persian call; real worker processes it; supervisor inspects transcript/roles/QA, corrects a turn, approves coaching, exports and sees accurate usage. A second tenant cannot discover any associated resource.
2. **Private path:** customer operator installs the signed bundle with egress denied, runs the same workflow using local models/identity/storage, upgrades and restores it without cloud dependencies.
3. **Failure path:** interrupt processing and storage; accepted job recovers without duplicate charges, partial output is clearly labeled, and no mock values appear.
4. **Privacy path:** request deletion, inject a purge failure, observe retry/alert, complete deletion, restore a backup and verify tombstones prevent resurrection.
5. **Buyer path:** a supervisor performs representative reviews faster under the agreed pilot method while human QA agreement stays within gate; the sponsor validates outcomes and full price/support scope.

The audit's 97 passing existing backend tests are useful baseline evidence only. None of the future acceptance results above should be marked passed until executed against the selected release and recorded. Do not broaden routine testing endlessly once gates pass; expand tests when a new defect, change or untested release risk warrants it.


## Explicit external dependencies

- Credential owners must revoke/rotate historical provider credentials and review usage; writing code cannot do that on their behalf.
- The customer must supply authorized representative recordings, annotation rights, QA rubric and a pilot sponsor.
- Actual PostgreSQL/broker/GPU/local-model/private-deployment test resources are needed for the corresponding gates.
- Provider/model redistribution and customer procurement eligibility need qualification.
- Named PBX/CRM versions, identity provider, support hours and pricing need agreed scope.
- An independent penetration test and customer pilot outcomes have not occurred.

## Historical validation versus new validation

Previous audit: 97 existing backend tests passed, 142 warnings, in 82.25 seconds against an isolated copy. This is historical evidence, not a new run in this preparation phase, and does not validate GPU/real AI/real broker/PostgreSQL/offline deployment. Re-run relevant checks after implementation.
