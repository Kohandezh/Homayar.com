"""WP06-T02: transactional outbox and durable dispatch.

Failure boundaries exercised with a deterministic fake publisher:
rollback leaves no intent, broker down after commit, publish succeeded but the
mark failed (duplicate delivery), competing dispatchers, cancellation before
publication, mismatched payload, bounded retries. The processor is run for
real (mock providers) to show duplicate delivery does not duplicate results
or usage.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from backend.database.saas_models import (
    DispatchState,
    JobDispatch,
    JobState,
    Meeting,
    ProcessingJob,
    TranscriptSegment,
    UsageEvent,
)
from backend.jobs import outbox
from backend.jobs.processor import process_job
from backend.jobs.tasks import process_meeting_job
from backend.security.context import CSRF_COOKIE
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_job_processor import _providers, _queued_upload


class FakeBroker:
    """Records publications; can be told to fail N times or to block."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str | None]] = []
        self.fail_times = 0
        self.gate: threading.Event | None = None

    def __call__(self, job_id: str, settings, *, workspace_id=None) -> str:
        if self.gate is not None:
            self.gate.wait(timeout=5)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("broker unavailable")
        self.published.append((job_id, workspace_id))
        return f"msg-{len(self.published)}"


@pytest.fixture
def broker(monkeypatch) -> FakeBroker:
    fake = FakeBroker()
    monkeypatch.setattr(outbox, "publish_processing_job", fake)
    return fake


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _dispatch_row(job_id: str) -> JobDispatch:
    with inspection_session_factory()() as db:
        row = db.scalar(select(JobDispatch).where(JobDispatch.job_id == job_id))
        assert row is not None
        db.expunge(row)
        return row


def _job_payload(client, workspace_id: str, meeting_id: str) -> dict:
    response = client.get(
        f"/api/v1/meetings/{meeting_id}/job", headers={"X-Workspace-ID": workspace_id}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _meeting_of(job_id: str) -> str:
    with inspection_session_factory()() as db:
        return db.get(ProcessingJob, job_id).meeting_id


def test_intent_commits_with_the_job_and_is_published_once(
    client, email_outbox, monkeypatch, broker, settings
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    # _queued_upload patches the request-time dispatch away, so the intent is
    # still pending: exactly what a crash right after commit leaves behind.
    row = _dispatch_row(job_id)
    assert row.state == DispatchState.PENDING and row.attempt_count == 0
    payload = _job_payload(client, workspace_id, _meeting_of(job_id))
    assert payload["dispatch"]["state"] == "pending"

    counts = outbox.sweep_dispatch_outbox(settings)
    assert counts == {"scanned": 1, "published": 1}
    assert broker.published == [(job_id, workspace_id)]
    row = _dispatch_row(job_id)
    assert row.state == DispatchState.PUBLISHED
    assert row.message_id == "msg-1" and row.published_at is not None
    # idempotent: nothing left to publish
    assert outbox.sweep_dispatch_outbox(settings) == {"scanned": 0}
    assert outbox.dispatch_job(job_id, settings).state == "published"
    assert broker.published == [(job_id, workspace_id)]
    assert _job_payload(client, workspace_id, _meeting_of(job_id))["dispatch"]["state"] == (
        "published"
    )


def test_rolled_back_upload_leaves_no_job_and_no_intent(client, email_outbox, monkeypatch, broker):
    from backend.tests.test_uploads_api import _authenticated_workspace, _upload, _valid_metadata

    _, workspace_id = _authenticated_workspace(client, email_outbox)
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=10_000),  # exceeds the demo quota
    )
    rejected = _upload(client, workspace_id=workspace_id, key="outbox-quota-exceeded-1")
    assert rejected.status_code == 429, rejected.text
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count(ProcessingJob.id))) == 0
        assert db.scalar(select(func.count(JobDispatch.id))) == 0
        assert db.scalar(select(func.count(Meeting.id))) == 0
    assert broker.published == []


def test_broker_outage_backs_off_then_recovers_and_finally_parks(
    client, email_outbox, monkeypatch, broker, settings
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    now = datetime.now(UTC)

    broker.fail_times = 1
    assert outbox.dispatch_job(job_id, settings, now=now).state == "deferred"
    row = _dispatch_row(job_id)
    assert row.state == DispatchState.PENDING and row.attempt_count == 1
    assert "ConnectionError" in row.last_error
    assert _utc(row.next_attempt_at) > now
    # not due yet: neither the sweeper nor a direct call republishes
    assert outbox.sweep_dispatch_outbox(settings, now=now) == {"scanned": 0}
    assert outbox.dispatch_job(job_id, settings, now=now).state == "not_due"
    assert broker.published == []
    # due again: published, error cleared
    later = now + timedelta(seconds=settings.dispatch_retry_base_seconds + 1)
    assert outbox.sweep_dispatch_outbox(settings, now=later) == {"scanned": 1, "published": 1}
    assert _dispatch_row(job_id).last_error is None

    # a second job whose broker never comes back parks as FAILED, visibly
    job2, workspace2 = _queued_upload(client, email_outbox, monkeypatch)  # new user + workspace
    broker.fail_times = 10_000
    clock = datetime.now(UTC)  # the new intent is due from its own creation time
    states = []
    for _ in range(settings.dispatch_max_attempts):
        outcome = outbox.dispatch_job(job2, settings, now=clock)
        states.append(outcome.state)
        clock = _utc(_dispatch_row(job2).next_attempt_at) + timedelta(seconds=1)
    assert states[:-1] == ["deferred"] * (settings.dispatch_max_attempts - 1)
    assert states[-1] == "failed"
    row = _dispatch_row(job2)
    assert row.state == DispatchState.FAILED and row.attempt_count == settings.dispatch_max_attempts
    assert outbox.dispatch_job(job2, settings, now=clock).state == "failed"
    assert outbox.sweep_dispatch_outbox(settings, now=clock) == {"scanned": 0}
    payload = _job_payload(client, workspace2, _meeting_of(job2))
    assert payload["dispatch"]["state"] == "failed"
    assert payload["dispatch"]["last_error"].startswith("ConnectionError")
    # WP06-T05: exhausted publication is its own failure class — the job is
    # parked (replayable), not left queued forever holding its reservations.
    assert payload["state"] == "dead_letter"
    assert payload["failure_code"] == "publication_failed"
    assert payload["failure_class"] == "publication"
    assert payload["attempt_count"] == 0  # it never ran


def test_publish_success_with_lost_mark_redelivers_without_duplicate_effects(
    client, email_outbox, monkeypatch, broker, settings
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)

    original_mark = outbox._mark_published
    calls = {"n": 0}

    def flaky_mark(dispatch, *, message_id, now):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database connection lost after publish")
        original_mark(dispatch, message_id=message_id, now=now)

    monkeypatch.setattr(outbox, "_mark_published", flaky_mark)
    with pytest.raises(RuntimeError):
        outbox.dispatch_job(job_id, settings)
    # the message went out but the row is still pending -> it will go out again
    assert broker.published == [(job_id, workspace_id)]
    assert _dispatch_row(job_id).state == DispatchState.PENDING
    assert outbox.dispatch_job(job_id, settings).state == "published"
    assert len(broker.published) == 2

    # Two deliveries of the same message: the second is a no-op for results
    # and usage (claim rejects running/terminal jobs; writes are idempotent).
    first = process_job(job_id, worker_id="worker-a", settings=settings, providers=_providers())
    second = process_job(job_id, worker_id="worker-b", settings=settings, providers=_providers())
    assert first["status"] == "completed"
    assert second["status"] == "completed"  # reported, not re-executed
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count(TranscriptSegment.id))) == db.scalar(
            select(func.count(TranscriptSegment.id))
        )
        segments = db.scalar(select(func.count(TranscriptSegment.id)))
        usage_events = db.scalar(
            select(func.count(UsageEvent.id)).where(UsageEvent.workspace_id == workspace_id)
        )
    third = process_job(job_id, worker_id="worker-c", settings=settings, providers=_providers())
    assert third["status"] == "completed"
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count(TranscriptSegment.id))) == segments
        assert (
            db.scalar(
                select(func.count(UsageEvent.id)).where(UsageEvent.workspace_id == workspace_id)
            )
            == usage_events
        )


def test_cancellation_withdraws_pending_publication_and_late_delivery_is_inert(
    client, email_outbox, monkeypatch, broker, settings
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    meeting_id = _meeting_of(job_id)
    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}",
        headers={"X-Workspace-ID": workspace_id, "X-CSRF-Token": client.cookies.get(CSRF_COOKIE)},
    )
    assert deleted.status_code == 204, deleted.text
    assert _dispatch_row(job_id).state == DispatchState.CANCELLED
    assert outbox.sweep_dispatch_outbox(settings) == {"scanned": 0}
    assert outbox.dispatch_job(job_id, settings).state == "cancelled"
    assert broker.published == []

    # A message that was already in flight arrives after the deletion.
    late = process_job(job_id, worker_id="late-worker", settings=settings, providers=_providers())
    assert late["status"] == "cancelled"
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        assert meeting.deleted_at is not None and meeting.status.value == "cancelled"
        assert db.get(ProcessingJob, job_id).state == JobState.CANCELLED
        assert db.scalar(select(func.count(TranscriptSegment.id))) == 0


def test_mismatched_payload_is_rejected_before_any_processing(
    client, email_outbox, monkeypatch, settings
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    assert outbox.verify_job_payload(job_id, workspace_id) is True
    assert outbox.verify_job_payload(job_id, "not-the-owner") is False
    assert outbox.verify_job_payload(job_id, None) is True
    # Call the task body directly (``apply`` would bind the Celery app to the
    # default broker and disturb the broker smoke test that runs later).
    outcome = process_meeting_job.run(job_id, workspace_id="not-the-owner")
    assert outcome == {"job_id": job_id, "status": "rejected_payload"}
    with inspection_session_factory()() as db:
        assert db.get(ProcessingJob, job_id).state == JobState.QUEUED
        assert db.get(ProcessingJob, job_id).attempt_count == 0


@pytest.mark.postgres
def test_competing_dispatchers_publish_exactly_once(
    client, email_outbox, monkeypatch, broker, settings
):
    """SKIP LOCKED: the second dispatcher sees the row as busy, never republishes."""
    job_id, _ = _queued_upload(client, email_outbox, monkeypatch)
    broker.gate = threading.Event()
    outcomes: dict[str, str] = {}

    def first():
        outcomes["first"] = outbox.dispatch_job(job_id, settings).state

    thread = threading.Thread(target=first)
    thread.start()
    # give the first dispatcher time to lock the row and block inside publish
    for _ in range(50):
        if broker.gate.is_set():
            break
        threading.Event().wait(0.05)
        if thread.is_alive():
            outcomes.setdefault("second", outbox.dispatch_job(job_id, settings).state)
            break
    broker.gate.set()
    thread.join(timeout=10)
    assert outcomes["first"] == "published"
    assert outcomes.get("second") in {"busy", "published"}
    assert len(broker.published) == 1


def test_dispatch_scope_sees_outbox_rows_only(client, email_outbox, monkeypatch, broker):
    """Service scope grants cross-tenant discovery of ids, not customer content."""
    if not is_postgres_url(configured_database_url()):
        pytest.skip("RLS scope test needs PostgreSQL")
    from sqlalchemy import text

    from backend.database.relational import get_session_factory
    from backend.database.tenant_context import SERVICE_SCOPE_DISPATCH, bind_service_scope

    job_a, _ = _queued_upload(client, email_outbox, monkeypatch)
    job_b, _ = _queued_upload(client, email_outbox, monkeypatch)
    with get_session_factory()() as db:  # application engine: homayar_app role
        assert db.scalar(select(func.count(JobDispatch.id))) == 0  # no context
        bind_service_scope(db, SERVICE_SCOPE_DISPATCH)
        assert {r for r in db.scalars(select(JobDispatch.job_id))} == {job_a, job_b}
        for table in ("meetings", "processing_jobs", "recordings", "transcript_segments"):
            assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0, table  # noqa: S608
        db.rollback()
