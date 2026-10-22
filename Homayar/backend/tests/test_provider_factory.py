"""Focused production provider factory and structured intelligence tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from backend.config import Settings
from backend.providers import egress
from backend.providers.base import MockProviderDisabledError, ProviderError
from backend.providers.diarization import UnavailableDiarizationProvider
from backend.providers.factory import (
    ProviderFactory,
    create_asr_provider,
    create_diarization_provider,
    create_intelligence_provider,
)
from backend.providers.mocks import (
    MockASRProvider,
    MockDiarizationProvider,
    MockIntelligenceProvider,
)
from backend.providers.openai_compatible import OpenAICompatibleIntelligenceProvider
from backend.providers.schemas import ActionPriority, IntelligenceRequest
from backend.providers.whisper import LocalWhisperProvider


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "local_ai_base_url": "http://intelligence.test/v1",
        "llm_model": "conversation-model",
        "allow_mock_providers": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _structured_content() -> dict[str, Any]:
    return {
        "summary": "The customer requested a revised proposal.",
        "summary_source_turn_indexes": [0],
        "action_items": [
            {
                "description": "Send the revised proposal",
                "owner": "Sales",
                "due_date": "2026-08-20",
                "priority": "high",
                "source_turn_indexes": [1],
            }
        ],
        "insights": [
            {
                "category": "customer_intent",
                "description": "The customer is ready to review terms.",
                "confidence": 0.9,
                "source_turn_indexes": [0, 1],
            }
        ],
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


def _run_provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    transcript: str = "Private transcript",
) -> Any:
    async def run() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleIntelligenceProvider(
                base_url="http://intelligence.test/v1",
                model="conversation-model",
                client=client,
                timeout_seconds=1,
            )
            # Stands in for the processor's routing decision (WP08-T06).
            with egress.permit("http://intelligence.test/v1/chat/completions"):
                return await provider.analyze(IntelligenceRequest(transcript_text=transcript))

    return asyncio.run(run())


def test_openai_compatible_validates_structured_output() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(_structured_content())}},
                ]
            },
        )

    result = _run_provider(handler)

    assert captured["url"] == "http://intelligence.test/v1/chat/completions"
    assert captured["payload"]["temperature"] == 0
    response_format = captured["payload"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"]["additionalProperties"] is False
    assert result.provider == "openai_compatible"
    assert result.model == "conversation-model"
    assert result.is_mock is False
    assert result.action_items[0].priority == ActionPriority.HIGH
    assert result.insights[0].source_turn_indexes == [0, 1]


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        json.dumps(
            {
                "summary": "Summary",
                "summary_source_turn_indexes": [],
                "action_items": [
                    {
                        "description": "Invalid priority",
                        "owner": None,
                        "due_date": None,
                        "priority": "urgent",
                        "source_turn_indexes": [],
                    }
                ],
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
        ),
    ],
)
def test_openai_compatible_rejects_malformed_json_and_schema(content: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    with pytest.raises(ProviderError) as error:
        _run_provider(handler)

    assert error.value.code == "intelligence_invalid_response"
    assert error.value.retryable is False
    assert str(error.value) == "Intelligence provider returned an invalid response"


def test_openai_compatible_sanitizes_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("upstream included private transcript", request=request)

    with pytest.raises(ProviderError) as error:
        _run_provider(handler, transcript="do-not-expose-timeout-transcript")

    assert error.value.code == "intelligence_timeout"
    assert error.value.retryable is True
    assert "private" not in str(error.value).lower()
    assert "transcript" not in str(error.value).lower()


@pytest.mark.parametrize(
    ("status_code", "retryable"),
    [(400, False), (429, True), (503, True)],
)
def test_openai_compatible_sanitizes_non_2xx(status_code: int, retryable: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            text="provider body contains transcript and credentials",
        )

    with pytest.raises(ProviderError) as error:
        _run_provider(handler, transcript="do-not-expose-http-transcript")

    assert error.value.code == "intelligence_provider_error"
    assert error.value.retryable is retryable
    assert str(error.value) == "Intelligence provider request failed"


def test_factory_selects_production_provider_types() -> None:
    bundle = ProviderFactory(_settings()).create_all()

    assert isinstance(bundle.asr, LocalWhisperProvider)
    assert isinstance(bundle.diarization, UnavailableDiarizationProvider)
    assert isinstance(bundle.intelligence, OpenAICompatibleIntelligenceProvider)


def test_factory_allows_mocks_only_in_explicit_non_production_mode() -> None:
    enabled = _settings(
        environment="development",
        allow_mock_providers=True,
        asr_provider="mock_asr",
        diarization_provider="mock_diarization",
        intelligence_provider="mock_intelligence",
    )

    assert isinstance(create_asr_provider(enabled), MockASRProvider)
    assert isinstance(create_diarization_provider(enabled), MockDiarizationProvider)
    assert isinstance(create_intelligence_provider(enabled), MockIntelligenceProvider)

    disabled = _settings(
        environment="staging",
        allow_mock_providers=True,
        asr_provider="mock_asr",
    )
    with pytest.raises(MockProviderDisabledError):
        create_asr_provider(disabled)


@pytest.mark.parametrize(
    ("field", "factory"),
    [
        ("asr_provider", create_asr_provider),
        ("diarization_provider", create_diarization_provider),
        ("intelligence_provider", create_intelligence_provider),
    ],
)
def test_factory_rejects_unknown_provider(
    field: str,
    factory: Callable[[Settings], Any],
) -> None:
    with pytest.raises(ProviderError) as error:
        factory(_settings(**{field: "unknown-provider"}))

    assert error.value.code.startswith("unsupported_")


def test_factory_rejects_unconfigured_openai_compatible_provider() -> None:
    settings = _settings(
        environment="production",
        database_url="postgresql+psycopg://homayar:unused@database.test/homayar?sslmode=require",
        redis_url="rediss://redis.test:6380/0",
        jwt_secret="j" * 48,
        token_pepper="p" * 48,
        secure_cookies=True,
        local_ai_base_url="",
        zai_api_key="",
        gapgpt_api_key="",
    )

    with pytest.raises(ProviderError) as error:
        create_intelligence_provider(settings)

    assert error.value.code == "intelligence_provider_unconfigured"
