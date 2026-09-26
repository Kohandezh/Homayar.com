"""share audiences, grants, recipients and public-link opt-in (WP03-T06)

Revision ID: d7f3a1c9e2b4
Revises: c4e8b2a7d519
Create Date: 2026-09-08 13:00:00.000000

Intentional behaviour change: existing shares created with
``require_authentication = true`` (any signed-in account) become
``workspace`` shares (current members of the source workspace only), and
existing anonymous links keep working only after an admin enables
``public_share_links_enabled`` for the workspace.  Both are stricter than
before; nothing becomes more permissive.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = 'd7f3a1c9e2b4'
down_revision: str | Sequence[str] | None = 'c4e8b2a7d519'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "public_share_links_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )

    with op.batch_alter_table("share_links", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "audience",
                sa.Enum(
                    "LINK", "WORKSPACE", "RECIPIENTS",
                    name="shareaudience", native_enum=False, length=16,
                ),
                nullable=False,
                server_default="WORKSPACE",
            )
        )
        batch_op.add_column(
            sa.Column(
                "allow_download", sa.Boolean(), nullable=False, server_default=sa.text("false")
            )
        )
        batch_op.add_column(
            sa.Column(
                "allow_export", sa.Boolean(), nullable=False, server_default=sa.text("false")
            )
        )
    # Backfill: anonymous links stay anonymous (gated by the workspace opt-in);
    # "any signed-in user" links become member-only.
    op.execute("UPDATE share_links SET audience = 'LINK' WHERE require_authentication = false")

    op.create_table(
        "share_recipients",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("share_id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["share_id"], ["share_links.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("share_id", "email", name="uq_share_recipient_email"),
    )
    op.create_index(
        op.f("ix_share_recipients_share_id"), "share_recipients", ["share_id"], unique=False
    )
    op.create_index(
        op.f("ix_share_recipients_user_id"), "share_recipients", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_share_recipients_workspace_id"),
        "share_recipients",
        ["workspace_id"],
        unique=False,
    )

    # Re-create the policies: share-scoped visibility of meeting content and
    # the new share_recipients table (no-op on SQLite).
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (drops recipients; audience information is lost)."""
    op.drop_index(op.f("ix_share_recipients_workspace_id"), table_name="share_recipients")
    op.drop_index(op.f("ix_share_recipients_user_id"), table_name="share_recipients")
    op.drop_index(op.f("ix_share_recipients_share_id"), table_name="share_recipients")
    op.drop_table("share_recipients")

    with op.batch_alter_table("share_links", schema=None) as batch_op:
        batch_op.drop_column("allow_export")
        batch_op.drop_column("allow_download")
        batch_op.drop_column("audience")

    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.drop_column("public_share_links_enabled")

    apply_row_level_security(op.get_bind())
