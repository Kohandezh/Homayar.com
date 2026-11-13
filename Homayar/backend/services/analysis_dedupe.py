"""One entry per finding in an analysis result (HOM-351).

A model can list the same action item, decision, key moment or extraction value
more than once.  ``merge_duplicate_findings`` merges exact duplicates before the
analysis is grounded and stored (``jobs.processor.persist_analysis``, shared by the
full job and the recompute):

- action items: same text after ``normalize_for_matching`` and the same kind.
  Two items with different owners stay apart, so no owner is lost; an item with
  no owner joins the first item of its text that has one (or none);
- decisions and key moments: same text after ``normalize_for_matching``
  (a key moment's kind does not matter);
- extraction values, within one field: the key of the taxonomy validator's
  duplicate check (``analysis_taxonomy.validator`` tier 4), that is the label id,
  and for an entity also its text folded exactly as the validator folds it
  (Persian, whatever the call language), so the merge and the validator agree.

Free text other than entity text is normalized in the call's language (Persian
when that language has no normalizer).  A merge keeps the first item at its place,
the sorted union of the cited turns and the first owner and due date that are set.
There is no fuzzy matching: items whose text differs stay apart.  Insights are
never merged.  Text that normalizes to nothing is never merged.

The function is pure (no database, clock or network), never changes its input
and never raises on a valid ``IntelligenceResult``: an extraction that is not the
expected shape is left as it is.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from typing import Any, TypeVar

from backend.analysis_taxonomy.validator import _fold as _validator_fold
from backend.providers.schemas import (
    ActionItem,
    ActionKind,
    ClaimStatus,
    Decision,
    IntelligenceResult,
    KeyMoment,
)
from backend.speech.text_normalize import normalize_for_matching

_DEFAULT_LANGUAGE = "fa"  # as in action_grounding: a language with no normalizer
_ENTITY_FIELD = "entity"

T = TypeVar("T")


def _normalizer(language: str | None) -> Callable[[str], str]:
    """``normalize_for_matching`` in ``language``; Persian when it has no normalizer."""
    chosen = language or _DEFAULT_LANGUAGE
    try:
        normalize_for_matching("", chosen)
    except (KeyError, ValueError):
        chosen = _DEFAULT_LANGUAGE
    return lambda text: normalize_for_matching(text, chosen)


def _union(*lists: Sequence[int]) -> list[int]:
    return sorted({index for indexes in lists for index in indexes})


def _group(items: Sequence[T], key: Callable[[T], Hashable | None]) -> list[list[T]]:
    """Items grouped by ``key`` in order of first appearance; a None key stays alone."""
    groups: list[list[T]] = []
    by_key: dict[Hashable, list[T]] = {}
    for item in items:
        item_key = key(item)
        if item_key is None:
            groups.append([item])
        elif item_key in by_key:
            by_key[item_key].append(item)
        else:
            group = [item]
            by_key[item_key] = group
            groups.append(group)
    return groups


def _text_key(normalize: Callable[[str], str], text: str) -> str | None:
    folded = normalize(text)
    return folded or None


class _OwnerGroup:
    """Same-text actions of one owner (``owner`` None: no owner seen yet)."""

    def __init__(self, owner: str | None, first: ActionItem) -> None:
        self.owner = owner
        self.items = [first]


def _group_actions(
    items: Sequence[ActionItem], normalize: Callable[[str], str]
) -> list[list[ActionItem]]:
    """Actions grouped by text and kind, and then by owner (review ruling R3).

    An action with an owner joins the group of that owner, else the first group
    that has no owner yet (which then takes the owner), else starts a group.  An
    action without an owner joins the first group of its text.  Owners are
    compared after normalization, so a spacing difference is the same owner."""
    groups: list[list[ActionItem]] = []
    by_text: dict[tuple[str, str], list[_OwnerGroup]] = {}
    for item in items:
        text = _text_key(normalize, item.description)
        if text is None:
            groups.append([item])
            continue
        same_text = by_text.setdefault((text, ActionKind(item.kind).value), [])
        owner = None if item.owner is None else (normalize(item.owner) or item.owner)
        if owner is None:
            target = same_text[0] if same_text else None
        else:
            target = next((group for group in same_text if group.owner == owner), None) or next(
                (group for group in same_text if group.owner is None), None
            )
        if target is None:
            target = _OwnerGroup(owner, item)
            same_text.append(target)
            groups.append(target.items)
            continue
        if target.owner is None:
            target.owner = owner
        target.items.append(item)
    return groups


def _merge_actions(group: list[ActionItem]) -> ActionItem:
    first = group[0]
    if len(group) == 1:
        return first
    # Grounding keeps an owner or a date only with its quote, so a value that comes
    # with its quote is preferred; the value and its quote always come together.
    owner_from = next(
        (item for item in group if item.owner is not None and item.owner_quote is not None),
        next((item for item in group if item.owner is not None), first),
    )
    due_from = next(
        (item for item in group if item.due_date is not None and item.due_quote is not None),
        next((item for item in group if item.due_date is not None), first),
    )
    return first.model_copy(
        update={
            "source_turn_indexes": _union(*(item.source_turn_indexes for item in group)),
            "owner": owner_from.owner,
            "owner_quote": owner_from.owner_quote,
            "due_date": due_from.due_date,
            "due_quote": due_from.due_quote,
        }
    )


def _merge_decisions(group: list[Decision]) -> Decision:
    first = group[0]
    if len(group) == 1:
        return first
    owner = next(
        (item.owner for item in group if item.owner.status == ClaimStatus.STATED), first.owner
    )
    due_date = next(
        (item.due_date for item in group if item.due_date.status == ClaimStatus.STATED),
        first.due_date,
    )
    return first.model_copy(
        update={
            "source_turn_indexes": _union(*(item.source_turn_indexes for item in group)),
            "owner": owner,
            "due_date": due_date,
        }
    )


def _merge_moments(group: list[KeyMoment]) -> KeyMoment:
    first = group[0]
    if len(group) == 1:
        return first
    return first.model_copy(
        update={"source_turn_indexes": _union(*(item.source_turn_indexes for item in group))}
    )


def _turn_list(value: Any) -> list[int] | None:
    """The value when it is a list of integers (``bool`` is not one), else None."""
    if not isinstance(value, list):
        return None
    if not all(isinstance(index, int) and not isinstance(index, bool) for index in value):
        return None
    return value


def _value_key(field: str, value: Any) -> tuple[str, str | None] | None:
    if not isinstance(value, dict):
        return None
    label = value.get("label")
    if not isinstance(label, str):
        return None
    if field != _ENTITY_FIELD:
        return (label, None)
    text = value.get("text")
    if not isinstance(text, str):
        return None
    folded = _validator_fold(text)  # R2: exactly the validator's tier-4 fold
    return (label, folded) if folded else None


def _merge_values(group: list[Any]) -> Any:
    first = group[0]
    if len(group) == 1:
        return first
    lists = [_turn_list(value.get("source_turn_indexes")) for value in group]
    merged = dict(first)
    if all(indexes is not None for indexes in lists):
        merged["source_turn_indexes"] = _union(*(indexes for indexes in lists if indexes))
    return merged


def _merge_field(field: str, answer: Any) -> Any:
    """One extraction field with its duplicate values merged; any other shape as is."""
    if not isinstance(answer, dict) or not isinstance(answer.get("values"), list):
        return answer

    def key(value: Any) -> tuple[str, str | None] | None:
        return _value_key(field, value)

    groups = _group(answer["values"], key)
    return {**answer, "values": [_merge_values(group) for group in groups]}


def _merge_extraction(extraction: Any) -> Any:
    if not isinstance(extraction, dict):
        return extraction
    return {field: _merge_field(field, answer) for field, answer in extraction.items()}


def merge_duplicate_findings(
    result: IntelligenceResult, *, language: str | None
) -> IntelligenceResult:
    """``result`` with exact duplicates merged; ``language`` picks the normalizer of
    every text but entity text (R2)."""
    normalize = _normalizer(language)
    action_items = [
        _merge_actions(group) for group in _group_actions(result.action_items, normalize)
    ]
    decisions = [
        _merge_decisions(group)
        for group in _group(result.decisions, lambda item: _text_key(normalize, item.description))
    ]
    key_moments = [
        _merge_moments(group)
        for group in _group(result.key_moments, lambda item: _text_key(normalize, item.description))
    ]
    return result.model_copy(
        update={
            "action_items": action_items,
            "decisions": decisions,
            "key_moments": key_moments,
            "extraction": _merge_extraction(result.extraction),
        }
    )
