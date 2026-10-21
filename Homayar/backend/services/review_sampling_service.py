"""Seeded review sample draws over the fully evaluated meetings (HOM-72).

The population is exactly the review denominator of the coverage counters:
the meetings in the evaluation ``fully_evaluated`` bucket of the window.  The
draw itself is ``backend.review_sampling``; this module builds its items from
stored data and the audit details that let anyone reproduce it.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from backend.database.saas_models import EvidenceStatus, TranscriptSource
from backend.review_sampling import (
    MAX_SEED,
    UNASSIGNED,
    SampleDraw,
    SampleItem,
    SamplingConfig,
    SamplingError,
    coerce_config,
    draw_sample,
    validate_seed,
)
from backend.services.coverage_service import (
    COVERAGE_VERSION,
    CoverageWindow,
    EvaluatedMeeting,
    compute_coverage,
    parse_window,
)
from backend.services.quality_service import SpeakerAttributionState

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# The shape of each meeting column a scope may name.  Scope values are stored in
# the audit row under keys the sanitizer does not mask, so free text (a phone
# number, an e-mail) must never pass.  The pure package accepts any string.
SCOPE_VALUE_SHAPES: dict[str, re.Pattern[str]] = {
    "agent_user_id": _UUID,
    "language": re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})?"),
    "source": re.compile(r"[a-z][a-z0-9_]{0,29}"),
    "team_id": _UUID,
}


def check_scope_values(config: SamplingConfig) -> None:
    """Every scope value is ``unassigned`` or has the shape of its column, else
    ``sampling_config_invalid`` (the message never repeats the value)."""
    for key, values in config.scope.items():
        for value in values:
            if value != UNASSIGNED and SCOPE_VALUE_SHAPES[key].fullmatch(value) is None:
                raise SamplingError(
                    "sampling_config_invalid",
                    f"every scope {key} value must be {UNASSIGNED!r} or a {key} value",
                )


def risk_flags_of(meeting: EvaluatedMeeting) -> list[str]:
    """The v1 risk flags, from data that exists today."""
    flags = []
    if meeting.quality.evidence == EvidenceStatus.PARTIAL.value:
        flags.append("evidence_partial")
    if meeting.quality.speaker_attribution == SpeakerAttributionState.UNKNOWN.value:
        flags.append("speaker_attribution_unknown")
    if meeting.revision_source == TranscriptSource.CORRECTION.value:
        flags.append("transcript_corrected")
    return flags


def sample_item(meeting: EvaluatedMeeting) -> SampleItem:
    return SampleItem(
        meeting.meeting_id,
        strata={
            "agent_user_id": meeting.agent_user_id,
            "language": meeting.language,
            "source": meeting.source,
            "team_id": meeting.team_id,
        },
        risk_flags=risk_flags_of(meeting),
    )


@dataclass(frozen=True)
class ReviewSample:
    window: CoverageWindow
    draw: SampleDraw

    def audit_details(self) -> dict[str, Any]:
        """Everything a later draw needs, under keys the audit sanitizer keeps
        unchanged (``seed`` is an int; ``_at``, ``_version``, ``_digest`` and
        ``_count`` keys are exempt; the config values sit under exempt or enum
        keys)."""
        return {
            "seed": self.draw.seed,
            "sampler_version": self.draw.sampler_version,
            "coverage_version": COVERAGE_VERSION,
            "window_from_at": self.window.start.isoformat(),
            "window_to_at": self.window.end.isoformat(),
            "config": self.draw.config,
            "config_digest": self.draw.config_digest,
            "population_count": self.draw.population_count,
            "population_digest": self.draw.population_digest,
            "selection_digest": self.draw.selection_digest,
            "selected_count": len(self.draw.selections),
        }


def draw_review_sample(
    db: Session,
    *,
    workspace_id: str,
    raw_from: object,
    raw_to: object,
    seed: object,
    config: object,
    recompute_max_attempts: int,
) -> ReviewSample:
    """Validate the request, then draw from the window's fully evaluated meetings.

    Raises ``CoverageWindowError`` or ``SamplingError`` before any query runs.
    Without a ``seed`` (absent or ``None``) the server picks one in ``0..MAX_SEED``.
    """
    window = parse_window(raw_from, raw_to)
    settings = coerce_config(config)
    check_scope_values(settings)
    chosen = secrets.randbelow(MAX_SEED + 1) if seed is None else validate_seed(seed)
    coverage = compute_coverage(
        db,
        workspace_id=workspace_id,
        window=window,
        recompute_max_attempts=recompute_max_attempts,
    )
    items = [sample_item(meeting) for meeting in coverage.fully_evaluated]
    return ReviewSample(window=window, draw=draw_sample(items, settings, seed=chosen))
