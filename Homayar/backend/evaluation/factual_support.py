"""Factual support of generated claims, from audit labels (HOM-69).

The labels are inputs from a human audit or a separate judge.  This module never decides
support itself.  It counts labels and checks that each citation points to a real stretch
of a named transcript revision.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from backend.evaluation.common import MetricValue, metrics_dict, ratio


class SupportLabel(str, enum.Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    NOT_AUDITED = "not_audited"


INVENTED = (SupportLabel.UNSUPPORTED, SupportLabel.CONTRADICTED)


@dataclass(frozen=True)
class Citation:
    start_ms: int
    end_ms: int
    revision_id: str = ""


@dataclass(frozen=True)
class AuditedClaim:
    claim_id: str
    label: SupportLabel
    citations: Sequence[Citation] = ()
    severe: bool = False
    recording_duration_ms: int | None = None
    slices: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FactualSupportReport:
    metrics: dict[str, MetricValue]  # "support_rate", "citation_validity"
    invented_severe: int
    invented_severe_claim_ids: tuple[str, ...]  # input order
    unsupported_claim_ids: tuple[str, ...]  # UNSUPPORTED or CONTRADICTED, input order

    def as_dict(self) -> dict[str, Any]:
        return {
            "metrics": metrics_dict(self.metrics),
            "invented_severe": self.invented_severe,
            "invented_severe_claim_ids": list(self.invented_severe_claim_ids),
            "unsupported_claim_ids": list(self.unsupported_claim_ids),
        }


def citation_is_valid(citation: Citation, recording_duration_ms: int | None) -> bool:
    if not 0 <= citation.start_ms < citation.end_ms:
        return False
    if not citation.revision_id.strip():
        return False
    return recording_duration_ms is None or citation.end_ms <= recording_duration_ms


def _structurally_valid(claim: AuditedClaim) -> bool:
    return bool(claim.citations) and all(
        citation_is_valid(c, claim.recording_duration_ms) for c in claim.citations
    )


def evaluate_factual_support(claims: Iterable[AuditedClaim]) -> FactualSupportReport:
    claims = list(claims)
    audited = [c for c in claims if c.label != SupportLabel.NOT_AUDITED]
    supported = sum(1 for c in audited if c.label == SupportLabel.SUPPORTED)
    valid = sum(1 for c in claims if _structurally_valid(c))
    unsupported = tuple(c.claim_id for c in claims if c.label in INVENTED)
    severe = tuple(c.claim_id for c in claims if c.severe and c.label in INVENTED)
    metrics = {
        "support_rate": ratio("support_rate", supported, len(audited), n_items=len(audited),
                              n_excluded=len(claims) - len(audited),
                              reason="no_audited_claims"),
        "citation_validity": ratio("citation_validity", valid, len(claims),
                                   n_items=len(claims), reason="no_claims"),
    }  # fmt: skip
    return FactualSupportReport(metrics, len(severe), severe, unsupported)
