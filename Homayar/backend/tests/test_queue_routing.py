"""Queue contract between the producer and the workers (WP06-T01).

Before this contract the dispatcher published to the ``processing`` queue while
every worker command consumed only Celery's default ``celery`` queue, so jobs
were accepted but never executed.

HOM-42 (ADR 0074): meeting jobs go to the ASR queue of their requirement
(``asr-cpu`` or ``asr-gpu``) and a worker consumes it only when its readiness
check qualifies it.
"""

from __future__ import annotations

import os

import pytest
from celery.contrib.testing.worker import start_worker
from celery.result import AsyncResult
from sqlalchemy import select

import backend.celery_app as celery_module
from backend.celery_app import (
    ASR_CPU_QUEUE,
    ASR_GPU_QUEUE,
    PROCESS_MEETING_TASK,
    PROCESSING_QUEUE,
    celery_app,
)
from backend.database.saas_models import JobDispatch, JobState, ProcessingJob
from backend.jobs import dispatcher, outbox, worker_readiness
from backend.jobs.tasks import process_meeting_job
from backend.providers.host_checks import HostProbes
from backend.tests._database import inspection_session_factory
from backend.tests._transport import use_broker as _broker
from backend.tests.test_job_processor import _providers, _queued_upload


def test_worker_default_queue_is_the_routed_queue() -> None:
    conf = celery_app.conf
    assert conf.task_default_queue == PROCESSING_QUEUE
    declared = {queue.name for queue in conf.task_queues}
    assert declared == {PROCESSING_QUEUE, ASR_CPU_QUEUE, ASR_GPU_QUEUE}
    # HOM-42: the meeting job is routed to the ASR queue of its requirement.
    routed = celery_app.amqp.router.route({}, PROCESS_MEETING_TASK)["queue"].name
    assert routed == worker_readiness.asr_queue_for(celery_module.get_settings())
    assert process_meeting_job.name == PROCESS_MEETING_TASK
    assert PROCESS_MEETING_TASK in celery_app.tasks


def test_transport_redelivery_bound_covers_the_hard_time_limit() -> None:
    """A delivery must not become visible again while its attempt may still
    be running under Celery's own kill bound (WP06-T07)."""
    conf = celery_app.conf
    assert conf.task_acks_late is True and conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.broker_transport_options["visibility_timeout"] == conf.task_time_limit
    assert conf.task_time_limit > conf.task_soft_time_limit


def test_dispatcher_publishes_to_the_declared_queue(monkeypatch: pytest.MonkeyPatch, settings):
    calls: list[dict] = []

    def fake_apply_async(*, args, queue):
        calls.append({"args": args, "queue": queue})

    monkeypatch.setattr(process_meeting_job, "apply_async", fake_apply_async)
    assert dispatcher.enqueue_processing_job("job-1", settings) is True
    # HOM-42: local Whisper on the CPU (the test settings) goes to asr-cpu.
    assert calls == [{"args": ["job-1"], "queue": ASR_CPU_QUEUE}]


def test_dispatcher_reports_publish_failure(monkeypatch: pytest.MonkeyPatch, settings):
    def failing_apply_async(**_kwargs):
        raise ConnectionError("broker down")

    monkeypatch.setattr(process_meeting_job, "apply_async", failing_apply_async)
    assert dispatcher.enqueue_processing_job("job-2", settings) is False


def _broker_params() -> list:
    params = [pytest.param("memory://", "cache+memory://", id="memory")]
    redis_url = os.environ.get("TEST_REDIS_URL", "").strip()
    if redis_url:
        # The real transport (WP06-T07); ``use_broker`` also resets the cached
        # result backend and producer pool that ``Celery.close()`` keeps, so
        # this parametrisation does not keep publishing to the memory broker.
        params.append(pytest.param(redis_url, redis_url, id="redis"))
    return params


class _CpuRuntime:
    """CTranslate2 as the readiness check sees it on a CPU host (HOM-42)."""

    def get_cuda_device_count(self) -> int:
        return 0

    def get_supported_compute_types(self, device: str, device_index: int = 0) -> set[str]:
        return {"float32", "int8"}


@pytest.fixture
def qualified_cpu_worker(monkeypatch):
    """The in-process worker passes its readiness check for local Whisper on
    the CPU (fake probes), so it consumes asr-cpu, where the dispatcher
    publishes. The selection and readiness it stores are restored after."""
    queues = celery_app.amqp.queues
    saved_selection = queues._consume_from
    saved_readiness = celery_module._worker_readiness
    monkeypatch.setattr(
        worker_readiness,
        "default_probes",
        lambda: HostProbes(find_spec=lambda name: object(), ctranslate2=_CpuRuntime, environ={}),
    )
    yield
    queues._consume_from = saved_selection
    celery_module._worker_readiness = saved_readiness


@pytest.mark.parametrize(("broker_url", "backend_url"), _broker_params())
def test_worker_without_explicit_queues_consumes_dispatched_jobs(
    broker_url: str,
    backend_url: str,
    client,
    settings,
    email_outbox,
    monkeypatch,
    qualified_cpu_worker,
) -> None:
    """Publish through the real dispatcher and let a worker started with no
    ``--queues`` option consume it. Without the queue contract this hangs and
    times out because the message sits in ``processing`` while the worker
    listens on ``celery``.

    The second half goes through the transactional outbox (WP06-T02): a
    committed job is published by ``dispatch_job`` and the same worker
    processes it; the outbox row records the broker message id."""
    job_id, _workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    # The in-process worker must see the test settings (temporary object
    # storage root) and deterministic providers, exactly like the API did.
    monkeypatch.setattr("backend.jobs.processor.get_settings", lambda: settings)
    monkeypatch.setattr("backend.jobs.processor.create_provider_bundle", lambda _s: _providers())
    with _broker(broker_url, backend_url):
        with start_worker(
            celery_app,
            pool="solo",
            concurrency=1,
            perform_ping_check=False,
            loglevel="warning",
            shutdown_timeout=15,
        ):
            assert dispatcher.enqueue_processing_job("job-does-not-exist", settings) is True
            result = process_meeting_job.apply_async(
                args=["job-does-not-exist-2"], queue=PROCESSING_QUEUE
            )
            outcome = result.get(timeout=20)

            dispatched = outbox.dispatch_job(job_id, settings)
            assert dispatched.state == "published"
            with inspection_session_factory()() as db:
                row = db.scalar(select(JobDispatch).where(JobDispatch.job_id == job_id))
                assert row is not None and row.message_id
                message_id = row.message_id
            processed = AsyncResult(message_id, app=celery_app).get(timeout=30)
    assert outcome is not None
    assert processed == {"job_id": job_id, "status": "completed"}
    with inspection_session_factory()() as db:
        assert db.get(ProcessingJob, job_id).state == JobState.COMPLETED
