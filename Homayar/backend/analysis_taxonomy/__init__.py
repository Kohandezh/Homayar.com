"""Conversation taxonomy: supported labels, extraction shape and validator (HOM-52).

See ``README.md`` in this package.  Nothing in the backend uses it yet (HOM-53 wires it).
"""

from backend.analysis_taxonomy.extraction import (
    EntityField,
    EntityValue,
    Extraction,
    LabelField,
    LabelValue,
)
from backend.analysis_taxonomy.mapping import EXTERNAL_ID_MAP
from backend.analysis_taxonomy.models import (
    ABSTAIN_VALUES,
    DEFAULT_TAXONOMY_ID,
    DIMENSIONS,
    TAXONOMY_SCHEMA,
    Basis,
    Cardinality,
    DimensionDefinition,
    FieldStatus,
    LabelDefinition,
    LabelStatus,
    Taxonomy,
    TaxonomyDimensions,
    TaxonomyError,
    TextMatch,
    load_taxonomy,
    load_taxonomy_file,
    released_versions,
    taxonomy_digest,
)
from backend.analysis_taxonomy.validator import (
    VALIDATOR_VERSION,
    AcceptedValue,
    ExtractionValidation,
    FieldValidation,
    Issue,
    ReasonCode,
    Verdict,
    validate_extraction,
)

__all__ = [
    "ABSTAIN_VALUES",
    "DEFAULT_TAXONOMY_ID",
    "DIMENSIONS",
    "EXTERNAL_ID_MAP",
    "TAXONOMY_SCHEMA",
    "VALIDATOR_VERSION",
    "AcceptedValue",
    "Basis",
    "Cardinality",
    "DimensionDefinition",
    "EntityField",
    "EntityValue",
    "Extraction",
    "ExtractionValidation",
    "FieldStatus",
    "FieldValidation",
    "Issue",
    "LabelDefinition",
    "LabelField",
    "LabelStatus",
    "LabelValue",
    "ReasonCode",
    "Taxonomy",
    "TaxonomyDimensions",
    "TaxonomyError",
    "TextMatch",
    "Verdict",
    "load_taxonomy",
    "load_taxonomy_file",
    "released_versions",
    "taxonomy_digest",
    "validate_extraction",
]
