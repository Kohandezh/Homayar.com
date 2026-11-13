"""Retrieval index: lexical entries and versioned embeddings, schema only (HOM-93, WP17-T02)

Revision ID: a3c7e19d4b62
Revises: e8b4d2f7a9c1
Create Date: 2026-11-13

Adds the two tables of the retrieval index (F57 and F58 of WP17):
``retrieval_entries`` holds one row per indexed text-view segment, the
normalized lexical text of ``text_view_segments.text`` (ADR 0034) with its
digest, its revision metadata and a snapshot of the record ACL; ``retrieval_embeddings``
holds versioned float32 vectors of an entry as portable bytes, because
``postgres:17-alpine`` has no pgvector.  Nothing writes the tables yet; the
indexer, the embedding writer and the vector store adapter follow in PR 2
(ADR 0068 records both halves).

* Why the ACL snapshot: the three columns (``team_id``, ``agent_user_id``,
  ``created_by_user_id``) are the exact fields of
  ``RecordAccess.meeting_clause``, copied from the live meeting at
  ``acl_synced_at``.  The snapshot is a pre-filter only: every read of PR 2
  joins the live ``meetings`` row and applies the live record access (HOM-94),
  so deletion, re-attribution (ADR 0052) and membership changes act at once,
  before any reindex.  The checks use no regex, so SQLite runs the same rules,
  and no check names a snapshot column: those are SET NULL when a team or a
  user is removed, and a check on them would make that removal fail.
* ``uq_retrieval_entry_segment_version`` keeps one entry per text-view segment
  and index version; ``uq_retrieval_embedding_entry_version`` keeps one
  embedding per entry and embedding version.  A new version never overwrites
  an old row, it writes new rows, so a reindex for a new model or a new layout
  can run next to the old index and swap when it is done.
* ``ix_retrieval_entry_live`` is a partial index over the entries whose
  revision was current when indexed (``superseded_at IS NULL``), like
  ``uq_transcript_revision_current``.  ``ix_retrieval_entry_text_fts`` is a
  PostgreSQL GIN expression index on ``to_tsvector('simple', text)``, the
  ``simple`` configuration because PostgreSQL has no Persian configuration and
  the ADR 0034 normalizer already did the language work; it is created on
  PostgreSQL only (SQLite, dev and tests, has no full-text index and PR 2 uses
  a plain fallback there), with the same expression as the ORM.
* Both tables are workspace tables under row-level security (ADR 0005): tenant
  only, never through a share token.  They get the full application grants
  except that ``retrieval_embeddings`` loses UPDATE (a vector is written once);
  the conversation purge deletes as the application role, so DELETE stays.  The
  migration re-applies the policies and the grants like ``e8b4d2f7a9c1``.

No data changes: no existing table's columns change and no row is written here.
The conversation purge (ADR 0021) and the backup reconciliation are code lists,
updated with this migration: the purge deletes the embeddings before their
entries and the entries before the text view rows and the segments whose
cascades would otherwise remove them uncounted, and ``backup/reconcile.py``
counts both tables.

Downgrade drops both tables with their indexes; every index entry and every
embedding is lost, and a rebuild from the text views (PR 2) is needed to get
them back.  The policies and the grants belong to the tables and go with them.
No other table's grants or policies change in either direction.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "a3c7e19d4b62"
down_revision: str | Sequence[str] | None = "e8b4d2f7a9c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENTRIES = "retrieval_entries"
EMBEDDINGS = "retrieval_embeddings"
FTS_INDEX = "ix_retrieval_entry_text_fts"
FTS_EXPRESSION = sa.text("to_tsvector('simple', text)")

# Frozen copies of the check strings in ``backend/database/saas_models.py`` at
# this revision, by constraint name.
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
# The partial live index's WHERE clause, as in the ORM.
LIVE_WHERE = sa.text("superseded_at IS NULL")
LIVE_INDEX = "ix_retrieval_entry_live"
ENTRY_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "meeting_id": ("meetings", "CASCADE"),
    "revision_id": ("transcript_revisions", "CASCADE"),
    "text_view_id": ("transcript_text_views", "CASCADE"),
    "text_view_segment_id": ("text_view_segments", "CASCADE"),
    "segment_id": ("transcript_segments", "CASCADE"),
    "team_id": ("teams", "SET NULL"),
    "agent_user_id": ("users", "SET NULL"),
    "created_by_user_id": ("users", "SET NULL"),
}
EMBEDDING_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "meeting_id": ("meetings", "CASCADE"),
    "entry_id": (ENTRIES, "CASCADE"),
}
ENTRY_INDEXES = (
    (f"ix_{ENTRIES}_workspace_id", ("workspace_id",)),
    (f"ix_{ENTRIES}_meeting_id", ("meeting_id",)),
    (f"ix_{ENTRIES}_revision_id", ("revision_id",)),
    (f"ix_{ENTRIES}_text_view_id", ("text_view_id",)),
    (f"ix_{ENTRIES}_segment_id", ("segment_id",)),
    ("ix_retrieval_entry_workspace_meeting", ("workspace_id", "meeting_id")),
)
EMBEDDING_INDEXES = (
    (f"ix_{EMBEDDINGS}_workspace_id", ("workspace_id",)),
    (f"ix_{EMBEDDINGS}_meeting_id", ("meeting_id",)),
    (f"ix_{EMBEDDINGS}_entry_id", ("entry_id",)),
    ("ix_retrieval_embedding_workspace_version", ("workspace_id", "embedding_version")),
)


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        ENTRIES,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("text_view_id", sa.String(length=36), nullable=False),
        sa.Column("text_view_segment_id", sa.String(length=36), nullable=False),
        sa.Column("segment_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=12), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("index_version", sa.String(length=40), nullable=False),
        sa.Column("team_id", sa.String(length=36), nullable=True),
        sa.Column("agent_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("acl_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        # TimestampMixin sets both in Python, like every other table; no
        # server default, or the migrated schema drifts from the ORM.
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *(
            sa.ForeignKeyConstraint([column], [f"{target}.id"], ondelete=action)
            for column, (target, action) in ENTRY_FOREIGN_KEYS.items()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "text_view_segment_id", "index_version", name="uq_retrieval_entry_segment_version"
        ),
        *(sa.CheckConstraint(check, name=name) for name, check in ENTRY_CHECKS.items()),
    )
    with op.batch_alter_table(ENTRIES, schema=None) as batch_op:
        for name, columns in ENTRY_INDEXES:
            batch_op.create_index(name, columns, unique=False)
        batch_op.create_index(
            LIVE_INDEX,
            ["workspace_id", "index_version"],
            unique=False,
            postgresql_where=LIVE_WHERE,
            sqlite_where=LIVE_WHERE,
        )
    op.create_table(
        EMBEDDINGS,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("entry_id", sa.String(length=36), nullable=False),
        sa.Column("embedding_version", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *(
            sa.ForeignKeyConstraint([column], [f"{target}.id"], ondelete=action)
            for column, (target, action) in EMBEDDING_FOREIGN_KEYS.items()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entry_id", "embedding_version", name="uq_retrieval_embedding_entry_version"
        ),
        *(sa.CheckConstraint(check, name=name) for name, check in EMBEDDING_CHECKS.items()),
    )
    with op.batch_alter_table(EMBEDDINGS, schema=None) as batch_op:
        for name, columns in EMBEDDING_INDEXES:
            batch_op.create_index(name, columns, unique=False)
    if _is_postgresql():
        # The same expression as the ORM's ``ix_retrieval_entry_text_fts``, so a
        # query ``to_tsvector('simple', text) @@ ...`` can use it.
        op.create_index(
            FTS_INDEX,
            ENTRIES,
            [FTS_EXPRESSION],
            postgresql_using="gin",
        )
    # The tenant policies of the new tables and their grants.
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (every index entry and embedding is lost)."""
    if _is_postgresql():
        op.drop_index(FTS_INDEX, table_name=ENTRIES)
    # The other indexes, the policies and the grants belong to the tables and go
    # with them.
    op.drop_table(EMBEDDINGS)
    op.drop_table(ENTRIES)
