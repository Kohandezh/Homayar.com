"""When to retry a request, and how long to wait first."""

from __future__ import annotations

import random
from dataclasses import dataclass

# 502/503/504 mean a proxy or the server could not answer now. A retry is safe
# because uploads carry an Idempotency-Key and the other calls only read.
RETRYABLE_STATUSES = frozenset({502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    """How the client retries.

    ``max_attempts`` counts the first attempt, so 4 means one try and up to
    three retries. Without ``Retry-After`` the wait before retry ``n`` is a
    random value between half and all of
    ``min(max_delay, base_delay * 2 ** (n - 1))``. With ``Retry-After`` the
    wait is exactly the server's value, even above ``max_delay``. When the
    server asks for more than ``max_retry_after`` seconds (default five
    minutes, for example the one-hour per-key rate-limit window), the client
    does not wait: it raises at once with ``retry_after`` set, so the caller
    decides. Pass ``max_retry_after=None`` to wait for any ``Retry-After``.
    """

    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 30.0
    max_retry_after: float | None = 300.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("delays must not be negative")

    def backoff(self, retry_number: int) -> float:
        """Exponential backoff with jitter, so many clients do not retry in step."""
        ceiling = min(self.max_delay, self.base_delay * 2 ** (retry_number - 1))
        return random.uniform(ceiling / 2, ceiling)  # noqa: S311 (timing jitter, not a secret)

    def should_retry(self, status_code: int, retry_after: float | None) -> bool:
        """Whether an error answer is worth another attempt.

        A 429 with ``Retry-After`` is a rate limit that ends. A 429 without it
        is a quota, and waiting does not help. Other 4xx and 500 are final.
        """
        if retry_after is not None and self.max_retry_after is not None:
            if retry_after > self.max_retry_after:
                return False
        if status_code == 429:
            return retry_after is not None
        return status_code in RETRYABLE_STATUSES
