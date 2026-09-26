"""workspace AI policies and per-attempt policy snapshots (WP08-T02)

Revision ID: d2b6f8a4c1e7
Revises: c9f2a4e6b8d1
Create Date: 2026-09-24 22:00:00.000000

Adds ``ai_policy_versions`` (append-only versions of a workspace's AI
processing policy) and ``run_policy_snapshots`` (the policy and model
placement each processing attempt ran under). Both are workspace tables under
row-level security. The application role may read and insert them but never
update or delete them (nor ``model_manifests``, WP08-T01); referential
cascades run as the owner and are unaffected. Existing workspaces have no
stored version and run under the built-in default (version 0); existing jobs
have no snapshot. Downgrade drops both tables and restores the ordinary
grants on ``model_manifests``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import APP_ROLE, apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "d2b6f8a4c1e7"
down_revision: str | Sequence[str] | None = "c9f2a4e6b8d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_policy_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("policy_schema", sa.String(length=40), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_ai_policy_version"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "version", name="uq_ai_policy_version"),
    )
    with op.batch_alter_table("ai_policy_versions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ai_policy_versions_workspace_id"), ["workspace_id"], unique=False
        )

    op.create_table(
        "run_policy_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("policy_digest", sa.String(length=64), nullable=False),
        sa.Column("policy_document", sa.JSON(), nullable=False),
        sa.Column("text_privacy_digest", sa.String(length=64), nullable=True),
        sa.Column("strict_manifests", sa.Boolean(), nullable=False),
        sa.Column("stages", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt >= 1", name="ck_run_policy_snapshot_attempt"),
        sa.CheckConstraint("policy_version >= 0", name="ck_run_policy_snapshot_version"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "attempt", name="uq_run_policy_snapshot_attempt"),
    )
    with op.batch_alter_table("run_policy_snapshots", schema=None) as batch_op:
        for column in ("workspace_id", "meeting_id", "job_id"):
            batch_op.create_index(
                batch_op.f(f"ix_run_policy_snapshots_{column}"), [column], unique=False
            )
    # Policies of the new tenant tables, grants, and the append-only revokes.
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table("run_policy_snapshots", schema=None) as batch_op:
        for column in ("job_id", "meeting_id", "workspace_id"):
            batch_op.drop_index(batch_op.f(f"ix_run_policy_snapshots_{column}"))
    op.drop_table("run_policy_snapshots")
    with op.batch_alter_table("ai_policy_versions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ai_policy_versions_workspace_id"))
    op.drop_table("ai_policy_versions")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        role_exists = bind.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE}
        ).scalar()
        if role_exists:
            bind.execute(sa.text(f"GRANT UPDATE, DELETE ON model_manifests TO {APP_ROLE}"))
