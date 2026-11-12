"""HOM-76 PR 2: agent appeals, supervisor dispositions and the appeal history.

The scored calls are real (HOM-74's fixture): a published scorecard, a real check run, a
supervisor who decides and completes the review, so the human result is the engine's.
On ``DEFINITION`` the reviewer accepts ``closing`` and overrides the auto-fail
``fee_explained`` from ``failed`` to ``passed``; the human outcomes are then
``greeting``, ``fee_explained`` and ``closing`` passed, ``off_topic`` failed (it allows
not applicable) and ``agenda`` not applicable by conversation (not appealable).

Personas (``Queue`` of HOM-73): ``sup`` supervises team A and completes the reviews (the
original reviewer); ``plain`` is made a second supervisor of team A here, so it is an
eligible decider; ``agent`` is the call's agent and the appellant; ``sup_b`` supervises
team B only; ``lapsed`` is a member without authority; ``viewer`` is a viewer.

Every deny-case runs next to its allow-control.  A refusal must write nothing: the appeal
tables, the audit log and the notifications keep their counts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text, update

from backend.database.saas_models import (
    AuditEvent,
    Meeting,
    Notification,
    ReviewAppeal,
    ReviewAppealCriterion,
    ReviewAppealResult,
    ReviewCriterionDecision,
    ReviewItem,
    ReviewResultRecord,
    ScorecardVersion,
)
from backend.routers.v1 import review_appeals as appeals_router
from backend.scorecards import score_conversation
from backend.security.authorization import (
    API_KEY_SCOPES,
    ROLE_PERMISSIONS,
    Permission,
    WorkspaceRole,
)
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_review_decisions_api import (
    DEFINITION,
    SCORE_KEYS,
    Queue,
    Scored,
    _criterion,
    _scored_item,
    _snapshot_result,
    _stored_check_run,
    accept,
    build_queue,
    code,
    decided,
    override,
)
from backend.tests.test_review_queue_access import acted

APPEALS = "/api/v1/review-appeals"
MY_REVIEWS = "/api/v1/my-reviews"
MY_APPEALS = "/api/v1/my-appeals"
REASON = "I followed the script on this call."
DISPOSITION = "We checked the call again."
# Twenty criteria of weight 5: the most an appeal may name.
TWENTY = {
    "name": "Twenty criteria",
    "pass_threshold": 50,
    "criteria": [_criterion(f"c{index:02d}", 5, "hello") for index in range(20)],
}
HUMAN_OUTCOMES = {
    "greeting": "passed",
    "fee_explained": "passed",
    "closing": "passed",
    "off_topic": "failed",
    "agenda": "not_applicable",
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
APPEAL_KEYS = {
    "view",
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
}
REVIEWER_ID_KEYS = (
    "original_reviewer_user_id",
    "assignee_user_id",
    "assigned_by_user_id",
    "decided_by_user_id",
    "team_id",
)


def build_appeal_queue(client, email_outbox, monkeypatch) -> Queue:
    built = build_queue(client, email_outbox, monkeypatch)
    # A second supervisor of team A: an eligible decider who is not an original reviewer.
    built.assign_team(built.plain, built.team_a, "supervisor")
    return built


@pytest.fixture
def queue(client, email_outbox, monkeypatch) -> Queue:
    return build_appeal_queue(client, email_outbox, monkeypatch)


# --- helpers ------------------------------------------------------------------------------


@dataclass
class Reviewed:
    """A completed review item with a human result, on a call whose agent is ``agent``."""

    item: dict
    scored: Scored

    @property
    def id(self) -> str:
        return self.item["id"]

    @property
    def meeting_id(self) -> str:
        return self.scored.meeting_id


def reviewed(queue: Queue, *, agent=None, definition=DEFINITION, admin_decides=False) -> Reviewed:
    """``sup`` reviews and completes the call.  With ``admin_decides`` the admin is
    assigned first, accepts ``closing`` and is then reassigned away, so the admin is an
    original reviewer through a decision only."""
    agent = agent or queue.agent
    first = queue.admin if admin_decides else queue.sup
    scored = _scored_item(queue, assignee=first, definition=definition, agent_user_id=agent.user_id)
    item = scored.item
    if definition is DEFINITION:
        item = decided(first, item, "closing", **accept())
        if admin_decides:
            item = acted(queue.owner, item, "assign", assignee_user_id=queue.sup.user_id)
            item = acted(queue.sup, item, "start")
        item = decided(queue.sup, item, "fee_explained", **override("passed", evidence=[0]))
    item = acted(queue.sup, item, "complete")
    assert item["status"] == "completed"
    return Reviewed(item=item, scored=scored)


def file(persona, item_id: str, criterion_ids: Any = ("greeting",), reason_note: Any = REASON):
    if isinstance(criterion_ids, tuple):
        criterion_ids = list(criterion_ids)
    return persona.post(
        f"{MY_REVIEWS}/{item_id}/appeal",
        json={"criterion_ids": criterion_ids, "reason_note": reason_note},
    )


def filed(persona, item_id: str, criterion_ids: Any = ("greeting",), **extra: Any) -> dict:
    response = file(persona, item_id, criterion_ids, **extra)
    assert response.status_code == 201, response.text
    return response.json()


def act(persona, appeal: dict, action: str, **body: Any):
    payload = {"expected_revision": appeal["revision"], **body}
    return persona.post(f"{APPEALS}/{appeal['id']}/{action}", json=payload)


def acted_on(persona, appeal: dict, action: str, **body: Any) -> dict:
    response = act(persona, appeal, action, **body)
    assert response.status_code == 200, response.text
    return response.json()


def assign(persona, appeal: dict, assignee) -> dict:
    return acted_on(persona, appeal, "assign", assignee_user_id=assignee.user_id)


def uphold(criterion_id: str, **extra: Any) -> dict:
    return {"criterion_id": criterion_id, "decision": "uphold", **extra}


def change(criterion_id: str, outcome: str, **extra: Any) -> dict:
    return {
        "criterion_id": criterion_id,
        "decision": "change",
        "decided_outcome": outcome,
        "reason_code": "evidence_missed",
        "note": "The evidence is in turn 2.",
        **extra,
    }


def decide(persona, appeal: dict, decisions: Any, disposition_note: Any = DISPOSITION):
    return act(persona, appeal, "decide", decisions=decisions, disposition_note=disposition_note)


def counts() -> dict[str, int]:
    with inspection_session_factory()() as db:
        return {
            model.__tablename__: db.scalar(select(func.count()).select_from(model)) or 0
            for model in (
                ReviewAppeal,
                ReviewAppealCriterion,
                ReviewAppealResult,
                AuditEvent,
                Notification,
            )
        }


def appeal_events(appeal_id: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.target_type == "review_appeal", AuditEvent.target_id == appeal_id)
                .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
            )
        )


def stored_appeal(appeal_id: str) -> ReviewAppeal:
    with inspection_session_factory()() as db:
        row = db.get(ReviewAppeal, appeal_id)
        assert row is not None
        return row


def set_appeal(appeal_id: str, **values: Any) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(ReviewAppeal).where(ReviewAppeal.id == appeal_id).values(**values))
        db.commit()


def set_agent(owner, meeting_id: str, persona) -> None:
    """The attribution route itself (``meeting:attribute``), as HOM-360 built it."""
    moved = owner.patch(f"/api/v1/meetings/{meeting_id}", json={"agent_user_id": persona.user_id})
    assert moved.status_code == 200, moved.text


def freeze(monkeypatch, at: datetime) -> None:
    monkeypatch.setattr(appeals_router, "_now", lambda: at)


def completed_at(item: dict) -> datetime:
    return datetime.fromisoformat(item["completed_at"])


def everyone_but(queue: Queue, *keep) -> set[str]:
    people = (
        queue.owner,
        queue.admin,
        queue.sup,
        queue.sup_b,
        queue.agent,
        queue.plain,
        queue.lapsed,
        queue.viewer,
    )
    return {persona.user_id for persona in people if persona not in keep}


def routes(item_id: str, appeal_id: str) -> list[tuple[str, str, dict | None]]:
    """Every new appeal route, with a body that passes the shape checks."""
    revision = {"expected_revision": 1}
    return [
        ("GET", MY_REVIEWS, None),
        ("GET", f"{MY_REVIEWS}/{item_id}", None),
        (
            "POST",
            f"{MY_REVIEWS}/{item_id}/appeal",
            {"criterion_ids": ["greeting"], "reason_note": "x"},
        ),
        ("GET", MY_APPEALS, None),
        ("GET", APPEALS, None),
        ("GET", f"{APPEALS}/{appeal_id}", None),
        ("GET", f"{APPEALS}/{appeal_id}/history", None),
        ("POST", f"{APPEALS}/{appeal_id}/withdraw", revision),
        ("POST", f"{APPEALS}/{appeal_id}/assign", {**revision, "assignee_user_id": "x"}),
        ("POST", f"{APPEALS}/{appeal_id}/decide", {**revision, "decisions": []}),
    ]


# --- AC1: permissions and route policy -------------------------------------------------------


def test_the_two_permissions_belong_to_every_role_and_never_to_a_key():
    assert Permission.REVIEW_APPEAL.value == "review:appeal"
    assert Permission.NOTIFICATION_READ.value == "notification:read"
    for permission in (Permission.REVIEW_APPEAL, Permission.NOTIFICATION_READ):
        holders = {role for role, granted in ROLE_PERMISSIONS.items() if permission in granted}
        assert holders == set(WorkspaceRole)
        assert permission not in API_KEY_SCOPES


def test_the_session_payload_lists_both_permissions_for_every_role(queue):
    for persona in (queue.owner, queue.admin, queue.agent, queue.viewer):
        me = persona.client.get("/api/v1/auth/me", cookies=persona.cookies)
        assert me.status_code == 200, me.text
        (workspace,) = [w for w in me.json()["workspaces"] if w["id"] == queue.workspace_id]
        assert {"review:appeal", "notification:read"} <= set(workspace["permissions"])


def test_an_api_key_gets_403_on_every_appeal_route_and_cannot_carry_the_scopes(client, queue):
    owner = queue.owner
    target = reviewed(queue)
    appeal = filed(queue.agent, target.id)
    before = counts()
    every_scope = sorted(permission.value for permission in API_KEY_SCOPES)
    key = _mint(client, owner.headers, owner.cookies, name="all", scopes=every_scope)
    assert key.status_code == 201, key.text
    headers = _bearer(key.json()["key"], **{"X-Workspace-ID": owner.workspace_id})
    for method, path, body in routes(target.id, appeal["id"]):
        response = client.request(method, path, json=body, headers=headers)
        assert response.status_code == 403, (method, path, response.text)
        assert code(response) == "api_key_scope_denied", (method, path)
    for scope in ("review:appeal", "notification:read"):
        refused = _mint(client, owner.headers, owner.cookies, name=scope, scopes=[scope])
        assert refused.status_code == 422, refused.text
    # Nothing of the appeal changed (minting the key writes its own event only).
    after = counts()
    assert {k: v for k, v in after.items() if k != "audit_events"} == {
        k: v for k, v in before.items() if k != "audit_events"
    }
    assert [event.event_type for event in appeal_events(appeal["id"])] == ["review_appeal.filed"]


def test_every_appeal_write_needs_the_csrf_token(queue):
    target = reviewed(queue)
    appeal = filed(queue.agent, target.id)
    before = counts()
    for persona in (queue.agent, queue.owner):
        for method, path, body in routes(target.id, appeal["id"]):
            if method != "POST":
                continue
            response = persona.client.post(
                path,
                json=body,
                cookies=persona.cookies,
                headers={"X-Workspace-ID": persona.workspace_id},
            )
            assert response.status_code == 403, (path, response.text)
            assert code(response) == "csrf_failed", path
    assert counts() == before
    # Allow-control: the same withdraw with the token.
    assert act(queue.agent, appeal, "withdraw").status_code == 200


def test_a_viewer_reaches_the_agent_routes_but_not_the_supervisor_routes(queue):
    target = reviewed(queue, agent=queue.viewer)
    for path in (MY_REVIEWS, MY_APPEALS):
        assert queue.viewer.get(path).status_code == 200, path
    appeal = filed(queue.viewer, target.id)
    assert queue.viewer.get(f"{APPEALS}/{appeal['id']}").json()["view"] == "appellant"
    for method, path, body in routes(target.id, appeal["id"]):
        if path.endswith(("/assign", "/decide")) or path == APPEALS:
            response = queue.viewer.client.request(
                method, path, json=body, cookies=queue.viewer.cookies, headers=queue.viewer.headers
            )
            assert response.status_code == 403, (path, response.text)
            assert code(response) == "permission_denied", path


# --- AC2: my reviews --------------------------------------------------------------------------


def my_reviews(persona, **params: Any) -> list[dict]:
    response = persona.get(MY_REVIEWS, params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor"}
    return body["items"]


def test_the_agent_lists_only_completed_reviews_of_their_own_calls(queue):
    mine = reviewed(queue)
    other = reviewed(queue, agent=queue.lapsed)
    in_review = _scored_item(queue, agent_user_id=queue.agent.user_id)
    rows = my_reviews(queue.agent)
    assert [row["review_item_id"] for row in rows] == [mine.id]
    (row,) = rows
    assert set(row) == SUMMARY_KEYS
    for key in ("machine", "human", "final"):
        assert set(row[key]) == SCORE_KEYS, key
    assert row["machine"]["outcome"] == "failed"  # the auto-fail fee was not explained
    assert row["human"]["outcome"] == "passed"  # the reviewer overrode it
    assert row["final"] == row["human"] and row["final_source"] == "human"
    assert row["appeal"] is None
    deadline = datetime.fromisoformat(row["appeal_deadline"])
    assert deadline - datetime.fromisoformat(row["completed_at"]) == timedelta(days=14)
    assert deadline.utcoffset() == timedelta(0)
    # Another user, even the owner, sees none of the agent's reviews.
    for persona in (queue.owner, queue.sup, queue.admin):
        assert mine.id not in [r["review_item_id"] for r in my_reviews(persona)]
        response = persona.get(f"{MY_REVIEWS}/{mine.id}")
        assert response.status_code == 404 and code(response) == "review_item_not_found"
    # An item still in review is not listed and not readable.
    response = queue.agent.get(f"{MY_REVIEWS}/{in_review.item['id']}")
    assert response.status_code == 404 and code(response) == "review_item_not_found"
    # Allow-control: the other call's agent sees their own review.
    assert [r["review_item_id"] for r in my_reviews(queue.lapsed)] == [other.id]


def test_my_reviews_are_newest_first_with_keyset_pages(queue):
    first = reviewed(queue)
    second = reviewed(queue)
    with inspection_session_factory()() as db:
        db.execute(
            update(ReviewItem)
            .where(ReviewItem.id == first.id)
            .values(completed_at=datetime.now(UTC) - timedelta(hours=1))
        )
        db.commit()
    page = queue.agent.get(MY_REVIEWS, params={"limit": 1}).json()
    assert [row["review_item_id"] for row in page["items"]] == [second.id]
    assert page["next_cursor"]
    rest = queue.agent.get(MY_REVIEWS, params={"limit": 1, "cursor": page["next_cursor"]}).json()
    assert [row["review_item_id"] for row in rest["items"]] == [first.id]
    assert rest["next_cursor"] is None
    bad = queue.agent.get(MY_REVIEWS, params={"cursor": "not-a-cursor"})
    assert bad.status_code == 400 and code(bad) == "invalid_cursor"
    for limit in (0, 101):
        assert queue.agent.get(MY_REVIEWS, params={"limit": limit}).status_code == 422


def test_the_detail_shows_each_criterion_without_any_reviewer_id(queue):
    target = reviewed(queue)
    response = queue.agent.get(f"{MY_REVIEWS}/{target.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == SUMMARY_KEYS | {"criteria"}
    assert [row["criterion_id"] for row in body["criteria"]] == list(HUMAN_OUTCOMES)
    assert {row["criterion_id"]: row["human_outcome"] for row in body["criteria"]} == (
        HUMAN_OUTCOMES
    )
    by_id = {row["criterion_id"]: row for row in body["criteria"]}
    assert by_id["fee_explained"]["machine_outcome"] == "failed"
    assert by_id["fee_explained"]["decision"] == {
        "decision": "override",
        "reviewer_outcome": "passed",
        "reason_code": "other",
        "note": "why not",
        "evidence": [{"turn_index": 0}],
    }
    assert by_id["greeting"]["decision"] is None
    assert by_id["agenda"]["reviewable"] is False
    assert all(row["appeal"] is None for row in body["criteria"])
    assert not everyone_but(queue, queue.agent) & _ids_in(body)


def _ids_in(payload: Any) -> set[str]:
    """Every string anywhere in a JSON payload."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            found.add(value)
        elif isinstance(value, dict):
            for key, inner in value.items():
                found.add(str(key))
                walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)

    walk(payload)
    return found


def test_the_reviews_follow_the_attribution_and_a_deleted_call_disappears(queue):
    target = reviewed(queue)
    set_agent(queue.owner, target.meeting_id, queue.lapsed)
    assert my_reviews(queue.agent) == []
    assert queue.agent.get(f"{MY_REVIEWS}/{target.id}").status_code == 404
    assert [row["review_item_id"] for row in my_reviews(queue.lapsed)] == [target.id]
    with inspection_session_factory()() as db:
        db.execute(
            update(Meeting)
            .where(Meeting.id == target.meeting_id)
            .values(deleted_at=datetime.now(UTC))
        )
        db.commit()
    assert my_reviews(queue.lapsed) == []
    response = queue.lapsed.get(f"{MY_REVIEWS}/{target.id}")
    assert response.status_code == 404 and code(response) == "review_item_not_found"
    refused = file(queue.lapsed, target.id)
    assert refused.status_code == 404 and code(refused) == "review_item_not_found"


# --- AC3: filing --------------------------------------------------------------------------------


def assert_refused(response, status: int, error: str, before: dict) -> None:
    assert response.status_code == status, response.text
    assert code(response) == error, response.text
    assert counts() == before


def test_filing_writes_the_appeal_its_criteria_one_event_and_the_snapshot(queue):
    target = reviewed(queue)
    response = file(queue.agent, target.id, ["fee_explained", "off_topic"])
    assert response.status_code == 201, response.text
    appeal = response.json()
    assert set(appeal) == APPEAL_KEYS
    assert appeal["view"] == "appellant"
    assert (appeal["status"], appeal["revision"], appeal["appellant_user_id"]) == (
        "open",
        1,
        queue.agent.user_id,
    )
    for key in REVIEWER_ID_KEYS:
        assert appeal[key] is None, key
    assert [row["criterion_id"] for row in appeal["criteria"]] == ["fee_explained", "off_topic"]
    row = stored_appeal(appeal["id"])
    assert row.original_reviewer_user_id == queue.sup.user_id  # the item's assignee
    assert row.reason_note == REASON
    with inspection_session_factory()() as db:
        criteria = {
            c.criterion_id: c.appealed_outcome
            for c in db.scalars(
                select(ReviewAppealCriterion).where(ReviewAppealCriterion.appeal_id == appeal["id"])
            )
        }
    # The human outcomes, not the machine ones (the fee was overridden to passed).
    assert criteria == {"fee_explained": "passed", "off_topic": "failed"}
    (event,) = appeal_events(appeal["id"])
    assert event.event_type == "review_appeal.filed"
    assert event.actor_user_id == queue.agent.user_id
    assert event.details == {
        "review_item_id": target.id,
        "scorecard_result_id": target.scored.result_id,
        "criterion_ids": ["fee_explained", "off_topic"],
        "appellant_user_id": queue.agent.user_id,
        "revision": 1,
    }
    assert REASON not in json.dumps(event.details)
    # The summary shows it.
    (summary,) = my_reviews(queue.agent)
    assert summary["appeal"] == {"id": appeal["id"], "status": "open", "disposition": None}


def test_filing_checks_run_in_order(queue):
    target = reviewed(queue)
    in_review = _scored_item(queue, agent_user_id=queue.agent.user_id)
    before = counts()
    # 1. Not visible: another user (even the owner), another call, a missing id.
    for persona in (queue.owner, queue.sup):
        assert_refused(file(persona, target.id), 404, "review_item_not_found", before)
    assert_refused(file(queue.agent, "missing"), 404, "review_item_not_found", before)
    # 2. Visible but not appealable: the agent's own call, still in review.
    assert_refused(
        file(queue.agent, in_review.item["id"]), 409, "review_item_not_appealable", before
    )
    # 5. Criteria before the note: an unknown criterion with a blank note.
    assert_refused(
        file(queue.agent, target.id, ["nope"], reason_note=" "),
        422,
        "appeal_criterion_invalid",
        before,
    )
    # Allow-control.
    filed(queue.agent, target.id)


def test_the_window_is_fourteen_days_from_completion(queue, monkeypatch):
    late = reviewed(queue)
    last_second = reviewed(queue)
    before = counts()
    freeze(monkeypatch, completed_at(late.item) + timedelta(days=14))
    assert_refused(file(queue.agent, late.id), 409, "appeal_window_closed", before)
    freeze(monkeypatch, completed_at(last_second.item) + timedelta(days=14) - timedelta(seconds=1))
    filed(queue.agent, last_second.id)


def test_one_appeal_per_item_also_after_a_withdrawal(queue):
    target = reviewed(queue)
    appeal = filed(queue.agent, target.id)
    before = counts()
    assert_refused(file(queue.agent, target.id), 409, "appeal_exists", before)
    acted_on(queue.agent, appeal, "withdraw")
    before = counts()
    assert_refused(file(queue.agent, target.id), 409, "appeal_exists", before)
    # Allow-control: another item.
    filed(queue.agent, reviewed(queue).id)


def test_each_named_criterion_must_be_an_appealable_criterion_of_the_result(queue):
    target = reviewed(queue)
    before = counts()
    for criteria in (["nope"], ["greeting", "nope"], ["agenda"]):
        response = file(queue.agent, target.id, criteria)
        assert_refused(response, 422, "appeal_criterion_invalid", before)
        assert response.json()["error"]["details"] == {"criterion_id": criteria[-1]}, criteria
    filed(queue.agent, target.id, ["off_topic"])


def test_the_criteria_count_and_distinctness(queue):
    target = reviewed(queue)
    before = counts()
    for criteria in ([], ["greeting", "greeting"], "greeting", None, [1], ["greeting"] * 21):
        assert_refused(
            file(queue.agent, target.id, criteria), 422, "appeal_criteria_invalid", before
        )
    twenty = reviewed(queue, definition=TWENTY)
    before = counts()
    too_many = [f"c{index:02d}" for index in range(20)] + ["c00"]
    assert_refused(file(queue.agent, twenty.id, too_many), 422, "appeal_criteria_invalid", before)
    # Allow-controls: one criterion and twenty criteria.
    filed(queue.agent, target.id, ["greeting"])
    appeal = filed(queue.agent, twenty.id, [f"c{index:02d}" for index in range(20)])
    assert len(appeal["criteria"]) == 20


def test_the_reason_note_rules(queue):
    targets = [reviewed(queue) for _ in range(3)]
    before = counts()
    for note in ("", "   ", "x" * 2001, None, 7, "a\x00b"):
        assert_refused(
            file(queue.agent, targets[0].id, reason_note=note),
            422,
            "appeal_reason_required",
            before,
        )
    filed(queue.agent, targets[0].id, reason_note="x")
    long = filed(queue.agent, targets[1].id, reason_note="x" * 2000)
    assert len(stored_appeal(long["id"]).reason_note) == 2000
    padded = filed(queue.agent, targets[2].id, reason_note="  why  ")
    assert stored_appeal(padded["id"]).reason_note == "why"


def test_the_filing_body_forbids_unknown_keys(queue):
    target = reviewed(queue)
    before = counts()
    response = queue.agent.post(
        f"{MY_REVIEWS}/{target.id}/appeal",
        json={"criterion_ids": ["greeting"], "reason_note": REASON, "status": "decided"},
    )
    assert response.status_code == 422 and counts() == before


# --- AC4: supervisor visibility -----------------------------------------------------------------


def listed(persona, **params: Any) -> list[str]:
    response = persona.get(APPEALS, params=params)
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()["items"]]


def assert_supervisor_sees(persona, appeal: dict) -> None:
    assert appeal["id"] in listed(persona)
    response = persona.get(f"{APPEALS}/{appeal['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["view"] == "supervisor"


def assert_hidden(persona, appeal: dict) -> None:
    before = counts()
    revision = stored_appeal(appeal["id"]).revision
    assert appeal["id"] not in listed(persona)
    for action, body in (
        ("assign", {"assignee_user_id": persona.user_id}),
        ("decide", {"decisions": [], "disposition_note": DISPOSITION}),
    ):
        response = act(persona, {**appeal, "revision": revision}, action, **body)
        assert response.status_code == 404, (action, response.text)
        assert code(response) == "review_appeal_not_found", action
    assert counts() == before


def test_owner_admin_and_the_teams_supervisor_see_the_appeal_others_do_not(queue):
    target = reviewed(queue)
    appeal = filed(queue.agent, target.id)
    for persona in (queue.owner, queue.admin, queue.plain):
        assert_supervisor_sees(persona, appeal)
    # Another team's supervisor, a member without authority, the original reviewer.
    for persona in (queue.sup_b, queue.lapsed, queue.sup):
        assert_hidden(persona, appeal)
        response = persona.get(f"{APPEALS}/{appeal['id']}")
        assert response.status_code == 404 and code(response) == "review_appeal_not_found"
    # The appellant (the call's agent) is hidden from the supervisor routes and reads
    # the appeal in the agent view only.
    assert_hidden(queue.agent, appeal)
    view = queue.agent.get(f"{APPEALS}/{appeal['id']}").json()
    assert view["view"] == "appellant"
    for key in REVIEWER_ID_KEYS:
        assert view[key] is None, key


def test_a_reviewer_who_only_decided_a_criterion_is_an_original_reviewer(queue):
    target = reviewed(queue, admin_decides=True)
    appeal = filed(queue.agent, target.id)
    with inspection_session_factory()() as db:
        deciders = set(
            db.scalars(
                select(ReviewCriterionDecision.decided_by_user_id).where(
                    ReviewCriterionDecision.review_item_id == target.id
                )
            )
        )
    assert deciders == {queue.admin.user_id, queue.sup.user_id}
    assert stored_appeal(appeal["id"]).original_reviewer_user_id == queue.sup.user_id
    for persona in (queue.admin, queue.sup):
        assert_hidden(persona, appeal)
        assert persona.get(f"{APPEALS}/{appeal['id']}").status_code == 404
    # Allow-control: the owner and the second supervisor still see it.
    for persona in (queue.owner, queue.plain):
        assert_supervisor_sees(persona, appeal)


def test_the_list_filters_and_pages(queue):
    appeals = [filed(queue.agent, reviewed(queue).id) for _ in range(3)]
    assigned = assign(queue.owner, appeals[0], queue.plain)
    acted_on(queue.agent, appeals[1], "withdraw")
    ids = [appeal["id"] for appeal in appeals]
    assert listed(queue.owner) == ids  # created_at, id ascending
    assert listed(queue.owner, status="open") == [ids[0], ids[2]]
    assert listed(queue.owner, status="withdrawn") == [ids[1]]
    assert listed(queue.owner, assignee_user_id=queue.plain.user_id) == [assigned["id"]]
    page = queue.owner.get(APPEALS, params={"limit": 2}).json()
    assert [row["id"] for row in page["items"]] == ids[:2] and page["next_cursor"]
    rest = queue.owner.get(APPEALS, params={"limit": 2, "cursor": page["next_cursor"]}).json()
    assert [row["id"] for row in rest["items"]] == ids[2:] and rest["next_cursor"] is None
    bad = queue.owner.get(APPEALS, params={"status": "lost"})
    assert bad.status_code == 422 and code(bad) == "review_appeal_filter_invalid"
    bad = queue.owner.get(APPEALS, params={"cursor": "x"})
    assert bad.status_code == 400 and code(bad) == "invalid_cursor"


def test_my_appeals_lists_only_the_callers_own_appeals_newest_first(queue):
    first = filed(queue.agent, reviewed(queue).id)
    second = filed(queue.agent, reviewed(queue).id)
    other = filed(queue.lapsed, reviewed(queue, agent=queue.lapsed).id)
    response = queue.agent.get(MY_APPEALS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["id"] for row in body["items"]] == [second["id"], first["id"]]
    assert all(row["view"] == "appellant" for row in body["items"])
    assert [row["id"] for row in queue.lapsed.get(MY_APPEALS).json()["items"]] == [other["id"]]
    assert queue.owner.get(MY_APPEALS).json()["items"] == []
    page = queue.agent.get(MY_APPEALS, params={"limit": 1}).json()
    rest = queue.agent.get(MY_APPEALS, params={"limit": 1, "cursor": page["next_cursor"]}).json()
    assert [row["id"] for row in page["items"] + rest["items"]] == [second["id"], first["id"]]


# --- AC5: assign --------------------------------------------------------------------------------


def test_assign_refuses_each_party_and_original_reviewer_with_its_code(queue):
    target = reviewed(queue, admin_decides=True)
    appeal = filed(queue.agent, target.id)
    before = counts()
    for persona, error in (
        (queue.agent, "appeal_assignee_is_party"),  # the agent and the appellant
        (queue.sup, "appeal_assignee_is_original_reviewer"),  # the completing assignee
        (queue.admin, "appeal_assignee_is_original_reviewer"),  # a decider of a criterion
        (queue.sup_b, "review_assignee_ineligible"),  # HOM-73: no authority
        (queue.lapsed, "review_assignee_ineligible"),
        (queue.viewer, "review_assignee_ineligible"),
    ):
        response = act(queue.owner, appeal, "assign", assignee_user_id=persona.user_id)
        assert_refused(response, 422, error, before)
    for value in (None, 7, "unknown-user"):
        response = act(queue.owner, appeal, "assign", assignee_user_id=value)
        assert_refused(response, 422, "review_assignee_ineligible", before)
    # Allow-controls: the owner, the second supervisor (by the owner), and each again.
    for persona in (queue.owner, queue.plain):
        appeal = assign(queue.owner, appeal, persona)
        assert appeal["assignee_user_id"] == persona.user_id
        assert appeal["assigned_by_user_id"] == queue.owner.user_id


def test_an_owner_or_admin_who_is_a_party_cannot_assign(queue):
    """SPEC-pr2 R10: right after visibility, the caller is not the call's current agent
    (nor the appellant).  An admin who became the agent still sees the appeal."""
    target = reviewed(queue)
    appeal = filed(queue.agent, target.id)
    set_agent(queue.owner, target.meeting_id, queue.admin)
    before = counts()
    assert_supervisor_sees(queue.admin, appeal)
    for revision in (appeal["revision"], 9):  # before the revision check
        response = act(
            queue.admin,
            {**appeal, "revision": revision},
            "assign",
            assignee_user_id=queue.plain.user_id,
        )
        assert_refused(response, 422, "appeal_assigner_is_party", before)
    # Allow-control: the owner assigns.
    assert assign(queue.owner, appeal, queue.plain)["assignee_user_id"] == queue.plain.user_id


def test_an_eligible_admin_is_accepted_and_the_same_assignee_again_writes_nothing(queue):
    appeal = filed(queue.agent, reviewed(queue).id)
    appeal = assign(queue.plain, appeal, queue.admin)  # a supervising member assigns
    assert appeal["revision"] == 2
    before = counts()
    again = assign(queue.owner, appeal, queue.admin)
    assert again["revision"] == 2 and again["updated_at"] == appeal["updated_at"]
    assert counts() == before


def test_assign_checks_visibility_then_revision_then_open(queue):
    appeal = filed(queue.agent, reviewed(queue).id)
    before = counts()
    # A stale revision before the assignee rules: the agent as assignee is not reached.
    stale = act(
        queue.owner, {**appeal, "revision": 9}, "assign", assignee_user_id=queue.agent.user_id
    )
    assert_refused(stale, 409, "review_appeal_revision_conflict", before)
    assert stale.json()["error"]["details"] == {"current_revision": 1}
    withdrawn = acted_on(queue.agent, appeal, "withdraw")
    before = counts()
    closed = act(queue.owner, withdrawn, "assign", assignee_user_id=queue.agent.user_id)
    assert_refused(closed, 409, "review_appeal_closed", before)
    missing = act(queue.owner, {**appeal, "id": "missing"}, "assign", assignee_user_id="x")
    assert_refused(missing, 404, "review_appeal_not_found", before)


# --- AC6 and AC9: decide ------------------------------------------------------------------------


def ready(
    queue: Queue, criteria=("greeting", "fee_explained", "off_topic")
) -> tuple[Reviewed, dict]:
    """A reviewed call, an appeal on ``criteria`` and ``plain`` as its decider."""
    target = reviewed(queue)
    appeal = filed(queue.agent, target.id, list(criteria))
    return target, assign(queue.owner, appeal, queue.plain)


def human_row(item_id: str) -> ReviewResultRecord:
    with inspection_session_factory()() as db:
        row = db.scalar(
            select(ReviewResultRecord).where(ReviewResultRecord.review_item_id == item_id)
        )
        assert row is not None
        return row


def snapshot_reviews(target: Reviewed) -> dict:
    with inspection_session_factory()() as db:
        decisions = sorted(
            (row.criterion_id, row.decision, row.reviewer_outcome, row.note, row.decided_by_user_id)
            for row in db.scalars(
                select(ReviewCriterionDecision).where(
                    ReviewCriterionDecision.review_item_id == target.id
                )
            )
        )
    human = human_row(target.id)
    return {
        "machine": _snapshot_result(target.scored.result_id),
        "decisions": decisions,
        "human": {
            column.key: getattr(human, column.key)
            for column in ReviewResultRecord.__table__.columns
        },
    }


def test_all_uphold_is_upheld_with_no_appeal_result(queue):
    target, appeal = ready(queue)
    before_reviews = snapshot_reviews(target)
    decisions = [uphold("greeting"), uphold("fee_explained", note="Seen."), uphold("off_topic")]
    response = decide(queue.plain, appeal, decisions)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["disposition"], body["revision"]) == ("decided", "upheld", 3)
    assert body["decided_by_user_id"] == queue.plain.user_id
    assert body["disposition_note"] == DISPOSITION
    assert body["appeal_result"] is None and body["final"] == body["human"]
    assert {row["criterion_id"]: row["decision"] for row in body["criteria"]} == {
        "greeting": "uphold",
        "fee_explained": "uphold",
        "off_topic": "uphold",
    }
    assert counts()["review_appeal_results"] == 0
    assert snapshot_reviews(target) == before_reviews


def test_all_change_is_granted_and_stores_the_appeal_result(queue):
    target, appeal = ready(queue)
    before_reviews = snapshot_reviews(target)
    decisions = [
        change("greeting", "failed"),
        change("fee_explained", "failed"),
        change("off_topic", "not_applicable"),
    ]
    response = decide(queue.plain, appeal, decisions)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["disposition"] == "granted"
    human = human_row(target.id)
    expected_outcomes = {
        **human.reviewer_outcomes,
        "greeting": "failed",
        "fee_explained": "failed",
        "off_topic": "not_applicable",
    }
    with inspection_session_factory()() as db:
        (row,) = db.scalars(select(ReviewAppealResult))
        document = db.get(ScorecardVersion, target.scored.version_id).document
    expected = score_conversation(
        document, _stored_check_run(target.scored.result_id), target.scored.facts, expected_outcomes
    )
    assert row.reviewer_outcomes == expected_outcomes
    assert row.appeal_id == appeal["id"] and row.review_item_id == target.id
    assert row.result == expected.to_json()
    assert (row.outcome, row.reason, row.earned_weight, row.failed_weight) == (
        expected.outcome,
        expected.reason,
        expected.earned_weight,
        expected.failed_weight,
    )
    assert body["appeal_result"]["outcome"] == expected.outcome == "failed"
    assert body["final"] == body["appeal_result"]
    # The machine result, the decisions and the human result never change.
    assert snapshot_reviews(target) == before_reviews
    # AC9: the agent's review shows the appeal result as final.
    (summary,) = my_reviews(queue.agent)
    assert summary["final_source"] == "appeal" and summary["final"] == body["appeal_result"]
    assert summary["human"] == body["human"]
    detail = queue.agent.get(f"{MY_REVIEWS}/{target.id}").json()
    by_id = {row["criterion_id"]: row for row in detail["criteria"]}
    assert by_id["greeting"]["appeal"] == {
        "decision": "change",
        "decided_outcome": "failed",
        "reason_code": "evidence_missed",
        "note": "The evidence is in turn 2.",
    }
    assert by_id["closing"]["appeal"] is None


def test_mixed_decisions_are_partially_granted(queue):
    target, appeal = ready(queue, ("greeting", "off_topic"))
    body = decide(queue.plain, appeal, [uphold("greeting"), change("off_topic", "passed")]).json()
    assert body["disposition"] == "partially_granted"
    with inspection_session_factory()() as db:
        (row,) = db.scalars(select(ReviewAppealResult))
    assert row.reviewer_outcomes == {
        **human_row(target.id).reviewer_outcomes,
        "off_topic": "passed",
    }


def test_decide_checks_run_in_order_and_each_refusal_writes_nothing(queue):
    target, appeal = ready(queue)
    good = [uphold("greeting"), uphold("fee_explained"), uphold("off_topic")]
    before = counts()
    # Visible: the original reviewer and another team's supervisor get 404.
    for persona in (queue.sup, queue.sup_b):
        assert_refused(decide(persona, appeal, good), 404, "review_appeal_not_found", before)
    # The revision before the assignee rule.
    stale = decide(queue.owner, {**appeal, "revision": 1}, good)
    assert_refused(stale, 409, "review_appeal_revision_conflict", before)
    # Only the assignee decides.
    assert_refused(decide(queue.owner, appeal, good), 403, "review_appeal_not_assignee", before)
    # The engine version, before the body (an empty body would be incomplete).
    with inspection_session_factory()() as db:
        db.execute(
            text("UPDATE scorecard_results SET scorecards_version = 'scorecards/0' WHERE id = :id"),
            {"id": target.scored.result_id},
        )
        db.commit()
    assert_refused(decide(queue.plain, appeal, []), 409, "scorecard_version_unsupported", before)
    with inspection_session_factory()() as db:
        db.execute(
            text("UPDATE scorecard_results SET scorecards_version = 'scorecards/1' WHERE id = :id"),
            {"id": target.scored.result_id},
        )
        db.commit()
    assert stored_appeal(appeal["id"]).status == "open"
    # Allow-control.
    assert decide(queue.plain, appeal, good).status_code == 200


def test_the_independence_checks_refuse_every_party_and_original_reviewer(queue):
    """The service's own guard (``check_decider``), checked directly: the supervisor
    routes already hide the appellant and every original reviewer, so through HTTP only
    an admin who became the call's agent reaches it (see the attribution test)."""
    from backend.database.relational import get_session_factory
    from backend.database.tenant_context import bind_tenant_context
    from backend.services.review_appeal_service import ReviewQueueError, check_decider

    target = reviewed(queue, admin_decides=True)
    appeal = filed(queue.agent, target.id)
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=queue.workspace_id)
        row = db.get(ReviewAppeal, appeal["id"])
        meeting = db.get(Meeting, target.meeting_id)
        for persona, error in (
            (queue.agent, "appeal_decider_is_party"),
            (queue.sup, "appeal_decider_is_original_reviewer"),
            (queue.admin, "appeal_decider_is_original_reviewer"),
        ):
            with pytest.raises(ReviewQueueError) as refused:
                check_decider(db, row, meeting, user_id=persona.user_id)
            assert refused.value.code == error, persona.user_id
        # Allow-controls.
        for persona in (queue.owner, queue.plain):
            check_decider(db, row, meeting, user_id=persona.user_id)


def test_decide_authority_follows_the_calls_current_team(queue):
    target, appeal = ready(queue)
    good = [uphold("greeting"), uphold("fee_explained"), uphold("off_topic")]
    moved = queue.owner.patch(
        f"/api/v1/meetings/{target.meeting_id}", json={"team_id": queue.team_b}
    )
    assert moved.status_code == 200, moved.text
    before = counts()
    # The assignee supervises team A only: the appeal is gone for them.
    assert_refused(decide(queue.plain, appeal, good), 404, "review_appeal_not_found", before)
    # Team B's supervisor sees it now, but is not the assignee.
    assert_refused(decide(queue.sup_b, appeal, good), 403, "review_appeal_not_assignee", before)
    appeal = assign(queue.owner, appeal, queue.sup_b)
    assert decide(queue.sup_b, appeal, good).status_code == 200


def test_the_decide_body_rules(queue):
    target, appeal = ready(queue)
    before = counts()
    full = [uphold("greeting"), uphold("fee_explained"), uphold("off_topic")]
    for decisions in (
        full[:2],  # one appealed criterion missing
        [*full, uphold("closing")],  # a criterion that was not appealed
        [*full, uphold("greeting")],  # a criterion twice
        None,
        "uphold",
        [*full[:2], "off_topic"],
        [*full[:2], {"decision": "uphold"}],
    ):
        assert_refused(
            decide(queue.plain, appeal, decisions), 422, "appeal_decisions_incomplete", before
        )
    # SPEC-pr2 R3: a value with no code of its own.
    for entry in (
        {"criterion_id": "off_topic", "decision": "reject"},
        uphold("off_topic", decided_outcome="passed"),
        uphold("off_topic", reason_code="other"),
        uphold("off_topic", note="x" * 2001),
        uphold("off_topic", note=5),
        uphold("off_topic", extra="x"),
        change("off_topic", "unknown"),
        change("off_topic", "passed", note="x" * 2001),
    ):
        assert_refused(
            decide(queue.plain, appeal, [*full[:2], entry]), 422, "appeal_decision_invalid", before
        )
    # A change equal to the appealed (human) outcome.
    assert_refused(
        decide(queue.plain, appeal, [*full[:2], change("off_topic", "failed")]),
        422,
        "appeal_change_matches",
        before,
    )
    # not_applicable needs na_allowed: greeting does not allow it, off_topic does.
    assert_refused(
        decide(queue.plain, appeal, [change("greeting", "not_applicable"), *full[1:]]),
        422,
        "reviewer_na_not_allowed",
        before,
    )
    for extra in (
        {"reason_code": None},
        {"reason_code": "because"},
        {"note": None},
        {"note": "   "},
    ):
        assert_refused(
            decide(queue.plain, appeal, [*full[:2], change("off_topic", "passed", **extra)]),
            422,
            "override_reason_required",
            before,
        )
    for note in ("", "  ", None, "x" * 2001, 3):
        assert_refused(
            decide(queue.plain, appeal, full, disposition_note=note),
            422,
            "appeal_disposition_note_required",
            before,
        )
    # Allow-control: off_topic changed to not_applicable (it allows it).
    body = decide(queue.plain, appeal, [*full[:2], change("off_topic", "not_applicable")]).json()
    assert body["disposition"] == "partially_granted"


def test_a_decided_appeal_is_closed(queue):
    target, appeal = ready(queue, ("greeting",))
    decided_appeal = decide(queue.plain, appeal, [uphold("greeting")]).json()
    before = counts()
    again = decide(queue.plain, decided_appeal, [uphold("greeting")])
    assert_refused(again, 409, "review_appeal_closed", before)
    withdraw = act(queue.agent, decided_appeal, "withdraw")
    assert_refused(withdraw, 409, "review_appeal_closed", before)


# --- AC7: withdraw ------------------------------------------------------------------------------


def test_withdraw_is_for_the_appellant_only(queue):
    target, appeal = ready(queue)
    before = counts()
    # Not visible at all: another agent and the original reviewer.
    for persona in (queue.lapsed, queue.sup):
        assert_refused(act(persona, appeal, "withdraw"), 404, "review_appeal_not_found", before)
    # Visible as a reviewer, not the appellant.
    for persona in (queue.owner, queue.plain):
        assert_refused(act(persona, appeal, "withdraw"), 403, "appeal_not_appellant", before)
    stale = act(queue.agent, {**appeal, "revision": 1}, "withdraw")
    assert_refused(stale, 409, "review_appeal_revision_conflict", before)
    body = acted_on(queue.agent, appeal, "withdraw")
    assert (body["status"], body["revision"], body["view"]) == ("withdrawn", 3, "appellant")
    assert body["withdrawn_at"] is not None
    assert [event.event_type for event in appeal_events(appeal["id"])][-1] == (
        "review_appeal.withdrawn"
    )


# --- AC8: attribution changes -------------------------------------------------------------------


def test_a_new_agent_can_neither_be_assigned_nor_decide_and_the_appellant_can_withdraw(queue):
    target = reviewed(queue)
    appeal = filed(queue.agent, target.id, ["greeting"])
    appeal = assign(queue.owner, appeal, queue.admin)
    set_agent(queue.owner, target.meeting_id, queue.admin)
    before = counts()
    # The admin, now the call's agent, cannot decide.
    assert_refused(
        decide(queue.admin, appeal, [uphold("greeting")]), 422, "appeal_decider_is_party", before
    )
    # The new agent (a member) cannot be assigned either.
    set_agent(queue.owner, target.meeting_id, queue.lapsed)
    before = counts()
    assert_refused(
        act(queue.owner, appeal, "assign", assignee_user_id=queue.lapsed.user_id),
        422,
        "appeal_assignee_is_party",
        before,
    )
    # The old appellant is still a party.
    assert_refused(
        act(queue.owner, appeal, "assign", assignee_user_id=queue.agent.user_id),
        422,
        "appeal_assignee_is_party",
        before,
    )
    # /my-reviews follows the new agent, and the old appellant can still withdraw.
    assert [row["review_item_id"] for row in my_reviews(queue.lapsed)] == [target.id]
    assert my_reviews(queue.agent) == []
    assert acted_on(queue.agent, appeal, "withdraw")["status"] == "withdrawn"


# --- AC10: audit history ------------------------------------------------------------------------


def test_each_change_writes_one_event_and_the_history_lists_them_oldest_first(queue):
    target = reviewed(queue)
    appeal = filed(queue.agent, target.id, ["greeting", "off_topic"])
    appeal = assign(queue.owner, appeal, queue.admin)
    appeal = assign(queue.owner, appeal, queue.plain)
    assign(queue.owner, appeal, queue.plain)  # the same assignee: no event
    decide(queue.plain, appeal, [uphold("greeting"), change("off_topic", "passed")])
    events = appeal_events(appeal["id"])
    assert [event.event_type for event in events] == [
        "review_appeal.filed",
        "review_appeal.assigned",
        "review_appeal.assigned",
        "review_appeal.decided",
    ]
    assigned = events[2].details
    assert assigned == {
        "assignee_user_id_before": queue.admin.user_id,
        "assignee_user_id_after": queue.plain.user_id,
        "revision": 3,
    }
    with inspection_session_factory()() as db:
        result_id = db.scalar(select(ReviewAppealResult.id))
    assert events[3].details == {
        "disposition": "partially_granted",
        "upheld_count": 1,
        "changed_count": 1,
        "human_outcome": "passed",
        "final_outcome": "passed",
        "appeal_result_id": result_id,
        "revision": 4,
    }
    every_detail = json.dumps([event.details for event in events])
    for note in (REASON, DISPOSITION, "The evidence is in turn 2."):
        assert note not in every_detail
    # The supervisor history: every actor and every id.
    history = queue.owner.get(f"{APPEALS}/{appeal['id']}/history")
    assert history.status_code == 200, history.text
    assert history.json()["next_cursor"] is None
    items = history.json()["items"]
    assert [item["event_type"] for item in items] == [event.event_type for event in events]
    assert [item["actor_user_id"] for item in items] == [
        queue.agent.user_id,
        queue.owner.user_id,
        queue.owner.user_id,
        queue.plain.user_id,
    ]
    assert [item["actor_role"] for item in items] == [
        "appellant",
        "reviewer",
        "reviewer",
        "reviewer",
    ]
    assert items[2]["details"] == assigned
    # The appellant's history: only their own actor id, no user id in any details.
    mine = queue.agent.get(f"{APPEALS}/{appeal['id']}/history").json()["items"]
    assert [item["actor_user_id"] for item in mine] == [queue.agent.user_id, None, None, None]
    assert [item["actor_role"] for item in mine] == [
        "appellant",
        "reviewer",
        "reviewer",
        "reviewer",
    ]
    assert not everyone_but(queue) & _ids_in([item["details"] for item in mine])
    assert mine[2]["details"] == {"revision": 3}
    # Anyone else: 404.
    for persona in (queue.sup, queue.lapsed):
        response = persona.get(f"{APPEALS}/{appeal['id']}/history")
        assert response.status_code == 404 and code(response) == "review_appeal_not_found"


def test_no_agent_view_holds_a_reviewer_id(queue):
    target = reviewed(queue)
    appeal = filed(queue.agent, target.id, ["greeting"])
    appeal = assign(queue.owner, appeal, queue.plain)
    decide(queue.plain, appeal, [change("greeting", "failed")])
    reviewers = everyone_but(queue, queue.agent)
    for path in (
        MY_REVIEWS,
        f"{MY_REVIEWS}/{target.id}",
        MY_APPEALS,
        f"{APPEALS}/{appeal['id']}",
        f"{APPEALS}/{appeal['id']}/history",
    ):
        response = queue.agent.get(path)
        assert response.status_code == 200, (path, response.text)
        assert not reviewers & _ids_in(response.json()), path
    # Control: the supervisor view names the decider.
    supervisor = queue.owner.get(f"{APPEALS}/{appeal['id']}").json()
    assert supervisor["decided_by_user_id"] == queue.plain.user_id
    assert supervisor["team_id"] == queue.team_a
