"""No fabricated analytics may exist on any importable backend path (WP04-T04/T05).

The legacy speech and analysis services alternated speakers on a fixed clock
and returned random quality scores when a model was unavailable. They were
removed together with the routes and tasks that called them; this test keeps
them from coming back.
"""

from __future__ import annotations

import ast
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
RANDOM_ALLOWLIST = {
    "database/seed.py",
    # seeded random.Random for bootstrap resampling only; no output value is random
    "evaluation/candidate.py",
}
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


def seeded_random_violations(source: str) -> list[str]:
    """Uses of ``random`` in ``source`` other than an explicit ``random.Random(...)``."""
    tree = ast.parse(source)
    allowed = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "Random"
        and isinstance(node.value, ast.Name)
    }
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "random":
            found.append(f"line {node.lineno}: from random import")
        elif isinstance(node, ast.Import):
            found.extend(
                f"line {node.lineno}: import random as {alias.asname}"
                for alias in node.names
                if alias.name == "random" and alias.asname
            )
        elif isinstance(node, ast.Name) and node.id == "random" and id(node) not in allowed:
            found.append(f"line {node.lineno}: random used other than random.Random")
    return found


@pytest.mark.parametrize(
    ("source", "allowed"),
    [
        ("import random\nrandom.random()\n", False),
        ("import random\nrandom.choice(x)\n", False),
        ("import random\nrandom.seed(1)\n", False),
        ("import random\nrandom.shuffle(x)\n", False),
        ("from random import choice\n", False),
        ("import random as r\nr.Random(1)\n", False),
        ("import random\nf(random)\n", False),
        ('import random\nrandom.Random("s").randrange(3)\n', True),
    ],
    ids=[
        "random-random",
        "random-choice",
        "random-seed",
        "random-shuffle",
        "from-random-import",
        "import-random-as",
        "random-module-passed-on",
        "explicit-random-Random",
    ],
)
def test_seeded_random_check(source: str, allowed: bool) -> None:
    assert (seeded_random_violations(source) == []) is allowed


def test_candidate_report_uses_only_seeded_random() -> None:
    source = (BACKEND_ROOT / "evaluation" / "candidate.py").read_text(encoding="utf-8")
    assert "random.Random(" in source
    assert seeded_random_violations(source) == []
