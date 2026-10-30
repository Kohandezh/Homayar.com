# ADR 0052: Call attribution is a separate right

Date: 2026-10-30 · Status: Proposed (HOM-360) · Builds on: ADR 0004, ADR 0013, ADR 0051

All code paths are under `Homayar/backend/`.

## Context

The supervisor review queue (ADR 0051, D4) decides who sees and works a review from the
call's **current** `team_id` and `agent_user_id`. Until HOM-360,
`PATCH /api/v1/meetings/{id}` (and its alias `PATCH /api/v1/conversations/{id}`) let any
holder of `meeting:update`, members included, change both. That opened four paths
(ADR 0051, "Closed gap"):

1. Anyone who could read a call could clear its `agent_user_id`. A null value was never
   checked. An agent who also supervises the call's team cleared themselves and then saw
   and worked the review of their own call.
2. A record-scoped supervisor could move a call into a team they supervise, and so gain
   review authority over it.
3. Under `record_scope = workspace`, any member could move any call to any active team,
   also before any review existed, and then create and work its review.
4. Under `record_scope = workspace`, a member could set `team_id` to null, so only admins
   saw the call's review.

HOM-74 adds criterion decisions to a review, so these paths had to close first. A lock
that holds only while a review is active does not close path 3.

## Decision

**R1. A new permission.** `meeting:attribute` (`Permission.MEETING_ATTRIBUTE`) changes a
call's team or agent. OWNER and ADMIN hold it; MEMBER and VIEWER do not. It is not in
`API_KEY_SCOPES`, so no key can carry it, and the handler also refuses every API-key
caller by itself (until HOM-361 a stored key scope is not intersected with
`API_KEY_SCOPES`). A supervisor assignment never grants it, and `record_scope` never
grants it.

**R2. A change needs the right.** A `team_id` or `agent_user_id` in the body that differs
from the call's current value is an attribution change; clearing to null is a change
when the current value is not null. Without `meeting:attribute` the answer is
`403 attribution_change_denied`, and no field of the request is written, also not
`title`. This holds with or without a review item and under any `record_scope`.

**R3. An unchanged value is not a change.** It needs no right, is not checked again, is
not written and is not listed in the audit `fields`. If nothing else changed, the answer
is the existing `422 no_changes`. A client that sends back the whole record when it edits
the title keeps working for members.

**R4. Never your own call.** Nobody changes the attribution of a call where they are the
current agent, or sets `agent_user_id` to themselves. This holds for owners and admins
too: `403 attribution_own_call`.

**R5. Order of checks.** The meeting lookup and record predicate (an invisible call stays
`404`), then R3, R2, R4, then the existing validators (`422 invalid_team`,
`422 invalid_agent`). Every check runs before any field is written. The guard is
`authorize_attribution_change` in `services/team_service.py`. The two validators
`validate_team_attribution` and `validate_agent_attribution` do not change: on the
meetings route they now run only for owners and admins, who are unrestricted.

**R6. Audit.** Every accepted change writes `meeting.attribution_changed` (target
`meeting`, category `access`) in the same transaction as `meeting.updated`. Its details
are ids only: `team_id_before`, `team_id_after`, `agent_user_id_before`,
`agent_user_id_after` (always all four) and `active_review_item_id` (the call's `open` or
`in_review` item, or null). The four `*_before` / `*_after` keys are exempt from
free-text masking like other id keys. A refused change writes no audit event.

## Consequences

- Supervisors and flat-workspace members can no longer re-attribute a call. Only an
  owner or admin moves a call or changes its agent, and never for their own call.
- ADR 0051 D4 does not change: review authority still follows the call's current team.
- A console or integration that changed a call's team or agent with a member's session
  or with any API key now gets `403 attribution_change_denied`. The product console sends
  only `title` today, so it is not affected. A client that sends back unchanged values
  keeps working (R3).
- The workspace owner and admins get one more permission each (owner 23, admin 19), shown
  in the session's permission list. The console ignores permissions it does not know.
- Every re-attribution is visible in the audit log, with the review item it moved.

## Not built

- Voicemail: `PATCH /api/v1/voicemails/{id}` uses the two validators for its own
  `assigned_team_id` / `assigned_user_id`. Those fields are not the call's attribution,
  and their rules do not change.
- No console screen changes a call's team or agent.
- No migration and no schema change.
