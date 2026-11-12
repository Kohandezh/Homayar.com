"""Coaching tasks from accepted findings, schema only (HOM-77, WP14-T06)

Revision ID: 5f3a91c0d7b2
Revises: c4a8e2f7d913
Create Date: 2026-11-13

Adds the one table of coaching (F47): ``coaching_tasks``.  A supervisor turns one
accepted finding (one criterion of one completed review item that has a human result,
whose final outcome is ``failed``) into a task for the call's agent, with an owner
(the coach), a due date and an instruction.  The agent acknowledges it, and the owner
closes it with a follow-up review (an outcome and a note); a supervisor may cancel it.
Nothing writes the table yet; the service, the routes, the audit events, the
notifications and the reminder sweep follow in PR 2.

* The frozen lists live in ``backend/database/saas_models.py``:
  ``COACHING_TASK_STATUSES``, ``COACHING_TASK_ACTIVE_STATUSES``,
  ``COACHING_FINDING_SOURCES``, ``COACHING_FOLLOW_UP_OUTCOMES`` and
  ``COACHING_CANCEL_REASONS``.  Every note is at most ``REVIEW_NOTE_MAX_LENGTH``
  characters and the criterion id follows ``CRITERION_KEY_CHECK``.  The checks copy
  them as frozen strings, by constraint name.  They use no regex, so SQLite runs the
  same rules.  A check passes when its expression is NULL, so each one is false,
  never NULL, for a row it refuses.  No check names a user column:
  ``agent_user_id``, ``owner_user_id``, ``created_by_user_id``,
  ``closed_by_user_id`` and ``cancelled_by_user_id`` are SET NULL when a user is
  removed, and a check on them would make that removal fail.
* Why the composite keys: a task can only name the result its item reviewed
  (``fk_coaching_tasks_item`` on ``uq_review_items_result``), that result's own
  workspace and meeting (``fk_coaching_tasks_result`` on
  ``uq_scorecard_result_scope``), an item that has a human result
  (``fk_coaching_tasks_review_result`` on ``uq_review_results_item``) and a criterion
  of that result (``fk_coaching_tasks_criterion`` on
  ``uq_scorecard_criterion_result_criterion``).  A buggy service cannot cross items,
  results, criteria or workspaces.
* ``uq_coaching_tasks_active_finding`` is a partial unique index on
  (``review_item_id``, ``criterion_id``) over the active statuses: at most one
  ``assigned`` or ``acknowledged`` task per finding.  A closed or cancelled task does
  not block a new one.
* The table is a workspace table under row-level security (ADR 0005): tenant only,
  never through a share token.  It gets the full application grants, because the
  service of PR 2 acknowledges, reassigns, closes and cancels the tasks and the
  reminder sweep marks them.

No data changes: no existing table's columns change and no row is written here.  The
conversation purge (ADR 0021) and the backup reconciliation are code lists, updated
with this migration: the purge deletes the coaching tasks of the purged conversation
before the appeal rows, the review decisions, the human results and the review items
whose cascades would otherwise remove them uncounted, and ``backup/reconcile.py``
counts the table.

Downgrade drops the table with its indexes; every coaching task is lost.  The policy
and the grants belong to the table and go with it.  No other table's grants or
policies change in either direction.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "5f3a91c0d7b2"
down_revision: str | Sequence[str] | None = "c4a8e2f7d913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "coaching_tasks"

# Frozen copies of the check strings in ``backend/database/saas_models.py`` at this
# revision, by constraint name.
CHECKS = {
    "ck_coaching_tasks_status": "status IN ('assigned','acknowledged','closed','cancelled')",
    "ck_coaching_tasks_revision": "revision >= 1",
    "ck_coaching_tasks_finding_source": "finding_source IN ('human_result','appeal_result')",
    "ck_coaching_tasks_instruction": (
        "length(trim(instruction)) > 0 AND length(instruction) <= 2000"
    ),
    "ck_coaching_tasks_acknowledgement_note": (
        "acknowledgement_note IS NULL OR (length(acknowledgement_note) <= 2000 "
        "AND acknowledged_at IS NOT NULL)"
    ),
    "ck_coaching_tasks_follow_up_outcome": (
        "follow_up_outcome IS NULL OR follow_up_outcome IN "
        "('improved','partially_improved','not_improved')"
    ),
    "ck_coaching_tasks_follow_up_note_length": (
        "follow_up_note IS NULL OR length(follow_up_note) <= 2000"
    ),
    "ck_coaching_tasks_cancel_reason": (
        "cancel_reason IS NULL OR cancel_reason IN "
        "('finding_overturned','agent_changed','duplicate','other')"
    ),
    "ck_coaching_tasks_cancel_note_length": "cancel_note IS NULL OR length(cancel_note) <= 2000",
    "ck_coaching_tasks_assigned_fields": (
        "status <> 'assigned' OR (acknowledged_at IS NULL AND follow_up_outcome IS NULL "
        "AND follow_up_note IS NULL AND closed_at IS NULL AND cancel_reason IS NULL "
        "AND cancel_note IS NULL AND cancelled_at IS NULL)"
    ),
    "ck_coaching_tasks_acknowledged_fields": (
        "status <> 'acknowledged' OR (acknowledged_at IS NOT NULL AND follow_up_outcome "
        "IS NULL AND follow_up_note IS NULL AND closed_at IS NULL AND cancel_reason "
        "IS NULL AND cancel_note IS NULL AND cancelled_at IS NULL)"
    ),
    "ck_coaching_tasks_closed_fields": (
        "status <> 'closed' OR (acknowledged_at IS NOT NULL AND closed_at IS NOT NULL "
        "AND follow_up_outcome IS NOT NULL AND follow_up_note IS NOT NULL "
        "AND length(trim(follow_up_note)) > 0 AND cancel_reason IS NULL "
        "AND cancel_note IS NULL AND cancelled_at IS NULL)"
    ),
    "ck_coaching_tasks_cancelled_fields": (
        "status <> 'cancelled' OR (cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL "
        "AND closed_at IS NULL AND follow_up_outcome IS NULL AND follow_up_note IS NULL)"
    ),
    "ck_coaching_tasks_criterion_key": (
        "length(criterion_id) BETWEEN 2 AND 64 "
        "AND trim(substr(criterion_id, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
        "AND trim(criterion_id, 'abcdefghijklmnopqrstuvwxyz0123456789_') = ''"
    ),
}
# The active statuses, as in ``COACHING_TASK_ACTIVE_WHERE``.
ACTIVE_WHERE = sa.text("status IN ('assigned','acknowledged')")
ACTIVE_INDEX = "uq_coaching_tasks_active_finding"
FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "meeting_id": ("meetings", "CASCADE"),
    "agent_user_id": ("users", "SET NULL"),
    "owner_user_id": ("users", "SET NULL"),
    "created_by_user_id": ("users", "SET NULL"),
    "closed_by_user_id": ("users", "SET NULL"),
    "cancelled_by_user_id": ("users", "SET NULL"),
}
INDEXES = (
    ("ix_coaching_tasks_workspace_id", ("workspace_id",)),
    ("ix_coaching_tasks_queue", ("workspace_id", "status", "due_at", "id")),
    ("ix_coaching_tasks_agent", ("workspace_id", "agent_user_id", "created_at", "id")),
    ("ix_coaching_tasks_due", ("status", "due_at")),
    ("ix_coaching_tasks_workspace_meeting", ("workspace_id", "meeting_id")),
)
ITEM_KEY = ["review_item_id", "scorecard_result_id"]
ITEM_TARGET = ["review_items.id", "review_items.scorecard_result_id"]
RESULT_KEY = ["scorecard_result_id", "workspace_id", "meeting_id"]
RESULT_TARGET = [
    "scorecard_results.id",
    "scorecard_results.workspace_id",
    "scorecard_results.meeting_id",
]
CRITERION_KEY = ["scorecard_result_id", "criterion_id"]
CRITERION_TARGET = [
    "scorecard_criterion_results.result_id",
    "scorecard_criterion_results.criterion_id",
]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("review_item_id", sa.String(length=36), nullable=False),
        sa.Column("scorecard_result_id", sa.String(length=36), nullable=False),
        sa.Column("criterion_id", sa.String(length=64), nullable=False),
        sa.Column("finding_source", sa.String(length=20), nullable=False),
        sa.Column("agent_user_id", sa.String(length=36), nullable=True),
        sa.Column("owner_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledgement_note", sa.Text(), nullable=True),
        sa.Column("follow_up_outcome", sa.String(length=30), nullable=True),
        sa.Column("follow_up_note", sa.Text(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("cancel_reason", sa.String(length=30), nullable=True),
        sa.Column("cancel_note", sa.Text(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("reminder_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("overdue_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *(
            sa.ForeignKeyConstraint([column], [f"{target}.id"], ondelete=action)
            for column, (target, action) in FOREIGN_KEYS.items()
        ),
        sa.ForeignKeyConstraint(
            ITEM_KEY, ITEM_TARGET, ondelete="CASCADE", name="fk_coaching_tasks_item"
        ),
        sa.ForeignKeyConstraint(
            RESULT_KEY, RESULT_TARGET, ondelete="CASCADE", name="fk_coaching_tasks_result"
        ),
        sa.ForeignKeyConstraint(
            ["review_item_id"],
            ["review_results.review_item_id"],
            ondelete="CASCADE",
            name="fk_coaching_tasks_review_result",
        ),
        sa.ForeignKeyConstraint(
            CRITERION_KEY,
            CRITERION_TARGET,
            ondelete="CASCADE",
            name="fk_coaching_tasks_criterion",
        ),
        sa.PrimaryKeyConstraint("id"),
        *(sa.CheckConstraint(check, name=name) for name, check in CHECKS.items()),
    )
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        for name, columns in INDEXES:
            batch_op.create_index(name, columns, unique=False)
        batch_op.create_index(
            ACTIVE_INDEX,
            ["review_item_id", "criterion_id"],
            unique=True,
            postgresql_where=ACTIVE_WHERE,
            sqlite_where=ACTIVE_WHERE,
        )
    # The tenant policy of the new table and its grants.
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (every coaching task is lost)."""
    # The indexes, the policy and the grants belong to the table and go with it.
    op.drop_table(TABLE)
