# ADR 0068: Retrieval index: lexical text and versioned embeddings

Date: 2026-11-13 · Status: Proposed (HOM-93, WP17-T02) · Builds on: ADR 0005, ADR 0021, ADR 0034, ADR 0052

## Context

Parent HOM-21 (WP17, features F57 and F58) asks for evidence retrieval over the
conversation library. F57 promises: all returned evidence is authorized and
current, or explicitly historical, and deletion or membership changes remove
access immediately, even before a reindex. F58 asks for semantic retrieval over
the same text.

HOM-93 ships in two PRs. PR 1 (this decision) adds the schema only: the two
tables, their checks, their indexes and their row-level security. PR 2 adds the
indexer, the embedding writer and the vector store adapter. The read path that
re-checks authorization is HOM-94; hybrid search, reranking and filters are
HOM-95; analytical tools are HOM-96.

Two facts fix the shape of the storage. Every PostgreSQL image of this repo is
`postgres:17-alpine` (CI and the on-prem compose file), and that image has no
pgvector. And the normalized text of a conversation already exists:
`text_view_segments.text` (ADR 0034) is the text a default reader gets, already
redacted when the workspace enforces a redaction policy.

## Decision

### D1. One entry is one text-view segment of one revision

`retrieval_entries` holds one row per `text_view_segments` row under one
`index_version`. The source is `text_view_segments.text`, the normalized text
of ADR 0034. That text is already redacted when the workspace enforces a
redaction policy, and an index row is never returned as evidence unless its
text view matches the workspace's current default reader view. The rule: an
entry is evidence only while its text view's (`input_view`,
`input_policy_digest`) equals the default reader view of the workspace now, as
`backend.privacy.transcript_view.choose_view(workspace, requested=None, ...)`
gives it: original with no digest when redaction is off, redacted with the
policy digest when it is on. Same rule as `routers/v1/reports.py:528-533`.
Readers join `transcript_text_views` through `text_view_id`. PR 2's read
primitives apply the rule, and HOM-94 must keep it. Said plainly: after an
admin changes the redaction policy, old entries stop being evidence at once;
they come back only when a new text view is derived and indexed (the indexer
then deletes the old-policy entries). The row copies the segment's lineage:
meeting, revision, text view, text view segment and raw segment ids, the
sequence, the revision number, the language, the text and its sha256. PR 2
copies `workspace_id` and `meeting_id` of an entry from its
`text_view_segments` row, and those of an embedding from its entry, never from
the caller. PR 2 tests that invariant. Composite foreign keys that pin the
copy are a possible later change. `index_version` (for example
`retrieval-index/1`) names the index layout. A different layout writes new rows
under a new version; it never overwrites the old ones. The unique constraint on
(text_view_segment_id, index_version) keeps one entry per segment and version.

### D2. Lexical index: PostgreSQL full text with the simple configuration

On PostgreSQL the lexical search uses `to_tsvector('simple', text)` with a GIN
expression index `ix_retrieval_entry_text_fts`. The configuration is `simple`
because PostgreSQL has no Persian configuration and the text is already
normalized by the ADR 0034 normalizer (digits, ZWNJ and punctuation are already
canonical). SQLite, used in development and tests, has no full-text index: PR 2
uses a plain fallback query there. The index expression is the same string in
the ORM and in the migration, so a query of the form
`to_tsvector('simple', text) @@ plainto_tsquery('simple', ...)` can use the
index.

### D3. Vector store: a portable table, not pgvector

pgvector is not in `postgres:17-alpine`, so vectors live in the portable
`retrieval_embeddings` table as float32 little-endian bytes: exactly
`4 * dimensions` bytes per vector, checked by the database. PR 2 puts a
`VectorStore` adapter in front of the table. Its first implementation scores
candidates in Python inside one workspace. A pgvector adapter can come later
without changing callers. The limit is stated plainly: the scan is an exact scan,
so its cost is bounded by the candidate set PR 2 (and HOM-95) pass in.

### D4. Embedding version: a new model never overwrites an old vector

`embedding_version` is the sha256 of a canonical JSON of `provider`, `model`,
`dimensions` and `index_version` only. `ProviderIdentity.params` is NOT hashed:
for the shipped adapter it is the endpoint URL, and moving the server must not
invalidate every vector. Same model, same dimensions and same index version
mean the same vector space. If a future adapter has a param that changes the
vectors (for example a pooling or truncation option), that param must go into
the hash by name; a transport param such as the endpoint never does. The
unique constraint on (entry_id, embedding_version) keeps one vector per entry
and version. A new model or a new index version never overwrites an old vector;
it writes new rows. A reindex for a new version can run next to the old index
and swap when it is done. The row also stores `provider`, `model`,
`dimensions`, `text_sha256` (the sha256 of the exact text that was embedded) and
a copy of `workspace_id` and `meeting_id`, so the purge and the backup
reconciliation find the rows by meeting.

### D5. Embedding provider: injected, local only, honest when absent

PR 2 takes an injected `EmbeddingProvider` (`backend/providers/base.py`).
Production uses only the configured local or offline provider. There is
no cloud fallback: a workspace that did not choose an embedding provider gets no
embeddings. Tests use a deterministic fake and never make a network call. With
no provider configured the index is lexical-only and that state is visible, so
no caller mistakes a missing vector for an empty answer.

### D6. ACL: a snapshot as pre-filter, the live meeting as authority

The entry stores a snapshot of the record ACL: `team_id`, `agent_user_id` and
`created_by_user_id`, the exact fields of `RecordAccess.meeting_clause`, plus
`acl_synced_at`. The snapshot is a pre-filter only. Every read joins the live
`meetings` row (`deleted_at IS NULL`, live attribution) and applies
`RecordAccess.meeting_clause`. So deletion, re-attribution (ADR 0052) and
membership changes act at once, before any reindex (F57). A stale snapshot has
two effects, stated plainly. It can never leak: the live meeting decides, so a
removed or re-attributed record disappears from the results at once. It can
hide evidence: a reader that pre-filters on the snapshot misses the entries of
a user who just got access, for example the newly attributed agent or the
supervisor of the new team, until the snapshot is re-synced. So PR 2 re-syncs
the snapshot in the same request that changes a call's team or agent, and the
PR 2 read primitives filter on the live meeting, not on the snapshot. HOM-94
owns that read path and must keep that rule if it adds a snapshot pre-filter.
Neither table is reachable through a share token: both are tenant-only
workspace tables under the row-level security policy of ADR 0005.

### D7. Revision: current comes from the live row, history is labeled

`revision_id` and `revision_number` are stored on the entry. `superseded_at`
marks entries of a revision that stopped being current; NULL means the entry's
revision was current when the entry was indexed. "Current" at read time still
comes from the live `transcript_revisions.is_current`, never from the index.
Historical entries are returned only when a caller asks for history, and they
are labeled as historical in the answer.

### D8. Deletion: the purge counts both tables, cascades cover hard deletes

The conversation purge (ADR 0021) deletes and counts the meeting's rows in both
tables: the embeddings before their entries, and the entries before the text
view rows and the segments whose cascades would otherwise remove them
uncounted. The backup reconciliation lists both tables in `CONTENT_TABLES`.
ON DELETE CASCADE from meetings, revisions, text views and segments covers hard
deletes. The team and user snapshot columns are SET NULL, so removing a team or
a user keeps the entry and only clears the snapshot.

### D9. Out of scope, with the owning ticket

Authorization filters and the evidence re-check are HOM-94. Hybrid search,
reranking and filters are HOM-95. Analytical tools are HOM-96. PR 2 of HOM-93
builds the indexer, the embedding writer and the vector store adapter on the
tables of this decision.

## Consequences

- One migration adds the two tables, their checks, their indexes and the
  re-applied row-level security; no pgvector, no extension, no stored tsvector
  column, no trigger.
- `retrieval_embeddings` holds no UPDATE grant for the application role: a
  vector is written once and never changed. It is deleted by the purge, or by
  cascade when its entry is deleted (the indexer removing old-policy entries in
  D1, a reindex swapping out the old index version in D4, a hard delete in D8).
  `retrieval_entries` keeps UPDATE, because PR 2 sets `superseded_at` and
  re-syncs the ACL snapshot columns.
- The stored ACL snapshot can be stale between the last index run and the live
  row. A stale snapshot can never leak: every read joins the live meeting and
  applies `RecordAccess.meeting_clause`, so a removed or re-attributed record
  disappears at once. A stale snapshot can hide evidence: a reader that
  pre-filters on it misses the entries of a user who just got access until the
  snapshot is re-synced. PR 2 re-syncs the snapshot in the same request that
  changes a call's team or agent, and its read primitives filter on the live
  meeting, not on the snapshot; HOM-94 must keep that rule if it adds a
  snapshot pre-filter.
- The exact vector scan costs linear time in the candidate set. That is
  accepted for now and stated here; a pgvector adapter can replace it later
  without changing callers.
