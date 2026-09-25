# ADR 0032 — Console-facing contract additions from the Session C integration

Date: 2026-09-25 · Status: accepted · Packages: WP03 (identity), WP07 (privacy views), WP12-T03 (playback path) · Product: `7be3abe` and later

## Context

Session B built the console journey against product `d1cc5d4` and recorded the
backend gaps G1–G11 (`docs/handoff/session-b/BACKEND_GAPS.md`, proposals, not
accepted contracts). Session C resolved the ones that block the supported
journey or misstate security or privacy. Every change is additive: no route,
field or code changes meaning.

## Decision

1. **Sign-in method discovery (G1, G7).** `GET /api/v1/auth/methods` (public)
   answers `{"password": true, "google": bool, "phone_otp": bool}`; a method is
   `true` only when enabled *and* its provider settings are present. It says
   nothing about the provider being reachable. The console offers Google and
   phone sign-in from this answer only (no build flags). `POST /auth/otp/request`
   answers `503 phone_auth_unavailable` when phone sign-in is disabled or
   Asanak is not configured and `503 otp_delivery_failed` (Retry-After 30)
   when the provider refuses the message; nothing is committed in either case.
2. **Verification resend (G2).** `POST /api/v1/auth/verify-email/resend`
   (session + CSRF) issues a new link that supersedes older ones: `202
   {"status": "accepted", "resend_after": 60}`; `202 already_verified` for a
   verified account (no token); `429 verification_cooldown` with Retry-After
   (one per 60 s, five per hour per user); `409 email_not_set` for a phone-only
   account. Audited as `auth.verification_resent`. Delivery goes through the
   standard-library SMTP sender (the previous one imported a package that is
   not a dependency, so no e-mail could be delivered); a delivery failure is
   logged without the address and is not shown to the caller, as at
   registration.
3. **Selectable workspaces (G9).** `/auth/me`, login, refresh and OTP list
   exactly the workspaces `require_workspace` accepts, through one predicate
   (`selectable_workspace_clause`: effective membership window and an active
   workspace).
4. **The view of every content response (G10).** Summary, each action item and
   the evidence list carry `view`, `privacy_enforced` and, for a redacted view,
   `redaction {detector_version, classes, policy_digest}`. The console labels
   each tab from its own response; the transcript notice claims only the
   transcript. `analysis_run.input_view` separately states which text the
   model was given.
5. **Workspace-scoped playback (G11).** `GET
   /api/v1/workspaces/{workspace_id}/meetings/{meeting_id}/recording`
   (`meeting:read`, route inventory updated): the session cookie
   authenticates (a native `<audio>` element sends it; it cannot send
   `X-Workspace-ID`), the path only selects among the caller's current
   memberships exactly as the header does, and every request — each Range
   request — re-checks membership, permission, record scope and the
   tombstone. `Content-Disposition: inline`, Range/206/416 as for downloads,
   one chunk of memory. No credential appears in the URL; no signed URL is
   issued. With presigning storage the redirect stays object-scoped and
   expiry-bounded, as for downloads.

## Consequences

- A removed member, an expired membership, a signed-out session or a deleted
  meeting stops playback on the next Range request; access already streamed
  is not recalled.
- The access cookie lives 15 minutes; a long playback continues after the
  console refreshes the session, and otherwise stops with 401.
- Gaps G3 (upload-policy read), G4 (permission list), G5 (member allowance
  read) and G6 (cancel) remain proposals; G8 (product source availability) is
  resolved by publication.
