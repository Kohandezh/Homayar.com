# Authentication and session-family contract (WP03-T01)

Status: accepted 2026-09-08. Single contract for the product API (`/api/v1/auth`);
the legacy `fastapi_users`/`python-jose` stack was removed from the codebase.
Source of truth: `backend/security/tokens.py`, `backend/security/context.py`,
`backend/services/auth_service.py`, `backend/services/otp_service.py`,
`backend/services/google_oauth_service.py`, `backend/routers/v1/auth.py`.
Every statement below is covered by a test in `backend/tests/test_api.py` unless
marked *(gap)*; gaps are WP03-T02 work.

Authorization (what an authenticated session may do) is a separate contract:
the role × resource × operation × scope matrix lives in the product repository
at `docs/security/authorization-matrix.md` and is pinned by
`backend/tests/test_authorization_matrix.py` (WP03-T03, ADR 0004).

## Identities

| Method | Verified ownership before use | Storage |
|---|---|---|
| Email + password | Email verification token (single use, hashed) | Argon2 hash via `pwdlib`; `AuthIdentity` row |
| Phone + OTP (Asanak) | OTP challenge: 180 s TTL, 60 s resend cooldown, 5 attempts, single use, hashed | `OtpChallenge` row; codes never logged |
| Google OIDC | ID-token claims verified against Google; state/nonce challenge row (`OAuthChallenge`) | `AuthIdentity` linked only to the signed-in user (`/google/link/start`) |

Registration never accepts roles (`test_public_registration_forbids_role_mass_assignment`).
Login errors are generic; password-reset and OTP requests are not enumerable.

## Session family

- A login creates one `LoginSession` (id = session family). The refresh token is
  `<session_id>.<random>`; only `sha256(pepper, "refresh:" + token)` is stored.
- Access token: HS256 JWT, 15 min (`access_token_minutes`), claims
  `sub`, `sid`, `sv` (user `session_version`), `type=access`, `iss`, `aud`, `iat`,
  `exp`, `jti`; secret derived from `JWT_SECRET` (≥ 32 chars in production).
- Refresh: 30 days (`refresh_token_days`), rotated on every `/refresh` under a row
  lock. Presenting a refresh token whose hash does not match the current one is a
  **replay** and revokes the family (`RefreshTokenReplayError`).
- Every request resolving an actor checks: session exists, belongs to the user,
  not revoked, not expired, user active, `sv` equals `user.session_version`.
- Revocation: `/logout` revokes the session; password reset and account
  deactivation bump `session_version` and revoke all sessions
  (`revoke_all_user_sessions`).
- A break-glass account (`users.is_break_glass`, HOM-129) is also checked against its
  window on every request and refresh: the session serves only while the window named in
  `login_sessions.break_glass_window_id` is open.

## Privileged MFA (HOM-129, ADR 0076)

- Second factor: TOTP (RFC 6238, SHA-1, 6 digits, 30 s, +-1 step), offline, no SMS. The
  secret is AES-256-GCM under `MFA_SECRET_KEYS`, bound to the user id; recovery codes (10,
  single use) are stored as peppered digests.
- Routes (`/api/v1/auth/mfa`, signed-in session; POSTs need CSRF; API keys get
  `401 api_key_not_accepted`): `GET /mfa` (status), `POST /mfa/totp/enroll`,
  `POST /mfa/totp/confirm`, `POST /mfa/verify`, `POST /mfa/recovery-codes`. Confirm and
  verify share 10 attempts per 15 minutes per user.
- A verification marks the session (`mfa_verified_at`, `mfa_method`); a refresh keeps it, a
  new login starts without it. It counts for `PRIVILEGED_MFA_MAX_AGE_MINUTES` (720).
- The gate runs on every request of a workspace or organization owner/admin and of a
  platform operator: `403 mfa_enrollment_required` without a factor, `403 mfa_required` with
  one. The MFA routes, `/me`, `/refresh` and `/logout` are outside it. API keys are never
  gated (they cannot answer a factor).

## Cut-off (HOM-129, ADR 0076)

| Event | Takes effect |
|---|---|
| User disabled (`python -m backend.operator_access disable-user`) | next request: access token, refresh token and API key `401`; login refused |
| All sessions revoked (`revoke-sessions`) | next request of each session |
| MFA reset (`mfa-reset`) | factor and recovery codes gone, sessions revoked; enrol again after a new login |
| Group removed at the IdP | ADR 0066: within 15 minutes |
| Break-glass window expired or closed, or `BREAK_GLASS_ENABLED=false` | next request and refresh of the break-glass account |

A break-glass account signs in by password only, only inside an open window; any other
attempt answers like a wrong password (`401 invalid_credentials`). It never uses API keys.
Runbook: `docs/operations/break-glass.md`.

## Transport

- Cookies `homayar_access`, `homayar_refresh`, `homayar_csrf` (`HttpOnly` for the
  first two; `Secure` when `SECURE_COOKIES=true`, mandatory in production;
  optional `cookie_domain`). Bearer access tokens are accepted for API clients.
- CSRF: cookie-authenticated mutations must send `X-CSRF-Token` equal to the
  `homayar_csrf` cookie **and** matching the session's stored CSRF hash
  (`require_csrf`). Bearer requests are exempt.
- Rate limits: login, OTP request/verify and reset endpoints go through
  `RateLimiter` (memory in tests, Redis in deployments).

## One-time tokens

`OneTimeToken` rows (email verification, password reset): hashed, purpose-bound,
single use, expiring; consuming a reset token revokes all sessions.

## Negative coverage (WP03-T02)

`backend/tests/test_api.py` and `backend/tests/test_auth_contract.py` cover: refresh
rotation and replay revocation, logout with CSRF, password reset invalidating
sessions and single-use tokens, deactivated users, forged tokens, OTP
normalization/replay/cooldown/attempt limit, OTP code bound to its challenge with
older open challenges superseded on a new request, cookie attributes
(HttpOnly/SameSite/Path/Secure/Domain), Google link requiring an authenticated
CSRF session, Google sign-in refusing to merge into an existing email account, and
one Google identity never linking to two accounts. Live Google/Asanak delivery is
not exercised (blocked_external: provider configuration).

## Removed

`backend/auth/*`, `backend/middleware/auth.py`, `backend/routers/auth.py` and the
`ENABLE_LEGACY_API` composition flag. The remaining files under
`backend/routers/` that still import the removed middleware (`dashboard`, `graph`,
`copilot`, `ldap_sync`, `webhook`) are unwired legacy code kept only as reference
for WP15/WP17 and cannot be imported; `test_truthful_outputs.py` pins the removal.
