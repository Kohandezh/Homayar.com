# ADR 0076: Privileged MFA, account cut-off and break-glass access

Date: 2026-11-13 · Status: Proposed (HOM-129, WP23-T03) · Builds on: ADR 0012, ADR 0013,
ADR 0047 (decision 5), ADR 0066

## Context

Parent HOM-26 (feature F07) asks for three things that the product did not have:

1. **Privileged MFA.** People who can change who sees what (workspace and organization
   owners and admins) and platform operators must prove a second factor. The product runs
   offline on customer servers, so the factor cannot depend on SMS or a cloud service.
2. **Cut-off.** A disabled user, a revoked session and a removed group must lose access
   within a stated maximum, with an immediate way for an operator to act.
3. **Break-glass.** When the identity provider or the normal admins are not available,
   there must be an emergency local account. It must be off by default, opened only from
   the server host, time-boxed, audited and alerting.

ADR 0047 (decision 5) named the first owner account as the break-glass account of an
offline install. This ADR makes that explicit: an operator designates the account, and it
signs in only inside a window the operator opens.

## Decision

### D1. What is privileged

A **session** is privileged on a request when it acts as:

- a workspace `owner` or `admin` (the membership the request resolved),
- an organization `owner` or `admin` (on organization routes), or
- a platform operator (`users.system_role = 'operator'`), on every platform route,
  whatever the workspace role.

`member`, `viewer`, organization `analyst` and `executive` are never asked for MFA.

### D2. The gate

`security/context.py` checks the gate on **every** privileged request, not only at login.
It runs where the role is resolved: in `_resolve_workspace` (so in `require_workspace`,
`require_permission`, `require_path_permission`, the module guard, the session-only trends
guard and the plain workspace routes such as `GET /capabilities`), in `require_organization`
(so in every organization permission guard), and in the platform operator guards after the
operator check. For a session the API-key scope check is a no-op, so the order is the one
of SPEC D5: before the permission check. The write variants inherit it. A guard written
later on top of these resolvers is gated without its own code.

A session passes when `login_sessions.mfa_verified_at` is set and is not older than
`PRIVILEGED_MFA_MAX_AGE_MINUTES` (default 720, allowed 5 to 1440). Otherwise:

- `403 mfa_enrollment_required` when the user has no confirmed factor,
- `403 mfa_required` when the user has one.

A passing session costs no query; the factor lookup runs only on a refusal. A refresh
keeps the verification (same session row); a new login starts unverified. The routes
`/auth/mfa*`, `/auth/me`, `/auth/refresh` and `/auth/logout` are outside the gate, so a
privileged user can always enrol and verify. `PRIVILEGED_MFA_REQUIRED=false` turns the gate
off; production then logs `PRIVILEGED_MFA_DISABLED` at startup.

### D3. TOTP

RFC 6238 TOTP on RFC 4226 HOTP, standard library only (`security/totp.py`): SHA-1, 6
digits, 30-second steps, a drift window of one step before and after. A code is accepted
only for a step greater than `last_used_step`, so a code is single use; the factor row is
locked (`SELECT ... FOR UPDATE`) while a code is checked, so two requests cannot both use
one step. Secrets are 20 random bytes (base32, 32 characters). No SMS, no network.

### D4. Enrolment, encryption and key rotation

- `POST /auth/mfa/totp/enroll` returns the secret and an `otpauth://` URI once and stores
  a **pending** secret. `POST /auth/mfa/totp/confirm` with a code from that secret makes it
  the confirmed factor and returns recovery codes. The confirmed factor keeps working until
  a re-enrolment is confirmed.
- A user with no factor may enrol only within `MFA_ENROLLMENT_FRESH_LOGIN_MINUTES`
  (default 15) of the login (`403 mfa_reauthentication_required`), so an old, possibly
  stolen session cannot attach its own factor. A user with a factor needs a verified
  session to enrol again (`403 mfa_required`). A pending secret expires after 15 minutes.
- Secrets are stored only as AES-256-GCM ciphertext under `MFA_SECRET_KEYS` (the
  `PII_MAPPING_KEYS` format, `key_id:base64(32 bytes)`, comma separated), with the user id
  as associated data: a ciphertext copied to another user's row does not decrypt. The
  secret never appears in logs or audit details.
- Rotation: add a new key to `MFA_SECRET_KEYS` and point `MFA_SECRET_ACTIVE_KEY_ID` at it.
  New enrolments use the new key; each row keeps its `encryption_key_id`, so older factors
  still decrypt while their key stays in the keyring. Removing a key makes the factors it
  encrypted unusable: TOTP verification answers `503 mfa_unavailable`, recovery codes still
  work, and the operator runs `mfa-reset` for those users.
- Without a keyring nobody can enrol (`503 mfa_unavailable`), so privileged users stay
  refused. This holds in **every** environment, not only production: a development or
  staging deploy without `MFA_SECRET_KEYS` and with the gate on refuses its owners, admins
  and operators until a keyring is set (or `PRIVILEGED_MFA_REQUIRED=false`). Startup does not
  fail on an empty keyring; it logs `PRIVILEGED_MFA_KEYRING_MISSING` in every environment
  except `test`. It fails on a keyring that does not parse or that does not hold the active
  key id.
- Codes typed with Persian or Arabic-Indic digits are accepted (`normalize_digits`, as on
  the OTP route), for TOTP and for recovery codes.

### D5. Recovery codes and rate limits

Ten codes per set, 10 characters of the base32 alphabet, shown as `xxxxx-xxxxx`. Input is
case-insensitive and ignores hyphens and spaces. Only a peppered digest is stored; a code is
used by a conditional update (`used_at IS NULL`), so it is single use under concurrency. A
new set (`POST /auth/mfa/recovery-codes`, verified session only) replaces the old set.
Confirm and verify share one rate limit per user: 10 attempts per 15 minutes.

### D6. Cut-off, with its arithmetic

Operators act from the server host (D8). Each change applies on the next request, because
every request checks the user and the session again:

- **Disabled user** (`disable-user`): `users.is_active=false`, every session revoked. The
  next request with an access token, a refresh token or an API key answers `401`; login is
  refused. `enable-user` allows new logins; revoked sessions stay revoked. API keys are not
  revoked: they are refused while the user is disabled and work again after
  `enable-user`; an admin revokes a suspect key in the console.
- **Revoked sessions** (`revoke-sessions`): every session ends on its next request.
- **Group removed at the IdP**: ADR 0066, within 15 minutes; the per-request membership
  check applies the removal at once.
- **Lost factor** (`mfa-reset`): the factor and the recovery codes are deleted and every
  session is revoked; the user enrols again after a new login.

### D7. Break-glass

- A break-glass account is a local account with `users.is_break_glass=true`. It is an
  ordinary owner by its memberships, so it also needs MFA; its TOTP device and recovery
  codes are kept offline with its password.
- `BREAK_GLASS_ENABLED` is false by default. With it false, nothing can be designated or
  opened, and every open window counts as closed. Undesignating and closing work with it
  off, so an operator can always clean up.
- **Designate** needs a password on the account and no unrevoked OIDC identity (a
  federated account depends on the IdP that break-glass replaces). It revokes every session.
- **Open** a window from the host: a reason is required, `1 <= minutes <=
  BREAK_GLASS_MAX_WINDOW_MINUTES` (default 60, at most 240), one open window at a time.
- **Login**: password only, and only while a window is open. Outside a window the answer is
  exactly a wrong password (`401 invalid_credentials`). OTP and Google logins of the account
  are always refused the same way. A session made in a window remembers it
  (`login_sessions.break_glass_window_id`) and expires no later than the window.
- **Every request and every refresh** of the account checks that its window is still open.
  Expiry, `close` and turning the setting off end access at once.
- **API keys** (SPEC revision 5): a break-glass account never uses API keys. A key bound to
  it is refused on every request (`401 api_key_invalid`), inside or outside a window, and
  creating one while signed in as it is refused (`403 permission_denied`). Old keys stay in
  the database and work again only if the account is undesignated. Why: break-glass is
  interactive emergency use, and a key would outlive the window.

### D8. Operator CLI

`python -m backend.operator_access` runs on the server host, against the API's database:
`disable-user`, `enable-user`, `revoke-sessions`, `mfa-reset`, and `break-glass designate |
undesignate | open | close | status`. The actor in the audit events is the login name passed
as `LOGNAME` (the runbook passes the operator's host login), else the process user. Exit 0
with the result on stdout (the application's log output goes to stderr); exit 2 with the
refusal code on stderr. There is no HTTP route for these actions.

### D9. Alerting and audit

- Audit (all `security`): `auth.mfa_totp_enrolled`, `auth.mfa_verified`,
  `auth.mfa_recovery_code_used`, `auth.mfa_verify_failed`,
  `auth.mfa_recovery_codes_regenerated`, `auth.user_disabled`, `auth.user_enabled`,
  `auth.sessions_revoked`, `auth.mfa_reset`, `auth.break_glass_designated`,
  `auth.break_glass_undesignated`, `auth.break_glass_opened`, `auth.break_glass_closed`,
  `auth.break_glass_login`, `auth.break_glass_login_refused`.
- Break-glass events (`window_opened`, `window_closed`, `login`, `login_refused`) send one
  email per address in `BREAK_GLASS_ALERT_EMAILS` and count
  `homayar_break_glass_events_total{event}`; the alert `HomayarBreakGlassUsed` (critical)
  pages on any increase in 10 minutes. The email seam never raises, and every caller also
  guards its call, so a failing mail server never blocks opening or closing a window or a
  login.

### D10. API keys and MFA

API keys are not sessions and cannot answer a second factor, so the gate does not apply to
them. They stay bounded: platform routes refuse keys; creating a key is an owner/admin route
and so needs an MFA-verified session; a disabled user's keys are refused; a break-glass
account's keys are refused (D7).

## Release note (operators)

1. **Keep the gate off until the console has enrolment screens.** The code default is
   `PRIVILEGED_MFA_REQUIRED=true`, so set `PRIVILEGED_MFA_REQUIRED=false` on every
   deployment (production, staging, development, CI end-to-end) before this release, and
   keep it false until the console enrolment and verification screens ship. Without them a
   privileged user would have to enrol through the API.
2. When the screens ship, generate an MFA key and set both variables (keep the key in the
   secret manager, never in the repository):

   ```sh
   python3 -c "import base64, os; print('k1:' + base64.b64encode(os.urandom(32)).decode())"
   # MFA_SECRET_KEYS=k1:<the 44 base64 characters printed>
   # MFA_SECRET_ACTIVE_KEY_ID=k1
   ```

3. Then set `PRIVILEGED_MFA_REQUIRED=true`, recreate the API container, and have every owner,
   admin and platform operator enrol at once: privileged routes answer
   `403 mfa_enrollment_required` until they do, existing sessions start unverified, and
   enrolment works within 15 minutes of a fresh login (see the pre-enrolment risk below).
4. Break-glass stays off until `BREAK_GLASS_ENABLED=true` is set; see
   `docs/operations/break-glass.md`.

## Consequences

- One migration adds `user_mfa_totp`, `mfa_recovery_codes`, `break_glass_windows`,
  `users.is_break_glass` and three `login_sessions` columns. The three tables are
  user-scoped authentication data without a tenant policy, like `login_sessions`.
- Owners, admins and operators have one more step after every login (and again after
  `PRIVILEGED_MFA_MAX_AGE_MINUTES`).
- Older test suites run with the gate off (the shared settings fixture); the HOM-129 tests
  turn it on.

## Known gaps

- **Pre-enrolment risk.** Until a privileged user has enrolled, the password plus the
  fresh-login window (`MFA_ENROLLMENT_FRESH_LOGIN_MINUTES`) is enough to enrol a factor: an
  attacker who has the password and logs in first can attach their own authenticator, and
  the real user then gets `403 mfa_required`. The window keeps an old or stolen session from
  doing it, but not a fresh login. Mitigation: enrol every privileged account right after
  the release; an operator can undo a wrong enrolment with `mfa-reset`.
- The console has no enrolment or verification screens yet; until the follow-up lands, a
  privileged user enrols through the API. This must land before the gate is turned on (see
  the release note).
- **Break-glass leftovers.** Share links and webhook endpoints that a break-glass account
  creates during a window outlive the window: closing it ends the sessions, not what the
  account made. The runbook's "after use" step reviews them and revokes what is not needed.
- There is no admin HTTP route to disable a user; the host CLI is the only way.
- HOM-127 (OIDC) may accept the IdP's `amr` claim as the second factor instead of TOTP.
- The CLI runs in its own process, so its window events alert by email and audit; the API
  metric does not see them (logins through the API are counted).
- There is no tool to re-encrypt factors under a new key; a removed key needs `mfa-reset`
  for the users it covered.
