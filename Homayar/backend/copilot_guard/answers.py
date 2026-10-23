"""The check of the answer writer's output (HOM-97).

``check_answer`` reads the writer's JSON and turns an answer that cited evidence does not
support into an abstention: ``no_evidence``, with a ``GuardDefect`` as the detail.  A
guard never turns "no" into "yes": a refused or abstained decision comes back unchanged.
Pure: no database, no network, no clock.  It never changes its inputs.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from backend.copilot_guard.defects import GuardDefect, MalformedJson, check_decision, strict_loads
from backend.copilot_guard.framing import EvidenceSpan, check_spans
from backend.copilot_guard.signals import normalize_text, scan_injection
from backend.copilot_intents import AbstainReason, Route, RoutingDecision

MAX_ANSWER_LENGTH = 20000
MAX_SENTENCES = 20
MAX_SENTENCE_LENGTH = 1000

_ANSWER_KEYS = frozenset({"sentences"})
_SENTENCE_KEYS = frozenset({"text", "citations"})
# Opening mark: closing mark.  Inside a quote, only its own closing mark ends it.
_QUOTE_MARKS = {'"': '"', "“": "”", "«": "»"}
_CLOSING_ONLY = frozenset({"”", "»"})
# \d is every Unicode decimal digit (Nd), so 0-9, ۰-۹ and ٠-٩ and every other script.  A
# group separator (, or U+066C) stands only before exactly three digits, after a lead of one
# to three digits.  Any other comma splits the text, so "12,34" is the numbers 12 and 34.
_NUMBER = re.compile(r"\d{1,3}(?:[,\u066c]\d{3}(?!\d))+(?:[.\u066b]\d+)?|\d+(?:[.\u066b]\d+)?")
_GROUP_SEPARATORS = frozenset({",", "\u066c"})
_DECIMAL_POINTS = frozenset({".", "\u066b"})
# The checks read normalized text while the user sees the raw text.  So a sentence may hold
# no control, separator or format character (a bidi override can show 42 as 24), except
# the Persian joiners ZWNJ and ZWJ, and no lone surrogate (it is not text).
_HIDDEN_CATEGORIES = frozenset({"Cc", "Zl", "Zp", "Cs"})
_JOINERS = frozenset({"\u200c", "\u200d"})
# A rendered link or Markdown image can send data out of a page.  The writer never needs one.
_LINK_MARKERS = ("://", "www.", "](", "![")


@dataclass(frozen=True)
class AnswerSentence:
    text: str  # as the writer wrote it
    citations: tuple[str, ...]  # span ids, in the writer's order

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("text must be a str")
        if not isinstance(self.citations, tuple) or not all(
            isinstance(citation, str) for citation in self.citations
        ):
            raise ValueError("citations must be a tuple of str")


@dataclass(frozen=True)
class GuardedAnswer:
    decision: RoutingDecision
    sentences: tuple[AnswerSentence, ...] | None  # None exactly when refused or abstained

    def __post_init__(self) -> None:
        decision = check_decision(self.decision)
        closed = decision.route in (Route.REFUSE, Route.ABSTAIN)
        if closed != (self.sentences is None):
            raise ValueError("sentences is None exactly when the decision is refuse or abstain")
        if self.sentences is not None and (
            not isinstance(self.sentences, tuple)
            or not all(isinstance(sentence, AnswerSentence) for sentence in self.sentences)
        ):
            raise ValueError("sentences must be a tuple of AnswerSentence")


def _allowed_numbers(numbers: object) -> tuple[Decimal, ...]:
    if not isinstance(numbers, Iterable) or isinstance(numbers, str | bytes | Mapping):
        raise ValueError("allowed_numbers must be a collection of finite Decimal")
    items = tuple(numbers)
    for item in items:
        if not isinstance(item, Decimal) or not item.is_finite():
            raise ValueError("allowed_numbers must be a collection of finite Decimal")
    return items


def _hidden(char: str) -> bool:
    category = unicodedata.category(char)
    return category in _HIDDEN_CATEGORIES or (category == "Cf" and char not in _JOINERS)


def _has_link(text: str) -> bool:
    """A link or Markdown marker, also in full-width or upper-case form."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return any(marker in folded for marker in _LINK_MARKERS)


def _sentences(raw: str) -> tuple[AnswerSentence, ...] | None:
    """The sentences of the exact shape ``{"sentences": [{"text", "citations"}, ...]}``,
    or ``None`` when the answer is malformed.  A text with a hidden character or a link
    marker is malformed too (README "Check order", step 2)."""
    if len(raw) > MAX_ANSWER_LENGTH:
        return None
    try:
        document = strict_loads(raw)
    except MalformedJson:
        return None
    if not isinstance(document, dict) or set(document) != _ANSWER_KEYS:
        return None
    items = document["sentences"]
    if not isinstance(items, list) or len(items) > MAX_SENTENCES:
        return None
    sentences = []
    for item in items:
        if not isinstance(item, dict) or set(item) != _SENTENCE_KEYS:
            return None
        text, citations = item["text"], item["citations"]
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_SENTENCE_LENGTH:
            return None
        if any(_hidden(char) for char in text) or _has_link(text):
            return None
        if not isinstance(citations, list) or not all(isinstance(c, str) for c in citations):
            return None
        if len(set(citations)) != len(citations):
            return None
        sentences.append(AnswerSentence(text=text, citations=tuple(citations)))
    return tuple(sentences)


@dataclass(frozen=True)
class _Quote:
    start: int  # the index of the opening mark
    end: int  # the index after the closing mark
    text: str  # between the marks


def _quotes(text: str) -> tuple[_Quote, ...] | None:
    """Every quote of ``text``, left to right, or ``None`` when a mark is unpaired."""
    quotes = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in _CLOSING_ONLY:
            return None
        closing = _QUOTE_MARKS.get(char)
        if closing is None:
            index += 1
            continue
        end = text.find(closing, index + 1)
        if end < 0:
            return None
        quotes.append(_Quote(start=index, end=end + 1, text=text[index + 1 : end]))
        index = end + 1
    return tuple(quotes)


def _word_char(char: str) -> bool:
    """A letter, a number or a combining mark: a quote may not start or end next to one."""
    return unicodedata.category(char)[0] in "LNM"


@dataclass(frozen=True)
class _SpanText:
    """A span's normalized text and its word edges, computed once per check."""

    text: str
    starts: tuple[int, ...]  # sorted: the indexes where a quote may start
    is_start: bytes  # is_start[i] == 1 when a quote may start at i
    is_end: bytes  # is_end[j] == 1 when a quote may end just before j

    @classmethod
    def of(cls, raw: str) -> _SpanText:
        text = normalize_text(raw)
        word = [_word_char(char) for char in text] + [False]
        is_start = bytes(1 if i == 0 or not word[i - 1] else 0 for i in range(len(text) + 1))
        is_end = bytes(0 if word[j] else 1 for j in range(len(text) + 1))
        starts = tuple(i for i in range(len(text)) if is_start[i])
        return cls(text=text, starts=starts, is_start=is_start, is_end=is_end)


class _Spans:
    """The given spans by id.  The word edges of a span are computed only when a quote
    first needs them, so a long span costs nothing on an answer that does not quote it."""

    def __init__(self, spans: Sequence[EvidenceSpan]) -> None:
        self._raw = {span.span_id: span.text for span in spans}
        self._done: dict[str, _SpanText] = {}

    def __contains__(self, span_id: object) -> bool:
        return span_id in self._raw

    def text(self, span_id: str) -> _SpanText:
        if span_id not in self._done:
            self._done[span_id] = _SpanText.of(self._raw[span_id])
        return self._done[span_id]


def _verified(quote: str, cited: Sequence[_SpanText]) -> bool:
    """The normalized quote holds a letter, and one cited span (normalized) holds it,
    starting and ending on a word edge of that span."""
    needle = normalize_text(quote)
    if not any(unicodedata.category(char).startswith("L") for char in needle):
        return False
    for span in cited:
        found = span.text.find(needle)
        while found >= 0:
            if span.is_start[found]:
                if span.is_end[found + len(needle)]:
                    return True
                after = found + 1
            else:
                # Jump to the next word start: a long run of letters cannot slow the search.
                index = bisect_right(span.starts, found)
                if index == len(span.starts):
                    break
                after = span.starts[index]
            found = span.text.find(needle, after)
    return False


def _without_quotes(text: str, quotes: tuple[_Quote, ...]) -> str:
    """The writer's own words: each quote, with its marks, becomes one space."""
    parts = []
    index = 0
    for quote in quotes:
        parts.append(text[index : quote.start])
        parts.append(" ")
        index = quote.end
    parts.append(text[index:])
    return "".join(parts)


def _number_value(match: str) -> Decimal:
    digits = []
    for char in match:
        if char in _GROUP_SEPARATORS:
            continue
        digits.append("." if char in _DECIMAL_POINTS else str(unicodedata.decimal(char)))
    return Decimal("".join(digits))


def _numbers(text: str) -> tuple[Decimal, ...]:
    """Every number of ``text`` after normalization (so full-width digits count).  A sign
    is not part of a number, and ``%`` or ``٪`` after it does not change its value."""
    return tuple(_number_value(match.group()) for match in _NUMBER.finditer(normalize_text(text)))


def _sentence_defect(
    route: Route,
    sentence: AnswerSentence,
    spans: _Spans,
    allowed: tuple[Decimal, ...],
) -> GuardDefect | None:
    """The first defect of one sentence, in the README order."""
    if sentence.citations and (
        route is Route.SQL_AGGREGATE or any(c not in spans for c in sentence.citations)
    ):
        return GuardDefect.CITATION_UNKNOWN
    if route is Route.EVIDENCE_RETRIEVAL and not sentence.citations:
        return GuardDefect.CITATION_MISSING
    quotes = _quotes(sentence.text)
    if quotes is None:
        return GuardDefect.QUOTE_NOT_IN_EVIDENCE
    cited = [spans.text(citation) for citation in sentence.citations] if quotes else []
    if not all(_verified(quote.text, cited) for quote in quotes):
        return GuardDefect.QUOTE_NOT_IN_EVIDENCE
    own_words = _without_quotes(sentence.text, quotes)
    if scan_injection(own_words):
        return GuardDefect.ANSWER_CONTAINS_INSTRUCTION
    numbers = _numbers(own_words)
    if numbers and (
        route is Route.EVIDENCE_RETRIEVAL or any(number not in allowed for number in numbers)
    ):
        return GuardDefect.NUMBER_NOT_FROM_AGGREGATE
    return None


def _abstain(decision: RoutingDecision, defect: GuardDefect) -> GuardedAnswer:
    abstained = replace(
        decision, route=Route.ABSTAIN, reason=AbstainReason.NO_EVIDENCE, detail=defect.value
    )
    return GuardedAnswer(decision=abstained, sentences=None)


def check_answer(
    decision: RoutingDecision,
    raw_answer: str,
    *,
    evidence: Sequence[EvidenceSpan] = (),
    allowed_numbers: Collection[Decimal] = (),
) -> GuardedAnswer:
    """Check the answer writer's output.  The first defect decides (README "The answer
    check"):

    1. a refused or abstained decision: the same decision, ``sentences`` ``None``;
    2. not the exact sentences shape: ``answer_malformed``;
    3. zero sentences: ``answer_empty``;
    4. each sentence in order: ``citation_unknown``, ``citation_missing``,
       ``quote_not_in_evidence``, ``answer_contains_instruction``,
       ``number_not_from_aggregate``;
    5. no defect: the same decision and the sentences.

    Every abstention is ``replace(decision, route=abstain, reason=no_evidence,
    detail=<defect>)``.  ``ValueError`` first, for every route: a decision that is not a
    ``RoutingDecision``, bad spans or two spans with one id, an allowed number that is not
    a finite ``Decimal``, or a ``raw_answer`` that is not a str.
    """
    decision = check_decision(decision)
    spans = check_spans(evidence)
    allowed = _allowed_numbers(allowed_numbers)
    if not isinstance(raw_answer, str):
        raise ValueError("raw_answer must be a str")
    if decision.route in (Route.REFUSE, Route.ABSTAIN):
        return GuardedAnswer(decision=decision, sentences=None)
    sentences = _sentences(raw_answer)
    if sentences is None:
        return _abstain(decision, GuardDefect.ANSWER_MALFORMED)
    if not sentences:
        return _abstain(decision, GuardDefect.ANSWER_EMPTY)
    given = _Spans(spans)
    for sentence in sentences:
        defect = _sentence_defect(decision.route, sentence, given, allowed)
        if defect is not None:
            return _abstain(decision, defect)
    return GuardedAnswer(decision=decision, sentences=sentences)
