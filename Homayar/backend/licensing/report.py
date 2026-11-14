"""The operator view of the offline licence with the workspace counters (SPEC 2.7)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.saas_models import Entitlement
from backend.licensing.offline_license import LicenseStatus, format_zulu
from backend.schemas.offline_license import LicenseCounter, OfflineLicenseResponse


def _remaining(row: Entitlement, license_limit: int | None) -> int | None:
    limits = [value for value in (row.limit_value, license_limit) if value is not None]
    if not limits:
        return None
    return max(0, min(limits) - row.used_value - row.reserved_value)


def license_report(
    db: Session, *, workspace_id: str, status: LicenseStatus
) -> OfflineLicenseResponse:
    """The licence status plus one counter per entitlement row of the caller's
    workspace, read under the request's tenant context (limits are per workspace)."""
    rows = db.scalars(
        select(Entitlement)
        .where(Entitlement.workspace_id == workspace_id)
        .order_by(Entitlement.metric)
    ).all()
    counters = [
        LicenseCounter(
            metric=row.metric,
            limit=row.limit_value,
            license_limit=status.limits.get(row.metric),
            used=row.used_value,
            reserved=row.reserved_value,
            remaining=_remaining(row, status.limits.get(row.metric)),
        )
        for row in rows
    ]
    return OfflineLicenseResponse(
        enforced=status.enforced,
        state=status.state,
        reason=status.reason,
        processing_allowed=status.processing_allowed,
        license_id=status.license_id,
        customer_id=status.customer_id,
        customer_name=status.customer_name,
        issued_at=format_zulu(status.issued_at),
        expires_at=format_zulu(status.expires_at),
        grace_until=format_zulu(status.grace_until),
        days_left=status.days_left,
        modules=list(status.modules),
        unknown_modules=list(status.unknown_modules),
        limits=dict(status.limits),
        unknown_limits=list(status.unknown_limits),
        key_fingerprint=status.key_fingerprint,
        warnings=list(status.warnings),
        counters=counters,
    )
