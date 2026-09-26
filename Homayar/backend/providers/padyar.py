"""Padyar AI Gateway: the external adapter boundary (WP08-T04).

Padyar AI Gateway is an external gateway that would sit *behind* Homayar's
provider ports (``ASRProvider``, ``DiarizationProvider``,
``IntelligenceProvider``); product modules only ever see those ports, their
typed requests/results and manifests, never a gateway request format.

Its API contract has not been supplied, so there is no adapter.  Selecting it
is an explicit, typed unconfigured state (``provider_contract_unavailable``):
the model preflight reports it and every job fails visibly before any stage
runs.  A generic OpenAI-compatible adapter pointed at a gateway URL is *not*
a Padyar integration and must not be reported as one.  ``MISSING_INPUTS``
lists what building the adapter requires; none of it may be assumed.
"""

from __future__ import annotations

from backend.providers.base import ProviderError

NAMES = frozenset({"padyar", "padyar_gateway", "padyar_ai_gateway"})
DISPLAY_NAME = "Padyar AI Gateway"

# The contract inputs the adapter needs; recorded in the Session A handoff.
MISSING_INPUTS: tuple[str, ...] = (
    "base URL and endpoint paths per capability",
    "authentication scheme and how credentials are scoped (per deployment or per tenant)",
    "model identifiers and how a model revision is pinned",
    "supported capabilities: text analysis, speech-to-text, diarization, embeddings",
    "structured-output mode (JSON schema strict or not) and its error semantics",
    "usage fields (input/output tokens, audio seconds) in responses",
    "error model, rate limits and Retry-After behaviour",
    "data residency: processing regions, retention and subprocessors",
    "whether the gateway itself is a cloud subprocessor or deployable privately",
)


def contract_unavailable(stage: str) -> ProviderError:
    return ProviderError(
        "provider_contract_unavailable",
        f"The {DISPLAY_NAME} {stage} adapter is not available: its API contract has not "
        "been supplied",
        retryable=False,
    )
