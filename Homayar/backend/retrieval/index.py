"""The indexer of the retrieval index (HOM-93, ADR 0068 D1, D6, D7).

:func:`index_text_view` runs in the caller's tenant-bound transaction, in its
own savepoint, right after the processor derives a text view.  It writes one
``retrieval_entries`` row per non-empty text-view segment of the view.  Each
row copies the segment's lineage and text (already redacted under an enforced
policy) and a snapshot of the record ACL.  It never raises: a fault rolls back
the savepoint and is reported in the outcome, so an index failure never fails
the transcript.

Two rules run before anything is written:

* the *policy rule*: every entry of the meeting whose text view was derived
  under another redaction policy, and every entry of another view of the same
  revision, is deleted.  So after a stricter policy the old unredacted text
  leaves the index when the new view is indexed.  (The read primitives already
  hide it before that, because they keep only entries of the workspace's
  current reader view.)
* the *revision rule*: live entries of the meeting's other revisions under the
  same policy get ``superseded_at`` set.  History stays in the table, labeled,
  and only a caller that asks for history sees it.

:func:`refresh_meeting_acl` re-syncs the snapshot of every entry of a meeting
in the request that changes its team or agent.  The snapshot is a pre-filter
only (ADR 0068 D6): a stale one can never leak, because every read joins the
live meeting row, but it should not drift either.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    Meeting,
    RetrievalEntry,
    StageState,
    TextViewSegment,
    TranscriptRevision,
    TranscriptTextView,
)
from backend.services.logging_service import logger

# Names the index layout and rules.  A change to what is indexed writes new
# rows under a new version; it never overwrites the old ones.
INDEX_VERSION = "retrieval-index/1"

REASON_VIEW_MISSING = "view_missing"
REASON_VIEW_NOT_USABLE = "view_not_usable"
REASON_MEETING_DELETED = "meeting_deleted"
REASON_REVISION_NOT_CURRENT = "revision_not_current"
REASON_INDEX_FAILED = "index_failed"

USABLE_STATES = (StageState.COMPLETED, StageState.PARTIAL)


@dataclass(frozen=True)
class IndexOutcome:
    """What the indexer reports about one view (states and counts only)."""

    state: str  # "indexed", "skipped", "failed"
    reason_code: str | None
    entry_count: int
    superseded_count: int
    removed_count: int


def _skipped(reason: str) -> IndexOutcome:
    return IndexOutcome("skipped", reason, 0, 0, 0)


def _same_policy_views(view: TranscriptTextView) -> Select:
    """The ids of the views of this meeting derived under this view's policy."""
    digest_matches = (
        TranscriptTextView.input_policy_digest.is_(None)
        if view.input_policy_digest is None
        else TranscriptTextView.input_policy_digest == view.input_policy_digest
    )
    return select(TranscriptTextView.id).where(
        TranscriptTextView.workspace_id == view.workspace_id,
        TranscriptTextView.meeting_id == view.meeting_id,
        TranscriptTextView.input_view == view.input_view,
        digest_matches,
    )


def index_text_view(db: Session, *, view_id: str, workspace_id: str, now: datetime) -> IndexOutcome:
    """Index the view ``view_id`` of ``workspace_id``.  Never raises.

    The view is read inside ``workspace_id`` only: a view of another workspace
    is ``view_missing``.  ``workspace_id`` and ``meeting_id`` of each entry
    are copied from its ``text_view_segments`` row, never from the arguments
    (SPEC Revision 2 R2).
    """
    savepoint = None
    try:
        savepoint = db.begin_nested()
        outcome = _index(db, view_id=view_id, workspace_id=workspace_id, now=now)
        savepoint.commit()
        return outcome
    except Exception as exc:  # the job must never fail because of the index
        if savepoint is not None:
            # Always roll the savepoint back.  After a database error in a
            # flush the savepoint is no longer "active", but it still has to
            # be rolled back, or the caller's session stays unusable
            # (PendingRollbackError) and the job fails.
            try:
                savepoint.rollback()
            except Exception as rollback_exc:  # noqa: BLE001 - never raise
                logger.error(
                    "RETRIEVAL_INDEX_ROLLBACK_FAILED view_id=%s error_type=%s",
                    view_id,
                    type(rollback_exc).__name__,
                )
        # Ids and the error class only.  Never the text of the conversation:
        # a database error message can quote the row it refused.
        logger.error(
            "RETRIEVAL_INDEX_FAILED view_id=%s workspace_id=%s error_type=%s",
            view_id,
            workspace_id,
            type(exc).__name__,
        )
        return IndexOutcome("failed", REASON_INDEX_FAILED, 0, 0, 0)


def _index(db: Session, *, view_id: str, workspace_id: str, now: datetime) -> IndexOutcome:
    row = db.execute(
        select(TranscriptTextView, Meeting, TranscriptRevision)
        .join(Meeting, Meeting.id == TranscriptTextView.meeting_id)
        .join(TranscriptRevision, TranscriptRevision.id == TranscriptTextView.revision_id)
        .where(
            TranscriptTextView.id == view_id,
            TranscriptTextView.workspace_id == workspace_id,
        )
    ).first()
    if row is None:
        return _skipped(REASON_VIEW_MISSING)
    view, meeting, revision = row
    if view.state not in USABLE_STATES:
        return _skipped(REASON_VIEW_NOT_USABLE)
    if meeting.deleted_at is not None:
        return _skipped(REASON_MEETING_DELETED)
    if not revision.is_current:
        return _skipped(REASON_REVISION_NOT_CURRENT)

    same_policy = _same_policy_views(view)
    # The policy rule.  This view's own entries are never touched.
    removed_count = db.execute(
        delete(RetrievalEntry)
        .where(
            RetrievalEntry.workspace_id == view.workspace_id,
            RetrievalEntry.meeting_id == view.meeting_id,
            RetrievalEntry.text_view_id != view.id,
            or_(
                RetrievalEntry.revision_id == revision.id,
                RetrievalEntry.text_view_id.not_in(same_policy),
            ),
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    # The revision rule.
    superseded_count = db.execute(
        update(RetrievalEntry)
        .where(
            RetrievalEntry.workspace_id == view.workspace_id,
            RetrievalEntry.meeting_id == view.meeting_id,
            RetrievalEntry.revision_id != revision.id,
            RetrievalEntry.superseded_at.is_(None),
            RetrievalEntry.text_view_id.in_(same_policy),
        )
        .values(superseded_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount

    # Idempotent: a segment already indexed under this version is skipped,
    # so a second call writes nothing (unique segment + index version).
    existing = set(
        db.scalars(
            select(RetrievalEntry.text_view_segment_id).where(
                RetrievalEntry.text_view_id == view.id,
                RetrievalEntry.index_version == INDEX_VERSION,
            )
        )
    )
    segments = db.scalars(
        select(TextViewSegment)
        .where(TextViewSegment.view_id == view.id)
        .order_by(TextViewSegment.sequence)
    ).all()
    for segment in segments:
        if segment.id in existing or not segment.text.strip():
            continue
        db.add(
            RetrievalEntry(
                workspace_id=segment.workspace_id,
                meeting_id=segment.meeting_id,
                revision_id=revision.id,
                text_view_id=view.id,
                text_view_segment_id=segment.id,
                segment_id=segment.segment_id,
                sequence=segment.sequence,
                revision_number=revision.revision_number,
                language=view.language,
                text=segment.text,
                text_sha256=hashlib.sha256(segment.text.encode("utf-8")).hexdigest(),
                index_version=INDEX_VERSION,
                team_id=meeting.team_id,
                agent_user_id=meeting.agent_user_id,
                created_by_user_id=meeting.created_by_user_id,
                acl_synced_at=now,
                superseded_at=None,
            )
        )
    db.flush()
    entry_count = db.scalar(
        select(func.count())
        .select_from(RetrievalEntry)
        .where(
            RetrievalEntry.text_view_id == view.id,
            RetrievalEntry.index_version == INDEX_VERSION,
        )
    )
    return IndexOutcome("indexed", None, entry_count or 0, superseded_count, removed_count)


def refresh_meeting_acl(db: Session, *, meeting: Meeting, now: datetime) -> int:
    """Re-sync the ACL snapshot of every entry of ``meeting``.  Returns the row count.

    One UPDATE, in the caller's transaction (the request that changed the
    call's team or agent).  It reads the meeting object as the caller set it,
    so it runs after the new fields are assigned and before the commit.
    """
    return db.execute(
        update(RetrievalEntry)
        .where(
            RetrievalEntry.workspace_id == meeting.workspace_id,
            RetrievalEntry.meeting_id == meeting.id,
        )
        .values(
            team_id=meeting.team_id,
            agent_user_id=meeting.agent_user_id,
            created_by_user_id=meeting.created_by_user_id,
            acl_synced_at=now,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
