"""No fabricated analytics may exist on any importable backend path (WP04-T04/T05).

The legacy speech and analysis services alternated speakers on a fixed clock
and returned random quality scores when a model was unavailable. They were
removed together with the routes and tasks that called them; this test keeps
them from coming back.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]

REMOVED_MODULES = (
    "backend.services.stt_service",
    "backend.services.analysis_service",
    "backend.routers.calls",
    "backend.celery_app.tasks",
    # Legacy authentication stack (WP03-T01): the v1 contract in
    # backend/security and backend/services/auth_service is the only one.
    "backend.auth",
    "backend.middleware.auth",
    "backend.routers.auth",
    # Unmounted legacy stack (N11 / F01): Neo4j dashboard, graph, Copilot,
    # LDAP sync and VoIP webhook routers, the services only they used, the
    # legacy Pydantic models, the WebSocket stub and the Copilot prompt loader.
    # Inventory: Homayar/docs/architecture/legacy-retirement.md.
    "backend.routers.copilot",
    "backend.routers.dashboard",
    "backend.routers.graph",
    "backend.routers.ldap_sync",
    "backend.routers.webhook",
    "backend.services.copilot_service",
    "backend.services.dashboard_service",
    "backend.services.graph_service",
    "backend.services.issue_aggregation_service",
    "backend.services.call_service",
    "backend.services.ad_service",
    "backend.services.user_service",
    "backend.services.pdf_service",
    "backend.services.redis_service",
    "backend.services.llm_router",
    "backend.database.models",
    "backend.websocket",
    "backend.websocket.manager",
    "backend.prompts",
)

# Development-only data seeding for the legacy graph demo; never imported by the
# application. Anything else that draws random numbers must use ``secrets``.
RANDOM_ALLOWLIST = {"database/seed.py"}
RANDOM_PATTERN = re.compile(r"^\s*(import random\b|from random import)", re.MULTILINE)


@pytest.mark.parametrize("module", REMOVED_MODULES)
def test_fabricated_analytics_modules_are_gone(module: str) -> None:
    try:
        spec = importlib.util.find_spec(module)
    except ModuleNotFoundError:  # parent package removed as well
        spec = None
    assert spec is None, f"{module} must not be importable"


def test_no_random_numbers_outside_the_allowlist() -> None:
    offenders = []
    for path in BACKEND_ROOT.rglob("*.py"):
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        if relative.startswith(("tests/", ".venv/")) or "/.venv/" in relative:
            continue
        if relative in RANDOM_ALLOWLIST:
            continue
        if RANDOM_PATTERN.search(path.read_text(encoding="utf-8")):
            offenders.append(relative)
    assert offenders == [], f"random-number use outside the allowlist: {offenders}"
