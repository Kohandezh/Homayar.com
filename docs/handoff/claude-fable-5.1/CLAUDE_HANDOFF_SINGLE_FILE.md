# Homayar — complete Claude handoff in one file

Prepared 2026-09-07 for the user-selected Claude model. This is a reference attachment: connect the actual repository and tools before requesting execution. The ZIP also contains independent work-package folders, machine-readable backlog and graphs. No product implementation was performed during preparation.

# Project-start master prompt

# Homayar — Claude project-start master prompt

Copy the prompt below into the Claude environment in which you have selected your preferred model. “Claude Fable 5.1” is the user's supplied model label; this package assumes no unverified API model ID, context limit or special capability. Instructions apply to the connected repository and tools, not to the spelling of a model name.

---

You are the lead implementation engineer for Homayar Commercial v1.0. You are continuing an audited, partially implemented project. Your job is to execute the supplied implementation packages carefully, preserve existing useful work and produce a commercially credible Persian-first conversation-intelligence product with SaaS and qualified private/offline deployment.

This is an implementation kickoff when I send you this prompt. The prior assistant prepared the handoff only; it did not complete these product features. Do not interpret a prepared document, folder, graph edge or version string as an implemented capability.

## Workspace and first reads

The expected local workspace is `/Users/emperor/Documents/AI/Homayar.com`. Resolve it from the connected environment; do not assume you have filesystem access merely because the path is named. If running in Claude web without file/terminal access, read the attached handoff, explain the exact missing access and request the repository connection or authorized source files. Do not pretend to run commands, deploy or install skills in an environment that has no tools.

There are TWO independent dirty Git repositories:

- Workspace root: `Kohandezh/Homayar.com`, Next.js marketing website in `src/`, shared local infrastructure in `infra/`, existing website tests/scripts.
- `Homayar/`: `Kohandezh/HomaYar`, product FastAPI backend in `backend/`, React/Vite console in `frontend/`, Python manifests/migrations.

The outer repository does not yet deliver the nested product source through a reliable clean-checkout contract. Do not delete nested `.git`, accidentally stage an unexplained Gitlink, reset local changes or merge disconnected master wholesale.

Read in this order, using targeted sections when the full report is too large:

1. All applicable `AGENTS.md` and `CLAUDE.md`, preserving current instructions. The website requires reading relevant installed Next.js docs before code changes.
2. `docs/handoff/claude-fable-5.1/START_HERE_FA.md` and `CONTEXT_SNAPSHOT.json`.
3. `docs/handoff/claude-fable-5.1/REMAINING_WORK.md`.
4. `docs/architecture/workspace-structure.md` and `docs/architecture/decisions/0001-preserve-runtime-roots.md`.
5. `docs/planning/homayar-commercial-blueprint.md`: Sections 1–5, 11–17, 31–38 and 40 initially; consult other sections by package.
6. `docs/planning/backlog.json` and `docs/planning/work-packages/README.md`.
7. `docs/handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md`, `SKILLS_SETUP.md`, `GRAPHIFY_GUIDE.md` and `PROGRESS.md`.

The full plan has 95 prioritized features, 64 required P0/P1 feature gates, 24 packages and 161 decomposed implementation tasks. P2/P3 features are expansion options, not permission to broaden the initial work.

## Product decision

Build evidence-backed Persian contact-center QA and operational issue discovery. The buyer is a QA/CX/operations team with recorded calls and a repeatable rubric. Core workflow: authorized ingestion → validated audio → real transcript and reliable speaker/role attribution → criterion-level QA with evidence → supervisor correction/review → coaching or approved action → scoped issue trends.

Use a modular FastAPI product with PostgreSQL authoritative for identity, tenancy, conversations, jobs, usage and quality records; private object storage for media; a Celery-compatible broker for delivery; persistent CPU/GPU inference workers; pgvector for scoped retrieval. Keep the current React/Vite console and separate Next.js website. Neo4j is an optional derived projection after useful-query proof; Graphify is a source/planning navigation tool, not the production customer knowledge graph.

One product must support SaaS and a qualified private/offline profile. Privacy policy is evaluated before model cost. No hidden cloud fallback is permitted. Do not make remote cloud identity, telemetry, model download or a model aggregator mandatory for private operation.

## Current evidence you must preserve

The dated baseline found:

- Committed product dev `605cb074` is one commit ahead of main `ecff6657`; master `3766bc90` belongs to disconnected legacy history. Verify current refs before acting.
- The most advanced relational/session/tenant/storage/provider work exists in uncommitted local changes. It is useful but not released.
- Legacy real Whisper uses fabricated time-based speaker roles; legacy AI failure can become random/default business output. Do not expose it as genuine analysis.
- Current v1 real diarization is unavailable; a provider interface is not a working model.
- Jobs dispatch to `processing`, while the inspected worker configuration subscribes to `celery` by default. Prove queue delivery with an actual broker and worker.
- Durable job records exist, but outbox/leases/checkpoint recovery and duplicate-effect proof remain open.
- Current v1 identity checks are stronger than legacy; complete tenant/resource/role isolation and PostgreSQL concurrency still need proof.
- Object deletion is best effort after database mutation; retention execution/restore tombstones need completion.
- Default image/provider assumptions do not yet deliver a complete local AI bundle.
- Historical master includes provider-key-shaped credentials in source and compiled artifacts. Owners must revoke/rotate and review use. Do not print values, test credentials against providers, or substitute Git cleanup for rotation.
- Public claims, case studies, integrations and assurance promises exceed demonstrated evidence. A timed demo is a simulation, not real speech inference.
- The old call/graph/analytics/Copilot components are on disk but not all routed in the current meetings-oriented console.

Historical validation: 97 existing backend tests passed in an isolated copy, with 142 warnings, during the September 7 audit. They are mainly SQLite/test-provider tests. This is not evidence that PostgreSQL, real Celery, GPU models, Persian accuracy, private installation or production security passed. Do not repeat this as a new test result.

## Authorization and change discipline

Implement local, reviewable code/configuration/tests/documentation for the selected work package. Preserve all pre-existing dirty changes. Use the correct repository and a `codex/` feature branch if a new branch is needed, without resetting the worktree. Do not commit merely to hide a dirty baseline.

Do not publish/deploy, send customer messages, change paid services, rotate credentials, rewrite remote history, delete user data or merge/push unless I authorize that external/destructive action. Prepare a concrete change and exact operational step first. Do not ask repeatedly for permission already supplied for ordinary local implementation. If a necessary decision is missing, continue independent work and ask one specific question with its consequence.

Do not upgrade unrelated libraries, move all runtime folders to `apps/`, install overlapping frameworks, change my agent/memory system, or fix every discovered issue in one PR. Resolve the clean-checkout source contract explicitly in WP01. Preserve the temporary layout until a tested migration offers real benefit.

## Execution method

1. Inspect current `git status` in both repositories, current refs, file timestamps and the progress record. Compare with the dated snapshot, but treat newer user work as authoritative.
2. Select one package and one bounded subtask. Read its implementation checklist, current code and acceptance criteria. Record scope and expected validation before editing.
3. Use current Graphify queries to find impact, then verify relationships in source. Static/inferred edges are not runtime proof. If source hashes are stale, rebuild the selected graph safely before relying on it.
4. Identify the smallest correct change, including migration, API, UI, authorization, error states, observability and docs actually affected. Use existing good primitives rather than a broad rewrite.
5. Add meaningful regression/integration/evaluation checks for changed behavior, not tests that merely restate the implementation. Use real PostgreSQL/queue/model/deployment tests where their gate requires them.
6. Run appropriate checks, inspect the diff and obtain an independent review for sensitive changes. Avoid endless testing after the relevant gates pass.
7. Update `PROGRESS.md` and the relevant package task state with exact evidence. Keep pending/external blockers visible; do not mark a whole feature complete when only one layer is implemented.
8. Continue to the next dependency-ready subtask within authorized scope. If the session ends, leave the next action and exact file/command context so another session can resume without repeating the audit.

Use at most a few independent agents when the environment supports them: platform, audio/evaluation and UI can sometimes progress separately. Assign non-overlapping files and clear contracts. Do not use multiple orchestration systems for the same work, and do not assume subagents exist in every Claude environment.

## First implementation session

Begin with a non-destructive preflight and WP01-T01/T02: exact source/dirty-state inventory and clean-checkout source contract. Review existing CI/Docker paths before selecting a contract. Prepare and implement the smallest reviewed local change that makes the source requirement explicit and testable; do not promise a remote clean build before the uncommitted source is reconciled.

In parallel where possible, prepare the safe WP02 credential-owner inventory and WP13 corpus/annotation protocol. Do not require customer audio or credentials to begin the source/CI work. Start WP04 truthful-state/evidence contracts next, then proceed through dependency-ready P0 work. Claim inventory in WP21 can start immediately; publishing corrected copy still follows the authorized publishing workflow.

Finish the first session with a concrete artifact or implementation step, validation result, unresolved owner decisions and exact next task. Do not spend the session rewriting the entire blueprint or starting a meeting-bot/graph showcase.

## Definition of trustworthy output

- Never turn provider failure into random scores, fabricated speakers, default NPS or invented business facts.
- Every finding cites immutable transcript revision and valid original audio offsets; corrections create new revisions and supersede dependent results explicitly.
- Scores distinguish pass/fail/not applicable/insufficient evidence and use the documented denominator. Speaker uncertainty can block role-specific scoring.
- Audio metrics are computed from signals/events. LLM guesses are not silence measurements; diarization clusters are not named identities.
- Aggregate counts come from authorized deterministic queries, not an LLM estimate from a retrieved sample.
- Every tenant/resource/job/object/share/export/search path enforces scope. Network failures never override privacy policy.
- Exactly-once effects are achieved through idempotent ledger/artifact/action contracts; do not claim exactly-once distributed execution.
- Approved external actions need authorized human approval, a preview, idempotency and audit.
- Offline qualification means the exact bundle works with outbound networking denied, including installation dependencies, local inference, identity, export, deletion, upgrades and restore.

## Acceptance and release

Follow each feature's acceptance gate in its package and blueprint Section 38. Freeze proposed quality targets with the pilot baseline before evaluating release candidates; do not lower thresholds silently after failure. Report sample sizes, supported slices, abstention, processing coverage and confidence limitations.

A version string does not prove a milestone. v1 requires qualified source/release artifacts, real speech and QA quality, review workflow, tenant isolation, correct billing, real queue recovery, private/cloud qualification, security retest, operator documentation and customer acceptance. No raw model/provider benchmark establishes Homayar end-to-end performance.

Defer P2/P3: mandatory graph, broad meeting bots, real-time assistance, voiceprints, generalized emotion/personality, uncalibrated churn/NPS predictions, unlimited inference, many named integrations and code forks per customer.

## Skills and reporting

Read only relevant installed skills. The handoff inventory found Graphify, Gstack engineering/review/debug/QA/security, UI UX Pro Max and Ruflo workflow skills already present in `~/.claude/skills`. File presence does not prove their runtime dependencies or current-session activation. Use `SKILLS_SETUP.md` for verification and missing-only setup. Do not reinstall or upgrade the full factory automatically.

Communicate to me in Persian, using exact technical identifiers in English. Keep updates concise: what changed, what evidence shows, what remains and the next action. End each session with:

- Package/subtask and status: implemented, validated, partial or blocked.
- Files changed and why.
- Exact checks and results; distinguish historical from newly executed tests.
- Risks/external prerequisites and next dependency-ready task.
- Updated progress/evidence paths.

Start now by reading the entry documents, verifying both repository states and beginning WP01's smallest executable task.


---

# Execution and continuity

# Execution and resume protocol

This package prepares future implementation. Product code was not changed by the preparation task. The user will start implementation by giving the master prompt to Claude.

## Status meanings

| State | Required evidence |
|---|---|
| not_started | No implementation completed in the current work log; existing partial source may still exist |
| in_progress | Named subtask, bounded file ownership, intended tests and current diff |
| implemented_unverified | Code exists but the required checks have not passed |
| partial | Some feature gates pass; missing layers and tests are named |
| blocked_external | Specific missing credential-owner/customer/hardware/procurement input; independent work continues |
| validated | Relevant tests/evaluation executed on the current code and evidence recorded |
| complete | All task/feature acceptance criteria and required review/documentation are satisfied |

A work package is complete only when all its feature criteria pass. A full release additionally needs cross-feature scenarios, supported topology, security/operations and customer acceptance. Do not mark complete because a mock test, a screenshot, a diagram or a version label exists.

## Session start

1. Read `PROGRESS.md`, current package and applicable root/product instructions.
2. Inspect both Git states. Preserve user changes and record the baseline. Never run a blind clean/reset/stash.
3. Query the current graph with an explicit `--graph` path; compare source hashes before relying on it.
4. Read actual files in the selected change area and current framework documentation.
5. Write the next task ID, expected result, files/ownership and appropriate validation into the progress entry.

## Required task evidence

Each task entry records date, agent/environment, repository/ref, scope, changed paths, migration/schema impact, executed commands, exit/results, model/data versions if relevant, acceptance criteria satisfied, criteria still pending, external dependencies, review and next action. Never record secrets or customer conversation content.

No commit, PR or release identifier should be invented. If no commit was made, say so and preserve the diff. Branch changes must not discard local work. External communications, production deployment, destructive cleanup and credential changes need the relevant task authorization.

## Suggested test selection

- Text-only handoff edits: link/JSON/checklist consistency; no product suite needed.
- Backend behavior: narrow regression tests, relevant integration tests and type/lint checks.
- Tenant/usage concurrency: real PostgreSQL tests, not SQLite alone.
- Queue/recovery: real broker and worker with fault injection and idempotency assertions.
- Speech/QA: actual qualified models and frozen authorized evaluation corpus.
- Frontend: affected build/type checks and browser workflow, including Persian RTL and errors.
- Deployment: configuration/build checks, supported-profile smoke and install/upgrade/restore as required.

Read the current manifests rather than blindly pasting old commands. Run backend tests from `Homayar/` with its environment; marketing commands from root; console commands from `Homayar/frontend/`. Keep real data and `.env` out of disposable test snapshots.

## Dependency nuance

`backlog.json` lists completion dependencies. WP02 credential containment and WP13 corpus rights/protocol can start immediately. WP20 instrumentation, WP21 claim inventory and WP22 documentation can start incrementally even though their completed deliverables depend on later packages. Avoid a false circular block between baseline data collection and models evaluated on that data.

## Resume after context loss

Read the newest progress entry, then inspect its exact files/diff and evidence. Continue its stated next task. Do not rerun the whole research audit, reinstall the toolchain or regenerate all plans. If the current state differs from the log, preserve the newer state and reconcile before editing.


---

# Skills setup

# Claude skills: verified inventory and setup plan

Preparation only: no skill was installed, upgraded, removed or activated by this handoff. This follows the user's clarification to prepare the work for Claude rather than perform product/environment implementation. The core relevant skills are already present on this computer.

## Existing skill files

All paths below were checked on 2026-09-07. File presence is verified; session activation and most runtime dependencies were not exercised. See SKILLS_INVENTORY.json for exact paths and hashes.

| Skill | Existing location under ~/.claude/skills | Use |
|---|---|---|
| Graphify | graphify/SKILL.md | Source relationships and impact queries; installed CLI 0.9.40 successfully rebuilt this handoff's map |
| Gstack plan-eng-review | gstack/plan-eng-review/SKILL.md | One package's architecture and implementation review |
| Gstack review | gstack/review/SKILL.md | Review changed code before completion |
| Gstack investigate | gstack/investigate/SKILL.md | Reproduce and locate defects |
| Gstack qa / browse | gstack/qa/SKILL.md; gstack/browse/SKILL.md | Existing browser workflow QA |
| Gstack cso | gstack/cso/SKILL.md | Threat/security review |
| UI UX Pro Max | uiux-pro-max/SKILL.md | RTL/LTR, accessibility and review workflow |
| SPARC methodology | ruflo-skills/sparc-methodology/SKILL.md | Optional bounded spec→code→verification process |
| Swarm orchestration | ruflo-skills/swarm-orchestration/SKILL.md | Optional independent multi-agent work |
| Verification quality | ruflo-skills/verification-quality/SKILL.md | Optional evidence discipline |

The minimal workflow is Graphify + one engineering/review skill + the existing test tools. Load UI/QA/security skills when relevant. Ruflo/agents are optional; do not start a swarm merely because the files exist. Do not install a second browser stack, RAG runtime, scheduler or model framework for this kickoff.

## Claude preflight

In Claude Code or another filesystem-enabled Claude environment, inspect the installed skill descriptions and use the environment's skill discovery/help mechanism. Open a fresh Claude session if newly installed skills are not discovered. In Claude web, these local folders are not automatically accessible; upload the needed instructions or connect the local workspace. Never claim a skill is active based only on this inventory.

Useful read-only checks:

```bash
command -v claude
command -v graphify
graphify --version
command -v uv
command -v pnpm
command -v node
```

Read the relevant SKILL.md before running its workflow. Follow the user's task scope over generic suggestions to install hooks, modify memory or auto-publish. Preserve existing skill/custom settings; do not overwrite them during discovery.

## Missing-only installation plan for Claude

No additional skill is required to read this handoff or begin WP01. If a later session discovers an absent Graphify skill but the CLI is installed, the documented native installer is:

```bash
graphify install --platform claude
```

Before executing it, inspect the target directory and back up any custom skill contents. A Graphify binary upgrade is separate from rebuilding its output. Version 0.9.40 was used here; no claim is made that it is the latest available release.

If Graphify itself is missing on another computer, read its official package/project installation documentation, select a pinned supported release and install into an isolated tool environment. Do not copy private `.env`, tokens or agent settings. For optional skills, inspect source/license/instructions before installation, install only what the chosen package needs and verify discovery in the next session.

Gstack, UI UX Pro Max and Ruflo are already installed here. Use their existing local instructions; no speculative model-specific “Fable plugin” or API model ID is necessary. Model selection remains in the user's Claude UI.

## Product dependencies are not skills

Python, Node/pnpm, PostgreSQL, a broker, object storage, FFmpeg and GPU/model runtimes belong to product development/deployment. WP01/05/06/09/19 qualify them. Do not download many model weights or install global servers merely to prepare a prompt. Actual model licenses, hardware, offline dependencies and evaluation are still open work.


---

# Preparation evidence

# Preparation report — 2026-09-07

The user's final scope was to prepare everything for Claude, leaving product implementation to Claude. This task therefore created documentation, executable-task specifications and generated source/planning maps only.

## Completed preparation

- Organized docs/planning, docs/planning/work-packages, docs/handoff/claude-fable-5.1, docs/security and architecture decision documents.
- Copied the complete 40-section commercial blueprint with a historical-audit note.
- Prepared 95 prioritized feature records, 24 package folders, 161 decomposed subtasks and exact acceptance coverage for all 64 P0/P1 features.
- Prepared a complete remaining-work report, redacted security-owner actions, baseline repository snapshot and dependency-aware kickoff.
- Wrote Claude master/continuation prompts, execution protocol, progress template and Persian entry guide.
- Verified 11 relevant skill entries already exist under ~/.claude/skills. No additional skill is required to begin WP01; no skill was installed, upgraded, removed or activated here.
- Rebuilt source/planning Graphify maps and supplied a safe new-snapshot refresh helper. The installed Graphify version is 0.9.40; its binary was not upgraded.
- Prepared the single-file Claude reference and ZIP transfer package.

## Source and environment preserved

All 338 selected pre-existing runtime/source/configuration file hashes match the preparation baseline. Both repository HEADs are unchanged. Runtime folders, product logic, deployment configuration, Git histories, secrets, databases and customer media were not modified. No commit, push, merge, deployment, credential rotation, paid service change, watcher or agent hook was performed.

This is documentation/folder preparation, not an apps-directory migration or a completed monorepo conversion. The two-repository clean-checkout contract remains WP01 work. The application's full source is not inside the handoff ZIP; connect the actual workspace before asking Claude to execute.

## Graph evidence

The current source map covers 285 selected current code/test files across the website and product. The resolved navigation view has 2,580 nodes, 7,652 edges and 106 communities. The lossless companion has 2,714 nodes and all 8,492 original AST relationships, including 134 explicitly unresolved reference nodes. All full-graph endpoints exist, but those unresolved references were not assigned invented definitions. Raw diagnostics remain available, including one self-loop and relation consolidation in the simple view.

The planning graph has 119 nodes (95 features plus 24 packages), 148 explicit plan relationships and no missing/dangling endpoints. Its edges describe planned work, not runtime implementation.

Extraction was local AST plus deterministic explicit-backlog mapping: no separate LLM/API extraction call, no paid extraction token usage and no model download. Free-form semantic extraction of all documents/images/recordings was not performed. Secrets/environments, customer media, databases, dependencies/caches and legacy sample/history content were excluded. Historical graphify-out snapshots were preserved.

## Validation actually performed

- Checked 40 blueprint sections, all feature/package/task counts, exact P0/P1 acceptance mapping and acyclic package dependencies.
- Validated JSON, Markdown local links, portable helper syntax/root resolution and protected-file hashes.
- Ran Graphify queries against the new code and planning maps successfully.
- Opened both HTML graphs in Playwright Chromium with all external HTTP(S) blocked. Each rendered a canvas with zero page errors and no external request. The vis-network 9.1.6 renderer and MIT license are bundled; its bytes matched the original template's SHA-384 before local-file loading was adjusted.
- Screened prepared textual artifacts for selected credential patterns without printing values; results are in VALIDATION.json. This is a limited check, not an exhaustive security certification.
- Checked ZIP integrity and excluded source secrets, customer data, Git stores and dependency folders.

No product test suite was rerun because no product behavior changed. The 97-pass backend result belongs to the earlier isolated audit; it is not a new implementation result or proof of real AI/GPU/queue/PostgreSQL/offline readiness.

## Work still open

All product implementation/release qualification remains open as specified in REMAINING_WORK.md and the backlog. Existing partial code should be reviewed and reused. Claude should begin WP01-T01/T02, prepare the credential-owner inventory in WP02 and the authorized-corpus protocol in WP13, then follow P0/P1 dependencies.

External requirements remain: credential owners, authorized representative recordings/annotation rights, actual PBX/CRM/IdP versions, real database/broker/GPU/private-install test resources, model/provider licensing/procurement, independent security retest and customer/pilot commercial acceptance. Preparation cannot replace those owner decisions or evidence.

“Anthropic Claude Fable 5.1” is the model label supplied by the user. No API identifier, context limit or model-specific capability was assumed or configured. Skill files are present; apart from Graphify used here, their runtime/current-session activation was not re-tested.

## Start

Read START_HERE_FA.md. Send MASTER_PROMPT.md to Claude with the connected workspace. For Claude web/Project, attach CLAUDE_HANDOFF_SINGLE_FILE.md and connect source/tools separately. Use CONTINUE_PROMPT.md and PROGRESS.md to resume later sessions.


---

# Full audited commercial blueprint

Historical audit copy: the original report was produced outside the repositories on 2026-09-07. This copy was added to the documentation-only Claude handoff afterward. Statements about the audit being read-only and its test results refer to that earlier phase, not to new product implementation.

# 1. Executive Verdict

**Homayar has useful product foundations, but it is not yet a trustworthy commercial voice-intelligence product. Build a Persian-first, evidence-backed customer-conversation intelligence platform, entering the market through contact-center QA and operational issue discovery. Preserve private deployment. Defer a broad meeting-assistant business and autonomous actions.**

Planning and technical due diligence, 7 September 2026. Prepared for the next implementation agent. This report is a recommendation, not authorization to execute remediation, migrate data, rotate credentials, publish claims, or deploy software.

Reading guide: [critical blockers](#5-critical-problems), [feature priorities](#9-feature-prioritization-matrix), [target architecture](#11-target-technical-architecture), [unit economics](#22-unit-economics), [website audit](#27-homayarcom-audit), [implementation packages](#35-feature-by-feature-implementation-plan), [acceptance gates](#38-acceptance-criteria), and [final strategy](#40-final-recommended-product-strategy).

## Readiness assessment

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

## Evidence boundaries

The audit inspected the HomaYar remote branch topology through a separate bare mirror, relevant source and history, the dirty local product and website trees, public Homayar pages, deployment/CI documents and current primary technology/vendor sources. No original repository files, branches, commits, secrets, databases or deployments were intentionally changed. All analysis artifacts and test writes are outside both repositories.

The current product backend was copied into an isolated temporary directory and its existing tests ran: **97 passed, 142 warnings, 82.25 seconds**. These are mainly SQLite/test-provider checks. They do not prove PostgreSQL concurrency, live Celery delivery, real GPU inference, real Persian accuracy, deployment health or security against a running production system. A separate configuration inspection showed Celery's configured worker queue set as **celery**, while the application sends jobs to **processing** and the Compose worker omits an explicit queue selection. This is a release-blocking configuration mismatch until an actual broker/worker test proves correction.

The live homepage and demo were inspected in a browser, and other public pages were retrieved as HTML. No microphone recording, customer upload, contact submission, payment or authenticated production workflow was performed. The authenticated console was assessed from source and tests; a complete visual and accessibility sign-off remains a release gate. No revenue records, customer contracts, actual production topology, support history or customer benchmark corpus were supplied. Claims about those remain unverified.

Memory preflight found the configured LightRAG data and Codex memories directories empty. Existing local planning/runbook documents were reviewed as historical context, not as proof that their target design had shipped. The 18 August assessment already favors PostgreSQL, a same-origin console and optional graph; preserve that sensible direction. [Existing assessment](/Users/emperor/Documents/AI/Homayar.com/docs/architecture/initial-assessment.md)

Evidence labels used throughout:

- **D**: committed product dev at 605cb074; **N**: main at ecff6657; **M**: disconnected master at 3766bc9.
- **W**: uncommitted local product overlay; **S**: uncommitted local marketing/infrastructure tree; **L**: live public website on the audit date.
- **Verified** means code or observed behavior supports the statement. **Proposed** means target design. **Assumption** means an input requiring customer or operational validation.
- “Missing” means no implementation was found in the inspected relevant code; it does not claim a capability cannot exist in an undisclosed external deployment.

# 2. Current Architecture

## Four distinct surfaces

~~~text
master, disconnected history
  Flask + AdminLTE/Jinja + SQLite + direct cloud-model calls
  legacy conversation/organization intelligence experiments

main -> dev, current committed product family
  React/Vite -> FastAPI
    -> overlapping custom JWT / fastapi-users authentication
    -> Neo4j for users, calls, transcripts and analysis
    -> local audio files
    -> Celery / Redis -> faster-whisper on CPU -> four prompt agents
    -> graph browser, aggregate dashboards, template-based Copilot, PDF

dirty product overlay, local only
  React/Vite /app -> FastAPI /api/v1
    -> SQLAlchemy models / Alembic -> SQLite default, PostgreSQL supported
    -> organizations, workspaces, sessions, memberships, usage reservations
    -> private local/S3 storage -> durable job record -> Celery processing
    -> ASR port / unavailable diarization port / compatible-LLM port
    -> meetings, transcript, summaries, actions, search, shares, usage, settings
    -> legacy API disabled by default; Neo4j optional flag

marketing repository and live public site
  Next.js website source + separate nested product Git repository
  local reverse-proxy, Compose, Docker and CI templates
  deployed public pages on LiteSpeed; deployment not proven identical to source
~~~

The local overlay has made a real architectural improvement: tenant-aware relational transactions now handle identity and usage. The API mounts seven v1 router groups and conditionally mounts the legacy routes under /api/legacy. The old fastapi-users router is not mounted by the new main entry point. Do not describe every legacy vulnerability as exposed by the default v1 runtime. [API composition](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/main.py:134)

The overlay also changes product behavior. The active frontend routes now expose overview, meetings, search, members, usage, settings and shares. Existing call reports, graph, incidents, analytics, users and Copilot components remain on disk but are not in the active route map. This is **unported product functionality**, not proof of completed replacement. [Current route map](/Users/emperor/Documents/AI/Homayar.com/Homayar/frontend/src/App.tsx:63)

## Main data-flow findings

1. **Committed STT is real, speaker attribution is fabricated.** A CPU int8 Whisper base model is constructed per call. VAD is enabled. “Agent” versus “customer” is determined by which half of a minute a segment starts in. It is not diarization. [STT](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/services/stt_service.py:25)
2. **Committed AI failure handling is unsafe.** QC/problem/insight/teacher fallbacks can generate business-looking values; normalization persists only selected fields and loses mock provenance. Silence and NPS-looking values are not measurements. [Analysis normalization](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/services/analysis_service.py:101)
3. **The local provider interface is useful but incomplete.** It provides local Whisper and one compatible JSON LLM adapter; diarization is either unavailable or a test mock. Production mock use is rejected. Provider configuration is global, not a complete tenant-specific policy router. [Provider factory](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/providers/factory.py:46)
4. **Transactional job state exists, recovery is incomplete.** Usage reservations and duplicate protections are meaningful. The queue consumer configuration, dispatch/commit gap, lease recovery, stage checkpoints and model lifetime still require work. [Dispatcher](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/jobs/dispatcher.py:8), [processor](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/jobs/processor.py)
5. **Graph and search are different capabilities.** The old graph browses Neo4j nodes; the new search uses SQL LIKE predicates. Neither establishes evidence-grounded hybrid retrieval. The graphify-out directory is a code-analysis artifact, not a customer conversation knowledge graph. [Search](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/routers/v1/insights.py:447), [Graphify report](/Users/emperor/Documents/AI/Homayar.com/Homayar/graphify-out/GRAPH_REPORT.md)

# 3. Repository & Branch Analysis

## Source-of-truth decision

| Surface | Observed state | Maturity and unique contribution | Recommended treatment |
|---|---|---|---|
| Remote master | 3766bc9, 19 July 2025; disconnected from main/dev | Legacy Flask/AdminLTE/SQLite application, bundled vendor assets, intelligence experiments; credential material in history | Historical reference only. Do not merge the application wholesale. Preserve evidence, contain secrets, archive after owner-approved remediation. |
| Remote main | ecff6657, 27 April 2026 | FastAPI/Neo4j rewrite with fastapi-users and Celery phase | Superseded committed baseline; not the most advanced branch. |
| Remote dev | 605cb074, 1 May 2026 | Exactly one commit ahead of main: 11 files, 378 insertions, 208 deletions; real Whisper, Redis blacklist/email and pipeline changes | Best committed baseline in this family, but not production-ready. |
| Local product dev working tree | Many modified files and untracked v1 models/services/providers/tests/migrations | Most advanced architecture, useful session/tenant/usage/storage foundations; call-center workflow removed from active UI | Preserve and review as a candidate integration, not a released version. |
| Website main | 8f4ba6a plus substantial uncommitted work | Next public site; local infra and product integration references | Separate source/release provenance from product and deployed site. |

Remote master exists even though the original product checkout had no local master tracking ref. A separate mirror was used to inspect it. Main is an ancestor of dev; master has no merge base with that history. Therefore this is not a normal three-way merge problem.

**Recommendation:** use dev as the historical migration base and deliberately integrate the useful local overlay into one reviewed product trunk. Make a protected main the eventual release source, with short-lived codex/feature branches, release tags and a maintained release branch only when on-prem support requires it. Retire long-lived competing main/dev development after convergence. Never reset the dirty tree to achieve that convergence.

Implementation-phase sequence:

1. Inventory and capture the exact dirty product and marketing changes in owner-approved branches; exclude databases, recordings, environment files, caches, generated graph artifacts and deployment ZIPs.
2. Split reviewable packages: configuration/schema; identity/tenancy; upload/storage; jobs/providers; console migration; infrastructure. Preserve working changes while reviewing them.
3. Resolve repository ownership. Near term, keep HomaYar authoritative for product and Homayar.com for marketing, with infrastructure consuming **pinned product releases**, not an untracked nested checkout. If a monorepo is selected later, migrate deliberately with history provenance.
4. Port useful call-center features to v1 permission-aware APIs; keep legacy routes inaccessible to ordinary SaaS users.
5. Migrate historical data through dry-run importers with tenant assignment, source IDs and reconciliation; no live dual writes.
6. Release from a clean checkout in CI. Require image digest, schema revision, model/prompt manifest and test/evaluation evidence.

The untracked website CI already checks for missing nested product sources and contains backend/migration/container jobs. Its presence locally does not mean a clean checkout of either remote can run it. Also, SQLite test fixtures override a PostgreSQL-looking CI environment; add real PostgreSQL integration tests rather than inferring coverage from service declarations. [CI](/Users/emperor/Documents/AI/Homayar.com/.github/workflows/ci.yml), [fixtures](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/tests/conftest.py:15)

Documentation drift is material: the README describes the old product, the local assessment describes a target, the local app exposes meetings, and the live marketing pages make wider enterprise claims. Each release needs a capability manifest that both docs and website consume.

# 4. Current Feature Inventory

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

# 5. Critical Problems

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

# 6. Product Definition

**Category:** enterprise customer-conversation intelligence, with a Persian-first contact-center QA and Voice of Customer entry product.

**Core promise:** turn recorded customer conversations into reviewable quality assessments and recurring operational issues, linked to the original evidence, deployable in the customer's private environment or a qualified cloud region.

The initial buyer is the contact-center/CX leader; the daily champion is a QA manager or supervisor; IT and security approve integration and data handling. The first commercial job is: “Help my supervisors find and resolve the calls and process failures that deserve attention, while reducing manual review time.”

The category can encompass service and sales calls without becoming a generic enterprise assistant. A shared Conversation domain should support call, meeting and other future source types, but the first product navigation and scorecards should serve contact centers. Keep Organization, Workspace, Conversation, Recording, Participant, Evidence and AnalysisRun in the core. Put industry taxonomies and QA rules in versioned configuration.

Do not claim a predictive system where there is only extraction. “Customer explicitly asked to cancel” is an observable signal. “72% churn probability” requires linked outcomes, calibrated validation and monitoring. NPS is survey-based; remove “estimated NPS” from commercial KPI labels. Complaint co-occurrence is a root-cause hypothesis until confirmed by tickets/events or a human.

## Strategic premise and kill criteria

Assumption: Persian-speaking contact centers with 50–500 agents have enough QA labor and unresolved-issue pain to buy this product, and private deployment materially affects procurement. Validate through ten qualified interviews, three data agreements and two paid pilots. If supervisors do not return weekly, review time does not improve by at least 30%, or the buyer will not pay a price that sustains the margin floor, stop broadening scope and revisit the workflow.

Customer value is measured by review throughput, accepted findings and action completion, not graph size or agent count. Persian support alone is not unique: Gong's official language list includes Persian (Iran). A defensible claim must be **measured performance on relevant Persian calls**, plus private operations and local integrations. [Gong language support](https://help.gong.io/docs/what-languages-are-supported-in-gong)

# 7. Product Modules

| Product family | Contents | Buyer / commercial role | Decision |
|---|---|---|---|
| Homayar Platform | Ingestion, transcript/timeline, reliable speakers, evidence, search, identity, tenancy, storage, administration, provider policy | Required foundation; priced through platform fee plus minutes | Core. No separate Graph SKU required to get truthful results. |
| Homayar Quality | QA scorecards, policy/script checks, supervisor review, calibration, appeals, coaching assignments | QA/contact-center manager; first paid value | Core commercial application. Combine QA, basic Compliance and Coach. |
| Homayar Insights | Topics, complaints, product/process issues, trends, executive briefs and evidence-backed Copilot | CX/operations leader; expansion into more teams | Basic issue trends in v1; advanced cross-call/account intelligence is an add-on. |
| Homayar Sales | Objections, buying signals, opportunities and CRM follow-up | Sales/service revenue leader | Optional P2 only after linked CRM outcomes and committed buyer. |
| Homayar Meetings | Uploaded meeting transcripts, decisions and action tracking, later calendar ingestion | Existing enterprise customer seeking private meeting intelligence | Optional P3 extension of the same core; no new standalone brand or bot product initially. |

Graph and Copilot are implementation capabilities and user experiences within Insights, not nine separate products. Advanced regulated policy packs can be commercial add-ons to Quality; fundamental auditability, secure deletion and accurate failure disclosure are not premium safety features.

Meetings should be **B: an optional module**. Preserve the useful existing meeting schema/action UI behind a source-type adapter, but do not let its sunk cost dictate the go-to-market. Reuse recording, participants, transcripts, summaries, evidence and actions. Defer meeting bots, calendar permissions and broad management memory until two existing customers commit enough expansion revenue to fund them.


# 8. Complete Feature Catalogue

The master catalogue below contains 95 independently prioritizable capabilities, including infrastructure and commercial enablement. IDs are stable handoff references. P0/P1 rows map to work packages in section 35 and measurable gates in section 38. A row can contain closely related subfeatures that share a delivery contract; those are decomposed in the work package. P2/P3 entries are evaluated options, not v1 commitments.

Platform provides ingestion, identity, storage and evidence. Quality provides QA, policy checks, review and coaching. Insights provides operational and customer trends and Copilot. Sales and Meetings are optional extensions. Commercial covers the website and pilot funnel. The same security and evidence guarantees apply to SaaS and private deployments.

Feature coverage includes calls, QA, coaching, customer signals, sales, organization trends, management, integrations, infrastructure and deployment. Deliberately rejected or deferred capabilities are still listed so the next agent cannot silently reintroduce them as missing v1 work.

# 9. Feature Prioritization Matrix

Scores are product judgments, not market survey data. Revenue (R), adoption (A), feasibility (F), time-to-market (T, 5 fastest), differentiation (D), enterprise importance (E), and risk (K, 5 highest) each use 1–5. Weighted score = 2 × [2R + 2A + F + T + D + 2E + (6 − K)], giving a 20–100 scale. P0 overrides economics for release blockers. P1 is the commercially necessary entry workflow with score at least 70; P2 is an expansion candidate; P3 is optional, rejected for v1 or dependent on new evidence. A high score cannot override scope, dependency or safety gates.

SaaS/On-prem columns indicate intended support **after the feature's release**, not present availability. Y = both deployment variants; Opt = optional future feature; Contract = provider/deployment dependent. All costs and complexity must still be verified in the pilot.

| ID / Feature | Current state | Target state | Module | SaaS | On-Prem | Revenue | Adoption | Feasibility | TTM | Differentiation | Enterprise | Risk | Score | Priority | Package |
|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| F01 Release/source provenance | W uncommitted | Clean reproducible release | Platform | Y | Y | 4 | 5 | 5 | 4 | 2 | 5 | 2 | 86 | P0 | WP01 |
| F02 CI, migrations and regression suites | S partial | Clean-checkout release gates | Platform | Y | Y | 4 | 5 | 5 | 4 | 2 | 5 | 2 | 86 | P0 | WP01 |
| F03 Secret containment and scanning | M leaked material | Rotated credentials; redacted scans | Platform | Y | Y | 5 | 5 | 5 | 5 | 1 | 5 | 5 | 84 | P0 | WP02 |
| F04 Unified login, refresh, reset, verification | W functional immature | One revocable session system | Platform | Y | Y | 4 | 5 | 5 | 4 | 2 | 5 | 4 | 82 | P0 | WP03 |
| F05 Tenant isolation across DB, files, jobs, search | W partial | Enforced scoped access plus RLS | Platform | Y | Y | 5 | 5 | 4 | 3 | 3 | 5 | 5 | 82 | P0 | WP03 |
| F06 Teams, supervisor and agent record permissions | W missing | Least-privilege operational roles | Platform | Y | Y | 5 | 5 | 4 | 3 | 3 | 5 | 4 | 84 | P1 | WP03 |
| F07 Enterprise OIDC/SAML federation and MFA | D LDAP partial | SSO through qualified identity broker | Platform | Y | Y | 5 | 4 | 4 | 3 | 3 | 5 | 4 | 80 | P1 | WP23 |
| F08 SCIM automated provisioning | Missing | Lifecycle provisioning after buyer demand | Platform | Opt | Opt | 3 | 3 | 4 | 3 | 2 | 4 | 3 | 64 | P2 | — |
| F09 Audit trail and tamper-evident export | W partial | Actor, scope and immutable external sink | Platform | Y | Y | 5 | 5 | 4 | 3 | 3 | 5 | 4 | 84 | P1 | WP20 |
| F10 TLS, encryption, key rotation, restricted networks | S partial | Tested deployment controls | Platform | Y | Y | 5 | 5 | 4 | 3 | 2 | 5 | 5 | 80 | P0 | WP02 |
| F11 Customer-managed keys and IP/session policies | Partial/missing | Dedicated-enterprise policy options | Platform | Opt | Opt | 4 | 3 | 3 | 2 | 3 | 5 | 4 | 68 | P2 | — |
| F12 Adversarial media validation | W functional immature | Bounded decode and channel-safe validation | Platform | Y | Y | 5 | 5 | 5 | 4 | 2 | 5 | 4 | 86 | P0 | WP05 |
| F13 Idempotent upload and ingestion ledger | W partial | Retry-safe ingest and source deduplication | Platform | Y | Y | 5 | 5 | 4 | 4 | 3 | 5 | 3 | 88 | P1 | WP05 |
| F14 Private object storage lifecycle | W partial | Qualified S3/private-storage adapters | Platform | Y | Y | 5 | 5 | 5 | 4 | 2 | 5 | 4 | 86 | P0 | WP05 |
| F15 Retention, deletion, legal hold and subject export | W fields only | Enforced purge and restore tombstones | Platform | Y | Y | 5 | 5 | 4 | 3 | 3 | 5 | 5 | 82 | P0 | WP07 |
| F16 Text PII redaction and access policy | Missing | Redacted downstream copy; restricted original | Platform | Y | Y | 5 | 4 | 3 | 3 | 4 | 5 | 5 | 78 | P1 | WP07 |
| F17 Durable queue, leases, checkpoints and recovery | W partial | No lost/stuck jobs or duplicate effects | Platform | Y | Y | 5 | 5 | 4 | 3 | 3 | 5 | 4 | 84 | P0 | WP06 |
| F18 Usage metering, quotas and plan limits | W functional immature | Exactly-once ledger effects and reconciliation | Platform | Y | Y | 5 | 5 | 4 | 3 | 2 | 5 | 4 | 82 | P1 | WP18 |
| F19 Subscription invoicing and payment adapters | W manual reads | Annual/manual plus optional PSP billing | Platform | Y | Y | 5 | 5 | 4 | 3 | 2 | 4 | 3 | 80 | P1 | WP18 |
| F20 Model/prompt registry and provider capabilities | W partial | Versioned stage-specific provider policies | Platform | Y | Y | 4 | 5 | 4 | 3 | 4 | 5 | 3 | 84 | P1 | WP08 |
| F21 Tenant privacy/region routing and egress denial | W global config | Privacy precedes cost or fallback | Platform | Y | Y | 5 | 5 | 4 | 3 | 4 | 5 | 5 | 84 | P0 | WP08 |
| F22 Provider retry, health and circuit breakers | W partial | Bounded transient retry; typed failures | Platform | Y | Y | 4 | 5 | 4 | 4 | 2 | 5 | 3 | 82 | P1 | WP08 |
| F23 Honest stage states and mock exclusion | D unsafe; W partial | Visible partial/unavailable; zero fake scores | Platform | Y | Y | 5 | 5 | 5 | 4 | 4 | 5 | 5 | 88 | P0 | WP04 |
| F24 Evidence/revision/provenance schema | W partial | Immutable spans and reproducible findings | Platform | Y | Y | 5 | 5 | 4 | 3 | 5 | 5 | 4 | 88 | P0 | WP04 |
| F25 Channel-aware speaker attribution | Missing | Trusted PBX channels before diarization | Platform | Y | Y | 5 | 5 | 4 | 4 | 5 | 5 | 3 | 92 | P1 | WP09 |
| F26 Audio normalization, VAD and quality checks | Partial | Preserve channels and original time offsets | Platform | Y | Y | 5 | 5 | 4 | 3 | 3 | 5 | 3 | 86 | P1 | WP09 |
| F27 Persian and English production STT | D/W MVP | Qualified local and external ASR adapters | Platform | Y | Y | 5 | 5 | 4 | 3 | 5 | 5 | 4 | 88 | P1 | WP09 |
| F28 Persistent GPU workers and bounded batching | Missing | Warm models; measured throughput/fairness | Platform | Y | Y | 5 | 5 | 4 | 3 | 3 | 5 | 4 | 84 | P1 | WP09 |
| F29 Code-switching, terminology and extra languages | W basic | Evaluated glossary and language policies | Platform | Opt | Opt | 4 | 4 | 3 | 2 | 4 | 4 | 4 | 70 | P2 | — |
| F30 Mono and multiparty diarization | Missing | Real anonymous speakers with uncertainty | Platform | Y | Y | 5 | 5 | 3 | 2 | 5 | 5 | 5 | 82 | P1 | WP10 |
| F31 Overlap detection and attribution | Missing | Preserve simultaneous speech and unknowns | Platform | Y | Y | 4 | 5 | 3 | 2 | 4 | 5 | 5 | 76 | P1 | WP10 |
| F32 Known-agent voice embeddings | Missing | Opt-in verified enrollment and revocation | Platform | Opt | Opt | 2 | 2 | 3 | 2 | 3 | 3 | 5 | 46 | P3 | — |
| F33 Timestamp alignment and calibrated confidence | W partial | Word/turn alignment, quality gates | Platform | Y | Y | 5 | 5 | 3 | 3 | 4 | 5 | 4 | 84 | P1 | WP10 |
| F34 Silence, talk ratio, overtalk and speaking rate | D prompt estimates | Deterministic metrics with denominators | Quality | Y | Y | 4 | 5 | 4 | 4 | 3 | 4 | 3 | 80 | P1 | WP11 |
| F35 Hold, transfer and call-state metrics | Missing | PBX-event facts; inference labeled | Quality | Opt | Opt | 3 | 4 | 4 | 3 | 2 | 4 | 3 | 68 | P2 | — |
| F36 Intent, topics, entities and resolution status | D prototype; W generic | Evidence-backed structured extraction | Insights | Y | Y | 5 | 5 | 4 | 3 | 4 | 4 | 4 | 82 | P1 | WP11 |
| F37 Summaries, decisions and key moments | W immature | Grounded concise summary with abstention | Platform | Y | Y | 5 | 5 | 4 | 4 | 3 | 4 | 3 | 84 | P1 | WP12 |
| F38 Action items, owners, deadlines and commitments | W partial | Evidence-linked tasks; no invented owners | Platform | Y | Y | 4 | 5 | 4 | 4 | 3 | 4 | 3 | 80 | P1 | WP24 |
| F39 Persian sentiment journey and escalation signals | D prototype | Validated text labels; uncertainty shown | Insights | Opt | Opt | 4 | 4 | 3 | 3 | 3 | 3 | 4 | 66 | P2 | — |
| F40 Acoustic emotion or personality scoring | Prototype claims | Research only; no employee decision scores | Insights | Opt | Opt | 1 | 2 | 2 | 1 | 1 | 1 | 5 | 26 | P3 | — |
| F41 Versioned custom weighted QA scorecards | Missing | Organization/campaign policy and NA rules | Quality | Y | Y | 5 | 5 | 4 | 3 | 5 | 5 | 4 | 88 | P1 | WP11 |
| F42 Keyword, script and compliance evidence rules | D fixed prompts | Deterministic plus semantic reviewed checks | Quality | Y | Y | 5 | 5 | 4 | 3 | 4 | 5 | 4 | 86 | P1 | WP11 |
| F43 Automated coverage and review sampling | Missing | Measured eligible-call coverage and sampling | Quality | Y | Y | 5 | 5 | 4 | 4 | 3 | 5 | 3 | 88 | P1 | WP14 |
| F44 Supervisor review and score override | Missing | Attribution, reason and original preserved | Quality | Y | Y | 5 | 5 | 4 | 4 | 4 | 5 | 3 | 90 | P1 | WP14 |
| F45 Transcript and turn/speaker correction | W rename only | Versioned edits and dependent recomputation | Quality | Y | Y | 5 | 5 | 4 | 3 | 4 | 5 | 4 | 86 | P1 | WP12 |
| F46 Calibration and appeal workflow | Missing | Independent reviewers and adjudication | Quality | Y | Y | 5 | 5 | 4 | 3 | 4 | 5 | 3 | 88 | P1 | WP14 |
| F47 Basic coaching assignments and progress | D teacher prototype | Supervisor-approved coaching with outcome | Quality | Y | Y | 5 | 4 | 4 | 3 | 4 | 4 | 3 | 80 | P1 | WP14 |
| F48 Personalized exercises and skill graph | Prototype/missing | Validated training effectiveness | Quality | Opt | Opt | 3 | 3 | 3 | 2 | 3 | 3 | 4 | 56 | P2 | — |
| F49 Complaint, product and operational issue trends | D partial | Normalized rates and evidence drill-down | Insights | Y | Y | 5 | 5 | 4 | 3 | 4 | 5 | 3 | 88 | P1 | WP15 |
| F50 Repeat contact and observed FCR | Missing | CRM-linked identity and observation window | Insights | Opt | Opt | 4 | 4 | 3 | 2 | 4 | 4 | 4 | 70 | P2 | — |
| F51 Versioned business graph projection | D browser only | Rebuildable tenant-scoped ontology | Insights | Opt | Opt | 3 | 3 | 3 | 2 | 4 | 4 | 4 | 62 | P2 | — |
| F52 Recurring root-cause and incident relationships | D per-call labels | Evidence-supported hypotheses and validation | Insights | Opt | Opt | 4 | 4 | 3 | 2 | 5 | 4 | 4 | 72 | P2 | — |
| F53 Customer journey, account 360 and risk graph | Missing | Authorized CRM identity/event links | Insights | Opt | Opt | 4 | 3 | 3 | 2 | 4 | 4 | 5 | 64 | P2 | — |
| F54 Cancellation/VIP-risk signals | D unvalidated | Observable risk signals; reviewed action | Insights | Opt | Opt | 4 | 4 | 3 | 3 | 3 | 4 | 4 | 70 | P2 | — |
| F55 Sales objections, intent and opportunities | D prototype | CRM-linked service-to-sales signals | Sales | Opt | Opt | 4 | 3 | 3 | 2 | 3 | 3 | 4 | 60 | P2 | — |
| F56 Churn/conversion/NPS prediction | D unsupported | Do not ship probabilities without labels | Insights | Opt | Opt | 2 | 2 | 2 | 1 | 2 | 2 | 5 | 36 | P3 | — |
| F57 Scoped lexical and semantic search | W LIKE search | Permission-safe hybrid retrieval | Platform | Y | Y | 5 | 5 | 4 | 3 | 3 | 5 | 4 | 84 | P1 | WP17 |
| F58 Evidence-backed analytical Copilot | D template prototype | Typed queries, citations, bounded retrieval | Insights | Y | Y | 5 | 4 | 3 | 2 | 4 | 4 | 5 | 72 | P1 | WP17 |
| F59 Post-call/digest alerts and review queues | D partial | Rate-limited, deduplicated actionable alerts | Quality | Y | Y | 5 | 5 | 4 | 4 | 3 | 4 | 3 | 84 | P1 | WP24 |
| F60 Approval-based CRM/ticket/coaching actions | W internal action CRUD | Preview, approve, execute, audit and reconcile | Insights | Y | Y | 5 | 4 | 4 | 3 | 4 | 4 | 4 | 78 | P1 | WP24 |
| F61 Role-specific dashboards and comparisons | D MVP; W usage only | Operational trends with denominator/drilldown | Insights | Y | Y | 5 | 5 | 4 | 3 | 3 | 5 | 3 | 86 | P1 | WP15 |
| F62 Executive daily/weekly briefs | Missing | Evidence and trend deltas, human feedback | Insights | Opt | Opt | 4 | 3 | 4 | 3 | 3 | 3 | 3 | 66 | P2 | — |
| F63 Asterisk/FreePBX/Issabel recording adapter | D generic webhook | Qualified channel/CDR ingestion package | Platform | Y | Y | 5 | 5 | 4 | 3 | 5 | 4 | 3 | 86 | P1 | WP16 |
| F64 Generic CRM/ticket ID and outcome mapping | Missing | Stable external IDs and reconciliation | Platform | Y | Y | 5 | 5 | 4 | 3 | 4 | 5 | 4 | 86 | P1 | WP16 |
| F65 3CX/Cisco/Avaya/Genesys/Grandstream adapters | Missing | Buyer-funded qualified connectors | Platform | Opt | Opt | 4 | 3 | 3 | 2 | 3 | 4 | 4 | 64 | P2 | — |
| F66 Salesforce/Dynamics/HubSpot/Zoho/Odoo/IR CRM | Missing | Prioritize named customer CRM adapter | Platform | Opt | Opt | 4 | 3 | 4 | 2 | 3 | 4 | 4 | 66 | P2 | — |
| F67 Jira/ServiceNow/Zendesk/Freshdesk adapters | Missing | Approved ticket operations and callbacks | Platform | Opt | Opt | 4 | 3 | 4 | 3 | 2 | 4 | 3 | 68 | P2 | — |
| F68 Versioned REST, scoped API keys and SDK | W partial models | Ingest/status/results with machine auth | Platform | Y | Y | 5 | 4 | 4 | 3 | 3 | 5 | 4 | 80 | P1 | WP16 |
| F69 Signed webhooks and delivery ledger | D weak; W model | Replay-safe events and bounded delivery | Platform | Y | Y | 5 | 4 | 4 | 3 | 3 | 5 | 4 | 80 | P1 | WP16 |
| F70 PDF, CSV, JSON and transcript exports | D PDF; W TXT/JSON/subtitles | Scoped bilingual reports and data export | Platform | Y | Y | 4 | 5 | 4 | 4 | 3 | 5 | 3 | 84 | P1 | WP24 |
| F71 Power BI/Metabase/Tableau connectors | Missing | Read-only tenant-scoped export contract | Insights | Opt | Opt | 3 | 3 | 4 | 3 | 2 | 4 | 3 | 64 | P2 | — |
| F72 Persian RTL/English LTR, dates, digits, accessibility | W partial | Consistent bilingual usable workflows | Platform | Y | Y | 5 | 5 | 4 | 3 | 4 | 5 | 3 | 88 | P1 | WP22 |
| F73 Mobile supervisor access | Partial responsive UI | Responsive review, no native app | Quality | Opt | Opt | 3 | 3 | 4 | 3 | 2 | 3 | 2 | 62 | P2 | — |
| F74 Air-gapped local STT/LLM/embed package | S prototype | No mandatory external dependencies | Platform | Y | Y | 5 | 5 | 3 | 2 | 5 | 5 | 5 | 82 | P1 | WP19 |
| F75 Offline license, upgrade and secure support bundle | Missing/partial | Signed artifacts; recoverable upgrades | Platform | Y | Y | 5 | 4 | 4 | 3 | 4 | 5 | 4 | 82 | P1 | WP19 |
| F76 Kubernetes, HA and very large scale | S single-host templates | Measured scale-triggered deployment option | Platform | Opt | Opt | 3 | 3 | 3 | 2 | 2 | 4 | 4 | 58 | P2 | — |
| F77 Hybrid local data / optional cloud management | Missing | Policy-controlled optional telemetry | Platform | Opt | Opt | 4 | 3 | 3 | 2 | 4 | 4 | 5 | 64 | P2 | — |
| F78 Metrics, traces, cost, queue/GPU health and SLOs | W basic logs/health | End-to-end operational visibility | Platform | Y | Y | 5 | 5 | 4 | 3 | 3 | 5 | 3 | 86 | P1 | WP20 |
| F79 Encrypted backups and restore drills | S scripts | Verified RPO/RTO and deletion-safe restores | Platform | Y | Y | 5 | 5 | 4 | 3 | 3 | 5 | 5 | 82 | P0 | WP20 |
| F80 Persian gold corpus and slice benchmarks | Missing | Consent, held-out calls and adjudication | Platform | Y | Y | 5 | 5 | 3 | 2 | 5 | 5 | 4 | 84 | P0 | WP13 |
| F81 AI regression and model/prompt promotion gates | Missing | Versioned quality reports and rollback | Platform | Y | Y | 5 | 5 | 4 | 3 | 5 | 5 | 4 | 88 | P1 | WP13 |
| F82 Security scans and independent penetration test | S scan template | Remediation evidence before enterprise release | Platform | Y | Y | 5 | 4 | 4 | 3 | 3 | 5 | 4 | 80 | P1 | WP23 |
| F83 Website claim/evidence register | L unsupported claims | Version-linked truthful commercial pages | Commercial | Y | Public site | 5 | 5 | 5 | 5 | 4 | 5 | 4 | 92 | P0 | WP21 |
| F84 Consented demo, lead capture and pilot funnel | L/S divergent | One demonstrable product-to-sales flow | Commercial | Y | Public site | 5 | 5 | 5 | 4 | 3 | 4 | 2 | 88 | P1 | WP21 |
| F85 Customer ROI calculator and pilot report | Missing | Measured labor benefit, explicit assumptions | Commercial | Y | Public site | 5 | 4 | 5 | 4 | 3 | 4 | 2 | 84 | P1 | WP21 |
| F86 Product/API/install/admin/security documentation | D/S drifting | Versioned role and deployment guides | Platform | Y | Y | 4 | 5 | 4 | 4 | 3 | 5 | 3 | 84 | P1 | WP22 |
| F87 Uploaded private meeting intelligence | W prototype | Optional shared-core module | Meetings | Opt | Opt | 2 | 3 | 4 | 3 | 2 | 2 | 3 | 52 | P3 | — |
| F88 Teams/Zoom/Meet/Webex bots and calendar capture | Missing | Fund only through qualified expansion | Meetings | Opt | Opt | 2 | 2 | 3 | 2 | 1 | 2 | 4 | 40 | P3 | — |
| F89 Real-time in-call assistance and alerts | Missing | Separate streaming research track | Quality | Opt | Opt | 3 | 3 | 2 | 1 | 2 | 3 | 5 | 48 | P3 | — |
| F90 Survey-linked actual CSAT/NPS | Missing | Measured feedback linked to conversations | Insights | Opt | Opt | 3 | 4 | 4 | 3 | 3 | 4 | 3 | 70 | P2 | — |
| F91 Organization incidents and anomaly detection | D partial | Minimum-volume alerts, confirmed incidents | Insights | Opt | Opt | 4 | 4 | 3 | 2 | 4 | 4 | 4 | 70 | P2 | — |
| F92 Tenant provider/scorecard/retention administration | W partial | Audited versioned policy configuration | Platform | Y | Y | 5 | 5 | 4 | 3 | 4 | 5 | 4 | 86 | P1 | WP22 |
| F93 Email/webhook then SMS/Teams/Slack/Telegram alerts | D email only | Qualified delivery adapters with redaction | Quality | Opt | Opt | 3 | 4 | 4 | 3 | 2 | 3 | 3 | 64 | P2 | — |
| F94 Audio PII masking | Missing | Redacted playback derivative with aligned spans | Platform | Opt | Opt | 4 | 3 | 3 | 2 | 4 | 5 | 5 | 68 | P2 | — |
| F95 ISO/SOC assurance program | L claims unverified | Evidence-led program; no invented certificate | Platform | Opt | Opt | 4 | 3 | 3 | 1 | 3 | 5 | 4 | 66 | P2 | — |

# 10. Graphify / Knowledge Graph Architecture

## Current assessment and decision

The current Neo4j schema groups per-call reports around calls and agents. It is useful as an early exploration model, but conflates transactions with analytics, has no tenant boundary in the legacy queries, and does not establish cross-call entity resolution or evidence-backed root causes. The local graphify-out artifacts map repository code; they must never be marketed as customer knowledge-graph intelligence.

**Keep graph as an optional derived projection. Start with relational issue/customer links and prove two business queries before operating Neo4j in every installation.** SQL joins, materialized aggregates and a small relationship table are adequate for v1. Introduce graph when multi-hop queries are repeatedly valuable and measurably simpler or faster there. Do not place passwords, identity sessions, billing or raw transcripts in the graph.

## Minimal ontology

| Entity | Stable identity / key data | Core relationships and evidence |
|---|---|---|
| Organization, Workspace | Tenant boundary, region, policy revision | Organization HAS_WORKSPACE Workspace; all projected entities carry tenant/workspace scope. |
| Team, Department, Branch | Customer external ID plus tenant | Agent MEMBER_OF Team, Team WITHIN Department; membership has valid-from/to dates. |
| Agent, Participant | HR/PBX ID when verified; anonymous conversation-local speaker otherwise | Agent HANDLED Conversation; Participant SPOKE Turn. Never infer employee identity solely from a cluster label. |
| Customer, Account, Contact | CRM IDs first; tenant-scoped pseudonymous linkage otherwise | Contact BELONGS_TO Account; Customer PARTICIPATED_IN Conversation. Unknown identities remain separate. |
| Conversation, Recording, TranscriptRevision, Turn | UUID plus immutable source ID; integer millisecond offsets | Conversation HAS_RECORDING; revision HAS_TURN; evidence identifies recording/revision/turn/span. |
| Topic, Intent, Product, Service | Controlled taxonomy ID, aliases, version | Conversation DISCUSSED Product; Evidence SUPPORTS Intent/Topic occurrence. Distinguish taxonomy from observations. |
| IssueOccurrence, IssueCluster, Incident | An occurrence is observed in one call; cluster groups reviewed similar occurrences | Occurrence OBSERVED_IN Conversation; MEMBER_OF Cluster; Cluster RELATED_TO Incident. |
| RootCauseHypothesis | Statement, confidence method, reviewer, status | Hypothesis EXPLAINS Cluster only as a hypothesis; CONFIRMED_BY Ticket/Event/Reviewer where available. |
| Ticket, Opportunity, Outcome | Authoritative CRM/ticket external IDs and timestamps | Conversation LINKED_TO Ticket; Opportunity EVIDENCED_BY Turn; Outcome RESOLVES Issue. |
| RuleVersion, Evaluation, Finding | Frozen policy/model/prompt/input revision | Evaluation APPLIES RuleVersion; Finding SUPPORTED_BY Evidence; Override SUPERSEDES Finding. |
| Commitment, Action, CoachingAssignment, Skill | Source text, assignee resolution, due date, completion | Conversation PRODUCED Action; Agent ASSIGNED Coaching; reviewed finding SUGGESTS SkillGap. |

Sentiment, emotion and risk are **dated observations or scores with provenance**, not permanent characteristics of a person. Locations, devices, systems and campaigns are optional customer taxonomies. Avoid automatically creating a node for every noun.

Each inferred relationship stores source evidence IDs, extraction run, model/prompt/schema version, confidence method, valid time, ingestion time and review status. Store edge identity deterministically from tenant, source occurrence and relation type. Keep extractor guesses separate from confirmed CRM facts. Entity resolution uses exact external IDs, then controlled aliases, then reviewed candidate matching; never merge all equal names or phone substrings across customers or tenants.

## Business cases and go/no-go criteria

| Graph feature | Who benefits / business value | Simpler first approach | Gate for graph implementation |
|---|---|---|---|
| Recurring issue/root-cause discovery | Operations manager identifies one process fix affecting many calls | Issue taxonomy and SQL trend counts | At least two confirmed multi-hop paths improve investigation time by 30% over the relational baseline. |
| Call-to-ticket linkage and unresolved chains | Supervisor sees repeat calls despite supposedly closed tickets | CRM IDs and joins | Cross-system chains longer than simple joins are repeatedly used. |
| Customer journey/account risk | Account manager sees unresolved promises and repeat complaints | Time-ordered customer event view | Accurate entity resolution and access policies first; measure accepted account actions. |
| Systemic failure and product issue propagation | Product/IT leader sees a common incident across branches/products | Time-series incident grouping | Confirm graph adds explanations beyond co-occurrence. |
| Agent skill/training graph | QA leader targets recurring skill gaps | Scorecard aggregates and coaching assignments | Demonstrated training effectiveness; no automatic punitive ranking. |
| Graph-powered Copilot | Analyst asks relationship questions with traceable supporting calls | Typed SQL tools plus retrieval | Query correctness, bounded traversal and zero ACL leakage in adversarial tests. |
| Organization bottlenecks | Operations leader finds handoff delays and unresolved ownership | Ticket handoff timestamps | Requires actual event/assignment data; conversations alone cannot prove bottlenecks. |

Projection pipeline: committed PostgreSQL outbox event → idempotent projector → versioned graph schema → reconciliation counts → ACL-aware retrieval. Graph outage never prevents upload, QA, billing or source-of-truth writes. Delete events remove projected nodes/edges and tombstone their source IDs. A rebuild from authoritative data must reproduce node/edge counts and evidence links. Keep graph query templates server-owned, parameterized, tenant-scoped, read-only and cost-bounded; no unrestricted generated Cypher.

# 11. Target Technical Architecture

## Recommendation: modular monolith plus specialized workers

Retain FastAPI and the useful React console. Keep the SEO-facing Next website separately deployable. Use one Python domain codebase with a small number of independently scalable process types: API, orchestration/CPU workers, GPU inference workers, scheduler/projector. This provides isolation where resource profiles differ without a distributed service for every “agent.”

~~~mermaid
flowchart TD
  Sources[PBX recordings / uploads / CRM metadata] --> Ingest[Authenticated ingestion gateway]
  Ingest --> Quarantine[Private quarantine and bounded media validation]
  Quarantine --> Objects[Private object storage]
  Ingest --> Tx[PostgreSQL: tenant, conversation, job, usage, outbox]
  Tx --> Dispatch[Outbox dispatcher and recovery scheduler]
  Dispatch --> Queue[Redis-compatible broker: explicit CPU/GPU queues]
  Queue --> Audio[Channel inspection / normalization / VAD]
  Objects --> Audio
  Audio --> ASR[Warm ASR workers]
  Audio --> Speakers[Channel attribution or real diarization]
  ASR --> Align[Aligned transcript and evidence revisions]
  Speakers --> Align
  Align --> Signals[Deterministic signal and rule engine]
  Align --> AI[Structured specialized AI analysis]
  Signals --> Findings[Validated findings / QA / review states]
  AI --> Findings
  Findings --> Tx
  Tx --> Search[Scoped lexical and vector indexes]
  Tx --> Graph[Optional graph projection]
  Tx --> Metrics[Versioned analytics aggregates]
  Search --> Retrieve[Authorized retrieval and typed analytics tools]
  Graph --> Retrieve
  Metrics --> Retrieve
  Retrieve --> API[API / evidence-backed Copilot]
  Tx --> API
  API --> UI[Supervisor / agent / analyst / executive console]
  Tx --> Action[Review and approval / alerts / integration outbox]
~~~

This deliberately replaces a serial “SQL → graph → vector → analytics” chain. These are parallel projections with different responsibilities, not mandatory stages that can all block a call.

## Boundaries and contracts

- **Identity/tenancy:** resolves actor, workspace and team/record permission. HTTP headers select an already-authorized workspace; they never confer access.
- **Ingestion/storage:** records external IDs, content hashes, channel provenance and validation results. API streams to quarantine; CPU-heavy decode runs with resource limits.
- **Orchestration:** durable stage attempts, lease generation, checkpoints, quotas and outbox. Workers receive opaque IDs, resolve scope from the database and validate artifact ownership.
- **Speech:** independent ASR and speaker adapters returning canonical time spans, confidence metadata and availability. Model processes persist across jobs.
- **Intelligence:** deterministic rules and structured extractors. Versioned schemas with strict evidence validation; no arbitrary tool access for transcript content.
- **Review/actions:** decisions, overrides, approvals and external delivery ledger. Product conclusions remain reviewable and reversible.
- **Analytics/retrieval:** consistent metric definitions, normalized denominators, source coverage and row-level authorization.

One stage may fail while another succeeds. Store stage status and expose a derived conversation status. For example, transcript complete + diarization unavailable produces **partial**, with speaker-dependent QA suppressed. Separate unavailable, unconfigured, insufficient confidence, transient provider failure, permanent input failure and cancelled. “Completed” describes a requested capability set, not merely reaching the end of a function.

## Reliability and performance design

Use a transactional outbox because committing a job and publishing to the broker cannot be one database transaction. Dispatch retries are idempotent. A stage attempt is uniquely identified by conversation, input revision, stage, policy/model version and attempt generation. A heartbeat renews leases; a fencing token prevents a stale worker from overwriting a newer attempt. The sweeper requeues overdue eligible work, reconciles stuck reservations and sends exhausted work to a visible dead-letter queue.

At-least-once delivery is acceptable. Exactly-once **effects** are enforced through unique constraints and compare-and-set transitions. Store stage artifacts before marking that stage complete. Retrying an LLM stage must reuse successful ASR output. Cancellation is checked between chunks/stages and before finalization. No blocking inference or full audio buffers inside the HTTP request lifecycle.

Initial SLO proposals: 99% successful completion for valid supported inputs excluding explicitly identified third-party outages; p95 completion within 15 minutes for a 30-minute two-channel call on the qualified reference GPU under admitted load; p95 list/report API response under 1 second at 100k indexed conversations. Treat these as qualification targets, not current performance. Publish load, model, hardware, retention, concurrency and exclusions with every benchmark.

# 12. SaaS Architecture

Use shared PostgreSQL tables with mandatory organization/workspace scope, application authorization and row-level security for standard SaaS. Offer a dedicated database/storage/worker deployment for contracts needing stronger operational isolation. Kubernetes namespaces alone do not isolate application records, object keys, caches or graph queries.

| Isolation option | Benefit | Cost / limitation | Decision |
|---|---|---|---|
| Shared DB + scoped queries + RLS | Lower cost, simpler fleet operations | Requires disciplined connection/session handling and negative tests | Default qualified cloud tier. |
| Database per tenant | Easier customer restore, residency and blast-radius control | More migrations, connections, backups and monitoring | Dedicated enterprise deployment. |
| Namespace/process isolation only | Resource quotas and scheduling | Does not solve data authorization | Complement, never substitute. |
| Hybrid shared/dedicated | Different contract needs without product forks | Routing/control-plane complexity | Support through deployment configuration after shared mode is sound. |

RLS must run under a non-owner application role without BYPASSRLS; force policies where necessary, and test connection-pool reuse so one tenant's context cannot leak to the next. Set tenant context transaction-locally from trusted actor resolution; background tasks use the same contract. Composite tenant/record foreign keys or equivalent invariants prevent cross-workspace references. PostgreSQL documents owner and privileged-role exceptions, so merely enabling a policy is insufficient. [PostgreSQL row security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)

Add organization-level administration over workspaces and teams. Workspace roles govern administration; resource policies determine which agents/calls a supervisor can see. Executives receive aggregates over explicitly authorized teams, not universal transcript access. Support delegated operator access through time-bounded approval and audit; no invisible global impersonation.

Billing is organization/contract-level with workspace allocations. Meter actual accepted audio seconds according to a documented rounding rule, not uploaded file size or estimated duration. Reserve before scheduling; finalize once; release on excluded failure. Keep internal provider costs separate from billable customer units. Define retry, reanalysis, dual-channel and duplicate-import policies; a stereo minute is one conversation minute unless the signed contract states otherwise.

Limits include audio minutes, stored bytes, retention, active users/supervisors, simultaneous jobs, requests, daily exports and Copilot query budgets. Rate-limit per tenant and actor, apply fair scheduling and prevent a large tenant from starving small tenants. Subscription changes create versioned entitlements; do not overwrite paid plans during startup seeding. Handle billing-period rollover and late job completion explicitly.

Each tenant policy specifies allowed processing regions/providers/models, retention by data type, scorecard versions, redaction, integrations and optional modules. Store secret references, never raw API keys in general settings JSON. Region changes require an explicit migration plan. Document subprocessors and customer ownership of recordings. Trial uploads require verified identity, consent and tight quotas; a local demo account must not be enabled in production.

SaaS recovery proposal: multi-zone database, private object replication/versioning consistent with deletion policy, encrypted off-site backups, monitored restore drills; initial RPO ≤24h/RTO ≤8h for standard contracts, stronger targets only after testing and pricing them. A cloud management outage should not erase durable jobs or meter data incorrectly.

# 13. On-Premises Architecture

**Private operation is a supported deployment profile of the same product, not a fork and not a cloud-dependent installer.** Start with a qualified Linux x86-64 Docker Compose package. Provide Kubernetes manifests when a customer already operates Kubernetes or proven load/HA needs justify it.

The package includes API/console, PostgreSQL, a qualified Redis-compatible broker, object storage, CPU workers, GPU ASR/diarization and local LLM/embedding workers. Neo4j is optional. The marketing website is not necessary inside an air gap; ship local help and API documentation instead.

Required distribution manifest: immutable image digests, OS/CPU/GPU support matrix, signed checksums, SBOM, license notices for code and model weights, CUDA/driver compatibility, exact model files, tokenizer/config files, schema revisions and migration/rollback instructions. Pre-download every artifact in a connected staging environment with appropriate licenses accepted; transfer a verified offline bundle. No model download, telemetry, font CDN, analytics beacon, package install, license heartbeat or mandatory email/SMS identity provider at runtime.

The existing API Dockerfile defaults INSTALL_AI_LOCAL to false, while the application defaults to local Whisper. Current on-prem templates do not establish working GPU/model/LLM provisioning. These must become distinct qualified image profiles rather than undocumented operator steps. [API image](/Users/emperor/Documents/AI/Homayar.com/infra/docker/api.Dockerfile:6)

Hardware proposals to benchmark, not certified requirements: a pilot host with 16 CPU cores, 64 GB RAM, encrypted SSD storage and one 16–24 GB NVIDIA GPU for sequential/controlled ASR and diarization; local LLM sizing depends strongly on selected model, quantization and context. A 24 GB card may not hold all models concurrently. Separate model services, time-slice or provision more GPUs after measurement. CPU fallback is an explicit low-throughput profile with revised latency limits, not a promise of GPU-equivalent performance. Size storage from real codec bitrate and retention, including backups and temporary decode space.

Local authentication supports customer OIDC/AD through an identity broker and a controlled break-glass account. No mandatory Google login or SMS. The network policy defaults to deny outbound access. Keys live in customer-controlled secrets storage. Health dashboards and alert destinations operate locally. A support bundle contains versions, sanitized configuration names, counters and error types; raw audio/transcripts, secrets and customer identities are excluded unless a separate customer-approved export is requested.

License metric: annual software license with committed processed-minute band and licensed environments/sites; concurrent worker capacity is an explicit sizing limit. Do not price by GPUs, which penalizes performance upgrades. Use signed offline entitlement files, expiry notice and a documented grace/read-only policy. Expiry must not delete recordings or prevent authorized export. No concealed remote kill switch.

Support economics require a qualified reference stack, installation checklist, upgrade windows, one supported LTS line and a defined responsibility split: customer owns hardware/network/backup target; Homayar owns application package, migrations, model bundle and product diagnosis. Installation and unusual integrations are separately priced services. Before each upgrade, test forward-compatible migrations, backup, restore and rollback to the prior application against the rollback schema window.

**Storage update:** MinIO's community repository is archived as observed on 7 September 2026. Do not adopt that archived release line as the default new long-term dependency. Keep the S3 interface; qualify customer-owned supported S3 storage, a supported commercial option, or SeaweedFS for the reference private package. Existing MinIO deployments require a planned support/migration decision, not immediate destructive replacement. [MinIO repository](https://github.com/minio/minio), [SeaweedFS](https://github.com/seaweedfs/seaweedfs)

# 14. Hybrid Architecture

Recommend a **local data plane first**, with optional cloud fleet management. Audio, transcripts, identity, retrieval, QA evidence and encryption keys remain local. The optional management plane receives approved health/version/license counters only; installation still functions offline. Customers explicitly enable each telemetry class and can inspect the outgoing payload.

A second mode may allow local ASR and redaction followed by cloud reasoning, but it must be opt-in by tenant and data classification. Redaction cannot guarantee anonymization. Sensitive entities, indirect identifiers and business secrets can remain in conversation text. Therefore support: deny cloud; allow selected redacted tasks; allow approved providers/regions. A failed local model must never silently fall back to a cloud API.

Use the same provider contracts and policy engine in SaaS, private and hybrid. Bundle a stable schema and version negotiation for fleet management. Signed outbound polling is preferable to opening inbound administrative ports. Cloud-control loss cannot interrupt local processing, authentication, deletion or export. Defer remote fleet orchestration until enough installations justify its operating cost.

# 15. AI / STT / Diarization Architecture

## Speech pipeline

Keep the original recording immutable. Inspect content, duration, channel count, sample rate, clipping, empty channels and decode validity. Normalize a derivative to the selected model's input format while retaining an exact mapping to original timestamps. Do not downmix stereo before checking whether PBX channels represent distinct parties. Denoising is optional and must beat the original recording on a held-out set; aggressive denoising can destroy speech evidence.

Priority order for speaker attribution:

1. **Trusted separated channels:** use channel-to-role mappings from verified PBX metadata. Detect silent/duplicated channels, crosstalk and transfers. Stereo alone does not prove agent/customer identity.
2. **Mono or genuinely mixed channels:** run a real diarization model, preserve anonymous speaker IDs and overlap intervals, then align transcript words/turns by temporal overlap. Keep ambiguous words unknown instead of choosing a confident-looking label.
3. **Role resolution:** use authenticated PBX agent IDs, CRM/call metadata and optional human review. A diarization cluster is a voice cluster, not a named person or agent role.
4. **Known-voice enrollment:** optional later, with explicit customer/employee policy, consent where required, minimum enrollment quality, calibrated similarity thresholds, false-accept testing and deletion. Unknown customers remain anonymous unless a reliable CRM link exists.

Evaluate pyannote community-1 as the first mono adapter. Its model card documents local/offline operation and an exclusive-diarization output useful for transcript reconciliation. The pipeline automatically downmixes multichannel input, which is precisely why Homayar must perform channel inspection first. Preserve both true overlapping diarization and an exclusive display view; the latter must not erase overtalk evidence. Code and model licenses differ and must be recorded separately. Persian performance is unproven until measured on Homayar's corpus. [Model card](https://huggingface.co/pyannote/speaker-diarization-community-1)

Use faster-whisper/CTranslate2 as the initial local ASR engine. Benchmark multilingual large-v3 and an appropriate faster multilingual variant against the current base model; do not assume an English-focused distilled model is appropriate for Persian. The official project supports quantization and batched inference, but its benchmark is not a Homayar throughput promise. [faster-whisper](https://github.com/SYSTRAN/faster-whisper)

GPU workers load one qualified model configuration per process, warm it before readiness and retain it across jobs. The current W adapter caches only inside a provider instance, while a new bundle is created per job; fix the lifetime boundary. Use separate CPU validation and GPU queues, one explicitly configured consumer for each queue, bounded dynamic batches and per-tenant fairness. Admission control checks available model memory, queue age and requested work. Avoid multiplying loaded models through a generic Celery concurrency setting.

Long recordings use VAD-aware chunks with controlled overlap and original time offsets, ASR checkpoints, de-duplication across chunk boundaries and conversation-level reconciliation. Use available full-recording speaker embeddings/clustering where feasible; independently diarizing chunks without reconciliation causes speaker-ID drift. Persist partial transcript artifacts after each completed chunk. Retry only failed chunks/stages; validate empty speech, truncated media, wrong duration, no-speech hallucinations and unsupported codecs. Expose partial transcript and excluded time intervals explicitly.

Language policy allows selected language, auto detection and code-switching flags. Store language per segment when supported; one global language code cannot describe every multilingual call. Glossaries include product names, Persian/Arabic character variants, numbers and English technical terms. Prompt hints or hotwords are provider-specific aids, not guaranteed vocabulary customization. Keep raw text, display-normalized text and search-normalized text separate.

## Intelligence stages

| Stage | Implementation | Output and trust rule |
|---|---|---|
| 1. Signal intelligence | Media/PBX event processing, VAD and speaker intervals | Duration, silence, channel activity, talk intervals and quality flags; no LLM-derived “measurements.” |
| 2. Conversation structure | Qualified extraction model plus schema/range validators | Intents, topics, entities, key moments, explicit requests, decisions and outcomes with evidence. |
| 3. Specialized analysis | Versioned QA/rule evaluators; targeted AI calls only when required | Criterion-level scores/findings; abstain when speaker/text/evidence quality is insufficient. |
| 4. Organization intelligence | SQL aggregates, approved taxonomies and trend statistics | Coverage-adjusted rates, repeat issues and changes; no counts extrapolated from a retrieved sample. |
| 5. Relationship intelligence | Optional graph projection and reviewed entity linking | Cross-call relationships with provenance; distinguish inferred and confirmed edges. |
| 6. Decision support | Reviewed recommendations and approval workflow | Suggested actions and measured outcomes; no autonomous consequential decision. |

Replace the fixed four-agent fan-out with a configurable directed pipeline. Run one structured extraction pass where it serves several analyses, deterministic QA checks alongside it, and additional focused evaluations only when the scorecard demands them. Coaching derives from accepted findings and supervisor goals. It should not spend another model call inventing advice for every call.

## Deterministic versus AI

Deterministic with qualified inputs: duration; VAD speech/silence intervals; per-speaker talk ratio; overlap duration; words per speaking minute; literal keyword occurrence; exact script phrase presence. A temporal overlap is not automatically an intentional interruption. Define an interruption heuristic and label it separately. PBX events determine actual hold/transfer; audio-only estimates must carry a different metric name.

AI-based: paraphrased intent, empathy evidence, summary, likely issue category, explicit sales opportunity and resolution-quality assessment. Hybrid: compliance, contextual script adherence and weighted QA. A missing ASR phrase is not proof the agent omitted it; low transcription quality routes to review. The engine supports pass, fail, not applicable and insufficient evidence. Scores use a documented weighted denominator excluding approved NA criteria; missing evidence cannot become a default passing score.

## Provider abstraction

Define ASR, diarization, embedding, reranking and structured-generation ports with capability metadata: supported language/task, timestamps, channel support, schema mode, context limit, latency/cost, privacy region, offline availability, endpoint authentication, model/license revision and benchmark status. Keep a per-stage tenant policy frozen on every run.

Routing order: legal/contractual eligibility and privacy → task/language capability and minimum quality → health/capacity → cost/latency. Support native adapters for whichever OpenAI, Anthropic, Google, GLM/Z.ai, DeepSeek or Qwen API is actually chosen; “OpenAI-compatible” does not imply identical structured-output/auth/token behavior. Support local vLLM/private endpoints; Ollama may suit development or small private pilots after qualification. API aggregators are optional, identified subprocessors, never a mandatory dependency.

Use bounded exponential backoff with jitter for transient errors, provider-specific rate limits, Retry-After handling, circuit breakers and deadlines. Invalid schema or disallowed provider is a typed failure, not a reason for endless retry. Fallback must remain inside the tenant's approved policy and equivalent quality tier. Record original and fallback model, token usage, latency, cost, schema version and failure reason; do not silently mix providers in a report. Treat the transcript as untrusted data and restrict model tools to read-only bounded retrieval.

## AI evaluation and Persian advantage

Build a consented, access-controlled corpus with train/development/test separation by customer, speaker and time. Start with at least 20 hours of genuinely representative Persian contact-center audio, including a frozen 5-hour held-out set and a separate English/mixed-language set. Increase minority-accent and rare-event coverage before making slice claims; small samples need confidence intervals and explicit insufficiency warnings. Double-annotate at least 20% and adjudicate disagreement. Do not use the evaluation set for prompt tuning.

Test formal/colloquial Persian, Tehran and other represented Iranian accents, 8 kHz telephony, mobile/noisy calls, stereo/mono, code switching, English technical terms, overlapping speech, product names, dates, IDs, financial amounts and negation. Store real sensitive IDs only under the approved data policy; synthetic fixtures complement but cannot replace authentic acoustic variation.

| Task | Evaluation | Proposed initial release gate, to be frozen after baseline |
|---|---|---|
| ASR | WER and CER under published Persian normalization; verbatim and normalized number/entity accuracy | Overall held-out WER ≤20%; critical supported slice ≤30%; critical entity exact match ≥95% or route the field to review. These are targets, not achieved results. |
| Diarization | DER including overlap, JER, speaker-count error; publish collar and overlap policy | DER ≤15% on the agreed mono contact-center set; report harder slices separately; stereo trusted-role accuracy ≥98% excluding verified unknowns. |
| Timestamp/evidence alignment | Human-checked span offset/error and resolvable citations | 100% cited spans resolve; ≥95% reviewed key moments start within 1 second; unknown/ambiguous alignment is shown. |
| Intent/issues | Macro-F1 and class precision/recall, class imbalance report | Macro-F1 ≥0.85 on supported taxonomy; critical high-cost findings require precision ≥0.95 or human confirmation. |
| Sentiment | Macro-F1 by language/domain; calibration and annotator agreement | Research baseline first; do not ship a generalized emotion claim. Sentiment requires ≥0.80 macro-F1 on the supported slice before commercial labeling. |
| QA | Human criterion agreement, weighted kappa, score MAE and bias slices | Kappa ≥0.75; score MAE ≤8/100 against adjudicated QA; critical violations precision ≥0.95. |
| Summary/actions | Factual support, coverage, owner/date accuracy, evidence support | ≥95% audited atomic claims supported; ≥95% explicit owner/date precision; 100% structural citation validity; no invented severe allegation in release set. |
| Copilot | Typed query correctness, evidence precision, abstention and ACL attacks | ≥95% answer correctness on 100 approved business questions; 100% exact deterministic counts; zero cross-tenant/role leaks. |
| Churn/sales prediction | Temporally held-out outcomes, calibration, precision/recall/lift and intervention impact | Not in v1. Build only after sufficient outcome labels and baseline comparison; score labels as signals meanwhile. |

Confidence must state its origin: ASR likelihood, calibrated classifier probability, diarization similarity or human judgment. An LLM's self-reported 0.95 is not a calibrated probability. Track abstention and coverage so a system cannot “improve accuracy” by hiding most difficult calls. Model/prompt changes require a signed evaluation report, slice comparisons, cost/latency checks, rollback manifest and staged promotion.

# 16. Data Architecture

| Store | Authoritative contents | Exclusions / operating rule |
|---|---|---|
| PostgreSQL | Identity references, organizations/workspaces/teams, conversations, metadata, transcript revisions/turns, findings/QA, reviews, jobs/stages, ledger, integration state, policies, audit indexes | No bulk audio; avoid an untyped JSON blob for every business entity. |
| Object storage | Original and normalized audio, chunk artifacts, redacted derivatives, exports and model-independent large artifacts | Private keys/paths; lifecycle tied to database policy and deletion ledger. |
| Redis-compatible service | Broker, locks/short-lived coordination, cache and rate limits | Never the sole job, billing, audit or retention record. |
| pgvector + lexical index | Tenant/record-scoped chunks and embeddings derived from versioned transcript text | Deletable/rebuildable; embedding model/version and ACL metadata required. |
| Optional Neo4j | Relationships needed for validated graph questions | No auth secrets, raw sensitive profiles or sole copies of business facts. |

Use Conversation with a source_type discriminator and source-specific metadata to evolve the current Meeting domain without a parallel platform. Map the existing meeting UUIDs to conversation UUIDs through a compatibility layer; migrate API paths with a documented deprecation window. Preserve external PBX call IDs independently from internal UUIDs. A phone number is not a globally trustworthy customer identifier.

Core additions: OrganizationMembership, TeamAssignment with valid time, ConversationParticipant, ChannelMapping, TranscriptRevision, Word/Turn, EvidenceSpan, AnalysisRun, StageAttempt, SignalMetric, TaxonomyVersion, RuleVersion, ScorecardVersion, CriterionResult, Review/Override/Appeal, ExternalRecord, IngestionEvent, OutboxEvent, DeletionJob, LegalHold and PolicyRevision. Keep the existing useful UsageReservation/UsageEvent and session models after review. Index by workspace, date, state and external source ID; partition high-volume tables only after measuring query and maintenance needs.

Evidence identity should survive reanalysis: a finding references an immutable transcript revision and original audio offsets. A correction creates a new revision; it does not rewrite the evidence cited by an older decision. Recompute dependent scores, mark older answers superseded, update projections and keep audit history. Store raw model responses only where policy permits, with short retention and restricted access; sanitized structured outputs are the normal product surface.

## Retention and deletion

Proposed defaults for a pilot: original audio 30 days, transcript/findings 90 days, minimal operational/audit metadata 365 days, all customer-configurable within agreed policy. These are product defaults to negotiate, not statutory retention requirements. On-prem customers own their retention policy. Use explicit values for immediate deletion, finite days and indefinite/legal hold; zero must have one unambiguous API meaning.

Deletion pipeline: authorize request → identify subject/conversation scope → evaluate legal hold → immediately deny normal access → enqueue durable deletion → remove originals/derivatives/exports → remove lexical/vector/graph projections → minimize relational records according to policy → record a non-content completion receipt. Retry failures and reconcile orphan objects. Define when backups age out and reapply tombstones during restore; never promise instantaneous erasure from immutable backups. Encrypt backups and isolate their keys and retention. Right-to-delete workflows must resolve actual customer identity and scope before deleting unrelated callers.

Anonymization is not synonymous with replacing a name. Keep reidentification maps separately encrypted if pseudonymization is used. Redacted analysis text and redacted audio are different features; do not claim the latter from text masking. Legal hold requires an authorized custodian, reason, scope, expiry/review and audited release.

# 17. Security Architecture

## Current findings and scope

The secret scan traversed **15 reachable commits, 8,683 blobs and approximately 72 MB** in the mirrored product refs. It detected credential-shaped material at 11 source/binary locations in master history. This is a pattern-based audit, not proof of complete absence of other secrets. Credential validity and ownership were not tested. No secret values are included here.

| Location in master history | Historical scope | Severity | Remediation |
|---|---|---|---|
| app.py:17; chat_intell.py:6; org_intell.py:7 | Present in master tip 3766bc90 and initial commit 19a2b2ae, with intervening master history | P0 | Revoke/rotate associated provider credentials, review usage/access, replace code defaults with secret references. |
| codes/common_issues/common_issues_Json.py:8; codes/nlp_db_acces.py:6 | Same reachable master history | P0 | Same response; review copied examples and downstream repositories. |
| __pycache__/chat_intell.cpython-313.pyc; __pycache__/org_intell.cpython-313.pyc; codes/QC/Agentic/__pycache__/qc_agentic.cpython-313.pyc | Same reachable master history | P0 | Binary artifacts can preserve credentials; include them in cleanup and redistribution inventory. |
| codes/__pycache__/nlp_db_acces.cpython-313.pyc; codes/common_issues/__pycache__/common_issues_Json.cpython-313.pyc; codes/resume_analysis/__pycache__/resume_analysis.cpython-313.pyc | Same reachable master history | P0 | Same response; exclude compiled caches from future source artifacts. |

The scanner also identified known defaults/placeholders and short hardcoded assignments requiring review. These are not all live leaked credentials. Historical bootstrap password removal in a1ad61a does not erase earlier history. The untracked local product environment contains nonempty provider/auth/database credential settings; that alone is not a leak, but those files must never enter source, container layers or the website ZIP. Rotation and history rewriting are recommendations only; neither was performed.

Legacy authentication only decodes JWT in its common dependency and does not consult the Redis blacklist. Main/dev overlapping auth implementations therefore do not share a single revocation boundary. Legacy self-update accepts a broad UserUpdate object including role-related fields, while authorization compares token subject and path identity; treat privilege-field editing as a security defect to remove and test, without assuming the ID mismatch is a proven live escalation. The legacy graph's default MATCH-all path returns complete node properties, potentially including user credential hashes. [Legacy auth](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/middleware/auth.py:12), [update route](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/routers/auth.py:88), [graph serializer](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/services/graph_service.py:22)

The W v1 stack is materially better: session hashes, active/revoked checks, session version, workspace membership checks, permission dependencies and CSRF protection. Preserve it, then prove refresh reuse detection, concurrent revocation, password reset invalidation, verified account linking and secure cookie behavior. A public share requiring “authentication” currently checks that a user exists, not necessarily membership in the source workspace; that can be a deliberate bearer-link product policy, but must not be described as organization-only access. Default private contact-center sharing to disabled or named authorized recipients. [Actor/workspace boundary](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/security/context.py:98), [share access](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/routers/v1/shares.py:247)

## Target threat controls

| Threat | Control | Required proof |
|---|---|---|
| Cross-tenant/role data leak | Scoped repositories, DB policies, private object checks, ACL-filtered search/graph/Copilot, restricted shares | Adversarial two-tenant tests for every read/write/download/export/job/share path and background task. |
| Token theft/reuse | HttpOnly secure cookies, short access lifetime, rotating hashed refresh family, CSRF/origin controls, revocation | Stolen/replayed/expired/rotated token scenarios; no privilege trust from client-decoded claims alone. |
| Malicious media and SSRF | Allowlisted media signatures, bounded ffprobe/decode sandbox, size/duration expansion limits, no arbitrary fetch URL | Corrupt/truncated/polyglot/oversized inputs fail safely; workers cannot reach metadata/admin networks. |
| Webhook spoof/replay | Tenant-scoped HMAC over raw body, timestamp, nonce/delivery ID, secret rotation and rate limits | Invalid signature/stale/replayed/duplicate events rejected or idempotently ignored. |
| Prompt injection/data exfiltration | Transcript treated as data, schema validation, read-only allowlisted tools, no hidden cloud fallback | Malicious transcript and retrieved-document tests cannot invoke actions or escape scope. |
| Secrets/logging exposure | Secret manager/reference, redaction at source, no audio/text in ordinary logs, allowlisted diagnostics | Canary-secret scanning of logs, traces, support bundles and image layers. |
| Supply-chain compromise | Locked dependencies/model hashes, SBOM, signed release images, license/CVE scanning, non-root/read-only runtime | Clean install, signature verification, critical vulnerability disposition and rollback drill. |
| Internal misuse | Least privilege, separate operator role, audited just-in-time support access, review approvals | Access review and audit reconstruction of one export and one override. |
| Data loss | Encrypted backups, independent failure domain, restore rehearsals and deletion tombstones | Timed restore within contracted RPO/RTO with private object access rechecked. |

Do not expose Neo4j, PostgreSQL, Redis or object storage consoles publicly. TLS is required at the actual ingress, not merely a response header. Validate LDAP certificates and service-account scope; prefer federation over storing customer directory passwords. Apply encryption at rest to database volumes, object storage and backups, and separate key rotation from data migration. Do not assume a Docker bridge or namespace supplies all these controls.

## Compliance and procurement

Prepare an ISO/IEC 27001-aligned risk register, asset/access inventory, incident process and supplier management. SOC 2 readiness is an evidence program; neither a code scan nor a security page is a certification or attestation. No Homayar certification was verified. [ISO description](https://www.iso.org/standard/27001)

For each target country/customer, legal/procurement review must determine recording notice/consent, employment monitoring, voice-biometric rules, residency, retention, international transfers, processor contracts and permitted provider procurement. Iranian governmental/banking requirements cannot be inferred from generic international checklists. US-linked cloud/software procurement for Iran also requires eligibility and sanctions/export review; a communications authorization is not a blanket authorization for every enterprise analytics sale. No legal conclusion on Homayar's eligibility is made here. [OFAC General License D-2](https://ofac.treasury.gov/system/files/126/iran_gld2.pdf)

# 18. Enterprise Features

Prioritize enterprise controls needed for the initial buyer, with evidence attached to each release.

| Capability | v1 decision | Notes |
|---|---|---|
| OIDC SSO; SAML through broker | P1 for enterprise contracts | Integrate the customer's IdP; qualify Keycloak for offline federation when needed. Avoid writing SAML yourself. |
| MFA and session controls | P1 via IdP; strong local admin fallback | Require privileged MFA, session revocation and idle/max lifetime; SMS alone is not the preferred admin factor. |
| LDAP/AD | P1 through supported broker/federation | Existing legacy code is a reference, not the new identity boundary. |
| SCIM | P2, buyer-driven | OIDC login does not provide deprovisioning; retain an explicit administrative removal process until SCIM exists. |
| Workspace/team/agent permissions | P1 | Supervisor sees assigned teams; agent sees their allowed calls/reviews; analyst gets permitted redacted data; executive gets aggregates. |
| Audit and support access | P1 | Append-only application events plus externally protected sink, export and time-bounded support authorization. |
| Private networking, TLS and backup encryption | P0/P1 | Required for qualified deployment; no premium tier needed for basic data safety. |
| CMK, dedicated DB, IP allowlist, regional controls | P2 or contract gate | Estimate key recovery, residency and operations cost before quoting. |
| Custom QA, review/calibration/appeal | P1 | Principal enterprise product feature; requires evidence and employee-facing transparency. |
| Retention, deletion, export and legal hold | P0 | Tenant-specific policy with execution proof and auditable exceptions. |
| SLA and disaster recovery | P1 after demonstration | Publish only tested commitments, operating hours, exclusions and escalation responsibilities. |
| Assurance reports/security questionnaires | P1 documentation | State actual tests/dates/scope; do not advertise SOC 2 reports or penetration results that do not exist. |

# 19. Integration Roadmap

Commercial ranking is based on the proposed initial ICP, not measured installation share. Require discovery of real PBX/CRM versions before committing a connector.

| Rank | Integration | Value and implementation approach | Release |
|---|---|---|---|
| 1 | Generic upload, bulk manifest, object-storage import and REST | Fastest paid pilot; stable source IDs, duration/channel metadata, resumable import and reconciliation | P1 |
| 2 | Asterisk / FreePBX / Issabel + generic CDR | Likely practical local PBX entry; recording spool/object import with CDR correlation and separate channels | P1, qualify one real deployment first |
| 3 | Customer's actual Iranian/custom CRM or Odoo | Link customer, ticket, agent and outcomes; often more valuable than another AI model | Generic contract P1; named adapter funded by pilot |
| 4 | Enterprise OIDC/AD/LDAP | Removes procurement blocker and stale access | P1 enterprise |
| 5 | Ticket/event webhooks | Converts findings into measurable follow-up, starts closed-loop ROI | P1 generic; approval required for writes |
| 6 | 3CX and Grandstream | Additional midmarket PBX sources | P2 after documented API/recording entitlement review |
| 7 | Cisco, Avaya, Genesys; SIPREC/recording platforms | Large enterprise value, larger integration/certification burden | P2 buyer-funded; do not claim universal SIPREC support |
| 8 | Salesforce, Dynamics, HubSpot, Zoho | International/customer expansion | P2 by paid demand; least-privilege scopes and field mappings |
| 9 | Jira, ServiceNow, Zendesk, Freshdesk | Incident/ticket operations | P2 named adapters; generic outbound webhook first |
| 10 | Microsoft 365 / Google Workspace | Identity, user directory and approved notifications before calendars | P2; exact tenant/admin consent documented |
| 11 | Teams, Zoom, Google Meet, Webex | Meeting ingestion only after optional module commitment | P3; no bot/recording-permission sprawl in v1 |
| 12 | Power BI, Metabase, Tableau, Grafana | BI exports; Grafana for service telemetry | CSV/JSON P1; scoped connector P2 |

An integration contract includes authentication/secret reference, source tenant, unique event ID, original timestamp/time zone, external conversation/customer/agent IDs, storage reference, channel map, consent metadata and schema version. Handle missing recordings, delayed CDRs, duplicate notifications, transfers, clock skew, deleted CRM records and changed agent extensions. Never identify people by extension alone across time.

Inbound APIs: create ingestion record, upload recording, request processing, get stage status/results, retrieve evidence, export and query approved analytics. Keep processing asynchronous with 202 plus job ID. API keys have tenant/service identity, narrow scopes, expiration, rotation and usage limits. Start with OpenAPI and one generated/example Python client; do not maintain many SDKs before adoption.

Outbound events include conversation.completed/partial/failed, qa.review_required, issue.cluster_updated and action.execution_result. Sign payloads, persist attempts, use exponential retry with jitter, honor recipient rate limits, deduplicate and expose dead letters. Do not put sensitive transcript snippets in notifications by default; link to an authorized report.

Reuse the organization's n8n installation for customer-specific integration prototypes and approved back-office workflows, if it meets the customer's data policy. Keep authoritative authorization, quotas, jobs and evidence in Homayar. Do not embed an unreviewed n8n runtime/license as a mandatory product dependency. Ruflo/Gstack remain engineering tools; LightRAG is not automatically a tenant-safe product knowledge store.

# 20. Business Model

Sell an annual operating system for a contact-center quality team: reliable ingestion, evidence-backed QA, supervisor review, coaching follow-up and issue trends. Minutes determine processing cost; reviewed decisions and better operations determine value. A transcription-only offer would face stronger price competition and underuse Homayar's workflow opportunity.

| Revenue stream | Buyer and economic purpose | Recommendation |
|---|---|---|
| SaaS subscription plus included minutes and overage | QA/CX budget; recurring access, processing and standard support | Primary cloud model, subject to eligible geography/provider procurement. Annual commitment after paid pilot. |
| Private/on-prem annual software license plus support | CIO/operations; controlled infrastructure and data | Mandatory commercial option. Specify supported topology, capacity, languages, maintenance and support responsibilities. |
| Deployment and integration services | IT/project budget; initial installation, identity, PBX and data mapping | Separate fixed-scope fee, milestones and change control. Do not conceal bespoke work inside license price. |
| Quality/VOC expansion | Existing customer with more teams, minutes, workflows or measurable insight adoption | Expand usage and advanced modules after core adoption. Basic evidence, review and security remain core. |
| Managed QA service | Customer lacking enough QA analysts | Partner-led or carefully bounded service; report human-delivery margin separately from software margin. |
| API access | Integrator embedding Homayar analysis into its product | P2 standalone revenue line; same evidence, privacy and metering infrastructure. No separate model platform initially. |
| Private appliance | Customer needing a qualified complete stack | Qualified hardware partner, software/support contract and capacity test; avoid inventory financing initially. |

One-time services are not ARR. Customer-owned GPU purchases are not Homayar recurring software revenue. Count channel revenue net of contractual revenue-sharing treatment; finance should define whether reseller sales are recorded gross or net. Renewals should depend on active supervisor use and reviewed outcomes, not the size of an accumulated recording archive.

The initial business hypothesis is that a 50–250-agent Persian-speaking operation with a QA lead, usable recordings and repeatable scorecards will pay to increase trustworthy review coverage and reduce review effort. A regulated organization may pay more for a qualified private deployment, but will take longer to buy. Neither willingness to pay nor market size has been demonstrated by the repository or website.

Use bottom-up planning: qualified accounts × attainable annual contract value × realistic win rate, then limit by implementation capacity. For illustration only, 100 reachable qualified accounts × $50,000 annual value × 10% wins is $500,000 new annual recurring value before churn. This is a sales scenario, not a TAM estimate. Validate the account count, budget and local-currency purchasing power through interviews and channel data before using it in fundraising.

Track recurring gross margin and implementation contribution independently. A customer requiring a dedicated engineer every month is a services account until the repeated work becomes a supported connector or documented operation. Reject pilots without an operational owner, data authorization, accessible recordings, baseline QA process and a plausible purchasing path.

# 21. Pricing Strategy

Use organization subscription + processing allowance + explicit capacity/support options. Do not charge for every viewer or customer record. Charge by analyzed audio duration once per purchased analysis revision; disclose rules for invalid files, failed processing, partial results, reanalysis and customer-requested extra models. Reserve quota before work, finalize only eligible delivered processing, and reconcile disputes against the usage ledger. Mono and stereo of the same duration should normally have the same buyer-facing minute price, with stereo cost included in the model.

The following USD figures are **commercial experiments**, not validated market prices, active Homayar offers or conversion into Iranian currency. They assume standard integration and no special regulatory certification. Test them with buyers, then quote in an eligible currency with stated tax, payment, price-adjustment and procurement terms.

| Proposed package | Price hypothesis | Included capacity and product | Boundaries |
|---|---|---|---|
| Paid design-partner pilot | $3,000–8,000 once; a defined portion creditable on annual conversion | One queue, one scorecard, up to 20,000 minutes, baseline and outcome report | Integration beyond one standard source quoted separately; two-week measurement period starts after setup. |
| SaaS Quality | $1,500/month equivalent, annual contract; $0.06 additional minute | 10,000 minutes/month; 5 reviewer/admin seats, agent self-review; custom QA, evidence, basic coaching, issue trends and standard exports | Shared service; default 30-day audio/90-day transcript retention; 2 concurrent processing jobs as an initial quota hypothesis. |
| SaaS Operations | $7,500/month equivalent; $0.045 additional minute | 100,000 minutes/month; 25 reviewer/admin seats; core Quality plus supported PBX adapter, scoped Copilot, approved webhooks and enterprise SSO | Initial 10-job concurrency subject to capacity qualification; implementation fee; extra storage and dedicated topology separately priced. |
| SaaS Enterprise | Annual quote with committed volume | More teams, contractual support, qualified residency/dedicated resources and integrations | No unlimited inference, unbounded concurrency or untested SLA. Price floor follows full cost and support allocation. |
| Private Standard | $40,000–60,000/year license and standard support; $8,000–20,000 deployment | One qualified installation, one production environment plus agreed test environment, standard connectors, local models, signed updates | Customer provides qualified hardware/operations; capacity and user bands in order form. No vendor-cloud requirement. |
| Private Enterprise | $90,000–150,000+/year, deployment scoped separately | Larger capacity, stronger support, federation, approved integrations and optional dedicated assistance | HA, DR, many sites, extended hours and custom models costed explicitly; no blanket guarantees. |

These package assumptions should be sold as one platform with two scale bands, not as nine disconnected modules. Graph exploration is an implementation option behind useful insight workflows; it should not be a compulsory fee. Basic Copilot usage can have a generous per-organization request allowance, with query/token budgets to prevent expensive unrestricted scans. Additional reviewers can be sold in packs after buyer research. SSO belongs in the enterprise offering without a punitive per-user surcharge; encryption, tenant isolation, deletion and accurate failure states are universal requirements.

Retained storage should have a stated GB-month allowance or derived recording-duration allowance. Audio format and replication materially affect cost. Bill extended retention separately at an explicit rate, and give administrators forecasts before increasing it. Count archived data in access policies and deletion obligations. Dedicated GPU capacity can be a reserved monthly option with a throughput envelope; do not equate a minute allowance with a latency promise.

At 10,000 monthly minutes the illustrative API operating cost in Section 22 is $605, giving about 60% gross margin on a $1,500 package before sales/R&D. At 100,000 minutes it is $2,050, giving about 73% on $7,500. These are deployment/fleet scenarios, not a claim that every tenant incurs a separate platform. Shared allocations must be measured. Introductory discounts require a documented end date and margin check; do not assume volume makes every low price profitable.

Offer annual prepaid commitments and transparent overage, not a perpetual license with undefined lifetime upgrades. For private installations, contractual capacity can be measured locally with an offline signed license and visible usage counters. Expiration must not destroy data or abruptly lock customer exports; use notice, a bounded grace period and an agreed read/export mode. Avoid remote kill switches. Separate support renewal from ownership of customer data.

The live website currently withholds public pricing, while the dirty local website contains $499/$1,499 references. Neither is evidence of a proven pricing model. Publish package structure and a realistic pilot process before publishing amounts. [Live pricing](https://homayar.com/pricing/)

# 22. Unit Economics

No production GPU benchmark, provider invoice, customer minute distribution or support time study was available. The following is a reproducible **planning model**, with every rate assumed. It is deliberately independent of current vendor price cards; replace it with measured telemetry and eligible vendor quotes before committing customer prices.

## Cost model and workload assumptions

One minute means one minute of recorded conversation, including two channels when present. Assume 30-day compressed audio retention, 90-day transcript retention, bounded summary/QA prompts, one initial successful analysis and no high-availability replica. Failed attempts and reprocessing require a separate overhead multiplier. API prices below are placeholders for a blended provider contract, not quotes from named vendors.

| Incremental cost category | API path, USD/audio minute | What must be measured |
|---|---:|---|
| STT | 0.00600 | Channel billing, language/model, duration rounding, retries |
| Diarization | 0.00200 | Whether bundled with STT; do not pay/count twice |
| LLM extraction, QA and summary | 0.00150 | Input/output tokens, repeated transcript passes, scorecard size, retries |
| Embeddings | 0.00005 | Indexed text length, re-embedding and query traffic |
| Optional graph projection | 0.00005 | Incremental write/read/storage allocation; remove if disabled |
| Audio/object storage | 0.00015 | Compression, retention, copies, lifecycle, request charges |
| Relational/vector storage and queries | 0.00015 | Retained rows, index size, dashboard concurrency |
| Transfer/bandwidth | 0.00010 | Upload/download/replication and provider region |
| CPU orchestration | 0.00020 | Decode, VAD, scheduling, schema validation and exports |
| Incremental delivery support | 0.00030 | Ticket handling and processing exception cost |
| **Total variable API cost** | **0.01050** | Excludes monthly standby capacity and fixed delivery operations below |

For LLMs, calculate (input tokens × input price + output tokens × output price) / 1,000,000 per call, then divide by duration. Add additional QA passes and Copilot queries separately. A fixed $0.0015/minute may fail badly with long calls, verbose outputs, many scorecards or repeated full-context prompts. Store actual provider usage and cost attribution by job/stage/version.

A 16 kHz, 16-bit stereo PCM minute is about 3.84 MB before headers. At one million minutes, one raw copy is about 3.84 TB. Assuming compression to 0.5 MB/minute produces about 500 GB; this is an assumption to test with actual codecs. Three retained copies roughly triple storage capacity before metadata/version overhead. Price storage from retained GB-month, not lifetime processed minutes alone.

For local speech, assume one $1/hour GPU instance running 730 hours/month, a combined STT+diarization throughput of 10 audio minutes per compute minute, and 55% useful utilization after scheduling/idle headroom. Capacity is 730 × 60 × 10 × 0.55 = **240,900 audio minutes/GPU/month**. The GPU-instance price includes its attached host for this model; additional decode/database CPU is in other allocations. This throughput is not established for Persian, the chosen models or 24 GB hardware.

A hybrid path runs speech locally and sends permitted text to cloud LLMs. It retains $0.0025/minute variable cost and adds $400/month specialized operations plus speech GPUs. A fully local path also runs its LLM on a separate assumed $1/hour GPU with capacity of 1,000,000 audio-minute-equivalents/month, retaining $0.001/minute other variable costs and adding $800/month specialized operations. The LLM capacity assumes bounded tokens per audio minute; a larger model or scorecard may need substantially more resources. These paths cannot substitute for one another when privacy policy forbids cloud processing.

Let M be monthly minutes; S = ceiling(M / 240,900); L = ceiling(M / 1,000,000); F be fixed platform/standby operations allocation. Then:

~~~text
API cost       = F + 0.0105 × M
Hybrid cost    = F + 0.0025 × M + 730 × S + 400
Fully local    = F + 0.0010 × M + 730 × (S + L) + 800
Gross margin   = (recognized recurring revenue − delivery COGS) / revenue
Price floor    = allocated delivery COGS / (1 − target gross margin)
~~~

F is assumed as $500, $1,000, $5,000 and $25,000 respectively at the four scales below. It represents platform standby, fixed delivery/support coverage and operational capacity, distinct from incremental support per minute. It is a staircase planning allowance, not an extrapolated industry benchmark. Sales, R&D, corporate overhead, tax and financing are excluded from gross margin and must still be funded.

| Minutes/month | F | Speech / LLM GPUs, fully local | API monthly / per minute | Hybrid monthly / per minute | Fully local monthly / per minute |
|---|---:|---:|---:|---:|---:|
| 10,000 | $500 | 1 / 1 | $605 / $0.06050 | $1,655 / $0.16550 | $2,770 / $0.27700 |
| 100,000 | $1,000 | 1 / 1 | $2,050 / $0.02050 | $2,380 / $0.02380 | $3,360 / $0.03360 |
| 1,000,000 | $5,000 | 5 / 1 | $15,500 / $0.01550 | $11,550 / $0.01155 | $11,180 / $0.01118 |
| 10,000,000 | $25,000 | 42 / 10 | $130,000 / $0.01300 | $81,060 / $0.00811 | $73,760 / $0.00738 |

At a usage-only selling rate of $0.04/minute, API/hybrid/local margins are approximately −51%/−314%/−593% at 10,000 minutes; 49%/41%/16% at 100,000; 61%/71%/72% at one million; and 68%/80%/82% at ten million. This explains the minimum platform fee and why a low-volume dedicated installation cannot be priced like pooled inference. A 70% target at one million minutes implies blended price floors of about $0.0517 API, $0.0385 hybrid and $0.0373 local before unmodeled redundancy or unusual support.

Within the first speech GPU's capacity, API versus hybrid break-even is ($730 + $400) / ($0.0105 − $0.0025) = **141,250 minutes/month**. The first fully local comparison crosses near 237,895 minutes, but buying another speech GPU at 240,901 minutes changes the curve again. There is no universal single threshold: use the staircase, concurrency and peak backlog constraints. At 25% utilization one speech GPU provides only 109,500 minutes/month, below the one-GPU hybrid break-even; at 70% it provides 306,600. Warm capacity, autoscaling and workload scheduling matter as much as hourly price.

On-prem customer TCO is annual license + hardware amortization + power/cooling + storage/backup + local operations + implementation amortization. For an illustrative $50,000 annual license, $18,000 hardware amortized over three years, $8,000 annual operations/power/storage and $12,000 setup amortized over three years, annual TCO is $68,000. At 1.2 million annual minutes that is about $0.0567/minute; at 12 million it is $0.0057 **only if the same hardware can handle that volume**, which must not be assumed. Capacity increases require additional hardware and support. Homayar's private-license margin is based on its own delivery/support cost, not the customer's electricity bill.

Before quoting, measure mono/stereo and accent/model slices, p50/p95 throughput, peak queue depth, 10–30% retry/reanalysis sensitivity, 2× token growth, 90/365-day retention, support hours per account, and one-node-failure capacity. Meter productive GPU hours and reserved idle hours separately. Keep approved routing deterministic by data policy first, quality second, deadline/capacity third and cost fourth. Target sustained software delivery gross margin above 70% as a business goal, with an explicit higher-cost pilot budget; it is not a current result.

# 23. ICP and Vertical Strategy

Start with a midmarket service operation where the QA lead can supply scorecards and outcomes, IT can export recordings, and one executive owns the result. Prefer a queue with 20,000–200,000 monthly minutes, repeated intents, reliable agent/customer metadata and a decision maker willing to fund a pilot. A large enterprise logo with inaccessible data is a poor first deployment.

The following ranks, sales cycles and annual software values are planning hypotheses in USD equivalents, not researched transaction medians. Local purchasing power, procurement and existing relationships may reorder them. Regulatory burden means additional diligence, not a conclusion about specific laws.

| Rank / vertical | Pain and urgency | Hypothesized annual value / sales cycle | Deployment and integration | Strategic decision |
|---|---|---|---|---|
| 1. E-commerce, delivery and consumer services | Repeated complaints, refund/escalation QA; high operational urgency | $20k–60k / 1–3 months | SaaS or private; PBX, ticket/order IDs | Best initial repeatable wedge; measurable resolution and review efficiency. |
| 2. BPO/contact-center outsourcers | Consistent QA across teams and clients; high | $40k–120k / 2–4 months | Private or isolated client workspaces; many agent identities | Strong channel and expansion potential; strict client data separation. |
| 3. ISP and regional telecom support | Service issues, repeat contacts, churn complaints; high | $40k–150k / 3–6 months | Often private; PBX, ticket/network incidents | Attractive second vertical; prove incident linkage without claiming causality. |
| 4. Insurance service and sales | Script adherence, claims explanations, coaching; high | $40k–120k / 3–6 months | Private preference; CRM/claims | Templates useful; human review for consequential compliance findings. |
| 5. Banking, payment and fintech support | Sensitive errors, identity scripts, dispute handling; high | $60k–180k / 6–12+ months | Private, SSO, security review, payment/CRM references | Valuable lighthouse after foundations; avoid depending on it for first cash. |
| 6. Automotive sales/after-sales | Appointment, warranty and dealer consistency; medium-high | $20k–80k / 2–5 months | CRM/dealer systems; cloud/private | Good templated expansion if a distributor provides access. |
| 7. Travel/hospitality | Booking changes, service recovery, seasonal spikes; medium-high | $20k–60k / 1–4 months | Reservation/ticket IDs; multilingual | Measurable operations but seasonal economics and language scope. |
| 8. Healthcare appointment/service centers | Access, appointments and communication QA; high sensitivity | $30k–100k / 4–9 months | Private preference; scheduling, limited clinical data | Restrict to service workflows; no clinical diagnosis or triage scoring. |
| 9. Utilities and public service hotlines | Complaint routing, outages, regional service issues; high | $50k–150k / 6–12+ months | Private; case/asset/region IDs | Later institutional sale with long procurement and operational burden. |
| 10. Industrial/B2B service and technical support | Equipment issues, warranty patterns, distributor quality; medium | $25k–100k / 3–8 months | ERP/CRM/asset identifiers | Graph relationships may add value after enough linked incidents exist. |
| 11. Government/citizen services | Service accountability and complaint reporting; variable | $50k–200k / 9–18+ months | Private/offline, extensive procurement | Selective partner-led opportunity after support and assurance maturity. |
| 12. General B2B sales teams | Coaching, deal capture, follow-up; medium | $15k–60k / 1–4 months | CRM, meetings and calendar ecosystem | Crowded market; add only through existing customer demand. |

Begin with a service/e-commerce template and one buyer-funded ISP/BPO template, sharing the same code and versioned configuration. Banking, insurance, healthcare, telecom and sales editions should be configuration bundles—vocabulary, QA rules, evidence examples, dashboards and connector mappings—not forks. Version templates and let customer policy owners approve them. Never label a generic template “compliant” with an industry regulation without a scoped expert review.

| Stakeholder | Value proposition and required proof |
|---|---|
| CEO / COO / Operations Director | See recurring service failures and whether interventions work; show trends linked to reviewed evidence and realized financial/operational outcomes. |
| CX / Contact Center Manager | Improve QA coverage and reduce review effort; show exception queues, stable scorecards and team adoption. |
| QA Manager | Configure criteria, calibrate reviewers, correct AI and defend scores; show evidence and agreement metrics. |
| CIO / CTO | Integrate without replacing the PBX/CRM; show versioned APIs, deployment boundaries, capacity and upgrade/restore evidence. |
| CISO / compliance owner | Control where data goes and who can access/export it; show tested access, retention, audit and local-only routing. |
| IT Manager | Install, monitor, back up and troubleshoot a bounded supported stack; show preflight checks and runbooks. |
| Supervisor | Find the few calls worth reviewing and assign specific coaching; show reduced review time and useful alerts. |
| Agent | Understand a score and correct errors; show accessible evidence, appeals and fair comparison cohorts. |
| Analyst / product owner | Quantify issue trends with denominator and drill-down; show accurate aggregates and approved linked source IDs. |

# 24. Competitive Analysis

Primary vendor sources were checked on 7 September 2026. These establish advertised capabilities, not independent accuracy, contractual availability or offline qualification. “Unverified” means the reviewed evidence does not establish the point; it is not a claim that a competitor lacks it. No competitor was benchmarked against Homayar audio.

| Vendor/category | Verified product emphasis | Implication for Homayar |
|---|---|---|
| Observe.AI | Auto QA, configurable evaluations, calibration and coaching across contact-center interactions | Automated scoring is table stakes; compete on trusted Persian evidence, integration and deployment fit. [Auto QA](https://www.observe.ai/post-interaction/auto-qa) |
| CallMiner | Conversation analytics and quality management tied to customer/agent improvement | VOC plus QA is an established category; a graph visualization alone does not differentiate it. [Quality management](https://callminer.com/solutions/quality-management) |
| NICE | Speech/text interaction analytics for customer and operational insights within its contact-center portfolio | Broad incumbent workflow and procurement footprint; target coexistence and underserved deployment/language needs. [Interaction analytics](https://www.nice.com/products/interaction-analytics?language=en) |
| Genesys | Speech/text analytics integrated into Genesys Cloud operations | Native ecosystem integration can outweigh a standalone feature. Homayar needs reliable import and measurable incremental value. [Overview](https://help.genesys.cloud/articles/speech-and-text-analytics-overview/) |
| Gong | Revenue conversation intelligence; official language documentation explicitly lists Persian (Iran) | Persian availability is not unique. Prove relative domain accuracy, private operation and service QA, not a language-count claim. [Languages](https://help.gong.io/docs/what-languages-are-supported-in-gong) |
| ZoomInfo Chorus | Call/meeting transcription, sales coaching, CRM-linked deal and relationship intelligence | Stronger adjacent sales story; avoid a horizontal meeting/sales launch. The retrieved product page has an older crawl, so current packaging needs sales confirmation. [Chorus](https://www.zoominfo.com/products/chorus) |
| Dialpad | AI scorecards for contact centers and coaching teams; documented role-specific grading permissions and paid add-on packaging | Review roles and scoring workflows are expected product behavior. [Scorecards](https://www.dialpad.com/features/ai-scorecards/), [grading documentation](https://help.dialpad.com/docs/grading-with-ai-scorecards) |
| Talkdesk | Automated quality management and coaching in its contact-center suite | Competes on operational workflow and installed platform, not transcription alone. [Automated quality management](https://www.talkdesk.com/blog/automated-quality-management/) |
| Amazon Connect Customer | Broad contact-center offering with analytics/AI capabilities in its current packaging | Bundled infrastructure creates price pressure. Compare the complete solution and region; its voice charge is not a standalone transcription quote. [Current pricing](https://aws.amazon.com/products/connect/customer/pricing/) |
| Google Customer Experience Insights | Standard post-call transcription, summaries, highlights and sentiment; Enterprise adds Quality AI scorecards | Direct benchmark for configurable automated QA economics. [Current product/pricing](https://cloud.google.com/contact-center/insights/pricing) |
| Microsoft Dynamics 365 Sales Insights | Conversation intelligence and seller coaching inside Dynamics | Installed CRM context is the advantage; Homayar should integrate before considering a sales-suite replacement. [Sales Insights documentation](https://learn.microsoft.com/en-us/dynamics365/sales/dynamics365-sales-insights-app) |
| Amerandish / Farsava / Hooshtel Analytics | Persian STT, VoIP voicebots and operator quality/contact-center analytics advertised on its current official site | A directly relevant local supplier/competitor. Homayar cannot assume an empty Persian market or superior local accuracy. [Official products](https://amerandish.com/) |
| Local VoIP/CRM integrators and internal enterprise teams | Often own recording access, installation and customer trust; no single validated feature set | Potential partners and substitutes. A simple transcript plus BI dashboard may satisfy lower-end buyers. Validate actual named products in sales discovery. |

Use the following procurement comparison instead of inventing missing checkmarks. V = directly established in the reviewed primary sources; P = product-family positioning, exact edition/scope needs confirmation; U = unverified; T = Homayar target, not delivered qualification.

| Product group | Transcription | QA / coaching | VOC | Copilot or assistant | Persian | On-prem / fully offline | Graph intelligence | Integrations / enterprise / pricing |
|---|---|---|---|---|---|---|---|---|
| Observe.AI | P | V / V | P | U in reviewed page | U | U / U | U | Contact-center platform; contract and security scope need RFP. |
| CallMiner | P | V / P | V | U | U | U / U | U | Enterprise solution; quote not publicly verified here. |
| NICE | V | P / P | V | P | U | U / U for this exact analytics SKU | U | Large platform; distinguish hosted/current SKU from legacy products. |
| Genesys Cloud | V | P / P | V | P | U | Reviewed Cloud product; private/offline U | U | Native Genesys context; exact add-on and language contract needed. |
| Gong | V | P / V | P | P | V language listing | U / U | Relationship features do not prove customer-queryable evidence graph | CRM/revenue context; price quote unverified. |
| Chorus | V | P / V | V | U for Chorus-specific scope | U | U / U | Relationship intelligence; evidence graph U | CRM syncing V; current price/package U. |
| Dialpad | P | V / V | U | P | U | U / U | U | Scorecards paid add-on; exact price U. |
| Talkdesk | P | V / V | P | P | U | U / U | U | Platform integration; commercial quote required. |
| Amazon Connect | V | P / P | P | P | U for required features | Reviewed managed cloud; offline U | U | Current voice price lists $0.038/min plus telephony, subject to region and full terms. |
| Google Insights | V | V / U | V | U in reviewed pricing page | U for entire QA pipeline | Reviewed managed cloud; offline U | U | Standard voice $0.015/min; Enterprise $0.025/min; standalone Quality AI $50 named/$60 concurrent agent monthly with stated limits. |
| Microsoft Sales Insights | V | P / V | P | P | U for exact feature | U / U | U | Dynamics integration; license scope needs quote. |
| Amerandish / Hooshtel | V | V / U | P | P across portfolio | V offering claim | Installation/offline scope U | U | Persian/VoIP focus; benchmark, security and price need direct qualification. |
| Homayar audited state → target | Real local Whisper, unqualified → T | Mock/partial legacy and missing v1 workflow → T | Limited legacy / target trends | Legacy templates and v1 SQL search → T | No benchmark → T | Compose exists, incomplete → T / T | Legacy partial; optional projection T | Generic foundations; no verified commercial deployment or active price. |

The vendor amounts above are observations from the cited current pricing pages, not like-for-like total costs. Google lists legacy contract exceptions and scorecard question/conversation limits; AWS adds telephony and varies by service/region. Neither establishes availability for a particular Iranian customer. Do not compare these to Homayar's illustrative component costs as if all included the same storage, support, workflows and integration.

The defensible positioning is: **Persian contact-center quality with auditable evidence, local integration and a qualified private deployment.** It becomes defensible only after a side-by-side buyer dataset, a usable review workflow and repeatable installation. Use an RFP to ask every finalist for the same audio benchmark, mono/stereo attribution, evidence export, tenant boundary, network-denied installation, model license, SSO/deprovisioning, retention, full price and support test. Public silence on a feature is not competitive proof.

# 25. Competitive Moat

| Candidate advantage | Current evidence | How it becomes durable | Decision |
|---|---|---|---|
| Persian accuracy | Whisper supports Persian in principle; no Homayar benchmark | Consented domain corpus, difficult-accent/entity evaluation, corrections and legally reusable improvements | Highest technical differentiation investment; measure against local and global alternatives. |
| Qualified private/offline operation | Packaging foundations only | Signed tested releases, supported hardware, restricted egress, upgrades and local support competence | Strong fit advantage; hard operations work, not merely a deployment toggle. |
| Iranian enterprise integration | Legacy PBX/LDAP references, incomplete current adapters | Repeatable PBX/CRM versions, identity mappings, installer/channel relationships and connector regression data | Potential distribution and switching advantage. |
| Custom QA and evidence | Partial abstractions; legacy fabricated outputs undermine trust | Customer-approved scorecards, calibrated decisions, review history and accepted coaching workflows | Core retention mechanism, not exclusive technology. |
| Knowledge graph | Legacy graph and code Graphify artifact | Reliable linked customer/product/case identities, useful cross-call queries better than SQL alone | Conditional P2 advantage; ontology and graph engine are readily copied. |
| Customer-specific models | Provider interfaces, no validated adaptation | Rights-cleared data, controlled evaluation, deployment and rollback of beneficial adaptations | Later service/product option; avoid one unmaintainable model per account. |
| Local compliance | No certification or legal conclusion verified | Documented controls, expert-reviewed templates, procurement evidence and incident competence | Trust asset; never market an unearned legal guarantee. |
| Cross-call operational intelligence | Proposed aggregation | Closed-loop links from evidence to action to outcome, useful longitudinal data | Potential moat if customers act and renew; a dashboard alone is not one. |
| Modular provider architecture | Local interfaces exist | Requalification discipline, price/quality telemetry and reproducible model swaps | Cost/control advantage; architecture by itself rarely creates buyer lock-in. |

Do not call customer data a moat unless Homayar has explicit rights to use it. Customer-private corrections may improve that customer's model only. Cross-customer benchmarking needs separately authorized, sufficiently aggregated data and controls against reidentification; exclude it from v1. Build retention through outcomes, workflow familiarity and integration quality while preserving customer export and deletion rights.

# 26. Go-To-Market

Founder-led, evidence-led sales should precede self-service acquisition. Pick one repeatable operational problem and two or three paid design partners. A successful installation in the chosen ICP is more useful than many unrelated demos. Use local VoIP and CRM integrators to gain recording access and implementation credibility, while retaining ownership of product quality and customer success.

| Stage | Deliverable | Exit criterion |
|---|---|---|
| Qualification / discovery | Buyer map, queue volume, recording rights, scorecard, infrastructure and budget | Named operational sponsor, IT owner and purchasing path; no unresolved data-access blocker. |
| Baseline and sample | Authorized representative sample, existing QA results and review-time baseline | Data quality and expected model performance can be assessed; written data handling and deletion terms. |
| Evidence demo | A real or clearly labeled synthetic call, transcript, criterion evidence, correction and trend drill-down | Buyer understands both useful outputs and unavailable/low-confidence states. |
| Setup | One source, identity/roles, scorecard, deployment and recovery check | Recordings arrive reliably; no production mock output; privacy boundary accepted. |
| Two-week measured pilot | Representative queue, frozen evaluation set, weekly supervisor calibration and cost telemetry | Gates below met or failures quantified; measurement starts after setup, not contract signature. |
| ROI review and proposal | Actual before/after results, full TCO, implementation scope and support terms | Sponsor agrees value and commercial decision; no invented churn or compliance savings. |
| Rollout | Training, acceptance, support handoff and usage review | Production release gates, owner sign-off and renewal success plan. |
| Expansion | More queues, scorecards, minutes or outcome-linked insight | Existing supervisors use it, issue actions close, margin and support remain healthy. |

Pilot targets are proposals to agree before measurement: at least 99% successful processing of valid supported inputs; no privacy/ACL defects; all presented scores cite evidence; predefined QA agreement thresholds in Section 15; at least 30% reduction in median hands-on review time on matched tasks; at least 70% of invited supervisors active weekly; and at least three accepted, owned improvement actions. Report small-sample uncertainty, scope exclusions and all failures. An action accepted by a supervisor is not yet a realized financial benefit. A two-week pilot cannot credibly prove annual churn reduction.

The 30-minute sales demo should spend about five minutes on the buyer's QA problem and baseline, ten on one call and review/correction, five on issue trends and action approval, five on private deployment/security boundaries and five on pilot scope. Demonstrate a degraded result as well as a good one. Customer audio requires explicit permission and access controls; offer a synthetic fixture for public demos. Public sample upload must not be enabled before retention, abuse limits and deletion are proven.

ROI calculator inputs: monthly calls and minutes; sampled reviews; current review minutes; reviewer loaded hourly cost; future review minutes including AI correction; subscription, implementation amortization, customer hardware and operator time. Annual labor capacity released = reviews/month × (old − new review minutes) / 60 × hourly cost × 12. Treat this as released capacity unless staffing expense actually falls. Do not count all calls as manually reviewed before adoption, and do not multiply “100% coverage” into fictitious labor savings.

Example assumptions: 2,000 reviews/month, 12 minutes before versus 7 after, and $20/hour yield $3,333/month of released review capacity. A $1,500 subscription and $500/month amortized implementation leave $1,333/month before customer operations; a $7,500 plan would not be justified by this saving alone. Use the customer's actual wage/currency inputs. Add AHT, repeat-contact or conversion benefits only from matched cohorts or a suitable controlled rollout, net of quality regressions. Avoid double-counting reduced handle time, reduced repeat calls and headcount savings from the same workload.

Partner economics are experiments: referral partners might receive 10–15% of collected first-year software revenue for a qualified closed account; delivery resellers might retain 20–30% when they own installation and first-line support. Define renewal share, payment after collection, deal registration, non-exclusivity, security duties and support escalation. At $50,000 annual software revenue and a 25% reseller discount, Homayar retains $37,500; a 70% delivery margin on that retained amount allows at most $11,250 delivery COGS. Do not stack discounts without this check. Hardware, cloud and security partners should be paid for defined deliverables; avoid exclusive model/provider commitments.

Commercial dashboard: ARR from active recurring contracts; new/expansion/churned ARR; gross and net revenue retention; paid-pilot conversion; time to first useful review; implementation hours; active organizations and supervisors; processed/eligible/analyzed minutes; fully evaluated call coverage; reviews and accepted overrides; accepted insight-to-action rate; action closure; processing success; stage latency; cost/minute; delivery gross margin; support hours/account. Calculate every rate with a denominator and time window. “Insights generated” without review or action is a weak success metric.

# 27. Homayar.com Audit

The deployed website and the dirty Next.js workspace are separate evidence surfaces. Public pages were fetched and the homepage/demo inspected in a browser. No form was submitted, microphone activated, customer file uploaded or purchase made. Source inspection establishes implementation intent; it does not prove the deployed build matches that source.

| Area | Observed issue | Recommended change / gate |
|---|---|---|
| Positioning | Persian RTL homepage has a visually prominent AI-person hero and broad claims about organizational intelligence. It does not quickly explain the call-quality buyer, input and concrete output. | Lead with Persian contact-center QA, visible evidence and private deployment availability tied to release qualification. Show the report/review workflow above the fold. [Homepage](https://homayar.com/) |
| Category and scope | Calls, emails, chat, tickets and wide AI-agent capabilities broaden the promise beyond the audited voice product. | Prioritize recorded calls, QA and operational issues. Clearly mark future channels and optional modules. |
| Product proof | Hero artwork communicates AI but gives little evidence of the actual working console. | Replace the main proof area with a real, anonymized or explicitly synthetic call report: criterion, cited turn, playback and reviewer correction. |
| Demo | The live page is a separate hand-built experience. Its script schedules predefined segments and report transitions; no browser microphone or fetch call was found in its inline code. It advertises 98 languages without a Homayar benchmark. | Label it a guided simulation. A separately labeled real sample can use an actual audited pipeline, show model/version and disclose failures. Do not call a timed simulation live transcription. [Demo](https://homayar.com/demo/) |
| Enterprise claims | Live pricing advertises unlimited enterprise calls, SSO/SAML/SCIM, a 99.9% SLA, SOC 2 reports, penetration-test material and named integrations not demonstrated by this audit. | Require a claim-to-release evidence register; withdraw, qualify or mark unavailable until proven. Unlimited processing must become explicit allowance/capacity terms. [Pricing](https://homayar.com/pricing/) |
| Case studies | Named stories include precise call volumes and improvement percentages, but no substantiation was located in the inspected materials. | Obtain customer permission, baseline/method and evidence, or label clearly as illustrative scenarios. Do not present unverified outcomes as customer results. [Case studies](https://homayar.com/case-studies/) |
| Customer logos | Homepage labels logos as customers of Kohan System Farda, rather than verified Homayar deployments. | Preserve that distinction and obtain logo permission; a parent company's relationships are not evidence of Homayar adoption. |
| Conversion | Live contact/book pages expose phone/email contact, but did not present a complete reservation/lead capture flow in the inspected page content. | One accessible request-pilot form with deployment, volume and contact fields; acknowledged receipt, delivery failure handling and owner follow-up. [Contact](https://homayar.com/contact/), [Booking](https://homayar.com/book/) |
| Pricing clarity | Public page withholds numeric pricing; dirty local content contains different figures. | Define which package/version is offered and keep a single published pricing source. Do not promote local draft prices as current. |
| Localization | Persian UI is present, including the demo after its script runs. Multiple language selectors are not evidence of equally complete translated product flows. | Ship complete Persian/English journeys first; localized errors, form validation, metadata, dates, numbers and RTL keyboard navigation. |
| SEO/share metadata | Inspected pages reuse broad homepage-style metadata, reducing route-specific clarity. | Unique title/description/canonical and tested locale alternates per page; accurate sitemap and robots behavior. Validate built output, not just metadata helpers. |
| Trust and legal pages | Privacy/terms pages are marketing disclosures until checked against actual processing, suppliers and retention. | Align with approved data flow, regions, subprocessors and contact identity; route to jurisdiction-specific review. Never infer certification from page presence. [Privacy](https://homayar.com/privacy/), [Terms](https://homayar.com/terms/) |

The case-study page includes, for example, Meridian Trust Bank and Northbridge Health outcomes, alongside other named organizations. These are **unverified claims in this audit**, not a determination that the organizations or outcomes are fictitious. Before publication, evidence should identify the customer, permission, period, denominator, baseline, intervention, attribution limits and approver. Parent-company references can be presented honestly as implementation experience when labeled that way.

Target information architecture: Home; Product/Quality; Voice of Customer; How It Works; Private Deployment; SaaS; Security; Integrations; Pricing/Pilot; Demo; Docs/API; About/Contact. Add an industry page only when a real template, supported integration and credible use case exist. Do not create thirty thin feature pages merely to fill navigation. Give each unsupported feature a version/status label rather than a generic “enterprise” badge.

Suggested homepage proposition: “Evidence-backed quality review for Persian customer calls.” Supporting copy: “Turn authorized recordings into searchable transcripts, configurable QA reviews and recurring issue reports. Review every finding against the conversation, with deployment options that keep your data under your control.” Use this as target copy only after the corresponding release gates pass. Before then, say “Join a paid design-partner pilot” and describe the tested scope.

| Website phase | Deliverables | Product prerequisite |
|---|---|---|
| W0 — claim correction | Claim register, clear simulation labels, qualified customer evidence, honest pilot CTA and consistent contact identity | WP21 P0; no product readiness implication. |
| W1 — pilot conversion | One focused homepage, demo, pilot request flow, qualified security/private pages and package outline | v0.6 measured pipeline and v0.7 review workflow for the demonstrated features. |
| W2 — commercial launch | Supported integration directory, verified results, ROI calculator, release-linked docs, priced offers if validated | v1.0 acceptance and actual support/privacy commitments. |
| W3 — expansion | Validated industry pages, advanced VOC/API offers and customer stories | Released P2 capabilities plus customer evidence. |

Store each public claim with claim ID, exact localized copy, feature ID, minimum product version, deployment profile, evidence artifact, owner and last review date. CI can reject a claim marked “available” without a matching released capability. This is a proposed future control; no website content or repository file was changed in this planning phase.

# 28. Business Expansion Ideas

Scores are informed judgment, not market data: revenue, margin, differentiation and strategic fit use 5 = favorable/high; sales complexity and implementation effort use 5 = difficult/high. Rank using commercial focus and dependencies as well as the numbers.

| Idea | Revenue | Sales complexity | Effort | Margin | Differentiation | Fit | Recommendation / payer |
|---|---:|---:|---:|---:|---:|---:|---|
| Core contact-center QA subscription | 5 | 3 | 3 | 4 | 4 | 5 | First; QA/operations pays for reliable review and coaching. |
| VoIP/CRM reseller offer | 4 | 3 | 2 | 3 | 4 | 5 | Early controlled channel; integrator owns defined first-line delivery. |
| Private AI software/appliance | 5 | 5 | 4 | 3 | 5 | 5 | Strategic option after offline qualification; customer funds hardware. |
| Industry template packs | 4 | 3 | 2 | 5 | 4 | 5 | P2 after two repeatable workflows; configuration, not forks. |
| Managed Voice Intelligence | 4 | 3 | 3 | 3 | 4 | 4 | Limited design partners; delivery costs and human review explicit. |
| Outsourced QA service | 3 | 2 | 3 | 2 | 2 | 3 | Partner-led; useful for market access, weak pure-software margin. |
| BPO multi-client platform | 5 | 4 | 4 | 4 | 4 | 5 | Strong expansion after client isolation and delegated administration. |
| Executive VOC subscription | 4 | 3 | 2 | 4 | 3 | 5 | Existing-account add-on only when leaders act on verified trends. |
| Compliance review module | 4 | 5 | 4 | 4 | 4 | 4 | Expert-approved templates and human disposition; no legal assurance claim. |
| Coaching service/curriculum | 3 | 2 | 3 | 2 | 3 | 4 | Partner-delivered content tied to accepted QA findings. |
| Analysis API as a service | 3 | 3 | 3 | 3 | 2 | 3 | P2 after stable metering and evidence contracts; avoid cheap STT competition. |
| Cross-call root-cause intelligence | 4 | 4 | 4 | 4 | 5 | 4 | P2; fund only if linked outcomes outperform simpler issue trends. |
| Customer-specific model adaptation | 4 | 4 | 5 | 2 | 4 | 3 | High-value bounded service; require rights and improvement benchmark. |
| Telecom white-label distribution | 5 | 5 | 5 | 3 | 3 | 3 | Later; integration, branding and support can consume the team. |
| Cross-company benchmarking service | 3 | 5 | 5 | 4 | 4 | 2 | Defer; rights, comparability and reidentification risks outweigh early value. |
| Horizontal meeting assistant | 3 | 3 | 5 | 3 | 1 | 1 | Defer; crowded category and recording-bot burden. |

Every approved expansion needs a named buyer, observed problem, likely contract, simpler alternative, engineering estimate, ongoing cost and link to retention. Templates are the lowest-complexity expansion once the core data and review workflow are reliable. A graph product, a coaching agency and a meeting bot are separate business bets; do not fund them simultaneously from the first contact-center pilot.

# 29. Build vs Buy Decisions

| Capability | Decision | Rationale / bounded internal work |
|---|---|---|
| STT | Support multiple options; integrate local OSS first, eligible commercial API optional | Build the stage contract, evaluation, policy and evidence mapping. Do not train a foundation ASR model. |
| Diarization | Integrate qualified model plus deterministic channel handling | Build channel inspection, speaker/role normalization and uncertainty; do not invent speaker turns. |
| Emotion | Postpone | Weak measurement and potential misuse; targeted sentiment can be evaluated later without emotion/personality claims. |
| LLM extraction/QA | Build task schema, rules, evidence and evaluation; integrate local/API models | Homayar owns workflow and truth boundaries; model vendors supply interchangeable inference. |
| Embeddings/reranking | Integrate multilingual model candidates after Persian retrieval evaluation | Build ACL/version/chunk policy; do not assume multilingual means adequate Persian. |
| Vector database | Integrate pgvector in existing PostgreSQL | Avoid another distributed store until measured scale/recall justifies it. |
| Identity | Integrate enterprise IdP/broker; preserve one application session boundary | Build membership/roles and resource authorization; do not implement SAML protocols. |
| Billing | Build usage/entitlement ledger; integrate eligible payment/invoice services | Manual annual invoicing first; no custom payment processor or card storage. |
| Object storage | Standard S3 interface; customer-managed or qualified maintained implementation | Own lifecycle/access/restore contracts; avoid dependence on one server distribution. |
| Graph | PostgreSQL authority; optional Neo4j projection after useful-query proof | Build ontology, evidence links and reprojector; do not make all product availability depend on graph. |
| Analytics | Build scoped SQL aggregates/materialized views and role dashboards | Deterministic denominators first; integrate BI export rather than a warehouse platform. |
| Observability | Integrate OpenTelemetry and Prometheus-compatible stack | Build domain metrics and redaction; do not invent tracing/metrics infrastructure. |
| Queues | Keep Celery-compatible stack, qualify broker version | Build outbox, leases, checkpoint/state contracts and recovery tests; not a custom distributed scheduler. |
| Audio decoding | Integrate maintained codec tools in bounded worker | Own validation, sandboxing, timeouts and normalization metadata. |
| QA/review/coaching | Build internally | Core differentiated workflow: customer scorecards, evidence, calibration, overrides, appeals and follow-through. |
| Search/Copilot | Build constrained product experience using integrated retrieval/models | ACL filters before retrieval, deterministic metrics, evidence citations and abstention. No autonomous unrestricted database agent. |
| Integrations/actions | Build canonical events and approval ledger; reuse adapters/workflow tooling | n8n can accelerate permitted customer-specific automation; keep source of truth and authorization in product. |
| PDF/CSV/JSON | Integrate rendering/serialization; build report template and export policy | Verify Persian fonts, RTL, formula injection protection and authorized content. Native Excel workbook is P2 unless a customer funds it. |

Reuse local provider interfaces, hashed session/workspace checks, quota reservation patterns, typed schemas and storage adapters after review. Reuse parent-company installation knowledge and real partner relationships if they exist and can be verified. Treat legacy graph/LDAP code as reference material with known defects, not trusted drop-in infrastructure. Organizational LightRAG, Codex-Mem, Gstack and Ruflo can support engineering knowledge and execution, but are not automatically suitable for tenant data processing or redistribution inside the appliance.

# 30. Open-Source Recommendations

Repository metadata and primary project/model pages were inspected on 7 September 2026. Stars below are rounded adoption signals, not quality scores. Releases are observations, not instructions to upgrade blindly. Lock a tested compatible set, including CUDA, Python, model weights and transitive dependencies. Repository licenses do not automatically cover every model, dataset, commercial extension or bundled component.

| Project / evidence | License and maintenance observation | Maturity, deployment and Persian suitability | Decision / effort / risk |
|---|---|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | MIT; ~25k stars; unarchived, last push Nov 2025, latest recorded release v1.2.1 Oct 2025 | Established inference library, CPU/GPU and quantization; recent cadence is slower than other candidates. Persian quality depends on model and data, not library choice. | Keep behind adapter, medium integration; benchmark pinned runtime and monitor maintenance risk. |
| [pyannote.audio](https://github.com/pyannote/pyannote-audio) and [community-1 model](https://huggingface.co/pyannote/speaker-diarization-community-1) | Library MIT, model CC-BY-4.0 with gated access terms; ~10k library stars, active Sep 2026, release 4.0.7 Jun 2026 | Mature diarization tooling; CPU possible, GPU desirable for throughput. Model supports offline use after download; default downmixing means preserve channel-aware path separately. No Homayar Persian DER proof. | Primary mono candidate, medium-high integration; model attribution, redistribution/download terms and overlap evaluation required. |
| [Silero VAD](https://github.com/snakers4/silero-vad) | MIT; ~10k stars; active Aug 2026, release 6.2.1 Feb 2026 | Practical lightweight VAD, CPU/ONNX suitability; not a speech recognizer or diarizer. Evaluate noisy Persian telephony and hold music. | Integrate, low-medium effort; ensure chunk boundaries do not lose words. |
| [pgvector](https://github.com/pgvector/pgvector) | PostgreSQL License verified from license file; ~23k stars; active Aug 2026; v0.8.6 tag checked | Mature PostgreSQL extension with approximate/exact search options; CPU, no inference GPU. Language-neutral storage; retrieval depends on embeddings. | Default vector store, medium effort; test filtered recall, index growth and tenant policies. |
| [FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding) / [BGE-M3](https://huggingface.co/BAAI/bge-m3) | MIT library/model card; ~12k library stars; active Aug 2026, v1.4.2 | Multilingual retrieval candidate, CPU possible/GPU useful for indexing. Persian support claim is not a domain recall benchmark. | Candidate, not mandatory; medium effort, validate against lexical baseline and license exact weights. |
| [Valkey](https://github.com/valkey-io/valkey) | BSD-3-Clause; ~27k stars; active Sep 2026, release 9.1.2 | Established Redis-compatible ecosystem direction; no GPU/language dependency. Broker behavior and supported client versions still require qualification. | Evaluate against current Redis dependency; medium effort, no automatic substitution before Celery/recovery tests. |
| [Keycloak](https://github.com/keycloak/keycloak) | Apache-2.0; ~36k stars; active Sep 2026, release 26.7.3 | Mature identity broker/server; adds Java service, DB, upgrade and admin burden. No GPU; Persian UI needs review. | Integrate when customer IdP requires broker, medium effort; prefer existing customer federation if simpler. |
| [vLLM](https://github.com/vllm-project/vllm) | Apache-2.0; ~91k stars; active Sep 2026, release v0.28.0 | Active inference server; substantial GPU/driver/model compatibility matrix. VRAM depends on weights, quantization, context and concurrency. Persian quality comes from selected model. | Qualify for local structured generation, medium-high effort; no claim every model fits pilot hardware. |
| [OpenTelemetry Collector](https://github.com/open-telemetry/opentelemetry-collector) | Apache-2.0; ~7.5k stars; active Sep 2026, v0.160.0 | Widely used telemetry pipeline; CPU, modest setup but many components/exporters. Language-neutral. | Integrate minimal collectors, medium effort; filter sensitive fields and disable outside export for offline profile. |
| [Prometheus](https://github.com/prometheus/prometheus) | Apache-2.0; ~66k stars; active Sep 2026, v3.14.0 | Mature metrics/alerting; CPU/storage sizing and label cardinality matter. Language-neutral. | Integrate, low-medium effort; no transcript/customer IDs as metric labels. |
| [SeaweedFS](https://github.com/seaweedfs/seaweedfs) | Apache-2.0; ~34k stars; active Sep 2026, v4.45 | Active storage system with S3 interface; operational complexity grows with topology. No GPU/language dependency. | Qualification candidate for private storage, medium-high effort; verify multipart, IAM, versioning, lifecycle and restore before selection. |
| [Neo4j](https://github.com/neo4j/neo4j) | Community GPL-3.0; ~17k stars; active Aug 2026; enterprise features have separate terms | Mature graph database, JVM/CPU/RAM operational cost. GitHub “latest release” metadata is stale and is not the current product version. Local infrastructure pins a 5.26 family image. | Optional P2 projection, medium-high effort; confirm version support, commercial packaging and HA license with vendor. |
| [MinIO](https://github.com/minio/minio) | AGPL-3.0; ~61k stars; repository marked archived; recorded last push Apr 2026, last release Oct 2025 | Existing familiar S3 dependency, but archived community repository changes support/supply-chain assessment. | Do not select as new unsupported default. Preserve adapter while evaluating customer-supported S3/commercial support or qualified alternative; migration is a separate decision. |

No open-source candidate here was installed, upgraded or benchmarked as part of this recommendation. Maintenance verification narrows choices; it does not complete security, compatibility, redistribution or enterprise support qualification. Use an SBOM plus a separate model manifest recording source, exact revision/hash, license, access terms, attribution, intended language, approved hardware and evaluation results. Recheck before procurement because project status and licenses can change.

# 31. Target Repository Structure

Keep two independently releasable repositories in the near term: HomaYar for the product and Homayar.com for marketing. Remove the *dependency* on an untracked nested source directory through a planned source reconciliation, not by deleting the local work. Build and publish product API/worker/console artifacts from the product release; deploy the website independently against a documented public API or sales backend. This avoids an unnecessary monorepo migration while resolving the current clean-checkout gap.

Proposed product layout, to be introduced incrementally after preserving the W overlay:

~~~text
HomaYar/
  backend/
    main.py                    # composition root, versioned API only
    api/v1/                    # routers and request/response contracts
    domain/                    # conversations, quality, actions, tenancy
    application/               # use cases and policy orchestration
    infrastructure/            # SQL, objects, broker, provider adapters
    providers/                 # stage contracts and model clients
    jobs/                      # outbox, stages, leases, task entry points
  frontend/src/
    app/                       # routes, layout, auth and localization
    features/                  # calls, review, insights, administration
    shared/                    # accessible UI, API client, evidence types
  migrations/                  # versioned PostgreSQL changes
  contracts/                   # OpenAPI export, events and JSON schemas
  integrations/                # supported source/CRM adapters and fixtures
  deploy/
    compose/                   # cloud/private profiles
    images/                    # API, console and CPU/GPU worker builds
    models/                    # manifests, licenses, checksums; not secret keys
    operations/                # preflight, backup, restore, upgrade, diagnostics
  evals/                       # manifests/scorers; no private audio in Git
  tests/
    unit/
    integration/
    e2e/
    security/
    recovery/
  docs/                        # product, architecture, install, admin, API
  .github/workflows/           # clean-checkout builds and release gates
~~~

Do not create empty microservice directories merely to match this diagram. Domain/application boundaries can initially be explicit modules under the existing backend. API and worker share domain contracts and one migration lineage; different entry points and images allow GPU scaling without network-splitting every function. Keep customer audio, credentials, database files, build artifacts, virtual environments, node_modules and model caches out of source. Store large approved model packages and fixtures in controlled artifact storage with manifests.

Version product and website separately, recording which product API versions the website/demo supports. Generate the TypeScript client or schema types from a stable contract where useful; do not duplicate evolving enums manually. Any future website code change must read the installed Next.js documentation required by AGENTS.md. This phase contains no code changes, so no Next.js API assumptions were introduced.

Development process: short feature branches from protected main; one coherent capability per PR; migration and feature-flag plan; human/domain review for scorecards and privacy-sensitive paths; automated checks; immutable release tags; changelog distinguishing model/quality changes from UI fixes. Designate a single migration owner during simultaneous schema work. Expand-contract migrations and rollback/roll-forward rehearsals take precedence over cosmetic refactors. Do not merge disconnected master wholesale.

# 32. Technical Debt Register

Evidence abbreviations remain M = legacy master, D = committed dev, W = dirty local product, S = website/infrastructure workspace. Entries describe audited evidence; target capabilities are not presumed absent solely because a page does not expose them.

| ID / priority | Verified debt and impact | Evidence | Remediation package |
|---|---|---|---|
| TD01 P0 | Disconnected legacy master; most advanced product and deployment work uncommitted; clean release provenance missing | Section 3 branch SHAs and dirty worktree inventory | WP01: preserve/reconcile, reproducible source and CI. |
| TD02 P0 | Provider-key-shaped material persists in master source/compiled history | Section 17 safe history metadata | WP02: owner rotation, approved containment/history process and scanning. |
| TD03 P0 | D speaker attribution is time-modulo fabrication; legacy analysis failure can become randomized/default business output | stt_service.py and analysis_service.py findings in Sections 2/5 | WP04, WP09–13: evidence, truthful states and evaluated real providers. |
| TD04 P0 | Worker dispatch selects processing queue; inspected default worker subscribes celery | jobs/dispatcher.py, celery_app.py and compose worker commands | WP06: explicit routing plus real broker E2E/recovery tests. |
| TD05 P0 | Lease field without demonstrated renewal/recovery/outbox; retry can repeat expensive stages | W jobs/processor.py | WP06: heartbeat, fencing, reconciliation and checkpoints. |
| TD06 P0 | Multiple legacy auth boundaries; v1 is safer but isolation proof/RLS and operational roles incomplete | middleware/auth.py, security/context.py, saas_models.py | WP03/23: one auth boundary, PostgreSQL and cross-resource isolation. |
| TD07 P0 | Deletion commits before best-effort object deletion; retention field lacks demonstrated purge lifecycle | meetings.py deletion and meeting_service.py expiry | WP07: durable deletion/tombstones/legal hold. |
| TD08 P0 | Default container excludes local AI dependencies while default provider expects local Whisper | infra/docker/api.Dockerfile and configuration | WP09/19: explicit supported CPU/GPU image profiles and preflight. |
| TD09 P1 | Real diarization adapter unavailable; per-job provider creation defeats longer-lived model cache | providers/factory.py, whisper.py and processor.py | WP09/10: persistent inference and qualified attribution. |
| TD10 P1 | Provider selection is global configuration, not tenant privacy/capability/cost policy | providers/factory.py | WP08: stage-specific policies, typed failures and no unauthorized fallback. |
| TD11 P1 | Legacy Neo4j write call passes incompatible keyword arguments; nested dict training tips risk unsupported property values | call_service.py vs neo4j_connection.py | Preserve evidence; replace current path with relational-authority workflow, not broad legacy repair. |
| TD12 P1 | Legacy graph queries expose unscoped full properties; Copilot uses narrow global templates | graph_service.py and copilot_service.py | WP03/17; optional later safe projection, no raw graph API. |
| TD13 P1 | Current routed UI is meetings-oriented; old call/graph/analytics/incident/Copilot components are unrouted | frontend/src/App.tsx | WP12/14/15/22: coherent contact-center navigation and operational roles. |
| TD14 P1 | Speaker edit renames only; SQL LIKE search and v1 exports limited to text/JSON/subtitles | v1/insights.py, export_service.py | WP12/17/24: corrected revisions, scoped retrieval and report exports. |
| TD15 P1 | Demo plan has limits; other default paid plans lack useful entitlement configuration; startup rewrites defaults | tenancy_service.py | WP18/22: versioned plan catalog, approved tenant configuration and migrations. |
| TD16 P1 | Tests are meaningful but SQLite fixtures do not prove PostgreSQL locking/concurrency; real AI/queue/deployment untested here | 97-pass isolated run and inspected fixtures/CI | WP01/06/13/19/20: targeted integration and deployment gates. |
| TD17 P1 | MinIO pinned dependency now has archived upstream community repo; license/support qualification unresolved | Section 30 upstream metadata | WP05/19: storage contract and supported distribution selection. |
| TD18 P1 | Share “require auth” semantics need organization/recipient policy; deletion/export controls must follow it | v1/shares.py and UI defaults | WP03/07/24: explicit audience, revocation and audit. |
| TD19 P1 | Backup/CI/operations scripts exist but no completed restore or remote CI evidence was verified | outer docs/operations and infra | WP01/20: demonstrated release operations. |
| TD20 P0 | Public claims exceed demonstrated product, including simulation and assurance/integration promises | Section 27 live evidence | WP21: claim governance before broader selling. |

Do not repeat a generic debt checklist as fact. Migrations, storage adapters, async jobs, typed providers, media validation, session revocation and tests **do exist in W**. The debt is qualification, completeness and deployment coherence. Base64-upload inefficiency or wholly synchronous processing was not established as a universal defect of the current v1 path. A code-quality rewrite without preserving these useful foundations would add cost and regression risk.

# 33. Risk Register

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

# 34. Development Roadmap

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

# 35. Feature-by-Feature Implementation Plan

The 24 packages below cover **all 64 P0/P1 features** in the 95-feature catalogue. A package is a deliverable group, not one oversized PR. Each numbered step is a candidate small implementation task; split further if it spans independent migrations, APIs and UI. Preserve current working behavior with compatibility adapters until the replacement passes acceptance. No tasks were executed by this report.

Every task should carry feature/package IDs, affected current modules, input/output contract, migration and rollback considerations, authorization rules, tests, telemetry and documentation changes. Domain owners approve the desired behavior; engineering reviewers approve implementation. Estimates should follow discovery of these tasks, not the apparent length of a feature label.

## WP01 — Reproducible source and release baseline

Features F01, F02. Owner: engineering lead. Dependencies: none; coordinate credential containment before distributing artifacts. Start with both Git trees, existing CI, pyproject, migrations and infrastructure references.

1. Inventory tracked/dirty/untracked changes and preserve authorized source provenance without importing secrets or runtime data.
2. Agree product/website ownership and replace build dependence on an untracked nested checkout with pinned release artifacts.
3. Reconcile the v1 overlay into reviewable changes; keep legacy routes disabled and document compatibility boundaries.
4. Lock dependencies and generate reproducible API, worker and console builds from a clean checkout.
5. Add PostgreSQL migration, queue integration, frontend build and critical browser-flow jobs to existing CI.
6. Publish a release manifest with source SHA, image digests, schema/model versions and linked validation results.

Done when an independent clean checkout builds the same release without local-only files and both fresh and upgrade migrations pass. No broad repository move is required to begin.

## WP02 — Credential containment and deployment security

Features F03, F10. Owner: security/platform. Dependencies: inventory available; containment precedes external pilot access.

1. Give credential owners the safe path/history inventory; revoke/rotate affected credentials through their approved process and review provider usage.
2. Identify copied artifacts/forks/caches and prepare a separately approved history-cleanup plan; preserve incident evidence.
3. Replace embedded/default credentials with secret references and mandatory secure production settings.
4. Enable redacted secret scanning for new commits, full release content, image layers and support bundles.
5. Configure actual ingress TLS, internal network exposure limits and database/object/backup encryption with key rotation ownership.
6. Test invalid production configuration, exposed service ports, log redaction and certificate/key renewal on the supported topology.

Done when containment is documented, release scans have no unreviewed secret findings, and deployment controls are demonstrated. History rewriting does not substitute for revocation.

## WP03 — Identity, tenancy and operational permissions

Features F04, F05, F06. Owner: backend/security. Dependencies: WP01; use existing v1 session/context models as the starting point.

1. Define one login/refresh/reset/verification/session-family contract; remove legacy auth from public runtime composition.
2. Test rotation/reuse, logout, password-reset invalidation, disabled users, account linking, cookies and CSRF.
3. Add organization/team/agent assignments with effective dates and a written role/resource permission matrix.
4. Enforce tenant and record scope in repositories, objects, jobs, exports, shares, retrieval and later projections.
5. Add PostgreSQL RLS under a non-bypass application role; set tenant context transaction-locally and test pooled-connection reuse.
6. Add explicit recipient/organization share policies, revocation and short-lived download authorization.
7. Build adversarial two-tenant/two-team tests including background jobs, guessed IDs, stale tokens and changed membership.

Done when every route/resource has an explicit policy and negative tests prove zero unauthorized access in the release suite.

## WP04 — Truthful states and evidence contracts

Features F23, F24. Owner: backend/ML. Dependencies: WP01; schema work coordinates with WP03.

1. Define run/stage states including queued, running, completed, partial, failed, unavailable and insufficient evidence, with typed reasons.
2. Introduce immutable transcript revisions, evidence spans, analysis runs and model/prompt/schema provenance.
3. Make every finding reference existing authorized turns and original audio offsets; reject malformed references.
4. Exclude mock providers from production configuration and preserve explicit mock labels in any development fixtures.
5. Remove random/default analytics from production result serialization; unavailable metrics remain unavailable.
6. Expose quality, coverage and degradation consistently through API, UI, exports and usage eligibility rules.

Done when injected provider failures cannot produce completed-looking invented scores and all displayed findings resolve to their exact source revision.

## WP05 — Ingestion and private object lifecycle

Features F12, F13, F14. Owner: backend/platform. Dependencies: WP03/04.

1. Define an ingestion ledger keyed by workspace, source and external event ID, with payload-hash conflict behavior.
2. Harden streamed/resumable upload limits, MIME/signature checks and bounded ffprobe/decode validation.
3. Store quarantined originals privately; authorize object promotion only after validation and record checksums/channel metadata.
4. Implement retry-safe finalize/abort and orphan reconciliation for database/object partial failures.
5. Qualify one supported S3/private storage implementation through contract tests, including multipart and access denial.
6. Add malicious/corrupt/oversized/truncated media fixtures and retries, including a forged extension and decoding timeout.

Done when duplicates do not create extra analyses/charges, invalid media is contained, and unauthorized object access remains impossible through both API and storage URLs.

## WP06 — Durable processing and recovery

Feature F17. Owner: backend/platform. Dependencies: WP03–05.

1. Align queue names and worker subscriptions explicitly; add a real broker-to-worker smoke test.
2. Commit job and outbox event atomically, then publish with retry and acknowledgement tracking.
3. Add stage attempts/checkpoints with immutable input hashes and idempotent artifact/result writes.
4. Implement lease renewal, fencing tokens and a sweeper for abandoned work; reject stale worker commits.
5. Define cancellation, retryability, deadlines, poison/dead-letter behavior and bounded retry budgets.
6. Make usage and downstream events idempotent even when processing is delivered more than once.
7. Inject worker kill, broker restart, lost acknowledgement, duplicate delivery and object-write failure; reconcile all accepted jobs.

Done when accepted jobs are recoverable, terminal states are honest, completed stages are reused and duplicate delivery cannot duplicate charges or actions. Do not promise exactly-once execution; require idempotent effects.

## WP07 — Retention, deletion and text privacy

Features F15, F16. Owner: backend/security. Dependencies: WP03–06.

1. Define explicit immediate/finite/indefinite retention semantics and migrate the current ambiguous zero value safely.
2. Add legal-hold ownership, reason, expiry/review and audited release; separate hold from ordinary user preference.
3. Implement tombstone-first access denial, durable object/derived-data purge jobs and retry reconciliation.
4. Reapply deletion tombstones after restore and document backup expiry rather than promising instant backup erasure.
5. Add Persian/English text PII detection, configurable classes and restricted original-to-redacted mappings.
6. Route permitted analysis/retrieval/export to the correct redacted or original version and record policy provenance.
7. Test retention with a controllable clock, held records, failed storage deletion, mixed-language identifiers and subject-scope export.

Done when deletion completes across every store or exposes a retryable failure, holds are respected, and text redaction is never represented as audio masking or guaranteed anonymization.

## WP08 — Provider registry and privacy routing

Features F20, F21, F22. Owner: ML/platform. Dependencies: WP03/04/06/07.

1. Add versioned model/prompt/capability manifests with language, task, license, endpoint, region and structured-output support.
2. Define per-tenant stage policies and immutable policy snapshots on each run.
3. Implement privacy/eligibility filtering before health, quality, latency and cost selection.
4. Qualify chosen local and API adapters through identical typed contract tests, including schema rejection and usage reporting.
5. Add deadlines, rate limits, backoff, Retry-After, health circuits and approved same-policy fallback.
6. Block unauthorized egress at network and application layers; test local failure with cloud keys configured.

Done when each stage is traceable to a qualified model/policy and no outage can move protected data to an unapproved provider.

## WP09 — Channel-aware speech and persistent inference

Features F25, F26, F27, F28. Owner: audio/ML. Dependencies: WP05/06/08 and WP13 corpus contract.

1. Inspect channel metadata, duplicated/silent channels, sample rate and trusted PBX channel-to-role mapping.
2. Create channel-preserving normalized derivatives, VAD intervals and reversible original-time mapping.
3. Implement selected Whisper model profiles and language policy; compare base versus stronger multilingual candidates.
4. Add long-call chunking/checkpoints, overlap deduplication and no-speech handling.
5. Move model lifetime to persistent warmed worker processes with memory-aware concurrency and bounded batching.
6. Add CPU/GPU readiness/preflight and route work only to qualified workers.
7. Measure Persian/English/mixed and noisy/stereo/mono accuracy, latency, GPU memory and actual cost under admitted load.

Done when speech meets the agreed corpus gates and repeated jobs reuse warm models without channel loss or duplicated boundary text.

## WP10 — Diarization, overlap and alignment

Features F30, F31, F33. Owner: audio/ML. Dependencies: WP04/08/09/13.

1. Implement a real mono diarization adapter with pinned community-1 or a better measured qualified model.
2. Preserve anonymous speaker clusters and overlap intervals separately from display-exclusive turns.
3. Reconcile speaker identities across long-call chunks and constrain counts only when source evidence supports them.
4. Align words/turns to speaker intervals and original recording offsets; retain ambiguous/unknown assignments.
5. Calibrate confidence/quality flags using held-out annotations; do not convert similarity into unsupported probability.
6. Add timeline representation for overlapping speech and correction-ready turn attribution.
7. Evaluate DER/JER, count errors, role accuracy and span offsets, publishing collar/overlap/unknown policies.

Done when real attribution replaces fabrication, overlap survives serialization, and the stated diarization/alignment gates pass on the supported corpus.

## WP11 — Measured signals and versioned quality evaluation

Features F34, F36, F41, F42. Owner: ML/backend with QA domain owner. Dependencies: WP04/08–10/13.

1. Compute speech/silence/talk ratio/overlap/rate from intervals with precise metric definitions and denominators.
2. Define supported intent/topic/entity/outcome taxonomy and schema with evidence and unknown values.
3. Implement structured extraction validation, confidence/abstention policy and critical-entity review routing.
4. Create versioned scorecards, weights, applicability, thresholds and pass/fail/NA/insufficient-evidence semantics.
5. Add deterministic keyword/script checks and contextual criteria using qualified targeted model evaluation.
6. Persist criterion evidence, calculation version and score denominator; prohibit inferred NPS or unsupported churn probabilities.
7. Compare scores with adjudicated human QA and add regressions for negation, missing speakers, low ASR confidence and contradictory evidence.

Done when scores are reproducible from a frozen rule/model version, explainable criterion by criterion and within the Section 38 accuracy gates.

## WP12 — Call report, summaries and correction

Features F37, F45. Owner: frontend/backend/ML. Dependencies: WP03/04/09–11.

1. Evolve meeting records into a compatible Conversation model and call-oriented navigation without losing existing IDs.
2. Build the primary report around summary, outcome, QA/evidence and review action; progressively disclose advanced panels.
3. Add audio/transcript synchronized navigation, speaker/time filters and explicit incomplete-analysis notices.
4. Generate supported summary/decision/key-moment fields with evidence, abstaining on unspecified details.
5. Implement transcript text and turn-to-speaker correction as new immutable revisions, with reason and reviewer identity.
6. Mark dependent scores/summaries/search projections stale and selectively recompute against the corrected revision.
7. Test Persian/English report usability and keyboard playback/evidence navigation with representative supervisors.

Done when a supervisor can find a cited moment, correct attribution/text, understand version history and see consistent updated findings without developer help.

## WP13 — Corpus and promotion evaluation

Features F80, F81. Owner: ML/QA/product. Dependencies: authorized data access; starts immediately and gates later packages.

1. Obtain rights, retention/access rules and a sampling plan across accents, channels, intents and sensitive fields.
2. Create annotation guidelines, normalized/verbatim text policy, speaker/overlap labels, scorecards and evidence labels.
3. Assemble at least 20 Persian hours with a frozen 5-hour test set and separate English/mixed-language coverage.
4. Double-annotate at least 20%, adjudicate disagreements and prevent speaker/customer/time leakage between splits.
5. Implement WER/CER, DER/JER, entity/intent, QA agreement, factual-support and retrieval evaluators.
6. Publish slice sample sizes, uncertainty, abstention, coverage, latency and cost with each candidate result.
7. Gate model/prompt promotion on the frozen release criteria and maintain a rollback manifest; collect production corrections separately.

Done when evaluation is reproducible from an access-controlled manifest, data rights are recorded and every promoted model has a comparable signed-off report.

## WP14 — Review, calibration, appeals and coaching

Features F43, F44, F46, F47. Owner: product/frontend/backend. Dependencies: WP03/11/12.

1. Define ingestion/processing/evaluation/review coverage as separate counters and build stratified/random/risk-triggered sampling.
2. Create supervisor review queues with assignment, due time and record-level authorization.
3. Add criterion override with reason/evidence, preserving machine and human scores separately.
4. Add blinded calibration batches and reviewer agreement reporting against adjudicated examples.
5. Implement agent appeal, supervisor disposition and audit history with clear notifications.
6. Create specific coaching tasks from accepted findings, with owner, due date, acknowledgement and follow-up review.
7. Validate matched-task review time and agent/supervisor usability; prevent cohort/rubric mixing in comparisons.

Done when the full review-to-correction-to-coaching/appeal loop is traceable and coverage cannot be inflated by counting failed or ineligible calls.

## WP15 — Issue trends and role dashboards

Features F49, F61. Owner: backend/analytics/frontend. Dependencies: WP03/11/14; external outcomes enrich later.

1. Define issue taxonomy, deduplication policy, supported filters and minimum sample thresholds.
2. Build scoped SQL aggregates for call reasons, unresolved outcomes, QA and product/service complaints.
3. Show denominators, processing coverage, date range, scorecard version and missing-data indicators.
4. Provide executive aggregates, analyst drill-down, supervisor team comparisons and agent self-view.
5. Link each trend to authorized supporting conversations and distinguish correlation from confirmed root cause.
6. Add reference-dataset reconciliation, filter/permission tests and measured query performance.

Done when every chart reconciles with source records and users see the right level of detail without cross-team/tenant leakage or unfair unadjusted rankings.

## WP16 — Supported ingestion/API/integration contracts

Features F63, F64, F68, F69. Owner: integration/backend. Dependencies: WP03/05/06/08.

1. Publish versioned asynchronous REST/OpenAPI contracts and a minimal Python client/example with retries and idempotency.
2. Add scoped, expiring, hashed API credentials, rotation and usage/rate limits.
3. Qualify one actual Asterisk/FreePBX/Issabel deployment's recording/CDR export and source ID/channel mapping.
4. Add configurable CRM/ticket/customer/agent/outcome mappings, reconciliation and delayed-record handling.
5. Persist outbound webhook deliveries and sign raw payloads with timestamp/key ID; provide replay/deduplication guidance.
6. Add retry/dead-letter/admin diagnostics without exposing credentials or transcript text.
7. Run contract fixtures for duplicates, transfers, missing recordings, clock skew, stale signatures and revoked service identities.

Done when one supported PBX plus one generic CRM outcome path works end to end, with explicit supported versions rather than a universal integration claim.

## WP17 — Scoped retrieval and analytical Copilot

Features F57, F58. Owner: backend/ML/frontend. Dependencies: WP03/04/07/11/15 and WP13 evaluation.

1. Define query intents that require exact SQL aggregates versus evidence retrieval.
2. Index normalized lexical text and versioned embeddings with tenant, record ACL and revision metadata.
3. Apply authorization filters before retrieval and enforce them again on evidence access; test approximate filtered recall.
4. Add bounded hybrid search/reranking and visible source/time/quality filters.
5. Implement read-only allowlisted analytical tools with row/time limits, deterministic numeric results and cited answers.
6. Add abstention, unsupported-question handling and transcript prompt-injection defenses.
7. Evaluate 100 approved business questions plus adversarial tenant/team cases and deletion/reindex scenarios.

Done when exact counts are exact, narrative answers cite authorized evidence and the system refuses questions that cannot be answered reliably.

## WP18 — Usage, plans and commercial billing

Features F18, F19. Owner: backend/finance/product. Dependencies: WP03/06; pricing policy approved before customer charging.

1. Define billable completion, partial failure, rounding, reanalysis, overage and refund/credit rules.
2. Preserve and harden usage reservations/ledger with PostgreSQL concurrency and idempotent stage completion.
3. Create versioned plans with real entitlements; stop startup defaults from silently overwriting tenant commercial configuration.
4. Add period rollover, subscription changes, quota alerts, concurrency admission and ledger reconciliation.
5. Generate approved annual/manual invoices and payment status before adding an eligible payment-service adapter.
6. Authenticate/deduplicate payment events and separate entitlement activation from untrusted callback content.
7. Reconcile customer usage reports/invoices with ledger totals; test duplicate completion, cancellation, rollover and plan change.

Done when money and entitlements can be explained from immutable ledger entries, without duplicate charges or invisible plan resets.

## WP19 — Offline package, licensing and upgrades

Features F74, F75. Owner: platform/ML. Dependencies: WP01/02/05/08–10/18/20; local model quality gates remain mandatory.

1. Define supported private CPU/GPU topologies, capacity envelope, local IdP and storage choices.
2. Build signed offline images/model bundles with pinned hashes, licenses and required runtime dependencies.
3. Add preflight for driver/VRAM/disk/ports/TLS, local model readiness and no-internet configuration.
4. Implement signed offline entitlements, expiry/grace/read-export behavior and operator-visible counters.
5. Package install, upgrade, backup/restore, rollback and sanitized support-bundle commands/runbooks.
6. Test install and representative processing/export/deletion with all outbound network access denied.
7. Rehearse upgrade from the prior supported version with real fixture data, failed migration recovery and customer-admin handoff.

Done when an operator can install and operate the exact documented offline profile without hidden downloads, cloud auth or developer intervention.

## WP20 — Observability, audit and recovery

Features F09, F78, F79. Owner: platform/backend/security. Dependencies: WP01–06; evolves throughout implementation.

1. Define correlation IDs and stage latency/error/queue/GPU/usage-cost metrics with sensitive-field redaction.
2. Add append-only application audit events for authentication, settings, access-sensitive exports, reviews, actions and deletion.
3. Deliver audit records to a separately protected sink and document integrity/retention/reconstruction procedures.
4. Create actionable alerts for stopped workers, lease backlog, provider failure, purge failure and storage/capacity limits.
5. Back up relational data, object manifests, keys/configuration references and deletion state consistently.
6. Restore into an isolated environment, reapply tombstones and verify object/DB integrity and access policies.
7. Measure proposed RPO 24h/RTO 8h and run incident/support drills with no transcript or secret leakage in diagnostics.

Done when operators can locate a failed job, reconstruct a sensitive action and restore a supported installation within the agreed recovery envelope.

## WP21 — Truthful website and measurable sales funnel

Features F83, F84, F85. Owner: product/marketing/frontend. Dependencies: WP01/04 for claims; WP12/14/19 evidence for product demonstrations.

1. Inventory every claim, case study, logo, price, supported language and integration against proof and owner.
2. Qualify/remove unsupported availability or outcome statements and label public fixtures/simulation explicitly.
3. Build a focused Persian/English homepage and real-version product/private/security pages.
4. Implement accessible pilot lead capture with delivery monitoring, privacy text and clear response ownership.
5. Create a permissioned demonstration using approved data; keep public simulation visibly separate.
6. Implement ROI inputs/formulas with baseline costs, matched-review savings and no double-counted speculative revenue.
7. Track consented funnel conversion and generate a pilot report separating measured outcomes from projections.

Done when published claims map to released evidence, the lead flow reaches its owner and the ROI example can be independently recomputed.

## WP22 — Administration, i18n and documentation

Features F72, F86, F92. Owner: frontend/backend/product documentation. Dependencies: WP03/07/08/11; document earlier packages as they land.

1. Define a role-specific information architecture and complete Persian/English message catalog, including errors and empty/degraded states.
2. Standardize locale-aware dates/numbers and Persian/Arabic text normalization without changing raw evidence.
3. Test RTL/LTR layout, focus order, keyboard controls, screen-reader labels and narrow-screen review workflows.
4. Build validated admin forms for providers, scorecards, retention, quotas and integration settings with secret masking.
5. Version changes, preview their impact, enforce permission checks and support rollback/audit.
6. Write product, API, architecture, install, SaaS/private admin, integration, security, backup, upgrade, troubleshooting and model/QA configuration guides.
7. Have a new operator and supervisor complete installation/admin/review tasks from documentation alone; correct observed gaps.

Done when supported locales and configuration flows are complete and documentation matches the exact released profile.

## WP23 — Enterprise federation and independent security qualification

Features F07, F82. Owner: identity/security/platform. Dependencies: WP02/03/19; external testing follows a stable candidate.

1. Qualify customer OIDC federation and SAML via supported broker; document callback, claim mapping and account linking.
2. Map groups to explicit application memberships/roles and define deprovisioning without pretending OIDC provides SCIM.
3. Enforce privileged MFA and test disabled users, revoked sessions, group removal and controlled break-glass access.
4. Run dependency/container/SBOM/secret scans and triage findings by reachable exploitability and supported versions.
5. Commission a scoped independent penetration test of API, browser, tenancy, upload, shares, jobs and private topology.
6. Remediate critical/high findings and retest; publish only the actual report scope/date and approved customer summary.

Done when federation lifecycle is proven and no unresolved critical/high security finding remains in the supported release scope.

## WP24 — Approved actions, alerts and exports

Features F38, F59, F60, F70. Owner: backend/frontend/integration. Dependencies: WP03/04/07/11/12/14/16.

1. Extract explicit commitments/action items with evidence; keep missing owner/date unset for reviewer completion.
2. Define alert rules, deduplication windows, severity, recipient scope and daily digest/suppression behavior.
3. Build a preview/approve/reject workflow for CRM/ticket/coaching actions with authorized human ownership.
4. Execute approved actions through scoped connectors with idempotency, retry, cancellation and delivery/audit records.
5. Add PDF, CSV and JSON report exports plus existing transcript formats with version/provenance and ACL controls.
6. Validate Persian shaping/fonts, formula-injection handling in CSV, large export limits, retention and authorized downloads.
7. Test rejected/revoked/duplicate approvals, stale source revisions and connector failure without duplicate external effects.

Done when users control external writes, accepted actions remain traceable and exported reports preserve the same evidence and privacy semantics as the UI.

# 36. Dependency Graph

Evidence contracts, authorized data and reliable execution precede business scores. Evaluation starts alongside foundations and gates every model-dependent release. Documentation and claim governance run continuously; they are not postponed until launch.

~~~mermaid
flowchart TD
    A[WP01 Source and CI] --> B[WP02 Security containment]
    A --> C[WP03 Identity and tenancy]
    A --> D[WP04 States and evidence]
    E[WP13 Authorized corpus and evaluation] --> J[WP09 Channel-aware STT]
    C --> F[WP05 Private ingestion]
    D --> F
    F --> G[WP06 Durable jobs]
    G --> H[WP07 Retention and privacy]
    H --> I[WP08 Provider policy]
    I --> J
    J --> K[WP10 Diarization and alignment]
    K --> L[WP11 Signals and QA]
    E --> L
    L --> M[WP12 Report and correction]
    M --> N[WP14 Review and coaching]
    N --> O[WP15 Trends and dashboards]
    O --> P[WP17 Search and Copilot]
    E --> P
    G --> Q[WP16 PBX CRM API and webhooks]
    Q --> R[WP24 Approved actions and exports]
    N --> R
    G --> S[WP18 Usage and billing]
    G --> T[WP20 Audit observability and recovery]
    B --> T
    T --> U[WP19 Offline package and upgrades]
    S --> U
    K --> U
    U --> V[WP23 Federation and security qualification]
    C --> V
    I --> W[WP22 Admin i18n and documentation]
    L --> W
    M --> X[WP21 Claims demo and ROI]
    U --> X
    P --> Y[v1 release gates]
    R --> Y
    V --> Y
    W --> Y
    X --> Y
~~~

The diagram shows dominant dependencies, not every shared authorization/test dependency. WP20's telemetry starts with WP01 and gains full recovery proof after jobs/storage exist. WP21's claim audit begins immediately; its real-product demo waits for the demonstrated profile. WP22 documentation follows each package. Parallel engineering streams can cover platform, audio/evaluation, and interface/customer workflow, but shared schemas and release gates need one coordinating owner.

Specific non-negotiable dependencies:

- Real role-specific QA requires usable transcript + trustworthy speaker/role attribution + versioned rubric + evidence mapping + adjudicated evaluation. Missing any one means review/abstention, not a fabricated score.
- Cross-call trends require stable source IDs + supported taxonomy + coverage denominators + tenant/team scope. A graph is optional.
- Grounded Copilot requires scoped retrieval + deterministic aggregate tools + revision-aware citations + a permission attack suite.
- Billable processing requires durable job outcome + idempotent usage ledger + an agreed partial/failure policy.
- Offline availability requires local speech/LLM/embedding models + dependency/model bundles + local identity/storage + denied egress + installation/upgrade/restore proof.
- Action execution requires grounded proposal + authorized approval + least-privilege connector + idempotent delivery and audit.

# 37. Release Plan

Version labels below are proposed qualification milestones. A version string already present in pyproject or a website footer does not establish completion. Reserve a candidate suffix until the corresponding gate passes.

| Milestone | Release scope | Required evidence | Commercial permission |
|---|---|---|---|
| v0.5 foundation candidate | Source reconciliation, secrets, v1 identity/tenancy, honest states, durable ingestion/jobs and deletion | Clean build; P0 security/isolation/recovery checks; corpus protocol; claim correction | Internal demonstrations with explicit scope. No blanket production readiness claim. |
| v0.6 measured voice candidate | Real channel-aware STT/diarization, evidence and evaluated extraction/QA | Held-out audio results, real queue/GPU pipeline and failure-state proof | Scoped paid pilot only after all applicable P0 gates and data terms. |
| v0.7 Quality pilot | Call report, supervisor review, calibration, appeals, coaching and issue trends | End-to-end user tests, matched review-time measurement and scoped source integration | Repeatable paid pilot; no unreleased enterprise/offline promise. |
| v0.8 delivery qualification | Offline bundle, local models, audit/restore, federation, usage/billing and approved actions | Denied-egress install/upgrade/recovery, identity lifecycle and invoice reconciliation | Qualified private/cloud contracts limited to supported profiles. |
| v0.9 release candidate | Complete P0/P1 scope, docs, security retest, load/quality regression and buyer acceptance | All Section 38 gates, open-defect disposition, signed manifests and rollback | Controlled production rollout with real support ownership. |
| v1.0 Commercial | Stable supported product and two independently qualified deployment profiles | Release board confirms technical, customer, security and operational acceptance | General commercial offer for the published supported scope. |
| v1.x expansion | Selected P2 integrations, VOC/graph, dedicated controls and domain packs | Buyer demand plus feature-specific tests, evaluation and margin/support case | New claims only after that feature's actual release. |

Use semantic versioning for public API contracts and migrations, while explicitly versioning model/prompt/scorecard behavior. A model update that changes scores requires evaluation and customer-visible release notes even if the API is unchanged. Maintain one supported private release line initially; define patch cadence, end-of-support notice and urgent security process in contracts. Never advertise an LTS duration the team cannot staff.

Each release includes change log, source/image/model hashes, migration compatibility, new/changed capabilities by deployment profile, known limitations, evaluation results, license notices, SBOM, installation/upgrade instructions, restore rehearsal and rollback/roll-forward decision. Default new inference behavior behind a controlled organization-level rollout, preserving previous report provenance. Release evidence should be stored outside mutable CI logs with an accountable approver.

# 38. Acceptance Criteria

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

# 39. What NOT To Build

- A horizontal meeting bot/calendar suite before contact-center QA is adopted. Preserve generic conversation storage; defer joining meetings and recording bots.
- Real-time agent assistance, voicebots or telephony replacement in v1. Post-call ingestion avoids streaming latency, call-control and safety complexity.
- Emotional state, personality, honesty or employee-worth scoring from voice. Do not treat sentiment as a psychological diagnosis or a reliable basis for employment decisions.
- Predicted NPS, churn probability, conversion uplift or customer lifetime value without real survey/outcome labels, calibration and evaluation. Surface cited signals instead.
- Automatic employee penalties, customer credit/insurance decisions, irreversible account changes or unsupervised external messages from model output.
- A mandatory knowledge graph for every table, raw Cypher exposed to users, or unrestricted agent-generated database commands. SQL aggregates and scoped retrieval solve most initial questions.
- Nine separately priced “AI agents,” broad autonomous multi-agent orchestration, or an extra LLM pass per module when one structured extraction can serve several needs.
- A proprietary ASR foundation model, vector database, SAML implementation, payment processor or distributed job scheduler.
- Separate codebases per industry/customer, separate SaaS/private feature implementations, or microservices for architectural appearance.
- Universal PBX/CRM compatibility, an unsupported GPU/hardware matrix, or Kubernetes before supported Compose/reference deployments need it.
- Cross-company voice/customer benchmarking or voiceprint enrollment before rights, privacy, evaluation and buyer value are established.
- An unlimited-processing plan, lifetime unlimited support, an untested 99.9% SLA, invented case-study outcomes or unearned certification claims.
- A cosmetic wholesale rewrite that discards the useful local session, tenant, quota, storage and provider work. Reconcile and prove these foundations feature by feature.

# 40. Final Recommended Product Strategy

Build **Homayar Commercial v1.0** as a Persian-first customer-conversation intelligence platform focused on contact-center quality and operational issues. Its core promise is reviewable evidence: what was said, who can reliably be attributed, which criterion applies, why a finding exists and what an authorized person chose to do about it. Offer SaaS and a separately qualified private/offline deployment through the same product contracts and codebase.

The implementation order is decisive: contain exposed historical credentials; preserve/reconcile the most advanced local work; establish one authenticated tenant-safe release; fix queue execution and recovery; make failure states honest; deliver measured real speech/diarization and evidence; complete supervisor review/coaching/trends; qualify offline installation, billing, identity, backup and support; then publish only demonstrated capabilities. A graph can strengthen later cross-call analysis, but does not unblock any of these foundations.

| Review lens | Assessment and required decision |
|---|---|
| Investor | Plausible narrow market with private/Persian differentiation, but no verified TAM, revenue, willingness to pay or retention evidence. Require paid pilots, implementation capacity, recurring margin and renewal signals before funding breadth. |
| CTO | W's relational modular-monolith direction is sounder than preserving legacy graph authority. Highest risks are source provenance, real runtime configuration, privacy/recovery and unqualified AI. Preserve useful abstractions and prove them in supported deployments. |
| Customer | Value is faster, more defensible review and useful operational follow-through. Trust depends on evidence, corrections, transparent limitations, reliable installation and ownership of data. A beautiful simulation cannot answer these questions. |
| Competitor | Global platforms can bundle transcription, QA, assistants and integrations; local vendors already advertise Persian speech and analytics. They can copy a feature list. Homayar must make local deployment, domain accuracy, trusted workflows and repeatable delivery measurably better for its chosen buyer. |

Go forward with two or three paid design partners in the selected service/BPO/ISP wedge, ideally including one private installation. A v1 release requires all applicable P0/P1 acceptance gates, clear supported scope, accountable operations and customer acceptance. Pilot success should establish accurate enough evidence and QA, reduced review effort, real supervisor adoption, an accepted price and support costs compatible with the business model.

If the speech/QA gates fail, narrow the supported audio/domain scope or change the model before selling automatic scoring. If technical gates pass but buyers will not pay enough, revise the buyer/problem/package before building more modules. If private installations repeatedly require bespoke engineering, simplify the supported topology and delivery contract before scaling channel sales. If graph-based insights do not improve decisions over SQL trends, keep graph optional.

The immediate next coding phase should begin with WP01–04 and the WP13 corpus program, with WP02 credential-owner containment and WP21 claim correction treated as urgent. This report itself authorizes none of the proposed mutations: repository reconciliation, credential changes, customer contact and publication remain future implementation actions under the appropriate task authorization.

One process improvement is worth adopting later: a reusable, read-only release audit that records branch/dirty-state provenance, capability evidence, safe secret metadata, tests, model evaluations and website claim status into one manifest. This would reduce repeated investigation and documentation drift. It is a suggestion only; no organizational tool, workflow, memory system or automation was modified.

Audit closure: this document was created outside both repositories. Existing backend tests were run against an isolated copy; current branch/history, source, public pages and primary external references support the findings within the stated limits. Secret values are deliberately excluded. The result is a due-diligence and implementation blueprint, not a claim that Homayar has already reached the target architecture or commercial readiness.

Final consistency checks confirmed all 40 required sections, 95 catalogue features, 24 implementation packages and an exact acceptance-criteria mapping for every one of the 64 P0/P1 features. All 25 local evidence links resolve, table structures are consistent, and the final Git status of each original repository matches its pre-audit snapshot.
