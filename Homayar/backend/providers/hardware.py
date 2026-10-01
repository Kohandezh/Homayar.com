"""Host hardware profile and model qualification records (local AI runtime).

Pure data plus best-effort detection with the standard library only:
CPU cores (``os.cpu_count``), physical memory (``os.sysconf`` where the
platform offers it) and NVIDIA GPUs through an optional ``nvidia-smi`` call
(argv, timeout, no shell).  Anything that cannot be detected is ``None`` or
empty with a note — never a guessed value.

A ``QualificationRecord`` states how a model manifest performs on a profile.
``planned`` records carry no measurements; ``validated`` records must carry
real measurements (sample count, when, and RTF or latency).  Nothing here
measures: benchmarks produce the numbers, this module only holds them.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from collections.abc import Callable
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

HARDWARE_SCHEMA = "homayar.hardware-profile/1"
QUALIFICATION_SCHEMA = "homayar.model-qualification/1"
NVIDIA_SMI_TIMEOUT_SECONDS = 5.0
_NVIDIA_QUERY = [
    "nvidia-smi",
    "--query-gpu=name,memory.total,driver_version",
    "--format=csv,noheader,nounits",
]


class _Data(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GpuInfo(_Data):
    vendor: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    memory_mib: int | None = Field(default=None, ge=0)
    driver_version: str | None = Field(default=None, max_length=80)


class HostProfile(_Data):
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,79}$")
    os: str = Field(min_length=1, max_length=40)
    arch: str = Field(min_length=1, max_length=40)
    cpu_logical_cores: int | None = Field(default=None, ge=1)
    ram_bytes: int | None = Field(default=None, ge=0)
    gpus: list[GpuInfo] = Field(default_factory=list, max_length=64)
    # What could not be detected and why (for the operator, not a failure).
    detection_notes: list[str] = Field(default_factory=list, max_length=16)

    @property
    def digest(self) -> str:
        """Identity of the hardware (notes excluded)."""
        data = self.model_dump(mode="json", exclude={"detection_notes", "profile_id"})
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_json(self) -> str:
        return json.dumps(
            {"schema": HARDWARE_SCHEMA, **self.model_dump(mode="json")},
            sort_keys=True,
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, text: str) -> HostProfile:
        data = json.loads(text)
        if data.pop("schema", None) != HARDWARE_SCHEMA:
            raise ValueError(f"expected schema {HARDWARE_SCHEMA}")
        return cls.model_validate(data)


Runner = Callable[..., subprocess.CompletedProcess]


def _ram_bytes() -> int | None:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        return None
    if pages <= 0 or size <= 0:
        return None
    return int(pages) * int(size)


def parse_nvidia_smi(output: str) -> list[GpuInfo]:
    gpus: list[GpuInfo] = []
    for line in output.splitlines():
        fields = [part.strip() for part in line.split(",")]
        if len(fields) < 3 or not fields[0]:
            continue
        memory = int(fields[1]) if fields[1].isdigit() else None
        gpus.append(
            GpuInfo(
                vendor="nvidia",
                name=fields[0][:200],
                memory_mib=memory,
                driver_version=fields[2][:80] or None,
            )
        )
    return gpus


def detect_nvidia_gpus(run: Runner = subprocess.run) -> tuple[list[GpuInfo], str | None]:
    """(GPUs, note).  A missing tool, timeout or error yields no GPUs and a note."""
    try:
        completed = run(
            _NVIDIA_QUERY,
            capture_output=True,
            text=True,
            timeout=NVIDIA_SMI_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return [], "nvidia-smi not installed: NVIDIA GPUs not detected"
    except subprocess.TimeoutExpired:
        return [], "nvidia-smi timed out: GPUs not detected"
    except OSError:
        return [], "nvidia-smi could not run: GPUs not detected"
    if completed.returncode != 0:
        return [], "nvidia-smi reported an error: GPUs not detected"
    return parse_nvidia_smi((completed.stdout or "")[:65536]), None


def detect_host_profile(
    profile_id: str = "detected", *, run: Runner = subprocess.run, probe_gpus: bool = True
) -> HostProfile:
    notes: list[str] = []
    ram = _ram_bytes()
    if ram is None:
        notes.append("physical memory not reported by this platform")
    gpus: list[GpuInfo] = []
    if probe_gpus:
        gpus, note = detect_nvidia_gpus(run)
        if note:
            notes.append(note)
    return HostProfile(
        profile_id=profile_id,
        os=(platform.system() or "unknown").lower(),
        arch=(platform.machine() or "unknown").lower(),
        cpu_logical_cores=os.cpu_count(),
        ram_bytes=ram,
        gpus=gpus,
        detection_notes=notes,
    )


class QualificationStatus(str, Enum):
    PLANNED = "planned"
    VALIDATED = "validated"


class QualificationMeasurements(_Data):
    # Processing time / audio duration (speech stages); < 1 is faster than real time.
    realtime_factor: float | None = Field(default=None, gt=0)
    latency_ms_p50: float | None = Field(default=None, ge=0)
    latency_ms_p95: float | None = Field(default=None, ge=0)
    sample_count: int = Field(ge=1)
    measured_at: datetime
    dataset: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _has_a_number(self) -> QualificationMeasurements:
        if self.realtime_factor is None and self.latency_ms_p50 is None:
            raise ValueError("a measurement records a realtime factor or a latency")
        return self


class QualificationRecord(_Data):
    manifest_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,79}$")
    manifest_version: int = Field(ge=1)
    stage: str = Field(pattern=r"^(asr|diarization|intelligence|tts)$")
    profile: HostProfile
    status: QualificationStatus
    measurements: QualificationMeasurements | None = None
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _status_matches_evidence(self) -> QualificationRecord:
        if self.status is QualificationStatus.VALIDATED and self.measurements is None:
            raise ValueError("a validated qualification carries its measurements")
        if self.status is QualificationStatus.PLANNED and self.measurements is not None:
            raise ValueError("a planned qualification has no measurements yet")
        return self

    def to_json(self) -> str:
        return json.dumps(
            {"schema": QUALIFICATION_SCHEMA, **self.model_dump(mode="json")},
            sort_keys=True,
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, text: str) -> QualificationRecord:
        data = json.loads(text)
        if data.pop("schema", None) != QUALIFICATION_SCHEMA:
            raise ValueError(f"expected schema {QUALIFICATION_SCHEMA}")
        return cls.model_validate(data)
