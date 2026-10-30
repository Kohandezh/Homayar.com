"""Version, codes, the scorecard definition, conversation facts and result types (HOM-54).

Definitions are frozen pydantic models that refuse unknown keys.  Conversation facts and
results are frozen dataclasses of plain values.  ``to_json`` gives the JSON-safe shape a
later unit stores (HOM-56); nothing here reads a database, a file or the network.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar

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
from pydantic_core import PydanticCustomError

from backend.conversation_checks import ContextualCriterion, PhraseCheck
from backend.conversation_checks.models import parse_definition

SCORECARDS_VERSION = "scorecards/1"

# The values of ConversationType and ConversationDirection in
# backend.database.saas_models (HOM-58), kept here as a copy so this package does not
# import the database.  A test keeps the copies equal to the enums.
CONVERSATION_TYPES = ("call", "meeting", "voicemail", "other")
CONVERSATION_DIRECTIONS = ("inbound", "outbound", "internal")

# A criterion id, and the scorecard key of the API (HOM-54 PR 1 checks it in the table).
_KEY = re.compile(r"[a-z][a-z0-9_]{1,63}")
KEY_PATTERN = f"^{_KEY.pattern}$"

REVIEWER_OUTCOMES = ("passed", "failed", "not_applicable")


def valid_key(value: object) -> bool:
    """True when ``value`` matches ``KEY_PATTERN``."""
    return isinstance(value, str) and _KEY.fullmatch(value) is not None


class ScorecardError(ValueError):
    """Invalid input.  ``code`` is one of the codes in the README."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ScorecardOutcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ScorecardReason(str, Enum):
    """The reason codes this package gives.  A criterion can also carry the reason of its
    check (``conversation_checks.ReasonCode``)."""

    # criterion reasons
    NOT_APPLICABLE_BY_CONVERSATION = "not_applicable_by_conversation"
    REVIEWER_NOT_APPLICABLE = "reviewer_not_applicable"
    DIRECTION_UNKNOWN = "direction_unknown"
    REVIEW_REQUIRED = "review_required"
    NA_NOT_ALLOWED = "na_not_allowed"
    # scorecard reasons
    NO_APPLICABLE_CRITERIA = "no_applicable_criteria"
    AUTO_FAIL = "auto_fail"
    EVIDENCE_MISSING = "evidence_missing"
    BELOW_THRESHOLD = "below_threshold"


# --- definitions ---------------------------------------------------------------------

# Rules with their own code.  Any other broken rule is ``scorecard_invalid`` at the top
# level and ``criterion_invalid`` inside a criterion.
_RULE_CODES = frozenset(
    {
        "duplicate_criterion",
        "weights_invalid",
        "weight_zero_not_auto_fail",
        "check_invalid",
        "check_id_mismatch",
        "na_not_allowed",
    }
)


def _rule(code: str, message: str) -> PydanticCustomError:
    return PydanticCustomError(code, "{message}", {"message": message})


class _Definition(BaseModel):
    """The constructor raises ``ScorecardError``.  Pydantic's own class methods
    (``model_validate``, ``model_validate_json``) still raise its ``ValidationError``;
    both are ``ValueError``, and ``validate_definition`` always raises ``ScorecardError``.
    A nested model instance is validated again (``revalidate_instances``)."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")
    _top_level: ClassVar[bool] = False

    def __init__(self, /, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as error:
            raise _error(error, top_level=self._top_level) from error


def _error(error: ValidationError, *, top_level: bool) -> ScorecardError:
    first = error.errors()[0]
    where = ".".join(str(part) for part in first["loc"])
    nested = first.get("ctx", {}).get("error")
    if isinstance(nested, ScorecardError):
        # Pydantic builds a nested model with its own constructor, so a criterion or an
        # applies_to inside a definition already raised its code.
        return ScorecardError(nested.code, f"{where}: {nested}")
    message = f"{where}: {first['msg']}" if where else first["msg"]
    if first["type"] in _RULE_CODES:
        return ScorecardError(first["type"], message)
    in_criterion = len(first["loc"]) >= 2 and first["loc"][0] == "criteria"
    if top_level and not in_criterion:
        return ScorecardError("scorecard_invalid", message)
    return ScorecardError("criterion_invalid", message)


def _text(value: str, name: str, limit: int) -> str:
    if not 1 <= len(value.strip()) <= limit:
        raise ValueError(f"{name} must have 1 to {limit} characters after strip()")
    return value


def _value_set(value: object, allowed: tuple[str, ...], name: str) -> tuple[str, ...] | None:
    """``None``, or a non-empty list of distinct ``allowed`` values as plain strings.  An
    enum member is read as its value."""
    if value is None:
        return None
    if not isinstance(value, list | tuple) or not value:
        raise ValueError(f"{name} must be null or a non-empty list")
    items = []
    for item in value:
        item = item.value if isinstance(item, Enum) else item
        if not isinstance(item, str) or item not in allowed:
            raise ValueError(f"{name}: {item!r} is not one of {allowed}")
        items.append(allowed[allowed.index(item)])
    if len(set(items)) != len(items):
        raise ValueError(f"{name} must not repeat a value")
    return tuple(items)


class Applicability(_Definition):
    """Which conversations a criterion applies to.  ``None`` in a field means every value."""

    conversation_types: tuple[str, ...] | None = None
    directions: tuple[str, ...] | None = None

    @field_validator("conversation_types", mode="before")
    @classmethod
    def _types(cls, value: object) -> tuple[str, ...] | None:
        return _value_set(value, CONVERSATION_TYPES, "conversation_types")

    @field_validator("directions", mode="before")
    @classmethod
    def _directions(cls, value: object) -> tuple[str, ...] | None:
        return _value_set(value, CONVERSATION_DIRECTIONS, "directions")

    @model_validator(mode="after")
    def _not_empty(self) -> Applicability:
        if self.conversation_types is None and self.directions is None:
            raise ValueError("give conversation_types or directions; for every conversation "
                             "use applies_to null")  # fmt: skip
        return self


class Criterion(_Definition):
    """One weighted criterion.  It wraps exactly one conversation check."""

    criterion_id: StrictStr
    label: StrictStr
    weight: StrictInt = Field(ge=0, le=100)
    auto_fail: StrictBool = False  # a confirmed failure fails the whole scorecard
    na_allowed: StrictBool = False  # the criterion may be not applicable (approved NA)
    applies_to: Applicability | None = None  # None: every conversation
    check: PhraseCheck | ContextualCriterion

    @field_validator("criterion_id")
    @classmethod
    def _criterion_id(cls, value: str) -> str:
        if not valid_key(value):
            raise ValueError(f"criterion_id must match {KEY_PATTERN}")
        return value

    @field_validator("label")
    @classmethod
    def _label(cls, value: str) -> str:
        return _text(value, "label", 200)

    @field_validator("check", mode="before")
    @classmethod
    def _check(cls, value: object) -> PhraseCheck | ContextualCriterion:
        # A check model is validated again; a dict is validated by its kind.
        try:
            return parse_definition(value)
        except ValueError as error:
            raise _rule("check_invalid", str(error)) from None

    @model_validator(mode="after")
    def _rules(self) -> Criterion:
        if self.check.check_id != self.criterion_id:
            raise _rule(
                "check_id_mismatch",
                f"check_id {self.check.check_id!r} is not criterion_id {self.criterion_id!r}",
            )
        if self.weight == 0 and not self.auto_fail:
            raise _rule("weight_zero_not_auto_fail", "a criterion with weight 0 must auto_fail")
        if self.check.kind == "contextual" and self.check.trigger_phrases and not self.na_allowed:
            raise _rule(
                "na_not_allowed",
                "a contextual check with trigger_phrases can be not applicable: set na_allowed",
            )
        return self


class ScorecardDefinition(_Definition):
    """A scorecard: criteria with weights that add up to 100, and a pass threshold."""

    _top_level: ClassVar[bool] = True

    name: StrictStr
    description: StrictStr | None = Field(default=None, max_length=2000)
    pass_threshold: StrictInt = Field(ge=1, le=100)  # a percent
    criteria: tuple[Criterion, ...] = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _text(value, "name", 200)

    @field_validator("criteria", mode="before")
    @classmethod
    def _criteria_list(cls, value: object) -> object:
        if not isinstance(value, list | tuple):
            raise ValueError("criteria must be a list")
        return value

    @model_validator(mode="after")
    def _rules(self) -> ScorecardDefinition:
        seen: set[str] = set()
        for item in self.criteria:
            if item.criterion_id in seen:
                raise _rule(
                    "duplicate_criterion", f"criterion_id {item.criterion_id!r} is given twice"
                )
            seen.add(item.criterion_id)
        total = sum(item.weight for item in self.criteria)
        if total != 100:
            raise _rule("weights_invalid", f"the weights add up to {total}, not 100")
        return self


def validate_definition(value: object) -> ScorecardDefinition:
    """A definition validated again, or a plain dict validated.

    A model instance is validated again, because ``model_copy(update=...)`` and
    ``model_construct`` skip validation.
    """
    if isinstance(value, ScorecardDefinition):
        try:
            data = value.model_dump(warnings=False)
        except (AttributeError, TypeError, ValueError) as error:  # a model_construct copy
            raise ScorecardError("scorecard_invalid", str(error)) from error
        return ScorecardDefinition(**data)
    if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        return ScorecardDefinition(**value)
    raise ScorecardError("scorecard_invalid", "a definition is a ScorecardDefinition or a dict")


def definition_digest(definition: ScorecardDefinition | Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of the definition (sorted keys, no spaces, UTF-8)."""
    model = validate_definition(definition)
    canonical = json.dumps(
        model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- the conversation ----------------------------------------------------------------


def _fact(value: object, allowed: tuple[str, ...], name: str) -> str:
    value = value.value if isinstance(value, Enum) else value
    if not isinstance(value, str) or value not in allowed:
        raise ScorecardError("conversation_invalid", f"{name} must be one of {allowed}")
    return allowed[allowed.index(value)]


@dataclass(frozen=True)
class ConversationFacts:
    """What applicability reads from a conversation (the HOM-58 columns of ``meetings``)."""

    conversation_type: str
    direction: str | None  # None: the direction is not known

    def __post_init__(self) -> None:
        # An enum member is stored as its plain string.
        kind = _fact(self.conversation_type, CONVERSATION_TYPES, "conversation_type")
        object.__setattr__(self, "conversation_type", kind)
        if self.direction is not None:
            direction = _fact(self.direction, CONVERSATION_DIRECTIONS, "direction")
            object.__setattr__(self, "direction", direction)

    def to_json(self) -> dict[str, Any]:
        return {"conversation_type": self.conversation_type, "direction": self.direction}


# --- results -------------------------------------------------------------------------


@dataclass(frozen=True)
class CriterionResult:
    criterion_id: str
    label: str
    weight: int
    auto_fail: bool
    na_allowed: bool
    outcome: str  # a ScorecardOutcome value
    reason: str | None
    decided_by: str | None  # rule, model, reviewer or None
    review_required: bool
    review_reasons: tuple[str, ...]
    # Copied from the check result; None, None, (), () when no check ran.
    check_outcome: str | None
    check_reason: str | None
    evidence: tuple[dict[str, Any], ...]
    candidate_evidence: tuple[dict[str, Any], ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "label": self.label,
            "weight": self.weight,
            "auto_fail": self.auto_fail,
            "na_allowed": self.na_allowed,
            "outcome": self.outcome,
            "reason": self.reason,
            "decided_by": self.decided_by,
            "review_required": self.review_required,
            "review_reasons": list(self.review_reasons),
            "check_outcome": self.check_outcome,
            "check_reason": self.check_reason,
            "evidence": copy.deepcopy(list(self.evidence)),
            "candidate_evidence": copy.deepcopy(list(self.candidate_evidence)),
        }


@dataclass(frozen=True)
class ScorecardResult:
    scorecards_version: str
    checks_version: str
    definition_digest: str
    conversation: ConversationFacts
    outcome: str  # a ScorecardOutcome value
    reason: str | None
    complete: bool  # no criterion is insufficient_evidence
    pass_threshold: int
    score: float | None  # score_min when complete, else None
    score_min: float | None
    score_max: float | None
    applicable_weight: int
    earned_weight: int
    failed_weight: int
    insufficient_weight: int
    criteria: tuple[CriterionResult, ...]  # one per criterion, in definition order

    def to_json(self) -> dict[str, Any]:
        return {
            "scorecards_version": self.scorecards_version,
            "checks_version": self.checks_version,
            "definition_digest": self.definition_digest,
            "conversation": self.conversation.to_json(),
            "outcome": self.outcome,
            "reason": self.reason,
            "complete": self.complete,
            "pass_threshold": self.pass_threshold,
            "score": self.score,
            "score_min": self.score_min,
            "score_max": self.score_max,
            "applicable_weight": self.applicable_weight,
            "earned_weight": self.earned_weight,
            "failed_weight": self.failed_weight,
            "insufficient_weight": self.insufficient_weight,
            "criteria": [item.to_json() for item in self.criteria],
        }
