"""WP06-T07 transport drills on the real Redis/Celery stack.

``test_fault_injection.py`` proves the durable-processing promises against an
in-process fault model.  These drills inject the faults that model cannot: a
broker that outlives the test process, worker processes that really die, a
scheduler that really runs.

* a pool child killed with SIGKILL mid-attempt (``task_reject_on_worker_lost``
  requeues the delivery; the copy meets the live lease and is inert; the lease
  lapses by itself; recovery requeues exactly once);
* the whole worker killed (no acknowledgement, no requeue: the delivery stays
  parked in Redis' ``unacked`` set; recovery does not wait for the broker; the
  parked copy restored after the visibility timeout is inert);
* a visibility-timeout redelivery to a second worker during a live attempt;
* the broker restarted and stopped (persisted deliveries survive, worker and
  publisher reconnect, publication failure is deferred and swept later);
* beat really scheduling the dispatch and lease sweeps.

Every drill is bounded (gates and deadlines, only polling waits) and ends
with the invariants of ``backend.jobs.invariants`` and one settlement per
meeting.  Requires PostgreSQL (row locking, the fences) and ``TEST_REDIS_URL``
(a dedicated disposable database); the broker-restart drill additionally
needs ``TEST_REDIS_CONTAINER`` and the Docker CLI.
"""

from __future__ import annotations

import re
import shutil
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.database.saas_models import (
    DispatchState,
    JobDispatch,
    JobState,
    ProcessingStageRun,
    TranscriptSegment,
)
from backend.jobs import invariants, outbox, processor, recovery
from backend.jobs.leases import as_utc
from backend.storage import create_object_storage
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests._transport import (
    LEASE_SECONDS,
    TransportProcess,
    configured_redis_container,
    configured_redis_url,
    docker_container,
    flush_test_database,
    queue_state,
    spawn_beat,
    spawn_worker,
    use_broker,
    wait_for_redis,
)
from backend.tests.test_job_leases import _expire_lease, _job
from backend.tests.test_job_processor import _queued_upload
from backend.tests.test_usage_ledger import _events

pytestmark = [pytest.mark.postgres, pytest.mark.redis]

DEADLINE = 60.0
VISIBILITY_SECONDS = 4
# kombu's Redis transport makes an unacknowledged delivery visible again after
# ``visibility_timeout`` but *restores* it only when a consumer checks: once
# when its consumer starts and then every tenth ``maybe_restore_messages``
# call, which the event loop schedules every 10 s — i.e. about every 100 s.
RESTORE_DEADLINE = 200.0
TASK = "backend.jobs.process_meeting["
RECEIVED = "] received"
SUCCEEDED = "] succeeded in"
# The prefork parent notices a killed pool child through the child's sentinel
# or, failing that, its pool-maintenance timer (``AsynPool.timers``: every
# 5 s).  On Linux the timer is what fires, so the killed child's delivery is
# requeued up to ~5 s after the kill.
POOL_MAINTENANCE_SECONDS = 5.0
# The kill-child drill needs that requeued copy to meet a *live* lease, so its
# worker runs with a lease that outlasts the detection delay (renewed every
# half lease; the other drills keep the shorter LEASE_SECONDS).
KILL_CHILD_LEASE_SECONDS = 12
# A copy that meets a live attempt is acknowledged without starting anything.
# Its claim locks the job row with SKIP LOCKED, so it answers
# ``already_running`` when the row is free and ``busy`` when it arrives while
# the live attempt's heartbeat (or another protected write) holds the row for
# its few milliseconds.  Both answers are inert.
INERT_COPY_STATUSES = frozenset({"already_running", "busy"})
# The redelivery drill holds its attempt for kombu's ~100 s restore cadence;
# 2 s of renewal slack (the 4 s LEASE_SECONDS renewed every 2 s) does not
# survive a slow runner's scheduling stalls over that long, so its workers run
# with a lease that does (renewed every third of the lease).
LIVE_ATTEMPT_LEASE_SECONDS = 30
LIVE_ATTEMPT_RENEW_SECONDS = 10
# Lines of each worker's log printed at teardown; pytest shows them only when
# the drill failed, which is the only evidence a CI run keeps.
LOG_TAIL_LINES = 40
_RESULT_STATUS = re.compile(r"'status': '([a-z_]+)'")


def _wait(predicate, *, timeout: float = DEADLINE, what: str) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"{what}: not reached within {timeout}s")
        time.sleep(0.1)


def _settlements(workspace_id: str) -> int:
    return sum(1 for event in _events(workspace_id) if event.event_type == "committed")


def _violations(workspace_id: str, settings) -> list[invariants.Violation]:
    with inspection_session_factory()() as db:
        return invariants.check_workspace(
            db, workspace_id, settings=settings, storage=create_object_storage(settings)
        )


def _stage_runs(job_id: str) -> list[tuple[int, str, str, str | None]]:
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(ProcessingStageRun)
            .where(ProcessingStageRun.job_id == job_id)
            .order_by(ProcessingStageRun.attempt.asc(), ProcessingStageRun.sequence.asc())
        ).all()
        return [(r.attempt, r.stage, r.state.value, r.reason_code) for r in rows]


def _transcript_rows(meeting_id: str) -> int:
    with inspection_session_factory()() as db:
        return len(
            db.scalars(
                select(TranscriptSegment.id).where(TranscriptSegment.meeting_id == meeting_id)
            ).all()
        )


def _dispatch(job_id: str) -> JobDispatch:
    with inspection_session_factory()() as db:
        row = db.scalar(select(JobDispatch).where(JobDispatch.job_id == job_id))
        assert row is not None
        db.expunge(row)
        return row


def _received(process: TransportProcess) -> int:
    return process.count(TASK, and_also=RECEIVED)


def _succeeded(process: TransportProcess, status: str | None = None) -> int:
    return process.count(TASK, and_also=SUCCEEDED if status is None else f"'status': '{status}'")


def _statuses(*processes: TransportProcess) -> list[str]:
    """The ``status`` of every task result the workers logged."""
    return [
        match.group(1)
        for process in processes
        for line in process.lines()
        if TASK in line and SUCCEEDED in line
        for match in [_RESULT_STATUS.search(line)]
        if match
    ]


def _holding_asr(job_id: str) -> bool:
    """The attempt is running and its ASR stage is held at the gate."""
    job = _job(job_id)
    return job.state == JobState.RUNNING and job.stage == "transcribing"


def _lease_lapsed(job_id: str) -> bool:
    expires = as_utc(_job(job_id).lease_expires_at)
    return expires is not None and expires <= datetime.now(UTC)


def _lease_left(job_id: str) -> float:
    """Seconds until the current lease expires (negative once lapsed)."""
    expires = as_utc(_job(job_id).lease_expires_at)
    if expires is None:
        return float("-inf")
    return (expires - datetime.now(UTC)).total_seconds()


def _completed_once(job_id: str, workspace_id: str, settings, *, attempts: int) -> None:
    job = _job(job_id)
    assert job.state == JobState.COMPLETED, (job.state, job.failure_code)
    assert job.attempt_count == attempts
    assert job.dispatch.state == DispatchState.PUBLISHED
    assert _transcript_rows(job.meeting_id) == 1
    assert _settlements(workspace_id) == 1
    assert _violations(workspace_id, settings) == []


class Drill:
    def __init__(self, settings, client, email_outbox, monkeypatch, tmp_path, redis_url: str):
        self.settings = settings
        self.client = client
        self.email_outbox = email_outbox
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.redis_url = redis_url
        self.gate = tmp_path / "asr-gate"
        self.processes: list[TransportProcess] = []

    def queued_job(self) -> tuple[str, str]:
        """An accepted upload whose publish intent is still PENDING."""
        return _queued_upload(self.client, self.email_outbox, self.monkeypatch)

    def open_gate(self) -> None:
        self.gate.write_text("open", encoding="utf-8")

    def redis(self) -> dict[str, int]:
        return queue_state(self.redis_url)

    def worker(
        self,
        name: str,
        *,
        gated: bool = True,
        visibility_timeout: int | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> TransportProcess:
        process = spawn_worker(
            self.settings,
            self.redis_url,
            name=name,
            gate=self.gate if gated else None,
            visibility_timeout=visibility_timeout,
            extra_env=extra_env,
        )
        self.processes.append(process)
        process.wait_for(" ready.", timeout=DEADLINE)
        return process

    def beat(self, *, extra_env: dict[str, str] | None = None) -> TransportProcess:
        process = spawn_beat(
            self.settings,
            self.redis_url,
            schedule_path=self.tmp_path / "celerybeat-schedule",
            extra_env=extra_env,
        )
        self.processes.append(process)
        process.wait_for("beat: Starting...", timeout=DEADLINE)
        return process

    def shutdown(self) -> None:
        for process in reversed(self.processes):
            process.stop()
            # Kept under pytest's tmp_path for failure analysis (the drills
            # assert on log evidence; a bare count is hard to read).
            log_path = self.tmp_path / f"{process.name}.log"
            log_path.write_text("\n".join(process.lines()) + "\n", encoding="utf-8")
            print(f"transport log: {log_path}")
            print(f"--- {process.name}: last {LOG_TAIL_LINES} lines ---")
            print(process.tail(LOG_TAIL_LINES))


@pytest.fixture
def drill(settings, client, email_outbox, monkeypatch, tmp_path):
    redis_url = configured_redis_url()
    if redis_url is None:
        pytest.skip("requires a disposable Redis (TEST_REDIS_URL unset)")
    if not is_postgres_url(configured_database_url()):
        pytest.skip("row locking and fencing are PostgreSQL evidence")
    flush_test_database(redis_url)
    instance = Drill(settings, client, email_outbox, monkeypatch, tmp_path, redis_url)
    try:
        yield instance
    finally:
        instance.shutdown()
        flush_test_database(redis_url)


# --- worker death -------------------------------------------------------------


def test_killed_pool_child_requeues_inertly_and_recovery_settles_once(drill: Drill):
    job_id, workspace_id = drill.queued_job()
    worker = drill.worker(
        "kill-child",
        extra_env={
            "JOB_LEASE_SECONDS": str(KILL_CHILD_LEASE_SECONDS),
            "JOB_LEASE_RENEW_INTERVAL_SECONDS": str(KILL_CHILD_LEASE_SECONDS // 2),
        },
    )
    with use_broker(drill.redis_url):
        assert outbox.dispatch_job(job_id, drill.settings).state == "published"
        _wait(lambda: _holding_asr(job_id), what="first attempt held at the ASR gate")
        first = _job(job_id)
        assert (first.attempt_count, first.lease_generation) == (1, 1)
        assert (first.locked_by or "").startswith("celery:")
        # The requeued copy (below) must meet a live lease: kill only while the
        # lease outlasts the pool's detection delay with room for redelivery.
        _wait(
            lambda: _lease_left(job_id) >= POOL_MAINTENANCE_SECONDS + 2,
            what="lease outlasting the pool's detection delay",
        )

        # SIGKILL the pool child in the middle of the ASR stage (held at the gate).
        worker.kill_child()
        worker.wait_for("WorkerLostError", timeout=DEADLINE)
        # acks_late + reject_on_worker_lost: the delivery is requeued and the
        # new pool child receives it while the first attempt's lease is still
        # valid — the copy must be acknowledged without starting anything.
        worker.wait_for(TASK, and_also=RECEIVED, timeout=DEADLINE, count=2)
        worker.wait_for(TASK, and_also="'status': 'already_running'", timeout=DEADLINE)
        _wait(
            lambda: drill.redis() == {"queued": 0, "unacked": 0}, what="requeued copy acknowledged"
        )
        job = _job(job_id)
        assert job.state == JobState.RUNNING
        assert (job.attempt_count, job.lease_generation) == (1, 1)

        # The heartbeat thread died with the child: the lease lapses on its own.
        _wait(
            lambda: _lease_lapsed(job_id),
            timeout=KILL_CHILD_LEASE_SECONDS * 3,
            what="lease lapse",
        )
        drill.open_gate()
        assert recovery.sweep_expired_leases(drill.settings) == {"scanned": 1, "recovered": 1}
        _wait(lambda: _job(job_id).state == JobState.COMPLETED, what="recovered attempt completed")
        _wait(
            lambda: drill.redis() == {"queued": 0, "unacked": 0},
            what="recovery delivery acknowledged",
        )
    worker.stop()

    _completed_once(job_id, workspace_id, drill.settings, attempts=2)
    # claim (1) -> recovery fence (2) -> second claim (3)
    assert _job(job_id).lease_generation == 3
    runs = _stage_runs(job_id)
    assert (1, "asr", "failed", "lease_expired") in runs
    assert [r for r in runs if r[0] == 2 and r[2] != "completed"] == []
    # three deliveries: the original (child killed), the requeued copy
    # (inert) and the recovery intent (completed); two returned a result
    assert _received(worker) == 3 and _succeeded(worker) == 2
    assert _succeeded(worker, "already_running") == 1 and _succeeded(worker, "completed") == 1


def test_killed_worker_parks_the_delivery_and_recovery_needs_no_broker_help(drill: Drill):
    job_id, workspace_id = drill.queued_job()
    worker = drill.worker("kill-all", visibility_timeout=VISIBILITY_SECONDS)
    with use_broker(drill.redis_url):
        assert outbox.dispatch_job(job_id, drill.settings).state == "published"
        _wait(lambda: _holding_asr(job_id), what="first attempt held at the ASR gate")

        # SIGKILL parent and pool child: nothing acknowledges, nothing requeues.
        worker.kill_group()
        assert drill.redis() == {"queued": 0, "unacked": 1}

        # Recovery is driven by the lease, not by the broker: the job is
        # requeued through the outbox while the dead consumer's delivery is
        # still parked.
        _wait(lambda: _lease_lapsed(job_id), timeout=LEASE_SECONDS * 3, what="lease lapse")
        drill.open_gate()
        assert recovery.sweep_expired_leases(drill.settings) == {"scanned": 1, "recovered": 1}
        assert drill.redis() == {"queued": 1, "unacked": 1}

        healthy = drill.worker("healthy", visibility_timeout=VISIBILITY_SECONDS)
        _wait(lambda: _job(job_id).state == JobState.COMPLETED, what="recovered attempt completed")
        # After the visibility timeout kombu restores the parked delivery; the
        # healthy worker consumes it and it is inert (the job is done).
        _wait(
            lambda: drill.redis() == {"queued": 0, "unacked": 0},
            timeout=RESTORE_DEADLINE,
            what="parked delivery restored and acknowledged",
        )
        healthy.wait_for(TASK, and_also=RECEIVED, timeout=DEADLINE, count=2)
        healthy.wait_for(TASK, and_also=SUCCEEDED, timeout=DEADLINE, count=2)
    healthy.stop()

    _completed_once(job_id, workspace_id, drill.settings, attempts=2)
    assert (1, "asr", "failed", "lease_expired") in _stage_runs(job_id)
    # one delivery ran the recovered attempt, the other found the job done
    assert _received(healthy) == 2 and _succeeded(healthy, "completed") == 2


def test_visibility_timeout_redelivery_during_a_live_attempt_is_inert(drill: Drill):
    job_id, workspace_id = drill.queued_job()
    live_lease = {
        "JOB_LEASE_SECONDS": str(LIVE_ATTEMPT_LEASE_SECONDS),
        "JOB_LEASE_RENEW_INTERVAL_SECONDS": str(LIVE_ATTEMPT_RENEW_SECONDS),
    }
    holder = drill.worker("holder", visibility_timeout=VISIBILITY_SECONDS, extra_env=live_lease)
    other = drill.worker("other", visibility_timeout=VISIBILITY_SECONDS, extra_env=live_lease)
    with use_broker(drill.redis_url):
        assert outbox.dispatch_job(job_id, drill.settings).state == "published"
        _wait(lambda: _holding_asr(job_id), what="attempt held at the ASR gate")
        # The attempt is held at the gate longer than the visibility timeout:
        # kombu makes the unacknowledged delivery visible again and the idle
        # worker takes it.  The held attempt returns nothing before the gate
        # opens, so the first result logged is the copy's; any answer counts
        # here and is checked below, so a wrong one fails at once.
        _wait(
            lambda: _statuses(holder, other),
            timeout=RESTORE_DEADLINE,
            what="redelivered copy answered",
        )
        (copy_status,) = _statuses(holder, other)
        assert copy_status in INERT_COPY_STATUSES, copy_status
        job = _job(job_id)
        assert job.state == JobState.RUNNING
        assert (job.attempt_count, job.lease_generation) == (1, 1)
        drill.open_gate()
        _wait(lambda: _job(job_id).state == JobState.COMPLETED, what="held attempt completed")
        # COMPLETED commits inside the task before it returns, and stop() signals
        # the whole process group, pool child included: wait for the task's own
        # result line so the child is not killed before it logs it.
        _wait(
            lambda: "completed" in _statuses(holder, other),
            what="held attempt returned its result",
        )
        _wait(
            lambda: drill.redis() == {"queued": 0, "unacked": 0}, what="all deliveries acknowledged"
        )
    holder.stop()
    other.stop()

    _completed_once(job_id, workspace_id, drill.settings, attempts=1)
    assert _received(holder) + _received(other) == 2
    # two results: the inert copy and the one attempt that completed
    assert sorted(_statuses(holder, other)) == sorted([copy_status, "completed"])


# --- broker interruption --------------------------------------------------------


def test_broker_restart_and_outage_keep_accepted_work_deliverable(drill: Drill):
    container = configured_redis_container()
    if container is None or shutil.which("docker") is None:
        pytest.skip("requires TEST_REDIS_CONTAINER and the docker CLI")
    first_id, first_ws = drill.queued_job()
    second_id, second_ws = drill.queued_job()
    third_id, third_ws = drill.queued_job()
    drill.open_gate()
    settings = drill.settings
    with use_broker(drill.redis_url):
        # 1. A delivery published before a broker restart survives it (AOF).
        assert outbox.dispatch_job(first_id, settings).state == "published"
        assert drill.redis()["queued"] == 1
        docker_container("restart", container)
        wait_for_redis(drill.redis_url, timeout=DEADLINE)
        assert drill.redis()["queued"] == 1
        worker = drill.worker("reconnect")
        _wait(
            lambda: _job(first_id).state == JobState.COMPLETED, what="persisted delivery processed"
        )

        # 2. A running worker survives a broker restart and consumes again.
        docker_container("restart", container)
        wait_for_redis(drill.redis_url, timeout=DEADLINE)
        worker.wait_for("Trying to re-establish the connection", timeout=DEADLINE)
        assert outbox.dispatch_job(second_id, settings).state == "published"
        _wait(
            lambda: _job(second_id).state == JobState.COMPLETED,
            what="post-restart delivery processed",
        )

        # 3. Publication during an outage is deferred, never lost, and swept later.
        docker_container("stop", container)
        assert outbox.dispatch_job(third_id, settings).state == "deferred"
        row = _dispatch(third_id)
        assert row.state == DispatchState.PENDING and row.attempt_count == 1
        assert row.last_error and row.message_id is None
        assert _job(third_id).state == JobState.QUEUED
        docker_container("start", container)
        wait_for_redis(drill.redis_url, timeout=DEADLINE)
        later = datetime.now(UTC) + timedelta(seconds=settings.dispatch_retry_max_seconds + 1)
        assert outbox.sweep_dispatch_outbox(settings, now=later) == {"scanned": 1, "published": 1}
        _wait(lambda: _job(third_id).state == JobState.COMPLETED, what="swept delivery processed")
        _wait(
            lambda: drill.redis() == {"queued": 0, "unacked": 0}, what="all deliveries acknowledged"
        )
    worker.stop()

    for job_id, workspace_id in (
        (first_id, first_ws),
        (second_id, second_ws),
        (third_id, third_ws),
    ):
        _completed_once(job_id, workspace_id, settings, attempts=1)
    assert _dispatch(third_id).attempt_count == 2


# --- scheduler ---------------------------------------------------------------------


def test_beat_schedules_the_dispatch_and_lease_sweeps(drill: Drill):
    periods = {"DISPATCH_SWEEP_INTERVAL_SECONDS": "2", "JOB_RECOVERY_SWEEP_INTERVAL_SECONDS": "2"}
    drill.open_gate()
    worker = drill.worker("beat-worker", gated=False, extra_env=periods)
    beat = drill.beat(extra_env=periods)

    # A: accepted, intent PENDING, never published by the test.
    pending_id, pending_ws = drill.queued_job()
    # B: an attempt whose worker vanished (claimed, lease lapsed, nobody resumes).
    abandoned_id, abandoned_ws = drill.queued_job()
    _, status = processor._claim_job(abandoned_id, worker_id="vanished", settings=drill.settings)
    assert status == "claimed"
    _expire_lease(abandoned_id)

    _wait(lambda: _job(pending_id).state == JobState.COMPLETED, what="beat-published job completed")
    _wait(
        lambda: _job(abandoned_id).state == JobState.COMPLETED, what="beat-recovered job completed"
    )
    beat.wait_for("Sending due task dispatch-outbox-sweep", timeout=DEADLINE)
    beat.wait_for("Sending due task job-lease-recovery", timeout=DEADLINE)
    worker.wait_for("backend.jobs.sweep_dispatch_outbox[", timeout=DEADLINE)
    worker.wait_for("backend.jobs.sweep_expired_leases[", timeout=DEADLINE)
    _wait(lambda: drill.redis()["unacked"] == 0, what="sweep deliveries acknowledged")
    beat.stop()
    worker.stop()

    _completed_once(pending_id, pending_ws, drill.settings, attempts=1)
    assert _dispatch(pending_id).message_id
    _completed_once(abandoned_id, abandoned_ws, drill.settings, attempts=2)
    assert _job(abandoned_id).lease_generation == 3  # claim, fence, second claim
    # the vanished worker never advanced past the claim: its first stage is
    # the one recorded as abandoned
    assert (1, "materialize", "failed", "lease_expired") in _stage_runs(abandoned_id)
