"""Conversation columns on meetings (HOM-58, WP12-T01)

Revision ID: 6d4ec2d2e498
Revises: a7c3e9f5b2d1
Create Date: 2026-10-29

A conversation (a call, a meeting, a voicemail or another source type) is a row of
``meetings``.  This revision adds five columns to that table:

* ``conversation_type`` (``call``, ``meeting``, ``voicemail`` or ``other``; NOT NULL,
  server default ``'call'``, check ``ck_meeting_conversation_type``);
* ``direction`` (NULL for "not known", or ``inbound``, ``outbound``, ``internal``;
  check ``ck_meeting_direction``);
* ``external_source_system`` and ``external_conversation_id``, the conversation's id
  in its source system (both NULL or both set; check ``ck_meeting_external_ref``);
* ``started_at``, the time the conversation happened at its source.  It is not
  ``created_at``, which is the upload time.

It also adds the indexes ``ix_meeting_workspace_external`` and
``ix_meeting_workspace_type_created``.

Backfill: rows with ``source = 'voicemail'`` get ``conversation_type = 'voicemail'``;
every other row gets ``'call'`` (the server default).  The other four columns stay
NULL.  No row, id or child row changes.

The backfill lifts FORCE ROW LEVEL SECURITY on ``meetings`` for its one update: a
table owner that is not a superuser (the deployment shape of ADR 0005) sees no rows
under the forced tenant policy, so the update would change nothing.  FORCE is set
again right after the update, before the migration commits.  SQLite has no RLS, so
there only the update runs.

Why no rename: about 40 foreign keys, the row-level-security policies and raw SQL
point at ``meetings.id``.  Renaming or copying the table could lose ids and break
APIs; adding columns keeps every id, foreign key and API as it is.

Why the external reference is not unique: the ingestion ledger (ADR 0016) already
handles duplicate delivery.  A unique index would make a re-sent or re-ingested
conversation fail.

No RLS policy change: there is no new table, and ``meetings`` already has its
tenant policy.  New columns need no policy of their own.

Downgrade drops the two indexes, the three checks and the five columns.  It keeps
every meeting row and id, but the values of the five columns are lost.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6d4ec2d2e498"
down_revision: str | Sequence[str] | None = "a7c3e9f5b2d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of the checks in ``backend/database/saas_models.py`` at this revision.
TYPE_CHECK = "conversation_type IN ('call', 'meeting', 'voicemail', 'other')"
DIRECTION_CHECK = "direction IS NULL OR direction IN ('inbound', 'outbound', 'internal')"
EXTERNAL_REF_CHECK = (
    "(external_source_system IS NULL AND external_conversation_id IS NULL) OR "
    "(external_source_system IS NOT NULL AND external_conversation_id IS NOT NULL)"
)


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("meetings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "conversation_type", sa.String(length=20), server_default="call", nullable=False
            )
        )
        batch_op.add_column(sa.Column("direction", sa.String(length=12), nullable=True))
        batch_op.add_column(
            sa.Column("external_source_system", sa.String(length=60), nullable=True)
        )
        batch_op.add_column(
            sa.Column("external_conversation_id", sa.String(length=200), nullable=True)
        )
        batch_op.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))

    # The server default already gave every existing row 'call'.
    backfill_voicemail_type(op.get_bind())

    with op.batch_alter_table("meetings", schema=None) as batch_op:
        batch_op.create_check_constraint("ck_meeting_conversation_type", TYPE_CHECK)
        batch_op.create_check_constraint("ck_meeting_direction", DIRECTION_CHECK)
        batch_op.create_check_constraint("ck_meeting_external_ref", EXTERNAL_REF_CHECK)
        batch_op.create_index(
            "ix_meeting_workspace_external",
            ["workspace_id", "external_source_system", "external_conversation_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_meeting_workspace_type_created",
            ["workspace_id", "conversation_type", "created_at"],
            unique=False,
        )


def backfill_voicemail_type(connection: sa.Connection) -> int:
    """Set ``conversation_type = 'voicemail'`` on every row with ``source = 'voicemail'``.

    Returns the number of rows changed.  The policy is FORCED, so a table owner that
    is not a superuser sees no rows without a tenant: lift FORCE for this update only
    (as c7e2a9d4f1b6 and a2f7c4e9d1b3 do).
    """
    postgres = connection.dialect.name == "postgresql"
    if postgres:
        connection.execute(sa.text("ALTER TABLE meetings NO FORCE ROW LEVEL SECURITY"))
    try:
        result = connection.execute(
            sa.text(
                "UPDATE meetings SET conversation_type = 'voicemail' WHERE source = 'voicemail'"
            )
        )
    finally:
        if postgres:
            connection.execute(sa.text("ALTER TABLE meetings FORCE ROW LEVEL SECURITY"))
    return result.rowcount


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("meetings", schema=None) as batch_op:
        batch_op.drop_index("ix_meeting_workspace_type_created")
        batch_op.drop_index("ix_meeting_workspace_external")
        batch_op.drop_constraint("ck_meeting_external_ref", type_="check")
        batch_op.drop_constraint("ck_meeting_direction", type_="check")
        batch_op.drop_constraint("ck_meeting_conversation_type", type_="check")
        batch_op.drop_column("started_at")
        batch_op.drop_column("external_conversation_id")
        batch_op.drop_column("external_source_system")
        batch_op.drop_column("direction")
        batch_op.drop_column("conversation_type")
