# WP14 — Review, calibration, appeals and coaching

Status: NOT STARTED by this preparation task. Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP03, WP11, WP12.

## Implementation instructions

Features F43, F44, F46, F47. Owner: product/frontend/backend. Dependencies: WP03/11/12.

1. Define ingestion/processing/evaluation/review coverage as separate counters and build stratified/random/risk-triggered sampling.
2. Create supervisor review queues with assignment, due time and record-level authorization.
3. Add criterion override with reason/evidence, preserving machine and human scores separately.
4. Add blinded calibration batches and reviewer agreement reporting against adjudicated examples.
5. Implement agent appeal, supervisor disposition and audit history with clear notifications.
6. Create specific coaching tasks from accepted findings, with owner, due date, acknowledgement and follow-up review.
7. Validate matched-task review time and agent/supervisor usability; prevent cohort/rubric mixing in comparisons.

Done when the full review-to-correction-to-coaching/appeal loop is traceable and coverage cannot be inflated by counting failed or ineligible calls.

## Feature-level acceptance

### F43 — Automated coverage and review sampling (P1)

Current audited state: Missing. Target: Measured eligible-call coverage and sampling.

- [ ] Ingested/valid/transcribed/fully-evaluated/reviewed counts reconcile independently; random/stratified samples respect configured sizes and scopes; failed calls cannot inflate evaluation coverage.

### F44 — Supervisor review and score override (P1)

Current audited state: Missing. Target: Attribution, reason and original preserved.

- [ ] Authorized reviewer can accept/override criterion with required reason; machine score preserved; unauthorized or stale-revision write rejected; audit and recalculated aggregate match approved result.

### F46 — Calibration and appeal workflow (P1)

Current audited state: Missing. Target: Independent reviewers and adjudication.

- [ ] Blind calibration computes agreement against adjudicated set; agent appeal reaches authorized reviewer and records disposition; agent cannot edit machine/human decision directly.

### F47 — Basic coaching assignments and progress (P1)

Current audited state: D teacher prototype. Target: Supervisor-approved coaching with outcome.

- [ ] Accepted finding creates assigned task with due date; agent acknowledgement and follow-up status visible to allowed roles; overdue/reminder deduplication and closure audit pass.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
