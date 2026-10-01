"""Post-transcription voicemail workflow.

Single entry point for ``jobs.processor``: :func:`on_transcript_ready`, called
after a job's success transaction committed (see the P0-E report for the
exact hook).  It is idempotent: re-running it for the same current transcript
revision and classifier version changes nothing, and at most one follow-up
``ActionItem`` and one urgent ``Notification`` ever exist per voicemail
(guarded by the row lock and the ``follow_up_action_id`` / ``notified_at``
columns).  It flushes; the caller commits.

:func:`on_processing_failed` records a terminal processing failure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.saas_models import (
    ActionItem,
    ActionStatus,
    Meeting,
    Notification,
    TranscriptRevision,
    TranscriptSegment,
)
from backend.services.audit_service import record_audit_event
from backend.voicemail.classify import (
    REPEAT_WINDOW_HOURS,
    ClassificationInput,
    PriorityContext,
    RuleClassifier,
    VoicemailClassifier,
    prioritize,
)
from backend.voicemail.models import (
    FollowUpState,
    VoicemailCategory,
    VoicemailMessage,
    VoicemailPriority,
    VoicemailStatus,
)

ACTION_CREATED_BY = "voicemail_rules"
NOTIFICATION_KIND = "voicemail.urgent"

# Categories whose voicemail always needs someone to act.
FOLLOW_UP_CATEGORIES = frozenset(
    {
        VoicemailCategory.COMPLAINT,
        VoicemailCategory.CALLBACK_REQUEST,
        VoicemailCategory.SUPPORT,
        VoicemailCategory.BILLING,
        VoicemailCategory.APPOINTMENT,
        VoicemailCategory.SALES_INQUIRY,
    }
)
# ... and any non-spam voicemail at these priorities.
FOLLOW_UP_PRIORITIES = frozenset({VoicemailPriority.URGENT, VoicemailPriority.HIGH})

_ACTION_PRIORITY = {
    VoicemailPriority.URGENT: "high",
    VoicemailPriority.HIGH: "high",
    VoicemailPriority.NORMAL: "medium",
    VoicemailPriority.LOW: "low",
}
_ACTION_TEXT = {
    VoicemailCategory.COMPLAINT: "Respond to the caller's complaint",
    VoicemailCategory.CALLBACK_REQUEST: "Call the caller back",
    VoicemailCategory.SUPPORT: "Follow up on the caller's support request",
    VoicemailCategory.BILLING: "Resolve the caller's billing question",
    VoicemailCategory.APPOINTMENT: "Arrange the requested appointment",
    VoicemailCategory.SALES_INQUIRY: "Answer the caller's sales inquiry",
    VoicemailCategory.OTHER: "Review and follow up on the voicemail",
}

# Hook for a workspace VIP list / CRM tag: (session, workspace_id, caller) -> bool.
VipLookup = Callable[[Session, str, str], bool]


@dataclass(frozen=True)
class PipelineOutcome:
    voicemail_id: str
    status: str
    changed: bool
    action_item_id: str | None = None
    notification_id: str | None = None


def needs_follow_up(category: VoicemailCategory, priority: VoicemailPriority) -> bool:
    if category in (VoicemailCategory.SPAM, VoicemailCategory.EMPTY):
        return False
    return category in FOLLOW_UP_CATEGORIES or priority in FOLLOW_UP_PRIORITIES


def _current_revision(db: Session, meeting: Meeting) -> TranscriptRevision | None:
    return db.scalar(
        select(TranscriptRevision).where(
            TranscriptRevision.meeting_id == meeting.id,
            TranscriptRevision.workspace_id == meeting.workspace_id,
            TranscriptRevision.is_current.is_(True),
        )
    )


def _recent_calls(db: Session, voicemail: VoicemailMessage) -> int:
    if not voicemail.caller_number:
        return 0
    window_start = voicemail.received_at - timedelta(hours=REPEAT_WINDOW_HOURS)
    return int(
        db.scalar(
            select(func.count(VoicemailMessage.id)).where(
                VoicemailMessage.workspace_id == voicemail.workspace_id,
                VoicemailMessage.caller_number == voicemail.caller_number,
                VoicemailMessage.id != voicemail.id,
                VoicemailMessage.received_at >= window_start,
                VoicemailMessage.received_at <= voicemail.received_at,
            )
        )
        or 0
    )


def on_transcript_ready(
    session: Session,
    meeting_id: str,
    *,
    classifier: VoicemailClassifier | None = None,
    vip_lookup: VipLookup | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> PipelineOutcome | None:
    """Classify, prioritise and route the voicemail behind ``meeting_id``.

    Returns ``None`` when the meeting is not a voicemail (the hook may call
    this for every completed job) or has no current transcript yet.
    """
    classifier = classifier or RuleClassifier()
    now = now or datetime.now(UTC)
    voicemail = session.scalar(
        select(VoicemailMessage).where(VoicemailMessage.meeting_id == meeting_id).with_for_update()
    )
    if voicemail is None:
        return None
    meeting = session.get(Meeting, meeting_id)
    if meeting is None or meeting.deleted_at is not None:
        return None
    revision = _current_revision(session, meeting)
    if revision is None:
        return None

    if (
        voicemail.classified_revision_id == revision.id
        and voicemail.classifier_version == classifier.version
        and voicemail.status
        in (VoicemailStatus.CLASSIFIED.value, VoicemailStatus.NEEDS_REVIEW.value)
    ):
        return PipelineOutcome(
            voicemail.id,
            voicemail.status,
            changed=False,
            action_item_id=voicemail.follow_up_action_id,
        )

    segments = list(
        session.scalars(
            select(TranscriptSegment)
            .where(
                TranscriptSegment.revision_id == revision.id,
                TranscriptSegment.workspace_id == meeting.workspace_id,
            )
            .order_by(TranscriptSegment.sequence)
        )
    )
    text = " ".join(segment.text for segment in segments).strip()
    voicemail.status = VoicemailStatus.TRANSCRIBED.value
    result = classifier.classify(ClassificationInput(text=text, language=revision.language))
    context = PriorityContext(
        recent_calls_from_caller=_recent_calls(session, voicemail),
        caller_is_vip=bool(
            vip_lookup
            and voicemail.caller_number
            and vip_lookup(session, voicemail.workspace_id, voicemail.caller_number)
        ),
    )
    priority = prioritize(text, result, context)

    voicemail.category = result.category.value
    voicemail.intent = result.intent
    voicemail.topics = list(result.topics)
    voicemail.classification_confidence = result.confidence
    voicemail.priority = priority.priority.value
    voicemail.priority_score = priority.score
    voicemail.priority_reasons = [dict(reason) for reason in priority.reasons]
    voicemail.classifier_kind = classifier.kind
    voicemail.classifier_version = classifier.version
    voicemail.classifier_model_id = classifier.model_id
    voicemail.classified_revision_id = revision.id
    voicemail.classified_at = now
    voicemail.status = (
        VoicemailStatus.NEEDS_REVIEW.value
        if result.needs_review
        else VoicemailStatus.CLASSIFIED.value
    )
    voicemail.review_reason = result.review_reason

    action_id = voicemail.follow_up_action_id
    if (
        not result.needs_review
        and needs_follow_up(result.category, priority.priority)
        and voicemail.follow_up_action_id is None
        and voicemail.follow_up_state in (FollowUpState.NONE.value, FollowUpState.PENDING.value)
    ):
        existing = session.scalar(
            select(ActionItem.id).where(
                ActionItem.workspace_id == voicemail.workspace_id,
                ActionItem.meeting_id == meeting.id,
                ActionItem.created_by == ACTION_CREATED_BY,
            )
        )
        if existing is None:
            action = ActionItem(
                workspace_id=voicemail.workspace_id,
                meeting_id=meeting.id,
                description=_ACTION_TEXT.get(
                    result.category, _ACTION_TEXT[VoicemailCategory.OTHER]
                ),
                assignee_user_id=voicemail.assigned_user_id,
                priority=_ACTION_PRIORITY[priority.priority],
                status=ActionStatus.OPEN,
                source_segment_id=segments[0].id if segments else None,
                source_timestamp_ms=segments[0].start_ms if segments else None,
                created_by=ACTION_CREATED_BY,
            )
            session.add(action)
            session.flush()
            existing = action.id
        action_id = existing
        voicemail.follow_up_action_id = action_id
        voicemail.follow_up_state = FollowUpState.ACTION_CREATED.value

    notification_id = None
    if priority.priority is VoicemailPriority.URGENT and voicemail.notified_at is None:
        recipient = (
            voicemail.assigned_user_id or meeting.agent_user_id or meeting.created_by_user_id
        )
        notification = Notification(
            workspace_id=voicemail.workspace_id,
            user_id=recipient,
            kind=NOTIFICATION_KIND,
            title="Urgent voicemail",
            body=f"An urgent {result.category.value.replace('_', ' ')} voicemail needs attention.",
            data={
                "voicemail_id": voicemail.id,
                "meeting_id": meeting.id,
                "priority_score": priority.score,
                "reasons": [reason["code"] for reason in priority.reasons],
            },
        )
        session.add(notification)
        session.flush()
        notification_id = notification.id
        voicemail.notified_at = now

    record_audit_event(
        session,
        event_type="voicemail.classified",
        workspace_id=voicemail.workspace_id,
        target_type="voicemail",
        target_id=voicemail.id,
        details={
            "meeting_id": meeting.id,
            "revision_id": revision.id,
            "category": voicemail.category,
            "priority": voicemail.priority,
            "priority_score": voicemail.priority_score,
            "classifier_kind": classifier.kind,
            "classifier_version": classifier.version,
            "classifier_model_id": classifier.model_id,
            "action_item_id": action_id,
            "notification_id": notification_id,
        },
        settings=settings,
    )
    session.flush()
    return PipelineOutcome(
        voicemail.id,
        voicemail.status,
        changed=True,
        action_item_id=action_id,
        notification_id=notification_id,
    )


def on_processing_failed(
    session: Session, meeting_id: str, *, reason: str | None = None
) -> PipelineOutcome | None:
    voicemail = session.scalar(
        select(VoicemailMessage).where(VoicemailMessage.meeting_id == meeting_id).with_for_update()
    )
    if voicemail is None or voicemail.status == VoicemailStatus.FAILED.value:
        return None
    voicemail.status = VoicemailStatus.FAILED.value
    voicemail.review_reason = (reason or "processing_failed")[:60]
    session.flush()
    return PipelineOutcome(voicemail.id, voicemail.status, changed=True)
