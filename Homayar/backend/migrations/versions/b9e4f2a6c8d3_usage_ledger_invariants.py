"""usage ledger with stable operation identities and settlement invariants (WP06-T06)

Revision ID: b9e4f2a6c8d3
Revises: a7d2c4e9f6b1
Create Date: 2026-09-09 14:00:00.000000

``usage_events`` becomes an append-only ledger of every reservation transition:
``operation_key`` (stable business-operation identity, unique), ``reason``,
the pair (reservation, event type) unique, and the old "one event per
reservation" constraint dropped. ``usage_reservations`` gains the settlement
invariant "one COMMITTED reservation per meeting and metric" (partial unique
index). The ``entitlements`` row-level-security policy gains the read-only
``usage_reconciliation`` service scope.

Backfill: existing rows (all ``committed``) get ``<reservation id>:committed``
as their operation key. The migration refuses to run when a meeting already
has two COMMITTED reservations for one metric — resolve those rows first.

Downgrade: removes the ledger rows the previous schema cannot represent
(``reserved`` / ``released`` / ``adjusted``), restores the one-event-per-
reservation constraint and drops the new columns, index and policy scope.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = 'b9e4f2a6c8d3'
down_revision: str | Sequence[str] | None = 'a7d2c4e9f6b1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The initial migration created an unnamed UNIQUE(reservation_id); PostgreSQL
# named it itself, SQLite batch mode needs a naming convention to find it.
PG_LEGACY_UNIQUE = "usage_events_reservation_id_key"
SQLITE_NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
SQLITE_LEGACY_UNIQUE = "uq_usage_events_reservation_id"
SETTLED_WHERE = sa.text("state = 'COMMITTED'")


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            "SELECT meeting_id, entitlement_id, COUNT(*) AS n FROM usage_reservations "
            "WHERE state = 'COMMITTED' GROUP BY meeting_id, entitlement_id HAVING COUNT(*) > 1"
        )
    ).all()
    if duplicates:
        raise RuntimeError(
            "usage_reservations has meetings with more than one COMMITTED reservation for a "
            f"metric ({len(duplicates)}); resolve them before applying b9e4f2a6c8d3"
        )

    with op.batch_alter_table("usage_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("operation_key", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("reason", sa.String(length=160), nullable=True))
    op.execute(
        sa.text(
            "UPDATE usage_events SET operation_key = reservation_id || ':' || event_type "
            "WHERE operation_key IS NULL AND reservation_id IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE usage_events SET operation_key = 'legacy:' || id "
            "WHERE operation_key IS NULL"
        )
    )
    if _is_sqlite():
        with op.batch_alter_table(
            "usage_events", schema=None, naming_convention=SQLITE_NAMING
        ) as batch_op:
            batch_op.drop_constraint(SQLITE_LEGACY_UNIQUE, type_="unique")
    else:
        op.drop_constraint(PG_LEGACY_UNIQUE, "usage_events", type_="unique")
    with op.batch_alter_table("usage_events", schema=None) as batch_op:
        batch_op.alter_column("operation_key", existing_type=sa.String(length=200), nullable=False)
        batch_op.create_unique_constraint("uq_usage_event_operation", ["operation_key"])
        batch_op.create_unique_constraint(
            "uq_usage_event_reservation_type", ["reservation_id", "event_type"]
        )
        batch_op.create_index("ix_usage_events_reservation_id", ["reservation_id"], unique=False)
        batch_op.create_index(
            "ix_usage_event_workspace_time", ["workspace_id", "occurred_at"], unique=False
        )

    op.create_index(
        "uq_reservation_settled_per_meeting",
        "usage_reservations",
        ["meeting_id", "entitlement_id"],
        unique=True,
        postgresql_where=SETTLED_WHERE,
        sqlite_where=SETTLED_WHERE,
    )

    apply_row_level_security(bind)


def downgrade() -> None:
    """Downgrade schema (ledger rows other than settlements are removed)."""
    bind = op.get_bind()
    op.drop_index("uq_reservation_settled_per_meeting", table_name="usage_reservations")
    op.execute(sa.text("DELETE FROM usage_events WHERE event_type <> 'committed'"))
    with op.batch_alter_table("usage_events", schema=None) as batch_op:
        batch_op.drop_index("ix_usage_event_workspace_time")
        batch_op.drop_index("ix_usage_events_reservation_id")
        batch_op.drop_constraint("uq_usage_event_reservation_type", type_="unique")
        batch_op.drop_constraint("uq_usage_event_operation", type_="unique")
        batch_op.drop_column("reason")
        batch_op.drop_column("operation_key")
    if _is_sqlite():
        with op.batch_alter_table(
            "usage_events", schema=None, naming_convention=SQLITE_NAMING
        ) as batch_op:
            batch_op.create_unique_constraint(SQLITE_LEGACY_UNIQUE, ["reservation_id"])
    else:
        op.create_unique_constraint(PG_LEGACY_UNIQUE, "usage_events", ["reservation_id"])

    apply_row_level_security(bind)
