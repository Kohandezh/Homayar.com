# ADR 0066: IdP group mapping and honest deprovisioning

Date: 2026-11-13 · Status: Proposed (HOM-128, WP23-T02) · Builds on: ADR 0012, ADR 0013

## Context

Parent HOM-26 (F07) promises: a user disabled upstream, or removed from a group, loses
access within a documented maximum (proposed <= 15 minutes), with immediate admin
revocation available. OIDC offers no push channel: there is no SCIM, no webhook, nothing.
The application only learns that a user was disabled or left a group when it *asks* the
IdP. Pretending otherwise (syncing once at login and hoping) would leave removed users in
the workspaces indefinitely.

This decision defines the two halves: an explicit, stored mapping from IdP groups to
workspace memberships, and an honest deprovisioning story that states exactly when the
IdP is asked and what happens when it answers "gone" or does not answer. The OIDC client
itself (login, callback, account linking, the real checker) is HOM-127; this ADR builds
everything that does not need a live IdP and the interface HOM-127 will implement.

## Decision

### D1. OIDC is not SCIM: the app asks on two occasions and refuses on two gates

1. **Every refresh** of a user with an unrevoked OIDC identity asks the checker live,
   before the token is rotated (`auth_service.rotate_refresh_token`).  Refresh holds
   the `login_sessions` row lock during that call, so HOM-127's real checker must use
   a short network timeout.
2. **A periodic sweep** (`backend.jobs.federation_revalidation`, beat every
   `federation_revalidation_interval_seconds` = 300 s) revalidates every unrevoked
   identity whose `verified_at` is older than `federation_revalidation_max_age_minutes`
   = 10 minutes, oldest first, at most 200 per pass, one transaction per identity.
   Capacity: about 400 to 600 idle identities per 5 minutes.  Identities that keep
   answering "unavailable" stay the oldest and can fill a whole pass (one tenant's IdP
   down starves the others); HOM-127 should add a `last_checked_at` column or a
   rotation before real IdPs with many users arrive.
3. **API keys never wait on the IdP.** A request must not block on an external call, so
   a key bound to a federated user carries a freshness gate instead: if the identity's
   last confirmation is older than `federation_max_unconfirmed_minutes` = 15 minutes,
   the key is refused with `401 federation_reauthentication_required`.
4. **New login sessions carry the same gate** (password, OTP and Google all serve
   through `auth_service.create_login_session`): a federated user whose last IdP
   confirmation is older than 15 minutes (or was never confirmed) is refused with the
   same 401.  Nothing is revoked and no IdP call runs; the user signs in through the
   IdP again.  HOM-127's login callback must therefore update `verified_at` and clear
   `revoked_at` on the identity *before* it calls `create_login_session`, or its own
   fresh login would be refused.

Fail closed: when the checker is not configured, the identity profile is malformed, or
the checker raises or says "unavailable", every one of these paths answers
"unavailable" (refresh returns the same 401 and *nothing is revoked*; the user signs in
again). An already-issued access token lives out its <= 15 minutes.

### D2. The data model: one mapping table, one source column, one revocation stamp

- `federation_group_mappings`: (organization, issuer, group_name) -> (workspace, role),
  an explicit row an organization admin decides by hand (`organization:workspace:
  administer`, session + CSRF; a group never grants `owner`, and the role never exceeds
  what the actor could delegate by hand). Revocation is a stamp (`revoked_at`); a
  partial unique index keeps one unrevoked row per (organization, issuer, group,
  workspace). Issuer and group compare exactly (byte for byte, case-sensitive); IdP
  group ids are opaque (often GUIDs).
- `memberships.source`: `direct` (a person decided the row: owner bootstrap, accepted
  invitation, delegated installation) or `federated` (the reconciler owns it). Every
  existing row at migration time is `direct`: someone granted it by hand.
- `auth_identities.revoked_at`: stamped when the IdP said the user is gone. HOM-127's
  login callback clears it again on a later successful login (not built here).

### D3. The federated identity contract (for HOM-127)

A federated user is a `User` with an `AuthIdentity` where `provider = OIDC`,
`revoked_at IS NULL`, `profile["issuer"]` is the issuer URL, `profile["organization_id"]`
names the organization the IdP connection belongs to (both written at login), `subject`
is unique per provider, `verified_at` is the last successful IdP confirmation (updated
by every successful check), and `profile["groups"]` holds the sorted groups of the last
check (written by the reconciler, read when mappings change). An identity whose profile
lacks `issuer` or `organization_id` is malformed and fails closed exactly like an
unreachable IdP.

### D4. The reconciler and its rules

`backend.services.federation_service.sync_federated_memberships` turns one identity's
current groups into memberships, in one transaction (it flushes, never commits):

- **Desired state**: for each unrevoked mapping of this organization and issuer whose
  group matches, the highest mapped role (admin > member > viewer) in that workspace;
  only active workspaces of this organization count.
- No row -> create it `federated`. A `federated` row -> follow the IdP (role, active,
  no validity end; report granted / role_changed / nothing). A `direct` row -> never
  touched (`kept_direct`), and that includes every owner row.
- **Seats**: the same locking and counting as delegated installation
  (`lock_seats`, `consumes_seat`, `seat_available` with pending invitations). A full
  seat skips that workspace (audited, `federation.membership.seat_skipped`) and the
  sync continues with the others; a seat never raises.
- Every active `federated` membership of this user in this organization that is no
  longer desired goes inactive. Federated rows of other organizations and every
  `direct` row are not touched.
- One audit event per changed workspace (`federation.membership.synced`, the action in
  `details`); unchanged workspaces write nothing. A second identical sync changes
  nothing and audits nothing.

**Admin decision wins.** When an admin changes or removes a `federated` row (workspace
member routes, organization delegated installation and withdrawal, accepting an
invitation), the row becomes `direct`. A later sync never re-activates it and never
creates a new one for that workspace; an admin's role change is never overwritten. There
is deliberately no "hand back to the IdP" action.

**Mapping changes apply at once.** Creating or revoking a mapping re-runs the
reconciler inside the same request for every matching identity (unrevoked OIDC identity
whose profile names this organization and issuer; an organization membership is not
required), from each identity's stored groups, without touching `verified_at` (no IdP
was asked.

### D5. Gone means gone, but the account survives

When the checker says gone (on refresh or in the sweep):

- every login session of the user is revoked and `session_version` is bumped, so every
  access token dies on its next request;
- every active `federated` membership of that organization goes inactive;
- the identity is stamped `revoked_at` and one `federation.identity.deprovisioned`
  audit event is written.

On refresh the deprovisioning changes are committed even though the response is
`401 federation_reauthentication_required` (the commit runs first, like the replay
branch, so a later failure cannot undo the revocation). `direct` memberships and
`User.is_active` are **not** changed: a user may also be a direct member elsewhere, and
disabling the whole account is an admin decision, not the IdP's.

### D6. The promise, with its arithmetic

- **Session user disabled or removed upstream**: the next refresh asks the IdP live, so
  federated access ends when the current access token expires: <= `access_token_minutes`
  = 15.
- **Group removed upstream**: same path; the membership is removed on the next refresh
  and the per-request membership check applies it at once: <= 15 minutes.
- **API key of a federated user**: the sweep confirms every identity at least every
  `max_age + interval` = 10 + 5 = 15 minutes, and the freshness gate refuses keys older
  than 15 minutes: <= 15 minutes, and fail-closed when the IdP is down.
- **Login of a federated user**: the same freshness gate refuses a password, OTP or
  Google login whose IdP confirmation is older than 15 minutes.
- **IdP unreachable**: refresh and API keys fail closed.  Access can last up to 30
  minutes after the last confirmation: a login that just squeaked under the 15-minute
  gate mints a fresh 15-minute access token, and that token lives out its life.
- **Immediate admin revocation** (already existed, now documented): workspace member
  removal, organization member revocation, session revocation (each applies on the next
  request).
- The settings validator keeps the arithmetic honest at startup:
  `max_age * 60 + interval <= max_unconfirmed * 60` and
  `access_token_minutes <= federation_max_unconfirmed_minutes`.

After "gone", **federated access** ends within 15 minutes.  Direct memberships, API
keys and non-OIDC logins of the user stay until an admin acts, because the gates only
cover an unrevoked OIDC identity.

Known gaps, stated honestly: a login does not ask the IdP live, it only refuses stale
confirmations, so a user disabled upstream within the last 15 minutes may still log in
by password, OTP or Google until the confirmation goes stale (the sweep bounds it);
break-glass accounts and MFA are HOM-129; the real checker (the live userinfo call) is
HOM-127, so until it lands no checker is configured and every federated refresh, login
and API key fails closed by design.  The `federation.membership.seat_skipped` audit
event is written on every sync while a workspace stays full; this is accepted noise.
`reapply_organization_mappings` filters identities in Python,
which is fine at today's federated-identity counts; HOM-127 should move the filter into
SQL.

## Consequences

- One migration adds the mapping table, `memberships.source` and
  `auth_identities.revoked_at`; the RLS policy of the mapping table is the bound
  organization, like the other organization tables.
- The reconciler binds the tenant context per workspace for its reads and writes, so it
  behaves the same in a request and in the sweep (no user pre-bound).
- Permissions still come only from `ROLE_PERMISSIONS` (authorization-matrix §2); a
  mapped `member`/`viewer` never gets `meeting:attribute` (HOM-360/HOM-369: attribution
  is owner and admin only), and a mapping role never exceeds what the creating admin
  could delegate by hand.
- The console (HOM-240) and the OIDC client (HOM-127) build on the endpoints and the
  checker interface defined here; nothing in this ADR pretends OIDC pushed anything.
