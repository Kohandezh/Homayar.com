"""The taxonomy extraction of one analysis: judged, cited, stored (HOM-53).

A provider result with an ``extraction`` is judged with the pinned taxonomy and
policy (``evaluate_extraction``) against the turns the answering model received.
On a redacted run those turns hold placeholders, so an entity text is checked
against what the model saw and the stored text is never the original value.

Every kept value (auto-accepted or for review; never a withheld one) cites its
turns as ``extraction`` evidence spans.  Its ``finding_index`` counts the kept
values from 0 over the dimensions in taxonomy order (intent, topic, entity,
outcome), and inside a field in the order of the stored result.  The result is
stored unchanged, one immutable ``AnalysisExtraction`` row per run.  Any other JSON
value (an array, a string) is judged too, and stored as invalid.

No row is stored when there is nothing to judge: a legacy result or no analysis
(reason ``not_requested``), or an output that left the extraction out
(``missing_from_output``, one warning log).  The rest of the analysis is kept either
way.  Nothing here logs or audits a label or a text.

The call report reads the row of the current run (``current_extraction``), the
label names of the taxonomy version the row was judged with (``label_names``), and
where in a turn an entity text was quoted (``quoted_region``).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.analysis_taxonomy import (
    DIMENSIONS,
    POLICY_V1,
    Disposition,
    PolicyResult,
    PolicyValue,
    TaxonomyError,
    evaluate_extraction,
    load_taxonomy,
)
from backend.database.saas_models import (
    AnalysisExtraction,
    AnalysisRun,
    Meeting,
    StageState,
    TranscriptRevision,
    TranscriptSegment,
)
from backend.providers.schemas import IntelligenceResult, TranscriptTurn
from backend.services.evidence_service import (
    FINDING_EXTRACTION,
    EvidencePlan,
    resolve_turn_references,
)
from backend.services.logging_service import logger
from backend.services.transcript_service import current_analysis_run
from backend.speech.text_normalize import normalize_for_matching

# The pinned taxonomy; the provider asks in the same one (``openai_compatible``).
TAXONOMY_ID = "conversation"
TAXONOMY_VERSION = 1

# ``extraction_state`` of the completion audit events.
STATE_UNAVAILABLE = "unavailable"  # no row: no analysis, or a legacy result
STATE_INVALID = "invalid"
STATE_REVIEW_REQUIRED = "review_required"
STATE_ACCEPTED = "accepted"

# The four fields of a stored result, in citation order.
EXTRACTION_FIELDS: tuple[str, ...] = tuple(DIMENSIONS)

# A current run in one of these states analysed nothing, so it shows no extraction.
_NO_ANALYSIS_STATES = frozenset({StageState.SKIPPED, StageState.UNAVAILABLE, StageState.FAILED})

# The validator folds entity texts and turns as Persian (``normalize_for_matching``).
_MATCH_LANGUAGE = "fa"
_WORD = re.compile(r"\S+")

# ``extraction_reason`` of the completion audit events when no row is stored.
REASON_MISSING = "missing_from_output"  # asked for, but the output had none
REASON_NOT_REQUESTED = "not_requested"  # a legacy result, or no analysis at all


def kept_values(result: PolicyResult) -> list[PolicyValue]:
    """The kept values in citation order (their ``finding_index`` is the position)."""
    return [value for name in DIMENSIONS for value in result.field(name).values]


def cite_extraction(
    intelligence: IntelligenceResult | None,
    *,
    model_turns: Sequence[TranscriptTurn],
    plan: EvidencePlan,
    meeting: Meeting,
    revision: TranscriptRevision,
    segments: Sequence[TranscriptSegment],
    now: datetime,
) -> PolicyResult | None:
    """Judge the extraction against ``model_turns`` and cite every kept value.

    Turn ``i`` of the request is segment ``i`` of the revision, so the indexes are
    resolved against ``segments`` like every other finding.  ``None`` when there is
    nothing to judge (see ``extraction_reason``).
    """
    if intelligence is None or intelligence.extraction is None:
        if intelligence is not None and intelligence.extraction_missing:
            logger.warning(
                "ANALYSIS_EXTRACTION_MISSING meeting_id=%s reason=%s", meeting.id, REASON_MISSING
            )
        return None
    result = evaluate_extraction(
        intelligence.extraction,
        load_taxonomy(TAXONOMY_ID, TAXONOMY_VERSION),
        [turn.text for turn in model_turns],
        POLICY_V1,
    )
    for index, value in enumerate(kept_values(result)):
        plan.cite(
            meeting=meeting,
            revision=revision,
            references=resolve_turn_references(value.source_turn_indexes, segments),
            finding_type=FINDING_EXTRACTION,
            finding_index=index,
            confidence=value.confidence,
            now=now,
        )
    return result


def store_extraction(
    db: Session, *, run: AnalysisRun, result: PolicyResult, now: datetime
) -> AnalysisExtraction:
    """The row of ``run``, with ``result.to_json()`` unchanged."""
    row = AnalysisExtraction(
        workspace_id=run.workspace_id,
        meeting_id=run.meeting_id,
        analysis_run_id=run.id,
        taxonomy_id=result.taxonomy_id,
        taxonomy_version=result.taxonomy_version,
        taxonomy_digest=result.taxonomy_digest,
        validator_version=result.validator_version,
        policy_version=result.policy_version,
        policy_digest=result.policy_digest,
        valid=result.valid,
        needs_review=result.needs_review,
        review_count=sum(
            value.disposition is Disposition.REVIEW_REQUIRED for value in kept_values(result)
        ),
        result=result.to_json(),
        created_at=now,
    )
    db.add(row)
    return row


def extraction_state(row: AnalysisExtraction | None) -> str:
    if row is None:
        return STATE_UNAVAILABLE
    if not row.valid:
        return STATE_INVALID
    return STATE_REVIEW_REQUIRED if row.needs_review else STATE_ACCEPTED


def extraction_reason(
    intelligence: IntelligenceResult | None, row: AnalysisExtraction | None
) -> str | None:
    """Why no row was stored; ``None`` when one was."""
    if row is not None:
        return None
    if intelligence is not None and intelligence.extraction_missing:
        return REASON_MISSING
    return REASON_NOT_REQUESTED


def extraction_audit_details(
    row: AnalysisExtraction | None, reason: str | None
) -> dict[str, object]:
    """How the extraction went, for the completion audit events: no label, no text."""
    return {
        "extraction_state": extraction_state(row),
        "extraction_review_count": row.review_count if row is not None else 0,
        "extraction_reason": reason,
    }


def current_extraction(
    db: Session, *, workspace_id: str, meeting_id: str
) -> AnalysisExtraction | None:
    """The row of the meeting's current analysis run; ``None`` when there is no
    current run, the run analysed nothing, or it stored no row.  Never the row of
    another run."""
    run = current_analysis_run(db, workspace_id=workspace_id, meeting_id=meeting_id)
    if run is None or run.state in _NO_ANALYSIS_STATES:
        return None
    return db.scalar(
        select(AnalysisExtraction).where(
            AnalysisExtraction.workspace_id == workspace_id,
            AnalysisExtraction.meeting_id == meeting_id,
            AnalysisExtraction.analysis_run_id == run.id,
        )
    )


def label_names(row: AnalysisExtraction) -> dict[tuple[str, str], tuple[str, str]] | None:
    """``(field, label id) -> (English name, Persian name)`` from the taxonomy version
    the row was judged with, never a newer one.  ``None`` when that version cannot be
    loaded (unknown version, or a digest that does not match)."""
    try:
        taxonomy = load_taxonomy(
            row.taxonomy_id, row.taxonomy_version, expected_digest=row.taxonomy_digest
        )
    except TaxonomyError:
        return None
    return {
        (name, label.id): (label.name_en, label.name_fa)
        for name in DIMENSIONS
        for label in taxonomy.dimension(name).labels
    }


def _fold(text: str) -> str:
    return normalize_for_matching(text, _MATCH_LANGUAGE)


def quoted_regions(turn: str, quote: str) -> list[tuple[int, int]]:
    """Every region of ``turn`` that ``quote`` quotes, in text order.

    The validator's rule: the folded quote is a whole run of tokens of the folded
    turn.  Each word of ``turn`` is folded alone so its tokens keep the word's
    offsets; this is used only when those tokens are exactly the folded turn.  A
    region is the words a matching run covers, or the exact quote inside them when
    it is there.  When the words cannot be lined up with the folded turn, the
    regions are the exact occurrences of ``quote``.
    """
    tokens: list[tuple[str, int, int]] = []
    for word in _WORD.finditer(turn):
        tokens.extend((token, word.start(), word.end()) for token in _fold(word.group()).split())
    if " ".join(token for token, _start, _end in tokens) != _fold(turn):
        if not quote:
            return []
        exact = [match.start() for match in re.finditer(re.escape(quote), turn)]
        return [(start, start + len(quote)) for start in exact]
    needle = _fold(quote).split()
    regions: list[tuple[int, int]] = []
    for first in range(len(tokens) - len(needle) + 1 if needle else 0):
        if [token for token, _s, _e in tokens[first : first + len(needle)]] == needle:
            start, end = tokens[first][1], tokens[first + len(needle) - 1][2]
            inside = turn.find(quote, start, end)
            region = (inside, inside + len(quote)) if inside >= 0 else (start, end)
            if region not in regions:
                regions.append(region)
    return regions


def quoted_region(turn: str, quote: str) -> tuple[int, int] | None:
    """The first region of ``quoted_regions``, or ``None``."""
    regions = quoted_regions(turn, quote)
    return regions[0] if regions else None
