# ADR 0005 — PostgreSQL row-level security under a non-bypass application role

Date: 2026-09-08 · Status: accepted · Package: WP03-T05

## Context

Tenant isolation was enforced only in application code (every query filters by
`workspace_id`; ADR 0004 adds record scope). One missed filter, one raw SQL
statement or one careless background script would read across tenants. F05
requires database-enforced isolation and proof that pooled connections never
retain another tenant's context. Earlier "RLS" claims elsewhere were rejected
because they were exercised as a superuser or the table owner, which PostgreSQL
exempts from policies.

## Decision

1. **Roles.** The migration role keeps table ownership. A `NOLOGIN`,
   `NOSUPERUSER`, `NOBYPASSRLS` role `homayar_app` is created by migration
   `c4e8b2a7d519` and granted DML. The runtime either logs in as a member of
   `homayar_app` (recommended, `DATABASE_APP_ROLE=""`) or logs in as the owner
   and switches with `SET ROLE` on every pooled checkout
   (`DATABASE_APP_ROLE=homayar_app`). Production start-up refuses superuser or
   `BYPASSRLS` runtime roles and databases without RLS enabled.
2. **FORCE.** Every protected table is `ENABLE` + `FORCE ROW LEVEL SECURITY`,
   so the owner is subject to the policies too and an app accidentally
   connected as owner fails closed (sees nothing) instead of leaking.
3. **Policies.** One permissive `FOR ALL` policy per table (`backend/database/rls.py`):
   workspace-owned tables match `workspace_id = current tenant`; `processing_jobs`
   additionally by `homayar.job_id`; `share_links` / `workspace_invitations`
   additionally by the presented token hash; `memberships` additionally by the
   validated user id; `workspaces` / `organizations` by tenant or by an
   effective membership of the validated user; `audit_events` also admits
   platform-level rows with no workspace. Authentication tables (`users`,
   `login_sessions`, `auth_identities`, one-time tokens, OTP/OAuth challenges)
   and the `plans` catalog are deliberately unprotected.
4. **Context is transaction-scoped and server-derived.** `backend/database/tenant_context.py`
   binds `homayar.user_id` after session validation, `homayar.workspace_id`
   (+organization) after membership validation, `homayar.job_id` before a worker
   claims a job, and token hashes for share/invitation flows. Values are pushed
   with `set_config(name, value, true)` and re-applied by a `Session`
   `after_begin` hook, so they vanish at COMMIT/ROLLBACK and never survive
   pool reuse. Policies read `NULLIF(current_setting(name, true), '')`, so an
   unset or reset variable matches nothing (no default tenant).
5. **Tests run as the non-bypass role.** The whole backend suite runs with
   `DATABASE_APP_ROLE=homayar_app`; schema creation and cross-tenant inspection
   use a separate DBA engine. `test_row_level_security.py` additionally proves
   the direct login-role shape, missing/stale/rolled-back context, pooled
   reuse, cross-tenant reads/inserts/updates/re-homing, job-scoped worker
   access, the FORCE behaviour for a non-superuser owner and the production
   posture check.

## Consequences

- Any new code path that touches tenant tables outside a request must bind a
  context first; otherwise it sees nothing and writes are refused. That is the
  intended failure mode.
- `reset_postgres_schema` in tests drops tables with `CASCADE` because policies
  reference other tables.
- Behind a transaction-mode pooler (PgBouncer) only the direct login-role
  shape is supported; documented in `docs/operations/deployment.md`.
- Downgrading `c4e8b2a7d519` removes policies and disables RLS but leaves the
  role and grants (operator decision). Real PgBouncer and managed-PostgreSQL
  provider behaviour remain to be verified in a deployment rehearsal.
