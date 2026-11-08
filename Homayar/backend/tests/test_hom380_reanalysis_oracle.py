"""HOM-380 oracle: a re-analysis keeps a person's assignment on a voicemail meeting.

Written from SPEC.md only (ADR 0054).  ``persist_analysis`` has two callers: the
recompute after a transcript correction and the full processing job.  On a
voicemail meeting both must keep every AI action whose ``assignee_user_id`` is
not null, unchanged, and replace only the unassigned AI actions (A1 to A3).  On
any other meeting every AI action is replaced as before (A4).  Actions made by
``voicemail_rules`` or ``user`` are never removed (A5).  The recompute's
completed audit row keeps its shape and its ``action_item_count`` (A6).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, select

from backend.config import Settings
from backend.database.saas_models import (
    ActionItem,
    AnalysisRun,
    AuditEvent,
    EvidenceSpan,
    JobState,
    ProcessingJob,
    TranscriptRevision,
)
from backend.jobs import recompute
from backend.jobs.processor import process_job
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.schemas import ActionItem as ProviderActionItem
from backend.routers.v1 import voicemail as voicemail_router
from backend.services.audit_event_types import AuditEventType
from backend.tests._database import inspection_session_factory
from backend.tests.test_authorization_matrix import _Persona, _workspace_with_roles
from backend.tests.test_ingestion_ledger import _widen_concurrency
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider
from backend.tests.test_review_queue_access import stranger
from backend.tests.test_uploads_api import _valid_metadata
from backend.voicemail.models import VoicemailMessage

ORIGINAL_TEXT = "پیگیری قرارداد انجام شود"
CORRECTED_TEXT = "پیگیری فاکتور انجام شود"
OWNER_TEXT = "کارشناس فروش"
ACTIONS_PER_RUN = 2
PATHS = ["recompute", "process_job"]
_ids = itertools.count(1)


class _TwoActions(MockIntelligenceProvider):
    """The mock analysis plus two AI actions grounded in the first turn, each
    with an ``owner`` text, so ``assignee_text`` is set and ``assignee_user_id``
    is null on every action the run writes."""

    async def analyze(self, request):
        result = await super().analyze(request)
        said = request.turns[0].text
        actions = [
            ProviderActionItem(
                description=f"Call back {index}: {said}",
                owner=OWNER_TEXT,
                source_turn_indexes=[0],
            )
            for index in range(ACTIONS_PER_RUN)
        ]
        return result.model_copy(update={"action_items": actions})


def _bundle() -> ProviderBundle:
    return ProviderBundle(
        asr=MockASRProvider(allow_mock=True, transcript=ORIGINAL_TEXT),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=_TwoActions(allow_mock=True),
    )


@dataclass
class Crew:
    owner: _Persona
    member: _Persona
    settings: Settings


@pytest.fixture
def settings(settings):
    return settings.model_copy(
        update={
            "allow_mock_providers": True,
            "asr_provider": "mock",
            "diarization_provider": "mock",
            "intelligence_provider": "mock",
        }
    )


@pytest.fixture
def crew(client, settings, monkeypatch, email_outbox) -> Crew:
    client.app.include_router(voicemail_router.router, prefix=settings.api_prefix)
    monkeypatch.setattr(
        "backend.voicemail.ingest.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=42.3),
    )
    monkeypatch.setattr("backend.voicemail.ingest.dispatch_job", lambda job_id, _settings: True)
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=65.2),
    )
    monkeypatch.setattr("backend.routers.v1.meetings.dispatch_job", lambda *_a, **_k: True)
    monkeypatch.setattr(recompute, "create_provider_bundle", lambda _settings: _bundle())
    owner, people, _ = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    _widen_concurrency(owner.workspace_id)
    return Crew(owner=owner, member=people["member"], settings=settings)


def _voicemail_meeting(crew: Crew) -> tuple[str, str]:
    response = crew.owner.post(
        "/api/v1/voicemails",
        data={
            "external_message_id": f"vm380-{next(_ids):04d}",
            "caller_number": "0912 345 6789",
            "mailbox": "sales",
        },
        files={"file": ("vm.wav", b"voicemail wav bytes", "audio/wav")},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    return body["voicemail"]["meeting_id"], body["job"]["id"]


def _plain_meeting(crew: Crew) -> tuple[str, str]:
    response = crew.owner.client.post(
        "/api/v1/uploads",
        cookies=crew.owner.cookies,
        headers={**crew.owner.headers, "Idempotency-Key": f"hom380-{next(_ids):04d}"},
        data={"title": "Weekly customer call", "language": "fa"},
        files={"file": ("customer-call.wav", b"staged wav bytes", "audio/wav")},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    return body["meeting"]["id"], body["job"]["id"]


def _process(crew: Crew, job_id: str) -> None:
    result = process_job(
        job_id, worker_id="hom380-oracle", settings=crew.settings, providers=_bundle()
    )
    assert result["status"] == "completed", result


def _is_voicemail(meeting_id: str, workspace_id: str) -> bool:
    with inspection_session_factory()() as db:
        return (
            db.scalar(
                select(VoicemailMessage.id).where(
                    VoicemailMessage.meeting_id == meeting_id,
                    VoicemailMessage.workspace_id == workspace_id,
                )
            )
            is not None
        )


def _processed(crew: Crew, *, voicemail: bool) -> str:
    meeting_id, job_id = _voicemail_meeting(crew) if voicemail else _plain_meeting(crew)
    _process(crew, job_id)
    assert _is_voicemail(meeting_id, crew.owner.workspace_id) is voicemail
    return meeting_id


def _assign(crew: Crew, meeting_id: str, action_id: str) -> None:
    response = crew.owner.patch(
        f"/api/v1/meetings/{meeting_id}/actions/{action_id}",
        json={"assignee_user_id": crew.member.user_id},
    )
    assert response.status_code == 200, response.text
    assert response.json()["assignee_user_id"] == crew.member.user_id


def _user_action(crew: Crew, meeting_id: str) -> str:
    response = crew.member.post(
        f"/api/v1/meetings/{meeting_id}/actions", json={"description": "Call the customer back"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["created_by"] == "user"
    return response.json()["id"]


def _rules_action(crew: Crew, meeting_id: str) -> str:
    with inspection_session_factory()() as db:
        action = ActionItem(
            workspace_id=crew.owner.workspace_id,
            meeting_id=meeting_id,
            description="Return the customer's call",
            created_by="voicemail_rules",
        )
        db.add(action)
        db.commit()
        return action.id


def _actions(meeting_id: str) -> dict[str, dict]:
    keys = [attribute.key for attribute in inspect(ActionItem).column_attrs]
    with inspection_session_factory()() as db:
        rows = db.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting_id)).all()
        return {row.id: {key: getattr(row, key) for key in keys} for row in rows}


def _ai_ids(meeting_id: str) -> list[str]:
    return sorted(
        action_id for action_id, row in _actions(meeting_id).items() if row["created_by"] == "ai"
    )


def _evidence_of(action_id: str) -> dict[str, dict]:
    keys = [attribute.key for attribute in inspect(EvidenceSpan).column_attrs]
    with inspection_session_factory()() as db:
        rows = db.scalars(select(EvidenceSpan).where(EvidenceSpan.finding_id == action_id)).all()
        return {row.id: {key: getattr(row, key) for key in keys} for row in rows}


def _current_run(meeting_id: str) -> AnalysisRun:
    with inspection_session_factory()() as db:
        runs = db.scalars(
            select(AnalysisRun).where(
                AnalysisRun.meeting_id == meeting_id, AnalysisRun.is_current.is_(True)
            )
        ).all()
        assert len(runs) == 1
        return runs[0]


def _current_revision_id(meeting_id: str) -> str:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(TranscriptRevision.id).where(
                TranscriptRevision.meeting_id == meeting_id,
                TranscriptRevision.is_current.is_(True),
            )
        )


def _audit_ids(workspace_id: str) -> set[str]:
    with inspection_session_factory()() as db:
        return set(db.scalars(select(AuditEvent.id).where(AuditEvent.workspace_id == workspace_id)))


def _audit_rows(ids: set[str]) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.id.in_(ids))
                .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
            )
        )


def _correct(persona: _Persona, meeting_id: str) -> str:
    transcript = persona.get(f"/api/v1/meetings/{meeting_id}/transcript")
    assert transcript.status_code == 200, transcript.text
    body = transcript.json()
    response = persona.post(
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        json={
            "base_revision_id": body["revision"]["id"],
            "reason": "fix the wording",
            "edits": [{"segment_id": body["segments"][0]["id"], "text": CORRECTED_TEXT}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["revision"]["id"]


def _rerun(crew: Crew, meeting_id: str) -> str:
    with inspection_session_factory()() as db:
        job = db.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.meeting_id == meeting_id)
            .order_by(ProcessingJob.created_at.desc())
        )
        assert job is not None and job.state == JobState.COMPLETED
        job.state = JobState.QUEUED
        job.stage = "queued"
        job.completed_at = None
        db.commit()
        job_id = job.id
    _process(crew, job_id)
    return job_id


def _reanalyse(crew: Crew, path: str, meeting_id: str) -> AnalysisRun:
    """Run one caller of ``persist_analysis``; return the new current run after
    proving it is new and read the new current revision."""
    previous_run = _current_run(meeting_id)
    previous_revision = _current_revision_id(meeting_id)
    if path == "recompute":
        revision_id = _correct(crew.member, meeting_id)
        run = _current_run(meeting_id)
        assert run.job_id is None
    else:
        job_id = _rerun(crew, meeting_id)
        revision_id = _current_revision_id(meeting_id)
        run = _current_run(meeting_id)
        assert run.job_id == job_id
    assert revision_id != previous_revision
    assert run.id != previous_run.id
    assert run.transcript_revision_id == revision_id
    return run


def _written_by(run: AnalysisRun, rows: dict[str, dict]) -> dict[str, dict]:
    return {
        action_id: row
        for action_id, row in rows.items()
        if row["created_by"] == "ai" and row["analysis_run_id"] == run.id
    }


def _assigned_and_unassigned(crew: Crew, meeting_id: str) -> tuple[str, str]:
    ai = _ai_ids(meeting_id)
    assert len(ai) == ACTIONS_PER_RUN
    assigned, unassigned = ai
    _assign(crew, meeting_id, assigned)
    rows = _actions(meeting_id)
    assert rows[assigned]["assignee_user_id"] == crew.member.user_id
    assert rows[unassigned]["assignee_user_id"] is None
    assert rows[unassigned]["assignee_text"] == OWNER_TEXT
    return assigned, unassigned


@pytest.mark.parametrize("path", PATHS)
def test_a1_a2_voicemail_reanalysis_keeps_an_owner_assigned_ai_action_unchanged(crew, path):
    meeting_id = _processed(crew, voicemail=True)
    assigned, unassigned = _assigned_and_unassigned(crew, meeting_id)
    before = _actions(meeting_id)
    evidence = _evidence_of(assigned)
    assert evidence
    audit_before = _audit_ids(crew.owner.workspace_id)

    run = _reanalyse(crew, path, meeting_id)

    after = _actions(meeting_id)
    written = _written_by(run, after)
    assert len(written) == ACTIONS_PER_RUN
    assert unassigned not in after
    assert assigned in after, "the owner's assignment was deleted by the re-analysis"
    assert after[assigned] == before[assigned]
    assert after[assigned]["assignee_user_id"] == crew.member.user_id
    assert after[assigned]["analysis_run_id"] != run.id
    assert _evidence_of(assigned) == evidence
    assert {action_id for action_id, row in after.items() if row["created_by"] == "ai"} == {
        assigned,
        *written,
    }
    new_events = _audit_rows(_audit_ids(crew.owner.workspace_id) - audit_before)
    assert [event.event_type for event in new_events if event.target_id == assigned] == []
    assert not [event for event in new_events if event.event_type.startswith("action_item.")]


@pytest.mark.parametrize("path", PATHS)
def test_a1_a2_an_assigned_ai_action_survives_two_reanalyses_in_a_row(crew, path):
    meeting_id = _processed(crew, voicemail=True)
    assigned, _ = _assigned_and_unassigned(crew, meeting_id)
    before = _actions(meeting_id)[assigned]

    first = _reanalyse(crew, path, meeting_id)
    second = _reanalyse(crew, "process_job" if path == "recompute" else "recompute", meeting_id)

    after = _actions(meeting_id)
    assert first.id != second.id
    assert len(_written_by(second, after)) == ACTIONS_PER_RUN
    assert not _written_by(first, after)
    assert after.get(assigned) == before


@pytest.mark.parametrize("path", PATHS)
def test_a3_voicemail_reanalysis_still_replaces_the_unassigned_ai_actions(crew, path):
    meeting_id = _processed(crew, voicemail=True)
    _, unassigned = _assigned_and_unassigned(crew, meeting_id)
    old_run = _current_run(meeting_id)

    run = _reanalyse(crew, path, meeting_id)

    after = _actions(meeting_id)
    assert unassigned not in after
    written = _written_by(run, after)
    assert len(written) == ACTIONS_PER_RUN
    assert unassigned not in written
    for row in written.values():
        assert row["assignee_user_id"] is None
        assert row["assignee_text"] == OWNER_TEXT
        assert row["source_segment_id"] is not None
    assert not [
        row
        for row in after.values()
        if row["created_by"] == "ai"
        and row["assignee_user_id"] is None
        and row["analysis_run_id"] != run.id
    ]
    assert run.id != old_run.id


@pytest.mark.parametrize("path", PATHS)
def test_a3_a_voicemail_meeting_without_any_assignment_replaces_every_ai_action(crew, path):
    meeting_id = _processed(crew, voicemail=True)
    old_ai = set(_ai_ids(meeting_id))
    assert len(old_ai) == ACTIONS_PER_RUN

    run = _reanalyse(crew, path, meeting_id)

    after = _actions(meeting_id)
    assert not old_ai & set(after)
    assert set(_ai_ids(meeting_id)) == set(_written_by(run, after))
    assert len(_written_by(run, after)) == ACTIONS_PER_RUN


@pytest.mark.parametrize("path", PATHS)
def test_a4_non_voicemail_reanalysis_still_replaces_an_assigned_ai_action(crew, path):
    meeting_id = _processed(crew, voicemail=False)
    assigned, unassigned = _assigned_and_unassigned(crew, meeting_id)
    person = _user_action(crew, meeting_id)
    before = _actions(meeting_id)

    run = _reanalyse(crew, path, meeting_id)

    after = _actions(meeting_id)
    assert assigned not in after
    assert unassigned not in after
    written = _written_by(run, after)
    assert len(written) == ACTIONS_PER_RUN
    assert set(_ai_ids(meeting_id)) == set(written)
    assert after[person] == before[person]


def test_a4_a_voicemail_row_of_another_workspace_does_not_make_a_voicemail_meeting(
    crew, client, email_outbox
):
    meeting_id = _processed(crew, voicemail=False)
    assigned, unassigned = _assigned_and_unassigned(crew, meeting_id)
    other = stranger(client, email_outbox, "hom380-other-tenant@example.com")
    assert other.workspace_id != crew.owner.workspace_id
    with inspection_session_factory()() as db:
        db.add(
            VoicemailMessage(
                workspace_id=other.workspace_id,
                meeting_id=meeting_id,
                external_message_id="vm380-foreign",
                received_at=datetime.now(UTC),
            )
        )
        db.commit()
    assert _is_voicemail(meeting_id, crew.owner.workspace_id) is False

    run = _reanalyse(crew, "recompute", meeting_id)

    after = _actions(meeting_id)
    assert assigned not in after
    assert unassigned not in after
    assert set(_ai_ids(meeting_id)) == set(_written_by(run, after))


@pytest.mark.parametrize("path", PATHS)
def test_a5_rules_and_user_actions_are_never_removed_by_a_reanalysis(crew, path):
    meeting_id = _processed(crew, voicemail=True)
    _assigned_and_unassigned(crew, meeting_id)
    rules = _rules_action(crew, meeting_id)
    person = _user_action(crew, meeting_id)
    before = _actions(meeting_id)
    kept = {action_id: row for action_id, row in before.items() if row["created_by"] != "ai"}
    assert {rules, person} <= set(kept)
    assert before[rules]["assignee_user_id"] is None
    assert before[person]["assignee_user_id"] is None

    run = _reanalyse(crew, path, meeting_id)

    after = _actions(meeting_id)
    assert len(_written_by(run, after)) == ACTIONS_PER_RUN
    for action_id, row in kept.items():
        assert after.get(action_id) == row


def _recompute_events(crew: Crew, meeting_id: str) -> tuple[AnalysisRun, list[AuditEvent]]:
    audit_before = _audit_ids(crew.owner.workspace_id)
    run = _reanalyse(crew, "recompute", meeting_id)
    return run, _audit_rows(_audit_ids(crew.owner.workspace_id) - audit_before)


def test_a6_the_recompute_audit_row_keeps_its_shape_and_counts_only_new_ai_actions(crew):
    plain = _processed(crew, voicemail=False)
    voice = _processed(crew, voicemail=True)
    _assigned_and_unassigned(crew, plain)
    _assigned_and_unassigned(crew, voice)

    plain_run, plain_events = _recompute_events(crew, plain)
    voice_run, voice_events = _recompute_events(crew, voice)

    completed = AuditEventType.MEETING_RECOMPUTE_COMPLETED.value
    assert sorted(event.event_type for event in voice_events) == sorted(
        event.event_type for event in plain_events
    )
    assert not [event for event in voice_events if event.event_type.startswith("action_item.")]
    known = {member.value for member in AuditEventType}
    assert {event.event_type for event in voice_events} <= known
    (plain_completed,) = [event for event in plain_events if event.event_type == completed]
    (voice_completed,) = [event for event in voice_events if event.event_type == completed]
    assert set(voice_completed.details) == set(plain_completed.details)
    assert voice_completed.details["analysis_run_id"] == voice_run.id
    assert voice_completed.details["outcome"] == "completed"
    voice_written = _written_by(voice_run, _actions(voice))
    plain_written = _written_by(plain_run, _actions(plain))
    assert voice_completed.details["action_item_count"] == len(voice_written) == ACTIONS_PER_RUN
    assert plain_completed.details["action_item_count"] == len(plain_written) == ACTIONS_PER_RUN
