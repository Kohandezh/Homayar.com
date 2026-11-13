"""``python -m backend.licensing status-line``: the operator one-liner (HOM-109).

Exit 0 with one line on stdout when it can evaluate the licence; no database,
no network. When it cannot (for example the settings do not load), it writes a
short error to stderr, prints nothing on stdout and exits 1, so
``homayar-onprem.sh status`` prints ``license: unreadable`` (Amendment 3). It
calls ``evaluate``, not ``current_license_status``: the state log line would go
to stdout too. Loading the settings can log warnings (HOM-129, for example
``PRIVILEGED_MFA_KEYRING_MISSING``) through the JSON handler on stdout, so log
output goes to stderr while the line is computed. ``main`` is importable so the
test suite runs it in-process.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from backend.licensing.offline_license import evaluate, status_line


@contextmanager
def _logs_on_stderr() -> Iterator[None]:
    """Every log handler that writes to stdout writes to stderr meanwhile, so stdout
    holds only the licence line. ``setup_logging`` points the root handler at the
    real stdout (``sys.__stdout__``); any other logger's handler is moved the same way.
    The same pattern as ``backend.operator_access.__main__`` (HOM-129)."""
    loggers = [logging.getLogger()] + [
        item
        for item in logging.Logger.manager.loggerDict.values()
        if isinstance(item, logging.Logger)
    ]
    moved: list[tuple[logging.StreamHandler, object]] = []
    for item in loggers:
        for handler in item.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream in (
                sys.stdout,
                sys.__stdout__,
            ):
                moved.append((handler, handler.setStream(sys.__stderr__)))
    try:
        yield
    finally:
        for handler, stream in moved:
            handler.setStream(stream)


def main(argv: list[str]) -> int:
    if argv != ["status-line"]:
        print("usage: python -m backend.licensing status-line", file=sys.stderr)
        return 2
    try:
        # Imported before the block, so a handler an import sets up is moved too.
        from backend.config import get_settings

        with _logs_on_stderr():
            line = status_line(evaluate(get_settings()))
    except Exception as exc:
        print(f"license status-line: cannot evaluate ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
