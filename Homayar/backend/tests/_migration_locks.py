"""Keep ANALYZE away from the tables of an in-process Alembic run (HOM-352).

``apply_row_level_security`` grants on every table (``GRANT ... ON ALL TABLES`` rewrites
each ``pg_class`` row, with no table lock) and then runs ``ALTER TABLE <t> ENABLE ROW
LEVEL SECURITY``, which needs an AccessExclusiveLock.  An ANALYZE (autovacuum or manual)
that starts in between holds a ShareUpdateExclusiveLock on ``<t>``, and on PostgreSQL 17
its in-place statistics write waits for the migration's transaction: a deadlock, and the
detector may cancel the migration (CI, PR #206).

The test process wraps ``MigrationContext.run_migrations``: before the first step, on the
migration's own connection and transaction, it locks every existing table in SHARE
UPDATE EXCLUSIVE mode (the ANALYZE lock).  Autovacuum then skips those tables and a
manual ANALYZE waits for the commit; since the lock comes before the migration changes
any catalog row, an ANALYZE already running never waits on the migration.  Normal reads
and writes do not conflict with that mode.  ``env.py`` runs all steps in one
``begin_transaction`` (``transaction_per_migration`` is off), so the lock lasts until
the run commits or rolls back.  Tables the run creates are invisible to other sessions
until then.

Only online runs on PostgreSQL inside an open transaction take the lock; offline (SQL
generation) runs and SQLite are unchanged.  An Alembic child process (``release.
run_alembic``) does not load this module.  Product code is untouched.

Some migration tests also apply the registry themselves between Alembic steps
(``apply_row_level_security(db.connection())``); they call
``lock_tables_against_analyze`` on that connection first.
"""

# ruff: noqa: S608 - the SQL is built from catalog names (regclass), never from input.

from __future__ import annotations

import functools
from typing import Any

from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, text

_INSTALLED = "_hom352_locks_tables"
# A LOCK that cannot get a table within this time fails (LockNotAvailable) instead of
# hanging the test.  It applies to the LOCK statement only.
LOCK_TIMEOUT = "60s"
# set_config(..., true): for the current transaction only, like SET LOCAL.
SET_LOCK_TIMEOUT = text("SELECT set_config('lock_timeout', :value, true)")

# Every table and partitioned table in the user schemas that this role may lock in this
# mode (any of UPDATE, DELETE, TRUNCATE), oldest first.  LOCK TABLE refuses a
# materialized view (WrongObjectType), so none is listed.
EXISTING_TABLES = text(
    """
SELECT c.oid::regclass::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p')
   AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
   AND n.nspname NOT LIKE 'pg\\_temp\\_%' AND n.nspname NOT LIKE 'pg\\_toast\\_temp\\_%'
   AND has_table_privilege(c.oid, 'UPDATE, DELETE, TRUNCATE')
 ORDER BY c.oid
"""
)


def lock_tables_against_analyze(connection: Connection) -> None:
    """Lock every existing table in SHARE UPDATE EXCLUSIVE mode until the transaction ends.

    Also for a test that applies the row-level security registry itself (outside Alembic)
    on a database with data: call it on the same connection right before.
    """
    if connection.dialect.name != "postgresql" or not connection.in_transaction():
        return
    tables = connection.execute(EXISTING_TABLES).scalars().all()
    if not tables:
        return
    previous = connection.execute(text("SELECT current_setting('lock_timeout')")).scalar_one()
    connection.execute(SET_LOCK_TIMEOUT, {"value": LOCK_TIMEOUT})
    connection.execute(text(f"LOCK TABLE {', '.join(tables)} IN SHARE UPDATE EXCLUSIVE MODE"))
    # Back to the caller's value, so the migration steps run with their own settings.
    connection.execute(SET_LOCK_TIMEOUT, {"value": previous})


def lock_existing_tables(context: MigrationContext) -> None:
    if context.as_sql or context.connection is None:
        return
    lock_tables_against_analyze(context.connection)


def install() -> None:
    """Wrap ``MigrationContext.run_migrations`` once for this process."""
    original = MigrationContext.run_migrations
    if getattr(original, _INSTALLED, False):
        return

    @functools.wraps(original)
    def run_migrations(self: MigrationContext, **kw: Any) -> None:
        lock_existing_tables(self)
        original(self, **kw)

    setattr(run_migrations, _INSTALLED, True)
    MigrationContext.run_migrations = run_migrations  # type: ignore[method-assign]
