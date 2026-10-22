"""The exception raised for an API error answer."""

from __future__ import annotations

import math
from typing import Any

import httpx


class HomayarAPIError(Exception):
    """A non-2xx answer that was not retried, or still failed after the last retry.

    Every Homayar error body is ``{"error": {"code", "message", "request_id",
    "details"?}}``. Branch on ``code``; ``message`` is for people.
    """

    def __init__(
        self,
        status_code: int,
        code: str | None,
        message: str,
        *,
        request_id: str | None = None,
        details: Any = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"{status_code} {code or 'error'}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id
        self.details = details
        self.retry_after = retry_after

    @classmethod
    def from_response(cls, response: httpx.Response) -> HomayarAPIError:
        try:
            error = response.json()["error"]
        except (ValueError, KeyError, TypeError):
            # Not a Homayar body, for example an HTML page from a proxy.
            error = {}
        if not isinstance(error, dict):
            error = {}
        return cls(
            response.status_code,
            error.get("code"),
            error.get("message") or response.reason_phrase or "Request failed",
            request_id=error.get("request_id") or response.headers.get("X-Request-ID"),
            details=error.get("details"),
            retry_after=parse_retry_after(response.headers.get("Retry-After")),
        )


def parse_retry_after(value: str | None) -> float | None:
    """``Retry-After`` in seconds. Homayar sends whole seconds, never an HTTP date.

    Anything that is not a finite number of seconds (``inf``, ``nan``, a
    negative number, a date) gives ``None``, so it never reaches ``sleep()``.
    """
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None
