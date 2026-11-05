"""HOM-373: a voicemail follow-up action's assignee is attribution (ADR 0054).

``PATCH /api/v1/meetings/{meeting_id}/actions/{action_id}`` let any holder of
``meeting:update`` write ``assignee_user_id``.  The action a voicemail links
(``VoicemailMessage.follow_up_action_id``) carries the voicemail's assignment,
so this route is a side door around the HOM-369 rule.  Now (SPEC R1 to R8):

* on a follow-up action, a changed ``assignee_user_id``, clearing to ``null``
  included, needs ``meeting:attribute`` through the ADR 0054 guard: a member
  (D1), a record-scoped supervisor (D3) and every API key (D4) get
  ``403 attribution_change_denied`` and nothing is written, also not the other
  fields of the same body (D5);
* an unchanged value is not a change: it needs no right, is not validated
  again, is not written and is not listed in the audit ``fields`` (D2);
* nobody changes the follow-up action of a voicemail assigned to them, nobody
  changes such an action assigned to them, and nobody assigns one to
  themselves, owners and admins too: ``403 attribution_own_call`` (D6 to D8);
* the guard runs before the validator: an owner's non-member id is
  ``422 invalid_assignee`` (D9), a member's is ``403`` (D10);
* an ordinary (non-follow-up) action keeps today's behaviour (the allow
  controls of D1, D4 and D10), and an accepted change touches only the action,
  never the voicemail (D11).
"""

from __future__ import annotations

import itertools

import pytest
from sqlalchemy import select, update

from backend.database.saas_models import ActionItem, ApiKey, AuditEvent
from backend.routers.v1 import voicemail as voicemail_router
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _Persona
from backend.tests.test_ingestion_ledger import _widen_concurrency
from backend.tests.test_review_queue_access import Queue, build_queue, code, stranger
from backend.tests.test_uploads_api import _valid_metadata
from backend.voicemail.models import VoicemailMessage

ASSIGNEE_CHANGED = "action_item.assignee_changed"
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
        "/api/v1/voicemails",
        data={
            "external_message_id": f"vm373-{next(_ids):04d}",
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


def ordinary_action(queue: Queue) -> tuple[str, str]:
    """An action no voicemail links, on its own meeting: the R8 control group."""
    with inspection_session_factory()() as db:
        meeting_id = queue.meeting()
        action = ActionItem(
            workspace_id=queue.workspace_id,
            meeting_id=meeting_id,
            description="An ordinary action",
        )
        db.add(action)
        db.commit()
        return meeting_id, action.id


def action_url(voicemail_id: str, action_id: str) -> str:
    with inspection_session_factory()() as db:
        meeting_id = db.get(VoicemailMessage, voicemail_id).meeting_id
    return f"/api/v1/meetings/{meeting_id}/actions/{action_id}"


def snapshot(voicemail_id: str) -> tuple:
    """The linked action's fields, the voicemail's assignment fields, and how
    many audit events name the action or the voicemail."""
    with inspection_session_factory()() as db:
        voicemail = db.get(VoicemailMessage, voicemail_id)
        assert voicemail is not None
        action = db.get(ActionItem, voicemail.follow_up_action_id)
        assert action is not None
        action_events = len(
            db.scalars(
                select(AuditEvent.id).where(
                    AuditEvent.target_type == "action_item",
                    AuditEvent.target_id == action.id,
                )
            ).all()
        )
        voicemail_events = len(
            db.scalars(
                select(AuditEvent.id).where(
                    AuditEvent.target_type == "voicemail",
                    AuditEvent.target_id == voicemail_id,
                )
            ).all()
        )
        return (
            action.assignee_user_id,
            action.assignee_text,
            action.status.value,
            action.description,
            action.due_at,
            action.priority,
            voicemail.assigned_team_id,
            voicemail.assigned_user_id,
            voicemail.follow_up_state,
            action_events,
            voicemail_events,
        )


def events(action_id: str, event_type: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.event_type == event_type,
                    AuditEvent.target_type == "action_item",
                    AuditEvent.target_id == action_id,
                )
                .order_by(AuditEvent.occurred_at.asc())
            )
        )


def assert_refused(
    response, expected_status: int, expected_code: str, voicemail_id: str, before: tuple
) -> None:
    """The expected refusal; no action field, no voicemail field and no audit
    event changed."""
    assert response.status_code == expected_status, response.text
    assert code(response) == expected_code, response.text
    assert snapshot(voicemail_id) == before


def second_admin(queue: Queue) -> _Persona:
    """``lapsed`` promoted to admin: another admin than ``queue.admin``."""
    promoted = queue.owner.patch(
        f"/api/v1/members/{queue.membership_ids['lapsed']}", json={"role": "admin"}
    )
    assert promoted.status_code == 200, promoted.text
    return queue.lapsed


# --- D1: a member holds no meeting:attribute ----------------------------------------------


def test_d1_a_member_cannot_change_a_follow_up_actions_assignee(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    action_id = with_action(queue, voicemail)
    before = snapshot(voicemail)

    refused = queue.plain.patch(
        action_url(voicemail, action_id),
        json={"assignee_user_id": queue.plain.user_id, "status": "done"},
    )
    assert_refused(refused, 403, "attribution_change_denied", voicemail, before)
    assert refused.json()["error"]["message"] == (
        "Only an owner or admin may change a voicemail's team or assignee"
    )

    # Allow-control: the same member changes an ordinary action's assignee.
    plain_meeting, plain_action = ordinary_action(queue)
    accepted = queue.plain.patch(
        f"/api/v1/meetings/{plain_meeting}/actions/{plain_action}",
        json={"assignee_user_id": queue.plain.user_id},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["assignee_user_id"] == queue.plain.user_id
    with inspection_session_factory()() as db:
        assert db.get(ActionItem, plain_action).assignee_user_id == queue.plain.user_id


# --- D2: an unchanged value is not a change -------------------------------------------------


def test_d2_an_unchanged_follow_up_assignee_needs_no_right(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    action_id = with_action(queue, voicemail)

    accepted = queue.plain.patch(
        action_url(voicemail, action_id),
        json={"assignee_user_id": queue.agent.user_id, "status": "done"},
    )
    assert accepted.status_code == 200, accepted.text
    with inspection_session_factory()() as db:
        action = db.get(ActionItem, action_id)
        assert action.assignee_user_id == queue.agent.user_id  # not written, so unchanged
        assert action.status.value == "done"
    assert events(action_id, ASSIGNEE_CHANGED) == []
    updated = events(action_id, "action_item.updated")
    assert len(updated) == 1 and updated[0].details["fields"] == "status"


def test_d2b_a_body_with_only_an_unchanged_assignee_writes_no_audit_row(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    action_id = with_action(queue, voicemail)
    before = snapshot(voicemail)

    accepted = queue.plain.patch(
        action_url(voicemail, action_id), json={"assignee_user_id": queue.agent.user_id}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["assignee_user_id"] == queue.agent.user_id
    # Nothing changed and no audit row of any type names the action.
    assert snapshot(voicemail) == before


# --- D3: a record-scoped supervisor holds no meeting:attribute -----------------------------


def test_d3_a_record_scoped_supervisor_cannot_change_a_follow_up_actions_assignee(queue):
    switched = queue.owner.patch("/api/v1/admin/workspace-settings", json={"record_scope": "team"})
    assert switched.status_code == 200 and switched.json()["record_scope"] == "team"
    # Imported by ``sup``, so the record predicate lets them see it.
    voicemail = import_vm(queue.sup)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.plain.user_id)
    action_id = with_action(queue, voicemail)
    before = snapshot(voicemail)

    refused = queue.sup.patch(
        action_url(voicemail, action_id), json={"assignee_user_id": queue.agent.user_id}
    )
    assert_refused(refused, 403, "attribution_change_denied", voicemail, before)

    # Allow-control: the same supervisor changes the follow-up action's status only.
    accepted = queue.sup.patch(action_url(voicemail, action_id), json={"status": "done"})
    assert accepted.status_code == 200, accepted.text
    with inspection_session_factory()() as db:
        assert db.get(ActionItem, action_id).status.value == "done"
    assert events(action_id, ASSIGNEE_CHANGED) == []


# --- D4: an API key is refused by itself ---------------------------------------------------


def test_d4_an_api_key_never_changes_a_follow_up_actions_assignee(queue):
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

    # Even a stored key scope that names the right (written behind the API).
    with inspection_session_factory()() as db:
        db.execute(
            update(ApiKey)
            .where(ApiKey.id == minted.json()["id"])
            .values(scopes=["meeting:update", "meeting:attribute"])
        )
        db.commit()

    before = snapshot(voicemail)
    refused = queue.owner.client.patch(
        action_url(voicemail, action_id),
        json={"assignee_user_id": queue.plain.user_id},
        headers=headers,
    )
    assert_refused(refused, 403, "attribution_change_denied", voicemail, before)
    assert refused.json()["error"]["message"] == (
        "An API key cannot change a voicemail's team or assignee"
    )

    # Allow-control: the same key changes an ordinary action's assignee.
    plain_meeting, plain_action = ordinary_action(queue)
    accepted = queue.owner.client.patch(
        f"/api/v1/meetings/{plain_meeting}/actions/{plain_action}",
        json={"assignee_user_id": queue.plain.user_id},
        headers=headers,
    )
    assert accepted.status_code == 200, accepted.text
    with inspection_session_factory()() as db:
        assert db.get(ActionItem, plain_action).assignee_user_id == queue.plain.user_id


# --- D5: clearing to null is a change -------------------------------------------------------


def test_d5_clearing_a_follow_up_assignee_is_a_change(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    action_id = with_action(queue, voicemail)
    before = snapshot(voicemail)

    refused = queue.plain.patch(action_url(voicemail, action_id), json={"assignee_user_id": None})
    assert_refused(refused, 403, "attribution_change_denied", voicemail, before)

    # Allow-control: the owner (not their own voicemail, not their own action) clears it.
    accepted = queue.owner.patch(action_url(voicemail, action_id), json={"assignee_user_id": None})
    assert accepted.status_code == 200, accepted.text
    cleared = events(action_id, ASSIGNEE_CHANGED)
    assert len(cleared) == 1
    assert cleared[0].details == {
        "assignee_user_id_before": queue.agent.user_id,
        "assignee_user_id_after": None,
        "voice_message_id": voicemail,
    }


# --- D6: nobody assigns a follow-up action to themselves -------------------------------------


def test_d6_the_owner_never_assigns_a_follow_up_action_to_themselves(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a)
    action_id = with_action(queue, voicemail)
    before = snapshot(voicemail)

    refused = queue.owner.patch(
        action_url(voicemail, action_id), json={"assignee_user_id": queue.owner.user_id}
    )
    assert_refused(refused, 403, "attribution_own_call", voicemail, before)
    assert refused.json()["error"]["message"] == (
        "Nobody changes the team or assignee of a voicemail assigned to them,"
        " or assigns one to themselves"
    )

    # Allow-control: the owner assigns another member.
    accepted = queue.owner.patch(
        action_url(voicemail, action_id), json={"assignee_user_id": queue.plain.user_id}
    )
    assert accepted.status_code == 200, accepted.text
    changed = events(action_id, ASSIGNEE_CHANGED)
    assert len(changed) == 1
    assert changed[0].details == {
        "assignee_user_id_before": None,
        "assignee_user_id_after": queue.plain.user_id,
        "voice_message_id": voicemail,
    }


# --- D7: the action's current assignee cannot change it --------------------------------------


def test_d7_the_current_assignee_cannot_change_their_own_follow_up_action(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    # The action's assignee is the owner; the voicemail's is the agent.
    action_id = with_action(queue, voicemail, assignee_user_id=queue.owner.user_id)
    before = snapshot(voicemail)

    refused = queue.owner.patch(
        action_url(voicemail, action_id), json={"assignee_user_id": queue.plain.user_id}
    )
    assert_refused(refused, 403, "attribution_own_call", voicemail, before)

    # Allow-control: a different admin changes it.
    promoted = second_admin(queue)
    accepted = promoted.patch(
        action_url(voicemail, action_id), json={"assignee_user_id": queue.plain.user_id}
    )
    assert accepted.status_code == 200, accepted.text
    assert len(events(action_id, ASSIGNEE_CHANGED)) == 1


# --- D8: the voicemail's assignee cannot change the follow-up action -------------------------


def test_d8_the_voicemails_assignee_cannot_change_the_follow_up_action(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.owner.user_id)
    # The action's assignee is someone else.
    action_id = with_action(queue, voicemail, assignee_user_id=queue.agent.user_id)
    before = snapshot(voicemail)

    refused = queue.owner.patch(
        action_url(voicemail, action_id), json={"assignee_user_id": queue.plain.user_id}
    )
    assert_refused(refused, 403, "attribution_own_call", voicemail, before)

    # Allow-control: the owner on a voicemail not assigned to them.
    other = import_vm(queue.owner)
    set_assignment(other, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    other_action = with_action(queue, other)
    accepted = queue.owner.patch(
        action_url(other, other_action), json={"assignee_user_id": queue.plain.user_id}
    )
    assert accepted.status_code == 200, accepted.text
    assert len(events(other_action, ASSIGNEE_CHANGED)) == 1


# --- D9: the validator still runs for an owner -----------------------------------------------


def test_d9_an_owner_cannot_set_a_non_member_on_a_follow_up_action(queue, client, email_outbox):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    action_id = with_action(queue, voicemail)
    outsider = stranger(client, email_outbox, "hom373-outsider@example.com")
    before = snapshot(voicemail)

    invalid = queue.owner.patch(
        action_url(voicemail, action_id), json={"assignee_user_id": outsider.user_id}
    )
    assert_refused(invalid, 422, "invalid_assignee", voicemail, before)

    # Allow-control: the owner sets a member id.
    accepted = queue.owner.patch(
        action_url(voicemail, action_id), json={"assignee_user_id": queue.plain.user_id}
    )
    assert accepted.status_code == 200, accepted.text
    assert len(events(action_id, ASSIGNEE_CHANGED)) == 1


# --- D10: the guard runs before the validator ------------------------------------------------


def test_d10_a_members_non_member_id_is_refused_by_the_guard_not_the_validator(
    queue, client, email_outbox
):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    action_id = with_action(queue, voicemail)
    outsider = stranger(client, email_outbox, "hom373-outsider@example.com")
    before = snapshot(voicemail)

    refused = queue.plain.patch(
        action_url(voicemail, action_id), json={"assignee_user_id": outsider.user_id}
    )
    assert_refused(refused, 403, "attribution_change_denied", voicemail, before)

    # Allow-control: today's behaviour on an ordinary action.
    plain_meeting, plain_action = ordinary_action(queue)
    invalid = queue.plain.patch(
        f"/api/v1/meetings/{plain_meeting}/actions/{plain_action}",
        json={"assignee_user_id": outsider.user_id},
    )
    assert invalid.status_code == 422, invalid.text
    assert code(invalid) == "invalid_assignee"
    with inspection_session_factory()() as db:
        assert db.get(ActionItem, plain_action).assignee_user_id is None


# --- D11: an accepted change touches only the action ------------------------------------------


def test_d11_an_accepted_change_does_not_touch_the_voicemail(queue):
    voicemail = import_vm(queue.owner)
    set_assignment(voicemail, assigned_team_id=queue.team_a, assigned_user_id=queue.agent.user_id)
    action_id = with_action(queue, voicemail)

    accepted = queue.admin.patch(
        action_url(voicemail, action_id), json={"assignee_user_id": queue.plain.user_id}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["assignee_user_id"] == queue.plain.user_id
    with inspection_session_factory()() as db:
        action = db.get(ActionItem, action_id)
        row = db.get(VoicemailMessage, voicemail)
        assert action.assignee_user_id == queue.plain.user_id
        assert row.assigned_team_id == queue.team_a
        assert row.assigned_user_id == queue.agent.user_id
        assert row.follow_up_state == "action_created"
    changed = events(action_id, ASSIGNEE_CHANGED)
    assert len(changed) == 1
    assert changed[0].event_type == ASSIGNEE_CHANGED
    assert changed[0].actor_user_id == queue.admin.user_id
    assert changed[0].workspace_id == queue.workspace_id
