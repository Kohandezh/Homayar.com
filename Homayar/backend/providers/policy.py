"""Per-workspace AI processing policy (WP08-T02, ADR 0027).

A policy says, per stage, where a workspace's data may be processed: which
endpoint kinds (``in_process`` inside the worker, ``private`` endpoints the
deployment or customer operates, ``cloud`` third-party subprocessors), which
regions a networked endpoint may be in, optionally which manifests only, what
text a cloud analysis endpoint may receive, and whether a same-policy fallback
may be used (WP08-T05).  It never holds a credential or a provider URL.

Policies are versioned and append-only per workspace; every processing attempt
records the version it ran under (``run_policy_snapshots``).  A workspace
without a stored version runs under ``DEFAULT_POLICY`` (version 0): in-process
and private endpoints only, no cloud processing until an owner allows it.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.providers.registry import EndpointKind, is_manifest_id

POLICY_SCHEMA = "homayar.ai-policy/1"
DEFAULT_VERSION = 0
_REGION = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")


def _unique_sorted(values: list, *, what: str) -> list:
    if len(set(values)) != len(values):
        raise ValueError(f"{what} must not repeat")
    return sorted(values, key=lambda value: getattr(value, "value", value))


class StagePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Where the stage may run; empty = the stage may not run at all.
    endpoint_kinds: list[EndpointKind] = Field(max_length=3)
    # Regions a networked endpoint may process data in.  A cloud endpoint
    # needs its region listed (explicit approval); for private endpoints an
    # empty list means any region the operator declared.
    regions: list[str] = Field(default_factory=list, max_length=16)
    # Manifest ids the stage may use; empty = any manifest meeting the rest.
    manifests: list[str] = Field(default_factory=list, max_length=32)
    # Same-policy fallback to another eligible candidate (WP08-T05).
    fallback: bool = False

    @field_validator("endpoint_kinds")
    @classmethod
    def _kinds(cls, value: list[EndpointKind]) -> list[EndpointKind]:
        return _unique_sorted(value, what="endpoint_kinds")

    @field_validator("regions")
    @classmethod
    def _regions(cls, value: list[str]) -> list[str]:
        for region in value:
            if not _REGION.fullmatch(region) or region == "unknown":
                raise ValueError(f"invalid region {region!r}")
        return _unique_sorted(value, what="regions")

    @field_validator("manifests")
    @classmethod
    def _manifests(cls, value: list[str]) -> list[str]:
        for manifest_id in value:
            if not is_manifest_id(manifest_id):
                raise ValueError(f"invalid manifest id {manifest_id!r}")
        return _unique_sorted(value, what="manifests")

    @model_validator(mode="after")
    def _cloud_needs_regions(self) -> StagePolicy:
        if EndpointKind.CLOUD in self.endpoint_kinds and not self.regions:
            raise ValueError("allowing cloud endpoints needs the approved regions")
        return self


class AudioStagePolicy(StagePolicy):
    """ASR and diarization receive the recording itself."""


class TextStagePolicy(StagePolicy):
    # What a *cloud* analysis endpoint receives: the transcript redacted with
    # the workspace's classes (all classes when redaction is off), or the
    # original.  In-process and private endpoints follow the workspace's
    # redaction setting (WP07-T06) unchanged.
    cloud_text: Literal["redacted", "original"] = "redacted"


class AiPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asr: AudioStagePolicy
    diarization: AudioStagePolicy
    intelligence: TextStagePolicy

    def stage(self, name: str) -> StagePolicy:
        return getattr(self, name)

    def document(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            {"schema": POLICY_SCHEMA, **self.document()},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_LOCAL = [EndpointKind.IN_PROCESS]
_PRIVATE = [EndpointKind.IN_PROCESS, EndpointKind.PRIVATE]

DEFAULT_POLICY = AiPolicy(
    asr=AudioStagePolicy(endpoint_kinds=_PRIVATE),
    diarization=AudioStagePolicy(endpoint_kinds=_PRIVATE),
    intelligence=TextStagePolicy(endpoint_kinds=_PRIVATE),
)

# Starting points for an owner (the API accepts any valid document).
PRESETS: dict[str, AiPolicy] = {
    "local_only": AiPolicy(
        asr=AudioStagePolicy(endpoint_kinds=_LOCAL),
        diarization=AudioStagePolicy(endpoint_kinds=_LOCAL),
        intelligence=TextStagePolicy(endpoint_kinds=_LOCAL),
    ),
    "private": DEFAULT_POLICY,
}
