"""failure classes, job deadlines and bounded replays for processing jobs (WP06-T05)

Revision ID: a7d2c4e9f6b1
Revises: f1b6d8e4a2c7
Create Date: 2026-09-08 22:00:00.000000

Adds ``failure_class`` (why the job stopped: publication / transient /
permanent / abandoned / deadline / cancelled), ``deadline_at`` (overall job
deadline), ``replay_count`` and ``last_replayed_at`` (bounded operator replay
of dead-lettered jobs) to ``processing_jobs``.  Row-level security is
unchanged.

Backfill: terminal rows written before this revision get the class their
state and failure code imply; open rows (queued/running) keep a NULL deadline,
which means "no overall deadline" for that job — new jobs get one from
``JOB_DEADLINE_SECONDS``.  Downgrade drops the columns; a replayed job keeps
its (extended) ``max_attempts``.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7d2c4e9f6b1'
down_revision: str | Sequence[str] | None = 'f1b6d8e4a2c7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("processing_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("failure_class", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("replay_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("last_replayed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_check_constraint("ck_job_replays", "replay_count >= 0")

    # Classify rows that ended before the policy existed (states are stored
    # by enum name).
    op.execute(
        sa.text(
            "UPDATE processing_jobs SET failure_class = 'cancelled' "
            "WHERE state = 'CANCELLED' AND failure_class IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE processing_jobs SET failure_class = 'permanent' "
            "WHERE state = 'FAILED' AND failure_class IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE processing_jobs SET failure_class = 'abandoned' "
            "WHERE state = 'DEAD_LETTER' AND failure_code = 'lease_expired' "
            "AND failure_class IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE processing_jobs SET failure_class = 'transient' "
            "WHERE state = 'DEAD_LETTER' AND failure_class IS NULL"
        )
    )


def downgrade() -> None:
    """Downgrade schema (classification, deadlines and replay counters are lost)."""
    with op.batch_alter_table("processing_jobs", schema=None) as batch_op:
        batch_op.drop_constraint("ck_job_replays", type_="check")
        batch_op.drop_column("last_replayed_at")
        batch_op.drop_column("replay_count")
        batch_op.drop_column("deadline_at")
        batch_op.drop_column("failure_class")
