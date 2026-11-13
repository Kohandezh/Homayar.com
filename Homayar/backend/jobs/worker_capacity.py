"""Memory-aware worker concurrency for local ASR (HOM-41, ADR 0067).

One prefork worker process that holds a warm ASR model costs the worker base
plus the resident size of that model. This module turns a memory budget into
the number of worker processes that fit, before the worker starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# One worker process without a model.
WORKER_BASE_MEMORY_MIB = 512
# Each batch slot above 1 (ASR_BATCH_SLOT_MEMORY_MIB).
ASR_BATCH_SLOT_MEMORY_MIB = 256

# Built-in estimate by whisper_model name, used when no asr_profile is set.
_MODEL_MEMORY_MIB: dict[str, int] = {
    "tiny": 384,
    "base": 512,
    "small": 1024,
    "medium": 2048,
    "large-v3-turbo": 2560,
    "large-v3": 3584,
}

# Pools that hold one model per worker, not one per process. The threads
# pool module is celery.concurrency.thread (singular).
_SINGLE_PROCESS_POOLS = frozenset({"solo", "threads", "thread", "gevent", "eventlet"})


def _provider_name(value: str) -> str:
    # The same rule as backend.providers.factory._provider_name.
    return value.strip().lower().replace("-", "_")


def is_local_whisper(settings: Any) -> bool:
    """Whether the ASR provider loads a local Whisper model (HOM-42 reuses it)."""
    return _provider_name(settings.asr_provider) == "local_whisper"


def model_memory_mib(model: str) -> int | None:
    """The built-in memory estimate of a Whisper model name, or ``None``."""
    return _MODEL_MEMORY_MIB.get(model)


def asr_memory_estimate_mib(settings: Any) -> int | None:
    """Resident ASR memory per worker process (MiB), or ``None`` when unknown.

    ``0`` when the provider holds no model. A set budget override wins, then
    the profile's ``memory_mib``, then the model-name table.
    """
    if _provider_name(settings.asr_provider) != "local_whisper":
        return 0
    model_mib: int | None = None
    if settings.asr_model_memory_mib > 0:
        model_mib = settings.asr_model_memory_mib
    elif settings.asr_profile:
        from backend.providers.asr_profiles import get_asr_profile

        model_mib = get_asr_profile(settings.asr_profile).memory_mib
    else:
        model_mib = _MODEL_MEMORY_MIB.get(settings.whisper_model)
    if model_mib is None:
        return None
    return model_mib + (settings.asr_batch_size - 1) * ASR_BATCH_SLOT_MEMORY_MIB


def worker_processes(pool: Any, concurrency: int) -> int:
    """Processes that each hold a model. ``pool`` may be a string or a class.
    An unknown pool counts as prefork."""
    if isinstance(pool, str):
        name, module = pool, ""
    else:
        name = getattr(pool, "__name__", "")
        module = getattr(pool, "__module__", "")
    module_name = module.rsplit(".", 1)[-1] if module else ""
    if name in _SINGLE_PROCESS_POOLS or module_name in _SINGLE_PROCESS_POOLS:
        return 1
    return concurrency


@dataclass(frozen=True)
class CapacityDecision:
    ok: bool
    reason: str
    processes: int
    allowed_processes: int | None = None
    per_process_mib: int | None = None
    required_mib: int | None = None
    budget_mib: int = 0


def check_worker_memory(settings: Any, *, pool: Any, concurrency: int) -> CapacityDecision:
    """Whether that many model-holding processes fit the budget."""
    budget = settings.worker_memory_budget_mib
    estimate = asr_memory_estimate_mib(settings)
    processes = worker_processes(pool, concurrency)
    if budget == 0:
        return CapacityDecision(ok=True, reason="no_budget", processes=processes, budget_mib=0)
    if estimate == 0:
        return CapacityDecision(
            ok=True, reason="no_asr_model", processes=processes, budget_mib=budget
        )
    if estimate is None:
        return CapacityDecision(
            ok=False, reason="unknown_model_memory", processes=processes, budget_mib=budget
        )
    per_process_mib = WORKER_BASE_MEMORY_MIB + estimate
    required_mib = processes * per_process_mib
    allowed_processes = budget // per_process_mib
    if required_mib <= budget:
        return CapacityDecision(
            ok=True,
            reason="within_budget",
            processes=processes,
            allowed_processes=allowed_processes,
            per_process_mib=per_process_mib,
            required_mib=required_mib,
            budget_mib=budget,
        )
    if allowed_processes >= 1:
        return CapacityDecision(
            ok=False,
            reason="over_budget",
            processes=processes,
            allowed_processes=allowed_processes,
            per_process_mib=per_process_mib,
            required_mib=required_mib,
            budget_mib=budget,
        )
    return CapacityDecision(
        ok=False,
        reason="budget_too_small",
        processes=processes,
        allowed_processes=allowed_processes,
        per_process_mib=per_process_mib,
        required_mib=required_mib,
        budget_mib=budget,
    )


class WorkerCapacityError(RuntimeError):
    """A capacity decision refused a worker configuration."""

    def __init__(self, message: str, decision: CapacityDecision) -> None:
        super().__init__(message)
        self.decision = decision
