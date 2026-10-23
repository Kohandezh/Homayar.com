"""Operator system-health summary: GET /api/v1/admin/system-health (HOM-227)."""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import redis
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update

from backend.backup import metrics as backup_metrics
from backend.config import Settings
from backend.database.relational import reset_database_state
from backend.database.saas_models import (
    Meeting,
    Membership,
    Organization,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptSource,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.main import create_app
from backend.observability import heartbeats, metrics, system_health
from backend.observability.release import code_migration_head, runtime_identity
from backend.security.authorization import API_KEY_SCOPES, Permission
from backend.security.context import CSRF_COOKIE, require_platform_operator_read
from backend.services.rate_limit_service import RateLimiter
from backend.tests._database import TEST_APP_ROLE, inspection_session_factory
from backend.tests._transport import configured_redis_url
from backend.tests.conftest import login_user, register_user
from backend.tests.test_authorization_matrix import _insert_meeting, _route_policy
from backend.tests.test_job_processor import _queued_upload

API = "/api/v1/admin/system-health"
OPERATOR_EMAIL = "platform-operator@example.com"
SECRET_REDIS_URL = "redis://:redis-secret-password-4417@127.0.0.1:1/0"  # noqa: S105 - test value
SECRET_STORAGE_BUCKET = "secret-bucket-name-4417"  # noqa: S105 - test value
SECRET_ACCESS_KEY = "AKIASECRETACCESSKEY4417"  # noqa: S105 - test value
SECRET_SECRET_KEY = "secret-storage-secret-key-4417-abcdefghijklmnop"  # noqa: S105 - test value
BACKUP_MAX_AGE_SECONDS = 93600.0  # 26 h, the backup age alert
# A deadline far above every real wait in these tests: a bug that makes a request
# wait out its whole deadline then costs 30 s, which the wide bound below catches.
LONG_DEADLINE_SECONDS = 30.0
# Far below the 30 s of the long deadline and of the hung fakes' gate.wait(30), far
# above a loaded runner's request overhead; the only upper time limit left in the file.
WIDE_BOUND_SECONDS = 10.0

STATUSES = {"ok", "degraded", "down"}
DEPENDENCY_STATUSES = {"up", "down", "timeout"}
SCHEMA_STATUSES = {"up_to_date", "behind", "unstamped", "unknown"}
SWEEP_STATUSES = {"ok", "stale", "failing", "never_run", "unknown"}
BACKUP_STATUSES = {"ok", "stale", "missing", "not_configured", "unknown"}
AUDIT_SINK_STATUSES = {"ok", "stale", "failing", "never_run", "not_configured", "unknown"}
ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


# --- fixtures and helpers ----------------------------------------------------------------


@pytest.fixture
def settings(settings: Settings) -> Settings:
    # Secret-bearing settings that must never reach the response body.
    return settings.model_copy(
        update={
            "redis_url": SECRET_REDIS_URL,
            "storage_bucket": SECRET_STORAGE_BUCKET,
            "storage_access_key": SECRET_ACCESS_KEY,
            "storage_secret_key": SECRET_SECRET_KEY,
        }
    )


@pytest.fixture(autouse=True)
def no_backup_textfile(monkeypatch) -> None:
    monkeypatch.delenv(system_health.BACKUP_TEXTFILE_ENV, raising=False)


@pytest.fixture
def heartbeat_store() -> Iterator[heartbeats.MemoryHeartbeatStore]:
    store = heartbeats.MemoryHeartbeatStore()
    heartbeats.use_heartbeat_store(store)
    yield store
    heartbeats.use_heartbeat_store(None)


class _FakeRedis:
    def __init__(self, queue_length: int) -> None:
        self.queue_length = queue_length

    def ping(self) -> bool:
        return True

    def llen(self, _name: str) -> int:
        return self.queue_length

    def close(self) -> None:
        pass


@pytest.fixture
def redis_up(monkeypatch) -> _FakeRedis:
    fake = _FakeRedis(queue_length=4)
    monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, url, **kwargs: fake))
    return fake


@pytest.fixture
def gate() -> Iterator[threading.Event]:
    """Holds hung fakes; released at teardown so no test thread outlives the test long."""
    event = threading.Event()
    yield event
    event.set()


@dataclass(frozen=True)
class _RecordedWait:
    """One ``system_health.wait`` call: the futures it got and how it ended."""

    futures: frozenset
    timeout: float | None
    ran_out: bool  # not_done was non-empty: the wait ended before every future did


class _WaitRecorder:
    """What the ``waits`` fixture recorded; ``clear`` starts a fresh record."""

    def __init__(self) -> None:
        self.calls: list[_RecordedWait] = []

    def clear(self) -> None:
        self.calls.clear()


@pytest.fixture
def waits(monkeypatch) -> _WaitRecorder:
    """Wraps ``system_health.wait``: same call, same result, every call recorded."""
    recorder = _WaitRecorder()
    real_wait = system_health.wait

    def recording(futures, timeout=None, **rest):
        done, not_done = real_wait(futures, timeout=timeout, **rest)
        recorder.calls.append(
            _RecordedWait(futures=frozenset(futures), timeout=timeout, ran_out=bool(not_done))
        )
        return done, not_done

    monkeypatch.setattr(system_health, "wait", recording)
    return recorder


@pytest.fixture
def long_deadline(client, settings) -> system_health.SystemHealthCollector:
    """The app's collector with a 30 s deadline, in place before the first health
    request; the app's lifespan closes it at shutdown, as for the default one."""
    collector = system_health.SystemHealthCollector(settings, deadline=LONG_DEADLINE_SECONDS)
    client.app.state.system_health_collector = collector
    return collector


def _set_system_role(email: str, system_role: str) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(User).where(User.email == email).values(system_role=system_role))
        db.commit()


def _set_membership(workspace_id: str, **values) -> None:
    with inspection_session_factory()() as db:
        db.execute(
            update(Membership).where(Membership.workspace_id == workspace_id).values(**values)
        )
        db.commit()


def _operator(client, email_outbox, *, email: str = OPERATOR_EMAIL, operator: bool = True) -> str:
    """Register and sign in a workspace owner; make them a platform operator."""
    session = login_user(client, register_user(client, email_outbox, email=email))
    if operator:
        _set_system_role(email, "operator")
    return session["workspaces"][0]["id"]


def _get(client, workspace_id: str):
    return client.get(API, headers={"X-Workspace-ID": workspace_id})


def _health(client, workspace_id: str) -> dict:
    response = _get(client, workspace_id)
    assert response.status_code == 200, response.text
    return response.json()


def _timed(client, workspace_id: str) -> tuple[dict, float]:
    started = time.monotonic()
    response = _get(client, workspace_id)
    elapsed = time.monotonic() - started
    assert response.status_code == 200, response.text
    return response.json(), elapsed


def _error(response) -> str:
    return response.json()["error"]["code"]


def _stamp(revision: str | None) -> None:
    """Give the test database an Alembic stamp (``None``: no ``alembic_version`` table)."""
    with inspection_session_factory()() as db:
        postgres = db.get_bind().dialect.name == "postgresql"
        db.execute(text("DROP TABLE IF EXISTS alembic_version"))
        if revision is not None:
            db.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
            )
            db.execute(
                text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": revision}
            )
            if postgres:
                db.execute(text(f'GRANT SELECT ON alembic_version TO "{TEST_APP_ROLE}"'))
        db.commit()


def _beat(store, task: str, **fields: float | str) -> None:
    store.write(task, {key: str(value) for key, value in fields.items()})


def _fresh_sweeps(store) -> None:
    now = time.time()
    for task in heartbeats.PERIODIC_TASKS:
        _beat(store, task, last_run=now - 1, last_success=now - 1, last_duration_seconds=0.25)


def _backup_file(tmp_path: Path, monkeypatch, timestamps: dict[str, float] | None) -> Path:
    path = tmp_path / "textfile" / "homayar_backup.prom"
    if timestamps is not None:
        backup_metrics.record_success(path, timestamps)
    monkeypatch.setenv(system_health.BACKUP_TEXTFILE_ENV, str(path))
    return path


def _iso(moment: float) -> str:
    return datetime.fromtimestamp(moment, UTC).isoformat().replace("+00:00", "Z")


def _assert_iso_or_none(value) -> None:
    assert value is None or ISO_UTC.match(value), value


def _health_threads(name: str) -> set[threading.Thread]:
    return {
        thread
        for thread in threading.enumerate()
        if thread.name == f"homayar-health-{name}" and thread.is_alive()
    }


def _no_wait_ran_out(waits: _WaitRecorder) -> None:
    """No recorded wait ended with a future still pending (R1, R5)."""
    assert not any(wait.ran_out for wait in waits.calls), waits.calls


def _within_one_deadline(waits: _WaitRecorder) -> None:
    """Every recorded wait asked to wait at most one deadline, with no tolerance (R2)."""
    for wait in waits.calls:
        assert wait.timeout is not None, wait
        assert wait.timeout <= system_health.DEADLINE_SECONDS, wait


def _not_waited_on(waits: _WaitRecorder, future) -> None:
    """The given future was never given to a wait (R3)."""
    for wait in waits.calls:
        assert future not in wait.futures, wait


# --- 1. the contract ---------------------------------------------------------------------


def test_operator_gets_every_key_with_the_contract_vocabularies(
    client, email_outbox, heartbeat_store, redis_up, tmp_path, monkeypatch
):
    workspace_id = _operator(client, email_outbox)
    _stamp(code_migration_head())
    _fresh_sweeps(heartbeat_store)
    now = time.time()
    _backup_file(tmp_path, monkeypatch, {"postgres": now - 60, "objects": now - 60, "offsite": now})

    response = _get(client, workspace_id)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()

    assert set(body) == {
        "status",
        "checked_at",
        "dependencies",
        "schema",
        "queue",
        "sweeps",
        "backup",
        "audit_sink",
        "build",
    }
    assert body["status"] == "ok", body
    assert ISO_UTC.match(body["checked_at"])
    checked_at = datetime.fromisoformat(body["checked_at"])
    assert abs(checked_at.timestamp() - time.time()) < 60

    assert set(body["dependencies"]) == {"database", "redis", "storage"}
    for name, dependency in body["dependencies"].items():
        assert set(dependency) == {"status", "probe_seconds"}
        assert dependency["status"] == "up", name
        assert isinstance(dependency["probe_seconds"], float) and dependency["probe_seconds"] >= 0

    assert body["schema"] == {
        "status": "up_to_date",
        "code_head": code_migration_head(),
        "database_revision": code_migration_head(),
    }
    assert body["queue"] == {
        "status": "ok",
        "queued_due": 0,
        "oldest_queued_due_age_seconds": 0.0,
        "retry_scheduled": 0,
        "broker_queue_length": 4,
    }

    sweeps = body["sweeps"]
    assert set(sweeps) == {"status", "tasks"}
    assert sweeps["status"] == "ok"
    assert [task["task"] for task in sweeps["tasks"]] == list(heartbeats.PERIODIC_TASKS)
    for task in sweeps["tasks"]:
        assert set(task) == {
            "task",
            "status",
            "expected_interval_seconds",
            "last_run_at",
            "last_success_at",
            "last_failure_at",
            "last_failure_class",
            "last_duration_seconds",
        }
        assert task["status"] == "ok", task
        assert task["expected_interval_seconds"] == float(
            heartbeats.expected_interval_seconds(task["task"], client.app.state.settings)
        )
        for key in ("last_run_at", "last_success_at"):
            assert ISO_UTC.match(task[key]), task
        assert task["last_failure_at"] is None and task["last_failure_class"] is None
        assert task["last_duration_seconds"] == 0.25

    backup = body["backup"]
    assert backup["status"] == "ok"
    assert set(backup["targets"]) == {"postgres", "objects", "offsite"}
    for value in backup["targets"].values():
        assert ISO_UTC.match(value)

    assert body["audit_sink"] == {
        "status": "not_configured",
        "last_verify_at": None,
        "last_verify_success_at": None,
        "last_failure_class": None,
    }

    identity = runtime_identity()
    assert body["build"] == {
        key: identity[key]
        for key in ("version", "source_sha", "migration_head", "profile", "python_version")
    }


def test_vocabularies_hold_when_every_dependency_is_missing(client, email_outbox):
    # No fakes: the configured Redis (port 1) refuses, no heartbeat store answers.
    workspace_id = _operator(client, email_outbox)
    body = _health(client, workspace_id)
    assert body["status"] in STATUSES
    assert body["status"] == "degraded"
    for dependency in body["dependencies"].values():
        assert dependency["status"] in DEPENDENCY_STATUSES
    assert body["dependencies"]["redis"]["status"] == "down"
    assert body["schema"]["status"] in SCHEMA_STATUSES
    assert body["schema"]["status"] == "unstamped"  # the test schema is built by create_all
    assert body["queue"]["status"] == "ok" and body["queue"]["broker_queue_length"] is None
    assert body["sweeps"]["status"] == "unknown"
    for task in body["sweeps"]["tasks"]:
        assert task["status"] in SWEEP_STATUSES
    assert body["backup"]["status"] in BACKUP_STATUSES
    assert body["backup"]["status"] == "not_configured"
    assert body["backup"]["targets"] == {"postgres": None, "objects": None, "offsite": None}
    assert body["audit_sink"]["status"] in AUDIT_SINK_STATUSES


def test_the_route_declares_the_operator_read_policy(client):
    routes = [
        route
        for route in client.app.routes
        if isinstance(route, APIRoute) and route.path == API and "GET" in route.methods
    ]
    assert len(routes) == 1
    assert _route_policy(routes[0]) == "workspace:manage +operator"
    dependency = require_platform_operator_read(Permission.WORKSPACE_MANAGE)
    assert dependency.homayar_permission is Permission.WORKSPACE_MANAGE
    assert dependency.homayar_write is False
    assert dependency.homayar_platform is True


# --- 2. deny-cases and their allow-controls ------------------------------------------------


def test_no_session_is_401_and_an_operator_session_is_200(client, email_outbox, heartbeat_store):
    workspace_id = _operator(client, email_outbox)
    assert _get(client, workspace_id).status_code == 200
    client.cookies.clear()
    denied = _get(client, workspace_id)
    assert denied.status_code == 401, denied.text


def test_a_workspace_owner_who_is_not_an_operator_is_refused(client, email_outbox, heartbeat_store):
    workspace_id = _operator(client, email_outbox, operator=False)
    denied = _get(client, workspace_id)
    assert denied.status_code == 403
    assert _error(denied) == "platform_operator_required"
    _set_membership(workspace_id, role=WorkspaceRole.ADMIN)
    denied = _get(client, workspace_id)
    assert denied.status_code == 403
    assert _error(denied) == "platform_operator_required"

    _set_system_role(OPERATOR_EMAIL, "operator")  # allow-control: the same user, now operator
    assert _get(client, workspace_id).status_code == 200


@pytest.mark.parametrize("role", [WorkspaceRole.MEMBER, WorkspaceRole.VIEWER])
def test_an_operator_without_workspace_manage_is_refused(
    client, email_outbox, heartbeat_store, role
):
    workspace_id = _operator(client, email_outbox)
    _set_membership(workspace_id, role=role)
    denied = _get(client, workspace_id)
    assert denied.status_code == 403
    assert _error(denied) == "permission_denied"

    _set_membership(workspace_id, role=WorkspaceRole.ADMIN)  # allow-control
    assert _get(client, workspace_id).status_code == 200


def test_an_api_key_of_an_operator_is_refused_even_with_every_scope(
    client, email_outbox, heartbeat_store
):
    workspace_id = _operator(client, email_outbox)
    minted = client.post(
        "/api/v1/api-keys",
        json={
            "name": "operator-key",
            "scopes": sorted(scope.value for scope in API_KEY_SCOPES),
            "expires_in_days": 30,
        },
        headers={"X-Workspace-ID": workspace_id, "X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )
    assert minted.status_code == 201, minted.text
    raw_key = minted.json()["key"]
    assert _get(client, workspace_id).status_code == 200  # allow-control: the session

    cookies = dict(client.cookies)
    client.cookies.clear()
    denied = client.get(API, headers={"Authorization": f"Bearer {raw_key}"})
    assert denied.status_code == 403, denied.text
    assert _error(denied) in {"api_key_scope_denied", "platform_operator_required"}
    client.cookies.update(cookies)
    assert _get(client, workspace_id).status_code == 200


def test_the_operator_check_refuses_an_api_key_actor_by_itself():
    # require_permission refuses API keys first (workspace:manage is never a key scope);
    # the operator check refuses them on its own as well.
    dependency = require_platform_operator_read(Permission.WORKSPACE_MANAGE)

    def context(*, api_key, system_role):
        user = SimpleNamespace(system_role=system_role)
        return SimpleNamespace(actor=SimpleNamespace(api_key=api_key, user=user))

    with pytest.raises(HTTPException) as refused:
        asyncio.run(dependency(context=context(api_key=object(), system_role="operator")))
    assert refused.value.status_code == 403
    assert refused.value.detail["code"] == "platform_operator_required"
    with pytest.raises(HTTPException) as refused:
        asyncio.run(dependency(context=context(api_key=None, system_role="user")))
    assert refused.value.detail["code"] == "platform_operator_required"
    allowed = context(api_key=None, system_role="operator")
    assert asyncio.run(dependency(context=allowed)) is allowed


def test_an_operator_whose_membership_is_revoked_is_refused(client, email_outbox, heartbeat_store):
    workspace_id = _operator(client, email_outbox)
    _set_membership(workspace_id, is_active=False)
    denied = _get(client, workspace_id)
    assert denied.status_code in {403, 404}
    assert _error(denied) == "workspace_access_denied"

    _set_membership(workspace_id, is_active=True)  # allow-control
    assert _get(client, workspace_id).status_code == 200


# --- 3. a source that is down (raises) ----------------------------------------------------


def test_a_down_database_is_down_and_the_rest_stays_correct(
    client, email_outbox, heartbeat_store, redis_up, monkeypatch, waits, long_deadline
):
    workspace_id = _operator(client, email_outbox)
    _fresh_sweeps(heartbeat_store)

    def broken(snapshot, factory):
        raise ConnectionError("database at postgresql://user:pw@db:5432 refused")

    monkeypatch.setattr(metrics, "collect_database", broken)
    body, elapsed = _timed(client, workspace_id)
    assert elapsed < WIDE_BOUND_SECONDS
    _no_wait_ran_out(waits)  # a source that raises at once does not eat the deadline
    assert body["status"] == "down"
    database = body["dependencies"]["database"]
    assert database["status"] == "down" and isinstance(database["probe_seconds"], float)
    assert body["schema"] == {
        "status": "unknown",
        "code_head": code_migration_head(),
        "database_revision": None,
    }
    assert body["queue"] == {
        "status": "unknown",
        "queued_due": None,
        "oldest_queued_due_age_seconds": None,
        "retry_scheduled": None,
        "broker_queue_length": 4,
    }
    assert body["dependencies"]["redis"]["status"] == "up"
    assert body["dependencies"]["storage"]["status"] == "up"
    assert body["sweeps"]["status"] == "ok"
    assert "refused" not in str(body) and "postgresql" not in str(body)


def test_a_down_redis_is_down_and_only_the_broker_length_is_missing(
    client, email_outbox, heartbeat_store, waits, long_deadline
):
    # The configured Redis (port 1) refuses the connection.
    workspace_id = _operator(client, email_outbox)
    _stamp(code_migration_head())
    _fresh_sweeps(heartbeat_store)
    body, elapsed = _timed(client, workspace_id)
    assert elapsed < WIDE_BOUND_SECONDS
    _no_wait_ran_out(waits)  # a source that raises at once does not eat the deadline
    redis_probe = body["dependencies"]["redis"]
    assert redis_probe["status"] == "down" and isinstance(redis_probe["probe_seconds"], float)
    assert body["queue"]["status"] == "ok"
    assert body["queue"]["queued_due"] == 0 and body["queue"]["broker_queue_length"] is None
    assert body["dependencies"]["database"]["status"] == "up"
    assert body["schema"]["status"] == "up_to_date"
    assert body["sweeps"]["status"] == "ok"
    assert body["status"] == "degraded"


def test_a_down_object_store_is_down(
    client, email_outbox, heartbeat_store, redis_up, monkeypatch, waits, long_deadline
):
    workspace_id = _operator(client, email_outbox)
    _stamp(code_migration_head())
    _fresh_sweeps(heartbeat_store)

    class Refusing:
        def exists(self, tenant, key):
            raise OSError("bucket secret-bucket refused")

    monkeypatch.setattr(metrics, "probe_storage", lambda settings: Refusing())
    body, elapsed = _timed(client, workspace_id)
    assert elapsed < WIDE_BOUND_SECONDS
    _no_wait_ran_out(waits)  # a source that raises at once does not eat the deadline
    storage = body["dependencies"]["storage"]
    assert storage["status"] == "down" and isinstance(storage["probe_seconds"], float)
    assert body["dependencies"]["database"]["status"] == "up"
    assert body["dependencies"]["redis"]["status"] == "up"
    assert body["status"] == "degraded"


def test_a_down_heartbeat_store_makes_every_sweep_unknown(
    client, email_outbox, redis_up, monkeypatch, waits, long_deadline
):
    workspace_id = _operator(client, email_outbox)
    _stamp(code_migration_head())

    class Refusing:
        def write(self, task, fields):
            raise ConnectionError("down")

        def read_all(self):
            raise ConnectionError("redis://:secret@heartbeats refused")

    heartbeats.use_heartbeat_store(Refusing())
    try:
        body, elapsed = _timed(client, workspace_id)
    finally:
        heartbeats.use_heartbeat_store(None)
    assert elapsed < WIDE_BOUND_SECONDS
    _no_wait_ran_out(waits)  # a source that raises at once does not eat the deadline
    assert body["sweeps"]["status"] == "unknown"
    assert [task["task"] for task in body["sweeps"]["tasks"]] == list(heartbeats.PERIODIC_TASKS)
    for task in body["sweeps"]["tasks"]:
        assert task["status"] == "unknown"
        assert task["expected_interval_seconds"] > 0
        for key in (
            "last_run_at",
            "last_success_at",
            "last_failure_at",
            "last_failure_class",
            "last_duration_seconds",
        ):
            assert task[key] is None
    assert body["audit_sink"]["status"] == "not_configured"
    assert body["dependencies"]["database"]["status"] == "up"
    assert body["status"] == "degraded"


def test_a_failing_backup_read_is_unknown(
    client, email_outbox, heartbeat_store, redis_up, tmp_path, monkeypatch, waits, long_deadline
):
    workspace_id = _operator(client, email_outbox)
    _backup_file(tmp_path, monkeypatch, {"postgres": time.time()})

    def broken(path):
        raise ValueError("could not convert string to float: '1.2.3'")

    monkeypatch.setattr(backup_metrics, "read_values", broken)
    body, elapsed = _timed(client, workspace_id)
    assert elapsed < WIDE_BOUND_SECONDS
    _no_wait_ran_out(waits)  # a source that raises at once does not eat the deadline
    assert body["backup"] == {
        "status": "unknown",
        "targets": {"postgres": None, "objects": None, "offsite": None},
    }
    assert body["status"] == "degraded"


# --- 3/4. a source that hangs: bounded, and never started twice -----------------------------


def _assert_hung_twice(
    client, workspace_id: str, source: str, calls: list[int], waits: _WaitRecorder
) -> list[dict]:
    before = _health_threads(source)
    bodies = []
    started: list[set[threading.Thread]] = []
    for _ in range(2):
        waits.clear()
        body, elapsed = _timed(client, workspace_id)
        assert elapsed < WIDE_BOUND_SECONDS, elapsed
        _within_one_deadline(waits)  # a hung source is bounded by one deadline, no more
        bodies.append(body)
        started.append(_health_threads(source) - before)
    assert calls == [1]  # the second request reports it at once and starts no thread
    assert len(started[0]) == 1 and started[1] == started[0]
    return bodies


def test_a_hung_database_times_out_and_is_not_restarted(
    client, email_outbox, heartbeat_store, redis_up, gate, monkeypatch, waits
):
    workspace_id = _operator(client, email_outbox)
    _fresh_sweeps(heartbeat_store)
    calls: list[int] = []

    def hung(snapshot, factory):
        calls.append(1)
        gate.wait(30)
        return True

    monkeypatch.setattr(metrics, "collect_database", hung)
    for body in _assert_hung_twice(client, workspace_id, "database", calls, waits):
        assert body["dependencies"]["database"] == {"status": "timeout", "probe_seconds": None}
        assert body["schema"]["status"] == "unknown"
        assert body["queue"]["status"] == "unknown" and body["queue"]["queued_due"] is None
        assert body["queue"]["broker_queue_length"] == 4
        assert body["dependencies"]["redis"]["status"] == "up"
        assert body["dependencies"]["storage"]["status"] == "up"
        assert body["sweeps"]["status"] == "ok"
        assert body["status"] == "down"


def test_a_hung_redis_times_out_and_is_not_restarted(
    client, email_outbox, heartbeat_store, gate, monkeypatch, waits
):
    workspace_id = _operator(client, email_outbox)
    _stamp(code_migration_head())
    _fresh_sweeps(heartbeat_store)
    calls: list[int] = []

    def hung(snapshot, settings):
        calls.append(1)
        gate.wait(30)

    monkeypatch.setattr(metrics, "collect_redis", hung)
    for body in _assert_hung_twice(client, workspace_id, "redis", calls, waits):
        assert body["dependencies"]["redis"] == {"status": "timeout", "probe_seconds": None}
        assert body["queue"]["status"] == "ok" and body["queue"]["broker_queue_length"] is None
        assert body["dependencies"]["database"]["status"] == "up"
        assert body["schema"]["status"] == "up_to_date"
        assert body["status"] == "degraded"


def test_a_hung_object_store_times_out_and_is_not_restarted(
    client, email_outbox, heartbeat_store, redis_up, gate, monkeypatch, waits
):
    workspace_id = _operator(client, email_outbox)
    _stamp(code_migration_head())
    _fresh_sweeps(heartbeat_store)
    calls: list[int] = []

    class Hung:
        def exists(self, tenant, key):
            calls.append(1)
            gate.wait(30)
            return False

    monkeypatch.setattr(metrics, "probe_storage", lambda settings: Hung())
    for body in _assert_hung_twice(client, workspace_id, "storage", calls, waits):
        assert body["dependencies"]["storage"] == {"status": "timeout", "probe_seconds": None}
        assert body["dependencies"]["database"]["status"] == "up"
        assert body["dependencies"]["redis"]["status"] == "up"
        assert body["schema"]["status"] == "up_to_date"
        assert body["status"] == "degraded"


def test_a_hung_heartbeat_store_is_unknown_and_is_not_restarted(
    client, email_outbox, redis_up, gate, waits
):
    workspace_id = _operator(client, email_outbox)
    _stamp(code_migration_head())
    calls: list[int] = []

    class Hung:
        def write(self, task, fields):
            pass

        def read_all(self):
            calls.append(1)
            gate.wait(30)
            return {}

    heartbeats.use_heartbeat_store(Hung())
    try:
        bodies = _assert_hung_twice(client, workspace_id, "heartbeats", calls, waits)
    finally:
        heartbeats.use_heartbeat_store(None)
    for body in bodies:
        assert body["sweeps"]["status"] == "unknown"
        assert {task["status"] for task in body["sweeps"]["tasks"]} == {"unknown"}
        assert body["dependencies"]["database"]["status"] == "up"
        assert body["status"] == "degraded"


def test_a_hung_backup_file_is_unknown_and_is_not_restarted(
    client, email_outbox, heartbeat_store, redis_up, tmp_path, monkeypatch, waits
):
    # A FIFO without a writer blocks the reader in open(): a real hung file read.
    workspace_id = _operator(client, email_outbox)
    path = tmp_path / "hung.prom"
    os.mkfifo(path)
    monkeypatch.setenv(system_health.BACKUP_TEXTFILE_ENV, str(path))
    calls: list[int] = []
    read_values = backup_metrics.read_values

    def counted(target):
        calls.append(1)
        return read_values(target)

    monkeypatch.setattr(backup_metrics, "read_values", counted)
    try:
        for body in _assert_hung_twice(client, workspace_id, "backup", calls, waits):
            assert body["backup"] == {
                "status": "unknown",
                "targets": {"postgres": None, "objects": None, "offsite": None},
            }
            assert body["dependencies"]["database"]["status"] == "up"
            assert str(path) not in str(body)
    finally:
        # Opening the write end releases the reader (it then reads an empty file).
        os.close(os.open(path, os.O_WRONLY | os.O_NONBLOCK))


def test_hung_queue_aggregates_are_unknown_and_not_restarted(
    client, email_outbox, heartbeat_store, redis_up, gate, monkeypatch, waits
):
    workspace_id = _operator(client, email_outbox)
    _stamp(code_migration_head())
    _fresh_sweeps(heartbeat_store)
    calls: list[int] = []

    def hung(factory, *, now):
        calls.append(1)
        gate.wait(30)
        return 0, 0.0, 0

    monkeypatch.setattr(metrics, "read_queue_depth", hung)
    for body in _assert_hung_twice(client, workspace_id, "jobs", calls, waits):
        assert body["queue"] == {
            "status": "unknown",
            "queued_due": None,
            "oldest_queued_due_age_seconds": None,
            "retry_scheduled": None,
            "broker_queue_length": 4,
        }
        assert body["dependencies"]["database"]["status"] == "up"
        assert body["schema"]["status"] == "up_to_date"
        assert body["status"] == "degraded"


def test_every_source_hung_at_once_still_answers_within_one_deadline(
    client, email_outbox, gate, tmp_path, monkeypatch, waits
):
    workspace_id = _operator(client, email_outbox)

    def hang(*args, **kwargs):
        gate.wait(30)

    class HungStore:
        def write(self, task, fields):
            pass

        def read_all(self):
            gate.wait(30)
            return {}

    monkeypatch.setattr(metrics, "collect_database", hang)
    monkeypatch.setattr(metrics, "collect_redis", hang)
    monkeypatch.setattr(metrics, "collect_storage", hang)
    monkeypatch.setattr(backup_metrics, "read_values", hang)
    _backup_file(tmp_path, monkeypatch, None)
    heartbeats.use_heartbeat_store(HungStore())
    try:
        for _ in range(2):
            waits.clear()
            body, elapsed = _timed(client, workspace_id)
            assert elapsed < WIDE_BOUND_SECONDS, elapsed
            _within_one_deadline(waits)  # even every source hung is bounded by one deadline
    finally:
        heartbeats.use_heartbeat_store(None)
    assert {dependency["status"] for dependency in body["dependencies"].values()} == {"timeout"}
    assert body["schema"]["status"] == "unknown"
    assert body["queue"]["status"] == "unknown"
    assert body["sweeps"]["status"] == "unknown"
    assert body["backup"]["status"] == "unknown"
    assert body["status"] == "down"


def test_a_released_source_is_probed_again(
    client, email_outbox, heartbeat_store, redis_up, gate, monkeypatch
):
    workspace_id = _operator(client, email_outbox)
    _stamp(code_migration_head())
    calls: list[int] = []

    class Slow:
        def exists(self, tenant, key):
            calls.append(1)
            gate.wait(30)
            return False

    monkeypatch.setattr(metrics, "probe_storage", lambda settings: Slow())
    assert _health(client, workspace_id)["dependencies"]["storage"]["status"] == "timeout"
    gate.set()
    for _ in range(50):
        if not _health_threads("storage"):
            break
        time.sleep(0.02)
    # The finished probe is not reused: the next request starts a fresh one.
    assert _health(client, workspace_id)["dependencies"]["storage"]["status"] == "up"
    assert calls == [1, 1]


def test_a_hung_metrics_scrape_does_not_block_the_summary(
    client, email_outbox, heartbeat_store, redis_up, gate, monkeypatch
):
    workspace_id = _operator(client, email_outbox)
    entered = threading.Event()

    def hung(self):
        entered.set()
        gate.wait(30)

    # collect() runs refresh() inside the /metrics collector's lock: patching the
    # class method (the app's collector is a local in create_app) makes the scrape
    # hold that lock until gate is set, with no time limit at all.
    monkeypatch.setattr(metrics.StateCollector, "refresh", hung)
    scrape = threading.Thread(target=client.get, args=("/metrics",), daemon=True)
    scrape.start()
    try:
        assert entered.wait(10)
        body = _health(client, workspace_id)
        assert body["dependencies"]["database"]["status"] == "up"  # the summary answered
        assert scrape.is_alive()  # while the scrape still held the collector lock
    finally:
        gate.set()
        scrape.join(10)


@pytest.mark.parametrize("source", ["database", "storage"])
def test_a_source_still_hung_from_an_earlier_request_is_reported_at_once(
    client, email_outbox, heartbeat_store, redis_up, gate, monkeypatch, source, waits
):
    workspace_id = _operator(client, email_outbox)
    _stamp(code_migration_head())
    _fresh_sweeps(heartbeat_store)

    def hung(*args):
        gate.wait(30)

    monkeypatch.setattr(metrics, f"collect_{source}", hung)
    first, elapsed = _timed(client, workspace_id)
    assert first["dependencies"][source]["status"] == "timeout"
    assert elapsed >= system_health.DEADLINE_SECONDS  # this request started it and waited

    collector = client.app.state.system_health_collector
    hung_call = collector._inflight[source]
    waits.clear()
    second = collector.collect()
    # Still running from the first request: reported at once, not waited on again.
    assert collector._inflight[source] is hung_call  # no new call was started
    _not_waited_on(waits, hung_call.future)
    assert second["dependencies"][source] == {"status": "timeout", "probe_seconds": None}
    for other in {"database", "redis", "storage"} - {source}:
        assert second["dependencies"][other]["status"] == "up"
    if source == "database":
        assert second["queue"]["status"] == "unknown"
    waits.clear()
    body = _health(client, workspace_id)
    assert body["dependencies"][source]["status"] == "timeout"
    assert collector._inflight[source] is hung_call  # no new call was started
    _not_waited_on(waits, hung_call.future)


def test_a_call_started_by_an_earlier_request_never_answers_a_later_one(
    client, email_outbox, redis_up, gate, monkeypatch
):
    workspace_id = _operator(client, email_outbox)
    release_old_call = threading.Event()
    old_call_finished: list[bool] = []

    class Slow:
        def exists(self, tenant, key):
            gate.wait(30)
            return False

    class ReleasingStore:
        """The second request's own heartbeat read lets the first request's storage
        call finish, and returns only once that call is over."""

        def write(self, task, fields):
            pass

        def read_all(self):
            if release_old_call.is_set():
                gate.set()
                for _ in range(100):
                    if not _health_threads("storage"):
                        old_call_finished.append(True)
                        break
                    time.sleep(0.002)
            return {}

    monkeypatch.setattr(metrics, "probe_storage", lambda settings: Slow())
    heartbeats.use_heartbeat_store(ReleasingStore())
    try:
        assert _health(client, workspace_id)["dependencies"]["storage"]["status"] == "timeout"
        release_old_call.set()
        body = _health(client, workspace_id)
    finally:
        heartbeats.use_heartbeat_store(None)
    # The old call ended while this request waited, after a probe longer than the
    # deadline. It was not this request's call, so it does not answer it.
    assert old_call_finished == [True]
    assert body["dependencies"]["storage"] == {"status": "timeout", "probe_seconds": None}


def _concurrent_health(client, workspace_id: str, count: int) -> list[dict]:
    """``count`` requests released at the same moment, each from its own thread."""
    barrier = threading.Barrier(count)

    def call() -> dict:
        barrier.wait(10)
        return _health(client, workspace_id)

    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(call) for _ in range(count)]
        return [future.result(30) for future in futures]


@pytest.mark.parametrize(("count", "rounds"), [(2, 20), (8, 4)])
def test_overlapping_requests_on_a_healthy_install_are_all_up(
    client, email_outbox, heartbeat_store, redis_up, count, rounds
):
    workspace_id = _operator(client, email_outbox)
    _stamp(code_migration_head())
    _fresh_sweeps(heartbeat_store)
    assert _health(client, workspace_id)["status"] == "ok"
    bodies = []
    for _ in range(rounds):
        bodies += _concurrent_health(client, workspace_id, count)
    assert len(bodies) == count * rounds
    for body in bodies:
        statuses = {name: dependency["status"] for name, dependency in body["dependencies"].items()}
        assert statuses == {"database": "up", "redis": "up", "storage": "up"}, body
        assert body["status"] != "down"


def test_a_request_shares_a_young_call_that_another_request_started(
    client, email_outbox, heartbeat_store, redis_up, monkeypatch, waits, long_deadline
):
    workspace_id = _operator(client, email_outbox)
    _stamp(code_migration_head())
    assert _health(client, workspace_id)["dependencies"]["storage"]["status"] == "up"
    collector = client.app.state.system_health_collector
    entered, shared = threading.Event(), threading.Event()
    calls: list[int] = []
    collect_storage = metrics.collect_storage

    def slow(snapshot, storage):
        calls.append(1)
        entered.set()
        shared.wait(5)  # until the second request holds this call
        time.sleep(0.1)  # then slow, but well under the deadline
        collect_storage(snapshot, storage)

    monkeypatch.setattr(metrics, "collect_storage", slow)
    first: dict = {}
    request_a = threading.Thread(target=lambda: first.update(_health(client, workspace_id)))
    request_a.start()
    assert entered.wait(5)

    start = collector._start

    def start_and_signal(name, source):
        call = start(name, source)
        if name == "storage":
            shared.set()
        return call

    monkeypatch.setattr(collector, "_start", start_and_signal)
    waits.clear()  # record from just before the second request's collect
    started = time.monotonic()
    second = collector.collect()
    elapsed = time.monotonic() - started
    request_a.join(5)
    _no_wait_ran_out(waits)  # the shared young call answered both, no deadline spent
    assert calls == [1]  # one probe answered both requests
    assert second["dependencies"]["storage"]["status"] == "up"
    assert first["dependencies"]["storage"]["status"] == "up"
    assert (
        second["dependencies"]["storage"]["probe_seconds"]
        == (first["dependencies"]["storage"]["probe_seconds"])
    )
    assert elapsed < WIDE_BOUND_SECONDS


def test_a_shared_call_that_ends_after_its_own_deadline_does_not_count(
    client, email_outbox, heartbeat_store, redis_up, tmp_path, monkeypatch
):
    workspace_id = _operator(client, email_outbox)
    _stamp(code_migration_head())
    assert _health(client, workspace_id)["dependencies"]["storage"]["status"] == "up"
    collector = client.app.state.system_health_collector
    entered, release = threading.Event(), threading.Event()
    calls: list[int] = []
    ended_late: list[bool] = []

    def held(snapshot, storage):
        calls.append(1)
        entered.set()
        release.wait(5)

    def releasing_read(path):
        # The second request's own backup read: it lets the shared storage call end
        # after that call's deadline, and returns once the call is over.
        storage_call = collector._inflight["storage"]
        while time.monotonic() <= storage_call.expires:
            time.sleep(0.005)
        release.set()
        for _ in range(200):
            if storage_call.future.done():
                ended_late.append(storage_call.finished > storage_call.expires)
                break
            time.sleep(0.005)
        return {}

    monkeypatch.setattr(metrics, "collect_storage", held)
    first: dict = {}
    request_a = threading.Thread(target=lambda: first.update(_health(client, workspace_id)))
    request_a.start()
    assert entered.wait(5)
    time.sleep(0.2)  # the second request comes while the call is still young
    monkeypatch.setattr(backup_metrics, "read_values", releasing_read)
    _backup_file(tmp_path, monkeypatch, None)  # only the second request reads the backup
    second = collector.collect()
    request_a.join(5)
    assert calls == [1]  # the second request shared the call, it started none
    assert ended_late == [True]  # done before the answer was built, but late
    assert second["dependencies"]["storage"] == {"status": "timeout", "probe_seconds": None}
    assert first["dependencies"]["storage"] == {"status": "timeout", "probe_seconds": None}


@contextmanager
def _own_app(settings: Settings, email_outbox, monkeypatch) -> Iterator[tuple[object, TestClient]]:
    """An app with a lifespan of its own (the ``client`` fixture shuts down after the test)."""
    from backend.routers.v1 import auth as auth_router

    async def capture_verification(user, token, _settings):
        email_outbox.append({"kind": "verify", "user_id": user.id, "token": token})

    monkeypatch.setattr(auth_router, "_send_verification_message", capture_verification)
    RateLimiter.reset_memory()
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            yield app, client
    finally:
        reset_database_state()


def test_each_app_has_its_own_collector(settings, email_outbox, heartbeat_store, monkeypatch):
    collectors = []
    for index in range(2):
        with _own_app(settings, email_outbox, monkeypatch) as (app, client):
            workspace_id = _operator(client, email_outbox, email=f"operator-{index}@example.com")
            assert _get(client, workspace_id).status_code == 200
            collectors.append(app.state.system_health_collector)
    assert isinstance(collectors[0], system_health.SystemHealthCollector)
    assert collectors[0] is not collectors[1]


def test_startup_loads_the_migration_head_even_without_metrics(
    settings, email_outbox, heartbeat_store, redis_up, monkeypatch
):
    # METRICS_ENABLED=false (an on-premise install without Prometheus): the first
    # summary must not load the Alembic scripts inside the database deadline.
    without_metrics = settings.model_copy(update={"metrics_enabled": False})
    code_migration_head.cache_clear()
    with _own_app(without_metrics, email_outbox, monkeypatch) as (_app, client):
        assert code_migration_head.cache_info().currsize == 1  # warmed by the lifespan
        workspace_id = _operator(client, email_outbox)
        _stamp(code_migration_head())
        body = _health(client, workspace_id)
    assert body["dependencies"]["database"]["status"] == "up"
    assert body["schema"]["status"] == "up_to_date"


def _backend_alive(pid: int) -> bool:
    with inspection_session_factory()() as db:
        return bool(
            db.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE pid = :pid"), {"pid": pid})
        )


def test_the_lifespan_closes_the_collector_and_its_connections(
    settings, email_outbox, heartbeat_store, monkeypatch
):
    with _own_app(settings, email_outbox, monkeypatch) as (app, client):
        workspace_id = _operator(client, email_outbox)
        assert _get(client, workspace_id).status_code == 200
        engine = app.state.system_health_collector._engine
        pool = engine.pool
        assert pool.checkedin() == 1  # the health engine keeps one idle connection
        pid = None
        if engine.dialect.name == "postgresql":
            with engine.connect() as connection:  # that same idle connection
                pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
            assert _backend_alive(pid)
    # Lifespan shutdown disposed the engine: its pool is empty and the server
    # connection is gone, while the app object itself is still referenced here.
    assert pool.checkedin() == 0
    if pid is not None:
        for _ in range(100):
            if not _backend_alive(pid):
                break
            time.sleep(0.02)
        assert not _backend_alive(pid)


# --- 5. schema -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("revision", "expected"),
    [("head", "up_to_date"), ("0000aaaa1111", "behind"), (None, "unstamped")],
)
def test_schema_status(client, email_outbox, heartbeat_store, redis_up, revision, expected):
    workspace_id = _operator(client, email_outbox)
    stamped = code_migration_head() if revision == "head" else revision
    _stamp(stamped)
    body = _health(client, workspace_id)
    assert body["schema"] == {
        "status": expected,
        "code_head": code_migration_head(),
        "database_revision": stamped,
    }
    if expected != "up_to_date":
        assert body["status"] == "degraded"


# --- 6. sweeps -----------------------------------------------------------------------------


def test_sweep_statuses_and_failure_classes(client, email_outbox, heartbeat_store, redis_up):
    workspace_id = _operator(client, email_outbox)
    _stamp(code_migration_head())
    settings = client.app.state.settings
    now = time.time()
    tasks = list(heartbeats.PERIODIC_TASKS)
    ok, stale, failing, never_run, recovered, unsafe, failed_only = tasks[:7]
    stale_after = max(3 * heartbeats.expected_interval_seconds(stale, settings), 240.0)

    _beat(heartbeat_store, ok, last_run=now - 5, last_success=now - 5, last_duration_seconds=1.5)
    _beat(
        heartbeat_store, stale, last_run=now - stale_after - 30, last_success=now - stale_after - 30
    )
    _beat(
        heartbeat_store,
        failing,
        last_run=now - 2,
        last_success=now - 60,
        last_failure=now - 2,
        last_error_type="TimeoutError",
    )
    _beat(
        heartbeat_store,
        recovered,
        last_run=now - 2,
        last_success=now - 2,
        last_failure=now - 60,
        last_error_type="OperationalError",
    )
    _beat(
        heartbeat_store,
        unsafe,
        last_run=now - 2,
        last_failure=now - 2,
        last_error_type="ValueError: tenant acme-secret meeting 42",
    )
    _beat(heartbeat_store, failed_only, last_run=now - 2, last_failure=now - 2)
    for task in tasks[7:]:
        _beat(heartbeat_store, task, last_run=now - 1, last_success=now - 1)

    body = _health(client, workspace_id)
    by_task = {task["task"]: task for task in body["sweeps"]["tasks"]}
    assert body["sweeps"]["status"] == "ok"
    assert by_task[ok]["status"] == "ok"
    assert by_task[ok]["last_success_at"] == _iso(now - 5)
    assert by_task[ok]["last_duration_seconds"] == 1.5
    assert by_task[stale]["status"] == "stale"
    assert by_task[failing]["status"] == "failing"
    assert by_task[failing]["last_failure_class"] == "TimeoutError"
    assert by_task[failing]["last_failure_at"] == _iso(now - 2)
    assert by_task[never_run] == {
        "task": never_run,
        "status": "never_run",
        "expected_interval_seconds": float(
            heartbeats.expected_interval_seconds(never_run, settings)
        ),
        "last_run_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "last_failure_class": None,
        "last_duration_seconds": None,
    }
    assert by_task[recovered]["status"] == "ok"
    assert by_task[recovered]["last_failure_class"] == "OperationalError"
    assert by_task[unsafe]["status"] == "failing"
    assert by_task[unsafe]["last_failure_class"] == "other"
    assert by_task[failed_only]["status"] == "failing"
    assert by_task[failed_only]["last_failure_class"] == "other"
    for task in by_task.values():
        for key in ("last_run_at", "last_success_at", "last_failure_at"):
            _assert_iso_or_none(task[key])
    assert "acme-secret" not in str(body) and "meeting 42" not in str(body)
    assert body["status"] == "degraded"


def test_unusable_heartbeat_values_are_treated_as_absent(
    client, email_outbox, heartbeat_store, redis_up
):
    workspace_id = _operator(client, email_outbox)
    task = next(iter(heartbeats.PERIODIC_TASKS))
    _beat(
        heartbeat_store,
        task,
        last_run="nan",
        last_success="1e999",
        last_failure="not-a-number",
        last_duration_seconds="inf",
    )
    entry = _health(client, workspace_id)["sweeps"]["tasks"][0]
    assert entry["task"] == task
    assert entry["status"] == "never_run"
    assert entry["last_run_at"] is None and entry["last_duration_seconds"] is None


# --- 7. backup -----------------------------------------------------------------------------


def test_backup_not_configured_without_the_variable(client, email_outbox, heartbeat_store):
    workspace_id = _operator(client, email_outbox)
    assert _health(client, workspace_id)["backup"] == {
        "status": "not_configured",
        "targets": {"postgres": None, "objects": None, "offsite": None},
    }


def test_backup_missing_file_or_no_target_line(
    client, email_outbox, heartbeat_store, tmp_path, monkeypatch
):
    workspace_id = _operator(client, email_outbox)
    path = _backup_file(tmp_path, monkeypatch, None)
    body = _health(client, workspace_id)
    assert body["backup"] == {
        "status": "missing",
        "targets": {"postgres": None, "objects": None, "offsite": None},
    }
    assert str(path) not in _get(client, workspace_id).text

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# HELP something else\nother_metric 1\n", encoding="ascii")
    assert _health(client, workspace_id)["backup"]["status"] == "missing"


def test_backup_stale_when_a_target_is_old_or_absent(
    client, email_outbox, heartbeat_store, tmp_path, monkeypatch
):
    workspace_id = _operator(client, email_outbox)
    now = time.time()
    old = now - BACKUP_MAX_AGE_SECONDS - 60
    path = _backup_file(tmp_path, monkeypatch, {"postgres": now, "objects": old, "offsite": now})
    body = _health(client, workspace_id)
    assert body["backup"]["status"] == "stale"
    assert body["backup"]["targets"]["objects"] == _iso(float(f"{old:.3f}"))
    assert body["status"] == "degraded"

    path.unlink()
    _backup_file(tmp_path, monkeypatch, {"postgres": now, "objects": now})
    body = _health(client, workspace_id)
    assert body["backup"]["status"] == "stale"
    assert body["backup"]["targets"]["offsite"] is None
    assert body["backup"]["targets"]["postgres"] == _iso(float(f"{now:.3f}"))


def test_backup_ok_when_every_target_is_fresh(
    client, email_outbox, heartbeat_store, tmp_path, monkeypatch
):
    workspace_id = _operator(client, email_outbox)
    now = time.time()
    # Just inside the 26 h limit still counts as fresh.
    edge = now - BACKUP_MAX_AGE_SECONDS + 120
    path = _backup_file(tmp_path, monkeypatch, {"postgres": now, "objects": edge, "offsite": now})
    response = _get(client, workspace_id)
    backup = response.json()["backup"]
    assert backup["status"] == "ok"
    assert backup["targets"] == {
        "postgres": _iso(float(f"{now:.3f}")),
        "objects": _iso(float(f"{edge:.3f}")),
        "offsite": _iso(float(f"{now:.3f}")),
    }
    assert str(path) not in response.text and str(tmp_path) not in response.text


# --- 8. audit sink ---------------------------------------------------------------------------


@pytest.fixture
def audit_sink_task(monkeypatch) -> str:
    task = system_health.AUDIT_SINK_VERIFY_TASK
    monkeypatch.setattr(heartbeats, "OPERATOR_TASKS", (task,))
    monkeypatch.setattr(heartbeats, "KNOWN_TASKS", (*heartbeats.PERIODIC_TASKS, task))
    return task


def test_audit_sink_is_not_configured_on_main(client, email_outbox, heartbeat_store):
    assert system_health.AUDIT_SINK_VERIFY_TASK == "audit_sink_verify"
    assert system_health.AUDIT_SINK_VERIFY_TASK not in heartbeats.KNOWN_TASKS
    workspace_id = _operator(client, email_outbox)
    assert _health(client, workspace_id)["audit_sink"] == {
        "status": "not_configured",
        "last_verify_at": None,
        "last_verify_success_at": None,
        "last_failure_class": None,
    }


def test_audit_sink_heartbeat_fills_the_fields(
    client, email_outbox, heartbeat_store, redis_up, audit_sink_task
):
    workspace_id = _operator(client, email_outbox)
    _stamp(code_migration_head())
    _fresh_sweeps(heartbeat_store)
    body = _health(client, workspace_id)
    assert body["audit_sink"] == {
        "status": "never_run",
        "last_verify_at": None,
        "last_verify_success_at": None,
        "last_failure_class": None,
    }
    assert body["status"] == "ok"  # never_run does not degrade

    # Heartbeats written by the real recorder once the task is known.
    assert heartbeats.record_run(audit_sink_task, started=1000.0, finished=1001.0)
    body = _health(client, workspace_id)
    assert body["audit_sink"] == {
        "status": "ok",  # no staleness rule: an old success is still ok
        "last_verify_at": _iso(1001.0),
        "last_verify_success_at": _iso(1001.0),
        "last_failure_class": None,
    }
    assert body["status"] == "ok"

    assert heartbeats.record_run(
        audit_sink_task, started=1002.0, finished=1003.0, error=PermissionError("secret path")
    )
    body = _health(client, workspace_id)
    assert body["audit_sink"] == {
        "status": "failing",
        "last_verify_at": _iso(1003.0),
        "last_verify_success_at": _iso(1001.0),
        "last_failure_class": "PermissionError",
    }
    assert body["status"] == "degraded"


def test_audit_sink_is_unknown_when_the_heartbeat_store_fails(
    client, email_outbox, audit_sink_task
):
    workspace_id = _operator(client, email_outbox)

    class Refusing:
        def write(self, task, fields):
            raise ConnectionError("down")

        def read_all(self):
            raise ConnectionError("down")

    heartbeats.use_heartbeat_store(Refusing())
    try:
        body = _health(client, workspace_id)
    finally:
        heartbeats.use_heartbeat_store(None)
    assert body["audit_sink"] == {
        "status": "unknown",
        "last_verify_at": None,
        "last_verify_success_at": None,
        "last_failure_class": None,
    }


# --- 9. nothing tenant-, customer- or secret-bearing in the body -------------------------------


def test_the_body_holds_no_tenant_data_secrets_paths_or_messages(
    client, email_outbox, settings, heartbeat_store, redis_up, tmp_path, monkeypatch
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    with inspection_session_factory()() as db:
        user = db.scalar(
            select(User).join(Membership).where(Membership.workspace_id == workspace_id)
        )
        workspace = db.get(Workspace, workspace_id)
        organization = db.get(Organization, workspace.organization_id)
        workspace.name = "Workspace Secret Name 4417"
        organization.name = "Organization Secret Name 4417"
        email, user_id, organization_id = user.email, user.id, organization.id
        db.commit()
    _set_system_role(email, "operator")
    meeting_id = _insert_meeting(
        workspace_id, created_by=user_id, title="Meeting Secret Title 4417"
    )
    with inspection_session_factory()() as db:
        revision = TranscriptRevision(
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            revision_number=1,
            source=TranscriptSource.ASR,
            reason="seed",
            language="fa",
            segment_count=1,
            diarization_status=None,
            is_current=True,
        )
        db.add(revision)
        db.flush()
        db.add(
            TranscriptSegment(
                workspace_id=workspace_id,
                meeting_id=meeting_id,
                revision_id=revision.id,
                sequence=0,
                speaker_key="speaker_1",
                start_ms=0,
                end_ms=900,
                text="Transcript secret words 4417",
            )
        )
        db.commit()
        assert db.get(Meeting, meeting_id) is not None
    _beat(
        heartbeat_store,
        next(iter(heartbeats.PERIODIC_TASKS)),
        last_run=time.time(),
        last_failure=time.time(),
        last_error_type=f"Error for {email} in Workspace Secret Name 4417",
    )
    textfile = _backup_file(tmp_path, monkeypatch, {"postgres": time.time()})

    response = _get(client, workspace_id)
    assert response.status_code == 200, response.text
    raw = response.text
    assert response.json()["queue"]["queued_due"] == 1  # the seeded job is counted, not named
    forbidden = [
        "Workspace Secret Name 4417",
        "Organization Secret Name 4417",
        "Meeting Secret Title 4417",
        "Transcript secret words 4417",
        email,
        user_id,
        workspace_id,
        organization_id,
        meeting_id,
        job_id,
        settings.database_url,
        SECRET_REDIS_URL,
        "redis-secret-password-4417",
        SECRET_STORAGE_BUCKET,
        SECRET_ACCESS_KEY,
        SECRET_SECRET_KEY,
        settings.jwt_secret,
        settings.token_pepper,
        settings.storage_local_root,
        str(textfile),
        str(tmp_path),
    ]
    password = settings.database_url.partition("://")[2].partition("@")[0].partition(":")[2]
    if password:
        forbidden.append(password)
    for value in forbidden:
        assert value not in raw, value


# --- a real Redis -------------------------------------------------------------------------


@pytest.mark.redis
def test_a_real_redis_answers_the_probe_and_holds_the_heartbeats(client, email_outbox, settings):
    url = configured_redis_url()
    real = settings.model_copy(update={"redis_url": url})
    workspace_id = _operator(client, email_outbox)
    # The app's own Redis URL is a refusing port; give the app a collector on the real one.
    client.app.state.system_health_collector = system_health.SystemHealthCollector(real)
    task = next(iter(heartbeats.PERIODIC_TASKS))
    key = heartbeats.HEARTBEAT_KEY_PREFIX + task
    try:
        now = time.time()
        assert heartbeats.record_run(task, started=now - 1, finished=now, settings=real)
        body = _health(client, workspace_id)
    finally:
        cleanup = redis.Redis.from_url(url)
        try:
            cleanup.delete(key)
        finally:
            cleanup.close()
    assert body["dependencies"]["redis"]["status"] == "up"
    assert isinstance(body["queue"]["broker_queue_length"], int)
    assert body["sweeps"]["tasks"][0]["status"] == "ok"
    assert body["sweeps"]["tasks"][0]["last_success_at"] == _iso(float(f"{now:.3f}"))
