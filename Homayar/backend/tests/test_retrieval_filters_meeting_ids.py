"""HOM-98 (SPEC 2.1): ``SearchFilters.meeting_ids`` narrows every search entry point.

The validation of the field (every defect a ``ValueError``), the allow-controls
(an all-``None`` filter filters nothing), and the narrowing itself on the three
search entry points: lexical, vector and hybrid.  The narrowing runs inside the
candidate SQL, before scoring and before ``LIMIT`` (ADR 0080 D4, ADR 0084 D4),
so a hit outside the asked conversations is impossible, and a made-up
conversation id gives zero hits without an error.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from backend.database.saas_models import Meeting
from backend.retrieval import filters as filters_module
from backend.retrieval.evidence import search_evidence_lexical, search_evidence_vector
from backend.retrieval.filters import CONVERSATION_TYPES, MAX_FILTER_MEETING_IDS, SearchFilters
from backend.retrieval.hybrid import search_evidence_hybrid
from backend.retrieval.vector_store import TableVectorStore
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import _tenant
from backend.tests.test_retrieval_evidence import _bound, _context, _open_membership_window
from backend.tests.test_retrieval_index import (
    FakeEmbeddingProvider,
    _embed,
    _entries,
    _index,
    _seed_view,
    _version,
)

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
MARKER = "نشانه"
STORE = TableVectorStore()
MEETING_ID = "0b6f0000-0000-4000-8000-000000000000"


def _texts(index: int) -> tuple[str, str]:
    # One shared token (so a lexical query reaches every entry) and one number
    # per conversation (so the conversations are told apart).
    return (f"{MARKER} گفتگو {index} الف", f"{MARKER} گفتگو {index} ب")


@pytest.fixture
def world(client, email_outbox):
    """Three indexed conversations of one workspace, every entry embedded."""
    tenant = _tenant(client, email_outbox, "hom98-meeting-ids")
    _open_membership_window(tenant["user_id"], tenant["workspace_id"])
    setups = []
    for index in range(3):
        setup = _seed_view(tenant, f"hom98-mi-{index}", texts=_texts(index))
        _index(setup["text_view_id"], tenant["workspace_id"])
        setups.append(setup)
    _embed(
        tenant["workspace_id"],
        [entry.id for setup in setups for entry in _entries(setup["meeting_id"])],
    )
    return {"tenant": tenant, "workspace_id": tenant["workspace_id"], "setups": setups}


def _entry_ids(world, index: int | None = None) -> set[str]:
    setups = world["setups"] if index is None else [world["setups"][index]]
    return {entry.id for setup in setups for entry in _entries(setup["meeting_id"])}


def _meeting_ids(world, *indexes: int) -> frozenset[str]:
    return frozenset(world["setups"][index]["meeting_id"] for index in indexes)


def _query_vector(text: str) -> list[float]:
    return FakeEmbeddingProvider().query_for(text)


def _frozen_clock() -> float:
    # No stage of the hybrid search is skipped for time, however slow the runner is.
    return 0.0


# --- validation, no database ----------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(MEETING_ID, id="bare-str"),
        pytest.param(7, id="int"),
        pytest.param([], id="empty-list"),
        pytest.param((), id="empty-tuple"),
        pytest.param(set(), id="empty-set"),
        pytest.param(frozenset(), id="empty-frozenset"),
        pytest.param([7], id="int-item"),
        pytest.param([MEETING_ID, None], id="none-item"),
        pytest.param([b"0b6f"], id="bytes-item"),
        pytest.param([""], id="empty-id"),
        pytest.param(["x" * 65], id="65-chars"),
        pytest.param(["a\x00b"], id="nul"),
        pytest.param(["\ud800"], id="lone-surrogate"),
        pytest.param(iter([MEETING_ID]), id="iterator"),
    ],
)
def test_every_defect_is_a_value_error(value):
    with pytest.raises(ValueError):
        SearchFilters(meeting_ids=value)


def test_more_than_the_bound_of_distinct_ids_is_a_defect():
    ids = {f"{index:05d}" for index in range(MAX_FILTER_MEETING_IDS + 1)}
    with pytest.raises(ValueError):
        SearchFilters(meeting_ids=ids)


def test_the_bound_of_distinct_ids_is_accepted():
    ids = {f"{index:05d}" for index in range(MAX_FILTER_MEETING_IDS)}
    made = SearchFilters(meeting_ids=ids)
    assert made.meeting_ids == ids


@pytest.mark.parametrize(
    "value",
    [
        pytest.param([MEETING_ID], id="list"),
        pytest.param((MEETING_ID, "b"), id="tuple"),
        pytest.param({MEETING_ID}, id="set"),
        pytest.param(frozenset({MEETING_ID, "b"}), id="frozenset"),
        pytest.param([MEETING_ID, MEETING_ID], id="duplicates"),
        pytest.param(["x" * 64], id="64-chars"),
    ],
)
def test_every_spelling_is_stored_as_a_frozenset(value):
    made = SearchFilters(meeting_ids=value)
    assert isinstance(made.meeting_ids, frozenset)
    assert made.meeting_ids == frozenset(value)


def test_the_allow_controls():
    empty = SearchFilters()
    names = [field.name for field in dataclasses.fields(SearchFilters)]
    assert names[-1] == "meeting_ids"  # the field is added last
    assert all(getattr(empty, name) is None for name in names)


def test_the_constant_and_the_export():
    assert MAX_FILTER_MEETING_IDS == 20000
    assert "MAX_FILTER_MEETING_IDS" in filters_module.__all__


def test_the_condition_is_an_in_with_bound_parameters_in_a_fixed_order():
    query = select(Meeting.id)
    made = filters_module.apply_search_filters(
        query, SearchFilters(meeting_ids={"b", "a"}), dialect_name="sqlite"
    )
    same = filters_module.apply_search_filters(
        query, SearchFilters(meeting_ids=frozenset({"a", "b"})), dialect_name="sqlite"
    )
    assert "IN" in str(made).upper()
    assert str(made) == str(same)  # the same set always builds the same statement
    # The ids travel as bound parameters (an expanding IN), never as SQL text.
    bound = [value for item in made.compile().params.values() for value in item]
    assert sorted(bound) == ["a", "b"]


# --- narrowing on every entry point ---------------------------------------------------------


def test_lexical_narrows_to_the_asked_conversations(world):
    context = _context(world["tenant"]["user_id"], world["workspace_id"])
    with _bound(world["workspace_id"]) as db:
        narrowed = search_evidence_lexical(
            db,
            context=context,
            query=MARKER,
            now=NOW,
            filters=SearchFilters(meeting_ids=_meeting_ids(world, 0)),
        )
        every = search_evidence_lexical(db, context=context, query=MARKER, now=NOW)
    assert narrowed.state == "answered"
    assert {hit.entry_id for hit in narrowed.hits} == _entry_ids(world, 0)
    assert every.state == "answered"
    assert {hit.entry_id for hit in every.hits} == _entry_ids(world)


def test_vector_narrows_to_the_asked_conversations(world):
    context = _context(world["tenant"]["user_id"], world["workspace_id"])
    with _bound(world["workspace_id"]) as db:
        narrowed = search_evidence_vector(
            db,
            context=context,
            store=STORE,
            embedding_version=_version(),
            query_vector=_query_vector(f"{MARKER} گفتگو 1"),
            now=NOW,
            filters=SearchFilters(meeting_ids=_meeting_ids(world, 1)),
        )
        every = search_evidence_vector(
            db,
            context=context,
            store=STORE,
            embedding_version=_version(),
            query_vector=_query_vector(MARKER),
            now=NOW,
        )
    assert narrowed.state == "answered"
    assert {hit.entry_id for hit in narrowed.hits} == _entry_ids(world, 1)
    assert every.state == "answered"
    assert {hit.entry_id for hit in every.hits} == _entry_ids(world)


def test_hybrid_narrows_to_the_asked_conversations(world):
    context = _context(world["tenant"]["user_id"], world["workspace_id"])
    vector = _query_vector(f"{MARKER} گفتگو 2")
    with _bound(world["workspace_id"]) as db:
        narrowed = search_evidence_hybrid(
            db,
            context=context,
            query=MARKER,
            now=NOW,
            store=STORE,
            embedding_version=_version(),
            query_vector=vector,
            filters=SearchFilters(meeting_ids=_meeting_ids(world, 2)),
            clock=_frozen_clock,
        )
        two = search_evidence_hybrid(
            db,
            context=context,
            query=MARKER,
            now=NOW,
            store=STORE,
            embedding_version=_version(),
            query_vector=vector,
            filters=SearchFilters(meeting_ids=_meeting_ids(world, 0, 1)),
            clock=_frozen_clock,
        )
    assert narrowed.state == "answered"
    assert {hit.entry_id for hit in narrowed.hits} == _entry_ids(world, 2)
    assert two.state == "answered"
    assert {hit.entry_id for hit in two.hits} == _entry_ids(world, 0) | _entry_ids(world, 1)


def test_a_made_up_conversation_id_gives_zero_hits_without_an_error(world):
    context = _context(world["tenant"]["user_id"], world["workspace_id"])
    made_up = "3f1e2d3c-4b5a-4f6e-8d7c-6b5a4f3e2d1c"
    with _bound(world["workspace_id"]) as db:
        for outcome in (
            search_evidence_lexical(
                db,
                context=context,
                query=MARKER,
                now=NOW,
                filters=SearchFilters(meeting_ids=frozenset({made_up})),
            ),
            search_evidence_hybrid(
                db,
                context=context,
                query=MARKER,
                now=NOW,
                store=STORE,
                embedding_version=_version(),
                query_vector=_query_vector(MARKER),
                filters=SearchFilters(meeting_ids=frozenset({made_up})),
                clock=_frozen_clock,
            ),
        ):
            assert outcome.state == "answered"
            assert outcome.hits == ()


def test_meeting_ids_compose_with_the_other_filters(world):
    with inspection_session_factory()() as db:
        db.execute(
            update(Meeting)
            .where(Meeting.id == world["setups"][1]["meeting_id"])
            .values(conversation_type="meeting")
        )
        db.commit()
    context = _context(world["tenant"]["user_id"], world["workspace_id"])
    with _bound(world["workspace_id"]) as db:
        outcome = search_evidence_lexical(
            db,
            context=context,
            query=MARKER,
            now=NOW,
            filters=SearchFilters(
                meeting_ids=_meeting_ids(world, 0, 1),
                conversation_types=frozenset({"call"}) & CONVERSATION_TYPES,
            ),
        )
    assert outcome.state == "answered"
    assert {hit.entry_id for hit in outcome.hits} == _entry_ids(world, 0)


def test_the_narrowing_acts_before_the_limit(world):
    """``limit`` 1 with one asked conversation: the hit belongs to that
    conversation, not to whichever entry ranks first in the workspace."""
    context = _context(world["tenant"]["user_id"], world["workspace_id"])
    with _bound(world["workspace_id"]) as db:
        outcome = search_evidence_lexical(
            db,
            context=context,
            query=MARKER,
            now=NOW,
            limit=1,
            filters=SearchFilters(meeting_ids=_meeting_ids(world, 2)),
        )
    assert len(outcome.hits) == 1
    assert {hit.entry_id for hit in outcome.hits} <= _entry_ids(world, 2)


def test_a_deleted_conversation_is_never_a_hit(world):
    with inspection_session_factory()() as db:
        db.execute(
            update(Meeting)
            .where(Meeting.id == world["setups"][0]["meeting_id"])
            .values(deleted_at=NOW - timedelta(seconds=1))
        )
        db.commit()
    context = _context(world["tenant"]["user_id"], world["workspace_id"])
    with _bound(world["workspace_id"]) as db:
        outcome = search_evidence_lexical(
            db,
            context=context,
            query=MARKER,
            now=NOW,
            filters=SearchFilters(meeting_ids=_meeting_ids(world, 0)),
        )
    assert outcome.state == "answered"
    assert outcome.hits == ()
