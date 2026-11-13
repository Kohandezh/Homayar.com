"""HOM-351 F3: bound violations in a grammar-constrained answer are survivable.

llama.cpp (b11960, ``common/json-schema.cpp``) parses ``minimum``/``maximum``
only for the ``integer`` type (``build_integer``); a ``number`` node carries no
bounds and the converter (``common/json-schema-to-grammar.cpp``, ``KIND_NUMBER``)
emits the unconstrained ``number`` primitive. A strict ``response_format`` schema
therefore admits an out-of-range ``confidence``, and a server compiled without
full schema enforcement can miss the other bounds too. The adapter now: retries
once with a corrective message (paths and rules only, never values), then
repairs what can be repaired (clamp, truncate, drop a negative turn index), and
only then fails, retryably. Everything else stays a non-retryable
``intelligence_invalid_response`` after one call. No test reaches a network.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import traceback

import httpx
import pytest

from backend.jobs.processor import process_job
from backend.providers import egress
from backend.providers.base import ProviderError
from backend.providers.openai_compatible import (
    OpenAICompatibleIntelligenceProvider,
    _apply_repair,
    _rule_text,
)
from backend.providers.schemas import IntelligenceRequest, TranscriptTurn
from backend.tests.test_provider_resilience import (
    Script,
    _health,
    _llm,
    _private,
    _setup,
    _upload_job,
)
from backend.tests.test_provider_resilience import _bundle as _resilience_bundle

LLM_BASE = "https://llm.example.test/v1"
LOG_NAME = "backend.providers.openai_compatible"


def _not_stated() -> dict:
    return {"status": "not_stated", "text": None, "basis": None, "source_turn_indexes": []}


def _valid_output() -> dict:
    return {
        "summary": "The customer asked for a callback.",
        "summary_source_turn_indexes": [0],
        "action_items": [],
        "insights": [
            {
                "category": "request",
                "description": "Callback requested",
                "confidence": 0.8,
                "source_turn_indexes": [0],
            }
        ],
        "summary_fields": {
            "purpose": _not_stated(),
            "resolution": _not_stated(),
            "next_steps": _not_stated(),
        },
        "decisions": [],
        "key_moments": [],
        "extraction": {
            name: {"status": "not_stated", "values": []}
            for name in ("intent", "topic", "entity", "outcome")
        },
    }


REQUEST = IntelligenceRequest(
    transcript_text="Please call me back tomorrow.",
    turns=[TranscriptTurn(text="Please call me back tomorrow.", speaker_label="speaker_1")],
    language="en",
    call_id="call-1",
)


class _Scripted:
    """In-memory OpenAI-compatible endpoint that plays queued answers."""

    def __init__(self, answers: list[dict], usages: list[dict | None] | None = None) -> None:
        self.answers = [json.dumps(answer) for answer in answers]
        self.usages = (
            usages
            if usages is not None
            else [{"prompt_tokens": 120, "completion_tokens": 45}] * len(answers)
        )
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        index = len(self.requests) - 1
        envelope: dict = {"choices": [{"message": {"content": self.answers[index]}}]}
        if self.usages[index] is not None:
            envelope["usage"] = self.usages[index]
        return httpx.Response(200, json=envelope)


def _adapter(transport: _Scripted) -> OpenAICompatibleIntelligenceProvider:
    return OpenAICompatibleIntelligenceProvider(
        base_url=LLM_BASE,
        model="bounds-test-llm",
        client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )


@pytest.fixture(autouse=True)
def _approved_route():
    """Adapter-level tests stand in for the processor's routing decision."""
    with egress.permit(f"{LLM_BASE}/chat/completions"):
        yield


def _messages(payload: dict) -> list[dict]:
    return payload["messages"]


def _repair_events(caplog) -> list:
    return [
        record
        for record in caplog.records
        if record.message.startswith("intelligence_output_repaired")
    ]


def _corrective_text(payload: dict) -> str:
    return _messages(payload)[3]["content"]


# --- the control: one call, unchanged -----------------------------------------------------


def test_a_fully_valid_first_answer_makes_exactly_one_call() -> None:
    transport = _Scripted([_valid_output()])
    result = asyncio.run(_adapter(transport).analyze(REQUEST))
    assert result.summary == "The customer asked for a callback."
    assert len(transport.requests) == 1


# --- the corrective retry -----------------------------------------------------------------


def test_a_bound_violation_then_a_valid_retry_succeeds_with_two_calls() -> None:
    bad = _valid_output()
    bad["insights"][0]["confidence"] = 1.4
    transport = _Scripted(
        [bad, _valid_output()],
        usages=[
            {"prompt_tokens": 120, "completion_tokens": 45},
            {"prompt_tokens": 200, "completion_tokens": 80},
        ],
    )
    result = asyncio.run(_adapter(transport).analyze(REQUEST))

    assert result.insights[0].confidence == 0.8
    assert len(transport.requests) == 2
    # Both calls count in usage.
    assert result.usage is not None
    assert result.usage.input_tokens == 320 and result.usage.output_tokens == 125

    first, second = transport.requests
    # Same contract on the retry: model, temperature, response_format.
    assert second["model"] == first["model"]
    assert second["temperature"] == first["temperature"]
    assert second["response_format"] == first["response_format"]
    # The same original messages, the model's previous answer, then the correction.
    assert _messages(second)[:2] == _messages(first)
    assistant = _messages(second)[2]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == json.dumps(bad)
    assert _messages(second)[3]["role"] == "user"
    # Paths and rules only: the violated value never appears.
    text = _corrective_text(second)
    assert "insights[0].confidence: must be <= 1" in text
    assert "1.4" not in text
    assert "0.8" not in text


def test_the_corrective_message_holds_no_value_text() -> None:
    bad = _valid_output()
    bad["insights"][0]["category"] = "x" * 200
    bad["insights"][0]["description"] = "SECRET-VALUE-TOKEN"
    transport = _Scripted([bad, _valid_output()])
    asyncio.run(_adapter(transport).analyze(REQUEST))

    assert len(transport.requests) == 2
    text = _corrective_text(transport.requests[1])
    assert "insights[0].category: must be at most 128 characters" in text
    assert "SECRET-VALUE-TOKEN" not in text
    assert "x" * 32 not in text


def test_a_missing_required_field_fails_after_one_call_without_retry() -> None:
    bad = _valid_output()
    del bad["summary"]
    transport = _Scripted([bad, _valid_output()])
    with pytest.raises(ProviderError) as raised:
        asyncio.run(_adapter(transport).analyze(REQUEST))
    assert raised.value.code == "intelligence_invalid_response"
    assert raised.value.retryable is False
    assert len(transport.requests) == 1


def test_mixed_bound_and_structural_errors_keep_the_non_retryable_path() -> None:
    bad = _valid_output()
    bad["insights"][0]["confidence"] = 1.4
    del bad["decisions"]
    transport = _Scripted([bad, _valid_output()])
    with pytest.raises(ProviderError) as raised:
        asyncio.run(_adapter(transport).analyze(REQUEST))
    assert raised.value.retryable is False
    assert len(transport.requests) == 1


def test_a_non_bound_error_in_the_second_answer_is_retryable() -> None:
    first_bad = _valid_output()
    first_bad["insights"][0]["confidence"] = 1.4
    second_bad = _valid_output()
    del second_bad["summary"]
    transport = _Scripted([first_bad, second_bad])
    with pytest.raises(ProviderError) as raised:
        asyncio.run(_adapter(transport).analyze(REQUEST))
    assert raised.value.code == "intelligence_invalid_response"
    assert raised.value.retryable is True
    assert len(transport.requests) == 2


# --- the repair ---------------------------------------------------------------------------


def test_bound_violations_twice_are_repaired_exactly_to_the_bound(caplog) -> None:
    bad = _valid_output()
    bad["insights"][0]["confidence"] = 7
    bad["insights"][0]["category"] = "c" * 200
    transport = _Scripted([copy.deepcopy(bad), copy.deepcopy(bad)])
    with caplog.at_level(logging.INFO, logger=LOG_NAME):
        result = asyncio.run(_adapter(transport).analyze(REQUEST))

    assert len(transport.requests) == 2
    assert result.insights[0].confidence == 1
    assert result.insights[0].category == "c" * 128
    events = _repair_events(caplog)
    assert len(events) == 1
    text = events[0].getMessage()
    assert "insights[0].confidence" in text and "less_than_equal" in text
    assert "insights[0].category" in text and "string_too_long" in text
    assert "count=2" in text
    # No repaired value is logged.
    assert "c" * 16 not in text


def test_an_unrepairable_min_length_violation_twice_is_retryable() -> None:
    bad = _valid_output()
    bad["insights"][0]["category"] = ""
    transport = _Scripted([copy.deepcopy(bad), copy.deepcopy(bad)])
    with pytest.raises(ProviderError) as raised:
        asyncio.run(_adapter(transport).analyze(REQUEST))
    assert raised.value.code == "intelligence_invalid_response"
    assert raised.value.retryable is True
    assert len(transport.requests) == 2


# --- R2: negative turn indexes are bound violations, repaired by dropping ------------------


def test_negative_summary_turn_indexes_are_dropped_after_the_retry(caplog) -> None:
    bad = _valid_output()
    bad["summary_source_turn_indexes"] = [-1, 0]
    transport = _Scripted([copy.deepcopy(bad), copy.deepcopy(bad)])
    with caplog.at_level(logging.INFO, logger=LOG_NAME):
        result = asyncio.run(_adapter(transport).analyze(REQUEST))

    assert len(transport.requests) == 2
    assert result.summary_source_turn_indexes == [0]
    assert "summary_source_turn_indexes: must be >= 0" in _corrective_text(transport.requests[1])
    events = _repair_events(caplog)
    assert len(events) == 1
    text = events[0].getMessage()
    assert "summary_source_turn_indexes" in text and "count=1" in text


def _with_action_item(output: dict, indexes: list[int]) -> dict:
    output["action_items"] = [
        {
            "description": "Call the customer back",
            "owner": None,
            "due_date": None,
            "priority": "high",
            "source_turn_indexes": indexes,
        }
    ]
    return output


def test_negative_nested_source_turn_indexes_are_dropped_after_the_retry() -> None:
    bad = _with_action_item(_valid_output(), [0, -3])
    transport = _Scripted([copy.deepcopy(bad), copy.deepcopy(bad)])
    result = asyncio.run(_adapter(transport).analyze(REQUEST))

    assert len(transport.requests) == 2
    assert result.action_items[0].source_turn_indexes == [0]
    corrective = _corrective_text(transport.requests[1])
    assert "action_items[0].source_turn_indexes: must be >= 0" in corrective


def test_a_negative_turn_index_corrected_by_the_model_needs_no_repair(caplog) -> None:
    bad = _with_action_item(_valid_output(), [0, -3])
    transport = _Scripted([bad, _with_action_item(_valid_output(), [0])])
    with caplog.at_level(logging.INFO, logger=LOG_NAME):
        result = asyncio.run(_adapter(transport).analyze(REQUEST))

    assert len(transport.requests) == 2
    assert result.action_items[0].source_turn_indexes == [0]
    assert not _repair_events(caplog)


# --- usage honesty ------------------------------------------------------------------------


def test_usage_counts_both_calls_when_the_second_reports_none() -> None:
    bad = _valid_output()
    bad["insights"][0]["confidence"] = 1.4
    transport = _Scripted(
        [bad, _valid_output()],
        usages=[{"prompt_tokens": 120, "completion_tokens": 45}, None],
    )
    result = asyncio.run(_adapter(transport).analyze(REQUEST))
    assert result.usage is not None
    assert result.usage.input_tokens == 120 and result.usage.output_tokens == 45


# --- repair details -----------------------------------------------------------------------


def test_a_long_persian_string_is_truncated_on_a_character_boundary() -> None:
    bad = _valid_output()
    bad["insights"][0]["category"] = "درخواست" * 30  # 210 characters, 2 bytes each
    transport = _Scripted([copy.deepcopy(bad), copy.deepcopy(bad)])
    result = asyncio.run(_adapter(transport).analyze(REQUEST))

    assert len(transport.requests) == 2
    assert result.insights[0].category == ("درخواست" * 30)[:128]
    assert len(result.insights[0].category) == 128
    # Valid text after the cut: no half character.
    result.insights[0].category.encode("utf-8")


@pytest.mark.parametrize(
    ("error", "repaired", "rule"),
    [
        pytest.param(
            {"type": "too_long", "loc": ("items",), "ctx": {"max_length": 2}},
            {"items": [1, 2]},
            "must have at most 2 items",
            id="list-max-items",
        ),
        pytest.param(
            {"type": "too_short", "loc": ("items",), "ctx": {"min_length": 2}},
            None,
            "must have at least 2 items",
            id="list-min-items",
        ),
        pytest.param(
            {"type": "less_than", "loc": ("items",), "ctx": {"lt": 1}},
            None,
            "must be < 1",
            id="strict-bound",
        ),
    ],
)
def test_list_and_strict_bounds_repair_rules(error, repaired, rule) -> None:
    """The output schema has no list or strict bound today; the helpers still
    read pydantic's real context keys (a list bound is ``max_length``) and
    never invent a value for a strict bound."""
    output = {"items": [1, 2, 3]}
    assert _rule_text(error) == rule
    assert _apply_repair(output, error) is (repaired is not None)
    if repaired is not None:
        assert output == repaired


# --- review turn 1, R9: non-finite numbers, hidden context, no circuit failure ------------

MARKER = "MODEL-OUTPUT-MARKER"


def _with_raw_confidence(token: str) -> str:
    """A first answer whose confidence is the raw JSON number ``token``."""
    output = _valid_output()
    output["insights"][0]["confidence"] = 0.123456
    return json.dumps(output).replace("0.123456", token)


class _RawScripted(_Scripted):
    """Plays raw answer texts (for JSON numbers ``json.dumps`` cannot write)."""

    def __init__(self, raw_answers: list[str]) -> None:
        super().__init__([])
        self.answers = raw_answers
        self.usages = [None] * len(raw_answers)


@pytest.mark.parametrize(
    "token",
    [
        pytest.param("NaN", id="nan"),
        pytest.param("Infinity", id="infinity"),
        pytest.param("-Infinity", id="minus-infinity"),
        pytest.param("1e400", id="overflow-to-infinity"),
        # Strings: pydantic's lax mode reads them as floats (review turn 2).
        pytest.param('"NaN"', id="string-nan"),
        pytest.param('"Infinity"', id="string-infinity"),
        pytest.param('"-Infinity"', id="string-minus-infinity"),
        pytest.param('"inf"', id="string-inf"),
        pytest.param('"1e400"', id="string-overflow"),
    ],
)
def test_a_non_finite_number_is_refused_never_clamped(token) -> None:
    transport = _RawScripted([_with_raw_confidence(token), json.dumps(_valid_output())])
    with pytest.raises(ProviderError) as raised:
        asyncio.run(_adapter(transport).analyze(REQUEST))
    assert raised.value.code == "intelligence_invalid_response"
    assert raised.value.retryable is False  # not a bound: no corrective retry
    assert len(transport.requests) == 1


def test_a_string_that_is_not_a_number_keeps_the_one_call_refusal() -> None:
    """Control: a confidence string that does not parse as a float is a type
    error, not a bound (unchanged)."""
    transport = _RawScripted([_with_raw_confidence('"high"'), json.dumps(_valid_output())])
    with pytest.raises(ProviderError) as raised:
        asyncio.run(_adapter(transport).analyze(REQUEST))
    assert raised.value.retryable is False
    assert len(transport.requests) == 1


def test_a_non_finite_number_in_the_second_answer_is_refused_retryably() -> None:
    first = _valid_output()
    first["insights"][0]["confidence"] = 1.4
    transport = _RawScripted([json.dumps(first), _with_raw_confidence("Infinity")])
    with pytest.raises(ProviderError) as raised:
        asyncio.run(_adapter(transport).analyze(REQUEST))
    assert raised.value.code == "intelligence_invalid_response"
    assert raised.value.retryable is True
    assert len(transport.requests) == 2


def _marked_long_category() -> dict:
    output = _valid_output()
    output["insights"][0]["category"] = MARKER + "x" * 200
    return output


def _marked_bad_enum() -> dict:
    output = _with_action_item(_valid_output(), [0])
    output["action_items"][0]["priority"] = MARKER
    return output


def _marked_unrepairable() -> dict:
    output = _valid_output()
    output["insights"][0]["category"] = ""
    output["insights"][0]["description"] = MARKER
    return output


class _ThenUnavailable(_Scripted):
    """The corrective call fails at the transport level (HTTP 503)."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.requests:
            self.requests.append(json.loads(request.content))
            return httpx.Response(503)
        return super().__call__(request)


@pytest.mark.parametrize(
    ("transport", "retryable"),
    [
        pytest.param(lambda: _Scripted([_marked_bad_enum()]), False, id="first-structural"),
        pytest.param(
            lambda: _Scripted([_marked_long_category(), _marked_bad_enum()]),
            True,
            id="second-structural",
        ),
        pytest.param(
            lambda: _Scripted([_marked_long_category(), _marked_unrepairable()]),
            True,
            id="second-unrepairable",
        ),
        pytest.param(
            lambda: _ThenUnavailable([_marked_long_category()]),
            True,
            id="second-call-unavailable",
        ),
    ],
)
def test_no_raised_error_carries_model_output_in_its_traceback(transport, retryable) -> None:
    """A pydantic error prints the offending input. No error raised by the
    adapter may chain one, so a logged traceback never holds model output."""
    with pytest.raises(ProviderError) as raised:
        asyncio.run(_adapter(transport()).analyze(REQUEST))
    assert raised.value.retryable is retryable
    formatted = "".join(traceback.format_exception(raised.value))
    assert MARKER not in formatted
    assert "ValidationError" not in formatted


def test_an_invalid_response_is_not_a_circuit_failure(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    """R9: a retryable ``intelligence_invalid_response`` (bounds still broken
    after the corrective retry) is about answer quality, not availability: the
    endpoint's circuit records no failure, like any permanent error."""
    unrepairable = _valid_output()
    unrepairable["insights"][0]["category"] = ""
    script = Script(
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(unrepairable)}}]}
        )
    )
    primary = _llm(script, "llm-bounds.example.test")
    tenant, configured = _setup(
        client,
        email_outbox,
        monkeypatch,
        settings,
        tmp_path,
        "bounds-circuit",
        [_private(primary, "test.llm.bounds")],
    )
    job_id, _meeting = _upload_job(client, tenant, "bounds-circuit-1")
    result = process_job(
        job_id, worker_id="w-bounds", settings=configured, providers=_resilience_bundle(primary)
    )
    assert result == {
        "job_id": job_id,
        "status": "retry_scheduled",
        "code": "intelligence_invalid_response",
    }
    assert script.calls == 2
    health = _health(primary)
    assert health is None or health.consecutive_failures == 0


# --- rebase on p2-t1 (HOM-351 GPU AI): token cap and per-call payload ---------------------


class _SecondTruncated(_Scripted):
    """The corrective answer stops at the token cap (``finish_reason`` length)."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        response = super().__call__(request)
        if len(self.requests) == 2:
            envelope = json.loads(response.content)
            envelope["choices"][0]["finish_reason"] = "length"
            return httpx.Response(200, json=envelope)
        return response


def test_a_truncated_corrective_answer_is_intelligence_output_truncated() -> None:
    """p2-t1's token-cap rule applies to every response, the corrective one too."""
    first = _valid_output()
    first["insights"][0]["confidence"] = 1.4
    transport = _SecondTruncated([first, _valid_output()])
    with pytest.raises(ProviderError) as raised:
        asyncio.run(_adapter(transport).analyze(REQUEST))
    assert raised.value.code == "intelligence_output_truncated"
    assert raised.value.retryable is True
    assert len(transport.requests) == 2


def test_the_corrective_call_keeps_every_field_of_the_first_payload() -> None:
    """The corrective request is the first one plus two messages: the token cap,
    the sampling profile, ``chat_template_kwargs`` and the per-call bounded
    schema all stay the same."""
    first_answer = _valid_output()
    first_answer["insights"][0]["confidence"] = 1.4
    transport = _Scripted([first_answer, _valid_output()])
    adapter = OpenAICompatibleIntelligenceProvider(
        base_url=LLM_BASE,
        model="bounds-test-llm",
        max_tokens=1234,
        sampling="qwen3-nonthinking",
        local_server=True,
        client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )
    asyncio.run(adapter.analyze(REQUEST))

    first, second = transport.requests
    assert {k: v for k, v in second.items() if k != "messages"} == {
        k: v for k, v in first.items() if k != "messages"
    }
    assert first["max_tokens"] == 1234 and first["top_k"] == 20
    assert first["chat_template_kwargs"] == {"enable_thinking": False}
    schema = first["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["summary_source_turn_indexes"]["items"]["maximum"] == 0
    assert second["messages"][:2] == first["messages"]
