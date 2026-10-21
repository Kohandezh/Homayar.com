# Authorization matrix (role × resource × operation × scope)

Status: implemented and test-pinned (WP03-T03). Source of truth in code:
`backend/security/authorization.py` (permissions, grants, record scope) and
`backend/security/context.py` (session → workspace resolution).
`backend/tests/test_authorization_matrix.py` fails when the tables marked
*machine-checked* below drift from the code, or when a route is mounted without
an explicit policy. Edit the code and this file together.

## 1. Scopes

| Scope | Carrier | Effective today |
| --- | --- | --- |
| Platform administration | `User.system_role = operator` | **Deny by default.** It grants nothing alone. The voice node routes (`POST /api/v1/voice/nodes`, `/nodes/{node_id}/heartbeat`, `/nodes/{node_id}/drain`, `POST /api/v1/voice/recover`) require it together with `workspace:manage`, from a signed-in session and never an API key (`+operator`, §6). `GET /api/v1/voice/capacity` adds deployment data for such a caller. Every other route ignores it. |
| Organization administration | `OrganizationMembership` (active, unrevoked, `valid_from ≤ now < valid_until`) | Role → fixed set of *organization* permissions (§3a): membership and workspace administration, delegated installation of workspace roles, scoped aggregates/metadata. Resolved on **every** request from the path organization and server-side state; **never** confers workspace content access (§1a). |
| Workspace membership | `Membership` (active, `valid_from ≤ now < valid_until`) | Role → fixed permission set (§3). Resolved on **every request** from the database; a still-valid session loses access on the next request after removal, deactivation or window expiry. |
| Record ownership / team scope | `Workspace.record_scope`, `Meeting.team_id`, `Meeting.agent_user_id`, `Meeting.created_by_user_id`, effective `TeamAssignment` rows | Applies to **member** and **viewer** when `record_scope = team` (§5). Owner/admin are never record-scoped. |
| Service / background worker | `ProcessingJob.workspace_id` | Workers act on a persisted job id, never on a user context; every write is keyed by the job's workspace (`backend/jobs/processor.py`, pinned by `test_tenant_scope.py`). Under row-level security the worker binds `homayar.job_id` to read exactly one job, then that job's workspace; it cannot write before the tenant is bound (`test_row_level_security.py`). |

`X-Workspace-ID` selects among workspaces the caller already belongs to; it never confers access. Tenant identifiers are never accepted in request bodies (`extra="forbid"` on every write schema; pinned by tests).

The session payload (`/auth/login`, `/auth/refresh`, `/auth/me`, `/auth/otp/verify`) exposes `user.system_role` and, for each listed workspace, `permissions`: the sorted values of `ROLE_PERMISSIONS` for that membership role (§3). Both are read from the database on every call. They only drive what the console shows; every route still enforces its own policy.

### 1a. Organization scope (WP03-T03, ADR 0012)

- The organization is a path segment (`/api/v1/organizations/{organization_id}/...`); it never confers access. A caller without an effective membership of that organization gets `404 organization_not_found`, whether the organization exists or not.
- Organization roles administer; they do not read content. An organization owner/admin is a stranger to every workspace they were not explicitly installed in (`403 workspace_access_denied` on the workspace routes).
- **Delegated administration** installs an organization member (the caller included) in a workspace of the organization with a workspace role from §4a (never `owner`). The result is an ordinary `Membership` row: the workspace routes, §5 record scope and row-level security govern it afterwards; the workspace owner sees it in the member list; the organization route can withdraw it again. Users without an effective organization membership cannot be installed this way.
- **Scope grants** authorize an `analyst` or `executive` for one workspace, optionally narrowed to one active team. Executives read aggregates only (`organization:analytics:read`); analysts additionally read meeting metadata (`organization:records:read`) — never transcript text, summaries, recordings or exports. Owners/admins see aggregates and metadata of every workspace of the organization. Grants to owners/admins, foreign workspaces and foreign teams are refused; revoking the membership revokes its grants.
- Workspaces created inside an organization (`organization:workspace:create`) carry no trial subscription or entitlement; the creator becomes the workspace owner.
- Platform administration stays separate (`User.system_role`). No organization role confers it. Only the `+operator` voice routes and the deployment data of `GET /api/v1/voice/capacity` use it (§1); everything else denies by default.

## 2. Roles

| Role | Kind | Notes |
| --- | --- | --- |
| `owner` | workspace | Created with the personal workspace; not grantable through the API; cannot be changed or removed through the API. |
| `admin` | workspace | Grantable by owner only. Cannot grant/manage `admin` or `owner`. |
| `member` | workspace | Day-to-day user: upload, edit, share. |
| `viewer` | workspace | Read-only. |
| `supervisor` | team (effective-dated) | Record scope only: sees the meetings of the teams they supervise (plus own). Grants **no** workspace permission. |
| `agent` | team (effective-dated) | Record scope only: sees the meetings attributed to them (`agent_user_id`) or uploaded by them. Grants **no** workspace permission. |
| `owner` (organization) | organization (effective-dated) | Created with the organization (bootstrap/backfill); not grantable through the API. Administers memberships and workspaces, delegates workspace roles, sees aggregates/metadata of every workspace. |
| `admin` (organization) | organization (effective-dated) | Grantable by the organization owner. Same permissions as the owner; cannot grant/manage `admin` or `owner`. |
| `analyst` (organization) | organization (effective-dated) | Reads aggregates **and** meeting metadata of granted scopes only. No content access. |
| `executive` (organization) | organization (effective-dated) | Reads aggregates of granted scopes only. No content access. |

## 3. Permission grants by workspace role (machine-checked)

<!-- matrix:permissions:start -->
| Permission | owner | admin | member | viewer |
| --- | --- | --- | --- | --- |
| meeting:read | ✓ | ✓ | ✓ | ✓ |
| meeting:create | ✓ | ✓ | ✓ | – |
| meeting:update | ✓ | ✓ | ✓ | – |
| meeting:delete | ✓ | ✓ | – | – |
| share:manage | ✓ | ✓ | ✓ | – |
| member:read | ✓ | ✓ | ✓ | ✓ |
| member:manage | ✓ | ✓ | – | – |
| team:read | ✓ | ✓ | ✓ | ✓ |
| team:manage | ✓ | ✓ | – | – |
| workspace:manage | ✓ | ✓ | – | – |
| billing:read | ✓ | ✓ | – | – |
| billing:manage | ✓ | – | – | – |
| audit:read | ✓ | ✓ | – | – |
| api_key:manage | ✓ | ✓ | – | – |
| legal_hold:manage | ✓ | – | – | – |
| pii:original_access | ✓ | – | – | – |
| ai_policy:manage | ✓ | – | – | – |
<!-- matrix:permissions:end -->

`api_key:manage` (owner, admin) creates, lists and revokes workspace API keys (§6b). It is never grantable to a key, so a key cannot mint or widen keys. `legal_hold:manage` (owner only) places and releases legal holds and reads the legal-hold register (`GET /admin/legal-holds`, §6e); it is never an API-key scope. `pii:original_access` (owner only) reads original transcripts under an enforced redaction policy, reveals a redacted value and changes the redaction policy (§6k); it is never an API-key scope. `ai_policy:manage` (owner only) changes where the workspace's audio and text may be processed (§6m); it is never an API-key scope.

### 3a. Permission grants by organization role (machine-checked)

<!-- matrix:organization-permissions:start -->
| Permission | owner | admin | analyst | executive |
| --- | --- | --- | --- | --- |
| organization:read | ✓ | ✓ | ✓ | ✓ |
| organization:member:read | ✓ | ✓ | – | – |
| organization:member:manage | ✓ | ✓ | – | – |
| organization:workspace:read | ✓ | ✓ | ✓ | ✓ |
| organization:workspace:create | ✓ | ✓ | – | – |
| organization:workspace:administer | ✓ | ✓ | – | – |
| organization:analytics:read | ✓ | ✓ | ✓ | ✓ |
| organization:records:read | ✓ | ✓ | ✓ | – |
<!-- matrix:organization-permissions:end -->

`organization:workspace:read` lists every workspace for owners/admins and only the granted ones for analysts/executives (§1a). `organization:member:manage` also owns scope grants.

## 4. Role grant hierarchy (machine-checked)

Who may **grant** (invite, promote, demote) and **manage** (change role, remove) which workspace role. Self-management is always refused; nobody grants or manages `owner`.

<!-- matrix:grants:start -->
| Actor role | May grant / manage |
| --- | --- |
| owner | admin, member, viewer |
| admin | member, viewer |
| member | – |
| viewer | – |
<!-- matrix:grants:end -->

Role change requires both: the actor may grant the **new** role *and* may manage the target's **current** role (an admin can neither promote a member to admin nor demote another admin).

### 4a. Organization grant hierarchy and delegation (machine-checked)

Who may **grant** (to an existing verified account) and **manage** (change role/window, revoke) which organization role. Self-management is always refused; nobody grants or manages `owner`.

<!-- matrix:organization-grants:start -->
| Actor role | May grant / manage |
| --- | --- |
| owner | admin, analyst, executive |
| admin | analyst, executive |
| analyst | – |
| executive | – |
<!-- matrix:organization-grants:end -->

Which **workspace** roles an organization role may install in (or withdraw from) a workspace of the organization (delegated administration, §1a):

<!-- matrix:organization-delegation:start -->
| Organization role | May install / withdraw workspace role |
| --- | --- |
| owner | admin, member, viewer |
| admin | admin, member, viewer |
| analyst | – |
| executive | – |
<!-- matrix:organization-delegation:end -->

## 5. Record scope (`Workspace.record_scope`)

| Mode | owner / admin | member / viewer |
| --- | --- | --- |
| `workspace` (default; trial and personal workspaces) | all records | all records |
| `team` | all records | union of: meetings of teams where the caller holds an **effective** `supervisor` assignment; meetings with `agent_user_id = caller`; meetings with `created_by_user_id = caller`. No assignment → own records only. |

An assignment is effective when `revoked_at IS NULL AND valid_from ≤ now < valid_until (or NULL)` and its team is active. The predicate is applied to **every** meeting lookup (list, detail, job, recording, transcript, summary, actions, speakers, search, export, share create/list/revoke); an invisible record answers `404`, never `403`, so existence is not disclosed.

Attribution writes (`PATCH /meetings/{id}` with `team_id` / `agent_user_id`, permission `meeting:update`):

| Actor | `team_id` | `agent_user_id` |
| --- | --- | --- |
| unrestricted (owner/admin, or flat workspace) | any active team of this workspace, or `null` | any effective workspace member, or `null` |
| record-scoped supervisor | only a team they supervise; `null` refused (`403 team_scope_denied`) | only a user with an effective assignment in a team they supervise (`403 agent_scope_denied`) |
| record-scoped agent / unassigned member | refused (`403`) | refused (`403`) |

Teams of another workspace or inactive teams are `422 invalid_team`; non-members are `422 invalid_agent`.

## 6. Route inventory (machine-checked)

Policy column: `public`, `optional-session`, `session`, `session+csrf`, or a permission from §3, with `+csrf` when the route is a cookie-authenticated write. Workspace routes additionally require an effective membership (§1) and, where the resource is a meeting, the record predicate (§5).

`+operator` additionally requires `User.system_role = "operator"` from a signed-in session (never an API key): deployment-wide voice media nodes (P0 wave).

<!-- matrix:routes:start -->
| Method | Path | Policy |
| --- | --- | --- |
| GET | /api/health | public |
| GET | /healthz | public |
| GET | /readyz | public |
| GET | /metrics | public |
| POST | /api/v1/auth/register | public |
| POST | /api/v1/auth/verify-email | public |
| POST | /api/v1/auth/verify-email/resend | session+csrf |
| GET | /api/v1/auth/methods | public |
| POST | /api/v1/auth/login | public |
| POST | /api/v1/auth/refresh | public |
| POST | /api/v1/auth/logout | public |
| POST | /api/v1/auth/forgot-password | public |
| POST | /api/v1/auth/reset-password | public |
| POST | /api/v1/auth/otp/request | public |
| POST | /api/v1/auth/otp/verify | public |
| GET | /api/v1/auth/google/start | public |
| GET | /api/v1/auth/google/callback | public |
| GET | /api/v1/auth/google/link/start | session+csrf |
| GET | /api/v1/auth/me | session |
| POST | /api/v1/members/invitations/accept | session+csrf |
| GET | /api/v1/members | member:read |
| GET | /api/v1/members/invitations | member:manage |
| POST | /api/v1/members/invitations | member:manage +csrf |
| DELETE | /api/v1/members/invitations/{invitation_id} | member:manage +csrf |
| PATCH | /api/v1/members/{membership_id} | member:manage +csrf |
| DELETE | /api/v1/members/{membership_id} | member:manage +csrf |
| GET | /api/v1/teams | team:read |
| GET | /api/v1/teams/{team_id} | team:read |
| POST | /api/v1/teams | team:manage +csrf |
| PATCH | /api/v1/teams/{team_id} | team:manage +csrf |
| POST | /api/v1/teams/{team_id}/assignments | team:manage +csrf |
| DELETE | /api/v1/teams/{team_id}/assignments/{assignment_id} | team:manage +csrf |
| GET | /api/v1/admin/audit-events | audit:read |
| GET | /api/v1/admin/audit-event-types | audit:read |
| GET | /api/v1/admin/shares | workspace:manage |
| GET | /api/v1/admin/workspace-settings | workspace:manage |
| PATCH | /api/v1/admin/workspace-settings | workspace:manage +csrf |
| GET | /api/v1/admin/jobs | workspace:manage |
| GET | /api/v1/admin/jobs/summary | workspace:manage |
| GET | /api/v1/admin/coverage | workspace:manage |
| POST | /api/v1/admin/review-samples | workspace:manage +csrf |
| POST | /api/v1/admin/jobs/{job_id}/replay | workspace:manage +csrf |
| GET | /api/v1/admin/ai-policy | workspace:manage |
| PUT | /api/v1/admin/ai-policy | ai_policy:manage +csrf |
| GET | /api/v1/admin/ai-policy/versions | workspace:manage |
| GET | /api/v1/admin/jobs/{job_id}/policy-snapshots | workspace:manage |
| GET | /api/v1/admin/legal-holds | legal_hold:manage |
| GET | /api/v1/billing/plans | billing:read |
| GET | /api/v1/billing/subscription | billing:read |
| GET | /api/v1/billing/usage | billing:read |
| GET | /api/v1/billing/usage/events | billing:read |
| GET | /api/v1/billing/management | billing:manage |
| POST | /api/v1/uploads | meeting:create +csrf |
| POST | /api/v1/uploads/ | meeting:create +csrf |
| GET | /api/v1/meetings | meeting:read |
| GET | /api/v1/meetings/counts | meeting:read |
| GET | /api/v1/meetings/{meeting_id} | meeting:read |
| GET | /api/v1/meetings/{meeting_id}/job | meeting:read |
| GET | /api/v1/meetings/{meeting_id}/recording | meeting:read |
| GET | /api/v1/workspaces/{workspace_id}/meetings/{meeting_id}/recording | meeting:read |
| GET | /api/v1/meetings/{meeting_id}/playback | meeting:read |
| GET | /api/v1/meetings/{meeting_id}/transcript | meeting:read |
| GET | /api/v1/meetings/{meeting_id}/transcript/revisions | meeting:read |
| POST | /api/v1/meetings/{meeting_id}/transcript/corrections | meeting:update +csrf |
| POST | /api/v1/meetings/{meeting_id}/analysis/recompute | meeting:update +csrf |
| GET | /api/v1/meetings/{meeting_id}/evidence | meeting:read |
| GET | /api/v1/meetings/{meeting_id}/evidence/{evidence_id} | meeting:read |
| GET | /api/v1/meetings/{meeting_id}/quality | meeting:read |
| GET | /api/v1/ingestions | meeting:read |
| GET | /api/v1/ingestions/{ingestion_id} | meeting:read |
| GET | /api/v1/meetings/{meeting_id}/media | meeting:read |
| POST | /api/v1/meetings/{meeting_id}/media/{media_id}/verify | meeting:update +csrf |
| POST | /api/v1/meetings/{meeting_id}/legal-hold | legal_hold:manage +csrf |
| POST | /api/v1/media/{media_id}/legal-hold | legal_hold:manage +csrf |
| POST | /api/v1/media/{media_id}/legal-hold/release | legal_hold:manage +csrf |
| GET | /api/v1/meetings/{meeting_id}/summary | meeting:read |
| GET | /api/v1/meetings/{meeting_id}/report | meeting:read |
| GET | /api/v1/meetings/{meeting_id}/actions | meeting:read |
| GET | /api/v1/meetings/{meeting_id}/export | meeting:read |
| POST | /api/v1/meetings/{meeting_id}/transcript/redactions/{redaction_id}/reveal | pii:original_access +csrf |
| POST | /api/v1/privacy/subject-exports | pii:original_access +csrf |
| GET | /api/v1/search | meeting:read |
| PATCH | /api/v1/meetings/{meeting_id} | meeting:update +csrf |
| POST | /api/v1/meetings/{meeting_id}/actions | meeting:update +csrf |
| PATCH | /api/v1/meetings/{meeting_id}/actions/{action_id} | meeting:update +csrf |
| DELETE | /api/v1/meetings/{meeting_id}/actions/{action_id} | meeting:update +csrf |
| PATCH | /api/v1/meetings/{meeting_id}/speakers/{speaker_id} | meeting:update +csrf |
| DELETE | /api/v1/meetings/{meeting_id} | meeting:delete +csrf |
| GET | /api/v1/meetings/{meeting_id}/shares | share:manage |
| POST | /api/v1/meetings/{meeting_id}/shares | share:manage +csrf |
| DELETE | /api/v1/meetings/{meeting_id}/shares/{share_id} | share:manage +csrf |
| PATCH | /api/v1/meetings/{meeting_id}/shares/{share_id}/recipients | share:manage +csrf |
| GET | /api/v1/shares/{share_token} | optional-session |
| GET | /api/v1/shares/{share_token}/export | optional-session |
| GET | /api/v1/shares/{share_token}/recording | optional-session |
| GET | /api/v1/api-keys | api_key:manage |
| POST | /api/v1/api-keys | api_key:manage +csrf |
| DELETE | /api/v1/api-keys/{key_id} | api_key:manage +csrf |
| GET | /api/v1/organizations | session |
| GET | /api/v1/organizations/{organization_id} | organization:read |
| GET | /api/v1/organizations/{organization_id}/members | organization:member:read |
| POST | /api/v1/organizations/{organization_id}/members | organization:member:manage +csrf |
| PATCH | /api/v1/organizations/{organization_id}/members/{membership_id} | organization:member:manage +csrf |
| DELETE | /api/v1/organizations/{organization_id}/members/{membership_id} | organization:member:manage +csrf |
| GET | /api/v1/organizations/{organization_id}/workspaces | organization:workspace:read |
| POST | /api/v1/organizations/{organization_id}/workspaces | organization:workspace:create +csrf |
| POST | /api/v1/organizations/{organization_id}/workspaces/{workspace_id}/members | organization:workspace:administer +csrf |
| DELETE | /api/v1/organizations/{organization_id}/workspaces/{workspace_id}/members/{membership_id} | organization:workspace:administer +csrf |
| GET | /api/v1/organizations/{organization_id}/scope-grants | organization:member:read |
| POST | /api/v1/organizations/{organization_id}/scope-grants | organization:member:manage +csrf |
| DELETE | /api/v1/organizations/{organization_id}/scope-grants/{grant_id} | organization:member:manage +csrf |
| GET | /api/v1/organizations/{organization_id}/aggregates | organization:analytics:read |
| GET | /api/v1/organizations/{organization_id}/records | organization:records:read |
| GET | /api/v1/locations | meeting:read |
| POST | /api/v1/locations | workspace:manage +csrf |
| POST | /api/v1/locations/resolve | meeting:read |
| POST | /api/v1/locations/nearest | meeting:read |
| GET | /api/v1/locations/{location_id} | meeting:read |
| PATCH | /api/v1/locations/{location_id} | workspace:manage +csrf |
| DELETE | /api/v1/locations/{location_id} | workspace:manage +csrf |
| POST | /api/v1/voice/calls | meeting:create +csrf |
| GET | /api/v1/voice/calls/{session_id} | meeting:read |
| POST | /api/v1/voice/calls/{session_id}/transitions | meeting:update +csrf |
| GET | /api/v1/voice/capacity | meeting:read |
| POST | /api/v1/voice/nodes | workspace:manage +csrf +operator |
| POST | /api/v1/voice/nodes/{node_id}/heartbeat | workspace:manage +csrf +operator |
| POST | /api/v1/voice/nodes/{node_id}/drain | workspace:manage +csrf +operator |
| POST | /api/v1/voice/recover | workspace:manage +csrf +operator |
| POST | /api/v1/voicemails | meeting:create +csrf |
| GET | /api/v1/voicemails | meeting:read |
| GET | /api/v1/voicemails/analytics | meeting:read |
| GET | /api/v1/voicemails/{voicemail_id} | meeting:read |
| PATCH | /api/v1/voicemails/{voicemail_id} | meeting:update +csrf |
<!-- matrix:routes:end -->

`GET /metrics` (HOM-113) has no session dependency, so its row reads `public`; its real rule is enforced inside the handler (`backend.observability.metrics.metrics_access`). When `METRICS_BEARER_TOKEN` is set, a matching bearer token is required (constant-time comparison). In staging and production without a token it answers 404, unless `METRICS_ALLOW_UNAUTHENTICATED=true` opts out; that opt-out is on-premise only and is refused in cloud. `METRICS_ENABLED=false` answers 404. It is the only path exempt from the `TRUSTED_HOSTS` check (Prometheus scrapes `api:8000`). The nginx proxy never routes it, and a test asserts this.

Notes on the public rows: `logout` and `refresh` authenticate through the refresh cookie/token they consume; the three `shares/{share_token}` routes are token-addressed and enforce the share contract in §6a on every request; `auth/google/callback` binds to the state cookie issued by `google/start`.

## 6a. Share contract (WP03-T06)

Source: `backend/services/share_service.py`, `backend/services/download_service.py`; tests `test_share_policies.py`, `test_share_organization.py`, `test_row_level_security.py::test_share_context_exposes_only_the_shared_meeting`.

| Aspect | Rule |
| --- | --- |
| Who creates / lists / revokes | `share:manage` (owner, admin, member) **and** the meeting must be visible to the caller under record scope (§5). `meeting:read` alone never grants sharing. |
| Resource | exactly one meeting's processed result (transcript, summary, actions). Never jobs, search, other meetings, workspace data. |
| Audience `link` | anyone holding the link. Only while `Workspace.public_share_links_enabled` is true (default false; `workspace:manage` toggles it, and turning it off disables existing links immediately). Never with audio download. |
| Audience `workspace` | signed-in caller with an **effective** membership in the source workspace; under team record scope the meeting must also be visible to that caller. Legacy `require_authentication=true` shares were migrated to this audience (stricter than the former "any signed-in account"). |
| Audience `recipients` | signed-in caller who owns a listed address: a normalised email or an Iranian mobile number (E.164, `normalize_iranian_mobile`). The first match requires the address to be **verified** on the account (email verification, or the OTP contract for phones) and binds the recipient row to the stable user id; afterwards only that user id matches, so a changed or reassigned email/number transfers nothing and revokes nothing. Unverified addresses, arbitrary matching phone strings and pending invitations grant nothing. |
| Recipient editing | `PATCH …/shares/{share_id}/recipients` (`share:manage` +csrf, record visible to the caller): adds emails/phones and removes recipient rows by id on a `recipients` share only (`422 recipients_not_editable` otherwise, also once revoked). Deltas are applied under a row lock on the share, so concurrent editors lose nothing; the total stays ≤ 50 (`422 too_many_recipients`); an unknown id is `404 recipient_not_found`; the list never becomes empty. Audited as `meeting.share_recipients_changed` with counts and row ids, never addresses. A removed recipient is refused on the next request, including `Range` continuations. |
| Audience `organization` | signed-in caller who is a **current** member of the organization the source workspace belongs to: an effective membership in any active workspace of that organization, or an effective organization membership (§1a) — re-evaluated on every access from server-side state, so leaving, expiry, revocation and a workspace moved to another organization bite on the next request. The source workspace's team record scope is not applied (an explicit grant of one meeting; the issuer's own authority is re-checked). Takes no recipients. Only while `Workspace.organization_share_links_enabled` is true (default true; `workspace:manage` toggles it; off hides existing organization shares as `404` and refuses creation with `403 organization_share_links_disabled`). Nothing else of the workspace or tenant becomes reachable; no tenant context is bound for this audience. |
| Operations | `view` implied; `export` requires `allow_export`; `download` (original audio) requires `allow_download`. Each is checked per request. |
| Expiry / revocation | `expires_at` and `revoked_at` are evaluated on every request, including each `Range` request of a streamed download. |
| Issuer authority | re-checked per access: the issuer must still hold an effective membership with `share:manage` and, if record-scoped, still see the meeting; otherwise the share is suspended (404 for everyone). |
| Deleted or inactive | deleted meeting, inactive workspace, expired/revoked share, disabled public links, suspended issuer all answer `404 share_not_found` so tokens cannot probe state; authenticated-but-not-entitled callers get `401/403` with a specific code. |
| Downloads | local storage: application-mediated streaming with `Range`/`206`, bounded 1 MiB chunks, `X-Homayar-Revocation: immediate`. S3-compatible storage: 307 to a presigned URL for exactly that object, TTL ≤ 300 s, `X-Homayar-Revocation: expiry-bounded` (an already-issued URL survives revocation until it expires). |
| Database ceiling | a request carrying only the share token sees, through RLS, the one shared meeting's content, the issuer's membership and (as any signed-in user) the caller's own memberships and organization memberships; it can write only the share's access counters, a recipient binding and an audit row. Tenant context is bound only for the `workspace` audience after membership validation. |
| Tokens | 32 random bytes (URL-safe); only the peppered SHA-256 is stored; raw tokens appear in the creation response only. Rate limited per token and client address. |
| Administrator list | `GET /api/v1/admin/shares` (`workspace:manage`, read-only, never through an API key) lists every share link of the workspace with its status (`active`, `revoked`, `expired`), leaving out shares of soft-deleted meetings. It never returns the token, the token hash, a share URL or recipient addresses. Test: `test_admin_shares_api.py`. |

## 6b. API-key scope (WP03-T07)

Source: `backend/security/authorization.py` (`API_KEY_SCOPES`), `backend/security/context.py` (`require_actor_or_api_key`, `require_workspace`, `require_permission`), `backend/services/api_key_service.py`; tests `test_api_keys.py`.

| Aspect | Rule |
| --- | --- |
| Owner | exactly one workspace (`api_keys.workspace_id`, a workspace table under RLS). Organization-owned keys and service principals without a user are not defined (WP16-T02). |
| Principal | the user who created the key. Each request re-resolves that user's **current** membership in the key's workspace (`require_workspace`), so a revoked/expired membership, a disabled account or a non-active workspace stops the key on the next request. |
| Authority | `scopes ∩ API_KEY_SCOPES ∩ permissions of the current membership role`. A missing scope answers `403 api_key_scope_denied`; a scope the role no longer carries answers `403 permission_denied`. Record scope (§5) applies to the bound user as for a session. |
| Grant authority | creation needs `api_key:manage` (owner, admin) and the requested scopes must lie within the creator's own permissions (`403 scope_grant_denied`) and within `API_KEY_SCOPES` (`422 invalid_scopes`). `api_key:manage` is never a scope: keys cannot create, list or revoke keys. |
| Tenant pinning | the key's workspace is the tenant; `X-Workspace-ID` naming another workspace is `403 workspace_access_denied`; foreign object ids are `404` as for any member. Keys are refused on session routes (`/auth/*`), organization routes and token-addressed share routes (`401 api_key_not_accepted`). |
| Verifier | raw key `hk_<prefix>.<secret>`; only the peppered SHA-256 digest (`key_hash`) is stored; the raw key is returned once by `POST /api-keys`; listings show `key_prefix`. |
| Expiry / revocation | expiry is mandatory (1 … `API_KEY_MAX_TTL_DAYS`, default 90 days); `DELETE /api-keys/{key_id}` revokes at once (per-request check, nothing cached). Work already accepted through the key — uploads, processing jobs — belongs to the workspace and is neither cancelled nor lost by revocation. |
| Limits | at most `API_KEY_MAX_ACTIVE_PER_WORKSPACE` live keys per workspace (`422 too_many_api_keys`); `API_KEY_RATE_LIMIT_PER_HOUR` requests per key (`429 api_key_rate_limited`). |
| Usage / audit | `last_used_at` is stamped at most once a minute; `api_key.created` / `api_key.revoked` audit rows carry name, prefix and scopes (never the key or digest); actions taken through a key are audited under the bound user, with the request marked `api_key` (`request.state`). |
| Database ceiling | a request carrying only a key digest sees, through RLS, exactly that `api_keys` row (and may stamp it); every other table waits for the tenant bound after the membership check. |
| CSRF | header-carried credentials need no CSRF proof (not sent by browsers automatically); cookie sessions keep the double-submit check. |

Scopes an API key may carry (machine-checked against `API_KEY_SCOPES`):

<!-- matrix:api-key-scopes:start -->
| Scope | Grantable to an API key |
| --- | --- |
| meeting:read | ✓ |
| meeting:create | ✓ |
| meeting:update | ✓ |
| meeting:delete | – |
| share:manage | – |
| member:read | ✓ |
| member:manage | – |
| team:read | ✓ |
| team:manage | – |
| workspace:manage | – |
| billing:read | ✓ |
| billing:manage | – |
| audit:read | ✓ |
| api_key:manage | – |
| legal_hold:manage | – |
| pii:original_access | – |
| ai_policy:manage | – |
<!-- matrix:api-key-scopes:end -->

## 6c. Transcript revisions, analysis provenance, evidence and quality (WP04-T02/T03/T06, ADR 0014/0015)

| Rule | Enforcement | Test |
|---|---|---|
| A transcript revision and its segments are immutable once committed; a correction is a *new* revision whose parent is the revision it was made from | ORM `before_update` guards in `backend/database/saas_models.py` (`TranscriptImmutableError`), `correct_revision` in `backend/services/transcript_service.py` | `test_transcript_revisions.py` |
| Exactly one revision per meeting is current; reads without an explicit `revision_id` serve it; search, action-item source segments and exports use only current segments | partial unique index `uq_transcript_revision_current`, `current_segments_clause` | same |
| Corrections require `meeting:update` (+CSRF), the base must be the current revision (`409 revision_stale`), every edit must name a segment of that base and a known speaker (`422 invalid_segment` / `invalid_speaker`), and at least one edit must change something (`422 no_changes`) | route `POST /meetings/{meeting_id}/transcript/corrections`, `correct_revision` under the row lock of the current revision | same |
| A correction dispatches a recompute of the analysis on the corrected revision (summary, AI action items, evidence, text view; never ASR, a revision, a job, a checkpoint or usage); a person asks again with `meeting:update` (+CSRF), `404` for another workspace's meeting, `200 up_to_date` and no rows when nothing is stale, `429 recompute_rate_limited` over `RECOMPUTE_MANUAL_RATE_LIMIT_PER_MINUTE` requests per workspace, and may ask after the attempt cap (ADR 0037, HOM-63) | `POST /meetings/{meeting_id}/analysis/recompute`, `backend/jobs/recompute.py` under the meeting and current-revision row locks | `test_analysis_recompute.py` |
| Reprocessing appends a revision and never discards a person's speaker names; earlier revisions stay readable by id | `_persist_success` + `ensure_speakers` in `backend/jobs/processor.py` | same |
| Every summary / AI action item references the analysis run (revision analysed, provider, model, prompt/schema digests, checkpoint, truthful stage state) that produced it | `AnalysisRun`, `record_analysis_run`, `analysis_run_id` on `meeting_summaries` / `action_items` | same |
| Revisions and analysis runs are tenant rows readable through a share exactly like segments; a foreign tenant sees `404` | RLS shared-content policy (`transcript_revisions`, `analysis_runs`), `_meeting_or_404` | `test_transcript_revisions.py`, `test_row_level_security.py` |
| Audit of a correction carries ids and counts only, never transcript text | `transcript.revision.created` details | `test_transcript_revisions.py` |
| Every AI finding (summary overview, key point, action item) cites only segments of the revision the analysis read; references that name no such turn are rejected and counted on the run, never repaired; each finding carries `evidence_status` (`sufficient` / `partial` / `insufficient`) and the evidence rows resolve to revision, segment, speaker, character range and original audio offsets (WP04-T03) | `resolve_turn_references` / `EvidencePlan` in `backend/services/evidence_service.py`, `_persist_success`, `EvidenceSpan` (immutable) | `test_evidence_spans.py` |
| A person's action item may cite one segment of the current revision only (`422 invalid_source_segment` otherwise); the citation is a `human` span | `_source_segment` (current revision), `human_evidence` | same |
| Evidence stays bound to the analysed revision after a correction; the API reports whether that revision is still current | `GET /meetings/{meeting_id}/evidence[/{evidence_id}]` (`is_current_revision`) | same |
| Evidence rows are tenant rows readable through a share like other content; a foreign tenant sees `404` | RLS shared-content policy (`evidence_spans`), `_meeting_or_404` | `test_evidence_spans.py`, `test_transcript_revisions.py` (share ceiling) |
| Quality, coverage and degradation (transcription `complete`/`partial`/`unavailable`, speaker attribution `verified`/`inferred`/`unknown`, analysis `complete`/`partial`/`unavailable` + `analysis_stale`, evidence `sufficient`/`partial`/`insufficient`, speech coverage, degraded stages, mock label, usage state) are one assessment derived from persisted rows and reported identically by the meeting detail, `GET /meetings/{meeting_id}/quality`, the shared view, JSON/TXT/VTT exports and the completion audit; nothing is upgraded client-side (WP04-T06) | `assess_quality` / `assess_meeting` in `backend/services/quality_service.py`, `_quality` in the routers, `_quality_lines` in exports | `test_meeting_quality.py` |

## 6d. Ingestion ledger (WP05-T01, ADR 0016)

| Rule | Enforcement | Test |
|---|---|---|
| Every accepted capture event is one `IngestionRecord` identified by `(workspace, source_type, external_event_id)`; the same event id in another workspace is an independent identity (no cross-tenant collision, no oracle) | unique constraint `uq_ingestion_event` on `ingestion_records`, `accept_ingestion` in `backend/services/ingestion_service.py` | `test_ingestion_ledger.py` |
| Same identity + same canonical payload digest replays the existing ingestion (`202 idempotent_replay` with the same meeting and job); no second conversation, root job, storage object, usage reservation or audit chain is created | `resolve_existing` (`REPLAYED`), single-transaction claim: the ledger row is inserted before the conversation/job so the unique index decides concurrent claims | same (SQLite + PostgreSQL thread test) |
| Same identity + different payload digest is a `409 ingestion_conflict`; the first event is never overwritten or silently reprocessed; the conflict is counted on the row and audited with ids only | `resolve_existing` (`CONFLICT`, row lock, `conflict_count` / `last_conflict_at`), audit `ingestion.conflict` | same |
| Replay of an identity whose conversation was deleted is `410 ingestion_target_deleted` | `resolve_existing` (`TARGET_DELETED`) | same |
| Different event id + same media digest follows the workspace `duplicate_media_policy` (`allow`: accepted and linked through `duplicate_of_id`; `reject`: `409 duplicate_media`, nothing created); the policy is a server-side workspace setting changed only by `workspace:manage` (+CSRF) and audited | `find_live_duplicate`, `Workspace.duplicate_media_policy`, `PATCH /admin/workspace-settings` | same, `test_admin_api.py` |
| Identity and provenance columns of an accepted ingestion are immutable; only `conflict_count`, `last_conflict_at`, `meeting_id`, `job_id` may change | ORM `before_update` guard (`IngestionImmutableError`) in `backend/database/saas_models.py` | `test_ingestion_ledger.py` |
| Ledger rows are tenant rows: reads require `meeting:read` and honour record scope (a team-scoped member sees their own rows and rows of visible conversations); a foreign tenant sees `404`; API keys read with `meeting:read` and upload with `meeting:create` (provider `api`, key id recorded) | `visibility_clause`, routes `GET /ingestions[/{ingestion_id}]`, RLS tenant-only policy (`ingestion_records`) | same, `test_row_level_security.py` |
| Audit of ingestion events carries ids, source type/provider, sizes and counts only, never payload content, file names beyond the sanitizer, media or secrets | `_audit_details` in `backend/services/ingestion_service.py`, audit sanitizer | `test_ingestion_ledger.py` |
| Media validation runs in a fixed order before any object is stored: filename/extension policy, declared MIME syntax, size bounds, a signature pre-check that refuses executables/archives/documents/images and foreign-container-family bytes without spawning anything, bounded ffprobe (fixed argv, no shell, probe/analyze/allocation caps, timeout), container/codec/stream-shape/duration bounds, a plausibility floor for compressed audio (bytes per claimed second), a bounded ffmpeg decode of a prefix (`-xerror`, single thread, timeout, output discarded) and the declared MIME against the detected content; the same bytes always get the same answer (WP05-T02) | `validate_media_upload` in `backend/media/validation.py`, `MediaValidationPolicy.from_settings` (`MEDIA_DECODE_PROBE_SECONDS`, `MEDIA_MIN_BYTES_PER_SECOND`, `FFMPEG_*`) | `test_media_validation.py`, `test_media_fixtures.py` (real binaries) |
| A terminal media refusal of a staged payload is a `rejected` ledger row under the same identity (reason code, media digest, no conversation/job/object): the same payload replays the recorded refusal (`422`/`413`, `replayed`) without inspecting the bytes again, a different payload is `409 ingestion_conflict`; transient tooling failures (`503`: probe/decoder unavailable or timed out, file changed) and refusals before the payload exists leave the identity free and are audited only; rejected rows are never a live duplicate for the media policy (WP05-T02) | `record_rejection` / `audit_rejection` / `resolve_existing` in `backend/services/ingestion_service.py`, `_record_refusal` / `_settle_existing` in `backend/routers/v1/meetings.py`, check `ck_ingestion_accepted_reason` (migration `a8c4e2f6d1b9`) | `test_ingestion_ledger.py` |

| Adversarial fixtures travel the real route with the real tools (WP05-T06): a forged extension, a corrupted MP3 behind a valid header, a truncated MP4 (no `moov`), an ID3 tag claiming ~268 MB, sixteen channels, 384 kHz, a WAV whose header claims an hour and a real duration over the bound are refused terminally (`422`/`413`) as `rejected` ledger rows with no meeting, job, object or quarantine file left behind and the original bytes untouched; the identical bytes under the same key replay the recorded refusal without re-inspecting; an upload over the byte cap is refused while staging before any tool runs (`413 upload_too_large`, audit only); a real decoding timeout is transient (`503 media_decode_timeout` with `Retry-After`, no ledger row, identity free) and the retry with the same key creates exactly one chain whose stored object matches the submitted digest; a WAV polyglot with markup after the data chunk is audio, stored byte-for-byte and only ever served as an attachment | `validate_media_upload` (real ffprobe/ffmpeg), `_record_refusal`, `_upload_failure` (`Retry-After` on non-terminal refusals and storage unavailability), `stage_upload` byte cap, `staged.delete()` | `test_media_adversarial_api.py`, `test_media_fixtures.py` |

## 6e. Media objects and their lifecycle (WP05-T03, ADR 0017)

| Rule | Enforcement | Test |
|---|---|---|
| Every stored byte stream of a conversation is a `media_objects` row: the ORIGINAL upload (at most one per conversation, partial unique index) and derived kinds (`normalized`, `enhanced`, `channel_derived`, `clip`, `export`) that must name the object they were made from; content columns (kind, location, digest, size, type, duration, lineage, provenance) are immutable, only lifecycle columns change; rows are never removed | `MediaObject`, `ck_media_lineage`, `uq_media_original_per_meeting`, ORM guard `MediaImmutableError`, `register_original` / `register_derived` in `backend/services/media_lifecycle_service.py` | `test_media_lifecycle.py` |
| The ORIGINAL is registered in the upload transaction from the digest-verified stored object, linked to the recording, the ingestion row and the root job, with the conversation's retention; the storage layer never overwrites a published object | `create_upload_job`, `Recording.media_object_id`, `put_stream` (no in-place overwrite) | same, `test_storage_providers.py` |
| Deletion is state first, bytes second: a request marks the row `deletion_pending` (or records the request on a `legal_hold` row and keeps the bytes), the removal happens after the commit and is idempotent; a failed removal leaves the row pending; `deleted` rows carry `deleted_at`. Meeting deletion and delete-after-processing both go through this path; on meeting deletion the recording is unavailable at once, under delete-after-processing the recording is marked deleted only once the bytes are gone (WP06: nothing is pretended) and a hold keeps both; closing an ORIGINAL (`mark_deleted`, whichever path removed the bytes — a released hold's request included) retires its recording so no row claims bytes that no longer exist | `retire_meeting_media`, `request_deletion`, `delete_objects`, `mark_deleted`; `delete_meeting` route; `_delete_audio_after_success` in `backend/jobs/processor.py` | same, `test_job_processor.py` |
| A legal hold is a compliance action separate from deletion preferences (WP07-T02): only `legal_hold:manage` (owner; never an API-key scope, admins excluded) places or releases one, through `POST /meetings/{meeting_id}/legal-hold` (every live object of a live conversation), `POST /media/{media_id}/legal-hold` (one object, also of a deleted conversation) and `POST /media/{media_id}/legal-hold/release`; a hold records who placed it, a reason (3–200 chars) and a review date (future, ≤ 3650 days; constraint `ck_media_hold_review`), is idempotent (a repeat never rewrites owner/reason), overrides retention expiry, a recorded deletion request, orphan classification and temporary cleanup until released, and cannot be placed on deleted bytes (`409 already_deleted`); the release needs its own reason, is audited with `release_reason` / `held_since` / `deletion_pending`, and resumes a recorded deletion request state-first, bytes-second; the reconciliation sweep reports overdue reviews (`holds_review_due`, warning log) and never acts on them | `place_legal_hold` / `release_legal_hold` / `holds_due_for_review`, `Permission.LEGAL_HOLD_MANAGE`, `reconcile_media_objects` | `test_legal_hold.py`, `test_media_lifecycle.py` |
| The legal-hold register (HOM-192): `GET /admin/legal-holds` lists every object of the caller's workspace that is on hold now (released and never-held objects are left out) with media id, kind, meeting id and title, reason, who placed the hold, when, and the review date, and no storage key, digest, filename or size; it needs `legal_hold:manage` (owner only; admin, member and viewer get `403`; never an API-key scope, so every key gets `403 api_key_scope_denied`); holds of soft-deleted conversations and of objects without a meeting are listed (`meeting_title` is the stored title, or null without a meeting row); `due_before` keeps holds whose review date is on or before it (a value without a time zone is UTC); the order is review date then id, ascending, paged with a cursor of its own (a malformed cursor or another list's cursor is `400 invalid_cursor`); the query and its meeting join are bound to the caller's workspace; the read writes no audit event | `list_legal_holds` in `backend/routers/v1/admin.py`, `list_workspace_legal_holds` in `backend/services/admin_legal_holds_service.py` | `test_admin_legal_holds_api.py` |
| Integrity is verified by re-reading the bytes and comparing digest and size (`verified` / `corrupt` / `missing`), audited; the route needs `meeting:update` (+CSRF) and answers `404` for another conversation's object | `verify_integrity`, `POST /meetings/{meeting_id}/media/{media_id}/verify` | same |
| Media rows are tenant rows readable with `meeting:read` under record scope through the conversation; responses never carry the storage key; a foreign tenant sees `404`; RLS tenant-only policy | `GET /meetings/{meeting_id}/media`, `_get_meeting`, `media_objects` in `WORKSPACE_TABLES` | same, `test_row_level_security.py` |
| Audit of media events carries ids, kind, state, reason, size and integrity only | `_details` in `backend/services/media_lifecycle_service.py` | `test_media_lifecycle.py` |

## 6f. Reconciliation of objects and rows (WP05-T04, ADR 0018)

| Rule | Enforcement | Test |
|---|---|---|
| A commit failure after the object is stored rolls every row back, the route's abort path removes the object and the identity is free again; the retry creates one meeting, one job, one ingestion row, one media row and one reservation per metric; a hundredfold replay of one finalization yields one conversation and one usage effect | `upload_meeting` `finally` block, `uq_ingestion_event`, job and reservation idempotency keys inside one transaction | `test_ingestion_ledger.py` (abort/retry, 100× replay, invariants) |
| `deletion_pending` rows past the grace period are retried by the periodic sweep and closed only when the bytes are gone (closing an ORIGINAL retires its recording) | `reconcile_media_objects` in `backend/jobs/media_reconciliation.py` through `delete_objects` / `mark_deleted`; beat entry `media-reconciliation` | `test_media_reconciliation.py` |
| Objects under a tenant prefix without a live row (media rows not `deleted`, checkpoint artifacts not yet removed, pre-ledger recordings) and older than the grace period are reported (one audited count per workspace) by default and removed only when `MEDIA_RECONCILIATION_REMOVE_ORPHANS` is enabled, audited without the key; live rows whose bytes are missing are flagged `missing` through `verify_integrity`, never fabricated or removed; the sweep binds the tenant context per workspace and lists only that tenant's prefix through `ObjectStorage.iter_objects` (ownership-checked keys) | `reconcile_media_objects`, `LocalObjectStorage.iter_objects` / `purge_incomplete`, `S3ObjectStorage.iter_objects` | `test_media_reconciliation.py`, `test_storage_providers.py` |
| A listing failure skips the orphan and missing steps for that pass (the committed pending retry stands); a backend read failure while re-verifying one row skips only that row; local filesystem errors surface as `StorageError` (`local_list_failed`, `local_purge_failed`) and `purge_incomplete` validates the tenant id; every step is bounded by the batch size and a budget exhausted by the pending retry skips the listing entirely | `reconcile_media_objects` (`unavailable` count, `limit`), `LocalObjectStorage.iter_objects` / `purge_incomplete` | `test_media_reconciliation.py`, `test_storage_providers.py` |

## 6g. Object storage contract (WP05-T05, ADR 0019)

One suite (`backend/tests/test_storage_contract.py`) runs the same guarantees against the local adapter on a disposable directory, the S3 adapter over a controlled fake client, and — opt-in only — a designated disposable S3-compatible bucket (`backend/tests/_live_s3.py`; skipped by default and reported as NOT_RUN / BLOCKED_EXTERNAL, never as a pass). Fake-client results are adapter-contract evidence, not cloud IAM evidence.

| Rule | Enforcement | Test |
|---|---|---|
| Outcomes stay distinct: *missing* (`ObjectNotFoundError`, `exists() is False`, `delete() is False`) only for a 404 that is not a bucket-level code; *denied*, *transient* and *unavailable* surface as `StorageError` with the operation's stable code (`s3_read_failed`, `s3_head_failed`, `s3_delete_failed`, `s3_list_failed`, `s3_upload_failed`; `local_read_failed`, `local_stat_failed`, `local_delete_failed`, `local_list_failed`, `local_purge_failed`); a missing or misconfigured bucket (`NoSuchBucket`, `PermanentRedirect`) is `s3_bucket_unavailable`; `exists()` never answers `False` on a fault; a denied or failed deletion preserves the object for the retry | `S3ObjectStorage._is_not_found` / `_wrap`, `LocalObjectStorage._safe_lstat` / `open_reader` / `delete` (only `ELOOP`/`ENOTDIR`/`EISDIR`/`ENAMETOOLONG` mean an invalid key) | `test_storage_contract.py` (`read_faults`, `metadata_faults`, `delete_faults`, `missing_bucket`, `real_client_errors`) |
| Scope is validated before any backend call: another tenant's key, traversal, extra segments, short/invalid tokens, backslashes, absolute keys and wrong prefix casing are `InvalidObjectKeyError` for read/exists/delete/presign with no client operation issued; invalid tenant ids are `InvalidTenantError` for write, listing and purge (the default `purge_incomplete` validates the tenant, so a missing tenant context is never a global purge) | `validate_owned_object_key`, `validate_object_token`, `validate_tenant_id`, `ObjectStorage.purge_incomplete` | `test_storage_contract.py` (`another_tenants_key`, `forged_or_malformed_keys`, `invalid_tenant_ids`, `bad_object_tokens`) |
| Uploads are one bounded `PutObject` request: the size cap is enforced while streaming (an oversized or checksum-mismatched input issues no request), unknown-length input is spooled to a bounded temporary file and sent with the exact `ContentLength`, caller-chosen tokens use `If-None-Match: *` and a 412 is resolved by comparing the recorded SHA-256 (conflict preserves the existing object, nothing is deleted), a failed generated-key upload is cleaned up while a failed token upload never deletes; no multipart operation and no transfer helper (`upload_fileobj`, `TransferConfig`) is ever used; the S3 ETag is never treated as the digest | `S3ObjectStorage.put_stream` (real boto3 client under `botocore.stub.Stubber`, every API operation name recorded) | `test_storage_contract.py` (`sdk_probe` tests, `source_never_reaches_for_transfer_helpers`) |
| Creation is atomic per identity: an interrupted stream, a non-byte chunk, a checksum mismatch or an oversized input leaves no object and no temporary (local `.upload-*` / `.metadata-*`), the identity remains free for a retry; identical bytes under the same token are idempotent, different bytes conflict; empty input is stored deterministically (refusing empty media is the validator's job) | `LocalObjectStorage.put_stream` (temp + hard-link publish), `S3ObjectStorage.put_stream` | `test_storage_contract.py` (creation section) |
| Listing is lazy and tenant-bound: pagination (including an empty truncated page), entries under the prefix that are not owned object keys are skipped, one page is fetched per consumed page so a budget bounds the backend work, and `purge_incomplete` removes only stale temporaries and orphaned sidecars — never a published object or an in-flight write | `iter_objects`, `purge_incomplete` | `test_storage_contract.py` (listing section) |

## 6h. Retention policy (WP07-T01, ADR 0020)

| Rule | Enforcement | Test |
|---|---|---|
| Retention is a server-side workspace policy with explicit semantics: `finite` (deletion-eligible `retention_days` ∈ [1, 3650] after ingestion), `indefinite` (never by age; only explicit deletion), `immediate` (eligible once processing is terminal); the days are 0 under every non-finite mode (check constraint `ck_workspace_retention_mode`); a client never supplies an expiry — `retention_expires_at` is stamped at ingestion from the workspace row and copied to the media rows | `RetentionMode`, `retention_expiry_for` in `backend/services/retention_policy.py`, `create_upload_job` | `test_retention_policy.py` |
| Only `workspace:manage` (+CSRF) changes the policy, and every change is audited with field names only; a bare `retention_days: 0` is refused as ambiguous, `retention_days` under `indefinite`/`immediate` needs the mode as well, switching to `finite` needs a positive number, and `legacy_unresolved` can never be chosen | `update_workspace_settings` / `_apply_retention_policy`, `PATCH /admin/workspace-settings` | `test_retention_policy.py`, `test_admin_api.py` |
| The pre-WP07 ambiguous `retention_days = 0` is migrated as `legacy_unresolved` (never reinterpreted as "delete now" or "keep forever"): no expiry is stamped, `is_age_based_deletion_enabled` is false, nothing is purged by age until an administrator picks an explicit mode (a positive number resolves it to `finite`); the downgrade drops only the mode column, so the days are restored losslessly | migration `c3e7a9d2b5f1`, `is_age_based_deletion_enabled` (re-checked by every sweep before acting on a stored expiry) | `test_retention_policy.py` (PostgreSQL round trip) |

## 6i. Tombstone-first deletion and the durable purge (WP07-T03, ADR 0021)

| Rule | Enforcement | Test |
|---|---|---|
| A committed tombstone (`meetings.deleted_at`) denies every application-mediated path at once — detail, list, transcript, revisions, summary, actions, evidence, quality, export, recording download, job, search, public/recipient shares (links revoked in the same transaction), API-key routes, replay (meeting row lock) and checkpoint reuse — on current server state; a presigned URL issued earlier stays valid until its own expiry (`STORAGE_PRESIGN_TTL_SECONDS`), nothing revokes it instantly | `tombstone_meeting` in `backend/services/conversation_purge_service.py`, `_get_meeting` / `_meeting_or_404` / share lookups, `replay_dead_letter_job`, `resume_stage` | `test_retention_purge.py` |
| Stage publication locks the meeting row after its lease fence, so a tombstone committed meanwhile is seen and nothing is published after it; a cancelled attempt persists nothing | `_persist_success` in `backend/jobs/processor.py`, `publish_checkpoint` in `backend/jobs/checkpoints.py` (`with_for_update`) | `test_retention_purge.py::test_deletion_during_processing_never_republishes_content` |
| Every tombstone carries a durable `conversation_purges` row (tenant table, RLS); an attempt claims it with `FOR UPDATE SKIP LOCKED` and pushes `next_attempt_at` forward, removes the derived content rows (revisions, segments, analysis, evidence, summary, actions, speakers, share recipients) and scrubs the meeting title and recording filename, then removes bytes state-first/bytes-second; usage, provider usage, stage runs, audit, ingestion and media rows are kept (ids, amounts, states) | `run_purge_attempt`, `ConversationPurge`, migration `e6c2a9f4d1b8` | `test_retention_purge.py` (incl. PostgreSQL simultaneous claims) |
| The point of no return for bytes is the locked re-read in `delete_objects`: a hold committed after the deleter loaded its rows keeps the bytes; a hold arriving during the removal waits for the row lock and is refused `already_deleted`; hold routes lock the media rows before deciding | `delete_objects`, `media_by_id(for_update=True)`, `media_for_meeting(for_update=True)` | `test_retention_purge.py` (stale-scan race; PostgreSQL removal/hold race) |
| A storage error (denied, unavailable, failed) is never read as "gone": the row stays `deletion_pending` and the purge `PENDING` with `storage_pending`; retries back off (`RETENTION_PURGE_RETRY_BASE_SECONDS` doubling up to the max); after `RETENTION_PURGE_MAX_ATTEMPTS` the purge is `FAILED`, audited and alerted every sweep until `requeue_failed_purge` (operator) gives it a new budget | `run_purge_attempt`, `_fail_if_exhausted`, `requeue_failed_purge`, `run_retention_purge` (`purges_failed_open`, `purges_overdue` > 24 h) | `test_retention_purge.py` |
| A legal hold on any object of the conversation defers the whole purge (content rows, stage outputs, held bytes) without spending an attempt; the WP06 artifact sweep also skips stage outputs of a deleted, held conversation; an overdue review never releases anything; a release makes the deferred purge due at once | `run_purge_attempt` (`legal_hold`), `_cleanup_candidates` (`_meeting_held`), `nudge_purge` | `test_retention_purge.py::test_hold_blocks_expiry_overdue_review_never_releases_and_release_resumes` |
| The retention sweep tombstones a conversation only when, re-checked under the workspace (shared) and meeting (exclusive) row locks, the policy is enforceable now (`finite`/`immediate`; `indefinite` and `legacy_unresolved` never act), the stored expiry is at or before now, no job is `queued`/`running` (terminal jobs are neither mutated nor replayed and do not block) and no object is held; anything else is looked at again next pass | `expire_meeting`, `_expiry_candidates` in `backend/jobs/retention_purge.py`; beat entry `retention-purge` | `test_retention_purge.py` (boundary, modes, immediate) |
| Tenancy: each workspace is swept in its own tenant-bound session; a purge id presented under another tenant is not claimed; a foreign tenant cannot delete or see the conversation | `bind_tenant_context`, `conversation_purges` in `WORKSPACE_TABLES` | `test_retention_purge.py` (incl. PostgreSQL RLS probe) |
| Every tombstone is written after its commit to an append-only, fsynced journal outside the database and object-store backups (`TOMBSTONE_JOURNAL_DIR`, mode 0600, per-day JSON lines with a SHA-256 per line): ids, reason, timestamp and opaque object keys only (held objects omitted), never titles, filenames or text; `conversation_purges.journaled_at` is the outbox marker, the retention sweep writes any backlog and reports `journal_backlog`; a journal failure never blocks deletion (WP07-T04, ADR 0022) | `backend/services/tombstone_journal.py`, `delete_meeting`, `run_retention_purge`, migration `f3d7b1e9a5c4` | `test_tombstone_reapply.py` |
| After a restore and before traffic, `python -m backend.jobs.tombstone_reapply reapply` re-tombstones every journaled conversation that is alive again (queued/running jobs cancelled, links revoked, holds in the restored database respected), reopens its purge with `reapplied_at` (content rows and bytes restored by the backups are removed again) and `verify` exits non-zero while any journaled conversation is alive or a journal line is untrustworthy; keys of conversations unknown to the restored database are removed only with `--remove-unknown-keys` and never while a live row references them | `backend/jobs/tombstone_reapply.py`, `_remove_reappeared` in `run_purge_attempt` | `test_tombstone_reapply.py` (SQLite file-level backup/restore drill; simulated restore on PostgreSQL) |

## 6j. Text PII detection, redacted versions and restricted mappings (WP07-T05, ADR 0023)

| Rule | Enforcement | Test |
|---|---|---|
| Detection is deterministic and limited to validated structured classes (`email`, `phone`, `ir_national_id` check digit, `bank_card` Luhn, `iban` mod 97, `ir_postal_code` only next to a postal-code keyword); Persian/Arabic-Indic digits, separators, `+98`/`0098`/`98`/`0` prefixes and bracketed area codes are normalised with offsets kept; names, addresses, spelled-out numbers and anything else are not detected (disclosed residual risk: redaction is not anonymisation and never masks audio) | `backend/privacy/text_pii.py` (`DETECTOR_VERSION`) | `test_text_pii.py` (variants, hard negatives, synthetic annotated gate) |
| The workspace policy (`pii_redaction_enabled`, default off; `pii_redaction_classes`, only supported classes, at least one) changes only with `workspace:manage` (+CSRF) plus `pii:original_access` (§6k) and is audited by field name | `update_workspace_settings`, `PATCH /admin/workspace-settings` | `test_text_pii.py`, `test_admin_api.py` |
| A redacted version is identified by (revision, digest of detector version + sorted classes): a detector or policy change creates a new version, an existing one is never rewritten (ORM immutability guard); placeholders are stable per value within a version | `redact_revision`, `TranscriptRedaction` / `RedactedSegment`, `uq_redaction_revision_policy` | `test_text_pii.py` |
| Originals of replaced values exist only as AES-256-GCM ciphertext in `pii_mappings`, bound by associated data to tenant, redaction and placeholder, under keys from `PII_MAPPING_KEYS` (never in the database; `key_id` per row allows rotation); without a key nothing original is retained and reveal is impossible; reveal is audited (`pii.revealed`: placeholder, class, reason — never the value); the reveal route is in §6k | `backend/privacy/mapping_crypto.py`, `reveal_placeholder` | `test_text_pii.py` |
| Redacted versions and mappings are tenant rows (RLS tenant-only, not reachable through a share token), removed by the conversation purge; audits and logs carry ids, counts, classes and versions only | `WORKSPACE_TABLES`, `_remove_content` | `test_text_pii.py` (incl. PostgreSQL RLS probe, purge) |

## 6k. Routing to the redacted or original version and policy provenance (WP07-T06, ADR 0024)

| Rule | Enforcement | Test |
|---|---|---|
| Under an enforced workspace policy every content read defaults to the redacted version for every caller: transcript (any revision), evidence, summary (overview, key points, decisions, topics), action items (description, assignee text, evidence offsets), export (all formats) and the correction/action write responses; `?view=original` needs `pii:original_access` (owner, session only; `403 original_access_denied` otherwise, also for API keys) and is audited as `transcript.original_accessed` with the surface; without an enforced policy the original is the default and `?view=redacted` is available to any reader | `meeting_view` / `choose_view`, `MeetingView` | `test_privacy_routing.py` |
| Share links never show originals under an enforced policy (view and export); the share session cannot write, so the redacted texts are computed with the same deterministic detector and are identical to the persisted version | `_share_view` / `enforced_view` | `test_privacy_routing.py` |
| Search under an enforced policy matches only what the caller may read (title, speaker names, redacted transcript, summary and action texts); original-text candidates are re-checked against the redacted texts, so a search for a redacted value returns nothing | `_redacted_search_matches` | `test_privacy_routing.py` |
| The processor snapshots the policy at claim time; the intelligence provider receives only redacted turns and text (one pseudonymizer in segment order, so placeholders match the persisted redacted version created with the revision); the intelligence checkpoint identity includes the policy digest; `analysis_runs.input_view` / `input_policy_digest` record what the provider read (constraint `ck_analysis_run_input_view`; pre-T06 rows are `original`) | `_analysis_input`, `_persist_success`, migration `b5d9e3a1c7f2` | `test_privacy_routing.py` |
| Placeholders a person writes back (transcript correction, action item create/update) are replaced by the originals they stand for before storage, so editing a redacted text never overwrites an original with a token | `MeetingView.restore` | `test_privacy_routing.py` |
| Evidence offsets are mapped into the redacted text and widened to whole placeholders, so a citation never shows part of a value | `RedactedText.offset` | `test_privacy_routing.py` |
| `POST /meetings/{id}/transcript/redactions/{rid}/reveal` decrypts one placeholder: `pii:original_access` + CSRF, reason 3–200 chars, audited `pii.revealed` (placeholder, class, reason; never the value), `404 mapping_not_retained` without a stored mapping, `409 mapping_key_unavailable` without the key | `reveal_redacted_value`, `reveal_placeholder` | `test_privacy_routing.py` |
| Changing `pii_redaction_enabled` / `pii_redaction_classes` needs `pii:original_access` in addition to `workspace:manage` (whoever may switch redaction off could read originals) | `patch_workspace_settings` | `test_privacy_routing.py`, `test_text_pii.py` |
| Exports carry privacy provenance (view, enforced, detector version, classes, policy digest, redacted version id, entity count, what the current analysis read, the residual-risk notice) and `meeting.exported` / share access audits record the view | `export_privacy`, `MeetingExport.privacy` | `test_privacy_routing.py` |

## 6l. Subject-scope export (WP07-T07, ADR 0025)

| Rule | Enforcement | Test |
|---|---|---|
| `POST /privacy/subject-exports` returns what the workspace holds about a data subject: `pii:original_access` (owner; admin, member and API keys 403) + CSRF, 1–10 identifiers and a reason (3–200 chars) | `export_subject` | `test_retention_lifecycle.py` |
| An identifier must be exactly one supported e-mail, phone, national ID, bank card or IBAN (`422 invalid_subject_identifier` for names, fragments, sentences or several values); every spelling (Persian/Arabic-Indic/ASCII digits, separators, `+98`/`0098`/`0`) is canonicalised by the redaction detector and matched against detected spans, never substrings | `parse_subject_identifier`, `_Matcher` | `test_retention_lifecycle.py` |
| Scope: live conversations only (a tombstoned conversation — held or not — is out, as for every normal path), every transcript revision, the summary and action items; at most 500 conversations per export (newest first, `truncated` reported); the document states what is not included (audio, backups, audit metadata, deleted conversations, mentions without a supported identifier) and that segments may contain other people's words | `build_subject_export` | `test_retention_lifecycle.py` |
| Audited as `subject.exported` with identifier classes and counts, the scan size, matches and the reason — never an identifier or text | `export_subject` | `test_retention_lifecycle.py` |

## 6m. AI processing policy, run snapshots and enforcement (WP08-T02/T03, ADR 0027/0028)

| Rule | Enforcement | Test |
|---|---|---|
| The policy (per stage: allowed endpoint kinds, regions, manifests, cloud text form, fallback) is read with `workspace:manage` and changed only with `ai_policy:manage` (owner; admin, member and API keys 403) + CSRF, a reason (3–500 chars) and the version read (`409 policy_version_conflict` otherwise); every change is a new append-only version, audited `ai_policy.updated` (versions, digest, changed stages, reason) | `update_ai_policy`, `set_policy` | `test_ai_policy.py` |
| Without a stored version a workspace runs under the built-in default (version 0): in-process and private endpoints only, no cloud processing; cloud endpoints can be allowed only together with approved regions | `DEFAULT_POLICY`, `StagePolicy` | `test_ai_policy.py` |
| Each processing attempt reads the current version at claim and records a snapshot (policy document and digest, text-privacy digest, strict mode, per-stage adapter, manifest and placement) before any stage runs; retries and replays read the then-current version | `_claim_job`, `_record_policy_snapshot` | `test_ai_policy.py` |
| Policy versions, run snapshots and model manifests are append-only for the application role (ORM guards; PostgreSQL `REVOKE UPDATE, DELETE`); both policy tables are workspace tables under row-level security | `PolicyRecordImmutableError`, `APPEND_ONLY_TABLES` | `test_ai_policy.py` |
| Enforcement (WP08-T03, ADR 0028): every stage candidate is filtered by the policy (endpoint kind, region, manifest allowlist, licence) and capability (explicit language) before any ranking; a refused adapter is never called and nothing substitutes for it — refused ASR fails the attempt permanently (reservation released), refused diarization/analysis end `unavailable` with the typed reason and the meeting is `partial` | `backend.providers.routing`, `process_job` | `test_provider_routing.py` |
| A cloud analysis endpoint receives the redacted transcript (every class) unless the policy allows the original; the workspace redaction policy applies to every endpoint; a redaction failure never falls back to the original; permission for text never covers audio | `analysis_text_classes`, `_analysis_input` | `test_provider_routing.py` |
| A policy or redaction change during an attempt ends it with the retryable `policy_changed` before the next stage; the next attempt runs under the current policy | `_ensure_policy_current` | `test_provider_routing.py` |

## 7. Escalation paths and where they are refused

| Path | Control | Test |
| --- | --- | --- |
| Grant a stronger role than the caller may grant (invite/promote to admin as admin; any owner grant) | `GRANTABLE_ROLES` in `create_invitation` / `change_member_role` | `test_members_api.py`, `test_authorization_matrix.py::test_role_matrix_allowed_and_denied` |
| Manage a peer or superior (admin removes/demotes admin or owner; self-management) | `can_manage_member` | same |
| Move a record between teams the caller does not supervise, or detach it | `validate_team_attribution` | `test_authorization_matrix.py::test_team_scope_restricts_records_and_attribution` |
| Attribute a record to a foreign-workspace or inactive team | `validate_team_attribution` (`422 invalid_team`) | same |
| Reach related objects (transcript, summary, actions, speakers, export, shares, job, recording) through a meeting outside the caller's scope | record predicate inside every `_meeting_or_404`/`_get_meeting` | same; cross-tenant variant in `test_tenant_scope.py` |
| Bypass through exports | export route uses the same predicate; there is no bulk export | same |
| Bypass through background jobs | worker keyed by `job.workspace_id`; jobs never read user context | `test_tenant_scope.py::test_background_processing_writes_only_into_the_owning_workspace` |
| Bypass through API keys | a key acts as its bound user with `scopes ∩ API_KEY_SCOPES ∩ current membership permissions`, pinned to its workspace; session, organization and share-token routes refuse keys; `api_key:manage` is never a scope (§6b) | `test_api_keys.py`; `test_authorization_matrix.py::test_route_inventory_document_matches_code` (any new route must declare a policy) |
| Tenant id in a request body | `extra="forbid"` on write schemas | `test_authorization_matrix.py::test_tenant_id_in_body_and_foreign_team_are_rejected` |
| Use a revoked/expired membership through another endpoint | per-request `membership_effective_clause` | `test_tenant_scope.py::test_removed_member_loses_access_on_the_next_request`, `test_authorization_matrix.py::test_membership_validity_window_is_enforced` |
| Use an expired/revoked team assignment | `assignment_effective_clause` (evaluated per request) | `test_team_scope_restricts_records_and_attribution` |

## 8. Known gaps (tracked, not hidden)

- Platform operator status is used only by the `+operator` voice node routes and the deployment data of `GET /api/v1/voice/capacity` (§1); there is still no delegated, time-bounded operator grant and no other operator action (deny). Organization administration exists since WP03-T03 (§1a, §3a, §4a; carrier `OrganizationMembership`); organization-level billing allocations, organization settings/rename routes and organization service credentials (WP03-T07) are still open; the organization share audience exists (§6a).
- Share links and downloads: recipient (email/phone) and organization audiences, recipient editing and short-lived download authorization are in place (§6a); notifying recipients (email/SMS delivery of a share) has no product surface.
- API keys: user-bound workspace keys with a fixed scope set exist (§6b, ADR 0013); service principals, organization-owned keys, rotation and per-key usage metering (WP16-T02) are open.
- PostgreSQL row-level security (WP03-T05) is in place as a second, database-enforced layer: `backend/database/rls.py` (policies, `homayar_app` role, `FORCE`), `backend/database/tenant_context.py` (transaction-scoped context from validated state), migration `c4e8b2a7d519`, `backend/tests/test_row_level_security.py`. It does not replace this matrix: record scope (§5) and grant rules (§4) remain application decisions; RLS guarantees that a request or worker can never touch rows outside the tenant it validated.
- The console does not expose teams/assignments yet; the API is the contract.
- Media objects (WP05-T03): no producer writes derived kinds yet (WP09 normalisation/enhancement, clips and stored exports come later; the service and constraints exist); `retention_pending` is not entered (the WP07-T03 retention sweep tombstones and purges whole conversations instead, §6i); legal holds are owner-only routes with a review date since WP07-T02 (no organisation-level or platform-operator hold, no automatic action on an overdue review); failed physical deletions are retried by the WP05-T04 reconciliation sweep.
- Ingestion ledger (WP05-T01/T02): only `FILE_UPLOAD` events are produced today (console and API-key uploads); the other source types exist as identity contract only. Legacy uploads made before the ledger were backfilled with their job idempotency digest as event id. Media validation verifies a decoded *prefix* (bounded); an understated container duration (more audio than the header claims) is bounded by the worker's job deadline, not refused at upload. Rejections before staging (missing filename, empty, over the byte limit) are audited without a ledger row because no payload identity exists yet. Child-process rlimits for ffprobe/ffmpeg are not set (timeouts and probe/analyze caps bound them); the polyglot case relies on the attachment disposition of downloads, not on rewriting the bytes.
- Media reconciliation (WP05-T04): orphan removal is report-only unless `MEDIA_RECONCILIATION_REMOVE_ORPHANS` is enabled; the S3 listing is one prefix walk per workspace per pass (no inventory) and is unqualified against a real bucket; uploads are single-request `put_object` calls, so there are no multipart uploads to abort.
- Storage contract (WP05-T05): the live S3-compatible run is opt-in and has not been executed (BLOCKED_EXTERNAL: no designated disposable bucket or authorization in this environment); fake-client and `Stubber` evidence covers the adapter, not provider IAM, bucket policy, encryption at rest or the behaviour of a specific S3-compatible service; the local adapter maps filesystem faults during a *write* (`mkstemp`, link) as raw `OSError` (no object is ever published) rather than a `StorageError` code.
- Deletion and purge (WP07-T03, §6i): conversations tombstoned before WP07-T03 have no purge row and keep their content rows until an operator enqueues them (no automatic backfill); original filenames remain on the immutable media and ingestion ledger rows; purge attempts write media audits without an actor; `requeue_failed_purge` has no API route (operator entrypoint only); presigned URLs are revoked only by expiry; restores reapply tombstones only from the journal (WP07-T04): tombstones written while no journal was configured, holds or releases made after the backup, and backups themselves are not rewritten — deleted data remains in backups until they expire (runbook).
- Subject export (WP07-T07, §6l): synchronous and bounded (500 conversations); no subject-scope deletion (deletion stays per conversation, so an operator decides about conversations that also hold other callers' data); postal codes and names cannot identify a subject; no console UI.
- Text privacy (WP07-T05/T06, §6j/§6k): the original transcript rows stay in the database as the restricted original (access-controlled, not encrypted at rest by the application); analysis outputs produced before the policy was enabled are redacted at read time, not rewritten; values that occur only in analysis text are numbered after the transcript's; search under an enforced policy has no original-text mode and re-checks at most 4× the limit (200) candidates; ASR checkpoints hold original text (purged with the conversation); the console has no privacy-policy control, view switch or reveal UI; accuracy is measured only on a small synthetic set (no annotated customer-representative set exists).
