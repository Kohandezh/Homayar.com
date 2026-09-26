from __future__ import annotations

import os
import shutil
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.database.relational import reset_database_state
from backend.main import create_app
from backend.services.rate_limit_service import RateLimiter
from backend.tests._database import (
    REQUIRE_POSTGRES_ENV,
    TEST_APP_ROLE,
    TEST_DATABASE_ENV,
    configured_database_url,
    describe_database_url,
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
