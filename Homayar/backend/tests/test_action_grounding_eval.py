"""Dev gate for action item grounding (HOM-133, ADR 0058, feature F38).

Runs ``ground_action`` over fixed provider outputs in ``dev.jsonl`` and scores the
result with ``evaluate_action_items``.  The held-out set is the reviewer's, in a
separate test.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from backend.evaluation import ActionItemCase, evaluate_action_items
from backend.providers.schemas import ActionItem, TranscriptTurn
from backend.services.action_grounding import ground_action

DEV = Path(__file__).parent / "fixtures" / "evaluation" / "action_items" / "dev.jsonl"


def _load() -> list[dict[str, Any]]:
    lines = DEV.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _score(record: dict[str, Any]) -> ActionItemCase:
    turns = [TranscriptTurn(**turn) for turn in record["turns"]]
    action = ActionItem(**record["provider_item"])
    grounded = ground_action(
        action,
        turns,
        reference=datetime.fromisoformat(record["reference"]),
        language=record["language"],
    )
    gold = record["gold"]
    return ActionItemCase(
        gold_keep=gold["keep"],
        gold_owner=gold["owner"],
        gold_due=_date(gold["due_date"]),
        gold_kind=gold["kind"],
        predicted_keep=grounded is not None,
        predicted_owner=grounded.owner if grounded else None,
        predicted_due=grounded.due_date if grounded else None,
        predicted_kind=grounded.kind if grounded else None,
        item_id=record["id"],
        language=record["language"],
        slices={"language": record["language"]},
    )


RECORDS = _load()
CASES = [_score(record) for record in RECORDS]


def test_dev_set_shape() -> None:
    ids = [record["id"] for record in RECORDS]
    assert len(ids) >= 40
    assert len(set(ids)) == len(ids)
    persian = sum(1 for record in RECORDS if record["language"] == "fa")
    assert persian > len(RECORDS) / 2
    assert any(record["language"] == "en" for record in RECORDS)


def test_dev_gate_owner_and_due_precision() -> None:
    report = evaluate_action_items(CASES)
    owner = report.metrics["owner_precision"]
    due = report.metrics["due_precision"]
    assert owner.value is not None and owner.value >= 0.95, report.as_dict()
    assert due.value is not None and due.value >= 0.95, report.as_dict()


def test_dev_gate_unstated_values_stay_unset() -> None:
    report = evaluate_action_items(CASES)
    assert report.metrics["owner_unset_rate"].value == 1.0, report.as_dict()
    assert report.metrics["due_unset_rate"].value == 1.0, report.as_dict()


def test_dev_every_kept_item_has_a_cited_turn_and_a_kind() -> None:
    for record, case in zip(RECORDS, CASES, strict=True):
        if not case.predicted_keep:
            continue
        assert case.predicted_kind in {"commitment", "next_step"}, record["id"]
        n_turns = len(record["turns"])
        assert any(0 <= i < n_turns for i in record["provider_item"]["source_turn_indexes"]), (
            record["id"]
        )


@pytest.mark.parametrize("record", RECORDS, ids=[record["id"] for record in RECORDS])
def test_dev_case_never_keeps_a_wrong_value(record: dict[str, Any]) -> None:
    case = _score(record)
    if case.predicted_owner is not None:
        assert case.gold_owner is not None
    if case.predicted_due is not None:
        assert case.predicted_due == case.gold_due
