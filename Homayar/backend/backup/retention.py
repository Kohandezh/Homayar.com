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
    backup_ids: list[str],
    *,
    daily: int = DEFAULT_DAILY,
    monthly: int = DEFAULT_MONTHLY,
    qualifying: set[str] | None = None,
) -> set[str]:
    """Sets kept by the policy (see ``_retained``)."""
    keep, _ = _retained(backup_ids, daily=daily, monthly=monthly, qualifying=qualifying)
    return keep


def retained_monthly(
    backup_ids: list[str],
    *,
    daily: int = DEFAULT_DAILY,
    monthly: int = DEFAULT_MONTHLY,
    qualifying: set[str] | None = None,
) -> set[str]:
    """The kept sets that only the monthly rule keeps (older than the daily window)."""
    _, monthly_sets = _retained(backup_ids, daily=daily, monthly=monthly, qualifying=qualifying)
    return monthly_sets


def _retained(
    backup_ids: list[str],
    *,
    daily: int,
    monthly: int,
    qualifying: set[str] | None,
) -> tuple[set[str], set[str]]:
    """Sets kept by the policy, and those of them kept as monthly sets.

    * daily: the last ``daily`` UTC days that have a set keep one set each;
    * monthly: the last ``monthly`` months OLDER than the daily window keep
      one set each, so the history reaches about ``monthly`` months beyond
      the daily window (not 12 months counted from today);
    * a bucket keeps its newest *qualifying* set (full and deep-verified),
      falling back to its newest set, so a later database-only or failed
      set never pushes out the bucket's only full verified one;
    * the newest set is always kept.
    """
    if not backup_ids:
        return set(), set()
    good = set(backup_ids) if qualifying is None else set(qualifying) & set(backup_ids)
    ordered = sorted(backup_ids, key=lambda b: (backup_time(b), b), reverse=True)

    def representative(bucket: list[str]) -> str:
        return next((b for b in bucket if b in good), bucket[0])

    keep: set[str] = {ordered[0]}
    days: dict[str, list[str]] = {}
    for backup_id in ordered:  # newest first
        days.setdefault(backup_time(backup_id).strftime("%Y-%m-%d"), []).append(backup_id)
    daily_days = sorted(days, reverse=True)[:daily]
    keep.update(representative(days[day]) for day in daily_days)

    window_start = (
        datetime.strptime(min(daily_days), "%Y-%m-%d").replace(tzinfo=UTC)
        if daily_days
        else backup_time(ordered[0])
    )
    months: dict[str, list[str]] = {}
    for backup_id in ordered:
        moment = backup_time(backup_id)
        if moment < window_start:
            months.setdefault(moment.strftime("%Y-%m"), []).append(backup_id)
    monthly_sets = {
        representative(months[month]) for month in sorted(months, reverse=True)[:monthly]
    }
    return keep | monthly_sets, monthly_sets - keep


def verified_marker(root: Path, backup_id: str) -> Path:
    return Path(root) / "sets" / f"{backup_id}.verified"


def qualifying_sets(root: Path, sets: dict[str, Path]) -> set[str]:
    """Full (objects included) sets that passed a deep verify."""
    from backend.backup.manifest import BackupInvalid, read_manifest

    good = set()
    for backup_id, path in sets.items():
        if not verified_marker(root, backup_id).is_file():
            continue
        try:
            manifest = read_manifest(path)
        except BackupInvalid:
            continue
        objects = manifest.get("stores", {}).get("objects", {})
        if objects.get("included") and objects.get("integrity", "complete") == "complete":
            good.add(backup_id)
    return good


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
    keep = retained(
        list(sets), daily=daily, monthly=monthly, qualifying=qualifying_sets(root, sets)
    )
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
                verified_marker(root, backup_id).unlink(missing_ok=True)
                # The off-site backfill's record of a rejected copy (replicate.py).
                (root / "sets" / f"{backup_id}.offsite-rejected").unlink(missing_ok=True)
            if locked:
                for name in stale:
                    shutil.rmtree(root / "sets" / name)
    finally:
        os.close(lock_fd)
    return {"kept": sorted(keep), "expired": expired, "stale_partials": stale}
