"""Coaching tasks from accepted findings (HOM-77, ADR 0062).

Every request body forbids unknown keys.  ``expected_revision`` is the task revision the
caller read: a strict int from 1 (``true``, ``"2"`` and ``2.0`` are refused).  The other
fields are loose on purpose, like HOM-76's appeal bodies: the service checks them, so a
bad value answers the route's own code (``criterion_not_found``,
``coaching_due_invalid``, ``coaching_instruction_required``, ...) and not a generic
validation error.

``CoachingTaskView`` is the supervisor view or the agent view; the agent view sets
``team_id``, ``created_by_user_id``, ``closed_by_user_id``, ``cancelled_by_user_id``,
``cancel_note`` and the two sweep marks to ``null``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ``coaching_tasks.revision`` is a PostgreSQL integer; a larger number is no revision.
MAX_REVISION = 2**31 - 1

ExpectedRevision = Annotated[int, Field(ge=1, le=MAX_REVISION, strict=True)]


class CoachingTaskView(BaseModel):
    view: Literal["supervisor", "agent"]
    id: str
    review_item_id: str
    meeting_id: str
    team_id: str | None
    scorecard_result_id: str
    criterion_id: str
    finding_source: str
    agent_user_id: str | None  # the coachee
    owner_user_id: str | None  # the coach
    created_by_user_id: str | None
    instruction: str
    due_at: datetime
    overdue: bool  # active and ``now >= due_at``; never stored
    status: str
    acknowledged_at: datetime | None
    acknowledgement_note: str | None
    follow_up_outcome: str | None
    follow_up_note: str | None
    closed_at: datetime | None
    closed_by_user_id: str | None
    cancel_reason: str | None
    cancel_note: str | None
    cancelled_at: datetime | None
    cancelled_by_user_id: str | None
    reminder_notified_at: datetime | None
    overdue_notified_at: datetime | None
    revision: int
    created_at: datetime
    updated_at: datetime


class CoachingTaskListResponse(BaseModel):
    items: list[CoachingTaskView]
    next_cursor: str | None


class CoachingHistoryEntry(BaseModel):
    id: str
    event_type: str
    occurred_at: datetime
    actor_user_id: str | None
    actor_role: Literal["coachee", "owner", "reviewer", "system"]
    details: dict[str, Any]


class CoachingHistoryResponse(BaseModel):
    items: list[CoachingHistoryEntry]  # oldest first, every event of the task
    next_cursor: str | None  # always null: a task has a handful of events


class CoachingTaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: Any = None
    owner_user_id: Any = None
    due_at: Any = None
    instruction: Any = None


class CoachingTaskWriteRequest(BaseModel):
    """The common part of every task write: the revision the caller read."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: ExpectedRevision


class CoachingTaskReassignRequest(CoachingTaskWriteRequest):
    owner_user_id: Any = None


class CoachingTaskAcknowledgeRequest(CoachingTaskWriteRequest):
    note: Any = None


class CoachingTaskCloseRequest(CoachingTaskWriteRequest):
    follow_up_outcome: Any = None
    follow_up_note: Any = None


class CoachingTaskCancelRequest(CoachingTaskWriteRequest):
    cancel_reason: Any = None
    cancel_note: Any = None
