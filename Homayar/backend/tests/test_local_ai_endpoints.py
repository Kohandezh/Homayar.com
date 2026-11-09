"""HOM-351 U2: local embedding and reranker endpoints behind the registry.

A real fake OpenAI-compatible server (stdlib ``http.server`` on 127.0.0.1,
port 0, in a thread) serves the chat, embeddings and rerank routes and
records every request (path, headers, JSON body); the adapters are built
end-to-end through the factory.  Error, malformed-body and empty-input
cases use ``httpx.MockTransport``.  No network, model, GPU, PostgreSQL or
Redis is used, and every ``parametrize`` value is fixed (CI runs xdist).
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from backend.config import Settings
from backend.providers import egress
from backend.providers.base import (
    EmbeddingProvider,
    ProviderError,
    RerankHit,
    RerankProvider,
)
from backend.providers.embeddings import (
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleRerankProvider,
)
from backend.providers.factory import (
    create_embedding_provider,
    create_intelligence_provider,
    create_rerank_provider,
)
from backend.providers.host_checks import HostOptions, HostProbes, run_host_checks
from backend.providers.local_llm import (
    local_embedding_manifest_template,
    local_rerank_manifest_template,
)
from backend.providers.offline_profile import UNAVAILABLE, evaluate_offline_profile
from backend.providers.policy import DEFAULT_POLICY
from backend.providers.registry import (
    EMBEDDING_STAGE,
    MANIFEST_STAGES,
    RERANK_STAGE,
    ManifestError,
    ManifestRegistry,
    ModelManifest,
    parse_pins,
)
from backend.providers.schemas import IntelligenceRequest

EMBED_MODEL = "test-embed-model"
RERANK_MODEL = "test-rerank-model"
CHAT_MODEL = "test-chat-model"

# A structured answer the strict chat adapter accepts (all fields "not_stated").
_CHAT_CONTENT = json.dumps(
    {
        "summary": "خلاصه آزمایشی",
        "summary_source_turn_indexes": [],
        "action_items": [],
        "insights": [],
        "summary_fields": {
            "purpose": {
                "status": "not_stated",
                "text": None,
                "basis": None,
                "source_turn_indexes": [],
            },
            "resolution": {
                "status": "not_stated",
                "text": None,
                "basis": None,
                "source_turn_indexes": [],
            },
            "next_steps": {
                "status": "not_stated",
                "text": None,
                "basis": None,
                "source_turn_indexes": [],
            },
        },
        "decisions": [],
        "key_moments": [],
        "extraction": {
            name: {"status": "not_stated", "values": []}
            for name in ("intent", "topic", "entity", "outcome")
        },
    },
    ensure_ascii=False,
)


class _FakeOpenAIServer(ThreadingHTTPServer):
    """Records every request; answers the three routes deterministically."""

    daemon_threads = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.requests: list[dict] = []


class _Handler(BaseHTTPRequestHandler):
    server: _FakeOpenAIServer

    def log_message(self, *args: object) -> None:  # no stderr noise in CI
        return None

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        record = {
            "path": self.path,
            "headers": dict(self.headers.items()),
            "body": json.loads(raw) if raw else None,
        }
        self.server.requests.append(record)
        body = record["body"] or {}
        if self.path == "/v1/chat/completions":
            payload: object = {"choices": [{"message": {"content": _CHAT_CONTENT}}]}
        elif self.path == "/v1/embeddings":
            count = len(body["input"])
            # Entries arrive reversed; each carries its own index and a
            # vector that encodes it, so "order by index" is observable.
            data = [
                {"index": count - 1 - i, "embedding": [float(count - 1 - i), 1.0]}
                for i in range(count)
            ]
            payload = {"data": data}
        elif self.path == "/v1/rerank":
            count = len(body["documents"])
            results = [
                {"index": count - 1 - i, "relevance_score": (count - 1 - i) / 10}
                for i in range(count)
            ]
            payload = {"results": results}
        else:
            self.send_response(404)
            self.end_headers()
            return
        content = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


@pytest.fixture(scope="module")
def _server() -> _FakeOpenAIServer:
    server = _FakeOpenAIServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture()
def server(_server: _FakeOpenAIServer) -> _FakeOpenAIServer:
    _server.requests.clear()
    return _server


@pytest.fixture()
def base_url(server: _FakeOpenAIServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}/v1"


def run(coro):
    return asyncio.run(coro)


def _settings(**overrides) -> Settings:
    values: dict = {"environment": "test"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _mock(handler, *, model: str, base_url: str = "http://embedding.test/v1"):
    """A direct adapter on an in-memory transport (no real request)."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleEmbeddingProvider(
        base_url=base_url, model=model, client=client, timeout=1
    )


# --- the fake server serves all three routes through the factory ---------------


def test_chat_completions_still_reaches_the_fake_server(server, base_url: str) -> None:
    settings = _settings(local_ai_base_url=base_url, llm_model=CHAT_MODEL)
    provider = create_intelligence_provider(settings)
    endpoint = str(provider.identity().params["endpoint"])
    assert endpoint == f"{base_url}/chat/completions"
    with egress.permit(endpoint):
        result = run(provider.analyze(IntelligenceRequest(transcript_text="متن")))
    assert result.model == CHAT_MODEL and result.summary == "خلاصه آزمایشی"
    assert server.requests[0]["path"] == "/v1/chat/completions"
    assert server.requests[0]["body"]["model"] == CHAT_MODEL


def test_embedding_end_to_end_through_factory(server, base_url: str) -> None:
    settings = _settings(local_embedding_base_url=base_url, local_embedding_model=EMBED_MODEL)
    provider = create_embedding_provider(settings)
    assert isinstance(provider, EmbeddingProvider)
    assert isinstance(provider, OpenAICompatibleEmbeddingProvider)
    identity = provider.identity()
    assert identity.model == EMBED_MODEL
    assert identity.params["endpoint"] == f"{base_url}/embeddings"
    texts = ["اول", "دوم", "سوم"]
    with egress.permit(str(identity.params["endpoint"])):
        vectors = run(provider.embed(texts))
    assert vectors == [[0.0, 1.0], [1.0, 1.0], [2.0, 1.0]]  # in index order
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request["path"] == "/v1/embeddings"
    assert request["body"] == {"model": EMBED_MODEL, "input": texts}
    assert "Authorization" not in request["headers"]


def test_rerank_end_to_end_through_factory(server, base_url: str) -> None:
    settings = _settings(local_rerank_base_url=base_url, local_rerank_model=RERANK_MODEL)
    provider = create_rerank_provider(settings)
    assert isinstance(provider, RerankProvider)
    assert isinstance(provider, OpenAICompatibleRerankProvider)
    identity = provider.identity()
    assert identity.model == RERANK_MODEL
    assert identity.params["endpoint"] == f"{base_url}/rerank"
    documents = ["سند صفر", "سند یک", "سند دو"]
    with egress.permit(str(identity.params["endpoint"])):
        hits = run(provider.rerank("پرسش", documents, top_n=2))
    # top_n is sent to the server; the answer comes back sorted, not truncated.
    assert hits == [
        RerankHit(index=2, score=0.2),
        RerankHit(index=1, score=0.1),
        RerankHit(index=0, score=0.0),
    ]
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request["path"] == "/v1/rerank"
    assert request["body"] == {
        "model": RERANK_MODEL,
        "query": "پرسش",
        "documents": documents,
        "top_n": 2,
    }
    assert "Authorization" not in request["headers"]


def test_rerank_top_n_omitted_when_not_given(server, base_url: str) -> None:
    provider = create_rerank_provider(
        _settings(local_rerank_base_url=base_url, local_rerank_model=RERANK_MODEL)
    )
    endpoint = str(provider.identity().params["endpoint"])
    with egress.permit(endpoint):
        run(provider.rerank("q", ["a"]))
    assert server.requests[0]["body"] == {
        "model": RERANK_MODEL,
        "query": "q",
        "documents": ["a"],
    }


# --- the base-URL rule mirrors _chat_completions_url ---------------------------


@pytest.mark.parametrize(
    ("base", "route"),
    [
        ("http://embedding.test/v1", "embeddings"),
        ("http://embedding.test/v1/", "embeddings"),
        ("http://embedding.test", "embeddings"),
        ("http://rerank.test:8080/v1", "rerank"),
        ("http://rerank.test:8080/v1/", "rerank"),
        ("http://rerank.test:8080", "rerank"),
    ],
)
def test_url_rule_appends_the_route_like_chat(base: str, route: str) -> None:
    provider = (
        OpenAICompatibleEmbeddingProvider(base_url=base, model=EMBED_MODEL)
        if route == "embeddings"
        else OpenAICompatibleRerankProvider(base_url=base, model=RERANK_MODEL)
    )
    assert provider.identity().params["endpoint"] == f"{base.rstrip('/')}/{route}"


def test_base_url_with_credentials_or_bad_scheme_is_refused() -> None:
    for bad in ("https://user:pw@embedding.test/v1", "ftp://embedding.test/v1", "   "):
        with pytest.raises(ValueError):
            OpenAICompatibleEmbeddingProvider(base_url=bad, model=EMBED_MODEL)
        with pytest.raises(ValueError):
            OpenAICompatibleRerankProvider(base_url=bad, model=RERANK_MODEL)


# --- Authorization header ------------------------------------------------------


@pytest.mark.parametrize("kind", ["embedding", "rerank"])
def test_bearer_header_only_when_api_key_set(kind: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        if kind == "embedding":
            count = len(body["input"])
            content = {"data": [{"index": i, "embedding": [0.5]} for i in range(count)]}
        else:
            content = {"results": [{"index": 0, "relevance_score": 1.0}]}
        return httpx.Response(200, json=content)

    def provider(api_key: str):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        if kind == "embedding":
            return OpenAICompatibleEmbeddingProvider(
                base_url="http://embedding.test/v1",
                model=EMBED_MODEL,
                api_key=api_key,
                client=client,
            )
        return OpenAICompatibleRerankProvider(
            base_url="http://embedding.test/v1",
            model=RERANK_MODEL,
            api_key=api_key,
            client=client,
        )

    with_key = provider("test-key")
    endpoint = str(with_key.identity().params["endpoint"])
    with egress.permit(endpoint):
        if kind == "embedding":
            run(with_key.embed(["a"]))
        else:
            run(with_key.rerank("q", ["a"]))
    assert seen[-1].headers["Authorization"] == "Bearer test-key"

    no_key = provider("")
    with egress.permit(endpoint):
        if kind == "embedding":
            run(no_key.embed(["a"]))
        else:
            run(no_key.rerank("q", ["a"]))
    assert "Authorization" not in seen[-1].headers


# --- empty input sends no request ----------------------------------------------


def test_embedding_empty_texts_return_empty_without_request() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"data": []})

    provider = _mock(handler, model=EMBED_MODEL)
    assert run(provider.embed([])) == [] and calls == []


def test_rerank_empty_documents_return_empty_without_request() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"results": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleRerankProvider(
        base_url="http://rerank.test/v1", model=RERANK_MODEL, client=client
    )
    assert run(provider.rerank("q", [])) == [] and calls == []


# --- factory: unconfigured, and no cloud fallback ------------------------------


@pytest.mark.parametrize("base_url", ["", "   "])
def test_embedding_factory_unconfigured_without_base_url(base_url: str) -> None:
    with pytest.raises(ProviderError) as exc:
        create_embedding_provider(_settings(local_embedding_base_url=base_url))
    assert exc.value.code == "embedding_provider_unconfigured"
    assert exc.value.retryable is False


@pytest.mark.parametrize("model", ["", "   "])
def test_embedding_factory_unconfigured_without_model(model: str) -> None:
    settings = _settings(
        local_embedding_base_url="http://embedding.test/v1", local_embedding_model=model
    )
    with pytest.raises(ProviderError) as exc:
        create_embedding_provider(settings)
    assert exc.value.code == "embedding_provider_unconfigured"


@pytest.mark.parametrize("base_url", ["", "   "])
def test_rerank_factory_unconfigured_without_base_url(base_url: str) -> None:
    with pytest.raises(ProviderError) as exc:
        create_rerank_provider(_settings(local_rerank_base_url=base_url))
    assert exc.value.code == "rerank_provider_unconfigured"


@pytest.mark.parametrize("model", ["", "   "])
def test_rerank_factory_unconfigured_without_model(model: str) -> None:
    settings = _settings(local_rerank_base_url="http://rerank.test/v1", local_rerank_model=model)
    with pytest.raises(ProviderError) as exc:
        create_rerank_provider(settings)
    assert exc.value.code == "rerank_provider_unconfigured"


def test_no_cloud_fallback_for_embeddings_but_chat_still_uses_zai() -> None:
    settings = _settings(
        llm_provider="zai",
        zai_api_key="zai-test-key",
        local_ai_base_url="",
        local_embedding_base_url="",
        local_rerank_base_url="",
    )
    with pytest.raises(ProviderError) as exc:
        create_embedding_provider(settings)
    assert exc.value.code == "embedding_provider_unconfigured"
    with pytest.raises(ProviderError) as exc:
        create_rerank_provider(settings)
    assert exc.value.code == "rerank_provider_unconfigured"
    # Unchanged behaviour: the same settings still route chat to zai.
    chat = create_intelligence_provider(settings)
    assert type(chat).__name__ == "OpenAICompatibleIntelligenceProvider"
    assert chat.identity().params["endpoint"] == "https://api.z.ai/v1/chat/completions"


# --- malformed bodies -----------------------------------------------------------


def _embedding_call(body: object, *, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        content = body if isinstance(body, bytes) else json.dumps(body)
        return httpx.Response(status, content=content)

    provider = _mock(handler, model=EMBED_MODEL)
    with egress.permit("http://embedding.test/v1/embeddings"):
        return run(provider.embed(["a", "b", "c"]))


def _vector(index: int, values: list) -> dict:
    return {"index": index, "embedding": values}


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        {"no_data": True},
        {"data": [_vector(0, [0.1]), _vector(1, [0.2])]},
        {"data": [_vector(0, ["x"]), _vector(1, [0.1]), _vector(2, [0.1])]},
        {"data": [_vector(0, [0.1]), _vector(1, [0.1, 0.2]), _vector(2, [0.1])]},
        {"data": [_vector(0, [0.1]), _vector(1, [0.1]), _vector(5, [0.1])]},
        {"data": [_vector(0, [0.1]), _vector(0, [0.2]), _vector(2, [0.1])]},
        b'{"data": [{"index": 0, "embedding": [0.1]}, '
        b'{"index": 1, "embedding": [NaN]}, {"index": 2, "embedding": [0.1]}]}',
        # Three entries for three inputs, so the refusal is the overflow,
        # not the count check.  The huge integer parses as an int and
        # overflows the float conversion (math.isfinite / float).
        b'{"data": [{"index": 0, "embedding": [1'
        + b"0" * 400
        + b']}, {"index": 1, "embedding": [0.1]}, {"index": 2, "embedding": [0.1]}]}',
    ],
    ids=[
        "not-json",
        "missing-data",
        "two-of-three",
        "non-numeric",
        "ragged",
        "index-out-of-range",
        "duplicate-index",
        "nan-value",
        "int-too-large",
    ],
)
def test_embedding_malformed_body_is_not_retryable(body) -> None:
    with pytest.raises(ProviderError) as exc:
        _embedding_call(body)
    assert exc.value.code == "embedding_response_invalid"
    assert exc.value.retryable is False


def _rerank_call(body: object, *, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        content = body if isinstance(body, bytes) else json.dumps(body)
        return httpx.Response(status, content=content)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleRerankProvider(
        base_url="http://rerank.test/v1", model=RERANK_MODEL, client=client
    )
    with egress.permit("http://rerank.test/v1/rerank"):
        return run(provider.rerank("q", ["d0", "d1", "d2"]))


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        {"no_results": True},
        {"results": [{"index": 5, "relevance_score": 0.9}]},
        {"results": [{"index": 0, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.5}]},
        {"results": [{"index": 0, "relevance_score": "high"}]},
        b'{"results": [{"index": 0, "relevance_score": Infinity}]}',
        b'{"results": [{"index": 0, "relevance_score": 1' + b"0" * 400 + b"}]}",
    ],
    ids=[
        "not-json",
        "missing-results",
        "index-out-of-range",
        "duplicate",
        "non-numeric",
        "infinity-score",
        "int-too-large",
    ],
)
def test_rerank_malformed_body_is_not_retryable(body) -> None:
    with pytest.raises(ProviderError) as exc:
        _rerank_call(body)
    assert exc.value.code == "rerank_response_invalid"
    assert exc.value.retryable is False


def test_rerank_all_indexes_in_range_sorted_by_score() -> None:
    hits = _rerank_call({"results": [{"index": i, "relevance_score": i / 10} for i in range(3)]})
    assert hits == [
        RerankHit(index=2, score=0.2),
        RerankHit(index=1, score=0.1),
        RerankHit(index=0, score=0.0),
    ]


# --- HTTP status and transport errors -------------------------------------------


@pytest.mark.parametrize("kind", ["embedding", "rerank"])
@pytest.mark.parametrize(
    ("status", "retryable"),
    [(400, False), (429, True), (503, True)],
)
def test_http_error_retryability(kind: str, status: int, retryable: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="upstream error")

    if kind == "embedding":
        provider = _mock(handler, model=EMBED_MODEL)
        call = provider.embed(["a"])
        code = "embedding_provider_error"
    else:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleRerankProvider(
            base_url="http://rerank.test/v1", model=RERANK_MODEL, client=client
        )
        call = provider.rerank("q", ["a"])
        code = "rerank_provider_error"
    with egress.permit(str(provider.identity().params["endpoint"])):
        with pytest.raises(ProviderError) as exc:
            run(call)
    assert exc.value.code == code
    assert exc.value.retryable is retryable


@pytest.mark.parametrize("kind", ["embedding", "rerank"])
def test_timeout_and_connection_error_are_retryable(kind: str) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    for handler, code in ((timeout, f"{kind}_timeout"), (refused, f"{kind}_unavailable")):
        if kind == "embedding":
            provider = _mock(handler, model=EMBED_MODEL)
            call = provider.embed(["a"])
        else:
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            provider = OpenAICompatibleRerankProvider(
                base_url="http://rerank.test/v1", model=RERANK_MODEL, client=client
            )
            call = provider.rerank("q", ["a"])
        with egress.permit(str(provider.identity().params["endpoint"])):
            with pytest.raises(ProviderError) as exc:
                run(call)
        assert exc.value.code == code
        assert exc.value.retryable is True


def test_requests_without_permit_are_refused_before_the_network() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1]}]})

    provider = _mock(handler, model=EMBED_MODEL)
    with pytest.raises(ProviderError) as exc:
        run(provider.embed(["a"]))
    assert exc.value.code == egress.EGRESS_NOT_PERMITTED and calls == []


def test_deeply_nested_body_is_invalid_not_a_crash() -> None:
    # json.loads gives up on a very deep body (about 1000 levels) before any
    # check of ours runs: the parser must map it, not raise RecursionError.
    deep = b'{"data":' + b"[" * 100000 + b"]" * 100000 + b"}"
    with pytest.raises(ProviderError) as exc:
        _embedding_call(deep)
    assert exc.value.code == "embedding_response_invalid"
    assert exc.value.retryable is False


@pytest.mark.parametrize("kind", ["embedding", "rerank"])
def test_retry_after_header_is_passed_on_retryable_status(kind: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "30"}, text="slow down")

    if kind == "embedding":
        provider = _mock(handler, model=EMBED_MODEL)
        call = provider.embed(["a"])
    else:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleRerankProvider(
            base_url="http://rerank.test/v1", model=RERANK_MODEL, client=client
        )
        call = provider.rerank("q", ["a"])
    with egress.permit(str(provider.identity().params["endpoint"])):
        with pytest.raises(ProviderError) as exc:
            run(call)
    assert exc.value.retryable is True
    assert exc.value.retry_after_seconds == 30


def test_no_retry_after_on_non_retryable_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, headers={"Retry-After": "30"}, text="bad request")

    provider = _mock(handler, model=EMBED_MODEL)
    with egress.permit("http://embedding.test/v1/embeddings"):
        with pytest.raises(ProviderError) as exc:
            run(provider.embed(["a"]))
    assert exc.value.retryable is False
    assert exc.value.retry_after_seconds is None


# --- registry stages and manifest validation ------------------------------------


def test_new_manifest_stages_and_pins_unchanged() -> None:
    assert EMBEDDING_STAGE == "embedding" and RERANK_STAGE == "rerank"
    expected_stages = {"asr", "diarization", "intelligence", "tts", "embedding", "rerank"}
    assert set(MANIFEST_STAGES) >= expected_stages
    document = {
        "manifest_id": "embedding.llama-cpp.test-embed-model",
        "version": 1,
        "stage": "embedding",
        "status": "development",
        "provider": "openai_compatible",
        "model": EMBED_MODEL,
        "model_revision": "unpinned",
        "identity_params": {"endpoint": "http://127.0.0.1:8080/v1/embeddings"},
        "languages": ["fa"],
        "tasks": ["embedding"],
        "license": "UNKNOWN",
        "license_policy": "review_required",
        "endpoint_kind": "private",
        "endpoint": "http://127.0.0.1:8080/v1/embeddings",
        "region": "on-premise",
        "offline_capable": False,
    }
    assert ModelManifest.model_validate(document).stage == "embedding"
    document["manifest_id"] = "rerank.llama-cpp.test-rerank-model"
    document["stage"] = "rerank"
    assert ModelManifest.model_validate(document).stage == "rerank"
    # MODEL_MANIFEST_PINS parsing does not change: only processing stages.
    with pytest.raises(ValueError):
        parse_pins("embedding=embedding.llama-cpp.test-embed-model@1")


def test_embedding_manifest_template_matches_adapter() -> None:
    document = local_embedding_manifest_template("llama_cpp", model=EMBED_MODEL)
    manifest = ModelManifest.model_validate(document)
    adapter = OpenAICompatibleEmbeddingProvider(
        base_url="http://127.0.0.1:8080/v1", model=EMBED_MODEL
    )
    assert manifest.stage == "embedding"
    assert manifest.status.value == "development"
    assert manifest.license == "UNKNOWN"
    assert manifest.model_revision == "unpinned"
    assert manifest.endpoint_kind.value == "private"
    assert manifest.identity_params == dict(adapter.identity().params)
    assert egress.is_local_endpoint(manifest.endpoint)


def test_rerank_manifest_template_matches_adapter() -> None:
    document = local_rerank_manifest_template("llama_cpp", model=RERANK_MODEL)
    manifest = ModelManifest.model_validate(document)
    adapter = OpenAICompatibleRerankProvider(
        base_url="http://127.0.0.1:8080/v1", model=RERANK_MODEL
    )
    assert manifest.stage == "rerank"
    assert manifest.status.value == "development"
    assert manifest.identity_params == dict(adapter.identity().params)
    assert egress.is_local_endpoint(manifest.endpoint)
    with pytest.raises(ValueError):  # a public endpoint is never local
        local_rerank_manifest_template("vllm", model="m", base_url="https://llm.example.com/v1")


def test_strict_resolve_accepts_matching_embedding_manifest(base_url: str) -> None:
    settings = _settings(local_embedding_base_url=base_url, local_embedding_model=EMBED_MODEL)
    provider = create_embedding_provider(settings)
    document = local_embedding_manifest_template("llama_cpp", model=EMBED_MODEL, base_url=base_url)
    # Raise to what strict mode accepts: qualified needs an approved licence
    # policy and a pinned revision.
    document.update(status="qualified", license_policy="approved", model_revision="test-sha-1")
    manifest = ModelManifest.model_validate(document)
    registry = ManifestRegistry([manifest])
    resolved = registry.resolve(EMBEDDING_STAGE, provider.identity(), strict=True)
    assert resolved.manifest_id == manifest.manifest_id


def test_strict_resolve_refuses_manifest_for_another_endpoint(base_url: str) -> None:
    provider = create_embedding_provider(
        _settings(local_embedding_base_url=base_url, local_embedding_model=EMBED_MODEL)
    )
    document = local_embedding_manifest_template(
        "llama_cpp", model=EMBED_MODEL, base_url="http://127.0.0.1:9/v1"
    )
    document.update(status="qualified", license_policy="approved", model_revision="test-sha-1")
    registry = ManifestRegistry([ModelManifest.model_validate(document)])
    with pytest.raises(ManifestError) as exc:
        registry.resolve(EMBEDDING_STAGE, provider.identity(), strict=True)
    assert exc.value.code == "model_not_qualified"


def test_strict_resolve_refuses_manifest_for_another_model(base_url: str) -> None:
    provider = create_embedding_provider(
        _settings(local_embedding_base_url=base_url, local_embedding_model=EMBED_MODEL)
    )
    document = local_embedding_manifest_template(
        "llama_cpp", model="test-other-model", base_url=base_url
    )
    document.update(status="qualified", license_policy="approved", model_revision="test-sha-1")
    registry = ManifestRegistry([ModelManifest.model_validate(document)])
    with pytest.raises(ManifestError) as exc:
        registry.resolve(EMBEDDING_STAGE, provider.identity(), strict=True)
    assert exc.value.code == "model_not_qualified"


# --- offline profile (Ruling 2026-11-10) ----------------------------------------


def test_offline_profile_lists_new_stages_unavailable_and_stays_ok() -> None:
    report = evaluate_offline_profile({}, DEFAULT_POLICY)
    stages = report.document()["stages"]
    assert stages["embedding"]["status"] == UNAVAILABLE
    assert stages["rerank"]["status"] == UNAVAILABLE
    assert report.ok  # no denial: ok stays as it was without the new stages


# --- host checks -----------------------------------------------------------------


def _host_settings(**update) -> Settings:
    values: dict = {
        "environment": "test",
        "database_url": "",
        "redis_url": "",
    }
    values.update(update)
    return Settings(_env_file=None, **values)


def test_host_checks_probe_embedding_and_rerank_when_set() -> None:
    calls: list[tuple[str, int]] = []

    def connect(host: str, port: int, timeout: float) -> None:
        calls.append((host, port))

    settings = _host_settings(
        local_embedding_base_url="http://embedding.internal:8080/v1",
        local_rerank_base_url="http://rerank.internal:8081/v1",
    )
    result = run_host_checks(settings, None, HostOptions(), HostProbes(connect=connect, environ={}))
    check = next(c for c in result["checks"] if c["name"] == "dependency_ports")
    assert check["status"] == "pass"
    assert [(t["name"], t["host"], t["port"]) for t in check["targets"]] == [
        ("local_embedding", "embedding.internal", 8080),
        ("local_rerank", "rerank.internal", 8081),
    ]
    assert calls == [("embedding.internal", 8080), ("rerank.internal", 8081)]


def test_host_checks_skip_unset_embedding_and_rerank() -> None:
    def connect(host: str, port: int, timeout: float) -> None:
        raise AssertionError("nothing to probe")

    result = run_host_checks(
        _host_settings(), None, HostOptions(), HostProbes(connect=connect, environ={})
    )
    check = next(c for c in result["checks"] if c["name"] == "dependency_ports")
    assert check["status"] == "skip"  # not set means not checked, no failure
