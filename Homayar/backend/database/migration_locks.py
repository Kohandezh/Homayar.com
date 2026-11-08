"""Keep ANALYZE away from the tables of a migration run (HOM-352, HOM-372).

``apply_row_level_security`` grants on every table (``GRANT ... ON ALL
TABLES`` rewrites each ``pg_class`` row, with no table lock) and then runs
``ALTER TABLE <t> ENABLE ROW LEVEL SECURITY``, which needs an
AccessExclusiveLock.  An ANALYZE (autovacuum or manual) that starts in between
holds a ShareUpdateExclusiveLock on the table, and on PostgreSQL 17 its
in-place statistics write waits for the migration's transaction: a deadlock,
and the detector may cancel the migration (CI, PR #206).

``lock_tables_against_analyze`` prevents it by lock order: before the run
changes any catalog row, on the migration's own connection and transaction, it
locks every existing table in SHARE UPDATE EXCLUSIVE mode (the ANALYZE lock).
Autovacuum then skips those tables, and a manual ANALYZE waits for the commit.
An ANALYZE already running when the LOCK is issued never waits on the
migration, because the migration has not touched any catalog row yet.  Normal
reads and writes (AccessShare, RowShare, RowExclusive) do not conflict with
this mode, so the running application is not blocked.

``backend/migrations/env.py`` runs every revision in one transaction, so the
lock taken at the start lasts for the whole run.  ``apply_row_level_security``
also takes it as its first statement on PostgreSQL, which protects callers
outside Alembic (backup restore, development schema bootstrap) and is a fast
no-op re-lock under Alembic.

A ``LockNotAvailable`` error after the 60 s timeout means another session held
a conflicting lock (VACUUM, ANALYZE, CREATE INDEX CONCURRENTLY, or DDL): the
operator should retry the upgrade.

Only online runs on PostgreSQL inside an open transaction take the lock;
offline (SQL generation) runs and SQLite are unchanged.
"""

# ruff: noqa: S608 - the SQL is built from catalog names (regclass), never from input.

from __future__ import annotations

from sqlalchemy import Connection, text

# A LOCK that cannot get a table within this time fails (LockNotAvailable) instead of
# hanging the run.  It applies to the LOCK statement only.
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

    Call it on the connection that runs the GRANT/ALTER DDL, right before it, with
    the transaction already open (``backend/migrations/env.py`` at the start of the
    Alembic transaction, and ``apply_row_level_security`` as its first statement).
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
