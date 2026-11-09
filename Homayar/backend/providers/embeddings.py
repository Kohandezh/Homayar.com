"""OpenAI-compatible embedding and reranker adapters (HOM-351, ADR 0047 D5).

They talk to a private OpenAI-compatible server, set by its own base URL
(it may be the same host as ``LOCAL_AI_BASE_URL``): ``POST
<base>/embeddings`` (OpenAI shape) and ``POST <base>/rerank`` (the
llama.cpp ``llama-server`` / Jina / Cohere shape).  No API key means no
``Authorization`` header.  There is no cloud fallback for these stages:
the factory refuses an unconfigured endpoint.

The base-URL rule is the one ``_chat_completions_url`` uses for chat: the
route is appended unless the URL already ends with it.  Every request goes
through ``egress.check`` (WP08-T06): only a routing decision an operator or
the processor approved may leave the process.
"""

from __future__ import annotations

import math
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from backend.providers import egress
from backend.providers.base import (
    EmbeddingProvider,
    ProviderError,
    ProviderIdentity,
    RerankHit,
    RerankProvider,
)
from backend.providers.openai_compatible import retry_after_seconds

DEFAULT_TIMEOUT_SECONDS = 30.0


def _service_url(base_url: str, route: str) -> str:
    """``<base>/embeddings`` or ``<base>/rerank``, the chat URL rule."""
    value = base_url.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("OpenAI-compatible base URL must be an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("OpenAI-compatible base URL must not contain credentials or parameters")
    path = parsed.path.rstrip("/")
    if not path.endswith(f"/{route}"):
        path = f"{path}/{route}"
    endpoint = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    problem = egress.validate_endpoint(endpoint, kind=None)
    if problem is not None:
        raise ValueError(f"OpenAI-compatible base URL refused: {problem}")
    return endpoint


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


async def _post(
    endpoint: str,
    api_key: str,
    payload: dict[str, Any],
    timeout: httpx.Timeout,
    client: httpx.AsyncClient | None,
    *,
    stage: str,
) -> httpx.Response:
    """One POST under the egress permit, with the shared error mapping."""
    egress.check(endpoint)
    headers = _headers(api_key)
    try:
        if client is not None:
            return await client.post(endpoint, headers=headers, json=payload, timeout=timeout)
        async with httpx.AsyncClient(timeout=timeout) as one_off:
            return await one_off.post(endpoint, headers=headers, json=payload)
    except httpx.TimeoutException:
        raise ProviderError(
            f"{stage}_timeout",
            f"{stage.capitalize()} provider timed out",
            retryable=True,
        ) from None
    except httpx.RequestError:
        raise ProviderError(
            f"{stage}_unavailable",
            f"{stage.capitalize()} provider is unavailable",
            retryable=True,
        ) from None


def _raise_status(response: httpx.Response, *, stage: str) -> None:
    if 200 <= response.status_code < 300:
        return
    retryable = response.status_code == 429 or response.status_code >= 500
    raise ProviderError(
        f"{stage}_provider_error",
        f"{stage.capitalize()} provider request failed",
        retryable=retryable,
        retry_after_seconds=(
            retry_after_seconds(response.headers.get("retry-after")) if retryable else None
        ),
    )


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """``POST <base>/embeddings`` with ``{"model": ..., "input": texts}``."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not model.strip():
            raise ValueError("OpenAI-compatible model is required")
        if timeout <= 0:
            raise ValueError("OpenAI-compatible timeout must be positive")
        self._endpoint = _service_url(base_url, "embeddings")
        self._model = model.strip()
        self._api_key = api_key.strip()
        self._timeout = httpx.Timeout(timeout)
        self._client = client

    def identity(self) -> ProviderIdentity:
        # Endpoint (never the key) and model: the same model name behind
        # another endpoint is another provider, so a manifest can pin it.
        return ProviderIdentity(
            provider="openai_compatible",
            model=self._model,
            params={"endpoint": self._endpoint},
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []  # no request for no input
        response = await _post(
            self._endpoint,
            self._api_key,
            {"model": self._model, "input": texts},
            self._timeout,
            self._client,
            stage="embedding",
        )
        _raise_status(response, stage="embedding")
        return self._parse(response, len(texts))

    @staticmethod
    def _parse(response: httpx.Response, count: int) -> list[list[float]]:
        try:
            envelope = response.json()
            data = envelope["data"]
            if not isinstance(data, list) or len(data) != count:
                raise TypeError
            by_index: dict[int, list[float]] = {}
            dimensions: int | None = None
            for item in data:
                index = item["index"]
                vector = item["embedding"]
                if not isinstance(index, int) or isinstance(index, bool) or index in by_index:
                    raise TypeError
                if not isinstance(vector, list) or not vector:
                    raise TypeError
                if dimensions is None:
                    dimensions = len(vector)
                elif len(vector) != dimensions:
                    raise TypeError
                if any(
                    isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                    for v in vector
                ):
                    raise TypeError
                by_index[index] = [float(v) for v in vector]
            if sorted(by_index) != list(range(count)):
                raise TypeError
            return [by_index[i] for i in range(count)]
        except (KeyError, IndexError, TypeError, ValueError, RecursionError, OverflowError):
            raise ProviderError(
                "embedding_response_invalid",
                "Embedding provider returned an invalid response",
                retryable=False,
            ) from None


class OpenAICompatibleRerankProvider(RerankProvider):
    """``POST <base>/rerank`` with the llama.cpp / Jina / Cohere shape."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not model.strip():
            raise ValueError("OpenAI-compatible model is required")
        if timeout <= 0:
            raise ValueError("OpenAI-compatible timeout must be positive")
        self._endpoint = _service_url(base_url, "rerank")
        self._model = model.strip()
        self._api_key = api_key.strip()
        self._timeout = httpx.Timeout(timeout)
        self._client = client

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            provider="openai_compatible",
            model=self._model,
            params={"endpoint": self._endpoint},
        )

    async def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[RerankHit]:
        if not documents:
            return []  # no request for no input
        payload: dict[str, Any] = {
            "model": self._model,
            "query": query,
            "documents": documents,
        }
        if top_n is not None:
            payload["top_n"] = top_n
        response = await _post(
            self._endpoint, self._api_key, payload, self._timeout, self._client, stage="rerank"
        )
        _raise_status(response, stage="rerank")
        return self._parse(response, len(documents))

    @staticmethod
    def _parse(response: httpx.Response, count: int) -> list[RerankHit]:
        try:
            envelope = response.json()
            results = envelope["results"]
            if not isinstance(results, list):
                raise TypeError
            seen: set[int] = set()
            hits: list[RerankHit] = []
            for item in results:
                index = item["index"]
                score = item["relevance_score"]
                if (
                    not isinstance(index, int)
                    or isinstance(index, bool)
                    or not 0 <= index < count
                    or index in seen
                ):
                    raise TypeError
                if (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(score)
                ):
                    raise TypeError
                seen.add(index)
                hits.append(RerankHit(index=index, score=float(score)))
            hits.sort(key=lambda hit: hit.score, reverse=True)
            return hits
        except (KeyError, IndexError, TypeError, ValueError, RecursionError, OverflowError):
            raise ProviderError(
                "rerank_response_invalid",
                "Rerank provider returned an invalid response",
                retryable=False,
            ) from None
