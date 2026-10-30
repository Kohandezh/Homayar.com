"""Generic CRM mapping contract (HOM-88, WP16-T04, F64, ADR 0048).

A CRM or ticket system (the "source system") sends records such as "call X belongs to
customer C, ticket T, agent A, outcome WON".  A mapping profile per workspace and source
system says where those ids sit in the payload.  This module:

* validates profiles (field paths, the phone guard, outcome and agent maps, the window);
* extracts the mapped values of a payload and stores only them (``extracted``), never
  the raw payload;
* records each received record once per ``(workspace, source_system, event_id)``: the
  same payload is a replay, another payload is a conflict, and the unique index decides
  between two concurrent inserts;
* finds the conversation by its external id and links its customer, ticket, agent and
  outcome by stable external ids;
* sends every case it must not decide alone to the reconciliation queue: a missing,
  ambiguous or changed id, an unknown agent or outcome, an inactive upstream record.
  Nothing is matched by phone, title or time, and a link is never overwritten;
* resolves queue items on a human decision.

Writes to the links and items of one conversation are serialised with a transaction
advisory lock on PostgreSQL, so two writers never open the same item twice or link the
same type twice.  ``meetings`` columns never change here: the agent link does not touch
``meetings.agent_user_id`` (record scope) and resolving never touches ``external_*``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.saas_models import (
    LATE_RECORD_WINDOW_MAX_SECONDS,
    LATE_RECORD_WINDOW_MIN_SECONDS,
    CanonicalOutcome,
    ConversationExternalLink,
    ExternalLinkType,
    ExternalRecord,
    InboundRecordState,
    IntegrationInboundRecord,
    IntegrationMappingProfile,
    IntegrationReconciliationItem,
    Meeting,
    Membership,
    ReconciliationReason,
    ReconciliationResolution,
    ReconciliationStatus,
    User,
    Workspace,
)
from backend.security.authorization import membership_effective_clause
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event

# The serialized payload may be at most this many bytes (UTF-8, canonical form).
MAX_PAYLOAD_BYTES = 65536
# An extracted id, outcome code or map key is at most this long.
MAX_VALUE_LENGTH = 200
MAX_PATH_SEGMENTS = 8

_SOURCE_SYSTEM = re.compile(r"[a-z][a-z0-9_.-]{0,59}")
_SEGMENT = re.compile(r"[A-Za-z0-9_-]{1,64}")

FIELD_KEYS: tuple[str, ...] = (
    "conversation_id",
    "customer_id",
    "ticket_id",
    "agent_id",
    "outcome",
    "customer_active",
    "ticket_active",
)
_FLAG_KEYS = frozenset({"customer_active", "ticket_active"})
# A customer id path whose last segment looks like a phone number is refused (F64: no
# phone-number-only identity).
_PHONE_PARTS = ("phone", "mobile", "msisdn")
_PHONE_NAMES = frozenset(
    {"tel", "telephone", "ani", "cli", "caller_id", "callerid", "caller_number"}
)
# Entity link type -> (extracted id key, extracted active flag key or None).
_ENTITY_KEYS: dict[str, tuple[str, str | None]] = {
    ExternalLinkType.CUSTOMER.value: ("customer_id", "customer_active"),
    ExternalLinkType.TICKET.value: ("ticket_id", "ticket_active"),
    ExternalLinkType.AGENT.value: ("agent_id", None),
    ExternalLinkType.OUTCOME.value: ("outcome", None),
}
_CONVERSATION_REASONS = frozenset(
    {
        ReconciliationReason.CONVERSATION_NOT_FOUND.value,
        ReconciliationReason.AMBIGUOUS_CONVERSATION.value,
    }
)
# (reason, resolution) -> the body fields that resolution needs; every other field must
# be absent.  ``dismiss`` is allowed for every reason, ``keep_current`` for ``changed_id``.
_RESOLUTIONS: dict[tuple[str, str], frozenset[str]] = {
    **{
        (reason.value, ReconciliationResolution.DISMISS.value): frozenset()
        for reason in ReconciliationReason
    },
    (ReconciliationReason.CHANGED_ID.value, ReconciliationResolution.KEEP_CURRENT.value): (
        frozenset()
    ),
    (ReconciliationReason.CHANGED_ID.value, ReconciliationResolution.LINK.value): frozenset(),
    (ReconciliationReason.INACTIVE_RECORD.value, ReconciliationResolution.LINK.value): (
        frozenset()
    ),
    (ReconciliationReason.UNKNOWN_AGENT.value, ReconciliationResolution.LINK.value): frozenset(
        {"user_id"}
    ),
    (ReconciliationReason.UNKNOWN_OUTCOME.value, ReconciliationResolution.LINK.value): (
        frozenset({"outcome"})
    ),
    (ReconciliationReason.AMBIGUOUS_CONVERSATION.value, ReconciliationResolution.LINK.value): (
        frozenset({"meeting_id"})
    ),
    (ReconciliationReason.CONVERSATION_NOT_FOUND.value, ReconciliationResolution.LINK.value): (
        frozenset({"meeting_id"})
    ),
}
PROFILE_FIELDS: tuple[str, ...] = (
    "conversation_source_system",
    "field_map",
    "outcome_map",
    "agent_map",
    "late_record_window_seconds",
    "is_active",
)


class CrmMappingError(Exception):
    """A refused request: the router answers ``status_code`` with ``code``."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _unprocessable(code: str, message: str) -> CrmMappingError:
    return CrmMappingError(422, code, message)


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; every time here is UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# --- mapping profiles ----------------------------------------------------------------------


def require_source_system(value: object, *, field: str = "source_system") -> str:
    if not isinstance(value, str) or not _SOURCE_SYSTEM.fullmatch(value):
        raise _unprocessable(
            "invalid_source_system",
            f"{field} must be 1 to 60 characters: a lowercase letter, then a-z, 0-9, _ . -",
        )
    return value


def _valid_path(path: object) -> bool:
    if not isinstance(path, str):
        return False
    segments = path.split(".")
    return len(segments) <= MAX_PATH_SEGMENTS and all(_SEGMENT.fullmatch(s) for s in segments)


def _phone_like(segment: str) -> bool:
    name = segment.lower()
    return name in _PHONE_NAMES or any(part in name for part in _PHONE_PARTS)


def _validate_field_map(field_map: object) -> dict[str, str]:
    if not isinstance(field_map, dict):
        raise _unprocessable("invalid_field_map", "field_map must be an object")
    for key, path in field_map.items():
        if key not in FIELD_KEYS:
            raise _unprocessable(
                "invalid_field_map", f"field_map key {key!r} is not one of {', '.join(FIELD_KEYS)}"
            )
        if not _valid_path(path):
            raise _unprocessable(
                "invalid_field_map",
                f"field_map {key} must be a dotted path of 1 to {MAX_PATH_SEGMENTS} segments, "
                "each 1 to 64 characters of A-Z a-z 0-9 _ -",
            )
    if "conversation_id" not in field_map:
        raise _unprocessable("invalid_field_map", "field_map needs conversation_id")
    customer_path = field_map.get("customer_id")
    if customer_path is not None and _phone_like(customer_path.rsplit(".", 1)[-1]):
        raise _unprocessable(
            "phone_identity_forbidden",
            "customer_id must be a stable customer id, never a phone number field",
        )
    return dict(field_map)


def _valid_map_key(key: object) -> bool:
    # A key with outer spaces could never match: extracted values are trimmed.
    return isinstance(key, str) and 0 < len(key) <= MAX_VALUE_LENGTH and key == key.strip()


def _validate_outcome_map(outcome_map: object) -> dict[str, str]:
    if not isinstance(outcome_map, dict):
        raise _unprocessable("invalid_outcome_map", "outcome_map must be an object")
    canonical = {member.value for member in CanonicalOutcome}
    for key, value in outcome_map.items():
        if not _valid_map_key(key) or not isinstance(value, str) or value not in canonical:
            raise _unprocessable(
                "invalid_outcome_map",
                "outcome_map maps codes of 1 to 200 characters to one of "
                + ", ".join(sorted(canonical)),
            )
    return dict(outcome_map)


def active_member_ids(
    db: Session, *, workspace_id: str, user_ids: Iterable[str], now: datetime
) -> set[str]:
    """The ids among ``user_ids`` that are active members of the workspace now."""
    wanted = sorted({user_id for user_id in user_ids if isinstance(user_id, str)})
    if not wanted:
        return set()
    rows = db.scalars(
        select(Membership.user_id)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.workspace_id == workspace_id,
            Membership.user_id.in_(wanted),
            membership_effective_clause(now),
            User.is_active.is_(True),
        )
    )
    return set(rows)


def _validate_agent_map(
    db: Session, *, workspace_id: str, agent_map: object, now: datetime
) -> dict[str, str]:
    if not isinstance(agent_map, dict):
        raise _unprocessable("invalid_agent_map", "agent_map must be an object")
    for key, value in agent_map.items():
        if not _valid_map_key(key) or not isinstance(value, str):
            raise _unprocessable(
                "invalid_agent_map", "agent_map maps agent ids of 1 to 200 characters to user ids"
            )
    members = active_member_ids(db, workspace_id=workspace_id, user_ids=agent_map.values(), now=now)
    if set(agent_map.values()) - members:
        raise _unprocessable(
            "invalid_agent_map", "every agent_map user must be an active member of this workspace"
        )
    return dict(agent_map)


@dataclass(frozen=True)
class ProfileValues:
    conversation_source_system: str
    field_map: dict[str, str]
    outcome_map: dict[str, str]
    agent_map: dict[str, str]
    late_record_window_seconds: int
    is_active: bool


def validate_profile(
    db: Session,
    *,
    workspace_id: str,
    source_system: str,
    conversation_source_system: str | None,
    field_map: object,
    outcome_map: object,
    agent_map: object,
    late_record_window_seconds: object,
    is_active: bool,
    now: datetime,
) -> ProfileValues:
    require_source_system(source_system)
    conversation_system = require_source_system(
        source_system if conversation_source_system is None else conversation_source_system,
        field="conversation_source_system",
    )
    fields = _validate_field_map(field_map)
    outcomes = _validate_outcome_map(outcome_map)
    agents = _validate_agent_map(db, workspace_id=workspace_id, agent_map=agent_map, now=now)
    window = late_record_window_seconds
    if (
        not isinstance(window, int)
        or isinstance(window, bool)
        or not LATE_RECORD_WINDOW_MIN_SECONDS <= window <= LATE_RECORD_WINDOW_MAX_SECONDS
    ):
        raise _unprocessable(
            "invalid_late_window",
            f"late_record_window_seconds must be {LATE_RECORD_WINDOW_MIN_SECONDS} to "
            f"{LATE_RECORD_WINDOW_MAX_SECONDS}",
        )
    return ProfileValues(conversation_system, fields, outcomes, agents, window, is_active)


def get_profile(
    db: Session, *, workspace_id: str, source_system: str, for_update: bool = False
) -> IntegrationMappingProfile | None:
    query = select(IntegrationMappingProfile).where(
        IntegrationMappingProfile.workspace_id == workspace_id,
        IntegrationMappingProfile.source_system == source_system,
    )
    if for_update:
        query = query.with_for_update()
    return db.scalar(query)


def list_profiles(db: Session, *, workspace_id: str) -> list[IntegrationMappingProfile]:
    return list(
        db.scalars(
            select(IntegrationMappingProfile)
            .where(IntegrationMappingProfile.workspace_id == workspace_id)
            .order_by(IntegrationMappingProfile.source_system.asc())
        )
    )


def put_profile(
    db: Session,
    *,
    workspace: Workspace,
    source_system: str,
    values: ProfileValues,
    actor_user_id: str,
    request_id: str | None,
    settings: Settings,
    now: datetime,
) -> tuple[IntegrationMappingProfile, bool]:
    """Create (version 1) or replace the profile.  Returns ``(profile, created)``.

    A replace that changes nothing keeps the version and writes no audit row; one that
    changes something adds 1 to the version.  The caller commits.
    """
    profile = get_profile(
        db, workspace_id=workspace.id, source_system=source_system, for_update=True
    )
    if profile is None:
        profile = IntegrationMappingProfile(
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            source_system=source_system,
            version=1,
            created_at=now,
            updated_at=now,
            updated_by_user_id=actor_user_id,
            **{name: getattr(values, name) for name in PROFILE_FIELDS},
        )
        try:
            with db.begin_nested():
                db.add(profile)
                db.flush()
        except IntegrityError:
            # A concurrent request created it first: this one replaces it.
            profile = get_profile(
                db, workspace_id=workspace.id, source_system=source_system, for_update=True
            )
            if profile is None:  # pragma: no cover - the row was removed underneath us
                raise
        else:
            _audit_profile(db, profile, list(PROFILE_FIELDS), actor_user_id, request_id, settings)
            return profile, True
    changed = [name for name in PROFILE_FIELDS if getattr(profile, name) != getattr(values, name)]
    if changed:
        for name in changed:
            setattr(profile, name, getattr(values, name))
        profile.version += 1
        profile.updated_at = now
        profile.updated_by_user_id = actor_user_id
        db.flush()
        _audit_profile(db, profile, changed, actor_user_id, request_id, settings)
    return profile, False


def _audit_profile(
    db: Session,
    profile: IntegrationMappingProfile,
    changed: list[str],
    actor_user_id: str,
    request_id: str | None,
    settings: Settings,
) -> None:
    # Field names only: map values hold agent user ids and payload paths.
    record_audit_event(
        db,
        event_type=AuditEventType.INTEGRATION_MAPPING_UPDATED,
        actor_user_id=actor_user_id,
        workspace_id=profile.workspace_id,
        target_type="integration_mapping_profile",
        target_id=profile.id,
        request_id=request_id,
        details={
            "source_system": profile.source_system,
            "version": profile.version,
            "fields": ",".join(changed),
        },
        settings=settings,
    )


# --- payloads ------------------------------------------------------------------------------


def canonical_payload(payload: dict[str, Any]) -> bytes:
    """The payload with sorted keys and compact separators, as UTF-8 bytes."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(payload)).hexdigest()


def _follow(payload: object, path: str) -> object:
    value = payload
    for segment in path.split("."):
        if not isinstance(value, dict) or segment not in value:
            return None
        value = value[segment]
    return value


def _absent(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _invalid_value(key: str) -> CrmMappingError:
    if key in _FLAG_KEYS:
        return _unprocessable("invalid_field_value", f"{key} must be a JSON boolean")
    return _unprocessable(
        "invalid_field_value", f"{key} must be a string or an integer of 1 to 200 characters"
    )


def extract(field_map: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    """The mapped values of ``payload`` (DESIGN 3.2); ``None`` for an absent value."""
    extracted: dict[str, Any] = dict.fromkeys(FIELD_KEYS)
    for key in FIELD_KEYS:
        path = field_map.get(key)
        raw = _follow(payload, path) if path is not None else None
        if _absent(raw):
            if key == "conversation_id":
                raise _unprocessable(
                    "conversation_id_missing",
                    "The payload has no value at the conversation_id path",
                )
            continue
        if key in _FLAG_KEYS:
            if not isinstance(raw, bool):
                raise _invalid_value(key)
            extracted[key] = raw
            continue
        if isinstance(raw, bool) or not isinstance(raw, int | str):
            raise _invalid_value(key)
        value = str(raw) if isinstance(raw, int) else raw.strip()
        if len(value) > MAX_VALUE_LENGTH:
            raise _invalid_value(key)
        extracted[key] = value
    return extracted


# --- received records ----------------------------------------------------------------------


@dataclass(frozen=True)
class RecordOutcome:
    record: IntegrationInboundRecord
    replayed: bool = False
    conflict: bool = False
    item_ids: tuple[str, ...] = ()


def find_inbound_record(
    db: Session, *, workspace_id: str, source_system: str, event_id: str
) -> IntegrationInboundRecord | None:
    return db.scalar(
        select(IntegrationInboundRecord).where(
            IntegrationInboundRecord.workspace_id == workspace_id,
            IntegrationInboundRecord.source_system == source_system,
            IntegrationInboundRecord.event_id == event_id,
        )
    )


def submit_record(
    db: Session,
    *,
    workspace: Workspace,
    source_system: str,
    event_id: str,
    payload: dict[str, Any],
    actor_user_id: str,
    api_key_id: str | None,
    request_id: str | None,
    settings: Settings,
    now: datetime,
) -> RecordOutcome:
    """Record one received CRM record and apply it (DESIGN 4).  The caller commits.

    Order: path slug, payload size, profile lookup, then a seen identity (replay or
    conflict), then the profile's ``is_active`` and the extraction, then the new row.  A
    seen event is settled before the profile state and the extraction, so a replay still
    answers after the profile is turned off.  A refusal raises :class:`CrmMappingError`
    before anything is written.
    """
    require_source_system(source_system)
    try:
        canonical = canonical_payload(payload)
        event_id.encode()
    except UnicodeEncodeError as error:  # a lone surrogate such as "\ud800"
        raise _unprocessable("invalid_payload", "The record cannot be encoded as UTF-8") from error
    if len(canonical) > MAX_PAYLOAD_BYTES:
        raise CrmMappingError(
            413, "payload_too_large", f"The payload is over {MAX_PAYLOAD_BYTES} bytes"
        )
    profile = get_profile(db, workspace_id=workspace.id, source_system=source_system)
    if profile is None:
        raise CrmMappingError(
            404, "mapping_profile_not_found", "No mapping profile for this system"
        )
    digest = hashlib.sha256(canonical).hexdigest()
    existing = find_inbound_record(
        db, workspace_id=workspace.id, source_system=source_system, event_id=event_id
    )
    if existing is not None:
        return _settle_existing(
            db,
            existing,
            digest,
            actor_user_id=actor_user_id,
            request_id=request_id,
            settings=settings,
            now=now,
        )
    if not profile.is_active:
        raise CrmMappingError(409, "mapping_profile_inactive", "The mapping profile is inactive")
    extracted = extract(profile.field_map, payload)
    record = IntegrationInboundRecord(
        organization_id=workspace.organization_id,
        workspace_id=workspace.id,
        profile_id=profile.id,
        source_system=source_system,
        event_id=event_id,
        payload_hash=digest,
        extracted=extracted,
        state=InboundRecordState.PENDING.value,
        received_at=now,
        expires_at=now + timedelta(seconds=profile.late_record_window_seconds),
        attempt_count=0,
        conflict_count=0,
        api_key_id=api_key_id,
        created_by_user_id=actor_user_id,
    )
    try:
        with db.begin_nested():
            db.add(record)
            db.flush()  # the unique index decides between concurrent inserts here
    except IntegrityError:
        existing = find_inbound_record(
            db, workspace_id=workspace.id, source_system=source_system, event_id=event_id
        )
        if existing is None:  # pragma: no cover - another constraint failed
            raise
        return _settle_existing(
            db,
            existing,
            digest,
            actor_user_id=actor_user_id,
            request_id=request_id,
            settings=settings,
            now=now,
        )
    route_record(db, record=record, profile=profile, now=now, sweep=False)
    return RecordOutcome(record, item_ids=tuple(record_item_ids(db, record)))


def _settle_existing(
    db: Session,
    record: IntegrationInboundRecord,
    digest: str,
    *,
    actor_user_id: str,
    request_id: str | None,
    settings: Settings,
    now: datetime,
) -> RecordOutcome:
    """Same identity: the same payload is a replay; another payload is a conflict."""
    if record.payload_hash == digest:
        return RecordOutcome(record, replayed=True, item_ids=tuple(record_item_ids(db, record)))
    # One atomic UPDATE: another request may have counted a conflict since this one read
    # the row, and an in-memory ``+= 1`` would lose that count.
    db.execute(
        update(IntegrationInboundRecord)
        .where(IntegrationInboundRecord.id == record.id)
        .values(
            conflict_count=IntegrationInboundRecord.conflict_count + 1,
            last_conflict_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    locked = db.scalar(
        select(IntegrationInboundRecord)
        .where(IntegrationInboundRecord.id == record.id)
        .execution_options(populate_existing=True)
    )
    if locked is None:  # pragma: no cover - the row was removed underneath us
        raise CrmMappingError(409, "inbound_record_conflict", "The event id was used before")
    record_audit_event(
        db,
        event_type=AuditEventType.INTEGRATION_RECORD_CONFLICT,
        actor_user_id=actor_user_id,
        workspace_id=locked.workspace_id,
        target_type="integration_inbound_record",
        target_id=locked.id,
        outcome="rejected",
        request_id=request_id,
        details={"source_system": locked.source_system, "conflict_count": locked.conflict_count},
        settings=settings,
    )
    return RecordOutcome(locked, conflict=True)


def record_item_ids(db: Session, record: IntegrationInboundRecord) -> list[str]:
    """The items a record has: the ones it opened, and the open ones it reused.

    A reused item belongs to the record that opened it; it is found again by the
    conversation, source system, link type, the record's own value for that type and the
    reason that value meets now (the same rule as when the record was applied).
    """
    profile = db.get(IntegrationMappingProfile, record.profile_id)
    now = datetime.now(UTC)
    ids = set(
        db.scalars(
            select(IntegrationReconciliationItem.id).where(
                IntegrationReconciliationItem.workspace_id == record.workspace_id,
                IntegrationReconciliationItem.inbound_record_id == record.id,
            )
        )
    )
    if record.meeting_id is not None and record.state == InboundRecordState.RECONCILIATION.value:
        for link_type, (key, _flag) in _ENTITY_KEYS.items():
            value = (record.extracted or {}).get(key)
            if value is None or profile is None:
                continue
            external = db.scalar(
                select(ExternalRecord).where(
                    ExternalRecord.workspace_id == record.workspace_id,
                    ExternalRecord.source_system == record.source_system,
                    ExternalRecord.record_type == link_type,
                    ExternalRecord.external_id == value,
                )
            )
            reason = _entity_blocker(
                db,
                record=record,
                profile=profile,
                link_type=link_type,
                value=value,
                external=external,
                now=now,
            )
            ids.update(
                db.scalars(
                    select(IntegrationReconciliationItem.id).where(
                        IntegrationReconciliationItem.workspace_id == record.workspace_id,
                        IntegrationReconciliationItem.meeting_id == record.meeting_id,
                        IntegrationReconciliationItem.source_system == record.source_system,
                        IntegrationReconciliationItem.link_type == link_type,
                        IntegrationReconciliationItem.candidate_value == value,
                        IntegrationReconciliationItem.reason
                        == (reason or ReconciliationReason.CHANGED_ID).value,
                        IntegrationReconciliationItem.status == ReconciliationStatus.OPEN.value,
                    )
                )
            )
    return sorted(ids)


# --- finding the conversation and applying a record ----------------------------------------


def match_conversations(
    db: Session, *, workspace_id: str, source_system: str, conversation_id: str
) -> list[str]:
    """Live conversations of the workspace with exactly this external reference."""
    return list(
        db.scalars(
            select(Meeting.id)
            .where(
                Meeting.workspace_id == workspace_id,
                Meeting.deleted_at.is_(None),
                Meeting.external_source_system == source_system,
                Meeting.external_conversation_id == conversation_id,
            )
            .order_by(Meeting.id.asc())
        )
    )


def lock_conversation(db: Session, meeting_id: str) -> None:
    """Serialise link and item writes of one conversation (PostgreSQL only)."""
    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"crm-links:{meeting_id}"},
    )


def route_record(
    db: Session,
    *,
    record: IntegrationInboundRecord,
    profile: IntegrationMappingProfile,
    now: datetime,
    sweep: bool,
) -> str:
    """Find the record's conversation and apply it.  Returns the record's new state.

    One match applies the record; two or more open one ``ambiguous_conversation`` item;
    none keeps it ``pending``.  In the sweep, a pending record past ``expires_at`` opens
    a ``conversation_not_found`` item, and one before it counts one more attempt.
    """
    matches = match_conversations(
        db,
        workspace_id=record.workspace_id,
        source_system=profile.conversation_source_system,
        conversation_id=record.extracted["conversation_id"],
    )
    if len(matches) == 1:
        return apply_to_conversation(
            db, record=record, profile=profile, meeting_id=matches[0], now=now
        )
    if len(matches) > 1:
        _open_conversation_item(
            db, record, ReconciliationReason.AMBIGUOUS_CONVERSATION, matches, now
        )
        return record.state
    if sweep:
        expired = record.expires_at is None or _aware(record.expires_at) <= now
        if expired:
            _open_conversation_item(
                db, record, ReconciliationReason.CONVERSATION_NOT_FOUND, [], now
            )
            return record.state
        record.attempt_count += 1
        record.last_attempt_at = now
    return record.state


def _open_conversation_item(
    db: Session,
    record: IntegrationInboundRecord,
    reason: ReconciliationReason,
    candidates: list[str],
    now: datetime,
) -> str:
    """One item per record and conversation reason; the record leaves ``pending``."""
    existing = db.scalar(
        select(IntegrationReconciliationItem.id).where(
            IntegrationReconciliationItem.workspace_id == record.workspace_id,
            IntegrationReconciliationItem.inbound_record_id == record.id,
            IntegrationReconciliationItem.reason == reason.value,
            IntegrationReconciliationItem.status == ReconciliationStatus.OPEN.value,
        )
    )
    record.state = InboundRecordState.RECONCILIATION.value
    record.expires_at = None
    if existing is not None:
        return existing
    item = IntegrationReconciliationItem(
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        source_system=record.source_system,
        inbound_record_id=record.id,
        reason=reason.value,
        status=ReconciliationStatus.OPEN.value,
        candidate_value=record.extracted["conversation_id"],
        candidate_meeting_ids=sorted(candidates),
        created_at=now,
    )
    db.add(item)
    db.flush()
    return item.id


def apply_to_conversation(
    db: Session,
    *,
    record: IntegrationInboundRecord,
    profile: IntegrationMappingProfile,
    meeting_id: str,
    now: datetime,
    linked_by_user_id: str | None = None,
) -> str:
    """Handle each present entity on its own (DESIGN 4.2).  Returns the record's state.

    Clean entities link even when another entity of the same record goes to
    reconciliation.  ``linked_by_user_id`` is set when a human chose the conversation.
    """
    lock_conversation(db, meeting_id)
    record.meeting_id = meeting_id
    extracted = record.extracted
    opened: list[str] = []
    for link_type, (key, flag) in _ENTITY_KEYS.items():
        value = extracted.get(key)
        if value is None:
            continue
        external: ExternalRecord | None = None
        fields: dict[str, Any]
        if link_type == ExternalLinkType.OUTCOME.value:
            fields = {"outcome": profile.outcome_map.get(value), "external_outcome_code": value}
        else:
            external = upsert_external_record(
                db,
                record=record,
                record_type=link_type,
                external_id=value,
                active=extracted.get(flag) if flag else None,
            )
            fields = {"external_record_id": external.id}
            if link_type == ExternalLinkType.AGENT.value:
                fields["agent_user_id"] = profile.agent_map.get(value)
        blocker = _entity_blocker(
            db,
            record=record,
            profile=profile,
            link_type=link_type,
            value=value,
            external=external,
            now=now,
        )
        item_id = _link_or_queue(
            db,
            record=record,
            profile=profile,
            meeting_id=meeting_id,
            link_type=link_type,
            value=value,
            blocker=blocker,
            fields=fields,
            linked_by_user_id=linked_by_user_id,
            now=now,
        )
        if item_id is not None:
            opened.append(item_id)
    record.state = (
        InboundRecordState.RECONCILIATION.value if opened else InboundRecordState.APPLIED.value
    )
    record.expires_at = None
    db.flush()
    return record.state


def _entity_blocker(
    db: Session,
    *,
    record: IntegrationInboundRecord,
    profile: IntegrationMappingProfile,
    link_type: str,
    value: str,
    external: ExternalRecord | None,
    now: datetime,
) -> ReconciliationReason | None:
    """Why this entity may not link on its own, or ``None`` (DESIGN 4.2)."""
    if link_type == ExternalLinkType.OUTCOME.value:
        return None if value in profile.outcome_map else ReconciliationReason.UNKNOWN_OUTCOME
    if link_type == ExternalLinkType.AGENT.value:
        user_id = profile.agent_map.get(value)
        members = active_member_ids(
            db, workspace_id=record.workspace_id, user_ids=[user_id] if user_id else [], now=now
        )
        return None if user_id in members else ReconciliationReason.UNKNOWN_AGENT
    if external is not None and not external.is_active:
        return ReconciliationReason.INACTIVE_RECORD
    return None


def upsert_external_record(
    db: Session,
    *,
    record: IntegrationInboundRecord,
    record_type: str,
    external_id: str,
    active: bool | None,
) -> ExternalRecord:
    """The record's ``(source_system, record_type, external_id)``, created on first sight.

    ``last_seen_at`` is the newest ``received_at`` of the records that touched it.
    ``is_active`` copies what the source system says, but only from a record that is not
    older than ``last_seen_at``: an older delayed record never changes it.  When a
    record says nothing, the stored value stays (a new record starts active).  Homayar
    never reactivates on its own.  The row is locked, so two writers compare fresh times.
    """
    seen_at = _aware(record.received_at)
    query = (
        select(ExternalRecord)
        .where(
            ExternalRecord.workspace_id == record.workspace_id,
            ExternalRecord.source_system == record.source_system,
            ExternalRecord.record_type == record_type,
            ExternalRecord.external_id == external_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    row = db.scalar(query)
    if row is None:
        row = ExternalRecord(
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            source_system=record.source_system,
            record_type=record_type,
            external_id=external_id,
            is_active=True if active is None else active,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
            return row
        except IntegrityError:
            row = db.scalar(query)  # a concurrent writer created it first
            if row is None:  # pragma: no cover
                raise
    newest = _aware(row.last_seen_at)
    if active is not None and seen_at >= newest:
        row.is_active = active
    if seen_at > newest:
        row.last_seen_at = seen_at
    if seen_at < _aware(row.first_seen_at):
        row.first_seen_at = seen_at
    return row


def current_link(
    db: Session, *, meeting_id: str, source_system: str, link_type: str
) -> ConversationExternalLink | None:
    return db.scalar(
        select(ConversationExternalLink).where(
            ConversationExternalLink.meeting_id == meeting_id,
            ConversationExternalLink.source_system == source_system,
            ConversationExternalLink.link_type == link_type,
        )
    )


def _link_value(db: Session, link: ConversationExternalLink) -> str | None:
    if link.link_type == ExternalLinkType.OUTCOME.value:
        return link.external_outcome_code
    external = db.get(ExternalRecord, link.external_record_id) if link.external_record_id else None
    return external.external_id if external is not None else None


def _link_or_queue(
    db: Session,
    *,
    record: IntegrationInboundRecord,
    profile: IntegrationMappingProfile,
    meeting_id: str,
    link_type: str,
    value: str,
    blocker: ReconciliationReason | None,
    fields: dict[str, Any],
    linked_by_user_id: str | None,
    now: datetime,
) -> str | None:
    """Link, keep, or queue one entity (DESIGN 4.3).  Returns the item id it opened or reused.

    The same value as the current link changes nothing.  A blocker (inactive record,
    unknown agent, unknown outcome) opens its item.  No current link: link.  Another
    current value: never overwrite, open ``changed_id``.
    """
    link = current_link(
        db, meeting_id=meeting_id, source_system=record.source_system, link_type=link_type
    )
    current_value = _link_value(db, link) if link is not None else None
    if link is not None and current_value == value:
        return None
    if blocker is not None:
        return _open_entity_item(
            db, record, blocker, meeting_id, link_type, current_value, value, now
        )
    if link is None:
        set_link(
            db,
            record=record,
            profile=profile,
            meeting_id=meeting_id,
            link_type=link_type,
            fields=fields,
            linked_by_user_id=linked_by_user_id,
            now=now,
        )
        return None
    return _open_entity_item(
        db,
        record,
        ReconciliationReason.CHANGED_ID,
        meeting_id,
        link_type,
        current_value,
        value,
        now,
    )


def _open_entity_item(
    db: Session,
    record: IntegrationInboundRecord,
    reason: ReconciliationReason,
    meeting_id: str,
    link_type: str,
    current_value: str | None,
    candidate_value: str,
    now: datetime,
) -> str:
    """Reuse the open item of the same case, or open one."""
    existing = db.scalar(
        select(IntegrationReconciliationItem.id)
        .where(
            IntegrationReconciliationItem.workspace_id == record.workspace_id,
            IntegrationReconciliationItem.meeting_id == meeting_id,
            IntegrationReconciliationItem.source_system == record.source_system,
            IntegrationReconciliationItem.link_type == link_type,
            IntegrationReconciliationItem.reason == reason.value,
            IntegrationReconciliationItem.candidate_value == candidate_value,
            IntegrationReconciliationItem.status == ReconciliationStatus.OPEN.value,
        )
        .order_by(IntegrationReconciliationItem.created_at.asc())
        .limit(1)
    )
    if existing is not None:
        return existing
    item = IntegrationReconciliationItem(
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        source_system=record.source_system,
        inbound_record_id=record.id,
        meeting_id=meeting_id,
        link_type=link_type,
        reason=reason.value,
        status=ReconciliationStatus.OPEN.value,
        current_value=current_value,
        candidate_value=candidate_value,
        candidate_meeting_ids=[],
        created_at=now,
    )
    db.add(item)
    db.flush()
    return item.id


def set_link(
    db: Session,
    *,
    record: IntegrationInboundRecord,
    profile: IntegrationMappingProfile,
    meeting_id: str,
    link_type: str,
    fields: dict[str, Any],
    linked_by_user_id: str | None,
    now: datetime,
) -> ConversationExternalLink:
    """Create the link, or (on a human decision only) replace the current one."""
    values = {
        "external_record_id": None,
        "outcome": None,
        "external_outcome_code": None,
        "agent_user_id": None,
        **fields,
        "profile_version": profile.version,
        "inbound_record_id": record.id,
        "linked_by_user_id": linked_by_user_id,
        "linked_at": now,
    }
    link = current_link(
        db, meeting_id=meeting_id, source_system=record.source_system, link_type=link_type
    )
    if link is None:
        link = ConversationExternalLink(
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            meeting_id=meeting_id,
            source_system=record.source_system,
            link_type=link_type,
            **values,
        )
        db.add(link)
    else:
        for name, value in values.items():
            setattr(link, name, value)
    db.flush()
    return link


# --- the reconciliation queue --------------------------------------------------------------


def list_items(
    db: Session,
    *,
    workspace_id: str,
    status: str,
    source_system: str | None,
    reason: str | None,
    limit: int,
    before: datetime | None,
    before_id: str | None = None,
) -> list[IntegrationReconciliationItem]:
    """One page, newest first.  The cursor is tie-safe: the items of one record or one
    sweep run share ``created_at``, so ``before_id`` breaks the tie by id."""
    query = select(IntegrationReconciliationItem).where(
        IntegrationReconciliationItem.workspace_id == workspace_id,
        IntegrationReconciliationItem.status == status,
    )
    if source_system is not None:
        query = query.where(IntegrationReconciliationItem.source_system == source_system)
    if reason is not None:
        query = query.where(IntegrationReconciliationItem.reason == reason)
    if before is not None and before_id is not None:
        query = query.where(
            or_(
                IntegrationReconciliationItem.created_at < before,
                and_(
                    IntegrationReconciliationItem.created_at == before,
                    IntegrationReconciliationItem.id < before_id,
                ),
            )
        )
    elif before is not None:
        query = query.where(IntegrationReconciliationItem.created_at < before)
    return list(
        db.scalars(
            query.order_by(
                IntegrationReconciliationItem.created_at.desc(),
                IntegrationReconciliationItem.id.desc(),
            ).limit(limit)
        )
    )


def get_item(
    db: Session, *, workspace_id: str, item_id: str, for_update: bool = False
) -> IntegrationReconciliationItem:
    query = select(IntegrationReconciliationItem).where(
        IntegrationReconciliationItem.workspace_id == workspace_id,
        IntegrationReconciliationItem.id == item_id,
    )
    if for_update:
        query = query.with_for_update()
    item = db.scalar(query)
    if item is None:
        raise CrmMappingError(404, "reconciliation_item_not_found", "Reconciliation item not found")
    return item


def _invalid_resolution(message: str) -> CrmMappingError:
    return _unprocessable("invalid_resolution", message)


def _optional_text(name: str, value: object) -> str | None:
    """A resolve body field: absent (``None``) or a string, else ``invalid_resolution``."""
    if value is None or isinstance(value, str):
        return value
    raise _invalid_resolution(f"{name} must be a string")


def _live_conversation(db: Session, *, workspace_id: str, meeting_id: str) -> Meeting | None:
    return db.scalar(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.workspace_id == workspace_id,
            Meeting.deleted_at.is_(None),
        )
    )


def resolve_item(
    db: Session,
    *,
    workspace_id: str,
    item_id: str,
    resolution: object,
    meeting_id: object,
    user_id: object,
    outcome: object,
    actor_user_id: str,
    request_id: str | None,
    settings: Settings,
    now: datetime,
) -> IntegrationReconciliationItem:
    """Close one open item on a human decision (DESIGN 5.3).  The caller commits.

    ``link`` makes the link the item proposes (or applies the record to the chosen
    conversation); ``keep_current`` keeps the current link of a ``changed_id`` item;
    ``dismiss`` changes nothing.  ``meetings.external_*`` never change.
    """
    item = get_item(db, workspace_id=workspace_id, item_id=item_id, for_update=True)
    if item.status != ReconciliationStatus.OPEN.value:
        raise CrmMappingError(409, "reconciliation_item_closed", "The item is already closed")
    if not isinstance(resolution, str):
        raise _invalid_resolution("resolution must be one of link, keep_current, dismiss")
    chosen_meeting = _optional_text("meeting_id", meeting_id)
    chosen_user = _optional_text("user_id", user_id)
    chosen_outcome = _optional_text("outcome", outcome)
    needed = _RESOLUTIONS.get((item.reason, resolution))
    if needed is None:
        raise _invalid_resolution(f"{resolution!r} is not a resolution of a {item.reason} item")
    given = {
        name
        for name, value in (
            ("meeting_id", chosen_meeting),
            ("user_id", chosen_user),
            ("outcome", chosen_outcome),
        )
        if value is not None
    }
    if given != needed:
        raise _invalid_resolution(
            f"A {resolution} of a {item.reason} item takes "
            + (", ".join(sorted(needed)) if needed else "no other field")
        )
    record = db.get(IntegrationInboundRecord, item.inbound_record_id)
    profile = db.get(IntegrationMappingProfile, record.profile_id) if record else None
    if record is None or profile is None:  # pragma: no cover - cascades remove the item
        raise _invalid_resolution("The item's record is gone")

    target_meeting = item.meeting_id
    if resolution == ReconciliationResolution.LINK.value:
        if item.reason in _CONVERSATION_REASONS:
            target_meeting = _apply_chosen_conversation(
                db,
                item=item,
                record=record,
                profile=profile,
                meeting_id=chosen_meeting,
                actor_user_id=actor_user_id,
                now=now,
            )
        else:
            _link_candidate(
                db,
                item=item,
                record=record,
                profile=profile,
                user_id=chosen_user,
                outcome=chosen_outcome,
                actor_user_id=actor_user_id,
                now=now,
            )
    item.status = (
        ReconciliationStatus.DISMISSED.value
        if resolution == ReconciliationResolution.DISMISS.value
        else ReconciliationStatus.RESOLVED.value
    )
    item.resolution = resolution
    item.resolved_by_user_id = actor_user_id
    item.resolved_at = now
    db.flush()
    details = {
        "reason": item.reason,
        "resolution": resolution,
        "source_system": item.source_system,
        "link_type": item.link_type,
        "meeting_id": target_meeting,
        "inbound_record_id": item.inbound_record_id,
    }
    record_audit_event(
        db,
        event_type=AuditEventType.RECONCILIATION_ITEM_RESOLVED,
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        target_type="reconciliation_item",
        target_id=item.id,
        request_id=request_id,
        details={key: value for key, value in details.items() if value is not None},
        settings=settings,
    )
    return item


def _apply_chosen_conversation(
    db: Session,
    *,
    item: IntegrationReconciliationItem,
    record: IntegrationInboundRecord,
    profile: IntegrationMappingProfile,
    meeting_id: str | None,
    actor_user_id: str,
    now: datetime,
) -> str:
    if item.reason == ReconciliationReason.AMBIGUOUS_CONVERSATION.value and meeting_id not in (
        item.candidate_meeting_ids or []
    ):
        raise _invalid_resolution("meeting_id must be one of the item's candidate_meeting_ids")
    if (
        meeting_id is None
        or _live_conversation(db, workspace_id=item.workspace_id, meeting_id=meeting_id) is None
    ):
        raise _invalid_resolution("meeting_id must be a live conversation of this workspace")
    apply_to_conversation(
        db,
        record=record,
        profile=profile,
        meeting_id=meeting_id,
        now=now,
        linked_by_user_id=actor_user_id,
    )
    return meeting_id


def _link_candidate(
    db: Session,
    *,
    item: IntegrationReconciliationItem,
    record: IntegrationInboundRecord,
    profile: IntegrationMappingProfile,
    user_id: str | None,
    outcome: str | None,
    actor_user_id: str,
    now: datetime,
) -> None:
    """Make the link an entity item proposes, replacing the current one."""
    if (
        item.meeting_id is None
        or _live_conversation(db, workspace_id=item.workspace_id, meeting_id=item.meeting_id)
        is None
    ):
        raise _invalid_resolution("The item's conversation is gone")
    lock_conversation(db, item.meeting_id)
    value, link_type = item.candidate_value, item.link_type
    if value is None or link_type is None:  # pragma: no cover - entity items carry both
        raise _invalid_resolution("The item names no value to link")
    if link_type == ExternalLinkType.OUTCOME.value:
        if item.reason == ReconciliationReason.UNKNOWN_OUTCOME.value:
            canonical = outcome if outcome in {m.value for m in CanonicalOutcome} else None
        else:
            canonical = profile.outcome_map.get(value)
        if canonical is None:
            raise _invalid_resolution("outcome must be one of the canonical outcomes")
        fields: dict[str, Any] = {"outcome": canonical, "external_outcome_code": value}
    else:
        external = db.scalar(
            select(ExternalRecord).where(
                ExternalRecord.workspace_id == item.workspace_id,
                ExternalRecord.source_system == item.source_system,
                ExternalRecord.record_type == link_type,
                ExternalRecord.external_id == value,
            )
        )
        if external is None:  # pragma: no cover - external records are never deleted alone
            raise _invalid_resolution("The proposed external record is gone")
        fields = {"external_record_id": external.id}
        if link_type == ExternalLinkType.AGENT.value:
            agent_user = user_id if user_id is not None else profile.agent_map.get(value)
            members = active_member_ids(
                db,
                workspace_id=item.workspace_id,
                user_ids=[agent_user] if agent_user else [],
                now=now,
            )
            if agent_user not in members:
                raise _invalid_resolution("user_id must be an active member of this workspace")
            fields["agent_user_id"] = agent_user
    set_link(
        db,
        record=record,
        profile=profile,
        meeting_id=item.meeting_id,
        link_type=link_type,
        fields=fields,
        linked_by_user_id=actor_user_id,
        now=now,
    )


# --- conversation links --------------------------------------------------------------------


@dataclass(frozen=True)
class LinkView:
    source_system: str
    link_type: str
    external_id: str | None
    is_active: bool | None
    outcome: str | None
    agent_user_id: str | None
    profile_version: int
    linked_at: datetime
    linked_by_user_id: str | None


def conversation_links(db: Session, *, workspace_id: str, meeting_id: str) -> list[LinkView]:
    rows = db.execute(
        select(ConversationExternalLink, ExternalRecord)
        .outerjoin(ExternalRecord, ExternalRecord.id == ConversationExternalLink.external_record_id)
        .where(
            ConversationExternalLink.workspace_id == workspace_id,
            ConversationExternalLink.meeting_id == meeting_id,
        )
        .order_by(
            ConversationExternalLink.source_system.asc(), ConversationExternalLink.link_type.asc()
        )
    ).all()
    return [
        LinkView(
            source_system=link.source_system,
            link_type=link.link_type,
            external_id=external.external_id
            if external is not None
            else link.external_outcome_code,
            is_active=external.is_active if external is not None else None,
            outcome=link.outcome,
            agent_user_id=link.agent_user_id,
            profile_version=link.profile_version,
            linked_at=link.linked_at,
            linked_by_user_id=link.linked_by_user_id,
        )
        for link, external in rows
    ]
