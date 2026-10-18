"""Backup freshness for alerting (node_exporter textfile collector).

Contract with the alert rules (Lane D): the gauge
``homayar_backup_last_success_timestamp_seconds{target="postgres"|"objects"|"offsite"}``
holds the Unix time of the last *successful* backup of that target.  The
file is rewritten atomically (temporary file, fsync, rename) and only after a
success, so a failed or interrupted run leaves the previous value in place
and the age alert fires.  Path: ``HOMAYAR_BACKUP_TEXTFILE`` (Compose sets it
to node_exporter's textfile directory, ``NODE_EXPORTER_TEXTFILE_DIR`` on the
host, outside the 0700 backup root), else ``<root>/metrics/homayar_backup.prom``
for local runs.
"""

from __future__ import annotations

import fcntl
import os
import re
import tempfile
from pathlib import Path

METRIC = "homayar_backup_last_success_timestamp_seconds"
TARGETS = ("postgres", "objects", "offsite")
_LINE = re.compile(rf'^{METRIC}\{{target="([a-z]+)"\}} ([0-9.eE+]+)$')


def textfile_path(root: Path, env: dict[str, str] | None = None) -> Path:
    environ = os.environ if env is None else env
    configured = environ.get("HOMAYAR_BACKUP_TEXTFILE", "")
    return Path(configured) if configured else Path(root) / "metrics" / "homayar_backup.prom"


def read_values(path: Path) -> dict[str, float]:
    values: dict[str, float] = {}
    try:
        lines = Path(path).read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeDecodeError):
        return values
    for line in lines:
        match = _LINE.match(line.strip())
        if match and match.group(1) in TARGETS:
            values[match.group(1)] = float(match.group(2))
    return values


def _ensure_directory(directory: Path) -> None:
    try:
        directory.mkdir(parents=True)
        os.chmod(directory, 0o755)  # noqa: S103 - created here: node_exporter must read it
    except FileExistsError:
        pass


def probe(path: Path) -> None:
    """Raise ``OSError`` unless this user can write the textfile.

    Exercises what ``record_success`` needs (the lock file and a temporary
    next to the textfile) as the user that will write it: in Compose that is
    ``BACKUP_UID`` without ``CAP_DAC_OVERRIDE``, so a host-side check cannot
    stand in for it.
    """
    path = Path(path)
    _ensure_directory(path.parent)
    os.close(os.open(path.parent / f".{path.name}.lock", os.O_RDWR | os.O_CREAT, 0o644))
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".probe", dir=path.parent)
    os.close(fd)
    os.unlink(temporary)


def record_success(path: Path, timestamps: dict[str, float]) -> None:
    """Merge ``{target: unix_time}`` into the textfile.

    A value never moves backwards (verifying an old set cannot make the
    gauge look fresher than the newest verified one).  Concurrent writers
    (two ``compose run`` containers both have PID 1) are serialised by an
    ``flock`` on a lock file next to the textfile, and each writes its own
    ``mkstemp`` temporary before the atomic rename.  The file is 0644; a
    directory created here is 0755, and an existing one (possibly shared with
    other collectors and owned by someone else) keeps its mode.
    """
    unknown = set(timestamps) - set(TARGETS)
    if unknown:
        raise ValueError(f"unknown backup targets: {sorted(unknown)}")
    path = Path(path)
    _ensure_directory(path.parent)
    lock_fd = os.open(path.parent / f".{path.name}.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        values = read_values(path)
        for target, value in timestamps.items():
            values[target] = max(value, values.get(target, value))
        body = [
            f"# HELP {METRIC} Unix time of the last successful Homayar backup per target.",
            f"# TYPE {METRIC} gauge",
        ]
        body += [
            f'{METRIC}{{target="{target}"}} {values[target]:.3f}'
            for target in TARGETS
            if target in values
        ]
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            os.write(fd, ("\n".join(body) + "\n").encode("ascii"))
            os.fchmod(fd, 0o644)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, path)
    finally:
        os.close(lock_fd)  # releases the flock
