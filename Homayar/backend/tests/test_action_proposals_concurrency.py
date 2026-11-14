"""HOM-135 PR 2: concurrent proposal writes (PostgreSQL only, SPEC-pr2 section 6).

Every write locks the proposal row (``SELECT ... FOR UPDATE``) and checks the status and
the revision under the lock, so of two writes that read the same proposal exactly one
lands and the other answers ``409``.  Two creates for one source and action type race on
``uq_action_proposals_open_item``: one ``201``, one ``409 action_proposal_exists``.  An
approval writes exactly one delivery request, whatever runs next to it.
"""

from __future__ import annotations

import pytest

from backend.tests._database import configured_database_url, is_postgres_url
from backend.tests.test_action_proposals_api import (
    Queue,
    act,
    approve,
    create,
    crm_body,
    crm_on,
    crm_setup,
    events,
    requests_of,
    stored,
)
from backend.tests.test_review_appeals_api import build_appeal_queue
from backend.tests.test_review_queue_access import code

pytestmark = pytest.mark.postgres

STATE_CODES = {"action_proposal_not_pending", "action_proposal_revision_conflict"}


@pytest.fixture
def queue(client, email_outbox, monkeypatch) -> Queue:
    return build_appeal_queue(client, email_outbox, monkeypatch)


def _requires_postgres() -> None:
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")


def _together(*calls) -> list:
    from backend.tests.test_review_queue_concurrency import _together as together

    return together(*calls)


def _successes(proposal_id: str, event_type: str) -> int:
    return sum(
        1
        for event in events(proposal_id)
        if event.event_type == event_type and event.outcome == "success"
    )


def test_two_approves_at_once_give_one_201_one_409_and_one_request(queue):
    _requires_postgres()
    proposal = crm_on(queue)
    responses = _together(
        lambda: approve(queue.owner, proposal),
        lambda: approve(queue.admin, proposal),
    )
    statuses = sorted(response.status_code for response in responses)
    assert statuses == [201, 409], [response.text for response in responses]
    (loser,) = [response for response in responses if response.status_code == 409]
    assert code(loser) in STATE_CODES, loser.text
    assert len(requests_of(proposal["id"])) == 1
    row = stored(proposal["id"])
    assert (row.status, row.revision) == ("approved", 2)
    assert _successes(proposal["id"], "action_proposal.approved") == 1


def test_an_approve_and_a_reject_never_both_land(queue):
    _requires_postgres()
    proposal = crm_on(queue)
    responses = _together(
        lambda: approve(queue.owner, proposal),
        lambda: act(queue.admin, proposal, "reject", reason="incorrect"),
    )
    statuses = sorted(response.status_code for response in responses)
    assert statuses in ([200, 409], [201, 409]), [response.text for response in responses]
    (loser,) = [response for response in responses if response.status_code == 409]
    assert code(loser) in STATE_CODES, loser.text
    row = stored(proposal["id"])
    assert row.revision == 2
    if row.status == "approved":
        assert len(requests_of(proposal["id"])) == 1
    else:
        assert row.status == "rejected"
        assert requests_of(proposal["id"]) == []


def test_two_creates_for_one_source_and_type_give_one_201_and_one_409(queue):
    _requires_postgres()
    setup = crm_setup(queue)
    body = crm_body(setup["item_id"], setup["record_id"])
    responses = _together(
        lambda: create(queue.sup, body),
        lambda: create(queue.plain, body),
    )
    statuses = sorted(response.status_code for response in responses)
    assert statuses == [201, 409], [response.text for response in responses]
    (loser,) = [response for response in responses if response.status_code == 409]
    assert code(loser) == "action_proposal_exists", loser.text
    (winner,) = [response for response in responses if response.status_code == 201]
    listed = queue.owner.get("/api/v1/action-proposals").json()["items"]
    assert [row["id"] for row in listed] == [winner.json()["id"]]


def test_an_approve_with_the_old_hash_never_lands_after_the_refresh(queue):
    _requires_postgres()
    proposal = crm_on(queue)
    responses = _together(
        lambda: approve(queue.owner, proposal),
        lambda: act(queue.sup, proposal, "refresh"),
    )
    approve_response, refresh_response = responses
    row = stored(proposal["id"])
    if refresh_response.status_code == 200:
        # The refresh committed first: the approve of the old revision is refused.
        assert approve_response.status_code == 409, approve_response.text
        assert code(approve_response) == "action_proposal_revision_conflict"
        assert (row.status, row.payload_version) == ("pending", 2)
        assert requests_of(proposal["id"]) == []
    else:
        # The approve committed first: the refresh of an approved proposal is refused.
        assert approve_response.status_code == 201, approve_response.text
        assert refresh_response.status_code == 409, refresh_response.text
        assert code(refresh_response) in STATE_CODES
        assert (row.status, row.payload_version) == ("approved", 1)
        assert len(requests_of(proposal["id"])) == 1
