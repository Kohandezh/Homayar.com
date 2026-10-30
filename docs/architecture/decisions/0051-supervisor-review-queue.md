# ADR 0051: The supervisor review queue

Date: 2026-10-30 · Status: Proposed (HOM-73, WP14-T02, F43, F44) · Builds on: ADR 0004, ADR 0005, ADR 0021, ADR 0045

All code paths are under `Homayar/backend/`. HOM-73 lands in two PRs: PR 1 adds the
`review_items` table (migration `484a6df74abf`); PR 2 adds the permissions, the service,
the routes and the audit events. This ADR records the decisions of both, so the schema
serves the rules that come after it.

## Context

A machine score (HOM-56) is not the end of quality work. A supervisor must look at some
conversations, by choice or from a seeded review sample (HOM-72), before a deadline. The
work needs an owner, a due time and a state that two people cannot change at the same
time. Later tickets build on this queue: criterion override (HOM-74), appeals and
calibration (F46), coaching (F47) and the console queue page (HOM-265).

Two risks shape the design. A supervisor must never review their own conversation. And a
person who can read a conversation is not always a person who may judge it.

## Decision

### D1. A review item is one unit of review work

A `review_items` row names one conversation (a `meetings` row, ADR 0045) of one
workspace. It has an optional assignee (`assignee_user_id`, with `assigned_by_user_id`
and `assigned_at`), a required `due_at`, and an optional `scorecard_result_id`: the
machine result under review.

Three frozen lists in `database/saas_models.py` hold the codes:

| List | Values |
| ---- | ------ |
| `REVIEW_ITEM_SOURCES` | `manual` (a supervisor or admin queued it), `sample` (an admin enqueued a sample draw) |
| `REVIEW_ITEM_STATUSES` | `open`, `in_review` (active); `completed`, `cancelled` (terminal) |
| `REVIEW_CANCEL_REASONS` | `duplicate`, `out_of_scope`, `meeting_unusable`, `other` |

A cancel reason is a code, never free text, so no personal data reaches the audit log.
`source_details` is `{}` for a manual item. For a sampled item it holds the sampler
version, the seed, the config and selection digests and the selection's reasons, so the
draw can be traced.

The database refuses a row that breaks the state rules:

- each status has its own timestamps: `open` has none, `in_review` has `started_at`,
  `completed` has `started_at` and `completed_at`, `cancelled` has `cancelled_at` and a
  reason (and keeps `started_at` when it was cancelled during its review);
- only a cancelled item has a `cancel_reason`;
- `revision` is at least 1;
- `fk_review_items_scorecard_result` points (`scorecard_result_id`, `workspace_id`,
  `meeting_id`) at `uq_scorecard_result_scope` of `scorecard_results`. An item can only
  name a result of its own conversation. A NULL result id is not checked.

The checks use no regex, so SQLite enforces the same rules as PostgreSQL. Each check is
false, never NULL, for a row it refuses. No check names a user column: the three user
columns are `SET NULL` when a user is removed, and a check on them would make the
removal fail.

### D2. One active item per conversation

`uq_review_items_active_meeting` is a partial unique index on (`workspace_id`,
`meeting_id`) where the status is `open` or `in_review`. A conversation has at most one
active item. A completed or cancelled item does not block a new one, so a conversation
can be reviewed again later.

Creating a second active item answers `409 review_item_exists` with the id of the
active one. Two creates at the same time are decided by the index, never by an
application check alone: the loser gets the same 409, not a 500. A sample enqueue skips
a conversation that already has an active item and lists it in `skipped_meeting_ids`.

### D3. Every write is a compare-and-set on the revision

`revision` starts at 1. Each change adds 1 and sets `updated_at`. Every write body
carries `expected_revision`, and the service writes with
`UPDATE ... WHERE id = :id AND workspace_id = :w AND revision = :expected`. When no row
changes, the answer is `409 review_item_revision_conflict` with the current revision.
Two supervisors who act on the same item at once never both succeed (F44, "stale-revision
write rejected"). A closed item answers `409 review_item_closed`.

An item moves `open` to `in_review` (start) to `completed` (complete), and any active
item can be cancelled. Only the assignee starts or completes it. A new assignee, or no
assignee, sends an `in_review` item back to `open`.

### D4. Who sees and works an item

Two permissions open the door: `review:read` and `review:manage`, for OWNER, ADMIN and
MEMBER, not VIEWER. Neither is an API key scope. The record rules then decide which
items a caller sees, for caller `U` in workspace `W` at time `now`:

- **Review admin**: `U`'s workspace role is OWNER or ADMIN.
- **Review authority over a conversation M**: `U` is a review admin, or `M.team_id` is
  a team `U` supervises (`supervised_team_ids`: effective, non-revoked supervisor
  assignments on active teams, ADR 0004).
- **Visible item**: its conversation is in `W` and not deleted, `U` has review
  authority over it, and (`U` is a review admin, or `U` is not the conversation's
  agent). A supervisor never sees or works the review of their own conversation.
- **Eligible assignee**: an effective OWNER, ADMIN or MEMBER of `W` with review
  authority over the conversation, who is not its agent. This applies to admins too.
  The agent check comes first, before the other eligibility checks: the agent answers
  `422 review_assignee_is_agent`. Any other failure (not a member, a VIEWER, a membership
  that is not effective, no review authority) answers `422 review_assignee_ineligible`.

An item that is not visible answers `404 review_item_not_found`, the same as a missing
id or an id of another workspace. Never 403: the answer must not tell that the item
exists.

Authority comes from the conversation's **current** `team_id`. The item stores no team.
When a conversation moves to another team, the authority moves with it. When a
supervisor stops supervising a team, they lose its items at once (404), and a manager
reassigns them.

**Why record scope does not grant review authority.** The workspace `record_scope`
(ADR 0004) widens which conversations a member may *read*. It does not make an agent a
supervisor. In a flat workspace every member reads every conversation, and if that also
let every member judge every conversation, agents could review each other's calls with
no one choosing them. Review authority comes only from the workspace role or from a
supervisor assignment.

### D5. Purge, backup and isolation

`review_items` is a workspace table under row-level security (ADR 0005): tenant only,
never through a share token. It is not append-only, not no-delete and not no-update,
because the service updates the rows and the purge deletes them. The application role
has SELECT, INSERT, UPDATE and DELETE on its own workspace's rows.

The conversation purge (ADR 0021) deletes the items of the purged conversation, by
`workspace_id` and `meeting_id`, before the conversation's scorecard results, and counts
them in the removed total. The order matters: an item can name a result, and the
result's cascade would otherwise remove the item uncounted. A legal hold keeps the items,
as it keeps the other content rows, until its release.

`backup/reconcile.py` lists the table in `CONTENT_TABLES`, so the reconciliation of a
restored database counts it with the other conversation content.

Cascades also remove the items of a removed workspace, conversation or scorecard
result. Removing a user sets the three user columns to NULL and keeps the item.

### D6. The audit trail

PR 2 writes one audit event per change: `review_item.created`, `.assigned`,
`.due_changed`, `.started`, `.completed`, `.cancelled`, and `review_sample.enqueued` for a
sample enqueue. Details hold ids, codes, timestamps and the revision after the change,
never free text. A no-change assignment writes no event.

## Consequences

- Later review features (HOM-74, F46, F47) attach to one queue entry with one owner and
  one revision, instead of each one inventing its own.
- The one-active-item rule and the revision check live in the database, so a race
  between two requests cannot break them.
- A supervisor's queue follows team changes without any data migration, because
  authority is computed from the conversation's current team.
- Admins must reassign items when a supervisor leaves a team; the items do not move by
  themselves.

## Not built

- Criterion accept or override, reviewer outcomes and recalculated aggregates (HOM-74).
  Completing an item here only closes the queue entry.
- Appeals and calibration (F46), and coaching (F47).
- Reminders and notifications for due or overdue items.
- The `review` coverage counter stays `review_records_unavailable`.
- The data-subject export of review items.
- Any console change (HOM-265).
