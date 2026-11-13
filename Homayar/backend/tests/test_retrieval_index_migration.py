"""HOM-93 PR 1: the retrieval index tables and their one migration.

Two tables, nothing writes them yet.  ``retrieval_entries`` holds one row per
indexed text-view segment: the normalized lexical text (a copy of
``text_view_segments.text``, ADR 0034), its digest, the segment's revision
metadata and a snapshot of the record ACL (``team_id``, ``agent_user_id``,
``created_by_user_id``, the exact fields of ``RecordAccess.meeting_clause``).
``retrieval_embeddings`` holds versioned float32 vectors of an entry, portable
bytes because ``postgres:17-alpine`` has no pgvector.  The indexer, the
embedding writer and the vector store adapter are PR 2.  The tests check:

* the ORM declares the columns, the named constraints, the foreign keys with
  their ``ondelete`` actions and the indexes of the design, the partial live
  index with the same WHERE clause on both dialects, and the PostgreSQL-only
  GIN expression index with the same expression as the migration;
* ``database/rls.py`` lists both tables as workspace tables with the tenant
  policy, never through a share token, and ``retrieval_embeddings`` as a
  no-update table (a vector is written once), while ``retrieval_entries``
  keeps UPDATE for PR 2;
* every deny case of the SPEC is refused by its named constraint and the
  boundary allow case passes (SQLite and PostgreSQL): the sequence, the
  revision number, the text digest length, the dimensions bounds, the exact
  vector byte size and the embedding version length;
* duplicates refuse: a second entry of the same segment and index version, a
  second embedding of the same entry and embedding version; the allow
  controls (another index version, another embedding version) pass;
* cascades: deleting the meeting, the revision, the text view or the
  text-view segment removes the entries and their embeddings; deleting a team
  or a user clears the snapshot column and keeps the entry;
* on SQLite, the migration builds the same tables as the ORM, its downgrade
  removes them and nothing else, no full-text index exists there, and alembic
  upgrades from the previous head to this one, checks clean and downgrades
  one step;
* on PostgreSQL: the ORM schema and the migrated schema are the same;
  downgrade from the head removes both tables and leaves every other table as
  it was; upgrade recreates them; the GIN index exists and serves a
  ``to_tsvector('simple', text)`` query with ``enable_seqscan = off``;
  tenant isolation on read and write; nothing visible with no workspace
  bound; the application role may INSERT, SELECT and DELETE embeddings but
  not UPDATE them (permission denied), and may UPDATE an entry's
  ``superseded_at``;
* the conversation purge deletes and counts the embeddings before the
  entries and the entries before the text view rows whose cascades would
  otherwise remove them uncounted, and leaves another meeting's rows; the
  backup reconciliation counts both tables; the code has one migration head,
  this one; ADR 0068 records the design.
"""

from __future__ import annotations

import hashlib
import importlib
import re
from datetime import UTC, datetime, timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, delete, event, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from backend.backup.reconcile import CONTENT_TABLES as RECONCILED_TABLES
from backend.config import Settings
from backend.database.model_registry import Base
from backend.database.relational import get_session_factory
from backend.database.rls import (
    APP_ROLE,
    APPEND_ONLY_TABLES,
    NO_DELETE_TABLES,
    NO_UPDATE_TABLES,
    POLICIES,
    POLICY_NAME,
    SHARED_CONTENT_TABLES,
    TENANT,
    UNPROTECTED_TABLES,
    WORKSPACE_TABLES,
)
from backend.database.saas_models import (
    Meeting,
    RetrievalEmbedding,
    RetrievalEntry,
    ShareAudience,
    ShareLink,
    StageState,
    Team,
    TextViewSegment,
    TranscriptImmutableError,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptSource,
    TranscriptTextView,
    User,
)
from backend.database.tenant_context import bind_tenant_context
from backend.services.conversation_purge_service import _remove_content
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user, register_user
from backend.tests.test_hom129_mfa_migration import remove_privileged_mfa_schema

NEW_REVISION = "a3c7e19d4b62"
BASE_REVISION = "e8b4d2f7a9c1"  # HOM-128: federation group mappings
HEAD_REVISION = "d3d1c7aa3cd8"  # HOM-129: privileged MFA and break-glass, after HOM-93
ENTRIES = "retrieval_entries"
EMBEDDINGS = "retrieval_embeddings"
TABLES = (ENTRIES, EMBEDDINGS)
FTS_INDEX = "ix_retrieval_entry_text_fts"
FTS_EXPRESSION = "to_tsvector('simple', text)"
LIVE_INDEX = "ix_retrieval_entry_live"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
TEXT = "The normalized text of one segment."
INDEX_VERSION = "retrieval-index/1"
EMBEDDING_VERSION = "cd" * 32
TEXT_SHA256 = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
OTHER_VERSION = "ab" * 32
# The check strings of the SPEC, character for character.
ENTRY_CHECKS = {
    "ck_retrieval_entry_sequence": "sequence >= 0",
    "ck_retrieval_entry_revision_number": "revision_number >= 1",
    "ck_retrieval_entry_text_sha256": "length(text_sha256) = 64",
}
EMBEDDING_CHECKS = {
    "ck_retrieval_embedding_dimensions": "dimensions >= 1 AND dimensions <= 4096",
    "ck_retrieval_embedding_vector_size": "length(vector) = 4 * dimensions",
    "ck_retrieval_embedding_text_sha256": "length(text_sha256) = 64",
    "ck_retrieval_embedding_version": "length(embedding_version) = 64",
}
# The partial live index's WHERE clause, character for character.
LIVE_WHERE = "superseded_at IS NULL"
# SQLite names the columns of a broken unique constraint, not the constraint.
UNIQUE_COLUMNS = {
    "uq_retrieval_entry_segment_version": (ENTRIES, ("text_view_segment_id", "index_version")),
    "uq_retrieval_embedding_entry_version": (EMBEDDINGS, ("entry_id", "embedding_version")),
}


def _on_postgres() -> bool:
    return is_postgres_url(configured_database_url())


def _requires_postgres() -> None:
    if not _on_postgres():
        pytest.skip("needs PostgreSQL")


def _scalar(sql: str, **params):
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


def _tenant(client, email_outbox, label: str) -> dict:
    credentials = register_user(client, email_outbox, email=f"retrieval-{label}@example.com")
    session = login_user(client, credentials)
    return {"user_id": session["user"]["id"], "workspace_id": session["workspaces"][0]["id"]}


def _add(model, values: dict[str, object]) -> str:
    """Insert one row through the ORM as the test login role and commit it."""
    with inspection_session_factory()() as db:
        row = model(**values)
        db.add(row)
        db.commit()
        return row.id


def _indexed(tenant: dict, key: str) -> dict:
    """A conversation with a transcript revision, one segment, a completed text
    view and one normalized text-view segment: the smallest chain one entry needs."""
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=tenant["workspace_id"],
            created_by_user_id=tenant["user_id"],
            title=f"Indexed call {key}",
        )
        db.add(meeting)
        db.flush()
        revision = TranscriptRevision(
            workspace_id=tenant["workspace_id"],
            meeting_id=meeting.id,
            revision_number=1,
            source=TranscriptSource.ASR,
            reason="processing",
            language="fa",
            segment_count=1,
            is_current=True,
        )
        db.add(revision)
        db.flush()
        segment = TranscriptSegment(
            workspace_id=tenant["workspace_id"],
            meeting_id=meeting.id,
            revision_id=revision.id,
            sequence=0,
            speaker_key="agent",
            start_ms=0,
            end_ms=1500,
            text=TEXT,
        )
        db.add(segment)
        db.flush()
        view = TranscriptTextView(
            workspace_id=tenant["workspace_id"],
            meeting_id=meeting.id,
            revision_id=revision.id,
            input_view="original",
            language="fa",
            normalizer_version="fa-normalizer/1",
            extractor_version="entities/1",
            detector_version="pii-detector/1",
            view_digest=f"{hashlib.sha256(key.encode()).hexdigest()}",
            reference_at=NOW,
            timezone="Asia/Tehran",
            state=StageState.COMPLETED,
            segment_count=1,
            entity_count=0,
        )
        db.add(view)
        db.flush()
        text_segment = TextViewSegment(
            workspace_id=tenant["workspace_id"],
            meeting_id=meeting.id,
            view_id=view.id,
            segment_id=segment.id,
            sequence=0,
            text=TEXT,
            entity_count=0,
        )
        db.add(text_segment)
        db.commit()
        return {
            **tenant,
            "meeting_id": meeting.id,
            "revision_id": revision.id,
            "segment_id": segment.id,
            "text_view_id": view.id,
            "text_view_segment_id": text_segment.id,
        }


def _entry(setup: dict, **extra: object) -> dict[str, object]:
    """A valid entry of the setup's segment, live when indexed."""
    return {
        "workspace_id": setup["workspace_id"],
        "meeting_id": setup["meeting_id"],
        "revision_id": setup["revision_id"],
        "text_view_id": setup["text_view_id"],
        "text_view_segment_id": setup["text_view_segment_id"],
        "segment_id": setup["segment_id"],
        "sequence": 0,
        "revision_number": 1,
        "language": "fa",
        "text": TEXT,
        "text_sha256": TEXT_SHA256,
        "index_version": INDEX_VERSION,
        "acl_synced_at": NOW,
        **extra,
    }


def _embedding(setup: dict, entry_id: str, **extra: object) -> dict[str, object]:
    """A valid embedding of one entry: two float32 values, 8 bytes."""
    return {
        "workspace_id": setup["workspace_id"],
        "meeting_id": setup["meeting_id"],
        "entry_id": entry_id,
        "embedding_version": EMBEDDING_VERSION,
        "provider": "local-embeddings",
        "model": "bge-m3",
        "dimensions": 2,
        "vector": b"\x00\x00\x80\x3f" * 2,
        "text_sha256": TEXT_SHA256,
        **extra,
    }


def _names(constraint: str) -> str:
    if constraint in UNIQUE_COLUMNS and not _on_postgres():
        table, columns = UNIQUE_COLUMNS[constraint]
        return re.escape(", ".join(f"{table}.{column}" for column in columns))
    return rf"\b{constraint}\b"


def _refused(model, values: dict[str, object], constraint: str, *also: str) -> None:
    """The insert fails on ``constraint``."""
    names = (constraint, *also) if _on_postgres() else (constraint,)
    with inspection_session_factory()() as db:
        db.add(model(**values))
        with pytest.raises(IntegrityError, match="|".join(_names(name) for name in names)):
            db.flush()
        db.rollback()


def _ids(table: str, **where: str) -> set[str]:
    clauses = " AND ".join(f"{column} = :{column}" for column in where) or "1 = 1"
    with inspection_session_factory()() as db:
        return set(
            db.execute(text(f"SELECT id FROM {table} WHERE {clauses}"), where).scalars()  # noqa: S608
        )


# --- the deny and allow cases (SPEC acceptance criterion A6) ---------------------------------


def _assert_sequence_rules(tenant: dict) -> None:
    setup = _indexed(tenant, "sequence")
    _refused(RetrievalEntry, _entry(setup, sequence=-1), "ck_retrieval_entry_sequence")
    # Allow-control: the boundary value.
    _add(RetrievalEntry, _entry(setup, sequence=0))


def _assert_revision_number_rules(tenant: dict) -> None:
    setup = _indexed(tenant, "revision_number")
    _refused(RetrievalEntry, _entry(setup, revision_number=0), "ck_retrieval_entry_revision_number")
    _add(RetrievalEntry, _entry(setup, revision_number=1))


def _assert_text_sha256_rules(tenant: dict) -> None:
    setup = _indexed(tenant, "text_sha256")
    _refused(RetrievalEntry, _entry(setup, text_sha256="a" * 63), "ck_retrieval_entry_text_sha256")
    _add(RetrievalEntry, _entry(setup, text_sha256="a" * 64))


def _assert_dimensions_rules(tenant: dict) -> None:
    setup = _indexed(tenant, "dimensions")
    entry_id = _add(RetrievalEntry, _entry(setup))
    # A byte count that fits the value, so only the bound refuses it.
    _refused(
        RetrievalEmbedding,
        _embedding(setup, entry_id, dimensions=0, vector=b""),
        "ck_retrieval_embedding_dimensions",
    )
    _refused(
        RetrievalEmbedding,
        _embedding(setup, entry_id, dimensions=4097, vector=b"\x00" * (4 * 4097)),
        "ck_retrieval_embedding_dimensions",
    )
    # Allow-controls: both bounds with their exact byte count.
    _add(
        RetrievalEmbedding,
        _embedding(
            setup, entry_id, embedding_version=OTHER_VERSION, dimensions=1, vector=b"\x00\x00\x80?"
        ),
    )
    _add(
        RetrievalEmbedding,
        _embedding(
            setup,
            entry_id,
            embedding_version="ba" * 32,
            dimensions=4096,
            vector=b"\x00" * (4 * 4096),
        ),
    )


def _assert_vector_size_rules(tenant: dict) -> None:
    setup = _indexed(tenant, "vector_size")
    entry_id = _add(RetrievalEntry, _entry(setup))
    for size in (4 * 2 - 1, 4 * 2 + 1):
        _refused(
            RetrievalEmbedding,
            _embedding(setup, entry_id, vector=b"\x00" * size),
            "ck_retrieval_embedding_vector_size",
        )
    # Allow-control: the exact size.
    _add(
        RetrievalEmbedding,
        _embedding(setup, entry_id, embedding_version=OTHER_VERSION, vector=b"\x00" * 8),
    )


def _assert_embedding_text_sha256_rules(tenant: dict) -> None:
    setup = _indexed(tenant, "embedding_text_sha256")
    entry_id = _add(RetrievalEntry, _entry(setup))
    _refused(
        RetrievalEmbedding,
        _embedding(setup, entry_id, text_sha256="a" * 63),
        "ck_retrieval_embedding_text_sha256",
    )
    # Allow-control: a full sha256 hex digest.
    _add(RetrievalEmbedding, _embedding(setup, entry_id, text_sha256="a" * 64))


def _assert_embedding_version_rules(tenant: dict) -> None:
    setup = _indexed(tenant, "embedding_version")
    entry_id = _add(RetrievalEntry, _entry(setup))
    _refused(
        RetrievalEmbedding,
        _embedding(setup, entry_id, embedding_version="a" * 63),
        "ck_retrieval_embedding_version",
    )
    _add(RetrievalEmbedding, _embedding(setup, entry_id, embedding_version="a" * 64))


RULES = (
    _assert_sequence_rules,
    _assert_revision_number_rules,
    _assert_text_sha256_rules,
    _assert_dimensions_rules,
    _assert_vector_size_rules,
    _assert_embedding_text_sha256_rules,
    _assert_embedding_version_rules,
)


@pytest.mark.parametrize(
    "rules",
    RULES,
    ids=lambda rules: rules.__name__.removeprefix("_assert_"),
)
def test_the_database_refuses_the_deny_cases_and_takes_the_allow_cases(client, email_outbox, rules):
    rules(_tenant(client, email_outbox, "rules"))


# --- the duplicates and their allow-controls (SPEC acceptance criterion A7) -------------------


def test_a_second_entry_of_the_same_segment_and_index_version_is_refused(client, email_outbox):
    tenant = _tenant(client, email_outbox, "duplicate_entries")
    setup = _indexed(tenant, "duplicate_entries")
    _add(RetrievalEntry, _entry(setup))
    _refused(
        RetrievalEntry,
        _entry(setup, text="Another normalization of the same segment."),
        "uq_retrieval_entry_segment_version",
    )
    # Allow-control: the same segment under another index version.
    _add(
        RetrievalEntry,
        _entry(setup, index_version="retrieval-index/2", text="Another normalization."),
    )


def test_a_second_embedding_of_the_same_entry_and_version_is_refused(client, email_outbox):
    tenant = _tenant(client, email_outbox, "duplicate_embeddings")
    setup = _indexed(tenant, "duplicate_embeddings")
    entry_id = _add(RetrievalEntry, _entry(setup))
    _add(RetrievalEmbedding, _embedding(setup, entry_id))
    _refused(
        RetrievalEmbedding,
        _embedding(setup, entry_id, model="another-model"),
        "uq_retrieval_embedding_entry_version",
    )
    # Allow-control: the same entry under another embedding version.
    _add(
        RetrievalEmbedding,
        _embedding(setup, entry_id, embedding_version=OTHER_VERSION, model="another-model"),
    )


# --- an embedding row is written once, at the ORM guard too (SPEC revision 2, R5) --------------


def test_an_embedding_row_cannot_be_updated_but_a_new_version_row_can(client, email_outbox):
    tenant = _tenant(client, email_outbox, "immutable")
    setup = _indexed(tenant, "immutable")
    entry_id = _add(RetrievalEntry, _entry(setup))
    embedding_id = _add(RetrievalEmbedding, _embedding(setup, entry_id))
    with inspection_session_factory()() as db:
        row = db.get(RetrievalEmbedding, embedding_id)
        row.vector = b"\x00" * 8
        with pytest.raises(TranscriptImmutableError, match="RetrievalEmbedding"):
            db.flush()
        db.rollback()
    # Allow-control: a new version of the same entry is a new row, not an update.
    _add(
        RetrievalEmbedding,
        _embedding(setup, entry_id, embedding_version=OTHER_VERSION, model="another-model"),
    )
    assert len(_ids(EMBEDDINGS, entry_id=entry_id)) == 2


# --- the ORM declares the design ---------------------------------------------------------------

LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "meeting_id": 36,
    "revision_id": 36,
    "text_view_id": 36,
    "text_view_segment_id": 36,
    "segment_id": 36,
    "language": 12,
    "text_sha256": 64,
    "index_version": 40,
    "team_id": 36,
    "agent_user_id": 36,
    "created_by_user_id": 36,
}
NULLABLE = {"team_id", "agent_user_id", "created_by_user_id", "superseded_at"}
ENTRY_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("meeting_id",), ("meetings.id",), "CASCADE"),
    (None, ("revision_id",), ("transcript_revisions.id",), "CASCADE"),
    (None, ("text_view_id",), ("transcript_text_views.id",), "CASCADE"),
    (None, ("text_view_segment_id",), ("text_view_segments.id",), "CASCADE"),
    (None, ("segment_id",), ("transcript_segments.id",), "CASCADE"),
    (None, ("team_id",), ("teams.id",), "SET NULL"),
    (None, ("agent_user_id",), ("users.id",), "SET NULL"),
    (None, ("created_by_user_id",), ("users.id",), "SET NULL"),
}
EMBEDDING_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("meeting_id",), ("meetings.id",), "CASCADE"),
    (None, ("entry_id",), ("retrieval_entries.id",), "CASCADE"),
}
ENTRY_INDEXES = {
    "ix_retrieval_entries_workspace_id": (["workspace_id"], False),
    "ix_retrieval_entries_meeting_id": (["meeting_id"], False),
    "ix_retrieval_entries_revision_id": (["revision_id"], False),
    "ix_retrieval_entries_text_view_id": (["text_view_id"], False),
    "ix_retrieval_entries_segment_id": (["segment_id"], False),
    "ix_retrieval_entry_workspace_meeting": (["workspace_id", "meeting_id"], False),
    LIVE_INDEX: (["workspace_id", "index_version"], False),
    FTS_INDEX: ([], False),
}
EMBEDDING_INDEXES = {
    "ix_retrieval_embeddings_workspace_id": (["workspace_id"], False),
    "ix_retrieval_embeddings_meeting_id": (["meeting_id"], False),
    "ix_retrieval_embeddings_entry_id": (["entry_id"], False),
    "ix_retrieval_embedding_workspace_version": (["workspace_id", "embedding_version"], False),
}


def test_the_orm_declares_the_retrieval_entries_table():
    table = RetrievalEntry.__table__
    assert table.name == ENTRIES
    assert [column.name for column in table.columns] == [
        "id",
        "workspace_id",
        "meeting_id",
        "revision_id",
        "text_view_id",
        "text_view_segment_id",
        "segment_id",
        "sequence",
        "revision_number",
        "language",
        "text",
        "text_sha256",
        "index_version",
        "team_id",
        "agent_user_id",
        "created_by_user_id",
        "acl_synced_at",
        "superseded_at",
        "created_at",
    ]
    lengths = LENGTHS
    for name, _length in lengths.items():
        assert table.c[name].type.length == lengths[name], name
    for name in ("acl_synced_at", "superseded_at", "created_at"):
        assert table.c[name].type.timezone, name
    assert table.c.text.type.__class__.__name__ == "Text"
    assert {column.name for column in table.columns if column.nullable} == NULLABLE
    assert [column.name for column in table.primary_key] == ["id"]

    # The ORM defaults (SQLAlchemy wraps a callable default and keeps its name).
    assert table.c.id.default.arg.__name__ == "new_id"
    assert table.c.created_at.default.arg.__name__ == "utcnow"
    assert all(column.server_default is None for column in table.columns)

    assert {
        (
            constraint.name,
            tuple(constraint.column_keys),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in table.foreign_key_constraints
    } == ENTRY_FOREIGN_KEYS
    named = {constraint.name: constraint for constraint in table.constraints if constraint.name}
    assert {
        name: str(constraint.sqltext)
        for name, constraint in named.items()
        if name.startswith("ck_")
    } == ENTRY_CHECKS
    assert [name for name in named if name.startswith("uq_")] == [
        "uq_retrieval_entry_segment_version"
    ]
    assert {
        index.name: ([column.name for column in index.columns], index.unique)
        for index in table.indexes
    } == ENTRY_INDEXES
    # The partial live index carries the same WHERE clause on both dialects.
    (live,) = (index for index in table.indexes if index.name == LIVE_INDEX)
    for dialect in ("sqlite", "postgresql"):
        assert str(live.dialect_options[dialect]["where"]) == LIVE_WHERE, dialect
    # The full-text index is the GIN expression index, identical to the migration's.
    (fts,) = (index for index in table.indexes if index.name == FTS_INDEX)
    assert str(fts.expressions[0]) == FTS_EXPRESSION
    assert fts.dialect_options["postgresql"]["using"] == "gin"
    assert "HOM-93" in RetrievalEntry.__doc__


def test_the_orm_declares_the_retrieval_embeddings_table():
    table = RetrievalEmbedding.__table__
    assert table.name == EMBEDDINGS
    assert [column.name for column in table.columns] == [
        "id",
        "workspace_id",
        "meeting_id",
        "entry_id",
        "embedding_version",
        "provider",
        "model",
        "dimensions",
        "vector",
        "text_sha256",
        "created_at",
    ]
    for name, length in (
        ("id", 36),
        ("workspace_id", 36),
        ("meeting_id", 36),
        ("entry_id", 36),
        ("embedding_version", 64),
        ("provider", 128),
        ("model", 256),
        ("text_sha256", 64),
    ):
        assert table.c[name].type.length == length, name
    assert table.c.created_at.type.timezone
    assert table.c.vector.type.__class__.__name__ == "LargeBinary"
    assert not any(column.nullable for column in table.columns)
    assert [column.name for column in table.primary_key] == ["id"]

    assert table.c.id.default.arg.__name__ == "new_id"
    assert table.c.created_at.default.arg.__name__ == "utcnow"
    assert all(column.server_default is None for column in table.columns)

    assert {
        (
            constraint.name,
            tuple(constraint.column_keys),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in table.foreign_key_constraints
    } == EMBEDDING_FOREIGN_KEYS
    named = {constraint.name: constraint for constraint in table.constraints if constraint.name}
    assert {
        name: str(constraint.sqltext)
        for name, constraint in named.items()
        if name.startswith("ck_")
    } == EMBEDDING_CHECKS
    assert [name for name in named if name.startswith("uq_")] == [
        "uq_retrieval_embedding_entry_version"
    ]
    assert {
        index.name: ([column.name for column in index.columns], index.unique)
        for index in table.indexes
    } == EMBEDDING_INDEXES
    assert "HOM-93" in RetrievalEmbedding.__doc__


def test_both_tables_are_workspace_tables_and_embeddings_are_written_once():
    for table in TABLES:
        assert table in WORKSPACE_TABLES
        assert table not in APPEND_ONLY_TABLES
        assert table not in NO_DELETE_TABLES
        assert table not in SHARED_CONTENT_TABLES  # never through a share token
        assert table not in UNPROTECTED_TABLES
        assert POLICIES[table] == (f"workspace_id = {TENANT}", f"workspace_id = {TENANT}")
    # PR 2 updates only superseded_at and the ACL snapshot columns of an entry.
    assert ENTRIES not in NO_UPDATE_TABLES
    # A vector is written once; a new model or text gives a new row.
    assert EMBEDDINGS in NO_UPDATE_TABLES


# --- cascades and the ACL snapshot (SPEC acceptance criterion A8) ------------------------------


def test_deleting_the_meeting_the_revision_the_view_or_the_segment_removes_every_row(
    client, email_outbox
):
    second = _tenant(client, email_outbox, "cascade_b")
    first = _tenant(client, email_outbox, "cascade_a")
    by_meeting = _indexed(second, "cascade_meeting")
    by_revision = _indexed(second, "cascade_revision")
    by_view = _indexed(second, "cascade_view")
    by_segment = _indexed(second, "cascade_segment")
    elsewhere = _indexed(first, "cascade_elsewhere")
    rows = {
        key: _add(RetrievalEntry, _entry(setup))
        for key, setup in (
            ("meeting", by_meeting),
            ("revision", by_revision),
            ("view", by_view),
            ("segment", by_segment),
            ("elsewhere", elsewhere),
        )
    }
    embeddings = {
        key: _add(RetrievalEmbedding, _embedding(setup, rows[key]))
        for key, setup in (
            ("meeting", by_meeting),
            ("revision", by_revision),
            ("view", by_view),
            ("segment", by_segment),
            ("elsewhere", elsewhere),
        )
    }

    # Removals by the owner (DBA here): the FK actions run as the table owner.
    with inspection_session_factory()() as db:
        db.execute(delete(Meeting).where(Meeting.id == by_meeting["meeting_id"]))
        db.execute(
            delete(TranscriptRevision).where(TranscriptRevision.id == by_revision["revision_id"])
        )
        db.execute(
            delete(TranscriptTextView).where(TranscriptTextView.id == by_view["text_view_id"])
        )
        db.execute(
            delete(TextViewSegment).where(TextViewSegment.id == by_segment["text_view_segment_id"])
        )
        db.commit()
    assert _ids(ENTRIES) == {rows["elsewhere"]}
    assert _ids(EMBEDDINGS) == {embeddings["elsewhere"]}


def test_deleting_a_team_or_a_user_clears_the_snapshot_and_keeps_the_entry(client, email_outbox):
    tenant = _tenant(client, email_outbox, "acl_snapshot")
    with inspection_session_factory()() as db:
        team = Team(workspace_id=tenant["workspace_id"], name="Sales", slug="sales")
        db.add(team)
        agent = User(email="retrieval-agent@example.com", display_name="Agent")
        db.add(agent)
        db.commit()
        team_id, agent_id = team.id, agent.id
    setup = _indexed(tenant, "acl_snapshot")
    entry_id = _add(
        RetrievalEntry,
        _entry(
            setup, team_id=team_id, agent_user_id=agent_id, created_by_user_id=tenant["user_id"]
        ),
    )

    with inspection_session_factory()() as db:
        db.execute(delete(Team).where(Team.id == team_id))
        db.execute(delete(User).where(User.id == agent_id))
        db.commit()
    with inspection_session_factory()() as db:
        row = db.get(RetrievalEntry, entry_id)
        assert row.team_id is None
        assert row.agent_user_id is None
        # The removed team and agent are gone from the snapshot only; the entry
        # itself stays, with its text and its creator column.
        assert row.created_by_user_id == tenant["user_id"]
        assert row.text == TEXT and row.text_view_segment_id == setup["text_view_segment_id"]


# --- the migration module and the single head (SPEC decisions D5 and A1) -----------------------


def _migration_module():
    return importlib.import_module(f"backend.migrations.versions.{NEW_REVISION}_retrieval_index")


def remove_retrieval_index_tables(connection) -> None:
    """Take a ``create_all`` schema back to before HOM-93.

    Older migration tests build the full ORM, drop the newer tables and stamp an
    older revision.  This helper keeps the drops in one place with the
    migration's own names.
    """
    for name in reversed(TABLES):
        Base.metadata.tables[name].drop(connection)


def test_the_migration_follows_the_previous_head():
    module = _migration_module()
    assert module.revision == NEW_REVISION
    assert module.down_revision == BASE_REVISION
    doc = module.__doc__
    assert doc.splitlines()[0] == (
        "Retrieval index: lexical entries and versioned embeddings, schema only (HOM-93, WP17-T02)"
    )
    for words in ("Why", "No data changes", "Downgrade"):
        assert words in doc, words
    # Frozen copies of the model's check strings and WHERE clauses.
    assert module.ENTRY_CHECKS == ENTRY_CHECKS
    assert module.EMBEDDING_CHECKS == EMBEDDING_CHECKS
    assert str(module.LIVE_WHERE) == LIVE_WHERE
    assert str(module.FTS_EXPRESSION) == FTS_EXPRESSION


def test_the_code_has_one_migration_head_this_revision():
    from backend.observability.release import code_migration_head

    assert code_migration_head() == HEAD_REVISION


def test_alembic_heads_prints_exactly_this_revision():
    import io

    from alembic import command
    from alembic.config import Config

    from backend.observability.release import PROJECT_ROOT

    shown = io.StringIO()
    command.heads(Config(str(PROJECT_ROOT / "alembic.ini"), stdout=shown))
    lines = [line for line in shown.getvalue().splitlines() if line.strip()]
    assert lines == [f"{HEAD_REVISION} (head)"], shown.getvalue()


# --- the migration on SQLite (SPEC acceptance criteria A1, A2 and A3) --------------------------


def _reflect(connection, table: str) -> dict:
    inspector = inspect(connection)
    return {
        "columns": [
            (column["name"], str(column["type"]), column["nullable"])
            for column in inspector.get_columns(table)
        ],
        "primary_key": inspector.get_pk_constraint(table)["constrained_columns"],
        "foreign_keys": sorted(
            (
                key["name"] or "",
                tuple(key["constrained_columns"]),
                key["referred_table"],
                tuple(key["referred_columns"]),
                key["options"].get("ondelete"),
            )
            for key in inspector.get_foreign_keys(table)
        ),
        "unique": inspector.get_unique_constraints(table),
        "checks": sorted(
            (check["name"] or "", check["sqltext"])
            for check in inspector.get_check_constraints(table)
        ),
        "indexes": sorted(
            (index["name"], tuple(index["column_names"]), bool(index["unique"]))
            for index in inspector.get_indexes(table)
        ),
    }


def _objects(connection) -> set[tuple[str, str, str]]:
    """Every table and index of the database, as (type, name, table)."""
    return {
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT type, name, tbl_name FROM sqlite_master WHERE tbl_name <> 'alembic_version'"
            )
        ).all()
    }


def _untouched(shapes: dict) -> dict:
    """Every table this migration does not build or change."""
    return {name: shape for name, shape in shapes.items() if name not in TABLES}


def _partial_sql(connection) -> str:
    """The SQL text SQLite keeps for the partial live index."""
    return connection.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = :n"),
        {"n": LIVE_INDEX},
    ).scalar_one()


def test_on_sqlite_the_migration_builds_the_orm_tables_and_drops_only_them():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            orm = {table: _reflect(connection, table) for table in TABLES}
            orm_partial = _partial_sql(connection)
            for name in reversed(TABLES):
                Base.metadata.tables[name].drop(connection)
            before = {
                name: _reflect(connection, name) for name in inspect(connection).get_table_names()
            }
            before_objects = _objects(connection)

            module = _migration_module()
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
            assert {table: _reflect(connection, table) for table in TABLES} == orm
            assert sorted(name for name, _ in orm[ENTRIES]["checks"]) == sorted(ENTRY_CHECKS)
            assert sorted(name for name, _ in orm[EMBEDDINGS]["checks"]) == sorted(EMBEDDING_CHECKS)
            # The partial live index keeps the same WHERE clause as the ORM's.
            assert _partial_sql(connection) == orm_partial
            # No other table changed.
            after = {
                name: _reflect(connection, name) for name in inspect(connection).get_table_names()
            }
            assert _untouched(after) == _untouched(before)

            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
            final = {
                name: _reflect(connection, name) for name in inspect(connection).get_table_names()
            }
            assert final == before
            assert _objects(connection) == before_objects
            assert not any(name in TABLES for _, name, _ in _objects(connection))
    finally:
        engine.dispose()


def test_on_sqlite_no_full_text_index_exists_and_nothing_fails():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            for name in reversed(TABLES):
                Base.metadata.tables[name].drop(connection)
            module = _migration_module()
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
            names = (
                connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = :t"),
                    {"t": ENTRIES},
                )
                .scalars()
                .all()
            )
            assert FTS_INDEX not in names
            # A plain LIKE fallback still runs without error (PR 2 uses that on
            # SQLite); the table is empty, so it finds nothing.
            rows = connection.execute(
                text(f"SELECT id FROM {ENTRIES} WHERE text LIKE :needle"),  # noqa: S608
                {"needle": "%normalized%"},
            ).all()
            assert rows == []
    finally:
        engine.dispose()


def _alembic_at(monkeypatch, url: str):
    from alembic.config import Config

    from backend.config import reset_settings_cache
    from backend.observability.release import PROJECT_ROOT

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def test_on_sqlite_alembic_upgrades_to_the_head_checks_clean_and_downgrades_one_step(
    tmp_path, monkeypatch
):
    from alembic import command
    from alembic.util.exc import AutogenerateDiffsDetected

    from backend.config import reset_settings_cache

    url = f"sqlite:///{tmp_path / 'retrieval-index.db'}"
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            orm = {table: _reflect(connection, table) for table in TABLES}
            for name in reversed(TABLES):
                Base.metadata.tables[name].drop(connection)
            # HOM-129 adds the MFA and break-glass schema; take it out too.
            remove_privileged_mfa_schema(connection)
            before_objects = _objects(connection)

        config = _alembic_at(monkeypatch, url)
        command.stamp(config, BASE_REVISION)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            assert version.scalar() == HEAD_REVISION
            assert {table: _reflect(connection, table) for table in TABLES} == orm
        try:
            command.check(config)
        except AutogenerateDiffsDetected as exc:
            pytest.fail(f"the migration does not match the ORM: {exc}")

        # Down past HOM-129 to this revision, then one more step.
        command.downgrade(config, NEW_REVISION)
        command.downgrade(config, "-1")
        with engine.connect() as connection:
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            assert version.scalar() == BASE_REVISION
            assert _objects(connection) == before_objects

        # Upgrade again works (SPEC acceptance criterion A1).
        command.upgrade(config, "head")
        with engine.connect() as connection:
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            assert version.scalar() == HEAD_REVISION
    finally:
        engine.dispose()
        reset_settings_cache()


# --- PostgreSQL: the schema, the migration, isolation and grants -------------------------------


def _alembic(monkeypatch, settings: Settings):
    return _alembic_at(monkeypatch, settings.database_url)


def _shape(table: str) -> dict:
    """The table as the catalog shows it: columns, constraints, indexes, policy, grants."""
    with inspection_session_factory()() as db:
        columns = db.execute(
            text(
                "SELECT column_name, data_type, character_maximum_length, is_nullable, "
                "column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table},
        ).all()
        constraints = db.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = to_regclass(:t)"
            ),
            {"t": f"public.{table}"},
        ).all()
        indexes = db.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :t"), {"t": table}
        ).all()
        policies = db.execute(
            text(
                "SELECT policyname, permissive, roles::text, cmd, qual, with_check "
                "FROM pg_policies WHERE tablename = :t"
            ),
            {"t": table},
        ).all()
        flags = db.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = to_regclass(:t)"
            ),
            {"t": f"public.{table}"},
        ).all()
        grants = db.execute(
            text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_schema = 'public' AND table_name = :t AND grantee = :r"
            ),
            {"t": table, "r": APP_ROLE},
        ).all()
    return {
        "columns": {name: tuple(rest) for name, *rest in columns},
        "constraints": dict(constraints),
        "indexes": dict(indexes),
        "policies": {name: tuple(rest) for name, *rest in policies},
        "rls": [tuple(row) for row in flags],
        "grants": sorted(row[0] for row in grants),
    }


EMPTY_SHAPE = {
    "columns": {},
    "constraints": {},
    "indexes": {},
    "policies": {},
    "rls": [],
    "grants": [],
}

OTHER_TABLE_QUERIES = {
    "tables": (
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname NOT IN (:a, :b)"
    ),
    "columns": (
        "SELECT table_name, column_name, data_type, character_maximum_length, "
        "is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name NOT IN (:a, :b)"
    ),
    "constraints": (
        "SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid) "
        "FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = 'public' AND c.conrelid <> 0 "
        "AND c.conrelid::regclass::text NOT IN (:a, :b)"
    ),
    "indexes": (
        "SELECT tablename, indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename NOT IN (:a, :b)"
    ),
    "policies": (
        "SELECT tablename, policyname, permissive, roles::text, cmd, qual, with_check "
        "FROM pg_policies WHERE schemaname = 'public' AND tablename NOT IN (:a, :b)"
    ),
    "grants": (
        "SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema = 'public' AND table_name NOT IN (:a, :b)"
    ),
}


def _other_tables() -> dict:
    """Every other table of the schema, so a test can prove it did not change."""
    excluded = dict(zip("ab", TABLES, strict=True))
    with inspection_session_factory()() as db:
        return {
            name: sorted(tuple(row) for row in db.execute(text(sql), excluded).all())
            for name, sql in OTHER_TABLE_QUERIES.items()
        }


def _varchar(length: int, nullable: str = "NO") -> tuple:
    return ("character varying", length, nullable, None)


def _timestamp(nullable: str = "NO") -> tuple:
    return ("timestamp with time zone", None, nullable, None)


def _plain(kind: str, nullable: str = "NO") -> tuple:
    return (kind, None, nullable, None)


def _bytea() -> tuple:
    return ("bytea", None, "NO", None)


EXPECTED_ENTRY_COLUMNS = {
    "id": _varchar(36),
    "workspace_id": _varchar(36),
    "meeting_id": _varchar(36),
    "revision_id": _varchar(36),
    "text_view_id": _varchar(36),
    "text_view_segment_id": _varchar(36),
    "segment_id": _varchar(36),
    "sequence": _plain("integer"),
    "revision_number": _plain("integer"),
    "language": _varchar(12),
    "text": _plain("text"),
    "text_sha256": _varchar(64),
    "index_version": _varchar(40),
    "team_id": _varchar(36, "YES"),
    "agent_user_id": _varchar(36, "YES"),
    "created_by_user_id": _varchar(36, "YES"),
    "acl_synced_at": _timestamp(),
    "superseded_at": _timestamp("YES"),
    "created_at": _timestamp(),
}

EXPECTED_EMBEDDING_COLUMNS = {
    "id": _varchar(36),
    "workspace_id": _varchar(36),
    "meeting_id": _varchar(36),
    "entry_id": _varchar(36),
    "embedding_version": _varchar(64),
    "provider": _varchar(128),
    "model": _varchar(256),
    "dimensions": _plain("integer"),
    "vector": _bytea(),
    "text_sha256": _varchar(64),
    "created_at": _timestamp(),
}


def _foreign_key(table: str, column: str, target: str, action: str) -> tuple[str, str]:
    return (
        f"{table}_{column}_fkey",
        f"FOREIGN KEY ({column}) REFERENCES {target}(id) ON DELETE {action}",
    )


EXPECTED_ENTRY_CONSTRAINTS = dict(
    [
        (f"{ENTRIES}_pkey", "PRIMARY KEY (id)"),
        _foreign_key(ENTRIES, "workspace_id", "workspaces", "CASCADE"),
        _foreign_key(ENTRIES, "meeting_id", "meetings", "CASCADE"),
        _foreign_key(ENTRIES, "revision_id", "transcript_revisions", "CASCADE"),
        _foreign_key(ENTRIES, "text_view_id", "transcript_text_views", "CASCADE"),
        _foreign_key(ENTRIES, "text_view_segment_id", "text_view_segments", "CASCADE"),
        _foreign_key(ENTRIES, "segment_id", "transcript_segments", "CASCADE"),
        _foreign_key(ENTRIES, "team_id", "teams", "SET NULL"),
        _foreign_key(ENTRIES, "agent_user_id", "users", "SET NULL"),
        _foreign_key(ENTRIES, "created_by_user_id", "users", "SET NULL"),
        (
            "uq_retrieval_entry_segment_version",
            "UNIQUE (text_view_segment_id, index_version)",
        ),
    ]
)

EXPECTED_EMBEDDING_CONSTRAINTS = dict(
    [
        (f"{EMBEDDINGS}_pkey", "PRIMARY KEY (id)"),
        _foreign_key(EMBEDDINGS, "workspace_id", "workspaces", "CASCADE"),
        _foreign_key(EMBEDDINGS, "meeting_id", "meetings", "CASCADE"),
        _foreign_key(EMBEDDINGS, "entry_id", ENTRIES, "CASCADE"),
        (
            "uq_retrieval_embedding_entry_version",
            "UNIQUE (entry_id, embedding_version)",
        ),
    ]
)


def _index(table: str, name: str, columns: str, *, unique: bool = False) -> tuple[str, str]:
    kind = "UNIQUE INDEX" if unique else "INDEX"
    return name, f"CREATE {kind} {name} ON public.{table} USING btree ({columns})"


EXPECTED_ENTRY_INDEXES = dict(
    [
        _index(ENTRIES, f"{ENTRIES}_pkey", "id", unique=True),
        _index(ENTRIES, "ix_retrieval_entries_workspace_id", "workspace_id"),
        _index(ENTRIES, "ix_retrieval_entries_meeting_id", "meeting_id"),
        _index(ENTRIES, "ix_retrieval_entries_revision_id", "revision_id"),
        _index(ENTRIES, "ix_retrieval_entries_text_view_id", "text_view_id"),
        _index(ENTRIES, "ix_retrieval_entries_segment_id", "segment_id"),
        _index(ENTRIES, "ix_retrieval_entry_workspace_meeting", "workspace_id, meeting_id"),
        (
            FTS_INDEX,
            f"CREATE INDEX {FTS_INDEX} ON public.{ENTRIES} USING gin "
            "(to_tsvector('simple'::regconfig, text))",
        ),
    ]
)

EXPECTED_EMBEDDING_INDEXES = dict(
    [
        _index(EMBEDDINGS, f"{EMBEDDINGS}_pkey", "id", unique=True),
        _index(EMBEDDINGS, "ix_retrieval_embeddings_workspace_id", "workspace_id"),
        _index(EMBEDDINGS, "ix_retrieval_embeddings_meeting_id", "meeting_id"),
        _index(EMBEDDINGS, "ix_retrieval_embeddings_entry_id", "entry_id"),
        _index(
            EMBEDDINGS,
            "ix_retrieval_embedding_workspace_version",
            "workspace_id, embedding_version",
        ),
    ]
)

EXPECTED_GRANTS = {
    ENTRIES: ["DELETE", "INSERT", "SELECT", "UPDATE"],
    # A vector is written once: no UPDATE grant, DELETE stays for the purge.
    EMBEDDINGS: ["DELETE", "INSERT", "SELECT"],
}


def _assert_head_shape(table: str) -> dict:
    shape = _shape(table)
    assert shape["columns"] == (
        EXPECTED_ENTRY_COLUMNS if table == ENTRIES else EXPECTED_EMBEDDING_COLUMNS
    ), table
    constraints = dict(shape["constraints"])
    named_checks = {name for name in constraints if name.startswith("ck_")}
    assert named_checks == set(
        ENTRY_CHECKS if table == ENTRIES else EMBEDDING_CHECKS
    )  # their behaviour is pinned by the RULES above
    for name in named_checks:
        constraints.pop(name)
    assert constraints == (
        EXPECTED_ENTRY_CONSTRAINTS if table == ENTRIES else EXPECTED_EMBEDDING_CONSTRAINTS
    ), table
    indexes = dict(shape["indexes"])
    if table == ENTRIES:
        # The partial live index: its WHERE clause is pinned separately.
        partial = indexes.pop(LIVE_INDEX)
        assert partial.startswith(_index(ENTRIES, LIVE_INDEX, "workspace_id, index_version")[1]), (
            partial
        )
        assert partial.endswith(f"WHERE ({LIVE_WHERE})"), partial
    # A UNIQUE table constraint appears in pg_indexes as its backing index; its
    # definition is pinned with the constraints above.
    if table == ENTRIES:
        indexes.pop("uq_retrieval_entry_segment_version", None)
    else:
        indexes.pop("uq_retrieval_embedding_entry_version", None)
    assert indexes == (
        EXPECTED_ENTRY_INDEXES if table == ENTRIES else EXPECTED_EMBEDDING_INDEXES
    ), table
    # The tenant policy only: no share-token or service clause, the same check on write.
    ((name, policy),) = shape["policies"].items()
    permissive, roles, command, using, check = policy
    assert (name, permissive, roles, command) == (POLICY_NAME, "PERMISSIVE", "{public}", "ALL")
    assert using == check
    assert "homayar.workspace_id" in using
    assert "share" not in using and "service" not in using
    assert shape["rls"] == [(True, True)]
    assert shape["grants"] == EXPECTED_GRANTS[table], table
    return shape


@pytest.mark.postgres
def test_the_orm_schema_has_the_tables(client):
    _requires_postgres()
    for table in TABLES:
        _assert_head_shape(table)


@pytest.mark.postgres
def test_the_migration_creates_and_drops_only_the_tables(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    from alembic import command

    from backend.config import reset_settings_cache

    tenant = _tenant(client, email_outbox, "migration")
    orm_shapes = {table: _assert_head_shape(table) for table in TABLES}
    setup = _indexed(tenant, "migration")
    entry_id = _add(RetrievalEntry, _entry(setup))
    _add(RetrievalEmbedding, _embedding(setup, entry_id))
    meetings = _scalar("SELECT count(*) FROM meetings")
    views = _scalar("SELECT count(*) FROM transcript_text_views")

    config = _alembic(monkeypatch, settings)
    try:
        command.stamp(config, "head")
        head_others = _other_tables()
        command.downgrade(config, BASE_REVISION)
        others = _other_tables()  # both tables are gone with the downgrade
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        for table in TABLES:
            assert _scalar("SELECT to_regclass(:t) IS NULL", t=f"public.{table}")
            assert _shape(table) == EMPTY_SHAPE
        # Only the index rows are lost: the calls and their views stay.
        assert _scalar("SELECT count(*) FROM meetings") == meetings
        assert _scalar("SELECT count(*) FROM transcript_text_views") == views

        command.upgrade(config, NEW_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == NEW_REVISION
        assert {table: _shape(table) for table in TABLES} == orm_shapes
        assert all(_ids(table) == set() for table in TABLES)
        assert _other_tables() == others  # every other table as it was

        # The migrated schema refuses the same deny cases and takes the allow cases.
        for rules in RULES:
            rules(tenant)
        migrated = _indexed(tenant, "migration_migrated")
        migrated_id = _add(RetrievalEntry, _entry(migrated))
        assert migrated_id in _ids(ENTRIES)

        # Down and up again with rows present, then to the head.
        command.downgrade(config, BASE_REVISION)
        assert _other_tables() == others
        command.upgrade(config, NEW_REVISION)
        assert {table: _shape(table) for table in TABLES} == orm_shapes
        assert all(_ids(table) == set() for table in TABLES)
        assert _other_tables() == others
        command.upgrade(config, "head")
        assert _other_tables() == head_others
    finally:
        reset_settings_cache()


@pytest.mark.postgres
def test_the_full_text_index_is_gin_and_serves_the_query(client, email_outbox):
    _requires_postgres()
    setup = _indexed(_tenant(client, email_outbox, "fts"), "fts")
    entry_id = _add(RetrievalEntry, _entry(setup))
    definition = _scalar(
        "SELECT indexdef FROM pg_indexes WHERE tablename = :t AND indexname = :n",
        t=ENTRIES,
        n=FTS_INDEX,
    )
    assert definition is not None
    assert " USING gin (" in definition
    assert "to_tsvector('simple'::regconfig, text)" in definition
    with inspection_session_factory()() as db:
        db.execute(text("SET enable_seqscan = off"))
        plan = "\n".join(
            str(row[0])
            for row in db.execute(
                text(
                    "EXPLAIN SELECT id FROM retrieval_entries "
                    "WHERE to_tsvector('simple', text) @@ plainto_tsquery('simple', 'segment')"
                )
            ).all()
        )
    assert FTS_INDEX in plan, plan
    # And the query finds the indexed text.
    with inspection_session_factory()() as db:
        found = (
            db.execute(
                text(
                    "SELECT id FROM retrieval_entries "
                    "WHERE to_tsvector('simple', text) @@ plainto_tsquery('simple', 'segment')"
                )
            )
            .scalars()
            .all()
        )
    assert found == [entry_id]


def _write_rows(db, setup: dict) -> dict:
    """One entry and its embedding, written in the session's own transaction."""
    entry = RetrievalEntry(**_entry(setup))
    db.add(entry)
    db.flush()
    embedding = RetrievalEmbedding(**_embedding(setup, entry.id))
    db.add(embedding)
    db.flush()
    return setup | {"entry_id": entry.id, "embedding_id": embedding.id}


@pytest.mark.postgres
def test_a_session_reads_and_writes_only_its_own_workspace(client, email_outbox):
    _requires_postgres()
    first = _indexed(_tenant(client, email_outbox, "tenant_a"), "tenant_a")
    second = _indexed(_tenant(client, email_outbox, "tenant_b"), "tenant_b")
    factory = get_session_factory()

    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        first = _write_rows(db, first)
        db.commit()

    # Bound to B: none of A's rows; B writes its own.
    with factory() as db:
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        for table in TABLES:
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0  # noqa: S608
        second = _write_rows(db, second)
        db.commit()

    # Bound to A: only A's rows; an insert with B's ids fails (WITH CHECK).
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        for table in TABLES:
            visible = db.execute(text(f"SELECT workspace_id FROM {table}")).scalars()  # noqa: S608
            assert list(visible) == [first["workspace_id"]]
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        db.add(RetrievalEntry(**_entry(second)))
        with pytest.raises(DBAPIError, match="row-level security"):
            db.flush()
        db.rollback()

    # No workspace bound: nothing.
    with factory() as db:
        for table in TABLES:
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0  # noqa: S608


@pytest.mark.postgres
def test_a_share_token_does_not_reach_the_rows(client, email_outbox):
    _requires_postgres()
    setup = _indexed(_tenant(client, email_outbox, "share"), "share")
    _add(RetrievalEntry, _entry(setup))
    token_hash = "93" * 32
    with inspection_session_factory()() as db:
        db.add(
            ShareLink(
                workspace_id=setup["workspace_id"],
                meeting_id=setup["meeting_id"],
                created_by_user_id=setup["user_id"],
                token_hash=token_hash,
                audience=ShareAudience.LINK,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        db.commit()
    from backend.database.tenant_context import bind_capability_context

    with get_session_factory()() as db:
        bind_capability_context(db, share_token_hash=token_hash)
        # Control: the shared conversation itself is readable through the token.
        assert db.scalars(select(Meeting.id)).all() == [setup["meeting_id"]]
        assert db.scalars(select(RetrievalEntry.id)).all() == []
        db.rollback()


def _privileges(table: str) -> dict[str, bool]:
    return {
        privilege: _scalar(
            "SELECT has_table_privilege(:r, :t, :p)", r=APP_ROLE, t=table, p=privilege
        )
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
    }


def _apply_as_owner() -> None:
    from backend.database.rls import apply_row_level_security

    with inspection_session_factory()() as db:
        try:
            apply_row_level_security(db.connection())
        except Exception:
            db.rollback()
            raise
        db.commit()


@pytest.mark.postgres
def test_the_app_role_writes_embeddings_once_and_may_update_entries(client, email_outbox):
    _requires_postgres()
    setup = _indexed(_tenant(client, email_outbox, "grants"), "grants")
    # A vector is written once: INSERT, SELECT and DELETE only.
    assert _privileges(EMBEDDINGS) == {
        "SELECT": True,
        "INSERT": True,
        "UPDATE": False,
        "DELETE": True,
    }
    # PR 2 updates superseded_at and the ACL snapshot of an entry; the purge deletes.
    assert _privileges(ENTRIES) == {
        "SELECT": True,
        "INSERT": True,
        "UPDATE": True,
        "DELETE": True,
    }

    factory = get_session_factory()
    with factory() as db:
        bind_tenant_context(db, workspace_id=setup["workspace_id"])
        setup = _write_rows(db, setup)
        db.commit()

    with factory() as db:
        bind_tenant_context(db, workspace_id=setup["workspace_id"])
        # INSERT and SELECT already ran above; an UPDATE of a vector is refused.
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text(f"UPDATE {EMBEDDINGS} SET provider = 'x'"))  # noqa: S608
        db.rollback()

    # An UPDATE of an entry's superseded_at works, and DELETE of embeddings works.
    with factory() as db:
        bind_tenant_context(db, workspace_id=setup["workspace_id"])
        updated = db.execute(
            text(f"UPDATE {ENTRIES} SET superseded_at = :now WHERE id = :id"),  # noqa: S608
            {"now": NOW, "id": setup["entry_id"]},
        )
        assert updated.rowcount == 1
        deleted = db.execute(
            text(f"DELETE FROM {EMBEDDINGS} WHERE entry_id = :id"),  # noqa: S608
            {"id": setup["entry_id"]},
        )
        assert deleted.rowcount == 1
        db.commit()
    assert _ids(EMBEDDINGS) == set()
    assert len(_ids(ENTRIES)) == 1

    # Still the same grants after the policies and the grants are applied again.
    _apply_as_owner()
    assert _privileges(EMBEDDINGS)["UPDATE"] is False
    assert _privileges(ENTRIES)["UPDATE"] is True


# --- the conversation purge and the backup reconciliation (SPEC acceptance criterion A9) -------


def test_the_purge_deletes_and_counts_both_tables_of_its_meeting_only(client, email_outbox):
    tenant = _tenant(client, email_outbox, "purge")
    purged = _indexed(tenant, "purge_purged")
    kept = _indexed(tenant, "purge_kept")
    rows = {}
    for key, setup in (("purged", purged), ("kept", kept)):
        entry_id = _add(RetrievalEntry, _entry(setup))
        rows[key] = (entry_id, _add(RetrievalEmbedding, _embedding(setup, entry_id)))
    statements: list[str] = []

    def record(_connection, _cursor, statement, *_args) -> None:
        statements.append(statement)

    factory = get_session_factory()
    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            meeting = db.get(Meeting, purged["meeting_id"])
            removed = _remove_content(db, workspace_id=tenant["workspace_id"], meeting=meeting)
            db.commit()
        finally:
            event.remove(engine, "before_cursor_execute", record)

    deletes = [s.split()[2] for s in statements if s.lstrip().upper().startswith("DELETE FROM")]
    # The embeddings go before their entries, and the entries before the text view
    # rows and the segments whose cascades would otherwise remove them uncounted.
    assert deletes.index(EMBEDDINGS) < deletes.index(ENTRIES)
    for parent in (
        "text_view_entities",
        "text_view_segments",
        "transcript_text_views",
        "transcript_segments",
        "transcript_revisions",
    ):
        assert deletes.index(ENTRIES) < deletes.index(parent), parent
    # The purge counted both index rows (with the view, its segment, the raw
    # segment and the revision: six rows of the purged conversation).
    assert removed == 6
    assert _ids(ENTRIES, meeting_id=purged["meeting_id"]) == set()
    assert _ids(EMBEDDINGS, meeting_id=purged["meeting_id"]) == set()
    # Another conversation of the workspace keeps its rows.
    assert _ids(ENTRIES, meeting_id=kept["meeting_id"]) == {rows["kept"][0]}
    assert _ids(EMBEDDINGS, meeting_id=kept["meeting_id"]) == {rows["kept"][1]}


def test_the_backup_reconciliation_counts_both_tables():
    for table in TABLES:
        assert table in RECONCILED_TABLES, table
        columns = Base.metadata.tables[table].c
        assert "meeting_id" in columns and "workspace_id" in columns
    # The embeddings next to their entries, and the entries next to the text view
    # rows they were built from.
    assert RECONCILED_TABLES.index(EMBEDDINGS) == RECONCILED_TABLES.index(ENTRIES) - 1
    assert RECONCILED_TABLES.index("text_view_segments") < RECONCILED_TABLES.index(ENTRIES)


# --- the ADR (SPEC acceptance criterion A11) ---------------------------------------------------


def _decision_record(name: str) -> str:
    from backend.observability.release import PROJECT_ROOT

    path = PROJECT_ROOT.parent / "docs" / "architecture" / "decisions" / name
    assert path.exists(), f"no {path}"
    return path.read_text(encoding="utf-8")


def _adr() -> str:
    return _decision_record("0068-retrieval-index-lexical-and-versioned-embeddings.md")


def test_the_adr_is_named_and_built_like_the_others():
    content = _adr()
    assert content.startswith("# ADR 0068: ")
    assert "Proposed (HOM-93, WP17-T02)" in content
    assert "2026-11-13" in content
    for number in ("0005", "0021", "0034", "0052"):
        assert f"ADR {number}" in content, number
    for heading in ("Context", "Decision", "Consequences"):
        assert re.search(rf"^## {heading}\s*$", content, re.MULTILINE), heading
    for section in (
        "D1",
        "D2",
        "D3",
        "D4",
        "D5",
        "D6",
        "D7",
        "D8",
        "D9",
    ):
        assert re.search(rf"^### {section}\.", content, re.MULTILINE), section


def test_the_adr_uses_no_em_dash_and_no_double_hyphen_dash():
    content = _adr()
    assert "—" not in content
    assert not re.search(r"(^|\s)--(\s|$)", content, re.MULTILINE)


# What the SPEC says the ADR records; any one spelling of a topic is enough.
ADR_TOPICS = {
    "one_entry": ("one text-view segment",),
    "normalized_source": ("text_view_segments.text",),
    "redacted_source": ("redacted",),
    "default_reader": ("default reader",),
    "simple_config": ("'simple'", "simple configuration"),
    "gin": ("GIN", "gin"),
    "sqlite_fallback": ("fallback",),
    "no_pgvector": ("pgvector",),
    "float32": ("float32",),
    "vector_store": ("VectorStore",),
    "exact_scan": ("exact scan",),
    "embedding_version": ("embedding_version",),
    "canonical_json": ("canonical JSON",),
    "no_overwrite": ("never overwrites",),
    "provider": ("EmbeddingProvider",),
    "no_cloud": ("no cloud fallback",),
    "lexical_only": ("lexical-only",),
    "acl_snapshot": ("acl_synced_at",),
    "meeting_clause": ("meeting_clause",),
    "pre_filter": ("pre-filter",),
    "live_authority": ("live",),
    "deletion_at_once": ("at once",),
    "hom94": ("HOM-94",),
    "revision": ("revision_id",),
    "is_current": ("is_current",),
    "historical": ("historical",),
    "purge": ("purge",),
    "cascade": ("ON DELETE CASCADE",),
    "backup": ("reconcil",),
    "hom95": ("HOM-95",),
    "hom96": ("HOM-96",),
    "index_version": ("index_version",),
    "f57": ("F57",),
}


@pytest.mark.parametrize("spellings", ADR_TOPICS.values(), ids=ADR_TOPICS.keys())
def test_the_adr_records_the_design(spellings):
    content = _adr()
    assert any(spelling in content for spelling in spellings), spellings
