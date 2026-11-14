"""Approval-bound action proposals (HOM-135, WP24-T03, F60, ADR 0082 D3 to D6).

A proposal previews one exact outbound payload, built here from one source and pinned to
it: an action item (``crm_update``, ``ticket_create``) or a review finding
(``coaching_task``).  A person with ``action:approve`` approves it, optionally after an
edit of the editable fields; the approval writes exactly one immutable
``action_delivery_requests`` row in the same transaction.  Nothing here writes outside
Homayar: HOM-136 delivers the request.

Who sees what, for caller ``U`` in workspace ``W``: a proposal whose meeting passes
``U``'s record scope (``RecordAccess.meeting_clause``) and is not deleted.  A
``coaching_task`` proposal whose review item still exists also needs the review-queue
rule of HOM-73 (``Reviewer.meeting_clause``, as ``review_queue_service._visible_item``
applies it); once the review item is gone, the record scope alone decides.  Anything
else is ``404 action_proposal_not_found``, like a missing id or an id of another
workspace.

Every write locks the proposal row (``SELECT ... FOR UPDATE``), checks in the SPEC order
and changes the row only after every check passed, so two concurrent writes never both
land.  A refusal raises ``ActionProposalError`` before anything is written; when the
proposal was visible, the error carries the ``denied`` audit details for the router.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    ACTION_PROPOSAL_REJECT_REASONS,
    ACTION_PROPOSAL_REVOKE_REASONS,
    ACTION_PROPOSAL_STATUSES,
    ACTION_PROPOSAL_TYPES,
    ActionDeliveryRequest,
    ActionItem,
    ActionProposal,
    ActionStatus,
    AnalysisRun,
    ConversationExternalLink,
    EvidenceSpan,
    ExternalRecord,
    IntegrationMappingProfile,
    Meeting,
    ReviewItem,
    ScorecardResultRecord,
    new_id,
)
from backend.jobs.leases import as_utc
from backend.security.authorization import RecordAccess
from backend.services import coaching_service
from backend.services.audit_event_types import AuditEventType
from backend.services.evidence_service import FINDING_ACTION_ITEM
from backend.services.review_queue_service import Reviewer, ReviewQueueError
from backend.services.transcript_service import current_revision

TARGET_TYPE = "action_proposal"
ITEM_TYPES = ("crm_update", "ticket_create")
COACHING_TYPE = "coaching_task"
SOURCE_ACTION_ITEM = "action_item"
SOURCE_FINDING = "review_finding"
PAYLOAD_SCHEMAS = {
    "ticket_create": "ticket_create.v1",
    "crm_update": "crm_update.v1",
    "coaching_task": "coaching_task.v1",
}
# The fields edit-then-approve may change (SPEC 5.2); every other key keeps its value.
EDITABLE_FIELDS = {
    "ticket_create": frozenset({"title", "description", "priority", "due_at"}),
    "crm_update": frozenset({"note", "due_at"}),
    "coaching_task": frozenset({"owner_user_id", "due_at", "instruction"}),
}
ACTIONABLE_STATUSES = (ActionStatus.OPEN.value, ActionStatus.IN_PROGRESS.value)
# The priorities of ``ck_action_priority``; the model keeps them in the check only.
ACTION_PRIORITIES = ("low", "medium", "high")
CRM_LINK_TYPES = ("customer", "ticket")
TITLE_MAX_LENGTH = 120
# The coaching refusal of a blank or too long instruction (``coaching_service``).
INSTRUCTION_REQUIRED = "coaching_instruction_required"
TEXT_MAX_LENGTH = 4000
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
# The longest list cursor read (P2-6.1); a real one is about 120 characters.
MAX_CURSOR_LENGTH = 512
# The slug rule of ``ck_action_proposals_target_system`` (``_slug_check``).
_SLUG = re.compile(r"[a-z][a-z0-9_.-]{0,59}")
# ASCII digits only; ``\d`` and ``str.isdigit`` also match other scripts' digits.
_DIGITS = re.compile(r"[0-9]+")
# The open-proposal indexes as each database names them in a unique violation.
_OPEN_INDEX_MARKERS = (
    "uq_action_proposals_open_item",
    "uq_action_proposals_open_finding",
    "action_proposals.action_item_id, action_proposals.action_type",
    "action_proposals.review_item_id, action_proposals.criterion_id",
)


# --- errors -------------------------------------------------------------------------------


class ActionProposalError(Exception):
    """A refusal: the HTTP status, the code, optional details and, when the proposal was
    visible, the event type and the ids-only details of its ``denied`` audit row."""

    def __init__(
        self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.details = details
        self.event: AuditEventType | None = None
        self.denied: dict[str, Any] | None = None


def _invalid(message: str) -> ActionProposalError:
    return ActionProposalError(422, "action_proposal_invalid", message)


def _not_found() -> ActionProposalError:
    return ActionProposalError(404, "action_proposal_not_found", "Action proposal not found")


def _not_pending() -> ActionProposalError:
    return ActionProposalError(
        409, "action_proposal_not_pending", "Only a pending proposal can do this"
    )


def _not_approved() -> ActionProposalError:
    return ActionProposalError(
        409, "action_proposal_not_approved", "Only an approved proposal can be revoked"
    )


def _revision_conflict(current: int) -> ActionProposalError:
    return ActionProposalError(
        409,
        "action_proposal_revision_conflict",
        "The proposal changed since it was read; reload and retry",
        {"current_revision": current},
    )


def _target_invalid() -> ActionProposalError:
    return ActionProposalError(
        422, "action_proposal_target_invalid", "The target is not a valid destination"
    )


def _from_queue(error: ReviewQueueError) -> ActionProposalError:
    """A coaching or review-queue refusal, with its own status and code."""
    return ActionProposalError(error.status_code, error.code, str(error), error.details)


# --- the caller ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProposalCaller:
    """The caller's workspace, record scope and review authority (for findings)."""

    workspace_id: str
    user_id: str
    record_access: RecordAccess
    coaching: coaching_service.CoachingCaller

    @property
    def reviewer(self) -> Reviewer:
        return self.coaching.reviewer


# --- hashing ------------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    """The one canonical JSON text of a payload or a digest input (SPEC 5.2)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    """The one hash: sha256 of the UTF-8 text, lowercase hex."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha256_hex(canonical_json(value))


def delivery_idempotency_key(workspace_id: str, proposal_id: str, payload_hash: str) -> str:
    """The key HOM-136 sends to the connector (ADR 0082 D2)."""
    return sha256_hex(f"{workspace_id}|{proposal_id}|{payload_hash}")


def _iso(value: datetime | None) -> str | None:
    moment = as_utc(value)
    return moment.isoformat() if moment is not None else None


def _status_value(value: Any) -> str:
    return value.value if isinstance(value, ActionStatus) else str(value)


# --- visibility ---------------------------------------------------------------------------


def _visible_meeting(caller: ProposalCaller) -> Any:
    """The meetings the caller may read: live, in the workspace, in record scope."""
    return and_(
        Meeting.workspace_id == caller.workspace_id,
        Meeting.deleted_at.is_(None),
        caller.record_access.meeting_clause(),
    )


def _proposals(caller: ProposalCaller) -> Select[Any]:
    """The visible proposals with their meetings (SPEC 4.4 and P2-1.2)."""
    return (
        select(ActionProposal, Meeting)
        .join(
            Meeting,
            and_(
                Meeting.id == ActionProposal.meeting_id,
                Meeting.workspace_id == caller.workspace_id,
            ),
        )
        .where(
            ActionProposal.workspace_id == caller.workspace_id,
            _visible_meeting(caller),
            or_(
                ActionProposal.action_type != COACHING_TYPE,
                ActionProposal.review_item_id.is_(None),
                caller.reviewer.meeting_clause(),
            ),
        )
    )


def _load(
    db: Session, caller: ProposalCaller, proposal_id: str, *, lock: bool
) -> tuple[ActionProposal, Meeting]:
    # P2-7.5: a path id the database cannot hold is no proposal; refuse it before a query.
    if not _is_id(proposal_id):
        raise _not_found()
    query = _proposals(caller).where(ActionProposal.id == proposal_id)
    if lock:
        query = query.with_for_update(of=ActionProposal)
    row = db.execute(query.execution_options(populate_existing=True)).first()
    if row is None:
        raise _not_found()
    return row[0], row[1]


def get_proposal(db: Session, caller: ProposalCaller, *, proposal_id: str) -> ActionProposal:
    return _load(db, caller, proposal_id, lock=False)[0]


def _visible_action_item(
    db: Session, caller: ProposalCaller, item_id: Any
) -> tuple[ActionItem, Meeting]:
    row = None
    if isinstance(item_id, str) and item_id:
        row = db.execute(
            select(ActionItem, Meeting)
            .join(
                Meeting,
                and_(
                    Meeting.id == ActionItem.meeting_id, Meeting.workspace_id == caller.workspace_id
                ),
            )
            .where(
                ActionItem.workspace_id == caller.workspace_id,
                ActionItem.id == item_id,
                _visible_meeting(caller),
            )
        ).first()
    if row is None:
        raise ActionProposalError(404, "action_item_not_found", "Action item not found")
    return row[0], row[1]


# --- the request shape (SPEC 5.1 step 1) --------------------------------------------------


def _encodable(text: str) -> bool:
    """UTF-8 text: a lone surrogate cannot be encoded, hashed or sent to the database."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _is_id(value: Any) -> bool:
    """1 to 64 characters that the database can hold (P2-7.2): no NUL byte (PostgreSQL
    refuses it in text) and UTF-8 text (psycopg cannot send a lone surrogate).  No UUID
    pattern: ``target_external_record_id`` is an outside system's id."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= 64
        and "\x00" not in value
        and _encodable(value)
    )


def _shape(body: dict[str, Any]) -> str:
    """The action type, once the body has the fields of the PR 1 target table."""
    action_type = body.get("action_type")
    if not isinstance(action_type, str) or action_type not in ACTION_PROPOSAL_TYPES:
        raise _invalid(f"action_type must be one of {', '.join(ACTION_PROPOSAL_TYPES)}")
    if action_type in ITEM_TYPES:
        if not _is_id(body.get("action_item_id")):
            raise _invalid(f"action_item_id is required for {action_type}")
        for name in ("review_item_id", "criterion_id", "owner_user_id", "due_at", "instruction"):
            if body.get(name) is not None:
                raise _invalid(f"{name} is not allowed for {action_type}")
    else:
        if body.get("action_item_id") is not None:
            raise _invalid("action_item_id is not allowed for coaching_task")
        for name in ("review_item_id", "criterion_id"):
            if not _is_id(body.get(name)):
                raise _invalid(f"{name} is required for coaching_task")
    system, record = body.get("target_system"), body.get("target_external_record_id")
    if action_type == COACHING_TYPE:
        for name, value in (("target_system", system), ("target_external_record_id", record)):
            if value is not None:
                raise _invalid(f"{name} is not allowed for coaching_task")
        return action_type
    if not isinstance(system, str) or _SLUG.fullmatch(system) is None:
        raise _invalid(f"target_system must be the slug of a source system for {action_type}")
    if action_type == "crm_update" and not _is_id(record):
        raise _invalid("target_external_record_id is required for crm_update")
    if action_type == "ticket_create" and record is not None:
        raise _invalid("target_external_record_id is not allowed for ticket_create")
    return action_type


# --- the source, the pin, the target and the payload (SPEC 5.1 steps 2 to 6) --------------


@dataclass
class Built:
    """A proposal's source state, pin, target and payload, read now."""

    meeting: Meeting
    action_item: ActionItem | None
    review_item: ReviewItem | None
    criterion_id: str | None
    pin: str
    target_system: str | None
    target_record: ExternalRecord | None
    payload: dict[str, Any]
    source_digest: str


def item_digest(item: ActionItem) -> str:
    return canonical_hash(
        {
            "id": item.id,
            "description": item.description,
            "assignee_text": item.assignee_text,
            "assignee_user_id": item.assignee_user_id,
            "due_at": _iso(item.due_at),
            "priority": item.priority,
            "status": _status_value(item.status),
            "kind": item.kind,
            "analysis_run_id": item.analysis_run_id,
        }
    )


def finding_digest(db: Session, item: ReviewItem, criterion_id: str, meeting: Meeting) -> str:
    outcomes, source = coaching_service.final_outcomes(db, item)
    return canonical_hash(
        {
            "review_item_id": item.id,
            "criterion_id": criterion_id,
            "scorecard_result_id": item.scorecard_result_id,
            "final_outcome": outcomes.get(criterion_id),
            "outcome_source": source,
            "agent_user_id": meeting.agent_user_id,
        }
    )


def _pin(db: Session, meeting: Meeting, source_revision_id: str | None) -> str:
    """The meeting's current revision id; the source must have been read from it."""
    current = current_revision(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    if current is None:
        raise ActionProposalError(409, "transcript_missing", "The call has no transcript")
    if (source_revision_id or current.id) != current.id:
        raise ActionProposalError(
            409,
            "action_proposal_source_stale",
            "The source was read from another transcript revision",
        )
    return current.id


def _item_revision_id(db: Session, item: ActionItem) -> str | None:
    """An AI item's analysis run revision; ``None`` for a person item (no run)."""
    if item.analysis_run_id is None:
        return None
    return db.scalar(
        select(AnalysisRun.transcript_revision_id).where(
            AnalysisRun.id == item.analysis_run_id, AnalysisRun.workspace_id == item.workspace_id
        )
    )


def _crm_record(
    db: Session, *, workspace_id: str, meeting_id: str, system: Any, record_id: Any
) -> ExternalRecord | None:
    """An active record of the system, linked to the meeting as its customer or ticket."""
    if not isinstance(system, str) or not isinstance(record_id, str):
        return None
    return db.scalar(
        select(ExternalRecord)
        .join(
            ConversationExternalLink,
            and_(
                ConversationExternalLink.external_record_id == ExternalRecord.id,
                ConversationExternalLink.workspace_id == workspace_id,
                ConversationExternalLink.meeting_id == meeting_id,
                ConversationExternalLink.link_type.in_(CRM_LINK_TYPES),
                ConversationExternalLink.source_system == system,
            ),
        )
        .where(
            ExternalRecord.id == record_id,
            ExternalRecord.workspace_id == workspace_id,
            ExternalRecord.source_system == system,
            ExternalRecord.is_active.is_(True),
        )
        .limit(1)
    )


def _profile_exists(db: Session, *, workspace_id: str, system: Any) -> bool:
    if not isinstance(system, str):
        return False
    return (
        db.scalar(
            select(IntegrationMappingProfile.id).where(
                IntegrationMappingProfile.workspace_id == workspace_id,
                IntegrationMappingProfile.source_system == system,
                IntegrationMappingProfile.is_active.is_(True),
            )
        )
        is not None
    )


def _target(
    db: Session,
    *,
    action_type: str,
    workspace_id: str,
    meeting_id: str,
    system: Any,
    record_id: Any,
) -> ExternalRecord | None:
    """Step 5: the target is valid now, or ``422 action_proposal_target_invalid``."""
    if action_type == "crm_update":
        record = _crm_record(
            db, workspace_id=workspace_id, meeting_id=meeting_id, system=system, record_id=record_id
        )
        if record is None:
            raise _target_invalid()
        return record
    if action_type == "ticket_create" and not _profile_exists(
        db, workspace_id=workspace_id, system=system
    ):
        raise _target_invalid()
    return None


def _item_payload(
    action_type: str, item: ActionItem, record: ExternalRecord | None
) -> dict[str, Any]:
    common = {
        "due_at": _iso(item.due_at),
        "priority": item.priority,
        "kind": item.kind,
        "meeting_id": item.meeting_id,
        "action_item_id": item.id,
    }
    if action_type == "ticket_create":
        return {
            "schema": PAYLOAD_SCHEMAS["ticket_create"],
            "title": item.description[:TITLE_MAX_LENGTH].strip(),
            "description": item.description,
            "assignee_text": item.assignee_text,
            **common,
        }
    assert record is not None
    return {
        "schema": PAYLOAD_SCHEMAS["crm_update"],
        "source_system": record.source_system,
        "record_type": record.record_type,
        "external_id": record.external_id,
        "note": item.description,
        **common,
    }


def _coaching_body(criterion_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    # P2-8.1: an owner the database cannot hold (``_is_id`` refuses it) is no owner, so
    # ``check_create`` answers ``422 review_assignee_ineligible`` at its own owner step,
    # after the visibility checks, and the value never reaches a query.
    owner = payload.get("owner_user_id")
    return {
        "criterion_id": criterion_id,
        "owner_user_id": owner if _is_id(owner) else None,
        "due_at": payload.get("due_at"),
        "instruction": payload.get("instruction"),
    }


def _build(
    db: Session,
    caller: ProposalCaller,
    *,
    action_type: str,
    action_item_id: Any,
    review_item_id: Any,
    coaching_body: dict[str, Any],
    target_system: Any,
    target_record_id: Any,
    now: datetime,
) -> Built:
    """SPEC 5.1 steps 2 to 5 against the current state, then the payload and digest."""
    if action_type in ITEM_TYPES:
        item, meeting = _visible_action_item(db, caller, action_item_id)
        if _status_value(item.status) not in ACTIONABLE_STATUSES:
            raise ActionProposalError(
                409, "action_item_not_actionable", "Only an open or in-progress action item"
            )
        pin = _pin(db, meeting, _item_revision_id(db, item))
        record = _target(
            db,
            action_type=action_type,
            workspace_id=caller.workspace_id,
            meeting_id=meeting.id,
            system=target_system,
            record_id=target_record_id,
        )
        return Built(
            meeting=meeting,
            action_item=item,
            review_item=None,
            criterion_id=None,
            pin=pin,
            target_system=target_system,
            target_record=record,
            payload=_item_payload(action_type, item, record),
            source_digest=item_digest(item),
        )
    try:
        checked = coaching_service.check_create(
            db, caller.coaching, item_id=review_item_id, body=coaching_body, now=now
        )
    except ReviewQueueError as error:
        raise _from_queue(error) from error
    review, meeting = checked.item, checked.meeting
    result_revision = db.scalar(
        select(ScorecardResultRecord.transcript_revision_id).where(
            ScorecardResultRecord.id == review.scorecard_result_id,
            ScorecardResultRecord.workspace_id == caller.workspace_id,
        )
    )
    pin = _pin(db, meeting, result_revision)
    payload = {
        "schema": PAYLOAD_SCHEMAS[COACHING_TYPE],
        "review_item_id": review.id,
        "criterion_id": checked.criterion_id,
        "agent_user_id": meeting.agent_user_id,
        "owner_user_id": checked.owner_user_id,
        "due_at": _iso(checked.due_at),
        "instruction": checked.instruction,
    }
    return Built(
        meeting=meeting,
        action_item=None,
        review_item=review,
        criterion_id=checked.criterion_id,
        pin=pin,
        target_system=None,
        target_record=None,
        payload=payload,
        source_digest=finding_digest(db, review, checked.criterion_id, meeting),
    )


# --- staleness (SPEC 5.4, P2-1.1) ---------------------------------------------------------


def stale_reason(db: Session, proposal: ActionProposal) -> str | None:
    """The first reason the pin no longer holds, or ``None``.  The order: source removed,
    revision changed, source not actionable, finding not failed, target invalid, source
    changed (the general digest change last)."""
    workspace_id = proposal.workspace_id
    item: ActionItem | None = None
    review: ReviewItem | None = None
    if proposal.source_type == SOURCE_ACTION_ITEM:
        if proposal.action_item_id is not None:
            item = db.scalar(
                select(ActionItem).where(
                    ActionItem.id == proposal.action_item_id,
                    ActionItem.workspace_id == workspace_id,
                )
            )
        if item is None:
            return "source_removed"
    else:
        if proposal.review_item_id is not None:
            review = db.scalar(
                select(ReviewItem).where(
                    ReviewItem.id == proposal.review_item_id,
                    ReviewItem.workspace_id == workspace_id,
                )
            )
        if review is None:
            return "source_removed"
    current = current_revision(db, workspace_id=workspace_id, meeting_id=proposal.meeting_id)
    if current is None or current.id != proposal.source_transcript_revision_id:
        return "revision_changed"
    if item is not None and _status_value(item.status) not in ACTIONABLE_STATUSES:
        return "source_not_actionable"
    if review is not None:
        outcomes, _ = coaching_service.final_outcomes(db, review)
        if outcomes.get(proposal.criterion_id or "") != "failed":
            return "finding_not_failed"
    if proposal.action_type in ITEM_TYPES:
        try:
            _target(
                db,
                action_type=proposal.action_type,
                workspace_id=workspace_id,
                meeting_id=proposal.meeting_id,
                system=proposal.target_system,
                record_id=proposal.target_external_record_id,
            )
        except ActionProposalError:
            return "target_invalid"
    if item is not None:
        digest = item_digest(item)
    else:
        assert review is not None
        meeting = db.scalar(select(Meeting).where(Meeting.id == proposal.meeting_id))
        assert meeting is not None
        digest = finding_digest(db, review, proposal.criterion_id or "", meeting)
    if digest != proposal.source_digest:
        return "source_changed"
    return None


# --- writes -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """A write's result: the proposal, its audit event and the event's details."""

    proposal: ActionProposal
    event: AuditEventType
    details: dict[str, Any] = field(default_factory=dict)


def _details(proposal: ActionProposal, **extra: Any) -> dict[str, Any]:
    """Ids, codes, hashes and versions only (SPEC 5.7), never payload text."""
    return {
        "proposal_id": proposal.id,
        "meeting_id": proposal.meeting_id,
        "action_type": proposal.action_type,
        "source_type": proposal.source_type,
        "payload_version": proposal.payload_version,
        "payload_hash": proposal.payload_hash,
        "revision": proposal.revision,
        **extra,
    }


def _denied(
    error: ActionProposalError, event: AuditEventType, proposal: ActionProposal, **extra: Any
) -> ActionProposalError:
    """Mark a refusal of a visible proposal for its ``denied`` audit row (409 or 422)."""
    if error.status_code in (409, 422):
        error.event = event
        error.denied = {
            "proposal_id": proposal.id,
            "meeting_id": proposal.meeting_id,
            "action_type": proposal.action_type,
            "source_type": proposal.source_type,
            "revision": proposal.revision,
            "reason_code": error.code,
            **extra,
        }
    return error


def _is_open_index(error: IntegrityError) -> bool:
    text = str(error.orig)
    return any(marker in text for marker in _OPEN_INDEX_MARKERS)


def create_proposal(
    db: Session, caller: ProposalCaller, *, body: dict[str, Any], now: datetime
) -> Outcome:
    """SPEC 5.1, in order; a refusal writes nothing."""
    action_type = _shape(body)
    built = _build(
        db,
        caller,
        action_type=action_type,
        action_item_id=body.get("action_item_id"),
        review_item_id=body.get("review_item_id"),
        coaching_body=_coaching_body(body.get("criterion_id"), body),
        target_system=body.get("target_system"),
        target_record_id=body.get("target_external_record_id"),
        now=now,
    )
    payload_hash = canonical_hash(built.payload)
    proposal = ActionProposal(
        id=new_id(),
        workspace_id=caller.workspace_id,
        meeting_id=built.meeting.id,
        action_type=action_type,
        source_type=SOURCE_ACTION_ITEM if action_type in ITEM_TYPES else SOURCE_FINDING,
        action_item_id=built.action_item.id if built.action_item is not None else None,
        review_item_id=built.review_item.id if built.review_item is not None else None,
        criterion_id=built.criterion_id,
        source_transcript_revision_id=built.pin,
        source_digest=built.source_digest,
        target_system=built.target_system,
        target_external_record_id=(
            built.target_record.id if built.target_record is not None else None
        ),
        payload_json=built.payload,
        payload_hash=payload_hash,
        payload_version=1,
        status="pending",
        revision=1,
        created_by_user_id=caller.user_id,
        created_at=now,
        updated_at=now,
    )
    savepoint = db.begin_nested()
    try:
        db.add(proposal)
        db.flush()
        savepoint.commit()
    except IntegrityError as error:
        savepoint.rollback()
        if _is_open_index(error):
            raise ActionProposalError(
                409,
                "action_proposal_exists",
                "The source already has an open proposal of this type",
            ) from None
        raise
    return Outcome(proposal, AuditEventType.ACTION_PROPOSAL_CREATED, _details(proposal))


def _check_open(
    caller: ProposalCaller,
    proposal: ActionProposal,
    meeting: Meeting,
    status: str,
    expected_revision: int,
) -> None:
    """The checks every write runs after visibility, in order: the call's agent or the
    coachee (P2-4.1, P2-6.3), then the status, then the revision."""
    # An owner or admin passes the review-queue rule on their own call, so they read a
    # coaching proposal about it; nobody writes one about their own call (ADR 0062).  The
    # coachee is the stored payload's agent, who stays the coachee after the call moves
    # to another agent (``coaching_service._is_party``).
    coachee = (proposal.payload_json or {}).get("agent_user_id")
    if (
        proposal.action_type == COACHING_TYPE
        and caller.user_id is not None
        and caller.user_id in (meeting.agent_user_id, coachee)
    ):
        raise ActionProposalError(
            422, "coaching_actor_is_agent", "Nobody coaches or reviews their own call"
        )
    if proposal.status != status:
        raise _not_pending() if status == "pending" else _not_approved()
    if proposal.revision != expected_revision:
        raise _revision_conflict(proposal.revision)


def refresh_proposal(
    db: Session,
    caller: ProposalCaller,
    *,
    proposal_id: str,
    expected_revision: int,
    now: datetime,
) -> Outcome:
    """SPEC 5.6: rebuild a pending proposal from the current source."""
    proposal, meeting = _load(db, caller, proposal_id, lock=True)
    try:
        _check_open(caller, proposal, meeting, "pending", expected_revision)
        built = _build(
            db,
            caller,
            action_type=proposal.action_type,
            action_item_id=proposal.action_item_id,
            review_item_id=proposal.review_item_id,
            coaching_body=_coaching_body(proposal.criterion_id, proposal.payload_json or {}),
            target_system=proposal.target_system,
            target_record_id=proposal.target_external_record_id,
            now=now,
        )
    except ActionProposalError as error:
        raise _denied(error, AuditEventType.ACTION_PROPOSAL_REFRESHED, proposal) from error
    proposal.payload_json = built.payload
    proposal.payload_hash = canonical_hash(built.payload)
    proposal.source_digest = built.source_digest
    proposal.source_transcript_revision_id = built.pin
    proposal.payload_version += 1
    proposal.revision += 1
    proposal.updated_at = now
    db.flush()
    return Outcome(proposal, AuditEventType.ACTION_PROPOSAL_REFRESHED, _details(proposal))


def _text(value: Any, name: str, max_length: int) -> str:
    stripped = value.strip() if isinstance(value, str) else ""
    # P2-7.1: the ``_stored_text`` rule of the coaching service, so a lone surrogate is a
    # 422 here and never a UnicodeEncodeError (a 500) in the payload hash.
    if not stripped or len(stripped) > max_length or "\x00" in stripped or not _encodable(stripped):
        raise _invalid(f"{name} must be 1 to {max_length} characters of text")
    return stripped


def _optional_due(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if not isinstance(value, str):
            raise ValueError
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise _invalid("due_at must carry a UTC offset")
        # P2-7.1: a date at the edge of the calendar overflows in UTC (OverflowError).
        return moment.astimezone(UTC).isoformat()
    except (ValueError, OverflowError):
        raise _invalid("due_at must be an ISO 8601 date-time with a UTC offset, or null") from None


def _edited_payload(
    db: Session,
    caller: ProposalCaller,
    proposal: ActionProposal,
    payload: Any,
    now: datetime,
) -> dict[str, Any]:
    """SPEC 5.2 edit rules: the same keys, only the editable values changed, each one
    valid as on create (the coaching checks for a coaching task)."""
    stored = dict(proposal.payload_json or {})
    if not isinstance(payload, dict):
        raise _invalid("payload must be an object")
    # P2-8.2: fixed messages.  An echoed client key could hold a lone surrogate, and the
    # error response could not be written (a 500 after the denied event).
    if set(payload) - set(stored):
        raise _invalid("payload has keys the proposal does not have")
    if set(stored) - set(payload):
        raise _invalid("payload misses keys of the proposal")
    editable = EDITABLE_FIELDS[proposal.action_type]
    for key in sorted(set(stored) - editable):
        if payload[key] != stored[key]:
            raise ActionProposalError(
                422, "action_proposal_field_not_editable", f"{key} cannot be edited"
            )
    result = dict(stored)

    # P2-7.3: a value equal to the stored one is not an edit.  It keeps the stored value
    # and skips the field checks; only a changed value is checked and normalized.
    def changed(key: str) -> bool:
        return payload[key] != stored[key]

    if proposal.action_type == "ticket_create":
        if changed("title"):
            result["title"] = _text(payload["title"], "title", TITLE_MAX_LENGTH)
        if changed("description"):
            result["description"] = _text(payload["description"], "description", TEXT_MAX_LENGTH)
        if changed("priority"):
            if payload["priority"] not in ACTION_PRIORITIES:
                raise _invalid(f"priority must be one of {', '.join(ACTION_PRIORITIES)}")
            result["priority"] = payload["priority"]
        if changed("due_at"):
            result["due_at"] = _optional_due(payload["due_at"])
    elif proposal.action_type == "crm_update":
        if changed("note"):
            result["note"] = _text(payload["note"], "note", TEXT_MAX_LENGTH)
        if changed("due_at"):
            result["due_at"] = _optional_due(payload["due_at"])
    else:
        try:
            checked = coaching_service.check_create(
                db,
                caller.coaching,
                item_id=proposal.review_item_id or "",
                body=_coaching_body(proposal.criterion_id, payload),
                now=now,
            )
        except ReviewQueueError as error:
            # P2-3.1: an edited instruction is an edited value like the others, so its
            # refusal is ``action_proposal_invalid``; the owner and the due date keep the
            # coaching codes, and create and refresh keep ``coaching_instruction_required``.
            # An unchanged instruction is not an edit (P2-4.2): it keeps the coaching code,
            # as on an approve with no payload.
            edited_instruction = payload.get("instruction") != stored.get("instruction")
            if error.code == INSTRUCTION_REQUIRED and edited_instruction:
                raise _invalid(str(error)) from error
            raise _from_queue(error) from error
        # The coaching checks above always run on the approved payload (P2-4.2); an
        # unchanged value still keeps the stored value, never a normalized copy (P2-7.3).
        for key, value in (
            ("owner_user_id", checked.owner_user_id),
            ("due_at", _iso(checked.due_at)),
            ("instruction", checked.instruction),
        ):
            if changed(key):
                result[key] = value
    return result


def approve_proposal(
    db: Session,
    caller: ProposalCaller,
    *,
    proposal_id: str,
    expected_revision: int,
    payload_hash: Any,
    source_transcript_revision_id: Any,
    payload: Any,
    now: datetime,
) -> Outcome:
    """SPEC 5.5: approve the exact payload and write its one delivery request."""
    proposal, meeting = _load(db, caller, proposal_id, lock=True)
    stale: str | None = None
    edited = False
    new_payload: dict[str, Any] = {}
    try:
        _check_open(caller, proposal, meeting, "pending", expected_revision)
        if payload_hash != proposal.payload_hash:
            raise ActionProposalError(
                409,
                "action_proposal_payload_changed",
                "The payload changed since it was read; reload and approve again",
            )
        if source_transcript_revision_id != proposal.source_transcript_revision_id:
            raise ActionProposalError(
                409, "action_proposal_pin_mismatch", "The pinned transcript revision differs"
            )
        stale = stale_reason(db, proposal)
        if stale is not None:
            raise ActionProposalError(
                409,
                "action_proposal_stale",
                "The source changed since the proposal was built; refresh it",
                {"reason": stale},
            )
        if payload is not None:
            new_payload = _edited_payload(db, caller, proposal, payload, now)
            if new_payload != proposal.payload_json:
                edited = True
        elif proposal.action_type == COACHING_TYPE:
            # P2-4.2: the coaching checks run on the payload being approved, so an approve
            # with no payload answers like one with the unchanged payload.  The stored
            # payload is approved as it is; only the checks run.
            _edited_payload(db, caller, proposal, dict(proposal.payload_json or {}), now)
    except ActionProposalError as error:
        extra = {"stale_reason": stale} if stale is not None else {}
        raise _denied(error, AuditEventType.ACTION_PROPOSAL_APPROVED, proposal, **extra) from error
    if edited:
        proposal.payload_json = new_payload
        proposal.payload_hash = canonical_hash(new_payload)
        proposal.payload_version += 1
    proposal.status = "approved"
    proposal.decided_at = now
    proposal.decided_by_user_id = caller.user_id
    proposal.revision += 1
    proposal.updated_at = now
    request = ActionDeliveryRequest(
        id=new_id(),
        workspace_id=proposal.workspace_id,
        meeting_id=proposal.meeting_id,
        proposal_id=proposal.id,
        action_type=proposal.action_type,
        target_system=proposal.target_system,
        target_external_record_id=proposal.target_external_record_id,
        payload_json=proposal.payload_json,
        payload_hash=proposal.payload_hash,
        payload_version=proposal.payload_version,
        source_type=proposal.source_type,
        source_id=proposal.action_item_id or proposal.review_item_id,
        source_criterion_id=proposal.criterion_id,
        source_transcript_revision_id=proposal.source_transcript_revision_id,
        source_digest=proposal.source_digest,
        idempotency_key=delivery_idempotency_key(
            proposal.workspace_id, proposal.id, proposal.payload_hash
        ),
        approved_by_user_id=caller.user_id,
        approved_at=now,
        created_at=now,
    )
    db.add(request)
    db.flush()
    return Outcome(
        proposal,
        AuditEventType.ACTION_PROPOSAL_APPROVED,
        _details(proposal, delivery_request_id=request.id, edited=edited),
    )


def reject_proposal(
    db: Session,
    caller: ProposalCaller,
    *,
    proposal_id: str,
    expected_revision: int,
    reason: Any,
    now: datetime,
) -> Outcome:
    """SPEC 5.6: a pending proposal is rejected with a reason of the list."""
    proposal, meeting = _load(db, caller, proposal_id, lock=True)
    try:
        _check_open(caller, proposal, meeting, "pending", expected_revision)
        if not isinstance(reason, str) or reason not in ACTION_PROPOSAL_REJECT_REASONS:
            raise _invalid(f"reason must be one of {', '.join(ACTION_PROPOSAL_REJECT_REASONS)}")
    except ActionProposalError as error:
        raise _denied(error, AuditEventType.ACTION_PROPOSAL_REJECTED, proposal) from error
    proposal.status = "rejected"
    proposal.decided_at = now
    proposal.decided_by_user_id = caller.user_id
    proposal.reject_reason = reason
    proposal.revision += 1
    proposal.updated_at = now
    db.flush()
    return Outcome(
        proposal, AuditEventType.ACTION_PROPOSAL_REJECTED, _details(proposal, reason=reason)
    )


def revoke_proposal(
    db: Session,
    caller: ProposalCaller,
    *,
    proposal_id: str,
    expected_revision: int,
    reason: Any,
    now: datetime,
) -> Outcome:
    """SPEC 5.6: an approved proposal is revoked; its delivery request is never touched."""
    proposal, meeting = _load(db, caller, proposal_id, lock=True)
    try:
        _check_open(caller, proposal, meeting, "approved", expected_revision)
        if not isinstance(reason, str) or reason not in ACTION_PROPOSAL_REVOKE_REASONS:
            raise _invalid(f"reason must be one of {', '.join(ACTION_PROPOSAL_REVOKE_REASONS)}")
    except ActionProposalError as error:
        raise _denied(error, AuditEventType.ACTION_PROPOSAL_REVOKED, proposal) from error
    proposal.status = "revoked"
    proposal.revoked_at = now
    proposal.revoked_by_user_id = caller.user_id
    proposal.revoke_reason = reason
    proposal.revision += 1
    proposal.updated_at = now
    db.flush()
    return Outcome(
        proposal, AuditEventType.ACTION_PROPOSAL_REVOKED, _details(proposal, reason=reason)
    )


# --- the list -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ListQuery:
    statuses: tuple[str, ...]
    action_type: str | None
    meeting_id: str | None
    limit: int
    cursor: tuple[datetime, str] | None


def _encode_cursor(when: datetime, row_id: str) -> str:
    payload = {"v": 1, "created_at": _iso(when), "id": row_id}
    raw = canonical_json(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    # P2-6.1: a real cursor is about 120 characters.  A longer one is refused before any
    # decoding, so deep JSON nesting never reaches ``json.loads`` (a RecursionError, a 500).
    if len(cursor) > MAX_CURSOR_LENGTH:
        raise _invalid("cursor is not a cursor of this list")
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(
            base64.b64decode(cursor + padding, altchars=b"-_", validate=True).decode("utf-8")
        )
        if not isinstance(payload, dict) or set(payload) != {"v", "created_at", "id"}:
            raise ValueError
        # The version is the JSON number 1 only (not ``true`` or ``1.0``, P2-4.3).
        if type(payload["v"]) is not int or payload["v"] != 1:
            raise ValueError
        if not isinstance(payload["created_at"], str):
            raise ValueError
        row_id = str(uuid.UUID(str(payload["id"])))
        moment = datetime.fromisoformat(payload["created_at"])
        if moment.tzinfo is None:
            raise ValueError
        # A date at the edge of the calendar overflows in UTC (OverflowError, P2-4.3).
        moment = moment.astimezone(UTC)
    except (
        ValueError,
        TypeError,
        OverflowError,
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
    ):
        raise _invalid("cursor is not a cursor of this list") from None
    return moment, row_id


def parse_list(
    *,
    statuses: list[str] | None,
    action_type: str | None,
    meeting_id: str | None,
    limit: str | None,
    cursor: str | None,
) -> ListQuery:
    """The list parameters, or ``422 action_proposal_invalid`` (P2-1.3)."""
    if statuses and any(status not in ACTION_PROPOSAL_STATUSES for status in statuses):
        raise _invalid(f"status must be one of {', '.join(ACTION_PROPOSAL_STATUSES)}")
    if action_type is not None and action_type not in ACTION_PROPOSAL_TYPES:
        raise _invalid(f"action_type must be one of {', '.join(ACTION_PROPOSAL_TYPES)}")
    if meeting_id is not None and not _is_id(meeting_id):
        raise _invalid("meeting_id is not a meeting id")
    size = DEFAULT_LIMIT
    if limit is not None:
        # P2-4.3: ASCII digits only.  ``str.isdigit`` and ``int`` also take other digits
        # (a superscript two, Persian digits), and ``int`` refuses a string of more than
        # 4300 digits with a ValueError, so the value is read only after this check.
        digits = limit.lstrip("0")
        if _DIGITS.fullmatch(limit) is None or len(digits) > len(str(MAX_LIMIT)):
            raise _invalid(f"limit must be a whole number from 1 to {MAX_LIMIT}")
        size = int(digits or "0")
        if not 1 <= size <= MAX_LIMIT:
            raise _invalid(f"limit must be a whole number from 1 to {MAX_LIMIT}")
    return ListQuery(
        statuses=tuple(dict.fromkeys(statuses)) if statuses else ACTION_PROPOSAL_STATUSES,
        action_type=action_type,
        meeting_id=meeting_id,
        limit=size,
        cursor=_decode_cursor(cursor) if cursor is not None else None,
    )


def list_proposals(
    db: Session, caller: ProposalCaller, *, query: ListQuery
) -> tuple[list[ActionProposal], str | None]:
    """The visible proposals, oldest first (``created_at`` then ``id``), one keyset page."""
    statement = _proposals(caller).where(ActionProposal.status.in_(query.statuses))
    if query.action_type is not None:
        statement = statement.where(ActionProposal.action_type == query.action_type)
    if query.meeting_id is not None:
        statement = statement.where(ActionProposal.meeting_id == query.meeting_id)
    if query.cursor is not None:
        moment, row_id = query.cursor
        statement = statement.where(
            or_(
                ActionProposal.created_at > moment,
                and_(ActionProposal.created_at == moment, ActionProposal.id > row_id),
            )
        )
    rows = db.execute(
        statement.order_by(ActionProposal.created_at.asc(), ActionProposal.id.asc()).limit(
            query.limit + 1
        )
    ).all()
    page = [row[0] for row in rows[: query.limit]]
    next_cursor = (
        _encode_cursor(page[-1].created_at, page[-1].id) if len(rows) > query.limit else None
    )
    return page, next_cursor


# --- views --------------------------------------------------------------------------------


def _evidence_ids(db: Session, proposals: list[ActionProposal]) -> dict[str, list[str]]:
    item_ids = sorted({p.action_item_id for p in proposals if p.action_item_id is not None})
    found: dict[str, list[str]] = {item_id: [] for item_id in item_ids}
    if not item_ids:
        return found
    rows = db.execute(
        select(EvidenceSpan.finding_id, EvidenceSpan.id)
        .where(
            EvidenceSpan.workspace_id == proposals[0].workspace_id,
            EvidenceSpan.finding_type == FINDING_ACTION_ITEM,
            EvidenceSpan.finding_id.in_(item_ids),
        )
        .order_by(EvidenceSpan.created_at.asc(), EvidenceSpan.id.asc())
    ).all()
    for finding_id, span_id in rows:
        found[finding_id].append(span_id)
    return found


def _requests(db: Session, proposals: list[ActionProposal]) -> dict[str, ActionDeliveryRequest]:
    ids = [proposal.id for proposal in proposals]
    if not ids:
        return {}
    rows = db.scalars(
        select(ActionDeliveryRequest).where(
            ActionDeliveryRequest.workspace_id == proposals[0].workspace_id,
            ActionDeliveryRequest.proposal_id.in_(ids),
        )
    ).all()
    return {row.proposal_id: row for row in rows}


def _request_view(request: ActionDeliveryRequest | None) -> dict[str, Any] | None:
    if request is None:
        return None
    return {
        "id": request.id,
        "idempotency_key": request.idempotency_key,
        "approved_by_user_id": request.approved_by_user_id,
        "approved_at": as_utc(request.approved_at),
    }


def _base_view(
    proposal: ActionProposal, evidence: list[str], request: ActionDeliveryRequest | None
) -> dict[str, Any]:
    return {
        "id": proposal.id,
        "meeting_id": proposal.meeting_id,
        "action_type": proposal.action_type,
        "source_type": proposal.source_type,
        "action_item_id": proposal.action_item_id,
        "review_item_id": proposal.review_item_id,
        "criterion_id": proposal.criterion_id,
        "target_system": proposal.target_system,
        "target_external_record_id": proposal.target_external_record_id,
        "payload_hash": proposal.payload_hash,
        "payload_version": proposal.payload_version,
        "source_transcript_revision_id": proposal.source_transcript_revision_id,
        "status": proposal.status,
        "revision": proposal.revision,
        "created_by_user_id": proposal.created_by_user_id,
        "created_at": as_utc(proposal.created_at),
        "updated_at": as_utc(proposal.updated_at),
        "decided_by_user_id": proposal.decided_by_user_id,
        "decided_at": as_utc(proposal.decided_at),
        "reject_reason": proposal.reject_reason,
        "revoked_by_user_id": proposal.revoked_by_user_id,
        "revoked_at": as_utc(proposal.revoked_at),
        "revoke_reason": proposal.revoke_reason,
        "evidence_span_ids": evidence,
        "delivery_request": _request_view(request),
    }


def proposal_view(db: Session, proposal: ActionProposal) -> dict[str, Any]:
    """The preview: the stored payload and, for a pending proposal, live staleness."""
    evidence = _evidence_ids(db, [proposal]).get(proposal.action_item_id or "", [])
    view = _base_view(proposal, evidence, _requests(db, [proposal]).get(proposal.id))
    view["payload"] = proposal.payload_json
    if proposal.status == "pending":
        reason = stale_reason(db, proposal)
        view["stale"] = {"is_stale": reason is not None, "reason": reason}
    else:
        view["stale"] = None
    return view


def list_views(db: Session, proposals: list[ActionProposal]) -> list[dict[str, Any]]:
    """The list rows: the same view without the payload and without staleness."""
    evidence = _evidence_ids(db, proposals)
    requests = _requests(db, proposals)
    return [
        _base_view(
            proposal,
            evidence.get(proposal.action_item_id or "", []),
            requests.get(proposal.id),
        )
        for proposal in proposals
    ]
