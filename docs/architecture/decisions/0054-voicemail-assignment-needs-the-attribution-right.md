# ADR 0054: A voicemail's assignment needs the attribution right

Date: 2026-11-05 · Status: Proposed (HOM-369) · Builds on: ADR 0052

All code paths are under `Homayar/backend/`.

## Context

HOM-360 (ADR 0052) made a call's `team_id` / `agent_user_id` change its own right,
`meeting:attribute`, because the review queue decides authority from those values.
`PATCH /api/v1/voicemails/{id}` kept the older shape: it called the two attribution
validators directly and wrote `assigned_team_id` / `assigned_user_id` for any holder
of `meeting:update`, even when the value did not change. Clearing either field to
`null` was not checked by role at all, and a flat-workspace member could set non-null
values. The assignment decides who works a voicemail's follow-up, so this was an
assignment change without a permission check.

## Decision

The voicemail route now follows ADR 0052, with full parity:

- **V1.** A change of `assigned_team_id` or `assigned_user_id` needs
  `meeting:attribute` (owner, admin). A supervisor assignment and `record_scope` never
  grant it, and every API-key caller is refused first, whatever its scopes. Refusal:
  `403 attribution_change_denied`.
- **V2.** A change is a field in the body whose value differs from the voicemail's
  current value. Clearing to `null` is a change when the current value is not `null`.
- **V3.** An unchanged value needs no right, is not validated again, is not written,
  does not touch the follow-up action's assignee and is not listed in the audit
  `fields`. If nothing else changed, the answer stays the existing `422 no_changes`.
- **V4.** Nobody changes the assignment of a voicemail whose `assigned_user_id` is the
  caller, and nobody sets `assigned_user_id` to themselves. This holds for owners and
  admins too: `403 attribution_own_call`.
- **V5.** Order of checks: voicemail lookup with the record predicate (`404`), then
  the changed fields, then the guard (V1, then V4), then the existing validators for
  changed fields only (`422 invalid_team`, `422 invalid_agent`), then the
  `follow_up_state` handling. Every 403 / 422 is raised before any field is written.
- **V6.** A refusal writes nothing: no voicemail field (also not `follow_up_state` or
  `follow_up_done_at`), no follow-up action change, no audit event.
- **V7.** An accepted change writes `voicemail.assignment_changed` (category `access`)
  in the same transaction as `voicemail.updated`, with ids only:
  `assigned_team_id_before`, `assigned_team_id_after`, `assigned_user_id_before`,
  `assigned_user_id_after` (always all four) and `follow_up_action_id` (the voicemail's
  follow-up action, or `null`).

The guard is `authorize_attribution_change` in `services/team_service.py`, shared with
the meetings route; a `record` keyword only selects its message text, and the meeting
messages are unchanged. An accepted change of `assigned_user_id` still sets the linked
follow-up action's assignee.

## Consequences

- Only an owner or admin changes or clears a voicemail's assignment, and never on a
  voicemail assigned to them, and never to themselves.
- A client that sends back unchanged assignment values keeps working (V3); one that
  changed them with a member's session or with any API key now gets
  `403 attribution_change_denied`.
- Every assignment change is visible in the audit log.

## Not built

- The meetings and conversations routes do not change (ADR 0052 keeps its exact
  behaviour, status codes and messages).
- No console screen changes a voicemail's assignment.
- No migration and no schema change.
