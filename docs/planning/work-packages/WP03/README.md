# WP03 — Identity, tenancy and operational permissions

Status: IN PROGRESS since 2026-09-08 (T01/T02/T04/T05 validated in local scope; T03, T06 and T07 partial — the shared gap is organization-level membership/audiences and analyst/executive roles, plus API-key cases for T07; see [auth-session-contract](../../../architecture/auth-session-contract.md), [ADR 0004](../../../architecture/decisions/0004-authorization-matrix-and-record-scope.md), [ADR 0005](../../../architecture/decisions/0005-row-level-security.md), [ADR 0006](../../../architecture/decisions/0006-share-contract.md) and the machine-checked matrix at product `docs/security/authorization-matrix.md`). Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP01.

## Implementation instructions

Features F04, F05, F06. Owner: backend/security. Dependencies: WP01; use existing v1 session/context models as the starting point.

1. Define one login/refresh/reset/verification/session-family contract; remove legacy auth from public runtime composition.
2. Test rotation/reuse, logout, password-reset invalidation, disabled users, account linking, cookies and CSRF.
3. Add organization/team/agent assignments with effective dates and a written role/resource permission matrix.
4. Enforce tenant and record scope in repositories, objects, jobs, exports, shares, retrieval and later projections.
5. Add PostgreSQL RLS under a non-bypass application role; set tenant context transaction-locally and test pooled-connection reuse.
6. Add explicit recipient/organization share policies, revocation and short-lived download authorization.
7. Build adversarial two-tenant/two-team tests including background jobs, guessed IDs, stale tokens and changed membership.

Done when every route/resource has an explicit policy and negative tests prove zero unauthorized access in the release suite.

## Feature-level acceptance

### F04 — Unified login, refresh, reset, verification (P0)

Current audited state: W functional immature. Target: One revocable session system.

- [ ] Expired/revoked/disabled/reused refresh credentials and password-reset-invalidated sessions fail; legitimate rotation succeeds once; cookie CSRF and account-linking negative cases pass.

### F05 — Tenant isolation across DB, files, jobs, search (P0)

Current audited state: W partial. Target: Enforced scoped access plus RLS.

- [ ] Two-tenant negative suite covers all API/object/job/share/export/search/projection operations with zero unauthorized reads/writes; pooled PostgreSQL connections never retain another tenant's context.

### F06 — Teams, supervisor and agent record permissions (P1)

Current audited state: W missing. Target: Least-privilege operational roles.

- [ ] Supervisor sees assigned teams only, agent sees authorized self records, analyst sees approved data and executive sees allowed aggregates; all matrix-denied actions fail server-side.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
