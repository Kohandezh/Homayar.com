"""Append-only journal of conversation tombstones (WP07-T04, ADR 0022).

A backup restore brings back the database and object store as they were at
backup time: conversations deleted afterwards would be alive again.  Every
tombstone is therefore also written, after its commit, to a journal that lives
outside both backups (``TOMBSTONE_JOURNAL_DIR``, a separately replicated
volume).  ``backend.jobs.tombstone_reapply`` replays the journal into a
restored deployment before it takes traffic.

One JSON object per line (``v``, ``purge_id``, ``workspace_id``,
``meeting_id``, ``reason``, ``tombstoned_at``, ``object_keys``) plus a
SHA-256 of the canonical payload: ids, timestamps and opaque object keys only,
never titles, filenames or transcript text.  Files are per UTC day and only
ever appended and fsynced; a torn or altered line is reported, never trusted.
Journaling is idempotent at the reader: a tombstone written twice is applied
once.  The ``conversation_purges.journaled_at`` column is the outbox marker:
rows without it are (re)written by the retention sweep.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    ConversationPurge,
    MediaLifecycleState,
    MediaObject,
    Meeting,
    StageCheckpoint,
)
from backend.jobs.leases import as_utc
from backend.services.logging_service import logger

JOURNAL_VERSION = 1
JOURNAL_INLINE_LIMIT = 50
_FILE_PREFIX = "tombstones-"
_FILE_SUFFIX = ".jsonl"


class JournalUnavailable(RuntimeError):
    """The journal directory is not configured or cannot be written.

    ``reason`` is a bounded code that is safe to log; neither it nor the
    message carries a path or the operating system's error text.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class TombstoneRecord:
    purge_id: str
    workspace_id: str
    meeting_id: str
    reason: str
    tombstoned_at: datetime
    object_keys: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {
            "v": JOURNAL_VERSION,
            "purge_id": self.purge_id,
            "workspace_id": self.workspace_id,
            "meeting_id": self.meeting_id,
            "reason": self.reason,
            "tombstoned_at": self.tombstoned_at.astimezone(UTC).isoformat(),
            "object_keys": list(self.object_keys),
        }


def _digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def encode(record: TombstoneRecord) -> str:
    payload = record.payload()
    return json.dumps({**payload, "sha256": _digest(payload)}, sort_keys=True) + "\n"


def decode(line: str) -> TombstoneRecord:
    """Parse one line; raises ``ValueError`` for anything not trustworthy."""
    data = json.loads(line)
    if not isinstance(data, dict):
        raise ValueError("not an object")
    digest = data.pop("sha256", None)
    if data.get("v") != JOURNAL_VERSION or digest != _digest(data):
        raise ValueError("version or digest mismatch")
    keys = data["object_keys"]
    if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
        raise ValueError("object_keys must be strings")
    tombstoned_at = datetime.fromisoformat(str(data["tombstoned_at"]))
    if tombstoned_at.tzinfo is None:
        raise ValueError("tombstoned_at needs a timezone")
    return TombstoneRecord(
        purge_id=str(data["purge_id"]),
        workspace_id=str(data["workspace_id"]),
        meeting_id=str(data["meeting_id"]),
        reason=str(data["reason"]),
        tombstoned_at=tombstoned_at,
        object_keys=tuple(keys),
    )


class TombstoneJournal:
    def __init__(self, directory: str | os.PathLike[str]) -> None:
        if not str(directory):
            raise JournalUnavailable("not_configured", "TOMBSTONE_JOURNAL_DIR is not configured")
        self.directory = Path(directory)

    def append(self, records: list[TombstoneRecord], *, now: datetime) -> None:
        """Append and fsync; raises ``JournalUnavailable`` on any OS error."""
        if not records:
            return
        name = f"{_FILE_PREFIX}{now.astimezone(UTC):%Y%m%d}{_FILE_SUFFIX}"
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self.directory / name
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, "".join(encode(r) for r in records).encode("ascii"))
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            raise JournalUnavailable(
                "write_failed", f"journal write failed: {type(exc).__name__}"
            ) from exc

    def files(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(
            p
            for p in self.directory.iterdir()
            if p.name.startswith(_FILE_PREFIX) and p.name.endswith(_FILE_SUFFIX)
        )

    def read(self) -> tuple[list[TombstoneRecord], int]:
        """All trustworthy records (deduplicated by meeting) and the count of
        rejected lines (torn tail, edited line, unknown version)."""
        records: dict[str, TombstoneRecord] = {}
        rejected = 0
        for path in self.files():
            with path.open("r", encoding="ascii", errors="replace") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        record = decode(line)
                    except (ValueError, KeyError, TypeError):
                        rejected += 1
                        continue
                    records.setdefault(record.meeting_id, record)
        return list(records.values()), rejected


def journal_for(settings) -> TombstoneJournal | None:
    directory = getattr(settings, "tombstone_journal_dir", "")
    return TombstoneJournal(directory) if directory else None


def record_for(db: Session, purge: ConversationPurge) -> TombstoneRecord:
    """Build the journal record of a purge from the tenant-bound session."""
    meeting = db.get(Meeting, purge.meeting_id)
    tombstoned_at = as_utc(meeting.deleted_at if meeting and meeting.deleted_at else None)
    keys = list(
        db.scalars(
            select(MediaObject.storage_key).where(
                MediaObject.workspace_id == purge.workspace_id,
                MediaObject.meeting_id == purge.meeting_id,
                MediaObject.lifecycle_state != MediaLifecycleState.LEGAL_HOLD,
            )
        )
    )
    keys += list(
        db.scalars(
            select(StageCheckpoint.artifact_key).where(
                StageCheckpoint.workspace_id == purge.workspace_id,
                StageCheckpoint.meeting_id == purge.meeting_id,
            )
        )
    )
    return TombstoneRecord(
        purge_id=purge.id,
        workspace_id=purge.workspace_id,
        meeting_id=purge.meeting_id,
        reason=purge.reason,
        tombstoned_at=tombstoned_at or as_utc(purge.requested_at),
        object_keys=tuple(sorted(set(keys))),
    )


def journal_pending(
    db: Session, journal: TombstoneJournal, *, workspace_id: str, now: datetime, limit: int
) -> int:
    """Write every not-yet-journaled purge of the workspace; commits the marker.

    The marker is set only after the append (fsync) succeeded, so a crash in
    between rewrites the record (the reader deduplicates).
    """
    rows = list(
        db.scalars(
            select(ConversationPurge)
            .where(
                ConversationPurge.workspace_id == workspace_id,
                ConversationPurge.journaled_at.is_(None),
            )
            .order_by(ConversationPurge.requested_at.asc(), ConversationPurge.id.asc())
            .limit(limit)
        )
    )
    if not rows:
        db.rollback()
        return 0
    records = [record_for(db, row) for row in rows]
    try:
        journal.append(records, now=now)
    except JournalUnavailable as exc:
        db.rollback()
        cause = type(exc.__cause__).__name__ if exc.__cause__ is not None else "none"
        logger.error(
            "TOMBSTONE_JOURNAL_UNAVAILABLE workspace_id=%s reason=%s cause=%s",
            workspace_id,
            exc.reason,
            cause,
        )
        return 0
    for row in rows:
        row.journaled_at = now
    db.commit()
    return len(rows)
