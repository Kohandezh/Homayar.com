# HOMAYAR — Complete Application Vision and Capability Contract

Version: 1.0 · Recorded: 2026-09-27 · Language: English

Update: 2026-09-30 · Owner-approved backend capabilities N01–N15 added (section 6.7; Linear [HOM-141](https://linear.app/homayar/issue/HOM-141)). Original content is preserved; existing lines changed only for counts, cross-references, the contents list and the section 6 status qualifier for N-rows.

**Document status: comprehensive planning baseline, not a product certification.**

This document records the owner's product vision, selected capabilities, deployment targets, technology alternatives, governance requirements, and completion gates. It covers the existing 95-feature backlog plus the expanded voice, action, communications, commercial packaging, and configuration experience discussed with the owner. No capability becomes implemented, enabled, licensed, or production-ready merely because it appears here.

The owner requested this document inside the existing `Homayar/` application folder. This local checkout contains historical planning and user changes; this document does not reconcile branches, migrate repositories, modify runtime policies, or authorize publication. The spelling `Homayad` in the request is interpreted as the existing `Homayar` folder.

## Contents

1. [Product purpose and confirmed decisions](#1-product-purpose-and-confirmed-decisions)
2. [Architecture and ownership](#2-architecture-and-ownership)
3. [Deployment modes and truthful availability](#3-deployment-modes-and-truthful-availability)
4. [Status and evidence rules](#4-status-and-evidence-rules)
5. [Mandatory governance and engineering guardrails](#5-mandatory-governance-and-engineering-guardrails)
6. [Expanded capability matrices](#6-expanded-capability-matrices)
   - [6.7 Approved backend capabilities N01–N15](#67-approved-backend-capabilities-n01n15)
7. [User journeys and operational contracts](#7-user-journeys-and-operational-contracts)
8. [Definition of Done and release gates](#8-definition-of-done-and-release-gates)
9. [Decisions, delivery sequencing, and handoff](#9-decisions-delivery-sequencing-and-handoff)
10. [Technology and integration catalog](#10-technology-and-integration-catalog)
11. [Original 95-feature coverage and acceptance register](#11-original-95-feature-coverage-and-acceptance-register)
12. [Coverage verification and maintenance](#12-coverage-verification-and-maintenance)

## 1. Product purpose and confirmed decisions

Homayar is a modular voice intelligence and governed action platform. It turns recordings and live conversations into trustworthy transcripts, evidence-backed analysis, approved actions, and measurable outcomes. It can serve an individual operator, SME, medium business, enterprise, or public-sector organization through an appropriate deployment and policy profile.

The original commercial wedge remains private Persian/English call analysis: ingest a call, establish who said what using defensible evidence, explain findings, let a supervisor correct them, and measure whether review and follow-up improve. The expanded vision adds live agents, communications, workflow configuration, and commercial packaging without discarding this foundation.

### 1.1 Decisions already supplied by the owner

| Decision | Recorded direction | Boundary |
|---|---|---|
| Deployment | SaaS, connected on-premises, hybrid, and fully offline profiles | A mode is offered only after its own tests pass |
| Customer sizes | SME, medium, enterprise, government/public-sector, custom policy | Profiles are starting configurations, not certifications |
| Modules | Admin can enable/disable licensed capabilities | Backend enforcement is mandatory; disabling is not deletion |
| Technology choices | Keep multiple relevant solutions and alternatives in the catalog | Catalog inclusion does not require installing every alternative |
| CRM | Odoo Community by default or integrate the customer's Odoo | Other CRM products are outside the current selected CRM scope |
| AI access | Existing **Homayar AI API Gateway**, BYOK, local inference, and mixed pipelines | Its actual contract must be inspected before integration; do not invent an API |
| Agent default | Propose and request approval | Scoped autonomous execution is an explicit, revocable configuration |
| Outbound calls | Reminders, existing-customer follow-up, and prospect sales | Contact permissions, suppression, timing, and jurisdiction rules remain enforced |
| Configuration | Manual UI, text chatbot, and voice chatbot | All channels use the same policy and authorization checks |
| Workflow | AI drafts a workflow and performs a test run before activation | No irreversible external actions in a dry run |
| Commercial model | Configurable combinations of plans, usage, deployment, and BYOK | Exact prices and charge rules are versioned owner decisions |
| Retention | Admin-configurable finite, indefinite, keep-for-now, and retired states | Legal hold and backup retention are separate concepts |
| Failed/empty processing | Configurable according to sales policy | No fabricated usage and no duplicate settlement |
| Sales funnel | Questionnaire and registration produce a demo request and RFP | Customer-visible claims must match release evidence |
| Customer delivery | Export a selected-capability build/profile from one product line | Avoid a source fork and redesign for every customer |
| Support | Complete operational diagnosis and user-approved support bundles | No default export of secrets or unrestricted raw customer content |
| Development operation | A coding session uses one agent unless the owner explicitly authorizes delegation | Product runtime concurrency is a separate capacity decision |

### 1.2 What is not promised

No automatic ISO/SOC certification, universal PBX compatibility, guaranteed natural Persian speech, inferred actual NPS, unlimited concurrency, universal offline support for public networks, or production readiness follows from this vision. A repository README, a mocked test, or a successful demo is not sufficient evidence for those claims.

## 2. Architecture and ownership

```text
Public website: advertising / SEO / GEO / product truth / demo questionnaire
                              |
                   RFP + account + selected profile
                              |
Homayar console + API -----------------------------------------------+
  identity / tenant scope / capabilities / licensing / policy        |
  approval center / workflow configuration / audit / usage ledger    |
       |                    |                    |                  |
Recorded-media pipeline   Live Voice Agent     Action runtime       |
  validate + ingest       SIP / WebRTC         calendar / messages  |
  STT + diarization       STT -> LLM -> TTS     Odoo / email / tasks |
  evidence + QA           optional native      durable follow-up    |
  review + correction     audio models         + compensation      |
       |                    |                    |                  |
       +---------- Governed provider/connector contracts -----------+
                              |
              local models / Homayar AI API Gateway / BYOK
                              |
PostgreSQL + scoped storage + queue/outbox + search + observability
                              |
Odoo CRM: contacts / leads / activities / permitted business outcomes
```

### 2.1 Product boundaries

- **Public website:** marketing, multilingual discovery, accurate pricing and capability claims, lead capture, RFP, demo booking, links into the application. SEO/GEO work must not invent customer results or supported features.
- **Homayar backend:** authoritative tenant scope, conversation evidence, jobs, provider policy, approvals, licensing checks, usage events, and action execution.
- **Homayar console:** user journeys, administration, review, workflows, provider settings, cost reports, and support export.
- **Odoo:** CRM/business records and activities. It must not replace Homayar's processing, isolation, evidence, and authorization systems.
- **Customer Odoo and Homayar's own sales Odoo:** distinct connections, credentials, data ownership, and permission domains.
- **Homayar AI API Gateway:** the owner's existing gateway. Historical Padyar references require explicit reconciliation; do not silently assume these names identify the same deployment or contract.
- **Third-party projects:** normally dependencies, model artifacts, or separate services connected through adapters. Copying whole repositories into the source tree is not the default integration method.

### 2.2 Repository direction and preservation

The reported integrated direction is one versioned product repository containing the public site, application, infrastructure, and planning records, with the application in `Homayar/`. Historical reports describe ADR 0033 replacing a nested product checkout with ordinary tracked files. This authoring session did not verify that ADR or the current remote tree; the working folder inspected here still includes historical checkout material.

Use a monorepo as the preferred coordinated release layout, with independent build/deploy boundaries. A split repository is possible only with a documented need, pinned compatible releases, integration CI, and an ADR. Never change topology merely to match this document. Preserve original dirty checkouts, backups, and unpublished work until separately reconciled.

### 2.3 Preserve established foundations

Reuse the reported auth/session, tenant/RLS, truthful-stage, ingestion/storage, outbox, lease/fencing, checkpoint, usage-ledger, privacy/retention, and provider-policy foundations. Replacing them with Odoo, a generic workflow platform, or a new database requires demonstrated benefit, migration and rollback plans, and explicit architecture review.

## 3. Deployment modes and truthful availability

| Code | Profile | Network and processing contract |
|---|---|---|
| S | Hosted SaaS | Homayar-operated environment; region, tenancy and provider policies apply |
| P | Connected on-premises | Customer-operated installation; only approved external destinations permitted |
| H | Hybrid | Local and remote stages intentionally combined; per-stage data and region policy |
| O | Fully offline / air-gapped | Runtime works without public internet; all mandatory artifacts and services local |

**On-premises is an ownership/location property; offline is a network property.** A local installation calling an external API is P or H, not O. Offline can include an internal LAN and internal PBX. PSTN or internet connections cross the strict air-gap boundary even if inference remains local.

The owner's profile names map to these codes: Online SaaS is S, Connected On-Premise is P, Hybrid is H and Fully Offline/Air-Gapped is O. On-premises routing can use local AI, customer BYOK, approved online providers and hybrid stage routing where policy permits. **BYOK describes credential ownership, not a fifth deployment mode.** Fully offline processing denies public egress, including provider fallback, retries and telemetry.

Matrix cells below describe **design targets**, never current release support:

- **Y:** intended in this mode, subject to acceptance gates.
- **L:** local/internal equivalent only; external service semantics unavailable.
- **C:** conditional on provider, contract, permission, license, or supported hardware.
- **—:** external operation unavailable in strict offline mode.

| Capability family | S | P | H | O | Required distinction |
|---|---|---|---|---|---|
| Recording upload, analysis, review and export | Y | Y | Y | Y | Qualified local models required for O |
| Live AI conversation in an internal browser/LAN | Y | Y | Y | Y | Local media, inference, certificates and assets |
| Public website voice call | Y | Y | Y | L | Offline supports an internal web app only |
| Internal PBX calls | C | Y | Y | Y | Internal routing and media stay local |
| Public phone calls / SIP carrier | C | C | C | — | A carrier connection is external connectivity |
| Local model inference | C | Y | Y | Y | Hardware capacity and model rights verified |
| Cloud provider / external Gateway | Y | Y | Y | — | Local Gateway with only local backends can support O |
| Odoo integration | Y | Y | Y | L | Odoo and required services must be internal for O |
| Calendar and email | Y | Y | Y | L | Internal servers only in O |
| Telegram / WhatsApp / Bale / Eita / public social | C | C | C | — | Queue drafts locally; delivery requires connectivity |
| Policy, approvals, licensing checks and audit | Y | Y | Y | Y | Offline keys, clocks and revocation limits documented |
| Billing calculation and usage reports | Y | Y | Y | Y | Online payment collection is separate |
| Support diagnosis | Y | Y | Y | Y | Offline produces a bundle; it cannot send remotely |
| Updates / vulnerability intelligence | Y | Y | Y | L | Signed transfer packages and dated offline databases |

## 4. Status and evidence rules

| Status | Meaning |
|---|---|
| PLANNED | Recorded scope; implementation not asserted |
| IN_PROGRESS | Changes underway with a named owner and branch |
| PARTIAL | Some specified acceptance criteria met; exact gaps recorded |
| VALIDATED | Acceptance evidence passes for a specific revision, mode and environment |
| RELEASED | Validated artifact delivered through its release gates with documentation |
| BLOCKED_EXTERNAL | Named external prerequisite prevents a specific acceptance item |
| NOT_RUN | A check has not run; never equivalent to passed |
| NEEDS_RECONCILIATION | Historical reports exist but current repository/release evidence was not reconciled |
| DEFERRED / SUPERSEDED / EXCLUDED | Explicit scope decision; retain the reason and history |

One feature may be VALIDATED in P with one provider and PLANNED in O or with another provider. Store status per feature × mode × adapter/model × version. Repository availability is separate from adapter implementation, qualification and release eligibility.

### 4.1 Historical implementation context, not a new audit

The owner-provided session reports describe substantial implementation in WP03/WP04/WP06, partial WP05/WP07/WP08, console customer-journey improvements, and later CI/media validation fixes. The local planning records inspected for this document include an older September 7 baseline and cannot establish the current integrated status. Accordingly:

- Existing F01–F95 rows below are marked NEEDS_RECONCILIATION for current delivery evidence.
- Expanded requirements are PLANNED unless they overlap a reported foundation; overlap still needs verification.
- Approved backend capabilities N01–N15 (section 6.7) are PLANNED except N11, which is PARTIAL on recorded delivery evidence; each record states its status and the reason.
- No green Done mark, current CI claim, merge assertion, or completion percentage is manufactured here.
- A completed documentation task is not completion of the product.

## 5. Mandatory governance and engineering guardrails

These are normative product requirements. Their runtime implementation and CI enforcement remain separate deliverables.

### 5.1 Authorization, identity and actions

| Rule | Required behavior | Required evidence |
|---|---|---|
| GR01 | Effective capability = licensed AND deployment-compatible AND installed/healthy AND admin-enabled AND policy-allowed AND actor-authorized AND required approval valid | Negative tests for each denied term; no UI-only checks |
| GR02 | Tenant scope applies to DB, objects, jobs, retrieval, exports, shares, caches, graphs and diagnostics | Cross-tenant adversarial tests under the application DB role |
| GR03 | Voice/text Agent authority never exceeds the requesting user or delegated scope | Same denial through UI, API, chat and voice |
| GR04 | Delegations specify actions, resources, recipients, budget, expiry and revocation | Expiry/revocation race tests and current-policy checks |
| GR05 | Approval binds to exact action, recipient, payload/revision and material cost parameters | Changing approved content invalidates approval |
| GR06 | Recheck authorization immediately before external side effects | Membership/policy revoked between draft and send blocks send |
| GR07 | Multi-person approval requires genuinely distinct authorized approvers | Self-approval and duplicate-identity tests |
| GR08 | Phone number, display name and channel account alone do not prove corporate identity | Verified identity binding, unlink and account-recovery tests |
| GR09 | Tool output, documents and incoming messages are untrusted data | Prompt-injection tests cannot grant permission or reveal secrets |
| GR10 | Default interaction is suggestion plus approval; scoped autonomy is explicit | No default live send/call/config apply from a chat suggestion |

### 5.2 AI, data and operational safety

| Rule | Required behavior | Required evidence |
|---|---|---|
| GR11 | Manifest records exact model, prompt, schema, license, capabilities and qualification | Reject mutable/unknown production model identities |
| GR12 | Policy snapshot is retained for each attempt; stricter current restrictions still apply | Mid-run policy tightening blocks subsequent disallowed calls |
| GR13 | Fallback stays inside approved provider, region, data-class and budget boundaries | Local-only tenant with cloud credentials makes zero cloud calls |
| GR14 | Permissions for text, audio, embeddings and metadata export are distinct | Audio cannot leave under a text-only permit |
| GR15 | Application permits and network egress controls are both required for restricted profiles | DNS/redirect/metadata/private-endpoint policy tests and network denial drill |
| GR16 | Offline operation includes UI assets, model files, tokenizers, auth, licensing and telemetry behavior | Runtime under denied public network access; no hidden download |
| GR17 | Unknown/unavailable/mock/partial results remain explicit | No fabricated QA, diarization, confidence, prices, progress or usage |
| GR18 | Diarization labels are not verified identities; stereo alone is not a business role | Attribution provenance and unknown state visible |
| GR19 | Analysis and retrieval provide authorized evidence and revision lineage | Citations resolve to permitted source versions |
| GR20 | PII redaction is measured and fallible; authorized reveal is audited | Representative false-positive/negative evaluation and reveal tests |
| GR21 | Original text/audio and encrypted mappings have explicit access/retention policy | Purge covers originals, derivatives, indices and mapping records |
| GR22 | Delete blocks access before eventual byte removal; restore reapplies deletion journal | Race, retry, backup/restore and reapply tests |
| GR23 | Legal hold, retired archive and retention duration are separate states/policies | Holds prevent unauthorized purge; review date does not silently release hold |
| GR24 | Logs and support bundles exclude secrets and raw sensitive content by default | Redaction scans and tenant-limited export tests |
| GR25 | Contact campaigns respect suppression, consent/legal basis, timing and stop requests | Suppressed contact cannot be reached by retry or a second channel |
| GR26 | Agent claims reflect actual completion, not dispatch alone | Accepted, delivered, acknowledged, failed and completed remain distinct |

### 5.3 Reliability, commercial integrity and release discipline

| Rule | Required behavior | Required evidence |
|---|---|---|
| GR27 | External actions and usage transitions have stable idempotency keys | Duplicate deliveries do not duplicate tasks, calls, messages or settlement |
| GR28 | Recovery is bounded by retries, deadlines, leases and fencing | Worker death, lost ack, broker restart and stale-worker tests |
| GR29 | An ambiguous external outcome is reconciled before retry | Timeout after provider acceptance cannot trigger blind duplicate action |
| GR30 | Billing rules and price books are versioned with effective dates | Historical invoice reproducible from policy plus ledger events |
| GR31 | Reported, measured and estimated usage are distinct; missing provider tokens are not zero | Unknown usage preserved and estimates labeled |
| GR32 | Code, model, voice, dataset and enterprise licenses are reviewed separately | Pinned artifact license inventory, redistribution notices and approval |
| GR33 | Feature disablement does not silently delete data or strand jobs | Disable/drain/cancel/re-enable behavior tested and documented |
| GR34 | Release evidence belongs to the exact artifact/revision | Older green CI does not qualify newer code |
| GR35 | Live integrations require live authorized qualification where claimed | Fake-client success and skipped tests cannot claim live support |
| GR36 | Clean-checkout builds and upgrade/rollback paths are repeatable | CI build, migration and restored-backup tests |
| GR37 | Public website, demo, pricing and sales documents match release evidence | Claim register links each material claim to current evidence |
| GR38 | AI scoring uses observable criteria; predictions are not actual business outcomes | Ground truth, calibration, review and appeals; no inferred actual NPS |
| GR39 | Versioned configuration changes have preview, validation, audit and rollback/compensation | Concurrent edits and stale revision tests |
| GR40 | New features do not silently expand the release commitment | Approved release scope maps feature IDs to gates and blockers |

### 5.4 Governance profiles and user control

| Profile | Intended starting point | Still configurable |
|---|---|---|
| SME | Simple guided setup, few roles, conservative defaults | Allowed providers, budgets, retention and approved actions |
| Medium | Teams, shared inbox, Odoo workflows and departmental budgets | Team scopes and approval routing |
| Enterprise | Federation, fine-grained data policy, separation of duties, operational reporting | Custom role, policy and deployment requirements |
| Government/public-sector | Locality-first or air-gapped profile, controlled update/support channels | Institution-specific rules; no automatic compliance claim |
| Custom | Customer-authored policy under platform safety and contractual limits | Versioned overrides with validation and impact preview |

The user can inspect and modify settings within their role through forms, advanced configuration, text chat or voice. A normal user cannot override tenant-owner or platform security rules. Standards/control mappings, if added, must identify the exact framework version, implemented control, evidence and gaps; a mapping is not certification.

## 6. Expanded capability matrices

Each row is an independently trackable acceptance unit. All rows are **PLANNED / current implementation unverified** unless their section 6.7 record states other evidence (N11 only). Existing overlap is mapped to WP packages without renumbering the original backlog. Expanded scope is approved as product vision, not automatically as a V1 release blocker. Guardrails GR01–GR40 apply throughout.

Rows with an N-ID are the owner-approved backend capabilities N01–N15. Each follows the original rows of the matrix for the area it serves. Where it extends an existing row, its capability text names that row and the extended row names it back. Section 6.7 holds the full records.

### 6.1 Voice and PBX

| ID | Capability and example | S | P | H | O | WP / dependencies | Completion evidence |
|---|---|---|---|---|---|---|---|
| V01 | Import recordings, extension, time and call ID; nightly sales analysis; bulk import and migration extended by N02 | Y | Y | Y | L | WP05/09/16; PBX adapter | Replay-safe import; call ID provenance; correct tenant/channel mapping |
| V02 | Inbound AI secretary answers from permitted knowledge and records request | Y | Y | Y | L | WP08/17/24; voice runtime | Authorized answers, interruption handling, human fallback and Odoo result |
| V03 | Outbound reminders, follow-up and prospect sales | C | C | C | L | WP18/24; carrier; contact policy | Suppression/time/budget checks, approval, attempt ledger, safe ambiguous-outcome handling |
| V04 | Transfer to human with permitted summary and context | C | C | C | L | PBX transfer; WP03/24 | Real transfer, queue failure fallback and context scope test |
| V05 | Browser voice call with AI | Y | Y | Y | L | WebRTC; V02 | Browser permissions, reconnect, user session scope and local-network test |
| V06 | Live suggestions visible only to the human representative | Y | Y | Y | L | WP17; streaming speech | Suggestions do not leak into customer audio; evidence and latency evaluation |
| V07 | Visual call flow: hours, IVR, queues, extension and fallback | C | C | C | L | Policy; workflow; PBX | Validated routing graph, cycle/timeout checks, dry run and rollback |
| P01 | Asterisk / FreePBX / Issabel | C | C | C | L | WP16; exact platform version | Separate conformance record per platform/version, recording and control paths |
| P02 | FreeSWITCH / FusionPBX | C | C | C | L | WP16; SIP/media adapter | Same contract suite plus actual authorized platform tests |
| P03 | Generic PBX or SIP trunk | C | C | C | L | Codec/auth/transport profile | Tested call setup, media, DTMF, transfer, disconnect and failure cases |
| P04 | 3CX | C | C | C | C | Vendor API/license | Supported edition and API scope recorded; no universal support claim |
| P05 | Cisco / Avaya | C | C | C | C | Vendor SDK/version/license | Distinct adapters and certified test targets, not inferred SIP equivalence |
| P06 | Microsoft Teams Phone | C | C | C | — | Microsoft service/SBC/license | End-to-end authorized test and documented cloud dependency |
| N02 | Data import and migration: source definitions, mapping, preview and dry run, rejection report, resumable batches and rollback; extends V01 | Y | Y | Y | L | V01; F13; F64; WP16; N03 | Dry run writes nothing; one logical record per source key; checkpoint resume; batch rollback; rejection report |
| N04 | Data-quality backend: media, audio and transcript quality metrics, thresholds, event history and tenant aggregation; extends F26 | Y | Y | Y | Y | F26; F12; F23; F33; WP09 | Measured value or explicit unknown for every metric; threshold events recorded; no fabricated score |
| N14 | Browser/device support contract: versioned policy, capability negotiation and rejection reasons for recording and upload clients; extends F12 | Y | Y | Y | Y | F12; V05; X01; F68 | Published policy version equals the enforced media limits; negotiation and rejection reasons are stable codes |

V03 in O means internal extension calls only, not public prospect calling. P04/P05 offline eligibility is deployment-specific and must be proved. Recording import and live call control are separate capabilities even on the same PBX.

### 6.2 Calendar, email, tasks and outcomes

| ID | Capability and example | S | P | H | O | WP / dependencies | Completion evidence |
|---|---|---|---|---|---|---|---|
| A01 | Find, create, change or cancel an appointment | Y | Y | Y | L | WP16/24; calendar | Timezone/DST, conflict detection, idempotent booking and participant approval |
| A02 | Multi-stage reminder: notify, message, then call if permitted | Y | Y | Y | L | A01; communications | Escalation stops on acknowledgment, cancellation or suppression |
| A03 | Email summary, reply draft and approved sending | Y | Y | Y | L | Mail connector; policy | Correct thread/recipient, attachment scope, preview and auditable send |
| A04 | Assign Odoo activity with owner and deadline | Y | Y | Y | L | Odoo; WP24 | Correct company/user mapping and one activity after duplicate delivery |
| A05 | Follow until accepted/completed/blocked; record outcome | Y | Y | Y | L | Durable workflows; A04 | Distinct delivery/acceptance/completion events with timeout and escalation |
| A06 | Act autonomously inside approved rules; escalate exceptions | Y | Y | Y | Y | G03–G05; policy | Boundary tests: appointment change allowed, unauthorized discount refused |

### 6.3 Communications and shared history

| ID | Capability and example | S | P | H | O | WP / dependencies | Completion evidence |
|---|---|---|---|---|---|---|---|
| C01 | Telegram request, result and approval buttons | C | C | C | — | Channel connector; G02 | Verified identity, replay-safe callbacks and platform initiation rules |
| C02 | WhatsApp Business conversation and notifications | C | C | C | — | Official business API | Current template/session permissions, webhooks and delivery reconciliation |
| C03 | Bale messages and approval requests | C | C | C | — | Official Bale API | Tested capabilities; no assumed Telegram parity |
| C04 | Eita integration for verified supported operations | C | C | C | — | Confirmed API/access | Explicit operation list; unsupported private replies stay unavailable |
| C05 | SMS and email confirmations with secure links | Y | Y | Y | L | Asanak/mail; G02 | Single-use scoped confirmation; expiry; internal email only for O |
| C06 | Social assistant drafts, schedules and answers where API permits | C | C | C | — | Official network APIs | Approval before publish; credentials scoped; per-network capability tests |
| C07 | Shared customer timeline across calls, email and messages | Y | Y | Y | L | Odoo identity; connector ledger | Authorized identity links, channel provenance and deduplication |
| N07 | Notification preferences: user, tenant, channel and event preferences, quiet hours, escalation, acknowledgement and stop handling; extends F59 | Y | Y | Y | L | F59; F69; A02; C01–C05; N08 | Quiet hours and stop honored on every channel; one delivery per event window; attempts, fallback and acknowledgement recorded |

### 6.4 Access and delegated authority

| ID | Capability | S | P | H | O | WP / dependencies | Completion evidence |
|---|---|---|---|---|---|---|---|
| G01 | Record/team/owner/field-level access | Y | Y | Y | Y | WP03/17/23 | Unauthorized records and fields absent in every delivery path |
| G02 | Verified identity linking across channels | Y | Y | Y | L | WP03/16 | Account takeover/unlink/recovery tests; no number-only identity |
| G03 | Temporary, limited and revocable Agent delegation | Y | Y | Y | Y | WP03/24 | Action/resource/budget/expiry enforcement and revocation before execution |
| G04 | Central approval inbox with recipient, payload and cost | Y | Y | Y | Y | WP22/24 | Immutable approval binding, rejection, expiry and changed-payload tests |
| G05 | Independent multi-person approvals | Y | Y | Y | Y | WP03/23/24 | Distinct approvers and separation-of-duty policy enforced |
| N03 | Recording consent policy: versioned jurisdiction policies, consent receipts and per-purpose permissions enforced before recording, processing, export or outbound action | Y | Y | Y | Y | GR12; GR14; GR25; F15; V02–V04 | Missing or withdrawn consent blocks the action server-side at every entry point; receipts cite the policy version |

### 6.5 BYOK, providers and settings

| ID | Capability | S | P | H | O | WP / dependencies | Completion evidence |
|---|---|---|---|---|---|---|---|
| K01 | Separate organization/workspace keys and provider accounts | Y | Y | Y | L | WP08/18/22 | Key isolation, encrypted storage, revocation and billing attribution |
| K02 | Personal key for authorized users under organization policy | Y | Y | Y | L | K01; G01 | Personal key cannot bypass region/data/capability restrictions |
| K03 | Mixed resources per workflow stage | Y | Y | Y | L | WP08; policy snapshot | Audio local and only approved redacted text remote; all-local version for O |
| K04 | Routing among approved resources by quality/cost/latency | Y | Y | Y | Y | Registry; eval; budget | Constrained routing, transparent selection and no forbidden fallback |
| K05 | Homayar AI API Gateway adapter | C | C | C | C | Actual Gateway contract | Auth/schema/usage/error/routing contract suite; offline only if Gateway/backends local |
| K06 | Settings tab for every enabled mode/provider | Y | Y | Y | Y | WP22; K01–K05 | Endpoint/model/key-reference/region/budget tests; safe connectivity check; no secret echo |
| K07 | Provider/model qualification and deprecation | Y | Y | Y | Y | WP08/13 | Pinned artifacts, representative evaluation and tested migration/fallback |
| N05 | Reprocessing workflow: choose a prior result and a new model, prompt or provider version; estimate, approve, compare, accept or roll back | Y | Y | Y | Y | F24; F45; F17; N15; N08; G04 | New attempt identity; prior revisions immutable; idempotent submission; rollback restores the previous accepted revision; settled once |
| N06 | Feedback-to-evaluation loop: corrections with reason codes feed versioned, rights-checked evaluation datasets; extends F81 | Y | Y | Y | Y | F81; F80; F44; F45; N03 | Only eligible data enters a dataset version; training and evaluation splits disjoint; deletion propagates; no silent production training |
| N08 | Provider budget kill-switch: tenant, workspace, user, provider, workflow and model budgets reserved before each provider call; extends F18 | Y | Y | Y | Y | F18; K04; GR13; GR31; X11 | Concurrent calls cannot exceed a hard budget; retries and fallback cannot bypass it; unknown usage kept unknown |
| N15 | Model and prompt rollback: immutable versions, active-version pointer, staged promotion and rollback with affected-result lineage; extends F20 | Y | Y | Y | Y | F20; F81; K07; GR11 | No mutable "latest" in production; audited rollback to the previous accepted version; affected results listed |

### 6.6 Product configuration, commercial delivery and support

| ID | Capability | S | P | H | O | WP / dependencies | Completion evidence |
|---|---|---|---|---|---|---|---|
| X01 | Capability catalog with multiple provider alternatives | Y | Y | Y | Y | WP08/19/22 | Stable IDs, dependency graph, availability distinct from release support |
| X02 | Admin module check/uncheck | Y | Y | Y | Y | Licensing; G01 | API/worker enforcement, dependency preview, job drain and data preservation |
| X03 | Manual, text and voice configuration for nontechnical users | Y | Y | Y | Y | V05; workflow; policy | Same permissions; understandable preview; correction and cancellation |
| X04 | AI drafts models/settings/workflow from needs and documents | Y | Y | Y | Y | RAG; registry; X03 | Uses only qualified available choices; assumptions visible; never creates fictional model support |
| X05 | Workflow sandbox/test run | Y | Y | Y | Y | WP06/24 | No live irreversible side effects; fixtures labeled; predicted costs identified as estimates |
| X06 | Policy profiles: SME/medium/enterprise/government/custom | Y | Y | Y | Y | WP03/08/22/23 | Versioned overrides, effective policy explanation and conflict detection |
| X07 | Demo questionnaire, registration and RFP | Y | Y | Y | L | WP21; Odoo | Consented lead, complete requirements export, email delivery ledger; local draft in O |
| X08 | Selected-capability customer package/export | Y | Y | Y | Y | WP01/19; X01 | Signed manifest, required assets only, license notices, reproducible install |
| X09 | Online/offline license, activation and entitlement | Y | Y | Y | Y | WP18/19 | Signature, expiry, migration, clock/grace and documented revocation semantics |
| X10 | Plans, BYOK, wallet/credits and hybrid commercial policy | Y | Y | Y | Y | WP18 | Exact effective price policy and reproducible invoices; local settlement in O |
| X11 | Token/audio/call/user/time cost and usage reports | Y | Y | Y | Y | WP06/18/20 | Measured/reported/estimated separation; unknown tokens preserved; tenant/role scope |
| X12 | Failed, cancelled, retried and empty-transcript charge rules | Y | Y | Y | Y | WP18; owner policy | Every terminal outcome has versioned settlement/refund rules and idempotency tests |
| X13 | Finite/indefinite/keep-for-now/retired retention administration | Y | Y | Y | Y | WP07/22 | Precise transitions, review and hold interactions; no ambiguous zero-day setting |
| X14 | Correlated logs, traces, metrics and audit | Y | Y | Y | Y | WP20 | Tenant-safe correlation; retention; tamper evidence; no default content logging |
| X15 | Previewable diagnostic bundle and ticket upload | Y | Y | Y | Y | WP19/20; helpdesk | Redaction, encryption, checksum, consent; offline file export works |
| X16 | Optional commercial remote support; support entitlement extended by N09 | Y | C | C | — | Support license; temporary authorization | Expiring session, scoped access, audit and immediate disconnect |
| X17 | Persian/English UI, RTL/LTR and accessible configuration | Y | Y | Y | Y | WP22 | Keyboard/screen-reader checks, local font, timezone and localized messages |
| X18 | Evidence-backed scoring, correction and coaching | Y | Y | Y | Y | WP11–15 | Versioned rubric, citations, human override and appeal audit |
| X19 | Executive/team/user dashboards and scheduled reports | Y | Y | Y | Y | WP15/18/24 | Role-filtered reports; actual outcomes distinguished from predictions |
| X20 | Updates, rollback, backups and restore with deletion reapply | Y | Y | Y | Y | WP01/07/19/20 | Signed artifacts, migration/restore drill and access blocked until reconciliation |
| X21 | Optional HA/scale profile with capacity admission | C | C | C | C | WP20; qualified hardware | Measured concurrency, failover and capacity limits; no untested SLA |
| X22 | Evidence-backed marketing/SEO/GEO and pricing | Y | Y | Y | L | WP21 | Public claim matches offered feature/mode/provider; internal collateral in O |
| X23 | Connector SDK, versioned API and signed webhooks | Y | Y | Y | Y | WP16 | Scope, signature, replay, compatibility and delivery-ledger tests |
| X24 | Model, voice and dataset rights inventory | Y | Y | Y | Y | WP08/13/19 | Every distributed artifact has provenance, license and permitted-use record |
| N01 | Customer onboarding: idempotent, resumable tenant and workspace provisioning with owner, invitations, provider, profile and policy selection | Y | Y | Y | Y | X01; X06; X07; X09; F04–F06 | Repeat provisioning creates one tenant; a failed step resumes or cleans up; per-step audit; no cross-tenant access |
| N09 | Support entitlement: plan/contract support tier, SLA metadata, bundle eligibility, expiry, grace and suspension enforced in the backend; extends X16 | Y | Y | Y | Y | X16; X09; X15; F75; D13 | Expired or out-of-scope entitlement denies the support action server-side; billing state alone never grants access |
| N10 | Privacy-preserving product telemetry: versioned content-free events, opt-out, pseudonymous IDs, retention and deletion | Y | C | C | L | X14; F78; X01; GR16; GR24 | No content or secrets in events; opt-out and deployment-mode policy honored; zero telemetry egress in O |
| N11 | Migration and legacy retirement: inventory, replacement map, forbidden-path checks, removal after proof and controlled data migration; extends F01 | Y | Y | Y | Y | F01; F02; WP01; D18 | Per-path inventory with evidence; retired modules cannot return; live route set unchanged; CI on the merged revision |
| N12 | Disaster recovery objectives: versioned RPO/RTO per profile, backup scope, restore verification and drill evidence; extends F79 | Y | Y | Y | Y | F79; X20; GR22; GR23; D03 | Drill meets the approved RPO/RTO; isolation, legal hold and deletion journal verified before access |
| N13 | Accessibility backend support: localized error codes and metadata, field identifiers, locale, direction and accessible export metadata; extends F72 | Y | Y | Y | Y | F72; X17; F70; WP22 | Stable codes with Persian and English messages; RTL/LTR-safe export metadata; accessibility evidence records |

### 6.7 Approved backend capabilities N01–N15

The owner approved these fifteen backend capabilities on 2026-09-29 (owner mission prompt, sections 10 and 18; Linear parent [HOM-141](https://linear.app/homayar/issue/HOM-141)). Each keeps its N-ID as its stable capability ID and has one row in the matrix of the area it serves (sections 6.1 and 6.3–6.6). Four are new capabilities: N01, N03, N05 and N10. The other eleven extend an existing row, which keeps its ID, title and acceptance text and points back to the N-record: N02→V01, N04→F26, N06→F81, N07→F59, N08→F18, N09→X16, N11→F01, N12→F79, N13→F72, N14→F12 and N15→F20.

Rules for every record below:

- **Deployment modes.** The S/P/H/O values in each matrix row use the section 3 legend and are design targets, not release support. S is Online SaaS, P Connected On-Premise, H Hybrid and O Fully Offline/Air-Gapped. BYOK is credential ownership, not a fifth mode. O denies public egress, including provider fallback, retries and telemetry.
- **Status.** Section 4 vocabulary, held per feature × mode × adapter × version. A record is PLANNED until acceptance evidence for its own criteria exists on a named revision. "Existing foundation" lists code found by reading the source at `7112407`. It is a starting point, not acceptance evidence, and no test was run for this documentation update. The mission planning ledger ([coverage-ledger.json](../docs/handoff/session-reset/backend-mode-100-linear/20260929-backend-mode-100/coverage-ledger.json)) counts such foundations toward PARTIAL for N01, N02, N04, N05, N12, N13, N14 and N15. This Atlas records them as PLANNED under the rule above; the two sources differ in classification, not in evidence.
- **Shared requirements.** GR01–GR40 and RG01–RG10 apply. Unless a record says otherwise, the tenant boundary is workspace scope under PostgreSQL row-level security plus server-side authorization (GR01, GR02), and audit records carry actor, scope, time, reason and result without secrets or raw content (F09, GR24). Completion evidence uses the section 8.4 format.

#### N01 — Customer onboarding backend

- **Linear:** [HOM-142](https://linear.app/homayar/issue/HOM-142). **Matrix:** 6.6. **ID decision:** new ID. No row covers tenant provisioning as one process: X07 covers demo/RFP intake, X06 policy profiles, X09 licence activation and F04–F06 login and permissions. N01 connects them.
- **Purpose:** take a new customer from an approved registration or RFP to a workspace that is ready for its first upload, through one resumable, auditable process.
- **Backend contract:** tenant provisioning; workspace creation; initial owner/admin; invitation lifecycle with expiry and revocation; provider configuration; deployment-profile selection; initial policy selection; first-upload readiness; resumable onboarding state; idempotent provisioning; audit history; rollback or cleanup of failed provisioning; no cross-tenant access.
- **Deployment modes:** S Y · P Y · H Y · O Y. Provider configuration offers only the choices the selected profile permits. In O that means local providers and a signed offline licence (X09), with no activation call-out.
- **Dependencies:** X01 capability catalog; X06 policy profiles; X09 licensing; K06 provider settings; F04–F06 identity and permissions; WP18-T03 versioned plans. X07 hands its RFP/profile output to N01.
- **Security and privacy:** server-side authorization on every step; only a platform operator or an authorized self-service path creates a tenant; invitation tokens are single-use, expiring and revocable; provider credentials are stored as secret references and never echoed (K06); onboarding state holds no secret values.
- **Tenant boundary:** each onboarding run belongs to exactly one organization and workspace; a failed or abandoned run exposes no partial data to any other tenant.
- **Audit:** every step transition, invitation issue/acceptance/revocation, provider and profile selection, and rollback.
- **Failure behavior:** each step is idempotent and checkpointed; a failed step resumes from its checkpoint or runs compensating cleanup; a partly provisioned tenant stays blocked from ordinary use until the run completes or is cleaned up.
- **Offline implications:** fully local; invitations use internal mail or out-of-band delivery; no remote activation or provider check in O.
- **Commercial and usage implications:** the plan, trial allowance (D05) and BYOK/commercial combination come from versioned plan records (WP18-T03, X10); provisioning itself creates no usage charge; a failed run releases any reservation it made.
- **Repository candidates:** none named by the owner; extend the existing tenancy and membership services.
- **Acceptance criteria:** (1) repeating the same provisioning request creates one organization, one workspace and one owner; (2) a failure injected at each step leaves a resumable or cleaned-up state with no orphaned access; (3) invitations expire and revoke as configured and cannot be replayed; (4) profile and policy selection reject choices the plan, licence or deployment mode does not allow; (5) first-upload readiness reports every missing prerequisite; (6) two-tenant negative tests show zero cross-tenant reads or writes.
- **Completion evidence:** state-machine tests with injected failures, idempotency tests and two-tenant negative tests on PostgreSQL under the application role, plus per-step audit records, all for the exact revision. Current evidence: none.
- **Existing foundation:** registration creates an organization, workspace and owner membership, plus a demo subscription when `enable_free_demo` is set (`backend/services/tenancy_service.py`, `create_personal_workspace`); workspace invitations carry `expires_at` and `revoked_at` (`backend/database/saas_models.py`, `WorkspaceInvitation`); organization workspace creation (`backend/routers/v1/organizations.py`).
- **Status:** PLANNED. No N01 acceptance criterion has run.
- **External qualification:** owner decisions D05 (trial/demo), D10 (licensing) and D12 (identity) supply the values the flow applies.

#### N02 — Data import and migration

- **Linear:** [HOM-143](https://linear.app/homayar/issue/HOM-143). **Matrix:** 6.1. **ID decision:** extends V01 (import recordings with extension, time and call ID; replay-safe import and call ID provenance). V01 is the only Atlas row about importing data; N02 generalizes it to any approved source and to migration from legacy systems. F13 (ingestion ledger) and F64 (ID and outcome mapping) are related.
- **Purpose:** bring recordings and related records from customer systems, archives and legacy tools into Homayar with preview, validation and rollback, so migration does not depend on one-off scripts.
- **Backend contract:** import source definitions; upload and streamed ingestion; schema mapping; preview; dry run; validation; rejection report; resumability; checkpoints; idempotency; provenance; tenant isolation; conflict policy; rollback; usage accounting; audit events; legacy source mapping.
- **Deployment modes:** S Y · P Y · H Y · O L. In O, sources are local files, internal PBX systems or internal databases; external SaaS sources are unavailable.
- **Dependencies:** V01; F13 ingestion ledger; F12 media validation; F17 checkpoints and leases; F64 and WP16-T04 mappings; WP09-T01; N03 (imported recordings need a consent basis); N04 (quality events for imported media).
- **Security and privacy:** source credentials are secret references; imported media is validated before storage (F12) and never reaches inference unvalidated; rejection reports carry record identifiers and reason codes, not raw content; imports respect data-class and region policy (F21).
- **Tenant boundary:** each import batch belongs to one workspace; every created record carries that workspace under row-level security; a mapping cannot target another tenant.
- **Audit:** source definition changes, dry runs, commits, rejections, conflict resolutions and rollbacks.
- **Failure behavior:** checkpointed batches resume after worker or broker failure without duplicates; invalid records go to the rejection report; rolling back a batch removes or tombstones exactly that batch's records.
- **Offline implications:** file and internal-source imports work in O; no connector may download drivers or schemas at runtime.
- **Commercial and usage implications:** imported audio is metered once per logical record under the versioned policy (F18, X12); dry run and preview create no charge unless the policy says so; rejected records are not billed.
- **Repository candidates:** dlt, Meltano and Airbyte (section 10.13). The owner prefers a native implementation on the ingestion ledger, outbox and checkpoints, or dlt; Airbyte only if connector scale justifies a separate platform.
- **Acceptance criteria:** (1) a dry run reports what would be created, updated, rejected or conflicted and writes nothing; (2) importing the same source record twice yields one logical record, and a changed payload under the same key is resolved by the configured conflict policy; (3) an interrupted batch resumes from its checkpoint; (4) rolling back a batch removes access to exactly that batch; (5) the rejection report lists every rejected record with a reason code; (6) provenance links each record to its source and mapping version; (7) two-tenant negative tests pass.
- **Completion evidence:** import contract tests for dry run, idempotency, resume, rollback and rejection; two-tenant tests on PostgreSQL; a legacy-source mapping fixture; the exact revision. Current evidence: none.
- **Existing foundation:** idempotent ingestion keyed by source identity, where a different payload hash under the same identity is recorded as a conflict (`backend/services/ingestion_service.py`); voicemail import as a second producer (`backend/voicemail/ingest.py`); stage checkpoints (`backend/jobs/checkpoints.py`).
- **Status:** PLANNED. No N02 acceptance criterion has run.
- **External qualification:** representative samples from each supported source type; live PBX access for PBX imports (P01–P03).

#### N03 — Recording consent policy

- **Linear:** [HOM-144](https://linear.app/homayar/issue/HOM-144). **Matrix:** 6.4. **ID decision:** new ID. No capability row covers recording and processing consent: GR25 governs contact campaigns, GR14 separates export permissions, section 7.3 requires a recording notice and F15 covers retention and subject export. N03 is the enforcement capability those rules rely on.
- **Purpose:** decide, before any recording, processing, analysis, export or outbound action, whether an approved basis and the required notice exist for that conversation, and keep the proof.
- **Backend contract:** jurisdiction-aware policies; policy versions; lawful or approved basis; announcement requirements; consent receipt; recording permission; processing permission; analysis permission; export permission; withdrawal; opt-out; expiry; retention relationship; channel relationship; audit; enforcement before recording, processing or outbound action. Homayar remains the enforcement authority; an external consent service may hold records but never decides alone.
- **Deployment modes:** S Y · P Y · H Y · O Y. Policy evaluation and receipts must be local and need no call-out.
- **Dependencies:** GR12, GR14, GR25; F15 retention and subject export; ingestion paths (uploads, voicemail and V01 imports); the processing claim in the job pipeline (F17); exports (F70); outbound V03 and channels C01–C06. Consumers include V02–V04, N02, N06 and N07.
- **Security and privacy:** default deny when no applicable policy or basis exists; withdrawal takes effect before the next action; permissions are purpose-specific, so a recording permission does not allow export; receipts are tamper-evident and hold minimal personal data.
- **Tenant boundary:** policies and receipts are workspace-scoped; platform defaults are templates copied into a tenant, not shared records.
- **Audit:** policy version publication, receipt creation, withdrawal, opt-out, expiry, and every denied action with the policy version applied.
- **Failure behavior:** fail closed. When the policy is unavailable or ambiguous the action is refused with a stated reason; a job that loses its basis mid-run stops before its next stage and records a partial state (F23).
- **Offline implications:** fully local; jurisdiction policy updates arrive as signed packages.
- **Commercial and usage implications:** work refused for lack of consent creates no usage; outbound and campaign features (V03, C01–C06) cannot be offered in a region without an approved policy for it.
- **Repository candidates:** OpenFGC (section 10.13), as a reference only.
- **Acceptance criteria:** (1) without a valid basis, every entry point (upload, import, voicemail, processing claim, export, outbound) refuses server-side; (2) withdrawal blocks later processing and export; (3) every receipt references the exact policy version; (4) expiry and retention interact as configured; (5) recording, processing, analysis and export permissions are independent; (6) tightening a policy mid-run applies to later stages (GR12).
- **Completion evidence:** deny-case tests at each entry point with allow controls, withdrawal race tests and receipt audit checks, for the exact revision. Current evidence: none.
- **Existing foundation:** none; no consent module exists in the backend source at `7112407`.
- **Status:** PLANNED. No N03 acceptance criterion has run.
- **External qualification:** owner or legal input on each jurisdiction's rule content (engineering defaults until then). The engine alone supports no legal-compliance claim.

#### N04 — Data-quality backend

- **Linear:** [HOM-145](https://linear.app/homayar/issue/HOM-145). **Matrix:** 6.1. **ID decision:** extends F26 (audio normalization, VAD and quality checks; its acceptance requires exposed quality flags and no fabricated transcript for empty, music or noise input). F12 media validation, F23 honest stage states and F33 confidence are related.
- **Purpose:** give every recording and result measured, explainable quality signals, so reviewers and charge rules know what was actually processed, without invented scores.
- **Backend contract:** media validation metrics; codec/container state; audio quality; clipping; silence; duration; channel information; transcript confidence; diarization availability; partial or missing stage detection; provider failures; invalid evidence; quality thresholds; per-tenant aggregation; quality-event history; explicit unknown state; no fabricated scores.
- **Deployment modes:** S Y · P Y · H Y · O Y. Metrics must be computed locally.
- **Dependencies:** F26; F12; F23; F33; WP09-T02 (normalized derivatives and VAD); F30 diarization; F22 provider failures. X12 charge rules consume the results.
- **Security and privacy:** quality records hold measurements and codes, not audio or transcript text; aggregates respect role scope (G01).
- **Tenant boundary:** quality records are per workspace; aggregation never mixes tenants.
- **Audit:** threshold changes and quality overrides; quality events form an append-only history.
- **Failure behavior:** a metric that cannot be computed is stored as unknown with a reason; a threshold breach records an event instead of a silent drop.
- **Offline implications:** none beyond local computation.
- **Commercial and usage implications:** quality results feed failed or empty processing charge rules only through the versioned policy (X12, D04); unknown quality is not treated as failure for billing by default.
- **Repository candidates:** Evidently, Great Expectations and Soda Core (section 10.13) for tabular checks; audio-specific quality logic stays native to Homayar.
- **Acceptance criteria:** (1) synthetic fixtures for clipping, silence, channel layout, short and corrupted media produce the expected metrics; (2) a missing stage or provider failure appears as an explicit quality event; (3) no metric is filled with a default when it was not measured; (4) thresholds are versioned and per tenant; (5) per-tenant aggregation reconciles with the event history; (6) two-tenant negative tests pass.
- **Completion evidence:** fixture-based metric tests, aggregation reconciliation and two-tenant tests, for the exact revision. Current evidence: none.
- **Existing foundation:** stage and coverage quality assessment (`backend/services/quality_service.py`, served at `GET /api/v1/meetings/{meeting_id}/quality`); typed media validation (`backend/media/validation.py`); a PCM16 quality estimator (`estimate_quality` in `backend/speech/audio_profile.py`) that no live code calls yet.
- **Status:** PLANNED. No N04 acceptance criterion has run.
- **External qualification:** representative annotated recordings (F80) before any threshold is presented as qualified.

#### N05 — Reprocessing workflow

- **Linear:** [HOM-146](https://linear.app/homayar/issue/HOM-146). **Matrix:** 6.5. **ID decision:** new ID. F24 requires reanalysis to preserve provenance, F45 marks dependent outputs stale and F20 restores prior manifests, but no row defines an operator-driven reprocessing request with version selection, estimate, approval, comparison and acceptance.
- **Purpose:** let an authorized user re-run a prior result under a different model, prompt or provider version, compare it with the accepted result and decide which one stands.
- **Backend contract:** select the prior result; select the new model, prompt or provider version; immutable prior revisions; new attempt identity; lineage; cost estimate; authorization; approval where required; idempotent submission; bounded retry; cancellation; stale-worker fencing; checkpoint recovery; comparison; acceptance or rejection; rollback to the previous accepted revision; usage settlement. The existing Celery, outbox, lease and checkpoint architecture stays unless it cannot meet this contract.
- **Deployment modes:** S Y · P Y · H Y · O Y. Only versions qualified for the tenant's mode can be selected; in O these are locally installed models with no remote fallback (GR13).
- **Dependencies:** F24; F45 (WP12-T06); F17; F20 and N15 (version identity); N08 (budget reservation); G04 (approval); X12 (settlement rules); F81.
- **Security and privacy:** reprocessing rechecks current policy, consent (N03) and provider permissions; it cannot send data to a provider the current policy forbids, even if the original run used that provider.
- **Tenant boundary:** requests, attempts and revisions belong to the owning workspace; comparison views are role-scoped.
- **Audit:** request, estimate, approval, submission, cancellation, acceptance, rejection and rollback.
- **Failure behavior:** bounded retries with fencing; the accepted revision stays authoritative until a new one is accepted; cancellation releases unused reservations.
- **Offline implications:** local models and artifacts only in O.
- **Commercial and usage implications:** the estimate is shown and the budget reserved before submission; each attempt settles once under the versioned policy; provider cost and customer charge stay distinct (section 7.7); BYOK runs still record usage.
- **Repository candidates:** keep Celery (section 10.7). Temporal (section 10.7), Prefect and Dagster (section 10.13) are candidates, not mandatory replacements.
- **Acceptance criteria:** (1) resubmitting the same request creates one attempt; (2) prior revisions stay unchanged; (3) writes from a stale worker are rejected; (4) acceptance switches the authoritative revision atomically, and rollback restores the previous one; (5) a rejected attempt leaves the accepted result unchanged; (6) the estimate is labeled as an estimate, and settlement matches the ledger exactly once; (7) where policy requires approval, submission waits for it.
- **Completion evidence:** workflow tests covering worker kill, duplicate submission, cancellation and rollback, plus ledger reconciliation, for the exact revision. Current evidence: none.
- **Existing foundation:** dead-letter replay (`backend/jobs/replay.py`); immutable transcript revisions and superseded analysis runs (`backend/services/transcript_service.py`); checkpoints and leases (`backend/jobs/`).
- **Status:** PLANNED. No N05 acceptance criterion has run.
- **External qualification:** provider credentials and qualified alternative models for live comparisons.

#### N06 — Feedback-to-evaluation loop

- **Linear:** [HOM-147](https://linear.app/homayar/issue/HOM-147). **Matrix:** 6.5. **ID decision:** extends F81 (AI regression and model/prompt promotion gates). Its work-package task WP13-T07 already requires production corrections to be collected separately. F80 (corpus rights and splits), F44 (override) and F45 (correction) are related.
- **Purpose:** turn reviewer corrections into governed evaluation data, so model and prompt changes are measured against corrected real cases without using customer data outside its permitted purpose.
- **Backend contract:** supervisor correction events; transcript correction; speaker correction; QA override; reason codes; annotation revision history; consent and data-right checks; eligibility for evaluation datasets; export contract; dataset version; train/evaluation separation; lineage; deletion propagation; no silent production training; no use of unauthorized customer data.
- **Deployment modes:** S Y · P Y · H Y · O Y. Datasets stay inside the tenant's deployment; in O an export is a local file under the export contract.
- **Dependencies:** F81 (WP13-T07); F80; F44 (WP14-T03); F45; N03 (consent and data rights); F15 (deletion); X24 (rights inventory).
- **Security and privacy:** eligibility needs an explicit data-right basis per tenant and purpose, and the default is ineligible; no dataset leaves its tenant without an approved export; nothing trains a production model from these datasets without an explicit approved job.
- **Tenant boundary:** datasets and annotations are workspace-scoped; cross-tenant datasets would need a contractual basis from every contributing tenant and are out of scope by default.
- **Audit:** eligibility decisions, dataset-version creation, exports and deletion propagation.
- **Failure behavior:** deletion propagation retries until confirmed and blocks use of affected dataset versions meanwhile; ineligible items are excluded with a reason.
- **Offline implications:** fully local.
- **Commercial and usage implications:** evaluation use of customer corrections must be permitted by contract; customer data is never resold; any evaluation service offered needs its own versioned price policy.
- **Repository candidates:** Label Studio and Langfuse (section 10.11); Argilla (section 10.13).
- **Acceptance criteria:** (1) every correction event carries a reason code and revision lineage; (2) data without an eligible right never enters a dataset version; (3) training and evaluation splits are disjoint by source identity; (4) deleting a source item removes it from later use and marks affected dataset versions; (5) dataset versions are immutable and reproducible; (6) no production training path reads these datasets without an explicit approved job.
- **Completion evidence:** eligibility and deletion-propagation tests, a split-leakage check and audit records, for the exact revision. Current evidence: none.
- **Existing foundation:** transcript correction revisions (`backend/services/transcript_service.py`) as raw material.
- **Status:** PLANNED. No N06 acceptance criterion has run.
- **External qualification:** a contractual or legal basis for evaluation use per customer; adjudicated annotation (F80).

#### N07 — Notification preferences

- **Linear:** [HOM-148](https://linear.app/homayar/issue/HOM-148). **Matrix:** 6.3. **ID decision:** extends F59 (post-call/digest alerts and review queues; its acceptance requires authorized recipients only and visible suppressed, acknowledged, escalated and failed states). F93 (channel alerts), A02 (reminders) and C01–C05 (channels) are related.
- **Purpose:** let users and tenants control which events reach them, on which channel and when, while keeping delivery reliable and auditable.
- **Backend contract:** user preferences; tenant policy; channel preferences; event preferences; quiet hours; timezone; escalation; suppression; acknowledgement; delivery attempts; idempotency; fallback policy; offline behavior; audit; unsubscribe and stop handling.
- **Deployment modes:** S Y · P Y · H Y · O L. In O only in-app and internal channels (internal mail, internal PBX) deliver; public channels are unavailable.
- **Dependencies:** F59 (WP24-T02); F69 and WP16-T05 delivery ledger; C01–C05; A02; G02 identity; N03 (contact basis); N08 (budgets for paid channels).
- **Security and privacy:** tenant policy bounds user preferences, for example for mandatory security notices; payloads carry minimum content and secure links (C05); stop requests apply across every channel (GR25).
- **Tenant boundary:** preferences and deliveries are workspace-scoped; a recipient must be authorized for the underlying record.
- **Audit:** preference and policy changes, deliveries, acknowledgements, suppressions and stop requests.
- **Failure behavior:** every attempt is recorded; retries are bounded and idempotent; fallback goes only to channels the policy and the user allow; a suppressed recipient is never retried through another channel.
- **Offline implications:** no public channel in O; fallback cannot leave the local network.
- **Commercial and usage implications:** paid channels (SMS, WhatsApp, carrier calls) are metered per attempt under the versioned policy and budget (N08).
- **Repository candidates:** Apprise (section 10.8); Novu (section 10.13).
- **Acceptance criteria:** (1) quiet hours in the recipient's timezone defer non-urgent notices; (2) escalation stops on acknowledgement; (3) duplicate events inside the deduplication window produce one delivery; (4) stop or unsubscribe blocks later deliveries on every channel; (5) attempts, failures and fallback are visible; (6) unauthorized recipients never receive the notice.
- **Completion evidence:** preference-evaluation tests, delivery-ledger idempotency tests and cross-channel stop and suppression tests for the exact revision; live channel tests only for channels that are claimed (RG07). Current evidence: none.
- **Existing foundation:** a `notifications` table written only by the voicemail pipeline (`backend/voicemail/pipeline.py`), with no read, acknowledgement or delivery path.
- **Status:** PLANNED. No N07 acceptance criterion has run.
- **External qualification:** provider accounts and live tests for each claimed channel.

#### N08 — Provider budget kill-switch

- **Linear:** [HOM-149](https://linear.app/homayar/issue/HOM-149). **Matrix:** 6.5. **ID decision:** extends F18 (usage metering, quotas and plan limits; its acceptance requires that concurrent reservations cannot exceed a hard quota or double charge). K04 (routing within budget), GR13, GR31 and X11 are related.
- **Purpose:** stop provider spending at configured limits before a call is made, so a runaway workflow, retry loop or fallback cannot overspend.
- **Backend contract:** tenant budget; workspace budget; user budget where allowed; provider budget; workflow budget; model budget; time-window limits; reservations before provider calls; atomic ledger transitions; warning thresholds; hard stop; cancellation; refund/release; retry protection; fallback protection; operator override; approval; audit; unknown provider usage handling. The Homayar usage ledger stays the authority.
- **Deployment modes:** S Y · P Y · H Y · O Y. Budgets apply to BYOK credentials too, since BYOK is ownership, not an exemption. In O budgets govern local compute and model usage, and no fallback may reach a public provider.
- **Dependencies:** F18 (WP18-T02); K04; GR13; GR29; GR31; X11; G04 (override approval). Consumers include N05 (estimates).
- **Security and privacy:** budget configuration needs admin authority; overrides need an approval bound to the exact change (GR05); ledger entries hold no prompt or content.
- **Tenant boundary:** budgets and reservations belong to their tenant or workspace; one tenant's exhaustion never releases or consumes another's.
- **Audit:** budget changes, warnings, hard stops, overrides, releases and unknown-usage reconciliations.
- **Failure behavior:** fail closed. If the ledger is unavailable, no provider call is made; an ambiguous provider outcome is reconciled before retry (GR29); unknown usage stays unknown, never zero (GR31).
- **Offline implications:** the ledger is local and does not depend on connectivity.
- **Commercial and usage implications:** upstream provider cost and customer charge stay separate (section 7.7); budgets are customer controls, not price changes; BYOK usage may be billed by the provider directly but is still reserved and reported.
- **Repository candidates:** LiteLLM (section 10.5); Helicone and Portkey Gateway (section 10.13). None replaces the Homayar usage ledger.
- **Acceptance criteria:** (1) 100 concurrent calls cannot exceed a hard budget; (2) retries and fallback reserve before calling and stop at the limit; (3) cancellation releases unused reservations exactly; (4) a warning threshold fires once per window; (5) an override needs authorized approval and is audited; (6) unknown provider usage is stored as unknown and reconciled.
- **Completion evidence:** PostgreSQL concurrency tests, retry and fallback bypass tests and ledger reconciliation, for the exact revision. Current evidence: none.
- **Existing foundation:** processing-quota reservation before work and single settlement (`backend/services/entitlement_service.py`, tables `usage_reservations` and `usage_events`); a per-endpoint request-per-minute window (`provider_rate_limits`, `backend/providers/resilience.py`), which limits call count, not spend. No provider-spend budget exists yet.
- **Status:** PLANNED. No N08 acceptance criterion has run.
- **External qualification:** accurate usage reporting from each live provider (RG07).

#### N09 — Support entitlement

- **Linear:** [HOM-150](https://linear.app/homayar/issue/HOM-150). **Matrix:** 6.6. **ID decision:** extends X16 (optional commercial remote support, which depends on a support licence and temporary authorization). X09 (licence and entitlement), X15 (diagnostic bundle) and F75 are related.
- **Purpose:** decide in the backend which support a customer is entitled to, including tier, SLA terms, bundle scope and remote sessions, from its plan or contract, with expiry and grace handled explicitly.
- **Backend contract:** plan entitlement; contract entitlement; support tier; SLA metadata; support-bundle eligibility; diagnostic scope; feature access; deployment entitlement; expiry; grace period; suspension; renewal; audit history; backend enforcement; no secret or raw-content export by default. Billing systems must not become authorization authorities.
- **Deployment modes:** S Y · P Y · H Y · O Y. In O the entitlement comes from a signed offline licence with documented bounded validity (X09); remote support sessions (X16) are unavailable and bundles export as files (X15).
- **Dependencies:** X16; X09 (WP19-T04 signed offline entitlements); X15; F75; D13.
- **Security and privacy:** bundles exclude credentials, other tenants, full recordings and transcripts by default (GR24, section 7.5); diagnostic scope is limited to what the entitlement grants.
- **Tenant boundary:** an entitlement binds to one organization or contract, and support access stays within that tenant's scope.
- **Audit:** entitlement grants, changes, expiry, suspension, renewal and every entitlement-gated support action.
- **Failure behavior:** an expired or unknown entitlement denies the gated support action with a reason; grace follows the versioned policy.
- **Offline implications:** no remote renewal check in O; renewal arrives as a signed update.
- **Commercial and usage implications:** tiers and SLA terms are versioned commercial records; a billing or payment event alone never grants or removes support access.
- **Repository candidates:** Lago and Kill Bill (section 10.10); flagd (section 10.9). None may act as the authorization authority.
- **Acceptance criteria:** (1) each tier grants exactly its listed support actions, enforced server-side; (2) expiry, grace, suspension and renewal follow policy at every boundary; (3) a payment callback cannot activate support without the entitlement service; (4) a default bundle contains no canary secret and no raw content; (5) a tampered offline licence is rejected.
- **Completion evidence:** entitlement boundary tests, bundle redaction scans and audit records, for the exact revision. Current evidence: none.
- **Existing foundation:** none specific to support entitlement in the backend source at `7112407`.
- **Status:** PLANNED. No N09 acceptance criterion has run.
- **External qualification:** owner decision D13 (SLA, bundle recipient and remote-access policy).

#### N10 — Privacy-preserving product telemetry

- **Linear:** [HOM-151](https://linear.app/homayar/issue/HOM-151). **Matrix:** 6.6. **ID decision:** new ID. X14 and F78 cover operational logs, traces and metrics; no row covers product-usage telemetry with opt-out and pseudonymous identifiers.
- **Purpose:** learn which features and backend operations are used, and how they fail, without collecting customer content or secrets.
- **Backend contract:** feature-usage events; backend operation events; no raw audio by default; no transcript content by default; no provider secret collection; tenant opt-out; user policy; deployment-mode policy; offline-safe behavior; aggregation; retention; deletion; pseudonymous identifiers; export; audit; sampling; schema versioning.
- **Deployment modes:** S Y · P C · H C · O L. P and H send telemetry only to a destination the customer approved; O keeps telemetry local or off, with zero egress, including retries and buffered uploads.
- **Dependencies:** X14; F78; WP20-T01; X01 (deployment profile); GR16; GR24; F15 (deletion).
- **Security and privacy:** a versioned, content-free event schema is enforced where events are created, and free-text fields are not allowed; pseudonymous identifiers cannot be reversed without a separately protected key.
- **Tenant boundary:** tenant opt-out is authoritative; events carry a pseudonymous tenant key; deleting a tenant's data removes its events.
- **Audit:** opt-out changes, schema versions, exports and deletions.
- **Failure behavior:** telemetry failures never block product operations; the local buffer is bounded and records how many events it dropped; nothing retries across a denied network.
- **Offline implications:** O denies telemetry egress; local aggregation runs only if enabled.
- **Commercial and usage implications:** telemetry is not a billable metric and is not sold; any commercial effect of opting out needs an explicit owner decision.
- **Repository candidates:** OpenTelemetry Collector (section 10.11); PostHog and Plausible (section 10.13).
- **Acceptance criteria:** (1) the schema rejects content fields; (2) opt-out stops collection within one flush interval; (3) a network-denial test in O shows zero telemetry egress; (4) pseudonymous identifiers cannot be joined to a user without the protected key; (5) retention and deletion remove events on schedule; (6) each event records its sampling rate and schema version.
- **Completion evidence:** schema tests, opt-out and egress-denial tests and a retention-job test, for the exact revision. Current evidence: none.
- **Existing foundation:** none; no telemetry module exists in the backend source at `7112407`.
- **Status:** PLANNED. No N10 acceptance criterion has run.
- **External qualification:** an approved telemetry destination and a privacy review for S, P and H.

#### N11 — Migration and legacy retirement

- **Linear:** [HOM-152](https://linear.app/homayar/issue/HOM-152); follow-up [HOM-158](https://linear.app/homayar/issue/HOM-158). **Matrix:** 6.6. **ID decision:** extends F01 (release/source provenance). F01's work package WP01 holds task WP01-T03, "keep legacy routes disabled and document compatibility boundaries", which this capability extends (the mission planning ledger records WP01-T03 as validated before N11). D18 (existing code recovery) is related.
- **Purpose:** remove superseded code paths only after proving they are dead, keep them from coming back, and migrate any legacy data under control, so the product has one authoritative surface.
- **Backend contract:** legacy inventory; replacement mapping; dependency mapping; legacy data migration; compatibility boundary; dual read/write only when explicitly necessary; checkpoints; rollback; retirement gates; forbidden legacy-path checks; dead-route removal after proof; no activation of unsafe legacy auth or graph exposure; status and evidence for every retired path.
- **Deployment modes:** S Y · P Y · H Y · O Y. Retirement applies to every build and profile.
- **Dependencies:** F01; F02 (CI and regression suites); WP01-T04 ([HOM-28](https://linear.app/homayar/issue/HOM-28)) for dependency pruning; D18.
- **Security and privacy:** retired code is deleted, not left unmounted; unsafe patterns such as a non-constant-time or fail-open secret check are recorded and never reactivated; environment variables of retired settings are ignored and not exposed.
- **Tenant boundary:** code retirement holds no tenant data; any legacy data migration must assign each record to exactly one workspace under row-level security.
- **Audit:** the retirement inventory records each path with its evidence, replacement and status; a legacy data migration needs batch audit as in N02.
- **Failure behavior:** re-introducing a retired module fails the test suite; code retirement is rolled back by reverting its commit; a legacy data migration needs checkpoints and rollback.
- **Offline implications:** none specific; the guards run locally and in CI.
- **Commercial and usage implications:** no customer charge; retired capabilities must not be advertised (GR37, F83).
- **Repository candidates:** Semgrep, jscodeshift and ast-grep (section 10.13).
- **Acceptance criteria:** (1) a per-path inventory records what each retired path was, the evidence it was dead, its replacement and its status; (2) every retired module is listed in the forbidden-module guard and cannot be imported; (3) retirement leaves the mounted route set unchanged; (4) retired settings are no longer fields, and their environment variables are ignored in development and production; (5) no legacy auth, webhook or graph route is mounted; (6) any legacy data migration has checkpoints, rollback and a reconciliation report; (7) required CI passes on the merged revision.
- **Completion evidence (delivered):** commit `d5bbe5d` on the integration branch `claude/inspiring-tesla-ie06gi` removed the unmounted pre-v1 routers and services, the legacy LLM router, the Pydantic models module, the WebSocket stub, the prompt package and 15 dead settings. The per-path inventory is [docs/architecture/legacy-retirement.md](docs/architecture/legacy-retirement.md). The guards are `backend/tests/test_truthful_outputs.py` (`REMOVED_MODULES`) and `backend/tests/test_legacy_retirement.py`. On a local PostgreSQL environment, the reviewed tree (base `692affb` plus the patch, which the mission's landing check ([ledger](../docs/handoff/session-reset/backend-mode-100-linear/20260929-backend-mode-100/ledger.md), HOM-152 entry) found identical to `d5bbe5d`, whose parent is `692affb`) passed the blind oracle 199 of 199 and the full backend suite with 0 failed, 1109 passed and 46 skipped ([red-to-green record](../docs/handoff/session-reset/backend-mode-100-linear/20260929-backend-mode-100/n11-t1/red-to-green.md)); focused tests passed 83 of 83 on the integration checkout after landing.
- **Status:** **PARTIAL** (recorded 2026-09-30). That local evidence for the `d5bbe5d` content meets criteria 1–5. It is not VALIDATED for three reasons: required CI has not run on that revision, because GitHub Actions jobs do not start under the account's billing limit (RG01, GR34); the commit is not merged to `main`, whose head is still `2e834ed`; and criteria 6–7 are open. It is not RELEASED. Open items: README residue and the unused `jwt_expiry_hours` setting ([HOM-158](https://linear.app/homayar/issue/HOM-158)); pruning of the `ldap3` extra and `reportlab` in WP01-T04 ([HOM-28](https://linear.app/homayar/issue/HOM-28)); product-console pages that still call retired `/dashboard` endpoints (`frontend/src/lib/hooks.ts`), a frontend follow-up outside this backend scope; and whether any legacy data store needs migration, which is not yet established.
- **External qualification:** working GitHub Actions for CI on the exact revision, then a merge to `main` under the repository's rules.

#### N12 — Disaster recovery objectives

- **Linear:** [HOM-153](https://linear.app/homayar/issue/HOM-153). **Matrix:** 6.6. **ID decision:** extends F79 (encrypted backups and restore drills; its acceptance proposes RPO ≤24h/RTO ≤8h, reconciled manifests and tombstones reapplied before access). X20 and D03 are related.
- **Purpose:** state for each deployment profile how much data may be lost and how long recovery may take, and prove it with restore drills that preserve isolation, deletions and legal holds.
- **Backend contract:** versioned RPO/RTO per deployment profile; PostgreSQL backup contract; storage backup contract; model, artifact and configuration backup scope; encrypted backup metadata; restore workflow; restore verification; tenant isolation after restore; deletion-journal reapplication; legal-hold preservation; credential exclusion; drill evidence; partial-failure states.
- **Deployment modes:** S Y · P Y · H Y · O Y. Objectives differ by profile; in O backups go only to local or removable media or to the customer's own internal sites.
- **Dependencies:** F79; X20; GR22; GR23; F15; WP20-T05 and WP20-T06; X01 (deployment profiles); D03.
- **Security and privacy:** backups are encrypted; credentials are excluded and come back from the secret store; after a restore, access stays blocked until the deletion journal is reapplied and verification passes (GR22).
- **Tenant boundary:** restore verification proves row-level security and tenant isolation on the restored system; a per-tenant restore, if offered, cannot touch other tenants.
- **Audit:** backup runs, restore runs, verification results, drill evidence and changes to objectives.
- **Failure behavior:** partial restore states are explicit and keep access blocked; a failed backup raises an alert and never replaces the last good backup.
- **Offline implications:** local targets only; key custody documented for O.
- **Commercial and usage implications:** RPO/RTO tiers may be part of a plan or contract; no recovery objective or SLA is advertised without drill evidence (GR37).
- **Repository candidates:** pgBackRest and Restic (section 10.11).
- **Acceptance criteria:** (1) each profile has a versioned RPO/RTO record; (2) a drill on the supported topology meets it; (3) database and object manifests reconcile; (4) deleted data stays deleted after restore; (5) legal holds survive restore; (6) no credential is present in a backup; (7) tenant-isolation tests pass on the restored system.
- **Completion evidence:** a drill record per profile in the section 8.4 format, with measured RPO and RTO, the exact revision and the topology. Current evidence: none for N12.
- **Existing foundation:** backup and restore scripts for PostgreSQL and object storage and a tombstone-reapply script in the repository's `infra/scripts/`; the tombstone-reapply job (`backend/jobs/tombstone_reapply.py`).
- **Status:** PLANNED. No N12 acceptance criterion has run.
- **External qualification:** owner decision D03 (backup policy); a supported production topology and live object storage for drills.

#### N13 — Accessibility backend support

- **Linear:** [HOM-154](https://linear.app/homayar/issue/HOM-154). **Matrix:** 6.6. **ID decision:** extends F72 (Persian RTL/English LTR, dates, digits, accessibility). F72's acceptance describes user-interface behavior; N13 is the backend part it relies on. X17 and F70 are related.
- **Purpose:** give the console, exports and API clients the codes and metadata they need to present errors and content accessibly in Persian and English.
- **Backend contract (backend only):** stable localized error codes; localized error metadata; field identifiers; semantic validation responses; RTL/LTR-safe exported-content metadata; language and locale metadata; accessible export-structure metadata; accessibility evidence records. This capability changes no frontend code.
- **Deployment modes:** S Y · P Y · H Y · O Y. Message catalogs must ship with the release.
- **Dependencies:** F72; X17; WP22-T01 (message catalog); F70 exports (WP24-T05).
- **Security and privacy:** localized messages never include secrets or other tenants' data; error details name fields, not submitted values.
- **Tenant boundary:** tenant-specific terminology, if offered, stays in that tenant's scope.
- **Audit:** catalog versions and accessibility evidence records are versioned; no per-request audit is needed.
- **Failure behavior:** a missing translation falls back to the stable code and the default-language message, never to an empty string.
- **Offline implications:** none; no external dependency.
- **Commercial and usage implications:** no separate charge; accessibility claims need evidence (GR37).
- **Repository candidates:** axe-core and Pa11y (section 10.13), recorded for future frontend verification only.
- **Acceptance criteria:** (1) every error response has a stable code, a Persian and an English message, and field identifiers where relevant; (2) `Accept-Language` or the user's locale selects the catalog; (3) exports carry language, direction and structure metadata; (4) a catalog coverage test fails on a code without messages; (5) accessibility evidence records name the revision tested.
- **Completion evidence:** catalog coverage and locale-selection tests and export-metadata tests, for the exact revision. Current evidence: none.
- **Existing foundation:** one error envelope with `code`, `message`, `request_id` and `details` (`backend/main.py`, `_error_payload`).
- **Status:** PLANNED. No N13 acceptance criterion has run.
- **External qualification:** user-facing accessibility verification (keyboard, screen reader) by later frontend work.

#### N14 — Browser/device support backend contract

- **Linear:** [HOM-155](https://linear.app/homayar/issue/HOM-155). **Matrix:** 6.1. **ID decision:** extends F12 (adversarial media validation; its acceptance requires supported codec fixtures to pass and forged, malformed, oversized or duration-expanded inputs to fail). V05 (browser voice call) is related.
- **Purpose:** publish as a versioned backend contract which browsers, devices, codecs, containers and sizes Homayar supports for recording and upload, and tell clients before they upload.
- **Backend contract (backend only):** versioned support policy; supported recording and upload capabilities; codec/container constraints; maximum file and stream characteristics; compatibility API or configuration; capability negotiation; rejection reasons; audit and version history. This capability changes no frontend code.
- **Deployment modes:** S Y · P Y · H Y · O Y. The policy must be served locally.
- **Dependencies:** F12 (media validation policy); V05; X01; F68 (versioned API).
- **Security and privacy:** the published policy never loosens server-side validation; capabilities a client declares are hints, and the server still validates the media (F12).
- **Tenant boundary:** one platform policy; a tenant restriction can only narrow it.
- **Audit:** policy versions and tenant restrictions, with author and date.
- **Failure behavior:** an unknown client gets conservative defaults and explicit rejection reasons; unsupported media is rejected before storage with a typed reason.
- **Offline implications:** none; served locally.
- **Commercial and usage implications:** the published policy is a customer-visible claim and must match evidence (F83, GR37); rejected uploads are not billed.
- **Repository candidates:** Playwright, Browserslist and MDN browser-compat-data (section 10.13), as verification references.
- **Acceptance criteria:** (1) the API returns the current policy version and its limits; (2) those limits equal the enforced media-validation policy; (3) negotiation answers accept or reject, with reasons, for the capabilities a client declares; (4) every rejection reason is a stable code; (5) policy history can be retrieved.
- **Completion evidence:** contract tests that compare published and enforced limits, for the exact revision. Current evidence: none.
- **Existing foundation:** `MediaValidationPolicy` limits and typed rejection reasons (`backend/media/validation.py`).
- **Status:** PLANNED. No N14 acceptance criterion has run.
- **External qualification:** a real browser and device test matrix before any browser or device is listed as supported.

#### N15 — Model and prompt rollback

- **Linear:** [HOM-156](https://linear.app/homayar/issue/HOM-156). **Matrix:** 6.5. **ID decision:** extends F20 (model/prompt registry and provider capabilities; its acceptance requires that a prior approved manifest can be restored without rewriting historical results). F81 (rollback manifest), K07 and GR11 are related.
- **Purpose:** keep every model and prompt version immutable and identifiable, promote in stages, and roll back quickly to the previous accepted version while knowing which results each version produced.
- **Backend contract:** immutable model versions; immutable prompt versions; schema versions; model artifacts; checksum; license metadata; qualification state; deployment compatibility; provider compatibility; tenant compatibility; active version; staged promotion; rollback; previous accepted version; affected-result lineage; reprocessing linkage (N05); audit; failure recovery; no mutable "latest" production identity. The existing Homayar model/provider registry is preferred if it can meet this contract.
- **Deployment modes:** S Y · P Y · H Y · O Y. In O every artifact is local and checksum-verified, and nothing is pulled from a registry at runtime.
- **Dependencies:** F20; F81 (WP13-T07); K07; GR11; GR32; X24 (rights inventory). Consumers include N05.
- **Security and privacy:** artifacts are checksum-verified before activation; license metadata is checked (GR32); promotion and rollback need authorized approval.
- **Tenant boundary:** active-version pointers can be set per tenant; one tenant's pin never changes another tenant's version.
- **Audit:** promotions, rollbacks, qualification changes and pointer switches.
- **Failure behavior:** a failed promotion leaves the previous version active; rollback is an atomic pointer switch; every result keeps the version that produced it.
- **Offline implications:** local artifacts only.
- **Commercial and usage implications:** a rollback can trigger reprocessing costs (N05), estimated before submission; model license terms can restrict commercial use (D17).
- **Repository candidates:** MLflow, BentoML and KServe (section 10.13); the existing registry is preferred.
- **Acceptance criteria:** (1) production runs reference an immutable version, never "latest"; (2) rollback restores the previous accepted version in one audited step; (3) historical results are not rewritten; (4) a lineage query lists the results a given version produced; (5) an unqualified or checksum-mismatched artifact cannot be activated; (6) qualification state has a single owning record.
- **Completion evidence:** registry tests for promotion, rollback, lineage and checksum failure, for the exact revision. Current evidence: none.
- **Existing foundation:** digest-keyed manifests with a status enum (`backend/providers/registry.py`, `ManifestStatus`); qualification is modeled separately in `backend/providers/hardware.py` and `backend/speech/benchmark.py` (`QualificationRecord`), which N15 unifies.
- **Status:** PLANNED. No N15 acceptance criterion has run.
- **External qualification:** owner license approvals (D17) and qualified hardware per model (D09).

## 7. User journeys and operational contracts

### 7.1 Recorded call to reviewed outcome

1. Upload or ingest recording with tenant, source and idempotency key.
2. Validate media and limits before accepting work; report terminal versus retryable refusal honestly.
3. Persist accepted job, quota reservation and dispatch intent atomically.
4. Select qualified models under the current policy; retain the attempt's policy snapshot.
5. Process channels, STT, diarization/alignment and analysis with durable checkpoints and fenced writes.
6. Show available stages, evidence, missing capabilities and partial outcomes.
7. Allow authorized transcript/speaker correction and supervisor review; revisions retain lineage.
8. Draft permitted actions; approve, execute and track actual results in Odoo.
9. Settle usage once under the applicable commercial policy; enforce retention and legal hold.

### 7.2 AI-assisted configuration

The user may say: “Keep audio in our office, analyze redacted text through our Gateway, and ask me before creating a sales follow-up.” The assistant must identify the current actor and tenant, inspect allowed capabilities, ask only for missing required facts, draft a typed configuration, show the change and estimated impact, validate dependencies, run a sandbox test, and request the approval required by policy. Apply using an expected configuration revision, audit the change and provide rollback where technically possible.

Speech recognition ambiguity around recipients, dates, money, deletion or policy changes requires clarification before applying that material change. Voice confirmation uses the same authenticated approval model as other channels; recognizing a voice is not sufficient authorization.

Configuration states: `draft -> validated -> simulated -> awaiting_approval -> approved -> applying -> applied`, with explicit `rejected`, `expired`, `conflict`, `failed`, and `rolled_back` outcomes. These are proposed contracts, not claims about current API enums.

### 7.3 Inbound/outbound call and human transfer

Authenticate the source where possible, establish the applicable recording/contact notice, load only authorized knowledge, enforce budget and tool permissions, handle interruption and silence, and transfer to a human when requested or necessary. If transfer fails, give a truthful next step. A call ending is not proof that a business task completed. Persist disposition, permitted summary, action approvals and actual outcomes.

Public outbound campaigns additionally require contact eligibility, campaign limits, time windows, opt-out/suppression and region-specific rules. New-prospect sales is in the vision but not exempt from these controls.

### 7.4 Demo/RFP to customer package

Collect organization size, use cases, languages, deployment/network restrictions, PBX, Odoo version/location, channels, expected concurrency, retention, support and budget preferences. Generate a reviewable RFP with assumptions and unanswered decisions. Record it in the sales Odoo and send it to the configured demo-request address through an audited delivery path.

Produce a proposal and package manifest only from qualified capabilities. A 30-minute demo/trial experience is a target where the selected plan enables it; exact allowance, expiry and conversion rules require the commercial policy. Do not confuse demo duration with an unconditional free credit entitlement.

### 7.5 Support and offline operation

Allow the user to preview the categories and contents of diagnostic data, remove optional material, choose encryption/recipient and attach to a ticket. Bundle version, component health, redacted settings, selected error traces, correlation IDs and integrity metadata. Never package credentials, unrelated tenants, full recordings or transcripts by default. Offline mode exports a local file; it cannot claim remote delivery.

### 7.6 Retention state semantics

| State/policy | Meaning | Transition requirement |
|---|---|---|
| FINITE | Delete eligible content after a configured duration/anchor | Valid days, timezone/anchor, hold and processing checks |
| INDEFINITE | No scheduled expiry | Explicit policy; authorized manual deletion rules remain separate |
| KEEP_FOR_NOW | Deliberately unresolved future duration with review ownership | Must not silently become zero-day or indefinite default |
| RETIRED | Archived, restricted from ordinary edits/deletion | Explicit archive lock, retrieval policy and defined authorized release process |
| LEGAL_HOLD | Preservation restriction over otherwise applicable lifecycle | Authorized reason, review, release and audit; no automatic release on review date |
| TOMBSTONED | Access denied; physical purge may still be pending | Durable cleanup, retry/reconciliation and restore reapply |

These target semantics must be mapped through an ADR to existing enums such as LEGACY_UNRESOLVED without silently changing their historical meaning. “Cannot be deleted” must identify whether it means application policy, storage immutability, or a regulated retention mechanism; no claim of protection against physical destruction.

### 7.7 Commercial event semantics

Persist raw usage evidence and a versioned pricing interpretation separately. Distinguish reserved, committed, released and adjusted events; distinguish customer charge from upstream provider cost. A BYOK customer may still pay platform or support fees if the agreed plan says so. Empty transcript, partial output, cancellation, replay and failure need explicit rules. Do not retroactively apply new rates without an approved adjustment policy.

## 8. Definition of Done and release gates

### 8.1 Required feature record

Every feature record must contain: stable ID; owner; target release; actor/use case; mode; provider/model/version; prerequisites; data classes/destinations; permission and license rules; settings; happy path; denial/failure/recovery behavior; observable output; acceptance tests; evidence links; known gaps; rollback; documentation; commercial treatment; and status per supported configuration.

Proposed machine-readable companions are `capabilities.yaml`, versioned `policies/`, and `ACCEPTANCE.md` or equivalent existing registries. **This task creates AppVision.md only.** Those filenames are planned enforcement artifacts, not claims that files or runtime enforcement already exist.

```yaml
# Illustrative schema only; not an active configuration or granted entitlement.
id: V03
title: Outbound AI calls
delivery_status: planned
target_release: owner_assignment_required
enabled_by_default: false
deployment_profiles: [saas, on_prem_connected, hybrid]
offline_variant: internal_extension_only
requires: [voice_runtime, contact_policy, approval_service, usage_ledger]
provider_choices: catalog_references_required
permissions: [outbound_call_execute]
approval_policy: tenant_policy_required
data_destinations: explicitly_approved_only
acceptance_evidence: []
```

### 8.2 Feature completion checklist

- [ ] Exact behavior, boundaries, supported mode and dependencies documented.
- [ ] Backend authorization and entitlement checks cover every entry point.
- [ ] Real adapter and model artifact exist for the claimed configuration.
- [ ] Functional, denial, cross-tenant, failure/retry and concurrency tests pass where relevant.
- [ ] External side effects and billing are idempotent and auditable.
- [ ] Privacy, retention, exports and deletion cover derived data as well as primary records.
- [ ] UI accurately exposes status, errors, limitations and approval requirements.
- [ ] Persian/English and accessibility checks pass where user-facing.
- [ ] Performance measured on the advertised workload/hardware; thresholds approved before promotion.
- [ ] Code/model/voice/data licenses and redistribution obligations recorded.
- [ ] Clean-checkout CI and relevant migration/build checks pass on the final revision.
- [ ] Required live or offline tests pass for the advertised mode; skips/blockers remain explicit.
- [ ] Runbook, API/configuration reference and support behavior updated.
- [ ] Release artifact, evidence revision and claim register agree.

### 8.3 Release gates

| Gate | Required proof | Failure disposition |
|---|---|---|
| RG01 | **Source/CI:** Exact source/image/model revisions and clean-build CI | Block affected release |
| RG02 | **Security/isolation:** Secret containment, permissions, tenant and network tests | Block affected data handling/deployment |
| RG03 | **AI quality:** Representative Persian/English slice evaluation and approved thresholds | Do not qualify that model/use case |
| RG04 | **Reliability:** Queue/storage/worker failure recovery and no duplicate effects | Block affected workflow |
| RG05 | **Privacy:** Redaction limits, reveal audit, deletion/hold/restore tests | Block affected privacy claims |
| RG06 | **Commercial:** Reproducible metering/settlement/license behavior | Block paid use of affected plan |
| RG07 | **Integration:** Real authorized PBX/provider/channel/Odoo tests | Mark that connector unqualified |
| RG08 | **Offline:** No public egress, preloaded artifacts, local license/auth, offline install/upgrade | Do not market that profile as offline |
| RG09 | **UX/operations:** Real backend journey, accessibility, diagnosis and recovery | Document and block material journey failures |
| RG10 | **Publication:** Correct feature/mode/price claims and known limitations | Correct claims before publication |

Live S3, gateway contract, carrier access, provider credentials, evaluation data, remote CI and independent security assessment may block different configurations independently. Do not mark the entire product done because a local suite passes, or block an unrelated validated configuration without explaining the dependency.

### 8.4 Evidence format

Record command/test ID, UTC time, environment, application role, database/broker/model versions, exact source SHA or artifact digest, pass/fail/skip totals and reasons, live-versus-fixture distinction, limitations, and artifact/log location. Evidence must not expose secrets or personal data. Preserve historical failures and fixes without relabeling an old run as new validation.

## 9. Decisions, delivery sequencing, and handoff

### 9.1 Owner decisions still requiring exact values

| ID | Decision | Already fixed | Still needed |
|---|---|---|---|
| D01 | Release boundaries | All capabilities retained in vision | Exact V1/V2/future assignment for expanded scope |
| D02 | Retention | All requested modes supported as targets | Default mode, duration, anchor, retired-release authority |
| D03 | Backup policy | Separate from primary retention | Duration, location, encryption and restore objectives |
| D04 | Charging | Configurable sales policy | Empty/partial/failure/replay/refund rules and price book |
| D05 | Trial/demo | Guided demo and RFP desired | Allowance, expiry, verification, conversion and abuse limits |
| D06 | Outbound sales | Existing customers and new prospects included | Launch regions, contact sources, suppression and campaign limits |
| D07 | Gateway | Homayar AI API Gateway is primary | Repository/contract, auth, deployment and relation to historical Padyar naming |
| D08 | Odoo | Community or customer Odoo; only CRM target | Supported major versions, modules, company mapping and sync authority |
| D09 | Models/voices | Multiple options retained | Approved Persian benchmarks, voices and hardware profiles |
| D10 | Licensing | SaaS/on-prem/offline and modular plans | Activation, expiry/grace, device binding, transfer and revocation policy |
| D11 | Autonomy | Suggest-and-approve default | Which low-risk actions qualify for scoped autonomy and two-person approval |
| D12 | Identity | Role/record/field scope required | Role matrix, organization model, federation and channel verification methods |
| D13 | Operations | Logs, diagnostics and optional paid support | Log retention, support bundle recipient, SLA and remote-access policy |
| D14 | Connectors | All requested families stay listed | Launch priority, vendor licenses and live test resources |
| D15 | Storage | Local plus S3-compatible targets | Qualified live targets, immutability profile and region |
| D16 | Standards | Evidence-based governance | Customer-required standards/control versions and assessment scope |
| D17 | Technology licenses | Check code and model separately | Approval of any copyleft, fair-source or commercial embedding obligations |
| D18 | Existing code recovery | Preserve originals and backups | Review unreconciled website overlay and differing historical product files |

### 9.2 Proposed sequencing, not a new scope approval

1. Reconcile the current integrated source, latest CI, reported work and preservation inventory.
2. Finish a qualified post-call path: real speech/diarization, representative evaluation, evidence review and recovery.
3. Make governance, capabilities, BYOK, Odoo contracts and cost treatment explicit.
4. Deliver the guided customer journey, RFP and a truthful pilot profile.
5. Add live voice and internal PBX/browser paths, then qualify public outbound/channel integrations.
6. Add durable cross-channel actions, advanced configuration assistance and customer-specific package manifests.
7. Qualify each on-prem/offline/HA offering separately and publish only matching claims.

Existing baseline release requirements are retained in section 11. This sequence does not silently defer a previously required security, privacy or release gate.

### 9.3 Future coding session handoff rule

Read AppVision, current package/task records and applicable ADRs. Verify branch/HEAD and working tree before implementation. Select a bounded feature and explicitly record owned files, dependencies and acceptance criteria. Use one coding agent unless delegation is explicitly authorized. Preserve others' work, original checkouts and backups. Do not install every catalog option, rotate credentials, publish, or rewrite architecture merely because this document lists a capability. Report exact changes, evidence, gaps and next action; update status only for the tested revision and mode.

### 9.4 Conflict resolution and change control

Explicit owner direction overrides historical planning choices. Preserve historical records and add a supersession note rather than rewriting old evidence. Operational safety, tenant isolation and current legal/contractual constraints still apply. Architectural changes require an ADR; feature or policy changes require impact analysis across modes, licenses, tests, documentation and customer builds. No silent removal of alternatives from the catalog.

## 10. Technology and integration catalog

This catalog preserves multiple choices rather than committing to a single vendor. Entries are candidates from the preceding research, not installed dependencies or tested Homayar adapters. Links are primary repository/documentation references. Project status and license observations are a dated research snapshot and must be rechecked for the exact selected release before procurement, redistribution or implementation.

**Integration types:** LIB = application dependency; SVC = separately deployed service; MODEL = versioned model/voice artifact; API = external/vendor integration; CUSTOM = Homayar-owned implementation. **Execution:** ALL = cloud/on-prem/hybrid and potentially offline with local dependencies; CONNECTED = external network required for that operation; CONDITIONAL = exact version, language, license, hardware or vendor contract must be qualified. ALL is a feasibility target, not a tested compatibility badge.

Open-source code does not make hosting, GPU use, telephone numbers or API calls free. GPL/AGPL are not blanket bans on commercial use; their applicable obligations must be reviewed. Fair-source/custom licenses and enterprise components must not be labeled unrestricted open source. Model weights, datasets, voice samples and pretrained voices have separate terms.

### 10.1 Live voice agent orchestration — V02–V06

| Candidate | Type / execution | Role and limitation |
|---|---|---|
| [LiveKit Agents](https://github.com/livekit/agents) | LIB / ALL conditional | Apache-2.0 framework for voice agents; select local components for offline |
| [Pipecat](https://github.com/pipecat-ai/pipecat) | LIB / ALL conditional | BSD-licensed composable voice pipelines; local provider support must be wired |
| [jambonz feature server](https://github.com/jambonz/jambonz-feature-server) | SVC / ALL conditional | MIT telephony application layer with additional SIP/media infrastructure |
| [Bolna](https://github.com/bolna-ai/bolna) | LIB/SVC / CONDITIONAL | MIT orchestration core; hosted API/UI are separate; sample setup uses external services |
| [Vapi](https://docs.vapi.ai/quickstart/introduction) | API / CONNECTED | Commercial managed voice-agent alternative |
| [Retell AI](https://docs.retellai.com/general/introduction) | API / CONNECTED | Commercial managed voice-agent alternative |

LiveKit's [turn-detection model license](https://github.com/livekit/agents/blob/main/MODEL_LICENSE) is distinct from its code license. A cloud-default plugin configuration is not an offline installation.

### 10.2 PBX, SIP and browser transport — V01/V04/V05/V07/P01–P06

| Candidate | Type / execution | Role and limitation |
|---|---|---|
| [Asterisk](https://github.com/asterisk/asterisk) | SVC / ALL internal | PBX/media engine; public calls need a carrier |
| [FreePBX framework](https://github.com/FreePBX/framework) | SVC / ALL internal | Asterisk administration; commercial modules have separate terms |
| [Issabel](https://github.com/IssabelFoundation/issabel) | SVC / ALL internal | Customer PBX integration target; component/version qualification |
| [FreeSWITCH](https://github.com/signalwire/freeswitch) | SVC / ALL internal | Media/telephony engine; core MPL and component notices must be respected |
| [FusionPBX](https://github.com/fusionpbx/fusionpbx) | SVC / ALL internal | Management layer for FreeSWITCH |
| [Kamailio](https://github.com/kamailio/kamailio) | SVC / ALL internal | SIP routing; complementary to media and AI engines |
| [OpenSIPS](https://github.com/OpenSIPS/opensips) | SVC / ALL internal | Alternative SIP routing infrastructure |
| [LiveKit Server](https://github.com/livekit/livekit) | SVC / ALL | Self-hosted real-time media server |
| [LiveKit SIP](https://github.com/livekit/sip) | SVC / ALL internal | Bridge SIP and WebRTC |
| [Janus](https://github.com/meetecho/janus-gateway) | SVC / ALL | WebRTC gateway alternative |
| [mediasoup](https://github.com/versatica/mediasoup) | LIB / ALL | Custom media infrastructure; requires application implementation |
| [coturn](https://github.com/coturn/coturn) | SVC / ALL | TURN/STUN; local server for internal deployments |
| [Jitsi Meet](https://github.com/jitsi/jitsi-meet) | SVC / ALL conditional | Optional self-hosted conferencing; not an AI agent by itself |
| [3CX Call Control](https://www.3cx.com/docs/call-control-api/) | API / CONDITIONAL | Vendor edition, license and API restrictions apply |
| [Cisco JTAPI](https://developer.cisco.com/site/jtapi/) | API / CONDITIONAL | Supported Cisco call-control integration |
| Avaya vendor SDK/API | API / CONDITIONAL | Exact product/version and official contract required; no generic ready adapter verified |
| [Teams Direct Routing](https://learn.microsoft.com/en-us/microsoftteams/direct-routing-plan) | API / CONNECTED | Microsoft service and SBC dependencies; not air-gapped |
| [Twilio Python SDK](https://github.com/twilio/twilio-python) | LIB/API / CONNECTED | Free SDK for paid provider capabilities |
| [Telnyx Python SDK](https://github.com/team-telnyx/telnyx-python) | LIB/API / CONNECTED | Alternative provider SDK; number/region/service access varies |
| [SignalWire Python SDK](https://github.com/signalwire/signalwire-python) | LIB/API / CONNECTED | Alternative telephony service integration |

Generic SIP trunks need a tested profile for authentication, transport, codecs, DTMF, caller identity, recording, transfer and hangup. Support for one operation does not imply all operations work.

### 10.3 Speech recognition, alignment and voice activity — WP09/WP10

| Candidate | Type / execution | Role and limitation |
|---|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | LIB/MODEL / ALL | MIT runtime; qualify exact Whisper model and hardware |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | LIB/MODEL / ALL | Local inference on varied hardware |
| [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) | MODEL / ALL | Declares Persian ASR support; evaluate telephone audio and streaming behavior |
| Qwen3 Forced Aligner, same project | MODEL / CONDITIONAL | Persian absent from the declared aligner language set reviewed; do not assume parity with ASR |
| [Vosk](https://github.com/alphacep/vosk-api) | LIB/MODEL / ALL | Lightweight streaming/offline option; [model-specific licenses](https://alphacephei.com/vosk/models) |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | LIB/MODEL / ALL | ONNX speech components; language and rights depend on selected model |
| [Argmax OSS Swift / WhisperKit](https://github.com/argmaxinc/argmax-oss-swift) | LIB/MODEL / ALL on supported devices | Apple-device inference option |
| [WhisperLive](https://github.com/collabora/WhisperLive) | SVC / ALL conditional | Streaming transcription infrastructure; latency must be measured |
| [pyannote.audio](https://github.com/pyannote/pyannote-audio) | LIB / ALL | Diarization library; models separately licensed |
| [Community-1 diarization](https://huggingface.co/pyannote/speaker-diarization-community-1) | MODEL / ALL after acquisition | CC-BY-4.0 model, initial access conditions; preserve PBX channels before downmix |
| [WhisperX](https://github.com/m-bain/whisperX) | LIB/MODEL / ALL conditional | Alignment/diarization pipeline; language-specific aligner qualification |
| [Silero VAD](https://github.com/snakers4/silero-vad) | LIB/MODEL / ALL | Speech activity detection, not full semantic turn detection |
| [RNNoise](https://github.com/xiph/rnnoise) | LIB / ALL | Noise reduction; evaluate effects on recognition and evidence |

Channel identity mapping, overlap attribution, unknown speakers and evidence revision remain Homayar responsibilities. None of these components alone provides reliable employee identity or business role.

### 10.4 TTS, voices and native audio models

| Candidate | Type / execution | Role and limitation |
|---|---|---|
| [Piper](https://github.com/OHF-Voice/piper1-gpl) | LIB/MODEL / ALL | GPL code; Persian voices exist; inspect each [voice license](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/VOICES.md) |
| [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) | MODEL / ALL conditional | Persian not in the official language list reviewed |
| [Chatterbox](https://github.com/resemble-ai/chatterbox) | LIB/MODEL / ALL conditional | Multilingual synthesis; Persian not in the reviewed official language list |
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | MODEL / ALL conditional | Small TTS model; language/voice qualification required |
| [CosyVoice](https://github.com/QwenAudio/CosyVoice) | LIB/MODEL / ALL conditional | Streaming/multilingual voice option; Persian not qualified here |
| [OpenVoice](https://github.com/myshell-ai/OpenVoice) | LIB/MODEL / ALL conditional | Voice adaptation; authorized voice provenance and dependencies required |
| [F5-TTS](https://github.com/SWivid/F5-TTS) | LIB/MODEL / CONDITIONAL | MIT code, published pretrained models CC-BY-NC; not a default commercial weight choice |
| [Azure Speech](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support) | API / CONNECTED | Paid speech service; check exact locale/capability and region |
| [ElevenLabs](https://elevenlabs.io/docs/overview/models) | API / CONNECTED | Persian announced for Eleven v3; do not generalize across model families |
| [Deepgram](https://developers.deepgram.com/docs/models-languages-overview) | API / CONNECTED | Additional speech-provider candidate; model/language compatibility must be checked |
| [Google Speech-to-Text](https://docs.cloud.google.com/speech-to-text/docs/speech-to-text-supported-languages) | API / CONNECTED | Additional cloud ASR candidate with model/region/language constraints |
| [Moshi](https://github.com/kyutai-labs/moshi) | LIB/MODEL / ALL conditional | Full-duplex research/product candidate; model weights CC-BY-4.0 |
| [PersonaPlex](https://github.com/NVIDIA/personaplex) | LIB/MODEL / CONDITIONAL | MIT code, NVIDIA Open Model weights; hardware and Persian qualification outstanding |
| [Qwen3-Omni](https://github.com/QwenLM/Qwen3-Omni) | MODEL / ALL conditional | Native multimodal/audio option; no Persian readiness claim here |
| [Step-Audio2](https://github.com/stepfun-ai/Step-Audio2) | MODEL / ALL conditional | Mini variants described as Apache-2.0; exact model qualification required |
| [Step-Audio-EditX](https://github.com/stepfun-ai/Step-Audio-EditX) | MODEL / CONDITIONAL | Optional speech generation/editing research candidate; qualify license/language before use |
| [Ultravox](https://github.com/fixie-ai/ultravox) | LIB/MODEL / CONDITIONAL | Direct audio understanding with text output; separate TTS and base-model terms apply |

Native speech-to-speech options are alternatives to the STT/LLM/TTS pipeline, not mandatory replacements. Preserve inspectable evidence and action authorization whichever runtime is selected. The older Step-Audio repository directs users to successor projects and is not the preferred new dependency.

### 10.5 Local models, Gateway and secrets — WP08/K01–K07

| Candidate | Type / execution | Role and limitation |
|---|---|---|
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | LIB/SVC / ALL | Quantized local inference; model terms separate |
| [vLLM](https://github.com/vllm-project/vllm) | SVC / ALL | GPU model serving and batching |
| [Ollama](https://github.com/ollama/ollama) | SVC / ALL conditional | Local model management; disable external/cloud paths for O |
| [MLX-LM](https://github.com/ml-explore/mlx-lm) | LIB / ALL on supported devices | Apple Silicon model runtime |
| [LocalAI](https://github.com/mudler/LocalAI) | SVC / ALL conditional | Local API over multiple backends |
| [Qwen3](https://github.com/QwenLM/Qwen3) | MODEL / ALL conditional | Candidate family; qualify exact checkpoint, rights and Persian task performance |
| Existing Homayar AI API Gateway | SVC/API / CONDITIONAL | Primary owner-selected integration; repository and contract not inspected in this document |
| [LiteLLM](https://github.com/BerriAI/litellm) | LIB/SVC / ALL conditional | Optional unified provider interface; enterprise features separate |
| [Bifrost](https://github.com/maximhq/bifrost) | SVC / ALL conditional | Alternative gateway; Apache core and separate enterprise offerings |
| [OpenBao](https://github.com/openbao/openbao) | SVC / ALL | Secret management; local unseal/backup policy required for O |

Choose a runtime per qualified profile; do not install every runtime by default. All settings use secret references, never actual secrets in prompts, config exports or evidence.

### 10.6 Retrieval and knowledge — WP17

| Candidate | Type / execution | Role |
|---|---|---|
| [Haystack](https://github.com/deepset-ai/haystack) | LIB / ALL conditional | Retrieval and grounded-response pipelines |
| [LlamaIndex](https://github.com/run-llama/llama_index) | LIB / ALL conditional | Indexing and data connectors; cloud extras separate |
| [pgvector](https://github.com/pgvector/pgvector) | DB extension / ALL | Preferred low-change candidate beside existing PostgreSQL |
| [Qdrant](https://github.com/qdrant/qdrant) | SVC / ALL | Separate vector search alternative |
| [Milvus](https://github.com/milvus-io/milvus) | SVC / ALL | Alternative vector infrastructure |
| [OpenSearch](https://github.com/opensearch-project/OpenSearch) | SVC / ALL | Lexical and hybrid search alternative |
| [Docling](https://github.com/docling-project/docling) | LIB/MODEL / ALL conditional | Document extraction; model artifacts/terms separate |
| [Qwen3-Embedding](https://github.com/QwenLM/Qwen3-Embedding) | MODEL / ALL conditional | Embedding/reranking candidate for multilingual evaluation |

Enforce document/field permissions before and during retrieval; filter citations and cached results too. Deletion and access revocation must propagate to all projections.

### 10.7 Workflow and durable execution — WP06/WP24/X03–X05

| Candidate | Type / execution | Role and limitation |
|---|---|---|
| [React Flow / xyflow](https://github.com/xyflow/xyflow) | LIB / ALL | MIT visual graph editor; not an execution engine |
| [LangGraph](https://github.com/langchain-ai/langgraph) | LIB / ALL conditional | Stateful agent/workflow construction and human intervention |
| [Langflow](https://github.com/langflow-ai/langflow) | SVC / ALL conditional | Visual AI workflow alternative |
| [Activepieces](https://github.com/activepieces/activepieces) | SVC / ALL conditional | Automation; MIT core and separate enterprise code |
| [n8n](https://github.com/n8n-io/n8n) | SVC / CONDITIONAL | Internal automation candidate; commercial embedding/resale requires license review |
| [Dify](https://github.com/langgenius/dify) | SVC / CONDITIONAL | AI app platform; modified license restricts multi-tenant operation and some branding changes |
| [Flowise](https://github.com/FlowiseAI/Flowise) | SVC / CONDITIONAL | Archived repository in reviewed snapshot; enterprise exceptions; not new default |
| [Celery](https://github.com/celery/celery) | LIB/SVC / ALL | Preserve existing Homayar processing foundation |
| [Temporal](https://github.com/temporalio/temporal) | SVC / ALL | Alternative for durable long-running workflows; architecture decision required |
| [RabbitMQ](https://github.com/rabbitmq/rabbitmq-server) | SVC / ALL | Broker option, not a replacement for application idempotency |

Do not run two orchestration systems for the same authority by accident. Define which owns timers, retries, approval waits, cancellation and outcomes.

### 10.8 Odoo, calendars, communication and social

| Candidate | Type / execution | Role and limitation |
|---|---|---|
| [Odoo Community](https://github.com/odoo/odoo) | SVC/API / ALL if local | Selected CRM; choose supported major version and modules |
| [OCA Connector](https://github.com/OCA/connector) | LIB/addon / ALL if local | Odoo-side integration framework |
| [OCA Queue](https://github.com/OCA/queue) | Addon / ALL if local | Odoo-side jobs; not authority for Homayar processing |
| [OCA Helpdesk](https://github.com/OCA/helpdesk) | Addon / ALL if local | Preferred CRM-aligned helpdesk option |
| [Radicale](https://github.com/Kozea/Radicale) | SVC / ALL | Local CalDAV/CardDAV alternative |
| [Easy!Appointments](https://github.com/alextselegidis/easyappointments) | SVC / ALL conditional | Self-hosted booking alternative |
| [Cal.diy](https://github.com/calcom/cal.diy) | SVC / CONDITIONAL | Scheduling alternative; inspect selected release/license and external dependencies |
| [Google API client](https://github.com/googleapis/google-api-python-client) | LIB/API / CONNECTED | Gmail/Calendar and permitted Google APIs |
| [Microsoft Graph SDK](https://github.com/microsoftgraph/msgraph-sdk-python) | LIB/API / CONNECTED | Microsoft calendar/mail integrations |
| SMTP/IMAP adapter | CUSTOM / ALL if internal | Internal or external mail; secure auth and thread handling |
| [Apprise](https://github.com/caronc/apprise) | LIB / destination-dependent | Multi-destination notifications; not an approval engine |
| [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) | LIB/API / CONNECTED | Telegram bot connector; platform initiation rules apply |
| [Bale API](https://docs.bale.ai) | API / CONNECTED | Official integration; test protocol differences |
| [Eitaayar](https://eitaayar.ir/) | API / CONNECTED conditional | Only confirmed operations; private conversation not presumed |
| [WhatsApp Business Platform](https://whatsappbusiness.com/products/business-platform/) | API / CONNECTED | Official commercial messaging path |
| Asanak | API / CONNECTED conditional | Project-selected SMS provider; actual API contract required |
| [Chatwoot](https://github.com/chatwoot/chatwoot) | SVC / local UI, connected channels | Shared inbox; MIT core outside enterprise components |
| [Postiz](https://github.com/gitroomhq/postiz-app) | SVC / CONNECTED for publication | AGPL social scheduling; supported official connectors only |
| [Zammad](https://github.com/zammad/zammad) | SVC / ALL for internal ticketing | Alternative helpdesk, not a replacement CRM |

OCA modules have individual manifests/licenses and must match the Odoo major version. Public channel delivery cannot work across a strict air gap. A local outbox may retain approved pending work, but stale approvals and current policy must be rechecked before later delivery.

### 10.9 Identity, permissions, guardrails and licensing

| Candidate | Type / execution | Role and limitation |
|---|---|---|
| [Keycloak](https://github.com/keycloak/keycloak) | SVC / ALL | Enterprise identity/federation alternative |
| [Authentik](https://github.com/goauthentik/authentik) | SVC / ALL conditional | MIT core with separate enterprise components |
| [OPA](https://github.com/open-policy-agent/opa) | LIB/SVC / ALL | General deterministic policy evaluation |
| [OpenFGA](https://github.com/openfga/openfga) | SVC / ALL | Relationship-based authorization |
| [Casbin](https://github.com/apache/casbin) | LIB / ALL | Authorization library alternative |
| [Cerbos](https://github.com/cerbos/cerbos) | SVC / ALL conditional | Resource/action policy decision service |
| [NeMo Guardrails](https://github.com/NVIDIA-NeMo/Guardrails) | LIB / ALL conditional | Conversation rails with selected local/remote models |
| [Presidio](https://github.com/data-privacy-stack/presidio) | LIB / ALL conditional | PII tooling; Persian recognizers need evaluation |
| [flagd](https://github.com/open-feature/flagd) | SVC/LIB / ALL | Feature evaluation, not license enforcement by itself |
| [Unleash](https://github.com/Unleash/unleash) | SVC / ALL conditional | AGPL feature management in reviewed version; enterprise features separate |
| [Keygen](https://github.com/keygen-sh/keygen-api) | SVC/API / CONDITIONAL | Fair Core license; offline license workflow available, embedding terms require review |
| [cryptography](https://github.com/pyca/cryptography) | LIB / ALL | Cryptographic building blocks for signed licenses and encrypted bundles |

Existing Homayar auth, RLS and privacy controls remain authoritative until an approved migration says otherwise. Do not install every policy engine or assume a language-model prompt enforces access rights. Offline licenses cannot receive immediate remote revocation without a permitted update path; document bounded validity and renewal policy.

### 10.10 Billing, forms, storage and support

| Candidate | Type / execution | Role and limitation |
|---|---|---|
| Existing Homayar usage ledger | CUSTOM / ALL | Authoritative idempotent usage events; preserve provenance |
| [Lago](https://github.com/getlago/lago) | SVC / ALL for local billing | AGPL metering/billing alternative; online payment gateways separate |
| [Kill Bill](https://github.com/killbill/killbill) | SVC / ALL for local billing | Subscription/billing alternative |
| Odoo financial integration | API/addon / CONDITIONAL | Exact edition/modules/localization determine available functionality |
| [JSON Forms](https://github.com/eclipsesource/jsonforms) | LIB / ALL | Schema-driven settings, demo and RFP questionnaires |
| Existing local/S3 storage adapters | CUSTOM / ALL conditional | Preserve tested lifecycle and contract behavior |
| [SeaweedFS](https://github.com/seaweedfs/seaweedfs) | SVC / ALL | S3-compatible storage; qualify Object Lock/legal hold behavior |
| [Ceph](https://github.com/ceph/ceph) | SVC / ALL | Distributed storage alternative for larger installations |
| [MinIO](https://github.com/minio/minio) | SVC / CONDITIONAL | Archived public repository in reviewed snapshot; retain compatibility candidate, not new default |
| [Vazirmatn](https://github.com/rastikerdar/vazirmatn) | Asset / ALL | Local Persian/Arabic font; ship attribution/license |

Feature-selected packaging, RFP interpretation, audited reveals, retired-state enforcement, diagnostic export and precise plan rules require Homayar implementation even when these components are used.

### 10.11 Observability, evaluation and distribution

| Candidate | Type / execution | Role and limitation |
|---|---|---|
| [OpenTelemetry Collector](https://github.com/open-telemetry/opentelemetry-collector) | SVC / ALL | Trace/metric/log collection with local destinations |
| [Prometheus](https://github.com/prometheus/prometheus) | SVC / ALL | Metrics and alerting |
| [Grafana](https://github.com/grafana/grafana) | SVC / ALL | Dashboards; selected edition/license applies |
| [SigNoz](https://github.com/SigNoz/signoz) | SVC / ALL conditional | Observability alternative; edition boundaries matter |
| [Langfuse](https://github.com/langfuse/langfuse) | SVC / ALL conditional | AI tracing/evaluation; MIT core outside enterprise components; content collection controlled |
| [Label Studio](https://github.com/HumanSignal/label-studio) | SVC / ALL | Community annotation for consented evaluation data |
| [JiWER](https://github.com/jitsi/jiwer) | LIB / ALL | ASR error measurement |
| [pyannote.metrics](https://github.com/pyannote/pyannote-metrics) | LIB / ALL | Diarization evaluation |
| [Promptfoo](https://github.com/promptfoo/promptfoo) | Tool / ALL conditional | Prompt/security evaluation; remote-dependent plugins excluded for O |
| [Harbor](https://github.com/goharbor/harbor) | SVC / ALL | Private artifact/image registry |
| [K3s](https://github.com/k3s-io/k3s) | Infrastructure / ALL conditional | Optional cluster profile; not required for every SME |
| [Cosign](https://github.com/sigstore/cosign) | Tool / ALL with offline verification setup | Signed artifacts; prearrange trust material rather than assume online verification |
| [Syft](https://github.com/anchore/syft) | Tool / ALL | Software bill of materials |
| [Trivy](https://github.com/aquasecurity/trivy) | Tool / ALL with imported databases | Security scans; record offline database freshness |
| [pgBackRest](https://github.com/pgbackrest/pgbackrest) | Tool / ALL | PostgreSQL backup and restore |
| [Restic](https://github.com/restic/restic) | Tool / ALL | Encrypted file/artifact backups |
| [Patroni](https://github.com/patroni/patroni) | SVC / ALL conditional | PostgreSQL HA profile |
| [PgBouncer](https://github.com/pgbouncer/pgbouncer) | SVC / ALL | Connection pooling; tenant/session context and RLS must be tested |

### 10.12 Selection and availability policy

Keep a catalog entry for each alternative, with version, upstream URL, maintenance state, integration type, license findings, language/hardware prerequisites, offline dependencies, qualified operations and evidence. Select a default only after comparing the relevant acceptance workload. Prefer reuse of established Homayar infrastructure over replacement solely because another repository exists.

A strict open-source offline distribution must exclude cloud-only services, restricted pretrained weights and incompatible embedding terms from its shipped profile. It may retain those choices in the global catalog as conditional alternatives. Commercial gateway access or voice services can be offered in connected profiles without pretending they are offline or cost-free.

### 10.13 Approved backend capability candidates — N01–N15

The owner named these candidates for the approved backend capabilities in section 6.7. Candidates already in this catalog are not repeated: Celery and Temporal (10.7) for N05; Label Studio and Langfuse (10.11) for N06; Apprise (10.8) for N07; LiteLLM (10.5) for N08; Lago and Kill Bill (10.10) and flagd (10.9) for N09; OpenTelemetry Collector (10.11) for N10; pgBackRest and Restic (10.11) for N12. On 2026-09-30 each repository below was confirmed to exist publicly and not to be archived. Its license, edition boundaries and offline behavior were not reviewed in this update and must be checked for the exact release before selection (section 10.12).

| Candidate | Type / execution | Role and limitation |
|---|---|---|
| [dlt](https://github.com/dlt-hub/dlt) | LIB / ALL conditional | N02 import pipelines in Python; the owner's preferred external option after a native implementation |
| [Meltano](https://github.com/meltano/meltano) | LIB / ALL conditional | N02 alternative built on Singer connectors |
| [Airbyte](https://github.com/airbytehq/airbyte) | SVC / CONDITIONAL | N02 separate connector platform; only if connector scale justifies it |
| [OpenFGC](https://github.com/wso2/openfgc) | SVC / CONDITIONAL | N03 reference for fine-grained consent records; Homayar remains the enforcement authority |
| [Evidently](https://github.com/evidentlyai/evidently) | LIB / ALL conditional | N04 data and model quality reports; audio-specific quality logic stays native |
| [Great Expectations (GX Core)](https://github.com/fivetran/great_expectations) | LIB / ALL conditional | N04 tabular data validation; the repository is now under the `fivetran` organization |
| [Soda Core](https://github.com/sodadata/soda-core) | LIB / ALL conditional | N04 data-contract and quality checks |
| [Prefect](https://github.com/PrefectHQ/prefect) | SVC / CONDITIONAL | N05 workflow-orchestration alternative; not a replacement for the Celery/outbox authority |
| [Dagster](https://github.com/dagster-io/dagster) | SVC / CONDITIONAL | N05 asset-orchestration alternative; same limitation |
| [Argilla](https://github.com/argilla-io/argilla) | SVC / ALL conditional | N06 dataset curation and annotation for consented evaluation data |
| [Novu](https://github.com/novuhq/novu) | SVC / CONDITIONAL | N07 notification infrastructure; public channels need connectivity; not the preference authority |
| [Helicone](https://github.com/Helicone/helicone) | SVC / CONDITIONAL | N08 LLM usage and cost observability; the Homayar usage ledger stays authoritative |
| [Portkey Gateway](https://github.com/Portkey-AI/gateway) | SVC / CONDITIONAL | N08 alternative AI gateway; not the budget authority |
| [PostHog](https://github.com/PostHog/posthog) | SVC / CONDITIONAL | N10 product analytics; self-hosting and content controls must be qualified |
| [Plausible](https://github.com/plausible/analytics) | SVC / CONDITIONAL | N10 privacy-focused web analytics; fit for backend events must be qualified |
| [Semgrep](https://github.com/semgrep/semgrep) | LIB / ALL with local rules | N11 forbidden-path and code-pattern checks |
| [jscodeshift](https://github.com/facebook/jscodeshift) | LIB / ALL | N11 JavaScript codemods; frontend use is outside this backend scope |
| [ast-grep](https://github.com/ast-grep/ast-grep) | LIB / ALL | N11 structural code search and rewrite |
| [axe-core](https://github.com/dequelabs/axe-core) | LIB / ALL | N13 future frontend accessibility verification only |
| [Pa11y](https://github.com/pa11y/pa11y) | LIB / ALL conditional | N13 future frontend accessibility verification only |
| [Playwright](https://github.com/microsoft/playwright) | LIB / ALL with preloaded browsers | N14 browser verification of the support policy |
| [Browserslist](https://github.com/browserslist/browserslist) | LIB / ALL with dated data | N14 reference for browser-range definitions |
| [MDN browser-compat-data](https://github.com/mdn/browser-compat-data) | LIB / ALL as a dated snapshot | N14 reference for browser capability data |
| [MLflow](https://github.com/mlflow/mlflow) | SVC / ALL conditional | N15 model-registry alternative; the existing Homayar registry is preferred |
| [BentoML](https://github.com/bentoml/BentoML) | LIB/SVC / ALL conditional | N15 model packaging and serving alternative |
| [KServe](https://github.com/kserve/kserve) | SVC / CONDITIONAL | N15 model serving on Kubernetes; only with the optional cluster profile (F76, X21) |

## 11. Original 95-feature coverage and acceptance register

The following register preserves every ID, title, package, priority, delivery scope and recorded acceptance criterion from the local `docs/planning/backlog.json`. Historical scope descriptions are retained as history, not as a new restriction on the owner's expanded vision. The source's implementation-status strings are not promoted to current status.

**Current evidence status for every row: NEEDS_RECONCILIATION.** No existing feature was revalidated during documentation authoring. Exact acceptance wording is retained where present; missing acceptance is made explicit. Mode targets in this section are planning classifications and must be qualified per configuration.

### 11.1 Supersessions and scope qualifications

- **F66:** the old multi-CRM list is superseded by the owner's **Odoo-only CRM** direction. Other named CRMs remain historical alternatives, not current delivery commitments.
- **F64:** generic business ID/outcome mapping is retained as infrastructure, with Odoo as the selected CRM destination.
- **F40:** acoustic emotion/personality inference is preserved as a historical backlog item, **excluded from the recommended employee-scoring product scope**. Do not infer worker emotions/personality from voice. Use observable evidence-based criteria and human review.
- **F32:** voice embeddings, if ever pursued, need a separate identity/data-rights and threat-model decision; they cannot serve as sole authentication.
- **F39/F54/F56:** textual/customer signals or predictions must be labeled, validated and reviewable; never present them as known emotion, actual churn, actual conversion or measured NPS.
- **F67/F71/F88/F93:** historical integration lists remain in the register but are not promises that every connector is authorized, available or built. Qualify each independently.
- **F77:** hybrid deployment is now part of the selected product vision; individual release assignment remains open.
- **F95:** an assurance program or control mapping is not a certification claim.
- The old phrase “not authorized by initial coding kickoff” describes the historical kickoff. New owner selections in sections 1 and 6 supersede that phrase for vision scope, not automatically implementation/release authorization.

### 11.2 Baseline feature matrix

| ID | Original feature | WP | Priority | Historical delivery scope | Target modes / limitation |
|---|---|---|---|---|---|
| F01 | Release/source provenance | WP01 | P0 | v1 required | S/P/H/O conditional on local dependencies |
| F02 | CI, migrations and regression suites | WP01 | P0 | v1 required | S/P/H/O conditional on local dependencies |
| F03 | Secret containment and scanning | WP02 | P0 | v1 required | S/P/H/O conditional on local dependencies |
| F04 | Unified login, refresh, reset, verification | WP03 | P0 | v1 required | S/P/H/O conditional on local dependencies |
| F05 | Tenant isolation across DB, files, jobs, search | WP03 | P0 | v1 required | S/P/H/O conditional on local dependencies |
| F06 | Teams, supervisor and agent record permissions | WP03 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F07 | Enterprise OIDC/SAML federation and MFA | WP23 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F08 | SCIM automated provisioning | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F09 | Audit trail and tamper-evident export | WP20 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F10 | TLS, encryption, key rotation, restricted networks | WP02 | P0 | v1 required | S/P/H/O conditional on local dependencies |
| F11 | Customer-managed keys and IP/session policies | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F12 | Adversarial media validation | WP05 | P0 | v1 required | S/P/H/O conditional on local dependencies |
| F13 | Idempotent upload and ingestion ledger | WP05 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F14 | Private object storage lifecycle | WP05 | P0 | v1 required | S/P/H/O conditional on local dependencies |
| F15 | Retention, deletion, legal hold and subject export | WP07 | P0 | v1 required | S/P/H/O conditional on local dependencies |
| F16 | Text PII redaction and access policy | WP07 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F17 | Durable queue, leases, checkpoints and recovery | WP06 | P0 | v1 required | S/P/H/O conditional on local dependencies |
| F18 | Usage metering, quotas and plan limits | WP18 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F19 | Subscription invoicing and payment adapters | WP18 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F20 | Model/prompt registry and provider capabilities | WP08 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F21 | Tenant privacy/region routing and egress denial | WP08 | P0 | v1 required | S/P/H/O conditional on local dependencies |
| F22 | Provider retry, health and circuit breakers | WP08 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F23 | Honest stage states and mock exclusion | WP04 | P0 | v1 required | S/P/H/O conditional on local dependencies |
| F24 | Evidence/revision/provenance schema | WP04 | P0 | v1 required | S/P/H/O conditional on local dependencies |
| F25 | Channel-aware speaker attribution | WP09 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F26 | Audio normalization, VAD and quality checks | WP09 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F27 | Persian and English production STT | WP09 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F28 | Persistent GPU workers and bounded batching | WP09 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F29 | Code-switching, terminology and extra languages | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F30 | Mono and multiparty diarization | WP10 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F31 | Overlap detection and attribution | WP10 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F32 | Known-agent voice embeddings | — | P3 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F33 | Timestamp alignment and calibrated confidence | WP10 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F34 | Silence, talk ratio, overtalk and speaking rate | WP11 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F35 | Hold, transfer and call-state metrics | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F36 | Intent, topics, entities and resolution status | WP11 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F37 | Summaries, decisions and key moments | WP12 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F38 | Action items, owners, deadlines and commitments | WP24 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F39 | Persian sentiment journey and escalation signals | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F40 | Acoustic emotion or personality scoring | — | P3 | expansion; not authorized by initial coding kickoff | Excluded from recommended employee scoring scope |
| F41 | Versioned custom weighted QA scorecards | WP11 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F42 | Keyword, script and compliance evidence rules | WP11 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F43 | Automated coverage and review sampling | WP14 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F44 | Supervisor review and score override | WP14 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F45 | Transcript and turn/speaker correction | WP12 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F46 | Calibration and appeal workflow | WP14 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F47 | Basic coaching assignments and progress | WP14 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F48 | Personalized exercises and skill graph | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F49 | Complaint, product and operational issue trends | WP15 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F50 | Repeat contact and observed FCR | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F51 | Versioned business graph projection | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F52 | Recurring root-cause and incident relationships | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F53 | Customer journey, account 360 and risk graph | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F54 | Cancellation/VIP-risk signals | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F55 | Sales objections, intent and opportunities | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F56 | Churn/conversion/NPS prediction | — | P3 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F57 | Scoped lexical and semantic search | WP17 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F58 | Evidence-backed analytical Copilot | WP17 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F59 | Post-call/digest alerts and review queues | WP24 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F60 | Approval-based CRM/ticket/coaching actions | WP24 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F61 | Role-specific dashboards and comparisons | WP15 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F62 | Executive daily/weekly briefs | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F63 | Asterisk/FreePBX/Issabel recording adapter | WP16 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F64 | Generic CRM/ticket ID and outcome mapping | WP16 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F65 | 3CX/Cisco/Avaya/Genesys/Grandstream adapters | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H conditional; O only for verified internal equivalents |
| F66 | Salesforce/Dynamics/HubSpot/Zoho/Odoo/IR CRM | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H conditional; O only for verified internal equivalents |
| F67 | Jira/ServiceNow/Zendesk/Freshdesk adapters | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H conditional; O only for verified internal equivalents |
| F68 | Versioned REST, scoped API keys and SDK | WP16 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F69 | Signed webhooks and delivery ledger | WP16 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F70 | PDF, CSV, JSON and transcript exports | WP24 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F71 | Power BI/Metabase/Tableau connectors | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F72 | Persian RTL/English LTR, dates, digits, accessibility | WP22 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F73 | Mobile supervisor access | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F74 | Air-gapped local STT/LLM/embed package | WP19 | P1 | v1 required | O/P primary; supports packaging across profiles |
| F75 | Offline license, upgrade and secure support bundle | WP19 | P1 | v1 required | O/P primary; supports packaging across profiles |
| F76 | Kubernetes, HA and very large scale | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F77 | Hybrid local data / optional cloud management | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F78 | Metrics, traces, cost, queue/GPU health and SLOs | WP20 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F79 | Encrypted backups and restore drills | WP20 | P0 | v1 required | S/P/H/O conditional on local dependencies |
| F80 | Persian gold corpus and slice benchmarks | WP13 | P0 | v1 required | S/P/H/O conditional on local dependencies |
| F81 | AI regression and model/prompt promotion gates | WP13 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F82 | Security scans and independent penetration test | WP23 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F83 | Website claim/evidence register | WP21 | P0 | v1 required | S/P/H/O conditional on local dependencies |
| F84 | Consented demo, lead capture and pilot funnel | WP21 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F85 | Customer ROI calculator and pilot report | WP21 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F86 | Product/API/install/admin/security documentation | WP22 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F87 | Uploaded private meeting intelligence | — | P3 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F88 | Teams/Zoom/Meet/Webex bots and calendar capture | — | P3 | expansion; not authorized by initial coding kickoff | S/P/H conditional; O only for verified internal equivalents |
| F89 | Real-time in-call assistance and alerts | — | P3 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F90 | Survey-linked actual CSAT/NPS | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F91 | Organization incidents and anomaly detection | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F92 | Tenant provider/scorecard/retention administration | WP22 | P1 | v1 required | S/P/H/O conditional on local dependencies |
| F93 | Email/webhook then SMS/Teams/Slack/Telegram alerts | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H conditional; O only for verified internal equivalents |
| F94 | Audio PII masking | — | P2 | expansion; not authorized by initial coding kickoff | S/P/H/O conditional on local dependencies |
| F95 | ISO/SOC assurance program | — | P2 | expansion; not authorized by initial coding kickoff | All profiles: evidence/assessment program, not automatic certification |

**Rows extended by approved backend capabilities (section 6.7):** F01 by N11, F12 by N14, F18 by N08, F20 by N15, F26 by N04, F59 by N07, F72 by N13, F79 by N12 and F81 by N06. These rows keep their original titles, scope and acceptance text; each N-record adds the extended scope and carries its own status.

### 11.3 Baseline acceptance criteria and dependencies

#### F01 — Release/source provenance

- **Acceptance baseline:** Fresh checkout from the selected release ref builds API, console and workers with no dirty/untracked dependency; release manifest identifies every source/image/model revision.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.
- **Extended by approved backend capability:** [N11 — Migration and legacy retirement](#n11--migration-and-legacy-retirement) (section 6.7). The acceptance baseline above is unchanged; N11 carries its own criteria and status.

#### F02 — CI, migrations and regression suites

- **Acceptance baseline:** Required unit, PostgreSQL integration, queue and browser flows pass; fresh database and upgrade from the previous supported schema preserve seeded tenant/usage/evidence records.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F03 — Secret containment and scanning

- **Acceptance baseline:** Credential-owner containment is recorded for each suspected credential family; new source/history-distribution/image/support-bundle scans have zero unreviewed credential findings; no values enter reports/logs.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F04 — Unified login, refresh, reset, verification

- **Acceptance baseline:** Expired/revoked/disabled/reused refresh credentials and password-reset-invalidated sessions fail; legitimate rotation succeeds once; cookie CSRF and account-linking negative cases pass.
- **Recorded dependencies:** WP01
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F05 — Tenant isolation across DB, files, jobs, search

- **Acceptance baseline:** Two-tenant negative suite covers all API/object/job/share/export/search/projection operations with zero unauthorized reads/writes; pooled PostgreSQL connections never retain another tenant's context.
- **Recorded dependencies:** WP01
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F06 — Teams, supervisor and agent record permissions

- **Acceptance baseline:** Supervisor sees assigned teams only, agent sees authorized self records, analyst sees approved data and executive sees allowed aggregates; all matrix-denied actions fail server-side.
- **Recorded dependencies:** WP01
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F07 — Enterprise OIDC/SAML federation and MFA

- **Acceptance baseline:** OIDC and the supported SAML-broker profile complete login/logout/linking; privileged MFA enforced; disabled/group-removed users lose access within the documented maximum, proposed ≤15 minutes, with immediate admin revocation available.
- **Recorded dependencies:** WP02, WP03, WP19
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F08 — SCIM automated provisioning

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F09 — Audit trail and tamper-evident export

- **Acceptance baseline:** Every tested privileged configuration, export, review override, action and deletion records actor, scope, time, reason and result; protected sink detects a deliberately altered/missing event in an integrity drill.
- **Recorded dependencies:** WP01, WP02, WP03, WP04, WP05, WP06
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F10 — TLS, encryption, key rotation, restricted networks

- **Acceptance baseline:** External access uses validated TLS; DB/broker/object consoles are unreachable from public ingress; encrypted data/backup access fails without authorized keys; renewal/rotation drill succeeds.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F11 — Customer-managed keys and IP/session policies

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F12 — Adversarial media validation

- **Acceptance baseline:** Supported codec fixtures pass; forged, malformed, oversized and duration-expansion inputs fail within configured time/memory bounds and never reach inference; original checksums remain intact.
- **Recorded dependencies:** WP03, WP04
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.
- **Extended by approved backend capability:** [N14 — Browser/device support backend contract](#n14--browserdevice-support-backend-contract) (section 6.7). The acceptance baseline above is unchanged; N14 carries its own criteria and status.

#### F13 — Idempotent upload and ingestion ledger

- **Acceptance baseline:** Repeating one source event/upload finalization 100 times creates one logical conversation and one eligible usage effect; same key with changed payload returns a conflict.
- **Recorded dependencies:** WP03, WP04
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F14 — Private object storage lifecycle

- **Acceptance baseline:** Unauthorized/expired downloads fail; multipart abort and DB/storage partial failure reconcile without accessible orphan objects; supported storage contract suite passes.
- **Recorded dependencies:** WP03, WP04
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F15 — Retention, deletion, legal hold and subject export

- **Acceptance baseline:** Finite expiry and explicit immediate/indefinite settings behave as documented; access denied immediately on accepted deletion; active-store purge completes within 24h under normal conditions or raises retry alert; held data remains; restore reapplies tombstones.
- **Recorded dependencies:** WP03, WP04, WP05, WP06
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F16 — Text PII redaction and access policy

- **Acceptance baseline:** On a separately annotated supported-class Persian/English set, proposed recall ≥95% and precision ≥90%; unsupported classes and residual risk disclosed; protected originals never enter a redacted-only provider/export path.
- **Recorded dependencies:** WP03, WP04, WP05, WP06
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F17 — Durable queue, leases, checkpoints and recovery

- **Acceptance baseline:** Worker kill, broker outage, lost ACK, duplicate delivery and storage fault leave no lost accepted job; abandoned work resumes within two configured sweeper intervals after recovery; stale worker commits rejected; completed stages and financial/action effects are not duplicated.
- **Recorded dependencies:** WP03, WP04, WP05
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F18 — Usage metering, quotas and plan limits

- **Acceptance baseline:** 100 concurrent reservation/completion attempts cannot exceed a hard configured quota or double charge; cancellation/partial/retry/rollover reconcile exactly with ledger and customer usage report.
- **Recorded dependencies:** WP03, WP06
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.
- **Extended by approved backend capability:** [N08 — Provider budget kill-switch](#n08--provider-budget-kill-switch) (section 6.7). The acceptance baseline above is unchanged; N08 carries its own criteria and status.

#### F19 — Subscription invoicing and payment adapters

- **Acceptance baseline:** Invoice line totals match approved rate/usage/credits; replayed payment events do not duplicate activation/credit; unpaid/expired/grace transitions follow policy; startup does not overwrite negotiated entitlements.
- **Recorded dependencies:** WP03, WP06
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F20 — Model/prompt registry and provider capabilities

- **Acceptance baseline:** Every production run records stage model/prompt/schema/license-policy identifiers; an unknown/unqualified model fails preflight; a prior approved manifest can be restored without rewriting historical results.
- **Recorded dependencies:** WP03, WP04, WP06, WP07
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.
- **Extended by approved backend capability:** [N15 — Model and prompt rollback](#n15--model-and-prompt-rollback) (section 6.7). The acceptance baseline above is unchanged; N15 carries its own criteria and status.

#### F21 — Tenant privacy/region routing and egress denial

- **Acceptance baseline:** With cloud credentials present and the local provider unavailable, a local-only tenant emits zero unauthorized outbound requests and returns a truthful failure/partial state; forbidden region/provider choices are rejected.
- **Recorded dependencies:** WP03, WP04, WP06, WP07
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F22 — Provider retry, health and circuit breakers

- **Acceptance baseline:** Inject timeout, 429, Retry-After, 5xx, invalid schema and permanent auth errors; transient retries stay within budget, permanent errors stop, circuits open/recover, and no fallback violates tenant policy.
- **Recorded dependencies:** WP03, WP04, WP06, WP07
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F23 — Honest stage states and mock exclusion

- **Acceptance baseline:** Each forced missing/failing stage is represented in API/UI/export; unavailable diarization or failed QA cannot appear fully successful; production mock configuration is rejected and no random/default analytics are substituted.
- **Recorded dependencies:** WP01
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F24 — Evidence/revision/provenance schema

- **Acceptance baseline:** 100% of returned citations resolve to an authorized immutable transcript revision and valid original audio range; correction/reanalysis preserves old provenance and explicitly supersedes dependent results.
- **Recorded dependencies:** WP01
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F25 — Channel-aware speaker attribution

- **Acceptance baseline:** Verified separated-channel role accuracy ≥98% on the agreed set; swapped, silent, duplicated and transfer-affected channels are detected or marked unknown; stereo alone never establishes identity.
- **Recorded dependencies:** WP05, WP06, WP08, WP13
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F26 — Audio normalization, VAD and quality checks

- **Acceptance baseline:** Original timeline mapping remains correct through resampling/chunking; no speech loss >250ms at boundaries on annotated regression fixtures; empty/music/noise cases do not fabricate transcript; quality flags exposed.
- **Recorded dependencies:** WP05, WP06, WP08, WP13
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.
- **Extended by approved backend capability:** [N04 — Data-quality backend](#n04--data-quality-backend) (section 6.7). The acceptance baseline above is unchanged; N04 carries its own criteria and status.

#### F27 — Persian and English production STT

- **Acceptance baseline:** Persian held-out WER ≤20%, critical supported slice ≤30%; CER and entity accuracy reported; critical entities ≥95% exact match or routed to review; English/mixed slices measured separately and supported scope stated.
- **Recorded dependencies:** WP05, WP06, WP08, WP13
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F28 — Persistent GPU workers and bounded batching

- **Acceptance baseline:** Ten sequential jobs reuse the warmed model per configured worker process; configured concurrent load stays within memory budget without OOM; queue fairness and latency measured on the exact qualified hardware.
- **Recorded dependencies:** WP05, WP06, WP08, WP13
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F29 — Code-switching, terminology and extra languages

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F30 — Mono and multiparty diarization

- **Acceptance baseline:** Mono DER ≤15% on agreed held-out contact-center set, with overlap/collar policy and JER/count errors published; ambiguous clusters remain anonymous/unknown rather than fabricated agent identities.
- **Recorded dependencies:** WP04, WP08, WP09, WP13
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F31 — Overlap detection and attribution

- **Acceptance baseline:** Overlap intervals survive storage/UI/export; proposed overlap detection F1 ≥0.80 on an adequately annotated supported set; display-exclusive turns do not erase measured overlapping speech.
- **Recorded dependencies:** WP04, WP08, WP09, WP13
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F32 — Known-agent voice embeddings

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F33 — Timestamp alignment and calibrated confidence

- **Acceptance baseline:** ≥95% audited key-moment starts within ±1 second and all spans in recording bounds; confidence origin and calibrated/uncalibrated status visible; ambiguous alignment routes to review.
- **Recorded dependencies:** WP04, WP08, WP09, WP13
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F34 — Silence, talk ratio, overtalk and speaking rate

- **Acceptance baseline:** Known synthetic interval fixtures reconcile duration/silence/talk/overlap exactly to defined rounding; on annotated real clips, talk/silence ratio MAE ≤5 percentage points; no LLM-generated acoustic measurements.
- **Recorded dependencies:** WP04, WP08, WP09, WP10, WP13
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F35 — Hold, transfer and call-state metrics

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F36 — Intent, topics, entities and resolution status

- **Acceptance baseline:** Supported intent taxonomy macro-F1 ≥0.85; critical high-cost findings precision ≥0.95 or human confirmation; every field has evidence/unknown semantics and contradictory fixtures abstain correctly.
- **Recorded dependencies:** WP04, WP08, WP09, WP10, WP13
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F37 — Summaries, decisions and key moments

- **Acceptance baseline:** ≥95% audited atomic claims supported by cited evidence, 100% structural citation validity and no invented severe allegation in release set; summaries distinguish explicit outcome from inference.
- **Recorded dependencies:** WP03, WP04, WP09, WP10, WP11
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F38 — Action items, owners, deadlines and commitments

- **Acceptance baseline:** Explicit owner/date extraction precision ≥95% on held-out examples; unspecified values remain unset; every item links evidence and revision; proposed next steps are labeled separately from commitments.
- **Recorded dependencies:** WP03, WP04, WP07, WP11, WP12, WP14, WP16
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F39 — Persian sentiment journey and escalation signals

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F40 — Acoustic emotion or personality scoring

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F41 — Versioned custom weighted QA scorecards

- **Acceptance baseline:** Weights/applicability/NA/insufficient evidence calculations pass fixed examples; adjudicated QA kappa ≥0.75 and score MAE ≤8/100; scorecard versions remain reproducible and cannot silently change past scores.
- **Recorded dependencies:** WP04, WP08, WP09, WP10, WP13
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F42 — Keyword, script and compliance evidence rules

- **Acceptance baseline:** Exact phrase rules are deterministic; contextual critical findings precision ≥0.95 or require review; negation, quoted speech, wrong speaker and poor ASR regression cases do not create automatic confirmed violations.
- **Recorded dependencies:** WP04, WP08, WP09, WP10, WP13
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F43 — Automated coverage and review sampling

- **Acceptance baseline:** Ingested/valid/transcribed/fully-evaluated/reviewed counts reconcile independently; random/stratified samples respect configured sizes and scopes; failed calls cannot inflate evaluation coverage.
- **Recorded dependencies:** WP03, WP11, WP12
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F44 — Supervisor review and score override

- **Acceptance baseline:** Authorized reviewer can accept/override criterion with required reason; machine score preserved; unauthorized or stale-revision write rejected; audit and recalculated aggregate match approved result.
- **Recorded dependencies:** WP03, WP11, WP12
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F45 — Transcript and turn/speaker correction

- **Acceptance baseline:** Text and speaker-turn corrections create a new revision; playback/evidence remain navigable; dependent outputs become stale then recompute; old export and decision retain original provenance.
- **Recorded dependencies:** WP03, WP04, WP09, WP10, WP11
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F46 — Calibration and appeal workflow

- **Acceptance baseline:** Blind calibration computes agreement against adjudicated set; agent appeal reaches authorized reviewer and records disposition; agent cannot edit machine/human decision directly.
- **Recorded dependencies:** WP03, WP11, WP12
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F47 — Basic coaching assignments and progress

- **Acceptance baseline:** Accepted finding creates assigned task with due date; agent acknowledgement and follow-up status visible to allowed roles; overdue/reminder deduplication and closure audit pass.
- **Recorded dependencies:** WP03, WP11, WP12
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F48 — Personalized exercises and skill graph

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F49 — Complaint, product and operational issue trends

- **Acceptance baseline:** Counts/rates and drill-down match reference dataset exactly across time/team/product filters; insufficient samples and processing gaps are visible; correlation is not labeled confirmed cause.
- **Recorded dependencies:** WP03, WP11, WP14
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F50 — Repeat contact and observed FCR

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F51 — Versioned business graph projection

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F52 — Recurring root-cause and incident relationships

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F53 — Customer journey, account 360 and risk graph

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F54 — Cancellation/VIP-risk signals

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F55 — Sales objections, intent and opportunities

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F56 — Churn/conversion/NPS prediction

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F57 — Scoped lexical and semantic search

- **Acceptance baseline:** Approved relevant-evidence benchmark meets proposed recall@10 ≥0.90; all returned evidence is authorized/current or explicitly historical; deletion and membership changes remove access immediately even before reindex.
- **Recorded dependencies:** WP03, WP04, WP07, WP11, WP13, WP15
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F58 — Evidence-backed analytical Copilot

- **Acceptance baseline:** At least 95 of 100 frozen approved business questions answered correctly; deterministic counts 100% exact; unsupported questions abstain; zero cross-tenant/team leaks under prompt-injection and guessed-ID tests.
- **Recorded dependencies:** WP03, WP04, WP07, WP11, WP13, WP15
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F59 — Post-call/digest alerts and review queues

- **Acceptance baseline:** Repeated same-condition events create one alert per configured deduplication window; only authorized recipients notified; suppressed/acknowledged/escalated states and failed delivery visible.
- **Recorded dependencies:** WP03, WP04, WP07, WP11, WP12, WP14, WP16
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.
- **Extended by approved backend capability:** [N07 — Notification preferences](#n07--notification-preferences) (section 6.7). The acceptance baseline above is unchanged; N07 carries its own criteria and status.

#### F60 — Approval-based CRM/ticket/coaching actions

- **Acceptance baseline:** Rejected/unapproved/revoked/stale proposals cause zero external writes; repeated approved delivery causes one logical CRM/ticket effect; connector failures retry with complete audit.
- **Recorded dependencies:** WP03, WP04, WP07, WP11, WP12, WP14, WP16
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F61 — Role-specific dashboards and comparisons

- **Acceptance baseline:** Every role's dashboard respects scope and source denominators; proposed p95 normal API response <1 second on 100k-conversation reference dataset at documented load; cohort/rubric differences are shown.
- **Recorded dependencies:** WP03, WP11, WP14
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F62 — Executive daily/weekly briefs

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F63 — Asterisk/FreePBX/Issabel recording adapter

- **Acceptance baseline:** One named supported PBX/version imports 100 representative recordings/CDRs including transfers/duplicates/missing files; IDs and channel roles reconcile; unsupported versions are documented.
- **Recorded dependencies:** WP03, WP05, WP06, WP08
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F64 — Generic CRM/ticket ID and outcome mapping

- **Acceptance baseline:** Generic mapping links known call/customer/ticket/agent/outcome fixtures correctly; missing/changed/late IDs enter reconciliation rather than false joins; no phone-number-only global identity merge.
- **Recorded dependencies:** WP03, WP05, WP06, WP08
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F65 — 3CX/Cisco/Avaya/Genesys/Grandstream adapters

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F66 — Salesforce/Dynamics/HubSpot/Zoho/Odoo/IR CRM

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F67 — Jira/ServiceNow/Zendesk/Freshdesk adapters

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F68 — Versioned REST, scoped API keys and SDK

- **Acceptance baseline:** Published OpenAPI/client example completes upload→202 job→results; expired/revoked/out-of-scope key fails; rate limits and idempotency behavior verified through contract tests.
- **Recorded dependencies:** WP03, WP05, WP06, WP08
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F69 — Signed webhooks and delivery ledger

- **Acceptance baseline:** Raw-body HMAC/timestamp verification examples pass; altered/stale/replayed messages rejected or deduplicated; retry/dead-letter records allow safe redelivery without duplicate recipient effects.
- **Recorded dependencies:** WP03, WP05, WP06, WP08
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F70 — PDF, CSV, JSON and transcript exports

- **Acceptance baseline:** PDF/CSV/JSON and existing subtitle/text exports match authorized revision; Persian font/shaping and RTL manually checked; CSV formula injection neutralized; revoked access and retention apply to exported objects.
- **Recorded dependencies:** WP03, WP04, WP07, WP11, WP12, WP14, WP16
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F71 — Power BI/Metabase/Tableau connectors

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F72 — Persian RTL/English LTR, dates, digits, accessibility

- **Acceptance baseline:** All critical auth/upload/report/review/settings/error flows complete in Persian RTL and English LTR; keyboard-only flow succeeds, focus/labels are usable and 200% zoom does not hide required controls.
- **Recorded dependencies:** WP03, WP07, WP08, WP11
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.
- **Extended by approved backend capability:** [N13 — Accessibility backend support](#n13--accessibility-backend-support) (section 6.7). The acceptance baseline above is unchanged; N13 carries its own criteria and status.

#### F73 — Mobile supervisor access

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F74 — Air-gapped local STT/LLM/embed package

- **Acceptance baseline:** Fresh qualified installation with outbound network denied performs login, 30-minute Persian processing, local summary/QA/search, export and deletion; no hidden downloads/cloud calls; actual hardware/profile recorded.
- **Recorded dependencies:** WP01, WP02, WP05, WP08, WP09, WP10, WP18, WP20
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F75 — Offline license, upgrade and secure support bundle

- **Acceptance baseline:** Valid/expired/tampered offline license tests follow documented grace/export behavior; prior-version upgrade and failed-upgrade recovery preserve data; default support bundle contains no canary secret, transcript or identity.
- **Recorded dependencies:** WP01, WP02, WP05, WP08, WP09, WP10, WP18, WP20
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F76 — Kubernetes, HA and very large scale

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F77 — Hybrid local data / optional cloud management

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F78 — Metrics, traces, cost, queue/GPU health and SLOs

- **Acceptance baseline:** A failed/stuck job is discoverable by correlation ID; alert fires within 5 minutes for configured worker/purge outage; 24-hour mixed-load run achieves ≥99% valid supported processing and proposed p95 ≤15 minutes for 30-minute calls on reference GPU at admitted load.
- **Recorded dependencies:** WP01, WP02, WP03, WP04, WP05, WP06
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F79 — Encrypted backups and restore drills

- **Acceptance baseline:** Restore rehearsal on isolated supported topology meets proposed RPO ≤24h/RTO ≤8h; DB/object manifests reconcile; authorization works and deletion tombstones are reapplied before access.
- **Recorded dependencies:** WP01, WP02, WP03, WP04, WP05, WP06
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.
- **Extended by approved backend capability:** [N12 — Disaster recovery objectives](#n12--disaster-recovery-objectives) (section 6.7). The acceptance baseline above is unchanged; N12 carries its own criteria and status.

#### F80 — Persian gold corpus and slice benchmarks

- **Acceptance baseline:** Rights/access/split manifests exist; ≥20 Persian hours with frozen ≥5-hour held-out subset and separate English/mixed coverage; ≥20% double annotation/adjudication; no split leakage detected by identity/source checks.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F81 — AI regression and model/prompt promotion gates

- **Acceptance baseline:** Evaluation command reproduces metrics within documented stochastic tolerance; model/prompt change cannot promote without held-out quality/slice/cost report and approved rollback manifest.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.
- **Extended by approved backend capability:** [N06 — Feedback-to-evaluation loop](#n06--feedback-to-evaluation-loop) (section 6.7). The acceptance baseline above is unchanged; N06 carries its own criteria and status.

#### F82 — Security scans and independent penetration test

- **Acceptance baseline:** Independent scoped test completed on release candidate; zero unresolved critical/high findings, retest evidence for fixes, and no assurance statement exceeds actual test scope.
- **Recorded dependencies:** WP02, WP03, WP19
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F83 — Website claim/evidence register

- **Acceptance baseline:** Every public feature, accuracy, integration, customer result, deployment and assurance claim has an evidence owner and status; unreleased/unverified claims cannot be published as available/proven.
- **Recorded dependencies:** WP01, WP04, WP12, WP14, WP19
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F84 — Consented demo, lead capture and pilot funnel

- **Acceptance baseline:** Simulation clearly labeled; real demo identifies pipeline version and allowed sample; test lead reaches designated owner and receives acknowledgement; failures surface without collecting unnecessary sensitive data.
- **Recorded dependencies:** WP01, WP04, WP12, WP14, WP19
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F85 — Customer ROI calculator and pilot report

- **Acceptance baseline:** Calculator matches independently computed fixtures, including zero/negative ROI; costs and assumptions visible; no double-counting labor/AHT/churn benefits; pilot report distinguishes observed and projected outcomes.
- **Recorded dependencies:** WP01, WP04, WP12, WP14, WP19
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F86 — Product/API/install/admin/security documentation

- **Acceptance baseline:** New operator installs/administers/restores supported profile using released docs, and new supervisor completes review/correction/coaching with no undocumented developer step; broken command/API references corrected.
- **Recorded dependencies:** WP03, WP07, WP08, WP11
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F87 — Uploaded private meeting intelligence

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F88 — Teams/Zoom/Meet/Webex bots and calendar capture

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F89 — Real-time in-call assistance and alerts

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F90 — Survey-linked actual CSAT/NPS

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F91 — Organization incidents and anomaly detection

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F92 — Tenant provider/scorecard/retention administration

- **Acceptance baseline:** Authorized admins can preview/apply/version/rollback supported provider/scorecard/retention settings; invalid or privacy-violating configuration rejected; secrets masked and changes audited; other tenants unaffected.
- **Recorded dependencies:** WP03, WP07, WP08, WP11
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F93 — Email/webhook then SMS/Teams/Slack/Telegram alerts

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F94 — Audio PII masking

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

#### F95 — ISO/SOC assurance program

- **Acceptance baseline:** No explicit acceptance criterion recorded in this source. Define measurable behavior, denial/failure cases, supported modes and evidence before implementation/promotion.
- **Recorded dependencies:** None recorded; verify actual dependencies before delivery.
- **Additional governing gates:** GR01–GR40 as applicable; section 8; mode/provider-specific qualification; section 11.1 supersessions.

## 12. Coverage verification and maintenance

### 12.1 Sources and evidence boundary

- Owner-selected requirements in the conversation: V01–V07, P01–P06, A01–A06, C01–C07, G01–G05, K01–K04, licensing, all deployment modes, voice/text configuration, Odoo-only CRM, RFP/demo, diagnostics, retention options and configurable commercial policy.
- Local historical backlog: [backlog.json](../docs/planning/backlog.json).
- Local package index: [24 work packages](../docs/planning/work-packages/README.md).
- Historical handoff: [PROGRESS.md](../docs/handoff/claude-fable-5.1/PROGRESS.md) and [REMAINING_WORK.md](../docs/handoff/claude-fable-5.1/REMAINING_WORK.md). These may be older than the integrated implementation.
- External candidate references: section 10, from the preceding repository/documentation research. Recheck exact releases and licenses when selecting a component.
- Owner-provided session reports: historical implementation context only; not independently rerun here.
- Owner-approved backend capabilities N01–N15: owner mission prompt of 2026-09-29, sections 10 and 18; Linear [HOM-141](https://linear.app/homayar/issue/HOM-141) with children HOM-142 to HOM-156; mission planning records [PLAN.md](../docs/handoff/session-reset/backend-mode-100-linear/20260929-backend-mode-100/PLAN.md) and [coverage-ledger.json](../docs/handoff/session-reset/backend-mode-100-linear/20260929-backend-mode-100/coverage-ledger.json), planned against `682ffee`; N11 delivery evidence as linked in its section 6.7 record.

### 12.2 Completeness scope

This document records the full vision discussed to date, including all 95 historical feature IDs, all 35 originally selected V/P/A/C/G/K IDs, three additional provider/settings requirements K05–K07, 24 expanded cross-product requirements X01–X24, 15 owner-approved backend capabilities N01–N15 (four new, eleven extending an existing row), 40 guardrails, 10 release gates and 18 unresolved decision records. It preserves multiple technology alternatives and clearly separates planning from delivery evidence.

“Full vision recorded” means the discussed scope and completion obligations are documented. It does not mean all design details, customer-specific policies, endpoint schemas, prices, hardware sizes, integrations or implementation are finished. Unknowns have explicit decision or qualification requirements rather than invented values.

### 12.3 Maintenance contract

1. Preserve stable feature IDs and retain superseded records with reasons.
2. When implementing, link to exact work-package tasks and evidence; do not create a conflicting parallel status source.
3. Add per-mode/provider status to the existing task system or a versioned capability registry.
4. Keep AppVision, runtime policies, acceptance tests, packaging manifests and sales claims consistent.
5. Introduce executable registry/policy files through reviewed implementation work; prose alone is not enforcement.
6. Recheck technology maintenance and licensing before adopting or redistributing a version.
7. Record owner decisions with date, scope and effective policy version.
8. Record a documented scope change before dropping an option or adding a new release blocker.
9. Refresh Graphify after integrated source changes when appropriate; a source graph is not validation evidence by itself.
10. Review the vision at release boundaries and when an owner decision changes product scope.

### 12.4 Documentation verification record

The creation task checks structural coverage, original feature/acceptance preservation, unique IDs, relative local links, and an English-only document body. It performs no application test, provider call, schema migration, runtime configuration change, commit, push or deployment.

- Imported baseline features: **95**.
- Baseline source SHA-256: `19f83d3821eeee9fa59444e40632c124ffc206734dcc0011ae045c8e119fb3b7`.
- Document location: `Homayar/AppVision.md`.

- Structural verification: **PASS** — all 95 baseline IDs/titles/recorded acceptance criteria preserved; all 62 expanded capability IDs, 40 guardrail IDs, 10 release-gate IDs and 18 decision IDs present once in their matrices; local reference links resolve; table columns and code fences consistent; no Persian/Arabic-script body text.
- Application/runtime validation: **NOT_RUN** (documentation-only task).

**Update 2026-09-30 (approved backend capabilities N01–N15, Linear HOM-141):**

- Approved backend capabilities added: **15** (N01–N15). New capability IDs: **4** (N01, N03, N05, N10). Extensions of an existing row: **11** (N02→V01, N04→F26, N06→F81, N07→F59, N08→F18, N09→X16, N11→F01, N12→F79, N13→F72, N14→F12, N15→F20).
- Existing rows changed only by an added cross-reference: V01 and X16 in section 6. The nine extended F-rows keep their section 11.2 rows unchanged and gained one cross-reference bullet each in section 11.3.
- Structural verification: each of N01–N15 has exactly one matrix row and one section 6.7 record; every table touched keeps its header's column count; the 95 baseline IDs, 62 expanded capability IDs, 40 guardrail IDs, 10 release-gate IDs and 18 decision IDs each still appear once as a table row; local links added by this update resolve.
- Application/runtime validation: **NOT_RUN** for this documentation update. N11's delivery evidence is recorded in its section 6.7 record.
