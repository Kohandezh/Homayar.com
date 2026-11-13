"""Reconcile speaker identities across the chunks of a long call (HOM-46, ADR 0071).

Every chunk of a chunked call (ADR 0046) is diarized on its own, so each chunk
names its speakers with local labels: ``speaker_1`` in chunk 0 and
``speaker_1`` in chunk 1 are not known to be the same person.
:func:`reconcile_chunk_speakers` joins those local labels into global
speakers. Two kinds of evidence are read: the shared audio at every cut (two
neighbour chunks hear the same seconds, so the same voice speaks in both) and
speaker embeddings. A join is made only when the evidence is clear; anything
ambiguous stays separate and is reported. A speaker identity is never
invented: an unknown label stays unknown, and a speaker count hint is applied
only when a trusted source gave it and the evidence supports the merge.
Everything here is pure: no I/O, no randomness, no clock.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

from backend.providers.schemas import (
    UNKNOWN_SPEAKER_LABEL,
    ASRChunkSpan,
    DiarizationResult,
    DiarizationSegment,
    DiarizationStatus,
    ProviderIssue,
)

RECONCILE_VERSION: Final = "speaker-reconcile/1"
_CONTIGUITY_TOLERANCE: Final = 1e-6
_FLOAT_TOLERANCE: Final = 1e-9  # a difference at least this far under a threshold fails it


@dataclass(frozen=True)
class ReconcileSettings:
    min_overlap_seconds: float = 0.5  # co-speech in shared audio needed for overlap evidence
    overlap_match: float = 0.6  # overlap agreement at or above this says "same speaker"
    embedding_match: float = 0.75  # cosine at or above this says "same speaker"
    embedding_reject: float = 0.40  # cosine under this says "different speaker"
    margin: float = 0.10  # best score must beat the runner-up by at least this
    count_support: float = 0.60  # weakest score a count-hint merge may use

    def __post_init__(self) -> None:
        for name in (
            "min_overlap_seconds",
            "overlap_match",
            "embedding_match",
            "embedding_reject",
            "margin",
            "count_support",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"{name} must be a number")
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not 0 <= self.embedding_reject < self.embedding_match <= 1:
            raise ValueError(
                "embedding_reject must be at least 0 and under embedding_match,"
                " and embedding_match at most 1"
            )
        if not 0 < self.overlap_match <= 1:
            raise ValueError("overlap_match must be greater than 0 and at most 1")
        if not 0 <= self.margin < 1:
            raise ValueError("margin must be at least 0 and under 1")
        if not 0 < self.count_support <= 1:
            raise ValueError("count_support must be greater than 0 and at most 1")
        if not self.min_overlap_seconds >= 0:
            raise ValueError("min_overlap_seconds must be at least 0")

    def descriptor(self) -> dict[str, Any]:
        """The canonical, JSON-safe identity of this reconciliation (checkpoint input)."""
        return {
            "version": RECONCILE_VERSION,
            "min_overlap_seconds": float(self.min_overlap_seconds),
            "overlap_match": float(self.overlap_match),
            "embedding_match": float(self.embedding_match),
            "embedding_reject": float(self.embedding_reject),
            "margin": float(self.margin),
            "count_support": float(self.count_support),
        }


class CountSource(str, Enum):
    CHANNEL_LAYOUT = "channel_layout"  # trusted
    OPERATOR = "operator"  # trusted
    PROVIDER_ESTIMATE = "provider_estimate"  # not trusted, never applied


@dataclass(frozen=True)
class SpeakerCountHint:
    count: int  # >= 1
    source: CountSource

    def __post_init__(self) -> None:
        if isinstance(self.count, bool) or not isinstance(self.count, int):
            raise ValueError("count must be an int")
        if self.count < 1:
            raise ValueError("count must be at least 1")


@dataclass(frozen=True)
class ClusterEvidence:
    embedding: tuple[float, ...]
    embedding_model: str


@dataclass(frozen=True)
class ChunkDiarization:
    span: ASRChunkSpan
    result: DiarizationResult  # segment times are chunk-local: 0 == span.start_seconds
    clusters: Mapping[str, ClusterEvidence] = field(default_factory=dict)  # label -> evidence


@dataclass(frozen=True, order=True)
class ClusterRef:
    chunk_index: int
    label: str


class LinkEvidence(str, Enum):
    OVERLAP = "overlap"
    EMBEDDING = "embedding"
    BOTH = "overlap_and_embedding"
    COUNT_HINT = "count_hint"


@dataclass(frozen=True)
class SpeakerLink:
    left: ClusterRef  # left < right
    right: ClusterRef
    evidence: LinkEvidence
    score: float


class AmbiguityReason(str, Enum):
    NO_MARGIN = "no_margin"  # runner-up too close
    NOT_MUTUAL = "not_mutual"  # a's best is b, but b's best is not a
    CONFLICTING_EVIDENCE = "conflicting_evidence"  # overlap says same, embedding says different
    CANNOT_LINK = "cannot_link"  # link would join two clusters of one chunk


@dataclass(frozen=True)
class AmbiguousMatch:
    cluster: ClusterRef
    candidates: tuple[ClusterRef, ...]  # sorted
    reason: AmbiguityReason


class CountDecision(str, Enum):
    NO_HINT = "no_hint"
    UNTRUSTED_SOURCE = "untrusted_source"
    ALREADY_MET = "already_met"
    APPLIED = "applied"
    NOT_SUPPORTED = "not_supported"
    FEWER_THAN_HINT = "fewer_than_hint"


@dataclass(frozen=True)
class SpeakerReconciliation:
    result: DiarizationResult  # original time, global labels
    labels: Mapping[ClusterRef, str]  # every known chunk cluster -> its global label
    links: tuple[SpeakerLink, ...]  # sorted by (left, right)
    ambiguous: tuple[AmbiguousMatch, ...]  # sorted by (cluster, reason)
    count_decision: CountDecision
    version: str = RECONCILE_VERSION


# --- ranges and vectors -----------------------------------------------------------------


def _merge_ranges(ranges: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Sorted, non-overlapping copy of ``ranges`` (their union as half-open)."""
    merged: list[list[float]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _clip_ranges(
    ranges: Sequence[tuple[float, float]], start: float, end: float
) -> list[tuple[float, float]]:
    """``ranges`` cut down to ``[start, end)``; empty pieces are dropped."""
    return [
        (max(low, start), min(high, end))
        for low, high in ranges
        if max(low, start) < min(high, end)
    ]


def _intersect_ranges(
    left: Sequence[tuple[float, float]], right: Sequence[tuple[float, float]]
) -> list[tuple[float, float]]:
    """The intersection of two sorted, non-overlapping range lists."""
    result: list[tuple[float, float]] = []
    i = j = 0
    while i < len(left) and j < len(right):
        low = max(left[i][0], right[j][0])
        high = min(left[i][1], right[j][1])
        if low < high:
            result.append((low, high))
        if left[i][1] < right[j][1]:
            i += 1
        else:
            j += 1
    return result


def _total_seconds(ranges: Sequence[tuple[float, float]]) -> float:
    return sum(high - low for low, high in ranges)


def _cosine(left: ClusterEvidence | None, right: ClusterEvidence | None) -> float | None:
    """The cosine of the two embeddings, or ``None`` when there is no evidence."""
    if left is None or right is None:
        return None
    if left.embedding_model != right.embedding_model:
        return None
    if len(left.embedding) != len(right.embedding) or not left.embedding:
        return None
    if any(not math.isfinite(value) for value in left.embedding):
        return None
    if any(not math.isfinite(value) for value in right.embedding):
        return None
    left_norm = math.sqrt(sum(value * value for value in left.embedding))
    right_norm = math.sqrt(sum(value * value for value in right.embedding))
    if left_norm == 0.0 or right_norm == 0.0:
        return None
    dot = sum(a * b for a, b in zip(left.embedding, right.embedding, strict=True))
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


# --- union-find ---------------------------------------------------------------------------


def _find(parent: dict[ClusterRef, ClusterRef], ref: ClusterRef) -> ClusterRef:
    root = ref
    while parent[root] != root:
        root = parent[root]
    while parent[ref] != root:  # path compression
        parent[ref], ref = root, parent[ref]
    return root


def _chunk_indexes(members: dict[ClusterRef, set[ClusterRef]], root: ClusterRef) -> set[int]:
    return {ref.chunk_index for ref in members[root]}


# --- the reconciliation --------------------------------------------------------------------


def reconcile_chunk_speakers(
    chunks: Sequence[ChunkDiarization],
    *,
    settings: ReconcileSettings | None = None,  # None -> ReconcileSettings()
    count_hint: SpeakerCountHint | None = None,
) -> SpeakerReconciliation:
    """Join per-chunk speaker labels into one diarization with global labels."""
    settings = ReconcileSettings() if settings is None else settings
    if len(chunks) == 0:
        raise ValueError("chunks must not be empty")
    for index, chunk in enumerate(chunks):
        if chunk.span.index != index:
            raise ValueError("chunk span indexes must match their position")
    for before, after in zip(chunks, chunks[1:], strict=False):
        gap = abs(before.span.owned_end_seconds - after.span.owned_start_seconds)
        if gap > _CONTIGUITY_TOLERANCE:
            raise ValueError("owned ranges must be contiguous")

    # Known clusters and their speech, per available chunk, in original time (R3, R4).
    speech: list[dict[str, list[tuple[float, float]]]] = []
    for chunk in chunks:
        ranges: dict[str, list[tuple[float, float]]] = {}
        if chunk.result.status != DiarizationStatus.UNAVAILABLE:
            offset = chunk.span.start_seconds
            for segment in chunk.result.segments:
                if segment.speaker_label == UNKNOWN_SPEAKER_LABEL:
                    continue
                ranges.setdefault(segment.speaker_label, []).append(
                    (segment.start_seconds + offset, segment.end_seconds + offset)
                )
        speech.append({label: _merge_ranges(values) for label, values in ranges.items()})

    clusters = sorted(
        ClusterRef(index, label) for index, labels in enumerate(speech) for label in labels
    )
    by_chunk: list[list[ClusterRef]] = [
        [ref for ref in clusters if ref.chunk_index == index] for index in range(len(chunks))
    ]

    # Overlap evidence: adjacent chunks only, inside the shared audio (R5).
    overlap: dict[tuple[ClusterRef, ClusterRef], float] = {}
    for index in range(len(chunks) - 1):
        if not speech[index] or not speech[index + 1]:
            continue  # never read evidence across an unavailable chunk
        region = (chunks[index + 1].span.start_seconds, chunks[index].span.end_seconds)
        sides = []
        for side in (index, index + 1):
            sides.append(
                {
                    ref: _clip_ranges(speech[side][ref.label], region[0], region[1])
                    for ref in by_chunk[side]
                }
            )
        for a, a_ranges in sides[0].items():
            for b, b_ranges in sides[1].items():
                co = _total_seconds(_intersect_ranges(a_ranges, b_ranges))
                smaller = min(_total_seconds(a_ranges), _total_seconds(b_ranges))
                if smaller <= 0.0 or co < settings.min_overlap_seconds:
                    continue
                overlap[(a, b)] = co / smaller

    # Embedding evidence: any two chunks (R6).
    cosine: dict[tuple[ClusterRef, ClusterRef], float] = {}
    for position, a in enumerate(clusters):
        for b in clusters[position + 1 :]:
            if b.chunk_index == a.chunk_index:
                continue
            value = _cosine(
                chunks[a.chunk_index].clusters.get(a.label),
                chunks[b.chunk_index].clusters.get(b.label),
            )
            if value is not None:
                cosine[(a, b)] = value

    def pair_state(a: ClusterRef, b: ClusterRef) -> tuple[str, float, LinkEvidence | None]:
        """("conflict", 0.0, None), ("none", 0.0, None) or ("candidate", score, evidence)."""
        ov = overlap.get((a, b))
        cos = cosine.get((a, b))
        overlap_ok = ov is not None and ov >= settings.overlap_match
        emb_ok = cos is not None and cos >= settings.embedding_match
        emb_reject = cos is not None and cos < settings.embedding_reject
        if overlap_ok and emb_reject:
            return "conflict", 0.0, None
        if overlap_ok and emb_ok:
            return "candidate", min(ov, cos), LinkEvidence.BOTH
        if overlap_ok:
            return "candidate", ov, LinkEvidence.OVERLAP
        if emb_ok:
            return "candidate", cos, LinkEvidence.EMBEDDING
        return "none", 0.0, None

    # Matching: mutual best with a margin, then a union-find join (R7, R8).
    links: list[SpeakerLink] = []
    ambiguous: list[AmbiguousMatch] = []
    accepted: list[SpeakerLink] = []
    for i in range(len(chunks)):
        for j in range(i + 1, len(chunks)):
            candidates: dict[tuple[ClusterRef, ClusterRef], tuple[float, LinkEvidence]] = {}
            for a in by_chunk[i]:
                for b in by_chunk[j]:
                    state, score, evidence = pair_state(a, b)
                    if state == "conflict":
                        ambiguous.append(
                            AmbiguousMatch(a, (b,), AmbiguityReason.CONFLICTING_EVIDENCE)
                        )
                        ambiguous.append(
                            AmbiguousMatch(b, (a,), AmbiguityReason.CONFLICTING_EVIDENCE)
                        )
                    elif state == "candidate":
                        candidates[(a, b)] = (score, evidence)
            if not candidates:
                continue
            best_of_a: dict[ClusterRef, list[tuple[float, ClusterRef]]] = {}
            best_of_b: dict[ClusterRef, list[tuple[float, ClusterRef]]] = {}
            for (a, b), (score, _evidence) in candidates.items():
                best_of_a.setdefault(a, []).append((score, b))
                best_of_b.setdefault(b, []).append((score, a))
            for matches in best_of_a.values():
                matches.sort(key=lambda match: (-match[0], match[1]))
            for matches in best_of_b.values():
                matches.sort(key=lambda match: (-match[0], match[1]))
            accepted_a: set[ClusterRef] = set()
            accepted_b: set[ClusterRef] = set()
            for a, matches in sorted(best_of_a.items()):
                best_score, b = matches[0]
                runner_up = matches[1][0] if len(matches) > 1 else 0.0
                b_score, b_best = best_of_b[b][0]
                b_runner_up = best_of_b[b][1][0] if len(best_of_b[b]) > 1 else 0.0
                if b_best != a:
                    continue
                if best_score - runner_up < settings.margin - _FLOAT_TOLERANCE:
                    continue
                if b_score - b_runner_up < settings.margin - _FLOAT_TOLERANCE:
                    continue
                accepted.append(SpeakerLink(a, b, candidates[(a, b)][1], best_score))
                accepted_a.add(a)
                accepted_b.add(b)
            for a, matches in sorted(best_of_a.items()):
                if a in accepted_a:
                    continue
                b = matches[0][1]
                reason = (
                    AmbiguityReason.NOT_MUTUAL
                    if best_of_b[b][0][1] != a
                    else AmbiguityReason.NO_MARGIN
                )
                ambiguous.append(
                    AmbiguousMatch(a, tuple(sorted(partner for _s, partner in matches)), reason)
                )
            for b, matches in sorted(best_of_b.items()):
                if b in accepted_b:
                    continue
                a = matches[0][1]
                reason = (
                    AmbiguityReason.NOT_MUTUAL
                    if best_of_a[a][0][1] != b
                    else AmbiguityReason.NO_MARGIN
                )
                ambiguous.append(
                    AmbiguousMatch(b, tuple(sorted(partner for _s, partner in matches)), reason)
                )

    parent: dict[ClusterRef, ClusterRef] = {ref: ref for ref in clusters}
    members: dict[ClusterRef, set[ClusterRef]] = {ref: {ref} for ref in clusters}

    def cannot_link(a: ClusterRef, b: ClusterRef) -> bool:
        root_a, root_b = _find(parent, a), _find(parent, b)
        if root_a == root_b:
            return False
        return bool(_chunk_indexes(members, root_a) & _chunk_indexes(members, root_b))

    accepted.sort(key=lambda link: (-link.score, link.left, link.right))
    for link in accepted:
        if cannot_link(link.left, link.right):
            ambiguous.append(AmbiguousMatch(link.left, (link.right,), AmbiguityReason.CANNOT_LINK))
            ambiguous.append(AmbiguousMatch(link.right, (link.left,), AmbiguityReason.CANNOT_LINK))
            continue
        root_a, root_b = _find(parent, link.left), _find(parent, link.right)
        if root_a != root_b:
            parent[root_a] = root_b
            members[root_b] |= members.pop(root_a)
        links.append(link)

    # The count hint: trusted sources only, all or nothing (R9).
    decision = CountDecision.NO_HINT
    if count_hint is not None and count_hint.source == CountSource.PROVIDER_ESTIMATE:
        decision = CountDecision.UNTRUSTED_SOURCE
    elif count_hint is not None:
        group_count = len({_find(parent, ref) for ref in clusters})
        if group_count == count_hint.count:
            decision = CountDecision.ALREADY_MET
        elif group_count < count_hint.count:
            decision = CountDecision.FEWER_THAN_HINT
        else:
            support: list[tuple[float, ClusterRef, ClusterRef]] = []
            for position, a in enumerate(clusters):
                for b in clusters[position + 1 :]:
                    if b.chunk_index == a.chunk_index:
                        continue
                    pair = (a, b)
                    if pair_state(a, b)[0] == "conflict":
                        continue  # conflicting pairs are excluded
                    values = [
                        value
                        for value in (overlap.get(pair), cosine.get(pair))
                        if value is not None
                    ]
                    if not values:
                        continue
                    score = max(values)
                    if score >= settings.count_support:
                        support.append((score, a, b))
            support.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
            saved_parent = dict(parent)
            saved_members = {root: set(group) for root, group in members.items()}
            merges: list[SpeakerLink] = []
            count_ambiguous: list[AmbiguousMatch] = []  # kept only when the hint is applied
            for score, a, b in support:
                if len(merges) == group_count - count_hint.count:
                    break
                if cannot_link(a, b):
                    count_ambiguous.append(AmbiguousMatch(a, (b,), AmbiguityReason.CANNOT_LINK))
                    count_ambiguous.append(AmbiguousMatch(b, (a,), AmbiguityReason.CANNOT_LINK))
                    continue
                root_a, root_b = _find(parent, a), _find(parent, b)
                if root_a == root_b:
                    continue
                parent[root_a] = root_b
                members[root_b] |= members.pop(root_a)
                merges.append(SpeakerLink(a, b, LinkEvidence.COUNT_HINT, score))
            if len(merges) == group_count - count_hint.count:
                decision = CountDecision.APPLIED
                links.extend(merges)
                ambiguous.extend(count_ambiguous)
            else:
                decision = CountDecision.NOT_SUPPORTED  # none of the merges are kept
                parent = saved_parent
                members = saved_members

    # Global labels: earliest output segment first, then the smallest ref (R10).
    group_of = {ref: _find(parent, ref) for ref in clusters}
    groups: dict[ClusterRef, list[ClusterRef]] = {}
    for ref in clusters:
        groups.setdefault(group_of[ref], []).append(ref)

    pieces: list[tuple[float, float, str, ClusterRef | None, float | None]] = []
    for index, chunk in enumerate(chunks):
        if chunk.result.status == DiarizationStatus.UNAVAILABLE:
            continue
        span = chunk.span
        for segment in chunk.result.segments:
            start = segment.start_seconds + span.start_seconds
            end = segment.end_seconds + span.start_seconds
            clipped_start = max(start, span.owned_start_seconds)
            clipped_end = min(end, span.owned_end_seconds)
            if clipped_end <= clipped_start:
                continue  # empty after clipping
            ref = None
            if segment.speaker_label != UNKNOWN_SPEAKER_LABEL:
                ref = ClusterRef(index, segment.speaker_label)
            pieces.append(
                (clipped_start, clipped_end, segment.speaker_label, ref, segment.confidence)
            )

    earliest: dict[ClusterRef, float] = {}
    for start, _end, _label, ref, _confidence in pieces:
        if ref is not None:
            root = group_of[ref]
            earliest[root] = min(earliest.get(root, math.inf), start)
    with_segments = sorted(
        (root for root in groups if root in earliest),
        key=lambda root: (earliest[root], groups[root][0]),
    )
    without_segments = sorted(
        (root for root in groups if root not in earliest), key=lambda root: groups[root][0]
    )
    labels: dict[ClusterRef, str] = {
        ref: f"speaker_{position}"
        for position, root in enumerate(with_segments + without_segments, 1)
        for ref in groups[root]
    }

    segments = [
        DiarizationSegment(
            speaker_label=(labels[ref] if ref is not None else label),
            start_seconds=start,
            end_seconds=end,
            confidence=confidence,
        )
        for start, end, label, ref, confidence in pieces
    ]
    segments.sort(
        key=lambda segment: (segment.start_seconds, segment.end_seconds, segment.speaker_label)
    )

    # The merged result (R4, R11).
    available = [chunk for chunk in chunks if chunk.result.status != DiarizationStatus.UNAVAILABLE]
    source = available[0] if available else chunks[0]
    issue: ProviderIssue | None = None
    status = DiarizationStatus.AVAILABLE
    if not available:
        status = DiarizationStatus.UNAVAILABLE
        issue = ProviderIssue(
            code="diarization_unavailable",
            message="diarization was unavailable for every chunk",
            retryable=False,
        )
    elif len(available) < len(chunks):
        issue = ProviderIssue(
            code="diarization_partial",
            message=(
                f"diarization was unavailable for {len(chunks) - len(available)}"
                f" of {len(chunks)} chunks"
            ),
            retryable=False,
        )
    result = DiarizationResult(
        provider=source.result.provider,
        status=status,
        segments=segments,
        issue=issue,
        is_mock=any(chunk.result.is_mock for chunk in chunks),
    )

    links.sort(key=lambda link: (link.left, link.right))
    deduped: list[AmbiguousMatch] = []
    seen: set[AmbiguousMatch] = set()
    for match in ambiguous:
        if match not in seen:
            seen.add(match)
            deduped.append(match)
    deduped.sort(key=lambda match: (match.cluster, match.reason.value))

    return SpeakerReconciliation(
        result=result,
        labels=labels,
        links=tuple(links),
        ambiguous=tuple(deduped),
        count_decision=decision,
    )
