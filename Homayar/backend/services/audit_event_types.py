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

    ACTION_ITEM_ASSIGNEE_CHANGED = "action_item.assignee_changed", "action_item"
    ACTION_ITEM_CREATED = "action_item.created", "action_item"
    ACTION_ITEM_DELETED = "action_item.deleted", "action_item"
    ACTION_ITEM_UPDATED = "action_item.updated", "action_item"
    AI_POLICY_UPDATED = "ai_policy.updated", "ai_policy"
    API_KEY_CREATED = "api_key.created", "api_key"
    API_KEY_REVOKED = "api_key.revoked", "api_key"
    AUTH_BREAK_GLASS_CLOSED = "auth.break_glass_closed", "user"
    AUTH_BREAK_GLASS_DESIGNATED = "auth.break_glass_designated", "user"
    AUTH_BREAK_GLASS_LOGIN = "auth.break_glass_login", "user"
    AUTH_BREAK_GLASS_LOGIN_REFUSED = "auth.break_glass_login_refused", "user"
    AUTH_BREAK_GLASS_OPENED = "auth.break_glass_opened", "user"
    AUTH_BREAK_GLASS_UNDESIGNATED = "auth.break_glass_undesignated", "user"
    AUTH_EMAIL_VERIFIED = "auth.email_verified", "user"
    AUTH_GOOGLE_COMPLETED = "auth.google_completed", "user"
    AUTH_LOGIN = "auth.login", "user"
    AUTH_LOGIN_FAILED = "auth.login_failed", "login_identifier"
    AUTH_LOGOUT = "auth.logout", "login_session"
    AUTH_MFA_RESET = "auth.mfa_reset", "user"
    AUTH_MFA_RECOVERY_CODE_USED = "auth.mfa_recovery_code_used", "user"
    AUTH_MFA_RECOVERY_CODES_REGENERATED = "auth.mfa_recovery_codes_regenerated", "user"
    AUTH_MFA_TOTP_ENROLLED = "auth.mfa_totp_enrolled", "user"
    AUTH_MFA_VERIFIED = "auth.mfa_verified", "user"
    AUTH_MFA_VERIFY_FAILED = "auth.mfa_verify_failed", "user"
    AUTH_OTP_REQUESTED = "auth.otp_requested", "phone"
    AUTH_OTP_VERIFIED = "auth.otp_verified", "user"
    AUTH_PASSWORD_RESET_COMPLETED = "auth.password_reset_completed", "user"
    AUTH_PASSWORD_RESET_REQUESTED = "auth.password_reset_requested", "user"
    AUTH_REFRESH_REPLAY_DETECTED = "auth.refresh_replay_detected", "login_session"
    AUTH_REGISTER = "auth.register", "user"
    AUTH_SESSIONS_REVOKED = "auth.sessions_revoked", "user"
    AUTH_USER_DISABLED = "auth.user_disabled", "user"
    AUTH_USER_ENABLED = "auth.user_enabled", "user"
    AUTH_VERIFICATION_RESENT = "auth.verification_resent", "user"
    # The blinded calibration batches (HOM-75): every event targets the batch, and its
    # details hold ids, counts and revisions only, never an outcome, a note or the title.
    CALIBRATION_ADJUDICATION_RECORDED = "calibration_adjudication.recorded", "calibration_batch"
    CALIBRATION_ANSWER_RECORDED = "calibration_answer.recorded", "calibration_batch"
    CALIBRATION_BATCH_CANCELLED = "calibration_batch.cancelled", "calibration_batch"
    CALIBRATION_BATCH_CLOSED = "calibration_batch.closed", "calibration_batch"
    CALIBRATION_BATCH_CREATED = "calibration_batch.created", "calibration_batch"
    COACHING_TASK_ACKNOWLEDGED = "coaching_task.acknowledged", "coaching_task"
    COACHING_TASK_CANCELLED = "coaching_task.cancelled", "coaching_task"
    COACHING_TASK_CLOSED = "coaching_task.closed", "coaching_task"
    COACHING_TASK_CREATED = "coaching_task.created", "coaching_task"
    COACHING_TASK_OVERDUE = "coaching_task.overdue", "coaching_task"
    COACHING_TASK_REASSIGNED = "coaching_task.reassigned", "coaching_task"
    # IdP group mappings and federated deprovisioning (HOM-128, ADR 0066): the
    # reconciler's per-workspace events carry the action in ``details``; a seat
    # skip targets the workspace, because no membership row changed.
    FEDERATION_GROUP_MAPPING_CREATED = (
        "federation.group_mapping.created",
        ("federation_group_mapping"),
    )
    FEDERATION_GROUP_MAPPING_REVOKED = (
        "federation.group_mapping.revoked",
        ("federation_group_mapping"),
    )
    FEDERATION_IDENTITY_DEPROVISIONED = "federation.identity.deprovisioned", "user"
    FEDERATION_MEMBERSHIP_SEAT_SKIPPED = "federation.membership.seat_skipped", "workspace"
    FEDERATION_MEMBERSHIP_SYNCED = "federation.membership.synced", "membership"
    INGESTION_ACCEPTED = "ingestion.accepted", "ingestion"
    INGESTION_CONFLICT = "ingestion.conflict", "ingestion"
    INGESTION_DUPLICATE_MEDIA = "ingestion.duplicate_media", "ingestion"
    INGESTION_REJECTED = "ingestion.rejected", "ingestion"
    INGESTION_REPLAYED = "ingestion.replayed", "ingestion"
    INTEGRATION_MAPPING_UPDATED = "integration_mapping.updated", "integration_mapping_profile"
    INTEGRATION_RECORD_CONFLICT = "integration_record.conflict", "integration_inbound_record"
    MEDIA_DELETED = "media.deleted", "media"
    MEDIA_DELETION_REQUESTED = "media.deletion_requested", "media"
    MEDIA_DERIVATIVE_UNAVAILABLE = "media.derivative_unavailable", "media"
    MEDIA_INTEGRITY_CHECKED = "media.integrity_checked", "media"
    MEDIA_LEGAL_HOLD_INHERITED = "media.legal_hold_inherited", "media"
    MEDIA_LEGAL_HOLD_KEPT = "media.legal_hold_kept", "media"
    MEDIA_LEGAL_HOLD_PLACED = "media.legal_hold_placed", "media"
    MEDIA_LEGAL_HOLD_RELEASED = "media.legal_hold_released", "media"
    MEDIA_ORPHAN_REMOVED = "media.orphan_removed", "workspace"
    MEDIA_ORPHANS_REPORTED = "media.orphans_reported", "workspace"
    MEDIA_REGISTERED = "media.registered", "media"
    MEETING_ATTRIBUTION_CHANGED = "meeting.attribution_changed", "meeting"
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
    MEETING_RECOMPUTE_COMPLETED = "meeting.recompute.completed", "meeting"
    MEETING_RECOMPUTE_FAILED = "meeting.recompute.failed", "meeting"
    MEETING_RECOMPUTE_REQUESTED = "meeting.recompute.requested", "meeting"
    MEETING_RECOMPUTE_SUPERSEDED = "meeting.recompute.superseded", "meeting"
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
    RECONCILIATION_ITEM_RESOLVED = "reconciliation_item.resolved", "reconciliation_item"
    REVIEW_APPEAL_ASSIGNED = "review_appeal.assigned", "review_appeal"
    REVIEW_APPEAL_DECIDED = "review_appeal.decided", "review_appeal"
    REVIEW_APPEAL_FILED = "review_appeal.filed", "review_appeal"
    REVIEW_APPEAL_WITHDRAWN = "review_appeal.withdrawn", "review_appeal"
    REVIEW_CRITERION_DECIDED = "review_criterion.decided", "review_item"
    REVIEW_ITEM_ASSIGNED = "review_item.assigned", "review_item"
    REVIEW_ITEM_CANCELLED = "review_item.cancelled", "review_item"
    REVIEW_ITEM_COMPLETED = "review_item.completed", "review_item"
    REVIEW_ITEM_CREATED = "review_item.created", "review_item"
    REVIEW_ITEM_DUE_CHANGED = "review_item.due_changed", "review_item"
    REVIEW_ITEM_STARTED = "review_item.started", "review_item"
    REVIEW_SAMPLE_DRAWN = "review_sample.drawn", "workspace"
    REVIEW_SAMPLE_ENQUEUED = "review_sample.enqueued", "workspace"
    SCORECARD_PUBLISHED = "scorecard.published", "scorecard"
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
    VOICEMAIL_ASSIGNMENT_CHANGED = "voicemail.assignment_changed", "voicemail"
    VOICEMAIL_CLASSIFIED = "voicemail.classified", "voicemail"
    VOICEMAIL_UPDATED = "voicemail.updated", "voicemail"
    WEBHOOK_DEAD_LETTERS_REDELIVERED = "webhook.dead_letters_redelivered", "webhook_endpoint"
    WEBHOOK_DELIVERY_REDELIVERED = "webhook.delivery_redelivered", "webhook_delivery"
    WEBHOOK_ENDPOINT_CREATED = "webhook.endpoint_created", "webhook_endpoint"
    WEBHOOK_ENDPOINT_UPDATED = "webhook.endpoint_updated", "webhook_endpoint"
    WEBHOOK_SIGNING_KEY_REVOKED = "webhook.signing_key_revoked", "webhook_endpoint"
    WEBHOOK_SIGNING_KEY_ROTATED = "webhook.signing_key_rotated", "webhook_endpoint"
    WEBHOOK_TEST_QUEUED = "webhook.test_queued", "webhook_endpoint"
    WORKSPACE_MODULE_DISABLED = "workspace.module.disabled", "workspace"
    WORKSPACE_MODULE_ENABLED = "workspace.module.enabled", "workspace"
    WORKSPACE_SETTINGS_UPDATED = "workspace.settings.updated", "workspace"


_BY_VALUE: dict[str, AuditEventType] = {member.value: member for member in AuditEventType}


def lookup_audit_event_type(event_type: str) -> AuditEventType | None:
    """The registry member for a stored event type string, or ``None``."""
    return _BY_VALUE.get(event_type)
