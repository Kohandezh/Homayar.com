# WP24 — Approved actions, alerts and exports

Status: NOT STARTED by this preparation task. Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP03, WP04, WP07, WP11, WP12, WP14, WP16.

## Implementation instructions

Features F38, F59, F60, F70. Owner: backend/frontend/integration. Dependencies: WP03/04/07/11/12/14/16.

1. Extract explicit commitments/action items with evidence; keep missing owner/date unset for reviewer completion.
2. Define alert rules, deduplication windows, severity, recipient scope and daily digest/suppression behavior.
3. Build a preview/approve/reject workflow for CRM/ticket/coaching actions with authorized human ownership.
4. Execute approved actions through scoped connectors with idempotency, retry, cancellation and delivery/audit records.
5. Add PDF, CSV and JSON report exports plus existing transcript formats with version/provenance and ACL controls.
6. Validate Persian shaping/fonts, formula-injection handling in CSV, large export limits, retention and authorized downloads.
7. Test rejected/revoked/duplicate approvals, stale source revisions and connector failure without duplicate external effects.

Done when users control external writes, accepted actions remain traceable and exported reports preserve the same evidence and privacy semantics as the UI.

## Feature-level acceptance

### F38 — Action items, owners, deadlines and commitments (P1)

Current audited state: W partial. Target: Evidence-linked tasks; no invented owners.

- [ ] Explicit owner/date extraction precision ≥95% on held-out examples; unspecified values remain unset; every item links evidence and revision; proposed next steps are labeled separately from commitments.

### F59 — Post-call/digest alerts and review queues (P1)

Current audited state: D partial. Target: Rate-limited, deduplicated actionable alerts.

- [ ] Repeated same-condition events create one alert per configured deduplication window; only authorized recipients notified; suppressed/acknowledged/escalated states and failed delivery visible.

### F60 — Approval-based CRM/ticket/coaching actions (P1)

Current audited state: W internal action CRUD. Target: Preview, approve, execute, audit and reconcile.

- [ ] Rejected/unapproved/revoked/stale proposals cause zero external writes; repeated approved delivery causes one logical CRM/ticket effect; connector failures retry with complete audit.

### F70 — PDF, CSV, JSON and transcript exports (P1)

Current audited state: D PDF; W TXT/JSON/subtitles. Target: Scoped bilingual reports and data export.

- [ ] PDF/CSV/JSON and existing subtitle/text exports match authorized revision; Persian font/shaping and RTL manually checked; CSV formula injection neutralized; revoked access and retention apply to exported objects.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
