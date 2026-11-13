"""HOM-41: warm ASR worker processes, memory-aware concurrency, bounded batching.

Every faster-whisper object here is a fake module placed in ``sys.modules``:
the real library is not part of the dev environment. The warm cache is
cleared before and after each test, so no test depends on another.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.config import Settings
from backend.providers import warm_models
from backend.providers.asr_profiles import ASR_PROFILES
from backend.providers.base import ASRProvider, ProviderError
from backend.providers.factory import ProviderBundle, create_asr_provider
from backend.providers.mocks import MockIntelligenceProvider
from backend.providers.schemas import ASRRequest, ASRResult
from backend.providers.whisper import RESCUE_VAD_PARAMETERS, LocalWhisperProvider
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider, _queued_upload


@pytest.fixture(autouse=True)
def _clean_warm_cache():
    warm_models.clear()
    yield
    warm_models.clear()


# --- the fake faster_whisper module ------------------------------------------------


class _FakeWhisperModel:
    """Fake ``faster_whisper.WhisperModel``. Class knobs change the behaviour.

    ``constructed`` holds every instance, so a test counts loads. The first
    ``fail_first`` constructions raise ``error``.
    """

    constructed: list[_FakeWhisperModel] = []
    fail_first: int = 0
    error_message: str = "boom"
    load_delay: float = 0.0
    after_vad: float = 3.0
    after_vad_rescue: float | None = None
    detect: tuple[str, float] = ("fa", 1.0)

    def __init__(self, source: str, **kwargs: Any) -> None:
        cls = type(self)
        cls.constructed.append(self)
        if len(cls.constructed) <= cls.fail_first:
            raise RuntimeError(cls.error_message)
        self.source = source
        self.kwargs = kwargs
        self.calls: list[tuple[str, dict[str, Any]]] = []
        if cls.load_delay:
            time.sleep(cls.load_delay)

    def transcribe(self, path: str, **kwargs: Any) -> Any:
        cls = type(self)
        self.calls.append((path, kwargs))
        language = kwargs.get("language")
        rescued = "vad_parameters" in kwargs
        if rescued and cls.after_vad_rescue is not None:
            kept = cls.after_vad_rescue
        else:
            kept = cls.after_vad
        label = language or "auto"

        def segments():
            yield SimpleNamespace(start=0.0, end=1.0, text=f"text-{label}")

        if language is None:
            info = SimpleNamespace(
                language=cls.detect[0],
                language_probability=cls.detect[1],
                all_language_probs=[cls.detect],
                duration=1.5,
                duration_after_vad=kept,
            )
        else:
            info = SimpleNamespace(language=language, duration=1.5, duration_after_vad=kept)
        return segments(), info


class _FakeBatchedPipeline:
    """Fake ``faster_whisper.BatchedInferencePipeline``: records and forwards."""

    instances: list[_FakeBatchedPipeline] = []

    def __init__(self, model: Any = None, **kwargs: Any) -> None:
        type(self).instances.append(self)
        self.model = model
        self.kwargs = kwargs
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def transcribe(self, path: str, **kwargs: Any) -> Any:
        self.calls.append((path, dict(kwargs)))
        return self.model.transcribe(path, **kwargs)


def _install_fake(
    monkeypatch: pytest.MonkeyPatch, *, model_knobs: dict[str, Any] | None = None
) -> tuple[type[_FakeWhisperModel], type[_FakeBatchedPipeline]]:
    model_cls = type(
        "FakeWhisperModel", (_FakeWhisperModel,), {"constructed": [], **(model_knobs or {})}
    )
    pipeline_cls = type("FakeBatchedPipeline", (_FakeBatchedPipeline,), {"instances": []})
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = model_cls
    module.BatchedInferencePipeline = pipeline_cls
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return model_cls, pipeline_cls


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "local_ai_base_url": "http://intelligence.test/v1",
        "llm_model": "conversation-model",
        "allow_mock_providers": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _audio(tmp_path: Path) -> str:
    path = tmp_path / "call.wav"
    path.write_bytes(b"RIFF....WAVE")
    return str(path)


def _transcribe(
    provider: LocalWhisperProvider,
    audio: str,
    language: str | None = "fa",
    *,
    vad_filter: bool = True,
) -> ASRResult:
    request = ASRRequest(audio_path=audio, language=language, vad_filter=vad_filter)
    return asyncio.run(provider.transcribe(request))


def _provider(**kwargs: Any) -> LocalWhisperProvider:
    values: dict[str, Any] = {"model_name": "base", "device": "cpu", "compute_type": "int8"}
    values.update(kwargs)
    return LocalWhisperProvider(**values)


# --- AC1: ten sequential jobs, one load --------------------------------------------


def test_ten_sequential_jobs_load_the_model_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake(monkeypatch)
    settings = _settings()
    audio = _audio(tmp_path)

    for _ in range(10):
        result = _transcribe(create_asr_provider(settings), audio)
        assert result.text

    stats = warm_models.stats()
    assert stats.loads == 1
    assert stats.hits >= 9
    assert stats.failures == 0
    assert stats.keys == 1


# --- AC2: separate configs load separately -----------------------------------------


@pytest.mark.parametrize(
    "difference",
    [
        {"whisper_compute_type": "float32"},
        {"whisper_device": "cuda"},
        {"whisper_model": "small"},
    ],
)
def test_configs_that_change_the_load_load_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, difference: dict[str, Any]
) -> None:
    _install_fake(monkeypatch)
    audio = _audio(tmp_path)
    _transcribe(create_asr_provider(_settings()), audio)
    _transcribe(create_asr_provider(_settings(**difference)), audio)
    assert warm_models.stats().loads == 2


def test_language_policy_is_not_part_of_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake(monkeypatch)
    audio = _audio(tmp_path)
    _transcribe(create_asr_provider(_settings(asr_allowed_languages="fa,en")), audio)
    _transcribe(
        create_asr_provider(_settings(asr_allowed_languages="en", asr_default_language="en")),
        audio,
    )
    assert warm_models.stats().loads == 1


# --- AC3: failure is not cached -----------------------------------------------------


def test_a_failing_loader_is_not_cached_and_maps_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake(monkeypatch, model_knobs={"fail_first": 1})
    settings = _settings()
    audio = _audio(tmp_path)

    with pytest.raises(ProviderError) as exc:
        _transcribe(create_asr_provider(settings), audio)
    assert exc.value.code == "asr_failed"

    stats = warm_models.stats()
    assert stats.failures == 1
    assert stats.loads == 0
    assert stats.keys == 0

    result = _transcribe(create_asr_provider(settings), audio)
    assert result.text
    assert warm_models.stats().loads == 1


def test_a_missing_model_directory_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake(monkeypatch)
    weights = tmp_path / "weights"
    settings = _settings(whisper_model_path=str(weights))
    audio = _audio(tmp_path)

    with pytest.raises(ProviderError) as exc:
        _transcribe(create_asr_provider(settings), audio)
    assert exc.value.code == "asr_model_missing"
    assert warm_models.stats().failures == 1

    weights.mkdir()
    result = _transcribe(create_asr_provider(settings), audio)
    assert result.text
    assert warm_models.stats().loads == 1


def test_a_missing_library_failure_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A None entry in sys.modules makes "import faster_whisper" raise
    # ImportError, also where the library is installed. The lazy import runs
    # inside the loader, so the failure is counted and never cached.
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    audio = _audio(tmp_path)

    with pytest.raises(ProviderError) as exc:
        _transcribe(create_asr_provider(_settings()), audio)
    assert exc.value.code == "asr_unavailable"
    assert warm_models.stats().failures == 1
    assert warm_models.stats().keys == 0


# --- AC4: concurrent first use ------------------------------------------------------


def test_eight_threads_load_the_model_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model_cls, _ = _install_fake(monkeypatch, model_knobs={"load_delay": 0.05})
    settings = _settings()
    audio = _audio(tmp_path)
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            barrier.wait(timeout=10)
            _transcribe(create_asr_provider(settings), audio)
        except BaseException as exc:  # recorded and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors
    assert len(model_cls.constructed) == 1
    stats = warm_models.stats()
    assert stats.loads == 1
    assert stats.hits == 7


# --- AC5: an injected model bypasses the cache --------------------------------------


def test_an_injected_model_never_touches_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_cls, _ = _install_fake(monkeypatch)
    audio = _audio(tmp_path)

    model = model_cls("base")
    _transcribe(_provider(model=model), audio)
    assert model.calls

    batched = model_cls("base")
    _transcribe(_provider(model=batched, batch_size=4), audio)
    assert batched.calls

    stats = warm_models.stats()
    assert stats.loads == 0
    assert stats.hits == 0
    assert stats.keys == 0


# --- AC6: warm on start -------------------------------------------------------------


def test_warm_configured_asr_loads_the_model_and_jobs_add_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake(monkeypatch)
    settings = _settings()
    audio = _audio(tmp_path)

    assert warm_models.warm_configured_asr(settings) is True
    assert warm_models.stats().loads == 1

    _transcribe(create_asr_provider(settings), audio)
    stats = warm_models.stats()
    assert stats.loads == 1
    assert stats.hits == 1


def test_warm_off_loads_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake(monkeypatch)
    assert warm_models.warm_configured_asr(_settings(asr_warm_on_start=False)) is False
    assert warm_models.stats().loads == 0


def test_warm_skips_the_mock_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake(monkeypatch)
    settings = _settings(asr_provider="mock", allow_mock_providers=True)
    assert warm_models.warm_configured_asr(settings) is False
    assert warm_models.stats().loads == 0


def test_a_failed_warm_returns_false_and_never_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_fake(
        monkeypatch,
        model_knobs={
            "fail_first": 99,
            "error_message": "load broke at /models/large-v3/secret",
        },
    )
    settings = _settings(whisper_model="large-v3")

    with caplog.at_level(logging.WARNING):
        assert warm_models.warm_configured_asr(settings) is False

    records = [r for r in caplog.records if r.getMessage().startswith("asr_warm_failed")]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].exc_info is None  # a traceback would carry paths
    message = records[0].getMessage()
    assert "RuntimeError" in message
    assert "/models/large-v3" not in message
    assert "secret" not in message


def test_a_warm_missing_model_logs_the_error_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_fake(monkeypatch)
    settings = _settings(whisper_model_path=str(tmp_path / "absent-weights"))

    with caplog.at_level(logging.WARNING):
        assert warm_models.warm_configured_asr(settings) is False

    records = [r for r in caplog.records if r.getMessage().startswith("asr_warm_failed")]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "asr_model_missing" in message
    assert str(tmp_path / "absent-weights") not in message


# --- AC7: memory decisions ----------------------------------------------------------


def _capacity_settings(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "asr_provider": "local_whisper",
        "asr_profile": "",
        "whisper_model": "base",
        "asr_model_memory_mib": 0,
        "asr_batch_size": 1,
        "worker_memory_budget_mib": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_p40_profile_within_budget() -> None:
    from backend.jobs.worker_capacity import check_worker_memory

    decision = check_worker_memory(
        _capacity_settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=5120),
        pool="prefork",
        concurrency=1,
    )
    assert (decision.ok, decision.reason, decision.required_mib) == (True, "within_budget", 5120)


def test_p40_profile_two_workers_over_budget() -> None:
    from backend.jobs.worker_capacity import check_worker_memory

    decision = check_worker_memory(
        _capacity_settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=5120),
        pool="prefork",
        concurrency=2,
    )
    assert (decision.ok, decision.reason) == (False, "over_budget")
    assert decision.allowed_processes == 1


def test_p40_profile_without_budget() -> None:
    from backend.jobs.worker_capacity import check_worker_memory

    decision = check_worker_memory(
        _capacity_settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=0),
        pool="prefork",
        concurrency=4,
    )
    assert (decision.ok, decision.reason, decision.processes) == (True, "no_budget", 4)


def test_p40_profile_budget_too_small() -> None:
    from backend.jobs.worker_capacity import check_worker_memory

    decision = check_worker_memory(
        _capacity_settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=4096),
        pool="prefork",
        concurrency=1,
    )
    assert (decision.ok, decision.reason) == (False, "budget_too_small")
    assert decision.allowed_processes == 0


def test_base_model_two_workers_within_budget() -> None:
    from backend.jobs.worker_capacity import check_worker_memory

    decision = check_worker_memory(
        _capacity_settings(whisper_model="base", worker_memory_budget_mib=2048),
        pool="prefork",
        concurrency=2,
    )
    assert (decision.ok, decision.reason, decision.required_mib) == (True, "within_budget", 2048)


def test_cpu_base_profile_two_workers_within_budget() -> None:
    from backend.jobs.worker_capacity import check_worker_memory

    decision = check_worker_memory(
        _capacity_settings(asr_profile="cpu-base", worker_memory_budget_mib=2048),
        pool="prefork",
        concurrency=2,
    )
    assert (decision.ok, decision.reason, decision.required_mib) == (True, "within_budget", 2048)


def test_solo_pool_holds_one_model_process() -> None:
    from backend.jobs.worker_capacity import check_worker_memory

    decision = check_worker_memory(
        _capacity_settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=5120),
        pool="solo",
        concurrency=4,
    )
    assert decision.ok is True
    assert decision.processes == 1


def test_unknown_model_with_a_budget_is_refused_until_overridden() -> None:
    from backend.jobs.worker_capacity import check_worker_memory

    decision = check_worker_memory(
        _capacity_settings(whisper_model="custom-x", worker_memory_budget_mib=4096),
        pool="prefork",
        concurrency=1,
    )
    assert (decision.ok, decision.reason) == (False, "unknown_model_memory")

    overridden = check_worker_memory(
        _capacity_settings(whisper_model="custom-x", asr_model_memory_mib=1000),
        pool="prefork",
        concurrency=1,
    )
    assert overridden.ok is True


def test_batch_slots_add_memory_per_process() -> None:
    from backend.jobs.worker_capacity import (
        ASR_BATCH_SLOT_MEMORY_MIB,
        WORKER_BASE_MEMORY_MIB,
        check_worker_memory,
    )

    plain = check_worker_memory(
        _capacity_settings(asr_profile="cpu-base", worker_memory_budget_mib=65536),
        pool="prefork",
        concurrency=1,
    )
    batched = check_worker_memory(
        _capacity_settings(
            asr_profile="cpu-base", asr_batch_size=4, worker_memory_budget_mib=65536
        ),
        pool="prefork",
        concurrency=1,
    )
    assert plain.per_process_mib == WORKER_BASE_MEMORY_MIB + ASR_PROFILES["cpu-base"].memory_mib
    assert batched.per_process_mib - plain.per_process_mib == 3 * ASR_BATCH_SLOT_MEMORY_MIB
    assert batched.per_process_mib - plain.per_process_mib == 768


def test_mock_provider_needs_no_model_memory() -> None:
    from backend.jobs.worker_capacity import check_worker_memory

    decision = check_worker_memory(
        _capacity_settings(asr_provider="mock", worker_memory_budget_mib=2048),
        pool="prefork",
        concurrency=4,
    )
    assert (decision.ok, decision.reason, decision.processes) == (True, "no_asr_model", 4)


def test_provider_name_rule_matches_the_factory() -> None:
    from backend.jobs.worker_capacity import asr_memory_estimate_mib

    settings = _capacity_settings(whisper_model="base", asr_provider=" Local-Whisper ")
    assert asr_memory_estimate_mib(settings) == 512
    assert asr_memory_estimate_mib(_capacity_settings(asr_provider="Mock")) == 0


def test_profile_memory_values() -> None:
    from backend.jobs.worker_capacity import asr_memory_estimate_mib

    expected = {
        "cpu-base": 512,
        "cpu-small": 1024,
        "cpu-medium": 2048,
        "cpu-large-v3-turbo": 2560,
        "cpu-large-v3": 3584,
        "gpu-p40-large-v3": 4608,
    }
    assert {name: profile.memory_mib for name, profile in ASR_PROFILES.items()} == expected
    for name, memory_mib in expected.items():
        assert asr_memory_estimate_mib(_capacity_settings(asr_profile=name)) == memory_mib


def test_model_name_table_and_unknown_names() -> None:
    from backend.jobs.worker_capacity import asr_memory_estimate_mib

    table = {
        "tiny": 384,
        "base": 512,
        "small": 1024,
        "medium": 2048,
        "large-v3-turbo": 2560,
        "large-v3": 3584,
    }
    for model, memory_mib in table.items():
        assert asr_memory_estimate_mib(_capacity_settings(whisper_model=model)) == memory_mib
    assert asr_memory_estimate_mib(_capacity_settings(whisper_model="custom-x")) is None
    assert asr_memory_estimate_mib(_capacity_settings(asr_provider="mock")) == 0


def test_worker_processes_by_pool() -> None:
    from celery.concurrency.prefork import TaskPool as PreforkPool
    from celery.concurrency.solo import TaskPool as SoloPool
    from celery.concurrency.thread import TaskPool as ThreadPool

    from backend.jobs.worker_capacity import worker_processes

    assert worker_processes("prefork", 3) == 3
    assert worker_processes(PreforkPool, 3) == 3
    assert worker_processes("some-new-pool", 3) == 3  # unknown counts as prefork
    for single in ("solo", "threads", "gevent", "eventlet"):
        assert worker_processes(single, 3) == 1
    assert worker_processes(SoloPool, 3) == 1
    # The threads pool module is celery.concurrency.thread (singular).
    assert worker_processes(ThreadPool, 3) == 1


def test_worker_processes_matches_every_celery_pool_alias() -> None:
    import celery.concurrency as pools

    from backend.jobs.worker_capacity import worker_processes

    single = {"solo", "threads", "eventlet", "gevent"}
    resolved: set[str] = set()
    for alias in sorted(pools.ALIASES):
        try:
            pool_cls = pools.get_implementation(alias)
        except ImportError:
            continue  # optional dependency (eventlet/gevent) not installed
        resolved.add(alias)
        expected = 1 if alias in single else 3
        assert worker_processes(pool_cls, 3) == expected, alias
        assert worker_processes(alias, 3) == expected, alias
    assert {"prefork", "solo", "threads"} <= resolved


def test_worker_capacity_error_carries_the_decision() -> None:
    from backend.jobs.worker_capacity import (
        CapacityDecision,
        WorkerCapacityError,
        check_worker_memory,
    )

    decision = check_worker_memory(
        _capacity_settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=4096),
        pool="prefork",
        concurrency=1,
    )
    error = WorkerCapacityError("no room", decision)
    assert isinstance(error, RuntimeError)
    assert error.decision == CapacityDecision(
        ok=False,
        reason="budget_too_small",
        processes=1,
        allowed_processes=0,
        per_process_mib=5120,
        required_mib=5120,
        budget_mib=4096,
    )


# --- AC8: worker start --------------------------------------------------------------


class _FakeSender:
    def __init__(
        self,
        concurrency: int,
        pool_cls: Any = "prefork",
        autoscale: Any = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.concurrency = concurrency
        self.pool_cls = pool_cls
        self.autoscale = autoscale
        self.options = options


@pytest.fixture
def celery_module():
    import backend.celery_app as module

    return module


def test_worker_init_clamps_concurrency_to_the_budget(
    monkeypatch: pytest.MonkeyPatch, celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        celery_module,
        "get_settings",
        lambda: _settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=5120),
    )
    sender = _FakeSender(concurrency=2)

    with caplog.at_level(logging.INFO):
        celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.concurrency == 1
    assert warm_models.stats().loads == 0  # the receiver never loads a model
    messages = [record.getMessage() for record in caplog.records]
    assert any(message.startswith("worker_memory_check") for message in messages)
    # WARNING: at worker_init Celery has not set up logging, so INFO is dropped.
    clamped = [
        record
        for record in caplog.records
        if record.getMessage().startswith("worker_concurrency_clamped")
    ]
    assert len(clamped) == 1
    assert clamped[0].levelno == logging.WARNING


def test_worker_ready_logs_the_stored_decision_again(
    monkeypatch: pytest.MonkeyPatch, celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        celery_module,
        "get_settings",
        lambda: _settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=5120),
    )
    sender = _FakeSender(concurrency=1)

    with caplog.at_level(logging.INFO):
        celery_module.on_worker_init(signal=None, sender=sender)
        checks = [
            record
            for record in caplog.records
            if record.getMessage().startswith("worker_memory_check")
        ]
        assert len(checks) == 1  # kept from worker_init, INFO may be dropped there
        caplog.clear()
        celery_module.on_worker_ready(signal=None, sender=None)
        checks = [
            record
            for record in caplog.records
            if record.getMessage().startswith("worker_memory_check")
        ]

    assert len(checks) == 1
    assert checks[0].levelno == logging.INFO
    assert "within_budget" in checks[0].getMessage()


def test_worker_ready_without_a_stored_decision_logs_nothing(
    celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    celery_module._worker_memory_decision = None

    with caplog.at_level(logging.INFO):
        celery_module.on_worker_ready(signal=None, sender=None)

    assert not [
        record for record in caplog.records if record.getMessage().startswith("worker_memory_check")
    ]


def test_worker_ready_never_raises(celery_module) -> None:
    celery_module.on_worker_ready(signal=None, sender=None)


@pytest.mark.parametrize("autoscale", [(2, 1), [2, 1], "2,1"])
def test_the_budget_check_uses_the_autoscale_max(
    monkeypatch: pytest.MonkeyPatch, celery_module, autoscale
) -> None:
    # Concurrency 1 alone fits the budget; the autoscale maximum 2 does not.
    monkeypatch.setattr(
        celery_module,
        "get_settings",
        lambda: _settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=5120),
    )
    sender = _FakeSender(concurrency=1, autoscale=autoscale)

    celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.concurrency == 1
    assert sender.autoscale == [1, 1]  # the max was capped to the allowance


def test_the_budget_check_uses_the_autoscale_max_from_options(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    monkeypatch.setattr(
        celery_module,
        "get_settings",
        lambda: _settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=5120),
    )
    sender = _FakeSender(concurrency=1, options={"autoscale": "2,1"})

    celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.concurrency == 1
    assert sender.options["autoscale"] == [1, 1]


def test_without_autoscale_the_concurrency_is_checked(
    monkeypatch: pytest.MonkeyPatch, celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        celery_module,
        "get_settings",
        lambda: _settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=5120),
    )
    sender = _FakeSender(concurrency=1)

    with caplog.at_level(logging.INFO):
        celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.concurrency == 1
    assert not [
        record
        for record in caplog.records
        if record.getMessage().startswith("worker_concurrency_clamped")
    ]


def test_the_child_start_timeout_covers_the_model_load(celery_module) -> None:
    """A prefork child warms the model before it reports up; the Celery
    default of 4 s would kill it during the 12 to 14 s load (SPEC 10.1)."""
    assert celery_module.WORKER_PROC_ALIVE_TIMEOUT_SECONDS == 120.0
    assert celery_module.celery_app.conf.worker_proc_alive_timeout == 120.0
    assert celery_module.celery_app.conf.worker_proc_alive_timeout > 14.0


def test_worker_init_clamps_the_autoscale_too(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    monkeypatch.setattr(
        celery_module,
        "get_settings",
        lambda: _settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=5120),
    )
    # Celery autoscale order is [max, min]; the clamp writes [max, min] too.
    sender = _FakeSender(concurrency=4, autoscale=(4, 2))

    celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.concurrency == 1
    assert sender.autoscale == [1, 1]


def test_worker_init_clamps_the_autoscale_option_string(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    monkeypatch.setattr(
        celery_module,
        "get_settings",
        lambda: _settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=10240),
    )
    sender = _FakeSender(concurrency=4, options={"autoscale": "8,3", "pool": "prefork"})

    celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.concurrency == 2
    # options is changed in place: the other keys survive.
    assert sender.options == {"autoscale": [2, 2], "pool": "prefork"}


def test_worker_init_leaves_options_alone_without_a_clamp(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    monkeypatch.setattr(
        celery_module,
        "get_settings",
        lambda: _settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=0),
    )
    sender = _FakeSender(concurrency=2, options={"autoscale": "8,3", "pool": "prefork"})

    celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.concurrency == 2
    assert sender.options == {"autoscale": "8,3", "pool": "prefork"}


def test_worker_init_needs_no_autoscale_attribute(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    """A real WorkController has no autoscale attribute when worker_init fires."""
    monkeypatch.setattr(
        celery_module,
        "get_settings",
        lambda: _settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=5120),
    )
    sender = _FakeSender(concurrency=2)
    del sender.autoscale  # the shape of a real worker at worker_init

    celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.concurrency == 1


def test_worker_init_parses_an_autoscale_option_without_a_min(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    monkeypatch.setattr(
        celery_module,
        "get_settings",
        lambda: _settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=10240),
    )
    sender = _FakeSender(concurrency=4, options={"autoscale": "8,"})

    celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.options["autoscale"] == [2, 0]


def test_worker_init_without_a_budget_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    monkeypatch.setattr(
        celery_module,
        "get_settings",
        lambda: _settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=0),
    )
    sender = _FakeSender(concurrency=2)

    celery_module.on_worker_init(signal=None, sender=sender)

    assert sender.concurrency == 2


def test_worker_init_refuses_a_budget_that_fits_no_worker(
    monkeypatch: pytest.MonkeyPatch, celery_module, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        celery_module,
        "get_settings",
        lambda: _settings(asr_profile="gpu-p40-large-v3", worker_memory_budget_mib=4096),
    )
    sender = _FakeSender(concurrency=2)

    with caplog.at_level(logging.INFO):
        with pytest.raises(SystemExit) as exc:
            celery_module.on_worker_init(signal=None, sender=sender)
    assert exc.value.code == 78
    # Exactly one ERROR record before the worker exits.
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert errors[0].getMessage().startswith("worker_memory_refused")
    assert errors[0].exc_info is None


def test_worker_init_refuses_an_unknown_model_memory(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    monkeypatch.setattr(
        celery_module,
        "get_settings",
        lambda: _settings(whisper_model="custom-x", worker_memory_budget_mib=4096),
    )
    sender = _FakeSender(concurrency=2)

    with pytest.raises(SystemExit) as exc:
        celery_module.on_worker_init(signal=None, sender=sender)
    assert exc.value.code == 78


def test_worker_process_init_warms_the_model_once(
    monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    _install_fake(monkeypatch)
    monkeypatch.setattr(celery_module, "get_settings", lambda: _settings())

    celery_module.on_worker_process_init(signal=None, sender=None)

    assert warm_models.stats().loads == 1


def test_worker_process_init_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, celery_module
) -> None:
    _install_fake(monkeypatch, model_knobs={"fail_first": 99})
    monkeypatch.setattr(
        celery_module,
        "get_settings",
        lambda: _settings(whisper_model_path=str(tmp_path / "absent")),
    )

    celery_module.on_worker_process_init(signal=None, sender=None)  # must not raise


def test_worker_children_are_not_recycled(celery_module) -> None:
    assert celery_module.celery_app.conf.worker_max_tasks_per_child is None


# --- AC9: settings validation -------------------------------------------------------


@pytest.mark.parametrize("batch_size", [0, 17])
def test_settings_reject_batch_size_out_of_bounds(batch_size: int) -> None:
    with pytest.raises(ValueError):
        _settings(asr_batch_size=batch_size)


@pytest.mark.parametrize("batch_size", [1, 16])
def test_settings_accept_batch_size_bounds(batch_size: int) -> None:
    assert _settings(asr_batch_size=batch_size).asr_batch_size == batch_size


@pytest.mark.parametrize("field", ["worker_memory_budget_mib", "asr_model_memory_mib"])
@pytest.mark.parametrize("value", [-1, -512])
def test_settings_reject_negative_memory_values(field: str, value: int) -> None:
    with pytest.raises(ValueError):
        _settings(**{field: value})


def test_settings_defaults_for_the_new_fields() -> None:
    settings = _settings()
    assert settings.asr_warm_on_start is True
    assert settings.asr_batch_size == 1
    assert settings.worker_memory_budget_mib == 0
    assert settings.asr_model_memory_mib == 0


# --- AC10: batching -----------------------------------------------------------------


def test_batch_size_bounds_in_the_provider() -> None:
    for value in (0, 17, -1):
        with pytest.raises(ValueError):
            _provider(batch_size=value)
    assert _provider(batch_size=16).batch_size == 16
    assert _provider().batch_size == 1


def test_batch_size_one_is_exactly_the_plain_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_cls, pipeline_cls = _install_fake(monkeypatch)
    audio = _audio(tmp_path)

    provider = _provider(batch_size=1)
    result = _transcribe(provider, audio)

    assert result.text
    assert pipeline_cls.instances == []
    assert provider.identity().as_manifest() == {
        "provider": "local_whisper",
        "model": "base",
        "params": {"compute_type": "int8"},
    }
    assert provider.decode_identity() is None
    model = model_cls.constructed[0]
    assert [kwargs for _, kwargs in model.calls] == [
        {"beam_size": 5, "vad_filter": True, "language": "fa"}
    ]


def test_batched_pipeline_is_built_once_and_gets_batch_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, pipeline_cls = _install_fake(monkeypatch)
    audio = _audio(tmp_path)

    identities = []
    for _ in range(3):
        provider = _provider(batch_size=4)
        result = _transcribe(provider, audio)
        assert result.text
        identities.append(provider.identity().as_manifest())
        assert provider.decode_identity() == {"batch_size": 4}

    assert len(pipeline_cls.instances) == 1
    pipeline = pipeline_cls.instances[0]
    assert len(pipeline.calls) == 3
    assert all(kwargs["batch_size"] == 4 for _, kwargs in pipeline.calls)
    assert (
        identities
        == [{"provider": "local_whisper", "model": "base", "params": {"compute_type": "int8"}}] * 3
    )


def test_batched_and_plain_providers_share_one_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_cls, pipeline_cls = _install_fake(monkeypatch)
    audio = _audio(tmp_path)

    _transcribe(_provider(batch_size=1), audio)
    _transcribe(_provider(batch_size=4), audio)

    # The batch size is not in the model key: one model, one pipeline on it.
    assert len(model_cls.constructed) == 1
    assert len(pipeline_cls.instances) == 1
    assert pipeline_cls.instances[0].model is model_cls.constructed[0]


def test_batch_size_four_without_vad_stays_plain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_cls, pipeline_cls = _install_fake(monkeypatch)
    audio = _audio(tmp_path)

    provider = _provider(batch_size=4)
    result = _transcribe(provider, audio, vad_filter=False)

    assert result.text
    assert pipeline_cls.instances == []
    model = model_cls.constructed[0]
    assert len(model.calls) == 1
    assert "batch_size" not in model.calls[0][1]


def test_language_detection_always_uses_the_plain_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_cls, pipeline_cls = _install_fake(monkeypatch, model_knobs={"detect": ("ar", 0.6)})
    audio = _audio(tmp_path)

    provider = _provider(batch_size=4)
    result = _transcribe(provider, audio, language=None)

    # Detection: the plain model, no language, no batch_size.
    model = model_cls.constructed[0]
    detection = model.calls[0][1]
    assert "language" not in detection
    assert "batch_size" not in detection
    # The policy overrode ar to fa: the decode ran through the pipeline.
    assert len(pipeline_cls.instances) == 1
    decode = pipeline_cls.instances[0].calls[-1][1]
    assert decode["language"] == "fa"
    assert decode["batch_size"] == 4
    assert result.language == "fa"


def test_vad_rescue_works_on_the_batched_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, pipeline_cls = _install_fake(
        monkeypatch, model_knobs={"after_vad": 0.0, "after_vad_rescue": 3.0}
    )
    audio = _audio(tmp_path)

    provider = _provider(batch_size=4)
    result = _transcribe(provider, audio, language="fa")

    calls = pipeline_cls.instances[0].calls
    assert len(calls) == 2
    assert calls[0][1]["batch_size"] == 4
    assert "vad_parameters" not in calls[0][1]
    assert calls[1][1]["batch_size"] == 4
    assert calls[1][1]["vad_parameters"] == RESCUE_VAD_PARAMETERS
    assert result.text == "text-fa"


def test_decide_language_stays_plain_when_batched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_cls, pipeline_cls = _install_fake(monkeypatch)
    audio = _audio(tmp_path)

    decision = _provider(batch_size=4).decide_language(audio, None)

    assert decision.language == "fa"
    assert pipeline_cls.instances == []
    model = model_cls.constructed[0]
    assert len(model.calls) == 1
    assert "batch_size" not in model.calls[0][1]


# --- AC11: checkpoint identity ------------------------------------------------------


class _DecodeASR(ASRProvider):
    """Mock ASR that only declares a decode identity."""

    def __init__(self, decode: dict | None) -> None:
        self._inner = _mock_asr()
        self._decode = decode

    def identity(self):
        return self._inner.identity()

    def decode_identity(self) -> dict | None:
        return self._decode

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        return await self._inner.transcribe(request)


def _mock_asr():
    from backend.providers.mocks import MockASRProvider

    return MockASRProvider(allow_mock=True, transcript="پیگیری قرارداد انجام شود")


def _asr_requests(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    from backend.jobs import checkpoints as ckpt
    from backend.jobs.states import StageName

    captured: list[dict[str, Any]] = []
    original = ckpt.stage_identity

    def spy(context, stage, *, provider, request, upstream=None):
        if stage is StageName.ASR:
            captured.append(dict(request))
        return original(context, stage, provider=provider, request=request, upstream=upstream)

    monkeypatch.setattr(ckpt, "stage_identity", spy)
    return captured


@pytest.mark.parametrize(
    ("decode", "expected_extra"),
    [
        (None, {}),
        ({"batch_size": 4}, {"decode": {"batch_size": 4}}),
    ],
)
def test_processor_adds_the_decode_identity(
    client, email_outbox, settings, monkeypatch, decode, expected_extra
) -> None:
    from backend.jobs.processor import process_job

    job_id, _ = _queued_upload(client, email_outbox, monkeypatch)
    captured = _asr_requests(monkeypatch)
    providers = ProviderBundle(
        asr=_DecodeASR(decode),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=MockIntelligenceProvider(allow_mock=True),
    )
    result = process_job(job_id, worker_id="hom41-worker", settings=settings, providers=providers)
    assert result["status"] in {"completed", "partial"}
    assert captured == [{"language": "fa", "beam_size": 5, "vad_filter": True, **expected_extra}]


def test_processor_keeps_the_base_identity_without_the_hook(
    client, email_outbox, settings, monkeypatch
) -> None:
    from backend.jobs.processor import process_job

    job_id, _ = _queued_upload(client, email_outbox, monkeypatch)
    captured = _asr_requests(monkeypatch)
    providers = ProviderBundle(
        asr=_mock_asr(),  # no decode_identity at all
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=MockIntelligenceProvider(allow_mock=True),
    )
    result = process_job(job_id, worker_id="hom41-plain", settings=settings, providers=providers)
    assert result["status"] in {"completed", "partial"}
    assert captured == [{"language": "fa", "beam_size": 5, "vad_filter": True}]
