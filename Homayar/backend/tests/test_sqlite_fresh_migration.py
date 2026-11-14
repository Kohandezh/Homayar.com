"""HOM-353: a fresh SQLite database survives the full Alembic lifecycle.

``alembic upgrade head`` on an empty SQLite file used to stop at
``a1c3e5f7b9d2`` because that revision rewrote a CHECK constraint with plain
``op.*`` calls, which the SQLite dialect cannot render.  This test runs the
whole migration chain in-process on a throwaway file, whatever ``DATABASE_URL``
the surrounding run carries (the CI PostgreSQL job included), proves the
VOICEMAIL rule lands and unlands with the revision, and proves ``downgrade
base`` leaves no application table behind.
"""

from __future__ import annotations

import re

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.sql.elements import TextClause

from backend.config import reset_settings_cache
from backend.observability.release import PROJECT_ROOT

# Immutable history: the revision that adds VOICEMAIL and the one before it.
VOICEMAIL_REVISION = "a1c3e5f7b9d2"
PRE_VOICEMAIL_REVISION = "f5b1d7c3a9e2"
CONSTRAINT = "ck_ingestion_source_type"

_TABLE = text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :t")
_TABLES = (
    "SELECT name FROM sqlite_master "
    "WHERE type = 'table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'"
)
_VERSION = text("SELECT version_num FROM alembic_version")
_NAMED_CHECK = re.compile(r'CONSTRAINT "?' + re.escape(CONSTRAINT) + r'"? CHECK \(')


def _alembic_at(monkeypatch, url: str) -> Config:
    """Point env.py's ``get_settings().database_url`` at the SQLite file."""
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _query_scalar(url: str, statement: TextClause, parameter: str | None = None):
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return connection.execute(statement, {"t": parameter} if parameter else {}).scalar()
    finally:
        engine.dispose()


def _names(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return set(connection.execute(text(_TABLES)).scalars())
    finally:
        engine.dispose()


def _named_check(url: str) -> str | None:
    """The SQL of the source-type CHECK of ``ingestion_records``, if present.

    SQLite keeps the constraint inside the ``CREATE TABLE`` statement, so the
    check is cut out of ``sqlite_master`` by matching its ``CONSTRAINT ... CHECK
    (`` opener and the balanced parentheses that follow it.
    """
    create_table = _query_scalar(url, _TABLE, parameter="ingestion_records")
    if create_table is None:
        return None
    match = _NAMED_CHECK.search(create_table)
    if match is None:
        return None
    depth = 0
    for index in range(match.end() - 1, len(create_table)):
        if create_table[index] == "(":
            depth += 1
        elif create_table[index] == ")":
            depth -= 1
            if depth == 0:
                return create_table[match.start() : index + 1]
    return None


def test_fresh_sqlite_database_upgrades_downgrades_and_upgrades_again(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'fresh-migration.db'}"
    config = _alembic_at(monkeypatch, url)
    # Raises on multiple heads, so this also proves the single-head invariant.
    head = ScriptDirectory.from_config(config).get_current_head()
    try:
        command.upgrade(config, "head")
        assert _query_scalar(url, _VERSION) == head

        rule = _named_check(url)
        assert rule is not None
        assert "'VOICEMAIL'" in rule

        command.downgrade(config, PRE_VOICEMAIL_REVISION)
        assert _query_scalar(url, _VERSION) == PRE_VOICEMAIL_REVISION
        rule = _named_check(url)
        assert rule is not None
        assert "'VOICEMAIL'" not in rule

        command.downgrade(config, "base")
        assert _names(url) <= {"alembic_version"}

        command.upgrade(config, "head")
        assert _query_scalar(url, _VERSION) == head
        rule = _named_check(url)
        assert rule is not None
        assert "'VOICEMAIL'" in rule
    finally:
        reset_settings_cache()
