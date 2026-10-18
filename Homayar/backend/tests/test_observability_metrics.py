"""Operational metrics, access rules and correlation ids (HOM-113, ADR 0036)."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy import select

from backend.config import Settings
from backend.database.relational import reset_database_state
from backend.database.saas_models import AuditEvent, JobState, ProcessingJob, ProviderHealth
from backend.jobs.processor import process_job
from backend.main import create_app
from backend.observability import heartbeats
from backend.observability.correlation import (
    correlation_scope,
    current_correlation_id,
    job_correlation_id,
    request_correlation_id,
)
from backend.observability.metrics import failure_category, metrics_access, quantile
from backend.observability.release import code_migration_head
from backend.observability.scrub import scrub_text
from backend.services.logging_service import JSONFormatter
from backend.tests._database import inspection_session_factory
from backend.tests._transport import configured_redis_url
from backend.tests.conftest import register_user
from backend.tests.test_job_processor import _providers, _queued_upload

REPO_ROOT = Path(__file__).resolve().parents[3]
TOKEN = "metrics-bearer-token-for-tests-0123456789abcdef"  # noqa: S105 - test value


@pytest.fixture
def settings(settings: Settings) -> Settings:
    # Every scrape recomputes the state, so a test sees its own writes.
    return settings.model_copy(update={"metrics_cache_seconds": 0})


@pytest.fixture
def heartbeat_store():
    store = heartbeats.MemoryHeartbeatStore()
    heartbeats.use_heartbeat_store(store)
    yield store
    heartbeats.use_heartbeat_store(None)


@contextmanager
def _app_client(settings: Settings) -> Iterator[TestClient]:
    try:
        with TestClient(create_app(settings)) as client:
            yield client
    finally:
        reset_database_state()


def _scrape(client, **headers) -> dict[str, list[tuple[dict[str, str], float]]]:
    response = client.get("/metrics", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/plain")
    samples: dict[str, list[tuple[dict[str, str], float]]] = {}
    for family in text_string_to_metric_families(response.text):
        for sample in family.samples:
            samples.setdefault(sample.name, []).append((dict(sample.labels), sample.value))
    return samples


def _value(samples, name: str, **labels: str) -> float | None:
    for sample_labels, value in samples.get(name, []):
        if all(sample_labels.get(key) == wanted for key, wanted in labels.items()):
            return value
    return None


# --- scrape-time state -----------------------------------------------------------------


def test_metrics_expose_queue_jobs_dependencies_heartbeats_and_build_identity(
    client, email_outbox, settings, monkeypatch, heartbeat_store
):
    heartbeats.record_run(
        "retention_purge",
        started=1000.0,
        finished=1002.5,
        result={"purges_failed": 2, "purges_completed": 7, "workspace": "not-an-int"},
    )
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)

    queued = _scrape(client)
    assert _value(queued, "homayar_jobs", state="queued") == 1
    assert _value(queued, "homayar_jobs", state="dead_letter") == 0
    assert _value(queued, "homayar_jobs_queued_due") == 1
    assert _value(queued, "homayar_jobs_oldest_queued_due_age_seconds") >= 0
    assert _value(queued, "homayar_dependency_up", dependency="database") == 1
    assert _value(queued, "homayar_dependency_up", dependency="storage") == 1
    assert _value(queued, "homayar_dispatch_outbox", state="pending") == 1
    assert _value(
        queued, "homayar_periodic_task_result", task="retention_purge", field="purges_failed"
    ) == 2
    assert _value(
        queued, "homayar_periodic_task_last_success_timestamp_seconds", task="retention_purge"
    ) == 1002.5
    assert _value(queued, "homayar_periodic_task_last_duration_seconds", task="retention_purge") \
        == 2.5
    assert _value(
        queued, "homayar_periodic_task_interval_seconds", task="retention_purge"
    ) == settings.retention_purge_interval_seconds
    assert ("workspace" not in {labels.get("field") for labels, _ in queued.get(
        "homayar_periodic_task_result", [])})
    build = queued["homayar_build_info"][0][0]
    assert build["migration_head"] == code_migration_head()
    assert set(build) == {
        "version", "source_sha", "profile", "migration_head", "python_version", "uv_lock_sha256"
    }
    assert _value(
        queued,
        "homayar_http_requests_total",
        method="POST",
        route="/api/v1/uploads",
        status_class="2xx",
    ) == 1

    assert process_job(job_id, worker_id="metrics-test", settings=settings,
                       providers=_providers())["status"] == "completed"
    done = _scrape(client)
    assert _value(done, "homayar_jobs", state="completed") == 1
    assert _value(done, "homayar_jobs_queued_due") == 0
    assert _value(done, "homayar_jobs_finished_15m", outcome="completed") == 1
    assert _value(done, "homayar_job_turnaround_seconds_15m", quantile="0.95") >= 0
    assert _value(done, "homayar_jobs_retried_15m") == 0

    text = client.get("/metrics").text
    # No tenant, job or user identity is ever a label or a value.
    for identity in (workspace_id, job_id, "owner@example.com"):
        assert identity not in text


def test_failed_jobs_are_counted_by_bounded_category_and_affected_workspaces(
    client, email_outbox, settings, monkeypatch
):
    job_id, _ = _queued_upload(client, email_outbox, monkeypatch)
    with inspection_session_factory()() as db:  # DBA write: park the job
        job = db.get(ProcessingJob, job_id)
        job.state = JobState.DEAD_LETTER
        job.failure_class = "transient"
        job.failure_code = "asr_unavailable"
        job.attempt_count = 3
        job.completed_at = datetime.now(UTC)
        db.add(
            ProviderHealth(
                endpoint_key="e" * 64,
                stage="asr",
                provider="openai_compatible",
                model="whisper-large",
                consecutive_failures=6,
                open_until=datetime.now(UTC) + timedelta(minutes=1),
                last_failure_code="asr_unavailable",
                last_failure_at=datetime.now(UTC),
            )
        )
        db.commit()

    samples = _scrape(client)
    assert _value(samples, "homayar_jobs_failed_15m", outcome="dead_letter",
                  category="provider") == 1
    assert _value(samples, "homayar_jobs_failed_by_class_15m", outcome="dead_letter",
                  failure_class="transient") == 1
    assert _value(samples, "homayar_jobs_retried_15m") == 1
    assert _value(samples, "homayar_workspaces_with_failed_jobs_15m") == 1
    assert _value(samples, "homayar_provider_circuits_open", stage="asr") == 1
    assert _value(samples, "homayar_provider_consecutive_failures_max", stage="asr") == 6
    assert _value(samples, "homayar_provider_endpoints_failed_15m", stage="asr") == 1
    # The provider's model and endpoint never become labels.
    assert "whisper-large" not in client.get("/metrics").text


def test_route_labels_are_templates_not_raw_paths(client):
    meeting_id = str(uuid.uuid4())
    client.get(f"/api/v1/meetings/{meeting_id}")
    client.get(f"/no-such-path/{meeting_id}")
    samples = _scrape(client)
    routes = {labels["route"] for labels, _ in samples["homayar_http_requests_total"]}
    assert "/api/v1/meetings/{meeting_id}" in routes
    assert "unmatched" in routes
    assert not any(meeting_id in route for route in routes)


# --- access rules ----------------------------------------------------------------------


@pytest.fixture
def token_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"metrics_bearer_token": TOKEN})


def test_metrics_require_the_configured_bearer_token(token_settings):
    with _app_client(token_settings) as client:
        missing = client.get("/metrics")
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code \
            == 401
        assert client.get("/metrics", headers={"Authorization": f"Basic {TOKEN}"}).status_code \
            == 401
        accepted = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
        assert accepted.status_code == 200
        assert "homayar_build_info" in accepted.text
        assert TOKEN not in accepted.text


def test_disabled_metrics_answer_404(settings):
    with _app_client(settings.model_copy(update={"metrics_enabled": False})) as client:
        assert client.get("/metrics").status_code == 404


@pytest.mark.parametrize(
    ("environment", "token", "opt_out", "expected"),
    [
        ("development", "", False, "open"),
        ("test", "", False, "open"),
        ("staging", "", False, "disabled"),
        ("production", "", False, "disabled"),
        ("production", "", True, "open"),
        ("production", TOKEN, False, "token"),
        ("development", TOKEN, False, "token"),
    ],
)
def test_metrics_access_policy(settings, environment, token, opt_out, expected):
    configured = settings.model_copy(
        update={
            "environment": environment,
            "metrics_bearer_token": token,
            "metrics_allow_unauthenticated": opt_out,
        }
    )
    assert metrics_access(configured) == expected


def test_staging_without_token_refuses_and_logs_once(settings, caplog):
    from backend.database.relational import create_schema_for_test_or_development
    from backend.services.logging_service import logger

    logger.disabled = False  # Alembic's fileConfig in other tests disables it
    create_schema_for_test_or_development(settings)  # staging never creates schema itself
    staging = settings.model_copy(update={"environment": "staging", "auto_create_schema": False})
    with caplog.at_level(logging.WARNING, logger="homayar"):
        with _app_client(staging) as client:
            assert client.get("/metrics").status_code == 404
    assert "METRICS_ENDPOINT_DISABLED" in caplog.text
    opted_out = staging.model_copy(update={"metrics_allow_unauthenticated": True})
    with _app_client(opted_out) as client:
        assert client.get("/metrics").status_code == 200


def test_a_short_metrics_token_is_refused():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="METRICS_BEARER_TOKEN"):
        Settings(_env_file=None, metrics_bearer_token="short")  # noqa: S106 - test value


def test_the_proxy_never_routes_metrics_to_the_api():
    config = (REPO_ROOT / "infra/nginx/conf.d/homayar.conf").read_text(encoding="utf-8")
    blocks = re.findall(r"location\s+(=|\^~)?\s*(\S+)\s*\{(.*?)\n    \}", config, re.S)
    assert blocks
    api_paths = [path for _, path, body in blocks if "$api_upstream" in body or
                 "upload-proxy.conf" in body]
    assert api_paths and all(
        path == "/readyz" or path.startswith("/api/v1") for path in api_paths
    ), api_paths
    catch_all = [body for _, path, body in blocks if path == "/"]
    assert len(catch_all) == 1 and "$public_upstream" in catch_all[0]


# --- heartbeats ------------------------------------------------------------------------


def test_heartbeat_records_success_failure_and_bounded_counters(heartbeat_store):
    seen: list[str | None] = []

    def sweep():
        seen.append(current_correlation_id())
        return {"scanned": 3, "recovered": 1, "Bad-Key": 9, "flag": True, "note": "x"}

    assert heartbeats.run_with_heartbeat("lease_recovery", sweep)["scanned"] == 3
    assert seen[0] and seen[0].startswith("sweep:lease_recovery:")
    beat = heartbeat_store.read_all()["lease_recovery"]
    assert json.loads(beat["result"]) == {"recovered": 1, "scanned": 3}
    assert "last_failure" not in beat

    def broken():
        raise RuntimeError("database password=" + "h" * 7 + " unreachable")

    with pytest.raises(RuntimeError):
        heartbeats.run_with_heartbeat("lease_recovery", broken)
    beat = heartbeat_store.read_all()["lease_recovery"]
    assert beat["last_error_type"] == "RuntimeError"
    assert "h" * 7 not in json.dumps(beat)
    assert float(beat["last_failure"]) >= float(beat["last_success"])


def test_an_unreachable_heartbeat_store_never_fails_the_sweep():
    class Broken:
        def write(self, task, fields):
            raise ConnectionError("redis down")

        def read_all(self):
            raise ConnectionError("redis down")

    heartbeats.use_heartbeat_store(Broken())
    try:
        assert heartbeats.run_with_heartbeat("dispatch_outbox", lambda: {"scanned": 0}) == {
            "scanned": 0
        }
    finally:
        heartbeats.use_heartbeat_store(None)
    with pytest.raises(ValueError):
        heartbeats.record_run("not_a_task", started=0.0, finished=1.0)


@pytest.mark.redis
def test_redis_heartbeat_store_round_trip():
    url = configured_redis_url()
    store = heartbeats.RedisHeartbeatStore(url)
    store.write("media_reconciliation", {"last_success": "123.000", "result": '{"missing": 1}'})
    assert store.read_all()["media_reconciliation"]["last_success"] == "123.000"
    import redis

    client = redis.Redis.from_url(url)
    try:
        ttl = client.ttl(heartbeats.HEARTBEAT_KEY_PREFIX + "media_reconciliation")
        assert 0 < ttl <= heartbeats.HEARTBEAT_TTL_SECONDS
    finally:
        client.delete(heartbeats.HEARTBEAT_KEY_PREFIX + "media_reconciliation")
        client.close()


# --- correlation ids -------------------------------------------------------------------


def test_request_id_is_the_audit_correlation_id_when_the_caller_passes_none(
    client, email_outbox
):
    register_user(client, email_outbox, email="corr@example.com", verify=False)
    response = client.post(
        "/api/v1/auth/verify-email",
        json={"token": email_outbox[-1]["token"]},
        headers={"X-Request-ID": "corr-request-0001"},
    )
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "corr-request-0001"
    with inspection_session_factory()() as db:
        registered = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "auth.register"))
        verified = db.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "auth.email_verified")
        )
    # auth_service passes no request id: the bound correlation id fills it.
    assert registered is not None and registered.request_id
    assert verified is not None and verified.request_id == "corr-request-0001"


def test_processing_audit_events_carry_the_job_correlation_id(
    client, email_outbox, settings, monkeypatch
):
    from backend.jobs import processor
    from backend.jobs.tasks import process_meeting_job

    job_id, _ = _queued_upload(client, email_outbox, monkeypatch)
    original = processor.process_job
    monkeypatch.setattr(
        processor,
        "process_job",
        lambda job, worker_id: original(
            job, worker_id=worker_id, settings=settings, providers=_providers()
        ),
    )
    process_meeting_job.apply(args=(job_id,)).get()
    with inspection_session_factory()() as db:
        completed = db.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "meeting.processing.completed")
        )
    assert completed is not None
    assert completed.request_id == job_correlation_id(job_id)


def test_correlation_ids_are_bounded_and_scoped():
    assert request_correlation_id("ok-request-123") == "ok-request-123"
    generated = request_correlation_id("bad value with spaces")
    assert uuid.UUID(generated)
    assert request_correlation_id("x" * 81) != "x" * 81
    assert current_correlation_id() is None
    with correlation_scope("outer-0001"):
        with correlation_scope("inner-0001"):
            assert current_correlation_id() == "inner-0001"
        assert current_correlation_id() == "outer-0001"
    assert current_correlation_id() is None


# --- diagnostics never carry credentials -----------------------------------------------


def test_json_logs_carry_the_correlation_id_and_no_credentials():
    formatter = JSONFormatter()
    password, bearer, other = "p" * 11, "b" * 16, "h" * 7
    record = logging.LogRecord(
        "homayar", logging.ERROR, __file__, 1,
        "upstream %s failed with Authorization: Bearer " + bearer + " and password=%s",
        (f"postgresql://homayar:{password}@db:5432/homayar", other),
        None,
    )
    with correlation_scope("log-corr-0001"):
        from backend.observability.correlation import CorrelationLogFilter

        CorrelationLogFilter().filter(record)
    entry = json.loads(formatter.format(record))
    assert entry["correlation_id"] == "log-corr-0001"
    for secret in (password, bearer, other):
        assert secret not in entry["message"]
    assert "postgresql://homayar:[redacted]@db:5432/homayar" in entry["message"]


# Built at runtime from short parts so no literal looks like a real secret.
_JWT = ".".join(["eyJ" + "hbGciOiJub25lIn0", "eyJ" + "zdWIiOiIxIn0", "c2ln" + "bmF0dXJl"])
_HOMAYAR_KEY = "hk_" + "0123abcd" + "." + "Q" * 24
_PROVIDER_KEY = "sk-" + "live" + "x" * 12


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("cookie: homayar_session=" + "c" * 6 + "; other=1", "c" * 6),
        ("token=" + _JWT, "bmF0dXJl"),
        (f"key {_HOMAYAR_KEY} used", "Q" * 24),
        (f"provider said {_PROVIDER_KEY}", "x" * 12),
        ('{"client_' + 'secret": "' + "z" * 10 + '"}', "z" * 10),
        ("redis://:" + "r" * 10 + "@redis:6379/0", "r" * 10),
    ],
)
def test_scrub_removes_credential_values(raw, leaked):
    assert leaked not in scrub_text(raw)


def test_scrub_keeps_ordinary_diagnostics():
    line = "JOB_DISPATCH_SWEEP {'scanned': 3, 'published': 3} job_id=abc stage=asr"
    assert scrub_text(line) == line


# --- pure helpers ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failure_class", "code", "expected"),
    [
        ("publication", "publication_failed", "broker"),
        ("abandoned", "lease_expired", "worker_lost"),
        ("deadline", "job_deadline_exceeded", "deadline"),
        ("transient", "asr_unavailable", "provider"),
        ("transient", "provider_circuit_open", "provider"),
        ("transient", "object_not_found", "storage"),
        ("permanent", "media_decode_timeout", "media"),
        ("transient", "attempt_timeout", "timeout"),
        ("transient", "processing_failed", "other"),
        (None, None, "other"),
    ],
)
def test_failure_category_is_bounded(failure_class, code, expected):
    assert failure_category(failure_class, code) == expected


def test_quantile_is_nearest_rank():
    assert quantile([], 0.95) == 0.0
    assert quantile([5.0], 0.5) == 5.0
    values = [float(v) for v in range(1, 101)]
    assert quantile(values, 0.5) == 50.0
    assert quantile(values, 0.95) == 95.0
    assert quantile(values, 1.0) == 100.0
