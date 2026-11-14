# ADR 0054: A voicemail's assignment needs the attribution right

Date: 2026-11-05 · Status: Proposed (HOM-369; amended by HOM-373, HOM-375, HOM-380 and HOM-381) · Builds on: ADR 0052

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
- **R2.** On every voicemail action (any action on a voicemail meeting, the follow-up
  action included) an unchanged `assignee_user_id` needs no right, is not validated
  again, is not written and is not listed in the `action_item.updated` audit
  `fields`. A body with only an unchanged assignee answers `200` and writes no audit
  row. On an action on a meeting that is not a voicemail meeting, today's behaviour
  stays: the value is validated, written and listed, also when unchanged.
- **R3.** A change needs `meeting:attribute`. Every API key is refused first, whatever
  its scopes, and every caller without the right (members, record-scoped supervisors)
  gets `403 attribution_change_denied`.
- **R4.** A change is refused with `403 attribution_own_call`, owners and admins too,
  when the caller is the action's current `assignee_user_id`, the voicemail's
  `assigned_user_id`, or the new value.
- **R5.** Order: meeting lookup (`404` as today), action lookup (`404 action_not_found`
  as today), voicemail lookup, then the guard (R3, then R4), then `_validate_assignee`
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
- **R8.** Everything else is unchanged. The `assignee_user_id` of an action on a
  meeting that is not a voicemail meeting is normal work assignment and stays under
  `meeting:update`. `assignee_text` is free display text and never attribution.
  `description`, `due_at`, `priority` and `status` of a voicemail action stay under
  `meeting:update`. The voicemail route and the meetings route do not change, and
  the guard keeps its signature, messages and behaviour for its existing callers.

The voicemail's own assignment is not synced back from the action route: an accepted
change there changes only the action. The voicemail route keeps syncing the action from
the voicemail, as before.

## Delete and create (HOM-375)

Two side doors stayed open around the rules above: `POST /meetings/{id}/actions` on the
voicemail's meeting created an action with any `assignee_user_id`, the caller's own id
included, and `DELETE /meetings/{id}/actions/{action_id}` removed the follow-up action
with no assignment check. A member could also post an unassigned action there and then
patch its `assignee_user_id`, because only the linked action was guarded.

Decision: the assignee of any action on a voicemail's meeting is the voicemail's
assignment. An action is a voicemail action when its meeting is named by a
`VoicemailMessage` row of the workspace in `meeting_id`. The guard is the same,
`authorize_attribution_change` with `record="voicemail"`.

Create, `POST /meetings/{meeting_id}/actions`:

- **C1.** On a voicemail meeting, a body with a non-null `assignee_user_id` is an
  assignment. It runs the guard with `current_agent_user_id=voicemail.assigned_user_id`
  and `changes={"agent_user_id": <the value>}`. So: every API key and every caller
  without `meeting:attribute` gets `403 attribution_change_denied`; an owner or admin
  gets `403 attribution_own_call` when the value is their own id or when the voicemail's
  `assigned_user_id` is their id.
- **C2.** A body with `assignee_user_id` absent or `null` is not an assignment and stays
  under `meeting:update` as today. `assignee_text` is free display text and never
  attribution.
- **C3.** Order: meeting lookup (`404` as today), voicemail lookup, the guard, then
  `_validate_assignee` (`422 invalid_assignee`), then the source segment check, then
  `meeting_view` and the writes. So a member who sends a non-member id gets `403`, not
  `422`.
- **C4.** A refusal writes nothing: no action row, no evidence row, no audit row.
- **C5.** An accepted create with an assignee on a voicemail meeting writes
  `action_item.created` (as today) and `action_item.assignee_changed` in the same
  transaction, ids only: `assignee_user_id_before` = `null`, `assignee_user_id_after` =
  the value, `voice_message_id` = the voicemail id. Target `action_item`, the new
  action id.

Delete, `DELETE /meetings/{meeting_id}/actions/{action_id}`:

- **E1.** Deleting a voicemail action removes an assignment when the action is the
  voicemail's follow-up action, or when its `assignee_user_id` is not `null`. Such a
  delete runs the guard twice (as the PATCH route does), each time with
  `changes={"agent_user_id": None}`: once with
  `current_agent_user_id=action.assignee_user_id`, once with
  `current_agent_user_id=voicemail.assigned_user_id`. So: every API key and every
  caller without `meeting:attribute` gets `403 attribution_change_denied`; an owner or
  admin gets `403 attribution_own_call` when they are the action's assignee or the
  voicemail's assignee.
- **E2.** Deleting a voicemail action that is not the follow-up action and has no
  `assignee_user_id` stays under `meeting:update` as today.
- **E3.** Order: meeting lookup (`404`), action lookup (`404 action_not_found`),
  voicemail lookup, the guard, then the writes.
- **E4.** A refusal writes nothing: the action stays, the voicemail's
  `follow_up_action_id` stays, no audit row.
- **E5.** An accepted guarded delete writes `action_item.deleted` (as today) and
  `action_item.assignee_changed` in the same transaction, ids only:
  `assignee_user_id_before` = the action's assignee (may be `null`),
  `assignee_user_id_after` = `null`, `voice_message_id`. When the deleted action is the
  follow-up action, the handler also sets `voicemail.follow_up_action_id = None` in the
  same transaction (PostgreSQL does this through `ON DELETE SET NULL`; set it
  explicitly so SQLite behaves the same). It does not change `follow_up_state` or the
  voicemail's assignment.

Change, `PATCH /meetings/{meeting_id}/actions/{action_id}`:

- **P1.** HOM-373's rules R1 to R7 now apply to every voicemail action, not only the
  follow-up action. Same guard calls, same order, same "unchanged value is not a
  change" rule, same audit event. `voice_message_id` is the id of the voicemail whose
  meeting holds the action.
- **P2.** R8 stays for actions on meetings that are not voicemail meetings: their
  `assignee_user_id` is normal work assignment under `meeting:update`.

## Re-analysis (HOM-380)

`persist_analysis` in `jobs/processor.py` replaces a meeting's analysis outputs.
Two callers reach it: the full processing job (also a re-run of it) and the
recompute after a transcript correction. A correction needs only
`meeting:update`, so a member can trigger a recompute. The code deleted every
AI action of the meeting. On a voicemail meeting an owner had set the
`assignee_user_id` of an AI action through the actions route. The re-analysis
then deleted that action, with no check and no `action_item.assignee_changed`
audit row.

Decision: on a voicemail meeting, a re-analysis keeps the AI actions a person
assigned and replaces only the rest.

- **A1.** A re-analysis is every call of `persist_analysis`: the full processing
  job (the first run and every re-run) and the recompute after a transcript
  correction.
- **A2.** On a voicemail meeting, a re-analysis replaces only AI actions whose
  `assignee_user_id` is null. An AI action with a non-null `assignee_user_id`
  got that value from a person: the pipeline creates AI actions with a null
  `assignee_user_id` (only `assignee_text`).
- **A3.** A kept action that no new action matches is not changed at all:
  same id, `assignee_user_id`, description, status, due, priority,
  `analysis_run_id`, `source_segment_id`, evidence (see HOM-381 below).
- **A4.** On a meeting that is not a voicemail meeting, every AI action is
  replaced, an assigned one too: there the assignee is normal work assignment
  under `meeting:update` (R8, P2).
- **A5.** Actions with `created_by` `voicemail_rules` or `user` are never
  removed by a re-analysis, as before.
- **A6.** A re-analysis writes no `action_item.assignee_changed` row, because
  no assigned action is removed. The recompute's `meeting.recompute_completed`
  audit row keeps its shape, and `action_item_count` counts every AI action
  the run wrote: the inserted rows and the kept rows it updated (HOM-381 K8).

## Matching a kept action (HOM-381)

A3 left a gap. After a correction, a kept action still showed the text and
evidence of the old transcript revision, while the new run often wrote a
corrected, unassigned copy of the same action next to it. The user then saw
two near-duplicate actions.

Decision: when the new run produces an action that matches a kept action, the
kept action takes the new text and the new evidence, keeps its assignee and
every person-set field, and the matching new action is not written as its own
row.

- **K1.** Scope: only a voicemail meeting, only in `persist_analysis`, only
  kept actions. Every other meeting replaces all of its AI actions, as A4 says.
- **K2.** Match rule: same non-null `kind` on both sides, and close text. The
  ratio of `difflib.SequenceMatcher(None, a, b, autojunk=False)` on the two
  descriptions, after `normalize_for_matching` in the revision's language, is
  at least `KEPT_ACTION_MATCH_RATIO = 0.8`. `a` is the kept action's text and
  `b` is the new action's text, in this order: `ratio()` is not symmetric, so
  for some pairs the order decides the answer, and the rule fixes it. Text
  that normalizes to nothing never matches, and a kept action with a null
  `kind` never matches.
- **K3.** One to one, deterministic: every passing (kept, new) pair is sorted
  by ratio descending, then by kept order (`created_at`, then `id`), then by
  the new action's index in the run, and pairs are taken greedily while both
  sides are free.
- **K4.** A match writes onto the kept row, same id: the new `description`,
  `source_segment_id`, `source_timestamp_ms`, `evidence_status` and
  `analysis_run_id`. The new evidence spans are written with
  `finding_id` = the kept action's id. Everything else stays, above all
  `assignee_user_id`.
- **K5.** A kept action with no match is not changed at all (A3). A new action
  with no match is inserted as today, unassigned.
- **K6.** Evidence spans stay immutable (ADR 0014), so a merged action keeps
  its old spans in the table but shows only its own run's spans:
  `ActionItem.evidence` also requires the span to have no run, or the action
  to have no run, or both run ids to be equal. Every action that existed
  before sees the same spans as before.
- **K7.** The coverage counters count action evidence through the same scope
  as `ActionItem.evidence`, so the call report and the coverage counts stay
  equal.
- **K8.** The run's output holds every AI action it wrote, inserted and
  updated kept rows alike. The recompute audit and the quality grades read
  that list. No new audit event type and no `action_item.assignee_changed`
  row: the assignee does not change.
- **K9.** `voicemail_rules` and `user` actions, the voicemail's follow-up
  action and `follow_up_action_id` are never touched, and every other rule of
  this ADR keeps its behaviour.

There is no migration and no schema change. Evidence stays immutable, and an
action shows the evidence of its own run, so the merge needs only row updates.

## Consequences

- Only an owner or admin changes or clears a voicemail's assignment, and never on a
  voicemail assigned to them, and never to themselves. The same holds for the assignee
  of every action on a voicemail's meeting through the actions route: set on create,
  changed on update, removed on delete.
- A client that sends back unchanged assignment values keeps working (V3, R2); one that
  set, changed or removed them with a member's session or with any API key now gets
  `403 attribution_change_denied`, on create and delete too.
- Every assignment change is visible in the audit log: `voicemail.assignment_changed`
  on the voicemail route, `action_item.assignee_changed` on the actions route, also on
  an accepted create with an assignee and an accepted guarded delete.
- A person-set assignment on a voicemail meeting survives a re-analysis (A2, A3): the
  full job and a correction's recompute keep every assigned AI action unchanged, and
  write no `action_item.assignee_changed` row for it.
- HOM-381: when a re-analysis restates an assigned voicemail action, the kept
  action takes the new text and evidence and no near-duplicate row appears
  (K1 to K9). The assignee and every person-set field never change.

## Not built

- The meetings and conversations routes do not change (ADR 0052 keeps its exact
  behaviour, status codes and messages).
- No console screen changes a voicemail's assignment.
- No fuzzy matching beyond K2; no console change.
- No migration and no schema change.
