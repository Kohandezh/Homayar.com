"""HOM-73 PR 2: concurrent writes on PostgreSQL (DESIGN §5.2, §5.5; criteria 20 and 21).

* Two writes with the same ``expected_revision``: the compare-and-set lets exactly one
  succeed; the other is ``409 review_item_revision_conflict``.
* Two creates for one conversation: the partial unique index lets exactly one in; the
  other is ``409 review_item_exists`` with the winner's id, never a 500.

Each rule is shown twice: a race of two requests released together, and a fixed order
where a second session holds the row (or the index entry) until the request waits on it.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from backend.database.relational import get_session_factory
from backend.database.saas_models import ReviewItem
from backend.database.tenant_context import bind_tenant_context
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_review_queue_access import (
    Queue,
    act,
    build_queue,
    code,
    create,
    created,
    due,
    review_events,
    stored,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def queue(client, email_outbox, monkeypatch) -> Queue:
    return build_queue(client, email_outbox, monkeypatch)


def _requires_postgres() -> None:
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")


def _together(*calls) -> list:
    """Run the calls in threads released at the same moment; their results in order."""
    barrier = threading.Barrier(len(calls))
    results: list = [None] * len(calls)

    def run(index: int, call) -> None:
        barrier.wait(timeout=15)
        try:
            results[index] = call()
        except BaseException as exc:  # surfaced by the assertions
            results[index] = exc

    threads = [threading.Thread(target=run, args=pair) for pair in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert all(not thread.is_alive() for thread in threads), "a request hung"
    return results


def _items_of(meeting_id: str) -> list[str]:
    with inspection_session_factory()() as db:
        return list(db.scalars(select(ReviewItem.id).where(ReviewItem.meeting_id == meeting_id)))


def test_two_writes_with_the_same_revision_exactly_one_succeeds(queue):
    _requires_postgres()
    item = created(queue.owner, queue.m_a2)
    for round_ in range(5):
        responses = _together(
            lambda item=item: act(queue.owner, item, "due-time", due_at=due(timedelta(days=3))),
            lambda item=item: act(queue.admin, item, "due-time", due_at=due(timedelta(days=4))),
        )
        assert sorted(r.status_code for r in responses) == [200, 409], responses
        (winner,) = [r for r in responses if r.status_code == 200]
        (loser,) = [r for r in responses if r.status_code == 409]
        assert code(loser) == "review_item_revision_conflict"
        assert loser.json()["error"]["details"] == {"current_revision": item["revision"] + 1}
        item = winner.json()
        assert item["revision"] == round_ + 2
    assert stored(item["id"]).revision == 6
    events = [
        e for e in review_events(queue.workspace_id) if e.event_type == "review_item.due_changed"
    ]
    assert len(events) == 5


def test_a_write_waiting_on_a_concurrent_change_gets_the_conflict(queue):
    """A second session moves the revision and holds the row; the request read the old
    revision, waits on the row lock, and its update then matches no row."""
    _requires_postgres()
    item = created(queue.owner, queue.m_a2)
    responses: list = []
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=queue.workspace_id)
        db.execute(
            update(ReviewItem)
            .where(ReviewItem.id == item["id"])
            .values(revision=ReviewItem.revision + 1, updated_at=datetime.now(UTC))
        )
        request = threading.Thread(
            target=lambda: responses.append(act(queue.owner, item, "cancel"))
        )
        request.start()
        time.sleep(1.0)
        assert request.is_alive(), responses  # waiting on the held row
        db.commit()
    request.join(timeout=30)
    (response,) = responses
    assert response.status_code == 409 and code(response) == "review_item_revision_conflict"
    assert response.json()["error"]["details"] == {"current_revision": 2}
    assert stored(item["id"]).status == "open"


def test_two_creates_for_one_conversation_one_201_one_409(queue):
    _requires_postgres()
    for _ in range(5):
        meeting = queue.meeting(team_id=queue.team_a)
        responses = _together(
            lambda meeting=meeting: create(queue.owner, meeting),
            lambda meeting=meeting: create(queue.admin, meeting),
        )
        assert sorted(r.status_code for r in responses) == [201, 409], responses
        (winner,) = [r for r in responses if r.status_code == 201]
        (loser,) = [r for r in responses if r.status_code == 409]
        assert code(loser) == "review_item_exists"
        assert loser.json()["error"]["details"] == {"review_item_id": winner.json()["id"]}
        assert _items_of(meeting) == [winner.json()["id"]]


def test_a_create_waiting_on_the_index_gets_the_409(queue):
    """A second session inserts the active item and holds it uncommitted; the request's
    check misses it, its insert waits on the partial unique index, then fails."""
    _requires_postgres()
    meeting = queue.meeting(team_id=queue.team_a)
    responses: list = []
    now = datetime.now(UTC)
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=queue.workspace_id)
        held = ReviewItem(
            workspace_id=queue.workspace_id,
            meeting_id=meeting,
            source="manual",
            source_details={},
            status="open",
            due_at=now + timedelta(days=1),
            created_by_user_id=queue.owner.user_id,
            created_at=now,
            updated_at=now,
            revision=1,
        )
        db.add(held)
        db.flush()
        request = threading.Thread(target=lambda: responses.append(create(queue.owner, meeting)))
        request.start()
        time.sleep(1.0)
        assert request.is_alive(), responses  # waiting on the index entry
        db.commit()
        held_id = held.id
    request.join(timeout=30)
    (response,) = responses
    assert response.status_code == 409, response.text
    assert code(response) == "review_item_exists"
    assert response.json()["error"]["details"] == {"review_item_id": held_id}
    with inspection_session_factory()() as db:
        count = db.scalar(
            select(func.count()).select_from(ReviewItem).where(ReviewItem.meeting_id == meeting)
        )
    assert count == 1
