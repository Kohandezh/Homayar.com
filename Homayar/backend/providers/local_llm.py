"""Local LLM servers behind the OpenAI-compatible analysis adapter.

llama.cpp ``llama-server``, Ollama, vLLM and LocalAI all serve
``POST /v1/chat/completions``; ``OpenAICompatibleIntelligenceProvider`` talks
to them unchanged (no API key means no ``Authorization`` header).  Whether a
given server/model honours ``response_format: json_schema`` strictly is a
qualification question, not something this module asserts.

``local_llm_manifest_template`` returns a *development* manifest document for
an operator manifest file (``MODEL_MANIFEST_PATHS``): identity parameters are
taken from the real adapter so the manifest matches it, the endpoint must be a
local/private host, and licence and revision stay ``UNKNOWN``/``unpinned``
until reviewed.  No builtin manifest is added: endpoints are deployment
specific and nothing here is qualified.
"""

from __future__ import annotations

from backend.providers import egress
from backend.providers.openai_compatible import OpenAICompatibleIntelligenceProvider
from backend.providers.registry import ModelManifest

# Conventional default base URLs (the operator's real URL wins).
LOCAL_LLM_SERVERS: dict[str, str] = {
    "llama_cpp": "http://127.0.0.1:8080/v1",
    "ollama": "http://127.0.0.1:11434/v1",
    "vllm": "http://127.0.0.1:8000/v1",
    "localai": "http://127.0.0.1:8080/v1",
}


def local_llm_manifest_template(
    server: str,
    *,
    model: str,
    base_url: str | None = None,
    languages: list[str] | None = None,
) -> dict[str, object]:
    if server not in LOCAL_LLM_SERVERS:
        raise ValueError(f"unknown local LLM server {server!r}")
    url = base_url or LOCAL_LLM_SERVERS[server]
    adapter = OpenAICompatibleIntelligenceProvider(base_url=url, model=model)
    identity = adapter.identity()
    endpoint = str(identity.params["endpoint"])
    problem = egress.validate_endpoint(endpoint, kind="local")
    if problem is not None:
        raise ValueError(f"a local LLM endpoint must be local: {problem}")
    safe_model = "".join(c if c.isalnum() or c in "._-" else "-" for c in model.lower())
    document: dict[str, object] = {
        "manifest_id": f"intelligence.{server.replace('_', '-')}.{safe_model}"[:80],
        "version": 1,
        "stage": "intelligence",
        "status": "development",
        "provider": identity.provider,
        "model": identity.model,
        "model_revision": "unpinned",
        "identity_params": dict(identity.params),
        "languages": languages or ["fa"],
        "tasks": ["summary", "action_items", "insights"],
        "license": "UNKNOWN",
        "license_policy": "review_required",
        "endpoint_kind": "private",
        "endpoint": endpoint,
        "region": "on-premise",
        "offline_capable": False,
        "structured_output": "json_schema_strict",
        "notes": f"Template for a local {server} server; not qualified.",
    }
    ModelManifest.model_validate(document)  # the template itself is valid
    return document
