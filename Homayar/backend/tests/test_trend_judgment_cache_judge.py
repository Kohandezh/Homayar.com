"""HOM-312: the exact faster judge, the shared tally, and the judging rules version.

``SignatureJudge`` must give judgments equal to ``judge_conversation`` for every stored row and
every conversation-level fact, also when the caller hands it the row's ``JudgedContent``
instead of the row (a cached judgment).  ``tally`` must count shared and unshared judgments the
same.  And ``JUDGMENT_VERSION`` must change whenever the judging changes: the digest of the
judgments of a fixed corpus is pinned per version.  No database.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
from typing import Any

from backend.issue_trends import (
    JUDGMENT_VERSION,
    ConversationFacts,
    SignatureJudge,
    judge_conversation,
    tally,
)
from backend.issue_trends.counting import FIELD_NAMES
from backend.services.issue_trend_service import trend_definition
from backend.tests.perf_trend_routes import _Documents

# version -> sha256 of the judgments of ``_corpus()``.  Never edit an entry: a change to the
# judging adds a new version (``JUDGMENT_VERSION``) with its new digest.
PINNED = {
    "judgment/1": "6b3e517c1ca99bc60cad7099ac97470b609815e97ab4ee08c62a1326fb966e35",
}


def _broken(rng: random.Random, result: Any) -> Any:
    """``result`` with one random defect (or none)."""
    broken = copy.deepcopy(result)
    roll = rng.randrange(14)
    field = broken["fields"][rng.choice(FIELD_NAMES)]
    if roll == 0:
        field.pop(rng.choice(sorted(field)))
    elif roll == 1:
        field["unexpected"] = 1
    elif roll == 2:
        field["verdict"] = rng.choice(("accepted", "abstained", "rejected", "maybe"))
    elif roll == 3:
        field["status"] = rng.choice((None, "stated", "not_stated", "insufficient_evidence"))
    elif roll == 4 and field["values"]:
        field["values"][0]["disposition"] = rng.choice(("auto_accepted", "review_required", "x"))
    elif roll == 5 and field["values"]:
        field["values"][0]["label"] = rng.choice(("refund", "pricing", "escalated", "resolved"))
    elif roll == 6:
        field["withheld"] = []
    elif roll == 7:
        broken["valid"] = not broken["valid"]
    elif roll == 8:
        broken["policy_version"] = "another"
    elif roll == 9:
        broken = [1, 2]
    elif roll == 10:
        broken["taxonomy_version"] = "1"
    elif roll == 11 and field["values"]:
        field["values"][0]["confidence"] = "high"
    elif roll in (12, 13):  # a consistent rejected field: extraction_invalid
        field.update(
            verdict="rejected",
            status=None,
            abstain_reason=None,
            review_required=False,
            values=[],
            withheld=[],
        )
        broken["valid"] = False
        broken["needs_review"] = any(f["review_required"] for f in broken["fields"].values())
    return broken


def _corpus(seed: int = 312, count: int = 400) -> list[Any]:
    """Realistic stored rows (the perf dataset's generator) and broken variants of them."""
    documents = _Documents(random.Random(seed))  # noqa: S311 - a fixed test corpus
    rng = random.Random(seed + 1)  # noqa: S311
    rows: list[Any] = []
    for _ in range(count):
        result = documents.row()["result"]
        rows.append(result)
        rows.append(_broken(rng, result))
    return rows


def _facts(rows: list[Any]) -> list[ConversationFacts]:
    """Each row under the conversation-level facts that decide before it, and as is."""
    facts = []
    for n, row in enumerate(rows):
        variant = n % 9
        has_run = variant != 1
        state = {2: "failed", 3: "partial"}.get(variant, "completed")
        facts.append(
            ConversationFacts(
                meeting_id=f"m{n}",
                ingestion_id=f"i{n}",
                media_group_id=f"g{n}",
                is_counted_copy=variant != 4,
                current_revision_id="r",
                transcript_is_mock=variant == 5,
                analysis_run_id=f"run{n}" if has_run else None,
                analysis_state=state if has_run else None,
                analysis_revision_id=("old" if variant == 6 else "r") if has_run else None,
                analysis_is_mock=has_run and variant == 7,
                extraction_run_id=f"run{n}" if has_run and variant != 8 else None,
                extraction=row if has_run and variant != 8 else None,
            )
        )
    return facts


def test_the_signature_judge_judges_like_judge_conversation():
    definition = trend_definition()
    facts = _facts(_corpus())
    expected = [judge_conversation(item, definition) for item in facts]
    judge = SignatureJudge(definition)
    assert [judge.judge(item) for item in facts] == expected
    # a cached judgment: the row's content handed in, the row itself not read
    cached = SignatureJudge(definition)
    with_content = []
    for item in facts:
        content = cached.content(item.extraction) if item.extraction_run_id else None
        stripped = ConversationFacts(**{**item.__dict__, "extraction": None})
        with_content.append(cached.judge(stripped, content))
    assert with_content == expected
    # the corpus reaches the conversation buckets, the exclusion and the unreadable reasons
    assert len({judgment.bucket for judgment in expected}) >= 8
    assert len({judgment.unreadable for judgment in expected}) >= 4
    assert any(judgment.exclusion is not None for judgment in expected)


def test_tally_counts_shared_and_unshared_judgments_the_same():
    definition = trend_definition()
    facts = _facts(_corpus(count=150))
    shared = [SignatureJudge(definition).judge(item) for item in facts]
    unshared = [judge_conversation(item, definition) for item in facts]
    assert tally(shared, definition) == tally(unshared, definition)


def test_a_judging_change_comes_with_a_new_judgment_version():
    definition = trend_definition()
    judgments = [judge_conversation(item, definition) for item in _facts(_corpus())]
    digest = hashlib.sha256(
        json.dumps([j.to_json() for j in judgments], sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert PINNED.get(JUDGMENT_VERSION) == digest, (
        "the judging changed: add a new JUDGMENT_VERSION with this digest",
        JUDGMENT_VERSION,
        digest,
    )
