"""Webhook delivery ledger, schema only (HOM-89, WP16-T05)

Revision ID: e4b8d1f6a3c7
Revises: 565d2ccd4fca
Create Date: 2026-10-30

Signed outbound webhooks (F69) need a ledger of what was sent, to whom, and with which
key.  The tables ``webhook_endpoints`` and ``webhook_deliveries`` exist since the first
migration, but nothing used them.  This revision changes them and adds two tables.  It
adds only schema; the signing, the delivery worker and the API come in a later PR.

``webhook_endpoints``:

* ``secret_ciphertext`` becomes nullable.  It is a legacy column; new code never reads
  or writes it, because the secrets move to ``webhook_signing_keys``;
* new ``description`` and ``created_by_user_id`` (``ON DELETE SET NULL``).

``webhook_signing_keys`` (new): the signing secrets of an endpoint.  A secret is
encrypted with AES-GCM under an app keyring key (``encryption_key_id``, ``nonce``,
``secret_ciphertext``).  It is encrypted and not hashed, because the sender needs the
secret itself to compute the HMAC; a hash cannot sign.  ``key_id`` is ``whk_`` and 24
lowercase hex characters (check ``ck_webhook_signing_key_id_format``, unique
``uq_webhook_signing_key_id``).  ``expires_at`` NULL marks the active key, and the
partial unique index ``uq_webhook_signing_keys_active`` allows one active key per
endpoint.  A rotated key keeps signing until it expires.

``webhook_deliveries``:

* the global unique constraint on ``event_id`` is replaced by
  ``uq_webhook_delivery_endpoint_event`` on ``(endpoint_id, event_id)``.  Event ids are
  unique per endpoint and not globally: one event goes to several endpoints with the
  same id, which is the receiver's dedupe key, but it is never queued twice for one
  endpoint;
* new ``payload`` and ``payload_sha256``: the exact bytes that are signed and sent.  The
  bytes are frozen when the event is queued, so every attempt sends the same bytes and
  the same signature input, and a receiver can dedupe a retry;
* new ``last_attempt_at`` and ``delivered_at``;
* checks ``ck_webhook_delivery_state`` (``pending``, ``sending``, ``delivered``,
  ``failed``, ``dead_letter``), ``ck_webhook_delivery_attempt_count``,
  ``ck_webhook_delivery_delivered_at`` and ``ck_webhook_delivery_payload_sha256``, and
  the index ``ix_webhook_deliveries_due`` for the sweeper.

``webhook_delivery_attempts`` (new): one row per HTTP request sent, written once after
the request ends.  It is append-only: the application role may read and insert rows
but never update or delete them, so the log of what left the system cannot be
rewritten.  Cascades from a workspace or a delivery run as the owner and still work.

Row-level security: the two new tables are tenant only.  ``webhook_deliveries`` gets
its own policy: a session may read rows of its bound workspace, or every tenant's rows
under the ``webhook_delivery`` service scope, so the sweeper can find due deliveries.
The scope is read-only: the WITH CHECK refuses every insert and update while the scope
is held, even with a workspace bound, because the USING clause would let such a session
reach another tenant's row.  Every write runs in a session bound to the row's own
workspace, without the scope.  The application role also loses DELETE on
``webhook_deliveries``, because the policy's USING clause would otherwise let the scope
delete every tenant's rows, and the application never deletes a ledger row.  The scope
reaches no endpoint, key or attempt row.

No data changes.  The new columns are nullable.  The new checks hold for rows written
before this revision: the state default is ``pending``, ``attempt_count`` starts at 0,
and ``payload_sha256`` is NULL.  A row that breaks a new check stops the upgrade with
that check's error, and nothing changes.  The one known way to get such a row is a
downgrade and a new upgrade: the downgrade drops ``delivered_at``, so a ``delivered``
row then breaks ``ck_webhook_delivery_delivered_at``.  Change its state first.

Downgrade drops the two new tables, so every signing key and attempt is lost.  It drops
the new columns, checks, index and ``uq_webhook_delivery_endpoint_event``, so the
payloads and their digests are lost too.  It restores the tenant-only policy and the
DELETE grant on ``webhook_deliveries``, and re-creates the unique constraint on
``event_id``.  It refuses to run while one event id is on two delivery rows, and it
deletes nothing to make it fit: remove those rows first.  It fills a NULL
``webhook_endpoints.secret_ciphertext`` with empty bytes, then makes the column NOT
NULL again, and drops ``description`` and ``created_by_user_id``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import APP_ROLE, POLICY_NAME, TENANT, apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "e4b8d1f6a3c7"
down_revision: str | Sequence[str] | None = "565d2ccd4fca"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENDPOINTS = "webhook_endpoints"
KEYS = "webhook_signing_keys"
DELIVERIES = "webhook_deliveries"
ATTEMPTS = "webhook_delivery_attempts"

# The first migration created an unnamed UNIQUE(event_id); PostgreSQL named it itself,
# SQLite batch mode needs a naming convention to find it.
PG_EVENT_UNIQUE = "webhook_deliveries_event_id_key"
SQLITE_NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
SQLITE_EVENT_UNIQUE = "uq_webhook_deliveries_event_id"
# A SQLite database built by ``create_all`` holds the endpoint's creator foreign key
# without a name; this convention gives the reflected key the name the downgrade drops.
SQLITE_FK_NAMING = {"fk": "%(table_name)s_%(column_0_name)s_fkey"}
ENDPOINT_CREATOR_FK = "webhook_endpoints_created_by_user_id_fkey"
ACTIVE_KEY_WHERE = sa.text("expires_at IS NULL")

# Frozen copies of the checks in ``backend/database/saas_models.py`` at this revision.
KEY_ID_CHECK = (
    "length(key_id) = 28 AND substr(key_id, 1, 4) = 'whk_' "
    "AND trim(substr(key_id, 5), '0123456789abcdef') = ''"
)
NONCE_CHECK = "length(nonce) = 12"
STATE_CHECK = "state IN ('pending', 'sending', 'delivered', 'failed', 'dead_letter')"
DELIVERED_AT_CHECK = "state <> 'delivered' OR delivered_at IS NOT NULL"
PAYLOAD_SHA256_CHECK = (
    "payload_sha256 IS NULL OR (length(payload_sha256) = 64 "
    "AND trim(payload_sha256, '0123456789abcdef') = '')"
)
OUTCOME_CHECK = "outcome IN ('delivered', 'failed')"
STATUS_CODE_CHECK = "status_code IS NULL OR status_code BETWEEN 100 AND 599"
SHAPE_CHECK = (
    "(outcome = 'delivered' AND status_code IS NOT NULL AND status_code BETWEEN 200 AND 299 "
    "AND error_code IS NULL) OR (outcome = 'failed' AND error_code IS NOT NULL)"
)


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table(ENDPOINTS, schema=None) as batch_op:
        batch_op.alter_column("secret_ciphertext", existing_type=sa.LargeBinary(), nullable=True)
        batch_op.add_column(sa.Column("description", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("created_by_user_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            ENDPOINT_CREATOR_FK, "users", ["created_by_user_id"], ["id"], ondelete="SET NULL"
        )

    op.create_table(
        KEYS,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("endpoint_id", sa.String(length=36), nullable=False),
        sa.Column("key_id", sa.String(length=40), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=40), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(KEY_ID_CHECK, name="ck_webhook_signing_key_id_format"),
        sa.CheckConstraint(NONCE_CHECK, name="ck_webhook_signing_key_nonce"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["endpoint_id"], [f"{ENDPOINTS}.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_id", name="uq_webhook_signing_key_id"),
    )
    with op.batch_alter_table(KEYS, schema=None) as batch_op:
        batch_op.create_index(batch_op.f(f"ix_{KEYS}_workspace_id"), ["workspace_id"])
        batch_op.create_index(batch_op.f(f"ix_{KEYS}_endpoint_id"), ["endpoint_id"])
    op.create_index(
        "uq_webhook_signing_keys_active",
        KEYS,
        ["endpoint_id"],
        unique=True,
        postgresql_where=ACTIVE_KEY_WHERE,
        sqlite_where=ACTIVE_KEY_WHERE,
    )

    if _is_sqlite():
        with op.batch_alter_table(
            DELIVERIES, schema=None, naming_convention=SQLITE_NAMING
        ) as batch_op:
            batch_op.drop_constraint(SQLITE_EVENT_UNIQUE, type_="unique")
    else:
        op.drop_constraint(PG_EVENT_UNIQUE, DELIVERIES, type_="unique")
    with op.batch_alter_table(DELIVERIES, schema=None) as batch_op:
        batch_op.add_column(sa.Column("payload", sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column("payload_sha256", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_unique_constraint(
            "uq_webhook_delivery_endpoint_event", ["endpoint_id", "event_id"]
        )
        batch_op.create_check_constraint("ck_webhook_delivery_state", STATE_CHECK)
        batch_op.create_check_constraint("ck_webhook_delivery_attempt_count", "attempt_count >= 0")
        batch_op.create_check_constraint("ck_webhook_delivery_delivered_at", DELIVERED_AT_CHECK)
        batch_op.create_check_constraint("ck_webhook_delivery_payload_sha256", PAYLOAD_SHA256_CHECK)
        batch_op.create_index("ix_webhook_deliveries_due", ["state", "next_attempt_at"])

    op.create_table(
        ATTEMPTS,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("delivery_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("request_timestamp", sa.BigInteger(), nullable=False),
        sa.Column("signing_key_ids", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt_number >= 1", name="ck_webhook_attempt_number_positive"),
        sa.CheckConstraint("duration_ms >= 0", name="ck_webhook_attempt_duration"),
        sa.CheckConstraint(OUTCOME_CHECK, name="ck_webhook_attempt_outcome"),
        sa.CheckConstraint(STATUS_CODE_CHECK, name="ck_webhook_attempt_status_code"),
        sa.CheckConstraint(SHAPE_CHECK, name="ck_webhook_attempt_shape"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["delivery_id"], [f"{DELIVERIES}.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_id", "attempt_number", name="uq_webhook_attempt_number"),
    )
    with op.batch_alter_table(ATTEMPTS, schema=None) as batch_op:
        batch_op.create_index(batch_op.f(f"ix_{ATTEMPTS}_workspace_id"), ["workspace_id"])
        batch_op.create_index(batch_op.f(f"ix_{ATTEMPTS}_delivery_id"), ["delivery_id"])

    # The tenant policies of the new tables, the new webhook_deliveries policy, the
    # grants, the append-only revoke on the attempts and the DELETE revoke on the
    # deliveries.
    apply_row_level_security(op.get_bind())


def _without_force(
    connection: sa.Connection, table: str, statement: str, **params: object
) -> list[sa.Row]:
    """Run one statement with FORCE ROW LEVEL SECURITY lifted on ``table``.

    The policy is FORCED, so a table owner that is not a superuser sees no rows without
    a tenant (as in 6d4ec2d2e498).  FORCE is set again right after the statement.
    """
    postgres = connection.dialect.name == "postgresql"
    if postgres:
        connection.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
    try:
        result = connection.execute(sa.text(statement), params)
        return result.all() if result.returns_rows else []
    finally:
        if postgres:
            connection.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))


def downgrade() -> None:
    """Downgrade schema (signing keys, attempts and payloads are lost)."""
    bind = op.get_bind()
    duplicates = _without_force(
        bind,
        DELIVERIES,
        f"SELECT event_id FROM {DELIVERIES} GROUP BY event_id HAVING COUNT(*) > 1",  # noqa: S608
    )
    if duplicates:
        raise RuntimeError(
            f"{DELIVERIES} has {len(duplicates)} event id(s) on more than one row; the "
            f"previous schema keeps event_id globally unique.  Remove those rows before "
            f"downgrading {revision}; the downgrade deletes nothing."
        )

    with op.batch_alter_table(ATTEMPTS, schema=None) as batch_op:
        batch_op.drop_index(batch_op.f(f"ix_{ATTEMPTS}_delivery_id"))
        batch_op.drop_index(batch_op.f(f"ix_{ATTEMPTS}_workspace_id"))
    op.drop_table(ATTEMPTS)

    op.drop_index("uq_webhook_signing_keys_active", table_name=KEYS)
    with op.batch_alter_table(KEYS, schema=None) as batch_op:
        batch_op.drop_index(batch_op.f(f"ix_{KEYS}_endpoint_id"))
        batch_op.drop_index(batch_op.f(f"ix_{KEYS}_workspace_id"))
    op.drop_table(KEYS)

    with op.batch_alter_table(DELIVERIES, schema=None) as batch_op:
        batch_op.drop_index("ix_webhook_deliveries_due")
        batch_op.drop_constraint("ck_webhook_delivery_payload_sha256", type_="check")
        batch_op.drop_constraint("ck_webhook_delivery_delivered_at", type_="check")
        batch_op.drop_constraint("ck_webhook_delivery_attempt_count", type_="check")
        batch_op.drop_constraint("ck_webhook_delivery_state", type_="check")
        batch_op.drop_constraint("uq_webhook_delivery_endpoint_event", type_="unique")
        batch_op.drop_column("delivered_at")
        batch_op.drop_column("last_attempt_at")
        batch_op.drop_column("payload_sha256")
        batch_op.drop_column("payload")
    if _is_sqlite():
        with op.batch_alter_table(
            DELIVERIES, schema=None, naming_convention=SQLITE_NAMING
        ) as batch_op:
            batch_op.create_unique_constraint(SQLITE_EVENT_UNIQUE, ["event_id"])
    else:
        op.create_unique_constraint(PG_EVENT_UNIQUE, DELIVERIES, ["event_id"])
        # The tenant-only policy and the DELETE grant of the previous schema.  Frozen
        # SQL: the live registry now holds the new policy.
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {DELIVERIES}")  # noqa: S608
        op.execute(  # noqa: S608 - policy text from constants
            f"CREATE POLICY {POLICY_NAME} ON {DELIVERIES} AS PERMISSIVE FOR ALL TO PUBLIC "
            f"USING (workspace_id = {TENANT}) WITH CHECK (workspace_id = {TENANT})"
        )
        op.execute(f"GRANT DELETE ON {DELIVERIES} TO {APP_ROLE}")

    # Endpoints written after the upgrade hold no legacy secret.  Empty bytes keep the
    # rows, so the column can be NOT NULL again.
    _without_force(
        bind,
        ENDPOINTS,
        f"UPDATE {ENDPOINTS} SET secret_ciphertext = :empty "  # noqa: S608
        "WHERE secret_ciphertext IS NULL",
        empty=b"",
    )
    naming = SQLITE_FK_NAMING if _is_sqlite() else None
    with op.batch_alter_table(ENDPOINTS, schema=None, naming_convention=naming) as batch_op:
        batch_op.drop_constraint(ENDPOINT_CREATOR_FK, type_="foreignkey")
        batch_op.drop_column("created_by_user_id")
        batch_op.drop_column("description")
        batch_op.alter_column("secret_ciphertext", existing_type=sa.LargeBinary(), nullable=False)
