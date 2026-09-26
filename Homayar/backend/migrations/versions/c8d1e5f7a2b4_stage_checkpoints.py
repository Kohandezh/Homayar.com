"""stage checkpoints and attempt-scoped artifacts (WP06-T03)

Revision ID: c8d1e5f7a2b4
Revises: b9e4f2a6c8d3
Create Date: 2026-09-09 18:00:00.000000

Adds ``stage_checkpoints``: one row per declared stage artifact (PENDING
before the object is written, PUBLISHED after the fenced verification,
ABANDONED / INVALIDATED otherwise) with its input identity (``input_hash``
over the canonical ``input_manifest``), the verified artifact digest and the
execution identity that produced it.  At most one PUBLISHED row per meeting,
stage and input hash (partial unique index); the artifact key is unique.
``processing_stage_runs`` gains ``checkpoint_id`` / ``reused`` so each stage
run says which checkpoint it produced or reused.  The new table is covered
by the tenant row-level-security policy.

Downgrade drops the columns and the table; artifact objects in storage are
not touched (they are unreferenced afterwards).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = 'c8d1e5f7a2b4'
down_revision: str | Sequence[str] | None = 'b9e4f2a6c8d3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PUBLISHED_WHERE = sa.text("state = 'PUBLISHED'")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "stage_checkpoints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("lease_generation", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "PENDING", "PUBLISHED", "ABANDONED", "INVALIDATED",
                name="checkpointstate", native_enum=False, length=16,
            ),
            nullable=False,
        ),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("input_manifest", sa.JSON(), nullable=False),
        sa.Column("artifact_key", sa.String(length=600), nullable=False),
        sa.Column("artifact_schema", sa.String(length=80), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=True),
        sa.Column("artifact_size_bytes", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=128), nullable=True),
        sa.Column("model", sa.String(length=256), nullable=True),
        sa.Column("is_mock", sa.Boolean(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidation_reason", sa.String(length=80), nullable=True),
        sa.Column("object_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt >= 0", name="ck_checkpoint_attempt"),
        sa.CheckConstraint("lease_generation >= 0", name="ck_checkpoint_generation"),
        sa.CheckConstraint(
            "artifact_size_bytes IS NULL OR artifact_size_bytes >= 0",
            name="ck_checkpoint_artifact_size",
        ),
        sa.CheckConstraint(
            "state <> 'PUBLISHED' OR (artifact_sha256 IS NOT NULL AND "
            "artifact_size_bytes IS NOT NULL AND published_at IS NOT NULL AND "
            "object_deleted_at IS NULL)",
            name="ck_checkpoint_published_complete",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_key"),
    )
    with op.batch_alter_table("stage_checkpoints", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_stage_checkpoints_job_id"), ["job_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_stage_checkpoints_meeting_id"), ["meeting_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_stage_checkpoints_workspace_id"), ["workspace_id"], unique=False
        )
        batch_op.create_index(
            "ix_checkpoint_meeting_stage_state", ["meeting_id", "stage", "state"], unique=False
        )
        batch_op.create_index(
            "ix_checkpoint_workspace_state_updated",
            ["workspace_id", "state", "updated_at"],
            unique=False,
        )
        batch_op.create_index(
            "uq_checkpoint_published_identity",
            ["meeting_id", "stage", "input_hash"],
            unique=True,
            postgresql_where=PUBLISHED_WHERE,
            sqlite_where=PUBLISHED_WHERE,
        )

    with op.batch_alter_table("processing_stage_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("checkpoint_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column("reused", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_index(
            batch_op.f("ix_processing_stage_runs_checkpoint_id"), ["checkpoint_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_processing_stage_runs_checkpoint_id_stage_checkpoints",
            "stage_checkpoints",
            ["checkpoint_id"],
            ["id"],
            ondelete="SET NULL",
        )

    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (checkpoint provenance is lost; objects stay unreferenced)."""
    with op.batch_alter_table("processing_stage_runs", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_processing_stage_runs_checkpoint_id_stage_checkpoints", type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_processing_stage_runs_checkpoint_id"))
        batch_op.drop_column("reused")
        batch_op.drop_column("checkpoint_id")

    with op.batch_alter_table("stage_checkpoints", schema=None) as batch_op:
        batch_op.drop_index("uq_checkpoint_published_identity")
        batch_op.drop_index("ix_checkpoint_workspace_state_updated")
        batch_op.drop_index("ix_checkpoint_meeting_stage_state")
        batch_op.drop_index(batch_op.f("ix_stage_checkpoints_workspace_id"))
        batch_op.drop_index(batch_op.f("ix_stage_checkpoints_meeting_id"))
        batch_op.drop_index(batch_op.f("ix_stage_checkpoints_job_id"))
    op.drop_table("stage_checkpoints")
