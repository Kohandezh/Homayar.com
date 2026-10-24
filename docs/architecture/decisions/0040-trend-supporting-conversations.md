# ADR 0040: Supporting conversations of a trend cell

Date: 2026-10-24 · Status: proposed (HOM-83, parent HOM-19, features F49 and F61) · Builds on: ADR 0004, ADR 0006, ADR 0012, ADR 0023, ADR 0024, ADR 0038

## Context

HOM-80 built `GET /api/v1/trends/issues` on the caller's record scope. HOM-82 (ADR 0038)
added the role views and the rules that keep a cell from showing one person: the kind of a
cohort comes from its agents, an agent cohort is all or nothing per layer, team cohorts come
from one canonical released set, and organization views decide per workspace and week.

HOM-83 links each trend cell to the conversations behind it, so a reader can check a count
against the records. Three facts shape the answer:

- A list of conversations is a count. Its length, a total, a page cursor or an order can
  rebuild a hidden numerator.
- A trend shows how often an issue was counted. It does not show why. A listed conversation
  is one where the issue was counted, not one that explains the trend.
- An organization role never gives content access (ADR 0012 decision 2). An organization
  caller can open no conversation.

## Decision

1. **One session-only read route.** `GET /api/v1/trends/conversations`, `meeting:read`,
   matrix marker `+session`: every API key gets `401 api_key_not_accepted` before any key
   lookup, like `/trends/teams` and `/trends/me`. Any workspace role with `meeting:read` may
   call it, inside its record scope. Reads are not audited.
2. **It drills into one cell of one workspace view.** `trend_view` is required: `issues`
   (`GET /trends/issues`), `teams` (one team block of `GET /trends/teams`, exactly one
   `team_id`) or `me` (`GET /trends/me`). The request repeats that view's query, checked by
   the view's own parser (for `issues`, one helper that both routes call). `from` and `to` are
   required, each exactly once: the client passes the view's echoed `window.from` and
   `window.to`, so a default window can never move between the view and the link. The cell is
   named by `row` (`family:<id>` or `issue:<id>`, a row the view shows for the same selectors)
   and an optional `point` (the index of a point; absent means the window).
3. **Refusal order.** The credentials; `422 trend_view_invalid`; the view's own query checks
   in the view's own order (for `issues` they end with `403 agent_scope_denied`, the one
   helper of SPEC A5), then `422 trend_window_required` (`details.key` `from`, then `to`);
   `422 trend_team_invalid` (`teams` only), then `422 trend_row_invalid`,
   `422 trend_point_invalid`, `422 trend_page_invalid` (`offset`, then `limit`); the team
   scope (`403 team_scope_denied`, `teams` only); then the cell (`403 trend_links_withheld`).
   Only the last step reads data about the cell.
4. **The decision is the view's own.** Each view's cohort decision is one function
   (`issue_cohort`, `team_scope` with `team_cohort`, `self_cohort`), cut out of the merged
   view with no logic change and called by the view and by the link route. The route then
   makes the cells with the same `cohort_parts` and `apply_masks` calls and reads the target
   cell. Nothing the client sends decides a release. The new
   `JudgedConversation.analysis_run_id` field is read by no decision.
5. **Links only for a `computed` cell.** A cell is linkable when its status, after the
   masks, is `computed`. Every other cell gives no links: `insufficient_sample` (also a rate
   cell whose numerator is shown), `withheld` (all or nothing, or a team outside the
   canonical set), a hidden numerator (an agent cell under 30, also for the agent),
   `not_computable` (the `qa` row, an empty denominator).
6. **One answer for every non-linkable cell.** `403 trend_links_withheld`: the app's error
   envelope with the same message and `details`, and no `notices`. It is decided from the
   cell status alone. The hidden numerator changes nothing in the answer, and no list or
   evidence read runs. A released cell with numerator 0 answers `200` with no conversations.
7. **The list is the numerator.** The conversations of the cohort that are included, lie in
   the point, and have state `counted` for the row: the same rule `tally` uses for the
   numerator. Cell and list come from one load; the service checks that the list length is
   the numerator.
8. **A link holds ids only.** Per conversation: `meeting_id`, `state` (`counted`),
   `analysis_run_id` (the judged run) and `evidence_ids`: the extraction evidence spans of that
   run whose `finding_index` is a kept, auto-accepted value that makes the row hold.
   `evidence_ids` can be empty (the provider cited no valid turn for that value). An empty
   list does not mean "not counted". No agent, team, time, language or source: each would
   split a released cell into cells the views withhold (a per-agent numerator, a team outside
   the canonical set, a masked point, a small language subset). No other issue's state: that
   would be a cross table of two rows. No text: the caller opens text through the existing
   evidence and transcript routes, which apply `choose_view` and audit an original read
   (ADR 0024).
9. **Count and order.** `page.total` is the visible numerator. Offset paging, 50 per page by
   default, at most 100. Order by meeting id ascending, a random UUID, so the order says
   nothing about time, agent, team or value.
10. **Record scope is the open check.** The population requires a live meeting of the
    workspace (`deleted_at IS NULL`) in the caller's `meeting_clause`, the same predicates as
    `GET /meetings/{id}`. A tombstoned or purged meeting, a held tombstoned meeting, an ended
    assignment, an inactive team and another workspace are out. A share never enters the
    record scope, so it never adds a link. The team of a meeting is read at request time, for
    the cell and the list alike.
11. **No links on organization views.** The executive aggregates and the analyst drill-down
    get no link and no count of linkable conversations: the caller may open none of these
    conversations (ADR 0012 decision 2).
12. **Correlation, never cause.** Every trend answer carries `"notices":
    ["correlation_not_cause"]`: `GET /trends/issues`, `GET /trends/me`, `GET /trends/teams`,
    `GET /organizations/{id}/trends/issues`, `GET
    /organizations/{id}/workspaces/{id}/trends/issues` and the link answer. The key is
    additive. No field, status or word says that a conversation or a trend causes an issue.
13. **Cost.** Statements P and G of the view, plus two statements for a non-empty page (the
    extraction rows of the page's judged runs, then their evidence spans), and no coverage
    run. A link request runs the view's count minus 9 plus at most 2. Pinned in a test.
14. **Contract 1.4.0.** The operation is added to the contract snapshot (additive), and its
    description says who may call it and that an API key is refused. The executive
    aggregates, `/trends/issues` and `/trends/me` gain the `notices` key
    (`["correlation_not_cause"]`); `/trends/issues` is not in the snapshot. `/trends/teams`
    and the analyst drill-down carry it already, unchanged
    (`["correlation_not_cause", "unadjusted_comparison"]`). The new link answer carries
    `["correlation_not_cause"]`.

## Product defaults (proposed)

The ticket is silent on these. Each is the safest choice found; the owner may change them.

- The link route refuses API keys, although `GET /trends/issues` accepts them.
- Links list only the numerator (`counted`). No links to `pending_review`, `absent`,
  `missing` or excluded duplicates, and none to the denominator.
- Only a `computed` cell is linkable. The owner may relax this: a rate cell under 30 with its
  numerator shown could be linked, but it may rest on one agent. An agent cell under 30 hides
  its numerator and is never linkable, for the agent too.
- A link shows no agent, team, time, language or source, no other issue's state and no
  evidence of other labels.
- 50 conversations per page by default, at most 100, ordered by meeting id.
- `insufficient_sample`, the `qa` row and an empty cell get the same refusal as a withheld
  cell.
- One route serves the three workspace views; `trend_view=teams` drills one team at a time.
- Organization views get no links.
- Evidence ids come from the judged run, even if a newer run became current since.
- Link reads are not audited.

## Why no migration

The links are derived at read time from the tables HOM-80 already reads, plus the
`analysis_extractions` and `evidence_spans` rows of the judged runs. Nothing is stored.

## Not built

- **A co-occurrence view** (two issues in the same conversations). If built later it says
  "appears together with", never "causes" or "because", and it needs its own small-group
  rule.
- **A "confirmed root cause" state.** It would need an explicit human action on a write
  route, an audit event and storage (a migration). Code never infers it.
- Links on the organization views.
- Links to pending, absent or missing conversations, or to the denominator.
- Text snippets in a link answer.
- API-key access to links.

## Risks

- **Opening the listed meetings.** The normal meeting routes show the team, agent and time of
  each listed meeting. A caller who opens every one can rebuild breakdowns the views
  withhold. They may open those records anyway (record scope, ADR 0004); this is the
  fairness bypass ADR 0038 accepts under "Differencing on workspace views", now cheaper.
- **Differencing two link answers.** Two link answers with different windows, grains,
  languages or sources can isolate a few conversations by id, as ADR 0038 accepts for counts.
  The caller may open those conversations anyway.
- **An own-only cohort.** When a cohort is only the caller's own calls, it has no all or
  nothing rule. A family list minus the lists of its sibling issues then rebuilds the list of
  an issue whose cell is hidden. These are the caller's own calls; fairness only.
- **Inside a rate cohort.** A `computed` point or row can rest on one agent. Its link lists
  that agent's conversations. The cell has at least 30 conversations, the HOM-79 minimum for
  one agent.
- **One snapshot per answer.** A meeting deleted or moved after the load can still be in that
  answer; opening it then answers `404`. Pages of two requests can shift when data changes.
- **An older run.** When a newer analysis run became current after the load, the evidence ids
  are of the judged run. `GET /meetings/{id}/evidence/{evidence_id}` still serves them.
- **One route, three views.** A wrong mapping from `trend_view` to a decision would break the
  release rule. A test checks every cell of each view against its link answer.
- **Cost.** The link route judges the same conversations as the view. Each page reloads the
  whole population, so reading every conversation of a numerator of N costs `ceil(N/100)`
  loads. HOM-84 measures the p95.
