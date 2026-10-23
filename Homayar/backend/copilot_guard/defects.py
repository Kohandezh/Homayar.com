"""The codes of the guard, and the strict JSON reader of model output (HOM-97).

``GuardDefect`` is the ``detail`` of every refusal and abstention this package makes.  The
reasons stay the HOM-92 ones: a malformed plan refuses with ``invalid_request``, and every
answer defect abstains with ``no_evidence``.
"""

from __future__ import annotations

import json
import math
from enum import StrEnum

from backend.copilot_intents import Route, RoutingDecision


class GuardDefect(StrEnum):
    PLAN_MALFORMED = "plan_malformed"  # the planner output is not {"intent", "params"}
    ANSWER_MALFORMED = "answer_malformed"  # the answer is not the exact sentences shape
    ANSWER_EMPTY = "answer_empty"  # zero sentences
    CITATION_UNKNOWN = "citation_unknown"  # not a given span, or any citation on an aggregate
    CITATION_MISSING = "citation_missing"  # an evidence sentence that cites nothing
    QUOTE_NOT_IN_EVIDENCE = "quote_not_in_evidence"  # a quote its cited spans do not hold
    ANSWER_CONTAINS_INSTRUCTION = "answer_contains_instruction"  # an injection signal
    NUMBER_NOT_FROM_AGGREGATE = "number_not_from_aggregate"  # a number SQL did not give


class MalformedJson(ValueError):
    pass


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise MalformedJson(f"key {key!r} appears twice in one object")
        document[key] = value
    return document


def _no_constant(name: str) -> float:
    raise MalformedJson(f"{name} is not JSON")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise MalformedJson(f"{text} is not a finite number")
    return value


def strict_loads(raw: str) -> object:
    """``json.loads`` that refuses a duplicate key at any level, ``NaN``, ``Infinity``, a
    number too large for a float, and nesting too deep to read.  Raises ``MalformedJson``."""
    try:
        return json.loads(
            raw,
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_no_constant,
            parse_float=_finite_float,
        )
    except MalformedJson:
        raise
    except (ValueError, RecursionError) as exc:
        raise MalformedJson(str(exc)) from exc


def check_decision(decision: object) -> RoutingDecision:
    if not isinstance(decision, RoutingDecision) or not isinstance(decision.route, Route):
        raise ValueError("decision must be a RoutingDecision")
    return decision
