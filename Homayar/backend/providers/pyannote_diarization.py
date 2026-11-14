"""Local pyannote speaker-diarization adapter (HOM-44, features F30/F31/F33).

Loads the ``pyannote/speaker-diarization-community-1`` pipeline only from
local files under the deployment's artifact root. Every required file must
carry a sha256 pin in the manifest and match it byte for byte before the
pipeline is loaded; a missing or wrong pin is a refusal (UNAVAILABLE), never
an exception and never a download. No Hugging Face token is used anywhere.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from backend.providers.artifacts import verify_artifacts
from backend.providers.base import DiarizationProvider, ProviderError, ProviderIdentity
from backend.providers.registry import ModelArtifact
from backend.providers.schemas import (
    DiarizationRequest,
    DiarizationResult,
    DiarizationSegment,
    DiarizationStatus,
    ProviderIssue,
)

PROVIDER_NAME = "pyannote_local"
MODEL_ID = "pyannote/speaker-diarization-community-1"
MANIFEST_ID = "diarization.pyannote.community-1"
MANIFEST_VERSION = 1
MODEL_DIR = "diarization/pyannote-community-1"
REQUIRED_FILES = (
    "config.yaml",
    "segmentation/pytorch_model.bin",
    "embedding/pytorch_model.bin",
    "plda/plda.npz",
    "plda/xvec_transform.npz",
)


def _refuse(code: str, message: str) -> DiarizationResult:
    return DiarizationResult(
        provider=PROVIDER_NAME,
        status=DiarizationStatus.UNAVAILABLE,
        issue=ProviderIssue(code=code, message=message, retryable=False),
    )


def _load_pyannote_pipeline(model_dir: str, *, device: str = "cpu") -> Any:
    """The default loader: local files only, telemetry off, no token.

    The imports live here so the module (and the factory that builds the
    provider) load without pyannote and without torch installed.
    """
    os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
    from pyannote.audio import Pipeline

    try:
        from pyannote.audio.telemetry import set_telemetry_metrics
    except ImportError:
        set_telemetry_metrics = None
    if callable(set_telemetry_metrics):
        set_telemetry_metrics(False)
    pipeline = Pipeline.from_pretrained(model_dir)
    import torch

    return pipeline.to(torch.device(device))


def _segments_from_annotation(annotation: Any) -> DiarizationResult:
    """Map a pyannote Annotation to anonymous, time-sorted segments.

    Overlapping speech is kept: two speakers may own the same span (F31).
    """
    kept: list[tuple[float, float, str]] = []
    for turn, speaker in annotation:
        start = max(0.0, round(float(turn.start), 3))
        end = round(float(turn.end), 3)
        if end <= start:
            continue
        kept.append((start, end, str(speaker)))
    first_start: dict[str, float] = {}
    for start, _end, label in kept:
        if label not in first_start or start < first_start[label]:
            first_start[label] = start
    ordered = sorted(first_start, key=lambda label: (first_start[label], label))
    mapping = {label: f"speaker_{index}" for index, label in enumerate(ordered, start=1)}
    segments = [
        DiarizationSegment(speaker_label=mapping[label], start_seconds=start, end_seconds=end)
        for start, end, label in kept
    ]
    segments.sort(
        key=lambda segment: (segment.start_seconds, segment.end_seconds, segment.speaker_label)
    )
    return DiarizationResult(
        provider=PROVIDER_NAME,
        status=DiarizationStatus.AVAILABLE,
        segments=segments,
        is_mock=False,
    )


class PyannoteDiarizationProvider(DiarizationProvider):
    """Diarization from a pinned local pyannote community-1 pipeline."""

    def __init__(
        self,
        *,
        artifact_root: str,
        artifacts: Sequence[ModelArtifact],
        device: str = "cpu",
        pipeline: Any | None = None,
        pipeline_factory: Callable[[str], Any] | None = None,
    ) -> None:
        if pipeline is not None and pipeline_factory is not None:
            raise ValueError("Provide either a loaded pipeline or a pipeline factory, not both")
        self._artifact_root = artifact_root
        self._artifacts = tuple(artifacts)
        self._device = device
        self._pipeline = pipeline
        self._pipeline_factory: Callable[[str], Any] = (
            pipeline_factory
            if pipeline_factory is not None
            else partial(_load_pyannote_pipeline, device=device)
        )
        self._lock = threading.Lock()

    def identity(self) -> ProviderIdentity:
        # The device changes where the pipeline runs, not what it is.
        return ProviderIdentity(provider=PROVIDER_NAME, model=MODEL_ID)

    def _load(self) -> Any:
        """The load gate, then the factory call. Returns the pipeline, or an
        UNAVAILABLE refusal. A refusal is never cached: the next call runs the
        gate again, so pins added to a rebuilt provider work without code."""
        if not self._artifact_root.strip():
            return _refuse(
                "diarization_artifact_root_unset",
                "The diarization model artifact root is not configured",
            )
        required = [f"{MODEL_DIR}/{name}" for name in REQUIRED_FILES]
        pinned = {
            artifact.path: artifact
            for artifact in self._artifacts
            if artifact.kind == "file" and artifact.path in set(required)
        }
        unpinned = [path for path in required if path not in pinned]
        if unpinned:
            return _refuse(
                "diarization_model_unpinned",
                "The diarization model files have no sha256 pin: " + ", ".join(unpinned),
            )
        manifest = SimpleNamespace(
            manifest_id=MANIFEST_ID,
            version=MANIFEST_VERSION,
            artifacts=[pinned[path] for path in required],
        )
        report = verify_artifacts(manifest, self._artifact_root)
        failed = [
            f"{check.path} ({check.status})" for check in report.checks if check.status != "ok"
        ]
        if failed:
            return _refuse(
                "diarization_model_integrity",
                "The diarization model files failed verification: " + ", ".join(failed),
            )
        model_dir = str(Path(self._artifact_root).resolve() / MODEL_DIR)
        try:
            return self._pipeline_factory(model_dir)
        except ImportError:
            return _refuse(
                "diarization_runtime_missing",
                "pyannote.audio or one of its dependencies could not be imported",
            )
        except Exception as exc:
            raise ProviderError(
                "diarization_model_load_failed",
                "The local diarization model failed to load",
                retryable=False,
            ) from exc

    def _get_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        with self._lock:
            if self._pipeline is None:
                loaded = self._load()
                if isinstance(loaded, DiarizationResult):
                    return loaded
                self._pipeline = loaded
            return self._pipeline

    def _diarize_sync(self, request: DiarizationRequest) -> DiarizationResult:
        pipeline = self._get_pipeline()
        if isinstance(pipeline, DiarizationResult):
            return pipeline
        call: dict[str, Any] = {"audio": request.audio_path, "uri": "recording"}
        try:
            if request.expected_speaker_count is not None:
                output = pipeline(call, max_speakers=request.expected_speaker_count)
            else:
                output = pipeline(call)
        except Exception as exc:
            raise ProviderError(
                "diarization_failed",
                "Local speaker diarization failed",
                retryable=False,
            ) from exc
        # pyannote 4 returns a PipelineOutput; a legacy call returns the
        # Annotation itself. Overlap stays: speaker_diarization, never the
        # exclusive variant (F31).
        annotation = getattr(output, "speaker_diarization", output)
        return _segments_from_annotation(annotation)

    async def diarize(self, request: DiarizationRequest) -> DiarizationResult:
        return await asyncio.to_thread(self._diarize_sync, request)
