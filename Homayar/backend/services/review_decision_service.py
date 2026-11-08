"""Criterion decisions and the human result of a review (HOM-74, ADR 0053).

A decision is one supervisor's judgment of one criterion of the machine result under
review: an ``accept`` confirms the machine outcome, an ``override`` changes it and needs
a reason code from the frozen list, a non-blank note of at most 2000 characters and one
to twenty turn indexes of the result's transcript revision as evidence.  The machine
rows are never written; the human judgment lives only in ``review_criterion_decisions``.

The checks of ``decide_criterion`` run in the SPEC's order: visible (404
``review_item_not_found``), the revision (409 ``review_item_revision_conflict``), active
(409 ``review_item_closed``), the assignee (403 ``review_not_assignee``), not the call's
agent (422 ``review_assignee_is_agent``), ``in_review`` (409 ``review_item_state_invalid``),
a result (409 ``review_item_has_no_result``), the engine's version (409
``scorecard_version_unsupported``), the criterion (404 ``criterion_not_found``), a
reviewable criterion (422 ``criterion_not_reviewable``), then the body rules.  A refusal
writes nothing and no audit event.

The write is the item's compare-and-set first, then the decision upsert, in one
transaction, so two concurrent decisions on one item never both land.  An identical
decision (the same stored form) writes nothing.  ``criteria_data`` builds the criteria
view: the machine score next to the human score (a preview while the item is active,
the stored ``review_results`` row after completion) and each criterion's machine outcome
next to its decision.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    REVIEW_DECISIONS,
    REVIEW_NOTE_MAX_LENGTH,
    REVIEW_OVERRIDE_REASONS,
    REVIEW_REVIEWER_OUTCOMES,
    CriterionResultRecord,
    ReviewCriterionDecision,
    ReviewResultRecord,
    ScorecardResultRecord,
    TranscriptRevision,
    new_id,
)
from backend.scorecards import SCORECARDS_VERSION
from backend.services.audit_event_types import AuditEventType
from backend.services.review_queue_service import (
    Change,
    ItemView,
    Reviewer,
    ReviewQueueError,
    _visible_item,
    _writable,
    compare_and_set,
    score_human_result,
)

# One criterion of the machine result is not reviewable only for this reason (README
# row 1): the conversation itself puts it out of scope, so no person may judge it.
NOT_APPLICABLE_BY_CONVERSATION = "not_applicable_by_conversation"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"
MAX_EVIDENCE_TURNS = 20


def _invalid_decision() -> ReviewQueueError:
    return ReviewQueueError(422, "review_decision_invalid", "The decision body is not valid")


def _note_too_long() -> ReviewQueueError:
    return ReviewQueueError(
        422, "review_note_invalid", f"note is longer than {REVIEW_NOTE_MAX_LENGTH}"
    )


def _checked_note(stored_note: str) -> str:
    """A stored note must be storable text, or ``422 review_note_invalid``.

    PostgreSQL ``TEXT`` refuses a NUL character, and a lone surrogate (for example
    ``\\ud800``) cannot be encoded as UTF-8 at all; both would reach the database only as
    a driver error, so the note's own code answers before anything is written.
    """
    if "\x00" in stored_note:
        raise ReviewQueueError(422, "review_note_invalid", "note must not contain a NUL character")
    try:
        stored_note.encode("utf-8")
    except UnicodeEncodeError:
        raise ReviewQueueError(
            422, "review_note_invalid", "note must be encodable as UTF-8"
        ) from None
    if len(stored_note) > REVIEW_NOTE_MAX_LENGTH:
        raise _note_too_long()
    return stored_note


def _result_of(db: Session, caller: Reviewer, item_result_id: str) -> ScorecardResultRecord:
    result = db.scalar(
        select(ScorecardResultRecord).where(
            ScorecardResultRecord.id == item_result_id,
            ScorecardResultRecord.workspace_id == caller.workspace_id,
        )
    )
    if result is None:  # the composite foreign key holds; this guards a broken row
        raise ReviewQueueError(404, "review_item_not_found", "Review item not found")
    if result.scorecards_version != SCORECARDS_VERSION:
        raise ReviewQueueError(
            409,
            "scorecard_version_unsupported",
            f"{result.scorecards_version} is not this engine's {SCORECARDS_VERSION}",
        )
    return result


def _segment_count(db: Session, result: ScorecardResultRecord) -> int:
    count = db.scalar(
        select(TranscriptRevision.segment_count).where(
            TranscriptRevision.id == result.transcript_revision_id
        )
    )
    return count or 0


def _evidence_indexes(value: Any, segment_count: int) -> list[int]:
    """The body's evidence as sorted turn indexes, or ``422 override_evidence_required``.

    One to twenty distinct integers, each a turn of the result's transcript revision.  A
    bool is not an integer (``true`` is refused), and neither is a float or a string.
    """
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_EVIDENCE_TURNS:
        raise ReviewQueueError(
            422, "override_evidence_required", "An override needs one to twenty turn indexes"
        )
    indexes: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ReviewQueueError(
                422, "override_evidence_required", "Every turn index is an integer"
            )
        if item < 0 or item >= segment_count:
            raise ReviewQueueError(
                422,
                "override_evidence_required",
                "Every turn index is a turn of the reviewed transcript revision",
            )
        indexes.append(item)
    if len(set(indexes)) != len(indexes):
        raise ReviewQueueError(422, "override_evidence_required", "A turn index twice")
    return sorted(indexes)


def decide_criterion(
    db: Session,
    caller: Reviewer,
    *,
    item_id: str,
    criterion_id: str,
    body: dict[str, Any],
    now: datetime,
) -> Change:
    """Write one criterion decision, or nothing when it is identical to the stored one.

    ``body`` is the request body as plain values (``ReviewDecisionRequest.model_dump()``);
    the model is loose, so every rule below answers with its own code.
    """
    expected_revision = body["expected_revision"]
    item, meeting = _writable(db, caller, item_id=item_id, expected_revision=expected_revision)
    if item.assignee_user_id != caller.user_id:
        raise ReviewQueueError(403, "review_not_assignee", "Only the assignee may do this")
    if meeting.agent_user_id == caller.user_id:
        raise ReviewQueueError(
            422, "review_assignee_is_agent", "Nobody reviews their own conversation"
        )
    if item.status != "in_review":
        raise ReviewQueueError(
            409, "review_item_state_invalid", "The review item must be in_review"
        )
    if item.scorecard_result_id is None:
        raise ReviewQueueError(
            409, "review_item_has_no_result", "The review item has no scorecard result"
        )
    result = _result_of(db, caller, item.scorecard_result_id)
    criterion = db.scalar(
        select(CriterionResultRecord).where(
            CriterionResultRecord.result_id == result.id,
            CriterionResultRecord.workspace_id == caller.workspace_id,
            CriterionResultRecord.criterion_id == criterion_id,
        )
    )
    if criterion is None:
        raise ReviewQueueError(404, "criterion_not_found", "Criterion not found")
    if criterion.reason == NOT_APPLICABLE_BY_CONVERSATION:
        raise ReviewQueueError(
            422, "criterion_not_reviewable", "The criterion does not apply to this conversation"
        )

    decision = body.get("decision")
    if not isinstance(decision, str) or decision not in REVIEW_DECISIONS:
        raise _invalid_decision()
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise ReviewQueueError(422, "review_note_invalid", "note must be a string")

    if decision == "accept":
        if body.get("reason_code") is not None or body.get("evidence_turn_indexes") is not None:
            raise _invalid_decision()
        reviewer = body.get("reviewer_outcome")
        if reviewer is not None and reviewer != criterion.outcome:
            raise _invalid_decision()
        if criterion.outcome == INSUFFICIENT_EVIDENCE:
            raise ReviewQueueError(
                422,
                "criterion_accept_not_possible",
                "An insufficient_evidence criterion needs an override",
            )
        stored_note = (note or "").strip() or None
        if stored_note is not None:
            stored_note = _checked_note(stored_note)
        stored_outcome = criterion.outcome
        stored_reason: str | None = None
        stored_evidence: list[dict[str, int]] = []
        indexes: list[int] = []
    else:
        reviewer = body.get("reviewer_outcome")
        if not isinstance(reviewer, str) or reviewer not in REVIEW_REVIEWER_OUTCOMES:
            raise _invalid_decision()
        if reviewer == criterion.outcome:
            raise ReviewQueueError(
                422, "override_matches_machine", "The outcome already is the machine outcome"
            )
        if reviewer == "not_applicable" and not criterion.na_allowed:
            raise ReviewQueueError(
                422, "reviewer_na_not_allowed", "The criterion may not be not applicable"
            )
        reason = body.get("reason_code")
        stored_note = note.strip() if isinstance(note, str) else None
        if (
            not isinstance(reason, str)
            or reason not in REVIEW_OVERRIDE_REASONS
            or stored_note is None
            or stored_note == ""
        ):
            raise ReviewQueueError(
                422, "override_reason_required", "An override needs a reason code and a note"
            )
        stored_note = _checked_note(stored_note)
        indexes = _evidence_indexes(
            body.get("evidence_turn_indexes"), _segment_count(db, result)
        )
        stored_outcome = reviewer
        stored_reason = reason
        stored_evidence = [{"turn_index": index} for index in indexes]

    existing = db.scalar(
        select(ReviewCriterionDecision).where(
            ReviewCriterionDecision.review_item_id == item.id,
            ReviewCriterionDecision.criterion_id == criterion_id,
        )
    )
    previous_decision = existing.decision if existing is not None else None
    previous_outcome = existing.reviewer_outcome if existing is not None else None
    if (
        existing is not None
        and existing.decision == decision
        and existing.reviewer_outcome == stored_outcome
        and existing.reason_code == stored_reason
        and existing.note == stored_note
        and list(existing.evidence) == stored_evidence
    ):
        # The same decision in the same stored form: no write, no revision change, no event.
        return Change(ItemView(item, meeting.team_id), None)

    # The compare-and-set comes first, so two concurrent decisions never both land.
    compare_and_set(db, item, expected_revision=expected_revision, now=now, values={})
    values = {
        "decision": decision,
        "machine_outcome": criterion.outcome,
        "reviewer_outcome": stored_outcome,
        "reason_code": stored_reason,
        "note": stored_note,
        "evidence": stored_evidence,
        "decided_by_user_id": caller.user_id,
        "updated_at": now,
    }
    if existing is None:
        db.add(
            ReviewCriterionDecision(
                id=new_id(),
                workspace_id=item.workspace_id,
                meeting_id=item.meeting_id,
                review_item_id=item.id,
                scorecard_result_id=result.id,
                criterion_id=criterion_id,
                created_at=now,
                **values,
            )
        )
    else:
        for column, value in values.items():
            setattr(existing, column, value)
    db.flush()
    return Change(
        ItemView(item, meeting.team_id),
        AuditEventType.REVIEW_CRITERION_DECIDED,
        {
            "criterion_id": criterion_id,
            "scorecard_result_id": result.id,
            "decision": decision,
            "machine_outcome": criterion.outcome,
            "reviewer_outcome": stored_outcome,
            "reason_code": stored_reason,
            "evidence_turn_indexes": indexes,
            "previous_decision": previous_decision,
            "previous_reviewer_outcome": previous_outcome,
            "revision": item.revision,
        },
    )


# --- the criteria view --------------------------------------------------------------------


def _score_fields(source: Any, result_json: dict[str, Any]) -> dict[str, Any]:
    """The ten score keys the SPEC lists, from a stored row and its ``result`` JSON."""
    return {
        "outcome": source.outcome,
        "reason": source.reason,
        "complete": source.complete,
        "score": result_json["score"],
        "score_min": result_json["score_min"],
        "score_max": result_json["score_max"],
        "pass_threshold": source.pass_threshold,
        "applicable_weight": source.applicable_weight,
        "earned_weight": source.earned_weight,
        "failed_weight": source.failed_weight,
        "insufficient_weight": source.insufficient_weight,
    }


def reviewer_outcomes_of(db: Session, item_id: str) -> dict[str, str]:
    """``{criterion_id: reviewer_outcome}`` from the item's current decisions."""
    rows = db.scalars(
        select(ReviewCriterionDecision).where(ReviewCriterionDecision.review_item_id == item_id)
    )
    return {row.criterion_id: row.reviewer_outcome for row in rows}


def criteria_data(db: Session, caller: Reviewer, *, item_id: str) -> dict[str, Any]:
    """The criteria view of one visible item; anything else is ``404`` like the queue."""
    item, _ = _visible_item(db, caller, item_id)
    data: dict[str, Any] = {
        "review_item_id": item.id,
        "revision": item.revision,
        "status": item.status,
        "scorecard_result_id": item.scorecard_result_id,
        "machine": None,
        "human": None,
        "criteria": [],
    }
    if item.scorecard_result_id is None:
        return data
    result = db.scalar(
        select(ScorecardResultRecord).where(
            ScorecardResultRecord.id == item.scorecard_result_id,
            ScorecardResultRecord.workspace_id == caller.workspace_id,
        )
    )
    if result is None:  # the composite foreign key holds; this guards a broken row
        return data
    data["machine"] = _score_fields(result, result.result)
    rows = list(
        db.scalars(
            select(CriterionResultRecord)
            .where(
                CriterionResultRecord.result_id == result.id,
                CriterionResultRecord.workspace_id == caller.workspace_id,
            )
            .order_by(CriterionResultRecord.position)
        )
    )
    decisions = {
        row.criterion_id: row
        for row in db.scalars(
            select(ReviewCriterionDecision).where(ReviewCriterionDecision.review_item_id == item.id)
        )
    }
    data["criteria"] = [
        {
            "criterion_id": row.criterion_id,
            "position": row.position,
            "weight": row.weight,
            "auto_fail": row.auto_fail,
            "na_allowed": row.na_allowed,
            "reviewable": row.reason != NOT_APPLICABLE_BY_CONVERSATION,
            "machine": {
                "outcome": row.outcome,
                "reason": row.reason,
                "decided_by": row.decided_by,
                "review_required": row.review_required,
                "review_reasons": row.review_reasons,
                "evidence": row.evidence,
            },
            "decision": (
                {
                    "decision": decision.decision,
                    "reviewer_outcome": decision.reviewer_outcome,
                    "reason_code": decision.reason_code,
                    "note": decision.note,
                    "evidence": decision.evidence,
                    "decided_by_user_id": decision.decided_by_user_id,
                    "updated_at": decision.updated_at,
                }
                if (decision := decisions.get(row.criterion_id)) is not None
                else None
            ),
        }
        for row in rows
    ]
    if item.status == "completed":
        human = db.scalar(
            select(ReviewResultRecord).where(ReviewResultRecord.review_item_id == item.id)
        )
        if human is not None:
            data["human"] = {**_score_fields(human, human.result), "final": True}
    elif item.status in ("open", "in_review"):
        try:
            preview = score_human_result(db, result, reviewer_outcomes_of(db, item.id))
        except ReviewQueueError:
            preview = None
        if preview is not None:
            data["human"] = {**_score_fields(preview, preview.to_json()), "final": False}
    return data
