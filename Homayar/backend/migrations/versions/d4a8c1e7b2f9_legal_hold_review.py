"""legal hold review date (WP07-T02)

Revision ID: d4a8c1e7b2f9
Revises: c3e7a9d2b5f1
Create Date: 2026-09-16 18:00:00.000000

A legal hold now carries the date by which it must be reviewed
(``media_objects.legal_hold_review_at``), present exactly when the hold is.
Existing held rows (none are expected before WP07-T02: holds had no route)
would violate the new constraint, so they receive a review date one year
after the hold was placed instead of being reinterpreted or released.

Downgrade drops the column and the constraint; holds themselves are kept.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a8c1e7b2f9"
down_revision: str | Sequence[str] | None = "c3e7a9d2b5f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_media_hold_review"
CHECK = "(legal_hold_at IS NULL) = (legal_hold_review_at IS NULL)"


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("media_objects", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("legal_hold_review_at", sa.DateTime(timezone=True), nullable=True)
        )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "UPDATE media_objects SET legal_hold_review_at = legal_hold_at + INTERVAL '365 days' "
            "WHERE legal_hold_at IS NOT NULL AND legal_hold_review_at IS NULL"
        )
    else:
        op.execute(
            "UPDATE media_objects SET legal_hold_review_at = datetime(legal_hold_at, '+365 days') "
            "WHERE legal_hold_at IS NOT NULL AND legal_hold_review_at IS NULL"
        )
    with op.batch_alter_table("media_objects", schema=None) as batch_op:
        batch_op.create_check_constraint(CONSTRAINT, CHECK)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("media_objects", schema=None) as batch_op:
        batch_op.drop_constraint(CONSTRAINT, type_="check")
        batch_op.drop_column("legal_hold_review_at")
