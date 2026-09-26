"""Disposable worker/beat process for the real-transport drills (WP06-T07).

Started as ``python -m backend.tests._transport_worker worker|beat ...`` by
``backend.tests._transport``.  It runs the production Celery app
(``backend.celery_app``) with the deployed queue, acknowledgement, prefetch
and time-limit settings; the test-only adjustments are explicit and small:

* the provider bundle is replaced by deterministic mocks whose ASR stage is
  held at a gate file (``HOMAYAR_TEST_ASR_GATE``): the stage blocks until the
  file exists, which is how a drill keeps an attempt in flight while it kills
  the process or lets the lease lapse;
* ``HOMAYAR_TEST_VISIBILITY_TIMEOUT`` sets the Redis ``visibility_timeout``
  transport option so an unacknowledged delivery is restored within the test
  deadline instead of after kombu's default hour;
* the pool is ``prefork`` with concurrency 1 so a drill can kill the one pool
  child and observe the parent's reaction.

Never imported by the application.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

GATE_ENV = "HOMAYAR_TEST_ASR_GATE"
GATE_TIMEOUT_ENV = "HOMAYAR_TEST_ASR_GATE_SECONDS"
VISIBILITY_ENV = "HOMAYAR_TEST_VISIBILITY_TIMEOUT"


def _gated_bundle(_settings):
    from backend.providers.base import DiarizationProvider
    from backend.providers.factory import ProviderBundle
    from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
    from backend.providers.schemas import (
        DiarizationResult,
        DiarizationSegment,
        DiarizationStatus,
    )

    gate = os.environ.get(GATE_ENV, "")
    # Longer than kombu's ~100 s restore cadence: a drill that waits for a
    # visibility-timeout redelivery must still be holding the attempt then.
    hold = float(os.environ.get(GATE_TIMEOUT_ENV, "240"))

    class GatedMockASRProvider(MockASRProvider):
        async def transcribe(self, request):
            if gate:
                deadline = time.monotonic() + hold
                while not Path(gate).exists():
                    if time.monotonic() > deadline:
                        raise RuntimeError("the ASR gate was never opened")
                    await asyncio.sleep(0.05)
            return await super().transcribe(request)

    class SingleSpeakerDiarizationProvider(DiarizationProvider):
        """The fully successful path (``MockDiarizationProvider`` is honestly
        unavailable, which would make every drill outcome ``partial``)."""

        async def diarize(self, request):
            return DiarizationResult(
                provider="single-speaker-test",
                status=DiarizationStatus.AVAILABLE,
                segments=[
                    DiarizationSegment(speaker_label="speaker_1", start_seconds=0, end_seconds=1e6)
                ],
                is_mock=True,
            )

    return ProviderBundle(
        asr=GatedMockASRProvider(allow_mock=True, transcript="transport drill transcript"),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=MockIntelligenceProvider(allow_mock=True),
    )


def _option(argv: list[str], flag: str) -> str | None:
    if flag in argv:
        return argv[argv.index(flag) + 1]
    return None


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in {"worker", "beat"}:
        print(f"usage: python -m {__name__} worker|beat [options]", file=sys.stderr)
        return 2
    from backend.celery_app import PROCESSING_QUEUE, celery_app

    visibility = os.environ.get(VISIBILITY_ENV, "").strip()
    if visibility:
        options = dict(celery_app.conf.broker_transport_options or {})
        options["visibility_timeout"] = int(visibility)
        celery_app.conf.broker_transport_options = options

    if argv[0] == "worker":
        from backend.jobs import processor

        # Patched in the parent before the pool forks, so every child inherits it.
        processor.create_provider_bundle = _gated_bundle
        celery_app.worker_main(
            [
                "worker",
                "--queues",
                PROCESSING_QUEUE,
                "--pool",
                "prefork",
                "--concurrency",
                "1",
                "--loglevel",
                "info",
                *argv[1:],
            ]
        )
        return 0

    celery_app.Beat(
        loglevel="info",
        schedule=_option(argv[1:], "--schedule"),
        max_interval=1,
    ).run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
