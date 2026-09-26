"""durable conversation purge (WP07-T03)

Revision ID: e6c2a9f4d1b8
Revises: d4a8c1e7b2f9
Create Date: 2026-09-23 12:00:00.000000

Adds ``conversation_purges``: one row per tombstoned conversation carrying
the durable purge (attempts, schedule, error code, counters) and, once
completed, the non-content receipt.  Tenant-scoped like every workspace
table (row-level security re-applied after the DDL).

No backfill: conversations tombstoned before WP07-T03 get no purge row, so
deploying this revision never removes existing customer content by itself;
enqueueing them is an explicit operator decision (ADR 0021).

Downgrade drops the table (purge history and receipts are lost; tombstones
on ``meetings`` stay).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "e6c2a9f4d1b8"
down_revision: str | Sequence[str] | None = "d4a8c1e7b2f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "conversation_purges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=80), nullable=True),
        sa.Column("content_purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_rows_removed", sa.Integer(), nullable=False),
        sa.Column("objects_removed", sa.Integer(), nullable=False),
        sa.Column("objects_held", sa.Integer(), nullable=False),
        sa.Column("objects_pending", sa.Integer(), nullable=False),
        sa.CheckConstraint("state IN ('PENDING', 'COMPLETED', 'FAILED')", name="ck_purge_state"),
        sa.CheckConstraint("attempts >= 0", name="ck_purge_attempts"),
        sa.CheckConstraint(
            "(state = 'COMPLETED') = (completed_at IS NOT NULL)", name="ck_purge_completed"
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("meeting_id"),
    )
    with op.batch_alter_table("conversation_purges", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_conversation_purges_workspace_id"), ["workspace_id"], unique=False
        )
        batch_op.create_index(
            "ix_purge_workspace_state_due",
            ["workspace_id", "state", "next_attempt_at"],
            unique=False,
        )
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("conversation_purges", schema=None) as batch_op:
        batch_op.drop_index("ix_purge_workspace_state_due")
        batch_op.drop_index(batch_op.f("ix_conversation_purges_workspace_id"))
    op.drop_table("conversation_purges")
