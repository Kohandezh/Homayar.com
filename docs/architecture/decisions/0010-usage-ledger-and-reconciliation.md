# ADR 0010 — Idempotent usage ledger, settlement invariants and reconciliation

Date: 2026-09-09 · Status: accepted · Package: WP06-T06

## Context

Processing is delivered at least once (ADR 0007) and fenced (ADR 0008), and
parked work is replayed with fresh reservations (ADR 0009). The allowance
bookkeeping underneath was thinner than those guarantees: `usage_events`
recorded only the `committed` transition (one row per reservation, enforced
by a unique column), reservations and releases left no ledger trace, nothing
in the database prevented a meeting from being settled twice through two
reservations, and no process compared the entitlement counters
(`used_value` / `reserved_value`) with the reservation rows or released a
reservation that could never settle.

## Decision

1. **Stable business-operation identities.** A reservation is identified by
   the upload's idempotency key (`<key>:audio`, `<key>:concurrency`) and, for
   a replay, by `replay:<job>:<n>:<metric>`; retries of an attempt reuse the
   job's reservation. Every ledger row carries `operation_key =
   <reservation id>:<event type>` (or a reconciliation identity), unique in
   the database, and the pair (reservation, event type) is unique too. No
   path generates a new identity per retry.
2. **Append-only ledger of every transition.** `usage_events` records
   `reserved` (allowance taken), `committed` (the one billable settlement,
   with provider metadata), `released` (given back, with the reason) and
   `adjusted` (reconciliation only, with the deltas). `reserve_usage`,
   `commit_usage` and `release_usage` remain idempotent on the reservation
   state under a row lock; the unique keys are the database backstop.
3. **Settlement invariant in PostgreSQL.** A partial unique index allows at
   most one `COMMITTED` reservation per meeting and metric, whatever retries,
   redeliveries, recoveries or replays happened. A future re-analysis product
   must introduce its own revision identity before a second charge is
   representable.
4. **Reconciliation, separated from charging.** `backend.jobs.usage_reconciliation`
   (Celery beat `usage-reconciliation`, or `python -m
   backend.jobs.usage_reconciliation [--repair]`) discovers entitlements
   across tenants under the read-only `usage_reconciliation` RLS scope and
   reconciles each in its own tenant-bound transaction holding the entitlement
   row `FOR UPDATE`: reservations still `RESERVED` after
   `USAGE_RECONCILIATION_GRACE_SECONDS` whose meeting is gone or that no
   queued/running job references are released (`reconciled_orphan`); counters
   are compared with the sums implied by the reservation rows. Drift is logged
   and audited (`usage.reconciled`, outcome `failure`); it is corrected only
   when `USAGE_RECONCILIATION_REPAIR_COUNTERS` (or `--repair`) is set, through
   one idempotent `adjusted` row whose key encodes the observed→expected
   counters. Historical usage and balances are never rewritten silently, and
   reconciliation never creates a customer charge.

## Consequences

- Migration `b9e4f2a6c8d3`: `operation_key` (backfilled as
  `<reservation>:committed`), `reason`, the two unique constraints, the
  workspace/time index, the partial unique index (the migration refuses to run
  when a meeting already has two committed reservations for a metric), and the
  `entitlements` policy scope. Downgrade deletes the ledger rows the old schema
  cannot represent (`reserved` / `released` / `adjusted`) and restores the
  one-event-per-reservation constraint.
- API: `GET /billing/usage/events` now lists all four event types and exposes
  `operation_key` and `reason`; consumers that summed every row as consumption
  must filter on `event_type = committed`.
- Downstream events: today the only effects of completion besides the ledger
  are provider-usage rows and audit rows, both written inside the fenced
  settlement transaction. Webhook/notification delivery (WP16) must key its
  deliveries on these operation identities.
- Not covered: billing periods and period rollover of counters (WP18 — until
  then reconciliation assumes the ledger and the counters cover the same
  period), invoice reconciliation, the empty-transcript charging policy
  (ADR 0003, still open), fault injection (WP06-T07).

## Evidence

`backend/tests/test_usage_ledger.py`: each transition recorded once with stable
keys and visible through the billing API; the database refuses a second
`committed` row and a second `COMMITTED` reservation per meeting/metric;
dead-letter → replay → completion → duplicate delivery charges once;
concurrent settlement of one reservation commits once (PostgreSQL);
reconciliation releases orphans, ignores live reservations of the same age,
reports drift without touching counters, repairs once on request and is inert
afterwards; the reconciliation scope reads entitlements only and cannot
write (PostgreSQL); settings validation. Existing lease/outbox tests updated to
count settlements rather than every ledger row.
