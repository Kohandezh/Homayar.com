"""row-level security under a non-bypass application role (WP03-T05)

Revision ID: c4e8b2a7d519
Revises: 9b1d4f2c7a35
Create Date: 2026-09-08 11:00:00.000000

PostgreSQL only (a no-op on SQLite).  Creates the NOLOGIN role ``homayar_app``
if missing, grants it DML on the schema, and enables + forces row-level
security with one permissive policy per tenant table (see
``backend/database/rls.py`` for the exact expressions).  The migration role
keeps ownership; because policies are FORCED the owner is also subject to
them, so every runtime connection must bind a tenant context
(``backend/database/tenant_context.py``).

Downgrade drops the policies and disables RLS but leaves the role and grants
in place (dropping a role that other login roles are members of is an
operator decision, not a migration side effect).
"""
from collections.abc import Sequence

from alembic import op

from backend.database.rls import apply_row_level_security, remove_row_level_security

# revision identifiers, used by Alembic.
revision: str = 'c4e8b2a7d519'
down_revision: str | Sequence[str] | None = '9b1d4f2c7a35'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema."""
    remove_row_level_security(op.get_bind())
