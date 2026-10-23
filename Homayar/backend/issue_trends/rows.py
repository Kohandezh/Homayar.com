"""Trend rows for HOM-80 and HOM-81 (HOM-79, D7).

A row turns the tally of one issue or one family into a cell with its denominator,
the pending-review count and the missing-data counts.  The provenance block names
every version and digest a trend answer depends on.  Pure: no database, no clock.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from backend.issue_trends.counting import TRENDS_VERSION, MissingBucket, StateCounts, TrendTally
from backend.issue_trends.definition import SourceState, TrendDefinition
from backend.issue_trends.filters import TrendFilters, selected_family_ids, selected_issue_ids
from backend.issue_trends.samples import (
    SAMPLE_POLICY_V1,
    Cell,
    CellKind,
    CellReason,
    CellStatus,
    SamplePolicy,
    sample_cell,
    sample_policy_digest,
    unavailable_cell,
)


class RowLevel(str, Enum):
    FAMILY = "family"
    ISSUE = "issue"


@dataclass(frozen=True)
class TrendRow:
    level: RowLevel
    family_id: str
    issue_id: str | None  # None for a family row
    # numerator = confirmed conversations; denominator = assessable conversations
    # (confirmed + pending_review + absent).
    cell: Cell
    # Assessable conversations whose issue holds but waits for a person.  Never in the
    # numerator.  None when the source is unavailable, or hidden like the numerator.
    pending_review: int | None
    # The part of the absent conversations that is absent only because another label of
    # a single-valued dimension waits for review.  Hidden like pending_review.
    absent_pending_review: int | None
    # Conversations of the population that could not be assessed, per bucket; every
    # MissingBucket key.  Empty for an unavailable source.
    missing: Mapping[MissingBucket, int]

    def to_json(self) -> dict[str, object]:
        return {
            "level": self.level.value,
            "family": self.family_id,
            "issue": self.issue_id,
            "cell": self.cell.to_json(),
            "pending_review": self.pending_review,
            "absent_pending_review": self.absent_pending_review,
            "missing": {bucket.value: count for bucket, count in self.missing.items()},
        }


def _row(
    level: RowLevel,
    family_id: str,
    issue_id: str | None,
    counts: StateCounts,
    kind: CellKind,
    policy: SamplePolicy,
) -> TrendRow:
    cell = sample_cell(counts.confirmed, counts.denominator, kind=kind, policy=policy)
    # An agent cell under the minimum hides every count of the person, like its numerator.
    hidden = cell.status is CellStatus.INSUFFICIENT_SAMPLE and cell.numerator is None
    return TrendRow(
        level=level,
        family_id=family_id,
        issue_id=issue_id,
        cell=cell,
        pending_review=None if hidden else counts.pending_review,
        absent_pending_review=None if hidden else counts.absent_pending_review,
        missing={bucket: counts.missing[bucket] for bucket in MissingBucket},
    )


def build_rows(
    tally: TrendTally,
    definition: TrendDefinition,
    filters: TrendFilters,
    *,
    kind: CellKind = CellKind.RATE,
    policy: SamplePolicy = SAMPLE_POLICY_V1,
) -> tuple[TrendRow, ...]:
    """Family rows (``selected_family_ids``) then issue rows (``selected_issue_ids``),
    each in file order.  A family whose source is unavailable gets
    ``unavailable_cell(tally.included, its source reason)``.  Raises ``ValueError`` when
    ``tally`` was made with another definition."""
    if (tally.definition_id, tally.definition_version, tally.definition_digest) != (
        definition.definition_id,
        definition.version,
        definition.digest,
    ):
        raise ValueError("the tally was made with another definition")
    kind = CellKind(kind)
    rows: list[TrendRow] = []
    for family_id in selected_family_ids(filters, definition):
        family = definition.family(family_id)
        assert family is not None
        if family.source_state is SourceState.AVAILABLE:
            rows.append(
                _row(RowLevel.FAMILY, family_id, None, tally.families[family_id], kind, policy)
            )
            continue
        assert family.source_reason is not None
        minimum = (
            policy.min_denominator_agent
            if kind is CellKind.AGENT
            else (policy.min_denominator_rate)
        )
        cell = unavailable_cell(
            tally.included, CellReason(family.source_reason.value), minimum=minimum
        )
        rows.append(TrendRow(RowLevel.FAMILY, family_id, None, cell, None, None, {}))
    for issue_id in selected_issue_ids(filters, definition):
        issue = definition.issue(issue_id)
        assert issue is not None
        rows.append(
            _row(RowLevel.ISSUE, issue.family, issue_id, tally.issues[issue_id], kind, policy)
        )
    return tuple(rows)


def trend_provenance(
    definition: TrendDefinition, policy: SamplePolicy = SAMPLE_POLICY_V1
) -> dict[str, object]:
    """The versions and digests of a trend answer (README "A trend answer"):
    ``trends_version``, ``definition``, ``taxonomy``, ``extraction_policies``,
    ``validator_versions`` and ``sample_policy``."""
    pin = definition.taxonomy
    return {
        "trends_version": TRENDS_VERSION,
        "definition": {
            "id": definition.definition_id,
            "version": definition.version,
            "digest": definition.digest,
        },
        "taxonomy": {
            "id": pin.taxonomy_id,
            "version": pin.taxonomy_version,
            "digest": pin.taxonomy_digest,
        },
        "extraction_policies": [
            {"version": p.policy_version, "digest": p.policy_digest}
            for p in definition.extraction_policies
        ],
        "validator_versions": list(definition.validator_versions),
        "sample_policy": {"version": policy.version, "digest": sample_policy_digest(policy)},
    }
