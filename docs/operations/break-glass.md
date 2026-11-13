# Break-glass access and account cut-off

Decision: [ADR 0076](../architecture/decisions/0076-privileged-mfa-and-break-glass.md) (HOM-129).
Alert: [HomayarBreakGlassUsed](alerts.md#homayarbreakglassused).

Every command here runs **on the server host**, in the API container, against the API's
database. There is no HTTP route for any of them. The audit events record as `actor` the
login name passed as `LOGNAME`; the command below passes your host login (without it the
actor would be the container user `app`).

```sh
docker compose --file infra/compose/compose.onprem.yaml --profile onprem \
  exec -e LOGNAME="$(id -un)" api python -m backend.operator_access <command>
```

Each command prints one line and exits 0, or prints its refusal code on stderr and exits 2.

## Before the first release with HOM-129

1. Generate the MFA key and put both values in the secret manager (never in the repository):

   ```sh
   python3 -c "import base64, os; print('k1:' + base64.b64encode(os.urandom(32)).decode())"
   ```

   Set `MFA_SECRET_KEYS=k1:<the printed base64>` and `MFA_SECRET_ACTIVE_KEY_ID=k1`. Both
   compose files pass them to the containers.
2. After the release every owner, admin and platform operator must enrol a TOTP app. Until
   then privileged routes answer `403 mfa_enrollment_required`. Enrolment works within 15
   minutes of a fresh login. Keep the 10 recovery codes offline.
3. To add a new key later: append it (`k1:...,k2:...`), set the active id to `k2`, then
   recreate the API container (see "Set up" below). Keep `k1` in the list while any user
   enrolled under it.

## Cut-off

| Need | Command | Effect |
| --- | --- | --- |
| Lock an account out | `disable-user --email E --reason R` | Every token, refresh token and API key refused on the next request; login refused. |
| Let it back in | `enable-user --email E` | New logins work. Old sessions stay revoked. Its API keys work again. |
| End every session | `revoke-sessions --email E --reason R` | Every session ends on its next request; prints how many. |
| Lost authenticator | `mfa-reset --email E --reason R` | Factor and recovery codes deleted, sessions revoked. The user signs in again and enrols within 15 minutes. |

A group removed at the identity provider follows ADR 0066 (within 15 minutes).

## Break-glass

A break-glass account is a local password account that signs in only inside a window you
open. It is an owner by its memberships, so it also needs MFA: enrol it once, and keep its
password, its TOTP device (or seed) and its recovery codes together in the offline safe.

### Set up (once)

1. Set `BREAK_GLASS_ENABLED=true` and `BREAK_GLASS_ALERT_EMAILS` (comma-separated), then
   recreate the API container so it reads the new environment (a `restart` keeps the old
   one):

   ```sh
   docker compose --file infra/compose/compose.onprem.yaml --profile onprem up -d --no-deps api
   ```

   With the setting false nothing can be designated or opened, and every open window counts
   as closed. Turning it off later works the same way and ends every open window at once.
2. Create a local owner account with a strong password (no Google or OIDC sign-in on it).
3. Sign in with it and enrol MFA (`/api/v1/auth/mfa/totp/enroll`, then `/confirm`) while it
   is still an ordinary account.
4. `break-glass designate --email E`. Its sessions end; from now on it signs in only inside
   a window, only by password. It never uses API keys: any key it has is refused, and it
   cannot create new ones.
5. `break-glass status` lists designated accounts and open windows.

### Use

1. `break-glass open --email E --minutes M --reason "why"` (1 to
   `BREAK_GLASS_MAX_WINDOW_MINUTES`, default 60). The alert email goes out and the audit
   event `auth.break_glass_opened` is written.
2. Sign in with the password, then verify MFA (`/api/v1/auth/mfa/verify`, TOTP or a recovery
   code). The session ends no later than the window.
3. Do only the emergency work.
4. `break-glass close --email E` as soon as you are done. Every session of the account ends
   at once. An expired window ends access the same way.

### After use

1. Rotate the password of the break-glass account.
2. If a recovery code was used, or the TOTP device left the safe: run `mfa-reset`, open a
   short window, sign in and enrol again, then close the window.
3. Review the `auth.break_glass_*` audit events and file the incident note.

### Retire an account

`break-glass undesignate --email E` (allowed with the setting off). It closes an open window,
ends every session, and makes the account an ordinary owner again.

## What alerts

- Email to `BREAK_GLASS_ALERT_EMAILS` on `window_opened`, `window_closed`, `login` and
  `login_refused` (a correct password outside a window, or any OTP or Google login of the
  account; a wrong password counts only in `homayar_auth_failures_total`). A failing mail
  server never blocks the action; it logs `BREAK_GLASS_ALERT_FAILED`.
- `HomayarBreakGlassUsed` (critical) sees API logins only (`login`, `login_refused`). Window
  open and close arrive by email and audit, because the CLI runs in its own process, which
  Prometheus does not scrape.
- Audit events: `auth.break_glass_designated`, `auth.break_glass_undesignated`,
  `auth.break_glass_opened`, `auth.break_glass_closed`, `auth.break_glass_login`,
  `auth.break_glass_login_refused`, and for cut-off `auth.user_disabled`,
  `auth.user_enabled`, `auth.sessions_revoked`, `auth.mfa_reset`.
