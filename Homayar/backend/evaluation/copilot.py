"""The Copilot evaluation harness: question sets, scoring and the report (HOM-98, ADR 0084).

A **question set** is one frozen JSON file of business questions with what the
Copilot should do with each (``copilot-questions/1``, SPEC 2.4).  The approved
100 questions are an owner artifact and never live in this repository; the
fixture set of the tests is a synthetic placeholder, clearly marked.  A frozen
set must be exact: every unknown key at any level is a defect, and so is a
repeated JSON key (Amendment 5 A5.8) or a question text with a lone surrogate
(A5.12: ``answer_question`` refuses such a question at step 0, A4.6).

The module is pure: standard library plus ``backend.copilot_guard``
(``normalize_text``) and ``backend.speech.text_normalize`` (``normalize_digits``,
the single owner of the digit tables).  No database, no network, no clock.  File
input happens only in :func:`load_question_set` and the CLI.  The same input
always gives the same output.

Scoring (SPEC 2.4 with Amendments 2 A2.1, A2.2, A2.6, A2.8): a ``count``
question is correct when the answer was answered, every expected decimal is one
of the fact values and appears in the text as a whole number (Persian and
Arabic-Indic digits read as ASCII, a group separator between digits is
ignored, and a decimal point touches the number only when a digit follows it,
A2.6).  An ``answer`` question is correct when it was answered, every expected
conversation alias (mapped through ``conversation_ids``; a missing alias is a
``ValueError`` before any rule, A2.8) is cited and every ``must_include`` text
survives ``normalize_text`` and ``casefold`` on both sides.  An
``unsupported`` question is correct when the Copilot refused or abstained,
with the expected reason when one is pinned.  ``intent_match`` is reported,
never part of ``correct``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from backend.copilot_guard.signals import normalize_text
from backend.speech.text_normalize import normalize_digits

__all__ = [
    "QUESTION_SET_FORMAT",
    "REPORT_VERSION",
    "CopilotEvaluationReport",
    "ObservedAnswer",
    "Question",
    "QuestionKind",
    "QuestionScore",
    "QuestionSet",
    "load_question_set",
    "main",
    "parse_question_set",
    "run_question_set",
    "score_question",
]

QUESTION_SET_FORMAT = "copilot-questions/1"
REPORT_VERSION = "copilot-eval/1"

# SPEC 2.4: every pattern written ^...$ is a FULL match (Amendment 2 A2.7), so a
# trailing newline is a defect like any other character outside the pattern.
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
DECIMAL_PATTERN = re.compile(r"-?[0-9]+(\.[0-9]+)?")
MAX_QUESTION_LENGTH = 2000

_TOP_KEYS = frozenset({"format", "set_id", "approved", "notice", "questions"})
_QUESTION_REQUIRED = frozenset({"id", "question", "caller", "kind", "expected"})
_QUESTION_OPTIONAL = frozenset({"expected_intent", "slices"})
_ANSWER_KEYS = frozenset({"cited_conversations", "must_include"})

# A group separator (an ASCII or Persian thousands separator) between two digits.
# The Persian and Arabic-Indic digits themselves are read by ``normalize_digits``
# (the single owner of the digit tables, backend/speech/text_normalize.py).
_GROUP_SEPARATOR = re.compile(r"(?<=[0-9])[,٬](?=[0-9])")


class QuestionKind(StrEnum):
    COUNT = "count"
    ANSWER = "answer"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ObservedAnswer:
    """What the harness observed for one question: plain data, no model objects.

    ``status`` is the ``CopilotStatus`` value.  ``reason`` is ``None`` exactly
    when the question was answered.  ``fact_values`` holds every value of the
    fact map the answer prompt was built from (empty for a non-aggregate
    answer), in fact order.
    """

    status: str
    reason: str | None
    intent_id: str | None
    text: str
    cited_conversation_ids: tuple[str, ...]
    fact_values: tuple[Decimal, ...]


@dataclass(frozen=True)
class Question:
    """One frozen question.  ``expected`` and ``slices`` are read-only mappings
    and every list inside ``expected`` is a tuple (A5.10), so a question holds
    no mutable value."""

    id: str
    question: str
    caller: str
    kind: QuestionKind
    expected_intent: str | None
    expected: Mapping[str, object]
    slices: Mapping[str, str]


@dataclass(frozen=True)
class QuestionSet:
    """One parsed question-set file.  ``sha256`` is the hex digest of the file bytes."""

    format: str
    set_id: str
    approved: bool
    notice: str
    questions: tuple[Question, ...]
    sha256: str


def _defect(message: str) -> ValueError:
    return ValueError(message)


def _object_pairs(pairs: list[tuple[str, object]]) -> dict:
    """A5.8: a JSON object with a repeated key is a defect at any level.  A
    frozen set is exact, and ``json.loads`` would silently keep the last
    duplicate.  One pass with a seen set (F4), so a bad file fails fast."""
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise _defect(f"a repeated key in the question set: {key!r}")
        seen.add(key)
    return dict(pairs)


def _exact_keys(value: object, keys: frozenset[str], *, where: str) -> dict:
    if not isinstance(value, dict):
        raise _defect(f"{where} must be a JSON object")
    unknown = sorted(set(value) - keys)
    missing = sorted(keys - set(value))
    if unknown:
        raise _defect(f"unknown key in {where}: {unknown[0]!r}")
    if missing:
        raise _defect(f"missing key in {where}: {missing[0]}")
    return value


def _non_empty_str(value: object, *, where: str, limit: int | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise _defect(f"{where} must be a non-empty string")
    if limit is not None and len(value) > limit:
        raise _defect(f"{where} holds at most {limit} characters")
    return value


def _question(document: object) -> Question:
    if not isinstance(document, dict):
        raise _defect("a question must be a JSON object")
    unknown = sorted(set(document) - _QUESTION_REQUIRED - _QUESTION_OPTIONAL)
    missing = sorted(_QUESTION_REQUIRED - set(document))
    if unknown:
        raise _defect(f"unknown key in a question: {unknown[0]!r}")
    if missing:
        raise _defect(f"missing key in a question: {missing[0]}")
    item = document
    identifier = _non_empty_str(item["id"], where="question id")
    if not ID_PATTERN.fullmatch(identifier):
        raise _defect(f"question id {identifier!r} does not match the id pattern")
    kind_raw = item["kind"]
    if not isinstance(kind_raw, str):
        raise _defect("kind must be a string")
    try:
        kind = QuestionKind(kind_raw)
    except ValueError as exc:
        raise _defect(f"unknown kind: {kind_raw!r}") from exc
    expected = _expected(kind, item["expected"])
    # A5.12: a lone surrogate is not text (the guard's ``frame_untrusted``
    # refuses it, A4.6), so ``validate`` catches it before a run starts.
    question_text = _non_empty_str(item["question"], where="question", limit=MAX_QUESTION_LENGTH)
    if any("\ud800" <= character <= "\udfff" for character in question_text):
        raise _defect("question holds no lone surrogate")
    # ``expected_intent`` is a string or absent: an explicit null is a defect.
    if "expected_intent" in item:
        expected_intent = item["expected_intent"]
        if not isinstance(expected_intent, str):
            raise _defect("expected_intent must be a string")
    else:
        expected_intent = None
    slices_raw = item.get("slices", {})
    if not isinstance(slices_raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in slices_raw.items()
    ):
        raise _defect("slices must map strings to strings")
    return Question(
        id=identifier,
        question=question_text,
        caller=_non_empty_str(item["caller"], where="caller"),
        kind=kind,
        expected_intent=expected_intent,
        expected=MappingProxyType(dict(expected)),
        slices=MappingProxyType(dict(slices_raw)),
    )


def _expected(kind: QuestionKind, value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _defect("expected must be a JSON object")
    if kind is QuestionKind.COUNT:
        item = _exact_keys(value, frozenset({"values"}), where="the expected of a count")
        values = item["values"]
        if not isinstance(values, list) or not values:
            raise _defect("the values of a count are a non-empty list")
        for entry in values:
            if not isinstance(entry, str) or not DECIMAL_PATTERN.fullmatch(entry):
                raise _defect(f"a count value is not a decimal string: {entry!r}")
        return {"values": tuple(values)}
    if kind is QuestionKind.ANSWER:
        unknown = sorted(set(value) - _ANSWER_KEYS)
        if unknown:
            raise _defect(f"unknown key in the expected of an answer: {unknown[0]!r}")
        expected: dict[str, object] = {}
        for key in ("cited_conversations", "must_include"):
            if key not in value:
                continue
            entries = value[key]
            if not isinstance(entries, list) or not all(
                isinstance(entry, str) and entry for entry in entries
            ):
                raise _defect(f"{key} is a list of non-empty strings")
            expected[key] = tuple(entries)
        # At least one of the two lists is present and non-empty: two absent
        # or two empty lists pin nothing to check, so they are defects.
        if not any(expected.get(key) for key in ("cited_conversations", "must_include")):
            raise _defect("an answer names at least one citation or must_include text")
        return expected
    # QuestionKind.UNSUPPORTED
    if not value:
        return {}
    item = _exact_keys(value, frozenset({"reason"}), where="the expected of an unsupported")
    if not isinstance(item["reason"], str):
        raise _defect("the reason of an unsupported question is a string")
    return {"reason": item["reason"]}


def parse_question_set(raw: bytes) -> QuestionSet:
    """Parse one question-set file.  ``ValueError`` on any defect: bad UTF-8,
    bad JSON (or a too deeply nested document, A5.7), a repeated JSON key at
    any level (A5.8), a missing or unknown key at any level, a wrong type or a
    value out of range, a question text with a lone surrogate (A5.12).  A
    frozen set must be exact."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _defect("the question set must be UTF-8") from exc
    try:
        document = json.loads(text, object_pairs_hook=_object_pairs)
    except json.JSONDecodeError as exc:
        raise _defect(f"the question set is not one JSON document: {exc}") from exc
    except RecursionError as exc:
        raise _defect("the question set is not one JSON document: too deeply nested") from exc
    item = _exact_keys(document, _TOP_KEYS, where="the question set")
    if item["format"] != QUESTION_SET_FORMAT:
        raise _defect(f"format must be {QUESTION_SET_FORMAT!r}")
    set_id = _non_empty_str(item["set_id"], where="set_id")
    if not ID_PATTERN.fullmatch(set_id):
        raise _defect("set_id does not match the id pattern")
    if not isinstance(item["approved"], bool):
        raise _defect("approved is a boolean")
    notice = _non_empty_str(item["notice"], where="notice")
    if not isinstance(item["questions"], list):
        raise _defect("questions is a list")
    questions: list[Question] = []
    seen: set[str] = set()
    for entry in item["questions"]:
        question = _question(entry)
        if question.id in seen:
            raise _defect(f"question id {question.id!r} appears twice")
        seen.add(question.id)
        questions.append(question)
    return QuestionSet(
        format=QUESTION_SET_FORMAT,
        set_id=set_id,
        approved=item["approved"],
        notice=notice,
        questions=tuple(questions),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def load_question_set(path: str | os.PathLike) -> QuestionSet:
    """Parse the question-set file at ``path`` (``ValueError`` on any defect)."""
    with open(path, "rb") as handle:
        return parse_question_set(handle.read())


# --- the scoring rules ----------------------------------------------------------------------


def _number_pattern(value: str) -> re.Pattern[str]:
    """The whole-number pattern of one expected decimal string: Persian and
    Arabic-Indic digits are read first, a group separator between digits is
    ignored, the match touches no other digit, and a decimal point touches it
    only when a digit follows it (A2.6)."""
    plain = value.removeprefix("-")
    if "." in plain:
        whole, fraction = plain.split(".", maxsplit=1)
        body = re.escape(whole) + r"[.٫]" + re.escape(fraction)
    else:
        body = re.escape(plain)
    return re.compile(rf"(?<![0-9.٫]){body}(?![0-9])(?![.٫][0-9])")


def _whole_number_in_text(value: str, text: str) -> bool:
    folded = _GROUP_SEPARATOR.sub("", normalize_digits(text))
    return _number_pattern(value).search(folded) is not None


def _aliases(question: Question, conversation_ids: Mapping[str, str]) -> dict[str, str]:
    """A2.8: every expected alias must have a mapping, before any scoring rule."""
    mapped: dict[str, str] = {}
    for alias in question.expected.get("cited_conversations", ()):  # type: ignore[union-attr]
        if alias not in conversation_ids:
            raise ValueError(f"conversation alias {alias!r} is missing from the mapping")
        mapped[alias] = conversation_ids[alias]
    return mapped


def _failure(
    question: Question, observed: ObservedAnswer, aliases: Mapping[str, str]
) -> str | None:
    """The first failing rule of the question (SPEC 2.4)."""
    answered = observed.status == "answered"
    if question.kind is QuestionKind.COUNT:
        if not answered:
            return "not_answered"
        values = [Decimal(entry) for entry in question.expected["values"]]  # type: ignore[index]
        if not all(value in observed.fact_values for value in values):
            return "count_not_in_facts"
        if not all(
            _whole_number_in_text(entry, observed.text)
            for entry in question.expected["values"]  # type: ignore[index]
        ):
            return "count_not_in_text"
        return None
    if question.kind is QuestionKind.ANSWER:
        if not answered:
            return "not_answered"
        if not all(
            conversation in observed.cited_conversation_ids
            for conversation in aliases.values()
        ):
            return "citation_missing"
        must_include = question.expected.get("must_include", ())  # type: ignore[union-attr]
        folded = normalize_text(observed.text).casefold()
        if not all(normalize_text(entry).casefold() in folded for entry in must_include):
            return "text_missing"
        return None
    # QuestionKind.UNSUPPORTED
    if not answered:
        failed = observed.status not in ("refused", "abstained")
    else:
        failed = True
    if failed:
        return "answered"
    reason = question.expected.get("reason")  # type: ignore[union-attr]
    if reason is not None and observed.reason != reason:
        return "wrong_reason"
    return None


def score_question(
    question: Question,
    observed: ObservedAnswer,
    *,
    conversation_ids: Mapping[str, str] = {},
) -> QuestionScore:
    """Score one observed answer against its question.  ``ValueError`` when an
    expected conversation alias has no mapping (A2.8), whatever the status."""
    aliases = _aliases(question, conversation_ids)
    failure = _failure(question, observed, aliases)
    intent_match = (
        None
        if question.expected_intent is None
        else observed.intent_id == question.expected_intent
    )
    return QuestionScore(
        question_id=question.id,
        kind=question.kind,
        correct=failure is None,
        status=observed.status,
        reason=observed.reason,
        failure=failure,
        intent_match=intent_match,
    )


@dataclass(frozen=True)
class QuestionScore:
    """The outcome of one question.  ``failure`` names the first failing rule."""

    question_id: str
    kind: QuestionKind
    correct: bool
    status: str
    reason: str | None
    failure: str | None
    intent_match: bool | None


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


@dataclass(frozen=True)
class CopilotEvaluationReport:
    """The report of one run.  ``to_json`` returns plain JSON types with every
    ratio a float or ``null`` when its denominator is 0."""

    format: str
    set_id: str
    approved: bool
    notice: str
    question_set_sha256: str
    scores: tuple[QuestionScore, ...]

    def to_json(self) -> dict[str, object]:
        total = len(self.scores)
        by_kind = {
            kind: [score for score in self.scores if score.kind is kind]
            for kind in QuestionKind
        }
        count_scores = by_kind[QuestionKind.COUNT]
        answer_scores = by_kind[QuestionKind.ANSWER]
        unsupported_scores = by_kind[QuestionKind.UNSUPPORTED]
        supported = [*count_scores, *answer_scores]
        correct = sum(1 for score in self.scores if score.correct)
        count_correct = sum(1 for score in count_scores if score.correct)
        answer_correct = sum(1 for score in answer_scores if score.correct)
        unsupported_correct = sum(1 for score in unsupported_scores if score.correct)
        abstained = sum(
            1 for score in unsupported_scores if score.status in ("refused", "abstained")
        )
        supported_abstained = sum(
            1 for score in supported if score.status in ("refused", "abstained")
        )
        count_ratio = _ratio(count_correct, len(count_scores))
        answer_ratio = _ratio(answer_correct, len(answer_scores))
        abstention_ratio = _ratio(abstained, len(unsupported_scores))
        false_abstention = _ratio(supported_abstained, len(supported))
        applicable = self.approved and total == 100
        checks = {
            "correct_at_least_95": correct >= 95,
            "exact_count_accuracy": count_ratio == 1.0,  # a null ratio never passes (A1.4)
            "abstention_rate": abstention_ratio == 1.0,
        }
        return {
            "version": REPORT_VERSION,
            "format": self.format,
            "set_id": self.set_id,
            "approved": self.approved,
            "notice": self.notice,
            "question_set_sha256": self.question_set_sha256,
            "total": total,
            "correct": correct,
            "overall_accuracy": _ratio(correct, total),
            "count": {
                "total": len(count_scores),
                "correct": count_correct,
                "exact_count_accuracy": count_ratio,
            },
            "answer": {
                "total": len(answer_scores),
                "correct": answer_correct,
                "answer_accuracy": answer_ratio,
            },
            "unsupported": {
                "total": len(unsupported_scores),
                "abstained": abstained,
                "correct": unsupported_correct,
                "abstention_rate": abstention_ratio,
            },
            "supported_abstained": supported_abstained,
            "false_abstention_rate": false_abstention,
            "f58": {
                "applicable": applicable,
                "passed": all(checks.values()) if applicable else None,
                "checks": checks,
            },
            "questions": [
                {
                    "id": score.question_id,
                    "kind": score.kind.value,
                    "correct": score.correct,
                    "status": score.status,
                    "reason": score.reason,
                    "failure": score.failure,
                    "intent_match": score.intent_match,
                }
                for score in sorted(self.scores, key=lambda score: score.question_id)
            ],
        }


def run_question_set(
    question_set: QuestionSet,
    ask: Callable[[Question], ObservedAnswer],
    *,
    conversation_ids: Mapping[str, str] = {},
) -> CopilotEvaluationReport:
    """Ask every question through ``ask`` and score it.  The report holds one
    score per question, in the order of the set."""
    scores = tuple(
        score_question(question, ask(question), conversation_ids=conversation_ids)
        for question in question_set.questions
    )
    return CopilotEvaluationReport(
        format=question_set.format,
        set_id=question_set.set_id,
        approved=question_set.approved,
        notice=question_set.notice,
        question_set_sha256=question_set.sha256,
        scores=scores,
    )


# --- the CLI --------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.evaluation.copilot",
        description="Validate a Copilot question-set file (HOM-98).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="parse one question-set file")
    validate.add_argument("path", help="the question-set JSON file")
    return parser


def main(argv: list[str] | None = None) -> int:
    """``validate <path>``: print one JSON document (sorted keys) and exit 0;
    bad data exits 1 with one line on stderr; bad arguments exit 2."""
    args = _parser().parse_args(argv)
    try:
        question_set = load_question_set(args.path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    by_kind: dict[str, int] = {}
    for question in question_set.questions:
        by_kind[question.kind.value] = by_kind.get(question.kind.value, 0) + 1
    print(
        json.dumps(
            {
                "format": question_set.format,
                "set_id": question_set.set_id,
                "approved": question_set.approved,
                "questions": len(question_set.questions),
                "by_kind": by_kind,
                "sha256": question_set.sha256,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
