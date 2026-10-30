"""Supported private deployment topologies (HOM-106, ADR 0047).

A topology names one host shape the product supports, with its minimums.
``DEPLOYMENT_TOPOLOGY`` selects one, and the ``topology`` host check
(``host_checks.py``) compares it with the machine and the settings. Empty
means no topology and no check. Shapes that are not supported yet live only
in the ADR, never here.

The CPU and memory minimums are host totals, and every number is planned,
not measured (ADR 0047, D2). The ADR table must match these values; a test
fails when they drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class DeploymentTopology:
    name: str
    status: str  # "reference" or "supported"
    deployment_mode: str  # "onprem" or "cloud"
    min_cpu_count: int  # host CPUs
    min_memory_mib: int  # host physical memory
    gpu_count: int  # minimum visible NVIDIA GPUs; 0 = no GPU query
    gpu_compute_capabilities: tuple[str, ...]  # empty = any
    min_free_gpu_mib: int  # for --min-free-gpu-mib; 0 without a GPU
    allowed_asr_profiles: tuple[str, ...]
    notes: str = ""


_TOPOLOGIES = (
    DeploymentTopology(
        name="single-host-p40",
        status="reference",
        deployment_mode="onprem",
        min_cpu_count=16,
        min_memory_mib=24576,
        gpu_count=1,
        gpu_compute_capabilities=("6.1",),
        min_free_gpu_mib=4096,
        allowed_asr_profiles=("gpu-p40-large-v3",),
        notes="The app server shape: one Linux host, Docker Compose, Tesla P40 GPUs.",
    ),
    DeploymentTopology(
        name="single-host-cpu",
        status="supported",
        deployment_mode="onprem",
        min_cpu_count=16,
        min_memory_mib=24576,
        gpu_count=0,
        gpu_compute_capabilities=(),
        min_free_gpu_mib=0,
        allowed_asr_profiles=(
            "cpu-base",
            "cpu-small",
            "cpu-medium",
            "cpu-large-v3-turbo",
            "cpu-large-v3",
        ),
        notes="One Linux host, Docker Compose, no GPU: a low-throughput profile.",
    ),
)

TOPOLOGIES: Mapping[str, DeploymentTopology] = MappingProxyType(
    {topology.name: topology for topology in _TOPOLOGIES}
)


def get_topology(name: str) -> DeploymentTopology:
    topology = TOPOLOGIES.get(name)
    if topology is None:
        raise ValueError(
            f"Unknown deployment topology {name!r}; valid topologies: {', '.join(TOPOLOGIES)}"
        )
    return topology
