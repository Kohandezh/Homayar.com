"""Per-channel signal activity on a 16 kHz derivative (HOM-38, ADR 0042).

``signal-activity/1`` is a **signal-level heuristic**, not speech detection:
"active" means "this channel carries signal above its own noise floor".  It
never claims that someone speaks and never sets or implies a role (roles come
only from the HOM-37 trusted mapping).

Each channel is measured on its own, in 20 ms frames (320 samples at 16 kHz,
no overlap; a trailing partial frame is not measured):

* frame AC energy ``E = sum(x^2) - (sum x)^2 / 320`` from exact integer sums,
  so a constant (DC) channel has ``E = 0``; ``E = 0`` has no level and is never
  active; otherwise the level is ``10 * log10(E / (320 * 32768^2))`` dBFS;
* the noise floor is the ``NOISE_PERCENTILE`` percentile of the frame levels
  (frames without a level sort lowest; unknown when that frame has none);
* a run starts on a frame at or above ``on_dbfs = max(FLOOR_DBFS, floor +
  MARGIN_DB)`` and continues while the level stays at or above ``off_dbfs =
  on_dbfs - HYSTERESIS_DB``;
* gaps of fewer than ``MIN_GAP_FRAMES`` frames are merged, then runs of fewer
  than ``MIN_ACTIVITY_FRAMES`` frames are dropped.

Intervals are half-open ``[start, end)`` in derivative samples, multiples of
320, not padded (:func:`padded` is for consumers).  Every threshold is a
constant of the version: a change is a new version.

Memory stays bounded: the PCM is read in blocks and each channel keeps one
float level per frame (8 bytes per 20 ms).
"""

from __future__ import annotations

import math
import sys
from array import array
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

ACTIVITY_VERSION: Final = "signal-activity/1"
ACTIVITY_KIND: Final = "signal_level_heuristic"
ACTIVITY_SAMPLE_RATE_HZ: Final = 16_000
FRAME_SAMPLES: Final = 320
FLOOR_DBFS: Final = -50.0
MARGIN_DB: Final = 12.0
HYSTERESIS_DB: Final = 6.0
NOISE_PERCENTILE: Final = 10
MIN_ACTIVITY_FRAMES: Final = 5
MIN_GAP_FRAMES: Final = 10
RECOMMENDED_PADDING_SAMPLES: Final = 1_600
MAX_CHANNELS: Final = 8

INSPECTION_COMPLETE: Final = "complete"
INSPECTION_UNAVAILABLE: Final = "unavailable"
INSPECTION_MISSING: Final = "missing"

_SAMPLE_BYTES: Final = 2
# 320 * E is an integer; the level divides it by 320 * 320 * full scale squared.
_LEVEL_DENOMINATOR: Final = FRAME_SAMPLES * FRAME_SAMPLES * 32_768**2
_NO_LEVEL: Final = -math.inf


@dataclass(frozen=True, slots=True)
class ChannelActivity:
    index: int
    noise_floor_dbfs: float | None  # None: the percentile frame has no level
    on_dbfs: float
    off_dbfs: float
    active_frames: int
    intervals: tuple[tuple[int, int], ...]  # [start, end) in derivative samples


@dataclass(frozen=True, slots=True)
class ActivityResult:
    measured_samples: int  # 320 * whole frames
    channels: tuple[ChannelActivity, ...]


class ActivityMeter:
    """Streams interleaved signed 16-bit little-endian PCM at 16 kHz.

    ``feed`` takes bytes of any length; a partial frame (or sample) is carried
    to the next call, so any split of the same bytes gives the same result.
    """

    def __init__(self, channel_count: int) -> None:
        if (
            isinstance(channel_count, bool)
            or not isinstance(channel_count, int)
            or not 1 <= channel_count <= MAX_CHANNELS
        ):
            raise ValueError(f"channel_count must be between 1 and {MAX_CHANNELS}")
        self._channel_count = channel_count
        self._frame_bytes = FRAME_SAMPLES * channel_count * _SAMPLE_BYTES
        self._pending = bytearray()
        self._levels = [array("d") for _ in range(channel_count)]

    def feed(self, pcm: bytes) -> None:
        self._pending += pcm
        usable = len(self._pending) - len(self._pending) % self._frame_bytes
        if usable == 0:
            return
        samples = array("h")
        samples.frombytes(self._pending[:usable])
        del self._pending[:usable]
        if sys.byteorder == "big":
            samples.byteswap()
        length = usable // self._frame_bytes * FRAME_SAMPLES
        for index, levels in enumerate(self._levels):
            channel = samples[index :: self._channel_count]
            for start in range(0, length, FRAME_SAMPLES):
                frame = channel[start : start + FRAME_SAMPLES]
                total = sum(frame)
                scaled = FRAME_SAMPLES * math.sumprod(frame, frame) - total * total
                levels.append(10 * math.log10(scaled / _LEVEL_DENOMINATOR) if scaled else _NO_LEVEL)

    def finish(self) -> ActivityResult:
        frames = len(self._levels[0])
        return ActivityResult(
            measured_samples=frames * FRAME_SAMPLES,
            channels=tuple(_channel(index, levels) for index, levels in enumerate(self._levels)),
        )


def measure_activity(pcm: bytes, channel_count: int) -> ActivityResult:
    meter = ActivityMeter(channel_count)
    meter.feed(pcm)
    return meter.finish()


def _channel(index: int, levels: array) -> ChannelActivity:
    floor = _noise_floor(levels)
    on_dbfs = FLOOR_DBFS if floor is None else max(FLOOR_DBFS, floor + MARGIN_DB)
    off_dbfs = on_dbfs - HYSTERESIS_DB
    runs = [
        run
        for run in _merged(_runs(levels, on_dbfs, off_dbfs))
        if run[1] - run[0] >= MIN_ACTIVITY_FRAMES
    ]
    return ChannelActivity(
        index=index,
        noise_floor_dbfs=floor,
        on_dbfs=on_dbfs,
        off_dbfs=off_dbfs,
        active_frames=sum(end - start for start, end in runs),
        intervals=tuple((start * FRAME_SAMPLES, end * FRAME_SAMPLES) for start, end in runs),
    )


def _noise_floor(levels: array) -> float | None:
    if not levels:
        return None
    value = sorted(levels)[(len(levels) - 1) * NOISE_PERCENTILE // 100]
    return None if value == _NO_LEVEL else value


def _runs(levels: array, on_dbfs: float, off_dbfs: float) -> list[tuple[int, int]]:
    """Frame runs ``[j0, j1)`` by the hysteresis rule."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for frame, level in enumerate(levels):
        if start is None:
            if level >= on_dbfs:
                start = frame
        elif level < off_dbfs:
            runs.append((start, frame))
            start = None
    if start is not None:
        runs.append((start, len(levels)))
    return runs


def _merged(runs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in runs:
        if merged and start - merged[-1][1] < MIN_GAP_FRAMES:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def padded(
    intervals: Iterable[tuple[int, int]], pad_samples: int, sample_count: int
) -> list[tuple[int, int]]:
    """Widen each interval by ``pad_samples`` on both sides, clamp to
    ``[0, sample_count)`` and merge intervals that then overlap or touch."""
    for name, value in (("pad_samples", pad_samples), ("sample_count", sample_count)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be an int of at least 0")
    result: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        start, end = max(0, start - pad_samples), min(sample_count, end + pad_samples)
        if start >= end:
            continue
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


# --- the record -------------------------------------------------------------------------


def inspection_status(inspection: Any) -> str:
    """``complete``, ``unavailable`` or ``missing`` for the ORIGINAL's HOM-37 record."""
    if inspection is None:
        return INSPECTION_MISSING
    if (
        isinstance(inspection, dict)
        and inspection.get("status") == INSPECTION_COMPLETE
        and isinstance(inspection.get("channels"), list)
    ):
        return INSPECTION_COMPLETE
    return INSPECTION_UNAVAILABLE


def _flags(inspection: Any, index: int) -> tuple[bool | None, bool | None, list[int] | None]:
    """The HOM-37 silent and duplicated flags of one channel; unknown stays None."""
    if inspection_status(inspection) != INSPECTION_COMPLETE:
        return None, None, None
    entry = next(
        (
            item
            for item in inspection["channels"]
            if isinstance(item, dict) and item.get("index") == index
        ),
        None,
    )
    if entry is None:
        return None, None, None
    silent = entry.get("silent") if isinstance(entry.get("silent"), bool) else None
    duplicated = entry.get("duplicated") if isinstance(entry.get("duplicated"), bool) else None
    if duplicated is None:
        return silent, None, None
    others: set[int] = set()
    pairs = inspection.get("duplicate_pairs")
    for pair in pairs if isinstance(pairs, list) else []:
        if isinstance(pair, list) and len(pair) == 2 and index in pair:
            other = pair[1] if pair[0] == index else pair[0]
            if isinstance(other, int) and not isinstance(other, bool) and other != index:
                others.add(other)
    return silent, duplicated, sorted(others)


def activity_record(result: ActivityResult, inspection: dict[str, Any] | None) -> dict[str, Any]:
    """The ``activity`` object of the ``audio-derivative/1`` record.

    The HOM-37 flags are copied per channel and never remove an interval:
    HOM-37 measured only the first seconds of the file (SPEC D3).
    """
    channels = []
    for channel in result.channels:
        silent, duplicated, duplicate_of = _flags(inspection, channel.index)
        channels.append(
            {
                "index": channel.index,
                "source_channel": channel.index,
                "noise_floor_dbfs": (
                    round(channel.noise_floor_dbfs, 2)
                    if channel.noise_floor_dbfs is not None
                    else None
                ),
                "on_dbfs": channel.on_dbfs,
                "off_dbfs": channel.off_dbfs,
                "active_frames": channel.active_frames,
                "intervals": [[start, end] for start, end in channel.intervals],
                "inspection_silent": silent,
                "inspection_duplicated": duplicated,
                "duplicate_of": duplicate_of,
            }
        )
    return {
        "version": ACTIVITY_VERSION,
        "kind": ACTIVITY_KIND,
        "frame_samples": FRAME_SAMPLES,
        "measured_samples": result.measured_samples,
        "params": {
            "floor_dbfs": FLOOR_DBFS,
            "margin_db": MARGIN_DB,
            "hysteresis_db": HYSTERESIS_DB,
            "noise_percentile": NOISE_PERCENTILE,
            "min_activity_frames": MIN_ACTIVITY_FRAMES,
            "min_gap_frames": MIN_GAP_FRAMES,
            "recommended_padding_samples": RECOMMENDED_PADDING_SAMPLES,
        },
        "channels": channels,
    }
