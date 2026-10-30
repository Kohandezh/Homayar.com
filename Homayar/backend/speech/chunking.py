"""Long-call chunk planning and the merge of chunk transcripts (HOM-40, ADR 0046).

A long call is transcribed in chunks. :func:`plan_chunks` cuts the call's
16 kHz samples into chunks that **own** a part of the timeline. The owned parts
partition ``[0, total)``. Each chunk's audio also reaches ``overlap`` samples
past every shared cut, so speech at a cut is heard by both chunks. A cut is
placed in the middle of the longest inactive gap near the target length (the
gaps come from ``signal-activity/1``, a signal heuristic, not speech
detection), else at the target.

:func:`merge_chunk_segments` maps chunk segments back to original time, keeps a
segment only in the chunk that owns its midpoint, and removes a word run that
both chunks heard at a seam. Everything here is pure: integers in, integers and
exact fractions inside, floats only at the edge.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Final

from backend.providers.schemas import ASRResult, ASRSegment

CHUNKING_VERSION: Final = "asr-chunking/1"


@dataclass(frozen=True)
class ChunkSettings:
    min_call_seconds: float = 1200.0  # calls at or under this length: one chunk
    target_seconds: float = 600.0  # wanted owned length of a chunk
    search_seconds: float = 30.0  # cut is searched within +- this around the target
    overlap_seconds: float = 2.0  # audio given past each shared cut, on both sides
    min_gap_seconds: float = 0.2  # an inactive gap shorter than this is not a cut place

    def __post_init__(self) -> None:
        for name in (
            "min_call_seconds",
            "target_seconds",
            "search_seconds",
            "overlap_seconds",
            "min_gap_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"{name} must be a number")
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not self.target_seconds > 0:
            raise ValueError("target_seconds must be greater than 0")
        if not 0 <= self.search_seconds < self.target_seconds / 2:
            raise ValueError("search_seconds must be at least 0 and under target_seconds / 2")
        if not 0 <= self.overlap_seconds <= self.search_seconds:
            raise ValueError("overlap_seconds must be between 0 and search_seconds")
        if not self.min_call_seconds >= self.target_seconds + self.search_seconds:
            raise ValueError("min_call_seconds must be at least target_seconds + search_seconds")
        if not self.min_gap_seconds >= 0:
            raise ValueError("min_gap_seconds must be at least 0")

    def descriptor(self) -> dict[str, Any]:
        """The canonical, JSON-safe identity of this chunking (checkpoint input)."""
        return {
            "version": CHUNKING_VERSION,
            "min_call_seconds": float(self.min_call_seconds),
            "target_seconds": float(self.target_seconds),
            "search_seconds": float(self.search_seconds),
            "overlap_seconds": float(self.overlap_seconds),
            "min_gap_seconds": float(self.min_gap_seconds),
        }


@dataclass(frozen=True)
class ChunkPlan:
    index: int
    start_sample: int  # first sample given to ASR (inclusive)
    end_sample: int  # end of the audio given to ASR (exclusive)
    owned_start_sample: int  # this chunk owns [owned_start_sample, owned_end_sample)
    owned_end_sample: int
    sample_rate: int


def _samples(seconds: float, sample_rate: int) -> int:
    return round(seconds * sample_rate)


def _gaps(total: int, active: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """The complement of ``active`` inside ``[0, total)``, as half-open ranges."""
    gaps: list[tuple[int, int]] = []
    cursor = 0
    for start, end in sorted(active):
        start, end = max(0, start), min(total, end)
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < total:
        gaps.append((cursor, total))
    return gaps


def plan_chunks(
    total_samples: int,
    *,
    sample_rate: int,
    settings: ChunkSettings,
    active: Sequence[tuple[int, int]] | None = None,
) -> list[ChunkPlan]:
    """Cut ``total_samples`` into chunks (ADR 0046 planning rules).

    ``active`` holds sorted half-open active sample ranges; ``None`` means no
    activity information (every cut is at the target).
    """
    if isinstance(total_samples, bool) or not isinstance(total_samples, int) or total_samples < 1:
        raise ValueError("total_samples must be a positive int")
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate < 1:
        raise ValueError("sample_rate must be a positive int")
    total = total_samples
    min_call = _samples(settings.min_call_seconds, sample_rate)
    target = _samples(settings.target_seconds, sample_rate)
    search = _samples(settings.search_seconds, sample_rate)
    overlap = _samples(settings.overlap_seconds, sample_rate)
    min_gap = _samples(settings.min_gap_seconds, sample_rate)
    if total <= min_call:
        return [ChunkPlan(0, 0, total, 0, total, sample_rate)]

    gaps = [
        (start, end)
        for start, end in (_gaps(total, active) if active is not None else [])
        if end - start >= min_gap
    ]
    owned: list[tuple[int, int]] = []
    pos = 0
    while total - pos > target + search:
        low = pos + target - search
        high = min(pos + target + search, total - search)
        best: tuple[int, int] | None = None
        for start, end in gaps:
            start, end = max(start, low), min(end, high)
            if end > start and (best is None or end - start > best[1] - best[0]):
                best = (start, end)
        cut = (best[0] + best[1]) // 2 if best is not None else pos + target
        owned.append((pos, cut))
        pos = cut
    owned.append((pos, total))
    return [
        ChunkPlan(
            index=index,
            start_sample=max(0, start - overlap),
            end_sample=min(total, end + overlap),
            owned_start_sample=start,
            owned_end_sample=end,
            sample_rate=sample_rate,
        )
        for index, (start, end) in enumerate(owned)
    ]


# --- merge ----------------------------------------------------------------------------


def _word_key(word: str) -> str:
    """A word for the seam comparison: casefolded, edge punctuation removed."""
    start, end = 0, len(word)
    while start < end and unicodedata.category(word[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(word[end - 1]).startswith("P"):
        end -= 1
    return word[start:end].casefold()


def _repeated_run(earlier: list[str], later: list[str]) -> int:
    """Length of the longest start run of ``later`` that equals a suffix of ``earlier``."""
    earlier_keys = [_word_key(word) for word in earlier]
    later_keys = [_word_key(word) for word in later]
    for length in range(min(len(earlier_keys), len(later_keys)), 0, -1):
        if later_keys[:length] == earlier_keys[-length:]:
            return length
    return 0


@dataclass
class _Piece:
    start: float
    end: float
    text: str
    speaker_label: str
    confidence: float | None


def merge_chunk_segments(
    plans: Sequence[ChunkPlan],
    results: Sequence[ASRResult],
) -> list[ASRSegment]:
    """One transcript in original time from the chunk results (ADR 0046 merge rules)."""
    if len(plans) != len(results):
        raise ValueError("every chunk plan needs exactly one result")
    kept: list[list[_Piece]] = []
    for plan, result in zip(plans, results, strict=True):
        offset = Fraction(plan.start_sample, plan.sample_rate)
        owned_start = Fraction(plan.owned_start_sample, plan.sample_rate)
        owned_end = Fraction(plan.owned_end_sample, plan.sample_rate)
        pieces: list[_Piece] = []
        for segment in result.segments:
            start = Fraction(segment.start_seconds) + offset
            end = Fraction(segment.end_seconds) + offset
            if not owned_start <= (start + end) / 2 < owned_end:
                continue
            pieces.append(
                _Piece(
                    start=float(start),
                    end=float(end),
                    text=segment.text,
                    speaker_label=segment.speaker_label,
                    confidence=segment.confidence,
                )
            )
        pieces.sort(key=lambda piece: piece.start)
        kept.append(pieces)

    for earlier_pieces, later_pieces in zip(kept, kept[1:], strict=False):
        if not earlier_pieces or not later_pieces:
            continue
        earlier, later = earlier_pieces[-1], later_pieces[0]
        if not (later.start < earlier.end and earlier.start < later.end):
            continue
        words = later.text.split()
        repeated = _repeated_run(earlier.text.split(), words)
        if repeated == 0:
            continue
        if repeated == len(words):
            later_pieces.pop(0)
            continue
        later.text = " ".join(words[repeated:])
        later.start = min(max(later.start, earlier.end), later.end)

    pieces = sorted(
        (piece for chunk in kept for piece in chunk), key=lambda piece: piece.start
    )
    return [
        ASRSegment(
            index=index,
            text=piece.text,
            start_seconds=piece.start,
            end_seconds=piece.end,
            speaker_label=piece.speaker_label,
            confidence=piece.confidence,
        )
        for index, piece in enumerate(pieces)
    ]
