"""HOM-89: enqueue, the delivery worker and the sweeper (DESIGN sections 3 and 5).

Every request goes through ``httpx.MockTransport``; nothing reaches the network.
``now`` is pinned so the due check, the lease and the signed timestamp are exact.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import socket
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from celery.exceptions import SoftTimeLimitExceeded
from homayar_client import WebhookVerificationError, verify_webhook
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    WebhookDelivery,
    WebhookDeliveryAttempt,
    WebhookEndpoint,
)
from backend.database.tenant_context import TenantContext, bind_tenant_context, current_context
from backend.jobs import webhook_delivery
from backend.jobs.webhook_delivery import deliver_webhook, sweep_webhook_deliveries
from backend.observability import heartbeats
from backend.services.webhook_service import WEBHOOK_EVENT_TYPES, enqueue_event
from backend.tests import test_row_level_security as _rls
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_webhook_api import (
    ENDPOINTS,
    HOOK,
    KEYRING_KEY,
    _created,
    _owner,
    _persona,
)

# PostgreSQL probe role: the fixture is discovered from this module namespace.
probe_role = _rls.probe_role


class WorkerStopped(BaseException):
    """The worker process dies after the request left and before it recorded it."""


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
    """Pin tasks_eager on: a request or a sweep then publishes nothing (SPEC A4.2).

    Tests of the publish path turn it off themselves and record apply_async.
    """
    monkeypatch.setattr(settings, "tasks_eager", True)


@pytest.fixture
def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class Receiver:
    """A MockTransport that records each request and answers from a script.

    An answer is an ``httpx.Response`` or a function of the request. The last
    answer repeats.
    """

    def __init__(self, *answers, follow_redirects: bool = False) -> None:
        self.requests: list[httpx.Request] = []
        self._answers = list(answers) or [httpx.Response(204)]
        self.client = httpx.Client(
            transport=httpx.MockTransport(self._handle), follow_redirects=follow_redirects
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        return answer(request) if callable(answer) else answer


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


def _refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _enqueue(
    workspace_id: str,
    *,
    now: datetime,
    endpoint_id: str | None = None,
    event_type: str = "webhook.test",
    data: dict | None = None,
) -> list[str]:
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=workspace_id)
        deliveries = enqueue_event(
            db,
            workspace_id=workspace_id,
            event_type=event_type,
            data={"note": "ids only"} if data is None else data,
            endpoint_id=endpoint_id,
            now=now,
        )
        ids = [delivery.id for delivery in deliveries]
        db.commit()
    return ids


def _delivery(delivery_id: str) -> dict:
    with inspection_session_factory()() as db:
        row = db.get(WebhookDelivery, delivery_id)
        return {
            column: getattr(row, column)
            for column in (
                "workspace_id",
                "endpoint_id",
                "event_id",
                "event_type",
                "state",
                "attempt_count",
                "next_attempt_at",
                "last_status_code",
                "last_error",
                "payload",
                "payload_sha256",
                "last_attempt_at",
                "delivered_at",
            )
        }


def _attempts(delivery_id: str) -> list[dict]:
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(WebhookDeliveryAttempt)
            .where(WebhookDeliveryAttempt.delivery_id == delivery_id)
            .order_by(WebhookDeliveryAttempt.attempt_number)
        ).all()
        return [
            {
                column: getattr(row, column)
                for column in (
                    "workspace_id",
                    "attempt_number",
                    "request_timestamp",
                    "signing_key_ids",
                    "started_at",
                    "duration_ms",
                    "outcome",
                    "status_code",
                    "error_code",
                )
            }
            for row in rows
        ]


def _update(model, row_id: str, **values) -> None:
    with inspection_session_factory()() as db:
        row = db.get(model, row_id)
        for name, value in values.items():
            setattr(row, name, value)
        db.commit()


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _one_delivery(client, email_outbox, monkeypatch, now, url: str = HOOK):
    owner = _owner(client, email_outbox, monkeypatch)
    created = _created(owner, url)
    [delivery_id] = _enqueue(owner.workspace_id, now=now)
    return owner, created, delivery_id


def _entries(request: httpx.Request) -> list[str]:
    return request.headers["Homayar-Webhook-Signature"].split(",")


def _capture_publishes(monkeypatch, settings) -> list[str]:
    """Turn tasks_eager off and record each published delivery id (no broker)."""
    from backend.jobs import tasks

    published: list[str] = []
    monkeypatch.setattr(settings, "tasks_eager", False)
    monkeypatch.setattr(
        tasks.deliver_webhook_task, "apply_async", lambda *, args, **_kw: published.append(args[0])
    )
    return published


class TrickleServer:
    """A local HTTP receiver that answers one header byte every ``interval`` seconds.

    Each byte arrives well inside any per-read timeout, so only a total
    deadline ends the request. ``cut`` is set when the client hangs up.
    """

    def __init__(self, interval: float = 0.2) -> None:
        self._interval = interval
        self._stop = threading.Event()
        self.cut = threading.Event()
        self._server = socket.create_server(("127.0.0.1", 0))
        self._server.settimeout(0.2)
        self.url = f"http://127.0.0.1:{self._server.getsockname()[1]}/hook"
        self._threads = [threading.Thread(target=self._serve, daemon=True)]

    def __enter__(self) -> TrickleServer:
        self._threads[0].start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        for thread in list(self._threads):
            thread.join(timeout=5)
        self._server.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            handler = threading.Thread(target=self._trickle, args=(connection,), daemon=True)
            self._threads.append(handler)
            handler.start()

    def _trickle(self, connection: socket.socket) -> None:
        with connection:
            connection.settimeout(1)
            try:
                connection.recv(65536)
                connection.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                while not self._stop.wait(self._interval):
                    connection.sendall(b"a")
            except OSError:
                self.cut.set()


# --- enqueue ---------------------------------------------------------------------------


def test_the_catalogue_is_the_test_event_only():
    assert WEBHOOK_EVENT_TYPES == ("webhook.test",)


def test_enqueue_fans_out_to_active_subscribed_endpoints(client, email_outbox, monkeypatch, now):
    owner = _owner(client, email_outbox, monkeypatch)
    subscribed = _created(owner, "https://a.example.com/x")["endpoint"]["id"]
    _created(owner, "https://b.example.com/x", event_types=[])
    inactive = _created(owner, "https://c.example.com/x")["endpoint"]["id"]
    assert owner.patch(f"{ENDPOINTS}/{inactive}", json={"is_active": False}).status_code == 200

    [only] = _enqueue(owner.workspace_id, now=now)
    assert _delivery(only)["endpoint_id"] == subscribed

    second = _created(owner, "https://d.example.com/x")["endpoint"]["id"]
    ids = _enqueue(owner.workspace_id, now=now, data={"count": 2})
    rows = [_delivery(delivery_id) for delivery_id in ids]

    assert sorted(row["endpoint_id"] for row in rows) == sorted([subscribed, second])
    assert len({row["event_id"] for row in rows}) == 1
    assert rows[0]["payload"] == rows[1]["payload"]
    for row in rows:
        assert row["state"] == "pending"
        assert row["attempt_count"] == 0
        assert row["event_type"] == "webhook.test"
        assert _utc(row["next_attempt_at"]) == now
        assert row["payload_sha256"] == hashlib.sha256(row["payload"]).hexdigest()
    assert json.loads(rows[0]["payload"]) == {
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data": {"count": 2},
        "id": rows[0]["event_id"],
        "type": "webhook.test",
        "workspace_id": owner.workspace_id,
    }


def test_enqueue_refuses_an_unknown_event_type(client, email_outbox, monkeypatch, now):
    owner = _owner(client, email_outbox, monkeypatch)
    _created(owner)

    with pytest.raises(ValueError):
        _enqueue(owner.workspace_id, now=now, event_type="job.completed")

    with inspection_session_factory()() as db:
        assert db.scalars(select(WebhookDelivery)).all() == []


def test_enqueue_with_an_endpoint_id_targets_it_without_a_subscription(
    client, email_outbox, monkeypatch, now
):
    owner = _owner(client, email_outbox, monkeypatch)
    endpoint_id = _created(owner, event_types=[])["endpoint"]["id"]
    _created(owner, "https://other.example.com/x")

    [delivery_id] = _enqueue(owner.workspace_id, now=now, endpoint_id=endpoint_id)

    assert _delivery(delivery_id)["endpoint_id"] == endpoint_id


def test_enqueue_writes_in_the_callers_transaction(client, email_outbox, monkeypatch, now):
    owner = _owner(client, email_outbox, monkeypatch)
    _created(owner)

    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=owner.workspace_id)
        assert (
            len(
                enqueue_event(
                    db, workspace_id=owner.workspace_id, event_type="webhook.test", data={}, now=now
                )
            )
            == 1
        )
        db.rollback()

    with inspection_session_factory()() as db:
        assert db.scalars(select(WebhookDelivery)).all() == []


# --- one delivery, one request ---------------------------------------------------------


@pytest.mark.parametrize("status", [200, 204, 299])
def test_a_2xx_answer_delivers_and_the_request_verifies(
    client, email_outbox, monkeypatch, now, status
):
    owner, created, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    receiver = Receiver(httpx.Response(status))
    stored = _delivery(delivery_id)

    deliver_webhook(delivery_id, now=now, http_client=receiver.client)

    [request] = receiver.requests
    t = int(now.timestamp())
    expected_hex = hmac.new(
        created["signing_secret"].encode("utf-8"),
        f"{stored['event_id']}.{t}.".encode("ascii") + stored["payload"],
        hashlib.sha256,
    ).hexdigest()
    assert request.method == "POST"
    assert str(request.url) == HOOK
    assert request.content == stored["payload"]
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["User-Agent"] == "Homayar-Webhooks/1"
    assert request.headers["Homayar-Webhook-Id"] == stored["event_id"]
    assert request.headers["Homayar-Webhook-Timestamp"] == str(t)
    assert request.headers["Homayar-Webhook-Signature"] == f"v1={created['key_id']}:{expected_hex}"
    assert created["signing_secret"] not in str(request.headers)
    event = verify_webhook(
        headers=request.headers,
        body=request.content,
        secrets={created["key_id"]: created["signing_secret"]},
        now=now.timestamp(),
    )
    assert event == json.loads(stored["payload"])

    row = _delivery(delivery_id)
    assert row["state"] == "delivered"
    assert _utc(row["delivered_at"]) == now
    assert _utc(row["last_attempt_at"]) == now
    assert row["last_status_code"] == status
    assert row["last_error"] is None
    assert row["next_attempt_at"] is None
    assert row["attempt_count"] == 1
    [attempt] = _attempts(delivery_id)
    assert attempt["attempt_number"] == 1
    assert attempt["outcome"] == "delivered"
    assert attempt["status_code"] == status
    assert attempt["error_code"] is None
    assert attempt["request_timestamp"] == t
    assert attempt["signing_key_ids"] == [created["key_id"]]
    assert _utc(attempt["started_at"]) == now
    assert attempt["duration_ms"] >= 0
    assert attempt["workspace_id"] == owner.workspace_id


def test_a_queued_test_event_round_trips_through_the_sdk(client, email_outbox, monkeypatch, now):
    owner = _owner(client, email_outbox, monkeypatch)
    created = _created(owner, event_types=[])
    queued = owner.post(f"{ENDPOINTS}/{created['endpoint']['id']}/test").json()
    receiver = Receiver()

    deliver_webhook(queued["delivery_id"], now=datetime.now(UTC), http_client=receiver.client)

    [request] = receiver.requests
    event = verify_webhook(
        headers=request.headers,
        body=request.content,
        secrets={created["key_id"]: created["signing_secret"]},
    )
    assert event["id"] == queued["event_id"]
    assert event["type"] == "webhook.test"
    assert event["data"] == {"endpoint_id": created["endpoint"]["id"]}
    assert _delivery(queued["delivery_id"])["state"] == "delivered"


@pytest.mark.parametrize("status", [500, 404, 429])
def test_a_non_2xx_answer_fails_with_http_status(client, email_outbox, monkeypatch, now, status):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)

    deliver_webhook(delivery_id, now=now, http_client=Receiver(httpx.Response(status)).client)

    # HOM-90: a failed attempt is retried after the first delay.
    row = _delivery(delivery_id)
    assert (row["state"], row["last_status_code"], row["last_error"]) == (
        "pending",
        status,
        "http_status",
    )
    assert _utc(row["next_attempt_at"]) == now + timedelta(seconds=60)
    assert row["delivered_at"] is None
    [attempt] = _attempts(delivery_id)
    assert (attempt["outcome"], attempt["status_code"], attempt["error_code"]) == (
        "failed",
        status,
        "http_status",
    )


def test_a_redirect_is_a_failure_and_is_never_followed(client, email_outbox, monkeypatch, now):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    receiver = Receiver(
        httpx.Response(301, headers={"Location": "https://elsewhere.example.com/"}),
        httpx.Response(204),
        follow_redirects=True,
    )

    deliver_webhook(delivery_id, now=now, http_client=receiver.client)

    assert [str(request.url) for request in receiver.requests] == [HOOK]
    row = _delivery(delivery_id)
    assert (row["state"], row["last_status_code"], row["last_error"]) == (
        "pending",
        301,
        "http_status",
    )


@pytest.mark.parametrize(
    ("answer", "code"), [(_timeout, "timeout"), (_refused, "connection_error")]
)
def test_no_answer_fails_without_a_status(client, email_outbox, monkeypatch, now, answer, code):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)

    deliver_webhook(delivery_id, now=now, http_client=Receiver(answer).client)

    row = _delivery(delivery_id)
    assert (row["state"], row["last_status_code"], row["last_error"]) == ("pending", None, code)
    [attempt] = _attempts(delivery_id)
    assert (attempt["outcome"], attempt["status_code"], attempt["error_code"]) == (
        "failed",
        None,
        code,
    )


@pytest.mark.parametrize("state", ["delivered", "failed", "dead_letter"])
def test_a_finished_delivery_is_not_sent(client, email_outbox, monkeypatch, now, state):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _update(WebhookDelivery, delivery_id, state=state, delivered_at=now, next_attempt_at=now)
    receiver = Receiver()

    deliver_webhook(delivery_id, now=now + timedelta(hours=1), http_client=receiver.client)

    assert receiver.requests == []
    assert _delivery(delivery_id)["state"] == state


def test_a_pending_delivery_waits_for_its_time(client, email_outbox, monkeypatch, now):
    owner = _owner(client, email_outbox, monkeypatch)
    _created(owner)
    [delivery_id] = _enqueue(owner.workspace_id, now=now + timedelta(minutes=5))
    receiver = Receiver()

    deliver_webhook(delivery_id, now=now, http_client=receiver.client)
    assert receiver.requests == [] and _delivery(delivery_id)["state"] == "pending"

    deliver_webhook(delivery_id, now=now + timedelta(minutes=5), http_client=receiver.client)
    assert len(receiver.requests) == 1


def test_a_pending_delivery_without_a_time_is_due(client, email_outbox, monkeypatch, now):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _update(WebhookDelivery, delivery_id, next_attempt_at=None)
    receiver = Receiver()

    deliver_webhook(delivery_id, now=now, http_client=receiver.client)

    assert len(receiver.requests) == 1


def test_a_lease_blocks_until_it_expires_then_the_same_bytes_go_again(
    client, email_outbox, monkeypatch, now, settings
):
    _, created, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)

    def stop_after_sending(request: httpx.Request) -> httpx.Response:
        raise WorkerStopped

    first = Receiver(stop_after_sending)
    with pytest.raises(WorkerStopped):
        deliver_webhook(delivery_id, now=now, http_client=first.client)

    lease = now + timedelta(seconds=settings.webhook_delivery_timeout_seconds + 30)
    row = _delivery(delivery_id)
    assert (row["state"], row["attempt_count"], _utc(row["next_attempt_at"])) == (
        "sending",
        1,
        lease,
    )
    assert _attempts(delivery_id) == []

    second = Receiver()
    deliver_webhook(delivery_id, now=lease - timedelta(seconds=1), http_client=second.client)
    assert second.requests == []

    deliver_webhook(delivery_id, now=lease, http_client=second.client)

    [lost], [resent] = first.requests, second.requests
    assert resent.content == lost.content == row["payload"]
    assert resent.headers["Homayar-Webhook-Id"] == lost.headers["Homayar-Webhook-Id"]
    assert resent.headers["Homayar-Webhook-Timestamp"] == str(int(lease.timestamp()))
    assert verify_webhook(
        headers=resent.headers,
        body=resent.content,
        secrets={created["key_id"]: created["signing_secret"]},
        now=lease.timestamp(),
    )
    [attempt] = _attempts(delivery_id)
    assert attempt["attempt_number"] == 2
    row = _delivery(delivery_id)
    assert (row["state"], row["attempt_count"]) == ("delivered", 2)


# --- refusals before sending -----------------------------------------------------------


def _assert_refused(delivery_id: str, receiver: Receiver, code: str) -> None:
    assert receiver.requests == []
    row = _delivery(delivery_id)
    assert (row["state"], row["last_error"], row["next_attempt_at"]) == ("dead_letter", code, None)
    assert row["attempt_count"] == 0
    assert _attempts(delivery_id) == []


def test_an_inactive_endpoint_is_refused_before_sending(client, email_outbox, monkeypatch, now):
    owner, created, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    patched = owner.patch(f"{ENDPOINTS}/{created['endpoint']['id']}", json={"is_active": False})
    assert patched.status_code == 200
    receiver = Receiver()

    deliver_webhook(delivery_id, now=now, http_client=receiver.client)

    _assert_refused(delivery_id, receiver, "endpoint_inactive")


@pytest.mark.parametrize(
    "url",
    ["http://169.254.169.254/latest", "ftp://hooks.example.com/x", "https://u:p@h.example.com/"],
)
def test_a_url_made_unsafe_after_enqueue_is_refused(client, email_outbox, monkeypatch, now, url):
    _, created, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _update(WebhookEndpoint, created["endpoint"]["id"], url=url)
    receiver = Receiver()

    deliver_webhook(delivery_id, now=now, http_client=receiver.client)

    _assert_refused(delivery_id, receiver, "url_refused")


def test_production_refuses_a_plain_http_url_at_send(
    client, email_outbox, monkeypatch, now, settings
):
    _, _, delivery_id = _one_delivery(
        client, email_outbox, monkeypatch, now, url="http://hooks.example.com/x"
    )
    monkeypatch.setattr(settings, "environment", "production")
    receiver = Receiver()

    deliver_webhook(delivery_id, now=now, http_client=receiver.client)

    _assert_refused(delivery_id, receiver, "url_refused")


@pytest.mark.parametrize(
    "url",
    ["https://minio:9000/x", "https://crm.internal/x", "https://api.homayar-cloud_private:8000/x"],
)
@pytest.mark.parametrize("mode", ["cloud", "onprem"])
def test_a_production_cloud_refuses_an_internal_name_at_send(
    client, email_outbox, monkeypatch, now, settings, url, mode
):
    """SPEC A5.4: the URL was changed in the database after it was checked."""
    _, created, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _update(WebhookEndpoint, created["endpoint"]["id"], url=url)
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "deployment_mode", mode)
    receiver = Receiver()

    deliver_webhook(delivery_id, now=now, http_client=receiver.client)

    if mode == "cloud":
        _assert_refused(delivery_id, receiver, "url_refused")
    else:
        assert [str(request.url) for request in receiver.requests] == [url]
        assert _delivery(delivery_id)["state"] == "delivered"


@pytest.mark.parametrize(
    "keyring",
    [("", ""), (f"wk2:{'A' * 43}=", "wk2")],
    ids=["no keyring", "the key that encrypted the secret was removed"],
)
def test_a_missing_keyring_key_is_refused_before_sending(
    client, email_outbox, monkeypatch, now, settings, keyring
):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    monkeypatch.setattr(settings, "webhook_secret_keys", keyring[0])
    monkeypatch.setattr(settings, "webhook_secret_active_key_id", keyring[1])
    receiver = Receiver()

    deliver_webhook(delivery_id, now=now, http_client=receiver.client)

    _assert_refused(delivery_id, receiver, "signing_unavailable")


# --- rotation ---------------------------------------------------------------------------


def test_during_the_overlap_both_keys_sign_and_after_it_only_the_new_one(
    client, email_outbox, monkeypatch
):
    owner = _owner(client, email_outbox, monkeypatch)
    old = _created(owner)
    rotate = f"{ENDPOINTS}/{old['endpoint']['id']}/signing-keys"
    new = owner.post(rotate, json={"overlap_seconds": 3600}).json()
    expiry = datetime.fromisoformat(new["previous_key_expires_at"])
    expiry = _utc(expiry)
    during = datetime.now(UTC).replace(microsecond=0)
    after = expiry + timedelta(seconds=1)
    [first] = _enqueue(owner.workspace_id, now=during)
    [second] = _enqueue(owner.workspace_id, now=during)
    receiver = Receiver()

    deliver_webhook(first, now=during, http_client=receiver.client)
    deliver_webhook(second, now=after, http_client=receiver.client)

    overlap, later = receiver.requests
    assert [entry.split(":")[0] for entry in _entries(overlap)] == [
        f"v1={new['key_id']}",
        f"v1={old['key_id']}",
    ]
    for key_id, secret in (
        (new["key_id"], new["signing_secret"]),
        (old["key_id"], old["signing_secret"]),
    ):
        assert verify_webhook(
            headers=overlap.headers,
            body=overlap.content,
            secrets={key_id: secret},
            now=during.timestamp(),
        )
    assert [entry.split(":")[0] for entry in _entries(later)] == [f"v1={new['key_id']}"]
    with pytest.raises(WebhookVerificationError) as caught:
        verify_webhook(
            headers=later.headers,
            body=later.content,
            secrets={old["key_id"]: old["signing_secret"]},
            now=after.timestamp(),
        )
    assert caught.value.code == "unknown_key"
    assert _attempts(first)[0]["signing_key_ids"] == [new["key_id"], old["key_id"]]


def test_rotation_with_no_overlap_signs_with_the_new_key_only(client, email_outbox, monkeypatch):
    owner = _owner(client, email_outbox, monkeypatch)
    old = _created(owner)
    rotate = f"{ENDPOINTS}/{old['endpoint']['id']}/signing-keys"
    new = owner.post(rotate, json={"overlap_seconds": 0}).json()
    at = datetime.now(UTC) + timedelta(seconds=1)
    [delivery_id] = _enqueue(owner.workspace_id, now=at)
    receiver = Receiver()

    deliver_webhook(delivery_id, now=at, http_client=receiver.client)

    [request] = receiver.requests
    assert [entry.split(":")[0] for entry in _entries(request)] == [f"v1={new['key_id']}"]


# --- logging ---------------------------------------------------------------------------


def test_log_lines_carry_no_secret_signature_query_or_payload(
    client, email_outbox, monkeypatch, now, caplog
):
    caplog.set_level(logging.DEBUG)
    owner = _owner(client, email_outbox, monkeypatch)
    created = _created(owner, "https://hooks.example.com/x?token=query-secret-value")
    [delivered, failed] = [
        _enqueue(owner.workspace_id, now=now, data={"marker": "payload-marker-value"})[0]
        for _ in range(2)
    ]
    receiver = Receiver(httpx.Response(204), httpx.Response(500))

    deliver_webhook(delivered, now=now, http_client=receiver.client)
    deliver_webhook(failed, now=now, http_client=receiver.client)

    text_ = "\n".join(record.getMessage() for record in caplog.records)
    assert delivered in text_ and failed in text_
    signatures = [request.headers["Homayar-Webhook-Signature"] for request in receiver.requests]
    for forbidden in (
        created["signing_secret"],
        "query-secret-value",
        "payload-marker-value",
        *[signature.split(":")[1] for signature in signatures],
    ):
        assert forbidden not in text_


# --- the total deadline (review F1) ----------------------------------------------------


def _assert_timed_out(delivery_id: str, elapsed: float, deadline: float) -> None:
    assert elapsed < deadline + 2.5, elapsed
    row = _delivery(delivery_id)
    assert (row["state"], row["last_status_code"], row["last_error"]) == (
        "pending",
        None,
        "timeout",
    )
    [attempt] = _attempts(delivery_id)
    assert (attempt["outcome"], attempt["status_code"], attempt["error_code"]) == (
        "failed",
        None,
        "timeout",
    )
    assert deadline * 1000 * 0.9 <= attempt["duration_ms"] < (deadline + 2.5) * 1000


@pytest.mark.parametrize("which", ["worker client", "caller client"])
def test_a_trickling_receiver_is_cut_at_the_deadline(
    client, email_outbox, monkeypatch, now, settings, which
):
    monkeypatch.setattr(settings, "webhook_delivery_timeout_seconds", 1)
    # A caller's client with a long per-read timeout: only the total deadline can end it.
    given = httpx.Client(timeout=30) if which == "caller client" else None
    try:
        with TrickleServer() as server:
            _, _, delivery_id = _one_delivery(
                client, email_outbox, monkeypatch, now, url=server.url
            )
            started = time.monotonic()
            deliver_webhook(delivery_id, now=now, http_client=given)
            elapsed = time.monotonic() - started
            # The request was cut (socket shut down), not only abandoned.
            assert server.cut.wait(timeout=3)
    finally:
        if given is not None:
            given.close()

    _assert_timed_out(delivery_id, elapsed, 1.0)


def test_a_transport_that_never_answers_is_cut_at_the_deadline(
    client, email_outbox, monkeypatch, now, settings
):
    monkeypatch.setattr(settings, "webhook_delivery_timeout_seconds", 1)
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    release = threading.Event()

    def slow(request: httpx.Request) -> httpx.Response:
        release.wait(timeout=10)
        return httpx.Response(200)

    try:
        started = time.monotonic()
        deliver_webhook(delivery_id, now=now, http_client=Receiver(slow).client)
        elapsed = time.monotonic() - started
    finally:
        release.set()

    _assert_timed_out(delivery_id, elapsed, 1.0)


def test_celerys_soft_time_limit_during_the_send_is_a_timeout(
    client, email_outbox, monkeypatch, now
):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)

    def soft_limit(request: httpx.Request) -> httpx.Response:
        raise SoftTimeLimitExceeded()

    deliver_webhook(delivery_id, now=now, http_client=Receiver(soft_limit).client)

    row = _delivery(delivery_id)
    assert (row["state"], row["last_error"]) == ("pending", "timeout")
    assert _attempts(delivery_id)[0]["error_code"] == "timeout"


def test_the_delivery_task_limits_end_before_the_lease():
    from backend.config import get_settings
    from backend.jobs import tasks

    timeout = get_settings().webhook_delivery_timeout_seconds
    lease = timeout + webhook_delivery.LEASE_GRACE.total_seconds()
    task = tasks.deliver_webhook_task

    assert task.soft_time_limit == timeout + 10
    assert task.time_limit == timeout + 20
    assert timeout < task.soft_time_limit < task.time_limit < lease


# --- the sweeper -----------------------------------------------------------------------


def test_the_sweeper_returns_due_ids_oldest_first(client, email_outbox, monkeypatch, now, settings):
    owner = _owner(client, email_outbox, monkeypatch)
    _created(owner)
    [newer] = _enqueue(owner.workspace_id, now=now - timedelta(seconds=30))
    [older] = _enqueue(owner.workspace_id, now=now - timedelta(seconds=60))
    [future] = _enqueue(owner.workspace_id, now=now + timedelta(seconds=60))
    [done] = _enqueue(owner.workspace_id, now=now - timedelta(seconds=90))
    _update(WebhookDelivery, done, state="delivered", delivered_at=now, next_attempt_at=None)
    [leased] = _enqueue(owner.workspace_id, now=now)
    _update(WebhookDelivery, leased, state="sending", next_attempt_at=now - timedelta(seconds=45))
    [held] = _enqueue(owner.workspace_id, now=now)
    _update(WebhookDelivery, held, state="sending", next_attempt_at=now + timedelta(seconds=5))

    assert webhook_delivery.due_delivery_ids(now=now, limit=100) == [older, leased, newer]
    assert webhook_delivery.due_delivery_ids(now=now, limit=1) == [older]
    # With tasks_eager nothing is published: the result is empty and the rows stay due.
    assert sweep_webhook_deliveries(now=now) == []
    assert _delivery(older)["state"] == "pending"

    published = _capture_publishes(monkeypatch, settings)
    assert sweep_webhook_deliveries(now=now) == [older, leased, newer] == published
    assert sweep_webhook_deliveries(now=now, limit=1) == [older]
    assert future not in published


def test_the_sweeper_publishes_one_task_per_due_delivery(
    client, email_outbox, monkeypatch, now, settings
):
    from backend.celery_app import PROCESSING_QUEUE
    from backend.jobs import tasks

    owner = _owner(client, email_outbox, monkeypatch)
    _created(owner)
    first = _enqueue(owner.workspace_id, now=now - timedelta(seconds=2))[0]
    second = _enqueue(owner.workspace_id, now=now - timedelta(seconds=1))[0]
    monkeypatch.setattr(settings, "tasks_eager", False)
    published: list[tuple] = []

    def apply_async(*, args, queue, **_kwargs):
        if args == [first]:
            raise ConnectionError("broker down")
        published.append((args, queue))

    monkeypatch.setattr(tasks.deliver_webhook_task, "apply_async", apply_async)

    assert sweep_webhook_deliveries(now=now) == [second]
    assert published == [([second], PROCESSING_QUEUE)]


def test_the_test_route_publishes_after_commit_and_survives_a_broker_failure(
    client, email_outbox, monkeypatch, settings, now
):
    """SPEC A4.2: without tasks_eager, one deliver_webhook task per queued delivery."""
    from backend.celery_app import PROCESSING_QUEUE, celery_app
    from backend.jobs import tasks

    owner = _owner(client, email_outbox, monkeypatch)
    one = f"{ENDPOINTS}/{_created(owner)['endpoint']['id']}"
    monkeypatch.setattr(settings, "tasks_eager", False)
    published: list[tuple[list[str], str]] = []

    def apply_async(*, args, queue, **kwargs):
        assert kwargs == {}
        with inspection_session_factory()() as db:  # visible: the route committed first
            assert db.get(WebhookDelivery, args[0]) is not None
        published.append((args, queue))

    def no_other_publish(*_args, **_kwargs):
        raise AssertionError("only deliver_webhook_task.apply_async may publish")

    monkeypatch.setattr(tasks.deliver_webhook_task, "apply_async", apply_async)
    monkeypatch.setattr(celery_app, "send_task", no_other_publish)
    first = owner.post(f"{one}/test")
    second = owner.post(f"{one}/test")

    assert first.status_code == 202 and second.status_code == 202
    assert published == [
        ([first.json()["delivery_id"]], PROCESSING_QUEUE),
        ([second.json()["delivery_id"]], PROCESSING_QUEUE),
    ]

    def broker_down(**_kwargs):
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.deliver_webhook_task, "apply_async", broker_down)
    lost = owner.post(f"{one}/test")

    assert lost.status_code == 202, lost.text
    lost_id = lost.json()["delivery_id"]
    assert _delivery(lost_id)["state"] == "pending"
    # The lost publish is not lost work: the row stays due for the sweeper.
    assert lost_id in webhook_delivery.due_delivery_ids(now=now + timedelta(minutes=1), limit=100)


def test_with_tasks_eager_the_test_route_publishes_nothing(client, email_outbox, monkeypatch):
    """SPEC A4.2: an eager publish would run the delivery inside the request."""
    from backend.jobs import tasks

    owner = _owner(client, email_outbox, monkeypatch)
    one = f"{ENDPOINTS}/{_created(owner)['endpoint']['id']}"
    published: list = []
    monkeypatch.setattr(
        tasks.deliver_webhook_task, "apply_async", lambda **kw: published.append(kw)
    )

    queued = owner.post(f"{one}/test")

    assert queued.status_code == 202
    assert published == []
    assert _delivery(queued.json()["delivery_id"])["state"] == "pending"


def test_after_a_sweep_the_claim_and_record_bind_only_the_workspace(
    client, email_outbox, monkeypatch, now, settings
):
    """The read-only scope of the sweep never leaks into a write (SPEC Addendum 3)."""
    owner, created, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _capture_publishes(monkeypatch, settings)
    writes: list[tuple[TenantContext, set[str]]] = []

    def watch(session, _flush_context, _instances) -> None:
        written = {type(row).__name__ for row in (*session.new, *session.dirty)}
        if written & {"WebhookDelivery", "WebhookDeliveryAttempt"}:
            writes.append((current_context(session), written))

    event.listen(Session, "before_flush", watch)
    try:
        assert sweep_webhook_deliveries(now=now) == [delivery_id]
        receiver = Receiver()
        deliver_webhook(delivery_id, now=now, http_client=receiver.client)
    finally:
        event.remove(Session, "before_flush", watch)

    assert len(receiver.requests) == 1
    assert _delivery(delivery_id)["state"] == "delivered"
    assert len(_attempts(delivery_id)) == 1
    # One flush for the claim, one for the record; both tenant-bound, no scope.
    assert len(writes) == 2
    for context, _written in writes:
        assert context.service_scope is None
        assert context.workspace_id == owner.workspace_id
    assert "WebhookDeliveryAttempt" in writes[1][1]


# --- Celery wiring ---------------------------------------------------------------------


def test_celery_registers_the_tasks_and_the_beat_entry():
    from backend.celery_app import PROCESSING_QUEUE, celery_app
    from backend.config import get_settings
    from backend.jobs import tasks

    assert tasks.deliver_webhook_task.name == "backend.jobs.deliver_webhook"
    assert tasks.sweep_webhook_deliveries_task.name == "backend.jobs.sweep_webhook_deliveries"
    assert {"backend.jobs.deliver_webhook", "backend.jobs.sweep_webhook_deliveries"} <= set(
        celery_app.tasks
    )
    entries = [
        entry
        for entry in celery_app.conf.beat_schedule.values()
        if entry["task"] == "backend.jobs.sweep_webhook_deliveries"
    ]
    assert len(entries) == 1
    assert entries[0]["schedule"] == float(get_settings().webhook_sweep_interval_seconds)
    assert entries[0]["options"]["queue"] == PROCESSING_QUEUE
    for name in ("backend.jobs.deliver_webhook", "backend.jobs.sweep_webhook_deliveries"):
        assert celery_app.conf.task_routes[name] == {"queue": PROCESSING_QUEUE}


def test_the_celery_tasks_call_the_worker_functions(monkeypatch):
    from backend.jobs import tasks

    calls: list = []
    monkeypatch.setattr(webhook_delivery, "deliver_webhook", calls.append)
    monkeypatch.setattr(webhook_delivery, "sweep_webhook_deliveries", lambda: ["a", "b"])
    store = heartbeats.MemoryHeartbeatStore()
    heartbeats.use_heartbeat_store(store)
    try:
        tasks.deliver_webhook_task.run("delivery-1")
        result = tasks.sweep_webhook_deliveries_task.run()
    finally:
        heartbeats.use_heartbeat_store(None)

    assert calls == ["delivery-1"]
    assert result == {"published": 2}
    # The sweep reports a heartbeat like every other periodic task (SPEC A4.8).
    record = store.read_all()["webhook_delivery"]
    assert json.loads(record["result"]) == {"published": 2}
    assert "last_success" in record and "last_failure" not in record


def test_the_webhook_sweep_is_a_periodic_task_with_its_beat_interval(settings):
    assert heartbeats.PERIODIC_TASKS["webhook_delivery"] == "webhook_sweep_interval_seconds"
    assert "webhook_delivery" in heartbeats.KNOWN_TASKS
    assert heartbeats.expected_interval_seconds("webhook_delivery", settings) == float(
        settings.webhook_sweep_interval_seconds
    )


# --- PostgreSQL: concurrency and the service scope (AC7) -------------------------------


@pytest.mark.postgres
def test_two_concurrent_workers_send_one_request(client, email_outbox, monkeypatch, now):
    """The claim uses SKIP LOCKED: a second worker never waits for a held row."""
    if not is_postgres_url(configured_database_url()):
        pytest.skip("row locks are PostgreSQL evidence")
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    receiver = Receiver()
    original = webhook_delivery._lock_due
    first_locked = threading.Event()
    second_done = threading.Event()
    errors: list[BaseException] = []

    def first_holds_its_lock(*args, **kwargs):
        row = original(*args, **kwargs)
        if threading.current_thread().name == "first":
            first_locked.set()
            second_done.wait(timeout=20)
        return row

    monkeypatch.setattr(webhook_delivery, "_lock_due", first_holds_its_lock)

    def run() -> None:
        try:
            deliver_webhook(delivery_id, now=now, http_client=receiver.client)
        except BaseException as exc:  # reported below, not swallowed
            errors.append(exc)

    first = threading.Thread(target=run, name="first")
    second = threading.Thread(target=run, name="second")
    first.start()
    assert first_locked.wait(timeout=20)
    try:
        started = time.monotonic()
        second.start()
        second.join(timeout=2)
        # SKIP LOCKED: while the first worker holds the row lock the second one
        # returns at once and sends nothing. A plain FOR UPDATE would wait here.
        assert not second.is_alive(), "the second worker waited for the row lock"
        assert time.monotonic() - started < 2
        assert receiver.requests == []
    finally:
        second_done.set()
        first.join(timeout=20)
        second.join(timeout=20)

    assert errors == []
    assert len(receiver.requests) == 1
    assert _delivery(delivery_id)["attempt_count"] == 1
    assert len(_attempts(delivery_id)) == 1


@pytest.mark.postgres
def test_the_sweeper_sees_two_workspaces_and_each_claim_binds_its_own(
    client, email_outbox, monkeypatch, now, settings, probe_role
):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("row-level security is PostgreSQL evidence")
    one = _owner(client, email_outbox, monkeypatch)
    two = _persona(client, email_outbox, "webhook-two@example.com")
    _created(one)
    _created(two)
    [first] = _enqueue(one.workspace_id, now=now - timedelta(seconds=2))
    [second] = _enqueue(two.workspace_id, now=now - timedelta(seconds=1))
    _capture_publishes(monkeypatch, settings)

    assert sweep_webhook_deliveries(now=now) == [first, second]

    engine = create_engine(probe_role, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            _rls._bind(connection, service_scope="webhook_delivery")
            assert _rls._count(connection, "webhook_deliveries") == 2
            for table in ("webhook_endpoints", "webhook_signing_keys", "webhook_delivery_attempts"):
                assert _rls._count(connection, table) == 0, table
            with pytest.raises(DBAPIError, match="row-level security"):
                with connection.begin_nested():
                    connection.execute(
                        text("UPDATE webhook_deliveries SET state = 'failed' WHERE id = :id"),
                        {"id": first},
                    )
            with pytest.raises(DBAPIError, match="permission denied"):
                with connection.begin_nested():
                    connection.execute(text("DELETE FROM webhook_deliveries"))
            connection.rollback()
    finally:
        engine.dispose()

    receiver = Receiver()
    deliver_webhook(first, now=now, http_client=receiver.client)
    deliver_webhook(second, now=now, http_client=receiver.client)

    assert len(receiver.requests) == 2
    for delivery_id, workspace_id in ((first, one.workspace_id), (second, two.workspace_id)):
        row = _delivery(delivery_id)
        assert row["state"] == "delivered"
        assert _attempts(delivery_id)[0]["workspace_id"] == workspace_id
