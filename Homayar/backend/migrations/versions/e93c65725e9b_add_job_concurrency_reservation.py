"""add job concurrency reservation

Revision ID: e93c65725e9b
Revises: ada201e253df
Create Date: 2026-08-18 19:43:57.010329

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e93c65725e9b"
down_revision: str | Sequence[str] | None = "ada201e253df"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("processing_jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("concurrency_reservation_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_processing_jobs_concurrency_reservation_id",
            ["concurrency_reservation_id"],
        )
        batch_op.create_foreign_key(
            "fk_processing_jobs_concurrency_reservation_id",
            "usage_reservations",
            ["concurrency_reservation_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("processing_jobs", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_processing_jobs_concurrency_reservation_id",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "uq_processing_jobs_concurrency_reservation_id",
            type_="unique",
        )
        batch_op.drop_column("concurrency_reservation_id")
