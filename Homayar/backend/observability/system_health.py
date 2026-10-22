"""Operator system-health summary (HOM-227): ``GET /api/v1/admin/system-health``.

A customer's on-premise admin often has no Grafana, so the console (HOM-232)
asks one question instead: is this installation healthy?  The answer reads
the ``/metrics`` sources (HOM-113, ADR 0036) through their collectors:
PostgreSQL (``SELECT 1`` and the Alembic revision against the code's
migration head), Redis (ping and the broker queue length), object storage,
the durable queue (``processing_jobs``), the periodic sweep heartbeats, the
backup freshness textfile the backup tool writes (``HOMAYAR_BACKUP_TEXTFILE``)
and the release identity.  The thresholds are the alert rules'
(``infra/observability/prometheus/homayar.rules.yml``): a sweep is stale after
``max(3 * interval, 240)`` seconds without a success, a backup target after
26 hours.

Deadline: every source runs in a daemon thread of its own, and one request
waits at most ``DEADLINE_SECONDS`` for all of them together, so the answer
comes in under a second even when a dependency hangs.  A source that misses
the deadline is reported as ``timeout`` (a section as ``unknown``) and stays in
flight; a source is never started twice at once.  A request that comes while a
call runs shares it when the call is younger than one deadline: it waits for
it until the call's own start plus one deadline, never longer, so overlapping
requests on a healthy install get the same answer.  A call older than one
deadline hangs: as in the ``/metrics`` collector, it is reported as
``timeout`` at once, without waiting, so a hung dependency costs one thread,
never one per request.  A result counts only when its call finished within one
deadline of its start, so no answer uses a result older than that.  The queue
aggregates start as soon as the database probe answered, inside the same
deadline.  Daemon threads never hold up the process exit.

State: the collector (in-flight sources, a small database engine of its own
with a connect timeout, the storage client) belongs to one app
(``app.state``); the app's lifespan closes it at shutdown.  It shares no lock
with the ``/metrics`` collector and never uses the request's database session.

The answer holds statuses, counts, timestamps, exception class names
(filtered) and the build identity.  It never holds a tenant, user, meeting or
job id or name, customer content, a URL, a path, a secret or an error message.
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from backend.backup import metrics as backup_metrics
from backend.config import Settings, get_settings
from backend.observability import heartbeats, metrics, release

# A request waits at most this long for all sources together.  The rest of the
# one-second budget covers authentication, the answer and the timer slack of a
# loaded host (a timed wait there can return 0.2 s late).  A cold S3 client can
# miss the first deadline; its probe keeps running and later requests reuse the client.
DEADLINE_SECONDS = 0.5
# The alert rules' thresholds (infra/observability/prometheus/homayar.rules.yml).
SWEEP_STALE_INTERVALS = 3
SWEEP_STALE_MIN_SECONDS = 240.0
BACKUP_MAX_AGE_SECONDS = 93600.0  # 26 h
# The variable the backup tool and Compose use (backend/backup/metrics.py).
BACKUP_TEXTFILE_ENV = "HOMAYAR_BACKUP_TEXTFILE"
# The operator task the protected audit sink's verify run reports as (HOM-115).
AUDIT_SINK_VERIFY_TASK = "audit_sink_verify"
BUILD_KEYS = ("version", "source_sha", "migration_head", "profile", "python_version")
_FAILURE_CLASS = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}")


@dataclass(frozen=True)
class Outcome:
    """How a source ended by the deadline: ``done`` with its value, ``failed``
    (it raised; the value is the exception class name) or ``timeout`` (the value
    is ``Timeout``, or ``StillRunning`` for a call older than one deadline)."""

    state: Literal["done", "failed", "timeout"]
    value: Any = None


@dataclass(frozen=True)
class Probe:
    """A dependency probe: whether it answered, how long it took, what it read."""

    up: bool
    seconds: float
    snapshot: metrics.Snapshot


@dataclass
class _Call:
    """One run of a source, shared by the requests that come within one deadline
    of its start.  Times are ``time.monotonic()`` seconds."""

    future: Future[Any]
    expires: float  # its start plus one deadline
    finished: float | None = None


def _settle(call: _Call, source: Callable[[], Any]) -> None:
    future = call.future
    if not future.set_running_or_notify_cancel():
        return
    try:
        result = source()
    except BaseException as exc:  # as concurrent.futures does: the future reports it
        call.finished = time.monotonic()
        future.set_exception(exc)
    else:
        call.finished = time.monotonic()
        future.set_result(result)


def _until(call: _Call, deadline: float) -> float:
    """How long a request waits for a call: the call's own expiry, at most the request's."""
    return min(deadline, call.expires)


def _wait(calls: list[_Call | None], deadline: float) -> None:
    pending = [call for call in calls if call is not None and not call.future.done()]
    if pending:
        until = max(_until(call, deadline) for call in pending)
        wait([call.future for call in pending], timeout=max(0.0, until - time.monotonic()))


def _outcome(call: _Call | None, deadline: float) -> Outcome:
    if call is None:
        return Outcome("timeout", "StillRunning")
    future = call.future
    if not future.done() or call.finished is None or call.finished > _until(call, deadline):
        return Outcome("timeout", "Timeout")
    error = future.exception()
    if error is not None:
        return Outcome("failed", type(error).__name__)
    return Outcome("done", future.result())


def _gauge(snapshot: metrics.Snapshot, name: str, **labels: str) -> float | None:
    family = snapshot.families.get(name)
    if family is None:
        return None
    _, label_names, samples = family
    for label_values, value in samples:
        sample_labels = dict(zip(label_names, label_values, strict=True))
        if all(sample_labels.get(key) == wanted for key, wanted in labels.items()):
            return value
    return None


def _probe(collect: Callable[[metrics.Snapshot], Any], dependency: str) -> Probe:
    """Run a ``/metrics`` dependency collector; an error means the dependency is down."""
    snapshot = metrics.Snapshot()
    started = time.perf_counter()
    try:
        collect(snapshot)
        up = _gauge(snapshot, "homayar_dependency_up", dependency=dependency) == 1.0
    except Exception:
        up = False
    return Probe(up=up, seconds=time.perf_counter() - started, snapshot=snapshot)


class SystemHealthCollector:
    """The sources of one app's summary, run under one deadline per request."""

    def __init__(self, settings: Settings, *, deadline: float | None = None) -> None:
        self._settings = settings
        self._deadline = deadline
        self._lock = threading.Lock()  # held only to check or start a source
        self._inflight: dict[str, _Call] = {}
        self._factory_lock = threading.Lock()
        self._engine: Any = None
        self._factory: sessionmaker[Session] | None = None
        self._storage: Any = None

    def _session_factory(self) -> sessionmaker[Session]:
        with self._factory_lock:
            if self._factory is None:
                self._engine, self._factory = metrics.metrics_session_factory(self._settings)
            return self._factory

    def _object_storage(self) -> Any:
        # Only the storage source calls this, and it never runs twice at once.
        if self._storage is None:
            self._storage = metrics.probe_storage(self._settings)
        return self._storage

    def _database(self) -> Probe:
        return _probe(
            lambda part: metrics.collect_database(part, self._session_factory()), "database"
        )

    def _redis(self) -> Probe:
        return _probe(lambda part: metrics.collect_redis(part, self._settings), "redis")

    def _storage_probe(self) -> Probe:
        return _probe(lambda part: metrics.collect_storage(part, self._object_storage()), "storage")

    def _heartbeats(self) -> Any:
        return heartbeats.heartbeat_store(self._settings).read_all()

    def _budget(self) -> float:
        return DEADLINE_SECONDS if self._deadline is None else self._deadline

    def _start(self, name: str, source: Callable[[], Any]) -> _Call | None:
        """This request's call of the source.  The running call when it started less
        than one deadline ago (overlapping requests share it); ``None`` when the
        running call is older (it hangs and is not started twice); else a new call
        in a new thread."""
        started = time.monotonic()
        with self._lock:
            previous = self._inflight.get(name)
            if previous is not None and not previous.future.done():
                return previous if started < previous.expires else None
            call = _Call(future=Future(), expires=started + self._budget())
            self._inflight[name] = call
            threading.Thread(
                target=_settle,
                args=(call, source),
                name=f"homayar-health-{name}",
                daemon=True,
            ).start()
            return call

    def collect(self) -> dict[str, Any]:
        """The summary (the fields of ``SystemHealthResponse``), within the deadline."""
        now = datetime.now(UTC)
        deadline = time.monotonic() + self._budget()
        textfile = os.environ.get(BACKUP_TEXTFILE_ENV, "").strip()
        sources: dict[str, Callable[[], Any]] = {
            "database": self._database,
            "redis": self._redis,
            "storage": self._storage_probe,
            "heartbeats": self._heartbeats,
        }
        if textfile:
            sources["backup"] = lambda: backup_metrics.read_values(Path(textfile))
        calls = {name: self._start(name, source) for name, source in sources.items()}
        # The queue aggregates need the database: they start once its probe answered.
        _wait([calls["database"]], deadline)
        database = _outcome(calls["database"], deadline)
        if database.state == "done" and database.value.up:
            calls["jobs"] = self._start(
                "jobs", lambda: metrics.read_queue_depth(self._session_factory(), now=now)
            )
        _wait(list(calls.values()), deadline)
        outcomes = {name: _outcome(call, deadline) for name, call in calls.items()}
        _log_failures(outcomes)
        return summarize(
            outcomes, settings=self._settings, now=now, backup_configured=bool(textfile)
        )

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()


def _log_failures(outcomes: Mapping[str, Outcome]) -> None:
    from backend.services.logging_service import logger

    for name, outcome in outcomes.items():
        if outcome.state != "done":
            logger.warning(
                "SYSTEM_HEALTH_SOURCE_FAILED source=%s error_type=%s", name, outcome.value
            )


# --- the answer -----------------------------------------------------------------------


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _moment(value: Any) -> datetime | None:
    """A Unix time as a UTC datetime; ``None`` when absent or unusable."""
    seconds = _number(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _failure_class(value: Any) -> str:
    """The recorded exception class name when it looks like one, else ``other``."""
    return value if isinstance(value, str) and _FAILURE_CLASS.fullmatch(value) else "other"


@dataclass(frozen=True)
class _Beat:
    """One task's heartbeat hash (``backend.observability.heartbeats``), parsed."""

    last_run: datetime | None = None
    last_success: datetime | None = None
    last_failure: datetime | None = None
    failure_class: str | None = None
    duration_seconds: float | None = None

    @classmethod
    def read(cls, beats: Any, task: str) -> _Beat:
        fields = beats.get(task) if isinstance(beats, Mapping) else None
        if not isinstance(fields, Mapping):
            return cls()
        last_failure = _moment(fields.get("last_failure"))
        duration = _number(fields.get("last_duration_seconds"))
        return cls(
            last_run=_moment(fields.get("last_run")),
            last_success=_moment(fields.get("last_success")),
            last_failure=last_failure,
            failure_class=None
            if last_failure is None
            else _failure_class(fields.get("last_error_type")),
            duration_seconds=duration if duration is not None and duration >= 0 else None,
        )

    @property
    def ran(self) -> bool:
        return self.last_success is not None or self.last_failure is not None

    @property
    def failing(self) -> bool:
        return self.last_failure is not None and (
            self.last_success is None or self.last_failure > self.last_success
        )


def _dependency(outcome: Outcome) -> dict[str, Any]:
    if outcome.state == "timeout":
        return {"status": "timeout", "probe_seconds": None}
    if outcome.state == "failed":
        return {"status": "down", "probe_seconds": None}
    probe: Probe = outcome.value
    return {"status": "up" if probe.up else "down", "probe_seconds": probe.seconds}


def _schema(database: Outcome) -> dict[str, Any]:
    head = release.code_migration_head()
    if database.state != "done" or not database.value.up:
        return {"status": "unknown", "code_head": head, "database_revision": None}
    revision = database.value.snapshot.facts.get("database_revision")
    if revision is None:
        status = "unstamped"
    elif revision == head:
        status = "up_to_date"
    else:
        status = "behind"
    return {"status": status, "code_head": head, "database_revision": revision}


def _queue(jobs: Outcome | None, redis: Outcome) -> dict[str, Any]:
    broker_length = None
    if redis.state == "done":
        length = _gauge(redis.value.snapshot, "homayar_broker_queue_length")
        broker_length = None if length is None else int(length)
    if jobs is None or jobs.state != "done":
        return {
            "status": "unknown",
            "queued_due": None,
            "oldest_queued_due_age_seconds": None,
            "retry_scheduled": None,
            "broker_queue_length": broker_length,
        }
    queued_due, oldest_age, retry_scheduled = jobs.value
    return {
        "status": "ok",
        "queued_due": int(queued_due),
        "oldest_queued_due_age_seconds": float(oldest_age),
        "retry_scheduled": int(retry_scheduled),
        "broker_queue_length": broker_length,
    }


def _sweep_status(beat: _Beat, interval: float | None, now: datetime) -> str:
    if not beat.ran:
        return "never_run"
    if beat.failing:
        return "failing"
    stale_after = max(SWEEP_STALE_INTERVALS * (interval or 0.0), SWEEP_STALE_MIN_SECONDS)
    last_success = beat.last_success  # set: it ran and is not failing
    if last_success is not None and (now - last_success).total_seconds() <= stale_after:
        return "ok"
    return "stale"


def _sweeps(outcome: Outcome, *, settings: Settings, now: datetime) -> dict[str, Any]:
    known = outcome.state == "done"
    tasks = []
    for task in heartbeats.PERIODIC_TASKS:
        interval = heartbeats.expected_interval_seconds(task, settings)
        beat = _Beat.read(outcome.value, task) if known else _Beat()
        tasks.append(
            {
                "task": task,
                "status": _sweep_status(beat, interval, now) if known else "unknown",
                "expected_interval_seconds": interval,
                "last_run_at": beat.last_run,
                "last_success_at": beat.last_success,
                "last_failure_at": beat.last_failure,
                "last_failure_class": beat.failure_class,
                "last_duration_seconds": beat.duration_seconds,
            }
        )
    return {"status": "ok" if known else "unknown", "tasks": tasks}


def _backup(outcome: Outcome | None, *, configured: bool, now: datetime) -> dict[str, Any]:
    targets: dict[str, datetime | None] = dict.fromkeys(backup_metrics.TARGETS)
    if not configured:
        return {"status": "not_configured", "targets": targets}
    if outcome is None or outcome.state != "done":
        return {"status": "unknown", "targets": targets}
    values = outcome.value if isinstance(outcome.value, Mapping) else {}
    for target in backup_metrics.TARGETS:
        targets[target] = _moment(values.get(target))
    present = [moment for moment in targets.values() if moment is not None]
    if not present:
        status = "missing"
    elif len(present) == len(targets) and all(
        (now - moment).total_seconds() <= BACKUP_MAX_AGE_SECONDS for moment in present
    ):
        status = "ok"
    else:
        status = "stale"
    return {"status": status, "targets": targets}


def _audit_sink_configured() -> bool:
    """Whether the audit sink's verify run reports a heartbeat (HOM-115 adds the task;
    ``KNOWN_TASKS`` is built from ``OPERATOR_TASKS`` at import, so check both)."""
    return (
        AUDIT_SINK_VERIFY_TASK in heartbeats.KNOWN_TASKS
        or AUDIT_SINK_VERIFY_TASK in heartbeats.OPERATOR_TASKS
    )


def _audit_sink(outcome: Outcome) -> dict[str, Any]:
    if not _audit_sink_configured():
        status, beat = "not_configured", _Beat()
    elif outcome.state != "done":
        status, beat = "unknown", _Beat()
    else:
        # An operator-run task: no beat interval, so no staleness rule.
        beat = _Beat.read(outcome.value, AUDIT_SINK_VERIFY_TASK)
        status = "never_run" if not beat.ran else "failing" if beat.failing else "ok"
    return {
        "status": status,
        "last_verify_at": beat.last_run,
        "last_verify_success_at": beat.last_success,
        "last_failure_class": beat.failure_class,
    }


def _build() -> dict[str, str]:
    identity = release.runtime_identity()
    return {key: identity[key] for key in BUILD_KEYS}


def _overall(body: Mapping[str, Any]) -> str:
    dependencies = body["dependencies"]
    if dependencies["database"]["status"] != "up":
        return "down"
    healthy = (
        dependencies["redis"]["status"] == "up",
        dependencies["storage"]["status"] == "up",
        body["schema"]["status"] == "up_to_date",
        body["queue"]["status"] == "ok",
        body["sweeps"]["status"] == "ok",
        all(task["status"] == "ok" for task in body["sweeps"]["tasks"]),
        # not_configured (and an audit sink that never ran) is not a fault.
        body["backup"]["status"] in ("ok", "not_configured"),
        body["audit_sink"]["status"] in ("ok", "never_run", "not_configured"),
    )
    return "ok" if all(healthy) else "degraded"


def summarize(
    outcomes: Mapping[str, Outcome],
    *,
    settings: Settings,
    now: datetime,
    backup_configured: bool,
) -> dict[str, Any]:
    """The answer from the sources' outcomes: never invents a value a source did not give."""
    body = {
        "checked_at": now,
        "dependencies": {
            name: _dependency(outcomes[name]) for name in ("database", "redis", "storage")
        },
        "schema": _schema(outcomes["database"]),
        "queue": _queue(outcomes.get("jobs"), outcomes["redis"]),
        "sweeps": _sweeps(outcomes["heartbeats"], settings=settings, now=now),
        "backup": _backup(outcomes.get("backup"), configured=backup_configured, now=now),
        "audit_sink": _audit_sink(outcomes["heartbeats"]),
        "build": _build(),
    }
    return {"status": _overall(body), **body}


async def system_health_collector(
    request: Request, settings: Settings = Depends(get_settings)
) -> SystemHealthCollector:
    """The app's collector in ``app.state.system_health_collector``, created on first
    use; the app's lifespan closes it at shutdown (``backend/main.py``).

    An ``async`` dependency runs on the event loop, so two first requests cannot
    both create one."""
    state = request.app.state
    collector = getattr(state, "system_health_collector", None)
    if collector is None:
        collector = state.system_health_collector = SystemHealthCollector(settings)
    return collector
