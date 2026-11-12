"""HOM-76 PR 2: the in-app notifications of an appeal and the inbox routes (ADR 0057 D6).

Kinds and recipients: ``review_appeal.filed`` goes to every user who could be assigned
the appeal (owner, admin, or a member supervising the call's current team), minus the
appellant, the current agent and every original reviewer, at most 50 ordered by user
id; ``review_appeal.assigned`` to the new assignee; ``review_appeal.decided`` to the
appellant (none when the user was removed); ``review_appeal.withdrawn`` to the
assignee, if any.  At most one row per user, kind and appeal.  Titles and bodies are
fixed English texts; ``data`` holds ids only.

The inbox (``notification:read``, session callers only) shows the caller's own rows of
the current workspace, newest first, with keyset pages and ``unread_only``; marking a
row read is idempotent and another user's row is ``404 notification_not_found``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from backend.database.saas_models import Notification, ReviewAppeal
from backend.services import notification_service
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_review_appeals_api import (
    Queue,
    acted_on,
    assign,
    build_appeal_queue,
    change,
    code,
    decide,
    filed,
    reviewed,
    uphold,
)
from backend.tests.test_review_queue_access import stranger


@pytest.fixture
def queue(client, email_outbox, monkeypatch) -> Queue:
    return build_appeal_queue(client, email_outbox, monkeypatch)


NOTIFICATIONS = "/api/v1/notifications"
TEXTS = {
    "review_appeal.filed": (
        "Appeal filed",
        "An agent appealed a completed review. Open the appeals queue to assign it.",
    ),
    "review_appeal.assigned": ("Appeal assigned to you", "You were asked to decide an appeal."),
    "review_appeal.decided": (
        "Your appeal was decided",
        "A reviewer decided your appeal. Open it to see the result.",
    ),
    "review_appeal.withdrawn": (
        "Appeal withdrawn",
        "The agent withdrew an appeal assigned to you.",
    ),
}
VIEW_KEYS = {"id", "kind", "title", "body", "data", "read_at", "created_at"}


def rows(kind: str | None = None, appeal_id: str | None = None) -> list[Notification]:
    with inspection_session_factory()() as db:
        query = select(Notification).order_by(Notification.user_id, Notification.id)
        if kind is not None:
            query = query.where(Notification.kind == kind)
        found = list(db.scalars(query))
    if appeal_id is not None:
        found = [row for row in found if row.data.get("appeal_id") == appeal_id]
    return found


def recipients(kind: str, appeal_id: str) -> list[str]:
    """The users with a row, sorted (rows of one write share ``created_at``, so their
    order is proven through the cap instead)."""
    return sorted(row.user_id for row in rows(kind, appeal_id))


def assert_texts(row: Notification, appeal: dict) -> None:
    assert (row.title, row.body) == TEXTS[row.kind]
    assert row.data == {
        "appeal_id": appeal["id"],
        "review_item_id": appeal["review_item_id"],
        "meeting_id": appeal["meeting_id"],
    }
    assert row.read_at is None


# --- who gets which row ---------------------------------------------------------------------


def test_a_filed_appeal_notifies_every_possible_decider_except_the_parties(queue):
    appeal = filed(queue.agent, reviewed(queue).id)
    # Not the agent (appellant), not sup (original reviewer), not sup_b, lapsed or the
    # viewer (no review authority over the call).  The team's supervisors first, then
    # the owners and admins, each group by user id (SPEC-pr2 R11).
    expected = sorted([queue.plain.user_id, queue.owner.user_id, queue.admin.user_id])
    assert recipients("review_appeal.filed", appeal["id"]) == expected
    for row in rows("review_appeal.filed", appeal["id"]):
        assert_texts(row, appeal)
        assert row.workspace_id == queue.workspace_id
    assert rows("review_appeal.assigned") == []


def test_a_filed_appeal_skips_a_reviewer_who_only_decided_a_criterion(queue):
    appeal = filed(queue.agent, reviewed(queue, admin_decides=True).id)
    assert recipients("review_appeal.filed", appeal["id"]) == sorted(
        [queue.plain.user_id, queue.owner.user_id]
    )


def test_a_filed_appeal_notifies_supervisors_first_then_admins_up_to_the_cap(queue, monkeypatch):
    """The team's supervisors first, then the owners and admins, each group by user id,
    up to ``MAX_FILED_RECIPIENTS``."""
    monkeypatch.setattr(notification_service, "MAX_FILED_RECIPIENTS", 2)
    appeal = filed(queue.agent, reviewed(queue).id)
    # The cap keeps the team's supervisor first, then the first owner or admin by id.
    first_admin = min(queue.owner.user_id, queue.admin.user_id)
    assert recipients("review_appeal.filed", appeal["id"]) == sorted(
        [queue.plain.user_id, first_admin]
    )
    monkeypatch.setattr(notification_service, "MAX_FILED_RECIPIENTS", 1)
    second = filed(queue.agent, reviewed(queue).id)
    assert recipients("review_appeal.filed", second["id"]) == [queue.plain.user_id]
    monkeypatch.undo()
    assert notification_service.MAX_FILED_RECIPIENTS == 50


def test_an_assignment_notifies_the_new_assignee_once(queue):
    appeal = filed(queue.agent, reviewed(queue).id)
    appeal = assign(queue.owner, appeal, queue.plain)
    assert recipients("review_appeal.assigned", appeal["id"]) == [queue.plain.user_id]
    assert_texts(rows("review_appeal.assigned", appeal["id"])[0], appeal)
    # The same assignee again: no new row.  Away and back: still one row each.
    appeal = assign(queue.owner, appeal, queue.plain)
    appeal = assign(queue.owner, appeal, queue.admin)
    appeal = assign(queue.owner, appeal, queue.plain)
    assert recipients("review_appeal.assigned", appeal["id"]) == sorted(
        [queue.admin.user_id, queue.plain.user_id]
    )


def test_an_earlier_appeals_rows_never_stop_a_new_appeals_row_nor_allow_a_duplicate(queue):
    """The de-duplication reads only the rows written since this appeal was filed, then
    matches its ``appeal_id``: the same recipient and kind of an earlier appeal does not
    count, and the same appeal never gets a second row."""
    earlier = assign(queue.owner, filed(queue.agent, reviewed(queue).id), queue.plain)
    later = filed(queue.agent, reviewed(queue).id)
    with inspection_session_factory()() as db:
        filed_at = db.get(ReviewAppeal, later["id"]).created_at
        older = db.scalars(
            select(Notification).where(Notification.kind == "review_appeal.assigned")
        )
        for row in older:  # the earlier appeal's row is older than the later filing
            row.created_at = filed_at - timedelta(minutes=5)
        db.commit()
    later = assign(queue.owner, later, queue.plain)
    assert recipients("review_appeal.assigned", earlier["id"]) == [queue.plain.user_id]
    assert recipients("review_appeal.assigned", later["id"]) == [queue.plain.user_id]
    # Away and back on the same appeal: still one row for it.
    later = assign(queue.owner, later, queue.admin)
    later = assign(queue.owner, later, queue.plain)
    assert recipients("review_appeal.assigned", later["id"]) == sorted(
        [queue.admin.user_id, queue.plain.user_id]
    )
    assert len(rows("review_appeal.assigned")) == 3
    # Directly: a second notify of the same appeal, kind and user adds nothing.
    with inspection_session_factory()() as db:
        appeal = db.get(ReviewAppeal, later["id"])
        added = notification_service.notify_appeal(
            db,
            appeal=appeal,
            kind="review_appeal.assigned",
            user_ids=[queue.plain.user_id, queue.owner.user_id],
            now=datetime.now(UTC),
        )
        db.commit()
    assert added == [queue.owner.user_id]
    assert len(rows("review_appeal.assigned")) == 4


def test_a_decision_notifies_the_appellant(queue):
    appeal = filed(queue.agent, reviewed(queue).id, ["greeting"])
    appeal = assign(queue.owner, appeal, queue.plain)
    decide(queue.plain, appeal, [change("greeting", "failed")])
    assert recipients("review_appeal.decided", appeal["id"]) == [queue.agent.user_id]
    assert_texts(rows("review_appeal.decided", appeal["id"])[0], appeal)


def test_a_decision_notifies_nobody_when_the_appellant_was_removed(queue):
    appeal = filed(queue.agent, reviewed(queue).id, ["greeting"])
    appeal = assign(queue.owner, appeal, queue.plain)
    with inspection_session_factory()() as db:
        db.execute(
            update(ReviewAppeal)
            .where(ReviewAppeal.id == appeal["id"])
            .values(appellant_user_id=None)
        )
        db.commit()
    response = decide(queue.plain, appeal, [uphold("greeting")])
    assert response.status_code == 200, response.text
    assert rows("review_appeal.decided") == []


def test_a_withdrawal_notifies_the_assignee_if_any(queue):
    unassigned = filed(queue.agent, reviewed(queue).id)
    acted_on(queue.agent, unassigned, "withdraw")
    assert rows("review_appeal.withdrawn") == []
    appeal = assign(queue.owner, filed(queue.agent, reviewed(queue).id), queue.plain)
    acted_on(queue.agent, appeal, "withdraw")
    assert recipients("review_appeal.withdrawn", appeal["id"]) == [queue.plain.user_id]
    assert_texts(rows("review_appeal.withdrawn", appeal["id"])[0], appeal)


# --- the inbox ----------------------------------------------------------------------------------


def inbox(persona, **params) -> dict:
    response = persona.get(NOTIFICATIONS, params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor"}
    return body


def test_the_inbox_shows_own_rows_of_this_workspace_newest_first(client, email_outbox, queue):
    first = filed(queue.agent, reviewed(queue).id)
    second = filed(queue.agent, reviewed(queue).id)
    with inspection_session_factory()() as db:
        older = db.scalar(
            select(Notification)
            .where(
                Notification.user_id == queue.owner.user_id,
                Notification.kind == "review_appeal.filed",
            )
            .order_by(Notification.created_at.asc(), Notification.id.asc())
        )
        assert older is not None and older.data["appeal_id"] == first["id"]
        older.created_at = datetime.now(UTC) - timedelta(minutes=5)
        db.commit()
    other = stranger(client, email_outbox, "appeal-inbox-stranger@example.com")
    with inspection_session_factory()() as db:
        foreign = Notification(
            workspace_id=other.workspace_id,
            user_id=queue.owner.user_id,
            kind="review_appeal.filed",
            title="Appeal filed",
            body="x",
            data={},
        )
        db.add(foreign)
        db.commit()
        foreign_id = foreign.id
    items = inbox(queue.owner)["items"]
    assert [item["data"]["appeal_id"] for item in items] == [second["id"], first["id"]]
    for item in items:
        assert set(item) == VIEW_KEYS
        assert datetime.fromisoformat(item["created_at"]).utcoffset() == timedelta(0)
    assert foreign_id not in {item["id"] for item in items}
    # Keyset pages.
    page = inbox(queue.owner, limit=1)
    assert [item["data"]["appeal_id"] for item in page["items"]] == [second["id"]]
    rest = inbox(queue.owner, limit=1, cursor=page["next_cursor"])
    assert [item["data"]["appeal_id"] for item in rest["items"]] == [first["id"]]
    assert rest["next_cursor"] is None
    bad = queue.owner.get(NOTIFICATIONS, params={"cursor": "x"})
    assert bad.status_code == 400 and code(bad) == "invalid_cursor"
    # The agent filed both and is notified of neither.
    assert inbox(queue.agent)["items"] == []
    # The foreign row cannot be marked from this workspace.
    response = queue.owner.post(f"{NOTIFICATIONS}/{foreign_id}/read")
    assert response.status_code == 404 and code(response) == "notification_not_found"


def test_marking_read_is_idempotent_and_only_for_the_owner_of_the_row(queue):
    filed(queue.agent, reviewed(queue).id)
    filed(queue.agent, reviewed(queue).id)
    items = inbox(queue.owner)["items"]
    target = items[0]
    # Another user's row.
    for persona in (queue.admin, queue.agent):
        response = persona.post(f"{NOTIFICATIONS}/{target['id']}/read")
        assert response.status_code == 404 and code(response) == "notification_not_found"
    response = queue.owner.post(f"{NOTIFICATIONS}/{target['id']}/read")
    assert response.status_code == 200, response.text
    read = response.json()
    assert set(read) == VIEW_KEYS and read["read_at"] is not None
    again = queue.owner.post(f"{NOTIFICATIONS}/{target['id']}/read").json()
    assert again["read_at"] == read["read_at"]
    unread = inbox(queue.owner, unread_only="true")["items"]
    assert [item["id"] for item in unread] == [items[1]["id"]]
    assert len(inbox(queue.owner, unread_only="false")["items"]) == 2
    missing = queue.owner.post(f"{NOTIFICATIONS}/missing/read")
    assert missing.status_code == 404 and code(missing) == "notification_not_found"


def test_the_inbox_is_for_sessions_only_and_marking_needs_csrf(client, queue):
    filed(queue.agent, reviewed(queue).id)
    (row,) = [item for item in inbox(queue.owner)["items"]]
    owner = queue.owner
    every_scope = sorted(["meeting:read", "member:read", "team:read", "audit:read"])
    key = _mint(client, owner.headers, owner.cookies, name="inbox", scopes=every_scope)
    assert key.status_code == 201, key.text
    headers = _bearer(key.json()["key"], **{"X-Workspace-ID": owner.workspace_id})
    for method, path in (("GET", NOTIFICATIONS), ("POST", f"{NOTIFICATIONS}/{row['id']}/read")):
        response = client.request(method, path, headers=headers)
        assert response.status_code == 403, (method, path, response.text)
        assert code(response) == "api_key_scope_denied"
    response = owner.client.post(
        f"{NOTIFICATIONS}/{row['id']}/read",
        cookies=owner.cookies,
        headers={"X-Workspace-ID": owner.workspace_id},
    )
    assert response.status_code == 403 and code(response) == "csrf_failed"
    assert inbox(owner)["items"][0]["read_at"] is None
    # Every role reads its own inbox, a viewer too.
    assert inbox(queue.viewer)["items"] == []


@pytest.mark.parametrize("limit", [0, 101])
def test_the_inbox_limit_is_one_to_a_hundred(queue, limit):
    assert queue.owner.get(NOTIFICATIONS, params={"limit": limit}).status_code == 422
