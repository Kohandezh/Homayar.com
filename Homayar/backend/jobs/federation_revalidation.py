"""Periodic revalidation of federated identities (HOM-128, ADR 0066).

The sweep asks the configured checker about every unrevoked OIDC identity
whose last IdP confirmation is older than
``federation_revalidation_max_age_minutes``, oldest first, at most
``limit`` per pass.  Each identity is handled in its own transaction, so no
row locks are held across another identity's live IdP call and one failing
identity never stops the others.  A "gone" answer revokes the user's
sessions and federated memberships of that organization; "unavailable"
changes nothing and the identity is picked up again by the next pass.

Without beat, run one pass with ``python -m backend.jobs.federation_revalidation``.
With no checker configured (until HOM-127) the task does nothing and logs once.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from backend.celery_app import FEDERATION_REVALIDATION_TASK, celery_app
from backend.config import Settings, get_settings
from backend.database.relational import get_session_factory
from backend.services.federation_service import (
    get_federated_identity_checker,
    revalidate_stale_federated_identities,
)
from backend.services.logging_service import logger

DEFAULT_LIMIT = 200


def run_federation_revalidation(
    settings: Settings | None = None, *, now: datetime | None = None, limit: int | None = None
) -> dict[str, int]:
    """One bounded pass; each identity is committed in its own transaction."""
    settings = settings or get_settings()
    checker = get_federated_identity_checker()
    if checker is None:
        logger.info("FEDERATION_REVALIDATION_SKIPPED reason=no_checker")
        return {"active": 0, "gone": 0, "unavailable": 0}
    moment = now or datetime.now(UTC)
    with get_session_factory()() as db:
        counts = revalidate_stale_federated_identities(
            db,
            checker=checker,
            now=moment,
            settings=settings,
            limit=limit or DEFAULT_LIMIT,
        )
    if any(counts.values()):
        logger.info("FEDERATION_REVALIDATION_SWEEP %s", counts)
    return counts


@celery_app.task(name=FEDERATION_REVALIDATION_TASK, acks_late=False, ignore_result=True)
def revalidate_federated_identities_task() -> dict[str, int]:
    """Periodic: reconfirm stale federated identities (HOM-128)."""
    return run_federation_revalidation()


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    from backend.database.relational import configure_database

    configure_database()
    print(json.dumps(run_federation_revalidation()))
