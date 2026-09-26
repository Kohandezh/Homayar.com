"""tombstone journal markers (WP07-T04)

Revision ID: f3d7b1e9a5c4
Revises: e6c2a9f4d1b8
Create Date: 2026-09-23 18:00:00.000000

Adds ``conversation_purges.journaled_at`` (the outbox marker of the tombstone
journal kept outside the backups) and ``reapplied_at`` (when a restore last
reapplied the tombstone). Existing purge rows start unjournaled, so the next
retention sweep writes them to the journal. Downgrade drops both columns.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3d7b1e9a5c4"
down_revision: str | Sequence[str] | None = "e6c2a9f4d1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("conversation_purges", schema=None) as batch_op:
        batch_op.add_column(sa.Column("journaled_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("reapplied_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("conversation_purges", schema=None) as batch_op:
        batch_op.drop_column("reapplied_at")
        batch_op.drop_column("journaled_at")
