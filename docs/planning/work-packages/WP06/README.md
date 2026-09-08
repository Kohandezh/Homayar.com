# WP06 — Durable processing and recovery

Status: IN PROGRESS since 2026-09-08 (T01 queue contract, T02 transactional outbox and T04 execution leases/fencing/recovery validated in local scope — see [ADR 0007](../../../architecture/decisions/0007-dispatch-outbox.md) and [ADR 0008](../../../architecture/decisions/0008-execution-leases-and-fencing.md); T03, T05, T06, T07 not started: stage checkpoints, cancellation/deadline/dead-letter policy and replay, idempotent usage events under redelivery, fault injection. Broker evidence so far is a Celery worker integration test on the in-memory transport; Redis-backed transport and deployed beat remain unverified). Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP03, WP04, WP05.

## Implementation instructions

Feature F17. Owner: backend/platform. Dependencies: WP03–05.

1. Align queue names and worker subscriptions explicitly; add a real broker-to-worker smoke test.
2. Commit job and outbox event atomically, then publish with retry and acknowledgement tracking.
3. Add stage attempts/checkpoints with immutable input hashes and idempotent artifact/result writes.
4. Implement lease renewal, fencing tokens and a sweeper for abandoned work; reject stale worker commits.
5. Define cancellation, retryability, deadlines, poison/dead-letter behavior and bounded retry budgets.
6. Make usage and downstream events idempotent even when processing is delivered more than once.
7. Inject worker kill, broker restart, lost acknowledgement, duplicate delivery and object-write failure; reconcile all accepted jobs.

Done when accepted jobs are recoverable, terminal states are honest, completed stages are reused and duplicate delivery cannot duplicate charges or actions. Do not promise exactly-once execution; require idempotent effects.

## Feature-level acceptance

### F17 — Durable queue, leases, checkpoints and recovery (P0)

Current audited state: W partial. Target: No lost/stuck jobs or duplicate effects.

- [ ] Worker kill, broker outage, lost ACK, duplicate delivery and storage fault leave no lost accepted job; abandoned work resumes within two configured sweeper intervals after recovery; stale worker commits rejected; completed stages and financial/action effects are not duplicated.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
