"""HOM-375: create and delete of a voicemail action is attribution (ADR 0054).

HOM-373 guarded only ``PATCH /meetings/{meeting_id}/actions/{action_id}`` and
only the one action a voicemail links.  Three side doors stayed open for any
holder of ``meeting:update``:

* ``DELETE /meetings/{meeting_id}/actions/{action_id}`` removed the follow-up
  action with no assignment check;
* ``POST /meetings/{meeting_id}/actions`` on the voicemail's meeting created
  an action with any ``assignee_user_id``, the caller's own id included;
* a member could post an unassigned action there and then patch its
  ``assignee_user_id``, because the guard only knew the linked action.

Now (SPEC C1 to C5, E1 to E5, P1, P2):

* ``POST`` with a non-null ``assignee_user_id`` on a voicemail meeting needs
  ``meeting:attribute`` through the ADR 0054 guard: a member (1), a
  record-scoped supervisor (3) and every API key (2) get ``403
  attribution_change_denied`` and nothing is written, no action row and no
  audit row; nobody posts their own id and nobody posts an assignee for a
  voicemail assigned to them: ``403 attribution_own_call`` (4, 5); the guard
  runs before the validator, so an owner's non-member id is ``422
  invalid_assignee`` (6) and a member's is ``403``;
* ``DELETE`` of the follow-up action, assigned or not (7, 9), and of any
  voicemail action that has an assignee (8), needs the same guard twice;
  the action's own assignee and the voicemail's assignee never delete it:
  ``403 attribution_own_call`` (10); a refusal keeps the action, the link
  and every audit row;
* an accepted guarded delete clears ``follow_up_action_id`` and writes
  ``action_item.deleted`` plus ``action_item.assignee_changed`` with ids
  only (11);
* the full bypass, delete then recreate with an assignee, is refused on
  both steps (12);
* the assignee of every voicemail action, not only the follow-up, is
  guarded on ``PATCH``: path 3 is closed (13).
"""

from __future__ import annotations

from sqlalchemy import select

from backend.database.saas_models import ActionItem, AuditEvent
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_review_queue_access import Queue, code, stranger
from backend.tests.test_voicemail_action_assignee_api import (
    action_url,
    assert_refused,
    events,
    import_vm,
    set_assignment,
    snapshot,
    with_action,
)
from backend.tests.test_voicemail_action_assignee_api import (
    queue as _voicemail_queue,
)
from backend.voicemail.models import VoicemailMessage

# Re-export the fixture under its own name: pytest finds it for every test
# below, and the module-level name is an assignment, not an import, so the
# ``queue`` parameters shadow it without tripping F401/F811.
queue = _voicemail_queue

CREATED = "action_item.created"
DELETED = "action_item.deleted"
ASSIGNEE_CHANGED = "action_item.assignee_changed"


# --- helpers ------------------------------------------------------------------------------


def meeting_id_of(voicemail_id: str) -> str:
    with inspection_session_factory()() as db:
        return db.get(VoicemailMessage, voicemail_id).meeting_id


def meeting_url(voicemail_id: str) -> str:
    """The create-actions URL of the voicemail's meeting."""
    return f"/api/v1/meetings/{meeting_id_of(voicemail_id)}/actions"


def meeting_assignees(voicemail_id: str) -> dict[str, str | None]:
    """The stored assignee of every action on the voicemail's meeting."""
    with inspection_session_factory()() as db:
        voicemail = db.get(VoicemailMessage, voicemail_id)
        rows = db.scalars(
            select(ActionItem).where(
                ActionItem.workspace_id == voicemail.workspace_id,
                ActionItem.meeting_id == voicemail.meeting_id,
            )
        ).all()
        return {row.id: row.assignee_user_id for row in rows}


def meeting_action_events(workspace_id: str) -> list[str]:
    """The ids of every ``action_item.*`` audit row of the workspace."""
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent.id).where(
                    AuditEvent.workspace_id == workspace_id,
                    AuditEvent.event_type.like("action_item.%"),
                )
            )
        )


def stored_action(queue: Queue, meeting_id: str, *, assignee_user_id: str | None = None) -> str:
    """An action on ``meeting_id`` behind the API, linked to no voicemail."""
    with inspection_session_factory()() as db:
        action = ActionItem(
            workspace_id=queue.workspace_id,
            meeting_id=meeting_id,
            description="An unlinked action",
            assignee_user_id=assignee_user_id,
        )
        db.add(action)
        db.commit()
        return action.id


# --- 1: a member holds no meeting:attribute ------------------------------------------------


def test_1_a_member_cannot_post_an_assignee_on_a_voicemail_meeting(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    url = meeting_url(voicemail)
    before = meeting_assignees(voicemail)
    before_events = meeting_action_events(queue.workspace_id)

    refused = queue.plain.post(
        url,
        json={"description": "Call the customer back", "assignee_user_id": queue.plain.user_id},
    )
    assert refused.status_code == 403, refused.text
    assert code(refused) == "attribution_change_denied", refused.text
    assert meeting_assignees(voicemail) == before
    assert meeting_action_events(queue.workspace_id) == before_events

    # Allow-control: the same member posts no assignee on the voicemail meeting.
    accepted = queue.plain.post(url, json={"description": "Call the customer back"})
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["assignee_user_id"] is None

    # Allow-control: the same member posts that assignee on a non-voicemail meeting.
    control = queue.plain.post(
        f"/api/v1/meetings/{queue.meeting()}/actions",
        json={"description": "Call the customer back", "assignee_user_id": queue.plain.user_id},
    )
    assert control.status_code == 201, control.text


# --- 2: an API key is refused by itself ----------------------------------------------------


def test_2_an_api_key_never_posts_an_assignee_on_a_voicemail_meeting(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    minted = _mint(
        queue.owner.client,
        queue.owner.headers,
        queue.owner.cookies,
        name="crm",
        scopes=["meeting:update"],
    )
    assert minted.status_code == 201, minted.text
    headers = _bearer(minted.json()["key"], **{"X-Workspace-ID": queue.workspace_id})
    url = meeting_url(voicemail)
    before = meeting_assignees(voicemail)

    refused = queue.owner.client.post(
        url,
        json={"description": "Call the customer back", "assignee_user_id": queue.plain.user_id},
        headers=headers,
    )
    assert refused.status_code == 403, refused.text
    assert code(refused) == "attribution_change_denied", refused.text
    assert meeting_assignees(voicemail) == before

    # Allow-control: the same key posts no assignee.
    accepted = queue.owner.client.post(
        url, json={"description": "Call the customer back"}, headers=headers
    )
    assert accepted.status_code == 201, accepted.text


# --- 3: a record-scoped supervisor holds no meeting:attribute ------------------------------


def test_3_a_record_scoped_supervisor_cannot_post_an_assignee(queue):
    switched = queue.owner.patch("/api/v1/admin/workspace-settings", json={"record_scope": "team"})
    assert switched.status_code == 200 and switched.json()["record_scope"] == "team"
    # Imported by ``sup``, so the record predicate lets them see it.
    voicemail = import_vm(queue.sup)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.plain.user_id)
    before = meeting_assignees(voicemail)

    refused = queue.sup.post(
        meeting_url(voicemail),
        json={"description": "Call the customer back", "assignee_user_id": queue.agent.user_id},
    )
    assert refused.status_code == 403, refused.text
    assert code(refused) == "attribution_change_denied", refused.text
    assert meeting_assignees(voicemail) == before


# --- 4: nobody assigns a voicemail action to themselves ------------------------------------


def test_4_the_owner_never_posts_their_own_id_as_assignee(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    before = meeting_assignees(voicemail)

    refused = queue.owner.post(
        meeting_url(voicemail),
        json={"description": "Call the customer back", "assignee_user_id": queue.owner.user_id},
    )
    assert refused.status_code == 403, refused.text
    assert code(refused) == "attribution_own_call", refused.text
    assert meeting_assignees(voicemail) == before

    # Allow-control: the owner posts another member.
    accepted = queue.owner.post(
        meeting_url(voicemail),
        json={"description": "Call the customer back", "assignee_user_id": queue.plain.user_id},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["assignee_user_id"] == queue.plain.user_id
    changed = events(accepted.json()["id"], ASSIGNEE_CHANGED)
    assert len(changed) == 1
    assert changed[0].details == {
        "assignee_user_id_before": None,
        "assignee_user_id_after": queue.plain.user_id,
        "voice_message_id": voicemail,
    }
    assert len(events(accepted.json()["id"], CREATED)) == 1


# --- 5: the voicemail's assignee never posts an assignee -----------------------------------


def test_5_an_admin_a_voicemail_is_assigned_to_never_posts_an_assignee(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.admin.user_id)
    before = meeting_assignees(voicemail)

    refused = queue.admin.post(
        meeting_url(voicemail),
        json={"description": "Call the customer back", "assignee_user_id": queue.plain.user_id},
    )
    assert refused.status_code == 403, refused.text
    assert code(refused) == "attribution_own_call", refused.text
    assert meeting_assignees(voicemail) == before

    # Allow-control: the owner does the same.
    accepted = queue.owner.post(
        meeting_url(voicemail),
        json={"description": "Call the customer back", "assignee_user_id": queue.plain.user_id},
    )
    assert accepted.status_code == 201, accepted.text


# --- 6: the guard runs before the validator ------------------------------------------------


def test_6_the_guard_runs_before_the_assignee_validator_on_create(queue, client, email_outbox):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    outsider = stranger(client, email_outbox, "hom375-outsider@example.com")
    before = meeting_assignees(voicemail)

    invalid = queue.owner.post(
        meeting_url(voicemail),
        json={"description": "Call the customer back", "assignee_user_id": outsider.user_id},
    )
    assert invalid.status_code == 422, invalid.text
    assert code(invalid) == "invalid_assignee", invalid.text
    assert meeting_assignees(voicemail) == before

    refused = queue.plain.post(
        meeting_url(voicemail),
        json={"description": "Call the customer back", "assignee_user_id": outsider.user_id},
    )
    assert refused.status_code == 403, refused.text
    assert code(refused) == "attribution_change_denied", refused.text
    assert meeting_assignees(voicemail) == before


# --- 7: a member cannot delete the follow-up action ----------------------------------------


def test_7_a_member_cannot_delete_a_follow_up_action(queue):
    voicemail = import_vm(queue.owner)
    # Unassigned voicemail and unassigned action: the link alone guards it.
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=None)
    action_id = with_action(queue, voicemail)
    before = snapshot(voicemail)

    refused = queue.plain.delete(action_url(voicemail, action_id))
    assert_refused(refused, 403, "attribution_change_denied", voicemail, before)
    with inspection_session_factory()() as db:
        assert db.get(ActionItem, action_id) is not None
        assert db.get(VoicemailMessage, voicemail).follow_up_action_id == action_id

    # Allow-control: an unassigned non-follow-up action on the same meeting.
    loose = stored_action(queue, meeting_id_of(voicemail))
    accepted = queue.plain.delete(action_url(voicemail, loose))
    assert accepted.status_code == 204, accepted.text

    # Allow-control: an assigned action on a meeting no voicemail names.
    plain_meeting = queue.meeting()
    assigned = stored_action(queue, plain_meeting, assignee_user_id=queue.plain.user_id)
    control = queue.plain.delete(f"/api/v1/meetings/{plain_meeting}/actions/{assigned}")
    assert control.status_code == 204, control.text


# --- 8: an assigned non-follow-up voicemail action is guarded ------------------------------


def test_8_a_member_cannot_delete_an_assigned_voicemail_action(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    with_action(queue, voicemail)
    action_id = stored_action(queue, meeting_id_of(voicemail), assignee_user_id=queue.plain.user_id)
    before = meeting_assignees(voicemail)
    before_events = meeting_action_events(queue.workspace_id)

    refused = queue.plain.delete(action_url(voicemail, action_id))
    assert refused.status_code == 403, refused.text
    assert code(refused) == "attribution_change_denied", refused.text
    assert meeting_assignees(voicemail) == before
    assert meeting_action_events(queue.workspace_id) == before_events


# --- 9: an API key is refused by itself on delete ------------------------------------------


def test_9_an_api_key_never_deletes_a_follow_up_action(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    action_id = with_action(queue, voicemail)
    minted = _mint(
        queue.owner.client,
        queue.owner.headers,
        queue.owner.cookies,
        name="crm",
        scopes=["meeting:update"],
    )
    assert minted.status_code == 201, minted.text
    headers = _bearer(minted.json()["key"], **{"X-Workspace-ID": queue.workspace_id})
    before = snapshot(voicemail)

    refused = queue.owner.client.delete(action_url(voicemail, action_id), headers=headers)
    assert_refused(refused, 403, "attribution_change_denied", voicemail, before)
    assert refused.json()["error"]["message"] == (
        "An API key cannot change a voicemail's team or assignee"
    )


# --- 10: an admin never deletes their own voicemail action ---------------------------------


def test_10_an_admin_never_deletes_their_own_voicemail_action(queue):
    # The action's assignee is the admin (a non-follow-up voicemail action).
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    mine = stored_action(queue, meeting_id_of(voicemail), assignee_user_id=queue.admin.user_id)

    refused = queue.admin.delete(action_url(voicemail, mine))
    assert refused.status_code == 403, refused.text
    assert code(refused) == "attribution_own_call", refused.text
    with inspection_session_factory()() as db:
        assert db.get(ActionItem, mine) is not None
    assert events(mine, DELETED) == []
    assert events(mine, ASSIGNEE_CHANGED) == []

    # The voicemail's assignee is the admin; the follow-up is someone else's.
    other = import_vm(queue.owner)
    set_assignment(other, assigned_team_id=queue.team_a, assigned_user_id=queue.admin.user_id)
    follow_up = with_action(queue, other, assignee_user_id=queue.plain.user_id)
    before = snapshot(other)

    refused_b = queue.admin.delete(action_url(other, follow_up))
    assert_refused(refused_b, 403, "attribution_own_call", other, before)


# --- 11: an accepted guarded delete --------------------------------------------------------


def test_11_the_owner_deletes_a_follow_up_action_assigned_to_a_member(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    action_id = with_action(queue, voicemail, assignee_user_id=queue.plain.user_id)

    accepted = queue.owner.delete(action_url(voicemail, action_id))
    assert accepted.status_code == 204, accepted.text
    with inspection_session_factory()() as db:
        assert db.get(ActionItem, action_id) is None
        row = db.get(VoicemailMessage, voicemail)
        assert row.follow_up_action_id is None
        assert row.follow_up_state == "action_created"
        assert row.assigned_team_id == queue.team_a
        assert row.assigned_user_id == queue.agent.user_id
    assert len(events(action_id, DELETED)) == 1
    changed = events(action_id, ASSIGNEE_CHANGED)
    assert len(changed) == 1
    assert changed[0].details == {
        "assignee_user_id_before": queue.plain.user_id,
        "assignee_user_id_after": None,
        "voice_message_id": voicemail,
    }


# --- 12: the full bypass is refused on both steps ------------------------------------------


def test_12_the_full_bypass_delete_then_recreate_is_refused(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    action_id = with_action(queue, voicemail)
    before = meeting_assignees(voicemail)

    refused = queue.plain.delete(action_url(voicemail, action_id))
    assert refused.status_code == 403, refused.text
    assert code(refused) == "attribution_change_denied", refused.text

    recreated = queue.plain.post(
        meeting_url(voicemail),
        json={"description": "Call the customer back", "assignee_user_id": queue.plain.user_id},
    )
    assert recreated.status_code == 403, recreated.text
    assert code(recreated) == "attribution_change_denied", recreated.text
    assert meeting_assignees(voicemail) == before


# --- 13: path 3, patch of a created action is guarded --------------------------------------


def test_13_a_created_action_on_a_voicemail_meeting_is_guarded_on_patch(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    created = queue.plain.post(
        meeting_url(voicemail), json={"description": "Call the customer back"}
    )
    assert created.status_code == 201, created.text
    action_id = created.json()["id"]
    url = action_url(voicemail, action_id)

    refused = queue.plain.patch(
        url, json={"assignee_user_id": queue.plain.user_id, "status": "done"}
    )
    assert refused.status_code == 403, refused.text
    assert code(refused) == "attribution_change_denied", refused.text
    with inspection_session_factory()() as db:
        action = db.get(ActionItem, action_id)
        assert action.assignee_user_id is None
        assert action.status.value == "open"
    assert events(action_id, ASSIGNEE_CHANGED) == []
    assert events(action_id, "action_item.updated") == []

    # Allow-control: the owner assigns a member.
    accepted = queue.owner.patch(url, json={"assignee_user_id": queue.plain.user_id})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["assignee_user_id"] == queue.plain.user_id
    assert len(events(action_id, ASSIGNEE_CHANGED)) == 1

    # An unchanged value needs no right and is not listed in the audit fields.
    unchanged = queue.plain.patch(
        url, json={"assignee_user_id": queue.plain.user_id, "status": "done"}
    )
    assert unchanged.status_code == 200, unchanged.text
    # The owner's accepted change wrote one updated row (fields: assignee);
    # the member's later one lists only status, never the unchanged assignee.
    updated = events(action_id, "action_item.updated")
    assert [row.details["fields"] for row in updated] == ["assignee_user_id", "status"]
