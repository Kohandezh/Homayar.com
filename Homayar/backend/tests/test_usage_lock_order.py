"""HOM-333: usage rows are locked in one order, so a release never deadlocks the reconciliation.

The order is "Lock order (HOM-333)" in ``backend/services/entitlement_service.py``:
the entitlement row first, then the reservation rows, and every decision is made on
the rows as they are under the locks.

The two-session tests run real PostgreSQL sessions in threads, as
``test_media_lock_order.py`` does. One session stops while it holds its locks; the
other is then seen waiting on a lock that the first one holds (``pg_blocking_pids``).
Then the first session goes on. Neither may be aborted as a deadlock (SQLSTATE
``40P01``), the reservation is final exactly once, and the counters match the
reservation rows. Steps are ordered with events and lock waits, never with a clock.

The fresh-row tests run one step after the other, so they run on SQLite too: a
session loads a reservation, the reconciliation then releases it, and the first
session's release or settlement must see the released row, not its old copy.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    JobState,
    ProcessingJob,
    ReservationState,
    UsageEvent,
    UsageReservation,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs import usage_reconciliation
from backend.jobs.failures import release_job_reservations
from backend.jobs.processor import job_reservation_amount
from backend.jobs.usage_reconciliation import (
    ORPHAN_REASON,
    EntitlementRef,
    ReconciliationOutcome,
    reconcile_entitlement,
)
from backend.services.entitlement_service import (
    ReservationStateError,
    commit_usage,
    release_usage,
)
from backend.tests._database import inspection_session_factory
from backend.tests.test_job_leases import _job
from backend.tests.test_job_processor import _queued_upload
from backend.tests.test_media_lock_order import (
    DEADLINE,
    _blocked,
    _pause,
    _pid,
    _settled,
    _Worker,
)
from backend.tests.test_usage_ledger import (
    _age_reservations,
    _entitlement,
    _events,
    expected_counters_match,
)

RELEASE_REASON = "job_cancelled"
# The one error a losing settlement may end with (AC4): the reconciliation released it first.
LOST_SETTLEMENT = "Released reservation cannot be committed"
METRICS = ("audio_seconds", "concurrent_jobs")


# --- helpers ----------------------------------------------------------------------------


def _parked_upload(client, email_outbox, monkeypatch, settings) -> tuple[str, str]:
    """An uploaded job parked without releasing (a legacy row), its reservations aged.

    No queued or running job references the reservations and they are older than the
    grace period, so the reconciliation sees both as orphans.
    """
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        job.state = JobState.DEAD_LETTER
        job.stage = "dead_letter"
        db.commit()
    _age_reservations(workspace_id, seconds=settings.usage_reconciliation_grace_seconds * 2)
    return job_id, workspace_id


def _ref(workspace_id: str, metric: str = "audio_seconds") -> EntitlementRef:
    return EntitlementRef(_entitlement(workspace_id, metric).id, workspace_id, metric)


def _reservation(reservation_id: str) -> UsageReservation:
    with inspection_session_factory()() as db:
        reservation = db.get(UsageReservation, reservation_id)
        assert reservation is not None
        db.expunge(reservation)
        return reservation


def _final_events(workspace_id: str, reservation_id: str) -> list[UsageEvent]:
    """The ``committed`` and ``released`` ledger rows of one reservation."""
    return [
        event
        for event in _events(workspace_id)
        if event.reservation_id == reservation_id and event.event_type != "reserved"
    ]


def _counters(workspace_id: str) -> dict[str, tuple[int, int]]:
    """(used, reserved) of each entitlement of the workspace."""
    rows = {metric: _entitlement(workspace_id, metric) for metric in METRICS}
    return {metric: (row.used_value, row.reserved_value) for metric, row in rows.items()}


def _hold() -> SimpleNamespace:
    return SimpleNamespace(entered=threading.Event(), gate=threading.Event(), pid=None)


def _in_session(
    workspace_id: str, action: Callable[[Session], Any], hold: SimpleNamespace | None = None
) -> Callable[[], Any]:
    """``action`` in its own tenant session, then a commit.

    With ``hold``, the session stops after ``action``, its locks held and nothing
    committed, until ``hold.gate`` is set; it keeps its backend pid in ``hold.pid``.
    """

    def work() -> Any:
        with get_session_factory()() as db:
            bind_tenant_context(db, workspace_id=workspace_id)
            result = action(db)
            if hold is not None:
                hold.pid = _pid(db)
                hold.entered.set()
                assert hold.gate.wait(DEADLINE)
            db.commit()
            return result

    return work


def _release_action(path: str, job_id: str, reservation_id: str) -> Callable[[Session], Any]:
    if path == "release_usage":
        return lambda db: release_usage(db, reservation_id, reason=RELEASE_REASON).state
    # The path park_job, cancel_job and DELETE /meetings use. It loads each
    # reservation with db.get (no lock) before it releases it.
    return lambda db: release_job_reservations(
        db, db.get(ProcessingJob, job_id), reason=RELEASE_REASON
    )


def _settle(reservation_id: str) -> Callable[[Session], str]:
    """The job completion's settlement: the amount is read (no lock) before commit_usage."""

    def action(db: Session) -> str:
        amount = job_reservation_amount(db, reservation_id)
        try:
            commit_usage(db, reservation_id, actual_amount=amount)
        except ReservationStateError as exc:
            return str(exc)
        return "committed"

    return action


def _two_sessions(
    monkeypatch,
    settings,
    workspace_id: str,
    action: Callable[[Session], Any],
    *,
    first: str,
    stop_at: str = "release_usage",
    after: bool = False,
) -> tuple[ReconciliationOutcome, Any]:
    """The reconciliation of the audio entitlement and ``action`` in two sessions.

    ``first == "reconciliation"``: the reconciliation stops at its first call of
    ``stop_at`` (by default before it releases its first orphan), the entitlement row
    locked, and the other session must wait on it. Otherwise the other session stops
    after ``action``, before its commit, and the reconciliation must wait on it.

    The paused session is let go and every started session is joined also when a
    step fails, so no session keeps its row locks into the teardown.
    """
    ref = _ref(workspace_id)
    reconciliation = _Worker(lambda: reconcile_entitlement(ref, settings))
    if first == "reconciliation":
        pause = _pause(monkeypatch, usage_reconciliation, stop_at, after=after)
        other = _Worker(_in_session(workspace_id, action))
    else:
        pause = _hold()
        other = _Worker(_in_session(workspace_id, action, hold=pause))
    started: list[_Worker] = []
    try:
        if first == "reconciliation":
            reconciliation.start()
            started.append(reconciliation)
            assert pause.entered.wait(DEADLINE)
            other.start()
            started.append(other)
            _blocked(other, pause.pid)
        else:
            other.start()
            started.append(other)
            assert pause.entered.wait(DEADLINE)
            reconciliation.start()
            started.append(reconciliation)
            _blocked(reconciliation, pause.pid)
    finally:
        pause.gate.set()
        for worker in started:
            worker.join(DEADLINE)
    outcome, result = _settled(reconciliation, other)
    return outcome, result


def _assert_counters_clean(settings, workspace_id: str, outcome: ReconciliationOutcome) -> None:
    """The counters match the reservation rows, and the reconciliation reports no drift."""
    assert outcome.drift is False, (outcome.observed, outcome.expected)
    assert expected_counters_match(workspace_id)
    again = reconcile_entitlement(_ref(workspace_id), settings)
    assert again.state == "clean", again


# --- AC1 to AC3, AC5: a release and the reconciliation of the same orphan ----------------


@pytest.mark.postgres
@pytest.mark.parametrize("first", ["reconciliation", "release"])
@pytest.mark.parametrize("path", ["release_usage", "release_job_reservations"])
def test_a_release_and_the_reconciliation_of_the_same_orphan_finish_without_a_deadlock(
    client, email_outbox, monkeypatch, settings, path, first
):
    """``reconciliation`` first deadlocked before HOM-333: the reconciliation held the
    entitlement row and waited for the reservation row, the release held the
    reservation row and waited for the entitlement row.
    """
    job_id, workspace_id = _parked_upload(client, email_outbox, monkeypatch, settings)
    job = _job(job_id)
    audio, concurrency = job.reservation_id, job.concurrency_reservation_id

    outcome, _ = _two_sessions(
        monkeypatch, settings, workspace_id, _release_action(path, job_id, audio), first=first
    )

    assert _reservation(audio).state is ReservationState.RELEASED
    released = _final_events(workspace_id, audio)
    assert [event.event_type for event in released] == ["released"]
    if first == "reconciliation":
        # It held the entitlement row first, so it released the orphan; the release
        # then found the row released and wrote nothing.
        assert outcome.orphans_released == [audio]
        assert released[0].reason == ORPHAN_REASON
    else:
        # The release committed before the reconciliation read its orphans.
        assert outcome.orphans_released == []
        assert released[0].reason == RELEASE_REASON
    if path == "release_job_reservations":
        # The job's reservation on the other entitlement: released once, by the job path.
        assert _reservation(concurrency).state is ReservationState.RELEASED
        assert [
            (event.event_type, event.reason) for event in _final_events(workspace_id, concurrency)
        ] == [("released", RELEASE_REASON)]
    _assert_counters_clean(settings, workspace_id, outcome)


# --- AC4, AC5: a settlement against the reconciliation ------------------------------------


@pytest.mark.postgres
@pytest.mark.parametrize("first", ["reconciliation", "settlement"])
def test_a_settlement_and_the_reconciliation_of_the_same_orphan_finish_without_a_deadlock(
    client, email_outbox, monkeypatch, settings, first
):
    """``reconciliation`` first deadlocked before HOM-333, as the release did."""
    job_id, workspace_id = _parked_upload(client, email_outbox, monkeypatch, settings)
    audio = _job(job_id).reservation_id
    amount = _reservation(audio).reserved_value

    outcome, settled = _two_sessions(
        monkeypatch, settings, workspace_id, _settle(audio), first=first
    )

    # Either may win; the reservation is final once and every report agrees with it.
    reservation = _reservation(audio)
    final = _final_events(workspace_id, audio)
    if reservation.state is ReservationState.RELEASED:
        assert settled == LOST_SETTLEMENT
        assert outcome.orphans_released == [audio]
        assert [(event.event_type, event.reason) for event in final] == [
            ("released", ORPHAN_REASON)
        ]
    else:
        assert reservation.state is ReservationState.COMMITTED
        assert settled == "committed"
        assert outcome.orphans_released == []
        assert reservation.committed_value == amount
        assert [(event.event_type, event.value) for event in final] == [("committed", amount)]
    _assert_counters_clean(settings, workspace_id, outcome)


# --- AC6: allow-controls ------------------------------------------------------------------


def test_the_reconciliation_alone_still_releases_an_orphan(
    client, email_outbox, monkeypatch, settings
):
    job_id, workspace_id = _parked_upload(client, email_outbox, monkeypatch, settings)
    audio = _job(job_id).reservation_id

    outcome = reconcile_entitlement(_ref(workspace_id), settings)

    assert outcome.orphans_released == [audio]
    assert outcome.state == "orphans"
    assert _reservation(audio).state is ReservationState.RELEASED
    assert [(e.event_type, e.reason) for e in _final_events(workspace_id, audio)] == [
        ("released", ORPHAN_REASON)
    ]
    _assert_counters_clean(settings, workspace_id, outcome)


@pytest.mark.postgres
@pytest.mark.parametrize("first", ["reconciliation", "release"])
def test_the_reconciliation_skips_a_live_reservation_that_a_release_releases(
    client, email_outbox, monkeypatch, settings, first
):
    """The job is queued, so its reservation is live: only the release releases it.

    With nothing to release, the reconciliation stops after it read the live
    reservations, the entitlement row locked.
    """
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    _age_reservations(workspace_id, seconds=settings.usage_reconciliation_grace_seconds * 2)
    audio = _job(job_id).reservation_id

    outcome, _ = _two_sessions(
        monkeypatch,
        settings,
        workspace_id,
        _release_action("release_usage", job_id, audio),
        first=first,
        stop_at="_live_reservation_ids",
        after=True,
    )

    assert outcome.orphans_released == []
    assert _reservation(audio).state is ReservationState.RELEASED
    assert [(e.event_type, e.reason) for e in _final_events(workspace_id, audio)] == [
        ("released", RELEASE_REASON)
    ]
    _assert_counters_clean(settings, workspace_id, outcome)


def test_release_and_commit_stay_idempotent_and_keep_their_errors(
    client, email_outbox, monkeypatch, settings
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    job = _job(job_id)
    audio, concurrency = job.reservation_id, job.concurrency_reservation_id
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=workspace_id)
        commit_usage(db, audio)
        release_usage(db, concurrency, reason="job_completed")
        db.commit()
    events = [(e.reservation_id, e.event_type) for e in _events(workspace_id)]
    counters = _counters(workspace_id)

    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=workspace_id)
        # A second call on a final reservation returns it and writes nothing.
        assert commit_usage(db, audio).state is ReservationState.COMMITTED
        again = release_usage(db, concurrency, reason="job_completed_again")
        assert again.state is ReservationState.RELEASED
        assert again.failure_reason == "job_completed"
        db.commit()
        # The other final state is refused, as before.
        with pytest.raises(ReservationStateError, match="Committed reservation cannot be released"):
            release_usage(db, audio, reason="too_late")
        db.rollback()
        with pytest.raises(ReservationStateError, match=LOST_SETTLEMENT):
            commit_usage(db, concurrency)
        db.rollback()
        with pytest.raises(ReservationStateError, match="Reservation not found"):
            release_usage(db, "no-such-reservation", reason="x")
        db.rollback()
        with pytest.raises(ReservationStateError, match="Reservation not found"):
            commit_usage(db, "no-such-reservation")
        db.rollback()

    assert [(e.reservation_id, e.event_type) for e in _events(workspace_id)] == events
    assert _counters(workspace_id) == counters


# --- R2: decide on the fresh row, not on a copy loaded before the locks -------------------


@pytest.mark.parametrize("path", ["release_usage", "release_job_reservations", "commit_usage"])
def test_a_release_or_settlement_decides_on_the_fresh_row_not_on_an_old_copy(
    client, email_outbox, monkeypatch, settings, path
):
    """The session loads the reservation (RESERVED), the reconciliation then releases it.

    Before HOM-333 the locked read gave back the session's old RESERVED copy, so the
    release or the settlement ran again on a released reservation and failed on the
    counters ("Entitlement counters are inconsistent").
    """
    job_id, workspace_id = _parked_upload(client, email_outbox, monkeypatch, settings)
    job = _job(job_id)
    audio, concurrency = job.reservation_id, job.concurrency_reservation_id

    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=workspace_id)
        loaded = db.get(UsageReservation, audio)
        assert loaded is not None and loaded.state is ReservationState.RESERVED
        outcome = reconcile_entitlement(_ref(workspace_id), settings)
        assert outcome.orphans_released == [audio]
        if path == "commit_usage":
            assert _settle(audio)(db) == LOST_SETTLEMENT
        else:
            _release_action(path, job_id, audio)(db)
        db.commit()

    assert [(e.event_type, e.reason) for e in _final_events(workspace_id, audio)] == [
        ("released", ORPHAN_REASON)
    ]
    if path == "release_job_reservations":
        assert [(e.event_type, e.reason) for e in _final_events(workspace_id, concurrency)] == [
            ("released", RELEASE_REASON)
        ]
    assert expected_counters_match(workspace_id)
