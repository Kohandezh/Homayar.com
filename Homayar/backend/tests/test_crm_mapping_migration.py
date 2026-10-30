"""HOM-88 PR 1: the CRM mapping tables and their one migration.

Five workspace tables hold the generic CRM mapping contract (F64): mapping profiles,
stable external ids, the ledger of received CRM records, the links of a conversation to
those records, and the reconciliation queue.  PR 1 adds only the tables.  The tests check:

* the ORM declares every column, constraint and index, and ``database/rls.py`` lists the
  five tables (the ledger with the read-only ``crm_reconciliation`` discovery clause);
* the CHECK constants are built from the enums and use no regex, so SQLite runs the same
  rules;
* on PostgreSQL, the ORM schema (``create_all``) and the migrated schema are the same;
* the database refuses every deny case on the intended constraint, and the closest allow
  case passes;
* tenant isolation on all five tables, the service-scope rules, and no DELETE on the
  ledger for the application role;
* upgrade from ``47676f445509`` creates the five tables; downgrade removes them and
  leaves every other table as it was.
"""

from __future__ import annotations

import importlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

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
    apply_row_level_security,
)
from backend.database.saas_models import (
    EXTERNAL_LINK_TYPE_CHECK,
    EXTERNAL_RECORD_TYPE_CHECK,
    INBOUND_RECORD_STATE_CHECK,
    INTEGRATION_CONVERSATION_SOURCE_CHECK,
    INTEGRATION_SOURCE_SYSTEM_CHECK,
    LINK_OUTCOME_CHECK,
    RECONCILIATION_LINK_TYPE_CHECK,
    RECONCILIATION_REASON_CHECK,
    RECONCILIATION_RESOLUTION_CHECK,
    RECONCILIATION_STATUS_CHECK,
    Base,
    CanonicalOutcome,
    ConversationExternalLink,
    ExternalLinkType,
    ExternalRecord,
    ExternalRecordType,
    InboundRecordState,
    IntegrationInboundRecord,
    IntegrationMappingProfile,
    IntegrationReconciliationItem,
    ReconciliationReason,
    ReconciliationResolution,
    ReconciliationStatus,
    new_id,
    utcnow,
)
from backend.database.tenant_context import (
    SERVICE_SCOPE_CRM_RECONCILIATION,
    SERVICE_SCOPE_USAGE_RECONCILIATION,
    bind_service_scope,
    bind_tenant_context,
)
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user, register_user

NEW_REVISION = "565d2ccd4fca"
BASE_REVISION = "47676f445509"  # HOM-54: scorecard_versions
HEAD_REVISION = "484a6df74abf"  # HOM-73: review items, after HOM-56
PROFILES = "integration_mapping_profiles"
RECORDS = "external_records"
LEDGER = "integration_inbound_records"
LINKS = "conversation_external_links"
ITEMS = "integration_reconciliation_items"
TABLES = (PROFILES, RECORDS, LEDGER, LINKS, ITEMS)
NON_LEDGER = (PROFILES, RECORDS, LINKS, ITEMS)
MODELS = {
    PROFILES: IntegrationMappingProfile,
    RECORDS: ExternalRecord,
    LEDGER: IntegrationInboundRecord,
    LINKS: ConversationExternalLink,
    ITEMS: IntegrationReconciliationItem,
}
JSON_COLUMNS = frozenset(
    {"field_map", "outcome_map", "agent_map", "extracted", "candidate_meeting_ids"}
)

# --- the section 2 contract --------------------------------------------------------------

V36 = ("character varying", 36)
TZ = ("timestamp with time zone", None)
TENANCY = {"id": (*V36, "NO"), "organization_id": (*V36, "NO"), "workspace_id": (*V36, "NO")}

# table -> [(column, data type, length, nullable)] in table order.
COLUMNS: dict[str, list[tuple[str, str, int | None, str]]] = {
    PROFILES: [
        ("id", *V36, "NO"),
        ("organization_id", *V36, "NO"),
        ("workspace_id", *V36, "NO"),
        ("source_system", "character varying", 60, "NO"),
        ("conversation_source_system", "character varying", 60, "NO"),
        ("field_map", "json", None, "NO"),
        ("outcome_map", "json", None, "NO"),
        ("agent_map", "json", None, "NO"),
        ("late_record_window_seconds", "integer", None, "NO"),
        ("is_active", "boolean", None, "NO"),
        ("version", "integer", None, "NO"),
        ("created_at", *TZ, "NO"),
        ("updated_at", *TZ, "NO"),
        ("updated_by_user_id", *V36, "YES"),
    ],
    RECORDS: [
        ("id", *V36, "NO"),
        ("organization_id", *V36, "NO"),
        ("workspace_id", *V36, "NO"),
        ("source_system", "character varying", 60, "NO"),
        ("record_type", "character varying", 16, "NO"),
        ("external_id", "character varying", 200, "NO"),
        ("is_active", "boolean", None, "NO"),
        ("first_seen_at", *TZ, "NO"),
        ("last_seen_at", *TZ, "NO"),
    ],
    LEDGER: [
        ("id", *V36, "NO"),
        ("organization_id", *V36, "NO"),
        ("workspace_id", *V36, "NO"),
        ("profile_id", *V36, "NO"),
        ("source_system", "character varying", 60, "NO"),
        ("event_id", "character varying", 200, "NO"),
        ("payload_hash", "character varying", 64, "NO"),
        ("extracted", "json", None, "NO"),
        ("state", "character varying", 16, "NO"),
        ("meeting_id", *V36, "YES"),
        ("received_at", *TZ, "NO"),
        ("expires_at", *TZ, "YES"),
        ("attempt_count", "integer", None, "NO"),
        ("last_attempt_at", *TZ, "YES"),
        ("conflict_count", "integer", None, "NO"),
        ("last_conflict_at", *TZ, "YES"),
        ("api_key_id", *V36, "YES"),
        ("created_by_user_id", *V36, "YES"),
    ],
    LINKS: [
        ("id", *V36, "NO"),
        ("organization_id", *V36, "NO"),
        ("workspace_id", *V36, "NO"),
        ("meeting_id", *V36, "NO"),
        ("source_system", "character varying", 60, "NO"),
        ("link_type", "character varying", 16, "NO"),
        ("external_record_id", *V36, "YES"),
        ("outcome", "character varying", 16, "YES"),
        ("external_outcome_code", "character varying", 200, "YES"),
        ("agent_user_id", *V36, "YES"),
        ("profile_version", "integer", None, "NO"),
        ("inbound_record_id", *V36, "YES"),
        ("linked_by_user_id", *V36, "YES"),
        ("linked_at", *TZ, "NO"),
    ],
    ITEMS: [
        ("id", *V36, "NO"),
        ("organization_id", *V36, "NO"),
        ("workspace_id", *V36, "NO"),
        ("source_system", "character varying", 60, "NO"),
        ("inbound_record_id", *V36, "NO"),
        ("meeting_id", *V36, "YES"),
        ("link_type", "character varying", 16, "YES"),
        ("reason", "character varying", 32, "NO"),
        ("status", "character varying", 16, "NO"),
        ("current_value", "character varying", 200, "YES"),
        ("candidate_value", "character varying", 200, "YES"),
        ("candidate_meeting_ids", "json", None, "NO"),
        ("resolution", "character varying", 16, "YES"),
        ("resolved_by_user_id", *V36, "YES"),
        ("resolved_at", *TZ, "YES"),
        ("created_at", *TZ, "NO"),
    ],
}

TENANT_KEYS = {
    "organization_id": ("organizations.id", "CASCADE"),
    "workspace_id": ("workspaces.id", "CASCADE"),
}
FOREIGN_KEYS: dict[str, dict[str, tuple[str, str]]] = {
    PROFILES: {**TENANT_KEYS, "updated_by_user_id": ("users.id", "SET NULL")},
    RECORDS: dict(TENANT_KEYS),
    LEDGER: {
        **TENANT_KEYS,
        "profile_id": (f"{PROFILES}.id", "CASCADE"),
        "meeting_id": ("meetings.id", "SET NULL"),
        "api_key_id": ("api_keys.id", "SET NULL"),
        "created_by_user_id": ("users.id", "SET NULL"),
    },
    LINKS: {
        **TENANT_KEYS,
        "meeting_id": ("meetings.id", "CASCADE"),
        "external_record_id": (f"{RECORDS}.id", "RESTRICT"),
        "agent_user_id": ("users.id", "SET NULL"),
        "inbound_record_id": (f"{LEDGER}.id", "SET NULL"),
        "linked_by_user_id": ("users.id", "SET NULL"),
    },
    ITEMS: {
        **TENANT_KEYS,
        "inbound_record_id": (f"{LEDGER}.id", "CASCADE"),
        "meeting_id": ("meetings.id", "SET NULL"),
        "resolved_by_user_id": ("users.id", "SET NULL"),
    },
}
UNIQUES: dict[str, dict[str, list[str]]] = {
    PROFILES: {"uq_integration_profile_source": ["workspace_id", "source_system"]},
    RECORDS: {
        "uq_external_record_identity": [
            "workspace_id",
            "source_system",
            "record_type",
            "external_id",
        ]
    },
    LEDGER: {"uq_inbound_record_event": ["workspace_id", "source_system", "event_id"]},
    LINKS: {"uq_conversation_external_link": ["meeting_id", "source_system", "link_type"]},
    ITEMS: {},
}
CHECKS: dict[str, set[str]] = {
    PROFILES: {
        "ck_integration_profile_source_system",
        "ck_integration_profile_conversation_source",
        "ck_integration_profile_late_window",
        "ck_integration_profile_version",
    },
    RECORDS: {"ck_external_record_type", "ck_external_record_id"},
    LEDGER: {
        "ck_inbound_record_event_id",
        "ck_inbound_record_payload_hash",
        "ck_inbound_record_state",
        "ck_inbound_record_pending_expiry",
        "ck_inbound_record_attempts",
        "ck_inbound_record_conflicts",
    },
    LINKS: {
        "ck_conversation_link_type",
        "ck_conversation_link_outcome",
        "ck_conversation_link_shape",
        "ck_conversation_link_agent",
    },
    ITEMS: {
        "ck_reconciliation_link_type",
        "ck_reconciliation_reason",
        "ck_reconciliation_status",
        "ck_reconciliation_resolution",
        "ck_reconciliation_closed",
    },
}
# Non-unique indexes: name -> columns.
INDEXES: dict[str, dict[str, list[str]]] = {
    table: {
        f"ix_{table}_organization_id": ["organization_id"],
        f"ix_{table}_workspace_id": ["workspace_id"],
    }
    for table in TABLES
}
INDEXES[LEDGER] |= {
    f"ix_{LEDGER}_profile_id": ["profile_id"],
    f"ix_{LEDGER}_meeting_id": ["meeting_id"],
    "ix_inbound_records_pending": ["state", "expires_at"],
}
INDEXES[LINKS] |= {
    f"ix_{LINKS}_external_record_id": ["external_record_id"],
    f"ix_{LINKS}_inbound_record_id": ["inbound_record_id"],
}
INDEXES[ITEMS] |= {
    f"ix_{ITEMS}_inbound_record_id": ["inbound_record_id"],
    f"ix_{ITEMS}_meeting_id": ["meeting_id"],
    "ix_reconciliation_workspace_status_created": ["workspace_id", "status", "created_at"],
}


# --- helpers -----------------------------------------------------------------------------


def _requires_postgres() -> None:
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")


def _scalar(sql: str, **params):
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


def _tenant(client, email_outbox, label: str) -> dict:
    credentials = register_user(client, email_outbox, email=f"crm-map-{label}@example.com")
    session = login_user(client, credentials)
    workspace = session["workspaces"][0]
    return {
        "user_id": session["user"]["id"],
        "workspace_id": workspace["id"],
        "organization_id": workspace["organization_id"],
    }


def _alembic(monkeypatch, settings: Settings):
    from alembic.config import Config

    from backend.config import reset_settings_cache
    from backend.observability.release import PROJECT_ROOT

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _insert_statement(table: str, row: dict[str, object]):
    names = ", ".join(row)
    values = ", ".join(
        f"CAST(:{name} AS JSON)" if name in JSON_COLUMNS else f":{name}" for name in row
    )
    params = {
        name: json.dumps(value) if name in JSON_COLUMNS else value for name, value in row.items()
    }
    return text(f"INSERT INTO {table} ({names}) VALUES ({values})"), params  # noqa: S608


def _insert(db, table: str, row: dict[str, object]) -> str:
    statement, params = _insert_statement(table, row)
    db.execute(statement, params)
    return str(row["id"])


def _insert_as_dba(table: str, row: dict[str, object]) -> str:
    with inspection_session_factory()() as db:
        _insert(db, table, row)
        db.commit()
    return str(row["id"])


def _meeting(tenant: dict) -> str:
    """One conversation by raw SQL, as the DBA."""
    meeting_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    with inspection_session_factory()() as db:
        db.execute(
            text(
                "INSERT INTO meetings (id, workspace_id, created_by_user_id, title, language, "
                "status, duration_seconds, source, created_at, updated_at) VALUES (:id, :w, :u, "
                ":id, 'fa', 'COMPLETED', 5, 'upload', :now, :now)"
            ),
            {"id": meeting_id, "w": tenant["workspace_id"], "u": tenant["user_id"], "now": now},
        )
        db.commit()
    return meeting_id


def _tenancy(tenant: dict) -> dict[str, object]:
    return {
        "id": str(uuid.uuid4()),
        "organization_id": tenant["organization_id"],
        "workspace_id": tenant["workspace_id"],
    }


def _profile(tenant: dict, **extra: object) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        **_tenancy(tenant),
        "source_system": "odoo",
        "conversation_source_system": "issabel",
        "field_map": {"conversation_id": "call.id", "customer_id": "partner.ref"},
        "outcome_map": {"WON": "won"},
        "agent_map": {},
        "late_record_window_seconds": 604800,
        "is_active": True,
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "updated_by_user_id": tenant["user_id"],
        **extra,
    }


def _record(tenant: dict, **extra: object) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        **_tenancy(tenant),
        "source_system": "odoo",
        "record_type": "customer",
        "external_id": "C-1",
        "is_active": True,
        "first_seen_at": now,
        "last_seen_at": now,
        **extra,
    }


EXTRACTED = {
    "conversation_id": "call-1",
    "customer_id": "C-1",
    "ticket_id": None,
    "agent_id": None,
    "outcome": "WON",
    "customer_active": None,
    "ticket_active": None,
}


def _inbound(tenant: dict, profile_id: str, **extra: object) -> dict[str, object]:
    return {
        **_tenancy(tenant),
        "profile_id": profile_id,
        "source_system": "odoo",
        "event_id": "E-1",
        "payload_hash": "a" * 64,
        "extracted": EXTRACTED,
        "state": "applied",
        "meeting_id": None,
        "received_at": datetime.now(UTC),
        "expires_at": None,
        "attempt_count": 0,
        "last_attempt_at": None,
        "conflict_count": 0,
        "last_conflict_at": None,
        "api_key_id": None,
        "created_by_user_id": tenant["user_id"],
        **extra,
    }


def _link(tenant: dict, meeting_id: str, record_id: str | None, **extra: object) -> dict:
    return {
        **_tenancy(tenant),
        "meeting_id": meeting_id,
        "source_system": "odoo",
        "link_type": "customer",
        "external_record_id": record_id,
        "outcome": None,
        "external_outcome_code": None,
        "agent_user_id": None,
        "profile_version": 1,
        "inbound_record_id": None,
        "linked_by_user_id": None,
        "linked_at": datetime.now(UTC),
        **extra,
    }


def _outcome_link(tenant: dict, meeting_id: str, **extra: object) -> dict:
    return _link(
        tenant,
        meeting_id,
        None,
        **{"link_type": "outcome", "outcome": "won", "external_outcome_code": "WON", **extra},
    )


def _item(tenant: dict, inbound_id: str, **extra: object) -> dict[str, object]:
    return {
        **_tenancy(tenant),
        "source_system": "odoo",
        "inbound_record_id": inbound_id,
        "meeting_id": None,
        "link_type": None,
        "reason": "conversation_not_found",
        "status": "open",
        "current_value": None,
        "candidate_value": None,
        "candidate_meeting_ids": [],
        "resolution": None,
        "resolved_by_user_id": None,
        "resolved_at": None,
        "created_at": datetime.now(UTC),
        **extra,
    }


def _seed(tenant: dict, label: str) -> dict[str, str]:
    """One row in each of the five tables of ``tenant``, as the DBA."""
    meeting_id = _meeting(tenant)
    profile_id = _insert_as_dba(PROFILES, _profile(tenant, source_system=f"seed-{label}"))
    record_id = _insert_as_dba(RECORDS, _record(tenant, external_id=f"C-{label}"))
    inbound_id = _insert_as_dba(
        LEDGER, _inbound(tenant, profile_id, event_id=f"E-{label}", meeting_id=meeting_id)
    )
    link_id = _insert_as_dba(LINKS, _link(tenant, meeting_id, record_id))
    item_id = _insert_as_dba(ITEMS, _item(tenant, inbound_id, reason="changed_id"))
    return {
        "meeting": meeting_id,
        PROFILES: profile_id,
        RECORDS: record_id,
        LEDGER: inbound_id,
        LINKS: link_id,
        ITEMS: item_id,
    }


def _probe(tenant: dict, seeded: dict[str, str], table: str) -> dict[str, object]:
    """A new valid row of ``table`` for ``tenant``: only row-level security can refuse it."""
    probe = f"probe-{uuid.uuid4().hex[:8]}"
    if table == PROFILES:
        return _profile(tenant, source_system=probe)
    if table == RECORDS:
        return _record(tenant, external_id=probe)
    if table == LEDGER:
        return _inbound(tenant, seeded[PROFILES], event_id=probe)
    if table == LINKS:
        return _link(tenant, seeded["meeting"], seeded[RECORDS], source_system=probe)
    return _item(tenant, seeded[LEDGER], reason="changed_id", candidate_value=probe)


def _ids(table: str, workspace_id: str | None = None) -> set[str]:
    with inspection_session_factory()() as db:
        if workspace_id is None:
            rows = db.execute(text(f"SELECT id FROM {table}"))  # noqa: S608
        else:
            rows = db.execute(
                text(f"SELECT id FROM {table} WHERE workspace_id = :w"),  # noqa: S608
                {"w": workspace_id},
            )
        return {row[0] for row in rows}


def _visible(db, table: str) -> set[str]:
    return {row[0] for row in db.execute(text(f"SELECT id FROM {table}"))}  # noqa: S608


# --- the catalog view --------------------------------------------------------------------


def _shape(table: str) -> dict:
    """The table as the catalog shows it: columns, constraints, indexes, policy, grants."""
    with inspection_session_factory()() as db:
        columns = db.execute(
            text(
                "SELECT column_name, data_type, character_maximum_length, is_nullable, "
                "column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t ORDER BY ordinal_position"
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
        "columns": [tuple(row) for row in columns],
        "constraints": dict(constraints),
        "indexes": dict(indexes),
        "policies": {name: tuple(rest) for name, *rest in policies},
        "rls": [tuple(row) for row in flags],
        "grants": sorted(row[0] for row in grants),
    }


EMPTY_SHAPE = {
    "columns": [],
    "constraints": {},
    "indexes": {},
    "policies": {},
    "rls": [],
    "grants": [],
}


def _other_tables() -> dict:
    """Every table of the schema except the five, so a test can prove it did not change."""
    queries = {
        "tables": (
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT (c.relname::text = ANY(:t))"
        ),
        "columns": (
            "SELECT table_name, column_name, ordinal_position, data_type, "
            "character_maximum_length, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND NOT (table_name::text = ANY(:t))"
        ),
        "constraints": (
            "SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid) "
            "FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
            "WHERE n.nspname = 'public' AND c.conrelid <> 0 "
            "AND NOT (c.conrelid::regclass::text = ANY(:t))"
        ),
        "indexes": (
            "SELECT tablename, indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND NOT (tablename::text = ANY(:t))"
        ),
        "policies": (
            "SELECT tablename, policyname, permissive, roles::text, cmd, qual, with_check "
            "FROM pg_policies WHERE schemaname = 'public' AND NOT (tablename::text = ANY(:t))"
        ),
        "grants": (
            "SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE table_schema = 'public' AND NOT (table_name::text = ANY(:t))"
        ),
    }
    with inspection_session_factory()() as db:
        return {
            name: sorted(tuple(row) for row in db.execute(text(sql), {"t": list(TABLES)}).all())
            for name, sql in queries.items()
        }


def _expected_constraints(table: str) -> dict[str, str]:
    expected = {f"{table}_pkey": "PRIMARY KEY (id)"}
    for column, (target, ondelete) in FOREIGN_KEYS[table].items():
        target_table, target_column = target.split(".")
        expected[f"{table}_{column}_fkey"] = (
            f"FOREIGN KEY ({column}) REFERENCES {target_table}({target_column}) "
            f"ON DELETE {ondelete}"
        )
    for name, columns in UNIQUES[table].items():
        expected[name] = f"UNIQUE ({', '.join(columns)})"
    return expected


def _expected_indexes(table: str) -> dict[str, str]:
    expected = {
        f"{table}_pkey": f"CREATE UNIQUE INDEX {table}_pkey ON public.{table} USING btree (id)"
    }
    for name, columns in UNIQUES[table].items():
        expected[name] = (
            f"CREATE UNIQUE INDEX {name} ON public.{table} USING btree ({', '.join(columns)})"
        )
    for name, columns in INDEXES[table].items():
        expected[name] = f"CREATE INDEX {name} ON public.{table} USING btree ({', '.join(columns)})"
    return expected


def _assert_head_shape() -> dict[str, dict]:
    shapes = {}
    for table in TABLES:
        shape = _shape(table)
        assert shape["columns"] == [
            (name, data_type, length, nullable, None)
            for name, data_type, length, nullable in COLUMNS[table]
        ], table
        constraints = dict(shape["constraints"])
        checks = {name for name in constraints if name.startswith("ck_")}
        assert checks == CHECKS[table], table  # their behaviour is pinned by _assert_rules
        for name in checks:
            constraints.pop(name)
        assert constraints == _expected_constraints(table), table
        assert shape["indexes"] == _expected_indexes(table), table
        # The tenant policy only: no share-token clause; the ledger adds the read-only scope.
        ((name, policy),) = shape["policies"].items()
        permissive, roles, command, using, check = policy
        assert (name, permissive, roles, command) == (POLICY_NAME, "PERMISSIVE", "{public}", "ALL")
        assert "homayar.workspace_id" in using and "homayar.workspace_id" in check
        assert "share" not in using and "share" not in check
        assert "service" not in check, table  # the scope never writes
        if table == LEDGER:
            assert "homayar.service_scope" in using and "'crm_reconciliation'" in using
            assert "usage_reconciliation" not in using
        else:
            assert using == check and "service" not in using, table
        assert shape["rls"] == [(True, True)], table
        if table == LEDGER:
            assert shape["grants"] == ["INSERT", "SELECT", "UPDATE"]  # no DELETE on the ledger
        else:
            assert shape["grants"] == ["DELETE", "INSERT", "SELECT", "UPDATE"], table
        shapes[table] = shape
    return shapes


# --- deny cases and allow controls (SPEC section 4) --------------------------------------


def _deny(table: str, row: dict[str, object], constraint: str) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        _insert_as_dba(table, row)


def _assert_rules(first: dict, second: dict) -> None:
    meeting = _meeting(first)
    other_meeting = _meeting(first)

    # Profiles: the slug rule on both source columns.
    for slug in ("Odoo", "1odoo", "", "-odoo", "odoo crm", "odoo/crm", "odöo"):
        _deny(PROFILES, _profile(first, source_system=slug), "ck_integration_profile_source_system")
    for slug in ("Issabel", "9pbx", ""):
        _deny(
            PROFILES,
            _profile(
                first, source_system=f"conv-{uuid.uuid4().hex[:6]}", conversation_source_system=slug
            ),
            "ck_integration_profile_conversation_source",
        )
    with pytest.raises(DBAPIError, match=r"character varying\(60\)"):
        _insert_as_dba(PROFILES, _profile(first, source_system="a" * 61))
    for slug in ("odoo", "hamkaran-crm", "a.b_c", "x", "a" * 60, "z9"):
        _insert_as_dba(
            PROFILES, _profile(first, source_system=slug, conversation_source_system=slug)
        )

    # One profile per workspace and source system; another workspace may use the same slug.
    _deny(PROFILES, _profile(first, source_system="odoo"), "uq_integration_profile_source")
    _insert_as_dba(PROFILES, _profile(second, source_system="odoo"))

    # The late-record window and the version.
    for seconds in (3599, 2592001, 0, -1):
        _deny(
            PROFILES,
            _profile(first, source_system="window", late_record_window_seconds=seconds),
            "ck_integration_profile_late_window",
        )
    _insert_as_dba(
        PROFILES, _profile(first, source_system="window-min", late_record_window_seconds=3600)
    )
    _insert_as_dba(
        PROFILES, _profile(first, source_system="window-max", late_record_window_seconds=2592000)
    )
    for version in (0, -1):
        _deny(
            PROFILES,
            _profile(first, source_system="ver", version=version),
            "ck_integration_profile_version",
        )
    _insert_as_dba(PROFILES, _profile(first, source_system="ver", version=1))

    # External records: identity per (workspace, source system, record type, external id).
    record_id = _insert_as_dba(RECORDS, _record(first, external_id="C-9"))
    _deny(RECORDS, _record(first, external_id="C-9"), "uq_external_record_identity")
    _insert_as_dba(RECORDS, _record(first, external_id="C-9", record_type="ticket"))
    _insert_as_dba(RECORDS, _record(first, external_id="C-9", source_system="hamkaran"))
    _insert_as_dba(RECORDS, _record(second, external_id="C-9"))
    _deny(
        RECORDS,
        _record(first, external_id="C-10", record_type="contact"),
        "ck_external_record_type",
    )
    _deny(RECORDS, _record(first, external_id=""), "ck_external_record_id")
    _insert_as_dba(RECORDS, _record(first, external_id="C-10", record_type="agent"))

    # The ledger: identity per (workspace, source system, event id).
    profile_id = _insert_as_dba(PROFILES, _profile(first, source_system="ledger"))
    inbound_id = _insert_as_dba(LEDGER, _inbound(first, profile_id, event_id="E-9"))
    _deny(LEDGER, _inbound(first, profile_id, event_id="E-9"), "uq_inbound_record_event")
    _insert_as_dba(LEDGER, _inbound(first, profile_id, event_id="E-9", source_system="hamkaran"))
    _deny(LEDGER, _inbound(first, profile_id, event_id=""), "ck_inbound_record_event_id")
    # A pending record needs its expiry; other states do not.
    _deny(
        LEDGER,
        _inbound(first, profile_id, event_id="E-pending", state="pending", expires_at=None),
        "ck_inbound_record_pending_expiry",
    )
    _insert_as_dba(LEDGER, _inbound(first, profile_id, event_id="E-applied", state="applied"))
    _insert_as_dba(
        LEDGER, _inbound(first, profile_id, event_id="E-reconciliation", state="reconciliation")
    )
    _insert_as_dba(
        LEDGER,
        _inbound(
            first,
            profile_id,
            event_id="E-pending",
            state="pending",
            expires_at=datetime.now(UTC) + timedelta(days=7),
        ),
    )
    _deny(
        LEDGER,
        _inbound(first, profile_id, event_id="E-state", state="done"),
        "ck_inbound_record_state",
    )
    for digest in ("a" * 63, "a" * 0):
        _deny(
            LEDGER,
            _inbound(first, profile_id, event_id="E-hash", payload_hash=digest),
            "ck_inbound_record_payload_hash",
        )
    _insert_as_dba(LEDGER, _inbound(first, profile_id, event_id="E-hash", payload_hash="b" * 64))
    _deny(
        LEDGER,
        _inbound(first, profile_id, event_id="E-n", attempt_count=-1),
        "ck_inbound_record_attempts",
    )
    _deny(
        LEDGER,
        _inbound(first, profile_id, event_id="E-n", conflict_count=-1),
        "ck_inbound_record_conflicts",
    )

    # Links: the shape of an outcome link and of a record link.
    _deny(
        LINKS,
        _outcome_link(first, meeting, external_record_id=record_id),
        "ck_conversation_link_shape",
    )
    _deny(LINKS, _outcome_link(first, meeting, outcome=None), "ck_conversation_link_shape")
    _deny(
        LINKS,
        _outcome_link(first, meeting, external_outcome_code=None),
        "ck_conversation_link_shape",
    )
    _insert_as_dba(LINKS, _outcome_link(first, meeting))
    _deny(LINKS, _link(first, meeting, None), "ck_conversation_link_shape")
    _deny(LINKS, _link(first, meeting, record_id, outcome="won"), "ck_conversation_link_shape")
    _deny(
        LINKS,
        _link(first, meeting, record_id, external_outcome_code="WON"),
        "ck_conversation_link_shape",
    )
    _insert_as_dba(LINKS, _link(first, meeting, record_id))
    # The agent user only on an agent link.
    _deny(
        LINKS,
        _link(first, other_meeting, record_id, agent_user_id=first["user_id"]),
        "ck_conversation_link_agent",
    )
    _insert_as_dba(
        LINKS, _link(first, meeting, record_id, link_type="agent", agent_user_id=first["user_id"])
    )
    # The outcome must be a canonical outcome.
    _deny(
        LINKS,
        _outcome_link(first, other_meeting, outcome="sold", external_outcome_code="SOLD"),
        "ck_conversation_link_outcome",
    )
    _insert_as_dba(LINKS, _outcome_link(first, other_meeting))
    _deny(
        LINKS,
        _link(first, other_meeting, record_id, link_type="phone"),
        "ck_conversation_link_type",
    )
    # One current link per conversation, source system and link type.
    _deny(LINKS, _link(first, meeting, record_id), "uq_conversation_external_link")
    _insert_as_dba(LINKS, _link(first, meeting, record_id, source_system="hamkaran"))
    _insert_as_dba(LINKS, _link(first, meeting, record_id, link_type="ticket"))

    # Reconciliation items: closed means resolution and resolved_at, open means neither.
    now = datetime.now(UTC)
    _deny(
        ITEMS,
        _item(first, inbound_id, status="resolved", resolution="link", resolved_at=None),
        "ck_reconciliation_closed",
    )
    _deny(
        ITEMS,
        _item(first, inbound_id, status="dismissed", resolution=None, resolved_at=now),
        "ck_reconciliation_closed",
    )
    _insert_as_dba(
        ITEMS, _item(first, inbound_id, status="resolved", resolution="link", resolved_at=now)
    )
    _deny(
        ITEMS,
        _item(first, inbound_id, status="open", resolution="dismiss"),
        "ck_reconciliation_closed",
    )
    _deny(
        ITEMS, _item(first, inbound_id, status="open", resolved_at=now), "ck_reconciliation_closed"
    )
    _insert_as_dba(ITEMS, _item(first, inbound_id, status="open"))
    _deny(ITEMS, _item(first, inbound_id, reason="fuzzy_match"), "ck_reconciliation_reason")
    _insert_as_dba(
        ITEMS,
        _item(
            first,
            inbound_id,
            reason="changed_id",
            meeting_id=meeting,
            link_type="customer",
            current_value="C-1",
            candidate_value="C-2",
        ),
    )
    _deny(
        ITEMS,
        _item(first, inbound_id, status="closed", resolution="dismiss", resolved_at=now),
        "ck_reconciliation_status",
    )
    _deny(
        ITEMS,
        _item(first, inbound_id, status="resolved", resolution="merge", resolved_at=now),
        "ck_reconciliation_resolution",
    )
    _deny(ITEMS, _item(first, inbound_id, link_type="phone"), "ck_reconciliation_link_type")
    _insert_as_dba(
        ITEMS,
        _item(
            first,
            inbound_id,
            reason="ambiguous_conversation",
            candidate_meeting_ids=[meeting, other_meeting],
            status="dismissed",
            resolution="dismiss",
            resolved_at=now,
            resolved_by_user_id=first["user_id"],
        ),
    )


# --- the ORM, the constants and the RLS lists --------------------------------------------


def test_the_orm_declares_the_tables():
    for table_name, model in MODELS.items():
        table = model.__table__
        assert table.name == table_name
        assert [column.name for column in table.columns] == [
            column[0] for column in COLUMNS[table_name]
        ]
        for name, _type, length, _nullable in COLUMNS[table_name]:
            if length is not None:
                assert table.c[name].type.length == length, (table_name, name)
        nullable = {column.name for column in table.columns if column.nullable}
        assert nullable == {
            name for name, _type, _length, null in COLUMNS[table_name] if null == "YES"
        }, table_name
        assert [column.name for column in table.primary_key] == ["id"]
        assert table.c.id.default.arg.__name__ == new_id.__name__
        foreign_keys = {
            key.parent.name: (key.target_fullname, key.ondelete) for key in table.foreign_keys
        }
        assert foreign_keys == FOREIGN_KEYS[table_name], table_name
        named = {constraint.name: constraint for constraint in table.constraints}
        for name, columns in UNIQUES[table_name].items():
            assert [column.name for column in named[name].columns] == columns, name
        checks = {name for name in named if name and name.startswith("ck_")}
        assert checks == CHECKS[table_name], table_name
        indexes = {index.name: [column.name for column in index.columns] for index in table.indexes}
        assert indexes == INDEXES[table_name], table_name
        assert all(not index.unique for index in table.indexes)
        assert "HOM-88" in (model.__doc__ or "")
        for column in table.columns:
            if column.type.__class__.__name__ == "DateTime":
                assert column.type.timezone, (table_name, column.name)


def test_the_orm_defaults_follow_the_design():
    profile = IntegrationMappingProfile.__table__.c
    assert profile.outcome_map.default.arg.__name__ == "dict"
    assert profile.agent_map.default.arg.__name__ == "dict"
    assert profile.late_record_window_seconds.default.arg == 604800
    assert profile.is_active.default.arg is True
    assert profile.version.default.arg == 1
    assert ExternalRecord.__table__.c.is_active.default.arg is True
    ledger = IntegrationInboundRecord.__table__.c
    assert ledger.attempt_count.default.arg == 0
    assert ledger.conflict_count.default.arg == 0
    items = IntegrationReconciliationItem.__table__.c
    assert items.status.default.arg == "open"
    assert items.candidate_meeting_ids.default.arg.__name__ == "list"
    assert items.created_at.default.arg.__name__ == utcnow.__name__


def test_the_enums_hold_the_design_values():
    assert [m.value for m in ExternalRecordType] == ["customer", "ticket", "agent"]
    assert [m.value for m in ExternalLinkType] == ["customer", "ticket", "agent", "outcome"]
    assert [m.value for m in CanonicalOutcome] == [
        "resolved",
        "unresolved",
        "escalated",
        "follow_up",
        "won",
        "lost",
        "other",
    ]
    assert [m.value for m in InboundRecordState] == ["applied", "pending", "reconciliation"]
    assert [m.value for m in ReconciliationReason] == [
        "conversation_not_found",
        "ambiguous_conversation",
        "changed_id",
        "unknown_agent",
        "unknown_outcome",
        "inactive_record",
    ]
    assert [m.value for m in ReconciliationStatus] == ["open", "resolved", "dismissed"]
    assert [m.value for m in ReconciliationResolution] == ["link", "keep_current", "dismiss"]


def test_the_check_constants_are_built_from_the_enums_without_regex():
    pairs = (
        (EXTERNAL_RECORD_TYPE_CHECK, ExternalRecordType),
        (EXTERNAL_LINK_TYPE_CHECK, ExternalLinkType),
        (RECONCILIATION_LINK_TYPE_CHECK, ExternalLinkType),
        (LINK_OUTCOME_CHECK, CanonicalOutcome),
        (INBOUND_RECORD_STATE_CHECK, InboundRecordState),
        (RECONCILIATION_REASON_CHECK, ReconciliationReason),
        (RECONCILIATION_STATUS_CHECK, ReconciliationStatus),
        (RECONCILIATION_RESOLUTION_CHECK, ReconciliationResolution),
    )
    for check, enum_type in pairs:
        listed = check.split("IN (", 1)[1].rstrip(")")
        assert listed == ", ".join(f"'{member.value}'" for member in enum_type), check
    for check in (
        *(check for check, _ in pairs),
        INTEGRATION_SOURCE_SYSTEM_CHECK,
        INTEGRATION_CONVERSATION_SOURCE_CHECK,
    ):
        for regex in ("~", "REGEXP", "SIMILAR", "LIKE"):
            assert regex not in check.upper(), check


def test_sqlite_runs_the_same_slug_and_window_checks():
    """The checks hold on SQLite too: the ORM table on an in-memory SQLite database."""
    table = IntegrationMappingProfile.__table__
    engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine, tables=[table])
        tenant = {"organization_id": "org", "workspace_id": "ws", "user_id": None}
        with engine.begin() as connection:
            for slug in ("odoo", "hamkaran-crm", "a.b_c", "a" * 60):
                connection.execute(table.insert(), _profile(tenant, source_system=slug))
        for row, constraint in (
            (_profile(tenant, source_system="Odoo"), "ck_integration_profile_source_system"),
            (_profile(tenant, source_system="1odoo"), "ck_integration_profile_source_system"),
            (_profile(tenant, source_system=""), "ck_integration_profile_source_system"),
            (_profile(tenant, source_system="a" * 61), "ck_integration_profile_source_system"),
            (
                _profile(tenant, source_system="ok", conversation_source_system="Pbx"),
                "ck_integration_profile_conversation_source",
            ),
            (
                _profile(tenant, source_system="ok", late_record_window_seconds=3599),
                "ck_integration_profile_late_window",
            ),
            (_profile(tenant, source_system="ok", version=0), "ck_integration_profile_version"),
        ):
            with pytest.raises(IntegrityError, match=constraint), engine.begin() as connection:
                connection.execute(table.insert(), row)
    finally:
        engine.dispose()


def test_the_five_tables_are_workspace_tables_and_the_ledger_has_the_scope():
    for table in NON_LEDGER:
        assert table in WORKSPACE_TABLES
        assert POLICIES[table] == (f"workspace_id = {TENANT}", f"workspace_id = {TENANT}")
    assert LEDGER not in WORKSPACE_TABLES
    # The read-only discovery path, exactly like ``usage_reconciliation`` on entitlements.
    assert POLICIES[LEDGER] == (
        f"workspace_id = {TENANT} OR {SERVICE} = 'crm_reconciliation'",
        f"workspace_id = {TENANT}",
    )
    assert POLICIES[LEDGER] == tuple(
        expression.replace("usage_reconciliation", "crm_reconciliation")
        for expression in POLICIES["entitlements"]
    )
    for table in TABLES:
        assert table not in SHARED_CONTENT_TABLES  # never through a share token
        assert table not in UNPROTECTED_TABLES
        assert table not in APPEND_ONLY_TABLES  # PR 2 updates rows; links and items change
    assert NO_DELETE_TABLES == (LEDGER, "webhook_deliveries")
    assert SERVICE_SCOPE_CRM_RECONCILIATION == "crm_reconciliation"


# --- PostgreSQL: the ORM schema (create_all) ---------------------------------------------


@pytest.mark.postgres
def test_the_orm_schema_has_the_tables_and_refuses_the_deny_cases(client, email_outbox):
    _requires_postgres()
    first = _tenant(client, email_outbox, "rules-a")
    second = _tenant(client, email_outbox, "rules-b")
    _assert_head_shape()
    _assert_rules(first, second)


@pytest.mark.postgres
def test_each_workspace_reads_and_writes_only_its_own_rows(client, email_outbox):
    _requires_postgres()
    first = _tenant(client, email_outbox, "tenant-a")
    second = _tenant(client, email_outbox, "tenant-b")
    seeded_a = _seed(first, "a")
    seeded_b = _seed(second, "b")
    factory = get_session_factory()

    # Bound to B: only B's rows in every table, and an insert for A fails.
    for table in TABLES:
        with factory() as db:
            assert db.scalar(text("SELECT current_user")) == APP_ROLE
            bind_tenant_context(db, workspace_id=second["workspace_id"])
            assert _visible(db, table) == {seeded_b[table]}, table
            with pytest.raises(DBAPIError, match="row-level security"):
                _insert(db, table, _probe(first, seeded_a, table))
            db.rollback()

    # Bound to A: only A's rows, and the same insert passes.
    for table in TABLES:
        with factory() as db:
            bind_tenant_context(db, workspace_id=first["workspace_id"])
            assert _visible(db, table) == {seeded_a[table]}, table
            _insert(db, table, _probe(first, seeded_a, table))
            db.commit()

    # No workspace bound: nothing.
    with factory() as db:
        for table in TABLES:
            assert _visible(db, table) == set(), table

    for table in TABLES:
        assert _ids(table, second["workspace_id"]) == {seeded_b[table]}, table
        assert len(_ids(table, first["workspace_id"])) == 2, table


@pytest.mark.postgres
def test_the_crm_reconciliation_scope_only_reads_the_ledger(client, email_outbox):
    _requires_postgres()
    first = _tenant(client, email_outbox, "scope-a")
    second = _tenant(client, email_outbox, "scope-b")
    seeded_a = _seed(first, "a")
    seeded_b = _seed(second, "b")
    factory = get_session_factory()

    # The scope without a workspace: the ledger rows of both workspaces, nothing else.
    with factory() as db:
        bind_service_scope(db, SERVICE_SCOPE_CRM_RECONCILIATION)
        assert _visible(db, LEDGER) == {seeded_a[LEDGER], seeded_b[LEDGER]}
        pending = db.execute(
            text("SELECT id, workspace_id, state, expires_at FROM integration_inbound_records")
        ).all()
        assert {row.workspace_id for row in pending} == {
            first["workspace_id"],
            second["workspace_id"],
        }
        for table in NON_LEDGER:
            assert _visible(db, table) == set(), table

    # The scope cannot write: INSERT and UPDATE fail on the tenant-bound WITH CHECK.
    with factory() as db:
        bind_service_scope(db, SERVICE_SCOPE_CRM_RECONCILIATION)
        with pytest.raises(DBAPIError, match="row-level security"):
            _insert(db, LEDGER, _probe(first, seeded_a, LEDGER))
        db.rollback()
    with factory() as db:
        bind_service_scope(db, SERVICE_SCOPE_CRM_RECONCILIATION)
        with pytest.raises(DBAPIError, match="row-level security"):
            db.execute(
                text("UPDATE integration_inbound_records SET attempt_count = 1 WHERE id = :id"),
                {"id": seeded_a[LEDGER]},
            )
        db.rollback()
    assert (
        _scalar(
            "SELECT attempt_count FROM integration_inbound_records WHERE id = :id",
            id=seeded_a[LEDGER],
        )
        == 0
    )

    # Allow-control: the same INSERT and UPDATE bound to the row's workspace work.
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        probe_id = _insert(db, LEDGER, _probe(first, seeded_a, LEDGER))
        updated = db.execute(
            text("UPDATE integration_inbound_records SET attempt_count = 1 WHERE id = :id"),
            {"id": seeded_a[LEDGER]},
        )
        assert updated.rowcount == 1
        db.commit()
    assert probe_id in _ids(LEDGER, first["workspace_id"])
    assert (
        _scalar(
            "SELECT attempt_count FROM integration_inbound_records WHERE id = :id",
            id=seeded_a[LEDGER],
        )
        == 1
    )

    # Another service scope reaches no ledger row.
    with factory() as db:
        bind_service_scope(db, SERVICE_SCOPE_USAGE_RECONCILIATION)
        assert _visible(db, LEDGER) == set()
        for table in NON_LEDGER:
            assert _visible(db, table) == set(), table


@pytest.mark.postgres
def test_the_app_role_never_deletes_ledger_rows(client, email_outbox):
    _requires_postgres()
    first = _tenant(client, email_outbox, "delete-a")
    second = _tenant(client, email_outbox, "delete-b")
    seeded_a = _seed(first, "a")
    seeded_b = _seed(second, "b")
    privileges = {
        table: {
            privilege: _scalar(
                "SELECT has_table_privilege(:r, :t, :p)", r=APP_ROLE, t=table, p=privilege
            )
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
        }
        for table in TABLES
    }
    assert privileges[LEDGER] == {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": False}
    for table in NON_LEDGER:
        assert all(privileges[table].values()), table

    # Bound to the row's workspace, or under the scope: the refusal is the missing grant.
    factory = get_session_factory()
    for bind in (
        lambda db: bind_tenant_context(db, workspace_id=first["workspace_id"]),
        lambda db: bind_service_scope(db, SERVICE_SCOPE_CRM_RECONCILIATION),
    ):
        with factory() as db:
            bind(db)
            assert seeded_a[LEDGER] in _visible(db, LEDGER)
            with pytest.raises(DBAPIError, match="permission denied"):
                db.execute(
                    text("DELETE FROM integration_inbound_records WHERE id = :id"),
                    {"id": seeded_a[LEDGER]},
                )
            db.rollback()
    assert _ids(LEDGER, first["workspace_id"]) == {seeded_a[LEDGER]}

    # Allow-control: the app may delete its own reconciliation item (not a ledger row).
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        db.execute(
            text("DELETE FROM integration_reconciliation_items WHERE id = :id"),
            {"id": seeded_a[ITEMS]},
        )
        db.commit()
    assert _ids(ITEMS, first["workspace_id"]) == set()

    # The owner deletes workspace A: its rows in all five tables go by cascade.
    with inspection_session_factory()() as db:
        db.execute(text("DELETE FROM workspaces WHERE id = :w"), {"w": first["workspace_id"]})
        db.commit()
    for table in TABLES:
        assert _ids(table, first["workspace_id"]) == set(), table
        assert _ids(table, second["workspace_id"]) == {seeded_b[table]}, table


@pytest.mark.postgres
def test_deleting_a_conversation_or_a_profile_follows_the_foreign_keys(client, email_outbox):
    _requires_postgres()
    tenant = _tenant(client, email_outbox, "cascade")
    seeded = _seed(tenant, "c")

    # A linked external record cannot be deleted (RESTRICT).
    with pytest.raises(IntegrityError, match=f"{LINKS}_external_record_id_fkey"):
        with inspection_session_factory()() as db:
            db.execute(text("DELETE FROM external_records WHERE id = :id"), {"id": seeded[RECORDS]})
            db.commit()

    # The conversation goes: its links go; the ledger row and the item stay, unlinked.
    with inspection_session_factory()() as db:
        db.execute(text("DELETE FROM meetings WHERE id = :id"), {"id": seeded["meeting"]})
        db.commit()
    assert _ids(LINKS, tenant["workspace_id"]) == set()
    assert (
        _scalar(
            "SELECT meeting_id FROM integration_inbound_records WHERE id = :id", id=seeded[LEDGER]
        )
        is None
    )
    assert _ids(ITEMS, tenant["workspace_id"]) == {seeded[ITEMS]}

    # The profile goes: its ledger rows and their items go.
    with inspection_session_factory()() as db:
        db.execute(
            text("DELETE FROM integration_mapping_profiles WHERE id = :id"),
            {"id": seeded[PROFILES]},
        )
        db.commit()
    assert _ids(LEDGER, tenant["workspace_id"]) == set()
    assert _ids(ITEMS, tenant["workspace_id"]) == set()
    assert _ids(RECORDS, tenant["workspace_id"]) == {seeded[RECORDS]}


# --- PostgreSQL: the migration -----------------------------------------------------------


def _migration_module():
    return importlib.import_module(f"backend.migrations.versions.{NEW_REVISION}_crm_mapping_tables")


def test_the_migration_follows_the_previous_head():
    module = _migration_module()
    assert module.revision == NEW_REVISION
    assert module.down_revision == BASE_REVISION
    doc = " ".join((module.__doc__ or "").split())
    assert "HOM-88" in doc
    for words in (
        "raw payload",
        "never merged",
        "meetings.agent_user_id",
        "crm_reconciliation",
        "read-only",
        "Downgrade",
    ):
        assert words in doc, words


@pytest.mark.postgres
def test_the_migration_creates_and_drops_only_the_five_tables(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    from alembic import command

    from backend.config import reset_settings_cache
    from backend.observability.release import code_migration_head

    assert code_migration_head() == HEAD_REVISION
    first = _tenant(client, email_outbox, "migration-a")
    second = _tenant(client, email_outbox, "migration-b")
    orm_shapes = _assert_head_shape()
    workspaces = _scalar("SELECT count(*) FROM workspaces")
    meetings_before = _scalar("SELECT count(*) FROM meetings")
    _seed(first, "before")

    config = _alembic(monkeypatch, settings)
    try:
        command.stamp(config, "head")
        command.downgrade(config, NEW_REVISION)
        # The re-upgrade applies the live RLS registry (HOM-89), so the baseline must too.
        with inspection_session_factory()() as db:
            apply_row_level_security(db.connection())
            db.commit()
        others = _other_tables()  # after the stamp, which adds alembic_version
        command.downgrade(config, BASE_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        for table in TABLES:
            assert _scalar("SELECT to_regclass(:t) IS NULL", t=f"public.{table}"), table
            assert _shape(table) == EMPTY_SHAPE, table
        assert _other_tables() == others  # every other table as it was
        assert _scalar("SELECT count(*) FROM workspaces") == workspaces
        assert _scalar("SELECT count(*) FROM meetings") == meetings_before + 1

        command.upgrade(config, NEW_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == NEW_REVISION
        for table in TABLES:
            assert _shape(table) == orm_shapes[table], table  # migrated equals the ORM schema
            assert _ids(table) == set(), table  # the downgrade dropped the rows
        assert _other_tables() == others

        # The migrated schema refuses the same deny cases; then rows go in for the next step.
        _assert_rules(first, second)
        _seed(second, "after")

        # Down and up again with rows present.
        command.downgrade(config, BASE_REVISION)
        assert _other_tables() == others
        command.upgrade(config, NEW_REVISION)
        for table in TABLES:
            assert _shape(table) == orm_shapes[table], table
            assert _ids(table) == set(), table
        assert _other_tables() == others
        command.upgrade(config, "head")
    finally:
        reset_settings_cache()
