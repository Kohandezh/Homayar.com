# ADR 0013 — API-key scope: workspace-owned, user-bound credentials

Date: 2026-09-10 · Status: accepted · Package: WP03-T07 (foundation for WP16-T02)

## Context

The product had an `ApiKey` table (workspace, creator, name, prefix, hash,
scopes, expiry, revocation, last use) and a reserved `api_key:manage`
permission, but no route created a key and no request could present one; the
matrix recorded "keys cannot bypass anything because none exist". WP03-T07
requires the adversarial two-tenant tests to cover stale and mis-scoped
credentials, and WP16 needs a credential that automation can hold without a
browser session. The master constraints: extend the existing foundation
minimally; define the owner, the verifier, the scopes, expiry/revocation, the
grant authority, the usage/audit linkage and the limits; keep user-bound keys
distinct from service credentials; refuse escalation through scopes, tenant
ids or object ids; keys must not mint stronger keys; revocation must stop new
requests with an explicit rule for queued work; never expose a stored raw key.

## Decision

1. **Owner and principal.** A key is owned by exactly one workspace
   (`api_keys.workspace_id`, a workspace table under row-level security) and
   is **bound to the user who created it**. It acts as that user, in that one
   workspace, and nowhere else. Organization-owned keys and *service
   principals* (keys without a user) are a distinct future credential
   (WP16-T02) that must not reuse this row shape's semantics; nothing here
   pretends to be one.
2. **Authority.** The effective permissions of a request are
   `scopes ∩ API_KEY_SCOPES ∩ permissions of the bound user's current
   membership role`. `API_KEY_SCOPES` is a fixed set in
   `backend/security/authorization.py` (today: `meeting:read`,
   `meeting:create`, `meeting:update`, `member:read`, `team:read`,
   `billing:read`, `audit:read`), mirrored in the matrix §6b and machine
   checked. Administration (members, teams, workspace settings, billing
   management, shares, deletion, key management) is session-only by this
   conservative interim rule; widening the set is a reviewed change of the
   constant and the document. A missing scope answers
   `403 api_key_scope_denied`; a scope the role no longer carries answers
   `403 permission_denied`. Record scope (ADR 0004 §5) applies to the bound
   user exactly as for a session.
3. **Grant authority.** `POST /api-keys` needs `api_key:manage` (owner,
   admin); requested scopes must lie within `API_KEY_SCOPES`
   (`422 invalid_scopes`) and within the creator's own permissions
   (`403 scope_grant_denied`, unreachable for owners/admins today and kept as
   defence in depth). `api_key:manage` is never a scope, so a key cannot
   create, list, widen or revoke keys — every key-management route answers
   `403 api_key_scope_denied` to a key.
4. **Tenant pinning and people-only surfaces.** The key's workspace is the
   tenant of every request it makes. An `X-Workspace-ID` header naming another
   workspace is `403 workspace_access_denied`; object ids of other tenants are
   `404` as for any member. `require_actor` (session routes such as
   `/auth/*`, organization routes, token-addressed share routes) refuses keys
   with `401 api_key_not_accepted`; only `require_workspace` — the workspace
   routes behind a permission — accepts them (`require_actor_or_api_key`).
5. **Verifier and secrecy.** The raw key is `hk_<prefix>.<secret>` (8 hex
   characters of public prefix, 32 random bytes of secret). Only the peppered
   SHA-256 digest is stored (`key_hash`, unique); the raw key is returned once
   by the creation response and never logged or audited; listings carry the
   prefix. Unknown, revoked, expired and disabled-account keys all answer
   `401 api_key_invalid`.
6. **Expiry, revocation, queued work.** Expiry is mandatory (1 …
   `API_KEY_MAX_TTL_DAYS`, default request 90 days) and evaluated per request;
   `DELETE /api-keys/{key_id}` revokes at once (idempotent). Nothing is cached:
   the row, the bound user and the membership are re-read on every request.
   Work already accepted through a key — an upload, its processing job, its
   settlement — belongs to the workspace, not to the credential: revoking the
   key neither cancels nor loses it (verified: a job uploaded through a key
   completes after the key is revoked).
7. **Limits and usage.** At most `API_KEY_MAX_ACTIVE_PER_WORKSPACE` live keys
   per workspace (`422 too_many_api_keys`); `API_KEY_RATE_LIMIT_PER_HOUR`
   requests per key through the existing rate limiter
   (`429 api_key_rate_limited`; `503` when the limiter is unavailable, like
   share links). `last_used_at` is stamped at most once a minute as evidence
   of use, not as metering; per-key usage metering belongs to WP16/WP18.
8. **Audit linkage.** `api_key.created` and `api_key.revoked` audit rows carry
   the name, prefix and scopes; actions performed through a key are audited
   under the bound user as before, and the request is marked
   (`request.state.auth_token_source = "api_key"`, `request.state.api_key_id`)
   for request logging. A per-event credential column on `audit_events` is a
   tracked gap (WP20).
9. **Database ceiling (ADR 0005 extended).** A presented key digest is bound
   as a capability (`homayar.api_key_hash`) before any tenant is known; the
   `api_keys` policy admits exactly that row (lookup, checks, last-use stamp).
   Every other table waits for the workspace bound by `require_workspace`
   after the bound user's membership was validated — the same order as a
   session. Migration `c3e9a7b5d1f4` re-applies the policy set; no schema
   change.
10. **CSRF.** Header-carried credentials (bearer tokens, API keys) are not sent
    by browsers automatically and need no CSRF proof; cookie sessions keep the
    double-submit check unchanged.

## Consequences

- WP03-T07's missing case — stale and mis-scoped API credentials — is covered
  by `backend/tests/test_api_keys.py`: issuance bounds and secrecy; scope,
  workspace and object-id pinning; people-only surfaces; expiry, revocation,
  guessed keys, demotion, membership deactivation and account disablement per
  request; accepted work surviving revocation; the document ↔ code scope set;
  and, on PostgreSQL, the one-row ceiling of the key capability.
- Three routes join the inventory (`GET/POST /api-keys`,
  `DELETE /api-keys/{key_id}`, `api_key:manage`); the matrix gained §6b.
- Not decided here: service principals and organization-owned keys, key
  rotation with overlap, per-key usage metering and quotas, IP allow-lists,
  the OpenAPI client (WP16-T01/T02).
- Verified in local scope on PostgreSQL 16.15 as `homayar_app` and on SQLite;
  not verified: remote CI, the deployed stack, the console.
