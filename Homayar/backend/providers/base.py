"""Provider interfaces and sanitized execution errors."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field

from backend.providers.schemas import (
    ASRRequest,
    ASRResult,
    DiarizationRequest,
    DiarizationResult,
    EmailRequest,
    EmailResult,
    IntelligenceRequest,
    IntelligenceResult,
    SMSRequest,
    SMSResult,
    TTSRequest,
    TTSResult,
)


class ProviderError(Exception):
    """Provider failure safe to expose to application orchestration."""

    def __init__(
        self,
        code: str,
        public_message: str,
        *,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable
        # How long the provider asked callers to wait (Retry-After, WP08-T05);
        # bounded by the adapter, never shortened by the retry policy.
        self.retry_after_seconds = retry_after_seconds


class MockProviderDisabledError(ProviderError):
    def __init__(self) -> None:
        super().__init__(
            "mock_provider_disabled",
            "Mock providers require allow_mock=True",
            retryable=False,
        )


ScalarParam = str | int | float | bool | None


@dataclass(frozen=True)
class ProviderIdentity:
    """What a provider *declares* determines its output (WP06-T03 checkpoints).

    ``provider`` and ``model`` are the stable names the provider also reports
    in its results; ``params`` are the result-affecting settings it was built
    with (compute type, prompt digest, endpoint, ...).  Never a credential.
    A stage checkpoint is reused only when this identity — not just the
    stage name — matches the one that produced it.  ``model="unversioned"``
    is the honest default for adapters that declare nothing more than their
    code path; it names *that code*, not "latest".
    """

    provider: str
    model: str
    params: Mapping[str, ScalarParam] = field(default_factory=dict)

    def as_manifest(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "params": {key: self.params[key] for key in sorted(self.params)},
        }


def code_identity(instance: object) -> str:
    """Import path of the adapter class: the default provider identity."""
    cls = type(instance)
    return f"{cls.__module__}:{cls.__qualname__}"


def text_digest(value: str) -> str:
    """Short, stable digest of a prompt/template/transcript (never the text itself)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def params_digest(value: Mapping[str, object]) -> str:
    return text_digest(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str))


class _IdentifiedProvider:
    """Mixin: the identity a checkpoint records for the adapter (override it)."""

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider=code_identity(self), model="unversioned")


class ASRProvider(_IdentifiedProvider, ABC):
    @abstractmethod
    async def transcribe(self, request: ASRRequest) -> ASRResult:
        raise NotImplementedError

    def language_policy_identity(self, language: str | None) -> dict | None:
        """Language policy a checkpoint records for this request language.

        ``None``: the adapter applies no policy, so the identity is unchanged.
        """
        return None


class DiarizationProvider(_IdentifiedProvider, ABC):
    @abstractmethod
    async def diarize(self, request: DiarizationRequest) -> DiarizationResult:
        raise NotImplementedError


class IntelligenceProvider(_IdentifiedProvider, ABC):
    @abstractmethod
    async def analyze(self, request: IntelligenceRequest) -> IntelligenceResult:
        raise NotImplementedError


class TTSProvider(_IdentifiedProvider, ABC):
    """Speech synthesis.  An adapter that cannot synthesize raises a
    ``ProviderError`` (``tts_unavailable`` ...); it never returns silence or
    placeholder audio as if it were speech (mocks mark ``is_mock``)."""

    @abstractmethod
    async def synthesize(self, request: TTSRequest) -> TTSResult:
        raise NotImplementedError


class SMSProvider(ABC):
    @abstractmethod
    async def send(self, request: SMSRequest) -> SMSResult:
        raise NotImplementedError


class EmailProvider(ABC):
    @abstractmethod
    async def send(self, request: EmailRequest) -> EmailResult:
        raise NotImplementedError
