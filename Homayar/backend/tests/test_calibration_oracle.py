"""HOM-75 PR 2 oracle (hom75-t1): blinded calibration batches through HTTP.

Written from SPEC.md ("PR 2 rules"), SPEC-pr2.md (AC1 to AC12) and ADR 0056 before the
implementation was visible.  The SPEC fixes the routes, the bodies, the error codes and
the criterion fields, but not the JSON key names of the views and the report.  So the
checks find data by value and compare views across changes: a view that must not show
something must stay equal when only that thing changes.  Rows are seeded in the
database only where no route exists (meetings, transcripts, scorecard results, a
lapsed team, a removed user).  The report numbers are computed by hand.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import func, select, update

from backend.conversation_checks import CheckTurn
from backend.database.saas_models import (
    AuditEvent,
    CalibrationAdjudication,
    CalibrationAnswer,
    CalibrationBatch,
    CalibrationExample,
    CalibrationParticipant,
    CriterionResultRecord,
    Membership,
    ReviewItem,
    ReviewResultRecord,
    ScorecardResultRecord,
    User,
    WorkspaceRole,
)
from backend.security.authorization import API_KEY_SCOPES
from backend.services.scorecard_result_service import record_result
from backend.tests import test_authorization_matrix as matrix
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import (
    MATRIX_DOC,
    _cookies,
    _doc_block,
    _insert_meeting,
    _Persona,
    _route_policy,
    _workspace_with_roles,
)
from backend.tests.test_review_decisions_oracle import (
    DEFINITION,
    OTHER_DEFINITION,
    TURNS,
    _machine_snapshot,
    _transcript,
    _version_id,
)
from backend.tests.test_review_queue_oracle import (
    _code,
    _race,
    _refused,
    _requires_postgres,
    _set_meeting,
    _signup,
)
from backend.tests.test_scorecard_result_service import NOW, _criterion, _run, _session

BATCHES = "/api/v1/calibration-batches"
TITLE = "Quarterly zebra calibration"
NOTE = "Adjudicator heard the fee at turn seven"
OUTCOMES = ("passed", "failed", "not_applicable")
OUTCOME_WORDS = frozenset({*OUTCOMES, "insufficient_evidence"})
CRITERIA = ("greeting", "closing", "fee_explained", "agenda", "callback")
REVIEWABLE = ("greeting", "closing", "fee_explained", "callback")
CRITERION_FIELDS = frozenset({"criterion_id", "position", "weight", "na_allowed", "reviewable"})
MACHINE_KEYS = frozenset(
    {
        "reason",
        "evidence",
        "decided_by",
        "review_required",
        "review_reasons",
        "check_outcome",
        "check_reason",
        "candidate_evidence",
    }
)
EVENT_TYPES = (
    "calibration_batch.created",
    "calibration_answer.recorded",
    "calibration_adjudication.recorded",
    "calibration_batch.closed",
    "calibration_batch.cancelled",
)
ERROR_CODES = (
    "calibration_admin_required",
    "calibration_result_invalid",
    "calibration_duplicate_conversation",
    "calibration_result_not_reviewable",
    "calibration_participant_is_agent",
    "calibration_participant_ineligible",
    "calibration_batch_not_found",
    "calibration_not_participant",
    "calibration_batch_closed",
    "calibration_example_not_found",
    "criterion_not_found",
    "criterion_not_reviewable",
    "reviewer_na_not_allowed",
    "calibration_adjudicator_is_participant",
    "calibration_adjudicator_is_agent",
    "calibration_batch_revision_conflict",
    "calibration_adjudication_incomplete",
    "calibration_batch_not_closed",
)
DESIGNED_ROUTES = {
    ("GET", BATCHES): "review:read",
    ("POST", BATCHES): "review:manage +csrf",
    ("GET", f"{BATCHES}/{{batch_id}}"): "review:read",
    (
        "POST",
        f"{BATCHES}/{{batch_id}}/examples/{{example_id}}/criteria/{{criterion_id}}/answer",
    ): "review:manage +csrf",
    (
        "POST",
        f"{BATCHES}/{{batch_id}}/examples/{{example_id}}/criteria/{{criterion_id}}/adjudication",
    ): "review:manage +csrf",
    ("POST", f"{BATCHES}/{{batch_id}}/close"): "review:manage +csrf",
    ("POST", f"{BATCHES}/{{batch_id}}/cancel"): "review:manage +csrf",
    ("GET", f"{BATCHES}/{{batch_id}}/report"): "review:read",
}
ROLES = {
    "admin": "admin",
    "p4": "admin",
    "p5": "admin",
    "sup": "member",
    "sup2": "member",
    "sup_b": "member",
    "lone": "member",
    "viewer": "viewer",
}
SUPERVISES = {"sup": "a", "sup2": "a", "sup_b": "b"}
TURNS_WITHOUT_HELLO = [
    CheckTurn("good morning and welcome to the shop", "agent", 0, 1000, 0.9),
    *TURNS[1:],
]
MEETINGS_ONLY = {
    "name": "Meetings only",
    "pass_threshold": 50,
    "criteria": [
        _criterion("agenda", 100, "agenda", applies_to={"conversation_types": ["meeting"]})
    ],
}
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
STAT_KEYS = frozenset({"compared", "matched", "agreement_bp"})
VIEW_KEYS = frozenset(
    {
        "id",
        "title",
        "status",
        "revision",
        "created_by_user_id",
        "created_at",
        "closed_at",
        "cancelled_at",
        "participants",
        "examples",
        "my_answers",
        "adjudications",
        "progress",
        "answers",
    }
)
VIEW_LISTS = {
    "participants": frozenset({"id", "user_id"}),
    "my_answers": frozenset({"example_id", "criterion_id", "outcome"}),
    "adjudications": frozenset({"example_id", "criterion_id", "outcome", "note"}),
    "progress": frozenset({"participant_id", "user_id", "answered", "expected"}),
    "answers": frozenset({"participant_id", "user_id", "example_id", "criterion_id", "outcome"}),
}
EXAMPLE_KEYS = frozenset({"id", "position", "meeting_id", "scorecard_result_id", "criteria"})
SUMMARY_KEYS = frozenset(
    {
        "id",
        "title",
        "status",
        "revision",
        "created_at",
        "closed_at",
        "cancelled_at",
        "example_count",
        "participant_count",
    }
)
FULL_STATS = frozenset(
    {"expected", "compared", "matched", "missing", "agreement_bp", "kappa_e4", "confusion"}
)
REPORT_KEYS = frozenset({"batch_id", "overall", "participants", "criteria", "machine"})


def _detail(batch_id: str) -> str:
    return f"{BATCHES}/{batch_id}"


def _answer_path(batch_id: str, example_id: str, criterion_id: str) -> str:
    return f"{BATCHES}/{batch_id}/examples/{example_id}/criteria/{criterion_id}/answer"


def _adjudication_path(batch_id: str, example_id: str, criterion_id: str) -> str:
    return f"{BATCHES}/{batch_id}/examples/{example_id}/criteria/{criterion_id}/adjudication"


def _action_path(batch_id: str, action: str) -> str:
    return f"{BATCHES}/{batch_id}/{action}"


def _report_path(batch_id: str) -> str:
    return f"{BATCHES}/{batch_id}/report"


def _nodes(tree, key=None):
    yield key, tree
    if isinstance(tree, dict):
        for name, value in tree.items():
            yield from _nodes(value, name)
    elif isinstance(tree, list):
        for value in tree:
            yield from _nodes(value, key)


def _dicts(tree) -> list[dict]:
    return [node for _, node in _nodes(tree) if isinstance(node, dict)]


def _texts(tree) -> set[str]:
    found = {node for _, node in _nodes(tree) if isinstance(node, str)}
    return found | {name for node in _dicts(tree) for name in node}


def _keys(tree) -> set[str]:
    return {name for node in _dicts(tree) for name in node}


def _words(tree) -> Counter:
    return Counter(
        node for _, node in _nodes(tree) if isinstance(node, str) and node in OUTCOME_WORDS
    )


def _ints(tree) -> set[int]:
    return {
        node for _, node in _nodes(tree) if isinstance(node, int) and not isinstance(node, bool)
    }


def _canon(tree, keep: set[str] | None = None):
    if isinstance(tree, dict):
        return {
            name: _canon(value, keep) for name, value in tree.items() if not name.endswith("_at")
        }
    if isinstance(tree, list):
        return sorted((_canon(value, keep) for value in tree), key=lambda value: json.dumps(value))
    if isinstance(tree, str) and keep is not None and UUID.match(tree) and tree not in keep:
        return "<id>"
    return tree


def _check_view(body: dict) -> dict:
    assert set(body) == VIEW_KEYS, sorted(set(body) ^ VIEW_KEYS)
    for name, keys in VIEW_LISTS.items():
        for item in body[name]:
            assert set(item) == keys, (name, sorted(set(item) ^ keys))
    for example in body["examples"]:
        assert set(example) == EXAMPLE_KEYS, sorted(set(example) ^ EXAMPLE_KEYS)
        for entry in example["criteria"]:
            assert set(entry) == CRITERION_FIELDS, sorted(set(entry) ^ CRITERION_FIELDS)
    return body


def _rows(view: dict, name: str, *keys: str) -> set[tuple]:
    return {tuple(item[key] for key in keys) for item in view[name]}


def _batch_of(body: dict) -> dict:
    candidates = [node for node in _dicts(body) if {"status", "revision"} <= set(node)]
    assert candidates, body
    return candidates[0]


def _id_of(node: dict, *names: str) -> str:
    for name in names:
        if isinstance(node.get(name), str):
            return node[name]
    raise AssertionError(f"no id among {names} in {node}")


def _examples(body) -> list[dict]:
    found = [node for node in _dicts(body) if {"scorecard_result_id", "position"} <= set(node)]
    positions = [node["position"] for node in found]
    assert len(positions) == len(set(positions)), body
    return sorted(found, key=lambda node: node["position"])


def _criteria(example: dict) -> list[dict]:
    found = [node for node in _dicts(example) if CRITERION_FIELDS <= set(node)]
    return sorted(found, key=lambda node: node["position"])


def _page(body: dict) -> tuple[list[dict], str | None]:
    lists = [value for value in body.values() if isinstance(value, list)]
    cursors = [name for name in body if "cursor" in name]
    assert len(lists) == 1 and len(cursors) == 1, body
    return lists[0], body[cursors[0]]


def _listed_ids(body: dict) -> list[str]:
    items, _ = _page(body)
    return [_id_of(item, "id", "batch_id") for item in items]


def _holders(tree, user_id: str) -> list[dict]:
    return [
        node
        for key, node in _nodes(tree)
        if isinstance(node, dict) and (node.get("user_id") == user_id or key == user_id)
    ]


def _now() -> datetime:
    return datetime.now(UTC)


class _Space:
    def __init__(self) -> None:
        self.revisions: dict[str, str] = {}
        self.count = 0

    def meeting(self, *, team: str | None = None, agent: str | None = None) -> str:
        self.count += 1
        meeting_id = _insert_meeting(
            self.workspace_id,
            created_by=self.owner.user_id,
            title=f"Calibrated call {self.count}",
            team_id=self.teams[team] if team else None,
            agent_user_id=agent,
        )
        _set_meeting(meeting_id, conversation_type="call", direction=None)
        return meeting_id

    def result(
        self,
        meeting_id: str,
        *,
        turns=TURNS,
        key: str = "calibrated",
        definition: dict = DEFINITION,
    ) -> str:
        if meeting_id not in self.revisions:
            self.revisions[meeting_id] = _transcript(self, meeting_id)
        tenant = {"user_id": self.owner.user_id, "workspace_id": self.workspace_id}
        with _session(tenant) as db:
            row, _ = record_result(
                db,
                workspace_id=self.workspace_id,
                meeting_id=meeting_id,
                scorecard_version_id=_version_id(self, key, definition),
                check_run=_run(definition, "call", None, turns),
                transcript_revision_id=self.revisions[meeting_id],
                now=NOW,
            )
            db.commit()
            return row.id


class _Elsewhere(_Space):
    def __init__(self, client, email_outbox, label: str) -> None:
        super().__init__()
        credentials = _signup(
            client, email_outbox, email=f"calibration-{label}@example.com", name=label
        )
        session = login_user(client, credentials)
        self.owner = _Persona(client, session["workspaces"][0]["id"], session, _cookies(client))
        self.workspace_id = self.owner.workspace_id
        self.teams: dict[str, str] = {}

    def conversation(self, **kwargs) -> tuple[str, str]:
        meeting_id = self.meeting()
        return meeting_id, self.result(meeting_id, **kwargs)


class _World(_Space):
    def __init__(self, client, email_outbox, monkeypatch, *names: str) -> None:
        super().__init__()
        self.client = client
        monkeypatch.setattr(matrix, "register_user", _signup)
        self.owner, self.people, self.memberships = _workspace_with_roles(
            client, email_outbox, monkeypatch, roles={name: ROLES[name] for name in names}
        )
        self.workspace_id = self.owner.workspace_id
        self.teams = {key: self._team(f"Calibration team {key.upper()}") for key in ("a", "b")}
        self.assignments = {
            name: self.assign(name, SUPERVISES[name]) for name in names if name in SUPERVISES
        }

    def _team(self, name: str) -> str:
        created = self.owner.post("/api/v1/teams", json={"name": name})
        assert created.status_code == 201, created.text
        return created.json()["id"]

    def assign(self, name: str, team: str) -> str:
        assigned = self.owner.post(
            f"/api/v1/teams/{self.teams[team]}/assignments",
            json={"user_id": self.user_id(name), "role": "supervisor"},
        )
        assert assigned.status_code == 201, assigned.text
        return assigned.json()["id"]

    def revoke(self, name: str, team: str) -> None:
        path = f"/api/v1/teams/{self.teams[team]}/assignments/{self.assignments.pop(name)}"
        assert self.owner.delete(path).status_code == 204

    def persona(self, name: str) -> _Persona:
        return self.owner if name == "owner" else self.people[name]

    def user_id(self, name: str) -> str:
        return self.persona(name).user_id

    def ids(self, *names: str) -> list[str]:
        return [self.user_id(name) for name in names]

    def conversation(
        self, *, team: str | None = "a", agent: str | None = None, **kwargs
    ) -> tuple[str, str]:
        meeting_id = self.meeting(team=team, agent=self.user_id(agent) if agent else None)
        return meeting_id, self.result(meeting_id, **kwargs)

    def create(
        self, results, participants, *, title: str | None = TITLE, by: str = "owner", **extra
    ):
        body = {
            "title": title,
            "scorecard_result_ids": list(results),
            "participant_user_ids": [
                self.user_id(name) if name in self.people or name == "owner" else name
                for name in participants
            ],
            **extra,
        }
        return self.persona(by).post(BATCHES, json=body)

    def batch(self, results, participants, **kwargs) -> _Batch:
        response = self.create(results, participants, **kwargs)
        assert response.status_code == 201, response.text
        return _Batch(self, response.json(), list(results))

    def listed(self, name: str = "owner", **params) -> dict:
        response = self.persona(name).get(BATCHES, params=params)
        assert response.status_code == 200, response.text
        return response.json()


class _Batch:
    def __init__(self, world: _World, body: dict, results: list[str]) -> None:
        self.world = world
        self.created = _check_view(body)
        self.id = body["id"]
        examples = sorted(body["examples"], key=lambda example: example["position"])
        assert [example["scorecard_result_id"] for example in examples] == results, body
        self.examples = [example["id"] for example in examples]
        self.results = results

    def view(self, name: str = "owner") -> dict:
        response = self.world.persona(name).get(_detail(self.id))
        assert response.status_code == 200, (name, response.text)
        return _check_view(response.json())

    def answer(self, index_or_id, criterion_id: str, outcome, *, by: str):
        example = self._example(index_or_id)
        body = {} if outcome is ... else {"outcome": outcome}
        return self.world.persona(by).post(_answer_path(self.id, example, criterion_id), json=body)

    def answered(self, index_or_id, criterion_id: str, outcome: str, *, by: str):
        response = self.answer(index_or_id, criterion_id, outcome, by=by)
        assert response.status_code == 200, (by, criterion_id, outcome, response.text)
        view = _check_view(response.json())
        assert view["id"] == self.id
        assert (self._example(index_or_id), criterion_id, outcome) in _rows(
            view, "my_answers", "example_id", "criterion_id", "outcome"
        )
        return response

    def adjudicate(self, index_or_id, criterion_id: str, outcome, *, by: str = "owner", **extra):
        example = self._example(index_or_id)
        body = {**({} if outcome is ... else {"outcome": outcome}), **extra}
        return self.world.persona(by).post(
            _adjudication_path(self.id, example, criterion_id), json=body
        )

    def adjudicated(self, index_or_id, criterion_id: str, outcome: str, **kwargs):
        response = self.adjudicate(index_or_id, criterion_id, outcome, **kwargs)
        assert response.status_code == 200, (criterion_id, outcome, response.text)
        view = _check_view(response.json())
        assert (self._example(index_or_id), criterion_id, outcome) in _rows(
            view, "adjudications", "example_id", "criterion_id", "outcome"
        )
        return response

    def complete(self, gold: dict | None = None) -> None:
        for index in range(len(self.examples)):
            for criterion_id in REVIEWABLE:
                outcome = (gold or {}).get((index, criterion_id), "passed")
                self.adjudicated(index, criterion_id, outcome)

    def act(self, action: str, *, by: str = "owner", revision: int | None = None):
        expected = _stored_batch(self.id).revision if revision is None else revision
        return self.world.persona(by).post(
            _action_path(self.id, action), json={"expected_revision": expected}
        )

    def done(self, action: str, **kwargs) -> None:
        response = self.act(action, **kwargs)
        assert response.status_code == 200, (action, response.text)
        view = _check_view(response.json())
        assert view["status"] == {"close": "closed", "cancel": "cancelled"}[action]
        assert view["revision"] == _stored_batch(self.id).revision

    def report(self, name: str = "owner"):
        return self.world.persona(name).get(_report_path(self.id))

    def _example(self, index_or_id) -> str:
        return self.examples[index_or_id] if isinstance(index_or_id, int) else index_or_id


def _stored_batch(batch_id: str) -> CalibrationBatch:
    with inspection_session_factory()() as db:
        row = db.get(CalibrationBatch, batch_id)
        assert row is not None, batch_id
        return row


def _state(workspace_id: str) -> tuple:
    with inspection_session_factory()() as db:
        batches = sorted(
            (row.id, row.status, row.revision, str(row.updated_at), str(row.closed_at))
            for row in db.scalars(
                select(CalibrationBatch).where(CalibrationBatch.workspace_id == workspace_id)
            )
        )
        examples = db.scalar(
            select(func.count())
            .select_from(CalibrationExample)
            .where(CalibrationExample.workspace_id == workspace_id)
        )
        participants = sorted(
            (row.id, row.user_id)
            for row in db.scalars(
                select(CalibrationParticipant).where(
                    CalibrationParticipant.workspace_id == workspace_id
                )
            )
        )
        answers = sorted(
            (row.id, row.criterion_id, row.outcome, str(row.updated_at))
            for row in db.scalars(
                select(CalibrationAnswer).where(CalibrationAnswer.workspace_id == workspace_id)
            )
        )
        adjudications = sorted(
            (row.id, row.criterion_id, row.outcome, row.note, str(row.updated_at))
            for row in db.scalars(
                select(CalibrationAdjudication).where(
                    CalibrationAdjudication.workspace_id == workspace_id
                )
            )
        )
        events = db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.event_type.like("calibration%"),
            )
        )
    return batches, examples, participants, answers, adjudications, events


def _events(workspace_id: str, event_type: str | None = None) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.workspace_id == workspace_id)
            .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
        )
        return [
            row
            for row in rows
            if row.event_type.startswith("calibration_")
            and (event_type is None or row.event_type == event_type)
        ]


def _every_audit_row() -> str:
    with inspection_session_factory()() as db:
        columns = AuditEvent.__mapper__.column_attrs
        return "\n".join(
            json.dumps(
                {column.key: getattr(row, column.key) for column in columns},
                default=str,
                ensure_ascii=False,
            )
            for row in db.scalars(select(AuditEvent))
        )


def _denied(world: _World, response, status: int, code: str, before: tuple) -> None:
    _refused(response, status, code)
    assert _state(world.workspace_id) == before, code


def _malformed(world: _World, response, before: tuple) -> None:
    assert response.status_code == 422, response.text
    assert _state(world.workspace_id) == before


def _machine_strings(*result_ids: str) -> set[str]:
    found = {"insufficient_evidence", "not_computable"}
    with inspection_session_factory()() as db:
        for result_id in result_ids:
            result = db.get(ScorecardResultRecord, result_id)
            if result.reason:
                found.add(result.reason)
            for row in db.scalars(
                select(CriterionResultRecord).where(CriterionResultRecord.result_id == result_id)
            ):
                found |= {value for value in (row.reason, row.check_reason) if value}
                found |= {
                    node
                    for _, node in _nodes(row.evidence)
                    if isinstance(node, str) and len(node) > 12
                }
    return found | {turn.text for turn in (*TURNS, *TURNS_WITHOUT_HELLO)}


def _assert_blind_to_the_machine(body, machine: set[str]) -> None:
    assert not (_keys(body) & MACHINE_KEYS), sorted(_keys(body) & MACHINE_KEYS)
    assert not (_texts(body) & machine), sorted(_texts(body) & machine)


def _machine_outcomes(result_id: str) -> dict[str, str]:
    with inspection_session_factory()() as db:
        return {
            row.criterion_id: row.outcome
            for row in db.scalars(
                select(CriterionResultRecord).where(CriterionResultRecord.result_id == result_id)
            )
        }


def _seed_admins(world: _World, count: int) -> list[str]:
    created = []
    with inspection_session_factory()() as db:
        for index in range(count):
            user = User(email=f"hom75-seeded-{index}-{uuid.uuid4().hex[:8]}@example.com")
            db.add(user)
            db.flush()
            db.add(
                Membership(
                    workspace_id=world.workspace_id,
                    user_id=user.id,
                    role=WorkspaceRole.ADMIN,
                    is_active=True,
                    valid_from=_now() - timedelta(minutes=5),
                )
            )
            created.append(user.id)
        db.commit()
    return created


def test_an_admin_creates_a_batch_with_the_examples_in_the_given_order(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup2")
    results = [world.conversation()[1] for _ in range(3)]
    given = [results[2], results[0], results[1]]
    before = _state(world.workspace_id)
    response = world.create(given, ["admin", "sup", "owner"])
    assert response.status_code == 201, response.text
    body = _check_view(response.json())
    batch = body
    assert (body["status"], body["revision"], body["title"]) == ("open", 1, TITLE)
    assert (body["closed_at"], body["cancelled_at"]) == (None, None)
    assert body["created_by_user_id"] == world.owner.user_id
    assert {person["user_id"] for person in body["participants"]} == set(
        world.ids("admin", "sup", "owner")
    )
    for name in ("my_answers", "adjudications", "progress", "answers"):
        assert body[name] == [], name
    examples = _examples(body)
    assert [example["scorecard_result_id"] for example in examples] == given
    assert [example["position"] for example in examples] == [0, 1, 2]
    for example in examples:
        criteria = _criteria(example)
        assert [entry["criterion_id"] for entry in criteria] == list(CRITERIA)
        assert [entry["position"] for entry in criteria] == [0, 1, 2, 3, 4]
        assert [entry["weight"] for entry in criteria] == [20, 20, 20, 20, 20]
        assert [entry["na_allowed"] for entry in criteria] == [False, True, False, False, False]
        assert [entry["reviewable"] for entry in criteria] == [True, True, True, False, True]
    assert set(world.ids("admin", "sup", "owner")) <= _texts(body)
    assert world.user_id("sup2") not in _texts(body)

    stored = _stored_batch(batch.get("id") or batch.get("batch_id"))
    assert (stored.status, stored.revision, stored.created_by_user_id) == (
        "open",
        1,
        world.owner.user_id,
    )
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(CalibrationExample).where(CalibrationExample.batch_id == stored.id)
        )
        assert sorted((row.position, row.scorecard_result_id) for row in rows) == list(
            enumerate(given)
        )
        people = db.scalars(
            select(CalibrationParticipant.user_id).where(
                CalibrationParticipant.batch_id == stored.id
            )
        )
        assert set(people) == set(world.ids("admin", "sup", "owner"))
    [event] = _events(world.workspace_id)
    assert (event.event_type, event.actor_user_id) == (
        "calibration_batch.created",
        world.owner.user_id,
    )
    assert set(given) | set(world.ids("admin", "sup", "owner")) <= _texts(event.details)
    assert TITLE not in json.dumps(event.details, ensure_ascii=False)
    assert len(_state(world.workspace_id)[0]) == len(before[0]) + 1

    by_admin = world.batch([results[0]], ["sup"], by="admin")
    assert by_admin.results == [results[0]]


def test_only_review_admins_create_a_batch(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup2", "viewer")
    _, result = world.conversation()
    before = _state(world.workspace_id)
    _denied(
        world,
        world.create([result], ["sup2"], by="sup"),
        403,
        "calibration_admin_required",
        before,
    )
    _denied(world, world.create([result], ["sup2"], by="viewer"), 403, "permission_denied", before)
    world.batch([result], ["sup2"])


def test_each_result_must_be_a_scored_live_conversation_of_the_workspace(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup")
    _, good = world.conversation()
    deleted_meeting, deleted = world.conversation()
    _set_meeting(deleted_meeting, deleted_at=_now())
    elsewhere = _Elsewhere(client, email_outbox, "result-elsewhere")
    _, foreign = elsewhere.conversation()
    before = _state(world.workspace_id)
    for bad in (str(uuid.uuid4()), foreign, deleted):
        response = world.create([good, bad], ["sup"])
        _denied(world, response, 422, "calibration_result_invalid", before)
        assert bad in json.dumps(response.json()["error"].get("details")), response.text
    world.batch([good], ["sup"])


def test_one_conversation_at_most_once_and_each_result_needs_a_reviewable_criterion(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup")
    meeting, first = world.conversation()
    second = world.result(meeting, key="another", definition=OTHER_DEFINITION)
    _, other = world.conversation()
    hollow_meeting = world.meeting(team="a")
    hollow = world.result(hollow_meeting, key="meetings_only", definition=MEETINGS_ONLY)
    before = _state(world.workspace_id)
    _denied(
        world,
        world.create([first, second], ["sup"]),
        422,
        "calibration_duplicate_conversation",
        before,
    )
    _denied(
        world,
        world.create([other, hollow], ["sup"]),
        422,
        "calibration_result_not_reviewable",
        before,
    )
    world.batch([first, other], ["sup"])
    world.batch([second], ["sup"])


def test_every_participant_must_be_eligible_for_every_example(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup_b", "lone", "viewer")
    _, team_a = world.conversation()
    agent_meeting, agent_result = world.conversation(agent="sup")
    b_meeting, team_b = world.conversation(team="b")
    loose_meeting, loose = world.conversation(team=None)
    elsewhere = _Elsewhere(client, email_outbox, "participant-elsewhere")
    before = _state(world.workspace_id)

    response = world.create([team_a, agent_result], ["admin", "sup"])
    _denied(world, response, 422, "calibration_participant_is_agent", before)
    assert (
        response.json()["error"]["details"].items()
        >= {
            "user_id": world.user_id("sup"),
            "meeting_id": agent_meeting,
        }.items()
    )
    for name in ("viewer", "lone"):
        response = world.create([team_a], [name])
        _denied(world, response, 422, "calibration_participant_ineligible", before)
        assert response.json()["error"]["details"]["user_id"] == world.user_id(name)
    response = world.create([team_a, team_b], ["sup"])
    _denied(world, response, 422, "calibration_participant_ineligible", before)
    assert (
        response.json()["error"]["details"].items()
        >= {
            "user_id": world.user_id("sup"),
            "meeting_id": b_meeting,
        }.items()
    )
    response = world.create([team_a, loose], ["sup"])
    _denied(world, response, 422, "calibration_participant_ineligible", before)
    assert response.json()["error"]["details"]["meeting_id"] == loose_meeting
    for stranger in (elsewhere.owner.user_id, str(uuid.uuid4())):
        response = world.create([team_a], [stranger])
        _denied(world, response, 422, "calibration_participant_ineligible", before)

    _set_meeting(b_meeting, agent_user_id=world.user_id("sup"))
    _denied(
        world,
        world.create([team_b], ["sup"]),
        422,
        "calibration_participant_is_agent",
        before,
    )

    world.batch([team_a, team_b, loose], ["admin"])
    world.batch([team_a], ["sup"])
    _set_meeting(b_meeting, agent_user_id=None)
    world.batch([team_b], ["sup_b"])
    _set_meeting(agent_meeting, agent_user_id=world.user_id("lone"))
    world.batch([team_a, agent_result], ["admin", "sup"])
    world.batch([team_a], ["owner", "admin"])


def test_the_size_limits_of_a_batch(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin")
    results = [world.conversation()[1] for _ in range(51)]
    people = _seed_admins(world, 21)
    before = _state(world.workspace_id)
    for listed, participants in (
        (results, ["admin"]),
        ([], ["admin"]),
        (results[:1], people),
        (results[:1], []),
    ):
        _malformed(world, world.create(listed, participants), before)
    shuffled = results[:50][::-1]
    batch = world.batch(shuffled, ["admin"])
    assert batch.results == shuffled
    world.batch(results[:1], people[:20])


def test_the_title_rule_and_repeated_ids(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup2")
    _, first = world.conversation()
    _, second = world.conversation()
    before = _state(world.workspace_id)
    for title in ("", "   ", "x" * 201, "ن" * 201, None):
        _malformed(world, world.create([first], ["sup"], title=title), before)
    missing = world.owner.post(
        BATCHES,
        json={"scorecard_result_ids": [first], "participant_user_ids": world.ids("sup")},
    )
    _malformed(world, missing, before)
    _malformed(world, world.create([first, first], ["sup"]), before)
    _malformed(world, world.create([first], ["sup", "sup"]), before)
    for title in ("x", "x" * 200, "ن" * 200, "  y  "):
        world.batch([first], ["sup"], title=title)
    world.batch([first, second], ["sup", "sup2"])


def _batch_routes(batch: _Batch, example_id: str) -> list[tuple[str, str, dict | None]]:
    return [
        ("GET", _detail(batch.id), None),
        ("GET", _report_path(batch.id), None),
        ("POST", _answer_path(batch.id, example_id, "greeting"), {"outcome": "passed"}),
        ("POST", _adjudication_path(batch.id, example_id, "greeting"), {"outcome": "passed"}),
        ("POST", _action_path(batch.id, "close"), {"expected_revision": 1}),
        ("POST", _action_path(batch.id, "cancel"), {"expected_revision": 1}),
    ]


def _send(persona: _Persona, method: str, path: str, body: dict | None):
    return persona.client.request(
        method, path, json=body, cookies=persona.cookies, headers=persona.headers
    )


def test_a_batch_is_invisible_to_non_participant_members_and_other_workspaces(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup_b", "lone", "viewer")
    _, result = world.conversation()
    batch = world.batch([result], ["sup"])
    elsewhere = _Elsewhere(client, email_outbox, "visibility-elsewhere")
    before = _state(world.workspace_id)
    for persona in (world.persona("sup_b"), world.persona("lone"), elsewhere.owner):
        for method, path, body in _batch_routes(batch, batch.examples[0]):
            _denied(
                world,
                _send(persona, method, path, body),
                404,
                "calibration_batch_not_found",
                before,
            )
    missing = _Batch.__new__(_Batch)
    missing.id = str(uuid.uuid4())
    for method, path, body in _batch_routes(missing, batch.examples[0]):
        _denied(
            world,
            _send(world.owner, method, path, body),
            404,
            "calibration_batch_not_found",
            before,
        )
    for method, path, body in [
        *_batch_routes(batch, batch.examples[0]),
        ("GET", BATCHES, None),
        (
            "POST",
            BATCHES,
            {
                "title": TITLE,
                "scorecard_result_ids": [result],
                "participant_user_ids": world.ids("sup"),
            },
        ),
    ]:
        _denied(
            world,
            _send(world.persona("viewer"), method, path, body),
            403,
            "permission_denied",
            before,
        )
    for name in ("sup", "owner", "admin"):
        assert _batch_of(batch.view(name))["status"] == "open"


def test_the_list_shows_admins_every_batch_and_members_only_theirs(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup2", "sup_b")
    _, result = world.conversation()
    first = world.batch([result], ["sup"])
    second = world.batch([result], ["sup2"])
    third = world.batch([result], ["admin"])
    newest = [third.id, second.id, first.id]
    for name in ("owner", "admin"):
        assert _listed_ids(world.listed(name)) == newest, name
    assert _listed_ids(world.listed("sup")) == [first.id]
    assert _listed_ids(world.listed("sup2")) == [second.id]
    assert _listed_ids(world.listed("sup_b")) == []

    for item in _page(world.listed("owner"))[0]:
        assert set(item) == SUMMARY_KEYS, sorted(set(item) ^ SUMMARY_KEYS)
        assert (item["example_count"], item["participant_count"], item["status"]) == (
            1,
            1,
            "open",
        )
    assert set(world.listed("owner")) == {"items", "next_cursor"}
    page = world.listed("owner", limit=2)
    items, cursor = _page(page)
    assert [_id_of(item, "id", "batch_id") for item in items] == newest[:2]
    assert cursor
    rest = world.listed("owner", limit=2, cursor=cursor)
    items, cursor = _page(rest)
    assert [_id_of(item, "id", "batch_id") for item in items] == newest[2:]
    assert cursor is None
    assert _listed_ids(world.listed("owner", limit=100)) == newest
    for limit in (0, 101):
        response = world.owner.get(BATCHES, params={"limit": limit})
        assert response.status_code == 422, response.text


def test_answers_adjudications_and_the_machine_stay_hidden_before_close(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup2")
    _, first = world.conversation()
    _, second = world.conversation(turns=TURNS_WITHOUT_HELLO)
    assert _machine_outcomes(first)["greeting"] != _machine_outcomes(second)["greeting"]
    batch = world.batch([first, second], ["admin", "sup", "sup2"])
    machine = _machine_strings(first, second)
    _assert_blind_to_the_machine(batch.created, machine)
    names = ("owner", "admin", "sup", "sup2")

    def look() -> tuple[dict, dict]:
        views = {name: batch.view(name) for name in names}
        lists = {name: world.listed(name) for name in names}
        for body in (*views.values(), *lists.values()):
            _assert_blind_to_the_machine(body, machine)
        return {name: _canon(view) for name, view in views.items()}, {
            name: _canon(body) for name, body in lists.items()
        }

    views, lists = look()
    raw = {name: batch.view(name) for name in names}
    for name in names:
        assert _words(raw[name]) == Counter(), name
        assert _words(world.listed(name)) == Counter(), name
    [x, y] = _examples(raw["sup"])
    assert _canon(_criteria(x), keep=set()) == _canon(_criteria(y), keep=set())
    for name in ("owner", "admin", "sup", "sup2"):
        _refused(batch.report(name), 409, "calibration_batch_not_closed")

    batch.answered(0, "greeting", "failed", by="sup2")
    batch.answered(0, "closing", "not_applicable", by="sup2")
    batch.answered(1, "fee_explained", "passed", by="sup2")
    adjudicated = batch.adjudicated(0, "greeting", "passed", note=NOTE)
    _assert_blind_to_the_machine(adjudicated.json(), machine)
    batch.adjudicated(1, "callback", "failed")
    after, after_lists = look()
    for name in ("admin", "sup"):
        assert after[name] == views[name], name
        assert after_lists[name] == lists[name], name
    assert after["sup2"] != views["sup2"]
    assert after["owner"] != views["owner"]
    owner_view = batch.view("owner")
    assert set(_words(owner_view)) == {"passed", "failed"}
    assert {3, 8} <= _ints(_holders(owner_view, world.user_id("sup2")))
    assert {0, 8} <= _ints(_holders(owner_view, world.user_id("sup")))
    x_id, y_id = batch.examples
    assert (owner_view["my_answers"], owner_view["answers"]) == ([], [])
    assert _rows(owner_view, "adjudications", "example_id", "criterion_id", "outcome", "note") == {
        (x_id, "greeting", "passed", NOTE),
        (y_id, "callback", "failed", None),
    }
    assert _rows(owner_view, "progress", "user_id", "answered", "expected") == {
        (world.user_id("sup2"), 3, 8),
        (world.user_id("sup"), 0, 8),
        (world.user_id("admin"), 0, 8),
    }
    assert _rows(owner_view, "progress", "participant_id", "user_id") == _rows(
        owner_view, "participants", "id", "user_id"
    )
    for name in ("admin", "sup", "sup2"):
        view = batch.view(name)
        assert (view["adjudications"], view["progress"], view["answers"]) == ([], [], []), name
    assert _rows(batch.view("sup2"), "my_answers", "example_id", "criterion_id", "outcome") == {
        (x_id, "greeting", "failed"),
        (x_id, "closing", "not_applicable"),
        (y_id, "fee_explained", "passed"),
    }
    for name in ("admin", "sup"):
        assert batch.view(name)["my_answers"] == [], name

    batch.answered(0, "greeting", "passed", by="sup2")
    batch.answered(0, "closing", "failed", by="sup2")
    batch.answered(1, "fee_explained", "failed", by="sup2")
    changed, changed_lists = look()
    assert changed["owner"] == after["owner"]
    for name in ("admin", "sup"):
        assert changed[name] == views[name], name
        assert changed_lists[name] == lists[name], name
    assert changed["sup2"] != after["sup2"]

    batch.adjudicated(0, "greeting", "failed")
    readjudicated, _ = look()
    assert readjudicated["owner"] != changed["owner"]
    for name in ("admin", "sup", "sup2"):
        assert readjudicated[name] == changed[name], name

    first_reply = batch.answered(0, "greeting", "failed", by="sup")
    _assert_blind_to_the_machine(first_reply.json(), machine)
    own, _ = look()
    assert own["sup"] != readjudicated["sup"]
    assert "failed" in _words(batch.view("sup"))
    for name in ("admin", "sup2"):
        assert own[name] == readjudicated[name], name
    assert {1, 8} <= _ints(_holders(batch.view("owner"), world.user_id("sup")))

    batch.answered(1, "greeting", "passed", by="admin")
    admin_own, _ = look()
    assert admin_own["admin"] != own["admin"]
    batch.adjudicated(1, "callback", "passed")
    batch.adjudicated(1, "greeting", "failed")
    again, _ = look()
    for name in ("admin", "sup", "sup2"):
        assert again[name] == admin_own[name], name

    repeated = batch.answered(0, "greeting", "failed", by="sup")
    batch.answered(1, "fee_explained", "passed", by="sup2")
    batch.answered(1, "callback", "failed", by="admin")
    repeated_again = batch.answered(0, "greeting", "failed", by="sup")
    assert _canon(repeated.json()) == _canon(repeated_again.json())
    _assert_blind_to_the_machine(repeated_again.json(), machine)

    rows = _every_audit_row()
    assert TITLE not in rows and NOTE not in rows
    recorded = _events(world.workspace_id, "calibration_answer.recorded") + _events(
        world.workspace_id, "calibration_adjudication.recorded"
    )
    assert recorded
    for event in recorded:
        assert not (_texts(event.details) & OUTCOME_WORDS), event.details
        assert not [key for key in _keys(event.details) if "outcome" in key or "note" in key]
        assert batch.id in _texts(event.details) or event.target_id == batch.id
    for event in _events(world.workspace_id):
        assert TITLE not in json.dumps(event.details, ensure_ascii=False)


def test_after_close_participants_see_the_adjudications_and_only_their_own_answers(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup2")
    first_meeting, first = world.conversation()
    second_meeting, second = world.conversation()
    batches = [world.batch([first, second], ["admin", "sup", "sup2"]) for _ in range(3)]
    keep = {
        *world.ids("owner", "admin", "sup", "sup2"),
        first,
        second,
        first_meeting,
        second_meeting,
        world.workspace_id,
    }
    for index, batch in enumerate(batches):
        batch.answered(0, "greeting", "failed", by="sup")
        batch.answered(1, "closing", "not_applicable", by="sup")
        batch.answered(0, "fee_explained", "passed", by="admin")
        batch.answered(0, "greeting", "failed" if index == 1 else "passed", by="sup2")
        batch.answered(1, "callback", "passed" if index == 1 else "failed", by="sup2")
        gold = {(0, "greeting"): "failed" if index == 2 else "passed", (1, "closing"): "passed"}
        batch.complete(gold)

    def views(name: str) -> list:
        return [_canon(batch.view(name), keep) for batch in batches]

    for name in ("admin", "sup"):
        before = views(name)
        assert before[0] == before[1] == before[2], name
    owner_before = views("owner")
    assert owner_before[0] == owner_before[1]
    assert owner_before[0] != owner_before[2]
    for batch in batches:
        assert batch.view("owner")["answers"] == []
        for name in ("admin", "sup", "sup2"):
            assert batch.view(name)["adjudications"] == [], name

    for batch in batches:
        batch.done("close")
        assert _stored_batch(batch.id).status == "closed"

    for name in ("admin", "sup"):
        after = views(name)
        assert after[0] == after[1], name
        assert after[0] != after[2], name
    owner_after = views("owner")
    assert owner_after[0] != owner_after[1]
    assert owner_after[0] != owner_after[2]
    own = batches[0].view("sup")
    assert "not_applicable" in _words(own)
    assert set(_words(batches[0].view("sup2"))) >= {"passed", "failed"}
    x_id, y_id = batches[0].examples
    mine = {
        "sup": {(x_id, "greeting", "failed"), (y_id, "closing", "not_applicable")},
        "admin": {(x_id, "fee_explained", "passed")},
        "sup2": {(x_id, "greeting", "passed"), (y_id, "callback", "failed")},
    }
    for name, expected in mine.items():
        view = batches[0].view(name)
        assert _rows(view, "my_answers", "example_id", "criterion_id", "outcome") == expected
        assert (view["progress"], view["answers"]) == ([], []), name
        assert len(view["adjudications"]) == 8, name
    owner = batches[0].view("owner")
    assert owner["my_answers"] == []
    assert len(owner["adjudications"]) == 8
    assert _rows(owner, "answers", "user_id", "example_id", "criterion_id", "outcome") == {
        (world.user_id(name), *answer) for name, answers in mine.items() for answer in answers
    }


def _answer_world(client, email_outbox, monkeypatch, *extra: str):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup2", *extra)
    first_meeting, first = world.conversation()
    second_meeting, second = world.conversation()
    batch = world.batch([first, second], ["admin", "sup", "sup2"])
    return world, batch, (first_meeting, second_meeting)


def test_the_answer_refusals_and_their_order(client, email_outbox, monkeypatch):
    world, batch, _ = _answer_world(client, email_outbox, monkeypatch, "sup_b")
    _, foreign_result = world.conversation()
    foreign = world.batch([foreign_result], ["sup"])
    cancelled = world.batch([batch.results[0]], ["sup", "admin"])
    cancelled.done("cancel")
    before = _state(world.workspace_id)
    elsewhere_example = foreign.examples[0]
    ghost = str(uuid.uuid4())

    _denied(
        world,
        batch.answer(0, "greeting", "passed", by="sup_b"),
        404,
        "calibration_batch_not_found",
        before,
    )
    _denied(
        world,
        batch.answer(0, "greeting", "passed", by="owner"),
        403,
        "calibration_not_participant",
        before,
    )
    _denied(
        world,
        cancelled.answer(0, "greeting", "passed", by="sup"),
        409,
        "calibration_batch_closed",
        before,
    )
    for example in (elsewhere_example, ghost):
        _denied(
            world,
            batch.answer(example, "greeting", "passed", by="sup"),
            404,
            "calibration_example_not_found",
            before,
        )
    for criterion_id in ("no_such_criterion", "empathy"):
        _denied(
            world,
            batch.answer(0, criterion_id, "passed", by="sup"),
            404,
            "criterion_not_found",
            before,
        )
    _denied(
        world,
        batch.answer(0, "agenda", "passed", by="sup"),
        422,
        "criterion_not_reviewable",
        before,
    )
    _denied(
        world,
        batch.answer(0, "greeting", "not_applicable", by="sup"),
        422,
        "reviewer_na_not_allowed",
        before,
    )

    _denied(
        world,
        cancelled.answer(0, "greeting", "passed", by="sup_b"),
        404,
        "calibration_batch_not_found",
        before,
    )
    _denied(
        world,
        cancelled.answer(0, "greeting", "passed", by="owner"),
        403,
        "calibration_not_participant",
        before,
    )
    _denied(
        world,
        cancelled.answer(ghost, "greeting", "passed", by="sup"),
        409,
        "calibration_batch_closed",
        before,
    )
    _denied(
        world,
        batch.answer(ghost, "agenda", "not_applicable", by="sup"),
        404,
        "calibration_example_not_found",
        before,
    )
    _denied(
        world,
        batch.answer(0, "no_such_criterion", "not_applicable", by="sup"),
        404,
        "criterion_not_found",
        before,
    )
    _denied(
        world,
        batch.answer(0, "agenda", "not_applicable", by="sup"),
        422,
        "criterion_not_reviewable",
        before,
    )

    for outcome in ("insufficient_evidence", "Passed", "PASSED", "pass", "", " passed", None, ...):
        _malformed(world, batch.answer(0, "greeting", outcome, by="sup"), before)

    batch.answered(0, "greeting", "passed", by="sup")
    batch.answered(0, "greeting", "failed", by="admin")
    batch.answered(0, "callback", "passed", by="sup")
    batch.answered(0, "closing", "not_applicable", by="sup")
    batch.answered(1, "greeting", "passed", by="sup2")


def test_eligibility_is_checked_again_when_a_participant_answers(client, email_outbox, monkeypatch):
    world, batch, (first_meeting, second_meeting) = _answer_world(client, email_outbox, monkeypatch)
    before = _state(world.workspace_id)

    _set_meeting(first_meeting, agent_user_id=world.user_id("sup"))
    _denied(
        world,
        batch.answer(0, "greeting", "passed", by="sup"),
        422,
        "calibration_participant_is_agent",
        before,
    )
    _denied(
        world,
        batch.answer(0, "no_such_criterion", "passed", by="sup"),
        422,
        "calibration_participant_is_agent",
        before,
    )
    _set_meeting(first_meeting, team_id=world.teams["b"])
    _denied(
        world,
        batch.answer(0, "greeting", "passed", by="sup"),
        422,
        "calibration_participant_is_agent",
        before,
    )
    _set_meeting(first_meeting, agent_user_id=None)
    _denied(
        world,
        batch.answer(0, "greeting", "passed", by="sup"),
        403,
        "calibration_participant_ineligible",
        before,
    )
    _denied(
        world,
        batch.answer(0, "no_such_criterion", "passed", by="sup"),
        403,
        "calibration_participant_ineligible",
        before,
    )
    batch.answered(1, "greeting", "passed", by="sup")
    batch.answered(0, "greeting", "passed", by="admin")
    _set_meeting(first_meeting, team_id=world.teams["a"])
    batch.answered(0, "greeting", "passed", by="sup")

    world.revoke("sup", "a")
    before = _state(world.workspace_id)
    _denied(
        world,
        batch.answer(1, "closing", "passed", by="sup"),
        403,
        "calibration_participant_ineligible",
        before,
    )
    assert batch.view("sup")
    batch.answered(1, "closing", "passed", by="sup2")


def test_an_answer_is_one_row_that_changes_until_close_and_an_identical_one_is_a_no_op(
    client, email_outbox, monkeypatch
):
    world, batch, _ = _answer_world(client, email_outbox, monkeypatch)

    def rows() -> list[tuple]:
        with inspection_session_factory()() as db:
            return [
                (row.criterion_id, row.outcome, str(row.updated_at))
                for row in db.scalars(
                    select(CalibrationAnswer).where(CalibrationAnswer.batch_id == batch.id)
                )
            ]

    seen = len(_events(world.workspace_id))
    batch.answered(0, "greeting", "passed", by="sup")
    [first] = rows()
    assert first[:2] == ("greeting", "passed")
    assert len(_events(world.workspace_id)) == seen + 1
    batch.answered(0, "greeting", "passed", by="sup")
    assert rows() == [first]
    assert len(_events(world.workspace_id)) == seen + 1
    batch.answered(0, "greeting", "failed", by="sup")
    [changed] = rows()
    assert changed[:2] == ("greeting", "failed")
    events = _events(world.workspace_id, "calibration_answer.recorded")
    assert len(events) == 2
    assert {event.actor_user_id for event in events} == {world.user_id("sup")}


def _adjudication_world(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup_b")
    first_meeting, first = world.conversation()
    _, second = world.conversation()
    batch = world.batch([first, second], ["admin", "sup"])
    return world, batch, first_meeting


def test_the_adjudication_refusals_and_their_order(client, email_outbox, monkeypatch):
    world, batch, first_meeting = _adjudication_world(client, email_outbox, monkeypatch)
    foreign = world.batch([batch.results[1]], ["sup"])
    cancelled = world.batch([batch.results[0]], ["sup", "admin"])
    cancelled.done("cancel")
    before = _state(world.workspace_id)
    ghost = str(uuid.uuid4())

    _denied(
        world,
        batch.adjudicate(0, "greeting", "passed", by="sup_b"),
        404,
        "calibration_batch_not_found",
        before,
    )
    _denied(
        world,
        batch.adjudicate(0, "greeting", "passed", by="sup"),
        403,
        "calibration_admin_required",
        before,
    )
    _denied(
        world,
        batch.adjudicate(0, "greeting", "passed", by="admin"),
        403,
        "calibration_adjudicator_is_participant",
        before,
    )
    _denied(
        world,
        cancelled.adjudicate(0, "greeting", "passed"),
        409,
        "calibration_batch_closed",
        before,
    )
    for example in (foreign.examples[0], ghost):
        _denied(
            world,
            batch.adjudicate(example, "greeting", "passed"),
            404,
            "calibration_example_not_found",
            before,
        )
    _set_meeting(first_meeting, agent_user_id=world.owner.user_id)
    _denied(
        world,
        batch.adjudicate(0, "greeting", "passed"),
        422,
        "calibration_adjudicator_is_agent",
        before,
    )
    _denied(
        world,
        batch.adjudicate(0, "no_such_criterion", "passed"),
        422,
        "calibration_adjudicator_is_agent",
        before,
    )
    batch.adjudicated(1, "greeting", "passed")
    _set_meeting(first_meeting, agent_user_id=None)
    before = _state(world.workspace_id)
    for criterion_id in ("no_such_criterion", "empathy"):
        _denied(
            world, batch.adjudicate(0, criterion_id, "passed"), 404, "criterion_not_found", before
        )
    _denied(world, batch.adjudicate(0, "agenda", "passed"), 422, "criterion_not_reviewable", before)
    _denied(
        world,
        batch.adjudicate(0, "greeting", "not_applicable"),
        422,
        "reviewer_na_not_allowed",
        before,
    )

    _denied(
        world,
        cancelled.adjudicate(0, "greeting", "passed", by="sup_b"),
        404,
        "calibration_batch_not_found",
        before,
    )
    _denied(
        world,
        cancelled.adjudicate(0, "greeting", "passed", by="sup"),
        403,
        "calibration_admin_required",
        before,
    )
    _denied(
        world,
        cancelled.adjudicate(0, "greeting", "passed", by="admin"),
        403,
        "calibration_adjudicator_is_participant",
        before,
    )
    _denied(
        world,
        cancelled.adjudicate(ghost, "greeting", "passed"),
        409,
        "calibration_batch_closed",
        before,
    )
    _denied(
        world,
        batch.adjudicate(0, "no_such_criterion", "not_applicable"),
        404,
        "criterion_not_found",
        before,
    )
    _denied(
        world,
        batch.adjudicate(0, "agenda", "not_applicable"),
        422,
        "criterion_not_reviewable",
        before,
    )

    batch.adjudicated(0, "greeting", "passed")
    batch.adjudicated(0, "closing", "not_applicable")
    batch.adjudicated(0, "callback", "failed")
    foreign.adjudicated(0, "greeting", "failed", by="admin")


def test_the_adjudication_note_and_the_upsert(client, email_outbox, monkeypatch):
    world, batch, _ = _adjudication_world(client, email_outbox, monkeypatch)
    before = _state(world.workspace_id)
    for note in ("x" * 2001, "ن" * 2001):
        _malformed(world, batch.adjudicate(0, "greeting", "passed", note=note), before)
    for outcome in ("insufficient_evidence", "Failed", "pass", "", None, ...):
        _malformed(world, batch.adjudicate(0, "greeting", outcome), before)
    batch.adjudicated(0, "greeting", "passed", note="x" * 2000)
    batch.adjudicated(0, "closing", "passed", note="ن" * 2000)
    batch.adjudicated(0, "fee_explained", "failed", note=None)
    batch.adjudicated(0, "callback", "passed")

    seen = len(_events(world.workspace_id))
    with inspection_session_factory()() as db:
        stored = {
            row.criterion_id: (row.outcome, row.note, str(row.updated_at), row.decided_by_user_id)
            for row in db.scalars(
                select(CalibrationAdjudication).where(CalibrationAdjudication.batch_id == batch.id)
            )
        }
    assert stored["greeting"][:2] == ("passed", "x" * 2000)
    assert stored["closing"][:2] == ("passed", "ن" * 2000)
    assert stored["greeting"][3] == world.owner.user_id
    batch.adjudicated(0, "callback", "passed")
    assert len(_events(world.workspace_id)) == seen
    batch.adjudicated(0, "callback", "failed")
    assert len(_events(world.workspace_id)) == seen + 1
    with inspection_session_factory()() as db:
        rows = list(
            db.scalars(
                select(CalibrationAdjudication).where(
                    CalibrationAdjudication.batch_id == batch.id,
                    CalibrationAdjudication.criterion_id == "callback",
                )
            )
        )
    assert [row.outcome for row in rows] == ["failed"]


def test_close_and_cancel(client, email_outbox, monkeypatch):
    world, batch, _ = _adjudication_world(client, email_outbox, monkeypatch)
    before = _state(world.workspace_id)
    _denied(
        world, batch.act("close", by="sup", revision=5), 403, "calibration_admin_required", before
    )
    _denied(world, batch.act("close", by="sup_b"), 404, "calibration_batch_not_found", before)
    stale = batch.act("close", revision=2)
    _denied(world, stale, 409, "calibration_batch_revision_conflict", before)
    assert stale.json()["error"]["details"]["current_revision"] == 1
    empty = batch.act("close")
    _denied(world, empty, 409, "calibration_adjudication_incomplete", before)
    assert empty.json()["error"]["details"]["missing"] == 8
    missing = world.owner.post(_action_path(batch.id, "close"), json={})
    _malformed(world, missing, before)

    for index in range(2):
        for criterion_id in REVIEWABLE:
            if (index, criterion_id) != (1, "callback"):
                batch.adjudicated(index, criterion_id, "passed")
    before = _state(world.workspace_id)
    partial = batch.act("close")
    _denied(world, partial, 409, "calibration_adjudication_incomplete", before)
    assert partial.json()["error"]["details"]["missing"] == 1
    batch.adjudicated(1, "callback", "failed")
    batch.answered(0, "greeting", "passed", by="sup")

    seen = len(_events(world.workspace_id))
    batch.done("close")
    stored = _stored_batch(batch.id)
    assert (stored.status, stored.revision, stored.closed_by_user_id) == (
        "closed",
        2,
        world.owner.user_id,
    )
    assert stored.closed_at is not None and stored.cancelled_at is None
    assert _batch_of(batch.view("sup"))["status"] == "closed"
    [closed] = _events(world.workspace_id)[seen:]
    assert closed.event_type == "calibration_batch.closed"

    before = _state(world.workspace_id)
    _denied(world, batch.act("close", revision=2), 409, "calibration_batch_closed", before)
    _denied(world, batch.act("cancel", revision=2), 409, "calibration_batch_closed", before)
    conflict = batch.act("close", revision=1)
    _denied(world, conflict, 409, "calibration_batch_revision_conflict", before)
    assert conflict.json()["error"]["details"]["current_revision"] == 2
    _denied(
        world,
        batch.answer(0, "greeting", "failed", by="sup"),
        409,
        "calibration_batch_closed",
        before,
    )
    _denied(
        world,
        batch.answer(0, "closing", "passed", by="admin"),
        409,
        "calibration_batch_closed",
        before,
    )
    _denied(
        world, batch.adjudicate(0, "greeting", "failed"), 409, "calibration_batch_closed", before
    )
    assert batch.report("owner").status_code == 200

    other = world.batch(batch.results, ["admin", "sup"])
    other.answered(0, "greeting", "failed", by="sup")
    before = _state(world.workspace_id)
    _denied(world, other.act("cancel", by="sup"), 403, "calibration_admin_required", before)
    _denied(
        world, other.act("cancel", revision=3), 409, "calibration_batch_revision_conflict", before
    )
    other.done("cancel")
    stored = _stored_batch(other.id)
    assert (stored.status, stored.revision, stored.cancelled_by_user_id) == (
        "cancelled",
        2,
        world.owner.user_id,
    )
    assert stored.cancelled_at is not None and stored.closed_at is None
    assert _words(other.view("owner")) == Counter()
    assert other.view("owner")["answers"] == []
    assert [event.event_type for event in _events(world.workspace_id)][-1] == (
        "calibration_batch.cancelled"
    )
    before = _state(world.workspace_id)
    _denied(
        world,
        other.answer(0, "greeting", "passed", by="sup"),
        409,
        "calibration_batch_closed",
        before,
    )
    _denied(
        world, other.adjudicate(0, "greeting", "passed"), 409, "calibration_batch_closed", before
    )
    _denied(world, other.act("close", revision=2), 409, "calibration_batch_closed", before)
    _denied(world, other.act("cancel", revision=2), 409, "calibration_batch_closed", before)
    for name in ("owner", "sup"):
        _denied(world, other.report(name), 409, "calibration_batch_not_closed", before)


GOLD = {
    (0, "greeting"): "failed",
    (0, "closing"): "not_applicable",
    (0, "fee_explained"): "passed",
    (0, "callback"): "passed",
    (1, "greeting"): "passed",
    (1, "closing"): "not_applicable",
    (1, "fee_explained"): "passed",
    (1, "callback"): "failed",
}
ANSWERS = {
    "sup": {
        (0, "greeting"): "failed",
        (0, "closing"): "not_applicable",
        (0, "fee_explained"): "passed",
        (0, "callback"): "failed",
        (1, "greeting"): "passed",
        (1, "fee_explained"): "passed",
        (1, "callback"): "failed",
    },
    "sup2": {
        (0, "greeting"): "failed",
        (1, "greeting"): "failed",
        (0, "fee_explained"): "passed",
    },
    "admin": {
        (0, "greeting"): "passed",
        (0, "closing"): "passed",
        (0, "fee_explained"): "failed",
        (0, "callback"): "failed",
        (1, "greeting"): "failed",
        (1, "closing"): "failed",
        (1, "callback"): "passed",
    },
    "p4": {(0, "fee_explained"): "passed"},
    "p5": {},
}
ROWS = {
    "sup": {
        "expected": 8,
        "compared": 7,
        "matched": 6,
        "missing": 1,
        "agreement_bp": 8571,
        "kappa_e4": 7667,
        "confusion": {
            "failed": {"failed": 2},
            "not_applicable": {"not_applicable": 1},
            "passed": {"failed": 1, "passed": 3},
        },
    },
    "sup2": {
        "expected": 8,
        "compared": 3,
        "matched": 2,
        "missing": 5,
        "agreement_bp": 6667,
        "kappa_e4": 4000,
        "confusion": {"failed": {"failed": 1}, "passed": {"failed": 1, "passed": 1}},
    },
    "admin": {
        "expected": 8,
        "compared": 7,
        "matched": 0,
        "missing": 1,
        "agreement_bp": 0,
        "kappa_e4": -5313,
        "confusion": {
            "failed": {"passed": 2},
            "not_applicable": {"failed": 1, "passed": 1},
            "passed": {"failed": 3},
        },
    },
    "p4": {
        "expected": 8,
        "compared": 1,
        "matched": 1,
        "missing": 7,
        "agreement_bp": 10000,
        "kappa_e4": None,
        "confusion": {"passed": {"passed": 1}},
    },
    "p5": {
        "expected": 8,
        "compared": 0,
        "matched": 0,
        "missing": 8,
        "agreement_bp": None,
        "kappa_e4": None,
        "confusion": {},
    },
}
OVERALL = {
    "expected": 40,
    "compared": 18,
    "matched": 9,
    "missing": 22,
    "agreement_bp": 5000,
    "kappa_e4": 1735,
    "confusion": {
        "failed": {"failed": 3, "passed": 2},
        "not_applicable": {"failed": 1, "not_applicable": 1, "passed": 1},
        "passed": {"failed": 5, "passed": 5},
    },
}
MACHINE_ROW = {
    "expected": 8,
    "compared": 8,
    "matched": 1,
    "missing": 0,
    "agreement_bp": 1250,
    "kappa_e4": -2727,
    "confusion": {
        "failed": {"insufficient_evidence": 1, "passed": 1},
        "not_applicable": {"passed": 2},
        "passed": {"failed": 2, "insufficient_evidence": 1, "passed": 1},
    },
}
CRITERION_ROWS = {
    "greeting": {"compared": 6, "matched": 3, "agreement_bp": 5000},
    "closing": {"compared": 3, "matched": 1, "agreement_bp": 3333},
    "fee_explained": {"compared": 5, "matched": 4, "agreement_bp": 8000},
    "callback": {"compared": 4, "matched": 1, "agreement_bp": 2500},
}


def _plain(value):
    if isinstance(value, dict):
        cleaned = {key: _plain(inner) for key, inner in value.items()}
        return {key: inner for key, inner in cleaned.items() if inner not in (0, {})}
    return value


def _fits(node: dict, expected: dict) -> bool:
    return all(
        name in node
        and (_plain(node[name]) == _plain(value) if name == "confusion" else node[name] == value)
        for name, value in expected.items()
    )


def _report(response) -> dict:
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == REPORT_KEYS, sorted(set(body) ^ REPORT_KEYS)
    assert set(body["overall"]) == FULL_STATS, body["overall"]
    if body["machine"] is not None:
        assert set(body["machine"]) == FULL_STATS, body["machine"]
    for row in body["participants"]:
        assert set(row) == FULL_STATS | {"participant_id", "user_id"}, row
    for row in body["criteria"]:
        assert set(row) == STAT_KEYS | {"criterion_id"}, row
    return body


def _stats(row: dict) -> dict:
    return {name: row[name] for name in FULL_STATS}


def _criterion_stats(body: dict) -> dict:
    return {
        row["criterion_id"]: {name: row[name] for name in STAT_KEYS} for row in body["criteria"]
    }


def test_the_report_numbers_and_who_sees_which_rows(client, email_outbox, monkeypatch):
    world = _World(
        client, email_outbox, monkeypatch, "admin", "p4", "p5", "sup", "sup2", "sup_b", "viewer"
    )
    _, first = world.conversation()
    _, second = world.conversation()
    machine = {"greeting": "passed", "closing": "passed", "fee_explained": "failed"}
    for result in (first, second):
        outcomes = _machine_outcomes(result)
        assert {name: outcomes[name] for name in machine} == machine
        assert (outcomes["callback"], outcomes["agenda"]) == (
            "insufficient_evidence",
            "not_applicable",
        )
    machine_before = [_machine_snapshot(result) for result in (first, second)]
    batch = world.batch([first, second], ["sup", "sup2", "admin", "p4", "p5"])
    for name, answers in ANSWERS.items():
        for (index, criterion_id), outcome in answers.items():
            batch.answered(index, criterion_id, outcome, by=name)
    batch.complete(GOLD)
    _refused(batch.report("owner"), 409, "calibration_batch_not_closed")
    batch.done("close")
    [closed] = _events(world.workspace_id, "calibration_batch.closed")
    assert any(
        _fits(node, {"compared": 18, "matched": 9, "agreement_bp": 5000})
        for node in _dicts(closed.details)
    ), closed.details
    assert any(node.get("revision") == 2 for node in _dicts(closed.details)), closed.details

    with inspection_session_factory()() as db:
        db.execute(
            update(CalibrationParticipant)
            .where(
                CalibrationParticipant.batch_id == batch.id,
                CalibrationParticipant.user_id == world.user_id("admin"),
            )
            .values(user_id=None)
        )
        db.commit()

    seats = {person["id"] for person in batch.view("owner")["participants"]}
    named = {world.user_id(person): ROWS[person] for person in ("sup", "sup2", "p4", "p5")}
    for name in ("owner", "p4"):
        body = _report(batch.report(name))
        assert body["batch_id"] == batch.id
        assert _stats(body["overall"]) == OVERALL
        assert _stats(body["machine"]) == MACHINE_ROW
        assert len(body["participants"]) == 5
        assert {row["user_id"]: _stats(row) for row in body["participants"]} == {
            **named,
            None: ROWS["admin"],
        }
        assert {row["participant_id"] for row in body["participants"]} == seats
        assert _criterion_stats(body).items() >= CRITERION_ROWS.items()

    body = _report(batch.report("sup"))
    assert body["machine"] is None
    assert [row["user_id"] for row in body["participants"]] == [world.user_id("sup")]
    assert _stats(body["participants"][0]) == ROWS["sup"]
    assert _stats(body["overall"]) == OVERALL
    assert _criterion_stats(body).items() >= CRITERION_ROWS.items()
    assert "insufficient_evidence" not in _texts(body)
    for name in ("sup2", "p4", "p5"):
        assert world.user_id(name) not in _texts(body), name

    _refused(batch.report("sup_b"), 404, "calibration_batch_not_found")
    _refused(batch.report("viewer"), 403, "permission_denied")
    assert [_machine_snapshot(result) for result in (first, second)] == machine_before
    with inspection_session_factory()() as db:
        for model in (ReviewItem, ReviewResultRecord):
            stored = db.scalar(
                select(func.count())
                .select_from(model)
                .where(model.workspace_id == world.workspace_id)
            )
            assert stored == 0, model.__tablename__


@pytest.mark.postgres
def test_two_concurrent_closes_with_one_revision_never_both_succeed(
    client, email_outbox, monkeypatch
):
    _requires_postgres()
    world = _World(client, email_outbox, monkeypatch, "admin", "sup")
    _, result = world.conversation()
    for action in ("close", "cancel"):
        batch = world.batch([result], ["sup"])
        batch.complete()
        seen = len(_events(world.workspace_id))
        results = _race(
            client,
            [
                (world.owner, _action_path(batch.id, "close"), {"expected_revision": 1}),
                (world.persona("admin"), _action_path(batch.id, action), {"expected_revision": 1}),
            ],
        )
        assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
        [loser] = [r for r in results if r.status_code == 409]
        assert _code(loser) in (
            "calibration_batch_revision_conflict",
            "calibration_batch_closed",
        )
        stored = _stored_batch(batch.id)
        assert stored.revision == 2 and stored.status in ("closed", "cancelled")
        assert len(_events(world.workspace_id)) == seen + 1


@pytest.mark.postgres
def test_an_answer_racing_a_close_is_either_in_the_closed_batch_or_refused(
    client, email_outbox, monkeypatch
):
    _requires_postgres()
    world = _World(client, email_outbox, monkeypatch, "sup")
    _, result = world.conversation()
    for _ in range(4):
        batch = world.batch([result], ["sup"])
        batch.complete()
        results = _race(
            client,
            [
                (world.owner, _action_path(batch.id, "close"), {"expected_revision": 1}),
                (
                    world.persona("sup"),
                    _answer_path(batch.id, batch.examples[0], "greeting"),
                    {"outcome": "passed"},
                ),
            ],
        )
        closed, answered = results
        assert closed.status_code == 200, closed.text
        with inspection_session_factory()() as db:
            stored = db.scalar(
                select(func.count())
                .select_from(CalibrationAnswer)
                .where(CalibrationAnswer.batch_id == batch.id)
            )
        if answered.status_code in (200, 201):
            assert stored == 1
        else:
            _refused(answered, 409, "calibration_batch_closed")
            assert stored == 0
        report = _report(batch.report("owner"))
        compared = [
            row["compared"]
            for row in report["participants"]
            if row["user_id"] == world.user_id("sup")
        ]
        assert compared == [stored]


def test_another_workspace_api_keys_and_missing_csrf_reach_nothing(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup")
    _, result = world.conversation()
    batch = world.batch([result], ["sup"])
    elsewhere = _Elsewhere(client, email_outbox, "isolation-elsewhere")
    before = _state(world.workspace_id)
    assert batch.id not in _listed_ids(elsewhere.owner.get(BATCHES).json())
    for method, path, body in _batch_routes(batch, batch.examples[0]):
        _denied(
            world,
            _send(elsewhere.owner, method, path, body),
            404,
            "calibration_batch_not_found",
            before,
        )

    scopes = sorted(scope.value for scope in API_KEY_SCOPES)
    minted = _mint(client, world.owner.headers, world.owner.cookies, scopes=scopes)
    assert minted.status_code == 201, minted.text
    key = _bearer(minted.json()["key"], **{"X-Workspace-ID": world.workspace_id})
    before = _state(world.workspace_id)
    create = {
        "title": TITLE,
        "scorecard_result_ids": [result],
        "participant_user_ids": world.ids("sup"),
    }
    routes = [
        *_batch_routes(batch, batch.examples[0]),
        ("GET", BATCHES, None),
        ("POST", BATCHES, create),
    ]
    for method, path, body in routes:
        response = client.request(method, path, json=body, headers=key)
        assert response.status_code in (401, 403), (path, response.text)
        assert _state(world.workspace_id) == before

    no_csrf = {"X-Workspace-ID": world.workspace_id}
    for method, path, body in routes:
        persona = world.persona("sup") if path.endswith(("answer", "report")) else world.owner
        sent = client.request(method, path, json=body, cookies=persona.cookies, headers=no_csrf)
        if method == "POST":
            _denied(world, sent, 403, "csrf_failed", before)
        elif path.endswith("report"):
            _refused(sent, 409, "calibration_batch_not_closed")
        else:
            assert sent.status_code == 200, (path, sent.text)
    batch.answered(0, "greeting", "passed", by="sup")


def test_the_calibration_events_are_catalogued(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch)
    listed = world.owner.get("/api/v1/admin/audit-event-types")
    assert listed.status_code == 200, listed.text
    types = {item["event_type"] for item in listed.json()["items"]}
    for event_type in EVENT_TYPES:
        assert event_type in types, event_type


def test_the_matrix_documents_the_routes_the_policies_and_the_rules(client):
    documented = {(row[0], row[1]): row[2] for row in _doc_block("routes")}
    assert {route: documented.get(route) for route in DESIGNED_ROUTES} == DESIGNED_ROUTES
    served = {
        (method, route.path): _route_policy(route)
        for route in client.app.routes
        if isinstance(route, APIRoute) and route.path.startswith(BATCHES)
        for method in route.methods
        if method != "HEAD"
    }
    assert served == DESIGNED_ROUTES
    text = MATRIX_DOC.read_text(encoding="utf-8")
    assert "## 6r." in text, "the matrix has no section 6r"
    start = text.index("## 6r.")
    end = text.find("\n## ", start + 1)
    section = text[start : end if end != -1 else len(text)]
    for name in (*ERROR_CODES, *EVENT_TYPES, "review:read", "review:manage"):
        assert name in section, name
