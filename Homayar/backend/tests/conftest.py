from __future__ import annotations

import os
import shutil
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, update

from backend.config import Settings
from backend.database.relational import reset_database_state
from backend.database.saas_models import AuditEvent, Plan, PlanVersion, Subscription
from backend.main import create_app
from backend.services.audit_event_types import lookup_audit_event_type
from backend.services.rate_limit_service import RateLimiter
from backend.tests._database import (
    REQUIRE_POSTGRES_ENV,
    TEST_APP_ROLE,
    TEST_DATABASE_ENV,
    configured_database_url,
    describe_database_url,
    inspection_session_factory,
    is_postgres_url,
    postgres_required,
    test_database_url,
)
from backend.tests._live_s3 import live_s3_unavailable_reason
from backend.tests._transport import APP_REDIS_URL, TEST_REDIS_ENV, configured_redis_url

REQUIRE_MEDIA_TOOLS_ENV = "HOMAYAR_REQUIRE_MEDIA_TOOLS"


def media_tools_missing() -> list[str]:
    return [name for name in ("ffprobe", "ffmpeg") if shutil.which(name) is None]


def media_tools_required() -> bool:
    return os.environ.get(REQUIRE_MEDIA_TOOLS_ENV, "").strip().lower() in {"1", "true", "yes"}


def pytest_sessionstart(session: pytest.Session) -> None:
    missing_media_tools = media_tools_missing()
    if missing_media_tools and media_tools_required():
        raise pytest.UsageError(
            f"{REQUIRE_MEDIA_TOOLS_ENV} is set but {', '.join(missing_media_tools)} "
            "missing from PATH; refusing to skip the ffmpeg-marked media tests"
        )
    configured = configured_database_url()
    if postgres_required() and not is_postgres_url(configured):
        raise pytest.UsageError(
            f"{REQUIRE_POSTGRES_ENV} is set but {TEST_DATABASE_ENV} is not a PostgreSQL URL; "
            "refusing to run the suite on SQLite"
        )
    if configured is not None:
        if not is_postgres_url(configured):
            raise pytest.UsageError(
                f"{TEST_DATABASE_ENV} must be a postgresql+psycopg:// URL, got "
                f"{describe_database_url(configured)}"
            )
        from sqlalchemy import create_engine, text

        engine = create_engine(configured, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                version = connection.execute(text("SHOW server_version")).scalar()
        except Exception as exc:  # pragma: no cover - configuration error path
            raise pytest.UsageError(
                f"cannot connect to {TEST_DATABASE_ENV}={describe_database_url(configured)}: {exc}"
            ) from exc
        finally:
            engine.dispose()
        session.config.stash[_DB_BACKEND] = (
            f"postgresql {version} ({describe_database_url(configured)})"
        )
    else:
        session.config.stash[_DB_BACKEND] = (
            f"sqlite (per-test temporary file; set {TEST_DATABASE_ENV} for PostgreSQL)"
        )


_DB_BACKEND = pytest.StashKey[str]()


def pytest_report_header(config: pytest.Config) -> str:
    return f"database backend: {config.stash.get(_DB_BACKEND, 'unknown')}"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skips: list[tuple[str, pytest.MarkDecorator]] = []
    if not is_postgres_url(configured_database_url()):
        reason = f"requires PostgreSQL ({TEST_DATABASE_ENV} unset)"
        skips.append(("postgres", pytest.mark.skip(reason=reason)))
    if configured_redis_url() is None:
        reason = f"requires a disposable Redis ({TEST_REDIS_ENV} unset)"
        skips.append(("redis", pytest.mark.skip(reason=reason)))
    if media_tools_missing():
        reason = "requires the ffprobe and ffmpeg binaries on PATH"
        skips.append(("ffmpeg", pytest.mark.skip(reason=reason)))
    live_s3_reason = live_s3_unavailable_reason()
    if live_s3_reason is not None:
        reason = f"NOT_RUN/BLOCKED_EXTERNAL: {live_s3_reason}"
        skips.append(("live_s3", pytest.mark.skip(reason=reason)))
    for item in items:
        for marker, skip in skips:
            if item.get_closest_marker(marker) is not None:
                item.add_marker(skip)


# HOM-185 runtime guard: every audit row the ORM inserts or updates during a
# test must carry a registered event type and that type's registered target
# type. The listener only records; the autouse fixture fails the test, so a
# job that swallows exceptions cannot hide a bad write.
_audit_violations: list[str] = []


def audit_write_violation(event_type: str, target_type: str | None) -> str | None:
    registered = lookup_audit_event_type(event_type)
    if registered is None:
        return (
            f"audit event_type {event_type!r} with target_type {target_type!r}: "
            "not in the registry (backend/services/audit_event_types.py)"
        )
    if target_type != registered.target_type:
        return (
            f"audit event_type {event_type!r} with target_type {target_type!r}: "
            f"registered target_type is {registered.target_type!r}"
        )
    return None


@event.listens_for(AuditEvent, "before_insert")
@event.listens_for(AuditEvent, "before_update")
def _record_audit_write(_mapper, _connection, target: AuditEvent) -> None:
    violation = audit_write_violation(target.event_type, target.target_type)
    if violation is not None:
        _audit_violations.append(violation)


def fail_on_unregistered_audit_writes(violations: list[str]) -> None:
    if violations:
        pytest.fail("unregistered audit writes: " + "; ".join(violations), pytrace=False)


@pytest.fixture(autouse=True)
def audit_event_guard() -> Generator[list[str], None, None]:
    _audit_violations.clear()
    yield _audit_violations
    found = list(_audit_violations)
    _audit_violations.clear()
    fail_on_unregistered_audit_writes(found)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        deployment_mode="cloud",
        database_url=test_database_url(tmp_path),
        database_app_role=TEST_APP_ROLE,
        redis_url=APP_REDIS_URL,
        jwt_secret="j" * 48,
        token_pepper="p" * 48,
        auto_create_schema=True,
        tasks_eager=True,
        secure_cookies=False,
        enable_free_demo=True,
        demo_quota_seconds=1800,
        storage_backend="local",
        storage_local_root=str(tmp_path / "objects"),
        upload_quarantine_dir=str(tmp_path / "quarantine"),
        upload_dir=str(tmp_path / "legacy-uploads"),
        enable_phone_auth=True,
        asanak_api_key="test-only-key",
        asanak_sender="TEST",
        enable_google_auth=False,
        cors_origins="http://testserver",
        trusted_hosts="testserver,localhost,127.0.0.1",
        frontend_url="http://testserver/app",
        public_url="http://testserver",
        _env_file=None,
    )


@pytest.fixture
def email_outbox() -> list[dict[str, str]]:
    return []


@pytest.fixture
def sms_outbox() -> list[dict[str, str]]:
    return []


@pytest.fixture
def client(
    settings: Settings,
    email_outbox: list[dict[str, str]],
    sms_outbox: list[dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    from backend.routers.v1 import auth as auth_router

    async def capture_verification(user, token, _settings):
        email_outbox.append({"kind": "verify", "user_id": user.id, "token": token})

    async def capture_reset(user, token, _settings):
        email_outbox.append({"kind": "reset", "user_id": user.id, "token": token})

    def capture_sms(phone: str, code: str, _settings):
        sms_outbox.append({"phone": phone, "code": code})

    monkeypatch.setattr(auth_router, "_send_verification_message", capture_verification)
    monkeypatch.setattr(auth_router, "_send_reset_message", capture_reset)
    monkeypatch.setattr(auth_router, "_send_asanak_code", capture_sms)
    RateLimiter.reset_memory()
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
    reset_database_state()


def register_user(
    client: TestClient,
    email_outbox: list[dict[str, str]],
    *,
    email: str = "owner@example.com",
    password: str = "SecurePass123",
    name: str = "Workspace Owner",
    verify: bool = True,
) -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "display_name": name,
            "locale": "fa",
        },
    )
    assert response.status_code == 202, response.text
    assert email_outbox and email_outbox[-1]["kind"] == "verify"
    if verify:
        verified = client.post(
            "/api/v1/auth/verify-email",
            json={"token": email_outbox[-1]["token"]},
        )
        assert verified.status_code == 200, verified.text
    return {"email": email, "password": password}


def login_user(client: TestClient, credentials: dict[str, str]) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "identifier": credentials["email"],
            "password": credentials["password"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def pin_negotiated_plan(workspace_id: str, key: str, terms: object) -> None:
    """An operator pins one workspace's subscription to a private plan ``key``.

    The plan (version 1 holding ``terms``) is created on first use; other
    workspaces keep their pins.
    """
    with inspection_session_factory()() as db:
        if db.get(Plan, key) is None:
            db.add(
                Plan(
                    key=key,
                    name=key,
                    is_public=False,
                    is_active=True,
                    entitlements_json=terms,
                    display_json={},
                    current_version=1,
                )
            )
            db.flush()
            db.add(
                PlanVersion(
                    plan_key=key,
                    version=1,
                    name=key,
                    is_public=False,
                    entitlements_json=terms,
                    source="operator",
                )
            )
            db.flush()
        db.execute(
            update(Subscription)
            .where(Subscription.workspace_id == workspace_id)
            .values(plan_key=key, plan_version=1)
        )
        db.commit()


def grant_admin_seats(workspace_id: str, seats: int = 50) -> None:
    """Give one workspace room for admins (HOM-167 F1).

    The free demo has one admin seat, which the owner holds.  Tests that add
    admins to a demo workspace pin it to a negotiated plan with ``seats`` seats.
    """
    pin_negotiated_plan(workspace_id, f"negotiated_seats_{seats}", {"seats": seats})
