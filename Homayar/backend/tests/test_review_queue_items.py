"""HOM-73 PR 2: the review queue's items, list and actions (DESIGN §5, §6).

* create (201, every response field), one active item per conversation (``409
  review_item_exists``), a scorecard result of the same conversation only, the due time
  rule (UTC offset, later than now, at most 90 days ahead);
* the list: default statuses, filters, ``due_at`` then ``id`` order, keyset pages,
  ``is_overdue``;
* assign, due-time, start, complete and cancel, each after the common checks: visible,
  ``expected_revision`` equal to the stored revision (else ``409
  review_item_revision_conflict`` and nothing changes), the item active;
* one audit event per successful change, none for a refusal or the no-change assign.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from backend.database.relational import get_session_factory
from backend.database.saas_models import Meeting, ReviewItem, ScorecardResultRecord
from backend.database.tenant_context import bind_tenant_context
from backend.jobs.leases import as_utc
from backend.services import review_queue_service
from backend.services.review_queue_service import ReviewQueueError, compare_and_set
from backend.tests.test_review_queue_access import (
    ACTIONS,
    ITEMS,
    Queue,
    act,
    acted,
    assert_utc,
    audit_count,
    build_queue,
    code,
    create,
    created,
    due,
    item_count,
    listed,
    listed_ids,
    review_events,
    set_item,
    stored,
)
from backend.tests.test_scorecard_results_migration import _add, _parents, _result


@pytest.fixture
def queue(client, email_outbox, monkeypatch) -> Queue:
    return build_queue(client, email_outbox, monkeypatch)


RESPONSE_FIELDS = {
    "id",
    "meeting_id",
    "team_id",
    "scorecard_result_id",
    "source",
    "source_details",
    "status",
    "assignee_user_id",
    "assigned_by_user_id",
    "assigned_at",
    "due_at",
    "is_overdue",
    "created_by_user_id",
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
    "cancelled_at",
    "cancel_reason",
    "revision",
}


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    assert parsed.utcoffset() == timedelta(0), value  # returned in UTC
    return parsed


def _events(queue, event_type: str) -> list:
    return [e for e in review_events(queue.workspace_id) if e.event_type == event_type]


# --- create (criteria 11, 12, 13) -------------------------------------------------------


def test_a_manual_create_returns_every_field(queue):
    due_at = datetime.now(UTC) + timedelta(days=2)
    body = created(queue.owner, queue.m_a, due_at=due_at.isoformat())
    assert set(body) == RESPONSE_FIELDS
    assert body["meeting_id"] == queue.m_a
    assert body["team_id"] == queue.team_a
    assert (body["source"], body["source_details"], body["status"]) == ("manual", {}, "open")
    assert body["revision"] == 1 and body["is_overdue"] is False
    assert body["scorecard_result_id"] is None
    assert (body["assignee_user_id"], body["assigned_by_user_id"], body["assigned_at"]) == (
        None,
        None,
        None,
    )
    assert body["created_by_user_id"] == queue.owner.user_id
    assert _time(body["due_at"]) == due_at
    assert _time(body["created_at"]) == _time(body["updated_at"])
    for name in ("started_at", "completed_at", "cancelled_at", "cancel_reason"):
        assert body[name] is None, name
    assert queue.owner.get(f"{ITEMS}/{body['id']}").json() == body

    with_assignee = created(queue.owner, queue.m_a2, assignee_user_id=queue.sup.user_id)
    assert with_assignee["assignee_user_id"] == queue.sup.user_id
    assert with_assignee["assigned_by_user_id"] == queue.owner.user_id
    assert _time(with_assignee["assigned_at"]) == _time(with_assignee["created_at"])
    assert with_assignee["status"] == "open" and with_assignee["revision"] == 1


def test_a_supervisor_creates_an_item_on_a_conversation_of_their_team(queue):
    body = created(queue.sup, queue.m_a2, assignee_user_id=queue.sup.user_id)
    assert body["created_by_user_id"] == queue.sup.user_id
    assert body["assigned_by_user_id"] == queue.sup.user_id
    refused = create(queue.sup, queue.m_b)
    assert refused.status_code == 404 and code(refused) == "meeting_not_found"


def test_a_conversation_has_one_active_item_at_a_time(queue):
    first = created(queue.owner, queue.m_a)
    refused = create(queue.owner, queue.m_a)
    assert refused.status_code == 409 and code(refused) == "review_item_exists"
    assert refused.json()["error"]["details"] == {"review_item_id": first["id"]}

    # ``in_review`` is active too.
    first = acted(queue.owner, first, "assign", assignee_user_id=queue.owner.user_id)
    first = acted(queue.owner, first, "start")
    refused = create(queue.owner, queue.m_a)
    assert refused.status_code == 409 and code(refused) == "review_item_exists"

    acted(queue.owner, first, "complete")
    second = created(queue.owner, queue.m_a)  # after a completed one
    acted(queue.owner, second, "cancel", reason="out_of_scope")
    third = created(queue.owner, queue.m_a)  # after a cancelled one
    assert len({first["id"], second["id"], third["id"]}) == 3
    # Another conversation is never blocked.
    assert create(queue.owner, queue.m_a2).status_code == 201


def test_a_create_that_loses_the_index_race_is_the_same_409(queue, monkeypatch):
    """The pre-check misses an item a concurrent request committed; the partial unique
    index refuses the insert and the answer is the 409, never a 500."""
    first = created(queue.owner, queue.m_a)
    real = review_queue_service.active_item_id
    calls = []

    def racing(db, **kwargs):
        calls.append(kwargs)
        return None if len(calls) == 1 else real(db, **kwargs)

    monkeypatch.setattr(review_queue_service, "active_item_id", racing)
    refused = create(queue.owner, queue.m_a)
    assert refused.status_code == 409 and code(refused) == "review_item_exists"
    assert refused.json()["error"]["details"] == {"review_item_id": first["id"]}
    assert len(calls) == 2 and item_count() == 1
    assert len(_events(queue, "review_item.created")) == 1


def test_an_item_names_only_a_scorecard_result_of_its_conversation(client, email_outbox, queue):
    tenant = {"workspace_id": queue.workspace_id, "user_id": queue.owner.user_id}
    x = _parents(tenant, key="review_x")
    y = _parents(tenant, key="review_y")
    result_x = _add(ScorecardResultRecord, _result(x))
    result_y = _add(ScorecardResultRecord, _result(y))
    from backend.tests.test_review_queue_access import stranger

    other = stranger(client, email_outbox, "review-results-other@example.com")
    z = _parents({"workspace_id": other.workspace_id, "user_id": other.user_id}, key="review_z")
    result_z = _add(ScorecardResultRecord, _result(z))
    for result_id in (result_x, result_z, "00000000-0000-4000-8000-000000000000"):
        refused = create(queue.owner, y["meeting_id"], scorecard_result_id=result_id)
        assert refused.status_code == 422, refused.text
        assert code(refused) == "review_scorecard_result_invalid"
    assert item_count() == 0
    body = created(queue.owner, y["meeting_id"], scorecard_result_id=result_y)
    assert body["scorecard_result_id"] == result_y
    assert (
        created(queue.owner, x["meeting_id"], scorecard_result_id=None)["scorecard_result_id"]
        is None
    )


# --- the due time rule (criterion 14) ----------------------------------------------------


def _refused_due_times() -> list[str]:
    now = datetime.now(UTC)
    return [
        (now + timedelta(days=1)).replace(tzinfo=None).isoformat(),  # naive
        now.isoformat(),  # now
        (now - timedelta(hours=1)).isoformat(),  # the past
        (now + timedelta(days=90, minutes=1)).isoformat(),  # more than 90 days ahead
    ]


def test_the_due_time_rule_on_create(queue):
    for due_at in _refused_due_times():
        refused = create(queue.owner, queue.meeting(), due_at=due_at)
        assert refused.status_code == 422, (due_at, refused.text)
        assert code(refused) == "review_due_at_invalid", due_at
    assert item_count() == 0
    now = datetime.now(UTC)
    for due_at in (now + timedelta(minutes=1), now + timedelta(days=90)):
        body = created(queue.owner, queue.meeting(), due_at=due_at.isoformat())
        assert _time(body["due_at"]) == due_at
    tehran = timezone(timedelta(hours=3, minutes=30))
    local = (now + timedelta(days=5)).astimezone(tehran)
    body = created(queue.owner, queue.meeting(), due_at=local.isoformat())
    assert _time(body["due_at"]) == local  # the same instant, in UTC


def test_the_due_time_rule_on_the_due_time_action(queue):
    item = created(queue.owner, queue.m_a)
    for due_at in _refused_due_times():
        refused = act(queue.owner, item, "due-time", due_at=due_at)
        assert refused.status_code == 422 and code(refused) == "review_due_at_invalid", due_at
    assert stored(item["id"]).revision == 1
    assert _events(queue, "review_item.due_changed") == []
    now = datetime.now(UTC)
    for due_at in (now + timedelta(minutes=1), now + timedelta(days=90)):
        before = item
        item = acted(queue.owner, item, "due-time", due_at=due_at.isoformat())
        assert _time(item["due_at"]) == due_at
        assert item["revision"] == before["revision"] + 1
    events = _events(queue, "review_item.due_changed")
    assert [e.details["revision"] for e in events] == [2, 3]
    assert _time(events[1].details["previous_due_at"]) == now + timedelta(minutes=1)
    assert _time(events[1].details["due_at"]) == now + timedelta(days=90)
    assert {e.target_type for e in events} == {"review_item"}


def _instant() -> tuple[datetime, str, list[object]]:
    """A valid due time, the same instant as an ISO string with ``Z``, and the values that
    are not a date-time string (DESIGN §9.7, §9.10, §9.12)."""
    instant = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)
    seconds = int(instant.timestamp())
    refused = [
        seconds,  # Unix seconds as a JSON number
        float(seconds),
        True,
        None,
        {"at": instant.isoformat()},
        [instant.isoformat()],
        str(seconds),  # a string that is only a number: Unix seconds
        f"{seconds}.5",
        "in two days",
        instant.strftime("%Y%m%dT%H%M%SZ"),  # ISO 8601 basic format
        instant.strftime("%G-W%V-%uT%H:%M:%SZ"),  # an ISO week date
    ]
    return instant, instant.strftime("%Y-%m-%dT%H:%M:%SZ"), refused


def test_due_at_must_be_an_iso_string_on_create_and_due_time(queue):
    instant, iso, refused = _instant()
    item = created(queue.owner, queue.m_a)
    for value in refused:
        response = create(queue.owner, queue.meeting(), due_at=value)
        assert response.status_code == 422, (value, response.text)
        assert code(response) == "review_due_at_invalid", value
        response = act(queue.owner, item, "due-time", due_at=value)
        assert response.status_code == 422, (value, response.text)
        assert code(response) == "review_due_at_invalid", value
    assert item_count() == 1 and stored(item["id"]).revision == 1
    # Allow-control: the same instant as an ISO 8601 string with ``Z``.
    assert _time(created(queue.owner, queue.meeting(), due_at=iso)["due_at"]) == instant
    moved = acted(queue.owner, item, "due-time", due_at=iso)
    assert _time(moved["due_at"]) == instant and moved["revision"] == 2
    # Pydantic's parsing takes lowercase ``t`` and ``z`` too.
    lower = instant.strftime("%Y-%m-%dt%H:%M:%Sz")
    assert _time(created(queue.owner, queue.meeting(), due_at=lower)["due_at"]) == instant


def test_the_api_schema_names_due_at_a_date_time_string(client):
    document = client.app.openapi()["components"]["schemas"]
    for model in (
        "ReviewItemCreateRequest",
        "ReviewItemDueTimeRequest",
        "ReviewSampleEnqueueRequest",
    ):
        schema = document[model]["properties"]["due_at"]
        assert (schema["type"], schema["format"]) == ("string", "date-time"), model
        assert "due_at" in document[model]["required"], model


# --- the list (criteria 15, 16) ----------------------------------------------------------


def test_the_list_filters(queue):
    owner = queue.owner
    open_ = created(owner, queue.meeting(), due_at=due(timedelta(days=1)))
    mine = created(owner, queue.meeting(), assignee_user_id=owner.user_id)
    supervised = created(owner, queue.m_a2, assignee_user_id=queue.sup.user_id)
    started = acted(queue.sup, supervised, "start")
    done = acted(
        owner,
        acted(owner, created(owner, queue.meeting(), assignee_user_id=owner.user_id), "start"),
        "complete",
    )
    dropped = acted(owner, created(owner, queue.meeting()), "cancel")

    assert set(listed_ids(owner)) == {open_["id"], mine["id"], started["id"]}  # the default
    assert listed_ids(owner, status="completed") == [done["id"]]
    assert set(listed_ids(owner, status=["open", "cancelled"])) == {
        open_["id"],
        mine["id"],
        dropped["id"],
    }
    assert listed_ids(owner, assignee="me") == [mine["id"]]
    assert set(listed_ids(owner, assignee="unassigned")) == {open_["id"]}
    assert listed_ids(owner, assignee=queue.sup.user_id) == [started["id"]]
    assert listed_ids(owner, meeting_id=queue.m_a2) == [started["id"]]
    assert listed_ids(queue.sup, assignee="me") == [started["id"]]

    set_item(open_["id"], due_at=datetime.now(UTC) - timedelta(hours=1))
    set_item(done["id"], due_at=datetime.now(UTC) - timedelta(hours=1))
    assert listed_ids(owner, overdue="true") == [open_["id"]]
    assert set(listed_ids(owner, overdue="false")) == {mine["id"], started["id"]}
    statuses = ["open", "in_review", "completed", "cancelled"]
    assert set(listed_ids(owner, overdue="false", status=statuses)) == {
        mine["id"],
        started["id"],
        done["id"],  # a closed item is never overdue
        dropped["id"],
    }

    for params in (
        {"status": "done"},
        {"status": ["open", "Open"]},
        {"assignee": "someone"},
        {"assignee": queue.sup.user_id.upper()},
        {"meeting_id": "not-a-uuid"},
        {"meeting_id": queue.m_a2.upper()},
        {"overdue": "maybe"},
    ):
        refused = owner.get(ITEMS, params=params)
        assert refused.status_code == 422, (params, refused.text)
        assert code(refused) == "review_filter_invalid", params


def test_is_overdue_is_true_only_for_an_active_item_past_its_due_time(queue):
    owner = queue.owner
    late_open = created(owner, queue.meeting())
    late_review = acted(
        owner, created(owner, queue.meeting(), assignee_user_id=owner.user_id), "start"
    )
    late_done = acted(owner, late_review, "complete")
    late_review = acted(
        owner, created(owner, queue.meeting(), assignee_user_id=owner.user_id), "start"
    )
    late_cancelled = acted(owner, created(owner, queue.meeting()), "cancel")
    on_time = created(owner, queue.meeting())
    past = datetime.now(UTC) - timedelta(minutes=5)
    for item in (late_open, late_review, late_done, late_cancelled):
        set_item(item["id"], due_at=past)
    flags = {
        item["id"]: item["is_overdue"]
        for item in listed(owner, status=["open", "in_review", "completed", "cancelled"])
    }
    assert flags == {
        late_open["id"]: True,
        late_review["id"]: True,
        late_done["id"]: False,
        late_cancelled["id"]: False,
        on_time["id"]: False,
    }
    assert owner.get(f"{ITEMS}/{late_open['id']}").json()["is_overdue"] is True


def test_the_list_order_and_its_pages(queue):
    owner = queue.owner
    items = [created(owner, queue.meeting(), due_at=due(timedelta(days=day))) for day in (3, 1, 2)]
    tied = [created(owner, queue.meeting()) for _ in range(3)]
    tie = datetime.now(UTC) + timedelta(days=4)
    for item in tied:
        set_item(item["id"], due_at=tie)
    rows = [stored(item["id"]) for item in items + tied]
    expected = [row.id for row in sorted(rows, key=lambda row: (as_utc(row.due_at), row.id))]
    assert listed_ids(owner) == expected

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        response = owner.get(ITEMS, params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) <= 2
        seen += [item["id"] for item in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == expected and pages == 3

    jobs_cursor = (
        base64.urlsafe_b64encode(
            json.dumps({"v": 1, "created_at": tie.isoformat(), "id": tied[0]["id"]}).encode()
        )
        .decode()
        .rstrip("=")
    )
    wrong_version = (
        base64.urlsafe_b64encode(
            json.dumps({"v": 2, "due_at": tie.isoformat(), "id": tied[0]["id"]}).encode()
        )
        .decode()
        .rstrip("=")
    )
    for cursor in ("not-a-cursor", jobs_cursor, wrong_version, "e30"):
        refused = owner.get(ITEMS, params={"cursor": cursor})
        assert refused.status_code == 400 and code(refused) == "invalid_cursor", cursor
    for limit in (0, 101):
        assert owner.get(ITEMS, params={"limit": limit}).status_code == 422
    assert owner.get(ITEMS, params={"limit": 100}).status_code == 200


# --- assign (criterion 17) ---------------------------------------------------------------


def test_assign_reassign_and_unassign(queue):
    owner, sup, admin = queue.owner, queue.sup, queue.admin
    item = created(owner, queue.m_a2)
    item = acted(owner, item, "assign", assignee_user_id=sup.user_id)
    assert (item["assignee_user_id"], item["assigned_by_user_id"]) == (sup.user_id, owner.user_id)
    assert item["assigned_at"] is not None and item["status"] == "open" and item["revision"] == 2

    item = acted(sup, item, "start")
    assert item["status"] == "in_review" and item["started_at"] is not None
    # A different person: back to open, started_at cleared.
    item = acted(owner, item, "assign", assignee_user_id=admin.user_id)
    assert (item["status"], item["started_at"], item["assignee_user_id"]) == (
        "open",
        None,
        admin.user_id,
    )
    assert item["revision"] == 4

    # The same person: no change, no new revision, no audit event.
    events = len(review_events(queue.workspace_id))
    same = acted(owner, item, "assign", assignee_user_id=admin.user_id)
    assert same == {**item, "is_overdue": same["is_overdue"]} and same["revision"] == 4
    assert len(review_events(queue.workspace_id)) == events

    # Unassign an in_review item: back to open, every assigned_* field cleared.
    item = acted(admin, item, "start")
    item = acted(owner, item, "assign", assignee_user_id=None)
    assert (
        item["assignee_user_id"],
        item["assigned_by_user_id"],
        item["assigned_at"],
        item["status"],
        item["started_at"],
    ) == (None, None, None, "open", None)
    assert item["revision"] == 6
    # Null and null: no change either.
    assert acted(owner, item, "assign", assignee_user_id=None)["revision"] == 6
    assert len(review_events(queue.workspace_id)) == events + 2

    assigned = _events(queue, "review_item.assigned")
    for event in assigned:
        assert set(event.details) == {"previous_assignee_user_id", "assignee_user_id", "revision"}
    assert [
        (
            e.details["previous_assignee_user_id"],
            e.details["assignee_user_id"],
            e.details["revision"],
        )
        for e in assigned
    ] == [
        (None, sup.user_id, 2),
        (sup.user_id, admin.user_id, 4),
        (admin.user_id, None, 6),
    ]
    # The key is required.
    missing = owner.post(f"{ITEMS}/{item['id']}/assign", json={"expected_revision": 6})
    assert missing.status_code == 422


def test_one_now_per_request_for_every_timestamp_it_sets(queue):
    """DESIGN §9.1: the request's ``now`` is ``updated_at`` and the timestamp the action
    sets, and every datetime comes back with a zero UTC offset."""
    owner, sup = queue.owner, queue.sup
    item = created(owner, queue.m_a2, assignee_user_id=sup.user_id)
    assert item["created_at"] == item["updated_at"] == item["assigned_at"]
    item = acted(owner, item, "assign", assignee_user_id=queue.admin.user_id)
    assert item["updated_at"] == item["assigned_at"] != item["created_at"]
    item = acted(owner, item, "assign", assignee_user_id=sup.user_id)
    item = acted(sup, item, "start")
    assert item["updated_at"] == item["started_at"]
    item = acted(sup, item, "complete")
    assert item["updated_at"] == item["completed_at"]
    assert_utc(item)
    cancelled = acted(owner, created(owner, queue.meeting()), "cancel")
    assert cancelled["updated_at"] == cancelled["cancelled_at"]
    assert_utc(cancelled)
    moved = acted(owner, created(owner, queue.meeting()), "due-time")
    assert _time(moved["updated_at"]) > _time(moved["created_at"])
    assert_utc(moved)


# --- start, complete, cancel (criteria 18, 19) ------------------------------------------


def _set_agent(meeting_id: str, user_id: str) -> None:
    from backend.tests._database import inspection_session_factory

    with inspection_session_factory()() as db:
        db.execute(update(Meeting).where(Meeting.id == meeting_id).values(agent_user_id=user_id))
        db.commit()


def test_the_assignee_check_comes_before_the_agent_check(queue):
    """An admin who is the conversation's current agent but not the assignee is refused
    as a non-assignee."""
    item = created(queue.owner, queue.m_a, assignee_user_id=queue.sup.user_id)
    _set_agent(queue.m_a, queue.admin.user_id)
    refused = act(queue.admin, item, "start")
    assert refused.status_code == 403 and code(refused) == "review_not_assignee"
    assert stored(item["id"]).status == "open"


def test_the_current_agent_still_changes_the_due_time_and_cancels(queue):
    """DESIGN §9.5 covers start and complete only: an admin who is the conversation's
    current agent may still change the due time and cancel the item."""
    owner, admin = queue.owner, queue.admin
    item = acted(owner, created(owner, queue.m_a), "assign", assignee_user_id=admin.user_id)
    _set_agent(queue.m_a, admin.user_id)
    item = acted(admin, item, "due-time")
    assert item["revision"] == 3
    item = acted(admin, item, "cancel", reason="other")
    assert (item["status"], item["revision"]) == ("cancelled", 4)


def test_start_and_complete_refuse_the_conversations_current_agent(queue):
    """An admin sees every item, so only start and complete stop an admin who became the
    conversation's agent after the assignment (DESIGN §9.5)."""
    owner, admin, agent = queue.owner, queue.admin, queue.agent
    item = acted(owner, created(owner, queue.m_a), "assign", assignee_user_id=admin.user_id)
    events = len(review_events(queue.workspace_id))

    _set_agent(queue.m_a, admin.user_id)
    refused = act(admin, item, "start")
    assert refused.status_code == 422 and code(refused) == "review_assignee_is_agent"
    refused = act(owner, item, "start")  # the assignee check comes first
    assert refused.status_code == 403 and code(refused) == "review_not_assignee"
    assert (stored(item["id"]).status, stored(item["id"]).revision) == ("open", 2)
    _set_agent(queue.m_a, agent.user_id)
    item = acted(admin, item, "start")  # allow-control: the agent is still X
    assert item["status"] == "in_review"

    _set_agent(queue.m_a, admin.user_id)
    refused = act(admin, item, "complete")
    assert refused.status_code == 422 and code(refused) == "review_assignee_is_agent"
    refused = act(admin, item, "start")  # before the state check: not 409
    assert refused.status_code == 422 and code(refused) == "review_assignee_is_agent"
    assert (stored(item["id"]).status, stored(item["id"]).revision) == ("in_review", 3)
    _set_agent(queue.m_a, agent.user_id)
    item = acted(admin, item, "complete")
    assert item["status"] == "completed" and item["revision"] == 4
    # Only the two allowed changes wrote an event.
    assert len(review_events(queue.workspace_id)) == events + 2


def test_only_the_assignee_starts_and_completes(queue):
    owner, sup, admin = queue.owner, queue.sup, queue.admin
    item = created(owner, queue.m_a2, assignee_user_id=sup.user_id)
    for persona in (owner, admin):  # visible, but not the assignee
        refused = act(persona, item, "start")
        assert refused.status_code == 403 and code(refused) == "review_not_assignee"
    refused = act(sup, item, "complete")  # open, not in review
    assert refused.status_code == 409 and code(refused) == "review_item_state_invalid"
    unassigned = created(owner, queue.m_a)
    refused = act(sup, unassigned, "start")
    assert refused.status_code == 403 and code(refused) == "review_not_assignee"

    item = acted(sup, item, "start")
    assert item["status"] == "in_review" and item["revision"] == 2
    refused = act(sup, item, "start")
    assert refused.status_code == 409 and code(refused) == "review_item_state_invalid"
    refused = act(owner, item, "complete")
    assert refused.status_code == 403 and code(refused) == "review_not_assignee"

    item = acted(sup, item, "complete")
    assert item["status"] == "completed" and item["completed_at"] is not None
    assert item["started_at"] is not None and item["revision"] == 3
    for persona in (owner, sup):
        for action in ACTIONS:
            refused = act(persona, item, action)
            assert refused.status_code == 409 and code(refused) == "review_item_closed", action
    assert stored(item["id"]).revision == 3
    started = _events(queue, "review_item.started")
    completed = _events(queue, "review_item.completed")
    assert [e.details for e in started] == [{"revision": 2}]
    assert [e.details for e in completed] == [{"revision": 3}]
    assert started[0].actor_user_id == sup.user_id


def test_cancel_needs_a_reason_code_and_closes_the_item(queue):
    owner = queue.owner
    item = created(owner, queue.m_a2, assignee_user_id=queue.sup.user_id)
    item = acted(queue.sup, item, "start")
    path = f"{ITEMS}/{item['id']}/cancel"
    for body in (
        {"expected_revision": 2},
        {"expected_revision": 2, "reason": "spam"},
        {"expected_revision": 2, "reason": None},
        {"expected_revision": 2, "reason": "duplicate", "note": "x"},
    ):
        assert owner.post(path, json=body).status_code == 422, body
    assert stored(item["id"]).revision == 2

    cancelled = acted(owner, item, "cancel", reason="meeting_unusable")
    assert (cancelled["status"], cancelled["cancel_reason"]) == ("cancelled", "meeting_unusable")
    assert cancelled["cancelled_at"] is not None and cancelled["revision"] == 3
    assert cancelled["started_at"] == item["started_at"]  # kept
    for action in ACTIONS:
        refused = act(owner, cancelled, action)
        assert refused.status_code == 409 and code(refused) == "review_item_closed", action
    (event,) = _events(queue, "review_item.cancelled")
    assert event.details == {"reason": "meeting_unusable", "revision": 3}
    for reason in ("duplicate", "out_of_scope", "other"):
        assert (
            acted(owner, created(owner, queue.meeting()), "cancel", reason=reason)["cancel_reason"]
            == reason
        )


# --- stale writes (criterion 20) --------------------------------------------------------


def test_every_action_with_a_stale_revision_is_a_409_and_changes_nothing(queue):
    owner, sup = queue.owner, queue.sup
    item = created(owner, queue.m_a2, assignee_user_id=sup.user_id)
    item = acted(owner, item, "due-time")  # revision 2
    events = audit_count()
    snapshot = owner.get(f"{ITEMS}/{item['id']}").json()
    for persona, action in (
        (owner, "assign"),
        (owner, "due-time"),
        (sup, "start"),
        (sup, "complete"),
        (owner, "cancel"),
    ):
        for stale in (1, 3, 99):
            refused = act(persona, {**item, "revision": stale}, action)
            assert refused.status_code == 409, (action, stale, refused.text)
            assert code(refused) == "review_item_revision_conflict"
            assert refused.json()["error"]["details"] == {"current_revision": 2}
    assert owner.get(f"{ITEMS}/{item['id']}").json() == snapshot
    assert audit_count() == events

    # The revision check comes before the closed check.
    done = acted(sup, acted(sup, item, "start"), "complete")
    refused = act(owner, {**done, "revision": 1}, "cancel")
    assert refused.status_code == 409 and code(refused) == "review_item_revision_conflict"
    assert refused.json()["error"]["details"] == {"current_revision": done["revision"]}
    # Visibility comes before both.
    refused = act(queue.sup_b, {**done, "revision": 1}, "cancel")
    assert refused.status_code == 404 and code(refused) == "review_item_not_found"


def test_expected_revision_is_a_strict_positive_int(queue):
    item = created(queue.owner, queue.m_a2)
    path = f"{ITEMS}/{item['id']}/start"
    for value in (0, -1, True, "1", 1.0, None):
        response = queue.owner.post(path, json={"expected_revision": value})
        assert response.status_code == 422, (value, response.text)
        assert code(response) == "validation_error", value
    for path, body in (
        (
            f"{ITEMS}/{item['id']}/assign",
            {"expected_revision": 1, "assignee_user_id": None, "x": 1},
        ),
        (ITEMS, {"meeting_id": queue.m_a, "due_at": due(), "extra": True}),
    ):
        assert queue.owner.post(path, json=body).status_code == 422


def test_every_successful_change_adds_exactly_one_to_the_revision(queue):
    owner, sup = queue.owner, queue.sup
    item = created(owner, queue.m_a2)
    revisions = [item["revision"]]
    for persona, action, body in (
        (owner, "assign", {"assignee_user_id": sup.user_id}),
        (owner, "due-time", {}),
        (sup, "start", {}),
        (sup, "complete", {}),
    ):
        item = acted(persona, item, action, **body)
        revisions.append(item["revision"])
    assert revisions == [1, 2, 3, 4, 5]
    assert stored(item["id"]).revision == 5


def test_the_compare_and_set_refuses_a_revision_that_moved_since_the_read(queue):
    """The guard behind every write: a concurrent change between the read and the
    update makes the update change no row, and the answer is the conflict."""
    item = created(queue.owner, queue.m_a2)
    factory = get_session_factory()
    with factory() as db:
        bind_tenant_context(db, workspace_id=queue.workspace_id)
        row = db.scalar(
            select(ReviewItem).where(
                ReviewItem.id == item["id"], ReviewItem.workspace_id == queue.workspace_id
            )
        )
        set_item(item["id"], revision=2)  # another writer, after the read
        with pytest.raises(ReviewQueueError) as refused:
            compare_and_set(
                db, row, expected_revision=1, now=datetime.now(UTC), values={"cancel_reason": None}
            )
        assert refused.value.code == "review_item_revision_conflict"
        assert refused.value.details == {"current_revision": 2}
        db.rollback()
    assert stored(item["id"]).revision == 2


# --- audit (criterion 23) ---------------------------------------------------------------


def test_each_change_writes_one_audit_event_and_a_refusal_none(queue, audit_event_guard):
    owner, sup = queue.owner, queue.sup
    item = created(owner, queue.m_a2, assignee_user_id=sup.user_id)
    (event,) = review_events(queue.workspace_id)
    assert (event.event_type, event.target_type, event.target_id) == (
        "review_item.created",
        "review_item",
        item["id"],
    )
    assert event.actor_user_id == owner.user_id
    assert set(event.details) == {"meeting_id", "source", "due_at", "assignee_user_id"}
    assert event.details["meeting_id"] == queue.m_a2 and event.details["source"] == "manual"
    assert event.details["assignee_user_id"] == sup.user_id
    assert _time(event.details["due_at"]) == _time(item["due_at"])

    before = audit_count()
    refusals = [
        create(owner, queue.m_a2),  # 409 exists
        create(owner, queue.meeting(), due_at="2026-01-01T00:00:00"),  # 422 due time
        act(owner, {**item, "revision": 9}, "cancel"),  # 409 conflict
        act(owner, item, "start"),  # 403 not the assignee
        act(owner, item, "assign", assignee_user_id=queue.plain.user_id),  # 422 ineligible
        act(queue.sup_b, item, "cancel"),  # 404
        act(owner, item, "assign", assignee_user_id=sup.user_id),  # 200, no change
    ]
    assert [response.status_code for response in refusals] == [409, 422, 409, 403, 422, 404, 200]
    assert audit_count() == before

    item = acted(owner, item, "due-time")
    item = acted(sup, item, "start")
    item = acted(sup, item, "complete")
    events = review_events(queue.workspace_id)
    assert [e.event_type for e in events] == [
        "review_item.created",
        "review_item.due_changed",
        "review_item.started",
        "review_item.completed",
    ]
    assert all(e.target_type == "review_item" and e.target_id == item["id"] for e in events)
    assert set(events[1].details) == {"previous_due_at", "due_at", "revision"}
    assert audit_count() == before + 3
    assert audit_event_guard == []
