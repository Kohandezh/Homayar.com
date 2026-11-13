"""HOM-351 Q1: the model writes its free text in the conversation's language.

A live run gave an English summary for a Persian call: the prompt had no rule for
the output language.  The prompt now says which fields are free text, that they
are written in the request ``language`` (or the language of the turns when the
code is not a real language), and that quotes stay copied exactly.  The prompt
version moves to 6; the payload of a Persian request changes only by the prompt.
"""

from __future__ import annotations

import json

from backend.providers.base import text_digest
from backend.providers.openai_compatible import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    OpenAICompatibleIntelligenceProvider,
)
from backend.providers.schemas import IntelligenceRequest, TranscriptTurn

REQUEST = IntelligenceRequest(
    transcript_text="سلام، قیمت نهایی رو فردا اعلام می‌کنم",
    turns=[
        TranscriptTurn(text="سلام", speaker_label="speaker_0"),
        TranscriptTurn(text="قیمت نهایی رو فردا اعلام می‌کنم", speaker_label="speaker_1"),
    ],
    language="fa",
)


def _adapter() -> OpenAICompatibleIntelligenceProvider:
    return OpenAICompatibleIntelligenceProvider(base_url="https://llm.example.test/v1", model="m")


def test_the_prompt_version_is_6():
    assert PROMPT_VERSION == 6
    params = _adapter().identity().params
    assert params["prompt_version"] == 6
    assert params["prompt_sha256"] == text_digest(SYSTEM_PROMPT)


def test_the_prompt_names_every_free_text_field_and_the_language_to_write_it_in():
    for words in (
        "Write every free-text value in the language of the conversation",
        "the summary, the text of each summary field, the description of each decision, "
        "key moment, action item and insight",
        "That language is the request's 'language' when it is a language code such as 'fa' "
        "or 'en', and otherwise the language the turns are spoken in",
        "Do not translate the conversation into English",
    ):
        assert words in SYSTEM_PROMPT, words


def test_the_prompt_keeps_quotes_and_labels_as_they_are():
    for words in (
        "Quotes are never translated: owner_quote, due_quote and entity text are copied "
        "exactly as they appear in the turns",
        "Label ids, kinds and other values the schema fixes stay as the schema gives them",
    ):
        assert words in SYSTEM_PROMPT, words


def test_the_rules_are_added_after_every_earlier_rule():
    # The prompt only grows at its end (as for HOM-53 and HOM-133).
    assert SYSTEM_PROMPT.index("Never guess an owner or a date.") < SYSTEM_PROMPT.index(
        "Write every free-text value"
    )


def test_a_persian_request_sends_the_same_user_content_and_the_new_prompt():
    payload = _adapter()._payload(REQUEST)

    system, user = payload["messages"]
    assert system == {"role": "system", "content": SYSTEM_PROMPT}
    assert user["role"] == "user"
    assert user["content"] == json.dumps(
        {
            "language": "fa",
            "transcript_text": REQUEST.transcript_text,
            "turns": [turn.model_dump(mode="json") for turn in REQUEST.turns],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
