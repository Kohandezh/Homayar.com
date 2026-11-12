"""The organization audit log read (HOM-243, ADR 0060).

An event belongs to organization A when all of these hold:

* its ``event_type`` is one of the catalog's eight ``organization.*`` types;
* ``details.organization_id`` equals A's id;
* ``workspace_id`` is NULL, or names a workspace whose ``organization_id``
  is A, whatever that workspace's status.

``audit_events`` carries no ``organization_id`` column and PostgreSQL's
tenant policy admits only ``workspace_id = <bound workspace>`` or
``workspace_id IS NULL`` rows, so the read walks the same path
``organization_service.scoped_records`` uses: the NULL-workspace rows are
queried under the organization context, then every workspace of the
organization is bound in turn for its own rows. The earlier context is
restored afterwards, so the request stays in the organization context and
no workspace remains bound. Every pass is ordered newest first and cut to
``limit + 1`` rows; the merged page is the first ``limit + 1`` of their union
in the same order, so the cursor walks the whole set exactly once. No index
or migration is added: the organization's events are an administration-scale
set, already narrowed by the ``event_type`` and ``details`` predicates.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from backend.database.saas_models import AuditEvent, Organization, Workspace
from backend.database.tenant_context import bind_tenant_context, current_context, restore_context
from backend.services.admin_service import (
    AuditEventPage,
    InvalidAuditRange,
    _encode_audit_cursor,
    _normalize_datetime,
    audit_event_filter_conditions,
)
from backend.services.audit_event_types import AuditEventType

ORGANIZATION_EVENT_TYPES: tuple[AuditEventType, ...] = tuple(
    sorted(
        (member for member in AuditEventType if member.value.startswith("organization.")),
        key=lambda member: member.value,
    )
)
_ORGANIZATION_EVENT_TYPE_VALUES: tuple[str, ...] = tuple(
    member.value for member in ORGANIZATION_EVENT_TYPES
)


def organization_audit_conditions(organization_id: str) -> list[ColumnElement[bool]]:
    """Row selection of one organization's events, before any page filter."""
    return [
        AuditEvent.event_type.in_(_ORGANIZATION_EVENT_TYPE_VALUES),
        AuditEvent.details["organization_id"].as_string() == organization_id,
    ]


def list_organization_audit_events(
    db: Session,
    *,
    organization: Organization,
    limit: int,
    cursor: str | None = None,
    event_type: str | None = None,
    outcome: str | None = None,
    actor_user_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    occurred_after: datetime | None = None,
    occurred_before: datetime | None = None,
) -> AuditEventPage:
    if occurred_after is not None and occurred_before is not None:
        if _normalize_datetime(occurred_after) >= _normalize_datetime(occurred_before):
            raise InvalidAuditRange("occurred_after must be earlier than occurred_before")

    conditions = audit_event_filter_conditions(
        event_type=event_type,
        outcome=outcome,
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
        occurred_after=occurred_after,
        occurred_before=occurred_before,
        cursor=cursor,
    )
    own = organization_audit_conditions(organization.id)

    def page_of(placement: ColumnElement[bool]) -> list[AuditEvent]:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(placement, *own, *conditions)
                .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
                .limit(limit + 1)
            )
        )

    saved = current_context(db)
    try:
        events = page_of(AuditEvent.workspace_id.is_(None))
        workspace_ids = list(
            db.scalars(
                select(Workspace.id)
                .where(Workspace.organization_id == organization.id)
                .order_by(Workspace.created_at.asc(), Workspace.id.asc())
            )
        )
        for workspace_id in workspace_ids:
            bind_tenant_context(db, workspace_id=workspace_id, organization_id=organization.id)
            events.extend(page_of(AuditEvent.workspace_id == workspace_id))
    finally:
        restore_context(db, saved)

    events.sort(key=lambda event: (event.occurred_at, event.id), reverse=True)
    has_more = len(events) > limit
    page_events = events[:limit]
    next_cursor = _encode_audit_cursor(page_events[-1]) if has_more else None
    return AuditEventPage(events=page_events, next_cursor=next_cursor)
