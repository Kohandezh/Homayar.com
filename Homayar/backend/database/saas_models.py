from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class IdentityProvider(str, enum.Enum):
    PASSWORD = "password"  # noqa: S105 - identity provider label, not a credential
    GOOGLE = "google"
    PHONE = "phone"
    OIDC = "oidc"
    LDAP = "ldap"


class WorkspaceRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class OrganizationRole(str, enum.Enum):
    """Organization-level roles (WP03-T03, ADR 0012).

    They administer the organization and see what was explicitly granted;
    they never confer workspace content access by themselves.
    """

    OWNER = "owner"
    ADMIN = "admin"
    ANALYST = "analyst"
    EXECUTIVE = "executive"


class RecordScope(str, enum.Enum):
    """How far a member/viewer sees inside a workspace (owners/admins see all)."""

    WORKSPACE = "workspace"  # flat workspace: every member sees every record
    TEAM = "team"  # supervisors see their teams, agents see their own records


class TeamRole(str, enum.Enum):
    SUPERVISOR = "supervisor"
    AGENT = "agent"


class ShareAudience(str, enum.Enum):
    """Who may open a share link (WP03-T06)."""

    LINK = "link"  # anyone holding the link; only when the workspace allows public links
    WORKSPACE = "workspace"  # current effective members of the source workspace
    RECIPIENTS = "recipients"  # named recipients who prove ownership of a verified email/phone
    # current members of the organization the source workspace belongs to
    # (workspace membership in any of its workspaces, or organization membership)
    ORGANIZATION = "organization"


class SubscriptionStatus(str, enum.Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ReservationState(str, enum.Enum):
    RESERVED = "reserved"
    COMMITTED = "committed"
    RELEASED = "released"


class MeetingStatus(str, enum.Enum):
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    TRANSCRIBING = "transcribing"
    DIARIZING = "diarizing"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ConversationType(str, enum.Enum):
    """What kind of conversation a ``meetings`` row holds (HOM-58, WP12-T01).

    A conversation is a row of ``meetings``.  The table keeps its name, so every
    id, foreign key and API stays.  Stored as the lowercase value with a check
    constraint.
    """

    CALL = "call"
    MEETING = "meeting"
    VOICEMAIL = "voicemail"
    OTHER = "other"


class ConversationDirection(str, enum.Enum):
    """Who started the conversation, seen from the workspace (HOM-58).

    A NULL column means the direction is not known.  Stored as the lowercase
    value with a check constraint.
    """

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    INTERNAL = "internal"


CONVERSATION_TYPE_CHECK = (
    "conversation_type IN (" + ", ".join(f"'{member.value}'" for member in ConversationType) + ")"
)
CONVERSATION_DIRECTION_CHECK = (
    "direction IS NULL OR direction IN ("
    + ", ".join(f"'{member.value}'" for member in ConversationDirection)
    + ")"
)
CONVERSATION_EXTERNAL_REF_CHECK = (
    "(external_source_system IS NULL AND external_conversation_id IS NULL) OR "
    "(external_source_system IS NOT NULL AND external_conversation_id IS NOT NULL)"
)


class JobState(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


class DispatchState(str, enum.Enum):
    """Transactional-outbox state of a job's broker publication (WP06-T02)."""

    PENDING = "pending"  # committed with the job; not yet acknowledged by the broker
    PUBLISHED = "published"  # broker accepted the message at least once
    FAILED = "failed"  # publication attempts exhausted; needs operator attention
    CANCELLED = "cancelled"  # job cancelled/deleted before publication


class StageState(str, enum.Enum):
    """Truthful per-stage outcome of one processing attempt (WP04-T01)."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    SKIPPED = "skipped"


class CheckpointState(str, enum.Enum):
    """Lifecycle of one stage checkpoint / artifact (WP06-T03).

    ``PENDING``: the attempt declared the artifact key before writing the
    object (so an orphan is discoverable); ``PUBLISHED``: the fenced
    transaction accepted the verified artifact as the authoritative result of
    that stage for those inputs; ``ABANDONED``: never published (stale
    attempt, failed write, empty result); ``INVALIDATED``: was published but
    may not be reused any more (artifact missing/corrupt, meeting deleted,
    superseded by a correction).
    """

    PENDING = "pending"
    PUBLISHED = "published"
    ABANDONED = "abandoned"
    INVALIDATED = "invalidated"


class ActionStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class TranscriptSource(str, enum.Enum):
    """Who produced a transcript revision (WP04-T02)."""

    ASR = "asr"  # the processing pipeline (speech recognition + speaker mapping)
    CORRECTION = "correction"  # a person edited a parent revision
    BACKFILL = "backfill"  # rows that existed before revisions were introduced


class EvidenceStatus(str, enum.Enum):
    """How well a finding is grounded in the transcript it was made from (WP04-T03).

    ``SUFFICIENT``: every reference resolved to a segment of the analysed
    revision; ``PARTIAL``: some references were rejected, at least one
    resolved; ``INSUFFICIENT``: nothing resolved (or nothing was offered).
    Stored as the lowercase value with a check constraint.
    """

    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


EVIDENCE_STATUS_CHECK = "evidence_status IN ('sufficient', 'partial', 'insufficient')"


class IngestionSourceType(str, enum.Enum):
    """How audio enters Homayar (WP05-T01): the capture source of an ingestion.

    Every capture provider (desktop, meeting bot, telephony, upload, watcher,
    API) reports one of these; the adapter that delivered the event is the
    ``source_provider`` string next to it.  Only ``FILE_UPLOAD`` has a
    producer today; the others are the canonical vocabulary later providers
    must use instead of inventing their own ledger.
    """

    FILE_UPLOAD = "file_upload"
    MICROPHONE = "microphone"
    SYSTEM_AUDIO = "system_audio"
    MIC_PLUS_SYSTEM_AUDIO = "mic_plus_system_audio"
    SIP_VOIP = "sip_voip"
    MEETING_BOT = "meeting_bot"
    EXTERNAL_RECORDING_URL = "external_recording_url"
    OFFLINE_FOLDER_WATCHER = "offline_folder_watcher"
    API = "api"
    VOICEMAIL = "voicemail"


class IngestionStatus(str, enum.Enum):
    """Outcome recorded on an ingestion row (WP05-T01).

    ``ACCEPTED``: the event created (or, for a backfilled row, corresponds
    to) exactly one conversation and one root processing job.  ``REJECTED``
    is reserved for WP05-T02, which records payload-inherent validation
    failures; nothing writes it yet.
    """

    ACCEPTED = "accepted"
    REJECTED = "rejected"


class DuplicateMediaPolicy(str, enum.Enum):
    """Workspace policy for a *new* event whose media already exists (case C).

    ``ALLOW``: accept, link the new ingestion to the earlier one and audit
    it (the workspace knowingly pays for processing the same audio again);
    ``REJECT``: refuse with ``409 duplicate_media`` naming the earlier
    ingestion, create nothing and audit the refusal.
    """

    ALLOW = "allow"
    REJECT = "reject"


# Non-native enums store the member *name* (project convention); the check
# constraints below pin that vocabulary in the database as well.
INGESTION_SOURCE_TYPE_CHECK = (
    "source_type IN (" + ", ".join(f"'{member.name}'" for member in IngestionSourceType) + ")"
)
INGESTION_STATUS_CHECK = "status IN ('ACCEPTED', 'REJECTED')"
DUPLICATE_MEDIA_POLICY_CHECK = "duplicate_media_policy IN ('ALLOW', 'REJECT')"


class RetentionMode(str, enum.Enum):
    """Explicit retention semantics of a workspace (WP07-T01, ADR 0020).

    ``FINITE``: conversations become deletion-eligible ``retention_days``
    (>= 1) after ingestion.  ``INDEFINITE``: never eligible by age; only an
    explicit deletion removes data.  ``IMMEDIATE``: eligible as soon as
    processing reaches a terminal state.  ``LEGACY_UNRESOLVED``: the
    pre-WP07 ambiguous ``retention_days = 0`` value migrated as-is; nothing
    is purged by age until an administrator chooses an explicit mode.  It
    can never be *set* through the API, only left behind by the migration.
    """

    INDEFINITE = "indefinite"
    FINITE = "finite"
    IMMEDIATE = "immediate"
    LEGACY_UNRESOLVED = "legacy_unresolved"


RETENTION_MODE_CHECK = (
    "(retention_mode = 'FINITE' AND retention_days >= 1) OR "
    "(retention_mode IN ('INDEFINITE', 'IMMEDIATE', 'LEGACY_UNRESOLVED') AND retention_days = 0)"
)


class MediaKind(str, enum.Enum):
    """What a stored byte stream is (WP05-T03, ADR 0017)."""

    ORIGINAL = "original"
    NORMALIZED = "normalized"
    ENHANCED = "enhanced"
    CHANNEL_DERIVED = "channel_derived"
    CLIP = "clip"
    EXPORT = "export"


class MediaLifecycleState(str, enum.Enum):
    ACTIVE = "active"
    RETENTION_PENDING = "retention_pending"
    DELETION_PENDING = "deletion_pending"
    DELETED = "deleted"
    LEGAL_HOLD = "legal_hold"


class MediaIntegrity(str, enum.Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    MISSING = "missing"
    CORRUPT = "corrupt"


MEDIA_KIND_CHECK = "kind IN (" + ", ".join(f"'{member.name}'" for member in MediaKind) + ")"
MEDIA_STATE_CHECK = (
    "lifecycle_state IN (" + ", ".join(f"'{member.name}'" for member in MediaLifecycleState) + ")"
)
MEDIA_INTEGRITY_CHECK = (
    "integrity IN (" + ", ".join(f"'{member.name}'" for member in MediaIntegrity) + ")"
)
MEDIA_BACKEND_CHECK = "storage_backend IN ('local', 's3')"


# Every class the WP07-T05 detector supports (backend.privacy.text_pii.PiiClass
# values); kept here so the model does not import the detector.
DEFAULT_PII_CLASSES: tuple[str, ...] = (
    "bank_card",
    "email",
    "iban",
    "ir_national_id",
    "ir_postal_code",
    "phone",
)


class PurgeState(str, enum.Enum):
    """Durable purge of a tombstoned conversation (WP07-T03, ADR 0021).

    ``PENDING``: work remains (content rows, bytes, or a hold defers it).
    ``COMPLETED``: every store is clean; the row is the non-content receipt.
    ``FAILED``: the attempt budget ran out; an operator requeues it.
    """

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


PURGE_STATE_CHECK = "state IN (" + ", ".join(f"'{member.name}'" for member in PurgeState) + ")"


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True)
    phone_e164: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(512))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    system_role: Mapped[str] = mapped_column(String(24), default="user", nullable=False)
    locale: Mapped[str] = mapped_column(String(10), default="fa", nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    identities: Mapped[list[AuthIdentity]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("email IS NOT NULL OR phone_e164 IS NOT NULL", name="ck_user_identity"),
        CheckConstraint("system_role IN ('user','operator')", name="ck_user_system_role"),
    )


class AuthIdentity(Base, TimestampMixin):
    __tablename__ = "auth_identities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[IdentityProvider] = mapped_column(
        Enum(IdentityProvider, native_enum=False, length=20), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(320), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    user: Mapped[User] = relationship(back_populates="identities")

    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_identity_provider_subject"),
        UniqueConstraint("user_id", "provider", name="uq_user_identity_provider"),
    )


class LoginSession(Base, TimestampMixin):
    __tablename__ = "login_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent_hash: Mapped[str | None] = mapped_column(String(64))
    ip_prefix: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_from_id: Mapped[str | None] = mapped_column(
        ForeignKey("login_sessions.id", ondelete="SET NULL")
    )

    __table_args__ = (Index("ix_sessions_user_active", "user_id", "revoked_at"),)


class OneTimeToken(Base):
    __tablename__ = "one_time_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (Index("ix_one_time_user_purpose", "user_id", "purpose"),)


class OAuthChallenge(Base):
    __tablename__ = "oauth_challenges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    nonce_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(160), nullable=False)
    intent: Mapped[str] = mapped_column(String(16), nullable=False, default="login")
    link_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    redirect_path: Mapped[str] = mapped_column(String(240), default="/app", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class OtpChallenge(Base):
    __tablename__ = "otp_challenges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    phone_e164: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), default="login", nullable=False)
    link_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint("attempts >= 0 AND attempts <= max_attempts", name="ck_otp_attempts"),
        Index("ix_otp_phone_created", "phone_e164", "created_at"),
    )


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)

    workspaces: Mapped[list[Workspace]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    record_scope: Mapped[RecordScope] = mapped_column(
        Enum(RecordScope, native_enum=False, length=16),
        default=RecordScope.WORKSPACE,
        server_default="WORKSPACE",
        nullable=False,
    )
    retention_days: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    # Explicit retention semantics (WP07-T01): ``retention_days`` only has a
    # meaning under ``FINITE``; the other modes keep it at 0.
    retention_mode: Mapped[RetentionMode] = mapped_column(
        Enum(RetentionMode, native_enum=False, length=20),
        default=RetentionMode.FINITE,
        server_default="FINITE",
        nullable=False,
    )
    delete_audio_after_processing: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # Anonymous "anyone with the link" shares are off until an admin opts in.
    public_share_links_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # "Current members of our organization" shares are on by default; switching
    # them off hides existing organization shares at once (ADR 0006, decision 6).
    organization_share_links_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    # Case C of the ingestion ledger (WP05-T01): what to do when a new event
    # carries media the workspace already ingested under another event.
    duplicate_media_policy: Mapped[DuplicateMediaPolicy] = mapped_column(
        Enum(DuplicateMediaPolicy, native_enum=False, length=16),
        default=DuplicateMediaPolicy.ALLOW,
        server_default="ALLOW",
        nullable=False,
    )
    # Text privacy (WP07-T05, ADR 0023): whether derived text is produced in
    # a redacted version and which detector classes that version masks.
    pii_redaction_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    pii_redaction_classes: Mapped[list[str]] = mapped_column(
        JSON, default=lambda: list(DEFAULT_PII_CLASSES), nullable=False
    )
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="workspaces")
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_workspace_org_slug"),
        CheckConstraint("retention_days >= 0", name="ck_workspace_retention"),
        CheckConstraint(RETENTION_MODE_CHECK, name="ck_workspace_retention_mode"),
        CheckConstraint(DUPLICATE_MEDIA_POLICY_CHECK, name="ck_workspace_duplicate_media_policy"),
    )


class Membership(Base, TimestampMixin):
    __tablename__ = "memberships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        Enum(WorkspaceRole, native_enum=False, length=16), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    workspace: Mapped[Workspace] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_membership_workspace_user"),
    )


class Team(Base, TimestampMixin):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (UniqueConstraint("workspace_id", "slug", name="uq_team_workspace_slug"),)


class TeamAssignment(Base, TimestampMixin):
    """Effective-dated operational role of a workspace member inside a team."""

    __tablename__ = "team_assignments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[TeamRole] = mapped_column(
        Enum(TeamRole, native_enum=False, length=16), nullable=False
    )
    assigned_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from", name="ck_team_assignment_window"
        ),
        Index("ix_team_assignment_workspace_user", "workspace_id", "user_id"),
    )


class WorkspaceInvitation(Base, TimestampMixin):
    __tablename__ = "workspace_invitations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invited_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[WorkspaceRole] = mapped_column(
        Enum(WorkspaceRole, native_enum=False, length=16), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrganizationMembership(Base, TimestampMixin):
    """Effective-dated organization role of a user (WP03-T03, ADR 0012)."""

    __tablename__ = "organization_memberships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[OrganizationRole] = mapped_column(
        Enum(OrganizationRole, native_enum=False, length=16), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    granted_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )

    organization: Mapped[Organization] = relationship()
    user: Mapped[User] = relationship(foreign_keys=[user_id])

    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_org_membership_org_user"),
        CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from", name="ck_org_membership_window"
        ),
    )


class OrganizationScopeGrant(Base, TimestampMixin):
    """Explicit analytics scope of an analyst/executive: one workspace, optionally one team."""

    __tablename__ = "organization_scope_grants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    membership_id: Mapped[str] = mapped_column(
        ForeignKey("organization_memberships.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    granted_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_org_scope_grant_membership_workspace", "membership_id", "workspace_id"),
    )


class Plan(Base, TimestampMixin):
    __tablename__ = "plans"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    entitlements_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    display_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # The plan_versions row whose terms (name, is_public, entitlements_json) this
    # row carries (WP18-T03); moved only by publishing a new version.
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (CheckConstraint("current_version >= 1", name="ck_plan_current_version"),)


class PlanVersion(Base):
    """One immutable version of a plan's commercial terms (WP18-T03).

    A global catalog, not tenant data: append-only (ORM guard below and no
    UPDATE/DELETE grant for the application role).  A subscription is pinned
    to the version it was granted under, so a later publish never changes what
    an existing tenant was given.
    """

    __tablename__ = "plan_versions"

    plan_key: Mapped[str] = mapped_column(ForeignKey("plans.key"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False)
    entitlements_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_plan_version_version"),
        CheckConstraint(
            "source IN ('catalog_seed', 'legacy_snapshot', 'operator', 'config')",
            name="ck_plan_version_source",
        ),
    )


class ModelManifestRecord(Base):
    """One registered model manifest version (WP08-T01, ADR 0026).

    A platform catalog, not tenant data: append-only, immutable, keyed by the
    digest of the canonical document.  Stage runs reference the digest, so a
    run keeps pointing at the exact manifest it ran under even after another
    version is selected or a file changes.
    """

    __tablename__ = "model_manifests"

    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    manifest_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("manifest_id", "version", name="uq_model_manifest_version"),
        CheckConstraint("version >= 1", name="ck_model_manifest_version"),
    )


class ProviderHealth(Base):
    """Health and request budget of one networked provider endpoint
    (WP08-T05, ADR 0030).

    Platform state, not tenant data: keyed by the digest of the adapter
    identity (provider, model, endpoint and declared parameters; never a
    credential) and shared by every worker and workspace that uses the
    endpoint.  ``open_until`` in the future means the circuit is open (after
    repeated transient failures, or because the provider asked callers to wait).
    """

    __tablename__ = "provider_health"

    endpoint_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_code: Mapped[str | None] = mapped_column(String(80))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "consecutive_failures >= 0 AND window_count >= 0", name="ck_provider_health_counts"
        ),
    )


class AiPolicyVersion(Base):
    """One version of a workspace's AI processing policy (WP08-T02, ADR 0027).

    Append-only: a change is a new version; version 0 is the built-in default
    and is never stored.  The document never holds a credential or URL.
    """

    __tablename__ = "ai_policy_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_schema: Mapped[str] = mapped_column(String(40), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "version", name="uq_ai_policy_version"),
        CheckConstraint("version >= 1", name="ck_ai_policy_version"),
    )


# ``^[a-z][a-z0-9_]{1,63}$`` without a regex, so SQLite runs the same check: 2 to 64
# characters, a lowercase first letter, and nothing outside ``a-z``, ``0-9`` and ``_``.
SCORECARD_KEY_CHECK = (
    "length(scorecard_key) BETWEEN 2 AND 64 "
    "AND trim(substr(scorecard_key, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
    "AND trim(scorecard_key, 'abcdefghijklmnopqrstuvwxyz0123456789_') = ''"
)


class ScorecardVersion(Base):
    """One published version of one QA scorecard of a workspace (HOM-54).

    Append-only: a change is a new version with the next number, and a stored row is
    never updated or deleted, so a past score can always name the exact definition it
    used.  There is no draft on the server.  ``document`` is the definition as
    validated by ``scorecards_version``; ``digest`` is its SHA-256.
    """

    __tablename__ = "scorecard_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scorecard_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    scorecards_version: Mapped[str] = mapped_column(String(40), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # Also the lookup of the latest version of a key and of a key's history.
        UniqueConstraint("workspace_id", "scorecard_key", "version", name="uq_scorecard_version"),
        CheckConstraint("version >= 1", name="ck_scorecard_version_positive"),
        CheckConstraint(SCORECARD_KEY_CHECK, name="ck_scorecard_key_format"),
    )


class RunPolicySnapshot(Base):
    """The policy and model placement one processing attempt ran under
    (WP08-T02/T03, ADR 0027).  Written once per attempt, never changed; it
    explains an execution and never authorizes a later one."""

    __tablename__ = "run_policy_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # Text-privacy classes digest the attempt claimed (WP07-T06); null = none.
    text_privacy_digest: Mapped[str | None] = mapped_column(String(64))
    strict_manifests: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Per stage: adapter, manifest, placement and the policy decision.
    stages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("job_id", "attempt", name="uq_run_policy_snapshot_attempt"),
        CheckConstraint("attempt >= 1", name="ck_run_policy_snapshot_attempt"),
        CheckConstraint("policy_version >= 0", name="ck_run_policy_snapshot_version"),
    )


class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_key: Mapped[str] = mapped_column(ForeignKey("plans.key"), nullable=False)
    # The plan version the subscription was granted under (WP18-T03).
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, native_enum=False, length=20), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(40), default="manual", nullable=False)
    provider_customer_id: Mapped[str | None] = mapped_column(String(200))
    provider_subscription_id: Mapped[str | None] = mapped_column(String(200), unique=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_subscription_workspace_status", "workspace_id", "status"),
        ForeignKeyConstraint(
            ["plan_key", "plan_version"],
            ["plan_versions.plan_key", "plan_versions.version"],
            name="fk_subscription_plan_version",
        ),
    )


class Entitlement(Base, TimestampMixin):
    __tablename__ = "entitlements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    metric: Mapped[str] = mapped_column(String(60), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    limit_value: Mapped[int | None] = mapped_column(Integer)
    used_value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("workspace_id", "metric", name="uq_entitlement_workspace_metric"),
        CheckConstraint("limit_value IS NULL OR limit_value >= 0", name="ck_entitlement_limit"),
        CheckConstraint("used_value >= 0 AND reserved_value >= 0", name="ck_entitlement_usage"),
    )


class Meeting(Base, TimestampMixin):
    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    team_id: Mapped[str | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), index=True
    )
    agent_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    language: Mapped[str] = mapped_column(String(12), default="fa", nullable=False)
    status: Mapped[MeetingStatus] = mapped_column(
        Enum(MeetingStatus, native_enum=False, length=20),
        default=MeetingStatus.UPLOADING,
        nullable=False,
    )
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source: Mapped[str] = mapped_column(String(30), default="upload", nullable=False)
    retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The conversation model (HOM-58): ``source`` says how the media arrived,
    # ``conversation_type`` says what the conversation is.
    conversation_type: Mapped[str] = mapped_column(
        String(20), default=ConversationType.CALL.value, server_default="call", nullable=False
    )
    direction: Mapped[str | None] = mapped_column(String(12))
    # The conversation's id in its source system: both set or both NULL.  Not
    # unique, because the ingestion ledger (ADR 0016) already handles a re-sent one.
    external_source_system: Mapped[str | None] = mapped_column(String(60))
    external_conversation_id: Mapped[str | None] = mapped_column(String(200))
    # When the conversation happened at its source (``created_at`` is upload time).
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recording: Mapped[Recording | None] = relationship(
        back_populates="meeting", cascade="all, delete-orphan", uselist=False
    )
    jobs: Mapped[list[ProcessingJob]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("duration_seconds >= 0", name="ck_meeting_duration"),
        CheckConstraint(CONVERSATION_TYPE_CHECK, name="ck_meeting_conversation_type"),
        CheckConstraint(CONVERSATION_DIRECTION_CHECK, name="ck_meeting_direction"),
        CheckConstraint(CONVERSATION_EXTERNAL_REF_CHECK, name="ck_meeting_external_ref"),
        Index("ix_meeting_workspace_created", "workspace_id", "created_at"),
        Index("ix_meeting_workspace_status", "workspace_id", "status"),
        Index(
            "ix_meeting_workspace_external",
            "workspace_id",
            "external_source_system",
            "external_conversation_id",
        ),
        Index(
            "ix_meeting_workspace_type_created", "workspace_id", "conversation_type", "created_at"
        ),
    )


class Recording(Base, TimestampMixin):
    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    storage_key: Mapped[str] = mapped_column(String(600), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    container_format: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    channel_count: Mapped[int | None] = mapped_column(Integer)
    sample_rate_hz: Mapped[int | None] = mapped_column(Integer)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The ORIGINAL media object behind this recording (WP05-T03); null only
    # for rows written before the media ledger that were not backfilled.
    media_object_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_objects.id", ondelete="SET NULL", name="fk_recording_media_object")
    )

    meeting: Mapped[Meeting] = relationship(back_populates="recording")
    media_object: Mapped[MediaObject | None] = relationship(foreign_keys=[media_object_id])

    __table_args__ = (
        CheckConstraint("size_bytes > 0 AND duration_seconds > 0", name="ck_recording_positive"),
        UniqueConstraint("media_object_id", name="uq_recording_media_object"),
        Index("ix_recording_workspace_checksum", "workspace_id", "checksum_sha256"),
    )


class MediaObject(Base):
    """One stored byte stream of a conversation and its private lifecycle
    (WP05-T03, ADR 0017).

    ``ORIGINAL`` is the upload as received (at most one per conversation,
    partial unique index); every other ``kind`` is derived from another
    object (``derived_from_id``) by a named ``producer`` under a job.  The
    content columns (kind, location, digest, size, type, duration, lineage,
    provenance, channel inspection) are immutable (ORM guard); the lifecycle columns (state,
    retention, deletion request, deletion, legal hold, integrity) are the
    only ones that change.  Rows are never removed: a deleted object is a
    ``DELETED`` row with ``deleted_at``; a held object keeps its bytes until
    the hold is released.
    """

    __tablename__ = "media_objects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL"), index=True
    )
    ingestion_id: Mapped[str | None] = mapped_column(
        ForeignKey("ingestion_records.id", ondelete="SET NULL")
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="SET NULL")
    )
    derived_from_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_objects.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[MediaKind] = mapped_column(
        Enum(MediaKind, native_enum=False, length=20), nullable=False
    )
    storage_backend: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(600), nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    container_format: Mapped[str | None] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    channel_count: Mapped[int | None] = mapped_column(Integer)
    sample_rate_hz: Mapped[int | None] = mapped_column(Integer)
    original_filename: Mapped[str | None] = mapped_column(String(300))
    # Channel facts, signal flags and trusted roles measured at ingest (HOM-37,
    # ADR 0039; backend/media/README.md). Write-once: set at insert through
    # backend.media.channel_store, never changed (content column, ORM guard).
    channel_inspection: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    producer: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    lifecycle_state: Mapped[MediaLifecycleState] = mapped_column(
        Enum(MediaLifecycleState, native_enum=False, length=20),
        default=MediaLifecycleState.ACTIVE,
        nullable=False,
    )
    retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_reason: Mapped[str | None] = mapped_column(String(40))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    legal_hold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    legal_hold_reason: Mapped[str | None] = mapped_column(String(200))
    legal_hold_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # When the hold must be reviewed (WP07-T02): a hold never silently lasts
    # forever; the reconciliation sweep reports overdue reviews.
    legal_hold_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    integrity: Mapped[MediaIntegrity] = mapped_column(
        Enum(MediaIntegrity, native_enum=False, length=12),
        default=MediaIntegrity.UNVERIFIED,
        nullable=False,
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    meeting: Mapped[Meeting | None] = relationship(foreign_keys=[meeting_id])
    derived_from: Mapped[MediaObject | None] = relationship(
        remote_side="MediaObject.id", foreign_keys=[derived_from_id]
    )

    __table_args__ = (
        CheckConstraint(MEDIA_KIND_CHECK, name="ck_media_kind"),
        CheckConstraint(MEDIA_STATE_CHECK, name="ck_media_state"),
        CheckConstraint(MEDIA_INTEGRITY_CHECK, name="ck_media_integrity"),
        CheckConstraint(MEDIA_BACKEND_CHECK, name="ck_media_backend"),
        CheckConstraint("length(sha256) = 64", name="ck_media_sha256"),
        CheckConstraint("size_bytes > 0", name="ck_media_size"),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0", name="ck_media_duration"
        ),
        CheckConstraint(
            "(kind = 'ORIGINAL' AND derived_from_id IS NULL) OR "
            "(kind <> 'ORIGINAL' AND derived_from_id IS NOT NULL)",
            name="ck_media_lineage",
        ),
        CheckConstraint(
            "(lifecycle_state = 'DELETED') = (deleted_at IS NOT NULL)", name="ck_media_deleted"
        ),
        CheckConstraint(
            "(lifecycle_state = 'LEGAL_HOLD') = (legal_hold_at IS NOT NULL)",
            name="ck_media_hold",
        ),
        CheckConstraint(
            "(legal_hold_at IS NULL) = (legal_hold_review_at IS NULL)", name="ck_media_hold_review"
        ),
        Index(
            "uq_media_original_per_meeting",
            "meeting_id",
            unique=True,
            postgresql_where=text("kind = 'ORIGINAL'"),
            sqlite_where=text("kind = 'ORIGINAL'"),
        ),
        Index("ix_media_workspace_state", "workspace_id", "lifecycle_state"),
        Index("ix_media_workspace_sha256", "workspace_id", "sha256"),
    )


class ConversationPurge(Base):
    """Durable purge of one tombstoned conversation (WP07-T03, ADR 0021).

    Created in the same transaction as the tombstone (``Meeting.deleted_at``),
    by an explicit deletion or by the retention sweep.  The sweep removes the
    derived content rows, asks every media object to go (holds respected),
    removes bytes behind a row lock and closes the row when every store is
    clean.  Only ids, counters, timestamps and an error code are stored: the
    completed row is the non-content receipt.  ``next_attempt_at`` is pushed
    forward when an attempt starts, so a concurrent sweeper skips it.
    """

    __tablename__ = "conversation_purges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[PurgeState] = mapped_column(
        Enum(PurgeState, native_enum=False, length=12),
        default=PurgeState.PENDING,
        nullable=False,
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(80))
    content_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_rows_removed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    objects_removed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    objects_held: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    objects_pending: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Tombstone journal (WP07-T04, ADR 0022): when the record reached the
    # journal outside the backups, and when a restore last reapplied it.
    journaled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reapplied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(PURGE_STATE_CHECK, name="ck_purge_state"),
        CheckConstraint("attempts >= 0", name="ck_purge_attempts"),
        CheckConstraint(
            "(state = 'COMPLETED') = (completed_at IS NOT NULL)", name="ck_purge_completed"
        ),
        Index("ix_purge_workspace_state_due", "workspace_id", "state", "next_attempt_at"),
    )


class TranscriptRedaction(Base):
    """A redacted version of one transcript revision (WP07-T05, ADR 0023).

    Identity is (revision, policy digest): the detector version and the class
    list are part of the computation identity, so a detector or policy change
    produces a new version and never rewrites an old one.  The redacted text
    lives in ``redacted_segments``; originals of replaced values only in the
    restricted, encrypted ``pii_mappings`` (absent when no key is configured:
    ``mapping_retained`` false, reveal impossible).
    """

    __tablename__ = "transcript_redactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_revisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    detector_version: Mapped[str] = mapped_column(String(40), nullable=False)
    classes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False)
    mapping_retained: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("revision_id", "policy_digest", name="uq_redaction_revision_policy"),
        CheckConstraint("entity_count >= 0", name="ck_redaction_entities"),
        Index("ix_redaction_workspace_meeting", "workspace_id", "meeting_id"),
    )


class RedactedSegment(Base):
    """The redacted text of one transcript segment under one redaction."""

    __tablename__ = "redacted_segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    redaction_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_redactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_segments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (UniqueConstraint("redaction_id", "segment_id", name="uq_redacted_segment"),)


class PiiMapping(Base):
    """Restricted original of one replaced value (WP07-T05, ADR 0023).

    The original is stored only as AES-256-GCM ciphertext under a key named by
    ``key_id`` (never in the database), bound by associated data to its tenant,
    redaction and placeholder.  Nothing but ids, class, offsets and the
    placeholder is readable without the key; reading an original is an audited,
    permission-checked action.
    """

    __tablename__ = "pii_mappings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    redaction_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_redactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_segments.id", ondelete="CASCADE"), nullable=False
    )
    placeholder: Mapped[str] = mapped_column(String(40), nullable=False)
    pii_class: Mapped[str] = mapped_column(String(24), nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    key_id: Mapped[str] = mapped_column(String(40), nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "redaction_id", "segment_id", "start_offset", name="uq_pii_mapping_position"
        ),
        CheckConstraint("end_offset > start_offset AND start_offset >= 0", name="ck_pii_offsets"),
        Index("ix_pii_mapping_redaction_placeholder", "redaction_id", "placeholder"),
    )


# Outcomes a derived text view can record (ADR 0034): stored as member NAMES.
TEXT_VIEW_STATE_CHECK = "state IN ('COMPLETED', 'PARTIAL', 'FAILED', 'SKIPPED')"


class TranscriptTextView(Base):
    """A derived, normalised view of one transcript revision with the entities
    found in it (WP22/F36, ADR 0034).

    Written by the post-call processor in the transaction that creates the
    revision; the raw segments are never changed.  The source is the text a
    reader gets by default: the persisted redacted version under an enforced
    workspace policy (``redaction_id``), the original segments otherwise.
    Identity is (revision, ``view_digest``): the digest covers the normaliser,
    extractor and PII detector versions, the source view and the reference
    time, so a version change yields a new view and an old one is never
    rewritten.  ``state`` is the honest outcome (a failed derivation is
    recorded, never substituted, and never fails the transcript).
    """

    __tablename__ = "transcript_text_views"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_revisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    redaction_id: Mapped[str | None] = mapped_column(
        ForeignKey("transcript_redactions.id", ondelete="CASCADE"), index=True
    )
    input_view: Mapped[str] = mapped_column(String(16), nullable=False)
    input_policy_digest: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(12), nullable=False)
    normalizer_version: Mapped[str] = mapped_column(String(40), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(40), nullable=False)
    detector_version: Mapped[str] = mapped_column(String(40), nullable=False)
    view_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    # "Today" for relative dates (the conversation's creation time) and the
    # timezone its local date is taken in.
    reference_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[StageState] = mapped_column(
        Enum(StageState, native_enum=False, length=24), nullable=False
    )
    reason_code: Mapped[str | None] = mapped_column(String(80))
    segment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="SET NULL")
    )
    attempt: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("revision_id", "view_digest", name="uq_text_view_revision_digest"),
        CheckConstraint(TEXT_VIEW_STATE_CHECK, name="ck_text_view_state"),
        CheckConstraint(
            "(input_view = 'original' AND input_policy_digest IS NULL "
            "AND redaction_id IS NULL) OR "
            "(input_view = 'redacted' AND input_policy_digest IS NOT NULL)",
            name="ck_text_view_input",
        ),
        # Output derived from a redacted text names the version it came from.
        CheckConstraint(
            "input_view = 'original' OR redaction_id IS NOT NULL OR state IN ('FAILED', 'SKIPPED')",
            name="ck_text_view_source",
        ),
        CheckConstraint("segment_count >= 0 AND entity_count >= 0", name="ck_text_view_counts"),
        CheckConstraint("attempt IS NULL OR attempt >= 0", name="ck_text_view_attempt"),
        Index("ix_text_view_workspace_meeting", "workspace_id", "meeting_id"),
    )


class TextViewSegment(Base):
    """The normalised text of one transcript segment under one text view."""

    __tablename__ = "text_view_segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    view_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_text_views.id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_segments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("view_id", "segment_id", name="uq_text_view_segment"),
        CheckConstraint("entity_count >= 0", name="ck_text_view_segment_entities"),
    )


class TextViewEntity(Base):
    """One entity found in the source text of one segment (ADR 0034).

    Offsets are character positions in the view's *source* text of that
    segment (the original segment, or its redacted version), never in the
    normalised text.  ``value`` is the extractor's normalised value (``null``
    for a person-name slot and for an ambiguous date or time, whose
    ``candidates`` list every reading); ``confidence`` is a rule prior, not a
    measured precision.
    """

    __tablename__ = "text_view_entities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    view_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_text_views.id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_segments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[Any | None] = mapped_column(JSON)
    candidates: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    ambiguous: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)

    __table_args__ = (
        UniqueConstraint("view_id", "position", name="uq_text_view_entity_position"),
        CheckConstraint(
            "start_offset >= 0 AND end_offset >= start_offset", name="ck_text_view_entity_offsets"
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_text_view_entity_confidence"
        ),
        Index("ix_text_view_entity_workspace_type", "workspace_id", "entity_type"),
    )


class IngestionRecord(Base):
    """One row per accepted ingestion event: the canonical entry of every
    capture source into Homayar (WP05-T01, ADR 0016).

    Identity is ``(workspace, source_type, external_event_id)``, unique in
    the database; a concurrent second claim of the same identity fails on
    the index, never on an application check.  ``payload_hash`` is the
    SHA-256 of the canonical ingestion payload (media digest, size, declared
    filename/type, title, language, source ids): a replay with the same hash
    returns this row, a replay with another hash is a conflict recorded on
    this row (``conflict_count``) and in the audit trail, never an
    overwrite.  ``media_hash`` is the SHA-256 of the original media bytes;
    a *different* event carrying the same media is a duplicate handled by
    the workspace policy (``duplicate_of_id``).

    Rows are immutable except for the conflict counters and the links that
    ``SET NULL`` when the conversation or job is removed; the ledger outlives
    both.
    """

    __tablename__ = "ingestion_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[IngestionSourceType] = mapped_column(
        Enum(IngestionSourceType, native_enum=False, length=32), nullable=False
    )
    source_provider: Mapped[str] = mapped_column(String(60), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    external_source_id: Mapped[str | None] = mapped_column(String(200))
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    media_hash: Mapped[str | None] = mapped_column(String(64))
    media_size_bytes: Mapped[int | None] = mapped_column(Integer)
    declared_content_type: Mapped[str | None] = mapped_column(String(120))
    original_filename: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[IngestionStatus] = mapped_column(
        Enum(IngestionStatus, native_enum=False, length=16),
        default=IngestionStatus.ACCEPTED,
        nullable=False,
    )
    reason: Mapped[str | None] = mapped_column(String(80))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    api_key_id: Mapped[str | None] = mapped_column(ForeignKey("api_keys.id", ondelete="SET NULL"))
    meeting_id: Mapped[str | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL"), index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="SET NULL")
    )
    duplicate_of_id: Mapped[str | None] = mapped_column(
        ForeignKey("ingestion_records.id", ondelete="SET NULL")
    )
    conflict_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_conflict_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    meeting: Mapped[Meeting | None] = relationship(foreign_keys=[meeting_id])

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source_type",
            "external_event_id",
            name="uq_ingestion_event",
        ),
        # One accepted ingestion per conversation and per root job.
        UniqueConstraint("meeting_id", name="uq_ingestion_meeting"),
        UniqueConstraint("job_id", name="uq_ingestion_job"),
        CheckConstraint(INGESTION_SOURCE_TYPE_CHECK, name="ck_ingestion_source_type"),
        CheckConstraint(INGESTION_STATUS_CHECK, name="ck_ingestion_status"),
        CheckConstraint("length(payload_hash) = 64", name="ck_ingestion_payload_hash"),
        CheckConstraint(
            "media_hash IS NULL OR length(media_hash) = 64", name="ck_ingestion_media_hash"
        ),
        CheckConstraint(
            "media_size_bytes IS NULL OR media_size_bytes > 0", name="ck_ingestion_media_size"
        ),
        CheckConstraint("conflict_count >= 0", name="ck_ingestion_conflicts"),
        CheckConstraint(
            "(status = 'ACCEPTED' AND reason IS NULL) OR "
            "(status = 'REJECTED' AND reason IS NOT NULL)",
            name="ck_ingestion_accepted_reason",
        ),
        Index("ix_ingestion_workspace_media", "workspace_id", "media_hash"),
        Index("ix_ingestion_workspace_received", "workspace_id", "received_at"),
    )


# --- CRM mapping (HOM-88, WP16-T04, F64) ------------------------------------------------
#
# Each enum-like column below stores the lowercase value with a check constraint, like
# ``Meeting.conversation_type``.


class ExternalRecordType(str, enum.Enum):
    """What an ``external_records`` row is in its source system (HOM-88)."""

    CUSTOMER = "customer"
    TICKET = "ticket"
    AGENT = "agent"


class ExternalLinkType(str, enum.Enum):
    """What a conversation is linked to in one source system (HOM-88)."""

    CUSTOMER = "customer"
    TICKET = "ticket"
    AGENT = "agent"
    OUTCOME = "outcome"


class CanonicalOutcome(str, enum.Enum):
    """The Homayar outcome that a source system's outcome code maps to (HOM-88)."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    ESCALATED = "escalated"
    FOLLOW_UP = "follow_up"
    WON = "won"
    LOST = "lost"
    OTHER = "other"


class InboundRecordState(str, enum.Enum):
    """Where one received CRM record stands (HOM-88).

    ``pending`` waits for its conversation until ``expires_at``; ``reconciliation``
    opened at least one item for a human.
    """

    APPLIED = "applied"
    PENDING = "pending"
    RECONCILIATION = "reconciliation"


class ReconciliationReason(str, enum.Enum):
    """Why the system did not decide a case alone (HOM-88)."""

    CONVERSATION_NOT_FOUND = "conversation_not_found"
    AMBIGUOUS_CONVERSATION = "ambiguous_conversation"
    CHANGED_ID = "changed_id"
    UNKNOWN_AGENT = "unknown_agent"
    UNKNOWN_OUTCOME = "unknown_outcome"
    INACTIVE_RECORD = "inactive_record"


class ReconciliationStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ReconciliationResolution(str, enum.Enum):
    LINK = "link"
    KEEP_CURRENT = "keep_current"
    DISMISS = "dismiss"


def _in_check(column: str, members: type[enum.Enum], *, nullable: bool = False) -> str:
    listed = f"{column} IN (" + ", ".join(f"'{member.value}'" for member in members) + ")"
    return f"{column} IS NULL OR {listed}" if nullable else listed


def _slug_check(column: str) -> str:
    """``^[a-z][a-z0-9_.-]{0,59}$`` without a regex, so SQLite runs the same check: 1 to
    60 characters, a lowercase first letter, and nothing outside ``a-z``, ``0-9``, ``_``,
    ``.`` and ``-``."""
    return (
        f"length({column}) BETWEEN 1 AND 60 "
        f"AND trim(substr({column}, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
        f"AND trim({column}, 'abcdefghijklmnopqrstuvwxyz0123456789_.-') = ''"
    )


INTEGRATION_SOURCE_SYSTEM_CHECK = _slug_check("source_system")
INTEGRATION_CONVERSATION_SOURCE_CHECK = _slug_check("conversation_source_system")
LATE_RECORD_WINDOW_MIN_SECONDS = 3600  # one hour
LATE_RECORD_WINDOW_MAX_SECONDS = 2592000  # 30 days
LATE_RECORD_WINDOW_DEFAULT_SECONDS = 604800  # 7 days
INTEGRATION_LATE_WINDOW_CHECK = (
    f"late_record_window_seconds BETWEEN {LATE_RECORD_WINDOW_MIN_SECONDS} "
    f"AND {LATE_RECORD_WINDOW_MAX_SECONDS}"
)
EXTERNAL_RECORD_TYPE_CHECK = _in_check("record_type", ExternalRecordType)
INBOUND_RECORD_STATE_CHECK = _in_check("state", InboundRecordState)
INBOUND_RECORD_PENDING_CHECK = (
    f"state <> '{InboundRecordState.PENDING.value}' OR expires_at IS NOT NULL"
)
EXTERNAL_LINK_TYPE_CHECK = _in_check("link_type", ExternalLinkType)
LINK_OUTCOME_CHECK = _in_check("outcome", CanonicalOutcome, nullable=True)
# An outcome link holds a canonical outcome and the source code, never a record; a
# customer, ticket or agent link holds a record, never an outcome.
LINK_SHAPE_CHECK = (
    f"(link_type = '{ExternalLinkType.OUTCOME.value}' AND external_record_id IS NULL "
    "AND outcome IS NOT NULL AND external_outcome_code IS NOT NULL) OR "
    f"(link_type <> '{ExternalLinkType.OUTCOME.value}' AND external_record_id IS NOT NULL "
    "AND outcome IS NULL AND external_outcome_code IS NULL)"
)
LINK_AGENT_CHECK = f"agent_user_id IS NULL OR link_type = '{ExternalLinkType.AGENT.value}'"
RECONCILIATION_LINK_TYPE_CHECK = _in_check("link_type", ExternalLinkType, nullable=True)
RECONCILIATION_REASON_CHECK = _in_check("reason", ReconciliationReason)
RECONCILIATION_STATUS_CHECK = _in_check("status", ReconciliationStatus)
RECONCILIATION_RESOLUTION_CHECK = _in_check("resolution", ReconciliationResolution, nullable=True)
# An open item has no resolution; a closed one has both its resolution and its time.
RECONCILIATION_CLOSED_CHECK = (
    f"(status = '{ReconciliationStatus.OPEN.value}' AND resolution IS NULL "
    "AND resolved_at IS NULL) OR "
    f"(status <> '{ReconciliationStatus.OPEN.value}' AND resolution IS NOT NULL "
    "AND resolved_at IS NOT NULL)"
)


class IntegrationMappingProfile(Base):
    """How to read one source system's records in one workspace (HOM-88).

    ``source_system`` is the CRM's slug, one profile per workspace.
    ``conversation_source_system`` names the system whose ids a record's conversation id
    holds (often a PBX).  ``field_map`` maps each canonical key to a dotted path into
    the payload; ``outcome_map`` and ``agent_map`` map external codes and agent ids to
    canonical outcomes and Homayar users.  ``version`` grows with every change, and each
    link keeps the version that produced it.
    """

    __tablename__ = "integration_mapping_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_system: Mapped[str] = mapped_column(String(60), nullable=False)
    conversation_source_system: Mapped[str] = mapped_column(String(60), nullable=False)
    field_map: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    outcome_map: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    agent_map: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    late_record_window_seconds: Mapped[int] = mapped_column(
        Integer, default=LATE_RECORD_WINDOW_DEFAULT_SECONDS, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    updated_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "source_system", name="uq_integration_profile_source"),
        CheckConstraint(
            INTEGRATION_SOURCE_SYSTEM_CHECK, name="ck_integration_profile_source_system"
        ),
        CheckConstraint(
            INTEGRATION_CONVERSATION_SOURCE_CHECK,
            name="ck_integration_profile_conversation_source",
        ),
        CheckConstraint(INTEGRATION_LATE_WINDOW_CHECK, name="ck_integration_profile_late_window"),
        CheckConstraint("version >= 1", name="ck_integration_profile_version"),
    )


class ExternalRecord(Base):
    """One customer, ticket or agent of a source system, known only by its stable
    external id (HOM-88).

    Identity is ``(workspace, source_system, record_type, external_id)``, unique in the
    database.  The same id in two source systems is two records, never merged.  No name,
    phone, email or other personal data is stored.  ``is_active`` mirrors what the source
    system says; Homayar never sets it to true on its own.
    """

    __tablename__ = "external_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_system: Mapped[str] = mapped_column(String(60), nullable=False)
    record_type: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source_system",
            "record_type",
            "external_id",
            name="uq_external_record_identity",
        ),
        CheckConstraint(EXTERNAL_RECORD_TYPE_CHECK, name="ck_external_record_type"),
        CheckConstraint("length(external_id) >= 1", name="ck_external_record_id"),
    )


class IntegrationInboundRecord(Base):
    """One record a source system sent us: the CRM ledger (HOM-88).

    Identity is ``(workspace, source_system, event_id)``, unique in the database, so a
    concurrent second insert loses on the index.  The raw payload is never stored:
    ``payload_hash`` is the SHA-256 of its canonical JSON, and ``extracted`` holds only
    the mapped ids, outcome code and active flags.  A ``pending`` record waits for its
    conversation until ``expires_at``.  The application role may not delete rows
    (``NO_DELETE_TABLES`` in ``database/rls.py``).
    """

    __tablename__ = "integration_inbound_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("integration_mapping_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_system: Mapped[str] = mapped_column(String(60), nullable=False)
    event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extracted: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    meeting_id: Mapped[str | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL"), index=True
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    conflict_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_conflict_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    api_key_id: Mapped[str | None] = mapped_column(ForeignKey("api_keys.id", ondelete="SET NULL"))
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "source_system", "event_id", name="uq_inbound_record_event"
        ),
        CheckConstraint("length(event_id) >= 1", name="ck_inbound_record_event_id"),
        CheckConstraint("length(payload_hash) = 64", name="ck_inbound_record_payload_hash"),
        CheckConstraint(INBOUND_RECORD_STATE_CHECK, name="ck_inbound_record_state"),
        CheckConstraint(INBOUND_RECORD_PENDING_CHECK, name="ck_inbound_record_pending_expiry"),
        CheckConstraint("attempt_count >= 0", name="ck_inbound_record_attempts"),
        CheckConstraint("conflict_count >= 0", name="ck_inbound_record_conflicts"),
        # The sweep's lookup of pending records by expiry.
        Index("ix_inbound_records_pending", "state", "expires_at"),
    )


class ConversationExternalLink(Base):
    """A conversation's current customer, ticket, agent or outcome in one source
    system (HOM-88).

    One current link per ``(meeting, source_system, link_type)``.  A changed value is
    never written over the link; it opens a reconciliation item.  The agent link never
    changes ``meetings.agent_user_id``, which drives record scope.
    ``linked_by_user_id`` is NULL when the link was made automatically.
    """

    __tablename__ = "conversation_external_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    source_system: Mapped[str] = mapped_column(String(60), nullable=False)
    link_type: Mapped[str] = mapped_column(String(16), nullable=False)
    external_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("external_records.id", ondelete="RESTRICT"), index=True
    )
    outcome: Mapped[str | None] = mapped_column(String(16))
    external_outcome_code: Mapped[str | None] = mapped_column(String(200))
    agent_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    inbound_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("integration_inbound_records.id", ondelete="SET NULL"), index=True
    )
    linked_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "meeting_id", "source_system", "link_type", name="uq_conversation_external_link"
        ),
        CheckConstraint(EXTERNAL_LINK_TYPE_CHECK, name="ck_conversation_link_type"),
        CheckConstraint(LINK_OUTCOME_CHECK, name="ck_conversation_link_outcome"),
        CheckConstraint(LINK_SHAPE_CHECK, name="ck_conversation_link_shape"),
        CheckConstraint(LINK_AGENT_CHECK, name="ck_conversation_link_agent"),
    )


class IntegrationReconciliationItem(Base):
    """A CRM mapping case the system will not decide alone; a human resolves it
    (HOM-88).

    ``meeting_id`` and ``link_type`` are NULL for the two conversation reasons.
    ``current_value`` and ``candidate_value`` hold the linked and the proposed external
    id (or outcome code); ``candidate_meeting_ids`` lists the matches of an ambiguous
    conversation.  An open item has no resolution; a closed one has its resolution and
    ``resolved_at``.
    """

    __tablename__ = "integration_reconciliation_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_system: Mapped[str] = mapped_column(String(60), nullable=False)
    inbound_record_id: Mapped[str] = mapped_column(
        ForeignKey("integration_inbound_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    meeting_id: Mapped[str | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL"), index=True
    )
    link_type: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=ReconciliationStatus.OPEN.value, nullable=False
    )
    current_value: Mapped[str | None] = mapped_column(String(200))
    candidate_value: Mapped[str | None] = mapped_column(String(200))
    candidate_meeting_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(16))
    resolved_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint(RECONCILIATION_LINK_TYPE_CHECK, name="ck_reconciliation_link_type"),
        CheckConstraint(RECONCILIATION_REASON_CHECK, name="ck_reconciliation_reason"),
        CheckConstraint(RECONCILIATION_STATUS_CHECK, name="ck_reconciliation_status"),
        CheckConstraint(RECONCILIATION_RESOLUTION_CHECK, name="ck_reconciliation_resolution"),
        CheckConstraint(RECONCILIATION_CLOSED_CHECK, name="ck_reconciliation_closed"),
        Index("ix_reconciliation_workspace_status_created", "workspace_id", "status", "created_at"),
    )


class UsageReservation(Base):
    __tablename__ = "usage_reservations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entitlement_id: Mapped[str] = mapped_column(
        ForeignKey("entitlements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    reserved_value: Mapped[int] = mapped_column(Integer, nullable=False)
    committed_value: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[ReservationState] = mapped_column(
        Enum(ReservationState, native_enum=False, length=16),
        default=ReservationState.RESERVED,
        nullable=False,
    )
    failure_reason: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("reserved_value > 0", name="ck_reservation_positive"),
        CheckConstraint(
            "committed_value IS NULL OR committed_value >= 0", name="ck_reservation_committed"
        ),
        # Settlement invariant (WP06-T06): one meeting is charged at most once
        # per metric, whatever retries, redeliveries or replays happened. A
        # future re-analysis product must add its own revision identity.
        Index(
            "uq_reservation_settled_per_meeting",
            "meeting_id",
            "entitlement_id",
            unique=True,
            postgresql_where=text("state = 'COMMITTED'"),
            sqlite_where=text("state = 'COMMITTED'"),
        ),
    )


class UsageEvent(Base):
    """Append-only usage ledger (WP06-T06).

    One row per reservation transition (``reserved`` / ``committed`` /
    ``released``) plus reconciliation ``adjusted`` rows. ``operation_key`` is
    the stable business-operation identity of the transition (unique), so a
    redelivered or retried transition cannot be recorded twice; the pair
    (reservation, event type) is unique as well.
    """

    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reservation_id: Mapped[str | None] = mapped_column(
        ForeignKey("usage_reservations.id", ondelete="RESTRICT"), index=True
    )
    operation_key: Mapped[str] = mapped_column(String(200), nullable=False)
    metric: Mapped[str] = mapped_column(String(60), nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(30), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(160))
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint("value >= 0", name="ck_usage_event_value"),
        UniqueConstraint("operation_key", name="uq_usage_event_operation"),
        UniqueConstraint("reservation_id", "event_type", name="uq_usage_event_reservation_type"),
        Index("ix_usage_event_workspace_time", "workspace_id", "occurred_at"),
    )


class ProcessingJob(Base, TimestampMixin):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reservation_id: Mapped[str] = mapped_column(
        ForeignKey("usage_reservations.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    concurrency_reservation_id: Mapped[str | None] = mapped_column(
        ForeignKey("usage_reservations.id", ondelete="RESTRICT"), nullable=True, unique=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    state: Mapped[JobState] = mapped_column(
        Enum(JobState, native_enum=False, length=20), default=JobState.QUEUED, nullable=False
    )
    stage: Mapped[str] = mapped_column(String(40), default="queued", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    # Execution lease (WP06-T04). ``locked_by`` is the owner, ``locked_at`` the
    # claim time of the current attempt (attempt deadline anchor).
    # ``lease_generation`` advances on every claim and every recovery fence;
    # a worker may renew or commit only while the row still carries the
    # generation it was handed, so a resumed stale worker is rejected.
    locked_by: Mapped[str | None] = mapped_column(String(120))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt_id: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_message: Mapped[str | None] = mapped_column(String(500))
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Failure policy (WP06-T05): the classified reason the job stopped
    # (``backend.jobs.failures.FailureClass``), its overall deadline and the
    # bounded operator replays of a dead-lettered job.
    failure_class: Mapped[str | None] = mapped_column(String(16))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replay_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    meeting: Mapped[Meeting] = relationship(back_populates="jobs")
    stage_runs: Mapped[list[ProcessingStageRun]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="ProcessingStageRun.sequence",
    )
    dispatch: Mapped[JobDispatch | None] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= max_attempts", name="ck_job_attempts"
        ),
        CheckConstraint("lease_generation >= 0", name="ck_job_lease_generation"),
        CheckConstraint("replay_count >= 0", name="ck_job_replays"),
        # A running attempt always has an owner and an expiring lease; anything
        # else carries neither, so "running without a lease" cannot exist.
        CheckConstraint(
            "(state = 'RUNNING' AND locked_by IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (state <> 'RUNNING' AND locked_by IS NULL AND lease_expires_at IS NULL)",
            name="ck_job_running_has_lease",
        ),
        Index("ix_job_workspace_state", "workspace_id", "state"),
        Index("ix_job_lease_expiry", "state", "lease_expires_at"),
    )


class JobDispatch(Base, TimestampMixin):
    """Transactional outbox row: the intent to publish one job to the broker.

    Written in the same transaction as the job; a publisher (request-time
    attempt or the sweeper) later publishes and marks it. Delivery is
    at-least-once by design — a crash between publish and mark republishes.
    """

    __tablename__ = "job_dispatches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    state: Mapped[DispatchState] = mapped_column(
        Enum(DispatchState, native_enum=False, length=16),
        default=DispatchState.PENDING,
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_id: Mapped[str | None] = mapped_column(String(120))
    last_error: Mapped[str | None] = mapped_column(String(500))
    # Job lease generation this intent was armed for (WP06-T04): the row is
    # re-armed at most once per generation (retry after failure, recovery of
    # an abandoned attempt), so duplicate scans cannot arm it twice.
    generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    job: Mapped[ProcessingJob] = relationship(back_populates="dispatch")

    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_dispatch_attempts"),
        CheckConstraint("generation >= 0", name="ck_dispatch_generation"),
        Index("ix_job_dispatch_state_due", "state", "next_attempt_at"),
    )


class ProcessingStageRun(Base, TimestampMixin):
    """One stage of one processing attempt with its typed outcome and provenance."""

    __tablename__ = "processing_stage_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[StageState] = mapped_column(
        Enum(StageState, native_enum=False, length=24), nullable=False
    )
    reason_code: Mapped[str | None] = mapped_column(String(80))
    reason_message: Mapped[str | None] = mapped_column(String(500))
    provider: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(256))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Checkpoint provenance (WP06-T03): the checkpoint this stage produced or
    # reused; ``reused`` says the provider was *not* called by this attempt.
    checkpoint_id: Mapped[str | None] = mapped_column(
        ForeignKey("stage_checkpoints.id", ondelete="SET NULL"), index=True
    )
    reused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Model manifest the stage ran under (WP08-T01): id/version for reading,
    # digest for the exact document in ``model_manifests``; ``unregistered``
    # (no digest) only outside strict mode.
    manifest_id: Mapped[str | None] = mapped_column(String(80))
    manifest_version: Mapped[int | None] = mapped_column(Integer)
    manifest_digest: Mapped[str | None] = mapped_column(String(64))

    job: Mapped[ProcessingJob] = relationship(back_populates="stage_runs")

    __table_args__ = (
        UniqueConstraint("job_id", "attempt", "stage", name="uq_stage_run_job_attempt_stage"),
        CheckConstraint("attempt >= 0", name="ck_stage_run_attempt"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_stage_run_latency"),
    )


class StageCheckpoint(Base, TimestampMixin):
    """A committed stage result with the provenance needed to reuse it (WP06-T03).

    Identity: ``input_hash`` is the SHA-256 of the canonical ``input_manifest``
    (scope, stage contract, source digests, provider identity, request
    parameters, upstream artifact digests) — never attempt ids, worker ids
    or timestamps.  Artifact: one attempt-scoped object (``artifact_key``)
    written through the storage abstraction and verified (``artifact_sha256``,
    ``artifact_size_bytes``, ``artifact_schema``) before the fenced
    publication.  At most one PUBLISHED checkpoint per meeting, stage and
    input hash (partial unique index); the object key is unique.
    """

    __tablename__ = "stage_checkpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[CheckpointState] = mapped_column(
        Enum(CheckpointState, native_enum=False, length=16),
        default=CheckpointState.PENDING,
        nullable=False,
    )
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    artifact_key: Mapped[str] = mapped_column(String(600), nullable=False, unique=True)
    artifact_schema: Mapped[str] = mapped_column(String(80), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    artifact_size_bytes: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(256))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidation_reason: Mapped[str | None] = mapped_column(String(80))
    # Set when the cleanup sweep (or deletion) removed the object; a
    # PUBLISHED row never has it.
    object_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("attempt >= 0", name="ck_checkpoint_attempt"),
        CheckConstraint("lease_generation >= 0", name="ck_checkpoint_generation"),
        CheckConstraint(
            "artifact_size_bytes IS NULL OR artifact_size_bytes >= 0",
            name="ck_checkpoint_artifact_size",
        ),
        # A published checkpoint always carries its verified artifact digest,
        # publication time and no deleted object.
        CheckConstraint(
            "state <> 'PUBLISHED' OR (artifact_sha256 IS NOT NULL AND "
            "artifact_size_bytes IS NOT NULL AND published_at IS NOT NULL AND "
            "object_deleted_at IS NULL)",
            name="ck_checkpoint_published_complete",
        ),
        Index(
            "uq_checkpoint_published_identity",
            "meeting_id",
            "stage",
            "input_hash",
            unique=True,
            postgresql_where=text("state = 'PUBLISHED'"),
            sqlite_where=text("state = 'PUBLISHED'"),
        ),
        Index("ix_checkpoint_meeting_stage_state", "meeting_id", "stage", "state"),
        Index("ix_checkpoint_workspace_state_updated", "workspace_id", "state", "updated_at"),
    )


class MeetingSpeaker(Base, TimestampMixin):
    __tablename__ = "meeting_speakers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    speaker_key: Mapped[str] = mapped_column(String(60), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(160))
    identity_type: Mapped[str] = mapped_column(String(30), default="unknown", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (UniqueConstraint("meeting_id", "speaker_key", name="uq_meeting_speaker_key"),)


class TranscriptRevision(Base):
    """One immutable version of a meeting's transcript (WP04-T02, ADR 0014).

    Every processing outcome and every human correction creates a *new*
    revision whose segments are never edited or deleted afterwards; a
    correction points at the revision it was made from (``parent_revision_id``).
    Exactly one revision per meeting is *current* (partial unique index); the
    only mutable columns are that pointer and ``superseded_at`` — the
    ``before_update`` guard below refuses everything else.  Provenance names
    the engine, model, checkpoint and input identity (``config_hash``) that
    produced the text, or the person and reason that corrected it.
    """

    __tablename__ = "transcript_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("transcript_revisions.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[TranscriptSource] = mapped_column(
        Enum(TranscriptSource, native_enum=False, length=16), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    language: Mapped[str] = mapped_column(String(12), nullable=False)
    segment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Engine provenance: the job/attempt, the ASR checkpoint that holds the raw
    # result and the identity hash of the inputs that produced it.
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="SET NULL")
    )
    attempt: Mapped[int | None] = mapped_column(Integer)
    checkpoint_id: Mapped[str | None] = mapped_column(
        ForeignKey("stage_checkpoints.id", ondelete="SET NULL")
    )
    config_hash: Mapped[str | None] = mapped_column(String(64))
    asr_provider: Mapped[str | None] = mapped_column(String(128))
    asr_model: Mapped[str | None] = mapped_column(String(256))
    diarization_provider: Mapped[str | None] = mapped_column(String(128))
    diarization_status: Mapped[str | None] = mapped_column(String(16))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    segments: Mapped[list[TranscriptSegment]] = relationship(
        back_populates="revision", order_by="TranscriptSegment.sequence"
    )

    __table_args__ = (
        UniqueConstraint("meeting_id", "revision_number", name="uq_transcript_revision_number"),
        CheckConstraint("revision_number >= 1", name="ck_transcript_revision_number"),
        CheckConstraint("segment_count >= 0", name="ck_transcript_revision_segments"),
        CheckConstraint("attempt IS NULL OR attempt >= 0", name="ck_transcript_revision_attempt"),
        Index(
            "uq_transcript_revision_current",
            "meeting_id",
            unique=True,
            postgresql_where=text("is_current"),
            sqlite_where=text("is_current"),
        ),
        Index("ix_transcript_revision_workspace_meeting", "workspace_id", "meeting_id"),
    )


class TranscriptSegment(Base):
    """One turn of one transcript revision; immutable once flushed."""

    __tablename__ = "transcript_segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_revisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker_key: Mapped[str] = mapped_column(String(60), default="unknown", nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    language: Mapped[str | None] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    revision: Mapped[TranscriptRevision] = relationship(back_populates="segments")

    __table_args__ = (
        UniqueConstraint("revision_id", "sequence", name="uq_segment_revision_sequence"),
        CheckConstraint("start_ms >= 0 AND end_ms >= start_ms", name="ck_segment_time"),
        Index("ix_segment_workspace_meeting", "workspace_id", "meeting_id"),
    )


class AnalysisRun(Base):
    """Provenance of one analysis (intelligence) pass over one transcript
    revision (WP04-T02).  The row is immutable except for the *current*
    pointer; the results it produced (summary, AI action items) reference it
    through ``analysis_run_id``.  A run that was not performed (no transcript)
    is recorded with its truthful stage state and no provider.
    """

    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    transcript_revision_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_revisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="SET NULL")
    )
    attempt: Mapped[int | None] = mapped_column(Integer)
    checkpoint_id: Mapped[str | None] = mapped_column(
        ForeignKey("stage_checkpoints.id", ondelete="SET NULL")
    )
    stage: Mapped[str] = mapped_column(String(40), default="intelligence", nullable=False)
    state: Mapped[StageState] = mapped_column(
        Enum(StageState, native_enum=False, length=24), nullable=False
    )
    reason_code: Mapped[str | None] = mapped_column(String(80))
    provider: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(256))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    prompt_sha256: Mapped[str | None] = mapped_column(String(64))
    output_schema_sha256: Mapped[str | None] = mapped_column(String(64))
    config_hash: Mapped[str | None] = mapped_column(String(64))
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Evidence accounting (WP04-T03): references the provider offered and how
    # many were rejected because they named no turn of the analysed revision.
    reference_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected_reference_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Which transcript text the provider received (WP07-T06, ADR 0024):
    # ``original`` or ``redacted`` under the policy digest (detector version +
    # classes) the job snapshotted at claim time.
    input_view: Mapped[str] = mapped_column(
        String(16), default="original", server_default="original", nullable=False
    )
    input_policy_digest: Mapped[str | None] = mapped_column(String(64))
    # Manifest of the intelligence model (WP08-T01); null for runs before it
    # or for an unregistered development adapter.
    manifest_digest: Mapped[str | None] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint("attempt IS NULL OR attempt >= 0", name="ck_analysis_run_attempt"),
        CheckConstraint(
            "(input_view = 'original' AND input_policy_digest IS NULL) OR "
            "(input_view = 'redacted' AND input_policy_digest IS NOT NULL)",
            name="ck_analysis_run_input_view",
        ),
        CheckConstraint("schema_version >= 1", name="ck_analysis_run_schema_version"),
        CheckConstraint(
            "reference_count >= 0 AND rejected_reference_count >= 0 "
            "AND rejected_reference_count <= reference_count",
            name="ck_analysis_run_references",
        ),
        Index(
            "uq_analysis_run_current",
            "meeting_id",
            unique=True,
            postgresql_where=text("is_current"),
            sqlite_where=text("is_current"),
        ),
        Index("ix_analysis_run_workspace_meeting", "workspace_id", "meeting_id"),
    )


class EvidenceSpan(Base):
    """One machine-readable citation: finding → revision → segment → text span →
    audio offsets (WP04-T03, ADR 0014 §evidence).

    Immutable.  ``finding_type``/``finding_id``/``finding_index`` say what the
    span supports (an action item row, or the n-th key point of the run's
    summary, or the summary overview).  Offsets are character positions in
    the segment's immutable text and milliseconds of the original audio taken
    from that segment; without word alignment a span covers the whole
    segment.  ``evidence_type`` names how the reference arose
    (``turn_reference`` from the provider, ``human`` from a person).
    """

    __tablename__ = "evidence_spans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    analysis_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True
    )
    transcript_revision_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_revisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_segments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    speaker_key: Mapped[str] = mapped_column(String(60), nullable=False)
    finding_type: Mapped[str] = mapped_column(String(24), nullable=False)
    finding_id: Mapped[str | None] = mapped_column(String(36), index=True)
    finding_index: Mapped[int | None] = mapped_column(Integer)
    text_start: Mapped[int] = mapped_column(Integer, nullable=False)
    text_end: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence_type: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    segment: Mapped[TranscriptSegment] = relationship()

    __table_args__ = (
        CheckConstraint("text_start >= 0 AND text_end >= text_start", name="ck_evidence_text"),
        CheckConstraint("start_ms >= 0 AND end_ms >= start_ms", name="ck_evidence_audio"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_evidence_confidence",
        ),
        CheckConstraint(
            "finding_type IN ('summary', 'insight', 'action_item', 'extraction')",
            name="ck_evidence_finding",
        ),
        CheckConstraint("evidence_type IN ('turn_reference', 'human')", name="ck_evidence_type"),
        CheckConstraint(
            "finding_index IS NULL OR finding_index >= 0", name="ck_evidence_finding_index"
        ),
        Index("ix_evidence_workspace_meeting", "workspace_id", "meeting_id"),
        Index("ix_evidence_run_finding", "analysis_run_id", "finding_type", "finding_index"),
    )


class AnalysisExtraction(Base):
    """The judged taxonomy extraction of one analysis run (HOM-53).

    ``result`` is the policy result as stored JSON: per dimension the kept values
    (auto-accepted or for review), the withheld ones and the issues, with the
    taxonomy, validator and policy versions and digests that produced it.  The
    columns next to it copy what a list needs without reading the JSON
    (``review_count``: kept values that need a person, HOM-18).  Immutable: a new
    analysis run gets a new row.  Entity values can be personal data (a phone, a
    national id), so the row is tenant-only and never reachable through a share.
    """

    __tablename__ = "analysis_extractions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    taxonomy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    taxonomy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    taxonomy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    validator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("analysis_run_id", name="uq_analysis_extraction_run"),
        CheckConstraint("taxonomy_version >= 1", name="ck_analysis_extraction_taxonomy_version"),
        CheckConstraint("review_count >= 0", name="ck_analysis_extraction_review_count"),
        # The review list of HOM-18.
        Index("ix_analysis_extraction_review", "workspace_id", "needs_review"),
    )


class TrendExtractionJudgment(Base):
    """The trend judgment of one analysis run's extraction, for one rules version (HOM-290).

    What the HOM-79 rules decide from a stored extraction row depends only on that row
    (immutable) and on the rules: the trend definition (``definition_digest``) and
    ``JUDGMENT_VERSION`` (``judgment_version``).  So it is stored once per run and rules
    version, as ``content_key``: the hex SHA-256 of the canonical JSON of the judged
    content, so the trend routes can count by that key in SQL.  The row holds ids,
    versions and a digest only, no personal data.  Immutable: a new definition or a new
    ``JUDGMENT_VERSION`` gets a new row.  Tenant-only, like its extraction.
    """

    __tablename__ = "trend_extraction_judgments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    extraction_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_extractions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    definition_id: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    judgment_version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # One row per run per rules version; also the lookup of the trend count's join.
        UniqueConstraint(
            "analysis_run_id",
            "definition_digest",
            "judgment_version",
            name="uq_trend_judgment_run",
        ),
        CheckConstraint("definition_version >= 1", name="ck_trend_judgment_definition_version"),
        # A tenant's rows of one rules version as one index-only range, hash-joined by the
        # trend count; the representative extraction comes from the run id.
        Index(
            "ix_trend_judgment_current",
            "workspace_id",
            "definition_digest",
            "judgment_version",
            "analysis_run_id",
            postgresql_include=["content_key"],
        ),
    )


class MeetingSummary(Base, TimestampMixin):
    __tablename__ = "meeting_summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    overview: Mapped[str] = mapped_column(Text, nullable=False)
    decisions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    key_points: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    topics: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    sentiment: Mapped[str | None] = mapped_column(String(30))
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    analysis_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="SET NULL"), index=True
    )
    # Grounding of the overview (WP04-T03); key points carry their own status.
    evidence_status: Mapped[str] = mapped_column(
        String(16), default=EvidenceStatus.INSUFFICIENT.value, nullable=False
    )

    analysis_run: Mapped[AnalysisRun | None] = relationship()

    __table_args__ = (CheckConstraint(EVIDENCE_STATUS_CHECK, name="ck_summary_evidence_status"),)


class ActionItem(Base, TimestampMixin):
    __tablename__ = "action_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    assignee_text: Mapped[str | None] = mapped_column(String(200))
    assignee_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    status: Mapped[ActionStatus] = mapped_column(
        Enum(ActionStatus, native_enum=False, length=20),
        default=ActionStatus.OPEN,
        nullable=False,
    )
    source_segment_id: Mapped[str | None] = mapped_column(
        ForeignKey("transcript_segments.id", ondelete="SET NULL")
    )
    source_timestamp_ms: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    created_by: Mapped[str] = mapped_column(String(30), default="ai", nullable=False)
    analysis_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="SET NULL"), index=True
    )
    evidence_status: Mapped[str] = mapped_column(
        String(16), default=EvidenceStatus.INSUFFICIENT.value, nullable=False
    )

    evidence: Mapped[list[EvidenceSpan]] = relationship(
        primaryjoin=(
            "and_(foreign(EvidenceSpan.finding_id) == ActionItem.id, "
            "EvidenceSpan.finding_type == 'action_item')"
        ),
        viewonly=True,
        order_by="EvidenceSpan.created_at",
    )

    __table_args__ = (
        CheckConstraint(
            "priority IN ('low', 'medium', 'high')",
            name="ck_action_priority",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_action_confidence",
        ),
        CheckConstraint(EVIDENCE_STATUS_CHECK, name="ck_action_evidence_status"),
    )


class ShareLink(Base, TimestampMixin):
    __tablename__ = "share_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Kept for API compatibility: True for every non-anonymous audience.
    require_authentication: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    audience: Mapped[ShareAudience] = mapped_column(
        Enum(ShareAudience, native_enum=False, length=16),
        default=ShareAudience.WORKSPACE,
        server_default="WORKSPACE",
        nullable=False,
    )
    # Viewing the processed result is implied; these are separate grants.
    allow_download: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    allow_export: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    recipients: Mapped[list[ShareRecipient]] = relationship(
        back_populates="share", cascade="all, delete-orphan", order_by="ShareRecipient.email"
    )


class ShareRecipient(Base):
    """A named recipient of a ``recipients`` share, bound to a user once proven.

    Exactly one address is set: a normalised email or an Iranian mobile number
    in E.164 form. The first access that proves ownership of the address on a
    *verified* account binds the row to that user id; afterwards only the user
    id matches (ADR 0006, decisions 3 and 7).
    """

    __tablename__ = "share_recipients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    share_id: Mapped[str] = mapped_column(
        ForeignKey("share_links.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str | None] = mapped_column(String(320))
    phone_e164: Mapped[str | None] = mapped_column(String(20))
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    share: Mapped[ShareLink] = relationship(back_populates="recipients")

    __table_args__ = (
        UniqueConstraint("share_id", "email", name="uq_share_recipient_email"),
        UniqueConstraint("share_id", "phone_e164", name="uq_share_recipient_phone"),
        CheckConstraint(
            "(email IS NULL) <> (phone_e164 IS NULL)", name="ck_share_recipient_address"
        ),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), index=True
    )
    actor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(60))
    target_id: Mapped[str | None] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(20), default="success", nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(80), index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (Index("ix_audit_workspace_time", "workspace_id", "occurred_at"),)


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookEndpoint(Base, TimestampMixin):
    __tablename__ = "webhook_endpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    event_types: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class WebhookDelivery(Base, TimestampMixin):
    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    endpoint_id: Mapped[str] = mapped_column(
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(500))


class ProviderUsage(Base):
    __tablename__ = "provider_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL"), index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="SET NULL"), index=True
    )
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    audio_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Reported by the provider (WP08-T04); null = not reported, never zero.
    # Rows written before revision e3a7c5f9b2d4 carry 0 for "not reported".
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_micros: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- immutability guards (WP04-T02) -------------------------------------------


class TranscriptImmutableError(RuntimeError):
    """An ORM update tried to change committed transcript or analysis provenance."""


# The only columns a revision/run row may change after its creation: the
# *current* pointer moves to a newer row.  Content and provenance never change.
MUTABLE_PROVENANCE_COLUMNS: frozenset[str] = frozenset({"is_current", "superseded_at"})


def _changed_columns(target: object) -> set[str]:
    state = inspect(target)
    return {
        attribute.key
        for attribute in state.attrs
        if attribute.key in state.mapper.column_attrs and attribute.history.has_changes()
    }


@event.listens_for(TranscriptSegment, "before_update")
@event.listens_for(EvidenceSpan, "before_update")
@event.listens_for(AnalysisExtraction, "before_update")
@event.listens_for(TrendExtractionJudgment, "before_update")
@event.listens_for(TranscriptRedaction, "before_update")
@event.listens_for(RedactedSegment, "before_update")
@event.listens_for(PiiMapping, "before_update")
@event.listens_for(TranscriptTextView, "before_update")
@event.listens_for(TextViewSegment, "before_update")
@event.listens_for(TextViewEntity, "before_update")
def _refuse_segment_update(_mapper, _connection, target: object) -> None:
    changed = _changed_columns(target)
    if changed:
        raise TranscriptImmutableError(
            f"{type(target).__name__} rows are immutable; create a new revision or "
            f"analysis instead (attempted: {', '.join(sorted(changed))})"
        )


@event.listens_for(TranscriptRevision, "before_update")
@event.listens_for(AnalysisRun, "before_update")
def _refuse_provenance_update(_mapper, _connection, target: object) -> None:
    changed = _changed_columns(target) - MUTABLE_PROVENANCE_COLUMNS
    if changed:
        raise TranscriptImmutableError(
            f"{type(target).__name__} rows are immutable except for the current pointer "
            f"(attempted: {', '.join(sorted(changed))})"
        )


class ManifestImmutableError(RuntimeError):
    """An ORM update tried to change a registered model manifest."""


@event.listens_for(ModelManifestRecord, "before_update")
def _refuse_manifest_update(_mapper, _connection, target: object) -> None:
    changed = _changed_columns(target)
    if changed:
        raise ManifestImmutableError(
            "Model manifests are immutable; register a new version instead "
            f"(attempted: {', '.join(sorted(changed))})"
        )


class PlanVersionImmutableError(RuntimeError):
    """An ORM update or delete tried to change a published plan version."""


@event.listens_for(PlanVersion, "before_update")
def _refuse_plan_version_update(_mapper, _connection, target: object) -> None:
    changed = _changed_columns(target)
    if changed:
        raise PlanVersionImmutableError(
            "Plan versions are append-only; publish a new version instead "
            f"(attempted: {', '.join(sorted(changed))})"
        )


@event.listens_for(PlanVersion, "before_delete")
def _refuse_plan_version_delete(_mapper, _connection, target: object) -> None:
    raise PlanVersionImmutableError("Plan versions are append-only")


class PolicyRecordImmutableError(RuntimeError):
    """An ORM update or delete tried to change a policy version or run snapshot."""


@event.listens_for(AiPolicyVersion, "before_update")
@event.listens_for(RunPolicySnapshot, "before_update")
def _refuse_policy_update(_mapper, _connection, target: object) -> None:
    changed = _changed_columns(target)
    if changed:
        raise PolicyRecordImmutableError(
            f"{type(target).__name__} rows are append-only "
            f"(attempted: {', '.join(sorted(changed))})"
        )


@event.listens_for(AiPolicyVersion, "before_delete")
@event.listens_for(RunPolicySnapshot, "before_delete")
def _refuse_policy_delete(_mapper, _connection, target: object) -> None:
    raise PolicyRecordImmutableError(f"{type(target).__name__} rows are append-only")


class ScorecardVersionImmutableError(RuntimeError):
    """An ORM update or delete tried to change a published scorecard version."""


@event.listens_for(ScorecardVersion, "before_update")
def _refuse_scorecard_version_update(_mapper, _connection, target: object) -> None:
    changed = _changed_columns(target)
    if changed:
        raise ScorecardVersionImmutableError(
            "Scorecard versions are append-only; publish a new version instead "
            f"(attempted: {', '.join(sorted(changed))})"
        )


@event.listens_for(ScorecardVersion, "before_delete")
def _refuse_scorecard_version_delete(_mapper, _connection, target: object) -> None:
    raise ScorecardVersionImmutableError("Scorecard versions are append-only")


class IngestionImmutableError(RuntimeError):
    """An ORM update tried to change a committed ingestion ledger row."""


# Bookkeeping an ingestion row may carry after its creation: conflict counters
# (case B) and the links the database itself clears when their target goes.
MUTABLE_INGESTION_COLUMNS: frozenset[str] = frozenset(
    {"conflict_count", "last_conflict_at", "meeting_id", "job_id"}
)


@event.listens_for(IngestionRecord, "before_update")
def _refuse_ingestion_update(_mapper, _connection, target: object) -> None:
    changed = _changed_columns(target) - MUTABLE_INGESTION_COLUMNS
    if changed:
        raise IngestionImmutableError(
            "IngestionRecord rows are immutable except for conflict bookkeeping "
            f"(attempted: {', '.join(sorted(changed))})"
        )


class MediaImmutableError(RuntimeError):
    """An ORM update tried to change the content columns of a media object."""


# Lifecycle columns of a media object (WP05-T03); everything else — what the
# bytes are, where they live, where they came from — is fixed at registration.
# ``channel_inspection`` (HOM-37) is write-once content: it is deliberately not
# listed here, so the guard refuses any change after the insert.
MUTABLE_MEDIA_COLUMNS: frozenset[str] = frozenset(
    {
        "lifecycle_state",
        "retention_expires_at",
        "deletion_requested_at",
        "deletion_reason",
        "deleted_at",
        "legal_hold_at",
        "legal_hold_reason",
        "legal_hold_by_user_id",
        "legal_hold_review_at",
        "integrity",
        "last_verified_at",
    }
)


@event.listens_for(MediaObject, "before_update")
def _refuse_media_update(_mapper, _connection, target: object) -> None:
    changed = _changed_columns(target) - MUTABLE_MEDIA_COLUMNS
    if changed:
        raise MediaImmutableError(
            f"MediaObject content columns are immutable (attempted: {', '.join(sorted(changed))})"
        )
