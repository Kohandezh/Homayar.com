"""HOM-74 PR 2: two concurrent decisions on one item (PostgreSQL only).

Both requests pass every check with the same ``expected_revision``; the item's
compare-and-set lets exactly one land (200 with the criteria view), the other answers
``409 review_item_revision_conflict``.  Exactly one decision row and one
``review_criterion.decided`` audit event exist afterwards.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from backend.database.saas_models import ReviewCriterionDecision, ReviewItem
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_review_decisions_api import (
    Queue,
    accept,
    build_queue,
    code,
    decided_events,
    decision_rows,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def queue(client, email_outbox, monkeypatch) -> Queue:
    return build_queue(client, email_outbox, monkeypatch)


def _requires_postgres() -> None:
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")


def _together(*calls) -> list:
    from backend.tests.test_review_queue_concurrency import _together as together

    return together(*calls)


def _decide(queue: Queue, item: dict, criterion_id: str):
    from backend.tests.test_review_decisions_api import decide

    return decide(queue.sup, item, criterion_id, **accept())


def test_two_decisions_with_the_same_revision_exactly_one_lands(queue):
    _requires_postgres()
    from backend.tests.test_review_decisions_api import _scored_item

    scored = _scored_item(queue)
    responses = _together(
        lambda: _decide(queue, scored.item, "greeting"),
        lambda: _decide(queue, scored.item, "greeting"),
    )
    assert sorted(response.status_code for response in responses) == [200, 409], responses
    (winner,) = [response for response in responses if response.status_code == 200]
    (loser,) = [response for response in responses if response.status_code == 409]
    assert code(loser) == "review_item_revision_conflict"
    assert loser.json()["error"]["details"] == {"current_revision": scored.item["revision"] + 1}
    body = winner.json()
    assert body["revision"] == scored.item["revision"] + 1
    with inspection_session_factory()() as db:
        revision = db.scalar(
            select(ReviewItem.revision).where(ReviewItem.id == scored.item["id"])
        )
        decisions = db.scalar(
            select(func.count())
            .select_from(ReviewCriterionDecision)
            .where(ReviewCriterionDecision.review_item_id == scored.item["id"])
        )
    assert (revision, decisions) == (scored.item["revision"] + 1, 1)
    rows = decision_rows(scored.item["id"])
    assert [row.criterion_id for row in rows] == ["greeting"]
    events = decided_events(queue.workspace_id)
    assert len(events) == 1 and events[0].details["revision"] == body["revision"]


def test_two_different_criteria_with_the_same_revision_exactly_one_lands(queue):
    """Two decisions on different criteria of one item still race on the item's
    revision: only one lands, the other must reload and retry."""
    _requires_postgres()
    from backend.tests.test_review_decisions_api import _scored_item

    scored = _scored_item(queue)
    from backend.tests.test_review_decisions_api import override

    def decide_override():
        from backend.tests.test_review_decisions_api import decide

        return decide(
            queue.sup, scored.item, "fee_explained", **override("passed", evidence=[0])
        )

    responses = _together(
        lambda: _decide(queue, scored.item, "greeting"),
        decide_override,
    )
    assert sorted(response.status_code for response in responses) == [200, 409], responses
    assert len(decision_rows(scored.item["id"])) == 1
    assert len(decided_events(queue.workspace_id)) == 1
