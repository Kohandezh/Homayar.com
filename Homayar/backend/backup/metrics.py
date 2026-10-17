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

import os
import re
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
    """Merge ``{target: unix_time}`` into the textfile (atomic replace)."""
    unknown = set(timestamps) - set(TARGETS)
    if unknown:
        raise ValueError(f"unknown backup targets: {sorted(unknown)}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    values = read_values(path)
    values.update(timestamps)
    body = [
        f"# HELP {METRIC} Unix time of the last successful Homayar backup per target.",
        f"# TYPE {METRIC} gauge",
    ]
    body += [
        f'{METRIC}{{target="{target}"}} {values[target]:.3f}'
        for target in TARGETS
        if target in values
    ]
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, ("\n".join(body) + "\n").encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)
