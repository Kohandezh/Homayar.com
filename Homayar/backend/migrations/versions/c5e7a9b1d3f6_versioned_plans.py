"""Versioned plans: append-only plan_versions and subscription pins (WP18-T03).

Revision ID: c5e7a9b1d3f6
Revises: a1c3e5f7b9d2
Create Date: 2026-09-30

Adds ``plan_versions``, the append-only history of each plan's commercial
terms (``name``, ``is_public``, ``entitlements_json``).  It adds
``plans.current_version``, the version a plan row carries, and
``subscriptions.plan_version``, the version a subscription was granted under.
The composite foreign key ``fk_subscription_plan_version`` references
``plan_versions (plan_key, version)``.  ``plan_versions`` is a global catalog
(no row policy, like ``plans``).  The application role may read and insert it
but never update or delete it.

Upgrade is lossless.  Every existing plan, catalog or not, gets version 1
(``source = 'legacy_snapshot'``) copied from its row; the row itself is not
modified and its ``current_version`` is 1.  Every existing subscription is
pinned to version 1 of its plan.

The only data step (``backfill_plan_versions``) reads ``plans`` and inserts
into ``plan_versions``, neither of which has row-level security.  The pins
come from a column DEFAULT inside the DDL, which is dropped afterwards.  The
migration therefore runs no DML on a table with FORCE ROW LEVEL SECURITY, and
behaves the same for a superuser and for a plain table owner.

Downgrade drops ``plan_versions``, ``plans.current_version``,
``subscriptions.plan_version`` and ``fk_subscription_plan_version``.  Only the
version history and the pins are lost: ``plans`` rows keep their current
terms (those of their latest version), and ``subscriptions`` and
``entitlements`` rows are untouched.

Not backward-compatible with the prior application: both new columns are
NOT NULL without a default (a default would pin a subscription to a version
it was not granted under), so the previous image fails every demo sign-up.
Stop the API, not only the workers, before this migration; roll the
application back only after ``alembic downgrade a1c3e5f7b9d2``.  The prior
application's start then rewrites every catalog plan row to its code
defaults, so published terms survive only in the pre-upgrade backup or an
export of ``plan_versions`` taken before the downgrade.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "c5e7a9b1d3f6"
down_revision: str | Sequence[str] | None = "a1c3e5f7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    op.create_table(
        "plan_versions",
        sa.Column("plan_key", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("entitlements_json", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_plan_version_version"),
        sa.CheckConstraint(
            "source IN ('catalog_seed', 'legacy_snapshot', 'operator', 'config')",
            name="ck_plan_version_source",
        ),
        sa.ForeignKeyConstraint(["plan_key"], ["plans.key"]),
        sa.PrimaryKeyConstraint("plan_key", "version"),
    )
    with op.batch_alter_table("plans", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("current_version", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.create_check_constraint("ck_plan_current_version", "current_version >= 1")

    inserted = backfill_plan_versions(op.get_bind())
    log.info("plan version backfill: %d legacy snapshot(s)", inserted)

    # Every existing subscription is pinned to version 1 of its plan by the
    # column default (DDL, not an UPDATE under row-level security).
    with op.batch_alter_table("subscriptions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("plan_version", sa.Integer(), nullable=False, server_default="1")
        )
    with op.batch_alter_table("plans", schema=None) as batch_op:
        batch_op.alter_column("current_version", server_default=None)
    with op.batch_alter_table("subscriptions", schema=None) as batch_op:
        batch_op.alter_column("plan_version", server_default=None)
        batch_op.create_foreign_key(
            "fk_subscription_plan_version",
            "plan_versions",
            ["plan_key", "plan_version"],
            ["plan_key", "version"],
        )
    # Grants for the new table, including the append-only revoke.
    apply_row_level_security(op.get_bind())


def backfill_plan_versions(connection: sa.Connection) -> int:
    """Insert version 1 (``legacy_snapshot``) for every plan that has no version.

    The name, is_public and entitlements_json values are copied from the row
    in SQL, so the JSON text is kept exactly as stored.  Idempotent: returns
    the number of versions inserted (0 when every plan already has one).
    """
    plans = sa.table(
        "plans",
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("is_public", sa.Boolean),
        sa.column("entitlements_json", sa.JSON),
    )
    plan_versions = sa.table(
        "plan_versions",
        sa.column("plan_key", sa.String),
        sa.column("version", sa.Integer),
        sa.column("name", sa.String),
        sa.column("is_public", sa.Boolean),
        sa.column("entitlements_json", sa.JSON),
        sa.column("source", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    snapshot = sa.select(
        plans.c.key,
        sa.literal(1, sa.Integer),
        plans.c.name,
        plans.c.is_public,
        plans.c.entitlements_json,
        sa.literal("legacy_snapshot", sa.String),
        sa.literal(datetime.now(UTC), sa.DateTime(timezone=True)),
    ).where(~sa.exists().where(plan_versions.c.plan_key == plans.c.key))
    result = connection.execute(
        plan_versions.insert().from_select(
            [
                "plan_key",
                "version",
                "name",
                "is_public",
                "entitlements_json",
                "source",
                "created_at",
            ],
            snapshot,
        ),
        execution_options={"preserve_rowcount": True},  # INSERT rowcount is not kept by default
    )
    return result.rowcount


def downgrade() -> None:
    with op.batch_alter_table("subscriptions", schema=None) as batch_op:
        batch_op.drop_constraint("fk_subscription_plan_version", type_="foreignkey")
        batch_op.drop_column("plan_version")
    with op.batch_alter_table("plans", schema=None) as batch_op:
        batch_op.drop_constraint("ck_plan_current_version", type_="check")
        batch_op.drop_column("current_version")
    op.drop_table("plan_versions")
