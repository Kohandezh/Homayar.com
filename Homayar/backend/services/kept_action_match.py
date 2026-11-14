"""Match a re-analysis action to a kept voicemail action (HOM-381, ADR 0054).

On a voicemail meeting a re-analysis keeps the AI actions a person assigned
(HOM-380).  When the new run produces an action that matches such a kept
action, the kept action takes the new text and evidence instead of a
near-duplicate row being written next to it.  This module holds only the match
rule; ``jobs.processor.persist_analysis`` applies it.

The rule (ADR 0054, "Matching a kept action (HOM-381)"):

- a new action matches a kept action when both have the same non-null ``kind``
  and their descriptions, after ``normalize_for_matching`` in the revision's
  language, are close: ``difflib.SequenceMatcher(None, a, b,
  autojunk=False).ratio()`` at least ``KEPT_ACTION_MATCH_RATIO``, with ``a``
  the kept action's text and ``b`` the new action's text.  ``ratio()`` is not
  symmetric, so the order is part of the rule: the kept text comes first.
  Text that normalizes to nothing never matches;
- one to one, deterministic: every passing (kept, new) pair is sorted by ratio
  descending, then by kept order (``created_at``, then ``id``), then by the
  new action's index in the run, and pairs are taken greedily while both
  sides are still free.

The module is pure: no database, clock or network.  The normalizer is
``services.analysis_dedupe._normalizer``, the same one the in-run merge uses,
so the two folds can never drift apart.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple

from backend.services.analysis_dedupe import _normalizer

KEPT_ACTION_MATCH_RATIO = 0.8


@dataclass(frozen=True)
class KeptAction:
    """The fields of a kept AI action the match rule reads (K2, K3)."""

    id: str
    description: str
    kind: str | None
    created_at: datetime


class NewAction(NamedTuple):
    """A grounded action the run would write as a row (K2)."""

    kind: str | None
    description: str


def _ratio(kept_text: str, new_text: str) -> float:
    """The K2 ratio with the kept text first: ``ratio()`` is not symmetric, so
    the argument order is part of the rule (kept first, new second)."""
    return difflib.SequenceMatcher(None, kept_text, new_text, autojunk=False).ratio()


def match_kept_actions(
    kept: Sequence[KeptAction], new: Sequence[NewAction], *, language: str | None
) -> dict[int, int]:
    """Which new action (index in ``new``) merges into which kept action (index
    in ``kept``); a new action absent from the result is written as its own row,
    a kept action absent from the values is left unchanged."""
    normalize = _normalizer(language)
    kept_texts = [normalize(action.description) or None for action in kept]
    kept_order = sorted(
        range(len(kept)), key=lambda index: (kept[index].created_at, kept[index].id)
    )
    kept_rank = {action_index: rank for rank, action_index in enumerate(kept_order)}
    pairs: list[tuple[float, int, int]] = []
    for new_index, action in enumerate(new):
        if action.kind is None:
            continue
        text = normalize(action.description)
        if not text:
            continue
        for kept_index, kept_action in enumerate(kept):
            if kept_action.kind is None or kept_action.kind != action.kind:
                continue
            other = kept_texts[kept_index]
            if not other:
                continue
            ratio = _ratio(other, text)  # K2: the kept text is the first argument
            if ratio >= KEPT_ACTION_MATCH_RATIO:
                pairs.append((ratio, kept_index, new_index))
    pairs.sort(key=lambda pair: (-pair[0], kept_rank[pair[1]], pair[2]))
    matches: dict[int, int] = {}
    taken_kept: set[int] = set()
    for _ratio_value, kept_index, new_index in pairs:
        if kept_index in taken_kept or new_index in matches:
            continue
        taken_kept.add(kept_index)
        matches[new_index] = kept_index
    return matches
