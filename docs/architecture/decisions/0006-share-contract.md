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
- Organization-level audiences, phone-number recipients and editable recipient
  lists were added on 2026-09-10 (amendment below); before that, "organization"
  sharing was the workspace audience and recipients were revoked and re-issued.
- Every role that reads `meetings` needs SELECT on `share_links` because the
  policy consults it; `homayar_app` has it by construction.
- Migration `d7f3a1c9e2b4` is additive plus a backfill; downgrade drops
  recipients and audience information.

## Amendment 2026-09-10 — organization audience, phone recipients, recipient editing (WP03-T06)

6. **`organization` audience.** A share whose audience is `organization` may be
   opened by a signed-in user who is *currently* a member of the organization
   the source workspace belongs to: an effective workspace membership in any
   active workspace of that organization, or an effective organization
   membership (ADR 0012), evaluated on every access. It is a specific grant of
   one meeting to that audience — nothing else of the workspace or tenant
   becomes reachable, and team record scope of the source workspace is not
   applied (like `recipients`, the issuer explicitly chose the audience and the
   issuer's own authority over the record is re-checked on every access). The
   workspace can switch the audience off (`organization_share_links_enabled`,
   default on); when off, such shares answer `404` for everyone, like `link`
   shares under the public-link kill switch. Leaving the organization (no
   effective membership left), membership expiry, a workspace moved to another
   organization (the audience follows the workspace's *current* organization),
   issuer demotion, meeting deletion, expiry and revocation all take effect on
   the next request; an already-issued S3 presigned URL still survives until
   it expires (decision 4 is unchanged).
7. **Phone recipients.** A recipient is either a verified email or an Iranian
   mobile number normalised by the existing rule (`normalize_iranian_mobile`,
   E.164). A phone recipient is matched only against an account whose phone
   was verified through the existing Asanak/OTP contract, and on first access
   the recipient row is bound to that account's stable user id; from then on
   only that user id matches. A later change or reassignment of the number
   neither transfers the grant to the new holder (the row is already bound)
   nor revokes it from the bound account; the issuer removes the recipient to
   do that. No second phone-authentication system exists.
8. **Recipient editing.** `PATCH /meetings/{id}/shares/{share_id}/recipients`
   (permission `share:manage`, CSRF, the caller must see the record) adds
   emails/phones and removes recipient rows by id on a `recipients` share.
   Edits are deltas applied under a row lock on the share, so concurrent
   editors cannot silently lose each other's changes; the total stays within
   the recipient limit; additions and removals are audited by count and row id
   (never by address or token). A removed recipient is refused on the next
   request, including range continuations of a streamed download; cached
   grants do not exist. Bounded presigned URLs behave as in decision 4.

Evidence (local scope, 2026-09-10): `backend/tests/test_share_organization.py`
(organization audience incl. sibling-workspace members, record-scoped members,
strangers, departure/expiry, kill switch, revocation; phone recipients incl.
reassignment; recipient editing incl. authority, audit shape and immediate
refusal; concurrent edits on PostgreSQL) with `test_share_policies.py`,
`test_row_level_security.py`, `test_authorization_matrix.py`, `test_admin_api.py`
and `test_organizations.py` on PostgreSQL 16.15 as `homayar_app` (31 passed) and
on SQLite; migration `b7d2f4a8c6e1` upgrade/check/downgrade/upgrade on a fresh
database. Not verified: S3 presigned downloads against a real bucket (unchanged),
the console, and share notification delivery (none exists — no SMS is sent for
shares).

