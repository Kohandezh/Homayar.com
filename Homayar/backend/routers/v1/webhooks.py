"""Outbound webhook endpoints (HOM-89, ADR 0050).

Management is session-only in effect: every route requires ``webhook:manage``,
which is never an API key scope, so a key is refused with
``403 api_key_scope_denied``. The signing secret appears in the create and
rotate answers only; views carry key ids and lifetimes, never key material.
There is no delete route: ``PATCH {is_active: false}`` keeps the ledger.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import WebhookEndpoint, WebhookSigningKey
from backend.jobs.webhook_delivery import publish_delivery
from backend.schemas.webhooks import (
    WebhookEndpointCreatedResponse,
    WebhookEndpointCreateRequest,
    WebhookEndpointListResponse,
    WebhookEndpointUpdateRequest,
    WebhookEndpointView,
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
    WebhookError,
    create_endpoint,
    enqueue_event,
    get_endpoint,
    list_endpoints,
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
    endpoint_id: str,
    details: dict,
) -> None:
    # Ids, key ids, event types, is_active and the URL's scheme and host only:
    # never a secret, the full URL or a payload.
    record_audit_event(
        db,
        event_type=event_type,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="webhook_endpoint",
        target_id=endpoint_id,
        request_id=getattr(request.state, "request_id", None),
        details=details,
        settings=settings,
    )


def _enforce_test_rate_limit(settings: Settings, workspace_id: str) -> None:
    # Like uploads: each test event is a real request to a customer's server.
    try:
        RateLimiter(settings).hit(
            f"webhook_test:workspace:{workspace_id}",
            limit=settings.webhook_test_rate_limit_per_hour,
            window_seconds=3600,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": "webhook_test_rate_limited", "message": "Too many webhook tests"},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except RateLimitUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "rate_limiter_unavailable",
                "message": "Webhook tests are temporarily unavailable",
            },
        ) from exc


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
        endpoint_id=endpoint.id,
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
            endpoint_id=endpoint.id,
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
        endpoint_id=endpoint.id,
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
    if not endpoint.is_active:
        raise WebhookError(409, "webhook_endpoint_inactive", "The webhook endpoint is not active")
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
        endpoint_id=endpoint.id,
        details={"delivery_id": delivery_id, "event_id": event_id},
    )
    db.commit()
    # After the commit, best effort: a lost publish is found by the sweeper.
    publish_delivery(delivery_id, settings)
    return WebhookTestQueuedResponse(delivery_id=delivery_id, event_id=event_id)
