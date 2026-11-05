"""HOM-74 PR 2: criterion accept and override, the human result, the criteria view.

Every SPEC deny-case runs next to its allow-control, and each denial asserts the code of
the check that must refuse it.  The machine rows must stay byte-identical; the human
result is the machine result scored again with the reviewer outcomes.  ``GET
/review-items/{id}/criteria`` shows the two side by side: a preview (``final: false``)
while the item is active, the stored row (``final: true``) after completion.

The scorecard results are real: a published version, a real ``CheckRun``, the engine's
own output stored through ``record_result``.  ``SEGMENTS`` is the transcript revision's
``segment_count``, so an evidence turn index of ``SEGMENTS`` or more is out of range.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select, update

from backend.conversation_checks import CheckTurn, finish_checks, plan_checks
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    AuditEvent,
    Meeting,
    MeetingStatus,
    ReviewCriterionDecision,
    ReviewResultRecord,
    ScorecardResultRecord,
    ScorecardVersion,
    TranscriptRevision,
    TranscriptSource,
)
from backend.database.tenant_context import bind_tenant_context
from backend.scorecards import (
    SCORECARDS_VERSION,
    ConversationFacts,
    ScorecardError,
    plan_scorecard,
    score_conversation,
)
from backend.services.scorecard_result_service import record_result, verify_result
from backend.services.scorecard_service import publish_version
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_review_queue_access import (
    ITEMS,
    Queue,
    act,
    acted,
    audit_count,
    build_queue,
    code,
    created,
    review_events,
    stored,
)

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
TURNS = [
    CheckTurn("hello and welcome to the shop", "agent", 0, 3000, 0.9),
    CheckTurn("I want to change my plan", "customer", 3000, 6000, 0.9),
    CheckTurn("thank you, goodbye", "agent", 6000, 9000, 0.9),
]
SEGMENTS = 25
MACHINE_OUTCOMES = {
    "greeting": "passed",
    "fee_explained": "failed",
    "closing": "passed",
    "off_topic": "failed",
    "agenda": "not_applicable",
}
SCORE_KEYS = {
    "outcome",
    "reason",
    "complete",
    "score",
    "score_min",
    "score_max",
    "pass_threshold",
    "applicable_weight",
    "earned_weight",
    "failed_weight",
    "insufficient_weight",
}
CRITERION_KEYS = {
    "criterion_id",
    "position",
    "weight",
    "auto_fail",
    "na_allowed",
    "reviewable",
    "machine",
    "decision",
}
CRITERION_MACHINE_KEYS = {
    "outcome",
    "reason",
    "decided_by",
    "review_required",
    "review_reasons",
    "evidence",
}
DECISION_KEYS = {
    "decision",
    "reviewer_outcome",
    "reason_code",
    "note",
    "evidence",
    "decided_by_user_id",
    "updated_at",
}
DECIDED_EVENT_KEYS = {
    "criterion_id",
    "scorecard_result_id",
    "decision",
    "machine_outcome",
    "reviewer_outcome",
    "reason_code",
    "evidence_turn_indexes",
    "previous_decision",
    "previous_reviewer_outcome",
    "revision",
}
COMPLETED_EVENT_KEYS = {
    "revision",
    "scorecard_result_id",
    "review_result_id",
    "machine_outcome",
    "machine_reason",
    "human_outcome",
    "human_reason",
    "applicable_weight",
    "earned_weight",
    "failed_weight",
    "insufficient_weight",
    "accepted_count",
    "overridden_count",
}


@pytest.fixture
def queue(client, email_outbox, monkeypatch) -> Queue:
    return build_queue(client, email_outbox, monkeypatch)


def _criterion(criterion_id: str, weight: int, phrase: str, **extra: Any) -> dict[str, Any]:
    return {
        "criterion_id": criterion_id,
        "label": criterion_id.replace("_", " ").capitalize(),
        "weight": weight,
        "check": {
            "check_id": criterion_id,
            "version": 1,
            "kind": "required_phrase",
            "phrases": [phrase],
        },
        **extra,
    }


# On a call: greeting and closing pass, the auto-fail fee is not explained, off_topic
# fails and allows not applicable, and the agenda criterion is for meetings only.
DEFINITION = {
    "name": "Review decisions",
    "pass_threshold": 70,
    "criteria": [
        _criterion("greeting", 20, "hello"),
        _criterion("fee_explained", 20, "cancellation fee", auto_fail=True),
        _criterion("closing", 20, "goodbye"),
        _criterion("off_topic", 20, "weather", na_allowed=True),
        _criterion("agenda", 20, "agenda", applies_to={"conversation_types": ["meeting"]}),
    ],
}
# Without a direction the follow_up criterion is insufficient evidence.
DIRECTED = {
    "name": "Directed decisions",
    "pass_threshold": 50,
    "criteria": [
        _criterion("greeting", 34, "hello"),
        _criterion("closing", 33, "goodbye"),
        _criterion("follow_up", 33, "follow up", applies_to={"directions": ["inbound"]}),
    ],
}


# --- the scored fixture -------------------------------------------------------------------


def _app_session(workspace_id: str):
    db = get_session_factory()()
    bind_tenant_context(db, workspace_id=workspace_id)
    return db


@dataclass
class Scored:
    """An ``in_review`` review item on a real stored machine result."""

    item: dict
    definition: dict[str, Any]
    facts: ConversationFacts
    run: Any
    version_id: str
    result_id: str
    meeting_id: str


def _scored_result(
    queue: Queue,
    *,
    definition: dict[str, Any] = DEFINITION,
    direction: str | None = "inbound",
    agent_user_id: str | None = None,
    key: str | None = None,
) -> dict[str, Any]:
    # Every call publishes its own scorecard key, so a test may score several
    # conversations with the same definition.
    key = key or f"decisions_{uuid.uuid4().hex[:12]}"
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=queue.workspace_id,
            created_by_user_id=queue.owner.user_id,
            title="Scored call under review",
            status=MeetingStatus.COMPLETED,
            duration_seconds=30,
            conversation_type="call",
            direction=direction,
            team_id=queue.team_a,
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
        result_id, version_id = row.id, version.id
    return {
        "meeting_id": meeting_id,
        "result_id": result_id,
        "version_id": version_id,
        "definition": definition,
        "facts": facts,
        "run": run,
    }


def _scored_item(
    queue: Queue, *, assignee=None, definition=DEFINITION, direction="inbound", **kwargs
) -> Scored:
    scored = _scored_result(queue, definition=definition, direction=direction, **kwargs)
    persona = assignee or queue.sup
    item = created(
        queue.owner,
        scored["meeting_id"],
        scorecard_result_id=scored["result_id"],
        assignee_user_id=persona.user_id,
    )
    item = acted(persona, item, "start")
    return Scored(
        item=item,
        definition=scored["definition"],
        facts=scored["facts"],
        run=scored["run"],
        version_id=scored["version_id"],
        result_id=scored["result_id"],
        meeting_id=scored["meeting_id"],
    )


def decide(persona, item: dict, criterion_id: str, **body: object):
    payload = {"expected_revision": item["revision"], **body}
    return persona.post(f"{ITEMS}/{item['id']}/criteria/{criterion_id}/decision", json=payload)


def decide_raw(persona, item: dict, criterion_id: str, **body: object):
    """The decision body as pre-serialized JSON, so a lone surrogate travels as its
    ``\\ud800`` escape (the client's own encoder refuses the character itself)."""
    import json as _json

    raw = _json.dumps({"expected_revision": item["revision"], **body})
    return persona.client.post(
        f"{ITEMS}/{item['id']}/criteria/{criterion_id}/decision",
        content=raw,
        cookies=persona.cookies,
        headers={**persona.headers, "Content-Type": "application/json"},
    )


def decided(persona, item: dict, criterion_id: str, **body: object) -> dict:
    """The criteria view of an accepted decision, usable as the next ``item``."""
    response = decide(persona, item, criterion_id, **body)
    assert response.status_code == 200, response.text
    view = response.json()
    return {"id": item["id"], **view}


def accept(**extra: object) -> dict:
    return {"decision": "accept", **extra}


def override(outcome: str, *, reason: str = "other", note: str = "why not", evidence=None, **extra):
    body: dict[str, object] = {
        "decision": "override",
        "reviewer_outcome": outcome,
        "reason_code": reason,
        "note": note,
    }
    if evidence is not None:
        body["evidence_turn_indexes"] = evidence
    return {**body, **extra}


def criteria(persona, item_id: str) -> dict:
    response = persona.get(f"{ITEMS}/{item_id}/criteria")
    assert response.status_code == 200, response.text
    return response.json()


def decision_rows(item_id: str) -> list[ReviewCriterionDecision]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(ReviewCriterionDecision)
                .where(ReviewCriterionDecision.review_item_id == item_id)
                .order_by(ReviewCriterionDecision.criterion_id)
            )
        )


def human_rows(item_id: str) -> list[ReviewResultRecord]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(ReviewResultRecord).where(ReviewResultRecord.review_item_id == item_id)
            )
        )


def decided_events(workspace_id: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.workspace_id == workspace_id,
                    AuditEvent.event_type == "review_criterion.decided",
                )
                .order_by(AuditEvent.occurred_at.asc())
            )
        )


def _snapshot_result(result_id: str) -> dict:
    with inspection_session_factory()() as db:
        row = db.get(ScorecardResultRecord, result_id)
        return {
            column: getattr(row, column)
            for column in (
                "scorecards_version",
                "checks_version",
                "definition_digest",
                "input_digest",
                "conversation_type",
                "direction",
                "outcome",
                "reason",
                "complete",
                "pass_threshold",
                "applicable_weight",
                "earned_weight",
                "failed_weight",
                "insufficient_weight",
                "check_run",
                "result",
            )
        }


def _set_agent(meeting_id: str, user_id: str | None) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(Meeting).where(Meeting.id == meeting_id).values(agent_user_id=user_id))
        db.commit()


def _reviewer_outcomes(scored: Scored, item: dict) -> dict[str, str]:
    return {row.criterion_id: row.reviewer_outcome for row in decision_rows(item["id"])}


def _expected_human(scored: Scored, item: dict, *, result_id: str | None = None) -> Any:
    with inspection_session_factory()() as db:
        document = db.get(ScorecardVersion, scored.version_id).document
    return score_conversation(
        document,
        _stored_check_run(result_id or scored.result_id),
        scored.facts,
        _reviewer_outcomes(scored, item),
    )


def _stored_check_run(result_id: str) -> dict[str, Any]:
    with inspection_session_factory()() as db:
        return db.get(ScorecardResultRecord, result_id).check_run


# --- permissions and route policy ---------------------------------------------------------


def test_a_viewer_gets_403_on_both_new_routes(queue):
    scored = _scored_item(queue)
    path = f"{ITEMS}/{scored.item['id']}/criteria/greeting/decision"
    response = queue.viewer.post(path, json=accept(expected_revision=scored.item["revision"]))
    assert response.status_code == 403 and code(response) == "permission_denied"
    response = queue.viewer.get(f"{ITEMS}/{scored.item['id']}/criteria")
    assert response.status_code == 403 and code(response) == "permission_denied"
    # Allow-control: the member assignee who supervises the team.
    body = decided(queue.sup, scored.item, "greeting", **accept())
    assert body["revision"] == scored.item["revision"] + 1


def test_a_member_without_authority_never_sees_the_item_or_its_criteria(queue):
    scored = _scored_item(queue)
    for persona in (queue.plain, queue.sup_b):
        response = decide(persona, scored.item, "greeting", **accept())
        assert response.status_code == 404 and code(response) == "review_item_not_found"
        response = persona.get(f"{ITEMS}/{scored.item['id']}/criteria")
        assert response.status_code == 404 and code(response) == "review_item_not_found"
    # Allow-control: a supervisor of that team.
    assert decide(queue.sup, scored.item, "greeting", **accept()).status_code == 200
    assert queue.sup.get(f"{ITEMS}/{scored.item['id']}/criteria").status_code == 200


def test_the_conversations_agent_does_not_see_the_item(queue):
    queue.assign_team(queue.lapsed, queue.team_a, "supervisor")
    scored = _scored_item(queue, assignee=queue.lapsed, agent_user_id=queue.sup.user_id)
    response = decide(queue.sup, scored.item, "greeting", **accept())
    assert response.status_code == 404 and code(response) == "review_item_not_found"
    assert queue.sup.get(f"{ITEMS}/{scored.item['id']}/criteria").status_code == 404
    # Allow-control: another supervisor of that team.
    assert decide(queue.lapsed, scored.item, "greeting", **accept()).status_code == 200


def test_an_admin_assignee_who_is_the_conversations_agent(queue):
    scored = _scored_item(queue, assignee=queue.admin)
    _set_agent(scored.meeting_id, queue.admin.user_id)
    response = decide(queue.admin, scored.item, "greeting", **accept())
    assert response.status_code == 422 and code(response) == "review_assignee_is_agent"
    assert decision_rows(scored.item["id"]) == []
    # Allow-control: an admin assignee who is not the agent.
    _set_agent(scored.meeting_id, queue.agent.user_id)
    assert decide(queue.admin, scored.item, "greeting", **accept()).status_code == 200


def test_an_item_of_another_workspace_is_404(client, email_outbox, queue):
    from backend.tests.test_review_queue_access import stranger

    other = stranger(client, email_outbox, "decisions-other@example.com")
    foreign = created(other, _foreign_meeting(other))
    response = decide(queue.owner, foreign, "greeting", **accept())
    assert response.status_code == 404 and code(response) == "review_item_not_found"
    assert queue.owner.get(f"{ITEMS}/{foreign['id']}/criteria").status_code == 404
    # Allow-control: the own workspace's item passes the visibility check (it fails later
    # on its missing result, not on visibility).
    own = acted(
        queue.owner, created(queue.owner, queue.m_a, assignee_user_id=queue.owner.user_id), "start"
    )
    refused = decide(queue.owner, own, "greeting", **accept())
    assert refused.status_code == 409 and code(refused) == "review_item_has_no_result"


def _foreign_meeting(persona) -> str:
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=persona.workspace_id,
            created_by_user_id=persona.user_id,
            title="Foreign call",
        )
        db.add(meeting)
        db.commit()
        return meeting.id


def test_an_api_key_gets_403_on_both_new_routes(client, queue):
    from backend.security.authorization import API_KEY_SCOPES

    scored = _scored_item(queue)
    every_scope = sorted(permission.value for permission in API_KEY_SCOPES)
    key = _mint(client, queue.owner.headers, queue.owner.cookies, name="all", scopes=every_scope)
    assert key.status_code == 201, key.text
    headers = _bearer(key.json()["key"], **{"X-Workspace-ID": queue.workspace_id})
    response = client.post(
        f"{ITEMS}/{scored.item['id']}/criteria/greeting/decision",
        json=accept(expected_revision=scored.item["revision"]),
        headers=headers,
    )
    assert response.status_code == 403 and code(response) == "api_key_scope_denied"
    response = client.get(f"{ITEMS}/{scored.item['id']}/criteria", headers=headers)
    assert response.status_code == 403 and code(response) == "api_key_scope_denied"
    # Allow-control: the session caller.
    assert decide(queue.sup, scored.item, "greeting", **accept()).status_code == 200


def test_the_decision_route_needs_the_csrf_token(queue):
    scored = _scored_item(queue)
    before = audit_count()
    response = queue.owner.client.post(
        f"{ITEMS}/{scored.item['id']}/criteria/greeting/decision",
        json=accept(expected_revision=scored.item["revision"]),
        cookies=queue.sup.cookies,
        headers={"X-Workspace-ID": queue.workspace_id},
    )
    assert response.status_code == 403 and code(response) == "csrf_failed"
    assert audit_count() == before and decision_rows(scored.item["id"]) == []
    # Allow-control: the same decision with the token.
    assert decide(queue.sup, scored.item, "greeting", **accept()).status_code == 200


# --- the write checks, in order -----------------------------------------------------------


def test_only_the_assignee_decides(queue):
    scored = _scored_item(queue)
    for persona in (queue.owner, queue.admin):
        response = decide(persona, scored.item, "greeting", **accept())
        assert response.status_code == 403 and code(response) == "review_not_assignee"
    assert decision_rows(scored.item["id"]) == []
    # Allow-control: the assignee.
    assert decide(queue.sup, scored.item, "greeting", **accept()).status_code == 200


def test_a_stale_revision_is_refused_and_writes_nothing(queue):
    scored = _scored_item(queue)
    events = audit_count()
    for stale in (1, scored.item["revision"] + 1, 99):
        response = decide(
            queue.sup, {**scored.item, "revision": stale}, "greeting", **accept()
        )
        assert response.status_code == 409, (stale, response.text)
        assert code(response) == "review_item_revision_conflict"
        assert response.json()["error"]["details"] == {
            "current_revision": scored.item["revision"]
        }
    assert decision_rows(scored.item["id"]) == []
    assert audit_count() == events
    # Allow-control: the current revision.
    body = decided(queue.sup, scored.item, "greeting", **accept())
    assert body["revision"] == scored.item["revision"] + 1


def test_an_open_item_is_not_decidable(queue):
    base = _scored_result(queue, key="open_item")
    open_item = created(
        queue.owner, base["meeting_id"], scorecard_result_id=base["result_id"]
    )
    open_item = acted(queue.owner, open_item, "assign", assignee_user_id=queue.sup.user_id)
    response = decide(queue.sup, open_item, "greeting", **accept())
    assert response.status_code == 409 and code(response) == "review_item_state_invalid"
    assert decision_rows(open_item["id"]) == []
    # Allow-control: the same item after start.
    open_item = acted(queue.sup, open_item, "start")
    assert decide(queue.sup, open_item, "greeting", **accept()).status_code == 200


def test_a_closed_item_is_not_decidable(queue):
    scored = _scored_item(queue)
    done = acted(queue.sup, scored.item, "complete")
    for persona in (queue.sup, queue.owner):
        response = decide(persona, done, "closing", **accept())
        assert response.status_code == 409 and code(response) == "review_item_closed"
    cancelled = _scored_item(queue)
    cancelled.item = acted(queue.owner, cancelled.item, "cancel", reason="duplicate")
    response = decide(queue.sup, cancelled.item, "closing", **accept())
    assert response.status_code == 409 and code(response) == "review_item_closed"
    assert human_rows(cancelled.item["id"]) == []
    # Allow-control: an active item.
    active = _scored_item(queue)
    assert decide(queue.sup, active.item, "closing", **accept()).status_code == 200


def test_an_item_without_a_result_is_not_decidable(queue):
    item = acted(
        queue.sup,
        created(queue.owner, queue.m_a2, assignee_user_id=queue.sup.user_id),
        "start",
    )
    response = decide(queue.sup, item, "greeting", **accept())
    assert response.status_code == 409 and code(response) == "review_item_has_no_result"
    assert decision_rows(item["id"]) == []
    # Allow-control: an item with a result.
    scored = _scored_item(queue)
    assert decide(queue.sup, scored.item, "greeting", **accept()).status_code == 200


def test_the_decision_needs_a_known_decision_value(queue):
    scored = _scored_item(queue)
    for decision in (None, "nope", 5):
        response = decide(queue.sup, scored.item, "greeting", decision=decision)
        assert response.status_code == 422, (decision, response.text)
        assert code(response) == "review_decision_invalid", decision
    for payload in (
        {"expected_revision": scored.item["revision"]},
        {"expected_revision": scored.item["revision"], "decision": None},
        {"reviewer_outcome": "passed"},
    ):
        response = queue.sup.post(
            f"{ITEMS}/{scored.item['id']}/criteria/greeting/decision", json=payload
        )
        assert response.status_code == 422, payload
    assert decision_rows(scored.item["id"]) == []
    # Allow-control: a known decision.
    assert decide(queue.sup, scored.item, "greeting", **accept()).status_code == 200


def test_an_unknown_criterion_is_404(queue):
    scored = _scored_item(queue)
    response = decide(queue.sup, scored.item, "nope", **accept())
    assert response.status_code == 404 and code(response) == "criterion_not_found"
    # Allow-control: a criterion of the result.
    assert decide(queue.sup, scored.item, "greeting", **accept()).status_code == 200


def test_a_criterion_not_applicable_by_conversation_is_not_reviewable(queue):
    scored = _scored_item(queue)
    response = decide(queue.sup, scored.item, "agenda", **accept())
    assert response.status_code == 422 and code(response) == "criterion_not_reviewable"
    assert decision_rows(scored.item["id"]) == []
    # Allow-control: an applicable criterion, and the view marks only agenda.
    assert decide(queue.sup, scored.item, "greeting", **accept()).status_code == 200
    view = criteria(queue.sup, scored.item["id"])
    flags = {entry["criterion_id"]: entry["reviewable"] for entry in view["criteria"]}
    assert flags == {
        "greeting": True,
        "fee_explained": True,
        "closing": True,
        "off_topic": True,
        "agenda": False,
    }


def test_an_insufficient_criterion_cannot_be_accepted(queue):
    scored = _scored_item(queue, definition=DIRECTED, direction=None, key="directed")
    response = decide(queue.sup, scored.item, "follow_up", **accept())
    assert response.status_code == 422 and code(response) == "criterion_accept_not_possible"
    assert decision_rows(scored.item["id"]) == []
    # Allow-control: an override settles it.
    assert (
        decide(
            queue.sup, scored.item, "follow_up", **override("passed", evidence=[1])
        ).status_code
        == 200
    )


def test_an_accept_carries_no_reason_no_evidence_and_the_machine_outcome(queue):
    scored = _scored_item(queue)
    for body in (
        accept(reason_code="other"),
        accept(evidence_turn_indexes=[0]),
        accept(evidence_turn_indexes=[]),
        accept(reason_code=""),
        accept(reviewer_outcome="failed"),
        accept(reviewer_outcome="nope"),
    ):
        response = decide(queue.sup, scored.item, "greeting", **body)
        assert response.status_code == 422, body
        assert code(response) == "review_decision_invalid", body
    assert decision_rows(scored.item["id"]) == []
    # Allow-controls: a note only, and the machine outcome repeated.
    item = decided(queue.sup, scored.item, "greeting", **accept(note=" fine "))
    assert (
        decide(queue.sup, item, "greeting", **accept(reviewer_outcome="passed")).status_code == 200
    )


def test_an_override_must_differ_from_the_machine_outcome(queue):
    scored = _scored_item(queue)
    for criterion, outcome in (("greeting", "passed"), ("fee_explained", "failed")):
        response = decide(
            queue.sup, scored.item, criterion, **override(outcome, evidence=[0])
        )
        assert response.status_code == 422, (criterion, outcome)
        assert code(response) == "override_matches_machine"
    # Allow-control: a different outcome.
    assert (
        decide(queue.sup, scored.item, "greeting", **override("failed", evidence=[0])).status_code
        == 200
    )


def test_an_override_to_not_applicable_needs_na_allowed(queue):
    scored = _scored_item(queue)
    response = decide(
        queue.sup, scored.item, "greeting", **override("not_applicable", evidence=[0])
    )
    assert response.status_code == 422 and code(response) == "reviewer_na_not_allowed"
    # Allow-control: a criterion that allows it.
    assert (
        decide(
            queue.sup, scored.item, "off_topic", **override("not_applicable", evidence=[0])
        ).status_code
        == 200
    )


def test_an_override_needs_a_reason_code_and_a_note(queue):
    scored = _scored_item(queue)
    for body in (
        {"decision": "override", "reviewer_outcome": "failed", "note": "why"},
        {
            "decision": "override",
            "reviewer_outcome": "failed",
            "reason_code": "bored",
            "note": "why",
        },
        {"decision": "override", "reviewer_outcome": "failed", "reason_code": "other"},
        {
            "decision": "override",
            "reviewer_outcome": "failed",
            "reason_code": "other",
            "note": "   ",
        },
        {
            "decision": "override",
            "reviewer_outcome": "failed",
            "reason_code": "other",
            "note": "",
        },
        {
            "decision": "override",
            "reviewer_outcome": "failed",
            "reason_code": "",
            "note": "why",
        },
    ):
        response = decide(queue.sup, scored.item, "greeting", **body)
        assert response.status_code == 422, body
        assert code(response) == "override_reason_required", body
    assert decision_rows(scored.item["id"]) == []
    # Allow-control: a code and a note.
    assert (
        decide(
            queue.sup,
            scored.item,
            "greeting",
            **override("failed", reason="other", note="why", evidence=[0]),
        ).status_code
        == 200
    )


def test_the_note_length_limit_after_strip(queue):
    scored = _scored_item(queue)
    response = decide(
        queue.sup, scored.item, "greeting", **accept(note="a" * 2001)
    )
    assert response.status_code == 422 and code(response) == "review_note_invalid"
    edge = "a" * 2000
    item = decided(queue.sup, scored.item, "greeting", **accept(note=f"  {edge}  "))
    (row,) = decision_rows(scored.item["id"])
    assert row.note == edge
    assert item["revision"] == scored.item["revision"] + 1
    # The same limit on an override, after its own checks pass.
    response = decide(
        queue.sup, item, "closing", **override("failed", note="b" * 2001, evidence=[0])
    )
    assert response.status_code == 422 and code(response) == "review_note_invalid"
    assert (
        decide(
            queue.sup, item, "closing", **override("failed", note="b" * 2000, evidence=[0])
        ).status_code
        == 200
    )


def test_a_note_with_a_nul_or_a_lone_surrogate_is_refused(queue):
    """PostgreSQL TEXT refuses both; the answer is the note's own code, never a 500."""
    scored = _scored_item(queue)
    for bad in ("a\x00b", "\ud800", "fine\x00"):
        response = decide_raw(queue.sup, scored.item, "greeting", **accept(note=bad))
        assert response.status_code == 422, (bad, response.text)
        assert code(response) == "review_note_invalid", bad
        response = decide_raw(
            queue.sup, scored.item, "closing", **override("failed", note=bad, evidence=[0])
        )
        assert response.status_code == 422, (bad, response.text)
        assert code(response) == "review_note_invalid", bad
    assert decision_rows(scored.item["id"]) == []
    assert stored(scored.item["id"]).revision == scored.item["revision"]
    # Allow-control: a clean note on an accept and on an override.
    item = decided(queue.sup, scored.item, "greeting", **accept(note="fine note"))
    assert (
        decide(
            queue.sup, item, "closing", **override("failed", note="why not", evidence=[0])
        ).status_code
        == 200
    )


def test_the_evidence_rules(queue):
    scored = _scored_item(queue)
    refused = [
        None,  # missing
        [],
        [0, 0],
        [-1],
        [True],
        [1.5],
        ["1"],
        [SEGMENTS],
        [0, SEGMENTS],
        list(range(21)),
        5,
    ]
    for evidence in refused:
        response = decide(
            queue.sup, scored.item, "greeting", **override("failed", evidence=evidence)
        )
        assert response.status_code == 422, evidence
        assert code(response) == "override_evidence_required", evidence
    assert decision_rows(scored.item["id"]) == []
    # Allow-controls: one item and twenty valid items, in any order.
    item = decided(
        queue.sup, scored.item, "greeting", **override("failed", evidence=[3])
    )
    assert (
        decide(
            queue.sup,
            item,
            "closing",
            **override("failed", evidence=list(range(20))[::-1]),
        ).status_code
        == 200
    )
    (closing, greeting) = sorted(decision_rows(scored.item["id"]), key=lambda r: r.criterion_id)
    assert greeting.evidence == [{"turn_index": 3}]
    assert closing.evidence == [{"turn_index": index} for index in range(20)]


def test_an_identical_decision_writes_nothing(queue):
    scored = _scored_item(queue)
    first = decided(
        queue.sup, scored.item, "greeting", **accept(note=" ok ", reviewer_outcome="passed")
    )
    revision = first["revision"]
    events = len(decided_events(queue.workspace_id))
    for body in (
        accept(note="ok"),
        accept(note=" ok "),
        accept(note="ok", reviewer_outcome="passed"),
    ):
        again = decided(queue.sup, {**scored.item, "revision": revision}, "greeting", **body)
        assert again["revision"] == revision
    assert len(decided_events(queue.workspace_id)) == events

    # The same for an override: the same evidence in another order.
    second = decided(
        queue.sup,
        {**scored.item, "revision": revision},
        "fee_explained",
        **override("passed", reason="other", note="n", evidence=[4, 2]),
    )
    revision = second["revision"]
    events = len(decided_events(queue.workspace_id))
    again = decided(
        queue.sup,
        {**scored.item, "revision": revision},
        "fee_explained",
        **override("passed", reason="other", note="n", evidence=[2, 4]),
    )
    assert again["revision"] == revision
    assert len(decided_events(queue.workspace_id)) == events

    # A new assignee after a reassignment writes nothing either, and the first decider
    # stays on the row.
    (row,) = [r for r in decision_rows(scored.item["id"]) if r.criterion_id == "greeting"]
    first_decider = row.decided_by_user_id
    item = acted(
        queue.owner,
        {**scored.item, "revision": revision},
        "assign",
        assignee_user_id=queue.admin.user_id,
    )
    item = acted(queue.admin, item, "start")
    again = decided(queue.admin, item, "greeting", **accept(note="ok"))
    assert again["revision"] == item["revision"]
    (row,) = [r for r in decision_rows(scored.item["id"]) if r.criterion_id == "greeting"]
    assert row.decided_by_user_id == first_decider == queue.sup.user_id

    # Allow-control: a changed decision bumps the revision and writes one event.
    before = len(decided_events(queue.workspace_id))
    changed = decided(
        queue.admin,
        item,
        "greeting",
        **override("failed", reason="other", note="changed", evidence=[1]),
    )
    assert changed["revision"] == item["revision"] + 1
    assert len(decided_events(queue.workspace_id)) == before + 1


def test_a_result_outside_the_current_engine_version_is_refused(queue):
    scored = _scored_item(queue)
    base = _scored_result(queue, key="stale_version")
    with inspection_session_factory()() as db:
        row = db.get(ScorecardResultRecord, base["result_id"])
        stale = ScorecardResultRecord(
            workspace_id=row.workspace_id,
            meeting_id=row.meeting_id,
            scorecard_version_id=row.scorecard_version_id,
            transcript_revision_id=row.transcript_revision_id,
            scorecards_version="scorecards/0",
            checks_version=row.checks_version,
            definition_digest=row.definition_digest,
            input_digest=uuid.uuid4().hex * 2,
            conversation_type=row.conversation_type,
            direction=row.direction,
            outcome=row.outcome,
            reason=row.reason,
            complete=row.complete,
            pass_threshold=row.pass_threshold,
            applicable_weight=row.applicable_weight,
            earned_weight=row.earned_weight,
            failed_weight=row.failed_weight,
            insufficient_weight=row.insufficient_weight,
            check_run=row.check_run,
            result=row.result,
        )
        db.add(stale)
        db.commit()
        stale_id = stale.id
    item = created(
        queue.owner,
        base["meeting_id"],
        scorecard_result_id=stale_id,
        assignee_user_id=queue.sup.user_id,
    )
    item = acted(queue.sup, item, "start")
    response = decide(queue.sup, item, "greeting", **accept())
    assert response.status_code == 409 and code(response) == "scorecard_version_unsupported"
    refused = act(queue.sup, item, "complete")
    assert refused.status_code == 409 and code(refused) == "scorecard_version_unsupported"
    assert stored(item["id"]).status == "in_review"
    assert human_rows(item["id"]) == []
    # Allow-control: the current version decides and completes.
    current = decided(queue.sup, scored.item, "greeting", **accept())
    assert act(queue.sup, current, "complete").status_code == 200


# --- the criteria view --------------------------------------------------------------------


def test_the_criteria_response_has_exactly_the_spec_keys(queue):
    scored = _scored_item(queue)
    decided(queue.sup, scored.item, "greeting", **accept(note="ok"))
    decided(
        queue.sup,
        {**scored.item, "revision": scored.item["revision"] + 1},
        "fee_explained",
        **override("passed", reason="other", note="why", evidence=[2, 1]),
    )
    item = {**scored.item, "revision": scored.item["revision"] + 2}
    view = criteria(queue.sup, item["id"])
    assert set(view) == {
        "review_item_id",
        "revision",
        "status",
        "scorecard_result_id",
        "machine",
        "human",
        "criteria",
    }
    assert (view["review_item_id"], view["revision"], view["status"]) == (
        item["id"],
        item["revision"],
        "in_review",
    )
    assert view["scorecard_result_id"] == scored.result_id
    assert set(view["machine"]) == SCORE_KEYS and view["machine"]["outcome"] == "failed"
    assert set(view["human"]) == SCORE_KEYS | {"final"} and view["human"]["final"] is False
    assert [entry["criterion_id"] for entry in view["criteria"]] == [
        "greeting",
        "fee_explained",
        "closing",
        "off_topic",
        "agenda",
    ]
    for entry in view["criteria"]:
        assert set(entry) == CRITERION_KEYS
        assert set(entry["machine"]) == CRITERION_MACHINE_KEYS
        if entry["decision"] is not None:
            assert set(entry["decision"]) == DECISION_KEYS
    by_id = {entry["criterion_id"]: entry for entry in view["criteria"]}
    assert by_id["greeting"]["decision"]["note"] == "ok"
    assert by_id["greeting"]["decision"]["reason_code"] is None
    assert by_id["greeting"]["decision"]["evidence"] == []
    assert by_id["greeting"]["decision"]["decided_by_user_id"] == queue.sup.user_id
    assert by_id["fee_explained"]["decision"]["evidence"] == [
        {"turn_index": 1},
        {"turn_index": 2},
    ]
    for criterion, outcome in MACHINE_OUTCOMES.items():
        assert by_id[criterion]["machine"]["outcome"] == outcome
    assert by_id["agenda"]["decision"] is None
    assert by_id["closing"]["decision"] is None


def test_an_item_without_a_result_has_an_empty_criteria_view(queue):
    item = created(queue.owner, queue.m_a2, assignee_user_id=queue.sup.user_id)
    view = criteria(queue.sup, item["id"])
    assert set(view) == {
        "review_item_id",
        "revision",
        "status",
        "scorecard_result_id",
        "machine",
        "human",
        "criteria",
    }
    assert (view["machine"], view["human"], view["criteria"]) == (None, None, [])
    assert view["scorecard_result_id"] is None


def test_a_preview_the_engine_refuses_is_null(queue):
    scored = _scored_item(queue)
    base = _scored_result(queue, key="broken_run")
    with inspection_session_factory()() as db:
        row = db.get(ScorecardResultRecord, base["result_id"])
        broken = ScorecardResultRecord(
            workspace_id=row.workspace_id,
            meeting_id=row.meeting_id,
            scorecard_version_id=row.scorecard_version_id,
            transcript_revision_id=row.transcript_revision_id,
            scorecards_version=row.scorecards_version,
            checks_version=row.checks_version,
            definition_digest=row.definition_digest,
            input_digest=uuid.uuid4().hex * 2,
            conversation_type=row.conversation_type,
            direction=row.direction,
            outcome=row.outcome,
            reason=row.reason,
            complete=row.complete,
            pass_threshold=row.pass_threshold,
            applicable_weight=row.applicable_weight,
            earned_weight=row.earned_weight,
            failed_weight=row.failed_weight,
            insufficient_weight=row.insufficient_weight,
            check_run={**row.check_run, "checks_version": "conversation-checks/0"},
            result=row.result,
        )
        db.add(broken)
        db.commit()
        broken_id = broken.id
    item = created(
        queue.owner,
        base["meeting_id"],
        scorecard_result_id=broken_id,
        assignee_user_id=queue.sup.user_id,
    )
    item = acted(queue.sup, item, "start")
    view = criteria(queue.sup, item["id"])
    assert view["machine"] is not None and view["criteria"] == []
    assert view["human"] is None
    # The null comes from the engine refusing the stored run, not from a missing result.
    broken_scored = Scored(
        item=item,
        definition=DEFINITION,
        facts=scored.facts,
        run=scored.run,
        version_id=scored.version_id,
        result_id=broken_id,
        meeting_id=scored.meeting_id,
    )
    with pytest.raises(ScorecardError):
        _expected_human(broken_scored, item)


# --- completion and the human result ------------------------------------------------------


def test_the_machine_rows_stay_byte_identical_and_verify_still_holds(queue):
    scored = _scored_item(queue)
    before = _snapshot_result(scored.result_id)
    with _app_session(queue.workspace_id) as db:
        assert verify_result(db, workspace_id=queue.workspace_id, result_id=scored.result_id)
    item = decided(queue.sup, scored.item, "greeting", **accept())
    item = decided(
        queue.sup,
        item,
        "fee_explained",
        **override("passed", reason="other", note="why", evidence=[1]),
    )
    item = decided(queue.sup, item, "closing", **accept())
    completed = acted(queue.sup, item, "complete")
    assert completed["status"] == "completed"
    assert _snapshot_result(scored.result_id) == before
    with _app_session(queue.workspace_id) as db:
        assert verify_result(db, workspace_id=queue.workspace_id, result_id=scored.result_id)


def test_the_stored_human_result_equals_the_engine_and_flips_the_outcome(queue):
    scored = _scored_item(queue)
    item = decided(queue.sup, scored.item, "greeting", **accept(note="ok"))
    item = decided(
        queue.sup,
        item,
        "fee_explained",
        **override("passed", reason="other", note="why", evidence=[1]),
    )
    item = decided(
        queue.sup,
        item,
        "off_topic",
        **override("not_applicable", reason="policy_exception", note="na", evidence=[0]),
    )
    preview = criteria(queue.sup, item["id"])["human"]
    assert preview is not None and preview["final"] is False
    completed = acted(queue.sup, item, "complete")
    assert completed["status"] == "completed"

    expected = _expected_human(scored, item).to_json()
    (row,) = human_rows(item["id"])
    assert row.result == expected
    assert row.reviewer_outcomes == _reviewer_outcomes(scored, item)
    assert row.scorecards_version == SCORECARDS_VERSION
    for column in (
        "outcome",
        "reason",
        "complete",
        "pass_threshold",
        "applicable_weight",
        "earned_weight",
        "failed_weight",
        "insufficient_weight",
    ):
        assert getattr(row, column) == expected[column], column
    assert (row.workspace_id, row.meeting_id, row.scorecard_result_id) == (
        queue.workspace_id,
        scored.meeting_id,
        scored.result_id,
    )

    view = criteria(queue.sup, item["id"])
    assert view["machine"]["outcome"] == "failed" and view["machine"]["reason"] == "auto_fail"
    assert view["human"]["outcome"] == "passed" and view["human"]["reason"] is None
    assert view["human"]["final"] is True
    assert view["human"]["score"] == 100.0
    stored_view = {key: value for key, value in view["human"].items() if key != "final"}
    assert stored_view == {key: value for key, value in preview.items() if key != "final"}


def test_a_completed_item_without_decisions_stores_the_machine_result_again(queue):
    scored = _scored_item(queue)
    completed = acted(queue.sup, scored.item, "complete")
    assert completed["status"] == "completed"
    expected = _expected_human(scored, scored.item).to_json()
    (row,) = human_rows(scored.item["id"])
    assert row.result == expected
    assert row.reviewer_outcomes == {}
    machine = criteria(queue.sup, scored.item["id"])["machine"]
    human = criteria(queue.sup, scored.item["id"])["human"]
    assert {k: human[k] for k in SCORE_KEYS} == machine


def test_the_completed_audit_details_match_the_stored_human_result(queue):
    scored = _scored_item(queue)
    item = decided(queue.sup, scored.item, "greeting", **accept(note="ok"))
    item = decided(
        queue.sup,
        item,
        "fee_explained",
        **override("passed", reason="other", note="why", evidence=[1]),
    )
    item = decided(queue.sup, item, "closing", **accept())
    completed = acted(queue.sup, item, "complete")
    (row,) = human_rows(item["id"])
    machine = _snapshot_result(scored.result_id)
    (event,) = [
        event
        for event in review_events(queue.workspace_id)
        if event.event_type == "review_item.completed"
    ]
    assert set(event.details) == COMPLETED_EVENT_KEYS
    assert event.details == {
        "revision": completed["revision"],
        "scorecard_result_id": scored.result_id,
        "review_result_id": row.id,
        "machine_outcome": machine["outcome"],
        "machine_reason": machine["reason"],
        "human_outcome": row.outcome,
        "human_reason": row.reason,
        "applicable_weight": row.applicable_weight,
        "earned_weight": row.earned_weight,
        "failed_weight": row.failed_weight,
        "insufficient_weight": row.insufficient_weight,
        "accepted_count": 2,
        "overridden_count": 1,
    }


def test_the_decided_audit_event_and_never_the_note(queue):
    note = f"SECRET-NOTE-{uuid.uuid4().hex}"
    scored = _scored_item(queue)
    item = decided(queue.sup, scored.item, "greeting", **accept(note=f"  {note}  "))
    first_revision = item["revision"]
    item = decided(
        queue.sup,
        item,
        "greeting",
        **override("failed", reason="evidence_missed", note="changed", evidence=[2, 1]),
    )
    events = decided_events(queue.workspace_id)
    assert [event.details for event in events] == [
        {
            "criterion_id": "greeting",
            "scorecard_result_id": scored.result_id,
            "decision": "accept",
            "machine_outcome": "passed",
            "reviewer_outcome": "passed",
            "reason_code": None,
            "evidence_turn_indexes": [],
            "previous_decision": None,
            "previous_reviewer_outcome": None,
            "revision": first_revision,
        },
        {
            "criterion_id": "greeting",
            "scorecard_result_id": scored.result_id,
            "decision": "override",
            "machine_outcome": "passed",
            "reviewer_outcome": "failed",
            "reason_code": "evidence_missed",
            "evidence_turn_indexes": [1, 2],
            "previous_decision": "accept",
            "previous_reviewer_outcome": "passed",
            "revision": item["revision"],
        },
    ]
    assert all(event.target_type == "review_item" for event in events)
    assert all(event.actor_user_id == queue.sup.user_id for event in events)
    (row,) = decision_rows(item["id"])
    assert row.note == "changed"  # the note is stored, only the audit never sees it
    with inspection_session_factory()() as db:
        every = db.scalars(
            select(AuditEvent).where(AuditEvent.workspace_id == queue.workspace_id)
        ).all()
    assert all(note not in str(event.details) for event in every)


def test_no_human_result_without_a_result_or_on_cancel(queue):
    before = audit_count()
    item = acted(
        queue.sup,
        created(queue.owner, queue.m_a2, assignee_user_id=queue.sup.user_id),
        "start",
    )
    completed = acted(queue.sup, item, "complete")
    assert completed["status"] == "completed"
    (event,) = [
        event
        for event in review_events(queue.workspace_id)
        if event.event_type == "review_item.completed"
    ]
    assert event.details == {"revision": completed["revision"]}
    with inspection_session_factory()() as db:
        total = db.scalar(select(func.count()).select_from(ReviewResultRecord))
    assert total == 0
    assert audit_count() == before + 3  # created, started, completed

    scored = _scored_item(queue)
    item = decided(queue.sup, scored.item, "greeting", **accept())
    cancelled = acted(queue.owner, item, "cancel", reason="duplicate")
    assert cancelled["status"] == "cancelled"
    assert human_rows(item["id"]) == []
    view = criteria(queue.sup, item["id"])
    assert view["human"] is None and view["machine"] is not None


def test_a_refusal_writes_nothing_and_no_event(queue):
    scored = _scored_item(queue)
    events = audit_count()
    refusals = [
        decide(queue.sup, scored.item, "greeting", **accept(reason_code="other")),
        decide(queue.sup, scored.item, "greeting", **override("passed", evidence=[0])),
        decide(queue.sup, scored.item, "nope", **accept()),
        decide(queue.admin, scored.item, "greeting", **accept()),
        decide(queue.plain, scored.item, "greeting", **accept()),
    ]
    assert [response.status_code for response in refusals] == [422, 422, 404, 403, 404]
    assert decision_rows(scored.item["id"]) == []
    assert audit_count() == events
    assert stored(scored.item["id"]).revision == scored.item["revision"]
