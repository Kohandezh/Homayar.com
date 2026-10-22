"""analysis extractions: the judged taxonomy extraction of each run (HOM-53)

Revision ID: c7e2a9d4f1b6
Revises: e6c3a9f1d4b8
Create Date: 2026-10-22 00:00:00.000000

Adds ``analysis_extractions``: one immutable row per analysis run with the judged
taxonomy extraction (the policy result as JSON, with the taxonomy, validator and
policy versions and digests that produced it) and the columns a review list needs
(``valid``, ``needs_review``, ``review_count``).  Tenant-only row-level security:
entity values can be personal data, so a share token never reaches a row.
``evidence_spans.finding_type`` may now be ``extraction``: the citation of a kept
value.

No data changes: existing rows are untouched.

Downgrade deletes the ``extraction`` evidence spans (the old check cannot be
restored while one exists), restores the old check and drops the table; the
extractions and their citations are lost.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "c7e2a9d4f1b6"
down_revision: str | Sequence[str] | None = "e6c3a9f1d4b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FINDING_CONSTRAINT = "ck_evidence_finding"
FINDINGS = "finding_type IN ('summary', 'insight', 'action_item', 'extraction')"
PREVIOUS_FINDINGS = "finding_type IN ('summary', 'insight', 'action_item')"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "analysis_extractions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("analysis_run_id", sa.String(length=36), nullable=False),
        sa.Column("taxonomy_id", sa.String(length=64), nullable=False),
        sa.Column("taxonomy_version", sa.Integer(), nullable=False),
        sa.Column("taxonomy_digest", sa.String(length=64), nullable=False),
        sa.Column("validator_version", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("policy_digest", sa.String(length=64), nullable=False),
        sa.Column("valid", sa.Boolean(), nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("review_count", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("taxonomy_version >= 1", name="ck_analysis_extraction_taxonomy_version"),
        sa.CheckConstraint("review_count >= 0", name="ck_analysis_extraction_review_count"),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("analysis_run_id", name="uq_analysis_extraction_run"),
    )
    with op.batch_alter_table("analysis_extractions", schema=None) as batch_op:
        for column in ("workspace_id", "meeting_id"):
            batch_op.create_index(
                batch_op.f(f"ix_analysis_extractions_{column}"), [column], unique=False
            )
        batch_op.create_index(
            "ix_analysis_extraction_review", ["workspace_id", "needs_review"], unique=False
        )

    with op.batch_alter_table("evidence_spans", schema=None) as batch_op:
        batch_op.drop_constraint(FINDING_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(FINDING_CONSTRAINT, FINDINGS)

    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (the extractions and their citations are lost)."""
    connection = op.get_bind()
    postgres = connection.dialect.name == "postgresql"
    # The policies are FORCED, so a table owner that is not a superuser sees no rows
    # without a tenant: lift FORCE for this delete only (as a2f7c4e9d1b3 does).
    if postgres:
        connection.execute(sa.text("ALTER TABLE evidence_spans NO FORCE ROW LEVEL SECURITY"))
    try:
        connection.execute(sa.text("DELETE FROM evidence_spans WHERE finding_type = 'extraction'"))
    finally:
        if postgres:
            connection.execute(sa.text("ALTER TABLE evidence_spans FORCE ROW LEVEL SECURITY"))
    with op.batch_alter_table("evidence_spans", schema=None) as batch_op:
        batch_op.drop_constraint(FINDING_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(FINDING_CONSTRAINT, PREVIOUS_FINDINGS)

    with op.batch_alter_table("analysis_extractions", schema=None) as batch_op:
        batch_op.drop_index("ix_analysis_extraction_review")
        for column in ("meeting_id", "workspace_id"):
            batch_op.drop_index(batch_op.f(f"ix_analysis_extractions_{column}"))
    op.drop_table("analysis_extractions")
