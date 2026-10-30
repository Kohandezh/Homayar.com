"""Supervisor review queue, schema only (HOM-73, WP14-T02)

Revision ID: 484a6df74abf
Revises: 5067ea773c0b
Create Date: 2026-10-30

Adds ``review_items``, one unit of human review work (F43, F44, WP14).  An item names
one conversation (a ``meetings`` row) that a supervisor must review, with an optional
assignee and a due time.  It is the queue entry that criterion override (HOM-74),
appeals, calibration and coaching (F46, F47) and the console queue page (HOM-265)
build on.  Nothing writes the table yet; the service and the routes follow in PR 2.

* ``source`` is ``manual`` (a supervisor or admin put the conversation in the queue)
  or ``sample`` (an admin enqueued the selections of a seeded review sample draw,
  HOM-72).  ``source_details`` is ``{}`` for a manual item and the draw's versions,
  digests and reasons for a sampled one.
* ``status`` is ``open`` or ``in_review`` (active), or ``completed`` or ``cancelled``
  (terminal).  Four checks tie each status to its timestamps, and ``cancel_reason``
  to a cancelled item.  The reason is a code from a frozen list, never free text.
* ``revision`` starts at 1 and grows by one with each change, so a write is a
  compare-and-set on it and two concurrent writes never both succeed (F44).
* ``fk_review_items_scorecard_result`` points (``scorecard_result_id``,
  ``workspace_id``, ``meeting_id``) at ``uq_scorecard_result_scope``
  (``id``, ``workspace_id``, ``meeting_id``) of ``scorecard_results``, so an item names
  only a result of its own conversation.  A NULL result id is not checked (MATCH
  SIMPLE).
* ``uq_review_items_active_meeting`` is a partial unique index on (``workspace_id``,
  ``meeting_id``) over the active statuses: at most one active item per conversation,
  and a finished one does not block a new one.

The checks copy the frozen lists of ``backend/database/saas_models.py``.  They use no
regex, so SQLite runs the same rules.  A check passes when its expression is NULL, so
each one is false, never NULL, for a row it refuses.  No check names a user column:
the three user columns are SET NULL when a user is removed, and a check on them would
make that removal fail.  Indexes: ``workspace_id``, ``meeting_id``, the assignee and
the scorecard result, and ``ix_review_items_queue`` (``workspace_id``, ``status``,
``due_at``, ``id``) for the queue order and its keyset pages.

Why not immutable: the service changes the status, the assignee and the due time, and
the conversation purge deletes the rows.  So the table is a workspace table under
row-level security (tenant only, never through a share token) with the full
application grants, and it is in none of ``APPEND_ONLY_TABLES``, ``NO_DELETE_TABLES``
and ``NO_UPDATE_TABLES``.  Why the purge removes the rows: an item is review work on a
conversation and can name a stored result of it.  The purge deletes the items before
the results.  Cascades from a removed workspace, conversation or scorecard result also
remove the rows.

No data changes: no existing table changes and no row is written here.

Downgrade drops the indexes and the table; every review item is lost.  The policy and
the grants belong to the table and go with it.  No other table's grants or policies
change in either direction.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "484a6df74abf"
down_revision: str | Sequence[str] | None = "5067ea773c0b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "review_items"

# Frozen copies of the check strings in ``backend/database/saas_models.py`` at this
# revision, by constraint name.
CHECKS = {
    "ck_review_items_status": "status IN ('open','in_review','completed','cancelled')",
    "ck_review_items_source": "source IN ('manual','sample')",
    "ck_review_items_cancel_reason": (
        "cancel_reason IS NULL OR "
        "cancel_reason IN ('duplicate','out_of_scope','meeting_unusable','other')"
    ),
    "ck_review_items_revision": "revision >= 1",
    "ck_review_items_open_fields": (
        "status <> 'open' OR (started_at IS NULL AND completed_at IS NULL "
        "AND cancelled_at IS NULL AND cancel_reason IS NULL)"
    ),
    "ck_review_items_in_review_fields": (
        "status <> 'in_review' OR (started_at IS NOT NULL AND completed_at IS NULL "
        "AND cancelled_at IS NULL AND cancel_reason IS NULL)"
    ),
    "ck_review_items_completed_fields": (
        "status <> 'completed' OR (started_at IS NOT NULL AND completed_at IS NOT NULL "
        "AND cancelled_at IS NULL AND cancel_reason IS NULL)"
    ),
    "ck_review_items_cancelled_fields": (
        "status <> 'cancelled' OR (cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL "
        "AND completed_at IS NULL)"
    ),
}
# The active statuses, as in ``REVIEW_ITEM_ACTIVE_WHERE``.
ACTIVE_WHERE = sa.text("status IN ('open','in_review')")
FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "meeting_id": ("meetings", "CASCADE"),
    "assignee_user_id": ("users", "SET NULL"),
    "assigned_by_user_id": ("users", "SET NULL"),
    "created_by_user_id": ("users", "SET NULL"),
}
INDEXED_COLUMNS = ("workspace_id", "meeting_id", "assignee_user_id", "scorecard_result_id")
QUEUE_INDEX = "ix_review_items_queue"
ACTIVE_INDEX = "uq_review_items_active_meeting"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("scorecard_result_id", sa.String(length=36), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_details", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("assignee_user_id", sa.String(length=36), nullable=True),
        sa.Column("assigned_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.String(length=32), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        *(
            sa.ForeignKeyConstraint([column], [f"{target}.id"], ondelete=action)
            for column, (target, action) in FOREIGN_KEYS.items()
        ),
        sa.ForeignKeyConstraint(
            ["scorecard_result_id", "workspace_id", "meeting_id"],
            [
                "scorecard_results.id",
                "scorecard_results.workspace_id",
                "scorecard_results.meeting_id",
            ],
            ondelete="CASCADE",
            name="fk_review_items_scorecard_result",
        ),
        sa.PrimaryKeyConstraint("id"),
        *(sa.CheckConstraint(check, name=name) for name, check in CHECKS.items()),
    )
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        for column in INDEXED_COLUMNS:
            batch_op.create_index(batch_op.f(f"ix_{TABLE}_{column}"), [column], unique=False)
        batch_op.create_index(QUEUE_INDEX, ["workspace_id", "status", "due_at", "id"], unique=False)
        batch_op.create_index(
            ACTIVE_INDEX,
            ["workspace_id", "meeting_id"],
            unique=True,
            postgresql_where=ACTIVE_WHERE,
            sqlite_where=ACTIVE_WHERE,
        )
    # The tenant policy of the new table and its grants.
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (every review item is lost)."""
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.drop_index(ACTIVE_INDEX)
        batch_op.drop_index(QUEUE_INDEX)
        for column in INDEXED_COLUMNS:
            batch_op.drop_index(batch_op.f(f"ix_{TABLE}_{column}"))
    op.drop_table(TABLE)
