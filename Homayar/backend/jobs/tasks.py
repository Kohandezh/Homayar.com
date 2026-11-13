from __future__ import annotations

import logging

from celery.exceptions import Reject

from backend.celery_app import (
    ARTIFACT_CLEANUP_TASK,
    COACHING_REMINDER_TASK,
    CRM_RECONCILIATION_TASK,
    DELIVER_WEBHOOK_TASK,
    DISPATCH_SWEEP_TASK,
    LEASE_RECOVERY_TASK,
    MEDIA_RECONCILIATION_TASK,
    PROCESS_MEETING_TASK,
    RECOMPUTE_ANALYSIS_TASK,
    RECOMPUTE_SWEEP_TASK,
    RETENTION_PURGE_TASK,
    USAGE_RECONCILIATION_TASK,
    VOICE_RECOVERY_TASK,
    WEBHOOK_SWEEP_TASK,
    celery_app,
)
from backend.config import get_settings
from backend.observability.correlation import correlation_scope, job_correlation_id
from backend.observability.heartbeats import run_with_heartbeat

logger = logging.getLogger(__name__)


def _asr_delivery_guard(task, job_id: str, workspace_id: str | None) -> dict | None:
    """Run ASR only on a worker whose readiness holds the job's queue (HOM-42).

    ``None``: run the job here. A dict: the job was published again to its
    queue (a message from before HOM-42). Raises ``Reject(requeue=True)``
    when this worker must not take an ASR delivery (a start-up race).
    """
    import backend.celery_app as celery_module
    from backend.jobs import dispatcher, worker_readiness

    settings = get_settings()
    delivery_queue = (task.request.delivery_info or {}).get("routing_key")
    readiness = celery_module.current_worker_readiness()
    if readiness is None:
        # No worker_init readiness in this process: assess once and keep it.
        readiness = worker_readiness.assess_worker_readiness(
            settings, pool="solo", concurrency=1, probes=worker_readiness.default_probes()
        )
        celery_module.remember_worker_readiness(readiness)
    decision = worker_readiness.asr_delivery_decision(readiness, delivery_queue, settings)
    if decision == "run":
        return None
    if decision == "reroute":
        target = worker_readiness.asr_queue_for(settings)
        try:
            dispatcher.publish_processing_job(job_id, settings, workspace_id=workspace_id)
        except Exception as exc:
            # Keep the message rather than lose the job; class name only.
            logger.error(
                "asr_job_reroute_failed from=%s to=%s error=%s",
                delivery_queue,
                target,
                type(exc).__name__,
            )
            raise Reject(requeue=True) from None
        logger.info("asr_job_rerouted from=%s to=%s", delivery_queue, target)
        return {"job_id": job_id, "status": "rerouted", "queue": target}
    logger.warning("asr_job_requeued queue=%s reason=%s", delivery_queue, readiness.reason)
    # The requeued message goes back at once. Stop consuming that queue on this
    # worker (it is consumed by mistake, for example after a manual
    # add_consumer), so a qualified worker takes it. Best effort.
    try:
        celery_app.control.cancel_consumer(delivery_queue, destination=[task.request.hostname])
    except Exception as exc:
        logger.error(
            "asr_consumer_cancel_failed queue=%s error=%s", delivery_queue, type(exc).__name__
        )
    else:
        logger.info("asr_consumer_cancelled queue=%s", delivery_queue)
    raise Reject(requeue=True)


@celery_app.task(
    bind=True,
    name=PROCESS_MEETING_TASK,
    autoretry_for=(),
    acks_late=True,
)
def process_meeting_job(self, job_id: str, workspace_id: str | None = None):
    from backend.jobs.outbox import verify_job_payload
    from backend.jobs.processor import process_job

    # One correlation id per attempt (HOM-113): its audit events and log lines
    # carry ``job:<id>:<delivery>``; ``job:<id>`` is the prefix an operator searches.
    with correlation_scope(job_correlation_id(job_id, self.request.id)):
        # Readiness guard (HOM-42): before the payload check and any ASR work.
        # Only a broker delivery is routed. Eager mode and a direct call
        # (``task.run(...)``; Celery's tracer sets called_directly=False for a
        # delivery) have no delivery, so there is nothing to route.
        if not self.request.is_eager and not self.request.called_directly:
            rerouted = _asr_delivery_guard(self, job_id, workspace_id)
            if rerouted is not None:
                return rerouted
        # The message may assert a workspace; the persisted job is the authority.
        if not verify_job_payload(job_id, workspace_id):
            return {"job_id": job_id, "status": "rejected_payload"}
        # No broker-level retry (WP06-T04): a retryable failure requeues the job
        # through the outbox; an abandoned attempt is recovered by the lease
        # sweeper. Either way the message is acknowledged here.
        return process_job(job_id, worker_id=f"celery:{self.request.id}")


@celery_app.task(name=DISPATCH_SWEEP_TASK, acks_late=False, ignore_result=True)
def sweep_dispatch_outbox_task():
    """Periodic recovery: publish every due outbox row (bounded batch)."""
    from backend.jobs.outbox import sweep_dispatch_outbox

    return run_with_heartbeat("dispatch_outbox", sweep_dispatch_outbox)


@celery_app.task(name=LEASE_RECOVERY_TASK, acks_late=False, ignore_result=True)
def sweep_expired_leases_task():
    """Periodic recovery: fence and requeue abandoned RUNNING attempts."""
    from backend.jobs.recovery import sweep_expired_leases

    return run_with_heartbeat("lease_recovery", sweep_expired_leases)


@celery_app.task(name=VOICE_RECOVERY_TASK, acks_late=False, ignore_result=True)
def sweep_voice_recovery_task():
    """Periodic voice recovery: lost media nodes and expired capacity slots."""
    from backend.voice.recovery import sweep_voice_recovery

    return run_with_heartbeat("voice_recovery", sweep_voice_recovery)


@celery_app.task(name=USAGE_RECONCILIATION_TASK, acks_late=False, ignore_result=True)
def reconcile_usage_task():
    """Periodic bookkeeping: release orphaned reservations, report counter drift."""
    from backend.jobs.usage_reconciliation import reconcile_usage

    return run_with_heartbeat("usage_reconciliation", reconcile_usage)


@celery_app.task(name=ARTIFACT_CLEANUP_TASK, acks_late=False, ignore_result=True)
def sweep_abandoned_artifacts_task():
    """Periodic bookkeeping: remove objects of abandoned/invalidated stage checkpoints."""
    from backend.jobs.checkpoints import sweep_abandoned_artifacts

    return run_with_heartbeat("artifact_cleanup", sweep_abandoned_artifacts)


@celery_app.task(name=MEDIA_RECONCILIATION_TASK, acks_late=False, ignore_result=True)
def reconcile_media_objects_task():
    """Periodic bookkeeping: retry pending deletions, report/remove orphans, flag missing."""
    from backend.jobs.media_reconciliation import reconcile_media_objects

    return run_with_heartbeat("media_reconciliation", reconcile_media_objects)


@celery_app.task(name=RETENTION_PURGE_TASK, acks_late=False, ignore_result=True)
def run_retention_purge_task():
    """Periodic: tombstone expired conversations and drive due purges (WP07-T03)."""
    from backend.jobs.retention_purge import run_retention_purge

    return run_with_heartbeat("retention_purge", run_retention_purge)


@celery_app.task(
    bind=True,
    name=RECOMPUTE_ANALYSIS_TASK,
    autoretry_for=(),
    acks_late=True,
)
def recompute_meeting_analysis_task(
    self, meeting_id: str, target_revision_id: str, workspace_id: str | None = None
):
    """Recompute one meeting's analysis on a corrected revision (HOM-63).

    No broker-level retry: a failure is recorded as a failed analysis run and
    the stale-analysis sweep dispatches again until the attempt cap.
    """
    from backend.jobs.recompute import run_recompute

    return run_recompute(
        meeting_id, target_revision_id, workspace_id=workspace_id, delivery=self.request.id
    )


@celery_app.task(name=RECOMPUTE_SWEEP_TASK, acks_late=False, ignore_result=True)
def sweep_stale_analyses_task():
    """Periodic recovery: dispatch a recompute for every stale meeting below the cap."""
    from backend.jobs.recompute import sweep_stale_analyses

    return run_with_heartbeat("analysis_recompute", sweep_stale_analyses)


@celery_app.task(name=CRM_RECONCILIATION_TASK, acks_late=False, ignore_result=True)
def reconcile_crm_records_task():
    """Periodic: apply pending CRM records whose conversation arrived (HOM-88)."""
    from backend.jobs.crm_reconciliation import reconcile_pending_records

    return run_with_heartbeat("crm_reconciliation", reconcile_pending_records)


# One delivery waits at most the request timeout. These limits, added to it, are
# a backstop below the delivery lease (timeout + 30 s): a stuck task ends before
# the row can be claimed again and never holds a processing worker for long.
# The soft limit is recorded as a timeout.
WEBHOOK_TASK_SOFT_LIMIT_GRACE_SECONDS = 10
WEBHOOK_TASK_HARD_LIMIT_GRACE_SECONDS = 20
_WEBHOOK_TIMEOUT_SECONDS = get_settings().webhook_delivery_timeout_seconds


@celery_app.task(
    name=DELIVER_WEBHOOK_TASK,
    autoretry_for=(),
    acks_late=False,
    ignore_result=True,
    soft_time_limit=_WEBHOOK_TIMEOUT_SECONDS + WEBHOOK_TASK_SOFT_LIMIT_GRACE_SECONDS,
    time_limit=_WEBHOOK_TIMEOUT_SECONDS + WEBHOOK_TASK_HARD_LIMIT_GRACE_SECONDS,
)
def deliver_webhook_task(delivery_id: str):
    """Send one webhook delivery (HOM-89). The claim decides; a duplicate task is inert.

    No broker-level retry: a lost task is found again by the sweep, and an
    attempt cut off after its claim is resent when its lease expires.
    """
    from backend.jobs import webhook_delivery

    webhook_delivery.deliver_webhook(delivery_id)


@celery_app.task(name=WEBHOOK_SWEEP_TASK, acks_late=False, ignore_result=True)
def sweep_webhook_deliveries_task():
    """Periodic recovery: publish a delivery task for every due webhook delivery."""
    from backend.jobs import webhook_delivery

    def sweep() -> dict[str, int]:
        return {"published": len(webhook_delivery.sweep_webhook_deliveries())}

    return run_with_heartbeat("webhook_delivery", sweep)


@celery_app.task(name=COACHING_REMINDER_TASK, acks_late=False, ignore_result=True)
def sweep_coaching_reminders_task():
    """Periodic: each active coaching task's due-soon reminder and overdue notice, once
    (HOM-77, ADR 0062 D4)."""
    from backend.jobs import coaching_reminders

    return run_with_heartbeat("coaching_reminders", coaching_reminders.run_coaching_reminders)
