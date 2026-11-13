"""HOM-41 oracle: warm ASR workers, memory budget and bounded batching (SPEC section 3)."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import logging
import sys
import threading
import time
import types
import weakref
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from celery.concurrency import ALIASES as CELERY_POOL_ALIASES
from celery.concurrency import get_implementation
from celery.concurrency.prefork import TaskPool as PreforkPool
from celery.concurrency.solo import TaskPool as SoloPool
from celery.concurrency.thread import TaskPool as ThreadPool
from celery.signals import worker_init, worker_process_init, worker_ready
from celery.worker.components import Pool as CeleryPoolStep
from pydantic import ValidationError

import backend.celery_app as celery_module
import backend.config as config_module
from backend.config import Settings
from backend.jobs import checkpoints as ckpt
from backend.jobs.processor import process_job
from backend.jobs.states import StageName
from backend.providers.asr_profiles import ASR_PROFILES
from backend.providers.base import ASRProvider, ProviderError
from backend.providers.factory import ProviderBundle, create_asr_provider
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.schemas import ASRRequest, ASRResult
from backend.providers.whisper import RESCUE_VAD_PARAMETERS, LocalWhisperProvider
from backend.speech.language_policy import LanguagePolicy
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider, _queued_upload

WARM_MODULE = "backend.providers.warm_models"
CAPACITY_MODULE = "backend.jobs.worker_capacity"
GPU = "gpu-p40-large-v3"
PRIVATE_DETAIL = "private-load-detail-hom41"
PRIVATE_DIR = "private-model-dir-hom41"
MISSING_DIR_MESSAGE = "The local Whisper model directory is not present"
NEW_ENV = (
    "ASR_WARM_ON_START",
    "ASR_BATCH_SIZE",
    "WORKER_MEMORY_BUDGET_MIB",
    "ASR_MODEL_MEMORY_MIB",
)
ASR_ENV = (
    "ASR_PROVIDER",
    "ASR_PROFILE",
    "WHISPER_MODEL",
    "WHISPER_DEVICE",
    "WHISPER_COMPUTE_TYPE",
    "WHISPER_MODEL_PATH",
    "WHISPER_LOCAL_FILES_ONLY",
    *NEW_ENV,
)
BASE_ASR_REQUEST = {"language": "fa", "beam_size": 5, "vad_filter": True}
ORIGINAL_GET_SETTINGS = config_module.get_settings


def _warm() -> Any:
    return importlib.import_module(WARM_MODULE)


def _capacity() -> Any:
    return importlib.import_module(CAPACITY_MODULE)


def _clear_warm_cache() -> None:
    try:
        module = importlib.import_module(WARM_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != WARM_MODULE:
            raise
        return
    module.clear()


@pytest.fixture(autouse=True)
def _isolated_warm_cache():
    _clear_warm_cache()
    yield
    _clear_warm_cache()


class FakeFasterWhisper:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.models: list[Any] = []
        self.model_args: list[tuple[str, dict[str, Any]]] = []
        self.pipelines: list[Any] = []
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.load_errors: list[BaseException] = []
        self.load_delay = 0.0
        self.first_kept = 3.0
        self.rescue_kept = 3.0
        self.detected = "en"
        self.module = self._build_module()

    @property
    def constructions(self) -> int:
        return len(self.model_args)

    def calls_on(self, origin: str) -> list[dict[str, Any]]:
        return [kwargs for source, _, kwargs in self.calls if source == origin]

    def respond(self, origin: str, audio: Any, kwargs: dict[str, Any]):
        with self.lock:
            self.calls.append((origin, str(audio), dict(kwargs)))
        rescued = "vad_parameters" in kwargs
        kept = self.rescue_kept if rescued else self.first_kept
        language = kwargs.get("language")
        label = f"{origin}-{'rescue' if rescued else 'first'}-{language or 'auto'}"
        segments = [SimpleNamespace(start=0.0, end=1.0, text=f"text-{label}")]
        if language is None:
            info = SimpleNamespace(
                language=self.detected,
                language_probability=0.9,
                all_language_probs=[(self.detected, 0.9)],
                duration=1.5,
                duration_after_vad=kept,
            )
        else:
            info = SimpleNamespace(language=language, duration=1.5, duration_after_vad=kept)
        return iter(segments), info

    def _build_module(self) -> types.ModuleType:
        fake = self

        class WhisperModel:
            def __init__(self, model_size_or_path: str, **kwargs: Any) -> None:
                with fake.lock:
                    fake.model_args.append((model_size_or_path, dict(kwargs)))
                    error = fake.load_errors.pop(0) if fake.load_errors else None
                if fake.load_delay:
                    time.sleep(fake.load_delay)
                if error is not None:
                    raise error
                with fake.lock:
                    fake.models.append(self)

            def transcribe(self, audio: Any, **kwargs: Any):
                return fake.respond("plain", audio, kwargs)

        class BatchedInferencePipeline:
            def __init__(self, model: Any = None, **kwargs: Any) -> None:
                self.model = model
                with fake.lock:
                    fake.pipelines.append(self)

            def transcribe(self, audio: Any, **kwargs: Any):
                return fake.respond("batched", audio, kwargs)

        module = types.ModuleType("faster_whisper")
        module.WhisperModel = WhisperModel  # type: ignore[attr-defined]
        module.BatchedInferencePipeline = BatchedInferencePipeline  # type: ignore[attr-defined]
        return module


class _InjectedModel:
    def __init__(self, fake: FakeFasterWhisper) -> None:
        self._fake = fake

    def transcribe(self, audio: Any, **kwargs: Any):
        return self._fake.respond("injected", audio, kwargs)


@pytest.fixture
def fake_whisper(monkeypatch: pytest.MonkeyPatch) -> FakeFasterWhisper:
    fake = FakeFasterWhisper()
    monkeypatch.setitem(sys.modules, "faster_whisper", fake.module)
    return fake


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
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _bare_settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        local_ai_base_url="http://intelligence.test/v1",
        llm_model="conversation-model",
    )


def _audio(tmp_path: Path) -> str:
    path = tmp_path / "call.wav"
    path.write_bytes(b"RIFF....WAVE")
    return str(path)


def _provider(**overrides: Any) -> LocalWhisperProvider:
    values: dict[str, Any] = {"model_name": "base", "device": "cpu", "compute_type": "int8"}
    values.update(overrides)
    return LocalWhisperProvider(**values)


def _transcribe(
    provider: Any, audio: str, language: str | None = "fa", *, vad_filter: bool = True
) -> ASRResult:
    request = ASRRequest(audio_path=audio, language=language, vad_filter=vad_filter)
    return asyncio.run(provider.transcribe(request))


def _counts(warm: Any) -> tuple[int, int, int, int]:
    stats = warm.stats()
    return (stats.loads, stats.hits, stats.failures, stats.keys)


def _in_thread(call: Callable[[], Any], timeout: float = 10.0) -> Any:
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["value"] = call()
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), "the call did not finish: a lock is still held"
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def _event_records(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if event in record.getMessage().lower() or getattr(record, "event", None) == event
    ]


def _everything_logged(caplog: pytest.LogCaptureFixture) -> str:
    skipped = {"pathname", "filename", "module", "funcName", "processName", "threadName"}
    parts = [caplog.text]
    for record in caplog.records:
        parts.append(record.getMessage())
        parts.append(
            repr({key: value for key, value in vars(record).items() if key not in skipped})
        )
    return "\n".join(parts)


@pytest.mark.parametrize(
    ("overrides", "loaded"),
    [
        ({}, ("base", {"device": "cpu", "compute_type": "int8"})),
        ({"asr_profile": GPU}, ("large-v3", {"device": "cuda", "compute_type": "int8_float32"})),
    ],
    ids=["cpu-default", "gpu-p40-profile"],
)
def test_ac1_ten_sequential_jobs_load_the_model_once(
    fake_whisper: FakeFasterWhisper, tmp_path: Path, overrides: dict[str, Any], loaded: Any
) -> None:
    settings = _settings(**overrides)
    audio = _audio(tmp_path)

    results = [_transcribe(create_asr_provider(settings), audio) for _ in range(10)]

    assert [result.text for result in results] == ["text-plain-first-fa"] * 10
    assert fake_whisper.constructions == 1
    assert fake_whisper.model_args == [loaded]
    assert len(fake_whisper.calls_on("plain")) == 10
    stats = _warm().stats()
    assert stats.loads == 1
    assert stats.hits >= 9
    assert stats.failures == 0
    assert stats.keys == 1


@pytest.mark.parametrize(
    ("first", "second", "loads"),
    [
        ({"whisper_compute_type": "int8"}, {"whisper_compute_type": "int8_float32"}, 2),
        ({"whisper_device": "cpu"}, {"whisper_device": "cuda"}, 2),
        ({"whisper_model": "base"}, {"whisper_model": "small"}, 2),
        ({"asr_profile": "cpu-small"}, {"asr_profile": GPU}, 2),
        ({"whisper_local_files_only": False}, {"whisper_local_files_only": True}, 2),
        ({"asr_default_language": "fa"}, {"asr_default_language": "en"}, 1),
        ({"asr_language_min_probability": 0.5}, {"asr_language_min_probability": 0.8}, 1),
        ({"asr_profile": "cpu-base"}, {}, 1),
    ],
    ids=[
        "compute-type",
        "device",
        "model",
        "profile",
        "local-files-only",
        "policy-default-language",
        "policy-min-probability",
        "cpu-base-profile-equals-explicit-base",
    ],
)
def test_ac2_settings_that_change_the_model_load_separately(
    fake_whisper: FakeFasterWhisper,
    tmp_path: Path,
    first: dict[str, Any],
    second: dict[str, Any],
    loads: int,
) -> None:
    audio = _audio(tmp_path)

    _transcribe(create_asr_provider(_settings(**first)), audio)
    _transcribe(create_asr_provider(_settings(**second)), audio)
    _transcribe(create_asr_provider(_settings(**first)), audio)

    assert fake_whisper.constructions == loads
    stats = _warm().stats()
    assert (stats.loads, stats.keys) == (loads, loads)
    assert stats.hits >= 1


@pytest.mark.parametrize(
    ("first", "second", "loads"),
    [
        ({"device_index": 0}, {"device_index": (0,)}, 1),
        ({"device_index": 0}, {"device_index": 1}, 2),
        ({"device_index": (0, 1)}, {"device_index": 0}, 2),
        ({"cpu_threads": 0}, {"cpu_threads": 4}, 2),
        ({"local_files_only": False}, {"local_files_only": True}, 2),
        (
            {"language_policy": LanguagePolicy()},
            {"language_policy": LanguagePolicy(default="en", min_probability=0.7)},
            1,
        ),
    ],
    ids=[
        "device-index-int-equals-tuple",
        "device-index-differs",
        "device-index-list-differs",
        "cpu-threads",
        "local-files-only",
        "language-policy-only",
    ],
)
def test_ac2_provider_key_holds_every_value_that_changes_the_load(
    fake_whisper: FakeFasterWhisper,
    tmp_path: Path,
    first: dict[str, Any],
    second: dict[str, Any],
    loads: int,
) -> None:
    audio = _audio(tmp_path)

    _transcribe(_provider(**first), audio)
    _transcribe(_provider(**second), audio)

    assert fake_whisper.constructions == loads
    assert _warm().stats().loads == loads


def test_ac2_model_path_is_part_of_the_key(fake_whisper: FakeFasterWhisper, tmp_path: Path) -> None:
    audio = _audio(tmp_path)
    first = tmp_path / "model-a"
    second = tmp_path / "model-b"
    first.mkdir()
    second.mkdir()

    _transcribe(_provider(model_path=str(first)), audio)
    _transcribe(_provider(model_path=str(second)), audio)
    _transcribe(_provider(model_path=str(first)), audio)

    assert [source for source, _ in fake_whisper.model_args] == [str(first), str(second)]
    assert _warm().stats().loads == 2


def test_ac2_an_injected_factory_is_part_of_the_key(
    fake_whisper: FakeFasterWhisper, tmp_path: Path
) -> None:
    audio = _audio(tmp_path)
    built: list[str] = []

    def factory_a(source: str, **kwargs: Any) -> Any:
        built.append("a")
        return fake_whisper.module.WhisperModel(source, **kwargs)

    def factory_b(source: str, **kwargs: Any) -> Any:
        built.append("b")
        return fake_whisper.module.WhisperModel(source, **kwargs)

    _transcribe(_provider(model_factory=factory_a), audio)
    _transcribe(_provider(model_factory=factory_a), audio)
    _transcribe(_provider(model_factory=factory_b), audio)
    _transcribe(_provider(), audio)
    _transcribe(_provider(), audio)

    assert built == ["a", "b"]
    assert fake_whisper.constructions == 3
    stats = _warm().stats()
    assert (stats.loads, stats.keys) == (3, 3)


def test_ac3_a_failed_load_is_not_cached_and_keeps_the_error_mapping(
    fake_whisper: FakeFasterWhisper, tmp_path: Path
) -> None:
    fake_whisper.load_errors.append(RuntimeError(PRIVATE_DETAIL))
    audio = _audio(tmp_path)
    settings = _settings()
    provider = create_asr_provider(settings)

    with pytest.raises(ProviderError) as failed:
        _transcribe(provider, audio)

    assert failed.value.code == "asr_failed"
    assert failed.value.retryable is False
    warm = _warm()
    assert _counts(warm) == (0, 0, 1, 0)

    assert _transcribe(provider, audio).text == "text-plain-first-fa"
    assert _transcribe(create_asr_provider(settings), audio).text == "text-plain-first-fa"
    assert fake_whisper.constructions == 2
    loads, hits, failures, keys = _counts(warm)
    assert (loads, failures, keys) == (1, 1, 1)
    assert hits >= 1


def test_ac3_a_missing_library_maps_to_asr_unavailable_and_is_tried_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    audio = _audio(tmp_path)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    provider = create_asr_provider(_settings())

    with pytest.raises(ProviderError) as missing:
        _transcribe(provider, audio)

    assert missing.value.code == "asr_unavailable"
    warm = _warm()
    assert _counts(warm) == (0, 0, 1, 0)

    fake = FakeFasterWhisper()
    monkeypatch.setitem(sys.modules, "faster_whisper", fake.module)
    assert _transcribe(provider, audio).text == "text-plain-first-fa"
    assert fake.constructions == 1
    assert _counts(warm)[0] == 1


def test_ac3_the_model_directory_check_runs_before_a_load_and_only_on_a_miss(
    fake_whisper: FakeFasterWhisper, tmp_path: Path
) -> None:
    audio = _audio(tmp_path)
    model_dir = tmp_path / "pinned-model"
    settings = _settings(whisper_model_path=str(model_dir))

    with pytest.raises(ProviderError) as missing:
        _transcribe(create_asr_provider(settings), audio)

    assert missing.value.code == "asr_model_missing"
    assert fake_whisper.constructions == 0
    assert _warm().stats().keys == 0

    model_dir.mkdir()
    assert _transcribe(create_asr_provider(settings), audio).text == "text-plain-first-fa"
    assert fake_whisper.model_args[0][0] == str(model_dir)

    model_dir.rmdir()
    assert _transcribe(create_asr_provider(settings), audio).text == "text-plain-first-fa"
    assert fake_whisper.constructions == 1
    assert _warm().stats().loads == 1


def test_ac3_get_or_load_stores_a_success_and_never_a_failure() -> None:
    warm = _warm()
    error = ValueError("loader failed")

    def failing() -> Any:
        raise error

    with pytest.raises(ValueError) as raised:
        warm.get_or_load(("model", "a"), failing)

    assert raised.value is error
    assert _counts(warm) == (0, 0, 1, 0)

    loaded = object()
    calls: list[int] = []

    def loader() -> Any:
        calls.append(1)
        return loaded

    assert warm.get_or_load(("model", "a"), loader) is loaded
    assert warm.get_or_load(("model", "a"), loader) is loaded
    assert calls == [1]
    assert _counts(warm) == (1, 1, 1, 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        warm.stats().loads = 5


def test_ac3_a_failed_load_releases_the_lock_for_every_key() -> None:
    warm = _warm()

    def failing() -> Any:
        raise RuntimeError("loader failed")

    with pytest.raises(RuntimeError):
        _in_thread(lambda: warm.get_or_load("broken", failing))

    assert _in_thread(lambda: warm.get_or_load("other", lambda: "ready")) == "ready"
    assert _in_thread(lambda: warm.get_or_load("broken", lambda: "fixed")) == "fixed"
    assert _counts(warm) == (2, 0, 1, 2)


def test_clear_drops_every_model_and_resets_the_counters() -> None:
    warm = _warm()
    warm.get_or_load("a", object)
    warm.get_or_load("a", object)
    warm.get_or_load("b", object)
    assert _counts(warm) == (2, 1, 0, 2)

    warm.clear()

    assert _counts(warm) == (0, 0, 0, 0)
    calls: list[int] = []
    warm.get_or_load("a", lambda: calls.append(1) or "again")
    assert calls == [1]


def test_ac4_concurrent_callers_of_one_key_run_one_loader() -> None:
    warm = _warm()
    barrier = threading.Barrier(8)
    calls: list[int] = []
    lock = threading.Lock()
    results: list[Any] = [None] * 8
    errors: list[BaseException] = []

    def loader() -> Any:
        with lock:
            calls.append(1)
        time.sleep(0.2)
        return object()

    def caller(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            results[index] = warm.get_or_load(("shared",), loader)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=caller, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert errors == []
    assert calls == [1]
    assert results[0] is not None
    assert all(result is results[0] for result in results)
    assert _counts(warm) == (1, 7, 0, 1)


def test_ac4_eight_concurrent_first_jobs_construct_the_model_once(
    fake_whisper: FakeFasterWhisper, tmp_path: Path
) -> None:
    fake_whisper.load_delay = 0.3
    settings = _settings()
    audio = _audio(tmp_path)
    barrier = threading.Barrier(8)
    results: list[Any] = [None] * 8
    errors: list[BaseException] = []

    def job(index: int) -> None:
        try:
            provider = create_asr_provider(settings)
            barrier.wait(timeout=10)
            results[index] = _transcribe(provider, audio)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=job, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert errors == []
    assert [result.text for result in results] == ["text-plain-first-fa"] * 8
    assert fake_whisper.constructions == 1
    assert len(fake_whisper.calls_on("plain")) == 8
    stats = _warm().stats()
    assert (stats.loads, stats.failures, stats.keys) == (1, 0, 1)


def test_ac5_an_injected_model_never_touches_the_cache(
    fake_whisper: FakeFasterWhisper, tmp_path: Path
) -> None:
    audio = _audio(tmp_path)
    warm = _warm()

    injected = _transcribe(_provider(model=_InjectedModel(fake_whisper)), audio)

    assert injected.text == "text-injected-first-fa"
    assert fake_whisper.constructions == 0
    assert _counts(warm) == (0, 0, 0, 0)

    loaded = _transcribe(_provider(), audio)

    assert loaded.text == "text-plain-first-fa"
    assert fake_whisper.constructions == 1
    assert _counts(warm)[0] == 1


@pytest.mark.parametrize(
    "provider_name",
    ["local_whisper", "Local-Whisper", " local-whisper "],
    ids=["canonical", "mixed-case-dash", "padded-dash"],
)
def test_ac6_warm_on_start_loads_the_model_before_the_first_job(
    fake_whisper: FakeFasterWhisper, tmp_path: Path, provider_name: str
) -> None:
    settings = _settings(asr_provider=provider_name, asr_warm_on_start=True)
    warm = _warm()

    assert warm.warm_configured_asr(settings) is True
    assert fake_whisper.constructions == 1

    assert _transcribe(create_asr_provider(settings), _audio(tmp_path)).text == (
        "text-plain-first-fa"
    )
    assert fake_whisper.constructions == 1
    loads, hits, failures, _ = _counts(warm)
    assert (loads, failures) == (1, 0)
    assert hits >= 1


def test_ac6_warm_on_start_is_on_by_default_and_follows_the_profile(
    fake_whisper: FakeFasterWhisper, tmp_path: Path
) -> None:
    settings = _settings(asr_profile=GPU)
    warm = _warm()

    assert warm.warm_configured_asr(settings) is True
    _transcribe(create_asr_provider(settings), _audio(tmp_path))

    assert fake_whisper.model_args == [
        ("large-v3", {"device": "cuda", "compute_type": "int8_float32"})
    ]
    assert _counts(warm)[0] == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"asr_warm_on_start": False},
        {"asr_warm_on_start": True, "asr_provider": "mock"},
        {"asr_warm_on_start": True, "asr_provider": "mock", "allow_mock_providers": False},
    ],
    ids=["warm-off", "mock-provider", "mock-provider-not-allowed"],
)
def test_ac6_warm_on_start_loads_nothing_when_off_or_not_local_whisper(
    fake_whisper: FakeFasterWhisper, overrides: dict[str, Any]
) -> None:
    warm = _warm()

    assert warm.warm_configured_asr(_settings(**overrides)) is False

    assert fake_whisper.constructions == 0
    assert _counts(warm) == (0, 0, 0, 0)


def test_ac6_with_warm_off_the_first_job_still_loads_once(
    fake_whisper: FakeFasterWhisper, tmp_path: Path
) -> None:
    settings = _settings(asr_warm_on_start=False)
    audio = _audio(tmp_path)

    assert _warm().warm_configured_asr(settings) is False
    _transcribe(create_asr_provider(settings), audio)
    _transcribe(create_asr_provider(settings), audio)

    assert fake_whisper.constructions == 1


def test_ac6_a_failed_warm_returns_false_and_logs_no_path_or_message(
    fake_whisper: FakeFasterWhisper, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    model_dir = tmp_path / PRIVATE_DIR
    model_dir.mkdir()
    fake_whisper.load_errors.append(RuntimeError(f"{PRIVATE_DETAIL} while reading {model_dir}"))
    settings = _settings(whisper_model_path=str(model_dir))
    warm = _warm()

    assert warm.warm_configured_asr(settings) is False

    records = _event_records(caplog, "asr_warm_failed")
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert not records[0].exc_info
    logged = _everything_logged(caplog)
    assert str(model_dir) not in logged
    assert PRIVATE_DIR not in logged
    assert PRIVATE_DETAIL not in logged
    assert warm.stats().keys == 0

    assert _transcribe(create_asr_provider(settings), _audio(tmp_path)).text == (
        "text-plain-first-fa"
    )
    assert fake_whisper.constructions == 2


def test_ac6_a_missing_model_directory_fails_the_warm_quietly(
    fake_whisper: FakeFasterWhisper, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    model_dir = tmp_path / PRIVATE_DIR

    assert _warm().warm_configured_asr(_settings(whisper_model_path=str(model_dir))) is False

    records = _event_records(caplog, "asr_warm_failed")
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert not records[0].exc_info
    logged = _everything_logged(caplog)
    assert str(model_dir) not in logged
    assert PRIVATE_DIR not in logged
    assert MISSING_DIR_MESSAGE not in logged
    assert fake_whisper.constructions == 0


def test_ac6_a_missing_library_fails_the_warm_quietly(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)

    assert _warm().warm_configured_asr(_settings()) is False

    records = _event_records(caplog, "asr_warm_failed")
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert not records[0].exc_info


def test_ac7_profiles_declare_their_resident_memory() -> None:
    assert {name: profile.memory_mib for name, profile in ASR_PROFILES.items()} == {
        "cpu-base": 512,
        "cpu-small": 1024,
        "cpu-medium": 2048,
        "cpu-large-v3-turbo": 2560,
        "cpu-large-v3": 3584,
        "gpu-p40-large-v3": 4608,
    }


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"asr_provider": "mock"}, 0),
        ({"asr_provider": "mock", "asr_model_memory_mib": 1000}, 0),
        ({"whisper_model": "tiny"}, 384),
        ({"whisper_model": "base"}, 512),
        ({"whisper_model": "small"}, 1024),
        ({"whisper_model": "medium"}, 2048),
        ({"whisper_model": "large-v3-turbo"}, 2560),
        ({"whisper_model": "large-v3"}, 3584),
        ({"asr_profile": GPU}, 4608),
        ({"asr_profile": "cpu-small", "whisper_model": "large-v3"}, 1024),
        ({"asr_profile": GPU, "asr_model_memory_mib": 1000}, 1000),
        ({"whisper_model": "custom-x"}, None),
        ({"whisper_model": "custom-x", "asr_batch_size": 4}, None),
        ({"whisper_model": "custom-x", "asr_model_memory_mib": 1000}, 1000),
        ({"whisper_model": "base", "asr_batch_size": 4}, 1280),
        ({"asr_profile": GPU, "asr_batch_size": 4}, 5376),
        ({"asr_provider": "Local-Whisper", "whisper_model": "small"}, 1024),
    ],
    ids=[
        "mock",
        "mock-with-override",
        "tiny",
        "base",
        "small",
        "medium",
        "large-v3-turbo",
        "large-v3",
        "gpu-profile",
        "profile-wins-over-model",
        "override-wins-over-profile",
        "unknown-model",
        "unknown-model-batched",
        "unknown-model-with-override",
        "base-batched",
        "gpu-profile-batched",
        "provider-name-spelling",
    ],
)
def test_ac7_asr_memory_estimate(overrides: dict[str, Any], expected: int | None) -> None:
    assert _capacity().asr_memory_estimate_mib(_settings(**overrides)) == expected


@pytest.mark.parametrize(
    ("pool", "expected"),
    [
        ("prefork", 3),
        (PreforkPool, 3),
        ("processes", 3),
        ("custom-pool", 3),
        ("solo", 1),
        (SoloPool, 1),
        ("threads", 1),
        (ThreadPool, 1),
        ("gevent", 1),
        ("eventlet", 1),
    ],
    ids=[
        "prefork",
        "prefork-class",
        "processes-alias",
        "unknown-counts-as-prefork",
        "solo",
        "solo-class",
        "threads",
        "threads-class",
        "gevent",
        "eventlet",
    ],
)
def test_ac7_worker_processes(pool: Any, expected: int) -> None:
    assert _capacity().worker_processes(pool, 3) == expected


DECISION_ROWS = [
    (
        "gpu-c1-budget-5120",
        "prefork",
        1,
        {"asr_profile": GPU, "worker_memory_budget_mib": 5120},
        {
            "ok": True,
            "reason": "within_budget",
            "processes": 1,
            "allowed_processes": 1,
            "per_process_mib": 5120,
            "required_mib": 5120,
            "budget_mib": 5120,
        },
    ),
    (
        "gpu-c2-budget-5120",
        "prefork",
        2,
        {"asr_profile": GPU, "worker_memory_budget_mib": 5120},
        {
            "ok": False,
            "reason": "over_budget",
            "processes": 2,
            "allowed_processes": 1,
            "per_process_mib": 5120,
            "required_mib": 10240,
            "budget_mib": 5120,
        },
    ),
    (
        "gpu-c2-prefork-class-budget-5120",
        PreforkPool,
        2,
        {"asr_profile": GPU, "worker_memory_budget_mib": 5120},
        {
            "ok": False,
            "reason": "over_budget",
            "processes": 2,
            "allowed_processes": 1,
            "per_process_mib": 5120,
            "required_mib": 10240,
            "budget_mib": 5120,
        },
    ),
    (
        "gpu-c2-no-budget",
        "prefork",
        2,
        {"asr_profile": GPU, "worker_memory_budget_mib": 0},
        {"ok": True, "reason": "no_budget", "processes": 2, "budget_mib": 0},
    ),
    (
        "gpu-c1-budget-4096",
        "prefork",
        1,
        {"asr_profile": GPU, "worker_memory_budget_mib": 4096},
        {
            "ok": False,
            "reason": "budget_too_small",
            "processes": 1,
            "allowed_processes": 0,
            "per_process_mib": 5120,
            "required_mib": 5120,
            "budget_mib": 4096,
        },
    ),
    (
        "cpu-base-profile-c2-budget-2048",
        "prefork",
        2,
        {"asr_profile": "cpu-base", "worker_memory_budget_mib": 2048},
        {
            "ok": True,
            "reason": "within_budget",
            "processes": 2,
            "allowed_processes": 2,
            "per_process_mib": 1024,
            "required_mib": 2048,
            "budget_mib": 2048,
        },
    ),
    (
        "base-model-c2-budget-2048",
        "prefork",
        2,
        {"whisper_model": "base", "worker_memory_budget_mib": 2048},
        {
            "ok": True,
            "reason": "within_budget",
            "processes": 2,
            "allowed_processes": 2,
            "per_process_mib": 1024,
            "required_mib": 2048,
            "budget_mib": 2048,
        },
    ),
    (
        "solo-c4-gpu-budget-5120",
        "solo",
        4,
        {"asr_profile": GPU, "worker_memory_budget_mib": 5120},
        {
            "ok": True,
            "reason": "within_budget",
            "processes": 1,
            "allowed_processes": 1,
            "per_process_mib": 5120,
            "required_mib": 5120,
            "budget_mib": 5120,
        },
    ),
    (
        "solo-class-c4-gpu-budget-5120",
        SoloPool,
        4,
        {"asr_profile": GPU, "worker_memory_budget_mib": 5120},
        {"ok": True, "reason": "within_budget", "processes": 1, "required_mib": 5120},
    ),
    (
        "threads-c4-gpu-budget-5120",
        "threads",
        4,
        {"asr_profile": GPU, "worker_memory_budget_mib": 5120},
        {"ok": True, "reason": "within_budget", "processes": 1, "required_mib": 5120},
    ),
    (
        "unknown-model-budget-4096",
        "prefork",
        1,
        {"whisper_model": "custom-x", "worker_memory_budget_mib": 4096},
        {
            "ok": False,
            "reason": "unknown_model_memory",
            "processes": 1,
            "allowed_processes": None,
            "per_process_mib": None,
            "required_mib": None,
            "budget_mib": 4096,
        },
    ),
    (
        "unknown-model-with-override-budget-4096",
        "prefork",
        2,
        {
            "whisper_model": "custom-x",
            "asr_model_memory_mib": 1000,
            "worker_memory_budget_mib": 4096,
        },
        {
            "ok": True,
            "reason": "within_budget",
            "processes": 2,
            "allowed_processes": 2,
            "per_process_mib": 1512,
            "required_mib": 3024,
            "budget_mib": 4096,
        },
    ),
    (
        "unknown-model-no-budget",
        "prefork",
        1,
        {"whisper_model": "custom-x", "worker_memory_budget_mib": 0},
        {"ok": True, "reason": "no_budget", "processes": 1, "budget_mib": 0},
    ),
    (
        "gpu-batch-4-c1-budget-5120",
        "prefork",
        1,
        {"asr_profile": GPU, "asr_batch_size": 4, "worker_memory_budget_mib": 5120},
        {
            "ok": False,
            "reason": "budget_too_small",
            "processes": 1,
            "allowed_processes": 0,
            "per_process_mib": 5888,
            "required_mib": 5888,
            "budget_mib": 5120,
        },
    ),
    (
        "base-batch-4-c1-budget-2048",
        "prefork",
        1,
        {"whisper_model": "base", "asr_batch_size": 4, "worker_memory_budget_mib": 2048},
        {
            "ok": True,
            "reason": "within_budget",
            "processes": 1,
            "allowed_processes": 1,
            "per_process_mib": 1792,
            "required_mib": 1792,
            "budget_mib": 2048,
        },
    ),
    (
        "mock-budget-1",
        "prefork",
        2,
        {"asr_provider": "mock", "worker_memory_budget_mib": 1},
        {"ok": True, "reason": "no_asr_model", "processes": 2, "budget_mib": 1},
    ),
    (
        "mock-budget-5120",
        "prefork",
        2,
        {"asr_provider": "mock", "worker_memory_budget_mib": 5120},
        {"ok": True, "reason": "no_asr_model", "processes": 2, "budget_mib": 5120},
    ),
    (
        "mock-no-budget",
        "prefork",
        2,
        {"asr_provider": "mock", "worker_memory_budget_mib": 0},
        {"ok": True, "processes": 2},
    ),
]


@pytest.mark.parametrize(
    ("pool", "concurrency", "overrides", "expected"),
    [row[1:] for row in DECISION_ROWS],
    ids=[row[0] for row in DECISION_ROWS],
)
def test_ac7_memory_decisions(
    pool: Any, concurrency: int, overrides: dict[str, Any], expected: dict[str, Any]
) -> None:
    decision = _capacity().check_worker_memory(
        _settings(**overrides), pool=pool, concurrency=concurrency
    )

    assert {name: getattr(decision, name) for name in expected} == expected


def test_ac7_batch_size_four_adds_768_mib_per_process() -> None:
    capacity = _capacity()

    def per_process(batch_size: int) -> int:
        settings = _settings(
            asr_profile=GPU, asr_batch_size=batch_size, worker_memory_budget_mib=100000
        )
        return capacity.check_worker_memory(settings, pool="prefork", concurrency=1).per_process_mib

    assert per_process(4) - per_process(1) == 768


def test_ac7_capacity_constants_and_types() -> None:
    capacity = _capacity()
    decision = capacity.check_worker_memory(
        _settings(asr_profile=GPU, worker_memory_budget_mib=5120), pool="prefork", concurrency=1
    )

    assert capacity.WORKER_BASE_MEMORY_MIB == 512
    assert capacity.ASR_BATCH_SLOT_MEMORY_MIB == 256
    assert issubclass(capacity.WorkerCapacityError, RuntimeError)
    assert dataclasses.is_dataclass(decision)
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.ok = False


def _homayar_receivers(signal: Any) -> list[Callable[..., Any]]:
    found = []
    for _key, reference in signal.receivers:
        receiver = reference() if isinstance(reference, weakref.ReferenceType) else reference
        module = str(getattr(receiver, "__module__", "") or "")
        if receiver is not None and module.startswith("backend."):
            found.append(receiver)
    return found


def _use_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    _warm()
    _capacity()

    def replacement() -> Settings:
        return settings

    replacement.hom41_oracle = True  # type: ignore[attr-defined]
    for name, module in list(sys.modules.items()):
        if not (name == "backend" or name.startswith("backend.")):
            continue
        current = getattr(module, "get_settings", None)
        if current is ORIGINAL_GET_SETTINGS or getattr(current, "hom41_oracle", False):
            monkeypatch.setattr(module, "get_settings", replacement)


def _fire(signal: Any, sender: Any, **extra: Any) -> None:
    receivers = _homayar_receivers(signal)
    assert receivers, f"no Homayar receiver is connected to {signal.name}"
    for receiver in receivers:
        receiver(signal=signal, sender=sender, **extra)


@pytest.mark.parametrize("pool_cls", ["prefork", PreforkPool], ids=["prefork", "prefork-class"])
def test_ac8_worker_init_clamps_concurrency_to_the_budget(
    fake_whisper: FakeFasterWhisper,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    pool_cls: Any,
) -> None:
    caplog.set_level(logging.INFO)
    _use_settings(monkeypatch, _settings(asr_profile=GPU, worker_memory_budget_mib=5120))
    sender = SimpleNamespace(concurrency=2, pool_cls=pool_cls, autoscale=None)

    _fire(worker_init, sender)

    assert sender.concurrency == 1
    assert sender.autoscale is None
    assert _event_records(caplog, "worker_memory_check")
    assert _event_records(caplog, "worker_concurrency_clamped")
    assert fake_whisper.constructions == 0
    assert _counts(_warm()) == (0, 0, 0, 0)


@pytest.mark.parametrize(
    ("overrides", "pool_cls", "concurrency"),
    [
        ({"asr_profile": GPU, "worker_memory_budget_mib": 0}, "prefork", 2),
        ({"asr_profile": GPU, "worker_memory_budget_mib": 5120}, "prefork", 1),
        ({"asr_profile": GPU, "worker_memory_budget_mib": 5120}, "solo", 4),
        ({"asr_provider": "mock", "worker_memory_budget_mib": 1}, "prefork", 2),
    ],
    ids=["no-budget", "within-budget", "solo-pool", "mock-provider"],
)
def test_ac8_worker_init_keeps_concurrency_that_fits(
    fake_whisper: FakeFasterWhisper,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    overrides: dict[str, Any],
    pool_cls: Any,
    concurrency: int,
) -> None:
    caplog.set_level(logging.INFO)
    _use_settings(monkeypatch, _settings(**overrides))
    sender = SimpleNamespace(concurrency=concurrency, pool_cls=pool_cls, autoscale=None)

    _fire(worker_init, sender)

    assert sender.concurrency == concurrency
    assert _event_records(caplog, "worker_memory_check")
    assert _event_records(caplog, "worker_concurrency_clamped") == []
    assert fake_whisper.constructions == 0


def test_ac8_worker_init_clamps_autoscale_too(
    fake_whisper: FakeFasterWhisper, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_settings(monkeypatch, _settings(asr_profile=GPU, worker_memory_budget_mib=5120))
    sender = SimpleNamespace(concurrency=2, pool_cls="prefork", autoscale=(4, 2))

    _fire(worker_init, sender)

    assert sender.concurrency == 1
    assert sender.autoscale == [1, 1]
    assert fake_whisper.constructions == 0


GPU_5120 = {"asr_profile": GPU, "worker_memory_budget_mib": 5120}
CPU_BASE_2048 = {"asr_profile": "cpu-base", "worker_memory_budget_mib": 2048}


@pytest.mark.parametrize(
    ("overrides", "concurrency", "attribute", "option", "expected", "processes"),
    [
        (GPU_5120, 2, (4, 2), "4,2", [1, 1], 1),
        (GPU_5120, 2, (4, 0), "4,", [1, 0], 1),
        (GPU_5120, 2, (4, 2), (4, 2), [1, 1], 1),
        (GPU_5120, 2, (4, 2), [4, 2], [1, 1], 1),
        (CPU_BASE_2048, 4, (4, 1), "4,1", [2, 1], 2),
    ],
    ids=["option-string", "option-string-empty-min", "option-tuple", "option-list", "min-kept"],
)
def test_ac8_worker_init_clamps_autoscale_in_the_options(
    fake_whisper: FakeFasterWhisper,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
    concurrency: int,
    attribute: Any,
    option: Any,
    expected: list[int],
    processes: int,
) -> None:
    _use_settings(monkeypatch, _settings(**overrides))
    options = {"autoscale": option, "queues": "processing"}
    sender = SimpleNamespace(
        concurrency=concurrency, pool_cls="prefork", autoscale=attribute, options=options
    )

    _fire(worker_init, sender)

    assert sender.concurrency == processes
    assert sender.autoscale == expected
    assert sender.options["autoscale"] == expected
    assert sender.options["queues"] == "processing"
    assert sender.options is options
    assert fake_whisper.constructions == 0


@pytest.mark.parametrize("option", ["4,2", (4, 2)], ids=["option-string", "option-tuple"])
def test_ac8_autoscale_clamp_survives_the_celery_pool_step(
    fake_whisper: FakeFasterWhisper, monkeypatch: pytest.MonkeyPatch, option: Any
) -> None:
    _use_settings(monkeypatch, _settings(**GPU_5120))
    sender = SimpleNamespace(
        concurrency=2,
        pool_cls="prefork",
        optimization=None,
        options={"autoscale": option, "queues": "processing"},
    )

    _fire(worker_init, sender)
    CeleryPoolStep(sender, **sender.options)

    assert sender.concurrency == 1
    assert (sender.max_concurrency, sender.min_concurrency) == (1, 1)
    assert list(sender.autoscale) == [1, 1]
    assert fake_whisper.constructions == 0


def test_ac8_autoscale_options_stay_when_no_clamp_applies(
    fake_whisper: FakeFasterWhisper, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_settings(monkeypatch, _settings(asr_profile=GPU, worker_memory_budget_mib=0))
    sender = SimpleNamespace(
        concurrency=2, pool_cls="prefork", autoscale=(4, 2), options={"autoscale": "4,2"}
    )

    _fire(worker_init, sender)

    assert sender.concurrency == 2
    assert sender.autoscale == (4, 2)
    assert sender.options == {"autoscale": "4,2"}


def test_ac8_receivers_accept_extra_keyword_arguments(
    fake_whisper: FakeFasterWhisper, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_settings(
        monkeypatch, _settings(asr_profile=GPU, worker_memory_budget_mib=0, asr_warm_on_start=False)
    )
    sender = SimpleNamespace(concurrency=2, pool_cls="prefork", autoscale=None)

    _fire(worker_init, sender, hom41_future_argument=True)
    _fire(worker_process_init, None, hom41_future_argument=True)

    assert sender.concurrency == 2
    assert fake_whisper.constructions == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"asr_profile": GPU, "worker_memory_budget_mib": 4096},
        {"whisper_model": "custom-x", "worker_memory_budget_mib": 4096},
    ],
    ids=["budget-too-small", "unknown-model-memory"],
)
def test_ac8_worker_init_stops_a_worker_that_cannot_fit(
    fake_whisper: FakeFasterWhisper,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    overrides: dict[str, Any],
) -> None:
    caplog.set_level(logging.INFO)
    _use_settings(monkeypatch, _settings(**overrides))
    sender = SimpleNamespace(concurrency=2, pool_cls="prefork", autoscale=None)

    with pytest.raises(SystemExit) as stopped:
        _fire(worker_init, sender)

    assert stopped.value.code == 78
    assert _event_records(caplog, "worker_memory_check")
    assert len([record for record in caplog.records if record.levelno >= logging.ERROR]) == 1
    assert fake_whisper.constructions == 0


def test_ac8_worker_process_init_warms_the_model_once(
    fake_whisper: FakeFasterWhisper, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(asr_profile=GPU)
    _use_settings(monkeypatch, settings)

    _fire(worker_process_init, None)
    _fire(worker_process_init, None)
    _transcribe(create_asr_provider(settings), _audio(tmp_path))

    assert fake_whisper.constructions == 1
    assert _counts(_warm())[0] == 1


def test_ac8_worker_process_init_never_raises_and_respects_warm_off(
    fake_whisper: FakeFasterWhisper, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_whisper.load_errors.append(RuntimeError(PRIVATE_DETAIL))
    _use_settings(monkeypatch, _settings())

    _fire(worker_process_init, None)

    assert fake_whisper.constructions == 1
    assert _warm().stats().keys == 0

    _use_settings(monkeypatch, _settings(asr_warm_on_start=False))
    _fire(worker_process_init, None)

    assert fake_whisper.constructions == 1


def test_ac8_child_processes_are_never_recycled() -> None:
    assert celery_module.celery_app.conf.worker_max_tasks_per_child is None


HOM351_P40_LOAD_SECONDS = 14.0


def _record_text(record: logging.LogRecord) -> str:
    return record.getMessage() + " " + repr(vars(record))


def test_amendment2_the_child_start_timeout_covers_the_warm_load() -> None:
    timeout = celery_module.celery_app.conf.worker_proc_alive_timeout

    assert celery_module.WORKER_PROC_ALIVE_TIMEOUT_SECONDS == 120.0
    assert timeout == 120.0
    assert timeout > HOM351_P40_LOAD_SECONDS


def test_amendment2_every_celery_pool_alias_is_counted() -> None:
    capacity = _capacity()
    expected = {
        "prefork": 3,
        "processes": 3,
        "solo": 1,
        "threads": 1,
        "gevent": 1,
        "eventlet": 1,
        "custom": 3,
    }
    checked_classes: list[str] = []

    assert set(CELERY_POOL_ALIASES) == set(expected)
    for alias, target in CELERY_POOL_ALIASES.items():
        assert capacity.worker_processes(alias, 3) == expected[alias], alias
        if target is None:
            continue
        try:
            pool_class = get_implementation(alias)
        except ImportError:
            continue
        assert capacity.worker_processes(pool_class, 3) == expected[alias], alias
        checked_classes.append(alias)
    assert {"prefork", "processes", "solo", "threads"} <= set(checked_classes)


def test_amendment2_the_clamp_is_logged_at_warning(
    fake_whisper: FakeFasterWhisper,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    _use_settings(monkeypatch, _settings(**GPU_5120))
    sender = SimpleNamespace(concurrency=2, pool_cls="prefork", autoscale=None)

    _fire(worker_init, sender)

    clamped = _event_records(caplog, "worker_concurrency_clamped")
    assert sender.concurrency == 1
    assert len(clamped) == 1
    assert clamped[0].levelno == logging.WARNING
    assert _event_records(caplog, "worker_memory_check")


@pytest.mark.parametrize(
    ("overrides", "concurrency", "reason"),
    [
        (GPU_5120, 2, "over_budget"),
        (GPU_5120, 1, "within_budget"),
        ({"asr_profile": GPU, "worker_memory_budget_mib": 0}, 2, "no_budget"),
    ],
    ids=["over-budget", "within-budget", "no-budget"],
)
def test_amendment2_worker_ready_logs_the_stored_decision_at_info(
    fake_whisper: FakeFasterWhisper,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    overrides: dict[str, Any],
    concurrency: int,
    reason: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    _use_settings(monkeypatch, _settings(**overrides))
    _fire(worker_init, SimpleNamespace(concurrency=concurrency, pool_cls="prefork", autoscale=None))
    caplog.clear()

    _fire(worker_ready, SimpleNamespace())

    records = _event_records(caplog, "worker_memory_check")
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert reason in _record_text(records[0])
    assert fake_whisper.constructions == 0


@pytest.mark.parametrize("sender", [SimpleNamespace(), None], ids=["consumer", "none"])
def test_amendment2_worker_ready_logs_nothing_without_a_stored_decision(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, sender: Any
) -> None:
    capacity = _capacity()
    for name, value in list(vars(celery_module).items()):
        if isinstance(value, capacity.CapacityDecision):
            monkeypatch.setattr(celery_module, name, None)
    caplog.set_level(logging.DEBUG)

    _fire(worker_ready, sender)

    assert _event_records(caplog, "worker_memory_check") == []


@pytest.mark.parametrize(
    ("attribute", "option"),
    [((4, 1), None), (None, "4,1"), (None, (4, 1)), (None, [4, 1]), ((4, 1), "4,1")],
    ids=["attribute-only", "option-string", "option-tuple", "option-list", "attribute-and-option"],
)
def test_amendment2_the_budget_check_uses_the_autoscale_maximum(
    fake_whisper: FakeFasterWhisper, monkeypatch: pytest.MonkeyPatch, attribute: Any, option: Any
) -> None:
    _use_settings(monkeypatch, _settings(**GPU_5120))
    values: dict[str, Any] = {"concurrency": 1, "pool_cls": "prefork", "autoscale": attribute}
    if option is not None:
        values["options"] = {"autoscale": option, "queues": "processing"}
    sender = SimpleNamespace(**values)

    _fire(worker_init, sender)

    assert sender.concurrency == 1
    if attribute is not None:
        assert sender.autoscale == [1, 1]
    if option is not None:
        assert sender.options == {"autoscale": [1, 1], "queues": "processing"}
    assert fake_whisper.constructions == 0


def test_amendment2_the_autoscale_maximum_is_clamped_in_a_real_shaped_worker(
    fake_whisper: FakeFasterWhisper, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_settings(monkeypatch, _settings(**GPU_5120))
    sender = SimpleNamespace(
        concurrency=1, pool_cls="prefork", optimization=None, options={"autoscale": (4, 1)}
    )

    _fire(worker_init, sender)
    CeleryPoolStep(sender, **sender.options)

    assert (sender.max_concurrency, sender.min_concurrency) == (1, 1)


@pytest.mark.parametrize(
    ("concurrency", "attribute", "option", "expected_concurrency", "expected_autoscale"),
    [
        (1, (4, 1), "4,1", 1, [2, 1]),
        (1, (2, 1), "2,1", 1, None),
        (4, (2, 1), "2,1", 4, None),
    ],
    ids=["autoscale-over-budget", "autoscale-fits", "autoscale-max-below-concurrency"],
)
def test_amendment2_the_autoscale_maximum_decides_against_the_budget(
    fake_whisper: FakeFasterWhisper,
    monkeypatch: pytest.MonkeyPatch,
    concurrency: int,
    attribute: Any,
    option: Any,
    expected_concurrency: int,
    expected_autoscale: list[int] | None,
) -> None:
    _use_settings(monkeypatch, _settings(**CPU_BASE_2048))
    sender = SimpleNamespace(
        concurrency=concurrency,
        pool_cls="prefork",
        autoscale=attribute,
        options={"autoscale": option},
    )

    _fire(worker_init, sender)

    assert sender.concurrency == expected_concurrency
    if expected_autoscale is None:
        assert sender.autoscale == attribute
        assert sender.options == {"autoscale": option}
    else:
        assert sender.autoscale == expected_autoscale
        assert sender.options == {"autoscale": expected_autoscale}


def test_ac9_new_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NEW_ENV:
        monkeypatch.delenv(name, raising=False)

    settings = _bare_settings()

    assert settings.asr_warm_on_start is True
    assert settings.asr_batch_size == 1
    assert settings.worker_memory_budget_mib == 0
    assert settings.asr_model_memory_mib == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"asr_batch_size": 0},
        {"asr_batch_size": 17},
        {"asr_batch_size": -1},
        {"worker_memory_budget_mib": -1},
        {"asr_model_memory_mib": -1},
    ],
    ids=["batch-0", "batch-17", "batch-negative", "budget-negative", "model-memory-negative"],
)
def test_ac9_out_of_range_settings_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _settings(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"asr_batch_size": 1},
        {"asr_batch_size": 16},
        {"worker_memory_budget_mib": 0},
        {"worker_memory_budget_mib": 5120},
        {"asr_model_memory_mib": 0},
        {"asr_model_memory_mib": 1000},
    ],
    ids=["batch-1", "batch-16", "budget-0", "budget-5120", "model-memory-0", "model-memory-1000"],
)
def test_ac9_in_range_settings_are_accepted(overrides: dict[str, Any]) -> None:
    settings = _settings(**overrides)

    assert {name: getattr(settings, name) for name in overrides} == overrides


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ASR_BATCH_SIZE", "0"),
        ("ASR_BATCH_SIZE", "17"),
        ("WORKER_MEMORY_BUDGET_MIB", "-1"),
        ("ASR_MODEL_MEMORY_MIB", "-1"),
    ],
    ids=["env-batch-0", "env-batch-17", "env-budget-negative", "env-model-memory-negative"],
)
def test_ac9_out_of_range_environment_values_are_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        _bare_settings()


def test_ac9_environment_values_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASR_BATCH_SIZE", "16")
    monkeypatch.setenv("WORKER_MEMORY_BUDGET_MIB", "5120")
    monkeypatch.setenv("ASR_MODEL_MEMORY_MIB", "1000")
    monkeypatch.setenv("ASR_WARM_ON_START", "false")

    settings = _bare_settings()

    assert settings.asr_batch_size == 16
    assert settings.worker_memory_budget_mib == 5120
    assert settings.asr_model_memory_mib == 1000
    assert settings.asr_warm_on_start is False


@pytest.mark.parametrize("value", [0, 17, -1], ids=["zero", "seventeen", "negative"])
def test_ac10_provider_rejects_a_batch_size_out_of_range(value: int) -> None:
    with pytest.raises(ValueError):
        _provider(batch_size=value)


@pytest.mark.parametrize("value", [1, 16], ids=["one", "sixteen"])
def test_ac10_provider_accepts_a_batch_size_in_range(value: int) -> None:
    provider = _provider(batch_size=value)

    assert provider.batch_size == value
    with pytest.raises(AttributeError):
        provider.batch_size = 2


@pytest.mark.parametrize(
    "overrides", [{}, {"asr_profile": "cpu-small"}], ids=["explicit-model", "profile"]
)
def test_ac10_the_factory_passes_the_batch_size(overrides: dict[str, Any]) -> None:
    default = create_asr_provider(_settings(**overrides))
    batched = create_asr_provider(_settings(asr_batch_size=4, **overrides))

    assert default.batch_size == 1
    assert default.decode_identity() is None
    assert batched.batch_size == 4
    assert batched.decode_identity() == {"batch_size": 4}
    assert batched.identity() == default.identity()


def test_ac10_batch_size_one_keeps_the_plain_path(
    fake_whisper: FakeFasterWhisper, tmp_path: Path
) -> None:
    provider = _provider()

    result = _transcribe(provider, _audio(tmp_path), "fa")

    assert result.text == "text-plain-first-fa"
    assert fake_whisper.calls_on("plain") == [
        {"beam_size": 5, "vad_filter": True, "language": "fa"}
    ]
    assert fake_whisper.pipelines == []
    assert provider.batch_size == 1
    assert provider.decode_identity() is None
    assert provider.identity().as_manifest() == {
        "provider": "local_whisper",
        "model": "base",
        "params": {"compute_type": "int8"},
    }


def test_ac10_batched_jobs_share_one_pipeline_on_the_warm_model(
    fake_whisper: FakeFasterWhisper, tmp_path: Path
) -> None:
    audio = _audio(tmp_path)
    providers = [_provider(batch_size=4) for _ in range(3)]

    results = [_transcribe(provider, audio, "fa") for provider in providers]

    assert [result.text for result in results] == ["text-batched-first-fa"] * 3
    assert fake_whisper.constructions == 1
    assert len(fake_whisper.pipelines) == 1
    assert fake_whisper.pipelines[0].model is fake_whisper.models[0]
    assert (
        fake_whisper.calls_on("batched")
        == [{"beam_size": 5, "vad_filter": True, "language": "fa", "batch_size": 4}] * 3
    )
    assert fake_whisper.calls_on("plain") == []
    assert providers[0].identity() == _provider().identity()
    assert providers[0].decode_identity() == {"batch_size": 4}


def test_ac10_batched_and_plain_providers_share_the_model(
    fake_whisper: FakeFasterWhisper, tmp_path: Path
) -> None:
    audio = _audio(tmp_path)

    plain = _transcribe(_provider(), audio, "fa")
    batched = _transcribe(_provider(batch_size=4), audio, "fa")

    assert (plain.text, batched.text) == ("text-plain-first-fa", "text-batched-first-fa")
    assert fake_whisper.constructions == 1
    assert len(fake_whisper.pipelines) == 1


def test_ac10_a_batched_provider_without_vad_uses_the_plain_model(
    fake_whisper: FakeFasterWhisper, tmp_path: Path
) -> None:
    result = _transcribe(_provider(batch_size=4), _audio(tmp_path), "fa", vad_filter=False)

    assert result.text == "text-plain-first-fa"
    assert fake_whisper.calls_on("plain") == [
        {"beam_size": 5, "vad_filter": False, "language": "fa"}
    ]
    assert fake_whisper.calls_on("batched") == []


@pytest.mark.parametrize(
    ("detected", "decided"), [("en", "en"), ("ar", "fa")], ids=["kept", "overridden"]
)
def test_ac10_language_detection_uses_the_plain_model(
    fake_whisper: FakeFasterWhisper, tmp_path: Path, detected: str, decided: str
) -> None:
    fake_whisper.detected = detected
    audio = _audio(tmp_path)
    provider = _provider(batch_size=4)

    result = _transcribe(provider, audio, None)

    assert result.language == decided
    plain = fake_whisper.calls_on("plain")
    batched = fake_whisper.calls_on("batched")
    assert {"beam_size": 5, "vad_filter": True} in plain
    assert all("batch_size" not in kwargs for kwargs in plain)
    assert all("language" in kwargs for kwargs in batched)
    assert all(kwargs.get("batch_size") == 4 for kwargs in batched)

    fake_whisper.calls.clear()
    decision = provider.decide_language(audio, None)

    assert decision.language == decided
    assert fake_whisper.calls_on("plain") == [{"beam_size": 5, "vad_filter": True}]
    assert fake_whisper.calls_on("batched") == []


def test_ac10_the_vad_rescue_runs_on_the_batched_path(
    fake_whisper: FakeFasterWhisper, tmp_path: Path
) -> None:
    fake_whisper.first_kept = 0.0
    fake_whisper.rescue_kept = 3.0

    result = _transcribe(_provider(batch_size=4), _audio(tmp_path), "en")

    assert fake_whisper.calls_on("batched") == [
        {"beam_size": 5, "vad_filter": True, "language": "en", "batch_size": 4},
        {
            "beam_size": 5,
            "vad_filter": True,
            "language": "en",
            "vad_parameters": RESCUE_VAD_PARAMETERS,
            "batch_size": 4,
        },
    ]
    assert fake_whisper.calls_on("plain") == []
    assert result.text == "text-batched-rescue-en"


def test_ac10_no_speech_after_the_batched_rescue_stays_empty(
    fake_whisper: FakeFasterWhisper, tmp_path: Path
) -> None:
    fake_whisper.first_kept = 0.0
    fake_whisper.rescue_kept = 0.0

    result = _transcribe(_provider(batch_size=4), _audio(tmp_path), "en")

    assert len(fake_whisper.calls_on("batched")) == 2
    assert result.text == ""
    assert result.segments == []


def test_ac10_the_vad_rescue_on_the_plain_path_is_unchanged(
    fake_whisper: FakeFasterWhisper, tmp_path: Path
) -> None:
    fake_whisper.first_kept = 0.0
    fake_whisper.rescue_kept = 3.0

    result = _transcribe(_provider(), _audio(tmp_path), "en")

    assert fake_whisper.calls_on("plain") == [
        {"beam_size": 5, "vad_filter": True, "language": "en"},
        {
            "beam_size": 5,
            "vad_filter": True,
            "language": "en",
            "vad_parameters": RESCUE_VAD_PARAMETERS,
        },
    ]
    assert result.text == "text-plain-rescue-en"


class _DecodeASR(ASRProvider):
    def __init__(self, decode: Callable[[], Any] | None) -> None:
        self._inner = MockASRProvider(allow_mock=True, transcript="پیگیری قرارداد انجام شود")
        self._decode = decode

    def identity(self):
        return self._inner.identity()

    def decode_identity(self) -> Any:
        return self._decode() if self._decode is not None else None

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        return await self._inner.transcribe(request)


class _NoHookASR:
    def __init__(self) -> None:
        self._inner = MockASRProvider(allow_mock=True, transcript="پیگیری قرارداد انجام شود")

    def identity(self):
        return self._inner.identity()

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        return await self._inner.transcribe(request)


def _asr_requests(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    original = ckpt.stage_identity

    def spy(context: Any, stage: Any, *args: Any, **kwargs: Any) -> Any:
        if stage is StageName.ASR:
            captured.append(dict(kwargs["request"]))
        return original(context, stage, *args, **kwargs)

    monkeypatch.setattr(ckpt, "stage_identity", spy)
    return captured


def _run_job(client, email_outbox, settings, monkeypatch, asr: Any) -> list[dict[str, Any]]:
    job_id, _ = _queued_upload(client, email_outbox, monkeypatch)
    captured = _asr_requests(monkeypatch)
    providers = ProviderBundle(
        asr=asr,
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=MockIntelligenceProvider(allow_mock=True),
    )
    result = process_job(job_id, worker_id="hom41-oracle", settings=settings, providers=providers)
    assert result["status"] in {"completed", "partial"}
    return captured


@pytest.mark.parametrize(
    ("batch_size", "expected"),
    [
        (1, BASE_ASR_REQUEST),
        (4, {**BASE_ASR_REQUEST, "decode": {"batch_size": 4}}),
    ],
    ids=["batch-1-equals-base", "batch-4-adds-decode"],
)
def test_ac11_checkpoint_identity_follows_the_whisper_batch_size(
    client, email_outbox, settings, monkeypatch, batch_size: int, expected: dict[str, Any]
) -> None:
    whisper = _provider(model=_InjectedModel(FakeFasterWhisper()), batch_size=batch_size)

    captured = _run_job(
        client, email_outbox, settings, monkeypatch, _DecodeASR(whisper.decode_identity)
    )

    assert captured == [expected]


def test_ac11_checkpoint_identity_takes_any_declared_decode_dict(
    client, email_outbox, settings, monkeypatch
) -> None:
    captured = _run_job(
        client, email_outbox, settings, monkeypatch, _DecodeASR(lambda: {"batch_size": 8})
    )

    assert captured == [{**BASE_ASR_REQUEST, "decode": {"batch_size": 8}}]


def test_ac11_checkpoint_identity_without_the_hook_is_the_base(
    client, email_outbox, settings, monkeypatch
) -> None:
    captured = _run_job(client, email_outbox, settings, monkeypatch, _NoHookASR())

    assert captured == [BASE_ASR_REQUEST]


def test_ac12_the_cpu_path_works_with_the_default_settings(
    fake_whisper: FakeFasterWhisper, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ASR_ENV:
        monkeypatch.delenv(name, raising=False)

    result = _transcribe(create_asr_provider(_bare_settings()), _audio(tmp_path), "fa")

    assert result.text == "text-plain-first-fa"
    assert fake_whisper.model_args == [("base", {"device": "cpu", "compute_type": "int8"})]
    assert fake_whisper.calls_on("batched") == []
