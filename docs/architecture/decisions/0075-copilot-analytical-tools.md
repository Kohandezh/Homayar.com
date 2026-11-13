# ADR 0075: Allowlisted read-only analytical tools for the Copilot

Date: 2026-11-13 · Status: Proposed (HOM-96, WP17-T05, parent HOM-21, features F57 F58) · Builds on: ADR 0040 and the HOM-92 catalogue

## Context

The analytical Copilot (WP17) must answer business questions with exact numbers. F58 asks
for deterministic counts that are 100% exact, abstention on unsupported questions, and no
cross-tenant or cross-team leak under prompt injection and guessed ids.

HOM-92 (`backend.copilot_intents`) already decides how a typed request is routed and gates
the cells of an answer. HOM-80 to HOM-83 compute the issue trends, their role views and
their supporting conversations. Nothing ran the SQL for the Copilot yet: a model had no
safe way to ask for a number.

The model's output is untrusted. A transcript can inject text into it. So the model must
never write SQL, never choose a table and never set its own scope.

## Decision

### 1. A fixed allowlist of five tools

`backend/copilot_tools/registry.py` holds `TOOLS`, a read-only mapping of five tools:
`conversation_count`, `action_items_due`, `scorecard_summary`, `issue_aggregate`,
`top_issues`. Each tool is a fixed SQLAlchemy query with bound parameters. The model sees
only `tool_schemas()` (JSON Schema objects with `additionalProperties: false`). A name
that is not in `TOOLS` abstains with `tool_unknown` and is never echoed.

`REGISTRY_DIGEST` is the sha256 of the canonical JSON of the schemas and permissions. A
test pins it, so a change to what the model sees, or to who may run a tool, is visible in
review.

### 2. One entry point with a fixed check order

`services/copilot_tool_service.run_tool(db, *, context, name, params, now, limits)` checks,
in order: programming errors (`ValueError`), a session not bound to the caller's workspace
(`ValueError`, before any query), the tool name, the tool's permissions, the params shape,
scope keys, unsupported words, the tool's own params, then the run. Permission comes before
the params, so a caller without the permission learns nothing from the defects of their
params. Scope keys win over other key defects, as in HOM-92.

### 3. Scope is added by the service

Every query has `workspace_id` equal to the caller's for every tenant table, live meetings
only, and `RecordAccess.meeting_clause()`. Params are ANDed with them, so a param only
narrows. Ids in params are compared, never looked up: a foreign id, an id outside the
scope and a random UUID give the same answer, apart from the caller's own input that
`filters` echoes (SPEC A11). On PostgreSQL the app role's row-level security is a second
wall; a test runs the tools as `homayar_app` and gets the same answers as the DBA view.

### 4. Exact numbers with citations

Counts are integers from SQL rows. A mean score is an exact `Fraction`, rounded half-even
to 2 decimals on integers. Issue numbers come from the `issue_trends` cells unchanged; the
service rebuilds each cell and checks its JSON is equal before `gate_aggregate`.

Every number carries the ids of the records behind it, ascending, capped at
`max_citations`, with the total and a "complete" flag. An issue cell lists conversations
only when it is `computed` (the HOM-83 rule, ADR 0040); any other cell withholds the list.

### 5. The issue tools reuse HOM-92 and HOM-80 to HOM-83

`issue_aggregate` and `top_issues` call `route_request` with catalogue version 1 and a
`CallerScope` built from the caller's record access. A refusal passes through unchanged.
They then run `issue_cohort`, `cohort_parts` and `apply_masks` once. The cell kind is the
stricter of the data kind and the routing decision's kind (SPEC A2). A masked row abstains
`withheld`. A family without issue rows (`qa`) gates its family row (SPEC A1).

### 6. Limits

`ToolLimits` (defaults 5000 rows, 100 citations, 5 s statement timeout, 10 s budget; caps
20000, 500, 15 s, 30 s). The run is inside a SAVEPOINT on the session's connection, so the
caller's pending objects are never flushed (SPEC A12). On PostgreSQL the service sets
`statement_timeout` with `set_config(..., true)` inside the savepoint and sets the old
value back on every exit. A cancelled statement abstains `time_limit_exceeded` and the
session stays usable. More rows than `max_rows` abstain `row_limit_exceeded`; a partial
number is never shown.

## Consequences

- HOM-97 can give a model five tools and show their answers without trusting the model.
- The same question on the same data gives the same JSON, byte for byte.
- No migration, no new table and no route. The tools read existing tables only.
- A new tool, or a change to a tool's meaning, is a new `TOOLS_VERSION`.

## Known limits

- The issue tools check `max_rows` after `issue_cohort` loaded the population (SPEC A10).
  The statement timeout and the time budget still bound the load.
- The time budget is checked after the run; it drops a slow answer but does not stop it.
- SQLite has no statement timeout.
- Two tool calls for one question can see different data (one snapshot per call).
- `key` of an abstention can hold text a model wrote (an unknown param key is echoed).
  HOM-97 must treat it as untrusted: never render it as an instruction or as markup, and
  cap it when logging.

## Not done

- No LLM wiring: no prompt, no model call, no route (HOM-97, HOM-239).
- No `cohort_comparison` and no `coverage_status` tool: `issue_aggregate` abstains
  `intent_not_supported` for them.
- No evidence retrieval (HOM-93 to HOM-95). HOM-93's unmerged tables are not used.

## Alternatives considered

- **SQL written by the model, run read-only.** Refused. A read-only role still reads any
  column of any tenant table the role can see, and the model's text is untrusted.
- **One generic "count rows of table X with filters" tool.** Refused. The scope rules
  differ per table, and a generic tool would let a model pick a table that holds personal
  data.
- **`Session.begin_nested()` for the savepoint.** Not used: it always flushes pending
  objects first, and the tool must never write.
- **Float rounding of the mean.** Refused. A float can round a half the wrong way; HOM-84
  must reconcile numbers exactly.
