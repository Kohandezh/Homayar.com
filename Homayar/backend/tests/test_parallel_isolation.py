"""pytest-xdist isolation (HOM-292, ``_parallel.py``).

The scan decides which tests run alone because they change cluster-wide PostgreSQL
state or check a wall-clock budget; the guard's state query must see a role change; and
under ``-n`` every worker has its own database and Redis index.
"""

from __future__ import annotations

import os
import secrets
import time
import types

import psycopg
import pytest
from sqlalchemy.engine import make_url

from backend.tests import _parallel
from backend.tests._database import configured_database_url
from backend.tests._transport import configured_redis_url, redis_database_index


def _role_helper() -> str:
    return "ALTER ROLE homayar_app CREATEDB"


def _calls_the_role_helper() -> str:
    return _role_helper()


def _alembic_helper(command, config) -> None:
    command.downgrade(config, "base")


def _plain_sql() -> list[str]:
    return [
        "SELECT 1",
        "GRANT SELECT ON meetings TO homayar_app",
        "GRANT USAGE, CREATE ON SCHEMA public TO homayar_app",
        "REVOKE ALL ON SCHEMA public FROM homayar_app",
        "CREATE TABLE hom292_probe (id int)",
    ]


def _alembic_heads(command, config) -> None:
    command.heads(config)


def _budget_check() -> None:
    started = time.monotonic()
    elapsed = time.monotonic() - started
    budget = 30.0
    assert elapsed < budget


def _waits_with_a_clock() -> None:
    deadline = time.monotonic() + 5
    assert deadline > 0


def _item(function, **fixtures):
    return types.SimpleNamespace(
        obj=function,
        fixturenames=list(fixtures),
        _fixtureinfo=types.SimpleNamespace(
            name2fixturedefs={
                name: (types.SimpleNamespace(func=func),) for name, func in fixtures.items()
            }
        ),
    )


def test_role_ddl_in_the_test_makes_it_exclusive():
    assert _parallel.runs_alone(_item(_role_helper))


def test_role_ddl_in_a_function_the_test_calls_makes_it_exclusive():
    assert _parallel.runs_alone(_item(_calls_the_role_helper))


def test_role_ddl_in_a_fixture_makes_the_test_exclusive():
    assert _parallel.runs_alone(_item(_plain_sql, probe=_role_helper))


def test_an_alembic_downgrade_makes_the_test_exclusive():
    assert _parallel.runs_alone(_item(_alembic_helper))


def test_a_wall_clock_budget_runs_alone_and_a_plain_wait_does_not():
    assert _parallel.runs_alone(_item(_budget_check))
    assert not _parallel.runs_alone(_item(_waits_with_a_clock))


def test_object_grants_plain_sql_and_alembic_heads_stay_shared():
    assert not _parallel.runs_alone(_item(_plain_sql))
    assert not _parallel.runs_alone(_item(_alembic_heads))


@pytest.mark.postgres
def test_the_guard_state_sees_a_role_created_and_dropped():
    # CREATE ROLE in this test makes it exclusive under xdist, so no other worker runs.
    url = configured_database_url()
    role = f"hom292_guard_{secrets.token_hex(3)}"
    libpq = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(libpq, autocommit=True) as connection:

        def state() -> str:
            return connection.execute(_parallel.CLUSTER_STATE).fetchone()[0]

        before = state()
        connection.execute(f"CREATE ROLE {role} NOLOGIN")
        try:
            assert state() != before
        finally:
            connection.execute(f"DROP ROLE {role}")
        assert state() == before


def test_the_lock_is_active_only_in_an_xdist_worker():
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    assert (_parallel.cluster_lock() is None) == (
        worker is None or configured_database_url() is None
    )


def test_an_xdist_worker_has_its_own_database_and_redis_index():
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    database = configured_database_url()
    redis = configured_redis_url()
    if worker is None:
        # A serial run uses the configured names unchanged.
        assert os.environ.get(_parallel.LOCK_DATABASE_ENV) is None
        return
    number = int(worker.removeprefix("gw"))
    if database is not None:
        base = os.environ[_parallel.LOCK_DATABASE_ENV]
        assert make_url(database).database == f"{make_url(base).database}_{worker}"
    if redis is not None:
        base_index = redis_database_index(os.environ[_parallel.BASE_REDIS_ENV])
        assert redis_database_index(redis) == base_index + number
