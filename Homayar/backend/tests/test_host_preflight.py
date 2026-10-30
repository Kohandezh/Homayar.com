"""HOM-108: host preflight (driver, VRAM, disk, ports, TLS, local runtime, no internet).

Every side effect goes through injected ``HostProbes`` fakes: no test needs
``nvidia-smi``, ``faster_whisper``, ``ctranslate2`` or the network.  Test
certificates and keys are generated here; none is committed.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from backend.config import Settings
from backend.jobs import model_preflight
from backend.jobs.model_preflight import run_preflight
from backend.providers.diarization import UnavailableDiarizationProvider
from backend.providers.factory import ProviderBundle, create_asr_provider
from backend.providers.host_checks import (
    CHECK_NAMES,
    DEFAULT_PROBE_TARGETS,
    HostOptions,
    HostProbes,
    run_host_checks,
)
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.whisper import LocalWhisperProvider

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
P40_UUID_0 = "GPU-1a2b3c4d-0000-1111-2222-333344445555"
P40_UUID_1 = "GPU-9f8e7d6c-aaaa-bbbb-cccc-ddddeeeeffff"
GIB = 1024 * 1024 * 1024
MIB = 1024 * 1024


def _smi_line(index, uuid, free, *, total=24576, driver="535.183.01", cap="6.1") -> str:
    return f"{index}, {uuid}, Tesla P40, {total}, {total - free}, {free}, {driver}, {cap}"


def _smi(*lines: str) -> str:
    return "\n".join(lines) + "\n"


TWO_P40 = _smi(_smi_line(0, P40_UUID_0, 2000), _smi_line(1, P40_UUID_1, 20000))


class FakeCT2:
    def __init__(self, *, count: int = 2, cuda=None, cpu=None) -> None:
        self.count = count
        self.cuda = set(cuda or {"float32", "int8", "int8_float32"})
        self.cpu = set(cpu or {"float32", "int8", "int8_float32"})

    def get_cuda_device_count(self) -> int:
        return self.count

    def get_supported_compute_types(self, device: str, device_index: int = 0) -> set[str]:
        return self.cuda if device == "cuda" else self.cpu


class Net:
    """A fake ``connect``: refuses the listed hosts, records every call."""

    def __init__(self, refuse=(), error: type[Exception] = ConnectionRefusedError) -> None:
        self.refuse = set(refuse)
        self.error = error
        self.calls: list[tuple[str, int, float]] = []

    def __call__(self, host: str, port: int, timeout: float) -> None:
        self.calls.append((host, port, timeout))
        if host in self.refuse or "*" in self.refuse:
            raise self.error("refused")


def _usage(free_mib: int):
    return SimpleNamespace(total=100 * GIB, used=100 * GIB - free_mib * MIB, free=free_mib * MIB)


def probes(
    *,
    smi: str | Exception | None = TWO_P40,
    returncode: int = 0,
    ct2: object | None = None,
    spec: bool = True,
    environ: dict | None = None,
    net: Net | None = None,
    disk=None,
    stdin: bytes = b"",
    temp: str = "/scratch",
    calls: list | None = None,
) -> HostProbes:
    def run(argv, **kwargs):
        if calls is not None:
            calls.append((argv, kwargs))
        if isinstance(smi, Exception):
            raise smi
        return subprocess.CompletedProcess(argv, returncode, stdout=smi or "", stderr="")

    return HostProbes(
        run=run,
        connect=net or Net(),
        disk_usage=disk or (lambda path: _usage(50_000)),
        find_spec=lambda name: object() if spec else None,
        ctranslate2=lambda: FakeCT2() if ct2 is None else (ct2 or None),
        environ=environ if environ is not None else {"HF_HUB_OFFLINE": "1"},
        now=lambda: NOW,
        read_stdin=lambda: stdin,
        temp_dir=lambda: temp,
    )


def _settings(**update) -> Settings:
    base = Settings(
        environment="test",
        database_url="sqlite:///./host-preflight.db",
        redis_url="redis://localhost:6379/0",
        storage_backend="local",
        storage_local_root="/srv/objects",
        _env_file=None,
    )
    return base.model_copy(update=update)


def _gpu_bundle(compute_type: str = "int8_float32", device: str = "cuda"):
    return SimpleNamespace(
        asr=LocalWhisperProvider(model_name="large-v3", device=device, compute_type=compute_type)
    )


def _cpu_bundle(compute_type: str = "int8"):
    return SimpleNamespace(
        asr=LocalWhisperProvider(model_name="base", device="cpu", compute_type=compute_type)
    )


def _indexed_whisper(device_index, device: str = "cuda") -> LocalWhisperProvider:
    return LocalWhisperProvider(
        model_name="large-v3",
        device=device,
        compute_type="int8_float32",
        device_index=device_index,
    )


class PublicDeviceWhisper(LocalWhisperProvider):
    """A public ``device`` that differs from ``_device``: the public one must win."""

    @property
    def device(self) -> str:
        return "cuda"


def _check(result: dict, name: str) -> dict:
    return next(check for check in result["checks"] if check["name"] == name)


def _run(settings=None, bundle=None, options=None, **probe_kwargs) -> dict:
    return run_host_checks(
        settings or _settings(),
        bundle or _gpu_bundle(),
        options or HostOptions(),
        probes(**probe_kwargs),
    )


# --- shape ----------------------------------------------------------------------------


def test_every_check_is_reported_once_in_order_and_is_json() -> None:
    result = _run(bundle=SimpleNamespace(asr=MockASRProvider(allow_mock=True)))
    assert [check["name"] for check in result["checks"]] == list(CHECK_NAMES)
    for check in result["checks"]:
        assert check["status"] in {"pass", "fail", "warn", "skip"}
        assert isinstance(check["detail"], str) and check["detail"]
    json.dumps(result)
    assert result["ok"] is not any(c["status"] == "fail" for c in result["checks"])


def test_a_non_whisper_adapter_skips_the_runtime_and_gpu_checks() -> None:
    result = _run(bundle=SimpleNamespace(asr=MockASRProvider(allow_mock=True)), smi=None)
    for name in ("asr_runtime", "gpu_driver", "gpu_memory"):
        assert _check(result, name)["status"] == "skip"
    for name in ("tls_certificate", "no_internet_config", "internet_probe"):
        assert _check(result, name)["status"] == "skip"


# --- asr_runtime ----------------------------------------------------------------------


def test_runtime_reports_the_adapter_not_the_settings() -> None:
    settings = _settings(whisper_model="base", whisper_device="cpu", whisper_compute_type="int8")
    check = _check(_run(settings=settings), "asr_runtime")
    assert check["status"] == "pass", check
    assert check["model"] == "large-v3"
    assert check["device"] == "cuda"
    assert check["device_index"] == 0
    assert check["compute_type"] == "int8_float32"
    assert check["asr_profile"] is None


@pytest.mark.parametrize("index, expected", [(1, 1), ((1, 0), 1)])
def test_runtime_reads_the_public_device_index(index, expected) -> None:
    adapter = PublicDeviceWhisper(
        model_name="large-v3", device="cpu", compute_type="int8_float32", device_index=index
    )
    assert adapter._device == "cpu"
    settings = _settings().model_copy(update={"asr_profile": "gpu-p40-large-v3"})
    result = _run(settings=settings, bundle=SimpleNamespace(asr=adapter))
    check = _check(result, "asr_runtime")
    assert check["device"] == "cuda" and check["device_index"] == expected
    assert check["asr_profile"] == "gpu-p40-large-v3"
    assert _check(result, "gpu_memory")["target_index"] == expected


def test_runtime_fails_without_faster_whisper_and_passes_with_it() -> None:
    check = _check(_run(spec=False), "asr_runtime")
    assert check["status"] == "fail" and "INSTALL_AI_LOCAL=true" in check["detail"]
    assert _check(_run(spec=True), "asr_runtime")["status"] == "pass"


def test_runtime_fails_without_ctranslate2() -> None:
    assert _check(_run(ct2=False), "asr_runtime")["status"] == "fail"


def test_gpu_mode_needs_cuda_in_the_container() -> None:
    assert _check(_run(ct2=FakeCT2(count=0)), "asr_runtime")["status"] == "fail"
    assert _check(_run(ct2=FakeCT2(count=2)), "asr_runtime")["status"] == "pass"


def test_gpu_device_with_an_index_suffix_is_gpu_mode() -> None:
    result = _run(bundle=_gpu_bundle(device="cuda:0"), ct2=FakeCT2(count=0))
    assert _check(result, "asr_runtime")["status"] == "fail"
    assert _check(result, "gpu_driver")["status"] == "pass"


@pytest.mark.parametrize("bundle", [_gpu_bundle("float16"), _cpu_bundle("float16")])
def test_an_unsupported_compute_type_fails(bundle) -> None:
    assert _check(_run(bundle=bundle), "asr_runtime")["status"] == "fail"


@pytest.mark.parametrize("compute_type", ["default", "auto"])
@pytest.mark.parametrize("make", [_gpu_bundle, _cpu_bundle])
def test_default_and_auto_compute_types_are_always_accepted(make, compute_type) -> None:
    ct2 = FakeCT2(cuda={"float32"}, cpu={"float32"})
    assert _check(_run(bundle=make(compute_type), ct2=ct2), "asr_runtime")["status"] == "pass"


def test_local_files_only_needs_model_bin_in_the_model_path(tmp_path: Path) -> None:
    settings = _settings(whisper_local_files_only=True, whisper_model_path=str(tmp_path))
    assert _check(_run(settings=settings), "asr_runtime")["status"] == "fail"
    (tmp_path / "model.bin").write_bytes(b"weights")
    assert _check(_run(settings=settings), "asr_runtime")["status"] == "pass"
    missing = _settings(whisper_local_files_only=True, whisper_model_path=str(tmp_path / "no"))
    assert _check(_run(settings=missing), "asr_runtime")["status"] == "fail"


def test_local_files_only_without_a_model_path_warns() -> None:
    settings = _settings(whisper_local_files_only=True, whisper_model_path="")
    assert _check(_run(settings=settings), "asr_runtime")["status"] == "warn"


def test_runtime_never_imports_faster_whisper_or_loads_a_model() -> None:
    adapter = LocalWhisperProvider(
        model_name="large-v3",
        device="cuda",
        compute_type="int8_float32",
        model_factory=lambda *a, **k: pytest.fail("a model was loaded"),
    )
    assert _check(_run(bundle=SimpleNamespace(asr=adapter)), "asr_runtime")["status"] == "pass"


# --- gpu_driver and gpu_memory ----------------------------------------------------------


def test_gpu_mode_without_nvidia_smi_fails_cpu_mode_skips() -> None:
    result = _run(smi=FileNotFoundError("nvidia-smi"))
    assert _check(result, "gpu_driver")["status"] == "fail"
    assert _check(result, "gpu_memory")["status"] == "fail"
    assert not result["ok"]
    calls: list = []
    cpu = _run(bundle=_cpu_bundle(), smi=FileNotFoundError("nvidia-smi"), calls=calls)
    assert _check(cpu, "gpu_driver")["status"] == "skip"
    assert _check(cpu, "gpu_memory")["status"] == "skip"
    assert calls == []


@pytest.mark.parametrize(
    "smi, returncode",
    [
        (subprocess.TimeoutExpired("nvidia-smi", 5), 0),
        (PermissionError("denied"), 0),
        (TWO_P40, 9),
        ("", 0),
    ],
)
def test_nvidia_smi_errors_fail_the_gpu_checks(smi, returncode) -> None:
    result = _run(smi=smi, returncode=returncode)
    assert _check(result, "gpu_driver")["status"] == "fail"
    assert _check(result, "gpu_memory")["status"] == "fail"


def test_nvidia_smi_runs_once_with_argv_and_timeout() -> None:
    calls: list = []
    result = _run(calls=calls, environ={"CUDA_VISIBLE_DEVICES": "1", "HF_HUB_OFFLINE": "1"})
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == [
        "nvidia-smi",
        "--query-gpu=index,uuid,name,memory.total,memory.used,memory.free,driver_version,"
        "compute_cap",
        "--format=csv,noheader,nounits",
    ]
    assert kwargs["timeout"] == 5 and not kwargs.get("shell")
    gpus = _check(result, "gpu_driver")["gpus"]
    assert gpus[1] == {
        "index": 1,
        "uuid": P40_UUID_1,
        "name": "Tesla P40",
        "memory_total_mib": 24576,
        "memory_used_mib": 4576,
        "memory_free_mib": 20000,
        "driver_version": "535.183.01",
        "compute_capability": "6.1",
    }


def test_values_that_are_not_numbers_become_null() -> None:
    smi = _smi(f"0, {P40_UUID_0}, Tesla P40, [N/A], [Not Supported], [N/A], 535.183.01, [N/A]")
    result = _run(smi=smi)
    gpu = _check(result, "gpu_driver")["gpus"][0]
    assert gpu["memory_total_mib"] is None and gpu["memory_used_mib"] is None
    assert gpu["memory_free_mib"] is None and gpu["compute_capability"] is None
    memory = _check(result, "gpu_memory")
    assert memory["status"] == "fail" and memory["memory_free_mib"] is None


@pytest.mark.parametrize(
    "driver, status",
    [
        ("525.60.12", "fail"),
        ("470.256.02", "fail"),
        ("525.60.13", "pass"),
        ("535.183.01", "pass"),
        ("560.35", "pass"),
    ],
)
def test_driver_version_is_compared_as_dotted_integers(driver, status) -> None:
    smi = _smi(_smi_line(0, P40_UUID_0, 20000, driver=driver))
    assert _check(_run(smi=smi), "gpu_driver")["status"] == status


def test_float16_on_pascal_warns_int8_float32_passes() -> None:
    ct2 = FakeCT2(cuda={"float16", "int8_float32", "int8_float16"})
    environ = {"CUDA_VISIBLE_DEVICES": "1", "HF_HUB_OFFLINE": "1"}
    for compute_type in ("float16", "int8_float16"):
        result = _run(bundle=_gpu_bundle(compute_type), ct2=ct2, environ=environ)
        assert _check(result, "gpu_driver")["status"] == "warn"
        assert result["ok"]
    result = _run(bundle=_gpu_bundle("int8_float32"), ct2=ct2, environ=environ)
    assert _check(result, "gpu_driver")["status"] == "pass"
    volta = _smi(_smi_line(0, P40_UUID_0, 20000, cap="7.0"))
    result = _run(bundle=_gpu_bundle("float16"), ct2=ct2, smi=volta)
    assert _check(result, "gpu_driver")["status"] == "pass"


def test_free_vram_below_the_minimum_fails() -> None:
    check = _check(_run(), "gpu_memory")
    assert check["status"] == "fail"
    assert check["target_index"] == 0
    assert check["memory_free_mib"] == 2000 and check["required_free_mib"] == 4096
    assert "shared" in check["detail"] and "snapshot" in check["detail"]


def test_cuda_visible_devices_picks_the_target_gpu() -> None:
    environ = {"CUDA_VISIBLE_DEVICES": " 1 ", "HF_HUB_OFFLINE": "1"}
    check = _check(_run(environ=environ), "gpu_memory")
    assert check["status"] == "pass"
    assert check["target_index"] == 1 and check["memory_free_mib"] == 20000


def test_cuda_visible_devices_without_a_matching_gpu_fails() -> None:
    check = _check(_run(environ={"CUDA_VISIBLE_DEVICES": "5"}), "gpu_memory")
    assert check["status"] == "fail" and check["target_index"] is None


def test_cuda_visible_devices_matches_a_uuid_prefix() -> None:
    environ = {"CUDA_VISIBLE_DEVICES": P40_UUID_1[:12], "HF_HUB_OFFLINE": "1"}
    check = _check(_run(environ=environ), "gpu_memory")
    assert check["status"] == "pass" and check["target_index"] == 1


def test_device_index_points_into_cuda_visible_devices() -> None:
    bundle = SimpleNamespace(asr=_indexed_whisper(1))
    environ = {"CUDA_VISIBLE_DEVICES": "1,0", "HF_HUB_OFFLINE": "1"}
    check = _check(_run(bundle=bundle, environ=environ), "gpu_memory")
    assert check["target_index"] == 0 and check["status"] == "fail"
    check = _check(_run(bundle=bundle, environ={}), "gpu_memory")
    assert check["target_index"] == 1 and check["status"] == "pass"


def test_the_free_vram_minimum_is_an_option() -> None:
    check = _check(_run(options=HostOptions(min_free_gpu_mib=1500)), "gpu_memory")
    assert check["status"] == "pass" and check["required_free_mib"] == 1500


# --- disk -------------------------------------------------------------------------------


def _disk(free: dict[str, int]):
    def usage(path: str):
        if path not in free:
            raise FileNotFoundError(path)
        return _usage(free[path])

    return usage


def test_disk_checks_every_configured_path() -> None:
    settings = _settings(
        model_artifact_root="/models", whisper_model_path="/models/large-v3"
    ).model_copy(update={"storage_local_root": "/srv/objects"})
    disk = _disk(
        {"/scratch": 9000, "/models": 9000, "/models/large-v3": 9000, "/srv/objects": 9000}
    )
    check = _check(_run(settings=settings, disk=disk), "disk")
    assert check["status"] == "pass"
    assert [entry["label"] for entry in check["paths"]] == [
        "temp",
        "model_artifact_root",
        "whisper_model_path",
        "storage_local_root",
    ]
    assert check["paths"][0] == {"label": "temp", "path": "/scratch", "free_mib": 9000, "ok": True}


def test_disk_fails_on_a_missing_path() -> None:
    settings = _settings(model_artifact_root="/missing")
    disk = _disk({"/scratch": 9000, "/srv/objects": 9000})
    check = _check(_run(settings=settings, disk=disk), "disk")
    assert check["status"] == "fail"
    missing = next(e for e in check["paths"] if e["label"] == "model_artifact_root")
    assert missing["free_mib"] is None and missing["ok"] is False


def test_disk_minimum_is_inclusive() -> None:
    at_minimum = _disk({"/scratch": 2048, "/srv/objects": 2048})
    assert _check(_run(disk=at_minimum), "disk")["status"] == "pass"
    below = _disk({"/scratch": 2047, "/srv/objects": 9000})
    assert _check(_run(disk=below), "disk")["status"] == "fail"
    options = HostOptions(min_free_disk_mib=10_000)
    big = _disk({"/scratch": 9000, "/srv/objects": 9000})
    assert _check(_run(disk=big, options=options), "disk")["status"] == "fail"


def test_disk_skips_paths_that_do_not_apply() -> None:
    settings = _settings(storage_backend="s3", whisper_model_path="/models/x")
    check = _check(
        _run(settings=settings, bundle=SimpleNamespace(asr=MockASRProvider(allow_mock=True))),
        "disk",
    )
    assert [entry["label"] for entry in check["paths"]] == ["temp"]


# --- dependency_ports -------------------------------------------------------------------


def _deps_settings(**update) -> Settings:
    return _settings(
        database_url="postgresql+psycopg://homayar_api:db-pass-s3cret@postgres/homayar?sslmode=disable",
        redis_url="rediss://:redis-pass-s3cret@redis.internal/0",
        storage_backend="s3",
        storage_endpoint_url="https://minio.internal",
        storage_access_key="ACCESSKEYS3CRET",
        storage_secret_key="secret-key-s3cret",  # noqa: S106 - fake, never sent
        local_ai_base_url="http://llm.internal:8000/v1",
        **update,
    )


def test_dependency_ports_connect_to_every_configured_target() -> None:
    net = Net()
    environ = {
        "https_proxy": "http://user:proxy-pass-s3cret@egress-proxy:3128",
        "HF_HUB_OFFLINE": "1",
    }
    result = _run(settings=_deps_settings(), net=net, environ=environ)
    check = _check(result, "dependency_ports")
    assert check["status"] == "pass"
    assert check["targets"] == [
        {"name": "database", "host": "postgres", "port": 5432, "ok": True},
        {"name": "redis", "host": "redis.internal", "port": 6379, "ok": True},
        {"name": "storage", "host": "minio.internal", "port": 443, "ok": True},
        {"name": "local_ai", "host": "llm.internal", "port": 8000, "ok": True},
        {"name": "egress_proxy", "host": "egress-proxy", "port": 3128, "ok": True},
    ]
    assert all(timeout == 2.0 for _, _, timeout in net.calls)
    text = json.dumps(result)
    for secret in ("s3cret", "homayar_api", "sslmode", "/v1", "ACCESSKEY"):
        assert secret not in text


def test_a_refused_dependency_fails_with_the_error_class_only() -> None:
    result = _run(settings=_deps_settings(), net=Net(refuse={"redis.internal"}))
    check = _check(result, "dependency_ports")
    assert check["status"] == "fail" and not result["ok"]
    redis = next(t for t in check["targets"] if t["name"] == "redis")
    assert redis == {
        "name": "redis",
        "host": "redis.internal",
        "port": 6379,
        "ok": False,
        "error": "ConnectionRefusedError",
    }
    assert "s3cret" not in json.dumps(result)


def test_dependency_ports_honour_explicit_ports_and_skip_sqlite() -> None:
    settings = _settings(
        database_url="postgresql://u:p@10.0.0.7:6543/db",
        redis_url="redis://cache:6380/1",
        storage_backend="s3",
        storage_endpoint_url="http://minio:9000",
    )
    targets = _check(_run(settings=settings), "dependency_ports")["targets"]
    assert [(t["name"], t["host"], t["port"]) for t in targets] == [
        ("database", "10.0.0.7", 6543),
        ("redis", "cache", 6380),
        ("storage", "minio", 9000),
    ]
    sqlite_only = _settings(redis_url="")
    check = _check(_run(settings=sqlite_only), "dependency_ports")
    assert check["status"] == "skip" and check["targets"] == []


def test_upper_case_proxy_wins() -> None:
    environ = {"HTTP_PROXY": "http://upper:3128", "http_proxy": "http://lower:3128"}
    targets = _check(_run(environ=environ), "dependency_ports")["targets"]
    assert targets[-1] == {"name": "egress_proxy", "host": "upper", "port": 3128, "ok": True}


# --- tls_certificate --------------------------------------------------------------------


def _cert(
    *,
    days_left: float = 60,
    names=("app.homayar.com",),
    cn="app.homayar.com",
    not_before_days: float = -30,
):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW + timedelta(days=not_before_days))
        .not_valid_after(NOW + timedelta(days=days_left))
    )
    if names:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False
        )
    cert = builder.sign(key, hashes.SHA256())
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return cert.public_bytes(serialization.Encoding.PEM), key_pem


def _tls(pem: bytes, tmp_path: Path, **options) -> dict:
    path = tmp_path / "fullchain.pem"
    path.write_bytes(pem)
    return _check(_run(options=HostOptions(tls_cert=str(path), **options)), "tls_certificate")


def test_tls_check_reports_the_leaf_certificate(tmp_path: Path) -> None:
    leaf, _ = _cert(days_left=60, names=("app.homayar.com", "www.homayar.com"))
    other, _ = _cert(days_left=3, cn="Intermediate", names=())
    check = _tls(leaf + other, tmp_path, tls_host="APP.homayar.com")
    assert check["status"] == "pass", check
    assert check["subject"] == "app.homayar.com"
    assert check["dns_names"] == ["app.homayar.com", "www.homayar.com"]
    assert check["days_left"] == 60
    assert check["not_after"].startswith("2026-12-29T12:00:00")


@pytest.mark.parametrize(
    "days_left, status", [(-1, "fail"), (3, "fail"), (14, "pass"), (60, "pass")]
)
def test_tls_expiry_and_minimum_days(tmp_path: Path, days_left, status) -> None:
    check = _tls(_cert(days_left=days_left)[0], tmp_path)
    assert check["status"] == status
    assert check["days_left"] == days_left


def test_tls_not_yet_valid_fails(tmp_path: Path) -> None:
    assert _tls(_cert(not_before_days=1)[0], tmp_path)["status"] == "fail"


def test_tls_minimum_days_is_an_option(tmp_path: Path) -> None:
    pem = _cert(days_left=20)[0]
    assert _tls(pem, tmp_path, tls_min_days=30)["status"] == "fail"
    assert _tls(pem, tmp_path, tls_min_days=10)["status"] == "pass"


@pytest.mark.parametrize(
    "host, status",
    [
        ("app.example.com", "pass"),
        ("example.com", "fail"),
        ("a.b.example.com", "fail"),
        ("other.org", "fail"),
    ],
)
def test_tls_host_matches_san_names_with_one_label_wildcards(tmp_path: Path, host, status) -> None:
    pem = _cert(names=("*.example.com",), cn="example.com")[0]
    assert _tls(pem, tmp_path, tls_host=host)["status"] == status


def test_tls_host_without_san_names_never_matches(tmp_path: Path) -> None:
    pem = _cert(names=(), cn="app.homayar.com")[0]
    assert _tls(pem, tmp_path)["status"] == "pass"
    assert _tls(pem, tmp_path, tls_host="app.homayar.com")["status"] == "fail"


def test_tls_input_with_a_private_key_fails_without_echoing_it(tmp_path: Path) -> None:
    cert, key = _cert()
    path = tmp_path / "bundle.pem"
    path.write_bytes(cert + key)
    result = _run(options=HostOptions(tls_cert=str(path)))
    check = _check(result, "tls_certificate")
    assert check["status"] == "fail" and "certificate" in check["detail"]
    body = key.decode().splitlines()[1]
    assert body not in json.dumps(result)
    path.write_bytes(cert)
    assert (
        _check(_run(options=HostOptions(tls_cert=str(path))), "tls_certificate")["status"] == "pass"
    )


def test_tls_reads_stdin_for_a_dash() -> None:
    pem = _cert()[0]
    check = _check(_run(options=HostOptions(tls_cert="-"), stdin=pem), "tls_certificate")
    assert check["status"] == "pass"


def test_tls_unreadable_or_not_pem_fails(tmp_path: Path) -> None:
    assert _tls(b"not a certificate", tmp_path)["status"] == "fail"
    options = HostOptions(tls_cert=str(tmp_path / "missing.pem"))
    assert _check(_run(options=options), "tls_certificate")["status"] == "fail"


# --- no_internet_config -----------------------------------------------------------------


def _offline(settings: Settings, environ=None, bundle=None) -> dict:
    return _check(
        _run(
            settings=settings,
            bundle=bundle,
            options=HostOptions(offline=True),
            environ=environ if environ is not None else {"HF_HUB_OFFLINE": "1"},
        ),
        "no_internet_config",
    )


def _quiet(**update) -> Settings:
    quiet = {
        "whisper_local_files_only": True,
        "enable_google_auth": False,
        "enable_phone_auth": False,
        "sentry_dsn": "",
        "smtp_host": "",
    }
    return _settings(**{**quiet, **update})


def _findings(check: dict) -> dict[str, str]:
    return {finding["setting"]: finding["status"] for finding in check["findings"]}


def test_a_quiet_offline_configuration_passes() -> None:
    check = _offline(_quiet())
    assert check["status"] == "pass" and check["findings"] == []


@pytest.mark.parametrize(
    "update, setting",
    [
        ({"whisper_local_files_only": False}, "WHISPER_LOCAL_FILES_ONLY"),
        ({"enable_google_auth": True}, "ENABLE_GOOGLE_AUTH"),
        ({"enable_phone_auth": True}, "ENABLE_PHONE_AUTH"),
        ({"smtp_host": "smtp.gmail.com"}, "SMTP_HOST"),
        ({"sentry_dsn": "https://pub-s3cret@o1.ingest.sentry.io/2"}, "SENTRY_DSN"),
    ],
)
def test_offline_fail_findings(update, setting) -> None:
    check = _offline(_quiet(**update))
    assert check["status"] == "fail"
    assert _findings(check) == {setting: "fail"}
    assert "s3cret" not in json.dumps(check)


@pytest.mark.parametrize(
    "update",
    [
        {"smtp_host": "smtp-relay"},
        {"smtp_host": "10.0.0.5"},
        {"sentry_dsn": "http://key@sentry.internal/1"},
        {"enable_phone_auth": True, "asanak_base_url": "http://sms-gateway/send"},
    ],
)
def test_local_hosts_are_not_offline_findings(update) -> None:
    assert _offline(_quiet(**update))["status"] == "pass"


def test_offline_warn_findings() -> None:
    check = _offline(_quiet(), environ={})
    assert check["status"] == "warn" and _findings(check) == {"HF_HUB_OFFLINE": "warn"}
    check = _offline(_quiet(), environ={"HF_HUB_OFFLINE": "1", "https_proxy": "http://p:3128"})
    assert check["status"] == "warn" and _findings(check) == {"HTTPS_PROXY": "warn"}
    check = _offline(_quiet(enable_google_auth=True), environ={"HTTP_PROXY": "http://p:3128"})
    assert check["status"] == "fail"
    assert _findings(check) == {
        "ENABLE_GOOGLE_AUTH": "fail",
        "HF_HUB_OFFLINE": "warn",
        "HTTP_PROXY": "warn",
    }


def test_whisper_findings_only_for_local_whisper() -> None:
    bundle = SimpleNamespace(asr=MockASRProvider(allow_mock=True))
    check = _offline(_quiet(whisper_local_files_only=False), environ={}, bundle=bundle)
    assert check["status"] == "pass"


# --- internet_probe ---------------------------------------------------------------------


def test_internet_probe_fails_when_any_target_is_reachable() -> None:
    net = Net(refuse={"1.1.1.1", "8.8.8.8"})
    check = _check(_run(options=HostOptions(probe_internet=True), net=net), "internet_probe")
    assert check["status"] == "fail"
    assert check["targets"] == [
        {"host": "1.1.1.1", "port": 443, "reachable": False},
        {"host": "8.8.8.8", "port": 53, "reachable": False},
        {"host": "huggingface.co", "port": 443, "reachable": True},
    ]
    assert {(h, p, t) for h, p, t in net.calls} >= {
        (host, port, 3.0) for host, port in DEFAULT_PROBE_TARGETS
    }


@pytest.mark.parametrize("error", [OSError, socket.gaierror, TimeoutError])
def test_internet_probe_passes_when_every_target_is_blocked(error) -> None:
    net = Net(refuse={"*"}, error=error)
    options = HostOptions(probe_internet=True, probe_targets=(("example.org", 80),))
    check = _check(_run(options=options, net=net), "internet_probe")
    assert check["status"] == "pass"
    assert check["targets"] == [{"host": "example.org", "port": 80, "reachable": False}]


def test_no_network_call_unless_asked() -> None:
    net = Net()
    _run(bundle=SimpleNamespace(asr=MockASRProvider(allow_mock=True)), net=net)
    assert [host for host, _, _ in net.calls] == ["localhost"]  # redis only


# --- run_preflight and the CLI ----------------------------------------------------------


def _preflight_bundle() -> ProviderBundle:
    return ProviderBundle(
        asr=MockASRProvider(allow_mock=True),
        diarization=UnavailableDiarizationProvider(),
        intelligence=MockIntelligenceProvider(allow_mock=True),
    )


def test_preflight_without_host_is_unchanged() -> None:
    settings = _settings()
    code, report = run_preflight(settings, providers=_preflight_bundle())
    assert "host" not in report
    code_host, report_host = run_preflight(
        settings, providers=_preflight_bundle(), host=HostOptions(), probes=probes()
    )
    assert report_host.pop("host")["ok"]
    assert (code_host, report_host) == (code, report)


def test_a_failing_host_check_makes_preflight_not_ready() -> None:
    settings = _settings()
    code, report = run_preflight(settings, providers=_preflight_bundle())
    assert code == 0, report
    failing = probes(disk=lambda path: _usage(10))
    code, report = run_preflight(
        settings, providers=_preflight_bundle(), host=HostOptions(), probes=failing
    )
    assert code == 1 and report["ready"] is False and report["host"]["ok"] is False
    warning = probes(environ={"HTTPS_PROXY": "http://p:3128"})
    code, report = run_preflight(
        settings, providers=_preflight_bundle(), host=HostOptions(), probes=warning
    )
    assert code == 0 and report["ready"] is True


def test_preflight_passes_offline_into_the_host_options() -> None:
    _, report = run_preflight(
        _settings(),
        providers=_preflight_bundle(),
        offline=True,
        host=HostOptions(),
        probes=probes(),
    )
    assert _check(report["host"], "no_internet_config")["status"] != "skip"
    _, report = run_preflight(
        _settings(),
        providers=_preflight_bundle(),
        host=HostOptions(offline=True),
        probes=probes(),
    )
    assert _check(report["host"], "no_internet_config")["status"] == "skip"


@pytest.mark.parametrize(
    "argv",
    [
        ["--tls-cert", "x.pem"],
        ["--probe-internet"],
        ["--min-free-gpu-mib", "1"],
        ["--min-free-disk-mib", "1"],
        ["--tls-host", "app.homayar.com"],
        ["--tls-min-days", "1"],
        ["--probe-target", "a:1"],
        ["--host", "--probe-target", "a:1"],
        ["--host", "--egress-allowlist"],
        ["--host", "--min-free-gpu-mib", "-1"],
        ["--host", "--min-free-disk-mib", "-1"],
        ["--host", "--tls-min-days", "-1"],
        ["--host", "--probe-internet", "--probe-target", "nohost"],
        ["--host", "--probe-internet", "--probe-target", ":443"],
        ["--host", "--probe-internet", "--probe-target", "a:0"],
        ["--host", "--probe-internet", "--probe-target", "a:65536"],
        ["--host", "--probe-internet", "--probe-target", "a:http"],
    ],
)
def test_cli_rejects_flag_misuse(argv, monkeypatch) -> None:
    monkeypatch.setattr(model_preflight, "run_preflight", lambda *a, **k: pytest.fail("ran"))
    with pytest.raises(SystemExit) as raised:
        model_preflight.main(argv)
    assert raised.value.code == 2


def test_cli_builds_the_host_options(monkeypatch, capsys) -> None:
    seen: dict = {}

    def fake(settings, **kwargs):
        seen.update(kwargs)
        return 1, {"ready": False, "host": {"ok": False, "checks": []}}

    monkeypatch.setattr(model_preflight, "get_settings", lambda: _settings())
    monkeypatch.setattr(model_preflight, "run_preflight", fake)
    code = model_preflight.main(
        [
            "--host",
            "--offline",
            "--min-free-gpu-mib",
            "6000",
            "--min-free-disk-mib",
            "0",
            "--tls-cert",
            "-",
            "--tls-host",
            "app.homayar.com",
            "--tls-min-days",
            "20",
            "--probe-internet",
            "--probe-target",
            "1.1.1.1:443",
            "--probe-target",
            "[2606:4700::1111]:443",
        ]
    )
    assert code == 1
    assert seen["offline"] is True
    assert seen["host"] == HostOptions(
        min_free_gpu_mib=6000,
        min_free_disk_mib=0,
        tls_cert="-",
        tls_host="app.homayar.com",
        tls_min_days=20,
        offline=True,
        probe_internet=True,
        probe_targets=(("1.1.1.1", 443), ("2606:4700::1111", 443)),
    )
    assert json.loads(capsys.readouterr().out)["host"]["ok"] is False


def test_cli_without_host_passes_no_host_options(monkeypatch, capsys) -> None:
    seen: dict = {}

    def fake(settings, **kwargs):
        seen.update(kwargs)
        return 0, {"ready": True}

    monkeypatch.setattr(model_preflight, "get_settings", lambda: _settings())
    monkeypatch.setattr(model_preflight, "run_preflight", fake)
    assert model_preflight.main([]) == 0
    assert seen.get("host") is None
    defaults = HostOptions()
    assert replace(defaults, offline=True).probe_targets == DEFAULT_PROBE_TARGETS


# --- amendment A1: host checks on the exit-2 path ---------------------------------------


def test_exit_2_with_a_built_bundle_still_reports_the_host() -> None:
    bad_pins = _settings(model_manifest_pins="not-a-pin")
    code, report = run_preflight(bad_pins, providers=_preflight_bundle())
    assert (code, report) == (2, {"ready": False, "error": "configuration_error"})
    code, report = run_preflight(
        bad_pins, providers=_preflight_bundle(), host=HostOptions(), probes=probes()
    )
    assert code == 2 and report["ready"] is False
    assert report["error"] == "configuration_error"
    assert set(report) == {"ready", "error", "host"}
    assert report["host"]["ok"] is True
    # The built bundle (mock ASR) was used: not local whisper, so the runtime check skips.
    runtime = _check(report["host"], "asr_runtime")
    assert runtime["status"] == "skip" and "not local Whisper" in runtime["detail"]


def test_exit_2_without_a_bundle_checks_the_asr_adapter_alone() -> None:
    settings = _settings(asr_provider="local_whisper", whisper_device="cuda")
    code, report = run_preflight(settings)
    assert (code, report) == (2, {"ready": False, "error": "intelligence_provider_unconfigured"})
    code, report = run_preflight(settings, host=HostOptions(), probes=probes())
    assert code == 2 and report["ready"] is False
    assert report["error"] == "intelligence_provider_unconfigured"
    runtime = _check(report["host"], "asr_runtime")
    assert runtime["status"] == "pass" and runtime["device"] == "cuda"
    assert _check(report["host"], "gpu_memory")["status"] == "fail"
    assert report["host"]["ok"] is False


def test_exit_2_stays_2_when_every_host_check_passes() -> None:
    settings = _settings(whisper_device="cuda")
    environ = {"CUDA_VISIBLE_DEVICES": "1", "HF_HUB_OFFLINE": "1"}
    code, report = run_preflight(settings, host=HostOptions(), probes=probes(environ=environ))
    assert report["host"]["ok"] is True
    assert code == 2 and report["ready"] is False


def test_exit_2_when_the_asr_adapter_cannot_be_built() -> None:
    settings = _settings(asr_provider="no-such-asr", whisper_model_path="/models/x")
    code, report = run_preflight(settings)
    assert code == 2 and "host" not in report
    disk = _disk({"/scratch": 9000, "/srv/objects": 9000, "/models/x": 9000})
    options = HostOptions(offline=True)
    code, report = run_preflight(
        settings, offline=True, host=options, probes=probes(environ={}, disk=disk)
    )
    assert code == 2 and report["ready"] is False and report["error"] == "unsupported_asr_provider"
    host = report["host"]
    assert [check["name"] for check in host["checks"]] == list(CHECK_NAMES)
    for name in ("asr_runtime", "gpu_driver", "gpu_memory"):
        check = _check(host, name)
        assert check["status"] == "skip" and "could not be built" in check["detail"]
    labels = [entry["label"] for entry in _check(host, "disk")["paths"]]
    assert labels == ["temp", "storage_local_root"]
    findings = _findings(_check(host, "no_internet_config"))
    assert "WHISPER_LOCAL_FILES_ONLY" not in findings and "HF_HUB_OFFLINE" not in findings


def test_run_host_checks_accepts_no_bundle() -> None:
    settings = _quiet(whisper_local_files_only=False, enable_google_auth=True)
    result = run_host_checks(settings, None, HostOptions(offline=True), probes(environ={}))
    assert [check["name"] for check in result["checks"]] == list(CHECK_NAMES)
    assert _check(result, "asr_runtime")["status"] == "skip"
    assert _findings(_check(result, "no_internet_config")) == {"ENABLE_GOOGLE_AUTH": "fail"}


# --- review round 1 ---------------------------------------------------------------------


@pytest.mark.parametrize("password", ["ab/cd+ef==", "pa#ss", "pa?ss", "p%2Fss"])
def test_database_password_with_url_characters_keeps_the_real_host(password) -> None:
    url = f"postgresql+psycopg://homayar_app:{password}@db:5432/homayar"
    net = Net()
    result = _run(settings=_settings(database_url=url, redis_url=""), net=net)
    check = _check(result, "dependency_ports")
    assert check["targets"] == [{"name": "database", "host": "db", "port": 5432, "ok": True}]
    assert net.calls == [("db", 5432, 2.0)]
    text = json.dumps(result)
    assert "homayar_app" not in text and password not in text
    for part in re.split(r"[/#?+=%]", password):
        if len(part) > 1:
            assert part not in check["detail"]
    assert "cd+ef" not in text and "p/ss" not in text


def test_database_url_without_a_port_uses_5432_and_bad_urls_are_skipped() -> None:
    settings = _settings(database_url="postgresql://u:pa#ss@db/h", redis_url="")
    targets = _check(_run(settings=settings), "dependency_ports")["targets"]
    assert targets == [{"name": "database", "host": "db", "port": 5432, "ok": True}]
    for url in ("not a url", "postgresql:///homayar?host=/var/run/postgresql"):
        check = _check(_run(settings=_settings(database_url=url, redis_url="")), "dependency_ports")
        assert check["status"] == "skip" and check["targets"] == []


@pytest.mark.parametrize("flag", ["--min-free-gpu-mib", "--min-free-disk-mib", "--tls-min-days"])
def test_cli_zero_is_still_a_host_only_value(flag, monkeypatch) -> None:
    seen: dict = {}

    def fake(settings, **kwargs):
        seen.update(kwargs)
        return 0, {"ready": True}

    monkeypatch.setattr(model_preflight, "get_settings", lambda: _settings())
    monkeypatch.setattr(model_preflight, "run_preflight", fake)
    with pytest.raises(SystemExit) as raised:
        model_preflight.main([flag, "0"])
    assert raised.value.code == 2 and seen == {}
    assert model_preflight.main(["--host", flag, "0"]) == 0
    assert getattr(seen["host"], flag[2:].replace("-", "_")) == 0


# --- after HOM-39: a named ASR profile ----------------------------------------------------


def test_asr_profile_adapter_from_the_factory_is_checked_on_the_gpu() -> None:
    settings = _settings(
        asr_provider="local_whisper",
        asr_profile="gpu-p40-large-v3",
        whisper_model="base",
        whisper_device="cpu",
        whisper_compute_type="int8",
    )
    bundle = SimpleNamespace(asr=create_asr_provider(settings))
    environ = {"CUDA_VISIBLE_DEVICES": "1", "HF_HUB_OFFLINE": "1"}
    result = _run(settings=settings, bundle=bundle, environ=environ)
    runtime = _check(result, "asr_runtime")
    assert runtime["status"] == "pass", runtime
    assert runtime["asr_profile"] == "gpu-p40-large-v3"
    assert runtime["model"] == "large-v3"
    assert runtime["device"] == "cuda" and runtime["device_index"] == 0
    assert runtime["compute_type"] == "int8_float32"
    assert _check(result, "gpu_driver")["status"] == "pass"
    memory = _check(result, "gpu_memory")
    assert memory["status"] == "pass" and memory["target_index"] == 1
    assert result["ok"] is True
