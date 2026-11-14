"""HOM-44: the local pyannote speaker-diarization adapter.

The adapter loads the pyannote community-1 pipeline only from local files,
after every required file passes its sha256 pin. It refuses (UNAVAILABLE,
never an exception) when the artifact root is unset, a file is unpinned or a
pinned file is missing, changed or a symlink. No network, no token.

All tests are offline: the pipeline and the loader are injected fakes. The
one opt-in test that runs the real library is marked ``pyannote_local``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import socket
import struct
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.config import Settings
from backend.providers import factory as factory_module
from backend.providers.artifacts import MISMATCH, MISSING, UNSAFE
from backend.providers.base import ProviderError, ProviderIdentity
from backend.providers.diarization import UnavailableDiarizationProvider
from backend.providers.factory import create_diarization_provider
from backend.providers.pyannote_diarization import (
    MANIFEST_ID,
    MANIFEST_VERSION,
    MODEL_DIR,
    MODEL_ID,
    PROVIDER_NAME,
    REQUIRED_FILES,
    PyannoteDiarizationProvider,
)
from backend.providers.registry import ManifestStatus, ModelArtifact, load_registry
from backend.providers.schemas import DiarizationRequest, DiarizationStatus


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "local_ai_base_url": "http://intelligence.test/v1",
        "llm_model": "conversation-model",
        "allow_mock_providers": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _turn(start: float, end: float) -> SimpleNamespace:
    return SimpleNamespace(start=start, end=end)


class _FakePipeline:
    """Records every call and answers with a fixed annotation."""

    def __init__(self, annotation: Any, *, legacy: bool = False) -> None:
        self.annotation = annotation
        self.legacy = legacy
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def __call__(self, files: dict[str, Any], **kwargs: Any) -> Any:
        self.calls.append((dict(files), dict(kwargs)))
        if self.legacy:
            return self.annotation
        return SimpleNamespace(speaker_diarization=self.annotation)


class _FakeFactory:
    def __init__(self, pipeline: Any | None = None, error: Exception | None = None) -> None:
        self.pipeline = pipeline
        self.error = error
        self.calls: list[str] = []

    def __call__(self, model_dir: str) -> Any:
        self.calls.append(model_dir)
        if self.error is not None:
            raise self.error
        assert self.pipeline is not None
        return self.pipeline


def _artifact(name: str, content: bytes) -> ModelArtifact:
    return ModelArtifact(path=f"{MODEL_DIR}/{name}", sha256=hashlib.sha256(content).hexdigest())


def _write_model_files(root: Path, contents: dict[str, bytes]) -> None:
    for name, content in contents.items():
        target = root / MODEL_DIR / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _full_contents() -> dict[str, bytes]:
    return {
        "config.yaml": b"pipeline: community-1",
        "segmentation/pytorch_model.bin": b"segmentation-weights",
        "embedding/pytorch_model.bin": b"embedding-weights",
        "plda/plda.npz": b"plda-data",
        "plda/xvec_transform.npz": b"xvec-data",
    }


def _full_pins(contents: dict[str, bytes] | None = None) -> list[ModelArtifact]:
    return [_artifact(name, content) for name, content in (contents or _full_contents()).items()]


def _diarize(provider: PyannoteDiarizationProvider, path: str = "/recordings/call.wav") -> Any:
    return asyncio.run(provider.diarize(DiarizationRequest(audio_path=path)))


def _library_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make pyannote and torch deterministically unimportable, installed or not."""
    for module in ("pyannote", "pyannote.audio", "torch"):
        monkeypatch.setitem(sys.modules, module, None)


# --- module surface ----------------------------------------------------------------------


def test_constants_name_the_community_1_model() -> None:
    assert PROVIDER_NAME == "pyannote_local"
    assert MODEL_ID == "pyannote/speaker-diarization-community-1"
    assert (MANIFEST_ID, MANIFEST_VERSION) == ("diarization.pyannote.community-1", 1)
    assert MODEL_DIR == "diarization/pyannote-community-1"
    assert REQUIRED_FILES == (
        "config.yaml",
        "segmentation/pytorch_model.bin",
        "embedding/pytorch_model.bin",
        "plda/plda.npz",
        "plda/xvec_transform.npz",
    )


def test_module_and_factory_import_without_pyannote_or_torch() -> None:
    # A fresh interpreter, so the assertion does not depend on what this test
    # process imported (or on what is installed on the machine).
    code = (
        "import sys; "
        "import backend.providers.pyannote_diarization; "
        "from backend.config import Settings; "
        "from backend.providers.factory import create_diarization_provider; "
        "settings = Settings("
        "_env_file=None, environment='test', diarization_provider='pyannote_local'); "
        "provider = create_diarization_provider(settings); "
        "assert type(provider).__name__ == 'PyannoteDiarizationProvider'; "
        "assert 'torch' not in sys.modules; "
        "assert not any(name.split('.')[0] == 'pyannote' for name in sys.modules)"
    )
    subprocess.run(  # noqa: S603 - fixed argv, this repo's own interpreter
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parents[2]),
        check=True,
    )


def test_identity_names_the_provider_and_model_only() -> None:
    provider = PyannoteDiarizationProvider(artifact_root="/models", artifacts=[])
    assert provider.identity() == ProviderIdentity(provider="pyannote_local", model=MODEL_ID)
    assert dict(provider.identity().params) == {}


def test_construction_rejects_pipeline_and_factory_together() -> None:
    with pytest.raises(ValueError):
        PyannoteDiarizationProvider(
            artifact_root="/models",
            artifacts=[],
            pipeline=_FakePipeline([]),
            pipeline_factory=_FakeFactory(),
        )


# --- the load gate (SPEC 3.2 and section 4) ----------------------------------------------


def test_no_pins_at_all_refuses_unpinned(tmp_path: Path) -> None:
    contents = _full_contents()
    _write_model_files(tmp_path, contents)
    factory = _FakeFactory(_FakePipeline([]))
    provider = PyannoteDiarizationProvider(
        artifact_root=str(tmp_path), artifacts=[], pipeline_factory=factory
    )

    result = _diarize(provider)

    assert result.provider == "pyannote_local"
    assert result.status == DiarizationStatus.UNAVAILABLE
    assert result.issue is not None
    assert result.issue.code == "diarization_model_unpinned"
    assert result.issue.retryable is False
    assert result.segments == []
    for name in REQUIRED_FILES:
        assert f"{MODEL_DIR}/{name}" in result.issue.message
    assert factory.calls == []


def test_four_of_five_pinned_refuses_unpinned(tmp_path: Path) -> None:
    contents = _full_contents()
    _write_model_files(tmp_path, contents)
    pins = [artifact for artifact in _full_pins(contents) if not artifact.path.endswith("plda.npz")]
    factory = _FakeFactory(_FakePipeline([]))
    provider = PyannoteDiarizationProvider(
        artifact_root=str(tmp_path), artifacts=pins, pipeline_factory=factory
    )

    result = _diarize(provider)

    assert result.issue is not None
    assert result.issue.code == "diarization_model_unpinned"
    assert f"{MODEL_DIR}/plda/plda.npz" in result.issue.message
    assert f"{MODEL_DIR}/config.yaml" not in result.issue.message
    assert factory.calls == []


def test_pinned_file_absent_refuses_integrity_missing(tmp_path: Path) -> None:
    contents = _full_contents()
    del contents["plda/plda.npz"]
    _write_model_files(tmp_path, contents)
    factory = _FakeFactory(_FakePipeline([]))
    provider = PyannoteDiarizationProvider(
        artifact_root=str(tmp_path), artifacts=_full_pins(), pipeline_factory=factory
    )

    result = _diarize(provider)

    assert result.issue is not None
    assert result.issue.code == "diarization_model_integrity"
    assert f"{MODEL_DIR}/plda/plda.npz" in result.issue.message
    assert MISSING in result.issue.message
    assert factory.calls == []


def test_pinned_file_bytes_differ_refuses_integrity_mismatch(tmp_path: Path) -> None:
    contents = _full_contents()
    _write_model_files(tmp_path, contents)
    (tmp_path / MODEL_DIR / "embedding/pytorch_model.bin").write_bytes(b"tampered")
    factory = _FakeFactory(_FakePipeline([]))
    provider = PyannoteDiarizationProvider(
        artifact_root=str(tmp_path), artifacts=_full_pins(contents), pipeline_factory=factory
    )

    result = _diarize(provider)

    assert result.issue is not None
    assert result.issue.code == "diarization_model_integrity"
    assert f"{MODEL_DIR}/embedding/pytorch_model.bin" in result.issue.message
    assert MISMATCH in result.issue.message
    assert factory.calls == []


def test_pinned_symlink_refuses_integrity_unsafe(tmp_path: Path) -> None:
    contents = _full_contents()
    del contents["config.yaml"]
    _write_model_files(tmp_path, contents)
    outside = tmp_path / "outside.yaml"
    outside.write_bytes(b"outside")
    (tmp_path / MODEL_DIR / "config.yaml").symlink_to(outside)
    factory = _FakeFactory(_FakePipeline([]))
    provider = PyannoteDiarizationProvider(
        artifact_root=str(tmp_path), artifacts=_full_pins(), pipeline_factory=factory
    )

    result = _diarize(provider)

    assert result.issue is not None
    assert result.issue.code == "diarization_model_integrity"
    assert f"{MODEL_DIR}/config.yaml" in result.issue.message
    assert UNSAFE in result.issue.message
    assert factory.calls == []


def test_empty_artifact_root_refuses_unset(tmp_path: Path) -> None:
    contents = _full_contents()
    _write_model_files(tmp_path, contents)
    factory = _FakeFactory(_FakePipeline([]))
    provider = PyannoteDiarizationProvider(
        artifact_root="", artifacts=_full_pins(contents), pipeline_factory=factory
    )

    result = _diarize(provider)

    assert result.issue is not None
    assert result.issue.code == "diarization_artifact_root_unset"
    assert result.issue.retryable is False
    assert factory.calls == []


def test_all_pinned_loads_once_from_the_absolute_dir(tmp_path: Path) -> None:
    contents = _full_contents()
    _write_model_files(tmp_path, contents)
    pipeline = _FakePipeline([(_turn(0.0, 2.0), "SPEAKER_00")])
    factory = _FakeFactory(pipeline)
    provider = PyannoteDiarizationProvider(
        artifact_root=str(tmp_path), artifacts=_full_pins(contents), pipeline_factory=factory
    )

    first = _diarize(provider, "/calls/a.wav")
    second = _diarize(provider, "/calls/a.wav")

    assert factory.calls == [str(Path(str(tmp_path)).resolve() / MODEL_DIR)]
    assert first.status == DiarizationStatus.AVAILABLE
    assert first.is_mock is False
    assert first.provider == "pyannote_local"
    assert [segment.speaker_label for segment in first.segments] == ["speaker_1"]
    assert second.status == DiarizationStatus.AVAILABLE
    assert len(pipeline.calls) == 2  # the factory load is not repeated


def test_refusal_is_not_cached_and_the_factory_stays_uncalled(tmp_path: Path) -> None:
    factory = _FakeFactory(_FakePipeline([]))
    provider = PyannoteDiarizationProvider(
        artifact_root=str(tmp_path), artifacts=[], pipeline_factory=factory
    )

    first = _diarize(provider)
    second = _diarize(provider)

    assert first.issue is not None and second.issue is not None
    assert first.issue.code == second.issue.code == "diarization_model_unpinned"
    assert factory.calls == []


def test_injected_pipeline_skips_the_gate() -> None:
    pipeline = _FakePipeline([(_turn(0.0, 1.0), "SPEAKER_00")])
    provider = PyannoteDiarizationProvider(artifact_root="", artifacts=[], pipeline=pipeline)

    result = _diarize(provider)

    assert result.status == DiarizationStatus.AVAILABLE
    assert [segment.speaker_label for segment in result.segments] == ["speaker_1"]


# --- the default loader (SPEC 3.3) -------------------------------------------------------


def test_missing_library_refuses_runtime_missing_and_disables_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contents = _full_contents()
    _write_model_files(tmp_path, contents)
    _library_absent(monkeypatch)
    monkeypatch.setenv("PYANNOTE_METRICS_ENABLED", "1")
    provider = PyannoteDiarizationProvider(
        artifact_root=str(tmp_path), artifacts=_full_pins(contents)
    )

    result = _diarize(provider)

    assert result.status == DiarizationStatus.UNAVAILABLE
    assert result.issue is not None
    assert result.issue.code == "diarization_runtime_missing"
    assert result.issue.retryable is False
    assert os.environ["PYANNOTE_METRICS_ENABLED"] == "0"


def test_loader_import_error_refuses_runtime_missing(tmp_path: Path) -> None:
    contents = _full_contents()
    _write_model_files(tmp_path, contents)
    provider = PyannoteDiarizationProvider(
        artifact_root=str(tmp_path),
        artifacts=_full_pins(contents),
        pipeline_factory=_FakeFactory(error=ImportError("no pyannote")),
    )

    result = _diarize(provider)

    assert result.status == DiarizationStatus.UNAVAILABLE
    assert result.issue is not None
    assert result.issue.code == "diarization_runtime_missing"


def test_loader_failure_raises_a_sanitized_provider_error(tmp_path: Path) -> None:
    contents = _full_contents()
    _write_model_files(tmp_path, contents)
    provider = PyannoteDiarizationProvider(
        artifact_root=str(tmp_path),
        artifacts=_full_pins(contents),
        pipeline_factory=_FakeFactory(error=RuntimeError("secret weights detail")),
    )

    with pytest.raises(ProviderError) as error:
        _diarize(provider)

    assert error.value.retryable is False
    assert "secret weights detail" not in error.value.public_message


# --- diarize (SPEC 3.4) ------------------------------------------------------------------


def test_labels_map_to_anonymous_speakers_and_overlap_is_kept() -> None:
    annotation = [
        (_turn(0.0, 3.0), "SPEAKER_01"),
        (_turn(2.5, 4.0), "SPEAKER_00"),
        (_turn(4.0, 6.0), "SPEAKER_01"),
    ]
    provider = PyannoteDiarizationProvider(
        artifact_root="/models", artifacts=[], pipeline=_FakePipeline(annotation)
    )

    result = _diarize(provider)

    assert result.status == DiarizationStatus.AVAILABLE
    assert result.is_mock is False
    # SPEAKER_01 speaks first, so it is speaker_1; SPEAKER_00 is speaker_2.
    assert [(s.speaker_label, s.start_seconds, s.end_seconds) for s in result.segments] == [
        ("speaker_1", 0.0, 3.0),
        ("speaker_2", 2.5, 4.0),
        ("speaker_1", 4.0, 6.0),
    ]
    assert all("SPEAKER_" not in s.speaker_label for s in result.segments)


def test_label_ties_break_by_label_string() -> None:
    annotation = [(_turn(1.0, 2.0), "B"), (_turn(1.0, 2.5), "A")]
    provider = PyannoteDiarizationProvider(
        artifact_root="/models", artifacts=[], pipeline=_FakePipeline(annotation)
    )

    result = _diarize(provider)

    assert [(s.speaker_label, s.start_seconds, s.end_seconds) for s in result.segments] == [
        ("speaker_2", 1.0, 2.0),  # B: same first start, higher label string
        ("speaker_1", 1.0, 2.5),  # A ends later, so it sorts after B
    ]


def test_times_are_rounded_clamped_and_empty_segments_skipped() -> None:
    annotation = [
        (_turn(-0.02, 1.0), "SPEAKER_00"),
        (_turn(2.0001, 2.0001), "SPEAKER_00"),
        (_turn(3.0, 2.9999), "SPEAKER_00"),
        (_turn(4.0, 5.123456), "SPEAKER_00"),
    ]
    provider = PyannoteDiarizationProvider(
        artifact_root="/models", artifacts=[], pipeline=_FakePipeline(annotation)
    )

    result = _diarize(provider)

    assert [(s.start_seconds, s.end_seconds) for s in result.segments] == [
        (0.0, 1.0),
        (4.0, 5.123),
    ]
    assert all(s.confidence is None for s in result.segments)


def test_pipeline_call_shape_with_and_without_a_speaker_count() -> None:
    pipeline = _FakePipeline([])
    provider = PyannoteDiarizationProvider(artifact_root="/models", artifacts=[], pipeline=pipeline)

    _diarize(provider, "/calls/mono.wav")
    asyncio.run(
        provider.diarize(DiarizationRequest(audio_path="/calls/mono.wav", expected_speaker_count=2))
    )

    assert pipeline.calls[0] == ({"audio": "/calls/mono.wav", "uri": "recording"}, {})
    assert pipeline.calls[1] == (
        {"audio": "/calls/mono.wav", "uri": "recording"},
        {"max_speakers": 2},
    )


def test_silence_is_available_with_no_segments() -> None:
    provider = PyannoteDiarizationProvider(
        artifact_root="/models", artifacts=[], pipeline=_FakePipeline([])
    )

    result = _diarize(provider)

    assert result.status == DiarizationStatus.AVAILABLE
    assert result.segments == []


def test_legacy_output_without_speaker_diarization_is_the_annotation() -> None:
    annotation = [(_turn(0.0, 1.5), "SPEAKER_00")]
    provider = PyannoteDiarizationProvider(
        artifact_root="/models", artifacts=[], pipeline=_FakePipeline(annotation, legacy=True)
    )

    result = _diarize(provider)

    assert result.status == DiarizationStatus.AVAILABLE
    assert [segment.end_seconds for segment in result.segments] == [1.5]


def test_pipeline_failure_raises_a_sanitized_provider_error() -> None:
    class _Boom:
        def __call__(self, files: dict[str, Any], **kwargs: Any) -> Any:
            raise RuntimeError("secret model internals")

    provider = PyannoteDiarizationProvider(artifact_root="/models", artifacts=[], pipeline=_Boom())

    with pytest.raises(ProviderError) as error:
        _diarize(provider)

    assert error.value.retryable is False
    assert "secret model internals" not in error.value.public_message


# --- factory, settings and the builtin manifest (SPEC 3.5, 3.6) --------------------------


def test_factory_builds_the_pyannote_provider_for_every_name(tmp_path: Path) -> None:
    for name in ("pyannote_local", "pyannote", "pyannote_community_1"):
        provider = create_diarization_provider(
            _settings(diarization_provider=name, model_artifact_root=str(tmp_path))
        )
        assert isinstance(provider, PyannoteDiarizationProvider)
        assert provider.identity().provider == "pyannote_local"


def test_factory_maps_a_broken_manifest_file_to_a_provider_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
    settings = _settings(
        diarization_provider="pyannote_local",
        model_artifact_root=str(tmp_path),
        model_manifest_paths=str(broken),
    )

    with pytest.raises(ProviderError) as error:
        create_diarization_provider(settings)

    assert error.value.code == "manifest_unreadable"
    assert error.value.retryable is False


def test_a_broken_manifest_file_leaves_the_unavailable_provider_alone(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
    provider = create_diarization_provider(
        _settings(diarization_provider="unavailable", model_manifest_paths=str(broken))
    )

    assert isinstance(provider, UnavailableDiarizationProvider)


def test_factory_default_provider_stays_unavailable() -> None:
    assert Settings(_env_file=None, environment="test").diarization_provider == "unavailable"
    assert isinstance(create_diarization_provider(_settings()), UnavailableDiarizationProvider)


def test_settings_carry_a_cpu_diarization_device_default() -> None:
    assert Settings(_env_file=None, environment="test").diarization_device == "cpu"


# SPEC amendment 1, table A2: the real pins the owner's server files carry.
_A2_PINS = (
    (
        "diarization/pyannote-community-1/config.yaml",
        "5ce2bfa9a938dc132cec1172592d65173cbb8f444ea1e4133f10f9391de155be",
    ),
    (
        "diarization/pyannote-community-1/segmentation/pytorch_model.bin",
        "7ad24338d844fb95985486eb1a464e32d229f6d7a03c9abe60f978bacf3f816e",
    ),
    (
        "diarization/pyannote-community-1/embedding/pytorch_model.bin",
        "6f10ff60898a1d185fa22e1d11e0bfa8a92efec811f11bca48cb8cafebefd929",
    ),
    (
        "diarization/pyannote-community-1/plda/plda.npz",
        "9b77bcd840692710dd3496f62ecfeed8d8e5f002fd991b785079b244eab7d255",
    ),
    (
        "diarization/pyannote-community-1/plda/xvec_transform.npz",
        "325f1ce8e48f7e55e9c8aa47e05d2766b7c48c4b25b8de8dd751e7a4cc5fbe8f",
    ),
)


def test_builtin_manifest_pins_the_five_community_files() -> None:
    registry = load_registry(_settings())
    manifest = registry.get(MANIFEST_ID, MANIFEST_VERSION)
    assert manifest is not None
    assert manifest.provider == "pyannote_local"
    assert manifest.model == MODEL_ID
    assert manifest.status is ManifestStatus.DEVELOPMENT
    assert manifest.model_revision == "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee"
    assert [(a.path, a.sha256) for a in manifest.artifacts] == [
        (path, sha) for path, sha in _A2_PINS
    ]
    assert all(artifact.kind == "file" for artifact in manifest.artifacts)


def test_factory_with_builtin_pins_and_absent_files_refuses_integrity_missing(
    tmp_path: Path,
) -> None:
    provider = create_diarization_provider(
        _settings(diarization_provider="pyannote_local", model_artifact_root=str(tmp_path))
    )

    result = _diarize(provider)

    assert result.status == DiarizationStatus.UNAVAILABLE
    assert result.issue is not None
    assert result.issue.code == "diarization_model_integrity"
    assert MISSING in result.issue.message


def test_factory_with_builtin_pins_and_other_bytes_refuses_integrity_mismatch(
    tmp_path: Path,
) -> None:
    for name in REQUIRED_FILES:
        target = tmp_path / MODEL_DIR / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"other bytes entirely " + name.encode())
    provider = create_diarization_provider(
        _settings(diarization_provider="pyannote_local", model_artifact_root=str(tmp_path))
    )

    result = _diarize(provider)

    assert result.status == DiarizationStatus.UNAVAILABLE
    assert result.issue is not None
    assert result.issue.code == "diarization_model_integrity"
    assert MISMATCH in result.issue.message


def test_factory_passes_manifest_pins_to_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contents = _full_contents()
    _write_model_files(tmp_path, contents)
    _library_absent(monkeypatch)
    registry = SimpleNamespace(
        get=lambda manifest_id, version: SimpleNamespace(artifacts=_full_pins(contents))
    )
    monkeypatch.setattr(factory_module, "load_registry", lambda settings: registry)
    provider = create_diarization_provider(
        _settings(diarization_provider="pyannote_local", model_artifact_root=str(tmp_path))
    )

    result = _diarize(provider)

    # The gate passed (the pins arrived), so the refusal is the missing library,
    # not diarization_model_unpinned.
    assert result.issue is not None
    assert result.issue.code == "diarization_runtime_missing"


# --- the opt-in real-library test (SPEC 3.7) ---------------------------------------------


def _two_tone_wav(path: Path, rate: int = 16000) -> None:
    """A short mono WAV: two different synthetic tones, one per speaker slot."""
    frames: list[int] = []
    amplitude = int(0.4 * 32767)
    for index in range(rate * 4):
        frequency = 440 if index < rate * 2 else 880
        sample = int(amplitude * math.sin(2 * math.pi * frequency * index / rate))
        frames.append(sample)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(struct.pack("<h", sample) for sample in frames))


@pytest.mark.pyannote_local
def test_real_pipeline_loads_and_diarizes_a_local_wav(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact_root = os.environ.get("MODEL_ARTIFACT_ROOT", "").strip()
    if not artifact_root:
        pytest.skip("NOT_RUN/OPT_IN: MODEL_ARTIFACT_ROOT does not hold the pinned model files")

    def _no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the pyannote load tried to reach the network")

    monkeypatch.setattr(socket.socket, "connect", _no_network)
    settings = _settings(diarization_provider="pyannote_local", model_artifact_root=artifact_root)
    provider = create_diarization_provider(settings)

    wav = tmp_path / "two-tones.wav"
    _two_tone_wav(wav)
    result = asyncio.run(provider.diarize(DiarizationRequest(audio_path=str(wav))))

    assert result.provider == "pyannote_local"
    assert result.status == DiarizationStatus.AVAILABLE
    assert result.is_mock is False
    assert all(segment.speaker_label.startswith("speaker_") for segment in result.segments)
