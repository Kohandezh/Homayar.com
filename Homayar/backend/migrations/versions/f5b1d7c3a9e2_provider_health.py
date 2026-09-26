"""provider endpoint health, circuits and request windows (WP08-T05)

Revision ID: f5b1d7c3a9e2
Revises: e3a7c5f9b2d4
Create Date: 2026-09-25 00:30:00.000000

Adds ``provider_health``: one row per networked provider endpoint (keyed by
the digest of the adapter identity, never a credential) with the consecutive
transient failures, the time until which its circuit is open, and the current
request window. Platform state shared by all workspaces, like
``model_manifests``: outside row-level security, holding no tenant data.
Downgrade drops the table (health state is rebuilt from new calls).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "f5b1d7c3a9e2"
down_revision: str | Sequence[str] | None = "e3a7c5f9b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_health",
        sa.Column("endpoint_key", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("open_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_code", sa.String(length=80), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "consecutive_failures >= 0 AND window_count >= 0", name="ck_provider_health_counts"
        ),
        sa.PrimaryKeyConstraint("endpoint_key"),
    )
    # Grants for the new platform table (no tenant policy).
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    op.drop_table("provider_health")
