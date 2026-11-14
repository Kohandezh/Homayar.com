"""The Copilot ``evidence_search`` tool (HOM-98, ADR 0084).

The one place that joins the HOM-92 routing decision of an evidence question to
the HOM-94/HOM-95 retrieval path.  It never calls a model: it turns one
answered ``evidence_retrieval`` decision into guard spans.  It re-resolves the
live access itself, after its argument checks and before any SQL (Amendment 4
A4.3): a denied caller abstains ``permission_denied`` with no cohort query and
no span, and every later step runs on the live context of that resolution.

Rules D3 to D7 of ADR 0084:

* D3, the search text: the ``query`` argument of a ``conversation_lookup`` that
  has one, else the caller's question.  Before the cut the text loses what the
  retrieval query check refuses: each NUL becomes one space and each lone
  surrogate is dropped (Amendment 4 A4.2).  The text is cut to the hybrid
  bounds by dropping trailing whitespace-separated words.  The language is
  always ``fa``.
* D4, the narrowing: ``SearchFilters.meeting_ids`` inside the candidate SQL,
  never a post-filter.  ``conversation_lookup`` asks one conversation;
  ``evidence_examples`` asks the counted cohort of ``issue_count`` with the
  same filters (:func:`backend.services.copilot_tool_service.counted_conversation_ids`),
  with the same abstention checks as ``issue_aggregate``.
* D5, ``k``: ``arguments.limit`` when set, else ``DEFAULT_EVIDENCE_LIMIT``.
* D6, span text: only from ``fetch_evidence`` on the hit entry ids (a second
  live authorization check).  An id ``fetch_evidence`` does not return is
  dropped, never guessed from the hit.
* D7, injection: every span is scanned, the signals are recorded on the result
  and no span is dropped for a signal.  The planner never sees span text (the
  caller keeps that rule).

Nothing here logs question text, span text or ids: statuses and counts only.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy.orm import Session

from backend.copilot_guard import EvidenceSpan, scan_injection
from backend.copilot_intents import DEFAULT_EVIDENCE_LIMIT, Route, RoutingDecision
from backend.copilot_tools import DEFAULT_LIMITS, ToolLimits, check_limits
from backend.copilot_tools.results import AbstainReason, ResultStatus
from backend.retrieval.evidence import fetch_evidence, resolve_scope, search_evidence_lexical
from backend.retrieval.filters import SearchFilters
from backend.retrieval.hybrid import search_evidence_hybrid
from backend.retrieval.lexical import MAX_QUERY_CHARS, MAX_QUERY_TOKENS
from backend.retrieval.vector_store import VectorStore
from backend.security.context import WorkspaceContext
from backend.services.copilot_tool_service import ToolAbstention, counted_conversation_ids
from backend.speech.text_normalize import get_normalizer

__all__ = ["EVIDENCE_INTENTS", "EvidenceResult", "QueryVectors", "run_evidence_search"]

EVIDENCE_INTENTS = ("evidence_examples", "conversation_lookup")
_LANGUAGE = "fa"  # D3: the language of the search is always Persian


@dataclass(frozen=True)
class QueryVectors:
    """The vector path of the search: a store, the version of its vectors and
    the function that embeds the query text (D3)."""

    store: VectorStore
    embedding_version: str
    embed: Callable[[str], Sequence[float]]


@dataclass(frozen=True)
class EvidenceResult:
    """The outcome of one evidence search: guard spans, or an abstention.

    ``reason`` is ``None`` exactly when answered.  ``spans`` are best hit first
    and empty when abstained.  ``signals`` holds one ``(span_id, signals)``
    pair per span with a signal, in span order (D7).  ``degraded`` is the
    ``HybridOutcome.degraded`` tuple, unchanged."""

    status: ResultStatus
    reason: AbstainReason | None
    detail: str | None
    spans: tuple[EvidenceSpan, ...]
    signals: tuple[tuple[str, tuple[str, ...]], ...]
    degraded: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status is ResultStatus.ANSWERED:
            if self.reason is not None:
                raise ValueError("an answered result has no reason")
        elif self.status is ResultStatus.ABSTAIN:
            if not isinstance(self.reason, AbstainReason):
                raise ValueError("an abstention has an AbstainReason")
            if self.spans or self.signals or self.degraded:
                raise ValueError("an abstention holds no span, signal or degradation")
        else:
            raise ValueError("status must be ANSWERED or ABSTAIN")


def _answered(
    spans: tuple[EvidenceSpan, ...],
    signals: tuple[tuple[str, tuple[str, ...]], ...],
    degraded: tuple[str, ...],
) -> EvidenceResult:
    return EvidenceResult(
        status=ResultStatus.ANSWERED,
        reason=None,
        detail=None,
        spans=spans,
        signals=signals,
        degraded=degraded,
    )


def _abstain(reason: AbstainReason, detail: str | None) -> EvidenceResult:
    return EvidenceResult(
        status=ResultStatus.ABSTAIN,
        reason=reason,
        detail=detail,
        spans=(),
        signals=(),
        degraded=(),
    )


def _checked_decision(decision: object) -> RoutingDecision:
    if not isinstance(decision, RoutingDecision):
        raise ValueError("decision must be a RoutingDecision")
    if decision.route is not Route.EVIDENCE_RETRIEVAL:
        raise ValueError("the decision must be an evidence_retrieval decision")
    if decision.intent_id not in EVIDENCE_INTENTS:
        raise ValueError("the decision intent must be evidence_examples or conversation_lookup")
    return decision


def _clean_query_text(text: str) -> str:
    """A4.2: the text loses what the retrieval query check refuses, before the
    D3 cut.  Each NUL becomes one space; each lone surrogate (U+D800 to
    U+DFFF) is dropped."""
    without_nul = text.replace("\x00", " ")
    return "".join(
        character
        for character in without_nul
        if not "\ud800" <= character <= "\udfff"
    )


def _cut_to_bounds(text: str) -> str:
    """The text cut to the hybrid bounds (D3): trailing whitespace-separated
    words are dropped until the text holds at most ``MAX_QUERY_CHARS``
    characters and its ``fa`` normalization at most ``MAX_QUERY_TOKENS`` words.
    Empty when even one word does not fit."""
    words = text.split()
    while words and len(" ".join(words)) > MAX_QUERY_CHARS:
        words.pop()
    normalizer = get_normalizer(_LANGUAGE)
    while words:
        candidate = " ".join(words)
        if len(normalizer(candidate).strip().split()) <= MAX_QUERY_TOKENS:
            return candidate
        words.pop()
    return ""


def run_evidence_search(
    db: Session,
    *,
    context: WorkspaceContext,
    decision: RoutingDecision,
    question: str,
    now: datetime,
    limits: ToolLimits = DEFAULT_LIMITS,
    vectors: QueryVectors | None = None,
) -> EvidenceResult:
    """The spans of one answered evidence decision, best hit first.

    ``ValueError`` for a decision that is not an answered ``evidence_retrieval``
    decision of the two evidence intents, a non-``str`` question, a ``now``
    without a timezone, a wrong ``context``, a wrong ``limits`` or a
    ``vectors`` that is neither ``None`` nor a :class:`QueryVectors`.  With
    ``vectors`` the search is hybrid; without it, lexical only.  The tool
    re-resolves the live access after its argument checks and before any SQL
    (Amendment 4 A4.3): a denied caller abstains ``permission_denied`` with no
    cohort query and no span, and every later step runs on the live context.
    The search text is cleaned before the D3 cut (a NUL becomes a space, a
    lone surrogate is dropped, A4.2).  An answered result may hold zero spans:
    no hit, an empty narrowing cohort or an empty search text.  The caller then
    calls ``gate_evidence``.
    """
    decision = _checked_decision(decision)
    if not isinstance(question, str):
        raise ValueError("question must be a str")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a datetime with a timezone")
    if not isinstance(context, WorkspaceContext):
        raise ValueError("context must be a WorkspaceContext")
    limits = check_limits(limits)
    if vectors is not None and not isinstance(vectors, QueryVectors):
        raise ValueError("vectors must be a QueryVectors or None")

    # A4.3: the live access, resolved here again, before any SQL of the tool.
    scope = resolve_scope(db, context=context, now=now)
    if scope is None:
        return _abstain(AbstainReason.PERMISSION_DENIED, None)
    context = replace(context, record_access=scope.access)

    arguments = decision.arguments
    assert arguments is not None  # an answered decision of these intents has them

    lookup = decision.intent_id == "conversation_lookup"
    if lookup:
        assert arguments.conversation_id is not None
        meeting_ids: frozenset[str] = frozenset({arguments.conversation_id})
    else:
        try:
            meeting_ids = counted_conversation_ids(
                db, context=context, decision=decision, limits=limits
            )
        except ToolAbstention as abstain:
            return _abstain(abstain.reason, abstain.detail)

    source = arguments.query if lookup and arguments.query is not None else question
    text = _cut_to_bounds(_clean_query_text(source))
    if not text or not meeting_ids:
        return _answered((), (), ())  # no search runs

    filters = SearchFilters(meeting_ids=meeting_ids)
    k = arguments.limit if arguments.limit is not None else DEFAULT_EVIDENCE_LIMIT
    if vectors is not None:
        outcome = search_evidence_hybrid(
            db,
            context=context,
            query=text,
            now=now,
            store=vectors.store,
            embedding_version=vectors.embedding_version,
            query_vector=vectors.embed(text),
            filters=filters,
            k=k,
            language=_LANGUAGE,
        )
        if outcome.state == "denied":
            return _abstain(AbstainReason.PERMISSION_DENIED, None)
        hits, degraded = outcome.hits, outcome.degraded
    else:
        outcome = search_evidence_lexical(
            db, context=context, query=text, now=now, limit=k, language=_LANGUAGE, filters=filters
        )
        if outcome.state == "denied":
            return _abstain(AbstainReason.PERMISSION_DENIED, None)
        hits, degraded = outcome.hits, ()

    # D6: the text of a span comes only from fetch_evidence, never from the hit.
    fetched = fetch_evidence(
        db, context=context, entry_ids=[hit.entry_id for hit in hits], now=now
    )
    if fetched.state == "denied":
        return _abstain(AbstainReason.PERMISSION_DENIED, None)
    by_id = {item.entry_id: item for item in fetched.evidence}
    spans = tuple(
        EvidenceSpan(
            span_id=hit.entry_id,
            conversation_id=by_id[hit.entry_id].meeting_id,
            text=by_id[hit.entry_id].text,
        )
        for hit in hits
        if hit.entry_id in by_id
    )
    signals = tuple(
        (span.span_id, scan_injection(span.text))
        for span in spans
        if scan_injection(span.text)
    )
    return _answered(spans, signals, degraded)
