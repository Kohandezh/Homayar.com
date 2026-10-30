"""Long-call chunk planning and merge (HOM-40, ``backend.speech.chunking``).

Pure tests: no database, no ffmpeg, no provider.
"""

from __future__ import annotations

import json
import string
from fractions import Fraction

import pytest

from backend.providers.schemas import ASRResult, ASRSegment
from backend.speech.chunking import (
    CHUNKING_VERSION,
    ChunkPlan,
    ChunkSettings,
    merge_chunk_segments,
    plan_chunks,
)

RATE = 16_000
# Small settings in samples: threshold 6 s, target 4 s, search 1 s, overlap 0.5 s.
SMALL = ChunkSettings(
    min_call_seconds=6.0, target_seconds=4.0, search_seconds=1.0, overlap_seconds=0.5
)


def _s(seconds: float) -> int:
    return round(seconds * RATE)


def _result(*segments: tuple[float, float, str], mock: bool = False) -> ASRResult:
    return ASRResult(
        provider="fake",
        model="fake-1",
        language="fa",
        text=" ".join(text for _start, _end, text in segments),
        segments=[
            ASRSegment(index=index, text=text, start_seconds=start, end_seconds=end)
            for index, (start, end, text) in enumerate(segments)
        ],
        is_mock=mock,
    )


def _assert_partition(plans: list[ChunkPlan], total: int) -> None:
    assert [plan.index for plan in plans] == list(range(len(plans)))
    assert plans[0].owned_start_sample == 0
    assert plans[-1].owned_end_sample == total
    for before, after in zip(plans, plans[1:], strict=False):
        assert before.owned_end_sample == after.owned_start_sample
    for plan in plans:
        assert plan.sample_rate == RATE
        assert plan.owned_start_sample < plan.owned_end_sample


# --- settings -------------------------------------------------------------------------


def test_default_settings_and_descriptor():
    settings = ChunkSettings()
    assert (
        settings.min_call_seconds,
        settings.target_seconds,
        settings.search_seconds,
        settings.overlap_seconds,
        settings.min_gap_seconds,
    ) == (1200.0, 600.0, 30.0, 2.0, 0.2)
    descriptor = settings.descriptor()
    assert descriptor["version"] == CHUNKING_VERSION == "asr-chunking/1"
    # JSON-safe and canonical: an int and the same float give one descriptor.
    assert json.loads(json.dumps(descriptor)) == descriptor
    assert ChunkSettings(target_seconds=600).descriptor() == descriptor
    assert ChunkSettings(target_seconds=599).descriptor() != descriptor


@pytest.mark.parametrize(
    "values",
    [
        {"target_seconds": 0},
        {"target_seconds": -1},
        {"search_seconds": -1},
        {"search_seconds": 300},  # not < target / 2
        {"overlap_seconds": -0.1},
        {"overlap_seconds": 31},  # > search
        {"min_call_seconds": 629},  # < target + search
        {"min_gap_seconds": -0.1},
        {"target_seconds": float("nan")},
        {"min_call_seconds": float("inf")},
    ],
)
def test_settings_refuse_invalid_combinations(values):
    with pytest.raises(ValueError):
        ChunkSettings(**values)


def test_settings_accept_the_edges():
    ChunkSettings(min_call_seconds=630, target_seconds=600, search_seconds=30, overlap_seconds=30)
    ChunkSettings(search_seconds=0, overlap_seconds=0, min_gap_seconds=0)


# --- planning -------------------------------------------------------------------------


@pytest.mark.parametrize("total", [1, _s(5.9), _s(6.0)])
def test_at_or_under_the_threshold_is_one_chunk(total):
    plans = plan_chunks(total, sample_rate=RATE, settings=SMALL, active=[(0, total)])
    assert plans == [ChunkPlan(0, 0, total, 0, total, RATE)]


def test_no_activity_information_cuts_at_the_target():
    total = _s(13)
    plans = plan_chunks(total, sample_rate=RATE, settings=SMALL, active=None)
    _assert_partition(plans, total)
    assert [(p.owned_start_sample, p.owned_end_sample) for p in plans] == [
        (0, _s(4)),
        (_s(4), _s(8)),
        (_s(8), total),
    ]
    # Overlap on both sides of every shared cut, clipped at 0 and at the end.
    assert [(p.start_sample, p.end_sample) for p in plans] == [
        (0, _s(4.5)),
        (_s(3.5), _s(8.5)),
        (_s(7.5), total),
    ]


def test_continuous_activity_cuts_at_the_target():
    total = _s(13)
    assert plan_chunks(total, sample_rate=RATE, settings=SMALL, active=[(0, total)]) == (
        plan_chunks(total, sample_rate=RATE, settings=SMALL, active=None)
    )


def test_cut_is_the_middle_of_the_longest_gap_inside_the_window():
    total = _s(9)
    # Window for the first cut: [3 s, 5 s]. Gaps: [3.2, 3.5) (0.3 s) and [4.0, 4.6) (0.6 s).
    active = [(0, _s(3.2)), (_s(3.5), _s(4.0)), (_s(4.6), total)]
    plans = plan_chunks(total, sample_rate=RATE, settings=SMALL, active=active)
    _assert_partition(plans, total)
    assert plans[0].owned_end_sample == (_s(4.0) + _s(4.6)) // 2


def test_a_gap_is_clipped_to_the_window_before_it_is_measured():
    total = _s(9)
    # Gap [1.0, 3.3): only 0.3 s inside the window. Gap [4.5, 4.75): 0.25 s inside.
    active = [(0, _s(1.0)), (_s(3.3), _s(4.5)), (_s(4.75), total)]
    plans = plan_chunks(total, sample_rate=RATE, settings=SMALL, active=active)
    assert plans[0].owned_end_sample == (_s(3.0) + _s(3.3)) // 2


def test_equal_gaps_pick_the_earliest():
    total = _s(9)
    active = [(0, _s(3.2)), (_s(3.5), _s(4.2)), (_s(4.5), total)]
    plans = plan_chunks(total, sample_rate=RATE, settings=SMALL, active=active)
    assert plans[0].owned_end_sample == (_s(3.2) + _s(3.5)) // 2


def test_a_gap_shorter_than_min_gap_is_not_a_cut_place():
    total = _s(9)
    active = [(0, _s(4.5)), (_s(4.65), total)]  # 0.15 s < 0.2 s
    plans = plan_chunks(total, sample_rate=RATE, settings=SMALL, active=active)
    assert plans[0].owned_end_sample == _s(4)
    longer = [(0, _s(4.5)), (_s(4.7), total)]  # 0.2 s: usable
    plans = plan_chunks(total, sample_rate=RATE, settings=SMALL, active=longer)
    assert plans[0].owned_end_sample == (_s(4.5) + _s(4.7)) // 2


def test_a_gap_outside_the_window_is_ignored():
    total = _s(9)
    active = [(0, _s(1.0)), (_s(2.0), total)]
    plans = plan_chunks(total, sample_rate=RATE, settings=SMALL, active=active)
    assert plans[0].owned_end_sample == _s(4)


def test_the_window_stops_search_before_the_end():
    # pos 4 s: total - pos = 5.6 s > 5 s; the window is [7, min(9, 9.6 - 1)] = [7, 8.6].
    total = _s(9.6)
    active = [(0, _s(8.5)), (_s(9.4), total)]  # the gap reaches only 0.1 s into the window
    plans = plan_chunks(total, sample_rate=RATE, settings=SMALL, active=active)
    assert [p.owned_end_sample for p in plans] == [_s(4), (_s(8.5) + _s(8.6)) // 2, total]


def test_silence_everywhere_cuts_in_the_middle_of_the_window():
    total = _s(13)
    plans = plan_chunks(total, sample_rate=RATE, settings=SMALL, active=[])
    _assert_partition(plans, total)
    # The whole window [pos + 3, pos + 5] is one gap: its middle is pos + 4.
    assert [p.owned_end_sample for p in plans] == [_s(4), _s(8), total]


@pytest.mark.parametrize("seconds", [6.01, 7.3, 11.9, 37.77, 123.45])
@pytest.mark.parametrize("pattern", ["none", "dense", "sparse"])
def test_lengths_stay_inside_the_bounds(seconds, pattern):
    total = _s(seconds)
    if pattern == "none":
        active = None
    elif pattern == "dense":
        active = [(start, start + _s(0.7)) for start in range(0, total, _s(0.9))]
    else:
        active = [(start, start + _s(2.5)) for start in range(0, total, _s(3.1))]
    plans = plan_chunks(total, sample_rate=RATE, settings=SMALL, active=active)
    _assert_partition(plans, total)
    assert len(plans) > 1
    for plan in plans:
        owned = plan.owned_end_sample - plan.owned_start_sample
        assert _s(1) <= owned <= _s(5)
        assert plan.start_sample == max(0, plan.owned_start_sample - _s(0.5))
        assert plan.end_sample == min(total, plan.owned_end_sample + _s(0.5))


# --- merge ----------------------------------------------------------------------------


def _two_chunks() -> list[ChunkPlan]:
    # Owned [0, 4 s) and [4 s, 9 s); audio [0, 4.5 s) and [3.5 s, 9 s).
    return plan_chunks(_s(9), sample_rate=RATE, settings=SMALL, active=None)


def test_offsets_map_segments_to_original_time_exactly():
    plans = [
        ChunkPlan(0, 0, 70_001, 0, 62_001, RATE),
        ChunkPlan(1, 54_001, 140_000, 62_001, 140_000, RATE),
    ]
    merged = merge_chunk_segments(
        plans, [_result((0.25, 1.0, "یک")), _result((1.0, 2.0, "دو"))]
    )
    assert [segment.start_seconds for segment in merged] == [
        0.25,
        float(Fraction(1) + Fraction(54_001, RATE)),
    ]
    assert merged[1].end_seconds == float(Fraction(2) + Fraction(54_001, RATE))
    assert [segment.index for segment in merged] == [0, 1]
    assert all(segment.speaker_label == "speaker_unknown" for segment in merged)


def test_a_segment_heard_in_the_overlap_is_kept_once():
    plans = _two_chunks()
    # Original 3.7 to 4.2 s (midpoint 3.95 s, owned by chunk 0); chunk 1 starts at 3.5 s.
    first = _result((1.0, 2.0, "اول"), (3.7, 4.2, "مشترک"))
    second = _result((0.2, 0.7, "مشترک"), (1.5, 2.5, "دوم"))
    merged = merge_chunk_segments(plans, [first, second])
    assert [segment.text for segment in merged] == ["اول", "مشترک", "دوم"]
    assert merged[1].start_seconds == pytest.approx(3.7)


def test_an_overlap_segment_heard_by_one_chunk_only_is_kept():
    plans = _two_chunks()
    # Original 4.1 to 4.4 s: only chunk 1 heard it; its midpoint is in chunk 1's range.
    first = _result((1.0, 2.0, "اول"))
    second = _result((0.6, 0.9, "تنها"))
    merged = merge_chunk_segments(plans, [first, second])
    assert [segment.text for segment in merged] == ["اول", "تنها"]
    # And one only chunk 0 heard (original 3.6 to 3.9 s).
    merged = merge_chunk_segments(plans, [_result((3.6, 3.9, "تنها")), _result()])
    assert [segment.text for segment in merged] == ["تنها"]


def test_a_repeated_seam_word_run_is_trimmed_once():
    plans = _two_chunks()
    # A sentence across the cut: chunk 0 hears it to 4.5 s, chunk 1 from 3.5 s.
    first = _result((2.5, 4.5, "ما قرارداد را امضا کردیم"))
    second = _result((0.0, 2.0, "را امضا، کردیم و رفتیم"))  # original 3.5 to 5.5 s
    merged = merge_chunk_segments(plans, [first, second])
    assert [segment.text for segment in merged] == ["ما قرارداد را امضا کردیم", "و رفتیم"]
    assert merged[1].start_seconds == pytest.approx(4.5)
    assert merged[1].end_seconds == pytest.approx(5.5)


def test_seam_words_compare_without_case_and_edge_punctuation():
    plans = _two_chunks()
    first = _result((3.0, 4.5, "We signed the Contract."))
    second = _result((0.2, 1.5, "the contract, and left"))
    merged = merge_chunk_segments(plans, [first, second])
    assert [segment.text for segment in merged] == ["We signed the Contract.", "and left"]


def test_a_fully_repeated_later_segment_is_dropped():
    plans = _two_chunks()
    first = _result((2.0, 4.4, "سلام خوب هستید"))
    second = _result((0.3, 0.9, "خوب هستید"), (2.0, 3.0, "بله"))
    merged = merge_chunk_segments(plans, [first, second])
    assert [segment.text for segment in merged] == ["سلام خوب هستید", "بله"]
    assert [segment.index for segment in merged] == [0, 1]


def test_the_same_words_in_non_overlapping_segments_are_kept():
    plans = _two_chunks()
    first = _result((2.0, 3.0, "بله بله"))
    second = _result((1.0, 1.5, "بله بله"))  # original 4.5 to 5.0 s: after 3.0 s
    merged = merge_chunk_segments(plans, [first, second])
    assert [segment.text for segment in merged] == ["بله بله", "بله بله"]


def test_only_a_suffix_run_is_trimmed():
    plans = _two_chunks()
    first = _result((3.0, 4.5, "الف ب ج"))
    second = _result((0.5, 1.5, "ب د"))  # "ب" is not a suffix of "الف ب ج"
    merged = merge_chunk_segments(plans, [first, second])
    assert [segment.text for segment in merged] == ["الف ب ج", "ب د"]


def test_empty_results_contribute_nothing():
    plans = plan_chunks(_s(13), sample_rate=RATE, settings=SMALL, active=None)
    merged = merge_chunk_segments(plans, [_result(), _result((1.0, 2.0, "وسط")), _result()])
    assert [(segment.index, segment.text) for segment in merged] == [(0, "وسط")]
    assert merged[0].start_seconds == pytest.approx(4.5)
    assert merge_chunk_segments(plans, [_result(), _result(), _result()]) == []


def test_output_is_sorted_and_valid():
    plans = _two_chunks()
    first = _result((3.0, 3.2, "ب"), (0.5, 0.9, "الف"))
    second = _result((5.0, 5.0, "ج"))
    merged = merge_chunk_segments(plans, [first, second])
    assert [segment.text for segment in merged] == ["الف", "ب", "ج"]
    assert [segment.index for segment in merged] == [0, 1, 2]
    for segment in merged:
        assert segment.end_seconds >= segment.start_seconds >= 0
        assert segment.text


def test_a_single_chunk_passes_through():
    total = _s(5)
    plans = plan_chunks(total, sample_rate=RATE, settings=SMALL)
    merged = merge_chunk_segments(plans, [_result((0.1, 1.0, "یک"), (1.2, 2.0, "دو"))])
    assert [(s.start_seconds, s.end_seconds, s.text) for s in merged] == [
        (0.1, 1.0, "یک"),
        (1.2, 2.0, "دو"),
    ]


# --- F26 boundary safety (AC3) ----------------------------------------------------------

WORD_SECONDS = 0.1


def _regions(cuts: list[float], total_seconds: float) -> list[tuple[str, float, float]]:
    """Speech regions that never overlap each other. Around the cuts (4, 8, 12, 16 s):
    one straddles the cut past both overlap edges, two sit inside an overlap, one
    straddles the cut inside the overlap, one reaches past one overlap edge. Ids
    are letters, so every word text is unique."""
    assert cuts == [4.0, 8.0, 12.0, 16.0]
    spans = [
        (0.2, 1.1),
        (2.7, 4.9),  # straddles 4 s; heard by both chunks in [3.5, 4.5)
        (5.5, 6.5),
        (7.55, 7.85),  # inside the overlap, before 8 s
        (8.15, 8.4),  # inside the overlap, after 8 s
        (11.8, 12.3),  # straddles 12 s inside the overlap: both chunks hear all of it
        (15.2, 16.4),  # straddles 16 s; the later chunk hears it from 15.5 s
        (total_seconds - 1.0, total_seconds - 0.1),
    ]
    names = string.ascii_lowercase
    return [(names[index], start, end) for index, (start, end) in enumerate(spans)]


def _words(region: tuple[str, float, float]) -> list[tuple[str, float, float]]:
    name, start, end = region
    count = round((end - start) / WORD_SECONDS)
    return [
        (f"{name}{index}", start + index * WORD_SECONDS, start + (index + 1) * WORD_SECONDS)
        for index in range(count)
    ]


def _fake_asr(plan: ChunkPlan, regions) -> ASRResult:
    """One segment per region the chunk's audio covers, clipped to the chunk, with
    the words that lie wholly inside the chunk's audio (chunk-relative time)."""
    begin = plan.start_sample / plan.sample_rate
    finish = plan.end_sample / plan.sample_rate
    segments = []
    for region in regions:
        words = [w for w in _words(region) if w[1] >= begin - 1e-9 and w[2] <= finish + 1e-9]
        if not words:
            continue
        start = max(region[1], begin) - begin
        end = min(region[2], finish) - begin
        segments.append((start, end, " ".join(word for word, _s0, _s1 in words)))
    return _result(*segments)


def test_annotated_regions_survive_the_cuts_within_250_ms():
    total_seconds = 21.0
    total = _s(total_seconds)
    # Regions are loud; the planner sees one long active run (no gaps): cuts at the target.
    plans = plan_chunks(total, sample_rate=RATE, settings=SMALL, active=[(0, total)])
    cuts = [plan.owned_end_sample / RATE for plan in plans[:-1]]
    assert len(cuts) >= 3
    regions = _regions(cuts, total_seconds)
    merged = merge_chunk_segments(plans, [_fake_asr(plan, regions) for plan in plans])

    spoken = [word for segment in merged for word in segment.text.split()]
    expected = [word for region in regions for word, _a, _b in _words(region)]
    assert sorted(spoken) == sorted(expected)  # every word, none twice
    assert len(spoken) == len(set(spoken))

    for name, start, end in regions:
        covered = sorted(
            (max(segment.start_seconds, start), min(segment.end_seconds, end))
            for segment in merged
            if any(word.startswith(name) and word[1:].isdigit() for word in segment.text.split())
        )
        cursor = start
        for low, high in covered:
            assert low - cursor <= 0.25, (name, cursor, low)
            cursor = max(cursor, high)
        assert end - cursor <= 0.25, (name, cursor, end)
