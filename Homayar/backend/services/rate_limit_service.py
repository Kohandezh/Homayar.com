from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import redis

from backend.config import Settings, get_settings


class RateLimitExceeded(ValueError):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = max(1, retry_after_seconds)
        super().__init__("Too many requests")


class RateLimitUnavailable(RuntimeError):
    pass


@dataclass
class _MemoryCounter:
    count: int
    expires_at: float


class RateLimiter:
    """Distributed fixed-window limiter with an isolated test/development fallback."""

    _memory: dict[str, _MemoryCounter] = {}
    _lock = threading.Lock()

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def hit(self, key: str, *, limit: int, window_seconds: int) -> None:
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("Rate-limit values must be positive")
        redis_key = f"homayar:rate:{key}"
        try:
            client = redis.Redis.from_url(
                self.settings.redis_url,
                socket_connect_timeout=0.3,
                socket_timeout=0.3,
                decode_responses=True,
            )
            pipe = client.pipeline(transaction=True)
            pipe.incr(redis_key)
            pipe.ttl(redis_key)
            count, ttl = pipe.execute()
            if count == 1 or ttl < 0:
                client.expire(redis_key, window_seconds)
                ttl = window_seconds
            if int(count) > limit:
                raise RateLimitExceeded(int(ttl))
            return
        except RateLimitExceeded:
            raise
        except Exception as exc:
            if self.settings.environment not in {"test", "development"}:
                if self.settings.rate_limit_fail_open:
                    return
                raise RateLimitUnavailable("Distributed rate limiter is unavailable") from exc

        now = time.monotonic()
        with self._lock:
            counter = self._memory.get(redis_key)
            if counter is None or counter.expires_at <= now:
                counter = _MemoryCounter(count=0, expires_at=now + window_seconds)
            counter.count += 1
            self._memory[redis_key] = counter
            if counter.count > limit:
                raise RateLimitExceeded(round(counter.expires_at - now))

    @classmethod
    def reset_memory(cls) -> None:
        with cls._lock:
            cls._memory.clear()

