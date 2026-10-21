"""The shape of one taxonomy extraction (HOM-52, WP11-T02).

The object a model will be asked for (HOM-53).  Every dimension is a status and a list
of values, so a single-valued dimension can still be given two values and the validator
can abstain on it.  Every key is required (``confidence`` may be ``null``), so a strict
JSON schema can be built from :class:`Extraction`.  Unknown keys are refused, so there is
no place for an NPS, a churn probability, a sentiment score or a free-text category.

Turn indexes mean what ``IntelligenceResult.source_turn_indexes`` means.  The shape
accepts any integer: a negative or out-of-range index is a validator rule, counted and
never repaired.  These models are the only shape check: tier 1 of the validator runs them.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr

from backend.analysis_taxonomy.models import Basis, FieldStatus

MAX_VALUES = 32
MAX_TURN_INDEXES = 64


class _Shape(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LabelValue(_Shape):
    label: StrictStr = Field(min_length=1, max_length=64)
    basis: Basis
    source_turn_indexes: tuple[StrictInt, ...] = Field(max_length=MAX_TURN_INDEXES)
    # The model's own number, not a calibrated probability; no rule here reads it.
    confidence: StrictFloat | None = Field(ge=0, le=1)


class EntityValue(LabelValue):
    text: StrictStr = Field(min_length=1, max_length=256)  # copied from a cited turn


class LabelField(_Shape):
    status: FieldStatus
    values: tuple[LabelValue, ...] = Field(max_length=MAX_VALUES)


class EntityField(_Shape):
    status: FieldStatus
    values: tuple[EntityValue, ...] = Field(max_length=MAX_VALUES)


class Extraction(_Shape):
    intent: LabelField
    topic: LabelField
    entity: EntityField
    outcome: LabelField
