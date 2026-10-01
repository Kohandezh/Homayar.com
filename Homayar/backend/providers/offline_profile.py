"""Deployment profile O: fully offline AI runtime (AppVision F21, K01-K07).

``evaluate_offline_profile`` proves, from what the registry resolved for each
stage and the workspace AI policy, that every stage (asr, diarization,
intelligence, tts) either runs locally or is explicitly unavailable:

* in-process with a manifest that declares it offline-capable (and, when an
  artifact root is given, whose pinned artifacts verify), or
* a *private* endpoint on a loopback/private/internal host
  (``egress.validate_endpoint(kind="local")``), or
* the explicit ``unavailable`` adapter / no adapter at all.

Anything else is a denial: a cloud endpoint, a private endpoint on a public
host, an unregistered adapter (its placement is unknown), a model not declared
offline-capable, a policy that allows cloud processing, or an artifact that is
missing or differs.  ``offline_permit`` is the per-call guard: it refuses a
non-local endpoint before opening the ordinary egress permit (ADR 0031).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from backend.providers import egress
from backend.providers.artifacts import verify_artifacts
from backend.providers.base import ProviderError
from backend.providers.policy import AiPolicy
from backend.providers.registry import (
    MANIFEST_STAGES,
    TTS_STAGE,
    EndpointKind,
    ManifestError,
    ManifestRegistry,
    ResolvedManifest,
)

PROFILE_ID = "O"
LOCAL = "local"
UNAVAILABLE = "unavailable"
DENIED = "denied"

CLOUD_ENDPOINT = "cloud_endpoint"
ENDPOINT_NOT_LOCAL = "endpoint_not_local"
UNREGISTERED_ADAPTER = "unregistered_adapter"
NOT_OFFLINE_CAPABLE = "not_offline_capable"
POLICY_ALLOWS_CLOUD = "policy_allows_cloud"
ARTIFACTS_UNDECLARED = "artifacts_undeclared"
ARTIFACTS_UNVERIFIED = "artifacts_unverified"
OFFLINE_EGRESS_REFUSED = "offline_egress_refused"


@dataclass(frozen=True)
class StageVerdict:
    stage: str
    status: str
    reason: str | None = None
    manifest_id: str | None = None
    endpoint: str | None = None
    artifacts: dict[str, object] | None = None

    def entry(self) -> dict[str, object]:
        data: dict[str, object] = {
            "status": self.status,
            "reason": self.reason,
            "manifest_id": self.manifest_id,
            "endpoint": self.endpoint,
        }
        if self.artifacts is not None:
            data["artifacts"] = self.artifacts
        return data


@dataclass(frozen=True)
class OfflineProfileReport:
    stages: tuple[StageVerdict, ...]

    @property
    def ok(self) -> bool:
        return all(verdict.status != DENIED for verdict in self.stages)

    @property
    def denials(self) -> tuple[StageVerdict, ...]:
        return tuple(verdict for verdict in self.stages if verdict.status == DENIED)

    def document(self) -> dict[str, object]:
        return {
            "profile": PROFILE_ID,
            "ok": self.ok,
            "stages": {verdict.stage: verdict.entry() for verdict in self.stages},
        }


def _verdict(
    stage: str,
    resolved: ResolvedManifest | None,
    *,
    artifact_root: str | Path | None,
    require_artifacts: bool,
) -> StageVerdict:
    if resolved is None or resolved.identity.provider == "unavailable":
        return StageVerdict(
            stage, UNAVAILABLE, manifest_id=resolved.manifest_id if resolved else None
        )
    manifest = resolved.manifest
    placement = resolved.placement
    if manifest is None:
        return StageVerdict(stage, DENIED, UNREGISTERED_ADAPTER, endpoint=placement.endpoint)

    def deny(reason: str, **extra: object) -> StageVerdict:
        return StageVerdict(
            stage, DENIED, reason, manifest.manifest_id, placement.endpoint, **extra
        )

    if placement.endpoint_kind is EndpointKind.CLOUD:
        return deny(CLOUD_ENDPOINT)
    if placement.endpoint_kind is EndpointKind.PRIVATE:
        if placement.endpoint is None or not egress.is_local_endpoint(placement.endpoint):
            return deny(ENDPOINT_NOT_LOCAL)
        return StageVerdict(stage, LOCAL, None, manifest.manifest_id, placement.endpoint)
    if not manifest.offline_capable:
        return deny(NOT_OFFLINE_CAPABLE)
    report = None
    if require_artifacts and not manifest.artifacts:
        return deny(ARTIFACTS_UNDECLARED)
    if manifest.artifacts:
        if artifact_root is None:
            if require_artifacts:
                return deny(ARTIFACTS_UNVERIFIED)
        else:
            report = verify_artifacts(manifest, artifact_root).document()
            if not report["ok"]:
                return deny(ARTIFACTS_UNVERIFIED, artifacts=report)
    return StageVerdict(stage, LOCAL, None, manifest.manifest_id, None, report)


def evaluate_offline_profile(
    resolved: Mapping[str, ResolvedManifest | None],
    policy: AiPolicy,
    *,
    artifact_root: str | Path | None = None,
    require_artifacts: bool = False,
) -> OfflineProfileReport:
    """``resolved`` maps each stage to what it resolved to (``None`` = no
    adapter: explicitly unavailable).  A stage missing from the mapping is
    unavailable.  ``require_artifacts`` denies an in-process model without
    pinned artifacts (it could fetch weights at runtime)."""
    verdicts: list[StageVerdict] = []
    for stage in MANIFEST_STAGES:
        stage_policy = policy.stage(stage) if stage != TTS_STAGE else None
        if stage_policy is not None and EndpointKind.CLOUD in stage_policy.endpoint_kinds:
            verdicts.append(StageVerdict(stage, DENIED, POLICY_ALLOWS_CLOUD))
            continue
        current = resolved.get(stage)
        verdict = _verdict(
            stage,
            current,
            artifact_root=artifact_root,
            require_artifacts=require_artifacts,
        )
        if (
            verdict.status == LOCAL
            and stage_policy is not None
            and current is not None
            and current.placement.endpoint_kind not in stage_policy.endpoint_kinds
        ):
            # Offline, but the workspace policy refuses it: the stage cannot run.
            verdict = StageVerdict(stage, UNAVAILABLE, "stage_not_permitted", verdict.manifest_id)
        verdicts.append(verdict)
    return OfflineProfileReport(tuple(verdicts))


def resolve_for_offline(
    bundle, registry: ManifestRegistry, *, strict: bool
) -> dict[str, ResolvedManifest | None]:
    """Resolve the bundle's stages (and its optional ``tts`` adapter).  A stage
    that cannot be resolved is returned as an unregistered placement so the
    profile denies it rather than hiding it."""
    result: dict[str, ResolvedManifest | None] = {}
    for stage in MANIFEST_STAGES:
        adapter = getattr(bundle, stage, None)
        if adapter is None:
            result[stage] = None
            continue
        identity = adapter.identity()
        try:
            result[stage] = registry.resolve(stage, identity, strict=strict)
        except ManifestError:
            result[stage] = ResolvedManifest(stage, None, identity)
    return result


@contextmanager
def offline_permit(endpoint: str) -> Iterator[None]:
    """Offline-profile egress permit: only a local endpoint is ever permitted."""
    problem = egress.validate_endpoint(endpoint, kind="local")
    if problem is not None:
        raise ProviderError(
            OFFLINE_EGRESS_REFUSED,
            "The offline deployment profile refuses a non-local AI endpoint",
            retryable=False,
        )
    with egress.permit(endpoint):
        yield
