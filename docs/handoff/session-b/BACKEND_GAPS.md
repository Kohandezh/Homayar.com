# Session B → Session A / integration: backend and deployment gaps

Each item names the blocked user action, the contract observed at product `d1cc5d4`, the minimum
behaviour needed, a **proposed** request/response (not accepted, not implemented by Session B),
and an acceptance test. Session B did not modify backend code, migrations, Python dependencies,
CI, Compose, nginx or the source lock.

## G1 — Sign-in method discovery

- **Blocked action:** the console cannot know whether Google or phone (Asanak) sign-in works, so
  it cannot honestly show or hide those entry points at runtime.
- **Observed:** `GET /auth/google/start` returns `404 google_auth_unavailable` JSON on a full-page
  navigation; `POST /auth/otp/request` raises `500 internal_error` when `ENABLE_PHONE_AUTH` is
  false or Asanak is unconfigured. Both default to disabled.
- **Interim console behaviour (Session B):** entry points are hidden unless the console is built
  with `VITE_ENABLE_GOOGLE_AUTH=true` / `VITE_ENABLE_PHONE_AUTH=true`, mirroring the backend
  `ENABLE_GOOGLE_AUTH` / `ENABLE_PHONE_AUTH` settings. Direct visits to `/app/phone` still work and
  an unexpected server error is shown as "phone sign-in is not available".
- **Minimum behaviour:** a public, cacheable read of enabled methods that is true only when the
  method is both enabled and configured.
- **Proposed:** `GET /api/v1/auth/methods` → `200 {"password": true, "google": false, "phone_otp": false}`.
  `POST /auth/otp/request` while unavailable → `503 {"error": {"code": "phone_auth_unavailable"}}`.
- **Acceptance:** with defaults, `/auth/methods` reports only `password`; enabling Google without
  client credentials still reports `google: false`; the console renders no Google/phone button.

## G2 — Resend the verification email

- **Blocked action:** a registered but unverified user (email lost, expired after 24 h, or the
  delivery failure that `/register` only logs) cannot upload (`403 verified_identity_required`)
  and has no way to request a new link.
- **Observed:** only `/register` issues the verification token; no resend route.
- **Proposed:** `POST /api/v1/auth/verify-email/resend` (session + CSRF, rate-limited, idempotent
  within a cooldown) → `202 {"status": "accepted", "resend_after": 60}`; `429 verification_cooldown`
  with `Retry-After`.
- **Acceptance:** an unverified session can request a link once per cooldown; a verified user gets
  202 without a new token; the old token is superseded.

## G3 — Upload policy read

- **Blocked action:** the console cannot show the deployment's real size/duration limits before
  a user uploads a large file; it only knows the product defaults.
- **Observed:** `ALLOWED_EXTENSIONS`, `MAX_UPLOAD_SIZE_MB`, `MAX_AUDIO_DURATION_SECONDS` are
  settings without a read API. The console keeps the default extension list for the file picker
  and relies on the server's 413/422 codes for everything else (it does not print numeric limits).
- **Proposed:** `GET /api/v1/uploads/policy` (`meeting:read`) →
  `{"allowed_extensions": [".wav", …], "max_upload_bytes": 262144000, "max_duration_seconds": 14400}`.
- **Acceptance:** values change with the settings; the console's picker and hints follow them.

## G4 — Effective permissions per workspace

- **Blocked action:** affordances (delete, replay, reveal original, usage) are derived from the
  role name using the product authorization matrix; a future matrix change would desynchronise
  the UI (the server still enforces).
- **Proposed:** add `permissions: string[]` to each `SessionResponse.workspaces[]` item, e.g.
  `{"id": "…", "role": "owner", "permissions": ["meeting:read", "pii:original_access", …]}`.
- **Acceptance:** the list equals `permissions_for_role(role)` and is pinned by the matrix test.

## G5 — Trial/usage visibility for members and viewers

- **Blocked action:** a member who uploads cannot see how much of the workspace allowance remains
  before uploading (`GET /billing/usage` needs `billing:read`, owner/admin only). Members only see
  the snapshot returned by their own upload.
- **Proposed (owner decision needed):** a narrower `GET /api/v1/usage/allowance`
  (`meeting:create`) returning only `audio_seconds` `{limit, used, reserved, remaining}`.
- **Acceptance:** a member reads the allowance; a viewer gets 403; no plan/billing data leaks.

## G6 — Cancel processing

- **Observed:** no cancel route; meeting status `cancelled` exists but cannot be reached from the
  console. The console exposes no cancel control.
- **Proposed:** `POST /api/v1/meetings/{id}/job/cancel` (`meeting:update` + CSRF) →
  `202 {job}`; releases the reservation; `409 job_not_cancellable` for terminal jobs.

## G7 — Phone OTP failure classification

- See G1: a disabled/unconfigured provider must not surface as `500 internal_error`.

## G8 — Product source availability (integration, not backend code)

- **Observed:** `infra/product-source.lock` on website `main` (`cfcc2c4`) pins
  `ref=feat/commercial-v1-overlay sha=d1cc5d46…`, but `https://github.com/Kohandezh/HomaYar`
  has only `dev`, `main`, `master` (checked 2026-09-24 with `git ls-remote`); `d1cc5d4` exists only
  in the local clone `.claude/worktrees/homayar-commercial-v1-a266f5/Homayar`. A CI run in `ci`
  mode cannot fetch the pinned product commit.
- **Needed:** push the product overlay branch (owner action), then re-verify the lock.

## Deployment notes for the integration pass

- The website links to the console at `NEXT_PUBLIC_CONSOLE_URL` (default `/app`, the same-origin
  nginx route). A website-only deployment without the `/app/` proxy (for example the README's
  Vercel path) must set it to the console origin or the links 404.
- Console build args to add in `infra/docker/console-web.Dockerfile` when the backend enables the
  providers: `VITE_ENABLE_GOOGLE_AUTH`, `VITE_ENABLE_PHONE_AUTH` (until G1 exists).
