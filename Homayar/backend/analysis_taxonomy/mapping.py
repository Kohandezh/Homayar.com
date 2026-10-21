"""Existing rule-based ids mapped onto the conversation taxonomy (HOM-52, D8).

The key is ``(source, external id)``; the value is a label id of the matching dimension
or an abstain value.  Ids that are the same in both places are not listed: the voicemail
intents and topics and the speech entity types are taxonomy ids already.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

EXTERNAL_ID_MAP: Mapping[tuple[str, str], str] = MappingProxyType(
    {
        # Voicemail OTHER: words were there, but no rule supported a category.
        ("voicemail.intent", "unknown"): "insufficient_evidence",
        # The speech slot marks where a name is said and never guesses it.
        ("speech.entity", "person_name_slot"): "person_name",
    }
)
