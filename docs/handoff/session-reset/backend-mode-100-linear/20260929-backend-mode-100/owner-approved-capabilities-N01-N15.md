accepted work and continue eligible source work on the verified integration
branch. Keep issues requiring CI/merge open.

If CI passes, merge only under section 17, verify resulting content on main and
check main CI. Account for squash/rebase merge semantics; the original source
SHA may not be an ancestor after those merge methods.

Never merge red, missing or unverified required checks. Do not change billing,
branch protection or required checks to bypass the blocker.

---

# 10. APPROVED MISSING BACKEND CAPABILITIES

Add all capabilities below to Homayar/AppVision.md.

Do not delete, replace, simplify or renumber existing Atlas content.

Reuse the stable ID of any capability already present in the current Atlas.
Assign a new stable ID only for a genuinely missing capability.

For each capability add:

- purpose;
- backend contract;
- deployment modes;
- dependencies;
- security and privacy rules;
- tenant boundary;
- audit requirements;
- failure behavior;
- acceptance evidence;
- status;
- external qualification requirements.

## 10.1 Customer onboarding backend

Implement:

- tenant provisioning;
- workspace creation;
- initial owner/admin;
- invitation lifecycle;
- invitation expiry/revocation;
- provider configuration;
- deployment-profile selection;
- initial policy selection;
- first-upload readiness;
- resumable onboarding state;
- idempotent provisioning;
- audit history;
- rollback/cleanup for failed provisioning;
- no cross-tenant access.

## 10.2 Data import and migration

Implement:

- import source definitions;
- upload/stream ingestion;
- schema mapping;
- preview;
- dry run;
- validation;
- rejection report;
- resumability;
- checkpoints;
- idempotency;
- provenance;
- tenant isolation;
- conflict policy;
- rollback;
- usage accounting;
- audit events;
- legacy source mapping.

Candidate repositories:

- dlt
- Meltano
- Airbyte

Prefer native Homayar implementation or `dlt` unless connector scale justifies
a larger external platform.

## 10.3 Recording consent policy

Implement:

- jurisdiction-aware policies;
- policy versions;
- lawful/approved basis;
- announcement requirements;
- consent receipt;
- recording permission;
- processing permission;
- analysis permission;
- export permission;
- withdrawal;
- opt-out;
- expiry;
- retention relationship;
- channel relationship;
- audit;
- enforcement before recording, processing or outbound action.

Candidate reference:

- OpenFGC

Homayar remains the enforcement authority.

## 10.4 Data-quality backend

Implement:

- media validation metrics;
- codec/container state;
- audio quality;
- clipping;
- silence;
- duration;
- channel information;
- transcript confidence;
- diarization availability;
- partial/missing stage detection;
- provider failures;
- invalid evidence;
- quality thresholds;
- per-tenant aggregation;
- quality-event history;
- explicit unknown state;
- no fabricated scores.

Candidate repositories:

- Evidently
- Great Expectations
- Soda Core

Audio-specific quality logic remains native to Homayar.

## 10.5 Reprocessing workflow

Implement:

- selecting prior result;
- selecting new model/prompt/provider version;
- immutable prior revisions;
- new attempt identity;
- lineage;
- cost estimate;
- authorization;
- approval where required;
- idempotent submission;
- bounded retry;
- cancellation;
- stale-worker fencing;
- checkpoint recovery;
- comparison;
- acceptance/rejection;
- rollback to previous accepted revision;
- usage settlement.

Preserve the existing Celery/outbox/lease/checkpoint architecture unless it
cannot satisfy the contract.

Temporal, Prefect and Dagster are candidates, not mandatory replacements.

## 10.6 Feedback-to-evaluation loop

Implement:

- supervisor correction events;
- transcript correction;
- speaker correction;
- QA override;
- reason codes;
- annotation revision history;
- consent/data-right checks;
- eligibility for evaluation datasets;
- export contract;
- dataset version;
- train/evaluation separation;
- lineage;
- deletion propagation;
- no silent production training;
- no use of unauthorized customer data.

Candidate repositories:

- Label Studio
- Argilla
- Langfuse

## 10.7 Notification preferences

Implement:

- user preferences;
- tenant policy;
- channel preferences;
- event preferences;
- quiet hours;
- timezone;
- escalation;
- suppression;
- acknowledgement;
- delivery attempts;
- idempotency;
- fallback policy;
- offline behavior;
- audit;
- unsubscribe/stop handling.

Candidate repositories:

- Apprise
- Novu

## 10.8 Provider budget kill-switch

Implement:

- tenant budget;
- workspace budget;
- user budget where allowed;
- provider budget;
- workflow budget;
- model budget;
- time-window limits;
- reservations before provider calls;
- atomic ledger transitions;
- warning thresholds;
- hard stop;
- cancellation;
- refund/release;
- retry protection;
- fallback protection;
- operator override;
- approval;
- audit;
- unknown provider usage handling.

Preserve the Homayar usage ledger as authority.

Candidate repositories:

- LiteLLM
- Helicone
- Portkey Gateway

## 10.9 Support entitlement

Implement:

- plan entitlement;
- contract entitlement;
- support tier;
- SLA metadata;
- support-bundle eligibility;
- diagnostic scope;
- feature access;
- deployment entitlement;
- expiry;
- grace period;
- suspension;
- renewal;
- audit history;
- backend enforcement;
- no secret/raw-content export by default.

Candidate repositories:

- Lago
- Kill Bill
- flagd

Billing systems must not become authorization authorities.

## 10.10 Privacy-preserving product telemetry

Implement:

- feature-usage events;
- backend operation events;
- no raw audio by default;
- no transcript content by default;
- no provider secret collection;
- tenant opt-out;
- user policy;
- deployment-mode policy;
- offline-safe behavior;
- aggregation;
- retention;
- deletion;
- pseudonymous identifiers;
- export;
- audit;
- sampling;
- schema versioning.

Candidate repositories:

- OpenTelemetry Collector
- PostHog
- Plausible

## 10.11 Migration and legacy retirement

Implement:

- legacy inventory;
- replacement mapping;
- dependency mapping;
- legacy data migration;
- compatibility boundary;
- dual-read/write only when explicitly necessary;
- checkpoints;
- rollback;
- retirement gates;
- forbidden legacy-path checks;
- dead-route removal after proof;
- no activation of unsafe legacy auth or graph exposure;
- status and evidence for every retired path.

Candidate tools:

- Semgrep
- jscodeshift
- ast-grep

## 10.12 Disaster recovery objectives

Implement:

- versioned RPO/RTO per deployment profile;
- PostgreSQL backup contract;
- storage backup contract;
- model/artifact/config backup scope;
- encrypted backup metadata;
- restore workflow;
- restore verification;
- tenant isolation after restore;
- deletion-journal reapplication;
- legal-hold preservation;
- credential exclusion;
- drill evidence;
- partial failure states.

Candidate repositories:

- pgBackRest
- Restic

## 10.13 Accessibility backend support

Implement only backend responsibilities:

- stable localized error codes;
- localized error metadata;
- field identifiers;
- semantic validation responses;
- RTL/LTR-safe exported-content metadata;
- language and locale metadata;
- accessible export structure metadata;
- accessibility evidence records.

Do not modify frontend code.

Candidate tools may be recorded for future frontend verification:

- axe-core
- Pa11y

Do not perform frontend implementation in this run.

## 10.14 Browser/device support backend contract

Implement only backend responsibilities:

- versioned support policy;
- supported recording/upload capabilities;
- codec/container constraints;
- maximum file and stream characteristics;
- compatibility API/config;
- capability negotiation;
- rejection reasons;
- audit/version history.

Do not modify frontend code.

Reference candidates:

- Playwright
- Browserslist
- MDN browser compatibility data

## 10.15 Model and prompt rollback

Implement:

- immutable model versions;
- immutable prompt versions;
- schema versions;
- model artifacts;
- checksum;
- license metadata;
- qualification state;
- deployment compatibility;
- provider compatibility;
- tenant compatibility;
- active version;
- staged promotion;
- rollback;
- previous accepted version;
- affected-result lineage;
- reprocessing linkage;
- audit;
- failure recovery;
- no mutable “latest” production identity.

Candidate repositories:

- MLflow
- BentoML
- KServe

Prefer the existing Homayar model/provider registry if it can satisfy the
contract.

---

# 18. APPVISION UPDATE

Update Homayar/AppVision.md during this run.

Required changes:

1. Add all 15 approved missing capabilities.
2. Assign stable IDs.
3. Add them to relevant capability matrices.
4. Add deployment-mode applicability.
5. Add dependencies.
6. Add completion evidence.
7. Add acceptance criteria.
8. Add security/privacy implications.
9. Add offline implications.
10. Add commercial/usage implications.
11. Update coverage counts.
12. Update cross-references.
13. Preserve all original features.
14. Preserve the technology catalog.
15. Add repository candidates where relevant.
16. Mark documentation-only additions as PLANNED unless source evidence supports
    another status.
17. Reuse stable IDs if an approved missing capability already exists.
18. Link capabilities to actual Linear issue IDs/URLs and acceptance evidence.
19. Preserve Online SaaS, Connected On-Premise, Hybrid and Fully Offline/Air-Gapped
    profiles. On-premise routing supports local AI, customer BYOK, approved online
    providers and hybrid stage routing where policy permits. BYOK describes
    credential ownership, not a fifth deployment mode. Fully offline processing
    denies public egress, including fallback, retries and telemetry.

Update Homayar/vision/AppVision-Status-Notes.md only from current evidence.

Update Homayar/ReportAppvisionProgress.md with:

- actual implementation paths;
- Linear issue IDs/URLs and confirmed workflow states;
- status;
- test evidence;
- commits;
- external blockers;
- exact continuation point.

---

