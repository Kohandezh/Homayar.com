"""Server-side retention policy of a workspace (WP07-T01, ADR 0020).

The workspace row is the only authority: clients never supply an expiry.
``retention_expiry_for`` is evaluated once at ingestion and stored on the
conversation and its media rows; ``is_age_based_deletion_enabled`` is
re-evaluated by every sweep before it treats a stored expiry as actionable,
so a policy change to ``INDEFINITE`` (or an unresolved legacy value) stops
age-based deletion even for rows that carry an old expiry.

``is_retention_expired`` is the sweep's expiry eligibility for one
conversation, so a recording route never refuses what the sweep would never
delete (HOM-60).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.database.saas_models import MediaLifecycleState, Meeting, RetentionMode, Workspace
from backend.jobs.leases import as_utc
from backend.services.conversation_purge_service import active_job_exists
from backend.services.media_lifecycle_service import media_for_meeting

MAX_RETENTION_DAYS = 3650


class InvalidRetentionPolicy(ValueError):
    """A (mode, days) pair that the contract does not allow."""


def validate_retention_policy(mode: RetentionMode, retention_days: int) -> None:
    if type(retention_days) is not int:
        raise InvalidRetentionPolicy("retention_days must be an integer")
    if mode is RetentionMode.FINITE:
        if not 1 <= retention_days <= MAX_RETENTION_DAYS:
            raise InvalidRetentionPolicy(
                f"Finite retention needs retention_days between 1 and {MAX_RETENTION_DAYS}"
            )
        return
    if retention_days != 0:
        raise InvalidRetentionPolicy(
            f"retention_days must be 0 (unused) when retention_mode is {mode.value}"
        )


def retention_expiry_for(workspace: Workspace, *, now: datetime) -> datetime | None:
    """Expiry stamped on a conversation ingested at ``now``.

    ``FINITE``: ``now + retention_days``.  ``IMMEDIATE``: ``now`` — the row is
    eligible as soon as processing is terminal (the sweep never removes a
    conversation that is still processing).  ``INDEFINITE`` and
    ``LEGACY_UNRESOLVED``: ``None`` — no age-based expiry exists.
    """
    mode = workspace.retention_mode
    if mode is RetentionMode.FINITE:
        return now + timedelta(days=workspace.retention_days)
    if mode is RetentionMode.IMMEDIATE:
        return now
    return None


def is_age_based_deletion_enabled(workspace: Workspace) -> bool:
    """Whether a stored ``retention_expires_at`` may be acted upon *today*.

    Re-checked at every mutation boundary so an old expiry never outranks the
    current policy; the legacy ambiguous value is never destructive.
    """
    return workspace.retention_mode in (RetentionMode.FINITE, RetentionMode.IMMEDIATE)


def is_expiry_due(workspace: Workspace, meeting: Meeting, *, now: datetime) -> bool:
    """The policy is enforceable now and the conversation's stored expiry passed.

    Naive values (SQLite) are read as UTC.  Only the meeting's expiry counts,
    as in the sweep; the media rows carry a copy of it.
    """
    if not is_age_based_deletion_enabled(workspace):
        return False
    expires = as_utc(meeting.retention_expires_at)
    return expires is not None and expires <= as_utc(now)


def is_retention_expired(
    db: Session, *, workspace: Workspace, meeting: Meeting, now: datetime
) -> bool:
    """Whether the sweep would expire this conversation now
    (``backend.jobs.retention_purge.expire_meeting``): the expiry is due, no
    processing job is queued or running, and no media object of the
    conversation is under legal hold.

    Reads jobs and media objects, so it needs the tenant-bound session of a
    workspace request; a share session cannot see those rows.
    """
    if not is_expiry_due(workspace, meeting, now=now):
        return False
    if active_job_exists(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id):
        return False
    media = media_for_meeting(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    return not any(m.lifecycle_state is MediaLifecycleState.LEGAL_HOLD for m in media)
