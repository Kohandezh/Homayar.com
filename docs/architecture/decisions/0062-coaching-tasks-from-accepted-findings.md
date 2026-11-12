# ADR 0062: Coaching tasks from accepted findings

Date: 2026-11-13 · Status: Proposed (HOM-77, WP14-T06, F47) · Builds on: ADR 0051, ADR 0052, ADR 0053, ADR 0054, ADR 0057

All code paths are under `Homayar/backend/`. HOM-77 lands in two PRs. PR 1 adds the
`coaching_tasks` table (migration `5f3a91c0d7b2`), the row-level security lists, the
conversation purge and the backup reconciliation. PR 2 adds the service, the routes,
the audit events, the notifications and the reminder sweep. This ADR records the
decisions of both, so the schema serves the rules that come after it.

## Context

F47 (parent HOM-18) asks for this: "Accepted finding creates assigned task with due
date; agent acknowledgement and follow-up status visible to allowed roles;
overdue/reminder deduplication and closure audit pass." WP14 step 6 says: "Create
specific coaching tasks from accepted findings, with owner, due date, acknowledgement
and follow-up review." The console ticket HOM-250 waits on this.

The review queue (ADR 0051), the human result (ADR 0053) and the appeals (ADR 0057)
already judge a call. Coaching turns one judgment into one piece of work for the
agent. Three risks shape the design. First, a task must name the exact finding it
coaches, in its own workspace, or the agent practises the wrong thing. Second, nobody
coaches or reviews their own call (the attribution rules of ADR 0052 and ADR 0054).
Third, a reminder or an overdue notice must go out once, even when two sweeps run at
the same time.

## Definitions

- **Accepted finding.** A criterion of a `completed` review item that has a human
  result (`review_results`), whose **final outcome** is `failed`. The final outcome is
  the one of ADR 0057 D4: the appeal result's outcome for that criterion if the item
  has an appeal result, else the human result's outcome. Completing a review approves
  every current decision on the item (ADR 0053 D3), so a failed criterion in the human
  result is a finding the reviewer accepted.
- **Coachee.** The call's current agent (`meetings.agent_user_id`) when the task is
  created. It is stored in `coaching_tasks.agent_user_id` and never changes.
- **Owner.** The coach: the supervisor who follows the task and does the follow-up
  review. It is stored in `owner_user_id`.
- **Active task.** A task with status `assigned` or `acknowledged`.

## Decision

### D1. The table (PR 1)

One table, `coaching_tasks` (model `CoachingTask`).
**One task names exactly one finding**: one criterion of one review item. This keeps
each task specific.

**Frozen lists.** They live in `backend/database/saas_models.py`, next to the HOM-76
lists:

- `COACHING_TASK_STATUSES`: `assigned`, `acknowledged`, `closed`, `cancelled`.
- `COACHING_TASK_ACTIVE_STATUSES`: `assigned`, `acknowledged`.
- `COACHING_FINDING_SOURCES`: `human_result`, `appeal_result`. It names the stored
  score that gave the `failed` outcome when the task was created.
- `COACHING_FOLLOW_UP_OUTCOMES`: `improved`, `partially_improved`, `not_improved`.
- `COACHING_CANCEL_REASONS`: `finding_overturned`, `agent_changed`, `duplicate`,
  `other`.
- `COACHING_MAX_DUE_DAYS` is 90 (PR 2 uses it).
- `COACHING_REMINDER_LEAD_HOURS` is 24 (PR 2 uses it).

Every note is at most `REVIEW_NOTE_MAX_LENGTH` (2000) characters. The code reuses that
constant and `_sql_list`, and does not copy the words.

**Columns.**

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | String(36) | no | primary key, `new_id` |
| `workspace_id` | String(36) | no | declared like `review_appeals.workspace_id` |
| `meeting_id` | String(36) | no | a copy of the item's conversation |
| `review_item_id` | String(36) | no | |
| `scorecard_result_id` | String(36) | no | the item's machine result |
| `criterion_id` | String(64) | no | the finding, as in `review_appeal_criteria` |
| `finding_source` | String(20) | no | in `COACHING_FINDING_SOURCES` |
| `agent_user_id` | users, SET NULL | yes | the coachee |
| `owner_user_id` | users, SET NULL | yes | the coach |
| `created_by_user_id` | users, SET NULL | yes | |
| `instruction` | Text | no | what to practise; not blank, at most 2000 |
| `due_at` | DateTime(tz) | no | |
| `status` | String(20) | no | default `assigned` |
| `acknowledged_at` | DateTime(tz) | yes | |
| `acknowledgement_note` | Text | yes | the agent's optional reply, at most 2000 |
| `follow_up_outcome` | String(30) | yes | |
| `follow_up_note` | Text | yes | |
| `closed_at` | DateTime(tz) | yes | |
| `closed_by_user_id` | users, SET NULL | yes | |
| `cancel_reason` | String(30) | yes | |
| `cancel_note` | Text | yes | at most 2000 |
| `cancelled_at` | DateTime(tz) | yes | |
| `cancelled_by_user_id` | users, SET NULL | yes | |
| `reminder_notified_at` | DateTime(tz) | yes | the due-soon reminder was sent (PR 2) |
| `overdue_notified_at` | DateTime(tz) | yes | the overdue notice was sent (PR 2) |
| `revision` | Integer | no | default 1 |
| `created_at`, `updated_at` | DateTime(tz) | no | like `review_appeals` |

The five user columns are SET NULL when a user is removed, and the row stays. No check
names a user column, for the same reason as ADR 0057 D1: a check on a user column
would make the removal fail.

**Keys.** They copy the `review_appeals` declarations, with `ondelete` exactly as
there.

- `fk_coaching_tasks_item`: (`review_item_id`, `scorecard_result_id`) to
  `review_items` (`id`, `scorecard_result_id`), that is `uq_review_items_result`. A
  task names only the result its item reviewed.
- `fk_coaching_tasks_result`: (`scorecard_result_id`, `workspace_id`, `meeting_id`)
  to `scorecard_results`, that is `uq_scorecard_result_scope`. The workspace and the
  meeting are the result's own.
- `fk_coaching_tasks_review_result`: (`review_item_id`) to `review_results`
  (`review_item_id`), that is `uq_review_results_item`. A task exists only for an
  item with a human result.
- `fk_coaching_tasks_criterion`: (`scorecard_result_id`, `criterion_id`) to
  `scorecard_criterion_results` (`result_id`, `criterion_id`), that is
  `uq_scorecard_criterion_result_criterion`. A task names only a criterion of that
  result.
- `uq_coaching_tasks_active_finding`: a partial unique index on (`review_item_id`,
  `criterion_id`) where the status is active (`postgresql_where` and `sqlite_where`,
  like `uq_review_items_active_meeting`). At most one active task per finding. A
  closed or cancelled task does not block a new one.
- `ix_coaching_tasks_queue` on (`workspace_id`, `status`, `due_at`, `id`): the
  supervisor list and its keyset pages.
- `ix_coaching_tasks_agent` on (`workspace_id`, `agent_user_id`, `created_at`, `id`):
  the agent's list.
- `ix_coaching_tasks_due` on (`status`, `due_at`): the reminder sweep.
- `ix_coaching_tasks_workspace_meeting` on (`workspace_id`, `meeting_id`): the purge
  and the backup.

**Checks.** No check uses a regex, so SQLite enforces the same rules as PostgreSQL. A
check passes when its expression is NULL, so each check is false, never NULL, for a
row it refuses.

- `ck_coaching_tasks_status`: the status is in the list.
- `ck_coaching_tasks_revision`: `revision >= 1`.
- `ck_coaching_tasks_finding_source`: the finding source is in the list.
- `ck_coaching_tasks_instruction`: `length(trim(instruction)) > 0 AND
  length(instruction) <= 2000`.
- `ck_coaching_tasks_acknowledgement_note`: NULL, or at most 2000 characters with
  `acknowledged_at` set.
- `ck_coaching_tasks_follow_up_outcome`: NULL or in the list.
- `ck_coaching_tasks_follow_up_note_length`: NULL or at most 2000 characters.
- `ck_coaching_tasks_cancel_reason`: NULL or in the list.
- `ck_coaching_tasks_cancel_note_length`: NULL or at most 2000 characters.
- `ck_coaching_tasks_assigned_fields`: an `assigned` task has no `acknowledged_at`,
  no follow-up outcome or note, no `closed_at`, and no cancel reason, note or time.
- `ck_coaching_tasks_acknowledged_fields`: an `acknowledged` task has
  `acknowledged_at`, and none of the follow-up, close or cancel fields.
- `ck_coaching_tasks_closed_fields`: a `closed` task has `acknowledged_at`,
  `closed_at`, a follow-up outcome and a non-blank follow-up note, and no cancel
  fields. A follow-up review needs an acknowledged task.
- `ck_coaching_tasks_cancelled_fields`: a `cancelled` task has `cancelled_at` and a
  cancel reason, and no `closed_at`, follow-up outcome or follow-up note. A cancelled
  task may or may not have been acknowledged.
- `ck_coaching_tasks_criterion_key`: the criterion key rule (`CRITERION_KEY_CHECK`),
  as `review_appeal_criteria` uses it.

The table is not immutable. PR 2 acknowledges, reassigns, closes and cancels a task
with a compare-and-set on `revision`, and the sweep sets the two `*_notified_at`
columns.

### D2. Independence

Nobody coaches or reviews their own call (attribution, ADR 0052 and ADR 0054).

- The creator, the owner and every actor of a supervisor write (reassign, close,
  cancel) is never the call's **current** agent and never the **coachee**.
- The owner has review authority over the call by the HOM-73 record rules
  (`check_assignee`). PR 2 checks it at create, at reassign and again at close.
- Every write reads the meeting again, so a `PATCH /meetings/{id}` that changed the
  agent is seen. The task keeps its coachee. The new current agent can never act on
  it as a supervisor. A supervisor may cancel it with `agent_changed`.
- Only the coachee acknowledges. The agent can never create, close, cancel or
  reassign a task, and can never change a finding.

### D3. The write rules of PR 2

**Permissions.** A new `coaching:respond` permission goes to every workspace role
(owner, admin, member, viewer). It lets a caller see their own coaching tasks and
acknowledge them. It is never an API-key scope. The supervisor side uses the HOM-73
permissions: reads need `review:read`, writes need `review:manage`. Every write needs
CSRF.

**Supervisor visibility.** The record rules are exactly those of review items
(ADR 0051). An owner or an admin sees every task. A member sees a task only when they
supervise the conversation's current team and are not its current agent. On top of
that, the coachee never sees the task through the supervisor routes. The coachee uses
the agent routes.

**Routes.** All routes are under `/api/v1`.

1. `POST /api/v1/review-items/{item_id}/coaching-tasks` (`review:manage`, CSRF). The
   body has `criterion_id`, `owner_user_id`, `due_at` (a timezone-aware ISO time) and
   `instruction`. The checks run in this order:
   - the item is visible to the caller (record rules), else
     `404 review_item_not_found`;
   - the caller is not the call's current agent, else `422 coaching_actor_is_agent`;
   - the item is `completed` with a human result, else
     `409 review_item_not_coachable`;
   - the item has no `open` appeal, else `409 review_appeal_open`;
   - the call has a current agent, else `409 coaching_no_agent`;
   - the criterion is in the result, else `404 criterion_not_found`;
   - its final outcome is `failed`, else `422 coaching_finding_not_failed`;
   - there is no active task for this item and criterion, else
     `409 coaching_task_exists` (a create that loses the race on
     `uq_coaching_tasks_active_finding` gets the same 409);
   - the owner is not the current agent, else `422 coaching_owner_is_agent`;
   - the HOM-73 `check_assignee` for the owner, with its existing codes;
   - `due_at` is after now and at most 90 days after now
     (`COACHING_MAX_DUE_DAYS`), else `422 coaching_due_invalid`;
   - `instruction` is not blank and at most 2000 characters, else
     `422 coaching_instruction_required`.

   It writes the task (`assigned`, revision 1, `agent_user_id` = the current agent,
   `finding_source`), the audit event and the notifications, in one transaction. The
   answer is `201`.
2. `GET /api/v1/coaching-tasks` (`review:read`) filters by `status`,
   `owner_user_id`, `agent_user_id` and `overdue` (a bool). It orders by `due_at` and
   `id` and returns keyset pages (`limit` 1 to 100, default 50; `cursor`, else
   `400 invalid_cursor`).
3. `GET /api/v1/coaching-tasks/{task_id}` is visible to a supervisor (full view) or
   to the coachee (agent view). Anyone else gets `404 coaching_task_not_found`.
4. `POST /api/v1/coaching-tasks/{task_id}/reassign` (`review:manage`, CSRF). The body
   has `expected_revision` and `owner_user_id`. The order: visible (404); the caller
   is not the current agent and not the coachee (`422 coaching_actor_is_agent`); the
   revision matches (`409 coaching_task_revision_conflict` with `current_revision` in
   the details); the task is active (`409 coaching_task_closed`); the new owner is not
   the current agent and not the coachee (`422 coaching_owner_is_agent`);
   `check_assignee` for the new owner. The same owner again writes nothing and no
   event.
5. `POST /api/v1/coaching-tasks/{task_id}/acknowledge` (`coaching:respond`, CSRF).
   The body has `expected_revision` and an optional `note` (at most 2000 characters,
   else `422 coaching_note_invalid`). The order: visible to the caller as coachee or
   supervisor, else 404; the caller is the coachee, else `403 coaching_not_agent`;
   the revision (409); the status is `assigned`, else
   `409 coaching_task_state_invalid`. It sets `acknowledged`, `acknowledged_at`, the
   note, and revision plus 1.
6. `POST /api/v1/coaching-tasks/{task_id}/close` (`review:manage`, CSRF) is the
   follow-up review. The body has `expected_revision`, `follow_up_outcome` and
   `follow_up_note`. The order: visible (404); the revision (409); active
   (`409 coaching_task_closed`); the caller is the owner, else
   `403 coaching_not_owner`; the caller is not the current agent and not the coachee
   (`422 coaching_actor_is_agent`); the status is `acknowledged`, else
   `409 coaching_task_not_acknowledged`; `check_assignee` for the caller; the outcome
   is in the list and the note is not blank and at most 2000 characters, else
   `422 coaching_follow_up_invalid`. It sets `closed`, `closed_at`,
   `closed_by_user_id`, the outcome and the note.
7. `POST /api/v1/coaching-tasks/{task_id}/cancel` (`review:manage`, CSRF). The body
   has `expected_revision`, `cancel_reason` and an optional `cancel_note`. The order:
   visible (404); the revision (409); active (`409 coaching_task_closed`); the caller
   is the owner, or a workspace owner or admin, else `403 coaching_not_owner`; the
   caller is not the current agent and not the coachee
   (`422 coaching_actor_is_agent`); the reason is in the list and the note is at most
   2000 characters, else `422 coaching_cancel_invalid`.
8. `GET /api/v1/my-coaching-tasks` (`coaching:respond`) lists the caller's tasks as
   coachee (`agent_user_id = caller`). It filters by `status`, puts the newest
   `created_at` first and returns keyset pages.
9. `GET /api/v1/coaching-tasks/{task_id}/history` lists the audit events oldest
   first, for the callers of route 3. In the coachee's view an actor id is shown only
   when the actor is the coachee or the current owner. Otherwise `actor_user_id` is
   null and `actor_role` is `reviewer`. The coachee's view also removes every other
   user id from the details.

**Agent view.** It shows the task id, the item id, the meeting id, the criterion id,
the finding source, the instruction, `due_at`, `overdue`, the status, the owner's user
id (the agent knows the coach), the acknowledgement, the follow-up outcome and note,
and the cancel reason. It never shows `created_by_user_id`, `closed_by_user_id`,
`cancelled_by_user_id` or any reviewer of the review item.

`overdue` is computed and never stored: an active task with `now >= due_at`.

A refusal writes nothing. **Concurrency.** Reassign, acknowledge, close and cancel are
a compare-and-set (`UPDATE ... WHERE id AND workspace_id AND revision = :expected AND
status IN (...)`), like HOM-76.

**Appeals and coaching.** A task can only be created when no appeal is open on the
item. When an appeal is decided later (the HOM-76 decide route) and it changes a
criterion that has an active coaching task to an outcome other than `failed`, the
same transaction cancels that task with `finding_overturned`
(`cancelled_by_user_id` = the decider), writes its audit event and notifies the
coachee and the owner.

### D4. Reminders and overdue notices (PR 2)

A Celery beat sweep runs every `coaching_reminder_sweep_interval_seconds` (a setting,
default 900). It follows the pattern of the existing beat entries in
`celery_app/__init__.py`, with a `python -m` fallback like the other sweeps. For each
active task:

- **Due soon:** `due_at - 24h <= now < due_at` (`COACHING_REMINDER_LEAD_HOURS`) and
  `reminder_notified_at IS NULL`. The sweep sets `reminder_notified_at` with a
  compare-and-set (`WHERE reminder_notified_at IS NULL AND status IN` the active
  statuses). Only the run whose update changed the row notifies the coachee
  (`coaching_task.due_soon`).
- **Overdue:** `now >= due_at` and `overdue_notified_at IS NULL`. The sweep uses the
  same compare-and-set on `overdue_notified_at`. The winning run notifies the coachee
  and the owner (`coaching_task.overdue`) and writes the audit event
  `coaching_task.overdue` (system actor).

So two sweeps at the same time, or a sweep run twice, never send a second reminder or
a second overdue notice for the same task. Notification rows also keep the HOM-76
rule: at most one row per user, kind and task. A task created with less than 24 hours
left gets its due-soon reminder on the first sweep, because the window rule already
holds. A task that is already overdue on its first sweep gets only the overdue notice:
the sweep sets `reminder_notified_at` too, with no due-soon row, so a late reminder
never follows an overdue notice.

### D5. Audit history

The target type is `coaching_task`. Ids, codes and times only. An event never carries
the instruction, the acknowledgement note, the follow-up note or the cancel note.

- `coaching_task.created`: the item, the result, the criterion, the finding source,
  the coachee, the owner and `due_at`.
- `coaching_task.reassigned`: the owner before and after.
- `coaching_task.acknowledged`.
- `coaching_task.closed`: the follow-up outcome. This is the closure audit of F47.
- `coaching_task.cancelled`: the cancel reason.
- `coaching_task.overdue`: `due_at` (system actor).

### D6. Notifications

Notifications are in-app rows in the existing `notifications` table. The table does
not change. PR 2 reuses the HOM-76 notification service. It generalises
`notify_appeal` into one helper that takes the subject id key (`appeal_id` or
`coaching_task_id`), keeps the one row per user, kind and subject rule, and keeps
`notify_appeal` working unchanged for the HOM-76 callers. Each kind has a fixed
English title and body. `data` holds ids only (`coaching_task_id`, `review_item_id`,
`meeting_id`). The kinds and their recipients:

- `coaching_task.assigned`: the coachee.
- `coaching_task.owner_assigned`: the owner, at create (when the owner is not the
  creator) and at reassign (the new owner).
- `coaching_task.acknowledged`: the owner.
- `coaching_task.due_soon`: the coachee.
- `coaching_task.overdue`: the coachee and the owner.
- `coaching_task.closed`: the coachee.
- `coaching_task.cancelled`: the coachee, and the owner when the owner did not
  cancel.

A removed user (a NULL id) gets nothing.

### D7. Purge, backup and isolation (PR 1)

- `database/rls.py` lists `coaching_tasks` in `WORKSPACE_TABLES`: row-level security,
  tenant only, never through a share token. It has the full grants (SELECT, INSERT,
  UPDATE, DELETE) and is not in `NO_UPDATE_TABLES`. The migration applies the policy
  and the grants the same way the HOM-76 migration does.
- The conversation purge (ADR 0021) deletes the `coaching_tasks` of the purged
  conversation, by `workspace_id` and `meeting_id`. It does this before it deletes
  the appeal rows, the review decisions, the human results and the review items,
  whose cascades would otherwise remove the tasks uncounted. It counts them in the
  removed total. A legal hold keeps them like the other content rows.
- `backup/reconcile.py` counts `coaching_tasks` (`CONTENT_TABLES`).

## Consequences

- A supervisor can turn one accepted finding into one task, and the agent, the owner
  and the history all point at the same criterion of the same review.
- The database holds the scope rules, so a bug in the service cannot move a task to
  another item, result, criterion or workspace, and cannot open two active tasks for
  one finding.
- The state rules live in checks, so a closed task always has its acknowledgement,
  outcome and note, and a cancelled task always has its reason.
- Reminder and overdue notices go out once per task, because the sweep marks the row
  with a compare-and-set before it notifies.
- PR 2 needs no schema change: everything its rules need is already a column or a key
  here.
- A user's removal keeps every task; only the user columns become NULL.

## Not built

- No automatic task from a finding: a supervisor chooses which findings to coach.
- No task from a machine score that no person reviewed.
- No due-date change route (cancel and create again).
- No reopening of a closed or cancelled task.
- No e-mail, digest or delivery preferences (WP24-T02, HOM-134).
- No console work (HOM-250).
