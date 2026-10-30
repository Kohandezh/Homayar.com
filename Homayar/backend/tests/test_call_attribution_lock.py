"""HOM-360: changing a call's team or agent is its own right (ADR 0052).

The review queue (HOM-73, ADR 0051) decides who sees and works a review from the call's
*current* ``team_id`` and ``agent_user_id``.  ``PATCH /meetings/{id}`` (and its alias
``PATCH /conversations/{id}``) may change them only under these rules:

* a value equal to the current one is not a change: it needs no right, is not checked
  again, is not written and is not in the audit ``fields`` (R3);
* a real change needs ``meeting:attribute`` (owner, admin; never a member, a viewer or
  an API key), else ``403 attribution_change_denied`` and nothing is written (R1, R2);
* nobody changes the attribution of their own call or makes themselves its agent, owners
  and admins too: ``403 attribution_own_call`` (R4);
* every accepted change writes ``meeting.attribution_changed`` with ids only (R6).

Every case runs on a call with no review item, with an ``open`` item and with an
``in_review`` item.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from backend.database.saas_models import ApiKey, AuditEvent, Meeting
from backend.security.authorization import (
    API_KEY_SCOPES,
    ROLE_PERMISSIONS,
    Permission,
    WorkspaceRole,
)
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _insert_meeting, _Persona
from backend.tests.test_review_queue_access import (
    ITEMS,
    Queue,
    acted,
    build_queue,
    code,
    create,
    created,
    stored,
    stranger,
)

MEETINGS = "/api/v1/meetings"
CONVERSATIONS = "/api/v1/conversations"
ATTRIBUTION_CHANGED = "meeting.attribution_changed"
REVIEW_STATES = ("none", "open", "in_review")


@pytest.fixture
def queue(client, email_outbox, monkeypatch) -> Queue:
    return build_queue(client, email_outbox, monkeypatch)


@pytest.fixture(params=REVIEW_STATES)
def review_state(request) -> str:
    return request.param


# --- helpers ------------------------------------------------------------------------------


def with_review(queue: Queue, meeting_id: str, state: str) -> dict | None:
    """No item, or an active item (``open`` or ``in_review``) on the call, made by the owner."""
    if state == "none":
        return None
    item = created(queue.owner, meeting_id, assignee_user_id=queue.owner.user_id)
    if state == "in_review":
        item = acted(queue.owner, item, "start")
    assert stored(item["id"]).status == state
    return item


def team_scope(queue: Queue) -> None:
    switched = queue.owner.patch("/api/v1/admin/workspace-settings", json={"record_scope": "team"})
    assert switched.status_code == 200 and switched.json()["record_scope"] == "team"


def second_admin(queue: Queue) -> _Persona:
    """``lapsed`` promoted to admin: another admin than ``queue.admin``."""
    promoted = queue.owner.patch(
        f"/api/v1/members/{queue.membership_ids['lapsed']}", json={"role": "admin"}
    )
    assert promoted.status_code == 200, promoted.text
    return queue.lapsed


def patch(persona: _Persona, meeting_id: str, body: dict, *, base: str = MEETINGS):
    return persona.patch(f"{base}/{meeting_id}", json=body)


def snapshot(meeting_id: str) -> tuple:
    """The call's title, team and agent, and how many audit events name it."""
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        assert meeting is not None
        events = db.scalars(
            select(AuditEvent.id).where(
                AuditEvent.target_type == "meeting", AuditEvent.target_id == meeting_id
            )
        ).all()
        return (meeting.title, meeting.team_id, meeting.agent_user_id, len(events))


def events(meeting_id: str, event_type: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.event_type == event_type,
                    AuditEvent.target_type == "meeting",
                    AuditEvent.target_id == meeting_id,
                )
                .order_by(AuditEvent.occurred_at.asc())
            )
        )


def assert_refused(response, expected_code: str, meeting_id: str, before: tuple) -> None:
    """``403 <expected_code>``; no field of the call changed and no audit event names it."""
    assert response.status_code == 403, response.text
    assert code(response) == expected_code, response.text
    assert snapshot(meeting_id) == before


def assert_attributed(
    response,
    meeting_id: str,
    *,
    actor: _Persona,
    before: tuple[str | None, str | None],
    after: tuple[str | None, str | None],
    item: dict | None,
) -> None:
    """``200``, the call carries ``after`` and one more ``meeting.attribution_changed``
    names it, with all four ids and the active review item (``None`` without one)."""
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["team_id"], body["agent_user_id"]) == after
    assert snapshot(meeting_id)[1:3] == after
    event = events(meeting_id, ATTRIBUTION_CHANGED)[-1]
    assert event.actor_user_id == actor.user_id
    assert event.workspace_id == actor.workspace_id
    assert event.details == {
        "team_id_before": before[0],
        "team_id_after": after[0],
        "agent_user_id_before": before[1],
        "agent_user_id_after": after[1],
        "active_review_item_id": item["id"] if item is not None else None,
    }
    names = ("team_id", "agent_user_id")
    changed = [name for name, old, new in zip(names, before, after, strict=True) if old != new]
    assert events(meeting_id, "meeting.updated")[-1].details == {"fields": ",".join(changed)}


# --- R1: the permission -------------------------------------------------------------------


def test_meeting_attribute_is_owner_and_admin_only_and_never_an_api_key_scope():
    assert Permission.MEETING_ATTRIBUTE.value == "meeting:attribute"
    holders = {
        role
        for role, granted in ROLE_PERMISSIONS.items()
        if Permission.MEETING_ATTRIBUTE in granted
    }
    assert holders == {WorkspaceRole.OWNER, WorkspaceRole.ADMIN}
    assert Permission.MEETING_ATTRIBUTE not in API_KEY_SCOPES
    # Allow-control: meeting:update stays with members.
    assert Permission.MEETING_UPDATE in ROLE_PERMISSIONS[WorkspaceRole.MEMBER]


# --- deny cases (SPEC §3, D1 to D9) -------------------------------------------------------


def test_d1_an_agent_who_supervises_the_team_cannot_clear_themselves(queue, review_state):
    """Path 1, team scope: ``sup`` is the agent of ``m_sup_own`` and supervises its team."""
    team_scope(queue)
    meeting = queue.m_sup_own
    item = with_review(queue, meeting, review_state)
    before = snapshot(meeting)

    refused = patch(queue.sup, meeting, {"agent_user_id": None})
    assert_refused(refused, "attribution_change_denied", meeting, before)
    if item is not None:
        hidden = queue.sup.get(f"{ITEMS}/{item['id']}")
        assert hidden.status_code == 404 and code(hidden) == "review_item_not_found"

    # Allow-control: an admin who is not the agent clears it.
    accepted = patch(queue.admin, meeting, {"agent_user_id": None})
    assert_attributed(
        accepted,
        meeting,
        actor=queue.admin,
        before=(queue.team_a, queue.sup.user_id),
        after=(queue.team_a, None),
        item=item,
    )


def test_d2_a_member_cannot_clear_another_calls_agent(queue, review_state):
    """Path 1, flat workspace: ``plain`` reads ``m_a`` and clears its agent."""
    meeting = queue.m_a
    item = with_review(queue, meeting, review_state)
    before = snapshot(meeting)

    refused = patch(queue.plain, meeting, {"agent_user_id": None})
    assert_refused(refused, "attribution_change_denied", meeting, before)

    accepted = patch(queue.admin, meeting, {"agent_user_id": None})
    assert_attributed(
        accepted,
        meeting,
        actor=queue.admin,
        before=(queue.team_a, queue.agent.user_id),
        after=(queue.team_a, None),
        item=item,
    )


def test_d3_a_record_scoped_supervisor_cannot_move_a_call_into_their_team(queue, review_state):
    """Path 2, team scope: ``sup`` uploaded a team B call and moves it into team A."""
    team_scope(queue)
    meeting = _insert_meeting(
        queue.workspace_id, created_by=queue.sup.user_id, title="Uploaded", team_id=queue.team_b
    )
    item = with_review(queue, meeting, review_state)
    assert queue.sup.get(f"{MEETINGS}/{meeting}").status_code == 200  # the uploader reads it
    before = snapshot(meeting)

    refused = patch(queue.sup, meeting, {"team_id": queue.team_a})
    assert_refused(refused, "attribution_change_denied", meeting, before)
    # No review authority came with the attempt.
    if item is not None:
        hidden = queue.sup.get(f"{ITEMS}/{item['id']}")
        assert hidden.status_code == 404 and code(hidden) == "review_item_not_found"
    denied = create(queue.sup, meeting)
    assert denied.status_code == 404 and code(denied) == "meeting_not_found"

    accepted = patch(queue.admin, meeting, {"team_id": queue.team_a})
    assert_attributed(
        accepted,
        meeting,
        actor=queue.admin,
        before=(queue.team_b, None),
        after=(queue.team_a, None),
        item=item,
    )
    # ADR 0051 D4: authority follows the new team.
    if item is not None:
        assert queue.sup.get(f"{ITEMS}/{item['id']}").status_code == 200
        assert queue.sup_b.get(f"{ITEMS}/{item['id']}").status_code == 404


def test_d4_a_flat_workspace_member_cannot_move_a_call_to_gain_review_authority(
    queue, review_state
):
    """Path 3, flat workspace: ``sup`` supervises team A and moves team B's call into it,
    then tries to create and work its review.  Also before any review exists."""
    meeting = queue.m_b
    item = with_review(queue, meeting, review_state)
    before = snapshot(meeting)

    for persona in (queue.sup, queue.plain):
        refused = patch(persona, meeting, {"team_id": queue.team_a})
        assert_refused(refused, "attribution_change_denied", meeting, before)
    assert snapshot(meeting)[1] == queue.team_b
    denied = create(queue.sup, meeting)
    assert denied.status_code == 404 and code(denied) == "meeting_not_found"
    if item is not None:
        hidden = queue.sup.get(f"{ITEMS}/{item['id']}")
        assert hidden.status_code == 404 and code(hidden) == "review_item_not_found"
        worked = queue.sup.post(
            f"{ITEMS}/{item['id']}/assign",
            json={"expected_revision": item["revision"], "assignee_user_id": queue.sup.user_id},
        )
        assert worked.status_code == 404 and code(worked) == "review_item_not_found"
        assert stored(item["id"]).revision == item["revision"]

    accepted = patch(queue.admin, meeting, {"team_id": queue.team_a})
    assert_attributed(
        accepted,
        meeting,
        actor=queue.admin,
        before=(queue.team_b, None),
        after=(queue.team_a, None),
        item=item,
    )
    # ADR 0051 D4: after the admin's move the team A supervisor has authority.
    if item is None:
        assert create(queue.sup, meeting).status_code == 201
    else:
        assert queue.sup.get(f"{ITEMS}/{item['id']}").status_code == 200
        assert queue.sup_b.get(f"{ITEMS}/{item['id']}").status_code == 404


def test_d5_a_flat_workspace_member_cannot_detach_a_call_from_its_team(queue, review_state):
    """Path 4, flat workspace: ``plain`` sets ``team_id`` to null."""
    meeting = queue.m_a2
    item = with_review(queue, meeting, review_state)
    before = snapshot(meeting)

    refused = patch(queue.plain, meeting, {"team_id": None})
    assert_refused(refused, "attribution_change_denied", meeting, before)
    if item is not None:  # the team's supervisor keeps the review
        assert queue.sup.get(f"{ITEMS}/{item['id']}").status_code == 200

    accepted = patch(queue.admin, meeting, {"team_id": None})
    assert_attributed(
        accepted,
        meeting,
        actor=queue.admin,
        before=(queue.team_a, None),
        after=(None, None),
        item=item,
    )


def test_d6_an_admin_never_changes_the_attribution_of_their_own_call(queue, review_state):
    """R4: ``admin`` is the agent of the call; R4 comes before the validators."""
    meeting = queue.meeting(team_id=queue.team_a, agent_user_id=queue.admin.user_id)
    item = with_review(queue, meeting, review_state)
    before = snapshot(meeting)

    for body in (
        {"team_id": queue.team_b},
        {"team_id": None},
        {"agent_user_id": None},
        {"agent_user_id": queue.plain.user_id},
        {"team_id": "no-such-team"},  # R4 before 422 invalid_team
        {"title": "Mine", "team_id": queue.team_b},
    ):
        refused = patch(queue.admin, meeting, body)
        assert_refused(refused, "attribution_own_call", meeting, before)

    # Allow-control: the same admin edits the title and sends back the same attribution.
    same = patch(
        queue.admin,
        meeting,
        {"title": "Mine", "team_id": queue.team_a, "agent_user_id": queue.admin.user_id},
    )
    assert same.status_code == 200, same.text
    assert events(meeting, "meeting.updated")[-1].details == {"fields": "title"}
    assert events(meeting, ATTRIBUTION_CHANGED) == []

    # Allow-control: another admin moves the call, then clears its agent.
    other = second_admin(queue)
    moved = patch(other, meeting, {"team_id": queue.team_b})
    assert_attributed(
        moved,
        meeting,
        actor=other,
        before=(queue.team_a, queue.admin.user_id),
        after=(queue.team_b, queue.admin.user_id),
        item=item,
    )
    cleared = patch(other, meeting, {"agent_user_id": None})
    assert_attributed(
        cleared,
        meeting,
        actor=other,
        before=(queue.team_b, queue.admin.user_id),
        after=(queue.team_b, None),
        item=item,
    )


def test_d7_an_owner_never_makes_themselves_the_agent(queue, review_state):
    """R4: also the owner, on a call without an agent and on a call with another agent."""
    items = {
        meeting: with_review(queue, meeting, review_state) for meeting in (queue.m_a2, queue.m_a)
    }
    for meeting in items:
        before = snapshot(meeting)
        refused = patch(queue.owner, meeting, {"agent_user_id": queue.owner.user_id})
        assert_refused(refused, "attribution_own_call", meeting, before)

    # Allow-control: the owner makes another member the agent.
    accepted = patch(queue.owner, queue.m_a2, {"agent_user_id": queue.plain.user_id})
    assert_attributed(
        accepted,
        queue.m_a2,
        actor=queue.owner,
        before=(queue.team_a, None),
        after=(queue.team_a, queue.plain.user_id),
        item=items[queue.m_a2],
    )


def test_d8_an_api_key_never_changes_attribution(client, queue, review_state):
    """R1: an owner's key with ``meeting:update`` changes the title, never the team or agent."""
    owner = queue.owner
    for meeting in (queue.m_a2, queue.m_a):
        with_review(queue, meeting, review_state)
    minted = _mint(client, owner.headers, owner.cookies, name="crm", scopes=["meeting:update"])
    assert minted.status_code == 201, minted.text
    headers = _bearer(minted.json()["key"], **{"X-Workspace-ID": owner.workspace_id})

    def key_patch(meeting_id: str, body: dict):
        return client.patch(f"{MEETINGS}/{meeting_id}", json=body, headers=headers)

    def assert_key_refused(response) -> None:
        # The key's own message: the key belongs to an owner, so "only an owner or
        # admin" would mislead its integrator.
        assert response.status_code == 403, response.text
        assert code(response) == "attribution_change_denied"
        assert response.json()["error"]["message"] == (
            "An API key cannot change a call's team or agent"
        )

    for meeting, body in (
        (queue.m_a2, {"team_id": queue.team_b}),
        (queue.m_a2, {"team_id": None}),
        (queue.m_a, {"agent_user_id": None}),
        (queue.m_a2, {"title": "From the key", "team_id": queue.team_b}),
    ):
        before = snapshot(meeting)
        assert_key_refused(key_patch(meeting, body))
        assert snapshot(meeting) == before

    # No key can carry the permission.
    refused_mint = _mint(
        client, owner.headers, owner.cookies, name="attribute", scopes=["meeting:attribute"]
    )
    assert refused_mint.status_code == 422, refused_mint.text

    # Even a stored key scope that names it (written behind the API) changes nothing.
    with inspection_session_factory()() as db:
        db.execute(
            update(ApiKey)
            .where(ApiKey.id == minted.json()["id"])
            .values(scopes=["meeting:update", "meeting:attribute"])
        )
        db.commit()
    before = snapshot(queue.m_a2)
    assert_key_refused(key_patch(queue.m_a2, {"team_id": queue.team_b}))
    assert snapshot(queue.m_a2) == before

    # Allow-control: the same key changes the title.
    renamed = key_patch(queue.m_a2, {"title": "From the key"})
    assert renamed.status_code == 200, renamed.text
    assert snapshot(queue.m_a2)[0] == "From the key"
    assert events(queue.m_a2, ATTRIBUTION_CHANGED) == []


def test_d9_a_refused_attribution_change_writes_no_other_field(queue, review_state):
    """R2: the title in the same body is not written either; R2 comes before the
    validators, so an unknown team is also ``attribution_change_denied``."""
    meeting = queue.m_a2
    with_review(queue, meeting, review_state)
    before = snapshot(meeting)
    for body in (
        {"title": "x", "team_id": queue.team_b},
        {"title": "x", "agent_user_id": queue.plain.user_id},
        {"title": "x", "direction": "inbound", "team_id": queue.team_b},
        {"team_id": "no-such-team"},
        {"agent_user_id": "no-such-user"},
    ):
        refused = patch(queue.plain, meeting, body)
        assert_refused(refused, "attribution_change_denied", meeting, before)

    accepted = patch(queue.plain, meeting, {"title": "x"})
    assert accepted.status_code == 200, accepted.text
    assert snapshot(meeting)[0] == "x"
    assert events(meeting, "meeting.updated")[-1].details == {"fields": "title"}


def test_the_conversations_alias_applies_the_same_rules(queue, review_state):
    meeting = queue.m_a
    item = with_review(queue, meeting, review_state)
    before = snapshot(meeting)
    refused = patch(queue.plain, meeting, {"agent_user_id": None}, base=CONVERSATIONS)
    assert_refused(refused, "attribution_change_denied", meeting, before)
    refused = patch(
        queue.owner, meeting, {"agent_user_id": queue.owner.user_id}, base=CONVERSATIONS
    )
    assert_refused(refused, "attribution_own_call", meeting, before)

    accepted = patch(queue.admin, meeting, {"team_id": queue.team_b}, base=CONVERSATIONS)
    assert_attributed(
        accepted,
        meeting,
        actor=queue.admin,
        before=(queue.team_a, queue.agent.user_id),
        after=(queue.team_b, queue.agent.user_id),
        item=item,
    )


# --- allow cases (SPEC §3, A1 to A4) ------------------------------------------------------


def test_a1_a_member_still_edits_the_other_fields(queue, review_state):
    meeting = queue.m_a
    with_review(queue, meeting, review_state)
    started_at = datetime(2026, 10, 1, 9, 30, tzinfo=UTC).isoformat()
    accepted = patch(
        queue.plain,
        meeting,
        {
            "title": "Edited",
            "conversation_type": "meeting",
            "direction": "outbound",
            "started_at": started_at,
        },
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["title"] == "Edited" and body["direction"] == "outbound"
    assert (body["team_id"], body["agent_user_id"]) == (queue.team_a, queue.agent.user_id)
    assert events(meeting, "meeting.updated")[-1].details == {
        "fields": "title,conversation_type,direction,started_at"
    }
    assert events(meeting, ATTRIBUTION_CHANGED) == []


def test_a2_unchanged_attribution_needs_no_right_and_is_not_checked_again(queue, review_state):
    """R3: a client that sends back the whole record keeps working for members."""
    meeting = queue.m_a
    with_review(queue, meeting, review_state)
    accepted = patch(
        queue.plain,
        meeting,
        {"title": "Full record", "team_id": queue.team_a, "agent_user_id": queue.agent.user_id},
    )
    assert accepted.status_code == 200, accepted.text
    assert events(meeting, "meeting.updated")[-1].details == {"fields": "title"}

    # Both values null and sent back as null.
    accepted = patch(
        queue.plain, queue.m_none, {"title": "No team", "team_id": None, "agent_user_id": None}
    )
    assert accepted.status_code == 200, accepted.text
    assert events(queue.m_none, "meeting.updated")[-1].details == {"fields": "title"}

    # An unchanged team is not validated again, even after the team was deactivated.
    team_c = queue.owner.post("/api/v1/teams", json={"name": "Team C"}).json()["id"]
    old = queue.meeting(team_id=team_c)
    assert (
        queue.owner.patch(f"/api/v1/teams/{team_c}", json={"is_active": False}).status_code == 200
    )
    accepted = patch(queue.plain, old, {"title": "Old team", "team_id": team_c})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["team_id"] == team_c
    assert events(old, "meeting.updated")[-1].details == {"fields": "title"}

    for touched in (meeting, queue.m_none, old):
        assert events(touched, ATTRIBUTION_CHANGED) == []


def test_a3_only_unchanged_attribution_is_no_change(queue, review_state):
    meeting = queue.m_a
    with_review(queue, meeting, review_state)
    for target, body in (
        (meeting, {"team_id": queue.team_a}),
        (meeting, {"agent_user_id": queue.agent.user_id}),
        (meeting, {"team_id": queue.team_a, "agent_user_id": queue.agent.user_id}),
        (queue.m_none, {"team_id": None, "agent_user_id": None}),
    ):
        before = snapshot(target)
        response = patch(queue.plain, target, body)
        assert response.status_code == 422, response.text
        assert code(response) == "no_changes"
        assert snapshot(target) == before


def test_a4_an_admin_still_meets_the_existing_validators(client, email_outbox, queue, review_state):
    meeting = queue.m_a2
    with_review(queue, meeting, review_state)
    team_c = queue.owner.post("/api/v1/teams", json={"name": "Team C"}).json()["id"]
    assert (
        queue.owner.patch(f"/api/v1/teams/{team_c}", json={"is_active": False}).status_code == 200
    )
    outsider = stranger(client, email_outbox, "hom360-outsider@example.com")
    before = snapshot(meeting)

    inactive = patch(queue.admin, meeting, {"team_id": team_c})
    assert inactive.status_code == 422 and code(inactive) == "invalid_team"
    non_member = patch(queue.admin, meeting, {"agent_user_id": outsider.user_id})
    assert non_member.status_code == 422 and code(non_member) == "invalid_agent"
    assert snapshot(meeting) == before
