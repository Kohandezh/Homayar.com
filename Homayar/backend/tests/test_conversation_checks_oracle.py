"""HOM-55 (WP11-T05) acceptance oracle: deterministic phrase checks and contextual criteria.

Written from the accepted contract before the implementation was visible.  Exact phrase
rules decide alone when the match is clean; negation, quoted speech, wrong speaker, unknown
speaker and poor ASR never become an automatic confirmed result; a contextual criterion or
an undecided phrase check goes to a targeted model request that only a qualified evaluator
with a valid, confident answer can decide.  The package is imported inside each test so the
file collects on the base commit and every case fails on its own there.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import re

import pytest

from backend.speech.text_normalize import normalize_for_matching

QUESTION = "Did the agent explain the cancellation fee?"
FA_QUESTION = "آیا کارشناس هزینه لغو را توضیح داد؟"
RESULT_KEYS = {
    "check_id",
    "check_version",
    "definition_digest",
    "kind",
    "critical",
    "outcome",
    "reason",
    "decided_by",
    "review_required",
    "review_reasons",
    "evidence",
    "candidate_evidence",
    "scope_turn_indexes",
    "evaluation",
}
SPAN_KEYS = {
    "turn_index",
    "speaker_role",
    "start_ms",
    "end_ms",
    "phrase",
    "token_start",
    "token_end",
}
EVALUATION_KEYS = {
    "request_id",
    "verdict",
    "confidence",
    "provider",
    "model",
    "manifest_id",
    "manifest_version",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _cc():
    return importlib.import_module("backend.conversation_checks")


def _t(text, role="agent", asr=None, start=None, end=None):
    return _cc().CheckTurn(
        text=text, speaker_role=role, start_ms=start, end_ms=end, asr_confidence=asr
    )


def _phrase(kind, phrases, **extra):
    definition = {"check_id": "phrase_check", "version": 1, "kind": kind, "phrases": list(phrases)}
    definition.update(extra)
    return definition


def _required(phrases, **extra):
    return _phrase("required_phrase", phrases, **extra)


def _forbidden(phrases, **extra):
    return _phrase("forbidden_phrase", phrases, **extra)


def _contextual(question=QUESTION, **extra):
    definition = {
        "check_id": "context_check",
        "version": 1,
        "kind": "contextual",
        "question": question,
    }
    definition.update(extra)
    return definition


def _plan(definitions, turns, language="fa"):
    return _cc().plan_checks(definitions, turns, language=language)


def _doc(run):
    value = run.to_json()
    if isinstance(value, str):
        value = json.loads(value)
    assert json.loads(json.dumps(value, ensure_ascii=False)) == value
    return value


def _finish(plan, answers=None, evaluator=None):
    return _doc(_cc().finish_checks(plan, answers, evaluator=evaluator))


def _one(definition, turns, language="fa", answers=None, evaluator=None):
    plan = _plan([definition], turns, language)
    return _finish(plan, answers, evaluator)["results"][0]


def _rule_only(definition, turns, language="fa"):
    plan = _plan([definition], turns, language)
    assert tuple(plan.requests) == ()
    return _finish(plan)["results"][0]


def _evaluator(status="qualified"):
    return _cc().EvaluatorInfo(
        provider="mock",
        model="judge-1",
        manifest_id="judge-manifest",
        manifest_version=2,
        manifest_status=status,
    )


def _answer(verdict, cites=(), confidence=0.9):
    return {"verdict": verdict, "source_turn_indexes": list(cites), "confidence": confidence}


def _span(index, role, phrase=None, token_start=None, token_end=None, start=None, end=None):
    return {
        "turn_index": index,
        "speaker_role": role,
        "start_ms": start,
        "end_ms": end,
        "phrase": phrase,
        "token_start": token_start,
        "token_end": token_end,
    }


def _confirmed(result, outcome, decided_by="rule"):
    assert result["outcome"] == outcome
    assert result["reason"] is None
    assert result["decided_by"] == decided_by
    assert result["review_required"] is False
    assert result["review_reasons"] == []


def _not_computable(result, reason, review_reasons=()):
    assert result["outcome"] == "not_computable"
    assert result["reason"] == reason
    assert result["decided_by"] is None
    assert result["review_required"] is bool(review_reasons)
    assert result["review_reasons"] == list(review_reasons)


def _codes(exc_info):
    return exc_info.value.code


# --- 3.1 constants ----------------------------------------------------------------------


def test_constants_are_pinned():
    cc = _cc()
    assert cc.CHECKS_VERSION == "conversation-checks/1"
    assert cc.MIN_ASR_CONFIDENCE == 0.6
    assert cc.MIN_EVALUATOR_CONFIDENCE == 0.7
    assert cc.MAX_WINDOW_TURNS == 12
    assert cc.CUE_WINDOW_TOKENS == 3


def test_checks_error_is_a_value_error():
    assert issubclass(_cc().ChecksError, ValueError)


# --- 3.3 definitions and errors -----------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"check_id": "Bad_id"},
        {"check_id": "a"},
        {"check_id": "1abc"},
        {"check_id": "has-dash"},
        {"check_id": "a" * 65},
        {"check_id": ""},
        {"version": 0},
        {"version": -1},
        {"phrases": []},
        {"phrases": [f"phrase {n}" for n in range(33)]},
        {"phrases": ["!!!"]},
        {"phrases": ["   "]},
        {"phrases": ["guaranteed returns", "؟؟"]},
        {"speaker_role": "manager"},
        {"scope": {"kind": "opening"}},
        {"scope": {"kind": "opening", "max_turns": 0}},
        {"scope": {"kind": "closing", "max_turns": -1}},
        {"scope": {"kind": "whole_call", "max_turns": 3}},
        {"scope": {"kind": "middle", "max_turns": 2}},
        {"unexpected": 1},
        {"kind": "maybe_phrase"},
        {"question": QUESTION},
    ],
    ids=lambda change: json.dumps(change, ensure_ascii=False)[:40],
)
def test_bad_phrase_definition_is_check_invalid(change):
    cc = _cc()
    with pytest.raises(cc.ChecksError) as exc_info:
        _plan([{**_forbidden(["guaranteed returns"]), **change}], [_t("hello")], "en")
    assert _codes(exc_info) == "check_invalid"


@pytest.mark.parametrize(
    "change",
    [
        {"question": ""},
        {"context_turns": 4},
        {"context_turns": -1},
        {"trigger_phrases": [f"trigger {n}" for n in range(33)]},
        {"phrases": ["guaranteed returns"]},
        {"scope": {"kind": "closing"}},
        {"check_id": "Context"},
    ],
    ids=lambda change: json.dumps(change, ensure_ascii=False)[:40],
)
def test_bad_contextual_definition_is_check_invalid(change):
    cc = _cc()
    with pytest.raises(cc.ChecksError) as exc_info:
        _plan([_contextual(**change)], [_t("hello")], "en")
    assert _codes(exc_info) == "check_invalid"


@pytest.mark.parametrize(
    "change",
    [
        {"check_id": "ab"},
        {"check_id": "a" + "b" * 63},
        {"check_id": "a_1"},
        {"version": 1},
        {"version": 7},
        {"phrases": [f"phrase {n}" for n in range(32)]},
        {"speaker_role": "any"},
        {"speaker_role": "customer"},
        {"scope": {"kind": "whole_call", "max_turns": None}},
        {"scope": {"kind": "whole_call"}},
        {"scope": {"kind": "opening", "max_turns": 1}},
        {"scope": {"kind": "closing", "max_turns": 40}},
        {"critical": True},
        {"model_fallback": True},
    ],
    ids=lambda change: json.dumps(change, ensure_ascii=False)[:40],
)
def test_valid_phrase_definition_is_accepted(change):
    plan = _plan([{**_forbidden(["guaranteed returns"]), **change}], [_t("hello")], "en")
    assert len(_finish(plan)["results"]) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"context_turns": 0},
        {"context_turns": 3},
        {"trigger_phrases": []},
        {"trigger_phrases": [f"trigger {n}" for n in range(32)]},
        {"speaker_role": "any"},
        {"scope": {"kind": "opening", "max_turns": 2}},
    ],
    ids=lambda change: json.dumps(change, ensure_ascii=False)[:40],
)
def test_valid_contextual_definition_is_accepted(change):
    plan = _plan([_contextual(**change)], [_t("hello")], "en")
    assert len(_finish(plan)["results"]) == 1


def test_models_are_accepted_alongside_dicts():
    cc = _cc()
    phrase = cc.PhraseCheck(
        check_id="greeting",
        version=1,
        kind="required_phrase",
        phrases=["hello"],
        scope=cc.Scope(kind="opening", max_turns=2),
    )
    contextual = cc.ContextualCriterion(
        check_id="fee", version=1, kind="contextual", question=QUESTION
    )
    plan = _plan(
        [phrase, contextual, _forbidden(["guaranteed returns"])], [_t("hello there")], "en"
    )
    results = _finish(plan)["results"]
    assert [r["check_id"] for r in results] == ["greeting", "fee", "phrase_check"]


def test_definition_models_refuse_unknown_keys_and_are_frozen():
    cc = _cc()
    with pytest.raises(ValueError):
        cc.PhraseCheck(
            check_id="greeting", version=1, kind="required_phrase", phrases=["hi"], extra=1
        )
    definition = cc.PhraseCheck(
        check_id="greeting", version=1, kind="required_phrase", phrases=["hi"]
    )
    with pytest.raises((ValueError, TypeError, AttributeError)):
        definition.check_id = "other"


def test_definition_defaults():
    cc = _cc()
    phrase = cc.PhraseCheck(check_id="greeting", version=1, kind="required_phrase", phrases=["hi"])
    assert phrase.critical is False
    assert phrase.speaker_role == "agent"
    assert phrase.scope.kind == "whole_call"
    assert phrase.scope.max_turns is None
    assert phrase.model_fallback is False
    contextual = cc.ContextualCriterion(
        check_id="fee", version=1, kind="contextual", question=QUESTION
    )
    assert list(contextual.trigger_phrases) == []
    assert contextual.context_turns == 1
    assert contextual.critical is False
    assert contextual.speaker_role == "agent"


def test_duplicate_check_id_is_refused():
    cc = _cc()
    with pytest.raises(cc.ChecksError) as exc_info:
        _plan(
            [_required(["hello"], check_id="same_id"), _contextual(check_id="same_id", version=2)],
            [_t("hello")],
            "en",
        )
    assert _codes(exc_info) == "duplicate_check"


def test_distinct_check_ids_are_accepted():
    plan = _plan(
        [_required(["hello"], check_id="first_id"), _contextual(check_id="second_id")],
        [_t("hello")],
        "en",
    )
    assert [r["check_id"] for r in _finish(plan)["results"]] == ["first_id", "second_id"]


@pytest.mark.parametrize("language", ["de", "xx", ""])
def test_unregistered_language_is_refused(language):
    cc = _cc()
    with pytest.raises(cc.ChecksError) as exc_info:
        _plan([_required(["hello"])], [_t("hello")], language)
    assert _codes(exc_info) == "language_unsupported"


@pytest.mark.parametrize("language", ["fa", "en", "az"])
def test_registered_language_is_accepted(language):
    run = _finish(_plan([_required(["hello"])], [_t("hello")], language))
    assert run["language"] == language
    assert run["results"][0]["outcome"] == "passed"


def test_unknown_request_id_in_answers_is_refused():
    cc = _cc()
    plan = _plan([_contextual()], [_t("We charge a cancellation fee.")], "en")
    with pytest.raises(cc.ChecksError) as exc_info:
        cc.finish_checks(plan, {"not-a-request": _answer("met", [0])}, evaluator=_evaluator())
    assert _codes(exc_info) == "unknown_request"


def test_unknown_request_id_on_a_plan_without_requests_is_refused():
    cc = _cc()
    plan = _plan([_required(["hello"])], [_t("hello")], "en")
    with pytest.raises(cc.ChecksError) as exc_info:
        cc.finish_checks(plan, {"anything": _answer("met", [0])}, evaluator=_evaluator())
    assert _codes(exc_info) == "unknown_request"


def test_known_request_id_in_answers_is_accepted():
    plan = _plan([_contextual()], [_t("We charge a cancellation fee.")], "en")
    request_id = plan.requests[0].request_id
    result = _finish(plan, {request_id: _answer("met", [0])}, _evaluator())["results"][0]
    _confirmed(result, "passed", "model")


# --- 3.3 definition_digest -------------------------------------------------------------


def _canonical_digest(definition):
    canonical = json.dumps(
        definition.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_definition_digest_is_sha256_of_canonical_json():
    cc = _cc()
    phrase = cc.PhraseCheck.model_validate(_required(["سلام", "خوش آمدید"], critical=True))
    contextual = cc.ContextualCriterion.model_validate(_contextual(trigger_phrases=["fee"]))
    for definition in (phrase, contextual):
        digest = cc.definition_digest(definition)
        assert HEX64.match(digest)
        assert digest == _canonical_digest(definition)


def test_definition_digest_in_result_matches_for_dict_and_model():
    cc = _cc()
    raw = _required(["hello"])
    model = cc.PhraseCheck.model_validate(raw)
    explicit = cc.PhraseCheck.model_validate(
        {
            **raw,
            "critical": False,
            "speaker_role": "agent",
            "scope": {"kind": "whole_call"},
            "model_fallback": False,
        }
    )
    from_dict = _finish(_plan([raw], [_t("hello")], "en"))["results"][0]
    from_model = _finish(_plan([model], [_t("hello")], "en"))["results"][0]
    assert from_dict["definition_digest"] == cc.definition_digest(model)
    assert from_model["definition_digest"] == cc.definition_digest(model)
    assert cc.definition_digest(explicit) == cc.definition_digest(model)


@pytest.mark.parametrize(
    "change",
    [
        {"check_id": "other_check"},
        {"version": 2},
        {"critical": True},
        {"speaker_role": "any"},
        {"scope": {"kind": "opening", "max_turns": 2}},
        {"phrases": ["hello", "hi"]},
        {"kind": "forbidden_phrase"},
        {"model_fallback": True},
    ],
    ids=lambda change: json.dumps(change)[:40],
)
def test_definition_digest_changes_with_every_field(change):
    cc = _cc()
    base = cc.PhraseCheck.model_validate(_required(["hello"]))
    changed = cc.PhraseCheck.model_validate({**_required(["hello"]), **change})
    assert cc.definition_digest(changed) != cc.definition_digest(base)
    assert cc.definition_digest(
        cc.PhraseCheck.model_validate(_required(["hello"]))
    ) == cc.definition_digest(base)


# --- 3.5 matching -------------------------------------------------------------------------


def test_fixture_folds_agree_with_the_shared_normalizer():
    assert normalize_for_matching("Hello, this call is recorded for quality.", "en").split(" ")[
        1:5
    ] == [
        "this",
        "call",
        "is",
        "recorded",
    ]
    assert normalize_for_matching("شركت كيميا", "fa") == normalize_for_matching("شرکت کیمیا", "fa")
    assert normalize_for_matching("می خواهم", "fa") == normalize_for_matching("میخواهم", "fa")
    assert normalize_for_matching("می‌خواهم", "fa") == normalize_for_matching("میخواهم", "fa")
    assert normalize_for_matching("کد ۱۲۳", "fa") == "کد 123"
    assert normalize_for_matching("کد ١٢٣", "fa") == "کد 123"
    assert normalize_for_matching("سلام، این تماس ضبط می شود", "fa").split(" ") == [
        "سلام",
        "این",
        "تماس",
        "ضبط",
        "میشود",
    ]


@pytest.mark.parametrize(
    ("language", "phrase", "text"),
    [
        ("fa", "شرکت کیمیا", "سلام، شركت كيميا در خدمت شماست"),
        ("fa", "شركت كيميا", "سلام، شرکت کیمیا در خدمت شماست"),
        ("fa", "می خواهم", "من می‌خواهم کمک کنم"),
        ("fa", "می‌خواهم", "من میخواهم کمک کنم"),
        ("fa", "میخواهم", "من می خواهم کمک کنم"),
        ("fa", "کد 123", "کد ۱۲۳ را یادداشت کنید"),
        ("fa", "کد ۱۲۳", "کد ١٢٣ را یادداشت کنید"),
        ("fa", "کد ١٢٣", "کد 123 را یادداشت کنید"),
        ("en", "this call is recorded", "THIS CALL IS RECORDED."),
        ("en", "This Call Is Recorded", "hello, this call is recorded"),
        ("en", "this call is recorded", "Hello, this call, is recorded!"),
        ("en", "call", "Thanks for the call."),
        ("fa", "سلام", "سلام، خوش آمدید"),
    ],
)
def test_folded_spellings_match(language, phrase, text):
    result = _rule_only(_required([phrase]), [_t(text)], language)
    _confirmed(result, "passed")
    assert result["evidence"][0]["phrase"] == phrase


@pytest.mark.parametrize(
    ("language", "phrase", "text"),
    [
        ("en", "call", "Please wait for a callback."),
        ("fa", "سلام", "سلامت باشید"),
        ("en", "this call is recorded", "Is this call recorded?"),
        ("fa", "این تماس ضبط می‌شود", "ضبط این تماس می‌شود"),
        ("en", "this call is recorded", "this call is not recorded"),
    ],
)
def test_partial_token_and_reordered_words_do_not_match(language, phrase, text):
    _confirmed(_rule_only(_required([phrase]), [_t(text)], language), "failed")


def test_phrase_split_across_two_turns_does_not_match():
    turns = [_t("Hello, this call"), _t("is recorded for quality.")]
    result = _rule_only(_required(["this call is recorded"]), turns, "en")
    _confirmed(result, "failed")
    assert result["scope_turn_indexes"] == [0, 1]


def test_span_has_token_range_into_folded_turn_and_the_phrase_as_defined():
    turns = [_t("Hello, this call is recorded for quality.", start=0, end=4200)]
    result = _rule_only(_required(["THIS Call is recorded"]), turns, "en")
    assert result["evidence"] == [_span(0, "agent", "THIS Call is recorded", 1, 5, 0, 4200)]


def test_persian_span_token_range():
    turns = [_t("سلام، این تماس ضبط می شود", start=100, end=2500)]
    result = _rule_only(_required(["این تماس ضبط می‌شود"]), turns, "fa")
    assert result["evidence"] == [_span(0, "agent", "این تماس ضبط می‌شود", 1, 5, 100, 2500)]


# --- 3.7 required_phrase -------------------------------------------------------------------


def test_required_no_turns_is_no_transcript():
    result = _rule_only(_required(["hello"], model_fallback=True), [], "en")
    _not_computable(result, "no_transcript")
    assert result["evidence"] == []
    assert result["candidate_evidence"] == []


def test_required_evidence_is_first_clean_match():
    turns = [
        _t("this call uses a recorded line", role="customer"),
        _t("this call uses a recorded line", start=10, end=20),
        _t("this call again"),
    ]
    result = _rule_only(_required(["recorded line", "this call"]), turns, "en")
    _confirmed(result, "passed")
    assert result["evidence"] == [_span(1, "agent", "this call", 0, 2, 10, 20)]


def test_required_said_only_by_customer_is_not_passed():
    turns = [_t("Hello", role="agent"), _t("this call is recorded", role="customer")]
    result = _rule_only(_required(["this call is recorded"]), turns, "en")
    _not_computable(result, "wrong_speaker", ["wrong_speaker"])
    assert result["evidence"] == []
    assert result["candidate_evidence"] == [_span(1, "customer", "this call is recorded", 0, 4)]


def test_required_said_by_agent_is_passed():
    turns = [_t("Hello", role="customer"), _t("this call is recorded", role="agent")]
    _confirmed(_rule_only(_required(["this call is recorded"]), turns, "en"), "passed")


def test_required_wrong_speaker_for_a_customer_check():
    turns = [_t("من می‌خواهم قرارداد را لغو کنم", role="agent")]
    result = _rule_only(_required(["قرارداد را لغو کنم"], speaker_role="customer"), turns, "fa")
    _not_computable(result, "wrong_speaker", ["wrong_speaker"])


def test_required_said_by_customer_with_any_role_is_passed():
    turns = [_t("this call is recorded", role="customer")]
    _confirmed(
        _rule_only(_required(["this call is recorded"], speaker_role="any"), turns, "en"), "passed"
    )


def test_required_unknown_speaker_is_review():
    result = _rule_only(
        _required(["این تماس ضبط می‌شود"]), [_t("این تماس ضبط می شود", role=None)], "fa"
    )
    _not_computable(result, "speaker_unknown", ["speaker_unknown"])


def test_required_unknown_speaker_with_any_role_is_passed():
    turns = [_t("این تماس ضبط می شود", role=None)]
    _confirmed(
        _rule_only(_required(["این تماس ضبط می‌شود"], speaker_role="any"), turns, "fa"), "passed"
    )


def test_required_absent_with_low_asr_relevant_turn_is_not_computable():
    turns = [_t("Hello there", asr=0.3), _t("How can I help?", asr=0.9)]
    result = _rule_only(_required(["this call is recorded"]), turns, "en")
    _not_computable(result, "low_asr_confidence")
    assert result["evidence"] == []


def test_required_absent_with_low_asr_unknown_role_turn_is_not_computable():
    turns = [_t("سلام", role=None, asr=0.3), _t("بفرمایید", asr=0.95)]
    _not_computable(
        _rule_only(_required(["این تماس ضبط می‌شود"]), turns, "fa"), "low_asr_confidence"
    )


@pytest.mark.parametrize("confidence", [0.9, None, 0.6])
def test_required_absent_all_confident_is_failed(confidence):
    turns = [_t("Hello there", asr=confidence), _t("How can I help?", asr=confidence)]
    result = _rule_only(_required(["this call is recorded"]), turns, "en")
    _confirmed(result, "failed")
    assert result["evidence"] == []
    assert result["scope_turn_indexes"] == [0, 1]


def test_required_absent_low_asr_on_other_role_turn_is_failed():
    turns = [_t("Hello there", asr=0.9), _t("I have a question", role="customer", asr=0.3)]
    _confirmed(_rule_only(_required(["this call is recorded"]), turns, "en"), "failed")


def test_required_absent_low_asr_on_other_role_turn_with_any_role_is_not_computable():
    turns = [_t("Hello there", asr=0.9), _t("I have a question", role="customer", asr=0.3)]
    result = _rule_only(_required(["this call is recorded"], speaker_role="any"), turns, "en")
    _not_computable(result, "low_asr_confidence")


def test_required_absent_low_asr_outside_scope_is_failed():
    turns = [_t("Hello there"), _t("How can I help?"), _t("Goodbye", asr=0.3)]
    scope = {"kind": "opening", "max_turns": 2}
    result = _rule_only(_required(["this call is recorded"], scope=scope), turns, "en")
    _confirmed(result, "failed")
    assert result["scope_turn_indexes"] == [0, 1]


def test_required_rule_result_is_confirmed_when_critical():
    _confirmed(_rule_only(_required(["hello"], critical=True), [_t("hello")], "en"), "passed")
    _confirmed(_rule_only(_required(["hello"], critical=True), [_t("goodbye")], "en"), "failed")


def test_required_clean_match_with_fallback_needs_no_request():
    turns = [_t("hello", role="customer"), _t("hello")]
    _confirmed(_rule_only(_required(["hello"], model_fallback=True), turns, "en"), "passed")


# --- scope --------------------------------------------------------------------------------


def _five_turns():
    return [_t("hello"), _t("one"), _t("two"), _t("three"), _t("goodbye")]


def test_opening_scope_is_first_turns_any_speaker():
    turns = [_t("hi", role="customer"), _t("hello"), _t("two"), _t("three")]
    result = _rule_only(
        _required(["hello"], scope={"kind": "opening", "max_turns": 2}), turns, "en"
    )
    _confirmed(result, "passed")
    assert result["scope_turn_indexes"] == [0, 1]


def test_phrase_after_opening_scope_is_not_found():
    result = _rule_only(
        _required(["goodbye"], scope={"kind": "opening", "max_turns": 2}), _five_turns(), "en"
    )
    _confirmed(result, "failed")
    assert result["scope_turn_indexes"] == [0, 1]


def test_closing_scope_is_last_turns():
    scope = {"kind": "closing", "max_turns": 2}
    found = _rule_only(_required(["goodbye"], scope=scope), _five_turns(), "en")
    _confirmed(found, "passed")
    assert found["scope_turn_indexes"] == [3, 4]
    _confirmed(_rule_only(_required(["hello"], scope=scope), _five_turns(), "en"), "failed")


def test_scope_larger_than_call_and_whole_call():
    opening = _rule_only(
        _required(["goodbye"], scope={"kind": "opening", "max_turns": 50}), _five_turns(), "en"
    )
    assert opening["scope_turn_indexes"] == [0, 1, 2, 3, 4]
    whole = _rule_only(_required(["goodbye"]), _five_turns(), "en")
    assert whole["scope_turn_indexes"] == [0, 1, 2, 3, 4]


def test_forbidden_phrase_outside_scope_is_passed():
    turns = [_t("hello"), _t("guaranteed returns"), _t("goodbye")]
    result = _rule_only(
        _forbidden(["guaranteed returns"], scope={"kind": "closing", "max_turns": 1}), turns, "en"
    )
    _confirmed(result, "passed")
    assert result["scope_turn_indexes"] == [2]


# --- 3.7 forbidden_phrase and section 4 pairs ---------------------------------------------


def test_forbidden_no_turns_is_no_transcript():
    _not_computable(_rule_only(_forbidden(["سود تضمینی"]), [], "fa"), "no_transcript")


def test_forbidden_evidence_is_every_clean_match_in_order():
    turns = [
        _t("risk free and guaranteed returns", start=0, end=10),
        _t("we never promise guaranteed returns"),
        _t("guaranteed returns?", role="customer"),
        _t("guaranteed returns again", start=30, end=40),
    ]
    result = _rule_only(_forbidden(["guaranteed returns", "risk free"]), turns, "en")
    _confirmed(result, "failed")
    assert result["evidence"] == [
        _span(0, "agent", "risk free", 0, 2, 0, 10),
        _span(0, "agent", "guaranteed returns", 3, 5, 0, 10),
        _span(3, "agent", "guaranteed returns", 0, 2, 30, 40),
    ]


def test_forbidden_absent_is_passed():
    result = _rule_only(
        _forbidden(["سود تضمینی"]), [_t("سلام"), _t("بفرمایید", role="customer")], "fa"
    )
    _confirmed(result, "passed")
    assert result["evidence"] == []
    assert result["scope_turn_indexes"] == [0, 1]


def test_forbidden_absent_with_low_asr_relevant_turn_is_not_computable():
    turns = [_t("سلام", asr=0.3), _t("بفرمایید", role="customer")]
    _not_computable(_rule_only(_forbidden(["سود تضمینی"]), turns, "fa"), "low_asr_confidence")


def test_forbidden_absent_with_low_asr_other_role_turn_is_passed():
    turns = [_t("سلام", asr=0.9), _t("بفرمایید", role="customer", asr=0.3)]
    _confirmed(_rule_only(_forbidden(["سود تضمینی"]), turns, "fa"), "passed")


def test_forbidden_rule_failure_is_confirmed_when_critical():
    result = _rule_only(
        _forbidden(["guaranteed returns"], critical=True),
        [_t("We offer guaranteed returns.")],
        "en",
    )
    _confirmed(result, "failed")
    assert result["critical"] is True


NEGATION_PAIRS = [
    ("en", "I can not offer guaranteed returns.", "I can offer guaranteed returns."),
    ("en", "We never offer guaranteed returns.", "We offer guaranteed returns."),
    ("en", "No guaranteed returns here.", "Yes guaranteed returns here."),
    ("en", "I can't offer guaranteed returns.", "I can offer guaranteed returns."),
    ("en", "I can’t offer guaranteed returns.", "I can offer guaranteed returns."),
    ("en", "We don't promise guaranteed returns.", "We promise guaranteed returns."),
    ("en", "Guaranteed returns? Not here.", "Guaranteed returns? Right here."),
    ("en", "not alpha beta guaranteed returns", "alpha beta guaranteed returns"),
    ("en", "guaranteed returns alpha beta not", "guaranteed returns alpha beta"),
    ("fa", "نه، سود تضمینی داریم", "بله، سود تضمینی داریم"),
    ("fa", "هرگز سود تضمینی نمی‌دهیم", "همیشه سود تضمینی می‌دهیم"),
    ("fa", "نباید سود تضمینی بدهیم", "باید سود تضمینی بدهیم"),
    ("fa", "سود تضمینی در کار نیست", "سود تضمینی در کار هست"),
    ("fa", "سود تضمینی نمی‌دهیم", "سود تضمینی می‌دهیم"),
    ("fa", "سود تضمینی نمیدهیم", "سود تضمینی میدهیم"),
    ("fa", "سود تضمینی نمی دهیم", "سود تضمینی می دهیم"),
]


def _forbidden_phrase_for(language):
    return "guaranteed returns" if language == "en" else "سود تضمینی"


@pytest.mark.parametrize(("language", "deny", "allow"), NEGATION_PAIRS)
def test_forbidden_with_negation_is_not_a_confirmed_violation(language, deny, allow):
    result = _rule_only(_forbidden([_forbidden_phrase_for(language)]), [_t(deny)], language)
    _not_computable(result, "negation", ["negation"])
    assert result["evidence"] == []
    assert len(result["candidate_evidence"]) == 1


@pytest.mark.parametrize(("language", "deny", "allow"), NEGATION_PAIRS)
def test_forbidden_without_negation_is_a_confirmed_violation(language, deny, allow):
    result = _rule_only(_forbidden([_forbidden_phrase_for(language)]), [_t(allow)], language)
    _confirmed(result, "failed")
    assert len(result["evidence"]) == 1


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "not alpha beta gamma guaranteed returns"),
        ("en", "guaranteed returns alpha beta gamma not"),
        ("en", "I know we now offer guaranteed returns."),
        ("fa", "نه الف ب ج سود تضمینی داریم"),
        ("fa", "در نهایت سود تضمینی داریم"),
        ("fa", "نمایش سود تضمینی داریم"),
    ],
)
def test_negation_cue_outside_window_or_not_a_cue_is_clean(language, text):
    _confirmed(
        _rule_only(_forbidden([_forbidden_phrase_for(language)]), [_t(text)], language), "failed"
    )


def test_negation_cue_in_another_turn_is_clean():
    turns = [_t("No.", role="customer"), _t("We offer guaranteed returns.")]
    _confirmed(_rule_only(_forbidden(["guaranteed returns"]), turns, "en"), "failed")


QUOTE_PAIRS = [
    (
        "en",
        'The flyer reads "guaranteed returns" in bold.',
        "The flyer reads guaranteed returns in bold.",
    ),
    (
        "en",
        "The flyer reads “guaranteed returns” in bold.",
        "The flyer reads guaranteed returns in bold.",
    ),
    (
        "en",
        "My manager said we offer guaranteed returns.",
        "My manager and we offer guaranteed returns.",
    ),
    ("en", "He says we offer guaranteed returns.", "He and we offer guaranteed returns."),
    ("en", "She told me we offer guaranteed returns.", "She and I offer guaranteed returns."),
    ("fa", "ما «سود تضمینی» داریم", "ما سود تضمینی داریم"),
    ("fa", 'ما "سود تضمینی" داریم', "ما سود تضمینی داریم"),
    ("fa", "مدیرم گفت سود تضمینی داریم", "مدیرم و ما سود تضمینی داریم"),
    ("fa", "آنها گفتند سود تضمینی داریم", "آنها و ما سود تضمینی داریم"),
    ("fa", "شما گفتید سود تضمینی داریم", "شما و ما سود تضمینی داریم"),
]


@pytest.mark.parametrize(("language", "deny", "allow"), QUOTE_PAIRS)
def test_forbidden_in_quoted_speech_is_not_a_confirmed_violation(language, deny, allow):
    result = _rule_only(_forbidden([_forbidden_phrase_for(language)]), [_t(deny)], language)
    _not_computable(result, "quoted_speech", ["quoted_speech"])


@pytest.mark.parametrize(("language", "deny", "allow"), QUOTE_PAIRS)
def test_forbidden_without_quotes_or_verb_is_a_confirmed_violation(language, deny, allow):
    _confirmed(
        _rule_only(_forbidden([_forbidden_phrase_for(language)]), [_t(allow)], language), "failed"
    )


@pytest.mark.parametrize("language", ["en", "fa"])
def test_forbidden_said_by_other_role_is_ignored(language):
    phrase = _forbidden_phrase_for(language)
    turns = [_t("hello"), _t(phrase, role="customer")]
    result = _rule_only(_forbidden([phrase]), turns, language)
    _confirmed(result, "passed")
    assert result["evidence"] == []
    assert result["candidate_evidence"] == []


@pytest.mark.parametrize("language", ["en", "fa"])
def test_forbidden_said_by_agent_is_a_confirmed_violation(language):
    phrase = _forbidden_phrase_for(language)
    turns = [_t("hello", role="customer"), _t(phrase, role="agent")]
    _confirmed(_rule_only(_forbidden([phrase]), turns, language), "failed")


def test_forbidden_customer_check_ignores_agent_and_flags_customer():
    phrase = "guaranteed returns"
    ignored = _rule_only(
        _forbidden([phrase], speaker_role="customer"), [_t(phrase, role="agent")], "en"
    )
    _confirmed(ignored, "passed")
    flagged = _rule_only(
        _forbidden([phrase], speaker_role="customer"), [_t(phrase, role="customer")], "en"
    )
    _confirmed(flagged, "failed")


def test_forbidden_said_by_customer_with_any_role_is_a_violation():
    turns = [_t("guaranteed returns", role="customer")]
    _confirmed(
        _rule_only(_forbidden(["guaranteed returns"], speaker_role="any"), turns, "en"), "failed"
    )


@pytest.mark.parametrize("language", ["en", "fa"])
def test_forbidden_unknown_speaker_is_not_a_confirmed_violation(language):
    phrase = _forbidden_phrase_for(language)
    result = _rule_only(_forbidden([phrase]), [_t(phrase, role=None)], language)
    _not_computable(result, "speaker_unknown", ["speaker_unknown"])


@pytest.mark.parametrize("language", ["en", "fa"])
def test_forbidden_unknown_speaker_with_any_role_is_a_violation(language):
    phrase = _forbidden_phrase_for(language)
    _confirmed(
        _rule_only(_forbidden([phrase], speaker_role="any"), [_t(phrase, role=None)], language),
        "failed",
    )


@pytest.mark.parametrize("confidence", [0.3, 0.59])
def test_forbidden_in_low_asr_turn_is_not_a_confirmed_violation(confidence):
    result = _rule_only(
        _forbidden(["سود تضمینی"]), [_t("ما سود تضمینی داریم", asr=confidence)], "fa"
    )
    _not_computable(result, "low_asr_confidence", ["low_asr_confidence"])


@pytest.mark.parametrize("confidence", [0.9, None, 0.6])
def test_forbidden_in_confident_turn_is_a_confirmed_violation(confidence):
    result = _rule_only(
        _forbidden(["سود تضمینی"]), [_t("ما سود تضمینی داریم", asr=confidence)], "fa"
    )
    _confirmed(result, "failed")


def test_doubt_codes_on_one_forbidden_match_are_in_fixed_order():
    turns = [_t('He said we do not offer "guaranteed returns"', role=None, asr=0.2)]
    result = _rule_only(_forbidden(["guaranteed returns"]), turns, "en")
    _not_computable(
        result,
        "speaker_unknown",
        ["speaker_unknown", "low_asr_confidence", "quoted_speech", "negation"],
    )


def test_doubt_codes_on_one_required_match_are_in_fixed_order():
    turns = [_t('She said no, "this call is recorded"', role="customer", asr=0.2)]
    result = _rule_only(_required(["this call is recorded"]), turns, "en")
    _not_computable(
        result,
        "wrong_speaker",
        ["wrong_speaker", "low_asr_confidence", "quoted_speech", "negation"],
    )


def test_undecided_reason_is_first_doubt_of_first_candidate_and_reasons_are_the_union():
    turns = [
        _t("We never offer guaranteed returns.", start=0, end=900),
        _t("hello", role="customer"),
        _t("guaranteed returns for you", role=None, asr=0.3, start=2000, end=2900),
    ]
    result = _rule_only(_forbidden(["guaranteed returns"]), turns, "en")
    _not_computable(result, "negation", ["speaker_unknown", "low_asr_confidence", "negation"])
    assert result["evidence"] == []
    assert result["candidate_evidence"] == [
        _span(0, "agent", "guaranteed returns", 3, 5, 0, 900),
        _span(2, None, "guaranteed returns", 0, 2, 2000, 2900),
    ]


# --- 3.7 model fallback windows -------------------------------------------------------------


def _fallback_turns():
    return [
        _t("Hello there.", role="customer", start=0, end=1000),
        _t("Welcome to the fund.", start=1000, end=2000),
        _t("We never offer guaranteed returns.", start=2000, end=3000),
        _t("Okay.", role="customer", start=3000, end=4000),
        _t("Anything else today?", start=4000, end=5000),
        _t("That is all.", role="customer", start=5000, end=6000),
    ]


def test_fallback_request_window_is_candidate_and_neighbours():
    plan = _plan([_forbidden(["guaranteed returns"], model_fallback=True)], _fallback_turns(), "en")
    assert len(plan.requests) == 1
    request = plan.requests[0]
    assert request.check_id == "phrase_check"
    assert request.language == "en"
    assert list(request.turn_indexes) == [1, 2, 3]
    assert [tuple(turn) for turn in request.turns] == [
        (1, "agent", "Welcome to the fund."),
        (2, "agent", "We never offer guaranteed returns."),
        (3, "customer", "Okay."),
    ]
    assert isinstance(request.question, str) and request.question


def test_fallback_windows_merge_sorted_without_duplicates():
    turns = [_t(f"turn {n}") for n in range(6)]
    turns[1] = _t("we never offer guaranteed returns")
    turns[3] = _t("we never offer guaranteed returns")
    plan = _plan([_forbidden(["guaranteed returns"], model_fallback=True)], turns, "en")
    assert list(plan.requests[0].turn_indexes) == [0, 1, 2, 3, 4]


def test_fallback_window_at_call_edges():
    turns = [
        _t("we never offer guaranteed returns"),
        _t("one"),
        _t("two"),
        _t("we never offer guaranteed returns"),
    ]
    plan = _plan([_forbidden(["guaranteed returns"], model_fallback=True)], turns, "en")
    assert list(plan.requests[0].turn_indexes) == [0, 1, 2, 3]
    plan = _plan([_forbidden(["guaranteed returns"], model_fallback=True)], turns[:2], "en")
    assert list(plan.requests[0].turn_indexes) == [0, 1]


def test_fallback_window_stays_inside_scope():
    turns = [_t(f"turn {n}") for n in range(6)]
    turns[2] = _t("we never offer guaranteed returns")
    turns[4] = _t("we never offer guaranteed returns")
    opening = _plan(
        [
            _forbidden(
                ["guaranteed returns"],
                model_fallback=True,
                scope={"kind": "opening", "max_turns": 3},
            )
        ],
        turns,
        "en",
    )
    assert list(opening.requests[0].turn_indexes) == [1, 2]
    closing = _plan(
        [
            _forbidden(
                ["guaranteed returns"],
                model_fallback=True,
                scope={"kind": "closing", "max_turns": 2},
            )
        ],
        turns,
        "en",
    )
    assert list(closing.requests[0].turn_indexes) == [4, 5]


def test_fallback_request_keeps_original_text():
    turns = [_t("سلام"), _t("نه، شركت ما سود تضمينی ندارد", role="customer"), _t("ممنون")]
    plan = _plan([_required(["شرکت ما سود تضمینی"], model_fallback=True)], turns, "fa")
    assert [tuple(turn) for turn in plan.requests[0].turns] == [
        (0, "agent", "سلام"),
        (1, "customer", "نه، شركت ما سود تضمينی ندارد"),
        (2, "agent", "ممنون"),
    ]


def test_fallback_window_of_twelve_makes_a_request():
    turns = [_t("we never offer guaranteed returns") for _ in range(12)]
    plan = _plan([_forbidden(["guaranteed returns"], model_fallback=True)], turns, "en")
    assert len(plan.requests) == 1
    assert list(plan.requests[0].turn_indexes) == list(range(12))


def test_fallback_window_of_thirteen_is_window_too_large():
    turns = [_t("we never offer guaranteed returns") for _ in range(13)]
    plan = _plan([_forbidden(["guaranteed returns"], model_fallback=True)], turns, "en")
    assert tuple(plan.requests) == ()
    result = _finish(plan)["results"][0]
    _not_computable(result, "negation", ["negation", "window_too_large"])
    assert len(result["candidate_evidence"]) == 13


def test_no_fallback_window_of_thirteen_has_no_window_reason():
    turns = [_t("we never offer guaranteed returns") for _ in range(13)]
    result = _rule_only(_forbidden(["guaranteed returns"]), turns, "en")
    _not_computable(result, "negation", ["negation"])


def test_fallback_not_used_when_rule_decides():
    turns = [_t("guaranteed returns", role="customer"), _t("hello")]
    _confirmed(
        _rule_only(_forbidden(["guaranteed returns"], model_fallback=True), turns, "en"), "passed"
    )
    _confirmed(
        _rule_only(
            _forbidden(["guaranteed returns"], model_fallback=True),
            [_t("guaranteed returns")],
            "en",
        ),
        "failed",
    )
    _not_computable(
        _rule_only(_forbidden(["guaranteed returns"], model_fallback=True), [], "en"),
        "no_transcript",
    )


# --- 3.8 model decisions on phrase checks ----------------------------------------------------


def _fallback_plan(kind="forbidden", **extra):
    definition = (_forbidden if kind == "forbidden" else _required)(
        ["guaranteed returns"], model_fallback=True, **extra
    )
    plan = _plan([definition], _fallback_turns(), "en")
    return plan, plan.requests[0].request_id


@pytest.mark.parametrize(
    ("kind", "verdict", "outcome"),
    [
        ("forbidden", "met", "failed"),
        ("forbidden", "not_met", "passed"),
        ("required", "met", "passed"),
        ("required", "not_met", "failed"),
    ],
)
def test_phrase_model_decision_outcome_always_needs_review(kind, verdict, outcome):
    plan, request_id = _fallback_plan(kind)
    result = _finish(plan, {request_id: _answer(verdict, [2], 0.9)}, _evaluator())["results"][0]
    assert result["outcome"] == outcome
    assert result["reason"] is None
    assert result["decided_by"] == "model"
    assert result["review_required"] is True
    assert result["review_reasons"] == ["negation"]
    assert result["evidence"] == [_span(2, "agent", None, None, None, 2000, 3000)]
    assert result["evaluation"] == {
        "request_id": request_id,
        "verdict": verdict,
        "confidence": 0.9,
        "provider": "mock",
        "model": "judge-1",
        "manifest_id": "judge-manifest",
        "manifest_version": 2,
    }


def test_phrase_model_decision_on_critical_check_adds_critical_after_doubts():
    plan, request_id = _fallback_plan(critical=True)
    result = _finish(plan, {request_id: _answer("met", [2])}, _evaluator())["results"][0]
    assert result["outcome"] == "failed"
    assert result["review_reasons"] == ["negation", "critical_check"]


def test_phrase_model_decision_window_turn_reasons_follow_doubts():
    turns = _fallback_turns()
    turns[1] = _t("Welcome to the fund.", role=None)
    turns[3] = _t("Okay.", role="customer", asr=0.3)
    plan = _plan(
        [_forbidden(["guaranteed returns"], model_fallback=True, critical=True)], turns, "en"
    )
    request_id = plan.requests[0].request_id
    result = _finish(plan, {request_id: _answer("met", [2])}, _evaluator())["results"][0]
    assert result["review_reasons"] == [
        "negation",
        "critical_check",
        "speaker_unknown",
        "low_asr_confidence",
    ]


@pytest.mark.parametrize(
    ("answers", "evaluator", "code", "evaluated"),
    [
        ("given", None, "evaluator_unavailable", False),
        ("given", "development", "evaluator_not_qualified", False),
        ("none", "qualified", "evaluator_no_answer", False),
        (_answer("met", []), "qualified", "evaluator_output_invalid", True),
        (_answer("met", [5]), "qualified", "evaluator_output_invalid", True),
        (_answer("abstain", []), "qualified", "evaluator_abstained", True),
        (_answer("met", [2], 0.69), "qualified", "evaluator_low_confidence", True),
        (_answer("met", [2], None), "qualified", "evaluator_low_confidence", True),
    ],
)
def test_phrase_evaluator_failure_keeps_doubt_and_candidates(answers, evaluator, code, evaluated):
    plan, request_id = _fallback_plan()
    if answers == "given":
        answer_map = {request_id: _answer("met", [2])}
    elif answers == "none":
        answer_map = None
    else:
        answer_map = {request_id: answers}
    info = None if evaluator is None else _evaluator(evaluator)
    result = _finish(plan, answer_map, info)["results"][0]
    _not_computable(result, "negation", ["negation", code])
    assert result["evidence"] == []
    assert result["candidate_evidence"] == [
        _span(2, "agent", "guaranteed returns", 3, 5, 2000, 3000)
    ]
    if evaluated:
        assert result["evaluation"]["request_id"] == request_id
        assert set(result["evaluation"]) == EVALUATION_KEYS
    else:
        assert result["evaluation"] is None


# --- 3.7 contextual windows -----------------------------------------------------------------


def _ctx_turns(count=7, trigger_at=(3,), trigger_role="customer"):
    turns = []
    for index in range(count):
        if index in trigger_at:
            turns.append(
                _t(
                    "What is the cancellation fee?",
                    role=trigger_role,
                    start=index * 1000,
                    end=index * 1000 + 900,
                )
            )
        else:
            role = "agent" if index % 2 == 0 else "customer"
            turns.append(
                _t(
                    f"Line {index} about the plan.",
                    role=role,
                    start=index * 1000,
                    end=index * 1000 + 900,
                )
            )
    return turns


def _ctx_request(definition, turns, language="en"):
    plan = _plan([definition], turns, language)
    assert len(plan.requests) == 1
    return plan, plan.requests[0]


@pytest.mark.parametrize(
    ("context_turns", "trigger_at", "window"),
    [
        (1, (3,), [2, 3, 4]),
        (0, (3,), [3]),
        (3, (3,), [0, 1, 2, 3, 4, 5, 6]),
        (1, (1, 5), [0, 1, 2, 4, 5, 6]),
        (2, (0,), [0, 1, 2]),
        (1, (6,), [5, 6]),
    ],
)
def test_contextual_window_with_triggers(context_turns, trigger_at, window):
    definition = _contextual(trigger_phrases=["cancellation fee"], context_turns=context_turns)
    _, request = _ctx_request(definition, _ctx_turns(trigger_at=trigger_at))
    assert list(request.turn_indexes) == window
    assert [index for index, _, _ in (tuple(turn) for turn in request.turns)] == window


def test_contextual_default_context_turns_is_one():
    _, request = _ctx_request(
        _contextual(trigger_phrases=["cancellation fee"]), _ctx_turns(trigger_at=(3,))
    )
    assert list(request.turn_indexes) == [2, 3, 4]


def test_contextual_window_without_triggers_is_every_scope_turn():
    _, request = _ctx_request(_contextual(), _ctx_turns())
    assert list(request.turn_indexes) == list(range(7))
    _, request = _ctx_request(_contextual(scope={"kind": "closing", "max_turns": 3}), _ctx_turns())
    assert list(request.turn_indexes) == [4, 5, 6]


def test_contextual_trigger_window_stays_inside_scope():
    definition = _contextual(
        trigger_phrases=["cancellation fee"], scope={"kind": "closing", "max_turns": 3}
    )
    _, request = _ctx_request(definition, _ctx_turns(trigger_at=(4,)))
    assert list(request.turn_indexes) == [4, 5]


def test_contextual_request_holds_question_and_original_window_text():
    turns = _ctx_turns(trigger_at=(3,))
    turns[2] = _t("We said no fee, never.", role=None)
    _, request = _ctx_request(_contextual(trigger_phrases=["cancellation fee"]), turns)
    assert request.check_id == "context_check"
    assert request.language == "en"
    assert QUESTION in request.question
    assert [tuple(turn) for turn in request.turns] == [
        (2, None, "We said no fee, never."),
        (3, "customer", "What is the cancellation fee?"),
        (4, "agent", "Line 4 about the plan."),
    ]


@pytest.mark.parametrize(
    "trigger_text",
    [
        'I will not charge a "cancellation fee", he said.',
        "CANCELLATION FEE!",
    ],
)
def test_contextual_trigger_ignores_doubts_and_role(trigger_text):
    turns = [_t("hello"), _t(trigger_text, role=None, asr=0.1), _t("bye", role="customer")]
    _, request = _ctx_request(_contextual(trigger_phrases=["cancellation fee"]), turns)
    assert list(request.turn_indexes) == [0, 1, 2]


def test_contextual_persian_trigger_with_arabic_letters():
    turns = [_t("سلام"), _t("هزينه لغو چقدر است؟", role="customer"), _t("توضیح می‌دهم"), _t("ممنون")]
    _, request = _ctx_request(
        _contextual(question=FA_QUESTION, trigger_phrases=["هزینه لغو"], context_turns=0),
        turns,
        "fa",
    )
    assert list(request.turn_indexes) == [1]


@pytest.mark.parametrize(
    ("trigger_phrases", "turns_factory"),
    [
        (["cancellation fee"], lambda: [_t("hello"), _t("Any questions?", role="customer")]),
        (["cancel"], lambda: [_t("hello"), _t("What is the cancellation fee?", role="customer")]),
        (["fee cancellation"], lambda: [_t("What is the cancellation fee?", role="customer")]),
    ],
)
def test_contextual_not_triggered_is_not_applicable(trigger_phrases, turns_factory):
    result = _rule_only(_contextual(trigger_phrases=trigger_phrases), turns_factory(), "en")
    assert result["outcome"] == "not_applicable"
    assert result["reason"] == "not_triggered"
    assert result["decided_by"] is None
    assert result["evaluation"] is None


def test_contextual_trigger_outside_scope_is_not_triggered():
    definition = _contextual(
        trigger_phrases=["cancellation fee"], scope={"kind": "closing", "max_turns": 3}
    )
    result = _rule_only(definition, _ctx_turns(trigger_at=(1,)), "en")
    assert result["outcome"] == "not_applicable"
    assert result["reason"] == "not_triggered"


def test_contextual_no_turns_is_no_transcript():
    result = _rule_only(_contextual(trigger_phrases=["cancellation fee"]), [], "en")
    assert result["outcome"] == "not_computable"
    assert result["reason"] == "no_transcript"
    assert result["decided_by"] is None


def test_contextual_window_of_thirteen_is_window_too_large():
    turns = [_t(f"Line {n}") for n in range(13)]
    result = _rule_only(_contextual(), turns, "en")
    assert result["outcome"] == "not_computable"
    assert result["reason"] == "window_too_large"
    assert result["decided_by"] is None


def test_contextual_window_of_twelve_makes_a_request():
    _, request = _ctx_request(_contextual(), [_t(f"Line {n}") for n in range(12)])
    assert list(request.turn_indexes) == list(range(12))


def test_contextual_scope_limits_window_size():
    turns = [_t(f"Line {n}") for n in range(20)]
    _, request = _ctx_request(_contextual(scope={"kind": "opening", "max_turns": 12}), turns)
    assert list(request.turn_indexes) == list(range(12))


def test_contextual_triggers_spread_too_wide_is_window_too_large():
    turns = _ctx_turns(count=20, trigger_at=(0, 7, 14))
    result = _rule_only(
        _contextual(trigger_phrases=["cancellation fee"], context_turns=3), turns, "en"
    )
    assert result["outcome"] == "not_computable"
    assert result["reason"] == "window_too_large"


# --- 3.8 contextual model evaluation ----------------------------------------------------------


def _ctx_plan(**extra):
    definition = _contextual(trigger_phrases=["cancellation fee"], **extra)
    plan = _plan([definition], _ctx_turns(trigger_at=(3,)), "en")
    return plan, plan.requests[0].request_id


@pytest.mark.parametrize(("verdict", "outcome"), [("met", "passed"), ("not_met", "failed")])
def test_contextual_model_decision_is_confirmed_when_not_critical(verdict, outcome):
    plan, request_id = _ctx_plan()
    result = _finish(plan, {request_id: _answer(verdict, [4, 2], 0.8)}, _evaluator())["results"][0]
    _confirmed(result, outcome, "model")
    assert result["evidence"] == [
        _span(2, "agent", None, None, None, 2000, 2900),
        _span(4, "agent", None, None, None, 4000, 4900),
    ]
    assert result["candidate_evidence"] == []
    assert result["evaluation"] == {
        "request_id": request_id,
        "verdict": verdict,
        "confidence": 0.8,
        "provider": "mock",
        "model": "judge-1",
        "manifest_id": "judge-manifest",
        "manifest_version": 2,
    }


@pytest.mark.parametrize("verdict", ["met", "not_met"])
def test_contextual_model_decision_on_critical_needs_review(verdict):
    plan, request_id = _ctx_plan(critical=True)
    result = _finish(plan, {request_id: _answer(verdict, [3])}, _evaluator())["results"][0]
    assert result["outcome"] == ("passed" if verdict == "met" else "failed")
    assert result["decided_by"] == "model"
    assert result["review_required"] is True
    assert result["review_reasons"] == ["critical_check"]


def test_contextual_window_unknown_speaker_needs_review():
    turns = _ctx_turns(trigger_at=(3,))
    turns[4] = _t("Line 4 about the plan.", role=None)
    plan = _plan([_contextual(trigger_phrases=["cancellation fee"])], turns, "en")
    request_id = plan.requests[0].request_id
    result = _finish(plan, {request_id: _answer("met", [3])}, _evaluator())["results"][0]
    assert result["review_required"] is True
    assert result["review_reasons"] == ["speaker_unknown"]


def test_contextual_window_unknown_speaker_with_any_role_is_confirmed():
    turns = _ctx_turns(trigger_at=(3,))
    turns[4] = _t("Line 4 about the plan.", role=None)
    plan = _plan(
        [_contextual(trigger_phrases=["cancellation fee"], speaker_role="any")], turns, "en"
    )
    request_id = plan.requests[0].request_id
    _confirmed(
        _finish(plan, {request_id: _answer("met", [3])}, _evaluator())["results"][0],
        "passed",
        "model",
    )


def test_contextual_unknown_or_low_turn_outside_window_is_confirmed():
    turns = _ctx_turns(trigger_at=(3,))
    turns[0] = _t("Line 0", role=None, asr=0.1)
    turns[6] = _t("Line 6", asr=0.1)
    plan = _plan([_contextual(trigger_phrases=["cancellation fee"])], turns, "en")
    request_id = plan.requests[0].request_id
    _confirmed(
        _finish(plan, {request_id: _answer("met", [3])}, _evaluator())["results"][0],
        "passed",
        "model",
    )


def test_contextual_window_low_asr_needs_review():
    turns = _ctx_turns(trigger_at=(3,))
    turns[2] = _t("Line 2 about the plan.", asr=0.3)
    plan = _plan([_contextual(trigger_phrases=["cancellation fee"])], turns, "en")
    request_id = plan.requests[0].request_id
    result = _finish(plan, {request_id: _answer("met", [3])}, _evaluator())["results"][0]
    assert result["review_reasons"] == ["low_asr_confidence"]


def test_contextual_review_reasons_order():
    turns = _ctx_turns(trigger_at=(3,))
    turns[2] = _t("Line 2 about the plan.", asr=0.3)
    turns[4] = _t("Line 4 about the plan.", role=None)
    plan = _plan([_contextual(trigger_phrases=["cancellation fee"], critical=True)], turns, "en")
    request_id = plan.requests[0].request_id
    result = _finish(plan, {request_id: _answer("not_met", [3])}, _evaluator())["results"][0]
    assert result["outcome"] == "failed"
    assert result["review_reasons"] == ["critical_check", "speaker_unknown", "low_asr_confidence"]


@pytest.mark.parametrize("status", ["development", "retired"])
def test_contextual_unqualified_evaluator_does_not_decide(status):
    plan, request_id = _ctx_plan()
    result = _finish(plan, {request_id: _answer("met", [3])}, _evaluator(status))["results"][0]
    _not_computable(result, "evaluator_not_qualified")
    assert result["evaluation"] is None
    assert result["evidence"] == []


def test_contextual_qualified_evaluator_decides():
    plan, request_id = _ctx_plan()
    _confirmed(
        _finish(plan, {request_id: _answer("met", [3])}, _evaluator("qualified"))["results"][0],
        "passed",
        "model",
    )


def test_contextual_without_evaluator_is_unavailable():
    plan, request_id = _ctx_plan()
    result = _finish(plan, {request_id: _answer("met", [3])}, None)["results"][0]
    _not_computable(result, "evaluator_unavailable")
    assert result["evaluation"] is None


def test_contextual_unqualified_takes_precedence_over_missing_answer():
    plan, _ = _ctx_plan()
    _not_computable(
        _finish(plan, None, _evaluator("development"))["results"][0], "evaluator_not_qualified"
    )


def test_contextual_missing_answer_is_no_answer():
    plan, _ = _ctx_plan()
    for answers in (None, {}):
        result = _finish(plan, answers, _evaluator())["results"][0]
        _not_computable(result, "evaluator_no_answer")
        assert result["evaluation"] is None


@pytest.mark.parametrize(
    "answer",
    [
        _answer("met", [5]),
        _answer("not_met", [0]),
        _answer("met", [3, 6]),
        _answer("met", []),
        _answer("not_met", []),
        _answer("met", [], 0.1),
    ],
)
def test_contextual_answer_citing_outside_window_or_nothing_is_invalid(answer):
    plan, request_id = _ctx_plan()
    result = _finish(plan, {request_id: answer}, _evaluator())["results"][0]
    _not_computable(result, "evaluator_output_invalid")
    assert result["evaluation"]["request_id"] == request_id
    assert result["evaluation"]["verdict"] == answer["verdict"]
    assert result["evidence"] == []


@pytest.mark.parametrize("cites", [[2], [3], [4], [2, 3, 4]])
def test_contextual_answer_citing_window_turns_decides(cites):
    plan, request_id = _ctx_plan()
    result = _finish(plan, {request_id: _answer("met", cites)}, _evaluator())["results"][0]
    _confirmed(result, "passed", "model")
    assert [span["turn_index"] for span in result["evidence"]] == sorted(cites)


@pytest.mark.parametrize(
    "answer", [_answer("abstain", []), _answer("abstain", [3], None), _answer("abstain", [3], 0.99)]
)
def test_contextual_abstain_is_not_computable(answer):
    plan, request_id = _ctx_plan()
    result = _finish(plan, {request_id: answer}, _evaluator())["results"][0]
    _not_computable(result, "evaluator_abstained")
    assert result["evaluation"]["verdict"] == "abstain"


@pytest.mark.parametrize("confidence", [0.69, 0.0, None])
def test_contextual_low_confidence_is_not_computable(confidence):
    plan, request_id = _ctx_plan()
    result = _finish(plan, {request_id: _answer("met", [3], confidence)}, _evaluator())["results"][
        0
    ]
    _not_computable(result, "evaluator_low_confidence")
    assert result["evaluation"]["confidence"] == confidence


@pytest.mark.parametrize("confidence", [0.7, 1.0])
def test_contextual_confidence_at_threshold_decides(confidence):
    plan, request_id = _ctx_plan()
    result = _finish(plan, {request_id: _answer("met", [3], confidence)}, _evaluator())["results"][
        0
    ]
    _confirmed(result, "passed", "model")


def test_answer_as_model_object_is_accepted():
    cc = _cc()
    plan, request_id = _ctx_plan()
    answer = cc.EvaluationAnswer(verdict="not_met", source_turn_indexes=[3], confidence=0.95)
    _confirmed(_finish(plan, {request_id: answer}, _evaluator())["results"][0], "failed", "model")


def test_evaluation_answer_defaults():
    answer = _cc().EvaluationAnswer(verdict="abstain")
    assert list(answer.source_turn_indexes) == []
    assert answer.confidence is None


# --- 3.4 API: order, ids, determinism, no mutation ------------------------------------------


def _mixed_definitions():
    return [
        _required(["this call is recorded"], check_id="disclosure", critical=True),
        _contextual(check_id="fee_first", trigger_phrases=["cancellation fee"]),
        _forbidden(["guaranteed returns"], check_id="no_guarantee", model_fallback=True),
        _contextual(check_id="fee_second", question="Did the agent offer a refund?"),
    ]


def _mixed_turns():
    return [
        _t("Hello, this call is recorded.", start=0, end=1000, asr=0.95),
        _t("What is the cancellation fee?", role="customer", start=1000, end=2000),
        _t("We never offer guaranteed returns.", start=2000, end=3000),
        _t("Thanks.", role="customer", start=3000, end=4000),
    ]


def test_requests_and_results_follow_definition_order():
    plan = _plan(_mixed_definitions(), _mixed_turns(), "en")
    assert [r.check_id for r in plan.requests] == ["fee_first", "no_guarantee", "fee_second"]
    ids = [r.request_id for r in plan.requests]
    assert len(set(ids)) == 3
    run = _cc().finish_checks(plan)
    assert len(run.results) == 4
    assert [r["check_id"] for r in _doc(run)["results"]] == [
        "disclosure",
        "fee_first",
        "no_guarantee",
        "fee_second",
    ]


def test_plan_is_deterministic():
    first = _plan(_mixed_definitions(), _mixed_turns(), "en")
    second = _plan(_mixed_definitions(), _mixed_turns(), "en")
    assert [r.request_id for r in first.requests] == [r.request_id for r in second.requests]
    assert [list(r.turn_indexes) for r in first.requests] == [
        list(r.turn_indexes) for r in second.requests
    ]
    assert [[tuple(t) for t in r.turns] for r in first.requests] == [
        [tuple(t) for t in r.turns] for r in second.requests
    ]
    assert [r.question for r in first.requests] == [r.question for r in second.requests]


def _mixed_answers(plan):
    return {
        plan.requests[0].request_id: _answer("met", [1], 0.9),
        plan.requests[1].request_id: _answer("met", [2], 0.8),
        plan.requests[2].request_id: _answer("abstain"),
    }


def test_finish_is_deterministic_and_reusable():
    plan = _plan(_mixed_definitions(), _mixed_turns(), "en")
    first = _finish(plan, _mixed_answers(plan), _evaluator())
    second = _finish(plan, _mixed_answers(plan), _evaluator())
    assert first == second
    again = _finish(
        _plan(_mixed_definitions(), _mixed_turns(), "en"), _mixed_answers(plan), _evaluator()
    )
    assert again == first
    outcomes = [
        (r["check_id"], r["outcome"], r["reason"], r["decided_by"]) for r in first["results"]
    ]
    assert outcomes == [
        ("disclosure", "passed", None, "rule"),
        ("fee_first", "passed", None, "model"),
        ("no_guarantee", "failed", None, "model"),
        ("fee_second", "not_computable", "evaluator_abstained", None),
    ]


def test_inputs_are_not_changed():
    definitions = _mixed_definitions()
    turns = _mixed_turns()
    definitions_before = copy.deepcopy(definitions)
    turns_before = [(t.text, t.speaker_role, t.start_ms, t.end_ms, t.asr_confidence) for t in turns]
    plan = _plan(definitions, turns, "en")
    answers = _mixed_answers(plan)
    answers_before = copy.deepcopy(answers)
    request_ids_before = [r.request_id for r in plan.requests]
    _finish(plan, answers, _evaluator())
    assert definitions == definitions_before
    assert [
        (t.text, t.speaker_role, t.start_ms, t.end_ms, t.asr_confidence) for t in turns
    ] == turns_before
    assert answers == answers_before
    assert [r.request_id for r in plan.requests] == request_ids_before


# --- 3.9 JSON shape -------------------------------------------------------------------------


def test_json_shape_matches_the_contract():
    cc = _cc()
    definition = _required(
        ["this call is recorded"], check_id="recording_disclosure", critical=True
    )
    turns = [
        _t("Hello, I am Sara. This call is recorded for quality.", start=0, end=4200),
        _t("Okay.", role="customer", start=4200, end=5000),
        _t("How can I help?", start=5000, end=6000),
    ]
    run = _finish(_plan([definition], turns, "fa"))
    assert set(run) == {"checks_version", "language", "evaluator", "results"}
    assert run["checks_version"] == "conversation-checks/1"
    assert run["language"] == "fa"
    assert run["evaluator"] is None
    [result] = run["results"]
    assert set(result) == RESULT_KEYS
    digest = result.pop("definition_digest")
    assert digest == cc.definition_digest(cc.PhraseCheck.model_validate(definition))
    assert result == {
        "check_id": "recording_disclosure",
        "check_version": 1,
        "kind": "required_phrase",
        "critical": True,
        "outcome": "passed",
        "reason": None,
        "decided_by": "rule",
        "review_required": False,
        "review_reasons": [],
        "evidence": [_span(0, "agent", "this call is recorded", 4, 8, 0, 4200)],
        "candidate_evidence": [],
        "scope_turn_indexes": [0, 1, 2],
        "evaluation": None,
    }


def test_json_has_evaluator_and_keys_on_every_result():
    plan = _plan(_mixed_definitions(), _mixed_turns(), "en")
    run = _finish(plan, _mixed_answers(plan), _evaluator())
    assert run["evaluator"] == {
        "provider": "mock",
        "model": "judge-1",
        "manifest_id": "judge-manifest",
        "manifest_version": 2,
        "manifest_status": "qualified",
    }
    for result in run["results"]:
        assert set(result) == RESULT_KEYS
        assert HEX64.match(result["definition_digest"])
        assert result["check_version"] == 1
        for span in result["evidence"] + result["candidate_evidence"]:
            assert set(span) == SPAN_KEYS
        for key in ("review_reasons", "evidence", "candidate_evidence", "scope_turn_indexes"):
            assert isinstance(result[key], list)
        if result["evaluation"] is not None:
            assert set(result["evaluation"]) == EVALUATION_KEYS
    assert [r["kind"] for r in run["results"]] == [
        "required_phrase",
        "contextual",
        "forbidden_phrase",
        "contextual",
    ]


def test_json_evaluator_null_without_requests_and_present_when_given():
    plan = _plan([_required(["hello"])], [_t("hello")], "en")
    assert _finish(plan)["evaluator"] is None
    assert (
        _finish(plan, None, _evaluator("development"))["evaluator"]["manifest_status"]
        == "development"
    )
