"""HOM-63: recompute the analysis after a transcript correction (ADR 0037).

A correction appends a new current revision. The analysis that read the old
revision is then stale (derived, WP04-T06). The recompute reruns only the
intelligence stage over the corrected segments: a new current analysis run,
a new summary, new AI action items and new evidence, all bound to the
corrected revision. It never reruns ASR or diarization, never writes a
transcript revision, a processing job, a stage checkpoint or usage, and never
changes a stored row except the old run's current pointer. A failure appends a
failed non-current run and keeps the old outputs; after the attempt cap the
derived status is ``failed`` and only a person can ask again.
"""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import func, inspect, select, text, update

from backend.celery_app import (
    PROCESSING_QUEUE,
    RECOMPUTE_ANALYSIS_TASK,
    RECOMPUTE_SWEEP_TASK,
    celery_app,
)
from backend.config import Settings
from backend.database.saas_models import (
    ActionItem,
    AnalysisRun,
    AuditEvent,
    Entitlement,
    EvidenceSpan,
    Meeting,
    MeetingStatus,
    MeetingSummary,
    Membership,
    ProcessingJob,
    ProcessingStageRun,
    ProviderUsage,
    StageCheckpoint,
    StageState,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptSource,
    TranscriptTextView,
    UsageReservation,
    WorkspaceRole,
)
from backend.jobs import recompute
from backend.jobs.processor import process_job
from backend.observability.heartbeats import PERIODIC_TASKS
from backend.providers.base import ProviderError
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.schemas import ActionItem as ProviderActionItem
from backend.services import transcript_service
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import AUDIT_EVENT_CATALOG
from backend.services.quality_service import assess_quality
from backend.services.report_service import NOTICE_ANALYSIS_RECOMPUTE_FAILED
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_insights_shares_api import _write_headers
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider
from backend.tests.test_uploads_api import _authenticated_workspace, _upload, _valid_metadata

ORIGINAL_TEXT = "پیگیری قرارداد انجام شود"
CORRECTED_TEXT = "پیگیری فاکتور انجام شود"
RECOMPUTE_TYPES = {
    AuditEventType.MEETING_RECOMPUTE_REQUESTED.value,
    AuditEventType.MEETING_RECOMPUTE_COMPLETED.value,
    AuditEventType.MEETING_RECOMPUTE_FAILED.value,
    AuditEventType.MEETING_RECOMPUTE_SUPERSEDED.value,
}


class _ActionIntelligence(MockIntelligenceProvider):
    """The mock analysis plus one AI action item grounded in the first turn."""

    async def analyze(self, request):
        result = await super().analyze(request)
        action = ProviderActionItem(
            description=f"Follow up: {request.turns[0].text}", source_turn_indexes=[0]
        )
        return result.model_copy(update={"action_items": [action]})


class _FailingIntelligence(MockIntelligenceProvider):
    calls = 0

    async def analyze(self, request):
        type(self).calls += 1
        raise ProviderError("provider_unavailable", "The analysis provider is down", retryable=True)


def _bundle(intelligence: MockIntelligenceProvider | None = None) -> ProviderBundle:
    return ProviderBundle(
        asr=MockASRProvider(allow_mock=True, transcript=ORIGINAL_TEXT),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=intelligence or _ActionIntelligence(allow_mock=True),
    )


@pytest.fixture
def settings(settings):
    # The recompute builds its providers from the settings, like the worker.
    return settings.model_copy(
        update={
            "allow_mock_providers": True,
            "asr_provider": "mock",
            "diarization_provider": "mock",
            "intelligence_provider": "mock",
        }
    )


@pytest.fixture
def analysis_provider(monkeypatch):
    """The intelligence adapter every recompute in the test uses (swappable)."""
    holder = {"intelligence": _ActionIntelligence(allow_mock=True)}
    monkeypatch.setattr(
        recompute, "create_provider_bundle", lambda _settings: _bundle(holder["intelligence"])
    )
    return holder


def _processed_meeting(client, email_outbox, settings, monkeypatch, *, key="recompute-upload"):
    credentials, workspace_id = _authenticated_workspace(client, email_outbox)
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=65.2),
    )
    monkeypatch.setattr("backend.routers.v1.meetings.dispatch_job", lambda *_a, **_k: True)
    upload = _upload(client, workspace_id=workspace_id, key=key)
    assert upload.status_code == 202, upload.text
    payload = upload.json()
    result = process_job(
        payload["job"]["id"], worker_id="test-recompute", settings=settings, providers=_bundle()
    )
    assert result["status"] == "completed"
    return credentials, workspace_id, payload["meeting"]["id"]


def _read(workspace_id: str) -> dict[str, str]:
    return {"X-Workspace-ID": workspace_id}


def _get(client, workspace_id: str, path: str) -> dict:
    response = client.get(path, headers=_read(workspace_id))
    assert response.status_code == 200, response.text
    return response.json()


def _correct(client, workspace_id: str, meeting_id: str, text: str = CORRECTED_TEXT) -> dict:
    transcript = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/transcript")
    response = client.post(
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        headers=_write_headers(client, workspace_id),
        json={
            "base_revision_id": transcript["revision"]["id"],
            "reason": "fix the wording",
            "edits": [{"segment_id": transcript["segments"][0]["id"], "text": text}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["revision"]


def _quality(client, workspace_id: str, meeting_id: str) -> dict:
    return _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/quality")


def _report(client, workspace_id: str, meeting_id: str) -> dict:
    return _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/report")


def _manual(client, workspace_id: str, meeting_id: str, headers: dict | None = None):
    return client.post(
        f"/api/v1/meetings/{meeting_id}/analysis/recompute",
        headers=headers if headers is not None else _write_headers(client, workspace_id),
    )


def _snapshot(model, meeting_id: str) -> dict[str, dict]:
    keys = [attribute.key for attribute in inspect(model).column_attrs]
    with inspection_session_factory()() as db:
        rows = db.scalars(select(model).where(model.meeting_id == meeting_id)).all()
        return {row.id: {key: getattr(row, key) for key in keys} for row in rows}


def _count(model, *conditions) -> int:
    with inspection_session_factory()() as db:
        return int(db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0)


def _runs(meeting_id: str) -> list[AnalysisRun]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AnalysisRun)
                .where(AnalysisRun.meeting_id == meeting_id)
                .order_by(AnalysisRun.created_at.asc())
            )
        )


def _current_run(meeting_id: str) -> AnalysisRun:
    current = [run for run in _runs(meeting_id) if run.is_current]
    assert len(current) == 1
    return current[0]


def _audit(meeting_id: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.target_id == meeting_id,
                    AuditEvent.event_type.in_(RECOMPUTE_TYPES),
                )
                .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
            )
        )


def _footprint(workspace_id: str, meeting_id: str) -> dict[str, object]:
    """Everything a recompute must never add to or change (ASR, jobs, usage)."""
    with inspection_session_factory()() as db:
        entitlements = sorted(
            (row.metric, row.used_value, row.reserved_value)
            for row in db.scalars(
                select(Entitlement).where(Entitlement.workspace_id == workspace_id)
            )
        )
        status = db.scalar(select(Meeting.status).where(Meeting.id == meeting_id))
    return {
        "asr_revisions": _count(
            TranscriptRevision,
            TranscriptRevision.meeting_id == meeting_id,
            TranscriptRevision.source == TranscriptSource.ASR,
        ),
        "jobs": _count(ProcessingJob, ProcessingJob.meeting_id == meeting_id),
        "stage_runs": _count(ProcessingStageRun, ProcessingStageRun.meeting_id == meeting_id),
        "checkpoints": _count(StageCheckpoint, StageCheckpoint.meeting_id == meeting_id),
        "provider_usage": _count(ProviderUsage, ProviderUsage.meeting_id == meeting_id),
        "reservations": _count(UsageReservation, UsageReservation.workspace_id == workspace_id),
        "entitlements": entitlements,
        "status": status,
    }


def _no_recompute_on_correction(settings, monkeypatch) -> None:
    monkeypatch.setattr(settings, "recompute_on_correction", False)


# --- registry, settings and wiring ---------------------------------------------


def test_recompute_audit_types_are_registered_for_meetings_in_the_processing_catalog():
    for value in RECOMPUTE_TYPES:
        member = AuditEventType(value)
        assert member.target_type == "meeting"
        assert AUDIT_EVENT_CATALOG[member] == "processing"


def test_recompute_settings_have_safe_defaults(settings):
    assert settings.recompute_on_correction is True
    assert settings.recompute_max_attempts == 3
    assert settings.recompute_sweep_interval_seconds > 0
    assert settings.recompute_manual_rate_limit_per_minute == 10
    with pytest.raises(ValueError):
        Settings(environment="test", recompute_max_attempts=0, _env_file=None)
    with pytest.raises(ValueError):
        Settings(environment="test", recompute_sweep_interval_seconds=0, _env_file=None)
    with pytest.raises(ValueError):
        Settings(environment="test", recompute_manual_rate_limit_per_minute=0, _env_file=None)


def test_recompute_tasks_are_registered_routed_and_scheduled():
    assert RECOMPUTE_ANALYSIS_TASK == "backend.jobs.recompute_meeting_analysis"
    assert RECOMPUTE_SWEEP_TASK == "backend.jobs.sweep_stale_analyses"
    for task in (RECOMPUTE_ANALYSIS_TASK, RECOMPUTE_SWEEP_TASK):
        assert task in celery_app.tasks
        assert celery_app.amqp.router.route({}, task)["queue"].name == PROCESSING_QUEUE
    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    assert RECOMPUTE_SWEEP_TASK in scheduled
    assert "analysis_recompute" in PERIODIC_TASKS


# --- derived status --------------------------------------------------------------


def _stub_rows(*, run_revision: str, current_revision: str):
    revision = TranscriptRevision(id=current_revision, segment_count=1, is_mock=False)
    run = AnalysisRun(
        id="run-1", transcript_revision_id=run_revision, state=StageState.COMPLETED, is_mock=False
    )
    return revision, run


@pytest.mark.parametrize(
    ("run_revision", "failures", "status", "code"),
    [
        ("rev-2", 0, "up_to_date", None),
        ("rev-1", 0, "pending", None),
        ("rev-1", 2, "pending", None),
        ("rev-1", 3, "failed", "provider_unavailable"),
        ("rev-1", 4, "failed", "provider_unavailable"),
    ],
)
def test_recompute_status_is_derived_from_staleness_and_the_failure_cap(
    run_revision, failures, status, code
):
    revision, run = _stub_rows(run_revision=run_revision, current_revision="rev-2")
    quality = assess_quality(
        revision=revision,
        run=run,
        summary_status=None,
        action_statuses=[],
        stage_runs=[],
        segments=[(0, 1000)],
        speaker_count=1,
        duration_seconds=10,
        recompute_failures=failures,
        recompute_failure_code="provider_unavailable" if failures else None,
        recompute_max_attempts=3,
    )
    assert quality.analysis_stale is (run_revision != "rev-2")
    assert quality.recompute_status == status
    assert quality.recompute_failure_code == code
    assert quality.as_dict()["recompute_status"] == status


# --- the correction trigger ------------------------------------------------------


def test_correction_without_recompute_leaves_the_analysis_stale_and_pending(
    client, email_outbox, settings, monkeypatch, analysis_provider
):
    _no_recompute_on_correction(settings, monkeypatch)
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    before = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/summary")

    corrected = _correct(client, workspace_id, meeting_id)

    quality = _quality(client, workspace_id, meeting_id)
    assert quality["analysis_stale"] is True
    assert quality["transcript_revision_id"] == corrected["id"]
    assert quality["analysed_revision_id"] != corrected["id"]
    assert (quality["recompute_status"], quality["recompute_failure_code"]) == ("pending", None)
    report = _report(client, workspace_id, meeting_id)
    assert report["qa"]["quality"]["recompute_status"] == "pending"
    assert "analysis_stale" in report["completeness"]["notices"]
    assert NOTICE_ANALYSIS_RECOMPUTE_FAILED not in report["completeness"]["notices"]
    summary = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/summary")
    assert summary["analysis_run_id"] == before["analysis_run_id"]
    assert summary["overview"] == before["overview"] == ORIGINAL_TEXT
    evidence = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/evidence")["items"]
    assert evidence and all(item["is_current_revision"] is False for item in evidence)
    assert _audit(meeting_id) == []  # nothing requested, nothing ran


def test_correction_recomputes_the_analysis_on_the_corrected_revision(
    client, email_outbox, settings, monkeypatch, analysis_provider
):
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    human = client.post(
        f"/api/v1/meetings/{meeting_id}/actions",
        headers=_write_headers(client, workspace_id),
        json={"description": "Call the customer back"},
    )
    assert human.status_code == 201, human.text
    old_run = _current_run(meeting_id)
    old_ai_actions = {
        item["id"]
        for item in _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/actions")
        if item["created_by"] == "ai"
    }
    assert old_ai_actions
    stored = {
        model: _snapshot(model, meeting_id)
        for model in (TranscriptRevision, TranscriptSegment, EvidenceSpan, AnalysisRun)
    }
    footprint = _footprint(workspace_id, meeting_id)

    corrected = _correct(client, workspace_id, meeting_id)

    # (2) the current run reads the corrected revision; nothing is stale
    run = _current_run(meeting_id)
    assert run.id != old_run.id and run.transcript_revision_id == corrected["id"]
    assert run.state == StageState.COMPLETED and run.job_id is None
    assert (run.provider, run.model) == ("mock_intelligence", "deterministic-v1")
    assert (run.input_view, run.input_policy_digest) == ("original", None)
    assert run.reference_count == 2 and run.rejected_reference_count == 0
    quality = _quality(client, workspace_id, meeting_id)
    assert quality["analysis_stale"] is False and quality["analysis_run_id"] == run.id
    assert (quality["recompute_status"], quality["recompute_failure_code"]) == ("up_to_date", None)
    report = _report(client, workspace_id, meeting_id)
    assert "analysis_stale" not in report["completeness"]["notices"]

    summary = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/summary")
    assert summary["analysis_run_id"] == run.id and summary["overview"] == CORRECTED_TEXT
    actions = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/actions")
    ai = [item for item in actions if item["created_by"] == "ai"]
    assert len(ai) == 1 and ai[0]["id"] not in old_ai_actions
    assert ai[0]["description"] == f"Follow up: {CORRECTED_TEXT}"
    with inspection_session_factory()() as db:
        assert db.get(ActionItem, ai[0]["id"]).analysis_run_id == run.id
        kept = db.get(ActionItem, human.json()["id"])
        assert kept is not None and kept.created_by != "ai"
        segment = db.scalar(
            select(TranscriptSegment).where(TranscriptSegment.revision_id == corrected["id"])
        )
        assert db.get(ActionItem, ai[0]["id"]).source_segment_id == segment.id
        view = db.scalar(
            select(TranscriptTextView).where(TranscriptTextView.revision_id == corrected["id"])
        )
        assert view is not None
    new_spans = [
        span
        for span_id, span in _snapshot(EvidenceSpan, meeting_id).items()
        if span_id not in stored[EvidenceSpan]
    ]
    assert len(new_spans) == 2
    assert {span["transcript_revision_id"] for span in new_spans} == {corrected["id"]}
    assert {span["analysis_run_id"] for span in new_spans} == {run.id}
    evidence = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/evidence")["items"]
    assert {item["id"] for item in evidence if item["is_current_revision"]} == {
        span["id"] for span in new_spans
    }

    # (3) stored evidence never changes (the correction itself only moves the
    # revision pointer; the recompute only moves the run pointer)
    for model, before in stored.items():
        after = _snapshot(model, meeting_id)
        for row_id, values in before.items():
            if (model is AnalysisRun and row_id == old_run.id) or (
                model is TranscriptRevision and row_id == corrected["parent_revision_id"]
            ):
                assert after[row_id]["is_current"] is False
                assert after[row_id]["superseded_at"] is not None
                values = {**values, "is_current": False, "superseded_at": None}
                after[row_id] = {**after[row_id], "superseded_at": None}
            assert after[row_id] == values, f"{model.__name__} {row_id} changed"

    # (4) no ASR, no job, no checkpoint, no usage, no status change
    assert _footprint(workspace_id, meeting_id) == footprint
    assert footprint["status"] == MeetingStatus.COMPLETED
    assert _count(TranscriptRevision, TranscriptRevision.meeting_id == meeting_id) == 2

    # (9) audited with registered types; ids and counts only
    events = _audit(meeting_id)
    assert [event.event_type for event in events] == [
        AuditEventType.MEETING_RECOMPUTE_REQUESTED.value,
        AuditEventType.MEETING_RECOMPUTE_COMPLETED.value,
    ]
    requested, completed = events
    assert requested.details["trigger"] == "correction"
    assert requested.actor_user_id is not None and completed.actor_user_id is None
    assert completed.details["analysis_run_id"] == run.id
    assert completed.details["revision_id"] == corrected["id"]
    assert completed.details["previous_analysis_run_id"] == old_run.id
    assert completed.details["outcome"] == "completed"
    assert completed.request_id and completed.request_id.startswith("recompute:")
    for event in events:
        assert CORRECTED_TEXT not in str(event.details) and ORIGINAL_TEXT not in str(event.details)


def test_search_after_a_correction_returns_the_corrected_text(
    client, email_outbox, settings, monkeypatch, analysis_provider
):
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    _correct(client, workspace_id, meeting_id)

    def search(query: str) -> list[dict]:
        response = client.get("/api/v1/search", headers=_read(workspace_id), params={"q": query})
        assert response.status_code == 200, response.text
        return response.json()["items"]

    found = search("فاکتور")
    assert [item["id"] for item in found] == [meeting_id]
    assert all("قرارداد" not in match["snippet"] for match in found[0]["matches"])
    assert search("قرارداد") == []


def test_a_dispatch_failure_never_fails_the_correction(
    client, email_outbox, settings, monkeypatch, analysis_provider
):
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)

    def broken(*_args, **_kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr(recompute, "recompute_meeting_analysis", broken)
    corrected = _correct(client, workspace_id, meeting_id)

    quality = _quality(client, workspace_id, meeting_id)
    assert quality["transcript_revision_id"] == corrected["id"]
    assert (quality["analysis_stale"], quality["recompute_status"]) == (True, "pending")
    # The request is on record; the sweeper finds the meeting later.
    assert [event.event_type for event in _audit(meeting_id)] == [
        AuditEventType.MEETING_RECOMPUTE_REQUESTED.value
    ]


# --- idempotency and superseded targets -----------------------------------------


def test_recompute_is_idempotent_and_the_newest_correction_wins(
    client, email_outbox, settings, monkeypatch, analysis_provider
):
    _no_recompute_on_correction(settings, monkeypatch)
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    first = _correct(client, workspace_id, meeting_id)
    second = _correct(client, workspace_id, meeting_id, text="پیگیری رسید انجام شود")
    old_run = _current_run(meeting_id)

    outcome = recompute.recompute_meeting_analysis(
        meeting_id, first["id"], workspace_id=workspace_id, settings=settings
    )
    assert outcome == "superseded"
    assert _current_run(meeting_id).id == old_run.id
    assert not [run for run in _runs(meeting_id) if run.transcript_revision_id == first["id"]]
    assert [event.event_type for event in _audit(meeting_id)] == [
        AuditEventType.MEETING_RECOMPUTE_SUPERSEDED.value
    ]

    outcome = recompute.recompute_meeting_analysis(
        meeting_id, second["id"], workspace_id=workspace_id, settings=settings
    )
    assert outcome == "completed"
    run = _current_run(meeting_id)
    assert run.transcript_revision_id == second["id"]

    rows = {
        model: _snapshot(model, meeting_id)
        for model in (AnalysisRun, EvidenceSpan, ActionItem, MeetingSummary)
    }
    audit_count = len(_audit(meeting_id))
    # Without a workspace the task finds it itself (operator or test call).
    assert recompute.recompute_meeting_analysis(meeting_id, second["id"], settings=settings) == (
        "up_to_date"
    )
    assert {model: _snapshot(model, meeting_id) for model in rows} == rows
    assert len(_audit(meeting_id)) == audit_count


def test_recompute_without_a_prior_analysis_writes_nothing(
    client, email_outbox, settings, monkeypatch, analysis_provider
):
    _, workspace_id = _authenticated_workspace(client, email_outbox)
    meeting_id, revision_id = _meeting_without_analysis(workspace_id)

    outcome = recompute.recompute_meeting_analysis(
        meeting_id, revision_id, workspace_id=workspace_id, settings=settings
    )
    assert outcome == "no_prior_analysis"
    assert _runs(meeting_id) == [] and _audit(meeting_id) == []
    assert (
        recompute.recompute_meeting_analysis(
            meeting_id, "no-such-revision", workspace_id=workspace_id, settings=settings
        )
        == "superseded"
    )


def _meeting_without_analysis(workspace_id: str) -> tuple[str, str]:
    with inspection_session_factory()() as db:
        user_id = db.scalar(
            select(Membership.user_id).where(Membership.workspace_id == workspace_id)
        )
        meeting = Meeting(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            title="Never analysed",
            status=MeetingStatus.COMPLETED,
            duration_seconds=30,
        )
        db.add(meeting)
        db.flush()
        revision = TranscriptRevision(
            workspace_id=workspace_id,
            meeting_id=meeting.id,
            revision_number=1,
            source=TranscriptSource.ASR,
            reason="processing",
            language="fa",
            segment_count=1,
            is_current=True,
        )
        db.add(revision)
        db.flush()
        db.add(
            TranscriptSegment(
                workspace_id=workspace_id,
                meeting_id=meeting.id,
                revision_id=revision.id,
                sequence=0,
                speaker_key="speaker_0",
                start_ms=0,
                end_ms=1000,
                text=ORIGINAL_TEXT,
            )
        )
        db.commit()
        return meeting.id, revision.id


# --- failure, the attempt cap and the manual retry --------------------------------


def test_a_failing_provider_keeps_the_old_outputs_and_stops_at_the_cap(
    client, email_outbox, settings, monkeypatch, analysis_provider
):
    monkeypatch.setattr(settings, "recompute_max_attempts", 2)
    _no_recompute_on_correction(settings, monkeypatch)
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    corrected = _correct(client, workspace_id, meeting_id)
    old_run = _current_run(meeting_id)
    old = {model: _snapshot(model, meeting_id) for model in (MeetingSummary, ActionItem)}
    analysis_provider["intelligence"] = _FailingIntelligence(allow_mock=True)
    _FailingIntelligence.calls = 0

    for attempt in (1, 2):
        outcome = recompute.recompute_meeting_analysis(
            meeting_id, corrected["id"], workspace_id=workspace_id, settings=settings
        )
        assert outcome == "failed"
        assert _current_run(meeting_id).id == old_run.id
        failed = [run for run in _runs(meeting_id) if run.state == StageState.FAILED]
        assert len(failed) == attempt
        assert all(
            (run.is_current, run.transcript_revision_id, run.reason_code)
            == (False, corrected["id"], "provider_unavailable")
            for run in failed
        )
        assert {model: _snapshot(model, meeting_id) for model in old} == old
        quality = _quality(client, workspace_id, meeting_id)
        assert quality["analysis_stale"] is True
        assert quality["recompute_status"] == ("pending" if attempt < 2 else "failed")

    quality = _quality(client, workspace_id, meeting_id)
    assert quality["recompute_failure_code"] == "provider_unavailable"
    report = _report(client, workspace_id, meeting_id)
    assert report["qa"]["quality"]["recompute_status"] == "failed"
    notices = report["completeness"]["notices"]
    assert notices.index("analysis_stale") < notices.index(NOTICE_ANALYSIS_RECOMPUTE_FAILED)
    events = _audit(meeting_id)
    assert [event.event_type for event in events] == [
        AuditEventType.MEETING_RECOMPUTE_FAILED.value
    ] * 2
    assert events[0].outcome == "failure" and events[0].details["outcome"] == "failed"
    assert events[0].details["analysis_run_id"] in {run.id for run in _runs(meeting_id)}
    # The sanitizer redacts keys containing "code": the reason travels as "cause".
    assert events[0].details["cause"] == "provider_unavailable"
    assert not [key for event in events for key in event.details if "code" in key.casefold()]

    # The sweeper (automatic recompute on) leaves a meeting at the cap alone ...
    monkeypatch.setattr(settings, "recompute_on_correction", True)
    swept = recompute.sweep_stale_analyses(settings=settings, workspace_ids=[workspace_id])
    assert (swept["stale"], swept["dispatched"], swept["at_failure_cap"]) == (1, 0, 1)
    assert _FailingIntelligence.calls == 2

    # ... but a person may ask for one more attempt (allow-control).
    response = _manual(client, workspace_id, meeting_id)
    assert response.status_code == 202, response.text
    assert response.json() == {"status": "failed"}
    assert _FailingIntelligence.calls == 3
    assert len([run for run in _runs(meeting_id) if run.state == StageState.FAILED]) == 3

    # A provider that works again clears the failure.
    analysis_provider["intelligence"] = _ActionIntelligence(allow_mock=True)
    response = _manual(client, workspace_id, meeting_id)
    assert response.status_code == 202 and response.json() == {"status": "up_to_date"}
    quality = _quality(client, workspace_id, meeting_id)
    assert (quality["recompute_status"], quality["recompute_failure_code"]) == ("up_to_date", None)
    assert (
        NOTICE_ANALYSIS_RECOMPUTE_FAILED
        not in _report(client, workspace_id, meeting_id)["completeness"]["notices"]
    )


# --- the manual endpoint ---------------------------------------------------------


def test_manual_recompute_needs_meeting_update_in_the_meetings_workspace(
    client, email_outbox, settings, monkeypatch, analysis_provider
):
    _no_recompute_on_correction(settings, monkeypatch)
    credentials, workspace_id, meeting_id = _processed_meeting(
        client, email_outbox, settings, monkeypatch
    )

    # Not stale: 200 and no new rows.
    runs = _snapshot(AnalysisRun, meeting_id)
    response = _manual(client, workspace_id, meeting_id)
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "up_to_date"}
    assert _snapshot(AnalysisRun, meeting_id) == runs and _audit(meeting_id) == []

    _correct(client, workspace_id, meeting_id)

    # Without CSRF proof: refused.
    assert _manual(client, workspace_id, meeting_id, headers=_read(workspace_id)).status_code == 403

    # A viewer (no meeting:update): 403, nothing written.
    with inspection_session_factory()() as db:
        db.execute(
            update(Membership)
            .where(Membership.workspace_id == workspace_id)
            .values(role=WorkspaceRole.VIEWER)
        )
        db.commit()
    assert _manual(client, workspace_id, meeting_id).status_code == 403
    assert _audit(meeting_id) == []
    with inspection_session_factory()() as db:
        db.execute(
            update(Membership)
            .where(Membership.workspace_id == workspace_id)
            .values(role=WorkspaceRole.OWNER)
        )
        db.commit()

    # Another workspace's member: 404, nothing written.
    outsider = register_user(client, email_outbox, email="recompute-outsider@example.com")
    other_workspace = login_user(client, outsider)["workspaces"][0]["id"]
    assert _manual(client, other_workspace, meeting_id).status_code == 404
    assert _audit(meeting_id) == []

    # The owner, stale: 202, one run, audited as a manual request.
    login_user(client, credentials)
    response = _manual(client, workspace_id, meeting_id)
    assert response.status_code == 202, response.text
    assert response.json() == {"status": "up_to_date"}
    assert len(_snapshot(AnalysisRun, meeting_id)) == len(runs) + 1
    events = _audit(meeting_id)
    assert [event.event_type for event in events] == [
        AuditEventType.MEETING_RECOMPUTE_REQUESTED.value,
        AuditEventType.MEETING_RECOMPUTE_COMPLETED.value,
    ]
    assert events[0].details["trigger"] == "manual" and events[0].actor_user_id is not None


def test_manual_recompute_is_rate_limited_per_workspace(
    client, email_outbox, settings, monkeypatch, analysis_provider
):
    """SPEC amendment B: every manual request counts against its workspace's
    per-minute limit, an ``up_to_date`` answer too; over it, 429 in the upload
    error shape and nothing is written."""
    monkeypatch.setattr(settings, "recompute_manual_rate_limit_per_minute", 2)
    _no_recompute_on_correction(settings, monkeypatch)
    first, first_workspace, first_meeting = _processed_meeting(
        client, email_outbox, settings, monkeypatch, key="limit-first"
    )
    second, second_workspace, second_meeting = _processed_meeting(
        client, email_outbox, settings, monkeypatch, key="limit-second"
    )

    # Allow-control: requests under the limit pass.
    login_user(client, first)
    for _ in range(2):
        response = _manual(client, first_workspace, first_meeting)
        assert response.status_code == 200 and response.json() == {"status": "up_to_date"}

    # Deny: the third request of the minute is refused, even for a stale meeting.
    _correct(client, first_workspace, first_meeting)
    limited = _manual(client, first_workspace, first_meeting)
    assert limited.status_code == 429, limited.text
    assert limited.json()["error"]["code"] == "recompute_rate_limited"
    assert limited.json()["error"]["message"] == "Too many recompute requests"
    assert int(limited.headers["Retry-After"]) > 0
    assert _audit(first_meeting) == []
    assert _quality(client, first_workspace, first_meeting)["recompute_status"] == "pending"

    # Allow-control: another workspace is not limited by the first one's requests.
    login_user(client, second)
    response = _manual(client, second_workspace, second_meeting)
    assert response.status_code == 200 and response.json() == {"status": "up_to_date"}


# --- the sweeper -----------------------------------------------------------------


def test_sweeper_dispatches_only_stale_meetings_below_the_cap(
    client, email_outbox, settings, monkeypatch, analysis_provider
):
    _no_recompute_on_correction(settings, monkeypatch)
    _, workspace_id, lost = _processed_meeting(
        client, email_outbox, settings, monkeypatch, key="sweep-lost"
    )
    lost_revision = _correct(client, workspace_id, lost)
    up_to_date = _upload_and_process(client, workspace_id, settings, key="sweep-current")
    capped = _upload_and_process(client, workspace_id, settings, key="sweep-capped")
    capped_revision = _correct(client, workspace_id, capped)
    _add_failed_runs(workspace_id, capped, capped_revision["id"], settings.recompute_max_attempts)
    never_analysed, _ = _meeting_without_analysis(workspace_id)

    dispatched: list[tuple[str, str, str | None]] = []
    real_dispatch = recompute.dispatch_recompute

    def capture(meeting_id, revision_id, *, workspace_id, settings):
        dispatched.append((meeting_id, revision_id, workspace_id))
        return real_dispatch(meeting_id, revision_id, workspace_id=workspace_id, settings=settings)

    monkeypatch.setattr(recompute, "dispatch_recompute", capture)

    # Automatic recompute off (SPEC amendment A): the sweep dispatches nothing.
    counts = recompute.sweep_stale_analyses(settings=settings, workspace_ids=[workspace_id])
    assert counts["disabled"] == 1 and counts["dispatched"] == 0 and dispatched == []
    assert _quality(client, workspace_id, lost)["recompute_status"] == "pending"

    # On again: the correction's dispatch above counts as lost; the sweep finds it.
    monkeypatch.setattr(settings, "recompute_on_correction", True)
    counts = recompute.sweep_stale_analyses(settings=settings, workspace_ids=[workspace_id])

    assert counts["disabled"] == 0
    assert dispatched == [(lost, lost_revision["id"], workspace_id)]
    assert counts["stale"] == 2 and counts["dispatched"] == 1 and counts["at_failure_cap"] == 1
    assert _current_run(lost).transcript_revision_id == lost_revision["id"]
    assert _quality(client, workspace_id, lost)["recompute_status"] == "up_to_date"
    assert _quality(client, workspace_id, capped)["recompute_status"] == "failed"
    assert _runs(never_analysed) == []
    sweep_events = [event for event in _audit(lost) if event.request_id]
    assert sweep_events and all(event.request_id.startswith("recompute:") for event in sweep_events)
    del up_to_date

    # A second sweep finds nothing left to do.
    dispatched.clear()
    assert (
        recompute.sweep_stale_analyses(settings=settings, workspace_ids=[workspace_id])[
            "dispatched"
        ]
        == 0
    )
    assert dispatched == []


def _upload_and_process(client, workspace_id: str, settings, *, key: str) -> str:
    upload = _upload(client, workspace_id=workspace_id, key=key)
    assert upload.status_code == 202, upload.text
    payload = upload.json()
    result = process_job(
        payload["job"]["id"], worker_id="test-recompute", settings=settings, providers=_bundle()
    )
    assert result["status"] == "completed"
    return payload["meeting"]["id"]


def _add_failed_runs(workspace_id: str, meeting_id: str, revision_id: str, count: int) -> None:
    with inspection_session_factory()() as db:
        for _ in range(count):
            db.add(
                AnalysisRun(
                    workspace_id=workspace_id,
                    meeting_id=meeting_id,
                    transcript_revision_id=revision_id,
                    state=StageState.FAILED,
                    reason_code="provider_unavailable",
                    is_current=False,
                )
            )
        db.commit()


# --- PostgreSQL: concurrent recomputes ---------------------------------------------


class _GatedIntelligence(_ActionIntelligence):
    """Holds every analysis until both recomputes have called the provider."""

    barrier = threading.Barrier(2, timeout=30)

    async def analyze(self, request):
        type(self).barrier.wait()
        return await super().analyze(request)


@pytest.mark.postgres
def test_two_recomputes_for_the_same_target_write_one_current_run(
    client, email_outbox, settings, monkeypatch, analysis_provider
):
    _no_recompute_on_correction(settings, monkeypatch)
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    corrected = _correct(client, workspace_id, meeting_id)
    _GatedIntelligence.barrier.reset()
    analysis_provider["intelligence"] = _GatedIntelligence(allow_mock=True)

    outcomes: list[str] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            outcomes.append(
                recompute.recompute_meeting_analysis(
                    meeting_id, corrected["id"], workspace_id=workspace_id, settings=settings
                )
            )
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    assert sorted(outcomes) == ["completed", "up_to_date"]
    on_target = [run for run in _runs(meeting_id) if run.transcript_revision_id == corrected["id"]]
    assert len(on_target) == 1 and on_target[0].is_current
    assert _count(MeetingSummary, MeetingSummary.meeting_id == meeting_id) == 1
    with inspection_session_factory()() as db:
        ai = db.scalars(
            select(ActionItem).where(
                ActionItem.meeting_id == meeting_id, ActionItem.created_by == "ai"
            )
        ).all()
    assert [item.analysis_run_id for item in ai] == [on_target[0].id]


def _wait_until_a_session_waits_for_a_lock(timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with inspection_session_factory()() as db:
            waiting = db.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            )
        if waiting:
            return
        time.sleep(0.05)
    raise AssertionError("the recompute never waited for the revision row lock")


@pytest.mark.postgres
def test_a_correction_and_a_recompute_write_step_overlap_without_a_deadlock(
    client, email_outbox, settings, monkeypatch, analysis_provider
):
    """Lock order (SPEC amendment C): a correction holds the current-revision row
    lock and then inserts its revision, whose foreign key takes FOR KEY SHARE on
    the meeting.  A recompute that reaches its write step in between must not
    hold a lock on the meeting that conflicts with it."""
    _no_recompute_on_correction(settings, monkeypatch)
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    first = _correct(client, workspace_id, meeting_id)
    old_run = _current_run(meeting_id)
    transcript = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/transcript")
    headers = _write_headers(client, workspace_id)

    holding, release, at_write = threading.Event(), threading.Event(), threading.Event()
    create_revision = transcript_service.create_revision

    def create_revision_after_release(*args, **kwargs):
        holding.set()  # correct_revision holds the current-revision row lock
        assert release.wait(timeout=30)
        return create_revision(*args, **kwargs)

    current_revision = recompute.current_revision

    def current_revision_seen(*args, **kwargs):
        if kwargs.get("for_update"):
            at_write.set()  # the recompute's write step asks for the same lock
        return current_revision(*args, **kwargs)

    failures: list[BaseException] = []

    def failure_code(exc, *, meeting_id):
        failures.append(exc)
        return "recompute_failed"

    monkeypatch.setattr(transcript_service, "create_revision", create_revision_after_release)
    monkeypatch.setattr(recompute, "current_revision", current_revision_seen)
    monkeypatch.setattr(recompute, "_failure_code", failure_code)
    results: dict[str, object] = {}

    def correct() -> None:
        try:
            results["correction"] = client.post(
                f"/api/v1/meetings/{meeting_id}/transcript/corrections",
                headers=headers,
                json={
                    "base_revision_id": transcript["revision"]["id"],
                    "reason": "fix the wording again",
                    "edits": [
                        {
                            "segment_id": transcript["segments"][0]["id"],
                            "text": "پیگیری رسید انجام شود",
                        }
                    ],
                },
            )
        except BaseException as exc:  # pragma: no cover - reported below
            results["correction_error"] = exc

    def run() -> None:
        try:
            results["recompute"] = recompute.recompute_meeting_analysis(
                meeting_id, first["id"], workspace_id=workspace_id, settings=settings
            )
        except BaseException as exc:  # pragma: no cover - reported below
            results["recompute_error"] = exc

    correction = threading.Thread(target=correct)
    correction.start()
    assert holding.wait(timeout=30)
    worker = threading.Thread(target=run)
    worker.start()
    assert at_write.wait(timeout=30)
    _wait_until_a_session_waits_for_a_lock()
    release.set()
    correction.join(timeout=60)
    worker.join(timeout=60)

    assert "correction_error" not in results and "recompute_error" not in results, results
    assert failures == []  # no deadlock (40P01) or other error inside the recompute
    assert results["correction"].status_code == 201, results["correction"].text
    newest = results["correction"].json()["revision"]["id"]
    assert results["recompute"] == "superseded"  # the newer correction wins
    assert _current_run(meeting_id).id == old_run.id
    assert not [run for run in _runs(meeting_id) if run.transcript_revision_id == first["id"]]

    monkeypatch.setattr(transcript_service, "create_revision", create_revision)
    assert (
        recompute.recompute_meeting_analysis(
            meeting_id, newest, workspace_id=workspace_id, settings=settings
        )
        == "completed"
    )
    assert _current_run(meeting_id).transcript_revision_id == newest
