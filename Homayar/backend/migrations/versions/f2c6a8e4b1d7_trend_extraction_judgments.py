"""trend extraction judgments: the stored trend judgment of each run (HOM-290)

Revision ID: f2c6a8e4b1d7
Revises: a7c3e9f5b2d1
Create Date: 2026-10-29 00:00:00.000000

Adds ``trend_extraction_judgments``: one immutable row per analysis run and rules
version (the trend definition id, version and digest, and ``JUDGMENT_VERSION``) with
``content_key``, the hex SHA-256 of the canonical JSON of what the HOM-79 rules judged
from the run's extraction, so the trend routes can count by that key in SQL.  The row
holds ids, versions and a digest only.  ``ix_trend_judgment_current`` (tenant, rules
version, run, covering ``content_key``) lets that count read a tenant's rows of one rules
version as one index-only range.  Tenant-only row-level security, like
``analysis_extractions``: a share token never reaches a row.

No data changes: existing rows are untouched.  No row is written here: the rules live in
Python, so the rows come from the application (HOM-290 part 2).

Downgrade drops the indexes and the table; the stored judgments are lost.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "f2c6a8e4b1d7"
down_revision: str | Sequence[str] | None = "a7c3e9f5b2d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "trend_extraction_judgments"
INDEXED = ("workspace_id", "extraction_id")
CURRENT_INDEX = "ix_trend_judgment_current"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("extraction_id", sa.String(length=36), nullable=False),
        sa.Column("analysis_run_id", sa.String(length=36), nullable=False),
        sa.Column("definition_id", sa.String(length=64), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("definition_digest", sa.String(length=64), nullable=False),
        sa.Column("judgment_version", sa.String(length=64), nullable=False),
        sa.Column("content_key", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("definition_version >= 1", name="ck_trend_judgment_definition_version"),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["extraction_id"], ["analysis_extractions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_run_id",
            "definition_digest",
            "judgment_version",
            name="uq_trend_judgment_run",
        ),
    )
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        for column in INDEXED:
            batch_op.create_index(batch_op.f(f"ix_{TABLE}_{column}"), [column], unique=False)
        batch_op.create_index(
            CURRENT_INDEX,
            ["workspace_id", "definition_digest", "judgment_version", "analysis_run_id"],
            unique=False,
            postgresql_include=["content_key"],
        )

    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (the stored judgments are lost)."""
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.drop_index(CURRENT_INDEX)
        for column in reversed(INDEXED):
            batch_op.drop_index(batch_op.f(f"ix_{TABLE}_{column}"))
    op.drop_table(TABLE)
