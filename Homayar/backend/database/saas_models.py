from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
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
    false,
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
    # HOM-129: a local emergency account; it signs in only inside an open
    # break-glass window (ADR 0076).  Server default only, so an INSERT that does
    # not set it also fits a schema before HOM-129 (older migration tests).
    is_break_glass: Mapped[bool] = mapped_column(Boolean, server_default=false(), nullable=False)

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
    # HOM-128: set when the IdP said the user is gone; HOM-127 clears it on a
    # later successful OIDC login.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    # HOM-129: when this session last passed the second factor, and how.  A
    # refresh keeps both (same row); a new login starts without them.
    mfa_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_method: Mapped[str | None] = mapped_column(String(16))
    break_glass_window_id: Mapped[str | None] = mapped_column(
        ForeignKey("break_glass_windows.id", ondelete="SET NULL")
    )

    __table_args__ = (Index("ix_sessions_user_active", "user_id", "revoked_at"),)


class UserMfaTotp(Base, TimestampMixin):
    """A user's TOTP factor (HOM-129, ADR 0076), one row per user.

    The secret is stored only encrypted (AES-256-GCM, ``MFA_SECRET_KEYS``).  The
    confirmed factor and a pending re-enrolment live side by side, so the working
    factor stays until the new one is confirmed with a code.
    """

    __tablename__ = "user_mfa_totp"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    encryption_key_id: Mapped[str | None] = mapped_column(String(64))
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_step: Mapped[int | None] = mapped_column(BigInteger)
    pending_encryption_key_id: Mapped[str | None] = mapped_column(String(64))
    pending_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    pending_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    pending_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MfaRecoveryCode(Base):
    """One single-use recovery code; only its peppered digest is stored (HOM-129)."""

    __tablename__ = "mfa_recovery_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BreakGlassWindow(Base):
    """A time-boxed period in which a break-glass account may sign in (HOM-129)."""

    __tablename__ = "break_glass_windows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    opened_by: Mapped[str] = mapped_column(String(128), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(128))


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
    # HOM-128: a row is "direct" when a person decided it (owner bootstrap,
    # invitation, admin installation) and "federated" when the IdP group
    # reconciler owns it.  An admin touch on a federated row makes it direct.
    source: Mapped[str] = mapped_column(
        String(16), default="direct", server_default="direct", nullable=False
    )

    workspace: Mapped[Workspace] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_membership_workspace_user"),
        CheckConstraint("source IN ('direct','federated')", name="ck_membership_source"),
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


FEDERATION_MAPPING_ROLE_CHECK = "role IN ('ADMIN','MEMBER','VIEWER')"


class FederationGroupMapping(Base, TimestampMixin):
    """One (organization, issuer, IdP group) -> (workspace, workspace role) rule.

    HOM-128, ADR 0066: an explicit, revocable mapping an organization admin
    decides by hand; the reconciler turns a federated user's current groups into
    workspace memberships through these rows.  A group never grants ``owner``.
    """

    __tablename__ = "federation_group_mappings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    # The group value exactly as the IdP returns it (often an opaque id); never
    # trimmed or case-folded.
    group_name: Mapped[str] = mapped_column(String(256), nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        Enum(WorkspaceRole, native_enum=False, length=16), nullable=False
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        CheckConstraint(FEDERATION_MAPPING_ROLE_CHECK, name="ck_federation_mapping_role"),
        Index(
            "uq_federation_group_mappings_active",
            "organization_id",
            "issuer",
            "group_name",
            "workspace_id",
            unique=True,
            sqlite_where=text("revoked_at IS NULL"),
            postgresql_where=text("revoked_at IS NULL"),
        ),
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


# The checks of the stored results (HOM-56) copy the outcome and reason rules of
# ``backend/scorecards/README.md``.  A CHECK passes when its expression is NULL, so every
# comparison with a nullable column has an ``IS NOT NULL`` guard: ``reason IN (...)``
# alone would accept a NULL reason.
SCORECARD_OUTCOME_CHECK = (
    "outcome IN ('passed', 'failed', 'not_applicable', 'insufficient_evidence')"
)
# README "Scorecard outcome": each outcome has its own reasons.
SCORECARD_RESULT_REASON_CHECK = (
    "(outcome = 'passed' AND reason IS NULL) "
    "OR (outcome = 'failed' AND reason IS NOT NULL "
    "AND reason IN ('auto_fail', 'below_threshold')) "
    "OR (outcome = 'not_applicable' AND reason IS NOT NULL "
    "AND reason = 'no_applicable_criteria') "
    "OR (outcome = 'insufficient_evidence' AND reason IS NOT NULL "
    "AND reason = 'evidence_missing')"
)
SCORECARD_RESULT_THRESHOLD_CHECK = "pass_threshold BETWEEN 1 AND 100"
# The denominator is the sum of its three parts, and the weights of a scorecard add up
# to 100.
SCORECARD_RESULT_WEIGHTS_CHECK = (
    "earned_weight >= 0 AND failed_weight >= 0 AND insufficient_weight >= 0 "
    "AND applicable_weight = earned_weight + failed_weight + insufficient_weight "
    "AND applicable_weight <= 100"
)
# A weight-0 criterion can be insufficient, so ``complete`` false with no insufficient
# weight is allowed.  ``NOT complete`` works on PostgreSQL booleans and SQLite integers.
SCORECARD_RESULT_COMPLETE_CHECK = "NOT complete OR insufficient_weight = 0"
SCORECARD_RESULT_DIGESTS_CHECK = "length(definition_digest) = 64 AND length(input_digest) = 64"
CRITERION_POSITION_CHECK = "position >= 0 AND position < 100"
CRITERION_WEIGHT_CHECK = "weight BETWEEN 0 AND 100"
# ``SCORECARD_KEY_CHECK`` applied to ``criterion_id``: the same three parts.
CRITERION_KEY_CHECK = SCORECARD_KEY_CHECK.replace("scorecard_key", "criterion_id")
# README "Criterion outcome", rows 1 and 3 to 9.  Row 2, a reviewer outcome, never
# reaches a machine result, so ``reviewer`` is refused.
CRITERION_DECIDED_BY_CHECK = (
    "(outcome = 'insufficient_evidence' AND decided_by IS NULL) "
    "OR (outcome = 'not_applicable' AND decided_by IS NOT NULL AND decided_by = 'rule') "
    "OR (outcome IN ('passed', 'failed') AND decided_by IS NOT NULL "
    "AND decided_by IN ('rule', 'model'))"
)
CRITERION_REASON_CHECK = (
    "(outcome IN ('passed', 'failed') AND reason IS NULL) "
    "OR (outcome IN ('not_applicable', 'insufficient_evidence') AND reason IS NOT NULL)"
)
# Review is required only through README row 4.
CRITERION_REVIEW_CHECK = (
    "NOT review_required OR (outcome = 'insufficient_evidence' AND reason IS NOT NULL "
    "AND reason = 'review_required')"
)
CRITERION_CHECK_OUTCOME_CHECK = (
    "check_outcome IS NULL "
    "OR check_outcome IN ('passed', 'failed', 'not_applicable', 'not_computable')"
)


class ScorecardResultRecord(Base):
    """One machine scoring of one conversation against one published scorecard version
    (HOM-56).

    Immutable: a stored result never changes, so a past score stays reproducible and
    explainable.  It is conversation content derived from a transcript, so the
    conversation purge removes it (bulk deletes, like ``analysis_extractions``).
    ``check_run`` is the input (``CheckRun.to_json()``) and ``result`` the output
    (``ScorecardResult.to_json()``), both stored exactly.  The columns next to them copy
    what a list or a check needs without reading the JSON.  There is no score column: the
    scores in ``result`` follow from the integer weights.  No column holds an NPS, a
    churn value or a probability.  Tenant-only, never through a share token.
    """

    __tablename__ = "scorecard_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scorecard_version_id: Mapped[str] = mapped_column(
        ForeignKey("scorecard_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The revision the check turns came from.  Every result names the revision its
    # evidence points into, so the purge of the revisions reaches every result.
    transcript_revision_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_revisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The calculation version, for example ``scorecards/1``.
    scorecards_version: Mapped[str] = mapped_column(String(40), nullable=False)
    checks_version: Mapped[str] = mapped_column(String(40), nullable=False)
    # A copy of the scorecard version's ``digest``.
    definition_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    # SHA-256 hex of the scoring input.
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    # The conversation facts used for scoring; a NULL direction is not known.
    conversation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(12))
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(40))
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    pass_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    # The score denominator and numerator, and the other two parts of the denominator.
    applicable_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    earned_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    insufficient_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    check_run: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # One stored result per version, conversation and input.
        UniqueConstraint(
            "scorecard_version_id", "meeting_id", "input_digest", name="uq_scorecard_result_input"
        ),
        # The target of the criterion rows' composite foreign key.  ``id`` alone is already
        # unique; this only lets a criterion row name its result's workspace and meeting.
        UniqueConstraint("id", "workspace_id", "meeting_id", name="uq_scorecard_result_scope"),
        # First, so SQLite names it when an unknown outcome also breaks the reason rule.
        CheckConstraint(SCORECARD_OUTCOME_CHECK, name="ck_scorecard_result_outcome"),
        CheckConstraint(SCORECARD_RESULT_REASON_CHECK, name="ck_scorecard_result_reason"),
        CheckConstraint(SCORECARD_RESULT_THRESHOLD_CHECK, name="ck_scorecard_result_threshold"),
        CheckConstraint(SCORECARD_RESULT_WEIGHTS_CHECK, name="ck_scorecard_result_weights"),
        CheckConstraint(SCORECARD_RESULT_COMPLETE_CHECK, name="ck_scorecard_result_complete"),
        CheckConstraint(SCORECARD_RESULT_DIGESTS_CHECK, name="ck_scorecard_result_digests"),
        CheckConstraint(CONVERSATION_TYPE_CHECK, name="ck_scorecard_result_conversation_type"),
        CheckConstraint(CONVERSATION_DIRECTION_CHECK, name="ck_scorecard_result_direction"),
        # The list of a conversation's results.
        Index("ix_scorecard_result_workspace_meeting", "workspace_id", "meeting_id"),
    )


class CriterionResultRecord(Base):
    """One criterion of one stored scorecard result, in definition order (HOM-56).

    A queryable copy of ``result["criteria"][position]`` of its result row, without the
    label (the definition holds it), so later tickets can find the criteria that need a
    person and point an override at one row.  Immutable, and removed by the conversation
    purge, like its result.
    """

    __tablename__ = "scorecard_criterion_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Its foreign key is ``fk_scorecard_criterion_result_result`` below.
    result_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # A copy of the result's conversation: the purge and the backup reconciliation find
    # content rows by ``meeting_id``.
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    criterion_id: Mapped[str] = mapped_column(String(64), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    auto_fail: Mapped[bool] = mapped_column(Boolean, nullable=False)
    na_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(40))
    decided_by: Mapped[str | None] = mapped_column(String(16))
    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    review_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    # Copied from the check result; NULL when no check ran.
    check_outcome: Mapped[str | None] = mapped_column(String(24))
    check_reason: Mapped[str | None] = mapped_column(String(40))
    evidence: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    candidate_evidence: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # A criterion row has its result's workspace and meeting.  The purge deletes
        # criterion rows by their own ``meeting_id``, so a row that named another meeting
        # would go with that meeting's purge and change a stored score.
        ForeignKeyConstraint(
            ["result_id", "workspace_id", "meeting_id"],
            [
                "scorecard_results.id",
                "scorecard_results.workspace_id",
                "scorecard_results.meeting_id",
            ],
            ondelete="CASCADE",
            name="fk_scorecard_criterion_result_result",
        ),
        # Its leading column also serves the ``result_id`` foreign key.
        UniqueConstraint("result_id", "position", name="uq_scorecard_criterion_result_position"),
        UniqueConstraint(
            "result_id", "criterion_id", name="uq_scorecard_criterion_result_criterion"
        ),
        # A conversation's criterion rows, for the purge and the backup reconciliation.
        Index("ix_scorecard_criterion_result_workspace_meeting", "workspace_id", "meeting_id"),
        # First, so SQLite names it when an unknown outcome also breaks other rules.
        CheckConstraint(SCORECARD_OUTCOME_CHECK, name="ck_scorecard_criterion_result_outcome"),
        CheckConstraint(CRITERION_POSITION_CHECK, name="ck_scorecard_criterion_result_position"),
        CheckConstraint(CRITERION_WEIGHT_CHECK, name="ck_scorecard_criterion_result_weight"),
        CheckConstraint(CRITERION_KEY_CHECK, name="ck_scorecard_criterion_result_key"),
        CheckConstraint(
            CRITERION_DECIDED_BY_CHECK, name="ck_scorecard_criterion_result_decided_by"
        ),
        CheckConstraint(CRITERION_REASON_CHECK, name="ck_scorecard_criterion_result_reason"),
        CheckConstraint(CRITERION_REVIEW_CHECK, name="ck_scorecard_criterion_result_review"),
        CheckConstraint(
            CRITERION_CHECK_OUTCOME_CHECK, name="ck_scorecard_criterion_result_check_outcome"
        ),
    )


# The supervisor review queue (HOM-73, WP14-T02).  A source, a status and a cancel
# reason are codes from these frozen lists, never free text, so no personal data reaches
# the audit log.
REVIEW_ITEM_SOURCES: tuple[str, ...] = ("manual", "sample")
REVIEW_ITEM_STATUSES: tuple[str, ...] = ("open", "in_review", "completed", "cancelled")
# ``completed`` and ``cancelled`` are terminal.
REVIEW_ITEM_ACTIVE_STATUSES: tuple[str, ...] = ("open", "in_review")
REVIEW_CANCEL_REASONS: tuple[str, ...] = ("duplicate", "out_of_scope", "meeting_unusable", "other")


def _sql_list(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{value}'" for value in values) + ")"


REVIEW_ITEM_STATUS_CHECK = f"status IN {_sql_list(REVIEW_ITEM_STATUSES)}"
REVIEW_ITEM_SOURCE_CHECK = f"source IN {_sql_list(REVIEW_ITEM_SOURCES)}"
REVIEW_ITEM_CANCEL_REASON_CHECK = (
    f"cancel_reason IS NULL OR cancel_reason IN {_sql_list(REVIEW_CANCEL_REASONS)}"
)
REVIEW_ITEM_REVISION_CHECK = "revision >= 1"
# Each status has its own timestamps, and only a cancelled item has a reason.  An item
# cancelled during its review keeps ``started_at``.  No check names a user column: those
# are SET NULL when a user is removed, and a check on them would make the removal fail.
REVIEW_ITEM_OPEN_FIELDS_CHECK = (
    "status <> 'open' OR (started_at IS NULL AND completed_at IS NULL "
    "AND cancelled_at IS NULL AND cancel_reason IS NULL)"
)
REVIEW_ITEM_IN_REVIEW_FIELDS_CHECK = (
    "status <> 'in_review' OR (started_at IS NOT NULL AND completed_at IS NULL "
    "AND cancelled_at IS NULL AND cancel_reason IS NULL)"
)
REVIEW_ITEM_COMPLETED_FIELDS_CHECK = (
    "status <> 'completed' OR (started_at IS NOT NULL AND completed_at IS NOT NULL "
    "AND cancelled_at IS NULL AND cancel_reason IS NULL)"
)
REVIEW_ITEM_CANCELLED_FIELDS_CHECK = (
    "status <> 'cancelled' OR (cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL "
    "AND completed_at IS NULL)"
)
REVIEW_ITEM_ACTIVE_WHERE = f"status IN {_sql_list(REVIEW_ITEM_ACTIVE_STATUSES)}"


class ReviewItem(Base):
    """One conversation a supervisor must review, with an optional assignee and a due
    time (HOM-73, WP14-T02).

    The queue entry that criterion override (HOM-74), appeals, calibration and coaching
    build on.  ``scorecard_result_id`` names the machine result under review, and only a
    result of the same conversation.  ``revision`` starts at 1 and grows by one with each
    change, so a write is a compare-and-set on it.  At most one item per conversation is
    active (``open`` or ``in_review``).  Not immutable: the service changes the status,
    the assignee and the due time, and the conversation purge deletes the rows.
    Tenant-only, never through a share token.
    """

    __tablename__ = "review_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Its foreign key is ``fk_review_items_scorecard_result`` below.
    scorecard_result_id: Mapped[str | None] = mapped_column(String(36), index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    # ``{}`` for a manual item; the draw's versions, digests and reasons for a sampled one.
    source_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    assignee_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    assigned_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (
        # An item names only a result of its own conversation and workspace
        # (``uq_scorecard_result_scope``).  A NULL result id is not checked (MATCH SIMPLE).
        ForeignKeyConstraint(
            ["scorecard_result_id", "workspace_id", "meeting_id"],
            [
                "scorecard_results.id",
                "scorecard_results.workspace_id",
                "scorecard_results.meeting_id",
            ],
            ondelete="CASCADE",
            name="fk_review_items_scorecard_result",
        ),
        CheckConstraint(REVIEW_ITEM_STATUS_CHECK, name="ck_review_items_status"),
        CheckConstraint(REVIEW_ITEM_SOURCE_CHECK, name="ck_review_items_source"),
        CheckConstraint(REVIEW_ITEM_CANCEL_REASON_CHECK, name="ck_review_items_cancel_reason"),
        CheckConstraint(REVIEW_ITEM_REVISION_CHECK, name="ck_review_items_revision"),
        CheckConstraint(REVIEW_ITEM_OPEN_FIELDS_CHECK, name="ck_review_items_open_fields"),
        CheckConstraint(
            REVIEW_ITEM_IN_REVIEW_FIELDS_CHECK, name="ck_review_items_in_review_fields"
        ),
        CheckConstraint(
            REVIEW_ITEM_COMPLETED_FIELDS_CHECK, name="ck_review_items_completed_fields"
        ),
        CheckConstraint(
            REVIEW_ITEM_CANCELLED_FIELDS_CHECK, name="ck_review_items_cancelled_fields"
        ),
        # At most one active item per conversation; a finished one does not block a new one.
        Index(
            "uq_review_items_active_meeting",
            "workspace_id",
            "meeting_id",
            unique=True,
            postgresql_where=text(REVIEW_ITEM_ACTIVE_WHERE),
            sqlite_where=text(REVIEW_ITEM_ACTIVE_WHERE),
        ),
        # The queue order (earliest due first) and its keyset pages.
        Index("ix_review_items_queue", "workspace_id", "status", "due_at", "id"),
        # The target of the composite foreign keys of the criterion decisions and the
        # human results (HOM-74): ``(id, scorecard_result_id)`` names exactly the result
        # an item is reviewing.  A NULL result id is fine (``id`` alone is unique).
        Index("uq_review_items_result", "id", "scorecard_result_id", unique=True),
    )


# The criterion decisions of a review (HOM-74, WP14-T03).  A decision, a reviewer
# outcome and an override reason are codes from these frozen lists, never free text,
# so no personal data reaches the audit log.  The reviewer outcomes are exactly the
# engine's ``REVIEWER_OUTCOMES`` (``backend.scorecards``).
REVIEW_DECISIONS: tuple[str, ...] = ("accept", "override")
REVIEW_REVIEWER_OUTCOMES: tuple[str, ...] = ("passed", "failed", "not_applicable")
REVIEW_OVERRIDE_REASONS: tuple[str, ...] = (
    "evidence_missed",
    "evidence_misread",
    "transcript_error",
    "context_outside_call",
    "policy_exception",
    "other",
)
REVIEW_NOTE_MAX_LENGTH = 2000

REVIEW_DECISION_CHECK = f"decision IN {_sql_list(REVIEW_DECISIONS)}"
REVIEW_DECISION_MACHINE_OUTCOME_CHECK = (
    f"machine_outcome IN "
    f"{_sql_list(('passed', 'failed', 'not_applicable', 'insufficient_evidence'))}"
)
REVIEW_DECISION_REVIEWER_OUTCOME_CHECK = (
    f"reviewer_outcome IN {_sql_list(REVIEW_REVIEWER_OUTCOMES)}"
)
REVIEW_DECISION_REASON_CODE_CHECK = (
    f"reason_code IS NULL OR reason_code IN {_sql_list(REVIEW_OVERRIDE_REASONS)}"
)
REVIEW_DECISION_NOTE_LENGTH_CHECK = f"note IS NULL OR length(note) <= {REVIEW_NOTE_MAX_LENGTH}"
# An accept confirms the machine outcome and carries no reason code.  A note on an
# accept is allowed.  No check names a user column: ``decided_by_user_id`` is SET NULL
# when a user is removed, and a check on it would make the removal fail.
REVIEW_DECISION_ACCEPT_CHECK = (
    "decision <> 'accept' OR (reviewer_outcome = machine_outcome AND reason_code IS NULL)"
)
# An override changes the outcome and needs a reason code and a non-blank note.  So a
# machine ``insufficient_evidence`` can never be accepted: the reviewer outcome cannot
# be ``insufficient_evidence``, so only an override can settle such a criterion.
REVIEW_DECISION_OVERRIDE_CHECK = (
    "decision <> 'override' OR (reviewer_outcome <> machine_outcome "
    "AND reason_code IS NOT NULL AND note IS NOT NULL AND length(trim(note)) > 0)"
)


class ReviewCriterionDecision(Base):
    """One human decision on one criterion of the machine result under review, inside
    one review item (HOM-74, WP14-T03, F44).

    The machine rows are never written; the human decision lives only here.  An accept
    confirms the machine outcome; an override changes it and needs a reason code from
    the frozen list and a non-blank note.  ``machine_outcome`` is a copy of the
    criterion row's outcome at decision time, so the decision explains itself even if
    the criterion row is rebuilt.  The row can change while the review is in progress
    (the service of PR 2 upserts it on the item's compare-and-set), so it is NOT
    immutable.  The three composite foreign keys tie the decision to its item's result,
    to its result's own workspace and meeting, and to a criterion of that result.
    Tenant-only, never through a share token; the conversation purge deletes the rows.
    """

    __tablename__ = "review_criterion_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # A copy of the result's conversation: the purge and the backup reconciliation find
    # content rows by ``meeting_id``.
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    # Its foreign key is ``fk_review_criterion_decisions_item`` below.
    review_item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Its foreign keys are ``fk_review_criterion_decisions_result`` and
    # ``fk_review_criterion_decisions_criterion`` below.
    scorecard_result_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # The criterion key, as in ``scorecard_criterion_results.criterion_id``.
    criterion_id: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    machine_outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    reviewer_outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(32))
    # Free text from the reviewer; required for an override, allowed on an accept.
    note: Mapped[str | None] = mapped_column(Text)
    # ``[]`` for an accept; the service of PR 2 fills it for an override.
    evidence: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    decided_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # A decision names only the result its item is reviewing (``uq_review_items_result``).
        ForeignKeyConstraint(
            ["review_item_id", "scorecard_result_id"],
            ["review_items.id", "review_items.scorecard_result_id"],
            ondelete="CASCADE",
            name="fk_review_criterion_decisions_item",
        ),
        # So the workspace and the meeting copies are the result's (and so the item's).
        ForeignKeyConstraint(
            ["scorecard_result_id", "workspace_id", "meeting_id"],
            [
                "scorecard_results.id",
                "scorecard_results.workspace_id",
                "scorecard_results.meeting_id",
            ],
            ondelete="CASCADE",
            name="fk_review_criterion_decisions_result",
        ),
        # So a decision names a criterion of that result
        # (``uq_scorecard_criterion_result_criterion``).
        ForeignKeyConstraint(
            ["scorecard_result_id", "criterion_id"],
            ["scorecard_criterion_results.result_id", "scorecard_criterion_results.criterion_id"],
            ondelete="CASCADE",
            name="fk_review_criterion_decisions_criterion",
        ),
        # One current decision per criterion per item.
        UniqueConstraint(
            "review_item_id", "criterion_id", name="uq_review_criterion_decisions_criterion"
        ),
        CheckConstraint(REVIEW_DECISION_CHECK, name="ck_review_criterion_decisions_decision"),
        CheckConstraint(
            REVIEW_DECISION_MACHINE_OUTCOME_CHECK,
            name="ck_review_criterion_decisions_machine_outcome",
        ),
        CheckConstraint(
            REVIEW_DECISION_REVIEWER_OUTCOME_CHECK,
            name="ck_review_criterion_decisions_reviewer_outcome",
        ),
        CheckConstraint(
            REVIEW_DECISION_REASON_CODE_CHECK, name="ck_review_criterion_decisions_reason_code"
        ),
        CheckConstraint(
            REVIEW_DECISION_NOTE_LENGTH_CHECK, name="ck_review_criterion_decisions_note_length"
        ),
        CheckConstraint(REVIEW_DECISION_ACCEPT_CHECK, name="ck_review_criterion_decisions_accept"),
        CheckConstraint(
            REVIEW_DECISION_OVERRIDE_CHECK, name="ck_review_criterion_decisions_override"
        ),
        CheckConstraint(CRITERION_KEY_CHECK, name="ck_review_criterion_decisions_criterion_key"),
        # The purge and the backup reconciliation, by workspace and conversation.
        Index("ix_review_criterion_decisions_workspace_meeting", "workspace_id", "meeting_id"),
    )


class ReviewResultRecord(Base):
    """The human score of one completed review: the machine result scored again by the
    same engine with the reviewer outcomes (HOM-74, WP14-T03, F44).

    One row per completed item that has a scorecard result.  ``reviewer_outcomes`` is
    the engine input and ``result`` the engine output (``ScorecardResult.to_json()``),
    both stored exactly; the integer columns next to them copy what a list or a check
    needs without reading the JSON.  The machine rows are never written, so the machine
    score and the human score stay separate.  Immutable, like ``scorecard_results``: a
    stored human result never changes, and only the conversation purge removes rows
    (bulk deletes).  No user column: the table has no UPDATE grant, so a ``SET NULL``
    action on a user column would need an update.  The completer is the item's assignee
    and the audit actor.  Tenant-only, never through a share token.
    """

    __tablename__ = "review_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    # Its foreign key is ``fk_review_results_item`` below.
    review_item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Its foreign keys are ``fk_review_results_item`` and
    # ``fk_review_results_scorecard_result`` below.
    scorecard_result_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # The calculation version the engine reported, for example ``scorecards/1``.
    scorecards_version: Mapped[str] = mapped_column(String(40), nullable=False)
    # The engine input: ``{criterion_id: reviewer_outcome}``.
    reviewer_outcomes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(40))
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    pass_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    # The score denominator and numerator, and the other two parts of the denominator.
    applicable_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    earned_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    insufficient_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # The human result names only the result its item is reviewing
        # (``uq_review_items_result``), and only its item.
        ForeignKeyConstraint(
            ["review_item_id", "scorecard_result_id"],
            ["review_items.id", "review_items.scorecard_result_id"],
            ondelete="CASCADE",
            name="fk_review_results_item",
        ),
        ForeignKeyConstraint(
            ["scorecard_result_id", "workspace_id", "meeting_id"],
            [
                "scorecard_results.id",
                "scorecard_results.workspace_id",
                "scorecard_results.meeting_id",
            ],
            ondelete="CASCADE",
            name="fk_review_results_scorecard_result",
        ),
        UniqueConstraint("review_item_id", name="uq_review_results_item"),
        # First, so SQLite names it when an unknown outcome also breaks the reason rule.
        CheckConstraint(SCORECARD_OUTCOME_CHECK, name="ck_review_result_outcome"),
        CheckConstraint(SCORECARD_RESULT_REASON_CHECK, name="ck_review_result_reason"),
        CheckConstraint(SCORECARD_RESULT_THRESHOLD_CHECK, name="ck_review_result_threshold"),
        CheckConstraint(SCORECARD_RESULT_WEIGHTS_CHECK, name="ck_review_result_weights"),
        CheckConstraint(SCORECARD_RESULT_COMPLETE_CHECK, name="ck_review_result_complete"),
        # The purge and the backup reconciliation, by workspace and conversation.
        Index("ix_review_results_workspace_meeting", "workspace_id", "meeting_id"),
    )


# The blinded calibration batches (HOM-75, WP14-T04).  A status and an outcome are
# codes from these frozen lists, never free text, so no personal data reaches the
# audit log.  The outcomes are exactly the reviewer outcomes of HOM-74, which are the
# engine's ``REVIEWER_OUTCOMES`` (``backend.scorecards``).
CALIBRATION_BATCH_STATUSES: tuple[str, ...] = ("open", "closed", "cancelled")
CALIBRATION_OUTCOMES: tuple[str, ...] = REVIEW_REVIEWER_OUTCOMES
CALIBRATION_TITLE_MAX_LENGTH = 200
CALIBRATION_MAX_EXAMPLES = 50
CALIBRATION_MAX_PARTICIPANTS = 20

CALIBRATION_STATUS_CHECK = f"status IN {_sql_list(CALIBRATION_BATCH_STATUSES)}"
CALIBRATION_REVISION_CHECK = "revision >= 1"
# Free text from the review admin: not blank, at most the title length.
CALIBRATION_TITLE_CHECK = (
    f"length(trim(title)) > 0 AND length(title) <= {CALIBRATION_TITLE_MAX_LENGTH}"
)
# Each status has its own closing timestamp.  No check names a user column: the three
# user columns are SET NULL when a user is removed, and a check on them would make the
# removal fail.
CALIBRATION_OPEN_FIELDS_CHECK = "status <> 'open' OR (closed_at IS NULL AND cancelled_at IS NULL)"
CALIBRATION_CLOSED_FIELDS_CHECK = (
    "status <> 'closed' OR (closed_at IS NOT NULL AND cancelled_at IS NULL)"
)
CALIBRATION_CANCELLED_FIELDS_CHECK = (
    "status <> 'cancelled' OR (cancelled_at IS NOT NULL AND closed_at IS NULL)"
)
# The example list is fixed at creation: 1 to 50 examples, so position 0 to 49.
CALIBRATION_POSITION_CHECK = f"position >= 0 AND position < {CALIBRATION_MAX_EXAMPLES}"
CALIBRATION_OUTCOME_CHECK = f"outcome IN {_sql_list(CALIBRATION_OUTCOMES)}"
# The adjudicator's note, the same length rule as a review note.
CALIBRATION_NOTE_LENGTH_CHECK = f"note IS NULL OR length(note) <= {REVIEW_NOTE_MAX_LENGTH}"


class CalibrationBatch(Base):
    """One blinded calibration exercise: a fixed list of examples and a fixed list of
    participants (HOM-75, WP14-T04, F46).

    A review admin (workspace OWNER or ADMIN) creates the batch, records the
    adjudications and closes it; only a closed batch shows answers next to
    adjudications.  ``revision`` starts at 1 and grows by one with each close or
    cancel, so a write is a compare-and-set on it, like ``review_items``.  Not
    immutable: the service changes the status and the closing columns.  Not
    conversation content: the conversation purge keeps the batch rows.  Tenant-only,
    never through a share token.
    """

    __tablename__ = "calibration_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Free text from the review admin; never copied to the audit log.
    title: Mapped[str] = mapped_column(String(CALIBRATION_TITLE_MAX_LENGTH), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    closed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    cancelled_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (
        CheckConstraint(CALIBRATION_STATUS_CHECK, name="ck_calibration_batches_status"),
        CheckConstraint(CALIBRATION_REVISION_CHECK, name="ck_calibration_batches_revision"),
        CheckConstraint(CALIBRATION_TITLE_CHECK, name="ck_calibration_batches_title"),
        CheckConstraint(CALIBRATION_OPEN_FIELDS_CHECK, name="ck_calibration_batches_open_fields"),
        CheckConstraint(
            CALIBRATION_CLOSED_FIELDS_CHECK, name="ck_calibration_batches_closed_fields"
        ),
        CheckConstraint(
            CALIBRATION_CANCELLED_FIELDS_CHECK, name="ck_calibration_batches_cancelled_fields"
        ),
        # The target of the composite foreign keys of the examples and the
        # participants: ``(id, workspace_id)`` names exactly the batch in its
        # workspace.
        Index("uq_calibration_batches_scope", "id", "workspace_id", unique=True),
        # The list order (newest first) and its keyset pages.
        Index("ix_calibration_batches_list", "workspace_id", "created_at", "id"),
    )


class CalibrationExample(Base):
    """One machine scorecard result of one conversation in one calibration batch
    (HOM-75, WP14-T04, F46).

    Immutable once written: the batch's example list is fixed at creation, so the
    application role has no UPDATE on the table and no user column exists (a SET NULL
    action on one would need an update).  ``position`` keeps the admin's order, 0 to
    49.  The composite keys tie the example to its batch in its workspace and to its
    result's own scope; ``uq_calibration_examples_meeting`` keeps one conversation at
    most once per batch.  It is conversation content: the conversation purge deletes
    the row, and the backup reconciliation counts it.  Tenant-only, never through a
    share token.
    """

    __tablename__ = "calibration_examples"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # Its foreign key is ``fk_calibration_examples_batch`` below.
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    # Its foreign key is ``fk_calibration_examples_scorecard_result`` below.
    scorecard_result_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # An example names only its own batch (``uq_calibration_batches_scope``).
        ForeignKeyConstraint(
            ["batch_id", "workspace_id"],
            ["calibration_batches.id", "calibration_batches.workspace_id"],
            ondelete="CASCADE",
            name="fk_calibration_examples_batch",
        ),
        # So the workspace and the meeting are the result's own.
        ForeignKeyConstraint(
            ["scorecard_result_id", "workspace_id", "meeting_id"],
            [
                "scorecard_results.id",
                "scorecard_results.workspace_id",
                "scorecard_results.meeting_id",
            ],
            ondelete="CASCADE",
            name="fk_calibration_examples_scorecard_result",
        ),
        # One conversation once per batch.
        UniqueConstraint("batch_id", "meeting_id", name="uq_calibration_examples_meeting"),
        UniqueConstraint("batch_id", "position", name="uq_calibration_examples_position"),
        CheckConstraint(CALIBRATION_POSITION_CHECK, name="ck_calibration_examples_position"),
        # The target of the composite foreign keys of the answers and the
        # adjudications: ``(id, batch_id, scorecard_result_id)`` names exactly the
        # result the example holds, in the batch it belongs to.
        Index(
            "uq_calibration_examples_scope", "id", "batch_id", "scorecard_result_id", unique=True
        ),
        # The purge and the backup reconciliation, by workspace and conversation.
        Index("ix_calibration_examples_workspace_meeting", "workspace_id", "meeting_id"),
    )


class CalibrationParticipant(Base):
    """One reviewer in one calibration batch (HOM-75, WP14-T04, F46).

    The participant list is fixed at creation.  A removed user leaves the row with
    ``user_id`` NULL, so the report still counts their answers.  Not conversation
    content: the conversation purge keeps the row.  Tenant-only, never through a share
    token.
    """

    __tablename__ = "calibration_participants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # Its foreign key is ``fk_calibration_participants_batch`` below.
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # A participant belongs to exactly one batch of its own workspace.
        ForeignKeyConstraint(
            ["batch_id", "workspace_id"],
            ["calibration_batches.id", "calibration_batches.workspace_id"],
            ondelete="CASCADE",
            name="fk_calibration_participants_batch",
        ),
        # One participant once per batch.  A removed user leaves a NULL ``user_id``;
        # NULLs are distinct, so several removed users may stay in one batch.
        UniqueConstraint("batch_id", "user_id", name="uq_calibration_participants_user"),
        # The target of the answers' participant foreign key.
        Index("uq_calibration_participants_scope", "id", "batch_id", unique=True),
    )


class CalibrationAnswer(Base):
    """One participant's outcome on one reviewable criterion of one example
    (HOM-75, WP14-T04, F46).

    The participant never sees the machine outcome through the calibration API: the
    answer is their own word from the frozen outcome list.  The service of PR 2
    upserts the row while the batch is open, so the table is not immutable.  The
    composite foreign keys tie the answer to its example's batch and result, to its
    participant's batch, to its result's own workspace and meeting, and to a criterion
    of that result, so a buggy service cannot cross batches, results or workspaces.
    Conversation content: the conversation purge deletes the row and the backup
    reconciliation counts it.  Tenant-only, never through a share token.
    """

    __tablename__ = "calibration_answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # A copy of the example's result's conversation: the purge and the backup
    # reconciliation find content rows by ``meeting_id``.
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    # Its foreign keys are ``fk_calibration_answers_example`` and
    # ``fk_calibration_answers_participant`` below.
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    example_id: Mapped[str] = mapped_column(String(36), nullable=False)
    participant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Its foreign keys are ``fk_calibration_answers_result`` and
    # ``fk_calibration_answers_criterion`` below.
    scorecard_result_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # The criterion key, as in ``scorecard_criterion_results.criterion_id``.
    criterion_id: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # An answer names only its example's batch and result
        # (``uq_calibration_examples_scope``).
        ForeignKeyConstraint(
            ["example_id", "batch_id", "scorecard_result_id"],
            [
                "calibration_examples.id",
                "calibration_examples.batch_id",
                "calibration_examples.scorecard_result_id",
            ],
            ondelete="CASCADE",
            name="fk_calibration_answers_example",
        ),
        # So the participant is one of the same batch (``uq_calibration_participants_scope``).
        ForeignKeyConstraint(
            ["participant_id", "batch_id"],
            ["calibration_participants.id", "calibration_participants.batch_id"],
            ondelete="CASCADE",
            name="fk_calibration_answers_participant",
        ),
        # So the workspace and the meeting copies are the result's own
        # (``uq_scorecard_result_scope``).
        ForeignKeyConstraint(
            ["scorecard_result_id", "workspace_id", "meeting_id"],
            [
                "scorecard_results.id",
                "scorecard_results.workspace_id",
                "scorecard_results.meeting_id",
            ],
            ondelete="CASCADE",
            name="fk_calibration_answers_result",
        ),
        # So an answer names a criterion of that result
        # (``uq_scorecard_criterion_result_criterion``).
        ForeignKeyConstraint(
            ["scorecard_result_id", "criterion_id"],
            ["scorecard_criterion_results.result_id", "scorecard_criterion_results.criterion_id"],
            ondelete="CASCADE",
            name="fk_calibration_answers_criterion",
        ),
        # One current answer per participant, example and criterion; answers may
        # change until close.
        UniqueConstraint(
            "participant_id", "example_id", "criterion_id", name="uq_calibration_answers_criterion"
        ),
        CheckConstraint(CALIBRATION_OUTCOME_CHECK, name="ck_calibration_answers_outcome"),
        CheckConstraint(CRITERION_KEY_CHECK, name="ck_calibration_answers_criterion_key"),
        # The purge and the backup reconciliation, by workspace and conversation.
        Index("ix_calibration_answers_workspace_meeting", "workspace_id", "meeting_id"),
    )


class CalibrationAdjudication(Base):
    """The correct outcome of one reviewable criterion of one example, recorded by a
    review admin who is not a participant of the batch (HOM-75, WP14-T04, F46).

    The adjudication is hidden from the participants until the batch closes.  The
    service of PR 2 upserts the row while the batch is open, so the table is not
    immutable.  The same composite foreign keys as the answers (minus the participant)
    tie the row to its example's batch and result and to a criterion of that result.
    Conversation content: the conversation purge deletes the row and the backup
    reconciliation counts it.  Tenant-only, never through a share token.
    """

    __tablename__ = "calibration_adjudications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # A copy of the example's result's conversation: the purge and the backup
    # reconciliation find content rows by ``meeting_id``.
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    # Its foreign key is ``fk_calibration_adjudications_example`` below.
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    example_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Its foreign keys are ``fk_calibration_adjudications_result`` and
    # ``fk_calibration_adjudications_criterion`` below.
    scorecard_result_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # The criterion key, as in ``scorecard_criterion_results.criterion_id``.
    criterion_id: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    # Free text from the adjudicator, at most the review note length.
    note: Mapped[str | None] = mapped_column(Text)
    decided_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # An adjudication names only its example's batch and result
        # (``uq_calibration_examples_scope``).
        ForeignKeyConstraint(
            ["example_id", "batch_id", "scorecard_result_id"],
            [
                "calibration_examples.id",
                "calibration_examples.batch_id",
                "calibration_examples.scorecard_result_id",
            ],
            ondelete="CASCADE",
            name="fk_calibration_adjudications_example",
        ),
        # So the workspace and the meeting copies are the result's own
        # (``uq_scorecard_result_scope``).
        ForeignKeyConstraint(
            ["scorecard_result_id", "workspace_id", "meeting_id"],
            [
                "scorecard_results.id",
                "scorecard_results.workspace_id",
                "scorecard_results.meeting_id",
            ],
            ondelete="CASCADE",
            name="fk_calibration_adjudications_result",
        ),
        # So an adjudication names a criterion of that result
        # (``uq_scorecard_criterion_result_criterion``).
        ForeignKeyConstraint(
            ["scorecard_result_id", "criterion_id"],
            ["scorecard_criterion_results.result_id", "scorecard_criterion_results.criterion_id"],
            ondelete="CASCADE",
            name="fk_calibration_adjudications_criterion",
        ),
        # One current adjudication per example and criterion.
        UniqueConstraint(
            "example_id", "criterion_id", name="uq_calibration_adjudications_criterion"
        ),
        CheckConstraint(CALIBRATION_OUTCOME_CHECK, name="ck_calibration_adjudications_outcome"),
        CheckConstraint(CRITERION_KEY_CHECK, name="ck_calibration_adjudications_criterion_key"),
        CheckConstraint(
            CALIBRATION_NOTE_LENGTH_CHECK, name="ck_calibration_adjudications_note_length"
        ),
        # The purge and the backup reconciliation, by workspace and conversation.
        Index("ix_calibration_adjudications_workspace_meeting", "workspace_id", "meeting_id"),
    )


# Agent appeals of review results (HOM-76, WP14-T05).  A status, a disposition, a
# criterion decision, an outcome and a reason code are codes from these frozen lists,
# never free text, so no personal data reaches the audit log.  The appealed outcome is
# one of the four machine outcomes (a criterion the reviewer did not decide keeps its
# machine outcome in the human result); the changed outcome is a reviewer outcome
# (``REVIEW_REVIEWER_OUTCOMES``); the change reason is an override reason
# (``REVIEW_OVERRIDE_REASONS``).
REVIEW_APPEAL_STATUSES: tuple[str, ...] = ("open", "decided", "withdrawn")
REVIEW_APPEAL_DISPOSITIONS: tuple[str, ...] = ("upheld", "partially_granted", "granted")
REVIEW_APPEAL_CRITERION_DECISIONS: tuple[str, ...] = ("uphold", "change")
REVIEW_APPEAL_MAX_CRITERIA = 20
REVIEW_APPEAL_WINDOW_DAYS = 14

REVIEW_APPEAL_STATUS_CHECK = f"status IN {_sql_list(REVIEW_APPEAL_STATUSES)}"
REVIEW_APPEAL_REVISION_CHECK = "revision >= 1"
# The agent's reason: not blank, at most the review note length.
REVIEW_APPEAL_REASON_NOTE_CHECK = (
    f"length(trim(reason_note)) > 0 AND length(reason_note) <= {REVIEW_NOTE_MAX_LENGTH}"
)
REVIEW_APPEAL_DISPOSITION_CHECK = (
    f"disposition IS NULL OR disposition IN {_sql_list(REVIEW_APPEAL_DISPOSITIONS)}"
)
REVIEW_APPEAL_DISPOSITION_NOTE_LENGTH_CHECK = (
    f"disposition_note IS NULL OR length(disposition_note) <= {REVIEW_NOTE_MAX_LENGTH}"
)
# Each status has its own fields.  A decided appeal needs a disposition and a non-blank
# note to the agent.  No check names a user column: the five user columns are SET NULL
# when a user is removed, and a check on them would make the removal fail.
REVIEW_APPEAL_OPEN_FIELDS_CHECK = (
    "status <> 'open' OR (disposition IS NULL AND disposition_note IS NULL "
    "AND decided_at IS NULL AND withdrawn_at IS NULL)"
)
REVIEW_APPEAL_DECIDED_FIELDS_CHECK = (
    "status <> 'decided' OR (disposition IS NOT NULL AND decided_at IS NOT NULL "
    "AND withdrawn_at IS NULL AND disposition_note IS NOT NULL "
    "AND length(trim(disposition_note)) > 0)"
)
REVIEW_APPEAL_WITHDRAWN_FIELDS_CHECK = (
    "status <> 'withdrawn' OR (withdrawn_at IS NOT NULL AND decided_at IS NULL "
    "AND disposition IS NULL AND disposition_note IS NULL)"
)
REVIEW_APPEAL_APPEALED_OUTCOME_CHECK = REVIEW_DECISION_MACHINE_OUTCOME_CHECK.replace(
    "machine_outcome", "appealed_outcome"
)
REVIEW_APPEAL_DECISION_CHECK = (
    f"decision IS NULL OR decision IN {_sql_list(REVIEW_APPEAL_CRITERION_DECISIONS)}"
)
REVIEW_APPEAL_DECIDED_OUTCOME_CHECK = (
    f"decided_outcome IS NULL OR decided_outcome IN {_sql_list(REVIEW_REVIEWER_OUTCOMES)}"
)
REVIEW_APPEAL_REASON_CODE_CHECK = REVIEW_DECISION_REASON_CODE_CHECK
REVIEW_APPEAL_NOTE_LENGTH_CHECK = REVIEW_DECISION_NOTE_LENGTH_CHECK
# A criterion row without a decision carries no decision fields.  A CHECK passes when
# its expression is NULL, so the uphold and change rules start with ``decision IS
# NULL OR`` and are false, never NULL, for a row they refuse.
REVIEW_APPEAL_UNDECIDED_CHECK = (
    "decision IS NOT NULL OR (decided_outcome IS NULL AND reason_code IS NULL AND note IS NULL)"
)
# An uphold keeps the human outcome: no new outcome and no reason code.  A note on an
# uphold is allowed.
REVIEW_APPEAL_UPHOLD_CHECK = (
    "decision IS NULL OR decision <> 'uphold' OR (decided_outcome IS NULL AND reason_code IS NULL)"
)
# A change needs a new outcome that differs from the appealed one, a reason code and a
# non-blank note.
REVIEW_APPEAL_CHANGE_CHECK = (
    "decision IS NULL OR decision <> 'change' OR (decided_outcome IS NOT NULL "
    "AND decided_outcome <> appealed_outcome AND reason_code IS NOT NULL "
    "AND note IS NOT NULL AND length(trim(note)) > 0)"
)


class ReviewAppeal(Base):
    """One appeal by the agent of a call against one completed review item that has
    a human result (HOM-76, WP14-T05, F46).

    The agent files at most one appeal per review item (``uq_review_appeals_item``),
    within ``REVIEW_APPEAL_WINDOW_DAYS`` of the review's completion, on 1 to
    ``REVIEW_APPEAL_MAX_CRITERIA`` criteria (the service of PR 2 enforces the window
    and the count).  A decider who is not a party to the call decides it.  The machine
    result and the human result are never written: a granted change lives in
    ``review_appeal_results``.  ``revision`` starts at 1 and grows by one with each
    assign, decide or withdraw, so a write is a compare-and-set on it, like
    ``review_items``.  Not immutable.  The composite keys tie the appeal to the result
    its item reviewed, to that result's own workspace and meeting, and to an item that
    has a human result.  Conversation content: the purge deletes the row and the
    backup reconciliation counts it.  Tenant-only, never through a share token.
    """

    __tablename__ = "review_appeals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # A copy of the item's conversation: the purge and the backup reconciliation find
    # content rows by ``meeting_id``.
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    # Its foreign keys are ``fk_review_appeals_item`` and
    # ``fk_review_appeals_review_result`` below.
    review_item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # The item's machine result.  Its foreign keys are ``fk_review_appeals_item`` and
    # ``fk_review_appeals_result`` below.
    scorecard_result_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # The agent who filed.
    appellant_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # A copy of the item's assignee at filing.
    original_reviewer_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    # Free text from the agent; never copied to the audit log.
    reason_note: Mapped[str] = mapped_column(Text, nullable=False)
    # The decider.
    assignee_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    assigned_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disposition: Mapped[str | None] = mapped_column(String(24))
    # Free text from the decider to the agent; never copied to the audit log.
    disposition_note: Mapped[str | None] = mapped_column(Text)
    decided_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (
        # An appeal names only the result its item reviewed (``uq_review_items_result``).
        ForeignKeyConstraint(
            ["review_item_id", "scorecard_result_id"],
            ["review_items.id", "review_items.scorecard_result_id"],
            ondelete="CASCADE",
            name="fk_review_appeals_item",
        ),
        # So the workspace and the meeting copies are the result's own
        # (``uq_scorecard_result_scope``).
        ForeignKeyConstraint(
            ["scorecard_result_id", "workspace_id", "meeting_id"],
            [
                "scorecard_results.id",
                "scorecard_results.workspace_id",
                "scorecard_results.meeting_id",
            ],
            ondelete="CASCADE",
            name="fk_review_appeals_result",
        ),
        # So an appeal exists only for an item that has a human result
        # (``uq_review_results_item``).
        ForeignKeyConstraint(
            ["review_item_id"],
            ["review_results.review_item_id"],
            ondelete="CASCADE",
            name="fk_review_appeals_review_result",
        ),
        # One appeal per review item, ever.
        UniqueConstraint("review_item_id", name="uq_review_appeals_item"),
        CheckConstraint(REVIEW_APPEAL_STATUS_CHECK, name="ck_review_appeals_status"),
        CheckConstraint(REVIEW_APPEAL_REVISION_CHECK, name="ck_review_appeals_revision"),
        CheckConstraint(REVIEW_APPEAL_REASON_NOTE_CHECK, name="ck_review_appeals_reason_note"),
        CheckConstraint(REVIEW_APPEAL_DISPOSITION_CHECK, name="ck_review_appeals_disposition"),
        CheckConstraint(
            REVIEW_APPEAL_DISPOSITION_NOTE_LENGTH_CHECK,
            name="ck_review_appeals_disposition_note_length",
        ),
        CheckConstraint(REVIEW_APPEAL_OPEN_FIELDS_CHECK, name="ck_review_appeals_open_fields"),
        CheckConstraint(
            REVIEW_APPEAL_DECIDED_FIELDS_CHECK, name="ck_review_appeals_decided_fields"
        ),
        CheckConstraint(
            REVIEW_APPEAL_WITHDRAWN_FIELDS_CHECK, name="ck_review_appeals_withdrawn_fields"
        ),
        # The target of the composite foreign keys of the criterion rows and the appeal
        # result: ``(id, review_item_id, scorecard_result_id)`` names exactly the appeal
        # with its item and result.
        Index(
            "uq_review_appeals_scope", "id", "review_item_id", "scorecard_result_id", unique=True
        ),
        # The appeals queue and its keyset pages.
        Index("ix_review_appeals_queue", "workspace_id", "status", "created_at", "id"),
        # The purge and the backup reconciliation, by workspace and conversation.
        Index("ix_review_appeals_workspace_meeting", "workspace_id", "meeting_id"),
    )


class ReviewAppealCriterion(Base):
    """One appealed criterion of one appeal, and later its decision (HOM-76,
    WP14-T05, F46).

    ``appealed_outcome`` is a copy of the criterion's outcome in the human result at
    filing.  The decision columns stay NULL until the appeal is decided; then the
    service of PR 2 writes them once: an uphold keeps the human outcome, a change
    names a new reviewer outcome with a reason code and a non-blank note.  Not
    immutable.  The composite keys tie the row to its appeal's item and result, to that
    result's own workspace and meeting, and to a criterion of that result.
    Conversation content: the purge deletes the row and the backup reconciliation
    counts it.  Tenant-only, never through a share token.
    """

    __tablename__ = "review_appeal_criteria"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # A copy of the result's conversation: the purge and the backup reconciliation find
    # content rows by ``meeting_id``.
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    # Its foreign key is ``fk_review_appeal_criteria_appeal`` below.
    appeal_id: Mapped[str] = mapped_column(String(36), nullable=False)
    review_item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Its foreign keys are ``fk_review_appeal_criteria_appeal``,
    # ``fk_review_appeal_criteria_result`` and ``fk_review_appeal_criteria_criterion``.
    scorecard_result_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # The criterion key, as in ``scorecard_criterion_results.criterion_id``.
    criterion_id: Mapped[str] = mapped_column(String(64), nullable=False)
    appealed_outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(16))
    decided_outcome: Mapped[str | None] = mapped_column(String(24))
    reason_code: Mapped[str | None] = mapped_column(String(32))
    # Free text from the decider; required for a change, allowed on an uphold.
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # A criterion row names only its appeal's item and result
        # (``uq_review_appeals_scope``).
        ForeignKeyConstraint(
            ["appeal_id", "review_item_id", "scorecard_result_id"],
            [
                "review_appeals.id",
                "review_appeals.review_item_id",
                "review_appeals.scorecard_result_id",
            ],
            ondelete="CASCADE",
            name="fk_review_appeal_criteria_appeal",
        ),
        # So the workspace and the meeting copies are the result's own
        # (``uq_scorecard_result_scope``).
        ForeignKeyConstraint(
            ["scorecard_result_id", "workspace_id", "meeting_id"],
            [
                "scorecard_results.id",
                "scorecard_results.workspace_id",
                "scorecard_results.meeting_id",
            ],
            ondelete="CASCADE",
            name="fk_review_appeal_criteria_result",
        ),
        # So the row names a criterion of that result
        # (``uq_scorecard_criterion_result_criterion``).
        ForeignKeyConstraint(
            ["scorecard_result_id", "criterion_id"],
            ["scorecard_criterion_results.result_id", "scorecard_criterion_results.criterion_id"],
            ondelete="CASCADE",
            name="fk_review_appeal_criteria_criterion",
        ),
        # One row per appeal and criterion.
        UniqueConstraint("appeal_id", "criterion_id", name="uq_review_appeal_criteria_criterion"),
        CheckConstraint(
            REVIEW_APPEAL_APPEALED_OUTCOME_CHECK,
            name="ck_review_appeal_criteria_appealed_outcome",
        ),
        CheckConstraint(REVIEW_APPEAL_DECISION_CHECK, name="ck_review_appeal_criteria_decision"),
        CheckConstraint(
            REVIEW_APPEAL_DECIDED_OUTCOME_CHECK,
            name="ck_review_appeal_criteria_decided_outcome",
        ),
        CheckConstraint(
            REVIEW_APPEAL_REASON_CODE_CHECK, name="ck_review_appeal_criteria_reason_code"
        ),
        CheckConstraint(
            REVIEW_APPEAL_NOTE_LENGTH_CHECK, name="ck_review_appeal_criteria_note_length"
        ),
        CheckConstraint(REVIEW_APPEAL_UNDECIDED_CHECK, name="ck_review_appeal_criteria_undecided"),
        CheckConstraint(REVIEW_APPEAL_UPHOLD_CHECK, name="ck_review_appeal_criteria_uphold"),
        CheckConstraint(REVIEW_APPEAL_CHANGE_CHECK, name="ck_review_appeal_criteria_change"),
        CheckConstraint(CRITERION_KEY_CHECK, name="ck_review_appeal_criteria_criterion_key"),
        # The purge and the backup reconciliation, by workspace and conversation.
        Index("ix_review_appeal_criteria_workspace_meeting", "workspace_id", "meeting_id"),
    )


class ReviewAppealResult(Base):
    """The score after a granted or partially granted appeal: the machine result
    scored again by the same engine with the human outcomes plus the changed ones
    (HOM-76, WP14-T05, F46).

    One row per appeal at most; only an appeal with at least one ``change`` has one.
    The columns copy ``review_results``: ``reviewer_outcomes`` is the engine input and
    ``result`` the engine output.  The machine result and the human result are never
    written.  Immutable, like ``review_results``: a stored appeal result never changes,
    and only the conversation purge removes rows (bulk deletes).  No user column: the
    table has no UPDATE grant, so a ``SET NULL`` action on a user column would need an
    update.  Tenant-only, never through a share token.
    """

    __tablename__ = "review_appeal_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    # Its foreign key is ``fk_review_appeal_results_appeal`` below.
    appeal_id: Mapped[str] = mapped_column(String(36), nullable=False)
    review_item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Its foreign keys are ``fk_review_appeal_results_appeal`` and
    # ``fk_review_appeal_results_scorecard_result`` below.
    scorecard_result_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # The calculation version the engine reported, for example ``scorecards/1``.
    scorecards_version: Mapped[str] = mapped_column(String(40), nullable=False)
    # The engine input: ``{criterion_id: outcome}``, the human outcomes plus the changes.
    reviewer_outcomes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(40))
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    pass_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    # The score denominator and numerator, and the other two parts of the denominator.
    applicable_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    earned_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    insufficient_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # The appeal result names only its appeal's item and result
        # (``uq_review_appeals_scope``).
        ForeignKeyConstraint(
            ["appeal_id", "review_item_id", "scorecard_result_id"],
            [
                "review_appeals.id",
                "review_appeals.review_item_id",
                "review_appeals.scorecard_result_id",
            ],
            ondelete="CASCADE",
            name="fk_review_appeal_results_appeal",
        ),
        ForeignKeyConstraint(
            ["scorecard_result_id", "workspace_id", "meeting_id"],
            [
                "scorecard_results.id",
                "scorecard_results.workspace_id",
                "scorecard_results.meeting_id",
            ],
            ondelete="CASCADE",
            name="fk_review_appeal_results_scorecard_result",
        ),
        UniqueConstraint("appeal_id", name="uq_review_appeal_results_appeal"),
        # First, so SQLite names it when an unknown outcome also breaks the reason rule.
        CheckConstraint(SCORECARD_OUTCOME_CHECK, name="ck_review_appeal_result_outcome"),
        CheckConstraint(SCORECARD_RESULT_REASON_CHECK, name="ck_review_appeal_result_reason"),
        CheckConstraint(SCORECARD_RESULT_THRESHOLD_CHECK, name="ck_review_appeal_result_threshold"),
        CheckConstraint(SCORECARD_RESULT_WEIGHTS_CHECK, name="ck_review_appeal_result_weights"),
        CheckConstraint(SCORECARD_RESULT_COMPLETE_CHECK, name="ck_review_appeal_result_complete"),
        # The purge and the backup reconciliation, by workspace and conversation.
        Index("ix_review_appeal_results_workspace_meeting", "workspace_id", "meeting_id"),
    )


# Coaching tasks from accepted findings (HOM-77, WP14-T06).  A status, a finding source,
# a follow-up outcome and a cancel reason are codes from these frozen lists, never free
# text, so no personal data reaches the audit log.  The finding source names the stored
# score that gave the ``failed`` outcome when the task was created.  Every note is at
# most ``REVIEW_NOTE_MAX_LENGTH`` characters.
COACHING_TASK_STATUSES: tuple[str, ...] = ("assigned", "acknowledged", "closed", "cancelled")
# ``closed`` and ``cancelled`` are terminal.
COACHING_TASK_ACTIVE_STATUSES: tuple[str, ...] = ("assigned", "acknowledged")
COACHING_FINDING_SOURCES: tuple[str, ...] = ("human_result", "appeal_result")
COACHING_FOLLOW_UP_OUTCOMES: tuple[str, ...] = ("improved", "partially_improved", "not_improved")
COACHING_CANCEL_REASONS: tuple[str, ...] = (
    "finding_overturned",
    "agent_changed",
    "duplicate",
    "other",
)
# The latest due date at creation, and how long before it the due-soon reminder goes
# out (the service and the reminder sweep of PR 2 use them; no check does).
COACHING_MAX_DUE_DAYS = 90
COACHING_REMINDER_LEAD_HOURS = 24

COACHING_TASK_STATUS_CHECK = f"status IN {_sql_list(COACHING_TASK_STATUSES)}"
COACHING_TASK_REVISION_CHECK = "revision >= 1"
COACHING_FINDING_SOURCE_CHECK = f"finding_source IN {_sql_list(COACHING_FINDING_SOURCES)}"
# What to practise: not blank, at most the review note length.
COACHING_TASK_INSTRUCTION_CHECK = (
    f"length(trim(instruction)) > 0 AND length(instruction) <= {REVIEW_NOTE_MAX_LENGTH}"
)
# The agent's optional reply exists only with the acknowledgement itself.
COACHING_ACKNOWLEDGEMENT_NOTE_CHECK = (
    f"acknowledgement_note IS NULL OR (length(acknowledgement_note) <= {REVIEW_NOTE_MAX_LENGTH} "
    "AND acknowledged_at IS NOT NULL)"
)
COACHING_FOLLOW_UP_OUTCOME_CHECK = (
    f"follow_up_outcome IS NULL OR follow_up_outcome IN {_sql_list(COACHING_FOLLOW_UP_OUTCOMES)}"
)
COACHING_FOLLOW_UP_NOTE_LENGTH_CHECK = (
    f"follow_up_note IS NULL OR length(follow_up_note) <= {REVIEW_NOTE_MAX_LENGTH}"
)
COACHING_CANCEL_REASON_CHECK = (
    f"cancel_reason IS NULL OR cancel_reason IN {_sql_list(COACHING_CANCEL_REASONS)}"
)
COACHING_CANCEL_NOTE_LENGTH_CHECK = (
    f"cancel_note IS NULL OR length(cancel_note) <= {REVIEW_NOTE_MAX_LENGTH}"
)
# Each status has its own fields.  A follow-up review needs an acknowledged task, an
# outcome and a non-blank note; a cancelled task may or may not have been acknowledged.
# A CHECK passes when its expression is NULL, so each rule is false, never NULL, for a
# row it refuses.  No check names a user column: the five user columns are SET NULL
# when a user is removed, and a check on them would make the removal fail.
COACHING_TASK_ASSIGNED_FIELDS_CHECK = (
    "status <> 'assigned' OR (acknowledged_at IS NULL AND follow_up_outcome IS NULL "
    "AND follow_up_note IS NULL AND closed_at IS NULL AND cancel_reason IS NULL "
    "AND cancel_note IS NULL AND cancelled_at IS NULL)"
)
COACHING_TASK_ACKNOWLEDGED_FIELDS_CHECK = (
    "status <> 'acknowledged' OR (acknowledged_at IS NOT NULL AND follow_up_outcome "
    "IS NULL AND follow_up_note IS NULL AND closed_at IS NULL AND cancel_reason "
    "IS NULL AND cancel_note IS NULL AND cancelled_at IS NULL)"
)
COACHING_TASK_CLOSED_FIELDS_CHECK = (
    "status <> 'closed' OR (acknowledged_at IS NOT NULL AND closed_at IS NOT NULL "
    "AND follow_up_outcome IS NOT NULL AND follow_up_note IS NOT NULL "
    "AND length(trim(follow_up_note)) > 0 AND cancel_reason IS NULL "
    "AND cancel_note IS NULL AND cancelled_at IS NULL)"
)
COACHING_TASK_CANCELLED_FIELDS_CHECK = (
    "status <> 'cancelled' OR (cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL "
    "AND closed_at IS NULL AND follow_up_outcome IS NULL AND follow_up_note IS NULL)"
)
COACHING_TASK_ACTIVE_WHERE = f"status IN {_sql_list(COACHING_TASK_ACTIVE_STATUSES)}"


class CoachingTask(Base):
    """One coaching task from one accepted finding (HOM-77, WP14-T06, F47).

    A task names exactly one finding: one criterion of one completed review item that
    has a human result, whose final outcome is ``failed``.  The coachee
    (``agent_user_id``) is the call's agent when the task is created and never
    changes; the owner (``owner_user_id``) is the coach who does the follow-up review.
    The coachee acknowledges, the owner closes the task with an outcome, and a
    supervisor may cancel it.  At most one active (``assigned`` or ``acknowledged``)
    task per finding (``uq_coaching_tasks_active_finding``); a closed or cancelled one
    does not block a new one.  ``revision`` starts at 1 and grows by one with each
    acknowledge, reassign, close or cancel, so a write is a compare-and-set on it, like
    ``review_items``.  The reminder sweep sets the two ``*_notified_at`` columns once
    each.  Not immutable.  The composite keys tie the task to the result its item
    reviewed, to that result's own workspace and meeting, to an item that has a human
    result and to a criterion of that result.  Conversation content: the purge deletes
    the row and the backup reconciliation counts it.  Tenant-only, never through a
    share token.
    """

    __tablename__ = "coaching_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # A copy of the item's conversation: the purge and the backup reconciliation find
    # content rows by ``meeting_id``.
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    # Its foreign keys are ``fk_coaching_tasks_item`` and
    # ``fk_coaching_tasks_review_result`` below.
    review_item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # The item's machine result.  Its foreign keys are ``fk_coaching_tasks_item``,
    # ``fk_coaching_tasks_result`` and ``fk_coaching_tasks_criterion`` below.
    scorecard_result_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # The finding: a criterion key of that result, as in
    # ``scorecard_criterion_results.criterion_id``.
    criterion_id: Mapped[str] = mapped_column(String(64), nullable=False)
    finding_source: Mapped[str] = mapped_column(String(20), nullable=False)
    # The coachee: the call's agent at creation, never changed.
    agent_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # The coach, who does the follow-up review.
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Free text from the supervisor: what to practise.  Never copied to the audit log.
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="assigned", nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The agent's optional reply; never copied to the audit log.
    acknowledgement_note: Mapped[str | None] = mapped_column(Text)
    follow_up_outcome: Mapped[str | None] = mapped_column(String(30))
    # Free text from the owner's follow-up review; never copied to the audit log.
    follow_up_note: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    cancel_reason: Mapped[str | None] = mapped_column(String(30))
    # Free text with a cancel; never copied to the audit log.
    cancel_note: Mapped[str | None] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # The due-soon reminder and the overdue notice were sent (the sweep of PR 2).
    reminder_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    overdue_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # A task names only the result its item reviewed (``uq_review_items_result``).
        ForeignKeyConstraint(
            ["review_item_id", "scorecard_result_id"],
            ["review_items.id", "review_items.scorecard_result_id"],
            ondelete="CASCADE",
            name="fk_coaching_tasks_item",
        ),
        # So the workspace and the meeting copies are the result's own
        # (``uq_scorecard_result_scope``).
        ForeignKeyConstraint(
            ["scorecard_result_id", "workspace_id", "meeting_id"],
            [
                "scorecard_results.id",
                "scorecard_results.workspace_id",
                "scorecard_results.meeting_id",
            ],
            ondelete="CASCADE",
            name="fk_coaching_tasks_result",
        ),
        # So a task exists only for an item that has a human result
        # (``uq_review_results_item``).
        ForeignKeyConstraint(
            ["review_item_id"],
            ["review_results.review_item_id"],
            ondelete="CASCADE",
            name="fk_coaching_tasks_review_result",
        ),
        # So the task names a criterion of that result
        # (``uq_scorecard_criterion_result_criterion``).
        ForeignKeyConstraint(
            ["scorecard_result_id", "criterion_id"],
            ["scorecard_criterion_results.result_id", "scorecard_criterion_results.criterion_id"],
            ondelete="CASCADE",
            name="fk_coaching_tasks_criterion",
        ),
        CheckConstraint(COACHING_TASK_STATUS_CHECK, name="ck_coaching_tasks_status"),
        CheckConstraint(COACHING_TASK_REVISION_CHECK, name="ck_coaching_tasks_revision"),
        CheckConstraint(COACHING_FINDING_SOURCE_CHECK, name="ck_coaching_tasks_finding_source"),
        CheckConstraint(COACHING_TASK_INSTRUCTION_CHECK, name="ck_coaching_tasks_instruction"),
        CheckConstraint(
            COACHING_ACKNOWLEDGEMENT_NOTE_CHECK, name="ck_coaching_tasks_acknowledgement_note"
        ),
        CheckConstraint(
            COACHING_FOLLOW_UP_OUTCOME_CHECK, name="ck_coaching_tasks_follow_up_outcome"
        ),
        CheckConstraint(
            COACHING_FOLLOW_UP_NOTE_LENGTH_CHECK, name="ck_coaching_tasks_follow_up_note_length"
        ),
        CheckConstraint(COACHING_CANCEL_REASON_CHECK, name="ck_coaching_tasks_cancel_reason"),
        CheckConstraint(
            COACHING_CANCEL_NOTE_LENGTH_CHECK, name="ck_coaching_tasks_cancel_note_length"
        ),
        CheckConstraint(
            COACHING_TASK_ASSIGNED_FIELDS_CHECK, name="ck_coaching_tasks_assigned_fields"
        ),
        CheckConstraint(
            COACHING_TASK_ACKNOWLEDGED_FIELDS_CHECK, name="ck_coaching_tasks_acknowledged_fields"
        ),
        CheckConstraint(COACHING_TASK_CLOSED_FIELDS_CHECK, name="ck_coaching_tasks_closed_fields"),
        CheckConstraint(
            COACHING_TASK_CANCELLED_FIELDS_CHECK, name="ck_coaching_tasks_cancelled_fields"
        ),
        CheckConstraint(CRITERION_KEY_CHECK, name="ck_coaching_tasks_criterion_key"),
        # At most one active task per finding; a closed or cancelled one does not block
        # a new one.
        Index(
            "uq_coaching_tasks_active_finding",
            "review_item_id",
            "criterion_id",
            unique=True,
            postgresql_where=text(COACHING_TASK_ACTIVE_WHERE),
            sqlite_where=text(COACHING_TASK_ACTIVE_WHERE),
        ),
        # The supervisor list (earliest due first) and its keyset pages.
        Index("ix_coaching_tasks_queue", "workspace_id", "status", "due_at", "id"),
        # The agent's own list and its keyset pages.
        Index("ix_coaching_tasks_agent", "workspace_id", "agent_user_id", "created_at", "id"),
        # The reminder sweep.
        Index("ix_coaching_tasks_due", "status", "due_at"),
        # The purge and the backup reconciliation, by workspace and conversation.
        Index("ix_coaching_tasks_workspace_meeting", "workspace_id", "meeting_id"),
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


# The retrieval index's dialect-specific index expressions (HOM-93).  They are
# defined at module level because ``text`` is also a column name on these tables,
# so the sqlalchemy ``text()`` wrapper cannot be called inside the class body.
RETRIEVAL_LIVE_WHERE = text("superseded_at IS NULL")
RETRIEVAL_FTS_EXPRESSION = text("to_tsvector('simple', text)")


class RetrievalEntry(Base):
    """One indexed text-view segment of one revision (HOM-93, WP17-T02, F57).

    The lexical unit of the retrieval index: one row per ``text_view_segments``
    row under one ``index_version``.  ``text`` is a copy of the segment's
    normalized text (ADR 0034), already redacted when the workspace enforces a
    redaction policy, so an index row never holds text a default reader could
    not see.  ``team_id``, ``agent_user_id`` and ``created_by_user_id`` are a
    snapshot of the record ACL (the exact fields of
    ``RecordAccess.meeting_clause``) taken at ``acl_synced_at``: a pre-filter
    only, never the authority.  Every read of PR 2 joins the live ``meetings``
    row and applies the live record access (HOM-94), so deletion and
    re-attribution act at once, before any reindex.  ``superseded_at`` marks
    entries of a revision that stopped being current; NULL means the entry's
    revision was current when indexed.  Not immutable: PR 2 updates only
    ``superseded_at`` and the ACL snapshot columns.  Conversation content: the
    purge deletes the row and the backup reconciliation counts it.
    Tenant-only, never through a share token.
    """

    __tablename__ = "retrieval_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # A copy of the segment's conversation: the purge and the backup
    # reconciliation find content rows by ``meeting_id``.
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_revisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text_view_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_text_views.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text_view_segment_id: Mapped[str] = mapped_column(
        ForeignKey("text_view_segments.id", ondelete="CASCADE"), nullable=False
    )
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_segments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The segment's order inside the view.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    # A copy of the revision's number, so listing entries of one revision never
    # joins the revision row.
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str] = mapped_column(String(12), nullable=False)
    # The normalized lexical text (a copy of ``text_view_segments.text``).
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Names the index layout and rules, e.g. ``retrieval-index/1``: a change in
    # what is indexed writes new rows, never over the old ones.
    index_version: Mapped[str] = mapped_column(String(40), nullable=False)
    # The ACL snapshot of ``Meeting.team_id``, ``Meeting.agent_user_id`` and
    # ``Meeting.created_by_user_id``: SET NULL when the team or the user goes.
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    agent_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    acl_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # NULL: the entry's revision was current when indexed.
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "text_view_segment_id", "index_version", name="uq_retrieval_entry_segment_version"
        ),
        CheckConstraint("sequence >= 0", name="ck_retrieval_entry_sequence"),
        CheckConstraint("revision_number >= 1", name="ck_retrieval_entry_revision_number"),
        CheckConstraint("length(text_sha256) = 64", name="ck_retrieval_entry_text_sha256"),
        Index("ix_retrieval_entry_workspace_meeting", "workspace_id", "meeting_id"),
        # The live entries of one workspace and index version (the NULL check of
        # ``superseded_at`` must survive on both dialects).
        Index(
            "ix_retrieval_entry_live",
            "workspace_id",
            "index_version",
            postgresql_where=RETRIEVAL_LIVE_WHERE,
            sqlite_where=RETRIEVAL_LIVE_WHERE,
        ),
        # PostgreSQL full-text on the normalized text, the ``simple``
        # configuration (the normalizer of ADR 0034 already did the language
        # work).  SQLite (dev and tests) has no full-text index; the migration
        # creates this index on PostgreSQL only, with the same expression.
        Index(
            "ix_retrieval_entry_text_fts",
            RETRIEVAL_FTS_EXPRESSION,
            postgresql_using="gin",
        ).ddl_if(dialect="postgresql"),
    )


class RetrievalEmbedding(Base):
    """One versioned embedding vector of one indexed entry (HOM-93, WP17-T02, F58).

    ``postgres:17-alpine`` has no pgvector, so the vector lives as portable
    bytes: float32 little-endian, ``4 * dimensions`` bytes.  ``embedding_version``
    is the sha256 of a canonical JSON of the provider name, the model, the
    dimensions and the index version only (ADR 0068 D4).  The provider params
    (the endpoint URL) are not hashed, so moving the embedding server keeps
    every vector valid.  A new model or a new index version never overwrites
    an old vector, it writes a new row.  Written once, never
    updated (the application role holds no UPDATE grant); a reader of PR 2
    scores candidates inside one workspace through a ``VectorStore`` adapter.
    Conversation content: the purge deletes the row and the backup
    reconciliation counts it.  Tenant-only, never through a share token.
    """

    __tablename__ = "retrieval_embeddings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # A copy of the entry's conversation: the purge and the backup
    # reconciliation find content rows by ``meeting_id``.
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entry_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # sha256 hex that names the embedding space (ADR 0068, decision D4).
    embedding_version: Mapped[str] = mapped_column(String(64), nullable=False)
    # From ``ProviderIdentity.provider`` and ``ProviderIdentity.model``.
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    # float32 little-endian, ``4 * dimensions`` bytes.
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # sha256 of the exact text that was embedded.
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "entry_id", "embedding_version", name="uq_retrieval_embedding_entry_version"
        ),
        CheckConstraint(
            "dimensions >= 1 AND dimensions <= 4096", name="ck_retrieval_embedding_dimensions"
        ),
        CheckConstraint(
            "length(vector) = 4 * dimensions", name="ck_retrieval_embedding_vector_size"
        ),
        CheckConstraint("length(text_sha256) = 64", name="ck_retrieval_embedding_text_sha256"),
        CheckConstraint("length(embedding_version) = 64", name="ck_retrieval_embedding_version"),
        Index("ix_retrieval_embedding_workspace_version", "workspace_id", "embedding_version"),
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
    # HOM-133 (ADR 0058): "commitment" or "next_step" for AI rows written from
    # HOM-133 on.  Person rows, voicemail rule rows and older rows stay NULL.
    kind: Mapped[str | None] = mapped_column(String(16))

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
        CheckConstraint(
            "kind IS NULL OR kind IN ('commitment', 'next_step')",
            name="ck_action_kind",
        ),
    )


# Approval-bound outbound actions (HOM-135, WP24-T03, F60, ADR 0082).  A proposal
# previews one exact payload; a person approves, rejects or revokes it.  An approval
# writes one immutable delivery request that HOM-136 delivers.
ACTION_PROPOSAL_TYPES: tuple[str, ...] = ("crm_update", "ticket_create", "coaching_task")
ACTION_PROPOSAL_SOURCES: tuple[str, ...] = ("action_item", "review_finding")
ACTION_PROPOSAL_STATUSES: tuple[str, ...] = ("pending", "approved", "rejected", "revoked")
# Open proposals: at most one per action item and action type, and one per finding.
ACTION_PROPOSAL_OPEN_STATUSES: tuple[str, ...] = ("pending", "approved")
ACTION_PROPOSAL_REJECT_REASONS: tuple[str, ...] = (
    "not_needed",
    "incorrect",
    "duplicate",
    "other",
)
ACTION_PROPOSAL_REVOKE_REASONS: tuple[str, ...] = ("approved_in_error", "no_longer_needed", "other")


def _lowercase_hex_check(column: str) -> str:
    """Exactly 64 characters of lowercase hex (a sha256 digest) without a regex, so
    SQLite runs the same check."""
    return f"length({column}) = 64 AND trim({column}, '0123456789abcdef') = ''"


ACTION_TYPE_CHECK = f"action_type IN {_sql_list(ACTION_PROPOSAL_TYPES)}"
ACTION_SOURCE_TYPE_CHECK = f"source_type IN {_sql_list(ACTION_PROPOSAL_SOURCES)}"
ACTION_PROPOSAL_STATUS_CHECK = f"status IN {_sql_list(ACTION_PROPOSAL_STATUSES)}"
ACTION_PROPOSAL_REVISION_CHECK = "revision >= 1"
ACTION_PAYLOAD_VERSION_CHECK = "payload_version >= 1"
ACTION_PROPOSAL_CRITERION_KEY_CHECK = f"criterion_id IS NULL OR ({CRITERION_KEY_CHECK})"
ACTION_REQUEST_CRITERION_KEY_CHECK = (
    "source_criterion_id IS NULL OR "
    f"({SCORECARD_KEY_CHECK.replace('scorecard_key', 'source_criterion_id')})"
)
ACTION_TARGET_SYSTEM_CHECK = f"target_system IS NULL OR ({_slug_check('target_system')})"
# The cross-field rules.  Each one passes when a value that another check owns is
# outside its list, so a row that breaks one field fails only that field's own named
# check, on both databases (PostgreSQL tests checks in name order, SQLite in its own).
# ``action_type``, ``source_type`` and ``status`` are NOT NULL, so no rule is ever
# NULL.  A source id (``action_item_id``, ``review_item_id``) may become NULL when
# its row is removed; PR 2 treats that proposal as stale.
ACTION_PROPOSAL_SOURCE_SHAPE_CHECK = (
    "(source_type <> 'action_item' OR (review_item_id IS NULL AND criterion_id IS NULL)) "
    "AND (source_type <> 'review_finding' OR (action_item_id IS NULL "
    "AND criterion_id IS NOT NULL))"
)
ACTION_TYPE_SOURCE_CHECK = (
    "NOT ((action_type IN ('crm_update','ticket_create') AND source_type = 'review_finding') "
    "OR (action_type = 'coaching_task' AND source_type = 'action_item'))"
)
ACTION_TARGET_SHAPE_CHECK = (
    "(action_type <> 'crm_update' OR (target_system IS NOT NULL "
    "AND target_external_record_id IS NOT NULL)) "
    "AND (action_type <> 'ticket_create' OR (target_system IS NOT NULL "
    "AND target_external_record_id IS NULL)) "
    "AND (action_type <> 'coaching_task' OR (target_system IS NULL "
    "AND target_external_record_id IS NULL))"
)
ACTION_PROPOSAL_REJECT_REASON_CHECK = (
    f"reject_reason IS NULL OR reject_reason IN {_sql_list(ACTION_PROPOSAL_REJECT_REASONS)}"
)
ACTION_PROPOSAL_REVOKE_REASON_CHECK = (
    f"revoke_reason IS NULL OR revoke_reason IN {_sql_list(ACTION_PROPOSAL_REVOKE_REASONS)}"
)
# Each status has its own fields.  No check names a user column: the three user
# columns are SET NULL when a user is removed, and a check on them would make the
# removal fail.
ACTION_PROPOSAL_PENDING_FIELDS_CHECK = (
    "status <> 'pending' OR (decided_at IS NULL AND reject_reason IS NULL "
    "AND revoked_at IS NULL AND revoke_reason IS NULL)"
)
ACTION_PROPOSAL_APPROVED_FIELDS_CHECK = (
    "status <> 'approved' OR (decided_at IS NOT NULL AND reject_reason IS NULL "
    "AND revoked_at IS NULL AND revoke_reason IS NULL)"
)
ACTION_PROPOSAL_REJECTED_FIELDS_CHECK = (
    "status <> 'rejected' OR (decided_at IS NOT NULL AND reject_reason IS NOT NULL "
    "AND revoked_at IS NULL AND revoke_reason IS NULL)"
)
ACTION_PROPOSAL_REVOKED_FIELDS_CHECK = (
    "status <> 'revoked' OR (decided_at IS NOT NULL AND revoked_at IS NOT NULL "
    "AND revoke_reason IS NOT NULL AND reject_reason IS NULL)"
)
ACTION_PROPOSAL_OPEN_WHERE = f"status IN {_sql_list(ACTION_PROPOSAL_OPEN_STATUSES)}"
ACTION_PROPOSAL_OPEN_ITEM_WHERE = f"{ACTION_PROPOSAL_OPEN_WHERE} AND action_item_id IS NOT NULL"
ACTION_PROPOSAL_OPEN_FINDING_WHERE = f"{ACTION_PROPOSAL_OPEN_WHERE} AND review_item_id IS NOT NULL"


class ActionProposal(Base):
    """One proposed outbound action and its exact payload (HOM-135, WP24-T03, F60).

    A proposal is built from one source: an action item (``crm_update``,
    ``ticket_create``) or a review finding, one criterion of a review item
    (``coaching_task``).  It pins that source: the transcript revision it was read from
    (``source_transcript_revision_id``) and a sha256 digest of the source fields
    (``source_digest``).  It stores the exact payload it would send
    (``payload_json``), its sha256 (``payload_hash``) and ``payload_version`` (1, and
    one more on every edit or refresh).  ``revision`` starts at 1 and grows by one with
    each state change, so a write is a compare-and-set on it, like
    ``coaching_tasks``.  An approval (PR 2) writes one ``action_delivery_requests``
    row; a revoke changes only the proposal.  At most one open (``pending`` or
    ``approved``) proposal per action item and action type, and per finding.  A
    removed source leaves its id NULL; PR 2 treats that proposal as stale.
    Conversation content: the purge deletes the row and the backup reconciliation
    counts it.  Tenant-only, never through a share token.
    """

    __tablename__ = "action_proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The source's conversation: the purge and the backup reconciliation find content
    # rows by ``meeting_id``, and the record scope of PR 2 is this meeting's.
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # The source.  The single-column keys do not tie it to this workspace and meeting;
    # the service of PR 2 checks that before the insert (ADR 0082).
    action_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("action_items.id", ondelete="SET NULL"), index=True
    )
    review_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("review_items.id", ondelete="SET NULL"), index=True
    )
    # The finding's criterion key, as in ``scorecard_criterion_results.criterion_id``.
    criterion_id: Mapped[str | None] = mapped_column(String(64))
    source_transcript_revision_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_revisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    # The slug of the target system; NULL for an internal coaching task.
    target_system: Mapped[str | None] = mapped_column(String(60))
    # The CRM record a ``crm_update`` writes to (HOM-88).  RESTRICT: a named record
    # cannot be deleted under a proposal.
    target_external_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("external_records.id", ondelete="RESTRICT"), index=True
    )
    # The exact payload; never copied to the audit log.
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Who approved or rejected it.
    decided_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    revoked_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(String(30))
    revoke_reason: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # The target of the delivery request's composite key, so a request stays in its
        # proposal's workspace and meeting.
        UniqueConstraint("id", "workspace_id", "meeting_id", name="uq_action_proposals_scope"),
        CheckConstraint(ACTION_TYPE_CHECK, name="ck_action_proposals_action_type"),
        CheckConstraint(ACTION_SOURCE_TYPE_CHECK, name="ck_action_proposals_source_type"),
        CheckConstraint(ACTION_PROPOSAL_STATUS_CHECK, name="ck_action_proposals_status"),
        CheckConstraint(ACTION_PROPOSAL_REVISION_CHECK, name="ck_action_proposals_revision"),
        CheckConstraint(ACTION_PAYLOAD_VERSION_CHECK, name="ck_action_proposals_payload_version"),
        CheckConstraint(
            _lowercase_hex_check("payload_hash"), name="ck_action_proposals_payload_hash"
        ),
        CheckConstraint(
            _lowercase_hex_check("source_digest"), name="ck_action_proposals_source_digest"
        ),
        CheckConstraint(
            ACTION_PROPOSAL_CRITERION_KEY_CHECK, name="ck_action_proposals_criterion_key"
        ),
        CheckConstraint(ACTION_TARGET_SYSTEM_CHECK, name="ck_action_proposals_target_system"),
        CheckConstraint(
            ACTION_PROPOSAL_SOURCE_SHAPE_CHECK, name="ck_action_proposals_source_shape"
        ),
        CheckConstraint(ACTION_TYPE_SOURCE_CHECK, name="ck_action_proposals_type_source"),
        CheckConstraint(ACTION_TARGET_SHAPE_CHECK, name="ck_action_proposals_target_shape"),
        CheckConstraint(
            ACTION_PROPOSAL_REJECT_REASON_CHECK, name="ck_action_proposals_reject_reason"
        ),
        CheckConstraint(
            ACTION_PROPOSAL_REVOKE_REASON_CHECK, name="ck_action_proposals_revoke_reason"
        ),
        CheckConstraint(
            ACTION_PROPOSAL_PENDING_FIELDS_CHECK, name="ck_action_proposals_pending_fields"
        ),
        CheckConstraint(
            ACTION_PROPOSAL_APPROVED_FIELDS_CHECK, name="ck_action_proposals_approved_fields"
        ),
        CheckConstraint(
            ACTION_PROPOSAL_REJECTED_FIELDS_CHECK, name="ck_action_proposals_rejected_fields"
        ),
        CheckConstraint(
            ACTION_PROPOSAL_REVOKED_FIELDS_CHECK, name="ck_action_proposals_revoked_fields"
        ),
        # At most one open proposal per action item and action type, and per finding.
        # A rejected or revoked one does not block a new one; a removed source never
        # blocks.
        Index(
            "uq_action_proposals_open_item",
            "action_item_id",
            "action_type",
            unique=True,
            postgresql_where=text(ACTION_PROPOSAL_OPEN_ITEM_WHERE),
            sqlite_where=text(ACTION_PROPOSAL_OPEN_ITEM_WHERE),
        ),
        Index(
            "uq_action_proposals_open_finding",
            "review_item_id",
            "criterion_id",
            unique=True,
            postgresql_where=text(ACTION_PROPOSAL_OPEN_FINDING_WHERE),
            sqlite_where=text(ACTION_PROPOSAL_OPEN_FINDING_WHERE),
        ),
        # The approval queue (oldest first) and its keyset pages.
        Index("ix_action_proposals_queue", "workspace_id", "status", "created_at", "id"),
        # The purge and the backup reconciliation, by workspace and conversation.
        Index("ix_action_proposals_workspace_meeting", "workspace_id", "meeting_id"),
    )


class ActionDeliveryRequest(Base):
    """One approved action, waiting for delivery (HOM-135, WP24-T03, F60).

    Written once, by the approval of PR 2, in the same transaction as the proposal's
    move to ``approved``; immutable after the insert.  It is an exact copy of what was
    approved: the payload, its hash and version, the source pins, the target and the
    approver.  The source columns are plain strings, not keys, so the copy keeps them
    when the source goes.  One request per proposal
    (``uq_action_delivery_requests_proposal``), always in the proposal's own workspace
    and meeting (``fk_action_delivery_requests_proposal``).  ``idempotency_key`` is the
    sha256 of ``workspace_id|proposal_id|payload_hash``; HOM-136 sends it to the
    connector.  A revoke changes the proposal, never this row: HOM-136 locks the
    proposal row and checks its status before any external write.  The application
    role may not UPDATE the table (``NO_UPDATE_TABLES``) and the ORM guard below
    refuses an update.  Conversation content: the purge deletes the row and the backup
    reconciliation counts it.  Tenant-only, never through a share token.
    """

    __tablename__ = "action_delivery_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    # Its foreign key is ``fk_action_delivery_requests_proposal`` below.
    proposal_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_system: Mapped[str | None] = mapped_column(String(60))
    target_external_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("external_records.id", ondelete="RESTRICT"), index=True
    )
    # The approved payload; never copied to the audit log.
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # The action item id or the review item id at approval: a copy, no key.
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_criterion_id: Mapped[str | None] = mapped_column(String(64))
    # The pinned revision at approval: a copy, no key.
    source_transcript_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # A request stays in its proposal's own workspace and meeting
        # (``uq_action_proposals_scope``).
        ForeignKeyConstraint(
            ["proposal_id", "workspace_id", "meeting_id"],
            [
                "action_proposals.id",
                "action_proposals.workspace_id",
                "action_proposals.meeting_id",
            ],
            ondelete="CASCADE",
            name="fk_action_delivery_requests_proposal",
        ),
        UniqueConstraint("proposal_id", name="uq_action_delivery_requests_proposal"),
        UniqueConstraint("idempotency_key", name="uq_action_delivery_requests_idempotency_key"),
        # For the delivery records of HOM-136.
        UniqueConstraint(
            "id", "workspace_id", "meeting_id", name="uq_action_delivery_requests_scope"
        ),
        CheckConstraint(ACTION_TYPE_CHECK, name="ck_action_delivery_requests_action_type"),
        CheckConstraint(ACTION_SOURCE_TYPE_CHECK, name="ck_action_delivery_requests_source_type"),
        CheckConstraint(
            ACTION_PAYLOAD_VERSION_CHECK, name="ck_action_delivery_requests_payload_version"
        ),
        CheckConstraint(
            _lowercase_hex_check("payload_hash"), name="ck_action_delivery_requests_payload_hash"
        ),
        CheckConstraint(
            _lowercase_hex_check("source_digest"),
            name="ck_action_delivery_requests_source_digest",
        ),
        CheckConstraint(
            _lowercase_hex_check("idempotency_key"),
            name="ck_action_delivery_requests_idempotency_key",
        ),
        CheckConstraint(
            ACTION_REQUEST_CRITERION_KEY_CHECK, name="ck_action_delivery_requests_criterion_key"
        ),
        CheckConstraint(ACTION_TYPE_SOURCE_CHECK, name="ck_action_delivery_requests_type_source"),
        CheckConstraint(ACTION_TARGET_SHAPE_CHECK, name="ck_action_delivery_requests_target_shape"),
        # The purge and the backup reconciliation, by workspace and conversation.
        Index("ix_action_delivery_requests_workspace_meeting", "workspace_id", "meeting_id"),
        # The delivery list of HOM-136 (oldest approval first) and its keyset pages.
        Index("ix_action_delivery_requests_approved", "workspace_id", "approved_at", "id"),
    )


# Product alert rules on calls (HOM-134, WP24-T02, F59, ADR 0085).  A rule names one
# condition; an event is one occurrence of that condition on one call; an alert groups
# the events of one rule and one dedupe group inside one deduplication window; a
# delivery is one message to one user for one purpose; a preference holds the digest,
# e-mail and mute choices of one user.  PR 1 is schema only: nothing writes the tables.
ALERT_CONDITION_TYPES: tuple[str, ...] = (
    "qa_criterion_failed",
    "risk_finding",
    "commitment_missed",
)
ALERT_SEVERITIES: tuple[str, ...] = ("info", "warning", "critical")
ALERT_DELIVERY_MODES: tuple[str, ...] = ("immediate", "digest_only")
# Used by the PR 2 validation of a rule's recipients; no column check names it.
ALERT_RECIPIENT_ROLES: tuple[str, ...] = ("owner", "admin", "supervisor")
ALERT_STATUSES: tuple[str, ...] = ("open", "acknowledged", "suppressed", "escalated")
# Acknowledged and suppressed are final; open and escalated still wait for a person.
ALERT_ACTIVE_STATUSES: tuple[str, ...] = ("open", "escalated")
ALERT_SUPPRESS_REASONS: tuple[str, ...] = (
    "false_positive",
    "known_issue",
    "duplicate",
    "other",
    "inherited",
)
ALERT_ESCALATION_REASONS: tuple[str, ...] = ("manual", "unacknowledged")
ALERT_EVENT_SOURCES: tuple[str, ...] = ("review_result", "analysis_run", "action_item")
ALERT_DELIVERY_PURPOSES: tuple[str, ...] = ("alert", "escalation", "digest")
ALERT_DELIVERY_CHANNELS: tuple[str, ...] = ("in_app", "email")
ALERT_DELIVERY_STATUSES: tuple[str, ...] = ("pending", "sent", "failed", "skipped")
ALERT_DELIVERY_REASONS: tuple[str, ...] = (
    "not_authorized",
    "muted",
    "digest_disabled",
    "email_missing",
    "email_send_failed",
)

ALERT_RULE_NAME_CHECK = "length(trim(name)) >= 1"
ALERT_CONDITION_TYPE_CHECK = f"condition_type IN {_sql_list(ALERT_CONDITION_TYPES)}"
ALERT_SEVERITY_CHECK = f"severity IN {_sql_list(ALERT_SEVERITIES)}"
ALERT_DELIVERY_MODE_CHECK = f"delivery_mode IN {_sql_list(ALERT_DELIVERY_MODES)}"
ALERT_RULE_CRITERION_KEY_CHECK = f"criterion_id IS NULL OR ({CRITERION_KEY_CHECK})"
ALERT_RULE_CONDITION_SHAPE_CHECK = (
    "condition_type NOT IN ('risk_finding','commitment_missed') OR criterion_id IS NULL"
)
ALERT_RULE_DEDUP_WINDOW_CHECK = "dedup_window_minutes >= 5 AND dedup_window_minutes <= 10080"
ALERT_RULE_ESCALATE_AFTER_CHECK = (
    "escalate_after_minutes IS NULL OR "
    "(escalate_after_minutes >= 5 AND escalate_after_minutes <= 10080)"
)
ALERT_RULE_REVISION_CHECK = "revision >= 1"
ALERT_STATUS_CHECK = f"status IN {_sql_list(ALERT_STATUSES)}"
ALERT_SUPPRESS_REASON_CHECK = (
    f"suppress_reason IS NULL OR suppress_reason IN {_sql_list(ALERT_SUPPRESS_REASONS)}"
)
ALERT_ESCALATION_REASON_CHECK = (
    f"escalation_reason IS NULL OR escalation_reason IN {_sql_list(ALERT_ESCALATION_REASONS)}"
)
ALERT_CONDITION_GROUP_CHECK = "length(condition_group) >= 1"
ALERT_DEDUPE_GROUP_CHECK = "length(dedupe_group) >= 1"
ALERT_EVENT_COUNT_CHECK = "event_count >= 1"
ALERT_ESCALATION_PAIR_CHECK = (
    "(escalated_at IS NULL AND escalation_reason IS NULL) "
    "OR (escalated_at IS NOT NULL AND escalation_reason IS NOT NULL)"
)
ALERT_SUPPRESSION_FIELDS_CHECK = (
    "(suppressed_at IS NULL AND suppressed_until IS NULL AND suppress_reason IS NULL) "
    "OR (suppressed_at IS NOT NULL AND suppressed_until IS NOT NULL "
    "AND suppress_reason IS NOT NULL)"
)
# Each status has its own fields.  No check names a user column: the four user columns
# are SET NULL when a user is removed, and a check on them would make the removal fail.
# An acknowledged or suppressed alert may also carry its escalation fields: it was
# escalated first, and the later move clears nothing.
ALERT_OPEN_FIELDS_CHECK = (
    "status <> 'open' OR (acknowledged_at IS NULL AND suppressed_at IS NULL "
    "AND escalated_at IS NULL)"
)
ALERT_ACKNOWLEDGED_FIELDS_CHECK = (
    "status <> 'acknowledged' OR (acknowledged_at IS NOT NULL AND suppressed_at IS NULL)"
)
ALERT_SUPPRESSED_FIELDS_CHECK = (
    "status <> 'suppressed' OR (suppressed_at IS NOT NULL AND acknowledged_at IS NULL)"
)
ALERT_ESCALATED_FIELDS_CHECK = (
    "status <> 'escalated' OR (escalated_at IS NOT NULL AND acknowledged_at IS NULL "
    "AND suppressed_at IS NULL)"
)
ALERT_EVENT_SOURCE_CHECK = f"source_type IN {_sql_list(ALERT_EVENT_SOURCES)}"
ALERT_CONDITION_REF_CHECK = "length(condition_ref) >= 1"
ALERT_DELIVERY_PURPOSE_CHECK = f"purpose IN {_sql_list(ALERT_DELIVERY_PURPOSES)}"
ALERT_DELIVERY_CHANNEL_CHECK = f"channel IN {_sql_list(ALERT_DELIVERY_CHANNELS)}"
ALERT_DELIVERY_STATUS_CHECK = f"status IN {_sql_list(ALERT_DELIVERY_STATUSES)}"
ALERT_DELIVERY_REASON_CHECK = f"reason IS NULL OR reason IN {_sql_list(ALERT_DELIVERY_REASONS)}"
ALERT_DELIVERY_SUBJECT_REF_CHECK = "length(subject_ref) >= 1"
ALERT_DELIVERY_ATTEMPT_COUNT_CHECK = "attempt_count >= 0"
ALERT_DELIVERY_PURPOSE_SHAPE_CHECK = (
    "(purpose <> 'digest' OR alert_id IS NULL) "
    "AND (purpose NOT IN ('alert','escalation') OR alert_id IS NOT NULL)"
)
ALERT_DELIVERY_SENT_FIELDS_CHECK = "status <> 'sent' OR (sent_at IS NOT NULL AND reason IS NULL)"
ALERT_DELIVERY_SKIPPED_FIELDS_CHECK = (
    "status <> 'skipped' OR (reason IS NOT NULL AND sent_at IS NULL)"
)
ALERT_DELIVERY_FAILED_FIELDS_CHECK = (
    "status <> 'failed' OR (reason IS NOT NULL AND sent_at IS NULL)"
)
ALERT_DELIVERY_PENDING_FIELDS_CHECK = "status <> 'pending' OR sent_at IS NULL"


class AlertRule(Base):
    """One alert rule of a workspace (HOM-134, WP24-T02, F59, ADR 0085).

    A rule names one condition type: a failed QA criterion (with an optional
    criterion id, NULL meaning any criterion), a risk finding of the meeting
    analysis, or a missed commitment.  It carries a severity, recipient roles and
    explicit recipient users, a dedup window in minutes (the window an alert stays
    open for new events), an optional auto-escalation delay and a delivery mode.
    ``immediate`` rules notify at open; ``digest_only`` rules notify only through the
    daily digest.  ``revision`` starts at 1 and grows by one with each change, so a
    PATCH is a compare-and-set on it, like ``coaching_tasks``.  Nothing writes the
    table yet: PR 2 adds the service, the routes and the permissions.  Workspace
    scoped, tenant only, never through a share token; not conversation content, so
    the purge keeps it and the backup reconciliation does not count it.
    """

    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    condition_type: Mapped[str] = mapped_column(String(30), nullable=False)
    criterion_id: Mapped[str | None] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient_roles: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    recipient_user_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    delivery_mode: Mapped[str] = mapped_column(String(20), default="immediate", nullable=False)
    dedup_window_minutes: Mapped[int] = mapped_column(Integer, default=1440, nullable=False)
    escalate_after_minutes: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    updated_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_alert_rules_scope"),
        UniqueConstraint("workspace_id", "name", name="uq_alert_rules_name"),
        CheckConstraint(ALERT_RULE_NAME_CHECK, name="ck_alert_rules_name"),
        CheckConstraint(ALERT_CONDITION_TYPE_CHECK, name="ck_alert_rules_condition_type"),
        CheckConstraint(ALERT_SEVERITY_CHECK, name="ck_alert_rules_severity"),
        CheckConstraint(ALERT_DELIVERY_MODE_CHECK, name="ck_alert_rules_delivery_mode"),
        CheckConstraint(ALERT_RULE_CRITERION_KEY_CHECK, name="ck_alert_rules_criterion_key"),
        CheckConstraint(ALERT_RULE_CONDITION_SHAPE_CHECK, name="ck_alert_rules_condition_shape"),
        CheckConstraint(ALERT_RULE_DEDUP_WINDOW_CHECK, name="ck_alert_rules_dedup_window"),
        CheckConstraint(ALERT_RULE_ESCALATE_AFTER_CHECK, name="ck_alert_rules_escalate_after"),
        CheckConstraint(ALERT_RULE_REVISION_CHECK, name="ck_alert_rules_revision"),
        Index("ix_alert_rules_active", "workspace_id", "enabled", "condition_type"),
    )


class Alert(Base):
    """One grouped alert of one rule and one dedupe group (HOM-134, WP24-T02, F59).

    The window opens at the first event and lasts the rule's
    ``dedup_window_minutes``: a later event of the same group inside the window joins
    this alert (``event_count`` plus one, ``last_event_at`` moves) and notifies
    nobody; an event at or after ``window_ends_at`` opens a new alert.
    ``uq_alerts_window`` is the database backstop: one alert per rule, dedupe group
    and window start.  States: ``open``, ``acknowledged``, ``suppressed``,
    ``escalated``.  Acknowledged and suppressed are final; an acknowledged or
    suppressed alert may still carry its escalation fields, because it was escalated
    first.  A suppression ends at ``suppressed_until``; a new alert of the same rule
    and group that opens before that time starts suppressed with reason
    ``inherited`` and notifies nobody.  The user columns are SET NULL when a user is
    removed; ``escalated_by_user_id`` NULL with ``escalated_at`` set means the
    escalation was automatic.  Workspace scoped, tenant only, never through a share
    token; not conversation content.
    """

    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[str] = mapped_column(String(36), nullable=False)
    condition_type: Mapped[str] = mapped_column(String(30), nullable=False)
    condition_group: Mapped[str] = mapped_column(String(80), nullable=False)
    dedupe_group: Mapped[str] = mapped_column(String(160), nullable=False)
    subject_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    suppressed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suppressed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suppress_reason: Mapped[str | None] = mapped_column(String(30))
    suppressed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalation_reason: Mapped[str | None] = mapped_column(String(30))
    escalated_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_alerts_scope"),
        UniqueConstraint("rule_id", "dedupe_group", "window_started_at", name="uq_alerts_window"),
        ForeignKeyConstraint(
            ["rule_id", "workspace_id"],
            ["alert_rules.id", "alert_rules.workspace_id"],
            ondelete="CASCADE",
            name="fk_alerts_rule",
        ),
        CheckConstraint(ALERT_CONDITION_TYPE_CHECK, name="ck_alerts_condition_type"),
        CheckConstraint(ALERT_SEVERITY_CHECK, name="ck_alerts_severity"),
        CheckConstraint(ALERT_STATUS_CHECK, name="ck_alerts_status"),
        CheckConstraint(ALERT_SUPPRESS_REASON_CHECK, name="ck_alerts_suppress_reason"),
        CheckConstraint(ALERT_ESCALATION_REASON_CHECK, name="ck_alerts_escalation_reason"),
        CheckConstraint(ALERT_CONDITION_GROUP_CHECK, name="ck_alerts_condition_group"),
        CheckConstraint(ALERT_DEDUPE_GROUP_CHECK, name="ck_alerts_dedupe_group"),
        CheckConstraint(ALERT_EVENT_COUNT_CHECK, name="ck_alerts_event_count"),
        CheckConstraint(ALERT_RULE_REVISION_CHECK, name="ck_alerts_revision"),
        CheckConstraint(ALERT_ESCALATION_PAIR_CHECK, name="ck_alerts_escalation_pair"),
        CheckConstraint(ALERT_SUPPRESSION_FIELDS_CHECK, name="ck_alerts_suppression_fields"),
        CheckConstraint(ALERT_OPEN_FIELDS_CHECK, name="ck_alerts_open_fields"),
        CheckConstraint(ALERT_ACKNOWLEDGED_FIELDS_CHECK, name="ck_alerts_acknowledged_fields"),
        CheckConstraint(ALERT_SUPPRESSED_FIELDS_CHECK, name="ck_alerts_suppressed_fields"),
        CheckConstraint(ALERT_ESCALATED_FIELDS_CHECK, name="ck_alerts_escalated_fields"),
        Index("ix_alerts_queue", "workspace_id", "status", "last_event_at", "id"),
        Index("ix_alerts_rule_group", "rule_id", "dedupe_group", "window_ends_at"),
    )


class AlertEvent(Base):
    """One occurrence of a rule's condition on one call (HOM-134, WP24-T02, F59).

    Written once, immutable after the insert: the application role may not UPDATE the
    table (``NO_UPDATE_TABLES``) and the ORM guard below refuses an update.  The same
    condition on the same call is one event, ever (``uq_alert_events_condition``), so
    a rerun or a recompute never counts twice.  ``source_type`` names where the
    condition was seen (a human review result, an analysis run, an action item);
    ``source_id`` and ``condition_ref`` are plain strings, no keys: the event
    outlives its source.  Conversation content: the purge deletes the row and the
    backup reconciliation counts it.  Tenant only, never through a share token.
    """

    __tablename__ = "alert_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alert_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(36), nullable=False)
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    condition_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "rule_id", "meeting_id", "condition_ref", name="uq_alert_events_condition"
        ),
        ForeignKeyConstraint(
            ["alert_id", "workspace_id"],
            ["alerts.id", "alerts.workspace_id"],
            ondelete="CASCADE",
            name="fk_alert_events_alert",
        ),
        ForeignKeyConstraint(
            ["rule_id", "workspace_id"],
            ["alert_rules.id", "alert_rules.workspace_id"],
            ondelete="CASCADE",
            name="fk_alert_events_rule",
        ),
        CheckConstraint(ALERT_EVENT_SOURCE_CHECK, name="ck_alert_events_source_type"),
        CheckConstraint(ALERT_CONDITION_REF_CHECK, name="ck_alert_events_condition_ref"),
        Index("ix_alert_events_alert", "alert_id", "occurred_at"),
        Index("ix_alert_events_workspace_meeting", "workspace_id", "meeting_id"),
    )


class AlertDelivery(Base):
    """One message to one user on one channel for one purpose (HOM-134, WP24-T02, F59).

    A purpose is ``alert`` (an alert opened), ``escalation`` (an alert escalated) or
    ``digest`` (the daily digest of one user); a digest delivery names no alert.  The
    status is ``pending``, ``sent``, ``failed`` or ``skipped``, with a reason code
    when a user got nothing (``not_authorized``, ``muted``, ``digest_disabled``,
    ``email_missing``) or an e-mail failed for good (``email_send_failed`` after three
    attempts).  In-app deliveries go through the notification service and are
    ``sent`` or ``skipped`` at once, with ``notification_id`` naming the in-app row.
    E-mail deliveries start ``pending`` and the PR 2 sweep sends them.  One delivery
    per workspace, purpose, subject, recipient and channel
    (``uq_alert_deliveries_target``): the digest reaches a user once per day and
    channel.  The recipient is SET NULL when a user is removed; a digest delivery is
    not conversation content and the purge keeps it.  Tenant only, never through a
    share token.
    """

    __tablename__ = "alert_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alert_id: Mapped[str | None] = mapped_column(String(36))
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    recipient_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    reason: Mapped[str | None] = mapped_column(String(40))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notification_id: Mapped[str | None] = mapped_column(
        ForeignKey("notifications.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "purpose",
            "subject_ref",
            "recipient_user_id",
            "channel",
            name="uq_alert_deliveries_target",
        ),
        ForeignKeyConstraint(
            ["alert_id", "workspace_id"],
            ["alerts.id", "alerts.workspace_id"],
            ondelete="CASCADE",
            name="fk_alert_deliveries_alert",
        ),
        CheckConstraint(ALERT_DELIVERY_PURPOSE_CHECK, name="ck_alert_deliveries_purpose"),
        CheckConstraint(ALERT_DELIVERY_CHANNEL_CHECK, name="ck_alert_deliveries_channel"),
        CheckConstraint(ALERT_DELIVERY_STATUS_CHECK, name="ck_alert_deliveries_status"),
        CheckConstraint(ALERT_DELIVERY_REASON_CHECK, name="ck_alert_deliveries_reason"),
        CheckConstraint(ALERT_DELIVERY_SUBJECT_REF_CHECK, name="ck_alert_deliveries_subject_ref"),
        CheckConstraint(
            ALERT_DELIVERY_ATTEMPT_COUNT_CHECK, name="ck_alert_deliveries_attempt_count"
        ),
        CheckConstraint(
            ALERT_DELIVERY_PURPOSE_SHAPE_CHECK, name="ck_alert_deliveries_purpose_shape"
        ),
        CheckConstraint(ALERT_DELIVERY_SENT_FIELDS_CHECK, name="ck_alert_deliveries_sent_fields"),
        CheckConstraint(
            ALERT_DELIVERY_SKIPPED_FIELDS_CHECK, name="ck_alert_deliveries_skipped_fields"
        ),
        CheckConstraint(
            ALERT_DELIVERY_FAILED_FIELDS_CHECK, name="ck_alert_deliveries_failed_fields"
        ),
        CheckConstraint(
            ALERT_DELIVERY_PENDING_FIELDS_CHECK, name="ck_alert_deliveries_pending_fields"
        ),
        Index("ix_alert_deliveries_due", "workspace_id", "status", "next_attempt_at"),
        Index("ix_alert_deliveries_alert_id", "alert_id"),
    )


class NotificationPreference(Base):
    """The notification choices of one user of one workspace (HOM-134, WP24-T02, F59).

    One row per workspace and user; no row means the defaults: the digest is on and
    e-mail is off.  ``muted_alert_rule_ids`` is the list of rule ids the user mutes
    for themselves; a muted rule gives the user a ``skipped`` delivery with reason
    ``muted``.  N07 (quiet hours and timezones) extends this table later.  The row
    goes when the user or the workspace goes.  Tenant only, never through a share
    token; not conversation content.
    """

    __tablename__ = "notification_preferences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    muted_alert_rule_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_notification_preferences_user"),
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


class WebhookDeliveryState(str, enum.Enum):
    """Where one delivery of one event to one endpoint stands (HOM-89).

    Stored as the lowercase value with a check constraint.  A failed attempt goes
    back to ``pending`` with a later ``next_attempt_at``; ``dead_letter`` is final
    until an admin redelivers it (out of attempts, refused before sending, or an
    expired lease on the last attempt; HOM-90, ADR 0050 D9).  ``failed`` is only on
    rows from before HOM-90, when one failed attempt was final.
    """

    PENDING = "pending"
    SENDING = "sending"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class WebhookAttemptOutcome(str, enum.Enum):
    """The result of one HTTP request of a delivery (HOM-89)."""

    DELIVERED = "delivered"
    FAILED = "failed"


# ``whk_`` and 24 lowercase hex characters, without a regex so SQLite runs the same check.
WEBHOOK_SIGNING_KEY_ID_CHECK = (
    "length(key_id) = 28 AND substr(key_id, 1, 4) = 'whk_' "
    "AND trim(substr(key_id, 5), '0123456789abcdef') = ''"
)
# AES-GCM takes a 12-byte nonce.
WEBHOOK_SIGNING_KEY_NONCE_CHECK = "length(nonce) = 12"
WEBHOOK_DELIVERY_STATE_CHECK = (
    "state IN (" + ", ".join(f"'{member.value}'" for member in WebhookDeliveryState) + ")"
)
WEBHOOK_DELIVERY_DELIVERED_AT_CHECK = (
    f"state <> '{WebhookDeliveryState.DELIVERED.value}' OR delivered_at IS NOT NULL"
)
# 64 lowercase hex characters (a SHA-256), or NULL on rows written before HOM-89.
WEBHOOK_DELIVERY_PAYLOAD_SHA256_CHECK = (
    "payload_sha256 IS NULL OR (length(payload_sha256) = 64 "
    "AND trim(payload_sha256, '0123456789abcdef') = '')"
)
WEBHOOK_ATTEMPT_OUTCOME_CHECK = (
    "outcome IN (" + ", ".join(f"'{member.value}'" for member in WebhookAttemptOutcome) + ")"
)
WEBHOOK_ATTEMPT_STATUS_CODE_CHECK = "status_code IS NULL OR status_code BETWEEN 100 AND 599"
# A delivered attempt has a 2xx status and no error; a failed one has an error code.
# ``status_code IS NOT NULL`` is needed: a NULL ``BETWEEN`` is NULL, and a check passes
# on NULL.
WEBHOOK_ATTEMPT_SHAPE_CHECK = (
    f"(outcome = '{WebhookAttemptOutcome.DELIVERED.value}' AND status_code IS NOT NULL "
    "AND status_code BETWEEN 200 AND 299 AND error_code IS NULL) "
    f"OR (outcome = '{WebhookAttemptOutcome.FAILED.value}' AND error_code IS NOT NULL)"
)


class WebhookEndpoint(Base, TimestampMixin):
    __tablename__ = "webhook_endpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    # Legacy column (HOM-89): new code never reads or writes it.  The signing secrets
    # live in ``webhook_signing_keys``.
    secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    event_types: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(200))
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class WebhookSigningKey(Base, TimestampMixin):
    """One signing secret of a webhook endpoint (HOM-89).

    The secret is encrypted with AES-GCM under an app keyring key
    (``encryption_key_id``), never hashed: the sender needs the secret itself to
    compute the HMAC.  ``expires_at`` NULL marks the active key; a rotated key keeps
    signing until it expires, so receivers can switch secrets without a gap.
    """

    __tablename__ = "webhook_signing_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    endpoint_id: Mapped[str] = mapped_column(
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_id: Mapped[str] = mapped_column(String(40), nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(40), nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        UniqueConstraint("key_id", name="uq_webhook_signing_key_id"),
        CheckConstraint(WEBHOOK_SIGNING_KEY_ID_CHECK, name="ck_webhook_signing_key_id_format"),
        CheckConstraint(WEBHOOK_SIGNING_KEY_NONCE_CHECK, name="ck_webhook_signing_key_nonce"),
        # At most one active key per endpoint.
        Index(
            "uq_webhook_signing_keys_active",
            "endpoint_id",
            unique=True,
            postgresql_where=text("expires_at IS NULL"),
            sqlite_where=text("expires_at IS NULL"),
        ),
    )


class WebhookDelivery(Base, TimestampMixin):
    """One event queued for one endpoint (HOM-89).

    ``payload`` holds the exact bytes that are signed and sent; every attempt sends
    the same bytes.  It is NULL only on rows written before HOM-89.  One event goes
    to several endpoints with the same ``event_id`` (the receiver's dedupe key), but
    never twice to one endpoint.
    """

    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    endpoint_id: Mapped[str] = mapped_column(
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), default=WebhookDeliveryState.PENDING.value, nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(500))
    payload: Mapped[bytes | None] = mapped_column(LargeBinary)
    payload_sha256: Mapped[str | None] = mapped_column(String(64))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("endpoint_id", "event_id", name="uq_webhook_delivery_endpoint_event"),
        CheckConstraint(WEBHOOK_DELIVERY_STATE_CHECK, name="ck_webhook_delivery_state"),
        CheckConstraint("attempt_count >= 0", name="ck_webhook_delivery_attempt_count"),
        CheckConstraint(
            WEBHOOK_DELIVERY_DELIVERED_AT_CHECK, name="ck_webhook_delivery_delivered_at"
        ),
        CheckConstraint(
            WEBHOOK_DELIVERY_PAYLOAD_SHA256_CHECK, name="ck_webhook_delivery_payload_sha256"
        ),
        # The sweeper's lookup of due rows.
        Index("ix_webhook_deliveries_due", "state", "next_attempt_at"),
    )


class WebhookDeliveryAttempt(Base):
    """One HTTP request sent for a delivery (HOM-89).

    Append-only: written once, after the request ends, and never changed, so the
    ledger shows every request that left.  The response body is never stored;
    ``error_code`` is a short code.
    """

    __tablename__ = "webhook_delivery_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    delivery_id: Mapped[str] = mapped_column(
        ForeignKey("webhook_deliveries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # The signed ``t`` (unix seconds).
    request_timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
    signing_key_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("delivery_id", "attempt_number", name="uq_webhook_attempt_number"),
        CheckConstraint("attempt_number >= 1", name="ck_webhook_attempt_number_positive"),
        CheckConstraint("duration_ms >= 0", name="ck_webhook_attempt_duration"),
        CheckConstraint(WEBHOOK_ATTEMPT_OUTCOME_CHECK, name="ck_webhook_attempt_outcome"),
        CheckConstraint(WEBHOOK_ATTEMPT_STATUS_CODE_CHECK, name="ck_webhook_attempt_status_code"),
        CheckConstraint(WEBHOOK_ATTEMPT_SHAPE_CHECK, name="ck_webhook_attempt_shape"),
    )


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
@event.listens_for(RetrievalEmbedding, "before_update")
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


class ScorecardResultImmutableError(RuntimeError):
    """An ORM update or delete tried to change a stored scorecard result or criterion row."""


@event.listens_for(ScorecardResultRecord, "before_update")
@event.listens_for(CriterionResultRecord, "before_update")
def _refuse_scorecard_result_update(_mapper, _connection, target: object) -> None:
    changed = _changed_columns(target)
    if changed:
        raise ScorecardResultImmutableError(
            f"{type(target).__name__} rows are immutable; score again instead "
            f"(attempted: {', '.join(sorted(changed))})"
        )


# Only the ORM unit of work is refused.  The conversation purge removes the rows with
# bulk ``delete()`` statements, which fire no mapper event.
@event.listens_for(ScorecardResultRecord, "before_delete")
@event.listens_for(CriterionResultRecord, "before_delete")
def _refuse_scorecard_result_delete(_mapper, _connection, target: object) -> None:
    raise ScorecardResultImmutableError(
        f"{type(target).__name__} rows are immutable; only the conversation purge removes them"
    )


class ReviewResultImmutableError(RuntimeError):
    """An ORM update or delete tried to change a stored human review result."""


@event.listens_for(ReviewResultRecord, "before_update")
def _refuse_review_result_update(_mapper, _connection, target: object) -> None:
    changed = _changed_columns(target)
    if changed:
        raise ReviewResultImmutableError(
            f"{type(target).__name__} rows are immutable; complete the review again instead "
            f"(attempted: {', '.join(sorted(changed))})"
        )


# Only the ORM unit of work is refused.  The conversation purge removes the rows with
# bulk ``delete()`` statements, which fire no mapper event.
@event.listens_for(ReviewResultRecord, "before_delete")
def _refuse_review_result_delete(_mapper, _connection, target: object) -> None:
    raise ReviewResultImmutableError(
        f"{type(target).__name__} rows are immutable; only the conversation purge removes them"
    )


class ReviewAppealResultImmutableError(RuntimeError):
    """An ORM update or delete tried to change a stored appeal result (HOM-76)."""


@event.listens_for(ReviewAppealResult, "before_update")
def _refuse_review_appeal_result_update(_mapper, _connection, target: object) -> None:
    changed = _changed_columns(target)
    if changed:
        raise ReviewAppealResultImmutableError(
            f"{type(target).__name__} rows are immutable; an appeal is decided once "
            f"(attempted: {', '.join(sorted(changed))})"
        )


# Only the ORM unit of work is refused.  The conversation purge removes the rows with
# bulk ``delete()`` statements, which fire no mapper event.
@event.listens_for(ReviewAppealResult, "before_delete")
def _refuse_review_appeal_result_delete(_mapper, _connection, target: object) -> None:
    raise ReviewAppealResultImmutableError(
        f"{type(target).__name__} rows are immutable; only the conversation purge removes them"
    )


class ActionDeliveryRequestImmutableError(RuntimeError):
    """An ORM update tried to change a stored delivery request (HOM-135)."""


# Only updates are refused.  A delete stays allowed: the proposal's cascade and the
# conversation purge remove requests, and the application role keeps DELETE.
@event.listens_for(ActionDeliveryRequest, "before_update")
def _refuse_action_delivery_request_update(_mapper, _connection, target: object) -> None:
    changed = _changed_columns(target)
    if changed:
        raise ActionDeliveryRequestImmutableError(
            f"{type(target).__name__} rows are immutable; revoke the proposal instead "
            f"(attempted: {', '.join(sorted(changed))})"
        )


class AlertEventImmutableError(RuntimeError):
    """An ORM update tried to change a stored alert event (HOM-134)."""


# Only updates are refused.  A delete stays allowed: the alert's cascade and the
# conversation purge remove events, and the application role keeps DELETE.
@event.listens_for(AlertEvent, "before_update")
def _refuse_alert_event_update(_mapper, _connection, target: object) -> None:
    changed = _changed_columns(target)
    if changed:
        raise AlertEventImmutableError(
            f"{type(target).__name__} rows are immutable; the purge removes them "
            f"(attempted: {', '.join(sorted(changed))})"
        )


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
