"""Versioned conversation taxonomy definitions and their strict loader (HOM-52, WP11-T02).

One checked-in JSON file per taxonomy version (``definitions/{id}.v{version}.json``)
lists the supported labels of the four dimensions (intent, topic, entity, outcome), the
evidence each label needs, and the abstain values each dimension allows.  A released
file never changes: a change is a new version file, and a test pins the digest of every
released one.  The digest is computed the way ``providers.registry.manifest_digest``
does it, over the parsed document, so formatting does not change it.

There is no default and no "latest" version on purpose: a caller always pins one, so a
past result is never judged again by a newer definition without anyone choosing it.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    model_validator,
)

TAXONOMY_SCHEMA = "homayar.conversation-taxonomy/1"
DEFAULT_TAXONOMY_ID = "conversation"
DEFINITIONS = Path(__file__).resolve().parent / "definitions"
DIMENSIONS = ("intent", "topic", "entity", "outcome")

_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
# A released file name: no leading zero and no version 0.  Any other name that ends in
# ".v<digits>.json" is refused, so such a file is never skipped or misread.
_FILE_NAME = re.compile(r"^(?P<id>[a-z][a-z0-9_]{1,63})\.v(?P<version>[1-9][0-9]*)\.json$")
_VERSIONED_NAME = re.compile(r"\.v[0-9]+\.json$")


class Basis(str, Enum):
    """Same words and meaning as the HOM-61 claim basis."""

    EXPLICIT = "explicit"  # a speaker says it
    INFERRED = "inferred"  # the model concluded it


class FieldStatus(str, Enum):
    STATED = "stated"  # a value, supported by the cited turns
    NOT_STATED = "not_stated"  # the conversation does not touch this dimension
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"  # abstain: maybe there, not supported
    CONTRADICTORY = "contradictory"  # abstain: turns support incompatible labels


ABSTAIN_VALUES = frozenset({FieldStatus.INSUFFICIENT_EVIDENCE, FieldStatus.CONTRADICTORY})

# Never a label id: status, basis and report words, and free-text catch-alls.
RESERVED_IDS = frozenset(
    {status.value for status in FieldStatus}
    | {basis.value for basis in Basis}
    | {"unknown", "unavailable", "other", "none"}
)


class Cardinality(str, Enum):
    SINGLE = "single"
    MULTI = "multi"


class LabelStatus(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"  # kept so old results resolve; new extractions are rejected


class TextMatch(str, Enum):
    NONE = "none"
    NORMALIZED_TOKEN_SPAN = "normalized_token_span"  # noqa: S105 (entity text in a cited turn)


def is_label_id(value: str) -> bool:
    return bool(_ID.fullmatch(value))


class _Definition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LabelDefinition(_Definition):
    id: str
    name_en: str = Field(min_length=1, max_length=80)
    name_fa: str = Field(min_length=1, max_length=80)
    definition: str = Field(min_length=1, max_length=500)
    allowed_basis: tuple[Basis, ...] = Field(min_length=1)
    min_source_turns: StrictInt = Field(ge=1, le=10)
    critical: StrictBool
    status: LabelStatus
    replaced_by: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> LabelDefinition:
        if not is_label_id(self.id):
            raise ValueError(f"label id {self.id!r} must match {_ID.pattern}")
        if self.id in RESERVED_IDS:
            raise ValueError(f"{self.id!r} is a reserved word, not a label id")
        if "\n" in self.definition:
            raise ValueError(f"the definition of {self.id!r} is one line")
        if len(set(self.allowed_basis)) != len(self.allowed_basis):
            raise ValueError(f"allowed_basis of {self.id!r} lists a basis twice")
        if self.replaced_by is not None and self.status is not LabelStatus.DEPRECATED:
            raise ValueError(f"only a deprecated label has replaced_by ({self.id!r})")
        return self


class DimensionDefinition(_Definition):
    name_en: str = Field(min_length=1, max_length=80)
    name_fa: str = Field(min_length=1, max_length=80)
    definition: str = Field(min_length=1, max_length=500)
    cardinality: Cardinality
    abstain_values: tuple[FieldStatus, ...] = Field(min_length=1)
    text_match: TextMatch
    labels: tuple[LabelDefinition, ...] = Field(min_length=1)
    retired_label_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _consistent(self) -> DimensionDefinition:
        abstain = set(self.abstain_values)
        if len(abstain) != len(self.abstain_values) or not abstain <= ABSTAIN_VALUES:
            raise ValueError("abstain_values lists distinct abstain values only")
        if FieldStatus.INSUFFICIENT_EVIDENCE not in abstain:
            raise ValueError("every dimension allows insufficient_evidence")
        if FieldStatus.CONTRADICTORY in abstain and self.cardinality is not Cardinality.SINGLE:
            raise ValueError("only a single-valued dimension can be contradictory")
        ids = [label.id for label in self.labels]
        if len(set(ids)) != len(ids):
            raise ValueError("a label id appears twice in one dimension")
        for retired in self.retired_label_ids:
            if not is_label_id(retired):
                raise ValueError(f"retired id {retired!r} must match {_ID.pattern}")
            if retired in ids:
                raise ValueError(f"retired id {retired!r} is still a label")
        active = {label.id for label in self.labels if label.status is LabelStatus.ACTIVE}
        for label in self.labels:
            if label.replaced_by is not None and label.replaced_by not in active:
                raise ValueError(f"{label.id!r} is replaced by an id that is not active here")
            if self.text_match is TextMatch.NORMALIZED_TOKEN_SPAN and label.allowed_basis != (
                Basis.EXPLICIT,
            ):
                raise ValueError(f"a text-matched label is explicit only ({label.id!r})")
        return self

    def label(self, label_id: str) -> LabelDefinition | None:
        return next((label for label in self.labels if label.id == label_id), None)


class TaxonomyDimensions(_Definition):
    intent: DimensionDefinition
    topic: DimensionDefinition
    entity: DimensionDefinition
    outcome: DimensionDefinition

    @model_validator(mode="after")
    def _consistent(self) -> TaxonomyDimensions:
        seen: dict[str, str] = {}
        for name in DIMENSIONS:
            dimension: DimensionDefinition = getattr(self, name)
            spans = dimension.text_match is TextMatch.NORMALIZED_TOKEN_SPAN
            if spans is not (name == "entity"):
                raise ValueError("entity, and only entity, uses normalized_token_span")
            for label in dimension.labels:
                if label.id in seen:
                    raise ValueError(f"{label.id!r} is a label of {seen[label.id]} and {name}")
                seen[label.id] = name
        return self


class Taxonomy(_Definition):
    schema_id: str = Field(alias="schema")
    taxonomy_id: str
    version: StrictInt = Field(ge=1)
    notes: str | None = Field(default=None, max_length=1000)
    dimensions: TaxonomyDimensions

    @model_validator(mode="after")
    def _consistent(self) -> Taxonomy:
        if self.schema_id != TAXONOMY_SCHEMA:
            raise ValueError(f"expected schema {TAXONOMY_SCHEMA}")
        if not is_label_id(self.taxonomy_id):
            raise ValueError(f"taxonomy_id must match {_ID.pattern}")
        return self

    @property
    def digest(self) -> str:
        return taxonomy_digest(self)

    def dimension(self, name: str) -> DimensionDefinition:
        if name not in DIMENSIONS:
            raise KeyError(name)
        return getattr(self.dimensions, name)

    def label(self, dimension: str, label_id: str) -> LabelDefinition | None:
        return self.dimension(dimension).label(label_id)


def taxonomy_digest(taxonomy: Taxonomy) -> str:
    canonical = json.dumps(
        taxonomy.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TaxonomyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _DuplicateKey(ValueError):
    pass


def _refuse_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise _DuplicateKey(f"key {key!r} appears twice in one object")
        document[key] = value
    return document


def load_taxonomy_file(path: Path) -> Taxonomy:
    """Load one definition file.  A file named ``{id}.v{version}.json`` must hold that
    id and version; a version with a leading zero, or version 0, is refused."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise TaxonomyError("taxonomy_unreadable", f"{path.name}: {exc}") from exc
    try:
        data = json.loads(text, object_pairs_hook=_refuse_duplicate_keys)
    except _DuplicateKey as exc:
        raise TaxonomyError("taxonomy_invalid", f"{path.name}: {exc}") from exc
    except ValueError as exc:
        raise TaxonomyError("taxonomy_unreadable", f"{path.name}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != TAXONOMY_SCHEMA:
        raise TaxonomyError("taxonomy_invalid", f"{path.name}: expected schema {TAXONOMY_SCHEMA}")
    try:
        taxonomy = Taxonomy.model_validate(data)
    except ValidationError as exc:
        raise TaxonomyError("taxonomy_invalid", f"{path.name}: {exc}") from exc
    named = _FILE_NAME.fullmatch(path.name)
    if named is None and _VERSIONED_NAME.search(path.name):
        raise TaxonomyError(
            "taxonomy_invalid", f"{path.name} is not of the form {{id}}.v{{version}}.json"
        )
    if named is not None and (
        named["id"] != taxonomy.taxonomy_id or int(named["version"]) != taxonomy.version
    ):
        raise TaxonomyError(
            "taxonomy_invalid",
            f"{path.name} holds {taxonomy.taxonomy_id} version {taxonomy.version}",
        )
    return taxonomy


@lru_cache(maxsize=32)
def _load_released(taxonomy_id: str, version: int) -> Taxonomy:
    path = DEFINITIONS / f"{taxonomy_id}.v{version}.json"
    if not path.is_file():
        raise TaxonomyError(
            "taxonomy_unknown", f"taxonomy {taxonomy_id!r} has no version {version}"
        )
    return load_taxonomy_file(path)


def load_taxonomy(
    taxonomy_id: str, version: int, *, expected_digest: str | None = None
) -> Taxonomy:
    """The released definition ``taxonomy_id`` at ``version``; never another version."""
    if (
        not isinstance(taxonomy_id, str)
        or not is_label_id(taxonomy_id)
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
    ):
        raise TaxonomyError(
            "taxonomy_unknown", f"taxonomy {taxonomy_id!r} has no version {version!r}"
        )
    taxonomy = _load_released(taxonomy_id, version)
    if expected_digest is not None and expected_digest != taxonomy.digest:
        raise TaxonomyError(
            "taxonomy_digest_mismatch",
            f"{taxonomy_id} version {version} does not have digest {expected_digest}",
        )
    return taxonomy


def released_versions(taxonomy_id: str = DEFAULT_TAXONOMY_ID) -> tuple[int, ...]:
    if not isinstance(taxonomy_id, str) or not is_label_id(taxonomy_id):
        return ()
    versions = []
    for path in DEFINITIONS.glob(f"{taxonomy_id}.v*.json"):
        named = _FILE_NAME.fullmatch(path.name)
        if named is not None and named["id"] == taxonomy_id:
            versions.append(int(named["version"]))
    return tuple(sorted(versions))
