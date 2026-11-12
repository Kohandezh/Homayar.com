"""HOM-75 PR 2: the calibration batch API, blinded end to end.

Every SPEC deny-case runs next to its allow-control, and each denial asserts the code
of the check that must refuse it, in the SPEC's order.  The blinding is the core:
before close no response shows another participant's answers, a participant sees no
adjudication, and no calibration response holds a machine outcome, reason or
evidence.  The audit details never hold an outcome, a note or the title.

The scorecard results are real (a published version, the engine's own output stored
through ``record_result``), so the criteria the report counts are the stored ones.  A
call scored with ``DEFINITION`` has four reviewable criteria (``greeting`` passed,
``fee_explained`` failed, ``closing`` passed, ``off_topic`` failed with
``na_allowed``) and one not reviewable (``agenda``, for meetings only).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select, update

from backend.conversation_checks import finish_checks, plan_checks
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    AuditEvent,
    CalibrationAnswer,
    CalibrationParticipant,
    Meeting,
    MeetingStatus,
    TranscriptRevision,
    TranscriptSource,
)
from backend.database.tenant_context import bind_tenant_context
from backend.scorecards import ConversationFacts, plan_scorecard
from backend.security.authorization import API_KEY_SCOPES
from backend.services.scorecard_result_service import record_result
from backend.services.scorecard_service import publish_version
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_review_decisions_api import (
    DEFINITION,
    SEGMENTS,
    TURNS,
    _criterion,
)
from backend.tests.test_review_queue_access import (
    Queue,
    build_queue,
    code,
    stranger,
)

BATCHES = "/api/v1/calibration-batches"
NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
REVIEWABLE = ("greeting", "fee_explained", "closing", "off_topic")
NOT_REVIEWABLE = "agenda"
# A definition whose only criterion is for meetings, so a call's result has no
# reviewable criterion at all.
MEETING_ONLY = {
    "name": "Meeting only",
    "pass_threshold": 100,
    "criteria": [
        _criterion("agenda", 100, "agenda", applies_to={"conversation_types": ["meeting"]})
    ],
}

BATCH_KEYS = {
    "id",
    "title",
    "status",
    "revision",
    "created_by_user_id",
    "created_at",
    "closed_at",
    "cancelled_at",
    "participants",
    "examples",
    "my_answers",
    "adjudications",
    "progress",
    "answers",
}
SUMMARY_KEYS = {
    "id",
    "title",
    "status",
    "revision",
    "created_at",
    "closed_at",
    "cancelled_at",
    "example_count",
    "participant_count",
}
CRITERION_KEYS = {"criterion_id", "position", "weight", "na_allowed", "reviewable"}
EXAMPLE_KEYS = {"id", "position", "meeting_id", "scorecard_result_id", "criteria"}
PROGRESS_KEYS = {"participant_id", "user_id", "answered", "expected"}
PARTICIPANT_KEYS = {"id", "user_id"}
# No calibration response before the report may hold any of these machine fields.
FORBIDDEN_KEYS = {"reason", "evidence", "decided_by", "machine", "review_required"}
OUTCOME_WORDS = {"passed", "failed", "not_applicable", "insufficient_evidence"}


@pytest.fixture
def queue(client, email_outbox, monkeypatch) -> Queue:
    return build_queue(client, email_outbox, monkeypatch)


# --- the scored fixture -------------------------------------------------------------------


def _app_session(workspace_id: str):
    db = get_session_factory()()
    bind_tenant_context(db, workspace_id=workspace_id)
    return db


@dataclass
class Scored:
    """A real stored machine result of one conversation."""

    meeting_id: str
    result_id: str


def _scored(
    queue: Queue,
    *,
    definition: dict[str, Any] = DEFINITION,
    team_id: str | None = None,
    agent_user_id: str | None = None,
    direction: str | None = "inbound",
    meeting_id: str | None = None,
    key: str | None = None,
) -> Scored:
    """Score one conversation of the queue's workspace and store the result.

    With ``meeting_id`` the conversation keeps its current team and agent; otherwise a
    new one is made on ``team_id`` (team A by default) with the given agent.
    """
    key = key or f"calibration_{uuid.uuid4().hex[:12]}"
    if meeting_id is None:
        with inspection_session_factory()() as db:
            meeting = Meeting(
                workspace_id=queue.workspace_id,
                created_by_user_id=queue.owner.user_id,
                title="Scored calibration call",
                status=MeetingStatus.COMPLETED,
                duration_seconds=30,
                conversation_type="call",
                direction=direction,
                team_id=team_id or queue.team_a,
                agent_user_id=agent_user_id,
            )
            db.add(meeting)
            db.flush()
            revision = TranscriptRevision(
                workspace_id=queue.workspace_id,
                meeting_id=meeting.id,
                revision_number=1,
                source=TranscriptSource.ASR,
                reason="processing",
                language="en",
                segment_count=SEGMENTS,
                is_current=True,
            )
            db.add(revision)
            db.commit()
            meeting_id, revision_id = meeting.id, revision.id
    else:
        with inspection_session_factory()() as db:
            revision_id = db.scalar(
                select(TranscriptRevision.id).where(
                    TranscriptRevision.meeting_id == meeting_id,
                    TranscriptRevision.is_current.is_(True),
                )
            )
    facts = ConversationFacts("call", direction)
    run = finish_checks(plan_checks(plan_scorecard(definition, facts), TURNS, language="en"))
    with _app_session(queue.workspace_id) as db:
        version = publish_version(
            db,
            workspace_id=queue.workspace_id,
            scorecard_key=key,
            definition=definition,
            expected_version=0,
            reason="publish",
            actor_user_id=queue.owner.user_id,
            now=NOW,
        )
        row, _ = record_result(
            db,
            workspace_id=queue.workspace_id,
            meeting_id=meeting_id,
            scorecard_version_id=version.id,
            check_run=run,
            transcript_revision_id=revision_id,
            now=NOW,
        )
        db.commit()
        return Scored(meeting_id=meeting_id, result_id=row.id)


# --- the API helpers ----------------------------------------------------------------------


def create_batch(persona, result_ids: list[str], participant_ids: list[str], **extra):
    return persona.post(
        BATCHES,
        json={
            "title": "Week 48 calibration",
            "scorecard_result_ids": result_ids,
            "participant_user_ids": participant_ids,
            **extra,
        },
    )


def created_batch(persona, result_ids: list[str], participant_ids: list[str]) -> dict:
    response = create_batch(persona, result_ids, participant_ids)
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == BATCH_KEYS, sorted(set(body) ^ BATCH_KEYS)
    return body


def view(persona, batch_id: str):
    return persona.get(f"{BATCHES}/{batch_id}")


def viewed(persona, batch_id: str) -> dict:
    response = view(persona, batch_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == BATCH_KEYS
    return body


def answer(persona, batch: dict, example_id: str, criterion_id: str, outcome: str):
    return persona.post(
        f"{BATCHES}/{batch['id']}/examples/{example_id}/criteria/{criterion_id}/answer",
        json={"outcome": outcome},
    )


def adjudicate(
    persona, batch: dict, example_id: str, criterion_id: str, outcome: str, note: str | None = None
):
    body: dict[str, Any] = {"outcome": outcome}
    if note is not None:
        body["note"] = note
    return persona.post(
        f"{BATCHES}/{batch['id']}/examples/{example_id}/criteria/{criterion_id}/adjudication",
        json=body,
    )


def close(persona, batch: dict, expected_revision: int | None = None):
    return persona.post(
        f"{BATCHES}/{batch['id']}/close",
        json={
            "expected_revision": batch["revision"]
            if expected_revision is None
            else expected_revision
        },
    )


def cancel(persona, batch: dict, expected_revision: int | None = None):
    return persona.post(
        f"{BATCHES}/{batch['id']}/cancel",
        json={
            "expected_revision": batch["revision"]
            if expected_revision is None
            else expected_revision
        },
    )


def reported(persona, batch_id: str) -> dict:
    response = persona.get(f"{BATCHES}/{batch_id}/report")
    assert response.status_code == 200, response.text
    return response.json()


def example_of(batch: dict, position: int = 0) -> dict:
    return batch["examples"][position]


def criterion_ids(batch: dict, example: dict) -> list[str]:
    return [row["criterion_id"] for row in example["criteria"]]


def adjudicate_all(persona, batch: dict, outcome_by_criterion: dict[str, str]) -> None:
    for example in batch["examples"]:
        for criterion in example["criteria"]:
            if not criterion["reviewable"]:
                continue
            response = adjudicate(
                persona,
                batch,
                example["id"],
                criterion["criterion_id"],
                outcome_by_criterion[criterion["criterion_id"]],
            )
            assert response.status_code == 200, response.text


def answer_all(persona, batch: dict, outcome_by_criterion: dict[str, str]) -> dict:
    """Answer each reviewable criterion named in ``outcome_by_criterion`` on every
    example (the others stay unanswered); the refreshed view."""
    for example in batch["examples"]:
        for criterion in example["criteria"]:
            if not criterion["reviewable"] or criterion["criterion_id"] not in outcome_by_criterion:
                continue
            response = answer(
                persona,
                batch,
                example["id"],
                criterion["criterion_id"],
                outcome_by_criterion[criterion["criterion_id"]],
            )
            assert response.status_code == 200, response.text
            batch = response.json()
    return batch


def calibration_events(workspace_id: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.workspace_id == workspace_id,
                    AuditEvent.event_type.like("calibration%"),
                )
                .order_by(AuditEvent.occurred_at.asc())
            )
        )


def answer_rows(batch_id: str) -> list[CalibrationAnswer]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(select(CalibrationAnswer).where(CalibrationAnswer.batch_id == batch_id))
        )


def no_machine_fields(value: Any) -> None:
    """No calibration response holds a machine field or machine free text."""
    if isinstance(value, dict):
        assert not (set(value) & FORBIDDEN_KEYS), sorted(set(value) & FORBIDDEN_KEYS)
        for item in value.values():
            no_machine_fields(item)
    elif isinstance(value, list):
        for item in value:
            no_machine_fields(item)


# --- AC1: create --------------------------------------------------------------------------


def test_create_builds_the_examples_in_the_given_order(queue):
    first = _scored(queue)
    second = _scored(queue)
    batch = created_batch(queue.owner, [second.result_id, first.result_id], [queue.sup.user_id])
    assert batch["status"] == "open"
    assert batch["revision"] == 1
    assert batch["title"] == "Week 48 calibration"
    assert batch["created_by_user_id"] == queue.owner.user_id
    assert batch["closed_at"] is None and batch["cancelled_at"] is None
    assert [(row["position"], row["meeting_id"]) for row in batch["examples"]] == [
        (0, second.meeting_id),
        (1, first.meeting_id),
    ]
    for example in batch["examples"]:
        assert set(example) == EXAMPLE_KEYS
        assert {row["criterion_id"] for row in example["criteria"]} == set(REVIEWABLE) | {
            NOT_REVIEWABLE
        }
        for criterion in example["criteria"]:
            assert set(criterion) == CRITERION_KEYS
    assert [row["user_id"] for row in batch["participants"]] == [queue.sup.user_id]
    assert all(set(row) == PARTICIPANT_KEYS for row in batch["participants"])
    assert batch["my_answers"] == []  # the creator is not a participant
    assert batch["adjudications"] == []  # nothing is adjudicated yet
    assert batch["answers"] == []
    # ``expected`` counts the reviewable criteria over all examples: 4 + 4.
    assert batch["progress"] == [
        {"participant_id": row["id"], "user_id": queue.sup.user_id, "answered": 0, "expected": 8}
        for row in batch["participants"]
    ]
    no_machine_fields(batch)


def test_create_refuses_non_admins(queue):
    scored = _scored(queue)
    # A member supervisor holds review:manage, but only a review admin may create.
    refused = create_batch(queue.sup, [scored.result_id], [queue.sup.user_id])
    assert refused.status_code == 403 and code(refused) == "calibration_admin_required"
    refused = create_batch(queue.agent, [scored.result_id], [queue.agent.user_id])
    assert refused.status_code == 403 and code(refused) == "calibration_admin_required"
    # A viewer does not even hold review:manage.
    refused = create_batch(queue.viewer, [scored.result_id], [queue.sup.user_id])
    assert refused.status_code == 403 and code(refused) == "permission_denied"
    # Allow-control: the owner creates the same batch.
    assert create_batch(queue.owner, [scored.result_id], [queue.sup.user_id]).status_code == 201


def test_create_refuses_bad_results_with_their_codes(queue, client, email_outbox):
    scored = _scored(queue)
    # An unknown id.
    unknown = str(uuid.uuid4())
    refused = create_batch(queue.owner, [unknown], [queue.sup.user_id])
    assert refused.status_code == 422 and code(refused) == "calibration_result_invalid"
    assert refused.json()["error"]["details"] == {"scorecard_result_id": unknown}
    # A result of another workspace.
    foreign = _scored_for(stranger(client, email_outbox, "calibration-foreign@example.com"))
    refused = create_batch(queue.owner, [foreign.result_id], [queue.sup.user_id])
    assert refused.status_code == 422 and code(refused) == "calibration_result_invalid"
    # A deleted conversation.
    with inspection_session_factory()() as db:
        db.execute(update(Meeting).where(Meeting.id == scored.meeting_id).values(deleted_at=NOW))
        db.commit()
    refused = create_batch(queue.owner, [scored.result_id], [queue.sup.user_id])
    assert refused.status_code == 422 and code(refused) == "calibration_result_invalid"
    # Allow-control: a live result of the workspace.
    fresh = _scored(queue)
    assert create_batch(queue.owner, [fresh.result_id], [queue.sup.user_id]).status_code == 201


def _scored_for(persona) -> Scored:
    """A stored result in another persona's own workspace (no queue needed)."""

    key = f"calibration_foreign_{uuid.uuid4().hex[:8]}"
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=persona.workspace_id,
            created_by_user_id=persona.user_id,
            title="Foreign call",
            status=MeetingStatus.COMPLETED,
            duration_seconds=30,
            conversation_type="call",
            direction="inbound",
        )
        db.add(meeting)
        db.flush()
        revision = TranscriptRevision(
            workspace_id=persona.workspace_id,
            meeting_id=meeting.id,
            revision_number=1,
            source=TranscriptSource.ASR,
            reason="processing",
            language="en",
            segment_count=SEGMENTS,
            is_current=True,
        )
        db.add(revision)
        db.commit()
        meeting_id, revision_id = meeting.id, revision.id
    facts = ConversationFacts("call", "inbound")
    run = finish_checks(plan_checks(plan_scorecard(DEFINITION, facts), TURNS, language="en"))
    with _app_session(persona.workspace_id) as db:
        version = publish_version(
            db,
            workspace_id=persona.workspace_id,
            scorecard_key=key,
            definition=DEFINITION,
            expected_version=0,
            reason="publish",
            actor_user_id=persona.user_id,
            now=NOW,
        )
        row, _ = record_result(
            db,
            workspace_id=persona.workspace_id,
            meeting_id=meeting_id,
            scorecard_version_id=version.id,
            check_run=run,
            transcript_revision_id=revision_id,
            now=NOW,
        )
        db.commit()
        return Scored(meeting_id=meeting_id, result_id=row.id)


def test_create_refuses_one_conversation_twice(queue):
    first = _scored(queue)
    second = _scored(queue, meeting_id=first.meeting_id)
    assert second.meeting_id == first.meeting_id
    refused = create_batch(queue.owner, [first.result_id, second.result_id], [queue.sup.user_id])
    assert refused.status_code == 422 and code(refused) == "calibration_duplicate_conversation"
    # Allow-control: one result of the conversation.
    assert create_batch(queue.owner, [second.result_id], [queue.sup.user_id]).status_code == 201


def test_create_refuses_a_result_without_a_reviewable_criterion(queue):
    meeting_only = _scored(queue, definition=MEETING_ONLY)
    refused = create_batch(queue.owner, [meeting_only.result_id], [queue.sup.user_id])
    assert refused.status_code == 422 and code(refused) == "calibration_result_not_reviewable"
    # Allow-control: a result with reviewable criteria.
    scored = _scored(queue)
    assert create_batch(queue.owner, [scored.result_id], [queue.sup.user_id]).status_code == 201


def test_create_refuses_ineligible_participants_with_their_codes(queue):
    on_team_a = _scored(queue, team_id=queue.team_a)
    # The agent of the example's conversation: the agent check comes first.
    with_agent = _scored(queue, team_id=queue.team_a, agent_user_id=queue.agent.user_id)
    refused = create_batch(queue.owner, [with_agent.result_id], [queue.agent.user_id])
    assert refused.status_code == 422 and code(refused) == "calibration_participant_is_agent"
    # A viewer is never eligible.
    refused = create_batch(queue.owner, [on_team_a.result_id], [queue.viewer.user_id])
    assert refused.status_code == 422 and code(refused) == "calibration_participant_ineligible"
    assert refused.json()["error"]["details"] == {
        "user_id": queue.viewer.user_id,
        "meeting_id": on_team_a.meeting_id,
    }
    # A member without an assignment over the conversation's team.
    refused = create_batch(queue.owner, [on_team_a.result_id], [queue.plain.user_id])
    assert refused.status_code == 422 and code(refused) == "calibration_participant_ineligible"
    # A supervisor of another team.
    refused = create_batch(queue.owner, [on_team_a.result_id], [queue.sup_b.user_id])
    assert refused.status_code == 422 and code(refused) == "calibration_participant_ineligible"
    # A supervisor of team A on a batch that also holds a team B example.
    on_team_b = _scored(queue, team_id=queue.team_b)
    refused = create_batch(
        queue.owner, [on_team_a.result_id, on_team_b.result_id], [queue.sup.user_id]
    )
    assert refused.status_code == 422 and code(refused) == "calibration_participant_ineligible"
    assert refused.json()["error"]["details"] == {
        "user_id": queue.sup.user_id,
        "meeting_id": on_team_b.meeting_id,
    }
    # Allow-controls: the supervisor of the team alone; review admins (the creator
    # among them) for both teams; the supervisor of team B on the team B example.
    assert create_batch(queue.owner, [on_team_a.result_id], [queue.sup.user_id]).status_code == 201
    created = create_batch(
        queue.owner,
        [on_team_a.result_id, on_team_b.result_id],
        [queue.admin.user_id, queue.owner.user_id],
    )
    assert created.status_code == 201, created.text
    assert (
        create_batch(queue.owner, [on_team_b.result_id], [queue.sup_b.user_id]).status_code == 201
    )
    # The agent check runs over every example before the team rule: the agent is a
    # member of team A without a supervision, so the first example alone would be
    # ineligible, yet the agent code answers and names the agent's conversation.
    refused = create_batch(
        queue.owner, [on_team_a.result_id, with_agent.result_id], [queue.agent.user_id]
    )
    assert refused.status_code == 422 and code(refused) == "calibration_participant_is_agent"
    assert refused.json()["error"]["details"] == {
        "user_id": queue.agent.user_id,
        "meeting_id": with_agent.meeting_id,
    }
    refused = create_batch(queue.owner, [on_team_a.result_id], [queue.agent.user_id])
    assert refused.status_code == 422 and code(refused) == "calibration_participant_ineligible"


def test_create_refuses_bad_bodies_with_the_usual_validation_error(queue):
    scored = _scored(queue)

    def post(**changes):
        body = {
            "title": "Week 48 calibration",
            "scorecard_result_ids": [scored.result_id],
            "participant_user_ids": [queue.sup.user_id],
            **changes,
        }
        return queue.owner.post(BATCHES, json=body)

    unknown_results = [str(uuid.uuid4()) for _ in range(51)]
    unknown_users = [str(uuid.uuid4()) for _ in range(21)]
    for changes in (
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 201},
        {"title": 7},
        {"scorecard_result_ids": []},
        {"scorecard_result_ids": unknown_results},  # 51
        {"scorecard_result_ids": [scored.result_id, scored.result_id]},
        {"scorecard_result_ids": [7]},
        {"participant_user_ids": []},
        {"participant_user_ids": unknown_users},  # 21
        {"participant_user_ids": [queue.sup.user_id, queue.sup.user_id]},
        {"surprise": True},
    ):
        refused = post(**changes)
        assert refused.status_code == 422 and code(refused) == "validation_error", changes
    assert calibration_events(queue.workspace_id) == []  # no refusal wrote anything
    # The closest accepted bodies.  50 results and 20 participants pass the body
    # rules and reach the database checks (the first unknown id is named).
    refused = post(scorecard_result_ids=unknown_results[:50])
    assert refused.status_code == 422 and code(refused) == "calibration_result_invalid"
    assert refused.json()["error"]["details"] == {"scorecard_result_id": unknown_results[0]}
    refused = post(participant_user_ids=unknown_users[:20])
    assert refused.status_code == 422 and code(refused) == "calibration_participant_ineligible"
    assert refused.json()["error"]["details"]["user_id"] == unknown_users[0]
    for title in ("x", "x" * 200, " padded "):
        created = post(title=title)
        assert created.status_code == 201, (title, created.text)
        assert created.json()["title"] == title  # stored as given


# --- AC2: visibility ----------------------------------------------------------------------


def test_a_batch_is_invisible_to_non_participants_and_strangers(queue, client, email_outbox):
    scored = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id])
    example = example_of(batch)
    foreign = stranger(client, email_outbox, "calibration-stranger@example.com")
    for persona in (queue.plain, foreign):
        # The read routes: 404, absent from the list.
        response = view(persona, batch["id"])
        assert response.status_code == 404 and code(response) == "calibration_batch_not_found"
        response = persona.get(f"{BATCHES}/{batch['id']}/report")
        assert response.status_code == 404 and code(response) == "calibration_batch_not_found"
        # The write routes: the same 404, never a 403.
        response = persona.post(
            f"{BATCHES}/{batch['id']}/examples/{example['id']}/criteria/greeting/answer",
            json={"outcome": "passed"},
        )
        assert response.status_code == 404 and code(response) == "calibration_batch_not_found"
        response = persona.post(
            f"{BATCHES}/{batch['id']}/examples/{example['id']}/criteria/greeting/adjudication",
            json={"outcome": "passed"},
        )
        assert response.status_code == 404 and code(response) == "calibration_batch_not_found"
        for action in ("close", "cancel"):
            response = persona.post(
                f"{BATCHES}/{batch['id']}/{action}", json={"expected_revision": 1}
            )
            assert response.status_code == 404 and code(response) == "calibration_batch_not_found"
    # The list: the admin sees every batch, the participant theirs, others nothing.
    other = created_batch(queue.owner, [_scored(queue).result_id], [queue.admin.user_id])
    listed = queue.owner.get(BATCHES).json()
    assert {item["id"] for item in listed["items"]} == {batch["id"], other["id"]}
    assert all(set(item) == SUMMARY_KEYS for item in listed["items"])
    listed = queue.sup.get(BATCHES).json()
    assert [item["id"] for item in listed["items"]] == [batch["id"]]
    assert queue.plain.get(BATCHES).json()["items"] == []
    no_machine_fields(queue.owner.get(BATCHES).json())


def test_the_list_pages_newest_first_with_a_cursor(queue):
    first = created_batch(queue.owner, [_scored(queue).result_id], [queue.sup.user_id])
    second = created_batch(queue.owner, [_scored(queue).result_id], [queue.sup.user_id])
    third = created_batch(queue.owner, [_scored(queue).result_id], [queue.admin.user_id])
    page = queue.owner.get(BATCHES, params={"limit": 2})
    assert page.status_code == 200, page.text
    body = page.json()
    assert [item["id"] for item in body["items"]] == [third["id"], second["id"]]
    assert body["next_cursor"] is not None
    assert body["items"][0]["example_count"] == 1 and body["items"][0]["participant_count"] == 1
    rest = queue.owner.get(BATCHES, params={"limit": 2, "cursor": body["next_cursor"]}).json()
    assert [item["id"] for item in rest["items"]] == [first["id"]]
    assert rest["next_cursor"] is None
    # A member sees only their own batches, with the same paging.
    mine = queue.sup.get(BATCHES, params={"limit": 1}).json()
    assert [item["id"] for item in mine["items"]] == [second["id"]]
    mine = queue.sup.get(BATCHES, params={"limit": 1, "cursor": mine["next_cursor"]}).json()
    assert [item["id"] for item in mine["items"]] == [first["id"]]
    # A bad cursor and a bad limit.
    refused = queue.owner.get(BATCHES, params={"cursor": "not-a-cursor"})
    assert refused.status_code == 400 and code(refused) == "invalid_cursor"
    for limit in (0, 101):
        refused = queue.owner.get(BATCHES, params={"limit": limit})
        assert refused.status_code == 422, limit


# --- AC3: blinding before close ------------------------------------------------------------


def _blind_batch(queue) -> tuple[dict, dict]:
    """An open batch with sup and the owner as participants, some answers, one
    adjudication."""
    scored = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id, queue.owner.user_id])
    example = example_of(batch)
    assert answer(queue.sup, batch, example["id"], "greeting", "passed").status_code == 200
    assert answer(queue.owner, batch, example["id"], "greeting", "failed").status_code == 200
    assert answer(queue.owner, batch, example["id"], "fee_explained", "failed").status_code == 200
    assert adjudicate(queue.admin, batch, example["id"], "greeting", "failed").status_code == 200
    return viewed(queue.sup, batch["id"]), batch


def test_a_participant_sees_only_their_own_answers_and_no_adjudication(queue):
    sup_view, batch = _blind_batch(queue)
    example = example_of(batch)
    assert [
        (row["example_id"], row["criterion_id"], row["outcome"]) for row in sup_view["my_answers"]
    ] == [(example["id"], "greeting", "passed")]
    assert sup_view["answers"] == []
    assert sup_view["adjudications"] == []
    assert sup_view["progress"] == []
    no_machine_fields(sup_view)
    # The report refuses while the batch is open.
    refused = queue.sup.get(f"{BATCHES}/{batch['id']}/report")
    assert refused.status_code == 409 and code(refused) == "calibration_batch_not_closed"


def test_an_admin_participant_sees_only_their_own_answers_and_no_adjudication(queue):
    _, batch = _blind_batch(queue)
    owner_view = viewed(queue.owner, batch["id"])
    assert {row["criterion_id"] for row in owner_view["my_answers"]} == {
        "greeting",
        "fee_explained",
    }
    assert owner_view["adjudications"] == []  # a participant, so none before close
    assert owner_view["progress"] == []  # a participant, so no progress block
    assert owner_view["answers"] == []
    no_machine_fields(owner_view)


def test_a_non_participant_admin_sees_progress_and_adjudications_but_no_answers(queue):
    _, batch = _blind_batch(queue)
    admin_view = viewed(queue.admin, batch["id"])
    assert admin_view["my_answers"] == []
    assert admin_view["answers"] == []  # before close
    assert [(row["criterion_id"], row["outcome"]) for row in admin_view["adjudications"]] == [
        ("greeting", "failed")
    ]
    answered = {row["user_id"]: row["answered"] for row in admin_view["progress"]}
    assert answered == {queue.sup.user_id: 1, queue.owner.user_id: 2}
    assert all(set(row) == PROGRESS_KEYS and row["expected"] == 4 for row in admin_view["progress"])
    no_machine_fields(admin_view)


# --- AC4: blinding in the audit log ---------------------------------------------------------


def test_the_audit_log_never_holds_an_outcome_a_note_or_the_title(queue):
    _, batch = _blind_batch(queue)
    example = example_of(batch)
    # A note on the adjudication, then the close.
    assert (
        adjudicate(
            queue.admin,
            batch,
            example["id"],
            "fee_explained",
            "failed",
            note="the fee was explained",
        ).status_code
        == 200
    )
    adjudicate_all(
        queue.admin,
        batch,
        {
            "greeting": "failed",
            "fee_explained": "failed",
            "closing": "passed",
            "off_topic": "not_applicable",
        },
    )
    assert close(queue.admin, batch).status_code == 200
    events = calibration_events(queue.workspace_id)
    types = [event.event_type for event in events]
    assert types.count("calibration_batch.created") == 1
    assert types.count("calibration_answer.recorded") == 3
    assert types.count("calibration_adjudication.recorded") == 5
    assert types.count("calibration_batch.closed") == 1
    for event in events:
        assert (event.target_type, event.target_id) == ("calibration_batch", batch["id"])
        details = event.details
        flat = repr(details)
        assert "Week 48 calibration" not in flat  # never the title
        if event.event_type in ("calibration_answer.recorded", "calibration_adjudication.recorded"):
            assert not OUTCOME_WORDS & set(details), flat
            assert "the fee was explained" not in flat  # never the note
        if event.event_type == "calibration_answer.recorded":
            assert set(details) == {"batch_id", "example_id", "criterion_id", "participant_id"}
        if event.event_type == "calibration_adjudication.recorded":
            assert set(details) == {"batch_id", "example_id", "criterion_id"}
    created_event = next(e for e in events if e.event_type == "calibration_batch.created")
    assert set(created_event.details) == {
        "result_ids",
        "participant_user_ids",
        "example_count",
        "participant_count",
    }
    closed_event = next(e for e in events if e.event_type == "calibration_batch.closed")
    assert set(closed_event.details) == {
        "revision",
        "example_count",
        "participant_count",
        "compared",
        "matched",
        "agreement_bp",
    }


# --- AC5: answer rules ----------------------------------------------------------------------


def test_answer_checks_in_the_spec_order(queue):
    scored = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id])
    example = example_of(batch)
    # A member who is not a participant cannot see the batch: 404 before anything.
    response = answer(queue.plain, batch, "not-an-example", "greeting", "passed")
    assert response.status_code == 404 and code(response) == "calibration_batch_not_found"
    # The criterion rules in order: an unknown criterion with an na outcome is the
    # 404; the criterion that does not apply, with an na outcome, is the 422 of
    # reviewable, not the na rule.
    response = answer(queue.sup, batch, example["id"], "nope", "not_applicable")
    assert response.status_code == 404 and code(response) == "criterion_not_found"
    response = answer(queue.sup, batch, example["id"], NOT_REVIEWABLE, "not_applicable")
    assert response.status_code == 422 and code(response) == "criterion_not_reviewable"
    # The example comes before the criterion.
    response = answer(queue.sup, batch, "not-an-example", "nope", "passed")
    assert response.status_code == 404 and code(response) == "calibration_example_not_found"
    # The participant check comes before the open check: an admin who is not a
    # participant, on a cancelled batch, still gets the participant code.
    assert cancel(queue.owner, batch).status_code == 200
    response = answer(queue.admin, batch, example["id"], "greeting", "passed")
    assert response.status_code == 403 and code(response) == "calibration_not_participant"
    # The open check comes before the example check: a participant with a bogus
    # example id on the cancelled batch gets the closed code.
    response = answer(queue.sup, batch, "not-an-example", "greeting", "passed")
    assert response.status_code == 409 and code(response) == "calibration_batch_closed"


def test_answer_example_and_eligibility_rules(queue):
    scored = _scored(queue)
    other = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id])
    elsewhere = created_batch(queue.owner, [other.result_id], [queue.sup.user_id])
    # An example of another batch.
    refused = answer(queue.sup, batch, example_of(elsewhere)["id"], "greeting", "passed")
    assert refused.status_code == 404 and code(refused) == "calibration_example_not_found"
    # The caller became the conversation's agent after the batch was created.
    with inspection_session_factory()() as db:
        db.execute(
            update(Meeting)
            .where(Meeting.id == scored.meeting_id)
            .values(agent_user_id=queue.sup.user_id)
        )
        db.commit()
    refused = answer(queue.sup, batch, example_of(batch)["id"], "greeting", "passed")
    assert refused.status_code == 422 and code(refused) == "calibration_participant_is_agent"
    # The conversation moved to a team the caller does not supervise.
    with inspection_session_factory()() as db:
        db.execute(
            update(Meeting)
            .where(Meeting.id == scored.meeting_id)
            .values(agent_user_id=None, team_id=queue.team_b)
        )
        db.commit()
    refused = answer(queue.sup, batch, example_of(batch)["id"], "greeting", "passed")
    assert refused.status_code == 403 and code(refused) == "calibration_participant_ineligible"
    # Allow-control: move it back and the answer lands.
    with inspection_session_factory()() as db:
        db.execute(
            update(Meeting).where(Meeting.id == scored.meeting_id).values(team_id=queue.team_a)
        )
        db.commit()
    assert (
        answer(queue.sup, batch, example_of(batch)["id"], "greeting", "passed").status_code == 200
    )


def test_answer_criterion_rules(queue):
    scored = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id])
    example = example_of(batch)
    # An unknown criterion.
    refused = answer(queue.sup, batch, example["id"], "nope", "passed")
    assert refused.status_code == 404 and code(refused) == "criterion_not_found"
    # The criterion that does not apply to the conversation.
    refused = answer(queue.sup, batch, example["id"], NOT_REVIEWABLE, "passed")
    assert refused.status_code == 422 and code(refused) == "criterion_not_reviewable"
    # ``not_applicable`` only when the criterion allows it.
    refused = answer(queue.sup, batch, example["id"], "greeting", "not_applicable")
    assert refused.status_code == 422 and code(refused) == "reviewer_na_not_allowed"
    assert answer(queue.sup, batch, example["id"], "off_topic", "not_applicable").status_code == 200
    # An unknown outcome is the app's usual validation error.
    refused = answer(queue.sup, batch, example["id"], "greeting", "reviewer")
    assert refused.status_code == 422 and code(refused) == "validation_error"
    assert len(answer_rows(batch["id"])) == 1  # refusals wrote nothing


def test_an_answer_changes_while_open_and_an_identical_one_writes_nothing(queue):
    scored = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id])
    example = example_of(batch)
    assert answer(queue.sup, batch, example["id"], "greeting", "passed").status_code == 200
    events = calibration_events(queue.workspace_id)
    assert [e.event_type for e in events].count("calibration_answer.recorded") == 1
    # The same outcome again: no new row, no new event.
    response = answer(queue.sup, batch, example["id"], "greeting", "passed")
    assert response.status_code == 200
    assert len(answer_rows(batch["id"])) == 1
    assert len(calibration_events(queue.workspace_id)) == len(events)
    # A different outcome changes the stored answer and writes one event.
    assert answer(queue.sup, batch, example["id"], "greeting", "failed").status_code == 200
    rows = answer_rows(batch["id"])
    assert len(rows) == 1 and rows[0].outcome == "failed"
    assert [e.event_type for e in calibration_events(queue.workspace_id)].count(
        "calibration_answer.recorded"
    ) == 2


# --- AC6: adjudication rules ----------------------------------------------------------------


def test_adjudication_checks_in_the_spec_order(queue):
    scored = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id, queue.owner.user_id])
    example = example_of(batch)
    # The admin check comes first: a member participant gets the admin code even
    # though they are also a participant.
    refused = adjudicate(queue.sup, batch, example["id"], "greeting", "failed")
    assert refused.status_code == 403 and code(refused) == "calibration_admin_required"
    # A participant admin gets the participant code, before the open check.
    assert cancel(queue.owner, batch).status_code == 200
    refused = adjudicate(queue.owner, batch, "not-an-example", "greeting", "failed")
    assert refused.status_code == 403 and code(refused) == "calibration_adjudicator_is_participant"
    # A non-participant admin on the cancelled batch: the open check, before example.
    refused = adjudicate(queue.admin, batch, "not-an-example", "greeting", "failed")
    assert refused.status_code == 409 and code(refused) == "calibration_batch_closed"


def test_adjudication_agent_criterion_and_note_rules(queue):
    scored = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id])
    example = example_of(batch)
    # The caller is the conversation's current agent.
    with inspection_session_factory()() as db:
        db.execute(
            update(Meeting)
            .where(Meeting.id == scored.meeting_id)
            .values(agent_user_id=queue.admin.user_id)
        )
        db.commit()
    refused = adjudicate(queue.admin, batch, example["id"], "greeting", "failed")
    assert refused.status_code == 422 and code(refused) == "calibration_adjudicator_is_agent"
    with inspection_session_factory()() as db:
        db.execute(
            update(Meeting).where(Meeting.id == scored.meeting_id).values(agent_user_id=None)
        )
        db.commit()
    refused = adjudicate(queue.admin, batch, example["id"], "nope", "failed")
    assert refused.status_code == 404 and code(refused) == "criterion_not_found"
    refused = adjudicate(queue.admin, batch, example["id"], NOT_REVIEWABLE, "failed")
    assert refused.status_code == 422 and code(refused) == "criterion_not_reviewable"
    refused = adjudicate(queue.admin, batch, example["id"], "greeting", "not_applicable")
    assert refused.status_code == 422 and code(refused) == "reviewer_na_not_allowed"
    assert (
        adjudicate(queue.admin, batch, example["id"], "off_topic", "not_applicable").status_code
        == 200
    )
    # Notes: 2000 characters accepted, 2001 refused.
    assert (
        adjudicate(
            queue.admin, batch, example["id"], "greeting", "failed", note="x" * 2000
        ).status_code
        == 200
    )
    refused = adjudicate(queue.admin, batch, example["id"], "greeting", "failed", note="x" * 2001)
    assert refused.status_code == 422 and code(refused) == "validation_error"
    refused = adjudicate(queue.admin, batch, example["id"], "greeting", "failed", note="a\x00b")
    assert refused.status_code == 422 and code(refused) == "validation_error"
    # An identical adjudication writes nothing.
    events = calibration_events(queue.workspace_id)
    assert (
        adjudicate(
            queue.admin, batch, example["id"], "greeting", "failed", note="x" * 2000
        ).status_code
        == 200
    )
    assert len(calibration_events(queue.workspace_id)) == len(events)


# --- AC7: close and cancel -------------------------------------------------------------------


def _adjudicated(queue, participants: list) -> dict:
    scored = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], participants)
    return batch


GOLDS = {
    "greeting": "failed",
    "fee_explained": "failed",
    "closing": "passed",
    "off_topic": "not_applicable",
}


def test_close_refuses_a_stale_revision_then_an_incomplete_batch_then_closes(queue):
    scored = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id, queue.owner.user_id])
    adjudicate_all(queue.admin, batch, GOLDS)
    # A stale revision: the conflict names the current revision.
    refused = close(queue.admin, batch, expected_revision=99)
    assert refused.status_code == 409 and code(refused) == "calibration_batch_revision_conflict"
    assert refused.json()["error"]["details"] == {"current_revision": 1}
    # An incomplete batch: one criterion adjudicated, three missing.
    fresh = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id])
    adjudicate(queue.admin, fresh, example_of(fresh)["id"], "greeting", "failed")
    refused = close(queue.admin, fresh)
    assert refused.status_code == 409 and code(refused) == "calibration_adjudication_incomplete"
    assert refused.json()["error"]["details"] == {"missing": 3}
    # The complete batch closes: revision 2.
    response = close(queue.admin, batch)
    assert response.status_code == 200, response.text
    closed = response.json()
    assert (closed["status"], closed["revision"]) == ("closed", 2)
    assert closed["closed_at"] is not None
    # A second close, even with the new revision, is refused.
    refused = close(queue.admin, closed)
    assert refused.status_code == 409 and code(refused) == "calibration_batch_closed"


def test_after_close_every_answer_and_adjudication_write_is_refused(queue):
    scored = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id])
    adjudicate_all(queue.admin, batch, GOLDS)
    closed = close(queue.admin, batch).json()
    example = example_of(closed)
    refused = answer(queue.sup, closed, example["id"], "greeting", "passed")
    assert refused.status_code == 409 and code(refused) == "calibration_batch_closed"
    refused = adjudicate(queue.admin, closed, example["id"], "greeting", "failed")
    assert refused.status_code == 409 and code(refused) == "calibration_batch_closed"
    assert answer_rows(closed["id"]) == []


def test_cancel_closes_the_batch_for_good(queue):
    scored = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id])
    # A stale revision is refused first.
    refused = cancel(queue.owner, batch, expected_revision=99)
    assert refused.status_code == 409 and code(refused) == "calibration_batch_revision_conflict"
    assert cancel(queue.owner, batch).status_code == 200
    cancelled = viewed(queue.owner, batch["id"])
    assert (cancelled["status"], cancelled["revision"]) == ("cancelled", 2)
    assert cancelled["cancelled_at"] is not None
    example = example_of(cancelled)
    refused = answer(queue.sup, cancelled, example["id"], "greeting", "passed")
    assert refused.status_code == 409 and code(refused) == "calibration_batch_closed"
    refused = adjudicate(queue.admin, cancelled, example["id"], "greeting", "failed")
    assert refused.status_code == 409 and code(refused) == "calibration_batch_closed"
    # A cancelled batch never reveals answers: the report refuses.
    refused = queue.admin.get(f"{BATCHES}/{cancelled['id']}/report")
    assert refused.status_code == 409 and code(refused) == "calibration_batch_not_closed"
    # And it cannot be closed afterwards.
    refused = close(queue.admin, cancelled)
    assert refused.status_code == 409 and code(refused) == "calibration_batch_closed"


# --- AC8: after close ------------------------------------------------------------------------


def _closed_batch(queue) -> dict:
    scored = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id, queue.owner.user_id])
    example = example_of(batch)
    assert answer(queue.sup, batch, example["id"], "greeting", "passed").status_code == 200
    assert answer(queue.sup, batch, example["id"], "closing", "passed").status_code == 200
    assert answer(queue.owner, batch, example["id"], "fee_explained", "failed").status_code == 200
    adjudicate_all(queue.admin, batch, GOLDS)
    return close(queue.admin, batch).json()


def test_after_close_the_blinding_relaxes_exactly_as_specified(queue):
    closed = _closed_batch(queue)
    # A participant sees the adjudications and their own answers, nobody else's.
    sup_view = viewed(queue.sup, closed["id"])
    assert {row["criterion_id"] for row in sup_view["adjudications"]} == set(REVIEWABLE)
    assert {row["criterion_id"] for row in sup_view["my_answers"]} == {"greeting", "closing"}
    assert sup_view["answers"] == []
    assert sup_view["progress"] == []
    no_machine_fields(sup_view)
    # An admin participant: the same participant rule.
    owner_view = viewed(queue.owner, closed["id"])
    assert {row["criterion_id"] for row in owner_view["my_answers"]} == {"fee_explained"}
    assert len(owner_view["adjudications"]) == 4
    assert owner_view["answers"] == []
    no_machine_fields(owner_view)
    # A non-participant admin now sees every answer.
    admin_view = viewed(queue.admin, closed["id"])
    assert admin_view["my_answers"] == []
    assert {(row["user_id"], row["criterion_id"]) for row in admin_view["answers"]} == {
        (queue.sup.user_id, "greeting"),
        (queue.sup.user_id, "closing"),
        (queue.owner.user_id, "fee_explained"),
    }
    no_machine_fields(admin_view)


# --- AC9: the report, end to end --------------------------------------------------------------


SUP_ANSWERS = {"greeting": "passed", "fee_explained": "failed", "closing": "passed"}
OWNER_ANSWERS = {
    "greeting": "failed",
    "fee_explained": "passed",
    "closing": "passed",
    "off_topic": "not_applicable",
}


def _reported_batch(queue) -> dict:
    scored = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id, queue.owner.user_id])
    answer_all(queue.sup, batch, SUP_ANSWERS)
    answer_all(queue.owner, batch, OWNER_ANSWERS)
    adjudicate_all(queue.admin, batch, GOLDS)
    return close(queue.admin, batch).json()


def test_the_report_of_a_hand_computed_batch(queue):
    closed = _reported_batch(queue)
    report = reported(queue.admin, closed["id"])
    # The close event carries the overall numbers below, and no outcome.
    (event,) = [
        e
        for e in calibration_events(queue.workspace_id)
        if e.event_type == "calibration_batch.closed"
    ]
    assert event.details == {
        "revision": 2,
        "example_count": 1,
        "participant_count": 2,
        "compared": 7,
        "matched": 5,
        "agreement_bp": 7143,
    }
    assert set(report) == {"batch_id", "overall", "participants", "criteria", "machine"}
    assert report["batch_id"] == closed["id"]
    # sup: 3 of 4 answered, 2 matched -> 6667; kappa 2/5 -> 4000.
    # owner: 4 of 4 answered, 3 matched -> 7500; kappa 7/11 -> 6364.
    rows = {row["user_id"]: row for row in report["participants"]}
    assert set(rows) == {queue.sup.user_id, queue.owner.user_id}
    sup, owner = rows[queue.sup.user_id], rows[queue.owner.user_id]
    assert (sup["expected"], sup["compared"], sup["matched"], sup["missing"]) == (4, 3, 2, 1)
    assert (sup["agreement_bp"], sup["kappa_e4"]) == (6667, 4000)
    assert sup["confusion"] == {
        "failed": {"passed": 1, "failed": 1},
        "passed": {"passed": 1},
    }
    assert (owner["expected"], owner["compared"], owner["matched"], owner["missing"]) == (
        4,
        4,
        3,
        0,
    )
    assert (owner["agreement_bp"], owner["kappa_e4"]) == (7500, 6364)
    assert owner["confusion"] == {
        "failed": {"failed": 1, "passed": 1},
        "passed": {"passed": 1},
        "not_applicable": {"not_applicable": 1},
    }
    # Overall pools both participants: 8 expected, 7 compared, 5 matched;
    # agreement_bp = 50000/7 = 7142.86 -> 7143.  answers P 4, F 2, NA 1; golds F 4,
    # P 2, NA 1: p_e = (4*2 + 2*4 + 1*1) / 49 = 17/49, p_o = 35/49,
    # kappa = (18/49) / (32/49) = 9/16 -> 5625.
    assert report["overall"] == {
        "expected": 8,
        "compared": 7,
        "matched": 5,
        "missing": 1,
        "agreement_bp": 7143,
        "kappa_e4": 5625,
        "confusion": {
            "failed": {"passed": 2, "failed": 2},
            "passed": {"passed": 2},
            "not_applicable": {"not_applicable": 1},
        },
    }
    # Per criterion, over all participants and examples: compared, matched, agreement_bp.
    assert report["criteria"] == [
        {"criterion_id": "greeting", "compared": 2, "matched": 1, "agreement_bp": 5000},
        {"criterion_id": "fee_explained", "compared": 2, "matched": 1, "agreement_bp": 5000},
        {"criterion_id": "closing", "compared": 2, "matched": 2, "agreement_bp": 10000},
        {"criterion_id": "off_topic", "compared": 1, "matched": 1, "agreement_bp": 10000},
    ]
    # The machine row: greeting passed, fee failed, closing passed, off_topic failed
    # against the golds: 4 compared, 2 matched -> 5000.  machine P 2, F 2; golds F 2,
    # P 1, NA 1: p_e = (2*1 + 2*2) / 16 = 6/16, p_o = 8/16,
    # kappa = (2/16) / (10/16) = 1/5 -> 2000.
    assert report["machine"] == {
        "expected": 4,
        "compared": 4,
        "matched": 2,
        "missing": 0,
        "agreement_bp": 5000,
        "kappa_e4": 2000,
        "confusion": {
            "failed": {"passed": 1, "failed": 1},
            "passed": {"passed": 1},
            "not_applicable": {"failed": 1},
        },
    }


def test_the_report_of_a_removed_participant_shows_a_null_user(queue):
    closed = _reported_batch(queue)
    # Remove sup behind the API (a user removal sets the column to NULL); the row and
    # its answers stay, so the report still counts them with a null user.
    with inspection_session_factory()() as db:
        removed = db.execute(
            update(CalibrationParticipant)
            .where(
                CalibrationParticipant.batch_id == closed["id"],
                CalibrationParticipant.user_id == queue.sup.user_id,
            )
            .values(user_id=None)
            .execution_options(synchronize_session=False)
        ).rowcount
        db.commit()
    assert removed == 1
    report = reported(queue.admin, closed["id"])
    rows = {row["user_id"]: row for row in report["participants"]}
    assert None in rows
    assert rows[None]["compared"] == 3  # sup's answers still count


def test_a_non_admin_participant_gets_their_own_row_and_no_machine_row(queue):
    closed = _reported_batch(queue)
    report = reported(queue.sup, closed["id"])
    assert set(report) == {"batch_id", "overall", "participants", "criteria", "machine"}
    assert [row["user_id"] for row in report["participants"]] == [queue.sup.user_id]
    assert report["machine"] is None
    assert report["overall"]["expected"] == 8  # the overall block is shared
    assert len(report["criteria"]) == 4


# --- AC11: isolation and API keys --------------------------------------------------------------


def test_every_route_refuses_an_api_key(client, queue):
    scored = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id])
    every_scope = sorted(permission.value for permission in API_KEY_SCOPES)
    key = _mint(client, queue.owner.headers, queue.owner.cookies, name="all", scopes=every_scope)
    assert key.status_code == 201, key.text
    headers = _bearer(key.json()["key"], **{"X-Workspace-ID": queue.owner.workspace_id})
    example = example_of(batch)
    for method, path, body in (
        ("GET", BATCHES, None),
        (
            "POST",
            BATCHES,
            {
                "title": "t",
                "scorecard_result_ids": [scored.result_id],
                "participant_user_ids": [queue.sup.user_id],
            },
        ),
        ("GET", f"{BATCHES}/{batch['id']}", None),
        ("GET", f"{BATCHES}/{batch['id']}/report", None),
        (
            "POST",
            f"{BATCHES}/{batch['id']}/examples/{example['id']}/criteria/greeting/answer",
            {"outcome": "passed"},
        ),
        (
            "POST",
            f"{BATCHES}/{batch['id']}/examples/{example['id']}/criteria/greeting/adjudication",
            {"outcome": "passed"},
        ),
        ("POST", f"{BATCHES}/{batch['id']}/close", {"expected_revision": 1}),
        ("POST", f"{BATCHES}/{batch['id']}/cancel", {"expected_revision": 1}),
    ):
        response = client.request(method, path, json=body, headers=headers)
        assert response.status_code == 403, (method, path, response.text)
        assert code(response) == "api_key_scope_denied", (method, path)
    for scope in ("review:read", "review:manage"):
        refused = _mint(
            client, queue.owner.headers, queue.owner.cookies, name=scope, scopes=[scope]
        )
        assert refused.status_code == 422, refused.text


def test_every_write_route_needs_the_csrf_token(queue):
    scored = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id])
    example = example_of(batch)
    paths = (
        (
            BATCHES,
            {
                "title": "t",
                "scorecard_result_ids": [scored.result_id],
                "participant_user_ids": [queue.sup.user_id],
            },
        ),
        (
            f"{BATCHES}/{batch['id']}/examples/{example['id']}/criteria/greeting/answer",
            {"outcome": "passed"},
        ),
        (
            f"{BATCHES}/{batch['id']}/examples/{example['id']}/criteria/greeting/adjudication",
            {"outcome": "passed"},
        ),
        (f"{BATCHES}/{batch['id']}/close", {"expected_revision": 1}),
        (f"{BATCHES}/{batch['id']}/cancel", {"expected_revision": 1}),
    )
    for path, body in paths:
        headers = {
            key: value for key, value in queue.owner.headers.items() if key != "X-CSRF-Token"
        }
        response = queue.owner.client.post(
            path, json=body, cookies=queue.owner.cookies, headers=headers
        )
        assert response.status_code == 403 and code(response) == "csrf_failed", path
    assert answer_rows(batch["id"]) == []
