"""Model preflight for operators (WP08-T01, ADR 0026).

    python -m backend.jobs.model_preflight [--register]
    python -m backend.jobs.model_preflight --egress-allowlist [--require-no-cloud]

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
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import urlsplit

from backend.config import Settings, get_settings
from backend.providers.artifacts import verify_artifacts
from backend.providers.base import ProviderError
from backend.providers.factory import ProviderBundle, create_provider_bundle
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
) -> tuple[int, dict[str, object]]:
    try:
        bundle = providers or create_provider_bundle(settings)
        registry = load_registry(settings)
        pins = parse_pins(settings.model_manifest_pins)
    except (ProviderError, ManifestError, ValueError) as exc:
        return 2, {"ready": False, "error": getattr(exc, "code", "configuration_error")}
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
    report["ready"] = ready
    return (0 if ready else 1), report


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
    args = parser.parse_args(argv)
    settings = get_settings()
    code, report = run_preflight(settings, offline=args.offline)
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
