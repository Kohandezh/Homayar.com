"""Deterministic validator of one taxonomy extraction (HOM-52, WP11-T02).

A pure function: no database, no network, no model, no clock.  It reads the extraction,
the pinned taxonomy and the turn texts the indexes refer to, and judges each dimension on
its own: accepted, abstained (with the abstain value) or rejected (with stable reason
codes and the path of every value that broke a rule).  It never changes its input,
never repairs a value and never invents one.

Rules run in four tiers per field: 1 shape (the pydantic models of ``extraction``),
2 status and label, 3 evidence, 4 cardinality.  Only the first tier with an issue is
reported, so one defect gives exactly one code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import cast

from pydantic import BaseModel, ValidationError

from backend.analysis_taxonomy.extraction import EntityField, EntityValue, Extraction, LabelField
from backend.analysis_taxonomy.models import (
    ABSTAIN_VALUES,
    DIMENSIONS,
    Basis,
    Cardinality,
    DimensionDefinition,
    FieldStatus,
    LabelStatus,
    Taxonomy,
    TextMatch,
)
from backend.speech.text_normalize import normalize_for_matching

VALIDATOR_VERSION = "analysis-taxonomy-validator/1"
# The entity text rule always folds with the Persian profile (it also handles English),
# so the result does not depend on the transcript's language tag.
_MATCH_LANGUAGE = "fa"


class Verdict(str, Enum):
    ACCEPTED = "accepted"
    ABSTAINED = "abstained"
    REJECTED = "rejected"


class ReasonCode(str, Enum):
    # tier 1: shape
    UNKNOWN_KEY = "unknown_key"
    MISSING_FIELD = "missing_field"
    INVALID_VALUE = "invalid_value"
    # tier 2: status and label
    STATUS_NOT_ALLOWED = "status_not_allowed"
    VALUES_NOT_ALLOWED = "values_not_allowed"
    MISSING_VALUES = "missing_values"
    UNSUPPORTED_LABEL = "unsupported_label"
    DEPRECATED_LABEL = "deprecated_label"
    BASIS_NOT_ALLOWED = "basis_not_allowed"
    # tier 3: evidence
    TURN_INDEX_OUT_OF_RANGE = "turn_index_out_of_range"
    TOO_FEW_TURNS = "too_few_turns"
    ENTITY_TEXT_NOT_FOUND = "entity_text_not_found"
    # tier 4: cardinality
    DUPLICATE_LABEL = "duplicate_label"
    CONTRADICTORY_VALUES = "contradictory_values"


_RANK = {code: rank for rank, code in enumerate(ReasonCode)}


@dataclass(frozen=True)
class Issue:
    code: ReasonCode
    path: str  # "estimated_nps", "outcome", "topic.values[1].label"
    message: str  # for people; not part of the contract


@dataclass(frozen=True)
class AcceptedValue:
    label: str
    basis: Basis
    source_turn_indexes: tuple[int, ...]  # distinct valid indexes, input order
    confidence: float | None
    critical: bool  # copied from the label definition
    text: str | None  # entity text as given; None for the other dimensions


@dataclass(frozen=True)
class FieldValidation:
    dimension: str
    verdict: Verdict
    # The input status when it parsed; contradictory when the validator abstains.
    status: FieldStatus | None
    values: tuple[AcceptedValue, ...]  # only for an accepted stated field
    issues: tuple[Issue, ...]
    rejected_turn_indexes: int  # distinct bad indexes counted in tier 3; 0 if it did not run

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(value.label for value in self.values)

    @property
    def abstain_value(self) -> FieldStatus | None:
        return self.status if self.verdict is Verdict.ABSTAINED else None


@dataclass(frozen=True)
class ExtractionValidation:
    taxonomy_id: str
    taxonomy_version: int
    taxonomy_digest: str
    validator_version: str
    fields: tuple[FieldValidation, ...]  # always the four dimensions, in DIMENSIONS order
    issues: tuple[Issue, ...]  # extraction-level: unknown top-level keys, not an object

    def field(self, dimension: str) -> FieldValidation:
        for field in self.fields:
            if field.dimension == dimension:
                return field
        raise KeyError(dimension)

    @property
    def valid(self) -> bool:
        return not self.issues and all(f.verdict is not Verdict.REJECTED for f in self.fields)


_MISSING = object()


def validate_extraction(
    extraction: Mapping[str, object] | Extraction,
    taxonomy: Taxonomy,
    turns: Sequence[str],
) -> ExtractionValidation:
    """Judge ``extraction`` against ``taxonomy``; turn ``i`` is ``turns[i]``."""
    raw: object = (
        extraction.model_dump(mode="json") if isinstance(extraction, Extraction) else extraction
    )
    issues: list[Issue] = []
    if isinstance(raw, Mapping):
        for key in raw:
            if key not in DIMENSIONS:
                issues.append(
                    Issue(ReasonCode.UNKNOWN_KEY, str(key), "the extraction has no such key")
                )
        source = raw
    else:
        issues.append(Issue(ReasonCode.INVALID_VALUE, "", "the extraction is not an object"))
        source = {}
    matcher = _TurnMatcher(turns)
    fields = tuple(
        _validate_field(name, source.get(name, _MISSING), taxonomy.dimension(name), matcher)
        for name in DIMENSIONS
    )
    return ExtractionValidation(
        taxonomy_id=taxonomy.taxonomy_id,
        taxonomy_version=taxonomy.version,
        taxonomy_digest=taxonomy.digest,
        validator_version=VALIDATOR_VERSION,
        fields=fields,
        issues=tuple(issues),
    )


class _TurnMatcher:
    """Turn count and folded turn texts, folded once per turn and call."""

    def __init__(self, turns: Sequence[str]) -> None:
        self._turns = turns
        self._folded: dict[int, str] = {}

    def __len__(self) -> int:
        return len(self._turns)

    def contains(self, index: int, needle: str) -> bool:
        if index not in self._folded:
            self._folded[index] = normalize_for_matching(self._turns[index], _MATCH_LANGUAGE)
        return f" {needle} " in f" {self._folded[index]} "


def _fold(text: str) -> str:
    return normalize_for_matching(text, _MATCH_LANGUAGE)


def _rejected(
    name: str, status: FieldStatus | None, issues: list[Issue], bad_indexes: int = 0
) -> FieldValidation:
    ordered = sorted(issues, key=lambda issue: _RANK[issue.code])  # stable: keeps position
    return FieldValidation(name, Verdict.REJECTED, status, (), tuple(ordered), bad_indexes)


def _validate_field(
    name: str, raw: object, dimension: DimensionDefinition, turns: _TurnMatcher
) -> FieldValidation:
    if raw is _MISSING:
        return _rejected(name, None, [Issue(ReasonCode.MISSING_FIELD, name, "missing")])

    # Tier 1: shape, by the extraction models (the single source of truth for shape).
    model: type[BaseModel] = EntityField if name == "entity" else LabelField
    try:
        field = cast(LabelField | EntityField, model.model_validate(raw))
    except ValidationError as exc:
        return _rejected(name, _raw_status(raw), [_shape_issue(name, e) for e in exc.errors()])
    status = field.status

    # Tier 2: status and label.
    issues: list[Issue] = []
    if status in ABSTAIN_VALUES and status not in dimension.abstain_values:
        issues.append(
            Issue(
                ReasonCode.STATUS_NOT_ALLOWED, f"{name}.status", f"{name} is never {status.value}"
            )
        )
    if status is not FieldStatus.STATED and field.values:
        issues.append(
            Issue(ReasonCode.VALUES_NOT_ALLOWED, f"{name}.values", f"{status.value} has no values")
        )
    if status is FieldStatus.STATED and not field.values:
        issues.append(Issue(ReasonCode.MISSING_VALUES, f"{name}.values", "stated needs a value"))
    if status is FieldStatus.STATED:
        for position, value in enumerate(field.values):
            at = f"{name}.values[{position}]"
            label = dimension.label(value.label)
            if label is None:
                issues.append(
                    Issue(ReasonCode.UNSUPPORTED_LABEL, f"{at}.label", f"not a {name} label")
                )
                continue
            if label.status is LabelStatus.DEPRECATED:
                issues.append(Issue(ReasonCode.DEPRECATED_LABEL, f"{at}.label", "deprecated"))
            if value.basis not in label.allowed_basis:
                issues.append(
                    Issue(ReasonCode.BASIS_NOT_ALLOWED, f"{at}.basis", "basis not allowed")
                )
    if issues:
        return _rejected(name, status, issues)
    if status is FieldStatus.NOT_STATED:
        return FieldValidation(name, Verdict.ACCEPTED, status, (), (), 0)
    if status is not FieldStatus.STATED:
        return FieldValidation(name, Verdict.ABSTAINED, status, (), (), 0)

    # Tier 3: evidence.  A value with a bad index is judged on that alone.
    bad_indexes: set[int] = set()  # distinct over the whole field
    resolved: list[tuple[int, ...]] = []
    for position, value in enumerate(field.values):
        at = f"{name}.values[{position}]"
        valid: list[int] = []
        bad: list[int] = []
        for offset, index in enumerate(value.source_turn_indexes):
            if index in valid or index in bad:
                continue
            if 0 <= index < len(turns):
                valid.append(index)
            else:
                bad.append(index)
                issues.append(
                    Issue(
                        ReasonCode.TURN_INDEX_OUT_OF_RANGE,
                        f"{at}.source_turn_indexes[{offset}]",
                        f"turn {index} is not one of the {len(turns)} turns",
                    )
                )
        bad_indexes.update(bad)
        resolved.append(tuple(valid))
        if bad:
            continue
        label = dimension.label(value.label)
        assert label is not None  # tier 2 passed
        if len(valid) < label.min_source_turns:
            issues.append(
                Issue(
                    ReasonCode.TOO_FEW_TURNS,
                    f"{at}.source_turn_indexes",
                    f"{label.id} needs {label.min_source_turns} cited turn(s)",
                )
            )
            continue
        if dimension.text_match is TextMatch.NORMALIZED_TOKEN_SPAN:
            needle = _fold(cast(EntityValue, value).text)
            if not needle or not any(turns.contains(index, needle) for index in valid):
                issues.append(
                    Issue(
                        ReasonCode.ENTITY_TEXT_NOT_FOUND,
                        f"{at}.text",
                        "the text is not in any cited turn",
                    )
                )
    if issues:
        return _rejected(name, status, issues, len(bad_indexes))

    # Tier 4: cardinality.
    seen: set[tuple[str, str | None]] = set()
    for position, value in enumerate(field.values):
        key = (value.label, _fold(value.text) if isinstance(value, EntityValue) else None)
        if key in seen:
            issues.append(
                Issue(
                    ReasonCode.DUPLICATE_LABEL,
                    f"{name}.values[{position}].label",
                    f"{value.label} is listed twice",
                )
            )
        seen.add(key)
    if issues:
        return _rejected(name, status, issues, len(bad_indexes))
    if dimension.cardinality is Cardinality.SINGLE and len(field.values) > 1:
        return FieldValidation(
            name,
            Verdict.ABSTAINED,
            FieldStatus.CONTRADICTORY,
            (),
            (
                Issue(
                    ReasonCode.CONTRADICTORY_VALUES,
                    name,
                    f"{name} has one value; the extraction gives several",
                ),
            ),
            len(bad_indexes),
        )

    values = []
    for value, indexes in zip(field.values, resolved, strict=True):
        label = dimension.label(value.label)
        assert label is not None
        values.append(
            AcceptedValue(
                label=value.label,
                basis=value.basis,
                source_turn_indexes=indexes,
                confidence=value.confidence,
                critical=label.critical,
                text=value.text if isinstance(value, EntityValue) else None,
            )
        )
    return FieldValidation(name, Verdict.ACCEPTED, status, tuple(values), (), len(bad_indexes))


def _raw_status(raw: object) -> FieldStatus | None:
    if not isinstance(raw, Mapping):
        return None
    status = raw.get("status")
    if not isinstance(status, str):
        return None
    try:
        return FieldStatus(status)
    except ValueError:
        return None


def _shape_issue(name: str, error: Mapping[str, object]) -> Issue:
    kind = error["type"]
    code = (
        ReasonCode.UNKNOWN_KEY
        if kind == "extra_forbidden"
        else ReasonCode.MISSING_FIELD
        if kind == "missing"
        else ReasonCode.INVALID_VALUE
    )
    path = name
    for part in cast(tuple[str | int, ...], error["loc"]):
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return Issue(code, path, str(error["msg"]))
