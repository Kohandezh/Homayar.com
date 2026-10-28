"""HOM-84 (WP15-T06, F49 and F61): every trend route reconciles with a hand-counted dataset.

The dataset is ``_trend_reference`` (read its docstring first).  Each expected number below is
a literal, counted by hand from the seeded rows, never from the product code.

Population of workspace A in the window W1..W3 (the "transcribed" conversations):

    alpha-big    35 with an extraction + 1 no_run                  = 36
    beta-mid     31                                                = 31
    gamma-small   6                                                =  6
    no team      the analyst's own upload (W3, C, no agent)        =  1
    total                                                            74

    Not in it: the failed, queued, no_transcript and deleted gaps (processing buckets), the
    rejected ledger row, the W0 call, b2's call 5 weeks before W1, the 4 voicemails (no ledger
    row) and all of workspace B.

Rows (denominator = conversations with an extraction; the no_run call is ``missing``
``analysis_unavailable`` in every row; a V call is ``missing``
``abstained_insufficient_evidence`` in the service_complaint rows):

    cohort       intent_report_dissatisfaction  intent_resolve_billing  service_complaint
                 (C + V) / (C + B + V)          B / (C + B + V)         C / (C + B)
    alpha-big    (20 + 5) / 35 = 0.7143         10 / 35 = 0.2857        20 / 30 = 0.6667
    beta-mid       9 / 31                       22 / 31                  9 / 31
    gamma-small    3 / 6                         3 / 6                   3 / 6
    no team        1 / 1                         0 / 1                   1 / 1
    workspace A  38 / 73 = 0.5205               35 / 73 = 0.4795        33 / 68 = 0.4853

    call_reason (family) holds for every extraction: 73 / 73 = 1.0 (alpha-big 35 / 35).
    complaint_refund = service_complaint (every C is a refund complaint); complaint_invoice is
    0 / 68 (no C is about an invoice; a B is not a complaint).

Weeks of workspace A (owner, no team filter):

    week  alpha-big            beta-mid   gamma-small  no team  included  intent_rd  service
    W1    15 (10 C, 5 B)       11 (3 C)   2 (1 C)      -        28        14 / 28    14 / 28
    W2    10 (5 C, 5 B) + 1    11 (3 C)   2 (1 C)      -        24         9 / 23     9 / 23
    W3    10 (5 C, 5 V)         9 (3 C)   2 (1 C)      1 C      22        15 / 22    10 / 17
    sum                                                         74        38 / 73    33 / 68

    The teams add up to the workspace minus the no-team upload (``issue_trends/filters.py``:
    "A meeting with no team appears only in answers without a team_id filter").

Ledger rows of workspace A in the window (``coverage/1``):

    accepted 78 = 74 transcribed + deleted 1 + in_progress 1 (queued) + failed 1
                  + no_transcript 1; rejected 1, so ingestion 78 / 79.
    evaluation 73 / 74: the no_run call is ``analysis_unavailable``, the rest fully evaluated.
    per week: W1 30 accepted (28 transcribed, deleted 1, failed 1), W2 25 (24, no_transcript 1),
    W3 23 (22, in_progress 1).
    alpha-big: 40 accepted (36 transcribed + the 4 gaps that are not transcribed), evaluation
    35 / 36.  agent b1 (all 5 gaps are b1's): 12 accepted, 8 transcribed, evaluation 7 / 8.

Disclosure (ADR 0038):

    alpha-big    exactly 5 agents (``MIN_COHORT_AGENTS``): kind rate, released.  A "fewer
                 than 6" rule would make it kind agent.
    beta-mid     3 agents: kind agent.  Every row has 31 conversations, so the all-or-nothing
                 rule hides nothing, but releasing it would leave gamma-small (2 agents, cells
                 under 30) as the remainder of the workspace total: decision 6 withholds
                 beta-mid (the team with the fewest conversations) -> every cell ``withheld``,
                 ``complementary_disclosure``, denominators kept.  The no-team upload has no
                 agent, so it changes no remainder.
    gamma-small  2 agents: kind agent, 6 conversations -> every cell ``insufficient_sample``
                 with the numerator hidden; the window layer is masked.
    workspace A  10 agents: kind rate, released.
    organization views: per week alpha-big has 5 agents (passes), beta-mid 3 and gamma-small 2
                 (``small_group``); the rest (beta-mid + gamma-small) has 5 agents, so
                 workspace A and alpha-big are released.
    the agent persona as an organization analyst (A6b): it knows b1's 8 calls through its
                 restricted membership.  Outside them alpha-big has 4 agents (b2..b5) in every
                 week, so it is not in Rel_out_k: alpha-big ``complementary_disclosure``;
                 workspace A outside b1's calls has 9 agents: released.

Workspace B (owner, no team filter; record scope ``team``):

    delta-b 15 (5 C, 10 B) + epsilon-four 32 (24 C, 4 B, 4 V) = 47 included.
    intent_rd (5 + 24 + 4) / 47 = 33 / 47 = 0.7021; call_reason 47 / 47; service_complaint
    C / (C + B) = 29 / 43 = 0.6744.  Weeks: W1 37 (call_reason 37 / 37 computed, intent_rd
    33 / 37 = 0.8919), W2 5 and W3 5 (delta-b B calls: intent_rd 0 / 5).
    The supervisor of epsilon-four sees its 32 calls only: 4 agents, kind agent, so decision 5
    decides: service_complaint has 28 (< 30, hidden) and every other row 32 (>= 30) ->
    ``withheld``, ``complementary_disclosure``, numerator null.
    Organization week test, W1: delta-b 5 agents passes, epsilon-four exactly 4 agents fails
    (``small_group``).  Workspace B minus Rel_k = {delta-b} is epsilon-four, 4 agents: fails, so
    Rel_k is empty and delta-b is ``complementary_disclosure``.  Workspace B has 9 agents in
    every row of W1 and 5 in W2 and W3: released.

Voicemails of A in the window: VM1 and VM2 (alpha-big, completed, no run), VM3 (beta-mid,
failed): total 3, transcribed 2, failed 1, evaluation 0 / 2 (``analysis_unavailable`` 2).

``/aggregates`` (HOM-320) counts the live meetings whose ledger row was received in
``coverage_window``, and shows null counts when the item is withheld:

    no window (default, coverage over W0..W3): workspace A 78 = 35 + 4 live gaps + 31 + 6 +
    the upload + the W0 call (not the outside call, not the deleted gap, not the 4
    voicemails: no ledger row); completed 76, failed 1, queued 1; audio 78 * 30 = 2340
    seconds; coverage transcribed 75 (the W0 call too).  Workspace B 47 meetings, 1410
    seconds.  beta-mid alone (``org_narrow``): withheld (3 agents), so every count is null.
    since = W1 start, until = W3 end: ``coverage_window`` is W1..W3, coverage transcribed 74
    (the HOM-82 block of the same window); workspace A 77 = 78 - the W0 call: completed 75,
    failed 1, queued 1.
"""

from __future__ import annotations

from typing import Any

from backend.tests._trend_reference import WEEK, Reference, build_reference

ISSUES = "/api/v1/trends/issues"
TEAMS = "/api/v1/trends/teams"
ME = "/api/v1/trends/me"
LINKS = "/api/v1/trends/conversations"
VOICEMAILS = "/api/v1/voicemails/analytics"
COVERAGE = "/api/v1/admin/coverage"
CAUSE_NOTICE = "correlation_not_cause"

INDICATORS_A = [
    "evaluation_incomplete",
    "missing_values",
    "processing_incomplete",
    "scorecard_unavailable",
    "source_unavailable",
]
BUCKETS_A = {"deleted": 1, "in_progress": 1, "failed": 1, "transcribed": 74, "no_transcript": 1}
BUCKETS_BIG = {"deleted": 1, "in_progress": 1, "failed": 1, "transcribed": 36, "no_transcript": 1}
BUCKETS_B = {"deleted": 0, "in_progress": 0, "failed": 0, "transcribed": 47, "no_transcript": 0}
RELEASED_RATE = {"status": "released", "reason": None, "kind": "rate", "masked": []}


def _ok(response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    body = response.json()
    _never_a_cause(body)
    return body


def _get(ref: Reference, who: str, path: str, *, workspace: str | None = "a", **params: Any):
    workspace_id = {"a": ref.a.workspace_id, "b": ref.b.workspace_id, None: None}[workspace]
    return ref.people[who].get(path, workspace_id=workspace_id, params=params)


def _never_a_cause(value: Any) -> None:
    """ADR 0040 decision 12: no field, status or word says that something causes an issue.
    The only key or string that holds "cause" is the notice ``correlation_not_cause``."""
    if isinstance(value, dict):
        for key, item in value.items():
            assert "cause" not in key.lower(), key
            _never_a_cause(item)
    elif isinstance(value, list):
        for item in value:
            _never_a_cause(item)
    elif isinstance(value, str):
        lowered = value.lower()
        assert "because" not in lowered and "root cause" not in lowered, value
        assert "cause" not in lowered or value == CAUSE_NOTICE, value


def _row(block: dict[str, Any], family: str, issue: str | None = None) -> dict[str, Any]:
    return next(r for r in block["rows"] if r["family"] == family and r["issue"] == issue)


def _cell(block: dict[str, Any], family: str, issue: str | None = None) -> tuple:
    """``(status, numerator, denominator, ratio, reason)`` of one row."""
    cell = _row(block, family, issue)["cell"]
    return cell["status"], cell["numerator"], cell["denominator"], cell["ratio"], cell["reason"]


def _counters(block: dict[str, Any]) -> tuple:
    """``(processing buckets, evaluation numerator, evaluation denominator, basis)``."""
    coverage = block["coverage"]
    return (
        coverage["processing"]["buckets"],
        coverage["evaluation"]["numerator"],
        coverage["evaluation"]["denominator"],
        coverage["basis"],
    )


def _links(ref: Reference, who: str, view: str, row: str, *, workspace: str = "a", **params: Any):
    return _get(
        ref,
        who,
        LINKS,
        workspace=workspace,
        trend_view=view,
        row=row,
        limit="100",
        **ref.window(),
        **params,
    )


def _listed(response) -> set[str]:
    body = _ok(response)
    ids = [item["meeting_id"] for item in body["conversations"]]
    assert len(ids) == len(set(ids)) == body["page"]["total"]
    assert all(item["state"] == "counted" for item in body["conversations"])
    return set(ids)


INSUFFICIENT = "insufficient_sample"
BELOW = "below_minimum_sample"
INTENT = ("call_reason", "intent_report_dissatisfaction")
BILLING = ("call_reason", "intent_resolve_billing")
SERVICE = ("service_complaint",)
REFUND = ("service_complaint", "complaint_refund")
REASON = ("call_reason",)


# --- workspace views -------------------------------------------------------------------------


def test_workspace_views_reconcile_with_the_reference_dataset(client, email_outbox):
    ref = build_reference(client, email_outbox)
    window = ref.window()

    # GET /trends/issues, owner, the whole window: every count of the table
    whole = _ok(_get(ref, "owner", ISSUES, **window))
    assert whole["population"] == {"included": 74, "excluded": {"duplicate_media": 0}}
    assert whole["disclosure"] == RELEASED_RATE
    assert _cell(whole, *INTENT) == ("computed", 38, 73, 0.5205, None)
    assert _cell(whole, *BILLING) == ("computed", 35, 73, 0.4795, None)
    assert _cell(whole, *REASON) == ("computed", 73, 73, 1.0, None)
    assert _cell(whole, *SERVICE) == ("computed", 33, 68, 0.4853, None)
    assert _cell(whole, *REFUND) == ("computed", 33, 68, 0.4853, None)
    assert _cell(whole, "qa") == (
        "not_computable",
        None,
        74,
        None,
        "scorecard_results_unavailable",
    )
    missing = {k: v for k, v in _row(whole, *SERVICE)["missing"].items() if v}
    assert missing == {"analysis_unavailable": 1, "abstained_insufficient_evidence": 5}
    assert {k: v for k, v in _row(whole, *INTENT)["missing"].items() if v} == {
        "analysis_unavailable": 1
    }
    assert _counters(whole) == (BUCKETS_A, 73, 74, "workspace")
    assert whole["coverage"]["processing"]["numerator"] == 74
    assert whole["coverage"]["processing"]["denominator"] == 78
    assert whole["missing_data"] == {"indicators": INDICATORS_A}
    assert whole["notices"] == [CAUSE_NOTICE]

    # the time filter: one week at a time, and the weeks add up to the window
    weeks = [_ok(_get(ref, "owner", ISSUES, **ref.window([week]))) for week in (0, 1, 2)]
    assert [w["population"]["included"] for w in weeks] == [28, 24, 22]
    assert [_cell(w, *INTENT) for w in weeks] == [
        (INSUFFICIENT, 14, 28, None, BELOW),
        (INSUFFICIENT, 9, 23, None, BELOW),
        (INSUFFICIENT, 15, 22, None, BELOW),
    ]
    assert [_cell(w, *SERVICE)[1:3] for w in weeks] == [(14, 28), (9, 23), (10, 17)]
    assert [w["coverage"]["processing"]["buckets"] for w in weeks] == [
        {"deleted": 1, "in_progress": 0, "failed": 1, "transcribed": 28, "no_transcript": 0},
        {"deleted": 0, "in_progress": 0, "failed": 0, "transcribed": 24, "no_transcript": 1},
        {"deleted": 0, "in_progress": 1, "failed": 0, "transcribed": 22, "no_transcript": 0},
    ]
    # W1: 28 / 30 transcribed, 28 / 28 evaluated, no missing value.  W2: 24 / 25 and 23 / 24
    # (the no_run call).  W3: 22 / 23 (the queued call), the V calls are missing values.
    assert weeks[0]["missing_data"]["indicators"] == [
        "insufficient_sample",
        "processing_incomplete",
        "scorecard_unavailable",
        "source_unavailable",
    ]
    assert weeks[1]["missing_data"]["indicators"] == [
        "evaluation_incomplete",
        "insufficient_sample",
        "missing_values",
        "processing_incomplete",
        "scorecard_unavailable",
        "source_unavailable",
    ]
    assert weeks[2]["missing_data"]["indicators"] == [
        "insufficient_sample",
        "missing_values",
        "processing_incomplete",
        "scorecard_unavailable",
        "source_unavailable",
    ]
    assert sum(w["population"]["included"] for w in weeks) == 74
    assert sum(_cell(w, *INTENT)[1] for w in weeks) == 38
    assert sum(_cell(w, *INTENT)[2] for w in weeks) == 73

    # grain=week gives the same three points inside one answer
    series = _ok(_get(ref, "owner", ISSUES, grain="week", **window))
    assert [p["partial"] for p in series["points"]] == [False, False, False]
    assert [p["population"]["included"] for p in series["points"]] == [28, 24, 22]
    assert [_cell(p, *INTENT) for p in series["points"]] == [_cell(w, *INTENT) for w in weeks]
    assert series["rows"] == whole["rows"]

    # the team filter: each team, and the teams add up to the workspace
    big = _ok(_get(ref, "owner", ISSUES, team_id=ref.teams["big"], **window))
    mid = _ok(_get(ref, "owner", ISSUES, team_id=ref.teams["mid"], **window))
    small = _ok(_get(ref, "owner", ISSUES, team_id=ref.teams["small"], **window))
    assert [t["population"]["included"] for t in (big, mid, small)] == [36, 31, 6]
    assert _cell(big, *INTENT) == ("computed", 25, 35, 0.7143, None)
    assert _cell(big, *BILLING) == ("computed", 10, 35, 0.2857, None)
    assert _cell(big, *SERVICE) == ("computed", 20, 30, 0.6667, None)  # 30: at the minimum
    assert big["disclosure"] == RELEASED_RATE  # exactly 5 agents: kind rate
    assert _counters(big) == (BUCKETS_BIG, 35, 36, "trend_population")
    assert _cell(mid, *INTENT) == ("withheld", None, 31, None, "complementary_disclosure")
    assert _cell(mid, *SERVICE) == ("withheld", None, 31, None, "complementary_disclosure")
    assert mid["disclosure"] == {
        "status": "masked",
        "reason": "complementary_disclosure",
        "kind": "agent",
        "masked": ["window"],
    }
    assert _cell(small, *INTENT) == (INSUFFICIENT, None, 6, None, BELOW)
    assert small["disclosure"]["kind"] == "agent" and small["disclosure"]["masked"] == ["window"]
    # the teams add up to the workspace minus the one no-team upload
    assert sum(t["population"]["included"] for t in (big, mid, small)) + 1 == 74
    assert sum(_cell(t, *INTENT)[2] for t in (big, mid, small)) + 1 == 73
    assert sum(_cell(t, *SERVICE)[2] for t in (big, mid, small)) + 1 == 68

    # the product (issue category) filter: a selector picks rows, never changes a number
    billing = _ok(_get(ref, "owner", ISSUES, issue="intent_resolve_billing", **window))
    assert [(r["family"], r["issue"]) for r in billing["rows"]] == [BILLING]
    assert _cell(billing, *BILLING) == _cell(whole, *BILLING)
    refund = _ok(_get(ref, "owner", ISSUES, service_area="refund", **window))
    assert [(r["family"], r["issue"]) for r in refund["rows"]] == [REFUND]
    assert _cell(refund, *REFUND) == ("computed", 33, 68, 0.4853, None)
    invoice = _ok(_get(ref, "owner", ISSUES, service_area="invoice", **window))
    assert [r["issue"] for r in invoice["rows"]] == ["complaint_invoice"]
    assert _cell(invoice, "service_complaint", "complaint_invoice") == (
        "computed",
        0,
        68,
        0.0,
        None,
    )
    # intent is single-valued: its two issues add up to the family
    assert _cell(whole, *INTENT)[1] + _cell(whole, *BILLING)[1] == _cell(whole, *REASON)[1]

    # GET /trends/teams, owner: one block per team, compared without a ranking
    teams = _ok(_get(ref, "owner", TEAMS, **window))
    assert [t["team"]["name"] for t in teams["teams"]] == ["alpha-big", "beta-mid", "gamma-small"]
    blocks = {t["team"]["name"]: t for t in teams["teams"]}
    for name, answer in (("alpha-big", big), ("beta-mid", mid), ("gamma-small", small)):
        assert blocks[name]["rows"] == answer["rows"], name
        assert blocks[name]["population"] == answer["population"], name
        assert blocks[name]["disclosure"] == answer["disclosure"], name
    assert blocks["alpha-big"]["disclosure"] == RELEASED_RATE
    assert blocks["alpha-big"]["mix"] == {
        "language": {"fa": 36},
        "source_type": {"file_upload": 36},
    }
    assert blocks["alpha-big"]["missing_data"] == {"indicators": INDICATORS_A}
    assert _counters(blocks["alpha-big"]) == (BUCKETS_BIG, 35, 36, "trend_population")
    assert blocks["beta-mid"]["missing_data"] == {
        "indicators": ["scorecard_unavailable", "source_unavailable"]
    }
    assert blocks["gamma-small"]["missing_data"] == {
        "indicators": ["insufficient_sample", "scorecard_unavailable", "source_unavailable"]
    }
    rows = len(blocks["alpha-big"]["rows"])
    assert len(teams["comparisons"]) == 3 * rows  # 3 pairs, one comparison per row
    assert {(c["status"], c["reason"]) for c in teams["comparisons"]} == {
        ("withheld", "small_group")
    }  # beta-mid and gamma-small are small groups: never compared
    assert all("rank" not in c for c in teams["comparisons"])
    assert teams["notices"] == [CAUSE_NOTICE, "unadjusted_comparison"]

    # scope: the supervisor of alpha-big counts alpha-big only
    supervised = _ok(_get(ref, "supervisor", ISSUES, **window))
    assert supervised["population"]["included"] == 36
    assert supervised["rows"] == big["rows"]
    assert supervised["scope"] == {"restricted": True}
    assert supervised["disclosure"] == RELEASED_RATE
    assert _counters(supervised) == (BUCKETS_BIG, 35, 36, "trend_population")

    # GET /trends/me, the agent b1: its own calls only, an agent cell under 30 hides counts
    me = _ok(_get(ref, "agent", ME, **window))
    assert me["subject"] == {"agent_user_id": ref.agents["big"][0]}
    assert me["population"]["included"] == 8
    assert _cell(me, *REASON) == (INSUFFICIENT, None, 7, None, BELOW)
    assert _cell(me, *SERVICE) == (INSUFFICIENT, None, 6, None, BELOW)
    assert _row(me, *INTENT)["pending_review"] is None
    assert me["disclosure"] == {"status": "released", "reason": None, "kind": "agent", "masked": []}
    assert _counters(me) == (
        {"deleted": 1, "in_progress": 1, "failed": 1, "transcribed": 8, "no_transcript": 1},
        7,
        8,
        "trend_population",
    )
    assert me["missing_data"] == {
        "indicators": [
            "evaluation_incomplete",
            "insufficient_sample",
            "missing_values",
            "processing_incomplete",
            "scorecard_unavailable",
            "source_unavailable",
        ]
    }
    assert me["notices"] == [CAUSE_NOTICE]

    # GET /trends/conversations: the list of a computed cell is its numerator, seed by seed
    in_window = {0, 1, 2}
    a = ref.a.workspace_id
    complaints = ref.meetings(workspace_id=a, week=in_window, extraction={"C", "V"})
    assert len(complaints) == 38
    listed = _listed(_links(ref, "owner", "issues", "issue:intent_report_dissatisfaction"))
    assert listed == complaints
    refunds = ref.meetings(workspace_id=a, week=in_window, extraction={"C"})
    assert _listed(_links(ref, "owner", "issues", "issue:complaint_refund")) == refunds
    big_complaints = ref.meetings(team="big", week=in_window, extraction={"C", "V"})
    assert len(big_complaints) == 25
    team_list = _links(
        ref, "owner", "teams", "issue:intent_report_dissatisfaction", team_id=ref.teams["big"]
    )
    assert _listed(team_list) == big_complaints
    assert team_list.json()["notices"] == [CAUSE_NOTICE]
    assert (
        _listed(_links(ref, "supervisor", "issues", "issue:intent_report_dissatisfaction"))
        == big_complaints
    )
    # the analyst (a viewer with no assignment) counts its own upload, and only that
    own = _ok(_get(ref, "analyst", ISSUES, **window))
    assert own["population"]["included"] == 1
    assert _cell(own, *INTENT) == (INSUFFICIENT, 1, 1, None, BELOW)
    assert _ok(_get(ref, "analyst", ME, **window))["population"]["included"] == 0  # an upload
    # is not a call as agent (ADR 0038 decision 1)

    # workspace B: a computed weekly cell, and its point link
    b_week = _ok(_get(ref, "owner", ISSUES, workspace="b", grain="week", **window))
    assert b_week["population"]["included"] == 47
    assert b_week["disclosure"] == RELEASED_RATE
    assert _cell(b_week, *INTENT) == ("computed", 33, 47, 0.7021, None)
    assert _cell(b_week, *REASON) == ("computed", 47, 47, 1.0, None)
    assert _cell(b_week, *SERVICE) == ("computed", 29, 43, 0.6744, None)
    assert [p["population"]["included"] for p in b_week["points"]] == [37, 5, 5]
    assert _cell(b_week["points"][0], *REASON) == ("computed", 37, 37, 1.0, None)
    assert _cell(b_week["points"][0], *INTENT) == ("computed", 33, 37, 0.8919, None)
    assert _cell(b_week["points"][1], *INTENT) == (INSUFFICIENT, 0, 5, None, BELOW)
    point = _links(ref, "owner", "issues", "family:call_reason", workspace="b",
                   grain="week", point="0")  # fmt: skip
    b_w1 = ref.meetings(workspace_id=ref.b.workspace_id, week=0, extraction={"C", "B", "V"})
    assert len(b_w1) == 37
    assert _listed(point) == b_w1
    assert point.json()["page"]["total"] == len(point.json()["conversations"]) == 37
    assert point.json()["point"]["index"] == 0
    window_list = _listed(_links(ref, "owner", "issues", "family:call_reason", workspace="b"))
    assert len(window_list) == 47 and b_w1 < window_list  # the point is smaller than the window

    # decision 5 inside one agent cohort: the supervisor of epsilon-four (4 agents) in B
    alone = _ok(_get(ref, "supervisor", ISSUES, workspace="b", **window))
    assert alone["population"]["included"] == 32
    assert _cell(alone, *SERVICE) == (INSUFFICIENT, None, 28, None, BELOW)  # under 30: hidden
    withheld = ("withheld", None, 32, None, "complementary_disclosure")
    assert _cell(alone, *REASON) == withheld  # 32 >= 30, withheld by the hidden row
    assert _cell(alone, *INTENT) == withheld
    assert alone["disclosure"] == {
        "status": "masked",
        "reason": "complementary_disclosure",
        "kind": "agent",
        "masked": ["window"],
    }

    # no link for a withheld, insufficient or hidden cell
    for who, view, extra in (
        ("supervisor", "issues", {"workspace": "b"}),  # decision 5: withheld at 32
        ("owner", "teams", {"team_id": ref.teams["mid"]}),  # withheld
        ("owner", "teams", {"team_id": ref.teams["small"]}),  # agent cell under 30
        ("owner", "issues", {"grain": "week", "point": "0"}),  # 14 / 28, insufficient
        ("agent", "me", {}),  # agent cell under 30, for the agent too
    ):
        refused = _links(ref, who, view, "issue:intent_report_dissatisfaction", **extra)
        assert refused.status_code == 403, (view, extra, refused.text)
        assert refused.json()["error"]["code"] == "trend_links_withheld"


# --- organization views ----------------------------------------------------------------------


def test_organization_views_reconcile_with_the_reference_dataset(client, email_outbox):
    ref = build_reference(client, email_outbox)
    window = ref.window()
    org = ref.organization_id
    executive = f"/api/v1/organizations/{org}/trends/issues"
    drill = f"/api/v1/organizations/{org}/workspaces/{ref.a.workspace_id}/trends/issues"
    aggregates = f"/api/v1/organizations/{org}/aggregates"

    owner = _ok(_get(ref, "owner", executive, workspace=None, **window))
    assert owner["notices"] == [CAUSE_NOTICE]
    assert "total" not in owner  # no organization total
    items = {w["workspace_id"]: w for w in owner["workspaces"]}
    assert list(items) == [ref.a.workspace_id, ref.b.workspace_id]
    a, b = items[ref.a.workspace_id], items[ref.b.workspace_id]
    assert a["disclosure"]["status"] == b["disclosure"]["status"] == "released"
    assert a["population"]["included"] == 74
    assert _cell(a, *INTENT) == ("computed", 38, 73, 0.5205, None)
    assert _cell(a, *SERVICE) == ("computed", 33, 68, 0.4853, None)
    assert _counters(a) == (BUCKETS_A, 73, 74, "workspace")
    assert a["missing_data"] == {"indicators": INDICATORS_A}
    assert b["population"]["included"] == 47
    assert _cell(b, *INTENT) == ("computed", 33, 47, 0.7021, None)
    assert _cell(b, *SERVICE) == ("computed", 29, 43, 0.6744, None)
    assert _counters(b) == (BUCKETS_B, 47, 47, "workspace")
    assert b["missing_data"] == {
        "indicators": ["missing_values", "scorecard_unavailable", "source_unavailable"]
    }

    weekly = _ok(_get(ref, "owner", executive, workspace=None, grain="week", **window))
    points = weekly["workspaces"][0]["points"]
    assert [p["population"]["included"] for p in points] == [28, 24, 22]
    assert [_cell(p, *INTENT)[1:3] for p in points] == [(14, 28), (9, 23), (15, 22)]
    b_points = weekly["workspaces"][1]["points"]
    assert [p["population"]["included"] for p in b_points] == [37, 5, 5]
    assert _cell(b_points[1], *INTENT) == (INSUFFICIENT, 0, 5, None, BELOW)  # rate: shown

    # an analyst with a grant on A sees A only, with the same numbers
    analyst = _ok(_get(ref, "org_analyst", executive, workspace=None, **window))
    assert [w["workspace_id"] for w in analyst["workspaces"]] == [ref.a.workspace_id]
    assert analyst["workspaces"][0]["rows"] == a["rows"]

    # a team grant on beta-mid (3 agents): withheld, no count at all
    narrow = _ok(_get(ref, "org_narrow", executive, workspace=None, **window))
    (block,) = narrow["workspaces"]
    assert block["team_ids"] == [ref.teams["mid"]]
    assert block["disclosure"] == {
        "status": "withheld",
        "reason": "small_group",
        "kind": None,
        "masked": [],
    }
    assert (block["population"], block["rows"], block["coverage"], block["missing_data"]) == (
        None,
        [],
        None,
        None,
    )

    # the drill-down: the workspace block, then one block per active team
    down = _ok(_get(ref, "org_analyst", drill, workspace=None, **window))
    assert down["notices"] == [CAUSE_NOTICE, "unadjusted_comparison"]
    assert down["workspace"]["rows"] == a["rows"]
    assert down["workspace"]["coverage"] == a["coverage"]
    teams = {t["team"]["name"]: t for t in down["teams"]}
    assert list(teams) == ["alpha-big", "beta-mid", "gamma-small"]
    assert teams["alpha-big"]["population"]["included"] == 36
    assert _cell(teams["alpha-big"], *INTENT) == ("computed", 25, 35, 0.7143, None)
    assert _counters(teams["alpha-big"]) == (BUCKETS_BIG, 35, 36, "trend_population")
    assert teams["alpha-big"]["disclosure"] == RELEASED_RATE  # exactly 5 agents passes
    for name in ("beta-mid", "gamma-small"):
        assert teams[name]["disclosure"]["status"] == "withheld", name
        assert teams[name]["disclosure"]["reason"] == "small_group", name
        assert teams[name]["coverage"] is None and teams[name]["rows"] == [], name

    # the week test at its boundary, and the remainder rule (decision 7), in workspace B
    drill_b = f"/api/v1/organizations/{org}/workspaces/{ref.b.workspace_id}/trends/issues"
    down_b = _ok(_get(ref, "owner", drill_b, workspace=None, **window))
    assert down_b["workspace"]["disclosure"] == RELEASED_RATE
    assert down_b["workspace"]["rows"] == b["rows"]
    teams_b = {t["team"]["name"]: t for t in down_b["teams"]}
    assert list(teams_b) == ["delta-b", "epsilon-four"]
    withheld_org = {"status": "withheld", "kind": None, "masked": []}
    assert teams_b["epsilon-four"]["disclosure"] == {**withheld_org, "reason": "small_group"}
    assert teams_b["delta-b"]["disclosure"] == {
        **withheld_org,
        "reason": "complementary_disclosure",
    }  # delta-b passes alone, but B minus delta-b in W1 is 4 agents
    for block in teams_b.values():
        assert block["population"] is None and block["coverage"] is None

    # A6b: the agent b1 as an organization analyst knows its own 8 calls of alpha-big
    known = _ok(_get(ref, "agent", drill, workspace=None, **window))
    assert known["workspace"]["disclosure"] == RELEASED_RATE
    assert known["workspace"]["rows"] == a["rows"]
    known_teams = {t["team"]["name"]: t["disclosure"] for t in known["teams"]}
    assert known_teams["alpha-big"] == {**withheld_org, "reason": "complementary_disclosure"}
    assert (
        known_teams["beta-mid"]
        == known_teams["gamma-small"]
        == {
            **withheld_org,
            "reason": "small_group",
        }
    )
    # drill-down consistency: org view, drill-down, workspace view and links agree per cell
    workspace = _ok(_get(ref, "owner", ISSUES, **window))
    big_view = _ok(_get(ref, "owner", ISSUES, team_id=ref.teams["big"], **window))
    assert workspace["rows"] == a["rows"] == down["workspace"]["rows"]
    assert big_view["rows"] == teams["alpha-big"]["rows"]
    whole_list = _ok(_links(ref, "owner", "issues", "issue:intent_report_dissatisfaction"))
    assert whole_list["page"]["total"] == _cell(a, *INTENT)[1] == 38
    team_list = _ok(
        _links(
            ref,
            "owner",
            "teams",
            "issue:intent_report_dissatisfaction",
            team_id=ref.teams["big"],
        )
    )
    assert team_list["page"]["total"] == _cell(teams["alpha-big"], *INTENT)[1] == 25
    counted = {c["meeting_id"] for c in team_list["conversations"]}
    assert counted <= {c["meeting_id"] for c in whole_list["conversations"]}
    assert not counted & ref.meetings(team="mid") and not counted & ref.meetings(team="delta")

    # /aggregates, no window: the existing counts and the HOM-82 block's metadata over the
    # default window (the 4 closed weeks before this week, W0..W3)  # HOM-320
    summary = _ok(_get(ref, "owner", aggregates, workspace=None))
    assert summary["coverage_window"] == {
        "from": (ref.start - WEEK).isoformat(),
        "to": ref.week_start.isoformat(),
        "basis": "ingestion_received_at",
    }
    rows = {item["workspace_id"]: item for item in summary["items"]}
    item_a, item_b = rows[ref.a.workspace_id], rows[ref.b.workspace_id]
    assert (item_a["meetings_total"], item_a["audio_seconds_total"]) == (78, 2340)  # HOM-320
    assert item_a["meetings_by_status"] == {"completed": 76, "failed": 1, "queued": 1}  # HOM-320
    assert item_a["action_items_total"] == 0
    assert (item_b["meetings_total"], item_b["audio_seconds_total"]) == (47, 1410)
    assert item_a["disclosure"] == a["disclosure"]
    assert item_a["coverage"]["processing"]["buckets"] == {**BUCKETS_A, "transcribed": 75}
    assert item_a["missing_data"] == {"indicators": INDICATORS_A}
    assert item_b["missing_data"] == b["missing_data"]

    # /aggregates with since and until on the closed weeks W1..W3: the metadata window is
    # that window (not the clock), so the W0 call leaves the counters and the existing
    # counts  # HOM-320
    dated = _ok(
        _get(ref, "owner", aggregates, workspace=None, since=window["from"], until=window["to"])
    )
    assert dated["coverage_window"] == {
        "from": ref.start.isoformat(),
        "to": (ref.start + 3 * WEEK).isoformat(),
        "basis": "ingestion_received_at",
    }
    dated_rows = {item["workspace_id"]: item for item in dated["items"]}
    dated_a = dated_rows[ref.a.workspace_id]
    assert dated_a["coverage"]["processing"]["buckets"] == BUCKETS_A  # 74, not 75
    assert dated_a["coverage"]["processing"] == a["coverage"]["processing"]
    assert dated_a["coverage"]["evaluation"] == a["coverage"]["evaluation"]
    assert dated_rows[ref.b.workspace_id]["coverage"]["processing"]["buckets"] == BUCKETS_B
    assert (dated_a["meetings_total"], dated_a["meetings_by_status"]) == (  # HOM-320
        77,  # HOM-320
        {"completed": 75, "failed": 1, "queued": 1},  # HOM-320
    )  # HOM-320
    narrowed = _ok(_get(ref, "org_narrow", aggregates, workspace=None))
    (item,) = narrowed["items"]
    assert (item["meetings_total"], item["audio_seconds_total"]) == (None, None)  # HOM-320, R1
    assert item["meetings_by_status"] is None  # HOM-320
    assert item["disclosure"]["status"] == "withheld"
    assert item["coverage"] is None and item["missing_data"] is None


# --- voicemail analytics and admin coverage ----------------------------------------------------


def test_voicemail_and_admin_coverage_reconcile_with_the_reference_dataset(client, email_outbox):
    ref = build_reference(client, email_outbox)
    window = ref.window()
    received = {"received_from": window["from"], "received_to": window["to"]}

    owner = _ok(_get(ref, "owner", VOICEMAILS, **received))
    assert owner["total"] == 3
    assert owner["coverage"]["processing"]["buckets"] == {
        "deleted": 0,
        "in_progress": 0,
        "failed": 1,
        "transcribed": 2,
        "no_transcript": 0,
    }
    assert owner["coverage"]["processing"]["denominator"] == 3
    evaluation = owner["coverage"]["evaluation"]
    assert (evaluation["numerator"], evaluation["denominator"]) == (0, 2)
    assert evaluation["buckets"]["analysis_unavailable"] == 2
    assert owner["missing_data"] == {
        "indicators": ["evaluation_incomplete", "processing_incomplete", "scorecard_unavailable"]
    }
    supervisor = _ok(_get(ref, "supervisor", VOICEMAILS, **received))
    assert supervisor["total"] == 2  # VM1 and VM2 of alpha-big; VM3 of beta-mid is out
    assert supervisor["coverage"]["processing"]["buckets"]["transcribed"] == 2
    assert supervisor["missing_data"] == {
        "indicators": ["evaluation_incomplete", "scorecard_unavailable"]
    }
    for who in ("analyst", "agent"):  # no voicemail is in their record scope
        empty = _ok(_get(ref, who, VOICEMAILS, **received))
        assert empty["total"] == 0, who
        assert empty["missing_data"] == {
            "indicators": ["empty_population", "scorecard_unavailable"]
        }, who

    coverage = _ok(_get(ref, "owner", COVERAGE, **window))
    counters = coverage["counters"]
    assert counters["ingestion"]["buckets"] == {"rejected": 1, "accepted": 78}
    assert (counters["ingestion"]["numerator"], counters["ingestion"]["denominator"]) == (78, 79)
    assert counters["processing"]["buckets"] == BUCKETS_A
    assert (counters["evaluation"]["numerator"], counters["evaluation"]["denominator"]) == (73, 74)
    assert counters["review"]["status"] == "not_computable"
    assert counters["review"]["reason"] == "review_records_unavailable"
    assert coverage["missing_data"] == {
        "indicators": [
            "evaluation_incomplete",
            "ingestion_incomplete",
            "processing_incomplete",
            "review_unavailable",
            "scorecard_unavailable",
        ]
    }
    # the trend coverage of the same window is the admin counters, field by field
    trend = _ok(_get(ref, "owner", ISSUES, **window))
    assert trend["coverage"]["processing"] == counters["processing"]
    assert trend["coverage"]["evaluation"] == counters["evaluation"]
