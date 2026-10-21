"""Confidence, abstention and critical-value review policy of an extraction (HOM-53).

A pure function over the validator's result: no database, no network, no model, no clock.
For every accepted stated value it decides one of three things: auto-accepted, withheld
for low confidence, or routed to human review.  A field whose values are all withheld
abstains.  It never changes its input and never turns an invalid extraction into a valid
one, nor the reverse.

``confidence`` is the model's own number, not a calibrated probability, so the version 1
thresholds are conservative placeholders.  Precision is not measured yet, so version 1
never auto-accepts a critical value.  Calibration publishes a new policy version; a stored
result always names the version and digest that produced it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum

from backend.analysis_taxonomy.extraction import Extraction
from backend.analysis_taxonomy.models import Basis, FieldStatus, Taxonomy
from backend.analysis_taxonomy.validator import (
    AcceptedValue,
    ExtractionValidation,
    FieldValidation,
    Issue,
    ReasonCode,
    Verdict,
    validate_extraction,
)

POLICY_VERSION = "extraction-policy/1"


@dataclass(frozen=True)
class ExtractionPolicy:
    version: str
    min_confidence_explicit: float  # threshold for a value with basis explicit
    min_confidence_inferred: float  # threshold for a value with basis inferred


# Placeholders until calibration: a new threshold is a new version.
POLICY_V1 = ExtractionPolicy(
    version=POLICY_VERSION, min_confidence_explicit=0.5, min_confidence_inferred=0.7
)


def policy_digest(policy: ExtractionPolicy) -> str:
    """sha256 of the canonical JSON of the policy, the same canonical form as the taxonomy."""
    canonical = json.dumps(
        asdict(policy), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Disposition(str, Enum):
    AUTO_ACCEPTED = "auto_accepted"
    REVIEW_REQUIRED = "review_required"  # a person confirms it (HOM-18)


class WithheldReason(str, Enum):
    BELOW_THRESHOLD = "below_threshold"
    MISSING_CONFIDENCE = "missing_confidence"


class AbstainReason(str, Enum):
    MODEL = "model"  # the extraction itself gave an abstain value
    CONTRADICTORY = "contradictory"  # the validator found incompatible values
    LOW_CONFIDENCE = "low_confidence"  # the policy withheld every value


class ReviewReason(str, Enum):
    CRITICAL_LABEL = "critical_label"


@dataclass(frozen=True)
class PolicyValue:
    """An accepted value the policy keeps, with what happens to it."""

    label: str
    basis: Basis
    source_turn_indexes: tuple[int, ...]
    confidence: float | None
    critical: bool
    text: str | None
    disposition: Disposition
    review_reason: ReviewReason | None


@dataclass(frozen=True)
class WithheldValue:
    """An accepted value the policy does not show; kept only so evaluation can count it."""

    label: str
    basis: Basis
    source_turn_indexes: tuple[int, ...]
    confidence: float | None
    critical: bool
    text: str | None
    reason: WithheldReason


@dataclass(frozen=True)
class PolicyField:
    dimension: str
    verdict: Verdict
    status: FieldStatus | None  # None when rejected
    abstain_reason: AbstainReason | None
    values: tuple[PolicyValue, ...]  # input order
    withheld: tuple[WithheldValue, ...]  # input order
    issues: tuple[Issue, ...]  # the validator's field issues

    @property
    def review_required(self) -> bool:
        return any(v.disposition is Disposition.REVIEW_REQUIRED for v in self.values)


@dataclass(frozen=True)
class PolicyResult:
    policy_version: str
    policy_digest: str
    taxonomy_id: str
    taxonomy_version: int
    taxonomy_digest: str
    validator_version: str
    valid: bool  # always the validator's
    issues: tuple[Issue, ...]  # extraction-level, from the validator
    fields: tuple[PolicyField, ...]  # always the four dimensions, in DIMENSIONS order

    def field(self, dimension: str) -> PolicyField:
        for field in self.fields:
            if field.dimension == dimension:
                return field
        raise KeyError(dimension)

    @property
    def needs_review(self) -> bool:
        return any(field.review_required for field in self.fields)

    def to_json(self) -> dict[str, object]:
        """The stored form: plain JSON, enum values as strings, no issue messages."""
        return {
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "taxonomy_id": self.taxonomy_id,
            "taxonomy_version": self.taxonomy_version,
            "taxonomy_digest": self.taxonomy_digest,
            "validator_version": self.validator_version,
            "valid": self.valid,
            "needs_review": self.needs_review,
            "issues": [_issue_json(issue) for issue in self.issues],
            "fields": {field.dimension: _field_json(field) for field in self.fields},
        }


def apply_policy(
    validation: ExtractionValidation, policy: ExtractionPolicy = POLICY_V1
) -> PolicyResult:
    """Decide, per field and per value, what ``validation`` may show and what needs review."""
    _check_policy(policy)
    return PolicyResult(
        policy_version=policy.version,
        policy_digest=policy_digest(policy),
        taxonomy_id=validation.taxonomy_id,
        taxonomy_version=validation.taxonomy_version,
        taxonomy_digest=validation.taxonomy_digest,
        validator_version=validation.validator_version,
        valid=validation.valid,
        issues=validation.issues,
        fields=tuple(_apply_field(field, policy) for field in validation.fields),
    )


def evaluate_extraction(
    extraction: Mapping[str, object] | Extraction,
    taxonomy: Taxonomy,
    turns: Sequence[str],
    policy: ExtractionPolicy = POLICY_V1,
) -> PolicyResult:
    """Exactly ``apply_policy(validate_extraction(extraction, taxonomy, turns), policy)``."""
    return apply_policy(validate_extraction(extraction, taxonomy, turns), policy)


def _check_policy(policy: ExtractionPolicy) -> None:
    if not isinstance(policy.version, str) or not policy.version.strip():
        raise ValueError("an extraction policy needs a version")
    for name in ("min_confidence_explicit", "min_confidence_inferred"):
        threshold = getattr(policy, name)
        # A NaN fails the comparison too, so it is refused.
        if isinstance(threshold, bool) or not isinstance(threshold, int | float):
            raise ValueError(f"{name} must be a number")
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"{name} must be between 0 and 1")


def _apply_field(field: FieldValidation, policy: ExtractionPolicy) -> PolicyField:
    def decided(
        verdict: Verdict,
        status: FieldStatus | None,
        abstain_reason: AbstainReason | None = None,
        values: tuple[PolicyValue, ...] = (),
        withheld: tuple[WithheldValue, ...] = (),
    ) -> PolicyField:
        return PolicyField(
            field.dimension, verdict, status, abstain_reason, values, withheld, field.issues
        )

    if field.verdict is Verdict.REJECTED:
        return decided(Verdict.REJECTED, None)
    if field.verdict is Verdict.ABSTAINED:
        by_validator = any(i.code is ReasonCode.CONTRADICTORY_VALUES for i in field.issues)
        if by_validator:
            return decided(
                Verdict.ABSTAINED, FieldStatus.CONTRADICTORY, AbstainReason.CONTRADICTORY
            )
        return decided(Verdict.ABSTAINED, field.status, AbstainReason.MODEL)
    if field.status is not FieldStatus.STATED:
        return decided(Verdict.ACCEPTED, field.status)

    values: list[PolicyValue] = []
    withheld: list[WithheldValue] = []
    for value in field.values:
        reason = _withheld_reason(value, policy)
        if reason is not None:
            withheld.append(WithheldValue(*_value_parts(value), reason=reason))
        elif value.critical:
            # Precision is not measured yet: a critical value always needs a person.
            values.append(
                PolicyValue(
                    *_value_parts(value),
                    disposition=Disposition.REVIEW_REQUIRED,
                    review_reason=ReviewReason.CRITICAL_LABEL,
                )
            )
        else:
            values.append(
                PolicyValue(
                    *_value_parts(value), disposition=Disposition.AUTO_ACCEPTED, review_reason=None
                )
            )
    if not values:
        return decided(
            Verdict.ABSTAINED,
            FieldStatus.INSUFFICIENT_EVIDENCE,
            AbstainReason.LOW_CONFIDENCE,
            withheld=tuple(withheld),
        )
    return decided(Verdict.ACCEPTED, FieldStatus.STATED, None, tuple(values), tuple(withheld))


def _withheld_reason(value: AcceptedValue, policy: ExtractionPolicy) -> WithheldReason | None:
    if value.confidence is None:
        return WithheldReason.MISSING_CONFIDENCE
    threshold = (
        policy.min_confidence_explicit
        if value.basis is Basis.EXPLICIT
        else policy.min_confidence_inferred
    )
    # The boundary passes: a value at the threshold is kept.  Fail closed: a NaN is never
    # ">=", so it is withheld.
    return None if value.confidence >= threshold else WithheldReason.BELOW_THRESHOLD


def _value_parts(
    value: AcceptedValue,
) -> tuple[str, Basis, tuple[int, ...], float | None, bool, str | None]:
    return (
        value.label,
        value.basis,
        value.source_turn_indexes,
        value.confidence,
        value.critical,
        value.text,
    )


def _issue_json(issue: Issue) -> dict[str, object]:
    return {"code": issue.code.value, "path": issue.path}  # the message is for people


def _value_json(value: PolicyValue | WithheldValue) -> dict[str, object]:
    data: dict[str, object] = {
        "label": value.label,
        "basis": value.basis.value,
        "source_turn_indexes": list(value.source_turn_indexes),
        "confidence": value.confidence,
        "critical": value.critical,
        "text": value.text,
    }
    if isinstance(value, PolicyValue):
        data["disposition"] = value.disposition.value
        data["review_reason"] = value.review_reason.value if value.review_reason else None
    else:
        data["reason"] = value.reason.value
    return data


def _field_json(field: PolicyField) -> dict[str, object]:
    return {
        "verdict": field.verdict.value,
        "status": field.status.value if field.status else None,
        "abstain_reason": field.abstain_reason.value if field.abstain_reason else None,
        "review_required": field.review_required,
        "issues": [_issue_json(issue) for issue in field.issues],
        "values": [_value_json(value) for value in field.values],
        "withheld": [_value_json(value) for value in field.withheld],
    }
