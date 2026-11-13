"""HOM-42 oracle: worker readiness and routing ASR jobs to qualified workers (SPEC section 4)."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import logging
import os
import subprocess
import sys
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import redis
import yaml
from celery import _state as celery_state
from celery.concurrency.prefork import TaskPool as PreforkPool
from celery.concurrency.solo import TaskPool as SoloPool
from celery.contrib.testing.worker import start_worker
from celery.exceptions import Reject
from celery.signals import worker_init, worker_process_init, worker_ready
from celery.utils.text import str_to_list
from kombu import Queue

import backend.celery_app as celery_module
import backend.config as config_module
from backend.config import Settings
from backend.jobs import dispatcher
from backend.jobs.tasks import process_meeting_job
from backend.jobs.worker_capacity import check_worker_memory
from backend.observability import heartbeats, metrics
from backend.providers.asr_profiles import ASR_PROFILES, get_asr_profile
from backend.providers.host_checks import HostOptions, HostProbes, run_host_checks
from backend.providers.whisper import LocalWhisperProvider

WR_MODULE = "backend.jobs.worker_readiness"
GPU = "gpu-p40-large-v3"
CPU_PROFILES = ("cpu-base", "cpu-small", "cpu-medium", "cpu-large-v3-turbo", "cpu-large-v3")
PROCESSING = "processing"
ASR_CPU = "asr-cpu"
ASR_GPU = "asr-gpu"
ALL_QUEUES = {PROCESSING, ASR_CPU, ASR_GPU}
P40_UUID = "GPU-1234abcd-5678-90ef-aaaa-bbbbccccdddd"
UUID_FRAGMENT = "1234abcd"
PRIVATE_MODEL_PATH = "/srv/hom42-private-models/whisper-large"
PRIVATE_MESSAGE = "hom42-private-probe-detail"
ADAPTER_MESSAGE = "Whisper model, device, and compute type are required"
FIXED_NOW = datetime(2026, 11, 13, tzinfo=UTC)
READINESS_FIELDS = (
    "qualified",
    "reason",
    "requirement",
    "queues",
    "asr_profile",
    "device",
    "device_index",
    "compute_type",
    "processes",
    "vram_required_mib",
    "vram_free_mib",
    "vram_budget_mib",
    "vram_allowed_processes",
    "gpu_name",
    "ram_budget_mib",
    "ram_per_process_mib",
    "ram_decision",
    "error_type",
    "checks",
)
LOGGED_FIELDS = (
    "qualified",
    "reason",
    "requirement",
    "queues",
    "asr_profile",
    "device",
    "device_index",
    "compute_type",
    "processes",
    "vram_required_mib",
    "vram_free_mib",
    "vram_budget_mib",
    "vram_allowed_processes",
    "gpu_name",
    "ram_budget_mib",
    "ram_per_process_mib",
)
NEW_ENV = ("WORKER_ASR_QUEUES", "WORKER_VRAM_BUDGET_MIB", "ASR_MODEL_VRAM_MIB")
NEW_ENV_DEFAULTS = {
    "WORKER_ASR_QUEUES": "${WORKER_ASR_QUEUES:-auto}",
    "WORKER_VRAM_BUDGET_MIB": "${WORKER_VRAM_BUDGET_MIB:-0}",
    "ASR_MODEL_VRAM_MIB": "${ASR_MODEL_VRAM_MIB:-0}",
}
ISOLATED_ENV = (
    "ASR_PROVIDER",
    "ASR_PROFILE",
    "WHISPER_MODEL",
    "WHISPER_DEVICE",
    "WHISPER_COMPUTE_TYPE",
    "WHISPER_MODEL_PATH",
    "WHISPER_LOCAL_FILES_ONLY",
    "ASR_WARM_ON_START",
    "ASR_BATCH_SIZE",
    "WORKER_MEMORY_BUDGET_MIB",
    "ASR_MODEL_MEMORY_MIB",
    *NEW_ENV,
    "CUDA_VISIBLE_DEVICES",
    "CELERY_CONCURRENCY",
)
ORIGINAL_GET_SETTINGS = config_module.get_settings
REPO_ROOT = Path(__file__).resolve().parents[3]
HOMAYAR_DIR = REPO_ROOT / "Homayar"
COMPOSE_DIR = REPO_ROOT / "infra" / "compose"
BASE_WORKER_COMMAND = [
    "/bin/sh",
    "-ec",
    'exec celery -A backend.celery_app worker --queues processing --loglevel="$${LOG_LEVEL}"'
    ' --concurrency="$${CELERY_CONCURRENCY}" --hostname="celery@%h"',
]
COMPOSE_GOLDEN = {
    "compose.onprem.yaml": {
        "services": "95dbf6058abc6ee61c0de87617624f34af651c25371b10d59cf4b8ff76f9f44a",
        "worker_env": "8d4b71e15c593bdda6142191c67b267bd585abedf41bb188817f1b62f3ae5f6e",
        "rest": "0a015c8d4983d13c9339bf090fc7de9497865848679b8fb1de831c41eb52b1ff",
    },
    "compose.cloud.yaml": {
        "services": "1305912eefe9fc82741185428b98533c1edb12baff0c847b23e405013162e57c",
        "worker_env": "d036878bc0abf752fbc3ab54c4ef8588001040cd8d53466b58d7c68d26bdaca0",
        "rest": "b8d2fb1138c1a24b48043bb727958538970561afffffdb296206a5b10c3066e7",
    },
}
HOST_CHECKS_GOLDEN = {
    "gpu_pass": "42187ed715c6a658e8e9fd114cbfb52773a2efd51af850cd399e45808aaeae5d",
    "gpu_no_smi_offline": "ec13de8e7bbc359eb85817cf14c4ed21cad52e03f1fdd64174fd057645f2ae48",
}


class ProbeExplodedError(RuntimeError):
    pass


def _wr() -> Any:
    return importlib.import_module(WR_MODULE)


def _wr_or_none() -> Any | None:
    try:
        return importlib.import_module(WR_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != WR_MODULE:
            raise
        return None


def _clear_stored_readiness() -> None:
    module = _wr_or_none()
    kind = getattr(module, "WorkerReadiness", None) if module is not None else None
    if not isinstance(kind, type):
        return
    for name, loaded in list(sys.modules.items()):
        if loaded is None or not name.startswith("backend.") or name.startswith("backend.tests"):
            continue
        for attribute, value in list(vars(loaded).items()):
            if not attribute.isupper() and isinstance(value, kind):
                setattr(loaded, attribute, None)


@pytest.fixture(autouse=True)
def _isolated_worker_state(monkeypatch: pytest.MonkeyPatch):
    for name in ISOLATED_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(celery_module, "_worker_memory_decision", None, raising=False)
    previous_app = celery_state.get_current_app()
    _clear_stored_readiness()
    yield
    _clear_stored_readiness()
    previous_app.set_current()


class RecordingControl:
    def __init__(self, real: Any) -> None:
        self.real = real
        self.cancelled: list[tuple[str, Any]] = []

    def cancel_consumer(self, queue: str, **kwargs: Any) -> None:
        self.cancelled.append((queue, kwargs.get("destination")))

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real, name)


@pytest.fixture(autouse=True)
def _recording_control(monkeypatch: pytest.MonkeyPatch) -> RecordingControl:
    control = RecordingControl(celery_module.celery_app.control)
    monkeypatch.setattr(celery_module.celery_app, "control", control)
    return control


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "local_ai_base_url": "http://intelligence.test/v1",
        "llm_model": "conversation-model",
        "allow_mock_providers": True,
        "asr_provider": "local_whisper",
        "asr_profile": "",
        "whisper_model": "base",
        "whisper_device": "cpu",
        "whisper_compute_type": "int8",
        "whisper_model_path": "",
        "whisper_local_files_only": False,
        "asr_warm_on_start": False,
        "redis_url": "redis://127.0.0.1:1/0",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _gpu(**overrides: Any) -> Settings:
    return _settings(asr_profile=GPU, **overrides)


def _cpu(**overrides: Any) -> Settings:
    return _settings(asr_profile="cpu-base", **overrides)


CUDA_NO_PROFILE = {
    "whisper_device": "cuda",
    "whisper_model": "large-v3",
    "whisper_compute_type": "int8_float32",
}
UNKNOWN_CUDA_MODEL = {
    "whisper_device": "cuda",
    "whisper_model": "hom42-custom-model",
    "whisper_compute_type": "int8_float32",
}


class FakeCT2:
    def __init__(
        self,
        cuda_devices: int = 1,
        cpu: tuple[str, ...] = ("int8", "int8_float32", "float32"),
        cuda: tuple[str, ...] = ("int8_float32", "int8", "float32"),
    ) -> None:
        self.cuda_devices = cuda_devices
        self.cpu = cpu
        self.cuda = cuda

    def get_cuda_device_count(self) -> int:
        return self.cuda_devices

    def get_supported_compute_types(self, device: str, device_index: int = 0) -> set[str]:
        return set(self.cpu if device == "cpu" else self.cuda)


@dataclass
class FakeHost:
    free_mib: int | None = 20000
    driver: str = "535.104.05"
    smi: str = "ok"
    faster_whisper: bool = True
    ct2: FakeCT2 | None = field(default_factory=FakeCT2)
    environ: dict[str, str] = field(default_factory=dict)
    explode: str | None = None
    calls: list[str] = field(default_factory=list)

    def _probe(self, name: str) -> None:
        self.calls.append(name)
        if self.explode == name:
            raise ProbeExplodedError(PRIVATE_MESSAGE)

    def run(self, args: Any, **_kwargs: Any) -> subprocess.CompletedProcess:
        self._probe("nvidia_smi")
        if self.smi == "missing":
            raise FileNotFoundError(2, "No such file or directory", "nvidia-smi")
        if self.smi == "error":
            return subprocess.CompletedProcess(args, 9, stdout="", stderr="NVML failure")
        free = "[N/A]" if self.free_mib is None else str(self.free_mib)
        used = "[N/A]" if self.free_mib is None else str(24576 - self.free_mib)
        line = f"0, {P40_UUID}, Tesla P40, 24576, {used}, {free}, {self.driver}, 6.1\n"
        return subprocess.CompletedProcess(args, 0, stdout=line, stderr="")

    def find_spec(self, name: str) -> object | None:
        self._probe("find_spec")
        return object() if self.faster_whisper and name == "faster_whisper" else None

    def ctranslate2(self) -> object | None:
        self._probe("ctranslate2")
        return self.ct2

    def probes(self) -> HostProbes:
        def refuse(_host: str, _port: int, _timeout: float) -> None:
            raise ConnectionRefusedError("refused")

        return HostProbes(
            run=self.run,
            connect=refuse,
            disk_usage=lambda _path: SimpleNamespace(total=10**12, used=0, free=50 * 1024**3),
            find_spec=self.find_spec,
            ctranslate2=self.ctranslate2,
            environ=dict(self.environ),
            now=lambda: FIXED_NOW,
            read_stdin=lambda: b"",
            temp_dir=lambda: "/hom42-temp",
            cpu_count=lambda: 8,
            memory_total_mib=lambda: 65536,
        )


def _cpu_only_host() -> FakeHost:
    return FakeHost(smi="missing", ct2=FakeCT2(cuda_devices=0))


def _host(spec: dict[str, Any]) -> FakeHost:
    values = dict(spec)
    if "ct2" in values and isinstance(values["ct2"], dict):
        values["ct2"] = FakeCT2(**values["ct2"])
    return FakeHost(**values)


def _assess(
    settings: Settings,
    host: FakeHost | None = None,
    *,
    pool: Any = "prefork",
    concurrency: int = 1,
    bundle: Any = None,
) -> Any:
    host = host if host is not None else FakeHost()
    return _wr().assess_worker_readiness(
        settings, pool=pool, concurrency=concurrency, probes=host.probes(), bundle=bundle
    )


def _rendered(readiness: Any) -> str:
    return json.dumps(readiness.to_dict(), sort_keys=True)


def _homayar_receivers(signal: Any) -> list[Callable[..., Any]]:
    found = []
    for _key, reference in signal.receivers:
        receiver = reference() if isinstance(reference, weakref.ReferenceType) else reference
        module = str(getattr(receiver, "__module__", "") or "")
        if receiver is not None and module.startswith("backend."):
            found.append(receiver)
    return found


def _fire(signal: Any, sender: Any, **extra: Any) -> None:
    receivers = _homayar_receivers(signal)
    assert receivers, f"no Homayar receiver is connected to {signal.name}"
    for receiver in receivers:
        receiver(signal=signal, sender=sender, **extra)


class _SettingsReplacement:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def __call__(self) -> Settings:
        return self.settings


def _use_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    _wr_or_none()
    for name in (
        "backend.jobs.tasks",
        "backend.jobs.dispatcher",
        "backend.jobs.worker_capacity",
        "backend.providers.warm_models",
    ):
        importlib.import_module(name)

    replacement = _SettingsReplacement(settings)
    for name, module in list(sys.modules.items()):
        if module is None or not (name == "backend" or name.startswith("backend.")):
            continue
        current = getattr(module, "get_settings", None)
        if current is ORIGINAL_GET_SETTINGS or isinstance(current, _SettingsReplacement):
            monkeypatch.setattr(module, "get_settings", replacement)


def _use_probes(monkeypatch: pytest.MonkeyPatch, host: FakeHost) -> None:
    module = _wr_or_none()
    if module is not None:
        monkeypatch.setattr(module, "default_probes", lambda: host.probes())


def _start(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    host: FakeHost,
    *,
    queues: str | list[str] | None = PROCESSING,
    concurrency: int = 1,
    pool: Any = "prefork",
) -> SimpleNamespace:
    _use_settings(monkeypatch, settings)
    _use_probes(monkeypatch, host)
    app = celery_module.make_celery()
    app.amqp.queues.select(str_to_list(queues))
    sender = SimpleNamespace(
        app=app,
        hostname="celery@hom42-oracle",
        concurrency=concurrency,
        pool_cls=pool,
        autoscale=None,
        options={},
    )
    _fire(worker_init, sender)
    return sender


def _consumed(sender: SimpleNamespace) -> set[str]:
    return set(sender.app.amqp.queues.consume_from)


def _event_records(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    found = []
    for record in caplog.records:
        words = record.getMessage().split()
        if (words and words[0] == event) or getattr(record, "event", None) == event:
            found.append(record)
    return found


def _record_text(record: logging.LogRecord) -> str:
    skipped = {"pathname", "filename", "module", "funcName", "processName", "threadName"}
    attributes = {key: value for key, value in vars(record).items() if key not in skipped}
    return record.getMessage() + "\n" + repr(attributes)


def _everything_logged(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join([caplog.text, *(_record_text(record) for record in caplog.records)])


@dataclass
class TaskSpies:
    verified: list[tuple[str, str | None]] = field(default_factory=list)
    processed: list[str] = field(default_factory=list)
    published: list[dict[str, Any]] = field(default_factory=list)


@pytest.fixture
def task_spies(monkeypatch: pytest.MonkeyPatch) -> TaskSpies:
    spies = TaskSpies()

    def verify(job_id: str, workspace_id: str | None = None) -> bool:
        spies.verified.append((job_id, workspace_id))
        return True

    def process(job_id: str, **_kwargs: Any) -> dict[str, str]:
        spies.processed.append(job_id)
        return {"job_id": job_id, "status": "completed"}

    def publish(*args: Any, **options: Any) -> SimpleNamespace:
        if args:
            options.setdefault("args", args[0])
        if len(args) > 1:
            options.setdefault("kwargs", args[1])
        spies.published.append(options)
        return SimpleNamespace(id="message-hom42")

    monkeypatch.setattr("backend.jobs.outbox.verify_job_payload", verify)
    monkeypatch.setattr("backend.jobs.processor.process_job", process)
    monkeypatch.setattr(process_meeting_job, "apply_async", publish)
    return spies


def _publication(options: dict[str, Any]) -> dict[str, Any]:
    queue = options.get("queue")
    return {
        "args": list(options.get("args") or []),
        "kwargs": dict(options.get("kwargs") or {}),
        "queue": getattr(queue, "name", queue),
    }


def _deliver(
    delivery_info: dict[str, Any] | None,
    job_id: str = "job-hom42",
    workspace_id: str | None = "ws-hom42",
) -> Any:
    process_meeting_job.push_request(
        id="delivery-hom42-0001",
        delivery_info=delivery_info,
        is_eager=False,
        called_directly=False,
    )
    try:
        return process_meeting_job(job_id, workspace_id=workspace_id)
    finally:
        process_meeting_job.pop_request()


@pytest.mark.parametrize(
    ("settings_factory", "routed"),
    [
        (lambda: _gpu(), ASR_GPU),
        (lambda: _cpu(), ASR_CPU),
        (lambda: _settings(), ASR_CPU),
        (lambda: _settings(asr_provider="mock"), ASR_CPU),
    ],
    ids=["gpu-profile", "cpu-profile", "no-profile-cpu", "remote-provider"],
)
def test_a1_declared_queues_and_the_process_meeting_route(
    monkeypatch: pytest.MonkeyPatch, settings_factory: Callable[[], Settings], routed: str
) -> None:
    _use_settings(monkeypatch, settings_factory())
    app = celery_module.make_celery()

    assert {queue.name for queue in app.conf.task_queues} == ALL_QUEUES
    assert len(app.conf.task_queues) == 3
    assert app.conf.task_default_queue == PROCESSING
    assert app.amqp.router.route({}, celery_module.PROCESS_MEETING_TASK)["queue"].name == routed
    assert celery_module.ASR_CPU_QUEUE == ASR_CPU
    assert celery_module.ASR_GPU_QUEUE == ASR_GPU
    assert tuple(celery_module.ASR_QUEUES) == (ASR_GPU, ASR_CPU)


def test_a1_control_other_routes_and_beat_entries_stay_on_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_settings(monkeypatch, _gpu())
    app = celery_module.make_celery()
    other_tasks = [
        celery_module.DISPATCH_SWEEP_TASK,
        celery_module.LEASE_RECOVERY_TASK,
        celery_module.USAGE_RECONCILIATION_TASK,
        celery_module.ARTIFACT_CLEANUP_TASK,
        celery_module.MEDIA_RECONCILIATION_TASK,
        celery_module.VOICE_RECOVERY_TASK,
        celery_module.RECOMPUTE_ANALYSIS_TASK,
        celery_module.RECOMPUTE_SWEEP_TASK,
        celery_module.DELIVER_WEBHOOK_TASK,
        celery_module.WEBHOOK_SWEEP_TASK,
        celery_module.COACHING_REMINDER_TASK,
        celery_module.RETENTION_PURGE_TASK,
        celery_module.CRM_RECONCILIATION_TASK,
        celery_module.FEDERATION_REVALIDATION_TASK,
    ]

    assert app.conf.task_default_queue == PROCESSING
    for task in other_tasks:
        assert app.amqp.router.route({}, task)["queue"].name == PROCESSING, task
    for name, entry in app.conf.beat_schedule.items():
        assert entry["options"]["queue"] == PROCESSING, name
    for task, route in app.conf.task_routes.items():
        if task != celery_module.PROCESS_MEETING_TASK:
            assert route == {"queue": PROCESSING}, task


@pytest.mark.parametrize(
    ("overrides", "requirement", "queue"),
    [
        ({"asr_profile": GPU}, "gpu", ASR_GPU),
        *[({"asr_profile": name}, "cpu", ASR_CPU) for name in CPU_PROFILES],
        ({"whisper_device": "cuda"}, "gpu", ASR_GPU),
        ({"whisper_device": "cuda:1"}, "gpu", ASR_GPU),
        ({"whisper_device": "cuda:0"}, "gpu", ASR_GPU),
        ({"whisper_device": "cpu"}, "cpu", ASR_CPU),
        ({"asr_profile": "cpu-base", "whisper_device": "cuda"}, "cpu", ASR_CPU),
        ({"asr_profile": GPU, "whisper_device": "cpu"}, "gpu", ASR_GPU),
        ({"asr_provider": "mock"}, "remote", ASR_CPU),
        ({"asr_provider": "mock", "whisper_device": "cuda"}, "remote", ASR_CPU),
        ({"asr_provider": " Local-Whisper ", "whisper_device": "cuda"}, "gpu", ASR_GPU),
        ({"asr_provider": "LOCAL_WHISPER"}, "cpu", ASR_CPU),
    ],
    ids=[
        "gpu-profile",
        *CPU_PROFILES,
        "no-profile-cuda",
        "no-profile-cuda-1",
        "no-profile-cuda-0",
        "no-profile-cpu",
        "cpu-profile-wins-over-whisper-device",
        "gpu-profile-wins-over-whisper-device",
        "remote-provider",
        "remote-provider-ignores-device",
        "provider-name-normalised-gpu",
        "provider-name-normalised-cpu",
    ],
)
def test_a2_requirement_and_queue(overrides: dict[str, Any], requirement: str, queue: str) -> None:
    wr = _wr()
    settings = _settings(**overrides)

    assert wr.asr_requirement(settings) == requirement
    assert wr.asr_queue_for(settings) == queue


def test_a2_every_cpu_profile_is_listed() -> None:
    assert {name for name in ASR_PROFILES if name.startswith("cpu-")} == set(CPU_PROFILES)
    assert {name for name, profile in ASR_PROFILES.items() if profile.device == "cuda"} == {GPU}


@pytest.mark.parametrize(
    ("published_with", "ambient", "queue"),
    [
        (lambda: _gpu(), lambda: _cpu(), ASR_GPU),
        (lambda: _cpu(), lambda: _gpu(), ASR_CPU),
        (lambda: _settings(), lambda: _gpu(), ASR_CPU),
        (lambda: _settings(asr_provider="mock"), lambda: _gpu(), ASR_CPU),
    ],
    ids=["gpu-profile", "cpu-profile", "no-profile-cpu", "remote-provider"],
)
def test_a3_publish_goes_to_the_asr_queue_with_unchanged_args(
    monkeypatch: pytest.MonkeyPatch,
    task_spies: TaskSpies,
    published_with: Callable[[], Settings],
    ambient: Callable[[], Settings],
    queue: str,
) -> None:
    _use_settings(monkeypatch, ambient())
    settings = published_with()

    message_id = dispatcher.publish_processing_job("job-1", settings, workspace_id="ws-1")
    dispatcher.publish_processing_job("job-2", settings)

    assert message_id == "message-hom42"
    assert [_publication(options) for options in task_spies.published] == [
        {"args": ["job-1"], "kwargs": {"workspace_id": "ws-1"}, "queue": queue},
        {"args": ["job-2"], "kwargs": {}, "queue": queue},
    ]


def test_a3_enqueue_goes_to_the_asr_queue(
    monkeypatch: pytest.MonkeyPatch, task_spies: TaskSpies
) -> None:
    _use_settings(monkeypatch, _cpu())

    assert dispatcher.enqueue_processing_job("job-3", _gpu()) is True
    assert [_publication(options) for options in task_spies.published] == [
        {"args": ["job-3"], "kwargs": {}, "queue": ASR_GPU}
    ]


TABLE = [
    (
        "off-refused",
        {"asr_profile": GPU, "worker_asr_queues": "OFF"},
        {},
        False,
        "asr_queues_off",
        (),
    ),
    (
        "off-control-auto",
        {"asr_profile": GPU, "worker_asr_queues": "auto"},
        {},
        True,
        "ready",
        (ASR_GPU, ASR_CPU),
    ),
    (
        "off-before-remote",
        {"asr_provider": "mock", "worker_asr_queues": "off"},
        {},
        False,
        "asr_queues_off",
        (),
    ),
    ("remote-provider", {"asr_provider": "mock"}, {}, True, "no_local_model", (ASR_CPU,)),
    (
        "remote-provider-spelling",
        {"asr_provider": " Mock "},
        {},
        True,
        "no_local_model",
        (ASR_CPU,),
    ),
    (
        "cpu-runtime-no-faster-whisper",
        {"asr_profile": "cpu-base"},
        {"faster_whisper": False},
        False,
        "asr_runtime_failed",
        (),
    ),
    (
        "cpu-runtime-no-ctranslate2",
        {"asr_profile": "cpu-base"},
        {"ct2": None},
        False,
        "asr_runtime_failed",
        (),
    ),
    (
        "cpu-runtime-unsupported-compute",
        {"asr_profile": "cpu-base"},
        {"ct2": {"cpu": ("float32",)}},
        False,
        "asr_runtime_failed",
        (),
    ),
    ("cpu-runtime-pass", {"asr_profile": "cpu-base"}, {}, True, "ready", (ASR_CPU,)),
    (
        "cpu-runtime-warn",
        {"asr_profile": "cpu-base", "whisper_local_files_only": True},
        {},
        True,
        "ready",
        (ASR_CPU,),
    ),
    ("cpu-no-profile-pass", {}, {}, True, "ready", (ASR_CPU,)),
    (
        "cpu-ignores-missing-gpu",
        {"asr_profile": "cpu-base"},
        {"smi": "missing", "ct2": {"cuda_devices": 0}},
        True,
        "ready",
        (ASR_CPU,),
    ),
    (
        "gpu-runtime-no-ctranslate2",
        {"asr_profile": GPU},
        {"ct2": None},
        False,
        "asr_runtime_failed",
        (),
    ),
    (
        "gpu-runtime-no-cuda-device",
        {"asr_profile": GPU},
        {"ct2": {"cuda_devices": 0}},
        False,
        "asr_runtime_failed",
        (),
    ),
    (
        "gpu-runtime-no-faster-whisper",
        {"asr_profile": GPU},
        {"faster_whisper": False},
        False,
        "asr_runtime_failed",
        (),
    ),
    (
        "gpu-runtime-warn",
        {"asr_profile": GPU, "whisper_local_files_only": True},
        {},
        True,
        "ready",
        (ASR_GPU, ASR_CPU),
    ),
    (
        "gpu-runtime-before-driver",
        {"asr_profile": GPU},
        {"ct2": None, "smi": "missing"},
        False,
        "asr_runtime_failed",
        (),
    ),
    (
        "gpu-driver-no-nvidia-smi",
        {"asr_profile": GPU},
        {"smi": "missing"},
        False,
        "gpu_driver_failed",
        (),
    ),
    (
        "gpu-driver-nvidia-smi-error",
        {"asr_profile": GPU},
        {"smi": "error"},
        False,
        "gpu_driver_failed",
        (),
    ),
    (
        "gpu-driver-too-old",
        {"asr_profile": GPU},
        {"driver": "470.57.02"},
        False,
        "gpu_driver_failed",
        (),
    ),
    (
        "gpu-driver-at-minimum",
        {"asr_profile": GPU},
        {"driver": "525.60.13"},
        True,
        "ready",
        (ASR_GPU, ASR_CPU),
    ),
    (
        "gpu-driver-before-unknown-vram",
        UNKNOWN_CUDA_MODEL,
        {"smi": "missing"},
        False,
        "gpu_driver_failed",
        (),
    ),
    ("gpu-unknown-model-vram", UNKNOWN_CUDA_MODEL, {}, False, "unknown_model_vram", ()),
    (
        "gpu-unknown-model-with-override",
        {**UNKNOWN_CUDA_MODEL, "asr_model_vram_mib": 3000},
        {},
        True,
        "ready",
        (ASR_GPU, ASR_CPU),
    ),
    (
        "gpu-unknown-vram-before-budget",
        {**UNKNOWN_CUDA_MODEL, "worker_vram_budget_mib": 1},
        {"free_mib": 1},
        False,
        "unknown_model_vram",
        (),
    ),
    ("gpu-no-profile-known-model", CUDA_NO_PROFILE, {}, True, "ready", (ASR_GPU, ASR_CPU)),
    (
        "gpu-no-profile-cuda-0",
        {**CUDA_NO_PROFILE, "whisper_device": "cuda:0"},
        {},
        True,
        "ready",
        (ASR_GPU, ASR_CPU),
    ),
    (
        "gpu-budget-one-below",
        {"asr_profile": GPU, "worker_vram_budget_mib": 3583},
        {},
        False,
        "vram_over_budget",
        (),
    ),
    (
        "gpu-budget-equal",
        {"asr_profile": GPU, "worker_vram_budget_mib": 3584},
        {},
        True,
        "ready",
        (ASR_GPU, ASR_CPU),
    ),
    (
        "gpu-budget-before-free",
        {"asr_profile": GPU, "worker_vram_budget_mib": 3583},
        {"free_mib": 100},
        False,
        "vram_over_budget",
        (),
    ),
    (
        "gpu-free-one-below",
        {"asr_profile": GPU},
        {"free_mib": 3583},
        False,
        "gpu_memory_failed",
        (),
    ),
    ("gpu-free-equal", {"asr_profile": GPU}, {"free_mib": 3584}, True, "ready", (ASR_GPU, ASR_CPU)),
    ("gpu-free-unknown", {"asr_profile": GPU}, {"free_mib": None}, False, "gpu_memory_failed", ()),
    (
        "gpu-visible-devices-missing-gpu",
        {"asr_profile": GPU},
        {"environ": {"CUDA_VISIBLE_DEVICES": "1"}},
        False,
        "gpu_memory_failed",
        (),
    ),
    (
        "gpu-visible-devices-unknown-uuid",
        {"asr_profile": GPU},
        {"environ": {"CUDA_VISIBLE_DEVICES": "GPU-ffff0000"}},
        False,
        "gpu_memory_failed",
        (),
    ),
    (
        "gpu-visible-devices-index",
        {"asr_profile": GPU},
        {"environ": {"CUDA_VISIBLE_DEVICES": "0"}},
        True,
        "ready",
        (ASR_GPU, ASR_CPU),
    ),
    (
        "gpu-visible-devices-uuid-prefix",
        {"asr_profile": GPU},
        {"environ": {"CUDA_VISIBLE_DEVICES": "GPU-1234abcd"}},
        True,
        "ready",
        (ASR_GPU, ASR_CPU),
    ),
    ("gpu-all-pass", {"asr_profile": GPU}, {}, True, "ready", (ASR_GPU, ASR_CPU)),
]


@pytest.mark.parametrize(
    ("overrides", "host_spec", "qualified", "reason", "queues"),
    [pytest.param(*row[1:], id=row[0]) for row in TABLE],
)
def test_a4_readiness_follows_the_table(
    overrides: dict[str, Any],
    host_spec: dict[str, Any],
    qualified: bool,
    reason: str,
    queues: tuple[str, ...],
) -> None:
    readiness = _assess(_settings(**overrides), _host(host_spec))

    assert (readiness.qualified, readiness.reason, tuple(readiness.queues)) == (
        qualified,
        reason,
        queues,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"asr_profile": GPU, "worker_asr_queues": "off"},
        {"asr_profile": GPU, "worker_asr_queues": " Off "},
        {"asr_provider": "mock"},
    ],
    ids=["off", "off-spelling", "remote"],
)
def test_a4_no_probe_runs_when_off_or_remote(overrides: dict[str, Any]) -> None:
    host = FakeHost(explode="nvidia_smi")

    readiness = _assess(_settings(**overrides), host)

    assert host.calls == []
    assert readiness.reason in {"asr_queues_off", "no_local_model"}


def test_a4_control_probes_run_for_a_local_gpu_model() -> None:
    host = FakeHost()

    readiness = _assess(_gpu(), host)

    assert readiness.reason == "ready"
    assert "nvidia_smi" in host.calls
    assert "ctranslate2" in host.calls


@pytest.mark.parametrize(
    ("overrides", "requirement"),
    [
        ({"whisper_compute_type": " "}, "cpu"),
        ({**CUDA_NO_PROFILE, "whisper_compute_type": " "}, "gpu"),
    ],
    ids=["cpu", "gpu"],
)
def test_a4_adapter_build_error_is_not_qualified(
    overrides: dict[str, Any], requirement: str
) -> None:
    readiness = _assess(_settings(**overrides))

    assert readiness.qualified is False
    assert readiness.reason == "asr_adapter_failed"
    assert tuple(readiness.queues) == ()
    assert ADAPTER_MESSAGE not in _rendered(readiness)


@pytest.mark.parametrize(
    "overrides", [{"whisper_compute_type": "int8"}, CUDA_NO_PROFILE], ids=["cpu", "gpu"]
)
def test_a4_adapter_build_control(overrides: dict[str, Any]) -> None:
    readiness = _assess(_settings(**overrides))

    assert readiness.qualified is True
    assert readiness.reason == "ready"


def test_a4_an_explicit_bundle_is_used() -> None:
    bundle = SimpleNamespace(
        asr=LocalWhisperProvider(model_name="base", device="cpu", compute_type="float16")
    )

    with_bundle = _assess(_cpu(), FakeHost(ct2=FakeCT2(cpu=("int8", "float32"))), bundle=bundle)
    without_bundle = _assess(_cpu(), FakeHost(ct2=FakeCT2(cpu=("int8", "float32"))))

    assert with_bundle.qualified is False
    assert with_bundle.reason == "asr_runtime_failed"
    assert without_bundle.qualified is True
    assert without_bundle.reason == "ready"


def test_a4_gpu_record_fields() -> None:
    readiness = _assess(_gpu(), FakeHost(free_mib=20000))

    assert readiness.requirement == "gpu"
    assert readiness.asr_profile == GPU
    assert readiness.device == "cuda"
    assert readiness.device_index == 0
    assert readiness.compute_type == "int8_float32"
    assert readiness.processes == 1
    assert readiness.vram_required_mib == 3584
    assert readiness.vram_free_mib == 20000
    assert readiness.gpu_name == "Tesla P40"
    assert readiness.error_type is None
    assert isinstance(readiness.checks, list)
    assert all(isinstance(check, dict) for check in readiness.checks)


@pytest.mark.parametrize(
    ("overrides", "profile"), [({"asr_profile": "cpu-base"}, "cpu-base"), ({}, None)]
)
def test_a4_cpu_record_fields(overrides: dict[str, Any], profile: str | None) -> None:
    readiness = _assess(_settings(**overrides))

    assert readiness.requirement == "cpu"
    assert readiness.asr_profile == profile
    assert readiness.device == "cpu"
    assert readiness.compute_type == "int8"
    assert readiness.vram_required_mib is None
    assert readiness.vram_free_mib is None
    assert readiness.gpu_name is None
    assert readiness.error_type is None


def test_a4_remote_record_fields() -> None:
    readiness = _assess(_settings(asr_provider="mock"), pool="prefork", concurrency=3)

    assert readiness.requirement == "remote"
    assert readiness.processes == 3
    assert readiness.vram_required_mib is None
    assert readiness.gpu_name is None


def test_a4_record_is_a_frozen_dataclass_with_a_json_dict() -> None:
    readiness = _assess(_gpu())
    as_dict = readiness.to_dict()

    assert dataclasses.is_dataclass(readiness)
    with pytest.raises(dataclasses.FrozenInstanceError):
        readiness.qualified = False
    assert isinstance(readiness.queues, tuple)
    assert set(READINESS_FIELDS) <= set(as_dict)
    assert json.loads(json.dumps(as_dict))["reason"] == "ready"
    assert list(json.loads(json.dumps(as_dict))["queues"]) == [ASR_GPU, ASR_CPU]


def test_a5_profile_vram() -> None:
    assert get_asr_profile(GPU).vram_mib == 3584
    for name in CPU_PROFILES:
        assert get_asr_profile(name).vram_mib == 0, name


@pytest.mark.parametrize(
    ("overrides", "pool", "concurrency", "processes", "required"),
    [
        ({"asr_profile": GPU}, "prefork", 1, 1, 3584),
        ({"asr_profile": GPU, "asr_model_vram_mib": 5000}, "prefork", 1, 1, 5000),
        ({"asr_profile": GPU, "asr_batch_size": 3}, "prefork", 1, 1, 4096),
        (
            {"asr_profile": GPU, "asr_model_vram_mib": 5000, "asr_batch_size": 2},
            "prefork",
            1,
            1,
            5256,
        ),
        ({"asr_profile": GPU}, "prefork", 2, 2, 7168),
        ({"asr_profile": GPU}, PreforkPool, 3, 3, 10752),
        ({"asr_profile": GPU}, "solo", 4, 1, 3584),
        ({"asr_profile": GPU}, SoloPool, 4, 1, 3584),
        ({"asr_profile": GPU}, "threads", 4, 1, 3584),
        (CUDA_NO_PROFILE, "prefork", 1, 1, 3584),
        ({**CUDA_NO_PROFILE, "whisper_model": "medium"}, "prefork", 1, 1, 2048),
        ({**CUDA_NO_PROFILE, "asr_model_vram_mib": 1000}, "prefork", 1, 1, 1000),
        (
            {**UNKNOWN_CUDA_MODEL, "asr_model_vram_mib": 1000, "asr_batch_size": 2},
            "prefork",
            2,
            2,
            2512,
        ),
    ],
    ids=[
        "profile",
        "override-wins",
        "batch-slots",
        "override-plus-batch",
        "prefork-2",
        "prefork-class-3",
        "solo-4",
        "solo-class-4",
        "threads-4",
        "model-table-large-v3",
        "model-table-medium",
        "override-over-table",
        "override-unknown-model",
    ],
)
def test_a5_vram_need(
    overrides: dict[str, Any], pool: Any, concurrency: int, processes: int, required: int
) -> None:
    readiness = _assess(_settings(**overrides), pool=pool, concurrency=concurrency)

    assert readiness.processes == processes
    assert readiness.vram_required_mib == required
    assert readiness.qualified is True
    assert readiness.reason == "ready"


def test_a5_unknown_model_has_no_vram_number() -> None:
    readiness = _assess(_settings(**UNKNOWN_CUDA_MODEL))

    assert readiness.reason == "unknown_model_vram"
    assert readiness.vram_required_mib is None


@pytest.mark.parametrize(
    ("budget", "free", "concurrency", "reason", "allowed"),
    [
        (0, 8000, 3, "gpu_memory_failed", 2),
        (4000, 20000, 2, "vram_over_budget", 1),
        (10000, 5000, 2, "gpu_memory_failed", 1),
        (3000, 20000, 1, "vram_over_budget", 0),
    ],
    ids=["free-limits", "budget-limits", "free-below-budget", "budget-below-one"],
)
def test_a5_no_vram_clamp_but_allowed_processes_in_the_record(
    budget: int, free: int, concurrency: int, reason: str, allowed: int
) -> None:
    readiness = _assess(
        _gpu(worker_vram_budget_mib=budget), FakeHost(free_mib=free), concurrency=concurrency
    )

    assert readiness.qualified is False
    assert readiness.reason == reason
    assert readiness.processes == concurrency
    assert readiness.vram_required_mib == 3584 * concurrency
    assert readiness.vram_allowed_processes == allowed
    assert readiness.vram_free_mib == free
    if budget:
        assert readiness.vram_budget_mib == budget


@pytest.mark.parametrize("budget", [0, 5120, 1000], ids=["no-budget", "fits", "too-small"])
def test_a5_ram_decision_is_copied_and_never_changes_qualified(budget: int) -> None:
    settings = _gpu(worker_memory_budget_mib=budget)
    decision = check_worker_memory(settings, pool="prefork", concurrency=1)

    readiness = _assess(settings)

    assert readiness.qualified is True
    assert readiness.ram_decision == decision.reason
    assert readiness.ram_per_process_mib == decision.per_process_mib
    if decision.budget_mib:
        assert readiness.ram_budget_mib == decision.budget_mib
    else:
        assert readiness.ram_budget_mib in (0, None)


@pytest.mark.parametrize(
    ("settings_factory", "probe"),
    [
        (lambda: _gpu(), "nvidia_smi"),
        (lambda: _gpu(), "ctranslate2"),
        (lambda: _gpu(), "find_spec"),
        (lambda: _cpu(), "ctranslate2"),
    ],
    ids=["gpu-nvidia-smi", "gpu-ctranslate2", "gpu-find-spec", "cpu-ctranslate2"],
)
def test_a6_probe_exception_gives_readiness_error(
    settings_factory: Callable[[], Settings], probe: str
) -> None:
    readiness = _assess(settings_factory(), FakeHost(explode=probe))

    assert readiness.qualified is False
    assert readiness.reason == "readiness_error"
    assert readiness.error_type == "ProbeExplodedError"
    assert tuple(readiness.queues) == ()
    assert PRIVATE_MESSAGE not in _rendered(readiness)


FAKE_CTRANSLATE2 = """
def get_cuda_device_count():
    return 2


def get_supported_compute_types(device, device_index=0):
    if device == "cpu":
        return {"int8", "int8_float32", "float32"}
    return [{"int8_float32", "float32"}, {"int8", "float32", "float16"}][device_index]
"""


def _patch_runner(
    monkeypatch: pytest.MonkeyPatch, runner: Callable[..., Any]
) -> list[tuple[Any, dict]]:
    calls: list[tuple[Any, dict]] = []
    original = subprocess.run

    def spy(args: Any, *rest: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return runner(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    module = _wr()
    for name, value in list(vars(module).items()):
        if value is original:
            monkeypatch.setattr(module, name, spy)
    return calls


def _real_child_with_fake_ctranslate2(tmp_path: Path) -> Callable[..., Any]:
    original = subprocess.run
    package_dir = tmp_path / "fake-site"
    package_dir.mkdir()
    (package_dir / "ctranslate2.py").write_text(FAKE_CTRANSLATE2, encoding="utf-8")

    def runner(args: Any, *rest: Any, **kwargs: Any) -> Any:
        env = dict(kwargs.get("env") or os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(package_dir), env.get("PYTHONPATH", "")) if part
        )
        return original(args, *rest, **{**kwargs, "env": env})

    return runner


def test_a7_child_reports_devices_and_parent_never_imports_ctranslate2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wr = _wr()
    monkeypatch.delitem(sys.modules, "ctranslate2", raising=False)
    calls = _patch_runner(monkeypatch, _real_child_with_fake_ctranslate2(tmp_path))

    ct2 = wr.subprocess_ctranslate2()

    assert "ctranslate2" not in sys.modules
    assert ct2 is not None
    assert ct2.get_cuda_device_count() == 2
    assert set(ct2.get_supported_compute_types("cpu")) == {"int8", "int8_float32", "float32"}
    assert set(ct2.get_supported_compute_types("cuda", device_index=0)) == {
        "int8_float32",
        "float32",
    }
    assert set(ct2.get_supported_compute_types("cuda", device_index=1)) == {
        "int8",
        "float32",
        "float16",
    }
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert isinstance(args, list | tuple)
    assert list(args[:2]) == [sys.executable, "-c"]
    assert len(args) == 3
    assert not kwargs.get("shell")
    assert kwargs.get("timeout") == 60


def test_a7_default_probes_use_the_child_and_feed_the_gpu_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wr = _wr()
    monkeypatch.delitem(sys.modules, "ctranslate2", raising=False)
    calls = _patch_runner(monkeypatch, _real_child_with_fake_ctranslate2(tmp_path))
    fake = FakeHost().probes()

    probes = wr.default_probes()
    assert isinstance(probes, HostProbes)
    combined = dataclasses.replace(fake, ctranslate2=probes.ctranslate2)
    readiness = wr.assess_worker_readiness(_gpu(), pool="prefork", concurrency=1, probes=combined)

    assert "ctranslate2" not in sys.modules
    assert any(list(args[:2]) == [sys.executable, "-c"] for args, _ in calls)
    assert readiness.qualified is True
    assert readiness.reason == "ready"


def _completed(stdout: str, code: int = 0) -> Callable[..., Any]:
    def runner(args: Any, *_rest: Any, **_kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args, code, stdout=stdout, stderr="Traceback")

    return runner


def _raises(error: BaseException) -> Callable[..., Any]:
    def runner(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    return runner


@pytest.mark.parametrize(
    "runner",
    [
        _completed("", 1),
        _completed("ModuleNotFoundError: No module named 'ctranslate2'", 1),
        _raises(subprocess.TimeoutExpired(cmd="python", timeout=60)),
        _completed("this is not json"),
        _completed('{"cuda'),
        _completed(""),
        _raises(FileNotFoundError(2, "No such file or directory")),
        _raises(OSError(8, "Exec format error")),
    ],
    ids=[
        "non-zero-exit",
        "missing-module",
        "timeout",
        "garbled",
        "truncated-json",
        "empty-output",
        "no-interpreter",
        "os-error",
    ],
)
def test_a7_failing_child_gives_none(
    monkeypatch: pytest.MonkeyPatch, runner: Callable[..., Any]
) -> None:
    wr = _wr()
    monkeypatch.delitem(sys.modules, "ctranslate2", raising=False)
    calls = _patch_runner(monkeypatch, runner)

    assert wr.subprocess_ctranslate2() is None
    assert wr.default_probes().ctranslate2() is None
    assert "ctranslate2" not in sys.modules
    assert calls


def test_a8_gpu_qualified_worker_takes_both_asr_queues(monkeypatch: pytest.MonkeyPatch) -> None:
    _wr()
    sender = _start(monkeypatch, _gpu(), FakeHost(), queues=PROCESSING)

    assert _consumed(sender) == ALL_QUEUES
    assert tuple(celery_module.current_worker_readiness().queues) == (ASR_GPU, ASR_CPU)


def test_a8_cpu_only_worker_with_gpu_requirement_takes_no_asr_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wr()
    sender = _start(monkeypatch, _gpu(), _cpu_only_host(), queues=PROCESSING)

    assert _consumed(sender) == {PROCESSING}
    assert celery_module.current_worker_readiness().qualified is False


def test_a8_cpu_qualified_worker_takes_asr_cpu_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _wr()
    sender = _start(monkeypatch, _cpu(), FakeHost(), queues=PROCESSING)

    assert _consumed(sender) == {PROCESSING, ASR_CPU}


@pytest.mark.parametrize(
    ("settings_factory", "host_factory", "queues", "consumed", "refused", "reason"),
    [
        (_gpu, _cpu_only_host, ASR_GPU, {PROCESSING}, [ASR_GPU], "asr_runtime_failed"),
        (_gpu, _cpu_only_host, "processing,asr-gpu", {PROCESSING}, [ASR_GPU], "asr_runtime_failed"),
        (
            _gpu,
            _cpu_only_host,
            ["asr-cpu", "asr-gpu"],
            {PROCESSING},
            [ASR_CPU, ASR_GPU],
            "asr_runtime_failed",
        ),
        (_cpu, FakeHost, ASR_GPU, {ASR_CPU}, [ASR_GPU], "ready"),
        (_gpu, FakeHost, ASR_GPU, {ASR_GPU, ASR_CPU}, [], None),
        (_gpu, FakeHost, "processing,asr-gpu", ALL_QUEUES, [], None),
        (_cpu, FakeHost, ASR_CPU, {ASR_CPU}, [], None),
    ],
    ids=[
        "cpu-only-explicit-asr-gpu-refused",
        "cpu-only-comma-list-refused",
        "cpu-only-both-asr-refused",
        "cpu-qualified-explicit-asr-gpu-refused",
        "gpu-qualified-explicit-asr-gpu-kept",
        "gpu-qualified-comma-list-kept",
        "cpu-qualified-explicit-asr-cpu-kept",
    ],
)
def test_a8_explicit_queues(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    settings_factory: Callable[[], Settings],
    host_factory: Callable[[], FakeHost],
    queues: Any,
    consumed: set[str],
    refused: list[str],
    reason: str | None,
) -> None:
    _wr()
    caplog.set_level(logging.DEBUG)

    sender = _start(monkeypatch, settings_factory(), host_factory(), queues=queues)

    assert _consumed(sender) == consumed
    records = _event_records(caplog, "worker_queue_refused")
    assert sorted(
        word.split("=", 1)[1]
        for record in records
        for word in record.getMessage().split()
        if word.startswith("queue=")
    ) == sorted(refused)
    for record in records:
        assert record.levelno == logging.ERROR
        assert f"reason={reason}" in record.getMessage()


@pytest.mark.parametrize(
    ("settings_factory", "host_factory", "consumed"),
    [
        (_gpu, _cpu_only_host, {PROCESSING}),
        (_cpu, FakeHost, {PROCESSING, ASR_CPU}),
        (_gpu, FakeHost, ALL_QUEUES),
        (lambda: _gpu(worker_asr_queues="off"), FakeHost, {PROCESSING}),
    ],
    ids=["cpu-only", "cpu-qualified", "gpu-qualified", "off"],
)
def test_a8_worker_without_queues_option_consumes_only_what_it_qualifies_for(
    monkeypatch: pytest.MonkeyPatch,
    settings_factory: Callable[[], Settings],
    host_factory: Callable[[], FakeHost],
    consumed: set[str],
) -> None:
    _wr()
    sender = _start(monkeypatch, settings_factory(), host_factory(), queues=None)

    assert _consumed(sender) == consumed


@pytest.mark.parametrize(
    ("queues", "consumed"),
    [(PROCESSING, {PROCESSING}), (None, {PROCESSING}), (ASR_CPU, {PROCESSING})],
    ids=["queues-processing", "no-queues", "explicit-asr-cpu"],
)
def test_a8_off_consumes_no_asr_queue(
    monkeypatch: pytest.MonkeyPatch, queues: Any, consumed: set[str]
) -> None:
    _wr()
    sender = _start(monkeypatch, _gpu(worker_asr_queues="OFF"), FakeHost(), queues=queues)

    assert _consumed(sender) == consumed
    assert celery_module.current_worker_readiness().reason == "asr_queues_off"


def test_a8_worker_ready_logs_readiness_and_not_qualified(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _wr()
    caplog.set_level(logging.DEBUG)
    sender = _start(monkeypatch, _gpu(), _cpu_only_host(), queues=PROCESSING)
    caplog.clear()

    _fire(worker_ready, sender)

    readiness_logs = _event_records(caplog, "worker_readiness")
    assert len(readiness_logs) == 1
    assert readiness_logs[0].levelno == logging.INFO
    for name in LOGGED_FIELDS:
        assert name in _record_text(readiness_logs[0]), name
    refused = _event_records(caplog, "worker_not_qualified")
    assert len(refused) == 1
    assert refused[0].levelno == logging.ERROR
    assert "reason=asr_runtime_failed" in refused[0].getMessage()
    assert "requirement=gpu" in refused[0].getMessage()
    assert _event_records(caplog, "worker_memory_check")
    assert celery_module.current_worker_readiness() is not None


def test_a8_qualified_worker_ready_logs_no_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _wr()
    caplog.set_level(logging.DEBUG)
    sender = _start(monkeypatch, _gpu(), FakeHost(), queues=PROCESSING)
    caplog.clear()

    _fire(worker_ready, sender)

    assert len(_event_records(caplog, "worker_readiness")) == 1
    assert _event_records(caplog, "worker_not_qualified") == []
    assert [record for record in caplog.records if record.levelno >= logging.ERROR] == []
    assert celery_module.current_worker_readiness().qualified is True


def test_a9_control_memory_clamp_still_happens(monkeypatch: pytest.MonkeyPatch) -> None:
    sender = _start(monkeypatch, _gpu(worker_memory_budget_mib=5120), FakeHost(), concurrency=2)

    assert sender.concurrency == 1


def test_a9_control_memory_refusal_still_exits_78(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit) as stopped:
        _start(monkeypatch, _gpu(worker_memory_budget_mib=1000), FakeHost(), concurrency=1)

    assert stopped.value.code == 78


def test_a9_readiness_sees_the_clamped_process_count(monkeypatch: pytest.MonkeyPatch) -> None:
    _wr()
    sender = _start(
        monkeypatch,
        _gpu(worker_memory_budget_mib=5120),
        FakeHost(free_mib=5000),
        concurrency=2,
    )

    readiness = celery_module.current_worker_readiness()
    assert sender.concurrency == 1
    assert readiness.processes == 1
    assert readiness.vram_required_mib == 3584
    assert readiness.qualified is True
    assert ASR_GPU in _consumed(sender)


def test_a9_memory_refusal_exits_before_readiness_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    wr = _wr()
    assessed: list[Any] = []
    real = wr.assess_worker_readiness

    def spy(*args: Any, **kwargs: Any) -> Any:
        assessed.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(wr, "assess_worker_readiness", spy)
    host = FakeHost()

    with pytest.raises(SystemExit) as stopped:
        _start(monkeypatch, _gpu(worker_memory_budget_mib=1000), host, concurrency=1)

    assert stopped.value.code == 78
    assert assessed == []
    assert host.calls == []
    assert celery_module.current_worker_readiness() is None


def test_a9_worker_init_looks_up_the_readiness_functions_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wr = _wr()
    seen: list[dict[str, Any]] = []
    real = wr.assess_worker_readiness

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(wr, "assess_worker_readiness", spy)

    _start(monkeypatch, _gpu(worker_memory_budget_mib=5120), FakeHost(), concurrency=3)

    assert len(seen) == 1
    assert seen[0]["concurrency"] == 1
    assert isinstance(seen[0]["probes"], HostProbes)


QUEUE_SETS = {"gpu-qualified": (ASR_GPU, ASR_CPU), "cpu-qualified": (ASR_CPU,), "none": ()}
DELIVERIES = [ASR_GPU, ASR_CPU, PROCESSING, None, "celery", ""]
TARGETS = {
    "gpu": lambda: _gpu(),
    "cpu": lambda: _cpu(),
    "remote": lambda: _settings(asr_provider="mock"),
}


def _expected_decision(queues: tuple[str, ...], delivery: str | None, target: str) -> str:
    if delivery in (ASR_GPU, ASR_CPU):
        return "run" if delivery in queues else "requeue"
    return "run" if target in queues else "reroute"


@pytest.mark.parametrize("target_kind", sorted(TARGETS))
@pytest.mark.parametrize("delivery", DELIVERIES, ids=[str(item) for item in DELIVERIES])
@pytest.mark.parametrize("queue_set", sorted(QUEUE_SETS))
def test_a10_delivery_decision_matrix(
    queue_set: str, delivery: str | None, target_kind: str
) -> None:
    wr = _wr()
    settings = TARGETS[target_kind]()
    base = _assess(_settings(asr_provider="mock"))
    queues = QUEUE_SETS[queue_set]
    readiness = dataclasses.replace(base, queues=queues, qualified=bool(queues))

    decision = wr.asr_delivery_decision(readiness, delivery, settings)

    assert decision == _expected_decision(queues, delivery, wr.asr_queue_for(settings))


@pytest.mark.parametrize(
    "delivery_info",
    [{"routing_key": PROCESSING}, None, {}, {"routing_key": "celery"}],
    ids=["processing", "no-delivery-info", "no-routing-key", "unknown-queue"],
)
def test_a10_legacy_message_on_unqualified_worker_is_rerouted_once(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    task_spies: TaskSpies,
    delivery_info: dict[str, Any] | None,
) -> None:
    _wr()
    caplog.set_level(logging.DEBUG)
    _start(monkeypatch, _gpu(), _cpu_only_host())

    outcome = _deliver(delivery_info, job_id="job-legacy", workspace_id="ws-legacy")

    assert outcome == {"job_id": "job-legacy", "status": "rerouted", "queue": ASR_GPU}
    assert [_publication(options) for options in task_spies.published] == [
        {"args": ["job-legacy"], "kwargs": {"workspace_id": "ws-legacy"}, "queue": ASR_GPU}
    ]
    assert task_spies.processed == []
    assert task_spies.verified == []
    rerouted = _event_records(caplog, "asr_job_rerouted")
    assert len(rerouted) == 1
    assert f"to={ASR_GPU}" in rerouted[0].getMessage()
    if delivery_info == {"routing_key": PROCESSING}:
        assert f"from={PROCESSING}" in rerouted[0].getMessage()


def test_a10_legacy_message_on_qualified_worker_runs(
    monkeypatch: pytest.MonkeyPatch, task_spies: TaskSpies
) -> None:
    _wr()
    _start(monkeypatch, _gpu(), FakeHost())

    outcome = _deliver({"routing_key": PROCESSING}, job_id="job-legacy")

    assert outcome == {"job_id": "job-legacy", "status": "completed"}
    assert task_spies.processed == ["job-legacy"]
    assert task_spies.verified == [("job-legacy", "ws-hom42")]
    assert task_spies.published == []


@pytest.mark.parametrize(
    ("settings_factory", "host_factory", "delivery"),
    [(_cpu, FakeHost, ASR_GPU), (_gpu, _cpu_only_host, ASR_CPU), (_gpu, _cpu_only_host, ASR_GPU)],
    ids=["cpu-worker-gets-asr-gpu", "unqualified-gets-asr-cpu", "unqualified-gets-asr-gpu"],
)
def test_a10_asr_delivery_outside_readiness_is_requeued(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    task_spies: TaskSpies,
    settings_factory: Callable[[], Settings],
    host_factory: Callable[[], FakeHost],
    delivery: str,
) -> None:
    _wr()
    caplog.set_level(logging.DEBUG)
    _start(monkeypatch, settings_factory(), host_factory())

    with pytest.raises(Reject) as rejected:
        _deliver({"routing_key": delivery})

    assert rejected.value.requeue is True
    assert task_spies.processed == []
    assert task_spies.verified == []
    assert task_spies.published == []
    requeued = _event_records(caplog, "asr_job_requeued")
    assert len(requeued) == 1
    assert requeued[0].levelno == logging.WARNING
    assert f"queue={delivery}" in requeued[0].getMessage()


@pytest.mark.parametrize(
    ("settings_factory", "delivery"),
    [(_cpu, ASR_CPU), (_gpu, ASR_GPU), (_gpu, ASR_CPU)],
    ids=["cpu-worker-gets-asr-cpu", "gpu-worker-gets-asr-gpu", "gpu-worker-gets-asr-cpu"],
)
def test_a10_asr_delivery_inside_readiness_runs(
    monkeypatch: pytest.MonkeyPatch,
    task_spies: TaskSpies,
    settings_factory: Callable[[], Settings],
    delivery: str,
) -> None:
    _wr()
    _start(monkeypatch, settings_factory(), FakeHost())

    outcome = _deliver({"routing_key": delivery}, job_id="job-run")

    assert outcome == {"job_id": "job-run", "status": "completed"}
    assert task_spies.processed == ["job-run"]
    assert task_spies.published == []


def test_a10_eager_mode_skips_the_guard(
    monkeypatch: pytest.MonkeyPatch, task_spies: TaskSpies
) -> None:
    _wr()
    _start(monkeypatch, _gpu(), _cpu_only_host())

    result = process_meeting_job.apply(args=["job-eager"], kwargs={"workspace_id": "ws-eager"})

    assert result.get() == {"job_id": "job-eager", "status": "completed"}
    assert task_spies.processed == ["job-eager"]
    assert task_spies.published == []


def test_a10_without_stored_readiness_the_task_assesses_with_default_probes(
    monkeypatch: pytest.MonkeyPatch, task_spies: TaskSpies
) -> None:
    _wr()
    _use_settings(monkeypatch, _gpu())
    _use_probes(monkeypatch, _cpu_only_host())

    with pytest.raises(Reject):
        _deliver({"routing_key": ASR_GPU})

    assert task_spies.processed == []


def _warm_spy(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []

    def warm(settings: Any) -> bool:
        calls.append(settings)
        return True

    monkeypatch.setattr("backend.providers.warm_models.warm_configured_asr", warm)
    return calls


@pytest.mark.parametrize(
    ("settings_factory", "host_factory", "reason"),
    [
        (_gpu, _cpu_only_host, "asr_runtime_failed"),
        (lambda: _gpu(worker_asr_queues="off"), FakeHost, "asr_queues_off"),
    ],
    ids=["not-qualified", "off"],
)
def test_a11_no_asr_queue_skips_the_warm(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    settings_factory: Callable[[], Settings],
    host_factory: Callable[[], FakeHost],
    reason: str,
) -> None:
    _wr()
    caplog.set_level(logging.DEBUG)
    _start(monkeypatch, settings_factory(), host_factory())
    warmed = _warm_spy(monkeypatch)

    _fire(worker_process_init, None)

    assert warmed == []
    skipped = _event_records(caplog, "asr_warm_skipped")
    assert len(skipped) == 1
    assert f"reason={reason}" in skipped[0].getMessage()


@pytest.mark.parametrize(
    ("settings_factory", "host_factory"),
    [(_gpu, FakeHost), (_cpu, FakeHost), (lambda: _settings(asr_provider="mock"), FakeHost)],
    ids=["gpu-qualified", "cpu-qualified", "remote"],
)
def test_a11_asr_queue_warms(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    settings_factory: Callable[[], Settings],
    host_factory: Callable[[], FakeHost],
) -> None:
    _wr()
    caplog.set_level(logging.DEBUG)
    _start(monkeypatch, settings_factory(), host_factory())
    warmed = _warm_spy(monkeypatch)

    _fire(worker_process_init, None)

    assert len(warmed) == 1
    assert _event_records(caplog, "asr_warm_skipped") == []


def test_a11_control_no_stored_readiness_warms_as_today(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    settings = _gpu()
    _use_settings(monkeypatch, settings)
    warmed = _warm_spy(monkeypatch)

    _fire(worker_process_init, None)

    assert warmed == [settings]
    assert _event_records(caplog, "asr_warm_skipped") == []


def _inspect_handler() -> Callable[..., Any]:
    from celery.worker.control import Panel

    assert Panel.meta["worker_readiness"].type == "inspect"
    return Panel.data["worker_readiness"]


def test_a12_inspect_returns_the_stored_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    _wr()
    _start(monkeypatch, _gpu(), FakeHost())
    handler = _inspect_handler()

    reply = handler(SimpleNamespace(app=celery_module.celery_app, consumer=None))

    assert reply == celery_module.current_worker_readiness().to_dict()
    assert json.loads(json.dumps(reply))["reason"] == "ready"


def test_a12_inspect_without_stored_readiness_is_unknown() -> None:
    _wr()
    handler = _inspect_handler()

    assert celery_module.current_worker_readiness() is None
    assert handler(SimpleNamespace(app=celery_module.celery_app, consumer=None)) == {
        "reason": "unknown"
    }


class FakeRedis:
    def __init__(self, lengths: dict[str, int], *, up: bool = True) -> None:
        self.lengths = lengths
        self.up = up
        self.llen_calls: list[str] = []

    def ping(self) -> bool:
        if not self.up:
            raise redis.exceptions.ConnectionError("down")
        return True

    def llen(self, name: Any) -> int:
        name = name.decode() if isinstance(name, bytes) else name
        self.llen_calls.append(name)
        return self.lengths.get(name, 0)

    def pipeline(self, *_args: Any, **_kwargs: Any) -> FakePipeline:
        return FakePipeline(self)

    def close(self) -> None:
        return None


class FakePipeline:
    def __init__(self, parent: FakeRedis) -> None:
        self.parent = parent
        self.queued: list[Any] = []

    def __enter__(self) -> FakePipeline:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def llen(self, name: Any) -> FakePipeline:
        self.queued.append(name)
        return self

    def execute(self) -> list[int]:
        return [self.parent.llen(name) for name in self.queued]


def _patch_redis(monkeypatch: pytest.MonkeyPatch, fake: FakeRedis) -> list[str]:
    opened: list[str] = []

    def from_url(cls: Any, url: str, **_kwargs: Any) -> FakeRedis:
        opened.append(url)
        return fake

    monkeypatch.setattr(redis.Redis, "from_url", classmethod(from_url))
    return opened


def test_a13_metrics_report_every_declared_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRedis({PROCESSING: 1, ASR_CPU: 2, ASR_GPU: 4})
    opened = _patch_redis(monkeypatch, fake)
    snapshot = metrics.Snapshot()

    metrics.collect_redis(snapshot, _settings())

    _help, labels, samples = snapshot.families["homayar_broker_queue_length"]
    assert labels == ("queue",)
    assert {values[0]: value for values, value in samples} == {
        PROCESSING: 1.0,
        ASR_CPU: 2.0,
        ASR_GPU: 4.0,
    }
    assert len(samples) == 3
    assert sorted(fake.llen_calls) == sorted(ALL_QUEUES)
    assert len(opened) == 1


def test_a13_control_metrics_when_redis_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis(monkeypatch, FakeRedis({}, up=False))
    snapshot = metrics.Snapshot()

    metrics.collect_redis(snapshot, _settings())

    assert "homayar_broker_queue_length" not in snapshot.families
    _help, _labels, samples = snapshot.families["homayar_dependency_up"]
    assert samples == [(("redis",), 0.0)]


@pytest.fixture
def memory_heartbeats():
    store = heartbeats.MemoryHeartbeatStore()
    heartbeats.use_heartbeat_store(store)
    yield store
    heartbeats.use_heartbeat_store(None)


def _system_health(client: Any, email_outbox: Any, settings: Settings) -> dict[str, Any]:
    from backend.tests.test_system_health import _health, _operator, _use_long_deadline

    workspace_id = _operator(client, email_outbox)
    _use_long_deadline(client.app, settings)
    return _health(client, workspace_id)


def test_a13_system_health_sums_the_three_queues(
    client: Any,
    email_outbox: Any,
    settings: Settings,
    memory_heartbeats: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_redis(monkeypatch, FakeRedis({PROCESSING: 1, ASR_CPU: 2, ASR_GPU: 4}))

    body = _system_health(client, email_outbox, settings)

    assert body["queue"]["broker_queue_length"] == 7
    assert set(body["queue"]) == {
        "status",
        "queued_due",
        "oldest_queued_due_age_seconds",
        "retry_scheduled",
        "broker_queue_length",
    }


def test_a13_control_system_health_without_redis(
    client: Any, email_outbox: Any, settings: Settings, memory_heartbeats: Any
) -> None:
    body = _system_health(client, email_outbox, settings)

    assert body["queue"]["broker_queue_length"] is None


def _active(*names: str) -> list[dict[str, Any]]:
    return [Queue(name, routing_key=name).as_dict(recurse=True) for name in names]


READY_GPU = {
    "qualified": True,
    "reason": "ready",
    "requirement": "gpu",
    "queues": [ASR_GPU, ASR_CPU],
}
NOT_QUALIFIED = {
    "qualified": False,
    "reason": "gpu_driver_failed",
    "requirement": "gpu",
    "queues": [],
}


def _queue_entry(report: dict[str, Any], name: str) -> dict[str, Any]:
    entries = [entry for entry in report["queues"] if entry["name"] == name]
    assert len(entries) == 1, report["queues"]
    return {key: entries[0][key] for key in ("name", "length", "consumers")}


def test_a14_waiting_gpu_job_without_consumer_is_not_ok() -> None:
    report = _wr().workers_report(
        {"celery@cpu-1": NOT_QUALIFIED},
        {"celery@cpu-1": _active(PROCESSING)},
        {PROCESSING: 0, ASR_CPU: 0, ASR_GPU: 3},
    )

    assert report["ok"] is False
    assert _queue_entry(report, ASR_GPU) == {"name": ASR_GPU, "length": 3, "consumers": 0}
    assert _queue_entry(report, PROCESSING)["consumers"] == 1
    assert {entry["name"] for entry in report["queues"]} == ALL_QUEUES
    assert len(report["queues"]) == 3


def test_a14_waiting_gpu_job_with_one_consumer_is_ok() -> None:
    report = _wr().workers_report(
        {"celery@gpu-1": READY_GPU, "celery@cpu-1": NOT_QUALIFIED},
        {
            "celery@gpu-1": _active(PROCESSING, ASR_GPU, ASR_CPU),
            "celery@cpu-1": _active(PROCESSING),
        },
        {PROCESSING: 0, ASR_CPU: 0, ASR_GPU: 3},
    )

    assert report["ok"] is True
    assert _queue_entry(report, ASR_GPU) == {"name": ASR_GPU, "length": 3, "consumers": 1}
    assert _queue_entry(report, ASR_CPU)["consumers"] == 1
    assert _queue_entry(report, PROCESSING)["consumers"] == 2


def test_a14_empty_gpu_queue_without_consumer_is_ok() -> None:
    report = _wr().workers_report(
        {"celery@cpu-1": NOT_QUALIFIED},
        {"celery@cpu-1": _active(PROCESSING)},
        {PROCESSING: 5, ASR_CPU: 0, ASR_GPU: 0},
    )

    assert report["ok"] is True
    assert _queue_entry(report, ASR_GPU) == {"name": ASR_GPU, "length": 0, "consumers": 0}


def test_a14_waiting_cpu_job_without_consumer_is_not_ok() -> None:
    report = _wr().workers_report(
        {"celery@cpu-1": NOT_QUALIFIED},
        {"celery@cpu-1": _active(PROCESSING)},
        {PROCESSING: 0, ASR_CPU: 2, ASR_GPU: 0},
    )

    assert report["ok"] is False


@pytest.mark.parametrize(
    "lengths",
    [
        {PROCESSING: 0, ASR_CPU: 0, ASR_GPU: None},
        {PROCESSING: 0, ASR_CPU: None, ASR_GPU: 0},
        {PROCESSING: 0, ASR_CPU: 0},
    ],
    ids=["gpu-length-none", "cpu-length-none", "gpu-length-absent"],
)
def test_a14_missing_value_is_not_ok(lengths: dict[str, Any]) -> None:
    report = _wr().workers_report(
        {"celery@gpu-1": READY_GPU},
        {"celery@gpu-1": _active(PROCESSING, ASR_GPU, ASR_CPU)},
        lengths,
    )

    assert report["ok"] is False


def test_a14_workers_are_sorted_by_hostname_with_their_readiness_and_queues() -> None:
    report = _wr().workers_report(
        {"celery@zeta": NOT_QUALIFIED, "celery@alpha": READY_GPU},
        {
            "celery@zeta": _active(PROCESSING),
            "celery@alpha": _active(PROCESSING, ASR_GPU, ASR_CPU),
        },
        {PROCESSING: 0, ASR_CPU: 0, ASR_GPU: 0},
    )

    workers = report["workers"]
    assert [worker["hostname"] for worker in workers] == ["celery@alpha", "celery@zeta"]
    assert READY_GPU in workers[0].values()
    assert NOT_QUALIFIED in workers[1].values()
    listed = [sorted(value) for value in workers[0].values() if isinstance(value, list | tuple)]
    assert sorted([PROCESSING, ASR_GPU, ASR_CPU]) in listed
    assert json.dumps(report)


def _cli(*args: str, **extra_env: str) -> subprocess.CompletedProcess:
    blocked = set(ISOLATED_ENV)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in blocked and not key.startswith(("ASR_", "WHISPER_", "WORKER_"))
    }
    env.update(
        {
            "ENVIRONMENT": "test",
            "REDIS_URL": "redis://127.0.0.1:1/0",
            "ALLOW_MOCK_PROVIDERS": "true",
            "LOCAL_AI_BASE_URL": "http://intelligence.test/v1",
            "LLM_MODEL": "conversation-model",
        }
    )
    env.update(extra_env)
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", WR_MODULE, *args],
        cwd=HOMAYAR_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _json_document(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except ValueError:
        lines = stripped.splitlines()
        for start, line in enumerate(lines):
            if line.startswith("{"):
                try:
                    return json.loads("\n".join(lines[start:]))
                except ValueError:
                    continue
    raise AssertionError(f"no JSON document in the output: {text!r}")


@pytest.mark.parametrize(
    ("extra_env", "processes"),
    [({}, 1), ({"CELERY_CONCURRENCY": "3"}, 3)],
    ids=["default-concurrency", "celery-concurrency"],
)
def test_a14_cli_check_qualified_exits_0(extra_env: dict[str, str], processes: int) -> None:
    completed = _cli("--check", ASR_PROVIDER="mock", **extra_env)

    assert completed.returncode == 0, completed.stderr[-2000:]
    report = _json_document(completed.stdout)
    assert report["qualified"] is True
    assert report["reason"] == "no_local_model"
    assert report["processes"] == processes


def test_a14_cli_check_not_qualified_exits_1() -> None:
    completed = _cli("--check", ASR_PROVIDER="mock", WORKER_ASR_QUEUES="off")

    assert completed.returncode == 1, completed.stderr[-2000:]
    report = _json_document(completed.stdout)
    assert report["qualified"] is False
    assert report["reason"] == "asr_queues_off"


@pytest.mark.parametrize(
    "extra_env",
    [
        {"ASR_PROVIDER": "mock", "WORKER_ASR_QUEUES": "maybe"},
        {"ASR_PROVIDER": "local_whisper", "WHISPER_COMPUTE_TYPE": " "},
    ],
    ids=["settings-error", "adapter-error"],
)
def test_a14_cli_check_configuration_error_exits_2(extra_env: dict[str, str]) -> None:
    completed = _cli("--check", **extra_env)

    assert completed.returncode == 2, (completed.stdout[-2000:], completed.stderr[-2000:])


def test_a14_cli_workers_without_broker_exits_2() -> None:
    completed = _cli("--workers", ASR_PROVIDER="mock")

    assert completed.returncode == 2, (completed.stdout[-2000:], completed.stderr[-2000:])


def _assert_private(text: str, *, urls: bool = True) -> None:
    assert UUID_FRAGMENT not in text
    assert PRIVATE_MODEL_PATH not in text
    assert "hom42-private-models" not in text
    assert PRIVATE_MESSAGE not in text
    if urls:
        assert "://" not in text
        assert "intelligence.test" not in text


@pytest.mark.parametrize(
    ("overrides", "environ"),
    [
        ({"whisper_model_path": PRIVATE_MODEL_PATH}, {"CUDA_VISIBLE_DEVICES": P40_UUID}),
        (
            {"whisper_model_path": PRIVATE_MODEL_PATH, "whisper_local_files_only": True},
            {"CUDA_VISIBLE_DEVICES": P40_UUID},
        ),
        ({"whisper_model_path": PRIVATE_MODEL_PATH}, {"CUDA_VISIBLE_DEVICES": "7"}),
    ],
    ids=["qualified", "model-path-checked", "missing-gpu"],
)
def test_a15_record_holds_no_uuid_path_or_url(
    overrides: dict[str, Any], environ: dict[str, str]
) -> None:
    readiness = _assess(_gpu(**overrides), FakeHost(environ=environ))

    _assert_private(_rendered(readiness))


def test_a15_control_host_checks_do_report_the_uuid() -> None:
    bundle = SimpleNamespace(
        asr=LocalWhisperProvider(
            model_name="large-v3", device="cuda", compute_type="int8_float32", device_index=(0,)
        )
    )

    options = HostOptions(min_free_gpu_mib=3584)
    report = run_host_checks(_gpu(), bundle, options, FakeHost().probes())

    assert P40_UUID in json.dumps(report)


def test_a15_record_of_a_probe_exception_holds_no_message() -> None:
    readiness = _assess(_gpu(whisper_model_path=PRIVATE_MODEL_PATH), FakeHost(explode="nvidia_smi"))

    assert readiness.reason == "readiness_error"
    _assert_private(_rendered(readiness))


@pytest.mark.parametrize(
    "host_factory",
    [
        lambda: FakeHost(environ={"CUDA_VISIBLE_DEVICES": P40_UUID}),
        lambda: FakeHost(environ={"CUDA_VISIBLE_DEVICES": "7"}),
        lambda: FakeHost(explode="nvidia_smi"),
        lambda: FakeHost(explode="ctranslate2"),
    ],
    ids=["qualified", "missing-gpu", "nvidia-smi-exception", "ctranslate2-exception"],
)
def test_a15_worker_logs_hold_no_uuid_path_or_message(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    host_factory: Callable[[], FakeHost],
) -> None:
    _wr()
    caplog.set_level(logging.DEBUG)
    sender = _start(
        monkeypatch,
        _gpu(whisper_model_path=PRIVATE_MODEL_PATH),
        host_factory(),
        queues="processing,asr-gpu",
    )
    _fire(worker_ready, sender)
    _warm_spy(monkeypatch)
    _fire(worker_process_init, None)

    assert _event_records(caplog, "worker_readiness")
    _assert_private(_everything_logged(caplog), urls=False)
    _assert_private(_rendered(celery_module.current_worker_readiness()))


def test_a16_defaults() -> None:
    settings = Settings(_env_file=None, environment="test")

    assert settings.worker_asr_queues == "auto"
    assert settings.worker_vram_budget_mib == 0
    assert settings.asr_model_vram_mib == 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [("OFF", "off"), (" Off ", "off"), ("off", "off"), ("AUTO", "auto"), (" auto ", "auto")],
)
def test_a16_worker_asr_queues_is_normalised_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: str
) -> None:
    monkeypatch.setenv("WORKER_ASR_QUEUES", value)

    assert Settings(_env_file=None, environment="test").worker_asr_queues == expected


@pytest.mark.parametrize("value", ["maybe", "on", "gpu", "auto,off"])
def test_a16_worker_asr_queues_rejects_other_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("WORKER_ASR_QUEUES", value)

    with pytest.raises(ValueError):
        Settings(_env_file=None, environment="test")


@pytest.mark.parametrize("name", ["WORKER_VRAM_BUDGET_MIB", "ASR_MODEL_VRAM_MIB"])
@pytest.mark.parametrize("value", ["-1", "-4096"])
def test_a16_negative_vram_values_fail(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        Settings(_env_file=None, environment="test")


@pytest.mark.parametrize("name", ["WORKER_VRAM_BUDGET_MIB", "ASR_MODEL_VRAM_MIB"])
def test_a16_vram_values_load(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "4096")

    settings = Settings(_env_file=None, environment="test")

    assert getattr(settings, name.lower()) == 4096


def _compose(file_name: str) -> dict[str, Any]:
    return yaml.safe_load((COMPOSE_DIR / file_name).read_text(encoding="utf-8"))


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@pytest.mark.parametrize("file_name", sorted(COMPOSE_GOLDEN))
def test_a17_worker_gets_the_three_settings_and_no_one_else(file_name: str) -> None:
    document = _compose(file_name)
    services = document["services"]

    for name, value in NEW_ENV_DEFAULTS.items():
        assert services["worker"]["environment"].get(name) == value, (file_name, name)
    for service_name, service in services.items():
        if service_name == "worker":
            continue
        environment = service.get("environment") or {}
        for name in NEW_ENV:
            assert name not in environment, (file_name, service_name, name)
    for name in NEW_ENV:
        assert name not in (document.get("x-app-environment") or {}), (file_name, name)


@pytest.mark.parametrize("file_name", sorted(COMPOSE_GOLDEN))
def test_a17_control_worker_command_and_other_services_unchanged(file_name: str) -> None:
    document = _compose(file_name)
    services = json.loads(json.dumps(document["services"]))
    worker_environment = services["worker"].pop("environment")
    kept_environment = {
        name: value for name, value in worker_environment.items() if name not in NEW_ENV
    }
    rest = {name: value for name, value in document.items() if name != "services"}

    assert document["services"]["worker"]["command"] == BASE_WORKER_COMMAND
    assert "--queues processing" in " ".join(document["services"]["worker"]["command"])
    assert _digest(services) == COMPOSE_GOLDEN[file_name]["services"]
    assert _digest(kept_environment) == COMPOSE_GOLDEN[file_name]["worker_env"]
    assert _digest(rest) == COMPOSE_GOLDEN[file_name]["rest"]


def test_a17_env_example_documents_the_three_settings() -> None:
    text = (HOMAYAR_DIR / ".env.example").read_text(encoding="utf-8")
    assigned = {
        line.split("=", 1)[0].lstrip("# ").strip() for line in text.splitlines() if "=" in line
    }

    for name in NEW_ENV:
        assert name in assigned, name


def test_a18_default_production_path(
    monkeypatch: pytest.MonkeyPatch, task_spies: TaskSpies
) -> None:
    _wr()
    api_settings = _settings()
    message_id = dispatcher.publish_processing_job("job-prod", api_settings, workspace_id="ws-1")

    sender = _start(
        monkeypatch,
        _gpu(),
        FakeHost(free_mib=20000),
        queues=PROCESSING,
        concurrency=1,
    )
    readiness = celery_module.current_worker_readiness()
    outcome = _deliver({"routing_key": ASR_CPU}, job_id="job-prod", workspace_id="ws-1")

    assert message_id == "message-hom42"
    assert [_publication(options) for options in task_spies.published] == [
        {"args": ["job-prod"], "kwargs": {"workspace_id": "ws-1"}, "queue": ASR_CPU}
    ]
    assert readiness.qualified is True
    assert tuple(readiness.queues) == (ASR_GPU, ASR_CPU)
    assert _consumed(sender) == ALL_QUEUES
    assert outcome == {"job_id": "job-prod", "status": "completed"}
    assert task_spies.processed == ["job-prod"]


@pytest.mark.redis
def test_a19_real_cpu_worker_leaves_gpu_message_and_runs_cpu_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.tests._transport import (
        configured_redis_url,
        flush_test_database,
        redis_client,
        use_broker,
    )

    wr = _wr()
    url = configured_redis_url()
    assert url is not None
    _use_settings(monkeypatch, _cpu())
    monkeypatch.setattr(wr, "default_probes", lambda: FakeHost().probes())
    ran: list[str] = []

    def process(job_id: str, **_kwargs: Any) -> dict[str, str]:
        ran.append(job_id)
        return {"job_id": job_id, "status": "completed"}

    monkeypatch.setattr("backend.jobs.outbox.verify_job_payload", lambda *_args, **_kw: True)
    monkeypatch.setattr("backend.jobs.processor.process_job", process)
    monkeypatch.setattr("backend.providers.warm_models.warm_configured_asr", lambda _s: False)
    queues = celery_module.celery_app.amqp.queues
    previous_selection = queues._consume_from
    flush_test_database(url)
    try:
        with use_broker(url, url):
            with start_worker(
                celery_module.celery_app,
                pool="solo",
                concurrency=1,
                perform_ping_check=False,
                loglevel="warning",
                shutdown_timeout=15,
                queues=[PROCESSING],
            ):
                process_meeting_job.apply_async(args=["job-hom42-gpu"], queue=ASR_GPU)
                cpu = process_meeting_job.apply_async(args=["job-hom42-cpu"], queue=ASR_CPU)
                assert cpu.get(timeout=30) == {"job_id": "job-hom42-cpu", "status": "completed"}
                time.sleep(1.0)
                client = redis_client(url)
                try:
                    waiting = client.llen(ASR_GPU)
                finally:
                    client.close()
    finally:
        queues._consume_from = previous_selection
        flush_test_database(url)

    assert ran == ["job-hom42-cpu"]
    assert waiting == 1


def _golden_host_probes(run: Callable[..., Any]) -> HostProbes:
    class GoldenCT2:
        def get_cuda_device_count(self) -> int:
            return 1

        def get_supported_compute_types(self, device: str, device_index: int = 0) -> set[str]:
            if device == "cpu":
                return {"int8", "float32"}
            return {"int8_float32", "float32", "int8"}

    def refuse(_host: str, _port: int, _timeout: float) -> None:
        raise ConnectionRefusedError("refused")

    return HostProbes(
        run=run,
        connect=refuse,
        disk_usage=lambda _path: SimpleNamespace(total=10**12, used=0, free=50 * 1024**3),
        find_spec=lambda name: object() if name == "faster_whisper" else None,
        ctranslate2=lambda: GoldenCT2(),
        environ={},
        now=lambda: FIXED_NOW,
        read_stdin=lambda: b"",
        temp_dir=lambda: "/hom42-temp",
        cpu_count=lambda: 8,
        memory_total_mib=lambda: 65536,
    )


def _golden_smi_ok(args: Any, **_kwargs: Any) -> subprocess.CompletedProcess:
    line = f"0, {P40_UUID}, Tesla P40, 24576, 4576, 20000, 535.104.05, 6.1\n"
    return subprocess.CompletedProcess(args, 0, stdout=line, stderr="")


def _golden_smi_missing(_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess:
    raise FileNotFoundError("nvidia-smi")


@pytest.mark.parametrize(
    ("golden", "run", "options"),
    [
        ("gpu_pass", _golden_smi_ok, HostOptions()),
        (
            "gpu_no_smi_offline",
            _golden_smi_missing,
            HostOptions(offline=True, min_free_gpu_mib=3584),
        ),
    ],
    ids=["gpu-pass", "gpu-no-smi-offline"],
)
def test_a20_control_host_checks_output_is_byte_for_byte_unchanged(
    golden: str, run: Callable[..., Any], options: HostOptions
) -> None:
    settings = SimpleNamespace(
        asr_profile=GPU,
        whisper_local_files_only=False,
        whisper_model_path="",
        model_artifact_root="",
        storage_backend="local",
        storage_local_root="/hom42-objects",
        database_url="sqlite:///hom42.db",
        redis_url="redis://redis.hom42.invalid:6379/0",
        storage_endpoint_url="",
        local_ai_base_url="",
        local_embedding_base_url="",
        local_rerank_base_url="",
        enable_google_auth=False,
        enable_phone_auth=False,
        asanak_base_url="",
        sentry_dsn="",
        smtp_host="",
        deployment_topology="",
    )
    bundle = SimpleNamespace(
        asr=LocalWhisperProvider(
            model_name="large-v3", device="cuda", compute_type="int8_float32", device_index=(0,)
        )
    )

    rendered = json.dumps(run_host_checks(settings, bundle, options, _golden_host_probes(run)))

    assert hashlib.sha256(rendered.encode()).hexdigest() == HOST_CHECKS_GOLDEN[golden], rendered
