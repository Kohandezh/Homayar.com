"""HOM-351 F4: a terminally failed analysis must not hide the transcript.

When ASR completed and its transcript checkpoint was committed, a terminal
failure of a later stage (intelligence) ends the meeting in the same ``partial``
outcome the policy-refusal path uses: the transcript is available through the
meeting API, the analysis run records the failure with its code, and no fake
summary is written. ASR itself failing still fails the meeting with no
transcript, and while retries remain nothing is written early. No test reaches
a network.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, update

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    AnalysisRun,
    Entitlement,
    JobState,
    Meeting,
    MeetingStatus,
    MeetingSummary,
    ProcessingJob,
    TranscriptRevision,
    TranscriptSegment,
    Workspace,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs import invariants, outbox
from backend.jobs.processor import process_job
from backend.providers.base import ProviderError
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.routing import PROVIDER_NOT_PERMITTED
from backend.tests._database import inspection_session_factory
from backend.tests.test_dispatch_outbox import FakeBroker
from backend.tests.test_job_failure_policy import _job, _meeting, _replay, _reservations
from backend.tests.test_job_processor import (
    PermanentFailureASRProvider,
    SingleSpeakerDiarizationProvider,
    _providers,
    _queued_upload,
    _stage_states,
)
from backend.tests.test_provider_routing import _bundle as _routing_bundle
from backend.tests.test_provider_routing import (
    _intelligence_stage,
    _llm_identity,
    _manifest,
    _patch_media,
    _run,
    _settings_with,
    _tenant,
    _Transport,
)
from backend.voicemail import pipeline as voicemail_pipeline

TRANSCRIPT_TEXT = "پیگیری قرارداد انجام شود"


class _FailingIntelligence(MockIntelligenceProvider):
    """A permitted analysis adapter whose every call fails."""

    def __init__(self, *, retryable: bool, code: str) -> None:
        super().__init__(allow_mock=True)
        self._retryable = retryable
        self._code = code

    async def analyze(self, request):
        raise ProviderError(
            self._code,
            "Intelligence provider failed for the test",
            retryable=self._retryable,
        )


def _bundle(intelligence) -> ProviderBundle:
    return ProviderBundle(
        asr=MockASRProvider(allow_mock=True, transcript=TRANSCRIPT_TEXT),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=intelligence,
    )


def _detail(client, workspace_id: str, meeting_id: str) -> dict:
    response = client.get(
        f"/api/v1/meetings/{meeting_id}", headers={"X-Workspace-ID": workspace_id}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _transcript(client, workspace_id: str, meeting_id: str) -> dict:
    response = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript", headers={"X-Workspace-ID": workspace_id}
    )
    assert response.status_code == 200, response.text
    return response.json()


# The console's meeting routes answer a failed-analysis partial meeting exactly
# as they answer a policy-refusal partial one (no summary yet, everything else
# readable).
CONSOLE_ROUTES = {
    "report": 200,
    "summary": 404,
    "job": 200,
    "actions": 200,
    "transcript/revisions": 200,
}


def _console_route_codes(client, workspace_id: str, meeting_id: str) -> dict[str, int]:
    return {
        route: client.get(
            f"/api/v1/meetings/{meeting_id}/{route}", headers={"X-Workspace-ID": workspace_id}
        ).status_code
        for route in CONSOLE_ROUTES
    }


# --- the allow cases (ruling R3: the partial outcome) -------------------------------------


def test_a_permanent_intelligence_failure_keeps_the_transcript_as_partial(
    client, email_outbox, settings, monkeypatch
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    result = process_job(
        job_id,
        worker_id="test-worker-failed-analysis",
        settings=settings,
        providers=_bundle(
            _FailingIntelligence(retryable=False, code="intelligence_invalid_response")
        ),
    )
    assert result == {
        "job_id": job_id,
        "status": "failed",
        "code": "intelligence_invalid_response",
    }

    factory = inspection_session_factory()
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        meeting = db.get(Meeting, job.meeting_id if job else "")
        assert job is not None and meeting is not None
        assert job.state == JobState.FAILED
        assert meeting.status == MeetingStatus.PARTIAL
        assert _stage_states(db, job_id) == {
            "materialize": ("completed", None),
            "asr": ("completed", None),
            "diarization": ("completed", None),
            "intelligence": ("failed", "intelligence_invalid_response"),
        }
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.meeting_id == meeting.id))
        assert run is not None
        assert run.state.value == "failed"
        assert run.reason_code == "intelligence_invalid_response"
        assert run.provider is None
        assert db.scalar(select(func.count(MeetingSummary.id))) == 0
        assert db.scalar(select(func.count(TranscriptSegment.id))) == 1
        audio = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id,
                Entitlement.metric == "audio_seconds",
            )
        )
        # Released like every terminal failure on base (finding 1): only a
        # completed job settles, so a replay can still settle once.
        assert audio is not None and (audio.used_value, audio.reserved_value) == (0, 0)

    assert _detail(client, workspace_id, meeting.id)["status"] == "partial"
    assert len(_transcript(client, workspace_id, meeting.id)["segments"]) == 1
    assert _console_route_codes(client, workspace_id, meeting.id) == CONSOLE_ROUTES


def test_exhausted_retries_keep_the_transcript_as_partial(
    client, email_outbox, settings, monkeypatch
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    # The retry budget is decided by the job row, not the runtime settings.
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job is not None
        job.max_attempts = 1
        db.commit()
    result = process_job(
        job_id,
        worker_id="test-worker-dead-letter-analysis",
        settings=settings,
        providers=_bundle(_FailingIntelligence(retryable=True, code="intelligence_unavailable")),
    )
    # ``process_job`` reports every terminal outcome (failed or dead letter) as
    # "failed" (unchanged on 0d1fc56); the job row says which one it is.
    assert result == {"job_id": job_id, "status": "failed", "code": "intelligence_unavailable"}

    factory = inspection_session_factory()
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        meeting = db.get(Meeting, job.meeting_id if job else "")
        assert job is not None and meeting is not None
        assert job.state == JobState.DEAD_LETTER
        assert meeting.status == MeetingStatus.PARTIAL
        assert _stage_states(db, job_id)["intelligence"] == ("failed", "intelligence_unavailable")
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.meeting_id == meeting.id))
        assert run is not None and run.state.value == "failed"
        assert run.reason_code == "intelligence_unavailable"
        assert db.scalar(select(func.count(MeetingSummary.id))) == 0
        assert db.scalar(select(func.count(TranscriptSegment.id))) == 1

    assert _detail(client, workspace_id, meeting.id)["status"] == "partial"
    assert len(_transcript(client, workspace_id, meeting.id)["segments"]) == 1


# --- the deny cases -----------------------------------------------------------------------


def test_a_policy_change_during_the_analysis_writes_no_transcript(
    client, email_outbox, settings, monkeypatch
):
    """WP08-T03: the attempt's policy snapshot never authorizes writing the
    transcript views after the workspace policy changed, even when the job
    then dead-letters. The next replay applies the new policy."""
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job is not None
        job.max_attempts = 1
        db.commit()

    class _PolicyChangingIntelligence(_FailingIntelligence):
        async def analyze(self, request):
            with get_session_factory()() as db:  # the owner turns on redaction meanwhile
                bind_tenant_context(db, workspace_id=workspace_id)
                workspace = db.get(Workspace, workspace_id)
                assert workspace is not None and not workspace.pii_redaction_enabled
                workspace.pii_redaction_enabled = True
                db.commit()
            return await super().analyze(request)

    result = process_job(
        job_id,
        worker_id="test-worker-policy-changed-analysis",
        settings=settings,
        providers=_bundle(
            _PolicyChangingIntelligence(retryable=True, code="intelligence_unavailable")
        ),
    )
    assert result == {"job_id": job_id, "status": "failed", "code": "intelligence_unavailable"}

    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        meeting = db.get(Meeting, job.meeting_id if job else "")
        assert job is not None and meeting is not None
        assert job.state == JobState.DEAD_LETTER
        assert meeting.status == MeetingStatus.FAILED
        assert db.scalar(select(func.count(TranscriptSegment.id))) == 0
    assert _transcript(client, workspace_id, meeting.id)["segments"] == []


# --- the controls -------------------------------------------------------------------------


def test_asr_failure_still_fails_the_meeting_with_no_transcript(
    client, email_outbox, settings, monkeypatch
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    result = process_job(
        job_id,
        worker_id="test-worker-asr-failure",
        settings=settings,
        providers=_providers(PermanentFailureASRProvider()),
    )
    assert result["status"] == "failed"

    factory = inspection_session_factory()
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        meeting = db.get(Meeting, job.meeting_id if job else "")
        assert job is not None and meeting is not None
        assert meeting.status == MeetingStatus.FAILED
    assert _transcript(client, workspace_id, meeting.id)["segments"] == []


def test_no_partial_state_is_written_while_retries_remain(
    client, email_outbox, settings, monkeypatch
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    result = process_job(
        job_id,
        worker_id="test-worker-retry-scheduled",
        settings=settings,
        providers=_bundle(_FailingIntelligence(retryable=True, code="intelligence_unavailable")),
    )
    assert result["status"] == "retry_scheduled"

    factory = inspection_session_factory()
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        meeting = db.get(Meeting, job.meeting_id if job else "")
        assert job is not None and meeting is not None
        assert meeting.status == MeetingStatus.QUEUED
        assert db.scalar(select(func.count(TranscriptSegment.id))) == 0
    assert _transcript(client, workspace_id, meeting.id)["segments"] == []


def test_a_policy_refusal_keeps_todays_partial_outcome(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    """Control: the policy-refusal partial path (the outcome F4 copies) is
    unchanged. The default policy refuses a cloud analysis endpoint."""
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "failed-analysis-refusal-control")
    transport = _Transport()
    cloud_settings = _settings_with(
        settings,
        tmp_path,
        [_manifest("intelligence", _llm_identity(), kind="cloud", region="eu-central")],
    )
    job_id, meeting_id, result = _run(
        client, tenant, cloud_settings, _routing_bundle(transport), key="refusal-control"
    )
    assert result["status"] == MeetingStatus.PARTIAL.value, result
    assert transport.bodies == []
    stage = _intelligence_stage(client, tenant, meeting_id)
    assert stage["state"] == "unavailable" and stage["reason_code"] == PROVIDER_NOT_PERMITTED
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job is not None and job.state == JobState.COMPLETED
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.meeting_id == meeting_id))
        assert run is not None and run.state.value == "unavailable"
    transcript = _transcript(client, tenant["workspace_id"], meeting_id)
    assert transcript["segments"]
    assert _console_route_codes(client, tenant["workspace_id"], meeting_id) == CONSOLE_ROUTES


def test_a_successful_job_is_unchanged(client, email_outbox, settings, monkeypatch):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    result = process_job(
        job_id, worker_id="test-worker-success-control", settings=settings, providers=_providers()
    )
    assert result == {"job_id": job_id, "status": "completed"}

    factory = inspection_session_factory()
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        meeting = db.get(Meeting, job.meeting_id if job else "")
        assert job is not None and meeting is not None
        assert meeting.status == MeetingStatus.COMPLETED
    assert len(_transcript(client, workspace_id, meeting.id)["segments"]) == 1


# --- review turn 1: settlement and replay, invariants, voicemail hook ----------------------


@pytest.fixture
def broker(monkeypatch) -> FakeBroker:
    fake = FakeBroker()
    monkeypatch.setattr(outbox, "publish_processing_job", fake)
    return fake


def _dead_letter_with_transcript(client, email_outbox, settings, monkeypatch) -> tuple[str, str]:
    """An F4 dead letter: retries exhausted in the analysis, transcript kept."""
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job is not None
        job.max_attempts = 1
        db.commit()
    result = process_job(
        job_id,
        worker_id="test-worker-f4-dead-letter",
        settings=settings,
        providers=_bundle(_FailingIntelligence(retryable=True, code="intelligence_unavailable")),
    )
    assert result["status"] == "failed"
    job = _job(job_id)
    assert job.state == JobState.DEAD_LETTER
    assert _meeting(job_id).status == MeetingStatus.PARTIAL
    return job_id, workspace_id


def _no_violations(workspace_id: str, settings) -> None:
    with inspection_session_factory()() as db:
        assert invariants.check_workspace(db, workspace_id, settings=settings) == []


def test_a_failed_analysis_settles_like_base_so_a_replay_completes(
    client, email_outbox, settings, monkeypatch, broker
):
    """Finding 1: the F4 path releases the audio allowance as every terminal
    failure does on base (only a completed job settles). A replay of the dead
    letter then settles once, on its new reservation."""
    job_id, workspace_id = _dead_letter_with_transcript(client, email_outbox, settings, monkeypatch)
    assert set(_reservations(job_id).values()) == {"released"}
    _no_violations(workspace_id, settings)

    replayed = _replay(client, workspace_id, job_id)
    assert replayed.status_code == 202, replayed.text
    result = process_job(
        job_id, worker_id="test-worker-f4-replay", settings=settings, providers=_providers()
    )
    assert result == {"job_id": job_id, "status": "completed"}
    job = _job(job_id)
    assert job.state == JobState.COMPLETED
    reservations = _reservations(job_id)
    assert reservations[job.reservation_id] == "committed"
    assert sorted(reservations.values()).count("committed") == 1
    assert _meeting(job_id).status == MeetingStatus.COMPLETED
    _no_violations(workspace_id, settings)


def test_a_second_terminal_failure_after_a_replay_ends_cleanly(
    client, email_outbox, settings, monkeypatch, broker
):
    job_id, workspace_id = _dead_letter_with_transcript(client, email_outbox, settings, monkeypatch)
    replayed = _replay(client, workspace_id, job_id)
    assert replayed.status_code == 202, replayed.text
    result = process_job(
        job_id,
        worker_id="test-worker-f4-replay-fails",
        settings=settings,
        providers=_bundle(
            _FailingIntelligence(retryable=False, code="intelligence_invalid_response")
        ),
    )
    assert result == {"job_id": job_id, "status": "failed", "code": "intelligence_invalid_response"}
    job = _job(job_id)
    assert job.state == JobState.FAILED and job.locked_by is None
    assert set(_reservations(job_id).values()) == {"released"}
    meeting = _meeting(job_id)
    assert meeting.status == MeetingStatus.PARTIAL
    # Each terminal attempt wrote its own revision; the latest is current.
    with inspection_session_factory()() as db:
        revisions = db.scalars(
            select(TranscriptRevision).where(TranscriptRevision.meeting_id == meeting.id)
        ).all()
        assert len(revisions) == 2 and all(rev.job_id == job_id for rev in revisions)
    assert len(_transcript(client, workspace_id, meeting.id)["segments"]) == 1
    _no_violations(workspace_id, settings)


def test_invariants_accept_a_failed_analysis_partial_meeting(
    client, email_outbox, settings, monkeypatch
):
    """R6 allow: PARTIAL with a FAILED latest job and a revision by that job."""
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    process_job(
        job_id,
        worker_id="test-worker-f4-invariants",
        settings=settings,
        providers=_bundle(
            _FailingIntelligence(retryable=False, code="intelligence_invalid_response")
        ),
    )
    assert _meeting(job_id).status == MeetingStatus.PARTIAL
    _no_violations(workspace_id, settings)


@pytest.mark.parametrize(
    "break_it",
    [
        pytest.param("asr-failure-marked-partial", id="no-revision"),
        pytest.param("revision-of-another-job", id="revision-not-by-the-job"),
    ],
)
def test_invariants_flag_a_partial_failed_meeting_without_its_transcript(
    client, email_outbox, settings, monkeypatch, break_it
):
    """R6 deny: PARTIAL with a FAILED latest job is a violation unless that
    job wrote a transcript revision of the meeting."""
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    if break_it == "asr-failure-marked-partial":
        process_job(
            job_id,
            worker_id="test-worker-f4-invariants-asr",
            settings=settings,
            providers=_providers(PermanentFailureASRProvider()),
        )
    else:
        process_job(
            job_id,
            worker_id="test-worker-f4-invariants-other",
            settings=settings,
            providers=_bundle(
                _FailingIntelligence(retryable=False, code="intelligence_invalid_response")
            ),
        )
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job is not None and job.state == JobState.FAILED
        meeting = db.get(Meeting, job.meeting_id)
        assert meeting is not None
        meeting.status = MeetingStatus.PARTIAL
        # A DBA write (Core, past the ORM immutability guard): the revision
        # is no longer attributed to this job.
        db.execute(
            update(TranscriptRevision.__table__)
            .where(TranscriptRevision.__table__.c.meeting_id == meeting.id)
            .values(job_id=None)
        )
        db.commit()
    with inspection_session_factory()() as db:
        violations = invariants.check_workspace(db, workspace_id, settings=settings)
    assert [(v.rule, v.job_id) for v in violations] == [("meeting_status", job_id)]


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param("analysis-fails", ("ready", None), id="failed-analysis-partial"),
        pytest.param("asr-fails", ("failed", "unsupported_audio"), id="asr-failure-control"),
    ],
)
def test_the_voicemail_hook_after_a_terminal_failure(
    client, email_outbox, settings, monkeypatch, case, expected
):
    """R8: after a failed-analysis partial outcome the voicemail hook runs
    ``on_transcript_ready`` (no failure reason), like the policy-refusal
    partial path; ASR failing still reports a processing failure."""
    calls: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(
        voicemail_pipeline,
        "on_transcript_ready",
        lambda db, meeting_id, settings=None: calls.append(("ready", meeting_id, None)),
    )
    monkeypatch.setattr(
        voicemail_pipeline,
        "on_processing_failed",
        lambda db, meeting_id, reason: calls.append(("failed", meeting_id, reason)),
    )
    job_id, _workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    providers = (
        _bundle(_FailingIntelligence(retryable=False, code="intelligence_invalid_response"))
        if case == "analysis-fails"
        else _providers(PermanentFailureASRProvider())
    )
    result = process_job(
        job_id, worker_id=f"test-worker-voicemail-{case}", settings=settings, providers=providers
    )
    assert result["status"] == "failed"
    assert calls == [(expected[0], _meeting(job_id).id, expected[1])]
