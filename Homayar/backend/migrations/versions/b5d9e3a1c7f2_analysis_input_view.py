"""analysis input view provenance (WP07-T06)

Revision ID: b5d9e3a1c7f2
Revises: a8e4c2f6d0b3
Create Date: 2026-09-24 10:00:00.000000

Records which transcript text an analysis run sent to its provider:
``input_view`` (``original`` or ``redacted``) and, for a redacted input, the
policy digest (detector version + classes). Every existing run read the
original transcript, so existing rows become ``original`` through the server
default. Downgrade drops the columns and the constraint.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5d9e3a1c7f2"
down_revision: str | Sequence[str] | None = "a8e4c2f6d0b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("input_view", sa.String(length=16), server_default="original", nullable=False)
        )
        batch_op.add_column(sa.Column("input_policy_digest", sa.String(length=64), nullable=True))
        batch_op.create_check_constraint(
            "ck_analysis_run_input_view",
            "(input_view = 'original' AND input_policy_digest IS NULL) OR "
            "(input_view = 'redacted' AND input_policy_digest IS NOT NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.drop_constraint("ck_analysis_run_input_view", type_="check")
        batch_op.drop_column("input_policy_digest")
        batch_op.drop_column("input_view")
