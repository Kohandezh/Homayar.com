"""HOM-369: a voicemail's assignment is the attribution right (ADR 0054).

``PATCH /api/v1/voicemails/{id}`` may change ``assigned_team_id`` /
``assigned_user_id`` only under the HOM-360 rule:

* a value equal to the current one is not a change: it needs no right, is not
  checked again, is not written, does not touch the follow-up action's assignee
  and is not listed in the audit ``fields`` (V3);
* a real change, clearing to null included, needs ``meeting:attribute`` (owner,
  admin; never a member, a record-scoped supervisor or an API key), else
  ``403 attribution_change_denied`` and nothing is written, also not
  ``follow_up_state`` (V1, V6);
* nobody changes the assignment of a voicemail assigned to them, or assigns one
  to themselves, owners and admins too: ``403 attribution_own_call`` (V4);
* the existing validators still run for a changed value (V5):
  ``422 invalid_team`` / ``422 invalid_agent``;
* every accepted change writes ``voicemail.assignment_changed`` with ids only
  (V7) and still syncs the follow-up action's assignee (V8).
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from backend.database.saas_models import ActionItem, ApiKey, AuditEvent, Membership
from backend.routers.v1 import voicemail as voicemail_router
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _Persona
from backend.tests.test_ingestion_ledger import _widen_concurrency
from backend.tests.test_review_queue_access import Queue, build_queue, code, stranger
from backend.tests.test_uploads_api import _valid_metadata
from backend.voicemail.models import VoicemailMessage

BASE = "/api/v1/voicemails"
ASSIGNMENT_CHANGED = "voicemail.assignment_changed"
_ids = itertools.count(1)


@pytest.fixture
def queue(client, settings, monkeypatch, email_outbox) -> Queue:
    client.app.include_router(voicemail_router.router, prefix=settings.api_prefix)
    monkeypatch.setattr(
        "backend.voicemail.ingest.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=42.3),
    )
    monkeypatch.setattr("backend.voicemail.ingest.dispatch_job", lambda job_id, _settings: True)
    built = build_queue(client, email_outbox, monkeypatch)
    _widen_concurrency(built.workspace_id)
    return built


# --- helpers ------------------------------------------------------------------------------


def import_vm(persona: _Persona) -> str:
    """Import one voicemail as ``persona``; return its id."""
    response = persona.post(
        BASE,
        data={
            "external_message_id": f"vm369-{next(_ids):04d}",
            "caller_number": "0912 345 6789",
            "mailbox": "sales",
        },
        files={"file": ("vm.wav", b"voicemail wav bytes", "audio/wav")},
    )
    assert response.status_code == 202, response.text
    return response.json()["voicemail"]["id"]


def set_assignment(voicemail_id: str, **values: object) -> None:
    """Change stored assignment fields behind the API: the test's starting point."""
    with inspection_session_factory()() as db:
        db.execute(
            update(VoicemailMessage).where(VoicemailMessage.id == voicemail_id).values(**values)
        )
        db.commit()


def with_action(queue: Queue, voicemail_id: str, *, assignee_user_id: str | None = None) -> str:
    """An open follow-up action on the voicemail, made behind the API."""
    with inspection_session_factory()() as db:
        voicemail = db.get(VoicemailMessage, voicemail_id)
        action = ActionItem(
            workspace_id=queue.workspace_id,
            meeting_id=voicemail.meeting_id,
            description="Call the customer back",
            assignee_user_id=(
                assignee_user_id if assignee_user_id is not None else voicemail.assigned_user_id
            ),
        )
        db.add(action)
        db.flush()
        voicemail.follow_up_action_id = action.id
        voicemail.follow_up_state = "action_created"
        db.commit()
        return action.id


def snapshot(voicemail_id: str) -> tuple:
    """Both assignment fields, the follow-up fields, the linked action's assignee
    and status, and how many audit events name the voicemail."""
    with inspection_session_factory()() as db:
        voicemail = db.get(VoicemailMessage, voicemail_id)
        assert voicemail is not None
        action = (
            db.get(ActionItem, voicemail.follow_up_action_id)
            if voicemail.follow_up_action_id
            else None
        )
        named = len(
            db.scalars(
                select(AuditEvent.id).where(
                    AuditEvent.target_type == "voicemail",
                    AuditEvent.target_id == voicemail_id,
                )
            ).all()
        )
        return (
            voicemail.assigned_team_id,
            voicemail.assigned_user_id,
            voicemail.follow_up_state,
            voicemail.follow_up_done_at,
            action.assignee_user_id if action is not None else None,
            action.status.value if action is not None else None,
            named,
        )


def events(voicemail_id: str, event_type: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.event_type == event_type,
                    AuditEvent.target_type == "voicemail",
                    AuditEvent.target_id == voicemail_id,
                )
                .order_by(AuditEvent.occurred_at.asc())
            )
        )


def assert_refused(response, expected_code: str, voicemail_id: str, before: tuple) -> None:
    """``403 <expected_code>``; no field changed and no audit event was added."""
    assert response.status_code == 403, response.text
    assert code(response) == expected_code, response.text
    assert snapshot(voicemail_id) == before


def assert_assigned(
    response,
    voicemail_id: str,
    *,
    actor: _Persona,
    before: tuple[str | None, str | None],
    after: tuple[str | None, str | None],
    action_id: str | None = None,
) -> None:
    """``200``; the row carries ``after`` and one ``voicemail.assignment_changed``
    names it with exactly the five id keys; ``voicemail.updated`` lists exactly
    the changed fields."""
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["assigned_team_id"], body["assigned_user_id"]) == after
    assert snapshot(voicemail_id)[:2] == after
    event = events(voicemail_id, ASSIGNMENT_CHANGED)[-1]
    assert event.actor_user_id == actor.user_id
    assert event.workspace_id == actor.workspace_id
    assert event.details == {
        "assigned_team_id_before": before[0],
        "assigned_team_id_after": after[0],
        "assigned_user_id_before": before[1],
        "assigned_user_id_after": after[1],
        "follow_up_action_id": action_id,
    }
    # ``before`` / ``after`` are (team, user); the route lists the assignee first.
    changed = [
        name
        for name, differs in (
            ("assigned_user_id", before[1] != after[1]),
            ("assigned_team_id", before[0] != after[0]),
        )
        if differs
    ]
    assert events(voicemail_id, "voicemail.updated")[-1].details["fields"] == ",".join(changed)


def second_admin(queue: Queue) -> _Persona:
    """``lapsed`` promoted to admin: another admin than ``queue.admin``."""
    promoted = queue.owner.patch(
        f"/api/v1/members/{queue.membership_ids['lapsed']}", json={"role": "admin"}
    )
    assert promoted.status_code == 200, promoted.text
    return queue.lapsed


# --- A1: a member holds no meeting:attribute ----------------------------------------------


def test_a1_a_member_cannot_change_or_clear_the_assignment(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    before = snapshot(voicemail)
    for body in (
        {"assigned_team_id": None},
        {"assigned_user_id": None},
        {"assigned_team_id": queue.team_b},
        {"assigned_user_id": queue.plain.user_id},
        {"assigned_team_id": queue.team_b, "follow_up_state": "done"},
    ):
        refused = queue.plain.patch(f"{BASE}/{voicemail}", json=body)
        assert_refused(refused, "attribution_change_denied", voicemail, before)
        assert refused.json()["error"]["message"] == (
            "Only an owner or admin may change a voicemail's team or assignee"
        )
    assert events(voicemail, ASSIGNMENT_CHANGED) == []


# --- A2: a record-scoped supervisor holds no meeting:attribute -----------------------------


def test_a2_a_record_scoped_supervisor_cannot_change_the_assignment(queue):
    switched = queue.owner.patch("/api/v1/admin/workspace-settings", json={"record_scope": "team"})
    assert switched.status_code == 200 and switched.json()["record_scope"] == "team"
    # Imported by ``sup``, so the record predicate lets them see it.
    voicemail = import_vm(queue.sup)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.plain.user_id)
    assert queue.sup.get(f"{BASE}/{voicemail}").status_code == 200
    before = snapshot(voicemail)
    for body in (
        {"assigned_team_id": None},
        {"assigned_user_id": None},
    ):
        refused = queue.sup.patch(f"{BASE}/{voicemail}", json=body)
        assert_refused(refused, "attribution_change_denied", voicemail, before)

    # Moving it into a team they supervise is refused too.
    moving = import_vm(queue.sup)
    set_assignment(moving, assigned_team_id=queue.team_b)
    before_moving = snapshot(moving)
    refused = queue.sup.patch(f"{BASE}/{moving}", json={"assigned_team_id": queue.team_a})
    assert_refused(refused, "attribution_change_denied", moving, before_moving)


# --- A3: an API key is refused by itself ---------------------------------------------------


def test_a3_an_api_key_never_changes_the_assignment(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a)
    minted = _mint(
        queue.owner.client,
        queue.owner.headers,
        queue.owner.cookies,
        name="crm",
        scopes=["meeting:update"],
    )
    assert minted.status_code == 201, minted.text
    headers = _bearer(minted.json()["key"], **{"X-Workspace-ID": queue.workspace_id})

    def key_patch(body: dict):
        return queue.owner.client.patch(f"{BASE}/{voicemail}", json=body, headers=headers)

    def assert_key_refused(response) -> None:
        assert response.status_code == 403, response.text
        assert code(response) == "attribution_change_denied"
        assert response.json()["error"]["message"] == (
            "An API key cannot change a voicemail's team or assignee"
        )

    for body in (
        {"assigned_team_id": queue.team_b},
        {"assigned_team_id": None},
        {"assigned_user_id": queue.plain.user_id},
    ):
        before = snapshot(voicemail)
        assert_key_refused(key_patch(body))
        assert snapshot(voicemail) == before

    # Even a stored key scope that names the right (written behind the API).
    with inspection_session_factory()() as db:
        db.execute(
            update(ApiKey)
            .where(ApiKey.id == minted.json()["id"])
            .values(scopes=["meeting:update", "meeting:attribute"])
        )
        db.commit()
    before = snapshot(voicemail)
    assert_key_refused(key_patch({"assigned_team_id": queue.team_b}))
    assert snapshot(voicemail) == before

    # Allow-control: the owner's session changes the team.
    accepted = queue.owner.patch(f"{BASE}/{voicemail}", json={"assigned_team_id": queue.team_b})
    assert_assigned(
        accepted,
        voicemail,
        actor=queue.owner,
        before=(queue.team_a, None),
        after=(queue.team_b, None),
    )


# --- A4: an owner and an admin change and clear ---------------------------------------------


def test_a4_an_owner_and_an_admin_change_and_clear_the_assignment(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)

    accepted = queue.admin.patch(
        f"{BASE}/{voicemail}",
        json={"assigned_team_id": queue.team_b, "assigned_user_id": queue.plain.user_id},
    )
    assert_assigned(
        accepted,
        voicemail,
        actor=queue.admin,
        before=(queue.team_a, queue.agent.user_id),
        after=(queue.team_b, queue.plain.user_id),
    )

    cleared = queue.owner.patch(
        f"{BASE}/{voicemail}", json={"assigned_team_id": None, "assigned_user_id": None}
    )
    assert_assigned(
        cleared,
        voicemail,
        actor=queue.owner,
        before=(queue.team_b, queue.plain.user_id),
        after=(None, None),
    )
    assert len(events(voicemail, ASSIGNMENT_CHANGED)) == 2


# --- A5: never your own voicemail -----------------------------------------------------------


def test_a5_nobody_changes_their_own_voicemail_assignment(queue):
    own = import_vm(queue.owner)
    set_assignment(own, assigned_team_id=queue.team_a, assigned_user_id=queue.admin.user_id)
    before = snapshot(own)
    for body in (
        {"assigned_team_id": queue.team_b},
        {"assigned_team_id": None},
        {"assigned_user_id": None},
        {"assigned_user_id": queue.plain.user_id},
        {"assigned_team_id": "no-such-team"},  # V4 comes before the 422 validators
    ):
        refused = queue.admin.patch(f"{BASE}/{own}", json=body)
        assert_refused(refused, "attribution_own_call", own, before)
        assert refused.json()["error"]["message"] == (
            "Nobody changes the team or assignee of a voicemail assigned to them,"
            " or assigns one to themselves"
        )

    # Allow-control: another admin moves the same voicemail.
    other = import_vm(queue.owner)
    set_assignment(other, assigned_team_id=queue.team_a, assigned_user_id=queue.admin.user_id)
    promoted = second_admin(queue)
    accepted = promoted.patch(f"{BASE}/{other}", json={"assigned_team_id": queue.team_b})
    assert_assigned(
        accepted,
        other,
        actor=promoted,
        before=(queue.team_a, queue.admin.user_id),
        after=(queue.team_b, queue.admin.user_id),
    )

    # The owner never assigns a voicemail to themselves.
    unassigned = import_vm(queue.owner)
    set_assignment(unassigned, assigned_team_id=queue.team_a)
    before_unassigned = snapshot(unassigned)
    refused = queue.owner.patch(
        f"{BASE}/{unassigned}", json={"assigned_user_id": queue.owner.user_id}
    )
    assert_refused(refused, "attribution_own_call", unassigned, before_unassigned)
    # Allow-control: the owner assigns another member.
    accepted = queue.owner.patch(
        f"{BASE}/{unassigned}", json={"assigned_user_id": queue.plain.user_id}
    )
    assert_assigned(
        accepted,
        unassigned,
        actor=queue.owner,
        before=(queue.team_a, None),
        after=(queue.team_a, queue.plain.user_id),
    )


# --- A6: an unchanged value is not a change -------------------------------------------------


def test_a6_unchanged_assignment_needs_no_right(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    accepted = queue.plain.patch(
        f"{BASE}/{voicemail}",
        json={
            "assigned_team_id": queue.team_a,
            "assigned_user_id": queue.agent.user_id,
            "follow_up_state": "done",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert events(voicemail, "voicemail.updated")[-1].details["fields"] == "follow_up_state"
    assert events(voicemail, ASSIGNMENT_CHANGED) == []
    after = snapshot(voicemail)
    assert after[:2] == (queue.team_a, queue.agent.user_id)
    assert after[2] == "done" and after[3] is not None

    # Both values null and sent back as null.
    empty = import_vm(queue.owner)
    accepted = queue.plain.patch(
        f"{BASE}/{empty}",
        json={"assigned_team_id": None, "assigned_user_id": None, "follow_up_state": "done"},
    )
    assert accepted.status_code == 200, accepted.text
    assert events(empty, "voicemail.updated")[-1].details["fields"] == "follow_up_state"
    assert events(empty, ASSIGNMENT_CHANGED) == []

    # The same values without another change answer the existing 422 no_changes.
    for target, body in (
        (voicemail, {"assigned_team_id": queue.team_a}),
        (voicemail, {"assigned_team_id": queue.team_a, "assigned_user_id": queue.agent.user_id}),
        (empty, {"assigned_team_id": None, "assigned_user_id": None}),
    ):
        before = snapshot(target)
        refused = queue.plain.patch(f"{BASE}/{target}", json=body)
        assert refused.status_code == 422, refused.text
        assert code(refused) == "no_changes"
        assert snapshot(target) == before


def test_a6b_an_unchanged_value_is_not_validated_again(queue):
    """V3: the stored value may stop being valid; sending it back unchanged still works."""
    voicemail = import_vm(queue.owner)
    team_c = queue.owner.post("/api/v1/teams", json={"name": "Team C"}).json()["id"]
    set_assignment(voicemail, assigned_team_id=team_c, assigned_user_id=queue.agent.user_id)
    assert (
        queue.owner.patch(f"/api/v1/teams/{team_c}", json={"is_active": False}).status_code == 200
    )
    # The agent's membership window closed, so the stored assignee is no longer an
    # effective member either.
    with inspection_session_factory()() as db:
        db.execute(
            update(Membership)
            .where(Membership.id == queue.membership_ids["agent"])
            .values(valid_until=datetime(2026, 1, 1, tzinfo=UTC))
        )
        db.commit()

    # An admin sends both current values back with a follow-up change.
    accepted = queue.admin.patch(
        f"{BASE}/{voicemail}",
        json={
            "assigned_team_id": team_c,
            "assigned_user_id": queue.agent.user_id,
            "follow_up_state": "done",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert events(voicemail, "voicemail.updated")[-1].details["fields"] == "follow_up_state"
    assert events(voicemail, ASSIGNMENT_CHANGED) == []

    # The same unchanged deactivated team, sent back by a member.
    accepted = queue.plain.patch(
        f"{BASE}/{voicemail}", json={"assigned_team_id": team_c, "follow_up_state": "none"}
    )
    assert accepted.status_code == 200, accepted.text
    assert events(voicemail, "voicemail.updated")[-1].details["fields"] == "follow_up_state"
    assert events(voicemail, ASSIGNMENT_CHANGED) == []


# --- A7: a refusal writes nothing, also not the follow-up ------------------------------------


def test_a7_a_refused_change_writes_no_follow_up_state_either(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    action_id = with_action(queue, voicemail)
    before = snapshot(voicemail)
    refused = queue.plain.patch(
        f"{BASE}/{voicemail}", json={"assigned_team_id": None, "follow_up_state": "done"}
    )
    assert_refused(refused, "attribution_change_denied", voicemail, before)
    with inspection_session_factory()() as db:
        action = db.get(ActionItem, action_id)
        assert action is not None
        assert action.assignee_user_id == queue.agent.user_id
        assert action.status.value == "open"

    # Allow-control: the member's follow_up_state alone is accepted.
    accepted = queue.plain.patch(f"{BASE}/{voicemail}", json={"follow_up_state": "done"})
    assert accepted.status_code == 200, accepted.text
    after = snapshot(voicemail)
    assert after[2] == "done" and after[3] is not None
    assert after[4:6] == (queue.agent.user_id, "done")  # the action keeps its assignee
    assert events(voicemail, ASSIGNMENT_CHANGED) == []


def test_a7b_every_refusal_with_follow_up_state_writes_nothing(queue):
    """V6: the guard's 403 and the validators' 422 both leave the follow-up state,
    the action and the audit log untouched."""
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a)
    with_action(queue, voicemail, assignee_user_id=queue.agent.user_id)
    before = snapshot(voicemail)
    team_c = queue.owner.post("/api/v1/teams", json={"name": "Team C"}).json()["id"]
    assert (
        queue.owner.patch(f"/api/v1/teams/{team_c}", json={"is_active": False}).status_code == 200
    )

    # (i) an admin's invalid team plus follow_up_state: 422, nothing written.
    invalid = queue.admin.patch(
        f"{BASE}/{voicemail}",
        json={"assigned_team_id": team_c, "follow_up_state": "done"},
    )
    assert invalid.status_code == 422 and code(invalid) == "invalid_team"
    assert snapshot(voicemail) == before

    # (ii) a member's refused change plus follow_up_state on a voicemail with no
    # action: 403 (the guard runs before the no_follow_up_action 422), nothing written.
    bare = import_vm(queue.owner)
    set_assignment(bare, assigned_team_id=queue.team_a)
    before_bare = snapshot(bare)
    refused = queue.plain.patch(
        f"{BASE}/{bare}",
        json={"assigned_team_id": None, "follow_up_state": "action_created"},
    )
    assert refused.status_code == 403 and code(refused) == "attribution_change_denied"
    assert snapshot(bare) == before_bare


# --- A8: the follow-up action keeps following the assignee -----------------------------------


def test_a8_an_accepted_assignee_change_still_syncs_the_action(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    action_id = with_action(queue, voicemail)
    accepted = queue.admin.patch(
        f"{BASE}/{voicemail}", json={"assigned_user_id": queue.plain.user_id}
    )
    assert_assigned(
        accepted,
        voicemail,
        actor=queue.admin,
        before=(queue.team_a, queue.agent.user_id),
        after=(queue.team_a, queue.plain.user_id),
        action_id=action_id,
    )
    assert snapshot(voicemail)[4] == queue.plain.user_id


def test_a8b_an_unchanged_assignee_does_not_touch_the_action(queue):
    """V3/V8: only a changed ``assigned_user_id`` syncs the follow-up action."""
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    action_id = with_action(queue, voicemail, assignee_user_id=queue.plain.user_id)
    accepted = queue.plain.patch(
        f"{BASE}/{voicemail}",
        json={"assigned_user_id": queue.agent.user_id, "follow_up_state": "done"},
    )
    assert accepted.status_code == 200, accepted.text
    with inspection_session_factory()() as db:
        action = db.get(ActionItem, action_id)
        assert action is not None
        assert action.assignee_user_id == queue.plain.user_id  # not the voicemail's
        assert action.status.value == "done"  # only the follow-up change touched it
    assert events(voicemail, "voicemail.updated")[-1].details["fields"] == "follow_up_state"
    assert events(voicemail, ASSIGNMENT_CHANGED) == []


# --- A9: the validators still run for a changed value ----------------------------------------


def test_a9_the_validators_still_run_for_a_changed_value(queue, client, email_outbox):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    team_c = queue.owner.post("/api/v1/teams", json={"name": "Team C"}).json()["id"]
    assert (
        queue.owner.patch(f"/api/v1/teams/{team_c}", json={"is_active": False}).status_code == 200
    )
    outsider = stranger(client, email_outbox, "hom369-outsider@example.com")
    before = snapshot(voicemail)

    inactive = queue.admin.patch(f"{BASE}/{voicemail}", json={"assigned_team_id": team_c})
    assert inactive.status_code == 422 and code(inactive) == "invalid_team"
    non_member = queue.admin.patch(
        f"{BASE}/{voicemail}", json={"assigned_user_id": outsider.user_id}
    )
    assert non_member.status_code == 422 and code(non_member) == "invalid_agent"
    assert snapshot(voicemail) == before

    # Allow-control: an active team of the workspace.
    accepted = queue.admin.patch(f"{BASE}/{voicemail}", json={"assigned_team_id": queue.team_b})
    assert_assigned(
        accepted,
        voicemail,
        actor=queue.admin,
        before=(queue.team_a, queue.agent.user_id),
        after=(queue.team_b, queue.agent.user_id),
    )


# --- A10: the earlier gates do not change ----------------------------------------------------


def test_a10_a_viewer_and_a_stranger_are_refused_earlier(queue, client, email_outbox):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a)
    before = snapshot(voicemail)

    denied = queue.viewer.patch(f"{BASE}/{voicemail}", json={"assigned_team_id": queue.team_b})
    assert denied.status_code == 403 and code(denied) == "permission_denied"

    outsider = stranger(client, email_outbox, "hom369-outsider@example.com")
    missing = outsider.patch(f"{BASE}/{voicemail}", json={"assigned_team_id": queue.team_b})
    assert missing.status_code == 404 and code(missing) == "voicemail_not_found"
    assert snapshot(voicemail) == before
