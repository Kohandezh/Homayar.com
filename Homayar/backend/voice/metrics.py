"""In-process voice runtime metrics (no external dependencies)."""

from __future__ import annotations

import threading
from collections import Counter
from typing import Any


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile; 0.0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-pct * len(ordered) // 100))))
    return ordered[rank - 1]


class VoiceMetrics:
    _MAX_SAMPLES = 10_000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self._admitted = 0
            self._rejections: Counter[str] = Counter()
            self._ended: Counter[str] = Counter()
            self._active = 0
            self._latencies_ms: list[float] = []
            self._latency_count = 0
            self._latency_sum = 0.0

    def observe_admission(self, latency_ms: float, *, admitted: bool, reason: str | None) -> None:
        with self._lock:
            self._latency_count += 1
            self._latency_sum += latency_ms
            self._latencies_ms.append(latency_ms)
            if len(self._latencies_ms) > self._MAX_SAMPLES:
                del self._latencies_ms[: len(self._latencies_ms) - self._MAX_SAMPLES]
            if admitted:
                self._admitted += 1
                self._active += 1
            elif reason:
                self._rejections[reason] += 1

    def observe_release(self, reason: str) -> None:
        with self._lock:
            self._ended[reason] += 1
            self._active = max(0, self._active - 1)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            samples = list(self._latencies_ms)
            return {
                "admitted_total": self._admitted,
                "active_calls": self._active,
                "rejections_by_reason": dict(self._rejections),
                "releases_by_reason": dict(self._ended),
                "admission_latency_ms": {
                    "count": self._latency_count,
                    "mean": (self._latency_sum / self._latency_count)
                    if self._latency_count
                    else 0.0,
                    "p50": percentile(samples, 50),
                    "p95": percentile(samples, 95),
                    "p99": percentile(samples, 99),
                    "max": max(samples) if samples else 0.0,
                },
            }


metrics = VoiceMetrics()


def snapshot() -> dict[str, Any]:
    return metrics.snapshot()
