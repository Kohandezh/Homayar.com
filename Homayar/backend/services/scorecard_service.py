"""Published QA scorecard versions (HOM-54).

Every read and write runs in the caller's tenant-bound session; ``scorecard_versions`` is
a workspace table under row-level security, so a caller-supplied workspace id never
reaches another tenant's scorecards.  A stored version never changes: a publish appends
the next version of its key, and publishing the same document again is a new version, so
the history shows every publish.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database.saas_models import ScorecardVersion
from backend.scorecards import (
    KEY_PATTERN,
    SCORECARDS_VERSION,
    ScorecardDefinition,
    ScorecardError,
    definition_digest,
    valid_key,
    validate_definition,
)

# ``scorecard_versions.version`` is a PostgreSQL integer; a larger number is no version.
MAX_VERSION = 2**31 - 1


class ScorecardVersionConflict(Exception):
    def __init__(self, current_version: int) -> None:
        super().__init__("The scorecard changed since it was read")
        self.current_version = current_version


def check_key(scorecard_key: object) -> str:
    """The key, or ``ScorecardError("scorecard_key_invalid")``.  A whole-string match,
    so a trailing newline is refused here and never reaches the database check."""
    if not valid_key(scorecard_key):
        raise ScorecardError("scorecard_key_invalid", f"the key must match {KEY_PATTERN}")
    return scorecard_key  # type: ignore[return-value]


def current_version(db: Session, *, workspace_id: str, scorecard_key: str) -> int:
    """The latest version of the key in the workspace, 0 when it has none."""
    latest = db.scalar(
        select(func.max(ScorecardVersion.version)).where(
            ScorecardVersion.workspace_id == workspace_id,
            ScorecardVersion.scorecard_key == scorecard_key,
        )
    )
    return int(latest or 0)


def publish_version(
    db: Session,
    *,
    workspace_id: str,
    scorecard_key: str,
    definition: ScorecardDefinition | Mapping[str, Any],
    expected_version: int,
    reason: str,
    actor_user_id: str | None,
    now: datetime,
) -> ScorecardVersion:
    """Append version ``expected_version + 1`` of the key.

    ``expected_version`` is the version the caller read, 0 for a new key (optimistic
    concurrency).  A concurrent writer makes one of two requests fail with
    ``ScorecardVersionConflict``, checked here and by ``uq_scorecard_version``.  An invalid
    key or definition raises ``ScorecardError`` with its code before anything is read.
    """
    check_key(scorecard_key)
    model = validate_definition(definition)
    current = current_version(db, workspace_id=workspace_id, scorecard_key=scorecard_key)
    if expected_version != current:
        raise ScorecardVersionConflict(current)
    row = ScorecardVersion(
        workspace_id=workspace_id,
        scorecard_key=scorecard_key,
        version=current + 1,
        scorecards_version=SCORECARDS_VERSION,
        document=model.model_dump(mode="json"),
        digest=definition_digest(model),
        reason=reason,
        created_by_user_id=actor_user_id,
        created_at=now,
    )
    savepoint = db.begin_nested()
    try:
        db.add(row)
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        raise ScorecardVersionConflict(
            current_version(db, workspace_id=workspace_id, scorecard_key=scorecard_key)
        ) from None
    return row


def latest_versions(db: Session, *, workspace_id: str) -> list[ScorecardVersion]:
    """The latest version of every key of the workspace, sorted by key."""
    latest = (
        select(
            ScorecardVersion.scorecard_key.label("scorecard_key"),
            func.max(ScorecardVersion.version).label("version"),
        )
        .where(ScorecardVersion.workspace_id == workspace_id)
        .group_by(ScorecardVersion.scorecard_key)
        .subquery()
    )
    return list(
        db.scalars(
            select(ScorecardVersion)
            .join(
                latest,
                and_(
                    ScorecardVersion.scorecard_key == latest.c.scorecard_key,
                    ScorecardVersion.version == latest.c.version,
                ),
            )
            .where(ScorecardVersion.workspace_id == workspace_id)
            .order_by(ScorecardVersion.scorecard_key.asc())
        )
    )


def list_versions(db: Session, *, workspace_id: str, scorecard_key: str) -> list[ScorecardVersion]:
    """Every version of the key, newest first; empty when the key has none."""
    return list(
        db.scalars(
            select(ScorecardVersion)
            .where(
                ScorecardVersion.workspace_id == workspace_id,
                ScorecardVersion.scorecard_key == scorecard_key,
            )
            .order_by(ScorecardVersion.version.desc())
        )
    )


def get_version(
    db: Session, *, workspace_id: str, scorecard_key: str, version: int
) -> ScorecardVersion | None:
    if not 1 <= version <= MAX_VERSION:  # never a stored version, and no integer overflow
        return None
    return db.scalar(
        select(ScorecardVersion).where(
            ScorecardVersion.workspace_id == workspace_id,
            ScorecardVersion.scorecard_key == scorecard_key,
            ScorecardVersion.version == version,
        )
    )
