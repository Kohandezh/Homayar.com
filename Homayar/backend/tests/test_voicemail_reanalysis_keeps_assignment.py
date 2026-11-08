"""HOM-380: a re-analysis keeps a person-set assignment on a voicemail meeting.

``persist_analysis`` replaced every AI action of the meeting.  Two callers
reach it: the full processing job and the recompute after a transcript
correction.  A correction needs only ``meeting:update``, so a member can
trigger a recompute.  On a voicemail meeting an owner had set the
``assignee_user_id`` of an AI action through the actions route (ADR 0054);
the re-analysis then deleted that action, with no check and no
``action_item.assignee_changed`` audit row.

Now (SPEC A1 to A6):

* on a voicemail meeting, a re-analysis keeps every AI action whose
  ``assignee_user_id`` is not null, every column unchanged: after a member's
  correction (A1) and after a full job re-run (A2);
* on the same meeting, AI actions with a null ``assignee_user_id`` are
  removed and the new run's AI actions are written, as today (A3);
* on a meeting no ``VoicemailMessage`` row names, an assigned AI action is
  still removed and replaced (A4, ADR 0054 R8 / P2);
* ``voicemail_rules`` and ``user`` actions are never removed (A5);
* the recompute audit keeps its shape: ``action_item_count`` counts only the
  new AI actions, and no ``action_item.assignee_changed`` row is written,
  because no assigned action is removed (A6).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from backend.database.saas_models import (
    ActionItem,
    AuditEvent,
    JobState,
    Membership,
    ProcessingJob,
    User,
    WorkspaceRole,
)
from backend.jobs import recompute
from backend.jobs.processor import process_job
from backend.providers.schemas import ActionItem as ProviderActionItem
from backend.services.audit_event_types import AuditEventType
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_analysis_recompute import (
    CORRECTED_TEXT,
    ORIGINAL_TEXT,
    _ActionIntelligence,
    _bundle,
    _correct,
    _current_run,
    _processed_meeting,
    _snapshot,
)
from backend.tests.test_insights_shares_api import _write_headers
from backend.voicemail.models import VoicemailMessage

FOLLOW_UP = "Follow up"
CALL_BACK = "Call back"


class _TwoActionIntelligence(_ActionIntelligence):
    """The mock analysis plus two AI action items grounded in the first turn."""

    async def analyze(self, request):
        result = await super().analyze(request)
        second = ProviderActionItem(
            description=f"{CALL_BACK}: {request.turns[0].text}", source_turn_indexes=[0]
        )
        return result.model_copy(update={"action_items": [*result.action_items, second]})


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
    holder = {"intelligence": _TwoActionIntelligence(allow_mock=True)}
    monkeypatch.setattr(
        recompute, "create_provider_bundle", lambda _settings: _bundle(holder["intelligence"])
    )
    return holder


# --- helpers ------------------------------------------------------------------------


def _link_voicemail(workspace_id: str, meeting_id: str) -> str:
    """Make the meeting a voicemail meeting (the ADR 0054 test, HOM-375)."""
    with inspection_session_factory()() as db:
        voicemail = VoicemailMessage(
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            external_message_id=f"vm-hom380-{uuid.uuid4().hex}",
            received_at=datetime.now(UTC),
        )
        db.add(voicemail)
        db.commit()
        return voicemail.id


def _member(client, email_outbox, workspace_id: str) -> tuple[dict, str]:
    """A member of the workspace: ``meeting:update``, no ``meeting:attribute``."""
    credentials = register_user(
        client, email_outbox, email=f"hom380-{uuid.uuid4().hex[:12]}@example.com"
    )
    with inspection_session_factory()() as db:
        user_id = db.scalar(select(User.id).where(User.email == credentials["email"]))
        assert user_id is not None
        db.add(Membership(workspace_id=workspace_id, user_id=user_id, role=WorkspaceRole.MEMBER))
        db.commit()
    return credentials, user_id


def _owner_assigns(
    client, credentials, workspace_id: str, meeting_id: str, action_id: str, member_user_id: str
) -> None:
    """The owner sets an AI action's assignee through the actions route."""
    login_user(client, credentials)
    response = client.patch(
        f"/api/v1/meetings/{meeting_id}/actions/{action_id}",
        headers=_write_headers(client, workspace_id),
        json={"assignee_user_id": member_user_id},
    )
    assert response.status_code == 200, response.text
    assert response.json()["assignee_user_id"] == member_user_id


def _person_action(workspace_id: str, meeting_id: str, *, created_by: str) -> str:
    """An action on the meeting that no re-analysis owns."""
    with inspection_session_factory()() as db:
        action = ActionItem(
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            description="An action no re-analysis owns",
            created_by=created_by,
        )
        db.add(action)
        db.commit()
        return action.id


def _ai_actions(meeting_id: str) -> dict[str, dict]:
    return {
        row_id: row
        for row_id, row in _snapshot(ActionItem, meeting_id).items()
        if row["created_by"] == "ai"
    }


def _two_ai_actions(meeting_id: str, text: str) -> tuple[str, str]:
    """The ids of the run's two AI actions: the follow-up one and the other."""
    actions = _ai_actions(meeting_id)
    assert len(actions) == 2
    follow_up = next(
        row_id for row_id, row in actions.items() if row["description"] == f"{FOLLOW_UP}: {text}"
    )
    other = next(
        row_id for row_id, row in actions.items() if row["description"] == f"{CALL_BACK}: {text}"
    )
    return follow_up, other


def _written_by_the_run(meeting_id: str, run_id: str, *, kept: str) -> dict[str, dict]:
    """The new AI actions of the run: every AI action the re-analysis wrote."""
    return {
        row_id: row
        for row_id, row in _ai_actions(meeting_id).items()
        if row_id != kept and row["analysis_run_id"] == run_id
    }


def _requeue_for_reprocessing(meeting_id: str) -> str:
    """Put the completed job back in the queue as an operator would for a re-run."""
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
        return job.id


def _recompute_completed(meeting_id: str) -> AuditEvent:
    with inspection_session_factory()() as db:
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.target_id == meeting_id,
                AuditEvent.event_type == AuditEventType.MEETING_RECOMPUTE_COMPLETED.value,
            )
        )
        assert event is not None
        return event


def _assignee_changes(workspace_id: str) -> list[str]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent.id).where(
                    AuditEvent.workspace_id == workspace_id,
                    AuditEvent.event_type == AuditEventType.ACTION_ITEM_ASSIGNEE_CHANGED.value,
                )
            )
        )


# --- A1, A3, A5, A6: the recompute after a member's correction ----------------------


def test_a1_a_recompute_keeps_an_assigned_ai_action_on_a_voicemail_meeting(
    client, email_outbox, settings, monkeypatch, analysis_provider
):
    credentials, workspace_id, meeting_id = _processed_meeting(
        client,
        email_outbox,
        settings,
        monkeypatch,
        key="hom380-recompute",
        intelligence=_TwoActionIntelligence(allow_mock=True),
    )
    _link_voicemail(workspace_id, meeting_id)
    member_credentials, member_id = _member(client, email_outbox, workspace_id)
    rules_action = _person_action(workspace_id, meeting_id, created_by="voicemail_rules")
    user_action = _person_action(workspace_id, meeting_id, created_by="user")
    assigned_id, unassigned_id = _two_ai_actions(meeting_id, ORIGINAL_TEXT)
    _owner_assigns(client, credentials, workspace_id, meeting_id, assigned_id, member_id)

    before = _snapshot(ActionItem, meeting_id)
    assert before[assigned_id]["assignee_user_id"] == member_id
    assert before[unassigned_id]["assignee_user_id"] is None
    assignee_rows = _assignee_changes(workspace_id)
    assert len(assignee_rows) == 1  # the owner's patch, nothing else

    login_user(client, member_credentials)
    corrected = _correct(client, workspace_id, meeting_id)

    # The recompute ran: the new run is current and reads the corrected revision.
    run = _current_run(meeting_id)
    assert run.transcript_revision_id == corrected["id"]

    after = _snapshot(ActionItem, meeting_id)

    # A1: the owner's assignment survives, every column unchanged.
    assert after[assigned_id] == before[assigned_id]

    # A3: the unassigned AI action was replaced by the new run's actions.
    assert unassigned_id not in after
    written = _written_by_the_run(meeting_id, run.id, kept=assigned_id)
    assert {row["description"] for row in written.values()} == {
        f"{FOLLOW_UP}: {CORRECTED_TEXT}",
        f"{CALL_BACK}: {CORRECTED_TEXT}",
    }
    assert all(row["assignee_user_id"] is None for row in written.values())

    # A5: voicemail_rules and user actions are never removed.
    assert after[rules_action] == before[rules_action]
    assert after[user_action] == before[user_action]

    # A6: no assignee_changed row by the re-analysis; the audit counts only
    # the new AI actions.
    assert _assignee_changes(workspace_id) == assignee_rows
    assert _recompute_completed(meeting_id).details["action_item_count"] == 2


# --- A2: the full processing job re-run --------------------------------------------


def test_a2_a_full_job_rerun_keeps_an_assigned_ai_action_on_a_voicemail_meeting(
    client, email_outbox, settings, monkeypatch
):
    credentials, workspace_id, meeting_id = _processed_meeting(
        client,
        email_outbox,
        settings,
        monkeypatch,
        key="hom380-full-job",
        intelligence=_TwoActionIntelligence(allow_mock=True),
    )
    _link_voicemail(workspace_id, meeting_id)
    member_credentials, member_id = _member(client, email_outbox, workspace_id)
    assigned_id, unassigned_id = _two_ai_actions(meeting_id, ORIGINAL_TEXT)
    _owner_assigns(client, credentials, workspace_id, meeting_id, assigned_id, member_id)

    before = _snapshot(ActionItem, meeting_id)
    job_id = _requeue_for_reprocessing(meeting_id)
    result = process_job(
        job_id,
        worker_id="test-hom380",
        settings=settings,
        providers=_bundle(_TwoActionIntelligence(allow_mock=True)),
    )
    assert result["status"] == "completed"

    # The full job ran: the new current run belongs to the job.
    run = _current_run(meeting_id)
    assert run.job_id == job_id

    after = _snapshot(ActionItem, meeting_id)

    # A2: the owner's assignment survives, every column unchanged.
    assert after[assigned_id] == before[assigned_id]

    # The unassigned AI action was replaced by the new run's actions.
    assert unassigned_id not in after
    written = _written_by_the_run(meeting_id, run.id, kept=assigned_id)
    assert {row["description"] for row in written.values()} == {
        f"{FOLLOW_UP}: {ORIGINAL_TEXT}",
        f"{CALL_BACK}: {ORIGINAL_TEXT}",
    }
    assert all(row["assignee_user_id"] is None for row in written.values())


# --- A4: an ordinary meeting keeps today's behaviour --------------------------------


def test_a4_on_an_ordinary_meeting_an_assigned_ai_action_is_still_replaced(
    client, email_outbox, settings, monkeypatch, analysis_provider
):
    credentials, workspace_id, meeting_id = _processed_meeting(
        client,
        email_outbox,
        settings,
        monkeypatch,
        key="hom380-ordinary",
        intelligence=_TwoActionIntelligence(allow_mock=True),
    )
    member_credentials, member_id = _member(client, email_outbox, workspace_id)
    assigned_id, unassigned_id = _two_ai_actions(meeting_id, ORIGINAL_TEXT)
    _owner_assigns(client, credentials, workspace_id, meeting_id, assigned_id, member_id)

    before = _snapshot(ActionItem, meeting_id)
    assert before[assigned_id]["assignee_user_id"] == member_id

    login_user(client, member_credentials)
    corrected = _correct(client, workspace_id, meeting_id)

    run = _current_run(meeting_id)
    assert run.transcript_revision_id == corrected["id"]

    after = _snapshot(ActionItem, meeting_id)

    # A4: replaced and rewritten, as today (ADR 0054 R8 / P2).
    assert assigned_id not in after
    assert unassigned_id not in after
    written = _written_by_the_run(meeting_id, run.id, kept=assigned_id)
    assert {row["description"] for row in written.values()} == {
        f"{FOLLOW_UP}: {CORRECTED_TEXT}",
        f"{CALL_BACK}: {CORRECTED_TEXT}",
    }
    assert all(row["assignee_user_id"] is None for row in written.values())
