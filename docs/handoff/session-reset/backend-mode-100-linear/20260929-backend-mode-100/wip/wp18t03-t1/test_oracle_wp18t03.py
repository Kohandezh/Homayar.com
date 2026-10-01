"""Oracle for WP18-T03 (HOM-101): versioned plans, insert-only catalog seeding.

Written from the SPEC of team wp18t03-t1 before the implementation existed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, bindparam, select, text
from sqlalchemy.exc import DBAPIError

from backend.config import Settings
from backend.database.relational import get_session_factory, reset_database_state
from backend.database.saas_models import Membership, User, WorkspaceRole
from backend.database.tenant_context import bind_tenant_context
from backend.main import create_app
from backend.services.rate_limit_service import RateLimiter
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user, register_user

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_HEAD = "a1c3e5f7b9d2"
INT32_MAX = 2147483647
CATALOG_KEYS = ("business", "demo", "enterprise", "on_premise", "professional")
CATALOG = {
    "demo": ("Free Demo", True, {"concurrent_jobs": 1, "seats": 1}),
    "professional": ("Professional", True, {}),
    "business": ("Business", True, {}),
    "enterprise": ("Enterprise", False, {}),
    "on_premise": ("On-Premise", False, {}),
}
VERSIONED_FIELDS = frozenset({"name", "is_public", "entitlements_json"})
DRIFT_LINE = re.compile(
    r"PLAN_CATALOG_DRIFT plan=(?P<plan>\S+) fields=(?P<fields>[a-z_]+(?:\s*,\s*[a-z_]+)*)"
)
PUBLISHED_LINE = re.compile(
    r"PLAN_VERSION_PUBLISHED plan=(?P<plan>\S+) version=(?P<version>\d+) source=(?P<source>\S+)"
)
_JSON_COLUMNS = {
    "plans": ("entitlements_json", "display_json"),
    "plan_versions": ("entitlements_json",),
    "subscriptions": (),
    "entitlements": (),
}
_ORDER = {
    "plans": "key",
    "plan_versions": "plan_key, version",
    "subscriptions": "id",
    "entitlements": "id",
}


@pytest.fixture(autouse=True)
def _dispose_engines() -> Iterator[None]:
    yield
    reset_database_state()


def _pg() -> bool:
    return is_postgres_url(configured_database_url())


def _dba():
    return inspection_session_factory()()


def _clause(statement: str, params: dict[str, Any]):
    clause = text(statement)
    typed = [
        bindparam(name, type_=JSON) for name, value in params.items() if isinstance(value, dict)
    ]
    return clause.bindparams(*typed) if typed else clause


def _dba_execute(*steps: tuple[str, dict[str, Any]]) -> None:
    with _dba() as db:
        for statement, params in steps:
            db.execute(_clause(statement, params), params)
        db.commit()


def _table_exists(name: str) -> bool:
    with _dba() as db:
        if _pg():
            return bool(
                db.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name}).scalar()
            )
        return (
            db.execute(
                text("SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = :n"),
                {"n": name},
            ).scalar()
            == 1
        )


def _decode(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _rows(table: str, where: str = "", params: dict[str, Any] | None = None) -> list[dict]:
    extra = ""
    if _pg():
        extra = ", xmin::text AS _xmin" + "".join(
            f", {column}::text AS _raw_{column}" for column in _JSON_COLUMNS[table]
        )
    sql = f"SELECT *{extra} FROM {table} {where} ORDER BY {_ORDER[table]}"  # noqa: S608
    with _dba() as db:
        rows = [dict(row) for row in db.execute(text(sql), params or {}).mappings()]
    for row in rows:
        for column in _JSON_COLUMNS[table]:
            row[column] = _decode(row[column])
    return rows


def _plans() -> dict[str, dict]:
    return {row["key"]: row for row in _rows("plans")}


def _versions(plan_key: str | None = None) -> list[dict]:
    if plan_key is None:
        return _rows("plan_versions")
    return _rows("plan_versions", "WHERE plan_key = :k", {"k": plan_key})


def _columns(row: dict) -> dict:
    return {name: value for name, value in row.items() if not name.startswith("_")}


def _terms(row: dict) -> tuple[str, bool, dict]:
    return row["name"], bool(row["is_public"]), row["entitlements_json"]


def _catalog_row(key: str, quota: int) -> tuple[str, bool, dict]:
    name, is_public, entitlements = CATALOG[key]
    if key == "demo":
        entitlements = {"audio_seconds": quota, **entitlements}
    return name, is_public, dict(entitlements)


def _apply_operator_edits() -> None:
    _dba_execute(
        (
            "UPDATE plans SET entitlements_json = :e WHERE key = 'professional'",
            {"e": {"audio_seconds": 360000, "concurrent_jobs": 3}},
        ),
        ("UPDATE plans SET is_public = false WHERE key = 'business'", {}),
        (
            "UPDATE plans SET name = :n, entitlements_json = :e WHERE key = 'enterprise'",
            {"n": "Enterprise (negotiated)", "e": {"audio_seconds": 900000, "seats": 50}},
        ),
        ("UPDATE plans SET is_active = false WHERE key = 'on_premise'", {}),
        ("UPDATE plans SET display_json = :d WHERE key = 'professional'", {"d": {"badge": "x"}}),
    )


def _dba_delete_plan(key: str) -> None:
    steps = []
    if _table_exists("plan_versions"):
        steps.append(("DELETE FROM plan_versions WHERE plan_key = :k", {"k": key}))
    steps.append(("DELETE FROM plans WHERE key = :k", {"k": key}))
    _dba_execute(*steps)


def _dba_delete_all_plans() -> None:
    steps = []
    if _table_exists("plan_versions"):
        steps.append(("DELETE FROM plan_versions", {}))
    steps.append(("DELETE FROM plans", {}))
    _dba_execute(*steps)


@contextmanager
def _captured_logs() -> Iterator[list[logging.LogRecord]]:
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    target = logging.getLogger("homayar")
    handler = _Collector(level=logging.DEBUG)
    previous_level, previous_disabled = target.level, target.disabled
    target.addHandler(handler)
    target.setLevel(logging.INFO)
    target.disabled = False
    try:
        yield records
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)
        target.disabled = previous_disabled


def _drift(records: list[logging.LogRecord]) -> dict[str, list[frozenset[str]]]:
    found: dict[str, list[frozenset[str]]] = {}
    for record in records:
        message = record.getMessage()
        if "PLAN_CATALOG_DRIFT" not in message:
            continue
        match = DRIFT_LINE.search(message)
        assert match is not None, message
        assert record.levelno == logging.WARNING, message
        fields = frozenset(part.strip() for part in match["fields"].split(","))
        found.setdefault(match["plan"], []).append(fields)
    return found


def _published(records: list[logging.LogRecord]) -> list[tuple[str, int, str]]:
    found = []
    for record in records:
        match = PUBLISHED_LINE.search(record.getMessage())
        if match is not None:
            assert record.levelno == logging.INFO, record.getMessage()
            found.append((match["plan"], int(match["version"]), match["source"]))
    return found


@contextmanager
def _restarted(settings: Settings, **overrides: Any) -> Iterator[TestClient]:
    runtime = settings.model_copy(update=overrides) if overrides else settings
    with TestClient(create_app(runtime)) as api:
        yield api


def _restart(settings: Settings, **overrides: Any) -> None:
    with _restarted(settings, **overrides):
        pass


def _tenancy():
    from backend.services import tenancy_service

    return tenancy_service


def _plan_catalog_error() -> type[Exception]:
    return _tenancy().PlanCatalogError


def _publish(plan_key: str, **kwargs: Any) -> int:
    with get_session_factory()() as db:
        version = _tenancy().publish_plan_version(db, plan_key, **kwargs)
        number = version.version
        db.commit()
    return number


def _signup(api: TestClient, email_outbox: list[dict[str, str]], email: str) -> dict:
    RateLimiter.reset_memory()
    credentials = register_user(api, email_outbox, email=email)
    with _dba() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
        assert membership is not None
        return {
            "credentials": credentials,
            "user_id": user.id,
            "workspace_id": membership.workspace_id,
        }


def _subscriptions(workspace_id: str) -> list[dict]:
    return _rows("subscriptions", "WHERE workspace_id = :w", {"w": workspace_id})


def _limits(workspace_id: str) -> dict[str, dict]:
    rows = _rows("entitlements", "WHERE workspace_id = :w", {"w": workspace_id})
    return {row["metric"]: row for row in rows}


def _owner_get(api: TestClient, tenant: dict, path: str):
    RateLimiter.reset_memory()
    login_user(api, tenant["credentials"])
    return api.get(path, headers={"X-Workspace-ID": tenant["workspace_id"]})


def _public_catalog(api: TestClient, tenant: dict) -> dict[str, dict]:
    response = _owner_get(api, tenant, "/api/v1/billing/plans")
    assert response.status_code == 200, response.text
    return {item["key"]: item for item in response.json()["items"]}


def test_restart_keeps_operator_edited_plan_rows_and_reports_drift(client, email_outbox, settings):
    owner = _signup(client, email_outbox, "ac1-owner@example.com")
    _apply_operator_edits()
    before = _plans()
    versions_before = _versions()

    catalog = _public_catalog(client, owner)
    assert catalog["professional"]["entitlements"] == {
        "audio_seconds": 360000,
        "concurrent_jobs": 3,
    }
    assert "business" not in catalog

    with _captured_logs() as records, _restarted(settings) as api:
        catalog_after = _public_catalog(api, owner)

    assert _plans() == before
    assert all(row["current_version"] >= 1 for row in before.values())
    assert len(_versions()) == len(versions_before)
    assert catalog_after["professional"]["entitlements"] == {
        "audio_seconds": 360000,
        "concurrent_jobs": 3,
    }
    assert "business" not in catalog_after
    assert _drift(records) == {
        "professional": [frozenset({"entitlements_json"})],
        "business": [frozenset({"is_public"})],
        "enterprise": [frozenset({"name", "entitlements_json"})],
    }
    drift_text = " ".join(r.getMessage() for r in records if "PLAN_CATALOG_DRIFT" in r.getMessage())
    for value in ("360000", "900000", "negotiated", "badge"):
        assert value not in drift_text


def test_restart_keeps_operator_edits_visible_in_the_public_catalog(client, email_outbox, settings):
    owner = _signup(client, email_outbox, "ac1-catalog@example.com")
    _apply_operator_edits()
    with _restarted(settings) as api:
        catalog = _public_catalog(api, owner)
    assert set(catalog) == {"demo", "professional"}
    assert catalog["professional"]["entitlements"] == {
        "audio_seconds": 360000,
        "concurrent_jobs": 3,
    }
    plans = _plans()
    assert _terms(plans["enterprise"]) == (
        "Enterprise (negotiated)",
        False,
        {"audio_seconds": 900000, "seats": 50},
    )
    assert bool(plans["on_premise"]["is_active"]) is False
    assert plans["professional"]["display_json"] == {"badge": "x"}


def test_idle_restart_leaves_every_plan_row_untouched(client, settings):
    before = _plans()
    _restart(settings)
    assert _plans() == before


def test_idle_restart_appends_no_version_and_logs_no_drift(client, settings):
    before = _plans()
    versions_before = _versions()
    assert len(versions_before) == len(CATALOG_KEYS)
    with _captured_logs() as records:
        _restart(settings)
    assert _plans() == before
    assert _versions() == versions_before
    assert _drift(records) == {}
    assert _published(records) == []


@pytest.mark.parametrize("session", ["dba", "app"])
def test_direct_catalog_call_without_settings_keeps_edited_rows(client, session):
    _apply_operator_edits()
    before = _plans()
    versions_before = _versions()
    factory = inspection_session_factory() if session == "dba" else get_session_factory()
    with factory() as db:
        _tenancy().ensure_plan_catalog(db)
        db.commit()
    assert _plans() == before
    assert _versions() == versions_before


def test_restart_reports_a_missing_current_version_row_as_drift(client, settings):
    _dba_execute(("DELETE FROM plan_versions WHERE plan_key = 'enterprise'", {}))
    before = _plans()
    versions_before = _versions()
    with _captured_logs() as records:
        _restart(settings)
    assert _plans() == before
    assert _versions() == versions_before
    drift = [
        r for r in records if re.search(r"PLAN_CATALOG_DRIFT plan=enterprise(\s|$)", r.getMessage())
    ]
    assert len(drift) == 1
    assert drift[0].levelno == logging.WARNING
    assert not any("plan=" in m and "enterprise" not in m for m in _drift_messages(records))


def _drift_messages(records: list[logging.LogRecord]) -> list[str]:
    return [r.getMessage() for r in records if "PLAN_CATALOG_DRIFT" in r.getMessage()]


def test_signup_keeps_dba_edited_plan_rows(client, email_outbox):
    _apply_operator_edits()
    before = _plans()
    _signup(client, email_outbox, "ac2-plain@example.com")
    assert _plans() == before


def test_signup_after_a_publish_leaves_plans_and_versions_unchanged(client, email_outbox):
    _apply_operator_edits()
    published = _publish(
        "professional",
        entitlements={"audio_seconds": 720000, "concurrent_jobs": 4},
        source="operator",
    )
    assert published == 2
    plans_before = _plans()
    versions_before = _versions()
    tenant = _signup(client, email_outbox, "ac2-after-publish@example.com")
    assert _plans() == plans_before
    assert _versions() == versions_before
    [subscription] = _subscriptions(tenant["workspace_id"])
    assert subscription["plan_key"] == "demo"
    assert subscription["plan_version"] == plans_before["demo"]["current_version"]


def test_development_demo_account_leaves_plans_and_versions_unchanged(client, settings):
    from backend.services.auth_service import ensure_development_demo_account

    _apply_operator_edits()
    plans_before = _plans()
    versions_before = _versions()
    with get_session_factory()() as db:
        user = ensure_development_demo_account(
            db, settings.model_copy(update={"enable_demo_account": True})
        )
        assert user is not None
        user_id = user.id
        db.commit()
    assert _plans() == plans_before
    assert _versions() == versions_before
    with _dba() as db:
        workspace_id = db.scalar(
            select(Membership.workspace_id).where(Membership.user_id == user_id)
        )
    [subscription] = _subscriptions(workspace_id)
    assert subscription["plan_version"] == plans_before["demo"]["current_version"]


def test_development_demo_account_path_keeps_dba_edited_rows(client, settings):
    from backend.services.auth_service import ensure_development_demo_account

    _apply_operator_edits()
    before = _plans()
    with get_session_factory()() as db:
        ensure_development_demo_account(
            db, settings.model_copy(update={"enable_demo_account": True})
        )
        db.commit()
    assert _plans() == before


@pytest.mark.parametrize("quota", [1800, 2400])
def test_first_startup_seeds_exactly_the_catalog_with_version_one(settings, quota):
    with _restarted(settings, demo_quota_seconds=quota):
        pass
    plans = _plans()
    assert sorted(plans) == sorted(CATALOG_KEYS)
    for key, row in plans.items():
        assert _terms(row) == _catalog_row(key, quota), key
        assert bool(row["is_active"]) is True
        assert row["display_json"] == {}
        assert row["current_version"] == 1
    versions = _versions()
    assert [(v["plan_key"], v["version"]) for v in versions] == [(k, 1) for k in CATALOG_KEYS]
    for version in versions:
        assert version["source"] == "catalog_seed"
        assert _terms(version) == _terms(plans[version["plan_key"]])
        assert version["created_at"] is not None


def test_first_startup_seeds_the_catalog_rows_with_code_defaults(settings):
    with _restarted(settings, demo_quota_seconds=2400):
        pass
    plans = _plans()
    assert sorted(plans) == sorted(CATALOG_KEYS)
    for key, row in plans.items():
        assert _terms(row) == _catalog_row(key, 2400), key
        assert bool(row["is_active"]) is True
        assert row["display_json"] == {}


@pytest.mark.parametrize("missing", ["business", "demo"])
def test_restart_recreates_only_a_deleted_catalog_key(client, settings, missing):
    _dba_execute(
        (
            "UPDATE plans SET entitlements_json = :e WHERE key = 'professional'",
            {"e": {"audio_seconds": 360000, "concurrent_jobs": 3}},
        ),
    )
    _dba_delete_plan(missing)
    before = _plans()
    versions_before = _versions()
    _restart(settings)
    after = _plans()
    assert sorted(after) == sorted(CATALOG_KEYS)
    assert {key: row for key, row in after.items() if key != missing} == before
    assert _terms(after[missing]) == _catalog_row(missing, settings.demo_quota_seconds)
    assert bool(after[missing]["is_active"]) is True
    assert after[missing]["display_json"] == {}
    assert after[missing]["current_version"] == 1
    [recreated] = _versions(missing)
    assert recreated["version"] == 1
    assert recreated["source"] == "catalog_seed"
    assert _terms(recreated) == _terms(after[missing])
    assert [v for v in _versions() if v["plan_key"] != missing] == versions_before


@pytest.mark.parametrize("missing", ["business", "demo"])
def test_restart_recreates_a_deleted_catalog_key_with_code_defaults(client, settings, missing):
    _dba_delete_plan(missing)
    _restart(settings)
    after = _plans()
    assert sorted(after) == sorted(CATALOG_KEYS)
    assert _terms(after[missing]) == _catalog_row(missing, settings.demo_quota_seconds)
    assert bool(after[missing]["is_active"]) is True


def _saas_models():
    from backend.database import saas_models

    return saas_models


def test_publish_appends_the_next_version_and_moves_the_row_to_it(client):
    _dba_execute(
        (
            "UPDATE plans SET is_active = false, display_json = :d WHERE key = 'professional'",
            {"d": {"badge": "x"}},
        ),
    )
    [first] = _versions("professional")
    terms = {"audio_seconds": 360000, "concurrent_jobs": 3}
    with _captured_logs() as records, get_session_factory()() as db:
        version = _tenancy().publish_plan_version(
            db,
            "professional",
            entitlements=terms,
            name="Professional Plus",
            is_public=False,
            source="operator",
        )
        assert isinstance(version, _saas_models().PlanVersion)
        assert (version.plan_key, version.version, version.source) == (
            "professional",
            2,
            "operator",
        )
        assert (version.name, version.is_public, version.entitlements_json) == (
            "Professional Plus",
            False,
            terms,
        )
        db.commit()
    row = _plans()["professional"]
    assert _terms(row) == ("Professional Plus", False, terms)
    assert row["current_version"] == 2
    assert bool(row["is_active"]) is False
    assert row["display_json"] == {"badge": "x"}
    versions = _versions("professional")
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0] == first
    assert versions[1]["source"] == "operator"
    assert _terms(versions[1]) == _terms(row)
    assert _published(records) == [("professional", 2, "operator")]


def test_publish_defaults_name_and_visibility_to_the_current_row(client):
    _apply_operator_edits()
    assert _publish("enterprise", entitlements={"audio_seconds": 1000000}) == 2
    assert _publish("business", entitlements={"concurrent_jobs": 5}) == 2
    plans = _plans()
    [_, enterprise] = _versions("enterprise")
    [_, business] = _versions("business")
    assert _terms(enterprise) == ("Enterprise (negotiated)", False, {"audio_seconds": 1000000})
    assert _terms(business) == ("Business", False, {"concurrent_jobs": 5})
    assert enterprise["source"] == business["source"] == "operator"
    assert _terms(plans["enterprise"]) == _terms(enterprise)
    assert _terms(plans["business"]) == _terms(business)


def test_publish_stores_the_complete_dict_and_numbers_consecutively(client):
    assert _publish("professional", entitlements={"audio_seconds": 10}) == 2
    assert _publish("professional", entitlements={"concurrent_jobs": 2}) == 3
    versions = _versions("professional")
    assert [v["version"] for v in versions] == [1, 2, 3]
    assert versions[2]["entitlements_json"] == {"concurrent_jobs": 2}
    row = _plans()["professional"]
    assert row["entitlements_json"] == {"concurrent_jobs": 2}
    assert row["current_version"] == 3


def test_publish_flushes_but_leaves_the_commit_to_the_caller(client):
    plans_before = _plans()
    versions_before = _versions()
    with get_session_factory()() as db:
        version = _tenancy().publish_plan_version(
            db, "professional", entitlements={"audio_seconds": 5}
        )
        assert version.version == 2
        visible = db.execute(
            text("SELECT count(*) FROM plan_versions WHERE plan_key = 'professional'")
        ).scalar_one()
        assert visible == 2
        assert _versions() == versions_before
        db.rollback()
    assert _plans() == plans_before
    assert _versions() == versions_before


_ORM_CHANGES: dict[str, Callable[[Any], None]] = {
    "name": lambda row: setattr(row, "name", "Rewritten"),
    "is_public": lambda row: setattr(row, "is_public", not row.is_public),
    "entitlements_json": lambda row: setattr(row, "entitlements_json", {"audio_seconds": 1}),
    "source": lambda row: setattr(row, "source", "operator"),
    "version": lambda row: setattr(row, "version", 7),
    "created_at": lambda row: setattr(row, "created_at", datetime(2020, 1, 1, tzinfo=UTC)),
}


@pytest.mark.parametrize("change", sorted(_ORM_CHANGES))
def test_orm_update_of_a_plan_version_is_refused(client, change):
    models = _saas_models()
    before = _versions()
    with get_session_factory()() as db:
        row = db.scalars(
            select(models.PlanVersion).where(models.PlanVersion.plan_key == "professional")
        ).one()
        _ORM_CHANGES[change](row)
        with pytest.raises(models.PlanVersionImmutableError):
            db.flush()
        db.rollback()
    assert _versions() == before


def test_orm_delete_of_a_plan_version_is_refused(client):
    models = _saas_models()
    before = _versions()
    with get_session_factory()() as db:
        row = db.scalars(
            select(models.PlanVersion).where(models.PlanVersion.plan_key == "enterprise")
        ).one()
        db.delete(row)
        with pytest.raises(models.PlanVersionImmutableError):
            db.flush()
        db.rollback()
    assert _versions() == before


def test_orm_read_of_plan_versions_and_a_publish_are_allowed(client):
    models = _saas_models()
    with get_session_factory()() as db:
        rows = db.scalars(select(models.PlanVersion)).all()
        assert sorted((row.plan_key, row.version) for row in rows) == [
            (key, 1) for key in CATALOG_KEYS
        ]
        db.flush()
        db.commit()
    assert _publish("professional", entitlements={"audio_seconds": 1}) == 2
    assert len(_versions()) == len(CATALOG_KEYS) + 1


@pytest.mark.postgres
def test_app_role_cannot_update_or_delete_plan_versions_but_can_read_and_insert(client):
    before = _versions()
    with get_session_factory()() as db:
        for statement in (
            "UPDATE plan_versions SET name = 'Rewritten'",
            "UPDATE plan_versions SET version = 9 WHERE plan_key = 'enterprise'",
            "DELETE FROM plan_versions",
            "DELETE FROM plan_versions WHERE plan_key = 'on_premise'",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                db.execute(text(statement))
            db.rollback()
        privileges = db.execute(
            text(
                "SELECT current_user, has_table_privilege('plan_versions', 'SELECT'), "
                "has_table_privilege('plan_versions', 'INSERT'), "
                "has_table_privilege('plan_versions', 'UPDATE'), "
                "has_table_privilege('plan_versions', 'DELETE')"
            )
        ).one()
        assert tuple(privileges) == ("homayar_app", True, True, False, False)
        assert db.execute(text("SELECT count(*) FROM plan_versions")).scalar_one() == len(
            CATALOG_KEYS
        )
        params = {"e": {"audio_seconds": 1}, "at": datetime.now(UTC)}
        db.execute(
            _clause(
                "INSERT INTO plan_versions (plan_key, version, name, is_public, "
                "entitlements_json, source, created_at) VALUES "
                "('professional', 2, 'Professional', true, :e, 'operator', :at)",
                params,
            ),
            params,
        )
        assert (
            db.execute(text("SELECT count(*) FROM plan_versions")).scalar_one()
            == len(CATALOG_KEYS) + 1
        )
        db.rollback()
    assert _versions() == before


_VALID_TERMS = {"audio_seconds": 1}
_INVALID_PUBLISHES: dict[str, tuple[str, dict[str, Any]]] = {
    "unknown-plan": ("gold", {"entitlements": _VALID_TERMS}),
    "unknown-plan-case": ("Professional", {"entitlements": _VALID_TERMS}),
    "entitlements-list": ("professional", {"entitlements": [["audio_seconds", 1]]}),
    "entitlements-none": ("professional", {"entitlements": None}),
    "entitlements-json-text": ("professional", {"entitlements": '{"audio_seconds": 1}'}),
    "key-uppercase": ("professional", {"entitlements": {"Audio": 1}}),
    "key-hyphen": ("professional", {"entitlements": {"audio-seconds": 1}}),
    "key-space": ("professional", {"entitlements": {"audio seconds": 1}}),
    "key-leading-digit": ("professional", {"entitlements": {"1audio": 1}}),
    "key-leading-underscore": ("professional", {"entitlements": {"_audio": 1}}),
    "key-empty": ("professional", {"entitlements": {"": 1}}),
    "key-61-chars": ("professional", {"entitlements": {"a" * 61: 1}}),
    "key-non-ascii": ("professional", {"entitlements": {"audiö": 1}}),
    "key-not-a-string": ("professional", {"entitlements": {1: 1}}),
    "value-true": ("professional", {"entitlements": {"audio_seconds": True}}),
    "value-false": ("professional", {"entitlements": {"audio_seconds": False}}),
    "value-float": ("professional", {"entitlements": {"audio_seconds": 1.5}}),
    "value-integral-float": ("professional", {"entitlements": {"audio_seconds": 10.0}}),
    "value-str": ("professional", {"entitlements": {"audio_seconds": "10"}}),
    "value-negative": ("professional", {"entitlements": {"audio_seconds": -1}}),
    "value-over-int32": ("professional", {"entitlements": {"audio_seconds": INT32_MAX + 1}}),
    "value-dict": ("professional", {"entitlements": {"audio_seconds": {"limit": 5}}}),
    "value-list": ("professional", {"entitlements": {"audio_seconds": [5]}}),
    "second-value-bad": (
        "professional",
        {"entitlements": {"audio_seconds": 5, "concurrent_jobs": -2}},
    ),
    "name-empty": ("professional", {"entitlements": _VALID_TERMS, "name": ""}),
    "name-blank": ("professional", {"entitlements": _VALID_TERMS, "name": "   "}),
    "name-whitespace": ("professional", {"entitlements": _VALID_TERMS, "name": "\t\n"}),
    "name-101-chars": ("professional", {"entitlements": _VALID_TERMS, "name": "x" * 101}),
    "source-catalog-seed": (
        "professional",
        {"entitlements": _VALID_TERMS, "source": "catalog_seed"},
    ),
    "source-legacy-snapshot": (
        "professional",
        {"entitlements": _VALID_TERMS, "source": "legacy_snapshot"},
    ),
    "source-uppercase": ("professional", {"entitlements": _VALID_TERMS, "source": "Operator"}),
    "source-unknown": ("professional", {"entitlements": _VALID_TERMS, "source": "admin"}),
    "source-empty": ("professional", {"entitlements": _VALID_TERMS, "source": ""}),
}


@pytest.mark.parametrize("case", list(_INVALID_PUBLISHES))
def test_publish_refuses_invalid_input_and_writes_nothing(client, case):
    plan_key, kwargs = _INVALID_PUBLISHES[case]
    plans_before = _plans()
    versions_before = _versions()
    error = _plan_catalog_error()
    assert issubclass(error, ValueError)
    with get_session_factory()() as db:
        with pytest.raises(error):
            _tenancy().publish_plan_version(db, plan_key, **kwargs)
        db.commit()
    assert _plans() == plans_before
    assert _versions() == versions_before


_VALID_PUBLISHES: dict[str, tuple[dict[str, Any], tuple[str, bool, dict], str]] = {
    "zero-and-unlimited": (
        {"entitlements": {"audio_seconds": 0, "concurrent_jobs": None}},
        ("Professional", True, {"audio_seconds": 0, "concurrent_jobs": None}),
        "operator",
    ),
    "int32-max": (
        {"entitlements": {"audio_seconds": INT32_MAX}},
        ("Professional", True, {"audio_seconds": INT32_MAX}),
        "operator",
    ),
    "empty-dict": ({"entitlements": {}}, ("Professional", True, {}), "operator"),
    "60-char-key": (
        {"entitlements": {"a" * 60: 1, "k9_x": 2}},
        ("Professional", True, {"a" * 60: 1, "k9_x": 2}),
        "operator",
    ),
    "100-char-name": (
        {"entitlements": {"seats": 3}, "name": "x" * 100},
        ("x" * 100, True, {"seats": 3}),
        "operator",
    ),
    "persian-name-private": (
        {
            "entitlements": {"seats": 3},
            "name": "پلن حرفه‌ای",
            "is_public": False,
        },
        ("پلن حرفه‌ای", False, {"seats": 3}),
        "operator",
    ),
    "source-config": (
        {"entitlements": {"audio_seconds": 7}, "source": "config"},
        ("Professional", True, {"audio_seconds": 7}),
        "config",
    ),
    "source-explicit-operator": (
        {"entitlements": {"audio_seconds": 8}, "source": "operator"},
        ("Professional", True, {"audio_seconds": 8}),
        "operator",
    ),
}


@pytest.mark.parametrize("case", list(_VALID_PUBLISHES))
def test_publish_accepts_valid_input(client, case):
    kwargs, expected, source = _VALID_PUBLISHES[case]
    assert _publish("professional", **kwargs) == 2
    [_, version] = _versions("professional")
    row = _plans()["professional"]
    assert _terms(version) == expected
    assert version["source"] == source
    assert _terms(row) == expected
    assert row["current_version"] == 2


_DEMO_OPERATOR_REFUSALS: dict[str, dict[str, Any]] = {
    "raised": {"entitlements": {"audio_seconds": 3600, "concurrent_jobs": 1, "seats": 1}},
    "lowered": {"entitlements": {"audio_seconds": 1799, "concurrent_jobs": 2, "seats": 1}},
    "omitted": {"entitlements": {"concurrent_jobs": 1, "seats": 1}},
    "unlimited": {"entitlements": {"audio_seconds": None, "concurrent_jobs": 1, "seats": 1}},
    "explicit-operator": {
        "entitlements": {"audio_seconds": 3600, "concurrent_jobs": 1, "seats": 1},
        "source": "operator",
    },
}


@pytest.mark.parametrize("case", list(_DEMO_OPERATOR_REFUSALS))
def test_operator_publish_cannot_change_the_demo_allowance(client, case):
    plans_before = _plans()
    versions_before = _versions()
    error = _plan_catalog_error()
    with get_session_factory()() as db:
        with pytest.raises(error) as raised:
            _tenancy().publish_plan_version(db, "demo", **_DEMO_OPERATOR_REFUSALS[case])
        db.commit()
    assert "DEMO_QUOTA_SECONDS" in str(raised.value)
    assert _plans() == plans_before
    assert _versions() == versions_before


def test_operator_publish_of_demo_concurrency_succeeds_and_survives_restarts(client, settings):
    terms = {"audio_seconds": 1800, "concurrent_jobs": 2, "seats": 1}
    assert _publish("demo", entitlements=terms, source="operator") == 2
    plans = _plans()
    versions = _versions()
    assert plans["demo"]["entitlements_json"] == terms
    assert plans["demo"]["current_version"] == 2
    _restart(settings)
    _restart(settings)
    assert _plans() == plans
    assert _versions() == versions


@pytest.mark.parametrize("quota", [3600, 600])
def test_changed_demo_quota_appends_one_config_version_on_restart(client, settings, quota):
    assert (
        _publish("demo", entitlements={"audio_seconds": 1800, "concurrent_jobs": 2, "seats": 1})
        == 2
    )
    before = _plans()
    versions_before = _versions()
    _restart(settings, demo_quota_seconds=quota)
    after = _plans()
    versions_after = _versions()
    new = [v for v in versions_after if v not in versions_before]
    assert [v for v in versions_after if v in versions_before] == versions_before
    assert len(new) == 1
    [config] = new
    assert (config["plan_key"], config["version"], config["source"]) == ("demo", 3, "config")
    assert _terms(config) == (
        "Free Demo",
        True,
        {"audio_seconds": quota, "concurrent_jobs": 2, "seats": 1},
    )
    assert _terms(after["demo"]) == _terms(config)
    assert after["demo"]["current_version"] == 3
    assert after["demo"]["is_active"] == before["demo"]["is_active"]
    assert after["demo"]["display_json"] == before["demo"]["display_json"]
    assert {k: v for k, v in after.items() if k != "demo"} == {
        k: v for k, v in before.items() if k != "demo"
    }
    _restart(settings, demo_quota_seconds=quota)
    assert _plans() == after
    assert _versions() == versions_after


def test_config_version_is_logged_and_no_drift_is_reported(client, settings):
    with _captured_logs() as records:
        _restart(settings, demo_quota_seconds=3600)
    assert _published(records) == [("demo", 2, "config")]
    assert _drift(records) == {}


def test_config_version_carries_the_row_as_it_was_before_the_restart(client, settings):
    _dba_execute(
        (
            "UPDATE plans SET name = :n, display_json = :d, entitlements_json = :e, "
            "is_active = false WHERE key = 'demo'",
            {
                "n": "Free Demo (ops)",
                "d": {"badge": "d"},
                "e": {"audio_seconds": 1800, "concurrent_jobs": 2, "seats": 4},
            },
        ),
    )
    _restart(settings, demo_quota_seconds=3600)
    [_, config] = _versions("demo")
    assert (config["version"], config["source"]) == (2, "config")
    assert _terms(config) == (
        "Free Demo (ops)",
        True,
        {"audio_seconds": 3600, "concurrent_jobs": 2, "seats": 4},
    )
    row = _plans()["demo"]
    assert _terms(row) == _terms(config)
    assert row["current_version"] == 2
    assert bool(row["is_active"]) is False
    assert row["display_json"] == {"badge": "d"}


def test_changed_demo_quota_restart_updates_the_demo_allowance(client, settings):
    _restart(settings, demo_quota_seconds=3600)
    row = _plans()["demo"]
    assert row["entitlements_json"]["audio_seconds"] == 3600
    assert row["entitlements_json"]["concurrent_jobs"] == 1
    assert row["name"] == "Free Demo"


def test_default_signup_keeps_the_tenant_visible_demo_allowance(client, email_outbox):
    tenant = _signup(client, email_outbox, "ac6-default@example.com")
    limits = _limits(tenant["workspace_id"])
    assert sorted(limits) == ["audio_seconds", "concurrent_jobs"]
    assert limits["audio_seconds"]["limit_value"] == 1800
    assert limits["concurrent_jobs"]["limit_value"] == 1
    assert {row["source"] for row in limits.values()} == {"demo"}
    [subscription] = _subscriptions(tenant["workspace_id"])
    assert (subscription["plan_key"], subscription["provider"]) == ("demo", "internal")
    usage = _owner_get(client, tenant, "/api/v1/billing/usage")
    assert usage.status_code == 200, usage.text
    remaining = {item["metric"]: item["remaining"] for item in usage.json()["items"]}
    assert remaining == {"audio_seconds": 1800, "concurrent_jobs": 1}


def test_signup_pins_the_subscription_to_the_current_demo_version(client, email_outbox):
    tenant = _signup(client, email_outbox, "ac6-pin@example.com")
    [subscription] = _subscriptions(tenant["workspace_id"])
    assert subscription["plan_key"] == "demo"
    assert subscription["plan_version"] == _plans()["demo"]["current_version"] == 1


def test_signup_after_a_demo_publish_gets_the_new_limits_and_pin(client, email_outbox):
    earlier = _signup(client, email_outbox, "ac6-earlier@example.com")
    earlier_limits = _limits(earlier["workspace_id"])
    earlier_subscriptions = _subscriptions(earlier["workspace_id"])
    assert (
        _publish(
            "demo",
            entitlements={"audio_seconds": 1800, "concurrent_jobs": 2, "seats": 1},
            source="operator",
        )
        == 2
    )
    later = _signup(client, email_outbox, "ac6-later@example.com")
    [later_subscription] = _subscriptions(later["workspace_id"])
    assert later_subscription["plan_version"] == 2
    later_limits = _limits(later["workspace_id"])
    assert later_limits["concurrent_jobs"]["limit_value"] == 2
    assert later_limits["audio_seconds"]["limit_value"] == 1800
    assert _subscriptions(earlier["workspace_id"]) == earlier_subscriptions
    assert earlier_subscriptions[0]["plan_version"] == 1
    assert _limits(earlier["workspace_id"]) == earlier_limits
    assert earlier_limits["concurrent_jobs"]["limit_value"] == 1


def test_signup_maps_a_null_limit_to_unlimited(client, email_outbox):
    assert (
        _publish("demo", entitlements={"audio_seconds": 1800, "concurrent_jobs": None, "seats": 1})
        == 2
    )
    tenant = _signup(client, email_outbox, "ac6-unlimited@example.com")
    limits = _limits(tenant["workspace_id"])
    assert sorted(limits) == ["audio_seconds", "concurrent_jobs"]
    assert limits["concurrent_jobs"]["limit_value"] is None
    assert limits["concurrent_jobs"]["source"] == "demo"
    usage = _owner_get(client, tenant, "/api/v1/billing/usage")
    assert usage.status_code == 200, usage.text
    remaining = {item["metric"]: item["remaining"] for item in usage.json()["items"]}
    assert remaining == {"audio_seconds": 1800, "concurrent_jobs": None}


def test_signup_creates_no_row_for_an_absent_metered_or_an_unmetered_key(client, email_outbox):
    assert _publish("demo", entitlements={"audio_seconds": 1800, "seats": 5, "exports": 3}) == 2
    tenant = _signup(client, email_outbox, "ac6-absent@example.com")
    limits = _limits(tenant["workspace_id"])
    assert sorted(limits) == ["audio_seconds"]
    assert limits["audio_seconds"]["limit_value"] == 1800
    assert limits["audio_seconds"]["source"] == "demo"
    [subscription] = _subscriptions(tenant["workspace_id"])
    assert subscription["plan_version"] == 2


def test_signup_grants_the_pinned_version_not_a_drifted_row(client, email_outbox):
    _dba_execute(
        (
            "UPDATE plans SET entitlements_json = :e WHERE key = 'demo'",
            {"e": {"audio_seconds": 1800, "concurrent_jobs": 5, "seats": 1}},
        ),
    )
    tenant = _signup(client, email_outbox, "ac6-drift@example.com")
    [subscription] = _subscriptions(tenant["workspace_id"])
    assert subscription["plan_version"] == 1
    assert _limits(tenant["workspace_id"])["concurrent_jobs"]["limit_value"] == 1


def test_signup_after_a_config_version_gets_the_new_quota_and_pin(client, email_outbox, settings):
    earlier = _signup(client, email_outbox, "ac6-config-earlier@example.com")
    earlier_limits = _limits(earlier["workspace_id"])
    with _restarted(settings, demo_quota_seconds=3600) as api:
        later = _signup(api, email_outbox, "ac6-config-later@example.com")
    [later_subscription] = _subscriptions(later["workspace_id"])
    assert later_subscription["plan_version"] == _plans()["demo"]["current_version"] == 2
    assert _limits(later["workspace_id"])["audio_seconds"]["limit_value"] == 3600
    assert _limits(earlier["workspace_id"]) == earlier_limits
    assert earlier_limits["audio_seconds"]["limit_value"] == 1800


def test_signup_with_the_free_demo_disabled_creates_no_subscription(client, email_outbox, settings):
    with _restarted(settings, enable_free_demo=False) as api:
        tenant = _signup(api, email_outbox, "ac6-no-demo@example.com")
    assert _subscriptions(tenant["workspace_id"]) == []
    assert _limits(tenant["workspace_id"]) == {}


def _two_tenants_with_a_negotiated_limit(client, email_outbox) -> tuple[dict, dict]:
    first = _signup(client, email_outbox, "ac7-first@example.com")
    second = _signup(client, email_outbox, "ac7-second@example.com")
    _dba_execute(
        (
            "UPDATE entitlements SET limit_value = 5000 "
            "WHERE workspace_id = :w AND metric = 'audio_seconds'",
            {"w": first["workspace_id"]},
        ),
    )
    return first, second


def test_tenant_rows_survive_a_restart(client, email_outbox, settings):
    _two_tenants_with_a_negotiated_limit(client, email_outbox)
    subscriptions = _rows("subscriptions")
    entitlements = _rows("entitlements")
    assert len(subscriptions) == 2
    assert 5000 in {row["limit_value"] for row in entitlements}
    _restart(settings)
    assert _rows("subscriptions") == subscriptions
    assert _rows("entitlements") == entitlements


def test_tenant_rows_survive_a_publish_and_a_config_version(client, email_outbox, settings):
    _two_tenants_with_a_negotiated_limit(client, email_outbox)
    subscriptions = _rows("subscriptions")
    entitlements = _rows("entitlements")
    assert (
        _publish("demo", entitlements={"audio_seconds": 1800, "concurrent_jobs": 2, "seats": 1})
        == 2
    )
    assert _rows("subscriptions") == subscriptions
    assert _rows("entitlements") == entitlements
    _restart(settings, demo_quota_seconds=3600)
    assert [v["source"] for v in _versions("demo")] == ["catalog_seed", "operator", "config"]
    assert _rows("subscriptions") == subscriptions
    assert _rows("entitlements") == entitlements
    assert {row["plan_version"] for row in subscriptions} == {1}


def _race(*works: Callable[[Any], Any]) -> list[Any]:
    barrier = threading.Barrier(len(works), timeout=30)
    results: list[Any] = [None] * len(works)
    errors: list[BaseException] = []
    factory = get_session_factory()

    def run(index: int, work: Callable[[Any], Any]) -> None:
        try:
            with factory() as db:
                db.connection()
                barrier.wait()
                results[index] = work(db)
                db.commit()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i, w)) for i, w in enumerate(works)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    return results


@pytest.mark.postgres
def test_concurrent_first_seeding_converges_on_one_catalog(client, settings):
    def seed(db):
        return _tenancy().ensure_plan_catalog(db, settings)

    for _ in range(3):
        _dba_delete_all_plans()
        _race(seed, seed)
        plans = _plans()
        assert sorted(plans) == sorted(CATALOG_KEYS)
        assert {row["current_version"] for row in plans.values()} == {1}
        assert [(v["plan_key"], v["version"], v["source"]) for v in _versions()] == [
            (key, 1, "catalog_seed") for key in CATALOG_KEYS
        ]


@pytest.mark.postgres
def test_concurrent_restarts_with_a_changed_demo_quota_append_one_version(client, settings):
    for number, quota in enumerate((3600, 5400, 7200), start=2):
        changed = settings.model_copy(update={"demo_quota_seconds": quota})

        def seed(db, runtime=changed):
            return _tenancy().ensure_plan_catalog(db, runtime)

        _race(seed, seed)
        versions = _versions("demo")
        assert [v["version"] for v in versions] == list(range(1, number + 1))
        assert versions[-1]["source"] == "config"
        assert versions[-1]["entitlements_json"]["audio_seconds"] == quota
        row = _plans()["demo"]
        assert row["current_version"] == number
        assert _terms(row) == _terms(versions[-1])
        assert len(_versions()) == len(CATALOG_KEYS) + number - 1


@pytest.mark.postgres
def test_concurrent_publishes_get_consecutive_versions(client):
    for attempt in range(3):
        start = _plans()["professional"]["current_version"]
        first = {"audio_seconds": 100 + attempt}
        second = {"audio_seconds": 200 + attempt}

        def publish_first(db, terms=first):
            return _tenancy().publish_plan_version(db, "professional", entitlements=terms).version

        def publish_second(db, terms=second):
            return _tenancy().publish_plan_version(db, "professional", entitlements=terms).version

        numbers = _race(publish_first, publish_second)
        assert sorted(numbers) == [start + 1, start + 2]
        by_number = dict(zip(numbers, (first, second), strict=True))
        versions = {v["version"]: v for v in _versions("professional")}
        assert sorted(versions) == list(range(1, start + 3))
        for number, terms in by_number.items():
            assert versions[number]["entitlements_json"] == terms
        row = _plans()["professional"]
        assert row["current_version"] == start + 2
        assert row["entitlements_json"] == by_number[start + 2]


LEGACY_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
LEGACY_PLANS: dict[str, tuple[str, bool, bool, dict, dict]] = {
    "demo": (
        "Free Demo",
        True,
        True,
        {"audio_seconds": 1800, "concurrent_jobs": 1, "seats": 1},
        {},
    ),
    "professional": (
        "Professional",
        True,
        True,
        {"audio_seconds": 360000, "concurrent_jobs": 3},
        {"badge": "x"},
    ),
    "business": ("Business", False, True, {}, {}),
    "enterprise": (
        "Enterprise (negotiated)",
        False,
        True,
        {"audio_seconds": 900000, "seats": 50},
        {},
    ),
    "on_premise": ("On-Premise", False, False, {}, {}),
    "acme_custom": ("Acme Custom", False, True, {"audio_seconds": 42}, {}),
}
NEW_COLUMNS = frozenset({"current_version", "plan_version"})
MIGRATION_PROBE_ROLE = "homayar_wp18_owner_probe"
MIGRATION_PROBE_PASSWORD = "wp18-owner-probe-only"  # noqa: S105


def _alembic_config():
    from alembic.config import Config

    return Config(str(REPO_ROOT / "alembic.ini"))


def _script():
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_alembic_config())


def _head_revision():
    script = _script()
    heads = script.get_heads()
    assert len(heads) == 1, heads
    return script.get_revision(heads[0])


def _alembic(monkeypatch, url: str, action: str, target: str) -> None:
    from alembic import command

    from backend.config import reset_settings_cache

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    reset_settings_cache()
    try:
        getattr(command, action)(_alembic_config(), target)
    finally:
        reset_settings_cache()


def _alembic_check(monkeypatch, url: str) -> None:
    from alembic import command
    from alembic.util.exc import AutogenerateDiffsDetected

    from backend.config import reset_settings_cache

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    try:
        command.check(_alembic_config())
    except AutogenerateDiffsDetected as exc:
        pytest.fail(f"migrations do not match the models: {exc}")
    finally:
        reset_settings_cache()


def _pre_existing(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in NEW_COLUMNS and k != "_xmin"}


def _table_columns(table: str) -> set[str]:
    with _dba() as db:
        return set(
            db.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :t"
                ),
                {"t": table},
            ).scalars()
        )


def _constraint_definition(name: str) -> str | None:
    with _dba() as db:
        return db.execute(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"),
            {"n": name},
        ).scalar()


def _seed_legacy_deployment() -> dict[str, str]:
    from backend.database.saas_models import Organization, Workspace

    ids = {"demo_ws": str(uuid4()), "custom_ws": str(uuid4())}
    with _dba() as db:
        for key, (name, is_public, is_active, entitlements, display) in LEGACY_PLANS.items():
            params = {
                "k": key,
                "n": name,
                "p": is_public,
                "a": is_active,
                "e": entitlements,
                "d": display,
                "t": LEGACY_AT,
            }
            db.execute(
                _clause(
                    "INSERT INTO plans (key, name, is_public, is_active, entitlements_json, "
                    "display_json, created_at, updated_at) "
                    "VALUES (:k, :n, :p, :a, :e, :d, :t, :t)",
                    params,
                ),
                params,
            )
        for label, workspace_id in ids.items():
            organization_id = str(uuid4())
            db.add(
                Organization(
                    id=organization_id,
                    name=f"Legacy {label}",
                    slug=f"legacy-{label.replace('_', '-')}-{workspace_id[:6]}",
                    status="active",
                )
            )
            db.flush()
            db.add(
                Workspace(
                    id=workspace_id,
                    organization_id=organization_id,
                    name=f"Legacy {label}",
                    slug="default",
                    status="active",
                )
            )
            db.flush()
        for workspace_id, plan_key, status, provider in (
            (ids["demo_ws"], "demo", "TRIALING", "internal"),
            (ids["custom_ws"], "acme_custom", "ACTIVE", "manual"),
        ):
            db.execute(
                text(
                    "INSERT INTO subscriptions (id, workspace_id, plan_key, status, provider, "
                    "starts_at, created_at, updated_at) "
                    "VALUES (:id, :w, :p, :s, :v, :t, :t, :t)"
                ),
                {
                    "id": str(uuid4()),
                    "w": workspace_id,
                    "p": plan_key,
                    "s": status,
                    "v": provider,
                    "t": LEGACY_AT,
                },
            )
        for workspace_id, metric, limit in (
            (ids["demo_ws"], "audio_seconds", 5000),
            (ids["demo_ws"], "concurrent_jobs", 1),
            (ids["custom_ws"], "audio_seconds", 42),
        ):
            db.execute(
                text(
                    "INSERT INTO entitlements (id, workspace_id, metric, source, limit_value, "
                    "used_value, reserved_value, period_start, created_at, updated_at) "
                    "VALUES (:id, :w, :m, 'demo', :l, 0, 0, :t, :t, :t)"
                ),
                {"id": str(uuid4()), "w": workspace_id, "m": metric, "l": limit, "t": LEGACY_AT},
            )
        db.commit()
    return ids


def _assert_legacy_snapshot_state(
    plans_before: dict, subscriptions_before: list, entitlements_before: list
) -> None:
    plans = _plans()
    assert sorted(plans) == sorted(plans_before)
    for key, row in plans.items():
        assert _pre_existing(row) == plans_before[key], key
        assert row["current_version"] == 1, key
    versions = _versions()
    assert [(v["plan_key"], v["version"]) for v in versions] == [(k, 1) for k in sorted(plans)]
    for version in versions:
        assert version["source"] == "legacy_snapshot"
        assert _terms(version) == _terms(plans[version["plan_key"]])
    subscriptions = _rows("subscriptions")
    assert [_pre_existing(row) for row in subscriptions] == subscriptions_before
    assert {row["plan_version"] for row in subscriptions} == {1}
    assert [_pre_existing(row) for row in _rows("entitlements")] == entitlements_before


def _assert_upgrade_installed_constraints_and_grants() -> None:
    definition = _constraint_definition("fk_subscription_plan_version")
    assert definition is not None
    normalized = re.sub(r"\s+", " ", definition)
    assert "FOREIGN KEY (plan_key, plan_version) REFERENCES plan_versions(plan_key, version)" in (
        normalized
    )
    with _dba() as db:
        column = db.execute(
            text(
                "SELECT column_default, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'subscriptions' "
                "AND column_name = 'plan_version'"
            )
        ).one()
        assert tuple(column) == (None, "NO")
        nullable = db.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'plans' "
                "AND column_name = 'current_version'"
            )
        ).scalar_one()
        assert nullable == "NO"
        privileges = db.execute(
            text(
                "SELECT has_table_privilege('homayar_app', 'plan_versions', 'SELECT'), "
                "has_table_privilege('homayar_app', 'plan_versions', 'INSERT'), "
                "has_table_privilege('homayar_app', 'plan_versions', 'UPDATE'), "
                "has_table_privilege('homayar_app', 'plan_versions', 'DELETE')"
            )
        ).one()
        assert tuple(privileges) == (True, True, False, False)
        security = dict(
            (name, (enabled, forced))
            for name, enabled, forced in db.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relnamespace = 'public'::regnamespace "
                    "AND relname IN ('plan_versions', 'plans', 'subscriptions', 'entitlements')"
                )
            )
        )
        assert security == {
            "plan_versions": (False, False),
            "plans": (False, False),
            "subscriptions": (True, True),
            "entitlements": (True, True),
        }


@pytest.mark.postgres
def test_upgrade_snapshots_an_existing_deployment_losslessly(settings, monkeypatch):
    from sqlalchemy import create_engine

    from backend.tests._database import reset_postgres_schema

    reset_postgres_schema(settings.database_url)
    try:
        _alembic(monkeypatch, settings.database_url, "upgrade", BASE_HEAD)
        _seed_legacy_deployment()
        plans_before = {key: _pre_existing(row) for key, row in _plans().items()}
        subscriptions_before = _rows("subscriptions")
        entitlements_before = _rows("entitlements")

        _alembic(monkeypatch, settings.database_url, "upgrade", "head")

        _assert_legacy_snapshot_state(
            plans_before,
            [_pre_existing(row) for row in subscriptions_before],
            [_pre_existing(row) for row in entitlements_before],
        )
        assert [row["_xmin"] for row in _rows("subscriptions")] == [
            row["_xmin"] for row in subscriptions_before
        ]
        assert _rows("entitlements") == entitlements_before
        _assert_upgrade_installed_constraints_and_grants()

        plans_migrated = _plans()
        versions_migrated = _versions()
        with _captured_logs() as records:
            _restart(settings, auto_create_schema=False)
        assert _plans() == plans_migrated
        assert _versions() == versions_migrated
        assert _drift(records) == {}
        assert _published(records) == []

        backfill = _head_revision().module.backfill_plan_versions
        engine = create_engine(settings.database_url)
        try:
            with engine.begin() as connection:
                assert backfill(connection) == 0
            assert _versions() == versions_migrated
            params = {"e": {"audio_seconds": 7}, "d": {}, "t": LEGACY_AT}
            _dba_execute(
                (
                    "INSERT INTO plans (key, name, is_public, is_active, entitlements_json, "
                    "display_json, current_version, created_at, updated_at) VALUES "
                    "('acme_second', 'Acme Second', false, true, :e, :d, 1, :t, :t)",
                    params,
                ),
            )
            with engine.begin() as connection:
                assert backfill(connection) == 1
            [added] = _versions("acme_second")
            assert (added["version"], added["source"]) == (1, "legacy_snapshot")
            assert _terms(added) == ("Acme Second", False, {"audio_seconds": 7})
            with engine.begin() as connection:
                assert backfill(connection) == 0
            assert len(_versions()) == len(versions_migrated) + 1
        finally:
            engine.dispose()
        _alembic_check(monkeypatch, settings.database_url)
    finally:
        reset_postgres_schema(settings.database_url)


def _probe_url(settings: Settings) -> str:
    from sqlalchemy.engine import make_url

    url = make_url(settings.database_url).set(
        username=MIGRATION_PROBE_ROLE, password=MIGRATION_PROBE_PASSWORD
    )
    return url.render_as_string(hide_password=False)


def _drop_probe_role() -> None:
    with _dba() as db:
        exists = db.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": MIGRATION_PROBE_ROLE}
        ).scalar()
        if exists:
            db.execute(text(f"DROP OWNED BY {MIGRATION_PROBE_ROLE} CASCADE"))
            db.execute(text(f"DROP ROLE {MIGRATION_PROBE_ROLE}"))
        db.commit()


def _hand_every_relation_to_the_probe() -> None:
    kinds = {"r": "TABLE", "p": "TABLE", "v": "VIEW", "m": "MATERIALIZED VIEW", "S": "SEQUENCE"}
    with _dba() as db:
        relations = db.execute(
            text(
                "SELECT c.relname, c.relkind FROM pg_class c "
                "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN "
                "('r', 'p', 'v', 'm', 'S') AND NOT EXISTS (SELECT 1 FROM pg_depend d "
                "WHERE d.objid = c.oid AND d.deptype IN ('a', 'i') AND c.relkind = 'S')"
            )
        ).all()
        for name, kind in relations:
            db.execute(text(f'ALTER {kinds[kind]} "{name}" OWNER TO {MIGRATION_PROBE_ROLE}'))
        db.commit()


@pytest.mark.postgres
def test_upgrade_behaves_the_same_for_a_plain_table_owner(settings, monkeypatch):
    from backend.database.rls import APP_ROLE
    from backend.tests._database import reset_postgres_schema

    reset_postgres_schema(settings.database_url)
    _drop_probe_role()
    try:
        _alembic(monkeypatch, settings.database_url, "upgrade", BASE_HEAD)
        _seed_legacy_deployment()
        _dba_execute(
            (
                f"CREATE ROLE {MIGRATION_PROBE_ROLE} LOGIN PASSWORD '{MIGRATION_PROBE_PASSWORD}' "
                f"NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE IN ROLE {APP_ROLE}",
                {},
            ),
            (f"GRANT USAGE, CREATE ON SCHEMA public TO {MIGRATION_PROBE_ROLE}", {}),
        )
        _hand_every_relation_to_the_probe()
        plans_before = {key: _pre_existing(row) for key, row in _plans().items()}
        subscriptions_before = _rows("subscriptions")
        entitlements_before = _rows("entitlements")

        _alembic(monkeypatch, _probe_url(settings), "upgrade", "head")

        _assert_legacy_snapshot_state(
            plans_before,
            [_pre_existing(row) for row in subscriptions_before],
            [_pre_existing(row) for row in entitlements_before],
        )
        assert [row["_xmin"] for row in _rows("subscriptions")] == [
            row["_xmin"] for row in subscriptions_before
        ]
        _assert_upgrade_installed_constraints_and_grants()
    finally:
        monkeypatch.setenv("DATABASE_URL", settings.database_url)
        reset_postgres_schema(settings.database_url)
        _drop_probe_role()


@pytest.mark.postgres
def test_downgrade_drops_only_history_and_pins_and_upgrade_restores(
    client, email_outbox, settings, monkeypatch
):
    tenant = _signup(client, email_outbox, "ac10-tenant@example.com")
    professional = {"audio_seconds": 360000, "concurrent_jobs": 3}
    assert _publish("professional", entitlements=professional, name="Professional Plus") == 2
    _dba_execute(
        (
            "INSERT INTO subscriptions (id, workspace_id, plan_key, plan_version, status, "
            "provider, starts_at, created_at, updated_at) VALUES "
            "(:id, :w, 'professional', 2, 'ACTIVE', 'manual', :t, :t, :t)",
            {"id": str(uuid4()), "w": tenant["workspace_id"], "t": LEGACY_AT},
        ),
    )
    assert sorted(row["plan_version"] for row in _rows("subscriptions")) == [1, 2]
    plans_before = {key: _pre_existing(row) for key, row in _plans().items()}
    subscriptions_before = [_pre_existing(row) for row in _rows("subscriptions")]
    entitlements_before = [_pre_existing(row) for row in _rows("entitlements")]
    head = _head_revision()
    assert head.down_revision == BASE_HEAD

    _alembic(monkeypatch, settings.database_url, "stamp", "head")
    _alembic(monkeypatch, settings.database_url, "downgrade", BASE_HEAD)

    assert not _table_exists("plan_versions")
    assert "current_version" not in _table_columns("plans")
    assert "plan_version" not in _table_columns("subscriptions")
    assert _constraint_definition("fk_subscription_plan_version") is None
    assert {key: _pre_existing(row) for key, row in _plans().items()} == plans_before
    assert _terms(_plans()["professional"]) == ("Professional Plus", True, professional)
    assert [_pre_existing(row) for row in _rows("subscriptions")] == subscriptions_before
    assert [_pre_existing(row) for row in _rows("entitlements")] == entitlements_before

    _alembic(monkeypatch, settings.database_url, "upgrade", "head")

    _assert_legacy_snapshot_state(plans_before, subscriptions_before, entitlements_before)
    [professional_version] = _versions("professional")
    assert _terms(professional_version) == ("Professional Plus", True, professional)
    _assert_upgrade_installed_constraints_and_grants()
    _alembic_check(monkeypatch, settings.database_url)


def test_exactly_one_new_revision_follows_the_base_head():
    script = _script()
    heads = script.get_heads()
    assert len(heads) == 1
    head = script.get_revision(heads[0])
    assert head.down_revision == BASE_HEAD
    children = [rev.revision for rev in script.walk_revisions() if rev.down_revision == BASE_HEAD]
    assert children == [head.revision]
    versions_dir = REPO_ROOT / "backend" / "migrations" / "versions"
    files = sorted(versions_dir.glob("*.py"))
    assert len(files) == 32
    assert callable(getattr(head.module, "backfill_plan_versions", None))


def test_head_revision_docstring_states_what_downgrade_drops():
    head = _head_revision()
    assert head.down_revision == BASE_HEAD
    doc = head.module.__doc__ or ""
    assert re.search(r"downgrad", doc, re.IGNORECASE)
    assert re.search(r"plan_versions|history", doc, re.IGNORECASE)


def test_existing_revision_files_are_byte_identical_to_base():
    versions_dir = REPO_ROOT / "backend" / "migrations" / "versions"
    added = {
        Path(rev.path).resolve()
        for rev in _script().walk_revisions()
        if rev.down_revision == BASE_HEAD
    }
    digest = hashlib.sha256()
    for path in sorted(versions_dir.glob("*.py")):
        if path.resolve() in added:
            continue
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode())
        digest.update(b"\n")
    assert digest.hexdigest() == (
        "ed33b6d517652e7d9f66211541abe6adcf0288b5c1d578d7ffef3dc3dffd5805"
    )


def test_plan_versions_is_classified_as_a_global_append_only_table():
    from backend.database import rls

    assert "plan_versions" in rls.UNPROTECTED_TABLES
    assert "plan_versions" in rls.APPEND_ONLY_TABLES
    assert "plan_versions" not in rls.POLICIES
    assert "plans" in rls.UNPROTECTED_TABLES
    assert "subscriptions" in rls.POLICIES


def test_rls_change_is_limited_to_classifying_plan_versions():
    from backend.database import rls

    assert set(rls.UNPROTECTED_TABLES) - {"plan_versions"} == {
        "alembic_version",
        "auth_identities",
        "login_sessions",
        "model_manifests",
        "oauth_challenges",
        "one_time_tokens",
        "otp_challenges",
        "plans",
        "provider_health",
        "users",
        "voice_admission_locks",
        "voice_capacity_slots",
        "voice_media_nodes",
    }
    assert [t for t in rls.APPEND_ONLY_TABLES if t != "plan_versions"] == [
        "model_manifests",
        "ai_policy_versions",
        "run_policy_snapshots",
    ]
    assert hashlib.sha256(repr(sorted(rls.POLICIES.items())).encode()).hexdigest() == (
        "f32bc3e781f4802e7dbad82169ba1f952e5a9c32b123d08a0ae872029a6dbd58"
    )
    assert hashlib.sha256(
        repr(sorted(rls.PRE_ORGANIZATION_POLICIES.items())).encode()
    ).hexdigest() == ("61be561ce03ca4b869b1b1b01943bc62462d2d170ee0834fd708a5c2ea49fd08")


@pytest.mark.postgres
def test_plan_versions_has_no_row_policy_and_tenant_tables_stay_forced(client):
    with _dba() as db:
        security = {
            name: (enabled, forced)
            for name, enabled, forced in db.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relnamespace = 'public'::regnamespace "
                    "AND relname IN ('plan_versions', 'plans', 'subscriptions', 'entitlements')"
                )
            )
        }
        policies = db.execute(
            text("SELECT count(*) FROM pg_policies WHERE tablename = 'plan_versions'")
        ).scalar_one()
    assert security == {
        "plan_versions": (False, False),
        "plans": (False, False),
        "subscriptions": (True, True),
        "entitlements": (True, True),
    }
    assert policies == 0


@pytest.mark.postgres
def test_a_tenant_reads_its_own_pinned_subscription_and_never_another(client, email_outbox):
    from backend.database.saas_models import Subscription

    first = _signup(client, email_outbox, "ac11-first@example.com")
    second = _signup(client, email_outbox, "ac11-second@example.com")
    factory = get_session_factory()
    for own, other in ((first, second), (second, first)):
        with factory() as db:
            bind_tenant_context(db, workspace_id=own["workspace_id"])
            rows = db.execute(
                text("SELECT workspace_id, plan_key, plan_version FROM subscriptions")
            ).all()
            assert [tuple(row) for row in rows] == [(own["workspace_id"], "demo", 1)]
            orm = db.scalars(select(Subscription)).all()
            assert [(s.workspace_id, s.plan_version) for s in orm] == [(own["workspace_id"], 1)]
            hidden = db.execute(
                text("SELECT count(*) FROM subscriptions WHERE workspace_id = :w"),
                {"w": other["workspace_id"]},
            ).scalar_one()
            assert hidden == 0
            db.rollback()
    with factory() as db:
        assert db.execute(text("SELECT count(*) FROM subscriptions")).scalar_one() == 0


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_billing_plans_refuses_writes_and_changes_nothing(client, email_outbox, method):
    from backend.security.context import CSRF_COOKIE

    owner = _signup(client, email_outbox, f"ac12-{method.lower()}@example.com")
    RateLimiter.reset_memory()
    login_user(client, owner["credentials"])
    before = _plans()
    response = client.request(
        method,
        "/api/v1/billing/plans",
        headers={
            "X-Workspace-ID": owner["workspace_id"],
            "X-CSRF-Token": client.cookies.get(CSRF_COOKIE) or "",
        },
        json={"key": "professional", "entitlements": {"audio_seconds": 1}},
    )
    assert response.status_code == 405, response.text
    assert _plans() == before


def test_owner_reads_published_terms_and_a_member_is_refused(client, email_outbox):
    owner = _signup(client, email_outbox, "ac12-owner@example.com")
    member = _signup(client, email_outbox, "ac12-member@example.com")
    with _dba() as db:
        db.add(
            Membership(
                workspace_id=owner["workspace_id"],
                user_id=member["user_id"],
                role=WorkspaceRole.MEMBER,
            )
        )
        db.commit()
    terms = {"audio_seconds": 360000, "concurrent_jobs": 3}
    assert _publish("professional", entitlements=terms, name="Professional Plus") == 2
    catalog = _public_catalog(client, owner)
    assert catalog["professional"]["name"] == "Professional Plus"
    assert catalog["professional"]["entitlements"] == terms
    refused = _owner_get(
        client,
        {"credentials": member["credentials"], "workspace_id": owner["workspace_id"]},
        "/api/v1/billing/plans",
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "permission_denied"


def test_owner_reads_the_seeded_catalog_and_a_member_is_refused(client, email_outbox):
    owner = _signup(client, email_outbox, "ac12-seed-owner@example.com")
    member = _signup(client, email_outbox, "ac12-seed-member@example.com")
    with _dba() as db:
        db.add(
            Membership(
                workspace_id=owner["workspace_id"],
                user_id=member["user_id"],
                role=WorkspaceRole.MEMBER,
            )
        )
        db.commit()
    catalog = _public_catalog(client, owner)
    assert set(catalog) == {"demo", "professional", "business"}
    refused = _owner_get(
        client,
        {"credentials": member["credentials"], "workspace_id": owner["workspace_id"]},
        "/api/v1/billing/plans",
    )
    assert refused.status_code == 403, refused.text


def test_billing_routes_stay_read_only_in_the_application(settings):
    app = create_app(settings)
    methods = {
        method
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/v1/billing")
        for method in getattr(route, "methods", set())
    }
    assert "GET" in methods
    assert methods <= {"GET", "HEAD"}


def test_publish_plan_version_is_not_reachable_from_request_handling_code():
    backend = REPO_ROOT / "backend"
    allowed = {backend / "services" / "tenancy_service.py", backend / "main.py"}
    offenders = sorted(
        str(path.relative_to(backend))
        for path in backend.rglob("*.py")
        if path.relative_to(backend).parts[0] not in {"tests", "migrations"}
        and path not in allowed
        and "publish_plan_version" in path.read_text(encoding="utf-8")
    )
    assert offenders == []


_LOCKED_FILES = {
    "docs/security/authorization-matrix.md": (
        "dff912f785f32cdcaae1124ad7d14f9360e2af0a88abcdf88c48905eb375981f"
    ),
    "backend/routers/v1/billing.py": (
        "59f57c84f004c0a2c5a0ca53b7f4fdcc0f85db2260ae911aa46036f7bfd6a980"
    ),
    "backend/schemas/billing.py": (
        "56c538f7487f9b6c0da202f427b26385fc7c8d9cf8d110c5a0503f11a669a7e5"
    ),
    "backend/services/billing_service.py": (
        "725066d55569af9acc9cef0aa005960510721f6d25baecec8fa2ff876d86af08"
    ),
    "backend/services/entitlement_service.py": (
        "5226dd75ec4ca2b5c4568154b1ac1d643fde7ad19e4d6501642919cf2d8a2f69"
    ),
    "backend/security/authorization.py": (
        "58682022bdec3e142c1b35f2a46d2b31ab8051ff7fe6dbd60fb743d082f9f2a6"
    ),
    "backend/config.py": "6a589728774497749c3f9c08efd280867edf9fc2e67f8cab2314e3649265e608",
    "backend/tests/test_authorization_matrix.py": (
        "51759dfce2c9ea2a689ab4a162e11c29d801ea04d9ad52fdf027578fc59263b8"
    ),
    "backend/tests/test_retention_policy.py": (
        "974595af4255a8078bca7704dc33e2ecf20069e35b6d3fc95dc7dacc223f1ea5"
    ),
    "backend/tests/test_ingestion_ledger.py": (
        "605d982d258fd7dc93459565ecb9282ed8a3e8f9111dbf591ec448f4b4665093"
    ),
    "backend/tests/test_media_lifecycle.py": (
        "405ccdf29c698d67cc5c86da14e5b62f184b923f04a1235b8a988b09a457eb0b"
    ),
}


@pytest.mark.parametrize("relative", sorted(_LOCKED_FILES))
def test_locked_file_is_byte_identical_to_base(relative):
    digest = hashlib.sha256((REPO_ROOT / relative).read_bytes()).hexdigest()
    assert digest == _LOCKED_FILES[relative]


def test_sqlite_fallback_creates_the_schema_seeds_publishes_and_signs_up(
    settings, email_outbox, monkeypatch, tmp_path
):
    from backend.routers.v1 import auth as auth_router

    async def capture_verification(user, token, _settings):
        email_outbox.append({"kind": "verify", "user_id": user.id, "token": token})

    monkeypatch.setattr(auth_router, "_send_verification_message", capture_verification)
    local = settings.model_copy(update={"database_url": f"sqlite:///{tmp_path / 'fallback.db'}"})
    RateLimiter.reset_memory()
    with TestClient(create_app(local)) as api:
        register_user(api, email_outbox, email="ac13-sqlite@example.com")
    with TestClient(create_app(local)):
        pass
    with get_session_factory()() as db:
        assert db.get_bind().dialect.name == "sqlite"
        plans = db.execute(text("SELECT key, current_version FROM plans ORDER BY key")).all()
        assert [tuple(row) for row in plans] == [(key, 1) for key in CATALOG_KEYS]
        versions = db.execute(
            text("SELECT plan_key, version, source FROM plan_versions ORDER BY plan_key")
        ).all()
        assert [tuple(row) for row in versions] == [(k, 1, "catalog_seed") for k in CATALOG_KEYS]
        subscriptions = db.execute(text("SELECT plan_key, plan_version FROM subscriptions")).all()
        assert [tuple(row) for row in subscriptions] == [("demo", 1)]
        limits = dict(db.execute(text("SELECT metric, limit_value FROM entitlements")).all())
        assert limits == {"audio_seconds": 1800, "concurrent_jobs": 1}
        version = _tenancy().publish_plan_version(
            db, "professional", entitlements={"audio_seconds": 5}
        )
        assert version.version == 2
        db.commit()
    with get_session_factory()() as db:
        row = db.execute(
            text("SELECT current_version, entitlements_json FROM plans WHERE key = 'professional'")
        ).one()
        assert (row[0], _decode(row[1])) == (2, {"audio_seconds": 5})
