"""Source, time and quality filters of the retrieval read path (HOM-95, ADR 0080).

:class:`SearchFilters` is one frozen value object every search entry point of
``backend.retrieval`` accepts.  Filters only NARROW: they become extra AND
conditions inside the candidate SQL of :func:`backend.retrieval.lexical.live_entries`,
so they act before scoring and before ``LIMIT``, exactly like the authorization
filters of ADR 0077, and no filter can add an entry the caller could not get
without it.  Validation happens at construction and every defect is a
``ValueError`` (never a ``TypeError`` or an ``AttributeError``).

Conversation time is ``COALESCE(meetings.started_at, meetings.created_at)``
(when it happened at the source, else upload time) with the same UTC handling
as ``retention_expires_at`` in ``live_entries``: on PostgreSQL an instant, on
SQLite the UTC wall-clock text the driver stores for an aware UTC value.

The audio-flag test reads the recording's channel inspection
(``recordings`` by ``meeting_id`` with ``deleted_at IS NULL``, then
``media_objects.channel_inspection``): a conversation with no recording, no
media object or a NULL inspection is KEPT (unknown quality is not a flag).
The JSON test is built per dialect with SQLAlchemy constructs (``json_*``
functions, never ``jsonb_*``: the column is ``json`` on PostgreSQL), with
every flag value as a bound parameter.  No value is ever put into SQL text.
"""

from __future__ import annotations

import math
import numbers
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import (
    ColumnElement,
    Select,
    String,
    and_,
    case,
    exists,
    false,
    func,
    literal_column,
    or_,
    select,
)

from backend.database.saas_models import (
    ConversationType,
    MediaObject,
    Meeting,
    Recording,
    RetrievalEntry,
    TranscriptSegment,
)

__all__ = [
    "AUDIO_FLAGS",
    "CONVERSATION_TYPES",
    "LIST_FLAGS",
    "MAX_SOURCE_LENGTH",
    "SearchFilters",
    "apply_search_filters",
    "checked_filters",
    "conversation_at",
    "sql_safe_text",
]

CONVERSATION_TYPES = frozenset(member.value for member in ConversationType)
# The flags read from the inspection's ``flags`` list; ``narrowband`` is its
# own boolean key of the inspection.
LIST_FLAGS = frozenset({"silent_channel", "duplicated_channels", "mono_as_stereo"})
AUDIO_FLAGS = LIST_FLAGS | {"narrowband"}
MAX_SOURCE_LENGTH = 30  # the length of ``meetings.source``


def _string_set(value: object, *, field: str) -> frozenset[str]:
    """A non-empty frozenset of strings; lists, tuples, sets and frozensets in,
    every defect (a str, a non-iterable, an empty set, a non-str item) a
    ``ValueError``."""
    if not isinstance(value, list | tuple | set | frozenset):
        raise ValueError(f"{field} must be a list, tuple, set or frozenset of strings")
    items: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field} must hold strings only")
        # Stored as an exact ``str``: a ``str`` subclass such as a
        # ``ConversationType`` member would otherwise reach the driver as an
        # enum, which some drivers bind by its name, not its value.
        items.add(str.__str__(item))
    if not items:
        raise ValueError(f"{field} must hold at least one value")
    return frozenset(items)


def sql_safe_text(value: str) -> bool:
    """A str the database can take as a bound parameter: no NUL character and
    encodable as UTF-8 (no lone surrogate).  The same rule as the query check
    of ``evidence._checked_query``."""
    if "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _utc_instant(value: object, *, field: str) -> datetime:
    """An aware datetime as its UTC instant; anything else a ``ValueError``."""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{field} must be a datetime with a timezone")
    try:
        return value.astimezone(UTC)
    except OverflowError as exc:
        # For example ``datetime.max`` west of UTC: its UTC instant does not
        # fit a datetime.
        raise ValueError(f"{field} has no UTC instant a datetime can hold") from exc


def checked_filters(filters: object) -> SearchFilters | None:
    """The shared ``filters`` argument check: ``SearchFilters`` or ``None``."""
    if filters is not None and not isinstance(filters, SearchFilters):
        raise ValueError("filters must be a SearchFilters or None")
    return filters


@dataclass(frozen=True)
class SearchFilters:
    """Narrowing conditions on the conversation, its transcript quality and the
    recording's channel inspection.  Every field optional; ``None`` means no
    condition, and ``SearchFilters()`` with every field ``None`` filters
    nothing (exactly ``filters=None``)."""

    conversation_types: frozenset[str] | None = None
    sources: frozenset[str] | None = None
    started_from: datetime | None = None
    started_before: datetime | None = None
    min_confidence: float | None = None
    exclude_audio_flags: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if self.conversation_types is not None:
            types = _string_set(self.conversation_types, field="conversation_types")
            unknown = types - CONVERSATION_TYPES
            if unknown:
                raise ValueError(f"conversation_types holds unknown values: {sorted(unknown)}")
            object.__setattr__(self, "conversation_types", types)
        if self.sources is not None:
            sources = _string_set(self.sources, field="sources")
            for source in sources:
                if not 1 <= len(source) <= MAX_SOURCE_LENGTH:
                    raise ValueError(f"a source must be 1..{MAX_SOURCE_LENGTH} characters")
                if not sql_safe_text(source):
                    raise ValueError("a source must be encodable as UTF-8 and hold no NUL")
            object.__setattr__(self, "sources", sources)
        started_from = None
        started_before = None
        if self.started_from is not None:
            started_from = _utc_instant(self.started_from, field="started_from")
            object.__setattr__(self, "started_from", started_from)
        if self.started_before is not None:
            started_before = _utc_instant(self.started_before, field="started_before")
            object.__setattr__(self, "started_before", started_before)
        if started_from is not None and started_before is not None:
            if started_from >= started_before:
                raise ValueError("started_from must be before started_before")
        if self.min_confidence is not None:
            value = self.min_confidence
            if isinstance(value, bool) or not isinstance(value, numbers.Real):
                raise ValueError("min_confidence must be a real number")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("min_confidence must be finite")
            # The range is checked on the real number itself, before float():
            # float(10**400) would raise OverflowError, not ValueError.  A NaN
            # of another real type fails this comparison too.
            if not 0 <= value <= 1:
                raise ValueError("min_confidence must be inside [0.0, 1.0]")
            object.__setattr__(self, "min_confidence", float(value))
        if self.exclude_audio_flags is not None:
            flags = _string_set(self.exclude_audio_flags, field="exclude_audio_flags")
            unknown = flags - AUDIO_FLAGS
            if unknown:
                raise ValueError(f"exclude_audio_flags holds unknown values: {sorted(unknown)}")
            object.__setattr__(self, "exclude_audio_flags", flags)


def conversation_at() -> ColumnElement[datetime]:
    """When the conversation happened: ``started_at`` at the source, else the
    meeting row's ``created_at`` (upload time)."""
    return func.coalesce(Meeting.started_at, Meeting.created_at)


def _audio_flag_predicate(flags: frozenset[str], *, dialect_name: str) -> ColumnElement[bool]:
    """``NOT EXISTS`` a live recording of the conversation whose channel
    inspection holds one of ``flags``.

    ``silent_channel``, ``duplicated_channels`` and ``mono_as_stereo`` are
    looked up in the inspection's ``flags`` list; ``narrowband`` is the
    inspection's ``narrowband`` JSON boolean only (a ``"narrowband"`` string
    inside ``flags`` does not count).  The flag values are bound parameters
    (an expanding ``IN``); the JSON key names are structure.  ``CASE WHEN``
    keeps the array walk lazy: walking a ``flags`` value that is not an array
    would be an error on both dialects.  A conversation with no live
    recording, no media object or a NULL inspection has no such row, so
    ``NOT EXISTS`` keeps it.  The subquery correlates to the ``meetings`` row
    of the enclosing candidate select.
    """
    listed = sorted(flags & LIST_FLAGS)
    inspection = MediaObject.channel_inspection
    branches: list[ColumnElement[bool]] = []
    if dialect_name == "postgresql":
        # The column is ``json`` (not ``jsonb``): the json_* functions.
        flags_value = inspection.op("->")(literal_column("'flags'"))
        flags_type = func.json_typeof(flags_value)
        elements = func.json_array_elements_text(flags_value).table_valued("value")
        narrowband = and_(
            func.json_typeof(inspection.op("->")(literal_column("'narrowband'"))) == "boolean",
            inspection.op("->>", return_type=String)(literal_column("'narrowband'")) == "true",
        )
    else:
        flags_type = func.json_type(inspection, "$.flags")
        elements = func.json_each(inspection, "$.flags").table_valued("value")
        # SQLite's json_type names a JSON true 'true' (and a false 'false').
        narrowband = func.json_type(inspection, "$.narrowband") == "true"
    if listed:
        holds_listed = exists(
            select(literal_column("1")).select_from(elements).where(elements.c.value.in_(listed))
        )
        branches.append(case((flags_type == "array", holds_listed), else_=false()))
    if "narrowband" in flags:
        branches.append(narrowband)
    flagged = exists(
        select(literal_column("1"))
        .select_from(Recording)
        .join(MediaObject, MediaObject.id == Recording.media_object_id)
        .where(
            Recording.meeting_id == Meeting.id,
            Recording.deleted_at.is_(None),
            or_(*branches),
        )
    )
    return ~flagged


def apply_search_filters(query: Select, filters: SearchFilters, *, dialect_name: str) -> Select:
    """The set conditions of ``filters`` as extra AND conditions (and, for
    ``min_confidence``, one extra join) on a ``live_entries`` candidate select.

    The caller has already checked the type.  Every condition narrows: nothing
    here can widen the candidate set the authorization filters built.
    """
    conditions = []
    if filters.conversation_types is not None:
        conditions.append(Meeting.conversation_type.in_(sorted(filters.conversation_types)))
    if filters.sources is not None:
        conditions.append(Meeting.source.in_(sorted(filters.sources)))
    if filters.started_from is not None:
        conditions.append(conversation_at() >= filters.started_from)
    if filters.started_before is not None:
        conditions.append(conversation_at() < filters.started_before)
    if filters.min_confidence is not None:
        # The entry's own transcript segment, joined on its stored id; a
        # segment with NULL confidence fails the comparison and drops out.
        query = query.join(TranscriptSegment, TranscriptSegment.id == RetrievalEntry.segment_id)
        conditions.append(TranscriptSegment.confidence >= filters.min_confidence)
    if filters.exclude_audio_flags is not None:
        conditions.append(
            _audio_flag_predicate(filters.exclude_audio_flags, dialect_name=dialect_name)
        )
    return query.where(*conditions) if conditions else query
