# ADR 0012 — Organization membership, delegated administration and scoped analytics

Date: 2026-09-10 · Status: accepted · Package: WP03-T03 (prepares WP03-T06 and WP03-T07)

## Context

`Organization` existed only as a container: registration created one
organization with one personal workspace, and every administrative action was
bound to a *workspace* membership (ADR 0004, decision 6: "scopes without a
carrier stay denied"). The product requires organization-level administration
over workspaces and teams, analyst and executive roles that see approved data
and allowed aggregates rather than transcripts, organization-restricted share
audiences (WP03-T06) and organization service credentials (WP03-T07 / WP16).
All of that needs an organization membership carrier — without merging
tenants, widening team-scoped access, moving wallets, usage or credentials, or
handing every organization member every workspace.

## Decision

1. **One carrier: `organization_memberships`.** A row binds a user to an
   organization with a role (`owner`, `admin`, `analyst`, `executive`), an
   effective window (`valid_from` / `valid_until`), `is_active`, `revoked_at`
   and the granting user. Effectiveness is evaluated from server-side state on
   **every** request (`require_organization`); a still-valid session loses the
   organization on the next request after revocation or window expiry.
   `X-Organization-ID` is not a header: the organization is a path segment,
   and the path never confers access.
2. **Organization roles never confer workspace content access.** Meetings,
   transcripts, exports, shares, jobs and recordings stay behind an effective
   *workspace* membership and the workspace's record scope (ADR 0004). An
   organization owner/admin who is not a member of a workspace gets `403
   workspace_access_denied` there, exactly like a stranger. Missing
   organization context is never a bypass: the organization routes require a
   membership, the workspace routes still require a workspace membership.
3. **Organization permissions and hierarchy (machine-checked).**
   `backend/security/authorization.py` owns `OrganizationPermission`,
   `ORGANIZATION_ROLE_PERMISSIONS`, `ORGANIZATION_GRANTABLE_ROLES` and
   `DELEGATED_WORKSPACE_ROLES`; the matrix document carries them between
   markers and `test_authorization_matrix.py` fails on drift. Owners grant
   `admin`, `analyst`, `executive`; admins grant `analyst`, `executive`;
   nobody grants `owner` through the API; self-management and touching the
   owner are refused. Grants go to existing accounts with a verified email —
   there is no second invitation system.
4. **Delegated administration is explicit and audited.** An owner/admin may
   *install* an organization member (themselves included) in any workspace of
   the organization with a workspace role they may delegate (`admin`,
   `member`, `viewer`; never `owner`), and withdraw such a role again. The
   result is an ordinary `Membership` row: the workspace member routes, the
   record scope and row-level security govern it afterwards, and the
   workspace owner sees it in the member list. Users without an effective
   organization membership cannot be installed this way; they are invited
   through the workspace invitation flow as before.
5. **Workspaces created inside an organization carry no trial.** The creator
   becomes the workspace owner; no demo subscription or entitlement is
   created (billing is organization-level with explicit workspace allocations,
   blueprint §12), so uploads are refused there until an allocation exists.
   Nothing is transferred between workspaces.
6. **Analysts and executives see granted scopes only.** A *scope grant*
   authorizes one analyst/executive membership for one workspace, optionally
   narrowed to one active team of that workspace. Executives read
   **aggregates** (meeting counts by status, audio seconds, action-item
   counts per workspace/team); analysts additionally read meeting
   **metadata** (id, title, status, language, duration, team, date) — never
   transcript text, summaries, recordings or exports. Grants of admins are
   refused, teams of other workspaces are refused, revoking the membership
   revokes its grants, and revoking a grant bites on the next request.
   Owners/admins see the aggregates and metadata of every workspace of the
   organization. Aggregates bind each authorized workspace as the tenant in
   turn, so they run under that workspace's row-level security; unauthorized
   workspaces are never bound.
7. **Row-level security (ADR 0005 extended).** `organization_memberships`
   are visible under the bound organization or to their own user;
   `organization_scope_grants` under the bound organization only. `workspaces`
   are additionally visible under a bound organization to effective
   owners/admins of that organization and to holders of an active scope grant
   on the workspace — and only when the workspace belongs to that
   organization, so a stray grant pointing elsewhere exposes nothing (the
   application validates the workspace with an organization-filtered lookup;
   a database-side subquery on `workspaces` inside the grant policy would
   recurse with the `workspaces` policy and is deliberately absent).
   `organizations` are additionally visible to their members (listing one's
   organizations). Earlier migrations that re-apply the policies on a schema
   without the carrier use the pre-organization expressions
   (`PRE_ORGANIZATION_POLICIES`). The organization context is bound only
   after the membership was validated, with the same transaction-scoped
   `set_config` as the tenant context, and vanishes at COMMIT/ROLLBACK.
8. **Bootstrap and backfill preserve boundaries.** Registration now creates
   the organization owner membership for the creator (same transaction as
   before). Migration `a2f7c4e9d1b3` backfills an owner membership for every
   existing organization that has **exactly one** distinct active workspace
   owner across its workspaces (today: every organization, since each holds
   one personal workspace). Organizations with none or several distinct owners
   are left without organization members and counted in the migration log —
   that is the recorded decision an operator must make by hand; nothing is
   inferred from email domains and no default organization is created.
9. **Platform administration stays separate.** `User.system_role` remains
   deny-by-default (ADR 0004); no organization role grants platform actions
   and no platform role is implied by an organization role.
10. **Not decided here.** Organization-level billing allocations and wallets
    (WP05/WP09), organization share audiences (WP03-T06, which now has its
    carrier), organization service credentials (WP03-T07), organization
    settings/rename routes and SCIM-style provisioning are tracked gaps.

## Consequences

- Every organization route declares an organization permission and appears in
  the route inventory; the matrix gained sections §1a (organization scope),
  §3a (organization permissions), §4a (organization grant hierarchy and
  delegation).
- Existing tests keep passing unchanged: personal workspaces, the trial grant,
  record scope and the share contract are untouched; the organization layer
  only adds rows and routes.
- Downgrading `a2f7c4e9d1b3` restores the previous `workspaces` /
  `organizations` policies before dropping the two tables (the new policies
  reference them).
- Verified in local scope on PostgreSQL 16.15 as `homayar_app` and on the
  SQLite fallback (`backend/tests/test_organizations.py`,
  `test_authorization_matrix.py`; migration upgrade/check/downgrade/upgrade).
  Not verified: managed-PostgreSQL role provisioning and PgBouncer (as before),
  the console (the API is the contract).
