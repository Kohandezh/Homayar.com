"""HOM-98 (SPEC 2.2): the ``evidence_search`` tool of the Copilot, rules D3 to D7.

One shared world (``tests/_copilot_world.py``): four conversations of one
workspace, three of them the counted ``complaint_refund`` cohort, one of them
carrying a prompt injection in its second segment.  The checks: the search
text (D3), the narrowing and its abstentions (D4), ``limit`` (D5), the span
shape and the fetch-by-id rule (D6), the injection signals (D7), the vectors
path, the denied caller and the zero-span answers.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import update

from backend.copilot_guard import EvidenceSpan, InjectionSignal, route_plan
from backend.copilot_intents import CallerScope, Route
from backend.copilot_tools import ToolLimits
from backend.copilot_tools.results import AbstainReason, ResultStatus
from backend.database.saas_models import Membership
from backend.services.copilot_evidence_service import (
    EvidenceResult,
    QueryVectors,
    run_evidence_search,
)
from backend.tests._copilot_world import (
    NOW,
    QUESTION,
    STORE,
    TREND_WINDOW,
    CopilotWorld,
    catalogue,
    plan_of,
    route_of,
    seed_copilot_world,
    vectors_of,
)
from backend.tests._database import inspection_session_factory
from backend.tests.test_copilot_tool_service import UNKNOWN_TOPIC
from backend.tests.test_coverage import Tenant
from backend.tests.test_issue_trend_service import BASE as TREND_BASE
from backend.tests.test_issue_trend_service import COMPLAINT, add_user, seed
from backend.tests.test_retrieval_evidence import (
    _bound,
    _captured_statements,
    _context,
    _touched_index,
)
from backend.tests.test_retrieval_index import _version


@pytest.fixture
def copilot_world(client, email_outbox) -> CopilotWorld:
    return seed_copilot_world(client, email_outbox)


def evidence(world, decision, question: str = QUESTION, **kwargs) -> EvidenceResult:
    with _bound(world.workspace_id) as db:
        return run_evidence_search(
            db,
            context=world.context(),
            decision=decision,
            question=question,
            now=kwargs.pop("now", NOW),
            **kwargs,
        )


def lookup_plan(world, label: str, query: str | None = None) -> str:
    params = {"conversation_id": world.conversation(label).meeting_id}
    if query is not None:
        params["query"] = query
    return plan_of("conversation_lookup", **params)


def examples_plan(**params: object) -> str:
    return plan_of("evidence_examples", **TREND_WINDOW, issue="complaint_refund", **params)


# --- the argument checks --------------------------------------------------------------------


def test_the_argument_checks_are_value_errors(copilot_world):
    world = copilot_world
    answered = route_of(lookup_plan(world, "0"), world)
    assert answered.route is Route.EVIDENCE_RETRIEVAL
    refused = route_of(plan_of("forecast"), world)
    aggregate = route_of(plan_of("issue_count", family="call_reason"), world)
    foreign = replace(answered, intent_id="conversation_lookupx")
    checks = [
        lambda: evidence(world, refused),
        lambda: evidence(world, aggregate),
        lambda: evidence(world, foreign),
        lambda: evidence(world, answered, question=7),  # type: ignore[arg-type]
        lambda: evidence(world, answered, now=datetime(2026, 1, 1)),  # type: ignore[arg-type]
    ]
    for check in checks:
        with pytest.raises(ValueError):
            check()


# --- D3: the search text --------------------------------------------------------------------


def test_a_lookup_with_a_query_searches_the_query_not_the_question(copilot_world):
    """The query hits only the second segment of conversation 0; the question
    would hit the first segment of every conversation."""
    world = copilot_world
    decision = route_of(lookup_plan(world, "0", query="وجه"), world)
    result = evidence(world, decision, question="سلام")
    assert result.status is ResultStatus.ANSWERED
    conversation = world.conversation("0")
    assert [span.span_id for span in result.spans] == [conversation.entry_ids[1]]


def test_a_lookup_without_a_query_searches_the_question(copilot_world):
    world = copilot_world
    decision = route_of(lookup_plan(world, "2"), world)
    result = evidence(world, decision, question="صورت‌حساب")
    conversation = world.conversation("2")
    assert result.status is ResultStatus.ANSWERED
    assert [span.span_id for span in result.spans] == [conversation.entry_ids[1]]


def test_the_text_is_cut_to_the_hybrid_bounds(copilot_world):
    world = copilot_world
    decision = route_of(lookup_plan(world, "0"), world)
    # 40 words (over the 32-word bound) of one matching token: the cut keeps
    # the search inside the bound, and the token still matches.
    long_question = " ".join(["سلام"] * 40)
    cut = evidence(world, decision, question=long_question)
    assert cut.status is ResultStatus.ANSWERED and cut.spans
    # One word longer than the character bound: nothing fits, no search runs.
    giant = evidence(world, decision, question="س" * 1001)
    assert giant.status is ResultStatus.ANSWERED and giant.spans == ()


def test_a_text_that_is_empty_after_strip_gives_zero_spans(copilot_world):
    world = copilot_world
    decision = route_of(lookup_plan(world, "0"), world)
    result = evidence(world, decision, question="   ")
    assert result.status is ResultStatus.ANSWERED
    assert result.spans == () and result.signals == () and result.degraded == ()


# --- A4.2: the characters the retrieval query check refuses ----------------------------------


def test_a_nul_in_the_question_never_raises(copilot_world):
    """A4.2: each NUL becomes one space before the D3 cut, so the retrieval
    query check never sees it.  Both routes that search the question."""
    world = copilot_world
    examples = route_of(examples_plan(), world)
    first = evidence(world, examples, question="refund\x00 delay")
    assert first.status is ResultStatus.ANSWERED  # no hit for that text, no error
    lookup = route_of(lookup_plan(world, "0"), world)  # no query argument
    second = evidence(world, lookup, question="refund\x00 delay")
    assert second.status is ResultStatus.ANSWERED and second.spans == ()
    # The cleaned text still searches: the NUL became a space.
    matching = evidence(world, lookup, question="سلام\x00 دنیا")
    assert matching.status is ResultStatus.ANSWERED
    assert world.conversation("0").entry_ids[0] in {span.span_id for span in matching.spans}


def test_a_question_of_only_a_nul_gives_zero_spans_and_no_search(copilot_world):
    world = copilot_world
    decision = route_of(lookup_plan(world, "0"), world)
    with _bound(world.workspace_id) as db:
        with _captured_statements(db) as statements:
            result = run_evidence_search(
                db,
                context=world.context(),
                decision=decision,
                question="\x00",
                now=NOW,
            )
    assert result.status is ResultStatus.ANSWERED and result.spans == ()
    assert _touched_index(statements) == []  # the text is blank, no search ran


def test_a_lone_surrogate_is_dropped_from_the_search_text(copilot_world):
    """A4.2 and A4.6: called directly, the tool drops lone surrogates from the
    search text (it is ``answer_question`` that rejects the question)."""
    world = copilot_world
    decision = route_of(lookup_plan(world, "0"), world)
    first = evidence(world, decision, question="refund\ud800 delay")
    assert first.status is ResultStatus.ANSWERED and first.spans == ()
    matching = evidence(world, decision, question="\udfffسلام")
    assert matching.status is ResultStatus.ANSWERED
    assert world.conversation("0").entry_ids[0] in {span.span_id for span in matching.spans}


def test_the_cleaning_rule_itself_is_pinned(copilot_world):
    """F5: a NUL becomes one space (an implementation that cuts the text at
    the NUL would lose every word after it) and a lone surrogate is dropped
    (a space would split the word).  The lexical match needs every token, so
    the pin puts the NUL first: only a surviving word can hit."""
    world = copilot_world
    decision = route_of(lookup_plan(world, "0"), world)  # no query argument
    greeting = world.conversation("0").entry_ids[0]
    nul = evidence(world, decision, question="\x00سلام")
    assert nul.status is ResultStatus.ANSWERED
    assert greeting in {span.span_id for span in nul.spans}
    surrogate = evidence(world, decision, question="سل\ud800ام")
    assert surrogate.status is ResultStatus.ANSWERED
    assert greeting in {span.span_id for span in surrogate.spans}


# --- D4: the narrowing ----------------------------------------------------------------------


def test_a_lookup_narrows_to_one_conversation(copilot_world):
    world = copilot_world
    decision = route_of(lookup_plan(world, "1"), world)
    result = evidence(world, decision, question="سلام")
    conversation = world.conversation("1")
    assert [span.span_id for span in result.spans] == [conversation.entry_ids[0]]
    assert {span.conversation_id for span in result.spans} == {conversation.meeting_id}


def test_examples_narrow_to_the_counted_cohort(copilot_world):
    """The question hits every conversation; the cohort holds only the three
    complaint conversations, never the billing one."""
    world = copilot_world
    decision = route_of(examples_plan(), world)
    result = evidence(world, decision, question=QUESTION)
    assert result.status is ResultStatus.ANSWERED
    assert {span.conversation_id for span in result.spans} == set(
        world.meeting_ids("0", "1", "2")
    )


def test_an_empty_cohort_gives_zero_spans_without_a_search(copilot_world):
    world = copilot_world
    plan = plan_of(
        "evidence_examples",
        **TREND_WINDOW,
        issue="complaint_refund",
        team_id=world.other_team_id,
    )
    decision = route_of(plan, world)
    assert decision.route is Route.EVIDENCE_RETRIEVAL
    with _bound(world.workspace_id) as db:
        with _captured_statements(db) as statements:
            result = run_evidence_search(
                db,
                context=world.context(),
                decision=decision,
                question=QUESTION,
                now=NOW,
            )
    assert result.status is ResultStatus.ANSWERED and result.spans == ()
    assert _touched_index(statements) == []  # the cohort ran, the index did not


def test_a_population_over_max_rows_abstains_row_limit_exceeded(copilot_world):
    world = copilot_world
    decision = route_of(examples_plan(), world)
    result = evidence(world, decision, limits=ToolLimits(3, 100, 5000, 10000))
    assert result.status is ResultStatus.ABSTAIN
    assert result.reason is AbstainReason.ROW_LIMIT_EXCEEDED
    assert result.detail is None and result.spans == ()


def test_a_masked_cohort_abstains_withheld(client, email_outbox):
    """The HOM-82 all-or-nothing rule: 31 calls of one named agent, only 6 of
    them a refund complaint, so a row of the window layer sits under the agent
    minimum.  A viewer who is not the agent gets the layer withheld, exactly as
    ``issue_aggregate`` does (the recipe of the HOM-96 masked fixture)."""
    tenant = Tenant(client, email_outbox, "hom98-evidence-withheld@example.com")
    # The fixed NOW of this suite must not depend on the registration clock
    # (the live check of A4.3 re-reads the membership against NOW).
    with inspection_session_factory()() as db:
        db.execute(
            update(Membership)
            .where(Membership.workspace_id == tenant.workspace_id)
            .values(valid_from=NOW - timedelta(days=365))
        )
        db.commit()
    agent = add_user("hom98-evidence-agent")
    for index in range(31):
        seed(
            tenant,
            received_at=TREND_BASE + timedelta(minutes=index),
            extraction=UNKNOWN_TOPIC if index < 25 else COMPLAINT,
            agent_user_id=agent,
        )
    plan = plan_of(
        "evidence_examples",
        **TREND_WINDOW,
        issue="intent_report_dissatisfaction",
        agent_user_id=agent,
    )
    decision = route_plan(
        plan,
        catalogue(),
        scope=CallerScope(workspace_id=tenant.workspace_id, team_ids=None, agent_user_ids=None),
        now=NOW,
    )
    with _bound(tenant.workspace_id) as db:
        result = run_evidence_search(
            db,
            context=_context(tenant.owner_id, tenant.workspace_id),
            decision=decision,
            question=QUESTION,
            now=NOW,
        )
    assert result.status is ResultStatus.ABSTAIN
    assert result.reason is AbstainReason.WITHHELD
    assert result.spans == ()


# --- D5 and D6 ------------------------------------------------------------------------------


def test_limit_caps_the_spans(copilot_world):
    world = copilot_world
    decision = route_of(examples_plan(limit=1), world)
    result = evidence(world, decision, question=QUESTION)
    assert result.status is ResultStatus.ANSWERED
    assert len(result.spans) == 1
    assert result.spans[0].conversation_id in world.meeting_ids("0", "1", "2")


def test_a_span_is_the_entry_id_the_conversation_id_and_the_text(copilot_world):
    world = copilot_world
    decision = route_of(lookup_plan(world, "3", query="فاکتور"), world)
    result = evidence(world, decision, question="سلام")
    conversation = world.conversation("3")
    assert result.status is ResultStatus.ANSWERED
    assert result.spans == (
        EvidenceSpan(
            span_id=conversation.entry_ids[1],
            conversation_id=conversation.meeting_id,
            text=conversation.texts[1],
        ),
    )


def test_a_made_up_conversation_gives_zero_spans_and_no_error(copilot_world):
    world = copilot_world
    plan = plan_of(
        "conversation_lookup", conversation_id="3f1e2d3c-4b5a-4f6e-8d7c-6b5a4f3e2d1c"
    )
    decision = route_of(plan, world)
    result = evidence(world, decision, question="سلام")
    assert result.status is ResultStatus.ANSWERED and result.spans == ()


# --- D7: the injection signals --------------------------------------------------------------


def test_an_injected_span_keeps_its_text_and_gives_a_signal(copilot_world):
    world = copilot_world
    decision = route_of(lookup_plan(world, "1", query="instructions"), world)
    result = evidence(world, decision, question="سلام")
    assert result.status is ResultStatus.ANSWERED
    conversation = world.conversation("1")
    injected = conversation.entry_ids[1]
    assert injected in {span.span_id for span in result.spans}  # no span is dropped
    assert result.signals == ((injected, (InjectionSignal.IGNORE_INSTRUCTIONS,)),)
    # The signal order follows the span order and holds only signalled spans.
    order = [span.span_id for span in result.spans]
    assert [span_id for span_id, _ in result.signals] == [
        span_id for span_id in order if span_id == injected
    ]


# --- the vectors path and the denied caller -------------------------------------------------


def test_the_vectors_path_runs_the_hybrid_search(copilot_world):
    world = copilot_world
    decision = route_of(lookup_plan(world, "0", query="وجه"), world)
    vectors = QueryVectors(
        store=STORE, embedding_version=_version(), embed=lambda text: vectors_of(text)
    )
    result = evidence(world, decision, question="سلام", vectors=vectors)
    assert result.status is ResultStatus.ANSWERED and result.degraded == ()
    conversation = world.conversation("0")
    assert {span.span_id for span in result.spans} == set(conversation.entry_ids)
    assert {span.text for span in result.spans} == set(conversation.texts)


def test_a_denied_caller_abstains_permission_denied(copilot_world):
    world = copilot_world
    with inspection_session_factory()() as db:
        db.execute(
            update(Membership)
            .where(Membership.workspace_id == world.workspace_id)
            .values(valid_from=NOW + timedelta(days=1))
        )
        db.commit()
    decision = route_of(examples_plan(), world)
    result = evidence(world, decision)
    assert result.status is ResultStatus.ABSTAIN
    assert result.reason is AbstainReason.PERMISSION_DENIED
    assert result.detail is None and result.spans == ()


# --- A4.3: the live access resolved inside the tool, before any SQL --------------------------


def _deny_membership(world) -> None:
    with inspection_session_factory()() as db:
        db.execute(
            update(Membership)
            .where(Membership.workspace_id == world.workspace_id)
            .values(valid_from=NOW + timedelta(days=1))
        )
        db.commit()


def test_denied_access_abstains_before_the_cohort_query(copilot_world):
    """A4.3: the scope is resolved after the argument checks and before the
    narrowing, so a denied caller abstains ``permission_denied`` even when the
    cohort would abstain ``row_limit_exceeded`` first."""
    world = copilot_world
    _deny_membership(world)
    decision = route_of(examples_plan(), world)
    result = evidence(world, decision, limits=ToolLimits(1, 100, 5000, 10000))
    assert result.status is ResultStatus.ABSTAIN
    assert result.reason is AbstainReason.PERMISSION_DENIED
    assert result.detail is None and result.spans == ()


def test_denied_access_abstains_even_with_a_blank_question(copilot_world):
    """A4.3: the denial comes before the blank-text shortcut of D3, so a blank
    question of a denied caller is not an answered zero-span result."""
    world = copilot_world
    _deny_membership(world)
    decision = route_of(examples_plan(), world)
    result = evidence(world, decision, question="")
    assert result.status is ResultStatus.ABSTAIN
    assert result.reason is AbstainReason.PERMISSION_DENIED
    assert result.spans == ()
