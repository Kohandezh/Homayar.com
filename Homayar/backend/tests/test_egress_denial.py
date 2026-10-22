"""WP08-T06: unauthorized egress is blocked in the application (ADR 0031).

An AI adapter can only reach the endpoint the processor's routing decision
permitted for one call; endpoints that are never acceptable (cloud metadata,
link-local, private addresses posing as cloud) are refused in manifests and
adapters; and the F21 scenario — the local provider fails while cloud keys and
a cloud fallback are configured — sends nothing to the cloud unless the owner's
policy allows it.  All traffic goes to an in-memory transport that records it.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.jobs.model_preflight import run_preflight
from backend.jobs.processor import process_job
from backend.providers import egress
from backend.providers.base import ProviderError
from backend.providers.factory import create_provider_bundle
from backend.providers.openai_compatible import OpenAICompatibleIntelligenceProvider
from backend.providers.registry import ModelManifest
from backend.providers.schemas import IntelligenceRequest
from backend.tests.test_ingestion_ledger import _patch_media, _tenant, _upload
from backend.tests.test_provider_routing import CLOUD_EU, _manifest, _policy, _set, _settings_with

LOCAL = "http://llm.internal:8000/v1"
CLOUD = "https://api.cloud-llm.example/v1"
OUTPUT = {
    "summary": "ok",
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
}


class Recorder:
    """Every request, by host; the local endpoint is down, the cloud answers."""

    def __init__(self) -> None:
        self.hosts: list[str] = []
        self.bodies: dict[str, list[str]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.hosts.append(host)
        self.bodies.setdefault(host, []).append(request.content.decode())
        if host == "llm.internal":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(OUTPUT)}}]})


def _adapter(base_url: str, recorder: Recorder) -> OpenAICompatibleIntelligenceProvider:
    return OpenAICompatibleIntelligenceProvider(
        base_url=base_url,
        model="m",
        client=httpx.AsyncClient(transport=httpx.MockTransport(recorder)),
    )


# --- the permit -----------------------------------------------------------------------


def test_the_permit_is_exact_and_scoped():
    endpoint = "https://llm.example.test/v1/chat/completions"
    with pytest.raises(ProviderError) as refused:
        egress.check(endpoint)
    assert refused.value.code == egress.EGRESS_NOT_PERMITTED and not refused.value.retryable
    with egress.permit(endpoint):
        egress.check(endpoint)
        egress.check("HTTPS://LLM.example.test:443/v1/chat/completions/")  # same endpoint
        for other in (
            "https://llm.example.test/v1/embeddings",
            "https://llm.example.test:8443/v1/chat/completions",
            "http://llm.example.test/v1/chat/completions",
            "https://evil.example/v1/chat/completions",
        ):
            with pytest.raises(ProviderError):
                egress.check(other)
    with pytest.raises(ProviderError):
        egress.check(endpoint)  # the permit ended with its block


def test_an_adapter_without_a_matching_permit_sends_nothing():
    recorder = Recorder()
    adapter = _adapter(CLOUD, recorder)
    request = IntelligenceRequest(transcript_text="Private words")
    with pytest.raises(ProviderError) as refused:
        asyncio.run(adapter.analyze(request))
    assert refused.value.code == egress.EGRESS_NOT_PERMITTED
    with egress.permit(f"{LOCAL}/chat/completions"):  # permitted: another endpoint
        with pytest.raises(ProviderError):
            asyncio.run(adapter.analyze(request))
    assert recorder.hosts == []


# --- endpoint policy --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "kind", "allowed"),
    [
        ("https://api.cloud-llm.example/v1", "cloud", True),
        ("http://api.cloud-llm.example/v1", "cloud", False),  # cloud needs HTTPS
        ("https://10.0.0.5/v1", "cloud", False),  # a private address is not a cloud
        ("https://127.0.0.1/v1", "cloud", False),
        ("https://localhost/v1", "cloud", False),
        ("http://llm.internal:8000/v1", "private", True),  # approved internal endpoint
        ("http://10.0.0.5:8000/v1", "private", True),
        ("http://127.0.0.1:8000/v1", "private", True),
        ("http://169.254.169.254/latest", "private", False),  # cloud metadata
        ("http://metadata.google.internal/v1", "private", False),
        ("http://100.100.100.200/v1", "private", False),
        ("http://[fd00:ec2::254]/v1", "private", False),
        ("http://[fe80::1]/v1", "private", False),
        ("http://0.0.0.0:8000/v1", "private", False),
        ("https://user:pass@api.cloud-llm.example/v1", "cloud", False),
        ("https://api.cloud-llm.example/v1?key=x", "cloud", False),
        ("ftp://api.cloud-llm.example/v1", None, False),
    ],
)
def test_the_endpoint_policy(url, kind, allowed):
    assert (egress.validate_endpoint(url, kind=kind) is None) is allowed


def test_manifests_and_adapters_refuse_unsafe_endpoints():
    base = {
        "manifest_id": "test.unsafe",
        "version": 1,
        "stage": "intelligence",
        "status": "development",
        "provider": "openai_compatible",
        "model": "m",
        "model_revision": "r1",
        "languages": ["*"],
        "license": "MIT",
        "license_policy": "approved",
        "region": "eu-central",
        "offline_capable": False,
    }
    with pytest.raises(ValueError, match="public host"):
        ModelManifest.model_validate(
            {**base, "endpoint_kind": "cloud", "endpoint": "https://10.0.0.5/v1"}
        )
    with pytest.raises(ValueError, match="metadata"):
        ModelManifest.model_validate(
            {**base, "endpoint_kind": "private", "endpoint": "http://169.254.169.254/v1"}
        )
    ModelManifest.model_validate({**base, "endpoint_kind": "private", "endpoint": LOCAL})
    with pytest.raises(ValueError, match="refused"):
        OpenAICompatibleIntelligenceProvider(base_url="http://169.254.169.254/v1", model="m")


# --- F21: local failure with cloud keys configured -------------------------------------------


def _configured(settings, tmp_path, recorder: Recorder):
    base = settings.model_copy(
        update={
            "allow_mock_providers": True,
            "asr_provider": "mock",
            "diarization_provider": "mock",
            "intelligence_provider": "openai_compatible",
            "llm_provider": "auto",
            "llm_model": "local-model",
            "local_ai_base_url": LOCAL,
            # Cloud credentials are present, as in a mixed deployment.
            "zai_api_key": "zai-key-present",
            "gapgpt_api_key": "gapgpt-key-present",
            "llm_fallback_base_url": CLOUD,
            "llm_fallback_model": "cloud-model",
            "llm_fallback_api_key": "cloud-key-present",
        }
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    bundle = create_provider_bundle(base, intelligence_client=client)
    (fallback,) = bundle.fallbacks["intelligence"]
    configured = _settings_with(
        base,
        tmp_path,
        [
            _manifest(
                "intelligence",
                bundle.intelligence.identity(),
                kind="private",
                region="customer-dc",
                manifest_id="test.llm.local",
            ),
            _manifest(
                "intelligence",
                fallback.identity(),
                kind="cloud",
                region="eu-central",
                manifest_id="test.llm.cloud",
            ),
        ],
    )
    return configured, bundle


def test_local_failure_with_cloud_keys_configured_sends_nothing_to_the_cloud(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "egress-f21")
    recorder = Recorder()
    configured, bundle = _configured(settings, tmp_path, recorder)
    assert bundle.intelligence.identity().params["endpoint"].startswith(LOCAL)

    def run(key: str) -> dict:
        accepted = _upload(client, tenant, key=key, language="en")
        assert accepted.status_code == 202, accepted.text
        return process_job(
            accepted.json()["job"]["id"], worker_id="w", settings=configured, providers=bundle
        )

    # 1. Default policy: the local endpoint is down; retry later, no cloud.
    first = run("f21-default")
    assert first["status"] == "retry_scheduled" and first["code"] == "intelligence_unavailable"
    # 2. Fallback approved, but the policy still allows private endpoints only.
    _set(client, tenant, _policy(intelligence={"fallback": True}), version=0)
    assert run("f21-fallback-private")["status"] == "retry_scheduled"
    # 3. Cloud allowed in its region, but no fallback approved.
    regions = {"regions": ["customer-dc", "eu-central"], "fallback": False}
    _set(client, tenant, _policy(intelligence={**CLOUD_EU, **regions}), version=1)
    assert run("f21-cloud-no-fallback")["status"] == "retry_scheduled"
    assert set(recorder.hosts) == {"llm.internal"} and len(recorder.hosts) == 3
    # No permit outlives an attempt.
    with pytest.raises(ProviderError):
        egress.check(f"{CLOUD}/chat/completions")

    # 4. Control: only when the owner allows both cloud and fallback does the
    # cloud endpoint receive a request, and then only redacted text.
    _set(client, tenant, _policy(intelligence={**CLOUD_EU, **regions, "fallback": True}), version=2)
    fourth = run("f21-cloud-fallback")
    assert fourth["status"] in {"completed", "partial"}, fourth
    assert recorder.hosts.count("api.cloud-llm.example") == 1
    # Neither ZAI nor GapGPT was ever contacted: the local endpoint wins by
    # configuration, and nothing falls back to them.
    assert not {"api.z.ai", "gapgpt.app"} & set(recorder.hosts)


def test_the_preflight_lists_the_egress_allowlist(settings, tmp_path):
    configured, _bundle = _configured(settings, tmp_path, Recorder())
    code, report = run_preflight(configured)
    assert code == 0, report
    assert [
        (e["candidate"], e["endpoint_kind"], e["host"], e["port"]) for e in report["egress"]
    ] == [
        ("primary", "private", "llm.internal", 8000),
        ("fallback", "cloud", "api.cloud-llm.example", 443),
    ]
    assert "key-present" not in json.dumps(report)


def test_the_egress_allowlist_mode_prints_proxy_lines_and_refuses_cloud_when_air_gapped(
    settings, tmp_path, monkeypatch, capsys
):
    from backend.jobs import model_preflight

    configured, _bundle = _configured(settings, tmp_path, Recorder())
    monkeypatch.setattr(model_preflight, "get_settings", lambda: configured)

    assert model_preflight.main(["--egress-allowlist"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "api.cloud-llm.example:443  # intelligence fallback cloud",
        "llm.internal:8000  # intelligence primary private",
    ]
    assert model_preflight.main(["--egress-allowlist", "--require-no-cloud"]) == 3
    captured = capsys.readouterr()
    assert "api.cloud-llm.example:443" in captured.err
    assert "key-present" not in captured.out + captured.err

    # Without the cloud fallback the air-gapped check passes.
    private_only = configured.model_copy(
        update={"llm_fallback_base_url": "", "llm_fallback_model": "", "llm_fallback_api_key": ""}
    )
    monkeypatch.setattr(model_preflight, "get_settings", lambda: private_only)
    assert model_preflight.main(["--egress-allowlist", "--require-no-cloud"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["llm.internal:8000  # intelligence primary private"]

