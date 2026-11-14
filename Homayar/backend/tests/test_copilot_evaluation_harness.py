"""HOM-98 (SPEC 2.5, 2.6): the placeholder set end to end through ``answer_question``.

The world is the shared Copilot world with deterministic ids (``uuid5`` of one
namespace), so the replay file can name its conversations and team: team one
holds conversations 0 and 1, the workspace record scope is TEAM, and the
callers are the owner (whole workspace) and the supervisor of team one.

Replay template syntax (the writer side of ``synthetic_placeholder.replay.json``,
also recorded in ADR 0084):

* ``{count}`` is replaced by the value of the first ``fact <key> = <value>``
  line of the prompt the writer receives;
* ``{quote}`` is replaced by the first three whitespace-separated words of the
  first ``span:`` transcript block of the prompt, and the sentence cites that
  span id (the template supplies the quote marks);
* ``{quotes}`` (used by a01 only, A5.11) ignores the fixed sentence: the writer
  returns one sentence per ``span:`` block, each quoting that span's first
  three words and citing that span id, so a citation pin can name the whole
  cohort even when the span order of a lexical tie is random;
* a question whose plan is refused never reaches its writer, so the answers map
  holds no entry for it.

The fake models read only the prompt they receive, so a wrong pipeline gives a
wrong answer.  The negative controls prove the harness can fail: a writer that
adds one to every count drops ``exact_count_accuracy`` below 1, and a planner
that routes an unsupported question to an evidence intent drops
``abstention_rate`` below 1.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import update

from backend.copilot_guard import ModelPrompt
from backend.database.saas_models import (
    Meeting,
    Membership,
    Team,
    TeamAssignment,
    TeamRole,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.evaluation.copilot import load_question_set, run_question_set
from backend.services.copilot_answer_service import answer_question
from backend.tests._copilot_world import (
    _FACT,
    DAY,
    NOW,
    FakePlanner,
    _all_spans,
    _first_span,
    seed_copilot_world,
)
from backend.tests._database import inspection_session_factory
from backend.tests.test_retrieval_evidence import _bound, _context

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "evaluation" / "copilot"
NAMESPACE = "6a98c0de-0000-4000-8000-000000000098"
TEAM_ONE = "6130cead-6332-5c1a-9ccc-7d2d22ac3d2e"
CONVERSATION_IDS = {
    "conv_refund_first": "9c5fba9a-8993-570d-8e22-04c03045e9b4",
    "conv_second": "a7ee743e-03cb-59a8-945e-6537e4b3d39d",
    "conv_third": "b161947d-40c9-52d2-8719-d6046b9b98d0",
}
QUESTION_SET = load_question_set(FIXTURES / "synthetic_placeholder.questions.json")
REPLAY = json.loads((FIXTURES / "synthetic_placeholder.replay.json").read_text("utf-8"))


class ReplayWriter:
    """The writer of the replay file: one template, filled from the prompt only."""

    def __init__(self, template: str | None) -> None:
        self.template = template
        self.prompts: list[ModelPrompt] = []

    def complete(self, prompt: ModelPrompt) -> str:
        self.prompts.append(prompt)
        if self.template is None:
            return json.dumps({"sentences": []})
        text = self.template
        citations: list[str] = []
        if "{count}" in text:
            found = _FACT.search(prompt.instructions)
            assert found is not None, prompt.instructions
            text = text.replace("{count}", found.group(1))
        if "{quote}" in text:
            span_id, span_text = _first_span(prompt.data)
            assert span_text, prompt.data
            text = text.replace("{quote}", " ".join(span_text.split()[:3]))
            citations = [span_id]
        if "{quotes}" in text:
            # One sentence per span, each quoting that span.  The citation pin
            # of a01 (A5.11) then names the whole cohort, deterministic even
            # though the span order of a tie is not.
            return json.dumps(
                {
                    "sentences": [
                        {
                            "text": f'The caller said "{" ".join(span_text.split()[:3])}".',
                            "citations": [span_id],
                        }
                        for span_id, span_text in _all_spans(prompt.data)
                    ]
                }
            )
        return json.dumps({"sentences": [{"text": text, "citations": citations}]})


class PlusOneWriter(ReplayWriter):
    """The first negative control: every count comes out one too high."""

    def complete(self, prompt: ModelPrompt) -> str:
        self.prompts.append(prompt)
        found = _FACT.search(prompt.instructions)
        assert found is not None, prompt.instructions
        wrong = Decimal(found.group(1)) + 1
        return json.dumps(
            {"sentences": [{"text": f"There were {wrong} conversations.", "citations": []}]}
        )


@pytest.fixture
def harness(client, email_outbox):
    """The deterministic world plus its two callers."""
    world = seed_copilot_world(client, email_outbox, "hom98-harness", namespace=NAMESPACE)
    assert world.conversation("0").meeting_id == CONVERSATION_IDS["conv_refund_first"]
    assert world.conversation("2").meeting_id == CONVERSATION_IDS["conv_third"]
    with inspection_session_factory()() as db:
        db.execute(
            update(Workspace).where(Workspace.id == world.workspace_id).values(record_scope="TEAM")
        )
        db.add(
            Team(
                id=TEAM_ONE,
                workspace_id=world.workspace_id,
                name="harness team one",
                slug="harness-team-one",
            )
        )
        supervisor = User(email="hom98-harness-sup@example.com", display_name="harness sup")
        db.add(supervisor)
        db.flush()
        db.add(
            Membership(
                workspace_id=world.workspace_id,
                user_id=supervisor.id,
                role=WorkspaceRole.MEMBER,
                valid_from=NOW - 365 * DAY,
            )
        )
        db.add(
            TeamAssignment(
                workspace_id=world.workspace_id,
                team_id=TEAM_ONE,
                user_id=supervisor.id,
                role=TeamRole.SUPERVISOR,
                assigned_by_user_id=world.owner_id,
                valid_from=NOW - 365 * DAY,
            )
        )
        for label in ("0", "1"):
            db.execute(
                update(Meeting)
                .where(Meeting.id == world.conversation(label).meeting_id)
                .values(team_id=TEAM_ONE)
            )
        db.commit()
        supervisor_id = supervisor.id
    callers = {
        "a_owner": world.context(),
        "a_supervisor_t1": _context(supervisor_id, world.workspace_id),
    }
    return world, callers


def run_set(harness, plans, answers):
    world, callers = harness

    def ask(question):
        planner = FakePlanner(plans[question.id])
        writer = ReplayWriter(answers.get(question.id))
        with _bound(world.workspace_id) as db:
            answer = answer_question(
                db,
                context=callers[question.caller],
                question=question.question,
                now=NOW,
                planner=planner,
                writer=writer,
            )
        return answer.observed()

    return run_question_set(QUESTION_SET, ask, conversation_ids=CONVERSATION_IDS)


def test_the_placeholder_set_answers_every_question_correctly(harness):
    report = run_set(harness, REPLAY["plans"], REPLAY["answers"]).to_json()
    assert report["total"] == 13
    assert report["correct"] == 13
    assert report["overall_accuracy"] == 1.0
    assert report["count"] == {"total": 4, "correct": 4, "exact_count_accuracy": 1.0}
    assert report["answer"] == {"total": 4, "correct": 4, "answer_accuracy": 1.0}
    assert report["unsupported"] == {
        "total": 5,
        "abstained": 5,
        "correct": 5,
        "abstention_rate": 1.0,
    }
    assert report["supported_abstained"] == 0
    assert report["false_abstention_rate"] == 0.0
    assert report["f58"]["applicable"] is False and report["f58"]["passed"] is None
    assert all(entry["correct"] for entry in report["questions"])
    assert all(entry["intent_match"] for entry in report["questions"])
    assert report["question_set_sha256"] == QUESTION_SET.sha256


def test_the_report_of_the_plus_one_control(harness):
    world, callers = harness

    def ask(question):
        planner = FakePlanner(REPLAY["plans"][question.id])
        writer = (
            PlusOneWriter(None)
            if question.id.startswith("c")
            else ReplayWriter(REPLAY["answers"].get(question.id))
        )
        with _bound(world.workspace_id) as db:
            answer = answer_question(
                db,
                context=callers[question.caller],
                question=question.question,
                now=NOW,
                planner=planner,
                writer=writer,
            )
        return answer.observed()

    report = run_question_set(QUESTION_SET, ask, conversation_ids=CONVERSATION_IDS).to_json()
    assert report["count"]["correct"] == 0
    assert report["count"]["exact_count_accuracy"] == 0.0
    # The guard refuses the invented number (``number_not_from_aggregate``), so
    # the answer is an abstention and the failure is ``not_answered``: the wrong
    # number never reaches the user.
    assert [entry["failure"] for entry in report["questions"] if entry["kind"] == "count"] == [
        "not_answered"
    ] * 4


def test_a_planner_that_misroutes_an_unsupported_question_drops_the_abstention_rate(harness):
    # a lookup with a query: the search text is the query, so the misrouted
    # question answers no matter what its own words are.
    misrouted = {**REPLAY["plans"], "u01": REPLAY["plans"]["a02"]}
    report = run_set(harness, misrouted, REPLAY["answers"]).to_json()
    assert report["unsupported"]["abstained"] == 4
    assert report["unsupported"]["abstention_rate"] == 0.8
    wrong = next(entry for entry in report["questions"] if entry["id"] == "u01")
    assert wrong["status"] == "answered" and wrong["failure"] == "answered"


def test_the_team_scoped_count_and_the_lookup_cite_the_right_conversations(harness):
    world, callers = harness

    def observed_of(question_id):
        question = next(item for item in QUESTION_SET.questions if item.id == question_id)
        planner = FakePlanner(REPLAY["plans"][question_id])
        writer = ReplayWriter(REPLAY["answers"].get(question_id))
        with _bound(world.workspace_id) as db:
            answer = answer_question(
                db,
                context=callers[question.caller],
                question=question.question,
                now=NOW,
                planner=planner,
                writer=writer,
            )
        return answer

    # the team-scoped count of the supervisor: 2, not the 3 of the whole workspace
    assert observed_of("c02").observed().fact_values == (Decimal("2"),)
    # the lookup cites exactly the asked conversation
    assert observed_of("a03").cited_conversation_ids() == (CONVERSATION_IDS["conv_third"],)
    assert observed_of("a02").cited_conversation_ids() == (CONVERSATION_IDS["conv_refund_first"],)
