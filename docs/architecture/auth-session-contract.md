# Authentication and session-family contract (WP03-T01)

Status: accepted 2026-09-08. Single contract for the product API (`/api/v1/auth`);
the legacy `fastapi_users`/`python-jose` stack was removed from the codebase.
Source of truth: `backend/security/tokens.py`, `backend/security/context.py`,
`backend/services/auth_service.py`, `backend/services/otp_service.py`,
`backend/services/google_oauth_service.py`, `backend/routers/v1/auth.py`.
Every statement below is covered by a test in `backend/tests/test_api.py` unless
marked *(gap)*; gaps are WP03-T02 work.

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
