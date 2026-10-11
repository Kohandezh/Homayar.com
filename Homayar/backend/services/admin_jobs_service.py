"""Workspace administrator's view of processing jobs (HOM-183).

* ``list_workspace_jobs`` pages a workspace's jobs newest first
  (``created_at`` then ``id``, both descending) with an opaque cursor, and
  reads each page with one query: the job joined to its meeting and, when
  there is one, its recording.
* ``summarize_workspace_jobs`` counts the same jobs per state.

Both filter on the caller's workspace in the query itself (row-level
security is extra defence) and leave out jobs of soft-deleted meetings.
``job_is_replayable`` repeats the static refusals of
``backend.jobs.replay.replay_dead_letter_job`` without taking locks or
reserving quota, so a job it accepts may still be refused for quota.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from backend.database.saas_models import JobState, Meeting, ProcessingJob, Recording
from backend.jobs.leases import as_utc, database_now
from backend.services.admin_service import MAX_AUDIT_DETAIL_STRING_LENGTH, _is_sensitive_string


class InvalidJobCursor(ValueError):
    pass


@dataclass(frozen=True)
class AdminJobRow:
    job: ProcessingJob
    meeting_title: str
    failure_message: str | None
    replayable: bool


@dataclass(frozen=True)
class AdminJobPage:
    rows: list[AdminJobRow]
    next_cursor: str | None


def _encode_job_cursor(job: ProcessingJob) -> str:
    created_at = as_utc(job.created_at)
    assert created_at is not None
    payload = {"v": 1, "created_at": created_at.isoformat(), "id": job.id}
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return encoded.rstrip(b"=").decode("ascii")


def _decode_job_cursor(cursor: str) -> tuple[datetime, str]:
    # Its own keys: an audit-events cursor ("occurred_at") is refused here.
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"v", "created_at", "id"}:
            raise ValueError
        if (
            payload["v"] != 1
            or not isinstance(payload["created_at"], str)
            or not isinstance(payload["id"], str)
        ):
            raise ValueError
        job_id = str(uuid.UUID(payload["id"]))
        created_at = datetime.fromisoformat(payload["created_at"])
        if created_at.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise InvalidJobCursor("Invalid job pagination cursor") from exc
    return created_at, job_id


def sanitize_failure_message(value: str | None) -> str | None:
    """The audit-details string rule: a secret-shaped value is redacted, the
    rest is cut to the audit string length."""
    if value is None:
        return None
    if _is_sensitive_string(value):
        return "[redacted]"
    return value[:MAX_AUDIT_DETAIL_STRING_LENGTH]


def job_is_replayable(
    job: ProcessingJob,
    meeting: Meeting | None,
    recording: Recording | None,
    *,
    max_replays: int,
    now: datetime,
) -> bool:
    """Whether ``replay_dead_letter_job`` would pass its static checks, in its
    order: state, replay limit, meeting, retention, recording."""
    if job.state != JobState.DEAD_LETTER:
        return False
    if job.replay_count >= max_replays:
        return False
    if meeting is None or meeting.deleted_at is not None:
        return False
    retention = as_utc(meeting.retention_expires_at)
    if retention is not None and retention <= now:
        return False
    return recording is not None and recording.deleted_at is None


def _live_jobs(workspace_id: str, *columns: Any) -> Select[Any]:
    """``columns`` over the workspace's jobs whose meeting is not soft-deleted."""
    return (
        select(*columns)
        .select_from(ProcessingJob)
        .join(
            Meeting,
            and_(Meeting.id == ProcessingJob.meeting_id, Meeting.workspace_id == workspace_id),
        )
        .where(ProcessingJob.workspace_id == workspace_id, Meeting.deleted_at.is_(None))
    )


def list_workspace_jobs(
    db: Session,
    *,
    workspace_id: str,
    limit: int,
    max_replays: int,
    cursor: str | None = None,
    states: list[JobState] | None = None,
    stage: str | None = None,
) -> AdminJobPage:
    query = _live_jobs(workspace_id, ProcessingJob, Meeting, Recording).outerjoin(
        Recording,
        and_(Recording.meeting_id == Meeting.id, Recording.workspace_id == workspace_id),
    )
    if states:
        query = query.where(ProcessingJob.state.in_(states))
    if stage is not None:
        query = query.where(ProcessingJob.stage == stage)
    if cursor is not None:
        cursor_time, cursor_id = _decode_job_cursor(cursor)
        query = query.where(
            or_(
                ProcessingJob.created_at < cursor_time,
                and_(ProcessingJob.created_at == cursor_time, ProcessingJob.id < cursor_id),
            )
        )

    rows = db.execute(
        query.order_by(ProcessingJob.created_at.desc(), ProcessingJob.id.desc()).limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    now = database_now(db)
    return AdminJobPage(
        rows=[
            AdminJobRow(
                job=job,
                meeting_title=meeting.title,
                failure_message=sanitize_failure_message(job.failure_message),
                replayable=job_is_replayable(
                    job, meeting, recording, max_replays=max_replays, now=now
                ),
            )
            for job, meeting, recording in page_rows
        ],
        next_cursor=_encode_job_cursor(page_rows[-1][0]) if has_more else None,
    )


def summarize_workspace_jobs(db: Session, *, workspace_id: str) -> dict[JobState, int]:
    """Job count per state; every state is present, zero when it has none."""
    counts = dict.fromkeys(JobState, 0)
    query = _live_jobs(workspace_id, ProcessingJob.state, func.count(ProcessingJob.id))
    for state, count in db.execute(query.group_by(ProcessingJob.state)).all():
        counts[state] = count
    return counts
