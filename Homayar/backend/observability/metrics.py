"""Prometheus metrics of the API process (WP20-T01, HOM-113; ADR 0036).

Two kinds of series:

* **In-process counters** of this API process: HTTP requests by route
  *template* and status class, request latency, authentication failures by
  bounded reason, audit write failures.
* **Scrape-time state** read when Prometheus scrapes ``/metrics`` and cached
  for ``METRICS_CACHE_SECONDS``: dependency health (PostgreSQL, Redis, object
  storage), the durable queue (``processing_jobs``), the dispatch outbox,
  recent job outcomes, durations and retries, provider endpoint health, the
  broker queue length, the periodic sweep heartbeats and the schema revision.
  Workers run in other processes and need no scrape endpoint of their own:
  everything they decide is durable in PostgreSQL or reported as a heartbeat.

Label rules: every label value comes from a fixed vocabulary (state, failure
class/category, stage, task, dependency, route template, status class).  No
tenant, user, meeting or job id, no provider endpoint and no free text is
ever a label, so the series count does not grow with customers.  Tenant
impact appears only as a count of affected workspaces; the per-workspace
breakdown stays behind the tenant-scoped admin API (``/api/v1/admin/jobs``).

Cross-tenant reads use the existing read-only service scopes of the
bookkeeping tables (``recovery`` on ``processing_jobs``, ``dispatch`` on
``job_dispatches``) and select aggregates only; ``provider_health`` is
platform state.  Nothing here reads customer content.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    PlatformCollector,
    ProcessCollector,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from backend.config import Settings

RECENT_WINDOW = timedelta(minutes=15)
MAX_DURATION_SAMPLES = 5000
DEPENDENCY_TIMEOUT_SECONDS = 2.0
PROBE_TENANT = "homayar-health-probe"
PROBE_OBJECT_TOKEN = "readiness-probe-object"  # noqa: S105 - object name, not a credential

JOB_STATES = ("queued", "running", "completed", "failed", "cancelled", "dead_letter")
FINISHED_STATES = ("completed", "failed", "cancelled", "dead_letter")
FAILURE_CLASSES = ("publication", "transient", "permanent", "abandoned", "deadline", "cancelled")
FAILURE_CATEGORIES = (
    "provider",
    "storage",
    "media",
    "timeout",
    "broker",
    "worker_lost",
    "deadline",
    "cancelled",
    "other",
)
DISPATCH_STATES = ("pending", "published", "failed", "cancelled")
PROVIDER_STAGES = ("asr", "diarization", "intelligence", "tts", "other")
QUANTILES = (("0.5", 0.5), ("0.95", 0.95), ("1", 1.0))
HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
AUTH_FAILURE_REASONS = frozenset(
    {"invalid_credentials", "invalid_refresh", "refresh_replay", "csrf_failed"}
)

METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST

REGISTRY = CollectorRegistry(auto_describe=True)
ProcessCollector(registry=REGISTRY)
PlatformCollector(registry=REGISTRY)

HTTP_REQUESTS = Counter(
    "homayar_http_requests",
    "API requests by method, route template and status class.",
    ("method", "route", "status_class"),
    registry=REGISTRY,
)
HTTP_DURATION = Histogram(
    "homayar_http_request_duration_seconds",
    "API request latency by method and route template.",
    ("method", "route"),
    buckets=(0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 120.0),
    registry=REGISTRY,
)
AUTH_FAILURES = Counter(
    "homayar_auth_failures",
    "Rejected authentication attempts by bounded reason.",
    ("reason",),
    registry=REGISTRY,
)
AUDIT_WRITE_FAILURES = Counter(
    "homayar_audit_write_failures",
    "Audit events that could not be written (delivery: transactional or best_effort).",
    ("delivery",),
    registry=REGISTRY,
)
COLLECTION_ERRORS = Counter(
    "homayar_metrics_collection_errors",
    "Scrape-time state sources that failed (the source's series are omitted).",
    ("source",),
    registry=REGISTRY,
)


def metrics_access(settings: Settings) -> Literal["disabled", "token", "open"]:
    """Who may scrape ``/metrics`` (HOM-113).

    A configured ``METRICS_BEARER_TOKEN`` is always required when set.  Without
    one, development and test serve openly, while staging and production refuse
    (404) unless ``METRICS_ALLOW_UNAUTHENTICATED=true`` opts out explicitly.
    """
    if not settings.metrics_enabled:
        return "disabled"
    if settings.metrics_bearer_token:
        return "token"
    if settings.environment in ("staging", "production") and not (
        settings.metrics_allow_unauthenticated
    ):
        return "disabled"
    return "open"


def status_class(status_code: int) -> str:
    return f"{status_code // 100}xx" if 100 <= status_code <= 599 else "other"


def observe_http(method: str, route: str, status_code: int, seconds: float) -> None:
    method = method.upper() if method.upper() in HTTP_METHODS else "OTHER"
    HTTP_REQUESTS.labels(method, route, status_class(status_code)).inc()
    HTTP_DURATION.labels(method, route).observe(max(0.0, seconds))


def observe_auth_failure(reason: str) -> None:
    AUTH_FAILURES.labels(reason if reason in AUTH_FAILURE_REASONS else "other").inc()


def observe_audit_write_failure(delivery: str) -> None:
    AUDIT_WRITE_FAILURES.labels(
        delivery if delivery in ("transactional", "best_effort") else "other"
    ).inc()


def failure_category(failure_class: str | None, failure_code: str | None) -> str:
    """Bounded category of a stopped job (``failure_code`` itself is open-ended)."""
    if failure_class == "publication":
        return "broker"
    if failure_class == "abandoned":
        return "worker_lost"
    if failure_class in ("deadline", "cancelled"):
        return failure_class
    code = (failure_code or "").casefold()
    if code.startswith(("object_", "storage_", "s3_", "local_", "invalid_storage", "checksum")):
        return "storage"
    if code.startswith(
        ("asr_", "diarization_", "intelligence_", "llm_", "tts_", "provider_", "manifest_")
    ):
        return "provider"
    if code.startswith(("media_", "audio_", "recording_", "unsupported_")):
        return "media"
    if code in ("attempt_timeout", "stage_timeout"):
        return "timeout"
    return "other"


def provider_stage(stage: str | None) -> str:
    value = (stage or "").casefold()
    return value if value in PROVIDER_STAGES else "other"


def quantile(values: list[float], q: float) -> float:
    """Nearest-rank quantile of ``values`` (0.0 when empty)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


@dataclass
class Snapshot:
    """Gauge samples of one collection: ``name -> (help, label names, samples)``."""

    families: dict[str, tuple[str, tuple[str, ...], list[tuple[tuple[str, ...], float]]]] = (
        field(default_factory=dict)
    )

    def add(
        self, name: str, help_text: str, value: float, **labels: str
    ) -> None:
        names = tuple(labels)
        entry = self.families.setdefault(name, (help_text, names, []))
        if entry[1] != names:
            raise ValueError(f"Inconsistent labels for {name}")
        entry[2].append((tuple(labels[n] for n in names), float(value)))

    def metric_families(self) -> Iterator[GaugeMetricFamily]:
        for name, (help_text, label_names, samples) in sorted(self.families.items()):
            family = GaugeMetricFamily(name, help_text, labels=list(label_names))
            for label_values, value in samples:
                family.add_metric(list(label_values), value)
            yield family


def _age(now: datetime, value: datetime | None) -> float:
    from backend.jobs.leases import as_utc

    moment = as_utc(value)
    return max(0.0, (now - moment).total_seconds()) if moment else 0.0


def _bounded_statement_timeout(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        milliseconds = int(DEPENDENCY_TIMEOUT_SECONDS * 1000)
        db.execute(text(f"SET LOCAL statement_timeout = {milliseconds}"))


def collect_database(snapshot: Snapshot, settings: Settings, *, now: datetime) -> bool:
    from backend.database.relational import get_session_factory

    started = time.perf_counter()
    try:
        with get_session_factory()() as db:
            _bounded_statement_timeout(db)
            db.execute(text("SELECT 1"))
            revision = _database_revision(db)
            db.rollback()
        up = True
    except Exception:
        up, revision = False, None
    snapshot.add(
        "homayar_dependency_up",
        "1 when the dependency answered the health probe of this scrape.",
        1.0 if up else 0.0,
        dependency="database",
    )
    snapshot.add(
        "homayar_dependency_probe_seconds",
        "Duration of the dependency health probe.",
        time.perf_counter() - started,
        dependency="database",
    )
    if up:
        from backend.observability.release import code_migration_head

        head = code_migration_head()
        snapshot.add(
            "homayar_schema_up_to_date",
            "1 when the database's Alembic revision equals this code's migration head "
            "(-1 when the database has no Alembic stamp, e.g. development create_all).",
            -1.0 if revision is None else float(revision == head),
        )
    return up


def _database_revision(db: Session) -> str | None:
    if db.get_bind().dialect.name == "postgresql":
        query = "SELECT to_regclass('public.alembic_version') IS NOT NULL"
    else:
        query = (
            "SELECT count(*) > 0 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'alembic_version'"
        )
    if not db.execute(text(query)).scalar_one():
        return None
    return db.execute(text("SELECT version_num FROM alembic_version")).scalar()


def collect_jobs(snapshot: Snapshot, *, now: datetime) -> None:
    from backend.database.relational import get_session_factory
    from backend.database.saas_models import JobState, ProcessingJob
    from backend.database.tenant_context import SERVICE_SCOPE_RECOVERY, bind_service_scope

    since = now - RECENT_WINDOW
    with get_session_factory()() as db:
        # Read-only discovery scope of the lease sweeper (aggregates only).
        bind_service_scope(db, SERVICE_SCOPE_RECOVERY)
        _bounded_statement_timeout(db)
        by_state = dict.fromkeys(JOB_STATES, 0)
        for state, count in db.execute(
            select(ProcessingJob.state, func.count()).group_by(ProcessingJob.state)
        ):
            by_state[state.value] = int(count)
        for state in JOB_STATES:
            snapshot.add(
                "homayar_jobs", "Processing jobs by state.", by_state[state], state=state
            )

        due_from = func.coalesce(ProcessingJob.retry_after, ProcessingJob.updated_at)
        due_count, oldest_due = db.execute(
            select(func.count(), func.min(due_from)).where(
                ProcessingJob.state == JobState.QUEUED,
                or_(ProcessingJob.retry_after.is_(None), ProcessingJob.retry_after <= now),
            )
        ).one()
        snapshot.add(
            "homayar_jobs_queued_due",
            "Queued jobs whose next attempt is due now (the durable queue depth).",
            int(due_count or 0),
        )
        snapshot.add(
            "homayar_jobs_oldest_queued_due_age_seconds",
            "Age of the oldest due queued job since it became due (queue latency).",
            _age(now, oldest_due) if due_count else 0.0,
        )
        snapshot.add(
            "homayar_jobs_retry_scheduled",
            "Queued jobs waiting for a retry after a failed attempt.",
            int(
                db.scalar(
                    select(func.count()).where(
                        ProcessingJob.state == JobState.QUEUED, ProcessingJob.attempt_count > 0
                    )
                )
                or 0
            ),
        )
        snapshot.add(
            "homayar_jobs_running_lease_expired",
            "Running jobs whose execution lease expired (worker lost, sweep pending).",
            int(
                db.scalar(
                    select(func.count()).where(
                        ProcessingJob.state == JobState.RUNNING,
                        ProcessingJob.lease_expires_at < now,
                    )
                )
                or 0
            ),
        )

        finished: dict[tuple[str, str], int] = defaultdict(int)
        failed_by_category: dict[tuple[str, str], int] = defaultdict(int)
        retried = 0
        # attempt_count is grouped as is (a small integer): a bound "> 1" in both the
        # select list and GROUP BY is two different expressions to PostgreSQL.
        for state, failure_class, failure_code, attempts, count in db.execute(
            select(
                ProcessingJob.state,
                ProcessingJob.failure_class,
                ProcessingJob.failure_code,
                ProcessingJob.attempt_count,
                func.count(),
            )
            .where(ProcessingJob.completed_at >= since)
            .group_by(
                ProcessingJob.state,
                ProcessingJob.failure_class,
                ProcessingJob.failure_code,
                ProcessingJob.attempt_count,
            )
        ):
            outcome = state.value
            if outcome not in FINISHED_STATES:
                continue
            klass = failure_class if failure_class in FAILURE_CLASSES else "none"
            finished[(outcome, klass)] += int(count)
            if attempts > 1:
                retried += int(count)
            if outcome in ("failed", "dead_letter"):
                category = failure_category(failure_class, failure_code)
                failed_by_category[(outcome, category)] += int(count)
        for outcome in FINISHED_STATES:
            snapshot.add(
                "homayar_jobs_finished_15m",
                "Jobs that reached a terminal state in the last 15 minutes.",
                sum(v for (o, _), v in finished.items() if o == outcome),
                outcome=outcome,
            )
        for (outcome, category), count in sorted(failed_by_category.items()):
            snapshot.add(
                "homayar_jobs_failed_15m",
                "Failed or dead-lettered jobs of the last 15 minutes by bounded category.",
                count,
                outcome=outcome,
                category=category,
            )
        for (outcome, klass), count in sorted(finished.items()):
            if outcome in ("failed", "dead_letter"):
                snapshot.add(
                    "homayar_jobs_failed_by_class_15m",
                    "Failed or dead-lettered jobs of the last 15 minutes by failure class.",
                    count,
                    outcome=outcome,
                    failure_class=klass,
                )
        snapshot.add(
            "homayar_jobs_retried_15m",
            "Jobs finished in the last 15 minutes that needed more than one attempt.",
            retried,
        )
        snapshot.add(
            "homayar_workspaces_with_failed_jobs_15m",
            "Workspaces with at least one failed or dead-lettered job in the last 15 minutes.",
            int(
                db.scalar(
                    select(func.count(func.distinct(ProcessingJob.workspace_id))).where(
                        ProcessingJob.completed_at >= since,
                        ProcessingJob.state.in_((JobState.FAILED, JobState.DEAD_LETTER)),
                    )
                )
                or 0
            ),
        )

        rows = db.execute(
            select(ProcessingJob.created_at, ProcessingJob.started_at, ProcessingJob.completed_at)
            .where(ProcessingJob.state == JobState.COMPLETED, ProcessingJob.completed_at >= since)
            .order_by(ProcessingJob.completed_at.desc())
            .limit(MAX_DURATION_SAMPLES)
        ).all()
        db.rollback()
    _duration_quantiles(snapshot, rows)


def _duration_quantiles(snapshot: Snapshot, rows: Iterable[Any]) -> None:
    from backend.jobs.leases import as_utc

    turnaround: list[float] = []
    processing: list[float] = []
    waiting: list[float] = []
    for created, started, completed in rows:
        created, started, completed = as_utc(created), as_utc(started), as_utc(completed)
        if created and completed:
            turnaround.append(max(0.0, (completed - created).total_seconds()))
        if started and completed:
            processing.append(max(0.0, (completed - started).total_seconds()))
        if created and started:
            waiting.append(max(0.0, (started - created).total_seconds()))
    for name, help_text, values in (
        (
            "homayar_job_turnaround_seconds_15m",
            "Creation to completion of jobs completed in the last 15 minutes.",
            turnaround,
        ),
        (
            "homayar_job_processing_seconds_15m",
            "First claim to completion (all attempts) of jobs completed in the last 15 minutes.",
            processing,
        ),
        (
            "homayar_job_queue_wait_seconds_15m",
            "Creation to first claim of jobs completed in the last 15 minutes.",
            waiting,
        ),
    ):
        for label, q in QUANTILES:
            snapshot.add(name, help_text, quantile(values, q), quantile=label)


def collect_dispatch(snapshot: Snapshot, *, now: datetime) -> None:
    from backend.database.relational import get_session_factory
    from backend.database.saas_models import DispatchState, JobDispatch
    from backend.database.tenant_context import SERVICE_SCOPE_DISPATCH, bind_service_scope

    with get_session_factory()() as db:
        bind_service_scope(db, SERVICE_SCOPE_DISPATCH)
        _bounded_statement_timeout(db)
        by_state = dict.fromkeys(DISPATCH_STATES, 0)
        for state, count in db.execute(
            select(JobDispatch.state, func.count()).group_by(JobDispatch.state)
        ):
            by_state[state.value] = int(count)
        oldest_due = db.scalar(
            select(func.min(JobDispatch.next_attempt_at)).where(
                JobDispatch.state == DispatchState.PENDING, JobDispatch.next_attempt_at <= now
            )
        )
        db.rollback()
    for state in DISPATCH_STATES:
        snapshot.add(
            "homayar_dispatch_outbox",
            "Dispatch outbox rows (job publication intents) by state.",
            by_state[state],
            state=state,
        )
    snapshot.add(
        "homayar_dispatch_oldest_due_age_seconds",
        "Age of the oldest due, unpublished outbox row (0 when none is due).",
        _age(now, oldest_due),
    )


def collect_providers(snapshot: Snapshot, *, now: datetime) -> None:
    from backend.database.relational import get_session_factory
    from backend.database.saas_models import ProviderHealth
    from backend.jobs.leases import as_utc

    since = now - RECENT_WINDOW
    totals = dict.fromkeys(PROVIDER_STAGES, 0)
    open_circuits = dict.fromkeys(PROVIDER_STAGES, 0)
    failing = dict.fromkeys(PROVIDER_STAGES, 0)
    recent = dict.fromkeys(PROVIDER_STAGES, 0)
    worst = dict.fromkeys(PROVIDER_STAGES, 0)
    with get_session_factory()() as db:
        _bounded_statement_timeout(db)
        rows = db.execute(
            select(
                ProviderHealth.stage,
                ProviderHealth.consecutive_failures,
                ProviderHealth.open_until,
                ProviderHealth.last_failure_at,
            )
        ).all()
        db.rollback()
    for stage, consecutive, open_until, last_failure in rows:
        key = provider_stage(stage)
        totals[key] += 1
        until, failed_at = as_utc(open_until), as_utc(last_failure)
        if until is not None and until > now:
            open_circuits[key] += 1
        if consecutive > 0:
            failing[key] += 1
        if failed_at is not None and failed_at >= since:
            recent[key] += 1
        worst[key] = max(worst[key], int(consecutive))
    for stage in PROVIDER_STAGES:
        snapshot.add(
            "homayar_provider_endpoints", "Known networked provider endpoints.", totals[stage],
            stage=stage,
        )
        snapshot.add(
            "homayar_provider_circuits_open",
            "Provider endpoints whose circuit is open now.",
            open_circuits[stage],
            stage=stage,
        )
        snapshot.add(
            "homayar_provider_endpoints_failing",
            "Provider endpoints whose last call failed (consecutive failures > 0).",
            failing[stage],
            stage=stage,
        )
        snapshot.add(
            "homayar_provider_endpoints_failed_15m",
            "Provider endpoints with a failure in the last 15 minutes.",
            recent[stage],
            stage=stage,
        )
        snapshot.add(
            "homayar_provider_consecutive_failures_max",
            "Largest consecutive failure count among the stage's endpoints.",
            worst[stage],
            stage=stage,
        )


def collect_redis(snapshot: Snapshot, settings: Settings, *, now: datetime) -> None:
    import redis

    from backend.celery_app import PROCESSING_QUEUE

    started = time.perf_counter()
    queue_length: int | None = None
    try:
        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=DEPENDENCY_TIMEOUT_SECONDS / 4,
            socket_timeout=DEPENDENCY_TIMEOUT_SECONDS / 4,
        )
        try:
            client.ping()
            queue_length = int(client.llen(PROCESSING_QUEUE))  # type: ignore[arg-type]
        finally:
            client.close()
        up = True
    except Exception:
        up = False
    snapshot.add(
        "homayar_dependency_up",
        "1 when the dependency answered the health probe of this scrape.",
        1.0 if up else 0.0,
        dependency="redis",
    )
    snapshot.add(
        "homayar_dependency_probe_seconds",
        "Duration of the dependency health probe.",
        time.perf_counter() - started,
        dependency="redis",
    )
    if queue_length is not None:
        snapshot.add(
            "homayar_broker_queue_length",
            "Messages waiting in the broker queue (published, not yet taken by a worker).",
            queue_length,
            queue=PROCESSING_QUEUE,
        )


def collect_heartbeats(snapshot: Snapshot, settings: Settings) -> None:
    from backend.observability.heartbeats import (
        KNOWN_TASKS,
        PERIODIC_TASKS,
        expected_interval_seconds,
        heartbeat_store,
    )

    for task in PERIODIC_TASKS:
        snapshot.add(
            "homayar_periodic_task_interval_seconds",
            "Configured beat interval of each periodic sweep.",
            expected_interval_seconds(task, settings) or 0.0,
            task=task,
        )
    heartbeats = heartbeat_store(settings).read_all()
    for task in KNOWN_TASKS:
        beat = heartbeats.get(task)
        if not beat:
            continue
        for field_name, metric, help_text in (
            (
                "last_success",
                "homayar_periodic_task_last_success_timestamp_seconds",
                "Unix time of the sweep's last successful run.",
            ),
            (
                "last_failure",
                "homayar_periodic_task_last_failure_timestamp_seconds",
                "Unix time of the sweep's last failed run.",
            ),
            (
                "last_run",
                "homayar_periodic_task_last_run_timestamp_seconds",
                "Unix time of the sweep's last run.",
            ),
            (
                "last_duration_seconds",
                "homayar_periodic_task_last_duration_seconds",
                "Wall time of the sweep's last run.",
            ),
        ):
            value = _as_float(beat.get(field_name))
            if value is not None:
                snapshot.add(metric, help_text, value, task=task)
        try:
            result = json.loads(beat.get("result") or "{}")
        except ValueError:
            result = {}
        from backend.observability.heartbeats import bounded_result

        for key, value in sorted(bounded_result(result).items()):
            snapshot.add(
                "homayar_periodic_task_result",
                "Outcome counters of the sweep's last successful run.",
                value,
                task=task,
                field=key,
            )


def _as_float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def collect_storage(snapshot: Snapshot, settings: Settings) -> None:
    from backend.storage.base import object_key_for
    from backend.storage.factory import create_object_storage

    started = time.perf_counter()
    try:
        storage = create_object_storage(settings)
        storage.exists(PROBE_TENANT, object_key_for(PROBE_TENANT, PROBE_OBJECT_TOKEN))
        up = True
    except Exception:
        up = False
    snapshot.add(
        "homayar_dependency_up",
        "1 when the dependency answered the health probe of this scrape.",
        1.0 if up else 0.0,
        dependency="storage",
    )
    snapshot.add(
        "homayar_dependency_probe_seconds",
        "Duration of the dependency health probe.",
        time.perf_counter() - started,
        dependency="storage",
    )


def collect_build_info(snapshot: Snapshot) -> None:
    from backend.observability.release import runtime_identity

    snapshot.add(
        "homayar_build_info",
        "Release identity of this process (always 1): source revision, version, "
        "migration head, Python version, lock digest prefix and build profile.",
        1.0,
        **runtime_identity(),
    )


def collect_snapshot(settings: Settings, *, now: datetime | None = None) -> Snapshot:
    """Every scrape-time source; a failing source is counted and skipped."""
    now = now or datetime.now(UTC)
    snapshot = Snapshot()
    sources: list[tuple[str, Callable[[], Any]]] = [
        ("build_info", lambda: collect_build_info(snapshot)),
        ("redis", lambda: collect_redis(snapshot, settings, now=now)),
        ("heartbeats", lambda: collect_heartbeats(snapshot, settings)),
        ("storage", lambda: collect_storage(snapshot, settings)),
    ]
    database_up = False
    try:
        database_up = collect_database(snapshot, settings, now=now)
    except Exception:
        COLLECTION_ERRORS.labels("database").inc()
    if database_up:
        sources += [
            ("jobs", lambda: collect_jobs(snapshot, now=now)),
            ("dispatch", lambda: collect_dispatch(snapshot, now=now)),
            ("providers", lambda: collect_providers(snapshot, now=now)),
        ]
    for name, source in sources:
        try:
            source()
        except Exception as exc:
            COLLECTION_ERRORS.labels(name).inc()
            from backend.services.logging_service import logger

            logger.warning(
                "METRICS_SOURCE_FAILED source=%s error_type=%s", name, type(exc).__name__
            )
    snapshot.add(
        "homayar_metrics_snapshot_timestamp_seconds",
        "Unix time the scrape-time state was collected.",
        now.timestamp(),
    )
    return snapshot


class StateCollector(Collector):
    """Serves the cached scrape-time snapshot; refreshes it at most every ``cache_seconds``."""

    def __init__(self, settings: Settings, *, cache_seconds: float) -> None:
        self._settings = settings
        self._cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._snapshot: Snapshot | None = None
        self._taken_at = 0.0

    def collect(self) -> Iterator[GaugeMetricFamily]:
        with self._lock:
            if self._snapshot is None or time.monotonic() - self._taken_at >= self._cache_seconds:
                self._snapshot = collect_snapshot(self._settings)
                self._taken_at = time.monotonic()
            snapshot = self._snapshot
        yield from snapshot.metric_families()

    def describe(self) -> list[Any]:
        return []


def render(collector: StateCollector) -> bytes:
    """The exposition text: in-process series plus the scrape-time snapshot."""
    state = CollectorRegistry(auto_describe=False)
    state.register(collector)
    return generate_latest(REGISTRY) + generate_latest(state)
