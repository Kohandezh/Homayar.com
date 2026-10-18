"""Celery app — background job processing for Homayar.

Queue contract (WP06-T01): the producer (backend.jobs.dispatcher) and every
worker agree on one explicit queue, PROCESSING_QUEUE. It is both the routed
queue for backend.jobs.process_meeting and the worker default, so a worker
started without ``--queues`` still consumes it; deployment commands pass
``--queues processing`` anyway so the subscription is visible in process lists.
"""

import logging

from celery import Celery
from celery.signals import setup_logging as celery_setup_logging
from kombu import Queue

from backend.config import get_settings

PROCESSING_QUEUE = "processing"
PROCESS_MEETING_TASK = "backend.jobs.process_meeting"
DISPATCH_SWEEP_TASK = "backend.jobs.sweep_dispatch_outbox"
LEASE_RECOVERY_TASK = "backend.jobs.sweep_expired_leases"
USAGE_RECONCILIATION_TASK = "backend.jobs.reconcile_usage"
VOICE_RECOVERY_TASK = "backend.voice.sweep_recovery"
ARTIFACT_CLEANUP_TASK = "backend.jobs.sweep_abandoned_artifacts"
MEDIA_RECONCILIATION_TASK = "backend.jobs.reconcile_media_objects"
RETENTION_PURGE_TASK = "backend.jobs.run_retention_purge"
# Grace between the cooperative attempt deadline and the hard kill.
TIME_LIMIT_GRACE_SECONDS = 60


def make_celery() -> Celery:
    settings = get_settings()
    broker_url = settings.redis_url

    celery = Celery(
        "homayar",
        broker=broker_url,
        backend=broker_url,
        include=["backend.jobs.tasks"],
    )
    celery.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_always_eager=settings.tasks_eager,
        task_eager_propagates=settings.environment == "test",
        # Queue contract: one declared queue, also the default, so producer
        # routing and worker subscriptions cannot drift apart.
        task_default_queue=PROCESSING_QUEUE,
        task_queues=(Queue(PROCESSING_QUEUE, routing_key=PROCESSING_QUEUE),),
        task_routes={
            PROCESS_MEETING_TASK: {"queue": PROCESSING_QUEUE},
            DISPATCH_SWEEP_TASK: {"queue": PROCESSING_QUEUE},
            LEASE_RECOVERY_TASK: {"queue": PROCESSING_QUEUE},
            USAGE_RECONCILIATION_TASK: {"queue": PROCESSING_QUEUE},
            ARTIFACT_CLEANUP_TASK: {"queue": PROCESSING_QUEUE},
            MEDIA_RECONCILIATION_TASK: {"queue": PROCESSING_QUEUE},
            VOICE_RECOVERY_TASK: {"queue": PROCESSING_QUEUE},
        },
        # Attempt bound of last resort (WP06-T04): the soft limit raises inside
        # the task (finalised as a retryable ``attempt_timeout``); the hard
        # limit kills a worker that ignores it — its lease then lapses and the
        # recovery sweeper fences and requeues the job.
        task_soft_time_limit=settings.job_attempt_timeout_seconds,
        task_time_limit=settings.job_attempt_timeout_seconds + TIME_LIMIT_GRACE_SECONDS,
        # Redis transport (WP06-T07): an unacknowledged delivery becomes
        # visible to other consumers again after ``visibility_timeout``
        # (kombu default: one hour, shorter than the hard time limit above).
        # Bound it by the attempt bound so a live attempt is never redelivered
        # by the broker; recovery of a dead worker is lease-driven
        # (``backend.jobs.recovery``) and does not wait for this timeout, and a
        # late redelivery meets the lease and is inert either way.
        broker_transport_options={
            "visibility_timeout": settings.job_attempt_timeout_seconds
            + TIME_LIMIT_GRACE_SECONDS,
        },
        # Periodic recovery (needs one beat scheduler: ``celery -A
        # backend.celery_app beat`` or the compose ``scheduler`` service):
        # - dispatch sweep (WP06-T02) republishes rows left PENDING;
        # - lease sweep (WP06-T04) fences RUNNING attempts whose lease lapsed
        #   and re-arms their intent. Without beat run
        # ``python -m backend.jobs.outbox`` / ``python -m backend.jobs.recovery``.
        beat_schedule={
            "dispatch-outbox-sweep": {
                "task": DISPATCH_SWEEP_TASK,
                "schedule": float(settings.dispatch_sweep_interval_seconds),
                "options": {"queue": PROCESSING_QUEUE, "expires":
                    float(settings.dispatch_sweep_interval_seconds)},
            },
            "voice-recovery": {
                "task": VOICE_RECOVERY_TASK,
                "schedule": float(settings.voice_node_heartbeat_ttl_seconds),
                "options": {"queue": PROCESSING_QUEUE, "expires":
                    float(settings.voice_node_heartbeat_ttl_seconds)},
            },
            "job-lease-recovery": {
                "task": LEASE_RECOVERY_TASK,
                "schedule": float(settings.job_recovery_sweep_interval_seconds),
                "options": {"queue": PROCESSING_QUEUE, "expires":
                    float(settings.job_recovery_sweep_interval_seconds)},
            },
            # - usage reconciliation (WP06-T06) releases orphaned reservations
            #   and reports counter drift. Without beat run
            #   ``python -m backend.jobs.usage_reconciliation``.
            "usage-reconciliation": {
                "task": USAGE_RECONCILIATION_TASK,
                "schedule": float(settings.usage_reconciliation_interval_seconds),
                "options": {"queue": PROCESSING_QUEUE, "expires":
                    float(settings.usage_reconciliation_interval_seconds)},
            },
            # - artifact cleanup (WP06-T03) removes objects of never-published
            #   or invalidated stage checkpoints after the grace period.
            #   Without beat run ``python -m backend.jobs.checkpoints``.
            "stage-artifact-cleanup": {
                "task": ARTIFACT_CLEANUP_TASK,
                "schedule": float(settings.job_artifact_sweep_interval_seconds),
                "options": {"queue": PROCESSING_QUEUE, "expires":
                    float(settings.job_artifact_sweep_interval_seconds)},
            },
            # - media reconciliation (WP05-T04) retries pending deletions,
            #   reports or removes orphan objects and flags missing ones.
            #   Without beat run python -m backend.jobs.media_reconciliation
            "media-reconciliation": {
                "task": MEDIA_RECONCILIATION_TASK,
                "schedule": float(settings.media_reconciliation_interval_seconds),
                "options": {"queue": PROCESSING_QUEUE, "expires":
                    float(settings.media_reconciliation_interval_seconds)},
            },
            # - retention purge (WP07-T03) tombstones expired conversations
            #   and drives every durable purge (content, bytes, receipts).
            #   Without beat run python -m backend.jobs.retention_purge
            "retention-purge": {
                "task": RETENTION_PURGE_TASK,
                "schedule": float(settings.retention_purge_interval_seconds),
                "options": {"queue": PROCESSING_QUEUE, "expires":
                    float(settings.retention_purge_interval_seconds)},
            },
        },
    )
    return celery


@celery_setup_logging.connect
def use_homayar_logging(loglevel: int | str | None = None, **_options: object) -> None:
    """Keep Homayar's JSON logging in workers and beat (log privacy).

    With no receiver on this signal a worker replaces the root handlers with
    Celery's own, which print a failed task's full traceback and the
    exception's text (SQL, bound parameters, psycopg DETAIL). With one, Celery
    leaves logging alone: every celery.* record reaches the scrubbed JSON
    handler, which writes the exception summary and no traceback.
    """
    from backend.services.logging_service import setup_logging

    level = logging.getLevelName(loglevel) if isinstance(loglevel, int) else loglevel
    setup_logging(level if isinstance(level, str) and level else "INFO")


celery_app = make_celery()
