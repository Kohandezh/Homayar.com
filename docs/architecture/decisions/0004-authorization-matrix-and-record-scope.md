# ADR 0004 — Explicit authorization matrix, effective-dated assignments and team record scope

Date: 2026-09-08 · Status: accepted · Package: WP03-T03

## Context

Authorization was spread over a role→permission dictionary in
`backend/security/context.py`, ad hoc role checks in the admin router and
if-chains in `member_service.py`. There was no written matrix, no proof that
every mounted route declares a policy, no record-level scoping (every member
saw every meeting) and no notion of time-bounded membership. F06 requires
supervisors to see only their teams and agents only their own records; the
master prompt requires a role × resource × operation × scope matrix with
deny-by-default behaviour and preserved scope distinctions (platform,
organization, workspace, record, service).

## Decision

1. **One policy module.** `backend/security/authorization.py` owns
   `Permission`, `ROLE_PERMISSIONS`, `GRANTABLE_ROLES`, the membership and
   assignment effective-date predicates and `RecordAccess`. Routers only use
   `require_permission` / `require_write_permission`; the admin router's extra
   role checks were replaced by an explicit `workspace:manage` permission.
2. **Machine-checked matrix.** `docs/security/authorization-matrix.md` (product
   repository) carries the permission grants, the grant hierarchy and the full
   route inventory between HTML markers. `test_authorization_matrix.py` rebuilds
   all three from the running application and fails on any drift; any route
   under `/api/v1/` outside `/auth/` that resolves neither a session nor a
   permission fails the suite (deny by default).
3. **Effective dates.** `memberships.valid_from/valid_until` and
   `team_assignments.valid_from/valid_until/revoked_at`. `require_workspace`
   resolves only effective memberships on every request; expiry, deactivation
   or revocation takes effect on the next request even with a valid session.
4. **Team record scope.** `Workspace.record_scope ∈ {workspace, team}`.
   Owners/admins are never scoped. In `team` mode, members/viewers see the
   union of meetings of teams they supervise, meetings attributed to them
   (`agent_user_id`) and meetings they uploaded. The predicate is applied to
   every meeting lookup, so transcript, summary, actions, speakers, export,
   shares, job and recording cannot be reached through a parent the caller
   cannot see; the answer is `404`, never `403`.
5. **Attribution is an escalation surface.** A scoped supervisor may attribute a
   record only to a team they supervise and only to agents assigned inside
   those teams; detaching a record or moving it elsewhere is refused. Teams of
   other workspaces and inactive teams are rejected for everyone.
6. **Scopes without a carrier stay denied.** Platform operator
   (`User.system_role`) and organization administration have no routes and no
   membership carrier; the matrix records them as deny-by-default rather than
   inventing permissions.

## Consequences

- Trial and personal workspaces keep `record_scope = workspace`; nothing
  changes for the upload-to-result journey.
- Adding a route now requires a matrix row; adding a permission requires a
  grants row. This is intentional friction.
- Organization-level assignments, analyst/executive roles (F06), API-key scopes
  (WP16), share recipient policies and short-lived download authorization
  (WP03-T06) and PostgreSQL RLS (WP03-T05) are tracked gaps, listed in §8 of
  the matrix.
- Migration `9b1d4f2c7a35` is additive; downgrade discards teams and
  assignments. Forward recovery (set `record_scope` back to `workspace`) is the
  documented production path.
