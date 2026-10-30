"""HOM-333 oracle: a release, a settlement and the usage reconciliation never deadlock.

Written from the specification before the fix. Each race test stops one session
while it holds its locks (the reconciliation before it releases its orphan, or a
release or settlement before it commits), sees the other session wait in
``pg_stat_activity`` on a lock the first one holds, then lets both finish. No
session may be aborted as a deadlock (SQLSTATE ``40P01``), the reservation must be
final exactly once, and the counters must match the reservation rows. The
sequential controls check what must still happen: a lone reconciliation releases
an orphan, a second call on a final reservation writes nothing, and a session that
loaded a reservation before another session released it decides on the fresh row.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    Entitlement,
    JobState,
    Meeting,
    ProcessingJob,
    ReservationState,
    UsageEvent,
    UsageReservation,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs import usage_reconciliation
from backend.jobs.failures import cancel_job, release_job_reservations
from backend.jobs.usage_reconciliation import (
    ORPHAN_REASON,
    EntitlementRef,
    reconcile_entitlement,
)
from backend.services.entitlement_service import (
    ReservationStateError,
    commit_usage,
    release_usage,
    reserve_usage,
)
from backend.tests._cost import WIDE_BOUND_SECONDS
from backend.tests._database import inspection_session_factory
from backend.tests.test_job_processor import _queued_upload

RESERVED = ReservationState.RESERVED
COMMITTED = ReservationState.COMMITTED
RELEASED = ReservationState.RELEASED
RELEASE_REASON = "hom333_release"
CANCEL_REASON = "hom333_cancel"
ORPHAN_SECONDS = 5
ACTUAL_SECONDS = 7
RELEASED_CANNOT_BE_COMMITTED = "Released reservation cannot be committed"
COMMITTED_CANNOT_BE_RELEASED = "Committed reservation cannot be released"
RECONCILIATION_FIRST = "reconciliation_first"


# --- the scene ----------------------------------------------------------------------------


def _scene(client, email_outbox, monkeypatch, settings, kind: str) -> SimpleNamespace:
    """One queued upload (its audio and concurrency reservations) and an orphan of ``kind``.

    ``unreferenced``: an extra reservation of the job's meeting that no job references,
    while the job stays queued. ``parked``: the job is dead-lettered without releasing (a
    legacy row), so its own reservations are orphans. ``meeting_deleted``: the job stays
    queued but its meeting is deleted. Every reservation is older than the grace period.
    """
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    aged = datetime.now(UTC) - timedelta(seconds=settings.usage_reconciliation_grace_seconds * 2)
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        scene = SimpleNamespace(
            workspace_id=workspace_id,
            job_id=job_id,
            audio_id=job.reservation_id,
            concurrency_id=job.concurrency_reservation_id,
            orphan_id=job.reservation_id,
        )
        if kind == "unreferenced":
            scene.orphan_id = reserve_usage(
                db,
                workspace_id=workspace_id,
                meeting_id=job.meeting_id,
                amount=ORPHAN_SECONDS,
                idempotency_key="hom333:orphan",
            ).id
        elif kind == "parked":
            job.state = JobState.DEAD_LETTER
            job.stage = "dead_letter"
        else:
            db.get(Meeting, job.meeting_id).deleted_at = datetime.now(UTC)
        for reservation in db.scalars(
            select(UsageReservation).where(UsageReservation.workspace_id == workspace_id)
        ):
            reservation.created_at = aged
        db.commit()
        orphan = db.get(UsageReservation, scene.orphan_id)
        scene.ref = EntitlementRef(orphan.entitlement_id, workspace_id, "audio_seconds")
    assert scene.audio_id and scene.concurrency_id
    assert _state(scene.orphan_id) is RESERVED
    return scene


@contextmanager
def _app_session(workspace_id: str) -> Iterator[Session]:
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=workspace_id)
        yield db


def _state(reservation_id: str) -> ReservationState:
    with inspection_session_factory()() as db:
        return db.get(UsageReservation, reservation_id).state


def _released_reason(reservation_id: str) -> str | None:
    """The reason of the reservation's one ``released`` event."""
    with inspection_session_factory()() as db:
        reasons = db.scalars(
            select(UsageEvent.reason).where(
                UsageEvent.reservation_id == reservation_id, UsageEvent.event_type == "released"
            )
        ).all()
    assert len(reasons) == 1, reasons
    return reasons[0]


def _snapshot(workspace_id: str) -> tuple:
    """Every reservation, usage event and counter of the workspace, to compare."""
    with inspection_session_factory()() as db:
        reservations = sorted(
            (r.id, r.state, r.committed_value, r.failure_reason, r.finalized_at)
            for r in db.scalars(
                select(UsageReservation).where(UsageReservation.workspace_id == workspace_id)
            )
        )
        events = sorted(
            (e.id, e.reservation_id, e.event_type, e.value, e.reason)
            for e in db.scalars(select(UsageEvent).where(UsageEvent.workspace_id == workspace_id))
        )
        counters = sorted(
            (e.metric, e.used_value, e.reserved_value)
            for e in db.scalars(select(Entitlement).where(Entitlement.workspace_id == workspace_id))
        )
    return reservations, events, counters


def _assert_final_once_and_counters_match(workspace_id: str) -> None:
    """Each reservation has one ``reserved`` event plus one event of its final state and
    nothing else, and each entitlement's counters equal what its reservation rows imply."""
    final = {RESERVED: {}, RELEASED: {"released": 1}, COMMITTED: {"committed": 1}}
    with inspection_session_factory()() as db:
        reservations = db.scalars(
            select(UsageReservation).where(UsageReservation.workspace_id == workspace_id)
        ).all()
        assert reservations
        for reservation in reservations:
            events = db.scalars(
                select(UsageEvent).where(UsageEvent.reservation_id == reservation.id)
            ).all()
            kinds = Counter(e.event_type for e in events)
            assert kinds == Counter({"reserved": 1, **final[reservation.state]}), (
                reservation.id,
                reservation.state,
                kinds,
            )
            for e in events:
                expected = (
                    reservation.committed_value
                    if e.event_type == "committed"
                    else reservation.reserved_value
                )
                assert e.value == expected, (reservation.id, e.event_type, e.value)
        for entitlement in db.scalars(
            select(Entitlement).where(Entitlement.workspace_id == workspace_id)
        ):
            rows = [r for r in reservations if r.entitlement_id == entitlement.id]
            used = sum(r.committed_value or 0 for r in rows if r.state is COMMITTED)
            reserved = sum(r.reserved_value for r in rows if r.state is RESERVED)
            assert (entitlement.used_value, entitlement.reserved_value) == (used, reserved), (
                entitlement.metric
            )


# --- sessions in threads ------------------------------------------------------------------


def _deadlock(error: BaseException | None) -> bool:
    """SQLSTATE 40P01, raised by psycopg or wrapped by SQLAlchemy."""
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, psycopg.errors.DeadlockDetected):
            return True
        if getattr(error, "sqlstate", None) == "40P01":
            return True
        error = getattr(error, "orig", None) or error.__cause__ or error.__context__
    return False


class _Worker(threading.Thread):
    """Runs ``work`` in its own thread and keeps its result or its error."""

    def __init__(self, work: Callable[[], Any]) -> None:
        super().__init__(daemon=True)
        self._work = work
        self.result: Any = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.result = self._work()
        except BaseException as exc:  # noqa: BLE001 - checked by the test
            self.error = exc

    def value(self) -> Any:
        """The work's result; re-raises the error it ended with."""
        if self.error is not None:
            raise self.error
        return self.result


@contextmanager
def _backend_pids() -> Iterator[dict[int, int]]:
    """The backend pid of the latest application transaction of each thread."""
    pids: dict[int, int] = {}
    factory = get_session_factory()

    def record(_session, _transaction, connection) -> None:
        pids[threading.get_ident()] = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar()

    event.listen(factory, "after_begin", record)
    try:
        yield pids
    finally:
        event.remove(factory, "after_begin", record)


def _waits_behind(worker: _Worker, pids: dict[int, int], holder: _Worker) -> None:
    """``worker``'s own backend is seen waiting on a lock that ``holder``'s backend holds."""
    deadline = time.monotonic() + WIDE_BOUND_SECONDS
    while time.monotonic() < deadline:
        own, held_by = pids.get(worker.ident), pids.get(holder.ident)
        if own is not None and held_by is not None:
            with inspection_session_factory()() as db:
                seen = db.execute(
                    text(
                        "SELECT 1 FROM pg_stat_activity "
                        "WHERE pid = :own AND wait_event_type = 'Lock' "
                        "AND CAST(:holder AS integer) = ANY(pg_blocking_pids(pid))"
                    ),
                    {"own": own, "holder": held_by},
                ).first()
            if seen is not None:
                return
        if not worker.is_alive():
            pytest.fail(f"the session never waited on a lock (error: {worker.error!r})")
        time.sleep(0.01)
    pytest.fail("the session did not wait on the other session's lock within the hang guard")


def _pause_reconciliation(monkeypatch) -> SimpleNamespace:
    """The reconciliation's first release waits for ``gate`` before it runs.

    The reconciliation then holds the entitlement row and has not released its orphan.
    """
    real = usage_reconciliation.release_usage
    pause = SimpleNamespace(entered=threading.Event(), gate=threading.Event())

    def paused(*args: Any, **kwargs: Any) -> Any:
        if not pause.entered.is_set():
            pause.entered.set()
            assert pause.gate.wait(WIDE_BOUND_SECONDS)
        return real(*args, **kwargs)

    monkeypatch.setattr(usage_reconciliation, "release_usage", paused)
    return pause


def _held(hold: SimpleNamespace | None) -> None:
    """With ``hold``, the caller stops here with its locks until ``hold.gate``."""
    if hold is not None:
        hold.entered.set()
        assert hold.gate.wait(WIDE_BOUND_SECONDS)


def _race(
    monkeypatch,
    settings,
    scene: SimpleNamespace,
    order: str,
    other: Callable[[SimpleNamespace | None], Callable[[], Any]],
) -> tuple[_Worker, _Worker]:
    """The reconciliation and ``other``, one of them stopped while it holds its locks.

    ``reconciliation_first``: the reconciliation stops before its first release and
    ``other`` must wait behind it. Otherwise ``other`` stops before it commits and the
    reconciliation must wait behind it. Returns (reconciliation, other) once both
    ended, neither aborted as a deadlock.
    """

    def reconcile_work() -> Any:
        return reconcile_entitlement(scene.ref, settings, repair_counters=False)

    with _backend_pids() as pids:
        if order == RECONCILIATION_FIRST:
            pause = _pause_reconciliation(monkeypatch)
            reconcile = first = _Worker(reconcile_work)
            action = second = _Worker(other(None))
        else:
            pause = SimpleNamespace(entered=threading.Event(), gate=threading.Event())
            action = first = _Worker(other(pause))
            reconcile = second = _Worker(reconcile_work)
        started: list[_Worker] = []
        try:
            first.start()
            started.append(first)
            assert pause.entered.wait(WIDE_BOUND_SECONDS), (
                f"the first session never stopped with its locks (error: {first.error!r})"
            )
            second.start()
            started.append(second)
            _waits_behind(second, pids, first)
        finally:
            pause.gate.set()
            for worker in started:
                worker.join(WIDE_BOUND_SECONDS)
    assert not [w for w in (first, second) if w.is_alive()], "a session did not finish"
    deadlocks = [w.error for w in (first, second) if _deadlock(w.error)]
    assert deadlocks == [], f"aborted as a deadlock (40P01): {deadlocks!r}"
    return reconcile, action


def _keep_loaded_copy(db: Session, reservation_id: str) -> None:
    """The session loads the reservation without a lock and keeps that copy.

    ``release_job_reservations`` does this with a local variable. The identity map
    holds rows weakly, so the copy is kept by a strong reference for the session's life.
    """
    copy = db.get(UsageReservation, reservation_id)
    assert copy is not None and copy.state is RESERVED
    db.info["hom333_loaded_copy"] = copy


def _release(scene: SimpleNamespace, path: str) -> Callable:
    def make(hold: SimpleNamespace | None) -> Callable[[], None]:
        def work() -> None:
            with _app_session(scene.workspace_id) as db:
                if path == "release_job_reservations":
                    job = db.get(ProcessingJob, scene.job_id)
                    release_job_reservations(db, job, reason=RELEASE_REASON)
                else:
                    if path == "release_usage_loaded":
                        _keep_loaded_copy(db, scene.orphan_id)
                    release_usage(db, scene.orphan_id, reason=RELEASE_REASON)
                _held(hold)
                db.commit()

        return work

    return make


def _settle(scene: SimpleNamespace, path: str) -> Callable:
    def make(hold: SimpleNamespace | None) -> Callable[[], None]:
        def work() -> None:
            with _app_session(scene.workspace_id) as db:
                if path == "commit_usage_loaded":
                    _keep_loaded_copy(db, scene.orphan_id)
                commit_usage(db, scene.orphan_id, actual_amount=ACTUAL_SECONDS)
                _held(hold)
                db.commit()

        return work

    return make


def _cancel(scene: SimpleNamespace) -> Callable:
    def make(hold: SimpleNamespace | None) -> Callable[[], None]:
        def work() -> None:
            with _app_session(scene.workspace_id) as db:
                job = db.scalar(
                    select(ProcessingJob).where(ProcessingJob.id == scene.job_id).with_for_update()
                )
                assert cancel_job(
                    db, job, reason=CANCEL_REASON, message="cancelled", now=datetime.now(UTC)
                )
                _held(hold)
                db.commit()

        return work

    return make


# --- AC1, AC2, AC3, AC5: a release of an orphan against the reconciliation ---------------


@pytest.mark.postgres
@pytest.mark.parametrize("order", [RECONCILIATION_FIRST, "release_first"])
@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("release_usage", "unreferenced"),
        ("release_usage", "meeting_deleted"),
        ("release_usage_loaded", "parked"),
        ("release_job_reservations", "parked"),
    ],
)
def test_a_release_of_an_orphan_and_the_reconciliation_finish_without_a_deadlock(
    client, email_outbox, monkeypatch, settings, order, path, kind
):
    scene = _scene(client, email_outbox, monkeypatch, settings, kind)
    reconcile, release = _race(monkeypatch, settings, scene, order, _release(scene, path))
    outcome = reconcile.value()
    release.value()

    assert outcome.found and outcome.drift is False
    assert set(outcome.orphans_released) <= {scene.orphan_id}
    assert len(outcome.orphans_released) == len(set(outcome.orphans_released))
    assert _state(scene.orphan_id) is RELEASED
    reason = _released_reason(scene.orphan_id)
    if order == RECONCILIATION_FIRST:
        by_reconciliation = scene.orphan_id in outcome.orphans_released
        assert reason == (ORPHAN_REASON if by_reconciliation else RELEASE_REASON)
    else:
        # The release committed first: the reconciliation must not release it again.
        assert scene.orphan_id not in outcome.orphans_released
        assert reason == RELEASE_REASON
    if path == "release_job_reservations":
        assert _state(scene.concurrency_id) is RELEASED
        assert _released_reason(scene.concurrency_id) == RELEASE_REASON
    if kind == "unreferenced":
        assert _state(scene.audio_id) is RESERVED
    _assert_final_once_and_counters_match(scene.workspace_id)


# --- AC4, AC5: a settlement of an orphan against the reconciliation -----------------------


@pytest.mark.postgres
@pytest.mark.parametrize("order", [RECONCILIATION_FIRST, "settlement_first"])
@pytest.mark.parametrize("path", ["commit_usage", "commit_usage_loaded"])
def test_a_settlement_of_an_orphan_and_the_reconciliation_finish_without_a_deadlock(
    client, email_outbox, monkeypatch, settings, order, path
):
    scene = _scene(client, email_outbox, monkeypatch, settings, "parked")
    reconcile, settle = _race(monkeypatch, settings, scene, order, _settle(scene, path))
    outcome = reconcile.value()

    assert outcome.found and outcome.drift is False
    final = _state(scene.orphan_id)
    if settle.error is None:
        assert final is COMMITTED
        assert scene.orphan_id not in outcome.orphans_released
        with inspection_session_factory()() as db:
            assert db.get(UsageReservation, scene.orphan_id).committed_value == ACTUAL_SECONDS
    else:
        # The one refusal allowed: the losing settlement of a reservation already released.
        assert isinstance(settle.error, ReservationStateError), repr(settle.error)
        assert str(settle.error) == RELEASED_CANNOT_BE_COMMITTED
        assert final is RELEASED
        assert outcome.orphans_released == [scene.orphan_id]
        assert _released_reason(scene.orphan_id) == ORPHAN_REASON
    if order != RECONCILIATION_FIRST:
        assert final is COMMITTED
    _assert_final_once_and_counters_match(scene.workspace_id)


# --- AC6: allow-controls ------------------------------------------------------------------


@pytest.mark.postgres
@pytest.mark.parametrize("order", [RECONCILIATION_FIRST, "cancellation_first"])
def test_a_cancellation_of_a_live_job_and_the_reconciliation_both_finish(
    client, email_outbox, monkeypatch, settings, order
):
    """The reconciliation skips the live job's reservation and releases only the real
    orphan; the cancellation releases the job's reservations once."""
    scene = _scene(client, email_outbox, monkeypatch, settings, "unreferenced")
    reconcile, cancel = _race(monkeypatch, settings, scene, order, _cancel(scene))
    outcome = reconcile.value()
    cancel.value()

    assert outcome.found and outcome.drift is False
    assert outcome.orphans_released == [scene.orphan_id]
    assert _released_reason(scene.orphan_id) == ORPHAN_REASON
    for live in (scene.audio_id, scene.concurrency_id):
        assert _state(live) is RELEASED
        assert _released_reason(live) == CANCEL_REASON
    with inspection_session_factory()() as db:
        assert db.get(ProcessingJob, scene.job_id).state is JobState.CANCELLED
    _assert_final_once_and_counters_match(scene.workspace_id)


@pytest.mark.parametrize("kind", ["unreferenced", "parked", "meeting_deleted"])
def test_a_reconciliation_alone_releases_an_orphan_and_reports_it(
    client, email_outbox, monkeypatch, settings, kind
):
    scene = _scene(client, email_outbox, monkeypatch, settings, kind)
    outcome = reconcile_entitlement(scene.ref, settings, repair_counters=False)

    assert outcome.orphans_released == [scene.orphan_id]
    assert outcome.state == "orphans" and outcome.drift is False
    assert _state(scene.orphan_id) is RELEASED
    assert _released_reason(scene.orphan_id) == ORPHAN_REASON
    if kind == "unreferenced":
        assert _state(scene.audio_id) is RESERVED
    _assert_final_once_and_counters_match(scene.workspace_id)


def test_a_second_call_on_a_final_reservation_returns_it_and_writes_nothing(
    client, email_outbox, monkeypatch, settings
):
    scene = _scene(client, email_outbox, monkeypatch, settings, "unreferenced")
    with _app_session(scene.workspace_id) as db:
        release_usage(db, scene.orphan_id, reason=RELEASE_REASON)
        db.commit()
    with _app_session(scene.workspace_id) as db:
        commit_usage(db, scene.audio_id, actual_amount=ACTUAL_SECONDS)
        db.commit()
    before = _snapshot(scene.workspace_id)

    with _app_session(scene.workspace_id) as db:
        again = release_usage(db, scene.orphan_id, reason="released twice")
        assert (again.id, again.state) == (scene.orphan_id, RELEASED)
        db.commit()
    with _app_session(scene.workspace_id) as db:
        again = commit_usage(db, scene.audio_id, actual_amount=ACTUAL_SECONDS - 1)
        assert (again.id, again.state, again.committed_value) == (
            scene.audio_id,
            COMMITTED,
            ACTUAL_SECONDS,
        )
        db.commit()
    assert _snapshot(scene.workspace_id) == before

    with _app_session(scene.workspace_id) as db:
        with pytest.raises(ReservationStateError, match=f"^{COMMITTED_CANNOT_BE_RELEASED}$"):
            release_usage(db, scene.audio_id, reason="release a committed one")
    with _app_session(scene.workspace_id) as db:
        with pytest.raises(ReservationStateError, match=f"^{RELEASED_CANNOT_BE_COMMITTED}$"):
            commit_usage(db, scene.orphan_id)
    assert _snapshot(scene.workspace_id) == before
    _assert_final_once_and_counters_match(scene.workspace_id)


# --- R2: decide on the fresh row, not on a copy loaded before the lock --------------------


@pytest.mark.parametrize("call", ["release_usage", "commit_usage"])
def test_a_session_that_loaded_a_reservation_before_another_released_it_decides_on_the_fresh_row(
    client, email_outbox, monkeypatch, settings, call
):
    """As ``release_job_reservations`` does, the session reads the reservation without a
    lock first. Another session releases it in between. The call must then see it
    released: a second release returns it and writes nothing, a commit is refused."""
    scene = _scene(client, email_outbox, monkeypatch, settings, "unreferenced")
    with _app_session(scene.workspace_id) as db:
        _keep_loaded_copy(db, scene.orphan_id)
        with _app_session(scene.workspace_id) as other:
            release_usage(other, scene.orphan_id, reason=RELEASE_REASON)
            other.commit()
        before = _snapshot(scene.workspace_id)
        if call == "release_usage":
            again = release_usage(db, scene.orphan_id, reason="released twice")
            assert (again.id, again.state) == (scene.orphan_id, RELEASED)
            db.commit()
        else:
            with pytest.raises(ReservationStateError, match=f"^{RELEASED_CANNOT_BE_COMMITTED}$"):
                commit_usage(db, scene.orphan_id, actual_amount=ACTUAL_SECONDS - 3)
            db.rollback()
    assert _snapshot(scene.workspace_id) == before
    assert _released_reason(scene.orphan_id) == RELEASE_REASON
    _assert_final_once_and_counters_match(scene.workspace_id)
