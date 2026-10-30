"""Host checks for the operator preflight (HOM-108, WP19-T03).

``run_host_checks`` checks the machine and configuration the stack really
runs on: the local ASR runtime, the NVIDIA driver, free VRAM on the GPU the
model will use, free disk, reachability of the configured dependencies, a
TLS certificate, the no-internet configuration and, only when asked, an
active outbound probe.  Every side effect goes through ``HostProbes`` so
tests inject fakes.

Nothing here loads a model or imports ``faster_whisper``.  The ASR runtime
is read from the built adapter (``bundle.asr``), never from the
``whisper_*`` settings, because a named ASR profile may build it.  Free VRAM
is a snapshot on shared GPUs: it is reported here and never enters a
``HostProfile`` digest (``hardware.py``).  No secret, credential, URL path
or private key ever enters the report.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from backend.providers.egress import is_local_host

CHECK_NAMES = (
    "asr_runtime",
    "gpu_driver",
    "gpu_memory",
    "disk",
    "dependency_ports",
    "tls_certificate",
    "no_internet_config",
    "internet_probe",
)
# CUDA 12.x minimum Linux driver (CTranslate2 4.x is built against CUDA 12).
MIN_NVIDIA_DRIVER = "525.60.13"
DEFAULT_MIN_FREE_GPU_MIB = 4096
DEFAULT_MIN_FREE_DISK_MIB = 2048
DEFAULT_TLS_MIN_DAYS = 14
DEFAULT_PROBE_TARGETS = (("1.1.1.1", 443), ("8.8.8.8", 53), ("huggingface.co", 443))

NVIDIA_SMI_TIMEOUT_SECONDS = 5
DEPENDENCY_TIMEOUT_SECONDS = 2.0
PROBE_TIMEOUT_SECONDS = 3.0
_NVIDIA_QUERY = [
    "nvidia-smi",
    "--query-gpu=index,uuid,name,memory.total,memory.used,memory.free,driver_version,compute_cap",
    "--format=csv,noheader,nounits",
]
_NVIDIA_OUTPUT_LIMIT = 65536
_PEM_LIMIT = 1024 * 1024
_PRIVATE_KEY = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_PROXY_VARIABLES = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")
# Compute types CTranslate2 resolves itself to a supported one.
_ALWAYS_SUPPORTED = frozenset({"default", "auto"})


def _tcp_connect(host: str, port: int, timeout: float) -> None:
    with socket.create_connection((host, port), timeout=timeout):
        return None


def _import_ctranslate2() -> Any | None:
    try:
        import ctranslate2
    except (ImportError, OSError):
        # OSError: the package is there but its native (CUDA) libraries are not.
        return None
    return ctranslate2


def _read_stdin() -> bytes:
    return sys.stdin.buffer.read(_PEM_LIMIT + 1)


@dataclass(frozen=True)
class HostOptions:
    min_free_gpu_mib: int = DEFAULT_MIN_FREE_GPU_MIB
    min_free_disk_mib: int = DEFAULT_MIN_FREE_DISK_MIB
    tls_cert: str | None = None  # a file path, or "-" for stdin
    tls_host: str | None = None
    tls_min_days: int = DEFAULT_TLS_MIN_DAYS
    offline: bool = False
    probe_internet: bool = False
    probe_targets: tuple[tuple[str, int], ...] = DEFAULT_PROBE_TARGETS


@dataclass(frozen=True)
class HostProbes:
    """Every side effect of the host checks, so tests inject fakes."""

    run: Callable[..., subprocess.CompletedProcess] = subprocess.run  # nvidia-smi only
    connect: Callable[[str, int, float], None] = _tcp_connect
    disk_usage: Callable[[str], Any] = shutil.disk_usage
    find_spec: Callable[[str], object | None] = importlib.util.find_spec
    ctranslate2: Callable[[], object | None] = _import_ctranslate2
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    read_stdin: Callable[[], bytes] = _read_stdin
    temp_dir: Callable[[], str] = tempfile.gettempdir


def _check(name: str, status: str, detail: str, **fields: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, **fields}


def _secret_text(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    text = getter() if callable(getter) else value
    return text.strip() if isinstance(text, str) else ""


def _setting_text(settings: Any, name: str) -> str:
    return _secret_text(getattr(settings, name, ""))


def _status(fails: list[str], warns: list[str], ok_detail: str) -> tuple[str, str]:
    if fails:
        return "fail", fails[0]
    if warns:
        return "warn", warns[0]
    return "pass", ok_detail


# --- the ASR runtime the adapter will use ---------------------------------------------


_NO_ADAPTER = "The ASR adapter could not be built, so the speech runtime is not known."


@dataclass(frozen=True)
class _Runtime:
    local: bool
    built: bool = True
    model: str | None = None
    device: str | None = None
    device_index: int = 0
    compute_type: str | None = None

    @property
    def gpu(self) -> bool:
        return (
            self.local
            and bool(self.device)
            and (self.device == "cuda" or self.device.startswith("cuda:"))
        )


def _runtime(bundle: Any) -> _Runtime:
    if bundle is None:
        # Exit-2 path without an adapter: not known to be local Whisper.
        return _Runtime(local=False, built=False)
    adapter = bundle.asr
    identity = adapter.identity()
    if identity.provider != "local_whisper":
        return _Runtime(local=False)
    # HOM-39 adds public ``device``/``device_index``; main has only ``_device``.
    device = getattr(adapter, "device", None)
    if device is None:
        device = getattr(adapter, "_device", None)
    index = getattr(adapter, "device_index", None)
    if isinstance(index, list | tuple):
        index = index[0] if index else None
    return _Runtime(
        local=True,
        model=identity.model,
        device=str(device) if device is not None else None,
        device_index=index if isinstance(index, int) else 0,
        compute_type=identity.params.get("compute_type"),
    )


def _check_asr_runtime(settings: Any, runtime: _Runtime, probes: HostProbes) -> dict[str, Any]:
    if not runtime.built:
        return _check("asr_runtime", "skip", _NO_ADAPTER)
    if not runtime.local:
        return _check("asr_runtime", "skip", "The ASR adapter is not local Whisper.")
    fields = {
        "model": runtime.model,
        "device": runtime.device,
        "device_index": runtime.device_index,
        "compute_type": runtime.compute_type,
        "asr_profile": getattr(settings, "asr_profile", "") or None,
    }
    fails: list[str] = []
    warns: list[str] = []
    try:
        spec = probes.find_spec("faster_whisper")
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        fails.append(
            "faster-whisper is not installed: build the api image with INSTALL_AI_LOCAL=true."
        )
    ct2 = probes.ctranslate2()
    if ct2 is None:
        fails.append("CTranslate2 is not installed or cannot load in this image.")
    else:
        fails.extend(_compute_type_problems(runtime, ct2))
    if getattr(settings, "whisper_local_files_only", False):
        model_path = _setting_text(settings, "whisper_model_path")
        if not model_path:
            warns.append(
                "WHISPER_LOCAL_FILES_ONLY is on without WHISPER_MODEL_PATH: the weights come "
                "from the Hugging Face cache, which preflight does not check."
            )
        elif not Path(model_path).is_dir():
            fails.append("WHISPER_MODEL_PATH is not a directory.")
        elif not (Path(model_path) / "model.bin").is_file():
            fails.append("WHISPER_MODEL_PATH has no model.bin file.")
    status, detail = _status(
        fails, warns, f"The local Whisper runtime can run on {runtime.device}."
    )
    return _check("asr_runtime", status, detail, **fields)


def _compute_type_problems(runtime: _Runtime, ct2: Any) -> list[str]:
    compute_type = runtime.compute_type or "default"
    try:
        if runtime.gpu:
            if int(ct2.get_cuda_device_count()) == 0:
                return ["CTranslate2 sees no CUDA device: the image or container has no CUDA."]
            if compute_type in _ALWAYS_SUPPORTED:
                return []
            supported = ct2.get_supported_compute_types("cuda", runtime.device_index)
        else:
            if compute_type in _ALWAYS_SUPPORTED:
                return []
            supported = ct2.get_supported_compute_types("cpu")
    except Exception as exc:  # a broken runtime is a finding, not a crash
        return [f"CTranslate2 could not report its devices ({type(exc).__name__})."]
    if compute_type not in set(supported):
        target = "this GPU" if runtime.gpu else "the CPU"
        return [f"Compute type {compute_type} is not supported on {target}."]
    return []


# --- GPU: driver and free memory ------------------------------------------------------


def _number(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        try:
            return int(float(value))
        except ValueError:
            return None


def _compute_capability(value: str) -> str | None:
    try:
        float(value)
    except ValueError:
        return None
    return value


def _parse_gpus(output: str) -> list[dict[str, Any]]:
    gpus: list[dict[str, Any]] = []
    for line in output.splitlines():
        fields = [part.strip() for part in line.split(",")]
        if len(fields) < 8 or not fields[0]:
            continue
        driver = fields[6]
        gpus.append(
            {
                "index": _number(fields[0]),
                "uuid": fields[1][:80],
                "name": fields[2][:200],
                "memory_total_mib": _number(fields[3]),
                "memory_used_mib": _number(fields[4]),
                "memory_free_mib": _number(fields[5]),
                "driver_version": None if driver.startswith("[") or not driver else driver[:80],
                "compute_capability": _compute_capability(fields[7]),
            }
        )
    return gpus


def _query_gpus(probes: HostProbes) -> tuple[list[dict[str, Any]], str | None]:
    """(GPUs, why there are none).  One ``nvidia-smi`` call: argv, timeout, no shell."""
    try:
        completed = probes.run(
            list(_NVIDIA_QUERY),
            capture_output=True,
            text=True,
            timeout=NVIDIA_SMI_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return [], "nvidia-smi is not installed in this container."
    except subprocess.TimeoutExpired:
        return [], "nvidia-smi timed out."
    except OSError:
        return [], "nvidia-smi could not run."
    if completed.returncode != 0:
        return [], "nvidia-smi reported an error."
    gpus = _parse_gpus((completed.stdout or "")[:_NVIDIA_OUTPUT_LIMIT])
    if not gpus:
        return [], "nvidia-smi lists no GPU."
    return gpus, None


def _version(value: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        return None


def _target_gpu(
    gpus: list[dict[str, Any]], runtime: _Runtime, environ: Mapping[str, str]
) -> dict[str, Any] | None:
    """The GPU CUDA will give the adapter, by ``CUDA_VISIBLE_DEVICES`` when set."""
    visible = (environ.get("CUDA_VISIBLE_DEVICES") or "").strip()
    if not visible:
        return next((gpu for gpu in gpus if gpu["index"] == runtime.device_index), None)
    entries = [entry.strip() for entry in visible.split(",")]
    if not 0 <= runtime.device_index < len(entries):
        return None
    entry = entries[runtime.device_index]
    if entry.startswith("GPU-"):
        # CUDA accepts any unique UUID prefix.
        matches = [gpu for gpu in gpus if (gpu["uuid"] or "").startswith(entry)]
        return matches[0] if len(matches) == 1 else None
    number = _number(entry) if entry.lstrip("-").isdigit() else None
    if number is None or number < 0:
        return None
    return next((gpu for gpu in gpus if gpu["index"] == number), None)


def _check_gpu_driver(
    runtime: _Runtime, gpus: list[dict[str, Any]], error: str | None, target: dict | None
) -> dict[str, Any]:
    if not runtime.built:
        return _check("gpu_driver", "skip", _NO_ADAPTER)
    if not runtime.gpu:
        return _check("gpu_driver", "skip", "The ASR adapter does not run on a GPU.")
    if error is not None:
        return _check("gpu_driver", "fail", error, gpus=[])
    minimum = _version(MIN_NVIDIA_DRIVER)
    for gpu in gpus:
        version = _version(gpu["driver_version"] or "")
        if version is None:
            return _check(
                "gpu_driver", "fail", "The NVIDIA driver version cannot be read.", gpus=gpus
            )
        if version < minimum:
            return _check(
                "gpu_driver",
                "fail",
                f"NVIDIA driver {gpu['driver_version']} is older than {MIN_NVIDIA_DRIVER}, "
                "the minimum for CUDA 12.",
                gpus=gpus,
            )
    capability = _version((target or {}).get("compute_capability") or "")
    if "float16" in (runtime.compute_type or "") and capability and capability < (7, 0):
        return _check(
            "gpu_driver",
            "warn",
            f"Compute type {runtime.compute_type} is slow on compute capability "
            f"{target['compute_capability']}: use int8_float32 on Pascal GPUs.",
            gpus=gpus,
        )
    return _check(
        "gpu_driver", "pass", f"NVIDIA driver {gpus[0]['driver_version']} is supported.", gpus=gpus
    )


def _check_gpu_memory(
    runtime: _Runtime,
    error: str | None,
    target: dict | None,
    options: HostOptions,
) -> dict[str, Any]:
    if not runtime.built:
        return _check("gpu_memory", "skip", _NO_ADAPTER)
    if not runtime.gpu:
        return _check("gpu_memory", "skip", "The ASR adapter does not run on a GPU.")
    fields = {
        "target_index": target["index"] if target else None,
        "memory_free_mib": target["memory_free_mib"] if target else None,
        "required_free_mib": options.min_free_gpu_mib,
    }
    if error is not None:
        return _check("gpu_memory", "fail", error, **fields)
    if target is None:
        return _check(
            "gpu_memory",
            "fail",
            "No GPU matches the device index and CUDA_VISIBLE_DEVICES.",
            **fields,
        )
    free = target["memory_free_mib"]
    snapshot = "The GPUs are shared, so this number is a snapshot."
    if free is None:
        return _check(
            "gpu_memory", "fail", f"Free memory of GPU {target['index']} is unknown.", **fields
        )
    if free < options.min_free_gpu_mib:
        return _check(
            "gpu_memory",
            "fail",
            f"GPU {target['index']} has {free} MiB free, below {options.min_free_gpu_mib} MiB. "
            + snapshot,
            **fields,
        )
    return _check(
        "gpu_memory",
        "pass",
        f"GPU {target['index']} has {free} MiB free. " + snapshot,
        **fields,
    )


# --- disk -------------------------------------------------------------------------------


def _check_disk(
    settings: Any, runtime: _Runtime, options: HostOptions, probes: HostProbes
) -> dict[str, Any]:
    paths = [("temp", probes.temp_dir())]
    if root := _setting_text(settings, "model_artifact_root"):
        paths.append(("model_artifact_root", root))
    if runtime.local and (model_path := _setting_text(settings, "whisper_model_path")):
        paths.append(("whisper_model_path", model_path))
    if getattr(settings, "storage_backend", "") == "local":
        if storage_root := _setting_text(settings, "storage_local_root"):
            paths.append(("storage_local_root", storage_root))
    entries: list[dict[str, Any]] = []
    for label, path in paths:
        try:
            free_mib = int(probes.disk_usage(path).free) // (1024 * 1024)
        except OSError:
            free_mib = None
        ok = free_mib is not None and free_mib >= options.min_free_disk_mib
        entries.append({"label": label, "path": path, "free_mib": free_mib, "ok": ok})
    failed = [entry for entry in entries if not entry["ok"]]
    if failed:
        first = failed[0]
        reason = (
            "does not exist"
            if first["free_mib"] is None
            else f"has {first['free_mib']} MiB free, below {options.min_free_disk_mib} MiB"
        )
        return _check("disk", "fail", f"The {first['label']} path {reason}.", paths=entries)
    return _check(
        "disk",
        "pass",
        f"Every path has at least {options.min_free_disk_mib} MiB free.",
        paths=entries,
    )


# --- dependency ports -------------------------------------------------------------------


def _endpoint(url: str, default_port: int | None = None) -> tuple[str, int] | None:
    """(host, port) of a URL; never its user, password, path or query."""
    if "://" not in url:
        url = f"http://{url}"
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        return None
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is None:
        port = default_port or {"https": 443, "http": 80}.get(parts.scheme.lower())
    return (host, port) if port else None


def _database_endpoint(url: str) -> tuple[str, int] | None:
    """(host, port) of ``DATABASE_URL`` as SQLAlchemy reads it.  ``urlsplit``
    would take the user name as the host when the password holds an
    unencoded ``/``, ``#`` or ``?``, which SQLAlchemy accepts."""
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError):
        return None
    if not parsed.host:
        return None
    return parsed.host, parsed.port or 5432


def _proxy(environ: Mapping[str, str]) -> tuple[str, str] | None:
    """(variable, value) of the outbound proxy; upper case names first."""
    for name in _PROXY_VARIABLES:
        value = (environ.get(name) or "").strip()
        if value:
            return name, value
    return None


def _dependency_targets(settings: Any, environ: Mapping[str, str]) -> list[tuple[str, str, int]]:
    targets: list[tuple[str, str, int]] = []

    def add(name: str, url: str, default_port: int | None = None) -> None:
        endpoint = _endpoint(url, default_port)
        if endpoint is not None:
            targets.append((name, *endpoint))

    database = _setting_text(settings, "database_url")
    if database and not database.lower().startswith("sqlite"):
        if endpoint := _database_endpoint(database):
            targets.append(("database", *endpoint))
    redis = _setting_text(settings, "redis_url")
    if redis and redis.lower().startswith(("redis://", "rediss://")):
        add("redis", redis, 6379)
    storage = _setting_text(settings, "storage_endpoint_url")
    if getattr(settings, "storage_backend", "") == "s3" and storage:
        add("storage", storage)
    if local_ai := _setting_text(settings, "local_ai_base_url"):
        add("local_ai", local_ai)
    if proxy := _proxy(environ):
        add("egress_proxy", proxy[1])
    return targets


def _check_dependency_ports(settings: Any, probes: HostProbes) -> dict[str, Any]:
    targets: list[dict[str, Any]] = []
    for name, host, port in _dependency_targets(settings, probes.environ):
        entry: dict[str, Any] = {"name": name, "host": host, "port": port, "ok": True}
        try:
            probes.connect(host, port, DEPENDENCY_TIMEOUT_SECONDS)
        except Exception as exc:  # any connect error means "not reachable"
            entry.update(ok=False, error=type(exc).__name__)
        targets.append(entry)
    if not targets:
        return _check(
            "dependency_ports", "skip", "No networked dependency is configured.", targets=[]
        )
    failed = [target for target in targets if not target["ok"]]
    if failed:
        first = failed[0]
        return _check(
            "dependency_ports",
            "fail",
            f"The {first['name']} at {first['host']}:{first['port']} does not accept connections.",
            targets=targets,
        )
    return _check(
        "dependency_ports",
        "pass",
        "Every configured dependency accepts connections.",
        targets=targets,
    )


# --- TLS certificate --------------------------------------------------------------------


def _host_matches(pattern: str, host: str) -> bool:
    pattern, host = pattern.lower().rstrip("."), host.lower().rstrip(".")
    if pattern.startswith("*."):
        # A wildcard covers exactly one extra label.
        label, _, rest = host.partition(".")
        return bool(label) and rest == pattern[2:]
    return pattern == host


def _check_tls(options: HostOptions, probes: HostProbes) -> dict[str, Any]:
    if options.tls_cert is None:
        return _check("tls_certificate", "skip", "No certificate given (--tls-cert).")
    try:
        if options.tls_cert == "-":
            data = probes.read_stdin()
        else:
            with open(options.tls_cert, "rb") as handle:
                data = handle.read(_PEM_LIMIT + 1)
    except OSError:
        return _check("tls_certificate", "fail", "The certificate file cannot be read.")
    if len(data) > _PEM_LIMIT:
        return _check("tls_certificate", "fail", "The certificate input is too large.")
    if _PRIVATE_KEY.search(data):
        return _check(
            "tls_certificate",
            "fail",
            "The input holds a private key: pass only the certificate (fullchain.pem).",
        )
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    try:
        cert = x509.load_pem_x509_certificates(data)[0]
    except (ValueError, IndexError):
        return _check("tls_certificate", "fail", "The input holds no PEM certificate.")
    names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    subject = str(names[0].value) if names else cert.subject.rfc4514_string()
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        dns_names = list(san.value.get_values_for_type(x509.DNSName))
    except x509.ExtensionNotFound:
        dns_names = []
    now = probes.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    not_after = cert.not_valid_after_utc
    days_left = (not_after - now).days
    fields = {
        "subject": subject,
        "dns_names": dns_names,
        "not_after": not_after.isoformat(),
        "days_left": days_left,
    }
    fails: list[str] = []
    if now >= not_after:
        fails.append("The certificate has expired.")
    elif now < cert.not_valid_before_utc:
        fails.append("The certificate is not valid yet.")
    elif days_left < options.tls_min_days:
        fails.append(
            f"The certificate expires in {days_left} days, fewer than {options.tls_min_days}."
        )
    if options.tls_host and not any(_host_matches(name, options.tls_host) for name in dns_names):
        fails.append(f"The certificate does not name {options.tls_host}.")
    status, detail = _status(fails, [], f"The certificate is valid for {days_left} more days.")
    return _check("tls_certificate", status, detail, **fields)


# --- no-internet configuration ----------------------------------------------------------


def _url_host(value: str) -> str:
    endpoint = _endpoint(value, 1) if value else None
    return endpoint[0] if endpoint else ""


def _check_no_internet(
    settings: Any, runtime: _Runtime, options: HostOptions, probes: HostProbes
) -> dict[str, Any]:
    if not options.offline:
        return _check("no_internet_config", "skip", "Not an offline check (--offline).")
    findings: list[dict[str, str]] = []

    def finding(setting: str, status: str, reason: str) -> None:
        findings.append({"setting": setting, "status": status, "reason": reason})

    if runtime.local and not getattr(settings, "whisper_local_files_only", False):
        finding("WHISPER_LOCAL_FILES_ONLY", "fail", "Whisper may download its weights at runtime.")
    if getattr(settings, "enable_google_auth", False):
        finding("ENABLE_GOOGLE_AUTH", "fail", "Google sign-in needs the internet.")
    if getattr(settings, "enable_phone_auth", False):
        host = _url_host(_setting_text(settings, "asanak_base_url"))
        if not is_local_host(host):
            finding("ENABLE_PHONE_AUTH", "fail", f"Phone sign-in sends SMS through {host}.")
    if sentry := _setting_text(settings, "sentry_dsn"):
        host = _url_host(sentry)
        if not is_local_host(host):
            finding("SENTRY_DSN", "fail", f"Error reports go to {host}.")
    if (smtp := _setting_text(settings, "smtp_host")) and not is_local_host(smtp):
        finding("SMTP_HOST", "fail", f"E-mail goes to {smtp}.")
    if runtime.local and probes.environ.get("HF_HUB_OFFLINE") != "1":
        finding("HF_HUB_OFFLINE", "warn", "The Hugging Face library may try the network.")
    proxies = {name.upper() for name in _PROXY_VARIABLES if (probes.environ.get(name) or "")}
    for name in ("HTTPS_PROXY", "HTTP_PROXY"):
        if name in proxies:
            finding(name, "warn", "An outbound proxy is configured.")
    fails = [f["reason"] for f in findings if f["status"] == "fail"]
    warns = [f["reason"] for f in findings if f["status"] == "warn"]
    status, detail = _status(fails, warns, "Nothing in the configuration needs the internet.")
    return _check("no_internet_config", status, detail, findings=findings)


# --- active internet probe --------------------------------------------------------------


def _check_internet_probe(options: HostOptions, probes: HostProbes) -> dict[str, Any]:
    if not options.probe_internet:
        return _check("internet_probe", "skip", "Not asked to probe (--probe-internet).")
    targets: list[dict[str, Any]] = []
    for host, port in options.probe_targets:
        try:
            probes.connect(host, port, PROBE_TIMEOUT_SECONDS)
        except Exception:  # refused, timed out or no DNS: all mean blocked
            reachable = False
        else:
            reachable = True
        targets.append({"host": host, "port": port, "reachable": reachable})
    reached = [target for target in targets if target["reachable"]]
    if reached:
        first = reached[0]
        return _check(
            "internet_probe",
            "fail",
            f"Outbound internet works: {first['host']}:{first['port']} is reachable.",
            targets=targets,
        )
    return _check("internet_probe", "pass", "No probe target is reachable.", targets=targets)


def run_host_checks(
    settings: Any, bundle: Any, options: HostOptions, probes: HostProbes | None = None
) -> dict[str, Any]:
    """``{"ok": bool, "checks": [...]}``, one check per ``CHECK_NAMES`` entry in order.
    Reads only ``bundle.asr`` from the bundle.  ``bundle`` may be ``None`` (the
    adapter could not be built): the ASR and GPU checks then skip."""
    probes = probes or HostProbes()
    runtime = _runtime(bundle)
    gpus: list[dict[str, Any]] = []
    gpu_error: str | None = None
    target = None
    if runtime.gpu:
        gpus, gpu_error = _query_gpus(probes)
        target = _target_gpu(gpus, runtime, probes.environ) if gpu_error is None else None
    checks = [
        _check_asr_runtime(settings, runtime, probes),
        _check_gpu_driver(runtime, gpus, gpu_error, target),
        _check_gpu_memory(runtime, gpu_error, target, options),
        _check_disk(settings, runtime, options, probes),
        _check_dependency_ports(settings, probes),
        _check_tls(options, probes),
        _check_no_internet(settings, runtime, options, probes),
        _check_internet_probe(options, probes),
    ]
    return {"ok": not any(check["status"] == "fail" for check in checks), "checks": checks}
