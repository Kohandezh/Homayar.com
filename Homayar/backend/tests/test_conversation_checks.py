"""Deterministic phrase checks and contextual criteria with targeted evaluation (HOM-55).

Every fixture is synthetic.  Each deny-case (no automatic confirmed violation) sits next
to its closest allow-control, so the result comes from the one thing that changed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend.conversation_checks import (
    CHECKS_VERSION,
    CUE_WINDOW_TOKENS,
    MANIFEST_STATUSES,
    MAX_WINDOW_TURNS,
    MIN_ASR_CONFIDENCE,
    MIN_EVALUATOR_CONFIDENCE,
    NEGATION_CUES,
    REPORTING_VERBS,
    CheckPlan,
    CheckRun,
    ChecksError,
    CheckTurn,
    ContextualCriterion,
    EvaluationAnswer,
    EvaluationRequest,
    EvaluatorInfo,
    Outcome,
    PhraseCheck,
    ReasonCode,
    Scope,
    definition_digest,
    finish_checks,
    plan_checks,
)
from backend.providers.registry import ManifestStatus
from backend.speech.text_normalize import normalize_for_matching

PACKAGE = Path(__file__).resolve().parents[1] / "conversation_checks"

QUALIFIED = EvaluatorInfo(
    provider="local",
    model="judge-small",
    manifest_id="judge",
    manifest_version=3,
    manifest_status="qualified",
)
DEVELOPMENT = EvaluatorInfo("local", "judge-small", "judge", 3, "development")


def agent(text: str, **extra) -> CheckTurn:
    return CheckTurn(text=text, speaker_role="agent", **extra)


def customer(text: str, **extra) -> CheckTurn:
    return CheckTurn(text=text, speaker_role="customer", **extra)


def forbidden(*phrases: str, **extra) -> PhraseCheck:
    fields = {"check_id": "no_guarantee", "version": 1, "kind": "forbidden_phrase"}
    return PhraseCheck(**{**fields, "phrases": list(phrases), **extra})


def required(*phrases: str, **extra) -> PhraseCheck:
    fields = {"check_id": "greeting", "version": 1, "kind": "required_phrase"}
    return PhraseCheck(**{**fields, "phrases": list(phrases), **extra})


def contextual(question: str = "Did the agent explain the cancellation fee?", **extra):
    return ContextualCriterion(
        **{
            "check_id": "fee_explained",
            "version": 1,
            "kind": "contextual",
            "question": question,
            **extra,
        }
    )


def run_one(definition, turns, *, language="en", answers=None, evaluator=None):
    plan = plan_checks([definition], turns, language=language)
    run = finish_checks(plan, answers, evaluator=evaluator)
    return run.results[0]


def as_json(result):
    return result.to_json()


# --- constants and version -----------------------------------------------------------


def test_version_and_constants_are_pinned():
    assert CHECKS_VERSION == "conversation-checks/1"
    assert MIN_ASR_CONFIDENCE == 0.6
    assert MIN_EVALUATOR_CONFIDENCE == 0.7
    assert MAX_WINDOW_TURNS == 12
    assert CUE_WINDOW_TOKENS == 3


def test_required_cue_words_are_present():
    for word in ("not", "no", "never", "نه", "هرگز", "نباید", "نیست"):
        assert word in NEGATION_CUES
    for word in ("said", "says", "told", "گفت", "گفتند", "گفتید"):
        assert word in REPORTING_VERBS


@pytest.mark.parametrize("language", ["fa", "en", "az"])
def test_every_cue_word_folds_to_one_token(language):
    for word in NEGATION_CUES | REPORTING_VERBS:
        folded = normalize_for_matching(word, language)
        assert folded and " " not in folded, word


def test_evaluator_statuses_are_the_registry_manifest_statuses():
    assert MANIFEST_STATUSES == tuple(status.value for status in ManifestStatus)
    for status in ManifestStatus:
        EvaluatorInfo("p", "m", None, None, status.value)
    with pytest.raises(ChecksError) as error:
        EvaluatorInfo("p", "m", None, None, "beta")
    assert error.value.code == "evaluator_invalid"


# --- no own normalizer ---------------------------------------------------------------


def test_package_defines_no_own_normalizer():
    banned = re.compile(
        r"unicodedata|maketrans|casefold\(|\.lower\(|\.upper\(|\.translate\("
        r"|\\u06[0-9a-fA-F]{2}|[۰-۹٠-٩]"
    )
    sources = sorted(PACKAGE.glob("*.py"))
    assert sources
    for path in sources:
        for number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            assert not banned.search(line), f"{path.name}:{number}: {line}"


def test_matching_goes_through_the_shared_normalizer(monkeypatch):
    import backend.conversation_checks.text as text_module

    calls = []
    real = text_module.normalize_for_matching

    def spy(text, language="fa"):
        calls.append(language)
        return real(text, language)

    check = forbidden("refund")
    monkeypatch.setattr(text_module, "normalize_for_matching", spy)
    text_module.clear_caches()
    try:
        run_one(check, [agent("full refund")], language="en")
    finally:
        text_module.clear_caches()
    assert "en" in calls
    assert set(calls) <= {"fa", "en", "az"}


# --- definitions ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"check_id": "A_bad"},
        {"check_id": "a"},
        {"check_id": "1abc"},
        {"check_id": "a" * 65},
        {"check_id": "ok\n"},
        {"version": 0},
        {"version": True},
        {"version": "1"},
        {"kind": "contextual"},
        {"kind": "other"},
        {"phrases": []},
        {"phrases": ["x"] * 33},
        {"phrases": [""]},
        {"phrases": ["  "]},
        {"phrases": ["!!!"]},
        {"phrases": [3]},
        {"critical": "yes"},
        {"speaker_role": "bot"},
        {"model_fallback": 1},
        {"scope": {"kind": "opening"}},
        {"scope": {"kind": "opening", "max_turns": 0}},
        {"scope": {"kind": "closing", "max_turns": None}},
        {"scope": {"kind": "whole_call", "max_turns": 3}},
        {"scope": {"kind": "middle", "max_turns": 3}},
        {"question": "Did they?"},
        {"unknown": 1},
    ],
)
def test_invalid_phrase_check_is_refused(change):
    data = {"check_id": "ok_id", "version": 1, "kind": "required_phrase", "phrases": ["hi"]}
    data.update(change)
    with pytest.raises(ChecksError) as error:
        PhraseCheck(**data)
    assert error.value.code == "check_invalid"
    assert isinstance(error.value, ValueError)
    with pytest.raises(ChecksError) as error:
        plan_checks([data], [agent("hi")], language="en")
    assert error.value.code == "check_invalid"


@pytest.mark.parametrize(
    "change",
    [
        {"question": ""},
        {"question": "   "},
        {"question": None},
        {"trigger_phrases": ["x"] * 33},
        {"trigger_phrases": ["..."]},
        {"context_turns": -1},
        {"context_turns": 4},
        {"context_turns": 1.0},
        {"kind": "required_phrase"},
        {"phrases": ["x"]},
        {"model_fallback": True},
    ],
)
def test_invalid_contextual_criterion_is_refused(change):
    data = {"check_id": "ok_id", "version": 1, "kind": "contextual", "question": "Q?"}
    data.update(change)
    with pytest.raises(ChecksError) as error:
        ContextualCriterion(**data)
    assert error.value.code == "check_invalid"
    with pytest.raises(ChecksError) as error:
        plan_checks([data], [agent("hi")], language="en")
    assert error.value.code == "check_invalid"


def test_defaults():
    check = PhraseCheck(check_id="ab", version=1, kind="required_phrase", phrases=["hi"])
    assert check.critical is False
    assert check.speaker_role == "agent"
    assert check.scope == Scope(kind="whole_call")
    assert check.scope.max_turns is None
    assert check.model_fallback is False
    criterion = ContextualCriterion(check_id="ab", version=1, kind="contextual", question="Q?")
    assert criterion.kind == "contextual"
    assert criterion.trigger_phrases == ()
    assert criterion.context_turns == 1
    assert criterion.speaker_role == "agent"


def test_a_definition_changed_by_model_copy_is_validated_again():
    broken = required("hi").model_copy(update={"phrases": ()})
    with pytest.raises(ChecksError) as error:
        plan_checks([broken], [agent("hello")], language="en")
    assert error.value.code == "check_invalid"
    bad_scope = required("hi").model_copy(update={"scope": {"kind": "opening"}})
    with pytest.raises(ChecksError) as error:
        plan_checks([bad_scope], [agent("hello")], language="en")
    assert error.value.code == "check_invalid"
    with pytest.raises(ChecksError):
        definition_digest(broken)


def test_pydantic_class_methods_raise_validation_error():
    data = {"check_id": "ab", "version": 0, "kind": "required_phrase", "phrases": ["hi"]}
    with pytest.raises(ValidationError):
        PhraseCheck.model_validate(data)
    with pytest.raises(ChecksError):
        plan_checks([data], [agent("hi")], language="en")


def test_contextual_kind_is_required():
    with pytest.raises(ChecksError) as error:
        ContextualCriterion(check_id="ab", version=1, question="Q?")  # type: ignore[call-arg]
    assert error.value.code == "check_invalid"


def test_definitions_are_frozen():
    check = required("hi")
    with pytest.raises(ValidationError):
        check.version = 2  # type: ignore[misc]


def test_a_definition_that_is_not_a_check_is_refused():
    values: list[Any] = [
        "hello",
        3,
        None,
        {"check_id": "ab", "version": 1},
        {"check_id": "ab", "version": 1, "question": "Q?"},
        {"check_id": "ab", "version": 1, "kind": "other", "question": "Q?"},
    ]
    for value in values:
        with pytest.raises(ChecksError) as error:
            plan_checks([value], [agent("hi")], language="en")
        assert error.value.code == "check_invalid"


def test_duplicate_check_id_is_refused():
    with pytest.raises(ChecksError) as error:
        plan_checks([required("hi"), required("hello")], [agent("hi")], language="en")
    assert error.value.code == "duplicate_check"


@pytest.mark.parametrize("language", ["de", "", "FA", "fa-IR", None, 1])
def test_unsupported_language_is_refused(language):
    with pytest.raises(ChecksError) as error:
        plan_checks([required("hi")], [agent("hi")], language=language)
    assert error.value.code == "language_unsupported"


def test_phrase_that_folds_empty_in_the_call_language_is_refused():
    # Tatweel is a letter for the English normaliser but is dropped by the Persian one.
    check = required("ـ")
    plan_checks([check], [agent("x")], language="en")
    with pytest.raises(ChecksError) as error:
        plan_checks([check], [agent("x")], language="fa")
    assert error.value.code == "check_invalid"


@pytest.mark.parametrize(
    "turn",
    [
        {"text": 3},
        {"text": "x", "speaker_role": "bot"},
        {"text": "x", "start_ms": -1},
        {"text": "x", "start_ms": 5, "end_ms": 4},
        {"text": "x", "start_ms": 1.5},
        {"text": "x", "asr_confidence": 1.5},
        {"text": "x", "asr_confidence": float("nan")},
        {"text": "x", "asr_confidence": True},
    ],
)
def test_invalid_turn_is_refused(turn):
    with pytest.raises(ChecksError) as error:
        CheckTurn(**turn)
    assert error.value.code == "turn_invalid"
    with pytest.raises(ChecksError) as error:
        plan_checks([required("hi")], [turn], language="en")
    assert error.value.code == "turn_invalid"


def test_turns_may_be_plain_dicts():
    result = run_one(required("hi"), [{"text": "hi there", "speaker_role": "agent"}])
    assert result.outcome == "passed"


# --- digest --------------------------------------------------------------------------


def test_definition_digest_is_canonical_json_sha256():
    check = required("hi", critical=True)
    canonical = json.dumps(
        check.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert definition_digest(check) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert definition_digest(check.model_dump()) == definition_digest(check)


def test_definition_digest_changes_with_any_field():
    base = required("hi")
    variants = [
        required("hi", check_id="greeting_b"),
        required("hi", version=2),
        required("hi", critical=True),
        required("hi", speaker_role="any"),
        required("hi", scope={"kind": "opening", "max_turns": 2}),
        required("hi", model_fallback=True),
        required("hi", "hello"),
        required("Hi"),
        forbidden("hi", check_id="greeting"),
    ]
    digests = {definition_digest(base)} | {definition_digest(v) for v in variants}
    assert len(digests) == len(variants) + 1


def test_digest_is_pinned():
    criterion = ContextualCriterion(
        check_id="fee_explained", version=1, kind="contextual", question="Q?"
    )
    canonical = (
        '{"check_id":"fee_explained","context_turns":1,"critical":false,"kind":"contextual",'
        '"question":"Q?","scope":{"kind":"whole_call","max_turns":null},'
        '"speaker_role":"agent","trigger_phrases":[],"version":1}'
    )
    assert definition_digest(criterion) == hashlib.sha256(canonical.encode()).hexdigest()


# --- matching ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("phrase", "text", "language"),
    [
        ("کیفیت", "كيفيت خدمات", "fa"),  # Arabic yeh and kaf
        ("می خواهم", "من میخواهم", "fa"),
        ("می‌خواهم", "من می خواهم", "fa"),
        ("میخواهم", "من می‌خواهم", "fa"),
        ("کد 48213", "کد ۴۸۲۱۳ است", "fa"),
        ("کد 48213", "کد ٤٨٢١٣ است", "fa"),
        ("this call is recorded", "Hello, THIS call is RECORDED!", "en"),
        ("this call, is recorded", "this call is recorded", "en"),
        ("سلام", "سلام، وقت بخیر", "fa"),
    ],
)
def test_phrase_matches(phrase, text, language):
    result = run_one(required(phrase), [agent(text)], language=language)
    assert result.outcome == "passed"
    assert result.decided_by == "rule"


@pytest.mark.parametrize(
    ("phrase", "turns", "language"),
    [
        ("call", ["please callback later"], "en"),
        ("سلام", ["سلامت باشید"], "fa"),
        ("call recorded", ["recorded call"], "en"),
        ("this call is recorded", ["this call", "is recorded"], "en"),
    ],
)
def test_phrase_does_not_match(phrase, turns, language):
    result = run_one(required(phrase), [agent(t) for t in turns], language=language)
    assert result.outcome == "failed"
    assert result.evidence == ()


def test_span_shape_and_token_offsets():
    turns = [
        CheckTurn("Good morning, this call is recorded.", "agent", 0, 4200, 0.95),
        customer("ok"),
    ]
    result = run_one(required("this call is recorded", critical=True), turns)
    data = as_json(result)
    assert data["evidence"] == [
        {
            "turn_index": 0,
            "speaker_role": "agent",
            "start_ms": 0,
            "end_ms": 4200,
            "phrase": "this call is recorded",
            "token_start": 2,
            "token_end": 6,
        }
    ]
    tokens = normalize_for_matching(turns[0].text, "en").split(" ")
    assert tokens[2:6] == ["this", "call", "is", "recorded"]


def test_required_passes_on_first_clean_match():
    turns = [
        agent("hi hi", asr_confidence=0.2),
        customer("hello"),
        agent("well hi and hello"),
        agent("hello"),
    ]
    result = run_one(required("hello", "hi"), turns)
    assert result.outcome == "passed"
    assert [(s.turn_index, s.token_start, s.phrase) for s in result.evidence] == [(2, 1, "hi")]
    assert result.candidate_evidence == ()


def test_forbidden_reports_every_clean_match_in_order():
    turns = [agent("refund now refund"), customer("refund"), agent("full refund")]
    result = run_one(forbidden("refund", "full refund"), turns)
    assert result.outcome == "failed"
    assert [(s.turn_index, s.token_start, s.token_end, s.phrase) for s in result.evidence] == [
        (0, 0, 1, "refund"),
        (0, 2, 3, "refund"),
        (2, 0, 2, "full refund"),
        (2, 1, 2, "refund"),
    ]


def test_two_phrases_with_the_same_fold_give_one_span():
    result = run_one(forbidden("Refund", "refund"), [agent("refund")])
    assert [s.phrase for s in result.evidence] == ["Refund"]


# --- scope ---------------------------------------------------------------------------


def test_opening_scope_searches_only_the_first_turns():
    turns = [customer("hi"), agent("how can I help"), agent("hello")]
    check = required("hello", scope={"kind": "opening", "max_turns": 2})
    result = run_one(check, turns)
    assert result.outcome == "failed"
    assert result.scope_turn_indexes == (0, 1)
    wider = required("hello", scope={"kind": "opening", "max_turns": 3})
    assert run_one(wider, turns).outcome == "passed"


def test_closing_scope_searches_only_the_last_turns():
    turns = [agent("goodbye"), customer("thanks"), agent("anything else")]
    check = required("goodbye", scope=Scope(kind="closing", max_turns=2))
    result = run_one(check, turns)
    assert result.outcome == "failed"
    assert result.scope_turn_indexes == (1, 2)
    assert (
        run_one(required("goodbye", scope=Scope(kind="closing", max_turns=9)), turns).outcome
        == "passed"
    )


# --- F42 deny-cases and allow-controls -----------------------------------------------


@pytest.mark.parametrize(
    ("deny", "allow", "language", "phrase"),
    [
        (
            "we will not guarantee a refund",
            "we will guarantee a refund",
            "en",
            "guarantee a refund",
        ),
        ("we never guarantee a refund", "we guarantee a refund", "en", "guarantee a refund"),
        ("we can't guarantee a refund", "we can guarantee a refund", "en", "guarantee a refund"),
        ("no we guarantee a refund", "so we guarantee a refund", "en", "guarantee a refund"),
        ("نه، بازپرداخت تضمین است", "بله، بازپرداخت تضمین است", "fa", "بازپرداخت تضمین"),
        (
            "ما هرگز بازپرداخت تضمین داریم",
            "ما همیشه بازپرداخت تضمین داریم",
            "fa",
            "بازپرداخت تضمین",
        ),
        ("بازپرداخت تضمین نمی‌کنیم", "بازپرداخت تضمین می‌کنیم", "fa", "بازپرداخت تضمین"),
        ("بازپرداخت تضمین نیست", "بازپرداخت تضمین هست", "fa", "بازپرداخت تضمین"),
    ],
)
def test_negation_is_never_an_automatic_violation(deny, allow, language, phrase):
    denied = run_one(forbidden(phrase, critical=True), [agent(deny)], language=language)
    assert denied.outcome == "not_computable"
    assert denied.reason == "negation"
    assert denied.review_required is True
    assert denied.review_reasons == ("negation",)
    assert denied.evidence == ()
    assert len(denied.candidate_evidence) == 1

    allowed = run_one(forbidden(phrase, critical=True), [agent(allow)], language=language)
    assert allowed.outcome == "failed"
    assert allowed.decided_by == "rule"
    assert allowed.review_required is False


def test_negation_cue_window_is_three_tokens_each_side():
    phrase = "guarantee a refund"
    near_before = "not one two guarantee a refund"
    far_before = "not one two three guarantee a refund"
    near_after = "guarantee a refund one two not"
    far_after = "guarantee a refund one two three not"
    assert run_one(forbidden(phrase), [agent(near_before)]).reason == "negation"
    assert run_one(forbidden(phrase), [agent(near_after)]).reason == "negation"
    assert run_one(forbidden(phrase), [agent(far_before)]).outcome == "failed"
    assert run_one(forbidden(phrase), [agent(far_after)]).outcome == "failed"


def test_a_cue_inside_the_phrase_itself_is_not_a_negation():
    result = run_one(forbidden("no refund possible"), [agent("there is no refund possible")])
    assert result.outcome == "failed"


def test_negation_in_another_turn_does_not_count():
    turns = [customer("no"), agent("we guarantee a refund")]
    assert run_one(forbidden("guarantee a refund"), turns).outcome == "failed"


@pytest.mark.parametrize(
    ("deny", "allow", "language"),
    [
        ('the ad says "full refund"', "the ad full refund", "en"),
        ("the ad “full refund” yes", "the ad full refund yes", "en"),
        ("he said full refund", "he full refund", "en"),
        ("he told me full refund", "he gave me full refund", "en"),
        ("«بازپرداخت کامل» در آگهی بود", "بازپرداخت کامل در آگهی بود", "fa"),
        ('"بازپرداخت کامل" در آگهی بود', "بازپرداخت کامل در آگهی بود", "fa"),
        ("مدیر گفت بازپرداخت کامل", "مدیر بازپرداخت کامل", "fa"),
        ("شما گفتید بازپرداخت کامل", "شما بازپرداخت کامل", "fa"),
    ],
)
def test_quoted_speech_is_never_an_automatic_violation(deny, allow, language):
    phrase = "full refund" if language == "en" else "بازپرداخت کامل"
    denied = run_one(forbidden(phrase), [agent(deny)], language=language)
    assert denied.outcome == "not_computable"
    assert denied.reason == "quoted_speech"
    assert denied.review_required is True
    allowed = run_one(forbidden(phrase), [agent(allow)], language=language)
    assert allowed.outcome == "failed"
    assert allowed.review_required is False


def test_quotes_elsewhere_in_the_turn_do_not_make_a_match_quoted():
    turn = agent('the plan "gold" gives a full refund')
    assert run_one(forbidden("full refund"), [turn]).outcome == "failed"
    turn = agent("طرح «طلایی» بازپرداخت کامل دارد")
    assert run_one(forbidden("بازپرداخت کامل"), [turn], language="fa").outcome == "failed"


@pytest.mark.parametrize(
    ("text", "phrase", "language"),
    [
        ("«این «سود تضمینی» است»", "سود تضمینی", "fa"),
        ('«این "سود تضمینی" است»', "سود تضمینی", "fa"),
        ('“The plan has "guaranteed returns" built in”', "guaranteed returns", "en"),
        ('"The plan has "guaranteed returns" built in"', "guaranteed returns", "en"),
        ('"این "سود تضمینی" است"', "سود تضمینی", "fa"),
        ('＂The plan has "guaranteed returns" built in＂', "guaranteed returns", "en"),
    ],
)
def test_a_phrase_in_a_nested_quote_is_quoted_speech(text, phrase, language):
    denied = run_one(forbidden(phrase), [agent(text)], language=language)
    assert denied.outcome == "not_computable"
    assert denied.reason == "quoted_speech"
    assert denied.review_reasons == ("quoted_speech",)
    assert denied.review_required is True


@pytest.mark.parametrize(
    ("text", "phrase", "language"),
    [
        ("«سلام» ما سود تضمینی داریم", "سود تضمینی", "fa"),
        ('She wrote "hello" and we offer guaranteed returns', "guaranteed returns", "en"),
        ("“The plan” has «gold» and guaranteed returns", "guaranteed returns", "en"),
        ('She wrote "hi" and "bye", we offer guaranteed returns', "guaranteed returns", "en"),
    ],
)
def test_a_phrase_outside_a_closed_quote_is_clean(text, phrase, language):
    allowed = run_one(forbidden(phrase), [agent(text)], language=language)
    assert allowed.outcome == "failed"
    assert allowed.decided_by == "rule"
    assert allowed.review_required is False


@pytest.mark.parametrize(
    "text",
    [
        "guaranteed returns, then the ad said “gold",
        "guaranteed returns » here",
        '“the plan "guaranteed returns” here',
        "«the plan guaranteed returns” here",
        'we offer guaranteed returns and "gold',
    ],
)
def test_an_unclear_quote_structure_makes_every_match_quoted(text):
    denied = run_one(forbidden("guaranteed returns"), [agent(text)])
    assert denied.outcome == "not_computable"
    assert "quoted_speech" in denied.review_reasons


def test_azerbaijani_cues():
    phrase = "tam geri ödəmə"
    denied = run_one(forbidden(phrase), [agent("biz heç tam geri ödəmə veririk")], language="az")
    assert denied.reason == "negation"
    allowed = run_one(forbidden(phrase), [agent("biz tam geri ödəmə veririk")], language="az")
    assert allowed.outcome == "failed"
    quoted = run_one(forbidden(phrase), [agent("o dedi tam geri ödəmə")], language="az")
    assert quoted.reason == "quoted_speech"


def test_an_unclosed_quote_covers_the_rest_of_the_turn():
    turn = agent('the ad "promised a full refund')
    assert run_one(forbidden("full refund"), [turn]).reason == "quoted_speech"


def test_wrong_speaker_never_violates_a_forbidden_phrase():
    denied = run_one(forbidden("full refund"), [customer("I want a full refund")])
    assert denied.outcome == "passed"
    assert denied.evidence == () and denied.candidate_evidence == ()
    allowed = run_one(forbidden("full refund"), [agent("I give a full refund")])
    assert allowed.outcome == "failed" and allowed.review_required is False


def test_customer_role_check_ignores_the_agent():
    check = forbidden("full refund", speaker_role="customer")
    assert run_one(check, [agent("a full refund")]).outcome == "passed"
    assert run_one(check, [customer("a full refund")]).outcome == "failed"


def test_any_role_check_counts_every_speaker():
    check = forbidden("full refund", speaker_role="any")
    assert run_one(check, [customer("a full refund")]).outcome == "failed"
    assert run_one(check, [CheckTurn("a full refund")]).outcome == "failed"


def test_unknown_speaker_is_never_an_automatic_violation():
    denied = run_one(forbidden("full refund"), [CheckTurn("a full refund")])
    assert denied.outcome == "not_computable"
    assert denied.reason == "speaker_unknown"
    assert denied.review_required is True
    allowed = run_one(forbidden("full refund"), [agent("a full refund")])
    assert allowed.outcome == "failed" and allowed.review_required is False


def test_poor_asr_is_never_an_automatic_violation():
    denied = run_one(forbidden("full refund"), [agent("a full refund", asr_confidence=0.3)])
    assert denied.outcome == "not_computable"
    assert denied.reason == "low_asr_confidence"
    assert denied.review_required is True
    for confidence in (0.9, None, MIN_ASR_CONFIDENCE):
        allowed = run_one(
            forbidden("full refund"), [agent("a full refund", asr_confidence=confidence)]
        )
        assert allowed.outcome == "failed" and allowed.review_required is False


def test_required_phrase_said_only_by_the_customer_does_not_pass():
    denied = run_one(required("this call is recorded"), [customer("is this call is recorded")])
    assert denied.outcome == "not_computable"
    assert denied.reason == "wrong_speaker"
    assert denied.review_required is True
    allowed = run_one(required("this call is recorded"), [agent("this call is recorded")])
    assert allowed.outcome == "passed"


def test_required_phrase_absent_with_poor_asr_is_not_a_failure():
    turns = [agent("hello there", asr_confidence=0.3), customer("hi")]
    denied = run_one(required("this call is recorded"), turns)
    assert denied.outcome == "not_computable"
    assert denied.reason == "low_asr_confidence"
    assert denied.review_required is False
    assert denied.decided_by is None
    turns = [agent("hello there", asr_confidence=0.9), customer("hi")]
    allowed = run_one(required("this call is recorded"), turns)
    assert allowed.outcome == "failed"
    assert allowed.review_required is False
    assert allowed.scope_turn_indexes == (0, 1)


def test_low_asr_in_an_irrelevant_turn_does_not_block_absence():
    turns = [agent("hello there"), customer("hi", asr_confidence=0.1)]
    assert run_one(required("this call is recorded"), turns).outcome == "failed"
    turns = [agent("hello there"), CheckTurn("hi", asr_confidence=0.1)]
    assert run_one(required("this call is recorded"), turns).reason == "low_asr_confidence"


def test_forbidden_absent_with_poor_asr_is_not_computable():
    turns = [agent("hello", asr_confidence=0.59)]
    result = run_one(forbidden("full refund"), turns)
    assert (result.outcome, result.reason, result.review_required) == (
        "not_computable",
        "low_asr_confidence",
        False,
    )
    assert (
        run_one(forbidden("full refund"), [agent("hello", asr_confidence=0.6)]).outcome == "passed"
    )


def test_no_transcript():
    for definition in (required("hi"), forbidden("hi"), contextual()):
        result = run_one(definition, [])
        assert result.outcome == "not_computable"
        assert result.reason == "no_transcript"
        assert result.review_required is False
        assert result.scope_turn_indexes == ()
    assert plan_checks([contextual()], [], language="en").requests == ()


def test_undecided_lists_every_doubt_in_fixed_order():
    turns = [
        CheckTurn("he said not a full refund", asr_confidence=0.2),
        agent("never a full refund"),
    ]
    result = run_one(forbidden("full refund"), turns)
    assert result.reason == "speaker_unknown"
    assert result.review_reasons == (
        "speaker_unknown",
        "low_asr_confidence",
        "quoted_speech",
        "negation",
    )
    assert [s.turn_index for s in result.candidate_evidence] == [0, 1]
    assert result.decided_by is None


def test_rule_results_are_confirmed_even_when_critical():
    result = run_one(forbidden("full refund", critical=True), [agent("a full refund")])
    assert (result.outcome, result.decided_by, result.review_required) == ("failed", "rule", False)


# --- targeted model evaluation for phrase checks --------------------------------------


def _fallback_turns():
    return [
        agent("hello"),
        customer("can I get my money back"),
        agent("we will not give a full refund"),
        customer("why"),
        agent("policy"),
    ]


def test_fallback_builds_one_targeted_request():
    check = forbidden("full refund", model_fallback=True)
    plan = plan_checks([check], _fallback_turns(), language="en")
    assert len(plan.requests) == 1
    request = plan.requests[0]
    assert isinstance(request, EvaluationRequest)
    assert request.check_id == "no_guarantee"
    assert request.language == "en"
    assert request.turn_indexes == (1, 2, 3)
    assert request.turns == (
        (1, "customer", "can I get my money back"),
        (2, "agent", "we will not give a full refund"),
        (3, "customer", "why"),
    )
    assert "full refund" in request.question
    assert "agent" in request.question
    again = plan_checks([check], _fallback_turns(), language="en")
    assert again.requests[0].request_id == request.request_id


def test_request_id_depends_on_the_window_text():
    check = forbidden("full refund", model_fallback=True)
    first = plan_checks([check], _fallback_turns(), language="en").requests[0]
    turns = _fallback_turns()
    turns[3] = customer("why not")
    second = plan_checks([check], turns, language="en").requests[0]
    assert first.request_id != second.request_id
    assert first.request_id.startswith("no_guarantee:")


def test_fallback_window_stays_inside_the_scope():
    turns = [agent("a"), agent("not a full refund"), agent("c")]
    check = forbidden("full refund", model_fallback=True, scope={"kind": "closing", "max_turns": 2})
    request = plan_checks([check], turns, language="en").requests[0]
    assert request.turn_indexes == (1, 2)


def test_fallback_met_and_not_met():
    check = forbidden("full refund", model_fallback=True)
    plan = plan_checks([check], _fallback_turns(), language="en")
    rid = plan.requests[0].request_id
    met = finish_checks(
        plan, {rid: EvaluationAnswer("met", [2], 0.9)}, evaluator=QUALIFIED
    ).results[0]
    assert (met.outcome, met.decided_by, met.reason) == ("failed", "model", None)
    # A phrase check reaches the model only with doubts, so they stay review reasons.
    assert met.review_required is True
    assert met.review_reasons == ("negation",)
    assert [(s.turn_index, s.phrase, s.token_start) for s in met.evidence] == [(2, None, None)]
    assert [s.turn_index for s in met.candidate_evidence] == [2]
    not_met = finish_checks(
        plan,
        {rid: {"verdict": "not_met", "source_turn_indexes": [2], "confidence": 0.8}},
        evaluator=QUALIFIED,
    ).results[0]
    assert (not_met.outcome, not_met.decided_by) == ("passed", "model")

    req = required("full refund", model_fallback=True)
    plan = plan_checks([req], _fallback_turns(), language="en")
    rid = plan.requests[0].request_id
    answer = {rid: EvaluationAnswer("met", [2], 0.9)}
    assert finish_checks(plan, answer, evaluator=QUALIFIED).results[0].outcome == "passed"
    answer = {rid: EvaluationAnswer("not_met", [2], 0.9)}
    assert finish_checks(plan, answer, evaluator=QUALIFIED).results[0].outcome == "failed"


def test_fallback_model_review_reasons_add_critical_unknown_and_low_asr():
    turns = [
        CheckTurn("hello", asr_confidence=0.1),
        agent("not a full refund"),
        customer("ok"),
    ]
    check = forbidden("full refund", model_fallback=True, critical=True)
    plan = plan_checks([check], turns, language="en")
    rid = plan.requests[0].request_id
    result = finish_checks(
        plan, {rid: EvaluationAnswer("not_met", [1], 0.95)}, evaluator=QUALIFIED
    ).results[0]
    assert result.outcome == "passed"
    assert result.review_reasons == (
        "negation",
        "critical_check",
        "speaker_unknown",
        "low_asr_confidence",
    )


def test_fallback_not_computable_keeps_the_doubt():
    check = forbidden("full refund", model_fallback=True)
    plan = plan_checks([check], _fallback_turns(), language="en")
    for evaluator, code in [
        (None, "evaluator_unavailable"),
        (DEVELOPMENT, "evaluator_not_qualified"),
    ]:
        result = finish_checks(plan, evaluator=evaluator).results[0]
        assert result.outcome == "not_computable"
        assert result.reason == "negation"
        assert result.review_required is True
        assert result.review_reasons == ("negation", code)
        assert result.evaluation is None
        assert [s.turn_index for s in result.candidate_evidence] == [2]


def test_fallback_window_too_large():
    turns = []
    for _ in range(5):
        turns += [agent("filler"), agent("not a full refund"), agent("filler")]
    plan = plan_checks([forbidden("full refund", model_fallback=True)], turns, language="en")
    assert plan.requests == ()
    result = finish_checks(plan).results[0]
    assert result.outcome == "not_computable"
    assert result.reason == "negation"
    assert result.review_reasons == ("negation", "window_too_large")
    assert result.review_required is True


def test_fallback_window_of_exactly_max_turns_is_allowed():
    turns = []
    for _ in range(4):
        turns += [agent("filler"), agent("not a full refund"), agent("filler")]
    assert len(turns) == MAX_WINDOW_TURNS
    plan = plan_checks([forbidden("full refund", model_fallback=True)], turns, language="en")
    assert len(plan.requests[0].turn_indexes) == MAX_WINDOW_TURNS


def test_no_request_when_the_rule_decides():
    check = forbidden("full refund", model_fallback=True)
    plan = plan_checks([check], [agent("a full refund"), agent("not a full refund")], language="en")
    assert plan.requests == ()
    assert finish_checks(plan).results[0].outcome == "failed"


# --- contextual criteria --------------------------------------------------------------


def _fee_turns():
    return [
        agent("hello, how can I help"),
        customer("I want to cancel my plan"),
        agent("sure, there is a cancellation fee of 20 dollars"),
        customer("ok"),
        agent("anything else"),
    ]


def test_contextual_without_triggers_sends_the_whole_scope():
    plan = plan_checks([contextual()], _fee_turns(), language="en")
    request = plan.requests[0]
    assert request.turn_indexes == (0, 1, 2, 3, 4)
    assert request.question == "Did the agent explain the cancellation fee?"
    assert request.turns[2] == (2, "agent", "sure, there is a cancellation fee of 20 dollars")


def test_contextual_trigger_window():
    criterion = contextual(trigger_phrases=["cancel"], context_turns=1)
    request = plan_checks([criterion], _fee_turns(), language="en").requests[0]
    assert request.turn_indexes == (0, 1, 2)
    criterion = contextual(trigger_phrases=["cancel"], context_turns=0)
    assert plan_checks([criterion], _fee_turns(), language="en").requests[0].turn_indexes == (1,)
    criterion = contextual(trigger_phrases=["cancel"], context_turns=3)
    assert plan_checks([criterion], _fee_turns(), language="en").requests[0].turn_indexes == (
        0,
        1,
        2,
        3,
        4,
    )


def test_contextual_trigger_ignores_role_and_doubts():
    turns = [CheckTurn("I do not want to cancel", asr_confidence=0.1), agent("ok")]
    criterion = contextual(trigger_phrases=["cancel"])
    assert plan_checks([criterion], turns, language="en").requests[0].turn_indexes == (0, 1)


def test_contextual_not_triggered():
    criterion = contextual(trigger_phrases=["refund"])
    plan = plan_checks([criterion], _fee_turns(), language="en")
    assert plan.requests == ()
    result = finish_checks(plan).results[0]
    assert (result.outcome, result.reason, result.review_required) == (
        "not_applicable",
        "not_triggered",
        False,
    )
    assert result.decided_by is None


def test_contextual_window_too_large():
    turns = [agent(f"line {i}") for i in range(13)]
    plan = plan_checks([contextual()], turns, language="en")
    assert plan.requests == ()
    result = finish_checks(plan).results[0]
    assert (result.outcome, result.reason, result.review_required) == (
        "not_computable",
        "window_too_large",
        False,
    )
    assert len(plan_checks([contextual()], turns[:12], language="en").requests) == 1


def _contextual_finish(answer, evaluator=QUALIFIED, criterion=None, turns=None):
    plan = plan_checks([criterion or contextual()], turns or _fee_turns(), language="en")
    rid = plan.requests[0].request_id
    answers = None if answer is None else {rid: answer}
    return finish_checks(plan, answers, evaluator=evaluator).results[0]


def test_contextual_met_is_confirmed_when_not_critical():
    result = _contextual_finish(EvaluationAnswer("met", [2, 1, 2], 0.9))
    assert (result.outcome, result.decided_by, result.review_required) == ("passed", "model", False)
    assert [s.turn_index for s in result.evidence] == [1, 2]
    assert result.evidence[1].speaker_role == "agent"
    assert result.evaluation.verdict == "met"
    assert result.evaluation.manifest_version == 3
    assert result.candidate_evidence == ()
    failed = _contextual_finish(EvaluationAnswer("not_met", [2], 0.9))
    assert (failed.outcome, failed.review_required) == ("failed", False)


def test_contextual_met_on_a_critical_criterion_needs_review():
    result = _contextual_finish(
        EvaluationAnswer("met", [2], 0.99), criterion=contextual(critical=True)
    )
    assert result.outcome == "passed"
    assert result.review_required is True
    assert result.review_reasons == ("critical_check",)
    failed = _contextual_finish(
        EvaluationAnswer("not_met", [2], 0.99), criterion=contextual(critical=True)
    )
    assert (failed.outcome, failed.review_required) == ("failed", True)


def test_contextual_window_with_unknown_or_low_turns_needs_review():
    turns = _fee_turns()
    turns[3] = CheckTurn("ok", asr_confidence=0.2)
    result = _contextual_finish(EvaluationAnswer("met", [2], 0.9), turns=turns)
    assert result.review_reasons == ("speaker_unknown", "low_asr_confidence")
    any_role = contextual(speaker_role="any")
    result = _contextual_finish(EvaluationAnswer("met", [2], 0.9), criterion=any_role, turns=turns)
    assert result.review_reasons == ("low_asr_confidence",)


@pytest.mark.parametrize(
    ("evaluator", "answer", "reason", "has_evaluation"),
    [
        (None, EvaluationAnswer("met", [2], 0.9), "evaluator_unavailable", False),
        (DEVELOPMENT, EvaluationAnswer("met", [2], 0.9), "evaluator_not_qualified", False),
        (
            EvaluatorInfo("p", "m", None, None, "retired"),
            EvaluationAnswer("met", [2], 0.9),
            "evaluator_not_qualified",
            False,
        ),
        (QUALIFIED, None, "evaluator_no_answer", False),
        (QUALIFIED, EvaluationAnswer("met", [], 0.9), "evaluator_output_invalid", True),
        (QUALIFIED, EvaluationAnswer("not_met", [], 0.9), "evaluator_output_invalid", True),
        (QUALIFIED, EvaluationAnswer("met", [9], 0.9), "evaluator_output_invalid", True),
        (QUALIFIED, EvaluationAnswer("met", [-1], 0.9), "evaluator_output_invalid", True),
        (QUALIFIED, EvaluationAnswer("abstain", [9], 0.9), "evaluator_output_invalid", True),
        (QUALIFIED, EvaluationAnswer("met", [2], 1.5), "evaluator_output_invalid", True),
        (QUALIFIED, EvaluationAnswer("maybe", [2], 0.9), "evaluator_output_invalid", True),
        (
            QUALIFIED,
            {"verdict": "met", "source_turn_indexes": [2], "x": 1},
            "evaluator_output_invalid",
            True,
        ),
        (
            QUALIFIED,
            {"verdict": "met", "source_turn_indexes": ["2"]},
            "evaluator_output_invalid",
            True,
        ),
        (QUALIFIED, "met", "evaluator_output_invalid", True),
        (QUALIFIED, EvaluationAnswer("abstain"), "evaluator_abstained", True),
        (QUALIFIED, EvaluationAnswer("abstain", [2], 0.9), "evaluator_abstained", True),
        (QUALIFIED, EvaluationAnswer("met", [2]), "evaluator_low_confidence", True),
        (QUALIFIED, EvaluationAnswer("met", [2], 0.69), "evaluator_low_confidence", True),
        (QUALIFIED, EvaluationAnswer("met", [2], float("nan")), "evaluator_output_invalid", True),
    ],
)
def test_contextual_evaluator_rows(evaluator, answer, reason, has_evaluation):
    result = _contextual_finish(answer, evaluator=evaluator)
    assert result.outcome == "not_computable"
    assert result.reason == reason
    assert result.review_required is False
    assert result.review_reasons == ()
    assert result.decided_by is None
    assert result.evidence == ()
    assert (result.evaluation is not None) is has_evaluation


@pytest.mark.parametrize("confidence", [0.7, 1.0, 1])
def test_confidence_at_the_threshold_is_used(confidence):
    result = _contextual_finish(EvaluationAnswer("met", [2], confidence))
    assert result.outcome == "passed"


def test_qualified_status_given_as_the_registry_enum():
    evaluator = EvaluatorInfo("p", "m", "id", 1, ManifestStatus.QUALIFIED)
    result = _contextual_finish(EvaluationAnswer("met", [2], 0.9), evaluator=evaluator)
    assert result.outcome == "passed"
    assert result.to_json()["evaluation"]["provider"] == "p"


def test_unknown_request_is_refused():
    plan = plan_checks([contextual()], _fee_turns(), language="en")
    with pytest.raises(ChecksError) as error:
        finish_checks(plan, {"nope": EvaluationAnswer("met", [2], 0.9)}, evaluator=QUALIFIED)
    assert error.value.code == "unknown_request"
    with pytest.raises(ChecksError) as error:
        finish_checks(plan, {"nope": EvaluationAnswer("met", [2], 0.9)})
    assert error.value.code == "unknown_request"


# --- the run and its JSON -------------------------------------------------------------


def test_results_follow_definition_order_and_json_shape():
    definitions = [
        required("this call is recorded", check_id="recording_disclosure", critical=True),
        forbidden("full refund"),
        contextual(),
    ]
    turns = [
        CheckTurn("Hello, this call is recorded", "agent", 0, 4200, 0.95),
        customer("I want to cancel"),
        agent("there is a fee"),
    ]
    plan = plan_checks(definitions, turns, language="en")
    assert isinstance(plan, CheckPlan)
    assert [r.check_id for r in plan.requests] == ["fee_explained"]
    rid = plan.requests[0].request_id
    run = finish_checks(plan, {rid: EvaluationAnswer("met", [2], 0.8)}, evaluator=QUALIFIED)
    assert isinstance(run, CheckRun)
    assert [r.check_id for r in run.results] == [
        "recording_disclosure",
        "no_guarantee",
        "fee_explained",
    ]
    data = run.to_json()
    assert json.loads(json.dumps(data)) == data
    assert data["checks_version"] == "conversation-checks/1"
    assert data["language"] == "en"
    assert data["evaluator"] == {
        "provider": "local",
        "model": "judge-small",
        "manifest_id": "judge",
        "manifest_version": 3,
        "manifest_status": "qualified",
    }
    first = data["results"][0]
    assert first == {
        "check_id": "recording_disclosure",
        "check_version": 1,
        "definition_digest": definition_digest(definitions[0]),
        "kind": "required_phrase",
        "critical": True,
        "outcome": "passed",
        "reason": None,
        "decided_by": "rule",
        "review_required": False,
        "review_reasons": [],
        "evidence": [
            {
                "turn_index": 0,
                "speaker_role": "agent",
                "start_ms": 0,
                "end_ms": 4200,
                "phrase": "this call is recorded",
                "token_start": 1,
                "token_end": 5,
            }
        ],
        "candidate_evidence": [],
        "scope_turn_indexes": [0, 1, 2],
        "evaluation": None,
    }
    assert data["results"][1]["outcome"] == "passed"
    third = data["results"][2]
    assert third["decided_by"] == "model"
    assert third["evaluation"] == {
        "request_id": rid,
        "verdict": "met",
        "confidence": 0.8,
        "provider": "local",
        "model": "judge-small",
        "manifest_id": "judge",
        "manifest_version": 3,
    }
    assert third["evidence"] == [
        {
            "turn_index": 2,
            "speaker_role": "agent",
            "start_ms": None,
            "end_ms": None,
            "phrase": None,
            "token_start": None,
            "token_end": None,
        }
    ]
    assert finish_checks(plan).to_json()["evaluator"] is None


def test_every_outcome_and_reason_is_a_known_code():
    assert {o.value for o in Outcome} == {"passed", "failed", "not_applicable", "not_computable"}
    codes = {c.value for c in ReasonCode}
    assert {
        "no_transcript",
        "not_triggered",
        "low_asr_confidence",
        "window_too_large",
        "speaker_unknown",
        "wrong_speaker",
        "quoted_speech",
        "negation",
        "evaluator_unavailable",
        "evaluator_not_qualified",
        "evaluator_no_answer",
        "evaluator_output_invalid",
        "evaluator_abstained",
        "evaluator_low_confidence",
        "critical_check",
    } == codes


def test_pure_and_deterministic():
    definitions: list[Any] = [
        forbidden("full refund", model_fallback=True),
        {"check_id": "fee_explained", "version": 1, "kind": "contextual", "question": "Q?"},
    ]
    turns = _fallback_turns()
    answers = {}
    snapshot = copy.deepcopy((definitions, turns))
    first_plan = plan_checks(definitions, turns, language="en")
    for request in first_plan.requests:
        answers[request.request_id] = {
            "verdict": "met",
            "source_turn_indexes": [2],
            "confidence": 0.9,
        }
    answers_snapshot = copy.deepcopy(answers)
    first = finish_checks(first_plan, answers, evaluator=QUALIFIED).to_json()
    second_plan = plan_checks(definitions, turns, language="en")
    second = finish_checks(second_plan, answers, evaluator=QUALIFIED).to_json()
    assert first == second
    assert (definitions, turns) == snapshot
    assert answers == answers_snapshot
    assert first_plan.requests == second_plan.requests


def test_empty_definition_list_gives_an_empty_run():
    run = finish_checks(plan_checks([], [agent("hi")], language="fa"))
    assert run.results == ()
    assert run.to_json()["results"] == []


def test_request_as_json_is_plain():
    request = plan_checks([contextual()], _fee_turns(), language="en").requests[0]
    data = request.to_json()
    assert json.loads(json.dumps(data)) == data
    assert data["turns"][0] == {
        "index": 0,
        "speaker_role": "agent",
        "text": "hello, how can I help",
    }


# --- Persian end to end ----------------------------------------------------------------


def test_persian_call_end_to_end():
    turns = [
        CheckTurn("سلام، وقت بخير. اين تماس ضبط مي شود", "agent", 0, 3000, 0.9),
        CheckTurn("سلام، مي‌خواهم اشتراكم را لغو كنم", "customer", 3000, 6000, 0.9),
        CheckTurn("براي لغو، هزينه‌اي معادل ۲۰ هزار تومان دارد", "agent", 6000, 9000, 0.9),
        CheckTurn("باشه ممنون", "customer", 9000, 10000, 0.9),
        CheckTurn("ما هرگز بازپرداخت كامل نداريم", "agent", 10000, 12000, 0.9),
    ]
    definitions: list[Any] = [
        PhraseCheck(
            check_id="recording_disclosure",
            version=1,
            kind="required_phrase",
            phrases=["این تماس ضبط می‌شود"],
            critical=True,
            scope={"kind": "opening", "max_turns": 2},
        ),
        PhraseCheck(
            check_id="fee_amount", version=1, kind="required_phrase", phrases=["20 هزار تومان"]
        ),
        PhraseCheck(
            check_id="no_full_refund",
            version=1,
            kind="forbidden_phrase",
            phrases=["بازپرداخت کامل"],
            critical=True,
        ),
        ContextualCriterion(
            check_id="fee_explained",
            version=1,
            kind="contextual",
            question="آیا کارشناس هزینه لغو را توضیح داد؟",
            trigger_phrases=["لغو"],
            context_turns=1,
        ),
    ]
    plan = plan_checks(definitions, turns, language="fa")
    assert [r.check_id for r in plan.requests] == ["fee_explained"]
    request = plan.requests[0]
    assert request.turn_indexes == (0, 1, 2, 3)
    run = finish_checks(
        plan, {request.request_id: EvaluationAnswer("met", [2], 0.85)}, evaluator=QUALIFIED
    )
    outcomes = [(r.check_id, r.outcome, r.reason, r.review_required) for r in run.results]
    assert outcomes == [
        ("recording_disclosure", "passed", None, False),
        ("fee_amount", "passed", None, False),
        ("no_full_refund", "not_computable", "negation", True),
        ("fee_explained", "passed", None, False),
    ]
    assert run.results[0].evidence[0].token_start == 3


def test_float_confidence_values_are_kept_exactly():
    result = _contextual_finish(EvaluationAnswer("met", [2], 0.7000000001))
    assert math.isclose(result.evaluation.confidence, 0.7000000001)
