"""Diarization provider implementations."""

from __future__ import annotations

from backend.providers.base import DiarizationProvider, ProviderIdentity
from backend.providers.schemas import (
    DiarizationRequest,
    DiarizationResult,
    DiarizationStatus,
    ProviderIssue,
)


class UnavailableDiarizationProvider(DiarizationProvider):
    """Explicit response for deployments without a diarization engine."""

    def __init__(self, reason: str = "No diarization backend is configured") -> None:
        self._reason = reason

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="unavailable", model="none")

    async def diarize(self, request: DiarizationRequest) -> DiarizationResult:
        return DiarizationResult(
            provider="unavailable",
            status=DiarizationStatus.UNAVAILABLE,
            issue=ProviderIssue(
                code="diarization_unavailable",
                message=self._reason,
                retryable=False,
            ),
        )
