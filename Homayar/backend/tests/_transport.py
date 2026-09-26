"""Real-transport test support (WP06-T07 transport drills).

The drills in ``test_transport_drills.py`` need three things the in-process
fault model of ``test_fault_injection.py`` cannot give: a broker that outlives
the test process (Redis), worker processes whose pool child can really be
killed, and a scheduler that really runs.  This module provides them without
touching anything shared:

* ``TEST_REDIS_URL`` names a *dedicated, disposable* Redis database (never
  database 0, which ``REDIS_URL`` uses); it is flushed before and after each
  drill.
* ``TEST_REDIS_CONTAINER`` optionally names the Docker container behind that
  URL so the broker-restart drill can stop and start it.  Only that container
  is ever touched, and only when the variable is set.
* Worker and beat processes are children of the test process in their own
  session (``backend.tests._transport_worker``) and are always terminated by
  the fixture that created them, escalating from SIGTERM to SIGKILL.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

TEST_REDIS_ENV = "TEST_REDIS_URL"

# The in-process application under test never shares a real Redis. An ambient
# REDIS_URL (CI's service container, a developer's Redis) — or the default
# localhost:6379 when one listens there — would let rate-limit counters outlive
# the test that made them and turn later requests into 429s. Nothing listens on
# port 1: the limiter takes its in-memory test fallback (reset by the client
# fixtures) and readiness reports Redis unavailable, as on a machine without
# Redis. Drill workers get the disposable TEST_REDIS_URL explicitly.
APP_REDIS_URL = "redis://127.0.0.1:1/0"
TEST_REDIS_CONTAINER_ENV = "TEST_REDIS_CONTAINER"
WORKER_MODULE = "backend.tests._transport_worker"
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# kombu's Redis transport keys: the declared queue at default priority and the
# hash of deliveries handed to a consumer but not yet acknowledged (shared by
# every consumer of the database).
QUEUE_KEY = "processing"
UNACKED_KEY = "unacked"

# Shortened lease for the drills (the deployed default is 120 s / 30 s): a
# killed worker's lease must lapse within the test deadline.  The renew
# interval keeps the documented "at most half the lease" rule.
LEASE_SECONDS = 4
RENEW_SECONDS = 2


def configured_redis_url() -> str | None:
    value = os.environ.get(TEST_REDIS_ENV, "").strip()
    return value or None


def configured_redis_container() -> str | None:
    value = os.environ.get(TEST_REDIS_CONTAINER_ENV, "").strip()
    return value or None


def redis_database_index(url: str) -> int:
    path = urlsplit(url).path.strip("/")
    if not path.isdigit():
        raise RuntimeError(f"{TEST_REDIS_ENV} must select an explicit database index (e.g. /1)")
    return int(path)


def redis_client(url: str):
    import redis

    return redis.Redis.from_url(url, socket_timeout=5, socket_connect_timeout=5)


def flush_test_database(url: str) -> None:
    """``FLUSHDB`` on the dedicated test database only; database 0 is refused."""
    if redis_database_index(url) == 0:
        raise RuntimeError(f"{TEST_REDIS_ENV} must not use database 0 (that is REDIS_URL's)")
    client = redis_client(url)
    try:
        client.flushdb()
    finally:
        client.close()


def queue_state(url: str) -> dict[str, int]:
    """Deliveries waiting in the queue and deliveries held unacknowledged."""
    client = redis_client(url)
    try:
        return {"queued": int(client.llen(QUEUE_KEY)), "unacked": int(client.hlen(UNACKED_KEY))}
    finally:
        client.close()


def wait_for_redis(url: str, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            client = redis_client(url)
            try:
                if client.ping():
                    return
            finally:
                client.close()
        except Exception:
            if time.monotonic() > deadline:
                raise
        time.sleep(0.2)


def docker_container(action: str, name: str, *, timeout: float = 90) -> None:
    """``docker <action> <name>`` against the disposable broker container only."""
    if action not in {"stop", "start", "restart"}:
        raise ValueError(action)
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("the docker CLI is not available")
    subprocess.run([docker, action, name], check=True, capture_output=True, timeout=timeout)  # noqa: S603 - fixed argv, test-owned processes


@contextmanager
def use_broker(url: str, backend: str | None = None) -> Iterator:
    """Point the process-wide Celery app at ``url`` for the duration.

    ``Celery.close()`` drops only the broker connection pool.  The result
    backend and the producer pool are cached separately and would keep
    publishing to (and polling) the previous transport — in the queue-contract
    test that is the memory transport of the preceding parametrisation — so
    both are reset here as well, together with kombu's global pools.
    """
    from kombu import pools

    from backend.celery_app import celery_app

    conf = celery_app.conf
    previous = {
        "broker_url": conf.broker_url,
        "result_backend": conf.result_backend,
        "task_always_eager": conf.task_always_eager,
    }

    def reset() -> None:
        celery_app.close()
        # Celery 5.5 keeps the backend in a process-wide cache (thread-safe
        # backends) or thread-locally; clear both so the next access rebuilds
        # it from the updated configuration.
        celery_app._backend_cache = None
        if hasattr(celery_app._local, "backend"):
            del celery_app._local.backend
        celery_app.amqp._producer_pool = None
        pools.reset()

    conf.update(broker_url=url, result_backend=backend or url, task_always_eager=False)
    reset()
    try:
        yield celery_app
    finally:
        conf.update(**previous)
        reset()


def process_environment(settings, redis_url: str, **extra: str) -> dict[str, str]:
    """Environment of a worker/beat child mirroring the test ``Settings``.

    Only the knobs a drill depends on are shortened (lease period, and the
    sweep periods a caller passes); queue, acknowledgement, prefetch and
    time-limit settings stay exactly as ``backend.celery_app`` configures them.
    """
    env = dict(os.environ)
    env.update(
        {
            "ENVIRONMENT": "test",
            "DEPLOYMENT_MODE": settings.deployment_mode,
            "DATABASE_URL": settings.database_url,
            "DATABASE_APP_ROLE": settings.database_app_role,
            "REDIS_URL": redis_url,
            "TASKS_EAGER": "false",
            "AUTO_CREATE_SCHEMA": "false",
            "ALLOW_MOCK_PROVIDERS": "true",
            "ASR_PROVIDER": "mock",
            "DIARIZATION_PROVIDER": "mock",
            "INTELLIGENCE_PROVIDER": "mock",
            "STORAGE_BACKEND": "local",
            "STORAGE_LOCAL_ROOT": settings.storage_local_root,
            "UPLOAD_DIR": settings.upload_dir,
            "UPLOAD_QUARANTINE_DIR": settings.upload_quarantine_dir,
            "JWT_SECRET": settings.jwt_secret,
            "TOKEN_PEPPER": settings.token_pepper,
            "LOG_LEVEL": "INFO",
            "JOB_LEASE_SECONDS": str(LEASE_SECONDS),
            "JOB_LEASE_RENEW_INTERVAL_SECONDS": str(RENEW_SECONDS),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            # macOS: the prefork pool forks a process that already imported
            # Objective-C backed libraries.
            "OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES",
        }
    )
    env.update(extra)
    return env


class TransportProcess:
    """A worker or beat child of the test process, in its own session."""

    def __init__(self, args: list[str], env: dict[str, str], *, name: str) -> None:
        self.name = name
        self.process = subprocess.Popen(  # noqa: S603 - fixed argv, test-owned processes
            args,
            env=env,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._pump, name=f"transport-{name}", daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            with self._lock:
                self._lines.append(line.rstrip("\n"))

    @property
    def pid(self) -> int:
        return self.process.pid

    def lines(self) -> list[str]:
        with self._lock:
            return list(self._lines)

    def count(self, needle: str, *, and_also: str | None = None) -> int:
        """Lines containing ``needle`` (and ``and_also`` when given)."""
        return sum(
            1 for line in self.lines() if needle in line and (and_also is None or and_also in line)
        )

    def tail(self, n: int = 30) -> str:
        return "\n".join(self.lines()[-n:])

    def wait_for(
        self, needle: str, *, timeout: float, count: int = 1, and_also: str | None = None
    ) -> None:
        deadline = time.monotonic() + timeout
        while self.count(needle, and_also=and_also) < count:
            if self.process.poll() is not None:
                raise AssertionError(
                    f"{self.name} exited with {self.process.returncode} before logging "
                    f"{needle!r} x{count}:\n{self.tail()}"
                )
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"{self.name} did not log {needle!r} x{count} within {timeout}s:\n{self.tail()}"
                )
            time.sleep(0.05)

    def child_pids(self) -> list[int]:
        pgrep = shutil.which("pgrep")
        if pgrep is None:
            raise RuntimeError("pgrep is not available")
        result = subprocess.run([pgrep, "-P", str(self.pid)], capture_output=True, text=True)  # noqa: S603 - fixed argv, test-owned processes
        return [int(pid) for pid in result.stdout.split()]

    def kill_child(self) -> int:
        """SIGKILL the one pool child (prefork, concurrency 1); the parent survives."""
        children = self.child_pids()
        assert len(children) == 1, f"expected one pool child, found {children}"
        os.kill(children[0], signal.SIGKILL)
        return children[0]

    def kill_group(self) -> None:
        """SIGKILL parent and children together: no acknowledgement, no requeue."""
        os.killpg(self.pid, signal.SIGKILL)
        self.process.wait(timeout=10)

    def stop(self, *, timeout: float = 30) -> int | None:
        """Warm shutdown (SIGTERM), escalating to SIGKILL after ``timeout``."""
        if self.process.poll() is None:
            try:
                os.killpg(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(self.pid, signal.SIGKILL)
                self.process.wait(timeout=10)
        self._reader.join(timeout=5)
        return self.process.returncode


def spawn_worker(
    settings,
    redis_url: str,
    *,
    name: str,
    gate: Path | None,
    visibility_timeout: int | None = None,
    extra_env: dict[str, str] | None = None,
) -> TransportProcess:
    env = process_environment(settings, redis_url, **(extra_env or {}))
    if gate is not None:
        env["HOMAYAR_TEST_ASR_GATE"] = str(gate)
    if visibility_timeout is not None:
        env["HOMAYAR_TEST_VISIBILITY_TIMEOUT"] = str(visibility_timeout)
    args = [sys.executable, "-m", WORKER_MODULE, "worker", "--hostname", f"{name}@%h"]
    return TransportProcess(args, env, name=name)


def spawn_beat(
    settings,
    redis_url: str,
    *,
    schedule_path: Path,
    extra_env: dict[str, str] | None = None,
) -> TransportProcess:
    env = process_environment(settings, redis_url, **(extra_env or {}))
    args = [sys.executable, "-m", WORKER_MODULE, "beat", "--schedule", str(schedule_path)]
    return TransportProcess(args, env, name="beat")
