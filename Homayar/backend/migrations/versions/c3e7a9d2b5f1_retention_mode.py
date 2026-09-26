"""explicit workspace retention mode (WP07-T01, ADR 0020)

Revision ID: c3e7a9d2b5f1
Revises: b9d5f3a7c2e8
Create Date: 2026-09-16 16:00:00.000000

``workspaces.retention_days`` (>= 0) was the only retention field and its
zero value was ambiguous ("delete at once" or "keep forever"?); nothing
enforced either reading.  This revision adds ``retention_mode``:

* rows with ``retention_days >= 1`` become ``FINITE`` (their meaning is
  unchanged: eligible for deletion that many days after ingestion);
* rows with ``retention_days = 0`` become ``LEGACY_UNRESOLVED`` — nothing is
  reinterpreted, nothing is purged by age, and an administrator must choose
  ``indefinite``, ``immediate`` or a finite number explicitly.

Downgrade drops the column; ``retention_days`` is untouched, so the
pre-WP07 state (including the ambiguous zero) is restored losslessly.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3e7a9d2b5f1"
down_revision: str | Sequence[str] | None = "b9d5f3a7c2e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_workspace_retention_mode"
CHECK = (
    "(retention_mode = 'FINITE' AND retention_days >= 1) OR "
    "(retention_mode IN ('INDEFINITE', 'IMMEDIATE', 'LEGACY_UNRESOLVED') AND retention_days = 0)"
)


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "retention_mode",
                sa.String(length=20),
                nullable=False,
                server_default="FINITE",
            )
        )
    op.execute(
        "UPDATE workspaces SET retention_mode = 'LEGACY_UNRESOLVED' WHERE retention_days = 0"
    )
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.create_check_constraint(CONSTRAINT, CHECK)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.drop_constraint(CONSTRAINT, type_="check")
        batch_op.drop_column("retention_mode")
