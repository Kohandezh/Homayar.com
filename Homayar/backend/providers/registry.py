"""Versioned model/prompt/capability manifests and preflight (WP08-T01, ADR 0026).

A manifest describes one *version* of what a stage runs: the adapter
(``provider``), the model and its pinned revision, the exact identity
parameters the adapter declares (endpoint, prompt and schema digests, compute
type ...), languages and tasks, licence and licence policy, where the data goes
(``endpoint_kind``, ``endpoint``, ``region``), offline capability and the
structured-output mode.  Manifests are immutable per ``(manifest_id,
version)``; a change is a new version, and an older approved version can be
selected again (``MODEL_MANIFEST_PINS``) without touching the runs that
recorded any other version.

Preflight resolves every stage adapter of a provider bundle against the
registry.  An adapter matches a manifest only when provider, model and *all*
declared identity parameters are equal, so an edited prompt, another endpoint
or another model is an unknown model.  In strict mode (staging, production)
an unknown, development-only or retired model fails before any stage runs;
in development and test an unregistered adapter is recorded as such.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.providers.base import ProviderError, ProviderIdentity
from backend.providers.egress import validate_endpoint

MANIFEST_SCHEMA = "homayar.model-manifests/1"
BUILTIN_MANIFESTS = Path(__file__).resolve().parent / "manifests" / "builtin.json"
STAGES = ("asr", "diarization", "intelligence")
UNREGISTERED = "unregistered"

Scalar = str | int | float | bool
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
_LANGUAGE = re.compile(r"^(\*|[a-z]{2,3}(-[A-Za-z0-9]{2,8})?)$")
# Names that point at whatever the provider serves today: never evidence of
# which weights produced a result (``unpinned`` is the skeleton's marker).
_MUTABLE_ALIASES = frozenset(
    {"unpinned", "latest", "main", "master", "head", "default", "stable", "current", "newest"}
)
_MUTABLE_SUFFIX = re.compile(r"[-:/@](latest|newest|current)$")


def is_mutable_alias(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered in _MUTABLE_ALIASES or bool(_MUTABLE_SUFFIX.search(lowered))


def is_manifest_id(value: str) -> bool:
    return bool(_ID.fullmatch(value))


class ManifestStatus(str, Enum):
    QUALIFIED = "qualified"  # approved for production use of this stage
    DEVELOPMENT = "development"  # usable in development/test only
    RETIRED = "retired"  # kept for history; never selected


class EndpointKind(str, Enum):
    IN_PROCESS = "in_process"  # runs inside the worker; no network call
    PRIVATE = "private"  # an endpoint operated by the customer/deployment
    CLOUD = "cloud"  # a third-party API: a subprocessor


class StructuredOutput(str, Enum):
    NONE = "none"
    JSON_OBJECT = "json_object"
    JSON_SCHEMA_STRICT = "json_schema_strict"


class ModelManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_id: str
    version: int = Field(ge=1)
    stage: Literal["asr", "diarization", "intelligence"]
    status: ManifestStatus
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    model_revision: str = Field(min_length=1, max_length=128)
    identity_params: dict[str, Scalar] = Field(default_factory=dict)
    languages: list[str] = Field(min_length=1)
    tasks: list[str] = Field(default_factory=list)
    license: str = Field(min_length=1, max_length=80)
    license_policy: Literal["approved", "review_required", "rejected"]
    endpoint_kind: EndpointKind
    endpoint: str | None = None
    region: str = Field(min_length=1, max_length=40)
    offline_capable: bool
    structured_output: StructuredOutput = StructuredOutput.NONE
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _consistent(self) -> ModelManifest:
        if not _ID.fullmatch(self.manifest_id):
            raise ValueError("manifest_id must be lowercase letters, digits, '.', '_' or '-'")
        for language in self.languages:
            if not _LANGUAGE.fullmatch(language):
                raise ValueError(f"invalid language code {language!r}")
        if self.endpoint_kind is EndpointKind.IN_PROCESS:
            if self.endpoint is not None or self.region != "local":
                raise ValueError("an in-process model has no endpoint and region 'local'")
        else:
            if not self.endpoint or not re.match(r"^https?://[^\s/@]+", self.endpoint):
                raise ValueError("a private or cloud model needs an HTTP(S) endpoint")
            problem = validate_endpoint(self.endpoint, kind=self.endpoint_kind.value)
            if problem is not None:
                raise ValueError(problem)
            declared = self.identity_params.get("endpoint")
            if declared is not None and declared != self.endpoint:
                raise ValueError("identity_params.endpoint must equal endpoint")
            if self.endpoint_kind is EndpointKind.CLOUD and self.region in {"local", "unknown"}:
                raise ValueError("a cloud model must name the region the data is processed in")
            if self.offline_capable:
                raise ValueError("a networked model is not offline-capable")
        if self.status is ManifestStatus.QUALIFIED:
            if self.license_policy != "approved":
                raise ValueError("a qualified manifest needs an approved licence policy")
            if is_mutable_alias(self.model_revision):
                raise ValueError("a qualified manifest pins the model revision")
            if is_mutable_alias(self.model):
                raise ValueError("a qualified manifest names a model, not a moving alias")
        return self

    @property
    def digest(self) -> str:
        return manifest_digest(self)

    def document(self) -> dict[str, object]:
        return self.model_dump(mode="json")


def manifest_digest(manifest: ModelManifest) -> str:
    canonical = json.dumps(
        manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ManifestError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ResolvedManifest:
    """What a stage ran under: a registered manifest, or an honest
    ``unregistered`` marker (development/test only)."""

    stage: str
    manifest: ModelManifest | None
    identity: ProviderIdentity

    @property
    def manifest_id(self) -> str:
        return self.manifest.manifest_id if self.manifest else UNREGISTERED

    @property
    def version(self) -> int | None:
        return self.manifest.version if self.manifest else None

    @property
    def digest(self) -> str | None:
        return self.manifest.digest if self.manifest else None

    @property
    def placement(self) -> Placement:
        """Where the stage's data goes.  Declared by the manifest; for an
        unregistered adapter (development/test only) inferred from its
        identity: no endpoint means in-process, any endpoint is treated as an
        unknown cloud region, so no policy can approve it."""
        if self.manifest is not None:
            return Placement(
                self.manifest.endpoint_kind, self.manifest.region, self.manifest.endpoint, True
            )
        endpoint = self.identity.params.get("endpoint")
        if endpoint is None:
            return Placement(EndpointKind.IN_PROCESS, "local", None, False)
        return Placement(EndpointKind.CLOUD, "unknown", str(endpoint), False)


@dataclass(frozen=True)
class Placement:
    endpoint_kind: EndpointKind
    region: str
    endpoint: str | None
    declared: bool

    @property
    def networked(self) -> bool:
        return self.endpoint_kind is not EndpointKind.IN_PROCESS


class ManifestRegistry:
    def __init__(self, manifests: list[ModelManifest]) -> None:
        by_key: dict[tuple[str, int], ModelManifest] = {}
        for manifest in manifests:
            key = (manifest.manifest_id, manifest.version)
            existing = by_key.get(key)
            if existing is not None and existing.digest != manifest.digest:
                raise ManifestError(
                    "manifest_conflict",
                    f"{manifest.manifest_id}@{manifest.version} is defined twice with "
                    "different content",
                )
            by_key[key] = manifest
        self._by_key = by_key

    @classmethod
    def from_files(cls, paths: list[Path]) -> ManifestRegistry:
        manifests: list[ModelManifest] = []
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ManifestError("manifest_unreadable", f"{path.name}: {exc}") from exc
            if not isinstance(data, dict) or data.get("schema") != MANIFEST_SCHEMA:
                raise ManifestError(
                    "manifest_unreadable", f"{path.name}: expected schema {MANIFEST_SCHEMA}"
                )
            try:
                manifests.extend(ModelManifest.model_validate(item) for item in data["manifests"])
            except (KeyError, TypeError, ValidationError) as exc:
                raise ManifestError("manifest_invalid", f"{path.name}: {exc}") from exc
        return cls(manifests)

    def all(self) -> list[ModelManifest]:
        return sorted(self._by_key.values(), key=lambda m: (m.stage, m.manifest_id, m.version))

    def get(self, manifest_id: str, version: int) -> ModelManifest | None:
        return self._by_key.get((manifest_id, version))

    def candidates(self, stage: str, identity: ProviderIdentity) -> list[ModelManifest]:
        params = dict(identity.params)
        return [
            manifest
            for manifest in self._by_key.values()
            if manifest.stage == stage
            and manifest.provider == identity.provider
            and manifest.model == identity.model
            and manifest.identity_params == params
        ]

    def resolve(
        self,
        stage: str,
        identity: ProviderIdentity,
        *,
        strict: bool,
        pin: tuple[str, int] | None = None,
    ) -> ResolvedManifest:
        if pin is not None:
            pinned = self.get(*pin)
            if pinned is None or pinned.stage != stage:
                raise ManifestError(
                    "manifest_pin_unknown", f"Pinned manifest {pin[0]}@{pin[1]} is not registered"
                )
            if pinned not in self.candidates(stage, identity):
                raise ManifestError(
                    "model_not_qualified",
                    f"The configured {stage} adapter does not match pinned manifest "
                    f"{pin[0]}@{pin[1]}",
                )
            usable = [pinned]
        else:
            usable = self.candidates(stage, identity)
        usable = [m for m in usable if m.status is not ManifestStatus.RETIRED]
        if strict:
            usable = [m for m in usable if m.status is ManifestStatus.QUALIFIED]
        if usable:
            return ResolvedManifest(stage, max(usable, key=lambda m: m.version), identity)
        if strict:
            raise ManifestError(
                "model_not_qualified",
                f"The configured {stage} model has no qualified manifest",
            )
        return ResolvedManifest(stage, None, identity)


def parse_pins(value: str) -> dict[str, tuple[str, int]]:
    """``stage=manifest_id@version`` entries, comma separated."""
    pins: dict[str, tuple[str, int]] = {}
    for entry in filter(None, (part.strip() for part in value.split(","))):
        stage, _, target = entry.partition("=")
        manifest_id, _, version = target.partition("@")
        if stage not in STAGES or not manifest_id or not version.isdigit() or stage in pins:
            raise ValueError(f"Invalid MODEL_MANIFEST_PINS entry {entry!r}")
        pins[stage] = (manifest_id, int(version))
    return pins


def parse_rate_limits(value: str) -> dict[str, int]:
    """``PROVIDER_RATE_LIMITS``: ``manifest_id=N`` calls per minute, comma
    separated (WP08-T05)."""
    limits: dict[str, int] = {}
    for entry in filter(None, (part.strip() for part in value.split(","))):
        manifest_id, _, count = entry.partition("=")
        manifest_id, count = manifest_id.strip(), count.strip()
        if not is_manifest_id(manifest_id) or not count.isdigit() or manifest_id in limits:
            raise ValueError(f"Invalid PROVIDER_RATE_LIMITS entry {entry!r}")
        if int(count) < 1:
            raise ValueError(f"PROVIDER_RATE_LIMITS for {manifest_id} must be positive")
        limits[manifest_id] = int(count)
    return limits


def manifest_paths(settings) -> list[Path]:
    extra = [Path(p.strip()) for p in settings.model_manifest_paths.split(",") if p.strip()]
    return [BUILTIN_MANIFESTS, *extra]


@lru_cache(maxsize=8)
def _cached_registry(paths: tuple[str, ...]) -> ManifestRegistry:
    return ManifestRegistry.from_files([Path(p) for p in paths])


def load_registry(settings) -> ManifestRegistry:
    """Registry of the builtin and configured manifest files (cached per path
    list; workers re-read manifests on restart)."""
    return _cached_registry(tuple(str(p) for p in manifest_paths(settings)))


def is_strict(settings) -> bool:
    if settings.model_manifest_strict is not None:
        return bool(settings.model_manifest_strict)
    return settings.environment not in {"development", "test"}


def resolve_bundle(bundle, settings) -> dict[str, ResolvedManifest]:
    """Preflight of a provider bundle; raises a non-retryable ``ProviderError``."""
    try:
        registry = load_registry(settings)
        pins = parse_pins(settings.model_manifest_pins)
        strict = is_strict(settings)
        return {
            stage: registry.resolve(
                stage, getattr(bundle, stage).identity(), strict=strict, pin=pins.get(stage)
            )
            for stage in STAGES
        }
    except ManifestError as exc:
        raise ProviderError(exc.code, str(exc), retryable=False) from exc
    except ValueError as exc:
        raise ProviderError("manifest_invalid", str(exc), retryable=False) from exc


def resolve_fallbacks(bundle, settings) -> dict[str, list[ResolvedManifest]]:
    """Preflight of the bundle's fallback candidates (WP08-T05): each needs a
    manifest like any adapter (a qualified one in strict mode); pins select
    the primary only.  Raises a non-retryable ``ProviderError``."""
    fallbacks = getattr(bundle, "fallbacks", None) or {}
    try:
        registry = load_registry(settings)
        strict = is_strict(settings)
        return {
            stage: [
                registry.resolve(stage, adapter.identity(), strict=strict) for adapter in adapters
            ]
            for stage, adapters in fallbacks.items()
            if adapters
        }
    except ManifestError as exc:
        raise ProviderError(exc.code, f"Fallback: {exc}", retryable=False) from exc


def manifest_skeleton(stage: str, identity: ProviderIdentity) -> dict[str, object]:
    """A starting point for an operator's manifest (no credential is ever part
    of an identity); every capability field must still be filled in truthfully."""
    params: Mapping[str, Scalar] = identity.params
    endpoint = params.get("endpoint")
    return {
        "manifest_id": f"{stage}.{identity.provider}.{identity.model}".lower().replace("/", "-")[
            :80
        ],
        "version": 1,
        "stage": stage,
        "status": "development",
        "provider": identity.provider,
        "model": identity.model,
        "model_revision": "unpinned",
        "identity_params": dict(params),
        "languages": ["fa"],
        "tasks": [],
        "license": "UNKNOWN",
        "license_policy": "review_required",
        "endpoint_kind": "in_process" if endpoint is None else "private",
        "endpoint": endpoint,
        "region": "local" if endpoint is None else "unknown",
        "offline_capable": endpoint is None,
        "structured_output": "none",
    }
