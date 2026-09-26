"""model manifests and stage-run manifest provenance (WP08-T01)

Revision ID: c9f2a4e6b8d1
Revises: b5d9e3a1c7f2
Create Date: 2026-09-24 20:00:00.000000

Adds the platform catalog ``model_manifests`` (append-only, keyed by the
digest of the canonical manifest document; not tenant data, so it is listed
with the other global catalog tables outside row-level security) and the
manifest reference of each stage run (``manifest_id``, ``manifest_version``,
``manifest_digest``) and of each analysis run (``manifest_digest``). Existing
runs predate the registry and keep null references. Downgrade drops the
columns and the catalog. Row-level security is re-applied after the DDL so
the application role holds its grants on the new table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "c9f2a4e6b8d1"
down_revision: str | Sequence[str] | None = "b5d9e3a1c7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_manifests",
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("manifest_id", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_model_manifest_version"),
        sa.PrimaryKeyConstraint("digest"),
        sa.UniqueConstraint("manifest_id", "version", name="uq_model_manifest_version"),
    )
    with op.batch_alter_table("processing_stage_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("manifest_id", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("manifest_version", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("manifest_digest", sa.String(length=64), nullable=True))
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("manifest_digest", sa.String(length=64), nullable=True))
    # Grants for the new catalog table (policies of tenant tables unchanged).
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.drop_column("manifest_digest")
    with op.batch_alter_table("processing_stage_runs", schema=None) as batch_op:
        batch_op.drop_column("manifest_digest")
        batch_op.drop_column("manifest_version")
        batch_op.drop_column("manifest_id")
    op.drop_table("model_manifests")
