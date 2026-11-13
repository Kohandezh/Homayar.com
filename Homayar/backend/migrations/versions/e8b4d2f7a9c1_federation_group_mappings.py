"""Federation group mappings and membership source (HOM-128, WP23-T02)

Revision ID: e8b4d2f7a9c1
Revises: 5f3a91c0d7b2
Create Date: 2026-11-13

Adds the stored mapping of an IdP group to a workspace role
(``federation_group_mappings``, ADR 0066), the ``memberships.source`` column
that separates rows a person decided from rows the IdP reconciler owns, and
``auth_identities.revoked_at`` for "the IdP said this user is gone".

* Why the role CHECK lists the enum *names* (uppercase): the non-native
  ``workspacerole`` enum stores the member names, as ``memberships.role`` does
  since the initial domain; ``OWNER`` is absent on purpose (a group never
  grants the workspace owner role).  The checks use no regex, so SQLite runs
  the same rules.
* ``ck_membership_source`` keeps every row honest about who decided it
  (``direct`` or ``federated``); the column arrives with server default
  ``direct``, so every existing row (every row written before this revision)
  becomes ``direct``: someone granted it by hand.
* ``auth_identities.revoked_at`` is written only by the deprovisioning path
  (the IdP said gone); HOM-127's later login callback clears it again.
* ``uq_federation_group_mappings_active`` is a partial unique index on
  (organization, issuer, group, workspace) over unrevoked rows only: a
  revoked mapping never blocks a fresh one.
* Why no batch mode on SQLite for ``memberships``: SQLite can add a column
  with its own named CHECK and drop that column again in place.  Only a SQLite
  schema built by ``create_all`` (dev and test databases) holds
  ``ck_membership_source`` as a table CHECK, which SQLite cannot drop in
  place; there the downgrade rebuilds the table in batch mode.
* The mapping table is an organization table under row-level security: the
  bound organization only, like ``organization_memberships``.  The migration
  re-applies the policies and the application grants, so the new table is
  covered the same way.

No data changes beyond the server default: there is no backfill and nothing
is inferred from existing identities or groups.

Downgrade drops the two columns and the table; every mapping and every
membership's ``source`` marker is lost (the memberships themselves stay).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "e8b4d2f7a9c1"
down_revision: str | Sequence[str] | None = "5f3a91c0d7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "federation_group_mappings"
MEMBERSHIPS = "memberships"
AUTH_IDENTITIES = "auth_identities"
SOURCE_CHECK_NAME = "ck_membership_source"
SOURCE_CHECK_SQL = "source IN ('direct', 'federated')"
ROLE_CHECK_NAME = "ck_federation_mapping_role"
ROLE_CHECK_SQL = "role IN ('ADMIN', 'MEMBER', 'VIEWER')"
UNREVOKED_WHERE = sa.text("revoked_at IS NULL")
ACTIVE_UNIQUE_INDEX = "uq_federation_group_mappings_active"

_SOURCE_COLUMN_CHECK_RE = re.compile(
    rf"\bsource\s+VARCHAR\(16\)\s+NOT NULL\s+DEFAULT\s+'direct'\s+"
    rf"CONSTRAINT\s+{SOURCE_CHECK_NAME}\s+CHECK\b",
    re.IGNORECASE,
)


def _is_sqlite() -> bool:
    return op.get_context().dialect.name == "sqlite"


def _sqlite_source_owns_check() -> bool:
    """True when ``memberships.source`` carries ``ck_membership_source`` itself."""
    if op.get_context().as_sql:
        return True
    table_sql = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memberships'"
        )
        .scalar()
    )
    return bool(table_sql) and _SOURCE_COLUMN_CHECK_RE.search(table_sql) is not None


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("group_name", sa.String(length=256), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "ADMIN", "MEMBER", "VIEWER", name="workspacerole", native_enum=False, length=16
            ),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(ROLE_CHECK_SQL, name=ROLE_CHECK_NAME),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["revoked_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.create_index(f"ix_{TABLE}_organization_id", ["organization_id"], unique=False)
        batch_op.create_index(f"ix_{TABLE}_workspace_id", ["workspace_id"], unique=False)
        batch_op.create_index(
            ACTIVE_UNIQUE_INDEX,
            ["organization_id", "issuer", "group_name", "workspace_id"],
            unique=True,
            postgresql_where=UNREVOKED_WHERE,
            sqlite_where=UNREVOKED_WHERE,
        )
    if _is_sqlite():
        op.execute(
            f"ALTER TABLE {MEMBERSHIPS} ADD COLUMN source VARCHAR(16) NOT NULL "
            f"DEFAULT 'direct' CONSTRAINT {SOURCE_CHECK_NAME} CHECK ({SOURCE_CHECK_SQL})"
        )
    else:
        op.add_column(
            MEMBERSHIPS,
            sa.Column("source", sa.String(length=16), nullable=False, server_default="direct"),
        )
        op.create_check_constraint(SOURCE_CHECK_NAME, MEMBERSHIPS, SOURCE_CHECK_SQL)
    op.add_column(
        AUTH_IDENTITIES, sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True)
    )
    # The tenant policy of the new table and its grants.
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    op.drop_column(AUTH_IDENTITIES, "revoked_at")
    if _is_sqlite():
        if _sqlite_source_owns_check():
            # The CHECK belongs to the column, so SQLite drops both together.
            op.execute(f"ALTER TABLE {MEMBERSHIPS} DROP COLUMN source")
        else:
            # A ``create_all`` schema: the CHECK is a table CHECK.
            with op.batch_alter_table(MEMBERSHIPS, schema=None) as batch_op:
                batch_op.drop_constraint(SOURCE_CHECK_NAME, type_="check")
                batch_op.drop_column("source")
    else:
        op.drop_constraint(SOURCE_CHECK_NAME, MEMBERSHIPS, type_="check")
        op.drop_column(MEMBERSHIPS, "source")
    # The indexes, the policy and the grants belong to the table and go with it.
    op.drop_table(TABLE)
