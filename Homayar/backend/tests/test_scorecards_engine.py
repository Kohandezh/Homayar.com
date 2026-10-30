"""HOM-54: the pure scorecard engine (``backend.scorecards``).

Every rule of SPEC sections 4.1 to 4.7 has a deny case and its closest allow case:
definition validation and its codes, applicability and planning, the criterion outcome
table (rows 1 to 9), weights and the half-up score, the scorecard outcome table (rows 1
to 7), the result shape, the digest and the HOM-69 helpers.  No database is used.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import math
import re
import subprocess
import sys
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from backend.conversation_checks import (
    CHECKS_VERSION,
    CheckTurn,
    ContextualCriterion,
    PhraseCheck,
    finish_checks,
    plan_checks,
)
from backend.conversation_checks import definition_digest as check_digest
from backend.database.saas_models import ConversationDirection, ConversationType
from backend.evaluation import RatingPair, ScorePair, kappa_by_criterion, score_mae
from backend.scorecards import (
    CONVERSATION_DIRECTIONS,
    CONVERSATION_TYPES,
    SCORECARDS_VERSION,
    Applicability,
    ConversationFacts,
    Criterion,
    ScorecardDefinition,
    ScorecardError,
    ScorecardResult,
    definition_digest,
    plan_scorecard,
    rating_pairs,
    score_conversation,
    score_pair,
    validate_definition,
)

PACKAGE = Path(__file__).resolve().parents[1] / "scorecards"


# --- builders ----------------------------------------------------------------------------


def phrase(check_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "version": 1,
        "kind": "required_phrase",
        "phrases": ["hello"],
        **extra,
    }


def contextual(check_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "version": 1,
        "kind": "contextual",
        "question": "Did the agent explain the fee?",
        **extra,
    }


def criterion(criterion_id: str, weight: int, **extra: Any) -> dict[str, Any]:
    return {
        "criterion_id": criterion_id,
        "label": criterion_id.replace("_", " ").capitalize(),
        "weight": weight,
        "check": phrase(criterion_id),
        **extra,
    }


def definition(*criteria: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "name": "Inbound sales",
        "pass_threshold": 80,
        "criteria": list(criteria) or [criterion("greeting", 100)],
        **extra,
    }


CALL_IN = {"conversation_type": "call", "direction": "inbound"}


def check_result(
    crit: dict[str, Any],
    outcome: str,
    *,
    reason: str | None = None,
    decided_by: str | None = "rule",
    review_required: bool = False,
    review_reasons: list[str] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One ``CheckResult.to_json()`` for the criterion's check."""
    check = crit["check"]
    if outcome not in ("passed", "failed"):
        decided_by = None
    return {
        "check_id": check["check_id"],
        "check_version": check["version"],
        "definition_digest": check_digest(check),
        "kind": check["kind"],
        "critical": False,
        "outcome": outcome,
        "reason": reason,
        "decided_by": decided_by,
        "review_required": review_required,
        "review_reasons": review_reasons or [],
        "evidence": evidence or [],
        "candidate_evidence": candidates or [],
        "scope_turn_indexes": [0],
        "evaluation": None,
    }


def run_of(*results: dict[str, Any]) -> dict[str, Any]:
    return {
        "checks_version": CHECKS_VERSION,
        "language": "en",
        "evaluator": None,
        "results": list(results),
    }


SPAN = {
    "turn_index": 0,
    "speaker_role": "agent",
    "start_ms": 0,
    "end_ms": 900,
    "phrase": "hello",
    "token_start": 0,
    "token_end": 1,
}


def score(defn, outcomes: dict[str, str], conversation=CALL_IN, reviewer=None, **kw):
    """Score ``defn`` with one result per named criterion (``outcome`` or ``(outcome, reason)``)."""
    by_id = {item["criterion_id"]: item for item in defn["criteria"]}
    results = []
    for criterion_id, outcome in outcomes.items():
        reason = None
        if isinstance(outcome, tuple):
            outcome, reason = outcome
        elif outcome == "not_applicable":
            reason = "not_triggered"
        elif outcome == "not_computable":
            reason = "no_transcript"
        results.append(check_result(by_id[criterion_id], outcome, reason=reason, **kw))
    return score_conversation(defn, run_of(*results), conversation, reviewer)


def code_of(call) -> str:
    with pytest.raises(ScorecardError) as error:
        call()
    assert isinstance(error.value, ValueError)
    return error.value.code


# --- version, constants, purity ----------------------------------------------------------


def test_version_is_pinned():
    assert SCORECARDS_VERSION == "scorecards/1"


def test_the_conversation_values_are_a_copy_of_the_hom58_enums():
    assert CONVERSATION_TYPES == tuple(member.value for member in ConversationType)
    assert CONVERSATION_DIRECTIONS == tuple(member.value for member in ConversationDirection)


def test_the_package_needs_no_database_provider_network_clock_or_randomness():
    banned = re.compile(
        r"^\s*(from|import)\s+(backend\.(database|providers|services|routers|jobs|security)"
        r"|sqlalchemy|fastapi|httpx|requests|socket|random|secrets|time|datetime|uuid)\b"
        r"|\bopen\(|Path\("
    )
    sources = sorted(PACKAGE.glob("*.py"))
    assert sources
    for path in sources:
        for number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            assert not banned.search(line), f"{path.name}:{number}: {line}"
    loaded = subprocess.run(  # noqa: S603 - fixed argv, the test's own interpreter
        [
            sys.executable,
            "-c",
            "import sys, backend.scorecards; print(sorted(m for m in sys.modules if "
            "m.startswith(('backend.database', 'backend.providers', 'sqlalchemy', 'fastapi'))))",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=PACKAGE.parents[1],
    )
    assert loaded.stdout.strip() == "[]"


# --- 4.1 and 4.2: the definition ---------------------------------------------------------


def test_a_minimal_definition_is_valid_as_a_dict_and_as_a_model():
    validated = validate_definition(definition())
    assert isinstance(validated, ScorecardDefinition)
    assert validated.description is None
    only = validated.criteria[0]
    assert (only.auto_fail, only.na_allowed, only.applies_to) == (False, False, None)
    assert isinstance(only.check, PhraseCheck)
    assert validate_definition(validated) == validated
    assert validate_definition(ScorecardDefinition(**definition())) == validated
    with pytest.raises(ValueError):
        validated.name = "changed"  # frozen


@pytest.mark.parametrize(
    "value",
    [None, "definition", 5, [definition()], {1: "x"}],
    ids=["none", "str", "int", "list", "non-str key"],
)
def test_a_definition_that_is_not_a_model_or_dict_is_scorecard_invalid(value):
    assert code_of(lambda: validate_definition(value)) == "scorecard_invalid"
    assert code_of(lambda: definition_digest(value)) == "scorecard_invalid"


SCORECARD_INVALID = {
    "unknown key": {"owner": "x"},
    "empty name": {"name": ""},
    "blank name": {"name": "   "},
    "long name": {"name": "a" * 201},
    "name not str": {"name": 5},
    "long description": {"description": "a" * 2001},
    "description not str": {"description": 5},
    "threshold 0": {"pass_threshold": 0},
    "threshold 101": {"pass_threshold": 101},
    "threshold bool": {"pass_threshold": True},
    "threshold str": {"pass_threshold": "80"},
    "threshold float": {"pass_threshold": 80.0},
    "no criteria": {"criteria": []},
    "criteria not a list": {"criteria": "greeting"},
    "criteria a set": {"criteria": {"greeting"}},
    "too many criteria": {
        "criteria": [criterion(f"c{index:03d}", 1) for index in range(101)],
    },
}


@pytest.mark.parametrize("change", SCORECARD_INVALID.values(), ids=SCORECARD_INVALID.keys())
def test_a_broken_top_level_field_is_scorecard_invalid(change):
    broken = {**definition(), **change}
    assert code_of(lambda: validate_definition(broken)) == "scorecard_invalid"
    assert code_of(lambda: ScorecardDefinition(**broken)) == "scorecard_invalid"


def test_missing_top_level_fields_are_scorecard_invalid():
    for field in ("name", "pass_threshold", "criteria"):
        broken = definition()
        del broken[field]
        assert code_of(lambda broken=broken: validate_definition(broken)) == "scorecard_invalid"


def test_top_level_allow_cases():
    hundred = [criterion(f"c{index:03d}", 1) for index in range(100)]
    for change in (
        {"name": " a "},
        {"name": "a" * 200},
        {"name": "  " + "a" * 200 + "  "},  # 200 after strip
        {"description": None},
        {"description": ""},
        {"description": "a" * 2000},
        {"pass_threshold": 1},
        {"pass_threshold": 100},
        {"criteria": hundred},
        {"criteria": tuple(definition()["criteria"])},
    ):
        validated = validate_definition({**definition(), **change})
        for key, value in change.items():
            if key == "criteria":
                assert len(validated.criteria) == len(value)
            else:
                assert getattr(validated, key) == value  # stored as given


CRITERION_INVALID = {
    "id upper case": {"criterion_id": "Greeting"},
    "id one letter": {"criterion_id": "g"},
    "id starts with digit": {"criterion_id": "1greeting"},
    "id too long": {"criterion_id": "g" * 65},
    "id not str": {"criterion_id": 5},
    "id trailing newline": {"criterion_id": "greeting\n"},  # re.match with "$" lets it pass
    "empty label": {"label": ""},
    "blank label": {"label": "  "},
    "long label": {"label": "a" * 201},
    "weight -1": {"weight": -1},
    "weight 101": {"weight": 101},
    "weight bool": {"weight": True},
    "weight float": {"weight": 50.0},
    "weight str": {"weight": "50"},
    "auto_fail not bool": {"auto_fail": 1},
    "na_allowed not bool": {"na_allowed": "yes"},
    "unknown key": {"section": "opening"},
    "applies_to unknown key": {"applies_to": {"conversation_types": ["call"], "teams": ["a"]}},
    "applies_to both null": {"applies_to": {}},
    "applies_to both explicit null": {
        "applies_to": {"conversation_types": None, "directions": None}
    },
    "types empty": {"applies_to": {"conversation_types": []}},
    "types unknown": {"applies_to": {"conversation_types": ["sms"]}},
    "types duplicate": {"applies_to": {"conversation_types": ["call", "call"]}},
    "types a str": {"applies_to": {"conversation_types": "call"}},
    "types upper case": {"applies_to": {"conversation_types": ["Call"]}},
    "directions empty": {"applies_to": {"directions": []}},
    "directions unknown": {"applies_to": {"directions": ["unknown"]}},
    "directions duplicate": {"applies_to": {"directions": ["inbound", "inbound"]}},
    "applies_to not a dict": {"applies_to": "call"},
}


@pytest.mark.parametrize("change", CRITERION_INVALID.values(), ids=CRITERION_INVALID.keys())
def test_a_broken_criterion_field_is_criterion_invalid(change):
    crit = {**criterion("greeting", 100), **change}
    broken = definition(crit)
    assert code_of(lambda: validate_definition(broken)) == "criterion_invalid"
    assert code_of(lambda: Criterion(**crit)) == "criterion_invalid"


def test_a_criterion_that_is_not_a_dict_is_criterion_invalid():
    assert code_of(lambda: validate_definition(definition("greeting"))) == "criterion_invalid"


def test_applicability_allow_cases_and_enum_members():
    for applies_to in (
        {"conversation_types": ["call"]},
        {"directions": ["inbound", "outbound"]},
        {"conversation_types": list(CONVERSATION_TYPES), "directions": ["internal"]},
        {"conversation_types": [ConversationType.MEETING], "directions": None},
    ):
        validated = validate_definition(
            definition(criterion("greeting", 100, applies_to=applies_to))
        )
        stored = validated.criteria[0].applies_to
        assert isinstance(stored, Applicability)
        dumped = stored.model_dump(mode="json")
        for key in ("conversation_types", "directions"):
            given = applies_to.get(key)
            plain = None if given is None else [getattr(value, "value", value) for value in given]
            assert dumped[key] == plain
    assert code_of(lambda: Applicability()) == "criterion_invalid"
    assert code_of(lambda: Applicability(directions=["sideways"])) == "criterion_invalid"


def test_a_criterion_id_given_twice_is_duplicate_criterion():
    broken = definition(criterion("greeting", 50), criterion("greeting", 50))
    assert code_of(lambda: validate_definition(broken)) == "duplicate_criterion"


@pytest.mark.parametrize("weights", [(50, 49), (50, 51), (100, 1)])
def test_weights_that_do_not_add_up_to_100_are_weights_invalid(weights):
    broken = definition(criterion("greeting", weights[0]), criterion("closing", weights[1]))
    assert code_of(lambda: validate_definition(broken)) == "weights_invalid"
    validate_definition(definition(criterion("greeting", 50), criterion("closing", 50)))


def test_weight_zero_needs_auto_fail():
    broken = definition(criterion("greeting", 100), criterion("disclosure", 0))
    assert code_of(lambda: validate_definition(broken)) == "weight_zero_not_auto_fail"
    assert code_of(lambda: Criterion(**criterion("disclosure", 0))) == "weight_zero_not_auto_fail"
    allowed = definition(criterion("greeting", 100), criterion("disclosure", 0, auto_fail=True))
    assert validate_definition(allowed).criteria[1].auto_fail is True


CHECK_INVALID = {
    "unknown kind": {"check_id": "greeting", "version": 1, "kind": "sms"},
    "no kind": {"check_id": "greeting", "version": 1, "phrases": ["hello"]},
    "no phrases": phrase("greeting", phrases=[]),
    "bad version": phrase("greeting", version=0),
    "unknown check key": phrase("greeting", weight=3),
    "not a dict": "greeting",
    "a number": 5,
    "empty question": contextual("greeting", question=" "),
}


@pytest.mark.parametrize("check", CHECK_INVALID.values(), ids=CHECK_INVALID.keys())
def test_a_check_that_is_not_a_valid_conversation_check_is_check_invalid(check):
    broken = definition(criterion("greeting", 100, check=check))
    assert code_of(lambda: validate_definition(broken)) == "check_invalid"
    assert code_of(lambda: Criterion(**criterion("greeting", 100, check=check))) == "check_invalid"


def test_a_check_given_as_a_model_and_as_a_dict_are_the_same():
    as_dict = definition(criterion("greeting", 100))
    as_model = definition(criterion("greeting", 100, check=PhraseCheck(**phrase("greeting"))))
    assert validate_definition(as_dict) == validate_definition(as_model)
    assert definition_digest(as_dict) == definition_digest(as_model)
    contextual_model = ContextualCriterion(**contextual("fee_explained"))
    validated = validate_definition(
        definition(criterion("fee_explained", 100, check=contextual_model))
    )
    assert validated.criteria[0].check == contextual_model


def test_a_check_built_with_model_construct_is_validated_again():
    unchecked = PhraseCheck.model_construct(
        check_id="greeting", version=0, kind="required_phrase", phrases=("hello",)
    )
    broken = definition(criterion("greeting", 100, check=unchecked))
    assert code_of(lambda: validate_definition(broken)) == "check_invalid"


def test_a_check_with_another_id_is_check_id_mismatch():
    broken = definition(criterion("greeting", 100, check=phrase("hello_said")))
    assert code_of(lambda: validate_definition(broken)) == "check_id_mismatch"
    assert code_of(lambda: Criterion(**broken["criteria"][0])) == "check_id_mismatch"


def test_a_trigger_check_needs_na_allowed():
    trigger = contextual("fee_explained", trigger_phrases=["fee"])
    broken = definition(criterion("fee_explained", 100, check=trigger))
    assert code_of(lambda: validate_definition(broken)) == "na_not_allowed"
    assert code_of(lambda: Criterion(**broken["criteria"][0])) == "na_not_allowed"
    validate_definition(definition(criterion("fee_explained", 100, check=trigger, na_allowed=True)))
    no_trigger = contextual("fee_explained")
    validate_definition(definition(criterion("fee_explained", 100, check=no_trigger)))


def test_each_rule_is_checked_again_on_a_changed_copy():
    valid = validate_definition(definition(criterion("greeting", 50), criterion("closing", 50)))
    copied = valid.model_copy(update={"pass_threshold": 0})
    assert code_of(lambda: validate_definition(copied)) == "scorecard_invalid"
    heavier = valid.criteria[0].model_copy(update={"weight": 60})
    assert (
        code_of(
            lambda: validate_definition(
                valid.model_copy(update={"criteria": (heavier, valid.criteria[1])})
            )
        )
        == "weights_invalid"
    )
    constructed = ScorecardDefinition.model_construct(
        name="QA", description=None, pass_threshold=80, criteria=(valid.criteria[0],)
    )
    assert code_of(lambda: validate_definition(constructed)) == "weights_invalid"
    assert code_of(lambda: definition_digest(constructed)) == "weights_invalid"


# --- 4.3: applicability and planning -----------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        {"conversation_type": "sms", "direction": None},
        {"conversation_type": "Call", "direction": None},
        {"conversation_type": None, "direction": None},
        {"conversation_type": "call", "direction": "unknown"},
        {"conversation_type": "call", "direction": ""},
        {"conversation_type": "call"},
        {"conversation_type": "call", "direction": None, "team": "a"},
        "call",
        None,
    ],
)
def test_bad_conversation_facts_are_conversation_invalid(value):
    defn = definition()
    assert code_of(lambda: plan_scorecard(defn, value)) == "conversation_invalid"
    if isinstance(value, dict) and set(value) == {"conversation_type", "direction"}:
        assert code_of(lambda: ConversationFacts(**value)) == "conversation_invalid"


def test_good_conversation_facts_as_a_dataclass_a_dict_and_enum_members():
    for conversation_type in CONVERSATION_TYPES:
        for direction in (*CONVERSATION_DIRECTIONS, None):
            facts = ConversationFacts(conversation_type, direction)
            assert plan_scorecard(definition(), facts)
            assert plan_scorecard(
                definition(), {"conversation_type": conversation_type, "direction": direction}
            )
    facts = ConversationFacts(ConversationType.VOICEMAIL, ConversationDirection.INBOUND)
    assert (facts.conversation_type, facts.direction) == ("voicemail", "inbound")
    assert type(facts.conversation_type) is str and type(facts.direction) is str


APPLICABILITY = definition(
    criterion("everywhere", 20),
    criterion("calls_only", 20, applies_to={"conversation_types": ["call"]}),
    criterion("inbound_only", 20, applies_to={"directions": ["inbound"]}),
    criterion(
        "inbound_calls", 20, applies_to={"conversation_types": ["call"], "directions": ["inbound"]}
    ),
    criterion(
        "meetings_out",
        20,
        applies_to={"conversation_types": ["meeting"], "directions": ["outbound"]},
    ),
)


@pytest.mark.parametrize(
    ("conversation", "planned"),
    [
        (("call", "inbound"), ["everywhere", "calls_only", "inbound_only", "inbound_calls"]),
        (("call", "outbound"), ["everywhere", "calls_only"]),
        (("call", None), ["everywhere", "calls_only"]),  # direction unknown: no check run
        (("meeting", "outbound"), ["everywhere", "meetings_out"]),
        (("meeting", None), ["everywhere"]),
        (("voicemail", "inbound"), ["everywhere", "inbound_only"]),
    ],
)
def test_plan_scorecard_returns_the_checks_of_the_criteria_that_apply(conversation, planned):
    checks = plan_scorecard(APPLICABILITY, ConversationFacts(*conversation))
    assert isinstance(checks, list)
    assert [check.check_id for check in checks] == planned
    assert all(isinstance(check, PhraseCheck) for check in checks)


def test_the_conversation_type_is_decided_before_the_direction():
    # The type does not match and the direction is unknown: not applicable, not unknown.
    result = score(
        APPLICABILITY,
        {"everywhere": "passed"},
        {"conversation_type": "voicemail", "direction": None},
    )
    rows = {item.criterion_id: item for item in result.criteria}
    assert (rows["inbound_calls"].outcome, rows["inbound_calls"].reason) == (
        "not_applicable",
        "not_applicable_by_conversation",
    )
    assert (rows["inbound_only"].outcome, rows["inbound_only"].reason) == (
        "insufficient_evidence",
        "direction_unknown",
    )


def test_the_plan_runs_through_conversation_checks_end_to_end():
    defn = definition(
        criterion("greeting", 40, check=phrase("greeting", phrases=["hello"])),
        criterion(
            "no_promise",
            40,
            check=phrase("no_promise", kind="forbidden_phrase", phrases=["guaranteed returns"]),
        ),
        criterion(
            "fee_explained",
            20,
            na_allowed=True,
            check=contextual("fee_explained", trigger_phrases=["cancel"]),
        ),
        pass_threshold=80,
    )
    turns = [
        CheckTurn("hello, how can I help", "agent", 0, 1000, 0.9),
        CheckTurn("I want to know your prices", "customer", 1000, 2000, 0.9),
    ]
    checks = plan_scorecard(defn, CALL_IN)
    run = finish_checks(plan_checks(checks, turns, language="en"))
    result = score_conversation(defn, run, CALL_IN)
    assert [(c.criterion_id, c.outcome, c.reason) for c in result.criteria] == [
        ("greeting", "passed", None),
        ("no_promise", "passed", None),
        ("fee_explained", "not_applicable", "not_triggered"),
    ]
    assert (result.outcome, result.score, result.applicable_weight) == ("passed", 100.0, 80)
    assert score_conversation(defn, run.to_json(), CALL_IN) == result


# --- 4.4: check_run_mismatch -------------------------------------------------------------

TWO = definition(criterion("greeting", 50), criterion("closing", 50))
GREETING, CLOSING = TWO["criteria"]


def _mismatch(run) -> str:
    return code_of(lambda: score_conversation(TWO, run, CALL_IN))


def test_a_check_run_that_does_not_match_the_plan_is_check_run_mismatch():
    good = [check_result(GREETING, "passed"), check_result(CLOSING, "passed")]
    assert score_conversation(TWO, run_of(*good), CALL_IN).outcome == "passed"  # allow

    assert _mismatch({**run_of(*good), "checks_version": "conversation-checks/2"}) == (
        "check_run_mismatch"
    )
    assert _mismatch(run_of(good[0])) == "check_run_mismatch"  # a result missing
    extra = check_result(criterion("extra_one", 0, auto_fail=True), "passed")
    assert _mismatch(run_of(*good, extra)) == "check_run_mismatch"  # an extra result
    assert _mismatch(run_of(*good, good[0])) == "check_run_mismatch"  # a duplicate
    assert _mismatch(run_of({**good[0], "check_version": 2}, good[1])) == "check_run_mismatch"
    assert _mismatch(run_of({**good[0], "definition_digest": "0" * 64}, good[1])) == (
        "check_run_mismatch"
    )
    for broken in (
        "run",
        None,
        {"checks_version": CHECKS_VERSION},
        {"checks_version": CHECKS_VERSION, "results": "x"},
        run_of({**good[0], "outcome": "maybe"}, good[1]),
        run_of({**good[0], "review_required": "no"}, good[1]),
        run_of({**good[0], "decided_by": None}, good[1]),  # passed without decided_by
        run_of({k: v for k, v in good[0].items() if k != "evidence"}, good[1]),
    ):
        assert _mismatch(broken) == "check_run_mismatch", broken


def test_a_result_for_a_criterion_that_does_not_apply_is_check_run_mismatch():
    defn = definition(
        criterion("greeting", 50),
        criterion("inbound_only", 50, na_allowed=True, applies_to={"directions": ["inbound"]}),
    )
    greeting, inbound = defn["criteria"]
    outbound = {"conversation_type": "call", "direction": "outbound"}
    both = run_of(check_result(greeting, "passed"), check_result(inbound, "passed"))
    assert code_of(lambda: score_conversation(defn, both, outbound)) == "check_run_mismatch"
    unknown = {"conversation_type": "call", "direction": None}
    assert code_of(lambda: score_conversation(defn, both, unknown)) == "check_run_mismatch"
    only = run_of(check_result(greeting, "passed"))
    assert score_conversation(defn, only, outbound).criteria[1].outcome == "not_applicable"


# --- 4.4: reviewer outcomes --------------------------------------------------------------

REVIEWED = definition(
    criterion("greeting", 40),
    criterion("fee_explained", 30, na_allowed=True),
    criterion("inbound_only", 15, applies_to={"directions": ["inbound"]}),
    criterion("meetings_only", 15, na_allowed=True, applies_to={"conversation_types": ["meeting"]}),
)


def _reviewed(reviewer, conversation=CALL_IN):
    by_id = {item["criterion_id"]: item for item in REVIEWED["criteria"]}
    facts = ConversationFacts(conversation["conversation_type"], conversation["direction"])
    planned = [check.check_id for check in plan_scorecard(REVIEWED, facts)]
    run = run_of(*(check_result(by_id[cid], "passed") for cid in planned))
    return score_conversation(REVIEWED, run, conversation, reviewer)


def test_bad_reviewer_outcomes_are_refused():
    for reviewer in (
        {"unknown_one": "passed"},
        {"greeting": "pass"},
        {"greeting": "insufficient_evidence"},
        {"greeting": None},
        {"meetings_only": "passed"},  # not applicable by conversation
        ["greeting"],
        "greeting",
    ):
        assert code_of(lambda r=reviewer: _reviewed(r)) == "reviewer_outcome_invalid", reviewer
    assert code_of(lambda: _reviewed({"greeting": "not_applicable"})) == "reviewer_na_not_allowed"


def test_reviewer_outcomes_for_applying_and_unknown_criteria_work():
    result = _reviewed({"greeting": "failed", "fee_explained": "not_applicable"})
    rows = {item.criterion_id: item for item in result.criteria}
    assert (rows["greeting"].outcome, rows["greeting"].reason, rows["greeting"].decided_by) == (
        "failed",
        None,
        "reviewer",
    )
    assert (rows["fee_explained"].outcome, rows["fee_explained"].reason) == (
        "not_applicable",
        "reviewer_not_applicable",
    )
    unknown = {"conversation_type": "call", "direction": None}
    result = _reviewed({"inbound_only": "passed"}, unknown)
    row = next(item for item in result.criteria if item.criterion_id == "inbound_only")
    assert (row.outcome, row.reason, row.decided_by) == ("passed", None, "reviewer")
    # No check ran for it: the check fields are empty.
    assert (row.check_outcome, row.check_reason, row.evidence, row.candidate_evidence) == (
        None,
        None,
        (),
        (),
    )
    assert _reviewed({}).outcome == _reviewed(None).outcome


# --- 4.4: the criterion outcome table ----------------------------------------------------

TABLE = definition(
    criterion("rule_one", 20, applies_to={"conversation_types": ["meeting"]}),
    criterion("rule_two", 20),
    criterion("rule_three", 20, applies_to={"directions": ["outbound"]}),
    criterion("other", 20, na_allowed=True),
    criterion("strict", 20),
)
T_RULE_TWO, T_OTHER, T_STRICT = TABLE["criteria"][1], TABLE["criteria"][3], TABLE["criteria"][4]
UNKNOWN_CALL = {"conversation_type": "call", "direction": None}


def _row(outcome_of_two, *, reviewer=None, other=("passed", None), strict=("passed", None), **kw):
    results = [
        check_result(T_RULE_TWO, outcome_of_two[0], reason=outcome_of_two[1], **kw),
        check_result(T_OTHER, other[0], reason=other[1]),
        check_result(T_STRICT, strict[0], reason=strict[1]),
    ]
    result = score_conversation(TABLE, run_of(*results), UNKNOWN_CALL, reviewer)
    return {item.criterion_id: item for item in result.criteria}


def _fields(item):
    return (item.outcome, item.reason, item.decided_by)


def test_criterion_row_1_not_applicable_by_conversation():
    item = _row(("passed", None))["rule_one"]
    assert _fields(item) == ("not_applicable", "not_applicable_by_conversation", "rule")
    assert (item.check_outcome, item.check_reason, item.evidence, item.candidate_evidence) == (
        None,
        None,
        (),
        (),
    )
    assert item.review_required is False and item.review_reasons == ()


def test_criterion_row_2_a_reviewer_outcome_wins_and_keeps_the_check_fields():
    rows = _row(
        ("failed", None),
        reviewer={"rule_two": "passed", "other": "not_applicable"},
        review_required=True,
        review_reasons=["negation"],
        evidence=[SPAN],
    )
    assert _fields(rows["rule_two"]) == ("passed", None, "reviewer")
    assert rows["rule_two"].check_outcome == "failed"
    assert rows["rule_two"].evidence == (SPAN,)
    assert rows["rule_two"].review_required is False and rows["rule_two"].review_reasons == ()
    assert _fields(rows["other"]) == ("not_applicable", "reviewer_not_applicable", "reviewer")
    failed = _row(("passed", None), reviewer={"rule_two": "failed"})["rule_two"]
    assert _fields(failed) == ("failed", None, "reviewer")


def test_criterion_row_3_unknown_direction_is_insufficient_evidence():
    item = _row(("passed", None))["rule_three"]
    assert _fields(item) == ("insufficient_evidence", "direction_unknown", None)
    assert item.check_outcome is None and item.evidence == ()


@pytest.mark.parametrize("check_outcome", ["passed", "failed", "not_computable", "not_applicable"])
def test_criterion_row_4_review_required_is_insufficient_evidence(check_outcome):
    reason = {"not_computable": "negation", "not_applicable": "not_triggered"}.get(check_outcome)
    item = _row(
        (check_outcome, reason),
        review_required=True,
        review_reasons=["negation", "critical_check"],
        candidates=[SPAN],
    )["rule_two"]
    assert _fields(item) == ("insufficient_evidence", "review_required", None)
    assert item.review_required is True
    assert item.review_reasons == ("negation", "critical_check")
    assert (item.check_outcome, item.check_reason) == (check_outcome, reason)
    assert item.candidate_evidence == (SPAN,)


@pytest.mark.parametrize("decided_by", ["rule", "model"])
def test_criterion_rows_5_and_6_passed_and_failed_keep_who_decided(decided_by):
    passed = _row(("passed", None), decided_by=decided_by, evidence=[SPAN])["rule_two"]
    assert _fields(passed) == ("passed", None, decided_by)
    assert passed.evidence == (SPAN,) and passed.check_outcome == "passed"
    failed = _row(("failed", None), decided_by=decided_by)["rule_two"]
    assert _fields(failed) == ("failed", None, decided_by)


def test_criterion_row_7_check_not_applicable_with_na_allowed():
    item = _row(("passed", None), other=("not_applicable", "not_triggered"))["other"]
    assert _fields(item) == ("not_applicable", "not_triggered", "rule")
    assert item.check_outcome == "not_applicable" and item.check_reason == "not_triggered"


def test_criterion_row_8_check_not_applicable_without_na_allowed():
    item = _row(("passed", None), strict=("not_applicable", "not_triggered"))["strict"]
    assert _fields(item) == ("insufficient_evidence", "na_not_allowed", None)
    assert item.check_outcome == "not_applicable"


@pytest.mark.parametrize(
    "reason", ["no_transcript", "low_asr_confidence", "evaluator_unavailable", "window_too_large"]
)
def test_criterion_row_9_not_computable_is_insufficient_evidence(reason):
    item = _row(("not_computable", reason))["rule_two"]
    assert _fields(item) == ("insufficient_evidence", reason, None)
    assert item.review_required is False


# --- 4.4: weights and score --------------------------------------------------------------


def _three(passed: int, failed: int) -> dict[str, Any]:
    """``passed`` weight passed, ``failed`` weight failed, the rest not applicable."""
    return definition(
        criterion("good", passed),
        criterion("bad", failed),
        criterion("absent", 100 - passed - failed, na_allowed=True),
        pass_threshold=50,
    )


@pytest.mark.parametrize(
    ("passed", "failed", "expected"),
    [
        (2, 1, 66.67),
        (1, 7, 12.5),
        (1, 31, 3.13),  # exactly 3.125: half up, not half even
        (5, 27, 15.63),  # exactly 15.625
        (3, 29, 9.38),  # exactly 9.375
        (1, 63, 1.56),  # 1.5625
        (1, 2, 33.33),
        (1, 1, 50.0),
        (98, 1, 98.99),
        (1, 98, 1.01),
    ],
)
def test_the_score_is_rounded_half_up_from_the_exact_fraction(passed, failed, expected):
    defn = _three(passed, failed)
    result = score(defn, {"good": "passed", "bad": "failed", "absent": "not_applicable"})
    assert (result.applicable_weight, result.earned_weight, result.failed_weight) == (
        passed + failed,
        passed,
        failed,
    )
    assert result.score == result.score_min == result.score_max == expected
    assert result.to_json()["score"] == expected
    exact = Fraction(100 * passed, passed + failed)
    assert Fraction(str(expected)) == Fraction(math.floor(exact * 100 + Fraction(1, 2)), 100)


def test_the_rounding_equals_decimal_round_half_up_for_every_fraction():
    from backend.scorecards.engine import _percent

    with localcontext() as context:
        context.prec = 300  # far more digits than any period of 1/whole for whole <= 100
        for whole in range(1, 101):
            for part in range(whole + 1):
                exact = Decimal(100 * part) / Decimal(whole)
                expected = exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                assert _percent(part, whole) == float(expected), (part, whole)


def test_the_score_needs_every_criterion_and_bounds_the_missing_ones():
    defn = definition(
        criterion("greeting", 50),
        criterion("closing", 30),
        criterion("fee_explained", 20),
        criterion("disclosure", 0, auto_fail=True),
        pass_threshold=50,
    )
    result = score(
        defn,
        {
            "greeting": "passed",
            "closing": "failed",
            "fee_explained": "not_computable",
            "disclosure": "passed",
        },
    )
    assert (result.applicable_weight, result.earned_weight) == (100, 50)
    assert (result.failed_weight, result.insufficient_weight) == (30, 20)
    assert result.complete is False
    assert (result.score, result.score_min, result.score_max) == (None, 50.0, 70.0)
    # A weight-0 criterion without evidence also makes the result incomplete.
    zero = score(
        defn,
        {
            "greeting": "passed",
            "closing": "passed",
            "fee_explained": "passed",
            "disclosure": "not_computable",
        },
    )
    assert zero.complete is False and zero.insufficient_weight == 0
    assert (zero.score, zero.score_min, zero.score_max) == (None, 100.0, 100.0)


def test_no_applicable_weight_gives_no_score():
    defn = definition(
        criterion("greeting", 100, na_allowed=True), criterion("disclosure", 0, auto_fail=True)
    )
    result = score(defn, {"greeting": "not_applicable", "disclosure": "passed"})
    assert result.applicable_weight == 0
    assert (result.score, result.score_min, result.score_max) == (None, None, None)


# --- 4.4: the scorecard outcome table ----------------------------------------------------


def test_scorecard_row_1_every_criterion_not_applicable():
    defn = definition(
        criterion("greeting", 60, na_allowed=True), criterion("closing", 40, na_allowed=True)
    )
    result = score(defn, {"greeting": "not_applicable", "closing": "not_applicable"})
    assert (result.outcome, result.reason) == ("not_applicable", "no_applicable_criteria")


def test_scorecard_row_2_a_failed_auto_fail_criterion_fails_the_whole_scorecard():
    defn = definition(
        criterion("greeting", 90),
        criterion("disclosure", 10, auto_fail=True),
        criterion("promise", 0, auto_fail=True),
        pass_threshold=50,
    )
    failed = score(defn, {"greeting": "passed", "disclosure": "failed", "promise": "passed"})
    assert (failed.outcome, failed.reason, failed.score) == ("failed", "auto_fail", 90.0)
    zero = score(defn, {"greeting": "not_computable", "disclosure": "passed", "promise": "failed"})
    assert (zero.outcome, zero.reason) == ("failed", "auto_fail")
    # Allow-control: the same criteria passed.
    passed = score(defn, {"greeting": "passed", "disclosure": "passed", "promise": "passed"})
    assert (passed.outcome, passed.reason) == ("passed", None)


def test_scorecard_rows_3_and_4_no_applicable_weight():
    defn = definition(
        criterion("greeting", 100, na_allowed=True), criterion("disclosure", 0, auto_fail=True)
    )
    missing = score(defn, {"greeting": "not_applicable", "disclosure": "not_computable"})
    assert (missing.outcome, missing.reason) == ("insufficient_evidence", "evidence_missing")
    decided = score(defn, {"greeting": "not_applicable", "disclosure": "passed"})
    assert (decided.outcome, decided.reason) == ("not_applicable", "no_applicable_criteria")


def test_scorecard_row_5_fails_when_even_the_missing_evidence_could_not_pass():
    defn = definition(
        criterion("greeting", 50), criterion("closing", 30), criterion("fee", 20), pass_threshold=80
    )
    result = score(defn, {"greeting": "failed", "closing": "passed", "fee": "not_computable"})
    assert (result.earned_weight + result.insufficient_weight) * 100 < 80 * 100
    assert (result.outcome, result.reason) == ("failed", "below_threshold")
    complete = score(defn, {"greeting": "passed", "closing": "passed", "fee": "failed"})
    assert (complete.outcome, complete.reason, complete.score) == ("passed", None, 80.0)
    below = score(defn, {"greeting": "passed", "closing": "failed", "fee": "passed"})
    assert (below.outcome, below.reason, below.score) == ("failed", "below_threshold", 70.0)


def test_scorecard_row_6_passes_when_even_failed_missing_evidence_would_pass():
    defn = definition(criterion("greeting", 60), criterion("closing", 40), pass_threshold=60)
    result = score(defn, {"greeting": "passed", "closing": "not_computable"})
    assert result.complete is False and result.score is None
    assert (result.outcome, result.reason) == ("passed", None)


def test_scorecard_row_6_never_depends_on_an_auto_fail_criterion_without_evidence():
    defn = definition(
        criterion("greeting", 90), criterion("disclosure", 10, auto_fail=True), pass_threshold=80
    )
    result = score(defn, {"greeting": "passed", "disclosure": "not_computable"})
    assert result.earned_weight * 100 >= 80 * result.applicable_weight
    assert (result.outcome, result.reason) == ("insufficient_evidence", "evidence_missing")
    zero = definition(
        criterion("greeting", 100), criterion("disclosure", 0, auto_fail=True), pass_threshold=80
    )
    result = score(
        zero, {"greeting": "passed", "disclosure": ("not_computable", "low_asr_confidence")}
    )
    assert (result.outcome, result.reason) == ("insufficient_evidence", "evidence_missing")


def test_scorecard_row_7_missing_evidence_never_passes_by_default():
    defn = definition(criterion("greeting", 70), criterion("closing", 30), pass_threshold=80)
    between = score(defn, {"greeting": "passed", "closing": "not_computable"})
    assert (between.score_min, between.score_max) == (70.0, 100.0)
    assert (between.outcome, between.reason) == ("insufficient_evidence", "evidence_missing")
    nothing = score(defn, {"greeting": "not_computable", "closing": "not_computable"})
    assert (nothing.outcome, nothing.reason) == ("insufficient_evidence", "evidence_missing")
    review = score(defn, {"greeting": "passed", "closing": "passed"}, review_required=True)
    assert review.outcome == "insufficient_evidence"


def test_the_threshold_boundary_passes():
    defn = definition(criterion("greeting", 80), criterion("closing", 20), pass_threshold=80)
    assert score(defn, {"greeting": "passed", "closing": "failed"}).outcome == "passed"
    one_less = definition(criterion("greeting", 79), criterion("closing", 21), pass_threshold=80)
    assert score(one_less, {"greeting": "passed", "closing": "failed"}).outcome == "failed"


# --- 4.5: the result shape ---------------------------------------------------------------

EXAMPLE = definition(
    criterion("greeting", 10, label="Greets the customer"),
    criterion("needs", 60),
    criterion("price", 10),
    criterion(
        "fee_explained",
        20,
        na_allowed=True,
        check=contextual("fee_explained", trigger_phrases=["cancel"]),
    ),
    pass_threshold=80,
)


def _example() -> ScorecardResult:
    by_id = {item["criterion_id"]: item for item in EXAMPLE["criteria"]}
    run = run_of(
        check_result(by_id["greeting"], "passed"),
        check_result(by_id["needs"], "passed"),
        check_result(by_id["price"], "failed"),
        check_result(by_id["fee_explained"], "not_applicable", reason="not_triggered"),
    )
    return score_conversation(EXAMPLE, run, CALL_IN)


def test_the_result_has_exactly_the_documented_shape():
    data = _example().to_json()
    assert json.loads(json.dumps(data)) == data
    assert list(data) == [
        "scorecards_version",
        "checks_version",
        "definition_digest",
        "conversation",
        "outcome",
        "reason",
        "complete",
        "pass_threshold",
        "score",
        "score_min",
        "score_max",
        "applicable_weight",
        "earned_weight",
        "failed_weight",
        "insufficient_weight",
        "criteria",
    ]
    assert {key: data[key] for key in list(data)[:-1]} == {
        "scorecards_version": "scorecards/1",
        "checks_version": "conversation-checks/1",
        "definition_digest": definition_digest(EXAMPLE),
        "conversation": {"conversation_type": "call", "direction": "inbound"},
        "outcome": "passed",
        "reason": None,
        "complete": True,
        "pass_threshold": 80,
        "score": 87.5,
        "score_min": 87.5,
        "score_max": 87.5,
        "applicable_weight": 80,
        "earned_weight": 70,
        "failed_weight": 10,
        "insufficient_weight": 0,
    }
    assert data["criteria"][0] == {
        "criterion_id": "greeting",
        "label": "Greets the customer",
        "weight": 10,
        "auto_fail": False,
        "na_allowed": False,
        "outcome": "passed",
        "reason": None,
        "decided_by": "rule",
        "review_required": False,
        "review_reasons": [],
        "check_outcome": "passed",
        "check_reason": None,
        "evidence": [],
        "candidate_evidence": [],
    }
    assert [item["criterion_id"] for item in data["criteria"]] == [
        "greeting",
        "needs",
        "price",
        "fee_explained",
    ]
    for item in data["criteria"]:
        assert item["outcome"] in ("passed", "failed", "not_applicable", "insufficient_evidence")
        assert type(item["outcome"]) is str
        assert isinstance(item["review_reasons"], list) and isinstance(item["evidence"], list)


def test_the_result_is_frozen_deterministic_and_never_changes_its_inputs():
    by_id = {item["criterion_id"]: item for item in EXAMPLE["criteria"]}
    run = run_of(
        check_result(by_id["greeting"], "passed", evidence=[SPAN]),
        check_result(by_id["needs"], "failed", review_required=True, review_reasons=["negation"]),
        check_result(by_id["price"], "failed"),
        check_result(by_id["fee_explained"], "not_applicable", reason="not_triggered"),
    )
    reviewer = {"needs": "passed"}
    snapshot = copy.deepcopy((EXAMPLE, run, reviewer, CALL_IN))
    first = score_conversation(EXAMPLE, run, CALL_IN, reviewer)
    second = score_conversation(EXAMPLE, run, CALL_IN, reviewer)
    assert json.dumps(first.to_json()) == json.dumps(second.to_json())
    assert (EXAMPLE, run, reviewer, CALL_IN) == snapshot
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.outcome = "failed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.criteria[0].outcome = "failed"  # type: ignore[misc]
    # Changing the JSON answer does not change the result.
    data = first.to_json()
    data["criteria"][0]["evidence"].append({"turn_index": 9})
    assert first.to_json()["criteria"][0]["evidence"] == [SPAN]
    run["results"][0]["evidence"].append({"turn_index": 8})
    assert first.to_json()["criteria"][0]["evidence"] == [SPAN]


# --- 4.6: the digest ---------------------------------------------------------------------


def test_the_digest_is_the_sha256_of_the_canonical_json():
    defn = definition(name="فرم کیفیت تماس")
    dumped = validate_definition(defn).model_dump(mode="json")
    canonical = json.dumps(dumped, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert definition_digest(defn) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", definition_digest(defn))
    assert "\\u" not in canonical  # UTF-8, not escapes


def test_every_field_changes_the_digest_and_a_model_or_dict_does_not():
    base = definition(
        criterion("greeting", 50),
        criterion("fee_explained", 50, check=contextual("fee_explained")),
    )
    digest = definition_digest(base)
    assert definition_digest(validate_definition(base)) == digest
    assert definition_digest(ScorecardDefinition(**base)) == digest
    # An explicit default dumps the same as an omitted one.
    explicit = copy.deepcopy(base)
    explicit["description"] = None
    explicit["criteria"][0]["auto_fail"] = False
    assert definition_digest(explicit) == digest

    def changed(mutate) -> str:
        value = copy.deepcopy(base)
        mutate(value)
        return definition_digest(value)

    variants = [
        changed(lambda d: d.update(name="Outbound")),
        changed(lambda d: d.update(description="")),
        changed(lambda d: d.update(pass_threshold=81)),
        changed(lambda d: d["criteria"].reverse()),
        changed(lambda d: d["criteria"][0].update(label="Hello")),
        changed(lambda d: (d["criteria"][0].update(weight=40), d["criteria"][1].update(weight=60))),
        changed(lambda d: d["criteria"][0].update(auto_fail=True)),
        changed(lambda d: d["criteria"][1].update(na_allowed=True)),
        changed(lambda d: d["criteria"][0].update(applies_to={"directions": ["inbound"]})),
        changed(lambda d: d["criteria"][0]["check"].update(version=2)),
        changed(lambda d: d["criteria"][0]["check"].update(critical=True)),
    ]
    assert len({digest, *variants}) == len(variants) + 1


# --- 4.7: feeding HOM-69 evaluation ------------------------------------------------------

RATED = definition(criterion("greeting", 50), criterion("closing", 50), pass_threshold=50)


def _rated(greeting: str, closing: str = "passed") -> ScorecardResult:
    return score(RATED, {"greeting": greeting, "closing": closing})


def test_rating_pairs_give_one_pair_per_criterion_and_drop_missing_evidence():
    pairs = rating_pairs(_rated("passed"), _rated("not_computable"))
    assert pairs == [
        RatingPair("passed", None, criterion="greeting"),
        RatingPair("passed", "passed", criterion="closing"),
    ]
    assert rating_pairs(_rated("failed").to_json(), _rated("passed")) == [
        RatingPair("failed", "passed", criterion="greeting"),
        RatingPair("passed", "passed", criterion="closing"),
    ]


def test_rating_pairs_need_the_same_definition():
    other = definition(criterion("greeting", 50), criterion("closing", 50), pass_threshold=60)
    other_result = score(other, {"greeting": "passed", "closing": "passed"})
    assert code_of(lambda: rating_pairs(_rated("passed"), other_result)) == "definition_mismatch"
    assert code_of(lambda: rating_pairs(_rated("passed"), "result")) == "result_invalid"


def test_the_helpers_feed_kappa_and_score_mae():
    rater_a = ["passed", "passed", "failed", "failed", "passed"]
    rater_b = ["passed", "failed", "failed", "failed", "not_computable"]
    pairs = []
    for a, b in zip(rater_a, rater_b, strict=True):
        pairs.extend(rating_pairs(_rated(a), _rated(b)))
    kappa = kappa_by_criterion(pairs)["greeting"]
    # Observed agreement 3/4, expected 1/2: kappa 0.5.  The missing evidence is excluded.
    assert kappa.value == pytest.approx(0.5)
    assert (kappa.n_items, kappa.n_excluded) == (4, 1)

    full = score_pair(_rated("passed"), 90)
    half = score_pair(_rated("failed"), 40.0)
    assert full == ScorePair(100.0, 90)
    assert half == ScorePair(50.0, 40.0)
    assert score_pair(_rated("not_computable"), 80) is None
    assert score_pair(_rated("passed").to_json(), 90) == full
    assert score_mae([full, half]).value == pytest.approx(10.0)
    for bad in (True, "90", None):
        assert code_of(lambda bad=bad: score_pair(_rated("passed"), bad)) == "result_invalid"
