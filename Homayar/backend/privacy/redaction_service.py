"""Redacted transcript versions and restricted mappings (WP07-T05, ADR 0023).

``redact_revision`` derives one redacted version of a transcript revision for a
class policy.  Its identity is ``(revision, policy_digest)`` where the digest
covers the detector version and the sorted class list, so a detector or policy
change yields a new version and an existing version is never rewritten
(immutable rows, like the transcript it derives from).  Placeholders are stable
across the segments of one version (``[PHONE_1]`` is the same number
everywhere).  Originals of replaced values go only to ``pii_mappings`` as
AES-GCM ciphertext; without a configured key they are not kept at all.

Nothing here logs or audits a detected value or redacted text: audits carry
ids, counts, classes and versions only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.saas_models import (
    PiiMapping,
    RedactedSegment,
    TranscriptRedaction,
    TranscriptRevision,
    TranscriptSegment,
    Workspace,
)
from backend.privacy.mapping_crypto import MappingCipher
from backend.privacy.text_pii import (
    DETECTOR_VERSION,
    PiiClass,
    Pseudonymizer,
    parse_classes,
    redact,
)
from backend.services.audit_service import record_audit_event


class RedactionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def policy_digest(classes: Iterable[PiiClass], *, detector_version: str = DETECTOR_VERSION) -> str:
    names = ",".join(sorted({c.value for c in classes}))
    return hashlib.sha256(f"{detector_version}|{names}".encode()).hexdigest()


def workspace_classes(workspace: Workspace) -> list[PiiClass]:
    return parse_classes(workspace.pii_redaction_classes or [])


def find_redaction(
    db: Session, *, workspace_id: str, revision_id: str, digest: str
) -> TranscriptRedaction | None:
    return db.scalar(
        select(TranscriptRedaction).where(
            TranscriptRedaction.workspace_id == workspace_id,
            TranscriptRedaction.revision_id == revision_id,
            TranscriptRedaction.policy_digest == digest,
        )
    )


def redact_revision(
    db: Session,
    *,
    revision: TranscriptRevision,
    classes: Iterable[PiiClass],
    settings: Settings,
    now: datetime,
    actor_user_id: str | None = None,
) -> TranscriptRedaction:
    """Return the redacted version of ``revision`` for ``classes`` (idempotent).

    Runs in the caller's tenant-bound transaction; a concurrent creator of the
    same version wins and its row is returned (savepoint + unique constraint).
    """
    chosen = sorted(set(classes), key=lambda c: c.value)
    if not chosen:
        raise RedactionError("no_pii_classes", "A redaction needs at least one class")
    digest = policy_digest(chosen)
    existing = find_redaction(
        db, workspace_id=revision.workspace_id, revision_id=revision.id, digest=digest
    )
    if existing is not None:
        return existing

    cipher = MappingCipher.from_settings(settings)
    segments = list(
        db.scalars(
            select(TranscriptSegment)
            .where(
                TranscriptSegment.workspace_id == revision.workspace_id,
                TranscriptSegment.revision_id == revision.id,
            )
            .order_by(TranscriptSegment.sequence.asc())
        )
    )
    pseudonymizer = Pseudonymizer()
    redacted = [
        (segment, *redact(segment.text, chosen, pseudonymizer=pseudonymizer))
        for segment in segments
    ]
    total = sum(len(replacements) for _, _, replacements in redacted)

    savepoint = db.begin_nested()
    try:
        redaction = TranscriptRedaction(
            workspace_id=revision.workspace_id,
            meeting_id=revision.meeting_id,
            revision_id=revision.id,
            detector_version=DETECTOR_VERSION,
            classes=[c.value for c in chosen],
            policy_digest=digest,
            entity_count=total,
            mapping_retained=cipher is not None,
            created_at=now,
        )
        db.add(redaction)
        db.flush()
        for segment, text, replacements in redacted:
            db.add(
                RedactedSegment(
                    workspace_id=revision.workspace_id,
                    meeting_id=revision.meeting_id,
                    redaction_id=redaction.id,
                    segment_id=segment.id,
                    sequence=segment.sequence,
                    text=text,
                    entity_count=len(replacements),
                )
            )
            if cipher is None:
                continue
            for replacement in replacements:
                key_id, nonce, ciphertext = cipher.encrypt(
                    replacement.value,
                    workspace_id=revision.workspace_id,
                    redaction_id=redaction.id,
                    placeholder=replacement.placeholder,
                )
                db.add(
                    PiiMapping(
                        workspace_id=revision.workspace_id,
                        meeting_id=revision.meeting_id,
                        redaction_id=redaction.id,
                        segment_id=segment.id,
                        placeholder=replacement.placeholder,
                        pii_class=replacement.pii_class.value,
                        start_offset=replacement.start,
                        end_offset=replacement.end,
                        key_id=key_id,
                        nonce=nonce,
                        ciphertext=ciphertext,
                    )
                )
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        existing = find_redaction(
            db, workspace_id=revision.workspace_id, revision_id=revision.id, digest=digest
        )
        if existing is None:
            raise
        return existing

    record_audit_event(
        db,
        event_type="transcript.redaction_created",
        actor_user_id=actor_user_id,
        workspace_id=revision.workspace_id,
        target_type="meeting",
        target_id=revision.meeting_id,
        details={
            "redaction_id": redaction.id,
            "revision_id": revision.id,
            "detector_version": DETECTOR_VERSION,
            "classes": ",".join(c.value for c in chosen),
            "entity_count": total,
            "mapping_retained": cipher is not None,
        },
        settings=settings,
    )
    return redaction


def redacted_segments(db: Session, *, redaction: TranscriptRedaction) -> list[RedactedSegment]:
    return list(
        db.scalars(
            select(RedactedSegment)
            .where(
                RedactedSegment.workspace_id == redaction.workspace_id,
                RedactedSegment.redaction_id == redaction.id,
            )
            .order_by(RedactedSegment.sequence.asc())
        )
    )


def reveal_placeholder(
    db: Session,
    *,
    redaction: TranscriptRedaction,
    placeholder: str,
    actor_user_id: str,
    reason: str,
    settings: Settings,
    request_id: str | None = None,
) -> str:
    """Decrypt the original behind ``placeholder`` (caller checked permission).

    Audited with the placeholder, class and reason, never the value.  Raises
    ``RedactionError`` when nothing is retained or the key is gone.
    """
    from backend.privacy.mapping_crypto import MappingIntegrityError, MappingKeyUnavailable

    row = db.scalar(
        select(PiiMapping)
        .where(
            PiiMapping.workspace_id == redaction.workspace_id,
            PiiMapping.redaction_id == redaction.id,
            PiiMapping.placeholder == placeholder,
        )
        .order_by(PiiMapping.start_offset.asc())
        .limit(1)
    )
    if row is None:
        raise RedactionError("mapping_not_retained", "No original is retained for this value")
    cipher = MappingCipher.from_settings(settings)
    if cipher is None:
        raise RedactionError("mapping_key_unavailable", "No mapping key is configured")
    try:
        value = cipher.decrypt(
            key_id=row.key_id,
            nonce=row.nonce,
            ciphertext=row.ciphertext,
            workspace_id=redaction.workspace_id,
            redaction_id=redaction.id,
            placeholder=placeholder,
        )
    except (MappingKeyUnavailable, MappingIntegrityError) as exc:
        raise RedactionError("mapping_key_unavailable", "The original cannot be decrypted") from exc
    record_audit_event(
        db,
        event_type="pii.revealed",
        actor_user_id=actor_user_id,
        workspace_id=redaction.workspace_id,
        target_type="meeting",
        target_id=redaction.meeting_id,
        request_id=request_id,
        details={
            "redaction_id": redaction.id,
            "placeholder": placeholder,
            "pii_class": row.pii_class,
            "reason": reason[:120],
        },
        settings=settings,
    )
    return value
