"""HOM-357 part R: early revoke of a retiring webhook signing key.

``POST /endpoints/{id}/signing-keys/{key_id}/revoke`` ends the key's overlap
at once: ``expires_at`` becomes now, one audit row is written, and a delivery
claimed after the revoke never signs with the key again. The active key is
not revokable (rotate with ``overlap_seconds`` 0 instead).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.database.saas_models import AuditEvent
from backend.jobs import webhook_delivery
from backend.security.authorization import API_KEY_SCOPES
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _Persona, _workspace_with_roles
from backend.tests.test_webhook_api import (
    ENDPOINTS,
    HOOK,
    KEYRING_KEY,
    SECRET_FIELDS,
    _created,
    _error,
    _owner,
    _persona,
)
from backend.tests.test_webhook_delivery import Receiver, _attempts, _enqueue, _entries


@pytest.fixture
def settings(settings):
    """The shared test settings plus a webhook keyring."""
    return settings.model_copy(
        update={"webhook_secret_keys": f"wk1:{KEYRING_KEY}", "webhook_secret_active_key_id": "wk1"}
    )


@pytest.fixture(autouse=True)
def worker_settings(settings, monkeypatch):
    """The worker reads get_settings(); here that is the test app's settings."""
    monkeypatch.setattr(webhook_delivery, "get_settings", lambda: settings)


@pytest.fixture(autouse=True)
def no_publish(settings, monkeypatch):
    monkeypatch.setattr(settings, "tasks_eager", True)


def _revoke(actor, endpoint_id: str, key_id: str):
    return actor.post(f"{ENDPOINTS}/{endpoint_id}/signing-keys/{key_id}/revoke")


def _keys_by_id(actor, endpoint_id: str) -> dict:
    endpoint = actor.get(f"{ENDPOINTS}/{endpoint_id}")
    assert endpoint.status_code == 200, endpoint.text
    return {key["key_id"]: key for key in endpoint.json()["signing_keys"]}


def _rotate(actor, endpoint_id: str, overlap: int):
    response = actor.post(
        f"{ENDPOINTS}/{endpoint_id}/signing-keys", json={"overlap_seconds": overlap}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _revoke_audit_rows() -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent).where(AuditEvent.event_type == "webhook.signing_key_revoked")
            ).all()
        )


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --- the revoke itself (R1) -------------------------------------------------------------


def test_revoking_a_retiring_key_expires_it_at_once_and_touches_no_other_key(
    client, email_outbox, monkeypatch
):
    owner = _owner(client, email_outbox, monkeypatch)
    created = _created(owner)
    endpoint_id = created["endpoint"]["id"]
    first = _rotate(owner, endpoint_id, 3600)  # created key retiring
    second = _rotate(owner, endpoint_id, 7200)  # first rotation's key retiring
    before = _keys_by_id(owner, endpoint_id)
    revoke_time = datetime.now(UTC)

    response = _revoke(owner, endpoint_id, first["previous_key_id"])

    assert response.status_code == 200, response.text
    view = response.json()
    assert view == owner.get(f"{ENDPOINTS}/{endpoint_id}").json()
    keys = {key["key_id"]: key for key in view["signing_keys"]}
    revoked = keys[first["previous_key_id"]]
    assert revoked["state"] == "expired"
    assert revoke_time - timedelta(seconds=5) <= _parse(revoked["expires_at"]) <= datetime.now(UTC)
    # Nothing else moves: the active key and the other retiring key keep their expiry.
    active = keys[second["key_id"]]
    assert active["state"] == "active" and active["expires_at"] is None
    other = keys[second["previous_key_id"]]
    assert other["state"] == "retiring"
    assert _parse(other["expires_at"]) == _parse(before[second["previous_key_id"]]["expires_at"])
    assert set(view) == {
        "id",
        "url",
        "description",
        "event_types",
        "is_active",
        "created_at",
        "updated_at",
        "signing_keys",
    }
    for field in SECRET_FIELDS:
        assert f'"{field}"' not in response.text


# --- the audit row (R2) -----------------------------------------------------------------


def test_a_revoke_writes_exactly_one_audit_row_with_safe_metadata(
    client, email_outbox, monkeypatch
):
    owner = _owner(client, email_outbox, monkeypatch)
    created = _created(owner)
    endpoint_id = created["endpoint"]["id"]
    rotated = _rotate(owner, endpoint_id, 3600)
    retiring = rotated["previous_key_id"]
    scheduled = rotated["previous_key_expires_at"]
    revoke_time = datetime.now(UTC)

    response = _revoke(owner, endpoint_id, retiring)

    assert response.status_code == 200, response.text
    [row] = _revoke_audit_rows()
    assert row.target_type == "webhook_endpoint"
    assert row.target_id == endpoint_id
    assert row.workspace_id == owner.workspace_id
    assert row.actor_user_id == owner.user_id
    assert row.details.keys() == {"key_id", "scheduled_expires_at", "revoked_at"}
    assert row.details["key_id"] == retiring
    assert _parse(row.details["scheduled_expires_at"]) == _parse(scheduled)
    assert (
        revoke_time - timedelta(seconds=5) <= _parse(row.details["revoked_at"]) <= datetime.now(UTC)
    )
    assert _parse(row.details["revoked_at"]) == _parse(
        _keys_by_id(owner, endpoint_id)[retiring]["expires_at"]
    )
    text = json.dumps(row.details)
    for secret in (created["signing_secret"], rotated["signing_secret"]):
        assert secret not in text
    assert HOOK not in text


# --- the signing guarantee (R3) ---------------------------------------------------------


def _overlap_pair(client, email_outbox, monkeypatch, overlap: int):
    owner = _owner(client, email_outbox, monkeypatch)
    old = _created(owner)
    endpoint_id = old["endpoint"]["id"]
    new = _rotate(owner, endpoint_id, overlap)
    return owner, old, new


def test_a_delivery_claimed_after_the_revoke_never_signs_with_the_key(
    client, email_outbox, monkeypatch
):
    owner, old, new = _overlap_pair(client, email_outbox, monkeypatch, 3600)
    endpoint_id = old["endpoint"]["id"]
    assert _revoke(owner, endpoint_id, old["key_id"]).status_code == 200
    later = datetime.now(UTC) + timedelta(seconds=1)
    [delivery_id] = _enqueue(owner.workspace_id, now=later)
    receiver = Receiver()

    from backend.jobs.webhook_delivery import deliver_webhook

    deliver_webhook(delivery_id, now=later, http_client=receiver.client)

    [request] = receiver.requests
    assert [entry.split(":")[0] for entry in _entries(request)] == [f"v1={new['key_id']}"]
    [attempt] = _attempts(delivery_id)
    assert attempt["signing_key_ids"] == [new["key_id"]]


def test_without_a_revoke_the_overlap_still_signs_with_both_keys(client, email_outbox, monkeypatch):
    owner, old, new = _overlap_pair(client, email_outbox, monkeypatch, 3600)
    during = datetime.now(UTC) + timedelta(seconds=1)
    [delivery_id] = _enqueue(owner.workspace_id, now=during)
    receiver = Receiver()

    from backend.jobs.webhook_delivery import deliver_webhook

    deliver_webhook(delivery_id, now=during, http_client=receiver.client)

    [request] = receiver.requests
    assert [entry.split(":")[0] for entry in _entries(request)] == [
        f"v1={new['key_id']}",
        f"v1={old['key_id']}",
    ]
    assert _attempts(delivery_id)[0]["signing_key_ids"] == [new["key_id"], old["key_id"]]


# --- the refusals (R4, R5, R6) ----------------------------------------------------------


def test_the_active_key_is_not_revokable(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    created = _created(owner)
    endpoint_id = created["endpoint"]["id"]
    rotated = _rotate(owner, endpoint_id, 3600)
    before = _keys_by_id(owner, endpoint_id)

    refused = _revoke(owner, endpoint_id, rotated["key_id"])

    assert refused.status_code == 409, refused.text
    assert _error(refused) == "webhook_signing_key_active"
    assert "overlap_seconds 0" in refused.json()["error"]["message"]
    assert _keys_by_id(owner, endpoint_id) == before
    assert _revoke_audit_rows() == []


@pytest.mark.parametrize("overlap", [0, 3600], ids=["expired-at-rotation", "retiring"])
def test_an_expired_or_already_revoked_key_is_not_revokable_again(
    client, email_outbox, monkeypatch, overlap
):
    owner = _owner(client, email_outbox, monkeypatch)
    created = _created(owner)
    endpoint_id = created["endpoint"]["id"]
    rotated = _rotate(owner, endpoint_id, overlap)
    target = rotated["previous_key_id"]
    if overlap:
        assert _revoke(owner, endpoint_id, target).status_code == 200  # the first revoke
    before = _keys_by_id(owner, endpoint_id)

    refused = _revoke(owner, endpoint_id, target)

    assert refused.status_code == 409, refused.text
    assert _error(refused) == "webhook_signing_key_expired"
    assert _keys_by_id(owner, endpoint_id) == before
    assert len(_revoke_audit_rows()) == (0 if overlap == 0 else 1)


def test_an_unknown_or_foreign_key_id_is_not_found_and_its_own_endpoint_revokes_it(
    client, email_outbox, monkeypatch
):
    owner = _owner(client, email_outbox, monkeypatch)
    one = _created(owner)["endpoint"]["id"]
    other_endpoint = _created(owner, "https://other.example.com/x")["endpoint"]["id"]
    other = _rotate(owner, other_endpoint, 3600)["previous_key_id"]
    foreign_workspace = _persona(client, email_outbox, "revoke-other-ws@example.com")
    _created(foreign_workspace)
    retiring = _rotate(owner, one, 3600)["previous_key_id"]

    unknown = _revoke(owner, one, "whk_0000000000000000000000000")
    wrong_endpoint = _revoke(owner, one, other)
    another_workspace = _revoke(foreign_workspace, one, retiring)

    assert unknown.status_code == 404 and _error(unknown) == "webhook_signing_key_not_found"
    assert wrong_endpoint.status_code == 404 and _error(wrong_endpoint) == (
        "webhook_signing_key_not_found"
    )
    assert another_workspace.status_code == 404 and _error(another_workspace) == (
        "webhook_endpoint_not_found"
    )
    # Allow-controls: the foreign key on its own endpoint, the owner's key on his.
    assert _revoke(owner, other_endpoint, other).status_code == 200
    assert _revoke(owner, one, retiring).status_code == 200
    assert len(_revoke_audit_rows()) == 2


# --- who may call it (R7) ---------------------------------------------------------------


def test_member_and_viewer_are_refused_and_owner_and_admin_are_allowed(
    client, email_outbox, monkeypatch
):
    owner, people, _ = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"admin": "admin", "member": "member", "viewer": "viewer"},
    )
    endpoint_id = _created(owner)["endpoint"]["id"]
    key_id = _rotate(owner, endpoint_id, 3600)["previous_key_id"]

    for name in ("member", "viewer"):
        refused = _revoke(people[name], endpoint_id, key_id)
        assert refused.status_code == 403, (name, refused.text)
        assert _error(refused) == "permission_denied"
    assert _revoke(people["admin"], endpoint_id, key_id).status_code == 200


def test_an_api_key_with_every_scope_is_refused(client, email_outbox, monkeypatch):
    owner: _Persona = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _created(owner)["endpoint"]["id"]
    key_id = _rotate(owner, endpoint_id, 3600)["previous_key_id"]
    minted = _mint(
        client,
        owner.headers,
        owner.cookies,
        name="automation",
        scopes=sorted(scope.value for scope in API_KEY_SCOPES),
    )
    assert minted.status_code == 201, minted.text

    saved = dict(client.cookies)
    client.cookies.clear()
    try:
        refused = client.post(
            f"{ENDPOINTS}/{endpoint_id}/signing-keys/{key_id}/revoke",
            headers=_bearer(minted.json()["key"]),
        )
    finally:
        client.cookies.update(saved)

    assert refused.status_code == 403, refused.text
    assert _error(refused) == "api_key_scope_denied"
    # Allow-control: the key's owner in a session.
    assert _revoke(owner, endpoint_id, key_id).status_code == 200


# --- the rotation cap opens again (R8) --------------------------------------------------


def test_a_revoke_frees_room_under_the_five_key_cap(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _created(owner)["endpoint"]["id"]
    rotate = f"{ENDPOINTS}/{endpoint_id}/signing-keys"
    for _ in range(4):
        assert owner.post(rotate, json={"overlap_seconds": 3600}).status_code == 201
    unexpired = [
        key for key in _keys_by_id(owner, endpoint_id).values() if key["state"] != "expired"
    ]
    assert len(unexpired) == 5
    assert owner.post(rotate, json={"overlap_seconds": 3600}).status_code == 409

    retiring = _keys_by_id(owner, endpoint_id)
    target = next(key_id for key_id, key in retiring.items() if key["state"] == "retiring")
    assert _revoke(owner, endpoint_id, target).status_code == 200

    rotated = owner.post(rotate, json={"overlap_seconds": 3600})

    assert rotated.status_code == 201, rotated.text
    keys = _keys_by_id(owner, endpoint_id)
    assert len([key for key in keys.values() if key["state"] != "expired"]) == 5
    assert keys[target]["state"] == "expired"
