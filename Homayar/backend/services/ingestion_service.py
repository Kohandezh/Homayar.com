"""Canonical ingestion ledger (WP05-T01, ADR 0016).

Every capture source (file upload today; desktop, meeting bot, telephony,
external URL, folder watcher and API later) enters Homayar through one
contract: an :class:`IngestionIdentity` (workspace, source type, external
event id) and an :class:`IngestionPayload` (media digest and the declared
facts about it).  The ledger row is the record of the decision:

* **case A** — same identity, same payload hash → the existing row is
  returned (``replayed``); nothing is created, reserved or scheduled again;
* **case B** — same identity, different payload hash → ``conflict``: the
  existing row is kept, its conflict counter advances under a row lock, an
  audit event is written and the caller answers ``409``;
* **case C** — different identity, same media hash as a *live* earlier
  ingestion → the workspace's ``duplicate_media_policy`` decides: ``allow``
  links the new row to the earlier one (audited), ``reject`` refuses with
  ``409 duplicate_media`` and creates nothing (audited).

Terminal media rejections (WP05-T02) are ``rejected`` rows under the same
identity: the same payload replays the recorded refusal without inspecting
the bytes again, a different payload is a conflict.  Transient inspection
failures (tooling unavailable or timed out) and pre-staging refusals leave the
identity free and are audited only.

Identity is enforced by the database (``uq_ingestion_event``), not by the
existence check that precedes it: a concurrent second claim of the same
identity fails on the unique index after the first commits, and the caller
resolves the winner with :func:`resolve_existing`.  Case C is serialised per
workspace by a row lock on the workspace during acceptance.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import and_, or_, select, true
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.saas_models import (
    DuplicateMediaPolicy,
    IngestionRecord,
    IngestionSourceType,
    IngestionStatus,
    Meeting,
    Workspace,
)
from backend.security.authorization import RecordAccess
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.storage.base import sanitize_original_filename

_PROVIDER = re.compile(r"^[a-z][a-z0-9_.-]{0,59}$")
_EVENT_ID = re.compile(r"^[A-Za-z0-9._:@/+=-]{1,200}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

PROVIDER_CONSOLE = "console"
PROVIDER_API = "api"
PROVIDER_BACKFILL = "backfill"


class IngestionError(ValueError):
    """A deterministic refusal of an ingestion request (mapped to HTTP by the router)."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        ingestion: IngestionRecord | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.ingestion = ingestion


class IngestionDecision(str, Enum):
    ACCEPTED = "accepted"
    REPLAYED = "replayed"
    CONFLICT = "conflict"
    DUPLICATE_REJECTED = "duplicate_rejected"
    TARGET_DELETED = "target_deleted"
    REJECTED = "rejected"
    REJECTED_REPLAY = "rejected_replay"


@dataclass(frozen=True)
class IngestionIdentity:
    """The idempotency identity of one source event inside one workspace."""

    source_type: IngestionSourceType
    source_provider: str
    external_event_id: str
    external_source_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_type, IngestionSourceType):
            raise ValueError("source_type must be an IngestionSourceType")
        if not _PROVIDER.fullmatch(self.source_provider):
            raise ValueError("source_provider must be a short lowercase identifier")
        if not _EVENT_ID.fullmatch(self.external_event_id):
            raise ValueError("external_event_id must contain 1-200 URL-safe ASCII characters")
        if self.external_source_id is not None and not _EVENT_ID.fullmatch(self.external_source_id):
            raise ValueError("external_source_id must contain 1-200 URL-safe ASCII characters")


@dataclass(frozen=True)
class IngestionPayload:
    """What the event claims to deliver; hashed canonically into ``payload_hash``.

    The declared content type is *not* part of the hash: clients label the
    same bytes differently (``audio/wav`` / ``audio/x-wav``) and the media
    digest already pins the content.  ``extra`` lets a future source add
    provider facts (a meeting platform id, a SIP call leg) to the identity of
    its payload without changing this contract.
    """

    media_sha256: str
    media_size_bytes: int
    original_filename: str | None
    title: str | None
    language: str
    declared_content_type: str | None = None
    extra: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.media_sha256):
            raise ValueError("media_sha256 must contain 64 lowercase hexadecimal characters")
        if self.media_size_bytes <= 0:
            raise ValueError("media_size_bytes must be positive")
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in self.extra.items()):
            raise ValueError("extra must map strings to strings")


def canonical_payload(identity: IngestionIdentity, payload: IngestionPayload) -> bytes:
    """Stable byte form of the payload facts (sorted keys, no whitespace, UTF-8)."""
    document: dict[str, Any] = {
        "source_type": identity.source_type.value,
        "external_source_id": identity.external_source_id,
        "media_sha256": payload.media_sha256,
        "media_size_bytes": payload.media_size_bytes,
        "original_filename": payload.original_filename,
        "title": payload.title,
        "language": payload.language,
        "extra": dict(sorted(payload.extra.items())),
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def payload_digest(identity: IngestionIdentity, payload: IngestionPayload) -> str:
    return hashlib.sha256(canonical_payload(identity, payload)).hexdigest()


@dataclass(frozen=True)
class IngestionOutcome:
    decision: IngestionDecision
    record: IngestionRecord
    created: Any = None
    duplicate_of: IngestionRecord | None = None


def find_ingestion(
    db: Session,
    *,
    workspace_id: str,
    identity: IngestionIdentity,
    for_update: bool = False,
) -> IngestionRecord | None:
    query = select(IngestionRecord).where(
        IngestionRecord.workspace_id == workspace_id,
        IngestionRecord.source_type == identity.source_type,
        IngestionRecord.external_event_id == identity.external_event_id,
    )
    if for_update:
        query = query.with_for_update()
    return db.scalar(query)


def ingestion_by_job(db: Session, *, workspace_id: str, job_id: str) -> IngestionRecord | None:
    return db.scalar(
        select(IngestionRecord).where(
            IngestionRecord.workspace_id == workspace_id, IngestionRecord.job_id == job_id
        )
    )


def ingestion_for_meeting(
    db: Session, *, workspace_id: str, meeting_id: str
) -> IngestionRecord | None:
    return db.scalar(
        select(IngestionRecord).where(
            IngestionRecord.workspace_id == workspace_id, IngestionRecord.meeting_id == meeting_id
        )
    )


def find_live_duplicate(
    db: Session, *, workspace_id: str, media_sha256: str
) -> IngestionRecord | None:
    """The earliest accepted ingestion of the same media whose conversation still exists."""
    return db.scalar(
        select(IngestionRecord)
        .join(Meeting, Meeting.id == IngestionRecord.meeting_id)
        .where(
            IngestionRecord.workspace_id == workspace_id,
            IngestionRecord.media_hash == media_sha256,
            IngestionRecord.status == IngestionStatus.ACCEPTED,
            Meeting.deleted_at.is_(None),
        )
        .order_by(IngestionRecord.created_at.asc(), IngestionRecord.id.asc())
        .limit(1)
    )


def visibility_clause(access: RecordAccess):
    """Predicate limiting ingestion rows to what ``access`` may see.

    Unrestricted actors see the workspace's ledger; a restricted actor sees
    the events they submitted and the events of the conversations their
    record scope admits (the query must outer-join ``Meeting``).
    """
    if access.unrestricted:
        return true()
    return or_(
        IngestionRecord.created_by_user_id == access.user_id,
        and_(Meeting.id.isnot(None), access.meeting_clause()),
    )


def _audit_details(record: IngestionRecord, **extra: Any) -> dict[str, Any]:
    details: dict[str, Any] = {
        "ingestion_id": record.id,
        "source_type": record.source_type.value,
        "source_provider": record.source_provider,
        "external_event_id": record.external_event_id,
    }
    details.update({key: value for key, value in extra.items() if value is not None})
    return details


def resolve_existing(
    db: Session,
    *,
    record: IngestionRecord,
    payload_hash: str,
    actor_user_id: str | None,
    request_id: str | None,
    now: datetime,
    settings: Settings,
) -> IngestionOutcome:
    """Classify a request whose identity already exists (cases A and B).

    A matching payload is a replay of the accepted event (or of an event whose
    conversation has since been deleted, reported as such); a different
    payload is a conflict recorded on the row under its lock.  Both are
    audited; the caller commits.
    """
    if record.payload_hash == payload_hash:
        record_audit_event(
            db,
            event_type=AuditEventType.INGESTION_REPLAYED,
            actor_user_id=actor_user_id,
            workspace_id=record.workspace_id,
            target_type="ingestion",
            target_id=record.id,
            request_id=request_id,
            details=_audit_details(
                record,
                meeting_id=record.meeting_id,
                status=record.status.value,
                reason=record.reason,
            ),
            settings=settings,
        )
        if record.status is IngestionStatus.REJECTED:
            return IngestionOutcome(IngestionDecision.REJECTED_REPLAY, record)
        meeting = record.meeting
        if record.meeting_id is None or meeting is None or meeting.deleted_at is not None:
            return IngestionOutcome(IngestionDecision.TARGET_DELETED, record)
        return IngestionOutcome(IngestionDecision.REPLAYED, record)

    locked = db.scalar(
        select(IngestionRecord).where(IngestionRecord.id == record.id).with_for_update()
    )
    if locked is None:  # pragma: no cover - the row was removed underneath us
        raise IngestionError("ingestion_conflict", "Ingestion event conflicts", ingestion=record)
    locked.conflict_count += 1
    locked.last_conflict_at = now
    record_audit_event(
        db,
        event_type=AuditEventType.INGESTION_CONFLICT,
        actor_user_id=actor_user_id,
        workspace_id=locked.workspace_id,
        target_type="ingestion",
        target_id=locked.id,
        outcome="rejected",
        request_id=request_id,
        details=_audit_details(locked, conflict_count=locked.conflict_count),
        settings=settings,
    )
    return IngestionOutcome(IngestionDecision.CONFLICT, locked)


def accept_ingestion[T](
    db: Session,
    *,
    workspace: Workspace,
    identity: IngestionIdentity,
    payload: IngestionPayload,
    received_at: datetime,
    now: datetime,
    actor_user_id: str | None,
    api_key_id: str | None,
    request_id: str | None,
    settings: Settings,
    create: Callable[[IngestionRecord], T],
) -> IngestionOutcome:
    """Claim the identity and create the conversation in the caller's transaction.

    Order inside the transaction: lock the workspace row (serialises case C
    per workspace), evaluate the duplicate-media policy, insert the ledger
    row (``IntegrityError`` here means a concurrent claim won: the caller
    rolls back and calls :func:`resolve_existing`), then ``create`` the
    conversation / recording / root job and link them.  Raises
    :class:`IngestionError` for a policy refusal after writing its audit
    row; the caller commits in every non-exception path and for the refusal.
    """
    db.execute(select(Workspace.id).where(Workspace.id == workspace.id).with_for_update())

    duplicate = find_live_duplicate(
        db, workspace_id=workspace.id, media_sha256=payload.media_sha256
    )
    if duplicate is not None and workspace.duplicate_media_policy == DuplicateMediaPolicy.REJECT:
        record_audit_event(
            db,
            event_type=AuditEventType.INGESTION_DUPLICATE_MEDIA,
            actor_user_id=actor_user_id,
            workspace_id=workspace.id,
            target_type="ingestion",
            target_id=duplicate.id,
            outcome="rejected",
            request_id=request_id,
            details={
                "source_type": identity.source_type.value,
                "source_provider": identity.source_provider,
                "external_event_id": identity.external_event_id,
                "duplicate_of_id": duplicate.id,
                "policy": DuplicateMediaPolicy.REJECT.value,
            },
            settings=settings,
        )
        raise IngestionError(
            "duplicate_media",
            "This media was already ingested by another event of the workspace",
            ingestion=duplicate,
        )

    record = IngestionRecord(
        organization_id=workspace.organization_id,
        workspace_id=workspace.id,
        source_type=identity.source_type,
        source_provider=identity.source_provider,
        external_event_id=identity.external_event_id,
        external_source_id=identity.external_source_id,
        payload_hash=payload_digest(identity, payload),
        media_hash=payload.media_sha256,
        media_size_bytes=payload.media_size_bytes,
        declared_content_type=(payload.declared_content_type or "")[:120] or None,
        original_filename=sanitize_original_filename(payload.original_filename),
        status=IngestionStatus.ACCEPTED,
        received_at=received_at,
        created_at=now,
        created_by_user_id=actor_user_id,
        api_key_id=api_key_id,
        duplicate_of_id=duplicate.id if duplicate is not None else None,
    )
    db.add(record)
    db.flush()  # the unique index decides between concurrent claims here

    created = create(record)
    meeting_id = getattr(created, "meeting", None) and created.meeting.id
    job_id = getattr(created, "job", None) and created.job.id
    record.meeting_id = meeting_id
    record.job_id = job_id
    db.flush()

    if duplicate is not None:
        record_audit_event(
            db,
            event_type=AuditEventType.INGESTION_DUPLICATE_MEDIA,
            actor_user_id=actor_user_id,
            workspace_id=workspace.id,
            target_type="ingestion",
            target_id=record.id,
            outcome="linked",
            request_id=request_id,
            details=_audit_details(
                record, duplicate_of_id=duplicate.id, policy=DuplicateMediaPolicy.ALLOW.value
            ),
            settings=settings,
        )
    record_audit_event(
        db,
        event_type=AuditEventType.INGESTION_ACCEPTED,
        actor_user_id=actor_user_id,
        workspace_id=workspace.id,
        target_type="ingestion",
        target_id=record.id,
        request_id=request_id,
        details=_audit_details(
            record,
            meeting_id=meeting_id,
            job_id=job_id,
            media_size_bytes=record.media_size_bytes,
            duplicate_of_id=record.duplicate_of_id,
        ),
        settings=settings,
    )
    return IngestionOutcome(
        IngestionDecision.ACCEPTED, record, created=created, duplicate_of=duplicate
    )


def record_rejection(
    db: Session,
    *,
    workspace: Workspace,
    identity: IngestionIdentity,
    payload: IngestionPayload,
    reason: str,
    received_at: datetime,
    now: datetime,
    actor_user_id: str | None,
    api_key_id: str | None,
    request_id: str | None,
    settings: Settings,
) -> IngestionRecord:
    """Record a terminal media refusal against the event identity (WP05-T02).

    Inserted and flushed in the caller's transaction (which must be fresh: the
    upload transaction was rolled back); ``IntegrityError`` means a concurrent
    request claimed the identity first and the caller answers from its row.
    Nothing but the ledger row and its audit event is written.
    """
    record = IngestionRecord(
        organization_id=workspace.organization_id,
        workspace_id=workspace.id,
        source_type=identity.source_type,
        source_provider=identity.source_provider,
        external_event_id=identity.external_event_id,
        external_source_id=identity.external_source_id,
        payload_hash=payload_digest(identity, payload),
        media_hash=payload.media_sha256,
        media_size_bytes=payload.media_size_bytes,
        declared_content_type=(payload.declared_content_type or "")[:120] or None,
        original_filename=sanitize_original_filename(payload.original_filename),
        status=IngestionStatus.REJECTED,
        reason=reason[:80],
        received_at=received_at,
        created_at=now,
        created_by_user_id=actor_user_id,
        api_key_id=api_key_id,
    )
    db.add(record)
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.INGESTION_REJECTED,
        actor_user_id=actor_user_id,
        workspace_id=workspace.id,
        target_type="ingestion",
        target_id=record.id,
        outcome="rejected",
        request_id=request_id,
        details=_audit_details(
            record, reason=record.reason, terminal=True, media_size_bytes=record.media_size_bytes
        ),
        settings=settings,
    )
    return record


def audit_rejection(
    db: Session,
    *,
    workspace_id: str,
    identity: IngestionIdentity,
    reason: str,
    terminal: bool,
    actor_user_id: str | None,
    request_id: str | None,
    settings: Settings,
    media_size_bytes: int | None = None,
) -> None:
    """Audit a refusal that leaves no ledger row (transient, or before staging)."""
    details: dict[str, Any] = {
        "source_type": identity.source_type.value,
        "source_provider": identity.source_provider,
        "external_event_id": identity.external_event_id,
        "reason": reason[:80],
        "terminal": terminal,
    }
    if media_size_bytes is not None:
        details["media_size_bytes"] = media_size_bytes
    record_audit_event(
        db,
        event_type=AuditEventType.INGESTION_REJECTED,
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        target_type="ingestion",
        outcome="rejected" if terminal else "transient",
        request_id=request_id,
        details=details,
        settings=settings,
    )


def list_ingestions(
    db: Session,
    *,
    workspace_id: str,
    access: RecordAccess,
    limit: int,
    before: datetime | None = None,
    source_type: IngestionSourceType | None = None,
    external_event_id: str | None = None,
    meeting_id: str | None = None,
) -> list[IngestionRecord]:
    query = (
        select(IngestionRecord)
        .outerjoin(Meeting, Meeting.id == IngestionRecord.meeting_id)
        .where(IngestionRecord.workspace_id == workspace_id, visibility_clause(access))
    )
    if before is not None:
        query = query.where(IngestionRecord.received_at < before)
    if source_type is not None:
        query = query.where(IngestionRecord.source_type == source_type)
    if external_event_id is not None:
        query = query.where(IngestionRecord.external_event_id == external_event_id)
    if meeting_id is not None:
        query = query.where(IngestionRecord.meeting_id == meeting_id)
    return list(
        db.scalars(
            query.order_by(IngestionRecord.received_at.desc(), IngestionRecord.id.desc()).limit(
                limit
            )
        )
    )


def ingestion_by_id(
    db: Session, *, workspace_id: str, access: RecordAccess, ingestion_id: str
) -> IngestionRecord | None:
    return db.scalar(
        select(IngestionRecord)
        .outerjoin(Meeting, Meeting.id == IngestionRecord.meeting_id)
        .where(
            IngestionRecord.workspace_id == workspace_id,
            IngestionRecord.id == ingestion_id,
            visibility_clause(access),
        )
    )
