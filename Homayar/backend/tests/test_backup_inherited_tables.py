"""HOM-220: inheritance and partitioned tables in the logical backup.

A plain ``FROM <table>`` also reads the rows of every table that inherits
from it.  The backup lists an inheritance child as its own table, so the
child's rows were dumped and counted twice, and the restore check then
failed on the doubled parent count.  A partitioned parent is the opposite
case: it has no rows of its own, so it must keep reading its partitions
(partitions are never listed).

The schema is built by the test itself, not the application schema, and no
table carries a foreign key (that path is a separate follow-up).
"""

from __future__ import annotations

import secrets
from contextlib import contextmanager

import pytest

from backend.backup.logical import (
    connect,
    describe_snapshot,
    load_tables,
    open_snapshot,
    with_database,
)
from backend.tests._database import configured_database_url

pytestmark = pytest.mark.postgres

PARENT = "h220_parent"
CHILD = "h220_child"  # INHERITS (PARENT), so a plain FROM PARENT reads its rows
PLAIN = "h220_plain"  # control: no inheritance
EVENTS = "h220_events"  # partitioned parent
EVENTS_OLD = "h220_events_old"  # leaf partition
EVENTS_MID = "h220_events_mid"  # partition that is itself partitioned
EVENTS_MID_A = "h220_events_mid_a"  # leaf partition of EVENTS_MID
EVENTS_MID_B = "h220_events_mid_b"  # leaf partition of EVENTS_MID
PARTITIONS = (EVENTS_OLD, EVENTS_MID, EVENTS_MID_A, EVENTS_MID_B)
LEAVES = (EVENTS_OLD, EVENTS_MID_A, EVENTS_MID_B)

PARENT_ROWS = [(1, "a", "p1"), (2, "a", "p2"), (3, "b", "p3")]
CHILD_ROWS = [(4, "a", "c4", "e4"), (5, "b", "c5", "e5"), (6, "b", "c6", "e6")]
PLAIN_ROWS = [(10, "c", "plain10")]
# (id, workspace_id, payload, bucket): bucket 0-9 goes to EVENTS_OLD, 10-19 to
# EVENTS_MID, which routes by workspace_id to its two leaves.
EVENTS_ROWS = [
    (21, "a", "e21", 5),
    (22, "b", "e22", 7),
    (23, "a", "e23", 15),
    (24, "a", "e24", 15),
    (25, "b", "e25", 15),
]

PLAIN_DDL = (
    f"CREATE TABLE {PLAIN} (id int PRIMARY KEY, workspace_id text NOT NULL, payload text NOT NULL)"
)

SCHEMA = (
    f"CREATE TABLE {PARENT} (id int PRIMARY KEY, workspace_id text NOT NULL,"
    " payload text NOT NULL)",
    f"CREATE TABLE {CHILD} (extra text) INHERITS ({PARENT})",
    PLAIN_DDL,
    f"CREATE TABLE {EVENTS} (id int, workspace_id text NOT NULL, payload text NOT NULL,"
    " bucket int NOT NULL) PARTITION BY RANGE (bucket)",
    f"CREATE TABLE {EVENTS_OLD} PARTITION OF {EVENTS} FOR VALUES FROM (0) TO (10)",
    f"CREATE TABLE {EVENTS_MID} PARTITION OF {EVENTS} FOR VALUES FROM (10) TO (20)"
    " PARTITION BY LIST (workspace_id)",
    f"CREATE TABLE {EVENTS_MID_A} PARTITION OF {EVENTS_MID} FOR VALUES IN ('a')",
    f"CREATE TABLE {EVENTS_MID_B} PARTITION OF {EVENTS_MID} FOR VALUES IN ('b')",
)

# Every place rows physically live: 3 + 3 + 1 + 2 + 2 + 1 = 12 rows.
PHYSICAL_STORAGES = (f"ONLY {PARENT}", CHILD, PLAIN, *LEAVES)


@pytest.fixture
def databases():
    """Names of databases a test creates; dropped (with their connections) afterwards."""
    created: list[str] = []
    yield created
    admin = with_database(configured_database_url(), "postgres")
    with connect(admin, autocommit=True) as connection:
        for name in created:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _new_database(databases: list[str], prefix: str) -> str:
    name = f"hy_h220_{prefix}_{secrets.token_hex(4)}"
    databases.append(name)
    admin = with_database(configured_database_url(), "postgres")
    with connect(admin, autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')
    return name


def _source_database(databases: list[str]) -> str:
    url = with_database(configured_database_url(), _new_database(databases, "src"))
    with connect(url) as connection:
        for statement in SCHEMA:
            connection.execute(statement)
        inserts = (
            (f"INSERT INTO {PARENT} (id, workspace_id, payload) VALUES (%s, %s, %s)", PARENT_ROWS),
            (
                f"INSERT INTO {CHILD} (id, workspace_id, payload, extra) VALUES (%s, %s, %s, %s)",
                CHILD_ROWS,
            ),
            (f"INSERT INTO {PLAIN} (id, workspace_id, payload) VALUES (%s, %s, %s)", PLAIN_ROWS),
            (
                f"INSERT INTO {EVENTS} (id, workspace_id, payload, bucket) VALUES (%s, %s, %s, %s)",
                EVENTS_ROWS,
            ),
        )
        for statement, rows in inserts:
            for row in rows:
                connection.execute(statement, row)
        connection.commit()
    return url


def _target_database(databases: list[str]) -> str:
    """The same tables, empty."""
    url = with_database(configured_database_url(), _new_database(databases, "tgt"))
    with connect(url) as connection:
        for statement in SCHEMA:
            connection.execute(statement)
        connection.commit()
    return url


def _plain_target_database(databases: list[str]) -> str:
    """Only the plain control table, empty (AC5 must pass on the unfixed base)."""
    url = with_database(configured_database_url(), _new_database(databases, "plain"))
    with connect(url) as connection:
        connection.execute(PLAIN_DDL)
        connection.commit()
    return url


@contextmanager
def _dumped(database_url: str):
    """The dump of every listed table plus the catalog, in one snapshot."""
    with open_snapshot(database_url) as snapshot:
        dumps = {table: b"".join(snapshot.copy_out(table)) for table in snapshot.tables}
        yield snapshot, dumps, describe_snapshot(snapshot)


def _copy(database_url: str, query: str) -> bytes:
    """Oracle dump of a query, straight from the server."""
    with connect(database_url) as connection:
        with connection.cursor() as cursor:
            buffer = bytearray()
            with cursor.copy(f"COPY ({query}) TO STDOUT") as copy:
                for chunk in copy:
                    buffer.extend(chunk)
        connection.rollback()
    return bytes(buffer)


def _scalar(database_url: str, query: str) -> int:
    with connect(database_url) as connection:
        value = connection.execute(query).fetchone()[0]
        connection.rollback()
    return int(value)


def _rows(database_url: str, query: str) -> list[tuple]:
    with connect(database_url) as connection:
        rows = connection.execute(query).fetchall()
        connection.rollback()
    return [tuple(row) for row in rows]


def _physical_rows(database_url: str) -> int:
    return sum(
        _scalar(database_url, f"SELECT count(*) FROM {target}") for target in PHYSICAL_STORAGES
    )


def test_dump_reads_each_inheritance_table_once(databases):
    source = _source_database(databases)
    with _dumped(source) as (_, dumps, _):
        assert dumps[PARENT] == _copy(
            source, f"SELECT id, workspace_id, payload FROM ONLY {PARENT}"
        )
        assert dumps[CHILD] == _copy(
            source, f"SELECT id, workspace_id, payload, extra FROM {CHILD}"
        )
        # Across the whole dump every physical row appears exactly once.
        assert sum(len(dump.splitlines()) for dump in dumps.values()) == _physical_rows(source)


def test_catalog_counts_own_rows_only(databases):
    source = _source_database(databases)
    with _dumped(source) as (_, _, catalog):
        tables = catalog["tables"]
        assert tables[PARENT]["rows"] == 3
        assert tables[PARENT]["tenant_rows"] == {"a": 2, "b": 1}
        assert tables[CHILD]["rows"] == 3
        assert tables[CHILD]["tenant_rows"] == {"a": 1, "b": 2}
        # No table's count covers another table's storage.
        assert sum(entry["rows"] for entry in tables.values()) == _physical_rows(source)


def test_partitioned_parent_dumps_every_leaf_once(databases):
    source = _source_database(databases)
    with _dumped(source) as (snapshot, dumps, catalog):
        assert EVENTS in snapshot.tables
        assert not set(PARTITIONS) & set(snapshot.tables)
        assert dumps[EVENTS] == _copy(
            source, f"SELECT id, workspace_id, payload, bucket FROM {EVENTS}"
        )
        assert catalog["tables"][EVENTS]["rows"] == len(EVENTS_ROWS)
        leaf_counts = {leaf: _scalar(source, f"SELECT count(*) FROM {leaf}") for leaf in LEAVES}
        assert leaf_counts == {EVENTS_OLD: 2, EVENTS_MID_A: 2, EVENTS_MID_B: 1}
        assert len(dumps[EVENTS].splitlines()) == sum(leaf_counts.values())


def test_restore_matches_source(databases):
    source = _source_database(databases)
    target = _target_database(databases)
    with _dumped(source) as (_, dumps, catalog):
        result = load_tables(target, catalog, lambda table: [dumps[table]])
    assert result["per_table"] == {name: entry["rows"] for name, entry in catalog["tables"].items()}
    assert result["rows"] == _physical_rows(source)
    compared = (
        f"SELECT id, workspace_id, payload FROM ONLY {PARENT} ORDER BY id",
        f"SELECT id, workspace_id, payload, extra FROM {CHILD} ORDER BY id",
        f"SELECT id, workspace_id, payload FROM {PLAIN} ORDER BY id",
        *(f"SELECT id, workspace_id, payload, bucket FROM {leaf} ORDER BY id" for leaf in LEAVES),
    )
    for query in compared:
        assert _rows(source, query) == _rows(target, query), query


def test_plain_table_round_trip(databases):
    source = _source_database(databases)
    target = _plain_target_database(databases)
    with _dumped(source) as (_, dumps, catalog):
        assert dumps[PLAIN] == _copy(source, f"SELECT id, workspace_id, payload FROM {PLAIN}")
        assert catalog["tables"][PLAIN]["rows"] == 1
        assert catalog["tables"][PLAIN]["tenant_rows"] == {"c": 1}
        plain_only = {
            "alembic_revisions": catalog["alembic_revisions"],
            "sequences": catalog["sequences"],
            "tables": {PLAIN: catalog["tables"][PLAIN]},
        }
        result = load_tables(target, plain_only, lambda table: [dumps[table]])
    assert result["per_table"] == {PLAIN: 1}
    query = f"SELECT id, workspace_id, payload FROM {PLAIN} ORDER BY id"
    assert _rows(source, query) == _rows(target, query)
