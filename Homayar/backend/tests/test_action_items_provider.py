"""HOM-133 (ADR 0058): the provider asks for the action kind and the grounding quotes.

The strict output schema requires ``kind``, ``owner_quote`` and ``due_quote`` on every
action item; the prompt says when to set each and never to guess.  The parser keeps
the new fields and refuses an unknown kind.  The mock still returns no action items.
No test reaches a network: the adapter runs on an in-memory transport.
"""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import date

import httpx
import pytest

from backend.providers import egress
from backend.providers.base import ProviderError
from backend.providers.mocks import MockIntelligenceProvider
from backend.providers.openai_compatible import (
    _OUTPUT_SCHEMA,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    OpenAICompatibleIntelligenceProvider,
)
from backend.providers.schemas import ActionItem, ActionKind
from backend.tests.test_analysis_claims_provider import ENDPOINT, REQUEST, VALID_OUTPUT

ITEM = {
    "description": "اعلام قیمت نهایی",
    "owner": "speaker_1",
    "due_date": "2026-11-13",
    "priority": "high",
    "source_turn_indexes": [0],
    "kind": "commitment",
    "owner_quote": "قیمت نهایی را فردا اعلام می‌کنم",
    "due_quote": "فردا",
}


def _run(output: object, seen: list | None = None):
    content = output if isinstance(output, str) else json.dumps(output)

    def reply(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    adapter = OpenAICompatibleIntelligenceProvider(
        base_url="https://llm.example.test/v1",
        model="actions-llm",
        client=httpx.AsyncClient(transport=httpx.MockTransport(reply)),
    )
    with egress.permit(ENDPOINT):
        return asyncio.run(adapter.analyze(REQUEST))


def _with_items(*items: dict) -> dict:
    output = copy.deepcopy(VALID_OUTPUT)
    output["action_items"] = list(items)
    return output


def test_the_prompt_version_is_5():
    assert PROMPT_VERSION == 5


def test_the_schema_requires_kind_and_both_quotes():
    item = _OUTPUT_SCHEMA["properties"]["action_items"]["items"]
    assert item["additionalProperties"] is False
    assert {"kind", "owner_quote", "due_quote"} <= set(item["required"])
    assert set(item["required"]) == set(item["properties"])
    properties = item["properties"]
    assert properties["kind"] == {"type": "string", "enum": ["commitment", "next_step"]}
    assert properties["owner_quote"] == {"type": ["string", "null"], "maxLength": 500}
    assert properties["due_quote"] == {"type": ["string", "null"], "maxLength": 200}


def test_the_prompt_says_when_to_set_each_field_and_never_to_guess():
    for words in (
        "An action item is something a speaker says will be done",
        "'commitment' when a speaker promises or accepts to do it",
        "'next_step' when it is only proposed or suggested",
        "Set owner only when the turns name who will do it",
        "owner_quote the exact words of a cited turn that name the owner",
        "Set due_date only when a speaker states a day or a date",
        "due_quote the exact words of a cited turn that state it",
        "Never guess an owner or a date",
    ):
        assert words in SYSTEM_PROMPT, words


def test_the_request_sends_the_schema_and_the_version():
    seen: list = []
    _run(_with_items(ITEM), seen)
    (payload,) = seen
    schema = payload["response_format"]["json_schema"]["schema"]
    item = schema["properties"]["action_items"]["items"]
    assert {"kind", "owner_quote", "due_quote"} <= set(item["required"])
    assert "owner_quote" in payload["messages"][0]["content"]


def test_the_parser_keeps_the_kind_and_the_quotes():
    result = _run(_with_items(ITEM, ITEM | {"kind": "next_step", "owner": None,
                                            "owner_quote": None, "due_date": None,
                                            "due_quote": None}))  # fmt: skip
    first, second = result.action_items
    assert first.kind is ActionKind.COMMITMENT
    assert (first.owner, first.owner_quote) == ("speaker_1", ITEM["owner_quote"])
    assert (first.due_date, first.due_quote) == (date(2026, 11, 13), "فردا")
    assert second.kind is ActionKind.NEXT_STEP
    assert (second.owner, second.owner_quote, second.due_date, second.due_quote) == (
        None,
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        pytest.param("kind", "other", id="unknown-kind"),
    ],
)
def test_the_parser_refuses_a_bad_kind_or_quote(key, value):
    with pytest.raises(ProviderError) as refused:
        _run(_with_items(ITEM | {key: value}))
    assert refused.value.code == "intelligence_invalid_response"


@pytest.mark.parametrize(
    ("key", "value", "limit"),
    [
        pytest.param("owner_quote", "x" * 501, 500, id="owner-quote-too-long"),
        pytest.param("due_quote", "x" * 201, 200, id="due-quote-too-long"),
    ],
)
def test_a_too_long_quote_is_truncated_after_the_corrective_retry(key, value, limit):
    # HOM-351 F3 (R10): maxLength is a bound. The transport repeats the answer,
    # so the retry sees it again and the repair cuts it to the bound.
    seen: list = []
    result = _run(_with_items(ITEM | {key: value}), seen)
    assert getattr(result.action_items[0], key) == "x" * limit
    assert len(seen) == 2


def test_an_item_without_the_new_keys_is_an_unlabelled_next_step():
    # Backward compatible: older checkpoints and other providers omit the new keys.
    item = ActionItem(description="ارسال گزارش", source_turn_indexes=[0])
    assert item.kind is ActionKind.NEXT_STEP
    assert (item.owner_quote, item.due_quote) == (None, None)


def test_the_mock_still_returns_no_action_items():
    provider = MockIntelligenceProvider(allow_mock=True)
    result = asyncio.run(provider.analyze(REQUEST))
    assert result.action_items == []
