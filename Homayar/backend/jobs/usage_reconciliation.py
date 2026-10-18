"""Usage ledger reconciliation (WP06-T06).

Processing is delivered at least once, so the *effects* on a customer's
allowance must be exactly-once. The database enforces that (unique operation
keys and reservation transitions, one COMMITTED reservation per meeting and
metric, compare-and-set counter updates). This sweeper is the bookkeeping
check behind those guarantees:

* **Orphaned reservations** — a ``RESERVED`` row older than the grace period
  whose meeting is gone or that no queued/running job references any more
  (legacy rows, hand edits, a future bug). They can never settle, so they are
  released (``reason = reconciled_orphan``), which frees the allowance and
  writes a ``released`` ledger row.
* **Counter drift** — ``entitlements.used_value`` / ``reserved_value``
  compared with what the reservation rows imply (sum of committed values, sum
  of still reserved values). Drift is reported (log + audit
  ``usage.reconciled``); it is *corrected* — through an idempotent
  ``adjusted`` ledger row — only when repair is enabled
  (``USAGE_RECONCILIATION_REPAIR_COUNTERS`` or ``--repair``). Historical usage
  and balances are never rewritten silently.

Discovery runs under the read-only ``usage_reconciliation`` service scope
(entitlements only: ids, metric, counters). Each entitlement is then
reconciled in its own transaction bound to the workspace read from the
persisted row, holding the entitlement row ``FOR UPDATE`` so it cannot race a
concurrent reservation or settlement. Repeated runs are inert once clean.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select

from backend.config import Settings, get_settings
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    Entitlement,
    JobState,
    Meeting,
    ProcessingJob,
    ReservationState,
    UsageReservation,
)
from backend.database.tenant_context import (
    SERVICE_SCOPE_USAGE_RECONCILIATION,
    bind_service_scope,
    bind_tenant_context,
)
from backend.jobs.leases import database_now
from backend.services.audit_service import record_audit_event
from backend.services.entitlement_service import adjust_usage, release_usage
from backend.services.logging_service import logger

ORPHAN_REASON = "reconciled_orphan"
COUNTER_REASON = "reconciled_counters"
AUDIT_EVENT = "usage.reconciled"


@dataclass(frozen=True)
class EntitlementRef:
    entitlement_id: str
    workspace_id: str
    metric: str


@dataclass
class ReconciliationOutcome:
    entitlement_id: str
    workspace_id: str
    metric: str
    orphans_released: list[str] = field(default_factory=list)
    observed: tuple[int, int] = (0, 0)  # (used_value, reserved_value) on the row
    expected: tuple[int, int] = (0, 0)  # implied by the reservation rows
    drift: bool = False
    repaired: bool = False
    adjustment_event_id: str | None = None
    found: bool = True

    @property
    def state(self) -> str:
        if not self.found:
            return "not_found"
        if self.drift and not self.repaired:
            return "drift"
        if self.repaired:
            return "repaired"
        if self.orphans_released:
            return "orphans"
        return "clean"


def discover_entitlements(*, limit: int) -> list[EntitlementRef]:
    """Entitlements across tenants (read-only service scope; no content)."""
    with get_session_factory()() as db:
        bind_service_scope(db, SERVICE_SCOPE_USAGE_RECONCILIATION)
        rows = db.execute(
            select(Entitlement.id, Entitlement.workspace_id, Entitlement.metric)
            .order_by(Entitlement.workspace_id.asc(), Entitlement.metric.asc())
            .limit(limit)
        ).all()
        db.rollback()
    return [EntitlementRef(entitlement_id=r[0], workspace_id=r[1], metric=r[2]) for r in rows]


def _live_reservation_ids(db, workspace_id: str) -> set[str]:
    rows = db.execute(
        select(ProcessingJob.reservation_id, ProcessingJob.concurrency_reservation_id).where(
            ProcessingJob.workspace_id == workspace_id,
            ProcessingJob.state.in_((JobState.QUEUED, JobState.RUNNING)),
        )
    ).all()
    return {value for row in rows for value in row if value}


def expected_counters(db, entitlement_id: str) -> tuple[int, int]:
    """(used, reserved) implied by the reservation rows of one entitlement."""
    used = db.scalar(
        select(func.coalesce(func.sum(UsageReservation.committed_value), 0)).where(
            UsageReservation.entitlement_id == entitlement_id,
            UsageReservation.state == ReservationState.COMMITTED,
        )
    )
    reserved = db.scalar(
        select(func.coalesce(func.sum(UsageReservation.reserved_value), 0)).where(
            UsageReservation.entitlement_id == entitlement_id,
            UsageReservation.state == ReservationState.RESERVED,
        )
    )
    return int(used or 0), int(reserved or 0)


def reconcile_entitlement(
    ref: EntitlementRef,
    settings: Settings,
    *,
    repair_counters: bool | None = None,
) -> ReconciliationOutcome:
    """Release orphaned reservations and check (optionally repair) the counters."""
    repair = (
        settings.usage_reconciliation_repair_counters
        if repair_counters is None
        else repair_counters
    )
    grace = timedelta(seconds=settings.usage_reconciliation_grace_seconds)
    outcome = ReconciliationOutcome(ref.entitlement_id, ref.workspace_id, ref.metric)
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=ref.workspace_id)
        entitlement = db.scalar(
            select(Entitlement)
            .where(
                Entitlement.id == ref.entitlement_id,
                Entitlement.workspace_id == ref.workspace_id,
            )
            .with_for_update()
        )
        if entitlement is None:
            db.rollback()
            outcome.found = False
            return outcome
        now = database_now(db)
        live = _live_reservation_ids(db, ref.workspace_id)
        stale = db.scalars(
            select(UsageReservation)
            .where(
                UsageReservation.entitlement_id == entitlement.id,
                UsageReservation.state == ReservationState.RESERVED,
                UsageReservation.created_at <= now - grace,
            )
            .order_by(UsageReservation.created_at.asc(), UsageReservation.id.asc())
        ).all()
        for reservation in stale:
            meeting = db.get(Meeting, reservation.meeting_id)
            alive = meeting is not None and meeting.deleted_at is None and reservation.id in live
            if alive:
                continue
            release_usage(db, reservation.id, reason=ORPHAN_REASON)
            outcome.orphans_released.append(reservation.id)
        db.flush()
        db.refresh(entitlement)

        outcome.expected = expected_counters(db, entitlement.id)
        outcome.observed = (entitlement.used_value, entitlement.reserved_value)
        outcome.drift = outcome.observed != outcome.expected
        if outcome.drift and repair:
            observed, expected = outcome.observed, outcome.expected
            key = (
                f"reconcile:{entitlement.id}:"
                f"{observed[0]}/{observed[1]}->{expected[0]}/{expected[1]}"
            )
            event = adjust_usage(
                db,
                entitlement_id=entitlement.id,
                used_delta=expected[0] - observed[0],
                reserved_delta=expected[1] - observed[1],
                reason=COUNTER_REASON,
                operation_key=key,
                details={
                    "observed_used": observed[0],
                    "observed_reserved": observed[1],
                    "expected_used": expected[0],
                    "expected_reserved": expected[1],
                },
            )
            outcome.repaired = event is not None
            outcome.adjustment_event_id = event.id if event is not None else None

        if outcome.orphans_released or outcome.drift:
            record_audit_event(
                db,
                event_type=AUDIT_EVENT,
                workspace_id=ref.workspace_id,
                target_type="entitlement",
                target_id=entitlement.id,
                outcome="success" if (not outcome.drift or outcome.repaired) else "failure",
                details={
                    "metric": ref.metric,
                    "orphans_released": len(outcome.orphans_released),
                    "observed_used": outcome.observed[0],
                    "observed_reserved": outcome.observed[1],
                    "expected_used": outcome.expected[0],
                    "expected_reserved": outcome.expected[1],
                    "drift": outcome.drift,
                    "repaired": outcome.repaired,
                },
                settings=settings,
            )
            db.commit()
        else:
            db.rollback()

    if outcome.drift and not outcome.repaired:
        logger.error(
            "USAGE_COUNTER_DRIFT workspace_id=%s metric=%s observed=%s expected=%s",
            ref.workspace_id,
            ref.metric,
            outcome.observed,
            outcome.expected,
        )
    elif outcome.orphans_released or outcome.repaired:
        logger.warning(
            "USAGE_RECONCILED workspace_id=%s metric=%s orphans_released=%s repaired=%s",
            ref.workspace_id,
            ref.metric,
            len(outcome.orphans_released),
            outcome.repaired,
        )
    return outcome


def reconcile_usage(
    settings: Settings | None = None,
    *,
    limit: int | None = None,
    repair_counters: bool | None = None,
) -> dict[str, int]:
    """Reconcile every entitlement (bounded batch). Returns outcome counts."""
    settings = settings or get_settings()
    batch = limit or settings.usage_reconciliation_batch_size
    refs = discover_entitlements(limit=batch)
    counts: dict[str, int] = {"scanned": len(refs), "orphans_released": 0}
    for ref in refs:
        outcome = reconcile_entitlement(ref, settings, repair_counters=repair_counters)
        counts[outcome.state] = counts.get(outcome.state, 0) + 1
        counts["orphans_released"] += len(outcome.orphans_released)
    if refs:
        logger.info("USAGE_RECONCILIATION_SWEEP %s", counts)
    return counts


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    from backend.database.relational import configure_database

    configure_database()
    summary = reconcile_usage(repair_counters=("--repair" in sys.argv[1:]) or None)
    print(json.dumps(summary))
    sys.exit(1 if summary.get("drift") else 0)
