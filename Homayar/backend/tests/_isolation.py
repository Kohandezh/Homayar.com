"""Build the PostgreSQL test schema once per process, reset rows between tests (HOM-292).

Before, every test dropped every table and the app's lifespan created the schema and
its row-level security again (about 2 s per test on CI).  Now:

* ``canonical_database_url`` (the ``settings`` fixture of a test that uses ``client``)
  compares a catalog fingerprint with the one taken right after the last full build.
  Equal: only the rows are removed.  Different (a test or a fixture changed the schema,
  ran a migration, dropped the tables, or changed a role or a database setting): a full
  build, which is the base drop followed by the real
  ``create_schema_for_test_or_development``.  No marker is needed.
* ``create_schema_unless_canonical`` replaces the app lifespan's schema call in the test
  process.  It skips the DDL only for the configured test URL when the fingerprint taken
  at that moment equals the canonical one; the real function is idempotent, so on that
  schema it would change nothing.
* A test with ``settings`` but without ``client`` keeps the base start state (no tables).

The fingerprint reads the catalogs as text (never OIDs): schemas, relations, columns,
constraints, indexes, policies, triggers, functions, types, rules and views, sequences,
default privileges, extensions and event triggers, plus the cluster-wide roles,
memberships and the role and database settings that apply to this database.  A check
first compares the same catalog rows in raw form (with OIDs, about half the cost); equal
raw rows give an equal text fingerprint, and when they differ the text fingerprint
decides.
"""

# ruff: noqa: S608 - the SQL is built from catalog names (quote_ident, regclass) and
# constants of this module, never from test or user input.

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.pool import NullPool

from backend.config import Settings, get_settings
from backend.database import relational
from backend.database.rls import audit_reader_statements
from backend.tests._database import (
    TEST_APP_ROLE,
    check_engine,
    configured_database_url,
    dispose_inspection_engine,
    is_postgres_url,
    reset_postgres_schema,
    test_database_url,
)

_USER_SCHEMAS = """
  SELECT oid, nspname FROM pg_namespace
   WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
     AND nspname NOT LIKE 'pg\\_temp\\_%' AND nspname NOT LIKE 'pg\\_toast\\_temp\\_%'
"""

_FINGERPRINT_LINES = f"""
WITH ns AS ({_USER_SCHEMAS}),
rel AS (SELECT c.* FROM pg_class c JOIN ns ON ns.oid = c.relnamespace),
current_db AS (SELECT oid FROM pg_database WHERE datname = current_database()),
parts AS (
  SELECT 'n' || ROW(n.nspname, pg_get_userbyid(n.nspowner), n.nspacl)::text AS line
    FROM pg_namespace n JOIN ns USING (oid)
  UNION ALL
  SELECT 'c' || ROW(c.relnamespace::regnamespace::text, c.relname, c.relkind,
         pg_get_userbyid(c.relowner), c.relacl, c.relrowsecurity, c.relforcerowsecurity,
         c.relpersistence, c.reloptions, c.relreplident, c.relispartition)::text
    FROM rel c
  UNION ALL
  SELECT 'a' || ROW(a.attrelid::regclass::text, a.attnum, a.attname,
         format_type(a.atttypid, a.atttypmod), a.attnotnull, a.attisdropped, a.attidentity,
         a.attgenerated, a.attacl, pg_get_expr(d.adbin, d.adrelid),
         a.attcollation::regcollation::text, a.attstorage, a.attstattarget)::text
    FROM pg_attribute a JOIN rel c ON c.oid = a.attrelid
    LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
   WHERE a.attnum > 0
  UNION ALL
  SELECT 'k' || ROW(k.conrelid::regclass::text, k.contypid::regtype::text, k.conname,
         k.contype, pg_get_constraintdef(k.oid), k.condeferrable, k.condeferred,
         k.convalidated, k.conislocal)::text
    FROM pg_constraint k JOIN ns ON ns.oid = k.connamespace
  UNION ALL
  SELECT 'i' || ROW(pg_get_indexdef(i.indexrelid), i.indisvalid, i.indisready,
         i.indisclustered, i.indisreplident)::text
    FROM pg_index i JOIN rel c ON c.oid = i.indexrelid
  UNION ALL
  SELECT 'p' || ROW(p.polrelid::regclass::text, p.polname, p.polcmd, p.polpermissive,
         p.polroles::regrole[]::text, pg_get_expr(p.polqual, p.polrelid),
         pg_get_expr(p.polwithcheck, p.polrelid))::text
    FROM pg_policy p JOIN rel c ON c.oid = p.polrelid
  UNION ALL
  SELECT 't' || ROW(t.tgrelid::regclass::text, pg_get_triggerdef(t.oid), t.tgenabled)::text
    FROM pg_trigger t JOIN rel c ON c.oid = t.tgrelid WHERE NOT t.tgisinternal
  UNION ALL
  SELECT 'T' || ROW(t.tgrelid::regclass::text, k.conname, t.tgfoid::regproc::text,
         t.tgtype, t.tgenabled)::text
    FROM pg_trigger t JOIN rel c ON c.oid = t.tgrelid
    LEFT JOIN pg_constraint k ON k.oid = t.tgconstraint
   WHERE t.tgisinternal
  UNION ALL
  SELECT 'f' || ROW(p.oid::regprocedure::text, p.prokind, pg_get_userbyid(p.proowner),
         p.proacl, md5(p.prosrc), p.prorettype::regtype::text, p.provolatile, p.prosecdef,
         p.proconfig)::text
    FROM pg_proc p JOIN ns ON ns.oid = p.pronamespace
  UNION ALL
  SELECT 'y' || ROW(t.oid::regtype::text, t.typtype, pg_get_userbyid(t.typowner), t.typacl,
         t.typnotnull, t.typdefault, t.typbasetype::regtype::text)::text
    FROM pg_type t JOIN ns ON ns.oid = t.typnamespace
   WHERE t.typtype <> 'c' OR t.typrelid IN (SELECT oid FROM pg_class WHERE relkind = 'c')
  UNION ALL
  SELECT 'e' || ROW(e.enumtypid::regtype::text, e.enumsortorder, e.enumlabel)::text
    FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid JOIN ns ON ns.oid = t.typnamespace
  UNION ALL
  SELECT 'r' || ROW(r.ev_class::regclass::text, r.rulename, pg_get_ruledef(r.oid),
         r.ev_enabled)::text
    FROM pg_rewrite r JOIN rel c ON c.oid = r.ev_class
  UNION ALL
  SELECT 'q' || ROW(s.seqrelid::regclass::text, s.seqtypid::regtype::text, s.seqstart,
         s.seqincrement, s.seqmin, s.seqmax, s.seqcache, s.seqcycle)::text
    FROM pg_sequence s JOIN rel c ON c.oid = s.seqrelid
  UNION ALL
  SELECT 'o' || ROW(d.objid::regclass::text, d.refobjid::regclass::text, d.refobjsubid,
         d.deptype)::text
    FROM pg_depend d JOIN rel c ON c.oid = d.objid
   WHERE d.classid = 'pg_class'::regclass AND d.refclassid = 'pg_class'::regclass
     AND c.relkind = 'S'
  UNION ALL
  SELECT 'h' || ROW(i.inhrelid::regclass::text, i.inhparent::regclass::text,
         i.inhseqno)::text
    FROM pg_inherits i JOIN rel c ON c.oid = i.inhrelid
  UNION ALL
  SELECT 's' || ROW(x.stxrelid::regclass::text, x.stxname, x.stxkeys::text,
         x.stxkind::text)::text
    FROM pg_statistic_ext x JOIN ns ON ns.oid = x.stxnamespace
  UNION ALL
  SELECT 'D' || ROW(pg_get_userbyid(d.defaclrole), d.defaclnamespace::regnamespace::text,
         d.defaclobjtype, d.defaclacl)::text
    FROM pg_default_acl d
  UNION ALL
  SELECT 'x' || ROW(x.extname, x.extversion, x.extnamespace::regnamespace::text)::text
    FROM pg_extension x
  UNION ALL
  SELECT 'E' || ROW(v.evtname, v.evtevent, v.evtenabled, v.evtfoid::regproc::text,
         v.evttags)::text
    FROM pg_event_trigger v
  UNION ALL
  SELECT 'R' || ROW(r.rolname, r.rolsuper, r.rolinherit, r.rolcreaterole, r.rolcreatedb,
         r.rolcanlogin, r.rolreplication, r.rolbypassrls, r.rolconnlimit,
         r.rolvaliduntil)::text
    FROM pg_roles r WHERE r.rolname NOT LIKE 'pg\\_%'
  UNION ALL
  SELECT 'M' || ROW(m.roleid::regrole::text, m.member::regrole::text,
         m.grantor::regrole::text, m.admin_option, m.inherit_option, m.set_option)::text
    FROM pg_auth_members m
  UNION ALL
  SELECT 'S' || ROW(coalesce((SELECT r.rolname FROM pg_roles r WHERE r.oid = s.setrole), '-'),
         s.setdatabase <> 0, s.setconfig)::text
    FROM pg_db_role_setting s WHERE s.setdatabase IN (0, (SELECT oid FROM current_db))
  UNION ALL
  SELECT 'B' || ROW(d.datname, pg_get_userbyid(d.datdba), d.datacl, d.datallowconn,
         d.datconnlimit, d.datistemplate)::text
    FROM pg_database d WHERE d.datname = current_database()
)
"""

FINGERPRINT = text(
    _FINGERPRINT_LINES + "SELECT md5(string_agg(line, E'\\n' ORDER BY line)) FROM parts"
)
FINGERPRINT_LINES = text(_FINGERPRINT_LINES + "SELECT line FROM parts ORDER BY line")

# The same catalog rows, raw: OIDs instead of names, node trees instead of deparsed
# expressions, no volatile statistics columns (relpages, reltuples, relfrozenxid, ...).
RAW_FINGERPRINT = text(
    f"""
WITH ns AS ({_USER_SCHEMAS}),
rel AS (SELECT c.oid, c.relkind FROM pg_class c JOIN ns ON ns.oid = c.relnamespace),
parts AS (
  SELECT 'n' || ROW(n.oid, n.nspname, n.nspowner, n.nspacl)::text AS line
    FROM pg_namespace n JOIN ns USING (oid)
  UNION ALL
  SELECT 'c' || ROW(c.oid, c.relname, c.relnamespace, c.relkind, c.relowner, c.relacl,
         c.relrowsecurity, c.relforcerowsecurity, c.relpersistence, c.reloptions,
         c.relreplident, c.relispartition, c.reltype, c.relam, c.relpartbound)::text
    FROM pg_class c JOIN ns ON ns.oid = c.relnamespace
  UNION ALL
  SELECT 'a' || ROW(a.attrelid, a.attnum, a.attname, a.atttypid, a.atttypmod, a.attnotnull,
         a.attisdropped, a.attidentity, a.attgenerated, a.attacl, a.attcollation,
         a.attstorage, a.attstattarget, a.atthasdef, a.attoptions)::text
    FROM pg_attribute a JOIN rel ON rel.oid = a.attrelid WHERE a.attnum > 0
  UNION ALL SELECT 'd' || ROW(d.*)::text FROM pg_attrdef d JOIN rel ON rel.oid = d.adrelid
  UNION ALL SELECT 'k' || ROW(k.*)::text FROM pg_constraint k JOIN ns ON ns.oid = k.connamespace
  UNION ALL SELECT 'i' || ROW(i.*)::text FROM pg_index i JOIN rel ON rel.oid = i.indexrelid
  UNION ALL SELECT 'p' || ROW(p.*)::text FROM pg_policy p JOIN rel ON rel.oid = p.polrelid
  UNION ALL SELECT 't' || ROW(t.*)::text FROM pg_trigger t JOIN rel ON rel.oid = t.tgrelid
  UNION ALL
  SELECT 'f' || ROW(p.oid, p.proname, p.pronamespace, p.proowner, p.prokind, p.proacl,
         md5(p.prosrc), p.proargtypes, p.prorettype, p.provolatile, p.prosecdef,
         p.proconfig)::text
    FROM pg_proc p JOIN ns ON ns.oid = p.pronamespace
  UNION ALL
  SELECT 'y' || ROW(t.oid, t.typname, t.typtype, t.typowner, t.typacl, t.typnotnull,
         t.typdefault, t.typbasetype, t.typrelid)::text
    FROM pg_type t JOIN ns ON ns.oid = t.typnamespace
  UNION ALL SELECT 'e' || ROW(e.*)::text FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
    JOIN ns ON ns.oid = t.typnamespace
  UNION ALL SELECT 'r' || ROW(r.*)::text FROM pg_rewrite r JOIN rel ON rel.oid = r.ev_class
  UNION ALL SELECT 'q' || ROW(s.*)::text FROM pg_sequence s JOIN rel ON rel.oid = s.seqrelid
  UNION ALL SELECT 'o' || ROW(d.*)::text FROM pg_depend d JOIN rel ON rel.oid = d.objid
   WHERE d.classid = 'pg_class'::regclass AND rel.relkind = 'S'
  UNION ALL SELECT 'h' || ROW(i.*)::text FROM pg_inherits i JOIN rel ON rel.oid = i.inhrelid
  UNION ALL SELECT 's' || ROW(x.oid, x.stxrelid, x.stxname, x.stxkeys, x.stxkind)::text
    FROM pg_statistic_ext x JOIN ns ON ns.oid = x.stxnamespace
  UNION ALL SELECT 'D' || ROW(d.*)::text FROM pg_default_acl d
  UNION ALL SELECT 'x' || ROW(x.*)::text FROM pg_extension x
  UNION ALL SELECT 'E' || ROW(v.*)::text FROM pg_event_trigger v
  UNION ALL
  SELECT 'R' || ROW(r.oid, r.rolname, r.rolsuper, r.rolinherit, r.rolcreaterole, r.rolcreatedb,
         r.rolcanlogin, r.rolreplication, r.rolbypassrls, r.rolconnlimit, r.rolvaliduntil)::text
    FROM pg_roles r
  UNION ALL SELECT 'M' || ROW(m.*)::text FROM pg_auth_members m
  UNION ALL SELECT 'S' || ROW(s.*)::text FROM pg_db_role_setting s
   WHERE s.setdatabase IN (0, (SELECT oid FROM pg_database WHERE datname = current_database()))
  UNION ALL
  SELECT 'B' || ROW(d.oid, d.datname, d.datdba, d.datacl, d.datallowconn, d.datconnlimit,
         d.datistemplate)::text
    FROM pg_database d WHERE d.datname = current_database()
)
SELECT md5(string_agg(line, ',' ORDER BY line)) FROM parts
"""
)

_INVENTORY = text(
    f"""
    WITH ns AS ({_USER_SCHEMAS})
    SELECT quote_ident(ns.nspname) || '.' || quote_ident(c.relname), c.relkind
      FROM pg_class c JOIN ns ON ns.oid = c.relnamespace
     WHERE c.relkind IN ('r', 'p', 'S')
     ORDER BY 1
    """
)

_SET_SEQUENCES_TO_START = text(
    "SELECT setval(s.seqrelid, s.seqstart, false) FROM pg_sequence s "
    "WHERE s.seqrelid::regclass::text = ANY(:names)"
)

RESET_STRATEGIES = ("delete", "truncate")


@dataclass
class _Baseline:
    """Database and role state right after the first full build, restored by later ones."""

    extensions: frozenset[str]
    public_acl: frozenset[tuple[str, str, bool]]
    default_acl: frozenset[tuple]
    database_settings: tuple[str, ...]
    app_role: tuple
    app_role_settings: tuple[tuple[bool, tuple[str, ...]], ...]
    app_memberships: frozenset[str]
    lines: tuple[str, ...]


@dataclass
class _State:
    canonical: str | None = None
    canonical_raw: str | None = None
    raw_refreshes: int = 0
    tables: tuple[str, ...] = ()
    sequences: tuple[str, ...] = ()
    nonempty_query: str = ""
    strategy: str | None = None
    baseline: _Baseline | None = None
    pending_cause: str | None = None
    builds: Counter = field(default_factory=Counter)
    build_seconds: Counter = field(default_factory=Counter)
    row_resets: int = 0
    lifespan: Counter = field(default_factory=Counter)
    rebaselines: list[str] = field(default_factory=list)


_state = _State()


def admin_engine(url: str | None = None) -> Engine:
    """A short-lived engine as the test login role (no ``SET ROLE``, no pooled leftovers)."""
    return create_engine(url or configured_database_url(), poolclass=NullPool)


def fingerprint(connection: Connection) -> str:
    return connection.execute(FINGERPRINT).scalar_one()


def current_fingerprint() -> str:
    with check_engine().connect() as connection:
        return fingerprint(connection)


def canonical_fingerprint() -> str | None:
    """The fingerprint taken right after the last full build in this process."""
    return _state.canonical


def active_reset_strategy() -> str | None:
    return _state.strategy


def reset_strategy(*, is_superuser: bool) -> str:
    """``DELETE`` with the foreign-key triggers off needs a superuser (R2); else ``TRUNCATE``."""
    return "delete" if is_superuser else "truncate"


def reset_rows(
    connection: Connection, *, tables: list[str] | tuple[str, ...], sequences, strategy: str
) -> None:
    """Remove every row of ``tables`` and set ``sequences`` back to their start.

    ``delete``: one ``DELETE`` per table with ``session_replication_role = replica``, so the
    foreign-key triggers do not fire and the order does not matter (every table that holds
    rows is in ``tables``).  ``truncate``: one ``TRUNCATE ... RESTART IDENTITY CASCADE``.
    """
    if strategy not in RESET_STRATEGIES:
        raise ValueError(f"unknown reset strategy {strategy!r}")
    if tables and strategy == "delete":
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        for table in tables:
            connection.execute(text(f"DELETE FROM {table}"))
        connection.execute(text("SET LOCAL session_replication_role = DEFAULT"))
    elif tables:
        connection.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    if sequences:
        connection.execute(_SET_SEQUENCES_TO_START, {"names": list(sequences)})


def canonical_database_url(tmp_path: Path) -> str:
    """The database URL of a test that uses ``client``: the canonical schema, no rows.

    Without PostgreSQL it is the per-test SQLite file, as before.
    """
    url = configured_database_url()
    if not is_postgres_url(url):
        return test_database_url(tmp_path)
    dispose_inspection_engine()
    if _state.canonical is not None:
        with check_engine().begin() as connection:
            if _is_canonical(connection):
                _reset_rows_of_the_canonical_schema(connection)
                _state.row_resets += 1
                _state.pending_cause = None
                return url
    _full_build(url)
    return url


def repair_cluster_state() -> None:
    """Put ``homayar_app`` and ``homayar_audit_reader`` back, and nothing else.

    Used after a test that changed cluster-wide state under the exclusive xdist lock: the
    schema stays as it is (the next test of the same module may still need it).  The
    reader statement is the cluster-wide first step of ``audit_reader_statements``.
    """
    url = configured_database_url()
    if _state.baseline is None or not is_postgres_url(url):
        return
    engine = admin_engine(url)
    try:
        with engine.begin() as connection:
            _restore_app_role(connection, _state.baseline)
            connection.execute(text(audit_reader_statements(app_role=TEST_APP_ROLE)[0]))
    finally:
        engine.dispose()


def rebuild_now(cause: str) -> None:
    """A full build right now (an xdist worker's first build, ``_parallel.py``)."""
    url = configured_database_url()
    if is_postgres_url(url):
        dispose_inspection_engine()
        _state.pending_cause = cause
        _full_build(url)


def empty_database_url(tmp_path: Path) -> str:
    """The base start state for a test with ``settings`` but without ``client`` (R3)."""
    url = test_database_url(tmp_path)
    if is_postgres_url(url):
        _state.pending_cause = "after settings without client"
    return url


def schema_is_canonical(database_url: str) -> bool:
    """R4: the exact test URL, and the fingerprint *now* equals the canonical one."""
    configured = configured_database_url()
    if _state.canonical is None or not is_postgres_url(configured) or database_url != configured:
        return False
    with check_engine().connect() as connection:
        return _is_canonical(connection)


def _is_canonical(connection: Connection) -> bool:
    """The fingerprint now equals the one taken right after the last full build.

    Raw rows first; when they differ (a test re-created an object with the same
    definition, so it has a new OID), the text fingerprint decides.
    """
    if _state.canonical is None:
        return False
    raw = connection.execute(RAW_FINGERPRINT).scalar_one()
    if raw == _state.canonical_raw:
        return True
    if fingerprint(connection) != _state.canonical:
        return False
    _state.canonical_raw = raw
    _state.raw_refreshes += 1
    return True


def create_schema_unless_canonical(settings: Settings | None = None) -> None:
    """The app lifespan's schema call in the test process (``backend.main`` only)."""
    settings = settings or get_settings()
    if schema_is_canonical(settings.database_url):
        _state.lifespan["skipped"] += 1
        return
    _state.lifespan["real"] += 1
    relational.create_schema_for_test_or_development(settings)


def summary_lines() -> list[str]:
    if not _state.builds and not _state.row_resets:
        return []
    builds = ", ".join(
        f"{cause}: {n} ({_state.build_seconds[cause]:.1f} s)"
        for cause, n in sorted(_state.builds.items())
    )
    lines = [
        f"database isolation: {_state.row_resets} row resets ({_state.strategy}), "
        f"full builds {builds}; lifespan schema calls skipped {_state.lifespan['skipped']}, "
        f"real {_state.lifespan['real']}; same definitions with new OIDs "
        f"{_state.raw_refreshes}"
    ]
    lines.extend(
        f"database isolation: cluster-wide state kept after a build, as on the base: {note}"
        for note in _state.rebaselines
    )
    return lines


# --- full build ------------------------------------------------------------------------------


def _schema_settings(url: str) -> Settings:
    return Settings(
        environment="test",
        database_url=url,
        database_app_role=TEST_APP_ROLE,
        jwt_secret="j" * 48,
        token_pepper="p" * 48,
        _env_file=None,
    )


def _full_build(url: str) -> None:
    cause = _state.pending_cause or ("first" if _state.canonical is None else "drift")
    _state.pending_cause = None
    started = time.perf_counter()
    reset_postgres_schema(url)  # the base drop (also disposes the inspection engine)
    engine = admin_engine(url)
    try:
        with engine.begin() as connection:
            _drop_leftovers(connection, _state.baseline)
            if _state.baseline is not None:
                _restore_baseline(connection, _state.baseline)
        relational.create_schema_for_test_or_development(_schema_settings(url))
        with engine.connect() as connection:
            canonical = fingerprint(connection)
            canonical_raw = connection.execute(RAW_FINGERPRINT).scalar_one()
            inventory = connection.execute(_INVENTORY).all()
            if _state.baseline is None:
                _state.baseline = _capture_baseline(connection)
                superuser = connection.execute(
                    text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
                ).scalar_one()
                _state.strategy = reset_strategy(is_superuser=bool(superuser))
            elif canonical != _state.canonical:
                _compare_with_the_first_build(connection, _state.baseline)
    finally:
        engine.dispose()
    _state.canonical = canonical
    _state.canonical_raw = canonical_raw
    _state.tables = tuple(name for name, kind in inventory if kind in ("r", "p"))
    _state.sequences = tuple(name for name, kind in inventory if kind == "S")
    _state.nonempty_query = " UNION ALL ".join(
        f"SELECT '{name}' WHERE EXISTS (SELECT 1 FROM {name})" for name in _state.tables
    )
    _state.builds[cause] += 1
    _state.build_seconds[cause] += time.perf_counter() - started


def _reset_rows_of_the_canonical_schema(connection: Connection) -> None:
    strategy = _state.strategy or "truncate"
    if strategy == "delete":
        tables = (
            connection.execute(text(_state.nonempty_query)).scalars().all() if _state.tables else []
        )
    else:
        tables = list(_state.tables)
    reset_rows(connection, tables=tables, sequences=_state.sequences, strategy=strategy)


def _drop_leftovers(connection: Connection, baseline: _Baseline | None) -> None:
    """Drop every schema object the real build does not create (SPEC section 12).

    The base drop removes the model tables and ``alembic_version`` only.  This removes the
    rest: every other relation (table, view, materialized view, sequence, foreign table,
    with their triggers, indexes and policies), function, procedure, aggregate, type and
    domain in ``public``; every other user schema; every event trigger; and extensions
    created after the first build.  Extensions that were there before the first build
    (and their objects) are part of the environment, not of a test, and stay.
    """
    if baseline is not None:
        for (name,) in connection.execute(
            text("SELECT quote_ident(extname) FROM pg_extension")
        ).all():
            if name not in baseline.extensions:
                connection.execute(text(f"DROP EXTENSION IF EXISTS {name} CASCADE"))
    for (name,) in connection.execute(
        text(
            f"SELECT quote_ident(ns.nspname) FROM ({_USER_SCHEMAS}) ns "
            "WHERE ns.nspname <> 'public' "
            "AND NOT EXISTS (SELECT 1 FROM pg_extension x WHERE x.extnamespace = ns.oid)"
        )
    ).all():
        connection.execute(text(f"DROP SCHEMA IF EXISTS {name} CASCADE"))
    for (name,) in connection.execute(
        text("SELECT quote_ident(evtname) FROM pg_event_trigger")
    ).all():
        connection.execute(text(f"DROP EVENT TRIGGER IF EXISTS {name}"))
    not_in_extension = (
        "NOT EXISTS (SELECT 1 FROM pg_depend e WHERE e.classid = '{catalog}'::regclass "
        "AND e.objid = {oid} AND e.deptype = 'e')"
    )
    drops = {
        "r": "TABLE",
        "p": "TABLE",
        "f": "FOREIGN TABLE",
        "v": "VIEW",
        "m": "MATERIALIZED VIEW",
        "S": "SEQUENCE",
    }
    for name, kind in connection.execute(
        text(
            "SELECT quote_ident(c.relname), c.relkind FROM pg_class c "
            "WHERE c.relnamespace = 'public'::regnamespace "
            "AND c.relkind IN ('r', 'p', 'f', 'v', 'm', 'S') AND "
            + not_in_extension.format(catalog="pg_class", oid="c.oid")
            # an identity or serial sequence goes with its table: sequences last
            + " ORDER BY c.relkind = 'S', c.relname"
        )
    ).all():
        connection.execute(text(f"DROP {drops[kind]} IF EXISTS public.{name} CASCADE"))
    for signature, kind in connection.execute(
        text(
            "SELECT p.oid::regprocedure::text, p.prokind FROM pg_proc p "
            "WHERE p.pronamespace = 'public'::regnamespace AND "
            + not_in_extension.format(catalog="pg_proc", oid="p.oid")
        )
    ).all():
        statement = "AGGREGATE" if kind == "a" else "ROUTINE"
        connection.execute(text(f"DROP {statement} IF EXISTS {signature} CASCADE"))
    for name, kind in connection.execute(
        text(
            "SELECT t.oid::regtype::text, t.typtype FROM pg_type t "
            "WHERE t.typnamespace = 'public'::regnamespace "
            "AND t.typtype IN ('b', 'c', 'd', 'e', 'r') "
            "AND (t.typtype <> 'c' OR t.typrelid IN (SELECT oid FROM pg_class WHERE relkind = "
            "'c')) "
            "AND NOT EXISTS (SELECT 1 FROM pg_type a WHERE a.typarray = t.oid) AND "
            + not_in_extension.format(catalog="pg_type", oid="t.oid")
        )
    ).all():
        statement = "DOMAIN" if kind == "d" else "TYPE"
        connection.execute(text(f"DROP {statement} IF EXISTS {name} CASCADE"))


# --- database and cluster-wide state (rule 7) ------------------------------------------------

_PUBLIC_ACL = text(
    "SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(a.grantee)) "
    "END, "
    "a.privilege_type, a.is_grantable FROM pg_namespace n, "
    "aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) a WHERE n.nspname = 'public'"
)
_DEFAULT_ACL = text(
    "SELECT quote_ident(pg_get_userbyid(d.defaclrole)), "
    "CASE WHEN d.defaclnamespace = 0 THEN '' ELSE d.defaclnamespace::regnamespace::text END, "
    "d.defaclobjtype, "
    "CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(a.grantee)) END, "
    "a.privilege_type, a.is_grantable FROM pg_default_acl d, aclexplode(d.defaclacl) a"
)
_DEFAULT_ACL_OBJECTS = {
    "r": "TABLES",
    "S": "SEQUENCES",
    "f": "FUNCTIONS",
    "T": "TYPES",
    "n": "SCHEMAS",
}
_DATABASE_SETTINGS = text(
    "SELECT coalesce(s.setconfig, '{}') FROM pg_database d LEFT JOIN pg_db_role_setting s "
    "ON s.setdatabase = d.oid AND s.setrole = 0 WHERE d.datname = current_database()"
)
_APP_ROLE = text(
    "SELECT rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin, rolreplication, "
    "rolbypassrls, rolconnlimit, rolvaliduntil::text FROM pg_roles WHERE rolname = :role"
)
_APP_ROLE_SETTINGS = text(
    "SELECT s.setdatabase <> 0, s.setconfig FROM pg_db_role_setting s JOIN pg_roles r "
    "ON r.oid = s.setrole WHERE r.rolname = :role AND s.setdatabase IN "
    "(0, (SELECT oid FROM pg_database WHERE datname = current_database())) ORDER BY 1"
)
_APP_MEMBERSHIPS = text(
    "SELECT quote_ident(g.rolname) || ' GRANTED BY ' || quote_ident(o.rolname) "
    "FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
    "JOIN pg_roles r ON r.oid = m.member JOIN pg_roles o ON o.oid = m.grantor "
    "WHERE r.rolname = :role"
)


def _capture_baseline(connection: Connection) -> _Baseline:
    role = {"role": TEST_APP_ROLE}
    return _Baseline(
        extensions=frozenset(
            connection.execute(text("SELECT quote_ident(extname) FROM pg_extension")).scalars()
        ),
        public_acl=frozenset(tuple(row) for row in connection.execute(_PUBLIC_ACL)),
        default_acl=frozenset(tuple(row) for row in connection.execute(_DEFAULT_ACL)),
        database_settings=tuple(connection.execute(_DATABASE_SETTINGS).scalar_one()),
        app_role=tuple(connection.execute(_APP_ROLE, role).one()),
        app_role_settings=tuple(
            (bool(scoped), tuple(config))
            for scoped, config in connection.execute(_APP_ROLE_SETTINGS, role)
        ),
        app_memberships=frozenset(connection.execute(_APP_MEMBERSHIPS, role).scalars()),
        lines=tuple(connection.execute(FINGERPRINT_LINES).scalars()),
    )


def _quoted_database(connection: Connection) -> str:
    return connection.execute(text("SELECT quote_ident(current_database())")).scalar_one()


def _set_statements(target: str, config: tuple[str, ...]) -> list[text]:
    statements = []
    for item in config:
        name, _, value = item.partition("=")
        statements.append(
            text(f"ALTER {target} SET {name} TO " + "'" + value.replace("'", "''") + "'")
        )
    return statements


def _restore_baseline(connection: Connection, baseline: _Baseline) -> None:
    """Undo database-wide and app-role changes a test left behind.

    The reader role is repaired by ``apply_row_level_security`` in the build itself, as on
    the base; roles a test created and did not drop stay (the base never dropped them).
    """
    current_acl = frozenset(tuple(row) for row in connection.execute(_PUBLIC_ACL))
    for grantee, privilege, _grantable in current_acl - baseline.public_acl:
        connection.execute(text(f"REVOKE {privilege} ON SCHEMA public FROM {grantee} CASCADE"))
    for grantee, privilege, grantable in baseline.public_acl - current_acl:
        option = " WITH GRANT OPTION" if grantable else ""
        connection.execute(text(f"GRANT {privilege} ON SCHEMA public TO {grantee}{option}"))

    _restore_default_privileges(connection, baseline.default_acl)

    database = _quoted_database(connection)
    if tuple(connection.execute(_DATABASE_SETTINGS).scalar_one()) != baseline.database_settings:
        connection.execute(text(f"ALTER DATABASE {database} RESET ALL"))
        for statement in _set_statements(f"DATABASE {database}", baseline.database_settings):
            connection.execute(statement)

    _restore_app_role(connection, baseline)


def _restore_app_role(connection: Connection, baseline: _Baseline) -> None:
    """``homayar_app``'s attributes, settings and memberships back to the first build's."""
    database = _quoted_database(connection)
    role = {"role": TEST_APP_ROLE}
    app = TEST_APP_ROLE
    current = connection.execute(_APP_ROLE, role).one_or_none()
    if current is not None and tuple(current) != baseline.app_role:
        flags = (
            "SUPERUSER",
            "INHERIT",
            "CREATEROLE",
            "CREATEDB",
            "LOGIN",
            "REPLICATION",
            "BYPASSRLS",
        )
        options = [
            flag if on else f"NO{flag}"
            for flag, on in zip(flags, baseline.app_role[:7], strict=True)
        ]
        options.append(f"CONNECTION LIMIT {int(baseline.app_role[7])}")
        valid_until = baseline.app_role[8]
        if current[8] != valid_until:
            # PostgreSQL cannot set VALID UNTIL back to NULL; 'infinity' means the same.
            options.append(f"VALID UNTIL '{valid_until or 'infinity'}'")
        if not baseline.app_role[4]:
            options.append("PASSWORD NULL")
        connection.execute(text(f"ALTER ROLE {app} " + " ".join(options)))
    if current is not None:
        settings = tuple(
            (bool(scoped), tuple(config))
            for scoped, config in connection.execute(_APP_ROLE_SETTINGS, role)
        )
        if settings != baseline.app_role_settings:
            connection.execute(text(f"ALTER ROLE {app} RESET ALL"))
            connection.execute(text(f"ALTER ROLE {app} IN DATABASE {database} RESET ALL"))
            for scoped, config in baseline.app_role_settings:
                target = f"ROLE {app} IN DATABASE {database}" if scoped else f"ROLE {app}"
                for statement in _set_statements(target, config):
                    connection.execute(statement)
        for membership in (
            set(connection.execute(_APP_MEMBERSHIPS, role).scalars()) - baseline.app_memberships
        ):
            granted, _, grantor = membership.partition(" GRANTED BY ")
            connection.execute(text(f"REVOKE {granted} FROM {app} GRANTED BY {grantor}"))


def _restore_default_privileges(connection: Connection, baseline: frozenset[tuple]) -> None:
    """``ALTER DEFAULT PRIVILEGES`` back to the first build's entries.

    A key with no entry in the first build goes back to PostgreSQL's built-in default:
    nothing for a per-schema entry, ``acldefault`` for a global one.
    """
    current = frozenset(tuple(row) for row in connection.execute(_DEFAULT_ACL))
    for key in {row[:3] for row in current | baseline}:
        role, schema, kind = key
        have = {row[3:] for row in current if row[:3] == key}
        if any(row[:3] == key for row in baseline):
            want = {row[3:] for row in baseline if row[:3] == key}
        elif schema:
            want = set()
        else:
            want = {
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' "
                        "ELSE quote_ident(pg_get_userbyid(a.grantee)) END, "
                        "a.privilege_type, a.is_grantable "
                        'FROM aclexplode(acldefault(CAST(:kind AS "char"), CAST(:role AS '
                        "regrole))) a"
                    ),
                    {"kind": kind.lower() if kind == "S" else kind, "role": role},
                )
            }
        target = f"ALTER DEFAULT PRIVILEGES FOR ROLE {role}" + (
            f" IN SCHEMA {schema}" if schema else ""
        )
        objects = _DEFAULT_ACL_OBJECTS[kind]
        for grantee, privilege, _grantable in have - want:
            connection.execute(
                text(f"{target} REVOKE {privilege} ON {objects} FROM {grantee} CASCADE")
            )
        for grantee, privilege, grantable in want - have:
            option = " WITH GRANT OPTION" if grantable else ""
            connection.execute(
                text(f"{target} GRANT {privilege} ON {objects} TO {grantee}{option}")
            )


# Fingerprint lines of cluster-wide or database-wide state that a full build does not own:
# roles, memberships, role settings and the database row.  A test that leaks a role leaves
# it on the base too; the run reports it and continues (SPEC section 12).
_NOT_SCHEMA_OBJECTS = ("R", "M", "S(", "B")


def _compare_with_the_first_build(connection: Connection, baseline: _Baseline) -> None:
    """After a build, the schema must be exactly the first build's.

    A difference in a schema object fails the test that needed the build: the build did not
    remove something a test left.  A difference only in cluster-wide state (a leaked role,
    for example) is reported in the terminal summary.  Database settings
    (``S(-,...``) count as schema state here: the build restores them.
    """
    now = set(connection.execute(FINGERPRINT_LINES).scalars())
    before = set(baseline.lines)
    changed = sorted(now ^ before)
    objects = [
        line
        for line in changed
        if line.startswith("S(-,") or not line.startswith(_NOT_SCHEMA_OBJECTS)
    ]
    if objects:
        raise RuntimeError(
            "the full build left schema objects the real build does not create "
            "(+ now, - first build): "
            + repr([("+" if line in now else "-") + line for line in objects[:8]])
        )
    _state.rebaselines.append(
        ", ".join(line.split(",")[0] + ")" for line in changed[:6])
        + (" ..." if len(changed) > 6 else "")
    )
