"""Version, limits, codes, check definitions and result types of the conversation checks.

Definitions are frozen pydantic models that refuse unknown keys.  Turns, requests,
answers and results are frozen dataclasses of plain values.  ``to_json`` gives the
JSON-safe shape a later unit stores; nothing here reads a database, a file or the network.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from backend.conversation_checks.text import folds_in_some_language

CHECKS_VERSION = "conversation-checks/1"

# Part of version 1: a new value is a new version.
MIN_ASR_CONFIDENCE = 0.6  # a turn below this is low; None is not low
MIN_EVALUATOR_CONFIDENCE = 0.7  # an answer below this, or without one, is not used
MAX_WINDOW_TURNS = 12  # a larger evaluation window is never sent
CUE_WINDOW_TOKENS = 3  # how far a negation cue may be from a match, each side

# The values of backend.providers.registry.ManifestStatus, kept here as a copy so this
# package does not import providers.  A test keeps the two equal.
MANIFEST_STATUSES = ("qualified", "development", "retired")
QUALIFIED = "qualified"

VERDICTS = ("met", "not_met", "abstain")

_CHECK_ID = re.compile(r"[a-z][a-z0-9_]{1,63}")

SpeakerRole = Literal["agent", "customer"]
CheckRole = Literal["agent", "customer", "any"]


class ChecksError(ValueError):
    """Invalid input.

    ``check_invalid``, ``duplicate_check``, ``language_unsupported`` and
    ``unknown_request`` come from the SPEC; ``turn_invalid`` and ``evaluator_invalid``
    are this package's own, for a bad turn and a bad ``EvaluatorInfo``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Outcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"
    NOT_COMPUTABLE = "not_computable"


class DecidedBy(str, Enum):
    RULE = "rule"
    MODEL = "model"


class ReasonCode(str, Enum):
    """Every ``reason`` and ``review_reasons`` code."""

    NO_TRANSCRIPT = "no_transcript"
    NOT_TRIGGERED = "not_triggered"
    WINDOW_TOO_LARGE = "window_too_large"
    # Doubts of a match (DOUBT_ORDER); low_asr_confidence also blocks an absence decision.
    SPEAKER_UNKNOWN = "speaker_unknown"
    WRONG_SPEAKER = "wrong_speaker"
    LOW_ASR_CONFIDENCE = "low_asr_confidence"
    QUOTED_SPEECH = "quoted_speech"
    NEGATION = "negation"
    # The evaluator rows, in the order finish_checks tries them.
    EVALUATOR_UNAVAILABLE = "evaluator_unavailable"
    EVALUATOR_NOT_QUALIFIED = "evaluator_not_qualified"
    EVALUATOR_NO_ANSWER = "evaluator_no_answer"
    EVALUATOR_OUTPUT_INVALID = "evaluator_output_invalid"
    EVALUATOR_ABSTAINED = "evaluator_abstained"
    EVALUATOR_LOW_CONFIDENCE = "evaluator_low_confidence"
    # Only a review reason of a model decision.
    CRITICAL_CHECK = "critical_check"


DOUBT_ORDER = (
    ReasonCode.SPEAKER_UNKNOWN,
    ReasonCode.WRONG_SPEAKER,
    ReasonCode.LOW_ASR_CONFIDENCE,
    ReasonCode.QUOTED_SPEECH,
    ReasonCode.NEGATION,
)


# --- definitions ---------------------------------------------------------------------


class _Definition(BaseModel):
    """The constructor raises ``ChecksError("check_invalid")``.  Pydantic's own class
    methods (``model_validate``, ``model_validate_json``) still raise its
    ``ValidationError``; both are ``ValueError``, and ``plan_checks`` always raises
    ``ChecksError``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as error:
            raise ChecksError("check_invalid", _first_error(error)) from error


def _first_error(error: ValidationError) -> str:
    first = error.errors()[0]
    where = ".".join(str(part) for part in first["loc"]) or "definition"
    return f"{where}: {first['msg']}"


class Scope(_Definition):
    """Which turns a check searches: the whole call, the first n or the last n turns."""

    kind: Literal["whole_call", "opening", "closing"] = "whole_call"
    max_turns: StrictInt | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Scope:
        if self.kind == "whole_call":
            if self.max_turns is not None:
                raise ValueError("a whole_call scope has no max_turns")
        elif self.max_turns is None or self.max_turns < 1:
            raise ValueError(f"an {self.kind} scope needs max_turns >= 1")
        return self

    def turn_indexes(self, turn_count: int) -> tuple[int, ...]:
        if self.kind == "whole_call" or self.max_turns is None:
            return tuple(range(turn_count))
        if self.kind == "opening":
            return tuple(range(min(self.max_turns, turn_count)))
        return tuple(range(max(turn_count - self.max_turns, 0), turn_count))


class _Check(_Definition):
    check_id: StrictStr
    version: StrictInt = Field(ge=1)
    critical: StrictBool = False
    speaker_role: CheckRole = "agent"
    scope: Scope = Scope()

    @field_validator("check_id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _CHECK_ID.fullmatch(value):
            raise ValueError(f"check_id must match ^{_CHECK_ID.pattern}$")
        return value


def _foldable(phrases: tuple[str, ...]) -> tuple[str, ...]:
    for phrase in phrases:
        if not folds_in_some_language(phrase):
            raise ValueError(f"phrase {phrase!r} folds to an empty string")
    return phrases


class PhraseCheck(_Check):
    """A phrase that must be said (``required_phrase``) or must not be (``forbidden_phrase``)."""

    kind: Literal["required_phrase", "forbidden_phrase"]
    phrases: tuple[StrictStr, ...] = Field(min_length=1, max_length=32)  # any-of
    model_fallback: StrictBool = False  # an undecided case becomes an evaluation request

    @field_validator("phrases")
    @classmethod
    def _phrases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _foldable(value)


class ContextualCriterion(_Check):
    """A criterion the evaluator judges on a targeted window of turns."""

    kind: Literal["contextual"]
    question: StrictStr
    trigger_phrases: tuple[StrictStr, ...] = Field(default=(), max_length=32)
    context_turns: StrictInt = Field(default=1, ge=0, le=3)

    @field_validator("question")
    @classmethod
    def _question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be empty")
        return value

    @field_validator("trigger_phrases")
    @classmethod
    def _triggers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _foldable(value)


Definition = PhraseCheck | ContextualCriterion


def parse_definition(value: object) -> Definition:
    """A definition validated again, or a plain dict validated by its ``kind``.

    A model instance is validated again, because ``model_copy(update=...)`` and
    ``model_construct`` skip validation.
    """
    if isinstance(value, PhraseCheck | ContextualCriterion):
        return type(value)(**value.model_dump(warnings=False))
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ChecksError("check_invalid", "a definition is a check model or a dict")
    kind = value.get("kind")
    if kind in ("required_phrase", "forbidden_phrase"):
        return PhraseCheck(**value)
    if kind == "contextual":
        return ContextualCriterion(**value)
    raise ChecksError("check_invalid", f"unknown or missing kind {kind!r}")


def definition_digest(definition: Definition | Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of the definition (sorted keys, no spaces, UTF-8)."""
    model = parse_definition(definition)
    canonical = json.dumps(
        model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- turns and the evaluator ---------------------------------------------------------


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


@dataclass(frozen=True)
class CheckTurn:
    """One turn of the call.  Its index is its position in the list given."""

    text: str
    speaker_role: SpeakerRole | None = None  # None: the speaker is not known
    start_ms: int | None = None
    end_ms: int | None = None
    asr_confidence: float | None = None

    def __post_init__(self) -> None:
        problem = self._problem()
        if problem:
            raise ChecksError("turn_invalid", problem)

    def _problem(self) -> str | None:
        if not isinstance(self.text, str):
            return "text must be a str"
        if self.speaker_role not in ("agent", "customer", None):
            return "speaker_role must be agent, customer or None"
        for name in ("start_ms", "end_ms"):
            value = getattr(self, name)
            if value is not None and (not _is_int(value) or value < 0):
                return f"{name} must be None or an int >= 0"
        if self.start_ms is not None and self.end_ms is not None and self.end_ms < self.start_ms:
            return "end_ms must not be before start_ms"
        confidence = self.asr_confidence
        # A NaN fails the range test too, so it is refused.
        if confidence is not None and (not _is_number(confidence) or not 0 <= confidence <= 1):
            return "asr_confidence must be None or a number from 0 to 1"
        return None

    @property
    def low_asr(self) -> bool:
        return self.asr_confidence is not None and self.asr_confidence < MIN_ASR_CONFIDENCE


@dataclass(frozen=True)
class EvaluatorInfo:
    """The model that answered the requests, and its manifest status in the registry."""

    provider: str
    model: str
    manifest_id: str | None
    manifest_version: int | None
    manifest_status: str  # one of MANIFEST_STATUSES

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not isinstance(self.model, str):
            raise ChecksError("evaluator_invalid", "provider and model must be str")
        if self.manifest_id is not None and not isinstance(self.manifest_id, str):
            raise ChecksError("evaluator_invalid", "manifest_id must be None or a str")
        if self.manifest_version is not None and not _is_int(self.manifest_version):
            raise ChecksError("evaluator_invalid", "manifest_version must be None or an int")
        if self.manifest_status not in MANIFEST_STATUSES:
            raise ChecksError("evaluator_invalid", f"manifest_status must be {MANIFEST_STATUSES}")
        # A ManifestStatus member is stored as its plain string.
        status = MANIFEST_STATUSES[MANIFEST_STATUSES.index(self.manifest_status)]
        object.__setattr__(self, "manifest_status", status)

    def to_json(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "manifest_id": self.manifest_id,
            "manifest_version": self.manifest_version,
            "manifest_status": self.manifest_status,
        }


# --- evaluation ----------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationRequest:
    """One targeted question.  ``turns`` holds exactly the window, as
    ``(index, speaker_role, text)`` with the original text."""

    request_id: str
    check_id: str
    question: str
    language: str
    turn_indexes: tuple[int, ...]
    turns: tuple[tuple[int, SpeakerRole | None, str], ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "check_id": self.check_id,
            "question": self.question,
            "language": self.language,
            "turn_indexes": list(self.turn_indexes),
            "turns": [
                {"index": index, "speaker_role": role, "text": text}
                for index, role, text in self.turns
            ],
        }


@dataclass(frozen=True)
class EvaluationAnswer:
    """The evaluator's answer to one request.

    The constructor checks nothing: an answer is model output, so ``finish_checks`` judges
    it and a bad one becomes ``evaluator_output_invalid``, never an exception.
    """

    verdict: str  # met, not_met or abstain
    source_turn_indexes: tuple[int, ...] | list[int] = ()
    confidence: float | None = None


@dataclass(frozen=True)
class Evaluation:
    """The answer that was judged, and who gave it."""

    request_id: str
    verdict: str | None  # None when the answer had no valid verdict
    confidence: float | None  # None when the answer had no finite number
    provider: str
    model: str
    manifest_id: str | None
    manifest_version: int | None

    def to_json(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "provider": self.provider,
            "model": self.model,
            "manifest_id": self.manifest_id,
            "manifest_version": self.manifest_version,
        }


def answer_values(answer: EvaluationAnswer) -> tuple[str | None, float | None]:
    """The verdict and confidence of ``answer`` that may be stored, else None."""
    verdict = answer.verdict if answer.verdict in VERDICTS else None
    confidence = answer.confidence
    if not _is_number(confidence) or not math.isfinite(confidence):  # type: ignore[arg-type]
        confidence = None
    return verdict, confidence


# --- results -------------------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    """Evidence in one turn.  A rule match has ``phrase`` and a half-open token range into
    ``normalize_for_matching(turn.text, language).split(" ")``; a model citation has
    neither."""

    turn_index: int
    speaker_role: SpeakerRole | None
    start_ms: int | None
    end_ms: int | None
    phrase: str | None = None
    token_start: int | None = None
    token_end: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "turn_index": self.turn_index,
            "speaker_role": self.speaker_role,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "phrase": self.phrase,
            "token_start": self.token_start,
            "token_end": self.token_end,
        }


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    check_version: int
    definition_digest: str
    kind: str
    critical: bool
    outcome: Outcome
    reason: ReasonCode | None  # None for passed and failed
    decided_by: DecidedBy | None  # None unless passed or failed
    review_required: bool
    review_reasons: tuple[ReasonCode, ...]
    evidence: tuple[Span, ...]
    candidate_evidence: tuple[Span, ...]
    scope_turn_indexes: tuple[int, ...]
    evaluation: Evaluation | None

    def to_json(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "check_version": self.check_version,
            "definition_digest": self.definition_digest,
            "kind": self.kind,
            "critical": self.critical,
            "outcome": self.outcome.value,
            "reason": self.reason.value if self.reason else None,
            "decided_by": self.decided_by.value if self.decided_by else None,
            "review_required": self.review_required,
            "review_reasons": [code.value for code in self.review_reasons],
            "evidence": [span.to_json() for span in self.evidence],
            "candidate_evidence": [span.to_json() for span in self.candidate_evidence],
            "scope_turn_indexes": list(self.scope_turn_indexes),
            "evaluation": self.evaluation.to_json() if self.evaluation else None,
        }


@dataclass(frozen=True)
class PlannedCheck:
    """One definition after ``plan_checks``: either a final result, or a request that
    ``finish_checks`` decides."""

    definition: Definition
    result: CheckResult | None  # final, no request
    request: EvaluationRequest | None
    # Only with a request: the result fields that stay, and for a phrase check its doubts.
    base: CheckResult | None = None
    doubts: tuple[ReasonCode, ...] = ()


@dataclass(frozen=True)
class CheckPlan:
    checks_version: str
    language: str
    turns: tuple[CheckTurn, ...]
    planned: tuple[PlannedCheck, ...] = field(repr=False)

    @property
    def requests(self) -> tuple[EvaluationRequest, ...]:
        """The evaluation requests, in definition order."""
        return tuple(item.request for item in self.planned if item.request is not None)


@dataclass(frozen=True)
class CheckRun:
    checks_version: str
    language: str
    evaluator: EvaluatorInfo | None
    results: tuple[CheckResult, ...]  # one per definition, in definition order

    def to_json(self) -> dict[str, Any]:
        return {
            "checks_version": self.checks_version,
            "language": self.language,
            "evaluator": self.evaluator.to_json() if self.evaluator else None,
            "results": [result.to_json() for result in self.results],
        }
