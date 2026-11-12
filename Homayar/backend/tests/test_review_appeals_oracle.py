"""HOM-76 PR 2 oracle: agent appeals, supervisor disposition, audit history, notifications.

Written from SPEC.md ("PR 2 rules", "Independence"), SPEC-pr2.md and ADR 0057 before the
implementation was visible.  Every refusal is paired with the closest request that must
still succeed, and every refusal must write nothing: no appeal row, no criterion row, no
appeal result, no audit event and no notification.  Rows are seeded in the database only
where no route exists (a moved completion time, a deleted call, a removed user, a tampered
engine version, bulk admins, inbox rows).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, update

from backend.database.saas_models import (
    AuditEvent,
    Membership,
    Notification,
    OrganizationMembership,
    ReviewAppeal,
    ReviewAppealCriterion,
    ReviewAppealResult,
    ReviewCriterionDecision,
    ReviewItem,
    ReviewResultRecord,
    ScorecardResultRecord,
    User,
    WorkspaceRole,
)
from backend.scorecards import SCORECARDS_VERSION
from backend.security.authorization import API_KEY_SCOPES
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _cookies, _Persona
from backend.tests.test_review_decisions_oracle import (
    ACCEPT,
    RESULTS,
    SCORE_KEYS,
    _block,
    _Case,
    _criterion_rows,
    _decision_rows,
    _engine,
    _human_results,
    _machine_snapshot,
    _override,
    _score,
    _transcript,
)
from backend.tests.test_review_queue_oracle import (
    _permissions,
    _race,
    _requires_postgres,
    _set_meeting,
    _signup,
    _World,
)
from backend.tests.test_scorecard_result_service import _criterion, _tamper

MY_REVIEWS = "/api/v1/my-reviews"
MY_APPEALS = "/api/v1/my-appeals"
APPEALS = "/api/v1/review-appeals"
NOTIFICATIONS = "/api/v1/notifications"
WINDOW = timedelta(days=14)
NOTE = "The fee was explained at turn 4 of the call."
DISPOSITION = "We listened to the call again."
CHANGE_NOTE = "The closing was said at turn 9."

REVIEW = (
    ("greeting", _override("failed", note="No greeting was heard.")),
    ("fee_explained", ACCEPT),
    (
        "callback",
        _override("failed", reason_code="policy_exception", note="No call back was offered."),
    ),
)
HUMAN_OUTCOMES = {"greeting": "failed", "fee_explained": "failed", "callback": "failed"}
APPEALED = {
    "greeting": "failed",
    "fee_explained": "failed",
    "callback": "failed",
    "closing": "passed",
}

WIDE = {
    "name": "Wide card",
    "pass_threshold": 60,
    "criteria": [
        _criterion(f"w{index:02d}", 5 if index < 16 else 4, "hello") for index in range(21)
    ],
}

SUMMARY_KEYS = {
    "review_item_id",
    "meeting_id",
    "completed_at",
    "appeal_deadline",
    "machine",
    "human",
    "final",
    "final_source",
    "appeal",
}
DETAIL_KEYS = SUMMARY_KEYS | {"criteria"}
DETAIL_CRITERION_KEYS = {
    "criterion_id",
    "position",
    "weight",
    "auto_fail",
    "na_allowed",
    "reviewable",
    "machine_outcome",
    "human_outcome",
    "decision",
    "appeal",
}
AGENT_DECISION_KEYS = {"decision", "reviewer_outcome", "reason_code", "note", "evidence"}
AGENT_APPEAL_KEYS = {"decision", "decided_outcome", "reason_code", "note"}
APPEAL_KEYS = {
    "id",
    "review_item_id",
    "meeting_id",
    "team_id",
    "scorecard_result_id",
    "status",
    "appellant_user_id",
    "original_reviewer_user_id",
    "assignee_user_id",
    "assigned_by_user_id",
    "assigned_at",
    "reason_note",
    "disposition",
    "disposition_note",
    "decided_by_user_id",
    "decided_at",
    "withdrawn_at",
    "created_at",
    "updated_at",
    "revision",
    "criteria",
    "human",
    "appeal_result",
    "final",
    "view",
}
APPELLANT_HIDDEN = (
    "original_reviewer_user_id",
    "assignee_user_id",
    "assigned_by_user_id",
    "decided_by_user_id",
    "team_id",
)
APPEAL_CRITERION_KEYS = {
    "criterion_id",
    "appealed_outcome",
    "decision",
    "decided_outcome",
    "reason_code",
    "note",
}
APPEAL_TIMES = ("assigned_at", "decided_at", "withdrawn_at", "created_at", "updated_at")
HISTORY_KEYS = {"id", "event_type", "occurred_at", "actor_user_id", "actor_role", "details"}
NOTIFICATION_KEYS = {"id", "kind", "title", "body", "data", "read_at", "created_at"}
TEXTS = {
    "review_appeal.filed": (
        "Appeal filed",
        "An agent appealed a completed review. Open the appeals queue to assign it.",
    ),
    "review_appeal.assigned": ("Appeal assigned to you", "You were asked to decide an appeal."),
    "review_appeal.decided": (
        "Your appeal was decided",
        "A reviewer decided your appeal. Open it to see the result.",
    ),
    "review_appeal.withdrawn": (
        "Appeal withdrawn",
        "The agent withdrew an appeal assigned to you.",
    ),
}
EVENT_TYPES = tuple(TEXTS)


def _refused(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert isinstance(body, dict) and isinstance(body.get("error"), dict), response.text
    assert body["error"].get("code") == code, response.text


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    assert parsed.utcoffset() is not None, value
    return parsed


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _review(
    world: _World, *, agent: str = "agent", assignee: str = "sup", former: str | None = None
) -> _Case:
    case = _Case(world, assignee=former or assignee, agent=agent)
    decisions = list(REVIEW)
    if former is not None:
        case.decided(*decisions.pop(0))
        case.act("assign", by="owner", assignee_user_id=world.user_id(assignee))
        case.act("start", by=assignee)
        case.assignee = assignee
    for criterion_id, body in decisions:
        case.decided(criterion_id, body)
    case.act("complete", by=assignee)
    return case


def _wide(world: _World, *, agent: str = "agent") -> _Case:
    case = _Case.__new__(_Case)
    case.world = world
    case.assignee = "sup"
    case.meeting_id = world.new_meeting(agent=agent)
    _set_meeting(case.meeting_id, conversation_type="call", direction=None)
    case.revision_id = _transcript(world, case.meeting_id)
    case.result_id = _score(world, case.meeting_id, case.revision_id, key="wide", definition=WIDE)
    item = world.create(case.meeting_id, scorecard_result_id=case.result_id)
    item = world.done(item, "assign", assignee_user_id=world.user_id("sup"))
    item = world.done(item, "start", by="sup")
    case.item = world.done(item, "complete", by="sup")
    return case


def _human(case: _Case) -> ReviewResultRecord:
    [human] = _human_results(case.id)
    return human


def _human_outcome(case: _Case) -> dict[str, str]:
    return {entry["criterion_id"]: entry["outcome"] for entry in _human(case).result["criteria"]}


def _completed_at(case: _Case) -> datetime:
    with inspection_session_factory()() as db:
        return _aware(db.get(ReviewItem, case.id).completed_at)


def _set_completed(case: _Case, when: datetime) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(ReviewItem).where(ReviewItem.id == case.id).values(completed_at=when))
        db.commit()


def _rows(db, model, workspace_id: str) -> list[dict]:
    columns = model.__mapper__.column_attrs
    return [
        {column.key: getattr(row, column.key) for column in columns}
        for row in db.scalars(
            select(model).where(model.workspace_id == workspace_id).order_by(model.id)
        )
    ]


def _state(world: _World) -> str:
    with inspection_session_factory()() as db:
        snapshot = {
            "appeals": _rows(db, ReviewAppeal, world.workspace_id),
            "criteria": _rows(db, ReviewAppealCriterion, world.workspace_id),
            "results": _rows(db, ReviewAppealResult, world.workspace_id),
            "notifications": _rows(db, Notification, world.workspace_id),
            "events": db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.workspace_id == world.workspace_id)
            ),
        }
    return json.dumps(snapshot, default=str, sort_keys=True)


def _frozen(case: _Case) -> str:
    decisions = [
        {c.key: getattr(row, c.key) for c in ReviewCriterionDecision.__mapper__.column_attrs}
        for row in _decision_rows(case.id)
    ]
    humans = [
        {c.key: getattr(row, c.key) for c in ReviewResultRecord.__mapper__.column_attrs}
        for row in _human_results(case.id)
    ]
    return json.dumps(
        {"machine": _machine_snapshot(case.result_id), "decisions": decisions, "human": humans},
        default=str,
        sort_keys=True,
    )


def _events(world: _World) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.workspace_id == world.workspace_id)
                .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
            )
        )


def _seen(world: _World) -> set[str]:
    return {event.id for event in _events(world)}


def _since(world: _World, seen: set[str]) -> list[AuditEvent]:
    return [event for event in _events(world) if event.id not in seen]


def _notes(world: _World, *, appeal_id: str | None = None, kind: str | None = None) -> list:
    with inspection_session_factory()() as db:
        statement = select(Notification).where(Notification.workspace_id == world.workspace_id)
        rows = list(db.scalars(statement.order_by(Notification.user_id, Notification.id)))
    return [
        row
        for row in rows
        if (kind is None or row.kind == kind)
        and (appeal_id is None or (row.data or {}).get("appeal_id") == appeal_id)
    ]


def _recipients(world: _World, appeal_id: str, kind: str) -> list[str]:
    return [row.user_id for row in _notes(world, appeal_id=appeal_id, kind=kind)]


def _appeal_row(appeal_id: str) -> ReviewAppeal:
    with inspection_session_factory()() as db:
        row = db.get(ReviewAppeal, appeal_id)
        assert row is not None, appeal_id
        return row


def _criteria_rows(appeal_id: str) -> dict[str, ReviewAppealCriterion]:
    with inspection_session_factory()() as db:
        return {
            row.criterion_id: row
            for row in db.scalars(
                select(ReviewAppealCriterion).where(ReviewAppealCriterion.appeal_id == appeal_id)
            )
        }


def _results(appeal_id: str) -> list[ReviewAppealResult]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(select(ReviewAppealResult).where(ReviewAppealResult.appeal_id == appeal_id))
        )


def _check_score(view: dict) -> dict:
    assert set(view) == set(SCORE_KEYS), sorted(set(view) ^ set(SCORE_KEYS))
    return view


def _check_appeal(view: dict, kind: str) -> dict:
    assert set(view) == APPEAL_KEYS, sorted(set(view) ^ APPEAL_KEYS)
    assert view["view"] == kind, view["view"]
    _check_score(view["human"])
    _check_score(view["final"])
    if view["appeal_result"] is not None:
        _check_score(view["appeal_result"])
    for criterion in view["criteria"]:
        assert set(criterion) == APPEAL_CRITERION_KEYS, sorted(
            set(criterion) ^ APPEAL_CRITERION_KEYS
        )
    for name in APPEAL_TIMES:
        if view[name] is not None:
            _time(view[name])
    if kind == "appellant":
        for name in APPELLANT_HIDDEN:
            assert view[name] is None, name
    return view


def _check_summary(summary: dict, keys: set[str] = SUMMARY_KEYS) -> dict:
    assert set(summary) == keys, sorted(set(summary) ^ keys)
    for name in ("machine", "human", "final"):
        _check_score(summary[name])
    assert summary["final_source"] in ("human", "appeal"), summary["final_source"]
    if summary["appeal"] is not None:
        assert set(summary["appeal"]) == {"id", "status", "disposition"}, summary["appeal"]
    assert _time(summary["appeal_deadline"]) == _time(summary["completed_at"]) + WINDOW
    return summary


def _check_detail(detail: dict) -> dict:
    _check_summary(detail, DETAIL_KEYS)
    positions = [criterion["position"] for criterion in detail["criteria"]]
    assert positions == sorted(positions), positions
    for criterion in detail["criteria"]:
        assert set(criterion) == DETAIL_CRITERION_KEYS, sorted(
            set(criterion) ^ DETAIL_CRITERION_KEYS
        )
        if criterion["decision"] is not None:
            assert set(criterion["decision"]) == AGENT_DECISION_KEYS, criterion["decision"]
        if criterion["appeal"] is not None:
            assert set(criterion["appeal"]) == AGENT_APPEAL_KEYS, criterion["appeal"]
    return detail


def _page(response) -> tuple[list[dict], str | None]:
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor"}, body
    return body["items"], body["next_cursor"]


def _listed(persona: _Persona, path: str, **params) -> list[dict]:
    items, cursor = _page(persona.get(path, params=params))
    assert cursor is None, cursor
    return items


def _my_reviews(world: _World, by: str) -> list[dict]:
    return [_check_summary(item) for item in _listed(world.persona(by), MY_REVIEWS)]


def _my_review(world: _World, case: _Case, by: str = "agent") -> dict:
    response = world.persona(by).get(f"{MY_REVIEWS}/{case.id}")
    assert response.status_code == 200, response.text
    return _check_detail(response.json())


def _file(
    world: _World, case: _Case, criteria=("greeting", "fee_explained"), *, by="agent", note=NOTE
):
    return world.persona(by).post(
        f"{MY_REVIEWS}/{case.id}/appeal",
        json={"criterion_ids": list(criteria), "reason_note": note},
    )


def _filed(world: _World, case: _Case, criteria=("greeting", "fee_explained"), **kwargs) -> dict:
    response = _file(world, case, criteria, **kwargs)
    assert response.status_code == 201, response.text
    view = _check_appeal(response.json(), "appellant")
    assert (view["status"], view["revision"], view["review_item_id"]) == ("open", 1, case.id)
    return view


def _get(world: _World, appeal: dict, by: str):
    return world.persona(by).get(f"{APPEALS}/{appeal['id']}")


def _seen_by(world: _World, appeal: dict, by: str, kind: str = "supervisor") -> dict:
    response = _get(world, appeal, by)
    assert response.status_code == 200, (by, response.text)
    return _check_appeal(response.json(), kind)


def _act(world: _World, appeal: dict, action: str, *, by: str, revision: int | None = None, **body):
    expected = appeal["revision"] if revision is None else revision
    return world.persona(by).post(
        f"{APPEALS}/{appeal['id']}/{action}", json={"expected_revision": expected, **body}
    )


def _done(world: _World, appeal: dict, action: str, *, by: str, **body) -> dict:
    response = _act(world, appeal, action, by=by, **body)
    assert response.status_code == 200, (action, by, response.text)
    kind = "appellant" if action == "withdraw" else "supervisor"
    return _check_appeal(response.json(), kind)


def _assign(world: _World, appeal: dict, assignee: str, *, by: str = "owner") -> dict:
    view = _done(world, appeal, "assign", by=by, assignee_user_id=world.user_id(assignee))
    assert view["revision"] == appeal["revision"] + 1
    assert view["assignee_user_id"] == world.user_id(assignee)
    return view


def _uphold(criterion_id: str, **extra) -> dict:
    return {"criterion_id": criterion_id, "decision": "uphold", **extra}


def _change(criterion_id: str, outcome: str, **extra) -> dict:
    return {
        "criterion_id": criterion_id,
        "decision": "change",
        "decided_outcome": outcome,
        "reason_code": "evidence_missed",
        "note": CHANGE_NOTE,
        **extra,
    }


def _body(decisions: list[dict], note: str | None = DISPOSITION) -> dict:
    body: dict = {"decisions": decisions}
    if note is not None:
        body["disposition_note"] = note
    return body


def _history(world: _World, appeal: dict, by: str) -> list[dict]:
    items = _listed(world.persona(by), f"{APPEALS}/{appeal['id']}/history")
    for entry in items:
        assert set(entry) == HISTORY_KEYS, sorted(set(entry) ^ HISTORY_KEYS)
        assert entry["actor_role"] in ("appellant", "reviewer", "system"), entry
        _time(entry["occurred_at"])
    return items


def _hidden(world: _World, appeal: dict, name: str) -> None:
    persona = world.persona(name)
    before = _state(world)
    assert appeal["id"] not in {item["id"] for item in _listed(persona, APPEALS)}, name
    _refused(_get(world, appeal, name), 404, "review_appeal_not_found")
    _refused(persona.get(f"{APPEALS}/{appeal['id']}/history"), 404, "review_appeal_not_found")
    _refused(
        _act(world, appeal, "assign", by=name, assignee_user_id=world.owner.user_id),
        404,
        "review_appeal_not_found",
    )
    _refused(
        _act(world, appeal, "decide", by=name, **_body([_uphold("greeting")])),
        404,
        "review_appeal_not_found",
    )
    assert _state(world) == before, name


def _no_ids(text: str, world: _World, *names: str) -> None:
    for name in names:
        assert world.user_id(name) not in text, name


def _outsider(client, email_outbox, label: str) -> _Persona:
    credentials = _signup(client, email_outbox, email=f"appeal-{label}@example.com", name="Other")
    session = login_user(client, credentials)
    return _Persona(client, session["workspaces"][0]["id"], session, _cookies(client))


def _check_note(row: dict) -> dict:
    assert set(row) == NOTIFICATION_KEYS, sorted(set(row) ^ NOTIFICATION_KEYS)
    _time(row["created_at"])
    if row["read_at"] is not None:
        _time(row["read_at"])
    return row


def _inbox(persona: _Persona, **params) -> list[dict]:
    items, cursor = _page(persona.get(NOTIFICATIONS, params=params))
    assert cursor is None, cursor
    return [_check_note(item) for item in items]


def _seed_note(
    workspace_id: str, user_id: str, created_at: datetime, kind: str = "review_appeal.filed"
) -> str:
    title, text = TEXTS[kind]
    with inspection_session_factory()() as db:
        row = Notification(
            workspace_id=workspace_id,
            user_id=user_id,
            kind=kind,
            title=title,
            body=text,
            data={"appeal_id": str(uuid.uuid4())},
            created_at=created_at,
            updated_at=created_at,
        )
        db.add(row)
        db.commit()
        return row.id


def _own_workspace(user_id: str, other_than: str) -> str:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(Membership.workspace_id).where(
                Membership.user_id == user_id, Membership.workspace_id != other_than
            )
        )


# --- AC1 permissions, API keys and CSRF -------------------------------------------------


def test_every_role_holds_review_appeal_and_notification_read_and_no_api_key_can(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "viewer")
    for name in ("owner", "admin", "sup", "viewer"):
        assert {"review:appeal", "notification:read"} <= _permissions(world.persona(name)), name
    keyed = {scope.value for scope in API_KEY_SCOPES}
    assert {"review:appeal", "notification:read"} & keyed == set()
    for scopes in (["review:appeal"], ["notification:read"], ["meeting:read", "review:appeal"]):
        _refused(
            _mint(client, world.owner.headers, world.owner.cookies, scopes=scopes),
            422,
            "invalid_scopes",
        )
    minted = _mint(client, world.owner.headers, world.owner.cookies, scopes=["meeting:read"])
    assert minted.status_code == 201, minted.text


def _routes(world: _World, case: _Case, spare: _Case, appeal: dict, note_id: str):
    path = f"{APPEALS}/{appeal['id']}"
    revision = {"expected_revision": appeal["revision"]}
    return [
        ("agent", "GET", MY_REVIEWS, None),
        ("agent", "GET", f"{MY_REVIEWS}/{case.id}", None),
        (
            "agent",
            "POST",
            f"{MY_REVIEWS}/{spare.id}/appeal",
            {"criterion_ids": ["greeting"], "reason_note": NOTE},
        ),
        ("agent", "GET", MY_APPEALS, None),
        ("owner", "GET", APPEALS, None),
        ("owner", "GET", path, None),
        ("owner", "GET", f"{path}/history", None),
        (
            "owner",
            "POST",
            f"{path}/assign",
            {**revision, "assignee_user_id": world.user_id("sup_agent")},
        ),
        (
            "sup_agent",
            "POST",
            f"{path}/decide",
            {**revision, **_body([_uphold("greeting"), _uphold("fee_explained")])},
        ),
        ("agent", "POST", f"{path}/withdraw", revision),
        ("sup_agent", "GET", NOTIFICATIONS, None),
        ("sup_agent", "POST", f"{NOTIFICATIONS}/{note_id}/read", None),
    ]


def _setup_routes(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup_agent", "agent")
    case, spare = _review(world), _review(world)
    appeal = _assign(world, _filed(world, case), "sup_agent")
    [note] = _notes(world, appeal_id=appeal["id"], kind="review_appeal.assigned")
    return world, case, spare, appeal, note.id


def test_an_api_key_with_every_key_scope_is_refused_on_every_new_route(
    client, email_outbox, monkeypatch
):
    world, case, spare, appeal, note_id = _setup_routes(client, email_outbox, monkeypatch)
    scopes = sorted(scope.value for scope in API_KEY_SCOPES)
    minted = _mint(client, world.owner.headers, world.owner.cookies, scopes=scopes)
    assert minted.status_code == 201, minted.text
    key = _bearer(minted.json()["key"], **{"X-Workspace-ID": world.workspace_id})
    before = _state(world)
    for _, method, path, body in _routes(world, case, spare, appeal, note_id):
        _refused(client.request(method, path, json=body, headers=key), 403, "api_key_scope_denied")
    assert _state(world) == before

    assert [item["review_item_id"] for item in _my_reviews(world, "agent")]
    _done(world, appeal, "withdraw", by="agent")


def test_every_write_without_the_csrf_token_is_refused_and_reads_still_work(
    client, email_outbox, monkeypatch
):
    world, case, spare, appeal, note_id = _setup_routes(client, email_outbox, monkeypatch)
    before = _state(world)
    for name, method, path, body in _routes(world, case, spare, appeal, note_id):
        persona = world.persona(name)
        sent = client.request(
            method,
            path,
            json=body,
            cookies=persona.cookies,
            headers={"X-Workspace-ID": world.workspace_id},
        )
        if method == "POST":
            _refused(sent, 403, "csrf_failed")
        else:
            assert sent.status_code == 200, (path, sent.text)
    assert _state(world) == before

    _filed(world, spare, ["greeting"])
    read = world.persona("sup_agent").post(f"{NOTIFICATIONS}/{note_id}/read")
    assert read.status_code == 200, read.text


def test_a_viewer_reads_the_agent_and_inbox_routes_but_no_supervisor_route(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup_agent", "agent", "viewer")
    case = _review(world)
    appeal = _filed(world, case)
    viewer = world.persona("viewer")
    before = _state(world)
    assert _listed(viewer, MY_REVIEWS) == []
    assert _listed(viewer, MY_APPEALS) == []
    assert _inbox(viewer) == []
    _refused(viewer.get(APPEALS), 403, "permission_denied")
    _refused(_get(world, appeal, "viewer"), 404, "review_appeal_not_found")
    _refused(viewer.get(f"{APPEALS}/{appeal['id']}/history"), 404, "review_appeal_not_found")
    _refused(
        _act(world, appeal, "assign", by="viewer", assignee_user_id=world.user_id("sup_agent")),
        403,
        "permission_denied",
    )
    _refused(
        _act(world, appeal, "decide", by="viewer", **_body([_uphold("greeting")])),
        403,
        "permission_denied",
    )
    assert _state(world) == before

    _assign(world, appeal, "sup_agent")


def test_a_viewer_appellant_reads_the_appellant_view_and_history_only(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup_agent", "viewer")
    case = _review(world, agent="viewer")
    appeal = _filed(world, case, by="viewer")
    appeal = _assign(world, appeal, "sup_agent")
    viewer = world.persona("viewer")
    view = _seen_by(world, appeal, "viewer", "appellant")
    assert view["appellant_user_id"] == viewer.user_id
    entries = _history(world, appeal, "viewer")
    assert [(e["event_type"], e["actor_user_id"], e["actor_role"]) for e in entries] == [
        ("review_appeal.filed", viewer.user_id, "appellant"),
        ("review_appeal.assigned", None, "reviewer"),
    ]
    _no_ids(json.dumps(view) + json.dumps(entries), world, "owner", "sup", "sup_agent")
    assert [item["id"] for item in _listed(viewer, MY_APPEALS)] == [appeal["id"]]
    _refused(viewer.get(APPEALS), 403, "permission_denied")
    assert _done(world, appeal, "withdraw", by="viewer")["status"] == "withdrawn"


# --- AC2 my reviews ---------------------------------------------------------------------


def test_my_reviews_lists_only_the_agents_completed_calls_with_a_human_result(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup", "agent", "lone")
    older, newer = _review(world), _review(world)
    now = datetime.now(UTC)
    _set_completed(older, now - timedelta(days=3))
    _set_completed(newer, now - timedelta(days=1))
    theirs = _review(world, agent="lone")
    open_item = _Case(world, agent="agent")
    unscored = _Case(world, agent="agent", scored=False)
    unscored.act("complete", by="sup")
    cancelled = _Case(world, agent="agent")
    cancelled.act("cancel", by="owner", reason="duplicate")
    deleted = _review(world)
    _set_meeting(deleted.meeting_id, deleted_at=now)

    listed = _my_reviews(world, "agent")
    assert [item["review_item_id"] for item in listed] == [newer.id, older.id]
    assert [item["review_item_id"] for item in _my_reviews(world, "lone")] == [theirs.id]
    assert _my_reviews(world, "owner") == []
    assert _my_reviews(world, "sup") == []

    for case in (open_item, unscored, cancelled, deleted, theirs):
        _refused(
            world.persona("agent").get(f"{MY_REVIEWS}/{case.id}"), 404, "review_item_not_found"
        )
    for name in ("owner", "sup"):
        _refused(world.persona(name).get(f"{MY_REVIEWS}/{newer.id}"), 404, "review_item_not_found")
    _refused(
        world.persona("agent").get(f"{MY_REVIEWS}/{uuid.uuid4()}"), 404, "review_item_not_found"
    )
    outsider = _outsider(client, email_outbox, "my-reviews")
    _refused(outsider.get(f"{MY_REVIEWS}/{newer.id}"), 404, "review_item_not_found")
    assert _listed(outsider, MY_REVIEWS) == []

    first = listed[0]
    human = _human(newer)
    assert first["meeting_id"] == newer.meeting_id
    assert _time(first["completed_at"]) == _completed_at(newer)
    assert _time(first["appeal_deadline"]) == _completed_at(newer) + WINDOW
    assert first["human"] == _block(human.result)
    assert first["final"] == first["human"]
    assert (first["final_source"], first["appeal"]) == ("human", None)
    with inspection_session_factory()() as db:
        machine = db.get(ScorecardResultRecord, newer.result_id)
        assert first["machine"] == _block(machine.result)


def test_my_reviews_pages_newest_first_and_bounds_the_limit(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "agent")
    cases = [_review(world) for _ in range(3)]
    now = datetime.now(UTC)
    for days, case in zip((5, 3, 1), cases, strict=True):
        _set_completed(case, now - timedelta(days=days))
    agent = world.persona("agent")
    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 1, **({"cursor": cursor} if cursor else {})}
        items, cursor = _page(agent.get(MY_REVIEWS, params=params))
        assert len(items) == 1, items
        seen.append(items[0]["review_item_id"])
        if cursor is None:
            break
    assert seen == [case.id for case in reversed(cases)]
    for limit in (0, 101):
        assert agent.get(MY_REVIEWS, params={"limit": limit}).status_code == 422, limit
    for limit in (1, 100):
        assert agent.get(MY_REVIEWS, params={"limit": limit}).status_code == 200, limit


def test_my_review_shows_each_criterion_and_never_a_reviewer_id(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup2", "agent")
    case = _review(world, assignee="sup", former="sup2")
    detail = _my_review(world, case)
    supervisor = case.view(by="owner")
    rows = {row.criterion_id: row for row in _criterion_rows(case.result_id)}
    human = _human_outcome(case)
    assert [c["criterion_id"] for c in detail["criteria"]] == [
        row.criterion_id for row in _criterion_rows(case.result_id)
    ]
    for entry in detail["criteria"]:
        row = rows[entry["criterion_id"]]
        assert {key: entry[key] for key in ("position", "weight", "auto_fail", "na_allowed")} == {
            "position": row.position,
            "weight": row.weight,
            "auto_fail": row.auto_fail,
            "na_allowed": row.na_allowed,
        }
        assert entry["reviewable"] is (row.reason != "not_applicable_by_conversation")
        assert entry["machine_outcome"] == row.outcome
        assert entry["human_outcome"] == human[row.criterion_id]
        assert entry["appeal"] is None
        [theirs] = [c for c in supervisor["criteria"] if c["criterion_id"] == row.criterion_id]
        if theirs["decision"] is None:
            assert entry["decision"] is None
        else:
            assert entry["decision"] == {
                key: theirs["decision"][key] for key in AGENT_DECISION_KEYS
            }
    assert {c["criterion_id"]: c["human_outcome"] for c in detail["criteria"]}[
        "greeting"
    ] == "failed"
    text = json.dumps(detail) + json.dumps(_my_reviews(world, "agent"))
    _no_ids(text, world, "owner", "sup", "sup2")


def test_my_reviews_follow_the_agent_and_a_deleted_call_disappears(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup", "agent", "lone")
    case = _review(world)
    assert [item["review_item_id"] for item in _my_reviews(world, "agent")] == [case.id]
    moved = world.owner.patch(
        f"/api/v1/meetings/{case.meeting_id}", json={"agent_user_id": world.user_id("lone")}
    )
    assert moved.status_code == 200, moved.text
    assert _my_reviews(world, "agent") == []
    _refused(world.persona("agent").get(f"{MY_REVIEWS}/{case.id}"), 404, "review_item_not_found")
    assert [item["review_item_id"] for item in _my_reviews(world, "lone")] == [case.id]
    assert _my_review(world, case, by="lone")["review_item_id"] == case.id

    _set_meeting(case.meeting_id, deleted_at=datetime.now(UTC))
    assert _my_reviews(world, "lone") == []
    _refused(world.persona("lone").get(f"{MY_REVIEWS}/{case.id}"), 404, "review_item_not_found")
    _refused(_file(world, case, by="lone"), 404, "review_item_not_found")


# --- AC3 filing -------------------------------------------------------------------------


def test_filing_stores_one_open_appeal_with_the_human_outcomes(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup_agent", "agent")
    case = _review(world)
    seen = _seen(world)
    view = _filed(world, case, ("greeting", "closing", "callback"))
    row = _appeal_row(view["id"])
    assert (row.status, row.revision, row.reason_note) == ("open", 1, NOTE)
    assert row.appellant_user_id == world.user_id("agent")
    assert row.original_reviewer_user_id == world.user_id("sup")
    assert (row.review_item_id, row.scorecard_result_id, row.meeting_id) == (
        case.id,
        case.result_id,
        case.meeting_id,
    )
    for name in ("assignee_user_id", "disposition", "decided_at", "withdrawn_at"):
        assert getattr(row, name) is None, name
    human = _human_outcome(case)
    criteria = _criteria_rows(view["id"])
    assert set(criteria) == {"greeting", "closing", "callback"}
    for criterion_id, criterion in criteria.items():
        assert criterion.appealed_outcome == human[criterion_id], criterion_id
        assert criterion.decision is None
    assert {c["criterion_id"]: c["appealed_outcome"] for c in view["criteria"]} == {
        "greeting": "failed",
        "closing": "passed",
        "callback": "failed",
    }
    assert view["appellant_user_id"] == world.user_id("agent")
    assert (view["reason_note"], view["human"]) == (NOTE, _block(_human(case).result))
    assert (view["appeal_result"], view["final"]) == (None, view["human"])
    [event] = _since(world, seen)
    assert (event.event_type, event.target_type, event.target_id, event.actor_user_id) == (
        "review_appeal.filed",
        "review_appeal",
        view["id"],
        world.user_id("agent"),
    )
    summary = _my_review(world, case)
    assert summary["appeal"] == {"id": view["id"], "status": "open", "disposition": None}
    assert [item["id"] for item in _listed(world.persona("agent"), MY_APPEALS)] == [view["id"]]


def test_filing_refusals_follow_the_designed_order_and_write_nothing(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup", "agent", "lone")
    case = _review(world)
    in_review = _Case(world, agent="agent")
    unscored = _Case(world, agent="agent", scored=False)
    unscored.act("complete", by="sup")
    before = _state(world)
    bad = {"criteria": ("agenda", "agenda"), "note": " "}

    def refused(
        target: _Case, status: int, code: str, *, by="agent", criteria=("greeting",), note=NOTE
    ):
        response = _file(world, target, criteria, by=by, note=note)
        _refused(response, status, code)
        assert _state(world) == before, code
        return response

    for name in ("owner", "sup", "lone"):
        refused(case, 404, "review_item_not_found", by=name)
        refused(case, 404, "review_item_not_found", by=name, **bad)
    missing = _Case.__new__(_Case)
    missing.item = {"id": str(uuid.uuid4())}
    refused(missing, 404, "review_item_not_found")
    for target in (in_review, unscored):
        refused(target, 409, "review_item_not_appealable")
        refused(target, 409, "review_item_not_appealable", **bad)

    refused(case, 422, "appeal_criteria_invalid", criteria=())
    refused(case, 422, "appeal_criteria_invalid", criteria=(), note="")
    refused(case, 422, "appeal_criteria_invalid", criteria=("greeting", "greeting"))
    for unknown in ("no_such_criterion", "Greeting", "greeting ", "agenda"):
        response = refused(case, 422, "appeal_criterion_invalid", criteria=(unknown,))
        assert response.json()["error"]["details"]["criterion_id"] == unknown
        refused(case, 422, "appeal_criterion_invalid", criteria=("greeting", unknown), note="")
    for note in ("", " ", "   ", "x" * 2001, "ن" * 2001):
        refused(case, 422, "appeal_reason_required", note=note)

    _filed(world, case, ["greeting"], note="x")


def test_the_window_closes_fourteen_days_after_completion(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "agent")
    case = _review(world)
    _set_completed(case, datetime.now(UTC) - WINDOW)
    before = _state(world)
    _refused(_file(world, case), 409, "appeal_window_closed")
    _refused(_file(world, case, ("agenda",), note=""), 409, "appeal_window_closed")
    _set_completed(case, datetime.now(UTC) - WINDOW - timedelta(days=30))
    _refused(_file(world, case), 409, "appeal_window_closed")
    assert _state(world) == before
    summary = _my_reviews(world, "agent")[0]
    assert _time(summary["appeal_deadline"]) == _completed_at(case) + WINDOW

    _set_completed(case, datetime.now(UTC) - WINDOW + timedelta(seconds=30))
    _filed(world, case, note="x" * 2000)


def test_one_to_twenty_criteria_are_taken_and_twenty_one_refused(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "agent")
    wide, other = _wide(world), _wide(world)
    names = [f"w{index:02d}" for index in range(21)]
    before = _state(world)
    _refused(_file(world, wide, names), 422, "appeal_criteria_invalid")
    _refused(_file(world, wide, names[:20] + names[:1]), 422, "appeal_criteria_invalid")
    assert _state(world) == before

    twenty = _filed(world, wide, names[:20], note="ن" * 2000)
    assert set(_criteria_rows(twenty["id"])) == set(names[:20])
    one = _filed(world, other, names[20:])
    assert set(_criteria_rows(one["id"])) == {"w20"}


def test_one_appeal_per_item_also_after_a_withdrawal(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "agent")
    case, other = _review(world), _review(world)
    appeal = _filed(world, case)
    before = _state(world)
    _refused(_file(world, case, ("callback",)), 409, "appeal_exists")
    _refused(_file(world, case, ("agenda",), note=""), 409, "appeal_exists")
    assert _state(world) == before
    _done(world, appeal, "withdraw", by="agent")
    before = _state(world)
    _refused(_file(world, case), 409, "appeal_exists")
    assert _state(world) == before

    _filed(world, other)


# --- AC4 supervisor visibility ----------------------------------------------------------


def test_only_owners_admins_and_independent_supervisors_see_an_appeal(
    client, email_outbox, monkeypatch
):
    world = _World(
        client,
        email_outbox,
        monkeypatch,
        "admin",
        "sup",
        "sup2",
        "sup_agent",
        "sup_b",
        "lone",
        "agent",
    )
    case = _review(world, assignee="sup", former="sup2")
    appeal = _filed(world, case)
    for name in ("owner", "admin", "sup_agent"):
        assert appeal["id"] in {item["id"] for item in _listed(world.persona(name), APPEALS)}, name
        view = _seen_by(world, appeal, name)
        assert view["appellant_user_id"] == world.user_id("agent")
        assert view["original_reviewer_user_id"] == world.user_id("sup")
        assert view["team_id"] == world.teams["a"]
        assert _history(world, appeal, name)
    for name in ("sup", "sup2", "sup_b", "lone"):
        _hidden(world, appeal, name)

    agent = world.persona("agent")
    before = _state(world)
    assert appeal["id"] not in {item["id"] for item in _listed(agent, APPEALS)}
    own = _seen_by(world, appeal, "agent", "appellant")
    assert own["appellant_user_id"] == world.user_id("agent")
    _refused(
        _act(world, appeal, "assign", by="agent", assignee_user_id=world.user_id("sup_agent")),
        404,
        "review_appeal_not_found",
    )
    _refused(
        _act(world, appeal, "decide", by="agent", **_body([_uphold("greeting")])),
        404,
        "review_appeal_not_found",
    )
    assert _state(world) == before

    _assign(world, appeal, "sup_agent")


def test_an_admin_who_completed_the_review_does_not_see_the_appeal(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup_agent", "agent")
    case = _review(world, assignee="admin")
    appeal = _filed(world, case)
    _hidden(world, appeal, "admin")
    _seen_by(world, appeal, "owner")
    _seen_by(world, appeal, "sup_agent")
    _refused(
        _act(world, appeal, "assign", by="owner", assignee_user_id=world.user_id("admin")),
        422,
        "appeal_assignee_is_original_reviewer",
    )
    _assign(world, appeal, "sup_agent")


def test_the_appeal_follows_the_calls_current_team(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup_agent", "sup_b", "agent")
    case = _review(world)
    appeal = _filed(world, case)
    _seen_by(world, appeal, "sup_agent")
    _hidden(world, appeal, "sup_b")
    _set_meeting(case.meeting_id, team_id=world.teams["b"])
    _hidden(world, appeal, "sup_agent")
    view = _seen_by(world, appeal, "sup_b")
    assert view["team_id"] == world.teams["b"]


def test_the_supervisor_list_filters_and_pages_in_created_order(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup_agent", "agent")
    first, second, third = (_filed(world, _review(world)) for _ in range(3))
    _assign(world, first, "sup_agent")
    _done(world, second, "withdraw", by="agent")
    order = [first["id"], second["id"], third["id"]]
    owner = world.owner

    assert [item["id"] for item in _listed(owner, APPEALS)] == order
    assert [item["id"] for item in _listed(owner, APPEALS, status="open")] == [
        first["id"],
        third["id"],
    ]
    assert [item["id"] for item in _listed(owner, APPEALS, status="withdrawn")] == [second["id"]]
    assert _listed(owner, APPEALS, status="decided") == []
    assert [
        item["id"] for item in _listed(owner, APPEALS, assignee_user_id=world.user_id("sup_agent"))
    ] == [first["id"]]
    for item in _listed(owner, APPEALS):
        _check_appeal(item, "supervisor")

    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 1, **({"cursor": cursor} if cursor else {})}
        items, cursor = _page(owner.get(APPEALS, params=params))
        assert len(items) == 1, items
        seen.append(items[0]["id"])
        if cursor is None:
            break
    assert seen == order
    mine = _listed(world.persona("agent"), MY_APPEALS)
    for item in mine:
        _check_appeal(item, "appellant")
    assert [item["id"] for item in mine] == list(reversed(order))
    _no_ids(json.dumps(mine), world, "owner", "sup", "sup_agent")


# --- AC5 assign -------------------------------------------------------------------------


def test_assign_refuses_each_party_and_original_reviewer_and_takes_an_eligible_person(
    client, email_outbox, monkeypatch
):
    world = _World(
        client,
        email_outbox,
        monkeypatch,
        "admin",
        "sup",
        "sup2",
        "sup_agent",
        "sup_b",
        "lone",
        "viewer",
        "agent",
    )
    case = _review(world, assignee="sup", former="sup2")
    appeal = _filed(world, case)
    before = _state(world)
    for name, code in (
        ("agent", "appeal_assignee_is_party"),
        ("sup", "appeal_assignee_is_original_reviewer"),
        ("sup2", "appeal_assignee_is_original_reviewer"),
        ("sup_b", "review_assignee_ineligible"),
        ("lone", "review_assignee_ineligible"),
        ("viewer", "review_assignee_ineligible"),
    ):
        for by in ("owner", "admin", "sup_agent"):
            _refused(
                _act(world, appeal, "assign", by=by, assignee_user_id=world.user_id(name)),
                422,
                code,
            )
    _refused(
        _act(world, appeal, "assign", by="owner", assignee_user_id=str(uuid.uuid4())),
        422,
        "review_assignee_ineligible",
    )
    assert _state(world) == before

    appeal = _assign(world, appeal, "sup_agent")
    appeal = _assign(world, appeal, "admin", by="sup_agent")
    appeal = _assign(world, appeal, "owner", by="admin")
    row = _appeal_row(appeal["id"])
    assert (row.assignee_user_id, row.assigned_by_user_id, row.revision) == (
        world.owner.user_id,
        world.user_id("admin"),
        4,
    )
    assert row.assigned_at is not None


def test_assign_checks_run_in_the_designed_order(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup_agent", "sup_b", "agent")
    case, closed_case = _review(world), _review(world)
    appeal = _filed(world, case)
    closed = _filed(world, closed_case)
    closed = _done(world, closed, "withdraw", by="agent")
    before = _state(world)
    party = {"assignee_user_id": world.user_id("agent")}
    for wrong in (2, 7):
        response = _act(world, appeal, "assign", by="owner", revision=wrong, **party)
        _refused(response, 409, "review_appeal_revision_conflict")
        assert response.json()["error"]["details"] == {"current_revision": 1}
    _refused(
        _act(world, appeal, "assign", by="sup_b", revision=9, **party),
        404,
        "review_appeal_not_found",
    )
    _refused(_act(world, closed, "assign", by="owner", **party), 409, "review_appeal_closed")
    _refused(
        _act(world, closed, "assign", by="owner", assignee_user_id=world.user_id("sup_agent")),
        409,
        "review_appeal_closed",
    )
    _refused(
        _act(world, closed, "assign", by="owner", revision=1, **party),
        409,
        "review_appeal_revision_conflict",
    )
    assert _state(world) == before

    _assign(world, appeal, "sup_agent")


def test_the_same_assignee_again_writes_nothing(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup_agent", "agent")
    appeal = _assign(world, _filed(world, _review(world)), "sup_agent")
    before = _state(world)
    again = _done(world, appeal, "assign", by="owner", assignee_user_id=world.user_id("sup_agent"))
    assert again["revision"] == appeal["revision"]
    assert again["assignee_user_id"] == world.user_id("sup_agent")
    assert _state(world) == before
    assert _recipients(world, appeal["id"], "review_appeal.assigned") == [
        world.user_id("sup_agent")
    ]


def test_an_owner_or_admin_who_is_a_party_cannot_assign(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup_agent", "agent")
    case, own = _review(world), _review(world, agent="admin")
    appeal = _filed(world, case)
    _move(world, case, "admin")
    before = _state(world)
    for revision in (1, 9):
        _refused(
            _act(
                world,
                appeal,
                "assign",
                by="admin",
                revision=revision,
                assignee_user_id=world.user_id("sup_agent"),
            ),
            422,
            "appeal_assigner_is_party",
        )
    assert _state(world) == before
    _seen_by(world, appeal, "admin")
    _assign(world, appeal, "sup_agent")

    filed = _filed(world, own, by="admin")
    _move(world, own, "agent")
    before = _state(world)
    response = _act(world, filed, "assign", by="admin", assignee_user_id=world.user_id("sup_agent"))
    assert (response.status_code, response.json()["error"]["code"]) in (
        (404, "review_appeal_not_found"),
        (422, "appeal_assigner_is_party"),
    ), response.text
    assert _state(world) == before
    _assign(world, filed, "sup_agent")


# --- AC6 decide -------------------------------------------------------------------------


def test_decide_refusals_follow_the_designed_order_and_write_nothing(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup_agent", "sup_b", "agent")
    case, closed_case = _review(world), _review(world)
    appeal = _assign(
        world, _filed(world, case, ("greeting", "fee_explained", "closing")), "sup_agent"
    )
    closed = _assign(world, _filed(world, closed_case), "sup_agent")
    closed = _done(world, closed, "withdraw", by="agent")
    frozen = _frozen(case)
    good = [_change("greeting", "passed"), _uphold("fee_explained"), _uphold("closing")]
    before = _state(world)

    def refused(status: int, code: str, body: dict, *, by="sup_agent", target=None, revision=None):
        response = _act(world, target or appeal, "decide", by=by, revision=revision, **body)
        _refused(response, status, code)
        assert _state(world) == before, code
        assert _frozen(case) == frozen, code
        return response

    def malformed(body: dict) -> None:
        response = _act(world, appeal, "decide", by="sup_agent", **body)
        assert response.status_code == 422, (body, response.text)
        assert _state(world) == before, body

    for name in ("sup", "sup_b", "agent"):
        refused(404, "review_appeal_not_found", _body(good), by=name)
    stale = refused(409, "review_appeal_revision_conflict", _body(good), revision=1)
    assert stale.json()["error"]["details"] == {"current_revision": appeal["revision"]}
    refused(404, "review_appeal_not_found", _body([]), by="sup_b", revision=1)
    for name in ("owner", "admin"):
        refused(403, "review_appeal_not_assignee", _body(good), by=name)
        refused(403, "review_appeal_not_assignee", _body([], note=""), by=name)
    refused(409, "review_appeal_closed", _body(good), target=closed)
    refused(409, "review_appeal_closed", _body([]), by="owner", target=closed)

    for decisions in (
        [],
        good[:2],
        [*good, _uphold("callback")],
        [*good, _uphold("greeting")],
        [_uphold("no_such_criterion"), *good[1:]],
        [_uphold("Greeting"), *good[1:]],
    ):
        refused(422, "appeal_decisions_incomplete", _body(decisions))
    refused(422, "appeal_decisions_incomplete", _body(good[:2], note=""))
    refused(422, "appeal_change_matches", _body([_change("greeting", "failed"), *good[1:]]))
    refused(
        422, "appeal_change_matches", _body([_change("greeting", "failed"), *good[1:]], note="")
    )
    refused(
        422,
        "reviewer_na_not_allowed",
        _body([_change("greeting", "not_applicable"), *good[1:]]),
    )
    refused(
        422,
        "reviewer_na_not_allowed",
        _body([_change("greeting", "not_applicable", reason_code=None), *good[1:]]),
    )
    for extra in (
        {"reason_code": None},
        {"reason_code": "Other"},
        {"reason_code": "evidence-missed"},
        {"reason_code": ""},
        {"note": None},
        {"note": ""},
        {"note": "   "},
    ):
        refused(
            422,
            "override_reason_required",
            _body([_change("greeting", "passed", **extra), *good[1:]]),
        )
    refused(
        422,
        "override_reason_required",
        _body([_change("greeting", "passed", reason_code=None), *good[1:]], note=""),
    )
    for note in ("", " ", "   ", "x" * 2001, "ن" * 2001, None):
        refused(422, "appeal_disposition_note_required", _body(good, note=note))

    for decisions in (
        [_uphold("greeting", decision="accept"), *good[1:]],
        [{"criterion_id": "greeting", "decision": "Uphold"}, *good[1:]],
        [{"criterion_id": "greeting", "decision": "override"}, *good[1:]],
        [_change("greeting", "insufficient_evidence"), *good[1:]],
        [_change("greeting", "Passed"), *good[1:]],
        [_change("greeting", "passed", note="x" * 2001), *good[1:]],
        [_change("greeting", "passed", note="ن" * 2001), *good[1:]],
        [good[0], _uphold("fee_explained", note="x" * 2001), good[2]],
    ):
        refused(422, "appeal_decision_invalid", _body(decisions))
    malformed({**_body(good), "unexpected": True})

    _tamper(RESULTS, RESULTS.c.id == case.result_id, scorecards_version="scorecards/0")
    frozen = _frozen(case)
    refused(409, "scorecard_version_unsupported", _body(good))
    _tamper(RESULTS, RESULTS.c.id == case.result_id, scorecards_version=SCORECARDS_VERSION)
    frozen = _frozen(case)

    with inspection_session_factory()() as db:
        db.execute(
            update(ReviewAppeal)
            .where(ReviewAppeal.id == appeal["id"])
            .values(assignee_user_id=world.user_id("sup"))
        )
        db.commit()
    before = _state(world)
    refused(404, "review_appeal_not_found", _body(good), by="sup")
    with inspection_session_factory()() as db:
        db.execute(
            update(ReviewAppeal)
            .where(ReviewAppeal.id == appeal["id"])
            .values(assignee_user_id=world.user_id("sup_agent"))
        )
        db.commit()

    decided = _done(world, appeal, "decide", by="sup_agent", **_body(good))
    assert (decided["status"], decided["revision"]) == ("decided", appeal["revision"] + 1)


SCENARIOS = {
    "upheld": (
        ("greeting", "fee_explained", "callback"),
        [
            _uphold("greeting", note="The reviewer heard it right."),
            _uphold("fee_explained"),
            _uphold("callback"),
        ],
        {},
    ),
    "granted": (
        ("greeting", "fee_explained"),
        [_change("greeting", "passed"), _change("fee_explained", "passed", reason_code="other")],
        {"greeting": "passed", "fee_explained": "passed"},
    ),
    "partially_granted": (
        ("greeting", "fee_explained", "closing", "callback"),
        [
            _uphold("greeting"),
            _change("fee_explained", "passed"),
            _change("closing", "not_applicable", reason_code="context_outside_call"),
            _uphold("callback", note="No call back was offered."),
        ],
        {"fee_explained": "passed", "closing": "not_applicable"},
    ),
}


@pytest.mark.parametrize("disposition", SCENARIOS)
def test_the_disposition_and_the_appeal_result_follow_the_criterion_decisions(
    client, email_outbox, monkeypatch, disposition
):
    criteria, decisions, changes = SCENARIOS[disposition]
    world = _World(client, email_outbox, monkeypatch, "sup", "sup_agent", "agent")
    case = _review(world)
    appeal = _assign(world, _filed(world, case, criteria), "sup_agent")
    frozen = _frozen(case)
    human = _human(case)
    assert human.reviewer_outcomes == HUMAN_OUTCOMES
    seen = _seen(world)

    view = _done(world, appeal, "decide", by="sup_agent", **_body(decisions))
    assert (view["status"], view["disposition"], view["disposition_note"]) == (
        "decided",
        disposition,
        DISPOSITION,
    )
    assert view["decided_by_user_id"] == world.user_id("sup_agent")
    assert view["revision"] == appeal["revision"] + 1
    _time(view["decided_at"])
    row = _appeal_row(appeal["id"])
    assert (row.status, row.disposition, row.revision) == ("decided", disposition, view["revision"])
    assert row.decided_by_user_id == world.user_id("sup_agent")
    assert row.decided_at is not None and row.withdrawn_at is None

    stored = _criteria_rows(appeal["id"])
    for decision in decisions:
        criterion = stored[decision["criterion_id"]]
        assert criterion.decision == decision["decision"]
        assert criterion.decided_outcome == decision.get("decided_outcome")
        assert criterion.reason_code == decision.get("reason_code")
        assert criterion.note == decision.get("note")
        assert criterion.appealed_outcome == APPEALED[decision["criterion_id"]]
    assert {c["criterion_id"]: c["decision"] for c in view["criteria"]} == {
        d["criterion_id"]: d["decision"] for d in decisions
    }

    results = _results(appeal["id"])
    if not changes:
        assert results == []
        assert view["appeal_result"] is None
        assert view["final"] == view["human"] == _block(human.result)
    else:
        outcomes = {**human.reviewer_outcomes, **changes}
        expected = _engine(case.result_id, outcomes)
        [result] = results
        assert result.reviewer_outcomes == outcomes
        assert result.result == expected
        assert result.scorecards_version == expected["scorecards_version"]
        for column in (
            "outcome",
            "reason",
            "complete",
            "pass_threshold",
            "applicable_weight",
            "earned_weight",
            "failed_weight",
            "insufficient_weight",
        ):
            assert getattr(result, column) == expected[column], column
        assert (result.workspace_id, result.meeting_id, result.review_item_id) == (
            world.workspace_id,
            case.meeting_id,
            case.id,
        )
        assert result.scorecard_result_id == case.result_id
        assert view["appeal_result"] == _block(expected)
        assert view["final"] == view["appeal_result"]
    assert _frozen(case) == frozen

    [event] = _since(world, seen)
    assert (event.event_type, event.target_type, event.target_id, event.actor_user_id) == (
        "review_appeal.decided",
        "review_appeal",
        appeal["id"],
        world.user_id("sup_agent"),
    )
    assert disposition in json.dumps(event.details)
    if changes:
        assert results[0].id in json.dumps(event.details)
    assert _recipients(world, appeal["id"], "review_appeal.decided") == [world.user_id("agent")]

    summary = _my_review(world, case)
    assert summary["appeal"] == {
        "id": appeal["id"],
        "status": "decided",
        "disposition": disposition,
    }
    assert summary["final"] == view["final"]
    assert summary["final_source"] == ("appeal" if changes else "human")
    assert summary["human"] == _block(human.result)
    entries = {entry["criterion_id"]: entry for entry in summary["criteria"]}
    for decision in decisions:
        assert entries[decision["criterion_id"]]["appeal"] == {
            "decision": decision["decision"],
            "decided_outcome": decision.get("decided_outcome"),
            "reason_code": decision.get("reason_code"),
            "note": decision.get("note"),
        }
    for criterion_id in set(entries) - set(criteria):
        assert entries[criterion_id]["appeal"] is None
    [listed] = [item for item in _my_reviews(world, "agent") if item["review_item_id"] == case.id]
    assert (listed["final"], listed["final_source"]) == (summary["final"], summary["final_source"])
    appellant = _seen_by(world, appeal, "agent", "appellant")
    assert (appellant["final"], appellant["appeal_result"]) == (
        view["final"],
        view["appeal_result"],
    )
    _no_ids(json.dumps(summary) + json.dumps(appellant), world, "owner", "sup", "sup_agent")


def test_a_decided_appeal_is_closed_to_every_later_write(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup_agent", "agent")
    appeal = _assign(world, _filed(world, _review(world)), "sup_agent")
    decided = _done(
        world,
        appeal,
        "decide",
        by="sup_agent",
        **_body([_uphold("greeting"), _uphold("fee_explained")]),
    )
    before = _state(world)
    _refused(
        _act(
            world,
            decided,
            "decide",
            by="sup_agent",
            **_body([_uphold("greeting"), _uphold("fee_explained")]),
        ),
        409,
        "review_appeal_closed",
    )
    _refused(
        _act(world, decided, "assign", by="owner", assignee_user_id=world.owner.user_id),
        409,
        "review_appeal_closed",
    )
    _refused(_act(world, decided, "withdraw", by="agent"), 409, "review_appeal_closed")
    assert _state(world) == before


# --- AC7 withdraw -----------------------------------------------------------------------


def test_only_the_appellant_withdraws_an_open_appeal_with_its_revision(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup_agent", "sup_b", "agent")
    appeal = _assign(world, _filed(world, _review(world)), "sup_agent")
    before = _state(world)
    for name in ("owner", "admin", "sup_agent"):
        _refused(_act(world, appeal, "withdraw", by=name), 403, "appeal_not_appellant")
    for name in ("sup", "sup_b"):
        _refused(_act(world, appeal, "withdraw", by=name), 404, "review_appeal_not_found")
    _refused(
        _act(world, {"id": str(uuid.uuid4()), "revision": 1}, "withdraw", by="agent"),
        404,
        "review_appeal_not_found",
    )
    stale = _act(world, appeal, "withdraw", by="agent", revision=1)
    _refused(stale, 409, "review_appeal_revision_conflict")
    assert stale.json()["error"]["details"] == {"current_revision": appeal["revision"]}
    assert _state(world) == before

    seen = _seen(world)
    view = _done(world, appeal, "withdraw", by="agent")
    assert (view["status"], view["revision"]) == ("withdrawn", appeal["revision"] + 1)
    _time(view["withdrawn_at"])
    row = _appeal_row(appeal["id"])
    assert (row.status, row.revision) == ("withdrawn", view["revision"])
    assert row.withdrawn_at is not None and row.decided_at is None
    [event] = _since(world, seen)
    assert (event.event_type, event.target_type, event.target_id, event.actor_user_id) == (
        "review_appeal.withdrawn",
        "review_appeal",
        appeal["id"],
        world.user_id("agent"),
    )
    assert _recipients(world, appeal["id"], "review_appeal.withdrawn") == [
        world.user_id("sup_agent")
    ]
    before = _state(world)
    _refused(_act(world, view, "withdraw", by="agent"), 409, "review_appeal_closed")
    assert _state(world) == before


def test_withdrawing_an_unassigned_appeal_notifies_nobody(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "agent")
    appeal = _filed(world, _review(world))
    _done(world, appeal, "withdraw", by="agent")
    assert _recipients(world, appeal["id"], "review_appeal.withdrawn") == []


# --- AC8 attribution --------------------------------------------------------------------


def _move(world: _World, case: _Case, name: str) -> None:
    moved = world.owner.patch(
        f"/api/v1/meetings/{case.meeting_id}", json={"agent_user_id": world.user_id(name)}
    )
    assert moved.status_code == 200, moved.text


def test_a_new_agent_can_never_assign_or_decide_and_the_old_appellant_can_still_withdraw(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup_agent", "agent", "lone")
    case, other = _review(world), _review(world)
    appeal = _filed(world, case)
    _move(world, case, "sup_agent")

    assert case.id not in {item["review_item_id"] for item in _my_reviews(world, "agent")}
    assert case.id in {item["review_item_id"] for item in _my_reviews(world, "sup_agent")}
    assert _my_review(world, case, by="sup_agent")["appeal"]["id"] == appeal["id"]
    before = _state(world)
    _refused(_file(world, case, by="sup_agent"), 409, "appeal_exists")
    for name in ("sup_agent", "agent"):
        _refused(
            _act(world, appeal, "assign", by="owner", assignee_user_id=world.user_id(name)),
            422,
            "appeal_assignee_is_party",
        )
    _refused(_get(world, appeal, "sup_agent"), 404, "review_appeal_not_found")
    assert _state(world) == before
    _seen_by(world, appeal, "agent", "appellant")

    appeal = _assign(world, appeal, "admin")
    _move(world, case, "admin")
    before = _state(world)
    body = _body([_uphold("greeting"), _uphold("fee_explained")])
    _refused(_act(world, appeal, "decide", by="admin", **body), 422, "appeal_decider_is_party")
    assert _state(world) == before
    _move(world, case, "lone")
    decided = _done(world, appeal, "decide", by="admin", **body)
    assert decided["status"] == "decided"

    later = _filed(world, other)
    _move(world, other, "lone")
    view = _done(world, later, "withdraw", by="agent")
    assert view["status"] == "withdrawn"
    assert other.id in {item["review_item_id"] for item in _my_reviews(world, "lone")}


# --- AC10 audit history -----------------------------------------------------------------


def test_each_change_writes_one_event_and_no_audit_row_holds_a_note(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup_agent", "agent")
    case, other = _review(world), _review(world)
    tokens = {
        name: f"{name}-{uuid.uuid4().hex}" for name in ("reason", "disposition", "change", "uphold")
    }

    seen = _seen(world)
    appeal = _filed(world, case, note=f"My reason {tokens['reason']}")
    [filed] = _since(world, seen)
    assert filed.event_type == "review_appeal.filed"
    text = json.dumps(filed.details)
    for value in (case.id, case.result_id, "greeting", "fee_explained", world.user_id("agent")):
        assert value in text, value

    seen = _seen(world)
    appeal = _assign(world, appeal, "admin")
    appeal = _assign(world, appeal, "sup_agent")
    events = _since(world, seen)
    assert [e.event_type for e in events] == ["review_appeal.assigned"] * 2
    assert all(e.target_id == appeal["id"] and e.target_type == "review_appeal" for e in events)
    assert world.user_id("admin") in json.dumps(events[1].details)
    assert world.user_id("sup_agent") in json.dumps(events[1].details)

    seen = _seen(world)
    _done(world, appeal, "assign", by="owner", assignee_user_id=world.user_id("sup_agent"))
    _refused(
        _act(world, appeal, "assign", by="owner", assignee_user_id=world.user_id("sup")),
        422,
        "appeal_assignee_is_original_reviewer",
    )
    assert _since(world, seen) == []

    seen = _seen(world)
    _done(
        world,
        appeal,
        "decide",
        by="sup_agent",
        **_body(
            [
                _change("greeting", "passed", note=f"Changed {tokens['change']}"),
                _uphold("fee_explained", note=f"Kept {tokens['uphold']}"),
            ],
            note=f"Decided {tokens['disposition']}",
        ),
    )
    [decided] = _since(world, seen)
    assert (decided.event_type, decided.actor_user_id) == (
        "review_appeal.decided",
        world.user_id("sup_agent"),
    )

    withdrawn = _filed(world, other)
    seen = _seen(world)
    _done(world, withdrawn, "withdraw", by="agent")
    assert [e.event_type for e in _since(world, seen)] == ["review_appeal.withdrawn"]

    with inspection_session_factory()() as db:
        rows = list(db.scalars(select(AuditEvent)))
        dump = "\n".join(
            json.dumps(
                {c.key: getattr(row, c.key) for c in AuditEvent.__mapper__.column_attrs},
                default=str,
                ensure_ascii=False,
            )
            for row in rows
        )
    for token in tokens.values():
        assert token not in dump, token


def test_the_history_is_oldest_first_and_hides_every_reviewer_from_the_appellant(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup_agent", "sup_b", "agent")
    case, other = _review(world), _review(world)
    appeal = _filed(world, case)
    appeal = _assign(world, appeal, "admin")
    appeal = _assign(world, appeal, "sup_agent", by="admin")
    _done(
        world,
        appeal,
        "decide",
        by="sup_agent",
        **_body([_change("greeting", "passed"), _uphold("fee_explained")]),
    )
    kinds = [
        "review_appeal.filed",
        "review_appeal.assigned",
        "review_appeal.assigned",
        "review_appeal.decided",
    ]
    for name in ("owner", "admin"):
        entries = _history(world, appeal, name)
        assert [e["event_type"] for e in entries] == kinds, name
        assert [e["actor_user_id"] for e in entries] == [
            world.user_id("agent"),
            world.owner.user_id,
            world.user_id("admin"),
            world.user_id("sup_agent"),
        ]
        assert [e["actor_role"] for e in entries] == [
            "appellant",
            "reviewer",
            "reviewer",
            "reviewer",
        ]
        times = [_time(e["occurred_at"]) for e in entries]
        assert times == sorted(times)

    own = _history(world, appeal, "agent")
    assert [e["event_type"] for e in own] == kinds
    assert [e["actor_user_id"] for e in own] == [world.user_id("agent"), None, None, None]
    assert [e["actor_role"] for e in own] == ["appellant", "reviewer", "reviewer", "reviewer"]
    _no_ids(
        json.dumps([e["details"] for e in own]),
        world,
        "owner",
        "admin",
        "sup",
        "sup_agent",
        "agent",
    )
    for name in ("sup", "sup_b"):
        _refused(
            world.persona(name).get(f"{APPEALS}/{appeal['id']}/history"),
            404,
            "review_appeal_not_found",
        )

    later = _assign(world, _filed(world, other), "admin")
    _done(world, later, "withdraw", by="agent")
    own = _history(world, later, "agent")
    assert [(e["event_type"], e["actor_user_id"], e["actor_role"]) for e in own] == [
        ("review_appeal.filed", world.user_id("agent"), "appellant"),
        ("review_appeal.assigned", None, "reviewer"),
        ("review_appeal.withdrawn", world.user_id("agent"), "appellant"),
    ]
    _no_ids(json.dumps([e["details"] for e in own]), world, "owner", "admin", "agent")


# --- AC11 notifications -----------------------------------------------------------------


def _check_rows(world: _World, appeal: dict, case: _Case, kind: str) -> list[str]:
    rows = _notes(world, appeal_id=appeal["id"], kind=kind)
    for row in rows:
        assert (row.title, row.body) == TEXTS[kind], kind
        assert row.data == {
            "appeal_id": appeal["id"],
            "review_item_id": case.id,
            "meeting_id": case.meeting_id,
        }
        assert row.read_at is None
    return [row.user_id for row in rows]


def test_each_kind_reaches_only_its_designed_recipients_once(client, email_outbox, monkeypatch):
    world = _World(
        client,
        email_outbox,
        monkeypatch,
        "admin",
        "sup",
        "sup2",
        "sup_agent",
        "sup_b",
        "lone",
        "viewer",
        "agent",
    )
    case = _review(world, assignee="sup", former="sup2")
    other = _review(world)
    appeal = _filed(world, case)
    assert _check_rows(world, appeal, case, "review_appeal.filed") == sorted(
        [world.owner.user_id, world.user_id("admin"), world.user_id("sup_agent")]
    )

    appeal = _assign(world, appeal, "sup_agent")
    assert _check_rows(world, appeal, case, "review_appeal.assigned") == [
        world.user_id("sup_agent")
    ]
    appeal = _assign(world, appeal, "admin")
    appeal = _assign(world, appeal, "sup_agent")
    assert _check_rows(world, appeal, case, "review_appeal.assigned") == sorted(
        [world.user_id("admin"), world.user_id("sup_agent")]
    )
    _done(
        world,
        appeal,
        "decide",
        by="sup_agent",
        **_body([_change("greeting", "passed"), _uphold("fee_explained")]),
    )
    assert _check_rows(world, appeal, case, "review_appeal.decided") == [world.user_id("agent")]
    assert _check_rows(world, appeal, case, "review_appeal.withdrawn") == []
    assert _check_rows(world, appeal, case, "review_appeal.filed") == sorted(
        [world.owner.user_id, world.user_id("admin"), world.user_id("sup_agent")]
    )

    later = _assign(world, _filed(world, other), "admin")
    _done(world, later, "withdraw", by="agent")
    assert _check_rows(world, later, other, "review_appeal.withdrawn") == [world.user_id("admin")]
    assert _check_rows(world, later, other, "review_appeal.filed") == sorted(
        [
            world.owner.user_id,
            world.user_id("admin"),
            world.user_id("sup_agent"),
            world.user_id("sup2"),
        ]
    )

    inbox = _inbox(world.persona("sup_agent"))
    assert {(n["kind"], n["data"]["appeal_id"]) for n in inbox} == {
        ("review_appeal.filed", appeal["id"]),
        ("review_appeal.assigned", appeal["id"]),
        ("review_appeal.filed", later["id"]),
    }
    for row in inbox:
        assert (row["title"], row["body"]) == TEXTS[row["kind"]]
        assert set(row["data"]) == {"appeal_id", "review_item_id", "meeting_id"}


def test_the_filed_notification_reaches_at_most_fifty_team_supervisors_first(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup_agent", "agent")
    case = _review(world)
    admins = []
    with inspection_session_factory()() as db:
        for index in range(52):
            user = User(email=f"appeal-admin-{index:02d}-{uuid.uuid4().hex[:6]}@example.com")
            db.add(user)
            db.flush()
            db.add(
                Membership(
                    workspace_id=world.workspace_id,
                    user_id=user.id,
                    role=WorkspaceRole.ADMIN,
                    valid_from=datetime.now(UTC) - timedelta(hours=1),
                )
            )
            admins.append(user.id)
        db.commit()
    appeal = _filed(world, case)
    chosen = [world.user_id("sup_agent"), *sorted([world.owner.user_id, *admins])[:49]]
    assert _check_rows(world, appeal, case, "review_appeal.filed") == sorted(chosen)


def test_no_decided_notification_reaches_a_removed_appellant(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup_agent", "agent")
    appeal = _assign(world, _filed(world, _review(world)), "sup_agent")
    with inspection_session_factory()() as db:
        db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.user_id == world.user_id("agent")
            )
        )
        db.execute(delete(User).where(User.id == world.user_id("agent")))
        db.commit()
    assert _appeal_row(appeal["id"]).appellant_user_id is None
    decided = _done(
        world,
        appeal,
        "decide",
        by="sup_agent",
        **_body([_uphold("greeting"), _uphold("fee_explained")]),
    )
    assert decided["status"] == "decided"
    assert _recipients(world, appeal["id"], "review_appeal.decided") == []


def test_the_inbox_shows_the_callers_own_rows_of_this_workspace_newest_first(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup", "agent")
    agent = world.persona("agent")
    now = datetime.now(UTC)
    mine = [
        _seed_note(world.workspace_id, agent.user_id, now - timedelta(minutes=minutes))
        for minutes in (30, 20, 10)
    ]
    _seed_note(world.workspace_id, world.owner.user_id, now)
    elsewhere = _own_workspace(agent.user_id, world.workspace_id)
    assert elsewhere is not None
    _seed_note(elsewhere, agent.user_id, now)

    assert [n["id"] for n in _inbox(agent)] == list(reversed(mine))
    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 1, **({"cursor": cursor} if cursor else {})}
        items, cursor = _page(agent.get(NOTIFICATIONS, params=params))
        assert len(items) == 1, items
        seen.append(items[0]["id"])
        if cursor is None:
            break
    assert seen == list(reversed(mine))
    assert [n["id"] for n in _inbox(world.owner)] != []
    assert set(mine) & {n["id"] for n in _inbox(world.owner)} == set()

    read = agent.post(f"{NOTIFICATIONS}/{mine[1]}/read")
    assert read.status_code == 200, read.text
    assert [n["id"] for n in _inbox(agent, unread_only="true")] == [mine[2], mine[0]]
    assert [n["id"] for n in _inbox(agent, unread_only="false")] == list(reversed(mine))


def test_marking_read_is_idempotent_and_another_users_row_is_404(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "agent")
    agent = world.persona("agent")
    now = datetime.now(UTC)
    own = _seed_note(world.workspace_id, agent.user_id, now)
    theirs = _seed_note(world.workspace_id, world.owner.user_id, now)
    elsewhere = _seed_note(_own_workspace(agent.user_id, world.workspace_id), agent.user_id, now)

    for row_id in (theirs, elsewhere, str(uuid.uuid4())):
        _refused(agent.post(f"{NOTIFICATIONS}/{row_id}/read"), 404, "notification_not_found")
    with inspection_session_factory()() as db:
        assert db.get(Notification, theirs).read_at is None
        assert db.get(Notification, elsewhere).read_at is None

    first = agent.post(f"{NOTIFICATIONS}/{own}/read")
    assert first.status_code == 200, first.text
    view = _check_note(first.json())
    assert view["id"] == own and view["read_at"] is not None
    with inspection_session_factory()() as db:
        stored = _aware(db.get(Notification, own).read_at)
    second = agent.post(f"{NOTIFICATIONS}/{own}/read")
    assert second.status_code == 200, second.text
    assert _check_note(second.json())["read_at"] == view["read_at"]
    with inspection_session_factory()() as db:
        assert _aware(db.get(Notification, own).read_at) == stored
    owner_read = world.owner.post(f"{NOTIFICATIONS}/{theirs}/read")
    assert owner_read.status_code == 200, owner_read.text


# --- AC12 concurrency (PostgreSQL) ------------------------------------------------------


def _two(results: list, winner: int, codes: set[str]) -> None:
    statuses = sorted(r.status_code for r in results)
    assert statuses == sorted([winner, 409]), [r.text for r in results]
    [loser] = [r for r in results if r.status_code == 409]
    assert loser.json()["error"]["code"] in codes, loser.text


@pytest.mark.postgres
def test_two_concurrent_decides_give_one_200_and_one_409(client, email_outbox, monkeypatch):
    _requires_postgres()
    world = _World(client, email_outbox, monkeypatch, "sup", "sup_agent", "agent")
    appeal = _assign(world, _filed(world, _review(world)), "sup_agent")
    seen = _seen(world)
    path = f"{APPEALS}/{appeal['id']}/decide"
    revision = {"expected_revision": appeal["revision"]}
    sup = world.persona("sup_agent")
    results = _race(
        client,
        [
            (sup, path, {**revision, **_body([_uphold("greeting"), _uphold("fee_explained")])}),
            (
                sup,
                path,
                {**revision, **_body([_change("greeting", "passed"), _uphold("fee_explained")])},
            ),
        ],
    )
    _two(results, 200, {"review_appeal_revision_conflict", "review_appeal_closed"})
    assert _appeal_row(appeal["id"]).revision == appeal["revision"] + 1
    assert len(_results(appeal["id"])) <= 1
    assert [e.event_type for e in _since(world, seen)] == ["review_appeal.decided"]
    assert len(_recipients(world, appeal["id"], "review_appeal.decided")) == 1


@pytest.mark.postgres
def test_two_concurrent_assigns_give_one_200_and_one_409(client, email_outbox, monkeypatch):
    _requires_postgres()
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup_agent", "agent")
    appeal = _filed(world, _review(world))
    seen = _seen(world)
    path = f"{APPEALS}/{appeal['id']}/assign"
    results = _race(
        client,
        [
            (
                world.owner,
                path,
                {"expected_revision": 1, "assignee_user_id": world.user_id("admin")},
            ),
            (
                world.persona("admin"),
                path,
                {"expected_revision": 1, "assignee_user_id": world.user_id("sup_agent")},
            ),
        ],
    )
    _two(results, 200, {"review_appeal_revision_conflict"})
    assert _appeal_row(appeal["id"]).revision == 2
    assert [e.event_type for e in _since(world, seen)] == ["review_appeal.assigned"]


@pytest.mark.postgres
def test_a_decide_racing_a_withdraw_lets_exactly_one_land(client, email_outbox, monkeypatch):
    _requires_postgres()
    world = _World(client, email_outbox, monkeypatch, "sup", "sup_agent", "agent")
    appeal = _assign(world, _filed(world, _review(world)), "sup_agent")
    seen = _seen(world)
    revision = {"expected_revision": appeal["revision"]}
    results = _race(
        client,
        [
            (
                world.persona("sup_agent"),
                f"{APPEALS}/{appeal['id']}/decide",
                {**revision, **_body([_uphold("greeting"), _uphold("fee_explained")])},
            ),
            (world.persona("agent"), f"{APPEALS}/{appeal['id']}/withdraw", revision),
        ],
    )
    _two(results, 200, {"review_appeal_revision_conflict", "review_appeal_closed"})
    row = _appeal_row(appeal["id"])
    assert row.revision == appeal["revision"] + 1
    assert row.status in ("decided", "withdrawn")
    assert (row.decided_at is None) is (row.status == "withdrawn")
    assert [e.event_type for e in _since(world, seen)] == [f"review_appeal.{row.status}"]


@pytest.mark.postgres
def test_two_concurrent_filings_give_one_201_and_one_409(client, email_outbox, monkeypatch):
    _requires_postgres()
    world = _World(client, email_outbox, monkeypatch, "sup", "agent")
    case = _review(world)
    seen = _seen(world)
    agent = world.persona("agent")
    path = f"{MY_REVIEWS}/{case.id}/appeal"
    results = _race(
        client,
        [
            (agent, path, {"criterion_ids": ["greeting"], "reason_note": NOTE}),
            (agent, path, {"criterion_ids": ["fee_explained", "callback"], "reason_note": NOTE}),
        ],
    )
    _two(results, 201, {"appeal_exists"})
    with inspection_session_factory()() as db:
        appeals = list(
            db.scalars(select(ReviewAppeal).where(ReviewAppeal.review_item_id == case.id))
        )
    assert len(appeals) == 1
    assert [e.event_type for e in _since(world, seen)] == ["review_appeal.filed"]
