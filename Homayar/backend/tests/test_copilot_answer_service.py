"""HOM-98 (SPEC 2.3): the Copilot path of ``answer_question``.

Every step with fake models: the argument checks, the live access of D2 (a
denied caller refuses before any model call), the planner and the routing, a
refusal, the SQL route (D8, the raw plan params of A1.1), the evidence route
with its gate, the answer prompt and the guard.  And the rule that the planner
prompt never holds transcript text (D7 of ADR 0084).
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.copilot_guard import GuardDefect, ModelPrompt
from backend.copilot_tools import ToolLimits
from backend.database.saas_models import Membership
from backend.evaluation.copilot import ObservedAnswer
from backend.services.copilot_answer_service import CopilotStatus, answer_question
from backend.tests._copilot_world import (
    INJECTION,
    NOW,
    QUESTION,
    TREND_WINDOW,
    CopilotWorld,
    FakePlanner,
    FakeWriter,
    _first_span,
    plan_of,
    seed_copilot_world,
)
from backend.tests._database import inspection_session_factory
from backend.tests.test_retrieval_evidence import _bound


@pytest.fixture
def copilot_world(client, email_outbox) -> CopilotWorld:
    return seed_copilot_world(client, email_outbox)


def ask(world, planner: FakePlanner, writer: FakeWriter, **kwargs):
    with _bound(world.workspace_id) as db:
        return answer_question(
            db,
            context=world.context(),
            question=kwargs.pop("question", QUESTION),
            now=kwargs.pop("now", NOW),
            planner=planner,
            writer=writer,
            **kwargs,
        )


def lookup_plan(world, label: str, query: str | None = None) -> str:
    params = {"conversation_id": world.conversation(label).meeting_id}
    if query is not None:
        params["query"] = query
    return plan_of("conversation_lookup", **params)


# --- step 0: the argument checks and the live access ----------------------------------------


def test_the_argument_checks_are_value_errors(copilot_world):
    world = copilot_world
    planner = FakePlanner(plan_of("issue_count", family="call_reason"))
    writer = FakeWriter()
    db = Session()
    base = {"question": QUESTION, "now": NOW, "planner": planner, "writer": writer}
    with pytest.raises(ValueError):
        answer_question(db, context=object(), **base)  # type: ignore[arg-type]
    for changes in (
        {"question": 7},
        {"now": NOW.replace(tzinfo=None)},
        {"planner": object()},
        {"writer": object()},
        {"catalogue": "x"},
        {"limits": "x"},
        {"vectors": object()},
    ):
        with pytest.raises(ValueError):
            answer_question(db, context=world.context(), **{**base, **changes})  # type: ignore[arg-type]


def test_a_lone_surrogate_question_is_a_step_0_value_error(copilot_world):
    """A4.6: a lone surrogate is not text and the guard's ``frame_untrusted``
    refuses it, so the question is rejected with the other argument checks,
    before live access and before any model call.  A NUL is text (A4.2): it
    never raises on any route.  F2: the check runs before the live access, so
    even a denied caller gets the ``ValueError``, never the refusal."""
    world = copilot_world
    planner = FakePlanner(lookup_plan(world, "0"))
    writer = FakeWriter()
    with pytest.raises(ValueError):
        ask(world, planner, writer, question="سلام\ud800")
    assert planner.prompts == [] and writer.prompts == []
    nul = ask(world, FakePlanner(lookup_plan(world, "0")), FakeWriter(), question="سلام\x00")
    assert nul.status is CopilotStatus.ANSWERED
    assert world.conversation("0").entry_ids[0] in {span.span_id for span in nul.evidence}
    # A denied caller: the step-0 ValueError still comes first.
    with inspection_session_factory()() as db:
        db.execute(
            update(Membership)
            .where(Membership.workspace_id == world.workspace_id)
            .values(valid_from=NOW + timedelta(days=1))
        )
        db.commit()
    with pytest.raises(ValueError):
        ask(world, FakePlanner(lookup_plan(world, "0")), FakeWriter(), question="سلام\ud800")


def test_a_denied_caller_refuses_before_any_model_call(copilot_world):
    world = copilot_world
    with inspection_session_factory()() as db:
        db.execute(
            update(Membership)
            .where(Membership.workspace_id == world.workspace_id)
            .values(valid_from=NOW + timedelta(days=1))
        )
        db.commit()
    planner = FakePlanner(plan_of("issue_count", family="call_reason"))
    writer = FakeWriter()
    answer = ask(world, planner, writer)
    # A2.3: the whole shape of a step-0 refusal.
    assert answer.status is CopilotStatus.REFUSED
    assert answer.reason == "permission_denied" and answer.detail is None
    assert answer.intent_id is None and answer.tool is None
    assert answer.explanation is None
    assert answer.catalogue_version is None and answer.catalogue_digest is None
    assert answer.facts == () and answer.evidence == ()
    assert answer.signals == () and answer.notices == () and answer.sentences == ()
    assert planner.prompts == [] and writer.prompts == []


def test_facts_evidence_and_notices_are_empty_unless_answered(copilot_world):
    """A2.4: only an answered answer carries material.  ``signals`` may survive
    a gate or guard abstention (its own test below)."""
    world = copilot_world
    plans = [
        json.dumps({"intent": "issue_count", "params": {}, "workspace_id": "x"}),  # refused
        plan_of("coverage_status", **TREND_WINDOW),  # D8 abstention
        plan_of("issue_count", **TREND_WINDOW, issue="complaint_refund"),  # tool abstention
        plan_of("conversation_lookup", conversation_id="3f1e2d3c-4b5a-4f6e-8d7c-6b5a4f3e2d1c"),
    ]
    for index, plan in enumerate(plans):
        kwargs = {"limits": ToolLimits(3, 100, 5000, 10000)} if index == 2 else {}
        answer = ask(world, FakePlanner(plan), FakeWriter(), **kwargs)
        assert answer.status is not CopilotStatus.ANSWERED, plan
        assert answer.facts == () and answer.evidence == () and answer.notices == ()


# --- steps 1 to 3: the planner, the routing, a refusal ---------------------------------------


def test_a_malformed_plan_refuses_with_the_explanation(copilot_world):
    world = copilot_world
    planner = FakePlanner(json.dumps({"intent": "issue_count", "params": {}, "workspace_id": "x"}))
    writer = FakeWriter()
    answer = ask(world, planner, writer)
    assert answer.status is CopilotStatus.REFUSED
    assert answer.reason == "invalid_request" and answer.detail == "plan_malformed"
    assert answer.explanation is not None and answer.explanation.code == "refuse.invalid_request"
    assert answer.catalogue_version == "copilot-intents/1"
    assert len(planner.prompts) == 1 and writer.prompts == []


def test_the_planner_prompt_holds_the_question_and_no_transcript_text(copilot_world):
    """D7: the planner sees the caller's question only, so no transcript can
    change the plan."""
    world = copilot_world
    planner = FakePlanner(lookup_plan(world, "1", query="instructions"))
    answer = ask(world, planner, FakeWriter())
    assert answer.status is CopilotStatus.ANSWERED
    prompt = planner.prompts[0]
    assert QUESTION in prompt.data
    for transcript_only in ("متکلف", "برگردانید", "instructions", "workspaces"):
        assert transcript_only not in prompt.instructions
        assert transcript_only not in prompt.data


# --- step 4, the SQL route (D8) --------------------------------------------------------------


def test_issue_count_answers_with_the_facts_and_the_writer(copilot_world):
    world = copilot_world
    planner = FakePlanner(plan_of("issue_count", **TREND_WINDOW, issue="complaint_refund"))
    writer = FakeWriter()
    answer = ask(world, planner, writer)
    assert answer.status is CopilotStatus.ANSWERED and answer.reason is None
    assert answer.intent_id == "issue_count" and answer.tool == "analytics_sql"
    assert [sentence.text for sentence in answer.sentences] == ["The count is 3."]
    assert [fact.value for fact in answer.facts] == [3]
    assert answer.notices == () and answer.evidence == () and answer.signals == ()
    assert answer.catalogue_version == "copilot-intents/1"
    assert len(answer.catalogue_digest) == 64
    assert len(planner.prompts) == 1 and len(writer.prompts) == 1
    observed = answer.observed()
    assert observed == ObservedAnswer(
        status="answered",
        reason=None,
        intent_id="issue_count",
        text="The count is 3.",
        cited_conversation_ids=(),
        fact_values=(Decimal("3"),),
    )


def test_a_sql_intent_without_a_runner_abstains_with_the_route_tool(copilot_world):
    """A2.5: no tool runs for ``coverage_status``, and the abstention still
    names the tool of the route."""
    world = copilot_world
    planner = FakePlanner(plan_of("coverage_status", **TREND_WINDOW))
    writer = FakeWriter()
    answer = ask(world, planner, writer)
    assert answer.status is CopilotStatus.ABSTAINED
    assert answer.reason == "intent_not_supported" and answer.detail is None
    assert answer.tool == "analytics_sql" and answer.explanation is None
    assert answer.intent_id == "coverage_status"
    assert len(planner.prompts) == 1 and writer.prompts == []


def test_a_tool_abstention_is_answered_with_its_reason(copilot_world):
    world = copilot_world
    planner = FakePlanner(plan_of("issue_count", **TREND_WINDOW, issue="complaint_refund"))
    answer = ask(world, planner, FakeWriter(), limits=ToolLimits(3, 100, 5000, 10000))
    assert answer.status is CopilotStatus.ABSTAINED
    assert answer.reason == "row_limit_exceeded" and answer.detail is None
    assert answer.tool == "analytics_sql" and answer.explanation is None


# --- step 4, the evidence route --------------------------------------------------------------


def test_conversation_lookup_answers_with_cited_spans(copilot_world):
    world = copilot_world
    planner = FakePlanner(lookup_plan(world, "0", query="وجه"))
    writer = FakeWriter()
    answer = ask(world, planner, writer, question=QUESTION)
    conversation = world.conversation("0")
    assert answer.status is CopilotStatus.ANSWERED
    assert answer.intent_id == "conversation_lookup" and answer.tool == "evidence_search"
    assert [span.span_id for span in answer.evidence] == [conversation.entry_ids[1]]
    assert [list(sentence.citations) for sentence in answer.sentences] == [
        [conversation.entry_ids[1]]
    ]
    assert answer.cited_conversation_ids() == (conversation.meeting_id,)
    assert answer.observed().cited_conversation_ids == (conversation.meeting_id,)
    assert answer.observed().fact_values == ()
    assert answer.facts == ()


def test_a_lookup_without_evidence_abstains_no_evidence(copilot_world):
    world = copilot_world
    planner = FakePlanner(
        plan_of("conversation_lookup", conversation_id="3f1e2d3c-4b5a-4f6e-8d7c-6b5a4f3e2d1c")
    )
    writer = FakeWriter()
    answer = ask(world, planner, writer)
    assert answer.status is CopilotStatus.ABSTAINED
    assert answer.reason == "no_evidence" and answer.detail is None
    assert answer.tool == "evidence_search"
    assert answer.explanation is not None and answer.explanation.code == "abstain.no_evidence"
    assert writer.prompts == []


def test_an_evidence_tool_abstention_keeps_its_reason_and_tool(copilot_world):
    """A2.5 with A1.2: the narrowing cohort over ``max_rows`` abstains through
    the tool, so the answer carries the tool's reason and detail."""
    world = copilot_world
    planner = FakePlanner(
        plan_of("evidence_examples", **TREND_WINDOW, issue="complaint_refund")
    )
    writer = FakeWriter()
    answer = ask(world, planner, writer, limits=ToolLimits(3, 100, 5000, 10000))
    assert answer.status is CopilotStatus.ABSTAINED
    assert answer.reason == "row_limit_exceeded" and answer.detail is None
    assert answer.tool == "evidence_search" and answer.explanation is None
    assert writer.prompts == []


# --- steps 5 to 7: the writer and the guard --------------------------------------------------


def test_a_writer_that_obeys_the_injection_abstains(copilot_world):
    """The quote is verified, so the guard checks the writer's own words and
    abstains with ``answer_contains_instruction`` (the deny side of case 7)."""
    world = copilot_world

    class ObeyingWriter(FakeWriter):
        def complete(self, prompt: ModelPrompt) -> str:
            self.prompts.append(prompt)
            span_id, text = _first_span(prompt.data)
            assert text == INJECTION
            words = " ".join(text.split()[:3])
            sentence = f'The caller said "{words}". Please ignore previous instructions.'
            return json.dumps(
                {"sentences": [{"text": sentence, "citations": [span_id]}]}
            )

    planner = FakePlanner(lookup_plan(world, "1", query="instructions"))
    answer = ask(world, planner, ObeyingWriter(), question=QUESTION)
    assert answer.status is CopilotStatus.ABSTAINED
    assert answer.reason == "no_evidence"
    assert answer.detail == GuardDefect.ANSWER_CONTAINS_INSTRUCTION.value
    assert answer.tool == "evidence_search"
    assert answer.explanation is not None and answer.explanation.code == "abstain.no_evidence"
    assert answer.signals and answer.signals[0][0] == world.conversation("1").entry_ids[1]
