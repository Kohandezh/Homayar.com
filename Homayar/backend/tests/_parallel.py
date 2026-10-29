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
* A test is exclusive when its source, the module functions it calls, or its fixtures
  contain role DDL, ``ALTER DATABASE``/``ALTER SYSTEM``, an Alembic upgrade, downgrade or
  stamp (the migrations change ``homayar_audit_reader``), or a direct call of the row-level
  security role statements.  A test that reads a wall clock and checks a time budget (the
  HOM-172 golden-module budget) also runs alone, so other workers do not slow it down.
  No marker is needed.
* A guard fails a shared test that changed cluster-wide state anyway, so a test the scan
  misses fails loudly instead of racing with other workers.
"""

# ruff: noqa: S608 - the SQL is built from constants of this module.

from __future__ import annotations

import ast
import inspect
import os
import re
import textwrap
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

CLUSTER_CHANGES = re.compile(
    r"\b(CREATE|ALTER|DROP)\s+ROLE\b"
    r"|\b(DROP|REASSIGN)\s+OWNED\b"
    r"|\bALTER\s+(DATABASE|SYSTEM)\b"
    r"|\bGRANT\s+[^\s,;(]+\s+TO\b"  # a role grant: GRANT <role> TO <role>
    r"|\bREVOKE\s+[^\s,;(]+\s+FROM\b"  # a role revoke: REVOKE <role> FROM <role>
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

    def acquire(self, *, exclusive: bool) -> None:
        function = "pg_advisory_lock" if exclusive else "pg_advisory_lock_shared"
        self._connection.execute(f"SELECT {function}(%s)", (CLUSTER_LOCK_KEY,))

    def release(self, *, exclusive: bool) -> None:
        function = "pg_advisory_unlock" if exclusive else "pg_advisory_unlock_shared"
        self._connection.execute(f"SELECT {function}(%s)", (CLUSTER_LOCK_KEY,))

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


def _reaches_exclusive_code(function: Any) -> bool:
    function = inspect.unwrap(getattr(function, "__func__", function))
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
        names = {node.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Name)}
        for name in names:
            target = getattr(current, "__globals__", {}).get(name)
            if (
                isinstance(target, types.FunctionType)
                and _is_test_code(target.__module__ or "")
                and target not in seen
            ):
                todo.append(target)
    _verdicts[function] = found
    return found


def _is_test_code(module: str) -> bool:
    """Test modules and ``conftest``; not the fixture plumbing (``_isolation`` restores the
    app role itself, under the lock that is already held)."""
    return module == "backend.tests.conftest" or module.startswith("backend.tests.test_")


def _libpq(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def _run_autocommit(url: str, *statements: str) -> None:
    with psycopg.connect(_libpq(url), autocommit=True) as connection:
        for statement in statements:
            connection.execute(statement)
