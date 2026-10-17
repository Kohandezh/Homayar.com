"""Backup retention: 30 daily + 12 monthly (provisional owner policy, HOM-153).

``retained`` decides, ``prune`` deletes.  Only complete sets count; the
newest complete set is always kept, whatever the policy says.  Partial
directories (interrupted runs) are removed once they are older than the
grace period and no backup holds the lock.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from backend.backup.manifest import LOCK_NAME, list_complete_sets, list_partial_sets

DEFAULT_DAILY = 30
DEFAULT_MONTHLY = 12
PARTIAL_GRACE_SECONDS = 6 * 3600


def backup_time(backup_id: str) -> datetime:
    return datetime.strptime(backup_id.split("-", 1)[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def retained(
    backup_ids: list[str], *, daily: int = DEFAULT_DAILY, monthly: int = DEFAULT_MONTHLY
) -> set[str]:
    """The newest set of each of the last ``daily`` days that have a set and
    of each of the last ``monthly`` months that have a set, plus the newest."""
    ordered = sorted(backup_ids, key=lambda b: (backup_time(b), b), reverse=True)
    keep: set[str] = set(ordered[:1])
    days: dict[str, str] = {}
    months: dict[str, str] = {}
    for backup_id in ordered:  # newest first: the first seen per bucket wins
        moment = backup_time(backup_id)
        days.setdefault(moment.strftime("%Y-%m-%d"), backup_id)
        months.setdefault(moment.strftime("%Y-%m"), backup_id)
    keep.update(sorted(days.values(), key=backup_time, reverse=True)[:daily])
    keep.update(sorted(months.values(), key=backup_time, reverse=True)[:monthly])
    return keep


def prune(
    root: Path,
    *,
    daily: int = DEFAULT_DAILY,
    monthly: int = DEFAULT_MONTHLY,
    dry_run: bool = False,
    partial_grace_seconds: int = PARTIAL_GRACE_SECONDS,
) -> dict[str, list[str]]:
    root = Path(root)
    sets = dict(list_complete_sets(root))
    keep = retained(list(sets), daily=daily, monthly=monthly)
    expired = sorted(set(sets) - keep)
    stale: list[str] = []
    lock_fd = os.open(root / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError:
            locked = False  # a backup is running: its partial dir is live
        if locked:
            now = time.time()
            stale = [
                p.name
                for p in list_partial_sets(root)
                if now - p.stat().st_mtime >= partial_grace_seconds
            ]
        if not dry_run:
            for backup_id in expired:
                shutil.rmtree(sets[backup_id])
            if locked:
                for name in stale:
                    shutil.rmtree(root / "sets" / name)
    finally:
        os.close(lock_fd)
    return {"kept": sorted(keep), "expired": expired, "stale_partials": stale}
