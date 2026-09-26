"""evidence spans and evidence status (WP04-T03, ADR 0014)

Revision ID: e6a9c3d7f2b5
Revises: d4f8b2c6e9a1
Create Date: 2026-09-11 00:30:00.000000

Adds ``evidence_spans``: one immutable citation per finding reference that
resolved to a segment of the analysed transcript revision (revision, segment,
speaker, character range, original audio offsets, provenance type), joined to
the shared meeting-content row-level-security policy.  ``action_items`` and
``meeting_summaries`` gain ``evidence_status`` (sufficient / partial /
insufficient; existing rows are ``insufficient`` because nothing recorded
their references), ``analysis_runs`` gains the offered / rejected reference
counts.

Downgrade drops the table and the columns; the citations are lost.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "e6a9c3d7f2b5"
down_revision: str | Sequence[str] | None = "d4f8b2c6e9a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVIDENCE_STATUS_CHECK = "evidence_status IN ('sufficient', 'partial', 'insufficient')"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "evidence_spans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("analysis_run_id", sa.String(length=36), nullable=True),
        sa.Column("transcript_revision_id", sa.String(length=36), nullable=False),
        sa.Column("segment_id", sa.String(length=36), nullable=False),
        sa.Column("speaker_key", sa.String(length=60), nullable=False),
        sa.Column("finding_type", sa.String(length=24), nullable=False),
        sa.Column("finding_id", sa.String(length=36), nullable=True),
        sa.Column("finding_index", sa.Integer(), nullable=True),
        sa.Column("text_start", sa.Integer(), nullable=False),
        sa.Column("text_end", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("evidence_type", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("text_start >= 0 AND text_end >= text_start", name="ck_evidence_text"),
        sa.CheckConstraint("start_ms >= 0 AND end_ms >= start_ms", name="ck_evidence_audio"),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_evidence_confidence",
        ),
        sa.CheckConstraint(
            "finding_type IN ('summary', 'insight', 'action_item')", name="ck_evidence_finding"
        ),
        sa.CheckConstraint("evidence_type IN ('turn_reference', 'human')", name="ck_evidence_type"),
        sa.CheckConstraint(
            "finding_index IS NULL OR finding_index >= 0", name="ck_evidence_finding_index"
        ),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["segment_id"], ["transcript_segments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["transcript_revision_id"], ["transcript_revisions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("evidence_spans", schema=None) as batch_op:
        for column in (
            "workspace_id",
            "meeting_id",
            "analysis_run_id",
            "transcript_revision_id",
            "segment_id",
            "finding_id",
        ):
            batch_op.create_index(batch_op.f(f"ix_evidence_spans_{column}"), [column], unique=False)
        batch_op.create_index(
            "ix_evidence_workspace_meeting", ["workspace_id", "meeting_id"], unique=False
        )
        batch_op.create_index(
            "ix_evidence_run_finding",
            ["analysis_run_id", "finding_type", "finding_index"],
            unique=False,
        )

    for table, constraint in (
        ("action_items", "ck_action_evidence_status"),
        ("meeting_summaries", "ck_summary_evidence_status"),
    ):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "evidence_status",
                    sa.String(length=16),
                    nullable=False,
                    server_default="insufficient",
                )
            )
            batch_op.create_check_constraint(constraint, EVIDENCE_STATUS_CHECK)

    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("reference_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("rejected_reference_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.create_check_constraint(
            "ck_analysis_run_references",
            "reference_count >= 0 AND rejected_reference_count >= 0 "
            "AND rejected_reference_count <= reference_count",
        )

    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (citations and evidence statuses are lost)."""
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.drop_constraint("ck_analysis_run_references", type_="check")
        batch_op.drop_column("rejected_reference_count")
        batch_op.drop_column("reference_count")
    for table, constraint in (
        ("meeting_summaries", "ck_summary_evidence_status"),
        ("action_items", "ck_action_evidence_status"),
    ):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(constraint, type_="check")
            batch_op.drop_column("evidence_status")
    with op.batch_alter_table("evidence_spans", schema=None) as batch_op:
        batch_op.drop_index("ix_evidence_run_finding")
        batch_op.drop_index("ix_evidence_workspace_meeting")
        for column in (
            "finding_id",
            "segment_id",
            "transcript_revision_id",
            "analysis_run_id",
            "meeting_id",
            "workspace_id",
        ):
            batch_op.drop_index(batch_op.f(f"ix_evidence_spans_{column}"))
    op.drop_table("evidence_spans")
