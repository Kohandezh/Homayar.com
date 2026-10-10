"""Derived normalised text views and entities of transcript revisions (WP22/F36).

Revision ID: d8a2f4c6b1e3
Revises: c5e7a9b1d3f6
Create Date: 2026-10-10

Adds three tenant tables (ADR 0034): ``transcript_text_views`` (one derived
view per revision and view digest: source view, normaliser/extractor/detector
versions, honest state), ``text_view_segments`` (the normalised text of each
segment) and ``text_view_entities`` (entities with offsets into the source
text).  All three are workspace tables under forced row-level security, never
reachable through a share token; RLS is re-applied after the DDL.

Upgrade creates empty tables: nothing is backfilled (conversations processed
before it have no derived view until they are processed again).  Transcript
rows are not touched.  Downgrade drops the three tables (derived views are
lost; transcripts, redactions and analyses are untouched).  The previous
application ignores the tables, so it may run against the upgraded schema.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "d8a2f4c6b1e3"
down_revision: str | Sequence[str] | None = "c5e7a9b1d3f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGE_STATES = (
    "QUEUED",
    "RUNNING",
    "COMPLETED",
    "PARTIAL",
    "FAILED",
    "UNAVAILABLE",
    "INSUFFICIENT_EVIDENCE",
    "SKIPPED",
)


def _tenant_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
    ]


def _tenant_fks() -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
    ]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "transcript_text_views",
        *_tenant_columns(),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("redaction_id", sa.String(length=36), nullable=True),
        sa.Column("input_view", sa.String(length=16), nullable=False),
        sa.Column("input_policy_digest", sa.String(length=64), nullable=True),
        sa.Column("language", sa.String(length=12), nullable=False),
        sa.Column("normalizer_version", sa.String(length=40), nullable=False),
        sa.Column("extractor_version", sa.String(length=40), nullable=False),
        sa.Column("detector_version", sa.String(length=40), nullable=False),
        sa.Column("view_digest", sa.String(length=64), nullable=False),
        sa.Column("reference_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=40), nullable=False),
        sa.Column(
            "state",
            sa.Enum(*STAGE_STATES, name="stagestate", native_enum=False, length=24),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("segment_count", sa.Integer(), nullable=False),
        sa.Column("entity_count", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('COMPLETED', 'PARTIAL', 'FAILED', 'SKIPPED')", name="ck_text_view_state"
        ),
        sa.CheckConstraint(
            "(input_view = 'original' AND input_policy_digest IS NULL "
            "AND redaction_id IS NULL) OR "
            "(input_view = 'redacted' AND input_policy_digest IS NOT NULL)",
            name="ck_text_view_input",
        ),
        sa.CheckConstraint(
            "input_view = 'original' OR redaction_id IS NOT NULL OR state IN ('FAILED', 'SKIPPED')",
            name="ck_text_view_source",
        ),
        sa.CheckConstraint("segment_count >= 0 AND entity_count >= 0", name="ck_text_view_counts"),
        sa.CheckConstraint("attempt IS NULL OR attempt >= 0", name="ck_text_view_attempt"),
        *_tenant_fks(),
        sa.ForeignKeyConstraint(["revision_id"], ["transcript_revisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["redaction_id"], ["transcript_redactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_id", "view_digest", name="uq_text_view_revision_digest"),
    )
    with op.batch_alter_table("transcript_text_views", schema=None) as batch_op:
        for column in ("workspace_id", "meeting_id", "revision_id", "redaction_id"):
            batch_op.create_index(
                batch_op.f(f"ix_transcript_text_views_{column}"), [column], unique=False
            )
        batch_op.create_index(
            "ix_text_view_workspace_meeting", ["workspace_id", "meeting_id"], unique=False
        )

    op.create_table(
        "text_view_segments",
        *_tenant_columns(),
        sa.Column("view_id", sa.String(length=36), nullable=False),
        sa.Column("segment_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("entity_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("entity_count >= 0", name="ck_text_view_segment_entities"),
        *_tenant_fks(),
        sa.ForeignKeyConstraint(["view_id"], ["transcript_text_views.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["segment_id"], ["transcript_segments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("view_id", "segment_id", name="uq_text_view_segment"),
    )
    with op.batch_alter_table("text_view_segments", schema=None) as batch_op:
        for column in ("workspace_id", "meeting_id", "view_id", "segment_id"):
            batch_op.create_index(
                batch_op.f(f"ix_text_view_segments_{column}"), [column], unique=False
            )

    op.create_table(
        "text_view_entities",
        *_tenant_columns(),
        sa.Column("view_id", sa.String(length=36), nullable=False),
        sa.Column("segment_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=24), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("ambiguous", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.CheckConstraint(
            "start_offset >= 0 AND end_offset >= start_offset",
            name="ck_text_view_entity_offsets",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_text_view_entity_confidence"
        ),
        *_tenant_fks(),
        sa.ForeignKeyConstraint(["view_id"], ["transcript_text_views.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["segment_id"], ["transcript_segments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("view_id", "position", name="uq_text_view_entity_position"),
    )
    with op.batch_alter_table("text_view_entities", schema=None) as batch_op:
        for column in ("workspace_id", "meeting_id", "view_id", "segment_id"):
            batch_op.create_index(
                batch_op.f(f"ix_text_view_entities_{column}"), [column], unique=False
            )
        batch_op.create_index(
            "ix_text_view_entity_workspace_type", ["workspace_id", "entity_type"], unique=False
        )

    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("text_view_entities")
    op.drop_table("text_view_segments")
    op.drop_table("transcript_text_views")
