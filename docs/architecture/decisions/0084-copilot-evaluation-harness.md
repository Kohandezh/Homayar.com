# ADR 0084: The Copilot evaluation harness, the evidence tool and the adversarial suites

Date: 2026-11-14 · Status: Proposed (HOM-98, WP17-T07, parent HOM-21, features F57 F58) · Builds on: ADR 0075, ADR 0077, ADR 0080 and the HOM-92 catalogue

## Context

HOM-21 promises two things of the analytical Copilot. F57: deletion and
membership changes remove access at once, even before any reindex. F58: at
least 95 of 100 frozen approved business questions answered correctly,
deterministic counts 100 percent exact, unsupported questions abstain, and
zero cross-tenant or cross-team leaks under prompt injection and guessed ids.

The parts existed but nothing joined them. HOM-97 built the guard (prompts,
plan routing, the answer check) and stated "nothing in the backend uses it
yet". HOM-96 built the SQL tools. HOM-95 built bounded hybrid search and
listed "wiring the Copilot `evidence_search` tool to hybrid search" as a
follow-up. There was no model client (none is added here: tests run fake
models), no evaluation runner for a frozen question set, and no adversarial
suite.

The approved 100 questions are an owner artifact. They do not exist in this
repository. Everything that can be tested locally is built here, against a
synthetic placeholder set that is clearly marked as NOT the approved set.

## Decision

### D1. One service function joins the guard, the SQL tool and the evidence tool

`backend.services.copilot_answer_service.answer_question` is the Copilot path
(SPEC 2.3). It is the only code that joins `copilot_guard`, `run_tool` and the
evidence tool of D3 to D7. No HTTP route lives there (that is HOM-239) and no
model client either: the models come in as a `CopilotModel` protocol
(`complete(prompt) -> str`), so tests run deterministic fakes and a real
adapter lands with the panel. The module is named
`copilot_answer_service`, not `copilot_service`: that path is a retired legacy
module that `tests/test_truthful_outputs.py` keeps absent (Amendment 3).

### D2. Live access at the start, a live context after it

`answer_question` first calls
`backend.retrieval.evidence.resolve_scope(db, context, now)`. `None` means
denied: the answer is `refused` / `permission_denied` and no model is called.
Otherwise every later step runs on `replace(context, record_access=scope.access)`,
the live context. A long-lived context object must not keep access after a
membership, team or key change (F57). The retrieval and tool layers re-resolve
the scope again on their own (ADR 0077 D2, ADR 0075): the live context is the
seam, not the only wall. The evidence tool re-resolves it too (Amendment 4
A4.3): `run_evidence_search` calls `resolve_scope` after its argument checks
and before any SQL, so a denied caller abstains `permission_denied` with no
cohort query and no span, and every later step of the tool runs on that live
resolution.

### D3. The search text of the evidence tool

For `conversation_lookup` with a `query` argument: that query. In every other
case: the caller's question. Before the cut, the text loses what the retrieval
query check refuses: each NUL becomes one space and each lone surrogate
(U+D800 to U+DFFF) is dropped (Amendment 4 A4.2). The text is cut to the
hybrid bounds (ADR 0080 D3) by dropping trailing whitespace-separated words
until it holds at most 1000 characters and its `fa` normalization at most 32
words. A text that is empty after the cut gives zero spans and no search runs.
The language of the search is always `fa`.

In `answer_question` a question that holds a lone surrogate is a step-0
`ValueError` (Amendment 4 A4.6): a lone surrogate is not text and the guard's
`frame_untrusted` refuses it, so it is rejected with the other argument
checks, before live access and before any model call. A NUL is text: it never
raises on any route.

### D4. Evidence narrowing inside the candidate SQL

`SearchFilters` gains `meeting_ids` (a frozenset of at most 20000 conversation
ids, each 1 to 64 characters), applied as `Meeting.id IN (...)` with bound
parameters inside `apply_search_filters`, so every search entry point narrows
by it before scoring and before `LIMIT`, exactly like the other filters of
ADR 0080 D4. Why inside the SQL: a post-filter after `k` hits loses recall,
because the asked conversation can sit outside the top hits of a plain search.

* `conversation_lookup` asks one conversation: `meeting_ids = {conversation_id}`.
* `evidence_examples` asks the counted cohort: the union, over the rows of the
  decision, of the window ids `_IssueAnswer.counted` gives, before any
  `max_citations` cut. The public helper
  `copilot_tool_service.counted_conversation_ids` reuses `_IssueAnswer` with
  the evidence decision itself, no second routing: the counted ids
  (`_Counted`) and the masks (`issue_cohort`) do not depend on the decision's
  cell kind, so an `issue_count` decision with the same filters gives the same
  cohort (an `issue_count` decision does have a `cell_kind`, because `count`
  is a cell gate; no gate is read here, Amendment 4 A4.1). Its abstention
  checks are the ones of `issue_aggregate`: a population over `limits.max_rows`
  abstains `row_limit_exceeded`, a withheld row abstains `withheld`. The
  abstention reaches the caller through the public `ToolAbstention` name of
  `copilot_tool_service` (Amendment 4 A4.4), never a private import. No gate
  runs on the narrowing. An empty cohort gives zero spans and no search runs.

### D5. `k` of the search

`arguments.limit` when the decision sets it, else `DEFAULT_EVIDENCE_LIMIT` (5).

### D6. Span text comes only from `fetch_evidence`

The hit entry ids go to `fetch_evidence` (ADR 0077 D4), a second live
authorization check. An id it does not return is dropped, never guessed from
the hit. Each span becomes a guard `EvidenceSpan(span_id=<entry id>,
conversation_id=<meeting id>, text=<text>)`, best hit first.

### D7. Injection text in a span is data

Every span is scanned with `scan_injection`. The signals are recorded on the
result and on the answer, and no span is dropped for a signal (the
`copilot_guard` README rule). The signals stay on the record also when the
gate or the guard then withholds the answer (Amendment 2 A2.4), so an
injection stays visible in the record even when the answer is not shown. The
planner prompt never holds transcript text: it holds the framed question only.

### D8. The SQL route runs `issue_aggregate` with the raw plan params

For the intents `issue_count`, `issue_rate` and `issue_trend` the route runs
`run_tool(db, context=<live context>, name="issue_aggregate", params={"intent":
<plan intent>, "params": <plan params>}, ...)`, where the intent and params
are the parsed raw plan (Amendment 1 A1.1), never a rebuild from the decision
(the decision's filter JSON holds a `window` object, not the `from` / `to`
keys routing reads). Any other `sql_aggregate` intent (`cohort_comparison`,
`coverage_status`) abstains `intent_not_supported` without running a tool; the
abstention still names the tool of the route (A2.5).

### D9. Nothing new is logged

No question text, plan text, answer text, span text, or ids beyond what the
existing services already log.

### The answer shape

`CopilotAnswer` (SPEC 2.3) with the Amendment 2 rules: a step-0 refusal has
`detail=None`, `explanation=None`, `intent_id=None`, `tool=None`, both
catalogue fields `None` and every tuple empty (A2.3); `facts`, `evidence` and
`notices` are empty unless the status is `answered` (A2.4); `tool` names the
tool of the route once the routing answered (A2.5). `observed()` gives the
pure `ObservedAnswer` the scorer reads.

### The question-set format `copilot-questions/1`

One UTF-8 JSON document: `format`, `set_id`, `approved` (a boolean), `notice`
and `questions`. Every unknown key at any level is a defect: a frozen set must
be exact. A repeated JSON key at any level is a defect too, because
`json.loads` would silently keep the last copy (Amendment 5 A5.8); a too
deeply nested document is the same defect as bad JSON, never an unhandled
`RecursionError` (A5.7). A question holds `id` (unique, `^[a-z0-9][a-z0-9_.-]{0,63}$` as a
full match, so a trailing newline is a defect, A2.7), `question` (a non-empty
string of at most 2000 characters, with no lone surrogate: that is what
`answer_question` refuses at step 0, A4.6 and A5.12), `caller` (a key the harness maps to an
identity), `kind` (`count`, `answer` or `unsupported`), `expected`, and the
optional `expected_intent` (a string or absent: an explicit `null` is a
defect) and `slices`. `expected` per kind: `count` a non-empty list of
decimal strings; `answer` optional `cited_conversations` and `must_include`
lists, at least one of them present and non-empty (two absent or two empty
lists pin nothing, so both are defects); `unsupported` `{}`
or `{"reason": ...}`.

Scoring (`score_question`), the first failing rule decides: `count` must be
answered, every expected decimal must be one of the fact values
(`count_not_in_facts`) and appear in the text as a whole number
(`count_not_in_text`). For the text match, Persian and Arabic-Indic digits
read as ASCII, a group separator between digits is ignored, the match touches
no other digit, and a decimal point touches the number only when a digit
follows it (A2.6: `"There were 3."` matches `3`, `"3.5"` does not). The match
is sign-blind (Amendment 5 A5.9, a known limit below). An `answer` must be
answered, cite every expected conversation alias (mapped
through the harness `conversation_ids` map; a missing alias is a `ValueError`
before any rule and whatever the status, A2.8) and contain every
`must_include` text after `normalize_text` and `casefold` on both sides. An
`unsupported` question is correct when the Copilot refused or abstained, with
the pinned reason when the set names one. `intent_match` is reported, never
part of `correct`.

### The metrics and the F58 thresholds

The report (`copilot-eval/1`) holds `total`, `correct`, `overall_accuracy`,
the per-kind blocks (`count` with `exact_count_accuracy`, `answer` with
`answer_accuracy`, `unsupported` with `abstained` and `abstention_rate` over
the unsupported questions, A2.2), `supported_abstained` and
`false_abstention_rate` over the supported questions (`count.total +
answer.total`, A2.1). A ratio is a float, or `null` when its denominator is 0,
and a `null` ratio never meets an F58 check (A1.4).

`f58` is applicable only when the set is approved and holds exactly 100
questions. It passes when all three checks hold: at least 95 correct, an
exact-count accuracy of exactly 1.0 (a set with no count question fails this
check), and an abstention rate of exactly 1.0 (a set with no unsupported
question fails this check).

### The synthetic placeholder set

`backend/tests/fixtures/evaluation/copilot/synthetic_placeholder.questions.json`
(`set_id` `synthetic-placeholder-v1`, `approved: false`) holds 13 questions:
4 counts (one asked by a team-scoped caller, one in Persian), 4 answers (both
evidence intents) and 5 unsupported (the four refusal reasons plus one
supported-looking lookup with no evidence that abstains `no_evidence`). Its
notice starts with "SYNTHETIC PLACEHOLDER. NOT the approved 100 business
questions" and says the approved set is an owner artifact. A result on this
file says nothing about F58.

The fake model scripts live next to it in
`synthetic_placeholder.replay.json`: `{"plans": {<id>: <plan JSON string>},
"answers": {<id>: <writer template>}}`, never inside the question set (an
approved set holds no model output). The replay template syntax (Amendment 1
A1.3): `{count}` is the value of the first `fact` line of the prompt the
writer receives; `{quote}` is the first three words of the first `span:`
block, and the sentence cites that span id; and `{quotes}` (a01 only,
Amendment 5 A5.11) returns one sentence per `span:` block, each quoting that
span and citing it, so a01 pins citations on the whole counted cohort: the
span order of a lexical tie is the entry id, which is random per run. The
fakes read only the prompt they receive, so a wrong pipeline gives a wrong
answer. The harness proves it
can fail: a writer that adds one to every count loses every count question
(the guard abstains `number_not_from_aggregate`, so the wrong number never
reaches the user), and a planner that routes an unsupported question to an
evidence intent drops the abstention rate below 1.

### The adversarial suites

`tests/test_copilot_adversarial.py` walks every deny-case of the SPEC through
`answer_question`: a foreign conversation id, a made-up id (the two answers
equal, field for field), a cross-team lookup by a narrow supervisor, a team
filter outside the caller scope (equal, field for field, to a made-up team),
a malformed plan, a transcript injection (the plan, route and scope unchanged,
the span framed as data, the signal recorded), a writer that obeys the
injection (abstains `answer_contains_instruction`; the exact quote answers),
a soft-deleted conversation with its retrieval rows left in place and no
reindex (never cited again, the count drops by exactly one), a supervisor
whose team assignment ended while the same context object is reused (no
evidence and no count from the team), a deactivated membership on the same
context object (refused, zero model calls) and a revoked API key (refused).
Every answered case also checks the no-leak property: each cited conversation
id and each fact citation id is live-visible to the caller at that moment.

## Consequences

* No migration, no route, no model client, no new dependency.
* `SearchFilters` holds a seventh optional field, `meeting_ids`, added last;
  the existing subset and default-None pins keep passing untouched.
* `copilot_tool_service` gains one public helper; `run_tool` is unchanged.
* `backend/evaluation/copilot.py` is pure (standard library plus
  `copilot_guard.normalize_text`); file input only in `load_question_set` and
  the CLI. The package `__init__` does not import it (Amendment 5 A5.6), so
  `python -m backend.evaluation.copilot` starts without a `RuntimeWarning`;
  import from `backend.evaluation.copilot`.
* The CLI `uv run python -m backend.evaluation.copilot validate <path>` prints
  one sorted JSON document (`format`, `set_id`, `approved`, `questions`,
  `by_kind`, `sha256`) and exits 0; bad data exits 1 with one stderr line;
  bad arguments exit 2.
* The Copilot guard and tools READMEs now point to
  `services/copilot_answer_service.py` instead of saying nothing uses them.

## Known limits

* The planner path answers only `issue_count`, `issue_rate` and `issue_trend`.
  `conversation_count`, `action_items_due` and `scorecard_summary` have no
  HOM-92 intent, and `cohort_comparison` / `coverage_status` have no runner in
  the planner path (they abstain `intent_not_supported`).
* The count text match is sign-blind (Amendment 5 A5.9): the expected value is
  matched in the text without its sign, because Persian and English writers
  say "fell by 3" or "کاهش ۳" for -3 with no minus sign. A text that names the
  wrong sign but the right digits still passes that rule; the fact values
  must still match exactly.
* Evidence spans are lexical-only in production until an `embed_entries` job
  exists (ADR 0080 follow-up); the harness passes no vectors, and the vectors
  seam (`QueryVectors`) is tested with the offline fake of the retrieval
  suite. Lexical-only search with D3 means a question that shares no word
  with the transcript abstains `no_evidence` (Amendment 5 A5.11): the search
  text is the caller's question and a lexical index matches only words that
  appear. The placeholder answer questions use a greeting that always hits,
  and a01 additionally pins citations on the whole cohort.
* A recording soft-deleted alone (original audio removed) does not hide its
  transcript entries from retrieval. That is how retention works today and it
  is unchanged here.
* The harness scores what `ObservedAnswer` shows. A question set can pin
  `intent_match`, but intent mismatches never fail a question.

## Not done

* The approved 100 questions and their reference dataset (an owner artifact),
  and a run with a real model.
* A real model adapter and the HTTP route of the Copilot panel (HOM-239).
* The console filter UI for `SearchFilters`, an ANN vector store and a local
  reranker (ADR 0080 follow-ups).

## Follow-ups

* Run the approved set with a real model behind the same protocol and store
  `GUARD_VERSION`, `catalogue_version`, `catalogue_digest` and
  `REGISTRY_DIGEST` with every answer (HOM-97 asks; the panel stores them).
* Teach the harness to split a "number and examples" question into one
  `issue_count` and one `evidence_examples` request, as the HOM-92 README
  describes, when the approved set needs it.
* Notes for the HTTP route (HOM-239, Amendment 4 A4.5): a non-`str` writer
  output raises `ValueError` (from `check_answer`), while a non-`str` planner
  output is refused `plan_malformed`; and the SQL route routes twice (the
  given catalogue in `route_plan`, the v1 catalogue inside `run_tool`), so
  only the v1 catalogue is supported on that route today.
