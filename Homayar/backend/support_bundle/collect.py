"""Container-side facts for the support bundle (HOM-110).

``collect_document`` builds the JSON the api container contributes to a
support bundle. Everything here is allowlist-only: a settings field whose
name is not in ``PUBLIC_SETTINGS`` is reported as ``set`` or ``unset``
and never with its value. Image IDs, digests and the 40-hex release SHA
are not secrets (they identify a build) and are copied.
"""

from __future__ import annotations

import platform
import sys
from importlib import metadata
from typing import Any

from backend.config import Settings

SCHEMA_VERSION = 1

# Settings copied with their value, by upper-case environment name. Booleans,
# numeric limits and bounded vocabulary words only: never a URL, DSN, key,
# password, token, host or e-mail address.
PUBLIC_SETTINGS: frozenset[str] = frozenset(
    {
        "ENVIRONMENT",
        "DEPLOYMENT_MODE",
        "DEPLOYMENT_TOPOLOGY",
        "ASR_PROFILE",
        "REGISTRATION_ENABLED",
        "ENABLE_FREE_DEMO",
        "ENABLE_DEMO_ACCOUNT",
        "ENABLE_GOOGLE_AUTH",
        "ENABLE_PHONE_AUTH",
        "SECURE_COOKIES",
        "STORAGE_BACKEND",
        "STORAGE_PRESIGNED_DELIVERY",
        "LOG_LEVEL",
        "DEMO_QUOTA_SECONDS",
        "MAX_UPLOAD_SIZE_MB",
        "OTP_TTL_SECONDS",
    }
)

# A fixed short list: the bundle reports versions of these only.
PACKAGE_LIST: tuple[str, ...] = (
    "fastapi",
    "pydantic",
    "pydantic-settings",
    "sqlalchemy",
    "alembic",
    "celery",
    "uvicorn",
    "psycopg",
)


def release_facts() -> dict[str, Any]:
    """Build identity from ``backend.observability.release`` (never a secret)."""
    from backend.observability.release import load_build_info, runtime_identity

    return {"identity": runtime_identity(), "build_info": load_build_info()}


def database_facts(settings: Settings) -> dict[str, Any]:
    """Reachability, server version and the alembic version. No tenant rows.

    No row of any tenant table and no per-tenant count is ever read; the two
    scalar facts come from ``SELECT version()`` and ``alembic_version``.
    """
    from sqlalchemy import create_engine, text

    facts: dict[str, Any] = {
        "reachable": False,
        "server_version": None,
        "alembic_version": None,
    }
    try:
        engine = create_engine(str(settings.database_url), pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                facts["server_version"] = str(connection.execute(text("SELECT version()")).scalar())
                facts["alembic_version"] = str(
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                )
            facts["reachable"] = True
        finally:
            engine.dispose()
    except Exception:
        pass
    return facts


def _settings_view(settings: Settings) -> dict[str, dict[str, Any]]:
    public: dict[str, Any] = {}
    other: dict[str, str] = {}
    for name in Settings.model_fields:
        env_name = name.upper()
        value = getattr(settings, name, None)
        if env_name in PUBLIC_SETTINGS and value is not None:
            public[env_name] = value if isinstance(value, (bool, int, float, str)) else str(value)
        else:
            other[env_name] = "set" if value not in (None, "") else "unset"
    return {"public": public, "other": other}


def _package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in PACKAGE_LIST:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def collect_document(settings: Settings | None = None) -> dict[str, Any]:
    """The whole container-side bundle content, JSON-ready."""
    if settings is None:
        from backend.config import get_settings

        settings = get_settings()
    return {
        "schema_version": SCHEMA_VERSION,
        "release": release_facts(),
        "runtime": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform_machine": platform.machine(),
            "python_hexversion": sys.hexversion,
        },
        "packages": _package_versions(),
        "settings": _settings_view(settings),
        "database": database_facts(settings),
    }
