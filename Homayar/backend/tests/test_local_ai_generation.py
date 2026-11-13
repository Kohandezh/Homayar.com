"""HOM-351 U5: bounded generation and the Qwen3 sampling profile.

On the Tesla P40 a cold prompt with the strict schema at temperature 0 looped
without end (7k to 21k tokens), because the adapter sent no ``max_tokens``.
These tests pin the fix:

- every OpenAI-compatible request carries a hard ``max_tokens`` cap;
- a reply cut by that cap (``finish_reason == "length"``) is never parsed;
- the LOCAL_AI_BASE_URL adapter can use Qwen3's non-thinking sampling and
  always turns thinking off; cloud and fallback adapters stay greedy and never
  send fields an OpenAI-style API could reject;
- the identity carries the cap and the sampling values actually sent.

No network, no GPU: requests go to an ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from backend.config import LOCAL_AI_SAMPLING_PROFILES, Settings
from backend.providers import egress
from backend.providers.base import ProviderError, text_digest
from backend.providers.factory import create_intelligence_fallbacks, create_intelligence_provider
from backend.providers.local_llm import local_llm_manifest_template
from backend.providers.openai_compatible import (
    _OUTPUT_SCHEMA,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TIMEOUT_SECONDS,
    SAMPLING_PROFILES,
    OpenAICompatibleIntelligenceProvider,
)
from backend.providers.schemas import IntelligenceRequest, TranscriptTurn
from backend.tests.test_provider_factory import _structured_content

LOCAL_BASE_URL = "http://local-ai:8080/v1"
LOCAL_MODEL = "qwen3-8b-q4km"
TRANSCRIPT = "Synthetic transcript: please send the signed contract on Monday."
REQUEST = IntelligenceRequest(transcript_text=TRANSCRIPT)

# Qwen3 model card, non-thinking mode: temperature 0.7, top_p 0.8, top_k 20,
# min_p 0. No presence_penalty (SPEC U5 amendment A1: 1.5 gave empty analyses).
QWEN3_VALUES = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0}
# _OUTPUT_SCHEMA as this module first saw it, to prove no call changes it.
SCHEMA_AT_IMPORT = copy.deepcopy(_OUTPUT_SCHEMA)
INDEX_FIELDS = ("source_turn_indexes", "summary_source_turn_indexes")
LOCAL_ONLY_FIELDS = ("top_p", "top_k", "min_p", "presence_penalty", "chat_template_kwargs")


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "intelligence_provider": "openai_compatible",
        "local_ai_base_url": LOCAL_BASE_URL,
        "llm_model": LOCAL_MODEL,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _cloud_settings(**overrides: Any) -> Settings:
    return _settings(
        local_ai_base_url="",
        llm_provider="zai",
        zai_api_key="zai-test-key",
        **overrides,
    )


def _completion(*, finish_reason: str | None, content: str | None = None) -> httpx.Response:
    text = json.dumps(_structured_content()) if content is None else content
    choice: dict[str, Any] = {"message": {"content": text}}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return httpx.Response(200, json={"choices": [choice]})


def _call(
    build: Callable[[httpx.AsyncClient], Any],
    *,
    finish_reason: str | None = "stop",
    request: IntelligenceRequest = REQUEST,
    content: str | None = None,
) -> tuple[Any, httpx.Request]:
    """Run one analysis through a mock transport; return (result, request)."""
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _completion(finish_reason=finish_reason, content=content)

    async def run() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            provider = build(client)
            # Stands in for the processor's routing decision (WP08-T06).
            with egress.permit(str(provider.identity().params["endpoint"])):
                return await provider.analyze(request)

    result = asyncio.run(run())
    return result, seen[0]


def _payload(build: Callable[[httpx.AsyncClient], Any]) -> dict[str, Any]:
    _, request = _call(build)
    return json.loads(request.content)


def _local(settings: Settings) -> Callable[[httpx.AsyncClient], Any]:
    return lambda client: create_intelligence_provider(settings, client=client)


def _fallback(settings: Settings) -> Callable[[httpx.AsyncClient], Any]:
    return lambda client: create_intelligence_fallbacks(settings, client=client)[0]


# --- item 1: LOCAL_AI_MAX_TOKENS --------------------------------------------------------------


def test_max_tokens_default_equals_the_adapter_constant() -> None:
    assert Settings(_env_file=None).local_ai_max_tokens == DEFAULT_MAX_TOKENS == 4096


@pytest.mark.parametrize("value", ["256", "32768"], ids=["min-256", "max-32768"])
def test_max_tokens_inside_the_range_is_accepted(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("LOCAL_AI_MAX_TOKENS", value)
    assert Settings(_env_file=None).local_ai_max_tokens == int(value)


@pytest.mark.parametrize(
    "value",
    ["255", "32769", "nan", "inf", "abc", "true", "0", "-4096"],
    ids=["255", "32769", "nan", "inf", "abc", "true", "zero", "negative"],
)
def test_max_tokens_outside_the_range_is_a_settings_error(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("LOCAL_AI_MAX_TOKENS", value)
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)
    assert "local_ai_max_tokens" in str(error.value).lower()


def test_max_tokens_refuses_a_python_bool() -> None:
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, local_ai_max_tokens=True)
    assert "local_ai_max_tokens" in str(error.value).lower()


def test_every_payload_carries_the_hard_cap() -> None:
    assert _payload(_local(_settings()))["max_tokens"] == DEFAULT_MAX_TOKENS


def test_local_endpoint_sends_the_configured_cap() -> None:
    assert _payload(_local(_settings(local_ai_max_tokens=8192)))["max_tokens"] == 8192


def test_cloud_endpoint_sends_the_constant_cap_not_the_local_setting() -> None:
    payload = _payload(_local(_cloud_settings(local_ai_max_tokens=8192)))
    assert payload["max_tokens"] == DEFAULT_MAX_TOKENS


def test_fallback_endpoint_sends_the_constant_cap_not_the_local_setting() -> None:
    settings = _settings(
        local_ai_max_tokens=8192,
        llm_fallback_base_url="http://fallback.test/v1",
        llm_fallback_model="fallback-model",
    )
    assert _payload(_fallback(settings))["max_tokens"] == DEFAULT_MAX_TOKENS


@pytest.mark.parametrize("value", [0, -1, True, 4096.0], ids=["zero", "negative", "bool", "float"])
def test_the_adapter_refuses_a_bad_cap(value: Any) -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleIntelligenceProvider(
            base_url=LOCAL_BASE_URL, model=LOCAL_MODEL, max_tokens=value
        )


# --- item 2: a reply cut by the cap is never parsed ---------------------------------------------


def test_finish_reason_length_with_valid_json_is_refused_as_truncated() -> None:
    with pytest.raises(ProviderError) as error:
        _call(_local(_settings()), finish_reason="length")
    assert error.value.code == "intelligence_output_truncated"
    assert error.value.retryable is True
    assert "contract" not in error.value.public_message.lower()
    assert "Monday" not in str(error.value)


def test_finish_reason_length_with_cut_json_is_refused_as_truncated() -> None:
    # U5-C2: a cut reply is "truncated", not "invalid", whatever the content.
    cut = json.dumps(_structured_content())[:120]
    with pytest.raises(ProviderError) as error:
        _call(_local(_settings()), finish_reason="length", content=cut)
    assert error.value.code == "intelligence_output_truncated"
    assert error.value.retryable is True


def test_cut_json_without_length_stays_an_invalid_response() -> None:
    # Control: the same cut JSON with finish_reason "stop" is the old error.
    cut = json.dumps(_structured_content())[:120]
    with pytest.raises(ProviderError) as error:
        _call(_local(_settings()), finish_reason="stop", content=cut)
    assert error.value.code == "intelligence_invalid_response"


def test_finish_reason_stop_with_the_same_json_is_accepted() -> None:
    result, _ = _call(_local(_settings()), finish_reason="stop")
    assert result.provider == "openai_compatible"
    assert result.model == LOCAL_MODEL


def test_a_reply_without_finish_reason_is_still_accepted() -> None:
    result, _ = _call(_local(_settings()), finish_reason=None)
    assert result.model == LOCAL_MODEL


def test_cloud_reply_cut_by_the_cap_is_refused_too() -> None:
    with pytest.raises(ProviderError) as error:
        _call(_local(_cloud_settings()), finish_reason="length")
    assert error.value.code == "intelligence_output_truncated"


# --- item 3: LOCAL_AI_SAMPLING ---------------------------------------------------------------


def test_sampling_profiles_in_settings_equal_the_adapter_profiles() -> None:
    assert set(LOCAL_AI_SAMPLING_PROFILES) == set(SAMPLING_PROFILES)
    assert SAMPLING_PROFILES["greedy"] == {"temperature": 0}
    assert SAMPLING_PROFILES["qwen3-nonthinking"] == QWEN3_VALUES


def test_sampling_default_is_greedy() -> None:
    assert Settings(_env_file=None).local_ai_sampling == "greedy"


@pytest.mark.parametrize("value", ["greedy", "qwen3-nonthinking"], ids=["greedy", "qwen3"])
def test_known_sampling_profiles_are_accepted(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("LOCAL_AI_SAMPLING", value)
    assert Settings(_env_file=None).local_ai_sampling == value


@pytest.mark.parametrize(
    "value",
    ["Greedy ", "qwen3", "", "QWEN3-NONTHINKING"],
    ids=["greedy-case-space", "qwen3-short", "empty", "upper"],
)
def test_unknown_sampling_profiles_are_a_settings_error(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("LOCAL_AI_SAMPLING", value)
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)
    assert "LOCAL_AI_SAMPLING" in str(error.value)


def test_local_greedy_payload_is_temperature_0_only() -> None:
    payload = _payload(_local(_settings()))
    assert payload["temperature"] == 0
    for name in ("top_p", "top_k", "min_p", "presence_penalty"):
        assert name not in payload, name


def test_local_qwen3_payload_sends_the_model_card_values() -> None:
    payload = _payload(_local(_settings(local_ai_sampling="qwen3-nonthinking")))
    for name, value in QWEN3_VALUES.items():
        assert payload[name] == value, name


@pytest.mark.parametrize("sampling", ["greedy", "qwen3-nonthinking"], ids=["greedy", "qwen3"])
def test_no_profile_sends_or_declares_presence_penalty(sampling: str) -> None:
    settings = _settings(local_ai_sampling=sampling)
    assert "presence_penalty" not in _payload(_local(settings))
    assert "presence_penalty" not in _identity(settings)


@pytest.mark.parametrize("sampling", ["greedy", "qwen3-nonthinking"], ids=["greedy", "qwen3"])
def test_cloud_payload_is_greedy_without_local_only_fields(sampling: str) -> None:
    payload = _payload(_local(_cloud_settings(local_ai_sampling=sampling)))
    assert payload["temperature"] == 0
    for name in LOCAL_ONLY_FIELDS:
        assert name not in payload, name


@pytest.mark.parametrize("sampling", ["greedy", "qwen3-nonthinking"], ids=["greedy", "qwen3"])
def test_fallback_payload_is_greedy_without_local_only_fields(sampling: str) -> None:
    settings = _settings(
        local_ai_sampling=sampling,
        llm_fallback_base_url="http://fallback.test/v1",
        llm_fallback_model="fallback-model",
    )
    payload = _payload(_fallback(settings))
    assert payload["temperature"] == 0
    for name in LOCAL_ONLY_FIELDS:
        assert name not in payload, name


def test_the_adapter_refuses_an_unknown_profile() -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleIntelligenceProvider(
            base_url=LOCAL_BASE_URL, model=LOCAL_MODEL, local_server=True, sampling="qwen3"
        )


def test_the_adapter_refuses_a_local_profile_on_a_non_local_server() -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleIntelligenceProvider(
            base_url="https://api.example.test/v1", model="m", sampling="qwen3-nonthinking"
        )


# --- item 4: thinking off on the local endpoint only -------------------------------------------


@pytest.mark.parametrize("sampling", ["greedy", "qwen3-nonthinking"], ids=["greedy", "qwen3"])
def test_local_payload_turns_thinking_off(sampling: str) -> None:
    payload = _payload(_local(_settings(local_ai_sampling=sampling)))
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


# --- item 5: the identity carries the cap and the sampling values sent ------------------------


def _identity(settings: Settings) -> dict[str, Any]:
    return dict(create_intelligence_provider(settings).identity().params)


def test_local_greedy_identity_carries_the_cap_and_temperature_0() -> None:
    params = _identity(_settings())
    assert params["max_tokens"] == DEFAULT_MAX_TOKENS
    assert params["temperature"] == 0
    for name in ("top_p", "top_k", "min_p", "presence_penalty", "enable_thinking"):
        assert name not in params, name


def test_identity_declares_the_turn_index_rule() -> None:
    assert _identity(_settings())["turn_index_bound"] == "per_call_max"
    assert _identity(_cloud_settings())["turn_index_bound"] == "per_call_max"


def test_local_qwen3_identity_carries_every_sampling_value() -> None:
    params = _identity(_settings(local_ai_sampling="qwen3-nonthinking"))
    for name, value in QWEN3_VALUES.items():
        assert params[name] == value, name
    assert params["enable_thinking"] is False
    assert params["max_tokens"] == DEFAULT_MAX_TOKENS


@pytest.mark.parametrize("sampling", ["greedy", "qwen3-nonthinking"], ids=["greedy", "qwen3"])
def test_identity_values_equal_the_values_sent(sampling: str) -> None:
    settings = _settings(local_ai_sampling=sampling, local_ai_max_tokens=2048)
    params = _identity(settings)
    payload = _payload(_local(settings))
    for name in (*SAMPLING_PROFILES[sampling], "max_tokens"):
        assert params[name] == payload[name], name


def test_same_settings_give_the_same_identity() -> None:
    settings = _settings(local_ai_sampling="qwen3-nonthinking")
    assert _identity(settings) == _identity(settings)


@pytest.mark.parametrize(
    "update",
    [{"local_ai_sampling": "qwen3-nonthinking"}, {"local_ai_max_tokens": 8192}],
    ids=["profile", "cap"],
)
def test_a_profile_or_cap_change_is_a_new_identity(update: dict[str, Any]) -> None:
    assert _identity(_settings()) != _identity(_settings(**update))


def test_cloud_identity_ignores_the_local_settings() -> None:
    base = _identity(_cloud_settings())
    changed = _identity(
        _cloud_settings(local_ai_sampling="qwen3-nonthinking", local_ai_max_tokens=8192)
    )
    assert base == changed
    assert base["max_tokens"] == DEFAULT_MAX_TOKENS


# U5-C3: cloud and fallback identities carry max_tokens 4096 and temperature 0,
# and no other sampling key.
SAMPLING_KEYS = {"temperature", "top_p", "top_k", "min_p", "presence_penalty", "enable_thinking"}


def _fallback_identity(settings: Settings) -> dict[str, Any]:
    return dict(create_intelligence_fallbacks(settings)[0].identity().params)


@pytest.mark.parametrize("sampling", ["greedy", "qwen3-nonthinking"], ids=["greedy", "qwen3"])
def test_cloud_and_fallback_identities_are_4096_and_temperature_0_only(sampling: str) -> None:
    local = {"local_ai_sampling": sampling, "local_ai_max_tokens": 8192}
    fallback = _settings(
        llm_fallback_base_url="http://fallback.test/v1",
        llm_fallback_model="fallback-model",
        **local,
    )
    for params in (_identity(_cloud_settings(**local)), _fallback_identity(fallback)):
        assert params["max_tokens"] == 4096
        assert params["temperature"] == 0
        assert SAMPLING_KEYS & set(params) == {"temperature"}


def test_default_local_identity_still_equals_the_local_llm_template() -> None:
    # A template-made manifest keeps matching a default local deployment.
    template = local_llm_manifest_template("llama_cpp", model=LOCAL_MODEL, base_url=LOCAL_BASE_URL)
    assert template["identity_params"] == _identity(_settings())


# --- item 8 (low): the timeout constant -------------------------------------------------------


def test_timeout_default_equals_the_adapter_constant() -> None:
    assert Settings(_env_file=None).local_ai_timeout_seconds == DEFAULT_TIMEOUT_SECONDS == 60.0


# --- amendment A1, item 9: bounded turn-index arrays (every endpoint) -------------------------


def _index_arrays(schema: object) -> list[tuple[str, dict[str, Any]]]:
    """Every (field name, array schema) of a turn-index field inside ``schema``."""
    found: list[tuple[str, dict[str, Any]]] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for name, field in (node.get("properties") or {}).items():
                if name in INDEX_FIELDS:
                    found.append((name, field))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return found


def _turns(count: int) -> IntelligenceRequest:
    turns = [
        TranscriptTurn(text=f"Synthetic turn {index}.", speaker_label=f"speaker_{index % 2}")
        for index in range(count)
    ]
    return IntelligenceRequest(transcript_text=" ".join(t.text for t in turns), turns=turns)


def _sent_schema(build: Callable[[httpx.AsyncClient], Any], request: IntelligenceRequest) -> Any:
    _, sent = _call(build, request=request)
    return json.loads(sent.content)["response_format"]["json_schema"]["schema"]


def test_the_static_schema_caps_every_index_array() -> None:
    arrays = _index_arrays(_OUTPUT_SCHEMA)
    # summary, 3 summary fields, action items, insights, decisions,
    # key moments and the 4 extraction dimensions.
    assert len(arrays) >= 12
    for name, field in arrays:
        assert field["maxItems"] == (20 if name == "summary_source_turn_indexes" else 10), name
        assert field["items"] == {"type": "integer", "minimum": 0}, name


@pytest.mark.parametrize(("count", "last"), [(3, 2), (40, 39)], ids=["3-turns", "40-turns"])
def test_each_call_bounds_every_index_to_its_last_turn(count: int, last: int) -> None:
    schema = _sent_schema(_local(_settings()), _turns(count))
    arrays = _index_arrays(schema)
    assert len(arrays) == len(_index_arrays(_OUTPUT_SCHEMA))
    for name, field in arrays:
        assert field["items"]["maximum"] == last, name
        assert field["items"]["minimum"] == 0, name
        assert field["maxItems"] == (20 if name == "summary_source_turn_indexes" else 10), name


def test_a_second_call_uses_its_own_turn_count() -> None:
    build = _local(_settings())
    _sent_schema(build, _turns(3))
    for name, field in _index_arrays(_sent_schema(build, _turns(40))):
        assert field["items"]["maximum"] == 39, name


def test_a_call_without_turns_allows_no_index() -> None:
    schema = _sent_schema(_local(_settings()), REQUEST)
    for name, field in _index_arrays(schema):
        assert field["maxItems"] == 0, name
        assert "maximum" not in field["items"], name


def test_the_cloud_endpoint_gets_the_same_bounds() -> None:
    for name, field in _index_arrays(_sent_schema(_local(_cloud_settings()), _turns(3))):
        assert field["items"]["maximum"] == 2, name


def test_calls_never_change_the_static_schema() -> None:
    build = _local(_settings())
    for request in (_turns(3), _turns(40), REQUEST):
        _sent_schema(build, request)
    assert _OUTPUT_SCHEMA == SCHEMA_AT_IMPORT
    for name, field in _index_arrays(_OUTPUT_SCHEMA):
        assert "maximum" not in field["items"], name
        assert field["maxItems"] != 0, name


def test_the_schema_digest_stays_the_static_schema_digest() -> None:
    expected = text_digest(json.dumps(_OUTPUT_SCHEMA, sort_keys=True, separators=(",", ":")))
    assert _identity(_settings())["output_schema_sha256"] == expected
