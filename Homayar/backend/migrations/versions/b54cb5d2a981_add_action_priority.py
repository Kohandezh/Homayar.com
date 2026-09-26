"""add action item priority

Revision ID: b54cb5d2a981
Revises: e93c65725e9b
Create Date: 2026-08-18 22:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b54cb5d2a981"
down_revision: str | Sequence[str] | None = "e93c65725e9b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("action_items", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "priority",
                sa.String(length=16),
                nullable=False,
                server_default="medium",
            )
        )
        batch_op.create_check_constraint(
            "ck_action_priority",
            "priority IN ('low', 'medium', 'high')",
        )


def downgrade() -> None:
    with op.batch_alter_table("action_items", schema=None) as batch_op:
        batch_op.drop_constraint("ck_action_priority", type_="check")
        batch_op.drop_column("priority")
