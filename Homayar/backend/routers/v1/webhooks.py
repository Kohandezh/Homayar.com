"""Outbound webhook endpoints and their delivery ledger (HOM-89, HOM-90, ADR 0050).

Management is session-only in effect: every route requires ``webhook:manage``,
which is never an API key scope, so a key is refused with
``403 api_key_scope_denied``. The signing secret appears in the create and
rotate answers only; views carry key ids and lifetimes, never key material.
There is no delete route: ``PATCH {is_active: false}`` keeps the ledger.

Diagnostics (HOM-90) list deliveries, show one with its attempts, and count an
endpoint's deliveries per state. They carry ids, states and short codes only:
never the payload, the endpoint URL, a secret, headers or a signature. A
redelivery moves a ``dead_letter`` or ``failed`` row back to ``pending`` with
the same event id and bytes, so the receiver drops a duplicate. It is rate
limited per workspace and audited.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import (
    WebhookDelivery,
    WebhookDeliveryAttempt,
    WebhookEndpoint,
    WebhookSigningKey,
)
from backend.jobs.webhook_delivery import publish_delivery
from backend.schemas.webhooks import (
    WebhookDeadLettersRedeliveredResponse,
    WebhookDeadLettersRedeliverRequest,
    WebhookDeliveryAttemptView,
    WebhookDeliveryDetailView,
    WebhookDeliveryListResponse,
    WebhookDeliveryStateCounts,
    WebhookDeliveryStateName,
    WebhookDeliveryView,
    WebhookEndpointCreatedResponse,
    WebhookEndpointCreateRequest,
    WebhookEndpointDeliverySummary,
    WebhookEndpointListResponse,
    WebhookEndpointUpdateRequest,
    WebhookEndpointView,
    WebhookRedeliveryQueuedResponse,
    WebhookSigningKeyRotatedResponse,
    WebhookSigningKeyRotateRequest,
    WebhookSigningKeyView,
    WebhookTestQueuedResponse,
)
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_permission,
    require_write_permission,
)
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.rate_limit_service import (
    RateLimiter,
    RateLimitExceeded,
    RateLimitUnavailable,
)
from backend.services.webhook_service import (
    DELIVERY_PAGE_DEFAULT,
    DELIVERY_PAGE_MAX,
    WebhookError,
    check_redeliverable,
    create_endpoint,
    delivery_attempts,
    delivery_summary,
    enqueue_event,
    get_delivery,
    get_endpoint,
    list_deliveries,
    list_endpoints,
    redeliver,
    redeliver_dead_letters,
    rotate_signing_key,
    signing_keys_by_endpoint,
    update_endpoint,
    url_audit_fields,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _key_view(key: WebhookSigningKey, now: datetime) -> WebhookSigningKeyView:
    expires_at = _as_utc(key.expires_at)
    if expires_at is None:
        state = "active"
    elif expires_at > now:
        state = "retiring"
    else:
        state = "expired"
    return WebhookSigningKeyView(
        key_id=key.key_id,
        created_at=_as_utc(key.created_at),
        expires_at=expires_at,
        state=state,
    )


def _view(endpoint: WebhookEndpoint, keys: list[WebhookSigningKey]) -> WebhookEndpointView:
    now = datetime.now(UTC)
    return WebhookEndpointView(
        id=endpoint.id,
        url=endpoint.url,
        description=endpoint.description,
        event_types=list(endpoint.event_types or []),
        is_active=endpoint.is_active,
        created_at=_as_utc(endpoint.created_at),
        updated_at=_as_utc(endpoint.updated_at),
        signing_keys=[_key_view(key, now) for key in keys],
    )


def _endpoint_view(db: Session, endpoint: WebhookEndpoint) -> WebhookEndpointView:
    return _view(endpoint, signing_keys_by_endpoint(db, [endpoint.id])[endpoint.id])


def _audit(
    db: Session,
    context: WorkspaceContext,
    request: Request,
    settings: Settings,
    *,
    event_type: AuditEventType,
    target_id: str,
    details: dict,
) -> None:
    # Ids, key ids, event types, states, counts, is_active and the URL's scheme
    # and host only: never a secret, the full URL or a payload.
    record_audit_event(
        db,
        event_type=event_type,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type=event_type.target_type,
        target_id=target_id,
        request_id=getattr(request.state, "request_id", None),
        details=details,
        settings=settings,
    )


def _enforce_hourly_limit(
    settings: Settings, *, key: str, limit: int, code: str, too_many: str, unavailable: str
) -> None:
    # Like uploads: over the limit 429 with Retry-After; limiter down 503
    # (or let through with RATE_LIMIT_FAIL_OPEN).
    try:
        RateLimiter(settings).hit(key, limit=limit, window_seconds=3600)
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": code, "message": too_many},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except RateLimitUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "rate_limiter_unavailable", "message": unavailable},
        ) from exc


def _enforce_test_rate_limit(settings: Settings, workspace_id: str) -> None:
    # Each test event is a real request to a customer's server.
    _enforce_hourly_limit(
        settings,
        key=f"webhook_test:workspace:{workspace_id}",
        limit=settings.webhook_test_rate_limit_per_hour,
        code="webhook_test_rate_limited",
        too_many="Too many webhook tests",
        unavailable="Webhook tests are temporarily unavailable",
    )


def _enforce_redeliver_rate_limit(settings: Settings, workspace_id: str) -> None:
    # One hit per call, for one row or a bulk batch: each sends real requests.
    _enforce_hourly_limit(
        settings,
        key=f"webhook_redeliver:workspace:{workspace_id}",
        limit=settings.webhook_redeliver_rate_limit_per_hour,
        code="webhook_redeliver_rate_limited",
        too_many="Too many webhook redeliveries",
        unavailable="Webhook redelivery is temporarily unavailable",
    )


def _require_active(endpoint: WebhookEndpoint) -> None:
    if not endpoint.is_active:
        raise WebhookError(409, "webhook_endpoint_inactive", "The webhook endpoint is not active")


def _delivery_fields(delivery: WebhookDelivery) -> dict:
    # Never the payload or its digest: ids, state and short codes only.
    return {
        "id": delivery.id,
        "endpoint_id": delivery.endpoint_id,
        "event_id": delivery.event_id,
        "event_type": delivery.event_type,
        "state": delivery.state,
        "attempt_count": delivery.attempt_count,
        "next_attempt_at": _as_utc(delivery.next_attempt_at),
        "last_attempt_at": _as_utc(delivery.last_attempt_at),
        "delivered_at": _as_utc(delivery.delivered_at),
        "last_status_code": delivery.last_status_code,
        "last_error": delivery.last_error,
        "created_at": _as_utc(delivery.created_at),
        "updated_at": _as_utc(delivery.updated_at),
    }


def _attempt_view(attempt: WebhookDeliveryAttempt) -> WebhookDeliveryAttemptView:
    return WebhookDeliveryAttemptView(
        attempt_number=attempt.attempt_number,
        started_at=_as_utc(attempt.started_at),
        duration_ms=attempt.duration_ms,
        outcome=attempt.outcome,
        status_code=attempt.status_code,
        error_code=attempt.error_code,
        signing_key_ids=list(attempt.signing_key_ids or []),
        request_timestamp=attempt.request_timestamp,
    )


@router.post("/endpoints", response_model=WebhookEndpointCreatedResponse, status_code=201)
def create_webhook_endpoint(
    payload: WebhookEndpointCreateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.WEBHOOK_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WebhookEndpointCreatedResponse:
    endpoint, key, secret = create_endpoint(
        db,
        workspace_id=context.workspace.id,
        user_id=context.actor.user.id,
        url=payload.url,
        event_types=payload.event_types,
        description=payload.description,
        settings=settings,
    )
    _audit(
        db,
        context,
        request,
        settings,
        event_type=AuditEventType.WEBHOOK_ENDPOINT_CREATED,
        target_id=endpoint.id,
        details={
            "key_id": key.key_id,
            "event_types": list(endpoint.event_types),
            "is_active": endpoint.is_active,
            **url_audit_fields(endpoint.url),
        },
    )
    db.commit()
    db.refresh(endpoint)
    return WebhookEndpointCreatedResponse(
        endpoint=_endpoint_view(db, endpoint), key_id=key.key_id, signing_secret=secret
    )


@router.get("/endpoints", response_model=WebhookEndpointListResponse)
def list_webhook_endpoints(
    context: WorkspaceContext = Depends(require_permission(Permission.WEBHOOK_MANAGE)),
    db: Session = Depends(get_db),
) -> WebhookEndpointListResponse:
    endpoints = list_endpoints(db, workspace_id=context.workspace.id)
    keys = signing_keys_by_endpoint(db, [endpoint.id for endpoint in endpoints])
    return WebhookEndpointListResponse(
        items=[_view(endpoint, keys[endpoint.id]) for endpoint in endpoints]
    )


@router.get("/endpoints/{endpoint_id}", response_model=WebhookEndpointView)
def get_webhook_endpoint(
    endpoint_id: str,
    context: WorkspaceContext = Depends(require_permission(Permission.WEBHOOK_MANAGE)),
    db: Session = Depends(get_db),
) -> WebhookEndpointView:
    endpoint = get_endpoint(db, workspace_id=context.workspace.id, endpoint_id=endpoint_id)
    return _endpoint_view(db, endpoint)


@router.patch("/endpoints/{endpoint_id}", response_model=WebhookEndpointView)
def update_webhook_endpoint(
    endpoint_id: str,
    payload: WebhookEndpointUpdateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.WEBHOOK_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WebhookEndpointView:
    endpoint = get_endpoint(
        db, workspace_id=context.workspace.id, endpoint_id=endpoint_id, for_update=True
    )
    changed = update_endpoint(
        db, endpoint=endpoint, changes=payload.model_dump(exclude_unset=True), settings=settings
    )
    if changed:
        details: dict = {
            "changed_fields": changed,
            "event_types": list(endpoint.event_types),
            "is_active": endpoint.is_active,
        }
        if "url" in changed:
            details.update(url_audit_fields(endpoint.url))
        _audit(
            db,
            context,
            request,
            settings,
            event_type=AuditEventType.WEBHOOK_ENDPOINT_UPDATED,
            target_id=endpoint.id,
            details=details,
        )
        db.commit()
        db.refresh(endpoint)
    return _endpoint_view(db, endpoint)


@router.post(
    "/endpoints/{endpoint_id}/signing-keys",
    response_model=WebhookSigningKeyRotatedResponse,
    status_code=201,
)
def rotate_webhook_signing_key(
    endpoint_id: str,
    request: Request,
    payload: WebhookSigningKeyRotateRequest | None = Body(default=None),
    context: WorkspaceContext = Depends(require_write_permission(Permission.WEBHOOK_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WebhookSigningKeyRotatedResponse:
    payload = payload or WebhookSigningKeyRotateRequest()
    endpoint = get_endpoint(
        db, workspace_id=context.workspace.id, endpoint_id=endpoint_id, for_update=True
    )
    key, secret, previous = rotate_signing_key(
        db,
        endpoint=endpoint,
        overlap_seconds=payload.overlap_seconds,
        user_id=context.actor.user.id,
        settings=settings,
    )
    previous_key_id = previous.key_id if previous is not None else None
    previous_expires_at = _as_utc(previous.expires_at) if previous is not None else None
    _audit(
        db,
        context,
        request,
        settings,
        event_type=AuditEventType.WEBHOOK_SIGNING_KEY_ROTATED,
        target_id=endpoint.id,
        details={
            "key_id": key.key_id,
            "previous_key_id": previous_key_id,
            "previous_key_expires_at": (
                previous_expires_at.isoformat() if previous_expires_at else None
            ),
            "overlap_seconds": payload.overlap_seconds,
        },
    )
    db.commit()
    return WebhookSigningKeyRotatedResponse(
        key_id=key.key_id,
        signing_secret=secret,
        previous_key_id=previous_key_id,
        previous_key_expires_at=previous_expires_at,
    )


@router.post(
    "/endpoints/{endpoint_id}/test", response_model=WebhookTestQueuedResponse, status_code=202
)
def queue_webhook_test(
    endpoint_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.WEBHOOK_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WebhookTestQueuedResponse:
    endpoint = get_endpoint(db, workspace_id=context.workspace.id, endpoint_id=endpoint_id)
    _require_active(endpoint)
    # Before anything is written: a refused test leaves no delivery row.
    _enforce_test_rate_limit(settings, context.workspace.id)
    [delivery] = enqueue_event(
        db,
        workspace_id=context.workspace.id,
        event_type="webhook.test",
        data={"endpoint_id": endpoint.id},
        endpoint_id=endpoint.id,
    )
    delivery_id, event_id = delivery.id, delivery.event_id
    _audit(
        db,
        context,
        request,
        settings,
        event_type=AuditEventType.WEBHOOK_TEST_QUEUED,
        target_id=endpoint.id,
        details={"delivery_id": delivery_id, "event_id": event_id},
    )
    db.commit()
    # After the commit, best effort: a lost publish is found by the sweeper.
    publish_delivery(delivery_id, settings)
    return WebhookTestQueuedResponse(delivery_id=delivery_id, event_id=event_id)


# --- delivery diagnostics and redelivery (HOM-90) --------------------------------------


@router.get("/deliveries", response_model=WebhookDeliveryListResponse)
def list_webhook_deliveries(
    endpoint_id: str | None = Query(default=None, max_length=100),
    state: WebhookDeliveryStateName | None = Query(default=None),
    event_type: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=DELIVERY_PAGE_DEFAULT, ge=1, le=DELIVERY_PAGE_MAX),
    cursor: str | None = Query(default=None),
    context: WorkspaceContext = Depends(require_permission(Permission.WEBHOOK_MANAGE)),
    db: Session = Depends(get_db),
) -> WebhookDeliveryListResponse:
    # Another workspace's endpoint id finds no rows: an empty page, not a 404.
    page = list_deliveries(
        db,
        workspace_id=context.workspace.id,
        limit=limit,
        cursor=cursor,
        endpoint_id=endpoint_id,
        state=state,
        event_type=event_type,
    )
    return WebhookDeliveryListResponse(
        items=[WebhookDeliveryView(**_delivery_fields(delivery)) for delivery in page.deliveries],
        next_cursor=page.next_cursor,
    )


@router.get("/deliveries/{delivery_id}", response_model=WebhookDeliveryDetailView)
def get_webhook_delivery(
    delivery_id: str,
    context: WorkspaceContext = Depends(require_permission(Permission.WEBHOOK_MANAGE)),
    db: Session = Depends(get_db),
) -> WebhookDeliveryDetailView:
    delivery = get_delivery(db, workspace_id=context.workspace.id, delivery_id=delivery_id)
    attempts, total = delivery_attempts(db, delivery=delivery)
    return WebhookDeliveryDetailView(
        **_delivery_fields(delivery),
        attempts=[_attempt_view(attempt) for attempt in attempts],
        attempt_total=total,
    )


@router.get(
    "/endpoints/{endpoint_id}/delivery-summary", response_model=WebhookEndpointDeliverySummary
)
def get_webhook_delivery_summary(
    endpoint_id: str,
    context: WorkspaceContext = Depends(require_permission(Permission.WEBHOOK_MANAGE)),
    db: Session = Depends(get_db),
) -> WebhookEndpointDeliverySummary:
    endpoint = get_endpoint(db, workspace_id=context.workspace.id, endpoint_id=endpoint_id)
    summary = delivery_summary(db, endpoint=endpoint)
    return WebhookEndpointDeliverySummary(
        endpoint_id=endpoint.id,
        counts=WebhookDeliveryStateCounts(**summary.counts),
        last_delivered_at=_as_utc(summary.last_delivered_at),
        last_failure_at=_as_utc(summary.last_failure_at),
        last_failure_error_code=summary.last_failure_error_code,
    )


@router.post(
    "/deliveries/{delivery_id}/redeliver",
    response_model=WebhookRedeliveryQueuedResponse,
    status_code=202,
)
def redeliver_webhook_delivery(
    delivery_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.WEBHOOK_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WebhookRedeliveryQueuedResponse:
    # The row lock comes first: a parallel second call waits here, then sees
    # ``pending`` and is refused before it reaches the rate limit.
    delivery = get_delivery(
        db, workspace_id=context.workspace.id, delivery_id=delivery_id, for_update=True
    )
    check_redeliverable(delivery)
    _require_active(
        get_endpoint(db, workspace_id=context.workspace.id, endpoint_id=delivery.endpoint_id)
    )
    # After every refusal and before any write: a refused call costs nothing.
    _enforce_redeliver_rate_limit(settings, context.workspace.id)
    previous_state = redeliver(db, delivery=delivery)
    event_id = delivery.event_id
    _audit(
        db,
        context,
        request,
        settings,
        event_type=AuditEventType.WEBHOOK_DELIVERY_REDELIVERED,
        target_id=delivery_id,
        details={
            "endpoint_id": delivery.endpoint_id,
            "event_id": event_id,
            "event_type": delivery.event_type,
            "previous_state": previous_state,
        },
    )
    db.commit()
    # After the commit, best effort: a lost publish is found by the sweeper.
    publish_delivery(delivery_id, settings)
    return WebhookRedeliveryQueuedResponse(
        delivery_id=delivery_id, event_id=event_id, state="pending"
    )


@router.post(
    "/endpoints/{endpoint_id}/redeliver-dead-letters",
    response_model=WebhookDeadLettersRedeliveredResponse,
    status_code=202,
)
def redeliver_webhook_dead_letters(
    endpoint_id: str,
    request: Request,
    payload: WebhookDeadLettersRedeliverRequest | None = Body(default=None),
    context: WorkspaceContext = Depends(require_write_permission(Permission.WEBHOOK_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WebhookDeadLettersRedeliveredResponse:
    payload = payload or WebhookDeadLettersRedeliverRequest()
    endpoint = get_endpoint(db, workspace_id=context.workspace.id, endpoint_id=endpoint_id)
    _require_active(endpoint)
    _enforce_redeliver_rate_limit(settings, context.workspace.id)
    since = _as_utc(payload.since)
    since = since.astimezone(UTC) if since is not None else None
    count, remaining = redeliver_dead_letters(db, endpoint=endpoint, since=since)
    _audit(
        db,
        context,
        request,
        settings,
        event_type=AuditEventType.WEBHOOK_DEAD_LETTERS_REDELIVERED,
        target_id=endpoint.id,
        details={
            "endpoint_id": endpoint.id,
            "count": count,
            "since": since.isoformat() if since is not None else None,
        },
    )
    db.commit()
    # No publish: the sweeper sends them at its own pace.
    return WebhookDeadLettersRedeliveredResponse(
        endpoint_id=endpoint_id, redelivered=count, remaining=remaining
    )
