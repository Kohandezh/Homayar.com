"""HOM-93 PR 2 oracle (hom93-t2): the retrieval indexer, embeddings and read primitives.

Written from SPEC.md (with its Revision 2) and ADR 0068 before the implementation was
visible.  It checks the observable contract of ``backend.retrieval``: the processor hook and
the indexer (one entry per non-empty text-view segment, ids copied from the segment row,
idempotence, each skip reason, the revision and policy rules, the savepoint), the ACL
refresh of ``PATCH /meetings/{id}``, live access and the live redaction policy in the read
primitives, tenancy, the insert-only embedding writer and its version (no ``params``), the
float32 packing, the lexical search on both dialects, the vector store and the purge.
Every case imports
``backend.retrieval`` inside the case, so on the base commit each one fails on its own.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import math
import struct
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    AuditEvent,
    Meeting,
    Membership,
    RecordScope,
    RetrievalEmbedding,
    RetrievalEntry,
    StageState,
    Team,
    TeamAssignment,
    TeamRole,
    TextViewSegment,
    TranscriptSource,
    TranscriptTextView,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs.processor import process_job
from backend.privacy.text_pii import PiiClass
from backend.providers.base import EmbeddingProvider, ProviderError, ProviderIdentity
from backend.security.authorization import RecordAccess, resolve_record_access
from backend.services.transcript_service import (
    SegmentDraft,
    create_revision,
    current_revision,
    segments_of,
)
from backend.speech import text_view
from backend.speech.text_view import derive_text_view
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_ingestion_ledger import CONTENT, _headers, _patch_media, _tenant, _upload
from backend.tests.test_job_processor import SilentASRProvider, _providers
from backend.tests.test_text_view import SegmentedASR

INDEX_VERSION = "retrieval-index/1"
DIMENSIONS = 8
FA = "سلام من علي هستم و درباره قرارداد زرافه صحبت مي کنيم"
BLANK = "‌"
EN = "Hello my name is Sara and the zebra contract is ready"
FA_SECOND = "قرارداد جدید ساعت ۱۰ فردا امضا مي شود"
NEWER = "نسخه تازه درباره پلنگ است"
PII = "شماره من ۰۹۱۲ ۳۴۵ ٦٧٨٩ و ایمیل ali.rezaei@example.com است"
TEXTS = (FA, BLANK, EN, FA_SECOND, EN)
STALE = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def _on_postgres() -> bool:
    return is_postgres_url(configured_database_url())


def _now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bound(workspace_id: str) -> Session:
    db = get_session_factory()()
    bind_tenant_context(db, workspace_id=workspace_id)
    return db


def _fake_vector(seed: str, value: str, size: int) -> list[float]:
    digest = b""
    counter = 0
    while len(digest) < size:
        digest += hashlib.sha256(f"{seed}|{counter}|{value}".encode()).digest()
        counter += 1
    return [(byte - 128) / 128 for byte in digest[:size]]


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norms = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norms


class _FakeEmbeddings(EmbeddingProvider):
    def __init__(
        self,
        *,
        model: str = "oracle-embed-a",
        size: int = DIMENSIONS,
        params: dict | None = None,
        error: Exception | None = None,
        bad_size_from_call: int | None = None,
    ) -> None:
        self.model = model
        self.size = size
        self.params = {"pooling": "mean"} if params is None else params
        self.error = error
        self.bad_size_from_call = bad_size_from_call
        self.calls: list[list[str]] = []

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            provider="oracle-fake-embeddings", model=self.model, params=dict(self.params)
        )

    def vector(self, value: str) -> list[float]:
        return _fake_vector(self.model, value, self.size)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.error is not None:
            raise self.error
        size = self.size
        if self.bad_size_from_call is not None and len(self.calls) >= self.bad_size_from_call:
            size += 1
        return [_fake_vector(self.model, value, size) for value in texts]

    def sent(self) -> list[str]:
        return [value for call in self.calls for value in call]


def _expected_version(
    identity: ProviderIdentity, dimensions: int, index_version: str = INDEX_VERSION
) -> str:
    payload = {
        "provider": identity.provider,
        "model": identity.model,
        "dimensions": dimensions,
        "index_version": index_version,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _user(label: str) -> str:
    with inspection_session_factory()() as db:
        user = User(
            email=f"hom93-t2-{label}-{uuid.uuid4().hex[:8]}@example.com", display_name=label
        )
        db.add(user)
        db.commit()
        return user.id


def _member(workspace_id: str, label: str) -> str:
    user_id = _user(label)
    with inspection_session_factory()() as db:
        db.add(Membership(workspace_id=workspace_id, user_id=user_id, role=WorkspaceRole.MEMBER))
        db.commit()
    return user_id


def _team(workspace_id: str, label: str) -> str:
    with inspection_session_factory()() as db:
        team = Team(
            workspace_id=workspace_id,
            name=f"Team {label}",
            slug=f"{label}-{uuid.uuid4().hex[:8]}",
        )
        db.add(team)
        db.commit()
        return team.id


def _supervise(workspace_id: str, team_id: str, user_id: str, assigned_by: str) -> str:
    with inspection_session_factory()() as db:
        assignment = TeamAssignment(
            workspace_id=workspace_id,
            team_id=team_id,
            user_id=user_id,
            role=TeamRole.SUPERVISOR,
            assigned_by_user_id=assigned_by,
            valid_from=_now() - timedelta(days=1),
        )
        db.add(assignment)
        db.commit()
        return assignment.id


def _write(model, row_id: str, **values: object) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(model).where(model.id == row_id).values(**values))
        db.commit()


def _workspace_classes(workspace_id: str) -> list[PiiClass]:
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, workspace_id)
        assert workspace is not None
        return [PiiClass(name) for name in workspace.pii_redaction_classes]


def _team_scope(workspace_id: str) -> None:
    _write(Workspace, workspace_id, record_scope=RecordScope.TEAM)


def _everyone(user_id: str) -> RecordAccess:
    return RecordAccess(
        user_id=user_id,
        unrestricted=True,
        supervised_team_ids=frozenset(),
        agent_team_ids=frozenset(),
    )


def _resolved(workspace_id: str, user_id: str) -> RecordAccess:
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, workspace_id)
        membership = db.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace_id, Membership.user_id == user_id
            )
        )
        assert workspace is not None and membership is not None
        return resolve_record_access(db, workspace=workspace, membership=membership)


def _process(
    client,
    email_outbox,
    monkeypatch,
    settings,
    label: str,
    *texts: str,
    tenant: dict | None = None,
    attribution: dict | None = None,
    asr=None,
) -> tuple[dict, str, str, dict]:
    _patch_media(monkeypatch)
    tenant = tenant or _tenant(client, email_outbox, label)
    accepted = _upload(
        client, tenant, key=f"hom93-t2-{label}", content=CONTENT + label.encode("utf-8")
    )
    assert accepted.status_code == 202, accepted.text
    meeting_id = accepted.json()["meeting"]["id"]
    job_id = accepted.json()["job"]["id"]
    if attribution:
        _write(Meeting, meeting_id, **attribution)
    result = process_job(
        job_id,
        worker_id=f"hom93-t2-{label}",
        settings=settings,
        providers=_providers(asr or SegmentedASR(*texts)),
    )
    return tenant, meeting_id, job_id, result


def _processed(client, email_outbox, monkeypatch, settings, label: str, *texts: str, **kwargs):
    tenant, meeting_id, job_id, result = _process(
        client, email_outbox, monkeypatch, settings, label, *texts, **kwargs
    )
    assert result == {"job_id": job_id, "status": "completed"}, result
    return tenant, meeting_id


def _views(meeting_id: str) -> list[TranscriptTextView]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(TranscriptTextView)
                .where(TranscriptTextView.meeting_id == meeting_id)
                .order_by(TranscriptTextView.created_at, TranscriptTextView.id)
            )
        )


def _view_segments(view_id: str) -> list[TextViewSegment]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(TextViewSegment)
                .where(TextViewSegment.view_id == view_id)
                .order_by(TextViewSegment.sequence)
            )
        )


def _current_revision_id(workspace_id: str, meeting_id: str) -> str:
    with inspection_session_factory()() as db:
        revision = current_revision(db, workspace_id=workspace_id, meeting_id=meeting_id)
        assert revision is not None
        return revision.id


def _entries(meeting_id: str) -> list[RetrievalEntry]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(RetrievalEntry)
                .where(RetrievalEntry.meeting_id == meeting_id)
                .order_by(
                    RetrievalEntry.revision_number,
                    RetrievalEntry.sequence,
                    RetrievalEntry.text_view_id,
                )
            )
        )


def _entry_state(meeting_id: str) -> list[tuple]:
    return [
        (
            entry.id,
            entry.text_view_id,
            entry.text,
            entry.team_id,
            entry.agent_user_id,
            entry.created_by_user_id,
            _utc(entry.acl_synced_at),
            _utc(entry.superseded_at),
        )
        for entry in _entries(meeting_id)
    ]


def _embeddings(**where: str) -> list[RetrievalEmbedding]:
    statement = select(RetrievalEmbedding).order_by(RetrievalEmbedding.id)
    for column, value in where.items():
        statement = statement.where(getattr(RetrievalEmbedding, column) == value)
    with inspection_session_factory()() as db:
        return list(db.scalars(statement))


def _clear_entries(meeting_id: str) -> None:
    with inspection_session_factory()() as db:
        db.execute(delete(RetrievalEmbedding).where(RetrievalEmbedding.meeting_id == meeting_id))
        db.execute(delete(RetrievalEntry).where(RetrievalEntry.meeting_id == meeting_id))
        db.commit()


def _stale_snapshots(meeting_id: str) -> None:
    with inspection_session_factory()() as db:
        db.execute(
            update(RetrievalEntry)
            .where(RetrievalEntry.meeting_id == meeting_id)
            .values(acl_synced_at=STALE)
        )
        db.commit()


def _index(workspace_id: str, view_id: str, *, bound: str | None = None, now=None):
    from backend.retrieval.index import index_text_view

    with _bound(bound or workspace_id) as db:
        outcome = index_text_view(db, view_id=view_id, workspace_id=workspace_id, now=now or _now())
        db.commit()
    return outcome


def _indexed(workspace_id: str, meeting_id: str) -> list[RetrievalEntry]:
    for view in _views(meeting_id):
        if view.state in (StageState.COMPLETED, StageState.PARTIAL):
            assert _index(workspace_id, view.id).state == "indexed"
    return _entries(meeting_id)


def _embed(
    workspace_id: str,
    provider: _FakeEmbeddings,
    entry_ids,
    *,
    dimensions: int = DIMENSIONS,
    bound: str | None = None,
    now=None,
    batch_size: int | None = None,
):
    from backend.retrieval.embeddings import embed_entries

    options = {} if batch_size is None else {"batch_size": batch_size}
    with _bound(bound or workspace_id) as db:
        outcome = asyncio.run(
            embed_entries(
                db,
                provider=provider,
                workspace_id=workspace_id,
                entry_ids=list(entry_ids),
                dimensions=dimensions,
                now=now or _now(),
                **options,
            )
        )
        db.commit()
    return outcome


def _lexical(workspace_id: str, access: RecordAccess, query: str, *, bound=None, **options):
    from backend.retrieval.lexical import lexical_search

    with _bound(bound or workspace_id) as db:
        return lexical_search(db, access=access, workspace_id=workspace_id, query=query, **options)


def _nearest(
    workspace_id: str,
    access: RecordAccess,
    version: str,
    vector,
    *,
    bound=None,
    **options,
):
    from backend.retrieval.vector_store import TableVectorStore

    with _bound(bound or workspace_id) as db:
        return TableVectorStore().nearest(
            db,
            access=access,
            workspace_id=workspace_id,
            embedding_version=version,
            query=vector,
            **options,
        )


def _new_revision(
    workspace_id: str,
    meeting_id: str,
    settings,
    *texts: str,
    classes=(),
    derive: bool = True,
) -> tuple[str, str | None]:
    now = _now()
    with _bound(workspace_id) as db:
        meeting = db.get(Meeting, meeting_id)
        parent = current_revision(db, workspace_id=workspace_id, meeting_id=meeting_id)
        revision, segments = create_revision(
            db,
            meeting=meeting,
            source=TranscriptSource.CORRECTION,
            reason="oracle_correction",
            language="fa",
            drafts=[
                SegmentDraft(
                    sequence=index,
                    speaker_key="unknown",
                    start_ms=index * 5000,
                    end_ms=index * 5000 + 4000,
                    text=value,
                    language="fa",
                )
                for index, value in enumerate(texts)
            ],
            now=now,
            created_by_user_id=meeting.created_by_user_id,
            parent=parent,
        )
        view_id = None
        if derive:
            outcome = derive_text_view(
                db,
                revision=revision,
                segments=segments,
                view_classes=list(classes),
                reference_at=meeting.created_at,
                settings=settings,
                now=now,
            )
            assert outcome.state is StageState.COMPLETED and outcome.view_id is not None
            view_id = outcome.view_id
        db.commit()
        return revision.id, view_id


def _derive_view(
    workspace_id: str, meeting_id: str, settings, *, classes=(), shift=timedelta(0)
) -> str:
    with _bound(workspace_id) as db:
        meeting = db.get(Meeting, meeting_id)
        revision = current_revision(db, workspace_id=workspace_id, meeting_id=meeting_id)
        outcome = derive_text_view(
            db,
            revision=revision,
            segments=segments_of(db, revision),
            view_classes=list(classes),
            reference_at=meeting.created_at + shift,
            settings=settings,
            now=_now(),
        )
        db.commit()
    assert outcome.state is StageState.COMPLETED and outcome.view_id is not None
    return outcome.view_id


@contextmanager
def _statements() -> Iterator[list[str]]:
    seen: list[str] = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        seen.append(" ".join(statement.lower().split()))

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", record)


def _updates_of(statements: list[str], table: str) -> list[str]:
    return [statement for statement in statements if statement.startswith(f"update {table} ")]


@contextmanager
def _refused_entry_inserts() -> Iterator[None]:
    def refuse(_connection, _cursor, statement, _parameters, _context, _many):
        if "insert into retrieval_entries" in " ".join(statement.lower().split()):
            raise RuntimeError("oracle refuses a retrieval entry insert")

    event.listen(Engine, "before_cursor_execute", refuse)
    try:
        yield
    finally:
        event.remove(Engine, "before_cursor_execute", refuse)


def _completed_audit(meeting_id: str) -> dict:
    with inspection_session_factory()() as db:
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "meeting.processing.completed",
                AuditEvent.target_id == meeting_id,
            )
        )
        assert audit is not None
        return dict(audit.details)


def _by_text(entries: list[RetrievalEntry], value: str) -> list[RetrievalEntry]:
    return [entry for entry in entries if value in entry.text]


def _ordered(results) -> bool:
    return list(results) == sorted(results, key=lambda result: (-result.score, result.entry_id))


def test_processing_a_meeting_indexes_every_non_empty_view_segment(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "t2-b1")
    workspace_id = tenant["workspace_id"]
    team_id = _team(workspace_id, "b1")
    agent_id = _user("b1-agent")
    _, meeting_id = _processed(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "t2-b1",
        *TEXTS,
        tenant=tenant,
        attribution={"team_id": team_id, "agent_user_id": agent_id},
    )
    (view,) = _views(meeting_id)
    assert view.state is StageState.COMPLETED
    segments = _view_segments(view.id)
    assert len(segments) == 5 and segments[1].text.strip() == ""
    revision_id = _current_revision_id(workspace_id, meeting_id)
    assert {(s.workspace_id, s.meeting_id) for s in segments} == {(workspace_id, meeting_id)}
    expected = [
        (
            segment.workspace_id,
            segment.meeting_id,
            revision_id,
            1,
            view.id,
            segment.id,
            segment.segment_id,
            segment.sequence,
            view.language,
            segment.text,
            _sha(segment.text),
            INDEX_VERSION,
            team_id,
            agent_id,
            tenant["user_id"],
            None,
        )
        for segment in segments
        if segment.text.strip()
    ]
    entries = _entries(meeting_id)
    assert [
        (
            entry.workspace_id,
            entry.meeting_id,
            entry.revision_id,
            entry.revision_number,
            entry.text_view_id,
            entry.text_view_segment_id,
            entry.segment_id,
            entry.sequence,
            entry.language,
            entry.text,
            entry.text_sha256,
            entry.index_version,
            entry.team_id,
            entry.agent_user_id,
            entry.created_by_user_id,
            entry.superseded_at,
        )
        for entry in entries
    ] == expected
    assert [entry.sequence for entry in entries] == [0, 2, 3, 4]
    assert all(entry.acl_synced_at is not None for entry in entries)
    details = _completed_audit(meeting_id)
    assert details["text_view_id"] == view.id
    assert details["text_view_state"] == "completed"


def test_indexing_the_same_view_again_writes_nothing(client, email_outbox, monkeypatch, settings):
    from backend.retrieval.index import INDEX_VERSION as declared

    assert declared == INDEX_VERSION
    tenant, meeting_id = _processed(client, email_outbox, monkeypatch, settings, "t2-b2", FA, EN)
    workspace_id = tenant["workspace_id"]
    (view,) = _views(meeting_id)
    first = _index(workspace_id, view.id)
    before = _entry_state(meeting_id)
    second = _index(workspace_id, view.id)
    assert (first.state, first.reason_code, first.entry_count) == ("indexed", None, 2)
    assert (second.state, second.reason_code, second.entry_count) == ("indexed", None, 2)
    assert (second.superseded_count, second.removed_count) == (0, 0)
    assert len(before) == 2
    assert [row[:3] for row in _entry_state(meeting_id)] == [row[:3] for row in before]
    assert dataclasses.is_dataclass(second)
    with pytest.raises(dataclasses.FrozenInstanceError):
        second.state = "failed"


def test_an_unknown_view_or_a_view_of_another_workspace_is_skipped_as_missing(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-missing", FA, EN
    )
    other = _tenant(client, email_outbox, "t2-missing-other")
    workspace_id = tenant["workspace_id"]
    (view,) = _views(meeting_id)
    _clear_entries(meeting_id)
    unknown = _index(workspace_id, str(uuid.uuid4()))
    assert (unknown.state, unknown.reason_code) == ("skipped", "view_missing")
    foreign = _index(other["workspace_id"], view.id)
    assert (foreign.state, foreign.reason_code) == ("skipped", "view_missing")
    assert _entries(meeting_id) == []
    with inspection_session_factory()() as db:
        assert (
            db.scalars(
                select(RetrievalEntry.id).where(
                    RetrievalEntry.workspace_id == other["workspace_id"]
                )
            ).all()
            == []
        )
    allowed = _index(workspace_id, view.id)
    assert (allowed.state, allowed.entry_count) == ("indexed", 2)
    assert len(_entries(meeting_id)) == 2


def test_a_failed_or_skipped_view_is_not_usable_and_a_partial_view_is_indexed(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "t2-usable")
    workspace_id = tenant["workspace_id"]
    _, silent_id, silent_job, silent = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "t2-usable-silent",
        tenant=tenant,
        asr=SilentASRProvider(),
    )
    assert silent == {"job_id": silent_job, "status": "partial"}
    (skipped_view,) = _views(silent_id)
    assert skipped_view.state is StageState.SKIPPED

    def broken(*_args, **_kwargs):
        raise RuntimeError("oracle fault")

    with monkeypatch.context() as patch:
        patch.setattr(text_view, "_persist", broken)
        _, failed_id = _processed(
            client, email_outbox, monkeypatch, settings, "t2-usable-failed", FA, tenant=tenant
        )
    (failed_view,) = _views(failed_id)
    assert failed_view.state is StageState.FAILED
    with monkeypatch.context() as patch:
        patch.setattr(text_view, "extract_entities", broken)
        _, partial_id = _processed(
            client, email_outbox, monkeypatch, settings, "t2-usable-partial", FA, tenant=tenant
        )
    (partial_view,) = _views(partial_id)
    assert partial_view.state is StageState.PARTIAL

    for view, meeting_id in ((skipped_view, silent_id), (failed_view, failed_id)):
        outcome = _index(workspace_id, view.id)
        assert (outcome.state, outcome.reason_code) == ("skipped", "view_not_usable")
        assert _entries(meeting_id) == []
    partial = _index(workspace_id, partial_view.id)
    assert (partial.state, partial.reason_code, partial.entry_count) == ("indexed", None, 1)
    (entry,) = _entries(partial_id)
    assert entry.text_view_id == partial_view.id


def test_a_deleted_meeting_is_skipped_and_the_live_meeting_is_indexed(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-deleted", FA, EN
    )
    workspace_id = tenant["workspace_id"]
    (view,) = _views(meeting_id)
    _clear_entries(meeting_id)
    _write(Meeting, meeting_id, deleted_at=_now())
    skipped = _index(workspace_id, view.id)
    assert (skipped.state, skipped.reason_code) == ("skipped", "meeting_deleted")
    assert _entries(meeting_id) == []
    _write(Meeting, meeting_id, deleted_at=None)
    indexed = _index(workspace_id, view.id)
    assert (indexed.state, indexed.entry_count) == ("indexed", 2)
    assert len(_entries(meeting_id)) == 2


def test_a_view_of_a_revision_that_is_no_longer_current_is_skipped(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-not-current", FA, EN
    )
    workspace_id = tenant["workspace_id"]
    (old_view,) = _views(meeting_id)
    _clear_entries(meeting_id)
    _revision_id, new_view_id = _new_revision(workspace_id, meeting_id, settings, NEWER)
    skipped = _index(workspace_id, old_view.id)
    assert (skipped.state, skipped.reason_code) == ("skipped", "revision_not_current")
    assert _entries(meeting_id) == []
    indexed = _index(workspace_id, new_view_id)
    assert (indexed.state, indexed.entry_count) == ("indexed", 1)
    assert [entry.text_view_id for entry in _entries(meeting_id)] == [new_view_id]


def test_a_newer_revision_supersedes_the_old_entries_and_history_is_labeled(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _processed(client, email_outbox, monkeypatch, settings, "t2-b4", FA, EN)
    workspace_id = tenant["workspace_id"]
    owner = _everyone(tenant["user_id"])
    old = _indexed(workspace_id, meeting_id)
    provider = _FakeEmbeddings()
    first = _embed(workspace_id, provider, [entry.id for entry in old])
    version = first.embedding_version
    old_ids = {entry.id for entry in old}
    (old_fa,) = _by_text(old, "زرافه")
    revision_id, new_view_id = _new_revision(workspace_id, meeting_id, settings, NEWER, EN)
    superseded_at = _now().replace(microsecond=0)
    outcome = _index(workspace_id, new_view_id, now=superseded_at)
    assert (outcome.state, outcome.entry_count) == ("indexed", 2)
    assert (outcome.superseded_count, outcome.removed_count) == (2, 0)
    entries = _entries(meeting_id)
    assert {entry.id for entry in entries} >= old_ids
    new = [entry for entry in entries if entry.id not in old_ids]
    assert {(entry.revision_id, entry.revision_number) for entry in new} == {(revision_id, 2)}
    assert all(entry.superseded_at is None for entry in new)
    assert all(
        _utc(entry.superseded_at) == superseded_at for entry in entries if entry.id in old_ids
    )
    new_ids = {entry.id for entry in new}
    _embed(workspace_id, provider, new_ids)
    (new_en,) = _by_text(new, "zebra")
    (new_leopard,) = _by_text(new, "پلنگ")

    assert _lexical(workspace_id, owner, "زرافه") == []
    (historical,) = _lexical(workspace_id, owner, "زرافه", include_history=True)
    assert (historical.entry_id, historical.meeting_id, historical.historical) == (
        old_fa.id,
        meeting_id,
        True,
    )
    assert [(r.entry_id, r.historical) for r in _lexical(workspace_id, owner, "پلنگ")] == [
        (new_leopard.id, False)
    ]
    assert [(r.entry_id, r.historical) for r in _lexical(workspace_id, owner, "zebra")] == [
        (new_en.id, False)
    ]
    labeled = {
        r.entry_id: r.historical
        for r in _lexical(workspace_id, owner, "zebra", include_history=True)
    }
    (old_en,) = _by_text(old, "zebra")
    assert labeled == {old_en.id: True, new_en.id: False}

    query = provider.vector(old_fa.text)
    current = _nearest(workspace_id, owner, version, query)
    assert {r.entry_id for r in current} == new_ids
    assert all(r.historical is False for r in current)
    with_history = _nearest(workspace_id, owner, version, query, include_history=True)
    assert with_history[0].entry_id == old_fa.id and with_history[0].historical is True
    assert with_history[0].score == pytest.approx(1.0, abs=1e-5)
    assert {r.entry_id: r.historical for r in with_history} == {
        **{entry_id: True for entry_id in old_ids},
        **{entry_id: False for entry_id in new_ids},
    }


def test_a_revision_that_stopped_being_current_is_hidden_before_any_reindex(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-b4-live", FA, EN
    )
    workspace_id = tenant["workspace_id"]
    owner = _everyone(tenant["user_id"])
    entries = _indexed(workspace_id, meeting_id)
    provider = _FakeEmbeddings()
    version = _embed(workspace_id, provider, [entry.id for entry in entries]).embedding_version
    (fa_entry,) = _by_text(entries, "زرافه")
    query = provider.vector(fa_entry.text)
    assert [r.entry_id for r in _lexical(workspace_id, owner, "زرافه")] == [fa_entry.id]
    assert _nearest(workspace_id, owner, version, query)[0].entry_id == fa_entry.id
    _new_revision(workspace_id, meeting_id, settings, NEWER, derive=False)
    assert all(entry.superseded_at is None for entry in _entries(meeting_id))
    assert _lexical(workspace_id, owner, "زرافه") == []
    assert _nearest(workspace_id, owner, version, query) == []
    assert [
        (r.entry_id, r.historical)
        for r in _lexical(workspace_id, owner, "زرافه", include_history=True)
    ] == [(fa_entry.id, True)]
    history = _nearest(workspace_id, owner, version, query, include_history=True)
    assert history[0].entry_id == fa_entry.id
    assert all(r.historical is True for r in history)


def test_a_stricter_policy_on_the_same_revision_removes_the_unredacted_entries(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-b5-same", PII, FA
    )
    workspace_id = tenant["workspace_id"]
    owner = _everyone(tenant["user_id"])
    old = _indexed(workspace_id, meeting_id)
    old_ids = {entry.id for entry in old}
    provider = _FakeEmbeddings()
    _embed(workspace_id, provider, old_ids)
    assert len(_embeddings(meeting_id=meeting_id)) == 2
    assert len(_lexical(workspace_id, owner, "ali.rezaei@example.com")) == 1
    assert len(_lexical(workspace_id, owner, "۰۹۱۲")) == 1
    _write(Workspace, workspace_id, pii_redaction_enabled=True)
    redacted_view_id = _derive_view(
        workspace_id, meeting_id, settings, classes=_workspace_classes(workspace_id)
    )
    redacted = {view.id: view for view in _views(meeting_id)}[redacted_view_id]
    assert redacted.input_view == "redacted" and redacted.input_policy_digest is not None
    outcome = _index(workspace_id, redacted_view_id)
    assert (outcome.state, outcome.entry_count) == ("indexed", 2)
    assert (outcome.removed_count, outcome.superseded_count) == (2, 0)
    entries = _entries(meeting_id)
    assert {entry.text_view_id for entry in entries} == {redacted_view_id}
    assert not old_ids & {entry.id for entry in entries}
    assert not any("ali.rezaei" in entry.text or "0912" in entry.text for entry in entries)
    assert _embeddings(meeting_id=meeting_id) == []
    for query in ("ali.rezaei@example.com", "۰۹۱۲", "0912"):
        assert _lexical(workspace_id, owner, query) == []
        assert _lexical(workspace_id, owner, query, include_history=True) == []
    (number,) = _lexical(workspace_id, owner, "شماره")
    assert number.entry_id in {entry.id for entry in entries} and number.historical is False


def test_a_stricter_policy_on_a_newer_revision_removes_the_old_policy_entries_with_history(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-b5-newer", PII, FA
    )
    workspace_id = tenant["workspace_id"]
    owner = _everyone(tenant["user_id"])
    old_ids = {entry.id for entry in _indexed(workspace_id, meeting_id)}
    _write(Workspace, workspace_id, pii_redaction_enabled=True)
    revision_id, view_id = _new_revision(
        workspace_id, meeting_id, settings, PII, FA, classes=_workspace_classes(workspace_id)
    )
    outcome = _index(workspace_id, view_id)
    assert (outcome.state, outcome.entry_count) == ("indexed", 2)
    assert (outcome.removed_count, outcome.superseded_count) == (2, 0)
    entries = _entries(meeting_id)
    assert not old_ids & {entry.id for entry in entries}
    assert {entry.revision_id for entry in entries} == {revision_id}
    assert _lexical(workspace_id, owner, "ali.rezaei@example.com", include_history=True) == []
    assert _lexical(workspace_id, owner, "۰۹۱۲", include_history=True) == []
    assert [
        (r.historical, r.entry_id in {entry.id for entry in entries})
        for r in _lexical(workspace_id, owner, "زرافه", include_history=True)
    ] == [(False, True)]


def test_another_view_of_the_same_revision_replaces_its_entries(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-same-revision", FA, EN
    )
    workspace_id = tenant["workspace_id"]
    owner = _everyone(tenant["user_id"])
    old_ids = {entry.id for entry in _indexed(workspace_id, meeting_id)}
    view_id = _derive_view(workspace_id, meeting_id, settings, shift=timedelta(days=1))
    assert view_id not in {entry.text_view_id for entry in _entries(meeting_id)}
    outcome = _index(workspace_id, view_id)
    assert (outcome.state, outcome.entry_count) == ("indexed", 2)
    assert (outcome.removed_count, outcome.superseded_count) == (2, 0)
    entries = _entries(meeting_id)
    assert {entry.text_view_id for entry in entries} == {view_id}
    assert not old_ids & {entry.id for entry in entries}
    assert [r.entry_id for r in _lexical(workspace_id, owner, "زرافه")] == [
        entry.id for entry in _by_text(entries, "زرافه")
    ]


@pytest.mark.postgres(reason="pysqlite starts no transaction before SAVEPOINT, so RELEASE commits")
def test_the_indexer_writes_inside_the_callers_transaction(
    client, email_outbox, monkeypatch, settings
):
    from backend.retrieval.index import index_text_view

    tenant, meeting_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-transaction", FA, EN
    )
    workspace_id = tenant["workspace_id"]
    _indexed(workspace_id, meeting_id)
    before = [row[:3] for row in _entry_state(meeting_id)]
    view_id = _derive_view(workspace_id, meeting_id, settings, shift=timedelta(days=2))
    with _bound(workspace_id) as db:
        outcome = index_text_view(db, view_id=view_id, workspace_id=workspace_id, now=_now())
        db.rollback()
    assert outcome.state == "indexed"
    assert [row[:3] for row in _entry_state(meeting_id)] == before
    with _bound(workspace_id) as db:
        index_text_view(db, view_id=view_id, workspace_id=workspace_id, now=_now())
        db.commit()
    assert {entry.text_view_id for entry in _entries(meeting_id)} == {view_id}


def test_an_index_failure_is_reported_rolled_back_and_never_fails_the_job(
    client, email_outbox, monkeypatch, settings, caplog
):
    from backend.retrieval.index import index_text_view

    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "t2-index-fault")
    workspace_id = tenant["workspace_id"]
    with _refused_entry_inserts():
        _, meeting_id = _processed(
            client, email_outbox, monkeypatch, settings, "t2-index-fault", PII, FA, tenant=tenant
        )
    (view,) = _views(meeting_id)
    assert view.state is StageState.COMPLETED
    assert _entries(meeting_id) == []
    assert _completed_audit(meeting_id)["text_view_id"] == view.id
    caplog.set_level(logging.INFO)
    caplog.clear()
    with _refused_entry_inserts():
        with _bound(workspace_id) as db:
            outcome = index_text_view(db, view_id=view.id, workspace_id=workspace_id, now=_now())
            meeting = db.get(Meeting, meeting_id)
            meeting.title = "Renamed after a failed index"
            db.commit()
    assert (outcome.state, outcome.reason_code) == ("failed", "index_failed")
    assert _entries(meeting_id) == []
    with inspection_session_factory()() as db:
        assert db.get(Meeting, meeting_id).title == "Renamed after a failed index"
    logged = [record for record in caplog.records if not record.name.startswith("sqlalchemy")]
    assert logged
    messages = "\n".join(record.getMessage() for record in logged)
    for secret in ("ali.rezaei", "0912", "زرافه"):
        assert secret not in messages
    assert (_index(workspace_id, view.id).state, len(_entries(meeting_id))) == ("indexed", 2)
    before = [row[:3] for row in _entry_state(meeting_id)]
    redacted_view_id = _derive_view(
        workspace_id, meeting_id, settings, classes=_workspace_classes(workspace_id)
    )
    with _refused_entry_inserts():
        refused = _index(workspace_id, redacted_view_id)
    assert (refused.state, refused.reason_code) == ("failed", "index_failed")
    assert [row[:3] for row in _entry_state(meeting_id)] == before
    replaced = _index(workspace_id, redacted_view_id)
    assert (replaced.state, replaced.removed_count) == ("indexed", 2)


def test_patch_attribution_refreshes_every_entry_snapshot_in_the_same_request(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "t2-b6")
    workspace_id = tenant["workspace_id"]
    agent_id = _member(workspace_id, "b6-agent")
    team_id = _team(workspace_id, "b6")
    _, meeting_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-b6-call", FA, EN, tenant=tenant
    )
    _, other_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-b6-other", FA_SECOND, tenant=tenant
    )
    _indexed(workspace_id, meeting_id)
    _indexed(workspace_id, other_id)
    _revision_id, view_id = _new_revision(workspace_id, meeting_id, settings, NEWER, EN)
    assert _index(workspace_id, view_id).superseded_count == 2
    _stale_snapshots(meeting_id)
    _stale_snapshots(other_id)
    other_before = _entry_state(other_id)
    started = _now()
    response = client.patch(
        f"/api/v1/meetings/{meeting_id}",
        json={"team_id": team_id, "agent_user_id": agent_id},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    finished = _now()
    assert response.status_code == 200, response.text
    entries = _entries(meeting_id)
    assert len(entries) == 4
    assert sum(entry.superseded_at is not None for entry in entries) == 2
    assert {
        (entry.team_id, entry.agent_user_id, entry.created_by_user_id) for entry in entries
    } == {(team_id, agent_id, tenant["user_id"])}
    for entry in entries:
        synced = _utc(entry.acl_synced_at)
        assert started - timedelta(seconds=5) <= synced <= finished + timedelta(seconds=5)
    assert _entry_state(other_id) == other_before
    cleared = client.patch(
        f"/api/v1/conversations/{meeting_id}",
        json={"team_id": None, "agent_user_id": None},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert cleared.status_code == 200, cleared.text
    assert {(entry.team_id, entry.agent_user_id) for entry in _entries(meeting_id)} == {
        (None, None)
    }
    assert _entry_state(other_id) == other_before


def test_a_refused_patch_leaves_the_entry_snapshot_unchanged(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "t2-b6-refused")
    workspace_id = tenant["workspace_id"]
    team_id = _team(workspace_id, "b6-refused")
    _, meeting_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-b6-refused", FA, EN, tenant=tenant
    )
    assert len(_indexed(workspace_id, meeting_id)) == 2
    _stale_snapshots(meeting_id)
    before = _entry_state(meeting_id)
    for body, status in (
        ({"agent_user_id": tenant["user_id"]}, 403),
        ({"team_id": str(uuid.uuid4())}, 422),
    ):
        refused = client.patch(
            f"/api/v1/meetings/{meeting_id}",
            json=body,
            headers=_headers(tenant),
            cookies=tenant["cookies"],
        )
        assert refused.status_code == status, refused.text
        assert _entry_state(meeting_id) == before
    accepted = client.patch(
        f"/api/v1/meetings/{meeting_id}",
        json={"team_id": team_id},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert accepted.status_code == 200, accepted.text
    assert {entry.team_id for entry in _entries(meeting_id)} == {team_id}
    assert all(_utc(entry.acl_synced_at) > STALE for entry in _entries(meeting_id))


def test_refresh_meeting_acl_updates_only_that_meetings_entries(
    client, email_outbox, monkeypatch, settings
):
    from backend.retrieval.index import refresh_meeting_acl

    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "t2-refresh")
    workspace_id = tenant["workspace_id"]
    agent_id = _member(workspace_id, "refresh-agent")
    team_id = _team(workspace_id, "refresh")
    _, meeting_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-refresh-call", FA, EN, tenant=tenant
    )
    _, other_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-refresh-other", FA, tenant=tenant
    )
    _indexed(workspace_id, meeting_id)
    _indexed(workspace_id, other_id)
    other_before = _entry_state(other_id)
    _write(Meeting, meeting_id, team_id=team_id, agent_user_id=agent_id)
    assert {(e.team_id, e.agent_user_id) for e in _entries(meeting_id)} == {(None, None)}
    synced_at = _now().replace(microsecond=0)
    with _bound(workspace_id) as db:
        meeting = db.get(Meeting, meeting_id)
        count = refresh_meeting_acl(db, meeting=meeting, now=synced_at)
        db.commit()
    assert count == 2
    assert {
        (e.team_id, e.agent_user_id, e.created_by_user_id, _utc(e.acl_synced_at))
        for e in _entries(meeting_id)
    } == {(team_id, agent_id, tenant["user_id"], synced_at)}
    assert _entry_state(other_id) == other_before


def test_access_is_live_for_reattribution_deletion_and_ended_assignments(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "t2-b7")
    workspace_id = tenant["workspace_id"]
    owner_id = tenant["user_id"]
    agent_id = _member(workspace_id, "b7-agent")
    supervisor_id = _member(workspace_id, "b7-supervisor")
    other_id = _member(workspace_id, "b7-other")
    team_id = _team(workspace_id, "b7")
    assignment_id = _supervise(workspace_id, team_id, supervisor_id, owner_id)
    _team_scope(workspace_id)
    _, call_id = _processed(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "t2-b7-call",
        FA,
        EN,
        tenant=tenant,
        attribution={"team_id": team_id, "agent_user_id": agent_id},
    )
    _, own_id = _processed(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "t2-b7-own",
        FA,
        EN,
        tenant=tenant,
        attribution={"agent_user_id": other_id},
    )
    entries = _indexed(workspace_id, call_id) + _indexed(workspace_id, own_id)
    provider = _FakeEmbeddings()
    version = _embed(workspace_id, provider, [e.id for e in entries]).embedding_version
    vector = provider.vector(_by_text(entries, "zebra")[0].text)

    def seen(user_id: str, *, include_history: bool = False) -> tuple[set, set]:
        access = _resolved(workspace_id, user_id)
        lexical = _lexical(workspace_id, access, "zebra", include_history=include_history)
        nearest = _nearest(
            workspace_id, access, version, vector, k=100, include_history=include_history
        )
        return {r.meeting_id for r in lexical}, {r.meeting_id for r in nearest}

    assert seen(owner_id) == ({call_id, own_id}, {call_id, own_id})
    assert seen(agent_id) == ({call_id}, {call_id})
    assert seen(supervisor_id) == ({call_id}, {call_id})
    assert seen(other_id) == ({own_id}, {own_id})

    _write(Meeting, call_id, team_id=None)
    assert {e.team_id for e in _entries(call_id)} == {team_id}
    assert seen(supervisor_id) == (set(), set())
    assert seen(agent_id) == ({call_id}, {call_id})
    _write(Meeting, call_id, team_id=team_id)
    assert seen(supervisor_id) == ({call_id}, {call_id})

    _write(TeamAssignment, assignment_id, revoked_at=_now() - timedelta(seconds=1))
    assert seen(supervisor_id) == (set(), set())
    assert seen(supervisor_id, include_history=True) == (set(), set())

    _write(Meeting, call_id, agent_user_id=other_id)
    assert {e.agent_user_id for e in _entries(call_id)} == {agent_id}
    assert seen(agent_id) == (set(), set())
    assert seen(agent_id, include_history=True) == (set(), set())
    assert seen(other_id) == ({call_id, own_id}, {call_id, own_id})

    _write(Meeting, call_id, deleted_at=_now())
    assert all(e.team_id == team_id for e in _entries(call_id))
    assert seen(owner_id) == ({own_id}, {own_id})
    assert seen(other_id) == ({own_id}, {own_id})
    assert seen(owner_id, include_history=True) == ({own_id}, {own_id})


def test_a_redaction_policy_change_acts_at_once_without_a_reindex(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "t2-b14")
    workspace_id = tenant["workspace_id"]
    owner = _everyone(tenant["user_id"])
    classes = _workspace_classes(workspace_id)
    assert classes and {c.value for c in classes} != {"phone"}
    _, raw_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-b14-raw", PII, FA, tenant=tenant
    )
    raw_entries = _indexed(workspace_id, raw_id)
    raw_ids = {e.id for e in raw_entries}
    provider = _FakeEmbeddings()
    version = _embed(workspace_id, provider, raw_ids).embedding_version
    fa_vector = provider.vector(_by_text(raw_entries, "زرافه")[0].text)
    pii_vector = provider.vector(_by_text(raw_entries, "0912")[0].text)

    def found(query: str, **options) -> set[str]:
        return {r.meeting_id for r in _lexical(workspace_id, owner, query, **options)}

    def near(vector, **options) -> set[str]:
        results = _nearest(workspace_id, owner, version, vector, k=100, **options)
        return {r.entry_id for r in results}

    for query in ("0912", "۰۹۱۲", "زرافه"):
        assert found(query) == {raw_id}
    assert near(pii_vector) == raw_ids
    before = _entry_state(raw_id)

    _write(Workspace, workspace_id, pii_redaction_enabled=True)
    assert _entry_state(raw_id) == before
    for query in ("0912", "۰۹۱۲", "زرافه"):
        assert found(query) == set()
        assert found(query, include_history=True) == set()
    assert near(pii_vector) == set()
    assert near(pii_vector, include_history=True) == set()

    _, redacted_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-b14-redacted", PII, FA, tenant=tenant
    )
    redacted_entries = _indexed(workspace_id, redacted_id)
    redacted_ids = {e.id for e in redacted_entries}
    assert len(redacted_ids) == 2
    assert not any("0912" in e.text or "ali.rezaei" in e.text for e in redacted_entries)
    _embed(workspace_id, provider, redacted_ids)
    assert found("زرافه") == {redacted_id}
    assert found("0912") == set()
    assert near(fa_vector) == redacted_ids

    _write(Workspace, workspace_id, pii_redaction_classes=["phone"])
    assert found("زرافه") == set()
    assert near(fa_vector) == set()
    _write(Workspace, workspace_id, pii_redaction_classes=[c.value for c in classes])
    assert found("زرافه") == {redacted_id}

    _write(Workspace, workspace_id, pii_redaction_enabled=False)
    assert found("0912") == {raw_id}
    assert found("زرافه") == {raw_id}
    assert near(fa_vector) == raw_ids

    _write(Workspace, workspace_id, pii_redaction_enabled=True)
    view_id = _derive_view(workspace_id, raw_id, settings, classes=classes)
    outcome = _index(workspace_id, view_id)
    assert (outcome.state, outcome.removed_count) == ("indexed", 2)
    assert found("زرافه") == {raw_id, redacted_id}
    assert found("0912") == set() and found("ali.rezaei@example.com") == set()


def _two_indexed_tenants(client, email_outbox, monkeypatch, settings, label: str):
    tenants = []
    provider = _FakeEmbeddings()
    version = None
    for side in ("a", "b"):
        tenant, meeting_id = _processed(
            client, email_outbox, monkeypatch, settings, f"{label}-{side}", FA, EN
        )
        entries = _indexed(tenant["workspace_id"], meeting_id)
        version = _embed(
            tenant["workspace_id"], provider, [e.id for e in entries]
        ).embedding_version
        tenants.append((tenant, meeting_id, entries))
    return tenants, provider, version


def test_a_search_in_one_workspace_never_returns_another_workspaces_entries(
    client, email_outbox, monkeypatch, settings
):
    tenants, provider, version = _two_indexed_tenants(
        client, email_outbox, monkeypatch, settings, "t2-b8"
    )
    vector = provider.vector(_by_text(tenants[0][2], "zebra")[0].text)
    for tenant, meeting_id, entries in tenants:
        workspace_id = tenant["workspace_id"]
        owner = _everyone(tenant["user_id"])
        own = {entry.id for entry in entries}
        lexical = _lexical(workspace_id, owner, "zebra")
        nearest = _nearest(workspace_id, owner, version, vector, k=100)
        assert lexical and {r.meeting_id for r in lexical} == {meeting_id}
        assert nearest and {r.meeting_id for r in nearest} == {meeting_id}
        assert {r.entry_id for r in lexical + nearest} <= own


@pytest.mark.postgres
def test_under_row_level_security_a_bound_workspace_reads_and_embeds_nothing_of_another(
    client, email_outbox, monkeypatch, settings
):
    tenants, provider, version = _two_indexed_tenants(
        client, email_outbox, monkeypatch, settings, "t2-b8-rls"
    )
    (one, one_meeting, one_entries), (two, _two_meeting, _) = tenants
    vector = provider.vector(_by_text(one_entries, "zebra")[0].text)
    owner = _everyone(one["user_id"])
    assert _lexical(one["workspace_id"], owner, "zebra", bound=two["workspace_id"]) == []
    assert (
        _nearest(one["workspace_id"], owner, version, vector, bound=two["workspace_id"], k=100)
        == []
    )
    assert {r.meeting_id for r in _lexical(one["workspace_id"], owner, "zebra")} == {one_meeting}
    before = len(_embeddings(workspace_id=one["workspace_id"]))
    other_model = _FakeEmbeddings(model="oracle-embed-rls")
    outcome = _embed(
        one["workspace_id"],
        other_model,
        [e.id for e in one_entries],
        bound=two["workspace_id"],
    )
    assert outcome.written == 0 and other_model.sent() == []
    assert len(_embeddings(workspace_id=one["workspace_id"])) == before


def test_embedding_writes_one_row_per_live_entry_and_a_second_run_writes_nothing(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _processed(client, email_outbox, monkeypatch, settings, "t2-b9", *TEXTS)
    workspace_id = tenant["workspace_id"]
    entries = _indexed(workspace_id, meeting_id)
    assert len(entries) == 4
    provider = _FakeEmbeddings()
    written_at = _now().replace(microsecond=0)
    with _statements() as first_run:
        outcome = _embed(
            workspace_id, provider, [e.id for e in entries], now=written_at, batch_size=3
        )
    assert outcome.state != "failed" and outcome.written == 4
    assert outcome.embedding_version == _expected_version(provider.identity(), DIMENSIONS)
    assert set(provider.sent()) == {entry.text for entry in entries}
    assert all(0 < len(call) <= 3 for call in provider.calls)
    rows = _embeddings(meeting_id=meeting_id)
    by_entry = {entry.id: entry for entry in entries}
    assert sorted(row.entry_id for row in rows) == sorted(by_entry)
    for row in rows:
        entry = by_entry[row.entry_id]
        expected = provider.vector(entry.text)
        assert (row.workspace_id, row.meeting_id) == (entry.workspace_id, entry.meeting_id)
        assert (row.workspace_id, row.meeting_id) == (workspace_id, meeting_id)
        assert row.embedding_version == outcome.embedding_version
        assert (row.provider, row.model, row.dimensions) == (
            "oracle-fake-embeddings",
            "oracle-embed-a",
            DIMENSIONS,
        )
        assert bytes(row.vector) == struct.pack(f"<{DIMENSIONS}f", *expected)
        assert row.text_sha256 == entry.text_sha256 == _sha(entry.text)
        assert _utc(row.created_at) == written_at
    again = _FakeEmbeddings()
    with _statements() as second_run:
        second = _embed(workspace_id, again, [e.id for e in entries])
    assert second.state != "failed" and second.written == 0
    assert second.embedding_version == outcome.embedding_version
    assert again.sent() == []
    assert [
        (row.id, bytes(row.vector), _utc(row.created_at))
        for row in _embeddings(meeting_id=meeting_id)
    ] == [(row.id, bytes(row.vector), _utc(row.created_at)) for row in rows]
    assert _updates_of(first_run + second_run, "retrieval_embeddings") == []


def test_another_model_or_dimension_count_writes_new_rows_under_a_new_version(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-b9-versions", FA, EN
    )
    workspace_id = tenant["workspace_id"]
    ids = [e.id for e in _indexed(workspace_id, meeting_id)]
    first = _FakeEmbeddings()
    version_a = _embed(workspace_id, first, ids).embedding_version
    rows_a = {row.id for row in _embeddings(meeting_id=meeting_id)}
    assert len(rows_a) == 2
    moved = _FakeEmbeddings(params={"endpoint": "http://embeddings.local:8080/v1"})
    with _statements() as moved_run:
        same = _embed(workspace_id, moved, ids)
    assert same.state != "failed" and same.embedding_version == version_a
    assert same.written == 0 and moved.sent() == []
    assert {row.id for row in _embeddings(meeting_id=meeting_id)} == rows_a
    assert _updates_of(moved_run, "retrieval_embeddings") == []
    other_model = _FakeEmbeddings(model="oracle-embed-b")
    outcome_b = _embed(workspace_id, other_model, ids)
    wider = _FakeEmbeddings(size=16)
    outcome_c = _embed(workspace_id, wider, ids, dimensions=16)
    versions = {version_a, outcome_b.embedding_version, outcome_c.embedding_version}
    assert len(versions) == 3
    assert outcome_b.embedding_version == _expected_version(other_model.identity(), DIMENSIONS)
    assert outcome_c.embedding_version == _expected_version(wider.identity(), 16)
    assert (outcome_b.written, outcome_c.written) == (2, 2)
    rows = _embeddings(meeting_id=meeting_id)
    assert len(rows) == 6 and rows_a <= {row.id for row in rows}
    by_version: dict[str, list[RetrievalEmbedding]] = {}
    for row in rows:
        by_version.setdefault(row.embedding_version, []).append(row)
    assert {version: len(found) for version, found in by_version.items()} == {
        version: 2 for version in versions
    }
    assert {
        (row.model, row.dimensions, len(bytes(row.vector)))
        for row in by_version[outcome_c.embedding_version]
    } == {("oracle-embed-a", 16, 64)}
    assert {row.model for row in by_version[outcome_b.embedding_version]} == {"oracle-embed-b"}


def test_embedding_skips_superseded_unlisted_and_foreign_entries(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-b9-filters", FA, EN
    )
    workspace_id = tenant["workspace_id"]
    old_ids = [e.id for e in _indexed(workspace_id, meeting_id)]
    _revision_id, view_id = _new_revision(workspace_id, meeting_id, settings, NEWER, EN)
    _index(workspace_id, view_id)
    live = [e for e in _entries(meeting_id) if e.superseded_at is None]
    assert len(live) == 2 and not {e.id for e in live} & set(old_ids)
    foreign_tenant, foreign_meeting = _processed(
        client, email_outbox, monkeypatch, settings, "t2-b9-foreign", FA
    )
    foreign_ids = [e.id for e in _indexed(foreign_tenant["workspace_id"], foreign_meeting)]
    provider = _FakeEmbeddings()
    outcome = _embed(workspace_id, provider, old_ids + [live[0].id] + foreign_ids)
    assert outcome.state != "failed" and outcome.written == 1
    assert provider.sent() == [live[0].text]
    assert [row.entry_id for row in _embeddings(workspace_id=workspace_id)] == [live[0].id]
    assert _embeddings(workspace_id=foreign_tenant["workspace_id"]) == []
    rest = _embed(workspace_id, _FakeEmbeddings(), [live[1].id])
    assert rest.written == 1
    assert sorted(row.entry_id for row in _embeddings(workspace_id=workspace_id)) == sorted(
        e.id for e in live
    )


@pytest.mark.parametrize(
    "size",
    [pytest.param(DIMENSIONS + 1, id="longer"), pytest.param(DIMENSIONS - 1, id="shorter")],
)
def test_a_dimension_mismatch_writes_nothing_and_does_not_raise(
    client, email_outbox, monkeypatch, settings, size
):
    tenant, meeting_id = _processed(
        client, email_outbox, monkeypatch, settings, f"t2-b9-mismatch-{size}", FA, EN
    )
    workspace_id = tenant["workspace_id"]
    entries = _indexed(workspace_id, meeting_id)
    ids = [e.id for e in entries]
    wrong = _FakeEmbeddings(size=size)
    outcome = _embed(workspace_id, wrong, ids, dimensions=DIMENSIONS)
    assert (outcome.state, outcome.reason_code) == ("failed", "embedding_dimensions_mismatch")
    assert _embeddings(meeting_id=meeting_id) == []
    late = _FakeEmbeddings(bad_size_from_call=2)
    batched = _embed(workspace_id, late, ids, batch_size=1)
    assert (batched.state, batched.reason_code) == ("failed", "embedding_dimensions_mismatch")
    (second_batch,) = late.calls[1]
    late_entry = next(e for e in entries if e.text == second_batch)
    assert late_entry.id not in {row.entry_id for row in _embeddings(meeting_id=meeting_id)}
    kept = {row.id: bytes(row.vector) for row in _embeddings(meeting_id=meeting_id)}
    with _statements() as retry:
        good = _embed(workspace_id, _FakeEmbeddings(), ids)
    assert good.state != "failed"
    assert sorted(row.entry_id for row in _embeddings(meeting_id=meeting_id)) == sorted(ids)
    after = {row.id: bytes(row.vector) for row in _embeddings(meeting_id=meeting_id)}
    assert {row_id: after[row_id] for row_id in kept} == kept
    assert _updates_of(retry, "retrieval_embeddings") == []


def test_a_provider_error_writes_nothing_and_does_not_raise(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-b9-error", FA, EN
    )
    workspace_id = tenant["workspace_id"]
    ids = [e.id for e in _indexed(workspace_id, meeting_id)]
    failing = _FakeEmbeddings(
        error=ProviderError("embedding_unavailable", "Embeddings are unavailable", retryable=True)
    )
    outcome = _embed(workspace_id, failing, ids)
    assert (outcome.state, outcome.reason_code) == ("failed", "embedding_unavailable")
    assert outcome.written == 0
    assert _embeddings(meeting_id=meeting_id) == []
    good = _embed(workspace_id, _FakeEmbeddings(), ids)
    assert good.state != "failed" and good.written == 2


def test_embedding_version_is_the_sha256_of_the_canonical_identity():
    from backend.retrieval.embeddings import embedding_version

    identity = ProviderIdentity(
        provider="oracle-fake", model="مدل-آزمایشی", params={"b": 2, "a": "یک", "c": None}
    )
    version = embedding_version(identity, dimensions=8, index_version=INDEX_VERSION)
    assert version == _expected_version(identity, 8)
    assert len(version) == 64 and int(version, 16) >= 0
    for params in (
        {"c": None, "a": "یک", "b": 2},
        {"endpoint": "http://embeddings.local:8080/v1"},
        {},
    ):
        moved = ProviderIdentity(provider="oracle-fake", model="مدل-آزمایشی", params=params)
        assert embedding_version(moved, dimensions=8, index_version=INDEX_VERSION) == version
    changed = [
        embedding_version(
            ProviderIdentity(provider="oracle-fake", model="other", params=identity.params),
            dimensions=8,
            index_version=INDEX_VERSION,
        ),
        embedding_version(
            ProviderIdentity(provider="oracle-other", model=identity.model, params={}),
            dimensions=8,
            index_version=INDEX_VERSION,
        ),
        embedding_version(identity, dimensions=16, index_version=INDEX_VERSION),
        embedding_version(identity, dimensions=8, index_version="retrieval-index/2"),
    ]
    assert len({version, *changed}) == 5


def test_pack_and_unpack_round_trip_float32_little_endian():
    from backend.retrieval.embeddings import pack_vector, unpack_vector

    values = [0.5, -1.25, 3.0, 0.0]
    packed = pack_vector(values)
    assert packed == struct.pack("<4f", *values) and len(packed) == 16
    assert unpack_vector(packed, 4) == values
    assert pack_vector(tuple(values)) == packed
    assert pack_vector([1.0]) == b"\x00\x00\x80\x3f"
    assert unpack_vector(pack_vector([0.1]), 1) == [struct.unpack("<f", struct.pack("<f", 0.1))[0]]
    assert unpack_vector(pack_vector([3.0e38, -0.0]), 2) == [
        struct.unpack("<f", struct.pack("<f", 3.0e38))[0],
        -0.0,
    ]
    widest = [float(index % 5) - 2.0 for index in range(4096)]
    assert len(pack_vector(widest)) == 4 * 4096
    assert unpack_vector(pack_vector(widest), 4096) == widest


@pytest.mark.parametrize(
    "values",
    [
        pytest.param([], id="empty"),
        pytest.param([0.0] * 4097, id="4097-values"),
        pytest.param([0.5, float("nan")], id="nan"),
        pytest.param([float("inf")], id="inf"),
        pytest.param([0.5, float("-inf")], id="minus-inf"),
        pytest.param([True], id="true"),
        pytest.param([0.5, False], id="false-among-floats"),
    ],
)
def test_pack_vector_refuses_each_invalid_input(values):
    from backend.retrieval.embeddings import pack_vector

    with pytest.raises(ValueError):
        pack_vector(values)


@pytest.mark.parametrize(
    ("extra", "dimensions"),
    [
        pytest.param(-1, 3, id="one-byte-short"),
        pytest.param(1, 3, id="one-byte-long"),
        pytest.param(0, 4, id="dimensions-too-many"),
        pytest.param(0, 2, id="dimensions-too-few"),
    ],
)
def test_unpack_vector_refuses_a_wrong_length(extra, dimensions):
    from backend.retrieval.embeddings import unpack_vector

    blob = struct.pack("<3f", 1.0, 2.0, 3.0)
    blob = blob[:extra] if extra < 0 else blob + b"\x00" * extra
    with pytest.raises(ValueError):
        unpack_vector(blob, dimensions)
    assert unpack_vector(struct.pack("<3f", 1.0, 2.0, 3.0), 3) == [1.0, 2.0, 3.0]


def _searchable(client, email_outbox, monkeypatch, settings, label: str):
    tenant, meeting_id = _processed(client, email_outbox, monkeypatch, settings, label, *TEXTS)
    workspace_id = tenant["workspace_id"]
    entries = _indexed(workspace_id, meeting_id)
    return workspace_id, _everyone(tenant["user_id"]), meeting_id, entries


def test_lexical_search_matches_a_persian_word_and_an_english_word(
    client, email_outbox, monkeypatch, settings
):
    workspace_id, owner, meeting_id, entries = _searchable(
        client, email_outbox, monkeypatch, settings, "t2-b11"
    )
    (persian,) = _lexical(workspace_id, owner, "زرافه")
    assert (persian.entry_id, persian.meeting_id, persian.historical) == (
        _by_text(entries, "زرافه")[0].id,
        meeting_id,
        False,
    )
    assert persian.score > 0
    english = _lexical(workspace_id, owner, "Sara")
    assert {r.entry_id for r in english} == {e.id for e in _by_text(entries, "Sara")}
    assert len(english) == 2
    with pytest.raises(dataclasses.FrozenInstanceError):
        persian.score = 0.0


def test_lexical_search_normalizes_the_query(client, email_outbox, monkeypatch, settings):
    workspace_id, owner, _meeting_id, entries = _searchable(
        client, email_outbox, monkeypatch, settings, "t2-normalize"
    )
    (ali,) = _by_text(entries, "علی")
    (digits,) = _by_text(entries, "10")
    english = {e.id for e in _by_text(entries, "Sara")}
    assert [r.entry_id for r in _lexical(workspace_id, owner, "علي")] == [ali.id]
    assert [r.entry_id for r in _lexical(workspace_id, owner, "علي", language="fa")] == [ali.id]
    assert _lexical(workspace_id, owner, "علي", language="en") == []
    assert [r.entry_id for r in _lexical(workspace_id, owner, "۱۰")] == [digits.id]
    assert [r.entry_id for r in _lexical(workspace_id, owner, "10")] == [digits.id]
    for spelling in ("SARA", "sara", "Sara"):
        assert {r.entry_id for r in _lexical(workspace_id, owner, spelling)} == english


def test_lexical_search_needs_every_token_and_escapes_like_wildcards(
    client, email_outbox, monkeypatch, settings
):
    workspace_id, owner, _meeting_id, entries = _searchable(
        client, email_outbox, monkeypatch, settings, "t2-tokens"
    )
    english = {e.id for e in _by_text(entries, "zebra")}
    assert {r.entry_id for r in _lexical(workspace_id, owner, "Sara zebra")} == english
    assert {r.entry_id for r in _lexical(workspace_id, owner, "zebra")} == english
    assert _lexical(workspace_id, owner, "Sara giraffe") == []
    for wildcard in ("z_bra", "zeb%", "%", "_"):
        assert _lexical(workspace_id, owner, wildcard) == []


def test_an_empty_query_returns_nothing(client, email_outbox, monkeypatch, settings):
    workspace_id, owner, _meeting_id, _entries_found = _searchable(
        client, email_outbox, monkeypatch, settings, "t2-empty"
    )
    for query in ("", "   ", BLANK, " ‌ "):
        assert _lexical(workspace_id, owner, query) == []
        assert _lexical(workspace_id, owner, query, include_history=True) == []
    assert len(_lexical(workspace_id, owner, "زرافه")) == 1


def test_lexical_search_orders_by_score_then_entry_id_and_honours_limit(
    client, email_outbox, monkeypatch, settings
):
    workspace_id, owner, _meeting_id, entries = _searchable(
        client, email_outbox, monkeypatch, settings, "t2-order"
    )
    contract = _lexical(workspace_id, owner, "قرارداد")
    assert {r.entry_id for r in contract} == {e.id for e in _by_text(entries, "قرارداد")}
    assert len(contract) == 2 and _ordered(contract)
    twins = _lexical(workspace_id, owner, "zebra")
    assert len(twins) == 2 and twins[0].score == twins[1].score
    assert [r.entry_id for r in twins] == sorted(e.id for e in _by_text(entries, "zebra"))
    assert _lexical(workspace_id, owner, "قرارداد", limit=1) == contract[:1]
    assert _lexical(workspace_id, owner, "قرارداد", limit=100) == contract
    if not _on_postgres():
        assert {r.score for r in contract} == {1.0}
        (both,) = _lexical(workspace_id, owner, "قرارداد زرافه")
        assert both.score == 2.0
    for limit in (0, -1, 101):
        with pytest.raises(ValueError):
            _lexical(workspace_id, owner, "قرارداد", limit=limit)


@pytest.mark.postgres
def test_the_full_text_gin_index_exists_on_postgres(client):
    with inspection_session_factory()() as db:
        definition = db.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE tablename = 'retrieval_entries' "
                "AND indexname = 'ix_retrieval_entry_text_fts'"
            )
        ).scalar_one()
    assert "USING gin" in definition
    assert "to_tsvector('simple'::regconfig, text)" in definition


@pytest.mark.postgres
def test_postgres_lexical_search_uses_the_full_text_tokens(
    client, email_outbox, monkeypatch, settings
):
    workspace_id, owner, _meeting_id, entries = _searchable(
        client, email_outbox, monkeypatch, settings, "t2-fts"
    )
    assert len(_lexical(workspace_id, owner, "Sara")) == 2
    assert [r.entry_id for r in _lexical(workspace_id, owner, "زرافه")] == [
        e.id for e in _by_text(entries, "زرافه")
    ]
    for prefix in ("Sar", "زراف", "zeb"):
        assert _lexical(workspace_id, owner, prefix) == []


def test_nearest_scores_by_cosine_and_orders_by_score_then_entry_id(
    client, email_outbox, monkeypatch, settings
):
    workspace_id, owner, meeting_id, entries = _searchable(
        client, email_outbox, monkeypatch, settings, "t2-nearest"
    )
    provider = _FakeEmbeddings()
    version = _embed(workspace_id, provider, [e.id for e in entries]).embedding_version
    by_id = {e.id: e for e in entries}
    query = provider.vector(_by_text(entries, "zebra")[0].text)
    results = _nearest(workspace_id, owner, version, query)
    assert sorted(r.entry_id for r in results) == sorted(by_id)
    assert _ordered(results)
    for result in results:
        expected = _cosine(query, provider.vector(by_id[result.entry_id].text))
        assert result.score == pytest.approx(expected, abs=1e-5)
        assert (result.meeting_id, result.historical) == (meeting_id, False)
    twins = sorted(e.id for e in _by_text(entries, "zebra"))
    assert [r.entry_id for r in results[:2]] == twins
    assert results[0].score == pytest.approx(1.0, abs=1e-5)
    assert _nearest(workspace_id, owner, version, query, k=1) == results[:1]
    assert len(_nearest(workspace_id, owner, version, query, candidate_limit=1)) <= 1
    assert _nearest(workspace_id, owner, _sha("no such embedding space"), query) == []
    other = _FakeEmbeddings(model="oracle-embed-other")
    other_version = _embed(workspace_id, other, list(by_id)).embedding_version
    moved = _nearest(workspace_id, owner, other_version, query)
    for result in moved:
        expected = _cosine(query, other.vector(by_id[result.entry_id].text))
        assert result.score == pytest.approx(expected, abs=1e-5)


def test_nearest_validates_its_bounds_and_query_length(client, email_outbox, monkeypatch, settings):
    workspace_id, owner, _meeting_id, entries = _searchable(
        client, email_outbox, monkeypatch, settings, "t2-bounds"
    )
    provider = _FakeEmbeddings()
    version = _embed(workspace_id, provider, [e.id for e in entries]).embedding_version
    query = provider.vector(entries[0].text)
    for options in ({"k": 0}, {"k": 101}, {"candidate_limit": 0}, {"candidate_limit": 5001}):
        with pytest.raises(ValueError):
            _nearest(workspace_id, owner, version, query, **options)
    for wrong in (query[:-1], [*query, 0.5]):
        with pytest.raises(ValueError):
            _nearest(workspace_id, owner, version, wrong)
    assert len(_nearest(workspace_id, owner, version, query, k=1)) == 1
    assert len(_nearest(workspace_id, owner, version, query, k=100)) == 4
    assert len(_nearest(workspace_id, owner, version, query, candidate_limit=5000)) == 4
    assert len(_nearest(workspace_id, owner, version, tuple(query), candidate_limit=1)) == 1


def test_the_purge_removes_the_entries_and_embeddings_of_that_meeting_only(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "t2-purge")
    workspace_id = tenant["workspace_id"]
    _, meeting_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-purge-call", FA, EN, tenant=tenant
    )
    _, kept_id = _processed(
        client, email_outbox, monkeypatch, settings, "t2-purge-kept", FA, tenant=tenant
    )
    entries = _indexed(workspace_id, meeting_id) + _indexed(workspace_id, kept_id)
    _embed(workspace_id, _FakeEmbeddings(), [e.id for e in entries])
    kept_entries = _entry_state(kept_id)
    kept_embeddings = [row.id for row in _embeddings(meeting_id=kept_id)]
    assert len(_embeddings(meeting_id=meeting_id)) == 2 and len(kept_embeddings) == 1
    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )
    assert deleted.status_code == 204
    assert _entries(meeting_id) == [] and _embeddings(meeting_id=meeting_id) == []
    assert _entry_state(kept_id) == kept_entries
    assert [row.id for row in _embeddings(meeting_id=kept_id)] == kept_embeddings
