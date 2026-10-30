"""JSONL input for the evaluators (HOM-69).

This is the hook for the WP13 gold corpus: each annotation layer is exported as one JSON
object per line, in the shapes below, and read back into the evaluator item types.
Unknown keys are ignored.  A missing key, a wrong type or bad JSON raises ``ValueError``;
``load_jsonl`` puts the 1-based line number in the message.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Mapping
from typing import Any

from backend.evaluation.agreement import RatingPair, ScorePair
from backend.evaluation.classification import LabeledItem
from backend.evaluation.diarization import DiarizationItem, SpeakerTurn
from backend.evaluation.entities import EntityItem, EntityMention
from backend.evaluation.factual_support import AuditedClaim, Citation, SupportLabel
from backend.evaluation.retrieval import RetrievalQuery
from backend.speech.benchmark import Utterance

KINDS: tuple[str, ...] = ("asr", "diarization", "intent", "entities", "qa_agreement",
                          "qa_score", "factual_support", "retrieval")  # fmt: skip

_MISSING = object()


def _get(rec: Mapping[str, Any], key: str, check: Callable[[Any], bool], what: str,
         default: Any = _MISSING) -> Any:  # fmt: skip
    if key not in rec:
        if default is _MISSING:
            raise ValueError(f"missing required key {key!r}")
        return default
    value = rec[key]
    if not check(value):
        raise ValueError(f"key {key!r} must be {what}, got {value!r}")
    return value


def _is_str(v: Any) -> bool:
    return isinstance(v, str)


def _is_opt_str(v: Any) -> bool:
    return v is None or isinstance(v, str)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_opt_int(v: Any) -> bool:
    return v is None or _is_int(v)


def _is_number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _is_bool(v: Any) -> bool:
    return isinstance(v, bool)


def _is_list(v: Any) -> bool:
    return isinstance(v, list)


def _is_obj(v: Any) -> bool:
    return isinstance(v, dict)


def _slices(rec: Mapping[str, Any]) -> dict[str, str]:
    slices = _get(rec, "slices", _is_obj, "an object", {})
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in slices.items()):
        raise ValueError("key 'slices' must map strings to strings")
    return dict(slices)


def _objects(rec: Mapping[str, Any], key: str, default: Any = _MISSING) -> list[Mapping[str, Any]]:
    items = _get(rec, key, _is_list, "a list", default)
    for x in items:
        if not isinstance(x, dict):
            raise ValueError(f"key {key!r} must be a list of objects, got {x!r}")
    return items


def _check_interval(key: str, start: int, end: int) -> None:
    if start < 0 or end <= start:
        raise ValueError(f"{key!r} needs 0 <= start < end, got [{start}, {end}]")


def _check_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite number {value!r}")
    if isinstance(value, dict):
        for v in value.values():
            _check_finite(v)
    elif isinstance(value, list):
        for v in value:
            _check_finite(v)


def _turns(rec: Mapping[str, Any], key: str) -> list[SpeakerTurn]:
    turns = []
    for t in _objects(rec, key):
        turn = SpeakerTurn(_get(t, "speaker", _is_str, "a string"),
                           _get(t, "start_ms", _is_int, "an int"),
                           _get(t, "end_ms", _is_int, "an int"))  # fmt: skip
        _check_interval(f"{key} turn", turn.start_ms, turn.end_ms)
        turns.append(turn)
    return turns


def _mentions(rec: Mapping[str, Any], key: str) -> list[EntityMention]:
    return [
        EntityMention(_get(m, "type", _is_str, "a string"),
                      _get(m, "value", _is_str, "a string"),
                      _get(m, "start", _is_opt_int, "an int or null", None),
                      _get(m, "end", _is_opt_int, "an int or null", None))
        for m in _objects(rec, key)
    ]  # fmt: skip


def _asr(rec: Mapping[str, Any]) -> Utterance:
    return Utterance(_get(rec, "reference", _is_str, "a string"),
                     _get(rec, "hypothesis", _is_str, "a string"),
                     _get(rec, "language", _is_str, "a string", "fa"),
                     _slices(rec))  # fmt: skip


def _diarization(rec: Mapping[str, Any]) -> DiarizationItem:
    region = _get(rec, "scored_region", lambda v: v is None or (
        _is_list(v) and len(v) == 2 and all(_is_int(x) for x in v)),
        "[start_ms, end_ms] or null", None)  # fmt: skip
    if region is not None:
        _check_interval("scored_region", region[0], region[1])
    return DiarizationItem(_get(rec, "recording_id", _is_str, "a string"),
                           _turns(rec, "reference"), _turns(rec, "hypothesis"),
                           tuple(region) if region is not None else None,
                           _slices(rec))  # fmt: skip


def _intent(rec: Mapping[str, Any]) -> LabeledItem:
    return LabeledItem(_get(rec, "gold", _is_opt_str, "a string or null"),
                       _get(rec, "predicted", _is_opt_str, "a string or null"),
                       _get(rec, "item_id", _is_str, "a string", ""),
                       _slices(rec))  # fmt: skip


def _entities(rec: Mapping[str, Any]) -> EntityItem:
    return EntityItem(_mentions(rec, "gold"), _mentions(rec, "predicted"),
                      _get(rec, "item_id", _is_str, "a string", ""),
                      _slices(rec))  # fmt: skip


def _qa_agreement(rec: Mapping[str, Any]) -> RatingPair:
    return RatingPair(_get(rec, "rating_a", _is_opt_str, "a string or null"),
                      _get(rec, "rating_b", _is_opt_str, "a string or null"),
                      _get(rec, "criterion", _is_str, "a string", ""),
                      _get(rec, "item_id", _is_str, "a string", ""),
                      _slices(rec))  # fmt: skip


def _qa_score(rec: Mapping[str, Any]) -> ScorePair:
    return ScorePair(_get(rec, "score", _is_number, "a number"),
                     _get(rec, "reference", _is_number, "a number"),
                     _get(rec, "item_id", _is_str, "a string", ""),
                     _slices(rec))  # fmt: skip


def _factual_support(rec: Mapping[str, Any]) -> AuditedClaim:
    labels = {x.value for x in SupportLabel}
    label = _get(rec, "label", lambda v: v in labels, f"one of {sorted(labels)}")
    citations = [
        Citation(_get(c, "start_ms", _is_int, "an int"),
                 _get(c, "end_ms", _is_int, "an int"),
                 _get(c, "revision_id", _is_str, "a string", ""))
        for c in _objects(rec, "citations", [])
    ]  # fmt: skip
    return AuditedClaim(_get(rec, "claim_id", _is_str, "a string"),
                        SupportLabel(label), tuple(citations),
                        _get(rec, "severe", _is_bool, "a boolean", False),
                        _get(rec, "recording_duration_ms", _is_opt_int, "an int or null", None),
                        _slices(rec))  # fmt: skip


def _retrieval(rec: Mapping[str, Any]) -> RetrievalQuery:
    relevance = _get(rec, "relevance", lambda v: _is_obj(v) and all(
        _is_int(x) and x >= 0 for x in v.values()),
        "an object of doc_id to an int >= 0")  # fmt: skip
    ranked = _get(rec, "ranked", lambda v: _is_list(v) and all(_is_str(x) for x in v),
                  "a list of strings")  # fmt: skip
    return RetrievalQuery(_get(rec, "query_id", _is_str, "a string"), dict(relevance),
                          list(ranked), _slices(rec))  # fmt: skip


_PARSERS: dict[str, Callable[[Mapping[str, Any]], object]] = {
    "asr": _asr,
    "diarization": _diarization,
    "intent": _intent,
    "entities": _entities,
    "qa_agreement": _qa_agreement,
    "qa_score": _qa_score,
    "factual_support": _factual_support,
    "retrieval": _retrieval,
}


def _parser(kind: str) -> Callable[[Mapping[str, Any]], object]:
    if kind not in _PARSERS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {KINDS}")
    return _PARSERS[kind]


def parse_record(kind: str, record: Mapping[str, Any]) -> object:
    parse = _parser(kind)
    if not isinstance(record, Mapping):
        raise ValueError(f"a record must be a JSON object, got {type(record).__name__}")
    _check_finite(dict(record))
    return parse(record)


def load_jsonl(kind: str, path: str | os.PathLike[str]) -> list[object]:
    _parser(kind)
    items: list[object] = []
    with open(path, "rb") as fh:
        for n, raw in enumerate(fh, 1):
            try:
                line = raw.decode("utf-8")
                if not line.strip():
                    continue
                items.append(parse_record(kind, json.loads(line)))
            except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
                raise ValueError(f"{os.fspath(path)}: line {n}: {exc}") from exc
    return items
