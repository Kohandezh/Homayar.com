"""Operational metrics, access rules and correlation ids (HOM-113, ADR 0036)."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import textwrap
import threading
import time
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
from backend.database.saas_models import AuditEvent, JobState, ProcessingJob, ProviderHealth, User
from backend.jobs.processor import process_job
from backend.main import create_app
from backend.observability import heartbeats, metrics, scrub
from backend.observability.correlation import (
    correlation_scope,
    current_correlation_id,
    job_correlation_id,
    request_correlation_id,
)
from backend.observability.metrics import (
    StateCollector,
    failure_category,
    metrics_access,
    quantile,
)
from backend.observability.release import code_migration_head
from backend.observability.scrub import diagnostic_text, scrub_text
from backend.services.logging_service import JSONFormatter, exception_summary
from backend.tests._database import inspection_session_factory
from backend.tests._transport import configured_redis_url
from backend.tests.conftest import register_user
from backend.tests.test_job_processor import _providers, _queued_upload

REPO_ROOT = Path(__file__).resolve().parents[3]
HOMAYAR_ROOT = Path(__file__).resolve().parents[2]
TOKEN = "metrics-bearer-token-for-tests-0123456789abcdef"  # noqa: S105 - test value


@pytest.fixture
def settings(settings: Settings, monkeypatch) -> Settings:
    # A wide source deadline: the host running the suite may be heavily loaded;
    # the deadline itself is tested with explicit timeouts below.
    monkeypatch.setattr(metrics, "SOURCE_TIMEOUT_SECONDS", 30.0)
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


def _counter(name: str, **labels: str) -> float:
    """A counter's current value as /metrics reports it, in single- and multiprocess mode."""
    exposition = metrics.in_process_exposition().decode()
    for family in text_string_to_metric_families(exposition):
        for sample in family.samples:
            if sample.name == name and all(sample.labels.get(k) == v for k, v in labels.items()):
                return sample.value
    return 0.0


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
    upload_labels = {"method": "POST", "route": "/api/v1/uploads", "status_class": "2xx"}
    # In-process counters are cumulative for the whole test process: compare deltas.
    uploads_before = _counter("homayar_http_requests_total", **upload_labels)
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)

    queued = _scrape(client)
    assert _value(queued, "homayar_jobs", state="queued") == 1
    assert _value(queued, "homayar_jobs", state="dead_letter") == 0
    assert _value(queued, "homayar_jobs_queued_due") == 1
    assert _value(queued, "homayar_jobs_oldest_queued_due_age_seconds") >= 0
    assert _value(queued, "homayar_dependency_up", dependency="database") == 1
    assert _value(queued, "homayar_dependency_up", dependency="storage") == 1
    assert _value(queued, "homayar_dispatch_outbox", state="pending") == 1
    assert (
        _value(
            queued, "homayar_periodic_task_result", task="retention_purge", field="purges_failed"
        )
        == 2
    )
    assert (
        _value(
            queued, "homayar_periodic_task_last_success_timestamp_seconds", task="retention_purge"
        )
        == 1002.5
    )
    assert (
        _value(queued, "homayar_periodic_task_last_duration_seconds", task="retention_purge") == 2.5
    )
    assert (
        _value(queued, "homayar_periodic_task_interval_seconds", task="retention_purge")
        == settings.retention_purge_interval_seconds
    )
    assert "workspace" not in {
        labels.get("field") for labels, _ in queued.get("homayar_periodic_task_result", [])
    }
    build = queued["homayar_build_info"][0][0]
    assert build["migration_head"] == code_migration_head()
    assert set(build) == {
        "version",
        "source_sha",
        "profile",
        "migration_head",
        "python_version",
        "uv_lock_sha256",
    }
    assert _value(queued, "homayar_http_requests_total", **upload_labels) == uploads_before + 1

    assert (
        process_job(job_id, worker_id="metrics-test", settings=settings, providers=_providers())[
            "status"
        ]
        == "completed"
    )
    done = _scrape(client)
    assert _value(done, "homayar_jobs", state="completed") == 1
    assert _value(done, "homayar_jobs_queued_due") == 0
    assert _value(done, "homayar_jobs_finished_15m", outcome="completed") == 1
    assert _value(done, "homayar_job_turnaround_seconds_15m", quantile="0.95") >= 0
    assert _value(done, "homayar_jobs_retried_15m") == 0

    text = client.get("/metrics").text
    with inspection_session_factory()() as db:
        emails = list(db.scalars(select(User.email)))
    assert emails
    # No tenant, job or user identity is ever a label or a value.
    for identity in (workspace_id, job_id, *emails):
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
    assert (
        _value(samples, "homayar_jobs_failed_15m", outcome="dead_letter", category="provider") == 1
    )
    assert (
        _value(
            samples,
            "homayar_jobs_failed_by_class_15m",
            outcome="dead_letter",
            failure_class="transient",
        )
        == 1
    )
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
        assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert (
            client.get("/metrics", headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401
        )
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
    caplog.clear()
    opted_out = staging.model_copy(
        update={"metrics_allow_unauthenticated": True, "deployment_mode": "onprem"}
    )
    with caplog.at_level(logging.WARNING, logger="homayar"):
        with _app_client(opted_out) as client:
            assert client.get("/metrics").status_code == 200
    assert "METRICS_UNAUTHENTICATED_OPT_OUT deployment_mode=onprem" in caplog.text


def test_a_short_metrics_token_is_refused():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="METRICS_BEARER_TOKEN"):
        Settings(_env_file=None, metrics_bearer_token="short")  # noqa: S106 - test value


def test_the_proxy_never_routes_metrics_to_the_api():
    config = (REPO_ROOT / "infra/nginx/conf.d/homayar.conf").read_text(encoding="utf-8")
    blocks = re.findall(r"location\s+(=|\^~)?\s*(\S+)\s*\{(.*?)\n    \}", config, re.S)
    assert blocks
    api_paths = [
        path for _, path, body in blocks if "$api_upstream" in body or "upload-proxy.conf" in body
    ]
    assert api_paths and all(
        path == "/readyz" or path.startswith("/api/v1") for path in api_paths
    ), api_paths
    # The catch-all (a redirect or the public site) never reaches the API.
    catch_all = [body for _, path, body in blocks if path == "/"]
    assert len(catch_all) == 1
    assert "$api_upstream" not in catch_all[0] and "metrics" not in catch_all[0]


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


def test_request_id_is_the_audit_correlation_id_when_the_caller_passes_none(client, email_outbox):
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
    # one id per attempt (delivery), with the job id as the searchable prefix
    assert completed.request_id.startswith(f"job:{job_id}:")
    assert completed.request_id != job_correlation_id(job_id)


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
        "homayar",
        logging.ERROR,
        __file__,
        1,
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


# --- review fixes: bounded scrapes, workers, trusted host, opt-out, logs ---------------


def test_a_hung_dependency_bounds_the_scrape_and_is_not_restarted(client, settings, monkeypatch):
    gate = threading.Event()
    calls: list[int] = []

    def hung(snapshot, storage):
        calls.append(1)
        gate.wait(30)

    monkeypatch.setattr(metrics, "collect_storage", hung)
    collector = StateCollector(settings, cache_seconds=0, source_timeout=0.5)
    before = _counter("homayar_metrics_collection_errors_total", source="storage")
    try:
        for _ in range(2):
            started = time.monotonic()
            snapshot = collector.refresh()
            assert time.monotonic() - started < 3.0  # two stages of 0.5 s, plus margin
            storage = [
                value
                for labels, value in snapshot.families["homayar_dependency_up"][2]
                if labels == ("storage",)
            ]
            assert storage == [0.0]
            assert ("database",) in [
                labels for labels, _ in snapshot.families["homayar_dependency_up"][2]
            ]
        assert calls == [1]  # the hung probe is not started a second time
        after = _counter("homayar_metrics_collection_errors_total", source="storage")
        assert after == before + 2
    finally:
        gate.set()
        collector.close()


def test_a_down_database_and_object_store_are_reported_fast(settings):
    broken = settings.model_copy(
        update={
            "database_url": "postgresql+psycopg://nobody@127.0.0.1:1/none",
            "storage_backend": "s3",
            "storage_bucket": "probe-bucket",
            "storage_endpoint_url": "http://127.0.0.1:1",
            "storage_region": "us-east-1",
            "storage_access_key": "a" * 20,
            "storage_secret_key": "b" * 40,
            "redis_url": "redis://127.0.0.1:1/0",
        }
    )
    collector = StateCollector(broken, cache_seconds=0, source_timeout=2.0)
    try:
        started = time.monotonic()
        snapshot = collector.refresh()
        # two stages of 2 s each at most, plus margin for a heavily loaded test host
        assert time.monotonic() - started < 9.0
    finally:
        collector.close()
    up = {labels[0]: value for labels, value in snapshot.families["homayar_dependency_up"][2]}
    assert up == {"database": 0.0, "redis": 0.0, "storage": 0.0}
    assert "homayar_jobs" not in snapshot.families  # no aggregates without a database


def test_multiprocess_mode_sums_every_worker(tmp_path):
    env = {**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(tmp_path / "metrics")}
    worker = textwrap.dedent(
        """
        import sys
        from backend.observability import metrics
        for _ in range(int(sys.argv[1])):
            metrics.observe_http("GET", "/api/v1/meetings", 200, 0.01)
        """
    )
    for count in ("3", "5"):  # two uvicorn workers, as separate processes
        subprocess.run(  # noqa: S603 - this interpreter and a fixed script
            [sys.executable, "-c", worker, count], env=env, cwd=HOMAYAR_ROOT, check=True
        )
    reader = (
        "import sys; from backend.observability import metrics; "
        "sys.stdout.write(metrics.in_process_exposition().decode())"
    )
    exposition = subprocess.run(  # noqa: S603 - this interpreter and a fixed script
        [sys.executable, "-c", reader],
        env=env,
        cwd=HOMAYAR_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    samples = {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in text_string_to_metric_families(exposition)
        for sample in family.samples
    }
    key = (
        "homayar_http_requests_total",
        (("method", "GET"), ("route", "/api/v1/meetings"), ("status_class", "2xx")),
    )
    assert samples[key] == 8
    assert "process_cpu_seconds_total" not in exposition  # no per-process series
    for source in metrics.COLLECTION_SOURCES:  # exported at 0 before any failure
        assert samples[("homayar_metrics_collection_errors_total", (("source", source),))] == 0


def test_every_scrape_source_is_exported_before_its_first_failure(client, settings, monkeypatch):
    # increase() needs a 0 sample before the first failure (PR #47 review, P4).
    used: set[str] = set()
    original = metrics.StateCollector._run

    def recording(self, sources, snapshot):
        used.update(sources)
        return original(self, sources, snapshot)

    monkeypatch.setattr(metrics.StateCollector, "_run", recording)
    collector = metrics.StateCollector(settings, cache_seconds=0)
    try:
        collector.refresh()
    finally:
        collector.close()
    assert used == set(metrics.COLLECTION_SOURCES)
    samples = _scrape(client)
    for source in metrics.COLLECTION_SOURCES:
        assert _value(samples, "homayar_metrics_collection_errors_total", source=source) is not None


def test_metrics_accept_the_internal_service_host_and_nothing_else_does(settings):
    with _app_client(settings) as client:
        internal = TestClient(client.app, base_url="http://api:8000")
        assert internal.get("/metrics").status_code == 200
        assert internal.get("/healthz").status_code == 400  # TRUSTED_HOSTS still applies


def test_a_token_file_with_trailing_whitespace_still_matches(settings):
    padded = settings.model_copy(update={"metrics_bearer_token": TOKEN + "\n "})
    with _app_client(padded) as client:
        assert (
            client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
        )


def test_cloud_refuses_the_unauthenticated_opt_out():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="METRICS_ALLOW_UNAUTHENTICATED"):
        Settings(
            _env_file=None,
            environment="staging",
            deployment_mode="cloud",
            metrics_allow_unauthenticated=True,
        )
    onprem = Settings(
        _env_file=None,
        environment="staging",
        deployment_mode="onprem",
        metrics_allow_unauthenticated=True,
    )
    assert metrics_access(onprem) == "open"


# Personal data and a share token as they would reach logs; built at runtime.
_EMAIL = "ali.rezaei" + "@example.com"
_PHONE = "0912" + "7654321"
_SHARE_TOKEN = "shr_" + "Zq9" * 12


def test_database_errors_are_logged_without_parameters_or_detail():
    from sqlalchemy.exc import IntegrityError

    class FakeDiag:
        constraint_name = "uq_users_email"
        table_name = "users"
        column_name = None

    class FakePsycopgError(Exception):
        sqlstate = "23505"
        diag = FakeDiag()

    original = FakePsycopgError(
        f'duplicate key value violates unique constraint "uq_users_email"\n'
        f"DETAIL:  Key (email)=({_EMAIL}) already exists."
    )
    error = IntegrityError(
        "INSERT INTO users (email, phone_e164) VALUES (%(email)s, %(phone)s)",
        {"email": _EMAIL, "phone": _PHONE},
        original,
    )
    summary = exception_summary(error)
    assert summary == (
        "IntegrityError FakePsycopgError sqlstate=23505 constraint_name=uq_users_email "
        "table_name=users"
    )
    record = logging.LogRecord(
        "homayar", logging.ERROR, __file__, 1, "write failed", (), (type(error), error, None)
    )
    line = JSONFormatter().format(record)
    for leaked in (_EMAIL, _PHONE, "INSERT INTO", "DETAIL"):
        assert leaked not in line


def test_unhandled_errors_log_the_route_template_and_correlation_without_pii(settings, caplog):
    from backend.observability.correlation import CorrelationLogFilter
    from backend.services.logging_service import logger

    logger.disabled = False  # Alembic's fileConfig in other tests disables it
    with _app_client(settings) as client:
        app = client.app

        @app.get("/api/v1/shares/{share_token}/explode")
        def explode(share_token: str):
            # Share tokens have no recognisable shape: the guarantee is that the raw
            # path never reaches the log. Personal data in the text is masked.
            raise RuntimeError(f"cannot notify {_EMAIL} or {_PHONE}")

        caplog.handler.addFilter(CorrelationLogFilter())
        failing = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level(logging.ERROR, logger="homayar"):
            response = failing.get(
                f"/api/v1/shares/{_SHARE_TOKEN}/explode",
                headers={"X-Request-ID": "error-request-0001"},
            )
    assert response.status_code == 500
    [record] = [r for r in caplog.records if r.getMessage().startswith("UNHANDLED_ERROR")]
    line = json.loads(JSONFormatter().format(record))
    assert line["correlation_id"] == "error-request-0001"
    assert "route=/api/v1/shares/{share_token}/explode" in line["message"]
    rendered = json.dumps(line)
    for leaked in (_SHARE_TOKEN, _EMAIL, _PHONE):
        assert leaked not in rendered
    assert "[EMAIL_1]" in line["exception"] and "[PHONE_1]" in line["exception"]


@pytest.mark.parametrize(
    "name",
    [
        "database_url",
        "redis_url",
        "jwt_secret",
        "token_pepper",
        "google_client_secret",
        "asanak_api_key",
        "storage_access_key",
        "storage_secret_key",
        "pii_mapping_keys",
        "llm_fallback_api_key",
        "zai_api_key",
        "gapgpt_api_key",
        "smtp_password",
        "neo4j_password",
        "sentry_dsn",
        "metrics_bearer_token",
    ],
)
def test_every_secret_setting_name_is_scrubbed_in_any_spelling(name):
    from backend.config import SECRET_FIELDS

    assert name in SECRET_FIELDS
    secret = "s" * 14
    for raw in (
        f"{name.upper()}={secret}",
        f"{name}: {secret}",
        f"{{'{name}': '{secret}'}}",
        f'{{"{name}": "{secret}"}}',
    ):
        assert secret not in scrub_text(raw), raw


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("dsn https://" + "k" * 12 + "@sentry.example.invalid/7", "k" * 12),
        ("postgresql://homayar:" + "pa/ss" + "w" * 6 + "@db:5432/homayar", "pa/ss"),
        ("postgresql://homayar:" + "p@ss" + "w" * 6 + "@db:5432/homayar", "ssw"),
        (
            "upstream rejected {'api_key': '" + "z" * 12 + "', 'token': '" + "t" * 12 + "'}",
            "z" * 12,
        ),
    ],
)
def test_scrub_covers_dsn_users_url_passwords_and_dict_reprs(raw, leaked):
    assert leaked not in scrub_text(raw)


def test_scrubbing_long_adversarial_input_is_bounded_and_fast():
    for hostile in ("a." * 8000, "eyJ-" * 4000, "x://" * 4000):
        started = time.monotonic()
        result = diagnostic_text(hostile)
        assert time.monotonic() - started < 1.0
        assert len(result) <= 2100


def test_a_bearer_token_across_the_length_limit_is_scrubbed_before_the_cut():
    token = "Q" * 30
    text = "x" * (scrub.MAX_DIAGNOSTIC_LENGTH - 15) + " Bearer " + token
    result = diagnostic_text(text)
    assert token[:7] not in result  # the old pre-cut kept 7 characters, too few to match
    assert len(result) <= scrub.MAX_DIAGNOSTIC_LENGTH + len(scrub.TRUNCATED)
    tail = "y" * scrub.MAX_SCRUB_INPUT + " Bearer " + token  # beyond what is examined
    assert token[:7] not in diagnostic_text(tail)


def test_a_failing_metric_write_never_fails_the_request(client, monkeypatch, caplog):
    from backend.services.logging_service import logger

    class FullDirectory:
        def labels(self, *labels):
            raise OSError(28, "No space left on device")

    logger.disabled = False  # Alembic's fileConfig in other tests disables it
    monkeypatch.setattr(metrics, "HTTP_REQUESTS", FullDirectory())
    monkeypatch.setattr(metrics, "guarded_write", metrics.WriteGuard())
    with caplog.at_level(logging.WARNING, logger="homayar"):
        for _ in range(3):
            assert client.get("/healthz").status_code == 200
    failures = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("METRICS_WRITE_FAILED")
    ]
    assert failures == ["METRICS_WRITE_FAILED error_type=OSError"]  # once, not per request


@contextmanager
def _uvicorn(app) -> Iterator[int]:
    """The app behind a real uvicorn server on a free loopback port."""
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=0, lifespan="off", log_config=None, access_log=False
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.05)
    try:
        yield server.servers[0].sockets[0].getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(10)


def test_the_server_log_of_an_escaped_database_error_has_no_sql_parameters_or_detail(
    settings, capfd
):
    # E1: uvicorn logs an exception that escaped the app on "uvicorn.error", with
    # its own handler and the full traceback. Run a real server and capture every
    # handler: uvicorn's formatter, the root JSON handler and the raw stdout/stderr.
    import httpx
    from sqlalchemy.exc import IntegrityError
    from uvicorn.logging import DefaultFormatter

    class FakePsycopgError(Exception):
        sqlstate = "23505"
        diag = None

    error_text = (
        'duplicate key value violates unique constraint "uq_users_email"\n'
        f"DETAIL:  Key (email)=({_EMAIL}) already exists."
    )
    server_lines: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record):
            server_lines.append(self.format(record))

    capture = Capture()
    capture.setFormatter(DefaultFormatter("%(levelprefix)s %(message)s", use_colors=False))
    server_logger = logging.getLogger("uvicorn.error")
    server_logger.disabled = False  # Alembic's fileConfig in other tests disables it
    server_logger.addHandler(capture)
    try:
        with _app_client(settings) as client:
            app = client.app

            @app.get("/api/v1/shares/{share_token}/db-explode")
            def db_explode(share_token: str):
                raise IntegrityError(
                    "INSERT INTO users (email, phone_e164) VALUES (%(email)s, %(phone)s)",
                    {"email": _EMAIL, "phone": _PHONE},
                    FakePsycopgError(error_text),
                )

            with _uvicorn(app) as port, httpx.Client(trust_env=False, timeout=30) as http:
                response = http.get(
                    f"http://127.0.0.1:{port}/api/v1/shares/{_SHARE_TOKEN}/db-explode",
                    headers={"Host": "testserver", "X-Request-ID": "error-request-0002"},
                )
    finally:
        server_logger.removeHandler(capture)
    assert response.status_code == 500
    [line] = [entry for entry in server_lines if "Exception in ASGI application" in entry]
    assert "request_id=error-request-0002" in line
    assert "IntegrityError FakePsycopgError sqlstate=23505" in line
    captured = capfd.readouterr()
    for output in ("\n".join(server_lines), captured.out, captured.err):
        for leaked in (_EMAIL, _PHONE, _SHARE_TOKEN, "INSERT INTO", "DETAIL", "Traceback"):
            assert leaked not in output, leaked


def test_the_server_log_filter_is_installed_once_on_uvicorns_loggers():
    from backend.services.logging_service import (
        SERVER_LOGGERS,
        ServerLogFilter,
        install_server_log_filter,
    )

    install_server_log_filter()
    install_server_log_filter()
    for name in SERVER_LOGGERS:
        filters = logging.getLogger(name).filters
        assert sum(isinstance(f, ServerLogFilter) for f in filters) == 1, name
