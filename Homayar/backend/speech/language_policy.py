"""Which language the ASR stage forces Whisper to use (HOM-39, F25-F28).

An explicit request is honoured as is, after normalization (``fa-IR`` becomes
``fa``, because Whisper only knows base codes). An auto request uses the
language Whisper detected only when it is in the allowed list and its
probability reaches the threshold. Otherwise the policy default is used, so
Persian phone audio is not transcribed as Arabic or Urdu.

Pure: no I/O and no model import. The provider supplies the detection.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

POLICY_VERSION = "asr-language/1"


def normalize_language(value: str | None) -> str | None:
    """Base language code, or ``None`` for an auto request.

    Strip, lower case, ``_`` becomes ``-``, keep the primary subtag.
    """
    if value is None:
        return None
    code = value.strip().lower().replace("_", "-").split("-", 1)[0]
    if not code or code == "auto":
        return None
    return code


def needs_detection(requested: str | None) -> bool:
    return normalize_language(requested) is None


def parse_language_list(value: str) -> tuple[str, ...]:
    """Comma list to normalized codes, in order, without empties or repeats."""
    codes: list[str] = []
    for item in value.split(","):
        code = normalize_language(item)
        if code is not None and code not in codes:
            codes.append(code)
    return tuple(codes)


class LanguageSource(str, Enum):
    REQUESTED = "requested"
    DETECTED = "detected"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class LanguagePolicy:
    allowed: tuple[str, ...] = ("fa", "en")
    default: str = "fa"
    min_probability: float = 0.5

    def __post_init__(self) -> None:
        if not self.allowed:
            raise ValueError("The ASR language policy needs at least one allowed language")
        for code in self.allowed:
            if not isinstance(code, str) or normalize_language(code) != code:
                raise ValueError(f"{code!r} is not a normalized language code")
        if self.default not in self.allowed:
            raise ValueError(
                f"The default ASR language {self.default!r} must be one of {list(self.allowed)}"
            )
        if not 0 <= self.min_probability <= 1:
            raise ValueError("The ASR language probability threshold must be between 0 and 1")

    @classmethod
    def from_settings(cls, settings: Any) -> LanguagePolicy:
        return cls(
            allowed=parse_language_list(settings.asr_allowed_languages),
            default=normalize_language(settings.asr_default_language) or "",
            min_probability=float(settings.asr_language_min_probability),
        )

    def descriptor(self) -> dict[str, Any]:
        """What the checkpoint identity records for an auto request."""
        return {
            "version": POLICY_VERSION,
            "allowed": list(self.allowed),
            "default": self.default,
            "min_probability": self.min_probability,
        }


@dataclass(frozen=True)
class LanguageDecision:
    language: str  # the code Whisper is forced to use
    source: LanguageSource
    requested: str | None  # the normalized request (None for auto)
    detected: str | None = None  # top language of the detection, any language
    probability: float | None = None  # probability of ``detected``
    reason: str | None = None  # None, "low_confidence" or "no_speech"


def decide(
    requested: str | None,
    policy: LanguagePolicy,
    probabilities: Sequence[tuple[str, float]] | None = None,
) -> LanguageDecision:
    """Apply the policy to a request and, for auto, to the detection result."""
    code = normalize_language(requested)
    if code is not None:
        return LanguageDecision(language=code, source=LanguageSource.REQUESTED, requested=code)
    if probabilities is None:
        raise ValueError("An auto language request needs the detection probabilities")

    entries = [
        (normalized, float(probability))
        for normalized, probability in (
            (normalize_language(language), probability) for language, probability in probabilities
        )
        if normalized is not None
    ]
    if not entries:
        return LanguageDecision(
            language=policy.default,
            source=LanguageSource.FALLBACK,
            requested=None,
            reason="no_speech",
        )

    # max() keeps the first entry on a tie.
    detected, top_probability = max(entries, key=lambda entry: entry[1])
    best: dict[str, float] = {}
    for language, probability in entries:
        if language in policy.allowed and probability > best.get(language, -1.0):
            best[language] = probability
    candidate = None
    for language in policy.allowed:  # earlier in ``allowed`` wins a tie
        if language in best and (candidate is None or best[language] > best[candidate]):
            candidate = language
    if candidate is not None and best[candidate] >= policy.min_probability:
        return LanguageDecision(
            language=candidate,
            source=LanguageSource.DETECTED,
            requested=None,
            detected=detected,
            probability=top_probability,
        )
    return LanguageDecision(
        language=policy.default,
        source=LanguageSource.FALLBACK,
        requested=None,
        detected=detected,
        probability=top_probability,
        reason="low_confidence",
    )
