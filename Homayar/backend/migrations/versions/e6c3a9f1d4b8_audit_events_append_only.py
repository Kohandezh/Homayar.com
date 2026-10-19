"""Append-only audit events and the protected sink's reader role (HOM-115, ADR 0036)

Revision ID: e6c3a9f1d4b8
Revises: d8a2f4c6b1e3
Create Date: 2026-10-18

Owner decision 2026-10-17 (option B): ``audit_events`` becomes append-only for the
application, and the protected audit sink reads it through a role of its own.

PostgreSQL only (SQLite has no roles; the ORM guard in ``audit_service`` covers it):

* ``REVOKE UPDATE, DELETE ON audit_events FROM homayar_app`` (through
  ``backend.database.rls.APPEND_ONLY_TABLES``).  INSERT and SELECT stay.  The
  ``ON DELETE SET NULL`` of ``workspace_id`` / ``actor_user_id`` runs as the table
  owner, so deleting a workspace or a user keeps working.
* ``homayar_audit_reader``: NOLOGIN, no superuser/BYPASSRLS, ``SELECT`` on
  ``audit_events`` only, plus the policy ``homayar_audit_export FOR SELECT TO
  homayar_audit_reader USING (true)``.  The tenant policy and its WITH CHECK are
  unchanged.  ``homayar_app`` is not a member, so no application session can
  ``SET ROLE`` to it.  A role of that name that already exists is made inert (no
  LOGIN or password, no role attribute, no membership either way); if it cannot be
  (an indirect membership path from ``homayar_app``, or a change the migration role
  may not make), the upgrade fails instead of continuing.

Creating the role needs CREATEROLE, as for ``homayar_app``; a migration role without
it needs both roles created by a DBA first (``docs/operations/audit.md``).

No data changes. Downgrade drops the reader policy and grants, drops the role when
no other database on the cluster still grants it anything and the migration role may
drop roles (otherwise the role stays, NOLOGIN and without privileges here), and gives
``homayar_app`` back UPDATE and DELETE on ``audit_events``. The previous
application never updates or deletes audit rows, so it runs on either schema.
A deeper downgrade re-applies the rule once it passes ``b9e4f2a6c8d3`` (and again
at ``f1b6d8e4a2c7`` and ``d7f3a1c9e2b4``): their downgrades, like every older upgrade,
call ``apply_row_level_security`` from the current code, which revokes UPDATE and
DELETE on ``audit_events`` and installs the reader role again.  Between
``d8a2f4c6b1e3`` and ``b9e4f2a6c8d3`` the table stays writable, as at those revisions.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import (
    APP_ROLE,
    apply_row_level_security,
    remove_audit_reader_statements,
)

# revision identifiers, used by Alembic.
revision: str = "e6c3a9f1d4b8"
down_revision: str | Sequence[str] | None = "d8a2f4c6b1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Revoke UPDATE/DELETE from the application role and create the sink reader."""
    # Grants, policies, the append-only revokes and the audit reader, idempotently.
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Drop the reader and give the application role its ordinary grants back."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for statement in remove_audit_reader_statements():
        bind.execute(sa.text(statement))
    role_exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE}
    ).scalar()
    if role_exists:
        bind.execute(sa.text(f"GRANT UPDATE, DELETE ON audit_events TO {APP_ROLE}"))
