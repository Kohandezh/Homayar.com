"""Plan and finish the checks of one call (HOM-55).

``plan_checks`` decides everything an exact rule can decide and turns the rest into
targeted evaluation requests.  ``finish_checks`` judges the evaluator's answers.  Both are
pure: no database, no network, no model call, no clock, and they never change their
inputs.  See ``README.md`` for every rule.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from backend.conversation_checks.models import (
    CHECKS_VERSION,
    CUE_WINDOW_TOKENS,
    DOUBT_ORDER,
    MAX_WINDOW_TURNS,
    MIN_EVALUATOR_CONFIDENCE,
    QUALIFIED,
    VERDICTS,
    CheckPlan,
    CheckResult,
    CheckRun,
    ChecksError,
    CheckTurn,
    ContextualCriterion,
    DecidedBy,
    Definition,
    Evaluation,
    EvaluationAnswer,
    EvaluationRequest,
    EvaluatorInfo,
    Outcome,
    PhraseCheck,
    PlannedCheck,
    ReasonCode,
    Span,
    answer_values,
    definition_digest,
    parse_definition,
)
from backend.conversation_checks.text import TurnText, analyse_turn, find_matches, fold_tokens
from backend.speech.text_normalize import registered_languages

_Match = tuple[Span, tuple[ReasonCode, ...]]  # a match and its doubts, in DOUBT_ORDER


def plan_checks(
    definitions: Iterable[Definition | Mapping[str, Any]],
    turns: Sequence[CheckTurn | Mapping[str, Any]],
    *,
    language: str = "fa",
) -> CheckPlan:
    """Decide every check the rules can decide; build one request for each of the rest."""
    if not isinstance(language, str) or language not in registered_languages():
        raise ChecksError(
            "language_unsupported",
            f"language must be one of {registered_languages()}, got {language!r}",
        )
    checks = _parse_definitions(definitions)
    call_turns = _parse_turns(turns)
    for check in checks:
        phrases = check.phrases if isinstance(check, PhraseCheck) else check.trigger_phrases
        for phrase in phrases:
            if not fold_tokens(phrase, language):
                raise ChecksError(
                    "check_invalid",
                    f"{check.check_id}: phrase {phrase!r} folds to an empty string in {language}",
                )
    texts = tuple(analyse_turn(turn.text, language) for turn in call_turns)
    planned = tuple(
        _plan_phrase(check, call_turns, texts, language)
        if isinstance(check, PhraseCheck)
        else _plan_contextual(check, call_turns, texts, language)
        for check in checks
    )
    return CheckPlan(CHECKS_VERSION, language, call_turns, planned)


def finish_checks(
    plan: CheckPlan,
    answers: Mapping[str, EvaluationAnswer | Mapping[str, Any]] | None = None,
    *,
    evaluator: EvaluatorInfo | Mapping[str, Any] | None = None,
) -> CheckRun:
    """The final result of every check, in definition order."""
    if not isinstance(plan, CheckPlan):
        raise TypeError("plan must come from plan_checks")
    evaluator_info = _parse_evaluator(evaluator)
    given = _parse_answers(answers, plan)
    results = tuple(
        item.result if item.result is not None else _finish(item, given, evaluator_info, plan.turns)
        for item in plan.planned
    )
    return CheckRun(CHECKS_VERSION, plan.language, evaluator_info, results)


# --- input ---------------------------------------------------------------------------


def _parse_definitions(definitions: object) -> tuple[Definition, ...]:
    if isinstance(definitions, str | bytes | Mapping) or not isinstance(definitions, Iterable):
        raise ChecksError("check_invalid", "definitions must be a list of checks")
    checks = tuple(parse_definition(value) for value in definitions)
    seen: set[str] = set()
    for check in checks:
        if check.check_id in seen:
            raise ChecksError("duplicate_check", f"check_id {check.check_id!r} is given twice")
        seen.add(check.check_id)
    return checks


def _parse_turns(turns: object) -> tuple[CheckTurn, ...]:
    if isinstance(turns, str | bytes | Mapping) or not isinstance(turns, Iterable):
        raise ChecksError("turn_invalid", "turns must be a list of turns")
    parsed = []
    for turn in turns:
        if isinstance(turn, CheckTurn):
            parsed.append(turn)
        elif isinstance(turn, Mapping) and all(isinstance(key, str) for key in turn):
            try:
                parsed.append(CheckTurn(**turn))
            except TypeError as error:
                raise ChecksError("turn_invalid", str(error)) from error
        else:
            raise ChecksError("turn_invalid", "a turn is a CheckTurn or a dict")
    return tuple(parsed)


def _parse_evaluator(evaluator: object) -> EvaluatorInfo | None:
    if evaluator is None or isinstance(evaluator, EvaluatorInfo):
        return evaluator
    if isinstance(evaluator, Mapping) and all(isinstance(key, str) for key in evaluator):
        try:
            return EvaluatorInfo(**evaluator)
        except TypeError as error:
            raise ChecksError("evaluator_invalid", str(error)) from error
    raise ChecksError("evaluator_invalid", "evaluator is an EvaluatorInfo, a dict or None")


def _parse_answers(answers: object, plan: CheckPlan) -> dict[str, object]:
    if answers is None:
        return {}
    if not isinstance(answers, Mapping):
        raise TypeError("answers must be a mapping of request_id to answer")
    known = {request.request_id for request in plan.requests}
    for request_id in answers:
        if request_id not in known:
            raise ChecksError("unknown_request", f"no request {request_id!r} in this plan")
    return dict(answers)


# --- phrase checks -------------------------------------------------------------------


def _plan_phrase(
    check: PhraseCheck, turns: tuple[CheckTurn, ...], texts: tuple[TurnText, ...], language: str
) -> PlannedCheck:
    scope = check.scope.turn_indexes(len(turns))
    base = _base(check, scope)
    if not turns:
        return _final(check, replace(base, reason=ReasonCode.NO_TRANSCRIPT))

    clean: list[Span] = []
    candidates: list[_Match] = []
    for match in _matches(check, turns, texts, scope, language):
        if match[1]:
            candidates.append(match)
        else:
            clean.append(match[0])

    required = check.kind == "required_phrase"
    if clean:
        evidence = (clean[0],) if required else tuple(clean)
        outcome = Outcome.PASSED if required else Outcome.FAILED
        return _final(check, _decided(base, outcome, DecidedBy.RULE, evidence))
    if candidates:
        return _plan_undecided(check, base, candidates, turns, scope, language)
    if any(turns[i].low_asr for i in scope if _relevant(check, turns[i])):
        return _final(check, replace(base, reason=ReasonCode.LOW_ASR_CONFIDENCE))
    outcome = Outcome.FAILED if required else Outcome.PASSED
    return _final(check, _decided(base, outcome, DecidedBy.RULE, ()))


def _matches(
    check: PhraseCheck,
    turns: tuple[CheckTurn, ...],
    texts: tuple[TurnText, ...],
    scope: tuple[int, ...],
    language: str,
) -> list[_Match]:
    """Every match in the scope, by turn then token, with its doubts.

    Two phrases that fold the same give one span, with the first phrase as defined.  For
    a forbidden phrase, a match said by the other known role is dropped.
    """
    phrases = [(phrase, fold_tokens(phrase, language)) for phrase in check.phrases]
    found: list[_Match] = []
    for index in scope:
        turn, text = turns[index], texts[index]
        if check.kind == "forbidden_phrase" and _other_role(check, turn):
            continue
        spans: dict[tuple[int, int], str] = {}
        for phrase, tokens in phrases:
            for start_end in find_matches(tokens, text.tokens):
                spans.setdefault(start_end, phrase)
        for (start, end), phrase in sorted(spans.items()):
            span = Span(index, turn.speaker_role, turn.start_ms, turn.end_ms, phrase, start, end)
            found.append((span, _doubts(check, turn, text, start, end)))
    return found


def _doubts(
    check: PhraseCheck, turn: CheckTurn, text: TurnText, start: int, end: int
) -> tuple[ReasonCode, ...]:
    doubts = []
    if check.speaker_role != "any" and turn.speaker_role is None:
        doubts.append(ReasonCode.SPEAKER_UNKNOWN)
    if check.kind == "required_phrase" and _other_role(check, turn):
        doubts.append(ReasonCode.WRONG_SPEAKER)
    if turn.low_asr:
        doubts.append(ReasonCode.LOW_ASR_CONFIDENCE)
    if text.reporting or any(text.quoted[start:end]):
        doubts.append(ReasonCode.QUOTED_SPEECH)
    before = text.negation[max(start - CUE_WINDOW_TOKENS, 0) : start]
    after = text.negation[end : end + CUE_WINDOW_TOKENS]
    if any(before) or any(after):
        doubts.append(ReasonCode.NEGATION)
    return tuple(doubts)


def _plan_undecided(
    check: PhraseCheck,
    base: CheckResult,
    candidates: list[_Match],
    turns: tuple[CheckTurn, ...],
    scope: tuple[int, ...],
    language: str,
) -> PlannedCheck:
    present = {code for _, doubts in candidates for code in doubts}
    doubts = tuple(code for code in DOUBT_ORDER if code in present)
    undecided = replace(
        base,
        reason=candidates[0][1][0],
        review_required=True,
        review_reasons=doubts,
        candidate_evidence=tuple(span for span, _ in candidates),
    )
    if not check.model_fallback:
        return _final(check, undecided)
    in_scope = set(scope)
    window = sorted(
        {
            neighbour
            for span, _ in candidates
            for neighbour in (span.turn_index - 1, span.turn_index, span.turn_index + 1)
            if neighbour in in_scope
        }
    )
    if len(window) > MAX_WINDOW_TURNS:
        too_large = doubts + (ReasonCode.WINDOW_TOO_LARGE,)
        return _final(check, replace(undecided, review_reasons=too_large))
    said = {span.phrase for span, _ in candidates}
    question = _phrase_question(check, [phrase for phrase in check.phrases if phrase in said])
    request = _request(check, base.definition_digest, question, window, turns, language)
    return PlannedCheck(check, None, request, base=undecided, doubts=doubts)


def _phrase_question(check: PhraseCheck, phrases: list[str]) -> str:
    who = "any speaker" if check.speaker_role == "any" else f"the {check.speaker_role}"
    listed = " or ".join(f'"{phrase}"' for phrase in phrases)
    return (
        f"In these turns, did {who} really make the statement {listed} as their own words? "
        "Answer not_met when the statement is negated, quoted, reported from someone else, "
        "or said by another speaker."
    )


# --- contextual criteria -------------------------------------------------------------


def _plan_contextual(
    check: ContextualCriterion,
    turns: tuple[CheckTurn, ...],
    texts: tuple[TurnText, ...],
    language: str,
) -> PlannedCheck:
    scope = check.scope.turn_indexes(len(turns))
    base = _base(check, scope)
    if not turns:
        return _final(check, replace(base, reason=ReasonCode.NO_TRANSCRIPT))
    if check.trigger_phrases:
        triggers = [fold_tokens(phrase, language) for phrase in check.trigger_phrases]
        hits = [i for i in scope if any(find_matches(t, texts[i].tokens) for t in triggers)]
        if not hits:
            return _final(
                check,
                replace(base, outcome=Outcome.NOT_APPLICABLE, reason=ReasonCode.NOT_TRIGGERED),
            )
        in_scope = set(scope)
        reach = check.context_turns
        window = sorted({j for i in hits for j in range(i - reach, i + reach + 1) if j in in_scope})
    else:
        window = list(scope)
    if len(window) > MAX_WINDOW_TURNS:
        return _final(check, replace(base, reason=ReasonCode.WINDOW_TOO_LARGE))
    request = _request(check, base.definition_digest, check.question, window, turns, language)
    return PlannedCheck(check, None, request, base=base)


# --- requests and answers ------------------------------------------------------------


def _request(
    check: Definition,
    digest: str,
    question: str,
    window: list[int],
    turns: tuple[CheckTurn, ...],
    language: str,
) -> EvaluationRequest:
    """The request id is the check id plus a hash of everything the evaluator is shown, so
    an answer is never reused for another window, question or definition."""
    window_turns = tuple((i, turns[i].speaker_role, turns[i].text) for i in window)
    shown = {
        "checks_version": CHECKS_VERSION,
        "check_id": check.check_id,
        "definition_digest": digest,
        "language": language,
        "question": question,
        "turns": [list(turn) for turn in window_turns],
    }
    canonical = json.dumps(shown, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    request_id = f"{check.check_id}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"
    return EvaluationRequest(
        request_id, check.check_id, question, language, tuple(window), window_turns
    )


def _finish(
    item: PlannedCheck,
    answers: dict[str, object],
    evaluator: EvaluatorInfo | None,
    turns: tuple[CheckTurn, ...],
) -> CheckResult:
    check, request, base = item.definition, item.request, item.base
    assert request is not None and base is not None
    code, evaluation, answer = _judge(request, answers.get(request.request_id), evaluator)
    phrase_check = isinstance(check, PhraseCheck)
    if code is not None:
        if phrase_check:
            # The doubt stays the reason; the evaluator code joins the review reasons.
            return replace(
                base, review_reasons=base.review_reasons + (code,), evaluation=evaluation
            )
        return replace(base, reason=code, evaluation=evaluation)

    assert answer is not None and evaluation is not None
    met = answer.verdict == "met"
    if check.kind == "forbidden_phrase":
        outcome = Outcome.FAILED if met else Outcome.PASSED
    else:
        outcome = Outcome.PASSED if met else Outcome.FAILED
    evidence = tuple(
        Span(i, turns[i].speaker_role, turns[i].start_ms, turns[i].end_ms)
        for i in sorted(set(answer.source_turn_indexes))
    )
    reasons = list(item.doubts)
    window = [turns[i] for i in request.turn_indexes]
    extra = (
        (ReasonCode.CRITICAL_CHECK, check.critical),
        (
            ReasonCode.SPEAKER_UNKNOWN,
            check.speaker_role != "any" and any(t.speaker_role is None for t in window),
        ),
        (ReasonCode.LOW_ASR_CONFIDENCE, any(t.low_asr for t in window)),
    )
    reasons += [code for code, holds in extra if holds and code not in reasons]
    return replace(
        _decided(base, outcome, DecidedBy.MODEL, evidence),
        candidate_evidence=base.candidate_evidence,
        review_required=bool(reasons),
        review_reasons=tuple(reasons),
        evaluation=evaluation,
    )


def _judge(
    request: EvaluationRequest, given: object, evaluator: EvaluatorInfo | None
) -> tuple[ReasonCode | None, Evaluation | None, EvaluationAnswer | None]:
    """The evaluator row that applies (None when the answer decides), the stored
    evaluation (from the output_invalid row on) and the parsed answer."""
    if evaluator is None:
        return ReasonCode.EVALUATOR_UNAVAILABLE, None, None
    if evaluator.manifest_status != QUALIFIED:
        return ReasonCode.EVALUATOR_NOT_QUALIFIED, None, None
    if given is None:
        return ReasonCode.EVALUATOR_NO_ANSWER, None, None

    answer = _parse_answer(given)
    verdict, confidence = answer_values(answer) if answer else (None, None)
    evaluation = Evaluation(
        request.request_id,
        verdict,
        confidence,
        evaluator.provider,
        evaluator.model,
        evaluator.manifest_id,
        evaluator.manifest_version,
    )
    if answer is None or not _well_formed(answer, request):
        return ReasonCode.EVALUATOR_OUTPUT_INVALID, evaluation, None
    if answer.verdict == "abstain":
        return ReasonCode.EVALUATOR_ABSTAINED, evaluation, None
    if answer.confidence is None or answer.confidence < MIN_EVALUATOR_CONFIDENCE:
        return ReasonCode.EVALUATOR_LOW_CONFIDENCE, evaluation, None
    return None, evaluation, answer


def _parse_answer(given: object) -> EvaluationAnswer | None:
    if isinstance(given, EvaluationAnswer):
        return given
    if isinstance(given, Mapping) and all(isinstance(key, str) for key in given):
        try:
            return EvaluationAnswer(**given)
        except TypeError:
            return None
    return None


def _well_formed(answer: EvaluationAnswer, request: EvaluationRequest) -> bool:
    """The output_invalid rules: a known verdict, int citations all inside the window, at
    least one citation for met and not_met, and a confidence from 0 to 1 or None."""
    if answer.verdict not in VERDICTS:
        return False
    cited = answer.source_turn_indexes
    if not isinstance(cited, list | tuple):
        return False
    if any(isinstance(i, bool) or not isinstance(i, int) for i in cited):
        return False
    if answer.verdict != "abstain" and not cited:
        return False
    if any(i not in request.turn_indexes for i in cited):
        return False
    confidence = answer.confidence
    if confidence is None:
        return True
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        return False
    return math.isfinite(confidence) and 0 <= confidence <= 1


# --- helpers -------------------------------------------------------------------------


def _other_role(check: Definition, turn: CheckTurn) -> bool:
    """The turn's role is known and is not the check's role."""
    return (
        check.speaker_role != "any"
        and turn.speaker_role is not None
        and turn.speaker_role != check.speaker_role
    )


def _relevant(check: Definition, turn: CheckTurn) -> bool:
    """A turn whose words count for an absence decision (the turn is already in scope)."""
    return not _other_role(check, turn)


def _base(check: Definition, scope: tuple[int, ...]) -> CheckResult:
    """A not_computable result with no reason yet; every outcome starts from it."""
    return CheckResult(
        check_id=check.check_id,
        check_version=check.version,
        definition_digest=definition_digest(check),
        kind=check.kind,
        critical=check.critical,
        outcome=Outcome.NOT_COMPUTABLE,
        reason=None,
        decided_by=None,
        review_required=False,
        review_reasons=(),
        evidence=(),
        candidate_evidence=(),
        scope_turn_indexes=scope,
        evaluation=None,
    )


def _decided(
    base: CheckResult, outcome: Outcome, decided_by: DecidedBy, evidence: tuple[Span, ...]
) -> CheckResult:
    return replace(
        base,
        outcome=outcome,
        reason=None,
        decided_by=decided_by,
        review_required=False,
        review_reasons=(),
        evidence=evidence,
        candidate_evidence=(),
    )


def _final(check: Definition, result: CheckResult) -> PlannedCheck:
    return PlannedCheck(check, result, None)
