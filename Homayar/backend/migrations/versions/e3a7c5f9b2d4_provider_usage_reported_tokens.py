"""provider-reported token usage may be absent (WP08-T04)

Revision ID: e3a7c5f9b2d4
Revises: d2b6f8a4c1e7
Create Date: 2026-09-24 23:30:00.000000

``provider_usage.input_tokens`` / ``output_tokens`` become nullable: null
means the provider did not report the value, which is different from zero.
No adapter reported tokens before this revision, so existing rows carry 0 for
"not reported"; they are left unchanged (a data rewrite here would run under
forced row-level security and could silently skip rows). Downgrade restores
NOT NULL with a table rewrite (``USING COALESCE(..., 0)``), which is DDL and
therefore not filtered by row-level security.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3a7c5f9b2d4"
down_revision: str | Sequence[str] | None = "d2b6f8a4c1e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("input_tokens", "output_tokens")


def upgrade() -> None:
    with op.batch_alter_table("provider_usage", schema=None) as batch_op:
        for column in COLUMNS:
            batch_op.alter_column(column, existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    for column in COLUMNS:
        if bind.dialect.name == "postgresql":
            op.execute(
                f"ALTER TABLE provider_usage ALTER COLUMN {column} TYPE integer "
                f"USING COALESCE({column}, 0)"
            )
        else:  # SQLite (development): no row-level security
            op.execute(f"UPDATE provider_usage SET {column} = COALESCE({column}, 0)")  # noqa: S608
    with op.batch_alter_table("provider_usage", schema=None) as batch_op:
        for column in COLUMNS:
            batch_op.alter_column(column, existing_type=sa.Integer(), nullable=False)
