# Copilot analytical tools (`backend.copilot_tools`)

HOM-96 (WP17-T05, parent HOM-21, features F57 F58). ADR 0075 records the decisions.

The analytical Copilot answers questions like "how many calls did team A have last week?"
or "which complaints are most frequent?". A model reads the question and picks a **tool**
and its **params**. This package is the list of tools the model may pick, and
`services/copilot_tool_service.py` runs them.

The core rule (from HOM-92): every number comes from an exact SQL count. A number never
comes from a model or from retrieved text.

## What it is and what it is not

It is a fixed **allowlist** of five read-only tools. Each tool is a fixed SQLAlchemy query
with bound parameters. A param can only narrow the query. The model never writes SQL and
never chooses a table.

| Part | Where | Pure? |
| --- | --- | --- |
| The tools, their schemas and limits | `registry.py` | yes |
| The result shape | `results.py` | yes |
| The param checks | `params.py` | yes |
| `run_tool` and the five runners | `services/copilot_tool_service.py` | no (reads the database) |

"Pure" means: no database, no clock, no network. The same input always gives the same
output.

It is not the model. No model is called here, there is no prompt and no LLM client. There
is no HTTP route either. The Copilot path that calls `run_tool` is
`backend/services/copilot_answer_service.py` (HOM-98, ADR 0084).

Import the pure parts from `backend.copilot_tools`, and `run_tool` from
`backend.services.copilot_tool_service`.

## The tools (`copilot-tools/1`)

| Tool | Permissions | What it counts |
| --- | --- | --- |
| `conversation_count` | `meeting:read` | conversations in a window, in total and by status, team, day, week or month |
| `action_items_due` | `meeting:read` | open action items due before a time, and how many of them are overdue |
| `scorecard_summary` | `meeting:read`, `scorecard:read` | outcomes and the mean score of one published scorecard version |
| `issue_aggregate` | `meeting:read` | the HOM-92 intents `issue_count`, `issue_rate`, `issue_trend` |
| `top_issues` | `meeting:read` | the most frequent issues of one trend family |

`TOOLS` is a read-only mapping (`MappingProxyType`) from name to `ToolSpec`. A `ToolSpec`
has `name`, `description` (one English line), `required_permissions` (sorted by value) and
`input_schema` (a JSON Schema object with `"additionalProperties": false`).

`ToolSpec` is immutable. It keeps the schema frozen in private storage, and
`input_schema` is a read-only property that returns a fresh plain dict (with lists) on
every access, equal to the tool's `tool_schemas()` entry. Changing the returned dict
changes nothing in the registry, so no code can open a schema after `REGISTRY_DIGEST` was
taken.

`tool_schemas()` gives a fresh list of plain dicts, sorted by name, of `{"name",
"description", "input_schema"}`. This is the shape a model's tool-use API takes. The schema property names
of a tool are exactly the keys its param check accepts. A test checks both directions.

`REGISTRY_DIGEST` is the sha256 hex of the canonical JSON (sorted keys, no spaces, UTF-8)
of `tool_schemas()`, with each tool's `required_permissions` values added to its item. A
test pins the hex value. Why: any change to what the model sees, or to who may run a tool,
must be a visible change in review.

## The entry point

```python
run_tool(
    db: Session,
    *,
    context: WorkspaceContext,
    name: object,
    params: object,
    now: datetime,
    limits: ToolLimits = DEFAULT_LIMITS,
) -> ToolResult
```

- `name` and `params` are **untrusted**: a model wrote them.
- `context` comes from authorization (the caller, their role, their record scope).
- `now` is the server clock. The service reads no clock except `time.monotonic()` for the
  time budget.
- `db` must already be bound to the caller's workspace (`bind_tenant_context`).

### The check order

The checks run in this order. The first defect decides.

| Step | Check | Result |
| --- | --- | --- |
| 1 | `now` has no timezone; `context` is not a `WorkspaceContext`; `limits` is not a valid `ToolLimits` | `ValueError` (a programming error) |
| 2 | the session is not bound to `context.workspace.id` | `ValueError`; no query runs |
| 3 | `name` is not a text key of `TOOLS` | abstain `tool_unknown`; `tool` is `null` |
| 4 | the caller lacks a permission of the tool | abstain `permission_denied` |
| 5 | `params` is not a mapping with text keys | abstain `params_invalid`, detail `params_not_mapping` |
| 6 | a scope key (`workspace_id`, `organization_id`, `role`, `scope`), first in sorted order | abstain `scope_not_allowed`, `key` = that key |
| 7 | an unsupported word of `issue_trends` (`product`, `sentiment`, ...), first in sorted order | abstain `unsupported_filter`, `key` = that key |
| 8 | the tool's param check (below) | abstain `params_invalid` with `detail` and `key`, or a specific reason |
| 9 | run the tool under the limits | `answered`, or an abstention |

Why this order:

- **A wrong session is a programming error.** A query on a session bound to another
  workspace could read the wrong tenant, so the service refuses before any query.
- **An unknown name is never echoed.** A model could put text in the name. `tool` is
  `null`.
- **Permission before params.** A caller without the permission learns nothing about the
  tool's rules from the defects of their params.
- **Scope keys win over other key defects.** A request that tries to set its own
  workspace or role is the most serious defect, so it is reported first (the HOM-92
  rule).

### The param check (step 8)

Each tool checks its params in this order (SPEC A3):

1. `param_unknown`: the first key, in sorted order, that the tool does not accept.
2. `validate_filters` of `issue_trends` on the tool's trend filter keys that are present
   (`from`, `to`, `team_id`, `agent_user_id`, and for `top_issues` also `language` and
   `source_type`). Its error code becomes `detail` (`filter_bad_type`,
   `filter_bad_value`, `filter_window_invalid`), its key becomes `key`.
3. The tool's own params, in sorted name order. Each one gives `param_missing`, then
   `param_bad_type`, then `param_bad_value` or a specific reason. A rule over two params
   (for example `due_after` before `due_before`) runs after every single param passed.

`key` is always a param key, never a value. No abstention holds a param value or an id.

## The result

`ToolResult.to_json()` always has exactly these keys:

```json
{
  "tools_version": "copilot-tools/1",
  "tool": "conversation_count",
  "status": "answered",
  "reason": null,
  "detail": null,
  "key": null,
  "filters": {"window": {"from": "2026-10-01T00:00:00+00:00",
                         "to": "2026-10-04T00:00:00+00:00",
                         "basis": "meeting_created_at"},
              "team_id": [], "agent_user_id": [], "group_by": "none", "series": null},
  "scope": {"restricted": false},
  "facts": [{"name": "conversations", "group": null, "value": 4, "parts": null,
             "citations": ["0b6f...", "..."], "citations_total": 4,
             "citations_complete": true, "citations_withheld": false}],
  "provenance": {},
  "limits": {"max_rows": 5000, "max_citations": 100, "statement_timeout_ms": 5000,
             "time_budget_ms": 10000}
}
```

- `status` is `answered` or `abstain`. There is no other status.
- On `abstain`: `reason` says why, `detail` and `key` say more when there is more to say
  (else `null`), `filters` is `null`, `facts` is `[]`, `provenance` is `{}`.
- `scope.restricted` is `context.record_access.restricted`, on an abstention too.
- `filters` is what the SQL used: the normalized params with the defaults filled in, lists
  sorted, and the window as `{"from", "to", "basis"}` in UTC.
- `json.dumps(result.to_json(), sort_keys=True)` is the same, byte for byte, for the same
  database state and the same inputs. There is no execution time, no random value and no
  wall clock in a result. Every list has a fixed order.

### Facts

| Key | Meaning |
| --- | --- |
| `name` | what the number is: `conversations`, `action_items_due`, `issue_count`, ... |
| `group` | `null`, or the group of the number, for example `{"status": "completed"}` |
| `value` | an `int`, or a `float` for a ratio or a mean |
| `parts` | `null`, or `{"numerator", "denominator"}` of a ratio or a mean |
| `citations` | record ids behind the number, ascending, at most `max_citations` |
| `citations_total` | how many records support the number (for a count it equals `value`) |
| `citations_complete` | `citations_total <= max_citations` and not withheld |
| `citations_withheld` | `true` when the list must not be shown; then `citations` is `[]` |

Citations are ids only. To read a record, open it through its own route (for example
`GET /meetings/{id}`), which checks access and audits the read.

### Abstain reasons

| Reason | When |
| --- | --- |
| `tool_unknown` | the name is not a tool |
| `permission_denied` | the caller lacks a permission of the tool |
| `params_invalid` | a param defect (`detail`: a `ParamDefect` or an `issue_trends` filter code) |
| `scope_not_allowed` | a scope key in the params |
| `unsupported_filter` | an unsupported word in the params |
| `individual_ranking_not_supported` | grouping by an agent or an assignee, or the HOM-92 `agent_ranking` intent |
| `intent_not_supported` | an intent `issue_aggregate` does not answer (`cohort_comparison`, `coverage_status`, the evidence intents) |
| `row_limit_exceeded` | more rows than `max_rows` |
| `time_limit_exceeded` | a statement cancelled by the timeout, or the run took longer than the time budget |
| `below_minimum_sample` | too few records for a reliable number |
| `not_computable` | the number cannot be computed (an empty denominator, the `qa` family, no scored result) |
| `withheld` | a HOM-82 mask hides a cell of the answer |
| `out_of_scope`, `invalid_request`, `outside_caller_scope`, `causal_not_supported`, `forecast_not_supported` | HOM-92 routing refusals, passed through unchanged |

`ParamDefect` (`detail` of `params_invalid`): `params_not_mapping`, `param_unknown`,
`param_missing`, `param_bad_type`, `param_bad_value`, plus the `issue_trends` filter codes
unchanged.

## Rules for every tool

- **Scope is added by the service, never by a param.** Every query has
  `<table>.workspace_id = <caller's workspace>` for every tenant table it reads,
  `meetings.deleted_at IS NULL`, and `context.record_access.meeting_clause()` (the
  caller's record scope). A filter is ANDed with them, so a filter can only narrow.
- **Ids are compared, never looked up.** A real team id of another workspace, a real team
  id outside the caller's scope and a random UUID give the same answer. `filters` echoes
  the caller's own input, which is not a leak: compare answers with that echoed id
  replaced by one placeholder (SPEC A11).
- **Row limit.** The population query has `ORDER BY <id> LIMIT max_rows + 1`. More than
  `max_rows` rows abstain with `row_limit_exceeded`. Never a partial number.
- **No ranking of people.** Grouping by an agent or an assignee abstains with
  `individual_ranking_not_supported` (HOM-92 rule). A filter on one agent inside the
  caller's scope is allowed.

## The tools in detail

### `conversation_count`

- Params: `from`, `to`, `team_id`, `agent_user_id` (trend filter rules), `group_by`: one of
  `none` (default), `status`, `team_id`, `day`, `week`, `month`.
- Population: `meetings` with `created_at` in `[from, to)` (basis `meeting_created_at`).
  Default window: the 30 days before `now`.
- Facts: `conversations` (the total), then one fact per group:
  - `status`, `team_id`: groups that have a meeting, value ascending, `null` first.
  - `day`, `week`, `month`: one fact per point of `series_points` in `Asia/Tehran`, zeros
    included. A week starts on Saturday. Group: `{"point", "from", "to", "partial"}` with
    local times. `filters.series` is `{"grain", "timezone"}` (else `null`).
- Citations: meeting ids.

### `action_items_due`

- Params: `due_before` (required, ISO 8601 with a timezone), `due_after` (optional, before
  `due_before`, at most 366 days before it), `team_id` (the meeting's team),
  `assignee_user_id` (a UUID or a list), `status` (`open`, `in_progress`, or both; default
  both), `group_by`: `none`, `status`, `priority`, `team_id`.
- Population: `action_items` joined to `meetings`, `due_at` set, `due_at < due_before`,
  `due_at >= due_after` when given. Window basis `action_item_due_at`; without
  `due_after` the window's `from` is `null`.
- Facts: `action_items_due` (total), `action_items_overdue` (`due_at < now`), then one
  `action_items_due` fact per group (present groups, value ascending, `null` first).
- Citations: action item ids.

### `scorecard_summary`

- Params: `scorecard_version_id` (required), `from`, `to`, `team_id`, `agent_user_id` (the
  window is on `meetings.created_at`).
- Population: `scorecard_results` of that version joined to `meetings`. One result per
  meeting: the newest by (`created_at`, `id`). The row limit counts the loaded rows.
- Minimum: `SAMPLE_POLICY_V1`, the agent minimum when an agent filter is given or the
  chosen results come from 1 to 4 agents, else the rate minimum (both 30 in version 1).
  Fewer chosen results abstain `below_minimum_sample`. So an unknown, a foreign and a
  real but empty version id give the same abstention.
- Facts: `scorecard_results` (count), one `scorecard_results` fact per `outcome`, and
  `mean_score_percent`: the mean of `earned_weight * 100 / applicable_weight` over the
  results with `applicable_weight > 0`. The mean is an exact `Fraction`; `parts` is its
  numerator and denominator in lowest terms; `value` is rounded half-even to 2 decimals
  without float arithmetic. No result with weight: abstain `not_computable`.
- Citations: scorecard result ids.

### `issue_aggregate`

- Params: exactly `intent` and `params` (a HOM-92 request). Both are required.
- It calls the HOM-92 `route_request` with catalogue version 1, the caller's scope and
  `now`. A refusal passes through with its reason, detail and key. Only `issue_count`,
  `issue_rate` and `issue_trend` are answered; other answered intents abstain
  `intent_not_supported`.
- The caller's `CallerScope`: the whole workspace when unrestricted, else the supervised
  teams. The SQL still ANDs `meeting_clause()`.
- It runs the HOM-80 to HOM-83 path once: `issue_cohort`, `cohort_parts`, `apply_masks`.
  The cell kind is the stricter of the data kind and the routing decision's `cell_kind`
  (SPEC A2): an agent cell hides a count below the minimum.
- A withheld (masked) row abstains `withheld`. Then `gate_aggregate` checks the cells: the
  window cells, or every point cell for `issue_trend`.
- Facts: one per row (and per point for a trend). `issue_count` gives the numerator;
  `issue_rate` and `issue_trend` give the ratio with `parts`.
- Citations follow the HOM-83 rule: only a `computed` cell lists its conversations (the
  included conversations whose state for the row is `counted`, sorted by meeting id). Any
  other cell (for example an `insufficient_sample` count) has `citations_withheld: true`.
- `provenance`: `catalogue_version`, `catalogue_digest`, the `trend_provenance` keys and
  `disclosure` (the HOM-82 block).

### `top_issues`

- Params: `family` (required, one string), `limit` (1 to 10, default 5), `from`, `to`,
  `team_id`, `agent_user_id`, `language`, `source_type`.
- It routes `issue_count` with the family and the filters, like `issue_aggregate`.
- It ranks the issue rows of the family by count (highest first, then issue id) and keeps
  `limit` of them. Group: `{"rank", "row"}`.
- A withheld issue row abstains `withheld` before the ranking and before the cut (SPEC
  A7), so the cut never tells which rows were hidden.
- A family without issue rows (`qa`) gates its family row instead (SPEC A1): `qa` abstains
  `not_computable` with detail `scorecard_results_unavailable`.

## Limits

`ToolLimits(max_rows, max_citations, statement_timeout_ms, time_budget_ms)`.

| | `DEFAULT_LIMITS` | `LIMIT_CAPS` |
| --- | --- | --- |
| `max_rows` | 5000 | 20000 |
| `max_citations` | 100 | 500 |
| `statement_timeout_ms` | 5000 | 15000 |
| `time_budget_ms` | 10000 | 30000 |

Every value must be an `int` (not a `bool`) from 1 to its cap, else `ValueError`.

- **Savepoint.** The run happens inside a SAVEPOINT on the session's connection
  (`db.connection().begin_nested()`, SPEC A12). `Session.begin_nested()` would first flush
  the caller's pending objects, and the tool must never write.
- **Statement timeout (PostgreSQL).** Inside the savepoint the service reads
  `current_setting('statement_timeout')`, then sets the limit with
  `set_config('statement_timeout', ..., true)`. On every exit it sets the old value back.
  A cancelled statement (SQLSTATE `57014`) rolls the savepoint back and abstains
  `time_limit_exceeded`. The caller's session stays usable. SQLite has no statement
  timeout.
- **Time budget.** `time.monotonic()` before and after the run. A run that took longer
  than `time_budget_ms` abstains `time_limit_exceeded`, and its answer is dropped.
- **Read-only.** The service runs only `SELECT` (and `WITH`) statements and the savepoint
  commands. It never writes, never flushes and never commits.

## Known limits

- **The issue tools check `max_rows` after the load (SPEC A10).** `issue_cohort` loads the
  whole population of the window before the service can count it. The statement timeout
  and the time budget still bound that load. The other three tools stop at
  `max_rows + 1` rows in SQL.
- **The time budget does not stop a run.** It is checked after the run. A slow Python step
  finishes and is then dropped. Only the statement timeout cancels work.
- **No statement timeout on SQLite.** SQLite is for tests and development.
- **Each run is its own snapshot.** Two tool calls for one question can see different data
  if a meeting changes between them.
- **Small groups by time.** `conversation_count` by day can show a count of 1. A count of
  calls is not judged like a rate, and the caller may open those records anyway (record
  scope).
- **`group_by=team_id` shows team ids.** The caller sees counts of teams that have a
  meeting in their scope only.

## What HOM-97 must do

- Send `tool_schemas()` to the model. Run only what `run_tool` returns; never run SQL from
  the model.
- Pass the caller's `WorkspaceContext`, a session bound to its workspace, and the server
  clock.
- Show an abstention as a clear message from its `reason` and `detail`. Never show a number
  for an abstention.
- Treat `key` as untrusted text. It can hold text a model wrote: an unknown param key is
  echoed as `key`. Never render it as an instruction or as markup, and cap its length when
  you log it.
- Show each number with its `filters` and, when `citations_complete` is false, say that
  the list is cut.
- Store `tools_version` and `REGISTRY_DIGEST` with each answer, next to the HOM-92
  `catalogue_version` and `catalogue_digest`.

## Versions

`TOOLS_VERSION` is `copilot-tools/1`. A change to a tool's meaning (its population, its
facts, its rounding) is a new version. A change to a schema or a permission changes
`REGISTRY_DIGEST`, and the pinned test fails until it is updated on purpose.
