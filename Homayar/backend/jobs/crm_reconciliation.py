"""Delayed CRM records (HOM-88, DESIGN 4.5, ADR 0047).

A CRM record can arrive before its conversation is uploaded.  It is then stored as
``pending`` until ``expires_at`` (received time + the profile's late-record window).
This sweep is the one retry path; there is no hook in the ingestion path.  For each
pending record:

* the conversation is found now (one match): the record is applied, its state becomes
  ``applied`` or ``reconciliation``;
* two or more matches: an ``ambiguous_conversation`` item, state ``reconciliation``;
* no match and ``expires_at <= now``: a ``conversation_not_found`` item, state
  ``reconciliation``;
* no match before ``expires_at``: it stays ``pending``, one more attempt is counted.

Discovery runs under the read-only ``crm_reconciliation`` service scope and reads only
ids and the workspace of pending rows.  Each record is then handled in its own
transaction bound to the workspace read from the row, holding the row ``FOR UPDATE SKIP
LOCKED`` and re-checking ``state = 'pending'``, so two sweeps at once never handle one
record twice.  Link and item writes of one conversation are serialised by the service.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from backend.config import Settings, get_settings
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    InboundRecordState,
    IntegrationInboundRecord,
    IntegrationMappingProfile,
)
from backend.database.tenant_context import (
    SERVICE_SCOPE_CRM_RECONCILIATION,
    bind_service_scope,
    bind_tenant_context,
)
from backend.services.crm_mapping_service import route_record
from backend.services.logging_service import logger

# The state a sweep leaves a record in -> its counter.
_COUNTERS = {
    InboundRecordState.APPLIED.value: "applied",
    InboundRecordState.RECONCILIATION.value: "reconciliation",
    InboundRecordState.PENDING.value: "still_pending",
}


@dataclass(frozen=True)
class PendingRef:
    record_id: str
    workspace_id: str


def discover_pending(*, limit: int) -> list[PendingRef]:
    """Pending records across tenants, oldest expiry first (read-only service scope)."""
    with get_session_factory()() as db:
        bind_service_scope(db, SERVICE_SCOPE_CRM_RECONCILIATION)
        rows = db.execute(
            select(IntegrationInboundRecord.id, IntegrationInboundRecord.workspace_id)
            .where(IntegrationInboundRecord.state == InboundRecordState.PENDING.value)
            .order_by(IntegrationInboundRecord.expires_at.asc(), IntegrationInboundRecord.id.asc())
            .limit(limit)
        ).all()
        db.rollback()
    return [PendingRef(record_id=row[0], workspace_id=row[1]) for row in rows]


def reconcile_record(ref: PendingRef, *, now: datetime) -> str:
    """Handle one pending record in its own workspace.  Returns the counter it adds to."""
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=ref.workspace_id)
        record = db.scalar(
            select(IntegrationInboundRecord)
            .where(
                IntegrationInboundRecord.id == ref.record_id,
                IntegrationInboundRecord.workspace_id == ref.workspace_id,
                IntegrationInboundRecord.state == InboundRecordState.PENDING.value,
            )
            .with_for_update(skip_locked=True)
        )
        if record is None:  # another sweep holds it, or it is no longer pending
            db.rollback()
            return "skipped"
        profile = db.get(IntegrationMappingProfile, record.profile_id)
        state = route_record(db, record=record, profile=profile, now=now, sweep=True)
        db.commit()
    return _COUNTERS[state]


def reconcile_pending_records(
    settings: Settings | None = None, *, now: datetime | None = None, limit: int | None = None
) -> dict[str, int]:
    """Retry every pending CRM record (bounded batch).  Returns outcome counts."""
    settings = settings or get_settings()
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    refs = discover_pending(limit=limit or settings.crm_reconciliation_batch_size)
    counts = {
        "scanned": len(refs),
        "applied": 0,
        "reconciliation": 0,
        "still_pending": 0,
        "skipped": 0,
    }
    for ref in refs:
        counts[reconcile_record(ref, now=moment)] += 1
    if refs:
        logger.info("CRM_RECONCILIATION_SWEEP %s", counts)
    return counts


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    from backend.database.relational import configure_database

    configure_database()
    print(json.dumps(reconcile_pending_records()))
