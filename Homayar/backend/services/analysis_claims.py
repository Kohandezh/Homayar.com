"""Structured analysis claims: summary fields, decisions, key moments (HOM-61).

A structured provider result (``summary_fields`` is set) adds claims next to
the insights in ``MeetingSummary.key_points``.  Each claim is one entry tagged
with a ``section`` key; its position in ``key_points`` is the
``finding_index`` of its evidence spans, as it already is for an insight.
Summary fields cite ``summary`` spans, decisions and key moments cite
``insight`` spans.  A ``not_stated`` summary field is stored as such, with no
text and no evidence.  ``/summary`` shows only the untagged entries (the
insights); the call report reads every entry.  An insight labeled "decision"
in a structured result also counts as an inferred decision.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.database.saas_models import Meeting, TranscriptRevision, TranscriptSegment
from backend.providers.schemas import SUMMARY_FIELD_NAMES, ClaimStatus, IntelligenceResult
from backend.services.evidence_service import (
    FINDING_INSIGHT,
    FINDING_SUMMARY,
    EvidencePlan,
    resolve_turn_references,
)

SECTION_SUMMARY_FIELD = "summary_field"
SECTION_DECISION = "decision"
SECTION_KEY_MOMENT = "key_moment"

# ``MeetingSummary.schema_version``: 2 = the key points may hold tagged claims.
LEGACY_SUMMARY_SCHEMA_VERSION = 1
STRUCTURED_SUMMARY_SCHEMA_VERSION = 2


def claim_section(point: object) -> str | None:
    """The section of a stored key point entry; None for an insight."""
    section = point.get("section") if isinstance(point, dict) else None
    return section if isinstance(section, str) else None


@dataclass(frozen=True)
class StructuredClaims:
    """The key point entries and the ``decisions`` list of one analysis."""

    structured: bool
    entries: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def schema_version(self) -> int:
        if self.structured:
            return STRUCTURED_SUMMARY_SCHEMA_VERSION
        return LEGACY_SUMMARY_SCHEMA_VERSION

    def decisions_for(self, key_points: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """The stored ``decisions`` list for these key points.

        An insight whose category is "decision" is a decision in both shapes.
        A structured result lists its own decisions first; the report reads
        such an insight there as an inferred decision, never an explicit one.
        """
        labeled = [
            point
            for point in key_points
            if claim_section(point) is None and point["category"].casefold() == "decision"
        ]
        return [*self.decisions, *labeled] if self.structured else labeled


NO_CLAIMS = StructuredClaims(structured=False)


def cite_claims(
    intelligence: IntelligenceResult,
    *,
    plan: EvidencePlan,
    meeting: Meeting,
    revision: TranscriptRevision,
    segments: Sequence[TranscriptSegment],
    first_index: int,
    now: datetime,
) -> StructuredClaims:
    """Resolve and cite every stated claim; entries start at ``first_index``.

    Every reference goes through ``resolve_turn_references`` and the plan, so
    an index outside the analysed revision is rejected and counted, never
    repaired.  A legacy result (no ``summary_fields``) has no claims.
    """
    if intelligence.summary_fields is None:
        return NO_CLAIMS
    entries: list[dict[str, Any]] = []

    def cited(turns: list[int], finding_type: str) -> dict[str, Any]:
        references = resolve_turn_references(turns, segments)
        evidence_ids = plan.cite(
            meeting=meeting,
            revision=revision,
            references=references,
            finding_type=finding_type,
            finding_index=first_index + len(entries),
            now=now,
        )
        return {
            "confidence": None,
            "source_turn_indexes": list(turns),
            "evidence_ids": evidence_ids,
            "evidence_status": references.status.value,
        }

    for name in SUMMARY_FIELD_NAMES:
        summary_field = getattr(intelligence.summary_fields, name)
        entry: dict[str, Any] = {
            "section": SECTION_SUMMARY_FIELD,
            "category": name,
            "status": summary_field.status.value,
        }
        if summary_field.status == ClaimStatus.STATED:
            entry.update(
                description=summary_field.text,
                basis=summary_field.basis.value,
                **cited(summary_field.source_turn_indexes, FINDING_SUMMARY),
            )
        entries.append(entry)
    for decision in intelligence.decisions:
        due_date = decision.due_date.value
        entries.append(
            {
                "section": SECTION_DECISION,
                "category": "decision",
                "description": decision.description,
                "basis": decision.basis.value,
                "owner": {"status": decision.owner.status.value, "value": decision.owner.value},
                "due_date": {
                    "status": decision.due_date.status.value,
                    "value": due_date.isoformat() if due_date is not None else None,
                },
                **cited(decision.source_turn_indexes, FINDING_INSIGHT),
            }
        )
    for moment in intelligence.key_moments:
        entries.append(
            {
                "section": SECTION_KEY_MOMENT,
                "category": moment.kind,
                "description": moment.description,
                **cited(moment.source_turn_indexes, FINDING_INSIGHT),
            }
        )
    return StructuredClaims(
        structured=True,
        entries=entries,
        decisions=[entry for entry in entries if entry["section"] == SECTION_DECISION],
    )
