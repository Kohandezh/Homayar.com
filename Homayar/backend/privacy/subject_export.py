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

import itertools
import re
from bisect import bisect_left
from collections.abc import Callable, Iterable
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
from backend.privacy.text_pii import (
    DETECTOR_VERSION,
    PiiClass,
    PiiSpan,
    _detect_in,
    _Text,
    detect,
    valid_luhn,
    valid_national_id,
)
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


# --- A mobile that only a reading of several classes finds (HOM-298) ----------------
#
# The export reads each class on its own (HOM-291).  A reading of several classes
# finds some more values, but most of those are chance readings: a real identifier
# and a chance one can have the same written shape.  So a mobile is also found
# when the reading of its own class saw an identifier on those digits but read
# it as another value, its class read together with one other class gives it in a
# usual written form, and that other class, read on its own, explains the cut with a
# value in a usual written form too (the "re-cut" rule).  Example:
# ``6219 6997 6901 0086 0931 397 0789`` read for phones alone is one ``00`` number; the
# card, which the card reading finds too, takes ``0086`` and leaves the mobile
# ``0931 397 0789``.  Only mobiles are read this way.  A chance landline (``0``, an
# area digit 1 to 8, then 9 digits) is far more common in other numbers than a chance
# mobile (``09`` and one of six digits), and a real landline after an ID has the same
# written shape as random digits after an ID.  Cards, IDs and other numbers are not
# re-read either (a price list of four-digit numbers next to an ID has a card's
# shape).  None of this depends on the classes of the request, so a value matches the
# same way in every export.

_REREAD_CLASSES = frozenset({PiiClass.PHONE})  # mobiles only
_CUTTING_CLASSES = frozenset(
    {PiiClass.PHONE, PiiClass.IR_NATIONAL_ID, PiiClass.BANK_CARD, PiiClass.IBAN}
)
# One separator between the groups of a digit run (the ``_SEP`` set of text_pii; a
# test keeps them equal).
_SEPARATORS = frozenset("  -‐‑‒–")
# Usual written forms, as the sizes of the digit groups.
_CARD_FORMS = {(16,), (4, 4, 4, 4)}
_ID_FORMS = {(10,), (3, 6, 1), (3, 3, 3, 1)}
_MOBILE_FORMS = {
    11: {(11,), (4, 3, 4), (4, 7), (4, 3, 2, 2)},  # 0912 345 6789
    12: {(12,), (2, 3, 3, 4), (2, 10)},  # 98 912 345 6789
    14: {(14,), (4, 3, 3, 4), (4, 10)},  # 0098 912 345 6789
}
# A landline is never re-read, but one written in whole groups can still show that a
# mobile reading cuts into another identifier (``_written_identifiers``).
_LANDLINE_FORMS = {(11,), (3, 4, 4), (3, 8)}  # 021 2345 6789
# The classes read together with a mobile, most useful first.
_CUTTER_ORDER = {PiiClass.PHONE: (PiiClass.BANK_CARD, PiiClass.IR_NATIONAL_ID, PiiClass.IBAN)}


_DIGIT_RE = re.compile("[0-9]")
_NOT_DIGIT_RE = re.compile("[^0-9]")


def _is_digit(char: str) -> bool:
    return "0" <= char <= "9"


def _sizes(at: list[int]) -> tuple[int, ...]:
    """The sizes of the groups of digits at positions ``at``."""
    sizes = [1]
    for before, here in itertools.pairwise(at):
        if here == before + 1:
            sizes[-1] += 1
        else:
            sizes.append(1)
    return tuple(sizes)


def _is_mobile(value: str) -> bool:
    return len(value) == 11 and value[:2] == "09" and value[2] in "012349" and value.isdigit()


def _spellings(pii_class: PiiClass, value: str) -> list[str]:
    """The digits a value of the re-cut rule can be written with: only a mobile."""
    if pii_class is PiiClass.PHONE and _is_mobile(value):
        return [value, "98" + value[1:], "0098" + value[1:]]
    return []


def _occurrence(norm: str, span: PiiSpan) -> list[int] | None:
    """The positions of the digits the span's value is written with."""
    at = [i for i in range(span.start, span.end) if _is_digit(norm[i])]
    digits = "".join(norm[i] for i in at)
    spellings = [span.value]
    if span.pii_class is PiiClass.PHONE and span.value[:1] == "0" and span.value[1:2] != "0":
        # The longest spelling first: ``98 ...`` inside ``0098 ...`` is not how the
        # value is written.
        spellings = ["0098" + span.value[1:], "98" + span.value[1:], span.value]
    for written in spellings:
        k = digits.find(written)
        if k >= 0 and not any(
            longer.endswith(written) and digits[:k].endswith(longer[: -len(written)])
            for longer in spellings
            if len(longer) > len(written)
        ):
            return at[k : k + len(written)]
    return None


def _written(pii_class: PiiClass, value: str, at: list[int]) -> bool:
    """Whether a value written at ``at`` is in a usual written form of its class."""
    sizes = _sizes(at)
    if pii_class is PiiClass.BANK_CARD:
        return len(value) == 16 and sizes in _CARD_FORMS
    if pii_class is PiiClass.IR_NATIONAL_ID:
        return sizes in _ID_FORMS
    if pii_class is PiiClass.PHONE and _is_mobile(value):
        return sizes in _MOBILE_FORMS.get(len(at), set())
    return False  # a landline or another phone does not count as written


def _written_kind(digits: str, sizes: tuple[int, ...]) -> bool:
    if len(digits) == 16:
        return sizes in _CARD_FORMS and valid_luhn(digits)
    if len(digits) == 10:
        return sizes in _ID_FORMS and valid_national_id(digits)
    for prefix in ("0", "98", "0098"):
        rest = digits[len(prefix) :] if digits.startswith(prefix) else ""
        if len(rest) == 10 and rest[0] == "9" and rest[1] in "012349":
            return sizes in _MOBILE_FORMS[len(digits)]
    return (
        len(digits) == 11
        and digits[0] == "0"
        and digits[1] in "12345678"
        and sizes in _LANDLINE_FORMS
    )


def _written_identifiers(norm: str) -> list[tuple[int, int]]:
    """Every identifier of the text written in whole groups in a usual form (card,
    ID, mobile, landline), as (first digit, last digit + 1), whatever the request."""
    runs: list[list[tuple[int, int]]] = []
    i = 0
    while i < len(norm):
        if not _is_digit(norm[i]):
            i += 1
            continue
        j = i
        while j < len(norm) and _is_digit(norm[j]):
            j += 1
        joined = runs and runs[-1][-1][1] == i - 1 and norm[i - 1] in _SEPARATORS
        if joined:
            runs[-1].append((i, j))
        else:
            runs.append([(i, j)])
        i = j
    found = []
    for run in runs:
        for a in range(len(run)):
            digits = ""
            for b in range(a, min(len(run), a + 4)):
                digits += norm[run[b][0] : run[b][1]]
                if len(digits) > 16:
                    break
                sizes = tuple(end - start for start, end in run[a : b + 1])
                if _written_kind(digits, sizes):
                    found.append((run[a][0], run[b][1]))
    return found


class _Near:
    """Spans as ``(start, end, ...)`` tuples sorted by start, and the ones that share
    a character with a stretch: each query looks only near the stretch."""

    def __init__(self, spans: Iterable[tuple]) -> None:
        self.spans = sorted(spans)
        self.starts = [span[0] for span in self.spans]
        self.longest = max((span[1] - span[0] for span in self.spans), default=0)

    def overlapping(self, lo: int, hi: int) -> list[tuple]:
        first = bisect_left(self.starts, lo - self.longest + 1)
        return [span for span in self.spans[first : bisect_left(self.starts, hi)] if span[1] > lo]


class _Alone:
    """The reading of each class on its own, its values and its spans by place, built
    on first use for one call."""

    def __init__(self, read: Callable[[PiiClass], list[PiiSpan]]) -> None:
        self._read = read
        self._values: dict[PiiClass, set[str]] = {}
        self._near: dict[PiiClass, _Near] = {}

    def values(self, pii_class: PiiClass) -> set[str]:
        if pii_class not in self._values:
            self._values[pii_class] = {span.value for span in self._read(pii_class)}
        return self._values[pii_class]

    def covering(self, pii_class: PiiClass, here: list[int]) -> list[PiiSpan]:
        """The spans of the class that hold a digit at ``here``."""
        if pii_class not in self._near:
            spans = self._read(pii_class)
            self._near[pii_class] = _Near((s.start, s.end, k) for k, s in enumerate(spans))
        spans = self._read(pii_class)
        return [
            spans[k]
            for _, _, k in self._near[pii_class].overlapping(here[0], here[-1] + 1)
            if any(spans[k].start <= i < spans[k].end for i in here)
        ]


class _Pair:
    """A reading of two classes, with where each value is written and whether it is in
    a usual written form, worked out once for every span."""

    def __init__(self, norm: str, reading: list[PiiSpan]) -> None:
        self.norm, self.reading = norm, reading
        self.at = [_occurrence(norm, span) for span in reading]
        self.shaped = [
            a is not None and _written(span.pii_class, span.value, a)
            for span, a in zip(reading, self.at, strict=True)
        ]
        self.used = {(a[0], a[-1] + 1) for a in self.at if a is not None}
        self.near = _Near((span.start, span.end, j) for j, span in enumerate(reading))

    def recut(self, k: int, alone: _Alone, written: _Near) -> bool:
        """Whether span k is a value of the re-cut rule."""
        span, here = self.reading[k], self.at[k]
        if here is None or not _written(span.pii_class, span.value, here):
            return False
        lo, hi = here[0], here[-1] + 1
        digits = "".join(self.norm[i] for i in here)
        if span.pii_class is PiiClass.PHONE and len(digits) == 14 and valid_luhn(digits):
            return False  # a 0098 number that is also a card: read alone, it is a card
        # A written identifier that cuts into the value shows a chance reading, unless
        # the reading uses another written identifier that cuts into that one.
        for start, end in written.overlapping(lo, hi):
            if (start, end) != (lo, hi) and not any(
                (s, e) not in ((start, end), (lo, hi)) and (s, e) in self.used
                for s, e in written.overlapping(start, end)
            ):
                return False
        # The reading of the value's own class covers every digit of it ...
        cover = alone.covering(span.pii_class, here)
        if not all(any(s.start <= i < s.end for s in cover) for i in here):
            return False
        # ... and each other class that cuts that reading finds the same value alone, in
        # a usual written form.
        return all(
            self.shaped[j] and self.reading[j].value in alone.values(self.reading[j].pii_class)
            for s in cover
            for _, _, j in self.near.overlapping(s.start, s.end)
            if self.reading[j].pii_class is not span.pii_class
        )


def _recut(
    norm: str,
    reading: list[PiiSpan],
    k: int,
    alone: Callable[[PiiClass], list[PiiSpan]],
    written: list[tuple[int, int]],
) -> bool:
    """Whether span k of a reading of two classes is a value of the re-cut rule.
    ``alone(c)`` is the reading of class c on its own, ``written`` the
    ``_written_identifiers`` of the text."""
    return _Pair(norm, reading).recut(k, _Alone(alone), _Near(written))


def _stretches(norm: str, spans: list[PiiSpan]) -> list[list[int]]:
    """The positions of the digits that ``spans`` cover, one list per stretch of
    spans with no digit between them."""
    stretches: list[list[int]] = []
    last = None
    for span in sorted(spans, key=lambda s: s.start):
        at = [i for i in range(span.start, span.end) if _is_digit(norm[i])]
        if last is not None and not _DIGIT_RE.search(norm, last, span.start):
            stretches[-1] += at
        else:
            stretches.append(at)
        last = span.end
    return stretches


def _lone_overlap(written: _Near, lo: int, hi: int) -> bool:
    """Whether a written identifier cuts into ``[lo, hi)`` and no other written
    identifier cuts into it: no reading can then use a rival (see ``_Pair.recut``)."""
    return any(
        (start, end) != (lo, hi)
        and not any(
            (s, e) not in ((start, end), (lo, hi)) for s, e in written.overlapping(start, end)
        )
        for start, end in written.overlapping(lo, hi)
    )


def _may_recut(
    norm: str,
    pii_class: PiiClass,
    value: str,
    spans: list[PiiSpan],
    written: Callable[[], _Near],
) -> bool:
    """What the re-cut rule needs of the value itself, tested on the text alone
    before any other reading: one spelling of it lies in digits that the reading of
    its class covers (``spans``), in a usual written form, is not a 0098 number that
    is also a card, and no written identifier cuts into it alone."""
    if not spans:
        return False
    spellings = _spellings(pii_class, value)
    digits = _NOT_DIGIT_RE.sub("", norm)
    if not any(spelling in digits for spelling in spellings):
        return False
    for at in _stretches(norm, spans):
        covered = "".join(norm[i] for i in at)
        for spelling in spellings:
            k = covered.find(spelling)
            while k >= 0:
                here = at[k : k + len(spelling)]
                if (
                    _written(pii_class, value, here)
                    and not (len(spelling) == 14 and valid_luhn(spelling))
                    and not _lone_overlap(written(), here[0], here[-1] + 1)
                ):
                    return True
                k = covered.find(spelling, k + 1)
    return False


def _recut_values(
    norm: str,
    wanted: set[tuple[PiiClass, str]],
    read: Callable[[frozenset[PiiClass]], list[PiiSpan]],
    matches: list[PiiSpan],
) -> set[tuple[PiiClass, str]]:
    """The wanted (class, value) pairs of the re-cut rule.  ``read(classes)`` is
    detect of the text for those classes, ``norm`` the text with its digits folded
    and ``matches`` its regex readings of every class."""
    found: set[tuple[PiiClass, str]] = set()
    reread = {(c, value) for c, value in wanted if c in _REREAD_CLASSES and _spellings(c, value)}
    if not reread:
        return found
    # Built on first use, for this call only.
    built: dict[str, _Near] = {}

    def written() -> _Near:
        if "written" not in built:
            built["written"] = _Near(_written_identifiers(norm))
        return built["written"]

    candidates = {
        (c, value)
        for c, value in reread
        if _may_recut(norm, c, value, read(frozenset({c})), written)
    }
    # Phones read together with each class that can share a mobile's digits, one at a
    # time (on the HOM-298 test corpora no mobile needed an e-mail reading).  A value is
    # found when any pair gives it, so the order only saves work: first the classes
    # with a regex reading on the digits the phone reading covers.
    if not candidates:
        return found
    alone = _Alone(lambda c: read(frozenset({c})))
    by_place = _Near((m.start, m.end, m.pii_class.value) for m in matches)
    for pii_class in sorted({c for c, _ in candidates}, key=lambda c: c.value):
        near = {
            PiiClass(name)
            for s in read(frozenset({pii_class}))
            for _, _, name in by_place.overlapping(s.start, s.end)
        }
        for other in sorted(_CUTTER_ORDER[pii_class], key=lambda c: c not in near):
            if {key for key in candidates if key[0] is pii_class} <= found:
                break
            pair = _Pair(norm, read(frozenset({pii_class, other})))
            for k, span in enumerate(pair.reading):
                key = (span.pii_class, span.value)
                if key in candidates and key not in found and pair.recut(k, alone, written()):
                    found.add(key)
    return found


class _Matcher:
    def __init__(self, identifiers: list[SubjectIdentifier]) -> None:
        self._wanted = {(item.pii_class, item.value) for item in identifiers}
        self._classes = sorted({item.pii_class for item in identifiers}, key=lambda c: c.value)
        self.segments = 0

    def classes(self, text: str | None) -> list[str]:
        if not text:
            return []
        # One class at a time (HOM-291): read together, a chance reading of one
        # class can cut a real value of another.  The work that does not depend
        # on the class is done once (HOM-302), and each class set is read once,
        # when first needed.  Both live only as long as this call.
        shared = _Text(text)
        readings: dict[frozenset[PiiClass], list[PiiSpan]] = {}

        def read(classes: frozenset[PiiClass]) -> list[PiiSpan]:
            if classes not in readings:
                readings[classes] = _detect_in(shared, classes)
            return readings[classes]

        found = {
            (span.pii_class, span.value)
            for pii_class in self._classes
            for span in read(frozenset({pii_class}))
            if (span.pii_class, span.value) in self._wanted
        }
        found |= _recut_values(shared.norm, self._wanted - found, read, shared.found)  # HOM-298
        return sorted({pii_class.value for pii_class, _ in found})


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
