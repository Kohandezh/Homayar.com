"""add teams, effective-dated assignments and record scope (WP03-T03)

Revision ID: 9b1d4f2c7a35
Revises: 544f28d9103c
Create Date: 2026-09-08 09:00:00.000000

Upgrade is additive: new tables, nullable meeting attribution columns and
defaulted columns on ``workspaces``/``memberships``.  Existing memberships get
``valid_from = now()`` so nobody loses access.  Downgrade drops the new
structures and therefore discards team data; on a production database prefer
forward recovery (set ``record_scope`` back to WORKSPACE) over downgrade.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9b1d4f2c7a35'
down_revision: str | Sequence[str] | None = '544f28d9103c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "record_scope",
                sa.Enum("WORKSPACE", "TEAM", name="recordscope", native_enum=False, length=16),
                nullable=False,
                server_default="WORKSPACE",
            )
        )

    with op.batch_alter_table("memberships", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "valid_from",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch_op.add_column(sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "teams",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_team_workspace_slug"),
    )
    op.create_index(op.f("ix_teams_workspace_id"), "teams", ["workspace_id"], unique=False)

    op.create_table(
        "team_assignments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("team_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "role",
            sa.Enum("SUPERVISOR", "AGENT", name="teamrole", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column("assigned_by_user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from", name="ck_team_assignment_window"
        ),
        sa.ForeignKeyConstraint(["assigned_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_team_assignment_workspace_user",
        "team_assignments",
        ["workspace_id", "user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_team_assignments_team_id"), "team_assignments", ["team_id"], unique=False
    )
    op.create_index(
        op.f("ix_team_assignments_user_id"), "team_assignments", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_team_assignments_workspace_id"),
        "team_assignments",
        ["workspace_id"],
        unique=False,
    )

    with op.batch_alter_table("meetings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("team_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("agent_user_id", sa.String(length=36), nullable=True))
        batch_op.create_index(batch_op.f("ix_meetings_team_id"), ["team_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_meetings_agent_user_id"), ["agent_user_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_meetings_team_id_teams", "teams", ["team_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_foreign_key(
            "fk_meetings_agent_user_id_users",
            "users",
            ["agent_user_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    """Downgrade schema (discards teams and assignments)."""
    with op.batch_alter_table("meetings", schema=None) as batch_op:
        batch_op.drop_constraint("fk_meetings_agent_user_id_users", type_="foreignkey")
        batch_op.drop_constraint("fk_meetings_team_id_teams", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_meetings_agent_user_id"))
        batch_op.drop_index(batch_op.f("ix_meetings_team_id"))
        batch_op.drop_column("agent_user_id")
        batch_op.drop_column("team_id")

    op.drop_index(op.f("ix_team_assignments_workspace_id"), table_name="team_assignments")
    op.drop_index(op.f("ix_team_assignments_user_id"), table_name="team_assignments")
    op.drop_index(op.f("ix_team_assignments_team_id"), table_name="team_assignments")
    op.drop_index("ix_team_assignment_workspace_user", table_name="team_assignments")
    op.drop_table("team_assignments")
    op.drop_index(op.f("ix_teams_workspace_id"), table_name="teams")
    op.drop_table("teams")

    with op.batch_alter_table("memberships", schema=None) as batch_op:
        batch_op.drop_column("valid_until")
        batch_op.drop_column("valid_from")

    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.drop_column("record_scope")
