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
from celery.signals import worker_init, worker_process_init, worker_ready
from kombu import Queue

from backend.config import get_settings

logger = logging.getLogger(__name__)

PROCESSING_QUEUE = "processing"
PROCESS_MEETING_TASK = "backend.jobs.process_meeting"
DISPATCH_SWEEP_TASK = "backend.jobs.sweep_dispatch_outbox"
LEASE_RECOVERY_TASK = "backend.jobs.sweep_expired_leases"
USAGE_RECONCILIATION_TASK = "backend.jobs.reconcile_usage"
VOICE_RECOVERY_TASK = "backend.voice.sweep_recovery"
ARTIFACT_CLEANUP_TASK = "backend.jobs.sweep_abandoned_artifacts"
MEDIA_RECONCILIATION_TASK = "backend.jobs.reconcile_media_objects"
RETENTION_PURGE_TASK = "backend.jobs.run_retention_purge"
# Analysis recompute after a transcript correction (HOM-63, ADR 0037).
RECOMPUTE_ANALYSIS_TASK = "backend.jobs.recompute_meeting_analysis"
RECOMPUTE_SWEEP_TASK = "backend.jobs.sweep_stale_analyses"
# Delayed CRM records (HOM-88): retries records that arrived before their conversation.
CRM_RECONCILIATION_TASK = "backend.jobs.reconcile_crm_records"
# Federated identity revalidation (HOM-128): reconfirms stale OIDC identities.
FEDERATION_REVALIDATION_TASK = "backend.jobs.revalidate_federated_identities"
# Signed webhook delivery (HOM-89, ADR 0050).
DELIVER_WEBHOOK_TASK = "backend.jobs.deliver_webhook"
WEBHOOK_SWEEP_TASK = "backend.jobs.sweep_webhook_deliveries"
# Coaching due-soon reminders and overdue notices (HOM-77, ADR 0062 D4).
COACHING_REMINDER_TASK = "backend.jobs.sweep_coaching_reminders"
# Grace between the cooperative attempt deadline and the hard kill.
TIME_LIMIT_GRACE_SECONDS = 60
# A prefork child warms the ASR model in its initializer, before it reports
# up. Celery kills a child that stays silent for worker_proc_alive_timeout
# (default 4.0 s); a large model loads in 12 to 14 s (HOM-351, SPEC 10.1).
WORKER_PROC_ALIVE_TIMEOUT_SECONDS = 120.0


def make_celery() -> Celery:
    settings = get_settings()
    broker_url = settings.redis_url

    celery = Celery(
        "homayar",
        broker=broker_url,
        backend=broker_url,
        include=["backend.jobs.tasks", "backend.jobs.federation_revalidation"],
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
            RECOMPUTE_ANALYSIS_TASK: {"queue": PROCESSING_QUEUE},
            RECOMPUTE_SWEEP_TASK: {"queue": PROCESSING_QUEUE},
            DELIVER_WEBHOOK_TASK: {"queue": PROCESSING_QUEUE},
            WEBHOOK_SWEEP_TASK: {"queue": PROCESSING_QUEUE},
            COACHING_REMINDER_TASK: {"queue": PROCESSING_QUEUE},
        },
        # Attempt bound of last resort (WP06-T04): the soft limit raises inside
        # the task (finalised as a retryable ``attempt_timeout``); the hard
        # limit kills a worker that ignores it — its lease then lapses and the
        # recovery sweeper fences and requeues the job.
        task_soft_time_limit=settings.job_attempt_timeout_seconds,
        task_time_limit=settings.job_attempt_timeout_seconds + TIME_LIMIT_GRACE_SECONDS,
        # Warm ASR workers (HOM-41, SPEC 10.1): a child that loads the model
        # in its initializer needs longer than the 4 s default to report up.
        worker_proc_alive_timeout=WORKER_PROC_ALIVE_TIMEOUT_SECONDS,
        # Redis transport (WP06-T07): an unacknowledged delivery becomes
        # visible to other consumers again after ``visibility_timeout``
        # (kombu default: one hour, shorter than the hard time limit above).
        # Bound it by the attempt bound so a live attempt is never redelivered
        # by the broker; recovery of a dead worker is lease-driven
        # (``backend.jobs.recovery``) and does not wait for this timeout, and a
        # late redelivery meets the lease and is inert either way.
        broker_transport_options={
            "visibility_timeout": settings.job_attempt_timeout_seconds + TIME_LIMIT_GRACE_SECONDS,
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
                "options": {
                    "queue": PROCESSING_QUEUE,
                    "expires": float(settings.dispatch_sweep_interval_seconds),
                },
            },
            "voice-recovery": {
                "task": VOICE_RECOVERY_TASK,
                "schedule": float(settings.voice_node_heartbeat_ttl_seconds),
                "options": {
                    "queue": PROCESSING_QUEUE,
                    "expires": float(settings.voice_node_heartbeat_ttl_seconds),
                },
            },
            "job-lease-recovery": {
                "task": LEASE_RECOVERY_TASK,
                "schedule": float(settings.job_recovery_sweep_interval_seconds),
                "options": {
                    "queue": PROCESSING_QUEUE,
                    "expires": float(settings.job_recovery_sweep_interval_seconds),
                },
            },
            # - usage reconciliation (WP06-T06) releases orphaned reservations
            #   and reports counter drift. Without beat run
            #   ``python -m backend.jobs.usage_reconciliation``.
            "usage-reconciliation": {
                "task": USAGE_RECONCILIATION_TASK,
                "schedule": float(settings.usage_reconciliation_interval_seconds),
                "options": {
                    "queue": PROCESSING_QUEUE,
                    "expires": float(settings.usage_reconciliation_interval_seconds),
                },
            },
            # - artifact cleanup (WP06-T03) removes objects of never-published
            #   or invalidated stage checkpoints after the grace period.
            #   Without beat run ``python -m backend.jobs.checkpoints``.
            "stage-artifact-cleanup": {
                "task": ARTIFACT_CLEANUP_TASK,
                "schedule": float(settings.job_artifact_sweep_interval_seconds),
                "options": {
                    "queue": PROCESSING_QUEUE,
                    "expires": float(settings.job_artifact_sweep_interval_seconds),
                },
            },
            # - media reconciliation (WP05-T04) retries pending deletions,
            #   reports or removes orphan objects and flags missing ones.
            #   Without beat run python -m backend.jobs.media_reconciliation
            "media-reconciliation": {
                "task": MEDIA_RECONCILIATION_TASK,
                "schedule": float(settings.media_reconciliation_interval_seconds),
                "options": {
                    "queue": PROCESSING_QUEUE,
                    "expires": float(settings.media_reconciliation_interval_seconds),
                },
            },
            # - retention purge (WP07-T03) tombstones expired conversations
            #   and drives every durable purge (content, bytes, receipts).
            #   Without beat run python -m backend.jobs.retention_purge
            "retention-purge": {
                "task": RETENTION_PURGE_TASK,
                "schedule": float(settings.retention_purge_interval_seconds),
                "options": {
                    "queue": PROCESSING_QUEUE,
                    "expires": float(settings.retention_purge_interval_seconds),
                },
            },
            # - analysis recompute sweep (HOM-63) dispatches a recompute for
            #   every stale meeting below the failure cap (a lost dispatch).
            #   Without beat call backend.jobs.recompute.sweep_stale_analyses()
            "analysis-recompute-sweep": {
                "task": RECOMPUTE_SWEEP_TASK,
                "schedule": float(settings.recompute_sweep_interval_seconds),
                "options": {
                    "queue": PROCESSING_QUEUE,
                    "expires": float(settings.recompute_sweep_interval_seconds),
                },
            },
            # - CRM reconciliation (HOM-88) applies pending CRM records whose
            #   conversation arrived and queues the expired ones. Without beat
            #   run python -m backend.jobs.crm_reconciliation
            "crm-reconciliation": {
                "task": CRM_RECONCILIATION_TASK,
                "schedule": float(settings.crm_reconciliation_interval_seconds),
                "options": {
                    "queue": PROCESSING_QUEUE,
                    "expires": float(settings.crm_reconciliation_interval_seconds),
                },
            },
            # - federated identity revalidation (HOM-128) reconfirms every
            #   stale OIDC identity; without beat run
            #   python -m backend.jobs.federation_revalidation
            "federation-revalidation": {
                "task": FEDERATION_REVALIDATION_TASK,
                "schedule": float(settings.federation_revalidation_interval_seconds),
                "options": {
                    "queue": PROCESSING_QUEUE,
                    "expires": float(settings.federation_revalidation_interval_seconds),
                },
            },
            # - webhook delivery sweep (HOM-89) publishes a delivery task for
            #   every due webhook delivery (a lost publish, an expired lease).
            #   Without beat run python -m backend.jobs.webhook_delivery
            "webhook-delivery-sweep": {
                "task": WEBHOOK_SWEEP_TASK,
                "schedule": float(settings.webhook_sweep_interval_seconds),
                "options": {
                    "queue": PROCESSING_QUEUE,
                    "expires": float(settings.webhook_sweep_interval_seconds),
                },
            },
            # - coaching reminders (HOM-77) send each active coaching task's due-soon
            #   reminder and overdue notice once. Without beat run
            #   python -m backend.jobs.coaching_reminders
            "coaching-reminders": {
                "task": COACHING_REMINDER_TASK,
                "schedule": float(settings.coaching_reminder_sweep_interval_seconds),
                "options": {
                    "queue": PROCESSING_QUEUE,
                    "expires": float(settings.coaching_reminder_sweep_interval_seconds),
                },
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


def _decision_fields(decision: object) -> str:
    return (
        f"ok={decision.ok} reason={decision.reason} processes={decision.processes}"
        f" allowed_processes={decision.allowed_processes}"
        f" per_process_mib={decision.per_process_mib}"
        f" required_mib={decision.required_mib} budget_mib={decision.budget_mib}"
    )


def _autoscale_bounds(value: object) -> tuple[int, int]:
    """``(min, max)`` of a Celery autoscale value: ``"max,min"`` (min may be
    empty, meaning 0), ``[max, min]`` or ``(max, min)``."""
    if isinstance(value, str):
        max_c, _, min_c = value.partition(",")
        return (int(min_c) if min_c else 0, int(max_c))
    max_c, min_c = value
    return (int(min_c), int(max_c))


# The CapacityDecision of the last worker_init, or None. Logged again at
# worker_ready, when Celery's logging is set up (SPEC 10.3).
_worker_memory_decision: object | None = None


@worker_init.connect
def on_worker_init(sender=None, **_kwargs: object) -> None:
    """Clamp or refuse the worker's concurrency before the pool is created
    (HOM-41, ADR 0067). Fires in the parent, after concurrency is final.

    Never loads a model: the parent forks the children, and a CUDA context
    made before a fork is not usable in the child. Celery's signal dispatch
    swallows every ``Exception`` a receiver raises, but not ``SystemExit``,
    so a refused configuration stops the worker with exit code 78.
    """
    global _worker_memory_decision

    from backend.jobs.worker_capacity import check_worker_memory

    settings = get_settings()
    autoscale = getattr(sender, "autoscale", None)
    options = getattr(sender, "options", None)
    if not autoscale and isinstance(options, dict):
        autoscale = options.get("autoscale")
    # With autoscale the pool can grow to its maximum, so the maximum is the
    # process count that must fit the budget (SPEC 10.4).
    processes = _autoscale_bounds(autoscale)[1] if autoscale else sender.concurrency
    decision = check_worker_memory(settings, pool=sender.pool_cls, concurrency=processes)
    _worker_memory_decision = decision
    logger.info("worker_memory_check %s", _decision_fields(decision))
    if decision.ok:
        return
    if decision.reason == "over_budget":
        allowed = decision.allowed_processes or 1
        old_concurrency = sender.concurrency
        sender.concurrency = min(old_concurrency, allowed)
        # Celery's Pool bootstep re-reads autoscale from sender.options after
        # this receiver, so both places must carry the clamped [max, min].
        if autoscale:
            old_min, _old_max = _autoscale_bounds(autoscale)
            clamped = [allowed, min(old_min, allowed)]
            if getattr(sender, "autoscale", None):
                sender.autoscale = clamped
            if isinstance(options, dict) and options.get("autoscale"):
                options["autoscale"] = clamped
        # WARNING, not INFO: at worker_init Celery has not set up logging,
        # so INFO records are dropped (SPEC 10.3).
        logger.warning(
            "worker_concurrency_clamped old=%s new=%s allowed_processes=%s",
            old_concurrency,
            sender.concurrency,
            allowed,
        )
        return
    logger.error("worker_memory_refused %s", _decision_fields(decision))
    raise SystemExit(78)


@worker_ready.connect
def on_worker_ready(sender=None, **_kwargs: object) -> None:
    """Log the stored memory decision once Celery's logging is up (SPEC 10.3).

    Never raises, and logs nothing when no decision was stored.
    """
    global _worker_memory_decision

    decision = _worker_memory_decision
    _worker_memory_decision = None
    if decision is not None:
        logger.info("worker_memory_check %s", _decision_fields(decision))


@worker_process_init.connect
def on_worker_process_init(sender=None, **_kwargs: object) -> None:
    """Warm the ASR model in each prefork child (HOM-41). Never raises."""
    from backend.providers.warm_models import warm_configured_asr

    try:
        warm_configured_asr(get_settings())
    except Exception as exc:  # warm_configured_asr never raises on its own
        # Error class only: no paths and no message text (log privacy).
        logger.warning("asr_warm_failed error=%s", type(exc).__name__)


celery_app = make_celery()
