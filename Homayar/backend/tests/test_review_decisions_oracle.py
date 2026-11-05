"""HOM-74 PR 2 oracle: criterion accept and override, and the human result on completion.

Written from the team SPEC before the implementation was visible.  Every refusal is
paired with the closest request that must still succeed, and every refusal must write
nothing: no item change, no decision row, no human result and no audit event.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

from backend.conversation_checks import CheckTurn
from backend.database.saas_models import (
    AuditEvent,
    CriterionResultRecord,
    ReviewCriterionDecision,
    ReviewItem,
    ReviewResultRecord,
    ScorecardResultRecord,
    ScorecardVersion,
    TranscriptRevision,
    TranscriptSource,
)
from backend.scorecards import SCORECARDS_VERSION, ConversationFacts, score_conversation
from backend.security.authorization import API_KEY_SCOPES
from backend.services.audit_service import AUDIT_EVENT_CATALOG
from backend.services.scorecard_result_service import record_result, verify_result
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import MATRIX_DOC, _cookies, _doc_block, _Persona
from backend.tests.test_review_queue_oracle import (
    ITEMS,
    _code,
    _race,
    _refused,
    _requires_postgres,
    _set_meeting,
    _signup,
    _World,
)
from backend.tests.test_scorecard_result_service import (
    NOW,
    _criterion,
    _run,
    _session,
    _tamper,
    _version,
)
from backend.tests.test_scorecard_results_migration import _add

SEGMENTS = 24
TURNS = [
    CheckTurn("hello and welcome to the shop", "agent", 0, 1000, 0.9),
    CheckTurn("I want to change my plan", "customer", 1000, 2000, 0.9),
    *(
        CheckTurn(
            f"let me look at option {index}", "agent", 2000 + index * 1000, 2900 + index * 1000, 0.9
        )
        for index in range(SEGMENTS - 3)
    ),
    CheckTurn("thank you, goodbye", "agent", 30000, 31000, 0.9),
]
DEFINITION = {
    "name": "Reviewed sales call",
    "pass_threshold": 60,
    "criteria": [
        _criterion("greeting", 20, "hello"),
        _criterion("closing", 20, "goodbye", na_allowed=True),
        _criterion("fee_explained", 20, "cancellation fee", auto_fail=True),
        _criterion("agenda", 20, "agenda", applies_to={"conversation_types": ["meeting"]}),
        _criterion("callback", 20, "call you back", applies_to={"directions": ["inbound"]}),
    ],
}
OTHER_DEFINITION = {
    "name": "Another card",
    "pass_threshold": 50,
    "criteria": [_criterion("empathy", 100, "sorry")],
}
CRITERIA = ("greeting", "closing", "fee_explained", "agenda", "callback")
MACHINE = {
    "greeting": "passed",
    "closing": "passed",
    "fee_explained": "failed",
    "agenda": "not_applicable",
    "callback": "insufficient_evidence",
}
REASONS = (
    "evidence_missed",
    "evidence_misread",
    "transcript_error",
    "context_outside_call",
    "policy_exception",
    "other",
)
ACCEPT = {"decision": "accept"}
VIEW_KEYS = {
    "review_item_id",
    "revision",
    "status",
    "scorecard_result_id",
    "machine",
    "human",
    "criteria",
}
SCORE_KEYS = (
    "outcome",
    "reason",
    "complete",
    "score",
    "score_min",
    "score_max",
    "pass_threshold",
    "applicable_weight",
    "earned_weight",
    "failed_weight",
    "insufficient_weight",
)
CRITERION_KEYS = {
    "criterion_id",
    "position",
    "weight",
    "auto_fail",
    "na_allowed",
    "reviewable",
    "machine",
    "decision",
}
CRITERION_MACHINE_KEYS = (
    "outcome",
    "reason",
    "decided_by",
    "review_required",
    "review_reasons",
    "evidence",
)
DECISION_KEYS = {
    "decision",
    "reviewer_outcome",
    "reason_code",
    "note",
    "evidence",
    "decided_by_user_id",
    "updated_at",
}
DECIDED_KEYS = {
    "criterion_id",
    "scorecard_result_id",
    "decision",
    "machine_outcome",
    "reviewer_outcome",
    "reason_code",
    "evidence_turn_indexes",
    "previous_decision",
    "previous_reviewer_outcome",
    "revision",
}
DECISION_CODES = (
    "review_item_not_found",
    "review_item_revision_conflict",
    "review_item_closed",
    "review_not_assignee",
    "review_assignee_is_agent",
    "review_item_state_invalid",
    "review_item_has_no_result",
    "scorecard_version_unsupported",
    "criterion_not_found",
    "criterion_not_reviewable",
    "review_decision_invalid",
    "criterion_accept_not_possible",
    "override_matches_machine",
    "reviewer_na_not_allowed",
    "override_reason_required",
    "review_note_invalid",
    "override_evidence_required",
    "review_result_not_computable",
)
RESULTS = ScorecardResultRecord.__table__


def _criteria_path(item_id: str) -> str:
    return f"{ITEMS}/{item_id}/criteria"


def _decision_path(item_id: str, criterion_id: str) -> str:
    return f"{ITEMS}/{item_id}/criteria/{criterion_id}/decision"


def _override(outcome: str, **extra: object) -> dict:
    return {
        "decision": "override",
        "reviewer_outcome": outcome,
        "reason_code": "evidence_missed",
        "note": "Heard it in the call",
        "evidence_turn_indexes": [1],
        **extra,
    }


def _without(body: dict, key: str) -> dict:
    return {name: value for name, value in body.items() if name != key}


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _when(value: str) -> datetime:
    return _aware(datetime.fromisoformat(value))


def _evidence(*indexes: int) -> list[dict]:
    return [{"turn_index": index} for index in indexes]


def _tenant(world: _World) -> dict:
    return {"user_id": world.owner.user_id, "workspace_id": world.workspace_id}


def _version_id(world: _World, key: str, definition: dict) -> str:
    versions = world.__dict__.setdefault("oracle_versions", {})
    if key not in versions:
        versions[key] = _version(_tenant(world), definition, key=key)
    return versions[key]


def _transcript(world: _World, meeting_id: str) -> str:
    with inspection_session_factory()() as db:
        revision = TranscriptRevision(
            workspace_id=world.workspace_id,
            meeting_id=meeting_id,
            revision_number=1,
            source=TranscriptSource.ASR,
            reason="processing",
            language="en",
            segment_count=SEGMENTS,
            is_current=True,
        )
        db.add(revision)
        db.commit()
        return revision.id


def _score(
    world: _World,
    meeting_id: str,
    revision_id: str,
    *,
    key: str = "reviewed",
    definition: dict = DEFINITION,
) -> str:
    tenant = _tenant(world)
    with _session(tenant) as db:
        row, _ = record_result(
            db,
            workspace_id=world.workspace_id,
            meeting_id=meeting_id,
            scorecard_version_id=_version_id(world, key, definition),
            check_run=_run(definition, "call", None, TURNS),
            transcript_revision_id=revision_id,
            now=NOW,
        )
        db.commit()
        return row.id


def _check_view(view: dict) -> dict:
    assert set(view) == VIEW_KEYS, sorted(set(view) ^ VIEW_KEYS)
    if view["machine"] is not None:
        assert set(view["machine"]) == set(SCORE_KEYS), view["machine"]
    if view["human"] is not None:
        assert set(view["human"]) == {*SCORE_KEYS, "final"}, view["human"]
    positions = [criterion["position"] for criterion in view["criteria"]]
    assert positions == sorted(positions), positions
    for criterion in view["criteria"]:
        assert set(criterion) == CRITERION_KEYS, sorted(set(criterion) ^ CRITERION_KEYS)
        assert set(criterion["machine"]) == set(CRITERION_MACHINE_KEYS), criterion["machine"]
        if criterion["decision"] is not None:
            assert set(criterion["decision"]) == DECISION_KEYS, criterion["decision"]
            _when(criterion["decision"]["updated_at"])
    return view


def _entry(view: dict, criterion_id: str) -> dict:
    [entry] = [c for c in view["criteria"] if c["criterion_id"] == criterion_id]
    return entry


def _block(result: dict) -> dict:
    return {key: result[key] for key in SCORE_KEYS}


def _human(view: dict) -> dict:
    return {key: value for key, value in view["human"].items() if key != "final"}


def _audit(workspace_id: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.workspace_id == workspace_id)
                .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
            )
        )


def _seen(workspace_id: str) -> set[str]:
    return {event.id for event in _audit(workspace_id)}


def _since(workspace_id: str, seen: set[str]) -> list[AuditEvent]:
    return [event for event in _audit(workspace_id) if event.id not in seen]


def _every_audit_row() -> str:
    with inspection_session_factory()() as db:
        rows = list(db.scalars(select(AuditEvent)))
        columns = AuditEvent.__mapper__.column_attrs
        return "\n".join(
            json.dumps(
                {column.key: getattr(row, column.key) for column in columns},
                default=str,
                ensure_ascii=False,
                sort_keys=True,
            )
            for row in rows
        )


def _decision_rows(item_id: str) -> list[ReviewCriterionDecision]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(ReviewCriterionDecision)
                .where(ReviewCriterionDecision.review_item_id == item_id)
                .order_by(ReviewCriterionDecision.criterion_id)
            )
        )


def _decision_row(item_id: str, criterion_id: str) -> ReviewCriterionDecision:
    [row] = [row for row in _decision_rows(item_id) if row.criterion_id == criterion_id]
    return row


def _human_results(item_id: str) -> list[ReviewResultRecord]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(ReviewResultRecord).where(ReviewResultRecord.review_item_id == item_id)
            )
        )


def _machine_result(result_id: str) -> ScorecardResultRecord:
    with inspection_session_factory()() as db:
        return db.get(ScorecardResultRecord, result_id)


def _criterion_rows(result_id: str) -> list[CriterionResultRecord]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(CriterionResultRecord)
                .where(CriterionResultRecord.result_id == result_id)
                .order_by(CriterionResultRecord.position)
            )
        )


def _machine_snapshot(result_id: str) -> str:
    row = _machine_result(result_id)
    rows = _criterion_rows(result_id)
    return json.dumps(
        {
            "result": {
                column.key: getattr(row, column.key)
                for column in ScorecardResultRecord.__mapper__.column_attrs
            },
            "criteria": [
                {
                    column.key: getattr(criterion, column.key)
                    for column in CriterionResultRecord.__mapper__.column_attrs
                }
                for criterion in rows
            ],
        },
        default=str,
        sort_keys=True,
    )


def _engine(result_id: str, reviewer_outcomes: dict[str, str]) -> dict:
    with inspection_session_factory()() as db:
        row = db.get(ScorecardResultRecord, result_id)
        document = db.get(ScorecardVersion, row.scorecard_version_id).document
        facts = ConversationFacts(row.conversation_type, row.direction)
        return score_conversation(document, row.check_run, facts, reviewer_outcomes).to_json()


class _Case:
    """A team A call with a stored machine result and its review item."""

    def __init__(
        self,
        world: _World,
        *,
        assignee: str | None = "sup",
        agent: str | None = None,
        start: bool = True,
        scored: bool = True,
    ):
        self.world = world
        self.assignee = assignee
        self.meeting_id = world.new_meeting(agent=agent)
        _set_meeting(self.meeting_id, conversation_type="call", direction=None)
        self.revision_id = _transcript(world, self.meeting_id)
        self.result_id = _score(world, self.meeting_id, self.revision_id) if scored else None
        item = world.create(self.meeting_id, scorecard_result_id=self.result_id)
        if assignee is not None:
            item = world.done(item, "assign", assignee_user_id=world.user_id(assignee))
            if start:
                item = world.done(item, "start", by=assignee)
        self.item = item

    @property
    def id(self) -> str:
        return self.item["id"]

    @property
    def revision(self) -> int:
        return self.item["revision"]

    def refresh(self) -> dict:
        self.item = self.world.get(self.item)
        return self.item

    def act(self, action: str, *, by: str, **body) -> dict:
        self.item = self.world.done(self.item, action, by=by, **body)
        return self.item

    def decide(
        self, criterion_id: str, body: dict, *, by: str | None = None, revision: int | None = None
    ):
        persona = self.world.persona(by or self.assignee)
        expected = self.revision if revision is None else revision
        return persona.post(
            _decision_path(self.id, criterion_id), json={"expected_revision": expected, **body}
        )

    def decided(
        self, criterion_id: str, body: dict, *, by: str | None = None, changes: bool = True
    ) -> dict:
        before = self.revision
        response = self.decide(criterion_id, body, by=by)
        assert response.status_code == 200, (criterion_id, body, response.text)
        view = _check_view(response.json())
        assert view["revision"] == before + (1 if changes else 0), (criterion_id, body)
        assert view["review_item_id"] == self.id
        self.refresh()
        assert self.revision == view["revision"]
        return view

    def state(self) -> tuple:
        with inspection_session_factory()() as db:
            item = db.get(ReviewItem, self.id)
            decisions = [
                (
                    row.criterion_id,
                    row.decision,
                    row.machine_outcome,
                    row.reviewer_outcome,
                    row.reason_code,
                    row.note,
                    json.dumps(row.evidence, sort_keys=True),
                    row.decided_by_user_id,
                    str(row.created_at),
                    str(row.updated_at),
                )
                for row in db.scalars(
                    select(ReviewCriterionDecision)
                    .where(ReviewCriterionDecision.review_item_id == self.id)
                    .order_by(ReviewCriterionDecision.criterion_id)
                )
            ]
            events = db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.workspace_id == self.world.workspace_id)
            )
            human = db.scalar(
                select(func.count())
                .select_from(ReviewResultRecord)
                .where(ReviewResultRecord.review_item_id == self.id)
            )
            return (
                item.revision,
                item.status,
                item.assignee_user_id,
                str(item.updated_at),
                decisions,
                events,
                human,
            )

    def refused(self, criterion_id: str, body: dict, status: int, code: str, **kwargs):
        before = self.state()
        response = self.decide(criterion_id, body, **kwargs)
        _refused(response, status, code)
        assert self.state() == before, (criterion_id, body, code)
        return response

    def malformed(self, criterion_id: str, body: dict, **kwargs) -> None:
        before = self.state()
        response = self.decide(criterion_id, body, **kwargs)
        assert response.status_code == 422, (body, response.text)
        assert self.state() == before, body

    def view(self, *, by: str = "owner") -> dict:
        response = self.world.persona(by).get(_criteria_path(self.id))
        assert response.status_code == 200, response.text
        view = _check_view(response.json())
        assert view["review_item_id"] == self.id
        return view

    def complete_refused(self, code: str) -> None:
        before = self.state()
        response = self.world.act(self.item, "complete", by=self.assignee)
        _refused(response, 409, code)
        assert self.state() == before, code


def _decided_event(event: AuditEvent, case: _Case, *, actor: str, **expected: object) -> None:
    assert (event.event_type, event.target_type, event.target_id) == (
        "review_criterion.decided",
        "review_item",
        case.id,
    )
    assert event.actor_user_id == actor
    assert event.outcome == "success"
    details = dict(event.details)
    assert set(details) == DECIDED_KEYS, sorted(set(details) ^ DECIDED_KEYS)
    wanted = dict(expected)
    assert sorted(details.pop("evidence_turn_indexes") or []) == sorted(
        wanted.pop("evidence_turn_indexes")
    )
    assert details == {"scorecard_result_id": case.result_id, **wanted}


def _three_decisions(case: _Case, notes: dict[str, str]) -> dict[str, str]:
    case.decided("greeting", {"decision": "accept", "note": notes["greeting"]})
    case.decided(
        "fee_explained",
        _override("passed", note=notes["fee_explained"], evidence_turn_indexes=[5, 2]),
    )
    case.decided(
        "callback",
        _override(
            "passed",
            reason_code="context_outside_call",
            note=notes["callback"],
            evidence_turn_indexes=[0],
        ),
    )
    return {"greeting": "passed", "fee_explained": "passed", "callback": "passed"}


# --- who may decide (deny-cases 1 to 8) -----------------------------------------------------


def test_a_viewer_is_refused_and_a_supervising_member_assignee_decides(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup", "viewer")
    case = _Case(world)
    viewer = world.persona("viewer")
    before = case.state()
    _refused(
        viewer.post(
            _decision_path(case.id, "greeting"),
            json={"expected_revision": case.revision, **ACCEPT},
        ),
        403,
        "permission_denied",
    )
    _refused(viewer.get(_criteria_path(case.id)), 403, "permission_denied")
    assert case.state() == before

    view = case.decided("greeting", ACCEPT)
    assert _entry(view, "greeting")["decision"]["decided_by_user_id"] == world.user_id("sup")
    assert case.view(by="sup") == view


def test_a_member_without_authority_and_the_calls_agent_get_404(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup2", "sup_b", "lone", "sup_agent")
    case = _Case(world, agent="sup_agent")
    for name in ("sup_b", "lone", "sup_agent"):
        case.refused("greeting", ACCEPT, 404, "review_item_not_found", by=name)
        _refused(world.persona(name).get(_criteria_path(case.id)), 404, "review_item_not_found")

    case.decided("greeting", ACCEPT, by="sup")
    assert case.view(by="sup2")["revision"] == case.revision


def test_an_admin_assignee_who_is_the_calls_agent_gets_422(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin")
    case = _Case(world, assignee="admin")
    _set_meeting(case.meeting_id, agent_user_id=world.user_id("admin"))
    case.refused("greeting", ACCEPT, 422, "review_assignee_is_agent")
    case.refused("fee_explained", _override("passed"), 422, "review_assignee_is_agent")

    _set_meeting(case.meeting_id, agent_user_id=None)
    case.decided("fee_explained", _override("passed"))


def test_an_item_of_another_workspace_or_a_missing_id_is_404(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    credentials = _signup(client, email_outbox, email="decision-other@example.com", name="O")
    session = login_user(client, credentials)
    other = _Persona(client, session["workspaces"][0]["id"], session, _cookies(client))
    before = case.state()
    _refused(
        other.post(
            _decision_path(case.id, "greeting"),
            json={"expected_revision": case.revision, **ACCEPT},
        ),
        404,
        "review_item_not_found",
    )
    _refused(other.get(_criteria_path(case.id)), 404, "review_item_not_found")
    missing = str(uuid.uuid4())
    _refused(
        world.owner.post(
            _decision_path(missing, "greeting"), json={"expected_revision": 1, **ACCEPT}
        ),
        404,
        "review_item_not_found",
    )
    _refused(world.owner.get(_criteria_path(missing)), 404, "review_item_not_found")
    assert case.state() == before

    case.decided("greeting", ACCEPT)
    assert case.view()["revision"] == case.revision


def test_an_api_key_with_every_key_scope_is_refused(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    scopes = sorted(scope.value for scope in API_KEY_SCOPES)
    minted = _mint(client, world.owner.headers, world.owner.cookies, scopes=scopes)
    assert minted.status_code == 201, minted.text
    key = _bearer(minted.json()["key"], **{"X-Workspace-ID": world.workspace_id})
    before = case.state()
    _refused(
        client.post(
            _decision_path(case.id, "greeting"),
            json={"expected_revision": case.revision, **ACCEPT},
            headers=key,
        ),
        403,
        "api_key_scope_denied",
    )
    _refused(client.get(_criteria_path(case.id), headers=key), 403, "api_key_scope_denied")
    assert case.state() == before

    case.decided("greeting", ACCEPT)
    case.view(by="owner")


def test_a_decision_without_the_csrf_token_is_refused(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    sup = world.persona("sup")
    no_csrf = {"X-Workspace-ID": world.workspace_id}
    before = case.state()
    _refused(
        client.post(
            _decision_path(case.id, "greeting"),
            json={"expected_revision": case.revision, **ACCEPT},
            cookies=sup.cookies,
            headers=no_csrf,
        ),
        403,
        "csrf_failed",
    )
    assert case.state() == before
    read = client.get(_criteria_path(case.id), cookies=sup.cookies, headers=no_csrf)
    assert read.status_code == 200, read.text

    case.decided("greeting", ACCEPT)


def test_only_the_assignee_decides_admins_and_owners_too(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup2")
    case = _Case(world)
    for name in ("owner", "admin", "sup2"):
        case.refused("greeting", ACCEPT, 403, "review_not_assignee", by=name)
        case.refused("fee_explained", _override("passed"), 403, "review_not_assignee", by=name)

    case.decided("greeting", ACCEPT, by="sup")


def test_a_stale_revision_writes_nothing(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    current = case.revision
    for wrong in sorted({current + 1, current + 7, current - 1} - {current}):
        stale = case.refused(
            "fee_explained",
            _override("passed"),
            409,
            "review_item_revision_conflict",
            revision=wrong,
        )
        assert stale.json()["error"]["details"] == {"current_revision": current}
    assert _decision_rows(case.id) == []

    before = dict(case.item)
    case.decided("fee_explained", _override("passed"))
    after = case.item
    assert after["revision"] == current + 1
    kept = set(before) - {"revision", "updated_at"}
    assert {key: after[key] for key in kept} == {key: before[key] for key in kept}
    assert len(_decision_rows(case.id)) == 1


# --- the item and the result (deny-cases 10 to 14) --------------------------------------------


def test_an_open_item_is_refused_until_it_is_started(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world, start=False)
    assert case.item["status"] == "open"
    case.refused("greeting", ACCEPT, 409, "review_item_state_invalid")
    case.refused("fee_explained", _override("passed"), 409, "review_item_state_invalid")

    case.act("start", by="sup")
    case.decided("greeting", ACCEPT)


def test_a_completed_or_cancelled_item_is_closed(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup")
    done = _Case(world)
    done.decided("greeting", ACCEPT)
    done.act("complete", by="sup")
    gone = _Case(world)
    gone.decided("greeting", ACCEPT)
    gone.act("cancel", by="owner", reason="duplicate")
    for case in (done, gone):
        for name in ("sup", "admin"):
            case.refused("fee_explained", _override("passed"), 409, "review_item_closed", by=name)
            case.refused("greeting", ACCEPT, 409, "review_item_closed", by=name)

    _Case(world).decided("fee_explained", _override("passed"))


def test_an_item_without_a_result_has_nothing_to_decide(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    plain = _Case(world, scored=False)
    assert plain.item["scorecard_result_id"] is None
    plain.refused("greeting", ACCEPT, 409, "review_item_has_no_result")
    plain.refused("fee_explained", _override("passed"), 409, "review_item_has_no_result")

    _Case(world).decided("greeting", ACCEPT)


def test_an_unknown_criterion_is_404(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    _score(world, case.meeting_id, case.revision_id, key="other", definition=OTHER_DEFINITION)
    for criterion_id in ("unknown_criterion", "Greeting", "GREETING", "greeting ", "empathy"):
        case.refused(criterion_id, ACCEPT, 404, "criterion_not_found")
        case.refused(criterion_id, _override("failed"), 404, "criterion_not_found")

    case.decided("greeting", ACCEPT)


def test_a_criterion_not_applicable_by_conversation_is_not_reviewable(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    reviewable = {c["criterion_id"]: c["reviewable"] for c in case.view()["criteria"]}
    assert reviewable == {criterion: criterion != "agenda" for criterion in CRITERIA}
    for body in (ACCEPT, _override("passed"), _override("failed"), _override("not_applicable")):
        case.refused("agenda", body, 422, "criterion_not_reviewable")

    case.decided("greeting", _override("failed"))


# --- the body (deny-cases 15 to 21) -----------------------------------------------------------


def test_insufficient_evidence_is_never_accepted_only_overridden(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    case.refused("callback", ACCEPT, 422, "criterion_accept_not_possible")
    case.refused(
        "callback",
        {"decision": "accept", "note": "Looks fine"},
        422,
        "criterion_accept_not_possible",
    )

    view = case.decided("callback", _override("passed"))
    decision = _entry(view, "callback")["decision"]
    assert (decision["decision"], decision["reviewer_outcome"]) == ("override", "passed")
    assert _decision_row(case.id, "callback").machine_outcome == "insufficient_evidence"


def test_an_accept_carries_no_reason_code_and_no_evidence(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    for extra in (
        {"reason_code": "other"},
        {"reason_code": "evidence_missed", "note": "Fine"},
        {"reason_code": ""},
        {"evidence_turn_indexes": [0]},
        {"evidence_turn_indexes": []},
        {"reviewer_outcome": "failed"},
        {"reviewer_outcome": "not_applicable"},
        {"reviewer_outcome": "Passed"},
    ):
        case.refused("greeting", {**ACCEPT, **extra}, 422, "review_decision_invalid")

    view = case.decided("greeting", {"decision": "accept", "note": "  Greeting was clear  "})
    assert _entry(view, "greeting")["decision"]["note"] == "Greeting was clear"
    case.decided(
        "greeting",
        {
            "decision": "accept",
            "reviewer_outcome": "passed",
            "reason_code": None,
            "evidence_turn_indexes": None,
            "note": "Second look",
        },
    )
    view = case.decided("greeting", {"decision": "accept", "note": " \n\t "})
    decision = _entry(view, "greeting")["decision"]
    assert (decision["reviewer_outcome"], decision["reason_code"], decision["note"]) == (
        "passed",
        None,
        None,
    )
    assert decision["evidence"] == []
    row = _decision_row(case.id, "greeting")
    assert (row.decision, row.machine_outcome, row.reviewer_outcome, row.note, row.evidence) == (
        "accept",
        "passed",
        "passed",
        None,
        [],
    )


def test_an_override_must_change_the_machine_outcome(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    for criterion_id, outcome in (
        ("greeting", "passed"),
        ("closing", "passed"),
        ("fee_explained", "failed"),
    ):
        case.refused(criterion_id, _override(outcome), 422, "override_matches_machine")

    case.decided("greeting", _override("failed"))
    case.decided("fee_explained", _override("passed"))


def test_not_applicable_needs_na_allowed(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    for criterion_id in ("greeting", "fee_explained", "callback"):
        case.refused(criterion_id, _override("not_applicable"), 422, "reviewer_na_not_allowed")

    view = case.decided("closing", _override("not_applicable"))
    assert _entry(view, "closing")["decision"]["reviewer_outcome"] == "not_applicable"
    assert _entry(view, "closing")["na_allowed"] is True


def test_an_override_needs_a_known_reason_code_and_a_note(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    body = _override("passed")
    refused = [_without(body, "reason_code"), _without(body, "note")]
    refused += [
        {**body, "reason_code": code}
        for code in (None, "", "Other", "EVIDENCE_MISSED", "unknown_reason", " other")
    ]
    refused += [{**body, "note": note} for note in (None, "", "   ", "\n\t ")]
    for sent in refused:
        case.refused("fee_explained", sent, 422, "override_reason_required")

    for code in REASONS:
        view = case.decided("fee_explained", {**body, "reason_code": code})
        assert _entry(view, "fee_explained")["decision"]["reason_code"] == code
    case.decided("fee_explained", {**body, "note": "x"})


def test_the_note_is_at_most_2000_characters_after_strip(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    for note in ("n" * 2001, " " + "n" * 2001 + " "):
        case.refused("fee_explained", _override("passed", note=note), 422, "review_note_invalid")
        case.refused("greeting", {**ACCEPT, "note": note}, 422, "review_note_invalid")

    case.decided("fee_explained", _override("passed", note="n" * 2000))
    view = case.decided("fee_explained", _override("passed", note="  " + "m" * 2000 + "\n"))
    assert _entry(view, "fee_explained")["decision"]["note"] == "m" * 2000
    assert _decision_row(case.id, "fee_explained").note == "m" * 2000
    view = case.decided("greeting", {**ACCEPT, "note": "a" * 2000})
    assert _entry(view, "greeting")["decision"]["note"] == "a" * 2000


def test_override_evidence_is_1_to_20_distinct_turns_of_the_transcript(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    body = _override("passed")
    refused = [_without(body, "evidence_turn_indexes")]
    refused += [
        {**body, "evidence_turn_indexes": value}
        for value in (
            None,
            [],
            list(range(21)),
            [1, 1],
            [3, 4, 3],
            [-1],
            [True],
            [0, True],
            [1.5],
            ["1"],
            [SEGMENTS],
            [0, SEGMENTS + 3],
        )
    ]
    for sent in refused:
        case.refused("fee_explained", sent, 422, "override_evidence_required")

    for indexes, stored in (
        ([0], _evidence(0)),
        ([SEGMENTS - 1], _evidence(SEGMENTS - 1)),
        (list(range(20)), _evidence(*range(20))),
        ([9, 3, 7], _evidence(3, 7, 9)),
    ):
        view = case.decided("fee_explained", {**body, "evidence_turn_indexes": indexes})
        assert _entry(view, "fee_explained")["decision"]["evidence"] == stored
        assert _decision_row(case.id, "fee_explained").evidence == stored


def test_unknown_decision_and_reviewer_outcome_spellings(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    for decision in ("Accept", "ACCEPT", "approve", "", "accept "):
        case.refused("greeting", {"decision": decision}, 422, "review_decision_invalid")
    for decision in ("Override", "OVERRIDE", "override ", "reject"):
        case.refused(
            "fee_explained",
            {**_override("passed"), "decision": decision},
            422,
            "review_decision_invalid",
        )
    for outcome in ("Passed", "PASSED", "pass", "", "insufficient_evidence", None):
        case.refused(
            "fee_explained",
            _override("passed", reviewer_outcome=outcome),
            422,
            "review_decision_invalid",
        )
    case.refused(
        "fee_explained",
        _without(_override("passed"), "reviewer_outcome"),
        422,
        "review_decision_invalid",
    )
    case.malformed("greeting", {})
    case.malformed("greeting", {"decision": None})
    before = case.state()
    missing_revision = world.persona("sup").post(_decision_path(case.id, "greeting"), json=ACCEPT)
    assert missing_revision.status_code == 422, missing_revision.text
    assert case.state() == before

    case.decided("greeting", ACCEPT)
    case.decided("fee_explained", _override("passed"))


def test_the_checks_run_in_the_designed_order(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup2")
    closed = _Case(world)
    closed.act("complete", by="sup")
    closed.refused(
        "greeting", ACCEPT, 409, "review_item_revision_conflict", revision=closed.revision - 1
    )
    closed.refused("greeting", ACCEPT, 409, "review_item_closed", by="admin")

    waiting = _Case(world, start=False)
    waiting.refused("unknown_criterion", {"decision": "bad"}, 403, "review_not_assignee", by="sup2")
    waiting.refused("unknown_criterion", {"decision": "bad"}, 409, "review_item_state_invalid")

    own = _Case(world, assignee="admin", start=False)
    _set_meeting(own.meeting_id, agent_user_id=world.user_id("admin"))
    own.refused("unknown_criterion", {"decision": "bad"}, 422, "review_assignee_is_agent")

    plain = _Case(world, scored=False)
    plain.refused("unknown_criterion", {"decision": "bad"}, 409, "review_item_has_no_result")

    case = _Case(world)
    _tamper(RESULTS, RESULTS.c.id == case.result_id, scorecards_version="scorecards/0")
    case.refused("unknown_criterion", {"decision": "bad"}, 409, "scorecard_version_unsupported")
    _tamper(RESULTS, RESULTS.c.id == case.result_id, scorecards_version=SCORECARDS_VERSION)
    case.refused("unknown_criterion", {"decision": "bad"}, 404, "criterion_not_found")
    case.refused("agenda", {"decision": "bad"}, 422, "criterion_not_reviewable")
    case.refused("greeting", {"decision": "bad"}, 422, "review_decision_invalid")

    case.decided("greeting", ACCEPT)


# --- the write, the response and the audit event (deny-case 22) ------------------------------


def test_a_decision_row_records_the_machine_outcome_the_caller_and_the_time(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    sup = world.user_id("sup")
    started = datetime.now(UTC) - timedelta(seconds=5)
    view = case.decided(
        "fee_explained",
        _override(
            "passed", note="\n  The fee was said in turn 9  \t", evidence_turn_indexes=[9, 3, 7]
        ),
    )
    finished = datetime.now(UTC) + timedelta(seconds=5)
    assert case.view(by="sup") == view

    row = _decision_row(case.id, "fee_explained")
    assert (
        row.workspace_id,
        row.meeting_id,
        row.scorecard_result_id,
        row.decision,
        row.machine_outcome,
        row.reviewer_outcome,
        row.reason_code,
        row.note,
        row.evidence,
        row.decided_by_user_id,
    ) == (
        world.workspace_id,
        case.meeting_id,
        case.result_id,
        "override",
        "failed",
        "passed",
        "evidence_missed",
        "The fee was said in turn 9",
        _evidence(3, 7, 9),
        sup,
    )
    assert started <= _aware(row.created_at) <= finished
    assert started <= _aware(row.updated_at) <= finished
    assert _entry(view, "fee_explained")["decision"] == {
        "decision": "override",
        "reviewer_outcome": "passed",
        "reason_code": "evidence_missed",
        "note": "The fee was said in turn 9",
        "evidence": _evidence(3, 7, 9),
        "decided_by_user_id": sup,
        "updated_at": _entry(view, "fee_explained")["decision"]["updated_at"],
    }
    assert _when(_entry(view, "fee_explained")["decision"]["updated_at"]) == _aware(row.updated_at)
    for criterion_id in ("greeting", "closing", "agenda", "callback"):
        assert _entry(view, criterion_id)["decision"] is None

    view = case.decided(
        "fee_explained",
        _override("passed", reason_code="other", note="Changed my mind", evidence_turn_indexes=[2]),
    )
    updated = _decision_row(case.id, "fee_explained")
    assert updated.id == row.id
    assert _aware(updated.created_at) == _aware(row.created_at)
    assert _aware(updated.updated_at) >= _aware(row.updated_at)
    assert (updated.reason_code, updated.note, updated.evidence) == (
        "other",
        "Changed my mind",
        _evidence(2),
    )
    assert len(_decision_rows(case.id)) == 1

    case.decided("greeting", {"decision": "accept", "note": "  ok  "})
    accepted = _decision_row(case.id, "greeting")
    assert (
        accepted.decision,
        accepted.machine_outcome,
        accepted.reviewer_outcome,
        accepted.reason_code,
        accepted.note,
        accepted.evidence,
        accepted.decided_by_user_id,
    ) == ("accept", "passed", "passed", None, "ok", [], sup)
    assert len(_decision_rows(case.id)) == 2


def test_an_identical_decision_writes_nothing_and_a_changed_one_writes_one_event(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    sup = world.user_id("sup")
    seen = _seen(world.workspace_id)
    body = _override("passed", note="  same note  ", evidence_turn_indexes=[5, 2])
    first = case.decided("fee_explained", body)
    [event] = _since(world.workspace_id, seen)
    _decided_event(
        event,
        case,
        actor=sup,
        criterion_id="fee_explained",
        decision="override",
        machine_outcome="failed",
        reviewer_outcome="passed",
        reason_code="evidence_missed",
        evidence_turn_indexes=[2, 5],
        previous_decision=None,
        previous_reviewer_outcome=None,
        revision=case.revision,
    )

    seen = _seen(world.workspace_id)
    state = case.state()
    for same in (body, {**body, "note": "same note", "evidence_turn_indexes": [2, 5]}):
        again = case.decided("fee_explained", same, changes=False)
        assert again == first
    assert case.state() == state
    assert _since(world.workspace_id, seen) == []
    case.refused(
        "fee_explained", body, 409, "review_item_revision_conflict", revision=case.revision - 1
    )

    case.decided("fee_explained", {**body, "note": "another note"})
    [event] = _since(world.workspace_id, seen)
    _decided_event(
        event,
        case,
        actor=sup,
        criterion_id="fee_explained",
        decision="override",
        machine_outcome="failed",
        reviewer_outcome="passed",
        reason_code="evidence_missed",
        evidence_turn_indexes=[2, 5],
        previous_decision="override",
        previous_reviewer_outcome="passed",
        revision=case.revision,
    )

    seen = _seen(world.workspace_id)
    case.decided("greeting", ACCEPT)
    case.decided("greeting", {**ACCEPT, "reviewer_outcome": "passed"}, changes=False)
    case.decided("greeting", _override("failed", reason_code="other", evidence_turn_indexes=[0]))
    accepted, overridden = _since(world.workspace_id, seen)
    _decided_event(
        accepted,
        case,
        actor=sup,
        criterion_id="greeting",
        decision="accept",
        machine_outcome="passed",
        reviewer_outcome="passed",
        reason_code=None,
        evidence_turn_indexes=[],
        previous_decision=None,
        previous_reviewer_outcome=None,
        revision=case.revision - 1,
    )
    _decided_event(
        overridden,
        case,
        actor=sup,
        criterion_id="greeting",
        decision="override",
        machine_outcome="passed",
        reviewer_outcome="failed",
        reason_code="other",
        evidence_turn_indexes=[0],
        previous_decision="accept",
        previous_reviewer_outcome="passed",
        revision=case.revision,
    )


@pytest.mark.postgres
def test_two_concurrent_decisions_on_one_item_give_one_200_and_one_409(
    client, email_outbox, monkeypatch
):
    _requires_postgres()
    world = _World(client, email_outbox, monkeypatch, "sup")
    sup = world.persona("sup")
    pairs = (
        (("greeting", ACCEPT), ("closing", ACCEPT)),
        (
            ("fee_explained", _override("passed")),
            ("fee_explained", _override("passed", reason_code="other")),
        ),
        (("callback", _override("passed")), ("greeting", _override("failed"))),
    )
    for pair in pairs:
        case = _Case(world)
        revision = case.revision
        seen = _seen(world.workspace_id)
        sends = [
            (sup, _decision_path(case.id, criterion_id), {"expected_revision": revision, **body})
            for criterion_id, body in pair
        ]
        results = _race(client, sends)
        assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
        [conflict] = [r for r in results if r.status_code == 409]
        assert _code(conflict) == "review_item_revision_conflict"
        assert case.refresh()["revision"] == revision + 1
        assert len(_decision_rows(case.id)) == 1
        events = _since(world.workspace_id, seen)
        assert [event.event_type for event in events] == ["review_criterion.decided"]


# --- completion and the human result (deny-case 24, positive criteria 1 to 6) ----------------


def test_completion_stores_the_engine_result_of_the_decisions_and_keeps_the_machine_rows(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    machine_before = _machine_snapshot(case.result_id)
    machine = _machine_result(case.result_id)
    assert (machine.outcome, machine.reason) == ("failed", "auto_fail")

    outcomes = _three_decisions(
        case, {"greeting": "Fine", "fee_explained": "Fee said", "callback": "Will email"}
    )
    expected = _engine(case.result_id, outcomes)
    preview = case.view(by="sup")
    assert preview["status"] == "in_review"
    assert preview["scorecard_result_id"] == case.result_id
    assert preview["machine"] == _block(machine.result)
    assert preview["human"] == {**_block(expected), "final": False}
    assert (preview["machine"]["outcome"], preview["human"]["outcome"]) == ("failed", "passed")
    assert _entry(preview, "fee_explained")["machine"]["outcome"] == "failed"
    assert _entry(preview, "fee_explained")["decision"]["reviewer_outcome"] == "passed"

    completed = case.act("complete", by="sup")
    assert completed["status"] == "completed"
    [human] = _human_results(case.id)
    assert human.result == expected
    assert human.reviewer_outcomes == outcomes
    assert human.scorecards_version == SCORECARDS_VERSION == expected["scorecards_version"]
    assert (
        human.workspace_id,
        human.meeting_id,
        human.review_item_id,
        human.scorecard_result_id,
    ) == (
        world.workspace_id,
        case.meeting_id,
        case.id,
        case.result_id,
    )
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
        assert getattr(human, column) == expected[column], column

    stored = case.view(by="sup")
    assert stored["status"] == "completed"
    assert stored["revision"] == completed["revision"]
    assert stored["human"] == {**_block(human.result), "final": True}
    assert _human(stored) == _human(preview)
    assert stored["machine"] == preview["machine"]
    assert stored["criteria"] == preview["criteria"]

    assert _machine_snapshot(case.result_id) == machine_before
    with _session(_tenant(world)) as db:
        assert verify_result(db, workspace_id=world.workspace_id, result_id=case.result_id) is True


def test_the_completed_event_matches_the_human_result_and_no_audit_row_holds_a_note(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    tokens = {
        name: f"note-{name}-{uuid.uuid4().hex}"
        for name in ("greeting", "fee_explained", "callback")
    }
    _three_decisions(
        case, {name: f"The reviewer wrote {token} here" for name, token in tokens.items()}
    )
    seen = _seen(world.workspace_id)
    completed = case.act("complete", by="sup")
    [event] = _since(world.workspace_id, seen)
    [human] = _human_results(case.id)
    machine = _machine_result(case.result_id)
    assert (event.event_type, event.target_type, event.target_id, event.actor_user_id) == (
        "review_item.completed",
        "review_item",
        case.id,
        world.user_id("sup"),
    )
    assert event.details == {
        "revision": completed["revision"],
        "scorecard_result_id": case.result_id,
        "review_result_id": human.id,
        "machine_outcome": machine.outcome,
        "machine_reason": machine.reason,
        "human_outcome": human.outcome,
        "human_reason": human.reason,
        "applicable_weight": human.applicable_weight,
        "earned_weight": human.earned_weight,
        "failed_weight": human.failed_weight,
        "insufficient_weight": human.insufficient_weight,
        "accepted_count": 1,
        "overridden_count": 2,
    }
    assert (event.details["machine_outcome"], event.details["human_outcome"]) == (
        "failed",
        "passed",
    )

    decided = [e for e in _audit(world.workspace_id) if e.event_type == "review_criterion.decided"]
    assert len(decided) == 3
    every_row = _every_audit_row()
    for token in tokens.values():
        assert token not in every_row


def test_no_result_completes_without_a_human_result_and_cancel_keeps_the_decisions(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup")
    plain = _Case(world, scored=False)
    view = plain.view(by="sup")
    assert (view["scorecard_result_id"], view["machine"], view["human"], view["criteria"]) == (
        None,
        None,
        None,
        [],
    )
    seen = _seen(world.workspace_id)
    completed = plain.act("complete", by="sup")
    [event] = _since(world.workspace_id, seen)
    assert (event.event_type, event.details) == (
        "review_item.completed",
        {"revision": completed["revision"]},
    )
    assert _human_results(plain.id) == []
    after = plain.view(by="sup")
    assert (after["status"], after["machine"], after["human"], after["criteria"]) == (
        "completed",
        None,
        None,
        [],
    )

    scored = _Case(world)
    scored.decided("fee_explained", _override("passed", note="Kept after cancel"))
    scored.decided("greeting", ACCEPT)
    kept = scored.state()[4]
    scored.act("cancel", by="owner", reason="duplicate")
    assert _human_results(scored.id) == []
    assert scored.state()[4] == kept
    cancelled = scored.view(by="sup")
    assert cancelled["status"] == "cancelled"
    assert cancelled["human"] is None
    assert cancelled["machine"] == _block(_machine_result(scored.result_id).result)
    assert _entry(cancelled, "fee_explained")["decision"]["note"] == "Kept after cancel"
    assert _entry(cancelled, "greeting")["decision"]["decision"] == "accept"


def test_reassignment_keeps_the_decisions_and_completion_approves_them(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup2")
    case = _Case(world)
    fee = _override("passed", note="Fee said at the end")
    case.decided("fee_explained", fee)
    case.act("assign", by="owner", assignee_user_id=world.user_id("sup2"))
    assert case.item["status"] == "open"
    kept = _entry(case.view(), "fee_explained")["decision"]
    assert kept["decided_by_user_id"] == world.user_id("sup")
    case.refused("greeting", ACCEPT, 403, "review_not_assignee", by="sup")
    case.refused("greeting", ACCEPT, 409, "review_item_state_invalid", by="sup2")

    case.act("start", by="sup2")
    case.assignee = "sup2"
    case.decided("fee_explained", fee, changes=False)
    assert _entry(case.view(), "fee_explained")["decision"] == kept
    case.decided(
        "callback", _override("failed", reason_code="policy_exception", note="No call back offered")
    )
    case.act("complete", by="sup2")
    outcomes = {"fee_explained": "passed", "callback": "failed"}
    [human] = _human_results(case.id)
    assert human.reviewer_outcomes == outcomes
    assert human.result == _engine(case.result_id, outcomes)


def test_complete_and_decide_refuse_an_unsupported_scorecards_version(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    case.decided("greeting", ACCEPT)
    _tamper(RESULTS, RESULTS.c.id == case.result_id, scorecards_version="scorecards/0")
    case.refused("fee_explained", _override("passed"), 409, "scorecard_version_unsupported")
    case.complete_refused("scorecard_version_unsupported")

    _tamper(RESULTS, RESULTS.c.id == case.result_id, scorecards_version=SCORECARDS_VERSION)
    case.decided("fee_explained", _override("passed"))
    case.act("complete", by="sup")
    [human] = _human_results(case.id)
    assert human.reviewer_outcomes == {"greeting": "passed", "fee_explained": "passed"}


def test_an_engine_refusal_at_completion_writes_nothing(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    case = _Case(world)
    case.decided("fee_explained", _override("passed"))
    bad = _add(
        ReviewCriterionDecision,
        {
            "workspace_id": world.workspace_id,
            "meeting_id": case.meeting_id,
            "review_item_id": case.id,
            "scorecard_result_id": case.result_id,
            "criterion_id": "agenda",
            "decision": "override",
            "machine_outcome": "not_applicable",
            "reviewer_outcome": "passed",
            "reason_code": "other",
            "note": "Inserted around the service",
            "evidence": [],
            "decided_by_user_id": world.user_id("sup"),
        },
    )
    assert case.view(by="sup")["human"] is None
    case.complete_refused("review_result_not_computable")

    with inspection_session_factory()() as db:
        db.execute(delete(ReviewCriterionDecision).where(ReviewCriterionDecision.id == bad))
        db.commit()
    assert case.view(by="sup")["human"]["final"] is False
    case.act("complete", by="sup")
    [human] = _human_results(case.id)
    assert human.reviewer_outcomes == {"fee_explained": "passed"}


def test_the_criteria_view_before_any_decision(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup2")
    case = _Case(world, start=False)
    view = case.view(by="sup2")
    machine = _machine_result(case.result_id)
    assert (view["revision"], view["status"], view["scorecard_result_id"]) == (
        case.revision,
        "open",
        case.result_id,
    )
    assert view["machine"] == _block(machine.result)
    assert view["machine"]["outcome"] == machine.outcome
    assert view["human"] == {**_block(_engine(case.result_id, {})), "final": False}
    assert _human(view) == view["machine"]
    assert [c["criterion_id"] for c in view["criteria"]] == list(CRITERIA)
    for entry, row in zip(view["criteria"], _criterion_rows(case.result_id), strict=True):
        assert {
            key: entry[key]
            for key in ("criterion_id", "position", "weight", "auto_fail", "na_allowed")
        } == {
            "criterion_id": row.criterion_id,
            "position": row.position,
            "weight": row.weight,
            "auto_fail": row.auto_fail,
            "na_allowed": row.na_allowed,
        }
        assert entry["machine"] == {key: getattr(row, key) for key in CRITERION_MACHINE_KEYS}
        assert entry["machine"]["outcome"] == MACHINE[row.criterion_id]
        assert entry["reviewable"] is (row.reason != "not_applicable_by_conversation")
        assert entry["decision"] is None


# --- catalogue and documents ----------------------------------------------------------------


def test_the_decision_event_is_catalogued_as_an_administration_event(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch)
    listed = world.owner.get("/api/v1/admin/audit-event-types")
    assert listed.status_code == 200, listed.text
    assert {
        "event_type": "review_criterion.decided",
        "group": "review_criterion",
        "target_type": "review_item",
    } in listed.json()["items"]
    assert AUDIT_EVENT_CATALOG.get("review_criterion.decided") == "administration"
    assert AUDIT_EVENT_CATALOG.get("review_item.completed") == "administration"


def test_the_matrix_documents_the_two_routes_and_the_decision_rules():
    documented = {(row[0], row[1]): row[2] for row in _doc_block("routes")}
    assert documented.get(("POST", f"{ITEMS}/{{item_id}}/criteria/{{criterion_id}}/decision")) == (
        "review:manage +csrf"
    )
    assert documented.get(("GET", f"{ITEMS}/{{item_id}}/criteria")) == "review:read"
    text = MATRIX_DOC.read_text(encoding="utf-8")
    start = text.index("## 6q.")
    section = text[start : text.index("\n## ", start + 1)]
    for code in (*DECISION_CODES, "review_criterion.decided"):
        assert code in section, code


def test_completing_an_item_without_a_result_is_unchanged_from_hom73(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup")
    item = world.in_review("sup")
    seen = _seen(world.workspace_id)
    completed = world.done(item, "complete", by="sup")
    [event] = _since(world.workspace_id, seen)
    assert (event.event_type, event.details) == (
        "review_item.completed",
        {"revision": completed["revision"]},
    )
    assert _human_results(item["id"]) == []
