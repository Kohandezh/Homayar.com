"""Snapshot-consistent logical dump and load of the PostgreSQL schema (HOM-116/117).

The dump is the *data* of every table in ``public`` taken inside ONE
``REPEATABLE READ READ ONLY`` transaction, with ``ACCESS SHARE`` locks on
every table (no DDL, no migration can run underneath).  The catalog that the
restore verifies against (row counts, per-tenant counts, object references,
the Alembic revision) is read in that same transaction, so the manifest
describes exactly the data in the set.

The *schema* is not dumped: it is rebuilt by ``alembic upgrade <revision>``
from the application's own migrations, then the data is loaded, then the
schema is moved forward to the code's head.  A restore therefore always
lands on the migration-defined schema (policies included) and proves the
migration chain at the same time.  It needs the code to contain the backup's
revision: a backup newer than the code is refused.

Only the psycopg driver the application already ships is used (no pg_dump /
pg_restore version coupling), so the same code runs in the API image, in CI
and on an operator host.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

SKIP_TABLES = frozenset({"alembic_version"})
_SESSION_SETTINGS = (
    "SET TIME ZONE 'UTC'",
    "SET datestyle = 'ISO, YMD'",
    "SET intervalstyle = 'postgres'",
    "SET extra_float_digits = 3",
    "SET client_encoding = 'UTF8'",
    "SET standard_conforming_strings = on",
)


class DatabaseBackupError(RuntimeError):
    """The database cannot be dumped or loaded safely; ``code`` is stable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def conninfo(database_url: str) -> str:
    """libpq URL for a SQLAlchemy ``postgresql+psycopg://`` URL."""
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise DatabaseBackupError("unsupported_database", "backups need PostgreSQL")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def with_database(database_url: str, database: str) -> str:
    return make_url(database_url).set(database=database).render_as_string(hide_password=False)


def database_name(database_url: str) -> str:
    return make_url(database_url).database or ""


def server_identity(database_url: str) -> tuple[str, int]:
    url = make_url(database_url)
    return (url.host or "localhost", int(url.port or 5432))


def connect(database_url: str, *, autocommit: bool = False) -> psycopg.Connection:
    # UTF-8 on the wire even for a SQL_ASCII maintenance database (texts as str).
    return psycopg.connect(conninfo(database_url), autocommit=autocommit, client_encoding="UTF8")


def _settings(connection: psycopg.Connection) -> None:
    for statement in _SESSION_SETTINGS:
        connection.execute(statement)


def _require_bypass(connection: psycopg.Connection, *, superuser: bool) -> None:
    row = connection.execute(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    if row is None:
        raise DatabaseBackupError("role_unknown", "current role not found")
    is_superuser, bypass = bool(row[0]), bool(row[1])
    if superuser and not is_superuser:
        raise DatabaseBackupError(
            "role_not_superuser",
            "restore loads data with triggers disabled and needs a superuser connection",
        )
    if not (is_superuser or bypass):
        raise DatabaseBackupError(
            "role_subject_to_rls",
            "the backup role must be a superuser or have BYPASSRLS; the application role "
            "(homayar_app) can never take a complete backup",
        )


def list_tables(connection: psycopg.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition "
        "ORDER BY c.relname"
    ).fetchall()
    return [row[0] for row in rows]


def table_columns(connection: psycopg.Connection, table: str) -> list[list[str]]:
    """``[[name, type], ...]`` of the stored (non-generated, non-dropped) columns."""
    rows = connection.execute(
        "SELECT a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
        "WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped "
        "AND a.attgenerated = '' ORDER BY a.attnum",
        (f'public."{table}"',),
    ).fetchall()
    return [[row[0], row[1]] for row in rows]


def _from_target(connection: psycopg.Connection, table: str) -> sql.Composable:
    """``FROM`` target of a listed table.

    A plain table (``relkind = 'r'``) is read with ``ONLY``: a plain
    ``FROM t`` also reads every table that inherits from ``t``, and an
    inheritance child is listed and dumped as its own table.  A partitioned
    parent (``relkind = 'p'``) has no rows of its own, so it is read bare and
    its partitions come along (partitions are never listed).
    """
    relkind = connection.execute(
        "SELECT relkind FROM pg_class WHERE oid = %s::regclass", (f'public."{table}"',)
    ).fetchone()[0]
    target = sql.Identifier(table)
    return sql.SQL("ONLY {}").format(target) if relkind == "r" else target


def list_sequences(connection: psycopg.Connection) -> dict[str, list[Any]]:
    names = [
        row[0]
        for row in connection.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'S' ORDER BY c.relname"
        ).fetchall()
    ]
    sequences: dict[str, list[Any]] = {}
    for name in names:
        row = connection.execute(
            sql.SQL("SELECT last_value, is_called FROM {}").format(sql.Identifier(name))
        ).fetchone()
        sequences[name] = [int(row[0]), bool(row[1])]
    return sequences


def alembic_revisions(connection: psycopg.Connection) -> list[str]:
    exists = connection.execute(
        "SELECT to_regclass('public.alembic_version') IS NOT NULL"
    ).fetchone()[0]
    if not exists:
        return []
    return sorted(row[0] for row in connection.execute("SELECT version_num FROM alembic_version"))


def _has_column(columns: list[list[str]], name: str) -> bool:
    return any(column[0] == name for column in columns)


def count_rows(connection: psycopg.Connection, table: str) -> int:
    return int(
        connection.execute(
            sql.SQL("SELECT count(*) FROM {}").format(_from_target(connection, table))
        ).fetchone()[0]
    )


def tenant_counts(connection: psycopg.Connection, table: str) -> dict[str, int]:
    rows = connection.execute(
        sql.SQL("SELECT workspace_id, count(*) FROM {} GROUP BY workspace_id").format(
            _from_target(connection, table)
        )
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows if row[0] is not None}


@dataclass
class Snapshot:
    """An open, exported, read-only snapshot of the source database."""

    connection: psycopg.Connection
    snapshot_id: str
    database_time: str
    server_version: str
    wal_lsn: str | None
    tables: list[str]
    columns: dict[str, list[list[str]]] = field(default_factory=dict)

    def copy_out(self, table: str) -> Iterator[bytes]:
        columns = sql.SQL(", ").join(sql.Identifier(c[0]) for c in self.columns[table])
        # COPY (SELECT ...) also reads partitioned parents, which plain
        # COPY <table> TO refuses.  The FROM target (see _from_target) keeps
        # a partitioned parent reading its partitions (partitions are never
        # listed) and reads a plain table with ONLY, so an inheritance child
        # is not dumped again inside its parent's dump (it is listed itself).
        statement = sql.SQL("COPY (SELECT {} FROM {}) TO STDOUT").format(
            columns, _from_target(self.connection, table)
        )
        with self.connection.cursor() as cursor:
            with cursor.copy(statement) as copy:
                for data in copy:
                    yield bytes(data)

    def query(self, statement: str, params: tuple | None = None) -> list[tuple]:
        return self.connection.execute(statement, params).fetchall()


@contextmanager
def open_snapshot(database_url: str, *, lock_timeout_ms: int = 60000) -> Iterator[Snapshot]:
    """Lock every table, THEN take the snapshot (order matters).

    In ``REPEATABLE READ`` the snapshot is fixed by the first query, so the
    role check and the table list run on a separate short connection and
    the snapshot transaction issues only ``SET`` and ``LOCK`` (which take no
    snapshot) before its first ``SELECT``.  A migration that committed while
    the locks were awaited (a table rewrite is not MVCC-safe) is therefore
    visible to the snapshot, and the table list is compared again inside it.
    """
    with connect(database_url, autocommit=True) as probe:
        _require_bypass(probe, superuser=False)
        tables = list_tables(probe)
    connection = connect(database_url)
    try:
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        connection.read_only = True
        _settings(connection)
        # Fail on any row a policy would hide instead of silently dumping less.
        connection.execute("SET row_security = off")
        connection.execute(f"SET lock_timeout = {int(lock_timeout_ms)}")
        if tables:
            connection.execute(
                sql.SQL("LOCK TABLE {} IN ACCESS SHARE MODE").format(
                    sql.SQL(", ").join(sql.Identifier(t) for t in tables)
                )
            )
        snapshot_id, database_time, lsn = connection.execute(
            "SELECT pg_export_snapshot(), statement_timestamp(), "
            "CASE WHEN pg_is_in_recovery() THEN NULL ELSE pg_current_wal_lsn()::text END"
        ).fetchone()
        if list_tables(connection) != tables:
            raise DatabaseBackupError(
                "schema_changed", "tables were created or dropped while the backup started"
            )
        server_version = connection.execute("SHOW server_version").fetchone()[0]
        snapshot = Snapshot(
            connection=connection,
            snapshot_id=str(snapshot_id),
            database_time=database_time.isoformat(),
            server_version=str(server_version),
            wal_lsn=lsn,
            tables=tables,
        )
        for table in tables:
            snapshot.columns[table] = table_columns(connection, table)
        yield snapshot
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()


def describe_snapshot(snapshot: Snapshot) -> dict[str, Any]:
    """Counts and schema identity read inside the snapshot."""
    connection = snapshot.connection
    tables: dict[str, Any] = {}
    for table in snapshot.tables:
        if table in SKIP_TABLES:
            continue
        columns = snapshot.columns[table]
        entry: dict[str, Any] = {"columns": columns, "rows": count_rows(connection, table)}
        if _has_column(columns, "workspace_id"):
            entry["tenant_rows"] = tenant_counts(connection, table)
        tables[table] = entry
    return {
        "alembic_revisions": alembic_revisions(connection),
        "sequences": list_sequences(connection),
        "tables": tables,
    }


# --- load -------------------------------------------------------------------


def foreign_key_violations(connection: psycopg.Connection) -> list[dict[str, Any]]:
    """Every foreign key of ``public`` checked with an anti-join (MATCH SIMPLE)."""
    constraints = connection.execute(
        "SELECT con.conname, child.relname, parent.relname, "
        "ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(n, i) "
        "      JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n ORDER BY k.i), "
        "ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(n, i) "
        "      JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.n ORDER BY k.i) "
        "FROM pg_constraint con "
        "JOIN pg_class child ON child.oid = con.conrelid "
        "JOIN pg_class parent ON parent.oid = con.confrelid "
        "JOIN pg_namespace n ON n.oid = con.connamespace "
        "WHERE con.contype = 'f' AND n.nspname = 'public' ORDER BY con.conname"
    ).fetchall()
    violations = []
    for name, child, parent, child_columns, parent_columns in constraints:
        not_null = sql.SQL(" AND ").join(
            sql.SQL("c.{} IS NOT NULL").format(sql.Identifier(column)) for column in child_columns
        )
        joined = sql.SQL(" AND ").join(
            sql.SQL("p.{} = c.{}").format(sql.Identifier(p), sql.Identifier(c))
            for c, p in zip(child_columns, parent_columns, strict=True)
        )
        statement = sql.SQL(
            "SELECT count(*) FROM {child} c WHERE {not_null} AND NOT EXISTS "
            "(SELECT 1 FROM {parent} p WHERE {joined})"
        ).format(
            child=sql.Identifier(child),
            parent=sql.Identifier(parent),
            not_null=not_null,
            joined=joined,
        )
        count = int(connection.execute(statement).fetchone()[0])
        if count:
            violations.append({"constraint": name, "table": child, "rows": count})
    return violations


def load_tables(
    database_url: str,
    catalog: dict[str, Any],
    read_table: Callable[[str], Iterable[bytes]],
) -> dict[str, Any]:
    """Load every table of the catalog into a database migrated to its revision.

    One transaction: on any failure nothing stays loaded.  Triggers (and so
    foreign-key checks) are disabled while copying because rows of a
    self-referencing table arrive in storage order; every foreign key is
    then checked explicitly before the commit.  Returns per-table counts.
    """
    connection = connect(database_url)
    try:
        _settings(connection)
        _require_bypass(connection, superuser=True)
        connection.execute("SET row_security = off")
        present = set(list_tables(connection)) - SKIP_TABLES
        expected = set(catalog["tables"])
        if present != expected:
            raise DatabaseBackupError(
                "schema_mismatch",
                "tables differ from the backup at its revision: "
                f"missing={sorted(expected - present)} unexpected={sorted(present - expected)}",
            )
        for table in sorted(expected):
            # Order-insensitive: COPY names its columns, and a schema built by
            # migrations orders added columns differently from create_all.
            if dict(map(tuple, table_columns(connection, table))) != dict(
                map(tuple, catalog["tables"][table]["columns"])
            ):
                raise DatabaseBackupError(
                    "schema_mismatch", f"columns of {table} differ from the backup"
                )
        connection.execute("SET session_replication_role = replica")
        # Migrations may seed rows (plans, catalogs); the backup is authoritative.
        connection.execute(
            sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                sql.SQL(", ").join(sql.Identifier(t) for t in sorted(expected))
            )
        )
        loaded: dict[str, int] = {}
        with connection.cursor() as cursor:
            for table in sorted(expected):
                columns = sql.SQL(", ").join(
                    sql.Identifier(c[0]) for c in catalog["tables"][table]["columns"]
                )
                statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
                    sql.Identifier(table), columns
                )
                with cursor.copy(statement) as copy:
                    for chunk in read_table(table):
                        copy.write(chunk)
                loaded[table] = count_rows(connection, table)
        connection.execute("SET session_replication_role = origin")
        for name, (last_value, is_called) in catalog.get("sequences", {}).items():
            connection.execute(
                "SELECT setval(%s::regclass, %s, %s)", (f'public."{name}"', last_value, is_called)
            )
        mismatched = {
            table: {"expected": catalog["tables"][table]["rows"], "loaded": count}
            for table, count in loaded.items()
            if count != catalog["tables"][table]["rows"]
        }
        if mismatched:
            raise DatabaseBackupError("row_count_mismatch", f"loaded counts differ: {mismatched}")
        # Every tenant got back exactly its own rows (before anything mutates them).
        tenant_tables = [t for t in sorted(expected) if "tenant_rows" in catalog["tables"][t]]
        crossed = [
            table
            for table in tenant_tables
            if tenant_counts(connection, table) != catalog["tables"][table]["tenant_rows"]
        ]
        if crossed:
            raise DatabaseBackupError(
                "tenant_count_mismatch", f"per-tenant counts differ: {crossed}"
            )
        violations = foreign_key_violations(connection)
        if violations:
            raise DatabaseBackupError("foreign_key_violation", f"{violations}")
        connection.commit()
        return {
            "tables": len(loaded),
            "rows": sum(loaded.values()),
            "tenant_tables_verified": len(tenant_tables),
            "foreign_keys_verified": True,
            "per_table": loaded,
        }
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
