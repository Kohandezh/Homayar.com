from __future__ import annotations

import ast
import os
import random
import re
import secrets
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.tests import test_truthful_outputs as guard
from backend.voice import admission
from backend.voice.admission import AdmissionPolicy, VoiceControlPlane
from backend.voice.loadtest import seed_workspaces, sqlite_session_factory
from backend.voice.metrics import VoiceMetrics
from backend.voice.models import VoiceCapacitySlot

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ADMISSION_PATH = BACKEND_ROOT / "voice" / "admission.py"
GUARD_PATH = BACKEND_ROOT / "tests" / "test_truthful_outputs.py"

BASE_ALLOWLIST = {"database/seed.py"}
BASE_PATTERN = r"^\s*(import random\b|from random import)"
BASE_ALLOWLIST_SOURCE = 'RANDOM_ALLOWLIST = {"database/seed.py"}'
BASE_PATTERN_SOURCE = (
    r'RANDOM_PATTERN = re.compile(r"^\s*(import random\b|from random import)", re.MULTILINE)'
)

MAX_DELAY = 0.075

FORBIDDEN_MODULES = ("random", "secrets", "numpy.random", "uuid")
FORBIDDEN_NAMES = frozenset(
    {
        "random",
        "urandom",
        "SystemRandom",
        "getrandbits",
        "randbelow",
        "randbits",
        "token_bytes",
        "token_hex",
        "token_urlsafe",
        "uuid1",
        "uuid4",
    }
)

RANDOMNESS_CALLS = (
    (random, "random"),
    (random, "uniform"),
    (random, "randint"),
    (random, "randrange"),
    (random, "getrandbits"),
    (random, "choice"),
    (random, "triangular"),
    (random, "gauss"),
    (random.Random, "random"),
    (random.SystemRandom, "random"),
    (random.SystemRandom, "getrandbits"),
    (secrets, "randbelow"),
    (secrets, "randbits"),
    (secrets, "choice"),
    (secrets, "token_bytes"),
    (secrets, "token_hex"),
    (os, "urandom"),
    (uuid, "uuid1"),
    (uuid, "uuid4"),
)

CONTENTION_MESSAGES = (
    "database is locked",
    "database table is locked",
    "deadlock detected",
    "could not serialize access due to concurrent update",
    "DATABASE IS LOCKED",
    "Deadlock Detected",
    "deadlock detected\nDETAIL:  Process 4242 waits for ShareLock on transaction 99; "
    "blocked by process 4343.",
)

NON_CONTENTION_MESSAGES = (
    "server closed the connection unexpectedly",
    "could not connect to server: Connection refused",
    "no such table: voice_media_nodes",
    None,
)

BUDGETS = (0, 1, 7)


def _operational(message: str | None) -> OperationalError:
    return OperationalError("SELECT 1", {}, None if message is None else Exception(message))


def _integrity() -> IntegrityError:
    return IntegrityError(
        "INSERT INTO voice_admission_locks", {}, Exception("UNIQUE constraint failed")
    )


def _plane(**policy) -> VoiceControlPlane:
    return VoiceControlPlane(AdmissionPolicy(**policy), metrics=VoiceMetrics())


def _work(failures: list[BaseException], sleeps: list[float]):
    pending = list(failures)
    seen: list[int] = []

    def work() -> str:
        seen.append(len(sleeps))
        if pending:
            raise pending.pop(0)
        return "done"

    return work, seen


def _assert_backoff(sleeps: list[float], count: int) -> None:
    assert len(sleeps) == count, sleeps
    for delay in sleeps:
        assert isinstance(delay, int | float) and not isinstance(delay, bool), delay
        assert 0 < delay <= MAX_DELAY, delay


def _randomness_findings(source: str) -> list[str]:
    findings: list[str] = []
    for node in ast.walk(ast.parse(source)):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            modules = [base, *(f"{base}.{alias.name}".lstrip(".") for alias in node.names)]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            modules = [node.value]
        for module in modules:
            if any(module == name or module.startswith(f"{name}.") for name in FORBIDDEN_MODULES):
                findings.append(f"module {module}")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            findings.append(f"name {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            findings.append(f"attribute {node.attr}")
        elif isinstance(node, ast.alias):
            bound = node.asname or node.name.split(".")[-1]
            if node.name.split(".")[-1] in FORBIDDEN_NAMES or bound in FORBIDDEN_NAMES:
                findings.append(f"alias {node.name}")
    return findings


def _production_modules_importing_random() -> list[str]:
    pattern = re.compile(BASE_PATTERN, re.MULTILINE)
    offenders = []
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        if relative.startswith(("tests/", ".venv/")) or "/.venv/" in relative:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(relative)
    return offenders


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


CLEAN_TREE = {
    "database/seed.py": "import random\n",
    "voice/admission.py": "import threading\nimport time\n",
    "tests/test_anything.py": "import random\n",
}


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []
    original = time.sleep

    def record(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(time, "sleep", record)
    for name, value in list(vars(admission).items()):
        if value is original:
            monkeypatch.setattr(admission, name, record)
    return delays


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    with Session(engine) as db:
        yield db
    engine.dispose()


@pytest.fixture
def voice_db(tmp_path: Path):
    factory = sqlite_session_factory(tmp_path / "voice.db", pool_size=5)
    workspace_id = seed_workspaces(factory, 1)[0]
    yield factory, workspace_id
    factory.kw["bind"].dispose()


def _failing_commits(
    monkeypatch: pytest.MonkeyPatch, db: Session, failures: list[BaseException]
) -> list[int]:
    pending = list(failures)
    attempts: list[int] = []
    real_commit = db.commit

    def commit() -> None:
        attempts.append(len(attempts) + 1)
        if pending:
            raise pending.pop(0)
        real_commit()

    monkeypatch.setattr(db, "commit", commit)
    return attempts


def _held(factory) -> int:
    with factory() as db:
        return db.execute(
            select(func.count(VoiceCapacitySlot.id)).where(VoiceCapacitySlot.released_at.is_(None))
        ).scalar_one()


def _admit(plane: VoiceControlPlane, db: Session, workspace_id: str):
    return plane.admit(
        db,
        workspace_id=workspace_id,
        external_call_id="oracle-call",
        direction="inbound",
        channel="sip",
    )


def test_guard_passes_on_the_production_tree() -> None:
    guard.test_no_random_numbers_outside_the_allowlist()


def test_only_the_seed_module_imports_random() -> None:
    assert _production_modules_importing_random() == ["database/seed.py"]


def test_seed_module_still_imports_random() -> None:
    source = (BACKEND_ROOT / "database" / "seed.py").read_text(encoding="utf-8")
    assert re.compile(BASE_PATTERN, re.MULTILINE).search(source)


def test_guard_constants_are_unchanged_from_base() -> None:
    source = GUARD_PATH.read_text(encoding="utf-8")
    segments: dict[str, list[str | None]] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in {"RANDOM_ALLOWLIST", "RANDOM_PATTERN"}:
                segments.setdefault(target.id, []).append(ast.get_source_segment(source, node))
    assert segments == {
        "RANDOM_ALLOWLIST": [BASE_ALLOWLIST_SOURCE],
        "RANDOM_PATTERN": [BASE_PATTERN_SOURCE],
    }
    assert guard.RANDOM_ALLOWLIST == BASE_ALLOWLIST
    assert guard.RANDOM_PATTERN.pattern == BASE_PATTERN
    assert guard.RANDOM_PATTERN.flags == re.compile(BASE_PATTERN, re.MULTILINE).flags


def test_guard_allows_random_in_the_seed_module_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_tree(tmp_path, CLEAN_TREE)
    monkeypatch.setattr(guard, "BACKEND_ROOT", tmp_path)
    guard.test_no_random_numbers_outside_the_allowlist()


@pytest.mark.parametrize(
    ("relative", "text"),
    [
        ("voice/admission.py", "import random\n"),
        ("voice/admission.py", "import random as rng\n"),
        ("voice/admission.py", "from random import random\n"),
        ("voice/admission.py", "def jitter():\n    import random\n"),
        ("voice/backoff.py", "from random import uniform\n"),
    ],
    ids=["import", "import-as", "from-import", "indented-import", "other-module"],
)
def test_guard_rejects_random_outside_the_seed_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str, text: str
) -> None:
    _write_tree(tmp_path, {**CLEAN_TREE, relative: text})
    monkeypatch.setattr(guard, "BACKEND_ROOT", tmp_path)
    with pytest.raises(AssertionError, match=re.escape(relative)):
        guard.test_no_random_numbers_outside_the_allowlist()


def test_admission_has_no_randomness_source() -> None:
    assert _randomness_findings(ADMISSION_PATH.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "snippet",
    [
        "import random\n",
        "import random as rng\n",
        "from random import uniform\n",
        "import secrets\n",
        "from secrets import randbelow\n",
        "import numpy.random\n",
        "from numpy import random\n",
        "import numpy as np\nnp.random.random()\n",
        "import os\nos.urandom(2)\n",
        "from os import urandom\n",
        "rng = SystemRandom()\n",
        "import importlib\nimportlib.import_module('secrets')\n",
        "__import__('random')\n",
        "import uuid\n",
        "from uuid import uuid4\n",
        "jitter = uuid4().int % 100\n",
    ],
)
def test_randomness_scan_flags_each_forbidden_spelling(snippet: str) -> None:
    assert _randomness_findings(snippet) != []


def test_randomness_scan_allows_deterministic_backoff() -> None:
    snippet = (
        "import threading\n"
        "import time\n"
        "from sqlalchemy.exc import OperationalError\n\n"
        "def backoff(attempt):\n"
        "    time.sleep(min(0.05, 0.001 * attempt))\n"
    )
    assert _randomness_findings(snippet) == []


def test_lock_contention_retries_draw_no_randomness(
    monkeypatch: pytest.MonkeyPatch, session: Session, sleeps: list[float]
) -> None:
    for owner, name in RANDOMNESS_CALLS:
        label = f"{getattr(owner, '__name__', owner)}.{name}"

        def forbidden(*_args, _label: str = label, **_kwargs):
            raise AssertionError(f"lock-contention retry drew randomness from {_label}")

        monkeypatch.setattr(owner, name, forbidden)
    work, seen = _work(
        [_operational("database is locked"), _operational("deadlock detected")], sleeps
    )
    assert _plane()._retrying(session, work) == "done"
    assert seen == [0, 1, 2]
    _assert_backoff(sleeps, 2)


@pytest.mark.parametrize("message", CONTENTION_MESSAGES)
def test_lock_contention_is_retried_after_a_sleep(
    session: Session, sleeps: list[float], message: str
) -> None:
    work, seen = _work([_operational(message)], sleeps)
    assert _plane()._retrying(session, work) == "done"
    assert seen == [0, 1]
    _assert_backoff(sleeps, 1)


@pytest.mark.parametrize("message", NON_CONTENTION_MESSAGES)
def test_non_contention_operational_error_is_raised_at_once_without_sleep(
    session: Session, sleeps: list[float], message: str | None
) -> None:
    error = _operational(message)
    work, seen = _work([error, _operational(message)], sleeps)
    with pytest.raises(OperationalError) as raised:
        _plane()._retrying(session, work)
    assert raised.value is error
    assert seen == [0]
    assert sleeps == []


@pytest.mark.parametrize("budget", BUDGETS)
def test_contention_clearing_at_the_last_allowed_retry_succeeds(
    session: Session, sleeps: list[float], budget: int
) -> None:
    work, seen = _work([_operational("database is locked") for _ in range(budget)], sleeps)
    assert _plane(max_retries=budget)._retrying(session, work) == "done"
    assert seen == list(range(budget + 1))
    _assert_backoff(sleeps, budget)


@pytest.mark.parametrize("budget", BUDGETS)
def test_contention_beyond_max_retries_raises(
    session: Session, sleeps: list[float], budget: int
) -> None:
    work, seen = _work([_operational("database is locked") for _ in range(budget + 1)], sleeps)
    with pytest.raises(OperationalError):
        _plane(max_retries=budget)._retrying(session, work)
    assert seen == list(range(budget + 1))
    _assert_backoff(sleeps, budget)


def test_default_policy_keeps_every_contention_delay_in_bounds(
    session: Session, sleeps: list[float]
) -> None:
    plane = _plane()
    budget = plane.policy.max_retries
    work, seen = _work(
        [_operational("could not serialize access") for _ in range(budget + 1)], sleeps
    )
    with pytest.raises(OperationalError):
        plane._retrying(session, work)
    assert seen == list(range(budget + 1))
    _assert_backoff(sleeps, budget)


@pytest.mark.parametrize("budget", BUDGETS)
def test_integrity_errors_within_budget_retry_without_sleep(
    session: Session, sleeps: list[float], budget: int
) -> None:
    work, seen = _work([_integrity() for _ in range(budget)], sleeps)
    assert _plane(max_retries=budget)._retrying(session, work) == "done"
    assert seen == [0] * (budget + 1)
    assert sleeps == []


@pytest.mark.parametrize("budget", BUDGETS)
def test_integrity_errors_beyond_budget_raise(
    session: Session, sleeps: list[float], budget: int
) -> None:
    work, seen = _work([_integrity() for _ in range(budget + 1)], sleeps)
    with pytest.raises(IntegrityError):
        _plane(max_retries=budget)._retrying(session, work)
    assert seen == [0] * (budget + 1)
    assert sleeps == []


def test_admission_rides_out_transient_lock_contention(
    voice_db, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    factory, workspace_id = voice_db
    plane = _plane()
    with factory() as db:
        plane.register_node(db, node_id="n1", max_concurrent=5)
        attempts = _failing_commits(
            monkeypatch, db, [_operational("database is locked"), _operational("deadlock detected")]
        )
        result = _admit(plane, db, workspace_id)
    assert result.admitted
    assert attempts == [1, 2, 3]
    _assert_backoff(sleeps, 2)
    assert _held(factory) == 1


def test_admission_gives_up_when_lock_contention_outlasts_the_budget(
    voice_db, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    factory, workspace_id = voice_db
    plane = _plane(max_retries=3)
    with factory() as db:
        plane.register_node(db, node_id="n1", max_concurrent=5)
        attempts = _failing_commits(
            monkeypatch, db, [_operational("database is locked") for _ in range(5)]
        )
        with pytest.raises(OperationalError):
            _admit(plane, db, workspace_id)
    assert attempts == [1, 2, 3, 4]
    _assert_backoff(sleeps, 3)
    assert _held(factory) == 0


def test_admission_does_not_retry_a_non_contention_error(
    voice_db, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    factory, workspace_id = voice_db
    plane = _plane()
    with factory() as db:
        plane.register_node(db, node_id="n1", max_concurrent=5)
        attempts = _failing_commits(
            monkeypatch, db, [_operational("server closed the connection unexpectedly")]
        )
        with pytest.raises(OperationalError):
            _admit(plane, db, workspace_id)
    assert attempts == [1]
    assert sleeps == []
    assert _held(factory) == 0
