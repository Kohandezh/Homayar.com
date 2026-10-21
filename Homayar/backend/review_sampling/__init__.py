"""Seeded random, stratified and risk-triggered review sampling (HOM-72).

See ``README.md`` in this package.  Pure: no database, no I/O.
"""

from backend.review_sampling.draw import config_digest, draw_sample, population_digest
from backend.review_sampling.models import (
    ERROR_CODES,
    MAX_SEED,
    METHODS,
    RISK_FLAGS,
    SAMPLER_VERSION,
    STRATUM_KEYS,
    UNASSIGNED,
    SampleDraw,
    SampleItem,
    SamplingConfig,
    SamplingError,
    coerce_config,
    validate_seed,
)

__all__ = [
    "ERROR_CODES",
    "MAX_SEED",
    "METHODS",
    "RISK_FLAGS",
    "SAMPLER_VERSION",
    "STRATUM_KEYS",
    "UNASSIGNED",
    "SampleDraw",
    "SampleItem",
    "SamplingConfig",
    "SamplingError",
    "coerce_config",
    "config_digest",
    "draw_sample",
    "population_digest",
    "validate_seed",
]
