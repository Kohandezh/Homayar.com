"""Route templates instead of raw paths in uvicorn's access log (log privacy).

uvicorn writes one access line per request (logger ``uvicorn.access``) with the
raw path and query string. Paths carry secrets: share links and invitations put
their token in the path (``/api/v1/shares/<token>``), and a query string can
hold anything a client sent. ``AccessLogFilter`` swaps the path for the route
template the request matched (the same label ``/metrics`` uses), drops the query
string, and leaves the client address, method, HTTP version and status alone.
A path that matches no route is written as ``unmatched``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from starlette.routing import BaseRoute, Match

ACCESS_LOGGER = "uvicorn.access"
UNMATCHED = "unmatched"


def route_template_for(routes: Sequence[BaseRoute], method: str, raw_path: str) -> str:
    """The template of the route ``method raw_path`` reaches, or ``unmatched``."""
    path = raw_path.split("?", 1)[0]
    scope = {"type": "http", "path": path, "root_path": "", "method": method.upper()}
    partial: BaseRoute | None = None
    for route in routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return _template(route)
        if match == Match.PARTIAL and partial is None:
            partial = route  # the path matches, the method does not (a 405)
    return _template(partial) if partial is not None else UNMATCHED


def _template(route: BaseRoute) -> str:
    template = getattr(route, "path", None)
    return template if isinstance(template, str) and template else UNMATCHED


class AccessLogFilter(logging.Filter):
    """Rewrite uvicorn's access record ``(client, method, path, version, status)``."""

    def __init__(self, routes: Sequence[BaseRoute]) -> None:
        super().__init__()
        self._routes = routes

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            return self._filter(record)
        except Exception:  # noqa: BLE001 - a filter must never raise into the server
            record.msg, record.args = "[unfilterable record]", ()
            record.exc_info = record.exc_text = None
            return True

    def _filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            client, method, path, version, status = args
            template = route_template_for(self._routes, str(method), str(path))
            record.args = (client, method, template, version, status)
            return True
        # Not the record shape uvicorn writes: keep nothing that could be a path.
        from backend.observability.scrub import diagnostic_text

        record.msg = diagnostic_text(record.getMessage())
        record.args = ()
        return True


def install_access_log_filter(routes: Sequence[BaseRoute]) -> None:
    """Attach the filter for these routes, replacing one an earlier app attached."""
    access_logger = logging.getLogger(ACCESS_LOGGER)
    for existing in [f for f in access_logger.filters if isinstance(f, AccessLogFilter)]:
        access_logger.removeFilter(existing)
    access_logger.addFilter(AccessLogFilter(routes))
