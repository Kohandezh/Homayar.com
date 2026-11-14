"""HOM-347: partition and inheritance follow-ups after HOM-220.

Three gaps: false foreign-key violations (per-partition clones of a foreign
key, and inheritance rows read without ``ONLY``), inheritance children that
``list_tables`` does not list (another schema, a foreign table) silently
missing from a dump, and reconcile counts that read an inheritance parent
without ``ONLY`` so they disagree with the dump.

Fix 7: the staging/live advisory lock of a restore run is freed synchronously
when the lock block exits, not only when the connection's server backend
exits later.
"""

from __future__ import annotations

import secrets
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import psycopg
import pytest
from psycopg import sql

from backend.backup import restore as restore_module
from backend.backup.logical import (
    DatabaseBackupError,
    connect,
    describe_snapshot,
    foreign_key_violations,
    load_tables,
    open_snapshot,
    with_database,
)
from backend.backup.reconcile import (
    _non_resurrection,
    _post_backup_state,
    _tenant_boundaries,
    _visible,
)
from backend.backup.restore import RestoreRefused, live_lock, staging_lock
from backend.tests._database import configured_database_url

pytestmark = pytest.mark.postgres

BUCKETS = "h347_buckets"
BUCKETS_LO = "h347_buckets_lo"
BUCKETS_HI = "h347_buckets_hi"
ORDERS = "h347_orders"

FK_SCHEMA = (
    f"CREATE TABLE {BUCKETS} (id int PRIMARY KEY, payload text NOT NULL) PARTITION BY RANGE (id)",
    f"CREATE TABLE {BUCKETS_LO} PARTITION OF {BUCKETS} FOR VALUES FROM (0) TO (10)",
    f"CREATE TABLE {BUCKETS_HI} PARTITION OF {BUCKETS} FOR VALUES FROM (10) TO (20)",
    f"CREATE TABLE {ORDERS} (id int PRIMARY KEY, bucket_id int NOT NULL REFERENCES {BUCKETS}(id))",
)

ORGS = "h347_orgs"
MEMBERS = "h347_members"
MEMBERS_EXTRA = "h347_members_extra"

INHERIT_FK_SCHEMA = (
    f"CREATE TABLE {ORGS} (id int PRIMARY KEY)",
    f"CREATE TABLE {MEMBERS} (id int PRIMARY KEY, org_id int NOT NULL REFERENCES {ORGS}(id))",
    f"CREATE TABLE {MEMBERS_EXTRA} (extra text) INHERITS ({MEMBERS})",
)


@pytest.fixture
def databases():
    created: list[str] = []
    yield created
    admin = with_database(configured_database_url(), "postgres")
    with connect(admin, autocommit=True) as connection:
        for name in created:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _new_database(databases: list[str], prefix: str) -> str:
    name = f"hy_h347_{prefix}_{secrets.token_hex(4)}"
    databases.append(name)
    admin = with_database(configured_database_url(), "postgres")
    with connect(admin, autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')
    return name


def _database(databases: list[str], prefix: str, statements: tuple[str, ...]) -> str:
    url = with_database(configured_database_url(), _new_database(databases, prefix))
    with connect(url) as connection:
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    return url


@contextmanager
def _dumped(database_url: str):
    with open_snapshot(database_url) as snapshot:
        dumps = {table: b"".join(snapshot.copy_out(table)) for table in snapshot.tables}
        yield snapshot, dumps, describe_snapshot(snapshot)


def _scalar(database_url: str, query: str) -> int:
    with connect(database_url) as connection:
        value = connection.execute(query).fetchone()[0]
        connection.rollback()
    return int(value)


# --- fix 1: foreign keys across partitions and inheritance parents ----------


def _fk_source_database(databases: list[str], extra_orders: tuple[tuple, ...] = ()) -> str:
    statements = FK_SCHEMA + (
        f"INSERT INTO {BUCKETS} (id, payload) VALUES (1, 'lo'), (11, 'hi')",
        f"INSERT INTO {ORDERS} (id, bucket_id) VALUES (101, 1), (102, 11)",
    )
    url = _database(databases, "fksrc", statements)
    if extra_orders:
        with connect(url) as connection:
            connection.execute("SET session_replication_role = replica")
            for row in extra_orders:
                connection.execute(f"INSERT INTO {ORDERS} (id, bucket_id) VALUES {row}")
            connection.commit()
    return url


def test_fk_to_partitioned_table_with_valid_rows_reports_no_violation(databases):
    source = _fk_source_database(databases)
    assert _scalar(source, f"SELECT count(*) FROM {BUCKETS_LO}") == 1
    assert _scalar(source, f"SELECT count(*) FROM {BUCKETS_HI}") == 1
    with connect(source) as connection:
        assert foreign_key_violations(connection) == []
        connection.rollback()


def test_a_real_orphan_on_a_partitioned_fk_target_is_reported_once(databases):
    source = _fk_source_database(databases, extra_orders=((103, 5),))
    with connect(source) as connection:
        name = connection.execute(
            "SELECT conname FROM pg_constraint WHERE contype = 'f' AND conparentid = 0"
            f" AND conrelid = 'public.{ORDERS}'::regclass"
        ).fetchone()[0]
        assert foreign_key_violations(connection) == [
            {"constraint": name, "table": ORDERS, "rows": 1}
        ]
        connection.rollback()


def test_restore_of_a_database_with_fk_to_partitioned_table_commits(databases):
    source = _fk_source_database(databases)
    target = _database(databases, "fktgt", FK_SCHEMA)
    with _dumped(source) as (_, dumps, catalog):
        result = load_tables(target, catalog, lambda table: [dumps[table]])
    assert result["per_table"] == {name: entry["rows"] for name, entry in catalog["tables"].items()}


def test_fk_on_inheritance_parent_covers_only_its_own_rows(databases):
    statements = INHERIT_FK_SCHEMA + (
        f"INSERT INTO {ORGS} (id) VALUES (1)",
        f"INSERT INTO {MEMBERS} (id, org_id) VALUES (1, 1)",
        f"INSERT INTO {MEMBERS_EXTRA} (id, org_id, extra) VALUES (2, 2, 'x')",
    )
    url = _database(databases, "inhfk", statements)
    with connect(url) as connection:
        assert foreign_key_violations(connection) == []
        name = connection.execute(
            "SELECT conname FROM pg_constraint WHERE contype = 'f' AND conparentid = 0"
            f" AND conrelid = 'public.{MEMBERS}'::regclass"
        ).fetchone()[0]
        connection.execute("SET session_replication_role = replica")
        connection.execute(f"INSERT INTO {MEMBERS} (id, org_id) VALUES (3, 3)")
        connection.commit()
        connection.execute("SET session_replication_role = origin")
        assert foreign_key_violations(connection) == [
            {"constraint": name, "table": MEMBERS, "rows": 1}
        ]
        connection.rollback()


# --- fix 2: unlisted inheritance children refused -----------------------------


PLAIN = "h347_plain"


def test_open_snapshot_refuses_unlisted_inheritance_children_in_other_schemas(databases):
    statements = (
        f"CREATE TABLE {PLAIN} (id int PRIMARY KEY, payload text NOT NULL)",
        "CREATE SCHEMA h347_a",
        "CREATE SCHEMA h347_b",
        "CREATE TABLE h347_a.h347_kid_z (extra text) INHERITS (public.h347_plain)",
        "CREATE TABLE h347_b.h347_kid_a (extra text) INHERITS (public.h347_plain)",
    )
    url = _database(databases, "unlisted", statements)
    with pytest.raises(DatabaseBackupError) as error:
        with open_snapshot(url):
            pass
    assert error.value.code == "unlisted_inheritance_child"
    message = error.value.message
    assert "h347_a.h347_kid_z" in message
    assert "h347_b.h347_kid_a" in message
    assert message.index("h347_a.h347_kid_z") < message.index("h347_b.h347_kid_a")
    assert "public.h347_plain" in message


FT_PARENT = "h347_ft_parent"
FT_CHILD = "h347_ft_kid"


def test_open_snapshot_refuses_a_foreign_table_inheritance_child(databases):
    statements = (
        "CREATE EXTENSION postgres_fdw",
        "CREATE SERVER h347_srv FOREIGN DATA WRAPPER postgres_fdw",
        f"CREATE TABLE {FT_PARENT} (id int PRIMARY KEY, payload text NOT NULL)",
        f"CREATE FOREIGN TABLE {FT_CHILD} () INHERITS ({FT_PARENT}) SERVER h347_srv",
    )
    url = _database(databases, "ftchild", statements)
    with pytest.raises(DatabaseBackupError) as error:
        with open_snapshot(url):
            pass
    assert error.value.code == "unlisted_inheritance_child"
    assert f"public.{FT_CHILD}" in error.value.message
    assert f"public.{FT_PARENT}" in error.value.message


def test_open_snapshot_allows_a_listed_public_child(databases):
    statements = (
        f"CREATE TABLE {PLAIN} (id int PRIMARY KEY, payload text NOT NULL)",
        f"CREATE TABLE h347_plain_kid (extra text) INHERITS ({PLAIN})",
        f"INSERT INTO {PLAIN} (id, payload) VALUES (1, 'p'), (2, 'q')",
        "INSERT INTO h347_plain_kid (id, payload, extra) VALUES (3, 'r', 'x')",
    )
    url = _database(databases, "listkid", statements)
    with _dumped(url) as (snapshot, _, catalog):
        assert {PLAIN, "h347_plain_kid"} <= set(snapshot.tables)
        assert catalog["tables"][PLAIN]["rows"] == 2
        assert catalog["tables"]["h347_plain_kid"]["rows"] == 1


EVENTS = "h347_events"


def test_open_snapshot_allows_partitions_of_a_partitioned_parent(databases):
    statements = (
        f"CREATE TABLE {EVENTS} (id int, bucket int NOT NULL) PARTITION BY RANGE (bucket)",
        f"CREATE TABLE {EVENTS}_lo PARTITION OF {EVENTS} FOR VALUES FROM (0) TO (10)",
        "CREATE SCHEMA h347_parts",
        f"CREATE TABLE h347_parts.{EVENTS}_hi PARTITION OF {EVENTS} FOR VALUES FROM (10) TO (20)",
        f"INSERT INTO {EVENTS} VALUES (1, 1), (11, 11)",
    )
    url = _database(databases, "parts", statements)
    with _dumped(url) as (snapshot, _, catalog):
        assert snapshot.tables == [EVENTS]
        assert catalog["tables"][EVENTS]["rows"] == 2


# --- fix 3: reconcile counts read own rows only -------------------------------


VIS = "h347_vis"
VIS_KID = "h347_vis_kid"


def test_visible_counts_own_rows_of_an_inheritance_parent(databases):
    statements = (
        f"CREATE TABLE {VIS} (id int PRIMARY KEY, workspace_id text NOT NULL)",
        f"CREATE TABLE {VIS_KID} (extra text) INHERITS ({VIS})",
        f"INSERT INTO {VIS} (id, workspace_id) VALUES (1, 'a'), (2, 'b')",
        f"INSERT INTO {VIS_KID} (id, workspace_id, extra) VALUES"
        " (3, 'c', 'x'), (4, 'd', 'y'), (5, 'e', 'z')",
    )
    url = _database(databases, "vis", statements)
    with connect(url) as connection:
        assert _visible(connection, VIS, sql.SQL("true")) == 2
        connection.rollback()


def test_post_backup_state_counts_own_rows_of_an_inheritance_parent(databases):
    statements = (
        "CREATE TABLE share_links (id int PRIMARY KEY, revoked_at timestamptz)",
        "CREATE TABLE share_links_kid (extra text) INHERITS (share_links)",
        "INSERT INTO share_links (id, revoked_at) VALUES (1, NULL), (2, now())",
        "INSERT INTO share_links_kid (id, revoked_at, extra) VALUES"
        " (3, NULL, 'a'), (4, now(), 'b'), (5, NULL, 'c')",
    )
    url = _database(databases, "pbs", statements)
    result = _post_backup_state(url, revoke_share_links=False, now=datetime.now(UTC))
    assert result["details"]["counts_before"]["active_share_links"] == 1


NOTES = "h347_notes"
NOTES_KID = "h347_notes_kid"
PART_TARGET = "h347_ptarget"
PART_LINKS = "h347_plinks"


def test_tenant_boundaries_reports_a_child_row_under_the_child_only(databases):
    statements = (
        "CREATE TABLE workspaces (id text PRIMARY KEY, organization_id text)",
        "CREATE TABLE meetings (id int PRIMARY KEY, workspace_id text NOT NULL"
        " REFERENCES workspaces(id))",
        f"CREATE TABLE {NOTES} (id int PRIMARY KEY, workspace_id text NOT NULL,"
        " meeting_id int NOT NULL REFERENCES meetings(id), organization_id text)",
        f"CREATE TABLE {NOTES_KID} (extra text) INHERITS ({NOTES})",
        f"CREATE TABLE {PART_TARGET} (id int PRIMARY KEY, workspace_id text NOT NULL)"
        " PARTITION BY RANGE (id)",
        f"CREATE TABLE {PART_TARGET}_0 PARTITION OF {PART_TARGET} FOR VALUES FROM (0) TO (10)",
        f"CREATE TABLE {PART_TARGET}_1 PARTITION OF {PART_TARGET} FOR VALUES FROM (10) TO (20)",
        f"CREATE TABLE {PART_LINKS} (id int PRIMARY KEY, workspace_id text NOT NULL,"
        f" target_id int NOT NULL REFERENCES {PART_TARGET}(id))",
        "CREATE TABLE h347_ftargets (id int PRIMARY KEY, workspace_id text NOT NULL)",
        "CREATE TABLE h347_ftargets_kid (extra text) INHERITS (h347_ftargets)",
        "CREATE TABLE h347_flinks (id int PRIMARY KEY, workspace_id text NOT NULL,"
        " ftarget_id int NOT NULL REFERENCES h347_ftargets(id))",
        "CREATE TABLE media_objects (id int PRIMARY KEY, workspace_id text NOT NULL,"
        " storage_key text NOT NULL)",
        "CREATE TABLE recordings (extra text) INHERITS (media_objects)",
        "CREATE SCHEMA other",
        "CREATE TABLE pt (id int PRIMARY KEY, workspace_id text NOT NULL) PARTITION BY RANGE (id)",
        "CREATE TABLE other.decoy PARTITION OF pt FOR VALUES FROM (0) TO (100)",
        "CREATE TABLE decoy (id int PRIMARY KEY, workspace_id text NOT NULL)",
        "CREATE TABLE links (id int PRIMARY KEY, workspace_id text NOT NULL,"
        " target_id int NOT NULL REFERENCES pt(id))",
        "INSERT INTO workspaces (id, organization_id) VALUES ('w1', 'orgA'), ('w2', 'orgB')",
        "INSERT INTO meetings (id, workspace_id) VALUES (1, 'w1'), (2, 'w2')",
        f"INSERT INTO {NOTES} (id, workspace_id, meeting_id, organization_id)"
        " VALUES (10, 'w1', 1, 'orgA')",
        f"INSERT INTO {NOTES_KID} (id, workspace_id, meeting_id, organization_id, extra)"
        " VALUES (11, 'w1', 2, 'orgB', 'x')",
        f"INSERT INTO {PART_TARGET} (id, workspace_id) VALUES (1, 'w1'), (11, 'w2')",
        f"INSERT INTO {PART_LINKS} (id, workspace_id, target_id)"
        " VALUES (20, 'w1', 1), (21, 'w2', 11)",
        "INSERT INTO h347_ftargets (id, workspace_id) VALUES (30, 'w1')",
        "INSERT INTO h347_ftargets_kid (id, workspace_id, extra) VALUES (31, 'w2', 'x')",
        "INSERT INTO media_objects (id, workspace_id, storage_key)"
        " VALUES (50, 'w1', 'tenants/w1/objects/a')",
        "INSERT INTO recordings (id, workspace_id, storage_key, extra)"
        " VALUES (51, 'w2', 'tenants/w1/objects/stolen', 'x')",
        "INSERT INTO pt (id, workspace_id) VALUES (1, 'w1')",
        "INSERT INTO decoy (id, workspace_id) VALUES (1, 'w2')",
        "INSERT INTO links (id, workspace_id, target_id) VALUES (100, 'w1', 1)",
    )
    url = _database(databases, "tb", statements)
    with connect(url) as connection:
        connection.execute("SET session_replication_role = replica")
        connection.execute(
            "INSERT INTO h347_flinks (id, workspace_id, ftarget_id) VALUES (40, 'w1', 31)"
        )
        connection.commit()
        connection.execute("SET session_replication_role = origin")
        connection.rollback()
    check = _tenant_boundaries(url, {"tenant_tables_verified": 1, "foreign_keys_verified": True})
    assert check["status"] == "FAIL"
    assert check["details"]["problems"] == [
        {"table": NOTES_KID, "problem": "meeting of another tenant", "rows": 1},
        {"table": NOTES_KID, "problem": "organization differs from tenant's", "rows": 1},
        {"table": "recordings", "problem": "object key outside tenant prefix", "rows": 1},
    ]


def test_non_resurrection_counts_own_content_rows_of_an_inheritance_parent(databases):
    statements = (
        "CREATE TABLE meetings (id text PRIMARY KEY, workspace_id text NOT NULL,"
        " deleted_at timestamptz)",
        "CREATE TABLE transcript_revisions (id int PRIMARY KEY, meeting_id text NOT NULL)",
        "CREATE TABLE transcript_revisions_kid (extra text) INHERITS (transcript_revisions)",
        "INSERT INTO meetings (id, workspace_id, deleted_at) VALUES ('m1', 'w1', NULL)",
        "INSERT INTO transcript_revisions (id, meeting_id) VALUES (1, 'm1')",
        "INSERT INTO transcript_revisions_kid (id, meeting_id, extra) VALUES (2, 'm1', 'x')",
    )
    url = _database(databases, "nr", statements)
    record = SimpleNamespace(meeting_id="m1", workspace_id="w1", object_keys=[])
    check = _non_resurrection(url, None, [record])
    assert check["details"]["content_rows"] == [
        {"meeting_id": "m1", "table": "transcript_revisions", "rows": 1}
    ]
    assert check["details"]["alive"] == ["m1"]


INHERIT_REF_SCHEMA = (
    "CREATE TABLE h347_targets (id int PRIMARY KEY)",
    "CREATE TABLE h347_targets_kid (extra text) INHERITS (h347_targets)",
    "CREATE TABLE h347_refs (id int PRIMARY KEY, target_id int NOT NULL"
    " REFERENCES h347_targets(id))",
)


def test_fk_referencing_an_inheritance_parent_sees_only_its_own_rows(databases):
    statements = INHERIT_REF_SCHEMA + (
        "INSERT INTO h347_targets (id) VALUES (1)",
        "INSERT INTO h347_targets_kid (id, extra) VALUES (2, 'x')",
        "INSERT INTO h347_refs (id, target_id) VALUES (101, 1)",
    )
    url = _database(databases, "inhref", statements)
    with connect(url) as connection:
        name = connection.execute(
            "SELECT conname FROM pg_constraint WHERE contype = 'f' AND conparentid = 0"
            " AND conrelid = 'public.h347_refs'::regclass"
        ).fetchone()[0]
        connection.execute("SET session_replication_role = replica")
        connection.execute("INSERT INTO h347_refs (id, target_id) VALUES (102, 2)")
        connection.commit()
        connection.execute("SET session_replication_role = origin")
        assert foreign_key_violations(connection) == [
            {"constraint": name, "table": "h347_refs", "rows": 1}
        ]
        connection.rollback()


PART_FK_SCHEMA = (
    "CREATE TABLE h347_porgs (id int PRIMARY KEY)",
    "CREATE TABLE h347_pmembers (id int, bucket int NOT NULL,"
    " org_id int NOT NULL REFERENCES h347_porgs(id)) PARTITION BY RANGE (bucket)",
    "CREATE TABLE h347_pmembers_lo PARTITION OF h347_pmembers FOR VALUES FROM (0) TO (10)",
    "CREATE TABLE h347_pmembers_hi PARTITION OF h347_pmembers FOR VALUES FROM (10) TO (20)",
    "INSERT INTO h347_porgs (id) VALUES (1)",
)


def test_fk_from_a_partitioned_table_reports_an_orphan_once(databases):
    url = _database(
        databases,
        "pfk",
        PART_FK_SCHEMA
        + ("INSERT INTO h347_pmembers (id, bucket, org_id) VALUES (101, 1, 1), (111, 11, 1)",),
    )
    assert _scalar(url, "SELECT count(*) FROM h347_pmembers_lo") == 1
    assert _scalar(url, "SELECT count(*) FROM h347_pmembers_hi") == 1
    with connect(url) as connection:
        assert foreign_key_violations(connection) == []
        name = connection.execute(
            "SELECT conname FROM pg_constraint WHERE contype = 'f' AND conparentid = 0"
            " AND conrelid = 'public.h347_pmembers'::regclass"
        ).fetchone()[0]
        connection.execute("SET session_replication_role = replica")
        connection.execute("INSERT INTO h347_pmembers (id, bucket, org_id) VALUES (102, 2, 9)")
        connection.commit()
        connection.execute("SET session_replication_role = origin")
        assert foreign_key_violations(connection) == [
            {"constraint": name, "table": "h347_pmembers", "rows": 1}
        ]
        connection.rollback()


def test_open_snapshot_refuses_an_unlisted_grandchild(databases):
    statements = (
        "CREATE TABLE h347_grand (id int PRIMARY KEY, payload text NOT NULL)",
        "CREATE TABLE h347_grand_kid (extra text) INHERITS (h347_grand)",
        "CREATE SCHEMA h347_gc",
        "CREATE TABLE h347_gc.h347_grand_great (e2 text) INHERITS (public.h347_grand_kid)",
    )
    url = _database(databases, "grand", statements)
    with pytest.raises(DatabaseBackupError) as error:
        with open_snapshot(url):
            pass
    assert error.value.code == "unlisted_inheritance_child"
    assert "h347_gc.h347_grand_great" in error.value.message
    assert "public.h347_grand_kid" in error.value.message


# --- fix 7: the staging advisory lock is freed synchronously --------------------


class _HeldLockConnection:
    def __init__(self, connection, *, unlock_error: BaseException | None, record: dict) -> None:
        self._connection = connection
        self._unlock_error = unlock_error
        self._record = record

    def execute(self, query, *args, **kwargs):
        self._record["statements"].append(str(query))
        if self._unlock_error is not None and "pg_advisory_unlock" in str(query):
            raise self._unlock_error
        return self._connection.execute(query, *args, **kwargs)

    def close(self) -> None:
        self._record["closed"] = True


def _hold_advisory_lock_connections(
    monkeypatch, *, unlock_error: BaseException | None = None
) -> list:
    """``_advisory_lock``'s connections stay open after ``close`` (their server
    backend has not exited yet) and record every statement and the close."""
    held: list = []
    real_connect = restore_module.connect

    def connect_and_hold(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        record: dict = {"statements": [], "closed": False}
        held.append((connection, record))
        return _HeldLockConnection(connection, unlock_error=unlock_error, record=record)

    monkeypatch.setattr(restore_module, "connect", connect_and_hold)
    return held


def _lock_free_for_another_session(name: str) -> bool:
    admin = with_database(configured_database_url(), "postgres")
    with connect(admin, autocommit=True) as connection:
        taken = bool(
            connection.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (name,)).fetchone()[0]
        )
        if taken:
            connection.execute("SELECT pg_advisory_unlock(hashtext(%s))", (name,))
    return taken


def test_staging_lock_is_freed_when_the_block_exits(monkeypatch):
    held = _hold_advisory_lock_connections(monkeypatch)
    with staging_lock(configured_database_url(), "h347_stg"):
        assert not _lock_free_for_another_session("homayar-restore:h347_stg")
    assert held[0][1]["closed"] is True
    assert _lock_free_for_another_session("homayar-restore:h347_stg")
    held[0][0].close()


def test_live_lock_is_freed_when_the_block_exits(monkeypatch):
    held = _hold_advisory_lock_connections(monkeypatch)
    with live_lock(configured_database_url(), "h347_live"):
        pass
    assert _lock_free_for_another_session("homayar-live:h347_live")
    held[0][0].close()


def test_staging_lock_is_freed_when_the_block_exits_by_an_exception(monkeypatch):
    held = _hold_advisory_lock_connections(monkeypatch)
    with pytest.raises(RuntimeError, match="boom"):
        with staging_lock(configured_database_url(), "h347_stg_boom"):
            raise RuntimeError("boom")
    assert held[0][1]["closed"] is True
    assert _lock_free_for_another_session("homayar-restore:h347_stg_boom")
    held[0][0].close()


def test_a_second_staging_lock_on_the_same_name_is_refused_while_it_runs(monkeypatch):
    held = _hold_advisory_lock_connections(monkeypatch)
    with staging_lock(configured_database_url(), "h347_busy"):
        with pytest.raises(RestoreRefused) as busy:
            with staging_lock(configured_database_url(), "h347_busy"):
                pass
        assert busy.value.code == "staging_busy"
        with staging_lock(configured_database_url(), "h347_other"):
            pass
    for connection, _ in held:
        connection.close()


def test_no_unlock_is_called_when_the_lock_was_not_acquired(monkeypatch):
    held = _hold_advisory_lock_connections(monkeypatch)
    admin = with_database(configured_database_url(), "postgres")
    with connect(admin, autocommit=True) as blocker:
        blocker.execute("SELECT pg_advisory_lock(hashtext(%s))", ("homayar-restore:h347_taken",))
        assert not _lock_free_for_another_session("homayar-restore:h347_taken")
        with pytest.raises(RestoreRefused) as busy:
            with staging_lock(configured_database_url(), "h347_taken"):
                pass
        assert busy.value.code == "staging_busy"
    assert not any("pg_advisory_unlock" in statement for statement in held[0][1]["statements"])
    held[0][0].close()


def test_a_failing_unlock_still_closes_the_connection_and_finishes_the_run(monkeypatch):
    held = _hold_advisory_lock_connections(
        monkeypatch, unlock_error=psycopg.OperationalError("unlock failed")
    )
    with staging_lock(configured_database_url(), "h347_unlock_fail"):
        pass
    record = held[0][1]
    assert any("pg_advisory_unlock" in s for s in record["statements"])
    assert record["closed"] is True
    held[0][0].close()


def test_a_failing_unlock_never_hides_a_body_exception(monkeypatch):
    held = _hold_advisory_lock_connections(
        monkeypatch, unlock_error=psycopg.OperationalError("unlock failed")
    )
    with pytest.raises(RuntimeError, match="boom"):
        with staging_lock(configured_database_url(), "h347_boom_unlock"):
            raise RuntimeError("boom")
    assert held[0][1]["closed"] is True
    held[0][0].close()


def test_the_connection_is_closed_when_the_unlock_raises_keyboardinterrupt(monkeypatch):
    held = _hold_advisory_lock_connections(monkeypatch, unlock_error=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        with staging_lock(configured_database_url(), "h347_ki_unlock"):
            raise ValueError("boom")
    assert held[0][1]["closed"] is True
    held[0][0].close()
