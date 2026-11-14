"""HOM-93 PR 2: the indexer, the embedding writer and the read primitives.

The processor indexes every completed or partial text view next to the revision
it derives (one ``retrieval_entries`` row per non-empty text-view segment, ADR
0068).  The tests prove:

* B1: end-to-end processing creates one entry per non-empty segment with the
  right tenant, ACL snapshot, revision and sha256, and the entry's
  ``workspace_id``/``meeting_id`` equal its segment row's (Revision 2 R2);
* B2: indexing the same view twice writes no new row;
* B3: every skip reason of the design is returned and nothing is written;
* B4: a newer current revision supersedes the old revision's entries, lexical
  and vector search return only the new ones, and ``include_history=True``
  brings the old ones back labeled ``historical``;
* B5: a stricter redaction policy (a new view with another
  ``input_policy_digest``) removes the old-policy entries of the meeting, so
  the old unredacted text is never searchable;
* B6: changing a call's team or agent through ``PATCH`` updates every entry's
  ACL snapshot in the same request;
* B7: access is live: a restricted user loses a result at once when the meeting
  is re-attributed, soft-deleted, or the user's team assignment ends, even
  though the entry's snapshot was not refreshed;
* B8: a search bound to workspace B never returns workspace A entries (SQLite
  and PostgreSQL with RLS, ``postgres`` marker);
* B9: the fake embedding provider writes one row per live entry, a second run
  writes nothing, another model or other dimensions gives another
  ``embedding_version`` and new rows, another ``params`` gives the SAME version
  (Revision 2 R1), and a dimension mismatch or a ``ProviderError`` writes
  nothing and does not raise;
* B10: ``pack_vector``/``unpack_vector`` round-trip and every refusal raises;
* B11: ``lexical_search`` matches a Persian and an English word, and the query
  is normalized; on PostgreSQL (``postgres`` marker) the full-text path runs
  and the GIN index serves it; on SQLite the fallback matches the same cases;
* B12: the purge of a meeting removes its entries and embeddings;
* B14: a call indexed with redaction off is found by a word the phone class
  would redact; after redaction is turned on the same search returns nothing at
  once, with no reindex, while a non-PII word of a call indexed under the
  current policy is still found (Revision 2 R3).

The fake embedding provider lives here and never touches the network.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, func, select, text, update
from sqlalchemy.exc import DBAPIError

from backend.config import Settings
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    Meeting,
    Membership,
    RetrievalEmbedding,
    RetrievalEntry,
    StageState,
    Team,
    TeamAssignment,
    TeamRole,
    TextViewSegment,
    TranscriptRedaction,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptSource,
    TranscriptTextView,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs import recompute
from backend.jobs.leases import as_utc
from backend.jobs.processor import process_job
from backend.privacy.redaction_service import policy_digest
from backend.privacy.text_pii import PiiClass
from backend.providers.base import EmbeddingProvider, ProviderError, ProviderIdentity
from backend.providers.mocks import MockASRProvider
from backend.providers.schemas import ASRRequest, ASRResult, ASRSegment
from backend.retrieval.embeddings import (
    embed_entries,
    embedding_version,
    pack_vector,
    unpack_vector,
)
from backend.retrieval.index import (
    INDEX_VERSION,
    IndexOutcome,
    index_text_view,
    refresh_meeting_acl,
)
from backend.retrieval.lexical import lexical_search
from backend.retrieval.vector_store import TableVectorStore
from backend.security.authorization import RecordAccess, resolve_record_access
from backend.tests import test_analysis_recompute as _recompute_tests
from backend.tests import test_row_level_security as _rls
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import _headers, _patch_media, _tenant, _upload
from backend.tests.test_job_processor import _providers

probe_role = _rls.probe_role


@pytest.fixture
def settings(settings, tmp_path):
    """Deployments of this module journal their tombstones (ADR 0022)."""
    return settings.model_copy(update={"tombstone_journal_dir": str(tmp_path / "journal")})


NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)

PERSIAN = (
    "سلام، من علی هستم. فردا ساعت ۱۰ صبح تماس بگیرید؛ مبلغ ۲۵۰ هزار تومان واريز شد "
    "و كد پيگيري AB-12345 است. موبایل ۰۹۱۲ ۳۴۵ ٦٧٨۹ و کتاب‌ها را می‌خوانم."
)
PERSIAN_SECOND = "تهـــران کدّ پستی؛ ایمیل ali.rezaei@example.com را بفرستید."
ENGLISH = (
    "Hello, my name is Sara. Call +44 20 7946 0000 or email sara@example.com, "
    "total 120 thousand toman."
)
# A word the phone class would redact (part of the mobile number above), and a
# word no class touches.
REDACTED_WORD = "0912"
PLAIN_FA_WORD = "تومان"

DIMENSIONS = 8


class SegmentedASR(MockASRProvider):
    """The deterministic mock, one ASR segment per configured text."""

    def __init__(self, *texts: str, language: str = "fa") -> None:
        super().__init__(allow_mock=True, transcript="\n".join(texts), language=language)
        self._texts = texts

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        result = await super().transcribe(request)
        return result.model_copy(
            update={
                "segments": [
                    ASRSegment(
                        index=index,
                        text=value,
                        start_seconds=index * 5.0,
                        end_seconds=index * 5.0 + 4.0,
                    )
                    for index, value in enumerate(self._texts)
                ]
            }
        )


class FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic offline fake: a hash of each text as a fixed-size vector.

    It never touches the network.  ``wrong_dimensions`` makes every vector
    shorter than declared, ``error`` makes every call raise a ``ProviderError``,
    ``bad_value`` puts that value (NaN, for example) first in every vector.
    """

    def __init__(
        self,
        *,
        dimensions: int = DIMENSIONS,
        model: str = "fake-embed",
        params: dict | None = None,
        wrong_dimensions: int | None = None,
        error: str | None = None,
        bad_value: float | None = None,
    ) -> None:
        self._dimensions = dimensions
        self._model = model
        self._params = params or {}
        self._wrong = wrong_dimensions
        self._error = error
        self._bad_value = bad_value

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="fake-local", model=self._model, params=dict(self._params))

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self._error is not None:
            raise ProviderError(self._error, "simulated provider failure")
        vectors = [self._vector(value) for value in texts]
        if self._wrong is not None:
            vectors = [vector[: self._wrong] for vector in vectors]
        if self._bad_value is not None:
            vectors = [[self._bad_value, *vector[1:]] for vector in vectors]
        return vectors

    def _vector(self, value: str) -> list[float]:
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        return [
            (digest[index % len(digest)] / 255.0) * 2.0 - 1.0 for index in range(self._dimensions)
        ]

    def query_for(self, value: str) -> list[float]:
        return self._vector(value)


def _process(
    client,
    email_outbox,
    monkeypatch,
    settings,
    label: str,
    *texts: str,
    language: str = "fa",
    tenant: dict | None = None,
):
    """Upload and process one call with the mock providers (a new tenant unless given)."""
    _patch_media(monkeypatch)
    if tenant is None:
        tenant = _tenant(client, email_outbox, label)
    accepted = _upload(client, tenant, key=f"{label}-0001", language=language)
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job"]["id"]
    result = process_job(
        job_id,
        worker_id=label,
        settings=settings,
        providers=_providers(SegmentedASR(*texts, language=language)),
    )
    assert result["status"] == "completed", result
    return tenant, accepted.json()["meeting"]["id"], job_id


def _entries(meeting_id: str) -> list[RetrievalEntry]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(RetrievalEntry)
                .where(RetrievalEntry.meeting_id == meeting_id)
                .order_by(RetrievalEntry.sequence, RetrievalEntry.id)
            )
        )


def _entry_count(meeting_id: str) -> int:
    with inspection_session_factory()() as db:
        return (
            db.scalar(
                select(func.count())
                .select_from(RetrievalEntry)
                .where(RetrievalEntry.meeting_id == meeting_id)
            )
            or 0
        )


def _embeddings(meeting_id: str) -> list[RetrievalEmbedding]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(RetrievalEmbedding).where(RetrievalEmbedding.meeting_id == meeting_id)
            )
        )


def _unrestricted() -> RecordAccess:
    return RecordAccess(
        user_id="nobody",
        unrestricted=True,
        supervised_team_ids=frozenset(),
        agent_team_ids=frozenset(),
    )


def _search(workspace_id: str, query: str, *, language: str = "fa", **kwargs) -> list:
    with inspection_session_factory()() as db:
        return lexical_search(
            db,
            access=_unrestricted(),
            workspace_id=workspace_id,
            query=query,
            language=language,
            **kwargs,
        )


def _seed_view(
    tenant: dict,
    key: str,
    *,
    texts: tuple[str, ...] = ("سلام دنیا",),
    state: StageState = StageState.COMPLETED,
    redacted: bool = False,
    is_current: bool = True,
    revision_number: int = 1,
    meeting_id: str | None = None,
    revision_id: str | None = None,
    reference_hours: float = 0.0,
) -> dict:
    """Build the smallest chain an entry needs, directly in the database.

    Returns the ids of the meeting, the revision, the view and its text-view
    segments.  ``reference_hours`` shifts the view digest, so two views of one
    revision can coexist.  ``redacted`` derives the view from a (fake)
    redaction of the given policy, like the processor does under an enforced
    policy.
    """
    # All classes: the digest a workspace with redaction on and no class list
    # gets from ``choose_view`` (every supported class).
    digest = policy_digest(list(PiiClass)) if redacted else None
    with inspection_session_factory()() as db:
        if meeting_id is None and revision_id is not None:
            # An existing revision belongs to one meeting; never invent another.
            meeting_id = db.get(TranscriptRevision, revision_id).meeting_id
        if meeting_id is None:
            meeting = Meeting(
                workspace_id=tenant["workspace_id"],
                created_by_user_id=tenant["user_id"],
                title=f"Seeded call {key}",
            )
            db.add(meeting)
            db.flush()
            meeting_id = meeting.id
        if revision_id is None:
            revision = TranscriptRevision(
                workspace_id=tenant["workspace_id"],
                meeting_id=meeting_id,
                revision_number=revision_number,
                source=TranscriptSource.ASR,
                reason="processing",
                language="fa",
                segment_count=len(texts),
                is_current=is_current,
            )
            db.add(revision)
            db.flush()
            revision_id = revision.id
            segment_ids = []
            for sequence, value in enumerate(texts):
                segment = TranscriptSegment(
                    workspace_id=tenant["workspace_id"],
                    meeting_id=meeting_id,
                    revision_id=revision_id,
                    sequence=sequence,
                    speaker_key="agent",
                    start_ms=sequence * 1000,
                    end_ms=sequence * 1000 + 999,
                    text=value,
                )
                db.add(segment)
                db.flush()
                segment_ids.append(segment.id)
        else:
            # An existing revision: its segments are reused, one view text each.
            segment_ids = list(
                db.scalars(
                    select(TranscriptSegment.id)
                    .where(TranscriptSegment.revision_id == revision_id)
                    .order_by(TranscriptSegment.sequence)
                )
            )
            assert len(segment_ids) == len(texts)
        redaction_id = None
        if redacted:
            redaction = TranscriptRedaction(
                workspace_id=tenant["workspace_id"],
                meeting_id=meeting_id,
                revision_id=revision_id,
                detector_version="pii-detector/1",
                classes=[cls.value for cls in PiiClass],
                policy_digest=digest,
                entity_count=0,
                mapping_retained=False,
            )
            db.add(redaction)
            db.flush()
            redaction_id = redaction.id
        view = TranscriptTextView(
            workspace_id=tenant["workspace_id"],
            meeting_id=meeting_id,
            revision_id=revision_id,
            redaction_id=redaction_id,
            input_view="redacted" if redacted else "original",
            input_policy_digest=digest,
            language="fa",
            normalizer_version="speech-normalize/1",
            extractor_version="entities/1",
            detector_version="pii-detector/1",
            view_digest=hashlib.sha256(f"{key}:{reference_hours}".encode()).hexdigest(),
            reference_at=datetime(2026, 11, 13, 8, 0, tzinfo=UTC)
            + timedelta(hours=reference_hours),
            timezone="Asia/Tehran",
            state=state,
            segment_count=len(texts),
            entity_count=0,
        )
        db.add(view)
        db.flush()
        text_segment_ids = []
        for sequence, (value, segment_id) in enumerate(zip(texts, segment_ids, strict=True)):
            row = TextViewSegment(
                workspace_id=tenant["workspace_id"],
                meeting_id=meeting_id,
                view_id=view.id,
                segment_id=segment_id,
                sequence=sequence,
                text=value,
                entity_count=0,
            )
            db.add(row)
            db.flush()
            text_segment_ids.append(row.id)
        db.commit()
        return {
            **tenant,
            "meeting_id": meeting_id,
            "revision_id": revision_id,
            "text_view_id": view.id,
            "text_view_segment_ids": text_segment_ids,
            "segment_ids": segment_ids,
        }


def _index(view_id: str, workspace_id: str):
    with inspection_session_factory()() as db:
        outcome = index_text_view(db, view_id=view_id, workspace_id=workspace_id, now=NOW)
        db.commit()
    return outcome


# --- B1: the processor indexes every segment ----------------------------------------


def test_b1_processing_indexes_one_entry_per_non_empty_segment(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id, _job_id = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "retrieval-b1",
        PERSIAN,
        PERSIAN_SECOND,
    )
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        view = db.scalar(
            select(TranscriptTextView).where(TranscriptTextView.meeting_id == meeting_id)
        )
        segments = list(
            db.scalars(
                select(TextViewSegment)
                .where(TextViewSegment.view_id == view.id)
                .order_by(TextViewSegment.sequence)
            )
        )
    entries = _entries(meeting_id)
    assert len(entries) == len(segments) == 2
    for entry, segment in zip(entries, segments, strict=True):
        assert entry.workspace_id == segment.workspace_id == tenant["workspace_id"]
        assert entry.meeting_id == segment.meeting_id == meeting_id  # Revision 2 R2
        assert entry.revision_id == view.revision_id
        assert entry.text_view_id == view.id
        assert entry.text_view_segment_id == segment.id
        assert entry.segment_id == segment.segment_id
        assert entry.sequence == segment.sequence
        assert entry.revision_number == 1
        assert entry.language == view.language == "fa"
        assert entry.text == segment.text
        assert entry.text_sha256 == hashlib.sha256(entry.text.encode()).hexdigest()
        assert entry.index_version == INDEX_VERSION == "retrieval-index/1"
        assert entry.superseded_at is None
        assert entry.acl_synced_at is not None
        assert (entry.team_id, entry.agent_user_id, entry.created_by_user_id) == (
            meeting.team_id,
            meeting.agent_user_id,
            meeting.created_by_user_id,
        )
    # The first normalized segment still holds the raw phone digits (redaction
    # is off); B14 flips that.
    assert REDACTED_WORD in entries[0].text
    # A segment whose text is only whitespace gets no entry (a seeded view,
    # because the ASR schema refuses empty turns).
    padded = _seed_view(tenant, "padded", texts=("سلام", "   "))
    outcome = _index(padded["text_view_id"], tenant["workspace_id"])
    assert outcome.state == "indexed" and outcome.entry_count == 1
    assert _entry_count(padded["meeting_id"]) == 1


# --- B2: idempotent indexing ----------------------------------------------------------


def test_b2_indexing_the_same_view_twice_writes_no_new_row(
    client, email_outbox, monkeypatch, settings
):
    _tenant_info, meeting_id, _job_id = _process(
        client, email_outbox, monkeypatch, settings, "retrieval-b2", PERSIAN
    )
    entries = _entries(meeting_id)
    view_id, workspace_id = entries[0].text_view_id, entries[0].workspace_id
    before = _entry_count(meeting_id)
    again = _index(view_id, workspace_id)
    assert again.state == "indexed"
    assert again.entry_count == before
    assert (again.superseded_count, again.removed_count) == (0, 0)
    assert _entry_count(meeting_id) == before


# --- B3: every skip reason, nothing written ------------------------------------------


def test_b3_every_skip_reason_is_returned_and_writes_nothing(client, email_outbox):
    tenant = _tenant(client, email_outbox, "retrieval-b3")
    other = _tenant(client, email_outbox, "retrieval-b3-other")
    usable = _seed_view(tenant, "usable")
    failed = _seed_view(tenant, "failed", state=StageState.FAILED)
    skipped = _seed_view(tenant, "skipped-state", state=StageState.SKIPPED)
    deleted = _seed_view(tenant, "deleted")
    not_current = _seed_view(tenant, "old", is_current=False)
    cross = _seed_view(other, "cross")
    with inspection_session_factory()() as db:
        db.execute(
            update(Meeting).where(Meeting.id == deleted["meeting_id"]).values(deleted_at=NOW)
        )
        db.commit()

    cases = (
        ("view_missing", "00000000-0000-4000-8000-000000000000"),
        ("view_missing", cross["text_view_id"]),  # another workspace: never read
        ("view_not_usable", failed["text_view_id"]),
        ("view_not_usable", skipped["text_view_id"]),
        ("meeting_deleted", deleted["text_view_id"]),
        ("revision_not_current", not_current["text_view_id"]),
    )
    for reason, view_id in cases:
        outcome = _index(view_id, tenant["workspace_id"])
        assert (outcome.state, outcome.reason_code) == ("skipped", reason), view_id
        assert (outcome.entry_count, outcome.superseded_count, outcome.removed_count) == (0, 0, 0)
    for setup in (usable, failed, skipped, deleted, not_current):
        assert _entry_count(setup["meeting_id"]) == 0


# --- B4: revisions supersede, history is labeled --------------------------------------


def test_b4_a_newer_revision_supersedes_and_only_the_new_one_is_searchable(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id, _job_id = _process(
        client, email_outbox, monkeypatch, settings, "retrieval-b4", PERSIAN
    )
    provider = FakeEmbeddingProvider()
    version = embedding_version(
        provider.identity(), dimensions=DIMENSIONS, index_version=INDEX_VERSION
    )
    old = _entries(meeting_id)
    assert len(old) == 1 and old[0].superseded_at is None
    with inspection_session_factory()() as db:
        embedded = asyncio.run(
            embed_entries(
                db,
                provider=provider,
                workspace_id=tenant["workspace_id"],
                entry_ids=[entry.id for entry in old],
                dimensions=DIMENSIONS,
                now=NOW,
            )
        )
        db.commit()
    assert embedded.written == 1

    with inspection_session_factory()() as db:
        db.execute(
            update(TranscriptRevision)
            .where(TranscriptRevision.id == old[0].revision_id)
            .values(is_current=False, superseded_at=NOW)
        )
        db.commit()
    newer = _seed_view(
        tenant,
        "newer",
        texts=("سلام دنیای جدید",),
        revision_number=2,
        meeting_id=meeting_id,
        reference_hours=1.0,
    )
    outcome = _index(newer["text_view_id"], tenant["workspace_id"])
    assert outcome.state == "indexed"
    assert outcome.entry_count == 1
    assert outcome.superseded_count == 1  # the old revision's entry
    assert outcome.removed_count == 0
    refreshed = _entries(meeting_id)
    by_revision = {entry.revision_number: entry for entry in refreshed}
    assert by_revision[1].superseded_at is not None
    assert as_utc(by_revision[1].superseded_at) == NOW
    assert by_revision[2].superseded_at is None

    fresh = _search(tenant["workspace_id"], "جدید")
    assert [result.entry_id for result in fresh] == [by_revision[2].id]
    assert all(result.historical is False for result in fresh)
    history = _search(tenant["workspace_id"], "سلام", include_history=True)
    assert {result.entry_id for result in history} == {
        by_revision[1].id,
        by_revision[2].id,
    }
    assert {result.historical for result in history} == {True, False}

    with inspection_session_factory()() as db:
        embedded_again = asyncio.run(
            embed_entries(
                db,
                provider=provider,
                workspace_id=tenant["workspace_id"],
                entry_ids=[entry.id for entry in refreshed],
                dimensions=DIMENSIONS,
                now=NOW,
            )
        )
        db.commit()
        assert embedded_again.written == 1  # only the new live entry
        nearest = TableVectorStore().nearest(
            db,
            access=_unrestricted(),
            workspace_id=tenant["workspace_id"],
            embedding_version=version,
            query=provider.query_for("سلام دنیای جدید"),
        )
        assert [result.entry_id for result in nearest] == [by_revision[2].id]
        assert nearest[0].historical is False
        whole_history = TableVectorStore().nearest(
            db,
            access=_unrestricted(),
            workspace_id=tenant["workspace_id"],
            embedding_version=version,
            query=provider.query_for(PERSIAN),
            k=10,
            include_history=True,
        )
    assert {result.entry_id for result in whole_history} == {
        by_revision[1].id,
        by_revision[2].id,
    }
    assert {result.historical for result in whole_history} == {True, False}


# --- B5: a stricter redaction policy removes the old entries -------------------------


def _set_redaction(workspace_id: str, enabled: bool) -> None:
    """An admin turns the workspace redaction policy on or off (the row, directly)."""
    with inspection_session_factory()() as db:
        db.execute(
            update(Workspace)
            .where(Workspace.id == workspace_id)
            .values(pii_redaction_enabled=enabled)
        )
        db.commit()


def test_b5_a_stricter_policy_removes_the_old_policy_entries(client, email_outbox):
    tenant = _tenant(client, email_outbox, "retrieval-b5")
    workspace_id = tenant["workspace_id"]
    original = _seed_view(tenant, "original", texts=("سلام، موبایل 0912 345 6789 است.",))
    meeting_id = original["meeting_id"]
    first = _index(original["text_view_id"], workspace_id)
    assert (first.state, first.entry_count) == ("indexed", 1)
    old_entry_id = _entries(meeting_id)[0].id
    assert [r.entry_id for r in _search(workspace_id, REDACTED_WORD)] == [old_entry_id]
    with inspection_session_factory()() as db:
        embedded = asyncio.run(
            embed_entries(
                db,
                provider=FakeEmbeddingProvider(),
                workspace_id=workspace_id,
                entry_ids=[old_entry_id],
                dimensions=DIMENSIONS,
                now=NOW,
            )
        )
        db.commit()
    assert embedded.written == 1

    # The admin turns redaction on; the processor derives a redacted view of
    # the same revision under the stricter policy and indexes it.
    _set_redaction(workspace_id, True)
    stricter = _seed_view(
        tenant,
        "stricter",
        texts=("سلام، موبایل [PHONE_1] است.",),
        redacted=True,
        revision_id=original["revision_id"],
        reference_hours=2.0,
    )
    assert stricter["meeting_id"] == meeting_id
    outcome = _index(stricter["text_view_id"], workspace_id)
    assert outcome.state == "indexed"
    assert outcome.removed_count == 1  # the original-policy entry is gone
    assert (outcome.entry_count, outcome.superseded_count) == (1, 0)
    entries = _entries(meeting_id)
    assert [entry.text for entry in entries] == ["سلام، موبایل [PHONE_1] است."]
    assert entries[0].text_view_id == stricter["text_view_id"]
    assert _embeddings(meeting_id) == []  # its vector went with it (cascade)
    # The old unredacted text is not searchable any more, not even with history,
    # and not even after the policy is turned off again: the row is gone.
    assert _search(workspace_id, REDACTED_WORD) == []
    assert _search(workspace_id, REDACTED_WORD, include_history=True) == []
    found = _search(workspace_id, "موبایل")
    assert [result.entry_id for result in found] == [entries[0].id]
    _set_redaction(workspace_id, False)
    assert _search(workspace_id, REDACTED_WORD, include_history=True) == []


# --- B6: PATCH attribution refreshes the snapshot in the same request ------------------


def test_b6_patch_attribution_updates_every_entry_acl(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id, _job_id = _process(
        client, email_outbox, monkeypatch, settings, "retrieval-b6", PERSIAN, PERSIAN_SECOND
    )
    entries = _entries(meeting_id)
    assert all(entry.agent_user_id is None and entry.team_id is None for entry in entries)
    before = {entry.id: entry.acl_synced_at for entry in entries}
    with inspection_session_factory()() as db:
        team = Team(workspace_id=tenant["workspace_id"], name="Sales", slug="sales")
        db.add(team)
        agent = User(email="retrieval-b6-agent@example.com", display_name="Agent")
        db.add(agent)
        db.flush()
        db.add(
            Membership(
                workspace_id=tenant["workspace_id"],
                user_id=agent.id,
                role=WorkspaceRole.MEMBER,
            )
        )
        db.commit()
        team_id, agent_id = team.id, agent.id
    moved = client.patch(
        f"/api/v1/meetings/{meeting_id}",
        json={"team_id": team_id, "agent_user_id": agent_id},
        cookies=tenant["cookies"],
        headers=_headers(tenant),
    )
    assert moved.status_code == 200, moved.text
    refreshed = _entries(meeting_id)
    assert len(refreshed) == len(entries)
    for entry in refreshed:
        assert (entry.team_id, entry.agent_user_id) == (team_id, agent_id)
        assert entry.acl_synced_at > before[entry.id]


# --- B7: access is live, the snapshot is not the authority ----------------------------


def _membership_of(user_id: str) -> Membership:
    with inspection_session_factory()() as db:
        return db.scalar(select(Membership).where(Membership.user_id == user_id))


def _resolved_access(workspace_id: str, membership: Membership) -> RecordAccess:
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, workspace_id)
        return resolve_record_access(db, workspace=workspace, membership=membership, now=NOW)


def test_b7_access_is_live_even_with_a_stale_snapshot(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id, _job_id = _process(
        client, email_outbox, monkeypatch, settings, "retrieval-b7", PERSIAN
    )
    workspace_id = tenant["workspace_id"]
    entry_id = _entries(meeting_id)[0].id
    assert _embed(workspace_id, [entry_id]).written == 1
    with inspection_session_factory()() as db:
        supervisor = User(email="retrieval-b7-sup@example.com", display_name="Supervisor")
        db.add(supervisor)
        db.flush()
        db.add(
            Membership(workspace_id=workspace_id, user_id=supervisor.id, role=WorkspaceRole.MEMBER)
        )
        team = Team(workspace_id=workspace_id, name="Support", slug="support")
        other_team = Team(workspace_id=workspace_id, name="Other", slug="other")
        db.add_all([team, other_team])
        db.flush()
        db.add(
            TeamAssignment(
                workspace_id=workspace_id,
                team_id=team.id,
                user_id=supervisor.id,
                role=TeamRole.SUPERVISOR,
                assigned_by_user_id=tenant["user_id"],
                valid_from=NOW - timedelta(days=1),
            )
        )
        db.execute(
            update(Workspace).where(Workspace.id == workspace_id).values(record_scope="TEAM")
        )
        meeting = db.get(Meeting, meeting_id)
        meeting.team_id = team.id
        db.flush()
        # The snapshot now says the supervised team may see the call.
        assert refresh_meeting_acl(db, meeting=meeting, now=NOW) == 1
        db.commit()
        supervisor_id, team_id, other_team_id = supervisor.id, team.id, other_team.id

    def visible(access: RecordAccess) -> tuple[list, list]:
        with inspection_session_factory()() as db:
            lexical = lexical_search(db, access=access, workspace_id=workspace_id, query="سلام")
            vector = _nearest(db, workspace_id, PERSIAN, access=access)
        return [r.entry_id for r in lexical], [r.entry_id for r in vector]

    access = _resolved_access(workspace_id, _membership_of(supervisor_id))
    assert access.restricted and access.supervised_team_ids == {team_id}
    assert visible(access) == ([entry_id], [entry_id])

    # Re-attributed away from the supervised team (the meeting row written
    # directly, the snapshot not refreshed): hidden at once.
    with inspection_session_factory()() as db:
        db.execute(update(Meeting).where(Meeting.id == meeting_id).values(team_id=other_team_id))
        db.commit()
    assert _entries(meeting_id)[0].team_id == team_id  # the snapshot is stale
    assert visible(access) == ([], [])

    # Back to the supervised team, then soft-deleted: hidden at once.
    with inspection_session_factory()() as db:
        db.execute(update(Meeting).where(Meeting.id == meeting_id).values(team_id=team_id))
        db.commit()
    assert visible(access) == ([entry_id], [entry_id])
    with inspection_session_factory()() as db:
        db.execute(update(Meeting).where(Meeting.id == meeting_id).values(deleted_at=NOW))
        db.commit()
    assert visible(access) == ([], [])

    # Restored, then the supervisor's team assignment ends: the access the
    # caller resolves now holds no team, so the call is hidden at once.
    with inspection_session_factory()() as db:
        db.execute(update(Meeting).where(Meeting.id == meeting_id).values(deleted_at=None))
        db.execute(
            update(TeamAssignment)
            .where(
                TeamAssignment.workspace_id == workspace_id,
                TeamAssignment.user_id == supervisor_id,
            )
            .values(revoked_at=NOW)
        )
        db.commit()
    ended = _resolved_access(workspace_id, _membership_of(supervisor_id))
    assert ended.supervised_team_ids == frozenset()
    assert visible(ended) == ([], [])
    assert _entries(meeting_id)[0].team_id == team_id  # the snapshot still says "visible"


# --- B8: tenant isolation ---------------------------------------------------------------


def _embed(workspace_id: str, entry_ids, provider: FakeEmbeddingProvider | None = None):
    with inspection_session_factory()() as db:
        outcome = asyncio.run(
            embed_entries(
                db,
                provider=provider or FakeEmbeddingProvider(),
                workspace_id=workspace_id,
                entry_ids=list(entry_ids),
                dimensions=DIMENSIONS,
                now=NOW,
            )
        )
        db.commit()
    return outcome


def _version(provider: FakeEmbeddingProvider | None = None) -> str:
    return embedding_version(
        (provider or FakeEmbeddingProvider()).identity(),
        dimensions=DIMENSIONS,
        index_version=INDEX_VERSION,
    )


def _nearest(db, workspace_id: str, text_value: str, *, access=None, **kwargs) -> list:
    return TableVectorStore().nearest(
        db,
        access=access or _unrestricted(),
        workspace_id=workspace_id,
        embedding_version=_version(),
        query=FakeEmbeddingProvider().query_for(text_value),
        **kwargs,
    )


def _two_tenants_same_text(client, email_outbox, monkeypatch, settings, label: str):
    """Two workspaces with the same call text, both indexed and embedded."""
    first, first_meeting, _ = _process(
        client, email_outbox, monkeypatch, settings, f"{label}-a", PERSIAN
    )
    second, second_meeting, _ = _process(
        client, email_outbox, monkeypatch, settings, f"{label}-b", PERSIAN
    )
    first_ids = {entry.id for entry in _entries(first_meeting)}
    second_ids = {entry.id for entry in _entries(second_meeting)}
    assert first_ids and second_ids and not first_ids & second_ids
    # A write for workspace B never embeds an entry of workspace A.
    crossed = _embed(second["workspace_id"], first_ids)
    assert (crossed.state, crossed.written, crossed.skipped) == ("embedded", 0, 0)
    assert _embed(first["workspace_id"], first_ids).written == len(first_ids)
    assert _embed(second["workspace_id"], second_ids).written == len(second_ids)
    return first, first_meeting, first_ids, second, second_meeting, second_ids


def test_b8_a_search_of_workspace_b_never_returns_workspace_a(
    client, email_outbox, monkeypatch, settings
):
    first, _, first_ids, second, _, second_ids = _two_tenants_same_text(
        client, email_outbox, monkeypatch, settings, "retrieval-b8"
    )
    # The same word is in both workspaces; each search returns its own only.
    assert {r.entry_id for r in _search(first["workspace_id"], "سلام")} == first_ids
    assert {r.entry_id for r in _search(second["workspace_id"], "سلام")} == second_ids
    with inspection_session_factory()() as db:
        for tenant, ids in ((first, first_ids), (second, second_ids)):
            found = _nearest(db, tenant["workspace_id"], PERSIAN, k=10, include_history=True)
            assert {result.entry_id for result in found} == ids
    # A workspace that does not exist (or is not visible) gives nothing.
    assert _search("00000000-0000-4000-8000-000000000000", "سلام") == []


@pytest.mark.postgres
def test_b8_under_rls_workspace_b_never_sees_workspace_a(
    client, email_outbox, monkeypatch, settings: Settings, probe_role
):
    first, first_meeting, first_ids, second, second_meeting, second_ids = _two_tenants_same_text(
        client, email_outbox, monkeypatch, settings, "retrieval-b8-rls"
    )
    entry = _entries(first_meeting)[0]
    tables = ("retrieval_entries", "retrieval_embeddings")
    engine = create_engine(probe_role, pool_size=1, max_overflow=0, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            for table in tables:  # no bound context: nothing is visible
                assert _rls._count(connection, table) == 0
            _rls._bind(connection, workspace_id=second["workspace_id"])
            assert [_rls._count(connection, table) for table in tables] == [
                len(second_ids),
                len(second_ids),
            ]
            touched = connection.execute(
                text("UPDATE retrieval_entries SET superseded_at = now() WHERE meeting_id = :m"),
                {"m": first_meeting},
            )
            assert touched.rowcount == 0
            removed = connection.execute(
                text("DELETE FROM retrieval_embeddings WHERE meeting_id = :m"),
                {"m": first_meeting},
            )
            assert removed.rowcount == 0
            with pytest.raises(DBAPIError, match="row-level security"):
                connection.execute(
                    text(
                        "INSERT INTO retrieval_entries (id, workspace_id, meeting_id, "
                        "revision_id, text_view_id, text_view_segment_id, segment_id, "
                        "sequence, revision_number, language, text, text_sha256, "
                        "index_version, acl_synced_at, created_at) VALUES ('forged', :w, :m, "
                        ":r, :v, :tvs, :s, 0, 1, 'fa', 'x', :sha, 'forged/1', now(), now())"
                    ),
                    {
                        "w": first["workspace_id"],
                        "m": first_meeting,
                        "r": entry.revision_id,
                        "v": entry.text_view_id,
                        "tvs": entry.text_view_segment_id,
                        "s": entry.segment_id,
                        "sha": "0" * 64,
                    },
                )
            connection.rollback()
    finally:
        engine.dispose()
    assert len(_entries(first_meeting)) == len(first_ids)  # nothing of A changed
    assert len(_embeddings(first_meeting)) == len(first_ids)

    # The read primitives in a session of the application role bound to B:
    # B's own rows come back, and asking for A's workspace gives nothing.
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        own = lexical_search(
            db, access=_unrestricted(), workspace_id=second["workspace_id"], query="سلام"
        )
        assert {result.entry_id for result in own} == second_ids
        assert (
            lexical_search(
                db, access=_unrestricted(), workspace_id=first["workspace_id"], query="سلام"
            )
            == []
        )
        assert {r.entry_id for r in _nearest(db, second["workspace_id"], PERSIAN)} == second_ids
        assert _nearest(db, first["workspace_id"], PERSIAN) == []


# --- B9: the embedding writer -----------------------------------------------------------


def test_b9_embed_entries_writes_skips_and_fails_honestly(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id, _job_id = _process(
        client, email_outbox, monkeypatch, settings, "retrieval-b9", PERSIAN, PERSIAN_SECOND
    )
    entries = _entries(meeting_id)
    entry_ids = [entry.id for entry in entries]
    provider = FakeEmbeddingProvider()
    identity = provider.identity()
    version = embedding_version(identity, dimensions=DIMENSIONS, index_version=INDEX_VERSION)

    def run(actor: FakeEmbeddingProvider, dimensions: int = DIMENSIONS):
        with inspection_session_factory()() as db:
            outcome = asyncio.run(
                embed_entries(
                    db,
                    provider=actor,
                    workspace_id=tenant["workspace_id"],
                    entry_ids=entry_ids,
                    dimensions=dimensions,
                    now=NOW,
                )
            )
            db.commit()
        return outcome

    first = run(provider)
    assert (first.state, first.reason_code, first.embedding_version) == ("embedded", None, version)
    assert (first.written, first.skipped) == (2, 0)
    rows = _embeddings(meeting_id)
    assert len(rows) == 2
    for row, entry in zip(
        sorted(rows, key=lambda item: item.entry_id),
        sorted(entries, key=lambda item: item.id),
        strict=True,
    ):
        assert row.workspace_id == entry.workspace_id and row.meeting_id == entry.meeting_id
        assert row.entry_id == entry.id
        assert row.embedding_version == version
        assert (row.provider, row.model) == (identity.provider, identity.model)
        assert row.dimensions == DIMENSIONS
        assert len(row.vector) == 4 * DIMENSIONS
        assert row.text_sha256 == entry.text_sha256
        assert as_utc(row.created_at) == NOW

    # A second run writes nothing and skips both.
    second = run(provider)
    assert (second.state, second.written, second.skipped) == ("embedded", 0, 2)

    # R4: a run in a session that already holds the stored rows only inserts.
    # The ORM update guard never fires, and every stored vector stays as it was.
    stored = {row.id: (bytes(row.vector), row.created_at) for row in _embeddings(meeting_id)}
    with inspection_session_factory()() as db:
        loaded = list(
            db.scalars(
                select(RetrievalEmbedding).where(RetrievalEmbedding.meeting_id == meeting_id)
            )
        )
        assert len(loaded) == 2
        again = asyncio.run(
            embed_entries(
                db,
                provider=provider,
                workspace_id=tenant["workspace_id"],
                entry_ids=entry_ids,
                dimensions=DIMENSIONS,
                now=NOW + timedelta(hours=1),
            )
        )
        db.commit()
    assert (again.state, again.written, again.skipped) == ("embedded", 0, 2)
    assert {
        row.id: (bytes(row.vector), row.created_at) for row in _embeddings(meeting_id)
    } == stored

    # Another endpoint (params) is the SAME version (Revision 2 R1): no rows.
    moved = run(FakeEmbeddingProvider(params={"endpoint": "http://elsewhere:8080"}))
    assert moved.embedding_version == version
    assert (moved.state, moved.written, moved.skipped) == ("embedded", 0, 2)

    # Another model or other dimensions is a new version with new rows.
    other_model = run(FakeEmbeddingProvider(model="fake-embed-v2"))
    assert other_model.embedding_version != version and other_model.written == 2
    other_dimensions = run(
        FakeEmbeddingProvider(dimensions=DIMENSIONS + 1), dimensions=DIMENSIONS + 1
    )
    assert other_dimensions.embedding_version not in {version, other_model.embedding_version}
    assert other_dimensions.written == 2
    assert len(_embeddings(meeting_id)) == 6

    # Each failing provider is a new model, so its version still needs rows
    # and the provider is really called.
    before = len(_embeddings(meeting_id))
    mismatch = run(FakeEmbeddingProvider(model="fake-embed-short", wrong_dimensions=DIMENSIONS - 1))
    assert (mismatch.state, mismatch.reason_code) == ("failed", "embedding_dimensions_mismatch")
    assert mismatch.written == 0
    failed = run(FakeEmbeddingProvider(model="fake-embed-down", error="embedding_unavailable"))
    assert (failed.state, failed.reason_code) == ("failed", "embedding_unavailable")
    assert failed.written == 0
    invalid = run(FakeEmbeddingProvider(model="fake-embed-nan", bad_value=float("nan")))
    assert (invalid.state, invalid.reason_code) == ("failed", "embedding_response_invalid")
    assert invalid.written == 0
    # R7: a value outside the float32 range is refused like a non-finite one.
    overflow = run(FakeEmbeddingProvider(model="fake-embed-overflow", bad_value=3.5e38))
    assert (overflow.state, overflow.reason_code) == ("failed", "embedding_response_invalid")
    assert overflow.written == 0
    assert len(_embeddings(meeting_id)) == before  # nothing written, nothing raised

    # Only live entries of this workspace, in entry_ids, are embedded.
    with inspection_session_factory()() as db:
        db.execute(
            update(RetrievalEntry)
            .where(RetrievalEntry.id == entries[0].id)
            .values(superseded_at=NOW)
        )
        db.commit()
    stale = run(FakeEmbeddingProvider(model="fake-embed-v3"))
    assert stale.written == 1  # the superseded entry is left alone


# --- B10: the vector packing ------------------------------------------------------------


def test_b10_pack_and_unpack_vector_round_trip_and_refusals():
    values = [0.5, -1.25, 0.0, 3.5]
    blob = pack_vector(values)
    assert isinstance(blob, bytes) and len(blob) == 4 * len(values)
    assert unpack_vector(blob, len(values)) == values
    # float32 rounding is visible but bounded.
    assert abs(unpack_vector(pack_vector([0.1]), 1)[0] - 0.1) < 1e-6

    with pytest.raises(ValueError):
        pack_vector([])
    with pytest.raises(ValueError):
        pack_vector([0.5] * 4097)
    assert len(pack_vector([0.5] * 4096)) == 4 * 4096  # the boundary passes
    with pytest.raises(ValueError):
        pack_vector([float("inf")])
    with pytest.raises(ValueError):
        pack_vector([float("nan")])
    with pytest.raises(ValueError):
        pack_vector([True])  # a bool is not a vector value
    with pytest.raises(ValueError):
        unpack_vector(pack_vector([0.5, 0.5]), 3)  # a wrong length refuses
    with pytest.raises(ValueError):
        unpack_vector(b"\x00" * 7, 2)


# --- B11: lexical search, both dialects --------------------------------------------------


def test_b11_lexical_search_matches_persian_and_english(
    client, email_outbox, monkeypatch, settings
):
    fa_tenant, fa_meeting, _ = _process(
        client, email_outbox, monkeypatch, settings, "retrieval-b11-fa", PERSIAN
    )
    en_tenant, en_meeting, _ = _process(
        client, email_outbox, monkeypatch, settings, "retrieval-b11-en", ENGLISH, language="en"
    )
    fa_entries = {entry.id for entry in _entries(fa_meeting)}
    en_entries = {entry.id for entry in _entries(en_meeting)}

    persian = _search(fa_tenant["workspace_id"], "سلام")
    assert {result.entry_id for result in persian} == fa_entries
    # The query is normalized: Persian digits fold to the indexed ASCII digits.
    folded = _search(fa_tenant["workspace_id"], "۰۹۱۲")
    assert {result.entry_id for result in folded} == fa_entries
    english = _search(en_tenant["workspace_id"], "Sara", language="en")
    assert {result.entry_id for result in english} == en_entries
    assert _search(fa_tenant["workspace_id"], "سلام دنیا") == []  # a missing word
    assert _search(fa_tenant["workspace_id"], "   ") == []  # empty after normalizing

    # limit bounds.
    with pytest.raises(ValueError):
        _search(fa_tenant["workspace_id"], "سلام", limit=0)
    with pytest.raises(ValueError):
        _search(fa_tenant["workspace_id"], "سلام", limit=101)
    limited = _search(fa_tenant["workspace_id"], "سلام", limit=1)
    assert len(limited) == 1


@pytest.mark.postgres
def test_b11_postgres_full_text_path_uses_the_gin_index(
    client, email_outbox, monkeypatch, settings
):
    fa_tenant, fa_meeting, _ = _process(
        client, email_outbox, monkeypatch, settings, "retrieval-b11-pg-fa", PERSIAN
    )
    en_tenant, en_meeting, _ = _process(
        client, email_outbox, monkeypatch, settings, "retrieval-b11-pg-en", ENGLISH, language="en"
    )
    fa_entries = {entry.id for entry in _entries(fa_meeting)}
    en_entries = {entry.id for entry in _entries(en_meeting)}
    assert {result.entry_id for result in _search(fa_tenant["workspace_id"], "سلام")} == fa_entries
    assert {
        result.entry_id for result in _search(en_tenant["workspace_id"], "Sara", language="en")
    } == en_entries
    # The full-text path ran: the statement is the GIN expression, and a word
    # prefix is not a token, so it matches nothing (the SQLite substring
    # fallback would match it).
    statements: list[str] = []
    with inspection_session_factory()() as db:

        def capture(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement)

        event.listen(db.get_bind(), "before_cursor_execute", capture)
        try:
            found = lexical_search(
                db, access=_unrestricted(), workspace_id=fa_tenant["workspace_id"], query="سلام"
            )
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", capture)
    assert {result.entry_id for result in found} == fa_entries
    assert all(result.score > 0 for result in found)
    assert any(
        "to_tsvector('simple', retrieval_entries.text)" in statement
        and "plainto_tsquery('simple'" in statement
        and "ts_rank(" in statement
        for statement in statements
    ), statements
    assert _search(fa_tenant["workspace_id"], "سلا") == []
    with inspection_session_factory()() as db:
        definition = db.scalar(
            text(
                "SELECT indexdef FROM pg_indexes WHERE tablename = 'retrieval_entries' "
                "AND indexname = 'ix_retrieval_entry_text_fts'"
            )
        )
        assert definition is not None and " USING gin (" in definition
        db.execute(text("SET enable_seqscan = off"))
        plan = "\n".join(
            str(row[0])
            for row in db.execute(
                text(
                    "EXPLAIN SELECT id FROM retrieval_entries "
                    "WHERE to_tsvector('simple', text) @@ plainto_tsquery('simple', 'سلام')"
                )
            ).all()
        )
    assert "ix_retrieval_entry_text_fts" in plan, plan


# --- B12: the purge removes entries and embeddings ---------------------------------------


def test_b12_purging_a_meeting_removes_its_entries_and_embeddings(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id, _job_id = _process(
        client, email_outbox, monkeypatch, settings, "retrieval-b12", PERSIAN, PERSIAN_SECOND
    )
    entries = _entries(meeting_id)
    with inspection_session_factory()() as db:
        outcome = asyncio.run(
            embed_entries(
                db,
                provider=FakeEmbeddingProvider(),
                workspace_id=tenant["workspace_id"],
                entry_ids=[entry.id for entry in entries],
                dimensions=DIMENSIONS,
                now=NOW,
            )
        )
        db.commit()
    assert outcome.written == 2 and len(_embeddings(meeting_id)) == 2
    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )
    assert deleted.status_code == 204, deleted.text
    assert _entries(meeting_id) == []
    assert _embeddings(meeting_id) == []


# --- B14: the redaction policy is live ---------------------------------------------------


def test_b14_turning_redaction_on_hides_old_entries_at_once(
    client, email_outbox, monkeypatch, settings
):
    tenant, plain_meeting, _ = _process(
        client, email_outbox, monkeypatch, settings, "retrieval-b14-plain", PERSIAN
    )
    workspace_id = tenant["workspace_id"]
    entry_id = _entries(plain_meeting)[0].id
    assert _embed(workspace_id, [entry_id]).written == 1
    # Redaction off: the phone digits are searchable, and the vector is found.
    assert [result.entry_id for result in _search(workspace_id, REDACTED_WORD)] == [entry_id]
    with inspection_session_factory()() as db:
        assert [r.entry_id for r in _nearest(db, workspace_id, PERSIAN)] == [entry_id]

    # An admin turns redaction on (the workspace row, written directly).
    _set_redaction(workspace_id, True)

    # The same search returns nothing at once, with no reindex: the entry's
    # view (original, no digest) is not the workspace's default reader view now.
    assert _search(workspace_id, REDACTED_WORD) == []
    assert _search(workspace_id, PLAIN_FA_WORD) == []
    assert _search(workspace_id, REDACTED_WORD, include_history=True) == []
    with inspection_session_factory()() as db:
        assert _nearest(db, workspace_id, PERSIAN, include_history=True) == []
    assert _entry_count(plain_meeting) == 1  # the row stays; it is not evidence

    # Control: a new call of the same workspace, indexed under the current
    # policy, is found by a non-PII word; its redacted text has no phone digits.
    _, enforced_meeting, _ = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "retrieval-b14-enforced",
        PERSIAN,
        tenant=tenant,
    )
    enforced_entries = _entries(enforced_meeting)
    assert len(enforced_entries) == 1
    assert REDACTED_WORD not in enforced_entries[0].text
    found = _search(workspace_id, PLAIN_FA_WORD)
    assert [result.entry_id for result in found] == [enforced_entries[0].id]
    assert _search(workspace_id, REDACTED_WORD) == []

    # The filter is live both ways: redaction off again shows the original
    # entry and hides the redacted one, still with no reindex.
    _set_redaction(workspace_id, False)
    assert [result.entry_id for result in _search(workspace_id, PLAIN_FA_WORD)] == [entry_id]


# --- the ACL refresh helper ---------------------------------------------------------------


def test_refresh_meeting_acl_updates_every_entry_and_returns_the_count(client, email_outbox):
    tenant = _tenant(client, email_outbox, "retrieval-refresh")
    setup = _seed_view(tenant, "refresh", texts=("یک", "دو"))
    assert _index(setup["text_view_id"], tenant["workspace_id"]).state == "indexed"
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, setup["meeting_id"])
        agent = User(email="retrieval-refresh-agent@example.com", display_name="Agent")
        db.add(agent)
        db.flush()
        meeting.agent_user_id = agent.id
        touched = refresh_meeting_acl(db, meeting=meeting, now=NOW)
        db.commit()
        agent_id = agent.id
    assert touched == 2
    for entry in _entries(setup["meeting_id"]):
        assert entry.agent_user_id == agent_id
        assert as_utc(entry.acl_synced_at) == NOW


# --- lexical: LIKE wildcards are escaped (both dialects) -------------------------------


def test_lexical_wildcards_in_the_query_match_only_themselves(client, email_outbox):
    tenant = _tenant(client, email_outbox, "retrieval-like")
    setup = _seed_view(tenant, "like", texts=("کد x_y مبلغ 100%", "کد xay مبلغ 1000"))
    assert _index(setup["text_view_id"], tenant["workspace_id"]).entry_count == 2
    first, second = (entry.id for entry in _entries(setup["meeting_id"]))
    # An unescaped "_" or "%" would match the second text too.
    assert [r.entry_id for r in _search(tenant["workspace_id"], "x_y")] == [first]
    assert [r.entry_id for r in _search(tenant["workspace_id"], "100%")] == [first]
    assert {r.entry_id for r in _search(tenant["workspace_id"], "کد")} == {first, second}
    # Order is score descending, then entry id.  SQLite gives both rows the
    # same score (2 matched tokens), so the id decides.  On PostgreSQL
    # ts_rank also weighs how close the words are, so the scores can differ.
    ordered = _search(tenant["workspace_id"], "کد مبلغ")
    assert {r.entry_id for r in ordered} == {first, second}
    assert ordered == sorted(ordered, key=lambda r: (-r.score, r.entry_id))


# --- D1/D2: an index fault never raises and never fails the job -----------------------


class _BrokenHashlib:
    """Stands in for ``hashlib`` in the indexer; its error quotes call text."""

    @staticmethod
    def sha256(_data):
        raise RuntimeError("sha256 refused سلام 0912")


def test_an_index_fault_rolls_back_its_savepoint_and_logs_no_text(
    client, email_outbox, monkeypatch, caplog
):
    tenant = _tenant(client, email_outbox, "retrieval-fault")
    workspace_id = tenant["workspace_id"]
    original = _seed_view(tenant, "fault-original", texts=("سلام 0912",))
    assert _index(original["text_view_id"], workspace_id).state == "indexed"
    old_ids = [entry.id for entry in _entries(original["meeting_id"])]
    stricter = _seed_view(
        tenant,
        "fault-stricter",
        texts=("سلام [PHONE_1]",),
        redacted=True,
        revision_id=original["revision_id"],
        reference_hours=3.0,
    )
    # The fault comes after the policy-rule DELETE ran inside the savepoint.
    monkeypatch.setattr("backend.retrieval.index.hashlib", _BrokenHashlib)
    with caplog.at_level(logging.ERROR), inspection_session_factory()() as db:
        db.execute(update(Meeting).where(Meeting.id == original["meeting_id"]).values(title="kept"))
        outcome = index_text_view(
            db, view_id=stricter["text_view_id"], workspace_id=workspace_id, now=NOW
        )
        db.commit()  # the caller's transaction is still usable
    assert outcome == IndexOutcome("failed", "index_failed", 0, 0, 0)
    assert [entry.id for entry in _entries(original["meeting_id"])] == old_ids  # rolled back
    with inspection_session_factory()() as db:
        assert db.get(Meeting, original["meeting_id"]).title == "kept"
    assert "RETRIEVAL_INDEX_FAILED" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "سلام" not in caplog.text and "0912" not in caplog.text


def test_an_index_fault_never_fails_the_job(client, email_outbox, monkeypatch, settings):
    monkeypatch.setattr("backend.retrieval.index.hashlib", _BrokenHashlib)
    _tenant_info, meeting_id, _job_id = _process(
        client, email_outbox, monkeypatch, settings, "retrieval-fault-job", PERSIAN
    )  # asserts the job completed
    with inspection_session_factory()() as db:
        view = db.scalar(
            select(TranscriptTextView).where(TranscriptTextView.meeting_id == meeting_id)
        )
    assert view is not None and view.state == StageState.COMPLETED
    assert _entry_count(meeting_id) == 0
    monkeypatch.undo()
    _patch_media(monkeypatch)
    # Once the fault is gone, indexing the same view works.
    assert _index(view.id, view.workspace_id).entry_count == 1


# --- D4: the vector store bounds -------------------------------------------------------


def test_vector_store_bounds_order_and_query_checks(client, email_outbox):
    tenant = _tenant(client, email_outbox, "retrieval-vector")
    workspace_id = tenant["workspace_id"]
    texts = ("یک", "دو", "سه")
    setup = _seed_view(tenant, "vector", texts=texts)
    assert _index(setup["text_view_id"], workspace_id).entry_count == 3
    entries = _entries(setup["meeting_id"])
    assert _embed(workspace_id, [entry.id for entry in entries]).written == 3
    store = TableVectorStore()
    with inspection_session_factory()() as db:
        for bad in (
            {"k": 0},
            {"k": 101},
            {"candidate_limit": 0},
            {"candidate_limit": 5001},
        ):
            with pytest.raises(ValueError):
                _nearest(db, workspace_id, "دو", **bad)
        for query in (
            [],
            [0.5] * (DIMENSIONS + 1),
            [float("nan")] * DIMENSIONS,
            [True] * DIMENSIONS,
        ):
            with pytest.raises(ValueError):
                store.nearest(
                    db,
                    access=_unrestricted(),
                    workspace_id=workspace_id,
                    embedding_version=_version(),
                    query=query,
                )
        results = _nearest(db, workspace_id, "دو", k=100, candidate_limit=5000)
        assert [r.entry_id for r in results][0] == entries[1].id
        assert results[0].score == pytest.approx(1.0, abs=1e-6)
        assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)
        assert {r.entry_id for r in results} == {entry.id for entry in entries}
        assert len(_nearest(db, workspace_id, "دو", k=1)) == 1
        # At most candidate_limit vectors are scored: the newest conversations
        # first, ties by entry id (HOM-95, ADR 0080).  All three entries share
        # one conversation here, so the window holds the lowest entry ids.
        limited = _nearest(db, workspace_id, "دو", candidate_limit=2)
        assert {r.entry_id for r in limited} == set(sorted(e.id for e in entries)[:2])
        # A version with no vector here gives nothing.
        other = TableVectorStore().nearest(
            db,
            access=_unrestricted(),
            workspace_id=workspace_id,
            embedding_version="f" * 64,
            query=[0.5] * DIMENSIONS,
        )
        assert other == []


# --- F1/F2: the database itself refuses the indexer's write at flush time -------------


@contextmanager
def _entries_refused_by_the_database():
    """Every new retrieval entry breaks ``ck_retrieval_entry_sequence``.

    The hook only changes a value before the INSERT.  The refusal comes from
    the database CHECK at flush time, on SQLite and PostgreSQL alike.
    """

    def break_sequence(_mapper, _connection, target) -> None:
        target.sequence = -1

    event.listen(RetrievalEntry, "before_insert", break_sequence)
    try:
        yield
    finally:
        event.remove(RetrievalEntry, "before_insert", break_sequence)


def test_a_database_refusal_at_flush_leaves_the_session_usable(client, email_outbox, caplog):
    tenant = _tenant(client, email_outbox, "retrieval-refused")
    workspace_id = tenant["workspace_id"]
    target = _seed_view(tenant, "refused-target", texts=("سلام 0912", "دنیا"))
    other = _seed_view(tenant, "refused-other", texts=("دیگر",))
    assert _index(other["text_view_id"], workspace_id).entry_count == 1
    # The other call's entry is moved onto the target's first segment.  The
    # indexer does not see it (another view, another meeting), so its INSERT
    # hits the unique key (text_view_segment_id, index_version) at flush.
    with inspection_session_factory()() as db:
        forged_id = db.scalar(
            select(RetrievalEntry.id).where(RetrievalEntry.meeting_id == other["meeting_id"])
        )
        db.execute(
            update(RetrievalEntry)
            .where(RetrievalEntry.id == forged_id)
            .values(text_view_segment_id=target["text_view_segment_ids"][0])
        )
        db.commit()

    with caplog.at_level(logging.ERROR), inspection_session_factory()() as db:
        outcome = index_text_view(
            db, view_id=target["text_view_id"], workspace_id=workspace_id, now=NOW
        )
        # The session is still usable: a query and a write in the same
        # transaction work, and the commit goes through.
        title = db.scalar(select(Meeting.title).where(Meeting.id == target["meeting_id"]))
        assert title == "Seeded call refused-target"
        db.execute(
            update(Meeting).where(Meeting.id == target["meeting_id"]).values(title="still usable")
        )
        db.commit()
    assert outcome == IndexOutcome("failed", "index_failed", 0, 0, 0)
    assert _entry_count(target["meeting_id"]) == 0  # no entry was written
    assert [entry.id for entry in _entries(other["meeting_id"])] == [forged_id]
    with inspection_session_factory()() as db:
        assert db.get(Meeting, target["meeting_id"]).title == "still usable"
    assert "RETRIEVAL_INDEX_FAILED" in caplog.text
    assert "IntegrityError" in caplog.text
    assert "سلام" not in caplog.text and "0912" not in caplog.text


def test_a_check_refusal_at_flush_returns_failed_and_writes_nothing(client, email_outbox):
    tenant = _tenant(client, email_outbox, "retrieval-refused-check")
    workspace_id = tenant["workspace_id"]
    setup = _seed_view(tenant, "refused-check", texts=("یک", "دو"))
    with _entries_refused_by_the_database(), inspection_session_factory()() as db:
        outcome = index_text_view(
            db, view_id=setup["text_view_id"], workspace_id=workspace_id, now=NOW
        )
        assert db.scalar(select(func.count()).select_from(RetrievalEntry)) is not None
        db.commit()
    assert outcome == IndexOutcome("failed", "index_failed", 0, 0, 0)
    assert _entry_count(setup["meeting_id"]) == 0
    # Without the refusal the same view indexes normally.
    assert _index(setup["text_view_id"], workspace_id).entry_count == 2


def test_an_index_refused_by_the_database_never_fails_the_job(
    client, email_outbox, monkeypatch, settings
):
    with _entries_refused_by_the_database():
        tenant, meeting_id, _job_id = _process(
            client, email_outbox, monkeypatch, settings, "retrieval-refused-job", PERSIAN
        )  # asserts the job completed
    with inspection_session_factory()() as db:
        revision = db.scalar(
            select(TranscriptRevision).where(
                TranscriptRevision.meeting_id == meeting_id,
                TranscriptRevision.is_current.is_(True),
            )
        )
        assert revision is not None
        segment_count = db.scalar(
            select(func.count())
            .select_from(TranscriptSegment)
            .where(TranscriptSegment.revision_id == revision.id)
        )
        view = db.scalar(
            select(TranscriptTextView).where(TranscriptTextView.meeting_id == meeting_id)
        )
        view_segment_count = db.scalar(
            select(func.count())
            .select_from(TextViewSegment)
            .where(TextViewSegment.view_id == view.id)
        )
    # The transcript and its text view are saved; only the index is empty.
    assert segment_count == 1 and view_segment_count == 1
    assert view.state == StageState.COMPLETED
    assert _entry_count(meeting_id) == 0
    # Once the database accepts the rows, indexing the saved view works.
    assert _index(view.id, tenant["workspace_id"]).entry_count == 1


# --- R7: vector values outside the float32 range ---------------------------------------

FLOAT32_MAX = 3.4028234663852886e38  # the largest finite float32


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(3.5e38, id="3.5e38"),
        pytest.param(-3.5e38, id="minus-3.5e38"),
        pytest.param(1e39, id="1e39"),
        pytest.param(10**400, id="10**400"),
    ],
)
def test_b10_a_value_outside_float32_is_refused_with_value_error(value):
    # pytest.raises(ValueError) does not catch OverflowError, so an
    # OverflowError fails this test.
    with pytest.raises(ValueError):
        pack_vector([0.5, value])


def test_b10_the_float32_limits_still_pack():
    values = [FLOAT32_MAX, -FLOAT32_MAX]
    assert unpack_vector(pack_vector(values), 2) == values


# --- R8: an oversized query is refused before any SQL runs ------------------------------


def _words(count: int) -> str:
    return " ".join(f"w{index}" for index in range(count))


def test_r8_an_oversized_query_is_refused_before_any_sql(client, email_outbox):
    tenant = _tenant(client, email_outbox, "retrieval-oversized")
    workspace_id = tenant["workspace_id"]
    setup = _seed_view(tenant, "oversized", texts=(_words(33),))
    assert _index(setup["text_view_id"], workspace_id).entry_count == 1
    entry_id = _entries(setup["meeting_id"])[0].id

    # At the limits the query runs: 1000 characters after strip, 32 tokens.
    assert _search(workspace_id, "x" * 1000) == []
    assert _search(workspace_id, "  " + "x" * 1000 + "  ") == []
    assert [r.entry_id for r in _search(workspace_id, _words(32))] == [entry_id]

    # Over the limits: a ValueError, no SQL statement, and the caller's
    # transaction is still usable afterwards.
    statements: list[str] = []
    with inspection_session_factory()() as db:
        db.execute(update(Meeting).where(Meeting.id == setup["meeting_id"]).values(title="kept"))

        def capture(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement)

        event.listen(db.get_bind(), "before_cursor_execute", capture)
        try:
            for oversized in ("x" * 1001, "  " + "x" * 1001, _words(33)):
                with pytest.raises(ValueError):
                    lexical_search(
                        db, access=_unrestricted(), workspace_id=workspace_id, query=oversized
                    )
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", capture)
        assert statements == []
        assert db.scalar(select(Meeting.title).where(Meeting.id == setup["meeting_id"])) == "kept"
        db.commit()
    with inspection_session_factory()() as db:
        assert db.get(Meeting, setup["meeting_id"]).title == "kept"


# --- R9: the text view recompute path indexes its new view ------------------------------


def test_r9_the_recompute_path_indexes_its_new_view(client, email_outbox, settings, monkeypatch):
    # The correction must not start the recompute itself: this test calls it.
    monkeypatch.setattr(settings, "recompute_on_correction", False)
    _, workspace_id, meeting_id = _recompute_tests._processed_meeting(
        client, email_outbox, settings, monkeypatch
    )
    (old,) = _entries(meeting_id)
    assert old.superseded_at is None
    assert [r.entry_id for r in _search(workspace_id, "قرارداد")] == [old.id]

    corrected = _recompute_tests._correct(client, workspace_id, meeting_id)
    # Only the recompute derives a view of the corrected revision: until it
    # runs, nothing indexes that revision, and the old one is not current.
    assert {entry.revision_id for entry in _entries(meeting_id)} == {old.revision_id}
    assert _search(workspace_id, "فاکتور") == []

    result = recompute.recompute_meeting_analysis(
        meeting_id,
        corrected["id"],
        workspace_id=workspace_id,
        settings=settings,
        providers=_recompute_tests._bundle(),
    )
    assert result == "completed"
    with inspection_session_factory()() as db:
        view = db.scalar(
            select(TranscriptTextView).where(TranscriptTextView.revision_id == corrected["id"])
        )
    assert view is not None and view.state == StageState.COMPLETED
    entries = {entry.revision_id: entry for entry in _entries(meeting_id)}
    assert set(entries) == {old.revision_id, corrected["id"]}
    new = entries[corrected["id"]]
    assert new.text_view_id == view.id and new.superseded_at is None
    assert entries[old.revision_id].superseded_at is not None  # the revision rule ran
    assert [r.entry_id for r in _search(workspace_id, "فاکتور")] == [new.id]
    assert _search(workspace_id, "قرارداد") == []
    history = _search(workspace_id, "قرارداد", include_history=True)
    assert [(r.entry_id, r.historical) for r in history] == [(old.id, True)]
