# ADR 0041: Coverage, scorecard and missing-data metadata for voicemail and organization aggregates

Date: 2026-10-24 · Status: proposed (HOM-299, follow-up of HOM-81) · Builds on: ADR 0012, ADR 0038, ADR 0040

All code paths are under `Homayar/backend/` at base `c5ef682`. Parts changed after the first design review carry "(D2-fix)". Parts changed by leader
decision L2 (SPEC section 13, after the final review) carry "(L2)".

## Context

HOM-81 (PR #125) added three blocks to `GET /trends/issues` and `GET /admin/coverage`:
`coverage` (the `coverage/1` counters), `scorecard` (always `not_available`) and
`missing_data` (sorted codes, each a pure function of the same response,
`services/aggregate_metadata.py:7-8`). HOM-82 (ADR 0038) added them to the role views and
withholds every count of a small group on the organization views. HOM-83 (ADR 0040) kept those
rules for the supporting conversations.

Two aggregate routes still have no metadata. Their population is not "the ledger rows of one
workspace in a window", so `coverage/1` (`services/coverage_service.py:12-15`) does not fit
them as it is:

- `GET /api/v1/voicemails/analytics` counts voicemail rows by `received_at` of the voicemail
  (`voicemail/search.py:149-197`).
- `GET /api/v1/organizations/{organization_id}/aggregates` counts meetings by
  `Meeting.created_at`, one item per scoped workspace, with optional and unbounded
  `since`/`until` and no small-group rule (`services/organization_service.py:756-800`).

The owner's rule for this ticket: the new metadata never reveals what HOM-82 withholds. A
withheld cohort gets no count in its coverage either.

## Decision

### D1. Which routes

1. **Two routes get the metadata.** At base, the only voicemail aggregate is
   `GET /voicemails/analytics` (`routers/v1/voicemail.py:267`; the module docstring lists the
   five voicemail routes, `routers/v1/voicemail.py:1-11`). No other router or service reads
   `VoicemailMessage` (a search of `routers/` and `services/` finds only
   `routers/v1/voicemail.py` and an audit event name). The organization routes that aggregate
   are `/aggregates` (`routers/v1/organizations.py:593`), `/trends/issues`
   (`routers/v1/organizations.py:668`) and the drill-down
   (`routers/v1/organizations.py:697`). `/records` (`routers/v1/organizations.py:625`) is a
   list of metadata, not an aggregate. So `/voicemails/analytics` and `/aggregates` are the
   two routes without the metadata. The leader's table in the SPEC is correct.
2. **The HOM-82 organization trend routes do not change.** Each block already carries
   `coverage`, `scorecard`, `missing_data` and `disclosure`, and a withheld block has null
   counts (`services/role_trend_service.py:708-728`, `schemas/trends.py:273-284`). One known
   gap stays: the coverage counters of a released block count ledger rows outside the trend
   population (deleted, in progress, failed or excluded meetings,
   `services/role_trend_service.py:785-795` with `population=_granted(team_ids)`), while the
   week test reads only the included conversations (`services/issue_trend_service.py:952-957`,
   `services/issue_trend_service.py:248-250`). A released block whose window holds no
   included conversation passes as "0 agents" and still shows, for example, the failed calls of
   one agent. ADR 0038 already records this as the risk "Processing counts of a released
   organization block". It stays a follow-up (R2), not part of this ticket.

### D2. Voicemail population

3. **One unit is one voicemail row, the same set `total` counts.** The population is
   `_scoped(workspace_id, access)` (`voicemail/search.py:62-72`: the workspace, a live meeting,
   the caller's `RecordAccess.meeting_clause()`) with
   `received_from <= VoicemailMessage.received_at < received_to`
   (`voicemail/search.py:172-177`). No other filter: the route has none
   (`routers/v1/voicemail.py:267-272`). So `|population| == total` by construction.
4. **The meeting of the voicemail supplies the processing facts.** Each voicemail has exactly
   one meeting (`uq_voicemail_meeting`, `voicemail/models.py:148`). Its status, current
   transcript revision, analysis run and findings decide the counter buckets, as in
   `coverage/1`.
5. **Not the ledger, and why.** A voicemail's ledger row has the same `received_at` instant:
   the import passes one `received_at` value to the ledger and to the voicemail row
   (`voicemail/ingest.py:211`, `voicemail/ingest.py:318`, `voicemail/ingest.py:332`,
   `services/ingestion_service.py:382`). But a voicemail written without the import (demo or
   legacy data) or whose ledger link is null (`ingestion_id` is `ON DELETE SET NULL`,
   `voicemail/models.py:95-97`) would be in `total` and not in a ledger-based count. Counting
   the voicemail rows keeps `processing.denominator == total` exact.

### D3. Voicemail denominators

6. **Two counters, `processing` and `evaluation`, with the `coverage/1` rules.** The same
   definitions, labels, buckets and bucket precedence as `coverage/1`
   (`services/coverage_service.py:92-131`, `services/coverage_service.py:584-673`), applied to
   the voicemail population:

   | Counter | Numerator | Denominator | Buckets (sum to the denominator) |
   | --- | --- | --- | --- |
   | `processing` | `transcribed` | the population (`total`) | `deleted`, `in_progress`, `failed`, `transcribed`, `no_transcript` |
   | `evaluation` | `fully_evaluated` | `processing.numerator` | the 8 `EVALUATION_BUCKETS` |

   Reconciliation: `processing.denominator == total`; `deleted == 0` always (the population has
   live meetings only); `evaluation.denominator == processing.buckets.transcribed`.
   `processing` reads the **meeting** status (`IN_PROGRESS_STATUSES`, `FAILED_STATUSES`,
   `services/report_service.py:38-49`), not `VoicemailMessage.status`. So `by_status` (the
   voicemail workflow state) and the processing buckets are two views; the ADR promises no
   equation between them (R4).
7. **`evaluation` applies to voicemails.** A voicemail meeting runs the same processing job,
   intelligence included, and the voicemail hook runs after a completed or partial result
   (`jobs/processor.py:2232-2251`). Its call report exists with the same completeness rules, so
   "fully evaluated" means the same as for a call. No made-up number.
8. **No `ingestion` and no `review` counter.** A voicemail row is written only inside an
   accepted ingestion (`voicemail/ingest.py:285-323`), so `ingestion` would always be 100%. A
   rejected voicemail event has a ledger row with no meeting, so it has no team or agent and no
   record scope can contain it. `review` has no source yet (HOM-73,
   `services/coverage_service.py:236-249`). The block keeps the two counters of
   `TrendCoverage` (`schemas/trends.py:124-135`).
9. **Window basis (D2-fix F4, F5).** Two separate fields, two separate meanings:
   - `coverage.window.basis: "voicemail_received_at"` names the column the window filters.
     `coverage.window.from` and `coverage.window.to` echo the route's `received_from` and
     `received_to` (the defaults and the 366-day limit of the route stay,
     `routers/v1/voicemail.py:274-280`).
   - `coverage.basis: "voicemail_population"` names the population: the voicemail rows of
     decision 3.

   The instants match `coverage/1`'s `ingestion_received_at` for imported voicemails (decision
   5), but the population is not the ledger, so the window basis says what is filtered.
   `coverage.version` stays `coverage/1`: the version names the counter rules, and
   `coverage.basis` names the population, as `TrendCoverage.basis` does.

   A `received_from` or `received_to` without a timezone is echoed as the route got it. It is
   never passed to `parse_window`, so the status of every request stays as on base: no new
   `422` and no `500`.


### D4. Organization population (L2)

10. **The population of an item is the HOM-82 block's (L2).** The `coverage` of a released
    item is the `coverage` of the workspace block of `GET /organizations/{id}/trends/issues`
    for the same caller, grant and window `W`: `compute_coverage` over the ledger rows of the
    workspace with `received_at` in `W`, narrowed for a team grant to the accepted rows of the
    granted teams (`population=_granted(team_ids)`), with basis `workspace` (whole workspace)
    or `trend_population` (team grant) (base `services/role_trend_service.py:785-795`). A
    team-scoped grant never counts a meeting outside its active granted teams
    (`services/organization_service.py:703-718` decides the grant, `_granted` applies it).
11. **Why not the `meetings_total` set (L2).** The first design counted the meetings that
    `meetings_total` counts (`Meeting.created_at`, live only). The final review reproduced R7
    with it: a call received in week k-2, where its agent is alone, and created in week k
    showed in the `/aggregates` counters of week k and not in the HOM-82 counters of week k.
    Subtracting one route from the other gave that one call, of a week HOM-82 withholds. The
    owner's rule ("never let the new metadata reveal what HOM-82 withholds") is explicit, so
    the two routes now share one population. Reconciling with `meetings_total` is not an owner
    rule: HOM-81 reconciles coverage with the trend population, not with meeting counts.
12. **Reconciliation (AC4 under L2).** The item counters follow the `coverage/1` rules of the
    HOM-82 block: the buckets of each counter sum to its denominator, and
    `evaluation.denominator == processing.buckets.transcribed`. They equal the HOM-82 block's
    counters field by field. **They do not reconcile with `meetings_total`**, and this is the
    one place the ADR says so: `meetings_total` counts live meetings by `Meeting.created_at` in
    the request window; `coverage` counts ledger rows by `received_at` in `W`, a soft-deleted
    meeting included (bucket `deleted`) and, for a whole-workspace grant, rejected rows and rows
    with no meeting. A client reads `coverage` with `coverage_window`, not with `since`/`until`
    (R6).

### D5. Organization window

13. **The window is a HOM-82 grid window, built by the HOM-82 parser (D2-fix F1).**
    `W` comes from `organization_query` (`services/role_trend_service.py:220-258`), the same
    function `GET /organizations/{id}/trends/issues` runs (`routers/v1/organizations.py:654-659`),
    with one `now` per request:
    - First try: `organization_query(raw)`, where `raw` holds `from` = `since.isoformat()` and
      `to` = `until.isoformat()`, each only when given. So `W` gets exactly the HOM-82 rules:
      both bounds a Saturday 00:00 in Asia/Tehran (`is_week_boundary`,
      `services/role_trend_service.py:210`), `to` at or before the start of the current week
      (`current_week_start`, `services/role_trend_service.py:201`), a missing `to` set to the
      start of the current week, a missing `from` set to `to` minus 4 weeks, a timezone required
      (`issue_trends/filters.py:215-224`), and `from < to` with a span of at most 366 days
      (`validate_filters` then `_window`, `issue_trends/filters.py:233-251`; the constant is
      `MAX_WINDOW` at `issue_trends/filters.py:30`). 366 days hold at most 52 whole weeks
      (364 days), the "at most 52" of ADR 0038 decision 7.
    - If that call refuses (`TrendFilterError`, `TrendSeriesError` or `TrendViewError`, the
      three errors the HOM-82 route turns into `422`, `routers/v1/organizations.py:660-665`), the
      route does not answer `422`. It uses `organization_query({})`: the 4 closed weeks before
      the start of the current week (`ORGANIZATION_DEFAULT_WEEKS`,
      `services/role_trend_service.py:130`). This default call cannot refuse.

    So every `W` is a window that the HOM-82 route itself would accept, and the trend filters
    it returns (`TrendFilters`, grain `none`) are the ones the HOM-82 block of D6 runs with.
    A `since` or `until` without a timezone is refused by the parser, so `W` falls back to the
    default. The status of the request stays as on base.

    Reason: the disclosure rule (D6) is decided per closed week, as in HOM-82. A free window
    defeats any small-group test, because two answers can be subtracted (`[a, c)` minus
    `[a, b)` gives `[b, c)`, which can hold one agent). Only whole closed weeks, each tested,
    keep every released number a sum of released weeks (ADR 0038 decision 7).
14. **The existing fields keep their window and behavior.** `since`, `until`, `meetings_total`
    and the rest still use the request window as today, and no request that works today gets a
    new `422`. A new top-level `coverage_window` names `W` with `TrendFilters.window.to_json()`:
    the same object as the `window` of the HOM-82 answer for that window (basis
    `ingestion_received_at`). It is always present, also when every item is withheld (a window
    is not a count).

### D6. Disclosure on organization aggregates (L2)

15. **The item metadata is the HOM-82 block (L2).** For each scoped workspace, with the
    workspace bound as the tenant (`services/organization_service.py`, `_item_metadata`), the
    route runs `organization_workspace_block` (new in `services/role_trend_service.py`): the body of
    the executive view's loop moved into one helper, which `organization_trends` now calls too
    (same statements, same answers). It runs the HOM-82 decision as is (statements P and G,
    `known_meetings`, `week_parts`, `caller_release`, `_decide`, so HOM-82's own A6), then the
    coverage of a released block, then `_organization_block` with grain `none`. The item takes
    the block's `coverage`, `scorecard`, `missing_data` and `disclosure` unchanged
    (`AGGREGATE_METADATA_KEYS`).

    **Invariant.** For the same caller, grant and window `W`, the `/aggregates` item metadata
    equals the metadata of the workspace block of `GET /organizations/{id}/trends/issues`
    field by field, or both are withheld. Any difference between the two routes is zero by
    construction. Grain does not change these four keys: `trend_missing_data` reads the window
    rows, never the points (base `services/aggregate_metadata.py:79-109`).
16. **The meeting test is removed (L2).** The first design added a second test over the
    meetings that `meetings_total` counts, with its own A6 logic rebuilt over meetings. It
    could only withhold more than HOM-82, and the second population it needed is what made R7
    possible. L2 removes it with its helpers (`item_meetings`, `known_meeting_ids`,
    `meeting_release`, the meeting week functions, `restricted_record_access`,
    `workspace_release`). One rule, less code.
17. **A withheld item.** `coverage: null`, `missing_data: null`, `scorecard` stays the
    `not_available` block, and `disclosure` is the HOM-82 block's
    `{**withheld_block(), "reason": <reason>}` (`services/issue_trend_service.py:895-897`,
    `services/role_trend_service.py:708-728` at base) with reason `small_group` or
    `complementary_disclosure`. The shape follows `OrganizationTrendBlock`
    (`schemas/trends.py:273-284`). A withheld item runs no coverage statement. A released item
    has the HOM-82 block's `{"status": "released", "reason": null, "kind": "rate",
    "masked": []}`.
18. **The withheld signal and audit (D2-fix F6).** This ADR accepts the HOM-82 signal: a
    withheld item tells the caller that some week rests on 1 to 4 agents, or that the rest of
    the workspace does (ADR 0038 decision 7 and its risk "One yes or no per week leaves the
    grant"). Both routes stay reads that are not audited, as today and as the HOM-82 views.
19. **The existing fields are not changed.** `meetings_total`, `meetings_by_status`,
    `audio_seconds_total` and `action_items_total` keep their base values and base meaning
    (`Meeting.created_at`, live meetings), also on a withheld item. They already show what
    HOM-82 withholds: the counts of a workspace or a team grant with 1 to 4 agents, for any free
    window. This ADR records it as risk R1 and a follow-up. It does not change them (the ticket
    adds fields only).

### D7. Disclosure on voicemail aggregates

20. **No HOM-82 rule applies.** The route is a workspace route with `meeting:read`
    (`routers/v1/voicemail.py:270`). Every count is over the voicemails the caller can already
    list with `GET /voicemails` (the same `_scoped` clause, `voicemail/search.py:62-78`). The
    facts behind each bucket (the meeting status, the transcript, the report's completeness)
    are readable by the same caller one by one: `GET /voicemails/{id}` shows `meeting_status`
    and the transcript excerpt (`routers/v1/voicemail.py:302-311`), and
    `GET /meetings/{id}/report` shows the completeness (`routers/v1/reports.py:651-662`), both
    with `meeting:read` inside the same record scope. HOM-82 withholds counts from callers who
    cannot open the records (organization roles, ADR 0012) or that would rank people. Neither
    holds here. A voicemail meeting's agent is the meeting's `agent_user_id`, often null; the
    route has no agent or team selector, so it builds no per-agent cohort.

### D8. Organization total

21. **No organization total.** `/aggregates` gets per-item metadata only, as HOM-82 has no
    organization total (ADR 0038, "No organization total and no all-teams total"). Top-level
    additions are metadata, not counts: `disclosure_policy` and `coverage_window`.

### D9. missing_data codes

22. **No new code.**
    - Voicemails: one new pure helper in `services/aggregate_metadata.py`,
      `population_missing_data(coverage, scorecard)`: `scorecard_unavailable` while the
      scorecard is `not_available` (always, today); `processing_incomplete`,
      `processing_unavailable`, `evaluation_incomplete`, `evaluation_unavailable` by
      `counter_indicators` (`services/aggregate_metadata.py:66-72`); `empty_population` when
      `coverage.processing.denominator == 0` (that is `total == 0`). `missing_values`,
      `pending_review`, `insufficient_sample` and `source_unavailable` read trend rows; this
      answer has none, so they never appear. `ingestion_*` and `review_*` need counters this
      block does not have.
    - `/aggregates` items (L2): the HOM-82 block's `missing_data` (`trend_missing_data`,
      `services/aggregate_metadata.py:101-109`), unchanged: it may also hold the trend row codes
      of that block. A withheld item has `missing_data: null`, never a list.

    Each code is a function of the same block, sorted and distinct
    (`services/aggregate_metadata.py:97-98`).

### D10. Contract

23. **Both operations join the ingestion contract, version 1.5.0.** In
    `contracts/openapi_contract.py`: `CONTRACT_VERSION = "1.5.0"` (line 36); a description
    paragraph "**Aggregate metadata (1.5.0).**"; two `OPERATION_NOTES` with "Since 1.5.0" and
    two `OPERATIONS` entries:
    - `("get", "/api/v1/voicemails/analytics")`: `(401, 403, 422, 429, 503)`. It accepts an API
      key with `meeting:read` today (`require_permission` on `require_workspace`,
      `security/context.py:315-316` and `security/context.py:369`), so it belongs in the
      API-key contract. `422` is `invalid_window`.
    - `("get", "/api/v1/organizations/{organization_id}/aggregates")`:
      `(401, 404, 422, 429, 503)`, as the HOM-82 executive route
      (`contracts/openapi_contract.py:183`). It is session-only today: an API key gets
      `401 api_key_not_accepted` (`security/context.py:205-216`, `security/context.py:495-497`).
      The note says so, as the HOM-82 notes do. It says that the item metadata is the HOM-82
      workspace block's, and that `coverage_window` is "the window that
      `GET /organizations/{organization_id}/trends/issues` would use for `from=since` and
      `to=until`, or its default (the 4 closed weeks before this week) when that route would
      refuse them" (L2).

    Regenerate `contracts/ingestion-v1.openapi.json` with
    `uv run python -m backend.contracts.openapi_contract --write`. In
    `tests/test_openapi_contract.py`: add both operations to the operation list
    (`tests/test_openapi_contract.py:33-38`) and set the version check to `1.5.0`
    (`tests/test_openapi_contract.py:222`), as HOM-83 did. No auth rule changes, so
    `docs/security/authorization-matrix.md` does not change (rows 276 and 297 stay).

### D11. Query cost

24. **`/voicemails/analytics`: 0 or 8 more statements, whatever the voicemail count
    (measured).** The existing statement gains the meeting columns (`Meeting.id`, `status`,
    `deleted_at`, `team_id`, `agent_user_id`, `source`, `language`; the join exists already,
    `voicemail/search.py:65`). Its rows and the existing values do not change. The fact load
    of `coverage/1` adds 8 statements, only when a meeting is completed or partial
    (`services/coverage_service.py:428-436`). Measured on PostgreSQL
    (`test_voicemail_statements_do_not_grow_with_the_voicemails`): a window with no voicemail
    and a window with one queued voicemail run the same statements as on base; one completed
    voicemail adds 8; six completed voicemails add the same 8.
25. **`/aggregates`: 3, 12 or 14 more statements per scoped workspace (L2, measured).**

    | Part | Statements | When |
    | --- | --- | --- |
    | existing (status counts, action items) | 2 | always, unchanged |
    | HOM-82 statements P and G (`judge_population`) | 2 | always |
    | the caller's workspace membership (`known_meetings`) | 1 | always |
    | assignments and scope meetings (`known_meetings`) | 2 | a restricted membership |
    | coverage ledger statement | 1 | released |
    | `coverage/1` facts | 8 | released and a meeting may be transcribed |

    Measured on PostgreSQL (`test_statements_per_workspace`, one more scoped workspace each
    step): a withheld item (1 meeting, 1 agent) adds 5 statements (2 existing + 3 new); a
    released item with 12 meetings adds 14 (2 + 12); one with 24 meetings adds 14 too; a
    released item of a caller with a restricted membership adds 16 (2 + 14). These are the
    HOM-82 executive view's own statements per workspace. The count does not grow with the
    number of meetings or teams. Python work grows with the conversations of `W` (at most 52
    weeks), as on the HOM-82 route. Latency is HOM-84. No cache.

### Response fields

New shared models in `schemas/coverage.py`, for the voicemail route only:

```python
class PopulationCoverageWindow(BaseModel):
    """``from <= <basis column> < to`` of a population that is not the ledger (HOM-299)."""

    model_config = ConfigDict(populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime
    basis: Literal["voicemail_received_at"]


class PopulationCoverage(BaseModel):
    version: Literal["coverage/1"]
    window: PopulationCoverageWindow
    basis: Literal["voicemail_population"]
    processing: CoverageCounter
    evaluation: CoverageCounter
```

The two basis fields are separate (D2-fix F4):

| Route | `coverage.window.basis` (the filtered column) | `coverage.basis` (the population) |
| --- | --- | --- |
| `/voicemails/analytics` | `"voicemail_received_at"` | `"voicemail_population"` |
| `/aggregates` item (L2) | `"ingestion_received_at"` | `"workspace"` or `"trend_population"` (as the HOM-82 block) |

`VoicemailAnalyticsResponse` (`schemas/voicemail.py:77`) adds, every one required and
non-null:

| Field | Type |
| --- | --- |
| `coverage` | `PopulationCoverage` |
| `scorecard` | `AggregateScorecard` |
| `missing_data` | `AggregateMissingData` |

`WorkspaceAggregateResponse` (`schemas/organizations.py:149`) adds (L2: the HOM-82 block's
types):

| Field | Type | Null when |
| --- | --- | --- |
| `coverage` | `TrendCoverage \| None` (`schemas/trends.py:124`) | withheld |
| `scorecard` | `AggregateScorecard` | never |
| `missing_data` | `AggregateMissingData \| None` | withheld |
| `disclosure` | `TrendDisclosure` (`schemas/trends.py:159`) | never |

`AggregatesResponse` (`schemas/organizations.py:159`) adds:

| Field | Type |
| --- | --- |
| `disclosure_policy` | `OrganizationDisclosurePolicy` (`schemas/trends.py:155`), from `organization_disclosure_policy()` |
| `coverage_window` | `TrendWindowResponse` (`schemas/trends.py:43`, basis `ingestion_received_at`), the `W` of decision 13 |

A withheld item:

```json
{
  "workspace_id": "…", "workspace_name": "…", "team_ids": ["…"],
  "meetings_total": 3, "meetings_by_status": {"completed": 3},
  "audio_seconds_total": 540, "action_items_total": 2,
  "coverage": null,
  "scorecard": {"status": "not_available", "version": null, "digest": null,
                "reason": "scorecard_results_unavailable"},
  "missing_data": null,
  "disclosure": {"status": "withheld", "reason": "small_group", "kind": null, "masked": []}
}
```

No existing field is renamed, removed or retyped.

### Code shape

- `services/coverage_service.py`: `_load_facts` gets an optional `candidates` subquery
  (default: today's `_candidate_meetings`, so existing callers run the same statements). A new
  `compute_population_coverage(db, *, workspace_id, window, rows, candidates,
  recompute_max_attempts)` feeds rows with the `_ledger_rows` attributes (`status` accepted,
  `reason` null) to the unchanged `_count`, and keeps `processing` and `evaluation`. Voicemails
  only.
- `services/aggregate_metadata.py`: `population_coverage_block`, `population_missing_data`.
- `voicemail/search.py`: the meeting columns and the coverage of the population.
- `services/role_trend_service.py`: two exposed helpers, no behavior change.
  `organization_workspace_block(db, *, workspace, team_ids, user_id, filters, grain, weeks,
  recompute_max_attempts)` holds the body of the `organization_trends` loop
  (base `services/role_trend_service.py:776-811`), and `organization_trends` calls it.
  `organization_weeks` exposes the closed weeks of a window.
- `services/organization_service.py`: `workspace_aggregates` takes an optional
  `trend_block(workspace=, team_ids=)` callable; per workspace it binds the tenant, runs it and
  keeps `AGGREGATE_METADATA_KEYS`. It does not import `role_trend_service` (that module imports
  `organization_service`). The router, which imports both already
  (`routers/v1/organizations.py:57`), builds `W` and the trend filters with
  `roles.organization_query` (decision 13) and passes `organization_workspace_block` with
  `functools.partial`.
- Routers and schemas: the fields above. Docstrings name HOM-299.

## Product defaults (proposed)

The ticket is silent on these. Each is the safest choice found; the owner may change them.

- Voicemail coverage counts voicemail rows, not ledger rows, and keeps the `coverage/1` name
  with a new `basis`.
- No `ingestion` or `review` counter on the voicemail route.
- `/voicemails/analytics` has no small-group rule: the caller can open every counted record.
- `/aggregates` metadata uses only a window the HOM-82 route accepts. A request window that
  the HOM-82 parser refuses gets the metadata of the last 4 closed weeks, named in
  `coverage_window`.
- An `/aggregates` item carries the HOM-82 block's metadata as is (L2): released or withheld
  exactly when HOM-82 releases or withholds that block.
- Withheld items keep their existing counts (R1).
- No organization total.

## Open questions for the owner

1. Is the 4-closed-week fallback for the `/aggregates` metadata acceptable?
2. Should a follow-up apply the HOM-82 rule to the existing `/aggregates` fields (R1)? It is a
   breaking change for the console.
3. Keep `coverage/1` as the version name for the voicemail population, which is not the
   ledger?

## Why no migration

Everything is read from the tables that `GET /voicemails/analytics`, `/aggregates`,
`coverage/1` and the HOM-82 views already read. The disclosure decision depends on the
conversations of the answer, so nothing is stored.

## Not built

- A small-group rule for the existing `/aggregates` fields (R1).
- A test of the processing counts of released HOM-82 organization trend blocks (R2).
- A reconciliation of the `/aggregates` coverage with `meetings_total` (L2: two populations by
  design, decision 12).
- Coverage of rejected voicemail events.

## Risks

- **R1. The existing `/aggregates` fields show small groups.** `meetings_total`,
  `meetings_by_status`, `audio_seconds_total` and `action_items_total` are shown for any
  window and any group size (`services/organization_service.py:756-800`). A free window lets
  a caller isolate one agent's meeting count and durations. The new metadata adds nothing to
  this, but does not fix it. Follow-up ticket: apply the HOM-82 rule to these fields (a
  breaking change for the console).
- **R2. The HOM-82 organization trend coverage is not tested by agents** (decision 2). Known
  since ADR 0038. The `/aggregates` metadata inherits it with the block (L2). Follow-up.
- **R3. Cost.** Up to 14 more statements per workspace on `/aggregates` (decision 25,
  measured): the HOM-82 executive view's statements, and its Python judgment of every
  conversation of up to 52 weeks. HOM-84 measures it.
- **R4. Two status views on voicemails.** `by_status` reads `VoicemailMessage.status`;
  `coverage.processing` reads the meeting. A client must not expect them to add up.
- **R5. Closed weeks still change** (as ADR 0038): a late transcription changes an old week, so
  two answers at two times can differ by a few meetings of a week that passed the test.
- **R6. Two windows and two populations on one item (L2).** The existing fields use
  `since`/`until` and live meetings by `created_at`; the metadata uses `coverage_window` and
  ledger rows by `received_at` (decision 12). A client must not add or subtract one from the
  other.
- **R7. Differencing across the two routes: closed by L2.** The first design counted the
  `/aggregates` coverage over the meetings of `meetings_total` (by `created_at`), the HOM-82
  block over ledger rows (by `received_at`). The final review reproduced the leak: one call
  received in week k-2 (its agent alone there) and created in week k showed as
  `evaluation.buckets.mock_analysis = 1` in the `/aggregates` counters of week k and 0 in the
  HOM-82 counters. L2 gives both routes one population: the item metadata is the HOM-82
  block's, so the difference is zero by construction
  (`test_a_meeting_received_in_another_week_is_counted_as_hom82_counts_it`,
  `test_every_item_metadata_equals_the_hom82_block`).
