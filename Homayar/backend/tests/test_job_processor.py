from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from backend.database.saas_models import (
    ActionItem,
    AuditEvent,
    Entitlement,
    JobState,
    Meeting,
    MeetingStatus,
    MeetingSummary,
    ProcessingJob,
    Recording,
    TranscriptSegment,
    Workspace,
)
from backend.jobs.processor import process_job
from backend.providers.base import ASRProvider, DiarizationProvider, ProviderError
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import (
    MockASRProvider,
    MockIntelligenceProvider,
)
from backend.providers.schemas import (
    ASRRequest,
    ASRResult,
    DiarizationRequest,
    DiarizationResult,
    DiarizationSegment,
    DiarizationStatus,
)
from backend.storage import create_object_storage
from backend.tests._database import inspection_session_factory
from backend.tests.test_uploads_api import (
    _authenticated_workspace,
    _upload,
    _valid_metadata,
)


class SingleSpeakerDiarizationProvider(DiarizationProvider):
    """Test double for the fully successful path: MockDiarizationProvider is
    honestly *unavailable*, which now yields a partial outcome."""

    async def diarize(self, request: DiarizationRequest) -> DiarizationResult:
        return DiarizationResult(
            provider="single-speaker-test",
            status=DiarizationStatus.AVAILABLE,
            segments=[
                DiarizationSegment(speaker_label="speaker_1", start_seconds=0, end_seconds=1e6)
            ],
            is_mock=True,
        )


def _providers(asr: ASRProvider | None = None) -> ProviderBundle:
    return ProviderBundle(
        asr=asr or MockASRProvider(allow_mock=True, transcript="پیگیری قرارداد انجام شود"),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=MockIntelligenceProvider(allow_mock=True),
    )


def _queued_upload(client, email_outbox, monkeypatch) -> tuple[str, str]:
    _, workspace_id = _authenticated_workspace(client, email_outbox)
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=12.4),
    )
    monkeypatch.setattr(
        "backend.routers.v1.meetings.dispatch_job",
        lambda *_args, **_kwargs: True,
    )
    response = _upload(client, workspace_id=workspace_id, key="processor-upload-key")
    assert response.status_code == 202, response.text
    return response.json()["job"]["id"], workspace_id


def test_processor_persists_results_commits_usage_and_is_idempotent(
    client,
    email_outbox,
    settings,
    monkeypatch,
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)

    first = process_job(
        job_id,
        worker_id="test-worker-success",
        settings=settings,
        providers=_providers(),
    )
    assert first == {"job_id": job_id, "status": "completed"}
    second = process_job(
        job_id,
        worker_id="test-worker-duplicate",
        settings=settings,
        providers=_providers(),
    )
    assert second == {"job_id": job_id, "status": "completed"}

    factory = inspection_session_factory()
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        meeting = db.get(Meeting, job.meeting_id if job else "")
        assert job is not None and meeting is not None
        assert job.state == JobState.COMPLETED
        assert job.attempt_count == 1
        assert meeting.status == MeetingStatus.COMPLETED
        assert db.scalar(select(func.count(TranscriptSegment.id))) == 1
        assert db.scalar(select(func.count(MeetingSummary.id))) == 1
        assert db.scalar(select(func.count(ActionItem.id))) == 0

        audio = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id,
                Entitlement.metric == "audio_seconds",
            )
        )
        concurrency = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id,
                Entitlement.metric == "concurrent_jobs",
            )
        )
        assert audio is not None and concurrency is not None
        assert (audio.used_value, audio.reserved_value) == (13, 0)
        assert (concurrency.used_value, concurrency.reserved_value) == (0, 0)
        completed_audits = db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.event_type == "meeting.processing.completed"
            )
        )
        assert completed_audits == 1


class FlakyASRProvider(ASRProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.delegate = MockASRProvider(allow_mock=True, transcript="retry succeeded")

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        self.calls += 1
        if self.calls == 1:
            raise ProviderError(
                "asr_temporarily_unavailable",
                "Speech recognition is temporarily unavailable",
                retryable=True,
            )
        return await self.delegate.transcribe(request)


def test_processor_retries_transient_failure_without_releasing_reservations(
    client,
    email_outbox,
    settings,
    monkeypatch,
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    flaky = FlakyASRProvider()
    processing_settings = settings.model_copy(update={"job_retry_delay_seconds": 1})

    # The retry is durable, not a broker-level task retry: the job is requeued
    # with retry_after and its outbox intent is re-armed for that time.
    first = process_job(
        job_id,
        worker_id="test-worker-retry-1",
        settings=processing_settings,
        providers=_providers(flaky),
    )
    assert first == {
        "job_id": job_id,
        "status": "retry_scheduled",
        "code": "asr_temporarily_unavailable",
    }

    factory = inspection_session_factory()
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        assert job is not None and job.retry_after is not None
        assert job.locked_by is None and job.lease_expires_at is None
        dispatch = job.dispatch
        assert dispatch is not None and dispatch.state.value == "pending"
        assert dispatch.generation == job.lease_generation == 1
        assert dispatch.next_attempt_at == job.retry_after
        audio = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id,
                Entitlement.metric == "audio_seconds",
            )
        )
        concurrency = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id,
                Entitlement.metric == "concurrent_jobs",
            )
        )
        assert job is not None and job.state == JobState.QUEUED
        assert job.attempt_count == 1
        assert audio is not None and audio.reserved_value == 13
        assert concurrency is not None and concurrency.reserved_value == 1
        # A message delivered before retry_after is not claimable.
    early = process_job(
        job_id,
        worker_id="test-worker-retry-early",
        settings=processing_settings,
        providers=_providers(flaky),
    )
    assert early == {"job_id": job_id, "status": "not_due"}
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        job.retry_after = datetime.now(UTC) - timedelta(seconds=1)  # make it due
        db.commit()

    result = process_job(
        job_id,
        worker_id="test-worker-retry-2",
        settings=processing_settings,
        providers=_providers(flaky),
    )
    assert result["status"] == "completed"
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        assert job is not None and job.attempt_count == 2
        assert db.scalar(select(func.count(TranscriptSegment.id))) == 1


class PermanentFailureASRProvider(ASRProvider):
    async def transcribe(self, request: ASRRequest) -> ASRResult:
        raise ProviderError(
            "unsupported_audio",
            "Speech recognition cannot process this recording",
            retryable=False,
        )


def test_processor_terminal_failure_releases_all_reservations(
    client,
    email_outbox,
    settings,
    monkeypatch,
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)

    result = process_job(
        job_id,
        worker_id="test-worker-terminal",
        settings=settings,
        providers=_providers(PermanentFailureASRProvider()),
    )
    assert result == {"job_id": job_id, "status": "failed", "code": "unsupported_audio"}

    factory = inspection_session_factory()
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        meeting = db.get(Meeting, job.meeting_id if job else "")
        assert job is not None and meeting is not None
        assert job.state == JobState.FAILED
        assert meeting.status == MeetingStatus.FAILED
        entitlements = list(
            db.scalars(select(Entitlement).where(Entitlement.workspace_id == workspace_id))
        )
        assert entitlements
        assert all(entitlement.reserved_value == 0 for entitlement in entitlements)


def test_processor_honors_workspace_audio_deletion_policy(
    client,
    email_outbox,
    settings,
    monkeypatch,
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    factory = inspection_session_factory()
    with factory() as db:
        workspace = db.get(Workspace, workspace_id)
        job = db.get(ProcessingJob, job_id)
        assert workspace is not None and job is not None
        recording = db.scalar(
            select(Recording).where(Recording.meeting_id == job.meeting_id)
        )
        assert recording is not None
        workspace.delete_audio_after_processing = True
        storage_key = recording.storage_key
        meeting_id = job.meeting_id
        db.commit()

    result = process_job(
        job_id,
        worker_id="test-worker-delete-audio",
        settings=settings,
        providers=_providers(),
    )
    assert result["status"] == "completed"
    assert not create_object_storage(settings).exists(workspace_id, storage_key)
    with factory() as db:
        recording = db.scalar(
            select(Recording).where(Recording.meeting_id == meeting_id)
        )
        assert recording is not None and recording.deleted_at is not None

    detail = client.get(
        f"/api/v1/meetings/{meeting_id}",
        headers={"X-Workspace-ID": workspace_id},
    )
    assert detail.status_code == 200
    assert detail.json()["recording"] is None


# --- WP04-T01: truthful stage states -----------------------------------------


class SilentASRProvider(ASRProvider):
    async def transcribe(self, request: ASRRequest) -> ASRResult:
        return ASRResult(provider="silent-test", model="none", language="fa", text="", segments=[])


def _stage_states(db, job_id: str) -> dict[str, tuple[str, str | None]]:
    from backend.database.saas_models import ProcessingStageRun

    rows = db.scalars(
        select(ProcessingStageRun)
        .where(ProcessingStageRun.job_id == job_id)
        .order_by(ProcessingStageRun.sequence)
    ).all()
    return {row.stage: (row.state.value, row.reason_code) for row in rows}


def test_unavailable_diarization_yields_partial_outcome(
    client, email_outbox, settings, monkeypatch
):
    from backend.database.saas_models import ProviderUsage
    from backend.providers.diarization import UnavailableDiarizationProvider

    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    providers = ProviderBundle(
        asr=MockASRProvider(allow_mock=True, transcript="پیگیری قرارداد انجام شود"),
        diarization=UnavailableDiarizationProvider(),
        intelligence=MockIntelligenceProvider(allow_mock=True),
    )
    result = process_job(
        job_id, worker_id="test-worker-partial", settings=settings, providers=providers
    )
    assert result == {"job_id": job_id, "status": "partial"}

    factory = inspection_session_factory()
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        meeting = db.get(Meeting, job.meeting_id if job else "")
        assert job is not None and meeting is not None
        assert job.state == JobState.COMPLETED and job.stage == "partial"
        assert meeting.status == MeetingStatus.PARTIAL
        assert _stage_states(db, job_id) == {
            "materialize": ("completed", None),
            "asr": ("completed", None),
            "diarization": ("unavailable", "diarization_unavailable"),
            "intelligence": ("completed", None),
        }
        usage_stages = set(
            db.scalars(select(ProviderUsage.stage).where(ProviderUsage.job_id == job_id))
        )
        assert usage_stages == {"asr", "intelligence"}
        assert db.scalar(select(func.count(TranscriptSegment.id))) == 1
        audit = db.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "meeting.processing.completed")
        )
        assert audit is not None and audit.details["outcome"] == "partial"

    detail = client.get(f"/api/v1/meetings/{meeting.id}", headers={"X-Workspace-ID": workspace_id})
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["status"] == "partial"
    stages = {run["stage"]: run for run in payload["latest_job"]["stages"]}
    assert stages["diarization"]["state"] == "unavailable"
    assert stages["diarization"]["reason_code"] == "diarization_unavailable"
    assert stages["asr"]["is_mock"] is True


def test_empty_transcript_is_insufficient_evidence_not_a_failure(
    client, email_outbox, settings, monkeypatch
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    result = process_job(
        job_id,
        worker_id="test-worker-silent",
        settings=settings,
        providers=_providers(SilentASRProvider()),
    )
    assert result == {"job_id": job_id, "status": "partial"}

    factory = inspection_session_factory()
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        meeting = db.get(Meeting, job.meeting_id if job else "")
        assert job is not None and meeting is not None
        assert job.state == JobState.COMPLETED and job.failure_code is None
        assert meeting.status == MeetingStatus.PARTIAL
        assert _stage_states(db, job_id) == {
            "materialize": ("completed", None),
            "asr": ("insufficient_evidence", "empty_transcript"),
            "diarization": ("skipped", "no_transcript"),
            "intelligence": ("skipped", "no_transcript"),
        }
        assert db.scalar(select(func.count(TranscriptSegment.id))) == 0
        assert db.scalar(select(func.count(MeetingSummary.id))) == 0
        audio = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id,
                Entitlement.metric == "audio_seconds",
            )
        )
        assert audio is not None and (audio.used_value, audio.reserved_value) == (13, 0)


def test_terminal_failure_records_failed_and_unreached_stages(
    client, email_outbox, settings, monkeypatch
):
    job_id, _workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    result = process_job(
        job_id,
        worker_id="test-worker-stage-failure",
        settings=settings,
        providers=_providers(PermanentFailureASRProvider()),
    )
    assert result["status"] == "failed"

    factory = inspection_session_factory()
    with factory() as db:
        assert _stage_states(db, job_id) == {
            "materialize": ("completed", None),
            "asr": ("failed", "unsupported_audio"),
            "diarization": ("skipped", "not_reached"),
            "intelligence": ("skipped", "not_reached"),
        }
