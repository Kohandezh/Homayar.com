"""HOM-95 implementer tests: search filters, ranking-aware vector window,
``score_entries``, the reranker adapter and bounded hybrid search (SPEC 4
A1 to A12; A13 lives in ``test_retrieval_filtered_recall.py``).

The world builders reuse the HOM-94 helpers (``_seed_view``, ``_index``,
``_embed`` and the evidence-suite context helpers), so every entry here is a
real indexed, embedded entry read under the live joins.  Filter truths are
computed in Python from the fixture metadata this file writes, never from the
SQL under test.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from backend.database.saas_models import (
    ConversationType,
    MediaKind,
    MediaObject,
    Meeting,
    Membership,
    Recording,
    Team,
    TeamAssignment,
    TeamRole,
    TranscriptRevision,
    TranscriptSegment,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.retrieval import vector_store as vector_store_module
from backend.retrieval.evidence import fetch_evidence
from backend.retrieval.filters import SearchFilters
from backend.retrieval.hybrid import HybridHit, HybridOutcome, search_evidence_hybrid
from backend.retrieval.lexical import lexical_search, live_entries
from backend.retrieval.rerank import RerankCandidate
from backend.retrieval.vector_store import TableVectorStore
from backend.security.context import WorkspaceContext
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import _tenant
from backend.tests.test_retrieval_evidence import (
    _bound,
    _captured_statements,
    _context,
    _open_membership_window,
    _touched_index,
)
from backend.tests.test_retrieval_index import (
    DIMENSIONS,
    FakeEmbeddingProvider,
    _embed,
    _entries,
    _index,
    _seed_view,
    _unrestricted,
    _version,
)

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)
STORE = TableVectorStore()


# --- the world and its helpers ------------------------------------------------------------


def _texts(label: str, index: int) -> tuple[str, str]:
    return (f"نشانه {label}{index} گفتگو الف", f"نشانه {label}{index} گفتگو ب")


def _hworld(client, email_outbox, label, *, meetings=6, embed_skip=()):
    """A tenant whose meetings are indexed; every meeting embedded except the
    ones in ``embed_skip`` (their entries have no vector of this version)."""
    tenant = _tenant(client, email_outbox, label)
    _open_membership_window(tenant["user_id"], tenant["workspace_id"])
    setups = []
    for index in range(meetings):
        setup = _seed_view(tenant, f"{label}-m{index}", texts=_texts(label, index))
        _index(setup["text_view_id"], tenant["workspace_id"])
        setups.append(setup)
    embed_ids = [
        entry.id
        for index, setup in enumerate(setups)
        if index not in embed_skip
        for entry in _entries(setup["meeting_id"])
    ]
    if embed_ids:
        _embed(tenant["workspace_id"], embed_ids)
    return {"tenant": tenant, "workspace_id": tenant["workspace_id"], "setups": setups}


def _ids(world, index: int | None = None) -> list[str]:
    setups = world["setups"] if index is None else [world["setups"][index]]
    return [entry.id for setup in setups for entry in _entries(setup["meeting_id"])]


def _set_meeting(meeting_id: str, **values) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(Meeting).where(Meeting.id == meeting_id).values(**values))
        db.commit()


def _confidences(meeting_id: str, values) -> None:
    """Write one confidence per segment, in sequence order (None keeps NULL)."""
    with inspection_session_factory()() as db:
        rows = list(
            db.scalars(
                select(TranscriptSegment.id)
                .where(TranscriptSegment.meeting_id == meeting_id)
                .order_by(TranscriptSegment.sequence)
            )
        )
        for segment_id, value in zip(rows, values, strict=True):
            db.execute(
                update(TranscriptSegment)
                .where(TranscriptSegment.id == segment_id)
                .values(confidence=value)
            )
        db.commit()


_FROM_FLAGS = object()  # the default: build the inspection from flags/narrowband


def _inspection(
    world,
    index: int,
    *,
    flags=(),
    narrowband=False,
    media=True,
    inspection=_FROM_FLAGS,
    deleted=False,
) -> None:
    """Give one meeting a recording with a channel inspection, or a recording
    with no media object (``media=False``), or a media object with an
    explicit ``inspection`` value (``None`` for no inspection at all).
    ``deleted`` soft-deletes the recording row."""
    meeting_id = world["setups"][index]["meeting_id"]
    organization_id = world["tenant"]["organization_id"]
    workspace_id = world["workspace_id"]
    if inspection is _FROM_FLAGS:
        inspection = {"flags": list(flags), "narrowband": narrowband}
    marker = uuid.uuid4().hex
    with inspection_session_factory()() as db:
        media_id = None
        if media:
            media_object = MediaObject(
                organization_id=organization_id,
                workspace_id=workspace_id,
                meeting_id=meeting_id,
                kind=MediaKind.ORIGINAL,
                storage_backend="local",
                storage_key=f"insp-{marker}",
                content_type="audio/wav",
                size_bytes=10,
                sha256=hashlib.sha256(f"insp-{marker}".encode()).hexdigest(),
                producer="hom95-tests",
                channel_inspection=inspection,
            )
            db.add(media_object)
            db.flush()
            media_id = media_object.id
        db.add(
            Recording(
                workspace_id=workspace_id,
                meeting_id=meeting_id,
                storage_key=f"rec-{marker}",
                original_filename="call.wav",
                media_type="audio/wav",
                container_format="wav",
                size_bytes=10,
                checksum_sha256=hashlib.sha256(f"rec-{marker}".encode()).hexdigest(),
                duration_seconds=1,
                media_object_id=media_id,
                deleted_at=NOW if deleted else None,
            )
        )
        db.commit()


def _meta_world(client, email_outbox, label="hom95-meta"):
    """The A2 world: six meetings with every filter dimension pinned.

    Meeting 3 has ``started_at`` NULL and an explicit ``created_at`` thirty
    days back, so the created_at fallback is exercised without depending on
    the real clock.  Conversation times, newest first: 5 (NOW-1d), 4 (NOW-2d),
    2 (NOW-4d), 1 (NOW-5d), 0 (NOW-6d), 3 (created_at, NOW-30d).
    """
    world = _hworld(client, email_outbox, label)
    # meeting: conversation_type, source, started_at, created_at, confidences
    plan = [
        ("call", "upload", NOW - 6 * DAY, None, (0.9, None)),
        ("meeting", "asanak", NOW - 5 * DAY, None, (0.5, 0.95)),
        ("voicemail", "upload", NOW - 4 * DAY, None, (0.99, 0.3)),
        ("call", "upload", None, NOW - 30 * DAY, (0.85, 0.85)),
        ("meeting", "upload", NOW - 2 * DAY, None, (0.1, 0.2)),
        ("voicemail", "asanak", NOW - 1 * DAY, None, (0.9, 0.9)),
    ]
    for index, (kind, source, started, created, confidences) in enumerate(plan):
        meeting_id = world["setups"][index]["meeting_id"]
        values = {"conversation_type": kind, "source": source}
        if started is not None:
            values["started_at"] = started
        if created is not None:
            values["created_at"] = created
        _set_meeting(meeting_id, **values)
        _confidences(meeting_id, confidences)
    _inspection(world, 1, flags=["silent_channel"])
    _inspection(world, 2, narrowband=True)
    _inspection(world, 3, flags=[], narrowband=False)  # an inspection without flags
    _inspection(world, 4, media=False)  # a recording with no media object
    _inspection(world, 5, flags=["duplicated_channels"], narrowband=True)
    # meeting 0 keeps no recording at all.
    return world, plan


def _expected_entries(world, meeting_indexes) -> set[str]:
    return {entry_id for index in meeting_indexes for entry_id in _ids(world, index)}


def _owner(world) -> WorkspaceContext:
    return _context(world["tenant"]["user_id"], world["workspace_id"])


class FakeReranker:
    """Records what it was given and plays back decided scores (or faults)."""

    def __init__(self, scores=None, error: Exception | None = None):
        self.scores = scores  # callable(candidates) -> Sequence[float]
        self.error = error
        self.calls: list[dict] = []

    def rerank(self, *, query, candidates, timeout_ms):
        self.calls.append(
            {"query": query, "candidates": list(candidates), "timeout_ms": timeout_ms}
        )
        if self.error is not None:
            raise self.error
        if self.scores is None:
            return [float(index) for index in range(len(candidates))]
        return self.scores(list(candidates))


def _query_vector(text: str) -> list[float]:
    return FakeEmbeddingProvider().query_for(text)


def _frozen_clock() -> float:
    """A clock that never moves: no stage is skipped for time, however slow
    the test runner is.  Tests of the time budget pass their own clock."""
    return 0.0


def _hybrid(db, context, query, **kwargs):
    vector = kwargs.pop("vector", None)
    if vector is not None:
        kwargs.setdefault("store", STORE)
        kwargs.setdefault("embedding_version", _version())
        kwargs.setdefault("query_vector", vector)
    kwargs.setdefault("clock", _frozen_clock)
    return search_evidence_hybrid(db, context=context, query=query, now=NOW, **kwargs)


def _rrf_ranks(hits) -> dict[str, int]:
    """Rank = 1 + the count of strictly higher scores, in the given order."""
    ranks: dict[str, int] = {}
    previous = None
    for position, hit in enumerate(hits):
        if previous is None or hit.score != previous:
            ranks[hit.entry_id] = position + 1
            previous = hit.score
        else:
            ranks[hit.entry_id] = ranks[hits[position - 1].entry_id]
    return ranks


def _expected_fusion(
    db,
    context,
    query,
    *,
    vector,
    filters=None,
    include_history=False,
    lexical_candidates=50,
    vector_candidates=50,
    candidate_limit=2000,
):
    """The SPEC 3.5 fusion computed from the primitives' own outputs."""
    access = context.record_access
    workspace_id = context.workspace.id
    lexical = lexical_search(
        db,
        access=access,
        workspace_id=workspace_id,
        query=query,
        limit=lexical_candidates,
        include_history=include_history,
        now=NOW,
        filters=filters,
    )
    nearest = STORE.nearest(
        db,
        access=access,
        workspace_id=workspace_id,
        embedding_version=_version(),
        query=vector,
        k=vector_candidates,
        candidate_limit=candidate_limit,
        include_history=include_history,
        now=NOW,
        filters=filters,
    )
    nearest_ids = {hit.entry_id for hit in nearest}
    rescue = [hit.entry_id for hit in lexical if hit.entry_id not in nearest_ids]
    scored = (
        STORE.score_entries(
            db,
            access=access,
            workspace_id=workspace_id,
            embedding_version=_version(),
            query=vector,
            entry_ids=rescue,
            include_history=include_history,
            now=NOW,
            filters=filters,
        )
        if rescue
        else []
    )
    vector_list = sorted([*nearest, *scored], key=lambda hit: (-hit.score, hit.entry_id))
    lexical_ranks = _rrf_ranks(lexical)
    vector_ranks = _rrf_ranks(vector_list)
    ids = {hit.entry_id for hit in lexical} | {hit.entry_id for hit in vector_list}
    by_id = {hit.entry_id: hit for hit in [*lexical, *vector_list]}
    fused = {
        entry_id: (
            (1 / (60 + lexical_ranks[entry_id]) if entry_id in lexical_ranks else 0.0)
            + (1 / (60 + vector_ranks[entry_id]) if entry_id in vector_ranks else 0.0)
        )
        for entry_id in ids
    }
    order = sorted(ids, key=lambda entry_id: (-fused[entry_id], entry_id))
    return {
        "order": order,
        "fused": fused,
        "lexical_ranks": lexical_ranks,
        "vector_ranks": vector_ranks,
        "by_id": by_id,
    }


# --- A1: SearchFilters validation (pure unit, no database) ---------------------------------


def test_a1_every_listed_defect_is_a_value_error():
    aware = datetime(2026, 11, 1, tzinfo=UTC)
    defects = [
        lambda: SearchFilters(conversation_types=7),
        lambda: SearchFilters(conversation_types="call"),
        lambda: SearchFilters(conversation_types={}),
        lambda: SearchFilters(conversation_types=set()),
        lambda: SearchFilters(conversation_types={"call", 7}),
        lambda: SearchFilters(conversation_types={"call", "webinar"}),
        lambda: SearchFilters(conversation_types={"CALL"}),
        lambda: SearchFilters(sources=7),
        lambda: SearchFilters(sources="upload"),
        lambda: SearchFilters(sources=set()),
        lambda: SearchFilters(sources={"upload", ""}),
        lambda: SearchFilters(sources={"x" * 31}),
        lambda: SearchFilters(sources={7}),
        lambda: SearchFilters(sources={"up\x00load"}),
        lambda: SearchFilters(sources={"\ud800"}),
        lambda: SearchFilters(sources=["upload", None]),
        lambda: SearchFilters(conversation_types=iter(["call"])),
        lambda: SearchFilters(started_from=datetime(2026, 11, 1).date()),
        lambda: SearchFilters(min_confidence=object()),
        # Values whose conversion overflows are ValueErrors, not OverflowErrors.
        lambda: SearchFilters(min_confidence=10**400),
        lambda: SearchFilters(
            started_from=datetime.max.replace(tzinfo=timezone(-timedelta(hours=1)))
        ),
        lambda: SearchFilters(
            started_before=datetime.min.replace(tzinfo=timezone(timedelta(hours=3, minutes=30)))
        ),
        lambda: SearchFilters(started_from=datetime(2026, 11, 1)),
        lambda: SearchFilters(started_from="2026-11-01"),
        lambda: SearchFilters(started_before=datetime(2026, 11, 1)),
        lambda: SearchFilters(started_from=aware, started_before=aware),
        lambda: SearchFilters(started_from=aware, started_before=aware - timedelta(seconds=1)),
        lambda: SearchFilters(min_confidence=True),
        lambda: SearchFilters(min_confidence="0.5"),
        lambda: SearchFilters(min_confidence=float("nan")),
        lambda: SearchFilters(min_confidence=float("inf")),
        lambda: SearchFilters(min_confidence=-0.1),
        lambda: SearchFilters(min_confidence=1.1),
        lambda: SearchFilters(exclude_audio_flags=7),
        lambda: SearchFilters(exclude_audio_flags="silent_channel"),
        lambda: SearchFilters(exclude_audio_flags=set()),
        lambda: SearchFilters(exclude_audio_flags={"silent_channel", "loud_channel"}),
        lambda: SearchFilters(exclude_audio_flags={"NARROWBAND"}),
        lambda: SearchFilters(exclude_audio_flags={7}),
    ]
    for defect in defects:
        with pytest.raises(ValueError):
            defect()


def test_a1_accepted_spellings_are_normalized():
    from_offset_zone = timezone(timedelta(hours=3, minutes=30))
    filters = SearchFilters(
        conversation_types=["call", "meeting"],
        sources=("upload", "asanak"),
        started_from=datetime(2026, 11, 1, 12, 0, tzinfo=from_offset_zone),
        started_before=datetime(2026, 11, 10, 8, 30, tzinfo=from_offset_zone),
        min_confidence=0,
        exclude_audio_flags=frozenset({"narrowband"}),
    )
    assert filters.conversation_types == frozenset({"call", "meeting"})
    assert filters.sources == frozenset({"upload", "asanak"})
    assert filters.started_from == datetime(2026, 11, 1, 8, 30, tzinfo=UTC)
    assert filters.started_before == datetime(2026, 11, 10, 5, 0, tzinfo=UTC)
    assert filters.min_confidence == 0.0
    assert filters.exclude_audio_flags == frozenset({"narrowband"})
    # Every accepted spelling of a set, and the enum member itself.
    for spelling in (["voicemail"], ("voicemail",), {"voicemail"}, frozenset({"voicemail"})):
        assert SearchFilters(conversation_types=spelling).conversation_types == frozenset(
            {"voicemail"}
        )
    from_enum = SearchFilters(conversation_types=[ConversationType.CALL]).conversation_types
    assert from_enum == frozenset({"call"})
    assert all(type(value) is str for value in from_enum)  # the value, not the enum
    # Every ConversationType value is accepted, "other" included (SPEC rev 1).
    every_type = SearchFilters(conversation_types=[member.value for member in ConversationType])
    assert every_type.conversation_types == frozenset({"call", "meeting", "voicemail", "other"})
    # min_confidence is stored as a float, also when given as 0 or 1 (rev 1, 1).
    for given, stored in ((0, 0.0), (1, 1.0), (0.25, 0.25)):
        value = SearchFilters(min_confidence=given).min_confidence
        assert value == stored and type(value) is float
    # SearchFilters() filters nothing and equals itself.
    assert SearchFilters() == SearchFilters(
        conversation_types=None,
        sources=None,
        started_from=None,
        started_before=None,
        min_confidence=None,
        exclude_audio_flags=None,
    )


def test_a2_other_and_an_enum_member_filter_on_the_stored_value(client, email_outbox):
    world = _hworld(client, email_outbox, "hom95-a2-other", meetings=2)
    _set_meeting(world["setups"][1]["meeting_id"], conversation_type="other")
    context = _owner(world)
    with _bound(world["workspace_id"]) as db:
        for types, meetings in (
            ({"other"}, (1,)),
            ([ConversationType.CALL], (0,)),
            ((ConversationType.OTHER, "call"), (0, 1)),
        ):
            outcome = _hybrid(
                db,
                context,
                "نشانه",
                vector=_query_vector("نشانه"),
                filters=SearchFilters(conversation_types=types),
                k=50,
            )
            assert {hit.entry_id for hit in outcome.hits} == _expected_entries(world, meetings)


# --- A2/A3/A4: every filter narrows exactly, inside the SQL --------------------------------

# Every deny-case names the meetings that pass, and an allow-control: a filter
# value that includes the dropped meetings, so the same entries come back.
# "confidence" marks the per-entry truth of min_confidence (see below).
ALL_MEETINGS = (0, 1, 2, 3, 4, 5)


@pytest.mark.parametrize(
    "deny,expected_meetings,allow,allowed_meetings",
    [
        pytest.param(
            lambda: SearchFilters(conversation_types={"call"}),
            (0, 3),
            lambda: SearchFilters(conversation_types={"call", "meeting", "voicemail"}),
            ALL_MEETINGS,
            id="conversation-types",
        ),
        pytest.param(
            lambda: SearchFilters(sources={"asanak"}),
            (1, 5),
            lambda: SearchFilters(sources={"asanak", "upload"}),
            ALL_MEETINGS,
            id="sources",
        ),
        pytest.param(
            lambda: SearchFilters(started_from=NOW - 3 * DAY),
            (4, 5),
            lambda: SearchFilters(started_from=NOW - 30 * DAY),
            ALL_MEETINGS,
            id="started-from",
        ),
        pytest.param(
            lambda: SearchFilters(started_before=NOW - 3 * DAY),
            (0, 1, 2, 3),
            lambda: SearchFilters(started_before=NOW - DAY + timedelta(microseconds=1)),
            ALL_MEETINGS,
            id="started-before",
        ),
        pytest.param(
            lambda: SearchFilters(started_from=NOW - 4 * DAY, started_before=NOW - 2 * DAY),
            (2,),
            lambda: SearchFilters(started_from=NOW - 30 * DAY, started_before=NOW),
            ALL_MEETINGS,
            id="date-range-half-open",
        ),
        pytest.param(
            lambda: SearchFilters(min_confidence=0.5),
            "confidence",
            lambda: SearchFilters(min_confidence=0.1),
            "confidence-allow",
            id="min-confidence",
        ),
        pytest.param(
            lambda: SearchFilters(exclude_audio_flags={"silent_channel"}),
            (0, 2, 3, 4, 5),
            lambda: SearchFilters(exclude_audio_flags={"mono_as_stereo"}),
            ALL_MEETINGS,
            id="exclude-silent-channel",
        ),
        pytest.param(
            lambda: SearchFilters(exclude_audio_flags={"duplicated_channels"}),
            (0, 1, 2, 3, 4),
            lambda: SearchFilters(exclude_audio_flags={"mono_as_stereo"}),
            ALL_MEETINGS,
            id="exclude-duplicated-channels",
        ),
        pytest.param(
            lambda: SearchFilters(exclude_audio_flags={"narrowband"}),
            (0, 1, 3, 4),
            lambda: SearchFilters(exclude_audio_flags={"mono_as_stereo"}),
            ALL_MEETINGS,
            id="exclude-narrowband",
        ),
        pytest.param(
            lambda: SearchFilters(exclude_audio_flags={"mono_as_stereo", "narrowband"}),
            (0, 1, 3, 4),
            lambda: SearchFilters(exclude_audio_flags={"mono_as_stereo"}),
            ALL_MEETINGS,
            id="exclude-flags-union",
        ),
    ],
)
def test_a2_each_filter_narrows_lexical_vector_and_hybrid(
    client, email_outbox, deny, expected_meetings, allow, allowed_meetings
):
    world, _plan = _meta_world(client, email_outbox, "hom95-a2")
    context = _owner(world)

    def truth(meetings) -> set[str]:
        if meetings == "confidence":
            # min_confidence=0.5: 0.9 passes, NULL fails (meeting 0); 0.5 and
            # 0.95 pass (meeting 1, 0.5 is the inclusive edge); 0.99 passes,
            # 0.3 fails (meeting 2); meetings 3 and 5 pass; meeting 4 (0.1,
            # 0.2) fails.
            return (
                {_ids(world, 0)[0]}
                | set(_ids(world, 1))
                | {_ids(world, 2)[0]}
                | set(_ids(world, 3))
                | set(_ids(world, 5))
            )
        if meetings == "confidence-allow":
            # min_confidence=0.1 keeps every non-NULL confidence (0.1 is the
            # inclusive edge of meeting 4); only the NULL segment drops.
            return set(_ids(world)) - {_ids(world, 0)[1]}
        return _expected_entries(world, meetings)

    def every_path(filters) -> list[set[str]]:
        lexical = lexical_search(
            db,
            access=context.record_access,
            workspace_id=world["workspace_id"],
            query="نشانه",
            limit=100,
            now=NOW,
            filters=filters,
        )
        vector = STORE.nearest(
            db,
            access=context.record_access,
            workspace_id=world["workspace_id"],
            embedding_version=_version(),
            query=_query_vector("نشانه"),
            k=100,
            candidate_limit=100,
            now=NOW,
            filters=filters,
        )
        outcome = _hybrid(
            db, context, "نشانه", vector=_query_vector("نشانه"), filters=filters, k=50
        )
        assert outcome.state == "answered"
        return [
            {hit.entry_id for hit in lexical},
            {hit.entry_id for hit in vector},
            {hit.entry_id for hit in outcome.hits},
        ]

    with _bound(world["workspace_id"]) as db:
        denied = truth(expected_meetings)
        allowed = truth(allowed_meetings)
        assert denied < allowed  # the deny-case really drops something
        assert every_path(deny()) == [denied, denied, denied]
        # The allow-control: the dropped entries come back when the filter
        # value includes them.
        assert every_path(allow()) == [allowed, allowed, allowed]


def test_a3_null_confidence_and_unknown_quality_rules(client, email_outbox):
    world, _plan = _meta_world(client, email_outbox, "hom95-a3")
    context = _owner(world)
    # Meeting 0 gains a recording that is soft-deleted and flagged: a deleted
    # recording is not the conversation's recording, so meeting 0 stays.
    _inspection(world, 0, flags=["silent_channel"], narrowband=True, deleted=True)
    # Meeting 3's inspection names "narrowband" in its flags list but its
    # narrowband boolean is false: the boolean decides, so it stays.
    with inspection_session_factory()() as db:
        db.execute(
            update(MediaObject)
            .where(MediaObject.meeting_id == world["setups"][3]["meeting_id"])
            .values(channel_inspection={"flags": ["narrowband"], "narrowband": False})
        )
        db.commit()
    every_flag = {"silent_channel", "duplicated_channels", "mono_as_stereo", "narrowband"}

    def returned(filters) -> set[str]:
        outcome = _hybrid(
            db, context, "نشانه", vector=_query_vector("نشانه"), filters=filters, k=50
        )
        return {hit.entry_id for hit in outcome.hits}

    with _bound(world["workspace_id"]) as db:
        # A NULL-confidence segment never passes min_confidence, even at 0.
        for bound in (0.0, 0.5):
            assert _ids(world, 0)[1] not in returned(SearchFilters(min_confidence=bound))
        assert _ids(world, 0)[1] in returned(None)
        kept = returned(SearchFilters(exclude_audio_flags=every_flag))
        # Kept: 0 (only a deleted recording), 3 (narrowband false, whatever
        # its flags list says), 4 (a recording without a media object).
        assert kept == _expected_entries(world, (0, 3, 4))
        # narrowband reads the boolean: meeting 2 has no flags but narrowband
        # true, so excluding only narrowband drops 2 and 5, not 3.
        assert returned(SearchFilters(exclude_audio_flags={"narrowband"})) == (
            _expected_entries(world, (0, 1, 3, 4))
        )


def test_a3_a_media_object_without_inspection_is_kept(client, email_outbox):
    world = _hworld(client, email_outbox, "hom95-a3-null", meetings=3)
    context = _owner(world)
    _inspection(world, 0, inspection=None)  # inspected never: NULL
    _inspection(world, 1, flags=["mono_as_stereo"])
    _inspection(world, 2, inspection={"sample_rate_hz": 8000})  # no flags key at all
    with _bound(world["workspace_id"]) as db:
        lexical = lexical_search(
            db,
            access=context.record_access,
            workspace_id=world["workspace_id"],
            query="نشانه",
            limit=100,
            now=NOW,
            filters=SearchFilters(
                exclude_audio_flags={"mono_as_stereo", "silent_channel", "narrowband"}
            ),
        )
        assert {hit.entry_id for hit in lexical} == _expected_entries(world, (0, 2))


def test_a4_half_open_range_on_the_coalesced_conversation_time(client, email_outbox):
    world, plan = _meta_world(client, email_outbox, "hom95-a4")
    context = _owner(world)
    with _bound(world["workspace_id"]) as db:
        # started_at exactly at the bound is in; exactly at started_before is out.
        exact = _hybrid(
            db,
            context,
            "نشانه",
            vector=_query_vector("نشانه"),
            filters=SearchFilters(started_from=NOW - 4 * DAY, started_before=NOW - 2 * DAY),
            k=50,
        )
        assert {hit.entry_id for hit in exact.hits} == set(_ids(world, 2))
        # Meeting 3 has started_at NULL: its conversation time IS created_at
        # (pinned thirty days back), so a window around that instant holds
        # exactly meeting 3 and no started_at-driven conversation.
        fallback = _hybrid(
            db,
            context,
            "نشانه",
            vector=_query_vector("نشانه"),
            filters=SearchFilters(started_from=NOW - 31 * DAY, started_before=NOW - 29 * DAY),
            k=50,
        )
        assert {hit.entry_id for hit in fallback.hits} == set(_ids(world, 3))


def test_a5_none_and_empty_filters_keep_todays_results(client, email_outbox):
    world = _hworld(client, email_outbox, "hom95-a5")
    context = _owner(world)
    with _bound(world["workspace_id"]) as db:
        base_lexical = lexical_search(
            db,
            access=context.record_access,
            workspace_id=world["workspace_id"],
            query="نشانه",
            limit=100,
            now=NOW,
        )
        base_vector = STORE.nearest(
            db,
            access=context.record_access,
            workspace_id=world["workspace_id"],
            embedding_version=_version(),
            query=_query_vector("نشانه"),
            k=100,
            now=NOW,
        )
        for none_like in (None, SearchFilters()):
            lexical = lexical_search(
                db,
                access=context.record_access,
                workspace_id=world["workspace_id"],
                query="نشانه",
                limit=100,
                now=NOW,
                filters=none_like,
            )
            vector = STORE.nearest(
                db,
                access=context.record_access,
                workspace_id=world["workspace_id"],
                embedding_version=_version(),
                query=_query_vector("نشانه"),
                k=100,
                now=NOW,
                filters=none_like,
            )
            assert lexical == base_lexical
            assert vector == base_vector
        assert {hit.entry_id for hit in base_lexical} == set(_ids(world))


def test_filters_argument_type_is_checked_everywhere(client, email_outbox):
    world = _hworld(client, email_outbox, "hom95-filters-type")
    context = _owner(world)
    with _bound(world["workspace_id"]) as db:
        from backend.retrieval.evidence import (
            search_evidence_lexical,
            search_evidence_vector,
        )

        for bad in (7, "call", object()):
            with pytest.raises(ValueError):
                live_entries(
                    db,
                    access=_unrestricted(),
                    workspace_id=world["workspace_id"],
                    include_history=False,
                    filters=bad,
                )
            with pytest.raises(ValueError):
                lexical_search(
                    db,
                    access=_unrestricted(),
                    workspace_id=world["workspace_id"],
                    query="نشانه",
                    filters=bad,
                )
            with pytest.raises(ValueError):
                STORE.nearest(
                    db,
                    access=_unrestricted(),
                    workspace_id=world["workspace_id"],
                    embedding_version=_version(),
                    query=_query_vector("نشانه"),
                    filters=bad,
                )
            with pytest.raises(ValueError):
                STORE.score_entries(
                    db,
                    access=_unrestricted(),
                    workspace_id=world["workspace_id"],
                    embedding_version=_version(),
                    query=_query_vector("نشانه"),
                    entry_ids=_ids(world)[:1],
                    filters=bad,
                )
            with pytest.raises(ValueError):
                search_evidence_lexical(db, context=context, query="نشانه", now=NOW, filters=bad)
            with pytest.raises(ValueError):
                search_evidence_vector(
                    db,
                    context=context,
                    store=STORE,
                    embedding_version=_version(),
                    query_vector=_query_vector("نشانه"),
                    now=NOW,
                    filters=bad,
                )
            with pytest.raises(ValueError):
                _hybrid(db, context, "نشانه", filters=bad)


# --- A6: the ranking-aware vector window ----------------------------------------------------


def _window(db, world, *, k=12, candidate_limit, filters=None, access=None):
    return STORE.nearest(
        db,
        access=access if access is not None else _unrestricted(),
        workspace_id=world["workspace_id"],
        embedding_version=_version(),
        query=_query_vector("نشانه"),
        k=k,
        candidate_limit=candidate_limit,
        now=NOW,
        filters=filters,
    )


def _in_result_order(hits) -> bool:
    keys = [(-hit.score, hit.entry_id) for hit in hits]
    return keys == sorted(keys)


def test_a6_the_window_holds_the_newest_conversations(client, email_outbox):
    world, _plan = _meta_world(client, email_outbox, "hom95-a6")
    # Conversation times, newest first: 5 (NOW-1d), 4 (NOW-2d), 2 (NOW-4d),
    # 1, 0, 3.  window=5: meetings 5 and 4 (two entries each) and the LOWEST
    # entry id of meeting 2.
    expected = set(_ids(world, 5)) | set(_ids(world, 4)) | {min(_ids(world, 2))}
    with _bound(world["workspace_id"]) as db:
        windowed = _window(db, world, candidate_limit=5)
        assert {hit.entry_id for hit in windowed} == expected
        assert _in_result_order(windowed)
        # A smaller k keeps the best k of the same window (the heap).
        best_two = _window(db, world, k=2, candidate_limit=5)
        assert best_two == windowed[:2]
        # Equal conversation times break by entry id ascending: with meetings
        # 4 and 5 at the same instant, a window of three holds the three
        # lowest entry ids of those four entries.
        _set_meeting(world["setups"][5]["meeting_id"], started_at=NOW - 2 * DAY)
        tied = _window(db, world, candidate_limit=3)
        pool = sorted(set(_ids(world, 4)) | set(_ids(world, 5)))
        assert {hit.entry_id for hit in tied} == set(pool[:3])
        assert _in_result_order(tied)


def test_a6_only_usable_vectors_take_a_window_slot(client, email_outbox):
    """SPEC revision 1, clarification 3: a conversation with no vector of
    the asked version, a deleted one, another team's one, or one a filter
    removes takes no window slot."""
    world = _hworld(client, email_outbox, "hom95-a6-slots", meetings=6, embed_skip=(5,))
    # Newest first: 5 (no vectors), 4 (deleted), 3 (a voicemail), 2, 1, 0.
    for index in range(6):
        _set_meeting(world["setups"][index]["meeting_id"], started_at=NOW - (10 - index) * DAY)
    _set_meeting(world["setups"][4]["meeting_id"], deleted_at=NOW)
    _set_meeting(world["setups"][3]["meeting_id"], conversation_type="voicemail")
    with _bound(world["workspace_id"]) as db:
        unfiltered = _window(db, world, candidate_limit=4)
        assert {hit.entry_id for hit in unfiltered} == _expected_entries(world, (3, 2))
        calls_only = _window(
            db, world, candidate_limit=4, filters=SearchFilters(conversation_types={"call"})
        )
        assert {hit.entry_id for hit in calls_only} == _expected_entries(world, (2, 1))


def test_a6_another_teams_conversation_takes_no_window_slot(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom95-a6-team")
    workspace_id = tenant["workspace_id"]
    _open_membership_window(tenant["user_id"], workspace_id)
    with inspection_session_factory()() as db:
        db.execute(
            update(Workspace).where(Workspace.id == workspace_id).values(record_scope="TEAM")
        )
        team, other = (
            Team(workspace_id=workspace_id, name="Team one", slug="hom95-a6-one"),
            Team(workspace_id=workspace_id, name="Team two", slug="hom95-a6-two"),
        )
        db.add_all([team, other])
        db.flush()
        user = User(email="hom95-a6-sup@example.org", display_name="Sup")
        db.add(user)
        db.flush()
        db.add(
            Membership(
                workspace_id=workspace_id,
                user_id=user.id,
                role=WorkspaceRole.MEMBER,
                valid_from=NOW - timedelta(days=365),
            )
        )
        db.add(
            TeamAssignment(
                workspace_id=workspace_id,
                team_id=team.id,
                user_id=user.id,
                role=TeamRole.SUPERVISOR,
                assigned_by_user_id=tenant["user_id"],
                valid_from=NOW - timedelta(days=365),
            )
        )
        db.commit()
        team_id, other_id, user_id = team.id, other.id, user.id
    setups = []
    # Meeting 0 is the newest and another team's; meetings 1 and 2 are ours.
    for index, (owner, days) in enumerate(((other_id, 1), (team_id, 2), (team_id, 3))):
        setup = _seed_view(tenant, f"hom95-a6-team-m{index}", texts=_texts("a6team", index))
        _set_meeting(setup["meeting_id"], team_id=owner, started_at=NOW - days * DAY)
        _index(setup["text_view_id"], workspace_id)
        setups.append(setup)
    world = {"workspace_id": workspace_id, "setups": setups}
    _embed(workspace_id, _ids(world))
    supervisor = _context(user_id, workspace_id)
    with _bound(workspace_id) as db:
        windowed = _window(db, world, candidate_limit=2, access=supervisor.record_access)
        assert {hit.entry_id for hit in windowed} == set(_ids(world, 1))


def test_a6_small_scan_batches_give_the_same_result(client, email_outbox, monkeypatch):
    world, _plan = _meta_world(client, email_outbox, "hom95-a6-batch")
    with _bound(world["workspace_id"]) as db:
        whole = _window(db, world, k=5, candidate_limit=9)
        monkeypatch.setattr(vector_store_module, "SCAN_BATCH", 2)
        batched = _window(db, world, k=5, candidate_limit=9)
    assert batched == whole and len(whole) == 5


# --- A7: score_entries ------------------------------------------------------------------------


def test_a7_score_entries_scores_only_authorized_live_filtered_ids(client, email_outbox):
    world_a = _hworld(client, email_outbox, "hom95-a7-a", meetings=4, embed_skip=(3,))
    world_b = _hworld(client, email_outbox, "hom95-a7-b", meetings=1)
    workspace_a = world_a["workspace_id"]
    foreign = _ids(world_b)
    doomed = _ids(world_a, 0)
    _set_meeting(world_a["setups"][0]["meeting_id"], deleted_at=NOW)
    # Meetings 1 and 2: embedded and live.  Meeting 3 is live but vector-less,
    # meeting 0 is deleted, and world_b is another tenant.
    live_ids = _ids(world_a, 1) + _ids(world_a, 2)

    with _bound(workspace_a) as db:
        asked = live_ids + doomed + foreign + [str(uuid.uuid4()) for _ in range(3)]
        scored = STORE.score_entries(
            db,
            access=_unrestricted(),
            workspace_id=workspace_a,
            embedding_version=_version(),
            query=_query_vector("نشانه"),
            entry_ids=asked,
            now=NOW,
        )
        assert {hit.entry_id for hit in scored} == set(live_ids)
        assert [(-hit.score, hit.entry_id) for hit in scored] == sorted(
            (-hit.score, hit.entry_id) for hit in scored
        )
        # An id under another live filter is silently absent too: the seeded
        # meetings are all calls (the column default), so a voicemail filter
        # keeps none of them.
        filtered = STORE.score_entries(
            db,
            access=_unrestricted(),
            workspace_id=workspace_a,
            embedding_version=_version(),
            query=_query_vector("نشانه"),
            entry_ids=live_ids,
            now=NOW,
            filters=SearchFilters(conversation_types={"voicemail"}),
        )
        assert filtered == []
        # A version without vectors here gives nothing.
        empty = STORE.score_entries(
            db,
            access=_unrestricted(),
            workspace_id=workspace_a,
            embedding_version="f" * 64,
            query=_query_vector("نشانه"),
            entry_ids=live_ids,
            now=NOW,
        )
        assert empty == []
        # Shape defects are ValueErrors: a str, a set, any other type, a
        # non-str item, 101 distinct ids (SPEC revision 1, clarification 2).
        for bad in ("ids", 7, {"a": 1}, set(live_ids), frozenset(live_ids), None):
            with pytest.raises(ValueError):
                STORE.score_entries(
                    db,
                    access=_unrestricted(),
                    workspace_id=workspace_a,
                    embedding_version=_version(),
                    query=_query_vector("نشانه"),
                    entry_ids=bad,
                )
        with pytest.raises(ValueError):
            STORE.score_entries(
                db,
                access=_unrestricted(),
                workspace_id=workspace_a,
                embedding_version=_version(),
                query=_query_vector("نشانه"),
                entry_ids=[str(uuid.uuid4()) for _ in range(101)],
            )
        with pytest.raises(ValueError):
            STORE.score_entries(
                db,
                access=_unrestricted(),
                workspace_id=workspace_a,
                embedding_version=_version(),
                query=_query_vector("نشانه"),
                entry_ids=live_ids + [7],
            )
        # 100 distinct ids is the edge; duplicates on top of them are accepted
        # and collapse; a tuple is accepted like a list.
        hundred = live_ids + [str(uuid.uuid4()) for _ in range(100 - len(live_ids))]
        edge = STORE.score_entries(
            db,
            access=_unrestricted(),
            workspace_id=workspace_a,
            embedding_version=_version(),
            query=_query_vector("نشانه"),
            entry_ids=tuple(hundred + hundred[:30]),
            now=NOW,
        )
        assert sorted(hit.entry_id for hit in edge) == sorted(live_ids)
        # An id the database cannot take (NUL, a lone surrogate) is just absent.
        odd = STORE.score_entries(
            db,
            access=_unrestricted(),
            workspace_id=workspace_a,
            embedding_version=_version(),
            query=_query_vector("نشانه"),
            entry_ids=["bad\x00id", "\ud800", live_ids[0]],
            now=NOW,
        )
        assert [hit.entry_id for hit in odd] == [live_ids[0]]
        # The query-vector checks of nearest: empty, not finite, wrong length.
        for vector in ([], [float("nan")] * DIMENSIONS, [0.5] * (DIMENSIONS + 1)):
            with pytest.raises(ValueError):
                STORE.score_entries(
                    db,
                    access=_unrestricted(),
                    workspace_id=workspace_a,
                    embedding_version=_version(),
                    query=vector,
                    entry_ids=live_ids,
                    now=NOW,
                )


def test_a7_score_entries_respects_the_team_acl(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom95-a7-team")
    workspace_id = tenant["workspace_id"]
    _open_membership_window(tenant["user_id"], workspace_id)
    with inspection_session_factory()() as db:
        db.execute(
            update(Workspace).where(Workspace.id == workspace_id).values(record_scope="TEAM")
        )
        teams = {}
        for name in ("one", "two"):
            team = Team(workspace_id=workspace_id, name=f"Team {name}", slug=f"hom95-a7-{name}")
            db.add(team)
            db.flush()
            teams[name] = team.id
        user = User(email="hom95-a7-sup@example.com", display_name="Sup")
        db.add(user)
        db.flush()
        db.add(
            Membership(
                workspace_id=workspace_id,
                user_id=user.id,
                role=WorkspaceRole.MEMBER,
                valid_from=NOW - timedelta(days=365),
            )
        )
        db.add(
            TeamAssignment(
                workspace_id=workspace_id,
                team_id=teams["one"],
                user_id=user.id,
                role=TeamRole.SUPERVISOR,
                assigned_by_user_id=tenant["user_id"],
                valid_from=NOW - timedelta(days=365),
            )
        )
        db.commit()
    setups = []
    for index in range(2):
        setup = _seed_view(tenant, f"hom95-a7-team-m{index}", texts=_texts("team", index))
        _set_meeting(setup["meeting_id"], team_id=teams["one" if index == 0 else "two"])
        _index(setup["text_view_id"], workspace_id)
        setups.append(setup)
    _embed(workspace_id, [entry.id for setup in setups for entry in _entries(setup["meeting_id"])])
    supervisor = _context(user.id, workspace_id)

    with _bound(workspace_id) as db:
        scored = STORE.score_entries(
            db,
            access=supervisor.record_access,
            workspace_id=workspace_id,
            embedding_version=_version(),
            query=_query_vector("نشانه"),
            entry_ids=[entry.id for setup in setups for entry in _entries(setup["meeting_id"])],
            now=NOW,
        )
        assert {hit.entry_id for hit in scored} == set(_ids({"setups": setups[:1]}, 0))


# --- A8: fusion --------------------------------------------------------------------------------


def test_a8_fusion_follows_rrf_with_shared_ranks(client, email_outbox):
    world = _hworld(client, email_outbox, "hom95-a8", meetings=6)
    context = _owner(world)
    query = "نشانه hom95-a83 گفتگو"  # meeting 3's texts, both entries
    vector = _query_vector("نشانه hom95-a83 گفتگو الف")
    with _bound(world["workspace_id"]) as db:
        expected = _expected_fusion(db, context, query, vector=vector, vector_candidates=3)
        outcome = _hybrid(
            db,
            context,
            query,
            vector=vector,
            vector_candidates=3,
            lexical_candidates=50,
            k=50,
        )
        assert outcome.state == "answered" and outcome.degraded == ()
        assert [hit.entry_id for hit in outcome.hits] == expected["order"]
        for hit in outcome.hits:
            assert hit.lexical_rank == expected["lexical_ranks"].get(hit.entry_id)
            assert hit.vector_rank == expected["vector_ranks"].get(hit.entry_id)
            assert hit.fused_score == pytest.approx(expected["fused"][hit.entry_id])
            assert hit.meeting_id == expected["by_id"][hit.entry_id].meeting_id
            assert hit.historical is False
        # Equal lexical scores share one rank: on SQLite every hit scores the
        # same, so every lexical rank is 1 (the shared-rank rule of 3.5 step 6).
        if db.get_bind().dialect.name == "sqlite":
            assert all(
                hit.lexical_rank == 1 for hit in outcome.hits if hit.lexical_rank is not None
            )
        # A small vector_candidates window forced score_entries into the union:
        # the lexical match that nearest skipped still carries a vector rank.
        rescued = [hit for hit in outcome.hits if hit.entry_id == _ids(world, 3)[1]]
        assert rescued and rescued[0].vector_rank is not None


def test_a8_lexical_only_works_and_mixed_vector_arguments_are_refused(client, email_outbox):
    world = _hworld(client, email_outbox, "hom95-a8b")
    context = _owner(world)
    with _bound(world["workspace_id"]) as db:
        lexical_only = _hybrid(db, context, "نشانه hom95-a8b1", k=50)
        assert lexical_only.state == "answered"
        assert {hit.entry_id for hit in lexical_only.hits} == set(_ids(world, 1))
        assert all(hit.vector_rank is None for hit in lexical_only.hits)
        # A query that normalizes to nothing gives no lexical list at all.
        blank = _hybrid(db, context, "   ", k=10)
        assert blank.state == "answered" and blank.hits == ()
        # Mixed vector arguments are ValueError before any SQL.
        with pytest.raises(ValueError):
            search_evidence_hybrid(
                db,
                context=context,
                query="نشانه",
                now=NOW,
                store=STORE,
                embedding_version=None,
                query_vector=_query_vector("نشانه"),
            )
        with pytest.raises(ValueError):
            search_evidence_hybrid(
                db,
                context=context,
                query="نشانه",
                now=NOW,
                store=None,
                embedding_version=_version(),
                query_vector=_query_vector("نشانه"),
            )
        with pytest.raises(ValueError):
            search_evidence_hybrid(
                db,
                context=context,
                query="نشانه",
                now=NOW,
                store=None,
                embedding_version=None,
                query_vector=[0.5] * DIMENSIONS,
            )


# --- A9: hybrid bounds ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"k": 0}, id="k-low"),
        pytest.param({"k": 51}, id="k-high"),
        pytest.param({"k": True}, id="k-bool"),
        pytest.param({"lexical_candidates": 0}, id="lexical-low"),
        pytest.param({"lexical_candidates": 101}, id="lexical-high"),
        pytest.param({"lexical_candidates": True}, id="lexical-bool"),
        pytest.param({"vector_candidates": 0}, id="vector-low"),
        pytest.param({"vector_candidates": 101}, id="vector-high"),
        pytest.param({"vector_candidates": True}, id="vector-bool"),
        pytest.param({"candidate_limit": 0}, id="window-low"),
        pytest.param({"candidate_limit": 5001}, id="window-high"),
        pytest.param({"candidate_limit": True}, id="window-bool"),
        pytest.param({"rerank_candidates": 0}, id="rerank-low"),
        pytest.param({"rerank_candidates": 51}, id="rerank-high"),
        pytest.param({"rerank_candidates": True}, id="rerank-bool"),
        pytest.param({"time_budget_ms": 49}, id="budget-low"),
        pytest.param({"time_budget_ms": 10001}, id="budget-high"),
        pytest.param({"time_budget_ms": True}, id="budget-bool"),
    ],
)
def test_a9_out_of_range_bounds_are_value_errors(client, email_outbox, kwargs):
    world = _hworld(client, email_outbox, "hom95-a9-bad")
    context = _owner(world)
    with _bound(world["workspace_id"]) as db:
        with pytest.raises(ValueError):
            _hybrid(db, context, "نشانه", vector=_query_vector("نشانه"), **kwargs)


def test_a9_edge_values_are_accepted(client, email_outbox):
    world = _hworld(client, email_outbox, "hom95-a9-edge")
    context = _owner(world)
    with _bound(world["workspace_id"]) as db:
        low = _hybrid(
            db,
            context,
            "نشانه",
            vector=_query_vector("نشانه"),
            k=1,
            lexical_candidates=1,
            vector_candidates=1,
            candidate_limit=1,
            rerank_candidates=1,
            time_budget_ms=50,
        )
        assert low.state == "answered" and len(low.hits) == 1
        high = _hybrid(
            db,
            context,
            "نشانه",
            vector=_query_vector("نشانه"),
            k=50,
            lexical_candidates=100,
            vector_candidates=100,
            candidate_limit=5000,
            rerank_candidates=50,
            time_budget_ms=10000,
        )
        assert high.state == "answered" and high.degraded == ()
        # A reranker that is not None and not rerank-shaped is a ValueError.
        with pytest.raises(ValueError):
            _hybrid(db, context, "نشانه", reranker=object())
        with pytest.raises(ValueError):
            _hybrid(db, context, "نشانه", reranker="nope")


# --- A10: the denied caller ----------------------------------------------------------------------


def test_a10_denied_caller_is_answered_denied_without_index_sql(client, email_outbox):
    world = _hworld(client, email_outbox, "hom95-a10")
    workspace_id = world["workspace_id"]
    context = _owner(world)
    with inspection_session_factory()() as db:
        db.execute(
            update(Membership)
            .where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == world["tenant"]["user_id"],
            )
            .values(is_active=False)
        )
        db.commit()
    with _bound(workspace_id) as db:
        with _captured_statements(db) as statements:
            for broken in (
                lambda: _hybrid(db, context, "x" * 1001),
                lambda: _hybrid(db, context, "نشانه", k=0),
                lambda: _hybrid(
                    db,
                    context,
                    "نشانه",
                    store=STORE,
                    embedding_version=None,
                    query_vector=_query_vector("نشانه"),
                ),
                lambda: _hybrid(db, context, "نشانه", filters=7),
            ):
                outcome = broken()
                assert isinstance(outcome, HybridOutcome)
                assert (outcome.state, outcome.hits, outcome.degraded) == (
                    "denied",
                    (),
                    (),
                )
        assert _touched_index(statements) == []


# --- A11: authorization under hybrid --------------------------------------------------------------


def _denied_worlds(client, email_outbox, label):
    """Workspace A (team-restricted) with one reachable meeting and five
    unreachable flavors, plus workspace B with the same text."""
    tenant = _tenant(client, email_outbox, f"{label}-a")
    workspace_id = tenant["workspace_id"]
    _open_membership_window(tenant["user_id"], workspace_id)
    with inspection_session_factory()() as db:
        db.execute(
            update(Workspace).where(Workspace.id == workspace_id).values(record_scope="TEAM")
        )
        team = Team(workspace_id=workspace_id, name="Team one", slug=f"{label}-one")
        other_team = Team(workspace_id=workspace_id, name="Team two", slug=f"{label}-two")
        db.add_all([team, other_team])
        db.flush()
        user = User(email=f"{label}-sup@example.com", display_name="Sup")
        db.add(user)
        db.flush()
        db.add(
            Membership(
                workspace_id=workspace_id,
                user_id=user.id,
                role=WorkspaceRole.MEMBER,
                valid_from=NOW - timedelta(days=365),
            )
        )
        db.add(
            TeamAssignment(
                workspace_id=workspace_id,
                team_id=team.id,
                user_id=user.id,
                role=TeamRole.SUPERVISOR,
                assigned_by_user_id=tenant["user_id"],
                valid_from=NOW - timedelta(days=365),
            )
        )
        db.commit()
        team_id, other_team_id = team.id, other_team.id
    setups = {}

    def seeded(key, *, team_ok=True, redacted=False):
        setup = _seed_view(
            tenant, f"{label}-{key}", texts=(f"نشانه {key} گفتگو",), redacted=redacted
        )
        if team_ok:
            _set_meeting(setup["meeting_id"], team_id=team_id)
        else:
            _set_meeting(setup["meeting_id"], team_id=other_team_id)
        _index(setup["text_view_id"], workspace_id)
        setups[key] = setup
        return setup

    seeded("alive")
    seeded("otherteam", team_ok=False)
    seeded("deleted")
    _set_meeting(setups["deleted"]["meeting_id"], deleted_at=NOW)
    seeded("expired")
    _set_meeting(setups["expired"]["meeting_id"], retention_expires_at=NOW - timedelta(seconds=1))
    superseded = seeded("superseded")
    with inspection_session_factory()() as db:
        db.execute(
            update(TranscriptRevision)
            .where(TranscriptRevision.id == superseded["revision_id"])
            .values(is_current=False, superseded_at=NOW)
        )
        db.commit()
    newer = _seed_view(
        tenant,
        f"{label}-superseded-v2",
        texts=("نشانه superseded گفتگو جدید",),
        revision_number=2,
        meeting_id=superseded["meeting_id"],
        reference_hours=1.0,
    )
    _index(newer["text_view_id"], workspace_id)
    setups["superseded-current"] = newer
    seeded("oldpolicy", redacted=True)
    _embed(
        workspace_id,
        [entry.id for setup in setups.values() for entry in _entries(setup["meeting_id"])],
    )
    tenant_b = _tenant(client, email_outbox, f"{label}-b")
    _open_membership_window(tenant_b["user_id"], tenant_b["workspace_id"])
    setup_b = _seed_view(tenant_b, f"{label}-b0", texts=("نشانه alive گفتگو",))
    _index(setup_b["text_view_id"], tenant_b["workspace_id"])
    _embed(tenant_b["workspace_id"], [entry.id for entry in _entries(setup_b["meeting_id"])])
    return {
        "workspace_id": workspace_id,
        "supervisor": _context(user.id, workspace_id),
        "authorized_ids": set(_ids({"setups": [setups["alive"], newer]})),
        "setups": setups,
        "b_ids": [entry.id for entry in _entries(setup_b["meeting_id"])],
    }


def test_a11_hybrid_never_returns_or_reranks_an_unauthorized_entry(client, email_outbox):
    worlds = _denied_worlds(client, email_outbox, "hom95-a11")
    context = worlds["supervisor"]
    reranker = FakeReranker()
    with _bound(worlds["workspace_id"]) as db:
        for outcome in (
            _hybrid(
                db,
                context,
                "نشانه",
                vector=_query_vector("نشانه alive گفتگو"),
                k=50,
            ),
            _hybrid(
                db,
                context,
                "نشانه",
                vector=_query_vector("نشانه alive گفتگو"),
                k=50,
                reranker=reranker,
            ),
        ):
            assert outcome.state == "answered"
            returned = {hit.entry_id for hit in outcome.hits}
            assert returned <= worlds["authorized_ids"]
            assert returned
        assert reranker.calls, "the reranker saw the candidates"
        for call in reranker.calls:
            assert {c.entry_id for c in call["candidates"]} <= worlds["authorized_ids"]
            for candidate in call["candidates"]:
                assert isinstance(candidate, RerankCandidate)
        # History stays hidden unless it is asked for, and then it is labeled.
        history = _hybrid(
            db,
            context,
            "نشانه",
            vector=_query_vector("نشانه alive گفتگو"),
            k=50,
            include_history=True,
        )
        old_ids = {
            entry.id
            for entry in _entries(worlds["setups"]["superseded"]["meeting_id"])
            if entry.superseded_at is not None
        }
        assert old_ids <= {hit.entry_id for hit in history.hits}
        assert all(hit.historical for hit in history.hits if hit.entry_id in old_ids)


# --- A12: the reranker and the time budget --------------------------------------------------------


class BrokenLength(Sequence):
    """A sequence whose ``__len__`` raises: a reranker output that cannot even
    be counted."""

    def __len__(self) -> int:
        raise RuntimeError("no length")

    def __getitem__(self, index):
        raise IndexError(index)


class ManualClock:
    """A clock the test (or a fake store or reranker) moves by hand."""

    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class SpyStore:
    """The real table store, counting calls and running a hook before each."""

    def __init__(self, before=None):
        self.before = before
        self.calls: list[str] = []

    def nearest(self, db, **kwargs):
        self.calls.append("nearest")
        if self.before is not None:
            self.before()
        return STORE.nearest(db, **kwargs)

    def score_entries(self, db, **kwargs):
        self.calls.append("score_entries")
        return STORE.score_entries(db, **kwargs)


def _with_store(db, context, query, store, **kwargs):
    kwargs.setdefault("clock", _frozen_clock)
    return search_evidence_hybrid(
        db,
        context=context,
        query=query,
        now=NOW,
        store=store,
        embedding_version=_version(),
        query_vector=_query_vector(query),
        **kwargs,
    )


def test_a12_the_reranker_reorders_only_the_first_block(client, email_outbox):
    world = _hworld(client, email_outbox, "hom95-a12a")
    context = _owner(world)
    with _bound(world["workspace_id"]) as db:
        fused = _hybrid(db, context, "نشانه", vector=_query_vector("نشانه"), k=50)
        fused_order = [hit.entry_id for hit in fused.hits]
        fused_scores = {hit.entry_id: hit.fused_score for hit in fused.hits}
        assert len(fused_order) == 12
        # Rising scores in candidate order: the last candidate scores best,
        # so the reranked block is the fused head reversed.
        reranker = FakeReranker(
            scores=lambda candidates: [float(i) for i in range(len(candidates))]
        )
        reranked = _hybrid(
            db,
            context,
            "نشانه",
            vector=_query_vector("نشانه"),
            k=50,
            reranker=reranker,
            rerank_candidates=4,
        )
        assert reranked.degraded == ()
        assert [hit.entry_id for hit in reranked.hits] == (
            list(reversed(fused_order[:4])) + fused_order[4:]
        )
        for hit in reranked.hits:
            assert hit.fused_score == fused_scores[hit.entry_id]
            if hit.entry_id in fused_order[:4]:
                assert hit.score == float(fused_order.index(hit.entry_id))
            else:
                assert hit.score == fused_scores[hit.entry_id]
        # It saw the stripped query and only the first four, in fused order,
        # as RerankCandidate objects with the entry's own text, and an int
        # budget of at least 1 ms.
        (call,) = reranker.calls
        assert call["query"] == "نشانه"
        assert [c.entry_id for c in call["candidates"]] == fused_order[:4]
        assert all(isinstance(c, RerankCandidate) for c in call["candidates"])
        texts = {entry.id: entry.text for entry in _entries_of(world)}
        assert all(c.text == texts[c.entry_id] for c in call["candidates"])
        assert type(call["timeout_ms"]) is int and 1 <= call["timeout_ms"] <= 2000
        # Equal rerank scores fall back to fused_score, then entry id: the
        # fused order itself.
        flat = _hybrid(
            db,
            context,
            "نشانه",
            vector=_query_vector("نشانه"),
            k=50,
            reranker=FakeReranker(scores=lambda candidates: [7.0] * len(candidates)),
            rerank_candidates=50,
        )
        assert [hit.entry_id for hit in flat.hits] == fused_order
        assert all(hit.score == 7.0 for hit in flat.hits)
        # k cuts after the rerank.
        cut = _hybrid(
            db,
            context,
            "نشانه",
            vector=_query_vector("نشانه"),
            k=2,
            reranker=FakeReranker(
                scores=lambda candidates: [float(i) for i in range(len(candidates))]
            ),
            rerank_candidates=4,
        )
        assert [hit.entry_id for hit in cut.hits] == list(reversed(fused_order[:4]))[:2]


def _entries_of(world):
    return [entry for setup in world["setups"] for entry in _entries(setup["meeting_id"])]


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda: FakeReranker(error=RuntimeError("boom")), id="raises"),
        pytest.param(lambda: FakeReranker(scores=lambda c: [0.5]), id="wrong-count"),
        pytest.param(lambda: FakeReranker(scores=lambda c: [float("nan")] * len(c)), id="nan"),
        pytest.param(lambda: FakeReranker(scores=lambda c: [float("inf")] * len(c)), id="inf"),
        pytest.param(lambda: FakeReranker(scores=lambda c: ["high"] * len(c)), id="non-number"),
        pytest.param(lambda: FakeReranker(scores=lambda c: [True] * len(c)), id="bool"),
        pytest.param(lambda: FakeReranker(scores=lambda c: None), id="none"),
        pytest.param(lambda: FakeReranker(scores=lambda c: [10**400] * len(c)), id="huge-int"),
        pytest.param(lambda: FakeReranker(scores=lambda c: BrokenLength()), id="len-raises"),
    ],
)
def test_a12_a_failing_reranker_degrades_to_fused_order(client, email_outbox, make):
    world = _hworld(client, email_outbox, "hom95-a12bad")
    context = _owner(world)
    with _bound(world["workspace_id"]) as db:
        fused = _hybrid(db, context, "نشانه", vector=_query_vector("نشانه"), k=50)
        outcome = _hybrid(
            db, context, "نشانه", vector=_query_vector("نشانه"), k=50, reranker=make()
        )
        assert outcome.state == "answered"
        assert outcome.degraded == ("rerank_failed",)
        assert outcome.hits == fused.hits  # fused order, fused scores


def test_a12_the_time_budget_names_every_shortcut(client, email_outbox):
    world = _hworld(client, email_outbox, "hom95-a12time")
    context = _owner(world)
    with _bound(world["workspace_id"]) as db:
        fused = _hybrid(db, context, "نشانه", vector=_query_vector("نشانه"), k=50)
        lexical_only = _hybrid(db, context, "نشانه", k=50)
        assert fused.hits != lexical_only.hits

        # The deadline passes before step 5 (the clock jumps right after the
        # deadline is taken): the store and the reranker are never called.
        clock = ManualClock()
        store = SpyStore()
        reranker = FakeReranker()

        def late_clock() -> float:
            value = clock.now
            clock.now = 100.0
            return value

        skipped = _with_store(
            db, context, "نشانه", store, k=50, reranker=reranker, clock=late_clock
        )
        assert skipped.degraded == ("vector_skipped_time", "rerank_skipped_time")
        assert store.calls == [] and reranker.calls == []
        assert skipped.hits == lexical_only.hits

        # Without a reranker the same clock only skips the vector stage.
        clock.now = 0.0
        no_rerank = _with_store(db, context, "نشانه", SpyStore(), k=50, clock=late_clock)
        assert no_rerank.degraded == ("vector_skipped_time",)
        assert no_rerank.hits == lexical_only.hits

        # The deadline passes during the vector stage: the rerank is skipped
        # and never called, the vector stage still counts.
        clock = ManualClock()
        reranker = FakeReranker()
        slow_store = SpyStore(before=lambda: setattr(clock, "now", 100.0))
        late = _with_store(db, context, "نشانه", slow_store, k=50, reranker=reranker, clock=clock)
        assert late.degraded == ("rerank_skipped_time",)
        assert reranker.calls == []
        assert late.hits == fused.hits

        # The reranker returns after the deadline: its scores are discarded.
        clock = ManualClock()

        def slow_scores(candidates):
            clock.now = 100.0
            return [float(i) for i in range(len(candidates))]

        timed_out = _with_store(
            db,
            context,
            "نشانه",
            SpyStore(),
            k=50,
            reranker=FakeReranker(scores=slow_scores),
            clock=clock,
        )
        assert timed_out.degraded == ("rerank_timeout",)
        assert timed_out.hits == fused.hits

        # Half a millisecond left: the reranker still gets an int budget of 1.
        clock = ManualClock()
        reranker = FakeReranker()
        nearly = SpyStore(before=lambda: setattr(clock, "now", 1.9995))
        tight = _with_store(db, context, "نشانه", nearly, k=50, reranker=reranker, clock=clock)
        assert tight.degraded == ()
        assert reranker.calls[0]["timeout_ms"] == 1
        assert type(reranker.calls[0]["timeout_ms"]) is int


def test_a12_an_entry_gone_before_the_rerank_is_neither_reranked_nor_returned(client, email_outbox):
    """SPEC revision 1, clarification 5: the rerank texts are re-read under
    the live joins; an entry they no longer return drops out."""
    world = _hworld(client, email_outbox, "hom95-a12gone", meetings=3)
    context = _owner(world)
    gone = set(_ids(world, 1))
    reranker = FakeReranker()
    # The meeting is deleted while the vector stage runs: after the lexical
    # stage listed its entries, before the rerank texts are read.
    store = SpyStore(before=lambda: _set_meeting(world["setups"][1]["meeting_id"], deleted_at=NOW))
    with _bound(world["workspace_id"]) as db:
        outcome = _with_store(
            db, context, "نشانه", store, k=50, reranker=reranker, rerank_candidates=50
        )
    assert outcome.degraded == ()
    seen = {candidate.entry_id for candidate in reranker.calls[0]["candidates"]}
    assert seen == set(_ids(world)) - gone
    assert {hit.entry_id for hit in outcome.hits} == set(_ids(world)) - gone


# --- shape and ordering contracts ----------------------------------------------------------------


def test_result_types_keep_the_spec_field_order():
    """SPEC revision 1, clarification 7."""
    assert [field.name for field in dataclasses.fields(HybridHit)] == [
        "entry_id",
        "meeting_id",
        "score",
        "fused_score",
        "lexical_rank",
        "vector_rank",
        "historical",
    ]
    assert [field.name for field in dataclasses.fields(HybridOutcome)] == [
        "state",
        "hits",
        "degraded",
    ]
    assert [field.name for field in dataclasses.fields(RerankCandidate)] == ["entry_id", "text"]
    assert "filters" not in inspect.signature(fetch_evidence).parameters


def test_every_argument_defect_is_refused_before_any_index_sql(client, email_outbox):
    world = _hworld(client, email_outbox, "hom95-args")
    context = _owner(world)
    vector = _query_vector("نشانه")
    defects = [
        {"query": 7},
        {"query": "bad\x00query"},
        {"query": "x" * 1001},
        {"query": " ".join(["w"] * 33)},
        {"language": "xx"},
        {"k": 0},
        {"filters": "call"},
        {"store": STORE, "embedding_version": _version()},
        {"store": STORE, "embedding_version": _version(), "query_vector": []},
        {"store": STORE, "embedding_version": _version(), "query_vector": [float("nan")] * 8},
        {"reranker": object()},
        {"reranker": FakeReranker(), "rerank_candidates": 51},
    ]
    with _bound(world["workspace_id"]) as db:
        for defect in defects:
            arguments = {"query": "نشانه", **defect}
            with _captured_statements(db) as statements:
                with pytest.raises(ValueError):
                    search_evidence_hybrid(db, context=context, now=NOW, **arguments)
            assert _touched_index(statements) == [], defect
        # A query of exactly 1000 characters and 32 words is accepted.
        assert _hybrid(db, context, " ".join(["w"] * 32)).state == "answered"
        assert _hybrid(db, context, "x" * 1000, vector=vector).state == "answered"
