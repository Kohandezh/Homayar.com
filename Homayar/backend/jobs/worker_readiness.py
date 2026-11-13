"""Worker readiness and ASR queue routing (HOM-42, ADR 0074).

    python -m backend.jobs.worker_readiness --check
    python -m backend.jobs.worker_readiness --workers

A meeting job (ASR, diarization, analysis) needs a GPU or only a CPU. The
producer publishes it to ``asr-gpu`` or ``asr-cpu`` (``asr_queue_for``). Each
worker checks at start what it can run (``assess_worker_readiness``) and
consumes only the ASR queues it qualifies for (``backend.celery_app``). A job
that needs a GPU never runs on a CPU-only worker: it waits in ``asr-gpu``
until a qualified worker is up.

The checks reuse the HOM-108 host checks (``backend.providers.host_checks``).
Nothing here loads a model or imports ``ctranslate2`` in this process: a CUDA
context made in the worker parent is not usable in the forked children (ADR
0067 D5), so CTranslate2 is asked in a fresh interpreter
(``subprocess_ctranslate2``).

No record and no log line holds a GPU UUID, a path, a URL, a secret or an
exception message. Exception class names only.

``--check`` assesses this container and prints the record as JSON. Exit
codes: 0 qualified, 1 not qualified, 2 configuration error (the settings or
the ASR adapter cannot be built). ``--workers`` asks the live workers and
reads the queue lengths. Exit codes: 0 ok, 1 an ASR queue has waiting
messages and no consumer (or a value is missing), 2 the broker cannot be
reached.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from types import SimpleNamespace
from typing import Any

from backend.config import get_settings
from backend.jobs import worker_capacity
from backend.providers import host_checks
from backend.providers.asr_profiles import get_asr_profile

# The queue names live in backend.celery_app (the queue contract). This module
# imports them at call time only: celery_app builds its routes with
# asr_queue_for, so a module-level import would be circular.

CTRANSLATE2_TIMEOUT_SECONDS = 60
INSPECT_TIMEOUT_SECONDS = 2.0
REDIS_TIMEOUT_SECONDS = 2.0
_CHILD_OUTPUT_LIMIT = 65536
# Run in a fresh interpreter: prints what CTranslate2 sees, as JSON.
_CTRANSLATE2_SCRIPT = textwrap.dedent(
    """\
    import json
    import ctranslate2

    count = int(ctranslate2.get_cuda_device_count())
    print(json.dumps({
        "cuda_device_count": count,
        "cpu_compute_types": sorted(ctranslate2.get_supported_compute_types("cpu")),
        "cuda_compute_types": [
            sorted(ctranslate2.get_supported_compute_types("cuda", index))
            for index in range(count)
        ],
    }))
    """
)
_PASSING = frozenset({"pass", "warn"})


# --- the job's requirement and its queue (producer side) -----------------------------


def asr_requirement(settings: Any) -> str:
    """``"remote"``, ``"gpu"`` or ``"cpu"``: what the configured ASR needs.

    Read from the settings only. No adapter is built and no model is loaded.
    """
    if not worker_capacity.is_local_whisper(settings):
        return "remote"
    if settings.asr_profile:
        device = get_asr_profile(settings.asr_profile).device
    else:
        device = settings.whisper_device
    return "gpu" if host_checks.is_cuda_device(device) else "cpu"


def asr_queue_for(settings: Any) -> str:
    """The ASR queue a meeting job goes to: ``asr-gpu`` only when it needs a GPU."""
    from backend.celery_app import ASR_CPU_QUEUE, ASR_GPU_QUEUE

    return ASR_GPU_QUEUE if asr_requirement(settings) == "gpu" else ASR_CPU_QUEUE


def vram_per_process_mib(settings: Any) -> int | None:
    """VRAM one worker process needs for the ASR model (MiB), or ``None`` when unknown.

    A set ``ASR_MODEL_VRAM_MIB`` wins, then the profile's ``vram_mib``, then
    the HOM-41 model-name memory table (a conservative stand-in). Each batch
    slot above 1 adds ``ASR_BATCH_SLOT_MEMORY_MIB``.
    """
    if settings.asr_model_vram_mib > 0:
        model_mib: int | None = settings.asr_model_vram_mib
    else:
        profile = get_asr_profile(settings.asr_profile) if settings.asr_profile else None
        if profile is not None and profile.vram_mib > 0:
            model_mib = profile.vram_mib
        else:
            # The model the adapter loads: the profile's, else whisper_model.
            model = profile.model if profile is not None else settings.whisper_model
            model_mib = worker_capacity.model_memory_mib(model)
    if model_mib is None:
        return None
    return model_mib + (settings.asr_batch_size - 1) * worker_capacity.ASR_BATCH_SLOT_MEMORY_MIB


# --- the readiness record --------------------------------------------------------------


@dataclass(frozen=True)
class WorkerReadiness:
    """What one worker is qualified for. Fields that do not apply are ``None``."""

    qualified: bool
    reason: str
    requirement: str | None = None  # "gpu", "cpu" or "remote"
    queues: tuple[str, ...] = ()  # the ASR queues this worker may consume
    asr_profile: str | None = None
    device: str | None = None
    device_index: int | None = None
    compute_type: str | None = None
    processes: int | None = None
    vram_required_mib: int | None = None
    vram_free_mib: int | None = None
    vram_budget_mib: int | None = None
    vram_allowed_processes: int | None = None
    gpu_name: str | None = None
    ram_budget_mib: int | None = None
    ram_per_process_mib: int | None = None
    ram_decision: str | None = None
    error_type: str | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable copy (lists, not tuples)."""
        data = {item.name: getattr(self, item.name) for item in fields(self)}
        data["queues"] = list(self.queues)
        data["checks"] = [dict(check) for check in self.checks]
        return data


def _public_check(check: Mapping[str, Any]) -> dict[str, Any]:
    """A host check without GPU UUIDs (privacy)."""
    data = dict(check)
    gpus = data.get("gpus")
    if isinstance(gpus, list):
        data["gpus"] = [
            {key: value for key, value in gpu.items() if key != "uuid"}
            for gpu in gpus
            if isinstance(gpu, Mapping)
        ]
    return data


# --- the check -----------------------------------------------------------------------


def assess_worker_readiness(
    settings: Any,
    *,
    pool: Any,
    concurrency: int,
    probes: host_checks.HostProbes | None = None,
    bundle: Any = None,
) -> WorkerReadiness:
    """Which ASR queues this worker may consume. Never raises.

    ``probes`` defaults to ``default_probes()``. ``bundle`` is an object with
    ``.asr``; ``None`` builds the ASR adapter alone from the settings (no
    model load). The first matching rule decides (SPEC 3.3, ADR 0074 D3).
    """
    try:
        return _assess(settings, pool=pool, concurrency=concurrency, probes=probes, bundle=bundle)
    except Exception as exc:  # a broken probe is a finding, never a crash
        try:
            requirement: str | None = asr_requirement(settings)
        except Exception:
            requirement = None
        return WorkerReadiness(
            qualified=False,
            reason="readiness_error",
            requirement=requirement,
            queues=(),
            error_type=type(exc).__name__,
        )


def _assess(
    settings: Any,
    *,
    pool: Any,
    concurrency: int,
    probes: host_checks.HostProbes | None,
    bundle: Any,
) -> WorkerReadiness:
    from backend.celery_app import ASR_CPU_QUEUE, ASR_GPU_QUEUE

    requirement = asr_requirement(settings)
    processes = worker_capacity.worker_processes(pool, concurrency)
    # RAM is reported only: HOM-41 already clamps or refuses at start.
    ram = worker_capacity.check_worker_memory(settings, pool=pool, concurrency=concurrency)
    common: dict[str, Any] = {
        "requirement": requirement,
        "asr_profile": settings.asr_profile or None,
        "processes": processes,
        "ram_budget_mib": ram.budget_mib,
        "ram_per_process_mib": ram.per_process_mib,
        "ram_decision": ram.reason,
    }

    def refuse(reason: str, **extra: Any) -> WorkerReadiness:
        return WorkerReadiness(qualified=False, reason=reason, queues=(), **common, **extra)

    if settings.worker_asr_queues == "off":
        return refuse("asr_queues_off")
    if requirement == "remote":
        return WorkerReadiness(
            qualified=True, reason="no_local_model", queues=(ASR_CPU_QUEUE,), **common
        )
    if bundle is None:
        from backend.providers import factory
        from backend.providers.base import ProviderError

        try:
            bundle = SimpleNamespace(asr=factory.create_asr_provider(settings))
        except (ProviderError, ValueError):
            return refuse("asr_adapter_failed")
    probes = probes if probes is not None else default_probes()

    if requirement == "cpu":
        host = host_checks.check_asr_host(settings, bundle, probes, min_free_gpu_mib=None)
        found = _host_fields(host)
        if _status(host, "asr_runtime") not in _PASSING:
            return refuse("asr_runtime_failed", **found)
        return WorkerReadiness(
            qualified=True, reason="ready", queues=(ASR_CPU_QUEUE,), **common, **found
        )

    per_process = vram_per_process_mib(settings)
    required = None if per_process is None else processes * per_process
    host = host_checks.check_asr_host(settings, bundle, probes, min_free_gpu_mib=required)
    budget = settings.worker_vram_budget_mib
    target = host.target_gpu or {}
    free = target.get("memory_free_mib")
    limits = [value for value in (free, budget if budget > 0 else None) if value is not None]
    found = {
        **_host_fields(host),
        "vram_required_mib": required,
        "vram_free_mib": free,
        "vram_budget_mib": budget,
        "vram_allowed_processes": (min(limits) // per_process if limits and per_process else None),
        "gpu_name": target.get("name"),
    }
    if _status(host, "asr_runtime") not in _PASSING:
        return refuse("asr_runtime_failed", **found)
    if _status(host, "gpu_driver") not in _PASSING:
        return refuse("gpu_driver_failed", **found)
    if required is None:
        return refuse("unknown_model_vram", **found)
    if budget > 0 and required > budget:
        return refuse("vram_over_budget", **found)
    if _status(host, "gpu_memory") not in _PASSING:
        return refuse("gpu_memory_failed", **found)
    return WorkerReadiness(
        qualified=True,
        reason="ready",
        # A GPU worker can run every CPU job too.
        queues=(ASR_GPU_QUEUE, ASR_CPU_QUEUE),
        **common,
        **found,
    )


def _status(host: host_checks.ASRHostChecks, name: str) -> str | None:
    return next((check["status"] for check in host.checks if check["name"] == name), None)


def _host_fields(host: host_checks.ASRHostChecks) -> dict[str, Any]:
    return {
        "device": host.device,
        "device_index": host.device_index if host.local else None,
        "compute_type": host.compute_type,
        "checks": [_public_check(check) for check in host.checks],
    }


# --- CTranslate2 in a fresh interpreter -------------------------------------------------


@dataclass(frozen=True)
class CTranslate2Facts:
    """What CTranslate2 reported in a child process, with the two calls the
    host checks make (``get_cuda_device_count``, ``get_supported_compute_types``)."""

    cuda_device_count: int
    cpu_compute_types: frozenset[str]
    cuda_compute_types: tuple[frozenset[str], ...]

    def get_cuda_device_count(self) -> int:
        return self.cuda_device_count

    def get_supported_compute_types(self, device: str, device_index: int = 0) -> set[str]:
        if device == "cpu":
            return set(self.cpu_compute_types)
        if device == "cuda" and 0 <= device_index < len(self.cuda_compute_types):
            return set(self.cuda_compute_types[device_index])
        raise ValueError("CTranslate2 reported no such device")


def _names(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _parse_facts(text: str) -> CTranslate2Facts | None:
    data = json.loads(text)
    if not isinstance(data, dict):
        return None
    count = data.get("cuda_device_count")
    cpu = data.get("cpu_compute_types")
    cuda = data.get("cuda_compute_types")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return None
    if not _names(cpu) or not isinstance(cuda, list) or len(cuda) != count:
        return None
    if not all(_names(types) for types in cuda):
        return None
    return CTranslate2Facts(
        cuda_device_count=count,
        cpu_compute_types=frozenset(cpu),
        cuda_compute_types=tuple(frozenset(types) for types in cuda),
    )


def subprocess_ctranslate2(
    run: Callable[..., subprocess.CompletedProcess] | None = None,
) -> CTranslate2Facts | None:
    """Ask CTranslate2 in a fresh interpreter; ``None`` on any failure.

    Never imports ``ctranslate2`` here: a CUDA context made in the worker
    parent before the fork is not usable in the children (ADR 0067 D5).
    ``run`` defaults to ``subprocess.run``, looked up at call time, so a
    patched ``subprocess.run`` reaches the child call.
    """
    run = run or subprocess.run
    try:
        completed = run(
            [sys.executable, "-c", _CTRANSLATE2_SCRIPT],
            capture_output=True,
            text=True,
            timeout=CTRANSLATE2_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 0:
            return None
        return _parse_facts((completed.stdout or "")[:_CHILD_OUTPUT_LIMIT])
    except Exception:  # missing module, timeout, bad JSON: all mean "not usable"
        return None


def default_probes() -> host_checks.HostProbes:
    """The real host probes, with CTranslate2 read in a child process."""
    return host_checks.HostProbes(ctranslate2=subprocess_ctranslate2)


# --- the task guard (consumer side) -----------------------------------------------------


def asr_delivery_decision(
    readiness: WorkerReadiness, delivery_queue: str | None, settings: Any
) -> str:
    """``"run"``, ``"reroute"`` or ``"requeue"`` for one meeting job delivery.

    A delivery from an ASR queue runs only when this worker holds that queue;
    otherwise it goes back to the broker for a qualified worker. Any other
    queue is a message published before HOM-42: it runs when this worker
    holds the job's queue, otherwise it is published again to that queue.
    """
    from backend.celery_app import ASR_QUEUES

    if delivery_queue in ASR_QUEUES:
        return "run" if delivery_queue in readiness.queues else "requeue"
    return "run" if asr_queue_for(settings) in readiness.queues else "reroute"


# --- operator view ----------------------------------------------------------------------


def workers_report(
    readiness_replies: Mapping[str, Any] | None,
    active_queue_replies: Mapping[str, Any] | None,
    queue_lengths: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The live workers and the declared queues, as one JSON document.

    ``ok`` is False when an ASR queue has waiting messages and no live
    consumer, or when a value is missing (a queue length, or a worker's
    readiness or active queues).
    """
    from backend.celery_app import ASR_QUEUES, DECLARED_QUEUES

    readiness_replies = readiness_replies or {}
    active_queue_replies = active_queue_replies or {}
    queue_lengths = queue_lengths or {}
    ok = True
    workers: list[dict[str, Any]] = []
    for hostname in sorted(set(readiness_replies) | set(active_queue_replies)):
        readiness = readiness_replies.get(hostname)
        active = active_queue_replies.get(hostname)
        if not isinstance(readiness, Mapping) or not isinstance(active, list):
            ok = False
        names = sorted(
            {
                queue["name"]
                for queue in (active if isinstance(active, list) else [])
                if isinstance(queue, Mapping) and isinstance(queue.get("name"), str)
            }
        )
        workers.append(
            {
                "hostname": hostname,
                "readiness": dict(readiness) if isinstance(readiness, Mapping) else None,
                "active_queues": names,
            }
        )
    queues: list[dict[str, Any]] = []
    for name in DECLARED_QUEUES:
        length = queue_lengths.get(name)
        consumers = sum(1 for worker in workers if name in worker["active_queues"])
        if not isinstance(length, int) or isinstance(length, bool):
            ok = False
        elif name in ASR_QUEUES and length > 0 and consumers == 0:
            ok = False
        queues.append({"name": name, "length": length, "consumers": consumers})
    return {"ok": ok, "workers": workers, "queues": queues}


def _queue_lengths_from_redis(settings: Any) -> dict[str, int]:
    """Messages waiting in each declared queue. Raises when Redis does not answer."""
    import redis

    from backend.celery_app import DECLARED_QUEUES

    client = redis.Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        socket_timeout=REDIS_TIMEOUT_SECONDS,
    )
    try:
        client.ping()
        return {name: int(client.llen(name)) for name in DECLARED_QUEUES}  # type: ignore[arg-type]
    finally:
        client.close()


def _inspect_worker_readiness(inspector: Any) -> Any:
    # Celery's Inspect has no method per custom command; _request sends it.
    method = getattr(inspector, "worker_readiness", None)
    if callable(method):
        return method()
    return inspector._request("worker_readiness")


# --- CLI ------------------------------------------------------------------------------


def _print(document: Mapping[str, Any]) -> None:
    print(json.dumps(document, indent=2, sort_keys=True))


def _celery_concurrency(environ: Mapping[str, str]) -> int:
    value = (environ.get("CELERY_CONCURRENCY") or "").strip()
    if not value:
        return 1
    concurrency = int(value)
    if concurrency < 1:
        raise ValueError("CELERY_CONCURRENCY must be 1 or more")
    return concurrency


def _check_command() -> int:
    try:
        settings = get_settings()
        concurrency = _celery_concurrency(os.environ)
    except Exception as exc:  # a settings ValidationError is a ValueError
        _print(
            {"qualified": False, "reason": "configuration_error", "error_type": type(exc).__name__}
        )
        return 2
    readiness = assess_worker_readiness(
        settings, pool="prefork", concurrency=concurrency, probes=default_probes()
    )
    _print(readiness.to_dict())
    if readiness.reason == "asr_adapter_failed":
        return 2
    return 0 if readiness.qualified else 1


def _workers_command() -> int:
    try:
        settings = get_settings()
    except Exception as exc:
        _print({"ok": False, "error": "configuration_error", "error_type": type(exc).__name__})
        return 2
    from backend.celery_app import celery_app

    try:
        lengths = _queue_lengths_from_redis(settings)
        inspector = celery_app.control.inspect(timeout=INSPECT_TIMEOUT_SECONDS)
        readiness_replies = _inspect_worker_readiness(inspector)
        active_queues = inspector.active_queues()
    except Exception as exc:
        _print({"ok": False, "error": "broker_unreachable", "error_type": type(exc).__name__})
        return 2
    report = workers_report(readiness_replies, active_queues, lengths)
    _print(report)
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.jobs.worker_readiness",
        description="Worker readiness and ASR queue routing (HOM-42).",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check", action="store_true", help="assess this container and print the record"
    )
    mode.add_argument(
        "--workers", action="store_true", help="ask the live workers and read the queue lengths"
    )
    args = parser.parse_args(argv)
    return _check_command() if args.check else _workers_command()


if __name__ == "__main__":
    sys.exit(main())
