"""Supervisor review queue API (HOM-73, ADR 0051; HOM-74, ADR 0053).

Every request body forbids unknown keys.  ``expected_revision`` is the revision the
caller read: a strict int from 1 (``true``, ``"2"`` and ``2.0`` are refused).  The due
time rule (an ISO 8601 string with a UTC offset, later than now, at most 90 days ahead) is
checked by the service, so a number, a naive time or a time out of range answers ``422
review_due_at_invalid`` instead of a generic error.

The decision body (HOM-74) is loose on purpose: ``decision``, ``reviewer_outcome``,
``reason_code``, ``note`` and ``evidence_turn_indexes`` are plain values the service
checks, so a bad value answers the decision's own code (``review_decision_invalid``,
``override_evidence_required``, ...) and not a generic validation error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, WithJsonSchema

from backend.schemas.coverage import (
    CoverageWindowResponse,
    ReviewSampleRequest,
    SampleDrawResponse,
)

# ``review_items.revision`` is a PostgreSQL integer; a larger number is no revision.
MAX_REVISION = 2**31 - 1

ReferenceId = Annotated[str, StringConstraints(min_length=1, max_length=36)]
ExpectedRevision = Annotated[int, Field(ge=1, le=MAX_REVISION, strict=True)]
CancelReason = Literal["duplicate", "out_of_scope", "meeting_unusable", "other"]
# Any JSON value reaches ``check_due_at``, which accepts only a date-time string, so a
# number is ``review_due_at_invalid`` too.  The API schema names the one accepted form.
DueAt = Annotated[Any, WithJsonSchema({"type": "string", "format": "date-time"})]


class ReviewItemResponse(BaseModel):
    id: str
    meeting_id: str
    team_id: str | None  # the conversation's current team
    scorecard_result_id: str | None
    source: str
    source_details: dict[str, Any]
    status: str
    assignee_user_id: str | None
    assigned_by_user_id: str | None
    assigned_at: datetime | None
    due_at: datetime
    is_overdue: bool  # active and ``due_at <= now``
    created_by_user_id: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    revision: int


class ReviewItemListResponse(BaseModel):
    items: list[ReviewItemResponse]  # ``due_at`` then ``id``, ascending
    next_cursor: str | None


class ReviewItemCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meeting_id: ReferenceId
    due_at: DueAt
    assignee_user_id: ReferenceId | None = None
    scorecard_result_id: ReferenceId | None = None


class ReviewItemWriteRequest(BaseModel):
    """The body of ``start`` and ``complete``."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: ExpectedRevision


class ReviewItemAssignRequest(ReviewItemWriteRequest):
    # Required, and ``null`` unassigns.
    assignee_user_id: ReferenceId | None


class ReviewItemDueTimeRequest(ReviewItemWriteRequest):
    due_at: DueAt


class ReviewItemCancelRequest(ReviewItemWriteRequest):
    reason: CancelReason


class ReviewDecisionRequest(BaseModel):
    """The body of a criterion decision (HOM-74).

    Loose on purpose: every field but ``expected_revision`` is an unchecked value the
    service validates, so its own error codes answer, never a generic 422.
    """

    model_config = ConfigDict(extra="forbid")

    expected_revision: ExpectedRevision
    decision: Any = None
    reviewer_outcome: Any = None
    reason_code: Any = None
    note: Any = None
    evidence_turn_indexes: Any = None


class ReviewCriterionMachineView(BaseModel):
    outcome: str
    reason: str | None
    decided_by: str | None
    review_required: bool
    review_reasons: list[str]
    evidence: list[Any]


class ReviewCriterionDecisionView(BaseModel):
    decision: str
    reviewer_outcome: str
    reason_code: str | None
    note: str | None
    evidence: list[Any]
    decided_by_user_id: str | None
    updated_at: datetime


class ReviewCriterionEntry(BaseModel):
    criterion_id: str
    position: int
    weight: int
    auto_fail: bool
    na_allowed: bool
    reviewable: bool  # false only for ``not_applicable_by_conversation``
    machine: ReviewCriterionMachineView
    decision: ReviewCriterionDecisionView | None


class ReviewScoreView(BaseModel):
    outcome: str
    reason: str | None
    complete: bool
    score: float | None
    score_min: float | None
    score_max: float | None
    pass_threshold: int
    applicable_weight: int
    earned_weight: int
    failed_weight: int
    insufficient_weight: int


class ReviewHumanScoreView(ReviewScoreView):
    final: bool  # the stored row after completion, a preview before


class ReviewCriteriaResponse(BaseModel):
    review_item_id: str
    revision: int
    status: str
    scorecard_result_id: str | None
    machine: ReviewScoreView | None  # the stored machine result
    human: ReviewHumanScoreView | None  # the stored human result, or a preview
    criteria: list[ReviewCriterionEntry]  # in position order


class ReviewSampleEnqueueRequest(ReviewSampleRequest):
    """The request of ``POST /admin/review-samples``, plus ``due_at`` for every created
    item."""

    due_at: DueAt


class ReviewSampleEnqueueResponse(BaseModel):
    coverage_version: str
    window: CoverageWindowResponse
    draw: SampleDrawResponse
    created: list[ReviewItemResponse]  # in draw order
    skipped_meeting_ids: list[str]  # selections that already had an active item
