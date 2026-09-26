"""transactional outbox for job dispatch (WP06-T02)

Revision ID: e5a7c3d9b1f2
Revises: d7f3a1c9e2b4
Create Date: 2026-09-08 15:00:00.000000

Creates ``job_dispatches`` and backfills one PENDING row per job that is still
QUEUED, so work accepted before this release is picked up by the sweeper
instead of relying on a broker message that may never have been sent.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = 'e5a7c3d9b1f2'
down_revision: str | Sequence[str] | None = 'd7f3a1c9e2b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "job_dispatches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "PENDING", "PUBLISHED", "FAILED", "CANCELLED",
                name="dispatchstate", native_enum=False, length=16,
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message_id", sa.String(length=120), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_dispatch_attempts"),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index(
        "ix_job_dispatch_state_due", "job_dispatches", ["state", "next_attempt_at"], unique=False
    )
    op.create_index(
        op.f("ix_job_dispatches_workspace_id"), "job_dispatches", ["workspace_id"], unique=False
    )

    # Backfill: every still-queued job gets a due PENDING intent (ids are
    # derived from the job id so a re-run of this statement is harmless).
    op.execute(
        sa.text(
            "INSERT INTO job_dispatches (id, workspace_id, job_id, state, attempt_count, "
            "max_attempts, next_attempt_at, created_at, updated_at) "
            "SELECT substr(j.id, 1, 36), j.workspace_id, j.id, 'PENDING', 0, 8, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "FROM processing_jobs j WHERE j.state = 'QUEUED' "
            "AND NOT EXISTS (SELECT 1 FROM job_dispatches d WHERE d.job_id = j.id)"
        )
    )

    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (pending publish intents are lost)."""
    op.drop_index(op.f("ix_job_dispatches_workspace_id"), table_name="job_dispatches")
    op.drop_index("ix_job_dispatch_state_due", table_name="job_dispatches")
    op.drop_table("job_dispatches")
