"""An ANALYZE that races an in-process Alembic run (HOM-352).

``apply_row_level_security`` first grants on every table (``GRANT ... ON ALL TABLES``
rewrites each ``pg_class`` row, with no table lock) and then runs ``ALTER TABLE <t>
ENABLE ROW LEVEL SECURITY`` (AccessExclusiveLock).  An ANALYZE in between holds a
ShareUpdateExclusiveLock on the table, and on PostgreSQL 17 its in-place statistics
write waits for the migration's transaction: a deadlock (CI, PR #206, autovacuum).

The cases force that interleaving with a manual ANALYZE on a second connection.  Its
session ``deadlock_timeout`` is longer than the migration's, so on a cycle the
migration's backend runs the deadlock check first and is the one that fails, as on CI.
``_migration_locks.py`` takes the ANALYZE lock on every existing table at the start of
each Alembic transaction, so the race cannot start.  The allow-control runs the same
interleaving without Alembic: PostgreSQL still reports that deadlock.
"""

from __future__ import annotations

import inspect
import io
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError

from backend.config import Settings, reset_settings_cache
from backend.database.rls import apply_row_level_security
from backend.observability.release import PROJECT_ROOT
from backend.tests import _migration_locks
from backend.tests._database import TEST_APP_ROLE, inspection_session_factory
from backend.tests._migration_locks import lock_tables_against_analyze

ENABLE_RLS = re.compile(r"^ALTER TABLE (\S+) ENABLE ROW LEVEL SECURITY$")
VERSION_UPDATE = re.compile(r"^(UPDATE|INSERT INTO) alembic_version\b")
# The ANALYZE session waits this long before it checks for a deadlock; the migration's
# backend keeps the server's 1 s, so it is the side that reports a cycle.
ANALYZE_DEADLOCK_TIMEOUT = "10s"
BLOCKED_WAIT = 30.0

USER_TABLES = """
SELECT c.oid::regclass::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p')
   AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
   AND n.nspname NOT LIKE 'pg\\_temp\\_%' AND n.nspname NOT LIKE 'pg\\_toast\\_temp\\_%'
"""
# Lock modes that conflict with ANALYZE's ShareUpdateExclusiveLock.
HELD_AGAINST_ANALYZE = """
SELECT l.relation::regclass::text FROM pg_locks l
 WHERE l.pid = %s AND l.locktype = 'relation' AND l.granted
   AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
   AND l.mode IN ('ShareUpdateExclusiveLock', 'ShareLock', 'ShareRowExclusiveLock',
                  'ExclusiveLock', 'AccessExclusiveLock')
"""


def _libpq(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def _alembic(monkeypatch, settings: Settings) -> Config:
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _latest_rls_revision(config: Config):
    """The newest revision whose upgrade calls ``apply_row_level_security``."""
    for revision in ScriptDirectory.from_config(config).walk_revisions():
        if "apply_row_level_security(" in inspect.getsource(revision.module.upgrade):
            return revision
    raise AssertionError("no revision applies the row-level security registry")


@contextmanager
def _on_statement(pattern: re.Pattern[str], action: Callable[[str, int], None]) -> Iterator[list]:
    """Run ``action(match, backend pid)`` before the first statement that matches."""
    seen: list[str] = []

    def before(conn, cursor, statement, parameters, context, executemany):
        found = pattern.match(statement.strip())
        if found and not seen:
            seen.append(statement.strip())
            action(found.group(1), cursor.connection.info.backend_pid)

    event.listen(Engine, "before_cursor_execute", before)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", before)


class _Analyze:
    """``ANALYZE <table>`` on its own connection, started and parked until it blocks."""

    def __init__(self, url: str) -> None:
        self.url = _libpq(url)
        self.error: BaseException | None = None
        self.thread: threading.Thread | None = None

    def start(self, table: str) -> None:
        connection = psycopg.connect(self.url, autocommit=True)
        connection.execute(f"SET deadlock_timeout = '{ANALYZE_DEADLOCK_TIMEOUT}'")
        pid = connection.info.backend_pid

        def run() -> None:
            try:
                connection.execute(f"ANALYZE {table}")
            except BaseException as exc:  # noqa: BLE001 - reported by finish()
                self.error = exc
            finally:
                connection.close()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        with psycopg.connect(self.url, autocommit=True) as watcher:
            deadline = time.monotonic() + BLOCKED_WAIT
            while self.thread.is_alive():
                waiting = watcher.execute(
                    "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = %s",
                    (pid,),
                ).fetchone()
                if waiting and waiting[0]:
                    return
                assert time.monotonic() < deadline, "the ANALYZE neither blocked nor ended"
                time.sleep(0.02)

    def finish(self) -> BaseException | None:
        if self.thread is not None:
            self.thread.join(timeout=60)
            assert not self.thread.is_alive(), "the ANALYZE did not end"
        return self.error


@pytest.mark.postgres
def test_an_analyze_between_the_grant_and_the_alter_does_not_deadlock_the_migration(
    client, monkeypatch, settings
):
    config = _alembic(monkeypatch, settings)
    revision = _latest_rls_revision(config)
    analyze = _Analyze(settings.database_url)
    try:
        command.stamp(config, "head")
        command.downgrade(config, revision.down_revision)
        with _on_statement(ENABLE_RLS, lambda table, _pid: analyze.start(table)) as seen:
            command.upgrade(config, revision.revision)
        assert seen, "the step never enabled row-level security"
        assert analyze.finish() is None
        command.upgrade(config, "head")
    finally:
        analyze.finish()
        reset_settings_cache()


@pytest.mark.postgres
def test_an_analyze_does_not_deadlock_a_direct_registry_apply_of_a_test(client, settings):
    # Migration tests also apply the registry themselves between Alembic steps, outside
    # Alembic (test_scorecard_versions_migration.py, run 13 of the HOM-352 proof loop).
    analyze = _Analyze(settings.database_url)
    try:
        with (
            inspection_session_factory()() as db,
            _on_statement(ENABLE_RLS, lambda table, _pid: analyze.start(table)) as seen,
        ):
            connection = db.connection()
            lock_tables_against_analyze(connection)
            apply_row_level_security(connection)
            db.commit()
        assert seen, "the registry never enabled row-level security"
        assert analyze.finish() is None
    finally:
        analyze.finish()


@pytest.mark.postgres
def test_the_alembic_transaction_holds_the_analyze_lock_on_every_table_to_its_end(
    client, monkeypatch, settings
):
    config = _alembic(monkeypatch, settings)
    revision = _latest_rls_revision(config)
    url = _libpq(settings.database_url)
    held: dict[str, set[str]] = {}

    def check(_table: str, pid: int) -> None:
        with psycopg.connect(url, autocommit=True) as watcher:
            held["tables"] = {row[0] for row in watcher.execute(HELD_AGAINST_ANALYZE, (pid,))}

    try:
        command.stamp(config, "head")
        command.downgrade(config, revision.down_revision)
        with psycopg.connect(url, autocommit=True) as connection:
            before = {row[0] for row in connection.execute(USER_TABLES)}
        assert "meetings" in before
        # The version row is written after the step's DDL, right before the commit.
        with _on_statement(VERSION_UPDATE, check) as seen:
            command.upgrade(config, revision.revision)
        assert seen, "the run never wrote the version row"
        assert before - held["tables"] == set()
        command.upgrade(config, "head")
    finally:
        reset_settings_cache()


@pytest.mark.postgres
def test_the_same_interleaving_outside_alembic_still_reports_the_deadlock(client, settings):
    # Allow-control: the lock is taken for Alembic runs only, so a real cycle between two
    # plain sessions is still detected and reported, never absorbed.
    url = _libpq(settings.database_url)
    analyze = _Analyze(settings.database_url)
    with psycopg.connect(url) as session:
        session.execute(f'GRANT SELECT ON meetings TO "{TEST_APP_ROLE}"')
        analyze.start("meetings")
        with pytest.raises(psycopg.errors.DeadlockDetected):
            session.execute("ALTER TABLE meetings ENABLE ROW LEVEL SECURITY")
        session.rollback()
    assert analyze.finish() is None


@pytest.mark.postgres
def test_a_materialized_view_does_not_break_the_lock(client):
    # LOCK TABLE refuses a materialized view, so the lock list leaves it out.
    with inspection_session_factory()() as db:
        db.execute(text("CREATE MATERIALIZED VIEW hom352_probe_view AS SELECT 1 AS x"))
        with pytest.raises(DBAPIError) as refused, db.begin_nested():
            db.execute(text("LOCK TABLE hom352_probe_view IN SHARE UPDATE EXCLUSIVE MODE"))
        assert isinstance(refused.value.orig, psycopg.errors.WrongObjectType)
        lock_tables_against_analyze(db.connection())
        db.rollback()


@pytest.mark.postgres
def test_a_held_table_lock_makes_the_lock_fail_and_the_timeout_is_restored(
    client, settings, monkeypatch
):
    monkeypatch.setattr(_migration_locks, "LOCK_TIMEOUT", "200ms")
    with inspection_session_factory()() as db:
        db.execute(text("SET LOCAL lock_timeout = '7s'"))
        lock_tables_against_analyze(db.connection())
        assert db.execute(text("SHOW lock_timeout")).scalar() == "7s"
        db.rollback()

    with psycopg.connect(_libpq(settings.database_url)) as holder:
        holder.execute("LOCK TABLE meetings IN SHARE UPDATE EXCLUSIVE MODE")
        with inspection_session_factory()() as db:
            with pytest.raises(OperationalError) as stuck:
                lock_tables_against_analyze(db.connection())
            assert isinstance(stuck.value.orig, psycopg.errors.LockNotAvailable)
            db.rollback()
        holder.rollback()


def test_offline_and_sqlite_runs_take_no_lock():
    # Offline (SQL-generation) output and a SQLite connection stay as Alembic makes them.
    output = io.StringIO()
    offline = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output, "fn": lambda heads, context: []},
    )
    offline.run_migrations()
    assert "LOCK" not in output.getvalue().upper()

    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection, connection.begin():
            statements: list[str] = []
            event.listen(
                connection, "before_cursor_execute", lambda *args: statements.append(args[2])
            )
            online = MigrationContext.configure(connection, opts={"fn": lambda heads, context: []})
            online.run_migrations()
            assert statements
            assert not [s for s in statements if "LOCK" in s.upper()]
            assert connection.execute(text("SELECT count(*) FROM alembic_version")).scalar() == 0
    finally:
        engine.dispose()
