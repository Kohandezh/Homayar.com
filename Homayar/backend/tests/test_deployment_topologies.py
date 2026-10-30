"""HOM-106: supported private topologies (ADR 0047) and the ``topology`` host check.

Every side effect goes through injected ``HostProbes`` fakes: no test reads
the real CPU count, memory or ``nvidia-smi``, except the two tests that call
the default probes and only check that they never raise.
"""

from __future__ import annotations

import dataclasses
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from backend.config import Settings
from backend.jobs import model_preflight
from backend.jobs.model_preflight import run_preflight
from backend.providers import host_checks
from backend.providers.asr_profiles import ASR_PROFILES
from backend.providers.deployment_topologies import (
    TOPOLOGIES,
    DeploymentTopology,
    get_topology,
)
from backend.providers.diarization import UnavailableDiarizationProvider
from backend.providers.host_checks import CHECK_NAMES, HostOptions, HostProbes, run_host_checks
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.whisper import LocalWhisperProvider

REPO_ROOT = Path(__file__).resolve().parents[3]
ADR = REPO_ROOT / "docs/architecture/decisions/0047-private-topologies-capacity-identity-storage.md"
COMPOSE = REPO_ROOT / "infra/compose/compose.onprem.yaml"

CPU = next(t for t in TOPOLOGIES.values() if t.gpu_count == 0)
P40 = next(t for t in TOPOLOGIES.values() if t.status == "reference")


def _smi_line(index: int, cap: str = "6.1", free: int = 20000) -> str:
    used = 24576 - free
    return f"{index}, GPU-{index:08d}-0000, Tesla P40, 24576, {used}, {free}, 535.183.01, {cap}"


def _smi(*caps: str) -> str:
    return "".join(_smi_line(index, cap) + "\n" for index, cap in enumerate(caps))


def probes(
    *,
    cpus: int | None = 36,
    memory: int | None = 27_648,
    smi: str | Exception = _smi("6.1", "6.1"),
    returncode: int = 0,
    calls: list | None = None,
) -> HostProbes:
    def run(argv, **kwargs):
        if calls is not None:
            calls.append(argv)
        if isinstance(smi, Exception):
            raise smi
        return subprocess.CompletedProcess(argv, returncode, stdout=smi, stderr="")

    return HostProbes(
        run=run,
        connect=lambda host, port, timeout: None,
        disk_usage=lambda path: SimpleNamespace(free=50_000 * 1024 * 1024),
        find_spec=lambda name: object(),
        ctranslate2=lambda: None,
        environ={"HF_HUB_OFFLINE": "1"},
        cpu_count=lambda: cpus,
        memory_total_mib=lambda: memory,
    )


def _settings(**update) -> Settings:
    base = Settings(
        environment="test",
        deployment_mode="onprem",
        database_url="sqlite:///./topology.db",
        redis_url="redis://localhost:6379/0",
        storage_backend="local",
        storage_local_root="/srv/objects",
        _env_file=None,
    )
    return base.model_copy(update=update)


def _for(topology: DeploymentTopology, **update) -> Settings:
    values = {
        "deployment_topology": topology.name,
        "asr_profile": topology.allowed_asr_profiles[0],
    }
    values.update(update)
    return _settings(**values)


def _mock_bundle():
    return SimpleNamespace(asr=MockASRProvider(allow_mock=True))


def _topology(settings=None, options=None, bundle=None, **probe_kwargs) -> dict:
    result = run_host_checks(
        settings or _settings(),
        bundle or _mock_bundle(),
        options or HostOptions(),
        probes(**probe_kwargs),
    )
    check = result["checks"][-1]
    assert check["name"] == "topology"
    check["_ok"] = result["ok"]
    return check


# --- the catalog ------------------------------------------------------------------------


def test_the_catalog_is_small_named_and_frozen() -> None:
    assert 1 <= len(TOPOLOGIES) <= 3
    for name, topology in TOPOLOGIES.items():
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", name)
        assert topology.name == name
        assert topology.status in {"reference", "supported"}
        assert topology.deployment_mode in {"onprem", "cloud"}
        assert topology.min_cpu_count > 0 and topology.min_memory_mib > 0
        with pytest.raises(dataclasses.FrozenInstanceError):
            topology.name = "x"  # type: ignore[misc]
    with pytest.raises(TypeError):
        TOPOLOGIES["x"] = CPU  # type: ignore[index]
    assert [t.status for t in TOPOLOGIES.values()].count("reference") == 1


def test_one_cpu_topology_and_the_p40_reference() -> None:
    assert CPU.gpu_count == 0 and CPU.status == "supported"
    assert P40.gpu_count >= 1 and "6.1" in P40.gpu_compute_capabilities
    assert P40.min_free_gpu_mib > 0 and CPU.min_free_gpu_mib == 0


def test_allowed_asr_profiles_exist_and_match_the_device() -> None:
    for topology in TOPOLOGIES.values():
        assert topology.allowed_asr_profiles
        for name in topology.allowed_asr_profiles:
            device = ASR_PROFILES[name].device
            assert (device == "cuda") is (topology.gpu_count > 0), (topology.name, name)


def test_get_topology_names_every_allowed_name() -> None:
    assert get_topology(P40.name) is P40
    with pytest.raises(ValueError) as raised:
        get_topology("nope")
    for name in TOPOLOGIES:
        assert name in str(raised.value)


@pytest.mark.parametrize("name", ["", "   "])
def test_get_topology_refuses_an_empty_name(name) -> None:
    with pytest.raises(ValueError):
        get_topology(name)


def _adr_rows() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in ADR.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and (match := re.fullmatch(r"`([a-z0-9-]+)`", cells[0])) and len(cells) == 9:
            if match.group(1) in TOPOLOGIES:
                rows[match.group(1)] = cells
    return rows


@pytest.mark.skipif(not ADR.is_file(), reason="the ADR is not in this checkout (api image)")
def test_the_adr_catalog_table_matches_the_code() -> None:
    rows = _adr_rows()
    assert set(rows) == set(TOPOLOGIES)
    for name, cells in rows.items():
        topology = TOPOLOGIES[name]
        expected = [
            f"`{name}`",
            topology.status,
            f"`{topology.deployment_mode}`",
            str(topology.min_cpu_count),
            str(topology.min_memory_mib),
            str(topology.gpu_count),
            ", ".join(topology.gpu_compute_capabilities) or "any",
            str(topology.min_free_gpu_mib),
            ", ".join(f"`{profile}`" for profile in topology.allowed_asr_profiles),
        ]
        assert cells == expected


# --- the setting ------------------------------------------------------------------------


def test_the_setting_is_empty_by_default_and_changes_nothing() -> None:
    plain = Settings(_env_file=None)
    assert plain.deployment_topology == ""
    explicit = Settings(deployment_topology="", _env_file=None)
    assert explicit.model_dump() == plain.model_dump()


def test_a_known_name_loads_and_is_stripped() -> None:
    assert Settings(deployment_topology=f"  {CPU.name} ", _env_file=None).deployment_topology == (
        CPU.name
    )


@pytest.mark.parametrize("value", ["nope", CPU.name.upper()])
def test_an_unknown_name_stops_the_settings(value) -> None:
    with pytest.raises(ValidationError) as raised:
        Settings(deployment_topology=value, _env_file=None)
    for name in TOPOLOGIES:
        assert name in str(raised.value)


def test_the_environment_variable_is_read(monkeypatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_TOPOLOGY", P40.name)
    assert Settings(_env_file=None).deployment_topology == P40.name


def test_empty_compose_values_load_the_settings_as_before(monkeypatch) -> None:
    before = Settings(_env_file=None).model_dump()
    monkeypatch.setenv("DEPLOYMENT_TOPOLOGY", "")
    monkeypatch.setenv("ASR_PROFILE", "")
    assert Settings(_env_file=None).model_dump() == before


def test_compose_passes_the_topology_through_the_shared_app_environment() -> None:
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert document["x-app-environment"]["DEPLOYMENT_TOPOLOGY"] == "${DEPLOYMENT_TOPOLOGY:-}"
    for name in ("api", "worker", "scheduler"):
        environment = document["services"][name]["environment"]
        assert environment["DEPLOYMENT_TOPOLOGY"] == "${DEPLOYMENT_TOPOLOGY:-}", name


# --- the host check: shape and skip ------------------------------------------------------


def test_topology_is_the_last_check() -> None:
    assert CHECK_NAMES[-1] == "topology"
    result = run_host_checks(_settings(), _mock_bundle(), HostOptions(), probes())
    assert [check["name"] for check in result["checks"]] == list(CHECK_NAMES)
    json.dumps(result)


def test_no_topology_skips_and_probes_nothing() -> None:
    calls: list = []

    def boom():
        raise AssertionError("probed")

    result = run_host_checks(
        _settings(),
        _mock_bundle(),
        HostOptions(),
        dataclasses.replace(probes(calls=calls), cpu_count=boom, memory_total_mib=boom),
    )
    check = result["checks"][-1]
    assert check["status"] == "skip" and check["detail"]
    assert calls == []


def test_the_reference_host_passes_its_topology() -> None:
    check = _topology(_for(P40))
    assert check["status"] == "pass", check
    assert check["topology"] == P40.name and check["topology_status"] == "reference"
    assert check["_ok"] is True


def test_the_compose_defaults_on_the_reference_shape_pass() -> None:
    compose_defaults = _for(
        P40,
        environment="production",
        storage_backend="s3",
        storage_endpoint_url="http://minio:9000",
        tombstone_journal_dir="/app/tombstones",
        enable_google_auth=False,
        enable_phone_auth=False,
    )
    check = _topology(compose_defaults, cpus=36, memory=27_000, smi=_smi("6.1", "6.1"))
    assert check["status"] == "pass", check


def test_the_cpu_topology_passes_and_never_queries_gpus() -> None:
    calls: list = []
    check = _topology(_for(CPU), calls=calls)
    assert check["status"] == "pass", check
    assert calls == []


# --- deny cases and their allow controls ---------------------------------------------------


@pytest.mark.parametrize("topology", [CPU, P40], ids=lambda t: t.name)
def test_cpu_count_minimum_is_inclusive(topology) -> None:
    assert _topology(_for(topology), cpus=topology.min_cpu_count)["status"] == "pass"
    check = _topology(_for(topology), cpus=topology.min_cpu_count - 1)
    assert check["status"] == "fail" and "CPU" in check["detail"]
    assert check["_ok"] is False


@pytest.mark.parametrize("topology", [CPU, P40], ids=lambda t: t.name)
def test_memory_minimum_is_inclusive(topology) -> None:
    assert _topology(_for(topology), memory=topology.min_memory_mib)["status"] == "pass"
    check = _topology(_for(topology), memory=topology.min_memory_mib - 1)
    assert check["status"] == "fail" and "memory" in check["detail"]


def test_a_gpu_topology_without_a_gpu_fails() -> None:
    assert _topology(_for(P40), smi=_smi("6.1"))["status"] == "pass"
    check = _topology(_for(P40), smi="")
    assert check["status"] == "fail" and "GPU" in check["detail"]


def test_too_few_gpus_reports_the_real_count(monkeypatch) -> None:
    two_gpus = dataclasses.replace(P40, name="two-p40", gpu_count=2)
    monkeypatch.setattr(
        host_checks, "get_topology", lambda name: two_gpus if name == "two-p40" else P40
    )
    options = HostOptions(topology="two-p40")
    check = _topology(_for(P40), options, smi=_smi("6.1"))
    assert check["status"] == "fail" and "GPU" in check["detail"]
    assert check["gpu_count"] == 1
    check = _topology(_for(P40), options, smi=_smi("6.1", "6.1"))
    assert check["status"] == "pass" and check["gpu_count"] == 2


def test_a_gpu_topology_with_the_wrong_compute_capability_fails() -> None:
    check = _topology(_for(P40), smi=_smi("8.6"))
    assert check["status"] == "fail" and "compute capability" in check["detail"]
    assert _topology(_for(P40), smi=_smi("8.6", "6.1"))["status"] == "pass"


def test_an_asr_profile_outside_the_list_fails() -> None:
    check = _topology(_for(P40, asr_profile="cpu-base"))
    assert check["status"] == "fail" and "ASR profile" in check["detail"]
    check = _topology(_for(CPU, asr_profile="gpu-p40-large-v3"))
    assert check["status"] == "fail" and "ASR profile" in check["detail"]


def test_a_deployment_mode_mismatch_fails() -> None:
    check = _topology(_for(CPU, deployment_mode="cloud"))
    assert check["status"] == "fail" and "deployment mode" in check["detail"]


def test_every_failed_requirement_is_named() -> None:
    check = _topology(
        _for(P40, asr_profile="cpu-base", deployment_mode="cloud"),
        cpus=1,
        memory=1,
        smi=_smi("8.6"),
    )
    assert check["status"] == "fail"
    for word in ("CPU", "memory", "compute capability", "ASR profile", "deployment mode"):
        assert word in check["detail"], word


# --- values that cannot be read --------------------------------------------------------


@pytest.mark.parametrize("probe, word", [("cpus", "CPU"), ("memory", "memory")])
def test_an_unreadable_value_warns(probe, word) -> None:
    check = _topology(_for(CPU), **{probe: None})
    assert check["status"] == "warn" and word in check["detail"]


@pytest.mark.parametrize(
    "smi, returncode",
    [(FileNotFoundError(), 0), (subprocess.TimeoutExpired("nvidia-smi", 5), 0), ("", 9)],
)
def test_nvidia_smi_that_cannot_run_warns(smi, returncode) -> None:
    check = _topology(_for(P40), smi=smi, returncode=returncode)
    assert check["status"] == "warn" and "GPU" in check["detail"]


def test_an_unreadable_compute_capability_warns() -> None:
    check = _topology(_for(P40), smi=_smi("[N/A]"))
    assert check["status"] == "warn" and "compute capability" in check["detail"]


def test_a_failure_wins_over_an_unreadable_value() -> None:
    check = _topology(_for(CPU), cpus=None, memory=1)
    assert check["status"] == "fail" and "memory" in check["detail"]


def test_an_empty_asr_profile_warns() -> None:
    check = _topology(_for(CPU, asr_profile=""))
    assert check["status"] == "warn" and "ASR profile" in check["detail"]


def test_probes_that_raise_are_unreadable_values() -> None:
    def boom():
        raise OSError("no /proc")

    result = run_host_checks(
        _for(CPU),
        _mock_bundle(),
        HostOptions(),
        dataclasses.replace(probes(), cpu_count=boom, memory_total_mib=boom),
    )
    assert result["checks"][-1]["status"] == "warn"


def test_the_default_probes_return_a_number_or_none() -> None:
    defaults = HostProbes()
    for probe in (defaults.cpu_count, defaults.memory_total_mib):
        value = probe()
        assert value is None or (isinstance(value, int) and value > 0)


def test_the_memory_probe_returns_none_when_sysconf_fails(monkeypatch) -> None:
    def broken(name):
        raise ValueError(name)

    monkeypatch.setattr(host_checks.os, "sysconf", broken)
    assert host_checks._memory_total_mib() is None


# --- identity and storage expectations --------------------------------------------------


@pytest.mark.parametrize("setting", ["enable_google_auth", "enable_phone_auth"])
def test_an_internet_sign_in_method_warns(setting) -> None:
    check = _topology(_for(CPU, **{setting: True}))
    assert check["status"] == "warn" and "sign-in" in check["detail"]


def test_production_storage_expectations_warn() -> None:
    production = {"environment": "production"}
    check = _topology(_for(CPU, **production, storage_backend="local", tombstone_journal_dir=""))
    assert check["status"] == "warn"
    assert "STORAGE_BACKEND" in check["detail"] and "TOMBSTONE_JOURNAL_DIR" in check["detail"]
    passing = _for(CPU, **production, storage_backend="s3", tombstone_journal_dir="/app/t")
    assert _topology(passing)["status"] == "pass"


# --- the option and the CLI -------------------------------------------------------------


def test_the_option_wins_over_the_setting() -> None:
    check = _topology(_for(CPU), HostOptions(topology=P40.name), smi="")
    assert check["topology"] == P40.name and check["status"] == "fail"
    assert _topology(_for(CPU), HostOptions(topology=f" {CPU.name} "))["status"] == "pass"


def test_an_unknown_option_fails_the_check_without_raising() -> None:
    check = _topology(_settings(), HostOptions(topology="nope"))
    assert check["status"] == "fail"
    for name in TOPOLOGIES:
        assert name in check["detail"]


def test_nvidia_smi_runs_once_when_the_runtime_and_the_topology_need_it() -> None:
    calls: list = []
    bundle = SimpleNamespace(
        asr=LocalWhisperProvider(model_name="large-v3", device="cuda", compute_type="int8_float32")
    )
    _topology(_for(P40), bundle=bundle, calls=calls)
    assert len(calls) == 1


def _preflight_bundle():
    return SimpleNamespace(
        asr=MockASRProvider(allow_mock=True),
        diarization=UnavailableDiarizationProvider(),
        intelligence=MockIntelligenceProvider(allow_mock=True),
    )


def test_a_topology_failure_makes_preflight_not_ready() -> None:
    code, report = run_preflight(
        _for(CPU), providers=_preflight_bundle(), host=HostOptions(), probes=probes()
    )
    assert code == 0, report
    code, report = run_preflight(
        _for(CPU), providers=_preflight_bundle(), host=HostOptions(), probes=probes(cpus=1)
    )
    assert code == 1 and report["host"]["checks"][-1]["status"] == "fail"


@pytest.mark.parametrize("argv", [["--topology", CPU.name], ["--host", "--topology", " "]])
def test_cli_rejects_topology_misuse(argv, monkeypatch) -> None:
    monkeypatch.setattr(model_preflight, "run_preflight", lambda *a, **k: pytest.fail("ran"))
    with pytest.raises(SystemExit) as raised:
        model_preflight.main(argv)
    assert raised.value.code == 2


def test_cli_passes_the_topology(monkeypatch, capsys) -> None:
    seen: dict = {}

    def fake(settings, **kwargs):
        seen.update(kwargs)
        return 0, {"ready": True}

    monkeypatch.setattr(model_preflight, "get_settings", lambda: _settings())
    monkeypatch.setattr(model_preflight, "run_preflight", fake)
    assert model_preflight.main(["--host", "--topology", P40.name]) == 0
    assert seen["host"] == HostOptions(topology=P40.name)
    assert model_preflight.main(["--host"]) == 0
    assert seen["host"].topology is None
