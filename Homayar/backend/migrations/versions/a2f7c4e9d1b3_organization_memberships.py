"""Organization memberships and scope grants (WP03-T03, ADR 0012).

Revision ID: a2f7c4e9d1b3
Revises: c8d1e5f7a2b4
Create Date: 2026-09-10

Adds the organization membership carrier (``organization_memberships``) and
the analytics scope grants of analysts/executives
(``organization_scope_grants``), installs their row-level security policies
and extends the ``workspaces`` / ``organizations`` policies so a bound
organization exposes its workspaces to effective organization owners/admins
and to scope-grant holders.

Backfill: every existing organization whose workspaces have **exactly one**
distinct active owner receives that user as its organization owner — today
that is every organization, since registration creates one personal
workspace per organization. Organizations with no or several distinct owners
are left without organization members and counted in the migration log; an
operator decides those by hand. Nothing is inferred from email domains and no
default organization is created.

The data step runs with FORCE ROW LEVEL SECURITY suspended on the three
source tables for the duration of this transaction, so it behaves the same
whether the migration role bypasses RLS (superuser/BYPASSRLS, as in CI) or
is the plain table owner (deployment shape of ADR 0005); the policies are
re-forced by ``apply_row_level_security`` before the migration commits.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from backend.database.rls import (
    POLICY_NAME,
    PRE_ORGANIZATION_POLICIES,
    apply_row_level_security,
)

revision: str = "a2f7c4e9d1b3"
down_revision: str | Sequence[str] | None = "c8d1e5f7a2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

_BACKFILL_SOURCES = ("organizations", "workspaces", "memberships")


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "OWNER",
                "ADMIN",
                "ANALYST",
                "EXECUTIVE",
                name="organizationrole",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "valid_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("granted_by_user_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from", name="ck_org_membership_window"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_org_membership_org_user"),
    )
    op.create_index(
        "ix_organization_memberships_organization_id",
        "organization_memberships",
        ["organization_id"],
    )
    op.create_index("ix_organization_memberships_user_id", "organization_memberships", ["user_id"])

    op.create_table(
        "organization_scope_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("membership_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("team_id", sa.String(length=36), nullable=True),
        sa.Column("granted_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["membership_id"], ["organization_memberships.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_organization_scope_grants_organization_id",
        "organization_scope_grants",
        ["organization_id"],
    )
    op.create_index(
        "ix_organization_scope_grants_membership_id", "organization_scope_grants", ["membership_id"]
    )
    op.create_index(
        "ix_organization_scope_grants_workspace_id", "organization_scope_grants", ["workspace_id"]
    )
    op.create_index(
        "ix_org_scope_grant_membership_workspace",
        "organization_scope_grants",
        ["membership_id", "workspace_id"],
    )

    counts = backfill_owner_memberships(op.get_bind())
    log.info("organization owner backfill: %s", counts)
    # Policies for the new tables plus the extended workspaces/organizations
    # policies; FORCE is (re)applied to every policy table here.
    apply_row_level_security(op.get_bind())


def backfill_owner_memberships(connection: sa.Connection) -> dict[str, int]:
    """Grant the organization owner role to the one active owner of each organization.

    Returns ``{"granted": n, "skipped_no_owner": n, "skipped_ambiguous": n}``.
    Idempotent: organizations that already have any organization membership
    are left alone.
    """
    postgres = connection.dialect.name == "postgresql"
    if postgres:
        for table in _BACKFILL_SOURCES:
            connection.execute(  # noqa: S608 - constant table names
                sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            )
    try:
        rows = (
            connection.execute(
                sa.text(
                    "SELECT o.id AS organization_id, "
                    "COUNT(DISTINCT m.user_id) AS owners, MIN(m.user_id) AS owner_id "
                    "FROM organizations o "
                    "LEFT JOIN workspaces w ON w.organization_id = o.id "
                    "LEFT JOIN memberships m ON m.workspace_id = w.id "
                    "AND m.role = 'OWNER' AND m.is_active = TRUE "
                    "WHERE NOT EXISTS (SELECT 1 FROM organization_memberships om "
                    "WHERE om.organization_id = o.id) "
                    "GROUP BY o.id ORDER BY o.id"
                )
            )
            .mappings()
            .all()
        )
        counts = {"granted": 0, "skipped_no_owner": 0, "skipped_ambiguous": 0}
        now = datetime.now(UTC)
        for row in rows:
            if row["owners"] == 1:
                connection.execute(
                    sa.text(
                        "INSERT INTO organization_memberships (id, organization_id, user_id, "
                        "role, is_active, valid_from, granted_by_user_id, created_at, updated_at) "
                        "VALUES (:id, :organization_id, :user_id, 'OWNER', TRUE, :now, "
                        ":user_id, :now, :now)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "organization_id": row["organization_id"],
                        "user_id": row["owner_id"],
                        "now": now,
                    },
                )
                counts["granted"] += 1
            elif row["owners"] == 0:
                counts["skipped_no_owner"] += 1
            else:
                counts["skipped_ambiguous"] += 1
        return counts
    finally:
        if postgres:
            for table in _BACKFILL_SOURCES:
                connection.execute(  # noqa: S608 - constant table names
                    sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
                )


def downgrade() -> None:
    if _is_postgres():
        # The extended policies reference the tables about to be dropped:
        # restore the pre-organization expressions first.
        for table, (using, check) in PRE_ORGANIZATION_POLICIES.items():
            op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")  # noqa: S608
            op.execute(  # noqa: S608 - policy text from constants, constant table names
                f"CREATE POLICY {POLICY_NAME} ON {table} AS PERMISSIVE FOR ALL TO PUBLIC "
                f"USING ({using}) WITH CHECK ({check})"
            )
    op.drop_index("ix_org_scope_grant_membership_workspace", table_name="organization_scope_grants")
    op.drop_index(
        "ix_organization_scope_grants_workspace_id", table_name="organization_scope_grants"
    )
    op.drop_index(
        "ix_organization_scope_grants_membership_id", table_name="organization_scope_grants"
    )
    op.drop_index(
        "ix_organization_scope_grants_organization_id", table_name="organization_scope_grants"
    )
    op.drop_table("organization_scope_grants")
    op.drop_index("ix_organization_memberships_user_id", table_name="organization_memberships")
    op.drop_index(
        "ix_organization_memberships_organization_id", table_name="organization_memberships"
    )
    op.drop_table("organization_memberships")
