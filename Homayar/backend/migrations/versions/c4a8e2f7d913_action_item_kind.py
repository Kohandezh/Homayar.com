"""Action item kind: commitment or next step (HOM-133, WP24-T01)

Revision ID: c4a8e2f7d913
Revises: 0c51985fcf36
Create Date: 2026-11-12

Adds ``action_items.kind`` (F38): ``commitment`` when a speaker promised or
accepted the action, ``next_step`` when it was only proposed.  The analysis
pipeline sets it on every AI action it writes from now on (ADR 0058).

* Why nullable: a person's action (``created_by = 'user'``), a voicemail rule
  action (``created_by = 'voicemail_rules'``) and every row written before this
  revision have no kind.  NULL means "not labelled", never a guess.
* ``ck_action_kind`` allows NULL, ``commitment`` and ``next_step`` only.  It uses
  no regex, so SQLite runs the same rule.
* Why no batch mode on SQLite: SQLite can add a column with its own named CHECK
  and drop that column again in place.  A batch rebuild would rewrite the whole
  ``action_items`` table and the order of its constraints.  Only a SQLite schema
  built by ``create_all`` (dev and test databases) holds ``ck_action_kind`` as a
  table CHECK, which SQLite cannot drop in place; there the downgrade rebuilds the
  table in batch mode.
* No new table, so no row-level security or grant change.  The column belongs to
  ``action_items`` and its existing policies and grants cover it.

No data changes: there is no backfill.  Existing rows read NULL.

Downgrade drops the check and the column; every stored kind is lost.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a8e2f7d913"
down_revision: str | Sequence[str] | None = "0c51985fcf36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "action_items"
CHECK_NAME = "ck_action_kind"
CHECK_SQL = "kind IS NULL OR kind IN ('commitment', 'next_step')"


_COLUMN_CHECK_RE = re.compile(
    rf"\bkind\s+VARCHAR\(16\)\s+CONSTRAINT\s+{CHECK_NAME}\s+CHECK\b", re.IGNORECASE
)


def _is_sqlite() -> bool:
    return op.get_context().dialect.name == "sqlite"


def _sqlite_column_owns_check() -> bool:
    """True when ``kind`` carries ``ck_action_kind`` itself (this upgrade wrote it)."""
    if op.get_context().as_sql:
        return True
    table_sql = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'action_items'"
        )
        .scalar()
    )
    return bool(table_sql) and _COLUMN_CHECK_RE.search(table_sql) is not None


def upgrade() -> None:
    if _is_sqlite():
        op.execute(
            f"ALTER TABLE {TABLE} ADD COLUMN kind VARCHAR(16) "
            f"CONSTRAINT {CHECK_NAME} CHECK ({CHECK_SQL})"
        )
        return
    op.add_column(TABLE, sa.Column("kind", sa.String(length=16), nullable=True))
    op.create_check_constraint(CHECK_NAME, TABLE, CHECK_SQL)


def downgrade() -> None:
    if _is_sqlite():
        if _sqlite_column_owns_check():
            # The CHECK belongs to the column, so SQLite drops both together.
            op.execute(f"ALTER TABLE {TABLE} DROP COLUMN kind")
            return
        # A ``create_all`` schema: the CHECK is a table CHECK.
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.drop_constraint(CHECK_NAME, type_="check")
            batch_op.drop_column("kind")
        return
    op.drop_constraint(CHECK_NAME, TABLE, type_="check")
    op.drop_column(TABLE, "kind")
