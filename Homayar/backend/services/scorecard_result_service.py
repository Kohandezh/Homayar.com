"""Stored machine scores of conversations (HOM-56, WP11-T06).

Every read and write runs in the caller's tenant-bound session and also filters by
``workspace_id``; ``scorecard_results`` and ``scorecard_criterion_results`` are workspace
tables under row-level security.  The service scores the conversation itself with the
HOM-54 engine, from the stored scorecard version and the meeting's own conversation
columns.  A caller gives ids, a ``CheckRun`` and the clock: never a result, a score, the
conversation facts or reviewer outcomes (HOM-74), so a stored result holds only what the
engine computes.  A stored result never changes.  The same input returns the stored row;
only the conversation purge removes rows.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.conversation_checks import CheckRun
from backend.database.saas_models import (
    CriterionResultRecord,
    Meeting,
    ScorecardResultRecord,
    ScorecardVersion,
    TranscriptRevision,
)
from backend.scorecards import (
    SCORECARDS_VERSION,
    ConversationFacts,
    ScorecardError,
    definition_digest,
    score_conversation,
)

# The columns of a criterion row that copy ``result["criteria"][position]``.  The label
# stays in the definition.
CRITERION_COLUMNS = (
    "criterion_id",
    "weight",
    "auto_fail",
    "na_allowed",
    "outcome",
    "reason",
    "decided_by",
    "review_required",
    "review_reasons",
    "check_outcome",
    "check_reason",
    "evidence",
    "candidate_evidence",
)
# The columns of a result row that copy the same key of ``result``.
RESULT_COLUMNS = (
    "scorecards_version",
    "checks_version",
    "definition_digest",
    "outcome",
    "reason",
    "complete",
    "pass_threshold",
    "applicable_weight",
    "earned_weight",
    "failed_weight",
    "insufficient_weight",
)


class ScorecardResultError(ValueError):
    """A refused request.  ``code`` names the rule; nothing was written."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def scoring_input_digest(
    check_run: CheckRun | Mapping[str, Any],
    conversation_type: str,
    direction: str | None,
    transcript_revision_id: str,
) -> str:
    """SHA-256 hex of the canonical JSON of everything a score is computed from.

    ``check_run`` is a ``CheckRun`` or its ``to_json()``.  The canonical form is the one of
    ``definition_digest``: sorted keys, no spaces, UTF-8.
    """
    document = {
        "check_run": check_run.to_json() if isinstance(check_run, CheckRun) else check_run,
        "conversation": {"conversation_type": conversation_type, "direction": direction},
        "transcript_revision_id": transcript_revision_id,
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _version(db: Session, *, workspace_id: str, scorecard_version_id: str) -> ScorecardVersion:
    row = db.scalar(
        select(ScorecardVersion).where(
            ScorecardVersion.workspace_id == workspace_id,
            ScorecardVersion.id == scorecard_version_id,
        )
    )
    if row is None:
        raise ScorecardResultError("scorecard_version_not_found")
    _check_calculation_version(row.scorecards_version)
    if definition_digest(row.document) != row.digest:
        raise ScorecardResultError(
            "definition_digest_mismatch", "the stored definition does not match its digest"
        )
    return row


def _check_calculation_version(scorecards_version: str) -> None:
    # Never score with other rules than the ones that validated or computed the row.
    if scorecards_version != SCORECARDS_VERSION:
        raise ScorecardResultError(
            "calculation_version_mismatch",
            f"{scorecards_version} is not this engine's {SCORECARDS_VERSION}",
        )


def _meeting(db: Session, *, workspace_id: str, meeting_id: str) -> Meeting:
    # A deleted conversation is gone for every application path, and its purge may
    # already have removed its content: a result stored now would outlive it.
    meeting = db.scalar(
        select(Meeting).where(
            Meeting.workspace_id == workspace_id,
            Meeting.id == meeting_id,
            Meeting.deleted_at.is_(None),
        )
    )
    if meeting is None:
        raise ScorecardResultError("meeting_not_found")
    return meeting


def _check_revision(
    db: Session, *, workspace_id: str, meeting_id: str, transcript_revision_id: object
) -> None:
    found = isinstance(transcript_revision_id, str) and db.scalar(
        select(TranscriptRevision.id).where(
            TranscriptRevision.workspace_id == workspace_id,
            TranscriptRevision.meeting_id == meeting_id,
            TranscriptRevision.id == transcript_revision_id,
        )
    )
    if not found:
        raise ScorecardResultError(
            "transcript_revision_invalid", "the revision is not a revision of this meeting"
        )


def find_by_input(
    db: Session, *, workspace_id: str, scorecard_version_id: str, meeting_id: str, input_digest: str
) -> ScorecardResultRecord | None:
    """The stored result of one input, or ``None``."""
    return db.scalar(
        select(ScorecardResultRecord).where(
            ScorecardResultRecord.workspace_id == workspace_id,
            ScorecardResultRecord.scorecard_version_id == scorecard_version_id,
            ScorecardResultRecord.meeting_id == meeting_id,
            ScorecardResultRecord.input_digest == input_digest,
        )
    )


def record_result(
    db: Session,
    *,
    workspace_id: str,
    meeting_id: str,
    scorecard_version_id: str,
    check_run: CheckRun,
    transcript_revision_id: str,
    now: datetime,
) -> tuple[ScorecardResultRecord, bool]:
    """Score the conversation with the stored version and store the result.

    Returns ``(row, created)``.  The same input (version, meeting, check run, conversation
    facts and revision) returns the stored row and ``False``, also when a concurrent writer
    stored it first (``uq_scorecard_result_input``).  A refusal raises
    ``ScorecardResultError`` and an engine refusal ``ScorecardError``; neither writes
    anything.
    """
    if not isinstance(check_run, CheckRun):
        # A dict could carry any extra field into the stored input.
        raise ScorecardResultError("check_run_invalid", "check_run must be a CheckRun")
    version = _version(db, workspace_id=workspace_id, scorecard_version_id=scorecard_version_id)
    meeting = _meeting(db, workspace_id=workspace_id, meeting_id=meeting_id)
    _check_revision(
        db,
        workspace_id=workspace_id,
        meeting_id=meeting_id,
        transcript_revision_id=transcript_revision_id,
    )
    facts = ConversationFacts(meeting.conversation_type, meeting.direction)
    result = score_conversation(version.document, check_run, facts).to_json()
    input_digest = scoring_input_digest(
        check_run, meeting.conversation_type, meeting.direction, transcript_revision_id
    )
    lookup = {
        "workspace_id": workspace_id,
        "scorecard_version_id": scorecard_version_id,
        "meeting_id": meeting_id,
        "input_digest": input_digest,
    }
    existing = find_by_input(db, **lookup)
    if existing is not None:
        return existing, False

    # Every value comes from the engine output, the run and the checked ids.
    row = ScorecardResultRecord(
        workspace_id=workspace_id,
        meeting_id=meeting_id,
        scorecard_version_id=scorecard_version_id,
        transcript_revision_id=transcript_revision_id,
        input_digest=input_digest,
        conversation_type=result["conversation"]["conversation_type"],
        direction=result["conversation"]["direction"],
        check_run=check_run.to_json(),
        result=result,
        created_at=now,
        **{column: result[column] for column in RESULT_COLUMNS},
    )
    savepoint = db.begin_nested()
    try:
        db.add(row)
        db.flush()
        db.add_all(
            CriterionResultRecord(
                workspace_id=workspace_id,
                meeting_id=meeting_id,
                result_id=row.id,
                position=position,
                created_at=now,
                **{column: criterion[column] for column in CRITERION_COLUMNS},
            )
            for position, criterion in enumerate(result["criteria"])
        )
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        existing = find_by_input(db, **lookup)
        if existing is None:  # not the concurrent insert of the same input
            raise
        return existing, False
    return row, True


def get_result(db: Session, *, workspace_id: str, result_id: str) -> ScorecardResultRecord | None:
    return db.scalar(
        select(ScorecardResultRecord).where(
            ScorecardResultRecord.workspace_id == workspace_id,
            ScorecardResultRecord.id == result_id,
        )
    )


def list_results(db: Session, *, workspace_id: str, meeting_id: str) -> list[ScorecardResultRecord]:
    """The conversation's results, newest first; empty for an unknown conversation."""
    return list(
        db.scalars(
            select(ScorecardResultRecord)
            .where(
                ScorecardResultRecord.workspace_id == workspace_id,
                ScorecardResultRecord.meeting_id == meeting_id,
            )
            .order_by(ScorecardResultRecord.created_at.desc(), ScorecardResultRecord.id.desc())
        )
    )


def criterion_rows(
    db: Session, *, workspace_id: str, result_id: str
) -> list[CriterionResultRecord]:
    """The result's criterion rows in definition order."""
    return list(
        db.scalars(
            select(CriterionResultRecord)
            .where(
                CriterionResultRecord.workspace_id == workspace_id,
                CriterionResultRecord.result_id == result_id,
            )
            .order_by(CriterionResultRecord.position)
        )
    )


def verify_result(db: Session, *, workspace_id: str, result_id: str) -> bool:
    """Whether a fresh computation from the stored input gives the stored row again.

    True only when the engine gives exactly the stored ``result`` from the stored
    definition, check run and conversation columns, and every copied column and criterion
    row still equals it.  A stored input the engine now refuses is not reproducible: false.
    """
    row = get_result(db, workspace_id=workspace_id, result_id=result_id)
    if row is None:
        raise ScorecardResultError("result_not_found")
    _check_calculation_version(row.scorecards_version)
    version = db.scalar(
        select(ScorecardVersion).where(
            ScorecardVersion.workspace_id == workspace_id,
            ScorecardVersion.id == row.scorecard_version_id,
        )
    )
    if version is None:  # the cascade removes the results of a removed version
        raise ScorecardResultError("scorecard_version_not_found")
    facts = ConversationFacts(row.conversation_type, row.direction)
    try:
        fresh = score_conversation(version.document, row.check_run, facts).to_json()
    except ScorecardError:
        return False
    rows = criterion_rows(db, workspace_id=workspace_id, result_id=row.id)
    stored = [{column: getattr(c, column) for column in CRITERION_COLUMNS} for c in rows]
    expected = [{column: c[column] for column in CRITERION_COLUMNS} for c in fresh["criteria"]]
    return (
        fresh == row.result
        and stored == expected
        and [c.position for c in rows] == list(range(len(expected)))
        and all(getattr(row, column) == fresh[column] for column in RESULT_COLUMNS)
        and row.definition_digest == version.digest
        and row.input_digest
        == scoring_input_digest(
            row.check_run, row.conversation_type, row.direction, row.transcript_revision_id
        )
    )
