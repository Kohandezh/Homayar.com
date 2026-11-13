"""Agent appeals of review results and supervisor dispositions (HOM-76, ADR 0057).

The agent of a call (the meeting's current ``agent_user_id``) may appeal a completed
review item that has a human result, once, within 14 days of ``completed_at``.  A decider
upholds or changes each appealed criterion; when one changes, the machine result is
scored again with the human outcomes plus the changes (``score_human_result``, the one
scoring copy) and stored in ``review_appeal_results``.  The machine result, the review
decisions and the human result are never written.

Who sees what, for caller ``U`` in workspace ``W``:

* **Agent view** (``review:appeal``): the completed, human-scored items of the calls whose
  current agent is ``U`` (not deleted); and the appeals ``U`` filed.  It never holds a
  reviewer's user id.
* **Supervisor view** (``review:read``): the HOM-73 record rule of the appeal's call
  (``Reviewer.meeting_clause``: an owner or admin sees every call; a member sees the calls
  of the teams they supervise, never their own), minus the appeals where ``U`` is the
  appellant or an original reviewer.  The **original reviewers** of an item are its
  completing assignee (``original_reviewer_user_id``) and every ``decided_by_user_id`` of
  the item's criterion decisions.
* Anything else is ``404 review_appeal_not_found`` (``404 review_item_not_found`` on the
  agent routes), like a missing id or an id of another workspace.

Every write reads the meeting again, checks in the ADR 0057 order, and updates the appeal
with a compare-and-set on ``revision`` (``AND status = 'open'``), so two concurrent writes
never both land.  A refusal raises before anything is written.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, false, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    REVIEW_APPEAL_MAX_CRITERIA,
    REVIEW_APPEAL_STATUSES,
    REVIEW_APPEAL_WINDOW_DAYS,
    REVIEW_NOTE_MAX_LENGTH,
    REVIEW_OVERRIDE_REASONS,
    REVIEW_REVIEWER_OUTCOMES,
    AuditEvent,
    CriterionResultRecord,
    Meeting,
    Membership,
    ReviewAppeal,
    ReviewAppealCriterion,
    ReviewAppealResult,
    ReviewCriterionDecision,
    ReviewItem,
    ReviewResultRecord,
    ScorecardResultRecord,
    new_id,
)
from backend.jobs.leases import as_utc
from backend.scorecards import SCORECARDS_VERSION
from backend.security.authorization import membership_effective_clause
from backend.services import notification_service
from backend.services.audit_event_types import AuditEventType
from backend.services.review_decision_service import (
    NOT_APPLICABLE_BY_CONVERSATION,
    _score_fields,
)
from backend.services.review_queue_service import (
    REVIEW_ADMIN_ROLES,
    Reviewer,
    ReviewQueueError,
    check_assignee,
    score_human_result,
)

__all__ = ["ReviewQueueError"]

APPEAL_WINDOW = timedelta(days=REVIEW_APPEAL_WINDOW_DAYS)
SUPERVISOR = "supervisor"
APPELLANT = "appellant"


# --- errors -------------------------------------------------------------------------------


def _item_not_found() -> ReviewQueueError:
    return ReviewQueueError(404, "review_item_not_found", "Review item not found")


def _appeal_not_found() -> ReviewQueueError:
    return ReviewQueueError(404, "review_appeal_not_found", "Review appeal not found")


def _revision_conflict(current_revision: int) -> ReviewQueueError:
    return ReviewQueueError(
        409,
        "review_appeal_revision_conflict",
        "The appeal changed since it was read; reload and retry",
        {"current_revision": current_revision},
    )


def _closed() -> ReviewQueueError:
    return ReviewQueueError(409, "review_appeal_closed", "The appeal is closed")


def _invalid_cursor() -> ReviewQueueError:
    return ReviewQueueError(400, "invalid_cursor", "Invalid pagination cursor")


def _incomplete(message: str) -> ReviewQueueError:
    return ReviewQueueError(422, "appeal_decisions_incomplete", message)


def _invalid_decision(message: str) -> ReviewQueueError:
    """A decision value with no code of its own (SPEC-pr2 R3)."""
    return ReviewQueueError(422, "appeal_decision_invalid", message)


# --- the caller ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppealCaller:
    """The caller's review authority (HOM-73) and whether their role reads the queue."""

    reviewer: Reviewer
    supervises: bool  # the role holds ``review:read``

    @property
    def workspace_id(self) -> str:
        return self.reviewer.workspace_id

    @property
    def user_id(self) -> str:
        return self.reviewer.user_id


def _stored_text(value: Any) -> str | None:
    """A note as stored: stripped, at most 2000 characters, storable text; else None."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or len(stripped) > REVIEW_NOTE_MAX_LENGTH or "\x00" in stripped:
        return None
    try:
        stripped.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return stripped


# --- keyset cursors -----------------------------------------------------------------------


def _encode_cursor(key: str, when: datetime | None, row_id: str) -> str:
    moment = as_utc(when)
    assert moment is not None
    payload = {"v": 1, key: moment.isoformat(), "id": row_id}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str, key: str) -> tuple[datetime, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(
            base64.b64decode(cursor + padding, altchars=b"-_", validate=True).decode("utf-8")
        )
        if not isinstance(payload, dict) or set(payload) != {"v", key, "id"}:
            raise ValueError
        if payload["v"] != 1 or not isinstance(payload[key], str):
            raise ValueError
        row_id = str(uuid.UUID(str(payload["id"])))
        moment = datetime.fromisoformat(payload[key])
        if moment.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
        raise _invalid_cursor() from None
    return moment.astimezone(UTC), row_id


# --- scores -------------------------------------------------------------------------------


def _machine(db: Session, workspace_id: str, result_id: str) -> ScorecardResultRecord:
    result = db.scalar(
        select(ScorecardResultRecord).where(
            ScorecardResultRecord.id == result_id,
            ScorecardResultRecord.workspace_id == workspace_id,
        )
    )
    if result is None:  # the composite foreign keys hold; this guards a broken row
        raise _item_not_found()
    return result


def _human(db: Session, item_id: str) -> ReviewResultRecord | None:
    return db.scalar(select(ReviewResultRecord).where(ReviewResultRecord.review_item_id == item_id))


def _appeal_of_item(db: Session, item_id: str) -> ReviewAppeal | None:
    return db.scalar(select(ReviewAppeal).where(ReviewAppeal.review_item_id == item_id))


def _appeal_result(db: Session, appeal_id: str) -> ReviewAppealResult | None:
    return db.scalar(select(ReviewAppealResult).where(ReviewAppealResult.appeal_id == appeal_id))


def score(row: Any) -> dict[str, Any]:
    """The ``ReviewScoreView`` of a stored machine, human or appeal result row."""
    return _score_fields(row, row.result)


def human_outcomes(human: ReviewResultRecord) -> dict[str, str]:
    """Each criterion's outcome in the stored human result (the engine output)."""
    outcomes: dict[str, str] = {}
    criteria = human.result.get("criteria") if isinstance(human.result, dict) else None
    for entry in criteria or []:
        if isinstance(entry, dict) and isinstance(entry.get("criterion_id"), str):
            outcomes[entry["criterion_id"]] = entry.get("outcome")
    return outcomes


def _criterion_rows(db: Session, result: ScorecardResultRecord) -> list[CriterionResultRecord]:
    return list(
        db.scalars(
            select(CriterionResultRecord)
            .where(
                CriterionResultRecord.result_id == result.id,
                CriterionResultRecord.workspace_id == result.workspace_id,
            )
            .order_by(CriterionResultRecord.position)
        )
    )


# --- the agent side -----------------------------------------------------------------------


def _my_items(caller: AppealCaller, *columns: Any) -> Select[Any]:
    """The completed, human-scored items of the caller's own (not deleted) calls."""
    return (
        select(*columns)
        .join(
            Meeting,
            and_(Meeting.id == ReviewItem.meeting_id, Meeting.workspace_id == caller.workspace_id),
        )
        .join(ReviewResultRecord, ReviewResultRecord.review_item_id == ReviewItem.id)
        .where(
            ReviewItem.workspace_id == caller.workspace_id,
            ReviewItem.status == "completed",
            Meeting.deleted_at.is_(None),
            Meeting.agent_user_id == caller.user_id,
        )
    )


def _summary(db: Session, item: ReviewItem, human: ReviewResultRecord) -> dict[str, Any]:
    assert item.scorecard_result_id is not None  # a human result needs one
    machine = _machine(db, item.workspace_id, item.scorecard_result_id)
    appeal = _appeal_of_item(db, item.id)
    appeal_result = _appeal_result(db, appeal.id) if appeal is not None else None
    completed = as_utc(item.completed_at)
    assert completed is not None
    human_view = score(human)
    return {
        "review_item_id": item.id,
        "meeting_id": item.meeting_id,
        "completed_at": completed,
        "appeal_deadline": completed + APPEAL_WINDOW,
        "machine": score(machine),
        "human": human_view,
        "final": score(appeal_result) if appeal_result is not None else human_view,
        "final_source": "appeal" if appeal_result is not None else "human",
        "appeal": (
            {"id": appeal.id, "status": appeal.status, "disposition": appeal.disposition}
            if appeal is not None
            else None
        ),
    }


@dataclass(frozen=True)
class Page:
    items: list[dict[str, Any]]
    next_cursor: str | None


def list_my_reviews(db: Session, caller: AppealCaller, *, limit: int, cursor: str | None) -> Page:
    """Newest ``completed_at`` first, then ``id``, one keyset page."""
    query = _my_items(caller, ReviewItem, ReviewResultRecord)
    if cursor is not None:
        moment, item_id = _decode_cursor(cursor, "completed_at")
        query = query.where(
            or_(
                ReviewItem.completed_at < moment,
                and_(ReviewItem.completed_at == moment, ReviewItem.id < item_id),
            )
        )
    rows = db.execute(
        query.order_by(ReviewItem.completed_at.desc(), ReviewItem.id.desc()).limit(limit + 1)
    ).all()
    page = rows[:limit]
    last = page[-1][0] if page else None
    return Page(
        items=[_summary(db, item, human) for item, human in page],
        next_cursor=(
            _encode_cursor("completed_at", last.completed_at, last.id)
            if len(rows) > limit and last is not None
            else None
        ),
    )


def get_my_review(db: Session, caller: AppealCaller, *, item_id: str) -> dict[str, Any]:
    row = db.execute(
        _my_items(caller, ReviewItem, ReviewResultRecord).where(ReviewItem.id == item_id)
    ).first()
    if row is None:
        raise _item_not_found()
    item, human = row
    data = _summary(db, item, human)
    machine = _machine(db, item.workspace_id, item.scorecard_result_id or "")
    decisions = {
        decision.criterion_id: decision
        for decision in db.scalars(
            select(ReviewCriterionDecision).where(ReviewCriterionDecision.review_item_id == item.id)
        )
    }
    appeal = _appeal_of_item(db, item.id)
    appealed: dict[str, ReviewAppealCriterion] = {}
    if appeal is not None and appeal.status == "decided":
        appealed = {
            row.criterion_id: row
            for row in db.scalars(
                select(ReviewAppealCriterion).where(ReviewAppealCriterion.appeal_id == appeal.id)
            )
        }
    outcomes = human_outcomes(human)
    criteria = []
    for row in _criterion_rows(db, machine):
        decision = decisions.get(row.criterion_id)
        appeal_row = appealed.get(row.criterion_id)
        criteria.append(
            {
                "criterion_id": row.criterion_id,
                "position": row.position,
                "weight": row.weight,
                "auto_fail": row.auto_fail,
                "na_allowed": row.na_allowed,
                "reviewable": row.reason != NOT_APPLICABLE_BY_CONVERSATION,
                "machine_outcome": row.outcome,
                "human_outcome": outcomes.get(row.criterion_id, row.outcome),
                # Never ``decided_by_user_id``: no reviewer identity reaches the agent.
                "decision": (
                    {
                        "decision": decision.decision,
                        "reviewer_outcome": decision.reviewer_outcome,
                        "reason_code": decision.reason_code,
                        "note": decision.note,
                        "evidence": decision.evidence,
                    }
                    if decision is not None
                    else None
                ),
                "appeal": (
                    {
                        "decision": appeal_row.decision,
                        "decided_outcome": appeal_row.decided_outcome,
                        "reason_code": appeal_row.reason_code,
                        "note": appeal_row.note,
                    }
                    if appeal_row is not None and appeal_row.decision is not None
                    else None
                ),
            }
        )
    return {**data, "criteria": criteria}


@dataclass(frozen=True)
class Outcome:
    """A write's result: the appeal and its view; ``event`` is ``None`` for a no-op."""

    appeal: ReviewAppeal
    meeting: Meeting
    view: str
    event: AuditEventType | None
    details: dict[str, Any] = field(default_factory=dict)


def _insert_appeal(db: Session, appeal: ReviewAppeal) -> bool:
    """Add the appeal in a savepoint; ``False`` when ``uq_review_appeals_item`` refuses."""
    savepoint = db.begin_nested()
    try:
        db.add(appeal)
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        return False
    return True


def _appeal_exists() -> ReviewQueueError:
    return ReviewQueueError(409, "appeal_exists", "The review item already has an appeal")


def file_appeal(
    db: Session, caller: AppealCaller, *, item_id: str, body: dict[str, Any], now: datetime
) -> Outcome:
    """The checks of ADR 0057 D3 item 3, in order; a refusal writes nothing."""
    row = db.execute(
        select(ReviewItem, Meeting)
        .join(
            Meeting,
            and_(Meeting.id == ReviewItem.meeting_id, Meeting.workspace_id == caller.workspace_id),
        )
        .where(
            ReviewItem.workspace_id == caller.workspace_id,
            ReviewItem.id == item_id,
            Meeting.deleted_at.is_(None),
            Meeting.agent_user_id == caller.user_id,
        )
    ).first()
    if row is None:
        raise _item_not_found()
    item, meeting = row
    human = _human(db, item.id) if item.status == "completed" else None
    if human is None or item.scorecard_result_id is None:
        raise ReviewQueueError(
            409, "review_item_not_appealable", "Only a completed review with a human result"
        )
    completed = as_utc(item.completed_at)
    assert completed is not None
    if not now < completed + APPEAL_WINDOW:
        raise ReviewQueueError(
            409,
            "appeal_window_closed",
            f"Appeals close {REVIEW_APPEAL_WINDOW_DAYS} days after review",
        )
    if _appeal_of_item(db, item.id) is not None:
        raise _appeal_exists()

    raw = body.get("criterion_ids")
    if not isinstance(raw, list) or not all(isinstance(value, str) for value in raw):
        raise ReviewQueueError(
            422, "appeal_criteria_invalid", "criterion_ids must be a list of criterion ids"
        )
    machine = _machine(db, caller.workspace_id, item.scorecard_result_id)
    known = {row.criterion_id: row for row in _criterion_rows(db, machine)}
    for criterion_id in raw:
        known_row = known.get(criterion_id)
        if known_row is None or known_row.reason == NOT_APPLICABLE_BY_CONVERSATION:
            raise ReviewQueueError(
                422,
                "appeal_criterion_invalid",
                "Not an appealable criterion of the reviewed result",
                {"criterion_id": criterion_id},
            )
    if not 1 <= len(raw) <= REVIEW_APPEAL_MAX_CRITERIA or len(set(raw)) != len(raw):
        raise ReviewQueueError(
            422,
            "appeal_criteria_invalid",
            f"Name 1 to {REVIEW_APPEAL_MAX_CRITERIA} distinct criteria",
        )
    reason_note = _stored_text(body.get("reason_note"))
    if reason_note is None:
        raise ReviewQueueError(
            422,
            "appeal_reason_required",
            f"reason_note must be 1 to {REVIEW_NOTE_MAX_LENGTH} characters of text",
        )

    appeal = ReviewAppeal(
        id=new_id(),
        workspace_id=item.workspace_id,
        meeting_id=item.meeting_id,
        review_item_id=item.id,
        scorecard_result_id=item.scorecard_result_id,
        appellant_user_id=caller.user_id,
        original_reviewer_user_id=item.assignee_user_id,
        status="open",
        reason_note=reason_note,
        created_at=now,
        updated_at=now,
        revision=1,
    )
    if not _insert_appeal(db, appeal):
        raise _appeal_exists()
    outcomes = human_outcomes(human)
    ordered = sorted(raw, key=lambda criterion_id: known[criterion_id].position)
    for criterion_id in ordered:
        db.add(
            ReviewAppealCriterion(
                id=new_id(),
                workspace_id=appeal.workspace_id,
                meeting_id=appeal.meeting_id,
                appeal_id=appeal.id,
                review_item_id=appeal.review_item_id,
                scorecard_result_id=appeal.scorecard_result_id,
                criterion_id=criterion_id,
                appealed_outcome=outcomes.get(criterion_id, known[criterion_id].outcome),
                created_at=now,
                updated_at=now,
            )
        )
    db.flush()
    notification_service.notify_appeal(
        db,
        appeal=appeal,
        kind=notification_service.FILED,
        user_ids=filed_recipients(db, appeal, meeting, now=now),
        now=now,
    )
    return Outcome(
        appeal,
        meeting,
        APPELLANT,
        AuditEventType.REVIEW_APPEAL_FILED,
        {
            "review_item_id": item.id,
            "scorecard_result_id": appeal.scorecard_result_id,
            "criterion_ids": ordered,
            "appellant_user_id": caller.user_id,
            "revision": appeal.revision,
        },
    )


def filed_recipients(
    db: Session, appeal: ReviewAppeal, meeting: Meeting, *, now: datetime
) -> list[str]:
    """Every user who could be assigned the appeal (HOM-73 ``check_assignee``: an owner, an
    admin or a member supervising the call's current team, with an effective membership),
    minus the appellant, the current agent and every original reviewer.  The team's
    supervisors come first, then the owners and admins, each group by user id; at most
    ``MAX_FILED_RECIPIENTS`` in total (SPEC-pr2 R11)."""
    excluded = {appeal.appellant_user_id, meeting.agent_user_id} | original_reviewers(db, appeal)
    memberships = db.execute(
        select(Membership.user_id, Membership.role).where(
            Membership.workspace_id == appeal.workspace_id,
            membership_effective_clause(now),
        )
    ).all()
    supervisors: list[str] = []
    admins: list[str] = []
    for user_id, role in sorted(memberships, key=lambda row: row[0]):
        if user_id in excluded:
            continue
        try:
            check_assignee(
                db,
                workspace_id=appeal.workspace_id,
                meeting=meeting,
                assignee_user_id=user_id,
                now=now,
            )
        except ReviewQueueError:
            continue
        (admins if role in REVIEW_ADMIN_ROLES else supervisors).append(user_id)
    return (supervisors + admins)[: notification_service.MAX_FILED_RECIPIENTS]


def list_my_appeals(
    db: Session, caller: AppealCaller, *, limit: int, cursor: str | None
) -> tuple[list[tuple[ReviewAppeal, Meeting]], str | None]:
    """The caller's own appeals (``appellant_user_id``), newest first, one keyset page."""
    query = _appeals(caller).where(ReviewAppeal.appellant_user_id == caller.user_id)
    if cursor is not None:
        moment, appeal_id = _decode_cursor(cursor, "created_at")
        query = query.where(
            or_(
                ReviewAppeal.created_at < moment,
                and_(ReviewAppeal.created_at == moment, ReviewAppeal.id < appeal_id),
            )
        )
    rows = db.execute(
        query.order_by(ReviewAppeal.created_at.desc(), ReviewAppeal.id.desc()).limit(limit + 1)
    ).all()
    page = [(appeal, meeting) for appeal, meeting in rows[:limit]]
    next_cursor = (
        _encode_cursor("created_at", page[-1][0].created_at, page[-1][0].id)
        if len(rows) > limit
        else None
    )
    return page, next_cursor


# --- independence and visibility ----------------------------------------------------------


def original_reviewers(db: Session, appeal: ReviewAppeal) -> set[str]:
    """The completing assignee and every decider of a criterion decision of the item."""
    found = set(
        db.scalars(
            select(ReviewCriterionDecision.decided_by_user_id).where(
                ReviewCriterionDecision.review_item_id == appeal.review_item_id,
                ReviewCriterionDecision.decided_by_user_id.is_not(None),
            )
        )
    )
    if appeal.original_reviewer_user_id is not None:
        found.add(appeal.original_reviewer_user_id)
    return found


def _check_independent(
    db: Session, appeal: ReviewAppeal, meeting: Meeting, *, user_id: str, role: str
) -> None:
    if user_id in (meeting.agent_user_id, appeal.appellant_user_id):
        raise ReviewQueueError(
            422, f"appeal_{role}_is_party", "Nobody acts on an appeal of their own call"
        )
    if user_id in original_reviewers(db, appeal):
        raise ReviewQueueError(
            422,
            f"appeal_{role}_is_original_reviewer",
            "An original reviewer of the item never decides its appeal",
        )


def check_decider(db: Session, appeal: ReviewAppeal, meeting: Meeting, *, user_id: str) -> None:
    """``422 appeal_decider_is_party`` (the call's current agent or the appellant), then
    ``422 appeal_decider_is_original_reviewer``."""
    _check_independent(db, appeal, meeting, user_id=user_id, role="decider")


def _appeals(caller: AppealCaller) -> Select[Any]:
    return (
        select(ReviewAppeal, Meeting)
        .join(
            Meeting,
            and_(
                Meeting.id == ReviewAppeal.meeting_id, Meeting.workspace_id == caller.workspace_id
            ),
        )
        .where(ReviewAppeal.workspace_id == caller.workspace_id, Meeting.deleted_at.is_(None))
    )


def _supervisor_clause(caller: AppealCaller) -> Any:
    if not caller.supervises:
        return false()
    decided_here = (
        select(ReviewCriterionDecision.id)
        .where(
            ReviewCriterionDecision.review_item_id == ReviewAppeal.review_item_id,
            ReviewCriterionDecision.decided_by_user_id == caller.user_id,
        )
        .exists()
    )
    return and_(
        caller.reviewer.meeting_clause(),
        or_(
            ReviewAppeal.appellant_user_id.is_(None),
            ReviewAppeal.appellant_user_id != caller.user_id,
        ),
        or_(
            ReviewAppeal.original_reviewer_user_id.is_(None),
            ReviewAppeal.original_reviewer_user_id != caller.user_id,
        ),
        ~decided_here,
    )


def _visible(
    db: Session, caller: AppealCaller, appeal_id: str
) -> tuple[ReviewAppeal, Meeting, str]:
    """The appeal and the caller's view of it, or ``404 review_appeal_not_found``."""
    base = _appeals(caller).where(ReviewAppeal.id == appeal_id)
    row = db.execute(base.where(_supervisor_clause(caller))).first()
    if row is not None:
        return row[0], row[1], SUPERVISOR
    row = db.execute(base.where(ReviewAppeal.appellant_user_id == caller.user_id)).first()
    if row is not None:
        return row[0], row[1], APPELLANT
    raise _appeal_not_found()


def _supervised(db: Session, caller: AppealCaller, appeal_id: str) -> tuple[ReviewAppeal, Meeting]:
    appeal, meeting, view = _visible(db, caller, appeal_id)
    if view != SUPERVISOR:
        raise _appeal_not_found()
    return appeal, meeting


def get_appeal(
    db: Session, caller: AppealCaller, *, appeal_id: str
) -> tuple[ReviewAppeal, Meeting, str]:
    return _visible(db, caller, appeal_id)


def parse_statuses(statuses: list[str] | None) -> tuple[str, ...]:
    if not statuses:
        return REVIEW_APPEAL_STATUSES
    if any(status not in REVIEW_APPEAL_STATUSES for status in statuses):
        raise ReviewQueueError(
            422,
            "review_appeal_filter_invalid",
            f"status must be one of {', '.join(REVIEW_APPEAL_STATUSES)}",
        )
    return tuple(dict.fromkeys(statuses))


def list_appeals(
    db: Session,
    caller: AppealCaller,
    *,
    statuses: tuple[str, ...],
    assignee_user_id: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[tuple[ReviewAppeal, Meeting]], str | None]:
    """The supervisor-visible appeals, ``created_at`` then ``id`` ascending."""
    query = _appeals(caller).where(_supervisor_clause(caller), ReviewAppeal.status.in_(statuses))
    if assignee_user_id is not None:
        query = query.where(ReviewAppeal.assignee_user_id == assignee_user_id)
    if cursor is not None:
        moment, appeal_id = _decode_cursor(cursor, "created_at")
        query = query.where(
            or_(
                ReviewAppeal.created_at > moment,
                and_(ReviewAppeal.created_at == moment, ReviewAppeal.id > appeal_id),
            )
        )
    rows = db.execute(
        query.order_by(ReviewAppeal.created_at.asc(), ReviewAppeal.id.asc()).limit(limit + 1)
    ).all()
    page = [(appeal, meeting) for appeal, meeting in rows[:limit]]
    next_cursor = (
        _encode_cursor("created_at", page[-1][0].created_at, page[-1][0].id)
        if len(rows) > limit
        else None
    )
    return page, next_cursor


# --- the appeal view ----------------------------------------------------------------------


def appeal_view(db: Session, appeal: ReviewAppeal, meeting: Meeting, view: str) -> dict[str, Any]:
    """The supervisor view, or the appellant view without any reviewer user id."""
    machine = _machine(db, appeal.workspace_id, appeal.scorecard_result_id)
    positions = {row.criterion_id: row.position for row in _criterion_rows(db, machine)}
    criteria = sorted(
        db.scalars(
            select(ReviewAppealCriterion).where(ReviewAppealCriterion.appeal_id == appeal.id)
        ),
        key=lambda row: (positions.get(row.criterion_id, 0), row.criterion_id),
    )
    human = _human(db, appeal.review_item_id)
    assert human is not None  # ``fk_review_appeals_review_result`` holds
    appeal_result = _appeal_result(db, appeal.id)
    human_view = score(human)
    supervisor = view == SUPERVISOR
    return {
        "view": view,
        "id": appeal.id,
        "review_item_id": appeal.review_item_id,
        "meeting_id": appeal.meeting_id,
        "team_id": meeting.team_id if supervisor else None,
        "scorecard_result_id": appeal.scorecard_result_id,
        "status": appeal.status,
        "appellant_user_id": appeal.appellant_user_id,
        "original_reviewer_user_id": appeal.original_reviewer_user_id if supervisor else None,
        "assignee_user_id": appeal.assignee_user_id if supervisor else None,
        "assigned_by_user_id": appeal.assigned_by_user_id if supervisor else None,
        "assigned_at": as_utc(appeal.assigned_at),
        "reason_note": appeal.reason_note,
        "disposition": appeal.disposition,
        "disposition_note": appeal.disposition_note,
        "decided_by_user_id": appeal.decided_by_user_id if supervisor else None,
        "decided_at": as_utc(appeal.decided_at),
        "withdrawn_at": as_utc(appeal.withdrawn_at),
        "created_at": as_utc(appeal.created_at),
        "updated_at": as_utc(appeal.updated_at),
        "revision": appeal.revision,
        "criteria": [
            {
                "criterion_id": row.criterion_id,
                "appealed_outcome": row.appealed_outcome,
                "decision": row.decision,
                "decided_outcome": row.decided_outcome,
                "reason_code": row.reason_code,
                "note": row.note,
            }
            for row in criteria
        ],
        "human": human_view,
        "appeal_result": score(appeal_result) if appeal_result is not None else None,
        "final": score(appeal_result) if appeal_result is not None else human_view,
    }


# --- the writes ---------------------------------------------------------------------------


def _compare_and_set(
    db: Session,
    appeal: ReviewAppeal,
    *,
    expected_revision: int,
    now: datetime,
    values: dict[str, Any],
) -> None:
    """Write ``values`` only while the appeal is still ``open`` at ``expected_revision``;
    the revision grows by one.  A concurrent writer makes the update change no row."""
    changed = db.execute(
        update(ReviewAppeal)
        .where(
            ReviewAppeal.id == appeal.id,
            ReviewAppeal.workspace_id == appeal.workspace_id,
            ReviewAppeal.revision == expected_revision,
            ReviewAppeal.status == "open",
        )
        .values(**values, updated_at=now, revision=ReviewAppeal.revision + 1)
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        current = db.execute(
            select(ReviewAppeal.revision, ReviewAppeal.status).where(
                ReviewAppeal.id == appeal.id, ReviewAppeal.workspace_id == appeal.workspace_id
            )
        ).first()
        if current is None:
            raise _appeal_not_found()
        if current.revision != expected_revision:
            raise _revision_conflict(current.revision)
        raise _closed()
    db.refresh(appeal)


def _writable(appeal: ReviewAppeal, expected_revision: int) -> None:
    if appeal.revision != expected_revision:
        raise _revision_conflict(appeal.revision)
    if appeal.status != "open":
        raise _closed()


def withdraw_appeal(
    db: Session, caller: AppealCaller, *, appeal_id: str, expected_revision: int, now: datetime
) -> Outcome:
    """Visible (404), the appellant (403), the revision (409), open (409)."""
    appeal, meeting, view = _visible(db, caller, appeal_id)
    if appeal.appellant_user_id != caller.user_id:
        raise ReviewQueueError(403, "appeal_not_appellant", "Only the appellant withdraws")
    _writable(appeal, expected_revision)
    _compare_and_set(
        db,
        appeal,
        expected_revision=expected_revision,
        now=now,
        values={"status": "withdrawn", "withdrawn_at": now},
    )
    notification_service.notify_appeal(
        db,
        appeal=appeal,
        kind=notification_service.WITHDRAWN,
        user_ids=[appeal.assignee_user_id],
        now=now,
    )
    return Outcome(
        appeal, meeting, view, AuditEventType.REVIEW_APPEAL_WITHDRAWN, {"revision": appeal.revision}
    )


def assign_appeal(
    db: Session,
    caller: AppealCaller,
    *,
    appeal_id: str,
    expected_revision: int,
    assignee_user_id: Any,
    now: datetime,
) -> Outcome:
    """Visible (404), the caller not a party (422 ``appeal_assigner_is_party``), the
    revision (409), open (409), the assignee not a party (422), not an original reviewer
    (422), then HOM-73's ``check_assignee``.  The same assignee writes nothing."""
    appeal, meeting = _supervised(db, caller, appeal_id)
    if caller.user_id in (meeting.agent_user_id, appeal.appellant_user_id):
        # Only an owner or admin reaches this: a member agent does not see the appeal.
        raise ReviewQueueError(
            422, "appeal_assigner_is_party", "Nobody acts on an appeal of their own call"
        )
    _writable(appeal, expected_revision)
    if not isinstance(assignee_user_id, str) or not assignee_user_id:
        raise ReviewQueueError(
            422,
            "review_assignee_ineligible",
            "The assignee needs review authority over the conversation",
        )
    _check_independent(db, appeal, meeting, user_id=assignee_user_id, role="assignee")
    check_assignee(
        db,
        workspace_id=caller.workspace_id,
        meeting=meeting,
        assignee_user_id=assignee_user_id,
        now=now,
    )
    previous = appeal.assignee_user_id
    if assignee_user_id == previous:
        return Outcome(appeal, meeting, SUPERVISOR, None)
    _compare_and_set(
        db,
        appeal,
        expected_revision=expected_revision,
        now=now,
        values={
            "assignee_user_id": assignee_user_id,
            "assigned_by_user_id": caller.user_id,
            "assigned_at": now,
        },
    )
    notification_service.notify_appeal(
        db, appeal=appeal, kind=notification_service.ASSIGNED, user_ids=[assignee_user_id], now=now
    )
    return Outcome(
        appeal,
        meeting,
        SUPERVISOR,
        AuditEventType.REVIEW_APPEAL_ASSIGNED,
        {
            "assignee_user_id_before": previous,
            "assignee_user_id_after": assignee_user_id,
            "revision": appeal.revision,
        },
    )


@dataclass(frozen=True)
class _Decision:
    criterion: ReviewAppealCriterion
    decision: str
    decided_outcome: str | None
    reason_code: str | None
    note: str | None


DECISION_KEYS = frozenset({"criterion_id", "decision", "decided_outcome", "reason_code", "note"})


def _parse_decisions(
    raw: Any,
    appealed: list[ReviewAppealCriterion],
    na_allowed: dict[str, bool],
) -> list[_Decision]:
    """The body's decisions, checked in the ADR 0057 order, in position order."""
    if not isinstance(raw, list) or not all(isinstance(entry, dict) for entry in raw):
        raise _incomplete("decisions must be a list of objects")
    ids = [entry.get("criterion_id") for entry in raw]
    if not all(isinstance(criterion_id, str) for criterion_id in ids):
        raise _incomplete("Every decision names a criterion_id")
    by_id = {row.criterion_id: row for row in appealed}
    if len(set(ids)) != len(ids) or set(ids) != set(by_id):
        raise _incomplete("One decision for each appealed criterion, each once")
    entries = {entry["criterion_id"]: entry for entry in raw}
    parsed: list[_Decision] = []
    for row in appealed:
        entry = entries[row.criterion_id]
        if set(entry) - DECISION_KEYS:
            raise _invalid_decision("A decision has an unknown key")
        decision = entry.get("decision")
        note = entry.get("note")
        if note is not None and not isinstance(note, str):
            raise _invalid_decision("note must be text")
        if decision == "uphold":
            if entry.get("decided_outcome") is not None or entry.get("reason_code") is not None:
                raise _invalid_decision("An uphold keeps the outcome and has no reason code")
            stored_note = None
            if note is not None and note.strip():
                stored_note = _stored_text(note)
                if stored_note is None:
                    raise _invalid_decision("note must be at most 2000 characters of text")
            parsed.append(_Decision(row, "uphold", None, None, stored_note))
        elif decision == "change":
            outcome = entry.get("decided_outcome")
            if not isinstance(outcome, str) or outcome not in REVIEW_REVIEWER_OUTCOMES:
                raise _invalid_decision("A change names a reviewer outcome")
            if outcome == row.appealed_outcome:
                raise ReviewQueueError(
                    422, "appeal_change_matches", "The outcome already is the appealed outcome"
                )
            if outcome == "not_applicable" and not na_allowed.get(row.criterion_id, False):
                raise ReviewQueueError(
                    422, "reviewer_na_not_allowed", "The criterion may not be not applicable"
                )
            reason = entry.get("reason_code")
            if (
                not isinstance(reason, str)
                or reason not in REVIEW_OVERRIDE_REASONS
                or note is None
                or not note.strip()
            ):
                raise ReviewQueueError(
                    422, "override_reason_required", "A change needs a reason code and a note"
                )
            stored_note = _stored_text(note)
            if stored_note is None:
                raise _invalid_decision("note must be at most 2000 characters of text")
            parsed.append(_Decision(row, "change", outcome, reason, stored_note))
        else:
            raise _invalid_decision("decision must be uphold or change")
    return parsed


def decide_appeal(
    db: Session,
    caller: AppealCaller,
    *,
    appeal_id: str,
    expected_revision: int,
    body: dict[str, Any],
    now: datetime,
) -> Outcome:
    """The checks of ADR 0057 D3 item 9, in order; a refusal writes nothing."""
    appeal, meeting = _supervised(db, caller, appeal_id)
    _writable(appeal, expected_revision)
    if appeal.assignee_user_id != caller.user_id:
        raise ReviewQueueError(403, "review_appeal_not_assignee", "Only the assignee decides")
    check_decider(db, appeal, meeting, user_id=caller.user_id)
    check_assignee(
        db,
        workspace_id=caller.workspace_id,
        meeting=meeting,
        assignee_user_id=caller.user_id,
        now=now,
    )
    machine = _machine(db, caller.workspace_id, appeal.scorecard_result_id)
    if machine.scorecards_version != SCORECARDS_VERSION:
        raise ReviewQueueError(
            409,
            "scorecard_version_unsupported",
            f"{machine.scorecards_version} is not this engine's {SCORECARDS_VERSION}",
        )
    criterion_rows = _criterion_rows(db, machine)
    positions = {row.criterion_id: row.position for row in criterion_rows}
    appealed = sorted(
        db.scalars(
            select(ReviewAppealCriterion).where(ReviewAppealCriterion.appeal_id == appeal.id)
        ),
        key=lambda row: (positions.get(row.criterion_id, 0), row.criterion_id),
    )
    decisions = _parse_decisions(
        body.get("decisions"),
        appealed,
        {row.criterion_id: row.na_allowed for row in criterion_rows},
    )
    disposition_note = _stored_text(body.get("disposition_note"))
    if disposition_note is None:
        raise ReviewQueueError(
            422,
            "appeal_disposition_note_required",
            f"disposition_note must be 1 to {REVIEW_NOTE_MAX_LENGTH} characters of text",
        )
    human = _human(db, appeal.review_item_id)
    assert human is not None  # ``fk_review_appeals_review_result`` holds
    changes = {
        d.criterion.criterion_id: d.decided_outcome for d in decisions if d.decision == "change"
    }
    # HOM-77 (ADR 0062 D2): a decision that would cancel a coaching task whose coachee
    # the decider is refuses here, before the first write.  A local import: the coaching
    # service imports this module.
    from backend.services import coaching_service

    coaching_service.check_decider_not_coachee(
        db, appeal=appeal, changes=changes, decider_user_id=caller.user_id
    )
    scored = None
    outcomes: dict[str, str] = {}
    if changes:
        outcomes = {**human.reviewer_outcomes, **changes}
        scored = score_human_result(db, machine, outcomes)  # 409 when not computable
    upheld = len(decisions) - len(changes)
    if not changes:
        disposition = "upheld"
    elif upheld == 0:
        disposition = "granted"
    else:
        disposition = "partially_granted"

    # The compare-and-set first, so two concurrent decides never both land.
    _compare_and_set(
        db,
        appeal,
        expected_revision=expected_revision,
        now=now,
        values={
            "status": "decided",
            "disposition": disposition,
            "disposition_note": disposition_note,
            "decided_by_user_id": caller.user_id,
            "decided_at": now,
        },
    )
    for decision in decisions:
        row = decision.criterion
        row.decision = decision.decision
        row.decided_outcome = decision.decided_outcome
        row.reason_code = decision.reason_code
        row.note = decision.note
        row.updated_at = now
    appeal_result_id: str | None = None
    final_outcome = human.outcome
    if scored is not None:
        stored = ReviewAppealResult(
            id=new_id(),
            workspace_id=appeal.workspace_id,
            meeting_id=appeal.meeting_id,
            appeal_id=appeal.id,
            review_item_id=appeal.review_item_id,
            scorecard_result_id=appeal.scorecard_result_id,
            scorecards_version=scored.scorecards_version,
            reviewer_outcomes=outcomes,
            outcome=scored.outcome,
            reason=scored.reason,
            complete=scored.complete,
            pass_threshold=scored.pass_threshold,
            applicable_weight=scored.applicable_weight,
            earned_weight=scored.earned_weight,
            failed_weight=scored.failed_weight,
            insufficient_weight=scored.insufficient_weight,
            result=scored.to_json(),
            created_at=now,
        )
        db.add(stored)
        appeal_result_id = stored.id
        final_outcome = stored.outcome
    db.flush()
    # HOM-77 (ADR 0062 D3): a change of a coached criterion to an outcome other than
    # failed cancels its active coaching task, in this transaction.
    coaching_service.cancel_overturned(
        db, appeal=appeal, changes=changes, decider_user_id=caller.user_id, now=now
    )
    notification_service.notify_appeal(
        db,
        appeal=appeal,
        kind=notification_service.DECIDED,
        user_ids=[appeal.appellant_user_id],
        now=now,
    )
    return Outcome(
        appeal,
        meeting,
        SUPERVISOR,
        AuditEventType.REVIEW_APPEAL_DECIDED,
        {
            "disposition": disposition,
            "upheld_count": upheld,
            "changed_count": len(changes),
            "human_outcome": human.outcome,
            "final_outcome": final_outcome,
            "appeal_result_id": appeal_result_id,
            "revision": appeal.revision,
        },
    )


# --- the history --------------------------------------------------------------------------


def _without_user_ids(details: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in details.items() if "user_id" not in key}


def history(db: Session, caller: AppealCaller, *, appeal_id: str) -> list[dict[str, Any]]:
    """The appeal's audit events, oldest first.  In the appellant's view an actor id is
    shown only for the appellant, every other actor is ``reviewer``, and no user id stays
    in the details."""
    appeal, _, view = _visible(db, caller, appeal_id)
    events = db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.workspace_id == caller.workspace_id,
            AuditEvent.target_type == "review_appeal",
            AuditEvent.target_id == appeal.id,
        )
        .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
    )
    entries: list[dict[str, Any]] = []
    for event in events:
        is_appellant = (
            event.actor_user_id is not None and event.actor_user_id == appeal.appellant_user_id
        )
        if view == APPELLANT:
            actor = event.actor_user_id if is_appellant else None
            role = APPELLANT if is_appellant else "reviewer"
            details = _without_user_ids(event.details or {})
        else:
            actor = event.actor_user_id
            role = APPELLANT if is_appellant else ("system" if actor is None else "reviewer")
            details = dict(event.details or {})
        entries.append(
            {
                "id": event.id,
                "event_type": event.event_type,
                "occurred_at": as_utc(event.occurred_at),
                "actor_user_id": actor,
                "actor_role": role,
                "details": details,
            }
        )
    return entries
