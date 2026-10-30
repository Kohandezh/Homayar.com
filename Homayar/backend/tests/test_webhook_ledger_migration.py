"""HOM-89 PR 1: the webhook delivery ledger, schema only, and its one migration.

The tables ``webhook_endpoints`` and ``webhook_deliveries`` existed since the first
migration, unused.  This revision changes them and adds ``webhook_signing_keys`` and
``webhook_delivery_attempts``, so signed outbound webhooks (PR 2) have a ledger.  The
tests check:

* the ORM declares every column, constraint and index of DESIGN section 2 by its exact
  name, and the check strings are built from the two enums;
* ``database/rls.py`` lists the two new tables as workspace tables, the attempts as
  append-only, ``webhook_deliveries`` as no-delete with its own policy, and
  ``tenant_context`` has the ``webhook_delivery`` service scope;
* on PostgreSQL, the database refuses every deny case of SPEC section 4 on the named
  constraint, and the closest allow case passes, on the ORM schema and on the migrated one;
* tenant isolation on the four webhook tables, the service-scope rules, the append-only
  rule and the no-delete rule, through the application role;
* upgrade from ``565d2ccd4fca`` gives the ORM schema; downgrade restores the base schema
  (the old unique constraint on ``event_id``, the tenant-only policy, the DELETE grant),
  refuses to run while one event id is on two rows, and leaves every other table as it was.
"""

from __future__ import annotations

import hashlib
import importlib
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import BigInteger, DateTime, Integer, LargeBinary, String, Table, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.types import JSON, Boolean

from backend.config import Settings
from backend.database.relational import get_session_factory
from backend.database.rls import (
    APP_ROLE,
    APPEND_ONLY_TABLES,
    NO_DELETE_TABLES,
    POLICIES,
    POLICY_NAME,
    SERVICE,
    SHARED_CONTENT_TABLES,
    TENANT,
    UNPROTECTED_TABLES,
    WORKSPACE_TABLES,
)
from backend.database.saas_models import (
    WEBHOOK_ATTEMPT_OUTCOME_CHECK,
    WEBHOOK_ATTEMPT_SHAPE_CHECK,
    WEBHOOK_ATTEMPT_STATUS_CODE_CHECK,
    WEBHOOK_DELIVERY_DELIVERED_AT_CHECK,
    WEBHOOK_DELIVERY_PAYLOAD_SHA256_CHECK,
    WEBHOOK_DELIVERY_STATE_CHECK,
    WEBHOOK_SIGNING_KEY_ID_CHECK,
    WEBHOOK_SIGNING_KEY_NONCE_CHECK,
    WebhookAttemptOutcome,
    WebhookDelivery,
    WebhookDeliveryAttempt,
    WebhookDeliveryState,
    WebhookEndpoint,
    WebhookSigningKey,
    new_id,
    utcnow,
)
from backend.database.tenant_context import (
    SERVICE_SCOPE_DISPATCH,
    SERVICE_SCOPE_WEBHOOK_DELIVERY,
    bind_service_scope,
    bind_tenant_context,
)
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user, register_user

NEW_REVISION = "e4b8d1f6a3c7"
BASE_REVISION = "565d2ccd4fca"  # HOM-88: CRM mapping tables
TABLES = (
    "webhook_endpoints",
    "webhook_signing_keys",
    "webhook_deliveries",
    "webhook_delivery_attempts",
)
NEW_TABLES = ("webhook_signing_keys", "webhook_delivery_attempts")
TENANT_ONLY = (f"workspace_id = {TENANT}", f"workspace_id = {TENANT}")


def _requires_postgres() -> None:
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")


# --- the ORM -----------------------------------------------------------------------------


def _columns(table: Table) -> dict[str, tuple[type, int | None, bool]]:
    """Each column as (type class, length, nullable)."""
    return {
        column.name: (type(column.type), getattr(column.type, "length", None), column.nullable)
        for column in table.columns
    }


def _foreign_keys(table: Table) -> dict[str, tuple[str, str | None]]:
    return {key.parent.name: (key.target_fullname, key.ondelete) for key in table.foreign_keys}


def _named(table: Table) -> dict[str, object]:
    return {constraint.name: constraint for constraint in table.constraints if constraint.name}


def _checks(table: Table) -> dict[str, str]:
    return {
        name: str(constraint.sqltext)
        for name, constraint in _named(table).items()
        if name.startswith("ck_")
    }


def _indexes(table: Table) -> dict[str, tuple[list[str], bool]]:
    return {
        index.name: ([column.name for column in index.columns], bool(index.unique))
        for index in table.indexes
    }


def _assert_timestamps(table: Table, names: tuple[str, ...]) -> None:
    for name in names:
        assert table.c[name].type.timezone, name
    assert table.c.id.default.arg.__name__ == new_id.__name__
    assert table.c.created_at.default.arg.__name__ == utcnow.__name__


def test_the_endpoint_gains_a_description_and_its_creator():
    table = WebhookEndpoint.__table__
    assert _columns(table) == {
        "id": (String, 36, False),
        "workspace_id": (String, 36, False),
        "url": (String, 1000, False),
        # Legacy: new code keeps the signing secrets in webhook_signing_keys.
        "secret_ciphertext": (LargeBinary, None, True),
        "event_types": (JSON, None, False),
        "is_active": (Boolean, None, False),
        "description": (String, 200, True),
        "created_by_user_id": (String, 36, True),
        "created_at": (DateTime, None, False),
        "updated_at": (DateTime, None, False),
    }
    assert _foreign_keys(table) == {
        "workspace_id": ("workspaces.id", "CASCADE"),
        "created_by_user_id": ("users.id", "SET NULL"),
    }
    assert _checks(table) == {}
    assert _indexes(table) == {"ix_webhook_endpoints_workspace_id": (["workspace_id"], False)}
    _assert_timestamps(table, ("created_at", "updated_at"))


def test_the_signing_keys_table():
    table = WebhookSigningKey.__table__
    assert table.name == "webhook_signing_keys"
    assert _columns(table) == {
        "id": (String, 36, False),
        "workspace_id": (String, 36, False),
        "endpoint_id": (String, 36, False),
        "key_id": (String, 40, False),
        "encryption_key_id": (String, 40, False),
        "nonce": (LargeBinary, None, False),
        "secret_ciphertext": (LargeBinary, None, False),
        "expires_at": (DateTime, None, True),
        "created_by_user_id": (String, 36, True),
        "created_at": (DateTime, None, False),
        "updated_at": (DateTime, None, False),
    }
    assert [column.name for column in table.primary_key] == ["id"]
    assert _foreign_keys(table) == {
        "workspace_id": ("workspaces.id", "CASCADE"),
        "endpoint_id": ("webhook_endpoints.id", "CASCADE"),
        "created_by_user_id": ("users.id", "SET NULL"),
    }
    named = _named(table)
    assert [column.name for column in named["uq_webhook_signing_key_id"].columns] == ["key_id"]
    assert _checks(table) == {
        "ck_webhook_signing_key_id_format": WEBHOOK_SIGNING_KEY_ID_CHECK,
        "ck_webhook_signing_key_nonce": WEBHOOK_SIGNING_KEY_NONCE_CHECK,
    }
    assert _indexes(table) == {
        "ix_webhook_signing_keys_workspace_id": (["workspace_id"], False),
        "ix_webhook_signing_keys_endpoint_id": (["endpoint_id"], False),
        "uq_webhook_signing_keys_active": (["endpoint_id"], True),
    }
    # At most one active key per endpoint, on both dialects.
    active = next(i for i in table.indexes if i.name == "uq_webhook_signing_keys_active")
    assert str(active.dialect_options["postgresql"]["where"]) == "expires_at IS NULL"
    assert str(active.dialect_options["sqlite"]["where"]) == "expires_at IS NULL"
    _assert_timestamps(table, ("expires_at", "created_at", "updated_at"))


def test_the_deliveries_table_is_unique_per_endpoint_and_event():
    table = WebhookDelivery.__table__
    assert _columns(table) == {
        "id": (String, 36, False),
        "workspace_id": (String, 36, False),
        "endpoint_id": (String, 36, False),
        "event_id": (String, 36, False),
        "event_type": (String, 100, False),
        "state": (String, 24, False),
        "attempt_count": (Integer, None, False),
        "next_attempt_at": (DateTime, None, True),
        "last_status_code": (Integer, None, True),
        "last_error": (String, 500, True),
        "payload": (LargeBinary, None, True),
        "payload_sha256": (String, 64, True),
        "last_attempt_at": (DateTime, None, True),
        "delivered_at": (DateTime, None, True),
        "created_at": (DateTime, None, False),
        "updated_at": (DateTime, None, False),
    }
    assert _foreign_keys(table) == {
        "workspace_id": ("workspaces.id", "CASCADE"),
        "endpoint_id": ("webhook_endpoints.id", "CASCADE"),
    }
    # The global unique event id is gone: one event goes to many endpoints.
    assert not table.c.event_id.unique
    named = _named(table)
    assert [column.name for column in named["uq_webhook_delivery_endpoint_event"].columns] == [
        "endpoint_id",
        "event_id",
    ]
    assert _checks(table) == {
        "ck_webhook_delivery_state": WEBHOOK_DELIVERY_STATE_CHECK,
        "ck_webhook_delivery_attempt_count": "attempt_count >= 0",
        "ck_webhook_delivery_delivered_at": WEBHOOK_DELIVERY_DELIVERED_AT_CHECK,
        "ck_webhook_delivery_payload_sha256": WEBHOOK_DELIVERY_PAYLOAD_SHA256_CHECK,
    }
    assert _indexes(table) == {
        "ix_webhook_deliveries_workspace_id": (["workspace_id"], False),
        "ix_webhook_deliveries_endpoint_id": (["endpoint_id"], False),
        "ix_webhook_deliveries_due": (["state", "next_attempt_at"], False),
    }
    assert table.c.state.default.arg == WebhookDeliveryState.PENDING.value == "pending"
    assert table.c.attempt_count.default.arg == 0
    _assert_timestamps(
        table,
        ("next_attempt_at", "last_attempt_at", "delivered_at", "created_at", "updated_at"),
    )


def test_the_attempts_table_has_no_updated_at():
    table = WebhookDeliveryAttempt.__table__
    assert table.name == "webhook_delivery_attempts"
    assert _columns(table) == {
        "id": (String, 36, False),
        "workspace_id": (String, 36, False),
        "delivery_id": (String, 36, False),
        "attempt_number": (Integer, None, False),
        "request_timestamp": (BigInteger, None, False),
        "signing_key_ids": (JSON, None, False),
        "started_at": (DateTime, None, False),
        "duration_ms": (Integer, None, False),
        "outcome": (String, 16, False),
        "status_code": (Integer, None, True),
        "error_code": (String, 40, True),
        "created_at": (DateTime, None, False),
    }
    assert _foreign_keys(table) == {
        "workspace_id": ("workspaces.id", "CASCADE"),
        "delivery_id": ("webhook_deliveries.id", "CASCADE"),
    }
    named = _named(table)
    assert [column.name for column in named["uq_webhook_attempt_number"].columns] == [
        "delivery_id",
        "attempt_number",
    ]
    assert _checks(table) == {
        "ck_webhook_attempt_number_positive": "attempt_number >= 1",
        "ck_webhook_attempt_duration": "duration_ms >= 0",
        "ck_webhook_attempt_outcome": WEBHOOK_ATTEMPT_OUTCOME_CHECK,
        "ck_webhook_attempt_status_code": WEBHOOK_ATTEMPT_STATUS_CODE_CHECK,
        "ck_webhook_attempt_shape": WEBHOOK_ATTEMPT_SHAPE_CHECK,
    }
    assert _indexes(table) == {
        "ix_webhook_delivery_attempts_workspace_id": (["workspace_id"], False),
        "ix_webhook_delivery_attempts_delivery_id": (["delivery_id"], False),
    }
    _assert_timestamps(table, ("started_at", "created_at"))


def test_the_checks_are_built_from_the_enums():
    assert [member.value for member in WebhookDeliveryState] == [
        "pending",
        "sending",
        "delivered",
        "failed",
        "dead_letter",
    ]
    assert [member.value for member in WebhookAttemptOutcome] == ["delivered", "failed"]
    assert WEBHOOK_DELIVERY_STATE_CHECK == (
        "state IN ('pending', 'sending', 'delivered', 'failed', 'dead_letter')"
    )
    assert WEBHOOK_DELIVERY_DELIVERED_AT_CHECK == (
        "state <> 'delivered' OR delivered_at IS NOT NULL"
    )
    assert WEBHOOK_ATTEMPT_OUTCOME_CHECK == "outcome IN ('delivered', 'failed')"
    # SPEC Addendum 1, A1: NULL-safe, so 'delivered' with a NULL status is refused.
    assert WEBHOOK_ATTEMPT_SHAPE_CHECK == (
        "(outcome = 'delivered' AND status_code IS NOT NULL AND status_code BETWEEN 200 AND 299 "
        "AND error_code IS NULL) OR (outcome = 'failed' AND error_code IS NOT NULL)"
    )
    assert WEBHOOK_ATTEMPT_STATUS_CODE_CHECK == (
        "status_code IS NULL OR status_code BETWEEN 100 AND 599"
    )
    # No regex, so SQLite runs the same checks.
    assert WEBHOOK_SIGNING_KEY_ID_CHECK == (
        "length(key_id) = 28 AND substr(key_id, 1, 4) = 'whk_' "
        "AND trim(substr(key_id, 5), '0123456789abcdef') = ''"
    )
    assert WEBHOOK_SIGNING_KEY_NONCE_CHECK == "length(nonce) = 12"
    assert WEBHOOK_DELIVERY_PAYLOAD_SHA256_CHECK == (
        "payload_sha256 IS NULL OR (length(payload_sha256) = 64 "
        "AND trim(payload_sha256, '0123456789abcdef') = '')"
    )


# --- the RLS registry --------------------------------------------------------------------


def test_the_rls_registry_lists_the_webhook_tables():
    for table in NEW_TABLES:
        assert table in WORKSPACE_TABLES, table
        assert POLICIES[table] == TENANT_ONLY, table
    assert POLICIES["webhook_endpoints"] == TENANT_ONLY
    assert "webhook_delivery_attempts" in APPEND_ONLY_TABLES
    assert "webhook_signing_keys" not in APPEND_ONLY_TABLES  # rotation sets expires_at
    # The sweeper discovers due rows across tenants; no row is written while the scope is
    # held, even with a workspace bound (SPEC Addendum 2).
    assert "webhook_deliveries" not in WORKSPACE_TABLES
    assert POLICIES["webhook_deliveries"] == (
        f"workspace_id = {TENANT} OR {SERVICE} = 'webhook_delivery'",
        f"workspace_id = {TENANT} AND {SERVICE} IS DISTINCT FROM 'webhook_delivery'",
    )
    # The application never deletes a ledger row (SPEC Addendum 1, A2).
    assert NO_DELETE_TABLES == ("integration_inbound_records", "webhook_deliveries")
    assert "webhook_deliveries" not in APPEND_ONLY_TABLES  # the worker updates the state
    for table in TABLES:
        assert table not in SHARED_CONTENT_TABLES, table  # never through a share token
        assert table not in UNPROTECTED_TABLES, table
    assert SERVICE_SCOPE_WEBHOOK_DELIVERY == "webhook_delivery"
    # The scope unlocks nothing else.
    for table, (using, check) in POLICIES.items():
        if table != "webhook_deliveries":
            assert "'webhook_delivery'" not in using + check, table


# --- the migration -----------------------------------------------------------------------


def _migration_module():
    return importlib.import_module(
        f"backend.migrations.versions.{NEW_REVISION}_webhook_delivery_ledger"
    )


def test_the_migration_follows_the_previous_head():
    module = _migration_module()
    assert module.revision == NEW_REVISION
    assert module.down_revision == BASE_REVISION
    doc = module.__doc__
    assert "HOM-89" in doc
    # DESIGN 2.6: the points the docstring covers.
    for phrase in (
        "encrypted",
        "hash",
        "frozen",
        "per endpoint",
        "append-only",
        "webhook_delivery",
        "read-only",
        "Downgrade",
        "refuses",
    ):
        assert phrase in doc, phrase


# --- PostgreSQL: rows --------------------------------------------------------------------

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
DIGEST = hashlib.sha256(b"{}").hexdigest()


def _scalar(sql: str, **params):
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


def _tenant(client, email_outbox, label: str) -> dict:
    credentials = register_user(client, email_outbox, email=f"webhook-{label}@example.com")
    session = login_user(client, credentials)
    return {"user_id": session["user"]["id"], "workspace_id": session["workspaces"][0]["id"]}


def _alembic(monkeypatch, settings: Settings):
    from alembic.config import Config

    from backend.config import reset_settings_cache
    from backend.observability.release import PROJECT_ROOT

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _key_id() -> str:
    return "whk_" + secrets.token_hex(12)


# Each row builder sets only columns that exist on the base schema too, unless a test
# passes more.  Core inserts fill the Python defaults (id, state, timestamps) as the ORM
# would.
def _endpoint(tenant: dict, **extra: object) -> dict[str, object]:
    return {
        "id": new_id(),
        "workspace_id": tenant["workspace_id"],
        "url": "https://crm.example.com/hooks",
        **extra,
    }


def _delivery(tenant: dict, endpoint_id: str, **extra: object) -> dict[str, object]:
    return {
        "id": new_id(),
        "workspace_id": tenant["workspace_id"],
        "endpoint_id": endpoint_id,
        "event_id": new_id(),
        "event_type": "webhook.test",
        **extra,
    }


def _key(tenant: dict, endpoint_id: str, **extra: object) -> dict[str, object]:
    return {
        "id": new_id(),
        "workspace_id": tenant["workspace_id"],
        "endpoint_id": endpoint_id,
        "key_id": _key_id(),
        "encryption_key_id": "keyring-1",
        "nonce": bytes(12),
        "secret_ciphertext": b"\x01" * 65,
        # A retiring key by default, so only the active-key cases meet the partial index.
        "expires_at": NOW + timedelta(days=1),
        **extra,
    }


def _attempt(tenant: dict, delivery_id: str, number: int, **extra: object) -> dict[str, object]:
    return {
        "id": new_id(),
        "workspace_id": tenant["workspace_id"],
        "delivery_id": delivery_id,
        "attempt_number": number,
        "request_timestamp": int(NOW.timestamp()),
        "signing_key_ids": [_key_id()],
        "started_at": NOW,
        "duration_ms": 120,
        "outcome": "delivered",
        "status_code": 204,
        "error_code": None,
        **extra,
    }


def _add(model, row: dict[str, object]) -> str:
    """Insert one row as the DBA (no row-level security); returns its id."""
    with inspection_session_factory()() as db:
        db.execute(model.__table__.insert().values(**row))
        db.commit()
    return str(row["id"])


def _refused(model, row: dict[str, object], constraint: str) -> str:
    """The row is refused on exactly ``constraint``; returns the id it would have had."""
    with pytest.raises(IntegrityError, match=f'"{constraint}"'):
        _add(model, row)
    return str(row["id"])


def _ids(table: str) -> set[str]:
    with inspection_session_factory()() as db:
        return set(db.scalars(text(f"SELECT id FROM {table}")).all())  # noqa: S608


def _assert_rules(first: dict, second: dict, label: str) -> None:
    """Every deny case of SPEC section 4 at the database level, and its closest allow."""
    kept: dict[str, set[str]] = {table: set() for table in TABLES}
    refused: set[str] = set()

    def add(model, row):
        kept[model.__tablename__].add(_add(model, row))
        return str(row["id"])

    def deny(model, row, constraint):
        refused.add(_refused(model, row, constraint))

    a1 = add(WebhookEndpoint, _endpoint(first, description=f"{label} a1"))
    a2 = add(WebhookEndpoint, _endpoint(first, created_by_user_id=first["user_id"]))
    b1 = add(WebhookEndpoint, _endpoint(second))

    # One event per endpoint; the same event id on another endpoint is fine.
    event_id = new_id()
    add(WebhookDelivery, _delivery(first, a1, event_id=event_id))
    deny(
        WebhookDelivery,
        _delivery(first, a1, event_id=event_id),
        "uq_webhook_delivery_endpoint_event",
    )
    add(WebhookDelivery, _delivery(first, a2, event_id=event_id))
    add(WebhookDelivery, _delivery(second, b1, event_id=event_id))

    for state in ("queued", "PENDING", "Delivered", ""):
        deny(WebhookDelivery, _delivery(first, a1, state=state), "ck_webhook_delivery_state")
    for member in WebhookDeliveryState:
        delivered_at = NOW if member is WebhookDeliveryState.DELIVERED else None
        add(WebhookDelivery, _delivery(first, a1, state=member.value, delivered_at=delivered_at))

    deny(
        WebhookDelivery, _delivery(first, a1, attempt_count=-1), "ck_webhook_delivery_attempt_count"
    )
    add(WebhookDelivery, _delivery(first, a1, attempt_count=0))

    deny(
        WebhookDelivery, _delivery(first, a1, state="delivered"), "ck_webhook_delivery_delivered_at"
    )
    add(WebhookDelivery, _delivery(first, a1, state="delivered", delivered_at=NOW))
    add(WebhookDelivery, _delivery(first, a1, state="failed", delivered_at=None))

    for digest in (DIGEST[:63], DIGEST[:63] + "A", DIGEST.upper(), DIGEST[:63] + "g"):
        deny(
            WebhookDelivery,
            _delivery(first, a1, payload=b"{}", payload_sha256=digest),
            "ck_webhook_delivery_payload_sha256",
        )
    add(WebhookDelivery, _delivery(first, a1, payload=b"{}", payload_sha256=DIGEST))
    add(WebhookDelivery, _delivery(first, a1, payload=None, payload_sha256=None))

    # Signing keys.
    for key_id in (
        "whk_" + "a" * 23,
        "WHK_" + "a" * 24,
        "whk_" + "a" * 23 + "g",
        "whx_" + "a" * 24,
        "whk_" + "A" * 24,
    ):
        deny(WebhookSigningKey, _key(first, a1, key_id=key_id), "ck_webhook_signing_key_id_format")
    taken = _key_id()
    add(WebhookSigningKey, _key(first, a1, key_id=taken))
    deny(WebhookSigningKey, _key(first, a2, key_id=taken), "uq_webhook_signing_key_id")
    deny(WebhookSigningKey, _key(second, b1, key_id=taken), "uq_webhook_signing_key_id")
    add(WebhookSigningKey, _key(first, a2))

    for size in (11, 13, 0):
        deny(WebhookSigningKey, _key(first, a1, nonce=bytes(size)), "ck_webhook_signing_key_nonce")
    add(WebhookSigningKey, _key(first, a1, nonce=bytes(12)))

    add(WebhookSigningKey, _key(first, a1, expires_at=None))
    deny(WebhookSigningKey, _key(first, a1, expires_at=None), "uq_webhook_signing_keys_active")
    add(WebhookSigningKey, _key(first, a1, expires_at=NOW + timedelta(hours=1)))
    add(WebhookSigningKey, _key(first, a2, expires_at=None))
    add(WebhookSigningKey, _key(second, b1, expires_at=None))

    # Attempts.  A refused row takes a number nobody uses, so only its own check fails.
    delivery = add(WebhookDelivery, _delivery(first, a1, state="sending", attempt_count=1))
    numbers = iter(range(3, 100))
    unused = 1000

    deny(WebhookDeliveryAttempt, _attempt(first, delivery, 0), "ck_webhook_attempt_number_positive")
    add(WebhookDeliveryAttempt, _attempt(first, delivery, 1))
    deny(WebhookDeliveryAttempt, _attempt(first, delivery, 1), "uq_webhook_attempt_number")
    add(WebhookDeliveryAttempt, _attempt(first, delivery, 2))

    deny(
        WebhookDeliveryAttempt,
        _attempt(first, delivery, unused, outcome="ok"),
        "ck_webhook_attempt_outcome",
    )
    add(WebhookDeliveryAttempt, _attempt(first, delivery, next(numbers), outcome="delivered"))
    add(
        WebhookDeliveryAttempt,
        _attempt(
            first,
            delivery,
            next(numbers),
            outcome="failed",
            error_code="http_status",
            status_code=500,
        ),
    )

    for code in (99, 600):
        deny(
            WebhookDeliveryAttempt,
            _attempt(
                first,
                delivery,
                unused,
                outcome="failed",
                status_code=code,
                error_code="http_status",
            ),
            "ck_webhook_attempt_status_code",
        )
    for code in (100, 599):
        add(
            WebhookDeliveryAttempt,
            _attempt(
                first,
                delivery,
                next(numbers),
                outcome="failed",
                status_code=code,
                error_code="http_status",
            ),
        )

    # A delivered attempt has a 2xx status and no error code.
    for extra in (
        {"status_code": 500},
        {"status_code": 302},
        {"status_code": 204, "error_code": "http_status"},
        {"status_code": None},
    ):
        deny(
            WebhookDeliveryAttempt,
            _attempt(first, delivery, unused, outcome="delivered", **extra),
            "ck_webhook_attempt_shape",
        )
    for code in (200, 204, 299):
        add(WebhookDeliveryAttempt, _attempt(first, delivery, next(numbers), status_code=code))
    # A failed attempt has an error code; the status may be missing (no response).
    for extra in (
        {"status_code": 500, "error_code": None},
        {"status_code": None, "error_code": None},
    ):
        deny(
            WebhookDeliveryAttempt,
            _attempt(first, delivery, unused, outcome="failed", **extra),
            "ck_webhook_attempt_shape",
        )
    add(
        WebhookDeliveryAttempt,
        _attempt(
            first, delivery, next(numbers), outcome="failed", status_code=None, error_code="timeout"
        ),
    )

    deny(
        WebhookDeliveryAttempt,
        _attempt(first, delivery, unused, duration_ms=-1),
        "ck_webhook_attempt_duration",
    )
    add(WebhookDeliveryAttempt, _attempt(first, delivery, next(numbers), duration_ms=0))

    for table in TABLES:
        stored = _ids(table)
        assert kept[table] <= stored, table
        assert not refused & stored, table


# --- PostgreSQL: the ORM schema (create_all) ---------------------------------------------


def _shape(table: str) -> dict:
    """One table as the catalog shows it: columns, constraints, indexes, policy, grants."""
    with inspection_session_factory()() as db:
        columns = db.execute(
            text(
                "SELECT column_name, data_type, character_maximum_length, is_nullable, "
                "column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table},
        ).all()
        constraints = db.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = to_regclass(:t)"
            ),
            {"t": f"public.{table}"},
        ).all()
        indexes = db.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :t"), {"t": table}
        ).all()
        policies = db.execute(
            text(
                "SELECT policyname, permissive, roles::text, cmd, qual, with_check "
                "FROM pg_policies WHERE tablename = :t"
            ),
            {"t": table},
        ).all()
        flags = db.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = to_regclass(:t)"
            ),
            {"t": f"public.{table}"},
        ).all()
        grants = db.execute(
            text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_schema = 'public' AND table_name = :t AND grantee = :r"
            ),
            {"t": table, "r": APP_ROLE},
        ).all()
    return {
        "columns": {name: tuple(rest) for name, *rest in columns},
        "constraints": dict(constraints),
        "indexes": dict(indexes),
        "policies": {name: tuple(rest) for name, *rest in policies},
        "rls": [tuple(row) for row in flags],
        "grants": sorted(row[0] for row in grants),
    }


def _shapes() -> dict[str, dict]:
    return {table: _shape(table) for table in TABLES}


ABSENT = {"columns": {}, "constraints": {}, "indexes": {}, "policies": {}, "rls": [], "grants": []}


def _other_tables() -> dict:
    """Every table but the four webhook tables, so a test can prove it did not change."""
    queries = {
        "tables": (
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> ALL(:t)"
        ),
        "columns": (
            "SELECT table_name, column_name, data_type, character_maximum_length, "
            "is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name <> ALL(:t)"
        ),
        "constraints": (
            "SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid) "
            "FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
            "WHERE n.nspname = 'public' AND c.conrelid <> 0 "
            "AND c.conrelid::regclass::text <> ALL(:t)"
        ),
        "indexes": (
            "SELECT tablename, indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename <> ALL(:t)"
        ),
        "policies": (
            "SELECT tablename, policyname, permissive, roles::text, cmd, qual, with_check "
            "FROM pg_policies WHERE schemaname = 'public' AND tablename <> ALL(:t)"
        ),
        "grants": (
            "SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE table_schema = 'public' AND table_name <> ALL(:t)"
        ),
    }
    with inspection_session_factory()() as db:
        return {
            name: sorted(tuple(row) for row in db.execute(text(sql), {"t": list(TABLES)}).all())
            for name, sql in queries.items()
        }


def _tenant_policy(shape: dict) -> tuple[str, str]:
    ((name, policy),) = shape["policies"].items()
    permissive, roles, command, using, check = policy
    assert (name, permissive, roles, command) == (POLICY_NAME, "PERMISSIVE", "{public}", "ALL")
    return using, check


def _assert_head_shapes(shapes: dict[str, dict]) -> None:
    constraints = {table: set(shape["constraints"]) for table, shape in shapes.items()}
    assert constraints == {
        "webhook_endpoints": {
            "webhook_endpoints_pkey",
            "webhook_endpoints_workspace_id_fkey",
            "webhook_endpoints_created_by_user_id_fkey",
        },
        "webhook_signing_keys": {
            "webhook_signing_keys_pkey",
            "webhook_signing_keys_workspace_id_fkey",
            "webhook_signing_keys_endpoint_id_fkey",
            "webhook_signing_keys_created_by_user_id_fkey",
            "uq_webhook_signing_key_id",
            "ck_webhook_signing_key_id_format",
            "ck_webhook_signing_key_nonce",
        },
        "webhook_deliveries": {
            "webhook_deliveries_pkey",
            "webhook_deliveries_workspace_id_fkey",
            "webhook_deliveries_endpoint_id_fkey",
            "uq_webhook_delivery_endpoint_event",
            "ck_webhook_delivery_state",
            "ck_webhook_delivery_attempt_count",
            "ck_webhook_delivery_delivered_at",
            "ck_webhook_delivery_payload_sha256",
        },
        "webhook_delivery_attempts": {
            "webhook_delivery_attempts_pkey",
            "webhook_delivery_attempts_workspace_id_fkey",
            "webhook_delivery_attempts_delivery_id_fkey",
            "uq_webhook_attempt_number",
            "ck_webhook_attempt_number_positive",
            "ck_webhook_attempt_duration",
            "ck_webhook_attempt_outcome",
            "ck_webhook_attempt_status_code",
            "ck_webhook_attempt_shape",
        },
    }
    deliveries = shapes["webhook_deliveries"]
    assert deliveries["constraints"]["uq_webhook_delivery_endpoint_event"] == (
        "UNIQUE (endpoint_id, event_id)"
    )
    assert deliveries["indexes"]["ix_webhook_deliveries_due"] == (
        "CREATE INDEX ix_webhook_deliveries_due ON public.webhook_deliveries "
        "USING btree (state, next_attempt_at)"
    )
    assert shapes["webhook_signing_keys"]["indexes"]["uq_webhook_signing_keys_active"] == (
        "CREATE UNIQUE INDEX uq_webhook_signing_keys_active ON public.webhook_signing_keys "
        "USING btree (endpoint_id) WHERE (expires_at IS NULL)"
    )
    assert shapes["webhook_endpoints"]["columns"]["secret_ciphertext"][2] == "YES"
    # Tenant only, except the read-only scope on the deliveries.
    for table in ("webhook_endpoints", "webhook_signing_keys", "webhook_delivery_attempts"):
        using, check = _tenant_policy(shapes[table])
        assert using == check and "service_scope" not in using, table
    using, check = _tenant_policy(deliveries)
    assert "'webhook_delivery'" in using and "'webhook_delivery'" in check
    assert "IS DISTINCT FROM" in check
    assert all(shape["rls"] == [(True, True)] for shape in shapes.values())
    assert {table: shape["grants"] for table, shape in shapes.items()} == {
        "webhook_endpoints": ["DELETE", "INSERT", "SELECT", "UPDATE"],
        "webhook_signing_keys": ["DELETE", "INSERT", "SELECT", "UPDATE"],
        "webhook_deliveries": ["INSERT", "SELECT", "UPDATE"],  # no delete (A2)
        "webhook_delivery_attempts": ["INSERT", "SELECT"],  # append-only
    }


def _without(mapping: dict, *keys: str) -> dict:
    return {key: value for key, value in mapping.items() if key not in keys}


def _assert_base_shapes(head: dict[str, dict], base: dict[str, dict]) -> None:
    """The base schema: the head schema without this revision (DESIGN 2.6)."""
    assert base["webhook_signing_keys"] == ABSENT
    assert base["webhook_delivery_attempts"] == ABSENT

    endpoints, was = base["webhook_endpoints"], head["webhook_endpoints"]
    columns = _without(was["columns"], "description", "created_by_user_id")
    data_type, length, _, default = columns["secret_ciphertext"]
    columns["secret_ciphertext"] = (data_type, length, "NO", default)
    assert endpoints == {
        **was,
        "columns": columns,
        "constraints": _without(was["constraints"], "webhook_endpoints_created_by_user_id_fkey"),
    }

    deliveries, was = base["webhook_deliveries"], head["webhook_deliveries"]
    new_checks = (
        "ck_webhook_delivery_state",
        "ck_webhook_delivery_attempt_count",
        "ck_webhook_delivery_delivered_at",
        "ck_webhook_delivery_payload_sha256",
    )
    assert deliveries == {
        "columns": _without(
            was["columns"], "payload", "payload_sha256", "last_attempt_at", "delivered_at"
        ),
        "constraints": {
            **_without(was["constraints"], "uq_webhook_delivery_endpoint_event", *new_checks),
            "webhook_deliveries_event_id_key": "UNIQUE (event_id)",
        },
        "indexes": {
            **_without(
                was["indexes"], "uq_webhook_delivery_endpoint_event", "ix_webhook_deliveries_due"
            ),
            "webhook_deliveries_event_id_key": (
                "CREATE UNIQUE INDEX webhook_deliveries_event_id_key "
                "ON public.webhook_deliveries USING btree (event_id)"
            ),
        },
        # The tenant-only policy, word for word the endpoints' policy.
        "policies": endpoints["policies"],
        "rls": [(True, True)],
        "grants": ["DELETE", "INSERT", "SELECT", "UPDATE"],
    }


@pytest.mark.postgres
def test_the_orm_schema_has_the_ledger_and_refuses_the_deny_cases(client, email_outbox):
    _requires_postgres()
    first = _tenant(client, email_outbox, "rules-a")
    second = _tenant(client, email_outbox, "rules-b")
    _assert_head_shapes(_shapes())
    _assert_rules(first, second, "orm")


# --- PostgreSQL: the application role ----------------------------------------------------


def _world(client, email_outbox, label: str) -> tuple[dict, dict]:
    """Two workspaces, each with one row in each of the four webhook tables."""
    worlds = []
    for name in ("a", "b"):
        tenant = _tenant(client, email_outbox, f"{label}-{name}")
        endpoint = _add(WebhookEndpoint, _endpoint(tenant))
        delivery = _add(
            WebhookDelivery,
            _delivery(tenant, endpoint, state="pending", next_attempt_at=NOW),
        )
        worlds.append(
            {
                "tenant": tenant,
                "webhook_endpoints": endpoint,
                "webhook_signing_keys": _add(WebhookSigningKey, _key(tenant, endpoint)),
                "webhook_deliveries": delivery,
                "webhook_delivery_attempts": _add(
                    WebhookDeliveryAttempt, _attempt(tenant, delivery, 1)
                ),
            }
        )
    return worlds[0], worlds[1]


def _visible(db, table: str) -> set[str]:
    return set(db.scalars(text(f"SELECT id FROM {table}")).all())  # noqa: S608


def _bind(db, *, workspace: dict | None = None, scope: str | None = None) -> None:
    assert db.scalar(text("SELECT current_user")) == APP_ROLE
    if scope is not None:
        bind_service_scope(db, scope)
    if workspace is not None:
        bind_tenant_context(db, workspace_id=workspace["tenant"]["workspace_id"])


def _as_app(statement, params: dict | None = None, **binding) -> int:
    """Run one write as the application role and commit; returns the row count.

    A Core insert reports -1 (SQLAlchemy's batched insert), so callers check inserts by id.
    """
    with get_session_factory()() as db:
        _bind(db, **binding)
        count = db.execute(statement, params or {}).rowcount
        db.commit()
    return count


def _app_refused(statement, params: dict | None = None, *, match: str, **binding) -> None:
    with get_session_factory()() as db:
        _bind(db, **binding)
        with pytest.raises(DBAPIError, match=match):
            db.execute(statement, params or {})
        db.rollback()


SET_SENDING = text(
    "UPDATE webhook_deliveries SET state = 'sending', attempt_count = attempt_count + 1 "
    "WHERE id = :id"
)
DELETE_DELIVERY = text("DELETE FROM webhook_deliveries WHERE id = :id")
MOVE_DELIVERY = text("UPDATE webhook_deliveries SET workspace_id = :w WHERE id = :id")
RLS_REFUSED = "row-level security"
NO_GRANT = "permission denied"


@pytest.mark.postgres
def test_a_session_sees_only_its_own_workspace(client, email_outbox):
    _requires_postgres()
    first, second = _world(client, email_outbox, "tenant")
    factory = get_session_factory()

    for own in (first, second):
        with factory() as db:
            _bind(db, workspace=own)
            for table in TABLES:
                assert _visible(db, table) == {own[table]}, table

    # No workspace bound: nothing.
    with factory() as db:
        _bind(db)
        for table in TABLES:
            assert _visible(db, table) == set(), table

    # Bound to A, a row for B is refused by the WITH CHECK; the same row for A works.
    rows = {
        WebhookEndpoint: lambda w: _endpoint(w["tenant"]),
        WebhookSigningKey: lambda w: _key(w["tenant"], w["webhook_endpoints"]),
        WebhookDelivery: lambda w: _delivery(w["tenant"], w["webhook_endpoints"]),
        WebhookDeliveryAttempt: lambda w: _attempt(w["tenant"], w["webhook_deliveries"], 2),
    }
    for model, row in rows.items():
        insert = model.__table__.insert()
        _app_refused(insert, row(second), match=RLS_REFUSED, workspace=first)
        allowed = row(first)
        _as_app(insert, allowed, workspace=first)
        assert allowed["id"] in _ids(model.__tablename__), model.__tablename__


@pytest.mark.postgres
def test_the_webhook_delivery_scope_reads_the_deliveries_only(client, email_outbox):
    _requires_postgres()
    first, second = _world(client, email_outbox, "scope")
    factory = get_session_factory()
    both = {first["webhook_deliveries"], second["webhook_deliveries"]}

    # The sweeper's scope, no workspace: every tenant's deliveries, nothing else.
    with factory() as db:
        _bind(db, scope=SERVICE_SCOPE_WEBHOOK_DELIVERY)
        assert _visible(db, "webhook_deliveries") == both
        for table in ("webhook_endpoints", "webhook_signing_keys", "webhook_delivery_attempts"):
            assert _visible(db, table) == set(), table

    # Another scope reaches no webhook row.
    with factory() as db:
        _bind(db, scope=SERVICE_SCOPE_DISPATCH)
        for table in TABLES:
            assert _visible(db, table) == set(), table

    # The scope alone cannot write: the WITH CHECK needs a bound workspace.
    target = {"id": first["webhook_deliveries"]}
    scope = SERVICE_SCOPE_WEBHOOK_DELIVERY
    insert = WebhookDelivery.__table__.insert()
    _app_refused(SET_SENDING, target, match=RLS_REFUSED, scope=scope)
    _app_refused(
        insert,
        _delivery(first["tenant"], first["webhook_endpoints"]),
        match=RLS_REFUSED,
        scope=scope,
    )
    # It cannot delete either: the application role has no DELETE (A2).
    _app_refused(DELETE_DELIVERY, target, match=NO_GRANT, scope=scope)

    # The scope and a workspace together still read every tenant's rows, but write
    # nothing (Addendum 2): not B's row moved into A, not A's own row, not a new row of A.
    with factory() as db:
        _bind(db, scope=scope, workspace=first)
        assert _visible(db, "webhook_deliveries") == both
    into_first = {"id": second["webhook_deliveries"], "w": first["tenant"]["workspace_id"]}
    _app_refused(MOVE_DELIVERY, into_first, match=RLS_REFUSED, scope=scope, workspace=first)
    _app_refused(SET_SENDING, target, match=RLS_REFUSED, scope=scope, workspace=first)
    _app_refused(SET_SENDING, target, match=RLS_REFUSED, scope=scope, workspace=second)
    _app_refused(
        insert,
        _delivery(first["tenant"], first["webhook_endpoints"]),
        match=RLS_REFUSED,
        scope=scope,
        workspace=first,
    )

    # Bound to the row's own workspace only, the same writes work, and B's row stays
    # out of reach.
    assert _as_app(SET_SENDING, target, workspace=first) == 1
    row = _delivery(first["tenant"], first["webhook_endpoints"])
    _as_app(insert, row, workspace=first)
    assert _as_app(MOVE_DELIVERY, into_first, workspace=first) == 0
    assert (
        _scalar("SELECT attempt_count FROM webhook_deliveries WHERE id = :id", id=target["id"]) == 1
    )
    assert row["id"] in _ids("webhook_deliveries")
    assert (
        _scalar(
            "SELECT workspace_id FROM webhook_deliveries WHERE id = :id",
            id=second["webhook_deliveries"],
        )
        == second["tenant"]["workspace_id"]
    )


@pytest.mark.postgres
def test_the_attempts_are_append_only_and_no_delivery_is_deleted(client, email_outbox):
    _requires_postgres()
    first, second = _world(client, email_outbox, "append")
    privileges = {
        table: {
            privilege: _scalar(
                "SELECT has_table_privilege(:r, :t, :p)", r=APP_ROLE, t=table, p=privilege
            )
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
        }
        for table in ("webhook_delivery_attempts", "webhook_deliveries")
    }
    assert privileges == {
        "webhook_delivery_attempts": {
            "SELECT": True,
            "INSERT": True,
            "UPDATE": False,
            "DELETE": False,
        },
        "webhook_deliveries": {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": False},
    }

    # The workspace is bound, so the rows are visible: the refusal is the missing grant.
    attempt = {"id": first["webhook_delivery_attempts"]}
    for statement in (
        text("UPDATE webhook_delivery_attempts SET duration_ms = 1 WHERE id = :id"),
        text("DELETE FROM webhook_delivery_attempts WHERE id = :id"),
    ):
        _app_refused(statement, attempt, match=NO_GRANT, workspace=first)
    delivery = {"id": first["webhook_deliveries"]}
    _app_refused(DELETE_DELIVERY, delivery, match=NO_GRANT, workspace=first)

    # Allow-controls: read and append an attempt, update the delivery.
    with get_session_factory()() as db:
        _bind(db, workspace=first)
        assert _visible(db, "webhook_delivery_attempts") == {attempt["id"]}
        db.execute(
            WebhookDeliveryAttempt.__table__.insert(),
            _attempt(
                first["tenant"],
                delivery["id"],
                2,
                outcome="failed",
                error_code="timeout",
                status_code=None,
            ),
        )
        db.commit()
    assert _as_app(SET_SENDING, delivery, workspace=first) == 1
    with inspection_session_factory()() as db:
        numbers = db.scalars(
            text("SELECT attempt_number FROM webhook_delivery_attempts WHERE delivery_id = :d"),
            {"d": delivery["id"]},
        ).all()
    assert sorted(numbers) == [1, 2]

    # The owner deletes workspace A: its rows in all four tables go by cascade, B's stay.
    with inspection_session_factory()() as db:
        db.execute(
            text("DELETE FROM workspaces WHERE id = :w"), {"w": first["tenant"]["workspace_id"]}
        )
        db.commit()
    for table in TABLES:
        assert _ids(table) == {second[table]}, table


# --- PostgreSQL: the migration -----------------------------------------------------------


def _endpoint_secrets() -> dict[str, bytes | None]:
    with inspection_session_factory()() as db:
        rows = db.execute(text("SELECT id, secret_ciphertext FROM webhook_endpoints")).all()
    return {row[0]: bytes(row[1]) if row[1] is not None else None for row in rows}


def _fail_delivered() -> None:
    """On the base schema, mark 'delivered' rows 'failed', as an operator would before
    upgrading again (the downgrade dropped their delivered_at)."""
    with inspection_session_factory()() as db:
        db.execute(text("UPDATE webhook_deliveries SET state = 'failed' WHERE state = 'delivered'"))
        db.commit()


@pytest.mark.postgres
def test_the_migration_round_trips_and_refuses_a_lossy_downgrade(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    from alembic import command

    from backend.config import reset_settings_cache
    from backend.observability.release import code_migration_head

    assert code_migration_head() == NEW_REVISION
    first = _tenant(client, email_outbox, "migration-a")
    second = _tenant(client, email_outbox, "migration-b")
    head = _shapes()
    _assert_head_shapes(head)

    # Rows written at the head: an endpoint of new code (no legacy secret) and a legacy
    # one, a delivered delivery with its payload, a key and an attempt.
    fresh = _add(WebhookEndpoint, _endpoint(first, description="new code"))
    legacy = _add(WebhookEndpoint, _endpoint(first, secret_ciphertext=b"legacy"))
    delivery = _delivery(
        first, legacy, payload=b"{}", payload_sha256=DIGEST, state="delivered", delivered_at=NOW
    )
    _add(WebhookDelivery, delivery)
    _add(WebhookSigningKey, _key(first, fresh, expires_at=None))
    _add(WebhookDeliveryAttempt, _attempt(first, str(delivery["id"]), 1))

    config = _alembic(monkeypatch, settings)
    try:
        command.stamp(config, "head")
        others = _other_tables()  # after the stamp, which adds alembic_version
        command.downgrade(config, BASE_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        for table in NEW_TABLES:
            assert _scalar("SELECT to_regclass(:t) IS NULL", t=f"public.{table}"), table
        _assert_base_shapes(head, _shapes())
        assert _other_tables() == others  # every other table as it was

        # Every endpoint and delivery stays; the missing secret is now empty bytes.
        assert _endpoint_secrets() == {fresh: b"", legacy: b"legacy"}
        assert _ids("webhook_deliveries") == {delivery["id"]}

        # The base schema: a secret is required again, and an event id is globally unique.
        with pytest.raises(IntegrityError, match='null value in column "secret_ciphertext"'):
            _add(WebhookEndpoint, _endpoint(first))
        _refused(
            WebhookDelivery,
            _delivery(first, fresh, event_id=delivery["event_id"]),
            "webhook_deliveries_event_id_key",
        )
        # The DELETE grant is back: the application role deletes in its own workspace.
        extra = _add(WebhookDelivery, _delivery(first, fresh))
        assert _as_app(DELETE_DELIVERY, {"id": extra}, workspace={"tenant": first}) == 1

        # The downgrade dropped delivered_at, so the 'delivered' row now breaks
        # ck_webhook_delivery_delivered_at: the upgrade refuses it and changes nothing.
        base = _shapes()
        with pytest.raises(IntegrityError, match='"ck_webhook_delivery_delivered_at"'):
            command.upgrade(config, "head")
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        assert _shapes() == base
        assert _ids("webhook_deliveries") == {delivery["id"]}
        _fail_delivered()

        command.upgrade(config, "head")
        assert _scalar("SELECT version_num FROM alembic_version") == NEW_REVISION
        assert _shapes() == head  # the migrated schema equals the ORM schema
        assert _other_tables() == others
        # No data changes on the way up.
        assert _endpoint_secrets() == {fresh: b"", legacy: b"legacy"}
        with inspection_session_factory()() as db:
            stored = db.execute(
                text(
                    "SELECT id, state, payload, payload_sha256, last_attempt_at, delivered_at "
                    "FROM webhook_deliveries"
                )
            ).all()
        assert [tuple(row) for row in stored] == [
            (delivery["id"], "failed", None, None, None, None)
        ]

        # The migrated schema refuses the same deny cases.
        _assert_rules(first, second, "migrated")

        # One event id on two rows: the downgrade refuses and deletes nothing.
        event_id = new_id()
        _add(WebhookDelivery, _delivery(first, fresh, event_id=event_id))
        twin = _add(WebhookDelivery, _delivery(first, legacy, event_id=event_id))
        before = {table: _ids(table) for table in TABLES}
        with pytest.raises(RuntimeError, match="event id"):
            command.downgrade(config, BASE_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == NEW_REVISION
        assert _shapes() == head
        assert {table: _ids(table) for table in TABLES} == before

        # Remove the twin and every other duplicate event id: down and up again with rows.
        with inspection_session_factory()() as db:
            db.execute(
                text(
                    "DELETE FROM webhook_deliveries WHERE id = :twin OR id IN (SELECT id FROM "
                    "(SELECT id, row_number() OVER (PARTITION BY event_id ORDER BY id) AS n "
                    "FROM webhook_deliveries) ranked WHERE n > 1)"
                ),
                {"twin": twin},
            )
            db.commit()
        kept = _ids("webhook_deliveries")
        command.downgrade(config, BASE_REVISION)
        assert _other_tables() == others
        assert _ids("webhook_deliveries") == kept
        _fail_delivered()
        command.upgrade(config, "head")
        assert _shapes() == head
        assert _other_tables() == others
        assert _ids("webhook_deliveries") == kept
    finally:
        reset_settings_cache()
