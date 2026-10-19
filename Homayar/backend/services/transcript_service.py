"""Immutable transcript revisions and analysis-run provenance (WP04-T02, ADR 0014).

A meeting's transcript is the set of its ``TranscriptRevision`` rows.  Each
processing outcome and each human correction *appends* a revision; segments
of an existing revision are never edited or deleted (the ORM guard in
``saas_models`` refuses it).  Exactly one revision is current; the pointer
moves under the row lock of the previous current revision so two concurrent
corrections of the same base cannot both win.  ``AnalysisRun`` rows record
which revision an analysis read and with which provider, model, prompt and
schema, so a summary or AI action item can always be traced to its inputs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    AnalysisRun,
    Meeting,
    MeetingSpeaker,
    StageState,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptSource,
)
from backend.providers.schemas import UNKNOWN_SPEAKER_LABEL

REASON_PROCESSING = "processing"
REASON_REPROCESSING = "reprocessing"
REASON_HUMAN_CORRECTION = "human_correction"

MAX_SEGMENT_TEXT_LENGTH = 8000
MAX_CORRECTION_EDITS = 500


class TranscriptError(ValueError):
    """A refused revision operation with an API error code."""

    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.status_code = status_code


@dataclass(frozen=True)
class SegmentDraft:
    sequence: int
    speaker_key: str
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None
    language: str | None = None


@dataclass(frozen=True)
class EngineProvenance:
    """What produced an ASR revision (never a credential or a raw artifact)."""

    job_id: str | None = None
    attempt: int | None = None
    checkpoint_id: str | None = None
    config_hash: str | None = None
    asr_provider: str | None = None
    asr_model: str | None = None
    diarization_provider: str | None = None
    diarization_status: str | None = None
    is_mock: bool = False


@dataclass(frozen=True)
class SegmentEdit:
    segment_id: str
    text: str | None = None
    speaker_key: str | None = None


# --- reads --------------------------------------------------------------------


def current_revision(
    db: Session, *, workspace_id: str, meeting_id: str, for_update: bool = False
) -> TranscriptRevision | None:
    query = select(TranscriptRevision).where(
        TranscriptRevision.workspace_id == workspace_id,
        TranscriptRevision.meeting_id == meeting_id,
        TranscriptRevision.is_current.is_(True),
    )
    if for_update:
        query = query.with_for_update()
    return db.scalar(query)


def revision_by_id(
    db: Session, *, workspace_id: str, meeting_id: str, revision_id: str
) -> TranscriptRevision | None:
    return db.scalar(
        select(TranscriptRevision).where(
            TranscriptRevision.id == revision_id,
            TranscriptRevision.workspace_id == workspace_id,
            TranscriptRevision.meeting_id == meeting_id,
        )
    )


def list_revisions(db: Session, *, workspace_id: str, meeting_id: str) -> list[TranscriptRevision]:
    return list(
        db.scalars(
            select(TranscriptRevision)
            .where(
                TranscriptRevision.workspace_id == workspace_id,
                TranscriptRevision.meeting_id == meeting_id,
            )
            .order_by(TranscriptRevision.revision_number.desc())
        )
    )


def segments_of(db: Session, revision: TranscriptRevision) -> list[TranscriptSegment]:
    return list(
        db.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.revision_id == revision.id)
            .order_by(TranscriptSegment.sequence.asc())
        )
    )


def current_segments_clause(workspace_id: str):
    """Filter for ``TranscriptSegment`` rows that belong to a *current* revision.

    Search and lookups must never surface superseded text as if it were the
    transcript; older revisions are reachable only by explicit revision id.
    """
    return TranscriptSegment.revision_id.in_(
        select(TranscriptRevision.id).where(
            TranscriptRevision.workspace_id == workspace_id,
            TranscriptRevision.is_current.is_(True),
        )
    )


def current_analysis_run(db: Session, *, workspace_id: str, meeting_id: str) -> AnalysisRun | None:
    return db.scalar(
        select(AnalysisRun).where(
            AnalysisRun.workspace_id == workspace_id,
            AnalysisRun.meeting_id == meeting_id,
            AnalysisRun.is_current.is_(True),
        )
    )


# --- writes -------------------------------------------------------------------


def _next_revision_number(db: Session, *, meeting_id: str) -> int:
    highest = db.scalar(
        select(func.max(TranscriptRevision.revision_number)).where(
            TranscriptRevision.meeting_id == meeting_id
        )
    )
    return int(highest or 0) + 1


def _supersede_current_revision(
    db: Session, *, workspace_id: str, meeting_id: str, now: datetime
) -> TranscriptRevision | None:
    previous = current_revision(
        db, workspace_id=workspace_id, meeting_id=meeting_id, for_update=True
    )
    if previous is not None:
        previous.is_current = False
        previous.superseded_at = now
        db.flush()
    return previous


def create_revision(
    db: Session,
    *,
    meeting: Meeting,
    source: TranscriptSource,
    reason: str,
    language: str,
    drafts: Sequence[SegmentDraft],
    now: datetime,
    note: str | None = None,
    created_by_user_id: str | None = None,
    parent: TranscriptRevision | None = None,
    provenance: EngineProvenance | None = None,
    is_mock: bool | None = None,
) -> tuple[TranscriptRevision, list[TranscriptSegment]]:
    """Append a new current revision with its segments; never touches older rows.

    The previous current revision (if any) is locked and superseded in the same
    transaction, so the partial unique index on the current pointer holds and
    a concurrent writer of the same meeting serialises behind this one.
    """
    engine = provenance or EngineProvenance()
    _supersede_current_revision(
        db, workspace_id=meeting.workspace_id, meeting_id=meeting.id, now=now
    )
    revision = TranscriptRevision(
        workspace_id=meeting.workspace_id,
        meeting_id=meeting.id,
        revision_number=_next_revision_number(db, meeting_id=meeting.id),
        parent_revision_id=parent.id if parent is not None else None,
        source=source,
        reason=reason[:40],
        note=(note or None) and note[:500],
        language=language[:12],
        segment_count=len(drafts),
        created_at=now,
        created_by_user_id=created_by_user_id,
        job_id=engine.job_id,
        attempt=engine.attempt,
        checkpoint_id=engine.checkpoint_id,
        config_hash=engine.config_hash,
        asr_provider=engine.asr_provider,
        asr_model=engine.asr_model,
        diarization_provider=engine.diarization_provider,
        diarization_status=engine.diarization_status,
        is_mock=engine.is_mock if is_mock is None else is_mock,
        is_current=True,
    )
    db.add(revision)
    db.flush()
    segments = [
        TranscriptSegment(
            workspace_id=meeting.workspace_id,
            meeting_id=meeting.id,
            revision_id=revision.id,
            sequence=draft.sequence,
            speaker_key=draft.speaker_key,
            start_ms=draft.start_ms,
            end_ms=draft.end_ms,
            text=draft.text,
            confidence=draft.confidence,
            language=draft.language,
            created_at=now,
        )
        for draft in drafts
    ]
    db.add_all(segments)
    db.flush()
    return revision, segments


def ensure_speakers(
    db: Session, *, workspace_id: str, meeting_id: str, labels: Sequence[str]
) -> None:
    """Add speaker rows for new labels; keep existing rows (and their names).

    Reprocessing must not discard a display name a person assigned earlier.
    """
    existing = set(
        db.scalars(
            select(MeetingSpeaker.speaker_key).where(
                MeetingSpeaker.workspace_id == workspace_id,
                MeetingSpeaker.meeting_id == meeting_id,
            )
        )
    )
    for label in sorted(set(labels) - existing):
        db.add(
            MeetingSpeaker(
                workspace_id=workspace_id,
                meeting_id=meeting_id,
                speaker_key=label,
                identity_type="unknown",
            )
        )
    db.flush()


def record_analysis_run(
    db: Session,
    *,
    meeting: Meeting,
    revision: TranscriptRevision,
    state: StageState,
    now: datetime,
    reason_code: str | None = None,
    job_id: str | None = None,
    attempt: int | None = None,
    checkpoint_id: str | None = None,
    config_hash: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    identity_params: Mapping[str, object] | None = None,
    schema_version: int = 1,
    is_mock: bool = False,
    reference_count: int = 0,
    rejected_reference_count: int = 0,
    input_view: str = "original",
    input_policy_digest: str | None = None,
    manifest_digest: str | None = None,
) -> AnalysisRun:
    """Append the current analysis run; the previous one is superseded.

    Prompt provenance comes from the provider's declared identity parameters
    (``prompt_version``, ``prompt_sha256``, ``output_schema_sha256``) when the
    adapter declares them; adapters that do not leave them empty rather than
    inventing a version.
    """
    previous = current_analysis_run(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    if previous is not None:
        previous.is_current = False
        previous.superseded_at = now
        db.flush()
    params = dict(identity_params or {})

    def _text(key: str, limit: int) -> str | None:
        value = params.get(key)
        return None if value is None else str(value)[:limit]

    run = AnalysisRun(
        workspace_id=meeting.workspace_id,
        meeting_id=meeting.id,
        transcript_revision_id=revision.id,
        job_id=job_id,
        attempt=attempt,
        checkpoint_id=checkpoint_id,
        stage="intelligence",
        state=state,
        reason_code=(reason_code or None) and reason_code[:80],
        provider=provider,
        model=model,
        prompt_version=_text("prompt_version", 40),
        prompt_sha256=_text("prompt_sha256", 64),
        output_schema_sha256=_text("output_schema_sha256", 64),
        config_hash=config_hash,
        schema_version=schema_version,
        is_mock=is_mock,
        reference_count=reference_count,
        rejected_reference_count=rejected_reference_count,
        input_view=input_view,
        input_policy_digest=input_policy_digest,
        manifest_digest=manifest_digest,
        is_current=True,
        created_at=now,
    )
    db.add(run)
    db.flush()
    return run


def correct_revision(
    db: Session,
    *,
    meeting: Meeting,
    base_revision_id: str,
    edits: Sequence[SegmentEdit],
    actor_user_id: str,
    now: datetime,
    note: str | None = None,
) -> tuple[TranscriptRevision, list[TranscriptSegment], int]:
    """Create a corrected revision from the *current* revision.

    Edits change the text and/or speaker of existing segments; timings, order
    and segment count are preserved so audio offsets stay valid.  The base must
    be the current revision (``409 revision_stale`` otherwise), every edit must
    name a segment of that base and a speaker known to the meeting, and at
    least one edit must change something.  ``note`` is the person's reason for
    the correction and is required (``422 reason_required`` when blank).
    Returns the new revision, its segments and the number of segments that
    differ from the base.
    """
    note = " ".join((note or "").split())
    if not note:
        raise TranscriptError("reason_required", "A reason for the correction is required")
    if not edits:
        raise TranscriptError("no_changes", "No transcript edits were provided")
    if len(edits) > MAX_CORRECTION_EDITS:
        raise TranscriptError("too_many_edits", "Too many transcript edits in one correction")
    base = current_revision(
        db, workspace_id=meeting.workspace_id, meeting_id=meeting.id, for_update=True
    )
    if base is None or base.id != base_revision_id:
        raise TranscriptError(
            "revision_stale",
            "The transcript changed since it was loaded; reload and retry",
            status_code=409,
        )
    originals = segments_of(db, base)
    by_id = {segment.id: segment for segment in originals}
    known_speakers = set(
        db.scalars(
            select(MeetingSpeaker.speaker_key).where(
                MeetingSpeaker.workspace_id == meeting.workspace_id,
                MeetingSpeaker.meeting_id == meeting.id,
            )
        )
    ) | {UNKNOWN_SPEAKER_LABEL}

    changes: dict[str, SegmentEdit] = {}
    for edit in edits:
        if edit.segment_id in changes:
            raise TranscriptError("duplicate_edit", "A segment was edited more than once")
        original = by_id.get(edit.segment_id)
        if original is None:
            raise TranscriptError(
                "invalid_segment", "A segment does not belong to the base revision"
            )
        text = None if edit.text is None else " ".join(edit.text.split())
        if text is not None and (not text or len(text) > MAX_SEGMENT_TEXT_LENGTH):
            raise TranscriptError("invalid_text", "Segment text is empty or too long")
        speaker = edit.speaker_key
        if speaker is not None and speaker not in known_speakers:
            raise TranscriptError("invalid_speaker", "Speaker is not known for this meeting")
        if (text is None or text == original.text) and (
            speaker is None or speaker == original.speaker_key
        ):
            continue
        changes[edit.segment_id] = SegmentEdit(edit.segment_id, text=text, speaker_key=speaker)
    if not changes:
        raise TranscriptError("no_changes", "The edits do not change the transcript")

    drafts: list[SegmentDraft] = []
    for original in originals:
        change = changes.get(original.id)
        new_text = change.text if change is not None else None
        new_speaker = change.speaker_key if change is not None else None
        drafts.append(
            SegmentDraft(
                sequence=original.sequence,
                speaker_key=new_speaker if new_speaker is not None else original.speaker_key,
                start_ms=original.start_ms,
                end_ms=original.end_ms,
                text=new_text if new_text is not None else original.text,
                # A person's wording carries no engine confidence.
                confidence=None if new_text is not None else original.confidence,
                language=original.language,
            )
        )
    revision, segments = create_revision(
        db,
        meeting=meeting,
        source=TranscriptSource.CORRECTION,
        reason=REASON_HUMAN_CORRECTION,
        language=base.language,
        drafts=drafts,
        now=now,
        note=note,
        created_by_user_id=actor_user_id,
        parent=base,
        is_mock=base.is_mock,
    )
    return revision, segments, len(changes)
