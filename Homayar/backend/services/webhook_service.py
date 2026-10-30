"""Outbound webhook endpoints, signing keys and event enqueue (HOM-89, ADR 0047).

* Management is for owners and admins in a session (``webhook:manage`` is
  never an API key scope). A signing secret leaves the server only in the
  create and rotate answers; at rest it is AES-GCM ciphertext.
* ``enqueue_event`` is the producer API. It runs in the caller's transaction
  (outbox rule): the delivery rows commit together with the business change,
  or not at all. It never commits and never touches the network.
* The URL policy here runs at create, at update and again before every send.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.saas_models import (
    WebhookDelivery,
    WebhookDeliveryState,
    WebhookEndpoint,
    WebhookSigningKey,
    new_id,
)
from backend.providers.egress import _METADATA_ADDRESSES, _METADATA_HOSTS, _address
from backend.services.webhook_signing import (
    WebhookSecretCipher,
    encode_payload,
    new_key_id,
    new_signing_secret,
)

# Only events that something sends are advertised. Later tickets add business
# events together with their producers.
WEBHOOK_EVENT_TYPES: tuple[str, ...] = ("webhook.test",)

WEBHOOK_URL_MAX_LENGTH = 1000

# Spaces and control characters. urlsplit() silently drops some of them, so
# they are refused before parsing.
_UNSAFE_CHARACTERS = re.compile(r"[\x00-\x20\x7f]")
# A last label that is all digits or 0x-hex: resolvers may read such a host
# as an IPv4 address (2852039166, 0xA9FEA9FE, 169.254.43518). No real domain
# ends this way, so only the canonical dotted form of an address is accepted.
_NUMERIC_LABEL = re.compile(r"(?:[0-9]+|0x[0-9a-f]*)")


def webhook_url_refusal(url: str, *, production: bool) -> str | None:
    """Why ``url`` cannot be a webhook endpoint, or ``None``.

    Refused everywhere: too long, not http(s), no host, userinfo, cloud
    metadata, link-local, unspecified or multicast addresses. In production
    also plain http and loopback. Private addresses stay allowed: on-premise
    CRMs live there. What a hostname resolves to at send time is the network
    layer's job (ADR 0031). The reason never repeats the URL, which can carry a
    token in its query.
    """
    if len(url) > WEBHOOK_URL_MAX_LENGTH:
        return f"a webhook URL is at most {WEBHOOK_URL_MAX_LENGTH} characters"
    if _UNSAFE_CHARACTERS.search(url):
        return "a webhook URL has no spaces or control characters"
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises ValueError on a port out of range
    except ValueError:
        return "the webhook URL is not valid"
    if parts.scheme not in {"http", "https"}:
        return "a webhook URL uses http or https"
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return "a webhook URL has a host"
    if "@" in parts.netloc:
        return "a webhook URL carries no user name or password"
    address = _address(host)
    if address is not None and address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address is None and (":" in host or _NUMERIC_LABEL.fullmatch(host.rsplit(".", 1)[-1])):
        return "a webhook URL host is a name or a dotted IP address"
    if host in _METADATA_HOSTS or address in _METADATA_ADDRESSES:
        return "cloud metadata services are never a webhook endpoint"
    if address is not None and (
        address.is_link_local or address.is_unspecified or address.is_multicast
    ):
        return "link-local, unspecified and multicast addresses are never a webhook endpoint"
    if production:
        if parts.scheme != "https":
            return "a webhook URL uses https in production"
        if _is_loopback(host, address):
            return "a webhook URL is not a loopback host in production"
    return None


def _is_loopback(host: str, address: ipaddress.IPv4Address | ipaddress.IPv6Address | None) -> bool:
    if address is not None:
        return address.is_loopback
    return host == "localhost" or host.endswith(".localhost")


# --- errors ----------------------------------------------------------------------------


class WebhookError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message})


class UnknownWebhookEventType(ValueError):
    """``enqueue_event`` was called with a type outside ``WEBHOOK_EVENT_TYPES``."""


def endpoint_not_found() -> WebhookError:
    # Another workspace's endpoint and an unknown id look alike.
    return WebhookError(404, "webhook_endpoint_not_found", "Webhook endpoint not found")


def check_url(url: str, settings: Settings) -> None:
    reason = webhook_url_refusal(url, production=settings.is_production)
    if reason is not None:
        raise WebhookError(422, "webhook_url_refused", f"The webhook URL is refused: {reason}")


def url_audit_fields(url: str) -> dict[str, str]:
    """The scheme and host only: a URL's path or query can carry a token."""
    parts = urlsplit(url)
    return {"url_scheme": parts.scheme, "url_host": parts.hostname or ""}


def normalize_event_types(values: Iterable[str]) -> list[str]:
    """Catalogue types in the given order, each once. An empty list is allowed."""
    values = list(values)
    unknown = sorted({value for value in values if value not in WEBHOOK_EVENT_TYPES})
    if unknown:
        named = ", ".join(value[:100] for value in unknown[:10])
        raise WebhookError(
            422, "webhook_event_type_unknown", f"Unknown webhook event types: {named}"
        )
    return list(dict.fromkeys(values))


def signing_cipher(settings: Settings) -> WebhookSecretCipher:
    cipher = WebhookSecretCipher.from_settings(settings)
    if cipher is None:
        raise WebhookError(
            503,
            "webhook_signing_unavailable",
            "Webhook signing is not configured on this server (WEBHOOK_SECRET_KEYS)",
        )
    return cipher


def _as_utc(value: datetime) -> datetime:
    value = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


# --- endpoints and keys ----------------------------------------------------------------


def _check_endpoint_limit(
    db: Session, workspace_id: str, settings: Settings, *, excluding: str | None = None
) -> None:
    query = (
        select(func.count())
        .select_from(WebhookEndpoint)
        .where(WebhookEndpoint.workspace_id == workspace_id, WebhookEndpoint.is_active.is_(True))
    )
    if excluding is not None:
        query = query.where(WebhookEndpoint.id != excluding)
    limit = settings.webhook_max_endpoints_per_workspace
    if (db.scalar(query) or 0) >= limit:
        raise WebhookError(
            409,
            "webhook_endpoint_limit",
            f"A workspace can have at most {limit} active webhook endpoints",
        )


def _add_signing_key(
    db: Session,
    cipher: WebhookSecretCipher,
    endpoint: WebhookEndpoint,
    *,
    created_by_user_id: str | None,
) -> tuple[WebhookSigningKey, str]:
    secret = new_signing_secret()
    key_id = new_key_id()
    encryption_key_id, nonce, ciphertext = cipher.encrypt(
        secret, workspace_id=endpoint.workspace_id, endpoint_id=endpoint.id, key_id=key_id
    )
    key = WebhookSigningKey(
        workspace_id=endpoint.workspace_id,
        endpoint_id=endpoint.id,
        key_id=key_id,
        encryption_key_id=encryption_key_id,
        nonce=nonce,
        secret_ciphertext=ciphertext,
        created_by_user_id=created_by_user_id,
    )
    db.add(key)
    db.flush()
    return key, secret


def create_endpoint(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    url: str,
    event_types: Iterable[str],
    description: str | None,
    settings: Settings,
) -> tuple[WebhookEndpoint, WebhookSigningKey, str]:
    """Add an active endpoint with its first key; returns the secret (shown once)."""
    check_url(url, settings)
    types = normalize_event_types(event_types)
    _check_endpoint_limit(db, workspace_id, settings)
    cipher = signing_cipher(settings)
    # The id is set here because the secret's associated data needs it.
    endpoint = WebhookEndpoint(
        id=new_id(),
        workspace_id=workspace_id,
        url=url,
        event_types=types,
        is_active=True,
        description=description,
        created_by_user_id=user_id,
    )
    db.add(endpoint)
    db.flush()
    key, secret = _add_signing_key(db, cipher, endpoint, created_by_user_id=user_id)
    return endpoint, key, secret


def list_endpoints(db: Session, *, workspace_id: str) -> list[WebhookEndpoint]:
    return list(
        db.scalars(
            select(WebhookEndpoint)
            .where(WebhookEndpoint.workspace_id == workspace_id)
            .order_by(WebhookEndpoint.created_at.asc(), WebhookEndpoint.id.asc())
        )
    )


def get_endpoint(
    db: Session, *, workspace_id: str, endpoint_id: str, for_update: bool = False
) -> WebhookEndpoint:
    query = select(WebhookEndpoint).where(
        WebhookEndpoint.id == endpoint_id, WebhookEndpoint.workspace_id == workspace_id
    )
    if for_update:
        # Serializes changes and rotations of one endpoint.
        query = query.with_for_update()
    endpoint = db.scalar(query)
    if endpoint is None:
        raise endpoint_not_found()
    return endpoint


def signing_keys_by_endpoint(
    db: Session, endpoint_ids: list[str]
) -> dict[str, list[WebhookSigningKey]]:
    """Every key of these endpoints, newest first."""
    keys: dict[str, list[WebhookSigningKey]] = {endpoint_id: [] for endpoint_id in endpoint_ids}
    if not endpoint_ids:
        return keys
    rows = db.scalars(
        select(WebhookSigningKey)
        .where(WebhookSigningKey.endpoint_id.in_(endpoint_ids))
        .order_by(WebhookSigningKey.created_at.desc(), WebhookSigningKey.id.desc())
    )
    for key in rows:
        keys[key.endpoint_id].append(key)
    return keys


def update_endpoint(
    db: Session, *, endpoint: WebhookEndpoint, changes: dict[str, Any], settings: Settings
) -> list[str]:
    """Apply ``changes`` (any of url, event_types, description, is_active).

    Everything is checked before anything changes. Returns the names of the
    fields that really changed.
    """
    wanted: dict[str, Any] = {}
    if "url" in changes:
        check_url(changes["url"], settings)
        wanted["url"] = changes["url"]
    if "event_types" in changes:
        wanted["event_types"] = normalize_event_types(changes["event_types"])
    if "description" in changes:
        wanted["description"] = changes["description"]
    if "is_active" in changes:
        wanted["is_active"] = bool(changes["is_active"])
    changed = [name for name, value in wanted.items() if getattr(endpoint, name) != value]
    if "is_active" in changed and wanted["is_active"]:
        _check_endpoint_limit(db, endpoint.workspace_id, settings, excluding=endpoint.id)
    for name in changed:
        setattr(endpoint, name, wanted[name])
    if changed:
        db.flush()
    return changed


def rotate_signing_key(
    db: Session,
    *,
    endpoint: WebhookEndpoint,
    overlap_seconds: int,
    user_id: str,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[WebhookSigningKey, str, WebhookSigningKey | None]:
    """Make a new active key; the previous active key signs until ``now + overlap``.

    Keys that are already retiring keep their expiry. With an overlap of 0 the
    previous key stops signing at once (for a leaked secret). The caller holds
    the endpoint row lock, so two rotations cannot both find the same active key.
    """
    cipher = signing_cipher(settings)
    now = _as_utc(now or datetime.now(UTC))
    previous = db.scalar(
        select(WebhookSigningKey).where(
            WebhookSigningKey.endpoint_id == endpoint.id, WebhookSigningKey.expires_at.is_(None)
        )
    )
    if previous is not None:
        previous.expires_at = now + timedelta(seconds=overlap_seconds)
        # Flushed first: at most one key per endpoint may have no expiry.
        db.flush()
    key, secret = _add_signing_key(db, cipher, endpoint, created_by_user_id=user_id)
    return key, secret, previous


# --- events ----------------------------------------------------------------------------


def enqueue_event(
    db: Session,
    *,
    workspace_id: str,
    event_type: str,
    data: dict,
    endpoint_id: str | None = None,
    now: datetime | None = None,
) -> list[WebhookDelivery]:
    """Queue one event for its endpoints in the caller's transaction.

    Targets are the workspace's active endpoints that subscribe to
    ``event_type``, or only ``endpoint_id`` when given (the test route), even
    if it does not subscribe. Every delivery of the event gets the same event
    id and the same payload bytes. ``data`` holds ids, states and counts only:
    never transcript text, personal data or credentials.
    """
    if event_type not in WEBHOOK_EVENT_TYPES:
        raise UnknownWebhookEventType(f"unknown webhook event type {event_type[:100]!r}")
    now = _as_utc(now or datetime.now(UTC))
    if endpoint_id is not None:
        endpoint = db.scalar(
            select(WebhookEndpoint).where(
                WebhookEndpoint.id == endpoint_id, WebhookEndpoint.workspace_id == workspace_id
            )
        )
        if endpoint is None:
            raise LookupError("unknown webhook endpoint")
        targets = [endpoint]
    else:
        active = db.scalars(
            select(WebhookEndpoint)
            .where(
                WebhookEndpoint.workspace_id == workspace_id,
                WebhookEndpoint.is_active.is_(True),
            )
            .order_by(WebhookEndpoint.created_at.asc(), WebhookEndpoint.id.asc())
        )
        targets = [endpoint for endpoint in active if event_type in (endpoint.event_types or [])]
    if not targets:
        return []
    event_id = str(uuid.uuid4())
    payload = encode_payload(
        event_id=event_id,
        event_type=event_type,
        workspace_id=workspace_id,
        data=data,
        created_at=now,
    )
    digest = hashlib.sha256(payload).hexdigest()
    deliveries = [
        WebhookDelivery(
            workspace_id=workspace_id,
            endpoint_id=endpoint.id,
            event_id=event_id,
            event_type=event_type,
            state=WebhookDeliveryState.PENDING.value,
            attempt_count=0,
            next_attempt_at=now,
            payload=payload,
            payload_sha256=digest,
        )
        for endpoint in targets
    ]
    db.add_all(deliveries)
    db.flush()
    return deliveries
