# ADR 0060: Organization audit log read

Date: 2026-11-12 · Status: Proposed (HOM-243) · Builds on: ADR 0012, ADR 0036

All code paths are under `Homayar/backend/`.

## Context

Organization routes already write audit events (member grants, changes and
revocations, scope grants, workspace creation, delegated installations), but no API
reads them back: an organization admin cannot see who changed the organization's
members or grants. The rows live in `audit_events` like every other event (ADR 0036).
That table has no `organization_id` column. The organization is only in the JSON
`details.organization_id` the writer stores. Five of the eight `organization.*` event
types are written with `workspace_id = NULL`; three are written bound to a workspace
of the organization.

PostgreSQL's tenant policy on `audit_events` admits `workspace_id = <bound workspace>`
or `workspace_id IS NULL`. An organization route binds only the organization
(`bind_organization_context`), so a single query under that context returns only the
NULL-workspace rows and silently drops the three workspace-bound types. SQLite has no
row-level security and hides nothing, so only a PostgreSQL-checked design is honest.

## Decision

### The permission

`OrganizationPermission.AUDIT_READ = "organization:audit:read"`, held by the
organization owner and admin only. It reaches them through
`_ORGANIZATION_ADMINISTRATION` (`frozenset(OrganizationPermission)`); analyst and
executive keep their explicit sets and do not get it. Like every organization route,
the reads are session only (an API key is `401 api_key_not_accepted`), and the
`authorization-matrix.md` tables list the permission and both routes.

### The routes

- `GET /api/v1/organizations/{organization_id}/audit-events` returns
  `AuditEventListResponse`, with the same query parameters, bounds and defaults as
  `GET /api/v1/admin/audit-events` (`limit` 1..100 default 50, `cursor`, `event_type`,
  `outcome`, `actor_user_id`, `target_type`, `target_id`, `occurred_after`,
  `occurred_before`), the same cursor format, the same newest-first order
  (`occurred_at`, then `id`), the same `sanitize_audit_details` on read, and the same
  errors (`400 invalid_cursor`, `400 invalid_audit_range`, 422 for out-of-bound
  values). The filter and cursor conditions are shared with the workspace query
  (`admin_service.audit_event_filter_conditions`), so the two reads cannot drift.
- `GET /api/v1/organizations/{organization_id}/audit-event-types` returns the eight
  `organization.*` members of the static catalog, sorted by `event_type`, each with
  its `group` and `target_type`.

### The row-selection rule

An event belongs to organization A only when **all** hold:

1. its `event_type` is one of the catalog's eight `organization.*` values;
2. `details.organization_id` equals A's id (compared as a JSON string, so it works on
   SQLite `JSON_EXTRACT` and PostgreSQL `->>` alike);
3. `workspace_id` is NULL, or names a workspace whose `organization_id` is A,
   whatever that workspace's status.

A non-organization event type is never returned, even when its details mention the
organization; an event of another organization is never returned, even when it sits
in a row PostgreSQL would admit.

### The per-workspace RLS binding

`organization_audit_service.list_organization_audit_events` follows the pattern of
`organization_service.scoped_records`: it queries the NULL-workspace rows under the
organization context, then binds each workspace of the organization in turn
(`bind_tenant_context(db, workspace_id=..., organization_id=...)`) and queries that
workspace's rows. The context saved before the loop is restored in a `finally`
(`restore_context`), so the request stays in the organization context and no
workspace remains bound after the read. The workspace ids are read in full before
the first workspace is bound. Each pass applies the same filters and cursor
predicate, orders by (`occurred_at`, `id`) descending and stops at `limit + 1`
rows. The first `limit + 1` rows of the merged passes, in the same order, are then
exactly the first `limit + 1` rows of the whole set. So `next_cursor` pages through
the organization's events the way the workspace route pages through one workspace:
every matching event once, `null` on the last page.

### No new index, no migration

`audit_events` keeps its shape. The organization's own events are an
administration-scale set (member and grant changes, not per-meeting traffic), already
narrowed by the `event_type IN (...)` and `details ->> 'organization_id'` predicates,
and the read walks one pass per workspace of one organization. A migration slot is
not spent (HOM-133 holds it), the append-only sink and its reader role are untouched,
and the write path (`audit_service`, the catalog) does not change.

## Consequences

- An organization owner or admin can finally read who changed the organization; the
  workspace audit surface (`/admin/audit-events`) is byte-identical and still shows
  only the bound workspace's own events.
- The organization read is correct on PostgreSQL with row-level security enforced,
  including the three workspace-bound event types, and leaves no workspace bound.
- One page costs one query for the NULL-workspace rows plus one per workspace of the
  organization, each cut to `limit + 1` rows. There is no cross-tenant UNION in SQL.
- If a future organization writes events at meeting scale, this decision should be
  revisited together with an `organization_id` column (HOM-133's migration slot).

## Not built

- No write or purge of organization audit events (ADR 0036 keeps history
  append-only).
- No organization audit for analysts or executives, and no API-key access.
- No export of the organization audit log.
