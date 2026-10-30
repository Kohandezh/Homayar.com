"""Signed webhook delivery: claim, send, record, and the sweeper (HOM-89, ADR 0050).

Transaction boundaries
----------------------
* *Claim*, one transaction bound to the delivery's own workspace: lock the row
  ``FOR UPDATE SKIP LOCKED`` if it is due, refuse what must not be sent
  (inactive endpoint, refused URL, no usable key), else mark it ``sending``
  with a lease and commit.
* *Send* with no transaction open. Redirects are never followed; the response
  body is never read, stored or logged.
* *Record*, one transaction: append the attempt row, then finish the delivery
  only if it still holds this claim (``sending`` with this attempt count).
* Delivery is **at-least-once**. A worker that stops after sending and before
  recording leaves the lease to expire; the next worker sends again with the
  same event id and the same bytes, so receivers drop duplicates by the id.
* The sweeper discovers due rows of every tenant under the read-only
  ``webhook_delivery`` service scope; the claim binds the workspace read from
  the persisted row, never from the message.
* One attempt per delivery here: a failure stays ``failed``. HOM-90 adds the
  retry schedule and the dead letter on the same rows.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    WebhookAttemptOutcome,
    WebhookDelivery,
    WebhookDeliveryAttempt,
    WebhookDeliveryState,
    WebhookEndpoint,
    WebhookSigningKey,
)
from backend.database.tenant_context import (
    SERVICE_SCOPE_WEBHOOK_DELIVERY,
    bind_service_scope,
    bind_tenant_context,
)
from backend.services.logging_service import logger
from backend.services.webhook_service import url_refusal_for
from backend.services.webhook_signing import (
    WebhookSecretCipher,
    WebhookSecretIntegrityError,
    WebhookSigningUnavailable,
    request_headers,
)

# The lease is the request timeout plus this grace: a worker that stops after
# its claim is replaced only after its request could no longer be running.
LEASE_GRACE = timedelta(seconds=30)
# How long the worker waits for a cut request to end before it closes its client.
_CUT_GRACE_SECONDS = 2.0

HTTP_STATUS = "http_status"
TIMEOUT = "timeout"
CONNECTION_ERROR = "connection_error"
ENDPOINT_INACTIVE = "endpoint_inactive"
URL_REFUSED = "url_refused"
SIGNING_UNAVAILABLE = "signing_unavailable"
# A row written before HOM-89 has no frozen payload; it can never be sent.
PAYLOAD_MISSING = "payload_missing"


@dataclass(frozen=True)
class _Claim:
    delivery_id: str
    workspace_id: str
    endpoint_id: str
    event_id: str
    attempt_number: int
    url: str
    payload: bytes
    keys: tuple[tuple[str, str], ...]  # (key_id, secret), active key first


@dataclass(frozen=True)
class _Result:
    outcome: str
    status_code: int | None
    error_code: str | None
    duration_ms: int


def _as_utc(value: datetime) -> datetime:
    value = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _due(now: datetime):
    pending = WebhookDeliveryState.PENDING.value
    sending = WebhookDeliveryState.SENDING.value
    return or_(
        and_(
            WebhookDelivery.state == pending,
            or_(WebhookDelivery.next_attempt_at.is_(None), WebhookDelivery.next_attempt_at <= now),
        ),
        # An expired lease: the worker that claimed the row stopped.
        and_(WebhookDelivery.state == sending, WebhookDelivery.next_attempt_at <= now),
    )


def _workspace_of(delivery_id: str) -> str | None:
    """Read the delivery's workspace under the read-only discovery scope."""
    with get_session_factory()() as db:
        bind_service_scope(db, SERVICE_SCOPE_WEBHOOK_DELIVERY)
        workspace_id = db.scalar(
            select(WebhookDelivery.workspace_id).where(WebhookDelivery.id == delivery_id)
        )
        db.rollback()
    return workspace_id


def _lock_due(
    db: Session, delivery_id: str, workspace_id: str, now: datetime
) -> WebhookDelivery | None:
    return db.execute(
        select(WebhookDelivery)
        .where(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.workspace_id == workspace_id,
            _due(now),
        )
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()


def _usable_keys(
    db: Session, endpoint: WebhookEndpoint, *, now: datetime, settings: Settings
) -> list[tuple[str, str]]:
    """Unexpired keys whose keyring key is still configured: active first, then newest."""
    cipher = WebhookSecretCipher.from_settings(settings)
    if cipher is None:
        return []
    keys = db.scalars(
        select(WebhookSigningKey).where(
            WebhookSigningKey.endpoint_id == endpoint.id,
            or_(WebhookSigningKey.expires_at.is_(None), WebhookSigningKey.expires_at > now),
        )
    ).all()
    keys = sorted(
        keys,
        key=lambda key: (key.expires_at is not None, -_as_utc(key.created_at).timestamp()),
    )
    usable: list[tuple[str, str]] = []
    for key in keys:
        if not cipher.has_key(key.encryption_key_id):
            continue
        try:
            secret = cipher.decrypt(
                encryption_key_id=key.encryption_key_id,
                nonce=key.nonce,
                ciphertext=key.secret_ciphertext,
                workspace_id=key.workspace_id,
                endpoint_id=key.endpoint_id,
                key_id=key.key_id,
            )
        except (WebhookSigningUnavailable, WebhookSecretIntegrityError):
            logger.error(
                "WEBHOOK_SIGNING_KEY_UNREADABLE endpoint_id=%s error_code=%s",
                endpoint.id,
                SIGNING_UNAVAILABLE,
            )
            continue
        usable.append((key.key_id, secret))
    return usable


def _refuse(db: Session, delivery: WebhookDelivery, code: str) -> None:
    """Fail before sending: no request, no attempt row, no attempt counted."""
    delivery_id, endpoint_id = delivery.id, delivery.endpoint_id
    delivery.state = WebhookDeliveryState.FAILED.value
    delivery.last_error = code
    delivery.next_attempt_at = None
    db.commit()
    logger.warning(
        "WEBHOOK_DELIVERY_REFUSED delivery_id=%s endpoint_id=%s error_code=%s",
        delivery_id,
        endpoint_id,
        code,
    )


def _claim(
    delivery_id: str, workspace_id: str, *, now: datetime, settings: Settings
) -> _Claim | None:
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=workspace_id)
        delivery = _lock_due(db, delivery_id, workspace_id, now)
        if delivery is None:  # not due, finished, or another worker holds it
            db.rollback()
            return None
        endpoint = db.get(WebhookEndpoint, delivery.endpoint_id)
        if endpoint is None or not endpoint.is_active:
            _refuse(db, delivery, ENDPOINT_INACTIVE)
            return None
        if url_refusal_for(endpoint.url, settings) is not None:
            _refuse(db, delivery, URL_REFUSED)
            return None
        if delivery.payload is None:
            _refuse(db, delivery, PAYLOAD_MISSING)
            return None
        keys = _usable_keys(db, endpoint, now=now, settings=settings)
        if not keys:
            _refuse(db, delivery, SIGNING_UNAVAILABLE)
            return None
        delivery.state = WebhookDeliveryState.SENDING.value
        delivery.attempt_count += 1
        delivery.last_attempt_at = now
        delivery.next_attempt_at = (
            now + timedelta(seconds=settings.webhook_delivery_timeout_seconds) + LEASE_GRACE
        )
        claim = _Claim(
            delivery_id=delivery.id,
            workspace_id=delivery.workspace_id,
            endpoint_id=delivery.endpoint_id,
            event_id=delivery.event_id,
            attempt_number=delivery.attempt_count,
            url=endpoint.url,
            payload=bytes(delivery.payload),
            keys=tuple(keys),
        )
        db.commit()
    return claim


class _DeadlinePassed(Exception):
    """The request did not end within the delivery timeout."""


class _Exchange:
    """One POST on a helper thread, so the worker stops waiting at a total deadline.

    An httpx timeout limits each connect, read and write on its own, not the
    whole request: a receiver that sends one header byte every few seconds
    could keep a request (and a queue worker) for minutes. Here the worker
    waits at most the deadline. Then it shuts down the request's socket, which
    the trace hook captured, so the request ends at once; with a client that
    has no socket (a test transport) the helper thread is left to finish on
    its own. The response body is never read.
    """

    def __init__(self, client: httpx.Client, url: str, content: bytes, headers: dict) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._sockets: list[Any] = []
        self._cut = False
        self._status: int | None = None
        self._error: BaseException | None = None
        self._request = client.build_request(
            "POST", url, content=content, headers=headers, extensions={"trace": self._trace}
        )
        self._thread = threading.Thread(target=self._run, name="webhook-send", daemon=True)

    def _trace(self, event: str, info: dict) -> None:
        if event not in ("connection.connect_tcp.complete", "connection.start_tls.complete"):
            return
        stream = info.get("return_value")
        sock = stream.get_extra_info("socket") if stream is not None else None
        if sock is None:
            return
        with self._lock:
            self._sockets.append(sock)
            cut = self._cut
        if cut:  # connected after the deadline passed
            _shutdown(sock)

    def _run(self) -> None:
        try:
            # A redirect is never followed: the signed request must not reach
            # a host the URL policy never checked.
            response = self._client.send(self._request, stream=True, follow_redirects=False)
            try:
                self._status = response.status_code
            finally:
                response.close()
        except BaseException as exc:  # handed over to the worker's thread
            self._error = exc

    def status_within(self, seconds: float) -> int | None:
        self._thread.start()
        self._thread.join(seconds)
        if self._thread.is_alive():
            self.cut()
            raise _DeadlinePassed
        if self._error is not None:
            raise self._error
        return self._status

    def cut(self) -> None:
        with self._lock:
            self._cut = True
            sockets = list(self._sockets)
        for sock in sockets:
            _shutdown(sock)

    def wait(self, seconds: float) -> None:
        if self._thread.is_alive():
            self._thread.join(seconds)


def _shutdown(sock: Any) -> None:
    # shutdown(), unlike close(), also wakes a read that is waiting on the socket.
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _send(
    claim: _Claim,
    *,
    timestamp: int,
    settings: Settings,
    http_client: httpx.Client | None,
) -> _Result:
    headers = request_headers(
        claim.keys, event_id=claim.event_id, timestamp=timestamp, body=claim.payload
    )
    deadline = float(settings.webhook_delivery_timeout_seconds)
    started = time.monotonic()
    own_client = http_client is None
    client = http_client or httpx.Client(timeout=deadline, follow_redirects=False)
    exchange = _Exchange(client, claim.url, claim.payload, headers)
    status_code: int | None = None
    error_code: str | None = None
    try:
        # The deadline counts from the start of the send, for every client.
        status_code = exchange.status_within(deadline)
    except (_DeadlinePassed, httpx.TimeoutException):
        error_code = TIMEOUT
    except SoftTimeLimitExceeded:
        # Celery's backstop fired while this task was sending.
        exchange.cut()
        error_code = TIMEOUT
    except Exception as exc:
        error_code = CONNECTION_ERROR
        if not isinstance(exc, httpx.HTTPError):
            logger.error(
                "WEBHOOK_DELIVERY_SEND_ERROR delivery_id=%s error_type=%s",
                claim.delivery_id,
                type(exc).__name__,
            )
    finally:
        if own_client:
            exchange.wait(_CUT_GRACE_SECONDS)
            client.close()
    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    if status_code is not None and not 100 <= status_code <= 599:
        status_code, error_code = None, HTTP_STATUS  # not a status the ledger can hold
    if status_code is not None and 200 <= status_code <= 299:
        return _Result(WebhookAttemptOutcome.DELIVERED.value, status_code, None, duration_ms)
    if error_code is None:
        error_code = HTTP_STATUS
    return _Result(WebhookAttemptOutcome.FAILED.value, status_code, error_code, duration_ms)


def _record(claim: _Claim, result: _Result, *, timestamp: int, now: datetime) -> None:
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=claim.workspace_id)
        db.add(
            WebhookDeliveryAttempt(
                workspace_id=claim.workspace_id,
                delivery_id=claim.delivery_id,
                attempt_number=claim.attempt_number,
                request_timestamp=timestamp,
                signing_key_ids=[key_id for key_id, _secret in claim.keys],
                started_at=now,
                duration_ms=result.duration_ms,
                outcome=result.outcome,
                status_code=result.status_code,
                error_code=result.error_code,
            )
        )
        delivery = db.execute(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.id == claim.delivery_id,
                WebhookDelivery.state == WebhookDeliveryState.SENDING.value,
                WebhookDelivery.attempt_count == claim.attempt_number,
            )
            .with_for_update()
        ).scalar_one_or_none()
        # None: a later claim took the row over after this lease expired; the
        # attempt row still records that this request left.
        if delivery is not None:
            delivered = result.outcome == WebhookAttemptOutcome.DELIVERED.value
            delivery.state = (
                WebhookDeliveryState.DELIVERED.value
                if delivered
                else WebhookDeliveryState.FAILED.value
            )
            delivery.delivered_at = now if delivered else None
            delivery.last_status_code = result.status_code
            delivery.last_error = result.error_code
            delivery.next_attempt_at = None
        db.commit()
    logger.info(
        "WEBHOOK_DELIVERY_ATTEMPT delivery_id=%s endpoint_id=%s attempt=%s outcome=%s "
        "status_code=%s error_code=%s",
        claim.delivery_id,
        claim.endpoint_id,
        claim.attempt_number,
        result.outcome,
        result.status_code,
        result.error_code,
    )


def deliver_webhook(
    delivery_id: str, *, now: datetime | None = None, http_client: httpx.Client | None = None
) -> None:
    """Send one due delivery once; safe to call repeatedly and concurrently.

    ``now`` drives the due check, the lease, the stored times and the signed
    timestamp. With ``http_client`` every request goes through that client.
    """
    settings = get_settings()
    now = _as_utc(now or datetime.now(UTC))
    workspace_id = _workspace_of(delivery_id)
    if workspace_id is None:
        return
    claim = _claim(delivery_id, workspace_id, now=now, settings=settings)
    if claim is None:
        return
    timestamp = int(now.timestamp())
    result = _send(claim, timestamp=timestamp, settings=settings, http_client=http_client)
    _record(claim, result, timestamp=timestamp, now=now)


def publish_delivery(delivery_id: str, settings: Settings) -> bool:
    """Hand one delivery to the worker queue; never raises.

    True only when a task was handed to the broker. With ``tasks_eager``
    (development, tests) nothing is published: an eager task would run the
    delivery inside the request, which must never wait on a receiver, and a
    test must never reach the network. The row stays due;
    ``python -m backend.jobs.webhook_delivery`` (or a worker's sweep) delivers it.
    """
    if settings.tasks_eager:
        return False
    try:
        from backend.celery_app import PROCESSING_QUEUE
        from backend.jobs.tasks import deliver_webhook_task

        deliver_webhook_task.apply_async(args=[delivery_id], queue=PROCESSING_QUEUE)
    except Exception as exc:
        logger.error(
            "WEBHOOK_DELIVERY_PUBLISH_FAILED delivery_id=%s error_type=%s",
            delivery_id,
            type(exc).__name__,
        )
        return False
    return True


def due_delivery_ids(*, now: datetime, limit: int) -> list[str]:
    """Due deliveries of every workspace, oldest first (read-only service scope)."""
    with get_session_factory()() as db:
        bind_service_scope(db, SERVICE_SCOPE_WEBHOOK_DELIVERY)
        due = list(
            db.scalars(
                select(WebhookDelivery.id)
                .where(_due(now))
                .order_by(
                    WebhookDelivery.next_attempt_at.asc().nulls_first(),
                    WebhookDelivery.id.asc(),
                )
                .limit(limit)
            )
        )
        db.rollback()
    return due


def sweep_webhook_deliveries(*, now: datetime | None = None, limit: int = 100) -> list[str]:
    """Publish one delivery task per due row; returns the ids it published, in order.

    Duplicate tasks are harmless: the claim decides who sends. With
    ``tasks_eager`` nothing is published, so the result is empty and the rows
    stay due.
    """
    settings = get_settings()
    now = _as_utc(now or datetime.now(UTC))
    due = due_delivery_ids(now=now, limit=limit)
    published = [delivery_id for delivery_id in due if publish_delivery(delivery_id, settings)]
    if due:
        logger.info("WEBHOOK_DELIVERY_SWEEP due=%s published=%s", len(due), len(published))
    return published


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    import json
    import sys

    from backend.database.relational import configure_database

    configure_database()
    # Without a worker: deliver every due row in this process.
    ids = due_delivery_ids(now=datetime.now(UTC), limit=100)
    for delivery_id in ids:
        deliver_webhook(delivery_id)
    print(json.dumps({"delivered_or_failed": len(ids)}))
    sys.exit(0)
