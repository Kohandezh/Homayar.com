# ADR 0080: Bounded hybrid search, a local reranker seam, and search filters

Date: 2026-11-14 · Status: Proposed (HOM-95, WP17-T04, parent HOM-21, features F57 F58) · Builds on: ADR 0068, ADR 0075, ADR 0077 · Supersedes: ADR 0077 D7

## Context

HOM-93 (ADR 0068) built the retrieval index: a lexical path (PostgreSQL full
text, a token `LIKE` on SQLite) and a vector path (`TableVectorStore`, an exact
cosine scan in Python, because `postgres:17-alpine` has no pgvector). HOM-94
(ADR 0077) put the authorization filters inside the candidate SQL, before any
score, and proved recall@10 = 1.0 with zero leaks on a fixture corpus.

Three things were missing for F57:

1. There was no way to use both paths for one question. A user who types the
   right word but whose conversation is not in the vector window got nothing
   from the vector path, and a paraphrase got nothing from the lexical path.
2. ADR 0077 D7 stated a limit: `TableVectorStore.nearest` scored at most
   `candidate_limit` vectors chosen by entry id. Entry ids are random, so the
   window was an arbitrary slice when the authorized set was larger.
3. Callers could not narrow a search by conversation type, source, time, or
   transcript and audio quality.

## Decision

### D1. One hybrid entry point, fused by reciprocal rank fusion (k = 60)

`backend.retrieval.hybrid.search_evidence_hybrid` runs the lexical stage,
then (when a store, an embedding version and a query vector are all given)
the vector stage, and fuses the two ranked lists:

`fused_score = sum(1 / (60 + rank))` over the lists an entry is in.

Equal scores share a rank: rank = 1 + the number of entries in that list with
a strictly higher score. The fused order is `fused_score` descending, then
entry id ascending. `HybridHit` carries `lexical_rank` and `vector_rank`
(`None` when the entry is not in that list) and no text.

Why ranks and not scores: `ts_rank` and cosine have no common scale, and no
calibration data exists to map one onto the other. On SQLite every lexical hit
has the same score (the number of query tokens), so only a rank can be
compared, and shared ranks keep equal hits equal instead of ordering them by
random entry id. `k = 60` is the usual RRF constant; it keeps one list's top
hit from swamping the other list.

A query that is empty after normalizing has no lexical list (a vector-only
search). With no vector arguments the search is lexical-only. Mixed vector
arguments are a `ValueError`.

### D2. The vector window: newest conversations first, plus `score_entries` (the D7 fix)

`TableVectorStore.nearest` still scores at most `candidate_limit` (1..5000)
vectors, but the window is now the `candidate_limit` authorized, filtered
vectors of the asked `embedding_version` in the order
`COALESCE(started_at, created_at) DESC, entry_id ASC`. Recent conversations
are what users ask about most, and the order is stable. Only usable vectors
take a slot: a conversation without a vector of that version, a deleted one,
another team's one, or one a filter removes takes none. Rows are read with
`yield_per(500)` and only the best `k` are kept in a heap, so memory is the
batch plus `k`, not `candidate_limit`. The result order is unchanged: score
descending, then entry id.

`VectorStore.score_entries` (new, also on `TableVectorStore`) scores exactly
the given entry ids (a list or tuple of at most 100 distinct strings) under
the same live joins, filters and query-vector checks as `nearest`. An id that
is not live, not authorized, filtered out, of another workspace, has no
vector of that version, or cannot be a database parameter is silently absent.
Hybrid calls it for every lexical candidate that `nearest` did not return, so
the lexical candidates are always vector-scored, wherever they sit in time.

The remaining limit, stated plainly: beyond 5000 authorized vectors (or
beyond the `candidate_limit` a caller picks), only the newest window plus the
lexical candidates are vector-scored. An old conversation that shares no word
with the query is not found by meaning alone. A pgvector or other ANN adapter
can replace the table scan behind `VectorStore` without changing callers.

### D3. Bounds and the time budget

| Argument | Default | Bounds |
| --- | --- | --- |
| `k` (hits returned) | 10 | 1..50 |
| `lexical_candidates` | 50 | 1..100 |
| `vector_candidates` | 50 | 1..100 |
| `candidate_limit` (vector window) | 2000 | 1..5000 |
| `rerank_candidates` | 20 | 1..50 |
| `time_budget_ms` | 2000 | 50..10000 |
| query | | at most 1000 characters after strip, 32 words after normalizing |
| `score_entries` ids | | at most 100 distinct |

Every bound is checked before any index SQL; a `bool` is not an int here;
every defect is a `ValueError`. A denied caller gets `("denied", (), ())`
before any argument check, with no index query (the ADR 0077 rule).

The deadline is `clock() + time_budget_ms / 1000`, taken after the argument
checks. It bounds the stages, not the SQL: when it has passed before the
vector stage, the vector stage is skipped (`"vector_skipped_time"`); when it
has passed before the rerank, the rerank is skipped (`"rerank_skipped_time"`);
a reranker that returns after it has its scores discarded
(`"rerank_timeout"`). A skipped stage never calls the store or the reranker.
One SQL statement is not interrupted by the timer: its cost is bounded by the
candidate limits above (at most 100 lexical rows, a 5000-vector window, 100
rescued ids, 50 rerank texts). `HybridOutcome.degraded` names every shortcut
taken, in stage order.

### D4. Filters narrow only, inside the candidate SQL, before scoring

`backend.retrieval.filters.SearchFilters` is a frozen value with six optional
fields: `conversation_types` (the `ConversationType` values), `sources`,
`started_from` and `started_before` (a half-open range on
`COALESCE(meetings.started_at, meetings.created_at)`), `min_confidence` (the
entry's transcript segment), and `exclude_audio_flags` (`silent_channel`,
`duplicated_channels`, `mono_as_stereo` from the channel inspection's `flags`
list, and `narrowband` from its boolean). Validation happens at construction
and every defect is a `ValueError`. Lists, tuples, sets and frozensets are
accepted and stored as frozensets; aware datetimes are stored as UTC.

`live_entries` turns the set fields into extra `AND` conditions (and one join
to `transcript_segments` for `min_confidence`), so filters act before scoring
and before `LIMIT`, exactly like the authorization filters. No filter can add
an entry the caller could not get without filters. `SearchFilters()` with no
field set is the same as `filters=None`. `fetch_evidence` takes no filters: a
fetch by id is not a search.

Two rules about missing data:

- Unknown audio quality is kept. A conversation with no live recording, no
  media object, or a NULL inspection has no flag, so `exclude_audio_flags`
  keeps it. The flag test is a `NOT EXISTS` over the live recording and its
  media object, built per dialect (`json_array_elements_text` and `->>` on
  PostgreSQL, where the column is `json`; `json_each` and `json_type` on
  SQLite) with the flag values as bound parameters.
- A NULL segment confidence fails `min_confidence`, even at 0. A caller who
  asks for a confidence floor gets only entries known to meet it.

### D5. A local-only reranker seam; none ships

`backend.retrieval.rerank.Reranker` is a protocol:
`rerank(*, query, candidates, timeout_ms) -> Sequence[float]`, one score per
candidate, same order, higher is better. Only a local reranker may implement
it (no network, no remote API). This PR ships the protocol only: no model, no
adapter, no new dependency.

Hybrid gives the reranker the stripped query, an `int` budget of at least
1 ms, and `RerankCandidate(entry_id, text)` objects for the first
`rerank_candidates` fused entries. Their text is re-read at that moment under
the same live joins and filters, never by id alone, so the reranker only sees
authorized text. An entry whose text the live joins no longer return is not
given to the reranker and is not returned. The reranked block is ordered by
rerank score, then `fused_score`, then entry id; the rest keeps fused order.
A reranker that raises, or returns a wrong count, a non-number or a
non-finite score, gives `"rerank_failed"` and the fused order. It never fails
the search.

Nothing in the read path logs entry text, query text or vectors.

### D6. Measured harness numbers

The HOM-94 corpus (`backend/tests/_retrieval_corpus.py`) gained deterministic
filter metadata (conversation types in blocks of three meeting indexes, start
times one day apart with three NULL starts on pinned `created_at` values, and
a segment confidence per entry with every sixth one NULL), and
`test_retrieval_filtered_recall.py` gained the hybrid tests. The truth is
computed in Python from the fixture metadata. Run on 2026-11-14, identical on
SQLite and PostgreSQL 17:

| Persona | Hybrid `tok<c>` + vector | Rescue text `nomatch` | Filter: types | Filter: dates + confidence | D7 window 20: hybrid (carried) | D7 window 20: vector-only (carried) | D7 window 20: hybrid (all 24) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| admin | 1.0 | 1.0 | 1.0 (128/208 kept) | 1.0 (66/208 kept) | 1.0 (24 carried) | 0.108 | 1.0 |
| supervisor1 | 1.0 | 1.0 | 1.0 (48/78) | 1.0 (30/78) | 1.0 (21) | 0.243 | 0.9 |
| agent2 | 1.0 | 1.0 | 1.0 (20/40) | 1.0 (10/40) | 1.0 (12) | 0.5 | 0.713 |
| member | 1.0 | 1.0 | 1.0 (20/40) | 1.0 (8/40) | 1.0 (12) | 0.5 | 0.875 |
| admin_b | 1.0 | 1.0 | 1.0 (20/50) | 1.0 (11/50) | 1.0 (15) | 0.4 | 0.796 |

Leaks are 0 everywhere, and no returned entry fails its filter. A query is
"carried" when every entry of its true top 10 holds the cluster token: the
text alone can find all of them. At a 20-vector window, hybrid keeps recall
1.0 on every carried query, where the vector path alone reaches 0.11 to 0.5.
The queries the text does not carry depend on the window (the D2 limit) and
are reported, not asserted. The HOM-94 numbers hold: vector recall at a full
window is 1.0 for every persona, and the post-filter control stays at 0.3375
(team-1 supervisor) and 0.2417 (team-2 agent).

## Consequences

- No migration, no route, no model change, no RLS change, no new dependency.
  `fetch_evidence`, `resolve_scope`, `security/*` and `database/*` are
  unchanged.
- `live_entries`, `lexical_search`, `VectorStore.nearest`,
  `TableVectorStore.nearest`, `search_evidence_lexical` and
  `search_evidence_vector` take a keyword `filters` (default `None`, which
  keeps the earlier results). Any other type than `SearchFilters` or `None`
  is a `ValueError`. `live_entries` also selects `conversation_at`.
- ADR 0077 D7 is superseded by D2: the window is ranking-aware for recent
  conversations and for every lexical candidate; the limit that remains is
  stated in D2.
- Follow-ups, out of scope here: wiring the Copilot `evidence_search` tool
  (`ToolName.EVIDENCE_SEARCH`) to hybrid search; an HTTP route and the console
  filter UI; a real local reranker adapter; a pgvector or ANN vector store; an
  `embed_entries` background job (production stays lexical-only until it
  exists); duplicate entries across `index_version` values.
