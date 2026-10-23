# Copilot query intents (`backend.copilot_intents`)

HOM-92 (WP17-T01). This package decides how the analytical Copilot answers a typed
request. It holds:

- a versioned catalogue of **intents**, in a JSON file;
- a deterministic **routing contract**: which route answers a request, or why it is
  refused;
- two **gates** that turn an answer that is not reliable into an abstention.

The core rule: **every number in a Copilot answer comes from an exact SQL aggregate. It
never comes from retrieved text or from a model.** Retrieved evidence only supports "what
was said" answers, and it cites authorized records.

## What it is and what it is not

It is pure. It uses no database, no network and no clock. It reads no file except its own
definition files and the pinned trend definition (through
`backend.issue_trends.load_definition`). The same input always gives the same output. It
never changes its inputs.

It is not the model. No model is called here. A later step (HOM-96 and HOM-97) turns the
user's free text into a typed request (`IntentRequest`). This package starts **after** a
typed request exists.

It does not run SQL, retrieve evidence or check authorization. It tells the later
components what they must do, and it checks what they return with the gates.

Nothing in the backend uses it yet. There is no table, no API and no migration here.

The version of the routing rules is `copilot-intents/1` (`CATALOGUE_VERSION`). Import
everything from `backend.copilot_intents`.

## The catalogue file

One file per version: `definitions/{definition_id}.v{version}.json`. Version 1 is
`definitions/intents.v1.json`.

Top-level keys:

| Key | Meaning |
| --- | --- |
| `schema` | always `homayar.copilot-intents/1` |
| `definition_id` | `intents` |
| `version` | an integer, `1` |
| `notes` | required; text or null |
| `trends` | the pinned trend definition: `definition_id`, `version`, `digest` |
| `sample_policy` | the pinned sample policy: `version`, `digest` |
| `coverage_version` | the coverage counters this catalogue answers with: `coverage/1` |
| `intents` | the intents, sorted by id |

An intent has these keys:

| Key | Meaning |
| --- | --- |
| `id` | matches `^[a-z][a-z0-9_]{1,63}$` |
| `route` | `sql_aggregate`, `evidence_retrieval` or `refuse` (never `abstain`) |
| `reason` | a refusal reason, set exactly when the route is `refuse`, else null |
| `gate` | `cell`, `count`, `comparison`, `evidence` or `none`: the gate the answer must pass |
| `target` | `none`, `selector` or `one_row`: which trend rows the request must select |
| `scope_rule` | `caller_scope` (the answer is narrowed by the caller's scope, so any caller) or `whole_workspace` (the answer counts the whole workspace, so only a caller whose scope is the whole workspace) |
| `components` | the WP17 tickets it depends on, sorted, at least one of `HOM-93` to `HOM-97` |
| `definition` | one line, at most 500 characters |
| `filter_keys` | the `issue_trends` filter keys it takes, sorted |
| `params` | its own params, sorted (see "Intent params") |

Why the pins: the catalogue checks filters with one trend definition and gates cells of
one sample policy. A new trend definition or policy needs a new catalogue version, so one
answer never mixes two sets of rules. The catalogue never states the sample numbers
itself. It pins `SAMPLE_POLICY_V1` by its digest, and the numbers live there.

### The loader

`load_catalogue(definition_id="intents", version=1, *, expected_digest=None)` and
`load_catalogue_file(path, *, expected_digest=None)`. They are strict, like the
`issue_trends` loader. They refuse with a `CatalogueError` that has a `code`. The checks run
in this order and the first defect is reported:

| Code | When |
| --- | --- |
| `catalogue_unknown` | a bad id, a bool, a version below 1, no file, or a file with no pinned digest (`load_catalogue` only) |
| `catalogue_unreadable` | the file is missing, not UTF-8 or not JSON |
| `catalogue_duplicate_key` | the same JSON key twice in one object |
| `catalogue_unknown_key` | a key that is not in the shape above, at any level |
| `catalogue_bad_id` | an intent id that does not match the pattern |
| `catalogue_invalid` | a wrong `schema`; a missing key; a wrong type; a broken intent rule (below); intents not sorted; content that does not match the file name |
| `catalogue_duplicate_id` | an intent id twice |
| `catalogue_reference_mismatch` | the trend definition pin does not load with its digest, or the sample policy pin is not `SAMPLE_POLICY_V1`, or `coverage_version` is not `coverage/1` |
| `catalogue_digest_mismatch` | the file holds a released id and version but not its pinned digest, or `expected_digest` was given and differs |

Intent rules (`catalogue_invalid`):

- No intent routes to `abstain`. Only the gates abstain.
- `refuse` has a `reason`, gate `none`, target `none`, no filter key and no param.
- `sql_aggregate` has gate `cell`, `count`, `comparison` or `none`. `evidence_retrieval`
  has gate `evidence`.
- Gate `comparison` is set exactly when the intent takes `compare_by`.
- `scope_rule` `whole_workspace` is only for an `sql_aggregate` intent.
- A param belongs to one route. `grain`, `compare_by` and `compare_values` are for
  `sql_aggregate`. `conversation_id`, `query` and `limit` are for `evidence_retrieval`.
- `compare_by` and `compare_values` come together.
- Target `selector` needs the `family`, `issue` and `service_area` filter keys. Target
  `one_row` needs `family` and `issue`.
- Components, filter keys and params are sorted lists without duplicates.

**Tamper check.** `RELEASED_CATALOGUE_DIGESTS` in `catalogue.py` pins the digest of every
released file. A test pins them too. A changed released file is refused at load time with
`catalogue_digest_mismatch`, not only in CI. A file of an unreleased version (for example
`intents.v2.json`) loads with `load_catalogue_file` when its content matches its name.
`load_catalogue` loads only a released version.

**The digest** is sha256 over the canonical JSON of the parsed file (sorted keys, no
spaces, UTF-8), like the `issue_trends` digest. Formatting does not change it. Any change of
meaning does.

`load_catalogue` is not cached. The caller loads it once and passes it to
`route_request`.

## The intents (version 1)

| Intent | Route | Reason | Gate | Target | Params | Components |
| --- | --- | --- | --- | --- | --- | --- |
| `issue_count` | `sql_aggregate` | none | `count` | `selector` | trend filters | HOM-96, HOM-97 |
| `issue_rate` | `sql_aggregate` | none | `cell` | `selector` | trend filters | HOM-96, HOM-97 |
| `issue_trend` | `sql_aggregate` | none | `cell` | `selector` | trend filters, `grain` | HOM-96, HOM-97 |
| `cohort_comparison` | `sql_aggregate` | none | `comparison` | `one_row` | trend filters, `compare_by`, `compare_values` | HOM-96, HOM-97 |
| `coverage_status` | `sql_aggregate` | `outside_caller_scope` for a team or agent scoped caller | `none` | `none` | `from`, `to` | HOM-96 |
| `evidence_examples` | `evidence_retrieval` | none | `evidence` | `selector` | trend filters, `limit` | HOM-93, HOM-94, HOM-95, HOM-97 |
| `conversation_lookup` | `evidence_retrieval` | none | `evidence` | `none` | `conversation_id`, `query` | HOM-93, HOM-94, HOM-95, HOM-97 |
| `out_of_scope` | `refuse` | `out_of_scope` | `none` | `none` | none | HOM-97 |
| `causal_question` | `refuse` | `causal_not_supported` | `none` | `none` | none | HOM-97 |
| `agent_ranking` | `refuse` | `individual_ranking_not_supported` | `none` | `none` | none | HOM-97 |
| `forecast` | `refuse` | `forecast_not_supported` | `none` | `none` | none | HOM-97 |

"Trend filters" are all nine `issue_trends` keys: `from`, `to`, `team_id`,
`agent_user_id`, `language`, `source_type`, `family`, `issue`, `service_area`. Their rules
are the `issue_trends` rules. This package never re-checks them on its own: it calls
`validate_filters`.

What each one means and why it exists:

- `issue_count`: how many conversations have a trend issue or family. One conversation
  counts once. The number is `cell.numerator` of the `issue_trends` row.
- `issue_rate`: the share of assessable conversations with the issue. The number is
  `cell.ratio`.
- `issue_trend`: the issue rate over time, one cell per point.
- `cohort_comparison`: one rate for two cohorts and their `difference`
  (`compare_cells`). It always carries the notice `correlation_not_cause`
  (`ASSOCIATION_NOTICE`): a difference between two teams is not a cause.
- `coverage_status`: the four `coverage/1` counters (ingestion, processing, evaluation,
  review) for one window. It takes only `from` and `to`. The counters are always
  workspace-wide: `compute_coverage` cannot narrow them to a team or an agent, and
  `GET /admin/coverage` is admin-only today. So `scope_rule` is `whole_workspace`: a caller
  whose scope has `team_ids` or `agent_user_ids` set refuses with `outside_caller_scope`
  (detail `whole_workspace_required`), whatever the params say. Only a whole-workspace
  caller gets an answer. Gate `none`: coverage counters describe the pipeline, so they
  have no sample minimum. A counter with an empty denominator is `not_computable` in its
  own shape. HOM-97 shows it as "cannot be computed", never as 0 or 0%.
- `evidence_examples`: a few cited spans from conversations where an issue was counted.
  Never a number.
- `conversation_lookup`: what was said in one named conversation. Never a number.
- `out_of_scope`: a question that is not about the caller's call analytics (weather,
  general knowledge, writing an email). An unknown intent id refuses the same way.
- `causal_question`: "why does X cause Y". The trend data shows only co-occurrence
  (`ASSOCIATION_NOTICE`). An answer would state a cause that no aggregate supports.
- `agent_ranking`: rank or compare individual agents. It judges people on small, noisy
  samples, and a ranking reveals hidden agent counts by comparison (the complementary
  disclosure problem of `issue_trends`). One agent's own rate is allowed (see "Agents").
- `forecast`: predict a future number. No exact aggregate can give a future value, so it
  would break the core rule.

Questions about a product by name, sentiment, NPS, repeat contact, a phone number or
another entity are not separate intents. Their words are `issue_trends`
`UNSUPPORTED_KEYS`, so any request that carries them refuses with `unsupported_filter`.
Questions about QA scorecards route normally (`family=qa`) and `gate_aggregate` abstains,
because the `qa` cell is `not_computable` (`scorecard_results_unavailable`).

### Intent params

The intent's own params sit in `IntentRequest.params` next to the trend filters. A key
that is not a param of this intent is read as a filter key, so `grain` sent to
`issue_rate` is `filter_unknown_key`.

| Param | Intent | Required | Type | Allowed values | Default |
| --- | --- | --- | --- | --- | --- |
| `grain` | `issue_trend` | no | string | `day`, `week`, `month` | `week` |
| `compare_by` | `cohort_comparison` | yes | string | `language`, `source_type`, `team_id` (`COHORT_KEYS`) | none |
| `compare_values` | `cohort_comparison` | yes | list of 2 strings | each one a valid value of the `compare_by` filter; the two different after normalizing | none |
| `conversation_id` | `conversation_lookup` | yes | string | canonical lowercase UUID text | none |
| `query` | `conversation_lookup` | no | string | 1 to 200 characters after trimming, no control character | none |
| `limit` | `evidence_examples` | no | integer (not a bool) | 1 to 10 | 5 |

Rules:

- `compare_values` keeps its order: the first value is the left side, the second the
  right side. `difference` is left minus right.
- `compare_by` set to `agent_user_id` refuses with `individual_ranking_not_supported`.
  Two agents side by side is a ranking of individuals.
- The `compare_by` key must not also be a filter (`language=fa` plus
  `compare_by=language`): `cohort_conflict`. The other filters apply to both sides.
- `query` is untrusted text. HOM-95 uses it only as a search query. HOM-97 never treats it
  as an instruction.
- Why a fixed `grain` list and no timezone param: HOM-80 cuts the series. The timezone of
  the cuts is HOM-80's choice (`issue_trends` open question 7).

### Targets

- `selector`: at least one of `family`, `issue`, `service_area`
  (`target_required`). A Copilot number always names what it counts.
- `one_row`: exactly one `issue` value or exactly one `family` value, and no
  `service_area` (`target_not_one_row`). A comparison is one rate for two cohorts, so it
  needs one row.
- Selectors that select no row (`family=call_reason` and `issue=complaint_refund`) refuse
  with `target_selects_nothing`. `issue_trends` gives an empty answer there; the Copilot
  refuses, because an empty answer would read as "zero".
- For evidence, the rows are the selected **issues** only (`selected_issue_ids`). Evidence
  comes from issue judgments, so `family=qa` (no issues) selects nothing.

## Routing

`route_request(request, catalogue, *, scope, now) -> RoutingDecision`.

`scope` is a `CallerScope`. It comes from authorization, never from the request:

- `workspace_id`: the caller's workspace;
- `team_ids`: the teams the caller may see, or `None` for the whole workspace;
- `agent_user_ids`: the agents the caller may see, or `None` for every agent the team scope
  allows. An empty set allows nothing.

`now` is the server clock. The package reads no clock.

### Routing order

The checks run in this order. The first defect decides.

| Step | Check | Result |
| --- | --- | --- |
| 1 | `now` is not a `datetime` with a timezone | `ValueError` (a programming error), always first |
| 1 | `request`, `catalogue` or `scope` has the wrong type | `ValueError` |
| 2 | `params` is not a mapping with text keys | `refuse`, `invalid_request`, detail `params_not_mapping` |
| 3 | a scope key (`workspace_id`, `organization_id`, `role`, `scope`), first in sorted order | `refuse`, `scope_not_allowed` |
| 4 | the intent is not in the catalogue (any text, or not text) | `refuse`, `out_of_scope`, detail `intent_unknown`, `intent_id` null |
| 5 | the intent's route is `refuse` | `refuse` with the intent's reason |
| 5b | the intent's `scope_rule` is `whole_workspace` and `scope.team_ids` or `scope.agent_user_ids` is not `None` | `refuse`, `outside_caller_scope`, detail `whole_workspace_required`, `key` null |
| 6 | `validate_filters` on every key that is not a param of the intent | `refuse`, `unsupported_filter` for `filter_unsupported`; else `invalid_request` with the filter code as detail |
| 7 | a filter key the intent does not take, first in sorted order | `refuse`, `invalid_request`, `param_not_allowed` |
| 8 | the intent's params, in sorted order | `refuse`, `invalid_request`, `param_missing`, `param_bad_type`, `param_bad_value` or `cohort_conflict`; an agent cohort gives `individual_ranking_not_supported` |
| 9 | the target | `refuse`, `invalid_request`, `target_required`, `target_not_one_row` or `target_selects_nothing` |
| 10 | a `team_id`, `agent_user_id` or team cohort value outside `scope` | `refuse`, `outside_caller_scope` |
| 11 | none of the above | the intent's route, `reason` null |

Why this order:

- **A scope key wins over everything**, the unknown intent included. A request that tries to
  set its own workspace, role or scope is the most serious defect. It must be told so, not
  "unknown". This is the `issue_trends` precedence, one step wider.
- **A refusal intent does not read its params.** They are never used, so a defect in them
  does not change the answer.
- **A whole-workspace intent checks the caller before its params.** The refusal depends
  on the scope only, so a narrow caller gets the same decision for any params. Only a scope
  key (step 3) is reported first, because it always wins.
- **Unsupported before invalid.** `validate_filters` reports `filter_unsupported` before
  `filter_unknown_key`. The filters run before the intent params, so `product` wins over a
  bad `compare_by`.
- **Scope membership last.** Every check before it is about the form of the request. The
  form never depends on what exists, so the order leaks nothing.

### The decision

`RoutingDecision` is frozen:

| Field | Meaning |
| --- | --- |
| `route` | `sql_aggregate`, `evidence_retrieval`, `refuse` or `abstain` |
| `intent_id` | the catalogue intent, or null when the intent is unknown |
| `reason` | a `RefusalReason` or an `AbstainReason`; null exactly when answered |
| `detail` | the precise defect: a `RequestDefect`, an `issue_trends` filter code, or a cell reason; null when answered |
| `key` | the request key at fault, never its value |
| `gate` | `cell`, `count`, `comparison`, `evidence` or `none`; `none` for every refusal |
| `cell_kind` | `agent` when an `agent_user_id` filter is given **or** `scope.agent_user_ids` is not `None`, else `rate`; null unless the gate is `cell`, `count` or `comparison` |
| `filters` | the normalized `TrendFilters`; null for a refusal and for `conversation_lookup` |
| `arguments` | the checked params (`IntentArguments`); null for a refusal |
| `notices` | `correlation_not_cause` for `cohort_comparison`, else empty |
| `catalogue_version`, `catalogue_digest` | the catalogue that decided; HOM-96 stores them with the answer |

`to_json()` gives the same fields as JSON.

### Refusal reasons

| Reason | When |
| --- | --- |
| `out_of_scope` | the `out_of_scope` intent, or an intent that is not in the catalogue |
| `scope_not_allowed` | the params carry a scope key |
| `unsupported_filter` | the params carry an `issue_trends` `UNSUPPORTED_KEYS` word |
| `invalid_request` | any other defect of the typed request (`detail` says which) |
| `outside_caller_scope` | a team or agent the caller cannot see, or `coverage_status` for a caller narrower than the workspace |
| `causal_not_supported` | the `causal_question` intent |
| `individual_ranking_not_supported` | the `agent_ranking` intent, or `compare_by=agent_user_id` |
| `forecast_not_supported` | the `forecast` intent |

### Teams, agents and leaks

- A `team_id` value outside `scope.team_ids` refuses with `outside_caller_scope`. One value
  outside the scope refuses the whole list.
- **The decision is the same whether the id exists or not**, field for field. The package
  cannot know whether a team exists. It compares the id with the scope only. The refusal
  never echoes the value.
- With `scope.team_ids` `None`, any well-formed `team_id` is in scope. A malformed one is
  `invalid_request` (`filter_bad_value`) before the scope check.
- `agent_user_id` follows the same rule with `scope.agent_user_ids`.
- A team cohort (`compare_by=team_id`) checks both values the same way, with key
  `compare_values`.
- Scope is never a filter. HOM-96 always adds the caller's scope to the SQL by AND, so a
  filter can only narrow what the caller may see. The refusal above stops the obvious case
  early. The AND stops every other case (for example an agent outside the teams when
  `agent_user_ids` is `None`).

### Agents

- One agent's own rate is allowed: an `agent_user_id` filter inside the scope. The
  decision's `cell_kind` is `agent`, so HOM-96 builds the cell with `CellKind.AGENT`. Its
  numerator is hidden below the minimum.
- An agent-scoped caller (`scope.agent_user_ids` set) always gets `cell_kind` `agent`, with
  or without the filter. The scope already narrows every cohort to those agents, so a
  `rate` cell would show a count that the agent rule hides.
- `issue_trends` says a cohort of one person is an agent cell, for example a team with one
  agent. The package cannot see team sizes. HOM-96 must pass `CellKind.AGENT` for such a
  cohort, even when the decision says `rate`.
- Ranking or comparing agents is refused (`agent_ranking`, `compare_by=agent_user_id`).
- A ranking can also be rebuilt from many one-agent `issue_rate` requests. The routing
  sees one request at a time, so it cannot stop that. HOM-97 must never answer a ranking or
  comparison of agents by combining one-agent requests.

## The gates

A route gives the plan. A gate checks the result before anything is shown.

### `gate_aggregate(decision, cell)`

After HOM-96 runs the SQL and builds the `issue_trends` cells, it calls `gate_aggregate`.
`cell` is one item, or a non-empty sequence of items: a time series (one cell per point),
or several rows of one answer. A sequence passes only when **every** item passes. The
decision's gate says which item type it takes:

| Gate | Intent | Items it takes |
| --- | --- | --- |
| `cell` | `issue_rate`, `issue_trend` | `Cell` only (`sample_cell`, `build_rows`, `unavailable_cell`) |
| `count` | `issue_count` | `Cell` only |
| `comparison` | `cohort_comparison` | `Comparison` only (`compare_cells`) |

Why a comparison takes only a `Comparison`: its own `status` and `reason` decide, so each
side is held to `max(kind minimum, min_denominator_comparison)`. A lone `Cell` would skip
the comparison minimum.

Gates `cell` and `comparison`:

| Items | Result | `detail` |
| --- | --- | --- |
| any item `not_computable` | `abstain`, `not_computable` | the cell reason (`empty_denominator`, `scorecard_results_unavailable`) |
| else any item `insufficient_sample` | `abstain`, `below_minimum_sample` | `below_minimum_sample` |
| every item `computed` | the decision, unchanged (the same object) | |

Gate `count` (HOM-79 rule: small counts stay visible, F49 of `issue_trends`):

| Items | Result | `detail` |
| --- | --- | --- |
| any item `not_computable` | `abstain`, `not_computable` | the cell reason |
| else any item with `numerator` `None` (an agent cell under the minimum hides its count) | `abstain`, `below_minimum_sample` | `below_minimum_sample` |
| else, even an `insufficient_sample` rate cell | the decision, unchanged (the same object) | |

Why: a count is exact, even when it is small. Only a ratio needs the minimum. HOM-97 must
show the denominator and the minimum next to a count whose cell is `insufficient_sample`,
for example "5 conversations, out of 10 assessable (a reliable rate needs 30)". It never
shows a ratio for that cell.

`not_computable` wins, in any order. This is the `compare_cells` precedence. The boundary
passes: a denominator equal to the minimum is `computed`.

The minimums are the ones of the cell. The catalogue never re-states them. It pins
`SAMPLE_POLICY_V1`, and `sample_cell` applies it.

A refused or abstained decision is returned unchanged: a gate never turns "no" into "yes".
`ValueError` for an unknown item shape, an empty sequence, an item of the wrong type for
the gate, or a decision whose gate is not `cell`, `count` or `comparison` (an evidence
route, or `coverage_status`). Why refuse the wrong gate: it is a programming error in
HOM-96, and a silent pass could show a number for an evidence route.

### `gate_evidence(decision, authorized_evidence_count)`

After HOM-94 and HOM-95 fetch the evidence the caller may see, HOM-96 calls
`gate_evidence` with the number of authorized spans.

| Count | Result |
| --- | --- |
| `0` | `abstain`, `no_evidence` |
| `1` or more | the decision, unchanged (the same object) |

`ValueError` for a negative number, a bool or a non-int, and for a decision whose gate is
not `evidence`. A refused or abstained decision is returned unchanged.

The count is never shown. It decides the gate only. "We found 4 examples" is not a
frequency and must never be read as one.

## Numbers come only from SQL

- Every number of an answer is a field of an `issue_trends` cell, comparison or row, or a
  `coverage/1` counter, built from SQL integer counts. Only `issue_trends` divides or
  subtracts.
- An evidence route never gives a number. Its decision has no `cell_kind`, and
  `gate_aggregate` refuses it.
- A question that needs a number **and** examples is two requests. HOM-96 (or HOM-97, when
  it builds the typed requests) splits it:
  1. `issue_count` (or `issue_rate`) with the filters: the number;
  2. `evidence_examples` with the **same** filters and a `limit`: the quotes.
  Both decisions carry the same `filters`, so the quotes come from the population that was
  counted. Each goes through its own gate. If the number abstains, the examples may still
  be shown, but with no number.
- A number the model writes in free text is never shown as a fact. HOM-97 removes it or
  replaces it with the number of the aggregate.

## How the intents map to HOM-93 to HOM-97

| Component | What it must do for these intents |
| --- | --- |
| HOM-93 (index) | Index normalized lexical text and versioned embeddings with tenant, team, agent, record ACL and revision metadata, so `evidence_examples` and `conversation_lookup` can filter by scope and by issue before ranking. Index only the current transcript revision and the current analysis run. |
| HOM-94 (authorization) | Apply `CallerScope` (workspace, teams, agents) as a filter **before** retrieval, and again on every evidence span it returns (the rule of `GET /meetings/{id}/evidence`: the meeting must be visible to the caller). For `conversation_lookup`, a conversation outside the scope and a conversation that does not exist give the same result: zero authorized spans, so `gate_evidence` abstains with `no_evidence`. Never a 403 that reveals that the id exists. |
| HOM-95 (search) | Bounded hybrid search inside the decision's `filters` (window, team, agent, language, source) and target issues. At most `limit` spans for `evidence_examples`. For `conversation_lookup`, only spans of that one conversation, ranked by `query` when it is given. Return the count of authorized spans for `gate_evidence`. |
| HOM-96 (analytical tools) | Run the allowlisted, read-only SQL for the `sql_aggregate` intents, with the caller's scope added by AND, row and time limits, and the decision's `filters` and `arguments`. Build cells with `issue_trends` (`build_rows`, `sample_cell`, `compare_cells`) using `cell_kind`, and call `gate_aggregate` before it shows a number. Run `compute_coverage` for `coverage_status` only on a decision the routing answered; that is a whole-workspace caller, because the counters cannot be narrowed to a team or an agent. Never compute coverage for a refused decision. Store `catalogue_version` and `catalogue_digest` with every answer. |
| HOM-97 (abstention and defenses) | Build the typed request, show every refusal and abstention as a clear message from its `reason` and `detail`, and treat transcript text and `query` as data, never as instructions. A request whose params a transcript injected (a scope key, an id outside the scope) is refused by the routing above. Show an `issue_count` whose cell is `insufficient_sample` with its denominator and minimum. Show a `not_computable` coverage counter as "cannot be computed", never as 0 or 0%. Never answer a ranking or comparison of agents by combining one-agent requests. |

## What HOM-98 should test

HOM-98 evaluates 100 frozen business questions and adversarial cases. For this package it
should check:

- Every question of the frozen set maps to one intent of this catalogue, or to
  `out_of_scope`. Record the intent next to each question.
- Every number in an answer equals the SQL aggregate of the reference dataset (HOM-84
  reconciliation), with no rounding difference. Deterministic counts are 100% exact.
- Every `refuse` intent and every refusal reason has at least one question, and the answer
  is the refusal, not a guess: causal, forecast, agent ranking, product by name,
  sentiment, out of scope.
- Abstention: a cohort below the minimum, an empty window, `family=qa`, and a conversation
  with no evidence each abstain with the right reason.
- A ranking of agents rebuilt from one-agent requests ("what is the complaint rate of
  agent A", then of agent B, then "who is worse") is refused, not answered by combining the
  answers.
- `coverage_status` asked by a team or agent scoped caller is refused with
  `outside_caller_scope`. A whole-workspace caller gets the counters.
- A small `issue_count` (for example 5 of 10) is shown with its denominator and minimum. A
  small `issue_rate` abstains.
- Prompt injection and guessed ids: a transcript or a question that asks for another
  workspace (`workspace_id`), another team, another agent, or a made-up conversation id
  never returns data outside the caller's scope. A made-up team id and a real team id
  outside the scope give the same answer, field for field.
- A "number and examples" question gives the number from SQL and quotes with citations,
  and the quotes come from the counted population.
- The same question asked twice gives the same decision and the same numbers.

## Versions and digests

- `CATALOGUE_VERSION` is `copilot-intents/1`. It changes when a routing rule of this README
  changes.
- `RELEASED_CATALOGUE_DIGESTS` and a test pin the digest of every released catalogue file.
  Editing a released file fails at load time and in CI.
- Any change is a new file with the next version. Old files stay forever.
- `load_catalogue` has a default (`intents`, version 1), because the contract of HOM-92
  fixes it. A caller should still pin its version and store the digest.

## Open questions

These are product choices. Version 1 uses the default given here.

1. A count below the sample minimum. Decided: `issue_count` shows an exact
   count with its denominator and minimum. It abstains only when the cell is
   `not_computable` or its count is hidden (an agent cell under the minimum).
2. A time series with one weak point. Default: the whole series abstains. Another choice is
   to show the series with a gap. HOM-96 can already get that by sending one request per
   point.
3. Several rows in one answer (`family=call_reason`, eight issues). Default: one weak row
   makes the whole answer abstain. HOM-96 may split the question into one request per row
   to show the rows that pass.
4. Ranking agents. Default: refused for everyone. Another choice is to allow it for
   managers above the agent minimum, once HOM-82 solves complementary disclosure.
5. Evidence of `pending_review` values. Default: `evidence_examples` uses only counted
   (confirmed) judgments, so a quote never shows a label that no person confirmed.
6. The default `grain` of `issue_trend`. Default: `week`.
7. The default and the maximum `limit` of `evidence_examples`. Default: 5, at most 10.
8. `cohort_comparison` with more than two cohorts. Default: exactly two. A comparison of
   three teams is three requests.
