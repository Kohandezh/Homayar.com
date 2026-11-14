"""The Copilot path: one question, one answer (HOM-98, ADR 0084 D1).

The only code that joins the guard (HOM-97), the SQL tool (HOM-96) and the
evidence tool (HOM-98) into one service function, :func:`answer_question`.  No
HTTP route lives here (that is HOM-239) and no model client either: the models
come in as the :class:`CopilotModel` protocol, so tests run with fakes and the
real adapter lands later.

The steps, in order, and the first stop decides (ADR 0084 section 2.3):

0. argument checks, then the live access of D2:
   ``resolve_scope`` re-reads the caller from live rows.  ``None`` refuses with
   ``permission_denied`` and no model call.  Every later step runs on the live
   context, ``replace(context, record_access=scope.access)``, so a long-lived
   context object never keeps access after a change (F57);
1. the planner prompt and the planner model;
2. ``route_plan`` with the caller's scope, never the model's;
3. a refusal answers the caller with ``explain``;
4. the tool of the route: ``analytics_sql`` (D8, the raw plan params of A1.1)
   or ``evidence_search`` and its ``gate_evidence``;
5. the answer prompt, with the facts map of the aggregate or the spans;
6. the writer model and ``check_answer``;
7. a guard abstention answers with ``explain``; else the answer is shown.

Model exceptions are not caught: the caller of a real model handles them
(HOM-239).  Nothing here logs question, plan, answer or span text (D9).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from functools import cache
from typing import Protocol

from sqlalchemy.orm import Session

from backend.copilot_guard import (
    AnswerSentence,
    EvidenceSpan,
    Explanation,
    ModelPrompt,
    ToolName,
    allowed_tools,
    build_answer_prompt,
    build_planner_prompt,
    check_answer,
    explain,
    parse_plan,
    route_plan,
)
from backend.copilot_intents import (
    IntentCatalogue,
    Route,
    RoutingDecision,
    gate_evidence,
    load_catalogue,
)
from backend.copilot_tools import DEFAULT_LIMITS, ToolLimits, check_limits
from backend.copilot_tools.results import Fact, ResultStatus
from backend.evaluation.copilot import ObservedAnswer
from backend.retrieval.evidence import resolve_scope
from backend.security.context import WorkspaceContext
from backend.services.copilot_evidence_service import (
    EvidenceResult,
    QueryVectors,
    run_evidence_search,
)
from backend.services.copilot_tool_service import ISSUE_INTENTS, caller_scope, run_tool

__all__ = [
    "CopilotAnswer",
    "CopilotModel",
    "CopilotStatus",
    "answer_question",
]


class CopilotModel(Protocol):
    """One model step of the Copilot.  Tests run fakes; a real adapter (an HTTP
    client, HOM-239) implements the same shape."""

    def complete(self, prompt: ModelPrompt) -> str: ...


class CopilotStatus(StrEnum):
    ANSWERED = "answered"
    REFUSED = "refused"
    ABSTAINED = "abstained"


@dataclass(frozen=True)
class CopilotAnswer:
    """One answer of :func:`answer_question`.

    ``reason`` is ``None`` exactly when answered.  ``tool`` is the
    ``ToolName`` value of the route (``analytics_sql`` or ``evidence_search``)
    once the routing answered, or ``None`` before that (a permission refusal
    or a routing refusal).  ``explanation`` is ``explain(decision)``
    of the routing, gate or guard decision that refused or abstained; a
    permission refusal of step 0 has no decision, so it is ``None`` there.
    ``facts``, ``evidence``, ``signals`` and ``notices`` hold the material of
    an answered decision only.  ``catalogue_version`` and
    ``catalogue_digest`` come from the decision when there is one, else
    ``None``."""

    status: CopilotStatus
    reason: str | None
    detail: str | None
    intent_id: str | None
    tool: str | None
    explanation: Explanation | None
    sentences: tuple[AnswerSentence, ...]
    facts: tuple[Fact, ...]
    evidence: tuple[EvidenceSpan, ...]
    signals: tuple[tuple[str, tuple[str, ...]], ...]
    notices: tuple[str, ...]
    catalogue_version: str | None
    catalogue_digest: str | None

    def __post_init__(self) -> None:
        if self.status is CopilotStatus.ANSWERED:
            if self.reason is not None:
                raise ValueError("an answered CopilotAnswer has no reason")
            if not isinstance(self.sentences, tuple) or not all(
                isinstance(sentence, AnswerSentence) for sentence in self.sentences
            ):
                raise ValueError("sentences must be a tuple of AnswerSentence")
        else:
            if not isinstance(self.reason, str) or not self.reason:
                raise ValueError("a refused or abstained answer has a reason")
            if self.sentences:
                raise ValueError("a refused or abstained answer holds no sentence")

    def cited_conversation_ids(self) -> tuple[str, ...]:
        """The sorted, distinct conversation ids of the spans the sentences cite."""
        conversation_of = {span.span_id: span.conversation_id for span in self.evidence}
        return tuple(
            sorted(
                {
                    conversation_of[citation]
                    for sentence in self.sentences
                    for citation in sentence.citations
                    if citation in conversation_of
                }
            )
        )

    def observed(self) -> ObservedAnswer:
        """The pure view of this answer the evaluation harness scores (SPEC 2.4)."""
        values: list[Decimal] = []
        for fact in self.facts:
            values.append(Decimal(str(fact.value)))
            if fact.parts is not None:
                values.append(Decimal(str(fact.parts[0])))
                values.append(Decimal(str(fact.parts[1])))
        return ObservedAnswer(
            status=self.status.value,
            reason=self.reason,
            intent_id=self.intent_id,
            text=" ".join(sentence.text for sentence in self.sentences),
            cited_conversation_ids=self.cited_conversation_ids(),
            fact_values=tuple(values),
        )


@cache
def _default_catalogue() -> IntentCatalogue:
    """The v1 catalogue, loaded once per process (immutable, pinned by digest)."""
    return load_catalogue()


def _model(name: str, model: object) -> None:
    if not callable(getattr(model, "complete", None)):
        raise ValueError(f"{name} must have a callable complete")


def _aware(now: object) -> None:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a datetime with a timezone")


def _fact_map(facts: Sequence[Fact]) -> dict[str, Decimal]:
    """The facts of step 5, by name: one key per fact, plus the numerator and
    the denominator of a fact with parts."""
    mapped: dict[str, Decimal] = {}
    for position, fact in enumerate(facts):
        key = f"{position}.{fact.name}"
        mapped[key] = Decimal(str(fact.value))
        if fact.parts is not None:
            mapped[f"{key}.numerator"] = Decimal(str(fact.parts[0]))
            mapped[f"{key}.denominator"] = Decimal(str(fact.parts[1]))
    return mapped


def answer_question(
    db: Session,
    *,
    context: WorkspaceContext,
    question: str,
    now: datetime,
    planner: CopilotModel,
    writer: CopilotModel,
    catalogue: IntentCatalogue | None = None,
    limits: ToolLimits = DEFAULT_LIMITS,
    vectors: QueryVectors | None = None,
) -> CopilotAnswer:
    """Answer one caller question through the guard, a tool and the writer.

    ``ValueError`` for a wrong ``context``, a non-``str`` question, a question
    that holds a lone surrogate (A4.6, checked here before anything else runs),
    a ``now`` without a timezone, a ``planner`` or ``writer`` without a
    callable ``complete``, a wrong ``catalogue``, a wrong ``limits`` or
    ``vectors`` that is neither ``None`` nor a :class:`QueryVectors`.  A
    denied caller (the live scope of D2) is refused with
    ``permission_denied`` before any model call.
    Model exceptions are not caught.
    """
    if not isinstance(context, WorkspaceContext):
        raise ValueError("context must be a WorkspaceContext")
    if not isinstance(question, str):
        raise ValueError("question must be a str")
    # A4.6: a lone surrogate is not text and the guard's ``frame_untrusted``
    # refuses it, so the question is rejected with the other argument checks,
    # before live access and before any model call.  A NUL is text (A4.2).
    if any("\ud800" <= character <= "\udfff" for character in question):
        raise ValueError("question must hold no lone surrogate")
    _aware(now)
    _model("planner", planner)
    _model("writer", writer)
    limits = check_limits(limits)
    if vectors is not None and not isinstance(vectors, QueryVectors):
        raise ValueError("vectors must be a QueryVectors or None")
    if catalogue is None:
        catalogue = _default_catalogue()
    elif not isinstance(catalogue, IntentCatalogue):
        raise ValueError("catalogue must be an IntentCatalogue")

    def stop(
        status: CopilotStatus,
        reason: str,
        decision: RoutingDecision | None,
        *,
        detail: str | None,
        tool: str | None,
        explanation: Explanation | None = None,
        signals: tuple[tuple[str, tuple[str, ...]], ...] = (),
    ) -> CopilotAnswer:
        return CopilotAnswer(
            status=status,
            reason=reason,
            detail=detail,
            intent_id=None if decision is None else decision.intent_id,
            tool=tool,
            explanation=explanation,
            sentences=(),
            facts=(),
            evidence=(),
            signals=signals,
            notices=(),
            catalogue_version=None if decision is None else decision.catalogue_version,
            catalogue_digest=None if decision is None else decision.catalogue_digest,
        )

    # Step 0, D2: the live access.  Denied: no model is called.
    scope = resolve_scope(db, context=context, now=now)
    if scope is None:
        return stop(
            CopilotStatus.REFUSED, "permission_denied", None, detail=None, tool=None
        )
    live = replace(context, record_access=scope.access)

    # Steps 1 and 2: the planner sees the question only, the scope is the caller's.
    planner_prompt = build_planner_prompt(question, catalogue)
    raw_plan = planner.complete(planner_prompt)
    decision = route_plan(raw_plan, catalogue, scope=caller_scope(live), now=now)

    # Step 3: a refusal answers the caller.
    if decision.route is Route.REFUSE:
        assert decision.reason is not None
        return stop(
            CopilotStatus.REFUSED,
            decision.reason.value,
            decision,
            detail=decision.detail,
            tool=None,
            explanation=explain(decision),
        )

    tools = allowed_tools(decision)
    spans: tuple[EvidenceSpan, ...] = ()
    signals: tuple[tuple[str, tuple[str, ...]], ...] = ()
    facts: tuple[Fact, ...] = ()
    tool: str | None = None
    if ToolName.ANALYTICS_SQL in tools:
        if decision.intent_id not in ISSUE_INTENTS:
            # D8: the planner path answers the three issue intents only.  No tool
            # runs, but the answer names the tool of the route (Amendment 2 A2.5).
            return stop(
                CopilotStatus.ABSTAINED,
                "intent_not_supported",
                decision,
                detail=None,
                tool=ToolName.ANALYTICS_SQL.value,
            )
        request = parse_plan(raw_plan)  # it passed route_plan, so it parses
        assert request is not None
        result = run_tool(
            db,
            context=live,
            name="issue_aggregate",
            params={"intent": request.intent, "params": request.params},
            now=now,
            limits=limits,
        )
        tool = ToolName.ANALYTICS_SQL.value
        if result.status is ResultStatus.ABSTAIN:
            assert result.reason is not None
            return stop(
                CopilotStatus.ABSTAINED,
                result.reason.value,
                decision,
                detail=result.detail,
                tool=tool,
            )
        facts = result.facts
    else:
        tool = ToolName.EVIDENCE_SEARCH.value
        evidence: EvidenceResult = run_evidence_search(
            db,
            context=live,
            decision=decision,
            question=question,
            now=now,
            limits=limits,
            vectors=vectors,
        )
        if evidence.status is ResultStatus.ABSTAIN:
            assert evidence.reason is not None
            return stop(
                CopilotStatus.ABSTAINED,
                evidence.reason.value,
                decision,
                detail=evidence.detail,
                tool=tool,
            )
        decision = gate_evidence(decision, len(evidence.spans))
        if decision.route is Route.ABSTAIN:
            return stop(
                CopilotStatus.ABSTAINED,
                "no_evidence",
                decision,
                detail=None,
                tool=tool,
                explanation=explain(decision),
                signals=evidence.signals,
            )
        spans = evidence.spans
        signals = evidence.signals

    # Step 5: the answer prompt, with the fact map of an aggregate (SPEC 2.3).
    fact_map = _fact_map(facts)
    if fact_map:
        answer_prompt = build_answer_prompt(decision, question=question, facts=fact_map)
    else:
        answer_prompt = build_answer_prompt(decision, question=question, evidence=spans)

    # Steps 6 and 7: the writer, then the guard.
    raw_answer = writer.complete(answer_prompt)
    guarded = check_answer(
        decision,
        raw_answer,
        evidence=spans,
        allowed_numbers=tuple(fact_map.values()),
    )
    if guarded.decision.route is Route.ABSTAIN:
        assert guarded.decision.reason is not None
        return stop(
            CopilotStatus.ABSTAINED,
            guarded.decision.reason.value,
            guarded.decision,
            detail=guarded.decision.detail,
            tool=tool,
            explanation=explain(guarded.decision),
            signals=signals,
        )
    return CopilotAnswer(
        status=CopilotStatus.ANSWERED,
        reason=None,
        detail=None,
        intent_id=decision.intent_id,
        tool=tool,
        explanation=None,
        sentences=guarded.sentences or (),
        facts=facts,
        evidence=spans,
        signals=signals,
        notices=decision.notices,
        catalogue_version=decision.catalogue_version,
        catalogue_digest=decision.catalogue_digest,
    )
