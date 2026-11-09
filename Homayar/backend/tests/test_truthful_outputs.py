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

# Files that may import ``random``, each with its reason. Anything else that draws
# random numbers must use ``secrets``.
RANDOM_ALLOWLIST = {
    # development-only data seeding for the legacy graph demo; never imported by the app
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


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _is_random_name(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "random"


def _first_arg(call: ast.Call, keyword: str) -> ast.expr | None:
    if call.args:
        return call.args[0]
    return next((k.value for k in call.keywords if k.arg == keyword), None)


def _annotation_nodes(tree: ast.AST) -> set[int]:
    roots: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            a = node.args
            params = [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]
            roots.extend(p.annotation for p in params if p is not None and p.annotation)
            if node.returns is not None:
                roots.append(node.returns)
    return {id(n) for root in roots for n in ast.walk(root)}


def seeded_random_violations(source: str) -> list[str]:
    """Uses of ``random`` in ``source`` other than a seeded ``random.Random(seed)``.

    Allowed: ``random.Random(x)`` called with exactly one positional argument that is not
    None and no keywords, and ``random.Random`` inside a type annotation.  Refused: every
    other use of the module, ``<x>.seed()`` or ``<x>.seed(None)`` (re-seeds from the OS),
    and reaching the module by its string name.
    """
    tree = ast.parse(source)
    in_annotation = _annotation_nodes(tree)
    seeded = {
        id(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and len(node.args) == 1
        and not node.keywords
        and not isinstance(node.args[0], ast.Starred)
        and not _is_none(node.args[0])
    }
    ok_names = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "Random"
        and _is_random_name(node.value)
        and (id(node) in seeded or id(node) in in_annotation)
    }
    found = []
    for node in ast.walk(tree):
        line = f"line {getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.ImportFrom) and node.module == "random":
            found.append(f"{line}: from random import")
        elif isinstance(node, ast.Import):
            found.extend(
                f"{line}: import random as {alias.asname}"
                for alias in node.names
                if alias.name == "random" and alias.asname
            )
        elif _is_random_name(node) and id(node) not in ok_names:
            found.append(f"{line}: random used other than a seeded random.Random(seed)")
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "seed" and isinstance(func, ast.Attribute):
                arg = _first_arg(node, "a")
                if arg is None or _is_none(arg):
                    found.append(f"{line}: .seed() without a seed re-seeds from the OS")
            elif name in ("import_module", "__import__"):
                arg = _first_arg(node, "name")
                if isinstance(arg, ast.Constant) and arg.value == "random":
                    found.append(f"{line}: random imported by its string name")
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "modules"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "sys"
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "random"
        ):
            found.append(f"{line}: random reached through sys.modules")
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
        ("import random\nrandom.Random().random()\n", False),
        ("import random\nrandom.Random(None).random()\n", False),
        ("import random\nR = random.Random\nR().random()\n", False),
        ("import random\nrng = random.Random(1)\nrng.seed()\n", False),
        ('import importlib\nimportlib.import_module("random")\n', False),
        ('__import__("random")\n', False),
        ('import sys\nsys.modules["random"]\n', False),
        ('import random\nrandom.Random("s").randrange(3)\n', True),
        ("import random\ndef f(rng: random.Random) -> None: ...\n", True),
    ],
    ids=[
        "random-random",
        "random-choice",
        "random-seed",
        "random-shuffle",
        "from-random-import",
        "import-random-as",
        "random-module-passed-on",
        "unseeded-Random",
        "Random-None",
        "Random-alias",
        "instance-reseed",
        "import-module",
        "dunder-import",
        "sys-modules",
        "explicit-random-Random",
        "Random-annotation",
    ],
)
def test_seeded_random_check(source: str, allowed: bool) -> None:
    assert (seeded_random_violations(source) == []) is allowed


def test_candidate_report_uses_only_seeded_random() -> None:
    source = (BACKEND_ROOT / "evaluation" / "candidate.py").read_text(encoding="utf-8")
    assert "random.Random(" in source
    assert seeded_random_violations(source) == []
