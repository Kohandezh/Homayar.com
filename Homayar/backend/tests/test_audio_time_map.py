"""HOM-38: the exact, reversible map between derivative samples and original time.

Pure arithmetic: no media, no database.  A derivative sample index ``k`` and an
original sample position meet through ``Fraction`` multiplication by the rate
ratio, so every round trip is exact, and the points of the shared grid are whole
numbers on both sides.
"""

from __future__ import annotations

import math
import random
from fractions import Fraction

import pytest

from backend.media.time_map import ORIGINAL_TIMELINE, TIME_MAP_VERSION, TimeMap

RATES = [8000, 11025, 16000, 44100, 48000]


@pytest.mark.parametrize("rate", RATES)
def test_derivative_to_original_and_back_is_exact(rate: int) -> None:
    mapping = TimeMap(rate)
    generator = random.Random(rate)  # noqa: S311 - seeded test data, not security
    samples = [0, 1, 319, 320, 16000, 16001] + [generator.randrange(0, 10**9) for _ in range(500)]
    for k in samples:
        position = mapping.to_original(k)
        assert isinstance(position, Fraction)
        assert position == Fraction(k * rate, 16000)
        assert mapping.to_derivative(position) == k


@pytest.mark.parametrize("rate", RATES)
def test_original_to_derivative_and_back_is_exact(rate: int) -> None:
    mapping = TimeMap(rate)
    generator = random.Random(-rate)  # noqa: S311 - seeded test data, not security
    for n in [0, 1, rate - 1, rate] + [generator.randrange(0, 10**9) for _ in range(500)]:
        assert mapping.to_original(mapping.to_derivative(n)) == n


@pytest.mark.parametrize("rate", RATES)
def test_shared_grid_points_are_whole_numbers_on_both_sides(rate: int) -> None:
    mapping = TimeMap(rate)
    derivative_step, original_step = mapping.shared_grid()
    gcd = math.gcd(rate, 16000)
    assert (derivative_step, original_step) == (16000 // gcd, rate // gcd)
    for m in range(0, 50):
        k = m * derivative_step
        assert mapping.to_original(k) == m * original_step
        assert mapping.to_original(k).denominator == 1
        assert mapping.to_derivative(m * original_step) == k


def test_the_grid_of_common_rates() -> None:
    assert TimeMap(44100).shared_grid() == (160, 441)  # 10 ms
    assert TimeMap(11025).shared_grid() == (640, 441)  # 40 ms
    assert TimeMap(8000).shared_grid() == (2, 1)
    assert TimeMap(16000).shared_grid() == (1, 1)


def test_frame_bounds_map_to_exact_original_positions() -> None:
    # 20 ms frames: 320 derivative samples.  At 11025 Hz a frame is 220.5 original samples.
    assert TimeMap(11025).to_original(320 * 3) == Fraction(1323, 2)
    assert TimeMap(44100).to_original(320 * 3) == 2646
    assert TimeMap(48000).original_seconds(320 * 3) == Fraction(3, 50)


@pytest.mark.parametrize("rate", RATES)
def test_original_cover_is_the_smallest_whole_range(rate: int) -> None:
    mapping = TimeMap(rate)
    for start, end in [(0, 320), (321, 641), (1, 2), (16000, 16001), (99, 99)]:
        low, high = mapping.original_cover(start, end)
        assert low <= mapping.to_original(start) < low + 1
        assert high - 1 < mapping.to_original(end) <= high
        assert (low, high) == (
            math.floor(mapping.to_original(start)),
            math.ceil(mapping.to_original(end)),
        )


def test_original_seconds_is_exact() -> None:
    assert TimeMap(44100).original_seconds(16000) == 1
    assert TimeMap(44100, offset=8000).original_seconds(8000) == 1
    assert TimeMap(8000).original_seconds(1) == Fraction(1, 16000)


def test_a_chunk_map_adds_its_offset() -> None:
    whole = TimeMap(44100)
    chunk = whole.child(32000)
    assert chunk.offset == 32000
    assert chunk.to_original(0) == whole.to_original(32000)
    assert chunk.to_derivative(whole.to_original(32010)) == 10
    inner = chunk.child(640)
    assert inner.offset == 32640
    assert inner.to_original(5) == whole.to_original(32645)
    assert (inner.original_rate_hz, inner.derivative_rate_hz) == (44100, 16000)


def test_the_map_is_frozen() -> None:
    mapping = TimeMap(44100)
    with pytest.raises((AttributeError, TypeError)):
        mapping.offset = 3  # type: ignore[misc]


def test_the_record_is_integers_only_and_round_trips() -> None:
    record = TimeMap(11025, offset=640).as_record(48011)
    assert record == {
        "version": TIME_MAP_VERSION,
        "original_timeline": ORIGINAL_TIMELINE,
        "original_sample_rate_hz": 11025,
        "derivative_sample_rate_hz": 16000,
        "derivative_offset_samples": 640,
        "derivative_sample_count": 48011,
        "shared_grid": [640, 441],
    }
    assert TIME_MAP_VERSION == "time-map/1"
    assert ORIGINAL_TIMELINE == "decoded-primary-audio/1"
    for value in record.values():
        assert not isinstance(value, float)
    assert TimeMap.from_record(record) == TimeMap(11025, offset=640)


@pytest.mark.parametrize("rate", [True, False, 44100.0, 0, -1, "44100", None])
def test_a_bad_original_rate_is_refused(rate) -> None:
    with pytest.raises(ValueError):
        TimeMap(rate)  # type: ignore[arg-type]
    assert TimeMap(44100).original_rate_hz == 44100  # allow-control


@pytest.mark.parametrize("rate", [True, 16000.0, 0, -16000])
def test_a_bad_derivative_rate_is_refused(rate) -> None:
    with pytest.raises(ValueError):
        TimeMap(44100, rate)  # type: ignore[arg-type]
    assert TimeMap(44100, 8000).derivative_rate_hz == 8000  # allow-control


@pytest.mark.parametrize("offset", [True, -1, 1.0, "0"])
def test_a_bad_offset_is_refused(offset) -> None:
    with pytest.raises(ValueError):
        TimeMap(44100, offset=offset)  # type: ignore[arg-type]
    assert TimeMap(44100, offset=0).offset == 0  # allow-control


def test_floats_and_bools_are_refused_as_positions() -> None:
    mapping = TimeMap(44100)
    for value in (1.5, True):
        with pytest.raises(ValueError):
            mapping.to_original(value)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            mapping.to_derivative(value)  # type: ignore[arg-type]


def test_a_bad_record_count_is_refused() -> None:
    with pytest.raises(ValueError):
        TimeMap(44100).as_record(-1)
    with pytest.raises(ValueError):
        TimeMap(44100).as_record(True)  # type: ignore[arg-type]


def _valid() -> dict:
    return TimeMap(44100).as_record(48000)


@pytest.mark.parametrize(
    "change",
    [
        {"version": "time-map/2"},
        {"original_timeline": "container-time/1"},
        {"original_sample_rate_hz": 44100.0},
        {"original_sample_rate_hz": True},
        {"original_sample_rate_hz": 0},
        {"derivative_sample_rate_hz": -16000},
        {"derivative_offset_samples": -1},
        {"derivative_offset_samples": 0.0},
        {"derivative_sample_count": -5},
        {"derivative_sample_count": "48000"},
        {"shared_grid": [160, 440]},
        {"shared_grid": [160.0, 441]},
        {"shared_grid": [160]},
        {"shared_grid": (160, 441)},
    ],
)
def test_a_wrong_record_is_refused(change: dict) -> None:
    with pytest.raises(ValueError):
        TimeMap.from_record({**_valid(), **change})
    assert TimeMap.from_record(_valid()) == TimeMap(44100)  # allow-control


@pytest.mark.parametrize("key", list(_valid()))
def test_a_record_with_a_missing_key_is_refused(key: str) -> None:
    record = _valid()
    del record[key]
    with pytest.raises(ValueError):
        TimeMap.from_record(record)


def test_a_record_with_an_extra_key_or_not_a_dict_is_refused() -> None:
    with pytest.raises(ValueError):
        TimeMap.from_record({**_valid(), "start_seconds": 0.0})
    with pytest.raises(ValueError):
        TimeMap.from_record([])  # type: ignore[arg-type]
