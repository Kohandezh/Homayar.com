# ADR 0006 — Share contract: audiences, separate grants, re-checked authority

Date: 2026-09-08 · Status: accepted · Package: WP03-T06

## Context

Share links granted a meeting's result to anyone with the token, or to "any
signed-in account" when `require_authentication` was set; neither expressed
who the recipient is, the issuer's authority was never re-checked, and the
original audio was reachable only through `meeting:read`. The blueprint asks
contact-center sharing to default to disabled or named recipients, and the
master prompt requires that a share grant only its resource and operations,
never implicit membership, with revocation semantics that are stated truthfully.

## Decision

1. **Audience is explicit.** `link` (anyone with the link, only while the
   workspace's `public_share_links_enabled` is on — default off), `workspace`
   (current effective members of the source workspace, subject to team record
   scope), `recipients` (named emails, bound to a user id once a signed-in
   account with that **verified** email opens the link). No "any authenticated
   user" audience remains; legacy `require_authentication=true` rows were
   migrated to `workspace`, legacy anonymous rows stay `link` and wait for the
   workspace opt-in. Both changes are stricter, none more permissive.
2. **Operations are separate grants.** Viewing the result is implied;
   `allow_export` and `allow_download` (original audio) are opt-in per share,
   and audio is never grantable to anonymous links.
3. **Authority is re-evaluated on every request.** Expiry, revocation, the
   workspace kill switch, meeting deletion, the caller's membership/record
   scope, recipient binding and the issuer's own current authority
   (`share:manage` membership and, if record-scoped, visibility of the meeting)
   are all checked per access. A share whose issuer lost authority is suspended
   for everyone. Anonymous callers always receive `404 share_not_found` so
   tokens cannot probe state.
4. **Downloads re-check at retrieval time where we control delivery.** Local
   storage streams in bounded chunks with `Range`/`206` support and every range
   request re-authorizes; the response says `X-Homayar-Revocation: immediate`.
   S3-compatible storage redirects to a presigned URL for exactly one object
   with TTL ≤ 300 s and says `expiry-bounded`; an already-issued URL survives
   revocation until it expires. We do not claim immediate revocation there.
5. **Database ceiling.** Under row-level security a presented share token
   exposes only the shared meeting's content, the share's own row and the
   issuer's membership/assignments; it can write only the share's counters and
   an audit row. Tenant context is bound only for the `workspace` audience
   after the caller's membership was validated.

## Consequences

- Existing consoles that send `require_authentication` keep working; the field
  maps to an audience and is echoed back. New fields (`audience`,
  `allow_download`, `allow_export`, `recipients`) are additive.
- Organization-level audiences do not exist because there is no
  organization-level membership (WP03-T03 gap); "organization" sharing is the
  workspace audience until that carrier exists. Phone-number recipients and
  editable recipient lists are not implemented (revoke and re-issue instead).
- Every role that reads `meetings` needs SELECT on `share_links` because the
  policy consults it; `homayar_app` has it by construction.
- Migration `d7f3a1c9e2b4` is additive plus a backfill; downgrade drops
  recipients and audience information.
