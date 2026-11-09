# ADR 0056: Blinded calibration and agreement reporting

Date: 2026-11-10 · Status: Proposed (HOM-75, WP14-T04, F46) · Builds on: ADR 0051, ADR 0053

All code paths are under `Homayar/backend/`. HOM-75 lands in two PRs: PR 1 adds the
five calibration tables (migration `02f616cbb655`); PR 2 adds the service, the routes,
the audit events and the report. This ADR records the decisions of both, so the
schema serves the rules that come after it.

## Context

A machine score (HOM-56) and a supervisor's review (HOM-73, HOM-74) tell one
workspace how its calls were judged. They do not tell whether two reviewers judge the
same way, or whether a reviewer agrees with the best answer. Calibration closes that
gap: reviewers answer the same fixed set of scored conversations without seeing each
other's answers, an adjudicator records the correct outcome, and the workspace reads
each reviewer's agreement against that adjudicated set (F46, first clause: blind
calibration computes agreement against adjudicated set). The console ticket HOM-249
waits on this.

Two risks shape the design. A reviewer must not be able to copy another reviewer's
answers, the machine outcome or the adjudication, or the exercise measures nothing.
And an answer or an adjudication must always belong to the exact criterion of the
exact result its example holds, in the batch it belongs to, or the report counts
pairs that were never compared.

## Decision

### D1. The five tables

A `calibration_batches` row is one exercise: a title, a status (`open`, `closed`,
`cancelled`) and a `revision` that starts at 1 and grows by one, so close and cancel
are compare-and-set writes, like `review_items`. Each status has its own closing
timestamp and its own user column (`closed_at` with `closed_by_user_id`,
`cancelled_at` with `cancelled_by_user_id`). The checks refuse every other mix of the
two timestamps. The user columns are outside every check: they are SET NULL when a
user is removed, and a check on them would make that removal fail.

A `calibration_examples` row is one machine scorecard result of one conversation in
one batch, with the admin's order as `position` (0 to 49). Immutable once written:
the list is fixed at creation, so the table has no user column, no UPDATE grant and
no ORM guard is needed. `uq_calibration_examples_meeting` keeps one conversation
once per batch. `fk_calibration_examples_batch` points at the new
`uq_calibration_batches_scope (id, workspace_id)` and
`fk_calibration_examples_scorecard_result` at `uq_scorecard_result_scope`, so an
example can only name its own batch and its result's own workspace and conversation.

A `calibration_participants` row is one reviewer in one batch. A removed user leaves
the row with a NULL `user_id`, so the report still counts their answers.
`uq_calibration_participants_user` keeps one participant once per batch.

A `calibration_answers` row is one participant's outcome on one criterion of one
example; a `calibration_adjudications` row is the correct outcome of one criterion,
with an optional note of at most 2000 characters (the review note length) and the
adjudicator in `decided_by_user_id`. The outcome list is exactly the engine's
`REVIEWER_OUTCOMES`: `passed`, `failed`, `not_applicable`. The words
`insufficient_evidence` and `reviewer` are refused. Both tables carry composite
foreign keys: to the example's batch and result together
(`uq_calibration_examples_scope`), so a row cannot name another batch's example or
another result; to the result's own workspace and meeting; and to a criterion of that
result. An answer also names a participant of its own batch
(`uq_calibration_participants_scope`). `uq_calibration_answers_criterion` keeps one
current answer per participant, example and criterion, and
`uq_calibration_adjudications_criterion` one current adjudication per example and
criterion. The service of PR 2 upserts both while the batch is open, so neither table
is immutable.

The frozen lists live in `backend/database/saas_models.py`: the statuses, the
outcomes, the title length 200, at most 50 examples and at most 20 participants. The
database does not count examples or participants; the create route of PR 2 does.

### D2. Blinding

Until the batch closes, nobody sees a participant's answers except that participant,
and no participant sees the adjudications, the machine outcomes, the machine reasons
or the machine evidence through the calibration API. The batch detail shows the batch,
the participants (user ids) and each example with its criteria (`criterion_id`,
`position`, `weight`, `na_allowed`, `reviewable`), and never the machine outcome, the
machine reason or the machine evidence.

Then the caller makes the difference. A participant, admin or not, sees only their own
answers, before and after close, and the adjudications only after close. An admin who
is not a participant sees per-participant progress counts (answered of expected) and
the adjudications, and other people's answers only after close. Nobody sees another
participant's answers before close.

A cancelled batch never reveals answers and has no report. Calibration never writes a
review item, a criterion decision, a human result or a machine result: it is a
separate exercise. The blinding covers the calibration API only; the meeting and
review-item endpoints still show the machine score to a reviewer who may read them.

### D3. The write rules of PR 2

No new permission. Reads need `review:read`; writes need `review:manage` and CSRF,
like HOM-73. Neither is an API key scope. A review admin is a workspace OWNER or
ADMIN (`REVIEW_ADMIN_ROLES`).

A user is eligible for an example's conversation M under the HOM-73 assignee rule
(`check_assignee`): effective OWNER, ADMIN or MEMBER; a MEMBER must supervise
`M.team_id`; and the user is not `M.agent_user_id`. The agent check comes first.

Routes under `/api/v1/calibration-batches`:

1. `POST ""` (review admin only, else `403 calibration_admin_required`): `title` (1
   to 200 chars, not blank), `scorecard_result_ids` (1 to 50, distinct),
   `participant_user_ids` (1 to 20, distinct). Each result must be in the workspace
   with a non-deleted conversation (`422 calibration_result_invalid`, details: the
   id), one conversation at most once (`422 calibration_duplicate_conversation`), and
   each result needs a reviewable criterion that is not
   `not_applicable_by_conversation` (`422 calibration_result_not_reviewable`). Each
   participant must be eligible for every example: the agent of an example's
   conversation answers `422 calibration_participant_is_agent`, any other failure
   `422 calibration_participant_ineligible` (details: `user_id`, `meeting_id`). The
   given order becomes `position`. The creator may be a participant.
2. `GET ""`: admins see every batch of the workspace, others only theirs. Newest
   first, keyset pages (`limit` 1 to 100, default 50; `cursor`), like the review item
   list.
3. `GET "/{batch_id}"`: not visible (not admin, not participant, other workspace,
   missing) is `404 calibration_batch_not_found`. The route shows the batch, the
   participants (user ids) and each example with its criteria (`criterion_id`,
   `position`, `weight`, `na_allowed`, `reviewable`), never the machine outcome, the
   machine reason or the machine evidence. Then by caller, as D2 says: a participant
   (admin or not) sees only their own answers, before and after close, and the
   adjudications only after close; an admin who is not a participant sees
   per-participant progress counts (answered of expected) and the adjudications, and
   other people's answers only after close. Nobody sees another participant's answers
   before close.
4. `POST "/{batch_id}/examples/{example_id}/criteria/{criterion_id}/answer"`, body
   `outcome`. Order: visible (404), caller is a participant
   (`403 calibration_not_participant`), batch open (`409 calibration_batch_closed`,
   also for cancelled), example in the batch (`404 calibration_example_not_found`),
   caller still eligible for the example's conversation
   (`422 calibration_participant_is_agent`, then `403 calibration_participant_ineligible`),
   criterion in the result (`404 criterion_not_found`), reviewable
   (`422 criterion_not_reviewable`), `not_applicable` only when `na_allowed`
   (`422 reviewer_na_not_allowed`). Upsert; the same outcome again writes nothing and
   no event. Answers can change until close.
5. `POST "/{batch_id}/examples/{example_id}/criteria/{criterion_id}/adjudication"`,
   body `outcome` and optional `note`. Order: visible (404), review admin
   (`403 calibration_admin_required`), not a participant of this batch
   (`403 calibration_adjudicator_is_participant`), open (409), example (404), caller
   not the conversation's current agent (`422 calibration_adjudicator_is_agent`),
   criterion (404, 422), na rule (422). Upsert, no-op on identical.
6. `POST "/{batch_id}/close"`, body `expected_revision`. Order: visible (404), review
   admin (403), revision (`409 calibration_batch_revision_conflict`, details
   `current_revision`), open (`409 calibration_batch_closed`), every reviewable
   criterion of every example adjudicated (`409 calibration_adjudication_incomplete`,
   details `missing`). Sets `closed`, `closed_at`, `closed_by_user_id`, revision + 1.
7. `POST "/{batch_id}/cancel"`, body `expected_revision`: the same order without the
   adjudication check.
8. `GET "/{batch_id}/report"`: visible (404), closed (`409 calibration_batch_not_closed`).
   Admins get everything; a non-admin participant gets the overall block, the
   per-criterion block and only their own participant row (no machine row).

Concurrency: an answer or adjudication write locks the batch row FOR SHARE
(`with_for_update(read=True)`) before it checks the status. Close and cancel are a
compare-and-set `UPDATE ... WHERE id AND workspace_id AND revision = :expected AND
status = 'open'`. So no answer or adjudication lands after a close commits. Answers
and adjudications do not change the batch revision.

### D4. Closing and the report

The report is computed on read from the frozen rows; it is not stored. All numbers
are exact integers, computed with `fractions.Fraction`, never a float. Pairs are
(example, reviewable criterion) with an adjudication. For one participant: `expected`
is the number of pairs; `compared` the pairs they answered; `matched` the answers
equal to the adjudication; `missing` is `expected - compared`. `agreement_bp` is
`matched * 10000 / compared` rounded half up to an integer, NULL when `compared` is
0. Cohen's kappa over the compared pairs: `p_o = matched / n`, `p_e` is the sum over
outcomes of `answers_k * gold_k / n^2`, `kappa = (p_o - p_e) / (1 - p_e)`;
`kappa_e4` is `kappa * 10000` rounded half away from zero, NULL when `n` is 0 or
`p_e` is 1. A `confusion` map `{adjudicated_outcome: {answer_outcome: count}}` comes
with it. Per criterion id, over all participants and examples together, the report
gives only `compared`, `matched` and `agreement_bp`. The overall block pools every
participant into one row and carries the same numbers a participant row has. The
machine row (admins only) compares the machine outcome of each pair against the
adjudication the same way, where `insufficient_evidence` counts as compared and never
matches. A participant whose user was removed shows `user_id: null`.

### D5. Purge, backup and isolation

All five tables are workspace tables under row-level security (ADR 0005): tenant
only, never through a share token. `calibration_examples` is in `NO_UPDATE_TABLES`
(its list is fixed at creation); the other four have the full application grants. The
conversation purge (ADR 0021) deletes the answers, the adjudications and then the
examples of the purged conversation, by `workspace_id` and `meeting_id`, before the
review decisions, the review items and the scorecard results whose cascades would
otherwise remove them uncounted, and counts them in the removed total. A legal hold
keeps them like the other content rows (the hold check defers the whole purge
attempt). Batches and participants are not conversation content and stay.
`backup/reconcile.py` lists the three content tables in `CONTENT_TABLES`; batches and
participants have no `meeting_id`, so like every other workspace table without a
conversation they are not content tables. Cascades also remove the rows of a removed
workspace, conversation or scorecard result. Removing a user sets every user column
to NULL (`created_by_user_id`, `closed_by_user_id`, `cancelled_by_user_id`,
`user_id`, `decided_by_user_id`) and keeps the rows.

### D6. The audit trail

PR 2 writes `calibration_batch.created` (batch id, result ids, participant user ids,
counts), `calibration_answer.recorded` (batch, example, criterion and participant
ids, never the outcome, because admins read the audit log and an admin may be a
participant), `calibration_adjudication.recorded` (ids only, never the outcome or the
note), `calibration_batch.closed` (revision, counts, overall `compared`, `matched`,
`agreement_bp`) and `calibration_batch.cancelled`. Never the title.

## Consequences

- Two reviewers can be compared on the same fixed material without one seeing the
  other, and the report measures agreement against an answer a person decided, not
  against the machine.
- The database holds the scope rules, so a bug in the service cannot move an answer
  to another batch, result or workspace.
- The exercise is fixed at creation: no adding or removing examples or participants
  later (cancel and create again).
- PR 2 needs no schema change: everything its rules need is already a column or a
  key here.
- The report can be re-derived at any time from the stored rows, because nothing
  about it is stored.

## Not built

- The meeting and review-item endpoints still show the machine score, the agent and
  any human review of the same conversation to a reviewer who may read them, so
  calibration blinds only the calibration API.
- Due dates or reminders for a batch.
- Adding or removing examples or participants after creation (cancel and create
  again).
- Pairwise reviewer-to-reviewer agreement.
- The data-subject export of calibration rows.
- The console pages (HOM-249).
- Appeals and coaching remain open (F46 second clause, F47).
