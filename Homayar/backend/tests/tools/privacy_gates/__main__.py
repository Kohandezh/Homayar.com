"""Command line entry: ``python -m backend.tests.tools.privacy_gates`` (HOM-321)."""

from __future__ import annotations

import sys

from backend.tests.tools.privacy_gates.runner import main

if __name__ == "__main__":
    sys.exit(main())
