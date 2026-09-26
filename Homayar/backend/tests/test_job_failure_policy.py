"""WP06-T05: failure classes, bounded retry budgets, deadlines, dead-letter
parking and authorized operator replay.

Publication failure, retryable execution failure, permanent failure,
abandonment, deadline and cancellation each reach one documented terminal
representation; only ``dead_letter`` is replayable, and replay is
authorized, audited, bounded, quota-checked and fenced.  Locking and
transactional guarantees are evidence on PostgreSQL only; the SQLite
fallback runs the same sequential cases for shape.  Deadlines and back-off
are exercised with DBA writes and fake clocks, never sleeps.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.config import Settings
from backend.database.saas_models import (
    AuditEvent,
    DispatchState,
    Entitlement,
    JobState,
    Meeting,
    MeetingStatus,
    ProcessingJob,
    Recording,
    TranscriptSegment,
    UsageEvent,
    UsageReservation,
    WorkspaceRole,
)
from backend.jobs import outbox, processor, recovery
from backend.jobs.failures import RetryPolicy
from backend.jobs.leases import StaleLeaseError
from backend.jobs.processor import ProcessingError, process_job
from backend.providers.base import ASRProvider, ProviderError
from backend.providers.schemas import ASRRequest, ASRResult
from backend.security.context import CSRF_COOKIE
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_admin_api import (
    _add_membership,
    _session_cookies,
    _user_and_workspace,
)
from backend.tests.test_dispatch_outbox import FakeBroker, _dispatch_row, _meeting_of, _utc
from backend.tests.test_job_leases import _claim, _expire_lease, _job, _persist
from backend.tests.test_job_processor import (
    PermanentFailureASRProvider,
    _providers,
    _queued_upload,
)


@pytest.fixture
def broker(monkeypatch) -> FakeBroker:
    fake = FakeBroker()
    monkeypatch.setattr(outbox, "publish_processing_job", fake)
    return fake


class AlwaysTransientASRProvider(ASRProvider):
    """A supplier that is never back: every call is a retryable failure."""

    def __init__(self) -> None:
        self.calls = 0

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        self.calls += 1
        raise ProviderError(
            "asr_unavailable", "Speech recognition is temporarily unavailable", retryable=True
        )


# --- helpers -----------------------------------------------------------------


def _edit_job(job_id: str, **changes) -> None:
    """DBA write on the job row (simulates time passing or legacy rows)."""
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job is not None
        for key, value in changes.items():
            setattr(job, key, value)
        db.commit()


def _make_due(job_id: str) -> None:
    """Bring a scheduled retry forward instead of waiting for the back-off."""
    past = datetime.now(UTC) - timedelta(seconds=1)
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job is not None and job.state == JobState.QUEUED
        job.retry_after = past
        if job.dispatch is not None:
            job.dispatch.next_attempt_at = past
        db.commit()


def _reservations(job_id: str) -> dict[str, str]:
    """reservation id -> state for every reservation the job's meeting ever took."""
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job is not None
        rows = db.scalars(
            select(UsageReservation).where(UsageReservation.meeting_id == job.meeting_id)
        ).all()
        return {row.id: row.state.value for row in rows}


def _meeting(job_id: str) -> Meeting:
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job is not None
        meeting = db.get(Meeting, job.meeting_id)
        assert meeting is not None
        db.expunge(meeting)
        return meeting


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


def _committed_usage(workspace_id: str) -> list[UsageEvent]:
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(UsageEvent).where(
                UsageEvent.workspace_id == workspace_id, UsageEvent.event_type == "committed"
            )
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def _replay(client, workspace_id: str, job_id: str, *, cookies: dict | None = None):
    cookies = cookies or _session_cookies(client)
    return client.post(
        f"/api/v1/admin/jobs/{job_id}/replay",
        cookies=cookies,
        headers={"X-Workspace-ID": workspace_id, "X-CSRF-Token": cookies[CSRF_COOKIE]},
    )


def _error(response) -> str:
    return response.json()["error"]["code"]


def _dead_letter_after_one_attempt(
    client, email_outbox, monkeypatch, settings: Settings
) -> tuple[str, str]:
    """A parked job the fastest honest way: a one-attempt budget and a supplier
    that never recovers."""
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    _edit_job(job_id, max_attempts=1)
    result = process_job(
        job_id,
        worker_id="worker-first",
        settings=settings,
        providers=_providers(asr=AlwaysTransientASRProvider()),
    )
    assert result["status"] == "failed"
    job = _job(job_id)
    assert job.state == JobState.DEAD_LETTER and job.failure_class == "transient"
    return job_id, workspace_id


# --- retry budget, back-off, deadlines ---------------------------------------


def test_transient_failures_back_off_and_exhaust_into_dead_letter(
    client, email_outbox, monkeypatch, settings, broker
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    policy = RetryPolicy.from_settings(settings)
    provider = AlwaysTransientASRProvider()
    providers = _providers(asr=provider)

    for attempt in (1, 2):
        before = datetime.now(UTC)
        result = process_job(
            job_id, worker_id=f"worker-{attempt}", settings=settings, providers=providers
        )
        assert result == {"job_id": job_id, "status": "retry_scheduled", "code": "asr_unavailable"}
        job = _job(job_id)
        assert job.state == JobState.QUEUED and job.stage == "retry_scheduled"
        assert job.attempt_count == attempt and job.failure_class == "transient"
        assert job.failure_code == "asr_unavailable" and job.locked_by is None
        # exponential back-off: base, 2 x base, ... capped by the maximum
        expected = timedelta(seconds=settings.job_retry_delay_seconds * 2 ** (attempt - 1))
        assert policy.retry_delay(attempt) == expected
        drift = _utc(job.retry_after) - (before + expected)
        assert timedelta(seconds=-5) <= drift <= timedelta(seconds=10)
        # the same outbox row carries the retry, due at retry_after, for this generation
        assert job.dispatch.state == DispatchState.PENDING
        assert job.dispatch.generation == job.lease_generation
        assert _utc(job.dispatch.next_attempt_at) == _utc(job.retry_after)
        assert set(_reservations(job_id).values()) == {"reserved"}
        _make_due(job_id)

    result = process_job(job_id, worker_id="worker-3", settings=settings, providers=providers)
    assert result == {"job_id": job_id, "status": "failed", "code": "asr_unavailable"}
    job = _job(job_id)
    assert job.state == JobState.DEAD_LETTER and job.stage == "dead_letter"
    assert job.attempt_count == 3 == job.max_attempts and job.completed_at is not None
    assert job.failure_code == "asr_unavailable" and job.failure_class == "transient"
    assert "retry limit" in job.failure_message
    assert job.locked_by is None and job.retry_after is None and job.replay_count == 0
    assert set(_reservations(job_id).values()) == {"released"}
    assert _meeting(job_id).status == MeetingStatus.FAILED
    failed = _audit(workspace_id, "meeting.processing.failed")
    assert failed[-1].details["terminal_reason"] == "attempts_exhausted"
    assert failed[-1].details["failure_class"] == "transient"
    assert _committed_usage(workspace_id) == []

    # a late duplicate delivery is inert: terminal jobs are not claimed
    late = process_job(job_id, worker_id="worker-late", settings=settings, providers=providers)
    assert late == {"job_id": job_id, "status": "dead_letter"}
    assert provider.calls == 3


def test_permanent_failure_is_final_classified_and_not_replayable(
    client, email_outbox, monkeypatch, settings, broker
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    result = process_job(
        job_id,
        worker_id="worker",
        settings=settings,
        providers=_providers(asr=PermanentFailureASRProvider()),
    )
    assert result["status"] == "failed"
    job = _job(job_id)
    assert job.state == JobState.FAILED and job.failure_class == "permanent"
    assert job.attempt_count == 1 and job.max_attempts == settings.job_max_attempts
    assert set(_reservations(job_id).values()) == {"released"}
    assert _audit(workspace_id, "meeting.processing.failed")[-1].details["terminal_reason"] == (
        "permanent"
    )

    refused = _replay(client, workspace_id, job_id)
    assert refused.status_code == 409 and _error(refused) == "job_not_replayable"
    assert refused.json()["error"]["details"] == {"state": "failed"}
    assert _job(job_id).state == JobState.FAILED


def test_job_deadline_parks_instead_of_scheduling_or_starting_an_attempt(
    client, email_outbox, monkeypatch, settings, broker
):
    provider = AlwaysTransientASRProvider()
    providers = _providers(asr=provider)

    # (a) a retry that would start after the deadline is not scheduled
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    _edit_job(job_id, deadline_at=datetime.now(UTC) + timedelta(seconds=20))  # < back-off
    result = process_job(job_id, worker_id="worker-a", settings=settings, providers=providers)
    assert result == {"job_id": job_id, "status": "failed", "code": "asr_unavailable"}
    job = _job(job_id)
    assert job.state == JobState.DEAD_LETTER and job.attempt_count == 1
    assert job.failure_code == "job_deadline_exceeded" and job.failure_class == "deadline"
    assert "deadline" in job.failure_message
    assert set(_reservations(job_id).values()) == {"released"}
    failed = _audit(workspace_id, "meeting.processing.failed")[-1]
    assert failed.details["terminal_reason"] == "deadline_exceeded"
    assert failed.details["cause"] == "asr_unavailable"  # the underlying cause stays visible

    # (b) a queued job already past its deadline is parked at claim time, unrun
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    _edit_job(job_id, deadline_at=datetime.now(UTC) - timedelta(seconds=1))
    calls = provider.calls
    result = process_job(job_id, worker_id="worker-b", settings=settings, providers=providers)
    assert result == {"job_id": job_id, "status": "dead_letter"}
    job = _job(job_id)
    assert job.state == JobState.DEAD_LETTER and job.attempt_count == 0
    assert job.failure_code == "job_deadline_exceeded" and job.failure_class == "deadline"
    assert provider.calls == calls
    assert set(_reservations(job_id).values()) == {"released"}
    assert _meeting(job_id).status == MeetingStatus.FAILED


def test_recovery_parks_an_abandoned_attempt_past_the_job_deadline(
    client, email_outbox, monkeypatch, settings, broker
):
    job_id, _workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    _claim(job_id, "worker-a", settings)
    _edit_job(job_id, deadline_at=datetime.now(UTC) - timedelta(seconds=1))
    _expire_lease(job_id)

    assert recovery.sweep_expired_leases(settings) == {"scanned": 1, "exhausted": 1}
    job = _job(job_id)
    assert job.state == JobState.DEAD_LETTER and job.locked_by is None
    assert job.failure_code == "job_deadline_exceeded" and job.failure_class == "deadline"
    assert job.attempt_count == 1 and job.max_attempts == settings.job_max_attempts
    assert set(_reservations(job_id).values()) == {"released"}
    assert broker.published == []  # nothing was requeued


# --- publication failure and replay -------------------------------------------


def test_publication_exhaustion_parks_atomically_and_replay_runs_the_job(
    client, email_outbox, monkeypatch, settings, broker
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    broker.fail_times = 10_000
    clock = datetime.now(UTC)
    for _ in range(settings.dispatch_max_attempts):
        outcome = outbox.dispatch_job(job_id, settings, now=clock)
        clock = _utc(_dispatch_row(job_id).next_attempt_at) + timedelta(seconds=1)
    assert outcome.state == "failed"

    job = _job(job_id)
    assert job.dispatch.state == DispatchState.FAILED
    assert job.state == JobState.DEAD_LETTER and job.stage == "dead_letter"
    assert job.failure_code == "publication_failed" and job.failure_class == "publication"
    assert job.attempt_count == 0 and job.lease_generation == 0  # it never ran
    assert set(_reservations(job_id).values()) == {"released"}
    assert _meeting(job_id).status == MeetingStatus.FAILED
    failed = _audit(workspace_id, "meeting.processing.failed")[-1]
    assert failed.details["failure_class"] == "publication"
    assert failed.details["dispatch_attempts"] == settings.dispatch_max_attempts
    # a FAILED intent is never republished by the sweeper
    assert outbox.sweep_dispatch_outbox(settings, now=clock) == {"scanned": 0}
    assert broker.published == []

    # the broker is back: a workspace administrator replays the job
    broker.fail_times = 0
    response = _replay(client, workspace_id, job_id)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["replay"] == 1 and body["max_replays"] == settings.job_max_replays
    assert body["dispatch"] == "published"
    assert body["job"]["state"] == "queued" and body["job"]["stage"] == "replay_scheduled"
    assert body["job"]["replay_count"] == 1 and body["job"]["failure_code"] is None
    assert body["job"]["failure_class"] is None and body["job"]["deadline_at"] is not None
    assert body["job"]["lease"]["generation"] == 1
    assert body["job"]["dispatch"]["state"] == "published"
    assert body["job"]["dispatch"]["generation"] == 1
    assert broker.published == [(job_id, workspace_id)]
    job = _job(job_id)
    assert job.state == JobState.QUEUED and job.lease_generation == 1
    assert job.max_attempts == settings.job_max_attempts and job.replay_count == 1
    reservations = _reservations(job_id)
    assert sorted(reservations.values()) == ["released", "released", "reserved", "reserved"]
    assert reservations[job.reservation_id] == "reserved"
    replayed = _audit(workspace_id, "meeting.processing.replayed")
    assert len(replayed) == 1 and replayed[0].actor_user_id is not None
    assert replayed[0].details["previous_failure"] == "publication_failed"
    assert replayed[0].details["replay"] == 1 and replayed[0].details["generation"] == 1

    # the replayed job processes normally and settles once, on the new reservation
    result = process_job(
        job_id, worker_id="worker-replay", settings=settings, providers=_providers()
    )
    assert result["status"] in {"completed", "partial"}
    job = _job(job_id)
    assert job.state == JobState.COMPLETED and job.attempt_count == 1
    assert job.lease_generation == 2 and job.failure_class is None
    reservations = _reservations(job_id)
    assert reservations[job.reservation_id] == "committed"
    assert sorted(reservations.values()) == ["committed", "released", "released", "released"]
    committed = _committed_usage(workspace_id)
    assert len(committed) == 1 and committed[0].reservation_id == job.reservation_id


def test_replay_is_bounded_per_job_and_refuses_other_states(
    client, email_outbox, monkeypatch, settings, broker
):
    monkeypatch.setattr(settings, "job_max_replays", 1)
    job_id, workspace_id = _dead_letter_after_one_attempt(
        client, email_outbox, monkeypatch, settings
    )
    parked = _job(job_id)

    first = _replay(client, workspace_id, job_id)
    assert first.status_code == 202, first.text
    assert first.json()["max_replays"] == 1
    job = _job(job_id)
    assert job.state == JobState.QUEUED and job.replay_count == 1
    assert job.lease_generation == parked.lease_generation + 1
    assert job.max_attempts == parked.attempt_count + settings.job_max_attempts  # history kept

    # not dead-lettered: queued, then running
    queued = _replay(client, workspace_id, job_id)
    assert queued.status_code == 409 and _error(queued) == "job_not_replayable"
    assert queued.json()["error"]["details"] == {"state": "queued"}
    _claim(job_id, "worker-running", settings)
    running = _replay(client, workspace_id, job_id)
    assert running.status_code == 409 and _error(running) == "job_not_replayable"

    # parked again (abandoned, budget cut to what was used): the replay budget is spent
    _edit_job(job_id, max_attempts=2)
    _expire_lease(job_id)
    assert recovery.sweep_expired_leases(settings) == {"scanned": 1, "exhausted": 1}
    job = _job(job_id)
    assert job.state == JobState.DEAD_LETTER and job.failure_class == "abandoned"
    reservations_before = _reservations(job_id)
    limited = _replay(client, workspace_id, job_id)
    assert limited.status_code == 409 and _error(limited) == "replay_limit_reached"
    assert limited.json()["error"]["details"] == {"replay_count": 1, "max_replays": 1}
    after = _job(job_id)
    assert after.state == JobState.DEAD_LETTER and after.replay_count == 1
    assert after.lease_generation == job.lease_generation
    assert _reservations(job_id) == reservations_before  # nothing new reserved
    assert len(_audit(workspace_id, "meeting.processing.replayed")) == 1

    # a completed job is not replayable either
    done_id, done_workspace = _queued_upload(client, email_outbox, monkeypatch)
    assert process_job(done_id, worker_id="w", settings=settings, providers=_providers())[
        "status"
    ] in {"completed", "partial"}
    done = _replay(client, done_workspace, done_id)
    assert done.status_code == 409 and _error(done) == "job_not_replayable"


def test_replay_refuses_quota_missing_recording_retention_and_deleted_meeting(
    client, email_outbox, monkeypatch, settings, broker
):
    job_id, workspace_id = _dead_letter_after_one_attempt(
        client, email_outbox, monkeypatch, settings
    )
    owner_cookies = _session_cookies(client)
    parked = _job(job_id)
    reservations_before = _reservations(job_id)
    meeting_id = _meeting_of(job_id)

    def _unchanged() -> None:
        job = _job(job_id)
        assert job.state == JobState.DEAD_LETTER and job.replay_count == 0
        assert job.lease_generation == parked.lease_generation
        assert job.reservation_id == parked.reservation_id
        assert _reservations(job_id) == reservations_before
        assert _audit(workspace_id, "meeting.processing.replayed") == []

    # current entitlement no longer covers the recording: refused, nothing reserved
    with inspection_session_factory()() as db:
        entitlement = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id, Entitlement.metric == "audio_seconds"
            )
        )
        assert entitlement is not None
        original_limit = entitlement.limit_value
        entitlement.limit_value = entitlement.used_value + entitlement.reserved_value + 1
        db.commit()
    quota = _replay(client, workspace_id, job_id)
    assert quota.status_code == 429 and _error(quota) == "audio_quota_exceeded"
    assert quota.json()["error"]["details"]["metric"] == "audio_seconds"
    _unchanged()
    with inspection_session_factory()() as db:
        entitlement = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id, Entitlement.metric == "audio_seconds"
            )
        )
        entitlement.limit_value = original_limit
        db.commit()

    # the recording is gone (deleted after processing / retention purge)
    with inspection_session_factory()() as db:
        recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting_id))
        assert recording is not None
        recording.deleted_at = datetime.now(UTC)
        db.commit()
    missing = _replay(client, workspace_id, job_id)
    assert missing.status_code == 409 and _error(missing) == "recording_missing"
    _unchanged()
    with inspection_session_factory()() as db:
        recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting_id))
        recording.deleted_at = None
        db.commit()

    # retention has ended
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        original_retention = meeting.retention_expires_at
        meeting.retention_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    expired = _replay(client, workspace_id, job_id)
    assert expired.status_code == 409 and _error(expired) == "retention_expired"
    _unchanged()
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        meeting.retention_expires_at = original_retention
        db.commit()

    # the meeting was deleted through the API: never resurrected
    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}",
        headers={
            "X-Workspace-ID": workspace_id,
            "X-CSRF-Token": client.cookies.get(CSRF_COOKIE),
        },
    )
    assert deleted.status_code == 204, deleted.text
    gone = _replay(client, workspace_id, job_id)
    assert gone.status_code == 409 and _error(gone) == "meeting_deleted"
    _unchanged()

    # a job of another workspace is not visible to this workspace's administrator
    other_job, _other_workspace = _dead_letter_after_one_attempt(
        client, email_outbox, monkeypatch, settings
    )
    foreign = _replay(client, workspace_id, other_job, cookies=owner_cookies)
    assert foreign.status_code == 404 and _error(foreign) == "job_not_found"
    assert _job(other_job).state == JobState.DEAD_LETTER


def test_replay_requires_workspace_administration_csrf_and_is_audited(
    client, email_outbox, monkeypatch, settings, broker
):
    job_id, workspace_id = _dead_letter_after_one_attempt(
        client, email_outbox, monkeypatch, settings
    )
    owner_cookies = _session_cookies(client)

    # a member of the workspace may not replay
    credentials = register_user(client, email_outbox, email="replay-member@example.com")
    login_user(client, credentials)
    member_id, _ = _user_and_workspace("replay-member@example.com")
    _add_membership(workspace_id=workspace_id, user_id=member_id, role=WorkspaceRole.MEMBER)
    denied = _replay(client, workspace_id, job_id)
    assert denied.status_code == 403 and _error(denied) == "permission_denied"

    # a cookie-authenticated write without CSRF is refused even for the owner
    no_csrf = client.post(
        f"/api/v1/admin/jobs/{job_id}/replay",
        cookies=owner_cookies,
        headers={"X-Workspace-ID": workspace_id},
    )
    assert no_csrf.status_code == 403 and _error(no_csrf) == "csrf_failed"
    assert _job(job_id).state == JobState.DEAD_LETTER
    assert _audit(workspace_id, "meeting.processing.replayed") == []

    # the owner can, and the actor is recorded
    accepted = _replay(client, workspace_id, job_id, cookies=owner_cookies)
    assert accepted.status_code == 202, accepted.text
    replayed = _audit(workspace_id, "meeting.processing.replayed")
    assert len(replayed) == 1
    assert replayed[0].actor_user_id is not None and replayed[0].actor_user_id != member_id
    assert replayed[0].target_id == _meeting_of(job_id)
    assert replayed[0].details["previous_class"] == "transient"


# --- cancellation and fencing across a replay ---------------------------------


def test_deletion_cancels_the_job_with_a_cancellation_class(
    client, email_outbox, monkeypatch, settings, broker
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    meeting_id = _meeting_of(job_id)
    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}",
        headers={
            "X-Workspace-ID": workspace_id,
            "X-CSRF-Token": client.cookies.get(CSRF_COOKIE),
        },
    )
    assert deleted.status_code == 204, deleted.text
    job = _job(job_id)
    assert job.state == JobState.CANCELLED and job.stage == "cancelled"
    assert job.failure_class == "cancelled" and job.failure_code == "meeting_deleted"
    assert job.completed_at is not None and job.locked_by is None
    assert job.dispatch.state == DispatchState.CANCELLED
    assert set(_reservations(job_id).values()) == {"released"}
    # cancelled is final
    refused = _replay(client, workspace_id, job_id)
    assert refused.status_code == 409 and _error(refused) == "job_not_replayable"


def test_attempts_from_before_a_replay_cannot_write_after_it(
    client, email_outbox, monkeypatch, settings, broker
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    _edit_job(job_id, max_attempts=1)
    stale = _claim(job_id, "worker-old", settings)  # generation 1, attempt 1
    _expire_lease(job_id)
    assert recovery.sweep_expired_leases(settings) == {"scanned": 1, "exhausted": 1}
    job = _job(job_id)
    assert job.state == JobState.DEAD_LETTER and job.failure_class == "abandoned"
    assert job.failure_code == "lease_expired" and job.lease_generation == 2

    assert _replay(client, workspace_id, job_id).status_code == 202
    job = _job(job_id)
    assert job.state == JobState.QUEUED and job.lease_generation == 3
    assert job.max_attempts == 1 + settings.job_max_attempts

    # the old owner resumes after the replay: every protected write is rejected
    with pytest.raises(StaleLeaseError):
        _persist(stale, settings, "late result")
    late_failure = processor._finalize_failure(
        stale,
        error=ProcessingError("asr_unavailable", "late", retryable=True),
        settings=settings,
    )
    assert late_failure == "superseded"
    job = _job(job_id)
    assert job.state == JobState.QUEUED and job.lease_generation == 3

    fresh = _claim(job_id, "worker-new", settings)
    assert fresh.lease.generation == 4 and fresh.lease.attempt == 2
    with pytest.raises(StaleLeaseError):
        _persist(stale, settings, "late result")
    assert _persist(fresh, settings, "replayed result") == "completed"
    job = _job(job_id)
    assert job.state == JobState.COMPLETED and job.attempt_count == 2
    with inspection_session_factory()() as db:
        texts = db.scalars(
            select(TranscriptSegment.text).where(
                TranscriptSegment.meeting_id == _meeting_of(job_id)
            )
        ).all()
    assert texts == ["replayed result"]
    committed = _committed_usage(workspace_id)
    assert len(committed) == 1 and committed[0].reservation_id == job.reservation_id


def test_failure_policy_settings_are_validated(settings):
    for overrides in (
        {"job_retry_delay_seconds": 60, "job_retry_max_delay_seconds": 30},
        {"job_max_attempts": 0},
        {"job_max_replays": -1},
        {"job_attempt_timeout_seconds": 7200, "job_deadline_seconds": 3600},
    ):
        with pytest.raises(ValueError):
            Settings(**{**settings.model_dump(), **overrides, "_env_file": None})
    policy = RetryPolicy.from_settings(
        settings.model_copy(
            update={"job_retry_delay_seconds": 30, "job_retry_max_delay_seconds": 100}
        )
    )
    assert [policy.retry_delay(n).total_seconds() for n in (1, 2, 3, 4)] == [30, 60, 100, 100]
