"""Periodic voice recovery (P0 wave, X21): lapsed media nodes and expired slots.

Runs as a Celery beat task so a lost node's calls end and its capacity returns
even when no operator calls ``POST /voice/recover``.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from backend.config import Settings, get_settings
from backend.database.relational import get_session_factory
from backend.routers.v1.voice import get_voice_control_plane

logger = logging.getLogger(__name__)


def sweep_voice_recovery(settings: Settings | None = None) -> dict[str, Any]:
    plane = get_voice_control_plane(settings or get_settings())
    with get_session_factory()() as db:
        report = plane.recover(db)
        db.commit()
    if report["lost_nodes"] or any(report["failed_calls"].values()):
        logger.warning("VOICE_RECOVERY %s", json.dumps(report))
    return report


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    from backend.database.relational import configure_database

    configure_database()
    print(json.dumps(sweep_voice_recovery()))
    sys.exit(0)
