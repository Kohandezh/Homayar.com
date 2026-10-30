"""Plan and score one conversation against a scorecard (HOM-54).

``plan_scorecard`` names the conversation checks to run.  ``score_conversation`` turns
their ``CheckRun`` into criterion outcomes, weights, a score and a scorecard outcome.
``rating_pairs`` and ``score_pair`` feed the HOM-69 agreement metrics.  All of it is
pure: no database, no network, no model call, no clock, and it never changes its inputs.
Every decision uses integer math.  See ``README.md`` for every rule.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import Any

from backend.conversation_checks import CHECKS_VERSION, CheckRun, ContextualCriterion, PhraseCheck
from backend.conversation_checks import definition_digest as check_digest
from backend.evaluation import RatingPair, ScorePair
from backend.scorecards.models import (
    REVIEWER_OUTCOMES,
    SCORECARDS_VERSION,
    ConversationFacts,
    Criterion,
    CriterionResult,
    ScorecardDefinition,
    ScorecardError,
    ScorecardOutcome,
    ScorecardReason,
    ScorecardResult,
    definition_digest,
    validate_definition,
)

PASSED = ScorecardOutcome.PASSED.value
FAILED = ScorecardOutcome.FAILED.value
NOT_APPLICABLE = ScorecardOutcome.NOT_APPLICABLE.value
INSUFFICIENT = ScorecardOutcome.INSUFFICIENT_EVIDENCE.value

# Applicability of a criterion for one conversation.
APPLIES = "applies"
UNKNOWN = "unknown"

BY_CONVERSATION = ScorecardReason.NOT_APPLICABLE_BY_CONVERSATION.value
BY_REVIEWER = ScorecardReason.REVIEWER_NOT_APPLICABLE.value

CHECK_OUTCOMES = ("passed", "failed", "not_applicable", "not_computable")
CHECK_DECIDERS = ("rule", "model")


# --- input ---------------------------------------------------------------------------


def parse_conversation(value: object) -> ConversationFacts:
    if isinstance(value, ConversationFacts):
        return value
    if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        try:
            return ConversationFacts(**value)
        except TypeError as error:  # a missing or an unknown key
            raise ScorecardError("conversation_invalid", str(error)) from error
    raise ScorecardError("conversation_invalid", "a conversation is ConversationFacts or a dict")


def applicability(criterion: Criterion, facts: ConversationFacts) -> str:
    """``applies``, ``not_applicable`` or ``unknown``.  The type is decided first."""
    rule = criterion.applies_to
    if rule is None:
        return APPLIES
    if (
        rule.conversation_types is not None
        and facts.conversation_type not in rule.conversation_types
    ):
        return NOT_APPLICABLE
    if rule.directions is not None:
        if facts.direction is None:
            return UNKNOWN
        if facts.direction not in rule.directions:
            return NOT_APPLICABLE
    return APPLIES


def _mismatch(message: str) -> ScorecardError:
    return ScorecardError("check_run_mismatch", message)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _well_formed(result: object) -> bool:
    """The fields of one ``CheckResult.to_json()`` this package reads, with their types."""
    if not isinstance(result, Mapping):
        return False
    keys = ("check_id", "check_version", "definition_digest", "outcome", "reason",
            "decided_by", "review_required", "review_reasons", "evidence",
            "candidate_evidence")  # fmt: skip
    if any(key not in result for key in keys):
        return False
    outcome, reason = result["outcome"], result["reason"]
    if not isinstance(outcome, str) or outcome not in CHECK_OUTCOMES:
        return False
    decided = outcome in ("passed", "failed")
    reasons = result["review_reasons"]
    return (
        isinstance(result["check_id"], str)
        and _is_int(result["check_version"])
        and isinstance(result["definition_digest"], str)
        and (reason is None if decided else isinstance(reason, str))
        and (result["decided_by"] in CHECK_DECIDERS if decided else result["decided_by"] is None)
        and isinstance(result["review_required"], bool)
        and isinstance(reasons, list | tuple)
        and all(isinstance(code, str) for code in reasons)
        and isinstance(result["evidence"], list | tuple)
        and isinstance(result["candidate_evidence"], list | tuple)
    )


def _check_results(
    definition: ScorecardDefinition, status: dict[str, str], check_run: object
) -> dict[str, dict[str, Any]]:
    """The check result of each applying criterion, copied.  The run must match the plan."""
    data = check_run.to_json() if isinstance(check_run, CheckRun) else check_run
    if not isinstance(data, Mapping):
        raise _mismatch("a check run is a CheckRun or its to_json() dict")
    if data.get("checks_version") != CHECKS_VERSION:
        raise _mismatch(f"checks_version must be {CHECKS_VERSION!r}")
    results = data.get("results")
    if not isinstance(results, list | tuple) or not all(_well_formed(r) for r in results):
        raise _mismatch("results must be a list of CheckResult.to_json() dicts")
    by_id: dict[str, Mapping[str, Any]] = {}
    for result in results:
        if result["check_id"] in by_id:
            raise _mismatch(f"two results for {result['check_id']!r}")
        by_id[result["check_id"]] = result
    expected = {criterion_id for criterion_id, value in status.items() if value == APPLIES}
    if set(by_id) != expected:
        missing, extra = sorted(expected - set(by_id)), sorted(set(by_id) - expected)
        raise _mismatch(f"results do not match the plan: missing {missing}, extra {extra}")
    for criterion in definition.criteria:
        result = by_id.get(criterion.criterion_id)
        if result is None:
            continue
        if result["check_version"] != criterion.check.version:
            raise _mismatch(f"{criterion.criterion_id}: another check_version")
        if result["definition_digest"] != check_digest(criterion.check):
            raise _mismatch(f"{criterion.criterion_id}: another definition_digest")
    return {criterion_id: copy.deepcopy(dict(result)) for criterion_id, result in by_id.items()}


def _reviewer_outcomes(
    definition: ScorecardDefinition, status: dict[str, str], value: object
) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ScorecardError("reviewer_outcome_invalid", "reviewer_outcomes is a mapping or null")
    criteria = {criterion.criterion_id: criterion for criterion in definition.criteria}
    parsed: dict[str, str] = {}
    for criterion_id, outcome in value.items():
        criterion = criteria.get(criterion_id) if isinstance(criterion_id, str) else None
        if criterion is None:
            raise ScorecardError("reviewer_outcome_invalid", f"{criterion_id!r} is not a criterion")
        if not isinstance(outcome, str) or outcome not in REVIEWER_OUTCOMES:
            raise ScorecardError(
                "reviewer_outcome_invalid", f"{criterion_id}: outcome must be {REVIEWER_OUTCOMES}"
            )
        if status[criterion_id] == NOT_APPLICABLE:
            raise ScorecardError(
                "reviewer_outcome_invalid", f"{criterion_id} does not apply to this conversation"
            )
        if outcome == NOT_APPLICABLE and not criterion.na_allowed:
            raise ScorecardError(
                "reviewer_na_not_allowed", f"{criterion_id} may not be not applicable"
            )
        parsed[criterion_id] = REVIEWER_OUTCOMES[REVIEWER_OUTCOMES.index(outcome)]
    return parsed


# --- planning ------------------------------------------------------------------------


def plan_scorecard(
    definition: ScorecardDefinition | Mapping[str, Any], conversation: object
) -> list[PhraseCheck | ContextualCriterion]:
    """The checks of the criteria that apply, in criterion order.  Pass them to
    ``conversation_checks.plan_checks``.  A criterion that does not apply, or whose
    applicability is unknown, has no check run."""
    model = validate_definition(definition)
    facts = parse_conversation(conversation)
    return [c.check for c in model.criteria if applicability(c, facts) == APPLIES]


# --- scoring -------------------------------------------------------------------------


def _criterion_result(
    criterion: Criterion, status: str, result: dict[str, Any] | None, reviewer: str | None
) -> CriterionResult:
    """The criterion outcome table of the README, rows 1 to 9, in order."""
    review_required = False
    review_reasons: tuple[str, ...] = ()
    if status == NOT_APPLICABLE:  # 1
        outcome, reason, decided_by = NOT_APPLICABLE, BY_CONVERSATION, "rule"
    elif reviewer is not None:  # 2
        reason = BY_REVIEWER if reviewer == NOT_APPLICABLE else None
        outcome, decided_by = reviewer, "reviewer"
    elif status == UNKNOWN or result is None:  # 3 (a criterion that applies has a result)
        outcome, reason, decided_by = INSUFFICIENT, ScorecardReason.DIRECTION_UNKNOWN.value, None
    elif result["review_required"]:  # 4
        outcome, reason, decided_by = INSUFFICIENT, ScorecardReason.REVIEW_REQUIRED.value, None
        review_required, review_reasons = True, tuple(result["review_reasons"])
    elif result["outcome"] in (PASSED, FAILED):  # 5 and 6
        outcome, reason, decided_by = result["outcome"], None, result["decided_by"]
    elif result["outcome"] == NOT_APPLICABLE and criterion.na_allowed:  # 7
        outcome, reason, decided_by = NOT_APPLICABLE, result["reason"], "rule"
    elif result["outcome"] == NOT_APPLICABLE:  # 8
        outcome, reason, decided_by = INSUFFICIENT, ScorecardReason.NA_NOT_ALLOWED.value, None
    else:  # 9: not_computable
        outcome, reason, decided_by = INSUFFICIENT, result["reason"], None
    return CriterionResult(
        criterion_id=criterion.criterion_id,
        label=criterion.label,
        weight=criterion.weight,
        auto_fail=criterion.auto_fail,
        na_allowed=criterion.na_allowed,
        outcome=outcome,
        reason=reason,
        decided_by=decided_by,
        review_required=review_required,
        review_reasons=review_reasons,
        check_outcome=result["outcome"] if result else None,
        check_reason=result["reason"] if result else None,
        evidence=tuple(result["evidence"]) if result else (),
        candidate_evidence=tuple(result["candidate_evidence"]) if result else (),
    )


def _percent(part: int, whole: int) -> float:
    """``100 * part / whole`` rounded half up to 2 decimals, from the exact fraction.

    The same as ``decimal.ROUND_HALF_UP`` on the exact value, in integer math: the
    hundredths are ``floor(10000 * part / whole + 1/2)``.
    """
    hundredths = (20000 * part + whole) // (2 * whole)
    return hundredths / 100


def _scorecard_outcome(
    rows: tuple[CriterionResult, ...], threshold: int, applicable: int, earned: int, missing: int
) -> tuple[str, str | None]:
    """The scorecard outcome table of the README, rows 1 to 7, in order."""
    if all(row.outcome == NOT_APPLICABLE for row in rows):  # 1
        return NOT_APPLICABLE, ScorecardReason.NO_APPLICABLE_CRITERIA.value
    if any(row.auto_fail and row.outcome == FAILED for row in rows):  # 2
        return FAILED, ScorecardReason.AUTO_FAIL.value
    if applicable == 0:
        if any(row.outcome == INSUFFICIENT for row in rows):  # 3
            return INSUFFICIENT, ScorecardReason.EVIDENCE_MISSING.value
        return NOT_APPLICABLE, ScorecardReason.NO_APPLICABLE_CRITERIA.value  # 4
    # 5: it fails even if every missing criterion passed.
    if (earned + missing) * 100 < threshold * applicable:
        return FAILED, ScorecardReason.BELOW_THRESHOLD.value
    # 6: it passes even if every missing criterion failed, and no auto-fail is missing.
    auto_fail_missing = any(row.auto_fail and row.outcome == INSUFFICIENT for row in rows)
    if earned * 100 >= threshold * applicable and not auto_fail_missing:
        return PASSED, None
    return INSUFFICIENT, ScorecardReason.EVIDENCE_MISSING.value  # 7


def score_conversation(
    definition: ScorecardDefinition | Mapping[str, Any],
    check_run: CheckRun | Mapping[str, Any],
    conversation: ConversationFacts | Mapping[str, Any],
    reviewer_outcomes: Mapping[str, str] | None = None,
) -> ScorecardResult:
    model = validate_definition(definition)
    facts = parse_conversation(conversation)
    status = {c.criterion_id: applicability(c, facts) for c in model.criteria}
    results = _check_results(model, status, check_run)
    reviewer = _reviewer_outcomes(model, status, reviewer_outcomes)
    rows = tuple(
        _criterion_result(
            c, status[c.criterion_id], results.get(c.criterion_id), reviewer.get(c.criterion_id)
        )
        for c in model.criteria
    )

    def weight(*outcomes: str) -> int:
        return sum(row.weight for row in rows if row.outcome in outcomes)

    applicable = weight(PASSED, FAILED, INSUFFICIENT)
    earned, failed, missing = weight(PASSED), weight(FAILED), weight(INSUFFICIENT)
    complete = all(row.outcome != INSUFFICIENT for row in rows)
    score_min = score_max = score = None
    if applicable > 0:
        score_min = _percent(earned, applicable)
        score_max = _percent(earned + missing, applicable)
        score = score_min if complete else None
    outcome, reason = _scorecard_outcome(rows, model.pass_threshold, applicable, earned, missing)
    return ScorecardResult(
        scorecards_version=SCORECARDS_VERSION,
        checks_version=CHECKS_VERSION,
        definition_digest=definition_digest(model),
        conversation=facts,
        outcome=outcome,
        reason=reason,
        complete=complete,
        pass_threshold=model.pass_threshold,
        score=score,
        score_min=score_min,
        score_max=score_max,
        applicable_weight=applicable,
        earned_weight=earned,
        failed_weight=failed,
        insufficient_weight=missing,
        criteria=rows,
    )


# --- HOM-69 evaluation ---------------------------------------------------------------


def _result_json(value: object) -> Mapping[str, Any]:
    """A ``ScorecardResult`` or its ``to_json()`` dict, as the dict."""
    if isinstance(value, ScorecardResult):
        return value.to_json()
    if isinstance(value, Mapping):
        criteria = value.get("criteria")
        if (
            isinstance(value.get("definition_digest"), str)
            and "score" in value
            and isinstance(criteria, list | tuple)
            and all(
                isinstance(item, Mapping)
                and isinstance(item.get("criterion_id"), str)
                and isinstance(item.get("outcome"), str)
                for item in criteria
            )
        ):
            return value
    raise ScorecardError("result_invalid", "a result is a ScorecardResult or its to_json() dict")


def _rating(outcome: str) -> str | None:
    return None if outcome == INSUFFICIENT else outcome


def rating_pairs(result_a: object, result_b: object) -> list[RatingPair]:
    """One pair per criterion of two results of the same definition.  Insufficient
    evidence is a None rating, so ``cohen_kappa`` excludes it."""
    a, b = _result_json(result_a), _result_json(result_b)
    ids_a = [item["criterion_id"] for item in a["criteria"]]
    ids_b = [item["criterion_id"] for item in b["criteria"]]
    if a["definition_digest"] != b["definition_digest"] or ids_a != ids_b:
        raise ScorecardError("definition_mismatch", "the results use different definitions")
    return [
        RatingPair(_rating(x["outcome"]), _rating(y["outcome"]), criterion=x["criterion_id"])
        for x, y in zip(a["criteria"], b["criteria"], strict=True)
    ]


def score_pair(result: object, reference_score: float) -> ScorePair | None:
    """The result's score against an adjudicated one, or None when the score is null."""
    score = _result_json(result)["score"]
    if not _is_number(reference_score):
        raise ScorecardError("result_invalid", "reference_score must be a finite number")
    if score is None:
        return None
    if not _is_number(score):
        raise ScorecardError("result_invalid", "score must be a finite number or null")
    return ScorePair(score=score, reference=reference_score)
