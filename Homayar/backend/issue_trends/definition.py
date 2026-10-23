"""Versioned trend issue definitions and their strict loader (HOM-79, WP15-T01).

One checked-in JSON file per version (``definitions/{definition_id}.v{version}.json``)
lists the trend families and issues.  A taxonomy-sourced issue only references labels
of the pinned conversation taxonomy; it never defines a label of its own.  A released
file never changes: a change is a new version file, and a test pins the digest of every
released one.  The digest is computed like ``taxonomy_digest``, over the parsed
document, so formatting does not change it.

The loader reads only its own definition files and the pinned taxonomy file (through
``backend.analysis_taxonomy.load_taxonomy``).  This is the only module of the package
that imports ``backend.analysis_taxonomy``.  There is no default and no "latest"
version: a caller always pins one.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError

from backend import analysis_taxonomy
from backend.analysis_taxonomy import (
    POLICY_V1,
    POLICY_VERSION,
    VALIDATOR_VERSION,
    Taxonomy,
    TaxonomyError,
    policy_digest,
)

DEFINITION_SCHEMA = "homayar.issue-trends/1"
DEFAULT_DEFINITION_ID = "trends"
DEFINITIONS = Path(__file__).resolve().parent / "definitions"

# The same id rule as a taxonomy label id.
ID_PATTERN = r"^[a-z][a-z0-9_]{1,63}$"

# Never a family or issue id: the taxonomy's reserved words and the words of the
# counting states.
RESERVED_IDS = frozenset(
    {
        "stated",
        "not_stated",
        "insufficient_evidence",
        "contradictory",
        "explicit",
        "inferred",
        "unknown",
        "unavailable",
        "other",
        "none",
        "counted",
        "pending_review",
        "absent",
        "missing",
        "all",
        "total",
    }
)

# A conversation-taxonomy reference may name one of these dimensions.  Entity is left
# out on purpose: an entity value is personal data and never a trend issue or a key.
REFERENCE_DIMENSIONS = ("intent", "topic", "outcome")
MAX_REFERENCES = 4

_ID = re.compile(ID_PATTERN)
# A released file name: no leading zero and no version 0, as for the taxonomy files.
_FILE_NAME = re.compile(r"^(?P<id>[a-z][a-z0-9_]{1,63})\.v(?P<version>[1-9][0-9]*)\.json$")
_VERSIONED_NAME = re.compile(r"\.v[0-9]+\.json$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SINGLE_VALUED = frozenset({"intent", "outcome"})
_NAME_MAX = 80
_DEFINITION_MAX = 500
# The stored-row versions this code can read.
_KNOWN_POLICIES = frozenset({(POLICY_VERSION, policy_digest(POLICY_V1))})
_KNOWN_VALIDATORS = frozenset({VALIDATOR_VERSION})
# The keys of every object of the file, for the unknown-key check.
_TOP_KEYS = frozenset(
    {
        "schema",
        "definition_id",
        "version",
        "notes",
        "taxonomy",
        "extraction_policies",
        "validator_versions",
        "families",
        "issues",
        "retired_issue_ids",
    }
)
_TAXONOMY_KEYS = frozenset({"taxonomy_id", "taxonomy_version", "taxonomy_digest"})
_POLICY_KEYS = frozenset({"policy_version", "policy_digest"})
_FAMILY_KEYS = frozenset(
    {"id", "name_en", "name_fa", "definition", "source", "source_state", "source_reason"}
)
_ISSUE_KEYS = frozenset(
    {"id", "family", "name_en", "name_fa", "definition", "source", "status", "match"}
)
_REFERENCE_KEYS = frozenset({"dimension", "label"})


class FamilySource(str, Enum):
    CONVERSATION_TAXONOMY = "conversation_taxonomy"  # the stored extraction (HOM-53)
    SCORECARD = "scorecard"  # scorecards and conversation checks (HOM-54, HOM-55)


class SourceState(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"  # no stored result yet: never counted, never a zero


class SourceReason(str, Enum):
    """Why a family's source is unavailable.  Also the ``reason`` of its cells."""

    SCORECARD_RESULTS_UNAVAILABLE = "scorecard_results_unavailable"


class IssueStatus(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"  # kept so old rows resolve; never selected or counted


class DefinitionErrorCode(str, Enum):
    """``TrendDefinitionError.code``.  The loader checks in this order and reports the
    first defect it finds, so one defect gives exactly one code."""

    # the file
    UNKNOWN = "definition_unknown"  # no file for that id and version, or a bad id
    UNREADABLE = "definition_unreadable"  # missing, not UTF-8 or not JSON
    DUPLICATE_KEY = "definition_duplicate_key"  # the same JSON key twice in one object
    # the shape
    UNKNOWN_KEY = "definition_unknown_key"
    BAD_ID = "definition_bad_id"  # an id that breaks ID_PATTERN, or a reserved word
    INVALID = "definition_invalid"  # any other shape or rule defect
    DUPLICATE_ID = "definition_duplicate_id"  # a family or issue id twice, or a retired id
    # the references
    TAXONOMY_UNKNOWN = "reference_taxonomy_unknown"  # the pinned taxonomy cannot be loaded
    TAXONOMY_DIGEST_MISMATCH = "reference_taxonomy_digest_mismatch"
    VERSION_MISMATCH = "reference_version_mismatch"  # an unknown policy or validator pin
    UNKNOWN_LABEL = "reference_unknown_label"  # no such label in any dimension
    WRONG_DIMENSION = "reference_wrong_dimension"  # the label is in another dimension
    INACTIVE_LABEL = "reference_inactive_label"  # the label is deprecated
    RETIRED_LABEL = "reference_retired_label"  # the label id is retired
    # last, on a file that passed every check above
    DIGEST_MISMATCH = "definition_digest_mismatch"  # expected_digest given and different


class TrendDefinitionError(ValueError):
    def __init__(self, code: DefinitionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class _Definition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaxonomyPin(_Definition):
    taxonomy_id: str
    taxonomy_version: int
    taxonomy_digest: str  # 64 hex


class PolicyPin(_Definition):
    policy_version: str
    policy_digest: str  # 64 hex


class LabelReference(_Definition):
    """One label of the pinned taxonomy.  Its taxonomy id, version and digest are the
    definition's ``taxonomy`` pin: one definition never mixes taxonomy versions."""

    dimension: str  # one of REFERENCE_DIMENSIONS
    label: str  # an active label id of that dimension


class FamilyDefinition(_Definition):
    id: str
    name_en: str  # 1 to 80 characters
    name_fa: str  # 1 to 80 characters
    definition: str  # one line, 1 to 500 characters
    source: FamilySource
    source_state: SourceState
    source_reason: SourceReason | None  # set exactly when source_state is unavailable


class IssueDefinition(_Definition):
    id: str
    family: str  # a family id of the same file
    name_en: str
    name_fa: str
    definition: str
    source: FamilySource  # always its family's source
    status: IssueStatus
    match: tuple[LabelReference, ...]  # 1 to MAX_REFERENCES, all must hold (AND)


class TrendDefinition(_Definition):
    """The parsed file.  Rules the loader enforces beyond the field types:

    - ``schema`` is ``DEFINITION_SCHEMA``; ``definition_id`` matches ``ID_PATTERN``;
      ``version`` >= 1; a file named ``{id}.v{version}.json`` holds that id and version.
    - Family and issue ids match ``ID_PATTERN``, are not in ``RESERVED_IDS`` and are
      unique across families and issues together; a retired issue id is never an issue.
    - An available family has at least one active issue; an unavailable family has no
      issue and a ``source_reason``.
    - An issue names an existing family and has the family's ``source``.
    - A ``conversation_taxonomy`` issue has 1 to ``MAX_REFERENCES`` references, at most
      one per single-valued dimension (intent, outcome), no reference twice, and no
      two active issues have the same set of references.
    - Every reference names an active label of its dimension in the pinned taxonomy,
      loaded with ``load_taxonomy(id, version, expected_digest=digest)``.
    - ``extraction_policies`` and ``validator_versions`` are non-empty and name only
      versions this package knows (``extraction-policy/1`` with the digest of
      ``POLICY_V1``, ``analysis-taxonomy-validator/1``).
    """

    schema_id: str = Field(alias="schema")
    definition_id: str
    version: int
    notes: str | None
    taxonomy: TaxonomyPin
    extraction_policies: tuple[PolicyPin, ...]
    validator_versions: tuple[str, ...]
    families: tuple[FamilyDefinition, ...]  # file order is the display and tie order
    issues: tuple[IssueDefinition, ...]  # file order is the display and tie order
    retired_issue_ids: tuple[str, ...]

    # Computed once when the model is built: the model is frozen, so it never changes.
    _digest: str = PrivateAttr(default="")

    def model_post_init(self, context: Any, /) -> None:
        self._digest = definition_digest(self)

    @property
    def digest(self) -> str:
        return self._digest

    def family(self, family_id: str) -> FamilyDefinition | None:
        return next((family for family in self.families if family.id == family_id), None)

    def issue(self, issue_id: str) -> IssueDefinition | None:
        return next((issue for issue in self.issues if issue.id == issue_id), None)

    def active_issues(self, family_id: str | None = None) -> tuple[IssueDefinition, ...]:
        """Active issues in file order; only those of ``family_id`` when it is given."""
        return tuple(
            issue
            for issue in self.issues
            if issue.status is IssueStatus.ACTIVE
            and (family_id is None or issue.family == family_id)
        )


def _pinned_taxonomy(definition: TrendDefinition) -> Taxonomy:
    """The pinned taxonomy, through ``backend.analysis_taxonomy.load_taxonomy`` looked up
    at call time: the only file read outside this package."""
    pin = definition.taxonomy
    return analysis_taxonomy.load_taxonomy(
        pin.taxonomy_id, pin.taxonomy_version, expected_digest=pin.taxonomy_digest
    )


def service_area_ids(definition: TrendDefinition) -> tuple[str, ...]:
    """The active topic label ids of the pinned taxonomy, in taxonomy order: the allowed
    values of the ``service_area`` filter."""
    taxonomy = _pinned_taxonomy(definition)
    return tuple(
        label.id for label in taxonomy.dimension("topic").labels if label.status.value == "active"
    )


def definition_digest(definition: TrendDefinition) -> str:
    """sha256 over the canonical JSON of the parsed file (sorted keys, no spaces,
    UTF-8, ``schema`` under its JSON name): the same canonical form as
    ``taxonomy_digest``."""
    canonical = json.dumps(
        definition.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _DuplicateKey(ValueError):
    pass


def _refuse_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise _DuplicateKey(f"key {key!r} appears twice in one object")
        document[key] = value
    return document


def _refuse(code: DefinitionErrorCode, path: Path, message: str) -> TrendDefinitionError:
    return TrendDefinitionError(code, f"{path.name}: {message}")


def _items(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _objects(document: dict[str, object]) -> Iterator[tuple[str, object, frozenset[str]]]:
    """Every object of the raw file with its allowed keys (wrong types are skipped here
    and refused by the model)."""
    yield "the file", document, _TOP_KEYS
    yield "taxonomy", document.get("taxonomy"), _TAXONOMY_KEYS
    for index, pin in enumerate(_items(document.get("extraction_policies"))):
        yield f"extraction_policies[{index}]", pin, _POLICY_KEYS
    for index, family in enumerate(_items(document.get("families"))):
        yield f"families[{index}]", family, _FAMILY_KEYS
    for index, issue in enumerate(_items(document.get("issues"))):
        yield f"issues[{index}]", issue, _ISSUE_KEYS
        match = issue.get("match") if isinstance(issue, dict) else None
        for position, reference in enumerate(_items(match)):
            yield f"issues[{index}].match[{position}]", reference, _REFERENCE_KEYS


def _declared_ids(document: dict[str, object]) -> Iterator[str]:
    for key in ("families", "issues"):
        for item in _items(document.get(key)):
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                yield item["id"]
    for retired in _items(document.get("retired_issue_ids")):
        if isinstance(retired, str):
            yield retired


def _is_bad_id(value: str) -> bool:
    return not _ID.fullmatch(value) or value in RESERVED_IDS


def _one_line_text(value: str, maximum: int) -> bool:
    return 1 <= len(value) <= maximum and "\n" not in value


def _rule_defect(definition: TrendDefinition, path: Path) -> str | None:
    """The first broken family, issue or file rule (``definition_invalid``)."""
    if definition.schema_id != DEFINITION_SCHEMA:
        return f"expected schema {DEFINITION_SCHEMA}"
    if not _ID.fullmatch(definition.definition_id):
        return f"definition_id must match {ID_PATTERN}"
    if definition.version < 1:
        return "version must be at least 1"
    if not _DIGEST.fullmatch(definition.taxonomy.taxonomy_digest):
        return "taxonomy_digest is 64 lowercase hex characters"
    if not definition.extraction_policies:
        return "extraction_policies is empty"
    if any(not _DIGEST.fullmatch(pin.policy_digest) for pin in definition.extraction_policies):
        return "a policy_digest is 64 lowercase hex characters"
    if not definition.validator_versions:
        return "validator_versions is empty"
    items: list[FamilyDefinition | IssueDefinition] = [*definition.families, *definition.issues]
    for item in items:
        if not (
            _one_line_text(item.name_en, _NAME_MAX)
            and _one_line_text(item.name_fa, _NAME_MAX)
            and _one_line_text(item.definition, _DEFINITION_MAX)
        ):
            return f"{item.id!r} needs one-line names (1 to 80) and a definition (1 to 500)"
    for family in definition.families:
        available = family.source_state is SourceState.AVAILABLE
        if available is (family.source_reason is not None):
            return f"family {family.id!r}: source_reason is set exactly when unavailable"
        issues = [issue for issue in definition.issues if issue.family == family.id]
        if available and not any(issue.status is IssueStatus.ACTIVE for issue in issues):
            return f"available family {family.id!r} has no active issue"
        if not available and issues:
            return f"unavailable family {family.id!r} has issues"
    reference_sets: set[frozenset[tuple[str, str]]] = set()
    for issue in definition.issues:
        owner = definition.family(issue.family)
        if owner is None:
            return f"issue {issue.id!r} names an unknown family"
        if issue.source is not owner.source:
            return f"issue {issue.id!r} does not have its family's source"
        if issue.source is not FamilySource.CONVERSATION_TAXONOMY:
            continue
        references = [(reference.dimension, reference.label) for reference in issue.match]
        if not 1 <= len(references) <= MAX_REFERENCES:
            return f"issue {issue.id!r} needs 1 to {MAX_REFERENCES} references"
        if any(dimension not in REFERENCE_DIMENSIONS for dimension, _label in references):
            return f"issue {issue.id!r} references a dimension other than {REFERENCE_DIMENSIONS}"
        if len(set(references)) != len(references):
            return f"issue {issue.id!r} lists a reference twice"
        for dimension in _SINGLE_VALUED:
            if sum(1 for name, _label in references if name == dimension) > 1:
                return f"issue {issue.id!r} has two references on single-valued {dimension}"
        if issue.status is IssueStatus.ACTIVE:
            key = frozenset(references)
            if key in reference_sets:
                return f"issue {issue.id!r} has the references of another active issue"
            reference_sets.add(key)
    named = _FILE_NAME.fullmatch(path.name)
    if named is None and _VERSIONED_NAME.search(path.name):
        return f"{path.name} is not of the form {{id}}.v{{version}}.json"
    if named is not None and (
        named["id"] != definition.definition_id or int(named["version"]) != definition.version
    ):
        return f"the file holds {definition.definition_id} version {definition.version}"
    return None


def _duplicate_id(definition: TrendDefinition) -> str | None:
    seen: set[str] = set()
    for item_id in [family.id for family in definition.families] + [
        issue.id for issue in definition.issues
    ]:
        if item_id in seen:
            return item_id
        seen.add(item_id)
    retired: set[str] = set()
    for item_id in definition.retired_issue_ids:
        if item_id in seen or item_id in retired:
            return item_id
        retired.add(item_id)
    return None


def _check_references(definition: TrendDefinition, path: Path) -> None:
    try:
        taxonomy = _pinned_taxonomy(definition)
    except TaxonomyError as exc:
        code = (
            DefinitionErrorCode.TAXONOMY_DIGEST_MISMATCH
            if exc.code == "taxonomy_digest_mismatch"
            else DefinitionErrorCode.TAXONOMY_UNKNOWN
        )
        raise _refuse(code, path, str(exc)) from exc
    for policy in definition.extraction_policies:
        if (policy.policy_version, policy.policy_digest) not in _KNOWN_POLICIES:
            raise _refuse(
                DefinitionErrorCode.VERSION_MISMATCH,
                path,
                f"unknown extraction policy {policy.policy_version}",
            )
    for version in definition.validator_versions:
        if version not in _KNOWN_VALIDATORS:
            raise _refuse(
                DefinitionErrorCode.VERSION_MISMATCH, path, f"unknown validator {version}"
            )
    for issue in definition.issues:
        for reference in issue.match:
            dimension = taxonomy.dimension(reference.dimension)
            label = dimension.label(reference.label)
            where = f"issue {issue.id!r}: {reference.dimension} {reference.label!r}"
            if label is not None:
                if label.status.value != "active":
                    raise _refuse(
                        DefinitionErrorCode.INACTIVE_LABEL, path, f"{where} is deprecated"
                    )
                continue
            if reference.label in dimension.retired_label_ids:
                raise _refuse(DefinitionErrorCode.RETIRED_LABEL, path, f"{where} is retired")
            elsewhere = [
                name
                for name in ("intent", "topic", "entity", "outcome")
                if taxonomy.dimension(name).label(reference.label) is not None
            ]
            if elsewhere:
                raise _refuse(
                    DefinitionErrorCode.WRONG_DIMENSION, path, f"{where} is a {elsewhere[0]} label"
                )
            raise _refuse(DefinitionErrorCode.UNKNOWN_LABEL, path, f"{where} is not a label")


def load_definition_file(path: Path) -> TrendDefinition:
    """Load and check one definition file, references included.  Raises
    ``TrendDefinitionError``."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _refuse(DefinitionErrorCode.UNREADABLE, path, str(exc)) from exc
    try:
        document = json.loads(text, object_pairs_hook=_refuse_duplicate_keys)
    except _DuplicateKey as exc:
        raise _refuse(DefinitionErrorCode.DUPLICATE_KEY, path, str(exc)) from exc
    except ValueError as exc:
        raise _refuse(DefinitionErrorCode.UNREADABLE, path, str(exc)) from exc
    if not isinstance(document, dict):
        raise _refuse(DefinitionErrorCode.INVALID, path, "the file is not a JSON object")
    for where, item, allowed in _objects(document):
        if isinstance(item, dict):
            unknown = sorted(key for key in item if key not in allowed)
            if unknown:
                raise _refuse(
                    DefinitionErrorCode.UNKNOWN_KEY, path, f"{where} has unknown key {unknown[0]!r}"
                )
    for item_id in _declared_ids(document):
        if _is_bad_id(item_id):
            raise _refuse(
                DefinitionErrorCode.BAD_ID,
                path,
                f"{item_id!r} must match {ID_PATTERN} and not be a reserved word",
            )
    try:
        # Strict JSON validation: a bool or a string is never an integer.
        definition = TrendDefinition.model_validate_json(json.dumps(document), strict=True)
    except ValidationError as exc:
        raise _refuse(DefinitionErrorCode.INVALID, path, str(exc)) from exc
    defect = _rule_defect(definition, path)
    if defect is not None:
        raise _refuse(DefinitionErrorCode.INVALID, path, defect)
    duplicate = _duplicate_id(definition)
    if duplicate is not None:
        raise _refuse(DefinitionErrorCode.DUPLICATE_ID, path, f"id {duplicate!r} is used twice")
    _check_references(definition, path)
    return definition


@lru_cache(maxsize=8)
def _load_released(definition_id: str, version: int) -> TrendDefinition:
    path = DEFINITIONS / f"{definition_id}.v{version}.json"
    if not path.is_file():
        raise TrendDefinitionError(
            DefinitionErrorCode.UNKNOWN, f"definition {definition_id!r} has no version {version}"
        )
    return load_definition_file(path)


def load_definition(
    definition_id: str, version: int, *, expected_digest: str | None = None
) -> TrendDefinition:
    """The released definition ``definition_id`` at ``version``; never another version.
    ``definition_unknown`` for a bad id, a bool or a version < 1, or no such file;
    ``definition_digest_mismatch`` when ``expected_digest`` is given and differs."""
    if (
        not isinstance(definition_id, str)
        or not _ID.fullmatch(definition_id)
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
    ):
        raise TrendDefinitionError(
            DefinitionErrorCode.UNKNOWN,
            f"definition {definition_id!r} has no version {version!r}",
        )
    definition = _load_released(definition_id, version)
    if expected_digest is not None and expected_digest != definition.digest:
        raise TrendDefinitionError(
            DefinitionErrorCode.DIGEST_MISMATCH,
            f"{definition_id} version {version} does not have digest {expected_digest}",
        )
    return definition


def released_versions(definition_id: str = DEFAULT_DEFINITION_ID) -> tuple[int, ...]:
    """The versions that have a released file, ascending; ``()`` for a bad id."""
    if not isinstance(definition_id, str) or not _ID.fullmatch(definition_id):
        return ()
    versions = []
    for path in DEFINITIONS.glob(f"{definition_id}.v*.json"):
        named = _FILE_NAME.fullmatch(path.name)
        if named is not None and named["id"] == definition_id:
            versions.append(int(named["version"]))
    return tuple(sorted(versions))
