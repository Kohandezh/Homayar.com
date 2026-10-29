"""pytest-xdist workers for the backend suite (HOM-292).

Run with ``-n <workers> --dist loadfile`` (CI does).  Without ``-n`` nothing here is
active and the suite runs exactly as before.

* Each worker uses its own database ``<TEST_DATABASE_URL database>_<worker id>``,
  created when the worker starts and dropped when it stops, its own Redis database
  index (``TEST_REDIS_URL`` index + worker number) and its own storage directories.
* PostgreSQL roles, role memberships and role settings are cluster-wide: every worker
  sees them.  A readers-writer lock (advisory locks on one connection per worker to the
  base test database) keeps a test that changes them alone: it holds the lock in
  exclusive mode, every other test in shared mode.  The lock covers the whole item
  protocol, so module-scoped fixture setup and teardown are inside it.
* A test is exclusive when its source, the functions and classes it reaches (bare names,
  ``self.x``, ``module.x``) in any ``backend.tests`` module except this plumbing, or its
  fixtures contain role DDL (``ROLE``, ``USER`` or ``GROUP``), a role grant or revoke (one
  role or a list), ``DROP/REASSIGN OWNED``, ``ALTER DATABASE``/``ALTER SYSTEM``, an Alembic
  upgrade, downgrade or stamp (the migrations change ``homayar_audit_reader``), or a direct
  call of the row-level security role statements.  A test that reads a wall clock and
  checks a time budget (the HOM-172 golden-module budget) also runs alone, so other
  workers do not slow it down.  No marker is needed.
* A guard compares the cluster-wide state when a shared test starts with the state after
  its teardown, and fails the test when they differ.

Known limit: the scan reads source text, upper-case SQL and Python references only.  A
test whose cluster change the scan cannot see (SQL built at run time, lower-case SQL, code
outside ``backend.tests``) and that undoes the change before it ends is not caught: it runs
in shared mode and other workers may see the change while it lasts.  The guard catches
only a change that is still there after teardown.
"""

# ruff: noqa: S608 - the SQL is built from constants of this module.

from __future__ import annotations

import ast
import inspect
import os
import re
import sys
import textwrap
import time
import types
from typing import Any

import psycopg
from sqlalchemy.engine import make_url

from backend.tests._database import TEST_DATABASE_ENV, configured_database_url, is_postgres_url
from backend.tests._transport import TEST_REDIS_ENV, configured_redis_url, redis_database_index

LOCK_DATABASE_ENV = "HOMAYAR_TEST_LOCK_DATABASE_URL"
BASE_REDIS_ENV = "HOMAYAR_TEST_BASE_REDIS_URL"
WORKER_DIRECTORY_ENVS = ("STORAGE_LOCAL_ROOT", "UPLOAD_DIR", "UPLOAD_QUARANTINE_DIR")
# pg_advisory_lock key of the cluster-wide state lock (any constant shared by the workers).
CLUSTER_LOCK_KEY = 4_292_000_001

_ROLE_LIST = r"[^\s,;(]+(?:\s*,\s*[^\s,;(]+)*"
CLUSTER_CHANGES = re.compile(
    r"\b(CREATE|ALTER|DROP)\s+(ROLE|USER|GROUP)\b"
    r"|\b(DROP|REASSIGN)\s+OWNED\b"
    r"|\bALTER\s+(DATABASE|SYSTEM)\b"
    # a role grant: GRANT <role>[, <role>...] TO <role> (an object grant has ON before TO)
    rf"|\bGRANT\s+{_ROLE_LIST}\s+TO\b"
    rf"|\bREVOKE\s+(?:(?:ADMIN|INHERIT|SET)\s+OPTION\s+FOR\s+)?{_ROLE_LIST}\s+FROM\b"
    r"|\bcommand\.(upgrade|downgrade|stamp)\b"
    r"|\bgetattr\(command\b"
    r"|\b(apply_row_level_security|audit_reader_statements|remove_audit_reader_statements)\b"
)

# A wall-clock read and a time budget in the same function.
TIME_BUDGET = re.compile(r"(?=[\s\S]*\b(?:monotonic|perf_counter)\(\))[\s\S]*\b[Bb]udget\b")

CLUSTER_STATE = """
SELECT md5(string_agg(line, ',' ORDER BY line)) FROM (
  SELECT 'R' || ROW(r.rolname, r.rolsuper, r.rolinherit, r.rolcreaterole, r.rolcreatedb,
         r.rolcanlogin, r.rolreplication, r.rolbypassrls, r.rolconnlimit,
         r.rolvaliduntil)::text AS line
    FROM pg_roles r WHERE r.rolname NOT LIKE 'pg\\_%'
  UNION ALL
  SELECT 'M' || ROW(m.roleid::regrole::text, m.member::regrole::text,
         m.grantor::regrole::text, m.admin_option, m.inherit_option, m.set_option)::text
    FROM pg_auth_members m
  UNION ALL
  SELECT 'S' || ROW(s.setrole::regrole::text, s.setconfig)::text
    FROM pg_db_role_setting s WHERE s.setdatabase = 0
) cluster
"""

_lock: ClusterLock | None = None


def worker_id(config: Any) -> str | None:
    workerinput = getattr(config, "workerinput", None)
    return workerinput.get("workerid") if workerinput else None


def configure_worker(worker: str) -> None:
    """Point this worker at its own database, Redis index and directories."""
    global _lock
    number = int(worker.removeprefix("gw"))
    url = configured_database_url()
    if is_postgres_url(url):
        base = make_url(url)
        own = base.set(database=f"{base.database}_{worker}")
        _run_autocommit(
            url,
            f'DROP DATABASE IF EXISTS "{own.database}" WITH (FORCE)',
            f'CREATE DATABASE "{own.database}"',
        )
        own_url = own.render_as_string(hide_password=False)
        os.environ[LOCK_DATABASE_ENV] = url
        os.environ[TEST_DATABASE_ENV] = own_url
        os.environ["DATABASE_URL"] = own_url
        _lock = ClusterLock(url)
        _lock.started = time.perf_counter()
    redis_url = configured_redis_url()
    if redis_url:
        index = redis_database_index(redis_url) + number
        os.environ[BASE_REDIS_ENV] = redis_url
        os.environ[TEST_REDIS_ENV] = redis_url.rsplit("/", 1)[0] + f"/{index}"
    for name in WORKER_DIRECTORY_ENVS:
        if os.environ.get(name):
            os.environ[name] = f"{os.environ[name]}-{worker}"


def unconfigure_worker() -> None:
    global _lock
    if _lock is not None:
        _lock.close()
        _lock = None
    base = os.environ.get(LOCK_DATABASE_ENV)
    own = configured_database_url()
    if base and own and own != base:
        _run_autocommit(base, f'DROP DATABASE IF EXISTS "{make_url(own).database}" WITH (FORCE)')


def cluster_lock() -> ClusterLock | None:
    return _lock


class ClusterLock:
    """Shared or exclusive hold on cluster-wide state, across the xdist workers.

    PostgreSQL queues a new shared request behind a waiting exclusive one, so a test
    that needs the exclusive lock does not starve.
    """

    def __init__(self, url: str) -> None:
        self._connection = psycopg.connect(_libpq(url), autocommit=True)
        self._held: bool | None = None  # True: exclusive, False: shared, None: not held
        # Time accounting for the worker's summary line.
        self.started = time.perf_counter()
        self.waited = {False: 0.0, True: 0.0}
        self.exclusive_seconds = 0.0
        self.exclusive_tests = 0
        self._exclusive_since: float | None = None
        self.file_seconds: dict[str, float] = {}
        self.file_ends: dict[str, float] = {}

    def note_test(self, path: str, seconds: float) -> None:
        name = path.rsplit("/", 1)[-1]
        self.file_seconds[name] = self.file_seconds.get(name, 0.0) + seconds
        self.file_ends[name] = time.perf_counter() - self.started

    def acquire(self, *, exclusive: bool) -> None:
        function = "pg_advisory_lock" if exclusive else "pg_advisory_lock_shared"
        started = time.perf_counter()
        self._connection.execute(f"SELECT {function}(%s)", (CLUSTER_LOCK_KEY,))
        now = time.perf_counter()
        self.waited[exclusive] += now - started
        if exclusive:
            self._exclusive_since = now

    def release(self, *, exclusive: bool) -> None:
        function = "pg_advisory_unlock" if exclusive else "pg_advisory_unlock_shared"
        self._connection.execute(f"SELECT {function}(%s)", (CLUSTER_LOCK_KEY,))
        if exclusive and self._exclusive_since is not None:
            self.exclusive_seconds += time.perf_counter() - self._exclusive_since
            self._exclusive_since = None

    def summary(self) -> list[str]:
        slowest = sorted(self.file_seconds.items(), key=lambda pair: -pair[1])[:10]
        last = sorted(self.file_ends.items(), key=lambda pair: pair[1])[-3:]
        return [
            f"cluster lock: waited {self.waited[False]:.1f} s for shared and "
            f"{self.waited[True]:.1f} s for exclusive holds; held exclusive "
            f"{self.exclusive_seconds:.1f} s for {self.exclusive_tests} tests; worker session "
            f"{time.perf_counter() - self.started:.1f} s",
            "slowest files: " + ", ".join(f"{name} {sec:.0f} s" for name, sec in slowest),
            "last files (ended at s): " + ", ".join(f"{name} {end:.0f}" for name, end in last),
        ]

    def hold(self, *, exclusive: bool) -> None:
        """Hold the lock in this mode; a worker keeps an exclusive hold between two
        exclusive tests in a row instead of queueing again behind the other workers."""
        if self._held is exclusive:
            return
        self.release_held()
        self.acquire(exclusive=exclusive)
        self._held = exclusive

    def release_held(self) -> None:
        if self._held is not None:
            self.release(exclusive=self._held)
            self._held = None

    def cluster_state(self) -> str:
        return self._connection.execute(CLUSTER_STATE).fetchone()[0]

    def close(self) -> None:
        self._connection.close()


def runs_alone(item: Any) -> bool:
    """True when the test, a function it calls, or one of its fixtures changes cluster-wide
    state or checks a wall-clock budget: it then holds the lock in exclusive mode."""
    starts = [getattr(item, "obj", None)]
    for name in item.fixturenames:
        for fixturedef in item._fixtureinfo.name2fixturedefs.get(name, ()):
            starts.append(fixturedef.func)
    return any(_reaches_exclusive_code(function) for function in starts if function)


_verdicts: dict[Any, bool] = {}
# The fixture plumbing restores the app role itself, under the lock that is already held;
# following it would make every test that uses ``settings`` exclusive.
_PLUMBING = frozenset({"backend.tests._isolation", "backend.tests._parallel"})


def _reaches_exclusive_code(function: Any) -> bool:
    function = _unwrap(function)
    if function in _verdicts:
        return _verdicts[function]
    _verdicts[function] = False  # cycles
    seen: set[Any] = set()
    todo = [function]
    found = False
    while todo and not found:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            source = textwrap.dedent(inspect.getsource(current))
        except (OSError, TypeError):
            continue
        if CLUSTER_CHANGES.search(source) or TIME_BUDGET.search(source):
            found = True
            break
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        todo.extend(target for target in _references(current, tree) if target not in seen)
    _verdicts[function] = found
    return found


def _references(current: Any, tree: ast.AST) -> list[Any]:
    """Functions and classes that ``current`` names: ``x``, ``self.x``, ``cls.x``,
    ``module.x`` and ``Class.x``, when they live in a followed ``backend.tests`` module."""
    namespace = _namespace(current)
    owner = _owner_class(current, namespace)
    found = []
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.Name):
            target = namespace.get(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            base = owner if node.value.id in ("self", "cls") else namespace.get(node.value.id)
            if isinstance(base, (types.ModuleType, type)):
                target = inspect.getattr_static(base, node.attr, None)
        target = _unwrap(target)
        if isinstance(target, (types.FunctionType, type)) and _is_followed(target):
            found.append(target)
    return found


def _unwrap(target: Any) -> Any:
    if isinstance(target, (staticmethod, classmethod, types.MethodType)):
        target = target.__func__
    return inspect.unwrap(target) if callable(target) else target


def _namespace(target: Any) -> dict[str, Any]:
    if isinstance(target, types.FunctionType):
        return target.__globals__
    module = sys.modules.get(getattr(target, "__module__", ""))
    return vars(module) if module is not None else {}


def _owner_class(target: Any, namespace: dict[str, Any]) -> type | None:
    if isinstance(target, type):
        return target
    qualname = getattr(target, "__qualname__", "")
    if "." not in qualname or "<locals>" in qualname:
        return None
    owner = namespace.get(qualname.split(".")[0])
    return owner if isinstance(owner, type) else None


def _is_followed(target: Any) -> bool:
    module = getattr(target, "__module__", None) or ""
    return module.startswith("backend.tests.") and module not in _PLUMBING


def _libpq(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def _run_autocommit(url: str, *statements: str) -> None:
    with psycopg.connect(_libpq(url), autocommit=True) as connection:
        for statement in statements:
            connection.execute(statement)
