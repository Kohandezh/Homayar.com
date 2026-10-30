"""HOM-89: the webhook endpoint API (DESIGN sections 6 and 7).

Six routes under ``/api/v1/webhooks``. Each needs ``webhook:manage`` (owner and
admin, never an API key scope). The signing secret appears only in the create
and rotate answers.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.database.saas_models import (
    AuditEvent,
    WebhookDelivery,
    WebhookEndpoint,
    WebhookSigningKey,
)
from backend.security.authorization import API_KEY_SCOPES, ROLE_PERMISSIONS, Permission
from backend.services.webhook_signing import WebhookSecretCipher
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _Persona, _workspace_with_roles
from backend.tests.test_share_policies import _cookies

ENDPOINTS = "/api/v1/webhooks/endpoints"
HOOK = "https://hooks.example.com/x"
KEYRING_KEY = base64.b64encode(b"webhook-api-test-keyring-32bytes").decode()
SECRET_FIELDS = ("signing_secret", "secret_ciphertext", "nonce", "encryption_key_id")


@pytest.fixture
def settings(settings):
    """The shared test settings plus a webhook keyring."""
    return settings.model_copy(
        update={"webhook_secret_keys": f"wk1:{KEYRING_KEY}", "webhook_secret_active_key_id": "wk1"}
    )


@pytest.fixture(autouse=True)
def no_publish(settings, monkeypatch):
    """Pin tasks_eager on: a request or a sweep then publishes nothing (SPEC A4.2).

    Tests of the publish path turn it off themselves and record apply_async.
    """
    monkeypatch.setattr(settings, "tasks_eager", True)


def _owner(client, email_outbox, monkeypatch) -> _Persona:
    owner, _, _ = _workspace_with_roles(client, email_outbox, monkeypatch, roles={})
    return owner


def _persona(client, email_outbox, email: str) -> _Persona:
    credentials = register_user(client, email_outbox, email=email, name=email.split("@")[0])
    session = login_user(client, credentials)
    return _Persona(client, session["workspaces"][0]["id"], session, _cookies(client))


def _create(actor, url: str = HOOK, event_types=("webhook.test",), **extra):
    return actor.post(ENDPOINTS, json={"url": url, "event_types": list(event_types), **extra})


def _created(actor, url: str = HOOK, event_types=("webhook.test",), **extra) -> dict:
    response = _create(actor, url, event_types, **extra)
    assert response.status_code == 201, response.text
    return response.json()


def _error(response) -> str:
    return response.json()["error"]["code"]


def _six_routes(actor, endpoint_id: str) -> list:
    one = f"{ENDPOINTS}/{endpoint_id}"
    return [
        _create(actor),
        actor.get(ENDPOINTS),
        actor.get(one),
        actor.patch(one, json={"description": "changed"}),
        actor.post(f"{one}/signing-keys", json={}),
        actor.post(f"{one}/test"),
    ]


def _assert_no_secret_fields(value) -> None:
    text = json.dumps(value)
    for field in SECRET_FIELDS:
        assert f'"{field}"' not in text, field


# --- create, read and the views -------------------------------------------------------


def test_create_returns_the_endpoint_the_key_id_and_the_secret_once(
    client, email_outbox, monkeypatch
):
    owner = _owner(client, email_outbox, monkeypatch)

    body = _created(owner, description="CRM")

    assert set(body) == {"endpoint", "key_id", "signing_secret"}
    assert body["key_id"].startswith("whk_") and len(body["key_id"]) == 28
    assert body["signing_secret"].startswith("whsec_") and len(body["signing_secret"]) == 49
    endpoint = body["endpoint"]
    assert set(endpoint) == {
        "id",
        "url",
        "description",
        "event_types",
        "is_active",
        "created_at",
        "updated_at",
        "signing_keys",
    }
    assert endpoint["url"] == HOOK
    assert endpoint["description"] == "CRM"
    assert endpoint["event_types"] == ["webhook.test"]
    assert endpoint["is_active"] is True
    assert [key["key_id"] for key in endpoint["signing_keys"]] == [body["key_id"]]
    assert endpoint["signing_keys"][0]["state"] == "active"
    assert endpoint["signing_keys"][0]["expires_at"] is None
    _assert_no_secret_fields(endpoint)


def test_list_and_get_never_return_a_secret(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    created = _created(owner)
    endpoint_id = created["endpoint"]["id"]

    listed = owner.get(ENDPOINTS)
    one = owner.get(f"{ENDPOINTS}/{endpoint_id}")

    assert listed.status_code == 200 and one.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [endpoint_id]
    assert one.json() == listed.json()["items"][0]
    for response in (listed, one):
        assert created["signing_secret"] not in response.text
        _assert_no_secret_fields(response.json())
    assert set(one.json()["signing_keys"][0]) == {"key_id", "created_at", "expires_at", "state"}


def test_the_secret_is_stored_encrypted_and_bound_to_its_endpoint(
    client, email_outbox, monkeypatch, settings
):
    owner = _owner(client, email_outbox, monkeypatch)
    created = _created(owner)
    secret = created["signing_secret"]

    with inspection_session_factory()() as db:
        key = db.scalar(
            select(WebhookSigningKey).where(WebhookSigningKey.key_id == created["key_id"])
        )
        endpoint = db.get(WebhookEndpoint, created["endpoint"]["id"])
        assert endpoint.secret_ciphertext is None  # legacy column, never written
        assert key.workspace_id == owner.workspace_id
        assert key.endpoint_id == endpoint.id
        assert key.encryption_key_id == "wk1"
        assert len(key.nonce) == 12
        assert secret.encode() not in key.secret_ciphertext
        assert key.expires_at is None
        cipher = WebhookSecretCipher.from_settings(settings)
        assert (
            cipher.decrypt(
                encryption_key_id=key.encryption_key_id,
                nonce=key.nonce,
                ciphertext=key.secret_ciphertext,
                workspace_id=key.workspace_id,
                endpoint_id=key.endpoint_id,
                key_id=key.key_id,
            )
            == secret
        )


def test_unknown_or_other_workspace_ids_are_not_found(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _created(owner)["endpoint"]["id"]
    other = _persona(client, email_outbox, "other-admin@example.com")
    one = f"{ENDPOINTS}/{endpoint_id}"

    refused = [
        other.get(one),
        other.patch(one, json={"is_active": False}),
        other.post(f"{one}/signing-keys", json={}),
        other.post(f"{one}/test"),
        owner.get(f"{ENDPOINTS}/no-such-endpoint"),
    ]

    for response in refused:
        assert response.status_code == 404, response.text
        assert _error(response) == "webhook_endpoint_not_found"
    assert other.get(ENDPOINTS).json() == {"items": []}
    # Allow-control: the owner of the endpoint's workspace.
    assert owner.get(one).status_code == 200
    with inspection_session_factory()() as db:
        assert db.get(WebhookEndpoint, endpoint_id).is_active is True


# --- permission ------------------------------------------------------------------------


def test_webhook_manage_is_owner_and_admin_only_and_never_a_key_scope():
    assert Permission.WEBHOOK_MANAGE.value == "webhook:manage"
    holders = {
        role.value
        for role, granted in ROLE_PERMISSIONS.items()
        if Permission.WEBHOOK_MANAGE in granted
    }
    assert holders == {"owner", "admin"}
    assert Permission.WEBHOOK_MANAGE not in API_KEY_SCOPES


def test_member_and_viewer_are_refused_and_admin_is_allowed(client, email_outbox, monkeypatch):
    owner, people, _ = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"admin": "admin", "member": "member", "viewer": "viewer"},
    )
    endpoint_id = _created(owner)["endpoint"]["id"]

    for name in ("member", "viewer"):
        for response in _six_routes(people[name], endpoint_id):
            assert response.status_code == 403, (name, response.text)
            assert _error(response) == "permission_denied"

    statuses = [response.status_code for response in _six_routes(people["admin"], endpoint_id)]
    assert statuses == [201, 200, 200, 200, 201, 202]


def test_an_api_key_with_every_scope_is_refused_on_every_route(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _created(owner)["endpoint"]["id"]
    minted = _mint(
        client,
        owner.headers,
        owner.cookies,
        name="automation",
        scopes=sorted(scope.value for scope in API_KEY_SCOPES),
    )
    assert minted.status_code == 201, minted.text
    raw_key = minted.json()["key"]
    one = f"{ENDPOINTS}/{endpoint_id}"

    saved = dict(client.cookies)
    client.cookies.clear()
    try:
        refused = [
            client.post(ENDPOINTS, json={"url": HOOK, "event_types": []}, headers=_bearer(raw_key)),
            client.get(ENDPOINTS, headers=_bearer(raw_key)),
            client.get(one, headers=_bearer(raw_key)),
            client.patch(one, json={"is_active": False}, headers=_bearer(raw_key)),
            client.post(f"{one}/signing-keys", json={}, headers=_bearer(raw_key)),
            client.post(f"{one}/test", headers=_bearer(raw_key)),
        ]
    finally:
        client.cookies.update(saved)

    for response in refused:
        assert response.status_code in (401, 403), response.text
    # Allow-control: the key's owner in a session.
    statuses = [response.status_code for response in _six_routes(owner, endpoint_id)]
    assert statuses == [201, 200, 200, 200, 201, 202]


# --- URL policy ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "ftp://hooks.example.com/x",
        "https://user:pw@hooks.example.com/x",
        "http://169.254.169.254/latest",
        "http://metadata.google.internal/",
        "http://[fe80::1]/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "https://hooks.example.com/" + "a" * 975,
    ],
)
def test_a_refused_url_is_422_and_nothing_is_stored(client, email_outbox, monkeypatch, url):
    owner = _owner(client, email_outbox, monkeypatch)

    response = _create(owner, url)

    assert response.status_code == 422, response.text
    assert _error(response) == "webhook_url_refused"
    assert response.json()["error"]["message"]
    assert owner.get(ENDPOINTS).json() == {"items": []}


@pytest.mark.parametrize(
    "url",
    [
        HOOK,
        "https://hooks.example.com/x?token=abc",
        "http://10.0.0.5:8080/hook",
        "http://192.168.1.20/hook",
        "http://localhost:9000/x",
        "http://127.0.0.1:9000/x",
        "https://hooks.example.com/" + "a" * 974,
    ],
)
def test_an_allowed_url_is_created(client, email_outbox, monkeypatch, url):
    owner = _owner(client, email_outbox, monkeypatch)

    assert len(url) <= 1000
    assert _created(owner, url)["endpoint"]["url"] == url


@pytest.mark.parametrize(
    ("url", "status"),
    [
        ("http://hooks.example.com/x", 422),
        ("https://localhost/x", 422),
        ("https://127.0.0.1/x", 422),
        ("https://[::1]/x", 422),
        (HOOK, 201),
        ("https://10.0.0.5/x", 201),
    ],
)
def test_production_also_refuses_http_and_loopback(
    client, email_outbox, monkeypatch, settings, url, status
):
    owner = _owner(client, email_outbox, monkeypatch)
    monkeypatch.setattr(settings, "environment", "production")

    response = _create(owner, url)

    assert response.status_code == status, response.text
    if status == 422:
        assert _error(response) == "webhook_url_refused"


@pytest.mark.parametrize(
    ("mode", "url", "status"),
    [
        ("cloud", "https://minio:9000/x", 422),
        ("cloud", "https://api/x", 422),
        ("cloud", "https://crm.internal/x", 422),
        ("cloud", "https://printer.local/x", 422),
        ("cloud", "https://a.localhost/x", 422),
        ("cloud", "https://[2001:db8::1]/x", 201),
        ("cloud", HOOK, 201),
        ("onprem", "https://minio:9000/x", 201),
        ("onprem", "https://api/x", 201),
        ("onprem", "https://crm.internal/x", 201),
        ("onprem", HOOK, 201),
    ],
)
def test_a_production_cloud_refuses_internal_host_names(
    client, email_outbox, monkeypatch, settings, mode, url, status
):
    """SPEC A5.4: in the deployed cloud these names reach internal services."""
    owner = _owner(client, email_outbox, monkeypatch)
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "deployment_mode", mode)

    response = _create(owner, url)

    assert response.status_code == status, response.text
    if status == 422:
        assert _error(response) == "webhook_url_refused"


@pytest.mark.parametrize(("mode", "status"), [("cloud", 422), ("onprem", 200)])
def test_patch_to_an_internal_name_follows_the_deployment(
    client, email_outbox, monkeypatch, settings, mode, status
):
    owner = _owner(client, email_outbox, monkeypatch)
    one = f"{ENDPOINTS}/{_created(owner)['endpoint']['id']}"
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "deployment_mode", mode)

    response = owner.patch(one, json={"url": "https://crm.internal/hook"})

    assert response.status_code == status, response.text
    expected = HOOK if status == 422 else "https://crm.internal/hook"
    assert owner.get(one).json()["url"] == expected


def test_patch_to_a_refused_url_keeps_the_stored_url(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _created(owner)["endpoint"]["id"]
    one = f"{ENDPOINTS}/{endpoint_id}"

    refused = owner.patch(one, json={"url": "http://169.254.169.254/latest"})
    allowed = owner.patch(one, json={"url": "https://hooks.example.com/new"})

    assert refused.status_code == 422 and _error(refused) == "webhook_url_refused"
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["url"] == "https://hooks.example.com/new"
    assert owner.get(one).json()["url"] == "https://hooks.example.com/new"


def test_patch_null_for_a_required_field_is_422(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    one = f"{ENDPOINTS}/{_created(owner)['endpoint']['id']}"

    for field in ("url", "event_types", "is_active"):
        assert owner.patch(one, json={field: None}).status_code == 422, field
    cleared = owner.patch(one, json={"description": None})
    assert cleared.status_code == 200 and cleared.json()["description"] is None


# --- event types -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "event_types", [["job.completed"], ["WEBHOOK.TEST"], ["webhook.test", "x"]]
)
def test_unknown_event_types_are_refused(client, email_outbox, monkeypatch, event_types):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _created(owner)["endpoint"]["id"]

    created = _create(owner, event_types=event_types)
    patched = owner.patch(f"{ENDPOINTS}/{endpoint_id}", json={"event_types": event_types})

    for response in (created, patched):
        assert response.status_code == 422, response.text
        assert _error(response) == "webhook_event_type_unknown"


@pytest.mark.parametrize(
    ("event_types", "stored"),
    [
        (["webhook.test"], ["webhook.test"]),
        (["webhook.test", "webhook.test"], ["webhook.test"]),
        ([], []),
    ],
)
def test_known_event_types_are_stored_once(client, email_outbox, monkeypatch, event_types, stored):
    owner = _owner(client, email_outbox, monkeypatch)

    assert _created(owner, event_types=event_types)["endpoint"]["event_types"] == stored


# --- keyring ---------------------------------------------------------------------------


def test_without_a_keyring_create_and_rotate_are_503(client, email_outbox, monkeypatch, settings):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _created(owner)["endpoint"]["id"]
    monkeypatch.setattr(settings, "webhook_secret_keys", "")
    monkeypatch.setattr(settings, "webhook_secret_active_key_id", "")

    created = _create(owner, "https://hooks.example.com/second")
    rotated = owner.post(f"{ENDPOINTS}/{endpoint_id}/signing-keys", json={})

    for response in (created, rotated):
        assert response.status_code == 503, response.text
        assert _error(response) == "webhook_signing_unavailable"
    assert len(owner.get(ENDPOINTS).json()["items"]) == 1
    with inspection_session_factory()() as db:
        assert len(db.scalars(select(WebhookSigningKey)).all()) == 1


# --- rotation --------------------------------------------------------------------------


@pytest.mark.parametrize("overlap", [-1, 604801, "60", 1.5, True])
def test_rotation_overlap_outside_0_to_7_days_is_422(client, email_outbox, monkeypatch, overlap):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _created(owner)["endpoint"]["id"]

    response = owner.post(
        f"{ENDPOINTS}/{endpoint_id}/signing-keys", json={"overlap_seconds": overlap}
    )

    assert response.status_code == 422, response.text


@pytest.mark.parametrize("overlap", [0, 604800])
def test_rotation_overlap_bounds_are_accepted(client, email_outbox, monkeypatch, overlap):
    owner = _owner(client, email_outbox, monkeypatch)
    created = _created(owner)
    endpoint_id = created["endpoint"]["id"]
    before = datetime.now(UTC)

    response = owner.post(
        f"{ENDPOINTS}/{endpoint_id}/signing-keys", json={"overlap_seconds": overlap}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"key_id", "signing_secret", "previous_key_id", "previous_key_expires_at"}
    assert body["previous_key_id"] == created["key_id"]
    expires = datetime.fromisoformat(body["previous_key_expires_at"].replace("Z", "+00:00"))
    expires = expires if expires.tzinfo else expires.replace(tzinfo=UTC)
    assert before + timedelta(seconds=overlap) - timedelta(seconds=1) <= expires
    assert expires <= datetime.now(UTC) + timedelta(seconds=overlap)
    keys = {
        key["key_id"]: key for key in owner.get(f"{ENDPOINTS}/{endpoint_id}").json()["signing_keys"]
    }
    assert keys[body["key_id"]]["state"] == "active"
    assert keys[created["key_id"]]["state"] == ("expired" if overlap == 0 else "retiring")


def test_rotation_default_overlap_is_one_day_and_retiring_keys_keep_their_expiry(
    client, email_outbox, monkeypatch
):
    owner = _owner(client, email_outbox, monkeypatch)
    created = _created(owner)
    rotate = f"{ENDPOINTS}/{created['endpoint']['id']}/signing-keys"

    first = owner.post(rotate)  # no body at all
    second = owner.post(rotate, json={"overlap_seconds": 3600})

    assert first.status_code == 201 and second.status_code == 201, (first.text, second.text)
    first_expiry = datetime.fromisoformat(first.json()["previous_key_expires_at"])
    first_expiry = first_expiry if first_expiry.tzinfo else first_expiry.replace(tzinfo=UTC)
    assert abs(first_expiry - (datetime.now(UTC) + timedelta(days=1))) < timedelta(minutes=1)
    assert second.json()["previous_key_id"] == first.json()["key_id"]
    keys = {
        key["key_id"]: key
        for key in owner.get(f"{ENDPOINTS}/{created['endpoint']['id']}").json()["signing_keys"]
    }
    assert [key["state"] for key in keys.values()].count("active") == 1
    assert keys[second.json()["key_id"]]["state"] == "active"
    assert keys[first.json()["key_id"]]["state"] == "retiring"
    assert keys[created["key_id"]]["state"] == "retiring"
    original_expiry = datetime.fromisoformat(keys[created["key_id"]]["expires_at"])
    original_expiry = (
        original_expiry if original_expiry.tzinfo else original_expiry.replace(tzinfo=UTC)
    )
    assert abs(original_expiry - first_expiry) < timedelta(seconds=1)
    assert first.json()["signing_secret"] != second.json()["signing_secret"]


# --- the rotation cap (SPEC A5.3) -----------------------------------------------------


def _unexpired_key_ids(owner, endpoint_id: str) -> list[str]:
    keys = owner.get(f"{ENDPOINTS}/{endpoint_id}").json()["signing_keys"]
    return sorted(key["key_id"] for key in keys if key["state"] != "expired")


def test_rotation_is_capped_at_five_unexpired_keys_but_overlap_zero_always_works(
    client, email_outbox, monkeypatch
):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _created(owner)["endpoint"]["id"]
    rotate = f"{ENDPOINTS}/{endpoint_id}/signing-keys"
    for _ in range(4):
        assert owner.post(rotate, json={"overlap_seconds": 3600}).status_code == 201
    before = owner.get(f"{ENDPOINTS}/{endpoint_id}").json()["signing_keys"]
    assert len(_unexpired_key_ids(owner, endpoint_id)) == 5

    refused = owner.post(rotate, json={"overlap_seconds": 3600})

    assert refused.status_code == 409, refused.text
    assert _error(refused) == "webhook_signing_key_limit"
    assert owner.get(f"{ENDPOINTS}/{endpoint_id}").json()["signing_keys"] == before

    # A leaked active key can always be rotated away: overlap 0 expires it at once.
    emergency = owner.post(rotate, json={"overlap_seconds": 0})
    assert emergency.status_code == 201, emergency.text
    assert len(_unexpired_key_ids(owner, endpoint_id)) == 5
    assert owner.post(rotate, json={"overlap_seconds": 3600}).status_code == 409

    # Expired keys do not count: once a retiring key expires, a normal rotation works.
    with inspection_session_factory()() as db:
        retiring = db.scalars(
            select(WebhookSigningKey)
            .where(
                WebhookSigningKey.endpoint_id == endpoint_id,
                WebhookSigningKey.expires_at.is_not(None),
            )
            .order_by(WebhookSigningKey.expires_at.desc())
        ).first()
        retiring.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert owner.post(rotate, json={"overlap_seconds": 3600}).status_code == 201
    assert len(_unexpired_key_ids(owner, endpoint_id)) == 5


# --- the test route --------------------------------------------------------------------


def test_the_test_route_queues_one_delivery_even_without_a_subscription(
    client, email_outbox, monkeypatch
):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _created(owner, event_types=[])["endpoint"]["id"]

    response = owner.post(f"{ENDPOINTS}/{endpoint_id}/test")

    assert response.status_code == 202, response.text
    body = response.json()
    assert set(body) == {"delivery_id", "event_id"}
    with inspection_session_factory()() as db:
        delivery = db.get(WebhookDelivery, body["delivery_id"])
        assert delivery.event_id == body["event_id"]
        assert delivery.endpoint_id == endpoint_id
        assert delivery.workspace_id == owner.workspace_id
        assert delivery.event_type == "webhook.test"
        # With tasks_eager the request never sends: the row waits for a worker.
        assert delivery.state == "pending" and delivery.attempt_count == 0
        envelope = json.loads(delivery.payload)
    assert envelope["data"] == {"endpoint_id": endpoint_id}
    assert envelope["id"] == body["event_id"]
    assert envelope["workspace_id"] == owner.workspace_id


def test_the_test_route_refuses_an_inactive_endpoint(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    one = f"{ENDPOINTS}/{_created(owner)['endpoint']['id']}"
    assert owner.patch(one, json={"is_active": False}).status_code == 200

    refused = owner.post(f"{one}/test")
    assert refused.status_code == 409 and _error(refused) == "webhook_endpoint_inactive"

    assert owner.patch(one, json={"is_active": True}).status_code == 200
    assert owner.post(f"{one}/test").status_code == 202


def _delivery_count(workspace_id: str) -> int:
    with inspection_session_factory()() as db:
        return len(
            db.scalars(
                select(WebhookDelivery).where(WebhookDelivery.workspace_id == workspace_id)
            ).all()
        )


def test_the_test_route_is_rate_limited_per_workspace(client, email_outbox, monkeypatch, settings):
    """SPEC A5.2: over the limit, 429 and no delivery row."""
    monkeypatch.setattr(settings, "webhook_test_rate_limit_per_hour", 2)
    owner = _owner(client, email_outbox, monkeypatch)
    test = f"{ENDPOINTS}/{_created(owner)['endpoint']['id']}/test"

    assert [owner.post(test).status_code for _ in range(2)] == [202, 202]
    refused = owner.post(test)

    assert refused.status_code == 429, refused.text
    assert _error(refused) == "webhook_test_rate_limited"
    assert int(refused.headers["Retry-After"]) > 0
    assert _delivery_count(owner.workspace_id) == 2
    # Another workspace has its own budget.
    other = _persona(client, email_outbox, "webhook-rate-other@example.com")
    other_test = f"{ENDPOINTS}/{_created(other)['endpoint']['id']}/test"
    assert other.post(other_test).status_code == 202


@pytest.mark.parametrize(("fail_open", "status"), [(False, 503), (True, 202)])
def test_an_unavailable_limiter_is_handled_like_uploads(
    client, email_outbox, monkeypatch, settings, fail_open, status
):
    owner = _owner(client, email_outbox, monkeypatch)
    test = f"{ENDPOINTS}/{_created(owner)['endpoint']['id']}/test"
    # Outside test and development the limiter has no in-memory fallback, and
    # the test app's Redis (port 1) refuses: the limiter is unavailable.
    monkeypatch.setattr(settings, "environment", "staging")
    monkeypatch.setattr(settings, "rate_limit_fail_open", fail_open)

    response = owner.post(test)

    assert response.status_code == status, response.text
    if status == 503:
        assert _error(response) == "rate_limiter_unavailable"
    assert _delivery_count(owner.workspace_id) == (0 if status == 503 else 1)


# --- the active endpoint limit ---------------------------------------------------------


def test_the_21st_active_endpoint_is_refused(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    ids = [_created(owner, f"https://hooks.example.com/{n}")["endpoint"]["id"] for n in range(20)]

    refused = _create(owner, "https://hooks.example.com/21")
    assert refused.status_code == 409 and _error(refused) == "webhook_endpoint_limit"

    assert owner.patch(f"{ENDPOINTS}/{ids[0]}", json={"is_active": False}).status_code == 200
    last = _created(owner, "https://hooks.example.com/21")
    reactivated = owner.patch(f"{ENDPOINTS}/{ids[0]}", json={"is_active": True})
    assert reactivated.status_code == 409 and _error(reactivated) == "webhook_endpoint_limit"
    # Patching an endpoint that is already active is not a re-activation.
    assert (
        owner.patch(f"{ENDPOINTS}/{last['endpoint']['id']}", json={"is_active": True}).status_code
        == 200
    )


# --- audit and secrets -----------------------------------------------------------------


def test_every_change_is_audited_with_safe_metadata(client, email_outbox, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    owner = _owner(client, email_outbox, monkeypatch)
    url = "https://hooks.example.com/x?token=query-secret-value"
    created = _created(owner, url)
    endpoint_id = created["endpoint"]["id"]
    one = f"{ENDPOINTS}/{endpoint_id}"
    assert (
        owner.patch(one, json={"url": "https://crm.example.com/y?token=other-query"}).status_code
        == 200
    )
    rotated = owner.post(f"{one}/signing-keys", json={})
    queued = owner.post(f"{one}/test")
    assert rotated.status_code == 201 and queued.status_code == 202

    with inspection_session_factory()() as db:
        events = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_type.like("webhook.%"))
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
        ).all()
        rows = {
            event.event_type: (
                event.target_type,
                event.target_id,
                event.details,
                event.actor_user_id,
            )
            for event in events
        }
    assert set(rows) == {
        "webhook.endpoint_created",
        "webhook.endpoint_updated",
        "webhook.signing_key_rotated",
        "webhook.test_queued",
    }
    for target_type, target_id, _details, actor in rows.values():
        assert (target_type, target_id, actor) == ("webhook_endpoint", endpoint_id, owner.user_id)
    created_details = rows["webhook.endpoint_created"][2]
    assert created_details["key_id"] == created["key_id"]
    assert created_details["url_scheme"] == "https"
    assert created_details["url_host"] == "hooks.example.com"
    assert created_details["event_types"] == ["webhook.test"]
    assert created_details["is_active"] is True
    updated_details = rows["webhook.endpoint_updated"][2]
    assert updated_details["changed_fields"] == ["url"]
    assert updated_details["url_host"] == "crm.example.com"
    rotated_details = rows["webhook.signing_key_rotated"][2]
    assert rotated_details["key_id"] == rotated.json()["key_id"]
    assert rotated_details["previous_key_id"] == created["key_id"]
    assert rows["webhook.test_queued"][2]["delivery_id"] == queued.json()["delivery_id"]

    secrets = (created["signing_secret"], rotated.json()["signing_secret"])
    audit_text = json.dumps([details for _t, _i, details, _a in rows.values()])
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    for text in (audit_text, log_text):
        for secret in secrets:
            assert secret not in text
        assert "query-secret-value" not in text and "other-query" not in text
        assert "/x?token" not in text


def test_the_secret_appears_only_in_create_and_rotate(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    created = _created(owner)
    one = f"{ENDPOINTS}/{created['endpoint']['id']}"
    rotated = owner.post(f"{one}/signing-keys", json={}).json()

    later = [
        owner.get(ENDPOINTS),
        owner.get(one),
        owner.patch(one, json={"description": "after"}),
        owner.post(f"{one}/test"),
    ]

    for response in later:
        assert response.status_code in (200, 202), response.text
        assert created["signing_secret"] not in response.text
        assert rotated["signing_secret"] not in response.text
