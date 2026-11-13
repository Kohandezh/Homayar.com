"""Process-wide cache of loaded ASR models (HOM-41, ADR 0067).

Every ``LocalWhisperProvider`` in the same process shares one module-level
store, so a worker process loads the faster-whisper model once and every job
reuses it. The cache holds the model itself and the batched inference
pipeline built on it (under the model key plus ``("batched",)``).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WarmModelStats:
    loads: int  # successful loader calls
    hits: int  # calls served from the store
    failures: int  # loader calls that raised
    keys: int  # stored models


class _WarmModelStore:
    """Thread-safe store: one lock per key, so a load of one key never blocks
    a load of another. A failed loader stores nothing, so the next call for
    that key tries again."""

    def __init__(self) -> None:
        self._models: dict[Hashable, Any] = {}
        self._locks: dict[Hashable, threading.Lock] = {}
        self._guard = threading.Lock()
        self._loads = 0
        self._hits = 0
        self._failures = 0

    def get_or_load[T](self, key: Hashable, loader: Callable[[], T]) -> T:
        with self._guard:
            if key in self._models:
                self._hits += 1
                return self._models[key]
            key_lock = self._locks.setdefault(key, threading.Lock())
        with key_lock:
            with self._guard:
                if key in self._models:
                    self._hits += 1
                    return self._models[key]
            try:
                model = loader()
            except BaseException:
                with self._guard:
                    self._failures += 1
                raise
            with self._guard:
                self._loads += 1
                self._models[key] = model
                self._locks.pop(key, None)
        return model

    def stats(self) -> WarmModelStats:
        with self._guard:
            return WarmModelStats(
                loads=self._loads,
                hits=self._hits,
                failures=self._failures,
                keys=len(self._models),
            )

    def clear(self) -> None:
        with self._guard:
            self._models.clear()
            self._locks.clear()
            self._loads = 0
            self._hits = 0
            self._failures = 0


_STORE = _WarmModelStore()


def get_or_load[T](key: Hashable, loader: Callable[[], T]) -> T:
    """The stored object for ``key``, or what ``loader()`` returned."""
    return _STORE.get_or_load(key, loader)


def stats() -> WarmModelStats:
    return _STORE.stats()


def clear() -> None:
    """Drop every stored model and reset all counters (tests use this)."""
    _STORE.clear()


def warm_configured_asr(settings: Any) -> bool:
    """Load the configured ASR model into the cache before any job runs.

    Returns ``False`` without loading when warm-on-start is off or the
    provider is not local Whisper. A failed load logs one ``asr_warm_failed``
    event with the error code or exception class only, and returns ``False``.
    """
    if not settings.asr_warm_on_start:
        return False
    # Imported here: the factory pulls in every provider module, and a
    # circular import would otherwise run at module load time.
    from backend.providers.factory import _provider_name, create_asr_provider

    if _provider_name(settings.asr_provider) != "local_whisper":
        return False
    try:
        provider = create_asr_provider(settings)
        provider._get_model()
    except Exception as exc:
        logger.warning("asr_warm_failed error=%s", getattr(exc, "code", None) or type(exc).__name__)
        return False
    return True
