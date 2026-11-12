# ADR 0053: Criterion override and the human result

Date: 2026-11-04 · Status: Proposed (HOM-74, WP14-T03, F44) · Builds on: ADR 0051, ADR 0052, the HOM-56 scorecard README

All code paths are under `Homayar/backend/`. HOM-74 lands in two PRs: PR 1 adds the
`review_criterion_decisions` and `review_results` tables (migration `a651110b03c3`); PR 2
adds the route, the service and the audit events. This ADR records the decisions of
both, so the schema serves the rules that come after it.

## Context

A machine score (HOM-56) is a first answer, not a final one. A supervisor works a review
item (HOM-73) and judges the machine result criterion by criterion. Some criteria are
right, some are wrong, and some have too little evidence for the machine to say
anything at all. The judgment must keep the machine score as it was, record what the
person changed and why, and produce a human score computed the same way as the machine
one, so the two can be compared.

Two risks shape the design. The stored machine score must never be edited to match a
person's opinion. And a decision must always belong to the exact result its item is
reviewing, so a re-scored conversation cannot silently move a decision to another
result.

## Decision

### D1. One decision row per criterion per item

A `review_criterion_decisions` row is one human decision on one criterion of the machine
result under review, inside one review item. The machine rows are never written. The
human decision lives only here.

A `decision` is `accept` or `override`. An accept confirms the machine outcome and
carries no reason code. A note on an accept is allowed. An override changes the outcome
and needs a `reason_code` from a frozen list and a non-blank note of at most 2000
characters. The reviewer outcome is one of `passed`, `failed`, `not_applicable`, the
same list as the engine's `REVIEWER_OUTCOMES`. `machine_outcome` is a copy of the
criterion row's outcome at decision time, so the row explains itself even later. The
database trusts this copy: the service of PR 2 writes it from the criterion row it
reads in the same transaction. The service, not a database check, enforces when a
decision may change: only while the item is `in_review`, and only by its assignee. A
machine `insufficient_evidence` can never be accepted, because the reviewer outcome
cannot be `insufficient_evidence`; only an override can settle it. The reason codes are
`evidence_missed`, `evidence_misread`, `transcript_error`, `context_outside_call`,
`policy_exception`, `other`. Codes, never free text, so no personal data reaches the
audit log.

The row is not immutable. The service upserts it while the review is in progress, on
the item's compare-and-set. `uq_review_criterion_decisions_criterion` on
(`review_item_id`, `criterion_id`) keeps one current decision per criterion per item.
Three composite foreign keys hold the scope: `fk_review_criterion_decisions_item` on
(`review_item_id`, `scorecard_result_id`) points at the new `uq_review_items_result` of
`review_items`, so a decision names only the result its item is reviewing;
`fk_review_criterion_decisions_result` ties the workspace and meeting copies to the
result's own; `fk_review_criterion_decisions_criterion` points at
`uq_scorecard_criterion_result_criterion`, so a decision names a criterion of that
result. No check names a user column: `decided_by_user_id` is SET NULL when a user is
removed, and a check on it would make that removal fail.

### D2. The human result is written once, like the machine result

A `review_results` row is the human score of one completed review: the machine result
scored again by the same engine with the reviewer outcomes. One row per completed item
that has a scorecard result (`uq_review_results_item`). The engine input
(`reviewer_outcomes`), the engine version (`scorecards_version`) and the engine output
(`result`, exactly `ScorecardResult.to_json()`) are stored, next to the same outcome,
reason, threshold and weight columns the machine result has, under the same rules. The
machine tables are never written, so the machine score and the human score stay
separate.

The table is immutable, like `scorecard_results`: the application role has no UPDATE on
it, and an ORM guard refuses updates and deletes. Only the conversation purge removes
rows. There is no user column on purpose: a SET NULL action on a user column would need
an update the table refuses. The completer is the item's assignee (only the assignee
completes, and a closed item never changes its assignee) and the audit actor. The same
two composite foreign keys as D1 tie the row to its item's result.

### D3. The write rules of PR 2

`POST /api/v1/review-items/{id}/criteria/{criterion_id}/decision` with `review:manage`
(no new permission; not an API key scope; CSRF, like the other HOM-73 actions). The
body carries `expected_revision` and `decision`, and for an override `reviewer_outcome`,
`reason_code`, `note` and `evidence_turn_indexes`, 1 to 20 distinct turn indexes of the
result's transcript revision.

Checks in order: the item is visible (`404 review_item_not_found`), the revision matches
(`409 review_item_revision_conflict`), it is active (`409 review_item_closed`), the
caller is the assignee (`403 review_not_assignee`), the caller is not the call's current
agent (`422 review_assignee_is_agent`), the status is `in_review`
(`409 review_item_state_invalid`), the item has a result (`409 review_item_has_no_result`),
the result's `scorecards_version` is the current engine version
(`409 scorecard_version_unsupported`), the criterion exists in the result
(`404 criterion_not_found`), the criterion is not `not_applicable_by_conversation`
(`422 criterion_not_reviewable`). Then the body: an accept of an
`insufficient_evidence` criterion (`422 criterion_accept_not_possible`), an override
equal to the machine outcome (`422 override_matches_machine`), an override to
`not_applicable` when `na_allowed` is false (`422 reviewer_na_not_allowed`), a missing
or unknown reason code or a blank note (`422 override_reason_required`), missing or
out-of-range evidence (`422 override_evidence_required`).

The write upserts the decision and does the item's compare-and-set in one transaction,
so a stale write never lands. An identical decision is not written and writes no event.
Reassignment keeps the decisions. Completing approves every current decision on the
item.

### D4. Completion and the recalculated score

`POST .../complete` (the HOM-73 route), when the item has a result: score the stored
check run again with the engine, the result's stored conversation facts and the
`reviewer_outcomes` from the decisions, and store one `review_results` row in the same
transaction as the status change. The machine rows are never written. An item without a
result completes as before, with no human result. `GET /api/v1/review-items/{id}/criteria`
(`review:read`) shows each criterion's machine outcome and evidence next to its
decision, and the machine score next to the human score: stored after completion, a
preview before.

### D5. Purge, backup and isolation

Both tables are workspace tables under row-level security (ADR 0005): tenant only,
never through a share token. `review_criterion_decisions` has the full application
grants, because the service upserts it. `review_results` is in `NO_UPDATE_TABLES`,
because a stored human score never changes. Neither is append-only or no-delete. The
conversation purge (ADR 0021) deletes the decisions and the human results of the purged
conversation, by `workspace_id` and `meeting_id`, before the review items and so before
the scorecard results, and counts them in the removed total. The order matters: the
cascades would otherwise remove them uncounted. A legal hold keeps them, as it keeps
the other content rows, until its release. `backup/reconcile.py` lists both tables in
`CONTENT_TABLES`, so the reconciliation of a restored database counts them. Cascades
also remove the rows of a removed workspace, conversation, review item or scorecard
result. Removing a user sets `decided_by_user_id` to NULL and keeps the decision.

### D6. The audit trail

PR 2 writes `review_criterion.decided` per accepted write. The details are ids, codes,
outcomes, turn indexes and the item revision, never the note text. `review_item.completed`
gains the result ids, the machine and the human outcome and reason, the four human
weights and the counts of accepted and overridden criteria, so the audit matches the
stored human result.

## Consequences

- The machine score and the human score are two stored facts, never one edited fact, so
  a past review can always be explained and re-derived.
- The database holds the scope rules, so a decision cannot name another result's
  criterion even inside a buggy service.
- Reviewers must say why they overrode, in their own words and in a code, before the
  write is accepted.
- The human score is only computed at completion, on the decisions current at that
  moment; a changed decision before completion simply changes the score that completion
  will compute.
- PR 2 needs no schema change: everything its rules need is already a column or a key
  here.

## Not built

- Withdrawing a decision after it was written; a later decision replaces it through the
  same upsert.
- Coaching (F47). Calibration is ADR 0056 (HOM-75) and appeals (F46) are ADR 0057 (HOM-76).
- The console pages for decisions and human results (HOM-265).
- The data-subject export of decisions.
