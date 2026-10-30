"""Usage allowance: reservations, settlements, releases and audited adjustments.

Lock order (HOM-333). Every transaction that changes usage rows locks an
``entitlements`` row first and the ``usage_reservations`` rows of that entitlement
after it. It never waits for an entitlement row while it holds a reservation row of
that entitlement. Across entitlements, the order in "Several entitlements in one
transaction" below applies. So two such transactions cannot wait for each other,
and PostgreSQL never has to abort one as a deadlock (SQLSTATE ``40P01``).

* ``reserve_usage`` takes the entitlement row with its counter update, then
  inserts the new reservation row.
* ``commit_usage`` and ``release_usage`` read the reservation's ``entitlement_id``
  without a lock (it never changes), lock that entitlement row (``FOR NO KEY
  UPDATE``, the lock their counter update takes anyway), then lock the
  reservation row (``FOR UPDATE``).
* ``adjust_usage`` locks only the entitlement row.
* ``reconcile_entitlement`` (``backend.jobs.usage_reconciliation``) locks the
  entitlement row ``FOR UPDATE``, then reads its stale reservations, releases the
  orphans through ``release_usage`` and corrects the counters through
  ``adjust_usage``, all under that lock.

Decide on fresh rows. After the locks, the state check and the amounts read the
locked rows as they are now (``populate_existing``). A copy that the same session
loaded earlier (``db.get`` in ``release_job_reservations`` or in the job
completion, the reconciliation's stale list) can be out of date after a lock
wait, so no decision uses it.

Several entitlements in one transaction. The reservations of one job are always
taken in this order: the audio reservation (``job.reservation_id``, metric
``audio_seconds``), then the concurrency reservation
(``job.concurrency_reservation_id``, metric ``concurrent_jobs``). The upload and
the replay reserve in this order; the job completion (``commit_usage``, then
``release_usage``) and ``release_job_reservations`` settle and release in it.

Relative to the job, meeting and media rows, these rows come after the meeting row
and before the media rows (``backend/media/README.md``, "Lock order").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    Entitlement,
    ReservationState,
    UsageEvent,
    UsageReservation,
)


class QuotaExceededError(ValueError):
    def __init__(
        self,
        *,
        limit: int | None,
        used: int,
        reserved: int,
        requested: int,
        metric: str = "audio_seconds",
    ):
        self.metric = metric
        self.limit = limit
        self.used = used
        self.reserved = reserved
        self.requested = requested
        remaining = None if limit is None else max(0, limit - used - reserved)
        self.remaining = remaining
        super().__init__("Processing quota exceeded")


class ReservationStateError(ValueError):
    pass


# Usage ledger (WP06-T06): one ``usage_events`` row per state transition of a
# reservation — ``reserved`` when allowance is taken, ``committed`` when the
# one billable settlement happens, ``released`` when it is given back — plus
# ``adjusted`` rows written only by reconciliation. Every row carries a
# deterministic ``operation_key`` (``<reservation id>:<event type>``, or a
# reconciliation identity), unique in the database, and the pair
# (reservation, event type) is unique too; a redelivered, retried or replayed
# transition therefore cannot be recorded twice, and ``used_value`` /
# ``reserved_value`` can be recomputed from the ledger and the reservation rows.
EVENT_RESERVED = "reserved"
EVENT_COMMITTED = "committed"
EVENT_RELEASED = "released"
EVENT_ADJUSTED = "adjusted"


def operation_key_for(reservation_id: str, event_type: str) -> str:
    """Stable business-operation identity of one reservation transition."""
    return f"{reservation_id}:{event_type}"


def _record_reservation_event(
    db: Session,
    reservation: UsageReservation,
    entitlement: Entitlement,
    *,
    event_type: str,
    value: int,
    reason: str | None = None,
    provider_metadata: dict | None = None,
) -> UsageEvent:
    event = UsageEvent(
        workspace_id=reservation.workspace_id,
        reservation_id=reservation.id,
        operation_key=operation_key_for(reservation.id, event_type),
        metric=entitlement.metric,
        value=value,
        event_type=event_type,
        subject_type="meeting",
        subject_id=reservation.meeting_id,
        reason=(reason or None) and reason[:160],
        provider_metadata=provider_metadata or {},
    )
    db.add(event)
    return event


@dataclass(frozen=True)
class UsageSnapshot:
    metric: str
    limit: int | None
    used: int
    reserved: int

    @property
    def remaining(self) -> int | None:
        if self.limit is None:
            return None
        return max(0, self.limit - self.used - self.reserved)


def get_usage_snapshot(
    db: Session, workspace_id: str, metric: str = "audio_seconds"
) -> UsageSnapshot:
    entitlement = db.scalar(
        select(Entitlement).where(
            Entitlement.workspace_id == workspace_id,
            Entitlement.metric == metric,
        )
    )
    if entitlement is None:
        raise QuotaExceededError(
            limit=0,
            used=0,
            reserved=0,
            requested=0,
            metric=metric,
        )
    return UsageSnapshot(
        metric=metric,
        limit=entitlement.limit_value,
        used=entitlement.used_value,
        reserved=entitlement.reserved_value,
    )


def reserve_usage(
    db: Session,
    *,
    workspace_id: str,
    meeting_id: str,
    amount: int,
    idempotency_key: str,
    metric: str = "audio_seconds",
) -> UsageReservation:
    if amount <= 0:
        raise ValueError("Reservation amount must be positive")
    existing = db.scalar(
        select(UsageReservation).where(UsageReservation.idempotency_key == idempotency_key)
    )
    if existing is not None:
        existing_entitlement = db.get(Entitlement, existing.entitlement_id)
        if (
            existing.workspace_id != workspace_id
            or existing.meeting_id != meeting_id
            or existing.reserved_value != amount
            or existing_entitlement is None
            or existing_entitlement.metric != metric
        ):
            raise ReservationStateError(
                "Idempotency key was reused with different parameters"
            )
        return existing

    entitlement = db.scalar(
        select(Entitlement).where(
            Entitlement.workspace_id == workspace_id,
            Entitlement.metric == metric,
        )
    )
    if entitlement is None:
        raise QuotaExceededError(
            limit=0,
            used=0,
            reserved=0,
            requested=amount,
            metric=metric,
        )

    reservation: UsageReservation
    try:
        with db.begin_nested():
            updated_id = db.scalar(
                update(Entitlement)
                .where(
                    Entitlement.id == entitlement.id,
                    or_(
                        Entitlement.limit_value.is_(None),
                        Entitlement.used_value + Entitlement.reserved_value + amount
                        <= Entitlement.limit_value,
                    ),
                )
                .values(reserved_value=Entitlement.reserved_value + amount)
                .returning(Entitlement.id)
            )
            if updated_id is None:
                db.refresh(entitlement)
                raise QuotaExceededError(
                    limit=entitlement.limit_value,
                    used=entitlement.used_value,
                    reserved=entitlement.reserved_value,
                    requested=amount,
                    metric=metric,
                )

            reservation = UsageReservation(
                workspace_id=workspace_id,
                entitlement_id=entitlement.id,
                meeting_id=meeting_id,
                idempotency_key=idempotency_key,
                reserved_value=amount,
                state=ReservationState.RESERVED,
            )
            db.add(reservation)
            db.flush()
            _record_reservation_event(
                db, reservation, entitlement, event_type=EVENT_RESERVED, value=amount
            )
            db.flush()
    except IntegrityError:
        existing = db.scalar(
            select(UsageReservation).where(UsageReservation.idempotency_key == idempotency_key)
        )
        if existing is None:
            raise
        existing_entitlement = db.get(Entitlement, existing.entitlement_id)
        if (
            existing.workspace_id != workspace_id
            or existing.meeting_id != meeting_id
            or existing.reserved_value != amount
            or existing_entitlement is None
            or existing_entitlement.metric != metric
        ):
            raise ReservationStateError(
                "Idempotency key was reused with different parameters"
            ) from None
        return existing
    return reservation


def _lock_reservation(db: Session, reservation_id: str) -> UsageReservation | None:
    """The reservation, locked after its entitlement row and read fresh.

    See "Lock order (HOM-333)" in the module docstring. ``populate_existing``
    replaces a copy this session loaded before, so the caller decides on the row
    as it is under the lock.
    """
    entitlement_id = db.scalar(
        select(UsageReservation.entitlement_id).where(UsageReservation.id == reservation_id)
    )
    if entitlement_id is not None:
        db.scalar(
            select(Entitlement.id)
            .where(Entitlement.id == entitlement_id)
            .with_for_update(key_share=True)
        )
    return db.scalar(
        select(UsageReservation)
        .where(UsageReservation.id == reservation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def commit_usage(
    db: Session,
    reservation_id: str,
    *,
    actual_amount: int | None = None,
    provider_metadata: dict | None = None,
) -> UsageReservation:
    reservation = _lock_reservation(db, reservation_id)
    if reservation is None:
        raise ReservationStateError("Reservation not found")
    if reservation.state == ReservationState.COMMITTED:
        return reservation
    if reservation.state != ReservationState.RESERVED:
        raise ReservationStateError("Released reservation cannot be committed")

    actual = reservation.reserved_value if actual_amount is None else actual_amount
    if actual < 0 or actual > reservation.reserved_value:
        raise ReservationStateError("Committed usage must be within the reserved amount")

    entitlement = db.get(Entitlement, reservation.entitlement_id)
    if entitlement is None:
        raise ReservationStateError("Entitlement not found")

    updated_id = db.scalar(
        update(Entitlement)
        .where(
            Entitlement.id == reservation.entitlement_id,
            Entitlement.reserved_value >= reservation.reserved_value,
        )
        .values(
            reserved_value=Entitlement.reserved_value - reservation.reserved_value,
            used_value=Entitlement.used_value + actual,
        )
        .returning(Entitlement.id)
    )
    if updated_id is None:
        raise ReservationStateError("Entitlement counters are inconsistent")

    reservation.state = ReservationState.COMMITTED
    reservation.committed_value = actual
    reservation.finalized_at = datetime.now(UTC)
    # The one billable settlement of this reservation. The row locks above
    # serialise concurrent callers; the unique operation key and the partial
    # unique index "one COMMITTED reservation per meeting and metric" are the
    # database backstops against a second settlement of the same work.
    _record_reservation_event(
        db,
        reservation,
        entitlement,
        event_type=EVENT_COMMITTED,
        value=actual,
        provider_metadata=provider_metadata,
    )
    db.flush()
    return reservation


def release_usage(
    db: Session,
    reservation_id: str,
    *,
    reason: str,
) -> UsageReservation:
    reservation = _lock_reservation(db, reservation_id)
    if reservation is None:
        raise ReservationStateError("Reservation not found")
    if reservation.state == ReservationState.RELEASED:
        return reservation
    if reservation.state == ReservationState.COMMITTED:
        raise ReservationStateError("Committed reservation cannot be released")

    updated_id = db.scalar(
        update(Entitlement)
        .where(
            Entitlement.id == reservation.entitlement_id,
            Entitlement.reserved_value >= reservation.reserved_value,
        )
        .values(reserved_value=Entitlement.reserved_value - reservation.reserved_value)
        .returning(Entitlement.id)
    )
    if updated_id is None:
        raise ReservationStateError("Entitlement counters are inconsistent")
    reservation.state = ReservationState.RELEASED
    reservation.failure_reason = reason[:160]
    reservation.finalized_at = datetime.now(UTC)
    entitlement = db.get(Entitlement, reservation.entitlement_id)
    if entitlement is None:
        raise ReservationStateError("Entitlement not found")
    _record_reservation_event(
        db,
        reservation,
        entitlement,
        event_type=EVENT_RELEASED,
        value=reservation.reserved_value,
        reason=reason,
    )
    db.flush()
    return reservation


def adjust_usage(
    db: Session,
    *,
    entitlement_id: str,
    used_delta: int,
    reserved_delta: int,
    reason: str,
    operation_key: str,
    details: dict | None = None,
) -> UsageEvent | None:
    """Correct entitlement counters by an audited, idempotent ledger adjustment.

    Only reconciliation (``backend.jobs.usage_reconciliation``) calls this,
    with an ``operation_key`` derived from the observed and expected counters,
    so applying the same correction twice is a no-op (returns ``None``); the
    unique key is the database backstop. Never used for customer charges.
    """
    if used_delta == 0 and reserved_delta == 0:
        return None
    existing = db.scalar(select(UsageEvent.id).where(UsageEvent.operation_key == operation_key))
    if existing is not None:
        return None
    entitlement = db.scalar(
        select(Entitlement).where(Entitlement.id == entitlement_id).with_for_update()
    )
    if entitlement is None:
        raise ReservationStateError("Entitlement not found")
    updated_id = db.scalar(
        update(Entitlement)
        .where(
            Entitlement.id == entitlement.id,
            Entitlement.used_value + used_delta >= 0,
            Entitlement.reserved_value + reserved_delta >= 0,
        )
        .values(
            used_value=Entitlement.used_value + used_delta,
            reserved_value=Entitlement.reserved_value + reserved_delta,
        )
        .returning(Entitlement.id)
    )
    if updated_id is None:
        raise ReservationStateError("Adjustment would make entitlement counters negative")
    event = UsageEvent(
        workspace_id=entitlement.workspace_id,
        reservation_id=None,
        operation_key=operation_key,
        metric=entitlement.metric,
        value=abs(used_delta),
        event_type=EVENT_ADJUSTED,
        subject_type="entitlement",
        subject_id=entitlement.id,
        reason=reason[:160],
        provider_metadata={
            "used_delta": used_delta,
            "reserved_delta": reserved_delta,
            **(details or {}),
        },
    )
    db.add(event)
    db.flush()
    db.refresh(entitlement)
    return event
