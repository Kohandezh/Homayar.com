from __future__ import annotations

from backend.celery_app import (
    ARTIFACT_CLEANUP_TASK,
    DISPATCH_SWEEP_TASK,
    LEASE_RECOVERY_TASK,
    MEDIA_RECONCILIATION_TASK,
    PROCESS_MEETING_TASK,
    RETENTION_PURGE_TASK,
    USAGE_RECONCILIATION_TASK,
    celery_app,
)


@celery_app.task(
    bind=True,
    name=PROCESS_MEETING_TASK,
    autoretry_for=(),
    acks_late=True,
)
def process_meeting_job(self, job_id: str, workspace_id: str | None = None):
    from backend.jobs.outbox import verify_job_payload
    from backend.jobs.processor import process_job

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

    return sweep_dispatch_outbox()


@celery_app.task(name=LEASE_RECOVERY_TASK, acks_late=False, ignore_result=True)
def sweep_expired_leases_task():
    """Periodic recovery: fence and requeue abandoned RUNNING attempts."""
    from backend.jobs.recovery import sweep_expired_leases

    return sweep_expired_leases()


@celery_app.task(name=USAGE_RECONCILIATION_TASK, acks_late=False, ignore_result=True)
def reconcile_usage_task():
    """Periodic bookkeeping: release orphaned reservations, report counter drift."""
    from backend.jobs.usage_reconciliation import reconcile_usage

    return reconcile_usage()


@celery_app.task(name=ARTIFACT_CLEANUP_TASK, acks_late=False, ignore_result=True)
def sweep_abandoned_artifacts_task():
    """Periodic bookkeeping: remove objects of abandoned/invalidated stage checkpoints."""
    from backend.jobs.checkpoints import sweep_abandoned_artifacts

    return sweep_abandoned_artifacts()


@celery_app.task(name=MEDIA_RECONCILIATION_TASK, acks_late=False, ignore_result=True)
def reconcile_media_objects_task():
    """Periodic bookkeeping: retry pending deletions, report/remove orphans, flag missing."""
    from backend.jobs.media_reconciliation import reconcile_media_objects

    return reconcile_media_objects()


@celery_app.task(name=RETENTION_PURGE_TASK, acks_late=False, ignore_result=True)
def run_retention_purge_task():
    """Periodic: tombstone expired conversations and drive due purges (WP07-T03)."""
    from backend.jobs.retention_purge import run_retention_purge

    return run_retention_purge()
