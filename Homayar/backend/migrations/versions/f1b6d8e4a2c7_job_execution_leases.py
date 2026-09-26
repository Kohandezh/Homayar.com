"""execution leases and fencing generations for processing jobs (WP06-T04)

Revision ID: f1b6d8e4a2c7
Revises: e5a7c3d9b1f2
Create Date: 2026-09-08 18:00:00.000000

Adds the lease columns (``lease_generation``, ``attempt_id``,
``lease_expires_at``, ``lease_renewed_at``) to ``processing_jobs`` and the
arming ``generation`` to ``job_dispatches``; extends the ``processing_jobs``
row-level-security policy with the read-only ``recovery`` service scope.

Backfill: a job that is RUNNING at upgrade time was started by a worker that
never renewed a lease, so its lease is set to expire immediately. The first
recovery sweep fences it and requeues it (or parks it when its attempts are
used up). Drain workers before upgrading so no pre-lease worker keeps
writing results without a fence.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = 'f1b6d8e4a2c7'
down_revision: str | Sequence[str] | None = 'e5a7c3d9b1f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNNING_HAS_LEASE = (
    "(state = 'RUNNING' AND locked_by IS NOT NULL AND lease_expires_at IS NOT NULL) "
    "OR (state <> 'RUNNING' AND locked_by IS NULL AND lease_expires_at IS NULL)"
)


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("processing_jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("lease_generation", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("attempt_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("lease_renewed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_check_constraint("ck_job_lease_generation", "lease_generation >= 0")
        batch_op.create_index("ix_job_lease_expiry", ["state", "lease_expires_at"], unique=False)

    # Backfill before the lease invariant is enforced: running jobs get an
    # already-expired lease (recovered by the first sweep); anything not
    # running must not carry an owner.
    op.execute(
        sa.text(
            "UPDATE processing_jobs SET locked_by = COALESCE(locked_by, 'pre-lease-worker'), "
            "lease_expires_at = CURRENT_TIMESTAMP, lease_renewed_at = locked_at "
            "WHERE state = 'RUNNING' AND lease_expires_at IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE processing_jobs SET locked_by = NULL, lease_expires_at = NULL, "
            "lease_renewed_at = NULL, attempt_id = NULL "
            "WHERE state <> 'RUNNING' AND (locked_by IS NOT NULL OR lease_expires_at IS NOT NULL)"
        )
    )
    with op.batch_alter_table("processing_jobs", schema=None) as batch_op:
        batch_op.create_check_constraint("ck_job_running_has_lease", RUNNING_HAS_LEASE)

    with op.batch_alter_table("job_dispatches", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("generation", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.create_check_constraint("ck_dispatch_generation", "generation >= 0")

    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (lease ownership is lost; running jobs keep locked_by/at)."""
    with op.batch_alter_table("job_dispatches", schema=None) as batch_op:
        batch_op.drop_constraint("ck_dispatch_generation", type_="check")
        batch_op.drop_column("generation")

    with op.batch_alter_table("processing_jobs", schema=None) as batch_op:
        batch_op.drop_constraint("ck_job_running_has_lease", type_="check")
        batch_op.drop_index("ix_job_lease_expiry")
        batch_op.drop_constraint("ck_job_lease_generation", type_="check")
        batch_op.drop_column("lease_renewed_at")
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("attempt_id")
        batch_op.drop_column("lease_generation")

    apply_row_level_security(op.get_bind())
