"""Privileged MFA and break-glass (HOM-129, WP23)

Revision ID: d3d1c7aa3cd8
Revises: a3c7e19d4b62
Create Date: 2026-11-13

Adds the storage of the privileged second factor and of the break-glass
windows (ADR 0076):

* ``user_mfa_totp``: one row per user with the TOTP secret, encrypted with
  AES-256-GCM under ``MFA_SECRET_KEYS`` (only the key id, the nonce and the
  ciphertext are stored).  The confirmed secret and a pending re-enrolment sit
  in separate columns, so a re-enrolment never removes the working factor
  before the new one is confirmed.  ``last_used_step`` refuses a replayed code.
* ``mfa_recovery_codes``: single-use recovery codes, only the peppered digest.
* ``break_glass_windows``: who opened a break-glass window for which account,
  why, until when, and who closed it.
* ``users.is_break_glass`` marks a local emergency account; it arrives with
  server default false, so every existing user is an ordinary account.
* ``login_sessions.mfa_verified_at`` / ``mfa_method`` record the last second
  factor of a session; ``break_glass_window_id`` binds a break-glass session to
  the window it was opened in (SET NULL when the window row goes).  Existing
  sessions start unverified, so a privileged user verifies once after this
  revision.

The three tables are user-scoped authentication data like ``login_sessions``:
they are listed in ``UNPROTECTED_TABLES`` (no tenant policy) and get the
application grants through ``apply_row_level_security``.

Downgrade drops the three tables and the four columns.  Every enrolled factor,
every recovery code and the break-glass history are lost; privileged users enrol
again after a later upgrade.  On SQLite both directions rebuild
``login_sessions`` by a copy from a frozen definition (SQLite cannot add or drop a
table-level foreign key in place); the rows keep their values.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "d3d1c7aa3cd8"
down_revision: str | Sequence[str] | None = "a3c7e19d4b62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TOTP = "user_mfa_totp"
RECOVERY_CODES = "mfa_recovery_codes"
WINDOWS = "break_glass_windows"
USERS = "users"
SESSIONS = "login_sessions"
# The copy of ``login_sessions`` while SQLite rebuilds it.
SQLITE_REBUILD = "_hom129_login_sessions"


def _is_sqlite() -> bool:
    return op.get_context().dialect.name == "sqlite"


def _session_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("mfa_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfa_method", sa.String(length=16), nullable=True),
        sa.Column(
            "break_glass_window_id",
            sa.String(length=36),
            sa.ForeignKey(f"{WINDOWS}.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def _sqlite_sessions_table(*, with_mfa: bool) -> sa.Table:
    """``login_sessions`` at this revision (frozen), under the rebuild name."""
    metadata = sa.MetaData()
    for name in (USERS, WINDOWS, SESSIONS):
        sa.Table(name, metadata, sa.Column("id", sa.String(length=36), primary_key=True))
    return sa.Table(
        SQLITE_REBUILD,
        metadata,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey(f"{USERS}.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_agent_hash", sa.String(length=64)),
        sa.Column("ip_prefix", sa.String(length=64)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "rotated_from_id",
            sa.String(length=36),
            sa.ForeignKey(f"{SESSIONS}.id", ondelete="SET NULL"),
        ),
        *(_session_columns() if with_mfa else ()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def _rebuild_sqlite_sessions(*, with_mfa: bool) -> None:
    """Copy-and-move of ``login_sessions`` on SQLite, from a frozen definition.

    SQLite cannot add or drop a table-level foreign key in place.  Batch mode would
    do the same copy, but it orders the constraints differently on every run, so the
    stored table text would change after each round trip.  The rows keep their
    values: Alembic runs SQLite without ``PRAGMA foreign_keys``.
    """
    bind = op.get_bind()
    target = _sqlite_sessions_table(with_mfa=with_mfa)
    present = {column["name"] for column in sa.inspect(bind).get_columns(SESSIONS)}
    shared = ", ".join(column.name for column in target.columns if column.name in present)
    op.execute(sa.schema.CreateTable(target))
    op.execute(
        f"INSERT INTO {SQLITE_REBUILD} ({shared}) SELECT {shared} FROM {SESSIONS}"  # noqa: S608
    )
    op.drop_table(SESSIONS)
    op.rename_table(SQLITE_REBUILD, SESSIONS)
    op.create_index(f"ix_{SESSIONS}_user_id", SESSIONS, ["user_id"], unique=False)
    op.create_index("ix_sessions_user_active", SESSIONS, ["user_id", "revoked_at"], unique=False)


def upgrade() -> None:
    op.create_table(
        TOTP,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=64), nullable=True),
        sa.Column("nonce", sa.LargeBinary(), nullable=True),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_step", sa.BigInteger(), nullable=True),
        sa.Column("pending_encryption_key_id", sa.String(length=64), nullable=True),
        sa.Column("pending_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("pending_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("pending_created_at", sa.DateTime(timezone=True), nullable=True),
        # TimestampMixin sets both in Python; no server default, like the ORM.
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_table(
        RECOVERY_CODES,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index(f"ix_{RECOVERY_CODES}_user_id", RECOVERY_CODES, ["user_id"], unique=False)
    op.create_table(
        WINDOWS,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("opened_by", sa.String(length=128), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(f"ix_{WINDOWS}_user_id", WINDOWS, ["user_id"], unique=False)

    op.add_column(
        USERS,
        sa.Column("is_break_glass", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    if _is_sqlite():
        _rebuild_sqlite_sessions(with_mfa=True)
    else:
        # add_column also adds the column's foreign key (unnamed, as in the ORM).
        for column in _session_columns():
            op.add_column(SESSIONS, column)
    # Grants of the new tables (no tenant policy: they are unprotected).
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (every MFA factor, recovery code and window is lost)."""
    if _is_sqlite():
        _rebuild_sqlite_sessions(with_mfa=False)
    else:
        # The foreign key belongs to the column and goes with it.
        op.drop_column(SESSIONS, "break_glass_window_id")
        op.drop_column(SESSIONS, "mfa_method")
        op.drop_column(SESSIONS, "mfa_verified_at")
    op.drop_column(USERS, "is_break_glass")
    # The indexes and the grants belong to the tables and go with them.
    op.drop_table(WINDOWS)
    op.drop_table(RECOVERY_CODES)
    op.drop_table(TOTP)
