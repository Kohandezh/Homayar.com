"""Versioned QA scorecards, schema only (HOM-54, WP11-T04)

Revision ID: 47676f445509
Revises: f2c6a8e4b1d7
Create Date: 2026-10-30

Adds ``scorecard_versions``.  Every row is one published version of one scorecard of
one workspace:

* ``scorecard_key`` names the scorecard in its workspace (check
  ``ck_scorecard_key_format``: the pattern ``^[a-z][a-z0-9_]{1,63}$``, written without
  a regex so SQLite runs the same check);
* ``version`` counts from 1 per key (check ``ck_scorecard_version_positive``), and
  ``uq_scorecard_version`` keeps ``(workspace_id, scorecard_key, version)`` unique;
* ``document`` is the definition, ``scorecards_version`` the engine version that
  validated it, ``digest`` its SHA-256, and ``reason`` why it was published.

It also adds the index ``ix_scorecard_versions_workspace_id``.  The unique constraint
already serves the latest version of a key and a key's history.

Why append-only: a past score must always name the exact definition it used (F41), so
a change is a new version and a stored row never changes.  The table is a workspace
table under row-level security (tenant only, never through a share token).  The
application role may read and insert rows but never update or delete them, like
``ai_policy_versions``.  Referential cascades run as the owner and are unaffected.

No data changes: no existing row is touched and no row is written here.  There is no
draft state on the server; the editor publishes a draft as a new version.

Downgrade drops the index and the table; the stored versions are lost.  The policy and
the revoked grants belong to the table and go with it.  No other table's grants or
policies change in either direction.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "47676f445509"
down_revision: str | Sequence[str] | None = "f2c6a8e4b1d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "scorecard_versions"

# Frozen copy of ``SCORECARD_KEY_CHECK`` in ``backend/database/saas_models.py`` at this
# revision.
KEY_CHECK = (
    "length(scorecard_key) BETWEEN 2 AND 64 "
    "AND trim(substr(scorecard_key, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
    "AND trim(scorecard_key, 'abcdefghijklmnopqrstuvwxyz0123456789_') = ''"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("scorecard_key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("scorecards_version", sa.String(length=40), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_scorecard_version_positive"),
        sa.CheckConstraint(KEY_CHECK, name="ck_scorecard_key_format"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "scorecard_key", "version", name="uq_scorecard_version"
        ),
    )
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f(f"ix_{TABLE}_workspace_id"), ["workspace_id"], unique=False
        )
    # The tenant policy of the new table, the grants, and the append-only revoke.
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (the stored versions are lost)."""
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.drop_index(batch_op.f(f"ix_{TABLE}_workspace_id"))
    op.drop_table(TABLE)
