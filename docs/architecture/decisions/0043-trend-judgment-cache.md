# ADR 0043: Trend judgment cache, its warm-up, and faster coverage counters

Date: 2026-10-26 · Status: proposed (HOM-312) · Builds on: ADR 0038, ADR 0040, ADR 0041

All code paths are under `Homayar/backend/` at base `73f6af9`.

## Context

At 100,000 conversations the trend routes took 10 to 45 s. The F61 target is p95 under 1 s for
the default 4-week window. Two parts took most of the time:

1. **Judging.** Every request read the stored extraction JSON of every conversation in the
   window (statement P, `services/issue_trend_service.py`) and judged it again with
   `judge_conversation` (`issue_trends/counting.py`). The JSON decode and the judging grow with
   the number of conversations.
2. **Coverage.** Every trend answer carries the `coverage/1` counters
   (`services/coverage_service.py`). The `unsupported_findings` rule read the summary, every
   action item and every evidence span of every transcribed meeting, and built a full quality
   response for each one.

The owner chose option N of the design: exact faster Python, plus a cache of judgments in each
API process, plus a warm-up at worker start. No migration, no Redis change. Later the owner also
approved U2: the same cache keeps each meeting's `unsupported_findings` verdict, with the
conditions written in D5.

## Decision

### D1. Exact faster judging

1. `judge_conversation` is split into a live part (`_live_bucket`: no run, stale run, mock, no
   extraction row, not the counted copy) and a content part (`_content_bucket`: version check,
   shape, consistency, dimensions, issues, families). The content part depends only on the
   stored extraction row and the trend definition.
2. `SignatureJudge` (`issue_trends/counting.py`) judges the content part once per distinct
   signature (per dimension: the abstain reason, or the sets of labels) and shares the result
   tuples between equal rows. `tally` counts each shared shape once with a count.
3. Same answers, byte for byte: `tests/test_trend_judgment_cache_judge.py` compares
   `SignatureJudge` with `judge_conversation` on a corpus of realistic and broken rows.

### D2. The judgment cache (N)

1. **Where.** `services/trend_judgment_cache.py`, class `JudgmentCache`. `create_app` creates one
   and stores it as `app.state.trend_judgment_cache`, so there is one per worker process. It is
   never a module global: tests reuse fixed ids across datasets.
2. **What an entry is.** Per analysis run: `(received_at, JudgedContent)`. `JudgedContent`
   (`bucket`, `unreadable`, `dimensions`, `issues`, `families`) is what the run's stored row
   decides. The conversation-level facts (no run, stale, mock, no row, counted copy) are never
   cached; they are read on every request. The entry lives only in memory, so its format is not a
   data contract.
3. **Key.** A map per `CacheKey(organization_id, workspace_id, definition_digest,
   judgment_version)`, and inside it the run id. The caller's role and record scope are not in
   the key on purpose: a run's judgment does not depend on who asks. The scope is applied before
   any lookup: statement P runs under the caller's scope clauses and row-level security, and only
   the run ids it returns are looked up. A re-analysis or a recompute (HOM-288) makes a new run,
   so a new key. `tests/test_trend_judgment_cache.py::test_a_new_run_is_judged_fresh` checks
   that a new run is judged on every judging path.
4. **Why a run can be cached forever.** An extraction row never changes after it is written
   (`services/analysis_extraction.py`), and a run id is a `uuid4` of one workspace.
5. **Rules version.** `JUDGMENT_VERSION = "judgment/1"` (`issue_trends/counting.py`). Bump it
   for any change to what the content part returns. A guard test pins a sha256 of the judgments
   of a fixed corpus next to the current version, so a judging change without a bump fails in CI
   (`tests/test_trend_judgment_cache_judge.py`, `PINNED`).
6. **Statement P with the cache.** P sends no JSON for a run that is in the cache. The cached run
   ids are one statement parameter (a PostgreSQL array, or a JSON array read with SQLite's
   `json_each`), so the statement count does not change: the pins of
   `tests/test_trend_statement_count.py` hold with no assertion changed.
7. **Cap.** `TREND_JUDGMENT_CACHE_MAX_ENTRIES` (default 100,000) entries over all maps and both
   kinds of entry: judgments and the coverage verdicts of D5. A conversation of the warm window
   uses about 2 entries (its judgment and its verdict). Past the cap, a request evicts the
   oldest entries of the least recently used map first; an evicted entry is only a miss next
   time. A request adds its new entries first, then moves the entries it used (its hits) to the
   recent end, and only then evicts: so a wide request (for example 12 weeks) that needs more
   than the free room evicts its own older new entries, never the entries of the default window
   it just used (review round 2). Verdict entries follow the same order. The warm-up never
   evicts (D3.2). 0 keeps nothing.
   **Size, measured** on the realistic perf data (tracemalloc after the request, per entry,
   `w2/verdict-bytes.txt`): a judgment entry keeps about 1,444 bytes at 10k conversations
   (about 2,400 at 1k: equal rows share their judgment tuples, and larger data has more equal
   rows); a verdict entry about 270 bytes. So the default 100,000 entries hold about 50,000
   conversations and about 86 MB per worker (at the 10k sharing level; the 100k run in the PR
   reports the real resident memory). The 4-week window of 100,000 conversations needs about
   66,000 entries, so it fits. A larger server raises the cap with
   `TREND_JUDGMENT_CACHE_MAX_ENTRIES` (about 0.86 MB per 1,000 entries).
8. **Threads.** The routes are sync, so FastAPI runs them in its thread pool. One
   `threading.Lock` guards every read, insert and eviction. No thread reads the database, judges
   or waits while it holds the lock. A request claims a missing run with its own
   `threading.Event`; another request that needs the same run waits on that event (at most 30 s),
   then reads the entry, or judges the run itself if the first request failed. A thread sets its
   own event before it waits on others, so two threads never wait on each other.
9. **Eviction race.** `pin` copies a request's cached contents under the lock, and `resolve`
   returns the contents of the misses. The request uses only these copies. So an eviction in
   between never leaves it without a judgment and never needs another statement.

### D3. The warm-up

`services/trend_judgment_warmup.py`, class `JudgmentWarmup`. The lifespan of `create_app` starts it
after startup when `TREND_JUDGMENT_WARMUP_ENABLED` is true (default), and stops it on shutdown.
It is `app.state.trend_judgment_warmup`, or `None` when it is off.

1. **What.** For each workspace, over the conversations received in the last 35 days (this
   covers the default windows): first the judgments, the most recent conversations first, then
   the coverage verdicts (D5), through the same statements a request runs. Only what is not
   cached yet is read and judged. Judgments go first because they cost the most to build again;
   a verdict also expires after the time limit.
2. **Order and cap.** Workspaces are warmed in the order of their latest processing job, most
   recent first. The warm-up **never evicts**: it inserts with `evict=False`, so it adds entries
   only while the cache has room (`JudgmentCache.room`), and it stops when the cache is full. A
   less active workspace never pushes out what a more active one got. Requests evict as usual
   (D2.7). Test: `test_the_warmup_never_evicts`.
3. **Which workspaces.** The list comes from `processing_jobs`: `meeting_service.py` creates a
   job for every upload, and no product code deletes a job row. It is also the only list across
   tenants that row-level security allows today without a migration. **A workspace whose
   conversations have no job row** (for example rows inserted outside the pipeline) **is not
   warmed**: its first request is cold, and its answer is correct.
4. **Tenancy (security note).** The warm-up is a new user of the existing read-only `recovery`
   service scope (`database/rls.py`, the policy of `processing_jobs`: rows are visible under the
   scope, and its `WITH CHECK` stays bound to the tenant, so the scope can not write). The other
   users are the lease sweeper (`jobs/recovery.py`) and the metrics
   (`observability/metrics.py`). With it, the warm-up runs one statement: `workspace_id` and
   `max(created_at)` of `processing_jobs`, grouped by workspace, in its own short session,
   closed before any other read. It writes nothing under the scope, and the scope reaches no
   customer content. The list only orders the warm-up and never leaves the process. Every
   content read then happens in a new session with one workspace bound as the tenant, so
   row-level security returns only that tenant's rows, and they go into maps keyed by that
   workspace (D2.3), as for a request.
5. **Low priority.** It is a daemon thread started after the app is configured, so it never
   blocks startup or `/readyz`. The judgments go to the cache in batches of at most 256 runs.
   Before each batch, and before the verdicts, it waits while any request uses the cache
   (`JudgmentCache.request()`: the trend judging and every coverage count with the cache),
   checking every 50 ms, then yields the interpreter. It waits only between batches, when it
   holds no claim on a run (D2.8), so a request that needs a run the warm-up is judging waits at
   most for one batch. (Found in review round 1: the first version claimed every run of a
   workspace at once and paused inside that claim, so such a request waited the full 30 s.
   Test: `test_a_request_never_waits_on_a_paused_warmup`.) A request during the warm-up is
   answered correctly, cold where needed.
6. **Stop.** `stop()` sets an event the thread checks between workspaces and before every
   batch, then joins the thread (at most 10 s). It is idempotent. A statement already running in
   the database finishes first.
7. **Failure.** Any error ends the warm-up with state `failed` and a log line
   `TREND_JUDGMENT_WARMUP_FAILED`. The app keeps serving.
8. **Rules-version bump.** A `JUDGMENT_VERSION` bump always comes with a deploy, and a deploy
   restarts the workers, so the warm-up at start covers it.
9. **Cold share.** The estimate `W x E / C` of the cold share of requests after a deploy is a
   best case: each caller warms only the runs of its own scoped, filtered rows.

### D4. Faster coverage counters

The counters keep their definitions (`coverage/1`) and their statements: 1 ledger statement,
plus 8 fact statements when a candidate meeting exists.

1. The revision and run statements return only the columns the rules read, not ORM objects, and
   only revisions with a segment.
2. Each transcribed meeting gets its evaluation bucket once (`_evaluation_bucket`), however many
   groups (whole workspace, each team) count it. It follows the order of
   `completeness_notices` without building the quality response.
3. `unsupported_finding_count` runs only for a meeting that reaches that rule, and the span
   statement reads only the finding types it counts (`summary`, `insight`, `action_item`).
4. `Coverage.evaluated` (the review sampling list) is built only when it is read, by the old
   `_count` over the same facts, so it is the old list.
5. **Drift guard.** `reference_coverage` keeps the old Python path as the oracle.
   `tests/test_coverage_sql_parity.py` requires identical counters, statement counts and
   per-meeting buckets on the HOM-84 reference dataset and on a generated set with a fixed,
   printed seed (`COVERAGE_PARITY_SEED`, default 312). The generated set reaches every
   evaluation bucket and every processing bucket, with missing and malformed fields.

### D5. Cached coverage verdicts (U2)

1. **What.** Per meeting, the `unsupported_findings` verdict (the count of
   `unsupported_finding_count`), in the same cache, under `VerdictKey(organization_id,
   workspace_id, verdict_version)` with the meeting id inside. `VERDICT_VERSION = "verdict/1"`
   (`services/coverage_service.py`): bump it when `unsupported_finding_count` or what it reads
   changes.
2. **Fingerprint.** Unlike an extraction row, the verdict inputs can change. So the revision
   statement also returns, per meeting: the summary id, its `updated_at` and its run; the count,
   latest `updated_at` and largest id of the AI action items; the count, latest `created_at`
   and largest id of the spans of the counted types. An entry is used only when its fingerprint
   is the same. Summaries and action items set `updated_at` on every ORM update; spans can not be
   updated, only added or deleted; a re-analysis replaces the summary (a new id).
3. **Only the misses are read.** After the runs and stage runs, the meetings whose bucket reaches
   the verdict step look up their verdict. The summary, AI action item and span statements then
   read only the meetings with no usable entry (one parameter). Still 8 fact statements.
4. **Race guard.** A verdict is kept only when the rows it was judged from have the fingerprint
   the lookup used. A write between the two statements is judged correctly for that request but
   not kept.
5. **Time limit.** `TREND_COVERAGE_VERDICT_TTL_SECONDS` (default 600). An entry older than the
   limit is not used; it is judged again. With 0, no verdict is ever reused (and none is kept).
6. **Cap and entry.** Verdict entries count against the same cap and LRU as judgments, and the
   warm-up fills them only in free room (D3.2). An entry keeps a 128-bit `blake2b` digest of
   the fingerprint, not the fingerprint. The digest is of the fingerprint's canonical text:
   `repr` of the tuple with every aware datetime in UTC first (`repr` is exact for its values:
   str, int, None, datetime; two aware datetimes at the same instant are equal in any zone, so
   they must give the same text). Measured bytes per verdict entry on the same real data
   (`w2/verdict-bytes.txt`): the earlier 64-bit `hash`, 280; the full tuple (option A), 759
   (+171%); lossless packed bytes (option B), 404 (+44%); the 128-bit digest (option C), 324
   in the prototype and 270 to 282 in the final code. A and B compare exactly but cost too much
   memory per entry, so C was chosen: a collision chance of 2^-128 per compare, below hardware
   error rates.
7. **Who uses it.** `GET /admin/coverage` and every trend route that adds coverage
   (`compute_coverage` and `compute_coverage_by_team` take `cache` and `organization_id`).
   A coverage counted with the cache has no `evaluated` list (reading it raises), so review
   sampling (`services/review_sampling_service.py`) counts without the cache.
8. **Tests** (`tests/test_trend_coverage_verdict.py`): a key point evidence edit, a new human
   span, a deleted span, a re-analysis, a summary edit and AI action item changes are each read
   through the route before and after, with a warm cache, and checked against an empty cache.
   Also: another workspace never shares an entry, the cap, the time limit (and 0), the race
   guard, and the parity with the reference path on a generated set.

**What can still be stale (condition e).** The write search
(`w2/u2-write-search.md`, also in the PR body) found no application write that changes a
verdict input without changing the fingerprint. These paths can:

| Path | What changes | Stale for at most |
| --- | --- | --- |
| Manual SQL by an operator (for example `UPDATE meeting_summaries SET key_points = ...` without touching `updated_at`) | a summary, action item or span in place | the time limit (600 s), or until the next restart |
| A migration run without restarting the API | rows of the three tables | the time limit, or until the restart (a deploy restarts the API) |
| A backup restore promoted without a restart | every row | the time limit, or until the restart |
| A delete of one span plus an insert of another with an older `created_at` and a smaller id, in one meeting (clock skew between hosts) | the span set, with the same count, latest time and largest id | the time limit |
| Two different fingerprints with the same 128-bit digest (D5.6) | a verdict of another state of the meeting | the time limit; the chance is 2^-128 per compare |

A deleted analysis run is **not** such a path. Its spans and its extraction row are deleted with
it (`ON DELETE CASCADE` on `evidence_spans.analysis_run_id` and
`analysis_extractions.analysis_run_id`, migration `e6a9c3d7f2b5` and the live schema), which
changes the span count of the fingerprint. The summary's `analysis_run_id` becomes NULL
(`ON DELETE SET NULL`), which changes the summary run of the fingerprint. The action items'
`analysis_run_id` also becomes NULL, but the verdict does not read it. No product path deletes
a run of a live meeting today.

The judgments of D2 have no time limit. No application path changes an extraction row after it
is written, so only a manual SQL change to `analysis_extractions` can make one stale, until the
worker restarts or the entry is evicted.

## Consequences

- The cache is per worker. After a deploy each worker starts cold and the warm-up fills it.
  With low traffic and many workers, more requests are cold (see D3.9).
- Memory: up to the cap per worker. Measured per entry: about 1,444 bytes for a judgment (10k
  data) and about 270 for a verdict, so about 86 MB for the default 100,000 entries (about
  50,000 conversations). Larger servers raise `TREND_JUDGMENT_CACHE_MAX_ENTRIES`. The 100k
  resident memory is in the PR body.
- A judging change needs a `JUDGMENT_VERSION` bump. The digest guard makes a missing bump fail.
- A coverage verdict can be stale only through a path outside the application, for at most the
  time limit. Setting it to 0 turns U2 off.
- Turning the cache off: `TREND_JUDGMENT_CACHE_MAX_ENTRIES=0` (every request judges again) and
  `TREND_JUDGMENT_WARMUP_ENABLED=false`.
- The 12-week organization routes stay over the 1 s target (reported in the PR).

## Follow-ups (not in this ticket)

- **Option 3: a shared Redis cache.** One cache for all workers, so one cold request per
  workspace instead of one per worker. It costs a network round trip and a serialization of each
  judgment, keys must carry the tenant, and the Redis memory limit and eviction become part of
  the trend answer time.
- **Option 4: a stored judgment table (the smallest migration).** A table
  `trend_extraction_judgments` (extraction id, run id, definition digest, `JUDGMENT_VERSION`,
  workspace id, a short code of the bucket and the row states), written in the same transaction
  as the extraction row and listed in the row-level security tables. Every worker is warm after a
  deploy. The stored code format then becomes a data contract and needs its own ADR, a backfill
  command, and a cleanup of rows of an old version.
