"""Backup freshness for alerting (node_exporter textfile collector).

Contract with the alert rules (Lane D): the gauge
``homayar_backup_last_success_timestamp_seconds{target="postgres"|"objects"|"offsite"}``
holds the Unix time of the last *successful* backup of that target.  The
file is rewritten atomically (temporary file, fsync, rename) and only after a
success, so a failed or interrupted run leaves the previous value in place
and the age alert fires.  Path: ``HOMAYAR_BACKUP_TEXTFILE`` or
``<BACKUP_ROOT>/metrics/homayar_backup.prom``.
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


def record_success(path: Path, timestamps: dict[str, float]) -> None:
    """Merge ``{target: unix_time}`` into the textfile.

    A value never moves backwards (verifying an old set cannot make the
    gauge look fresher than the newest verified one).  Concurrent writers
    (two ``compose run`` containers both have PID 1) are serialised by an
    ``flock`` on a lock file next to the textfile, and each writes its own
    ``mkstemp`` temporary before the atomic rename.  The directory is 0755
    and the file 0644 so a non-root node_exporter can read them.
    """
    unknown = set(timestamps) - set(TARGETS)
    if unknown:
        raise ValueError(f"unknown backup targets: {sorted(unknown)}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o755)  # noqa: S103 - node_exporter must traverse it
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
