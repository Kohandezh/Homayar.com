# Explicit blueprint dependency graph

Generated 2026-09-07T14:48:43.673373+00:00. Deterministic extraction of explicit blueprint/backlog relationships; no external LLM/API calls and zero billed extraction tokens. Generated relationships are static evidence, not execution proof. Planned capabilities are in the separate planning graph. Community labels are deterministic source-directory descriptions.

Integrity diagnostics: {"missing_endpoint_edges": 0, "dangling_endpoint_edges": 0, "self_loop_edges": 0, "directed_same_endpoint_collapsed_edges": 0, "undirected_same_endpoint_collapsed_edges": 0}. Raw relations are preserved in extraction.json; the rendered graph may consolidate same-endpoint relations.

# Graph Report - /Users/emperor/Documents/AI/Homayar.com  (2026-09-07)

## Corpus Check
- 25 files · ~0 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 119 nodes · 148 edges · 37 communities (6 shown, 31 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- planning · 0
- planning · 1
- planning · 2
- planning · 3
- planning · 4
- planning · 5
- planning · 6
- planning · 7
- planning · 8
- planning · 9
- planning · 10
- planning · 11
- planning · 12
- planning · 13
- planning · 14
- planning · 15
- planning · 16
- planning · 17
- planning · 18
- planning · 19
- planning · 20
- planning · 21
- planning · 22
- planning · 23
- planning · 24
- planning · 25
- planning · 26
- planning · 27
- planning · 28
- planning · 29
- planning · 30
- planning · 31
- planning · 32
- planning · 33
- planning · 34
- planning · 35
- planning · 36

## God Nodes (most connected - your core abstractions)
1. `WP03 Identity, tenancy and operational permissions` - 18 edges
2. `WP11 Measured signals and versioned quality evaluation` - 15 edges
3. `WP04 Truthful states and evidence contracts` - 14 edges
4. `WP08 Provider registry and privacy routing` - 13 edges
5. `WP09 Channel-aware speech and persistent inference` - 12 edges
6. `WP19 Offline package, licensing and upgrades` - 12 edges
7. `WP05 Ingestion and private object lifecycle` - 11 edges
8. `WP24 Approved actions, alerts and exports` - 11 edges
9. `WP06 Durable processing and recovery` - 10 edges
10. `WP07 Retention, deletion and text privacy` - 10 edges

## Surprising Connections (you probably didn't know these)
- `F01 Release/source provenance` --planned_in_package--> `WP01 Reproducible source and release baseline`  [EXTRACTED]
  docs/planning/backlog.json → docs/planning/work-packages/WP01/README.md
- `F02 CI, migrations and regression suites` --planned_in_package--> `WP01 Reproducible source and release baseline`  [EXTRACTED]
  docs/planning/backlog.json → docs/planning/work-packages/WP01/README.md
- `F03 Secret containment and scanning` --planned_in_package--> `WP02 Credential containment and deployment security`  [EXTRACTED]
  docs/planning/backlog.json → docs/planning/work-packages/WP02/README.md
- `F04 Unified login, refresh, reset, verification` --planned_in_package--> `WP03 Identity, tenancy and operational permissions`  [EXTRACTED]
  docs/planning/backlog.json → docs/planning/work-packages/WP03/README.md
- `F05 Tenant isolation across DB, files, jobs, search` --planned_in_package--> `WP03 Identity, tenancy and operational permissions`  [EXTRACTED]
  docs/planning/backlog.json → docs/planning/work-packages/WP03/README.md

## Communities (37 total, 31 thin omitted)

### Community 0 - "planning · 0"
Cohesion: 0.12
Nodes (20): F23 Honest stage states and mock exclusion, F24 Evidence/revision/provenance schema, F37 Summaries, decisions and key moments, F38 Action items, owners, deadlines and commitments, F43 Automated coverage and review sampling, F44 Supervisor review and score override, F45 Transcript and turn/speaker correction, F46 Calibration and appeal workflow (+12 more)

### Community 1 - "planning · 1"
Cohesion: 0.14
Nodes (18): F04 Unified login, refresh, reset, verification, F05 Tenant isolation across DB, files, jobs, search, F06 Teams, supervisor and agent record permissions, F12 Adversarial media validation, F13 Idempotent upload and ingestion ledger, F14 Private object storage lifecycle, F17 Durable queue, leases, checkpoints and recovery, F18 Usage metering, quotas and plan limits (+10 more)

### Community 2 - "planning · 2"
Cohesion: 0.15
Nodes (16): F01 Release/source provenance, F02 CI, migrations and regression suites, F03 Secret containment and scanning, F07 Enterprise OIDC/SAML federation and MFA, F09 Audit trail and tamper-evident export, F10 TLS, encryption, key rotation, restricted networks, F74 Air-gapped local STT/LLM/embed package, F75 Offline license, upgrade and secure support bundle (+8 more)

### Community 3 - "planning · 3"
Cohesion: 0.18
Nodes (12): F25 Channel-aware speaker attribution, F26 Audio normalization, VAD and quality checks, F27 Persian and English production STT, F28 Persistent GPU workers and bounded batching, F30 Mono and multiparty diarization, F31 Overlap detection and attribution, F33 Timestamp alignment and calibrated confidence, F80 Persian gold corpus and slice benchmarks (+4 more)

### Community 4 - "planning · 4"
Cohesion: 0.20
Nodes (11): F15 Retention, deletion, legal hold and subject export, F16 Text PII redaction and access policy, F20 Model/prompt registry and provider capabilities, F21 Tenant privacy/region routing and egress denial, F22 Provider retry, health and circuit breakers, F72 Persian RTL/English LTR, dates, digits, accessibility, F86 Product/API/install/admin/security documentation, F92 Tenant provider/scorecard/retention administration (+3 more)

### Community 5 - "planning · 5"
Cohesion: 0.20
Nodes (11): F34 Silence, talk ratio, overtalk and speaking rate, F36 Intent, topics, entities and resolution status, F41 Versioned custom weighted QA scorecards, F42 Keyword, script and compliance evidence rules, F49 Complaint, product and operational issue trends, F57 Scoped lexical and semantic search, F58 Evidence-backed analytical Copilot, F61 Role-specific dashboards and comparisons (+3 more)

## Knowledge Gaps
- **95 isolated node(s):** `F01 Release/source provenance`, `F02 CI, migrations and regression suites`, `F03 Secret containment and scanning`, `F04 Unified login, refresh, reset, verification`, `F05 Tenant isolation across DB, files, jobs, search` (+90 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **31 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `WP11 Measured signals and versioned quality evaluation` connect `planning · 5` to `planning · 0`, `planning · 3`, `planning · 4`?**
  _High betweenness centrality (0.010) - this node is a cross-community bridge._
- **Why does `WP19 Offline package, licensing and upgrades` connect `planning · 2` to `planning · 1`, `planning · 3`, `planning · 4`?**
  _High betweenness centrality (0.007) - this node is a cross-community bridge._
- **Why does `WP08 Provider registry and privacy routing` connect `planning · 4` to `planning · 0`, `planning · 1`?**
  _High betweenness centrality (0.006) - this node is a cross-community bridge._
- **What connects `F01 Release/source provenance`, `F02 CI, migrations and regression suites`, `F03 Secret containment and scanning` to the rest of the system?**
  _95 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `planning · 0` be split into smaller, more focused modules?**
  _Cohesion score 0.12105263157894737 - nodes in this community are weakly interconnected._
- **Should `planning · 1` be split into smaller, more focused modules?**
  _Cohesion score 0.13725490196078433 - nodes in this community are weakly interconnected._