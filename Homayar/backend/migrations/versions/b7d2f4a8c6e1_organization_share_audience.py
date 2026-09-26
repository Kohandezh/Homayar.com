"""Organization share audience, phone recipients and the organization kill switch (WP03-T06).

Revision ID: b7d2f4a8c6e1
Revises: a2f7c4e9d1b3
Create Date: 2026-09-10

* ``workspaces.organization_share_links_enabled`` (default on): switching it
  off hides existing ``organization`` shares of the workspace at once.
* ``share_recipients.phone_e164`` and a nullable ``email``: a recipient row
  carries exactly one address (``ck_share_recipient_address``); phone numbers
  are unique per share like emails.
* No policy changes: the ``share_recipients`` and ``share_links`` policies
  already cover the new audience and column (ADR 0006 amendment).

Downgrade converts ``organization`` shares to the stricter ``workspace``
audience, deletes phone-only recipients (they cannot be represented) and
restores ``email NOT NULL``; nothing becomes more permissive.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2f4a8c6e1"
down_revision: str | Sequence[str] | None = "a2f7c4e9d1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "organization_share_links_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            )
        )

    with op.batch_alter_table("share_recipients", schema=None) as batch_op:
        batch_op.add_column(sa.Column("phone_e164", sa.String(length=20), nullable=True))
        batch_op.alter_column("email", existing_type=sa.String(length=320), nullable=True)
        batch_op.create_unique_constraint("uq_share_recipient_phone", ["share_id", "phone_e164"])
        batch_op.create_check_constraint(
            "ck_share_recipient_address", "(email IS NULL) <> (phone_e164 IS NULL)"
        )


def downgrade() -> None:
    op.execute("UPDATE share_links SET audience = 'WORKSPACE' WHERE audience = 'ORGANIZATION'")
    op.execute("DELETE FROM share_recipients WHERE email IS NULL")

    with op.batch_alter_table("share_recipients", schema=None) as batch_op:
        batch_op.drop_constraint("ck_share_recipient_address", type_="check")
        batch_op.drop_constraint("uq_share_recipient_phone", type_="unique")
        batch_op.alter_column("email", existing_type=sa.String(length=320), nullable=False)
        batch_op.drop_column("phone_e164")

    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.drop_column("organization_share_links_enabled")
