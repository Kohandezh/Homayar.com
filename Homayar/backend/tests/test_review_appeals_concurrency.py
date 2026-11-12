"""HOM-76 PR 2: concurrent appeal writes (PostgreSQL only).

Assign, decide and withdraw are a compare-and-set on the appeal's revision, so of two
writes that read the same revision exactly one lands; the other answers ``409
review_appeal_revision_conflict`` (or ``409 review_appeal_closed`` when the winner closed
the appeal).  Two filings of one item race on ``uq_review_appeals_item``: one 201, one
``409 appeal_exists``.  The losers write no row, no audit event and no notification.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from backend.database.saas_models import (
    AuditEvent,
    Notification,
    ReviewAppeal,
    ReviewAppealResult,
)
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_review_appeals_api import (
    Queue,
    act,
    appeal_events,
    assign,
    build_appeal_queue,
    change,
    code,
    decide,
    file,
    filed,
    reviewed,
    stored_appeal,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def queue(client, email_outbox, monkeypatch) -> Queue:
    return build_appeal_queue(client, email_outbox, monkeypatch)


def _requires_postgres() -> None:
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")


def _together(*calls) -> list:
    from backend.tests.test_review_queue_concurrency import _together as together

    return together(*calls)


def _count(model, **where) -> int:
    with inspection_session_factory()() as db:
        query = select(func.count()).select_from(model)
        for column, value in where.items():
            query = query.where(getattr(model, column) == value)
        return db.scalar(query) or 0


def test_two_decides_with_the_same_revision_exactly_one_lands(queue):
    _requires_postgres()
    appeal = filed(queue.agent, reviewed(queue).id, ["greeting"])
    appeal = assign(queue.owner, appeal, queue.plain)
    responses = _together(
        lambda: decide(queue.plain, appeal, [change("greeting", "failed")]),
        lambda: decide(queue.plain, appeal, [change("greeting", "failed")]),
    )
    assert sorted(response.status_code for response in responses) == [200, 409], responses
    (loser,) = [response for response in responses if response.status_code == 409]
    assert code(loser) == "review_appeal_revision_conflict"
    assert loser.json()["error"]["details"] == {"current_revision": appeal["revision"] + 1}
    assert stored_appeal(appeal["id"]).status == "decided"
    assert _count(ReviewAppealResult, appeal_id=appeal["id"]) == 1
    types = [event.event_type for event in appeal_events(appeal["id"])]
    assert types.count("review_appeal.decided") == 1
    assert _count(Notification, kind="review_appeal.decided") == 1


def test_two_assigns_with_the_same_revision_exactly_one_lands(queue):
    _requires_postgres()
    appeal = filed(queue.agent, reviewed(queue).id)
    responses = _together(
        lambda: act(queue.owner, appeal, "assign", assignee_user_id=queue.plain.user_id),
        lambda: act(queue.owner, appeal, "assign", assignee_user_id=queue.admin.user_id),
    )
    assert sorted(response.status_code for response in responses) == [200, 409], responses
    (loser,) = [response for response in responses if response.status_code == 409]
    assert code(loser) == "review_appeal_revision_conflict"
    assert stored_appeal(appeal["id"]).revision == 2
    types = [event.event_type for event in appeal_events(appeal["id"])]
    assert types.count("review_appeal.assigned") == 1
    assert _count(Notification, kind="review_appeal.assigned") == 1


def test_a_decide_and_a_withdraw_never_both_land(queue):
    _requires_postgres()
    appeal = filed(queue.agent, reviewed(queue).id, ["greeting"])
    appeal = assign(queue.owner, appeal, queue.plain)
    responses = _together(
        lambda: decide(queue.plain, appeal, [change("greeting", "failed")]),
        lambda: act(queue.agent, appeal, "withdraw"),
    )
    assert sorted(response.status_code for response in responses) == [200, 409], responses
    (loser,) = [response for response in responses if response.status_code == 409]
    assert code(loser) in {"review_appeal_revision_conflict", "review_appeal_closed"}
    status = stored_appeal(appeal["id"]).status
    assert status in {"decided", "withdrawn"}
    results = _count(ReviewAppealResult, appeal_id=appeal["id"])
    assert results == (1 if status == "decided" else 0)
    types = [event.event_type for event in appeal_events(appeal["id"])]
    closing = [t for t in types if t in {"review_appeal.decided", "review_appeal.withdrawn"}]
    assert len(closing) == 1


def test_two_filings_of_one_item_exactly_one_lands(queue):
    _requires_postgres()
    target = reviewed(queue)
    events_before = _count(AuditEvent, event_type="review_appeal.filed")
    responses = _together(
        lambda: file(queue.agent, target.id, ["greeting"]),
        lambda: file(queue.agent, target.id, ["off_topic"]),
    )
    assert sorted(response.status_code for response in responses) == [201, 409], responses
    (loser,) = [response for response in responses if response.status_code == 409]
    assert code(loser) == "appeal_exists"
    assert _count(ReviewAppeal, review_item_id=target.id) == 1
    assert _count(AuditEvent, event_type="review_appeal.filed") == events_before + 1
    # One appeal's filed notifications: owner, admin and plain, once each.
    assert _count(Notification, kind="review_appeal.filed") == 3
