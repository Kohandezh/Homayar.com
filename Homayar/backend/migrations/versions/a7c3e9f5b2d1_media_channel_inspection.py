"""Channel inspection record on media objects (HOM-37, ADR 0039)

Revision ID: a7c3e9f5b2d1
Revises: c7e2a9d4f1b6
Create Date: 2026-10-22

Owner decision (SPEC §14, option B): the per-recording channel-inspection record
(``backend.media.channels.channel_inspection_record``) is kept in a new nullable JSON
column ``media_objects.channel_inspection``.

* Write-once: the record is set when the ORIGINAL object is inserted, in the ingest
  transaction, and never changed. The ORM guard on ``MediaObject`` refuses any later
  change, because the column is not one of ``MUTABLE_MEDIA_COLUMNS``.
* Nullable: objects registered before this revision, derived objects and objects of
  a disabled or failed analysis without a record stay NULL.
* No RLS change: ``media_objects`` already has the tenant policy; a new column needs
  no policy of its own.

No data changes. Downgrade drops the column (the records are lost; the previous
application never reads them).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c3e9f5b2d1"
down_revision: str | Sequence[str] | None = "c7e2a9d4f1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("media_objects", schema=None) as batch_op:
        batch_op.add_column(sa.Column("channel_inspection", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("media_objects", schema=None) as batch_op:
        batch_op.drop_column("channel_inspection")
