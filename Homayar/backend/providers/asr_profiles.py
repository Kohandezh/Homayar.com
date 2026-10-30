"""Named local Whisper configurations (HOM-39, F25).

A profile picks the faster-whisper model, device and compute type together,
so an operator sets one name (``ASR_PROFILE``) instead of three values that
must agree. Each profile names the builtin manifest whose identity equals its
own, so manifest routing finds it. A profile is not a qualification: every
manifest it names stays ``development`` until an operator qualifies it.

``gpu-p40-large-v3`` uses ``int8_float32`` because a Tesla P40 (compute
capability 6.1) runs int8 but has very slow FP16. CTranslate2 converts every
int8 variant to ``int8_float32`` and ``float16`` to ``float32`` on that GPU
(see ADR 0044).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class ASRProfile:
    name: str
    model: str  # faster-whisper model name
    device: str  # "cpu" or "cuda"
    compute_type: str
    manifest_id: str  # a builtin manifest whose identity equals this profile's identity
    device_index: tuple[int, ...] = (0,)
    cpu_threads: int = 0  # 0 = library default
    notes: str = ""


_PROFILES = (
    ASRProfile(
        name="cpu-base",
        model="base",
        device="cpu",
        compute_type="int8",
        manifest_id="asr.local-whisper.base",
        notes="Smallest multilingual model. Today's default model.",
    ),
    ASRProfile(
        name="cpu-small",
        model="small",
        device="cpu",
        compute_type="int8",
        manifest_id="asr.local-whisper.small",
    ),
    ASRProfile(
        name="cpu-medium",
        model="medium",
        device="cpu",
        compute_type="int8",
        manifest_id="asr.local-whisper.medium",
    ),
    ASRProfile(
        name="cpu-large-v3-turbo",
        model="large-v3-turbo",
        device="cpu",
        compute_type="int8",
        manifest_id="asr.local-whisper.large-v3-turbo",
        notes="large-v3 encoder with a 4-layer decoder: faster decoding.",
    ),
    ASRProfile(
        name="cpu-large-v3",
        model="large-v3",
        device="cpu",
        compute_type="int8",
        manifest_id="asr.local-whisper.large-v3",
    ),
    ASRProfile(
        name="gpu-p40-large-v3",
        model="large-v3",
        device="cuda",
        compute_type="int8_float32",
        manifest_id="asr.local-whisper.large-v3-int8-float32",
        notes="Tesla P40 (compute capability 6.1): int8 weights, float32 for the other layers.",
    ),
)

ASR_PROFILES: Mapping[str, ASRProfile] = MappingProxyType(
    {profile.name: profile for profile in _PROFILES}
)


def get_asr_profile(name: str) -> ASRProfile:
    profile = ASR_PROFILES.get(name)
    if profile is None:
        raise ValueError(
            f"Unknown ASR profile {name!r}; valid profiles: {', '.join(sorted(ASR_PROFILES))}"
        )
    return profile


def list_asr_profiles() -> tuple[ASRProfile, ...]:
    return tuple(sorted(ASR_PROFILES.values(), key=lambda profile: profile.name))
