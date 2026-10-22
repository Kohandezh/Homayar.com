"""Inputs, limits and the result of one review sample draw (HOM-72).

The constants and the rules below are part of ``review-sampling/1``.  A change
to any of them is a new ``SAMPLER_VERSION``.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

SAMPLER_VERSION = "review-sampling/1"
STRATUM_KEYS = ("agent_user_id", "language", "source", "team_id")
RISK_FLAGS = ("evidence_partial", "speaker_attribution_unknown", "transcript_corrected")
UNASSIGNED = "unassigned"
MAX_SEED = 2**53 - 1
METHODS = ("risk", "stratified", "random")

MAX_RANDOM_SIZE = 500
MAX_STRATIFY_KEYS = 2
MAX_PER_STRATUM_SIZE = 100
DEFAULT_RISK_CAP = 100
MAX_RISK_CAP = 500
MAX_SCOPE_VALUES = 20

ERROR_CODES = (
    "item_invalid",
    "duplicate_item",
    "sampling_config_invalid",
    "sampling_config_empty",
    "seed_invalid",
)

StratumKey = Literal["agent_user_id", "language", "source", "team_id"]
RiskFlag = Literal["evidence_partial", "speaker_attribution_unknown", "transcript_corrected"]


class SamplingError(ValueError):
    """Invalid input; ``code`` is one of ``ERROR_CODES``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, init=False)
class SampleItem:
    """One candidate for review.

    ``strata`` is a read-only mapping of every key of ``STRATUM_KEYS``; a key the
    caller left out is ``None``, which the draw reads as ``UNASSIGNED``.  ``risk_flags`` is sorted
    and has no repeats.  Anything else raises ``SamplingError`` ``item_invalid``.
    """

    item_id: str
    strata: Mapping[str, str | None]
    risk_flags: tuple[str, ...]

    def __init__(
        self,
        item_id: str,
        strata: Mapping[str, str | None] | None = None,
        risk_flags: Iterable[str] = (),
    ) -> None:
        if not isinstance(item_id, str):
            raise SamplingError("item_invalid", "item_id must be a string")
        given = {} if strata is None else strata
        if not isinstance(given, Mapping):
            raise SamplingError("item_invalid", f"strata of {item_id!r} must be a mapping")
        for key, value in given.items():
            if key not in STRATUM_KEYS:
                raise SamplingError("item_invalid", f"unknown stratum key {key!r} on {item_id!r}")
            if value is not None and not isinstance(value, str):
                raise SamplingError(
                    "item_invalid", f"stratum {key!r} of {item_id!r} must be a string or None"
                )
        if isinstance(risk_flags, (str, bytes)) or not isinstance(risk_flags, Iterable):
            raise SamplingError("item_invalid", f"risk_flags of {item_id!r} must be a list")
        flags = list(risk_flags)
        for flag in flags:
            if flag not in RISK_FLAGS:
                raise SamplingError("item_invalid", f"unknown risk flag {flag!r} on {item_id!r}")
        object.__setattr__(self, "item_id", item_id)
        object.__setattr__(
            self, "strata", MappingProxyType({key: given.get(key) for key in STRATUM_KEYS})
        )
        object.__setattr__(self, "risk_flags", tuple(sorted(set(flags))))

    def stratum_value(self, key: str) -> str:
        value = self.strata.get(key)
        return UNASSIGNED if value is None else value


def _distinct_sorted(values: list[str], name: str) -> list[str]:
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must not repeat a value")
    return sorted(values)


class SamplingConfig(BaseModel):
    """What to draw.  Frozen; unknown keys, wrong types and bounds are refused.

    Every validation path (the constructor, ``model_validate``,
    ``model_validate_json``, ``model_validate_strings``) raises ``SamplingError``:
    ``sampling_config_invalid`` for a rule break, ``sampling_config_empty`` when
    no method can select anything.
    The validated fields are already normalized (lists and scope keys sorted).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    random_size: int = Field(default=0, ge=0, le=MAX_RANDOM_SIZE)
    stratify_by: list[StratumKey] = Field(default_factory=list, max_length=MAX_STRATIFY_KEYS)
    per_stratum_size: int = Field(default=0, ge=0, le=MAX_PER_STRATUM_SIZE)
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    risk_cap: int = Field(default=DEFAULT_RISK_CAP, ge=1, le=MAX_RISK_CAP)
    scope: dict[StratumKey, list[str]] = Field(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise SamplingError("sampling_config_invalid", _first_error(exc)) from exc
        self.check_not_empty()

    @classmethod
    def _checked(cls, validate: Callable[[], Self]) -> Self:
        try:
            config = validate()
        except ValidationError as exc:
            # pydantic runs __init__ and wraps its SamplingError: keep that code
            for error in exc.errors():
                cause = error.get("ctx", {}).get("error")
                if isinstance(cause, SamplingError):
                    raise cause from exc
            raise SamplingError("sampling_config_invalid", _first_error(exc)) from exc
        config.check_not_empty()
        return config

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> Self:
        return cls._checked(lambda: super(SamplingConfig, cls).model_validate(obj, *args, **kwargs))

    @classmethod
    def model_validate_json(cls, json_data: Any, *args: Any, **kwargs: Any) -> Self:
        return cls._checked(
            lambda: super(SamplingConfig, cls).model_validate_json(json_data, *args, **kwargs)
        )

    @classmethod
    def model_validate_strings(cls, obj: Any, *args: Any, **kwargs: Any) -> Self:
        return cls._checked(
            lambda: super(SamplingConfig, cls).model_validate_strings(obj, *args, **kwargs)
        )

    @field_validator("stratify_by")
    @classmethod
    def _stratify_by(cls, value: list[str]) -> list[str]:
        return _distinct_sorted(value, "stratify_by")

    @field_validator("risk_flags")
    @classmethod
    def _risk_flags(cls, value: list[str]) -> list[str]:
        return _distinct_sorted(value, "risk_flags")

    @field_validator("scope")
    @classmethod
    def _scope(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        normalized = {}
        for key in sorted(value):
            values = value[key]
            if not 1 <= len(values) <= MAX_SCOPE_VALUES:
                raise ValueError(f"scope {key!r} needs 1 to {MAX_SCOPE_VALUES} values")
            normalized[key] = _distinct_sorted(values, f"scope {key!r}")
        return normalized

    @model_validator(mode="after")
    def _per_stratum_size(self) -> SamplingConfig:
        if bool(self.stratify_by) != (self.per_stratum_size > 0):
            raise ValueError("per_stratum_size must be above 0 exactly when stratify_by is set")
        return self

    def check_not_empty(self) -> None:
        if self.random_size == 0 and not self.stratify_by and not self.risk_flags:
            raise SamplingError(
                "sampling_config_empty",
                "random_size is 0, stratify_by and risk_flags are empty: nothing can be selected",
            )

    def normalized(self) -> dict[str, Any]:
        """Every field, in a JSON-safe form (the form the config digest reads)."""
        return {
            "random_size": self.random_size,
            "stratify_by": list(self.stratify_by),
            "per_stratum_size": self.per_stratum_size,
            "risk_flags": list(self.risk_flags),
            "risk_cap": self.risk_cap,
            "scope": {key: list(values) for key, values in self.scope.items()},
        }


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(part) for part in error["loc"]) or "config"
    return f"{where}: {error['msg']}"


def coerce_config(config: object) -> SamplingConfig:
    """``config`` as a validated ``SamplingConfig`` (a plain mapping is accepted).

    Any other value is ``sampling_config_invalid``: the check is at run time.
    """
    if isinstance(config, SamplingConfig):
        config.check_not_empty()
        return config
    if not isinstance(config, Mapping) or not all(isinstance(key, str) for key in config):
        raise SamplingError("sampling_config_invalid", "config must be an object of named fields")
    return SamplingConfig(**config)


def validate_seed(seed: object) -> int:
    """``seed`` when it is an ``int`` (not a ``bool``) in ``0..MAX_SEED``."""
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= MAX_SEED:
        raise SamplingError("seed_invalid", f"seed must be an integer from 0 to {MAX_SEED}")
    return seed


@dataclass(frozen=True)
class SampleDraw:
    """The result of ``draw_sample``.  ``to_json`` returns a fresh copy."""

    seed: int
    config: dict[str, Any]
    config_digest: str
    population_count: int
    scoped_count: int
    population_digest: str
    risk_flagged_count: int
    selections: list[dict[str, Any]]
    selection_digest: str
    shortfalls: list[dict[str, Any]]
    sampler_version: str = field(default=SAMPLER_VERSION)

    def to_json(self) -> dict[str, Any]:
        return {
            "sampler_version": self.sampler_version,
            "seed": self.seed,
            "config": copy.deepcopy(self.config),
            "config_digest": self.config_digest,
            "population_count": self.population_count,
            "scoped_count": self.scoped_count,
            "population_digest": self.population_digest,
            "risk_flagged_count": self.risk_flagged_count,
            "selections": copy.deepcopy(self.selections),
            "selection_digest": self.selection_digest,
            "shortfalls": copy.deepcopy(self.shortfalls),
        }
