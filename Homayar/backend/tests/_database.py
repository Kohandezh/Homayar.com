"""Test database selection (WP01-T05).

The suite runs against PostgreSQL when ``TEST_DATABASE_URL`` points at a
``postgresql+psycopg://`` server.  A test that uses ``client`` then starts from the
schema built once per process with every row removed (HOM-292, ``_isolation.py``); any
other test that uses ``settings`` starts from an empty schema.  Without that variable
it falls back to a throwaway SQLite file, which is fine for local iteration but does
not exercise row locking, constraint behaviour or migrations. Set
``HOMAYAR_REQUIRE_POSTGRES_TESTS=1`` (CI does) to turn the fallback into a hard error so
a misconfigured pipeline cannot pass on SQLite.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

TEST_DATABASE_ENV = "TEST_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "HOMAYAR_REQUIRE_POSTGRES_TESTS"

# Role the application engine assumes with SET ROLE during tests (WP03-T05).
# The test login role itself stays a superuser/BYPASSRLS DBA used only for
# schema resets and inspection.
TEST_APP_ROLE = "homayar_app"

_inspection_engine: Engine | None = None


def inspection_session_factory() -> sessionmaker[Session]:
    """Sessions for test *setup and inspection*, not for exercising the app.

    The application runs as the non-bypass role ``homayar_app`` and sees only
    the tenant it bound.  Tests that seed rows or assert across tenants need a
    DBA view: on PostgreSQL this is a separate engine as the test login role
    (superuser or BYPASSRLS, as in CI) without ``SET ROLE``; on the SQLite
    fallback it is simply the application factory.
    """
    global _inspection_engine
    configured = configured_database_url()
    if not is_postgres_url(configured):
        from backend.database.relational import get_session_factory

        return get_session_factory()
    if _inspection_engine is None:
        engine = create_engine(configured, pool_pre_ping=True)
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).scalar()
        if not row:
            engine.dispose()
            raise RuntimeError(
                "The test database role must be a superuser or have BYPASSRLS so tests can "
                "seed and inspect rows across tenants (the application runs as homayar_app)."
            )
        _inspection_engine = engine
    return sessionmaker(bind=_inspection_engine, autoflush=False, expire_on_commit=False)


def dispose_inspection_engine() -> None:
    global _inspection_engine
    if _inspection_engine is not None:
        _inspection_engine.dispose()
        _inspection_engine = None


_check_engine: Engine | None = None


def check_engine() -> Engine:
    """One pooled connection as the test login role for the per-test schema checks (HOM-292).

    ``reset_postgres_schema`` closes it, so a module that counts this database's
    connections after resetting the schema (the voice burst tests) never sees it.
    """
    global _check_engine
    if _check_engine is None:
        _check_engine = create_engine(
            configured_database_url(), pool_size=1, max_overflow=4, pool_pre_ping=True
        )
    return _check_engine


def dispose_check_engine() -> None:
    global _check_engine
    if _check_engine is not None:
        _check_engine.dispose()
        _check_engine = None


def configured_database_url() -> str | None:
    value = os.environ.get(TEST_DATABASE_ENV, "").strip()
    return value or None


def is_postgres_url(url: str | None) -> bool:
    return bool(url) and make_url(url).get_backend_name() == "postgresql"


def postgres_required() -> bool:
    return os.environ.get(REQUIRE_POSTGRES_ENV, "").strip().lower() in {"1", "true", "yes"}


def describe_database_url(url: str) -> str:
    return make_url(url).render_as_string(hide_password=True)


def reset_postgres_schema(url: str) -> None:
    """Drop every model table (and the Alembic stamp) so a test starts clean."""
    from backend.database.saas_models import Base

    dispose_inspection_engine()
    dispose_check_engine()
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            # CASCADE: row-level security policies reference other tables
            # (workspaces -> memberships, organizations -> workspaces).
            for table in reversed(Base.metadata.sorted_tables):
                connection.execute(text(f'DROP TABLE IF EXISTS "{table.name}" CASCADE'))
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    finally:
        engine.dispose()


def test_database_url(tmp_path: Path, *, name: str = "test") -> str:
    """Return the URL a test should use and make sure it starts empty."""
    configured = configured_database_url()
    if configured is None:
        return f"sqlite:///{tmp_path / f'{name}.db'}"
    if not is_postgres_url(configured):
        raise RuntimeError(
            f"{TEST_DATABASE_ENV} must be a postgresql+psycopg:// URL, got "
            f"{describe_database_url(configured)}"
        )
    reset_postgres_schema(configured)
    return configured
