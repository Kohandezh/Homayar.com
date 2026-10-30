"""HOM-90: admin diagnostics and redelivery of webhook deliveries (ADR 0050 D10).

Five routes under ``/api/v1/webhooks``, each behind ``webhook:manage``: the
delivery list and detail, an endpoint's delivery summary, one redelivery and
the bulk redelivery of an endpoint's dead letters. Rows in ``dead_letter`` or
``failed`` are written directly here: the retry schedule that produces them is
another work unit. Every request goes through ``httpx.MockTransport``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from homayar_client import verify_webhook
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select, text

from backend.config import Settings
from backend.database.saas_models import (
    AuditEvent,
    WebhookDelivery,
    WebhookDeliveryAttempt,
)
from backend.jobs import webhook_delivery
from backend.jobs.webhook_delivery import deliver_webhook
from backend.routers.v1 import webhooks as webhooks_router
from backend.security.authorization import API_KEY_SCOPES
from backend.services import webhook_service
from backend.services.rate_limit_service import RateLimiter
from backend.services.webhook_signing import encode_payload
from backend.tests import test_row_level_security as _rls
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _workspace_with_roles
from backend.tests.test_webhook_api import (
    ENDPOINTS,
    HOOK,
    KEYRING_KEY,
    _created,
    _error,
    _owner,
    _persona,
)
from backend.tests.test_webhook_delivery import Receiver, _attempts, _delivery, _enqueue, _update

# PostgreSQL probe role: the fixture is discovered from this module namespace.
probe_role = _rls.probe_role

DELIVERIES = "/api/v1/webhooks/deliveries"
STATES = ("pending", "sending", "delivered", "failed", "dead_letter")
VIEW_FIELDS = {
    "id",
    "endpoint_id",
    "event_id",
    "event_type",
    "state",
    "attempt_count",
    "next_attempt_at",
    "last_attempt_at",
    "delivered_at",
    "last_status_code",
    "last_error",
    "created_at",
    "updated_at",
}
ATTEMPT_FIELDS = {
    "attempt_number",
    "started_at",
    "duration_ms",
    "outcome",
    "status_code",
    "error_code",
    "signing_key_ids",
    "request_timestamp",
}
KEY_ID = "whk_" + "0" * 24
SENTINEL = "sentinel-transcript-text-9c41"


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
    """Pin tasks_eager on: nothing is published unless a test records it."""
    monkeypatch.setattr(settings, "tasks_eager", True)


@pytest.fixture
def publishes(monkeypatch) -> list[tuple[str, str]]:
    """Record each publish from the routes with the row's state at that moment.

    The state is read in a new session, so ``pending`` proves the publish ran
    after the commit.
    """
    seen: list[tuple[str, str]] = []

    def record(delivery_id: str, _settings) -> bool:
        with inspection_session_factory()() as db:
            seen.append((delivery_id, db.get(WebhookDelivery, delivery_id).state))
        return True

    monkeypatch.setattr(webhooks_router, "publish_delivery", record)
    return seen


@pytest.fixture
def limiter_hits(monkeypatch) -> list[str]:
    """Every redelivery rate-limit key hit, in order (the limiter still counts)."""
    keys: list[str] = []
    original = RateLimiter.hit

    def hit(self, key: str, *, limit: int, window_seconds: int) -> None:
        if key.startswith("webhook_redeliver:"):
            keys.append(key)
        original(self, key, limit=limit, window_seconds=window_seconds)

    monkeypatch.setattr(RateLimiter, "hit", hit)
    return keys


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _iso(value: str | None) -> datetime | None:
    return None if value is None else _utc(datetime.fromisoformat(value))


def _seed(
    workspace_id: str,
    endpoint_id: str,
    *,
    state: str = "dead_letter",
    payload: bool = True,
    created_at: datetime | None = None,
    event_type: str = "webhook.test",
    attempt_count: int = 1,
    data: dict | None = None,
) -> str:
    """One delivery row written directly, in any state."""
    created_at = created_at or datetime.now(UTC)
    event_id = str(uuid.uuid4())
    body = (
        encode_payload(
            event_id=event_id,
            event_type=event_type,
            workspace_id=workspace_id,
            data={"note": "ids only"} if data is None else data,
            created_at=created_at,
        )
        if payload
        else None
    )
    row = WebhookDelivery(
        workspace_id=workspace_id,
        endpoint_id=endpoint_id,
        event_id=event_id,
        event_type=event_type,
        state=state,
        attempt_count=attempt_count,
        next_attempt_at=(
            created_at + timedelta(hours=1) if state in ("pending", "sending") else None
        ),
        last_status_code=None if state == "delivered" else 500,
        last_error=None if state == "delivered" else "http_error",
        payload=body,
        payload_sha256=hashlib.sha256(body).hexdigest() if body is not None else None,
        last_attempt_at=created_at if attempt_count else None,
        delivered_at=created_at if state == "delivered" else None,
        created_at=created_at,
        updated_at=created_at,
    )
    with inspection_session_factory()() as db:
        db.add(row)
        db.commit()
        return row.id


def _attempt(
    workspace_id: str,
    delivery_id: str,
    number: int,
    *,
    started_at: datetime,
    outcome: str = "failed",
    error_code: str | None = "http_error",
    status_code: int | None = 500,
) -> None:
    with inspection_session_factory()() as db:
        db.add(
            WebhookDeliveryAttempt(
                workspace_id=workspace_id,
                delivery_id=delivery_id,
                attempt_number=number,
                request_timestamp=int(started_at.timestamp()),
                signing_key_ids=[KEY_ID],
                started_at=started_at,
                duration_ms=12,
                outcome=outcome,
                status_code=status_code,
                error_code=error_code,
            )
        )
        db.commit()


def _endpoint(actor, url: str = HOOK) -> str:
    return _created(actor, url)["endpoint"]["id"]


def _audit_rows(event_type: str) -> list[dict]:
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_type == event_type)
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
        ).all()
        return [
            {
                "target_type": row.target_type,
                "target_id": row.target_id,
                "details": row.details,
                "actor_user_id": row.actor_user_id,
                "workspace_id": row.workspace_id,
            }
            for row in rows
        ]


def _redelivery_audit_count() -> int:
    with inspection_session_factory()() as db:
        return (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.event_type.in_(
                        ["webhook.delivery_redelivered", "webhook.dead_letters_redelivered"]
                    )
                )
            )
            or 0
        )


def _redeliver(actor, delivery_id: str):
    return actor.post(f"{DELIVERIES}/{delivery_id}/redeliver")


def _bulk(actor, endpoint_id: str, **body):
    path = f"{ENDPOINTS}/{endpoint_id}/redeliver-dead-letters"
    return actor.post(path, json=body) if body else actor.post(path)


def _five_routes(actor, endpoint_id: str, delivery_id: str) -> list:
    return [
        actor.get(DELIVERIES),
        actor.get(f"{DELIVERIES}/{delivery_id}"),
        actor.get(f"{ENDPOINTS}/{endpoint_id}/delivery-summary"),
        _redeliver(actor, delivery_id),
        _bulk(actor, endpoint_id),
    ]


# --- B1: the list ----------------------------------------------------------------------


def test_the_list_is_newest_first_with_only_the_view_fields(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner)
    base = datetime.now(UTC).replace(microsecond=0)
    ids = [
        _seed(owner.workspace_id, endpoint_id, created_at=base + timedelta(seconds=n))
        for n in range(3)
    ]
    other = _persona(client, email_outbox, "diag-other@example.com")
    _seed(other.workspace_id, _endpoint(other))

    listed = owner.get(DELIVERIES)

    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert set(body) == {"items", "next_cursor"}
    assert body["next_cursor"] is None
    assert [item["id"] for item in body["items"]] == ids[::-1]
    for item in body["items"]:
        assert set(item) == VIEW_FIELDS
    newest = body["items"][0]
    assert newest["endpoint_id"] == endpoint_id
    assert newest["state"] == "dead_letter"
    assert newest["attempt_count"] == 1
    assert newest["last_status_code"] == 500
    assert newest["last_error"] == "http_error"
    assert _iso(newest["created_at"]) == base + timedelta(seconds=2)


def test_the_list_filters_by_endpoint_state_and_event_type(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    first = _endpoint(owner, "https://hooks.example.com/1")
    second = _endpoint(owner, "https://hooks.example.com/2")
    dead = _seed(owner.workspace_id, first, state="dead_letter")
    failed = _seed(owner.workspace_id, first, state="failed")
    delivered = _seed(owner.workspace_id, second, state="delivered")
    other_type = _seed(owner.workspace_id, second, event_type="meeting.completed")

    def ids(**params) -> set[str]:
        response = owner.get(DELIVERIES, params=params)
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    assert ids() == {dead, failed, delivered, other_type}
    assert ids(endpoint_id=first) == {dead, failed}
    assert ids(state="failed") == {failed}
    assert ids(state="dead_letter") == {dead, other_type}
    assert ids(event_type="meeting.completed") == {other_type}
    assert ids(endpoint_id=second, state="delivered") == {delivered}
    assert ids(state="pending") == set()


def test_paging_walks_every_row_once_and_ends_with_a_null_cursor(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner)
    base = datetime.now(UTC).replace(microsecond=0)
    # Two pairs share a created_at: the id breaks the tie.
    times = [
        base,
        base,
        base + timedelta(seconds=1),
        base + timedelta(seconds=2),
        base + timedelta(seconds=2),
    ]
    ids = [_seed(owner.workspace_id, endpoint_id, created_at=at) for at in times]

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        page = owner.get(DELIVERIES, params=params)
        assert page.status_code == 200, page.text
        body = page.json()
        assert len(body["items"]) <= 2
        seen.extend(item["id"] for item in body["items"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert pages < 10

    assert len(seen) == len(set(seen)) == len(ids)
    assert set(seen) == set(ids)
    expected = sorted(zip(times, ids, strict=True), key=lambda pair: (pair[0], pair[1]))
    assert seen == [delivery_id for _at, delivery_id in reversed(expected)]
    assert pages == 3


def _b64(value: dict | str) -> str:
    raw = value if isinstance(value, str) else json.dumps(value)
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


@pytest.mark.parametrize(
    "cursor",
    [
        "not-a-cursor!",
        _b64("not json"),
        _b64({"v": 1, "id": str(uuid.uuid4())}),
        _b64({"v": 2, "created_at": "2026-10-30T12:00:00+00:00", "id": str(uuid.uuid4())}),
        _b64({"v": 1, "created_at": "2026-10-30T12:00:00", "id": str(uuid.uuid4())}),
        _b64({"v": 1, "created_at": "2026-10-30T12:00:00+00:00", "id": "not-a-uuid"}),
        _b64({"v": 1, "due_at": "2026-10-30T12:00:00+00:00", "id": str(uuid.uuid4())}),
    ],
)
def test_a_bad_cursor_is_400_invalid_cursor(client, email_outbox, monkeypatch, cursor):
    owner = _owner(client, email_outbox, monkeypatch)

    refused = owner.get(DELIVERIES, params={"cursor": cursor})

    assert refused.status_code == 400, refused.text
    assert _error(refused) == "invalid_cursor"


@pytest.mark.parametrize(
    "params", [{"state": "lost"}, {"state": "PENDING"}, {"limit": 0}, {"limit": 101}]
)
def test_a_bad_state_or_limit_is_422(client, email_outbox, monkeypatch, params):
    owner = _owner(client, email_outbox, monkeypatch)

    assert owner.get(DELIVERIES, params=params).status_code == 422
    # Allow-control: the bounds and a real state.
    assert owner.get(DELIVERIES, params={"limit": 1, "state": "pending"}).status_code == 200
    assert owner.get(DELIVERIES, params={"limit": 100}).status_code == 200


# --- B2: the detail --------------------------------------------------------------------


def test_the_detail_lists_attempts_in_order_with_their_total(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner)
    delivery_id = _seed(owner.workspace_id, endpoint_id, attempt_count=3)
    base = datetime.now(UTC).replace(microsecond=0)
    for number in (2, 3, 1):
        _attempt(
            owner.workspace_id, delivery_id, number, started_at=base + timedelta(seconds=number)
        )

    detail = owner.get(f"{DELIVERIES}/{delivery_id}")

    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert set(body) == VIEW_FIELDS | {"attempts", "attempt_total"}
    assert body["id"] == delivery_id
    assert body["attempt_total"] == 3
    assert [attempt["attempt_number"] for attempt in body["attempts"]] == [1, 2, 3]
    first = body["attempts"][0]
    assert set(first) == ATTEMPT_FIELDS
    assert first["outcome"] == "failed"
    assert first["status_code"] == 500
    assert first["error_code"] == "http_error"
    assert first["signing_key_ids"] == [KEY_ID]
    assert first["duration_ms"] == 12
    assert first["request_timestamp"] == int((base + timedelta(seconds=1)).timestamp())
    assert _iso(first["started_at"]) == base + timedelta(seconds=1)


def test_a_delivery_without_attempts_has_an_empty_list(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    delivery_id = _seed(owner.workspace_id, _endpoint(owner), state="pending", attempt_count=0)

    body = owner.get(f"{DELIVERIES}/{delivery_id}").json()

    assert body["attempts"] == []
    assert body["attempt_total"] == 0


def test_the_detail_shows_the_latest_100_attempts(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    delivery_id = _seed(owner.workspace_id, _endpoint(owner), attempt_count=102)
    base = datetime.now(UTC).replace(microsecond=0)
    with inspection_session_factory()() as db:
        db.add_all(
            WebhookDeliveryAttempt(
                workspace_id=owner.workspace_id,
                delivery_id=delivery_id,
                attempt_number=number,
                request_timestamp=int(base.timestamp()) + number,
                signing_key_ids=[KEY_ID],
                started_at=base + timedelta(seconds=number),
                duration_ms=1,
                outcome="failed",
                error_code="timeout",
            )
            for number in range(1, 103)
        )
        db.commit()

    body = owner.get(f"{DELIVERIES}/{delivery_id}").json()

    assert body["attempt_total"] == 102
    assert [attempt["attempt_number"] for attempt in body["attempts"]] == list(range(3, 103))


def test_an_unknown_delivery_is_404(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)

    for response in (
        owner.get(f"{DELIVERIES}/{uuid.uuid4()}"),
        _redeliver(owner, str(uuid.uuid4())),
    ):
        assert response.status_code == 404, response.text
        assert _error(response) == "webhook_delivery_not_found"


# --- B3: the summary -------------------------------------------------------------------


def test_the_summary_counts_every_state_and_the_last_results(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner, "https://hooks.example.com/1")
    other_endpoint = _endpoint(owner, "https://hooks.example.com/2")
    base = datetime.now(UTC).replace(microsecond=0)
    dead = [_seed(owner.workspace_id, endpoint_id) for _ in range(2)]
    failed = _seed(owner.workspace_id, endpoint_id, state="failed")
    _seed(owner.workspace_id, endpoint_id, state="delivered", created_at=base)
    newest_delivered = _seed(
        owner.workspace_id,
        endpoint_id,
        state="delivered",
        created_at=base + timedelta(seconds=5),
    )
    _attempt(owner.workspace_id, dead[0], 1, started_at=base + timedelta(seconds=1))
    _attempt(
        owner.workspace_id,
        failed,
        1,
        started_at=base + timedelta(seconds=3),
        error_code="timeout",
        status_code=None,
    )
    # A newer success does not hide the last failure.
    _attempt(
        owner.workspace_id,
        newest_delivered,
        1,
        started_at=base + timedelta(seconds=5),
        outcome="delivered",
        error_code=None,
        status_code=204,
    )
    # Another endpoint's rows and failures are not counted.
    other_dead = _seed(owner.workspace_id, other_endpoint, created_at=base + timedelta(seconds=9))
    _attempt(owner.workspace_id, other_dead, 1, started_at=base + timedelta(seconds=9))

    summary = owner.get(f"{ENDPOINTS}/{endpoint_id}/delivery-summary")

    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert set(body) == {
        "endpoint_id",
        "counts",
        "last_delivered_at",
        "last_failure_at",
        "last_failure_error_code",
    }
    assert body["endpoint_id"] == endpoint_id
    assert body["counts"] == {
        "pending": 0,
        "sending": 0,
        "delivered": 2,
        "failed": 1,
        "dead_letter": 2,
    }
    assert _iso(body["last_delivered_at"]) == base + timedelta(seconds=5)
    assert _iso(body["last_failure_at"]) == base + timedelta(seconds=3)
    assert body["last_failure_error_code"] == "timeout"


def test_an_endpoint_without_deliveries_has_zero_counts(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner)

    body = owner.get(f"{ENDPOINTS}/{endpoint_id}/delivery-summary").json()

    assert body == {
        "endpoint_id": endpoint_id,
        "counts": dict.fromkeys(STATES, 0),
        "last_delivered_at": None,
        "last_failure_at": None,
        "last_failure_error_code": None,
    }
    unknown = owner.get(f"{ENDPOINTS}/no-such-endpoint/delivery-summary")
    assert unknown.status_code == 404 and _error(unknown) == "webhook_endpoint_not_found"


# --- B4: roles and keys ----------------------------------------------------------------


def test_member_and_viewer_are_refused_and_admin_is_allowed(client, email_outbox, monkeypatch):
    owner, people, _ = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"admin": "admin", "member": "member", "viewer": "viewer"},
    )
    endpoint_id = _endpoint(owner)
    delivery_id = _seed(owner.workspace_id, endpoint_id)

    for name in ("member", "viewer"):
        for response in _five_routes(people[name], endpoint_id, delivery_id):
            assert response.status_code == 403, (name, response.text)
            assert _error(response) == "permission_denied"
    assert _delivery(delivery_id)["state"] == "dead_letter"

    statuses = [
        response.status_code for response in _five_routes(people["admin"], endpoint_id, delivery_id)
    ]
    assert statuses == [200, 200, 200, 202, 202]


def test_an_api_key_with_every_scope_is_refused_on_every_new_route(
    client, email_outbox, monkeypatch
):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner)
    delivery_id = _seed(owner.workspace_id, endpoint_id)
    minted = _mint(
        client,
        owner.headers,
        owner.cookies,
        name="automation",
        scopes=sorted(scope.value for scope in API_KEY_SCOPES),
    )
    assert minted.status_code == 201, minted.text
    headers = _bearer(minted.json()["key"])

    saved = dict(client.cookies)
    client.cookies.clear()
    try:
        refused = [
            client.get(DELIVERIES, headers=headers),
            client.get(f"{DELIVERIES}/{delivery_id}", headers=headers),
            client.get(f"{ENDPOINTS}/{endpoint_id}/delivery-summary", headers=headers),
            client.post(f"{DELIVERIES}/{delivery_id}/redeliver", headers=headers),
            client.post(f"{ENDPOINTS}/{endpoint_id}/redeliver-dead-letters", headers=headers),
        ]
    finally:
        client.cookies.update(saved)

    for response in refused:
        assert response.status_code == 403, response.text
        assert _error(response) == "api_key_scope_denied"
    assert _delivery(delivery_id)["state"] == "dead_letter"
    # Allow-control: the key's owner in a session.
    statuses = [r.status_code for r in _five_routes(owner, endpoint_id, delivery_id)]
    assert statuses == [200, 200, 200, 202, 202]


# --- B5: tenant isolation --------------------------------------------------------------


def _cross_workspace_refusals(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner)
    delivery_id = _seed(owner.workspace_id, endpoint_id)
    _attempt(owner.workspace_id, delivery_id, 1, started_at=datetime.now(UTC))
    other = _persona(client, email_outbox, "diag-intruder@example.com")
    _endpoint(other)
    before = _delivery(delivery_id)

    delivery_refusals = [
        other.get(f"{DELIVERIES}/{delivery_id}"),
        _redeliver(other, delivery_id),
    ]
    endpoint_refusals = [
        other.get(f"{ENDPOINTS}/{endpoint_id}/delivery-summary"),
        _bulk(other, endpoint_id),
    ]

    for response in delivery_refusals:
        assert response.status_code == 404, response.text
        assert _error(response) == "webhook_delivery_not_found"
    for response in endpoint_refusals:
        assert response.status_code == 404, response.text
        assert _error(response) == "webhook_endpoint_not_found"
    # The same body as an id that does not exist.
    unknown = other.get(f"{DELIVERIES}/{uuid.uuid4()}").json()["error"]
    foreign = delivery_refusals[0].json()["error"]
    assert (foreign["code"], foreign["message"]) == (unknown["code"], unknown["message"])
    assert other.get(DELIVERIES).json() == {"items": [], "next_cursor": None}
    assert other.get(DELIVERIES, params={"endpoint_id": endpoint_id}).json()["items"] == []
    assert _delivery(delivery_id) == before
    assert _redelivery_audit_count() == 0
    # Allow-control: the owner of the row's workspace.
    assert [item["id"] for item in owner.get(DELIVERIES).json()["items"]] == [delivery_id]
    assert owner.get(f"{DELIVERIES}/{delivery_id}").status_code == 200
    assert owner.get(f"{ENDPOINTS}/{endpoint_id}/delivery-summary").status_code == 200
    assert _redeliver(owner, delivery_id).status_code == 202
    return owner, other, delivery_id


def test_another_workspace_gets_404_and_an_empty_list(client, email_outbox, monkeypatch):
    _cross_workspace_refusals(client, email_outbox, monkeypatch)


@pytest.mark.postgres
def test_isolation_holds_under_row_level_security(client, email_outbox, monkeypatch, probe_role):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("row-level security is PostgreSQL evidence")
    # The test app runs as the non-bypass application role: the same answers.
    owner, other, delivery_id = _cross_workspace_refusals(client, email_outbox, monkeypatch)

    # And row-level security alone hides the rows, without the route's filter.
    engine = create_engine(probe_role, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            _rls._bind(connection, workspace_id=other.workspace_id)
            for table in ("webhook_deliveries", "webhook_delivery_attempts"):
                assert _rls._count(connection, table) == 0, table
            updated = connection.execute(
                text("UPDATE webhook_deliveries SET state = 'pending' WHERE id = :id"),
                {"id": delivery_id},
            )
            assert updated.rowcount == 0
            connection.rollback()
            _rls._bind(connection, workspace_id=owner.workspace_id)
            assert _rls._count(connection, "webhook_deliveries") == 1
            assert _rls._count(connection, "webhook_delivery_attempts") == 1
            connection.rollback()
    finally:
        engine.dispose()


# --- B6: one redelivery ----------------------------------------------------------------


@pytest.mark.parametrize("state", ["dead_letter", "failed"])
def test_redelivery_requeues_the_same_event_and_audits_it(
    client, email_outbox, monkeypatch, publishes, state
):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner)
    delivery_id = _seed(owner.workspace_id, endpoint_id, state=state, attempt_count=3)
    _attempt(owner.workspace_id, delivery_id, 3, started_at=datetime.now(UTC))
    before = _delivery(delivery_id)
    attempts_before = _attempts(delivery_id)

    started = datetime.now(UTC)
    response = _redeliver(owner, delivery_id)
    finished = datetime.now(UTC)

    assert response.status_code == 202, response.text
    assert response.json() == {
        "delivery_id": delivery_id,
        "event_id": before["event_id"],
        "state": "pending",
    }
    after = _delivery(delivery_id)
    assert after["state"] == "pending"
    assert started - timedelta(seconds=1) <= _utc(after["next_attempt_at"]) <= finished
    for column in (
        "event_id",
        "event_type",
        "endpoint_id",
        "payload",
        "payload_sha256",
        "attempt_count",
        "last_status_code",
        "last_error",
        "last_attempt_at",
        "delivered_at",
    ):
        assert after[column] == before[column], column
    assert _attempts(delivery_id) == attempts_before
    [audit] = _audit_rows("webhook.delivery_redelivered")
    assert audit == {
        "target_type": "webhook_delivery",
        "target_id": delivery_id,
        "details": {
            "endpoint_id": endpoint_id,
            "event_id": before["event_id"],
            "event_type": "webhook.test",
            "previous_state": state,
        },
        "actor_user_id": owner.user_id,
        "workspace_id": owner.workspace_id,
    }
    assert publishes == [(delivery_id, "pending")]


# --- B7: refusals ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "payload"),
    [("pending", True), ("sending", True), ("delivered", True), ("dead_letter", False)],
)
def test_only_dead_letter_or_failed_rows_with_a_payload_are_redelivered(
    client, email_outbox, monkeypatch, publishes, limiter_hits, state, payload
):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner)
    refused_id = _seed(owner.workspace_id, endpoint_id, state=state, payload=payload)
    before = _delivery(refused_id)

    refused = _redeliver(owner, refused_id)

    assert refused.status_code == 409, refused.text
    assert _error(refused) == "webhook_delivery_not_redeliverable"
    assert _delivery(refused_id) == before
    assert _redelivery_audit_count() == 0
    assert limiter_hits == []
    assert publishes == []
    # Allow-control: the same endpoint, a dead letter with its payload.
    allowed = _redeliver(owner, _seed(owner.workspace_id, endpoint_id))
    assert allowed.status_code == 202, allowed.text
    assert len(limiter_hits) == 1


def test_an_inactive_endpoint_refuses_redelivery_until_it_is_reactivated(
    client, email_outbox, monkeypatch, publishes, limiter_hits
):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner)
    delivery_id = _seed(owner.workspace_id, endpoint_id)
    one = f"{ENDPOINTS}/{endpoint_id}"
    assert owner.patch(one, json={"is_active": False}).status_code == 200
    before = _delivery(delivery_id)

    for refused in (_redeliver(owner, delivery_id), _bulk(owner, endpoint_id)):
        assert refused.status_code == 409, refused.text
        assert _error(refused) == "webhook_endpoint_inactive"
    assert _delivery(delivery_id) == before
    assert _redelivery_audit_count() == 0
    assert limiter_hits == []

    assert owner.patch(one, json={"is_active": True}).status_code == 200
    assert _redeliver(owner, delivery_id).status_code == 202
    assert _delivery(delivery_id)["state"] == "pending"


# --- B8: the rate limit ----------------------------------------------------------------


def test_redelivery_is_rate_limited_per_workspace(
    client, email_outbox, monkeypatch, settings, publishes
):
    monkeypatch.setattr(settings, "webhook_redeliver_rate_limit_per_hour", 2)
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner)
    ids = [_seed(owner.workspace_id, endpoint_id) for _ in range(3)]

    # Allow-control: at the limit.
    assert [_redeliver(owner, delivery_id).status_code for delivery_id in ids[:2]] == [202, 202]
    before = _delivery(ids[2])
    refused = [_redeliver(owner, ids[2]), _bulk(owner, endpoint_id)]

    for response in refused:
        assert response.status_code == 429, response.text
        assert _error(response) == "webhook_redeliver_rate_limited"
        assert int(response.headers["Retry-After"]) > 0
    assert _delivery(ids[2]) == before
    assert len(_audit_rows("webhook.delivery_redelivered")) == 2
    assert _audit_rows("webhook.dead_letters_redelivered") == []
    assert [delivery_id for delivery_id, _ in publishes] == ids[:2]
    # The test route keeps its own budget and answers.
    assert owner.post(f"{ENDPOINTS}/{endpoint_id}/test").status_code == 202
    # Another workspace has its own budget.
    other = _persona(client, email_outbox, "diag-rate-other@example.com")
    other_endpoint = _endpoint(other)
    assert _redeliver(other, _seed(other.workspace_id, other_endpoint)).status_code == 202


def test_redeliver_and_bulk_share_the_rate_limit_key(
    client, email_outbox, monkeypatch, limiter_hits
):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner)

    assert _redeliver(owner, _seed(owner.workspace_id, endpoint_id)).status_code == 202
    assert _bulk(owner, endpoint_id).status_code == 202

    assert limiter_hits == [f"webhook_redeliver:workspace:{owner.workspace_id}"] * 2


@pytest.mark.parametrize(("fail_open", "status"), [(False, 503), (True, 202)])
def test_an_unavailable_limiter_is_handled_like_the_test_route(
    client, email_outbox, monkeypatch, settings, fail_open, status
):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner)
    delivery_id = _seed(owner.workspace_id, endpoint_id)
    bulk_row = _seed(owner.workspace_id, endpoint_id)
    # Outside test and development the limiter has no in-memory fallback, and
    # the test app's Redis (port 1) refuses: the limiter is unavailable.
    monkeypatch.setattr(settings, "environment", "staging")
    monkeypatch.setattr(settings, "rate_limit_fail_open", fail_open)

    responses = [_redeliver(owner, delivery_id), _bulk(owner, endpoint_id)]

    for response in responses:
        assert response.status_code == status, response.text
        if status == 503:
            assert _error(response) == "rate_limiter_unavailable"
    expected = "dead_letter" if status == 503 else "pending"
    assert _delivery(delivery_id)["state"] == expected
    assert _delivery(bulk_row)["state"] == expected


def test_the_redelivery_limit_setting_defaults_to_60_and_must_be_positive(monkeypatch):
    assert Settings(_env_file=None).webhook_redeliver_rate_limit_per_hour == 60
    monkeypatch.setenv("WEBHOOK_REDELIVER_RATE_LIMIT_PER_HOUR", "7")
    assert Settings(_env_file=None).webhook_redeliver_rate_limit_per_hour == 7
    monkeypatch.delenv("WEBHOOK_REDELIVER_RATE_LIMIT_PER_HOUR")
    for value in (0, -1):
        with pytest.raises(ValidationError, match="WEBHOOK_REDELIVER_RATE_LIMIT_PER_HOUR"):
            Settings(_env_file=None, webhook_redeliver_rate_limit_per_hour=value)
    # Allow-control: the lower bound.
    assert Settings(_env_file=None, webhook_redeliver_rate_limit_per_hour=1)


# --- B9: parallel redelivery (PostgreSQL) ----------------------------------------------


@pytest.mark.postgres
def test_two_parallel_redeliveries_change_the_row_once(
    client, email_outbox, monkeypatch, publishes
):
    """The second call waits on the row lock, sees ``pending`` and gets the 409."""
    if not is_postgres_url(configured_database_url()):
        pytest.skip("row locks are PostgreSQL evidence")
    owner = _owner(client, email_outbox, monkeypatch)
    delivery_id = _seed(owner.workspace_id, _endpoint(owner))
    original = RateLimiter.hit
    first_inside = threading.Event()
    calls: list[int] = []
    calls_lock = threading.Lock()

    def slow_first_hit(self, key: str, *, limit: int, window_seconds: int) -> None:
        with calls_lock:
            calls.append(1)
            first = len(calls) == 1
        if first:
            # The first call holds the row lock here while the second one starts.
            first_inside.set()
            time.sleep(1.5)
        original(self, key, limit=limit, window_seconds=window_seconds)

    monkeypatch.setattr(RateLimiter, "hit", slow_first_hit)
    results: dict[str, int] = {}
    errors: list[BaseException] = []

    def run(name: str) -> None:
        try:
            api = TestClient(client.app)
            response = api.post(
                f"{DELIVERIES}/{delivery_id}/redeliver",
                cookies=owner.cookies,
                headers=owner.headers,
            )
            results[name] = response.status_code
            if response.status_code == 409:
                assert _error(response) == "webhook_delivery_not_redeliverable"
        except BaseException as exc:  # reported below, not swallowed
            errors.append(exc)

    first = threading.Thread(target=run, args=("first",))
    second = threading.Thread(target=run, args=("second",))
    first.start()
    assert first_inside.wait(timeout=20)
    second.start()
    first.join(timeout=30)
    second.join(timeout=30)

    assert errors == []
    assert results == {"first": 202, "second": 409}
    # The second call never reached the limiter: it was refused after the lock.
    assert len(calls) == 1
    assert len(_audit_rows("webhook.delivery_redelivered")) == 1
    assert publishes == [(delivery_id, "pending")]
    assert _delivery(delivery_id)["state"] == "pending"


# --- B10: bulk redelivery --------------------------------------------------------------


def test_bulk_moves_only_this_endpoints_dead_letters_and_failures(
    client, email_outbox, monkeypatch, publishes
):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner, "https://hooks.example.com/1")
    other_endpoint = _endpoint(owner, "https://hooks.example.com/2")
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
    moved = [
        _seed(owner.workspace_id, endpoint_id, state="dead_letter", created_at=base),
        _seed(owner.workspace_id, endpoint_id, state="failed", created_at=base),
        _seed(owner.workspace_id, endpoint_id, state="dead_letter", created_at=base),
    ]
    kept = [
        _seed(owner.workspace_id, endpoint_id, state="dead_letter", payload=False),
        _seed(owner.workspace_id, endpoint_id, state="pending"),
        _seed(owner.workspace_id, endpoint_id, state="sending"),
        _seed(owner.workspace_id, endpoint_id, state="delivered"),
        _seed(owner.workspace_id, other_endpoint, state="dead_letter"),
    ]
    other = _persona(client, email_outbox, "diag-bulk-other@example.com")
    foreign = _seed(other.workspace_id, _endpoint(other))
    kept_before = {delivery_id: _delivery(delivery_id) for delivery_id in [*kept, foreign]}
    moved_before = {delivery_id: _delivery(delivery_id) for delivery_id in moved}

    started = datetime.now(UTC)
    response = _bulk(owner, endpoint_id)
    finished = datetime.now(UTC)

    assert response.status_code == 202, response.text
    assert response.json() == {"endpoint_id": endpoint_id, "redelivered": 3, "remaining": False}
    for delivery_id in moved:
        row = _delivery(delivery_id)
        assert row["state"] == "pending"
        assert started - timedelta(seconds=1) <= _utc(row["next_attempt_at"]) <= finished
        for column in ("event_id", "payload", "payload_sha256", "attempt_count", "last_error"):
            assert row[column] == moved_before[delivery_id][column], column
    for delivery_id, before in kept_before.items():
        assert _delivery(delivery_id) == before
    [audit] = _audit_rows("webhook.dead_letters_redelivered")
    assert audit["target_type"] == "webhook_endpoint"
    assert audit["target_id"] == endpoint_id
    assert audit["details"] == {"endpoint_id": endpoint_id, "count": 3, "since": None}
    assert publishes == []


def test_bulk_honours_since(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner)
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
    old = _seed(owner.workspace_id, endpoint_id, created_at=base)
    at_since = _seed(owner.workspace_id, endpoint_id, created_at=base + timedelta(minutes=10))
    newer = _seed(owner.workspace_id, endpoint_id, created_at=base + timedelta(minutes=20))
    since = base + timedelta(minutes=10)

    response = _bulk(owner, endpoint_id, since=since.isoformat())

    assert response.status_code == 202, response.text
    assert response.json()["redelivered"] == 2
    assert _delivery(old)["state"] == "dead_letter"
    assert _delivery(at_since)["state"] == "pending"
    assert _delivery(newer)["state"] == "pending"
    [audit] = _audit_rows("webhook.dead_letters_redelivered")
    assert audit["details"]["count"] == 2
    assert _iso(audit["details"]["since"]) == since


def test_bulk_with_since_null_or_no_body_has_no_filter(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner)
    old = datetime.now(UTC) - timedelta(days=30)
    first = _seed(owner.workspace_id, endpoint_id, created_at=old)

    assert _bulk(owner, endpoint_id, since=None).json()["redelivered"] == 1
    assert _delivery(first)["state"] == "pending"
    _update(WebhookDelivery, first, state="dead_letter", next_attempt_at=None)
    assert _bulk(owner, endpoint_id).json()["redelivered"] == 1
    assert [row["details"]["since"] for row in _audit_rows("webhook.dead_letters_redelivered")] == [
        None,
        None,
    ]
    refused = owner.post(
        f"{ENDPOINTS}/{endpoint_id}/redeliver-dead-letters", json={"since": "x", "extra": 1}
    )
    assert refused.status_code == 422


def test_bulk_is_capped_oldest_first_and_reports_what_remains(client, email_outbox, monkeypatch):
    monkeypatch.setattr(webhook_service, "WEBHOOK_REDELIVER_BATCH_LIMIT", 2)
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner)
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
    ids = [
        _seed(owner.workspace_id, endpoint_id, created_at=base + timedelta(seconds=n))
        for n in range(3)
    ]

    first = _bulk(owner, endpoint_id)

    assert first.json() == {"endpoint_id": endpoint_id, "redelivered": 2, "remaining": True}
    assert [_delivery(delivery_id)["state"] for delivery_id in ids] == [
        "pending",
        "pending",
        "dead_letter",
    ]
    second = _bulk(owner, endpoint_id)
    assert second.json() == {"endpoint_id": endpoint_id, "redelivered": 1, "remaining": False}
    assert [row["details"]["count"] for row in _audit_rows("webhook.dead_letters_redelivered")] == [
        2,
        1,
    ]


def test_bulk_with_nothing_to_move_is_202_and_still_audited(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _endpoint(owner)
    _seed(owner.workspace_id, endpoint_id, state="delivered")

    response = _bulk(owner, endpoint_id)

    assert response.status_code == 202, response.text
    assert response.json() == {"endpoint_id": endpoint_id, "redelivered": 0, "remaining": False}
    [audit] = _audit_rows("webhook.dead_letters_redelivered")
    assert audit["details"] == {"endpoint_id": endpoint_id, "count": 0, "since": None}


def test_the_batch_limit_is_500():
    assert webhook_service.WEBHOOK_REDELIVER_BATCH_LIMIT == 500


# --- B11: privacy ----------------------------------------------------------------------


def test_no_answer_audit_row_or_log_carries_the_payload_url_or_secret(
    client, email_outbox, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    owner = _owner(client, email_outbox, monkeypatch)
    url = "https://hooks.example.com/private-path-segment/in?token=query-secret-value"
    created = _created(owner, url)
    endpoint_id = created["endpoint"]["id"]
    delivery_id = _seed(owner.workspace_id, endpoint_id, data={"text": SENTINEL})
    bulk_id = _seed(owner.workspace_id, endpoint_id, data={"text": SENTINEL})
    _attempt(owner.workspace_id, delivery_id, 1, started_at=datetime.now(UTC))
    # Positive control: the sentinel is really in the stored payload.
    assert SENTINEL.encode() in _delivery(delivery_id)["payload"]
    assert SENTINEL.encode() in _delivery(bulk_id)["payload"]
    audit_before = _audit_ids()

    responses = [
        owner.get(DELIVERIES),
        owner.get(f"{DELIVERIES}/{delivery_id}"),
        owner.get(f"{ENDPOINTS}/{endpoint_id}/delivery-summary"),
        _redeliver(owner, delivery_id),
        _bulk(owner, endpoint_id),
        # Refusals too.
        _redeliver(owner, delivery_id),
        owner.get(DELIVERIES, params={"cursor": "bad"}),
    ]

    assert [response.status_code for response in responses] == [200, 200, 200, 202, 202, 409, 400]
    with inspection_session_factory()() as db:
        new_audit = [
            row.details
            for row in db.scalars(select(AuditEvent)).all()
            if row.id not in audit_before
        ]
    assert len(new_audit) == 2
    texts = {
        "answers": "\n".join(response.text for response in responses),
        "audit": json.dumps(new_audit),
        "logs": "\n".join(record.getMessage() for record in caplog.records),
    }
    for name, value in texts.items():
        for forbidden in (
            SENTINEL,
            "query-secret-value",
            "private-path-segment",
            created["signing_secret"],
            "payload_sha256",
        ):
            assert forbidden not in value, (name, forbidden)
    for response in responses[:3]:
        assert '"payload"' not in response.text
        assert '"url"' not in response.text


def _audit_ids() -> set[str]:
    with inspection_session_factory()() as db:
        return set(db.scalars(select(AuditEvent.id)).all())


# --- B12: the worker sends the same event again ----------------------------------------


def test_after_redelivery_the_worker_sends_the_same_id_and_bytes(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    created = _created(owner)
    first_send = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=10)
    [delivery_id] = _enqueue(owner.workspace_id, now=first_send - timedelta(seconds=1))
    failing = Receiver(httpx.Response(500))
    deliver_webhook(delivery_id, now=first_send, http_client=failing.client)
    [first_request] = failing.requests
    # The retry schedule (another work unit) ends here: the row is a dead letter.
    _update(WebhookDelivery, delivery_id, state="dead_letter", next_attempt_at=None)
    [first_attempt] = _attempts(delivery_id)

    assert _redeliver(owner, delivery_id).status_code == 202
    resend_at = datetime.now(UTC) + timedelta(seconds=1)
    receiver = Receiver(httpx.Response(204))
    deliver_webhook(delivery_id, now=resend_at, http_client=receiver.client)

    [resent] = receiver.requests
    assert resent.headers["Homayar-Webhook-Id"] == first_request.headers["Homayar-Webhook-Id"]
    assert resent.content == first_request.content
    event = verify_webhook(
        headers=resent.headers,
        body=resent.content,
        secrets={created["key_id"]: created["signing_secret"]},
        now=resend_at.timestamp(),
    )
    assert event["id"] == first_request.headers["Homayar-Webhook-Id"]
    attempts = _attempts(delivery_id)
    assert [attempt["attempt_number"] for attempt in attempts] == [
        first_attempt["attempt_number"],
        first_attempt["attempt_number"] + 1,
    ]
    assert attempts[0] == first_attempt
    assert _delivery(delivery_id)["state"] == "delivered"
