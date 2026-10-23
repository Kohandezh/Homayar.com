"""Counting and deduplication of trend issues per conversation (HOM-79, D2).

Pure functions: no database, no network, no clock, no file I/O.  They never change
their inputs.  One count means one conversation (one meeting).  ``judge_conversation``
reads the stored extraction of the meeting's current analysis run and gives every
active issue exactly one state; ``tally`` adds judged conversations up, counting
distinct conversations for every issue and every family.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from backend.issue_trends.definition import (
    REFERENCE_DIMENSIONS,
    IssueDefinition,
    SourceState,
    TrendDefinition,
)

TRENDS_VERSION = "issue-trends/1"

# ``StageState`` values (``database/saas_models.py``).  The column stores the member
# name (``COMPLETED``); HOM-80 passes the value (``completed``).  A test holds them equal.
STAGE_STATES = (
    "queued",
    "running",
    "completed",
    "partial",
    "failed",
    "unavailable",
    "insufficient_evidence",
    "skipped",
)
# A current analysis run in one of these states analysed nothing (the report's rule,
# ``services/analysis_extraction.py`` ``_NO_ANALYSIS_STATES``).
NO_ANALYSIS_STATES = frozenset({"skipped", "unavailable", "failed"})


class IssueState(str, Enum):
    COUNTED = "counted"  # every reference holds, every value auto-accepted
    PENDING_REVIEW = "pending_review"  # it holds, but a value needs a person (HOM-18)
    ABSENT = "absent"  # assessable, and the issue is not there
    MISSING = "missing"  # not assessable: see the bucket


class Exclusion(str, Enum):
    """A conversation left out of the population: in no numerator, no denominator and
    no missing-data bucket, but counted apart so it stays visible."""

    DUPLICATE_MEDIA = "duplicate_media"  # a member of a media group, not its counted copy


class MissingBucket(str, Enum):
    """Why a conversation, a dimension or an issue is not assessable.  Members are in
    precedence order: a conversation gets the first that applies, and a missing
    family takes the first of its issues' buckets in this order.  A missing issue
    takes the bucket of its first missing reference in ``match`` order."""

    # the whole conversation
    ANALYSIS_UNAVAILABLE = "analysis_unavailable"  # no current run, or it analysed nothing
    ANALYSIS_STALE = "analysis_stale"  # the run read an older transcript revision
    MOCK_ANALYSIS = "mock_analysis"  # the current transcript or the current run is a mock
    EXTRACTION_UNAVAILABLE = "extraction_unavailable"  # the current run stored no row
    EXTRACTION_UNREADABLE = "extraction_unreadable"  # the row is not a known stored shape
    VERSION_MISMATCH = "version_mismatch"  # judged with another taxonomy, policy or validator
    EXTRACTION_INVALID = "extraction_invalid"  # the row has valid = false
    # one dimension (field) of a valid row
    ABSTAINED_INSUFFICIENT_EVIDENCE = "abstained_insufficient_evidence"  # model abstained
    ABSTAINED_CONTRADICTORY = "abstained_contradictory"  # model, contradictory
    ABSTAINED_CONTRADICTORY_VALUES = "abstained_contradictory_values"  # validator found it
    ABSTAINED_LOW_CONFIDENCE = "abstained_low_confidence"  # policy withheld every value
    # one issue of an assessed dimension
    VALUE_WITHHELD = "value_withheld"  # the referenced label is only among the withheld


CONVERSATION_BUCKETS = (
    MissingBucket.ANALYSIS_UNAVAILABLE,
    MissingBucket.ANALYSIS_STALE,
    MissingBucket.MOCK_ANALYSIS,
    MissingBucket.EXTRACTION_UNAVAILABLE,
    MissingBucket.EXTRACTION_UNREADABLE,
    MissingBucket.VERSION_MISMATCH,
    MissingBucket.EXTRACTION_INVALID,
)


class UnreadableReason(str, Enum):
    """Why a stored row is ``extraction_unreadable``.  The walk and its order are in the
    README ("The stored shape"); the first defect gives the one reason."""

    VERSION_FIELDS = "shape_version_fields"  # not an object, or a version field unreadable
    MISSING_KEY = "shape_missing_key"
    UNKNOWN_KEY = "shape_unknown_key"
    BAD_TYPE = "shape_bad_type"
    BAD_WORD = "shape_bad_word"  # a string outside the allowed enum words
    INCONSISTENT = "shape_inconsistent"  # a pair of values the policy never writes


# The stored shape of ``PolicyResult.to_json()`` of ``extraction-policy/1``.  Each
# object has exactly these keys: a missing one and an extra one are both unreadable.
# Read first, before the shape, to decide ``version_mismatch``; name -> JSON type.
VERSION_FIELDS = (
    ("taxonomy_id", "string"),
    ("taxonomy_version", "integer"),
    ("taxonomy_digest", "string"),
    ("policy_version", "string"),
    ("policy_digest", "string"),
    ("validator_version", "string"),
)
RESULT_KEYS = frozenset(
    {name for name, _type in VERSION_FIELDS} | {"valid", "needs_review", "issues", "fields"}
)
FIELD_NAMES = ("intent", "topic", "entity", "outcome")  # exactly these keys of "fields"
FIELD_KEYS = frozenset(
    {"verdict", "status", "abstain_reason", "review_required", "issues", "values", "withheld"}
)
ISSUE_KEYS = frozenset({"code", "path"})
VALUE_KEYS = frozenset(
    {
        "label",
        "basis",
        "source_turn_indexes",
        "confidence",
        "critical",
        "text",
        "disposition",
        "review_reason",
    }
)
WITHHELD_KEYS = (VALUE_KEYS - {"disposition", "review_reason"}) | {"reason"}
# The allowed words of each enum key.
VERDICTS = frozenset({"accepted", "abstained", "rejected"})
FIELD_STATUSES = frozenset({"stated", "not_stated", "insufficient_evidence", "contradictory"})
ABSTAIN_REASONS = frozenset({"model", "contradictory", "low_confidence"})
BASES = frozenset({"explicit", "inferred"})
DISPOSITIONS = frozenset({"auto_accepted", "review_required"})
REVIEW_REASONS = frozenset({"critical_label"})
WITHHELD_REASONS = frozenset({"below_threshold", "missing_confidence"})


DIMENSION_BUCKETS = (
    MissingBucket.ABSTAINED_INSUFFICIENT_EVIDENCE,
    MissingBucket.ABSTAINED_CONTRADICTORY,
    MissingBucket.ABSTAINED_CONTRADICTORY_VALUES,
    MissingBucket.ABSTAINED_LOW_CONFIDENCE,
)


@dataclass(frozen=True)
class MediaGroupMember:
    """One ingestion of a media group (README "Duplicate media")."""

    ingestion_id: str
    received_at: datetime  # IngestionRecord.received_at, with a timezone
    # The coverage ``transcribed`` state now: accepted row, live meeting, status
    # completed or partial, a current revision with at least one segment.
    transcribed: bool


def counted_copy_id(members: Iterable[MediaGroupMember]) -> str | None:
    """The counted copy of one media group: the earliest transcribed member by
    ``received_at``, then by ``ingestion_id`` (string order) as a tie-break.  ``None``
    when no member is transcribed: the group counts nothing.

    The reference rule for HOM-80's SQL.  Raises ``ValueError`` for an empty group, an
    ingestion id seen twice, or a ``received_at`` without a timezone.
    """
    group = list(members)
    if not group:
        raise ValueError("a media group has at least one member")
    ids = [member.ingestion_id for member in group]
    if len(set(ids)) != len(ids):
        raise ValueError("an ingestion id appears twice in one media group")
    for member in group:
        at = member.received_at
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("received_at needs a timezone")
    transcribed = [member for member in group if member.transcribed]
    if not transcribed:
        return None
    return min(
        transcribed, key=lambda member: (member.received_at, member.ingestion_id)
    ).ingestion_id


@dataclass(frozen=True)
class ConversationFacts:
    """What the judge reads about one conversation of the population.

    HOM-80 loads it with set-based queries: the meeting's ingestion row (and the row
    it links with ``duplicate_of_id``), its current transcript revision, its current
    analysis run (``AnalysisRun.is_current``) and the ``analysis_extractions`` row of
    that run, if any.  Never the row of another run.
    """

    meeting_id: str
    ingestion_id: str  # the meeting's IngestionRecord.id
    # The root of the meeting's duplicate_of_id chain (README "Duplicate media"): the
    # first row of the chain that has no duplicate_of_id.  Its own id when it has none.
    media_group_id: str
    # This meeting's ingestion is the counted copy of its media group
    # (``counted_copy_id`` over the whole group, whatever the window, the filters and
    # the caller's scope).
    is_counted_copy: bool
    current_revision_id: str  # the meeting's current TranscriptRevision.id
    transcript_is_mock: bool  # TranscriptRevision.is_mock of that revision
    analysis_run_id: str | None  # the current run; None when there is none
    analysis_state: str | None  # its StageState value (STAGE_STATES); None exactly when no run
    analysis_revision_id: str | None  # AnalysisRun.transcript_revision_id; None when no run
    analysis_is_mock: bool  # AnalysisRun.is_mock; False when no run
    extraction_run_id: str | None  # analysis_run_id of the row; None exactly when no row
    # The row's stored ``result`` as decoded JSON: any JSON value, never checked here.
    # None when there is no row (or when the stored JSON is null).
    extraction: object | None


@dataclass(frozen=True)
class DimensionJudgment:
    dimension: str
    bucket: MissingBucket | None  # None: the field is assessed (stated or not_stated)


@dataclass(frozen=True)
class IssueJudgment:
    issue_id: str
    state: IssueState
    bucket: MissingBucket | None  # set exactly when state is missing
    # True only when state is absent and every reference that does not hold is on a
    # single-valued dimension whose kept value is review_required (README).
    absent_pending_review: bool


@dataclass(frozen=True)
class FamilyJudgment:
    family_id: str
    state: IssueState
    bucket: MissingBucket | None  # set exactly when state is missing
    # True only when state is absent and one of its issues is absent_pending_review.
    absent_pending_review: bool


@dataclass(frozen=True)
class ConversationJudgment:
    meeting_id: str
    media_group_id: str  # copied from the facts
    trends_version: str
    definition_id: str
    definition_version: int
    definition_digest: str
    exclusion: Exclusion | None
    bucket: MissingBucket | None  # a conversation bucket, or None
    unreadable: UnreadableReason | None  # set exactly when bucket is extraction_unreadable
    dimensions: tuple[DimensionJudgment, ...]  # intent, topic, outcome; () when excluded
    issues: tuple[IssueJudgment, ...]  # every active issue of an available family, file order
    families: tuple[FamilyJudgment, ...]  # every available family, file order

    def issue(self, issue_id: str) -> IssueJudgment:
        for issue in self.issues:
            if issue.issue_id == issue_id:
                return issue
        raise KeyError(issue_id)

    def family(self, family_id: str) -> FamilyJudgment:
        for family in self.families:
            if family.family_id == family_id:
                return family
        raise KeyError(family_id)

    def to_json(self) -> dict[str, object]:
        """Plain JSON, enum values as strings (README "Per conversation")."""

        def word(value: Enum | None) -> object:
            return value.value if value is not None else None

        return {
            "meeting_id": self.meeting_id,
            "media_group_id": self.media_group_id,
            "trends_version": self.trends_version,
            "definition": {
                "id": self.definition_id,
                "version": self.definition_version,
                "digest": self.definition_digest,
            },
            "exclusion": word(self.exclusion),
            "bucket": word(self.bucket),
            "unreadable": word(self.unreadable),
            "dimensions": {d.dimension: {"bucket": word(d.bucket)} for d in self.dimensions},
            "issues": {
                i.issue_id: {
                    "state": i.state.value,
                    "bucket": word(i.bucket),
                    "absent_pending_review": i.absent_pending_review,
                }
                for i in self.issues
            },
            "families": {
                f.family_id: {
                    "state": f.state.value,
                    "bucket": word(f.bucket),
                    "absent_pending_review": f.absent_pending_review,
                }
                for f in self.families
            },
        }


# --- the stored shape -----------------------------------------------------------------------

_SINGLE_VALUED = frozenset({"intent", "outcome"})
_ABSTAIN_STATUSES = frozenset({"insufficient_evidence", "contradictory"})


class _Unreadable(Exception):
    def __init__(self, reason: UnreadableReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _version_fields_readable(result: object) -> bool:
    if not isinstance(result, Mapping):
        return False
    for name, json_type in VERSION_FIELDS:
        if name not in result:
            return False
        value = result[name]
        if json_type == "integer" and not _is_int(value):
            return False
        if json_type == "string" and not isinstance(value, str):
            return False
    return True


# A test on one key's value of a stored object.
_Test = Callable[[object], bool]


def _string(value: object) -> bool:
    return isinstance(value, str)


def _optional_string(value: object) -> bool:
    return value is None or isinstance(value, str)


def _bool(value: object) -> bool:
    return isinstance(value, bool)


def _list(value: object) -> bool:
    return isinstance(value, list)


def _object(value: object) -> bool:
    return isinstance(value, Mapping)


def _turn_indexes(value: object) -> bool:
    return isinstance(value, list) and all(_is_int(item) for item in value)


def _optional_number(value: object) -> bool:
    return value is None or _is_number(value)


def _word(words: frozenset[str], *, optional: bool = False) -> _Test:
    def check(value: object) -> bool:
        return (optional and value is None) or value in words

    return check


def _walk_object(
    item: object,
    keys: frozenset[str],
    types: Mapping[str, _Test],
    words: Mapping[str, _Test] | None = None,
) -> Any:
    """One object of the walk: missing key, unknown key, bad type, bad word, in order."""
    if not isinstance(item, Mapping):
        raise _Unreadable(UnreadableReason.BAD_TYPE)
    if any(key not in item for key in keys):
        raise _Unreadable(UnreadableReason.MISSING_KEY)
    if any(key not in keys for key in item):
        raise _Unreadable(UnreadableReason.UNKNOWN_KEY)
    for key, test in types.items():
        if not test(item[key]):
            raise _Unreadable(UnreadableReason.BAD_TYPE)
    for key, test in (words or {}).items():
        if not test(item[key]):
            raise _Unreadable(UnreadableReason.BAD_WORD)
    return item


_ISSUE_TYPES = {"code": _string, "path": _string}
_RESULT_TYPES = {"valid": _bool, "needs_review": _bool, "issues": _list, "fields": _object}
_FIELD_TYPES = {
    "verdict": _string,
    "status": _optional_string,
    "abstain_reason": _optional_string,
    "review_required": _bool,
    "issues": _list,
    "values": _list,
    "withheld": _list,
}
_FIELD_WORDS = {
    "verdict": _word(VERDICTS),
    "status": _word(FIELD_STATUSES, optional=True),
    "abstain_reason": _word(ABSTAIN_REASONS, optional=True),
}
_VALUE_TYPES = {
    "label": _string,
    "basis": _string,
    "source_turn_indexes": _turn_indexes,
    "confidence": _optional_number,
    "critical": _bool,
    "text": _optional_string,
}
_KEPT_TYPES = {**_VALUE_TYPES, "disposition": _string, "review_reason": _optional_string}
_KEPT_WORDS = {
    "basis": _word(BASES),
    "disposition": _word(DISPOSITIONS),
    "review_reason": _word(REVIEW_REASONS, optional=True),
}
_WITHHELD_TYPES = {**_VALUE_TYPES, "reason": _string}
_WITHHELD_WORDS = {"basis": _word(BASES), "reason": _word(WITHHELD_REASONS)}


def _walk(result: Any) -> None:
    """The fixed walk (README "The stored shape", amendment A3)."""
    _walk_object(result, RESULT_KEYS, _RESULT_TYPES)
    for issue in result["issues"]:
        _walk_object(issue, ISSUE_KEYS, _ISSUE_TYPES)
    fields = result["fields"]
    _walk_object(fields, frozenset(FIELD_NAMES), dict.fromkeys(FIELD_NAMES, _object))
    for name in FIELD_NAMES:
        field = _walk_object(fields[name], FIELD_KEYS, _FIELD_TYPES, _FIELD_WORDS)
        for issue in field["issues"]:
            _walk_object(issue, ISSUE_KEYS, _ISSUE_TYPES)
        for value in field["values"]:
            _walk_object(value, VALUE_KEYS, _KEPT_TYPES, _KEPT_WORDS)
        for value in field["withheld"]:
            _walk_object(value, WITHHELD_KEYS, _WITHHELD_TYPES, _WITHHELD_WORDS)


def _field_consistent(name: str, field: Any) -> bool:
    verdict, status, reason = field["verdict"], field["status"], field["abstain_reason"]
    values, withheld = field["values"], field["withheld"]
    if verdict == "rejected":
        if status is not None or reason is not None or values or withheld:
            return False
    elif verdict == "abstained":
        if status not in _ABSTAIN_STATUSES or reason is None or values:
            return False
        if reason == "contradictory" and status != "contradictory":
            return False
        if reason == "low_confidence" and (status != "insufficient_evidence" or not withheld):
            return False
        if reason in ("model", "contradictory") and withheld:
            return False
    else:  # accepted
        if status is None or status in _ABSTAIN_STATUSES or reason is not None:
            return False
        if status == "not_stated" and (values or withheld):
            return False
        if status == "stated" and not values:
            return False
    if field["review_required"] != any(v["disposition"] == "review_required" for v in values):
        return False
    for value in values:
        has_reason = value["review_reason"] == "critical_label"
        if (value["disposition"] == "review_required") is not has_reason:
            return False
    return not (name in _SINGLE_VALUED and len(values) + len(withheld) > 1)


def _check_consistency(result: Any) -> None:
    fields = result["fields"]
    for name in FIELD_NAMES:
        if not _field_consistent(name, fields[name]):
            raise _Unreadable(UnreadableReason.INCONSISTENT)
    if result["needs_review"] != any(fields[name]["review_required"] for name in FIELD_NAMES):
        raise _Unreadable(UnreadableReason.INCONSISTENT)
    valid = not result["issues"] and all(
        fields[name]["verdict"] != "rejected" for name in FIELD_NAMES
    )
    if result["valid"] != valid:
        raise _Unreadable(UnreadableReason.INCONSISTENT)


# --- judging --------------------------------------------------------------------------------

_DIMENSION_BUCKET = {
    ("model", "insufficient_evidence"): MissingBucket.ABSTAINED_INSUFFICIENT_EVIDENCE,
    ("model", "contradictory"): MissingBucket.ABSTAINED_CONTRADICTORY,
    ("contradictory", "contradictory"): MissingBucket.ABSTAINED_CONTRADICTORY_VALUES,
    ("low_confidence", "insufficient_evidence"): MissingBucket.ABSTAINED_LOW_CONFIDENCE,
}
_BUCKET_RANK = {bucket: rank for rank, bucket in enumerate(MissingBucket)}


class _Reference(Enum):
    CONFIRMED = "confirmed"
    PENDING = "pending"
    WITHHELD = "withheld"
    NOT_HOLDING = "not_holding"


def _check_facts(facts: ConversationFacts) -> None:
    has_run = facts.analysis_run_id is not None
    if has_run is not (facts.analysis_state is not None):
        raise ValueError("analysis_state is set exactly when there is a current run")
    if has_run is not (facts.analysis_revision_id is not None):
        raise ValueError("analysis_revision_id is set exactly when there is a current run")
    if not has_run and facts.analysis_is_mock:
        raise ValueError("analysis_is_mock needs a current run")
    if has_run and facts.analysis_state not in STAGE_STATES:
        raise ValueError(f"analysis_state {facts.analysis_state!r} is not a StageState value")
    if facts.extraction_run_id is None:
        if facts.extraction is not None:
            raise ValueError("an extraction needs its row (extraction_run_id)")
    elif not has_run:
        raise ValueError("an extraction row needs a current run")
    elif facts.extraction_run_id != facts.analysis_run_id:
        raise ValueError("the extraction row is not the row of the current run")


def _conversation_bucket(
    facts: ConversationFacts, definition: TrendDefinition
) -> tuple[MissingBucket | None, UnreadableReason | None]:
    """Steps 2 to 9 (README "The steps per conversation")."""
    if facts.analysis_run_id is None or facts.analysis_state in NO_ANALYSIS_STATES:
        return MissingBucket.ANALYSIS_UNAVAILABLE, None
    if facts.analysis_revision_id != facts.current_revision_id:
        return MissingBucket.ANALYSIS_STALE, None
    if facts.transcript_is_mock or facts.analysis_is_mock:
        return MissingBucket.MOCK_ANALYSIS, None
    if facts.extraction_run_id is None:
        return MissingBucket.EXTRACTION_UNAVAILABLE, None
    result = facts.extraction
    if not _version_fields_readable(result):
        return MissingBucket.EXTRACTION_UNREADABLE, UnreadableReason.VERSION_FIELDS
    assert isinstance(result, Mapping)
    pin = definition.taxonomy
    if (
        (result["taxonomy_id"], result["taxonomy_version"], result["taxonomy_digest"])
        != (pin.taxonomy_id, pin.taxonomy_version, pin.taxonomy_digest)
        or (result["policy_version"], result["policy_digest"])
        not in {(p.policy_version, p.policy_digest) for p in definition.extraction_policies}
        or result["validator_version"] not in definition.validator_versions
    ):
        return MissingBucket.VERSION_MISMATCH, None
    try:
        _walk(result)
        _check_consistency(result)
    except _Unreadable as unreadable:
        return MissingBucket.EXTRACTION_UNREADABLE, unreadable.reason
    if not result["valid"]:
        return MissingBucket.EXTRACTION_INVALID, None
    return None, None


def _reference(field: Any, label: str) -> _Reference:
    values, withheld = field["values"], field["withheld"]
    dispositions = {value["disposition"] for value in values if value["label"] == label}
    if "auto_accepted" in dispositions:
        return _Reference.CONFIRMED
    if "review_required" in dispositions:
        return _Reference.PENDING
    if any(value["label"] == label for value in withheld):
        return _Reference.WITHHELD
    return _Reference.NOT_HOLDING


def _rests_on_review(dimension: str, field: Any) -> bool:
    """A reference that does not hold rests on review: a single-valued dimension whose
    kept value waits for a person."""
    values = field["values"]
    return dimension in _SINGLE_VALUED and any(
        value["disposition"] == "review_required" for value in values
    )


def _judge_issue(
    issue: IssueDefinition,
    fields: Any,
    buckets: Mapping[str, MissingBucket | None],
) -> IssueJudgment:
    not_holding: list[str] = []
    first_missing: MissingBucket | None = None
    pending = False
    for reference in issue.match:
        bucket = buckets[reference.dimension]
        if bucket is not None:
            first_missing = first_missing or bucket
            continue
        result = _reference(fields[reference.dimension], reference.label)
        if result is _Reference.NOT_HOLDING:
            not_holding.append(reference.dimension)
        elif result is _Reference.WITHHELD:
            first_missing = first_missing or MissingBucket.VALUE_WITHHELD
        elif result is _Reference.PENDING:
            pending = True
    if not_holding:
        rests = all(_rests_on_review(name, fields[name]) for name in not_holding)
        return IssueJudgment(issue.id, IssueState.ABSENT, None, rests)
    if first_missing is not None:
        return IssueJudgment(issue.id, IssueState.MISSING, first_missing, False)
    if pending:
        return IssueJudgment(issue.id, IssueState.PENDING_REVIEW, None, False)
    return IssueJudgment(issue.id, IssueState.COUNTED, None, False)


def _judge_family(family_id: str, issues: Sequence[IssueJudgment]) -> FamilyJudgment:
    states = {issue.state for issue in issues}
    if IssueState.COUNTED in states:
        return FamilyJudgment(family_id, IssueState.COUNTED, None, False)
    if IssueState.PENDING_REVIEW in states:
        return FamilyJudgment(family_id, IssueState.PENDING_REVIEW, None, False)
    missing = [issue.bucket for issue in issues if issue.bucket is not None]
    if missing:
        first = min(missing, key=_BUCKET_RANK.__getitem__)
        return FamilyJudgment(family_id, IssueState.MISSING, first, False)
    rests = any(issue.absent_pending_review for issue in issues)
    return FamilyJudgment(family_id, IssueState.ABSENT, None, rests)


def _available_families(definition: TrendDefinition) -> list[str]:
    return [
        family.id for family in definition.families if family.source_state is SourceState.AVAILABLE
    ]


def _counted_issues(definition: TrendDefinition) -> list[IssueDefinition]:
    available = set(_available_families(definition))
    return [issue for issue in definition.active_issues() if issue.family in available]


def judge_conversation(
    facts: ConversationFacts, definition: TrendDefinition
) -> ConversationJudgment:
    """Give every active issue and every available family of ``definition`` one state.

    Raises ``ValueError`` for facts that break their own contract: a row of another
    run (``extraction_run_id != analysis_run_id``); a row without a run;
    ``analysis_state`` or ``analysis_revision_id`` set without a run, or missing with
    one; ``analysis_is_mock`` true without a run; an ``analysis_state`` outside
    ``STAGE_STATES``; ``extraction`` not None without a row.  The steps and their
    order are in the README ("The steps per conversation").
    """
    _check_facts(facts)

    def judged(
        exclusion: Exclusion | None = None,
        bucket: MissingBucket | None = None,
        unreadable: UnreadableReason | None = None,
        dimensions: tuple[DimensionJudgment, ...] = (),
        issues: tuple[IssueJudgment, ...] = (),
        families: tuple[FamilyJudgment, ...] = (),
    ) -> ConversationJudgment:
        return ConversationJudgment(
            meeting_id=facts.meeting_id,
            media_group_id=facts.media_group_id,
            trends_version=TRENDS_VERSION,
            definition_id=definition.definition_id,
            definition_version=definition.version,
            definition_digest=definition.digest,
            exclusion=exclusion,
            bucket=bucket,
            unreadable=unreadable,
            dimensions=dimensions,
            issues=issues,
            families=families,
        )

    if not facts.is_counted_copy:
        return judged(exclusion=Exclusion.DUPLICATE_MEDIA)
    counted = _counted_issues(definition)
    families = _available_families(definition)
    bucket, unreadable = _conversation_bucket(facts, definition)
    if bucket is not None:
        return judged(
            bucket=bucket,
            unreadable=unreadable,
            dimensions=tuple(DimensionJudgment(name, bucket) for name in REFERENCE_DIMENSIONS),
            issues=tuple(
                IssueJudgment(issue.id, IssueState.MISSING, bucket, False) for issue in counted
            ),
            families=tuple(
                FamilyJudgment(family_id, IssueState.MISSING, bucket, False)
                for family_id in families
            ),
        )
    assert isinstance(facts.extraction, Mapping)
    fields: Any = facts.extraction["fields"]
    buckets: dict[str, MissingBucket | None] = {}
    for name in REFERENCE_DIMENSIONS:
        field = fields[name]
        if field["verdict"] == "abstained":
            buckets[name] = _DIMENSION_BUCKET[(field["abstain_reason"], field["status"])]
        else:
            buckets[name] = None
    issues = tuple(_judge_issue(issue, fields, buckets) for issue in counted)
    return judged(
        dimensions=tuple(DimensionJudgment(name, buckets[name]) for name in REFERENCE_DIMENSIONS),
        issues=issues,
        families=tuple(
            _judge_family(
                family_id,
                [
                    judgment
                    for issue, judgment in zip(counted, issues, strict=True)
                    if issue.family == family_id
                ],
            )
            for family_id in families
        ),
    )


@dataclass(frozen=True)
class StateCounts:
    """Distinct conversations of the included population in each state.

    ``confirmed + pending_review + absent + sum(missing.values())`` is always the
    included population.  ``missing`` always has every ``MissingBucket`` key.
    ``absent_pending_review`` is a part of ``absent``, never added to it."""

    confirmed: int
    pending_review: int
    absent: int
    absent_pending_review: int
    missing: Mapping[MissingBucket, int]

    @property
    def denominator(self) -> int:
        """The assessable conversations: confirmed + pending_review + absent."""
        return self.confirmed + self.pending_review + self.absent


@dataclass(frozen=True)
class TrendTally:
    trends_version: str
    definition_id: str
    definition_version: int
    definition_digest: str
    included: int  # conversations judged and not excluded
    excluded: Mapping[Exclusion, int]  # always every Exclusion key
    issues: Mapping[str, StateCounts]  # every active issue of an available family
    families: Mapping[str, StateCounts]  # every available family


def tally(judgments: Iterable[ConversationJudgment], definition: TrendDefinition) -> TrendTally:
    """Add judged conversations up.  Raises ``ValueError`` for a judgment made with
    another definition (id, version or digest), a meeting id seen twice, or two
    included (not excluded) judgments with the same ``media_group_id``: a media group
    has at most one counted copy."""
    included = 0
    excluded = dict.fromkeys(Exclusion, 0)
    issue_ids = [issue.id for issue in _counted_issues(definition)]
    family_ids = _available_families(definition)
    counters = {key: _Counter() for key in [*issue_ids, *family_ids]}
    meetings: set[str] = set()
    groups: set[str] = set()
    expected = (definition.definition_id, definition.version, definition.digest)
    for judgment in judgments:
        if (
            judgment.definition_id,
            judgment.definition_version,
            judgment.definition_digest,
        ) != expected:
            raise ValueError(f"{judgment.meeting_id} was judged with another definition")
        if judgment.meeting_id in meetings:
            raise ValueError(f"meeting {judgment.meeting_id} is judged twice")
        meetings.add(judgment.meeting_id)
        if judgment.exclusion is not None:
            excluded[judgment.exclusion] += 1
            continue
        if judgment.media_group_id in groups:
            raise ValueError(f"media group {judgment.media_group_id} has two counted copies")
        groups.add(judgment.media_group_id)
        included += 1
        states = [
            *(
                (issue.issue_id, issue.state, issue.bucket, issue.absent_pending_review)
                for issue in judgment.issues
            ),
            *(
                (family.family_id, family.state, family.bucket, family.absent_pending_review)
                for family in judgment.families
            ),
        ]
        for key, state, bucket, rests in states:
            counters[key].add(state, bucket, rests)
    return TrendTally(
        trends_version=TRENDS_VERSION,
        definition_id=definition.definition_id,
        definition_version=definition.version,
        definition_digest=definition.digest,
        included=included,
        excluded=excluded,
        issues={key: counters[key].counts() for key in issue_ids},
        families={key: counters[key].counts() for key in family_ids},
    )


class _Counter:
    def __init__(self) -> None:
        self.confirmed = 0
        self.pending_review = 0
        self.absent = 0
        self.absent_pending_review = 0
        self.missing = dict.fromkeys(MissingBucket, 0)

    def add(self, state: IssueState, bucket: MissingBucket | None, rests: bool) -> None:
        if state is IssueState.COUNTED:
            self.confirmed += 1
        elif state is IssueState.PENDING_REVIEW:
            self.pending_review += 1
        elif state is IssueState.ABSENT:
            self.absent += 1
            self.absent_pending_review += int(rests)
        else:
            assert bucket is not None
            self.missing[bucket] += 1

    def counts(self) -> StateCounts:
        return StateCounts(
            confirmed=self.confirmed,
            pending_review=self.pending_review,
            absent=self.absent,
            absent_pending_review=self.absent_pending_review,
            missing=dict(self.missing),
        )
