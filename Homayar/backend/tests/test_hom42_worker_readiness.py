"""HOM-42: worker readiness and routing ASR jobs to qualified workers (ADR 0074).

Every probe here is a fake: no ``nvidia-smi``, no ``ctranslate2`` import in
this process, no model load, no network. The real-worker tests run on the
in-memory broker, and on a disposable Redis when ``TEST_REDIS_URL`` is set
(marked ``redis``).
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from celery.exceptions import Reject

from backend.config import Settings
from backend.jobs import worker_readiness
from backend.providers.asr_profiles import ASR_PROFILES, get_asr_profile
from backend.providers.host_checks import HostProbes

GPU = "gpu-p40-large-v3"
FAKE_UUID_0 = "GPU-1234abcd-0000-1111-2222-333344445555"
FAKE_UUID_1 = "GPU-9876fedc-0000-1111-2222-333344445555"
PRIVATE_PATH = "/secret/models/large-v3-private"
PRIVATE_MESSAGE = "probe broke at /secret/private/place"
ALL_QUEUES = ["processing", "asr-cpu", "asr-gpu"]
WORKER_HOSTNAME = "celery@test-host"

# --- helpers ---------------------------------------------------------------------------


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "local_ai_base_url": "http://intelligence.test/v1",
        "llm_model": "conversation-model",
        "allow_mock_providers": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeCT2:
    """The two ctranslate2 calls the host checks make."""

    def __init__(self, *, count: int = 1, cuda=None, cpu=None) -> None:
        self.count = count
        self.cuda = set(cuda or {"float32", "int8", "int8_float32"})
        self.cpu = set(cpu or {"float32", "int8", "int8_float32"})

    def get_cuda_device_count(self) -> int:
        return self.count

    def get_supported_compute_types(self, device: str, device_index: int = 0) -> set[str]:
        return self.cuda if device == "cuda" else self.cpu


def _smi_line(index: int, uuid: str, free: int, *, driver: str = "535.183.01") -> str:
    return f"{index}, {uuid}, Tesla P40, 24576, {24576 - free}, {free}, {driver}, 6.1"


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["fake"], returncode, stdout=stdout, stderr="")


def probes(
    *,
    smi: str | BaseException | None = None,
    returncode: int = 0,
    ct2: Any = None,
    spec: Any = object(),
    environ: dict[str, str] | None = None,
) -> HostProbes:
    """Fake host probes: ``nvidia-smi`` output, ``find_spec`` and ctranslate2."""

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        if isinstance(smi, BaseException):
            raise smi
        return _completed(smi or "", returncode)

    return HostProbes(
        run=run,
        find_spec=lambda name: spec,
        ctranslate2=lambda: FakeCT2() if ct2 is None else ct2,
        environ=environ if environ is not None else {},
    )


def p40(free: int = 20000, **kwargs: Any) -> HostProbes:
    """One fake Tesla P40 with ``free`` MiB of free VRAM, working CUDA."""
    return probes(smi=_smi_line(0, FAKE_UUID_0, free), **kwargs)


READY_CPU = dict(qualified=True, reason="ready", requirement="cpu", queues=("asr-cpu",))
READY_GPU = dict(qualified=True, reason="ready", requirement="gpu", queues=("asr-gpu", "asr-cpu"))


def assess(**kwargs: Any) -> worker_readiness.WorkerReadiness:
    settings = kwargs.pop("settings", None) or _settings(asr_profile=GPU)
    kwargs.setdefault("pool", "prefork")
    kwargs.setdefault("concurrency", 1)
    kwargs.setdefault("probes", p40())
    return worker_readiness.assess_worker_readiness(settings, **kwargs)


def _verdict(record: worker_readiness.WorkerReadiness) -> tuple[bool, str, tuple[str, ...]]:
    return (record.qualified, record.reason, record.queues)


@pytest.fixture
def celery_module():
    import backend.celery_app as module

    return module


@pytest.fixture(autouse=True)
def _clean_worker_state(celery_module):
    """No stored readiness or queue selection leaks between tests."""
    queues = celery_module.celery_app.amqp.queues
    saved_selection = queues._consume_from
    saved_readiness = celery_module._worker_readiness
    celery_module._worker_readiness = None
    yield
    queues._consume_from = saved_selection
    celery_module._worker_readiness = saved_readiness


def _messages(caplog: pytest.LogCaptureFixture, prefix: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage().startswith(prefix)]


# --- A1: the queue contract ------------------------------------------------------------


def test_declared_queues_are_processing_and_the_two_asr_queues(celery_module) -> None:
    conf = celery_module.celery_app.conf
    assert conf.task_default_queue == "processing"
    assert {queue.name for queue in conf.task_queues} == {"processing", "asr-cpu", "asr-gpu"}
    assert len(conf.task_queues) == 3
    for queue in conf.task_queues:
        assert queue.routing_key == queue.name
    assert celery_module.ASR_CPU_QUEUE == "asr-cpu"
    assert celery_module.ASR_GPU_QUEUE == "asr-gpu"
    assert celery_module.ASR_QUEUES == ("asr-gpu", "asr-cpu")


def test_process_meeting_route_follows_asr_queue_for(celery_module) -> None:
    target = worker_readiness.asr_queue_for(celery_module.get_settings())
    conf = celery_module.celery_app.conf
    assert conf.task_routes[celery_module.PROCESS_MEETING_TASK] == {"queue": target}
    routed = celery_module.celery_app.amqp.router.route({}, celery_module.PROCESS_MEETING_TASK)
    assert routed["queue"].name == target


@pytest.mark.parametrize(("profile", "queue"), [(GPU, "asr-gpu"), ("cpu-base", "asr-cpu")])
def test_make_celery_routes_process_meeting_by_the_settings(
    monkeypatch: pytest.MonkeyPatch, celery_module, profile: str, queue: str
) -> None:
    monkeypatch.setattr(celery_module, "get_settings", lambda: _settings(asr_profile=profile))
    try:
        app = celery_module.make_celery()
        assert app.conf.task_routes[celery_module.PROCESS_MEETING_TASK] == {"queue": queue}
    finally:
        celery_module.celery_app.set_current()


def test_every_other_route_and_beat_entry_stays_on_processing(celery_module) -> None:
    conf = celery_module.celery_app.conf
    others = {
        task: route
        for task, route in conf.task_routes.items()
        if task != celery_module.PROCESS_MEETING_TASK
    }
    assert len(others) == 11
    for task, route in others.items():
        assert route == {"queue": "processing"}, task
    for name, entry in conf.beat_schedule.items():
        assert entry["options"]["queue"] == "processing", name


# --- A2: the job's requirement and its queue -------------------------------------------


@pytest.mark.parametrize("name", sorted(n for n in ASR_PROFILES if n.startswith("cpu-")))
def test_cpu_profiles_give_cpu_and_asr_cpu(name: str) -> None:
    settings = _settings(asr_profile=name)
    assert worker_readiness.asr_requirement(settings) == "cpu"
    assert worker_readiness.asr_queue_for(settings) == "asr-cpu"


def test_gpu_profile_gives_gpu_and_asr_gpu() -> None:
    settings = _settings(asr_profile=GPU)
    assert worker_readiness.asr_requirement(settings) == "gpu"
    assert worker_readiness.asr_queue_for(settings) == "asr-gpu"


@pytest.mark.parametrize("device", ["cuda", "cuda:1"])
def test_whisper_device_cuda_gives_gpu_without_a_profile(device: str) -> None:
    settings = _settings(whisper_device=device)
    assert worker_readiness.asr_requirement(settings) == "gpu"
    assert worker_readiness.asr_queue_for(settings) == "asr-gpu"


@pytest.mark.parametrize("device", ["cpu", "cudax", "auto"])
def test_any_other_whisper_device_gives_cpu(device: str) -> None:
    settings = _settings(whisper_device=device)
    assert worker_readiness.asr_requirement(settings) == "cpu"
    assert worker_readiness.asr_queue_for(settings) == "asr-cpu"


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        ("cuda", True),
        ("cuda:0", True),
        ("cuda:1", True),
        ("cuda0", False),
        ("cudax", False),
        ("CUDA", False),
        (" cuda", False),
        ("cpu", False),
        ("auto", False),
        ("", False),
        (None, False),
    ],
)
def test_one_cuda_device_rule_for_routing_and_host_checks(device, expected) -> None:
    from backend.providers import host_checks

    assert host_checks.is_cuda_device(device) is expected
    assert host_checks._Runtime(local=True, device=device).gpu is expected
    if device is not None:
        requirement = worker_readiness.asr_requirement(_settings(whisper_device=device))
        assert requirement == ("gpu" if expected else "cpu")


def test_routing_and_host_checks_share_the_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both sides call host_checks.is_cuda_device, so one change moves both."""
    from backend.providers import host_checks

    monkeypatch.setattr(host_checks, "is_cuda_device", lambda device: device == "npu")
    assert host_checks._Runtime(local=True, device="npu").gpu is True
    assert host_checks._Runtime(local=True, device="cuda").gpu is False
    assert worker_readiness.asr_requirement(_settings(whisper_device="npu")) == "gpu"
    assert worker_readiness.asr_requirement(_settings(whisper_device="cuda")) == "cpu"


def test_a_profile_wins_over_whisper_device() -> None:
    assert (
        worker_readiness.asr_requirement(_settings(asr_profile="cpu-base", whisper_device="cuda"))
        == "cpu"
    )
    assert (
        worker_readiness.asr_requirement(_settings(asr_profile=GPU, whisper_device="cpu")) == "gpu"
    )


def test_a_non_local_provider_gives_remote_and_asr_cpu() -> None:
    settings = _settings(asr_provider="mock", allow_mock_providers=True, whisper_device="cuda")
    assert worker_readiness.asr_requirement(settings) == "remote"
    assert worker_readiness.asr_queue_for(settings) == "asr-cpu"


def test_the_provider_name_is_normalised_like_the_factory() -> None:
    spaced = _settings(asr_provider=" Local-Whisper ", whisper_device="cuda")
    assert worker_readiness.asr_requirement(spaced) == "gpu"


def test_the_requirement_loads_no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.providers import factory

    def no_build(_settings):
        raise AssertionError("asr_requirement must not build an adapter")

    monkeypatch.setattr(factory, "create_asr_provider", no_build)
    assert worker_readiness.asr_requirement(_settings(asr_profile=GPU)) == "gpu"


# --- A3: the dispatcher publishes to the ASR queue -------------------------------------


def test_dispatcher_publishes_to_the_asr_queue_with_args_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.jobs import dispatcher
    from backend.jobs.tasks import process_meeting_job

    calls: list[dict] = []

    def fake_apply_async(**options):
        calls.append(options)
        return SimpleNamespace(id="msg-1")

    monkeypatch.setattr(process_meeting_job, "apply_async", fake_apply_async)

    assert (
        dispatcher.publish_processing_job("job-1", _settings(asr_profile=GPU), workspace_id="ws-1")
        == "msg-1"
    )
    assert dispatcher.publish_processing_job("job-2", _settings(asr_profile="cpu-base")) == "msg-1"
    assert dispatcher.enqueue_processing_job("job-3", _settings(asr_profile=GPU)) is True

    assert calls == [
        {"args": ["job-1"], "queue": "asr-gpu", "kwargs": {"workspace_id": "ws-1"}},
        {"args": ["job-2"], "queue": "asr-cpu"},
        {"args": ["job-3"], "queue": "asr-gpu"},
    ]


# --- A4: readiness follows the table ----------------------------------------------------


def test_worker_asr_queues_off_runs_no_probe() -> None:
    def no_probe(*_args, **_kwargs):
        raise AssertionError("no probe may run")

    record = assess(
        settings=_settings(asr_profile=GPU, worker_asr_queues="off"),
        probes=HostProbes(run=no_probe, find_spec=no_probe, ctranslate2=no_probe),
    )
    assert _verdict(record) == (False, "asr_queues_off", ())
    assert record.checks == []


def test_remote_requirement_is_qualified_for_asr_cpu_without_probes() -> None:
    def no_probe(*_args, **_kwargs):
        raise AssertionError("no probe may run")

    record = assess(
        settings=_settings(asr_provider="mock", allow_mock_providers=True),
        probes=HostProbes(run=no_probe, find_spec=no_probe, ctranslate2=no_probe),
    )
    assert _verdict(record) == (True, "no_local_model", ("asr-cpu",))
    assert record.requirement == "remote"
    assert record.checks == []


def test_cpu_requirement_asr_runtime_fail_refuses() -> None:
    record = assess(settings=_settings(asr_profile="cpu-base"), probes=probes(spec=None))
    assert _verdict(record) == (False, "asr_runtime_failed", ())
    assert record.requirement == "cpu"


def test_cpu_requirement_asr_runtime_pass_is_qualified() -> None:
    record = assess(settings=_settings(asr_profile="cpu-base"), probes=probes())
    assert _verdict(record) == (True, "ready", ("asr-cpu",))
    assert record.checks[0]["status"] == "pass"
    assert (record.device, record.compute_type, record.asr_profile) == ("cpu", "int8", "cpu-base")
    assert record.vram_required_mib is None and record.gpu_name is None


def test_cpu_requirement_asr_runtime_warn_is_qualified() -> None:
    # warn: WHISPER_LOCAL_FILES_ONLY without WHISPER_MODEL_PATH
    record = assess(
        settings=_settings(asr_profile="cpu-base", whisper_local_files_only=True), probes=probes()
    )
    assert record.checks[0]["status"] == "warn"
    assert _verdict(record) == (True, "ready", ("asr-cpu",))


def test_cpu_requirement_unsupported_compute_type_refuses() -> None:
    record = assess(
        settings=_settings(asr_profile="cpu-base"), probes=probes(ct2=FakeCT2(cpu={"float32"}))
    )
    assert _verdict(record) == (False, "asr_runtime_failed", ())


def test_gpu_requirement_asr_runtime_fail_refuses() -> None:
    record = assess(probes=p40(spec=None))
    assert _verdict(record) == (False, "asr_runtime_failed", ())


def test_gpu_requirement_without_cuda_is_refused_and_with_cuda_qualified() -> None:
    """Deny: the CPU-only worker (GPU requirement, no CUDA). Allow: CUDA works."""
    refused = assess(probes=p40(ct2=FakeCT2(count=0)))
    assert _verdict(refused) == (False, "asr_runtime_failed", ())
    qualified = assess(probes=p40())
    assert _verdict(qualified) == (True, "ready", ("asr-gpu", "asr-cpu"))


def test_gpu_requirement_no_ctranslate2_is_refused() -> None:
    none = worker_readiness.assess_worker_readiness(
        _settings(asr_profile=GPU),
        pool="prefork",
        concurrency=1,
        probes=HostProbes(
            run=lambda argv, **kw: _completed(_smi_line(0, FAKE_UUID_0, 20000)),
            find_spec=lambda name: object(),
            ctranslate2=lambda: None,
            environ={},
        ),
    )
    assert _verdict(none) == (False, "asr_runtime_failed", ())


def test_gpu_requirement_driver_fail_refuses() -> None:
    old = _smi_line(0, FAKE_UUID_0, 20000, driver="400.10.00")
    record = assess(probes=probes(smi=old))
    assert record.checks[0]["status"] == "pass"  # asr_runtime passed: the driver rule decides
    assert _verdict(record) == (False, "gpu_driver_failed", ())
    assert record.vram_required_mib == 3584


@pytest.mark.parametrize(
    "smi",
    [FileNotFoundError("nvidia-smi"), subprocess.TimeoutExpired(["nvidia-smi"], 5)],
    ids=["no-nvidia-smi", "timeout"],
)
def test_gpu_requirement_without_nvidia_smi_refuses_on_the_driver(smi) -> None:
    record = assess(probes=probes(smi=smi))
    assert _verdict(record) == (False, "gpu_driver_failed", ())


def test_gpu_requirement_unknown_vram_refuses() -> None:
    record = assess(
        settings=_settings(whisper_model="custom-x", whisper_device="cuda"), probes=p40()
    )
    assert [check["status"] for check in record.checks[:2]] == ["pass", "pass"]
    assert _verdict(record) == (False, "unknown_model_vram", ())
    assert record.vram_required_mib is None


def test_gpu_requirement_unknown_model_with_an_override_is_qualified() -> None:
    record = assess(
        settings=_settings(
            whisper_model="custom-x", whisper_device="cuda", asr_model_vram_mib=1000
        ),
        probes=p40(),
    )
    assert _verdict(record) == (True, "ready", ("asr-gpu", "asr-cpu"))
    assert record.vram_required_mib == 1000


def test_gpu_requirement_vram_budget_one_below_refuses_and_equal_passes() -> None:
    over = assess(settings=_settings(asr_profile=GPU, worker_vram_budget_mib=3583))
    assert [check["status"] for check in over.checks[:2]] == ["pass", "pass"]
    assert _verdict(over) == (False, "vram_over_budget", ())
    assert (over.vram_budget_mib, over.vram_required_mib) == (3583, 3584)
    assert over.vram_allowed_processes == 0

    exact = assess(settings=_settings(asr_profile=GPU, worker_vram_budget_mib=3584))
    assert _verdict(exact) == (True, "ready", ("asr-gpu", "asr-cpu"))
    assert exact.vram_allowed_processes == 1


def test_gpu_requirement_free_vram_one_below_refuses_and_equal_passes() -> None:
    below = assess(probes=p40(free=3583))
    assert [check["status"] for check in below.checks[:2]] == ["pass", "pass"]
    assert _verdict(below) == (False, "gpu_memory_failed", ())
    assert below.vram_free_mib == 3583
    assert below.vram_allowed_processes == 0

    equal = assess(probes=p40(free=3584))
    assert _verdict(equal) == (True, "ready", ("asr-gpu", "asr-cpu"))


def test_free_vram_is_checked_against_the_total_need() -> None:
    two = assess(concurrency=2, probes=p40(free=2 * 3584 - 1))
    assert _verdict(two) == (False, "gpu_memory_failed", ())
    assert two.vram_allowed_processes == 1  # the operator can set CELERY_CONCURRENCY=1
    fits = assess(concurrency=2, probes=p40(free=2 * 3584))
    assert _verdict(fits) == (True, "ready", ("asr-gpu", "asr-cpu"))


def test_cuda_visible_devices_pointing_at_a_missing_gpu_refuses() -> None:
    record = assess(probes=p40(environ={"CUDA_VISIBLE_DEVICES": "7"}))
    assert _verdict(record) == (False, "gpu_memory_failed", ())
    assert record.gpu_name is None and record.vram_free_mib is None

    allowed = assess(probes=p40(environ={"CUDA_VISIBLE_DEVICES": "0"}))
    assert _verdict(allowed) == (True, "ready", ("asr-gpu", "asr-cpu"))


def test_cuda_visible_devices_by_uuid_picks_that_gpu() -> None:
    two = "\n".join([_smi_line(0, FAKE_UUID_0, 1000), _smi_line(1, FAKE_UUID_1, 20000)])
    record = assess(probes=probes(smi=two, environ={"CUDA_VISIBLE_DEVICES": FAKE_UUID_1[:12]}))
    assert _verdict(record) == (True, "ready", ("asr-gpu", "asr-cpu"))
    assert record.vram_free_mib == 20000


def test_a_gpu_qualified_worker_reports_the_gpu() -> None:
    record = assess()
    assert record.queues == ("asr-gpu", "asr-cpu")
    assert record.requirement == "gpu"
    assert record.asr_profile == GPU
    assert (record.device, record.device_index, record.compute_type) == ("cuda", 0, "int8_float32")
    assert record.gpu_name == "Tesla P40"
    assert (record.vram_free_mib, record.vram_budget_mib, record.vram_required_mib) == (
        20000,
        0,
        3584,
    )
    assert record.vram_allowed_processes == 5
    assert record.processes == 1
    assert record.error_type is None
    assert [check["name"] for check in record.checks] == ["asr_runtime", "gpu_driver", "gpu_memory"]
    assert all(check["status"] == "pass" for check in record.checks)


def test_ram_decision_is_copied_into_the_record() -> None:
    within = assess(settings=_settings(asr_profile=GPU, worker_memory_budget_mib=5120))
    assert (within.ram_decision, within.ram_budget_mib, within.ram_per_process_mib) == (
        "within_budget",
        5120,
        5120,  # 512 worker base + 4608 model
    )
    assert within.qualified is True

    # RAM never changes the verdict: HOM-41 already clamps or refuses at start.
    over = assess(concurrency=2, settings=_settings(asr_profile=GPU, worker_memory_budget_mib=5120))
    assert over.ram_decision == "over_budget"
    assert _verdict(over) == (True, "ready", ("asr-gpu", "asr-cpu"))

    none = assess()
    assert (none.ram_decision, none.ram_budget_mib, none.ram_per_process_mib) == (
        "no_budget",
        0,
        None,
    )


def test_an_adapter_build_error_refuses_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.providers import factory
    from backend.providers.base import ProviderError

    def broken(_settings):
        raise ProviderError("adapter_failed", "the adapter cannot be built at " + PRIVATE_PATH)

    monkeypatch.setattr(factory, "create_asr_provider", broken)
    record = assess(settings=_settings(asr_profile="cpu-base"), probes=probes())
    assert _verdict(record) == (False, "asr_adapter_failed", ())
    assert PRIVATE_PATH not in json.dumps(record.to_dict())


def test_a_bundle_can_be_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.providers import factory

    settings = _settings(asr_profile=GPU)
    bundle = SimpleNamespace(asr=factory.create_asr_provider(settings))

    def no_build(_settings):
        raise AssertionError("the injected bundle must be used")

    monkeypatch.setattr(factory, "create_asr_provider", no_build)
    record = assess(settings=settings, bundle=bundle)
    assert _verdict(record) == (True, "ready", ("asr-gpu", "asr-cpu"))


# --- A5: the VRAM need ------------------------------------------------------------------


def test_the_gpu_profile_declares_its_vram_and_cpu_profiles_none() -> None:
    assert get_asr_profile(GPU).vram_mib == 3584
    for name, profile in ASR_PROFILES.items():
        if name.startswith("cpu-"):
            assert profile.vram_mib == 0, name


def test_vram_override_wins_over_the_profile() -> None:
    record = assess(settings=_settings(asr_profile=GPU, asr_model_vram_mib=2048))
    assert record.vram_required_mib == 2048


def test_vram_batch_slots_add_256_each() -> None:
    record = assess(settings=_settings(asr_profile=GPU, asr_batch_size=3))
    assert record.vram_required_mib == 3584 + 2 * 256


def test_vram_prefork_concurrency_multiplies() -> None:
    two = assess(concurrency=2)
    assert two.processes == 2
    assert two.vram_required_mib == 2 * 3584
    assert two.qualified is True


@pytest.mark.parametrize("pool", ["solo", "threads"])
def test_vram_solo_and_threads_count_one_process(pool: str) -> None:
    record = assess(pool=pool, concurrency=4)
    assert record.processes == 1
    assert record.vram_required_mib == 3584


def test_vram_model_name_table_is_the_stand_in_without_a_profile() -> None:
    record = assess(
        settings=_settings(whisper_model="large-v3", whisper_device="cuda"), probes=p40()
    )
    assert record.vram_required_mib == 3584


# --- A6: readiness never raises ---------------------------------------------------------


def test_an_exception_inside_a_probe_is_a_readiness_error() -> None:
    def boom(name: str):
        raise RuntimeError(PRIVATE_MESSAGE)

    record = assess(
        probes=HostProbes(
            run=lambda argv, **kw: _completed(_smi_line(0, FAKE_UUID_0, 20000)),
            find_spec=boom,
            ctranslate2=lambda: FakeCT2(),
            environ={},
        )
    )
    assert _verdict(record) == (False, "readiness_error", ())
    assert record.error_type == "RuntimeError"
    assert PRIVATE_MESSAGE not in json.dumps(record.to_dict())


def test_an_exception_inside_nvidia_smi_is_a_readiness_error() -> None:
    record = assess(probes=probes(smi=KeyError("x")))
    assert _verdict(record) == (False, "readiness_error", ())
    assert record.error_type == "KeyError"


# --- A7: the subprocess ctranslate2 probe ------------------------------------------------


def _ct2_json(count: int = 1, cuda: list | None = None) -> str:
    return json.dumps(
        {
            "cuda_device_count": count,
            "cpu_compute_types": ["float32", "int8"],
            "cuda_compute_types": cuda if cuda is not None else [["float32", "int8_float32"]],
        }
    )


def test_subprocess_ctranslate2_reads_a_fresh_interpreter() -> None:
    sys.modules.pop("ctranslate2", None)
    calls: list[tuple[list[str], dict]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _completed(_ct2_json())

    result = worker_readiness.subprocess_ctranslate2(run=fake_run)

    assert "ctranslate2" not in sys.modules
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[:2] == [sys.executable, "-c"] and len(argv) == 3
    assert "import ctranslate2" in argv[2]
    assert kwargs.get("timeout") == 60
    assert kwargs.get("shell", False) is False
    assert result.get_cuda_device_count() == 1
    assert set(result.get_supported_compute_types("cpu")) == {"float32", "int8"}
    assert set(result.get_supported_compute_types("cuda", 0)) == {"float32", "int8_float32"}
    assert set(result.get_supported_compute_types("cuda", device_index=0)) == {
        "float32",
        "int8_float32",
    }
    with pytest.raises(ValueError):
        result.get_supported_compute_types("cuda", 3)


def test_default_probes_use_the_subprocess_probe() -> None:
    assert worker_readiness.default_probes().ctranslate2 is worker_readiness.subprocess_ctranslate2


def test_default_probes_reach_a_patched_subprocess_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runner is looked up at call time, so patching subprocess.run reaches
    the child call made through default_probes()."""
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return _completed(_ct2_json(count=2, cuda=[["int8"], ["float32"]]))

    monkeypatch.setattr(subprocess, "run", fake_run)
    facts = worker_readiness.default_probes().ctranslate2()

    assert len(calls) == 1 and calls[0][:2] == [sys.executable, "-c"]
    assert facts.get_cuda_device_count() == 2
    assert set(facts.get_supported_compute_types("cuda", 1)) == {"float32"}


FAKE_CTRANSLATE2 = """\
def get_cuda_device_count():
    return 2


def get_supported_compute_types(device, device_index=0):
    if device == "cpu":
        return {"int8", "float32"}
    return [{"int8_float32", "float32"}, {"float32"}][device_index]
"""


def test_a_real_child_reads_a_fake_ctranslate2(tmp_path: Path) -> None:
    """A real fresh interpreter that imports a fake ``ctranslate2`` module from
    ``PYTHONPATH``: the child script and the JSON parsing are real, and this
    process never imports the module."""
    import os

    (tmp_path / "ctranslate2.py").write_text(FAKE_CTRANSLATE2, encoding="utf-8")

    def runner(argv, **kwargs):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            path for path in (str(tmp_path), env.get("PYTHONPATH", "")) if path
        )
        return subprocess.run(argv, env=env, **kwargs)  # noqa: S603 - the probe's own argv

    sys.modules.pop("ctranslate2", None)
    facts = worker_readiness.subprocess_ctranslate2(run=runner)

    assert "ctranslate2" not in sys.modules
    assert facts is not None
    assert facts.get_cuda_device_count() == 2
    assert set(facts.get_supported_compute_types("cpu")) == {"int8", "float32"}
    assert set(facts.get_supported_compute_types("cuda", 0)) == {"int8_float32", "float32"}
    assert set(facts.get_supported_compute_types("cuda", 1)) == {"float32"}


@pytest.mark.parametrize(
    "completed",
    [
        _completed("", returncode=1),  # the import failed in the child
        _completed("not json"),
        _completed('{"cuda_device_count": "x"}'),
        _completed('{"cuda_device_count": 1, "cpu_compute_types": ["int8"]}'),
        _completed(
            json.dumps(
                {
                    "cuda_device_count": 2,
                    "cpu_compute_types": ["int8"],
                    "cuda_compute_types": [["int8"]],
                }
            )
        ),
        _completed("[]"),
    ],
    ids=["exit-1", "not-json", "bad-count", "missing-cuda", "count-mismatch", "not-an-object"],
)
def test_a_failing_or_garbled_child_gives_none(completed: subprocess.CompletedProcess) -> None:
    assert worker_readiness.subprocess_ctranslate2(run=lambda argv, **kw: completed) is None


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired(cmd=["python"], timeout=60),
        FileNotFoundError("python"),
        OSError("x"),
    ],
    ids=["timeout", "missing", "oserror"],
)
def test_a_child_that_cannot_run_gives_none(error: BaseException) -> None:
    def fake_run(argv, **kwargs):
        raise error

    assert worker_readiness.subprocess_ctranslate2(run=fake_run) is None


def test_the_subprocess_facts_drive_the_runtime_check() -> None:
    facts = worker_readiness.subprocess_ctranslate2(run=lambda argv, **kw: _completed(_ct2_json()))
    record = assess(probes=p40(ct2=facts))
    assert _verdict(record) == (True, "ready", ("asr-gpu", "asr-cpu"))
    none_on_cuda = worker_readiness.subprocess_ctranslate2(
        run=lambda argv, **kw: _completed(_ct2_json(count=0, cuda=[]))
    )
    assert _verdict(assess(probes=p40(ct2=none_on_cuda))) == (False, "asr_runtime_failed", ())


# --- A8: queue selection in worker_init -------------------------------------------------


class _FakeSender:
    """A WorkController stand-in with a real Celery ``Queues`` map."""

    def __init__(
        self,
        *,
        selected: list[str] | None = None,
        concurrency: int = 1,
        pool_cls: Any = "prefork",
        autoscale: Any = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        from celery.app.amqp import Queues
        from kombu import Queue

        self.queues = Queues([Queue(name, routing_key=name) for name in ALL_QUEUES])
        if selected:
            self.queues.select(selected)  # what --queues does (WorkController.setup_queues)
        self.concurrency = concurrency
        self.pool_cls = pool_cls
        self.autoscale = autoscale
        self.options = options
        self.app = SimpleNamespace(amqp=SimpleNamespace(queues=self.queues))

    @property
    def consumed(self) -> set[str]:
        return set(self.queues.consume_from)


def _patch_readiness(monkeypatch: pytest.MonkeyPatch, record: worker_readiness.WorkerReadiness):
    calls: list[dict] = []

    def fake_assess(settings, *, pool, concurrency, probes=None, bundle=None):
        calls.append({"pool": pool, "concurrency": concurrency, "probes": probes})
        return record

    monkeypatch.setattr(worker_readiness, "assess_worker_readiness", fake_assess)
    monkeypatch.setattr(worker_readiness, "default_probes", lambda: "fake-probes")
    return calls


def _use_settings(monkeypatch: pytest.MonkeyPatch, celery_module, settings: Settings) -> None:
    monkeypatch.setattr(celery_module, "get_settings", lambda: settings)


def test_gpu_qualified_worker_with_queues_processing_consumes_all_three(
    monkeypatch: pytest.MonkeyPatch, celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    record = worker_readiness.WorkerReadiness(**READY_GPU)
    calls = _patch_readiness(monkeypatch, record)
    _use_settings(monkeypatch, celery_module, _settings(asr_profile=GPU))
    sender = _FakeSender(selected=["processing"], concurrency=2)

    with caplog.at_level(logging.INFO):
        celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.consumed == {"processing", "asr-gpu", "asr-cpu"}
    assert calls == [{"pool": "prefork", "concurrency": 2, "probes": "fake-probes"}]
    assert celery_module.current_worker_readiness() is record
    assert not _messages(caplog, "worker_queue_refused")


def test_the_real_readiness_drives_the_selection(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    """No fake assessment: fake probes only, deny and allow side by side."""
    _use_settings(monkeypatch, celery_module, _settings(asr_profile=GPU))
    monkeypatch.setattr(worker_readiness, "default_probes", lambda: p40())
    gpu = _FakeSender(selected=["processing"])
    celery_module.on_worker_init(signal=None, sender=gpu)
    assert gpu.consumed == {"processing", "asr-gpu", "asr-cpu"}

    monkeypatch.setattr(worker_readiness, "default_probes", lambda: p40(ct2=FakeCT2(count=0)))
    cpu_only = _FakeSender(selected=["processing"])
    celery_module.on_worker_init(signal=None, sender=cpu_only)
    assert cpu_only.consumed == {"processing"}
    assert celery_module.current_worker_readiness().reason == "asr_runtime_failed"


def test_cpu_only_worker_consumes_only_processing(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    record = worker_readiness.WorkerReadiness(
        qualified=False, reason="gpu_driver_failed", requirement="gpu", queues=()
    )
    _patch_readiness(monkeypatch, record)
    _use_settings(monkeypatch, celery_module, _settings(asr_profile=GPU))
    sender = _FakeSender(selected=["processing"])

    celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.consumed == {"processing"}


def test_a_cpu_qualified_worker_takes_asr_cpu_only(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    _patch_readiness(monkeypatch, worker_readiness.WorkerReadiness(**READY_CPU))
    _use_settings(monkeypatch, celery_module, _settings())
    sender = _FakeSender(selected=["processing"])

    celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.consumed == {"processing", "asr-cpu"}


def test_explicit_asr_gpu_on_a_cpu_worker_is_refused_but_never_empty(
    monkeypatch: pytest.MonkeyPatch, celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    record = worker_readiness.WorkerReadiness(
        qualified=False, reason="gpu_driver_failed", requirement="gpu", queues=()
    )
    _patch_readiness(monkeypatch, record)
    _use_settings(monkeypatch, celery_module, _settings(asr_profile=GPU))
    sender = _FakeSender(selected=["asr-gpu"])

    with caplog.at_level(logging.INFO):
        celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.consumed == {"processing"}
    refused = _messages(caplog, "worker_queue_refused")
    assert len(refused) == 1
    assert refused[0].levelno == logging.ERROR
    assert refused[0].getMessage() == "worker_queue_refused queue=asr-gpu reason=gpu_driver_failed"


def test_explicit_asr_gpu_on_a_cpu_qualified_worker_is_refused_and_asr_cpu_added(
    monkeypatch: pytest.MonkeyPatch, celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_readiness(monkeypatch, worker_readiness.WorkerReadiness(**READY_CPU))
    _use_settings(monkeypatch, celery_module, _settings())
    sender = _FakeSender(selected=["processing", "asr-gpu"])

    with caplog.at_level(logging.INFO):
        celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.consumed == {"processing", "asr-cpu"}
    assert len(_messages(caplog, "worker_queue_refused queue=asr-gpu reason=ready")) == 1


def test_explicit_asr_gpu_on_a_gpu_qualified_worker_is_kept(
    monkeypatch: pytest.MonkeyPatch, celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_readiness(monkeypatch, worker_readiness.WorkerReadiness(**READY_GPU))
    _use_settings(monkeypatch, celery_module, _settings(asr_profile=GPU))
    sender = _FakeSender(selected=["asr-gpu"])

    with caplog.at_level(logging.INFO):
        celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.consumed == {"asr-gpu", "asr-cpu"}
    assert not _messages(caplog, "worker_queue_refused")


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (
            worker_readiness.WorkerReadiness(
                qualified=False, reason="asr_runtime_failed", requirement="cpu", queues=()
            ),
            {"processing"},
        ),
        (
            worker_readiness.WorkerReadiness(
                qualified=False, reason="gpu_memory_failed", requirement="gpu", queues=()
            ),
            {"processing"},
        ),
        (worker_readiness.WorkerReadiness(**READY_CPU), {"processing", "asr-cpu"}),
        (worker_readiness.WorkerReadiness(**READY_GPU), {"processing", "asr-cpu", "asr-gpu"}),
    ],
    ids=["cpu-unqualified", "gpu-unqualified", "cpu-ready", "gpu-ready"],
)
def test_a_worker_without_queues_consumes_only_what_it_is_qualified_for(
    monkeypatch: pytest.MonkeyPatch, celery_module, caplog, record, expected
) -> None:
    _patch_readiness(monkeypatch, record)
    _use_settings(monkeypatch, celery_module, _settings())
    sender = _FakeSender()  # no --queues: Celery would consume every declared queue
    assert sender.consumed == set(ALL_QUEUES)

    with caplog.at_level(logging.INFO):
        celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.consumed == expected
    assert sender.queues.consume_from is not sender.queues  # an explicit selection
    assert not _messages(caplog, "worker_queue_refused")  # the operator named no ASR queue


def test_worker_asr_queues_off_consumes_no_asr_queue(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    _use_settings(monkeypatch, celery_module, _settings(asr_profile=GPU, worker_asr_queues="off"))
    monkeypatch.setattr(worker_readiness, "default_probes", lambda: p40())
    for selected in (["processing"], None):
        sender = _FakeSender(selected=selected)
        celery_module.on_worker_init(signal=None, sender=sender)
        assert sender.consumed == {"processing"}
        assert celery_module.current_worker_readiness().reason == "asr_queues_off"


def test_other_explicit_queues_are_kept(monkeypatch: pytest.MonkeyPatch, celery_module) -> None:
    _patch_readiness(monkeypatch, worker_readiness.WorkerReadiness(**READY_CPU))
    _use_settings(monkeypatch, celery_module, _settings())
    sender = _FakeSender(selected=["processing", "celery@host.dq2"])

    celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.consumed == {"processing", "celery@host.dq2", "asr-cpu"}


def test_worker_init_without_an_app_skips_readiness(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    """The HOM-41 fake senders have no app: nothing to select, nothing stored."""
    calls = _patch_readiness(monkeypatch, worker_readiness.WorkerReadiness(**READY_CPU))
    _use_settings(monkeypatch, celery_module, _settings())
    sender = SimpleNamespace(concurrency=1, pool_cls="prefork", autoscale=None)

    celery_module.on_worker_init(signal=None, sender=sender)

    assert celery_module.current_worker_readiness() is None
    assert calls == []


def test_a_crashing_assessment_still_never_selects_an_unqualified_asr_queue(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    def crash(*_args, **_kwargs):
        raise RuntimeError(PRIVATE_MESSAGE)

    monkeypatch.setattr(worker_readiness, "assess_worker_readiness", crash)
    _use_settings(monkeypatch, celery_module, _settings())
    sender = _FakeSender()

    celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.consumed == {"processing"}
    stored = celery_module.current_worker_readiness()
    assert (stored.reason, stored.error_type, stored.queues) == (
        "readiness_error",
        "RuntimeError",
        (),
    )


class _Channel:
    """The few channel calls a kombu Consumer makes when it declares its queues."""

    def __init__(self) -> None:
        self.declared: list[str] = []

    def exchange_declare(self, *args, **kwargs):
        return None

    def queue_declare(self, queue, *args, **kwargs):
        self.declared.append(queue)
        return SimpleNamespace(queue=queue, message_count=0, consumer_count=0)

    def queue_bind(self, *args, **kwargs):
        return None


def test_the_task_consumer_uses_exactly_the_applied_selection(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    """worker_init selects on the app's Queues; Celery's Tasks bootstep builds
    the task consumer with ``app.amqp.TaskConsumer(connection)``, which reads
    ``queues.consume_from``. Both sides use the real app here."""
    _patch_readiness(monkeypatch, worker_readiness.WorkerReadiness(**READY_CPU))
    _use_settings(monkeypatch, celery_module, _settings())
    app = celery_module.celery_app
    sender = SimpleNamespace(app=app, concurrency=1, pool_cls="solo", autoscale=None, options={})
    app.amqp.queues._consume_from = None  # a worker started without --queues

    celery_module.on_worker_init(signal=None, sender=sender)
    consumer = app.amqp.TaskConsumer(_Channel(), auto_declare=False)

    assert {queue.name for queue in consumer.queues} == {"processing", "asr-cpu"}


# --- A9: HOM-41 memory clamp first ------------------------------------------------------


def test_readiness_sees_the_clamped_concurrency(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    calls = _patch_readiness(monkeypatch, worker_readiness.WorkerReadiness(**READY_GPU))
    _use_settings(
        monkeypatch, celery_module, _settings(asr_profile=GPU, worker_memory_budget_mib=5120)
    )
    sender = _FakeSender(concurrency=2)

    celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.concurrency == 1  # HOM-41 clamp
    assert calls[0]["concurrency"] == 1  # readiness sees the final count


def test_readiness_sees_the_clamped_autoscale_max(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    calls = _patch_readiness(monkeypatch, worker_readiness.WorkerReadiness(**READY_GPU))
    _use_settings(
        monkeypatch, celery_module, _settings(asr_profile=GPU, worker_memory_budget_mib=5120)
    )
    sender = _FakeSender(concurrency=4, autoscale=(4, 2))

    celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.autoscale == [1, 1]
    assert calls[0]["concurrency"] == 1


def test_readiness_sees_the_autoscale_max_within_budget(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    calls = _patch_readiness(monkeypatch, worker_readiness.WorkerReadiness(**READY_GPU))
    _use_settings(monkeypatch, celery_module, _settings(asr_profile=GPU))
    sender = _FakeSender(concurrency=1, options={"autoscale": "3,1"})

    celery_module.on_worker_init(signal=None, sender=sender)

    assert calls[0]["concurrency"] == 3


def test_a_memory_refusal_exits_78_before_readiness_runs(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    calls = _patch_readiness(monkeypatch, worker_readiness.WorkerReadiness(**READY_GPU))
    _use_settings(
        monkeypatch, celery_module, _settings(asr_profile=GPU, worker_memory_budget_mib=4096)
    )
    sender = _FakeSender(selected=["processing"], concurrency=2)

    with pytest.raises(SystemExit) as exc:
        celery_module.on_worker_init(signal=None, sender=sender)

    assert exc.value.code == 78
    assert calls == []
    assert celery_module.current_worker_readiness() is None
    assert sender.consumed == {"processing"}


# --- A10: the task guard ----------------------------------------------------------------


def _readiness(queues: tuple[str, ...], **kwargs: Any) -> worker_readiness.WorkerReadiness:
    values: dict[str, Any] = dict(
        qualified=bool(queues), reason="ready" if queues else "gpu_memory_failed", requirement="gpu"
    )
    values.update(kwargs)
    return worker_readiness.WorkerReadiness(queues=queues, **values)


GPU_AND_CPU = ("asr-gpu", "asr-cpu")
CPU_ONLY = ("asr-cpu",)


@pytest.mark.parametrize(
    ("queues", "delivery", "profile", "expected"),
    [
        (GPU_AND_CPU, "asr-gpu", GPU, "run"),
        (GPU_AND_CPU, "asr-cpu", GPU, "run"),
        (CPU_ONLY, "asr-gpu", GPU, "requeue"),  # deny: a CPU worker gets asr-gpu
        (CPU_ONLY, "asr-cpu", GPU, "run"),  # allow: the same worker gets asr-cpu
        ((), "asr-cpu", "cpu-base", "requeue"),
        ((), "asr-gpu", "cpu-base", "requeue"),
        (GPU_AND_CPU, "processing", GPU, "run"),  # legacy message, qualified
        (CPU_ONLY, "processing", GPU, "reroute"),  # legacy, GPU target not held
        (CPU_ONLY, "processing", "cpu-base", "run"),  # legacy, CPU target held
        ((), "processing", "cpu-base", "reroute"),  # legacy, unqualified
        (CPU_ONLY, None, GPU, "reroute"),  # no routing key
        (GPU_AND_CPU, None, GPU, "run"),
        ((), "unknown-queue", "cpu-base", "reroute"),
    ],
)
def test_asr_delivery_decision_matrix(queues, delivery, profile, expected) -> None:
    record = _readiness(queues)
    settings = _settings(asr_profile=profile)
    assert worker_readiness.asr_delivery_decision(record, delivery, settings) == expected


class _TaskRun:
    """Runs process_meeting_job inside a real Celery request context, with fakes
    for the payload check, the processor and the publisher."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
        from backend.jobs import dispatcher, outbox, processor, tasks

        self.published: list[dict] = []
        self.processed: list[str] = []
        self.verified: list[str] = []
        self.publish_error: BaseException | None = None

        def fake_verify(job_id, workspace_id):
            self.verified.append(job_id)
            return True

        def fake_process(job_id, worker_id):
            self.processed.append(job_id)
            return {"job_id": job_id, "status": "completed"}

        def fake_publish(job_id, publish_settings, *, workspace_id=None):
            if self.publish_error is not None:
                raise self.publish_error
            self.published.append(
                {
                    "job_id": job_id,
                    "workspace_id": workspace_id,
                    "queue": worker_readiness.asr_queue_for(publish_settings),
                }
            )
            return "msg-new"

        # A fake control API (no broker): records cancel_consumer calls.
        self.cancelled: list[tuple[str, Any]] = []
        self.cancel_error: BaseException | None = None

        def fake_cancel_consumer(queue, **kwargs):
            if self.cancel_error is not None:
                raise self.cancel_error
            self.cancelled.append((queue, kwargs.get("destination")))

        monkeypatch.setattr(outbox, "verify_job_payload", fake_verify)
        monkeypatch.setattr(processor, "process_job", fake_process)
        monkeypatch.setattr(dispatcher, "publish_processing_job", fake_publish)
        monkeypatch.setattr(tasks, "get_settings", lambda: settings)
        monkeypatch.setattr(
            tasks.celery_app, "control", SimpleNamespace(cancel_consumer=fake_cancel_consumer)
        )
        self.task = tasks.process_meeting_job

    def __call__(self, routing_key: str | None, *, is_eager: bool = False):
        delivery_info = {"routing_key": routing_key} if routing_key is not None else None
        # called_directly=False: what Celery's tracer sets for a broker delivery.
        self.task.push_request(
            id="delivery-1",
            delivery_info=delivery_info,
            is_eager=is_eager,
            called_directly=False,
            hostname=WORKER_HOSTNAME,
        )
        try:
            return self.task.run("job-1", workspace_id="ws-1")
        finally:
            self.task.pop_request()


def test_the_task_runs_when_the_queue_is_held(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    celery_module._worker_readiness = _readiness(GPU_AND_CPU)
    run = _TaskRun(monkeypatch, _settings(asr_profile=GPU))

    assert run("asr-gpu") == {"job_id": "job-1", "status": "completed"}
    assert run.published == [] and run.processed == ["job-1"] and run.verified == ["job-1"]
    assert run.cancelled == []


def test_a_legacy_message_on_an_unqualified_worker_is_rerouted(
    monkeypatch: pytest.MonkeyPatch, celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    celery_module._worker_readiness = _readiness(CPU_ONLY)
    run = _TaskRun(monkeypatch, _settings(asr_profile=GPU))

    with caplog.at_level(logging.INFO):
        result = run("processing")

    assert result == {"job_id": "job-1", "status": "rerouted", "queue": "asr-gpu"}
    assert run.published == [{"job_id": "job-1", "workspace_id": "ws-1", "queue": "asr-gpu"}]
    assert run.processed == [] and run.verified == []  # the guard runs before both
    assert run.cancelled == []  # processing stays consumed (the sweeps run there)
    rerouted = _messages(caplog, "asr_job_rerouted")
    assert [record.getMessage() for record in rerouted] == [
        "asr_job_rerouted from=processing to=asr-gpu"
    ]


def test_a_legacy_message_on_a_qualified_worker_runs(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    celery_module._worker_readiness = _readiness(GPU_AND_CPU)
    run = _TaskRun(monkeypatch, _settings(asr_profile=GPU))

    assert run("processing") == {"job_id": "job-1", "status": "completed"}
    assert run.published == [] and run.processed == ["job-1"]


def test_an_asr_delivery_the_worker_does_not_hold_is_requeued(
    monkeypatch: pytest.MonkeyPatch, celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    celery_module._worker_readiness = _readiness(CPU_ONLY)
    run = _TaskRun(monkeypatch, _settings(asr_profile=GPU))

    with caplog.at_level(logging.INFO):
        with pytest.raises(Reject) as exc:
            run("asr-gpu")

    assert exc.value.requeue is True
    assert run.processed == [] and run.verified == [] and run.published == []
    requeued = _messages(caplog, "asr_job_requeued")
    assert len(requeued) == 1
    assert requeued[0].levelno == logging.WARNING
    assert requeued[0].getMessage() == "asr_job_requeued queue=asr-gpu reason=ready"
    # This worker stops consuming asr-gpu, so the message goes to a qualified worker.
    assert run.cancelled == [("asr-gpu", [WORKER_HOSTNAME])]
    assert [r.getMessage() for r in _messages(caplog, "asr_consumer_cancelled")] == [
        "asr_consumer_cancelled queue=asr-gpu"
    ]


def test_a_failed_consumer_cancel_still_requeues(
    monkeypatch: pytest.MonkeyPatch, celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    celery_module._worker_readiness = _readiness(CPU_ONLY)
    run = _TaskRun(monkeypatch, _settings(asr_profile=GPU))
    run.cancel_error = ConnectionError(PRIVATE_MESSAGE)

    with caplog.at_level(logging.INFO):
        with pytest.raises(Reject) as exc:
            run("asr-gpu")

    assert exc.value.requeue is True
    assert run.processed == [] and run.published == []
    failed = _messages(caplog, "asr_consumer_cancel_failed")
    assert [record.getMessage() for record in failed] == [
        "asr_consumer_cancel_failed queue=asr-gpu error=ConnectionError"
    ]
    assert not _messages(caplog, "asr_consumer_cancelled")
    assert PRIVATE_MESSAGE not in caplog.text


def test_a_failed_reroute_publish_keeps_the_message(
    monkeypatch: pytest.MonkeyPatch, celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    celery_module._worker_readiness = _readiness(())
    run = _TaskRun(monkeypatch, _settings(asr_profile=GPU))
    run.publish_error = ConnectionError(PRIVATE_MESSAGE)

    with caplog.at_level(logging.INFO):
        with pytest.raises(Reject) as exc:
            run("processing")

    assert exc.value.requeue is True
    assert run.processed == []
    assert run.cancelled == []  # a legacy processing delivery cancels nothing
    failed = _messages(caplog, "asr_job_reroute_failed")
    assert [record.getMessage() for record in failed] == [
        "asr_job_reroute_failed from=processing to=asr-gpu error=ConnectionError"
    ]
    assert PRIVATE_MESSAGE not in caplog.text


def test_eager_mode_skips_the_guard(monkeypatch: pytest.MonkeyPatch, celery_module) -> None:
    celery_module._worker_readiness = _readiness(())  # would requeue or reroute
    monkeypatch.setattr(
        worker_readiness,
        "assess_worker_readiness",
        lambda *a, **kw: pytest.fail("eager mode must not assess"),
    )
    run = _TaskRun(monkeypatch, _settings(asr_profile=GPU))

    assert run("asr-gpu", is_eager=True) == {"job_id": "job-1", "status": "completed"}
    assert run(None, is_eager=True) == {"job_id": "job-1", "status": "completed"}
    assert run.processed == ["job-1", "job-1"] and run.published == []


def test_a_direct_call_skips_the_guard(monkeypatch: pytest.MonkeyPatch, celery_module) -> None:
    """``task.run(...)`` outside a worker (as test_dispatch_outbox and
    test_job_leases do) has no broker delivery: nothing to route."""
    celery_module._worker_readiness = None
    monkeypatch.setattr(
        worker_readiness,
        "assess_worker_readiness",
        lambda *a, **kw: pytest.fail("a direct call must not assess"),
    )
    run = _TaskRun(monkeypatch, _settings(asr_profile=GPU))

    assert run.task.run("job-1", workspace_id="ws-1") == {"job_id": "job-1", "status": "completed"}
    assert run.processed == ["job-1"] and run.published == []
    assert celery_module.current_worker_readiness() is None  # nothing stored


def test_a_task_without_stored_readiness_assesses_once_and_keeps_it(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    record = _readiness(CPU_ONLY, requirement="cpu")
    calls: list[dict] = []

    def fake_assess(settings, *, pool, concurrency, probes=None, bundle=None):
        calls.append({"pool": pool, "concurrency": concurrency, "probes": probes})
        return record

    monkeypatch.setattr(worker_readiness, "assess_worker_readiness", fake_assess)
    monkeypatch.setattr(worker_readiness, "default_probes", lambda: "fake-probes")
    run = _TaskRun(monkeypatch, _settings())

    assert run("asr-cpu") == {"job_id": "job-1", "status": "completed"}
    assert run("processing") == {"job_id": "job-1", "status": "completed"}

    assert calls == [{"pool": "solo", "concurrency": 1, "probes": "fake-probes"}]
    assert celery_module.current_worker_readiness() is record


# --- A11: the warm step -----------------------------------------------------------------


@pytest.fixture
def warm_calls(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from backend.providers import warm_models

    calls: list[Any] = []
    monkeypatch.setattr(warm_models, "warm_configured_asr", lambda settings: calls.append(settings))
    return calls


@pytest.mark.parametrize(
    "record",
    [
        worker_readiness.WorkerReadiness(
            qualified=False, reason="asr_queues_off", requirement="cpu", queues=()
        ),
        worker_readiness.WorkerReadiness(
            qualified=False, reason="gpu_memory_failed", requirement="gpu", queues=()
        ),
    ],
    ids=["off", "gpu-unqualified"],
)
def test_a_stored_readiness_without_asr_queues_skips_the_warm(
    monkeypatch: pytest.MonkeyPatch, celery_module, caplog, warm_calls, record
) -> None:
    celery_module._worker_readiness = record
    _use_settings(monkeypatch, celery_module, _settings())

    with caplog.at_level(logging.INFO):
        celery_module.on_worker_process_init(signal=None, sender=None)

    assert warm_calls == []
    assert [r.getMessage() for r in _messages(caplog, "asr_warm_skipped")] == [
        f"asr_warm_skipped reason={record.reason}"
    ]


@pytest.mark.parametrize("queues", [CPU_ONLY, GPU_AND_CPU, ("asr-gpu",)])
def test_a_stored_readiness_with_asr_queues_warms(
    monkeypatch: pytest.MonkeyPatch, celery_module, caplog, warm_calls, queues
) -> None:
    celery_module._worker_readiness = _readiness(queues)
    _use_settings(monkeypatch, celery_module, _settings())

    with caplog.at_level(logging.INFO):
        celery_module.on_worker_process_init(signal=None, sender=None)

    assert len(warm_calls) == 1
    assert not _messages(caplog, "asr_warm_skipped")


def test_without_a_stored_readiness_it_warms_as_today(
    monkeypatch: pytest.MonkeyPatch, celery_module, warm_calls
) -> None:
    _use_settings(monkeypatch, celery_module, _settings())

    celery_module.on_worker_process_init(signal=None, sender=None)

    assert len(warm_calls) == 1


# --- worker_ready logging and A12: the inspect command -----------------------------------


def test_worker_ready_logs_the_readiness_event(
    monkeypatch: pytest.MonkeyPatch, celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    record = assess()
    celery_module._worker_readiness = record

    with caplog.at_level(logging.INFO):
        celery_module.on_worker_ready(signal=None, sender=None)

    events = _messages(caplog, "worker_readiness ")
    assert len(events) == 1
    assert events[0].levelno == logging.INFO
    message = events[0].getMessage()
    for field in (
        "qualified=True",
        "reason=ready",
        "requirement=gpu",
        "queues=asr-gpu,asr-cpu",
        f"asr_profile={GPU}",
        "device=cuda",
        "device_index=0",
        "compute_type=int8_float32",
        "processes=1",
        "vram_required_mib=3584",
        "vram_free_mib=20000",
        "vram_budget_mib=0",
        "vram_allowed_processes=5",
        "gpu_name='Tesla P40'",
        "ram_budget_mib=0",
        "ram_per_process_mib=None",
    ):
        assert field in message, field
    # key=value parsing keeps a GPU name with spaces in one value.
    import shlex

    pairs = dict(token.split("=", 1) for token in shlex.split(message)[1:])
    assert pairs["gpu_name"] == "Tesla P40"
    assert pairs["queues"] == "asr-gpu,asr-cpu"
    assert not _messages(caplog, "worker_not_qualified")
    assert celery_module.current_worker_readiness() is record  # kept stored


@pytest.mark.parametrize("requirement", ["cpu", "gpu"])
def test_worker_ready_logs_not_qualified_for_cpu_or_gpu(
    celery_module, caplog: pytest.LogCaptureFixture, requirement: str
) -> None:
    record = worker_readiness.WorkerReadiness(
        qualified=False, reason="asr_runtime_failed", requirement=requirement, queues=()
    )
    celery_module._worker_readiness = record

    with caplog.at_level(logging.INFO):
        celery_module.on_worker_ready(signal=None, sender=None)

    errors = _messages(caplog, "worker_not_qualified")
    assert [r.getMessage() for r in errors] == [
        f"worker_not_qualified reason=asr_runtime_failed requirement={requirement}"
    ]
    assert errors[0].levelno == logging.ERROR
    assert len(_messages(caplog, "worker_readiness ")) == 1
    assert celery_module.current_worker_readiness() is record


def test_worker_ready_logs_no_error_for_a_remote_requirement(
    celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    celery_module._worker_readiness = worker_readiness.WorkerReadiness(
        qualified=True, reason="no_local_model", requirement="remote", queues=CPU_ONLY
    )
    with caplog.at_level(logging.INFO):
        celery_module.on_worker_ready(signal=None, sender=None)
    assert not _messages(caplog, "worker_not_qualified")


def test_worker_ready_without_readiness_logs_nothing(
    celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        celery_module.on_worker_ready(signal=None, sender=None)
    assert not _messages(caplog, "worker_readiness")
    assert not _messages(caplog, "worker_not_qualified")


def test_worker_ready_keeps_the_hom41_memory_log(
    monkeypatch: pytest.MonkeyPatch, celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_readiness(monkeypatch, worker_readiness.WorkerReadiness(**READY_GPU))
    _use_settings(
        monkeypatch, celery_module, _settings(asr_profile=GPU, worker_memory_budget_mib=5120)
    )
    celery_module.on_worker_init(signal=None, sender=_FakeSender())
    caplog.clear()

    with caplog.at_level(logging.INFO):
        celery_module.on_worker_ready(signal=None, sender=None)

    assert len(_messages(caplog, "worker_memory_check")) == 1
    assert len(_messages(caplog, "worker_readiness ")) == 1


def test_the_worker_readiness_inspect_command(celery_module) -> None:
    from celery.worker.control import Panel

    command = Panel.data["worker_readiness"]
    assert Panel.meta["worker_readiness"].type == "inspect"
    assert command(None) == {"reason": "unknown"}

    record = assess()
    celery_module._worker_readiness = record
    reply = command(None)
    assert reply == record.to_dict()
    assert json.loads(json.dumps(reply)) == reply  # JSON-serialisable as it is


# --- A13: metrics and system health -----------------------------------------------------


class _FakeRedis:
    def __init__(self, lengths: dict[str, int], *, fail: bool = False) -> None:
        self.lengths = lengths
        self.fail = fail
        self.calls: list[str] = []

    def ping(self) -> bool:
        if self.fail:
            raise ConnectionError("redis down")
        return True

    def llen(self, name: str) -> int:
        self.calls.append(name)
        return self.lengths.get(name, 0)

    def close(self) -> None:
        pass


def _use_redis(monkeypatch: pytest.MonkeyPatch, fake: _FakeRedis) -> None:
    import redis

    monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, url, **kw: fake))


def test_collect_redis_reports_all_three_queues(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.observability import metrics

    fake = _FakeRedis({"processing": 1, "asr-cpu": 2, "asr-gpu": 3})
    _use_redis(monkeypatch, fake)
    snapshot = metrics.Snapshot()

    metrics.collect_redis(snapshot, _settings())

    _, label_names, samples = snapshot.families["homayar_broker_queue_length"]
    assert label_names == ("queue",)
    assert dict((values[0], value) for values, value in samples) == {
        "processing": 1.0,
        "asr-cpu": 2.0,
        "asr-gpu": 3.0,
    }
    assert sorted(fake.calls) == sorted(ALL_QUEUES)  # one llen each


def test_system_health_sums_the_three_queues_and_none_when_redis_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.observability import metrics, system_health

    _use_redis(monkeypatch, _FakeRedis({"processing": 1, "asr-cpu": 2, "asr-gpu": 3}))
    snapshot = metrics.Snapshot()
    metrics.collect_redis(snapshot, _settings())
    probe = system_health.Probe(up=True, seconds=0.1, snapshot=snapshot)
    assert (
        system_health._queue(None, system_health.Outcome("done", probe))["broker_queue_length"] == 6
    )

    _use_redis(monkeypatch, _FakeRedis({}, fail=True))
    down = metrics.Snapshot()
    metrics.collect_redis(down, _settings())
    assert "homayar_broker_queue_length" not in down.families
    outcome = system_health.Outcome(
        "done", system_health.Probe(up=False, seconds=0.1, snapshot=down)
    )
    assert system_health._queue(None, outcome)["broker_queue_length"] is None
    assert (
        system_health._queue(None, system_health.Outcome("timeout", "Timeout"))[
            "broker_queue_length"
        ]
        is None
    )


def test_the_metrics_exposition_has_one_sample_per_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    from prometheus_client import CollectorRegistry, generate_latest

    from backend.observability import metrics

    _use_redis(monkeypatch, _FakeRedis({"processing": 0, "asr-cpu": 4, "asr-gpu": 7}))
    snapshot = metrics.Snapshot()
    metrics.collect_redis(snapshot, _settings())

    class _Collector:
        def collect(self):
            return snapshot.metric_families()

    registry = CollectorRegistry()
    registry.register(_Collector())
    text = generate_latest(registry).decode()
    assert 'homayar_broker_queue_length{queue="processing"} 0.0' in text
    assert 'homayar_broker_queue_length{queue="asr-cpu"} 4.0' in text
    assert 'homayar_broker_queue_length{queue="asr-gpu"} 7.0' in text


# --- A14: workers_report and the CLI ----------------------------------------------------


EMPTY_LENGTHS = {"processing": 0, "asr-cpu": 0, "asr-gpu": 0}


def _queue_entry(report: dict, name: str) -> dict:
    return next(entry for entry in report["queues"] if entry["name"] == name)


def test_workers_report_flags_a_waiting_asr_queue_without_consumers() -> None:
    report = worker_readiness.workers_report(
        {"w1": {"qualified": False, "reason": "gpu_memory_failed"}},
        {"w1": [{"name": "processing"}]},
        {**EMPTY_LENGTHS, "asr-gpu": 2},
    )
    assert report["ok"] is False
    assert _queue_entry(report, "asr-gpu") == {"name": "asr-gpu", "length": 2, "consumers": 0}


def test_workers_report_ok_with_a_live_consumer() -> None:
    report = worker_readiness.workers_report(
        {"w1": {"qualified": True, "reason": "ready"}},
        {"w1": [{"name": "processing"}, {"name": "asr-gpu"}, {"name": "asr-cpu"}]},
        {**EMPTY_LENGTHS, "asr-gpu": 2},
    )
    assert report["ok"] is True
    assert _queue_entry(report, "asr-gpu") == {"name": "asr-gpu", "length": 2, "consumers": 1}


def test_workers_report_ok_for_an_empty_queue_without_consumers() -> None:
    report = worker_readiness.workers_report(
        {"w1": {"qualified": False}}, {"w1": [{"name": "processing"}]}, EMPTY_LENGTHS
    )
    assert report["ok"] is True
    assert _queue_entry(report, "asr-gpu") == {"name": "asr-gpu", "length": 0, "consumers": 0}
    empty = worker_readiness.workers_report({}, {}, EMPTY_LENGTHS)
    assert empty["ok"] is True and empty["workers"] == []


def test_waiting_processing_without_consumers_is_not_an_asr_alarm() -> None:
    report = worker_readiness.workers_report({}, {}, {**EMPTY_LENGTHS, "processing": 3})
    assert report["ok"] is True


@pytest.mark.parametrize(
    ("readiness", "active", "lengths"),
    [
        ({"w1": {"qualified": True}}, {"w1": [{"name": "processing"}]}, {"processing": 0}),
        (
            {"w1": {"qualified": True}},
            {"w1": [{"name": "processing"}]},
            {**EMPTY_LENGTHS, "asr-cpu": None},
        ),
        ({}, {"w1": [{"name": "processing"}]}, EMPTY_LENGTHS),  # no readiness reply
        ({"w1": {"qualified": True}}, {}, EMPTY_LENGTHS),  # no active queues reply
    ],
    ids=["missing-length", "none-length", "missing-readiness", "missing-active-queues"],
)
def test_workers_report_is_false_when_a_value_is_missing(readiness, active, lengths) -> None:
    assert worker_readiness.workers_report(readiness, active, lengths)["ok"] is False


def test_workers_report_lists_workers_sorted_with_active_queues() -> None:
    report = worker_readiness.workers_report(
        {"b@host": {"qualified": True}, "a@host": {"qualified": False}},
        {"a@host": [{"name": "processing"}, {"name": "asr-cpu"}], "b@host": []},
        EMPTY_LENGTHS,
    )
    assert report["workers"] == [
        {
            "hostname": "a@host",
            "readiness": {"qualified": False},
            "active_queues": ["asr-cpu", "processing"],
        },
        {"hostname": "b@host", "readiness": {"qualified": True}, "active_queues": []},
    ]
    assert [entry["name"] for entry in report["queues"]] == ["processing", "asr-cpu", "asr-gpu"]
    assert _queue_entry(report, "asr-cpu")["consumers"] == 1
    assert _queue_entry(report, "processing")["consumers"] == 1
    json.dumps(report)


@pytest.mark.parametrize(
    ("record", "code"),
    [
        (worker_readiness.WorkerReadiness(**READY_CPU), 0),
        (
            worker_readiness.WorkerReadiness(
                qualified=False, reason="gpu_memory_failed", requirement="gpu", queues=()
            ),
            1,
        ),
        (
            worker_readiness.WorkerReadiness(
                qualified=False, reason="asr_adapter_failed", requirement="cpu", queues=()
            ),
            2,
        ),
    ],
    ids=["qualified", "not-qualified", "adapter-failed"],
)
def test_cli_check_exit_codes(monkeypatch: pytest.MonkeyPatch, capsys, record, code) -> None:
    monkeypatch.setattr(worker_readiness, "get_settings", lambda: _settings())
    monkeypatch.setattr(worker_readiness, "assess_worker_readiness", lambda *a, **kw: record)

    assert worker_readiness.main(["--check"]) == code
    assert json.loads(capsys.readouterr().out) == record.to_dict()


def test_cli_check_settings_error_exits_2(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def bad_settings():
        raise ValueError("bad settings at " + PRIVATE_PATH)

    monkeypatch.setattr(worker_readiness, "get_settings", bad_settings)
    assert worker_readiness.main(["--check"]) == 2
    out = capsys.readouterr().out
    assert PRIVATE_PATH not in out
    assert json.loads(out)["error_type"] == "ValueError"


def test_cli_check_reads_celery_concurrency(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    seen: list[dict] = []

    def fake_assess(settings, *, pool, concurrency, probes=None, bundle=None):
        seen.append({"pool": pool, "concurrency": concurrency, "probes": probes})
        return worker_readiness.WorkerReadiness(**READY_CPU)

    monkeypatch.setattr(worker_readiness, "get_settings", lambda: _settings())
    monkeypatch.setattr(worker_readiness, "assess_worker_readiness", fake_assess)
    monkeypatch.setattr(worker_readiness, "default_probes", lambda: "fake-probes")

    monkeypatch.setenv("CELERY_CONCURRENCY", "3")
    assert worker_readiness.main(["--check"]) == 0
    monkeypatch.delenv("CELERY_CONCURRENCY")
    assert worker_readiness.main(["--check"]) == 0
    monkeypatch.setenv("CELERY_CONCURRENCY", "many")
    assert worker_readiness.main(["--check"]) == 2

    assert seen == [
        {"pool": "prefork", "concurrency": 3, "probes": "fake-probes"},
        {"pool": "prefork", "concurrency": 1, "probes": "fake-probes"},
    ]


def test_cli_check_with_fake_probes_end_to_end(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(worker_readiness, "get_settings", lambda: _settings(asr_profile=GPU))
    monkeypatch.setattr(worker_readiness, "default_probes", lambda: p40())
    assert worker_readiness.main(["--check"]) == 0
    assert json.loads(capsys.readouterr().out)["queues"] == ["asr-gpu", "asr-cpu"]

    monkeypatch.setattr(worker_readiness, "default_probes", lambda: p40(free=100))
    assert worker_readiness.main(["--check"]) == 1


class _Inspect:
    def __init__(self, readiness, active) -> None:
        self.readiness = readiness
        self.active = active

    def _request(self, command, **kwargs):
        assert command == "worker_readiness"
        return self.readiness

    def active_queues(self):
        return self.active


def _use_inspect(monkeypatch: pytest.MonkeyPatch, inspect: _Inspect, seen: list | None = None):
    from backend.celery_app import celery_app

    def factory(timeout=None, **kwargs):
        if seen is not None:
            seen.append(timeout)
        return inspect

    monkeypatch.setattr(celery_app, "control", SimpleNamespace(inspect=factory))


def test_cli_workers_exit_codes(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(worker_readiness, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        worker_readiness,
        "_queue_lengths_from_redis",
        lambda settings: {**EMPTY_LENGTHS, "asr-gpu": 2},
    )
    timeouts: list = []
    _use_inspect(
        monkeypatch,
        _Inspect(
            {"w1": {"qualified": True}}, {"w1": [{"name": "asr-gpu"}, {"name": "processing"}]}
        ),
        timeouts,
    )
    assert worker_readiness.main(["--workers"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert timeouts == [2.0]

    _use_inspect(monkeypatch, _Inspect(None, None))  # no live worker answered
    assert worker_readiness.main(["--workers"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False

    def broker_down(settings):
        raise ConnectionError("broker down at " + PRIVATE_PATH)

    monkeypatch.setattr(worker_readiness, "_queue_lengths_from_redis", broker_down)
    assert worker_readiness.main(["--workers"]) == 2
    out = capsys.readouterr().out
    assert PRIVATE_PATH not in out


def test_cli_workers_uses_a_worker_readiness_method_when_there_is_one(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    class _MethodInspect:
        def worker_readiness(self):
            return {"w1": {"qualified": True, "reason": "ready"}}

        def active_queues(self):
            return {"w1": [{"name": "processing"}, {"name": "asr-cpu"}]}

    monkeypatch.setattr(worker_readiness, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        worker_readiness,
        "_queue_lengths_from_redis",
        lambda settings: {**EMPTY_LENGTHS, "asr-cpu": 1},
    )
    _use_inspect(monkeypatch, _MethodInspect())

    assert worker_readiness.main(["--workers"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["workers"][0]["readiness"] == {"qualified": True, "reason": "ready"}
    assert _queue_entry(report, "asr-cpu") == {"name": "asr-cpu", "length": 1, "consumers": 1}


def test_queue_lengths_from_redis_reads_the_three_queues(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis({"processing": 1, "asr-cpu": 2, "asr-gpu": 3})
    _use_redis(monkeypatch, fake)
    assert worker_readiness._queue_lengths_from_redis(_settings()) == {
        "processing": 1,
        "asr-cpu": 2,
        "asr-gpu": 3,
    }
    _use_redis(monkeypatch, _FakeRedis({}, fail=True))
    with pytest.raises(ConnectionError):
        worker_readiness._queue_lengths_from_redis(_settings())


def test_cli_needs_one_mode(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        worker_readiness.main([])
    assert exc.value.code == 2


# --- A15: privacy -----------------------------------------------------------------------


def test_to_dict_and_logs_hold_no_uuid_path_or_message(
    celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    by_uuid = assess(
        settings=_settings(asr_profile=GPU, whisper_model_path=PRIVATE_PATH),
        probes=p40(environ={"CUDA_VISIBLE_DEVICES": FAKE_UUID_0}),
    )
    missing_path = assess(
        settings=_settings(
            asr_profile=GPU, whisper_model_path=PRIVATE_PATH, whisper_local_files_only=True
        ),
        probes=p40(),
    )
    assert missing_path.reason == "asr_runtime_failed"  # the path is not a directory

    def boom(name: str):
        raise RuntimeError(PRIVATE_MESSAGE)

    failed = assess(
        probes=HostProbes(
            run=lambda argv, **kw: _completed(_smi_line(0, FAKE_UUID_0, 20000)),
            find_spec=boom,
            ctranslate2=lambda: FakeCT2(),
            environ={},
        )
    )
    with caplog.at_level(logging.INFO):
        for record in (by_uuid, missing_path, failed):
            celery_module._worker_readiness = record
            celery_module.on_worker_ready(signal=None, sender=None)

    blobs = [json.dumps(record.to_dict()) for record in (by_uuid, missing_path, failed)]
    for text in [*blobs, caplog.text]:
        assert FAKE_UUID_0 not in text
        assert FAKE_UUID_0[:12] not in text
        assert PRIVATE_PATH not in text
        assert PRIVATE_MESSAGE not in text
        assert "GPU-" not in text
    assert by_uuid.qualified is True
    assert failed.error_type == "RuntimeError"


# --- A16: settings ----------------------------------------------------------------------


def test_settings_defaults_for_the_new_fields() -> None:
    settings = _settings()
    assert settings.worker_asr_queues == "auto"
    assert settings.worker_vram_budget_mib == 0
    assert settings.asr_model_vram_mib == 0
    for name, default in (
        ("worker_asr_queues", "auto"),
        ("worker_vram_budget_mib", 0),
        ("asr_model_vram_mib", 0),
    ):
        assert Settings.model_fields[name].default == default


@pytest.mark.parametrize(
    ("value", "expected"), [("OFF", "off"), (" off ", "off"), ("Auto", "auto")]
)
def test_settings_accept_and_normalise_auto_off(value: str, expected: str) -> None:
    assert _settings(worker_asr_queues=value).worker_asr_queues == expected


def test_settings_read_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_ASR_QUEUES", "OFF")
    monkeypatch.setenv("WORKER_VRAM_BUDGET_MIB", "8192")
    monkeypatch.setenv("ASR_MODEL_VRAM_MIB", "3000")
    settings = _settings()
    assert (
        settings.worker_asr_queues,
        settings.worker_vram_budget_mib,
        settings.asr_model_vram_mib,
    ) == (
        "off",
        8192,
        3000,
    )


@pytest.mark.parametrize("value", ["maybe", "", "on"])
def test_settings_reject_other_worker_asr_queues_values(value: str) -> None:
    with pytest.raises(ValueError, match="WORKER_ASR_QUEUES"):
        _settings(worker_asr_queues=value)


@pytest.mark.parametrize(
    ("field", "env_name"),
    [
        ("worker_vram_budget_mib", "WORKER_VRAM_BUDGET_MIB"),
        ("asr_model_vram_mib", "ASR_MODEL_VRAM_MIB"),
    ],
)
def test_settings_reject_negative_vram_values(field: str, env_name: str) -> None:
    with pytest.raises(ValueError, match=env_name):
        _settings(**{field: -1})
    assert getattr(_settings(**{field: 0}), field) == 0


# --- A17: compose -----------------------------------------------------------------------


COMPOSE_DIR = Path(__file__).resolve().parents[3] / "infra" / "compose"
NEW_WORKER_ENV = {
    "WORKER_ASR_QUEUES": "auto",
    "WORKER_VRAM_BUDGET_MIB": "0",
    "ASR_MODEL_VRAM_MIB": "0",
}


@pytest.mark.parametrize("file_name", ["compose.onprem.yaml", "compose.cloud.yaml"])
def test_compose_passes_the_new_variables_to_worker_only(file_name: str) -> None:
    path = COMPOSE_DIR / file_name
    if not path.exists():
        pytest.skip("infra/compose is not part of this checkout")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    services = document["services"]
    worker = services["worker"]
    for env_name, default in NEW_WORKER_ENV.items():
        assert worker["environment"][env_name] == f"${{{env_name}:-{default}}}", env_name
        assert env_name not in document["x-app-environment"], env_name
        for other in ("api", "scheduler"):
            assert env_name not in (services[other].get("environment") or {}), (other, env_name)
    command = worker["command"]
    command_text = " ".join(command) if isinstance(command, list) else command
    assert "--queues processing" in command_text


# --- A18: the default production path ---------------------------------------------------


def test_default_production_path_with_fakes(monkeypatch: pytest.MonkeyPatch, celery_module) -> None:
    """GPU profile on the worker only, the api without ASR_PROFILE, concurrency 1,
    a fake P40 with 20000 MiB free and working CUDA probes."""
    from backend.jobs import dispatcher
    from backend.jobs.tasks import process_meeting_job

    api_settings = _settings()  # the api does not see ASR_PROFILE
    worker_settings = _settings(asr_profile=GPU)
    published: list[str] = []

    def fake_apply_async(**options):
        published.append(options["queue"])
        return SimpleNamespace(id="m-1")

    monkeypatch.setattr(process_meeting_job, "apply_async", fake_apply_async)
    dispatcher.publish_processing_job("job-1", api_settings)
    assert published == ["asr-cpu"]

    # The worker starts with the compose command (--queues processing).
    _use_settings(monkeypatch, celery_module, worker_settings)
    monkeypatch.setattr(worker_readiness, "default_probes", lambda: p40())
    sender = _FakeSender(selected=["processing"], concurrency=1)
    celery_module.on_worker_init(signal=None, sender=sender)
    record = celery_module.current_worker_readiness()
    assert _verdict(record) == (True, "ready", ("asr-gpu", "asr-cpu"))
    assert sender.consumed == {"processing", "asr-gpu", "asr-cpu"}

    # The job arrives on asr-cpu and runs on the GPU worker.
    published.clear()
    run = _TaskRun(monkeypatch, worker_settings)
    assert run("asr-cpu") == {"job_id": "job-1", "status": "completed"}
    assert run.processed == ["job-1"] and run.published == []


# --- A19: a real worker -----------------------------------------------------------------


def _worker_scenario(
    monkeypatch: pytest.MonkeyPatch, celery_module, broker_url: str, backend_url: str
):
    """A CPU-only worker (GPU requirement, no CUDA) runs an ``asr-cpu`` message
    and leaves an ``asr-gpu`` message in its queue."""
    from celery.contrib.testing.worker import start_worker

    from backend.jobs import outbox, tasks
    from backend.jobs.tasks import process_meeting_job
    from backend.tests._transport import use_broker

    _use_settings(monkeypatch, celery_module, _settings(asr_profile=GPU))
    monkeypatch.setattr(tasks, "get_settings", lambda: _settings(asr_profile=GPU))
    # Fake readiness probes: CTranslate2 sees no CUDA device.
    monkeypatch.setattr(worker_readiness, "default_probes", lambda: p40(ct2=FakeCT2(count=0)))
    monkeypatch.setattr(outbox, "verify_job_payload", lambda job_id, workspace_id: False)
    app = celery_module.celery_app
    app.amqp.queues._consume_from = None  # started without --queues
    with use_broker(broker_url, backend_url):
        try:
            with start_worker(
                app,
                pool="solo",
                concurrency=1,
                perform_ping_check=False,
                loglevel="warning",
                shutdown_timeout=15,
            ) as worker:
                readiness = celery_module.current_worker_readiness()
                assert _verdict(readiness) == (False, "asr_runtime_failed", ())
                consumed = {queue.name for queue in worker.consumer.task_consumer.queues}
                assert consumed == {"processing"}

                gpu = process_meeting_job.apply_async(args=["gpu-job"], queue="asr-gpu")
                # A legacy message reaches this worker and moves to the job's queue.
                legacy = process_meeting_job.apply_async(args=["legacy-job"], queue="processing")
                assert legacy.get(timeout=20) == {
                    "job_id": "legacy-job",
                    "status": "rerouted",
                    "queue": "asr-gpu",
                }
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    assert not gpu.ready()
                    time.sleep(0.1)
                with app.connection_for_write() as connection:
                    channel = connection.default_channel
                    waiting = channel.queue_declare("asr-gpu", passive=True).message_count
                assert waiting == 2  # the GPU job and the rerouted copy
        finally:
            with app.connection_for_write() as connection:
                for name in ALL_QUEUES:
                    connection.default_channel.queue_purge(name)


def _cpu_worker_scenario(
    monkeypatch: pytest.MonkeyPatch, celery_module, broker_url: str, backend_url: str
):
    """A CPU-qualified worker runs ``asr-cpu`` and leaves ``asr-gpu``."""
    from celery.contrib.testing.worker import start_worker

    from backend.jobs import outbox, tasks
    from backend.jobs.tasks import process_meeting_job
    from backend.tests._transport import use_broker

    _use_settings(monkeypatch, celery_module, _settings())
    monkeypatch.setattr(tasks, "get_settings", lambda: _settings())
    monkeypatch.setattr(worker_readiness, "default_probes", lambda: probes())
    monkeypatch.setattr(outbox, "verify_job_payload", lambda job_id, workspace_id: False)
    app = celery_module.celery_app
    app.amqp.queues._consume_from = None
    with use_broker(broker_url, backend_url):
        try:
            with start_worker(
                app,
                pool="solo",
                concurrency=1,
                perform_ping_check=False,
                loglevel="warning",
                shutdown_timeout=15,
            ) as worker:
                consumed = {queue.name for queue in worker.consumer.task_consumer.queues}
                assert consumed == {"processing", "asr-cpu"}
                gpu = process_meeting_job.apply_async(args=["gpu-job"], queue="asr-gpu")
                cpu = process_meeting_job.apply_async(args=["cpu-job"], queue="asr-cpu")
                assert cpu.get(timeout=20) == {"job_id": "cpu-job", "status": "rejected_payload"}
                time.sleep(1)
                assert not gpu.ready()
                with app.connection_for_write() as connection:
                    channel = connection.default_channel
                    assert channel.queue_declare("asr-gpu", passive=True).message_count == 1
        finally:
            with app.connection_for_write() as connection:
                for name in ALL_QUEUES:
                    connection.default_channel.queue_purge(name)


def test_a_real_worker_on_the_memory_broker_follows_its_readiness(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    _worker_scenario(monkeypatch, celery_module, "memory://", "cache+memory://")
    _cpu_worker_scenario(monkeypatch, celery_module, "memory://", "cache+memory://")


@pytest.mark.redis
def test_a_cpu_only_worker_leaves_asr_gpu_and_runs_asr_cpu_on_redis(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    from backend.tests._transport import configured_redis_url, flush_test_database, redis_client

    redis_url = configured_redis_url()
    if redis_url is None:
        pytest.skip("requires a disposable Redis (TEST_REDIS_URL unset)")
    flush_test_database(redis_url)
    try:
        _cpu_worker_scenario(monkeypatch, celery_module, redis_url, redis_url)
        _worker_scenario(monkeypatch, celery_module, redis_url, redis_url)
        client = redis_client(redis_url)
        try:
            assert int(client.llen("asr-gpu")) == 0  # purged by the scenario
        finally:
            client.close()
    finally:
        flush_test_database(redis_url)


# --- the transport drill harness ----------------------------------------------------------


def test_the_drill_worker_and_producer_agree_on_asr_cpu(settings) -> None:
    """The drill workers run with the harness env (a mock ASR provider): their
    readiness is ``remote`` and holds ``asr-cpu``, where the drill producer
    (the test settings, local Whisper on the CPU) publishes."""
    from backend.tests._transport import process_environment

    env = process_environment(settings, "redis://127.0.0.1:1/9")
    worker_settings = _settings(
        asr_provider=env["ASR_PROVIDER"], allow_mock_providers=env["ALLOW_MOCK_PROVIDERS"]
    )

    def no_probe(*_args, **_kwargs):
        raise AssertionError("a remote requirement runs no probe")

    record = worker_readiness.assess_worker_readiness(
        worker_settings,
        pool="prefork",
        concurrency=1,
        probes=HostProbes(run=no_probe, find_spec=no_probe, ctranslate2=no_probe),
    )
    assert _verdict(record) == (True, "no_local_model", ("asr-cpu",))
    assert worker_readiness.asr_queue_for(settings) in record.queues


def test_queue_state_counts_every_declared_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.tests import _transport

    class _Client:
        def llen(self, name: str) -> int:
            return {"processing": 1, "asr-cpu": 2, "asr-gpu": 4}.get(name, 100)

        def hlen(self, name: str) -> int:
            assert name == "unacked"
            return 3

        def close(self) -> None:
            pass

    monkeypatch.setattr(_transport, "redis_client", lambda url: _Client())
    assert _transport.queue_state("redis://x") == {"queued": 7, "unacked": 3}
