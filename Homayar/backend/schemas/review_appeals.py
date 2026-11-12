"""Agent appeals of review results (HOM-76, ADR 0057).

Every request body forbids unknown keys.  ``expected_revision`` is the appeal revision
the caller read: a strict int from 1 (``true``, ``"2"`` and ``2.0`` are refused).  The
other fields are loose on purpose, like HOM-74's ``ReviewDecisionRequest``: the service
checks them, so a bad value answers the appeal's own code (``appeal_criteria_invalid``,
``appeal_reason_required``, ``appeal_decisions_incomplete``, ...) and not a generic
validation error.

The agent views never hold a reviewer's user id: ``MyReviewDetail`` drops
``decided_by_user_id`` and the appellant ``AppealView`` sets every reviewer user id and
``team_id`` to ``null``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.review_items import ReviewScoreView

# ``review_appeals.revision`` is a PostgreSQL integer; a larger number is no revision.
MAX_REVISION = 2**31 - 1

ExpectedRevision = Annotated[int, Field(ge=1, le=MAX_REVISION, strict=True)]


class MyReviewAppealSummary(BaseModel):
    id: str
    status: str
    disposition: str | None


class MyReviewSummary(BaseModel):
    review_item_id: str
    meeting_id: str
    completed_at: datetime
    appeal_deadline: datetime  # completed_at + 14 days
    machine: ReviewScoreView
    human: ReviewScoreView
    final: ReviewScoreView  # the appeal result when one exists, else the human result
    final_source: Literal["human", "appeal"]
    appeal: MyReviewAppealSummary | None


class MyReviewListResponse(BaseModel):
    items: list[MyReviewSummary]
    next_cursor: str | None


class MyReviewDecisionView(BaseModel):
    """The reviewer's decision on one criterion, without the reviewer's user id."""

    decision: str
    reviewer_outcome: str
    reason_code: str | None
    note: str | None
    evidence: list[Any]


class MyReviewAppealDecisionView(BaseModel):
    decision: str
    decided_outcome: str | None
    reason_code: str | None
    note: str | None


class MyReviewCriterion(BaseModel):
    criterion_id: str
    position: int
    weight: int
    auto_fail: bool
    na_allowed: bool
    reviewable: bool
    machine_outcome: str
    human_outcome: str
    decision: MyReviewDecisionView | None
    appeal: MyReviewAppealDecisionView | None  # only once the appeal is decided


class MyReviewDetail(MyReviewSummary):
    criteria: list[MyReviewCriterion]  # in position order


class AppealCriterionView(BaseModel):
    criterion_id: str
    appealed_outcome: str
    decision: str | None
    decided_outcome: str | None
    reason_code: str | None
    note: str | None


class AppealView(BaseModel):
    """One appeal.  ``view`` is ``supervisor`` (the full view) or ``appellant`` (every
    reviewer user id and ``team_id`` are ``null``)."""

    view: Literal["supervisor", "appellant"]
    id: str
    review_item_id: str
    meeting_id: str
    team_id: str | None
    scorecard_result_id: str
    status: str
    appellant_user_id: str | None
    original_reviewer_user_id: str | None
    assignee_user_id: str | None
    assigned_by_user_id: str | None
    assigned_at: datetime | None
    reason_note: str
    disposition: str | None
    disposition_note: str | None
    decided_by_user_id: str | None
    decided_at: datetime | None
    withdrawn_at: datetime | None
    created_at: datetime
    updated_at: datetime
    revision: int
    criteria: list[AppealCriterionView]  # in position order
    human: ReviewScoreView
    appeal_result: ReviewScoreView | None
    final: ReviewScoreView


class AppealListResponse(BaseModel):
    items: list[AppealView]
    next_cursor: str | None


class HistoryEntry(BaseModel):
    id: str
    event_type: str
    occurred_at: datetime
    actor_user_id: str | None
    actor_role: Literal["appellant", "reviewer", "system"]
    details: dict[str, Any]


class AppealHistoryResponse(BaseModel):
    items: list[HistoryEntry]  # oldest first, every event of the appeal
    next_cursor: str | None  # always null: an appeal has a handful of events


class AppealFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_ids: Any = None
    reason_note: Any = None


class AppealWriteRequest(BaseModel):
    """The body of a withdraw: only the revision the caller read."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: ExpectedRevision


class AppealAssignRequest(AppealWriteRequest):
    assignee_user_id: Any = None


class AppealDecideRequest(AppealWriteRequest):
    disposition_note: Any = None
    decisions: Any = None
