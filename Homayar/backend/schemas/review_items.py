"""Supervisor review queue API (HOM-73, ADR 0051).

Every request body forbids unknown keys.  ``expected_revision`` is the revision the
caller read: a strict int from 1 (``true``, ``"2"`` and ``2.0`` are refused).  The due
time rule (an ISO 8601 string with a UTC offset, later than now, at most 90 days ahead) is
checked by the service, so a number, a naive time or a time out of range answers ``422
review_due_at_invalid`` instead of a generic error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

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
# Any JSON value, required: ``check_due_at`` accepts only an ISO 8601 string.
DueAt = Any


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
