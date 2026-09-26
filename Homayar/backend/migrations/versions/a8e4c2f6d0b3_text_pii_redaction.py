"""text PII redaction versions and restricted mappings (WP07-T05)

Revision ID: a8e4c2f6d0b3
Revises: f3d7b1e9a5c4
Create Date: 2026-09-23 22:00:00.000000

Adds the workspace text-privacy policy (``pii_redaction_enabled``, default
off for every existing workspace; ``pii_redaction_classes``, every class the
detector supports) and three tenant tables: ``transcript_redactions`` (one
redacted version per revision and policy digest), ``redacted_segments`` and
``pii_mappings`` (AES-GCM ciphertext of replaced originals; keys never in the
database). Row-level security is re-applied after the DDL.

Downgrade drops the tables and columns (redacted versions and mappings are
lost; transcripts are untouched).
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "a8e4c2f6d0b3"
down_revision: str | Sequence[str] | None = "f3d7b1e9a5c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLASSES = ["bank_card", "email", "iban", "ir_national_id", "ir_postal_code", "phone"]


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
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("pii_redaction_enabled", sa.Boolean(), server_default="false", nullable=False)
        )
        batch_op.add_column(sa.Column("pii_redaction_classes", sa.JSON(), nullable=True))
    op.get_bind().execute(
        sa.text("UPDATE workspaces SET pii_redaction_classes = :classes"),
        {"classes": json.dumps(CLASSES)},
    )
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.alter_column("pii_redaction_classes", existing_type=sa.JSON(), nullable=False)

    op.create_table(
        "transcript_redactions",
        *_tenant_columns(),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("detector_version", sa.String(length=40), nullable=False),
        sa.Column("classes", sa.JSON(), nullable=False),
        sa.Column("policy_digest", sa.String(length=64), nullable=False),
        sa.Column("entity_count", sa.Integer(), nullable=False),
        sa.Column("mapping_retained", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("entity_count >= 0", name="ck_redaction_entities"),
        *_tenant_fks(),
        sa.ForeignKeyConstraint(["revision_id"], ["transcript_revisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_id", "policy_digest", name="uq_redaction_revision_policy"),
    )
    with op.batch_alter_table("transcript_redactions", schema=None) as batch_op:
        for column in ("workspace_id", "meeting_id", "revision_id"):
            batch_op.create_index(
                batch_op.f(f"ix_transcript_redactions_{column}"), [column], unique=False
            )
        batch_op.create_index(
            "ix_redaction_workspace_meeting", ["workspace_id", "meeting_id"], unique=False
        )

    op.create_table(
        "redacted_segments",
        *_tenant_columns(),
        sa.Column("redaction_id", sa.String(length=36), nullable=False),
        sa.Column("segment_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("entity_count", sa.Integer(), nullable=False),
        *_tenant_fks(),
        sa.ForeignKeyConstraint(["redaction_id"], ["transcript_redactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["segment_id"], ["transcript_segments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("redaction_id", "segment_id", name="uq_redacted_segment"),
    )
    with op.batch_alter_table("redacted_segments", schema=None) as batch_op:
        for column in ("workspace_id", "meeting_id", "redaction_id", "segment_id"):
            batch_op.create_index(
                batch_op.f(f"ix_redacted_segments_{column}"), [column], unique=False
            )

    op.create_table(
        "pii_mappings",
        *_tenant_columns(),
        sa.Column("redaction_id", sa.String(length=36), nullable=False),
        sa.Column("segment_id", sa.String(length=36), nullable=False),
        sa.Column("placeholder", sa.String(length=40), nullable=False),
        sa.Column("pii_class", sa.String(length=24), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("key_id", sa.String(length=40), nullable=False),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "end_offset > start_offset AND start_offset >= 0", name="ck_pii_offsets"
        ),
        *_tenant_fks(),
        sa.ForeignKeyConstraint(["redaction_id"], ["transcript_redactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["segment_id"], ["transcript_segments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "redaction_id", "segment_id", "start_offset", name="uq_pii_mapping_position"
        ),
    )
    with op.batch_alter_table("pii_mappings", schema=None) as batch_op:
        for column in ("workspace_id", "meeting_id", "redaction_id"):
            batch_op.create_index(batch_op.f(f"ix_pii_mappings_{column}"), [column], unique=False)
        batch_op.create_index(
            "ix_pii_mapping_redaction_placeholder", ["redaction_id", "placeholder"], unique=False
        )

    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("pii_mappings")
    op.drop_table("redacted_segments")
    op.drop_table("transcript_redactions")
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.drop_column("pii_redaction_classes")
        batch_op.drop_column("pii_redaction_enabled")
