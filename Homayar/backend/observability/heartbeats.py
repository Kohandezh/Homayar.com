"""Heartbeats of the periodic sweeps (WP20-T01/T04, HOM-113, HOM-126).

Every periodic task (dispatch outbox, lease recovery, usage and media
reconciliation, artifact cleanup, retention purge, voice recovery) runs on a
worker after Celery beat enqueued it.  After each run the worker records, in
one Redis hash per task:

* ``last_run`` / ``last_success`` / ``last_failure`` – Unix seconds;
* ``last_duration_seconds`` – wall time of the run;
* ``last_error_type`` – exception class name of the last failure (no message);
* ``result`` – the sweep's own outcome counters (``{"purges_failed": 1}``),
  integer values under short snake_case keys only.

The API reads the hashes when Prometheus scrapes ``/metrics``.  A stale
``last_success`` therefore means beat, every worker on the queue, or the
sweep itself stopped: the alert rules compare its age with the configured
interval.  The hashes hold names, timestamps and counters, never ids,
tenant data or content.

Recording is best effort: a sweep never fails because Redis is unreachable
(the Redis-down alert covers that case).
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from backend.config import Settings, get_settings
from backend.observability.correlation import correlation_scope, sweep_correlation_id

HEARTBEAT_KEY_PREFIX = "homayar:obs:sweep:"
# Long enough that a stopped scheduler still shows its last success; the
# alert fires on the age, not on the key disappearing.
HEARTBEAT_TTL_SECONDS = 7 * 24 * 3600
REDIS_TIMEOUT_SECONDS = 0.5

_RESULT_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_RESULT_FIELDS = 32

# Periodic task -> the setting that holds its beat interval (seconds).
PERIODIC_TASKS: dict[str, str] = {
    "dispatch_outbox": "dispatch_sweep_interval_seconds",
    "lease_recovery": "job_recovery_sweep_interval_seconds",
    "usage_reconciliation": "usage_reconciliation_interval_seconds",
    "artifact_cleanup": "job_artifact_sweep_interval_seconds",
    "media_reconciliation": "media_reconciliation_interval_seconds",
    "retention_purge": "retention_purge_interval_seconds",
    "voice_recovery": "voice_node_heartbeat_ttl_seconds",
    "analysis_recompute": "recompute_sweep_interval_seconds",
}
# Operator-run jobs that report through the same store (no beat interval). The
# protected audit sink (HOM-115) adds its export and verify runs with the tool.
OPERATOR_TASKS: tuple[str, ...] = ()
KNOWN_TASKS: tuple[str, ...] = (*PERIODIC_TASKS, *OPERATOR_TASKS)


def expected_interval_seconds(task: str, settings: Settings) -> float | None:
    attribute = PERIODIC_TASKS.get(task)
    return float(getattr(settings, attribute)) if attribute else None


def bounded_result(result: Any) -> dict[str, int]:
    """The integer counters of a sweep result with safe keys (others dropped)."""
    if not isinstance(result, Mapping):
        return {}
    counters: dict[str, int] = {}
    for key, value in result.items():
        if len(counters) >= MAX_RESULT_FIELDS:
            break
        if (
            isinstance(key, str)
            and _RESULT_KEY.fullmatch(key)
            and isinstance(value, int)
            and not isinstance(value, bool)
        ):
            counters[key] = value
    return counters


class HeartbeatStore(Protocol):
    def write(self, task: str, fields: Mapping[str, str]) -> None: ...

    def read_all(self) -> dict[str, dict[str, str]]: ...


class RedisHeartbeatStore:
    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url

    def _client(self):
        import redis

        return redis.Redis.from_url(
            self._redis_url,
            socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
            socket_timeout=REDIS_TIMEOUT_SECONDS,
            decode_responses=True,
        )

    def write(self, task: str, fields: Mapping[str, str]) -> None:
        key = HEARTBEAT_KEY_PREFIX + task
        client = self._client()
        try:
            pipeline = client.pipeline(transaction=True)
            pipeline.hset(key, mapping=dict(fields))
            pipeline.expire(key, HEARTBEAT_TTL_SECONDS)
            pipeline.execute()
        finally:
            client.close()

    def read_all(self) -> dict[str, dict[str, str]]:
        client = self._client()
        try:
            pipeline = client.pipeline(transaction=False)
            for task in KNOWN_TASKS:
                pipeline.hgetall(HEARTBEAT_KEY_PREFIX + task)
            values = pipeline.execute()
        finally:
            client.close()
        return {task: value for task, value in zip(KNOWN_TASKS, values, strict=True) if value}


class MemoryHeartbeatStore:
    """In-process store for tests and single-process development."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, str]] = {}

    def write(self, task: str, fields: Mapping[str, str]) -> None:
        with self._lock:
            self._data.setdefault(task, {}).update(fields)

    def read_all(self) -> dict[str, dict[str, str]]:
        with self._lock:
            return {task: dict(fields) for task, fields in self._data.items()}


_override: HeartbeatStore | None = None


def use_heartbeat_store(store: HeartbeatStore | None) -> None:
    """Replace the store (tests); ``None`` restores the Redis store."""
    global _override
    _override = store


def heartbeat_store(settings: Settings | None = None) -> HeartbeatStore:
    if _override is not None:
        return _override
    return RedisHeartbeatStore((settings or get_settings()).redis_url)


def record_run(
    task: str,
    *,
    started: float,
    finished: float,
    result: Any = None,
    error: BaseException | None = None,
    settings: Settings | None = None,
) -> bool:
    """Store one run's heartbeat; returns False when the store was unreachable."""
    if task not in KNOWN_TASKS:
        raise ValueError(f"Unknown periodic task {task!r}")
    fields = {
        "last_run": f"{finished:.3f}",
        "last_duration_seconds": f"{max(0.0, finished - started):.3f}",
    }
    if error is None:
        fields["last_success"] = f"{finished:.3f}"
        fields["result"] = json.dumps(bounded_result(result), sort_keys=True)
    else:
        fields["last_failure"] = f"{finished:.3f}"
        fields["last_error_type"] = type(error).__name__[:80]
    try:
        heartbeat_store(settings).write(task, fields)
    except Exception:  # best effort; the Redis-down alert covers the outage
        from backend.services.logging_service import logger

        logger.warning("SWEEP_HEARTBEAT_UNAVAILABLE task=%s", task)
        return False
    return True


def run_with_heartbeat[T](task: str, sweep: Callable[[], T]) -> T:
    """Run one periodic sweep under its own correlation id and record its heartbeat."""
    with correlation_scope(sweep_correlation_id(task)):
        started = time.time()
        try:
            result = sweep()
        except BaseException as exc:
            record_run(task, started=started, finished=time.time(), error=exc)
            raise
        record_run(task, started=started, finished=time.time(), result=result)
        return result
