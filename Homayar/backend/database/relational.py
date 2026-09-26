from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.config import Settings, get_settings
from backend.database.tenant_context import install_session_listeners

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _quote_identifier(name: str) -> str:
    from backend.database.rls import validate_role_name

    return '"' + validate_role_name(name) + '"'


def build_engine(settings: Settings | None = None, *, app_role: str | None = None) -> Engine:
    """Create the engine.

    ``app_role`` (default: ``settings.database_app_role``) switches every
    pooled PostgreSQL connection to that role with ``SET ROLE`` on checkout and
    back with ``RESET ROLE`` on checkin, so application SQL never runs with the
    login role's (owner/superuser) privileges.  Pass ``app_role=""`` for an
    administrative engine (schema creation, tests' inspection sessions).
    """
    settings = settings or get_settings()
    url = make_url(settings.database_url)
    kwargs: dict = {"pool_pre_ping": True}
    is_sqlite = url.get_backend_name() == "sqlite"

    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
        if url.database in (None, "", ":memory:"):
            kwargs["poolclass"] = StaticPool
    else:
        kwargs["pool_size"] = settings.database_pool_size
        kwargs["max_overflow"] = settings.database_max_overflow

    engine = create_engine(settings.database_url, **kwargs)

    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
        return engine

    role = settings.database_app_role if app_role is None else app_role
    if role:
        set_role_sql = f"SET ROLE {_quote_identifier(role)}"

        def _run_outside_transaction(dbapi_connection, statement: str) -> None:
            # psycopg opens an implicit transaction for any statement; end it so
            # SQLAlchemy finds the connection idle when it takes over.
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(statement)
            finally:
                cursor.close()
            dbapi_connection.commit()

        @event.listens_for(engine, "checkout")
        def _assume_app_role(dbapi_connection, _connection_record, _proxy) -> None:
            _run_outside_transaction(dbapi_connection, set_role_sql)

        @event.listens_for(engine, "checkin")
        def _drop_app_role(dbapi_connection, _connection_record) -> None:
            if dbapi_connection is None:
                return
            _run_outside_transaction(dbapi_connection, "RESET ROLE")

    return engine


def configure_database(settings: Settings | None = None, *, engine: Engine | None = None) -> None:
    global _engine, _session_factory
    _engine = engine or build_engine(settings)
    _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    install_session_listeners(_session_factory)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        configure_database()
    assert _engine is not None
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        configure_database()
    assert _session_factory is not None
    return _session_factory


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_schema_for_test_or_development(settings: Settings | None = None) -> None:
    """``create_all`` plus the same row-level security the migrations install.

    Runs on an administrative engine (no ``SET ROLE``) because DDL needs the
    owner; the application engine keeps running as the non-bypass role.
    """
    settings = settings or get_settings()
    if settings.environment not in {"test", "development"}:
        raise RuntimeError("Automatic schema creation is forbidden outside test/development")
    from backend.database.rls import apply_row_level_security
    from backend.database.saas_models import Base

    admin_engine = build_engine(settings, app_role="")
    try:
        with admin_engine.begin() as connection:
            Base.metadata.create_all(connection)
            apply_row_level_security(connection)
    finally:
        admin_engine.dispose()


def reset_database_state() -> None:
    """Dispose configured state. Intended for isolated tests."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
