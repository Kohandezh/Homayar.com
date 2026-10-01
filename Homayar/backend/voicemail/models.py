"""The voicemail side table (one row per voicemail meeting).

The conversation itself (audio, recording, processing job, transcript
revisions and segments) lives in the shared tables; this row only carries the
telephony envelope (mailbox, caller, external message id), the classification
result with its provenance, and the follow-up workflow state.

No migration exists yet: the integrator owns it (see the P0-E report).  The
table registers on the shared ``Base.metadata`` so test/development
``create_all`` builds it once this module is imported.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.saas_models import Base, IngestionSourceType, TimestampMixin, new_id

# The capture source every voicemail ingestion is recorded under.  Until the
# integrator adds ``IngestionSourceType.VOICEMAIL`` this is the telephony
# value; switching it is a one-line change here (plus the enum/migration).
VOICEMAIL_SOURCE_TYPE: IngestionSourceType = getattr(
    IngestionSourceType, "VOICEMAIL", IngestionSourceType.SIP_VOIP
)
VOICEMAIL_SOURCE_PROVIDER = "voicemail"
# ``meetings.source`` of a voicemail conversation (String(30), free text).
VOICEMAIL_MEETING_SOURCE = "voicemail"


class VoicemailStatus(str, enum.Enum):
    RECEIVED = "received"  # ingested, processing job queued
    PROCESSING = "processing"  # reported while the meeting's job runs
    TRANSCRIBED = "transcribed"  # a current transcript revision exists
    CLASSIFIED = "classified"  # classification + priority recorded
    NEEDS_REVIEW = "needs_review"  # empty / no speech: nothing to classify
    FAILED = "failed"  # processing failed terminally


class VoicemailCategory(str, enum.Enum):
    COMPLAINT = "complaint"
    SALES_INQUIRY = "sales_inquiry"
    SUPPORT = "support"
    APPOINTMENT = "appointment"
    CALLBACK_REQUEST = "callback_request"
    BILLING = "billing"
    SPAM = "spam"
    EMPTY = "empty"
    OTHER = "other"


class VoicemailPriority(str, enum.Enum):
    URGENT = "urgent"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class FollowUpState(str, enum.Enum):
    NONE = "none"
    PENDING = "pending"
    ACTION_CREATED = "action_created"
    DONE = "done"


def _in(column: str, values: type[enum.Enum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class VoicemailMessage(Base, TimestampMixin):
    __tablename__ = "voicemail_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    ingestion_id: Mapped[str | None] = mapped_column(
        ForeignKey("ingestion_records.id", ondelete="SET NULL")
    )
    # Telephony envelope.
    external_message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    mailbox: Mapped[str | None] = mapped_column(String(80))
    extension: Mapped[str | None] = mapped_column(String(40))
    caller_number: Mapped[str | None] = mapped_column(String(32))  # normalized
    caller_number_valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), default=VoicemailStatus.RECEIVED.value, nullable=False
    )
    review_reason: Mapped[str | None] = mapped_column(String(60))

    # Classification (null until classified; never invented).
    category: Mapped[str | None] = mapped_column(String(24))
    intent: Mapped[str | None] = mapped_column(String(40))
    topics: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    priority: Mapped[str | None] = mapped_column(String(8))
    priority_score: Mapped[int | None] = mapped_column(Integer)
    priority_reasons: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    classification_confidence: Mapped[float | None] = mapped_column(Float)
    # Provenance: which classifier produced the result, over which revision.
    classifier_kind: Mapped[str | None] = mapped_column(String(20))  # rules | llm
    classifier_version: Mapped[str | None] = mapped_column(String(60))
    classifier_model_id: Mapped[str | None] = mapped_column(String(256))
    classified_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("transcript_revisions.id", ondelete="SET NULL")
    )
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Follow-up workflow.
    follow_up_state: Mapped[str] = mapped_column(
        String(16), default=FollowUpState.NONE.value, nullable=False
    )
    follow_up_action_id: Mapped[str | None] = mapped_column(
        ForeignKey("action_items.id", ondelete="SET NULL")
    )
    follow_up_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_team_id: Mapped[str | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL")
    )
    assigned_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        UniqueConstraint("meeting_id", name="uq_voicemail_meeting"),
        UniqueConstraint(
            "workspace_id", "external_message_id", name="uq_voicemail_external_message"
        ),
        CheckConstraint(_in("status", VoicemailStatus), name="ck_voicemail_status"),
        CheckConstraint(
            "category IS NULL OR " + _in("category", VoicemailCategory),
            name="ck_voicemail_category",
        ),
        CheckConstraint(
            "priority IS NULL OR " + _in("priority", VoicemailPriority),
            name="ck_voicemail_priority",
        ),
        CheckConstraint(
            "priority_score IS NULL OR (priority_score >= 0 AND priority_score <= 100)",
            name="ck_voicemail_priority_score",
        ),
        CheckConstraint(_in("follow_up_state", FollowUpState), name="ck_voicemail_follow_up"),
        CheckConstraint("duration_seconds >= 0", name="ck_voicemail_duration"),
        Index("ix_voicemail_workspace_received", "workspace_id", "received_at"),
        Index("ix_voicemail_workspace_status", "workspace_id", "status"),
        Index("ix_voicemail_workspace_priority", "workspace_id", "priority", "received_at"),
        Index("ix_voicemail_workspace_category", "workspace_id", "category"),
        Index("ix_voicemail_workspace_caller", "workspace_id", "caller_number", "received_at"),
        Index("ix_voicemail_workspace_follow_up", "workspace_id", "follow_up_state"),
        Index("ix_voicemail_assigned_user", "assigned_user_id"),
    )
