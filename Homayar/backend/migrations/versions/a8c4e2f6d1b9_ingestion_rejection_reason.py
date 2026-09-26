"""rejected ingestions carry a reason (WP05-T02, ADR 0016 amendment)

Revision ID: a8c4e2f6d1b9
Revises: f7b1d3e5a9c2
Create Date: 2026-09-11 14:00:00.000000

WP05-T02 records terminal media rejections as ``REJECTED`` ledger rows.  The
status/reason check constraint of WP05-T01 only forbade a reason on accepted
rows; from here a rejected row must carry its reason code.  No data changes:
the WP05-T01 backfill wrote accepted rows only.

Downgrade restores the looser constraint (lossless).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8c4e2f6d1b9"
down_revision: str | Sequence[str] | None = "f7b1d3e5a9c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_ingestion_accepted_reason"
STRICT = "(status = 'ACCEPTED' AND reason IS NULL) OR (status = 'REJECTED' AND reason IS NOT NULL)"
LOOSE = "(status = 'ACCEPTED' AND reason IS NULL) OR status <> 'ACCEPTED'"


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("ingestion_records", schema=None) as batch_op:
        batch_op.drop_constraint(CONSTRAINT, type_="check")
        batch_op.create_check_constraint(CONSTRAINT, STRICT)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("ingestion_records", schema=None) as batch_op:
        batch_op.drop_constraint(CONSTRAINT, type_="check")
        batch_op.create_check_constraint(CONSTRAINT, LOOSE)
