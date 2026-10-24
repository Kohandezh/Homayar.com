"""Subject-scope export (WP07-T07, ADR 0025).

Finds what one workspace holds about a data subject who is identified by
structured identifiers (e-mail, phone, national ID, bank card, IBAN) and
returns it as one JSON document.  Identifiers are canonicalised by the same
detector as redaction (ADR 0023), so ``+98 912 345 6789``, ``۰۹۱۲۳۴۵۶۷۸۹`` and
``٠٩١٢-٣٤٥-٦٧٨٩`` are one subject; matching compares canonical values of
detected spans, never substrings.

Scope, stated in the export itself: live conversations of the workspace (a
tombstoned conversation is already denied to every normal path, held or not),
every transcript revision, the summary, the action items and the entity values of
every stored taxonomy extraction (HOM-53, kept and withheld).  Not included:
audio, backups, audit metadata, and anything that names the subject without
one of the supported identifiers (names, addresses).  Segments are returned as
they are, so they may also contain other people's words: the owner reviews the
document before releasing it.  Nothing here logs or audits an identifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    ActionItem,
    AnalysisExtraction,
    Meeting,
    MeetingSpeaker,
    MeetingSummary,
)
from backend.privacy.text_pii import DETECTOR_VERSION, PiiClass, _detect_each, detect
from backend.services.transcript_service import list_revisions, segments_of

SUBJECT_SCAN_LIMIT = 500
SUBJECT_CLASSES = frozenset(
    {PiiClass.EMAIL, PiiClass.PHONE, PiiClass.IR_NATIONAL_ID, PiiClass.BANK_CARD, PiiClass.IBAN}
)
SUBJECT_NOTICE = (
    "Conversations of this workspace that contain the given identifiers (any spelling). "
    "Segments are shown in full and may contain other people's words; review before "
    "release. Not included: audio, backups, audit metadata, deleted conversations, and "
    "mentions without a supported identifier (names, addresses)."
)


class SubjectIdentifierError(ValueError):
    code = "invalid_subject_identifier"


@dataclass(frozen=True)
class SubjectIdentifier:
    pii_class: PiiClass
    value: str  # canonical form; never logged


def parse_subject_identifier(raw: str) -> SubjectIdentifier:
    """One supported identifier and nothing else, in any common spelling."""
    text = " ".join(raw.split())
    spans = [span for span in detect(text) if span.pii_class in SUBJECT_CLASSES]
    if len(spans) != 1 or spans[0].start != 0 or spans[0].end != len(text):
        raise SubjectIdentifierError(
            "Each identifier must be exactly one e-mail, phone number, national ID, "
            "bank card or IBAN"
        )
    return SubjectIdentifier(spans[0].pii_class, spans[0].value)


class _Matcher:
    def __init__(self, identifiers: list[SubjectIdentifier]) -> None:
        self._wanted = {(item.pii_class, item.value) for item in identifiers}
        self._classes = {item.pii_class for item in identifiers}
        self.segments = 0

    def classes(self, text: str | None) -> list[str]:
        if not text:
            return []
        # One class at a time (HOM-291): read together, a chance reading of one
        # class can cut a real value of another.  The work that does not depend
        # on the class is done once (HOM-302).
        found = {
            span.pii_class.value
            for spans in _detect_each(text, [{pii_class} for pii_class in self._classes])
            for span in spans
            if (span.pii_class, span.value) in self._wanted
        }
        return sorted(found)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _meeting_matches(db: Session, meeting: Meeting, matcher: _Matcher) -> dict[str, Any] | None:
    names = {
        speaker.speaker_key: speaker.display_name or speaker.speaker_key
        for speaker in db.scalars(
            select(MeetingSpeaker).where(
                MeetingSpeaker.workspace_id == meeting.workspace_id,
                MeetingSpeaker.meeting_id == meeting.id,
            )
        )
    }
    revisions: list[dict[str, Any]] = []
    for revision in list_revisions(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id):
        segments = []
        for segment in segments_of(db, revision):
            matched = matcher.classes(segment.text)
            if not matched:
                continue
            matcher.segments += 1
            segments.append(
                {
                    "segment_id": segment.id,
                    "sequence": segment.sequence,
                    "speaker": names.get(segment.speaker_key, segment.speaker_key),
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "text": segment.text,
                    "matched": matched,
                }
            )
        if segments:
            revisions.append(
                {
                    "revision_id": revision.id,
                    "revision_number": revision.revision_number,
                    "is_current": revision.is_current,
                    "source": revision.source.value,
                    "segments": segments,
                }
            )

    summary_items: list[dict[str, Any]] = []
    summary = db.scalar(
        select(MeetingSummary).where(
            MeetingSummary.workspace_id == meeting.workspace_id,
            MeetingSummary.meeting_id == meeting.id,
        )
    )
    if summary is not None:
        texts = [("overview", summary.overview)] + [
            ("key_point", str(point.get("description", "")))
            for point in summary.key_points or []
            if isinstance(point, dict)
        ]
        # A structured decision's owner (HOM-61) and due date (HOM-293) are free
        # text too.
        for point in summary.decisions or []:
            owner = point.get("owner") if isinstance(point, dict) else None
            if isinstance(owner, dict) and isinstance(owner.get("value"), str):
                texts.append(("decision_owner", owner["value"]))
            due_date = point.get("due_date") if isinstance(point, dict) else None
            if isinstance(due_date, dict) and isinstance(due_date.get("value"), str):
                texts.append(("decision_due_date", due_date["value"]))
        for kind, text in texts:
            matched = matcher.classes(text)
            if matched:
                summary_items.append({"kind": kind, "text": text, "matched": matched})

    actions: list[dict[str, Any]] = []
    for action in db.scalars(
        select(ActionItem)
        .where(ActionItem.workspace_id == meeting.workspace_id, ActionItem.meeting_id == meeting.id)
        .order_by(ActionItem.created_at.asc(), ActionItem.id.asc())
    ):
        matched = sorted(
            set(matcher.classes(action.description)) | set(matcher.classes(action.assignee_text))
        )
        if matched:
            actions.append(
                {
                    "action_id": action.id,
                    "description": action.description,
                    "assignee": action.assignee_text,
                    "matched": matched,
                }
            )

    extraction = _extraction_matches(db, meeting, matcher)
    if not (revisions or summary_items or actions or extraction):
        return None
    match: dict[str, Any] = {
        "meeting_id": meeting.id,
        "title": meeting.title,
        "created_at": _iso(meeting.created_at),
        "retention_expires_at": _iso(meeting.retention_expires_at),
        "revisions": revisions,
        "summary": summary_items,
        "actions": actions,
    }
    if extraction:
        match["extraction"] = extraction
    return match


def _extraction_matches(db: Session, meeting: Meeting, matcher: _Matcher) -> list[dict[str, Any]]:
    """Entity values the matcher hits, kept and withheld, from every stored run."""
    found: list[dict[str, Any]] = []
    for row in db.scalars(
        select(AnalysisExtraction)
        .where(
            AnalysisExtraction.workspace_id == meeting.workspace_id,
            AnalysisExtraction.meeting_id == meeting.id,
        )
        .order_by(AnalysisExtraction.created_at.asc(), AnalysisExtraction.id.asc())
    ):
        fields = row.result.get("fields") if isinstance(row.result, dict) else None
        entity = fields.get("entity") if isinstance(fields, dict) else None
        if not isinstance(entity, dict):
            continue
        for value in [*(entity.get("values") or []), *(entity.get("withheld") or [])]:
            text = value.get("text") if isinstance(value, dict) else None
            if isinstance(text, str) and matcher.classes(text):
                found.append(
                    {
                        "analysis_run_id": row.analysis_run_id,
                        "label": value.get("label"),
                        "text": text,
                    }
                )
    return found


def build_subject_export(
    db: Session,
    *,
    workspace_id: str,
    identifiers: list[SubjectIdentifier],
    meeting_clause,
    now: datetime,
    limit: int = SUBJECT_SCAN_LIMIT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The export document and its value-free audit summary."""
    matcher = _Matcher(identifiers)
    meetings = list(
        db.scalars(
            select(Meeting)
            .where(
                Meeting.workspace_id == workspace_id,
                Meeting.deleted_at.is_(None),
                meeting_clause,
            )
            .order_by(Meeting.created_at.desc(), Meeting.id.asc())
            .limit(limit + 1)
        )
    )
    truncated = len(meetings) > limit
    meetings = meetings[:limit]
    matches = [
        match
        for match in (_meeting_matches(db, meeting, matcher) for meeting in meetings)
        if match is not None
    ]
    classes = sorted({item.pii_class.value for item in identifiers})
    document = {
        "generated_at": now.isoformat(),
        "workspace_id": workspace_id,
        "subject": {
            "identifiers": [
                {"class": item.pii_class.value, "value": item.value} for item in identifiers
            ]
        },
        "scope": {
            "detector_version": DETECTOR_VERSION,
            "meetings_scanned": len(meetings),
            "truncated": truncated,
            "notice": SUBJECT_NOTICE,
        },
        "matches": matches,
    }
    audit = {
        "identifier_classes": ",".join(classes),
        "identifier_count": len(identifiers),
        "meetings_scanned": len(meetings),
        "meetings_matched": len(matches),
        "segments_matched": matcher.segments,
        "truncated": truncated,
    }
    return document, audit
