"""WP18-T03 (HOM-101): versioned plans; startup never overwrites the plan catalog.

Defect D-12: the catalog seed rewrote ``name``, ``is_public`` and
``entitlements_json`` of every catalog plan on every API start and on every
sign-up, so operator/negotiated terms were silently reset.  After this unit:

* startup and sign-up only *insert* missing catalog keys (with version 1);
* an existing plan row is never rewritten.  The one exception is the demo
  allowance, owned by ``DEMO_QUOTA_SECONDS``: a change appends a ``config``
  version;
* ``plan_versions`` is an append-only history.  ``publish_plan_version`` is the
  one sanctioned write path.  A subscription is pinned to the version it was
  granted under, and the demo grant's entitlement rows come from that version;
* the migration snapshots existing rows losslessly and round-trips.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import JSON, Text, cast, create_engine, delete, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.config import Settings, reset_settings_cache
from backend.database.model_registry import Base
from backend.database.relational import (
    configure_database,
    create_schema_for_test_or_development,
    get_session_factory,
    reset_database_state,
)
from backend.database.rls import APP_ROLE, APPEND_ONLY_TABLES, UNPROTECTED_TABLES
from backend.database.saas_models import (
    Entitlement,
    Membership,
    Organization,
    Plan,
    PlanVersion,
    PlanVersionImmutableError,
    Subscription,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.database.tenant_context import bind_tenant_context
from backend.main import create_app
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.services.rate_limit_service import RateLimiter
from backend.services.tenancy_service import (
    PlanCatalogError,
    create_personal_workspace,
    ensure_plan_catalog,
    publish_plan_version,
)
from backend.tests._database import inspection_session_factory, reset_postgres_schema
from backend.tests.conftest import login_user, register_user

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
BASE_REVISION = "a1c3e5f7b9d2"
VERSIONED_PLANS_REVISION = "c5e7a9b1d3f6"

CATALOG_DEFAULTS: dict[str, tuple[str, bool, dict]] = {
    "demo": ("Free Demo", True, {"audio_seconds": 1800, "concurrent_jobs": 1, "seats": 1}),
    "professional": ("Professional", True, {}),
    "business": ("Business", True, {}),
    "enterprise": ("Enterprise", False, {}),
    "on_premise": ("On-Premise", False, {}),
}

DRIFT_LINE = re.compile(r"^PLAN_CATALOG_DRIFT plan=(?P<plan>\S+) fields=(?P<fields>\S+)$")
PUBLISHED_LINE = re.compile(
    r"^PLAN_VERSION_PUBLISHED plan=(?P<plan>\S+) version=(?P<version>\d+) source=(?P<source>\S+)$"
)


# --- helpers ------------------------------------------------------------------------


@pytest.fixture
def plan_logs(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """Records of the ``homayar`` logger.

    An in-process Alembic run (``fileConfig`` in ``env.py``) disables every
    logger that exists at that moment, ``homayar`` included; re-enable it for
    the duration of the test.
    """
    homayar = logging.getLogger("homayar")
    was_disabled = homayar.disabled
    homayar.disabled = False
    caplog.set_level(logging.INFO, logger="homayar")
    yield caplog
    homayar.disabled = was_disabled


@pytest.fixture
def catalog_db(settings: Settings) -> Iterator[Settings]:
    """An application database with the catalog seeded, without starting the API."""
    configure_database(settings)
    create_schema_for_test_or_development(settings)
    with get_session_factory()() as db:
        ensure_plan_catalog(db, settings)
        db.commit()
    yield settings
    reset_database_state()


@contextmanager
def _running_app(settings: Settings) -> Iterator[TestClient]:
    """A new application lifespan against the same database: an API restart."""
    reset_database_state()
    RateLimiter.reset_memory()
    with TestClient(create_app(settings)) as api:
        yield api


def _restart(settings: Settings) -> None:
    with _running_app(settings):
        pass


def _rows(table: sa.Table) -> dict[tuple, dict]:
    """Every row of ``table`` by primary key, JSON columns as stored text (byte-exact)."""
    columns = [
        cast(column, Text).label(column.name) if isinstance(column.type, JSON) else column
        for column in table.columns
    ]
    key_columns = list(table.primary_key.columns)
    with inspection_session_factory()() as db:
        result = db.execute(select(*columns).order_by(*key_columns))
        return {
            tuple(row._mapping[column.name] for column in key_columns): dict(row._mapping)
            for row in result
        }


def _catalog_state() -> tuple[dict, dict]:
    return _rows(Plan.__table__), _rows(PlanVersion.__table__)


def _tenant_state() -> tuple[dict, dict]:
    return _rows(Subscription.__table__), _rows(Entitlement.__table__)


def _drift(logs: pytest.LogCaptureFixture) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for record in logs.records:
        match = DRIFT_LINE.match(record.getMessage())
        if record.name != "homayar" or match is None:
            continue
        assert record.levelno == logging.WARNING, record.getMessage()
        assert match["plan"] not in found, f"two drift lines for {match['plan']}"
        found[match["plan"]] = match["fields"].split(",")
    return found


def _published(logs: pytest.LogCaptureFixture) -> list[tuple[str, int, str]]:
    lines = []
    for record in logs.records:
        match = PUBLISHED_LINE.match(record.getMessage())
        if record.name == "homayar" and match is not None:
            assert record.levelno == logging.INFO
            lines.append((match["plan"], int(match["version"]), match["source"]))
    return lines


def _dba_edits() -> None:
    """AC1's direct SQL edits by a DBA (not through the sanctioned write path)."""
    plans = Plan.__table__
    with inspection_session_factory()() as db:
        db.execute(
            update(plans)
            .where(plans.c.key == "professional")
            .values(
                entitlements_json={"audio_seconds": 360000, "concurrent_jobs": 3},
                display_json={"badge": "x"},
            )
        )
        db.execute(update(plans).where(plans.c.key == "business").values(is_public=False))
        db.execute(
            update(plans)
            .where(plans.c.key == "enterprise")
            .values(
                name="Enterprise (negotiated)",
                entitlements_json={"audio_seconds": 900000, "seats": 50},
            )
        )
        db.execute(update(plans).where(plans.c.key == "on_premise").values(is_active=False))
        db.commit()


def _cookies(client: TestClient) -> dict[str, str]:
    return {
        name: value
        for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
        if (value := client.cookies.get(name))
    }


def _signup(client: TestClient, email_outbox: list[dict[str, str]], email: str) -> dict:
    """Register + verify + log in; returns the new workspace and its session cookies."""
    client.cookies.clear()
    credentials = register_user(client, email_outbox, email=email)
    session = login_user(client, credentials)
    return {
        "user_id": session["user"]["id"],
        "workspace_id": session["workspaces"][0]["id"],
        "cookies": _cookies(client),
    }


def _grant(workspace_id: str) -> tuple[list[tuple], dict[str, tuple]]:
    """The workspace's subscriptions ``(plan_key, plan_version)`` and entitlement limits."""
    with inspection_session_factory()() as db:
        subscriptions = db.execute(
            select(Subscription.plan_key, Subscription.plan_version).where(
                Subscription.workspace_id == workspace_id
            )
        ).all()
        entitlements = {
            row.metric: (row.limit_value, row.source)
            for row in db.scalars(
                select(Entitlement).where(Entitlement.workspace_id == workspace_id)
            )
        }
    return [tuple(row) for row in subscriptions], entitlements


def _plan(key: str) -> Plan:
    with inspection_session_factory()() as db:
        plan = db.get(Plan, key)
        assert plan is not None
        return plan


def _versions(key: str) -> list[PlanVersion]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(PlanVersion)
                .where(PlanVersion.plan_key == key)
                .order_by(PlanVersion.version.asc())
            )
        )


def _terms(item: Plan | PlanVersion) -> tuple[str, bool, dict]:
    return item.name, item.is_public, item.entitlements_json


def _publish(plan_key: str, **kwargs) -> PlanVersion:
    with get_session_factory()() as db:
        created = publish_plan_version(db, plan_key, **kwargs)
        db.commit()
        return created


# --- AC1: startup never overwrites existing plans (D-12) -----------------------------


def test_restart_keeps_operator_edits_and_reports_only_the_drifted_fields(
    client: TestClient,
    email_outbox: list[dict[str, str]],
    settings: Settings,
    plan_logs: pytest.LogCaptureFixture,
) -> None:
    owner = _signup(client, email_outbox, "catalog-owner@example.com")
    _dba_edits()

    def catalog(api: TestClient) -> dict[str, dict]:
        response = api.get(
            "/api/v1/billing/plans",
            cookies=owner["cookies"],
            headers={"X-Workspace-ID": owner["workspace_id"]},
        )
        assert response.status_code == 200, response.text
        return {item["key"]: item for item in response.json()["items"]}

    before = catalog(client)
    assert before["professional"]["entitlements"] == {"audio_seconds": 360000, "concurrent_jobs": 3}
    assert "business" not in before
    plans, versions = _catalog_state()
    plan_logs.clear()

    with _running_app(settings) as api:
        after = catalog(api)

    # Every pre-existing row is identical in every column (updated_at and
    # current_version included) and no version was appended.
    assert _catalog_state() == (plans, versions)
    assert after == before
    drift = _drift(plan_logs)
    assert {plan: set(fields) for plan, fields in drift.items()} == {
        "professional": {"entitlements_json"},
        "business": {"is_public"},
        "enterprise": {"name", "entitlements_json"},
    }
    assert _published(plan_logs) == []


def test_a_restart_without_edits_writes_and_reports_nothing(
    client: TestClient, settings: Settings, plan_logs: pytest.LogCaptureFixture
) -> None:
    plans, versions = _catalog_state()
    assert len(plans) == 5 and len(versions) == 5
    plan_logs.clear()
    _restart(settings)
    assert _catalog_state() == (plans, versions)
    assert _drift(plan_logs) == {}
    assert _published(plan_logs) == []


def test_a_missing_current_version_row_is_reported_as_drift(
    client: TestClient, settings: Settings, plan_logs: pytest.LogCaptureFixture
) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(Plan.__table__).where(Plan.key == "enterprise").values(current_version=4))
        db.commit()
    plans, versions = _catalog_state()
    plan_logs.clear()
    _restart(settings)
    assert _catalog_state() == (plans, versions)
    drift = _drift(plan_logs)
    assert set(drift) == {"enterprise"}
    assert set(drift["enterprise"]) <= {"name", "is_public", "entitlements_json"}


# --- AC2: sign-up never modifies the catalog ----------------------------------------


def test_signup_and_the_demo_account_never_modify_the_catalog(
    client: TestClient, email_outbox: list[dict[str, str]], settings: Settings
) -> None:
    _dba_edits()
    _publish("professional", entitlements={"audio_seconds": 720000, "concurrent_jobs": 5})
    plans, versions = _catalog_state()
    assert len(versions) == 6

    tenant = _signup(client, email_outbox, "catalog-signup@example.com")
    assert _grant(tenant["workspace_id"])[0] == [("demo", 1)]
    assert _catalog_state() == (plans, versions)

    # The development demo account is created by the lifespan through the same
    # workspace bootstrap.
    with _running_app(settings.model_copy(update={"enable_demo_account": True})):
        pass
    with inspection_session_factory()() as db:
        demo_user = db.scalar(select(User).where(User.email == "demo@local.homayar"))
        assert demo_user is not None
        demo_workspace = db.scalar(
            select(Membership.workspace_id).where(Membership.user_id == demo_user.id)
        )
    assert _grant(demo_workspace)[0] == [("demo", 1)]
    assert _catalog_state() == (plans, versions)


# --- AC3: missing catalog keys are inserted, never others -----------------------------


def test_an_empty_database_gets_exactly_the_code_catalog_with_version_one(
    settings: Settings,
) -> None:
    custom = settings.model_copy(update={"demo_quota_seconds": 2400})
    configure_database(custom)
    create_schema_for_test_or_development(custom)
    try:
        with get_session_factory()() as db:
            ensure_plan_catalog(db, custom)
            db.commit()
        # Read before the engine is discarded: on the SQLite fallback the
        # inspection session is the application's own.
        with inspection_session_factory()() as db:
            plans = {plan.key: plan for plan in db.scalars(select(Plan))}
            versions = list(db.scalars(select(PlanVersion)))
    finally:
        reset_database_state()

    expected = dict(CATALOG_DEFAULTS)
    expected["demo"] = (
        "Free Demo",
        True,
        {"audio_seconds": 2400, "concurrent_jobs": 1, "seats": 1},
    )
    assert set(plans) == set(expected)
    for key, terms in expected.items():
        plan = plans[key]
        assert _terms(plan) == terms, key
        assert plan.is_active is True and plan.display_json == {} and plan.current_version == 1
    assert sorted((v.plan_key, v.version, v.source, *_terms(v)) for v in versions) == sorted(
        (key, 1, "catalog_seed", *terms) for key, terms in expected.items()
    )


def test_a_deleted_catalog_key_is_recreated_alone(client: TestClient, settings: Settings) -> None:
    _dba_edits()
    with inspection_session_factory()() as db:
        db.execute(delete(PlanVersion.__table__).where(PlanVersion.plan_key == "enterprise"))
        db.execute(delete(Plan.__table__).where(Plan.key == "enterprise"))
        db.commit()
    plans, versions = _catalog_state()
    assert ("enterprise",) not in plans

    _restart(settings)

    plans_after, versions_after = _catalog_state()
    recreated = plans_after.pop(("enterprise",))
    assert plans_after == plans
    new_version = versions_after.pop(("enterprise", 1))
    assert versions_after == versions
    assert (
        recreated["name"],
        recreated["is_public"],
        json.loads(recreated["entitlements_json"]),
    ) == CATALOG_DEFAULTS["enterprise"]
    assert recreated["is_active"] is True and json.loads(recreated["display_json"]) == {}
    assert recreated["current_version"] == 1
    assert new_version["source"] == "catalog_seed"
    assert (new_version["name"], new_version["is_public"], new_version["entitlements_json"]) == (
        recreated["name"],
        recreated["is_public"],
        recreated["entitlements_json"],
    )


# --- AC4: versions are immutable and numbered ------------------------------------------


def test_publish_appends_the_next_version_and_leaves_history_intact(
    catalog_db: Settings, plan_logs: pytest.LogCaptureFixture
) -> None:
    with inspection_session_factory()() as db:
        db.execute(
            update(Plan.__table__)
            .where(Plan.key == "professional")
            .values(display_json={"badge": "x"}, is_active=False)
        )
        db.commit()
    _plans, versions = _catalog_state()
    row_before = _rows(Plan.__table__)[("professional",)]

    with get_session_factory()() as db:
        created = publish_plan_version(
            db,
            "professional",
            entitlements={"audio_seconds": 360000, "concurrent_jobs": 3, "seats": None},
            name="Professional 2026",
        )
        assert isinstance(created, PlanVersion)
        assert (created.plan_key, created.version, created.source) == (
            "professional",
            2,
            "operator",
        )
        # Flushed, not committed: visible in this transaction only.
        assert (
            db.scalar(
                select(func.count())
                .select_from(PlanVersion)
                .where(PlanVersion.plan_key == "professional")
            )
            == 2
        )
        db.rollback()
    assert _catalog_state()[1] == versions
    plan_logs.clear()

    created = _publish(
        "professional",
        entitlements={"audio_seconds": 360000, "concurrent_jobs": 3, "seats": None},
        name="Professional 2026",
    )
    assert created.version == 2
    rows = _rows(PlanVersion.__table__)
    assert rows[("professional", 1)] == versions[("professional", 1)]  # byte-identical
    row = _rows(Plan.__table__)[("professional",)]
    version = rows[("professional", 2)]
    assert version["source"] == "operator"
    assert (row["name"], row["is_public"], row["entitlements_json"]) == (
        version["name"],
        version["is_public"],
        version["entitlements_json"],
    )
    assert row["name"] == "Professional 2026" and row["is_public"] is True
    assert json.loads(row["entitlements_json"]) == {
        "audio_seconds": 360000,
        "concurrent_jobs": 3,
        "seats": None,
    }
    assert row["current_version"] == 2
    assert (row["is_active"], row["display_json"]) == (
        row_before["is_active"],
        row_before["display_json"],
    )

    third = _publish("professional", entitlements={}, is_public=False, source="config")
    assert (third.version, third.source) == (3, "config")
    plan = _plan("professional")
    assert _terms(plan) == ("Professional 2026", False, {}) and plan.current_version == 3
    assert [v.version for v in _versions("professional")] == [1, 2, 3]
    assert _published(plan_logs) == [("professional", 2, "operator"), ("professional", 3, "config")]


def test_plan_versions_refuse_orm_updates_and_deletes(catalog_db: Settings) -> None:
    _plans, versions = _catalog_state()
    for factory in (get_session_factory(), inspection_session_factory()):
        with factory() as db:
            version = db.get(PlanVersion, ("demo", 1))
            assert version is not None
            version.entitlements_json = {"audio_seconds": 1}
            with pytest.raises(PlanVersionImmutableError):
                db.flush()
            db.rollback()
            version = db.get(PlanVersion, ("demo", 1))
            version.name = "Renamed"
            with pytest.raises(PlanVersionImmutableError):
                db.flush()
            db.rollback()
            db.delete(db.get(PlanVersion, ("demo", 1)))
            with pytest.raises(PlanVersionImmutableError):
                db.flush()
            db.rollback()
    assert _catalog_state()[1] == versions


def test_plan_versions_are_an_append_only_global_table() -> None:
    assert "plan_versions" in UNPROTECTED_TABLES
    assert "plan_versions" in APPEND_ONLY_TABLES


@pytest.mark.postgres
def test_the_app_role_cannot_update_or_delete_plan_versions(catalog_db: Settings) -> None:
    _plans, versions = _catalog_state()
    with get_session_factory()() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        for statement in (
            "UPDATE plan_versions SET name = 'rewritten'",
            "DELETE FROM plan_versions",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                db.execute(text(statement))
            db.rollback()
        # Reads and appends are allowed.
        assert db.scalar(text("SELECT count(*) FROM plan_versions")) == 5
        db.execute(
            text(
                "INSERT INTO plan_versions (plan_key, version, name, is_public, "
                "entitlements_json, source, created_at) VALUES ('enterprise', 2, 'Enterprise', "
                "false, '{}', 'operator', now())"
            )
        )
        db.rollback()
    assert _catalog_state()[1] == versions


def test_invalid_publishes_raise_and_write_nothing(catalog_db: Settings) -> None:
    cases: list[tuple[str, str, object, dict]] = [
        ("unknown plan", "gold", {"audio_seconds": 1}, {}),
        ("plan key is case-sensitive", "Professional", {"audio_seconds": 1}, {}),
        ("entitlements is a list", "professional", ["audio_seconds"], {}),
        ("entitlements is None", "professional", None, {}),
        ("entitlements is a str", "professional", "audio_seconds=1", {}),
        ("upper-case key", "professional", {"Audio": 1}, {}),
        ("key starts with a digit", "professional", {"1audio": 1}, {}),
        ("key with a dash", "professional", {"audio-seconds": 1}, {}),
        ("empty key", "professional", {"": 1}, {}),
        ("key longer than 60", "professional", {"a" * 61: 1}, {}),
        ("non-str key", "professional", {3: 1}, {}),
        ("negative value", "professional", {"audio_seconds": -1}, {}),
        ("bool value", "professional", {"audio_seconds": True}, {}),
        ("float value", "professional", {"audio_seconds": 1.5}, {}),
        ("integral float", "professional", {"audio_seconds": 10.0}, {}),
        ("str value", "professional", {"audio_seconds": "10"}, {}),
        ("dict value", "professional", {"audio_seconds": {"max": 1}}, {}),
        ("list value", "professional", {"audio_seconds": [1]}, {}),
        ("above int32", "professional", {"audio_seconds": 2147483648}, {}),
        ("one bad value among good ones", "professional", {"seats": 5, "audio_seconds": -5}, {}),
        ("empty name", "professional", {}, {"name": ""}),
        ("blank name", "professional", {}, {"name": "   "}),
        ("name longer than 100", "professional", {}, {"name": "x" * 101}),
        ("non-str name", "professional", {}, {"name": 7}),
        ("non-bool is_public", "professional", {}, {"is_public": "yes"}),
        ("source catalog_seed", "professional", {}, {"source": "catalog_seed"}),
        ("source legacy_snapshot", "professional", {}, {"source": "legacy_snapshot"}),
        ("unknown source", "professional", {}, {"source": "manual"}),
        (
            "operator changes demo audio_seconds",
            "demo",
            {"audio_seconds": 3600, "concurrent_jobs": 1, "seats": 1},
            {},
        ),
        ("operator omits demo audio_seconds", "demo", {"concurrent_jobs": 2, "seats": 1}, {}),
        ("operator sets demo audio_seconds unlimited", "demo", {"audio_seconds": None}, {}),
    ]
    state = _catalog_state()
    for label, plan_key, entitlements, extra in cases:
        with get_session_factory()() as db:
            with pytest.raises(PlanCatalogError) as caught:
                publish_plan_version(db, plan_key, entitlements=entitlements, **extra)
            db.commit()
        assert isinstance(caught.value, ValueError), label
        if plan_key == "demo":
            assert "DEMO_QUOTA_SECONDS" in str(caught.value), label
        assert _catalog_state() == state, label


def test_a_moved_back_current_version_still_appends_after_the_latest(
    catalog_db: Settings,
) -> None:
    _publish("professional", entitlements={"audio_seconds": 10})
    _publish("demo", entitlements={"audio_seconds": 1800, "concurrent_jobs": 2, "seats": 1})
    # A DBA returns both plans to version 1 by direct SQL.
    with inspection_session_factory()() as db:
        for key in ("professional", "demo"):
            db.execute(
                update(Plan.__table__)
                .where(Plan.key == key)
                .values(current_version=1, entitlements_json=CATALOG_DEFAULTS[key][2])
            )
        db.commit()

    assert _publish("professional", entitlements={"audio_seconds": 30}).version == 3
    with get_session_factory()() as db:
        ensure_plan_catalog(db, catalog_db.model_copy(update={"demo_quota_seconds": 3600}))
        db.commit()

    for key in ("professional", "demo"):
        assert [version.version for version in _versions(key)] == [1, 2, 3], key
        assert _plan(key).current_version == 3, key
    assert _versions("demo")[-1].source == "config"
    assert _plan("demo").entitlements_json == {
        "audio_seconds": 3600,
        "concurrent_jobs": 1,
        "seats": 1,
    }


def test_zero_unlimited_and_int32_values_are_accepted(catalog_db: Settings) -> None:
    created = _publish(
        "business",
        entitlements={"audio_seconds": 0, "concurrent_jobs": None, "seats": 2147483647},
    )
    assert created.version == 2
    assert _plan("business").entitlements_json == {
        "audio_seconds": 0,
        "concurrent_jobs": None,
        "seats": 2147483647,
    }


# --- AC5: DEMO_QUOTA_SECONDS owns the demo allowance ----------------------------------------


def test_a_changed_demo_quota_appends_one_config_version(
    client: TestClient, settings: Settings, plan_logs: pytest.LogCaptureFixture
) -> None:
    # An operator version first, so the config version must carry its terms.
    _publish(
        "demo",
        entitlements={"audio_seconds": 1800, "concurrent_jobs": 2, "seats": 1, "exports": 10},
        name="Free Demo 2026",
    )
    with inspection_session_factory()() as db:
        db.execute(
            update(Plan.__table__).where(Plan.key == "demo").values(display_json={"badge": "new"})
        )
        db.commit()
    plans, versions = _catalog_state()
    plan_logs.clear()

    raised = settings.model_copy(update={"demo_quota_seconds": 3600})
    _restart(raised)

    plans_after, versions_after = _catalog_state()
    demo_before, demo_after = plans.pop(("demo",)), plans_after.pop(("demo",))
    assert plans_after == plans  # no other plan changes
    appended = versions_after.pop(("demo", 3))
    assert versions_after == versions
    assert appended["source"] == "config"
    assert json.loads(appended["entitlements_json"]) == {
        "audio_seconds": 3600,
        "concurrent_jobs": 2,
        "seats": 1,
        "exports": 10,
    }
    assert (appended["name"], appended["is_public"]) == ("Free Demo 2026", True)
    assert (demo_after["name"], demo_after["is_public"], demo_after["entitlements_json"]) == (
        appended["name"],
        appended["is_public"],
        appended["entitlements_json"],
    )
    assert demo_after["current_version"] == 3
    for column in ("is_active", "display_json", "created_at"):
        assert demo_after[column] == demo_before[column], column
    assert _published(plan_logs) == [("demo", 3, "config")]
    assert _drift(plan_logs) == {}

    # A second restart with the same setting is a no-op.
    state = _catalog_state()
    plan_logs.clear()
    _restart(raised)
    assert _catalog_state() == state
    assert _published(plan_logs) == [] and _drift(plan_logs) == {}


@pytest.mark.parametrize("quota", [-1, 2147483648])
def test_an_out_of_range_demo_quota_is_refused_when_settings_load(quota: int) -> None:
    with pytest.raises(ValidationError, match="DEMO_QUOTA_SECONDS"):
        Settings(_env_file=None, demo_quota_seconds=quota)


@pytest.mark.parametrize("quota", [0, 2147483647])
def test_the_demo_quota_bounds_are_accepted(quota: int) -> None:
    assert Settings(_env_file=None, demo_quota_seconds=quota).demo_quota_seconds == quota


def test_an_operator_demo_publish_survives_restarts_and_reaches_only_new_signups(
    client: TestClient, email_outbox: list[dict[str, str]], settings: Settings
) -> None:
    first = _signup(client, email_outbox, "demo-first@example.com")
    created = _publish(
        "demo", entitlements={"audio_seconds": 1800, "concurrent_jobs": 2, "seats": 1}
    )
    assert created.version == 2
    state = _catalog_state()
    _restart(settings)
    _restart(settings)
    assert _catalog_state() == state
    assert _terms(_plan("demo"))[2] == {"audio_seconds": 1800, "concurrent_jobs": 2, "seats": 1}

    with _running_app(settings) as api:
        second = _signup(api, email_outbox, "demo-second@example.com")
    assert _grant(first["workspace_id"]) == (
        [("demo", 1)],
        {"audio_seconds": (1800, "demo"), "concurrent_jobs": (1, "demo")},
    )
    assert _grant(second["workspace_id"]) == (
        [("demo", 2)],
        {"audio_seconds": (1800, "demo"), "concurrent_jobs": (2, "demo")},
    )


# --- AC6: real entitlements at sign-up, pinned to a version -----------------------------------


def test_the_signup_grant_comes_from_the_pinned_version(
    client: TestClient, email_outbox: list[dict[str, str]]
) -> None:
    default = _signup(client, email_outbox, "grant-default@example.com")
    assert _plan("demo").current_version == 1
    assert _grant(default["workspace_id"]) == (
        [("demo", 1)],
        {"audio_seconds": (1800, "demo"), "concurrent_jobs": (1, "demo")},  # no "seats" row
    )
    usage = client.get(
        "/api/v1/billing/usage",
        cookies=default["cookies"],
        headers={"X-Workspace-ID": default["workspace_id"]},
    )
    assert usage.status_code == 200, usage.text
    snapshots = {item["metric"]: item for item in usage.json()["items"]}
    assert snapshots["audio_seconds"]["remaining"] == 1800
    assert snapshots["concurrent_jobs"]["remaining"] == 1

    # JSON null is an unlimited (NULL) limit.
    _publish("demo", entitlements={"audio_seconds": 1800, "concurrent_jobs": None})
    unlimited = _signup(client, email_outbox, "grant-unlimited@example.com")
    assert _grant(unlimited["workspace_id"]) == (
        [("demo", 2)],
        {"audio_seconds": (1800, "demo"), "concurrent_jobs": (None, "demo")},
    )

    # A metered metric absent from the version gets no row (fail closed).
    _publish("demo", entitlements={"audio_seconds": 1800})
    absent = _signup(client, email_outbox, "grant-absent@example.com")
    assert _grant(absent["workspace_id"]) == ([("demo", 3)], {"audio_seconds": (1800, "demo")})

    # A direct SQL edit of the row is not a version: the grant still follows v3.
    with inspection_session_factory()() as db:
        db.execute(
            update(Plan.__table__)
            .where(Plan.key == "demo")
            .values(entitlements_json={"audio_seconds": 1800, "concurrent_jobs": 9})
        )
        db.commit()
    drifted = _signup(client, email_outbox, "grant-drifted@example.com")
    assert _grant(drifted["workspace_id"]) == ([("demo", 3)], {"audio_seconds": (1800, "demo")})

    # Earlier workspaces keep their pins and limits.
    assert _grant(default["workspace_id"])[0] == [("demo", 1)]
    assert _grant(unlimited["workspace_id"])[1]["concurrent_jobs"] == (None, "demo")


def test_signup_without_the_free_demo_creates_no_subscription(
    client: TestClient, email_outbox: list[dict[str, str]], settings: Settings
) -> None:
    state = _catalog_state()
    with _running_app(settings.model_copy(update={"enable_free_demo": False})) as api:
        tenant = _signup(api, email_outbox, "no-demo@example.com")
    assert _grant(tenant["workspace_id"]) == ([], {})
    assert _catalog_state() == state


# --- AC7: tenant-negotiated rows are never touched ------------------------------------------


def test_existing_subscriptions_and_entitlements_are_never_touched(
    client: TestClient, email_outbox: list[dict[str, str]], settings: Settings
) -> None:
    one = _signup(client, email_outbox, "tenant-one@example.com")
    _signup(client, email_outbox, "tenant-two@example.com")
    with inspection_session_factory()() as db:
        db.execute(
            update(Entitlement.__table__)
            .where(
                Entitlement.workspace_id == one["workspace_id"],
                Entitlement.metric == "audio_seconds",
            )
            .values(limit_value=5000)
        )
        db.commit()
    tenants = _tenant_state()
    assert len(tenants[0]) == 2 and len(tenants[1]) == 4

    _restart(settings)  # (a)
    assert _tenant_state() == tenants
    _publish("demo", entitlements={"audio_seconds": 1800, "concurrent_jobs": 3, "seats": 1})  # (b)
    assert _tenant_state() == tenants
    _restart(settings.model_copy(update={"demo_quota_seconds": 3600}))  # (c)
    assert [v.source for v in _versions("demo")] == ["catalog_seed", "operator", "config"]
    assert _tenant_state() == tenants
    assert _grant(one["workspace_id"])[1]["audio_seconds"] == (5000, "demo")


# --- AC8: concurrency (PostgreSQL, app role, separate sessions) -------------------------------


def _race(*calls: Callable[[Session], object]) -> None:
    """Run each call in its own application session, released together by a barrier.

    Each transaction stays open briefly after its call so the other one queues
    behind its locks instead of running after it.
    """
    barrier = threading.Barrier(len(calls))
    errors: list[str] = []

    def run(call: Callable[[Session], object]) -> None:
        try:
            with get_session_factory()() as db:
                barrier.wait(timeout=15)
                call(db)
                time.sleep(0.3)
                db.commit()
        except BaseException as exc:  # surfaced by the assertion below
            errors.append(repr(exc))

    threads = [threading.Thread(target=run, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert all(not thread.is_alive() for thread in threads), "a racer hung"
    assert errors == []


@pytest.mark.postgres
def test_concurrent_seeding_of_an_empty_database_creates_one_catalog(settings: Settings) -> None:
    configure_database(settings)
    create_schema_for_test_or_development(settings)
    try:
        _race(*(lambda db: ensure_plan_catalog(db, settings) for _ in range(2)))
    finally:
        reset_database_state()
    plans, versions = _catalog_state()
    assert set(plans) == {(key,) for key in CATALOG_DEFAULTS}
    assert set(versions) == {(key, 1) for key in CATALOG_DEFAULTS}


@pytest.mark.postgres
def test_concurrent_restarts_with_a_changed_quota_append_one_config_version(
    catalog_db: Settings,
) -> None:
    raised = catalog_db.model_copy(update={"demo_quota_seconds": 3600})
    _race(*(lambda db: ensure_plan_catalog(db, raised) for _ in range(2)))
    versions = _versions("demo")
    assert [(v.version, v.source) for v in versions] == [(1, "catalog_seed"), (2, "config")]
    assert versions[1].entitlements_json["audio_seconds"] == 3600
    assert _plan("demo").current_version == 2
    assert len(_catalog_state()[1]) == 6


@pytest.mark.postgres
def test_concurrent_publishes_get_consecutive_versions(catalog_db: Settings) -> None:
    terms = [{"seats": 10}, {"seats": 20}]
    _race(
        *(
            lambda db, entitlements=entitlements: publish_plan_version(
                db, "professional", entitlements=entitlements
            )
            for entitlements in terms
        )
    )
    versions = _versions("professional")
    assert [v.version for v in versions] == [1, 2, 3]
    assert sorted((v.entitlements_json for v in versions[1:]), key=str) == sorted(terms, key=str)
    plan = _plan("professional")
    assert plan.current_version == 3
    assert _terms(plan) == _terms(versions[2])


# --- AC9 / AC10: migration upgrade, backfill and downgrade (PostgreSQL) ------------------------

OWNER_PROBE_ROLE = "homayar_plan_owner_probe"
OWNER_PROBE_PASSWORD = "plan-owner-probe-only"  # noqa: S105 - throwaway role on a test database

LEGACY_PLANS = sa.table(
    "plans",
    sa.column("key"),
    sa.column("name"),
    sa.column("is_public"),
    sa.column("is_active"),
    sa.column("entitlements_json", sa.JSON),
    sa.column("display_json", sa.JSON),
    sa.column("created_at"),
    sa.column("updated_at"),
)
LEGACY_SUBSCRIPTIONS = sa.table(
    "subscriptions",
    sa.column("id"),
    sa.column("workspace_id"),
    sa.column("plan_key"),
    sa.column("status"),
    sa.column("provider"),
    sa.column("starts_at"),
    sa.column("created_at"),
    sa.column("updated_at"),
)


def _alembic(database_url: str, monkeypatch: pytest.MonkeyPatch, action: str, target: str) -> None:
    homayar = logging.getLogger("homayar")
    was_disabled = homayar.disabled
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    try:
        getattr(command, action)(Config(str(REPO_ROOT / "alembic.ini")), target)
    finally:
        reset_settings_cache()
        homayar.disabled = was_disabled  # env.py's fileConfig disables existing loggers


def _admin(*statements: str) -> None:
    with inspection_session_factory()() as db:
        for statement in statements:
            db.execute(text(statement))
        db.commit()


def _table_rows(table: str, key: str) -> dict[str, dict]:
    """``SELECT *`` of a table at whatever revision, JSON columns also as exact text."""
    with inspection_session_factory()() as db:
        json_columns = (
            db.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :table AND data_type = 'json'"
                ),
                {"table": table},
            )
            .scalars()
            .all()
        )
        extra = "".join(f", {column}::text AS {column}__text" for column in json_columns)
        rows = db.execute(text(f"SELECT *{extra} FROM {table}")).mappings().all()  # noqa: S608
    return {row[key]: dict(row) for row in rows}


def _project(rows: dict[str, dict], like: dict[str, dict]) -> dict[str, dict]:
    """``rows`` restricted to the columns of ``like``; columns added later are ignored."""
    columns = set().union(*(row.keys() for row in like.values())) if like else set()
    return {key: {k: v for k, v in row.items() if k in columns} for key, row in rows.items()}


def _columns(table: str) -> set[str]:
    with inspection_session_factory()() as db:
        return set(
            db.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :table"
                ),
                {"table": table},
            ).scalars()
        )


def _scalar(sql: str, **params: object) -> object:
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


def _transfer_ownership(role: str) -> None:
    """Make ``role`` the plain owner of every relation (the ADR 0005 deployment shape)."""
    with inspection_session_factory()() as db:
        relations = db.execute(
            text(
                "SELECT c.relname, c.relkind FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S') "
                "AND NOT (c.relkind = 'S' AND EXISTS (SELECT 1 FROM pg_depend d "
                "WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid "
                "AND d.deptype IN ('a', 'i')))"
            )
        ).all()
        keywords = {"r": "TABLE", "p": "TABLE", "v": "VIEW", "m": "MATERIALIZED VIEW"}
        for name, kind in relations:
            keyword = keywords.get(kind, "SEQUENCE")
            db.execute(text(f'ALTER {keyword} "{name}" OWNER TO {role}'))
        db.commit()


@pytest.fixture(params=["superuser", "plain_owner"])
def legacy_database(
    request: pytest.FixtureRequest, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    """The schema at the base revision; yields the URL the new revision runs as.

    ``plain_owner``: a LOGIN role that owns every table but is neither a
    superuser nor BYPASSRLS, so FORCE ROW LEVEL SECURITY applies to it.
    """
    reset_database_state()
    reset_postgres_schema(settings.database_url)
    _alembic(settings.database_url, monkeypatch, "upgrade", BASE_REVISION)
    role_url = settings.database_url
    if request.param == "plain_owner":
        _admin(
            f"DROP ROLE IF EXISTS {OWNER_PROBE_ROLE}",
            f"CREATE ROLE {OWNER_PROBE_ROLE} LOGIN PASSWORD '{OWNER_PROBE_PASSWORD}' "
            "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE",
            f"GRANT USAGE, CREATE ON SCHEMA public TO {OWNER_PROBE_ROLE}",
        )
        _transfer_ownership(OWNER_PROBE_ROLE)
        role_url = (
            make_url(settings.database_url)
            .set(username=OWNER_PROBE_ROLE, password=OWNER_PROBE_PASSWORD)
            .render_as_string(hide_password=False)
        )
        assert (
            _scalar(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = :role",
                role=OWNER_PROBE_ROLE,
            )
            is False
        )
    try:
        yield role_url
    finally:
        reset_database_state()
        reset_postgres_schema(settings.database_url)
        if request.param == "plain_owner":
            _admin(f"DROP OWNED BY {OWNER_PROBE_ROLE}", f"DROP ROLE IF EXISTS {OWNER_PROBE_ROLE}")


def _seed_legacy_deployment() -> None:
    """A deployment at the base revision: edited catalog, a custom plan, tenants."""
    now = datetime.now(UTC)
    catalog = {key: list(terms) for key, terms in CATALOG_DEFAULTS.items()}
    catalog["professional"][2] = {"audio_seconds": 360000, "concurrent_jobs": 3}
    catalog["business"][1] = False
    catalog["enterprise"][0] = "Enterprise (negotiated)"
    catalog["enterprise"][2] = {"audio_seconds": 900000, "seats": 50}
    catalog["acme_custom"] = ["Acme Custom", False, {"audio_seconds": 42}]
    with inspection_session_factory()() as db:
        for key, (name, is_public, entitlements) in catalog.items():
            db.execute(
                LEGACY_PLANS.insert().values(
                    key=key,
                    name=name,
                    is_public=is_public,
                    is_active=key != "on_premise",
                    entitlements_json=entitlements,
                    display_json={"badge": "x"} if key == "professional" else {},
                    created_at=now,
                    updated_at=now,
                )
            )
        for index, plan_key in enumerate(("demo", "acme_custom")):
            organization = Organization(name=f"Legacy {index}", slug=f"legacy-{index}")
            workspace = Workspace(organization=organization, name=f"Legacy {index}", slug="default")
            db.add_all([organization, workspace])
            db.flush()
            db.execute(
                LEGACY_SUBSCRIPTIONS.insert().values(
                    id=f"00000000-0000-0000-0000-00000000000{index}",
                    workspace_id=workspace.id,
                    plan_key=plan_key,
                    status="TRIALING" if plan_key == "demo" else "ACTIVE",
                    provider="internal" if plan_key == "demo" else "manual",
                    starts_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                Entitlement(
                    workspace_id=workspace.id,
                    metric="audio_seconds",
                    source="demo" if plan_key == "demo" else "contract",
                    limit_value=5000 if plan_key == "demo" else 42,
                    used_value=0,
                    reserved_value=0,
                    period_start=now,
                )
            )
        db.commit()


def _assert_head_matches_the_legacy_rows(
    plans: dict[str, dict], subscriptions: dict[str, dict], entitlements: dict[str, dict]
) -> None:
    """AC9: every row kept; one ``legacy_snapshot`` v1 per plan; pins at 1."""
    plans_now = _table_rows("plans", "key")
    assert _project(plans_now, plans) == plans
    assert {row["current_version"] for row in plans_now.values()} == {1}
    with inspection_session_factory()() as db:
        versions = (
            db.execute(
                text(
                    "SELECT plan_key, version, name, is_public, source, "
                    "entitlements_json::text AS entitlements_text FROM plan_versions"
                )
            )
            .mappings()
            .all()
        )
    assert sorted(row["plan_key"] for row in versions) == sorted(plans)
    for row in versions:
        plan = plans[row["plan_key"]]
        assert (row["version"], row["source"]) == (1, "legacy_snapshot")
        assert (row["name"], row["is_public"], row["entitlements_text"]) == (
            plan["name"],
            plan["is_public"],
            plan["entitlements_json__text"],
        )
    subscriptions_now = _table_rows("subscriptions", "id")
    assert _project(subscriptions_now, subscriptions) == subscriptions
    assert {row["plan_version"] for row in subscriptions_now.values()} == {1}
    assert _project(_table_rows("entitlements", "id"), entitlements) == entitlements


@pytest.mark.postgres
def test_migration_snapshots_a_legacy_deployment_and_round_trips(
    legacy_database: str, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_legacy_deployment()
    plans = _table_rows("plans", "key")
    subscriptions = _table_rows("subscriptions", "id")
    entitlements = _table_rows("entitlements", "id")
    assert "acme_custom" in plans and len(subscriptions) == 2

    # --- AC9: upgrade ---
    _alembic(legacy_database, monkeypatch, "upgrade", VERSIONED_PLANS_REVISION)
    _assert_head_matches_the_legacy_rows(plans, subscriptions, entitlements)
    for table, column in (("plans", "current_version"), ("subscriptions", "plan_version")):
        assert (
            _scalar(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column",
                table=table,
                column=column,
            )
            is None
        ), f"{table}.{column} kept its migration default"
    assert (
        _scalar("SELECT count(*) FROM pg_constraint WHERE conname = 'fk_subscription_plan_version'")
        == 1
    )
    # apply_row_level_security ran: plan_versions is append-only for the app role.
    privileges = {
        privilege: _scalar(
            "SELECT has_table_privilege(:role, 'plan_versions', :privilege)",
            role=APP_ROLE,
            privilege=privilege,
        )
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
    }
    assert privileges == {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": False}

    # The backfill is idempotent.
    config = Config(str(REPO_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision(VERSIONED_PLANS_REVISION)
    engine = create_engine(legacy_database)
    try:
        with engine.begin() as connection:
            assert revision.module.backfill_plan_versions(connection) == 0
    finally:
        engine.dispose()
    _assert_head_matches_the_legacy_rows(plans, subscriptions, entitlements)

    # The first application start afterwards writes nothing (the application
    # runs on the full schema, so later revisions are applied first).
    _alembic(legacy_database, monkeypatch, "upgrade", "head")
    app_settings = settings.model_copy(update={"auto_create_schema": False})
    catalog_before = _catalog_state()
    _restart(app_settings)
    assert _catalog_state() == catalog_before
    _assert_head_matches_the_legacy_rows(plans, subscriptions, entitlements)

    # --- AC10: a published v2 and a pinned sign-up, then downgrade ---
    _publish(
        "professional",
        entitlements={"audio_seconds": 720000, "concurrent_jobs": 5},
        name="Professional v2",
    )
    with get_session_factory()() as db:
        user = User(email="pinned@example.com", display_name="Pinned")
        db.add(user)
        db.flush()
        pinned_workspace = create_personal_workspace(db, user=user, settings=app_settings).id
        db.commit()
    reset_database_state()
    assert _grant(pinned_workspace)[0] == [("demo", 1)]
    plans_head = _table_rows("plans", "key")
    assert plans_head["professional"]["current_version"] == 2
    subscriptions_head = _table_rows("subscriptions", "id")
    entitlements_head = _table_rows("entitlements", "id")

    _alembic(legacy_database, monkeypatch, "downgrade", BASE_REVISION)
    assert _scalar("SELECT to_regclass('public.plan_versions')") is None
    assert "current_version" not in _columns("plans")
    assert "plan_version" not in _columns("subscriptions")
    assert (
        _scalar("SELECT count(*) FROM pg_constraint WHERE conname = 'fk_subscription_plan_version'")
        == 0
    )
    plans_down = _table_rows("plans", "key")
    assert plans_down == _project(plans_head, plans_down)
    assert plans_down["professional"]["name"] == "Professional v2"
    assert json.loads(plans_down["professional"]["entitlements_json__text"]) == {
        "audio_seconds": 720000,
        "concurrent_jobs": 5,
    }
    subscriptions_down = _table_rows("subscriptions", "id")
    assert subscriptions_down == _project(subscriptions_head, subscriptions_down)
    entitlements_down = _table_rows("entitlements", "id")
    assert entitlements_down == _project(entitlements_head, entitlements_down)

    # Upgrading again yields the AC9 state for the downgraded rows.
    _alembic(legacy_database, monkeypatch, "upgrade", VERSIONED_PLANS_REVISION)
    _assert_head_matches_the_legacy_rows(plans_down, subscriptions_down, entitlements_down)


def test_the_versioned_plans_revision_is_the_only_child_of_the_base() -> None:
    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    children = [
        revision.revision
        for revision in script.walk_revisions()
        if revision.down_revision == BASE_REVISION
    ]
    assert children == [VERSIONED_PLANS_REVISION]
    revision = script.get_revision(VERSIONED_PLANS_REVISION)
    assert callable(revision.module.backfill_plan_versions)
    source = Path(revision.path).read_text(encoding="utf-8")
    assert "apply_row_level_security" in source


# --- AC11: tenancy ------------------------------------------------------------------------------


@pytest.mark.postgres
def test_a_tenant_session_sees_only_its_own_pinned_subscription(
    client: TestClient, email_outbox: list[dict[str, str]]
) -> None:
    one = _signup(client, email_outbox, "pin-one@example.com")
    _publish("demo", entitlements={"audio_seconds": 1800, "concurrent_jobs": 2, "seats": 1})
    two = _signup(client, email_outbox, "pin-two@example.com")
    for tenant, version in ((one, 1), (two, 2)):
        with get_session_factory()() as db:
            bind_tenant_context(db, workspace_id=tenant["workspace_id"])
            rows = db.execute(
                select(Subscription.workspace_id, Subscription.plan_key, Subscription.plan_version)
            ).all()
            assert [tuple(row) for row in rows] == [(tenant["workspace_id"], "demo", version)]


# --- AC12: authorization unchanged ------------------------------------------------------------


def test_the_plan_catalog_has_no_write_route(
    client: TestClient, email_outbox: list[dict[str, str]]
) -> None:
    owner = _signup(client, email_outbox, "catalog-write-owner@example.com")
    member = _signup(client, email_outbox, "catalog-write-member@example.com")
    with inspection_session_factory()() as db:
        db.add(
            Membership(
                workspace_id=owner["workspace_id"],
                user_id=member["user_id"],
                role=WorkspaceRole.MEMBER,
            )
        )
        db.commit()
    state = _catalog_state()
    headers = {
        "X-Workspace-ID": owner["workspace_id"],
        "X-CSRF-Token": owner["cookies"][CSRF_COOKIE],
    }
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = client.request(
            method,
            "/api/v1/billing/plans",
            cookies=owner["cookies"],
            headers=headers,
            json={"key": "professional", "entitlements": {"seats": 99}},
        )
        assert response.status_code == 405, (method, response.text)
    assert _catalog_state() == state

    allowed = client.get("/api/v1/billing/plans", cookies=owner["cookies"], headers=headers)
    assert allowed.status_code == 200
    denied = client.get(
        "/api/v1/billing/plans",
        cookies=member["cookies"],
        headers={"X-Workspace-ID": owner["workspace_id"]},
    )
    assert denied.status_code == 403

    for route in client.app.routes:
        assert getattr(route, "endpoint", None) is not publish_plan_version
    for path in (BACKEND_ROOT / "routers").rglob("*.py"):
        assert "publish_plan_version" not in path.read_text(encoding="utf-8"), path


# --- AC13: the SQLite fallback -------------------------------------------------------------------


def test_the_sqlite_fallback_creates_the_schema_and_versions_plans(
    tmp_path: Path, settings: Settings
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fallback.db'}")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            ensure_plan_catalog(db, settings)
            db.commit()
            ensure_plan_catalog(db, settings)
            db.commit()
            created = publish_plan_version(db, "professional", entitlements={"seats": 5})
            db.commit()
            assert created.version == 2
            assert db.scalar(select(func.count()).select_from(PlanVersion)) == 6
            assert db.get(Plan, "professional").entitlements_json == {"seats": 5}
    finally:
        engine.dispose()
