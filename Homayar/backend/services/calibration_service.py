"""Blinded calibration batches and the agreement report (HOM-75, ADR 0056).

A review admin builds a batch: a fixed list of machine results (examples) and a fixed
list of reviewers (participants).  Each participant gives their own outcome on every
reviewable criterion of every example (an answer).  A review admin who is not a
participant records the correct outcome (an adjudication).  Closing the batch unlocks
the agreement report.  Until close nobody sees another participant's answers, a
participant sees no adjudication, and no response holds a machine outcome, reason or
evidence.  Calibration never writes a review item, a decision, a human result or a
machine result.

The checks run in the ADR 0056 order.  The request schemas already refused a bad body
(the app's usual 422).  ``create_batch``: a review admin (403), then every result (in
the workspace with a live conversation, one conversation once, a reviewable
criterion), then every participant against every example with the HOM-73 assignee
rule (the agent check first, over every example).  ``answer_criterion``: visible
(404), a participant (403), the batch open (409), the example in the batch (404), the
caller still eligible (422, then 403), the criterion (404), reviewable (422),
``not_applicable`` only when ``na_allowed`` (422).  ``adjudicate_criterion``: visible,
a review admin (403), not a participant (403), open, the example, not the
conversation's current agent (422), the criterion, the na rule.  ``close_batch`` and
``cancel_batch``: visible, a review admin, the revision (409 with
``current_revision``), open, and for close every reviewable criterion adjudicated (409
with ``missing``).  A refusal writes nothing and no audit event.

Concurrency: an answer or an adjudication locks the batch row ``FOR SHARE`` before it
checks the status.  A close or a cancel locks the row ``FOR UPDATE`` before it reads
the revision, the status and the rows, and then writes a compare-and-set ``UPDATE ...
WHERE id AND workspace_id AND revision = :expected AND status = 'open'``.  So a close
waits for every answer that holds the share lock and then reads it, an answer that
comes after a close sees ``closed`` and refuses, and the answers present at close are
exactly the answers the report reads.  Answers and adjudications do not change the
batch revision.

The report is computed on read from the frozen rows; it is not stored.  Every number
is an exact integer computed with ``fractions.Fraction``, never a float.
``agreement_bp`` rounds half up and ``kappa_e4`` rounds half away from zero.  Python's
``round`` rounds half to even, so it is never used.

The audit details carry ids, counts and revisions only: never an outcome, a note or
the title, because admins read the audit log and an admin may be a participant.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fractions import Fraction
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    Base,
    CalibrationAdjudication,
    CalibrationAnswer,
    CalibrationBatch,
    CalibrationExample,
    CalibrationParticipant,
    CriterionResultRecord,
    Meeting,
    ScorecardResultRecord,
    new_id,
)
from backend.jobs.leases import as_utc
from backend.services.audit_event_types import AuditEventType
from backend.services.review_decision_service import NOT_APPLICABLE_BY_CONVERSATION
from backend.services.review_queue_service import (
    Reviewer,
    ReviewQueueError,
    check_assignee,
)

BASIS_POINTS = 10000


# --- the refusals, each with its own code ---------------------------------------------


def _not_found() -> ReviewQueueError:
    return ReviewQueueError(404, "calibration_batch_not_found", "Calibration batch not found")


def _admin_required() -> ReviewQueueError:
    return ReviewQueueError(403, "calibration_admin_required", "Only a review admin may do this")


def _not_participant() -> ReviewQueueError:
    return ReviewQueueError(403, "calibration_not_participant", "Only a participant may answer")


def _adjudicator_is_participant() -> ReviewQueueError:
    return ReviewQueueError(
        403, "calibration_adjudicator_is_participant", "An adjudicator may not be a participant"
    )


def _closed() -> ReviewQueueError:
    return ReviewQueueError(409, "calibration_batch_closed", "The batch is not open")


def _not_closed() -> ReviewQueueError:
    return ReviewQueueError(409, "calibration_batch_not_closed", "The batch is not closed")


def _revision_conflict(current_revision: int) -> ReviewQueueError:
    return ReviewQueueError(
        409,
        "calibration_batch_revision_conflict",
        "The batch changed since it was read; reload and retry",
        {"current_revision": current_revision},
    )


def _adjudication_incomplete(missing: int) -> ReviewQueueError:
    return ReviewQueueError(
        409,
        "calibration_adjudication_incomplete",
        "Every reviewable criterion needs its adjudication before close",
        {"missing": missing},
    )


def _example_not_found() -> ReviewQueueError:
    return ReviewQueueError(404, "calibration_example_not_found", "Example not found")


def _criterion_not_found() -> ReviewQueueError:
    return ReviewQueueError(404, "criterion_not_found", "Criterion not found")


def _criterion_not_reviewable() -> ReviewQueueError:
    return ReviewQueueError(
        422, "criterion_not_reviewable", "The criterion does not apply to this conversation"
    )


def _na_not_allowed() -> ReviewQueueError:
    return ReviewQueueError(
        422, "reviewer_na_not_allowed", "The criterion may not be not applicable"
    )


def _result_invalid(scorecard_result_id: str) -> ReviewQueueError:
    return ReviewQueueError(
        422,
        "calibration_result_invalid",
        "Every result must be of this workspace and a live conversation",
        {"scorecard_result_id": scorecard_result_id},
    )


def _duplicate_conversation(meeting_id: str) -> ReviewQueueError:
    return ReviewQueueError(
        422,
        "calibration_duplicate_conversation",
        "One conversation at most once per batch",
        {"meeting_id": meeting_id},
    )


def _result_not_reviewable(scorecard_result_id: str) -> ReviewQueueError:
    return ReviewQueueError(
        422,
        "calibration_result_not_reviewable",
        "Every result needs a reviewable criterion",
        {"scorecard_result_id": scorecard_result_id},
    )


def _participant_is_agent(user_id: str, meeting_id: str) -> ReviewQueueError:
    return ReviewQueueError(
        422,
        "calibration_participant_is_agent",
        "Nobody calibrates their own conversation",
        {"user_id": user_id, "meeting_id": meeting_id},
    )


def _participant_ineligible(user_id: str, meeting_id: str, status_code: int) -> ReviewQueueError:
    return ReviewQueueError(
        status_code,
        "calibration_participant_ineligible",
        "The participant needs review authority over every example's conversation",
        {"user_id": user_id, "meeting_id": meeting_id},
    )


def _adjudicator_is_agent(user_id: str, meeting_id: str) -> ReviewQueueError:
    return ReviewQueueError(
        422,
        "calibration_adjudicator_is_agent",
        "The adjudicator may not be the conversation's agent",
        {"user_id": user_id, "meeting_id": meeting_id},
    )


def _invalid_cursor() -> ReviewQueueError:
    return ReviewQueueError(400, "invalid_cursor", "Invalid calibration batch pagination cursor")


def _check_eligible(
    db: Session,
    *,
    workspace_id: str,
    meeting: Meeting,
    user_id: str,
    now: datetime,
    ineligible_status: int,
) -> None:
    """The HOM-73 assignee rule (the agent check first), with the calibration codes.

    ``check_assignee`` itself never changes: its review codes are mapped here.  At
    create an ineligible participant is a 422 (a bad body); at answer time it is a 403
    (the caller lost the authority after the batch was created).
    """
    try:
        check_assignee(
            db, workspace_id=workspace_id, meeting=meeting, assignee_user_id=user_id, now=now
        )
    except ReviewQueueError as error:
        if error.code == "review_assignee_is_agent":
            raise _participant_is_agent(user_id, meeting.id) from error
        raise _participant_ineligible(user_id, meeting.id, ineligible_status) from error


def _reviewable_clause() -> Any:
    # ``reason`` is NULL for most criteria; ``!=`` alone would drop those rows in SQL.
    return or_(
        CriterionResultRecord.reason.is_(None),
        CriterionResultRecord.reason != NOT_APPLICABLE_BY_CONVERSATION,
    )


def _is_reviewable(row: CriterionResultRecord) -> bool:
    return row.reason != NOT_APPLICABLE_BY_CONVERSATION


# --- the report's math, pure --------------------------------------------------------------


def round_half_up(value: Fraction) -> int:
    """``value`` rounded half up (0.5 -> 1); for the report's non-negative numbers."""
    return math.floor(value + Fraction(1, 2))


def round_half_away(value: Fraction) -> int:
    """``value`` rounded half away from zero (-5312.5 -> -5313); for ``kappa_e4``."""
    if value >= 0:
        return math.floor(value + Fraction(1, 2))
    return -math.floor(-value + Fraction(1, 2))


def agreement_row(pairs: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """The agreement numbers of the compared ``pairs``, each ``(adjudicated, answered)``.

    ``agreement_bp`` is ``matched * 10000 / compared`` rounded half up.  ``kappa_e4``
    is Cohen's kappa times 10000 rounded half away from zero, with
    ``p_o = matched / n`` and ``p_e = sum(answers_k * gold_k) / n^2``.  Both are
    ``None`` when nothing is compared; ``kappa_e4`` is also ``None`` when ``p_e = 1``.
    ``confusion`` maps the adjudicated outcome, then the answer outcome, to a count;
    only non-zero counts appear.
    """
    compared = len(pairs)
    matched = sum(1 for gold, answered in pairs if gold == answered)
    confusion: dict[str, dict[str, int]] = {}
    for gold, answered in pairs:
        row = confusion.setdefault(gold, {})
        row[answered] = row.get(answered, 0) + 1
    if compared == 0:
        return {
            "compared": 0,
            "matched": 0,
            "agreement_bp": None,
            "kappa_e4": None,
            "confusion": confusion,
        }
    answers = Counter(answered for _, answered in pairs)
    golds = Counter(gold for gold, _ in pairs)
    p_o = Fraction(matched, compared)
    p_e = Fraction(sum(answers[outcome] * golds[outcome] for outcome in golds), compared**2)
    kappa_e4 = None if p_e == 1 else round_half_away((p_o - p_e) / (1 - p_e) * BASIS_POINTS)
    return {
        "compared": compared,
        "matched": matched,
        "agreement_bp": round_half_up(Fraction(matched * BASIS_POINTS, compared)),
        "kappa_e4": kappa_e4,
        "confusion": confusion,
    }


def agreement_block(pairs: Sequence[tuple[str, str]], *, expected: int) -> dict[str, Any]:
    """A report block: ``expected``, the row numbers, and ``missing = expected - compared``."""
    row = agreement_row(pairs)
    return {
        "expected": expected,
        "compared": row["compared"],
        "matched": row["matched"],
        "missing": expected - row["compared"],
        "agreement_bp": row["agreement_bp"],
        "kappa_e4": row["kappa_e4"],
        "confusion": row["confusion"],
    }


@dataclass(frozen=True)
class Pair:
    """One (example, reviewable criterion) with its adjudication: the report's unit."""

    example_id: str
    criterion_id: str
    gold: str  # the adjudicated outcome
    machine: str  # the stored machine outcome (``insufficient_evidence`` never matches)


def report_numbers(
    pairs: Sequence[Pair],
    participant_ids: Sequence[str],
    answers: Mapping[tuple[str, str, str], str],
) -> dict[str, Any]:
    """Every number of one batch's report, pure.

    ``answers`` maps ``(participant_id, example_id, criterion_id)`` to the outcome.
    Returns ``overall`` (every participant pooled, ``expected`` = pairs times
    participants), ``participants`` (participant id -> block), ``criteria`` (per
    criterion id over every participant and example, in the order the pairs first
    name them) and ``machine`` (the machine outcome of each pair).
    """
    per_participant: dict[str, dict[str, Any]] = {}
    pooled: list[tuple[str, str]] = []
    by_criterion: dict[str, list[tuple[str, str]]] = {pair.criterion_id: [] for pair in pairs}
    for participant_id in participant_ids:
        compared: list[tuple[str, str]] = []
        for pair in pairs:
            outcome = answers.get((participant_id, pair.example_id, pair.criterion_id))
            if outcome is None:
                continue
            compared.append((pair.gold, outcome))
            by_criterion[pair.criterion_id].append((pair.gold, outcome))
        per_participant[participant_id] = agreement_block(compared, expected=len(pairs))
        pooled.extend(compared)
    criteria = []
    for criterion_id, criterion_pairs in by_criterion.items():
        row = agreement_row(criterion_pairs)
        criteria.append(
            {
                "criterion_id": criterion_id,
                "compared": row["compared"],
                "matched": row["matched"],
                "agreement_bp": row["agreement_bp"],
            }
        )
    return {
        "overall": agreement_block(pooled, expected=len(pairs) * len(participant_ids)),
        "participants": per_participant,
        "criteria": criteria,
        "machine": agreement_block(
            [(pair.gold, pair.machine) for pair in pairs], expected=len(pairs)
        ),
    }


# --- the reads of the frozen rows ---------------------------------------------------------


@dataclass(frozen=True)
class ExampleRows:
    """One example with its stored criterion rows, in definition order."""

    example: CalibrationExample
    criteria: list[CriterionResultRecord]

    @property
    def reviewable(self) -> list[CriterionResultRecord]:
        return [row for row in self.criteria if _is_reviewable(row)]


@dataclass(frozen=True)
class Gathered:
    """Everything the view, the close and the report read from one batch."""

    batch: CalibrationBatch
    examples: list[ExampleRows]
    participants: list[CalibrationParticipant]
    answers: list[CalibrationAnswer]
    adjudications: list[CalibrationAdjudication]

    def reviewable_count(self) -> int:
        """The reviewable criteria over every example: what each participant answers."""
        return sum(len(rows.reviewable) for rows in self.examples)

    def missing_adjudications(self) -> int:
        adjudicated = {(row.example_id, row.criterion_id) for row in self.adjudications}
        return sum(
            1
            for rows in self.examples
            for criterion in rows.reviewable
            if (rows.example.id, criterion.criterion_id) not in adjudicated
        )

    def pairs(self) -> list[Pair]:
        """The (example, reviewable criterion) pairs with an adjudication, in order."""
        adjudicated = {
            (row.example_id, row.criterion_id): row.outcome for row in self.adjudications
        }
        pairs = []
        for rows in self.examples:
            for criterion in rows.reviewable:
                gold = adjudicated.get((rows.example.id, criterion.criterion_id))
                if gold is not None:
                    pairs.append(
                        Pair(rows.example.id, criterion.criterion_id, gold, criterion.outcome)
                    )
        return pairs

    def answer_map(self) -> dict[tuple[str, str, str], str]:
        return {
            (row.participant_id, row.example_id, row.criterion_id): row.outcome
            for row in self.answers
        }

    def numbers(self) -> dict[str, Any]:
        return report_numbers(
            self.pairs(), [row.id for row in self.participants], self.answer_map()
        )


def _gather(db: Session, batch: CalibrationBatch) -> Gathered:
    examples = list(
        db.scalars(
            select(CalibrationExample)
            .where(CalibrationExample.batch_id == batch.id)
            .order_by(CalibrationExample.position)
        )
    )
    criteria = (
        list(
            db.scalars(
                select(CriterionResultRecord)
                .where(
                    CriterionResultRecord.workspace_id == batch.workspace_id,
                    CriterionResultRecord.result_id.in_(
                        [example.scorecard_result_id for example in examples]
                    ),
                )
                .order_by(CriterionResultRecord.position)
            )
        )
        if examples
        else []
    )
    by_result: dict[str, list[CriterionResultRecord]] = {}
    for row in criteria:
        by_result.setdefault(row.result_id, []).append(row)
    return Gathered(
        batch=batch,
        examples=[
            ExampleRows(example, by_result.get(example.scorecard_result_id, []))
            for example in examples
        ],
        participants=list(
            db.scalars(
                select(CalibrationParticipant)
                .where(CalibrationParticipant.batch_id == batch.id)
                .order_by(CalibrationParticipant.created_at, CalibrationParticipant.id)
            )
        ),
        answers=list(
            db.scalars(
                select(CalibrationAnswer)
                .where(CalibrationAnswer.batch_id == batch.id)
                .order_by(CalibrationAnswer.created_at, CalibrationAnswer.id)
            )
        ),
        adjudications=list(
            db.scalars(
                select(CalibrationAdjudication)
                .where(CalibrationAdjudication.batch_id == batch.id)
                .order_by(CalibrationAdjudication.created_at, CalibrationAdjudication.id)
            )
        ),
    )


# --- visibility and locks -----------------------------------------------------------------


def _participant_of(db: Session, batch_id: str, user_id: str) -> CalibrationParticipant | None:
    return db.scalar(
        select(CalibrationParticipant).where(
            CalibrationParticipant.batch_id == batch_id,
            CalibrationParticipant.user_id == user_id,
        )
    )


def _visible_batch(db: Session, caller: Reviewer, batch_id: str) -> CalibrationBatch:
    """The batch for an admin or a participant; anything else is the 404, never 403."""
    batch = db.scalar(
        select(CalibrationBatch).where(
            CalibrationBatch.id == batch_id,
            CalibrationBatch.workspace_id == caller.workspace_id,
        )
    )
    if batch is None:
        raise _not_found()
    if not caller.is_admin and _participant_of(db, batch.id, caller.user_id) is None:
        raise _not_found()
    return batch


def _locked_batch(db: Session, batch: CalibrationBatch, *, share: bool) -> CalibrationBatch:
    """The batch row again, locked ``FOR SHARE`` (a write) or ``FOR UPDATE`` (a close).

    The lock comes before the status check, so a write that waits behind a close
    sees the closed row, and a close that waits behind a write reads that write.
    ``populate_existing`` replaces the values read before the wait.
    """
    locked = db.scalar(
        select(CalibrationBatch)
        .where(CalibrationBatch.id == batch.id, CalibrationBatch.workspace_id == batch.workspace_id)
        .with_for_update(read=share)
        .execution_options(populate_existing=True)
    )
    if locked is None:  # removed between the two reads
        raise _not_found()
    return locked


def _insert(db: Session, row: Base) -> bool:
    """Add ``row`` in a savepoint; ``False`` when a unique or foreign key refuses it.

    Two racing first writes of the same answer (a double click) both find no row;
    the later one then reads the row the other stored instead of failing.
    """
    savepoint = db.begin_nested()
    try:
        db.add(row)
        db.flush()
    except IntegrityError:
        savepoint.rollback()
        return False
    savepoint.commit()
    return True


# --- the change a write returns -------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    """A write's result: the batch view for the caller, and its audit event.

    ``event`` is ``None`` when nothing changed (no audit event, no commit).  Every
    event targets the batch.
    """

    view: dict[str, Any]
    event: AuditEventType | None = None
    target_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    target_type: str = "calibration_batch"


# --- create ---------------------------------------------------------------------------------


def create_batch(
    db: Session,
    caller: Reviewer,
    *,
    title: str,
    scorecard_result_ids: Sequence[str],
    participant_user_ids: Sequence[str],
    now: datetime,
) -> Change:
    """Create a batch (201) with its examples in the given order and its participants.

    The body's shape (title, counts, repeated ids) was checked by the request schema.
    """
    if not caller.is_admin:
        raise _admin_required()

    meetings: list[Meeting] = []
    for scorecard_result_id in scorecard_result_ids:
        meeting = db.scalar(
            select(Meeting)
            .join(ScorecardResultRecord, ScorecardResultRecord.meeting_id == Meeting.id)
            .where(
                ScorecardResultRecord.id == scorecard_result_id,
                ScorecardResultRecord.workspace_id == caller.workspace_id,
                Meeting.workspace_id == caller.workspace_id,
                Meeting.deleted_at.is_(None),
            )
        )
        if meeting is None:
            raise _result_invalid(scorecard_result_id)
        meetings.append(meeting)
    seen: set[str] = set()
    for meeting in meetings:
        if meeting.id in seen:
            raise _duplicate_conversation(meeting.id)
        seen.add(meeting.id)
    reviewable = set(
        db.scalars(
            select(CriterionResultRecord.result_id)
            .where(
                CriterionResultRecord.workspace_id == caller.workspace_id,
                CriterionResultRecord.result_id.in_(list(scorecard_result_ids)),
                _reviewable_clause(),
            )
            .distinct()
        )
    )
    for scorecard_result_id in scorecard_result_ids:
        if scorecard_result_id not in reviewable:
            raise _result_not_reviewable(scorecard_result_id)
    for user_id in participant_user_ids:
        # The agent check comes first, over every example: the agent of any example's
        # conversation is ``calibration_participant_is_agent`` whatever else fails.
        for meeting in meetings:
            if meeting.agent_user_id == user_id:
                raise _participant_is_agent(user_id, meeting.id)
        # The rest of the rule reads only the conversation's team, so one check per
        # (team, agent) is enough; the first failing example in order is the one named.
        eligible_for: set[tuple[str | None, str | None]] = set()
        for meeting in meetings:
            scope = (meeting.team_id, meeting.agent_user_id)
            if scope in eligible_for:
                continue
            _check_eligible(
                db,
                workspace_id=caller.workspace_id,
                meeting=meeting,
                user_id=user_id,
                now=now,
                ineligible_status=422,
            )
            eligible_for.add(scope)

    batch = CalibrationBatch(
        id=new_id(),
        workspace_id=caller.workspace_id,
        title=title,
        status="open",
        created_by_user_id=caller.user_id,
        created_at=now,
        updated_at=now,
        revision=1,
    )
    db.add(batch)
    db.flush()
    for position, (scorecard_result_id, meeting) in enumerate(
        zip(scorecard_result_ids, meetings, strict=True)
    ):
        db.add(
            CalibrationExample(
                id=new_id(),
                workspace_id=caller.workspace_id,
                batch_id=batch.id,
                meeting_id=meeting.id,
                scorecard_result_id=scorecard_result_id,
                position=position,
                created_at=now,
            )
        )
    for user_id in participant_user_ids:
        db.add(
            CalibrationParticipant(
                id=new_id(),
                workspace_id=caller.workspace_id,
                batch_id=batch.id,
                user_id=user_id,
                created_at=now,
            )
        )
    db.flush()
    return Change(
        view=batch_view(db, caller, batch_id=batch.id),
        event=AuditEventType.CALIBRATION_BATCH_CREATED,
        target_id=batch.id,
        details={
            "result_ids": list(scorecard_result_ids),
            "participant_user_ids": list(participant_user_ids),
            "example_count": len(scorecard_result_ids),
            "participant_count": len(participant_user_ids),
        },
    )


# --- list ---------------------------------------------------------------------------------


def _encode_cursor(batch: CalibrationBatch) -> str:
    created_at = as_utc(batch.created_at)
    assert created_at is not None
    payload = {"v": 1, "created_at": created_at.isoformat(), "id": batch.id}
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return encoded.rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    # Its own keys: a review item cursor ("due_at") is refused here.
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
        batch_id = str(uuid.UUID(payload["id"]))
        created_at = datetime.fromisoformat(payload["created_at"])
        if created_at.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
        raise _invalid_cursor() from None
    return created_at.astimezone(UTC), batch_id


def list_batches(
    db: Session, caller: Reviewer, *, limit: int, cursor: str | None
) -> dict[str, Any]:
    """The visible batches, ``created_at`` then ``id`` descending, one keyset page.

    An admin sees every batch of the workspace; anyone else sees only the batches
    they are a participant of.
    """
    query = select(CalibrationBatch).where(CalibrationBatch.workspace_id == caller.workspace_id)
    if not caller.is_admin:
        query = query.join(
            CalibrationParticipant, CalibrationParticipant.batch_id == CalibrationBatch.id
        ).where(CalibrationParticipant.user_id == caller.user_id)
    if cursor is not None:
        created_at, batch_id = _decode_cursor(cursor)
        query = query.where(
            or_(
                CalibrationBatch.created_at < created_at,
                and_(CalibrationBatch.created_at == created_at, CalibrationBatch.id < batch_id),
            )
        )
    rows = list(
        db.scalars(
            query.order_by(CalibrationBatch.created_at.desc(), CalibrationBatch.id.desc()).limit(
                limit + 1
            )
        )
    )
    batches = rows[:limit]
    ids = [batch.id for batch in batches]
    example_counts = dict(
        db.execute(
            select(CalibrationExample.batch_id, func.count())
            .where(CalibrationExample.batch_id.in_(ids))
            .group_by(CalibrationExample.batch_id)
        ).all()
    )
    participant_counts = dict(
        db.execute(
            select(CalibrationParticipant.batch_id, func.count())
            .where(CalibrationParticipant.batch_id.in_(ids))
            .group_by(CalibrationParticipant.batch_id)
        ).all()
    )
    items = [
        {
            "id": batch.id,
            "title": batch.title,
            "status": batch.status,
            "revision": batch.revision,
            "created_at": as_utc(batch.created_at),
            "closed_at": as_utc(batch.closed_at),
            "cancelled_at": as_utc(batch.cancelled_at),
            "example_count": example_counts.get(batch.id, 0),
            "participant_count": participant_counts.get(batch.id, 0),
        }
        for batch in batches
    ]
    next_cursor = _encode_cursor(batches[-1]) if len(rows) > limit else None
    return {"items": items, "next_cursor": next_cursor}


# --- the batch view -------------------------------------------------------------------------


def batch_view(db: Session, caller: Reviewer, *, batch_id: str) -> dict[str, Any]:
    """The batch as the caller may see it (the fixed response shape, every key).

    A participant (admin or not) sees only their own answers, and the adjudications
    only after close.  An admin who is not a participant sees the progress counts and
    the adjudications, and every answer only after close.  No key holds a machine
    outcome, reason or evidence.
    """
    batch = _visible_batch(db, caller, batch_id)
    gathered = _gather(db, batch)
    participant = _participant_of(db, batch.id, caller.user_id)
    closed = batch.status == "closed"
    overseer = caller.is_admin and participant is None  # an admin who is not a participant
    expected = gathered.reviewable_count()
    answered = Counter(row.participant_id for row in gathered.answers)
    user_of = {row.id: row.user_id for row in gathered.participants}
    return {
        "id": batch.id,
        "title": batch.title,
        "status": batch.status,
        "revision": batch.revision,
        "created_by_user_id": batch.created_by_user_id,
        "created_at": as_utc(batch.created_at),
        "closed_at": as_utc(batch.closed_at),
        "cancelled_at": as_utc(batch.cancelled_at),
        "participants": [{"id": row.id, "user_id": row.user_id} for row in gathered.participants],
        "examples": [
            {
                "id": rows.example.id,
                "position": rows.example.position,
                "meeting_id": rows.example.meeting_id,
                "scorecard_result_id": rows.example.scorecard_result_id,
                "criteria": [
                    {
                        "criterion_id": row.criterion_id,
                        "position": row.position,
                        "weight": row.weight,
                        "na_allowed": row.na_allowed,
                        "reviewable": _is_reviewable(row),
                    }
                    for row in rows.criteria
                ],
            }
            for rows in gathered.examples
        ],
        "my_answers": [
            {"example_id": row.example_id, "criterion_id": row.criterion_id, "outcome": row.outcome}
            for row in gathered.answers
            if participant is not None and row.participant_id == participant.id
        ],
        "adjudications": [
            {
                "example_id": row.example_id,
                "criterion_id": row.criterion_id,
                "outcome": row.outcome,
                "note": row.note,
            }
            for row in gathered.adjudications
            if overseer or closed
        ],
        "progress": [
            {
                "participant_id": row.id,
                "user_id": row.user_id,
                "answered": answered[row.id],
                "expected": expected,
            }
            for row in gathered.participants
            if overseer
        ],
        "answers": [
            {
                "participant_id": row.participant_id,
                "user_id": user_of.get(row.participant_id),
                "example_id": row.example_id,
                "criterion_id": row.criterion_id,
                "outcome": row.outcome,
            }
            for row in gathered.answers
            if overseer and closed
        ],
    }


# --- answers and adjudications ----------------------------------------------------------------


def _example_of(db: Session, batch: CalibrationBatch, example_id: str) -> CalibrationExample:
    example = db.scalar(
        select(CalibrationExample).where(
            CalibrationExample.id == example_id, CalibrationExample.batch_id == batch.id
        )
    )
    if example is None:
        raise _example_not_found()
    return example


def _meeting_of(db: Session, example: CalibrationExample) -> Meeting:
    meeting = db.get(Meeting, example.meeting_id)
    if meeting is None:  # the composite foreign key holds; this guards a broken row
        raise _example_not_found()
    return meeting


def _criterion_of(
    db: Session, example: CalibrationExample, criterion_id: str, outcome: str
) -> CriterionResultRecord:
    """The criterion in the example's result (404), reviewable (422), the na rule (422)."""
    criterion = db.scalar(
        select(CriterionResultRecord).where(
            CriterionResultRecord.result_id == example.scorecard_result_id,
            CriterionResultRecord.workspace_id == example.workspace_id,
            CriterionResultRecord.criterion_id == criterion_id,
        )
    )
    if criterion is None:
        raise _criterion_not_found()
    if not _is_reviewable(criterion):
        raise _criterion_not_reviewable()
    if outcome == "not_applicable" and not criterion.na_allowed:
        raise _na_not_allowed()
    return criterion


def _current_answer(
    db: Session, participant_id: str, example_id: str, criterion_id: str
) -> CalibrationAnswer | None:
    return db.scalar(
        select(CalibrationAnswer).where(
            CalibrationAnswer.participant_id == participant_id,
            CalibrationAnswer.example_id == example_id,
            CalibrationAnswer.criterion_id == criterion_id,
        )
    )


def answer_criterion(
    db: Session,
    caller: Reviewer,
    *,
    batch_id: str,
    example_id: str,
    criterion_id: str,
    outcome: str,
    now: datetime,
) -> Change:
    """Write one answer (an upsert while the batch is open); nothing when identical."""
    batch = _visible_batch(db, caller, batch_id)
    participant = _participant_of(db, batch.id, caller.user_id)
    if participant is None:
        raise _not_participant()
    batch = _locked_batch(db, batch, share=True)
    if batch.status != "open":
        raise _closed()
    example = _example_of(db, batch, example_id)
    _check_eligible(
        db,
        workspace_id=caller.workspace_id,
        meeting=_meeting_of(db, example),
        user_id=caller.user_id,
        now=now,
        ineligible_status=403,
    )
    _criterion_of(db, example, criterion_id, outcome)

    existing = _current_answer(db, participant.id, example.id, criterion_id)
    if existing is None:
        stored = _insert(
            db,
            CalibrationAnswer(
                id=new_id(),
                workspace_id=batch.workspace_id,
                meeting_id=example.meeting_id,
                batch_id=batch.id,
                example_id=example.id,
                participant_id=participant.id,
                scorecard_result_id=example.scorecard_result_id,
                criterion_id=criterion_id,
                outcome=outcome,
                created_at=now,
                updated_at=now,
            ),
        )
        if not stored:
            existing = _current_answer(db, participant.id, example.id, criterion_id)
            if existing is None:  # not a racing answer: the example itself is gone
                raise _example_not_found()
    if existing is not None:
        if existing.outcome == outcome:
            # The same outcome again: no write, no audit event.
            return Change(view=batch_view(db, caller, batch_id=batch.id))
        existing.outcome = outcome
        existing.updated_at = now
        db.flush()
    return Change(
        view=batch_view(db, caller, batch_id=batch.id),
        event=AuditEventType.CALIBRATION_ANSWER_RECORDED,
        target_id=batch.id,
        details={
            "batch_id": batch.id,
            "example_id": example.id,
            "criterion_id": criterion_id,
            "participant_id": participant.id,
        },
    )


def _current_adjudication(
    db: Session, example_id: str, criterion_id: str
) -> CalibrationAdjudication | None:
    return db.scalar(
        select(CalibrationAdjudication).where(
            CalibrationAdjudication.example_id == example_id,
            CalibrationAdjudication.criterion_id == criterion_id,
        )
    )


def adjudicate_criterion(
    db: Session,
    caller: Reviewer,
    *,
    batch_id: str,
    example_id: str,
    criterion_id: str,
    outcome: str,
    note: str | None,
    now: datetime,
) -> Change:
    """Write one adjudication (an upsert while the batch is open); nothing when identical.

    The note was checked by the request schema (at most 2000 characters).
    """
    batch = _visible_batch(db, caller, batch_id)
    if not caller.is_admin:
        raise _admin_required()
    if _participant_of(db, batch.id, caller.user_id) is not None:
        raise _adjudicator_is_participant()
    batch = _locked_batch(db, batch, share=True)
    if batch.status != "open":
        raise _closed()
    example = _example_of(db, batch, example_id)
    meeting = _meeting_of(db, example)
    if meeting.agent_user_id == caller.user_id:
        raise _adjudicator_is_agent(caller.user_id, meeting.id)
    _criterion_of(db, example, criterion_id, outcome)

    existing = _current_adjudication(db, example.id, criterion_id)
    if existing is None:
        stored = _insert(
            db,
            CalibrationAdjudication(
                id=new_id(),
                workspace_id=batch.workspace_id,
                meeting_id=example.meeting_id,
                batch_id=batch.id,
                example_id=example.id,
                scorecard_result_id=example.scorecard_result_id,
                criterion_id=criterion_id,
                outcome=outcome,
                note=note,
                decided_by_user_id=caller.user_id,
                created_at=now,
                updated_at=now,
            ),
        )
        if not stored:
            existing = _current_adjudication(db, example.id, criterion_id)
            if existing is None:  # not a racing adjudication: the example itself is gone
                raise _example_not_found()
    if existing is not None:
        if existing.outcome == outcome and existing.note == note:
            # The same adjudication again: no write, no audit event.
            return Change(view=batch_view(db, caller, batch_id=batch.id))
        existing.outcome = outcome
        existing.note = note
        existing.decided_by_user_id = caller.user_id
        existing.updated_at = now
        db.flush()
    return Change(
        view=batch_view(db, caller, batch_id=batch.id),
        event=AuditEventType.CALIBRATION_ADJUDICATION_RECORDED,
        target_id=batch.id,
        details={"batch_id": batch.id, "example_id": example.id, "criterion_id": criterion_id},
    )


# --- close and cancel -------------------------------------------------------------------------


def _close_or_cancel(
    db: Session,
    caller: Reviewer,
    *,
    batch_id: str,
    expected_revision: int,
    now: datetime,
    closing: bool,
) -> Change:
    batch = _visible_batch(db, caller, batch_id)
    if not caller.is_admin:
        raise _admin_required()
    # Wait for every answer and adjudication that holds the share lock, then read.
    batch = _locked_batch(db, batch, share=False)
    if batch.revision != expected_revision:
        raise _revision_conflict(batch.revision)
    if batch.status != "open":
        raise _closed()
    gathered = _gather(db, batch)
    if closing:
        missing = gathered.missing_adjudications()
        if missing:
            raise _adjudication_incomplete(missing)
    values: dict[str, Any] = (
        {"status": "closed", "closed_at": now, "closed_by_user_id": caller.user_id}
        if closing
        else {"status": "cancelled", "cancelled_at": now, "cancelled_by_user_id": caller.user_id}
    )
    changed = db.execute(
        update(CalibrationBatch)
        .where(
            CalibrationBatch.id == batch.id,
            CalibrationBatch.workspace_id == batch.workspace_id,
            CalibrationBatch.revision == expected_revision,
            CalibrationBatch.status == "open",
        )
        .values(**values, updated_at=now, revision=CalibrationBatch.revision + 1)
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:  # the row lock makes this unreachable; the compare-and-set stays
        db.refresh(batch)
        if batch.revision != expected_revision:
            raise _revision_conflict(batch.revision)
        raise _closed()
    db.refresh(batch)
    details: dict[str, Any] = {
        "revision": batch.revision,
        "example_count": len(gathered.examples),
        "participant_count": len(gathered.participants),
    }
    if closing:
        overall = gathered.numbers()["overall"]
        details.update(
            compared=overall["compared"],
            matched=overall["matched"],
            agreement_bp=overall["agreement_bp"],
        )
    return Change(
        view=batch_view(db, caller, batch_id=batch.id),
        event=(
            AuditEventType.CALIBRATION_BATCH_CLOSED
            if closing
            else AuditEventType.CALIBRATION_BATCH_CANCELLED
        ),
        target_id=batch.id,
        details=details,
    )


def close_batch(
    db: Session, caller: Reviewer, *, batch_id: str, expected_revision: int, now: datetime
) -> Change:
    """Close the batch (revision + 1) once every reviewable criterion is adjudicated."""
    return _close_or_cancel(
        db, caller, batch_id=batch_id, expected_revision=expected_revision, now=now, closing=True
    )


def cancel_batch(
    db: Session, caller: Reviewer, *, batch_id: str, expected_revision: int, now: datetime
) -> Change:
    """Cancel the batch (revision + 1); a cancelled batch never reveals answers."""
    return _close_or_cancel(
        db, caller, batch_id=batch_id, expected_revision=expected_revision, now=now, closing=False
    )


# --- the report -------------------------------------------------------------------------------


def batch_report(db: Session, caller: Reviewer, *, batch_id: str) -> dict[str, Any]:
    """The agreement report of a closed batch, computed on read from the frozen rows.

    Admins get every block.  A participant who is not an admin gets the overall
    block, the per-criterion block and only their own row, and no machine row.
    """
    batch = _visible_batch(db, caller, batch_id)
    if batch.status != "closed":
        raise _not_closed()
    gathered = _gather(db, batch)
    numbers = gathered.numbers()
    own = None if caller.is_admin else _participant_of(db, batch.id, caller.user_id)
    shown = [row for row in gathered.participants if own is None or row.id == own.id]
    return {
        "batch_id": batch.id,
        "overall": numbers["overall"],
        "participants": [
            {"participant_id": row.id, "user_id": row.user_id, **numbers["participants"][row.id]}
            for row in shown
        ],
        "criteria": numbers["criteria"],
        "machine": numbers["machine"] if own is None else None,
    }
