# WP15 — Issue trends and role dashboards

Status: NOT STARTED by this preparation task. Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP03, WP11, WP14.

## Implementation instructions

Features F49, F61. Owner: backend/analytics/frontend. Dependencies: WP03/11/14; external outcomes enrich later.

1. Define issue taxonomy, deduplication policy, supported filters and minimum sample thresholds.
2. Build scoped SQL aggregates for call reasons, unresolved outcomes, QA and product/service complaints.
3. Show denominators, processing coverage, date range, scorecard version and missing-data indicators.
4. Provide executive aggregates, analyst drill-down, supervisor team comparisons and agent self-view.
5. Link each trend to authorized supporting conversations and distinguish correlation from confirmed root cause.
6. Add reference-dataset reconciliation, filter/permission tests and measured query performance.

Done when every chart reconciles with source records and users see the right level of detail without cross-team/tenant leakage or unfair unadjusted rankings.

## Feature-level acceptance

### F49 — Complaint, product and operational issue trends (P1)

Current audited state: D partial. Target: Normalized rates and evidence drill-down.

- [ ] Counts/rates and drill-down match reference dataset exactly across time/team/product filters; insufficient samples and processing gaps are visible; correlation is not labeled confirmed cause.

### F61 — Role-specific dashboards and comparisons (P1)

Current audited state: D MVP; W usage only. Target: Operational trends with denominator/drilldown.

- [ ] Every role's dashboard respects scope and source denominators; proposed p95 normal API response <1 second on 100k-conversation reference dataset at documented load; cohort/rubric differences are shown.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
