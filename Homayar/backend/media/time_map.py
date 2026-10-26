"""The exact map between derivative samples and original time (HOM-38, ADR 0042).

*Original time* is the decoded timeline of the ORIGINAL's primary audio
stream: sample index ``n`` as ffmpeg decodes ``-map 0:a:0`` with its default
decoder settings, counted from the first decoded sample.  A container start
offset is ignored; an encoder delay the file signals is removed by the
decoder, one it does not signal stays in the timeline (it is never guessed).
The record names this definition ``decoded-primary-audio/1``.

Derivative sample ``k`` (at ``derivative_rate_hz``, plus a chunk ``offset``)
is the original sample position ``(k + offset) * R_o / R_d``.  Both directions
are ``Fraction`` multiplications by inverse ratios, so every round trip is
exact; on the shared grid (every ``R_d / g`` derivative samples, which are
``R_o / g`` original samples, ``g = gcd(R_o, R_d)``) both sides are whole
numbers.  The record holds integers only: a float is never the source of
truth.

A chunk ``[a, b)`` of a derivative maps through :meth:`TimeMap.child` with
offset ``a``; a chunk of a chunk adds the offsets.  The record shape is the
same for a whole derivative and for a chunk.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Final

TIME_MAP_VERSION: Final = "time-map/1"
ORIGINAL_TIMELINE: Final = "decoded-primary-audio/1"
DEFAULT_DERIVATIVE_RATE_HZ: Final = 16_000

_RECORD_KEYS: Final = frozenset(
    {
        "version",
        "original_timeline",
        "original_sample_rate_hz",
        "derivative_sample_rate_hz",
        "derivative_offset_samples",
        "derivative_sample_count",
        "shared_grid",
    }
)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _exact(value: object, name: str) -> int | Fraction:
    """An int or a Fraction; a float or a bool is refused (never a source of truth)."""
    if _is_int(value) or isinstance(value, Fraction):
        return value  # type: ignore[return-value]
    raise ValueError(f"{name} must be an int or a Fraction")


@dataclass(frozen=True, slots=True)
class TimeMap:
    original_rate_hz: int
    derivative_rate_hz: int = DEFAULT_DERIVATIVE_RATE_HZ
    offset: int = 0

    def __post_init__(self) -> None:
        for name in ("original_rate_hz", "derivative_rate_hz"):
            value = getattr(self, name)
            if not _is_int(value) or value <= 0:
                raise ValueError(f"{name} must be a positive int")
        if not _is_int(self.offset) or self.offset < 0:
            raise ValueError("offset must be an int of at least 0")

    def to_original(self, k: int | Fraction) -> Fraction:
        """Original sample position of derivative sample ``k``."""
        k = _exact(k, "k")
        return Fraction(k + self.offset) * self.original_rate_hz / self.derivative_rate_hz

    def to_derivative(self, n: int | Fraction) -> Fraction:
        """Derivative sample position of original sample position ``n``."""
        n = _exact(n, "n")
        return Fraction(n) * self.derivative_rate_hz / self.original_rate_hz - self.offset

    def original_seconds(self, k: int | Fraction) -> Fraction:
        """Original time of derivative sample ``k``, in seconds."""
        k = _exact(k, "k")
        return Fraction(k + self.offset, 1) / self.derivative_rate_hz

    def original_cover(self, start: int, end: int) -> tuple[int, int]:
        """The smallest whole range of original samples that holds ``[start, end)``."""
        return math.floor(self.to_original(start)), math.ceil(self.to_original(end))

    def shared_grid(self) -> tuple[int, int]:
        """``(derivative step, original step)``: the points that are whole on both sides."""
        divisor = math.gcd(self.original_rate_hz, self.derivative_rate_hz)
        return self.derivative_rate_hz // divisor, self.original_rate_hz // divisor

    def child(self, offset: int) -> TimeMap:
        """The map of a chunk that starts ``offset`` samples into this one."""
        if not _is_int(offset) or offset < 0:
            raise ValueError("offset must be an int of at least 0")
        return TimeMap(self.original_rate_hz, self.derivative_rate_hz, self.offset + offset)

    def as_record(self, sample_count: int) -> dict[str, Any]:
        if not _is_int(sample_count) or sample_count < 0:
            raise ValueError("sample_count must be an int of at least 0")
        return {
            "version": TIME_MAP_VERSION,
            "original_timeline": ORIGINAL_TIMELINE,
            "original_sample_rate_hz": self.original_rate_hz,
            "derivative_sample_rate_hz": self.derivative_rate_hz,
            "derivative_offset_samples": self.offset,
            "derivative_sample_count": sample_count,
            "shared_grid": list(self.shared_grid()),
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> TimeMap:
        """Rebuild a map from its record; anything but the exact shape is refused."""
        if not isinstance(record, dict) or set(record) != _RECORD_KEYS:
            raise ValueError("a time map record has exactly the time-map/1 keys")
        if record["version"] != TIME_MAP_VERSION:
            raise ValueError("unknown time map version")
        if record["original_timeline"] != ORIGINAL_TIMELINE:
            raise ValueError("unknown original timeline")
        count = record["derivative_sample_count"]
        if not _is_int(count) or count < 0:
            raise ValueError("derivative_sample_count must be an int of at least 0")
        mapping = cls(
            record["original_sample_rate_hz"],
            record["derivative_sample_rate_hz"],
            record["derivative_offset_samples"],
        )
        grid = record["shared_grid"]
        if (
            not isinstance(grid, list)
            or len(grid) != 2
            or not all(_is_int(step) for step in grid)
            or tuple(grid) != mapping.shared_grid()
        ):
            raise ValueError("shared_grid does not match the rates")
        return mapping
