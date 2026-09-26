"""Broker publication primitive (WP06-T01 queue contract).

Application code does not call this directly any more: the transactional
outbox (``backend.jobs.outbox``) owns *when* a job is published and records
the result. This module only knows *how* to publish to the declared queue.
"""

from __future__ import annotations

from backend.celery_app import PROCESSING_QUEUE
from backend.config import Settings
from backend.jobs.tasks import process_meeting_job
from backend.services.logging_service import logger


def publish_processing_job(
    job_id: str, settings: Settings, *, workspace_id: str | None = None
) -> str:
    """Publish one message; raise on broker failure; return the message id.

    ``workspace_id`` travels only as an assertion the consumer re-checks
    against the persisted job (``outbox.verify_job_payload``); it never
    confers tenant context.
    """
    del settings  # the Celery app carries the broker configuration
    options: dict = {"args": [job_id], "queue": PROCESSING_QUEUE}
    if workspace_id:
        options["kwargs"] = {"workspace_id": workspace_id}
    result = process_meeting_job.apply_async(**options)
    return str(getattr(result, "id", "") or "")


def enqueue_processing_job(job_id: str, settings: Settings) -> bool:
    """Best-effort publish without outbox bookkeeping (smoke tests, tooling)."""
    try:
        publish_processing_job(job_id, settings)
    except Exception as exc:
        logger.error(
            "JOB_ENQUEUE_FAILED job_id=%s error_type=%s",
            job_id,
            type(exc).__name__,
        )
        return False
    return True
