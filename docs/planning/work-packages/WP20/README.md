# WP20 — Observability, audit and recovery

Status: NOT STARTED by this preparation task. Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP01, WP02, WP03, WP04, WP05, WP06.

Telemetry design starts with WP01; dependency list describes full recovery acceptance, not the earliest permissible work.

## Implementation instructions

Features F09, F78, F79. Owner: platform/backend/security. Dependencies: WP01–06; evolves throughout implementation.

1. Define correlation IDs and stage latency/error/queue/GPU/usage-cost metrics with sensitive-field redaction.
2. Add append-only application audit events for authentication, settings, access-sensitive exports, reviews, actions and deletion.
3. Deliver audit records to a separately protected sink and document integrity/retention/reconstruction procedures.
4. Create actionable alerts for stopped workers, lease backlog, provider failure, purge failure and storage/capacity limits.
5. Back up relational data, object manifests, keys/configuration references and deletion state consistently.
6. Restore into an isolated environment, reapply tombstones and verify object/DB integrity and access policies.
7. Measure proposed RPO 24h/RTO 8h and run incident/support drills with no transcript or secret leakage in diagnostics.

Done when operators can locate a failed job, reconstruct a sensitive action and restore a supported installation within the agreed recovery envelope.

## Feature-level acceptance

### F09 — Audit trail and tamper-evident export (P1)

Current audited state: W partial. Target: Actor, scope and immutable external sink.

- [ ] Every tested privileged configuration, export, review override, action and deletion records actor, scope, time, reason and result; protected sink detects a deliberately altered/missing event in an integrity drill.

### F78 — Metrics, traces, cost, queue/GPU health and SLOs (P1)

Current audited state: W basic logs/health. Target: End-to-end operational visibility.

- [ ] A failed/stuck job is discoverable by correlation ID; alert fires within 5 minutes for configured worker/purge outage; 24-hour mixed-load run achieves ≥99% valid supported processing and proposed p95 ≤15 minutes for 30-minute calls on reference GPU at admitted load.

### F79 — Encrypted backups and restore drills (P0)

Current audited state: S scripts. Target: Verified RPO/RTO and deletion-safe restores.

- [ ] Restore rehearsal on isolated supported topology meets proposed RPO ≤24h/RTO ≤8h; DB/object manifests reconcile; authorization works and deletion tombstones are reapplied before access.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
