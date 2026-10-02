"""WP06-T06: idempotent usage ledger, settlement invariants and reconciliation.

Processing is delivered at least once; a customer's allowance must move
exactly once per business operation. The ledger records every reservation
transition under a stable operation identity, the database refuses a second
settlement of the same work, and reconciliation releases orphaned
reservations and reports (or, only on request, repairs) counter drift.
Locking and constraint behaviour is evidence on PostgreSQL; the SQLite
fallback runs the same sequential cases for shape.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from backend.config import REVEAL_SECRETS_CONTEXT, Settings
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    AuditEvent,
    Entitlement,
    JobState,
    ProcessingJob,
    ReservationState,
    UsageEvent,
    UsageReservation,
)
from backend.database.tenant_context import (
    SERVICE_SCOPE_USAGE_RECONCILIATION,
    bind_service_scope,
    bind_tenant_context,
)
from backend.jobs import outbox
from backend.jobs.processor import process_job
from backend.jobs.usage_reconciliation import (
    COUNTER_REASON,
    ORPHAN_REASON,
    EntitlementRef,
    expected_counters,
    reconcile_entitlement,
    reconcile_usage,
)
from backend.routers.v1.billing import router as billing_router
from backend.services.entitlement_service import (
    ReservationStateError,
    commit_usage,
    operation_key_for,
    release_usage,
    reserve_usage,
)
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_admin_api import _session_cookies
from backend.tests.test_dispatch_outbox import FakeBroker, _meeting_of
from backend.tests.test_job_failure_policy import (
    _dead_letter_after_one_attempt,
    _replay,
    _reservations,
)
from backend.tests.test_job_leases import _job
from backend.tests.test_job_processor import _providers, _queued_upload

DEADLINE_SECONDS = 15.0


@pytest.fixture
def broker(monkeypatch) -> FakeBroker:
    fake = FakeBroker()
    monkeypatch.setattr(outbox, "publish_processing_job", fake)
    return fake


# --- helpers -----------------------------------------------------------------


def _events(workspace_id: str) -> list[UsageEvent]:
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(UsageEvent)
            .where(UsageEvent.workspace_id == workspace_id)
            .order_by(UsageEvent.occurred_at.asc(), UsageEvent.id.asc())
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def _entitlement(workspace_id: str, metric: str = "audio_seconds") -> Entitlement:
    with inspection_session_factory()() as db:
        row = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id, Entitlement.metric == metric
            )
        )
        assert row is not None
        db.expunge(row)
        return row


def _audit(workspace_id: str, event_type: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.workspace_id == workspace_id, AuditEvent.event_type == event_type)
            .order_by(AuditEvent.occurred_at.asc())
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def _age_reservations(workspace_id: str, *, seconds: int) -> None:
    """Make every reservation of the workspace look older than the grace period."""
    with inspection_session_factory()() as db:
        for row in db.scalars(
            select(UsageReservation).where(UsageReservation.workspace_id == workspace_id)
        ):
            row.created_at = datetime.now(UTC) - timedelta(seconds=seconds)
        db.commit()


def _refs(workspace_id: str) -> list[EntitlementRef]:
    with inspection_session_factory()() as db:
        rows = db.execute(
            select(Entitlement.id, Entitlement.metric).where(
                Entitlement.workspace_id == workspace_id
            )
        ).all()
    return [EntitlementRef(r[0], workspace_id, r[1]) for r in rows]


# --- ledger -------------------------------------------------------------------


def test_ledger_records_each_transition_once_with_stable_keys(
    client, email_outbox, monkeypatch, settings, broker
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    job = _job(job_id)
    audio, concurrency = job.reservation_id, job.concurrency_reservation_id
    assert audio and concurrency

    # the upload reserved twice (audio seconds, one job slot): two ledger rows
    events = _events(workspace_id)
    assert sorted((e.event_type, e.reservation_id) for e in events) == sorted(
        [("reserved", audio), ("reserved", concurrency)]
    )
    assert {e.operation_key for e in events} == {
        operation_key_for(audio, "reserved"),
        operation_key_for(concurrency, "reserved"),
    }

    meeting_id = _meeting_of(job_id)
    with inspection_session_factory()() as db:
        # the same business operation retried: same reservation, no new row
        again = reserve_usage(
            db,
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            amount=13,
            idempotency_key=f"{job.idempotency_key}:audio",
            metric="audio_seconds",
        )
        db.commit()
        assert again.id == audio
        # settlement twice: one committed row; a release afterwards is refused
        commit_usage(db, audio, actual_amount=13)
        db.commit()
        commit_usage(db, audio, actual_amount=13)
        db.commit()
        with pytest.raises(ReservationStateError):
            release_usage(db, audio, reason="too_late")
        db.rollback()
        # release twice: one released row carrying the reason
        release_usage(db, concurrency, reason="job_completed")
        db.commit()
        release_usage(db, concurrency, reason="job_completed_again")
        db.commit()

    events = _events(workspace_id)
    assert sorted((e.reservation_id, e.event_type) for e in events) == sorted(
        [
            (audio, "reserved"),
            (audio, "committed"),
            (concurrency, "reserved"),
            (concurrency, "released"),
        ]
    )
    by_key = {e.operation_key: e for e in events}
    assert by_key[operation_key_for(audio, "committed")].value == 13
    released = by_key[operation_key_for(concurrency, "released")]
    assert released.value == 1 and released.reason == "job_completed"
    entitlement = _entitlement(workspace_id)
    assert (entitlement.used_value, entitlement.reserved_value) == (13, 0)
    assert expected_counters_match(workspace_id)

    # the ledger is visible to the workspace through the billing API
    client.app.include_router(billing_router, prefix="/api/v1")
    response = client.get(
        "/api/v1/billing/usage/events?metric=audio_seconds",
        cookies=_session_cookies(client),
        headers={"X-Workspace-ID": workspace_id},
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert sorted(item["event_type"] for item in items) == ["committed", "reserved"]
    assert {item["operation_key"] for item in items} == {
        operation_key_for(audio, "reserved"),
        operation_key_for(audio, "committed"),
    }


def expected_counters_match(workspace_id: str) -> bool:
    with inspection_session_factory()() as db:
        for entitlement in db.scalars(
            select(Entitlement).where(Entitlement.workspace_id == workspace_id)
        ):
            if expected_counters(db, entitlement.id) != (
                entitlement.used_value,
                entitlement.reserved_value,
            ):
                return False
    return True


def test_database_refuses_a_second_settlement_of_the_same_work(
    client, email_outbox, monkeypatch, settings, broker
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    assert process_job(job_id, worker_id="w", settings=settings, providers=_providers())[
        "status"
    ] in {"completed", "partial"}
    job = _job(job_id)
    audio = job.reservation_id
    entitlement = _entitlement(workspace_id)

    with inspection_session_factory()() as db:
        # a second "committed" ledger row for the reservation: refused, even
        # under a fresh operation key
        for key in (operation_key_for(audio, "committed"), f"forged:{audio}"):
            with pytest.raises(IntegrityError):
                with db.begin_nested():
                    db.add(
                        UsageEvent(
                            workspace_id=workspace_id,
                            reservation_id=audio,
                            operation_key=key,
                            metric="audio_seconds",
                            value=13,
                            event_type="committed",
                            subject_type="meeting",
                            subject_id=job.meeting_id,
                        )
                    )
                    db.flush()
        # a second COMMITTED reservation for the same meeting and metric: refused
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(
                    UsageReservation(
                        workspace_id=workspace_id,
                        entitlement_id=entitlement.id,
                        meeting_id=job.meeting_id,
                        idempotency_key="forged:second-settlement",
                        reserved_value=13,
                        committed_value=13,
                        state=ReservationState.COMMITTED,
                    )
                )
                db.flush()
        # a released one is not a settlement and is allowed
        with db.begin_nested():
            db.add(
                UsageReservation(
                    workspace_id=workspace_id,
                    entitlement_id=entitlement.id,
                    meeting_id=job.meeting_id,
                    idempotency_key="forged:released",
                    reserved_value=13,
                    state=ReservationState.RELEASED,
                )
            )
            db.flush()
        db.rollback()

    committed = [e for e in _events(workspace_id) if e.event_type == "committed"]
    assert len(committed) == 1 and committed[0].reservation_id == audio
    assert _entitlement(workspace_id).used_value == 13


def test_redelivery_replay_and_recovery_charge_once(
    client, email_outbox, monkeypatch, settings, broker
):
    job_id, workspace_id = _dead_letter_after_one_attempt(
        client, email_outbox, monkeypatch, settings
    )
    parked = _job(job_id)
    first_audio = parked.reservation_id
    assert _reservations(job_id)[first_audio] == "released"
    assert _replay(client, workspace_id, job_id).status_code == 202
    assert process_job(job_id, worker_id="w2", settings=settings, providers=_providers())[
        "status"
    ] in {"completed", "partial"}
    job = _job(job_id)
    assert job.state == JobState.COMPLETED and job.reservation_id != first_audio
    # a late duplicate delivery of the completed job changes nothing
    assert process_job(job_id, worker_id="late", settings=settings, providers=_providers()) == {
        "job_id": job_id,
        "status": "completed",
    }

    events = _events(workspace_id)
    by_reservation: dict[str, list[str]] = {}
    for event in events:
        by_reservation.setdefault(event.reservation_id, []).append(event.event_type)
    assert by_reservation[first_audio] == ["reserved", "released"]
    assert by_reservation[job.reservation_id] == ["reserved", "committed"]
    assert sum(1 for e in events if e.event_type == "committed") == 1
    entitlement = _entitlement(workspace_id)
    assert (entitlement.used_value, entitlement.reserved_value) == (13, 0)
    assert expected_counters_match(workspace_id)
    assert reconcile_usage(settings) == {"scanned": 2, "orphans_released": 0, "clean": 2}


@pytest.mark.postgres
def test_concurrent_settlement_of_one_reservation_commits_once(
    client, email_outbox, monkeypatch, settings, broker
):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("row locking is PostgreSQL evidence")
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    audio = _job(job_id).reservation_id
    barrier = threading.Barrier(2, timeout=DEADLINE_SECONDS)
    results: list[str] = []
    errors: list[str] = []
    factory = get_session_factory()

    def settle(name: str) -> None:
        try:
            with factory() as db:
                bind_tenant_context(db, workspace_id=workspace_id)
                barrier.wait()
                reservation = commit_usage(db, audio, actual_amount=13)
                db.commit()
                results.append(f"{name}:{reservation.state.value}")
        except Exception as exc:  # noqa: BLE001 - surfaced through the assertion below
            errors.append(f"{name}:{type(exc).__name__}")

    threads = [threading.Thread(target=settle, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=DEADLINE_SECONDS)
    assert errors == []
    assert sorted(results) == ["a:committed", "b:committed"]
    committed = [e for e in _events(workspace_id) if e.event_type == "committed"]
    assert len(committed) == 1
    assert (_entitlement(workspace_id).used_value, _entitlement(workspace_id).reserved_value) == (
        13,
        0,
    )


# --- reconciliation -----------------------------------------------------------


def test_reconciliation_releases_orphans_and_reports_or_repairs_drift(
    client, email_outbox, monkeypatch, settings, broker
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    meeting_id = _meeting_of(job_id)
    # healthy: nothing to do, nothing audited
    assert reconcile_usage(settings) == {"scanned": 2, "orphans_released": 0, "clean": 2}
    assert _audit(workspace_id, "usage.reconciled") == []

    # an orphaned reservation (no job references it) older than the grace
    # period is released; the live job's reservations of the same age are not
    with inspection_session_factory()() as db:
        orphan = reserve_usage(
            db,
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            amount=100,
            idempotency_key="orphan:1",
            metric="audio_seconds",
        )
        db.commit()
        orphan_id = orphan.id
    assert _entitlement(workspace_id).reserved_value == 13 + 100
    assert reconcile_usage(settings)["clean"] == 2  # too young to be judged
    _age_reservations(workspace_id, seconds=settings.usage_reconciliation_grace_seconds * 2)
    counts = reconcile_usage(settings)
    assert counts == {"scanned": 2, "orphans_released": 1, "orphans": 1, "clean": 1}
    live = _job(job_id)
    reservations = _reservations(job_id)
    assert reservations[orphan_id] == "released"
    assert reservations[live.reservation_id] == "reserved"
    assert reservations[live.concurrency_reservation_id] == "reserved"
    released = [e for e in _events(workspace_id) if e.reservation_id == orphan_id]
    assert [e.event_type for e in released] == ["reserved", "released"]
    assert released[-1].reason == ORPHAN_REASON
    assert _entitlement(workspace_id).reserved_value == 13
    audited = _audit(workspace_id, "usage.reconciled")
    assert len(audited) == 1 and audited[0].outcome == "success"
    assert audited[0].details["orphans_released"] == 1 and audited[0].details["drift"] is False

    # counter drift: reported (and audited as a failure), not silently rewritten
    with inspection_session_factory()() as db:
        entitlement = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id, Entitlement.metric == "audio_seconds"
            )
        )
        entitlement.used_value += 7
        db.commit()
    counts = reconcile_usage(settings)
    assert counts["drift"] == 1 and counts.get("repaired") is None
    assert _entitlement(workspace_id).used_value == 7
    audited = _audit(workspace_id, "usage.reconciled")
    assert audited[-1].outcome == "failure" and audited[-1].details["drift"] is True
    assert audited[-1].details["observed_used"] == 7 and audited[-1].details["expected_used"] == 0
    assert [e.event_type for e in _events(workspace_id) if e.event_type == "adjusted"] == []

    # explicit repair: one idempotent adjustment, counters match the ledger
    counts = reconcile_usage(settings, repair_counters=True)
    assert counts["repaired"] == 1
    entitlement = _entitlement(workspace_id)
    assert (entitlement.used_value, entitlement.reserved_value) == (0, 13)
    adjusted = [e for e in _events(workspace_id) if e.event_type == "adjusted"]
    assert len(adjusted) == 1
    assert adjusted[0].value == 7 and adjusted[0].reason == COUNTER_REASON
    assert adjusted[0].operation_key == f"reconcile:{entitlement.id}:7/13->0/13"
    assert adjusted[0].provider_metadata["used_delta"] == -7
    assert adjusted[0].reservation_id is None and adjusted[0].subject_id == entitlement.id
    assert _audit(workspace_id, "usage.reconciled")[-1].details["repaired"] is True
    # idempotent: a further run finds nothing and writes nothing
    assert reconcile_usage(settings, repair_counters=True) == {
        "scanned": 2,
        "orphans_released": 0,
        "clean": 2,
    }
    assert len([e for e in _events(workspace_id) if e.event_type == "adjusted"]) == 1
    assert len(_audit(workspace_id, "usage.reconciled")) == 3
    assert expected_counters_match(workspace_id)


def test_reconciliation_leaves_a_deleted_meetings_reservation_released_and_is_scoped(
    client, email_outbox, monkeypatch, settings, broker
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    other_job, other_workspace = _queued_upload(client, email_outbox, monkeypatch)
    # a reservation whose meeting was deleted can never settle: it is released
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        job.state = JobState.DEAD_LETTER  # legacy row: parked without releasing
        job.stage = "dead_letter"
        db.commit()
    _age_reservations(workspace_id, seconds=settings.usage_reconciliation_grace_seconds * 2)
    _age_reservations(other_workspace, seconds=settings.usage_reconciliation_grace_seconds * 2)
    outcomes = [reconcile_entitlement(ref, settings) for ref in _refs(workspace_id)]
    assert sorted(outcome.state for outcome in outcomes) == ["orphans", "orphans"]
    assert set(_reservations(job_id).values()) == {"released"}
    # the other workspace's live job kept its reservations
    assert set(_reservations(other_job).values()) == {"reserved"}
    for workspace in (workspace_id, other_workspace):
        assert expected_counters_match(workspace)


@pytest.mark.postgres
def test_reconciliation_scope_reads_entitlements_only_and_cannot_write(
    client, email_outbox, monkeypatch, settings, broker
):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("RLS scope test needs PostgreSQL")
    _queued_upload(client, email_outbox, monkeypatch)
    _queued_upload(client, email_outbox, monkeypatch)
    factory = get_session_factory()  # application engine: homayar_app role
    with factory() as db:
        assert db.scalar(select(func.count(Entitlement.id))) == 0  # no context
        bind_service_scope(db, SERVICE_SCOPE_USAGE_RECONCILIATION)
        assert db.scalar(select(func.count(Entitlement.id))) == 4
        for table in (
            "meetings",
            "recordings",
            "transcript_segments",
            "usage_reservations",
            "usage_events",
            "processing_jobs",
            "audit_events",
        ):
            count = db.execute(
                text(f"SELECT count(*) FROM {table} WHERE workspace_id IS NOT NULL")  # noqa: S608
            ).scalar_one()
            assert count == 0, table
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE entitlements SET used_value = used_value + 1"))
            db.flush()
        db.rollback()
    with factory() as db:
        assert db.scalar(select(func.count(Entitlement.id))) == 0
        assert db.execute(
            text("SELECT current_setting('homayar.service_scope', true)")
        ).scalar() in (None, "")
        db.rollback()


def test_reconciliation_settings_are_validated(settings):
    for overrides in (
        {"usage_reconciliation_interval_seconds": 0},
        {"usage_reconciliation_grace_seconds": 0},
        {"usage_reconciliation_batch_size": 0},
    ):
        with pytest.raises(ValueError):
            Settings(
                **{
                    **settings.model_dump(context={REVEAL_SECRETS_CONTEXT: True}),
                    **overrides,
                    "_env_file": None,
                }
            )
