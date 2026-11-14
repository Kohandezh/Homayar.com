# ADR 0077: Authorization filters before scoring, re-checked on every evidence access

Date: 2026-11-14 · Status: Proposed (HOM-94, WP17-T03, parent HOM-21, features F57 F58) · Builds on: ADR 0005, ADR 0068, ADR 0075

Status note (HOM-95): D7 is superseded by [ADR 0080](0080-bounded-hybrid-search.md) D2.

## Context

HOM-93 (ADR 0068) gave the conversation library a lexical index and versioned
embeddings whose reads join live rows. HOM-96 (ADR 0075) gave the analytical
Copilot five allowlisted tools that scope every query inside the caller's
record access. What sat between them was open: the evidence read path itself.

Four gaps made the parent promises (F57 recall on authorized evidence, F58 no
cross-tenant or cross-team leak) unprovable:

1. Membership was frozen. Callers passed a `RecordAccess` resolved once at
   request start, so a long Copilot session kept access after its membership
   was deactivated, expired, lost a team assignment, after the user was
   deactivated, the workspace suspended, or an API key lacked the scope.
2. Retention was not filtered: a meeting past `retention_expires_at` stayed
   searchable until the retention sweep ran.
3. No evidence fetch by id existed for a model that names entry ids, and a
   naive one would have been an authorization bypass.
4. No harness measured recall under authorization, and no suite probed
   guessed ids, cross-tenant reads or prompt injection.

## Decision

### D1. One filter order: Python checks, one live caller query, then the index filters inside the candidate SQL

Every read of the evidence path applies, in this order:

1. **tenant**: PostgreSQL row-level security (ADR 0005), plus the session
   binding rule of ADR 0075 as a Python check before any query (a session
   not bound to the caller's workspace is a `ValueError`);
2. **the live caller check**, one separate SQL query in `resolve_scope` over
   live rows: an active membership inside its validity window, an active
   user, an active workspace, and the `meeting:read` permission on the live
   role. For an API-key actor the live `ApiKey` row is re-read by id
   (see D2), so this check is also where key state and key scopes live;
3. **the index filters**, all inside the candidate SQL of `live_entries`, so
   no entry outside the caller's scope is ever scored, ranked or counted
   toward `LIMIT`:

   - **workspace**: the entry, its meeting, its revision, its text view and
     its vectors all carry `workspace_id`;
   - **record ACL**: `RecordAccess.meeting_clause()` against the live
     `meetings` row (agent, uploader, supervised teams);
   - **retention**: `retention_expires_at IS NULL OR retention_expires_at > now`;
   - **deletion**: `deleted_at IS NULL`;
   - **current revision**: the live `transcript_revisions.is_current`
     (ADR 0068 D7), unless the caller asked for history;
   - **redaction**: the entry's text view must equal the workspace's current
     default reader view (ADR 0068 D1).

Nothing is post-filtered from a wider result.

### D2. Re-resolution on every search and every fetch

`backend.retrieval.evidence.resolve_scope` re-resolves membership, user,
workspace, role permission and record access from live rows on every call.
`context.record_access` and `context.membership.role` are never trusted: the
`WorkspaceContext` of a long session is only a hint of who might still be
calling. Deletion and membership changes therefore remove access at once,
before any reindex (F57).

"Live" also holds inside the caller's own SQLAlchemy session. A request
loads the context's `ApiKey`, `Workspace` and `Membership` into the same
session it passes to the evidence API, and that session's identity map
would hand back the cached objects with their old values. So the key read,
the caller query and a pre-read of the caller's `TeamAssignment` rows (the
rows `resolve_record_access` uses) all run with `populate_existing`: they
run SQL and write the live values over the cached objects. Nothing is
expired or expunged, so other objects in the session keep their unflushed
changes. The session is flushed first (after the argument and binding
checks), so the caller's own unflushed changes to these objects reach the
database and are read back by the refresh, not dropped.

An API-key actor is re-read by id on every call: a missing row, a set
`revoked_at`, an `expires_at <= now`, a row of another workspace, or a live
scope set without `meeting:read` denies with no index query. The scopes of
the stale `ApiKey` object are never used; only the live row's.

Login sessions are not re-checked here. The login session is validated at
the request edge (revocation, expiry, session version), and this module
re-resolves only what a long-lived caller object could outlive: membership,
user, workspace, role and key state. That limit is stated plainly: an access
token that survives revocation until its next edge check is a property of
the request layer, not of the evidence path.

A caller that fails any live check gets a plain `denied` outcome and no
query touches the index; the argument checks of the underlying primitives
(`limit`, an oversized query, a wrong vector length) stay `ValueError` for
callers that pass the scope check.

### D3. The stored ACL snapshot stays a pre-filter, never the authority (ADR 0068 D6 kept)

The snapshot columns (`team_id`, `agent_user_id`, `created_by_user_id`) are
not used as a filter by the evidence path at all. Every read joins the live
`meetings` row and applies the live `RecordAccess`, so re-attribution acts at
once, in both directions: the old team's supervisor loses the entry the
moment the meeting row moves, the new team's supervisor gains it, and a stale
snapshot can never leak.

### D4. The guessed-id rule: one answer for every reason

`fetch_evidence` treats its `entry_ids` as untrusted model output. An id of
another tenant, another workspace, a team outside the caller's access, a
deleted meeting, an expired retention, an old redaction policy, a superseded
revision (without `include_history`), and a random guess all give exactly the
same result: the id is `missing`. No reason per id, no error, no per-id
lookup path that could differ in timing, and no id found inside an entry's
text is ever followed. Ids are compared exactly (case-sensitive, no strip, no
normalization); an item that is not a `str` of length 1..64 is never queried
and never reported. A str id of length 1..64 that holds a NUL or cannot be
encoded as UTF-8 (a lone surrogate) is never put in SQL either, but it is
reported in `missing`, and the valid ids of the same call are still answered.
At most 100 distinct items per call; entry text, query text and vectors are
never logged, ids and counts only.

### D5. Legal hold does not re-open an expired conversation for retrieval

A held conversation past retention is kept for legal reasons, not served as
evidence: the retention filter of D1 ignores legal hold entirely. Compliance
access to a held conversation goes through the legal-hold workflow, not the
retrieval index.

### D6. The recall harness and its numbers

`backend/tests/_retrieval_corpus.py` and `test_retrieval_filtered_recall.py`
build a deterministic corpus (two workspaces, three teams, five personas, 266
entries in A over 26 meetings: 2 deleted, 2 past retention, 1 superseded
revision, 1 under an old redaction policy, plus 50 entries in B) with 8
seeded vector clusters, and compute the ground truth in Python from the
fixture's own metadata. On this store, at a full `candidate_limit`,
`search_evidence_vector` reaches mean recall@10 = 1.0 for every persona with
zero leaks, while a post-filter control (an unrestricted top-10 with the
Python ACL filter applied after) reached only 0.3375 for the team-1
supervisor and 0.2417 for the team-2 agent in the observed run (the exact
values vary a little with the entry ids the database assigns; both stay far
below 0.90). That is the proof that the corpus forces pre-filtering and that
the promise rests on D1, not on luck. Lexical token queries return exactly
the authorized truth set.

### D7. The stated limit: candidate_limit truncates by entry id order

`TableVectorStore.nearest` loads at most `candidate_limit` vectors, chosen by
entry id order, before scoring. When the authorized set is larger than
`candidate_limit`, recall falls (in one observed run of the harness the mean
recall@10 at `candidate_limit=20` ranged from about 0.13 to 0.54 across
personas; the numbers vary with the entry ids the database assigns) while
leaks stay zero, because the truncation happens inside the authorized
candidate SQL. HOM-95 owns ranking-aware candidate selection; this decision
only states the limit plainly.

## Consequences

- No migration, no model change, no RLS change, no route: the evidence path
  is a new module over the tables and primitives of HOM-93.
- `live_entries`, `lexical_search` and `TableVectorStore.nearest` gain a
  keyword-only `now` (default: the current UTC instant); a naive `now` is a
  `ValueError`, and an aware non-UTC `now` compares instants, never wall-clock
  text. Every existing caller keeps working without passing `now`.
- Denial has one shape: `("denied", ())` for search, `("denied", (), ())` for
  fetch, with no query over the index. Callers cannot learn whether a denial
  came from key state, key scope, membership, user, workspace or role.
  Retention, deletion, redaction and supersession never deny: they remove
  hits from search and put ids in `missing`, which is a different (and
  equally opaque) outcome.
- Programming errors keep their shape: a naive `now`, a wrong context type,
  an unbound session, a non-sequence `entry_ids` or more than 100 distinct
  ids are `ValueError`, in the check order of ADR 0075 (argument defects
  before binding for `fetch_evidence`, so a denied caller still learns the
  defects of its own arguments, and nothing else). A query that is not a
  `str`, a query the database cannot encode, or an unknown language is a
  `ValueError` as well, checked after the scope.
- A supervisor whose team assignment ends keeps `answered` state but loses
  the team's entries on the next call: the ACL narrows, the session survives.
- The recall harness is the F57 benchmark substrate: HOM-95's ranking work
  must keep leaks at zero and mean recall at or above 0.90 on this corpus.
