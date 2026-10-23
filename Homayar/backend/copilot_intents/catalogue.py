"""The versioned Copilot intent catalogue and its strict loader (HOM-92, WP17-T01).

One checked-in JSON file per version (``definitions/{definition_id}.v{version}.json``)
lists the intents the analytical Copilot can answer, and the route of each one.  A
released file never changes: a change is a new version file.  The digest of every
released file is pinned in ``RELEASED_CATALOGUE_DIGESTS``, so a changed released file is
refused at load time.  The digest is computed like ``issue_trends``, over the parsed
document, so formatting does not change it.

The loader reads only its own definition files and the pinned trend definition (through
``backend.issue_trends.load_definition``).  There is no default "latest" version.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from backend.issue_trends import (
    SAMPLE_POLICY_V1,
    FilterKey,
    TrendDefinition,
    TrendDefinitionError,
    load_definition,
    sample_policy_digest,
)

# The version of the routing rules of the README.  It changes when a routing rule changes.
CATALOGUE_VERSION = "copilot-intents/1"
CATALOGUE_SCHEMA = "homayar.copilot-intents/1"
DEFAULT_CATALOGUE_ID = "intents"
DEFINITIONS = Path(__file__).resolve().parent / "definitions"

# The ``coverage/1`` counters of ``services/coverage_service.py``.  A test holds it equal.
COVERAGE_VERSION = "coverage/1"

# The later WP17 tickets an intent depends on.
COMPONENTS = ("HOM-93", "HOM-94", "HOM-95", "HOM-96", "HOM-97")

# A released file never changes.  A new version adds its pin here and in the test.
RELEASED_CATALOGUE_DIGESTS = {
    ("intents", 1): "2cd7d4d41a4afb7471aa31d2c95996006404ba1a351914efd2b8e31f817c367e",
}

ID_PATTERN = r"^[a-z][a-z0-9_]{1,63}$"

_ID = re.compile(ID_PATTERN)
_FILE_NAME = re.compile(r"^(?P<id>[a-z][a-z0-9_]{1,63})\.v(?P<version>[1-9][0-9]*)\.json$")
_VERSIONED_NAME = re.compile(r"\.v[0-9]+\.json$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_DEFINITION_MAX = 500
_FILTER_KEYS = frozenset(key.value for key in FilterKey)
_SELECTOR_KEYS = frozenset({FilterKey.FAMILY.value, FilterKey.ISSUE.value})

_TOP_KEYS = frozenset(
    {
        "schema",
        "definition_id",
        "version",
        "notes",
        "trends",
        "sample_policy",
        "coverage_version",
        "intents",
    }
)
_TRENDS_KEYS = frozenset({"definition_id", "version", "digest"})
_SAMPLE_KEYS = frozenset({"version", "digest"})
_INTENT_KEYS = frozenset(
    {
        "id",
        "route",
        "reason",
        "gate",
        "target",
        "scope_rule",
        "components",
        "definition",
        "filter_keys",
        "params",
    }
)


class Route(StrEnum):
    SQL_AGGREGATE = "sql_aggregate"  # an exact SQL aggregate gives every number
    EVIDENCE_RETRIEVAL = "evidence_retrieval"  # cited spans of authorized records, no number
    REFUSE = "refuse"  # the request cannot be answered, whatever the data
    ABSTAIN = "abstain"  # the data cannot answer it reliably; only the gates give it


class RefusalReason(StrEnum):
    OUT_OF_SCOPE = "out_of_scope"  # not about the caller's call analytics, or unknown intent
    OUTSIDE_CALLER_SCOPE = "outside_caller_scope"  # a team or agent the caller cannot see
    SCOPE_NOT_ALLOWED = "scope_not_allowed"  # the request tries to set its own scope
    UNSUPPORTED_FILTER = "unsupported_filter"  # an issue_trends UNSUPPORTED_KEYS word
    INVALID_REQUEST = "invalid_request"  # any other defect of the typed request
    CAUSAL_NOT_SUPPORTED = "causal_not_supported"  # "why does X cause Y"
    INDIVIDUAL_RANKING_NOT_SUPPORTED = "individual_ranking_not_supported"  # agents ranked
    FORECAST_NOT_SUPPORTED = "forecast_not_supported"  # a future number


class AbstainReason(StrEnum):
    BELOW_MINIMUM_SAMPLE = "below_minimum_sample"  # a cell is insufficient_sample
    NOT_COMPUTABLE = "not_computable"  # a cell is not_computable
    NO_EVIDENCE = "no_evidence"  # no authorized evidence span


class Gate(StrEnum):
    """What must be checked after the route ran, before anything is shown."""

    CELL = "cell"  # gate_aggregate on Cells: every cell must be computed
    COUNT = "count"  # gate_aggregate on Cells: the count must be computable and shown
    COMPARISON = "comparison"  # gate_aggregate on Comparisons: every one must be computed
    EVIDENCE = "evidence"  # gate_evidence on the authorized evidence count
    NONE = "none"  # nothing: a refusal, or coverage counters (no sample minimum)


class ScopeRule(StrEnum):
    """Which callers an intent may answer."""

    # The answer is narrowed by the caller's scope (HOM-96 adds it by AND).  Any caller.
    CALLER_SCOPE = "caller_scope"
    # The answer always counts the whole workspace (compute_coverage).  Only a caller
    # whose scope is the whole workspace: team_ids and agent_user_ids both None.
    WHOLE_WORKSPACE = "whole_workspace"


class Target(StrEnum):
    """Which trend rows a request must select."""

    NONE = "none"  # no selector
    SELECTOR = "selector"  # at least one of family, issue, service_area
    ONE_ROW = "one_row"  # exactly one issue or exactly one family, no service_area


class ParamName(StrEnum):
    """Intent-specific parameters.  The rules of each are in ``routing.py`` and the
    README.  Each is used by one intent of version 1."""

    COMPARE_BY = "compare_by"
    COMPARE_VALUES = "compare_values"
    CONVERSATION_ID = "conversation_id"
    GRAIN = "grain"
    LIMIT = "limit"
    QUERY = "query"


# A param belongs to one route: grain and the cohorts shape SQL cells, the rest bound a
# retrieval.  The loader refuses a param on an intent of another route.
_PARAM_ROUTES = {
    ParamName.COMPARE_BY: Route.SQL_AGGREGATE,
    ParamName.COMPARE_VALUES: Route.SQL_AGGREGATE,
    ParamName.GRAIN: Route.SQL_AGGREGATE,
    ParamName.CONVERSATION_ID: Route.EVIDENCE_RETRIEVAL,
    ParamName.LIMIT: Route.EVIDENCE_RETRIEVAL,
    ParamName.QUERY: Route.EVIDENCE_RETRIEVAL,
}


class CatalogueErrorCode(StrEnum):
    """``CatalogueError.code``.  The loader checks in this order and reports the first
    defect it finds, so one defect gives exactly one code."""

    UNKNOWN = "catalogue_unknown"  # no released file for that id and version, or a bad id
    UNREADABLE = "catalogue_unreadable"  # missing, not UTF-8 or not JSON
    DUPLICATE_KEY = "catalogue_duplicate_key"  # the same JSON key twice in one object
    UNKNOWN_KEY = "catalogue_unknown_key"
    BAD_ID = "catalogue_bad_id"  # an intent id that breaks ID_PATTERN
    INVALID = "catalogue_invalid"  # any other shape or rule defect
    DUPLICATE_ID = "catalogue_duplicate_id"
    REFERENCE_MISMATCH = "catalogue_reference_mismatch"  # trends, sample or coverage pin
    DIGEST_MISMATCH = "catalogue_digest_mismatch"  # not the pinned or the expected digest


class CatalogueError(ValueError):
    def __init__(self, code: CatalogueErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class IntentDefinition:
    id: str
    route: Route
    reason: RefusalReason | None  # set exactly when the route is refuse
    gate: Gate
    target: Target
    scope_rule: ScopeRule
    components: tuple[str, ...]  # sorted, from COMPONENTS, never empty
    definition: str  # one line
    filter_keys: tuple[str, ...]  # the issue_trends filter keys it takes, sorted
    params: tuple[ParamName, ...]  # its own parameters, sorted


@dataclass(frozen=True)
class IntentCatalogue:
    version: str  # CATALOGUE_VERSION
    definition_id: str
    definition_version: int
    digest: str  # sha256 of the canonical JSON of the file
    notes: str | None
    trends: TrendDefinition  # the pinned trend definition, used for every filter check
    sample_policy_version: str
    sample_policy_digest: str
    coverage_version: str
    intents: tuple[IntentDefinition, ...]  # sorted by id

    def get(self, intent_id: object) -> IntentDefinition | None:
        if not isinstance(intent_id, str):
            return None
        return next((intent for intent in self.intents if intent.id == intent_id), None)


def catalogue_digest(document: object) -> str:
    """sha256 over the canonical JSON of the parsed file (sorted keys, no spaces, UTF-8),
    the same canonical form as ``issue_trends`` ``definition_digest``."""
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
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


def _refuse(code: CatalogueErrorCode, path: Path, message: str) -> CatalogueError:
    return CatalogueError(code, f"{path.name}: {message}")


class _Invalid(ValueError):
    pass


def _items(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _objects(document: dict[str, object]) -> Iterator[tuple[str, object, frozenset[str]]]:
    """Every object of the raw file with its allowed keys (wrong types are refused
    later, as ``catalogue_invalid``)."""
    yield "the file", document, _TOP_KEYS
    yield "trends", document.get("trends"), _TRENDS_KEYS
    yield "sample_policy", document.get("sample_policy"), _SAMPLE_KEYS
    for index, intent in enumerate(_items(document.get("intents"))):
        yield f"intents[{index}]", intent, _INTENT_KEYS


def _object(value: object, where: str, keys: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _Invalid(f"{where} is not an object")
    missing = sorted(keys - set(value))
    if missing:
        raise _Invalid(f"{where} has no key {missing[0]!r}")
    return value


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise _Invalid(f"{where} is not a non-empty string")
    return value


def _integer(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _Invalid(f"{where} is not an integer of at least 1")
    return value


def _digest_text(value: object, where: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise _Invalid(f"{where} is not 64 lowercase hex characters")
    return value


def _word[E: StrEnum](value: object, enum: type[E], where: str) -> E:
    if not isinstance(value, str):
        raise _Invalid(f"{where} is not a string")
    try:
        return enum(value)
    except ValueError as exc:
        raise _Invalid(f"{where} does not allow {value!r}") from exc


def _sorted_words(value: object, allowed: frozenset[str], where: str) -> tuple[str, ...]:
    """A list of distinct allowed strings in sorted order (one canonical form)."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _Invalid(f"{where} is not a list of strings")
    unknown = [item for item in value if item not in allowed]
    if unknown:
        raise _Invalid(f"{where} does not allow {unknown[0]!r}")
    if value != sorted(set(value)):
        raise _Invalid(f"{where} must be sorted and have no duplicate")
    return tuple(value)


def _intent(raw: object, where: str) -> IntentDefinition:
    item = _object(raw, where, _INTENT_KEYS)
    intent_id = _text(item["id"], f"{where}.id")
    route = _word(item["route"], Route, f"{where}.route")
    gate = _word(item["gate"], Gate, f"{where}.gate")
    target = _word(item["target"], Target, f"{where}.target")
    scope_rule = _word(item["scope_rule"], ScopeRule, f"{where}.scope_rule")
    reason = None
    if item["reason"] is not None:
        reason = _word(item["reason"], RefusalReason, f"{where}.reason")
    components = _sorted_words(item["components"], frozenset(COMPONENTS), f"{where}.components")
    definition = _text(item["definition"], f"{where}.definition")
    filter_keys = _sorted_words(item["filter_keys"], _FILTER_KEYS, f"{where}.filter_keys")
    params = _sorted_words(
        item["params"], frozenset(name.value for name in ParamName), f"{where}.params"
    )
    if len(definition) > _DEFINITION_MAX or "\n" in definition:
        raise _Invalid(f"{where}.definition is one line of at most {_DEFINITION_MAX} characters")
    if not components:
        raise _Invalid(f"{where} needs at least one component")
    if route is Route.ABSTAIN:
        raise _Invalid(f"{where}: only the gates abstain; no intent routes to abstain")
    if (route is Route.REFUSE) is (reason is None):
        raise _Invalid(f"{where}: reason is set exactly when the route is refuse")
    expected_gates = {
        Route.SQL_AGGREGATE: {Gate.CELL, Gate.COUNT, Gate.COMPARISON, Gate.NONE},
        Route.EVIDENCE_RETRIEVAL: {Gate.EVIDENCE},
        Route.REFUSE: {Gate.NONE},
    }[route]
    if gate not in expected_gates:
        raise _Invalid(f"{where}: route {route.value} cannot have gate {gate.value}")
    if (gate is Gate.COMPARISON) is not (ParamName.COMPARE_BY.value in params):
        raise _Invalid(f"{where}: gate comparison is set exactly when it takes compare_by")
    if scope_rule is ScopeRule.WHOLE_WORKSPACE and route is not Route.SQL_AGGREGATE:
        raise _Invalid(f"{where}: only an sql_aggregate intent can need the whole workspace")
    if route is Route.REFUSE and (filter_keys or params or target is not Target.NONE):
        raise _Invalid(f"{where}: a refusal takes no filter, param or target")
    for name in params:
        owner = _PARAM_ROUTES[ParamName(name)]
        if owner is not route:
            raise _Invalid(f"{where}: param {name} belongs to route {owner.value}")
    if (ParamName.COMPARE_BY.value in params) is not (ParamName.COMPARE_VALUES.value in params):
        raise _Invalid(f"{where}: compare_by and compare_values come together")
    if target is not Target.NONE and not _SELECTOR_KEYS <= set(filter_keys):
        raise _Invalid(f"{where}: target {target.value} needs the family and issue filters")
    if target is Target.SELECTOR and FilterKey.SERVICE_AREA.value not in filter_keys:
        raise _Invalid(f"{where}: target selector needs the service_area filter")
    return IntentDefinition(
        id=intent_id,
        route=route,
        reason=reason,
        gate=gate,
        target=target,
        scope_rule=scope_rule,
        components=components,
        definition=definition,
        filter_keys=filter_keys,
        params=tuple(ParamName(name) for name in params),
    )


@dataclass(frozen=True)
class _Parsed:
    """The checked top level of a file, before its references are checked."""

    definition_id: str
    version: int
    notes: str | None
    trends_id: str
    trends_version: int
    trends_digest: str
    sample_version: str
    sample_digest: str
    coverage_version: str
    intents: tuple[IntentDefinition, ...]


def _parse(document: dict[str, object], path: Path) -> _Parsed:
    """The shape and rule checks (``catalogue_invalid``)."""
    top = _object(document, "the file", _TOP_KEYS)
    if top["schema"] != CATALOGUE_SCHEMA:
        raise _Invalid(f"expected schema {CATALOGUE_SCHEMA}")
    definition_id = _text(top["definition_id"], "definition_id")
    if not _ID.fullmatch(definition_id):
        raise _Invalid(f"definition_id must match {ID_PATTERN}")
    version = _integer(top["version"], "version")
    notes = None if top["notes"] is None else _text(top["notes"], "notes")
    trends = _object(top["trends"], "trends", _TRENDS_KEYS)
    sample = _object(top["sample_policy"], "sample_policy", _SAMPLE_KEYS)
    raw_intents = top["intents"]
    parsed = _Parsed(
        definition_id=definition_id,
        version=version,
        notes=notes,
        trends_id=_text(trends["definition_id"], "trends.definition_id"),
        trends_version=_integer(trends["version"], "trends.version"),
        trends_digest=_digest_text(trends["digest"], "trends.digest"),
        sample_version=_text(sample["version"], "sample_policy.version"),
        sample_digest=_digest_text(sample["digest"], "sample_policy.digest"),
        coverage_version=_text(top["coverage_version"], "coverage_version"),
        intents=(),
    )
    if not isinstance(raw_intents, list) or not raw_intents:
        raise _Invalid("intents is a non-empty list")
    intents = tuple(_intent(raw, f"intents[{index}]") for index, raw in enumerate(raw_intents))
    ids = [intent.id for intent in intents]
    if ids != sorted(ids):
        raise _Invalid("intents must be sorted by id")
    named = _FILE_NAME.fullmatch(path.name)
    if named is None and _VERSIONED_NAME.search(path.name):
        raise _Invalid(f"{path.name} is not of the form {{id}}.v{{version}}.json")
    if named is not None and (named["id"] != definition_id or int(named["version"]) != version):
        raise _Invalid(f"the file holds {definition_id} version {version}")
    return replace(parsed, intents=intents)


def _duplicate_id(intents: tuple[IntentDefinition, ...]) -> str | None:
    seen: set[str] = set()
    for intent in intents:
        if intent.id in seen:
            return intent.id
        seen.add(intent.id)
    return None


def _check_references(parsed: _Parsed, path: Path) -> TrendDefinition:
    try:
        definition = load_definition(
            parsed.trends_id, parsed.trends_version, expected_digest=parsed.trends_digest
        )
    except TrendDefinitionError as exc:
        raise _refuse(CatalogueErrorCode.REFERENCE_MISMATCH, path, str(exc)) from exc
    if (parsed.sample_version, parsed.sample_digest) != (
        SAMPLE_POLICY_V1.version,
        sample_policy_digest(SAMPLE_POLICY_V1),
    ):
        raise _refuse(
            CatalogueErrorCode.REFERENCE_MISMATCH,
            path,
            f"unknown sample policy {parsed.sample_version}",
        )
    if parsed.coverage_version != COVERAGE_VERSION:
        raise _refuse(
            CatalogueErrorCode.REFERENCE_MISMATCH,
            path,
            f"unknown coverage version {parsed.coverage_version}",
        )
    return definition


def load_catalogue_file(path: Path, *, expected_digest: str | None = None) -> IntentCatalogue:
    """Load and check one catalogue file.  A file whose content is a released id and
    version must have that version's pinned digest.  A file of an unreleased version
    loads when its content matches its name.  Raises ``CatalogueError``."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _refuse(CatalogueErrorCode.UNREADABLE, path, str(exc)) from exc
    try:
        document = json.loads(text, object_pairs_hook=_refuse_duplicate_keys)
    except _DuplicateKey as exc:
        raise _refuse(CatalogueErrorCode.DUPLICATE_KEY, path, str(exc)) from exc
    except ValueError as exc:
        raise _refuse(CatalogueErrorCode.UNREADABLE, path, str(exc)) from exc
    if not isinstance(document, dict):
        raise _refuse(CatalogueErrorCode.INVALID, path, "the file is not a JSON object")
    for where, item, allowed in _objects(document):
        if isinstance(item, dict):
            unknown = sorted(key for key in item if key not in allowed)
            if unknown:
                raise _refuse(
                    CatalogueErrorCode.UNKNOWN_KEY, path, f"{where} has unknown key {unknown[0]!r}"
                )
    for intent in _items(document.get("intents")):
        if isinstance(intent, dict) and isinstance(intent.get("id"), str):
            if not _ID.fullmatch(intent["id"]):
                raise _refuse(
                    CatalogueErrorCode.BAD_ID, path, f"{intent['id']!r} must match {ID_PATTERN}"
                )
    try:
        parsed = _parse(document, path)
    except _Invalid as exc:
        raise _refuse(CatalogueErrorCode.INVALID, path, str(exc)) from exc
    duplicate = _duplicate_id(parsed.intents)
    if duplicate is not None:
        raise _refuse(CatalogueErrorCode.DUPLICATE_ID, path, f"id {duplicate!r} is used twice")
    trends = _check_references(parsed, path)
    digest = catalogue_digest(document)
    definition_id, version = parsed.definition_id, parsed.version
    pinned = RELEASED_CATALOGUE_DIGESTS.get((definition_id, version))
    if pinned is not None and digest != pinned:
        raise _refuse(
            CatalogueErrorCode.DIGEST_MISMATCH,
            path,
            f"{definition_id} version {version} is released with digest {pinned}",
        )
    if expected_digest is not None and digest != expected_digest:
        raise _refuse(
            CatalogueErrorCode.DIGEST_MISMATCH, path, f"the digest is not {expected_digest}"
        )
    return IntentCatalogue(
        version=CATALOGUE_VERSION,
        definition_id=definition_id,
        definition_version=version,
        digest=digest,
        notes=parsed.notes,
        trends=trends,
        sample_policy_version=parsed.sample_version,
        sample_policy_digest=parsed.sample_digest,
        coverage_version=parsed.coverage_version,
        intents=parsed.intents,
    )


def load_catalogue(
    definition_id: str = DEFAULT_CATALOGUE_ID,
    version: int = 1,
    *,
    expected_digest: str | None = None,
) -> IntentCatalogue:
    """The released catalogue ``definition_id`` at ``version``; never another version.
    ``catalogue_unknown`` for a bad id, a bool or a version < 1, no such file, or a file
    with no pinned digest; ``catalogue_digest_mismatch`` when the file is not its pinned
    digest, or ``expected_digest`` is given and differs.  Not cached: the caller loads it
    once and passes it to ``route_request``."""
    if (
        not isinstance(definition_id, str)
        or not _ID.fullmatch(definition_id)
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
    ):
        raise CatalogueError(
            CatalogueErrorCode.UNKNOWN, f"catalogue {definition_id!r} has no version {version!r}"
        )
    path = DEFINITIONS / f"{definition_id}.v{version}.json"
    if (definition_id, version) not in RELEASED_CATALOGUE_DIGESTS or not path.is_file():
        raise CatalogueError(
            CatalogueErrorCode.UNKNOWN, f"catalogue {definition_id!r} has no version {version}"
        )
    return load_catalogue_file(path, expected_digest=expected_digest)
