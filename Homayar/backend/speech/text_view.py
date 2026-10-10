"""Derived normalised text view and entities of a transcript revision (WP22/F36, ADR 0034).

The post-call processor calls :func:`derive_text_view` in the fenced
transaction that creates a transcript revision.  It writes a *derived* view
next to the revision: every segment normalised by the speech profile of
:mod:`backend.speech.text_normalize` for the revision's language, and the
entities :func:`backend.speech.entities.extract_entities` finds in it.  The
raw transcript is never changed (HOM-120): the view is new rows, and entity
offsets refer to the stored source text of each segment.

Source text (the PII decision of ADR 0034): under an enforced workspace
redaction policy the view is derived from the persisted redacted version of
the revision, the text every reader gets by default (ADR 0024), so it never
holds an identifier that redaction removed; without a policy it is derived
from the original segments, which every transcript reader may read.
``input_view``/``input_policy_digest`` say which, like ``analysis_runs``.

Identity is (revision, ``view_digest``); the digest covers the normaliser,
extractor and PII detector versions, the encoding of stored values, the
language, the source view and the reference time.  A version change yields a
new view and an existing one is never rewritten (immutable rows); creation is
idempotent and tolerates a concurrent creator (savepoint + unique constraint).

Honest states (F23): ``completed``; ``partial`` when normalisation succeeded
and entity extraction failed (no entity is kept); ``failed`` when the text
could not be normalised, read or stored; ``skipped`` for an empty transcript
or a language without a normaliser.  Nothing here can fail the transcript:
an exception becomes a recorded state.  Logs and audits carry ids, states,
counts, versions and exception types only, never text or entity values.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.saas_models import (
    StageState,
    TextViewEntity,
    TextViewSegment,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptTextView,
)
from backend.jobs.leases import as_utc
from backend.privacy.redaction_service import (
    RedactionError,
    find_redaction,
    policy_digest,
    redact_revision,
    redacted_segments,
)
from backend.privacy.text_pii import DETECTOR_VERSION, PiiClass
from backend.privacy.transcript_view import VIEW_ORIGINAL, VIEW_REDACTED
from backend.services.logging_service import logger
from backend.speech.dates import TEHRAN, DateRange
from backend.speech.entities import EXTRACTOR_VERSION, extract_entities
from backend.speech.text_normalize import get_normalizer

# The speech profile of backend.speech.text_normalize (normalize_fa/en/az with
# their defaults: no colloquial rewriting, Persian punctuation on).  Its
# behaviour is frozen by the HOM-120 golden tests; any intended change to it
# bumps this version (test_text_view pins its output on a fixed corpus).
NORMALIZER_VERSION = "speech-normalize/1"
# How entity values and candidates are encoded as JSON (dates and times ISO
# 8601, date ranges as {"start", "end"}).
VALUE_ENCODING = 1

REASON_NO_TRANSCRIPT = "no_transcript"
REASON_NORMALIZER_UNAVAILABLE = "normalizer_unavailable"
REASON_NORMALIZATION_FAILED = "normalization_failed"
REASON_EXTRACTION_FAILED = "entity_extraction_failed"
REASON_SOURCE_UNAVAILABLE = "source_unavailable"
REASON_PERSIST_FAILED = "persist_failed"


@dataclass(frozen=True)
class SourceSegment:
    segment_id: str
    sequence: int
    text: str


@dataclass(frozen=True)
class DerivedEntity:
    segment_id: str
    entity_type: str
    start: int
    end: int
    value: Any
    candidates: tuple[Any, ...]
    confidence: float
    ambiguous: bool
    source: str


@dataclass(frozen=True)
class DerivedText:
    state: StageState
    reason_code: str | None
    segments: tuple[tuple[SourceSegment, str], ...]  # (source, normalised text)
    entities: tuple[DerivedEntity, ...]


@dataclass(frozen=True)
class TextViewOutcome:
    """What the processor reports about the view (ids, state and counts only)."""

    view_id: str | None
    state: StageState
    reason_code: str | None
    input_view: str
    entity_count: int

    def audit_details(self) -> dict[str, object]:
        # Flat values: the audit sanitizer stringifies nested ones.
        return {
            "text_view_id": self.view_id,
            "text_view_state": self.state.value,
            "text_view_reason": self.reason_code,
            "text_view_input": self.input_view,
            "text_view_entities": self.entity_count,
        }


class _SourceUnavailable(RuntimeError):
    """The redacted text of some segment of the revision could not be read."""


def json_value(value: Any) -> Any:
    """An extractor value as JSON (VALUE_ENCODING 1); unknown types are refused."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, DateRange):
        return {"start": value.start.isoformat(), "end": value.end.isoformat()}
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_value(item) for item in value]
    raise TypeError(f"Unsupported entity value type: {type(value).__name__}")


def _entities(
    sources: Sequence[SourceSegment], reference: datetime, tz: ZoneInfo
) -> list[DerivedEntity]:
    found: list[DerivedEntity] = []
    for source in sources:
        for entity in extract_entities(source.text, reference, tz):
            found.append(
                DerivedEntity(
                    segment_id=source.segment_id,
                    entity_type=entity.type,
                    start=entity.start,
                    end=entity.end,
                    value=json_value(entity.value),
                    candidates=tuple(json_value(item) for item in entity.candidates),
                    confidence=float(entity.confidence),
                    ambiguous=bool(entity.ambiguous),
                    source=entity.source,
                )
            )
    return found


def derive(
    sources: Sequence[SourceSegment],
    *,
    language: str,
    reference: datetime,
    tz: ZoneInfo = TEHRAN,
) -> DerivedText:
    """The view of ``sources`` (pure; never raises, failures become states)."""
    ordered = sorted(sources, key=lambda source: source.sequence)
    if not ordered:
        return DerivedText(StageState.SKIPPED, REASON_NO_TRANSCRIPT, (), ())
    try:
        normalizer = get_normalizer(language)
    except KeyError:
        return DerivedText(StageState.SKIPPED, REASON_NORMALIZER_UNAVAILABLE, (), ())
    try:
        normalized = tuple((source, normalizer(source.text)) for source in ordered)
    except Exception as exc:
        logger.warning("TEXT_VIEW_NORMALIZATION_FAILED error_type=%s", type(exc).__name__)
        return DerivedText(StageState.FAILED, REASON_NORMALIZATION_FAILED, (), ())
    try:
        entities = tuple(_entities(ordered, reference, tz))
    except Exception as exc:
        logger.warning("TEXT_VIEW_EXTRACTION_FAILED error_type=%s", type(exc).__name__)
        return DerivedText(StageState.PARTIAL, REASON_EXTRACTION_FAILED, normalized, ())
    return DerivedText(StageState.COMPLETED, None, normalized, entities)


def view_digest(
    *,
    language: str,
    input_view: str,
    input_policy_digest: str | None,
    reference_at: datetime,
    tz: ZoneInfo,
) -> str:
    payload = {
        "normalizer": NORMALIZER_VERSION,
        "extractor": EXTRACTOR_VERSION,
        "detector": DETECTOR_VERSION,
        "values": VALUE_ENCODING,
        "language": language,
        "view": input_view,
        "policy": input_policy_digest,
        "reference_at": as_utc(reference_at).isoformat(),
        "timezone": tz.key,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def find_text_view(
    db: Session, *, workspace_id: str, revision_id: str, digest: str
) -> TranscriptTextView | None:
    return db.scalar(
        select(TranscriptTextView).where(
            TranscriptTextView.workspace_id == workspace_id,
            TranscriptTextView.revision_id == revision_id,
            TranscriptTextView.view_digest == digest,
        )
    )


def _outcome(view: TranscriptTextView) -> TextViewOutcome:
    return TextViewOutcome(
        view_id=view.id,
        state=view.state,
        reason_code=view.reason_code,
        input_view=view.input_view,
        entity_count=view.entity_count,
    )


@dataclass(frozen=True)
class _Identity:
    revision: TranscriptRevision
    classes: tuple[PiiClass, ...]
    input_view: str
    input_policy_digest: str | None
    reference_at: datetime
    tz: ZoneInfo
    digest: str
    job_id: str | None
    attempt: int | None

    def header(
        self,
        *,
        state: StageState,
        reason_code: str | None,
        redaction_id: str | None,
        segment_count: int,
        entity_count: int,
        now: datetime,
    ) -> TranscriptTextView:
        return TranscriptTextView(
            workspace_id=self.revision.workspace_id,
            meeting_id=self.revision.meeting_id,
            revision_id=self.revision.id,
            redaction_id=redaction_id,
            input_view=self.input_view,
            input_policy_digest=self.input_policy_digest,
            language=self.revision.language,
            normalizer_version=NORMALIZER_VERSION,
            extractor_version=EXTRACTOR_VERSION,
            detector_version=DETECTOR_VERSION,
            view_digest=self.digest,
            reference_at=self.reference_at,
            timezone=self.tz.key,
            state=state,
            reason_code=reason_code,
            segment_count=segment_count,
            entity_count=entity_count,
            job_id=self.job_id,
            attempt=self.attempt,
            created_at=now,
        )


def _sources(
    db: Session,
    identity: _Identity,
    segments: Sequence[TranscriptSegment],
    *,
    settings: Settings,
    now: datetime,
) -> tuple[list[SourceSegment], str | None]:
    """The text the view is derived from, and the redaction it came from."""
    originals = [
        SourceSegment(segment.id, segment.sequence, segment.text)
        for segment in sorted(segments, key=lambda segment: segment.sequence)
    ]
    if not identity.classes:
        return originals, None
    # Idempotent: the processor already created this version for the revision.
    redaction = redact_revision(
        db, revision=identity.revision, classes=identity.classes, settings=settings, now=now
    )
    texts = {row.segment_id: row.text for row in redacted_segments(db, redaction=redaction)}
    if set(texts) != {source.segment_id for source in originals}:
        raise _SourceUnavailable(redaction.id)
    return [
        SourceSegment(source.segment_id, source.sequence, texts[source.segment_id])
        for source in originals
    ], redaction.id


def _persist(
    db: Session,
    identity: _Identity,
    derived: DerivedText,
    *,
    redaction_id: str | None,
    now: datetime,
) -> TranscriptTextView:
    per_segment: dict[str, int] = {}
    for entity in derived.entities:
        per_segment[entity.segment_id] = per_segment.get(entity.segment_id, 0) + 1
    view = identity.header(
        state=derived.state,
        reason_code=derived.reason_code,
        redaction_id=redaction_id,
        segment_count=len(derived.segments),
        entity_count=len(derived.entities),
        now=now,
    )
    db.add(view)
    db.flush()
    revision = identity.revision
    for source, text in derived.segments:
        db.add(
            TextViewSegment(
                workspace_id=revision.workspace_id,
                meeting_id=revision.meeting_id,
                view_id=view.id,
                segment_id=source.segment_id,
                sequence=source.sequence,
                text=text,
                entity_count=per_segment.get(source.segment_id, 0),
            )
        )
    for position, entity in enumerate(derived.entities):
        db.add(
            TextViewEntity(
                workspace_id=revision.workspace_id,
                meeting_id=revision.meeting_id,
                view_id=view.id,
                segment_id=entity.segment_id,
                position=position,
                entity_type=entity.entity_type,
                start_offset=entity.start,
                end_offset=entity.end,
                value=entity.value,
                candidates=list(entity.candidates),
                confidence=entity.confidence,
                ambiguous=entity.ambiguous,
                source=entity.source,
            )
        )
    db.flush()
    return view


def _record_failure(
    db: Session, identity: _Identity, *, reason: str, now: datetime
) -> TextViewOutcome:
    """Record a view that could not be derived or stored (no output rows).

    Best effort in its own savepoint: if even this fails, the caller's
    transaction goes on and the outcome (audited) still says ``failed``.
    """
    savepoint = db.begin_nested()
    try:
        redaction = (
            find_redaction(
                db,
                workspace_id=identity.revision.workspace_id,
                revision_id=identity.revision.id,
                digest=identity.input_policy_digest,
            )
            if identity.input_policy_digest is not None
            else None
        )
        view = identity.header(
            state=StageState.FAILED,
            reason_code=reason,
            redaction_id=redaction.id if redaction is not None else None,
            segment_count=0,
            entity_count=0,
            now=now,
        )
        db.add(view)
        db.flush()
        savepoint.commit()
    except Exception as exc:
        savepoint.rollback()
        logger.error(
            "TEXT_VIEW_FAILURE_NOT_RECORDED revision_id=%s reason=%s error_type=%s",
            identity.revision.id,
            reason,
            type(exc).__name__,
        )
        return TextViewOutcome(None, StageState.FAILED, reason, identity.input_view, 0)
    return _outcome(view)


def derive_text_view(
    db: Session,
    *,
    revision: TranscriptRevision,
    segments: Sequence[TranscriptSegment],
    view_classes: Sequence[PiiClass],
    reference_at: datetime,
    settings: Settings,
    now: datetime,
    job_id: str | None = None,
    attempt: int | None = None,
    tz: ZoneInfo = TEHRAN,
) -> TextViewOutcome:
    """Create (or return) the derived view of ``revision`` in the caller's
    tenant-bound transaction.  ``view_classes`` is the workspace's enforced
    redaction policy (empty: the original is the default view).  Never raises
    for a derivation or storage fault: it is recorded as the view's state."""
    classes = tuple(sorted(set(view_classes), key=lambda c: c.value))
    input_digest = policy_digest(classes) if classes else None
    reference = as_utc(reference_at)
    identity = _Identity(
        revision=revision,
        classes=classes,
        input_view=VIEW_REDACTED if classes else VIEW_ORIGINAL,
        input_policy_digest=input_digest,
        reference_at=reference,
        tz=tz,
        digest=view_digest(
            language=revision.language,
            input_view=VIEW_REDACTED if classes else VIEW_ORIGINAL,
            input_policy_digest=input_digest,
            reference_at=reference,
            tz=tz,
        ),
        job_id=job_id,
        attempt=attempt,
    )
    existing = find_text_view(
        db, workspace_id=revision.workspace_id, revision_id=revision.id, digest=identity.digest
    )
    if existing is not None:
        return _outcome(existing)

    savepoint = db.begin_nested()
    try:
        sources, redaction_id = _sources(db, identity, segments, settings=settings, now=now)
        derived = derive(sources, language=revision.language, reference=reference, tz=tz)
        view = _persist(db, identity, derived, redaction_id=redaction_id, now=now)
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        existing = find_text_view(
            db,
            workspace_id=revision.workspace_id,
            revision_id=revision.id,
            digest=identity.digest,
        )
        if existing is not None:
            return _outcome(existing)  # a concurrent creator won
        reason = REASON_PERSIST_FAILED
    except (_SourceUnavailable, RedactionError):
        savepoint.rollback()
        reason = REASON_SOURCE_UNAVAILABLE
    except Exception as exc:
        savepoint.rollback()
        logger.error(
            "TEXT_VIEW_PERSIST_FAILED revision_id=%s error_type=%s",
            revision.id,
            type(exc).__name__,
        )
        reason = REASON_PERSIST_FAILED
    else:
        return _outcome(view)
    return _record_failure(db, identity, reason=reason, now=now)
