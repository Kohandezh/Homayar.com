"""HOM-83 (WP15-T05): ``GET /api/v1/trends/conversations``, the conversations behind a cell.

The route drills into one cell of ``/trends/issues``, ``/trends/teams`` or ``/trends/me``
(``trend_view``).  It decides the cell with the view's own code and lists the conversations
of its numerator only when the cell is ``computed``; every other cell is the same
``403 trend_links_withheld``.  A link holds ids only.

Each test builds one dataset and checks a table of cases; every refused case has its closest
allowed case and checks ``error.code`` (HOM-292: few app setups).  The pure rules are in
``test_trend_links_rules.py``.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from freezegun import freeze_time
from sqlalchemy import event, func, select, update

from backend.database.relational import get_engine
from backend.database.saas_models import (
    AnalysisRun,
    ApiKey,
    AuditEvent,
    EvidenceSpan,
    Meeting,
    OrganizationMembership,
    OrganizationRole,
    RecordScope,
    TranscriptRevision,
    TranscriptSegment,
    Workspace,
)
from backend.services.audit_event_types import AuditEventType
from backend.services.rate_limit_service import RateLimiter
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _cookies, _Persona, _workspace_with_roles
from backend.tests.test_issue_trend_routes import _place
from backend.tests.test_issue_trend_service import (
    BILLING,
    COMPLAINT,
    DAY,
    add_team,
    add_user,
    seed,
)
from backend.tests.test_issue_trends_counting import ok, stored
from backend.tests.test_organizations import _persona
from backend.tests.test_role_trend_routes import (
    _assign,
    _deactivate,
    _expire_membership,
    _install_member,
    _org_workspace,
)
from backend.tests.test_share_policies import _share, _token
from backend.tests.test_trend_links_rules import cause_keys

LINKS = "/api/v1/trends/conversations"
ISSUES = "/api/v1/trends/issues"
TEAMS = "/api/v1/trends/teams"
ME = "/api/v1/trends/me"

# Thursday 1 October 2026, 00:00 in Asia/Tehran: grain=day cuts the window into 2 whole days.
FROM = datetime(2026, 9, 30, 20, 30, tzinfo=UTC)
TO = FROM + 2 * DAY
WINDOW = {"from": FROM.isoformat(), "to": TO.isoformat()}
# A complaint whose topic could not be decided: counted as a complaint call, but missing for
# every complaint issue, so those rows rest on fewer conversations.
VAGUE = stored(intent=ok("report_dissatisfaction"), topic="insufficient_evidence")
KINDS = {"complaint": COMPLAINT, "billing": BILLING, "vague": VAGUE}
WITHHELD = {
    "code": "trend_links_withheld",
    "message": "This cell gives no conversation links",
    "details": {"key": None},
}
# The rows whose numerator holds a conversation of each kind; every other row counts none.
HOLDS: dict[tuple[str, str, str | None], frozenset[str]] = {
    ("family", "call_reason", None): frozenset({"complaint", "billing", "vague"}),
    ("issue", "call_reason", "intent_report_dissatisfaction"): frozenset({"complaint", "vague"}),
    ("issue", "call_reason", "intent_resolve_billing"): frozenset({"billing"}),
    ("family", "unresolved_outcome", None): frozenset({"complaint"}),
    ("issue", "unresolved_outcome", "outcome_escalated"): frozenset({"complaint"}),
    ("family", "service_complaint", None): frozenset({"complaint"}),
    ("issue", "service_complaint", "complaint_refund"): frozenset({"complaint"}),
}


def _answer(response) -> tuple[int, str | None, str | None]:
    """``(status, error.code, error.details.key)``; the code and key are ``None`` on 200."""
    if response.status_code == 200:
        return 200, None, None
    error = response.json()["error"]
    return response.status_code, error["code"], (error.get("details") or {}).get("key")


def _refusal(response) -> dict[str, Any]:
    """The error body without its ``request_id``: the same for every non-linkable cell."""
    error = dict(response.json()["error"])
    error.pop("request_id", None)
    return {"status": response.status_code, **error, "keys": sorted(response.json())}


WITHHELD_BODY = {"status": 403, **WITHHELD, "keys": ["error"]}


def _row_param(row: dict[str, Any]) -> str:
    return f"{row['level']}:{row['issue'] or row['family']}"


def _row_key(row: dict[str, Any]) -> tuple[str, str, str | None]:
    return row["level"], row["family"], row["issue"]


def _statements(send) -> tuple[int, Any]:
    """Query statements of one request, whatever its status.  The ``set_config`` calls of
    the row-level-security binding (PostgreSQL only) are left out, like HOM-82."""
    seen: list[str] = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "set_config" not in statement:
            seen.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", record)
    try:
        response = send()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return len(seen), response


def _at(day: int, index: int) -> datetime:
    """A call of ``day`` (0 or 1) of the window, inside that Tehran day."""
    return FROM + day * DAY + timedelta(hours=8, minutes=index % 600)


def _spans(meeting_id: str, *, current: bool, indexes: list[int], finding_type="extraction"):
    """Evidence spans of the meeting's current (or older) run on one new segment."""
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        revision = db.scalar(
            select(TranscriptRevision).where(
                TranscriptRevision.meeting_id == meeting_id,
                TranscriptRevision.is_current.is_(True),
            )
        )
        run = db.scalar(
            select(AnalysisRun).where(
                AnalysisRun.meeting_id == meeting_id, AnalysisRun.is_current.is_(current)
            )
        )
        segment = TranscriptSegment(
            workspace_id=meeting.workspace_id,
            meeting_id=meeting_id,
            revision_id=revision.id,
            sequence=len(indexes),
            speaker_key="s1",
            start_ms=0,
            end_ms=1000,
            text="turn zero",
        )
        db.add(segment)
        db.flush()
        ids = []
        for index in indexes:
            span = EvidenceSpan(
                workspace_id=meeting.workspace_id,
                meeting_id=meeting_id,
                analysis_run_id=run.id,
                transcript_revision_id=revision.id,
                segment_id=segment.id,
                speaker_key="s1",
                finding_type=finding_type,
                finding_index=index,
                text_start=0,
                text_end=9,
                start_ms=0,
                end_ms=1000,
                evidence_type="turn_reference",
            )
            db.add(span)
            db.flush()
            ids.append(span.id)
        db.commit()
        return ids


def _record_scope(place, scope: RecordScope) -> None:
    with inspection_session_factory()() as db:
        db.execute(
            update(Workspace).where(Workspace.id == place.workspace_id).values(record_scope=scope)
        )
        db.commit()


def _audits(event_type: AuditEventType) -> int:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == event_type.value)
        )


@dataclass(frozen=True)
class Call:
    meeting_id: str
    team: str | None
    agent: str | None
    kind: str  # a key of KINDS, or "none" for a call that is never counted
    day: int
    uploader: str | None = None


def _calls(place, calls: list[Call], *, team, agents, each, kinds, uploader=None, start=0):
    for number in range(len(agents) * each):
        index = start + number
        agent = agents[number % len(agents)] if agents else None
        kind = kinds[(index // 2) % len(kinds)]
        day = index % 2
        made = seed(
            place,
            received_at=_at(day, index),
            team_id=team,
            agent_user_id=agent,
            extraction=KINDS[kind],
            created_by=uploader,
        )
        calls.append(Call(made.meeting_id, team, agent, kind, day, uploader))


def _expected(calls, row_key, member, point: int | None) -> list[str]:
    kinds = HOLDS.get(row_key, frozenset())
    return sorted(
        c.meeting_id
        for c in calls
        if c.kind in kinds and member(c) and (point is None or c.day == point)
    )


def _pages(persona, params: list[tuple[str, str]], total: int) -> list[dict[str, Any]]:
    """Every page of a released cell at the largest page size."""
    answers = []
    for page in range(max(1, math.ceil(total / 100))):
        response = persona.get(
            LINKS, params=[*params, ("offset", str(page * 100)), ("limit", "100")]
        )
        assert response.status_code == 200, response.text
        answers.append(response.json())
    return answers


# --- 1. which cells link, and to which conversations ------------------------------------------


def test_links_follow_every_cell_of_each_view(client, email_outbox, monkeypatch):
    owner, people, _ = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"sup": "member", "me": "member"}
    )
    sup, me = people["sup"], people["me"]
    place = _place(owner)
    _record_scope(place, RecordScope.TEAM)
    big, small, other = (add_team(place, name) for name in ("big", "small", "other"))
    _assign(place, big, sup.user_id, "supervisor", by=owner.user_id)
    _assign(place, small, sup.user_id, "supervisor", by=owner.user_id)
    big_agents = [add_user(f"big{n}") for n in range(6)]
    small_agents = [add_user(f"small{n}") for n in range(3)]
    other_agents = [add_user(f"other{n}") for n in range(5)]
    all_kinds = ["complaint", "billing", "vague"]

    calls: list[Call] = []
    _calls(place, calls, team=big, agents=big_agents, each=6, kinds=all_kinds)
    _calls(place, calls, team=small, agents=small_agents, each=4, kinds=["complaint", "billing"])
    # A7 (a): the supervisor is one of the agents of the small team
    _calls(place, calls, team=small, agents=[sup.user_id], each=25, kinds=["complaint", "billing"])
    _calls(place, calls, team=other, agents=other_agents, each=6, kinds=all_kinds)
    for index in range(31):  # the caller's own calls, no team: 14 + 14 + 3
        kind = "complaint" if index < 14 else "billing" if index < 28 else "vague"
        made = seed(
            place,
            received_at=_at(index % 2, index),
            agent_user_id=me.user_id,
            extraction=KINDS[kind],
        )
        calls.append(Call(made.meeting_id, None, me.user_id, kind, index % 2))
    # A7 (b): the counted copy is in ``other``, outside the supervisor's scope
    original = seed(place, received_at=_at(0, 1), team_id=other, agent_user_id=other_agents[0])
    calls.append(Call(original.meeting_id, other, other_agents[0], "complaint", 0))
    copy = seed(
        place,
        received_at=_at(0, 2),
        team_id=big,
        agent_user_id=big_agents[0],
        duplicate_of=original.ingestion_id,
    )
    # A7 (c): an older run cited the same issue
    cited = seed(
        place,
        received_at=_at(1, 3),
        team_id=big,
        agent_user_id=big_agents[1],
        old_runs=[COMPLAINT],
    )
    calls.append(Call(cited.meeting_id, big, big_agents[1], "complaint", 1))
    current_spans = _spans(cited.meeting_id, current=True, indexes=[0, 1, 2])
    old_spans = _spans(cited.meeting_id, current=False, indexes=[0, 1])
    summary_spans = _spans(cited.meeting_id, current=True, indexes=[0], finding_type="summary")
    # A7 (d): one second before the window, and a mock transcript
    early = seed(
        place, received_at=FROM - timedelta(seconds=1), team_id=big, agent_user_id=big_agents[2]
    )
    mock = seed(
        place, received_at=_at(0, 4), team_id=big, agent_user_id=big_agents[3], revision_mock=True
    )
    never = {copy.meeting_id, early.meeting_id, mock.meeting_id}

    def scope_of(persona):
        if persona is owner:
            return lambda c: True
        if persona is sup:
            return lambda c: c.team in (big, small) or sup.user_id in (c.agent, c.uploader)
        return lambda c: me.user_id in (c.agent, c.uploader)

    paths = {"issues": ISSUES, "teams": TEAMS, "me": ME}

    def config(label, persona, trend_view, *, view=(), link=(), cohort=None, grain="none"):
        """One trend answer: every cell of it is checked.  ``view`` goes to both routes,
        ``link`` names the team block of ``/trends/teams``."""
        return label, persona, trend_view, list(view), list(link), cohort or (lambda c: True), grain

    def team(team_id):
        return lambda c: c.team == team_id

    # one answer per view and per kind of cohort; points only where some point is released
    configs = [
        config("owner, whole scope", owner, "issues", grain="day"),
        config("owner, big", owner, "issues", view=[("team_id", big)], cohort=team(big)),
        config("owner, other", owner, "issues", view=[("team_id", other)], cohort=team(other)),
        config("owner, small block", owner, "teams", link=[("team_id", small)], cohort=team(small)),
        config("owner, big block", owner, "teams", link=[("team_id", big)], cohort=team(big)),
        config("sup, whole scope", sup, "issues"),
        config("sup, small block", sup, "teams", link=[("team_id", small)], cohort=team(small)),
        config("me, self-view", me, "me"),
    ]
    seen: dict[str, set[str]] = {"linked": set(), "withheld": set()}
    listed: dict[int, set[str]] = {id(owner): set(), id(sup): set(), id(me): set()}
    statuses: set[str] = set()
    for label, persona, trend_view, view_params, link_params, cohort, grain in configs:
        query = [*WINDOW.items(), ("grain", grain), *view_params]
        view = persona.get(paths[trend_view], params=[*query, *link_params])
        assert view.status_code == 200, (label, view.text)
        body = view.json()
        assert "correlation_not_cause" in body["notices"], label  # A1
        if trend_view == "teams":
            body = next(t for t in body["teams"] if t["team"]["id"] == link_params[0][1])
        in_scope, member = scope_of(persona), cohort
        layer_rows = [(None, body["rows"])]
        layer_rows += [(index, point["rows"]) for index, point in enumerate(body["points"])]
        for point, rows in layer_rows:
            for row in rows:
                params = [
                    ("trend_view", trend_view),
                    *query,
                    *link_params,
                    ("row", _row_param(row)),
                ]
                if point is not None:
                    params.append(("point", str(point)))
                cell = row["cell"]
                statuses.add(f"{cell['status']}:{cell['numerator'] is None}")
                case = (label, point, _row_key(row))
                if cell["status"] != "computed":
                    refused = persona.get(LINKS, params=params)
                    assert _refusal(refused) == WITHHELD_BODY, (case, refused.text)
                    seen["withheld"].add(f"{label}:{point}:{_row_param(row)}")
                    continue
                pages = _pages(persona, params, cell["numerator"])
                first = pages[0]
                assert first["cell"] == cell and first["disclosure"] == body["disclosure"], case
                assert first["page"] == {"offset": 0, "limit": 100, "total": cell["numerator"]}
                assert first["trend_view"] == trend_view and first["notices"] == [
                    "correlation_not_cause"
                ], case
                assert first["row"] == {
                    "level": row["level"],
                    "family": row["family"],
                    "issue": row["issue"],
                }, case
                ids = [c["meeting_id"] for page in pages for c in page["conversations"]]
                assert ids == sorted(ids) and len(set(ids)) == len(ids), case  # A7 (e)
                expected = _expected(
                    calls, _row_key(row), lambda c, s=in_scope, m=member: s(c) and m(c), point
                )
                assert ids == expected, case
                assert not set(ids) & never, case
                for item in (c for page in pages for c in page["conversations"]):
                    assert set(item) == {"meeting_id", "state", "analysis_run_id", "evidence_ids"}
                    assert item["state"] == "counted", case
                listed[id(persona)].update(ids)
                seen["linked"].add(f"{label}:{point}:{_row_param(row)}")
                assert cause_keys(first) == [], case
    # the dataset holds every kind of cell
    assert {
        "computed:False",
        "insufficient_sample:False",
        "insufficient_sample:True",
        "withheld:True",
        "not_computable:True",
    } <= statuses, statuses
    assert seen["linked"] and seen["withheld"]

    # A7 (b): the copy is never listed, and the supervisor never sees the counted copy
    assert original.meeting_id in listed[id(owner)]
    assert original.meeting_id not in listed[id(sup)] | listed[id(me)]
    assert not never & (listed[id(owner)] | listed[id(sup)] | listed[id(me)])

    # every listed meeting is one the caller may open now (a sample per caller)
    for persona in (owner, sup, me):
        for meeting_id in sorted(listed[id(persona)])[:5]:
            opened = persona.get(f"/api/v1/meetings/{meeting_id}")
            assert opened.status_code == 200, opened.text

    # A7 (c): only the current run's spans of the row's values
    def evidence_of(row: str) -> list[str]:
        params = [("trend_view", "issues"), *WINDOW.items(), ("row", row)]
        total = owner.get(LINKS, params=params).json()["page"]["total"]
        pages = _pages(owner, params, total)
        items = [c for page in pages for c in page["conversations"]]
        return next(c["evidence_ids"] for c in items if c["meeting_id"] == cited.meeting_id)

    assert evidence_of("issue:intent_report_dissatisfaction") == [current_spans[0]]
    assert evidence_of("family:service_complaint") == current_spans[:2]
    assert evidence_of("issue:outcome_escalated") == [current_spans[2]]
    assert not set(evidence_of("family:call_reason")) & {*old_spans, *summary_spans}
    for evidence_id in current_spans:
        opened = owner.get(f"/api/v1/meetings/{cited.meeting_id}/evidence/{evidence_id}")
        assert opened.status_code == 200, opened.text

    # A7 (e): small pages are the large page cut in order
    base = [
        ("trend_view", "issues"),
        *WINDOW.items(),
        ("team_id", big),
        ("row", "family:call_reason"),
    ]
    whole = owner.get(LINKS, params=[*base, ("limit", "100")]).json()
    cut = []
    for offset in range(0, whole["page"]["total"], 7):
        page = owner.get(LINKS, params=[*base, ("offset", str(offset)), ("limit", "7")]).json()
        assert page["page"] == {"offset": offset, "limit": 7, "total": whole["page"]["total"]}
        cut.extend(c["meeting_id"] for c in page["conversations"])
    assert cut == [c["meeting_id"] for c in whole["conversations"]]

    # the deny table rows that need data (design section 7, rows 16 to 23 and A3)
    def link(persona, trend_view, *extra, window=True, grain="day"):
        query = [("trend_view", trend_view), *(WINDOW.items() if window else ()), ("grain", grain)]
        return persona.get(LINKS, params=[*query, *extra])

    def view_cell(persona, path, *extra, team=None, point=None, row="family:call_reason"):
        body = persona.get(path, params=[*WINDOW.items(), ("grain", "day"), *extra]).json()
        if team is not None:
            body = next(t for t in body["teams"] if t["team"]["id"] == team)
        rows = body["rows"] if point is None else body["points"][point]["rows"]
        return next(r["cell"] for r in rows if _row_param(r) == row)

    refused, allowed = (403, "trend_links_withheld", None), (200, None, None)
    small_block, big_block = ("team_id", small), ("team_id", big)
    cases = [
        (
            "16 the caller is one agent of a small group",
            link(sup, "teams", small_block, ("row", "issue:intent_report_dissatisfaction")),
            refused,
        ),
        (
            "16 allow: the same cohort, viewer outside",
            link(owner, "teams", small_block, ("row", "issue:intent_report_dissatisfaction")),
            allowed,
        ),
        (
            "17 a masked point",
            link(owner, "teams", small_block, ("row", "family:call_reason"), ("point", "0")),
            refused,
        ),
        (
            "17 allow: its window",
            link(owner, "teams", small_block, ("row", "family:call_reason")),
            allowed,
        ),
        ("18 a hidden numerator", link(me, "me", ("row", "issue:complaint_refund")), refused),
        ("18 allow: a computed own cell", link(me, "me", ("row", "family:call_reason")), allowed),
        (
            "19 a team outside the released set",
            link(owner, "teams", ("team_id", other), ("row", "family:call_reason")),
            refused,
        ),
        (
            "19 the same team as a filter",
            link(owner, "issues", ("team_id", other), ("row", "family:call_reason")),
            refused,
        ),
        (
            "19 allow: a released team",
            link(owner, "teams", big_block, ("row", "family:call_reason")),
            allowed,
        ),
        ("21 the qa row", link(owner, "issues", ("row", "family:qa")), refused),
        (
            "21 an empty denominator",
            link(owner, "issues", ("language", "en"), ("row", "family:call_reason")),
            refused,
        ),
        (
            "21 allow: a family with data",
            link(owner, "issues", ("language", "fa"), ("row", "family:call_reason")),
            allowed,
        ),
        (
            "A3 a rate cell under 30, numerator shown",
            link(owner, "teams", big_block, ("row", "issue:complaint_refund")),
            refused,
        ),
        (
            "A3 allow: the same cohort's computed cell",
            link(owner, "teams", big_block, ("row", "family:call_reason")),
            allowed,
        ),
        (
            "23 a released cell with numerator 0",
            link(owner, "issues", big_block, ("row", "issue:intent_cancel_service")),
            allowed,
        ),
    ]
    for label, response, expected in cases:
        assert _answer(response) == expected, (label, response.text)
    # the cells behind those rows are the cells the cases name
    assert (
        view_cell(sup, TEAMS, small_block, team=small, row="issue:intent_report_dissatisfaction")[
            "status"
        ]
        == "withheld"
    )
    masked_point = view_cell(owner, TEAMS, small_block, team=small, point=0)
    assert masked_point["numerator"] is None, masked_point  # hidden or withheld
    small_view = owner.get(TEAMS, params=[*WINDOW.items(), ("grain", "day"), small_block]).json()
    assert small_view["teams"][0]["disclosure"]["masked"] == ["points"]
    assert view_cell(me, ME, row="issue:complaint_refund")["numerator"] is None
    assert view_cell(owner, TEAMS, ("team_id", other), team=other)["status"] == "withheld"
    rate_point = view_cell(owner, TEAMS, big_block, team=big, row="issue:complaint_refund")
    assert rate_point["status"] == "insufficient_sample" and rate_point["numerator"], rate_point
    cancel = view_cell(owner, ISSUES, big_block, row="issue:intent_cancel_service")
    assert (cancel["status"], cancel["numerator"]) == ("computed", 0), cancel
    empty = link(owner, "issues", big_block, ("row", "issue:intent_cancel_service")).json()
    assert empty["page"]["total"] == 0 and empty["conversations"] == []
    past = link(owner, "issues", big_block, ("row", "family:call_reason"), ("offset", "999"))
    assert past.json()["conversations"] == [] and past.json()["page"]["offset"] == 999
    # 20: a hidden numerator of 0 and one above 0 give the same body
    zero = link(sup, "teams", small_block, ("row", "issue:intent_cancel_service"))
    some = link(sup, "teams", small_block, ("row", "issue:intent_report_dissatisfaction"))
    assert _refusal(zero) == _refusal(some) == WITHHELD_BODY


# --- 2. who may call, and which query --------------------------------------------------------


def test_link_refusals_and_their_closest_allows(client, email_outbox, monkeypatch):
    owner, people, memberships = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"sup": "member", "viewer": "viewer", "ex": "member", "stale": "member"},
    )
    sup, viewer, ex = people["sup"], people["viewer"], people["ex"]
    place = _place(owner)
    alpha, beta, gamma = (add_team(place, name) for name in ("alpha", "beta", "gamma"))
    _deactivate(gamma)
    _assign(place, alpha, sup.user_id, "supervisor", by=owner.user_id)
    _assign(place, beta, ex.user_id, "supervisor", by=owner.user_id, expired=True)
    for index in range(31):  # no agent: a rate cohort, released at 30
        seed(place, received_at=_at(index % 2, index), team_id=alpha)
    RateLimiter.reset_memory()  # five sign-ups an hour per address
    stranger_session = login_user(
        client, register_user(client, email_outbox, email="links-out@example.com")
    )
    stranger = _Persona(client, owner.workspace_id, stranger_session, _cookies(client))
    analyst = _persona(client, email_outbox, email="links-analyst@example.com", name="Analyst")
    granted = owner.post(
        f"/api/v1/organizations/{place.organization_id}/members",
        json={"email": analyst.email, "role": "analyst"},
    )
    assert granted.status_code == 201, granted.text
    executive = _persona(client, email_outbox, email="links-exec@example.com", name="Executive")
    granted = owner.post(
        f"/api/v1/organizations/{place.organization_id}/members",
        json={"email": executive.email, "role": "executive"},
    )
    assert granted.status_code == 201, granted.text
    org_owner = _persona(client, email_outbox, email="links-org-owner@example.com", name="Owner2")
    with inspection_session_factory()() as db:  # nobody grants owner through the API (ADR 0012)
        db.add(
            OrganizationMembership(
                organization_id=place.organization_id,
                user_id=org_owner.user_id,
                role=OrganizationRole.OWNER,
                is_active=True,
                valid_from=datetime.now(UTC) - DAY,
            )
        )
        db.commit()
    # the organization roles are effective: their organization route answers
    for persona in (analyst, executive, org_owner):
        reached = persona.get(f"/api/v1/organizations/{place.organization_id}/trends/issues")
        assert reached.status_code == 200, reached.text
    key = _mint(client, owner.headers, owner.cookies, scopes=["meeting:read"])
    revoked_key = _mint(client, owner.headers, owner.cookies, name="revoked")
    expired_key = _mint(client, owner.headers, owner.cookies, name="expired")
    assert {key.status_code, revoked_key.status_code, expired_key.status_code} == {201}, key.text
    revoked = client.delete(
        f"/api/v1/api-keys/{revoked_key.json()['id']}", headers=owner.headers, cookies=owner.cookies
    )
    assert revoked.status_code == 204, revoked.text
    with inspection_session_factory()() as db:
        db.get(ApiKey, expired_key.json()["id"]).expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
        db.commit()
    # the keys really are revoked and expired: a route that takes keys refuses them
    for dead in (revoked_key, expired_key):
        client.cookies.clear()
        refused = client.get(
            "/api/v1/meetings",
            headers=_bearer(dead.json()["key"], **{"X-Workspace-ID": owner.workspace_id}),
        )
        assert _answer(refused) == (401, "api_key_invalid", None), refused.text

    def params(*extra, view="issues", window=True, row="family:call_reason"):
        query = [("trend_view", view)] if view else []
        query += [*(WINDOW.items() if window else ()), *extra]
        return [*query, ("row", row)] if row else query

    def anonymous(*extra, headers=None):
        client.cookies.clear()
        return client.get(
            LINKS,
            params=params(*extra),
            headers={"X-Workspace-ID": owner.workspace_id, **(headers or {})},
        )

    alpha_block = ("team_id", alpha)
    unknown = str(uuid.uuid4())
    other_agent = add_user("someone")
    cases = [
        ("1 no credentials", anonymous(), (401, "authentication_required", None)),
        (
            "2 an API key with meeting:read",
            anonymous(headers=_bearer(key.json()["key"])),
            (401, "api_key_not_accepted", None),
        ),
        (
            "2 an unknown key",
            anonymous(headers=_bearer("hk_00000000.nope")),
            (401, "api_key_not_accepted", None),
        ),
        (
            "2 a revoked key",
            anonymous(headers=_bearer(revoked_key.json()["key"])),
            (401, "api_key_not_accepted", None),
        ),
        (
            "2 an expired key",
            anonymous(headers=_bearer(expired_key.json()["key"])),
            (401, "api_key_not_accepted", None),
        ),
        (
            "1, 2 allow: the key's user with a session",
            owner.get(LINKS, params=params()),
            (200, None, None),
        ),
        (
            "3 a stranger",
            stranger.get(LINKS, params=params()),
            (403, "workspace_access_denied", None),
        ),
        (
            "3 an organization analyst",
            analyst.get(LINKS, params=params(), workspace_id=owner.workspace_id),
            (403, "workspace_access_denied", None),
        ),
        (
            "3 an organization executive",
            executive.get(LINKS, params=params(), workspace_id=owner.workspace_id),
            (403, "workspace_access_denied", None),
        ),
        (
            "3 an organization owner",
            org_owner.get(LINKS, params=params(), workspace_id=owner.workspace_id),
            (403, "workspace_access_denied", None),
        ),
        ("3 allow: a viewer", viewer.get(LINKS, params=params()), (200, None, None)),
        (
            "5 trend_view missing",
            owner.get(LINKS, params=params(view=None)),
            (422, "trend_view_invalid", "trend_view"),
        ),
        (
            "5 trend_view repeated",
            owner.get(LINKS, params=[("trend_view", "me"), *params()]),
            (422, "trend_view_invalid", "trend_view"),
        ),
        (
            "5 trend_view org",
            owner.get(LINKS, params=params(view="org")),
            (422, "trend_view_invalid", "trend_view"),
        ),
        (
            "6 a scope key",
            owner.get(LINKS, params=params(("workspace_id", owner.workspace_id))),
            (422, "filter_scope_not_allowed", "workspace_id"),
        ),
        (
            "6 a scope key on teams",
            owner.get(LINKS, params=params(("role", "owner"), alpha_block, view="teams")),
            (422, "filter_scope_not_allowed", "role"),
        ),
        (
            "7 agent filter on teams",
            owner.get(
                LINKS, params=params(alpha_block, ("agent_user_id", owner.user_id), view="teams")
            ),
            (422, "trend_parameter_not_allowed", "agent_user_id"),
        ),
        (
            "7 agent filter on me",
            owner.get(LINKS, params=params(("agent_user_id", owner.user_id), view="me")),
            (422, "trend_parameter_not_allowed", "agent_user_id"),
        ),
        (
            "7 allow: own agent id on issues",
            owner.get(LINKS, params=params(("agent_user_id", owner.user_id))),
            (403, "trend_links_withheld", None),
        ),
        (
            "8 an unknown key",
            owner.get(LINKS, params=params(("cursor", "1"))),
            (422, "filter_unknown_key", "cursor"),
        ),
        ("8 allow: offset", owner.get(LINKS, params=params(("offset", "1"))), (200, None, None)),
        (
            "9 a bad grain",
            owner.get(LINKS, params=params(("grain", "hour"))),
            (422, "trend_grain_invalid", "grain"),
        ),
        (
            "A4 from missing",
            owner.get(LINKS, params=params(("to", WINDOW["to"]), window=False)),
            (422, "trend_window_required", "from"),
        ),
        (
            "A4 to missing",
            owner.get(LINKS, params=params(("from", WINDOW["from"]), window=False)),
            (422, "trend_window_required", "to"),
        ),
        (
            "A4 both missing",
            owner.get(LINKS, params=params(window=False)),
            (422, "trend_window_required", "from"),
        ),
        (
            "A4 from twice",
            owner.get(LINKS, params=params(("from", WINDOW["from"]))),
            (422, "filter_bad_type", "from"),
        ),
        (
            "10 row missing",
            owner.get(LINKS, params=params(row=None)),
            (422, "trend_row_invalid", "row"),
        ),
        (
            "10 row unknown",
            owner.get(LINKS, params=params(row="issue:nope")),
            (422, "trend_row_invalid", "row"),
        ),
        (
            "10 row not selected",
            owner.get(LINKS, params=params(("issue", "complaint_refund"), row="family:qa")),
            (422, "trend_row_invalid", "row"),
        ),
        (
            "10 allow: a selected row",
            owner.get(
                LINKS, params=params(("issue", "complaint_refund"), row="issue:complaint_refund")
            ),
            (200, None, None),
        ),
        (
            "11 point with grain none",
            owner.get(LINKS, params=params(("point", "0"))),
            (422, "trend_point_invalid", "point"),
        ),
        (
            "11 point past the last",
            owner.get(LINKS, params=params(("grain", "day"), ("point", "2"))),
            (422, "trend_point_invalid", "point"),
        ),
        (
            "11 point negative",
            owner.get(LINKS, params=params(("grain", "day"), ("point", "-1"))),
            (422, "trend_point_invalid", "point"),
        ),
        (
            "11 allow: the last point",
            owner.get(LINKS, params=params(("grain", "day"), ("point", "1"))),
            (403, "trend_links_withheld", None),
        ),
        (
            "12 limit 0",
            owner.get(LINKS, params=params(("limit", "0"))),
            (422, "trend_page_invalid", "limit"),
        ),
        (
            "12 limit 101",
            owner.get(LINKS, params=params(("limit", "101"))),
            (422, "trend_page_invalid", "limit"),
        ),
        (
            "12 offset -1",
            owner.get(LINKS, params=params(("offset", "-1"))),
            (422, "trend_page_invalid", "offset"),
        ),
        (
            "12 allow: limit 100",
            owner.get(LINKS, params=params(("limit", "100"))),
            (200, None, None),
        ),
        (
            "13 another agent",
            owner.get(LINKS, params=params(("agent_user_id", other_agent))),
            (403, "agent_scope_denied", "agent_user_id"),
        ),
        (
            "14 teams without a team",
            owner.get(LINKS, params=params(view="teams")),
            (422, "trend_team_invalid", "team_id"),
        ),
        (
            "14 teams with two teams",
            owner.get(LINKS, params=params(alpha_block, ("team_id", beta), view="teams")),
            (422, "trend_team_invalid", "team_id"),
        ),
        (
            "15 a member who supervises nothing",
            viewer.get(LINKS, params=params(alpha_block, view="teams")),
            (403, "team_scope_denied", "team_id"),
        ),
        (
            "15 an ended assignment",
            ex.get(LINKS, params=params(("team_id", beta), view="teams")),
            (403, "team_scope_denied", "team_id"),
        ),
        (
            "15 another team",
            sup.get(LINKS, params=params(("team_id", beta), view="teams")),
            (403, "team_scope_denied", "team_id"),
        ),
        (
            "15 an inactive team",
            owner.get(LINKS, params=params(("team_id", gamma), view="teams")),
            (403, "team_scope_denied", "team_id"),
        ),
        (
            "15 an unknown team",
            owner.get(LINKS, params=params(("team_id", unknown), view="teams")),
            (403, "team_scope_denied", "team_id"),
        ),
        (
            "15 allow: a supervised team",
            sup.get(LINKS, params=params(alpha_block, view="teams")),
            (200, None, None),
        ),
        (
            "15 allow: an owner",
            owner.get(LINKS, params=params(alpha_block, view="teams")),
            (200, None, None),
        ),
        # the refusal order (ADR 0040 decision 3)
        (
            "order: trend_view before the query",
            owner.get(LINKS, params=params(("grain", "hour"), view=None)),
            (422, "trend_view_invalid", "trend_view"),
        ),
        (
            "order: the view's query before the window",
            owner.get(LINKS, params=params(("grain", "hour"), window=False)),
            (422, "trend_grain_invalid", "grain"),
        ),
        (
            "order: A5 the own-agent check is the view's",
            owner.get(LINKS, params=params(("agent_user_id", other_agent), window=False)),
            (403, "agent_scope_denied", "agent_user_id"),
        ),
        (
            "order: the window before the row",
            owner.get(LINKS, params=params(window=False, row="issue:nope")),
            (422, "trend_window_required", "from"),
        ),
        (
            "order: A6 the team before the row",
            owner.get(LINKS, params=params(view="teams", row="issue:nope")),
            (422, "trend_team_invalid", "team_id"),
        ),
        (
            "order: the row before the point",
            owner.get(LINKS, params=params(("point", "9"), row="issue:nope")),
            (422, "trend_row_invalid", "row"),
        ),
        (
            "order: the point before the page",
            owner.get(LINKS, params=params(("point", "9"), ("limit", "0"))),
            (422, "trend_point_invalid", "point"),
        ),
        (
            "order: offset before limit",
            owner.get(LINKS, params=params(("offset", "x"), ("limit", "0"))),
            (422, "trend_page_invalid", "offset"),
        ),
        (
            "order: the page before the team scope",
            viewer.get(LINKS, params=params(alpha_block, ("limit", "0"), view="teams")),
            (422, "trend_page_invalid", "limit"),
        ),
    ]
    for label, response, expected in cases:
        assert _answer(response) == expected, (label, response.text)

    # 3: the closest allow of each organization role is the same person with a membership
    for persona in (analyst, executive, org_owner):
        _install_member(place, persona.user_id)
        allowed = persona.get(LINKS, params=params(), workspace_id=owner.workspace_id)
        assert _answer(allowed) == (200, None, None), allowed.text

    # 4: the same user after the membership window ended
    stale = people["stale"]
    assert _answer(stale.get(LINKS, params=params()))[0] == 200
    _expire_membership(memberships["stale"])
    expired = stale.get(LINKS, params=params())
    assert _answer(expired) == (403, "workspace_access_denied", None), expired.text


# --- 3. record scope, shares, redaction, deletion ---------------------------------------------


def test_links_follow_record_scope_shares_redaction_and_deletion(client, email_outbox, monkeypatch):
    owner, people, _ = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"sup": "member", "ag": "member"}
    )
    sup, ag = people["sup"], people["ag"]
    place = _place(owner)
    _record_scope(place, RecordScope.TEAM)
    alpha, beta, edge = (add_team(place, name) for name in ("alpha", "beta", "edge"))
    _assign(place, alpha, sup.user_id, "supervisor", by=owner.user_id)
    alpha_ids = [
        seed(place, received_at=_at(i % 2, i), team_id=alpha).meeting_id for i in range(34)
    ]
    beta_ids = [seed(place, received_at=_at(i % 2, i), team_id=beta).meeting_id for i in range(31)]
    uploads = [  # the member uploaded these for the team; they are not its agent
        seed(place, received_at=_at(i % 2, i), team_id=beta, created_by=ag.user_id).meeting_id
        for i in range(30)
    ]

    def listed(persona, trend_view, *extra, row="family:call_reason") -> list[str] | None:
        response = persona.get(
            LINKS,
            params=[
                ("trend_view", trend_view),
                *WINDOW.items(),
                *extra,
                ("row", row),
                ("limit", "100"),
            ],
        )
        if response.status_code != 200:
            assert _refusal(response) == WITHHELD_BODY, response.text
            return None
        return [c["meeting_id"] for c in response.json()["conversations"]]

    # a restricted supervisor: the supervised team only
    assert listed(sup, "issues") == sorted(alpha_ids)
    assert listed(sup, "teams", ("team_id", alpha)) == sorted(alpha_ids)
    # 22: a team the member does not supervise narrows to its own uploads, as /trends/issues
    assert listed(ag, "issues", ("team_id", beta)) == sorted(uploads)
    assert listed(ag, "issues") == sorted(uploads)
    assert listed(ag, "me") is None  # uploads are not the member's calls as agent
    assert listed(owner, "issues", ("team_id", beta)) == sorted([*beta_ids, *uploads])

    # a share grants one meeting through the share routes; it never adds a link
    shared = _share(client, owner.headers, owner.cookies, beta_ids[0], audience="organization")
    assert shared.status_code == 201, shared.text
    assert sup.get(f"/api/v1/shares/{_token(shared.json())}").status_code == 200
    assert sup.get(f"/api/v1/meetings/{beta_ids[0]}").status_code == 404
    assert beta_ids[0] not in (listed(sup, "issues") or [])

    # an enforced redaction policy: the link holds no text and writes no audit event
    spans = _spans(alpha_ids[0], current=True, indexes=[0])
    before = owner.get(
        LINKS,
        params=[
            ("trend_view", "issues"),
            *WINDOW.items(),
            ("team_id", alpha),
            ("row", "family:call_reason"),
        ],
    ).json()
    policy = owner.patch("/api/v1/admin/workspace-settings", json={"pii_redaction_enabled": True})
    assert policy.status_code == 200, policy.text
    audits = _audits(AuditEventType.TRANSCRIPT_ORIGINAL_ACCESSED)
    after = owner.get(
        LINKS,
        params=[
            ("trend_view", "issues"),
            *WINDOW.items(),
            ("team_id", alpha),
            ("row", "family:call_reason"),
        ],
    ).json()
    assert after == before
    assert _audits(AuditEventType.TRANSCRIPT_ORIGINAL_ACCESSED) == audits
    item = next(c for c in after["conversations"] if c["meeting_id"] == alpha_ids[0])
    assert item["evidence_ids"] == spans
    original = owner.get(
        f"/api/v1/meetings/{alpha_ids[0]}/evidence/{spans[0]}", params={"view": "original"}
    )
    assert original.status_code == 200, original.text
    assert _audits(AuditEventType.TRANSCRIPT_ORIGINAL_ACCESSED) == audits + 1

    # a tombstoned meeting is gone from the cell and the list
    deleted = owner.delete(f"/api/v1/meetings/{alpha_ids[1]}")
    assert deleted.status_code in (200, 202, 204), deleted.text
    assert listed(sup, "issues") == sorted(alpha_ids[:1] + alpha_ids[2:])
    # a legal hold keeps the rows of a tombstoned meeting; only deleted_at decides
    with inspection_session_factory()() as db:
        db.execute(update(Meeting).where(Meeting.id == alpha_ids[2]).values(deleted_at=FROM))
        db.commit()
    assert listed(sup, "issues") == sorted(alpha_ids[:1] + alpha_ids[3:])

    # a meeting moved to another team follows its current team, for the cell and the list
    with inspection_session_factory()() as db:
        db.execute(update(Meeting).where(Meeting.id == beta_ids[1]).values(team_id=alpha))
        db.commit()
    assert listed(sup, "issues") == sorted(alpha_ids[:1] + alpha_ids[3:] + beta_ids[1:2])
    assert beta_ids[1] not in listed(owner, "issues", ("team_id", beta))

    # an inactive team leaves the supervisor's scope
    _deactivate(alpha)
    assert listed(sup, "issues") is None
    refused = sup.get(
        LINKS,
        params=[
            ("trend_view", "teams"),
            *WINDOW.items(),
            ("team_id", alpha),
            ("row", "family:call_reason"),
        ],
    )
    assert _answer(refused) == (403, "team_scope_denied", "team_id"), refused.text

    # A7 (f): a released cell, then one deletion makes the small group withhold
    agents = [add_user(f"edge{n}") for n in range(3)]
    edge_ids = [
        seed(place, received_at=_at(i % 2, i), team_id=edge, agent_user_id=agents[i % 3]).meeting_id
        for i in range(30)
    ]
    vague = seed(
        place, received_at=_at(0, 31), team_id=edge, agent_user_id=agents[0], extraction=VAGUE
    ).meeting_id
    edge_row = ("row", "issue:intent_report_dissatisfaction")
    assert listed(owner, "issues", ("team_id", edge), row=edge_row[1]) == sorted([*edge_ids, vague])
    assert owner.delete(f"/api/v1/meetings/{edge_ids[0]}").status_code in (200, 202, 204)
    assert listed(owner, "issues", ("team_id", edge), row=edge_row[1]) is None
    view = owner.get(ISSUES, params=[*WINDOW.items(), ("team_id", edge)]).json()
    cell = next(r["cell"] for r in view["rows"] if _row_param(r) == edge_row[1])
    assert cell["status"] == "withheld", cell


# --- 4. cost, and the notice on every trend answer --------------------------------------------


# The "executive" call below gives no window, so its window is the last four closed Tehran
# weeks of "now" (role_trend_service.organization_query).  Only while "now" is in Tehran
# [2026-10-03, 2026-10-31) does that window cover this test's seed week (2026-09-26), whose
# single-agent population withholds the block; the empty weeks after 2026-10-31 release it
# and its coverage definitions name "conversations" (HOM-370).  Frozen to the last date the
# test passed on main.
_FROZEN_NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


@freeze_time(_FROZEN_NOW)
def test_link_statement_counts_and_notices(client, email_outbox, monkeypatch):
    owner, people, _ = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"sup": "member"}
    )
    sup = people["sup"]
    place = _place(owner)
    alpha, beta = (add_team(place, name) for name in ("alpha", "beta"))
    _assign(place, alpha, sup.user_id, "supervisor", by=owner.user_id)
    alpha_ids = [
        seed(place, received_at=_at(i % 2, i), team_id=alpha).meeting_id for i in range(31)
    ]
    for index in range(31):
        seed(place, received_at=_at(index % 2, index), team_id=beta)
        seed(place, received_at=_at(index % 2, index), agent_user_id=owner.user_id)
    for meeting_id in alpha_ids[:3]:
        _spans(meeting_id, current=True, indexes=[0])

    def count(persona, path, *extra):
        number, response = _statements(lambda: persona.get(path, params=[*WINDOW.items(), *extra]))
        return number, response

    def link(persona, trend_view, *extra):
        return count(persona, LINKS, ("trend_view", trend_view), *extra)

    alpha_block, row = ("team_id", alpha), ("row", "family:call_reason")
    cases = [
        # (label, link request, view request, link statements, view statements)
        (
            "issues, a page",
            link(owner, "issues", alpha_block, row),
            count(owner, ISSUES, alpha_block),
            7,
            14,
        ),
        (
            "issues, a page of one",
            link(owner, "issues", alpha_block, row, ("limit", "1")),
            count(owner, ISSUES, alpha_block),
            7,
            14,
        ),
        (
            "issues, an empty page",
            link(owner, "issues", alpha_block, row, ("offset", "50")),
            count(owner, ISSUES, alpha_block),
            5,
            14,
        ),
        (
            "issues, a refused cell",
            link(owner, "issues", alpha_block, ("row", "family:qa")),
            count(owner, ISSUES, alpha_block),
            5,
            14,
        ),
        ("me, a page", link(owner, "me", row), count(owner, ME), 7, 14),
        (
            "teams, owner",
            link(owner, "teams", alpha_block, row),
            count(owner, TEAMS, alpha_block),
            8,
            15,
        ),
        (
            "teams, a member who supervises",
            link(sup, "teams", alpha_block, row),
            count(sup, TEAMS, alpha_block),
            9,
            16,
        ),
    ]
    for label, (links, response), (views, view), pinned, pinned_view in cases:
        assert view.status_code == 200, (label, view.text)
        assert response.status_code in (200, 403), (label, response.text)
        assert (links, views) == (pinned, pinned_view), label
        page = response.status_code == 200 and response.json()["conversations"]
        assert links == views - 9 + (2 if page else 0), label

    # A1: the notice on every trend answer, and no key that names a cause
    organization = place.organization_id
    week_start = datetime(2026, 10, 2, 20, 30, tzinfo=UTC)  # Saturday 00:00 in Asia/Tehran
    answers = {
        "issues": owner.get(ISSUES, params=list(WINDOW.items())),
        "me": owner.get(ME, params=list(WINDOW.items())),
        "teams": owner.get(TEAMS, params=list(WINDOW.items())),
        "link": owner.get(LINKS, params=[("trend_view", "issues"), *WINDOW.items(), row]),
        "executive": owner.get(f"/api/v1/organizations/{organization}/trends/issues"),
        "drill-down": owner.get(
            f"/api/v1/organizations/{organization}/workspaces/{place.workspace_id}/trends/issues",
            params={"from": (week_start - 7 * DAY).isoformat(), "to": week_start.isoformat()},
        ),
    }
    for label, response in answers.items():
        assert response.status_code == 200, (label, response.text)
        body = response.json()
        assert "correlation_not_cause" in body["notices"], label
        assert cause_keys(body) == [], label
    assert answers["link"].json()["notices"] == ["correlation_not_cause"]
    # the organization views carry no link and no count of linkable conversations
    for label in ("executive", "drill-down"):
        text = answers[label].text
        assert "conversations" not in text and "meeting_id" not in text, label
    # a refusal is an error envelope with no notices (A8)
    refused = owner.get(
        LINKS, params=[("trend_view", "issues"), *WINDOW.items(), ("row", "family:qa")]
    )
    assert set(refused.json()) == {"error"}


# --- 5. row-level security ---------------------------------------------------------------------


@pytest.mark.postgres
def test_links_never_cross_a_workspace_under_row_level_security(client, email_outbox):
    owner = _persona(client, email_outbox, email="links-rls@example.com", name="Owner")
    organization = owner.organization_id
    first = _org_workspace(owner, organization, "First")
    second = _org_workspace(owner, organization, "Second")
    ids = {}
    for place in (first, second):
        ids[place.workspace_id] = sorted(
            seed(place, received_at=_at(i % 2, i)).meeting_id for i in range(31)
        )
        _spans(ids[place.workspace_id][0], current=True, indexes=[0])
    for place in (first, second):
        response = owner.get(
            LINKS,
            params=[
                ("trend_view", "issues"),
                *WINDOW.items(),
                ("row", "family:call_reason"),
                ("limit", "100"),
            ],
            workspace_id=place.workspace_id,
        )
        assert response.status_code == 200, response.text
        items = response.json()["conversations"]
        assert [c["meeting_id"] for c in items] == ids[place.workspace_id]
        assert len(items[0]["evidence_ids"]) == 1
        assert not {c["meeting_id"] for c in items} & {
            meeting
            for key, meetings in ids.items()
            if key != place.workspace_id
            for meeting in meetings
        }
