# WP18 — Usage, plans and commercial billing

Status: NOT STARTED by this preparation task. Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP03, WP06.

## Implementation instructions

Features F18, F19. Owner: backend/finance/product. Dependencies: WP03/06; pricing policy approved before customer charging.

1. Define billable completion, partial failure, rounding, reanalysis, overage and refund/credit rules.
2. Preserve and harden usage reservations/ledger with PostgreSQL concurrency and idempotent stage completion.
3. Create versioned plans with real entitlements; stop startup defaults from silently overwriting tenant commercial configuration.
4. Add period rollover, subscription changes, quota alerts, concurrency admission and ledger reconciliation.
5. Generate approved annual/manual invoices and payment status before adding an eligible payment-service adapter.
6. Authenticate/deduplicate payment events and separate entitlement activation from untrusted callback content.
7. Reconcile customer usage reports/invoices with ledger totals; test duplicate completion, cancellation, rollover and plan change.

Done when money and entitlements can be explained from immutable ledger entries, without duplicate charges or invisible plan resets.

## Feature-level acceptance

### F18 — Usage metering, quotas and plan limits (P1)

Current audited state: W functional immature. Target: Exactly-once ledger effects and reconciliation.

- [ ] 100 concurrent reservation/completion attempts cannot exceed a hard configured quota or double charge; cancellation/partial/retry/rollover reconcile exactly with ledger and customer usage report.

### F19 — Subscription invoicing and payment adapters (P1)

Current audited state: W manual reads. Target: Annual/manual plus optional PSP billing.

- [ ] Invoice line totals match approved rate/usage/credits; replayed payment events do not duplicate activation/credit; unpaid/expired/grace transitions follow policy; startup does not overwrite negotiated entitlements.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
