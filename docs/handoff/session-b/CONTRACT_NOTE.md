# Session B contract note — product API consumed by the console and website

Verified against the **committed** product source at
`d1cc5d464c6ef51620dd445bd86726ef9280e473` (WP07-T07, the commit pinned by
`infra/product-source.lock` on website `cfcc2c4`). Sources read: `backend/routers/v1/*.py`,
`backend/schemas/*.py`, `backend/security/context.py`, `backend/main.py`,
`backend/config.py`, `backend/services/{auth,otp,tenancy,ingestion,upload}_service.py`,
`backend/media/validation.py`, product `docs/security/authorization-matrix.md`.
Session A's uncommitted work and the later local commit `42543aa` (WP08-T01, backend only)
were **not** used.

## Transport

- Same origin in the Compose/nginx deployment (`infra/nginx/conf.d/homayar.conf`): website `/`,
  console SPA `/app/` (Vite `--base=/app/`), API `/api/v1/`. The website therefore links to the
  console with plain `<a href="/app/...">` (not Next `<Link>`: `/app` is not a Next route).
- Cookies `homayar_access` (HttpOnly, path `/`), `homayar_refresh` (HttpOnly, path
  `/api/v1/auth`), `homayar_csrf` (readable, path `/`); SameSite=Lax; `Secure`/`Domain` from
  backend settings. Console sends `X-CSRF-Token` on non-GET and `X-Workspace-ID` on non-auth calls.
- Error body: `{"error": {"code", "message", "request_id", "details?"}}`; 422 validation →
  `details: [{field, message}]`. `Retry-After` header on 429/503 where noted.
- Unhandled backend exceptions → `500 internal_error` (no code distinguishes "provider not
  configured").

## Authentication (`/api/v1/auth`)

| Method + path | Auth | Request fields used | Response / states |
| --- | --- | --- | --- |
| POST `/register` | public, 5/h/IP | `display_name`, `email`, `password`, `locale` | 202 `{status:"pending_verification"}`; 409 `account_exists`; 422; 429 `rate_limited` + Retry-After. Creates a personal organization + workspace (owner) and, when `ENABLE_FREE_DEMO` (default true), a `trialing` demo subscription with `audio_seconds` limit `DEMO_QUOTA_SECONDS` (default 1800) and `concurrent_jobs` 1. Email delivery failure is logged, not surfaced. |
| POST `/login` | public, 20/15 min/IP + 10/15 min/identifier | `identifier`, `password` | `SessionResponse`; 401 `invalid_credentials` (also disabled accounts); 429 `rate_limited`; 503 `rate_limiter_unavailable`. Unverified users **can** log in (`requires_verification: true`). |
| POST `/refresh` | refresh cookie + CSRF | — | `SessionResponse`; 401 `invalid_refresh` (replay revokes the family). |
| POST `/logout` | CSRF | — | 200; 403 `csrf_failed`. |
| GET `/me` | session | — | `SessionResponse {user, workspaces[{id, organization_id, name, role}], access_expires_in, requires_verification}`; 401 `authentication_required`. |
| POST `/verify-email` | public | `token` | 200 `verified`; 400 `invalid_verification_token`. |
| POST `/forgot-password` | public, 10/h/IP | `email` | always 202 (non-enumerable). |
| POST `/reset-password` | public | `token`, `password` | 200; 400 `invalid_reset_token`; revokes all sessions. |
| POST `/otp/request` | public, 10/h/IP | `phone` | 202 `{challenge_id, expires_in (180), resend_after (60)}`; 400 `invalid_phone`; 429 `otp_cooldown` + Retry-After; **500 `internal_error` when phone auth is disabled or Asanak is unconfigured** (RuntimeError). Persian digits accepted in the code only. |
| POST `/otp/verify` | public | `challenge_id`, `code` | `SessionResponse`; 400 `invalid_otp` (wrong/expired/exhausted/superseded); 429. |
| GET `/google/start` | public, browser navigation | — | 302 to Google; **404 JSON `google_auth_unavailable` when unconfigured** (the browser lands on raw JSON). |
| GET `/google/callback` | public | — | success: 302 to `PUBLIC_URL + redirect_path`; failure: 302 to `FRONTEND_URL/login?error=account_link_required` or `?error=google_auth_failed`. |

Password rules: product schema `RegisterRequest` (backend validation; console relies on 422 messages).

## Workspace resolution

- `X-Workspace-ID` only selects among the caller's effective memberships; missing header → the
  oldest membership. Unknown/removed/expired membership or inactive workspace →
  `403 workspace_access_denied`. Permission missing → `403 permission_denied`.
- No endpoint returns the caller's permission list; the console derives affordances from the
  workspace `role` using the machine-checked matrix (owner/admin/member/viewer). Every action
  still handles 403 from the server.

## Upload and processing

| Method + path | Permission | Fields used | States / errors |
| --- | --- | --- | --- |
| POST `/uploads` (multipart) | `meeting:create` + CSRF; verified email **or** phone | `file`, `title?` (≤240), `language` (default `fa`), header `Idempotency-Key` | 202 `UploadAcceptedResponse {meeting, recording, job, usage{metric,limit,used,reserved,remaining}, idempotent_replay, ingestion}`. Same key + same bytes → replay (no new job or usage). 400 `invalid_idempotency_key`; 403 `verified_identity_required`; 409 `ingestion_conflict` (same key, different payload), `duplicate_media` (workspace policy); 410 `ingestion_target_deleted`; 413 `upload_too_large` / `media_file_too_large` / `media_duration_exceeded`; 422 media refusals (`unsupported_extension`, `unsupported_mime_type`, `mime_type_mismatch`, `empty_media`, `empty_upload`, `missing_filename`, `unsafe_filename`, `malformed_media`, `unsupported_container`, `extension_content_mismatch`, `missing_audio_stream`, `invalid_audio_stream`, `invalid_media_duration`, `media_duration_implausible`, `media_decode_failed`, …); 429 `audio_quota_exceeded` / `concurrent_job_limit_reached` with `details{metric, limit, used, reserved, requested, remaining}`, `upload_rate_limited` + Retry-After; 503 `storage_unavailable`, `media_probe_unavailable`/`media_probe_timeout`/`media_decoder_unavailable`/`media_decode_timeout` + `Retry-After: 30` (identity stays free, same key may be retried). |
| GET `/meetings?limit&before&status` | `meeting:read` | — | `{items: MeetingResponse[], next_before}` |
| GET `/meetings/{id}` | `meeting:read` | — | `MeetingDetailResponse {…, recording, latest_job, quality, ingestion}`; 404 `meeting_not_found` (also deleted/tombstoned or outside record scope). |
| GET `/meetings/{id}/job` | `meeting:read` | — | `JobResponse {id, state, stage, attempt_count, max_attempts, failure_code, failure_message, stages[]}`; 404 `job_not_found`. |
| POST `/admin/jobs/{job_id}/replay` | `workspace:manage` + CSRF | — | 202 `{job, replay, max_replays, dispatch}`; 409 `job_not_replayable`/`replay_limit_reached`/`meeting_deleted`/`retention_expired`/`recording_missing`; 429 quota. Only for `dead_letter` jobs. |

Enumerations: meeting `status` ∈ uploading, uploaded, queued, processing, transcribing,
diarizing, analyzing, completed, partial, failed, cancelled. Job `state` ∈ queued, running,
completed, failed, cancelled, dead_letter. Stage `state` ∈ queued, running, completed, partial,
failed, unavailable, insufficient_evidence, skipped. No progress percentage exists in any
contract. No cancel endpoint exists.

Upload limits are **server settings with no read API**: `ALLOWED_EXTENSIONS` default
`.wav,.mp3,.ogg,.m4a,.aac,.flac,.mp4,.webm`, `MAX_UPLOAD_SIZE_MB` default 250,
`MAX_AUDIO_DURATION_SECONDS` default 14400, `UPLOAD_RATE_LIMIT_PER_HOUR` default 30.

## Results and privacy

| Method + path | Permission | Notes |
| --- | --- | --- |
| GET `/meetings/{id}/transcript?view=&revision_id=` | `meeting:read` | `{segments[{id, sequence, speaker_key, speaker_name, start_ms, end_ms, text, confidence}], speakers[], revision, view: "original"\|"redacted", privacy_enforced, redaction{id, classes, entity_count, mapping_retained, …}}`. Under an enforced policy the default is redacted; `view=original` needs `pii:original_access` (owner, session only) → otherwise `403 original_access_denied`; each original read is audited (`transcript.original_accessed`). |
| GET `/meetings/{id}/summary?view=` | `meeting:read` | 404 `summary_not_ready` when no summary exists. Same view rules. |
| GET `/meetings/{id}/actions?view=` | `meeting:read` | Same view rules. |
| GET `/meetings/{id}/quality` | `meeting:read` | Same object as `MeetingDetailResponse.quality`. |
| POST `/meetings/{id}/transcript/redactions/{redaction_id}/reveal` | `pii:original_access` + CSRF | `{placeholder: "[CLASS_n]", reason (3–200)}` → `{redaction_id, placeholder, value}`; 404 `mapping_not_retained`/`redaction_not_found`; 409 `mapping_key_unavailable`; audited `pii.revealed`. |
| GET `/meetings/{id}/recording`, `/export` | `meeting:read` | Streamed/blob download. |
| DELETE `/meetings/{id}` | `meeting:delete` + CSRF | 204; afterwards every read is 404 `meeting_not_found`. |

## Usage and trial

| Method + path | Permission | Notes |
| --- | --- | --- |
| GET `/billing/usage` | `billing:read` (owner, admin) | `{workspace_id, items[{metric, source, limit, used, reserved, remaining, period_start, period_end, updated_at}]}`. Metric `audio_seconds` is in **seconds**. Members/viewers get 403. |
| GET `/billing/subscription`, `/billing/plans` | `billing:read` | Plan `demo` = "Free Demo"; other plans have empty entitlements; no price fields, no payment flow. |
| Upload 202 body `usage` | — | The reservation snapshot after the upload (available to members too). |

The empty-transcript charging policy is **undecided** (backlog open decision); the console must
not describe whether an empty result consumes allowance.

## Known backend gaps (details and proposals in `BACKEND_GAPS.md`)

G1 no auth-method discovery (Google/phone availability) · G2 no resend-verification endpoint ·
G3 no upload-policy read API (formats/size/duration) · G4 no per-workspace permission list ·
G5 usage not readable by members/viewers · G6 no job cancel endpoint · G7 phone OTP returns 500
when unconfigured · G8 product commit `d1cc5d4` is not on the GitHub remote · G9 `/auth/me`
lists non-effective memberships.
