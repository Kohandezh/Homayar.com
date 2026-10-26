"""HOM-38: ``signal-activity/1``, a per-channel signal-level heuristic.

Pure: 16 kHz PCM is built in memory.  "Activity" means a channel carries signal
above its own noise floor; it is never speech detection and never sets a role.
These tests prove the arithmetic and the rules on synthetic signals only.
"""

from __future__ import annotations

import json
import math
import random
import sys
from array import array

import pytest

from backend.media.activity import (
    ACTIVITY_KIND,
    ACTIVITY_SAMPLE_RATE_HZ,
    ACTIVITY_VERSION,
    FLOOR_DBFS,
    FRAME_SAMPLES,
    HYSTERESIS_DB,
    MARGIN_DB,
    MIN_ACTIVITY_FRAMES,
    MIN_GAP_FRAMES,
    NOISE_PERCENTILE,
    RECOMMENDED_PADDING_SAMPLES,
    ActivityMeter,
    ActivityResult,
    activity_record,
    measure_activity,
    padded,
)

RATE = ACTIVITY_SAMPLE_RATE_HZ
FRAME = FRAME_SAMPLES


# --- PCM helpers ----------------------------------------------------------------------


def _amplitude(level_dbfs: float) -> float:
    """Peak amplitude (full scale 1.0) of a sine whose mean square is ``level_dbfs``."""
    return 10 ** ((level_dbfs + 10 * math.log10(2)) / 20)


def _tone(frames: int, level_dbfs: float, frequency: float = 1000.0) -> list[float]:
    # 1000 Hz at 16 kHz: 16 samples per period, 20 whole periods per frame (no DC).
    amplitude = _amplitude(level_dbfs)
    count = frames * FRAME
    return [amplitude * math.sin(2 * math.pi * frequency * n / RATE) for n in range(count)]


def _silence(frames: int) -> list[float]:
    return [0.0] * (frames * FRAME)


def _hiss(frames: int, level_dbfs: float, seed: int = 38) -> list[float]:
    # Uniform noise in [-a, a] has mean square a^2 / 3.
    amplitude = math.sqrt(3) * 10 ** (level_dbfs / 20)
    generator = random.Random(seed)  # noqa: S311 - seeded test data, not security
    return [generator.uniform(-amplitude, amplitude) for _ in range(frames * FRAME)]


def _pcm(*channels: list[float]) -> bytes:
    samples = array("h")
    for frame in zip(*channels, strict=True):
        samples.extend(max(-32768, min(32767, round(value * 32767))) for value in frame)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples.tobytes()


def _intervals(*channels: list[float]) -> list[tuple[tuple[int, int], ...]]:
    result = measure_activity(_pcm(*channels), len(channels))
    return [channel.intervals for channel in result.channels]


# --- the constants are the published version ------------------------------------------


def test_the_constants_of_the_version() -> None:
    assert ACTIVITY_VERSION == "signal-activity/1"
    assert ACTIVITY_KIND == "signal_level_heuristic"
    assert (FRAME_SAMPLES, ACTIVITY_SAMPLE_RATE_HZ) == (320, 16000)
    assert (FLOOR_DBFS, MARGIN_DB, HYSTERESIS_DB) == (-50.0, 12.0, 6.0)
    assert (NOISE_PERCENTILE, MIN_ACTIVITY_FRAMES, MIN_GAP_FRAMES) == (10, 5, 10)
    assert RECOMMENDED_PADDING_SAMPLES == 1600


# --- no activity (A6) and its allow-controls -------------------------------------------


def test_digital_silence_gives_no_interval_and_no_floor() -> None:
    result = measure_activity(_pcm(_silence(100)), 1)
    (channel,) = result.channels
    assert channel.intervals == () and channel.active_frames == 0
    assert channel.noise_floor_dbfs is None
    assert (channel.on_dbfs, channel.off_dbfs) == (FLOOR_DBFS, FLOOR_DBFS - HYSTERESIS_DB)


def test_a_constant_dc_channel_gives_no_interval() -> None:
    result = measure_activity(_pcm([0.3] * (100 * FRAME)), 1)
    assert result.channels[0].intervals == ()
    assert result.channels[0].noise_floor_dbfs is None


def test_a_hiss_below_the_floor_gives_no_interval() -> None:
    # -65 dBFS: the floor plus the margin stays below -50, so -50 is the start threshold.
    result = measure_activity(_pcm(_hiss(100, -65.0)), 1)
    (channel,) = result.channels
    assert channel.intervals == ()
    assert channel.noise_floor_dbfs == pytest.approx(-65.0, abs=1.0)
    assert channel.on_dbfs == FLOOR_DBFS


def test_a_louder_constant_hiss_gives_no_interval() -> None:
    result = measure_activity(_pcm(_hiss(100, -30.0)), 1)
    (channel,) = result.channels
    assert channel.intervals == ()
    assert channel.noise_floor_dbfs == pytest.approx(-30.0, abs=1.0)
    assert channel.on_dbfs == pytest.approx(channel.noise_floor_dbfs + MARGIN_DB)
    assert channel.off_dbfs == pytest.approx(channel.on_dbfs - HYSTERESIS_DB)


def test_a_burst_over_hiss_is_found_on_its_frames() -> None:
    signal = _hiss(50, -60.0) + _tone(25, -10.0) + _hiss(50, -60.0, seed=7)
    hiss_only = _hiss(125, -60.0, seed=9)
    burst = [a + b for a, b in zip(signal, hiss_only, strict=True)]
    assert _intervals(burst) == [((50 * FRAME, 75 * FRAME),)]


def test_a_burst_over_digital_silence_is_found_with_an_unknown_floor() -> None:
    result = measure_activity(_pcm(_silence(50) + _tone(25, -20.0) + _silence(50)), 1)
    (channel,) = result.channels
    assert channel.noise_floor_dbfs is None
    assert channel.intervals == ((50 * FRAME, 75 * FRAME),)
    assert channel.active_frames == 25


def test_a_burst_starting_inside_a_frame_is_found_within_one_frame() -> None:
    start = 50 * FRAME + 100
    signal = [0.0] * start + _tone(30, -20.0) + _silence(40)
    ((found,),) = _intervals(signal)
    assert abs(found[0] - start) <= FRAME
    assert found[0] % FRAME == 0 and found[1] % FRAME == 0


# --- the decision rules -----------------------------------------------------------------


def test_hysteresis_keeps_a_dip_above_the_off_threshold() -> None:
    # Floor unknown (digital silence dominates): on -50, off -56.
    dip = _tone(12, -53.0)  # below on, above off
    signal = _silence(60) + _tone(20, -20.0) + dip + _tone(20, -20.0) + _silence(60)
    assert _intervals(signal) == [((60 * FRAME, 112 * FRAME),)]


def test_a_dip_below_the_off_threshold_ends_the_run() -> None:
    dip = _tone(12, -60.0)  # below off: two runs, the gap of 12 frames is kept
    signal = _silence(60) + _tone(20, -20.0) + dip + _tone(20, -20.0) + _silence(60)
    assert _intervals(signal) == [((60 * FRAME, 80 * FRAME), (92 * FRAME, 112 * FRAME))]


def test_a_run_needs_the_on_threshold_to_start() -> None:
    signal = _silence(60) + _tone(20, -53.0) + _silence(60)  # between off and on only
    assert _intervals(signal) == [()]


def test_a_gap_below_min_gap_frames_is_merged() -> None:
    gap = MIN_GAP_FRAMES - 1
    signal = _silence(60) + _tone(10, -20.0) + _silence(gap) + _tone(10, -20.0) + _silence(60)
    assert _intervals(signal) == [((60 * FRAME, (80 + gap) * FRAME),)]


def test_a_gap_of_min_gap_frames_is_kept() -> None:
    gap = MIN_GAP_FRAMES
    signal = _silence(60) + _tone(10, -20.0) + _silence(gap) + _tone(10, -20.0) + _silence(60)
    assert _intervals(signal) == [
        ((60 * FRAME, 70 * FRAME), ((70 + gap) * FRAME, (80 + gap) * FRAME))
    ]


def test_a_run_below_min_activity_frames_is_dropped() -> None:
    short = MIN_ACTIVITY_FRAMES - 1
    assert _intervals(_silence(60) + _tone(short, -20.0) + _silence(60)) == [()]


def test_a_run_of_min_activity_frames_is_kept() -> None:
    frames = MIN_ACTIVITY_FRAMES
    assert _intervals(_silence(60) + _tone(frames, -20.0) + _silence(60)) == [
        ((60 * FRAME, (60 + frames) * FRAME),)
    ]


def test_short_runs_merged_by_a_small_gap_are_kept() -> None:
    # Two 3-frame runs are each too short, but merging comes first.
    signal = _silence(60) + _tone(3, -20.0) + _silence(2) + _tone(3, -20.0) + _silence(60)
    assert _intervals(signal) == [((60 * FRAME, 68 * FRAME),)]


def test_activity_at_the_very_end_is_closed() -> None:
    assert _intervals(_silence(60) + _tone(20, -20.0)) == [((60 * FRAME, 80 * FRAME),)]


def test_channels_are_measured_independently() -> None:
    left = _silence(20) + _tone(20, -20.0) + _silence(80)
    right = _silence(80) + _tone(20, -20.0) + _silence(20)
    assert _intervals(left, right) == [
        ((20 * FRAME, 40 * FRAME),),
        ((80 * FRAME, 100 * FRAME),),
    ]


def test_the_noise_floor_is_the_published_percentile() -> None:
    # 100 frames: 10 at -40, 90 at -20; index (100 - 1) * 10 // 100 = 9 is still -40.
    signal = _tone(10, -40.0) + _tone(90, -20.0)
    (channel,) = measure_activity(_pcm(signal), 1).channels
    assert channel.noise_floor_dbfs == pytest.approx(-40.0, abs=0.05)
    assert channel.on_dbfs == pytest.approx(-28.0, abs=0.05)
    assert channel.intervals == ((10 * FRAME, 100 * FRAME),)


def test_intervals_are_half_open_multiples_of_the_frame() -> None:
    signal = _silence(13) + _tone(17, -20.0) + _silence(31)
    ((interval,),) = _intervals(signal)
    assert interval == (13 * FRAME, 30 * FRAME)


# --- streaming --------------------------------------------------------------------------


def test_a_trailing_partial_frame_is_not_measured() -> None:
    signal = _silence(10) + _tone(10, -20.0) + [0.5] * 100
    result = measure_activity(_pcm(signal), 1)
    assert result.measured_samples == 20 * FRAME
    assert result.channels[0].intervals == ((10 * FRAME, 20 * FRAME),)


def test_less_than_one_frame_measures_nothing() -> None:
    result = measure_activity(_pcm([0.5] * 100), 1)
    assert result.measured_samples == 0
    assert result.channels[0].intervals == () and result.channels[0].noise_floor_dbfs is None


def test_any_split_of_the_bytes_gives_the_same_result() -> None:
    left = _hiss(70, -45.0) + _tone(30, -20.0) + _hiss(40, -45.0, seed=3)
    right = _tone(40, -25.0) + _silence(100)
    data = _pcm(left, right)
    whole = measure_activity(data, 2)
    generator = random.Random(4)  # noqa: S311 - seeded test data, not security
    for _ in range(5):
        meter = ActivityMeter(2)
        position = 0
        while position < len(data):
            step = generator.choice([1, 3, 7, 641, 1280, 4097])
            meter.feed(data[position : position + step])
            position += step
        assert meter.finish() == whole


def test_more_channels_keep_their_order() -> None:
    channels = [
        _silence(10 * index) + _tone(10, -20.0) + _silence(80 - 10 * index) for index in range(8)
    ]
    result = measure_activity(_pcm(*channels), 8)
    assert [channel.index for channel in result.channels] == list(range(8))
    for index, channel in enumerate(result.channels):
        assert channel.intervals == ((10 * index * FRAME, (10 * index + 10) * FRAME),)


@pytest.mark.parametrize("count", [0, 9, -1, True])
def test_the_channel_count_is_checked(count) -> None:
    with pytest.raises(ValueError):
        ActivityMeter(count)
    assert isinstance(ActivityMeter(8), ActivityMeter)  # allow-control


def test_the_result_is_frozen() -> None:
    result = measure_activity(_pcm(_silence(10)), 1)
    assert isinstance(result, ActivityResult)
    with pytest.raises((AttributeError, TypeError)):
        result.measured_samples = 0  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        result.channels[0].intervals = ()  # type: ignore[misc]


# --- padding ----------------------------------------------------------------------------


def test_padding_widens_clamps_and_merges() -> None:
    intervals = [(320, 640), (1600, 1920), (2240, 2560), (9600, 9920)]
    assert padded(intervals, 400, 10000) == [(0, 1040), (1200, 2960), (9200, 10000)]


def test_padding_merges_touching_intervals_and_keeps_order() -> None:
    assert padded([(1000, 2000), (100, 200)], 0, 5000) == [(100, 200), (1000, 2000)]
    assert padded([(100, 200), (300, 400)], 50, 5000) == [(50, 450)]


def test_padding_arguments_are_checked() -> None:
    with pytest.raises(ValueError):
        padded([(0, 320)], -1, 1000)
    with pytest.raises(ValueError):
        padded([(0, 320)], 10, -1)


# --- the record -------------------------------------------------------------------------


def _inspection(channels: list[dict], *, status: str = "complete", pairs=None) -> dict:
    return {
        "version": "channel-inspection/1",
        "status": status,
        "duplicate_pairs": pairs or [],
        "channels": channels,
    }


def _measured(*channels: list[float]) -> ActivityResult:
    return measure_activity(_pcm(*channels), len(channels))


def test_the_record_carries_the_version_params_and_channels() -> None:
    result = _measured(_silence(60) + _tone(20, -20.0), _hiss(80, -30.0))
    inspection = _inspection(
        [
            {"index": 0, "silent": False, "duplicated": False},
            {"index": 1, "silent": False, "duplicated": False},
        ]
    )
    record = activity_record(result, inspection)
    assert record == {
        "version": "signal-activity/1",
        "kind": "signal_level_heuristic",
        "frame_samples": 320,
        "measured_samples": 80 * FRAME,
        "params": {
            "floor_dbfs": -50.0,
            "margin_db": 12.0,
            "hysteresis_db": 6.0,
            "noise_percentile": 10,
            "min_activity_frames": 5,
            "min_gap_frames": 10,
            "recommended_padding_samples": 1600,
        },
        "channels": [
            {
                "index": 0,
                "source_channel": 0,
                "noise_floor_dbfs": None,
                "on_dbfs": -50.0,
                "off_dbfs": -56.0,
                "active_frames": 20,
                "intervals": [[60 * FRAME, 80 * FRAME]],
                "inspection_silent": False,
                "inspection_duplicated": False,
                "duplicate_of": [],
            },
            {
                "index": 1,
                "source_channel": 1,
                "noise_floor_dbfs": round(result.channels[1].noise_floor_dbfs, 2),
                "on_dbfs": result.channels[1].on_dbfs,
                "off_dbfs": result.channels[1].off_dbfs,
                "active_frames": 0,
                "intervals": [],
                "inspection_silent": False,
                "inspection_duplicated": False,
                "duplicate_of": [],
            },
        ],
    }
    json.dumps(record, allow_nan=False)
    assert "speech" not in json.dumps(record)


def test_a_channel_flagged_silent_keeps_its_intervals() -> None:
    # HOM-37 measured only the first 30 s; the full-length measure wins (SPEC D3).
    result = _measured(_silence(60) + _tone(20, -20.0), _silence(80))
    inspection = _inspection(
        [
            {"index": 0, "silent": True, "duplicated": False},
            {"index": 1, "silent": True, "duplicated": False},
        ]
    )
    record = activity_record(result, inspection)
    assert record["channels"][0]["inspection_silent"] is True
    assert record["channels"][0]["intervals"] == [[60 * FRAME, 80 * FRAME]]
    assert record["channels"][1]["intervals"] == []


def test_duplicate_flags_name_the_other_channels() -> None:
    tone = _silence(10) + _tone(20, -20.0)
    result = _measured(tone, tone, _silence(30))
    inspection = _inspection(
        [
            {"index": 0, "silent": False, "duplicated": True},
            {"index": 1, "silent": False, "duplicated": True},
            {"index": 2, "silent": True, "duplicated": False},
        ],
        pairs=[[0, 1]],
    )
    channels = activity_record(result, inspection)["channels"]
    assert [channel["duplicate_of"] for channel in channels] == [[1], [0], []]
    assert channels[0]["intervals"] == channels[1]["intervals"] != []


@pytest.mark.parametrize(
    "inspection",
    [
        None,
        _inspection([{"index": 0, "silent": None, "duplicated": None}], status="unavailable"),
        {"status": "complete"},
        "not a record",
    ],
)
def test_without_a_complete_inspection_the_flags_are_unknown(inspection) -> None:
    record = activity_record(_measured(_silence(20)), inspection)
    (channel,) = record["channels"]
    assert channel["inspection_silent"] is None
    assert channel["inspection_duplicated"] is None
    assert channel["duplicate_of"] is None


def test_an_inspection_for_another_channel_count_is_unknown_per_missing_channel() -> None:
    inspection = _inspection([{"index": 0, "silent": False, "duplicated": False}])
    channels = activity_record(_measured(_silence(20), _silence(20)), inspection)["channels"]
    assert channels[0]["inspection_silent"] is False
    assert (channels[1]["inspection_silent"], channels[1]["duplicate_of"]) == (None, None)
