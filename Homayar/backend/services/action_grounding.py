"""Ground an AI action item's owner and due date in the words of the call (HOM-133, ADR 0058).

A provider may name an owner or a due date that nobody said.  This module
keeps a value only when the provider also quoted the exact words of a cited
turn that state it.  Anything it cannot check stays unset for the reviewer.

Rules (ADR 0058):

- G1. No cited turn in range: the item is dropped (``None``).
- G2. The owner is kept only when ``owner_quote`` is inside a cited turn and
  either names the owner as a whole word (G2b), or the item is a
  ``commitment`` and the owner is that turn's speaker label (G2a), which is
  not the unknown speaker label (G2c).
- G3. The due date is kept only when ``due_quote`` is inside a cited turn and
  holds exactly one unambiguous date that equals ``due_date`` (G3b).
- G4. ``kind`` is the provider's label.
- G5. Pure: no database, no clock, no network, and it never raises.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from backend.providers.schemas import (
    UNKNOWN_SPEAKER_LABEL,
    ActionItem,
    ActionKind,
    TranscriptTurn,
)
from backend.speech.dates import extract_temporal
from backend.speech.text_normalize import normalize_for_matching

OWNER_EXPLICIT = "explicit"
VALUE_UNSET = "unset"

_DEFAULT_LANGUAGE = "fa"


@dataclass(frozen=True)
class GroundedAction:
    kind: str  # "commitment" | "next_step"
    owner: str | None  # None unless explicit
    due_date: date | None  # None unless explicit
    owner_status: str  # "explicit" | "unset"
    due_status: str  # "explicit" | "unset"
    turn_indexes: tuple[int, ...]  # the resolved cited turns, first-seen order, no duplicates


def _norm(text: str, language: str) -> str:
    try:
        return normalize_for_matching(text, language).strip()
    except KeyError:
        # A language with no normalizer: use the default one, never raise (G5).
        return normalize_for_matching(text, _DEFAULT_LANGUAGE).strip()


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _whole_word_in(needle: str, haystack: str) -> bool:
    """``needle`` occurs in ``haystack`` with no letter or digit next to it (G2b)."""
    if not needle:
        return False
    return re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", haystack) is not None


def _cited_indexes(action: ActionItem, turn_count: int) -> tuple[int, ...]:
    seen: dict[int, None] = {}
    for index in action.source_turn_indexes:
        if 0 <= index < turn_count:
            seen.setdefault(index, None)
    return tuple(seen)


def _quote_turns(quote: str | None, cited: Sequence[TranscriptTurn], language: str):
    """The cited turns whose text holds ``quote`` (plain substring after norm)."""
    if _blank(quote):
        return []
    assert quote is not None
    norm_quote = _norm(quote, language)
    if not norm_quote:
        return []
    return [turn for turn in cited if norm_quote in _norm(turn.text, language)]


def _ground_owner(
    action: ActionItem, cited: Sequence[TranscriptTurn], kind: str, language: str
) -> str | None:
    owner = action.owner
    if _blank(owner):
        return None
    assert owner is not None
    turns = _quote_turns(action.owner_quote, cited, language)
    if not turns:
        return None
    assert action.owner_quote is not None
    name_path = _whole_word_in(_norm(owner, language), _norm(action.owner_quote, language))
    for turn in turns:
        if name_path:
            return owner
        if (
            kind == ActionKind.COMMITMENT.value
            and turn.speaker_label != UNKNOWN_SPEAKER_LABEL  # G2c: names no person
            and owner == turn.speaker_label
        ):
            return owner
    return None


def _ground_due(
    action: ActionItem,
    cited: Sequence[TranscriptTurn],
    reference: datetime,
    language: str,
) -> date | None:
    due = action.due_date
    if due is None:
        return None
    if not _quote_turns(action.due_quote, cited, language):
        return None
    assert action.due_quote is not None
    try:
        matches = extract_temporal(action.due_quote, reference)
    except Exception:  # noqa: BLE001 - G5: an unreadable quote leaves the date unset
        return None
    dates = [match for match in matches if match.kind == "date"]
    if len(dates) != 1:
        return None
    only = dates[0]
    if only.ambiguous or only.value != due:
        return None
    return due


def ground_action(
    action: ActionItem,
    turns: Sequence[TranscriptTurn],
    *,
    reference: datetime,
    language: str = _DEFAULT_LANGUAGE,
) -> GroundedAction | None:
    """Keep only what the cited turns state; ``None`` when nothing is cited (G1)."""
    indexes = _cited_indexes(action, len(turns))
    if not indexes:
        return None
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    cited = [turns[index] for index in indexes]
    kind = ActionKind(action.kind).value
    owner = _ground_owner(action, cited, kind, language)
    due = _ground_due(action, cited, reference, language)
    return GroundedAction(
        kind=kind,
        owner=owner,
        due_date=due,
        owner_status=OWNER_EXPLICIT if owner is not None else VALUE_UNSET,
        due_status=OWNER_EXPLICIT if due is not None else VALUE_UNSET,
        turn_indexes=indexes,
    )
