"""Approval-bound action proposals (HOM-135, WP24-T03, F60, ADR 0082).

Every request body forbids unknown keys.  ``expected_revision`` is the proposal revision
the caller read: a strict int from 1 (``true``, ``"2"`` and ``2.0`` are refused).  The
other fields are loose on purpose, like the coaching bodies: the service checks them, so a
bad value answers the route's own code (``action_proposal_invalid``,
``coaching_due_invalid``, ...) and not a generic validation error.

``ActionProposalView`` is the preview of one proposal; the list returns
``ActionProposalListItem``, the same view without the payload and without the live
staleness check.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

# ``action_proposals.revision`` is a PostgreSQL integer; a larger number is no revision.
MAX_REVISION = 2**31 - 1
ExpectedRevision = Annotated[int, Field(ge=1, le=MAX_REVISION, strict=True)]


class StaleView(BaseModel):
    is_stale: bool
    reason: str | None


class DeliveryRequestView(BaseModel):
    id: str
    idempotency_key: str
    approved_by_user_id: str | None
    approved_at: datetime


class ActionProposalListItem(BaseModel):
    id: str
    meeting_id: str
    action_type: str
    source_type: str
    action_item_id: str | None
    review_item_id: str | None
    criterion_id: str | None
    target_system: str | None
    target_external_record_id: str | None
    payload_hash: str
    payload_version: int
    source_transcript_revision_id: str
    status: str
    revision: int
    created_by_user_id: str | None
    created_at: datetime
    updated_at: datetime
    decided_by_user_id: str | None
    decided_at: datetime | None
    reject_reason: str | None
    revoked_by_user_id: str | None
    revoked_at: datetime | None
    revoke_reason: str | None
    evidence_span_ids: list[str]  # of an action item source, oldest first; empty for a finding
    delivery_request: DeliveryRequestView | None


class ActionProposalView(ActionProposalListItem):
    payload: dict[str, Any]
    # Checked live for a ``pending`` proposal only; ``null`` for every other status.
    stale: StaleView | None


class ActionProposalListResponse(BaseModel):
    items: list[ActionProposalListItem]
    next_cursor: str | None


class ActionProposalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: Any = None
    action_item_id: Any = None
    review_item_id: Any = None
    criterion_id: Any = None
    target_system: Any = None
    target_external_record_id: Any = None
    owner_user_id: Any = None
    due_at: Any = None
    instruction: Any = None


class ActionProposalWriteRequest(BaseModel):
    """The common part of every proposal write: the revision the caller read."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: ExpectedRevision


class ActionProposalRefreshRequest(ActionProposalWriteRequest):
    pass


class ActionProposalApproveRequest(ActionProposalWriteRequest):
    payload_hash: Any = None
    source_transcript_revision_id: Any = None
    # The edited payload of edit-then-approve; absent or null approves the stored one.
    payload: Any = None


class ActionProposalDecisionRequest(ActionProposalWriteRequest):
    """A reject or a revoke: the reason from its list."""

    reason: Any = None
