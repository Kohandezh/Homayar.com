"""Model preflight for operators (WP08-T01, ADR 0026).

    python -m backend.jobs.model_preflight [--register]
    python -m backend.jobs.model_preflight --egress-allowlist [--require-no-cloud]
    python -m backend.jobs.model_preflight --host [--offline] [--min-free-gpu-mib N]
        [--min-free-disk-mib N] [--tls-cert PATH|-] [--tls-host NAME] [--tls-min-days N]
        [--probe-internet [--probe-target HOST:PORT ...]] [--topology NAME]

Builds the configured provider bundle (no model is loaded and no provider is
called), resolves every stage against the manifest registry in the configured
mode and prints the result as JSON.  For a stage without a usable manifest it
prints a skeleton built from the adapter's declared identity (never a
credential); every capability field of that skeleton must still be filled in
truthfully before it can be qualified.  ``--register`` stores the resolved
manifests in the catalog.  Exit codes: 0 ready, 1 a stage is not qualified,
2 configuration or manifest file error.

A manifest that pins offline artifacts (``artifacts``) has them verified
under ``MODEL_ARTIFACT_ROOT`` (SHA-256; missing or different = not ready).
``--offline`` also evaluates deployment profile O (every stage local or
explicitly unavailable, in-process models with verified artifacts) and makes
any violation not ready.

``--egress-allowlist`` prints only the ``host:port`` lines the egress proxy
needs (``infra/egress/allowlist.txt``), one per networked endpoint, with the
stage, candidate and endpoint kind as a comment; readiness does not change
its exit code.  ``--require-no-cloud`` makes it exit 3 when any of them is a
``cloud`` endpoint (the air-gapped check).

``--host`` (HOM-108) also checks the machine the stack runs on and adds the
result under ``host`` (``backend.providers.host_checks``): the local ASR
runtime, the NVIDIA driver and free VRAM of the target GPU, free disk,
connections to the configured dependencies, a TLS certificate
(``--tls-cert``, ``-`` reads stdin), the no-internet configuration (with
``--offline``) and, only with ``--probe-internet``, an outbound connection
probe and, with ``DEPLOYMENT_TOPOLOGY`` or ``--topology``, the supported
topology (HOM-106, ADR 0047).  A ``fail`` makes the report not ready
(exit 1); ``warn`` and ``skip`` do not change the exit code.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlsplit

from backend.config import Settings, get_settings
from backend.providers.artifacts import verify_artifacts
from backend.providers.base import ProviderError
from backend.providers.factory import (
    ProviderBundle,
    create_asr_provider,
    create_provider_bundle,
)
from backend.providers.host_checks import (
    DEFAULT_MIN_FREE_DISK_MIB,
    DEFAULT_MIN_FREE_GPU_MIB,
    DEFAULT_PROBE_TARGETS,
    DEFAULT_TLS_MIN_DAYS,
    HostOptions,
    HostProbes,
    run_host_checks,
)
from backend.providers.registry import (
    STAGES,
    ManifestError,
    is_strict,
    load_registry,
    manifest_skeleton,
    parse_pins,
)


def _artifact_root(settings: Settings) -> str | None:
    value = getattr(settings, "model_artifact_root", "")
    return value.strip() if isinstance(value, str) and value.strip() else None


def run_preflight(
    settings: Settings,
    *,
    providers: ProviderBundle | None = None,
    offline: bool = False,
    policy=None,
    host: HostOptions | None = None,
    probes: HostProbes | None = None,
) -> tuple[int, dict[str, object]]:
    bundle = providers
    try:
        bundle = bundle or create_provider_bundle(settings)
        registry = load_registry(settings)
        pins = parse_pins(settings.model_manifest_pins)
    except (ProviderError, ManifestError, ValueError) as exc:
        failed: dict[str, object] = {
            "ready": False,
            "error": getattr(exc, "code", "configuration_error"),
        }
        if host is not None:
            # The operator still gets the host report when the stack is not
            # configured yet (HOM-108 A1); the exit code stays 2.
            failed["host"] = run_host_checks(
                settings, bundle or _asr_only(settings), replace(host, offline=offline), probes
            )
        return 2, failed
    strict = is_strict(settings)
    report: dict[str, object] = {"strict": strict, "stages": {}}
    stages: dict[str, object] = {}
    ready = True
    for stage in STAGES:
        identity = getattr(bundle, stage).identity()
        try:
            resolved = registry.resolve(stage, identity, strict=strict, pin=pins.get(stage))
        except ManifestError as exc:
            ready = False
            stages[stage] = {
                "ready": False,
                "error": exc.code,
                "manifest_skeleton": manifest_skeleton(stage, identity),
            }
            continue
        stages[stage] = {
            "ready": True,
            "manifest_id": resolved.manifest_id,
            "version": resolved.version,
            "digest": resolved.digest,
            "status": resolved.manifest.status.value if resolved.manifest else "unregistered",
        }
        if resolved.manifest is not None and resolved.manifest.artifacts:
            root = _artifact_root(settings)
            if root is None:
                stages[stage].update(ready=False, error="artifact_root_unconfigured")
                ready = False
                continue
            artifacts = verify_artifacts(resolved.manifest, root).document()
            stages[stage]["artifacts"] = artifacts
            if not artifacts["ok"]:
                stages[stage].update(ready=False, error="artifacts_unverified")
                ready = False
    fallbacks: dict[str, object] = {}
    for stage, adapters in (getattr(bundle, "fallbacks", None) or {}).items():
        entries = []
        for adapter in adapters:
            identity = adapter.identity()
            try:
                resolved = registry.resolve(stage, identity, strict=strict)
            except ManifestError as exc:
                ready = False
                entries.append(
                    {
                        "ready": False,
                        "error": exc.code,
                        "manifest_skeleton": manifest_skeleton(stage, identity),
                    }
                )
                continue
            entries.append(
                {
                    "ready": True,
                    "manifest_id": resolved.manifest_id,
                    "version": resolved.version,
                    "digest": resolved.digest,
                }
            )
        fallbacks[stage] = entries
    report["stages"] = stages
    if fallbacks:
        report["fallbacks"] = fallbacks
    report["egress"] = _egress_allowlist(bundle, registry, strict)
    if offline:
        from backend.providers.offline_profile import (
            evaluate_offline_profile,
            resolve_for_offline,
        )
        from backend.providers.policy import DEFAULT_POLICY

        profile = evaluate_offline_profile(
            resolve_for_offline(bundle, registry, strict=strict),
            policy or DEFAULT_POLICY,
            artifact_root=_artifact_root(settings),
            require_artifacts=True,
        )
        report["offline_profile"] = profile.document()
        ready = ready and profile.ok
    if host is not None:
        host_report = run_host_checks(settings, bundle, replace(host, offline=offline), probes)
        report["host"] = host_report
        ready = ready and host_report["ok"]
    report["ready"] = ready
    return (0 if ready else 1), report


def _asr_only(settings: Settings) -> SimpleNamespace | None:
    """The ASR adapter alone, for host checks when the full bundle cannot be built."""
    try:
        return SimpleNamespace(asr=create_asr_provider(settings))
    except (ProviderError, ValueError):
        return None


def _egress_allowlist(bundle: ProviderBundle, registry, strict: bool) -> list[dict[str, object]]:
    """The networked endpoints the configured adapters may reach (WP08-T06):
    the source for the egress proxy / firewall allowlist.  Unresolvable
    adapters are left out (they fail preflight anyway)."""
    adapters = [(stage, "primary", getattr(bundle, stage)) for stage in STAGES]
    for stage, fallback_adapters in (getattr(bundle, "fallbacks", None) or {}).items():
        adapters += [(stage, "fallback", adapter) for adapter in fallback_adapters]
    entries: list[dict[str, object]] = []
    for stage, candidate, adapter in adapters:
        try:
            resolved = registry.resolve(stage, adapter.identity(), strict=strict)
        except ManifestError:
            continue
        placement = resolved.placement
        if not placement.networked or placement.endpoint is None:
            continue
        url = urlsplit(placement.endpoint)
        entries.append(
            {
                "stage": stage,
                "candidate": candidate,
                "manifest_id": resolved.manifest_id,
                "endpoint_kind": placement.endpoint_kind.value,
                "region": placement.region,
                "scheme": url.scheme,
                "host": url.hostname,
                "port": url.port or {"http": 80, "https": 443}[url.scheme],
            }
        )
    return entries


EGRESS_CLOUD_REFUSED = 3


def egress_allowlist_lines(report: dict[str, object]) -> list[str]:
    """``host:port  # stage candidate kind`` lines, one per destination."""
    seen: dict[str, list[str]] = {}
    for entry in report.get("egress", []) or []:
        destination = f"{entry['host']}:{entry['port']}"
        seen.setdefault(destination, []).append(
            f"{entry['stage']} {entry['candidate']} {entry['endpoint_kind']}"
        )
    return [f"{dest}  # {'; '.join(notes)}" for dest, notes in sorted(seen.items())]


_HOST_ONLY_FLAGS = (
    "min_free_gpu_mib",
    "min_free_disk_mib",
    "tls_cert",
    "tls_host",
    "tls_min_days",
    "probe_internet",
    "probe_target",
    "topology",
)


def _probe_target(value: str) -> tuple[str, int] | None:
    host, _, port = value.rpartition(":")
    host = host.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host or not port.isdigit() or not 1 <= int(port) <= 65535:
        return None
    return host, int(port)


def _host_options(parser: argparse.ArgumentParser, args: argparse.Namespace) -> HostOptions | None:
    """The ``--host`` options, or ``None``; misuse is a usage error (exit 2)."""
    if not args.host:
        # Identity, not equality: ``0`` is a given value, not "unset".
        given = [
            name
            for name in _HOST_ONLY_FLAGS
            if getattr(args, name) is not None and getattr(args, name) is not False
        ]
        if given:
            parser.error(f"--{given[0].replace('_', '-')} needs --host")
        return None
    if args.egress_allowlist:
        parser.error("--host cannot be combined with --egress-allowlist")
    if args.probe_target and not args.probe_internet:
        parser.error("--probe-target needs --probe-internet")
    for name in ("min_free_gpu_mib", "min_free_disk_mib", "tls_min_days"):
        if (getattr(args, name) or 0) < 0:
            parser.error(f"--{name.replace('_', '-')} cannot be negative")
    targets = DEFAULT_PROBE_TARGETS
    if args.probe_target:
        parsed = [_probe_target(value) for value in args.probe_target]
        if None in parsed:
            parser.error("--probe-target is HOST:PORT with a port from 1 to 65535")
        targets = tuple(parsed)
    topology = args.topology.strip() if args.topology is not None else None
    if topology == "":
        parser.error("--topology needs a topology name")
    return HostOptions(
        min_free_gpu_mib=_default(args.min_free_gpu_mib, DEFAULT_MIN_FREE_GPU_MIB),
        min_free_disk_mib=_default(args.min_free_disk_mib, DEFAULT_MIN_FREE_DISK_MIB),
        tls_cert=args.tls_cert,
        tls_host=args.tls_host,
        tls_min_days=_default(args.tls_min_days, DEFAULT_TLS_MIN_DAYS),
        offline=args.offline,
        probe_internet=args.probe_internet,
        probe_targets=targets,
        topology=topology,
    )


def _default(value: int | None, default: int) -> int:
    return default if value is None else value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.jobs.model_preflight")
    parser.add_argument("--register", action="store_true", help="store resolved manifests")
    parser.add_argument(
        "--egress-allowlist",
        action="store_true",
        help="print the host:port lines for the egress proxy allowlist",
    )
    parser.add_argument(
        "--require-no-cloud",
        action="store_true",
        help="with --egress-allowlist: exit 3 when a cloud endpoint is configured",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="also require deployment profile O (local-only, verified artifacts)",
    )
    parser.add_argument(
        "--host",
        action="store_true",
        help="also check this host: driver, VRAM, disk, ports, TLS, local runtime",
    )
    parser.add_argument(
        "--min-free-gpu-mib",
        type=int,
        metavar="N",
        help=f"with --host: free VRAM the target GPU needs (default {DEFAULT_MIN_FREE_GPU_MIB})",
    )
    parser.add_argument(
        "--min-free-disk-mib",
        type=int,
        metavar="N",
        help=f"with --host: free disk each path needs (default {DEFAULT_MIN_FREE_DISK_MIB})",
    )
    parser.add_argument(
        "--tls-cert", metavar="PATH", help="with --host: PEM certificate file, - for stdin"
    )
    parser.add_argument("--tls-host", metavar="NAME", help="with --host: name the cert must cover")
    parser.add_argument(
        "--tls-min-days",
        type=int,
        metavar="N",
        help=f"with --host: days the cert must stay valid (default {DEFAULT_TLS_MIN_DAYS})",
    )
    parser.add_argument(
        "--probe-internet",
        action="store_true",
        help="with --host: fail when an outbound connection succeeds",
    )
    parser.add_argument(
        "--probe-target",
        action="append",
        metavar="HOST:PORT",
        help="with --probe-internet: replaces the default targets (repeatable)",
    )
    parser.add_argument(
        "--topology",
        metavar="NAME",
        help="with --host: check this supported topology instead of DEPLOYMENT_TOPOLOGY",
    )
    args = parser.parse_args(argv)
    host = _host_options(parser, args)
    settings = get_settings()
    code, report = run_preflight(settings, offline=args.offline, host=host)
    if args.egress_allowlist:
        if code == 2:
            print(json.dumps(report, ensure_ascii=False), file=sys.stderr)
            return 2
        for line in egress_allowlist_lines(report):
            print(line)
        cloud = [e for e in report.get("egress", []) or [] if e["endpoint_kind"] == "cloud"]
        if args.require_no_cloud and cloud:
            hosts = ", ".join(sorted({f"{e['host']}:{e['port']}" for e in cloud}))
            print(f"cloud AI endpoints configured: {hosts}", file=sys.stderr)
            return EGRESS_CLOUD_REFUSED
        return 0
    if code == 0 and args.register:
        from backend.providers.registry import resolve_bundle, resolve_fallbacks
        from backend.services.model_registry_service import register_resolved

        try:
            bundle = create_provider_bundle(settings)
            register_resolved(resolve_bundle(bundle, settings).values())
            for resolved in resolve_fallbacks(bundle, settings).values():
                register_resolved(resolved)
            report["registered"] = True
        except ProviderError as exc:
            report.update(ready=False, error=exc.code)
            code = 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
