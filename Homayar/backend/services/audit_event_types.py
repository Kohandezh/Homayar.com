"""The catalog of audit event types (HOM-185).

Every event type the backend can write to ``audit_events.event_type`` is a
member here. A member is a ``str`` equal to the stored string, so it can be
passed wherever an event type string is passed. Each member also carries the
``target_type`` the code stores with it (``None`` when it stores none) and its
``group``, the event type prefix before the first dot.

The stored strings must never change: historical rows keep them. The test
guards in ``backend/tests`` fail when code writes a type that is missing here
or writes it with another target type.
"""

from __future__ import annotations

from enum import StrEnum


class AuditEventType(StrEnum):
    target_type: str | None

    def __new__(cls, value: str, target_type: str | None) -> AuditEventType:
        member = str.__new__(cls, value)
        member._value_ = value
        member.target_type = target_type
        return member

    @property
    def group(self) -> str:
        return self.value.split(".", 1)[0]

    ACTION_ITEM_CREATED = "action_item.created", "action_item"
    ACTION_ITEM_DELETED = "action_item.deleted", "action_item"
    ACTION_ITEM_UPDATED = "action_item.updated", "action_item"
    AI_POLICY_UPDATED = "ai_policy.updated", "ai_policy"
    API_KEY_CREATED = "api_key.created", "api_key"
    API_KEY_REVOKED = "api_key.revoked", "api_key"
    AUTH_EMAIL_VERIFIED = "auth.email_verified", "user"
    AUTH_GOOGLE_COMPLETED = "auth.google_completed", "user"
    AUTH_LOGIN = "auth.login", "user"
    AUTH_LOGIN_FAILED = "auth.login_failed", "login_identifier"
    AUTH_LOGOUT = "auth.logout", "login_session"
    AUTH_OTP_REQUESTED = "auth.otp_requested", "phone"
    AUTH_OTP_VERIFIED = "auth.otp_verified", "user"
    AUTH_PASSWORD_RESET_COMPLETED = "auth.password_reset_completed", "user"
    AUTH_PASSWORD_RESET_REQUESTED = "auth.password_reset_requested", "user"
    AUTH_REFRESH_REPLAY_DETECTED = "auth.refresh_replay_detected", "login_session"
    AUTH_REGISTER = "auth.register", "user"
    AUTH_VERIFICATION_RESENT = "auth.verification_resent", "user"
    INGESTION_ACCEPTED = "ingestion.accepted", "ingestion"
    INGESTION_CONFLICT = "ingestion.conflict", "ingestion"
    INGESTION_DUPLICATE_MEDIA = "ingestion.duplicate_media", "ingestion"
    INGESTION_REJECTED = "ingestion.rejected", "ingestion"
    INGESTION_REPLAYED = "ingestion.replayed", "ingestion"
    MEDIA_DELETED = "media.deleted", "media"
    MEDIA_DELETION_REQUESTED = "media.deletion_requested", "media"
    MEDIA_INTEGRITY_CHECKED = "media.integrity_checked", "media"
    MEDIA_LEGAL_HOLD_PLACED = "media.legal_hold_placed", "media"
    MEDIA_LEGAL_HOLD_RELEASED = "media.legal_hold_released", "media"
    MEDIA_ORPHAN_REMOVED = "media.orphan_removed", "workspace"
    MEDIA_ORPHANS_REPORTED = "media.orphans_reported", "workspace"
    MEDIA_REGISTERED = "media.registered", "media"
    MEETING_DELETED = "meeting.deleted", "meeting"
    MEETING_EXPORTED = "meeting.exported", "meeting"
    MEETING_PROCESSING_CANCELLED = "meeting.processing.cancelled", "meeting"
    MEETING_PROCESSING_COMPLETED = "meeting.processing.completed", "meeting"
    MEETING_PROCESSING_FAILED = "meeting.processing.failed", "meeting"
    MEETING_PROCESSING_LEASE_EXPIRED = "meeting.processing.lease_expired", "meeting"
    MEETING_PROCESSING_REPLAYED = "meeting.processing.replayed", "meeting"
    MEETING_PROCESSING_RETRY_SCHEDULED = "meeting.processing.retry_scheduled", "meeting"
    MEETING_PURGE_COMPLETED = "meeting.purge_completed", "meeting"
    MEETING_PURGE_FAILED = "meeting.purge_failed", "meeting"
    MEETING_PURGE_REQUEUED = "meeting.purge_requeued", "meeting"
    MEETING_RECORDING_DELETED_AFTER_PROCESSING = (
        "meeting.recording.deleted_after_processing",
        "meeting",
    )
    MEETING_RECORDING_RETAINED_AFTER_PROCESSING = (
        "meeting.recording.retained_after_processing",
        "meeting",
    )
    MEETING_RETENTION_EXPIRED = "meeting.retention_expired", "meeting"
    MEETING_SHARE_ACCESSED = "meeting.share_accessed", "share_link"
    MEETING_SHARE_CREATED = "meeting.share_created", "share_link"
    MEETING_SHARE_RECIPIENTS_CHANGED = "meeting.share_recipients_changed", "share_link"
    MEETING_SHARE_REVOKED = "meeting.share_revoked", "share_link"
    MEETING_SPEAKER_RENAMED = "meeting.speaker_renamed", "meeting_speaker"
    MEETING_TOMBSTONE_REAPPLIED = "meeting.tombstone_reapplied", "meeting"
    MEETING_UPDATED = "meeting.updated", "meeting"
    MEETING_UPLOADED = "meeting.uploaded", "meeting"
    MEMBER_INVITATION_ACCEPTED = "member.invitation.accepted", "workspace_invitation"
    MEMBER_INVITATION_CREATED = "member.invitation.created", "workspace_invitation"
    MEMBER_INVITATION_DELIVERY_FAILED = "member.invitation.delivery_failed", "workspace_invitation"
    MEMBER_INVITATION_REVOKED = "member.invitation.revoked", "workspace_invitation"
    MEMBER_REMOVED = "member.removed", "membership"
    MEMBER_ROLE_CHANGED = "member.role_changed", "membership"
    MODEL_MANIFEST_REGISTERED = "model_manifest.registered", "model_manifest"
    ORGANIZATION_MEMBER_CHANGED = "organization.member.changed", "organization_membership"
    ORGANIZATION_MEMBER_GRANTED = "organization.member.granted", "organization_membership"
    ORGANIZATION_MEMBER_REVOKED = "organization.member.revoked", "organization_membership"
    ORGANIZATION_SCOPE_GRANTED = "organization.scope.granted", "organization_scope_grant"
    ORGANIZATION_SCOPE_REVOKED = "organization.scope.revoked", "organization_scope_grant"
    ORGANIZATION_WORKSPACE_CREATED = "organization.workspace.created", "workspace"
    ORGANIZATION_WORKSPACE_MEMBER_INSTALLED = (
        "organization.workspace.member_installed",
        "membership",
    )
    ORGANIZATION_WORKSPACE_MEMBER_REMOVED = "organization.workspace.member_removed", "membership"
    PII_REVEALED = "pii.revealed", "meeting"
    SERVICE_LOCATION_CREATED = "service_location.created", "service_location"
    SERVICE_LOCATION_DELETED = "service_location.deleted", "service_location"
    SERVICE_LOCATION_UPDATED = "service_location.updated", "service_location"
    SUBJECT_EXPORTED = "subject.exported", "workspace"
    TEAM_ASSIGNMENT_CREATED = "team.assignment.created", "team_assignment"
    TEAM_ASSIGNMENT_REVOKED = "team.assignment.revoked", "team_assignment"
    TEAM_CREATED = "team.created", "team"
    TEAM_UPDATED = "team.updated", "team"
    TRANSCRIPT_ORIGINAL_ACCESSED = "transcript.original_accessed", "meeting"
    TRANSCRIPT_REDACTION_CREATED = "transcript.redaction_created", "meeting"
    TRANSCRIPT_REVISION_CREATED = "transcript.revision.created", "transcript_revision"
    USAGE_RECONCILED = "usage.reconciled", "entitlement"
    VOICE_NODE_DRAIN_CHANGED = "voice.node.drain_changed", "voice_media_node"
    VOICE_NODE_REGISTERED = "voice.node.registered", "voice_media_node"
    VOICEMAIL_CLASSIFIED = "voicemail.classified", "voicemail"
    VOICEMAIL_UPDATED = "voicemail.updated", "voicemail"
    WORKSPACE_SETTINGS_UPDATED = "workspace.settings.updated", "workspace"


_BY_VALUE: dict[str, AuditEventType] = {member.value: member for member in AuditEventType}


def lookup_audit_event_type(event_type: str) -> AuditEventType | None:
    """The registry member for a stored event type string, or ``None``."""
    return _BY_VALUE.get(event_type)
