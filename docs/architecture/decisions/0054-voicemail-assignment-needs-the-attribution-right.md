# ADR 0054: A voicemail's assignment needs the attribution right

Date: 2026-11-05 · Status: Proposed (HOM-369; amended by HOM-373) · Builds on: ADR 0052

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

## Follow-up action assignee (HOM-373)

`PATCH /api/v1/meetings/{meeting_id}/actions/{action_id}` let any holder of
`meeting:update` write `assignee_user_id`. The pipeline copies the voicemail's assignee
into its follow-up action, so this route was a side door around the rule above.

Decision: the `assignee_user_id` of a voicemail's follow-up action is attribution. It is
the same assignment this ADR guards, so it gets the same guard,
`authorize_attribution_change` with `record="voicemail"`, with the same error codes and
message text. An action is a voicemail follow-up when a `VoicemailMessage` row of the
workspace points at it with `follow_up_action_id`.

- **R1.** A change is `assignee_user_id` in the body with a value different from the
  action's current `assignee_user_id`. Clearing to `null` is a change when the current
  value is not `null`. Ids compare as exact strings, as for the voicemail's own
  assignment.
- **R2.** On a follow-up action an unchanged `assignee_user_id` needs no right, is not
  validated again, is not written and is not listed in the `action_item.updated` audit
  `fields`. A body with only an unchanged assignee answers `200` and writes no audit
  row. On any other action today's behaviour stays: the value is validated,
  written and listed, also when unchanged.
- **R3.** A change needs `meeting:attribute`. Every API key is refused first, whatever
  its scopes, and every caller without the right (members, record-scoped supervisors)
  gets `403 attribution_change_denied`.
- **R4.** A change is refused with `403 attribution_own_call`, owners and admins too,
  when the caller is the action's current `assignee_user_id`, the voicemail's
  `assigned_user_id`, or the new value.
- **R5.** Order: meeting lookup (`404` as today), action lookup (`404 action_not_found`
  as today), follow-up lookup, then the guard (R3, then R4), then `_validate_assignee`
  for a changed value only (`422 invalid_assignee`), then the writes. The guard runs
  before `meeting_view` and before any field of the action is assigned, so a member who
  sends a non-member id gets `403`, not `422`.
- **R6.** A refusal writes nothing: no action field of the same body (also not
  `description`, `status`, `due_at`, `priority`, `assignee_text`), no voicemail field,
  no audit event.
- **R7.** An accepted change writes `action_item.assignee_changed` (target `action_item`,
  category `access`) in the same transaction as `action_item.updated`, with ids only:
  `assignee_user_id_before`, `assignee_user_id_after` (always both) and
  `voice_message_id`. Not `voicemail_id`: the word `voicemail` contains `email`, which
  the audit masker treats as personal data.
- **R8.** Everything else is unchanged. The `assignee_user_id` of any other action item
  is normal work assignment and stays under `meeting:update`. `assignee_text` is free
  display text and never attribution. `description`, `due_at`, `priority` and `status`
  of a follow-up action stay under `meeting:update`. `POST /meetings/{id}/actions`,
  `DELETE /meetings/{id}/actions/{action_id}`, the voicemail route and the meetings
  route do not change, and the guard keeps its signature, messages and behaviour for
  its existing callers.

The voicemail's own assignment is not synced back from the action route: an accepted
change there changes only the action. The voicemail route keeps syncing the action from
the voicemail, as before.

## Consequences

- Only an owner or admin changes or clears a voicemail's assignment, and never on a
  voicemail assigned to them, and never to themselves. The same holds for the assignee
  of a voicemail's follow-up action through the actions route.
- A client that sends back unchanged assignment values keeps working (V3, R2); one that
  changed them with a member's session or with any API key now gets
  `403 attribution_change_denied`.
- Every assignment change is visible in the audit log: `voicemail.assignment_changed`
  on the voicemail route, `action_item.assignee_changed` on the actions route.

## Not built

- The meetings and conversations routes do not change (ADR 0052 keeps its exact
  behaviour, status codes and messages).
- No console screen changes a voicemail's assignment.
- No migration and no schema change.
