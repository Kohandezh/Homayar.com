"""Correlation ids (WP20-T01, HOM-113).

One id names one unit of work: an HTTP request (the validated ``X-Request-ID``
or a fresh UUID), a processing attempt (``job:<job id>:<delivery>``, the first
eight characters of the broker delivery id, so a fenced attempt and its
replacement differ while ``job:<job id>`` stays a searchable prefix) or one run
of a periodic sweep (``sweep:<task>:<run>``).  The id lives in a context variable,
so it follows the work into FastAPI's threadpool and into Celery task code
without being passed around.  ``record_audit_event`` stores it as the audit
row's ``request_id`` when the caller gives none, and every JSON log line
carries it as ``correlation_id``.

A correlation id is an opaque routing value, never a secret or a tenant id
supplied by the client: a client-supplied ``X-Request-ID`` is accepted only
when it matches ``CORRELATION_ID_PATTERN``.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# Same bound as the audit column (String(80)) and the proxy's request id.
CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,80}$")
MAX_CORRELATION_ID_LENGTH = 80

_current: ContextVar[str | None] = ContextVar("homayar_correlation_id", default=None)


def current_correlation_id() -> str | None:
    return _current.get()


def request_correlation_id(supplied: str | None) -> str:
    """The client's ``X-Request-ID`` when it is well formed, else a fresh UUID."""
    if supplied and CORRELATION_ID_PATTERN.fullmatch(supplied):
        return supplied
    return str(uuid.uuid4())


def job_correlation_id(job_id: str, delivery: str | None = None) -> str:
    value = f"job:{job_id}:{delivery[:8]}" if delivery else f"job:{job_id}"
    return value[:MAX_CORRELATION_ID_LENGTH]


def sweep_correlation_id(task: str) -> str:
    return f"sweep:{task}:{uuid.uuid4().hex[:12]}"[:MAX_CORRELATION_ID_LENGTH]


@contextmanager
def correlation_scope(correlation_id: str) -> Iterator[str]:
    """Bind ``correlation_id`` for the enclosed work, restoring the outer one after."""
    token = _current.set(correlation_id[:MAX_CORRELATION_ID_LENGTH])
    try:
        yield correlation_id
    finally:
        _current.reset(token)


class CorrelationLogFilter(logging.Filter):
    """Adds ``correlation_id`` (or ``None``) to every record passing the handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _current.get()
        return True
