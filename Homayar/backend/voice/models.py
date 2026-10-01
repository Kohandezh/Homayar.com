"""Voice call runtime control plane tables (AppVision X21, V02/V05).

Two kinds of tables live here:

* Tenant tables (``workspace_id`` is the tenant key, RLS-eligible):
  ``voice_call_sessions`` and ``voice_workspace_limits``.
* Deployment-scoped control-plane tables (no tenant; admission must see the
  whole deployment to enforce the global and per-node limits):
  ``voice_media_nodes``, ``voice_capacity_slots`` (carries ``workspace_id`` as
  data for per-workspace counting, but must NOT be filtered by tenant RLS) and
  ``voice_admission_locks``.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.saas_models import Base, TimestampMixin, new_id, utcnow


class CallDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    BROWSER = "browser"


class CallChannel(str, enum.Enum):
    SIP = "sip"
    WEBRTC = "webrtc"
    PBX = "pbx"


class CallState(str, enum.Enum):
    ADMITTED = "admitted"
    RINGING = "ringing"
    ACTIVE = "active"
    ENDED = "ended"
    FAILED = "failed"
    REJECTED = "rejected"


TERMINAL_STATES = frozenset({CallState.ENDED, CallState.FAILED, CallState.REJECTED})
LIVE_STATES = frozenset({CallState.ADMITTED, CallState.RINGING, CallState.ACTIVE})


def _in_check(column: str, members: type[enum.Enum]) -> str:
    return f"{column} IN (" + ", ".join(f"'{m.name}'" for m in members) + ")"


class VoiceMediaNode(Base, TimestampMixin):
    """A media worker able to carry calls. Deployment-scoped (no tenant)."""

    __tablename__ = "voice_media_nodes"
    __table_args__ = (
        CheckConstraint("max_concurrent >= 0", name="ck_voice_media_nodes_max_concurrent"),
        CheckConstraint("generation >= 1", name="ck_voice_media_nodes_generation"),
        Index("ix_voice_media_nodes_health", "draining", "heartbeat_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    max_concurrent: Mapped[int] = mapped_column(Integer, nullable=False)
    draining: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # Fencing token: incremented whenever the node is declared lost; every
    # node-originated write must present the current value.
    generation: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    lost_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VoiceAdmissionLock(Base):
    """Single-row serialization point for admission (row lock on PostgreSQL,
    write lock on SQLite). ``id`` is the deployment profile name."""

    __tablename__ = "voice_admission_locks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)


class VoiceWorkspaceLimit(Base, TimestampMixin):
    """Per-workspace concurrent call limit (tenant table)."""

    __tablename__ = "voice_workspace_limits"
    __table_args__ = (
        CheckConstraint(
            "max_concurrent_calls >= 0", name="ck_voice_workspace_limits_max_concurrent"
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    max_concurrent_calls: Mapped[int] = mapped_column(Integer, nullable=False)


class VoiceCallSession(Base, TimestampMixin):
    """One call through the runtime (tenant table)."""

    __tablename__ = "voice_call_sessions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "external_call_id", name="uq_voice_call_sessions_external_call"
        ),
        CheckConstraint(_in_check("state", CallState), name="ck_voice_call_sessions_state"),
        CheckConstraint(
            _in_check("direction", CallDirection), name="ck_voice_call_sessions_direction"
        ),
        CheckConstraint(_in_check("channel", CallChannel), name="ck_voice_call_sessions_channel"),
        # A live call always has a node; a rejected call never has one.
        CheckConstraint(
            "state NOT IN ('ADMITTED', 'RINGING', 'ACTIVE') OR node_id IS NOT NULL",
            name="ck_voice_call_sessions_live_has_node",
        ),
        CheckConstraint(
            "state <> 'REJECTED' OR node_id IS NULL", name="ck_voice_call_sessions_rejected_no_node"
        ),
        CheckConstraint(
            "state NOT IN ('ENDED', 'FAILED', 'REJECTED') OR ended_at IS NOT NULL",
            name="ck_voice_call_sessions_terminal_has_end",
        ),
        Index("ix_voice_call_sessions_workspace_state", "workspace_id", "state"),
        Index("ix_voice_call_sessions_node_state", "node_id", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    external_call_id: Mapped[str] = mapped_column(String(255), nullable=False)
    direction: Mapped[CallDirection] = mapped_column(
        Enum(CallDirection, native_enum=False, length=16), nullable=False
    )
    channel: Mapped[CallChannel] = mapped_column(
        Enum(CallChannel, native_enum=False, length=16), nullable=False
    )
    state: Mapped[CallState] = mapped_column(
        Enum(CallState, native_enum=False, length=16), nullable=False
    )
    node_id: Mapped[str | None] = mapped_column(
        ForeignKey("voice_media_nodes.id", ondelete="RESTRICT")
    )
    # Node generation at admission (fencing token held by the media worker).
    node_generation: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(16))
    rejection_reason: Mapped[str | None] = mapped_column(String(32))
    end_reason: Mapped[str | None] = mapped_column(String(64))
    admitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # ringing
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VoiceCapacitySlot(Base):
    """One held unit of capacity. Deployment-scoped: counted across tenants.

    ``released_at IS NULL`` means the slot is held. ``session_id`` is unique,
    so a call can never hold two slots.
    """

    __tablename__ = "voice_capacity_slots"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_voice_capacity_slots_session"),
        CheckConstraint("generation >= 1", name="ck_voice_capacity_slots_generation"),
        Index("ix_voice_capacity_slots_node_held", "node_id", "released_at"),
        Index("ix_voice_capacity_slots_workspace_held", "workspace_id", "released_at"),
        Index("ix_voice_capacity_slots_held_expiry", "released_at", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("voice_call_sessions.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(
        ForeignKey("voice_media_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(String(64))


VOICE_TABLES = (
    VoiceMediaNode.__table__,
    VoiceAdmissionLock.__table__,
    VoiceWorkspaceLimit.__table__,
    VoiceCallSession.__table__,
    VoiceCapacitySlot.__table__,
)
