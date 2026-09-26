"""API-key capability policy on api_keys (WP03-T07, ADR 0013).

Revision ID: c3e9a7b5d1f4
Revises: b7d2f4a8c6e1
Create Date: 2026-09-10

No schema change: the ``api_keys`` row-level security policy additionally
admits the one row whose ``key_hash`` equals the presented key digest
(``homayar.api_key_hash``), so a key can be looked up, checked and stamped
before any tenant is bound. Every other table still waits for the workspace
context that ``require_workspace`` binds after the bound user's membership was
validated. Downgrade restores the tenant-only policy.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from backend.database.rls import POLICY_NAME, TENANT, apply_row_level_security

revision: str = "c3e9a7b5d1f4"
down_revision: str | Sequence[str] | None = "b7d2f4a8c6e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON api_keys")  # noqa: S608
    op.execute(  # noqa: S608 - policy text from constants
        f"CREATE POLICY {POLICY_NAME} ON api_keys AS PERMISSIVE FOR ALL TO PUBLIC "
        f"USING (workspace_id = {TENANT}) WITH CHECK (workspace_id = {TENANT})"
    )
