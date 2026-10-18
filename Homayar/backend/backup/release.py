"""Release identity and migration plumbing shared by backup and restore."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import backend

PROJECT_DIR = Path(backend.__file__).resolve().parent.parent
ALEMBIC_INI = PROJECT_DIR / "alembic.ini"


class MigrationError(RuntimeError):
    pass


def code_heads() -> list[str]:
    """Alembic heads of the code that is running (no database access)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    return sorted(script.get_heads())


def code_has_revision(revision: str) -> bool:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.util.exc import CommandError

    script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    try:
        return script.get_revision(revision) is not None
    except CommandError:
        return False


def run_alembic(database_url: str, target: str, *, timeout: int = 3600) -> None:
    """``alembic upgrade <target>`` against ``database_url`` in a child process.

    A child process because ``backend.migrations.env`` reads the URL from the
    cached settings and configures logging globally; neither may leak into
    the caller.
    """
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL_FILE"}
    env["DATABASE_URL"] = database_url
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", target],
        cwd=str(PROJECT_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        tail = "\n".join((result.stderr or result.stdout).strip().splitlines()[-15:])
        # Never echo a DSN (it may carry a password).
        tail = re.sub(r"://[^@\s/]+@", "://***@", tail)
        raise MigrationError(f"alembic upgrade {target} failed:\n{tail}")


def _pyproject_version() -> str | None:
    path = PROJECT_DIR / "pyproject.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else None


def _git_sha() -> str | None:
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv
            ["git", "-C", str(PROJECT_DIR), "rev-parse", "HEAD"],  # noqa: S607 - git from PATH
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def release_metadata() -> dict[str, str]:
    """What is known about the running release; "unknown" is said, not guessed."""
    return {
        "name": "homayar-platform",
        "version": os.environ.get("HOMAYAR_RELEASE_VERSION") or _pyproject_version() or "unknown",
        "release_sha": os.environ.get("HOMAYAR_RELEASE_SHA") or _git_sha() or "unknown",
        "api_image": os.environ.get("API_IMAGE", ""),
    }
