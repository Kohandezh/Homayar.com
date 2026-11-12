# ADR 0057: Agent appeal, supervisor disposition and audit history

Date: 2026-11-12 · Status: Proposed (HOM-76, WP14-T05, F46) · Builds on: ADR 0051, ADR 0052, ADR 0053, ADR 0054

All code paths are under `Homayar/backend/`. HOM-76 lands in two PRs. PR 1 adds the
three appeal tables (migration `0c51985fcf36`), the row-level security lists, the ORM
guard, the conversation purge and the backup reconciliation. PR 2 adds the service,
the routes, the audit events and the notifications. This ADR records the decisions of
both, so the schema serves the rules that come after it.

## Context

A machine score (HOM-56) and a supervisor's review (HOM-73, ADR 0051; HOM-74,
ADR 0053) judge a call. The agent of that call may disagree with the review. F46,
second clause, asks for this: "agent appeal reaches authorized reviewer and records
disposition; agent cannot edit machine/human decision directly." The console ticket
HOM-250 waits on this ("Each appeal has a full audit history"). Coaching (HOM-77)
builds on the final outcome after an appeal.

Three risks shape the design. First, the agent must never change a machine or a
human decision. They only ask; a different person decides. Second, nobody may decide
on their own call (the attribution rules of HOM-360 and HOM-369, ADR 0052 and
ADR 0054). Third, an appeal row must always belong to the exact item, result and
criterion it names, in its own workspace, or the final outcome counts the wrong thing.

## Decision

### D1. The three tables

**What an appeal is.** A review item that is `completed` and has a human result
(`review_results`) holds the final judgment of a call. The agent of that call (the
meeting's current `agent_user_id`) may file one appeal on that review item, within
14 days of the review's `completed_at`. The appeal names 1 to 20 criteria of the
reviewed result and gives a reason note.

A decider then decides the appeal. For each appealed criterion the decider either
upholds it (the human outcome stays) or changes it (a new outcome, with a reason code
and a note). The overall disposition follows from the criterion decisions:

- `upheld`: no criterion changed.
- `granted`: every appealed criterion changed.
- `partially_granted`: some criteria changed.

When at least one criterion changed, the service scores the machine result again with
the human outcomes plus the changes and stores an appeal result. The machine result
and the human result are never written.

**Frozen lists.** They live in `backend/database/saas_models.py`, next to the HOM-74
and HOM-75 lists:

- `REVIEW_APPEAL_STATUSES`: `open`, `decided`, `withdrawn`.
- `REVIEW_APPEAL_DISPOSITIONS`: `upheld`, `partially_granted`, `granted`.
- `REVIEW_APPEAL_CRITERION_DECISIONS`: `uphold`, `change`.
- `REVIEW_APPEAL_MAX_CRITERIA` is 20.
- `REVIEW_APPEAL_WINDOW_DAYS` is 14.

Every note is at most `REVIEW_NOTE_MAX_LENGTH` (2000) characters. The appealed outcome
is one of the four machine outcomes of `REVIEW_DECISION_MACHINE_OUTCOME_CHECK`
(`passed`, `failed`, `not_applicable`, `insufficient_evidence`). The reason: a
criterion the reviewer did not decide keeps its machine outcome in the human result.
The changed outcome is one of `REVIEW_REVIEWER_OUTCOMES` (`passed`, `failed`,
`not_applicable`), so `insufficient_evidence` and `reviewer` are refused. The change
reason codes are `REVIEW_OVERRIDE_REASONS`. The code reuses those objects and does
not copy the words.

**Check rules.** No check uses a regex, so SQLite enforces the same rules as
PostgreSQL. A check passes when its expression is NULL, so each check is false, never
NULL, for a row it refuses. No check names a user column. User columns are SET NULL
when a user is removed, and a check on them would make the removal fail.

**`review_appeals` (model `ReviewAppeal`).** One appeal of one completed review item.
Columns: `id`, `workspace_id`, `meeting_id` (a copy of the item's conversation),
`review_item_id`, `scorecard_result_id` (the item's machine result),
`appellant_user_id` (the agent who filed), `original_reviewer_user_id` (a copy of the
item's assignee at filing), `status` (default `open`), `reason_note` (the agent's
reason), `assignee_user_id` (the decider), `assigned_by_user_id`, `assigned_at`,
`disposition`, `disposition_note` (the decider's explanation to the agent),
`decided_by_user_id`, `decided_at`, `withdrawn_at`, `created_at`, `updated_at` and
`revision` (default 1). The five user columns are SET NULL when a user is removed, and
the row stays. The table is not immutable: PR 2 assigns, decides and withdraws an
appeal with a compare-and-set on `revision`, like `review_items`.

Its keys:

- `fk_review_appeals_item`: (`review_item_id`, `scorecard_result_id`) to
  `review_items` (`id`, `scorecard_result_id`), that is `uq_review_items_result`.
  An appeal names only the result its item reviewed.
- `fk_review_appeals_result`: (`scorecard_result_id`, `workspace_id`, `meeting_id`)
  to `scorecard_results`, that is `uq_scorecard_result_scope`. The workspace and the
  meeting are the result's own.
- `fk_review_appeals_review_result`: (`review_item_id`) to `review_results`
  (`review_item_id`), that is `uq_review_results_item`. So an appeal can exist only
  for an item that has a human result. No new index on `review_results` is needed.
- `uq_review_appeals_item`: one appeal per review item, ever.
- `uq_review_appeals_scope` on (`id`, `review_item_id`, `scorecard_result_id`): the
  target of the child keys.
- `ix_review_appeals_queue` on (`workspace_id`, `status`, `created_at`, `id`): the
  appeals queue and its keyset pages.
- `ix_review_appeals_workspace_meeting`: the purge and the backup.

Its checks:

- `ck_review_appeals_status`: the status is in the list.
- `ck_review_appeals_revision`: `revision >= 1`.
- `ck_review_appeals_reason_note`: the reason is not blank and at most 2000
  characters.
- `ck_review_appeals_disposition`: the disposition is NULL or in the list.
- `ck_review_appeals_disposition_note_length`: the disposition note is NULL or at most
  2000 characters.
- `ck_review_appeals_open_fields`: an `open` appeal has no disposition, no
  disposition note, no `decided_at` and no `withdrawn_at`.
- `ck_review_appeals_decided_fields`: a `decided` appeal has a disposition, a
  `decided_at`, a non-blank disposition note and no `withdrawn_at`.
- `ck_review_appeals_withdrawn_fields`: a `withdrawn` appeal has a `withdrawn_at`,
  and no `decided_at`, no disposition and no disposition note.

**`review_appeal_criteria` (model `ReviewAppealCriterion`).** One appealed criterion
of one appeal, and later its decision. Columns: `id`, `workspace_id`, `meeting_id`,
`appeal_id`, `review_item_id`, `scorecard_result_id`, `criterion_id`,
`appealed_outcome` (the criterion's outcome in the human result at filing),
`decision`, `decided_outcome`, `reason_code`, `note`, `created_at`, `updated_at`. The
table is not immutable: PR 2 writes the decision columns once, when the appeal is
decided.

Its keys:

- `fk_review_appeal_criteria_appeal`: (`appeal_id`, `review_item_id`,
  `scorecard_result_id`) to `uq_review_appeals_scope`. A row names only its appeal's
  item and result.
- `fk_review_appeal_criteria_result`: to the result's own workspace and meeting.
- `fk_review_appeal_criteria_criterion`: (`scorecard_result_id`, `criterion_id`) to
  `scorecard_criterion_results`. A row names only a criterion of that result.
- `uq_review_appeal_criteria_criterion`: one row per appeal and criterion.
- `ix_review_appeal_criteria_workspace_meeting`: the purge and the backup.

Its checks:

- `ck_review_appeal_criteria_appealed_outcome`: one of the four machine outcomes.
- `ck_review_appeal_criteria_decision`: NULL, `uphold` or `change`.
- `ck_review_appeal_criteria_decided_outcome`: NULL or a reviewer outcome.
- `ck_review_appeal_criteria_reason_code`: NULL or an override reason.
- `ck_review_appeal_criteria_note_length`: NULL or at most 2000 characters.
- `ck_review_appeal_criteria_undecided`: a row with no decision has no decided
  outcome, no reason code and no note.
- `ck_review_appeal_criteria_uphold`: an uphold has no decided outcome and no reason
  code. A note on an uphold is allowed.
- `ck_review_appeal_criteria_change`: a change has a decided outcome that differs from
  the appealed outcome, a reason code and a non-blank note.
- `ck_review_appeal_criteria_criterion_key`: the criterion key rule
  (`CRITERION_KEY_CHECK`).

**`review_appeal_results` (model `ReviewAppealResult`).** The score after a granted or
partially granted appeal: the machine result scored again by the same engine with the
human outcomes plus the changed ones. One row per appeal at most
(`uq_review_appeal_results_appeal`). Only an appeal with at least one `change` has
one. Columns: `id`, `workspace_id` and `meeting_id` (declared exactly as
`review_results` declares them), `appeal_id`, `review_item_id`,
`scorecard_result_id`, then exactly the scoring columns of `review_results` with the
same types: `scorecards_version`, `reviewer_outcomes` (the engine input), `outcome`,
`reason`, `complete`, `pass_threshold`, `applicable_weight`, `earned_weight`,
`failed_weight`, `insufficient_weight`, `result` (the engine output), and
`created_at`. The keys are `fk_review_appeal_results_appeal` (to
`uq_review_appeals_scope`) and `fk_review_appeal_results_scorecard_result` (to the
result's own scope). The checks `ck_review_appeal_result_outcome`,
`ck_review_appeal_result_reason`, `ck_review_appeal_result_threshold`,
`ck_review_appeal_result_weights` and `ck_review_appeal_result_complete` are the same
strings as the `ck_review_result_*` checks of `review_results`, in the same order
(outcome first, so SQLite names it when an unknown outcome also breaks the reason
rule).

The table is immutable, exactly like `review_results`. It has no UPDATE grant and sits
in `NO_UPDATE_TABLES`. An ORM guard refuses an update and a delete
(`ReviewAppealResultImmutableError`). It has no user column, because a SET NULL action
on one would need an update.

### D2. Independence

Nobody decides on their own call (HOM-360, HOM-369).

- The decider is never the call's current agent, never the appellant and never an
  original reviewer.
- The original reviewers of an item are its assignee who completed it
  (`original_reviewer_user_id`) and every user who is the `decided_by_user_id` of a
  criterion decision of that item. A reviewer who was reassigned away while the item
  was `in_review` keeps their decisions, so they count too. PR 2 reads the decision
  rows at assign time and at decide time.
- The decider has review authority over the call by the HOM-73 record rules
  (`check_assignee`).

### D3. The write rules of PR 2

**Permission.** A new `review:appeal` permission goes to every workspace role (owner,
admin, member, viewer). It lets a caller see their own reviewed calls and file and
withdraw their own appeals. It is never an API-key scope. The supervisor side uses the
HOM-73 permissions: reads need `review:read`, writes need `review:manage`. Every write
needs CSRF, like HOM-73.

**Agent side.**

1. `GET /api/v1/my-reviews` (`review:appeal`) lists completed review items that have
   a human result, whose conversation is not deleted and has
   `agent_user_id = caller`. Newest `completed_at` first, keyset pages (`limit` 1 to
   100, default 50; `cursor`). Each row has the item id, the meeting id,
   `completed_at`, the machine and the human outcome, the final outcome (D4),
   `appeal_deadline` (`completed_at` plus 14 days), and the appeal's id and status if
   there is one.
2. `GET /api/v1/my-reviews/{item_id}` returns the same, plus per criterion: the
   machine outcome, the human outcome, the decision, the reason code, the reviewer's
   note, the evidence turn indexes, and the appeal decision if decided. It never shows
   a reviewer's user id (not the assignee, not the decider). A call the caller may not
   see (not the current agent, another workspace, missing, deleted call) is
   `404 review_item_not_found`.
3. `POST /api/v1/my-reviews/{item_id}/appeal` (CSRF) takes `criterion_ids` (1 to 20,
   distinct) and `reason_note`. The checks run in this order:
   - visible as above, else `404 review_item_not_found`;
   - the item is `completed` with a human result, else
     `409 review_item_not_appealable`;
   - `now < completed_at + 14 days`, else `409 appeal_window_closed`;
   - no appeal yet, else `409 appeal_exists` (a create that loses the race on
     `uq_review_appeals_item` gets the same 409);
   - each criterion is in the result and is not `not_applicable_by_conversation`,
     else `422 appeal_criterion_invalid` with the `criterion_id` in the details; the
     count and the distinctness, else `422 appeal_criteria_invalid`;
   - `reason_note` is not blank and at most 2000 characters, else
     `422 appeal_reason_required`.

   It writes the appeal (`open`, revision 1, `original_reviewer_user_id` = the item's
   `assignee_user_id`), one criterion row per id with `appealed_outcome` taken from
   the stored human result, the audit event and the notifications, in one
   transaction.
4. `GET /api/v1/my-appeals` (`review:appeal`) lists the caller's own appeals
   (`appellant_user_id = caller`), newest first, keyset pages.
5. `POST /api/v1/review-appeals/{appeal_id}/withdraw` (`review:appeal`, CSRF) takes
   `expected_revision`. The order: visible to the caller as appellant or as reviewer,
   else `404 review_appeal_not_found`; the caller is the appellant, else
   `403 appeal_not_appellant`; the revision matches, else
   `409 review_appeal_revision_conflict` with `current_revision` in the details; the
   appeal is `open`, else `409 review_appeal_closed`. It sets `withdrawn`,
   `withdrawn_at` and revision plus 1.

**Supervisor side.** The record rules are exactly those of review items (ADR 0051).
An owner or an admin sees every appeal. A member sees an appeal only when they
supervise the conversation's current team and are not its current agent. On top of
that, the appellant and every original reviewer (D2) never see the appeal through
the supervisor routes. The appellant uses the agent routes.

6. `GET /api/v1/review-appeals` (`review:read`) filters by `status` and
   `assignee_user_id`, orders by `created_at` and `id`, and returns keyset pages.
7. `GET /api/v1/review-appeals/{appeal_id}` is visible to a supervisor by the rules
   above (full view) or to the appellant (agent view, with no reviewer user ids).
   Anyone else gets `404 review_appeal_not_found`.
8. `POST /api/v1/review-appeals/{appeal_id}/assign` (`review:manage`, CSRF) takes
   `expected_revision` and `assignee_user_id`. The order: visible (404); the revision
   (409); `open` (`409 review_appeal_closed`); the new assignee is not the call's
   current agent and not the appellant (`422 appeal_assignee_is_party`); the new
   assignee is not an original reviewer (`422 appeal_assignee_is_original_reviewer`);
   the HOM-73 `check_assignee` with its existing codes. The same assignee again
   writes nothing and no event.
9. `POST /api/v1/review-appeals/{appeal_id}/decide` (`review:manage`, CSRF) takes
   `expected_revision`, `disposition_note` and `decisions`: one entry per appealed
   criterion with `criterion_id`, `decision` (`uphold` or `change`), and for a change
   `decided_outcome`, `reason_code` and `note`. The order:
   - visible (404); the revision (409); `open` (409);
   - the caller is the assignee, else `403 review_appeal_not_assignee`;
   - the caller is not the call's current agent and not the appellant, else
     `422 appeal_decider_is_party`;
   - the caller is not an original reviewer, else
     `422 appeal_decider_is_original_reviewer`;
   - the HOM-73 `check_assignee` for the caller;
   - the result's `scorecards_version` is the engine's, else
     `409 scorecard_version_unsupported`;
   - the body has exactly the appealed criteria, each once, else
     `422 appeal_decisions_incomplete`; a change equal to the appealed outcome is
     `422 appeal_change_matches`; a change to `not_applicable` when `na_allowed` is
     false is `422 reviewer_na_not_allowed`; a change without a known reason code or
     with a blank note is `422 override_reason_required`; a blank or too long
     `disposition_note` is `422 appeal_disposition_note_required`.

   "Original reviewer" in these checks means any original reviewer as D2 defines it
   (the completing assignee or any decider of a criterion decision of the item). The
   same rule applies to the assign check. The decide route writes the criterion
   decisions, the appeal result when any criterion changed (D4), `decided`, the
   disposition, `decided_by_user_id`, `decided_at`, revision plus 1, the audit event
   and the notification, in one transaction. A refusal writes nothing.

**Attribution changes.** Every write reads the meeting again. If
`PATCH /meetings/{id}` changed the agent after filing, the appeal stays. The
appellant can still withdraw it, and the new current agent can never decide it.
Nobody acts on an appeal of a call where they are the current agent.

**Concurrency.** Assign, decide and withdraw are a compare-and-set on the appeal's
`revision` (`UPDATE ... WHERE id AND workspace_id AND revision = :expected AND
status = 'open'`), like HOM-73. Two concurrent decides never both land.

### D4. The appeal result and the final outcome

The final outcome of a review item is the appeal result if one exists, else the human
result. The machine result, the review decisions and the human result never change.
The appeal result's `reviewer_outcomes` are the human result's `reviewer_outcomes`
plus every changed criterion's `decided_outcome`. The service computes it with
`score_human_result`, the one scoring copy. An upheld appeal has no appeal result, so
its final outcome stays the human result.

### D5. Audit history

Each event has the target `review_appeal` and carries ids and codes only. It never
carries `reason_note`, `disposition_note` or a criterion note.

- `review_appeal.filed`: the item, the result, the criterion ids and the appellant.
- `review_appeal.assigned`: the assignee before and after.
- `review_appeal.withdrawn`.
- `review_appeal.decided`: the disposition, the counts of upheld and changed
  criteria, the human outcome, the final outcome, and the appeal result id or null.

`GET /api/v1/review-appeals/{appeal_id}/history` lists the events oldest first, for
the same callers as the detail route. For the appellant, an actor id is shown only
when the actor is the appellant. Otherwise `actor_user_id` is null and `actor_role` is
`reviewer`. The appellant's view also removes every user id from the event details
(for example the assignee before and after), so no reviewer identity reaches the
agent through the history.

### D6. Notifications

Notifications are in-app rows in the existing `notifications` table. That table does
not change in PR 1 or PR 2. Each row has a fixed English title and body, never a
name, a note or transcript text. `data` holds ids only (`appeal_id`,
`review_item_id`, `meeting_id`). The kinds and their recipients:

- `review_appeal.filed`: every user who could be assigned the appeal (owner, admin, or
  member supervising the call's current team, with review authority, effective
  membership), minus the appellant, the current agent and every original reviewer
  (D2); at most 50, ordered by user id.
- `review_appeal.assigned`: the new assignee.
- `review_appeal.decided`: the appellant (none when the user was removed).
- `review_appeal.withdrawn`: the assignee, if any.

There is at most one row per user, kind and appeal id. Two routes read them, for the
caller's own rows in the current workspace only, and for session callers only. API
keys get `403 api_key_scope_denied`.

- `GET /api/v1/notifications`: newest first, keyset pages, an `unread_only` filter.
- `POST /api/v1/notifications/{notification_id}/read`: idempotent. Another user's
  row is `404 notification_not_found`.

Delivery preferences, e-mail and digests are not built (WP24-T02, HOM-134).

### D7. Purge, backup and isolation

- `database/rls.py` lists the three tables in `WORKSPACE_TABLES`: tenant only, never
  through a share token. `review_appeal_results` is in `NO_UPDATE_TABLES`. The other
  two get the full grants (SELECT, INSERT, UPDATE, DELETE). The migration applies the
  policies and the grants the same way the HOM-75 migration does.
- The conversation purge (ADR 0021) deletes `review_appeal_results`,
  `review_appeal_criteria` and then `review_appeals` of the purged conversation, by
  `workspace_id` and `meeting_id`. It does this before it deletes the review
  decisions, the human results and the review items, whose cascades would otherwise
  remove the appeal rows uncounted. It counts them in the removed total. The deletes
  are bulk deletes, so the ORM guard of `review_appeal_results` does not fire, the
  same as for `review_results`. A legal hold keeps the appeal rows, like the other
  content rows: the purge removes no content while any object of the conversation is
  held.
- `backup/reconcile.py` counts the three tables (`CONTENT_TABLES`).

## Consequences

- An agent can ask for a second look at their review without being able to change
  any machine or human decision.
- The database holds the scope rules, so a bug in the service cannot move an appeal
  row to another item, result, criterion or workspace.
- The final outcome is always one of two stored, immutable scores, so a report or
  coaching (HOM-77) can read it without scoring again.
- PR 2 needs no schema change: everything its rules need is already a column or a
  key here.
- A user's removal keeps every appeal row; only the user columns become NULL.

## Not built

- No appeal of a machine score that no person reviewed.
- One appeal per review item; no second appeal.
- No reopening of a decided appeal.
- Notifications of a purged conversation keep their ids (no text) until the
  notification retention work.
- No delivery preferences or delivery channels (WP24-T02, HOM-134).
- Coaching (HOM-77, F47).
- The console pages (HOM-250).
- The data-subject export of appeal rows.
