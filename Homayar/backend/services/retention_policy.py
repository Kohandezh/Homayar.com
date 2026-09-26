"""Server-side retention policy of a workspace (WP07-T01, ADR 0020).

The workspace row is the only authority: clients never supply an expiry.
``retention_expiry_for`` is evaluated once at ingestion and stored on the
conversation and its media rows; ``is_age_based_deletion_enabled`` is
re-evaluated by every sweep before it treats a stored expiry as actionable,
so a policy change to ``INDEFINITE`` (or an unresolved legacy value) stops
age-based deletion even for rows that carry an old expiry.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from backend.database.saas_models import RetentionMode, Workspace

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
