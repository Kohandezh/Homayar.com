"""Fault injection points for the backup/restore crash tests (test-only).

``HOMAYAR_BACKUP_FAULT=<point>`` raises ``FaultInjected`` at that point and
``HOMAYAR_BACKUP_FAULT=<point>:pause`` touches ``HOMAYAR_BACKUP_FAULT_MARKER``
and sleeps so a test can kill the process there.  Honoured only when
``ENVIRONMENT`` is ``test`` or ``development``; inert everywhere else.
"""

from __future__ import annotations

import os
import time
from pathlib import Path


class FaultInjected(RuntimeError):
    pass


def point(name: str) -> None:
    spec = os.environ.get("HOMAYAR_BACKUP_FAULT", "")
    if not spec:
        return
    target, _, action = spec.partition(":")
    if target != name:
        return
    if os.environ.get("ENVIRONMENT", "").strip().lower() not in {"test", "development"}:
        return
    if action == "pause":
        marker = os.environ.get("HOMAYAR_BACKUP_FAULT_MARKER", "")
        if marker:
            Path(marker).write_text(f"{os.getpid()}\n", encoding="ascii")
        time.sleep(float(os.environ.get("HOMAYAR_BACKUP_FAULT_PAUSE_SECONDS", "300")))
        return
    raise FaultInjected(f"injected fault at {name}")
