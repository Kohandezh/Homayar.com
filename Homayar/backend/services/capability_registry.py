"""The capability registry of HOM-229: the product's modules, in code.

Pure data and pure functions only: no database access, no imports from the rest
of the backend.  Module keys are lowercase ASCII, stable, and part of the API and
of plan entitlements; they are never renamed.  The three readers below implement
the fail-closed rules of ADR 0065
(``docs/architecture/decisions/0065-capability-registry-and-module-enforcement.md``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

CAPABILITY_REGISTRY_VERSION = 1

MODULES_SETTINGS_KEY = "modules"
ENTITLEMENTS_MODULES_KEY = "modules"


@dataclass(frozen=True)
class CapabilityModule:
    key: str
    name: str
    description: str


CAPABILITY_MODULES: tuple[CapabilityModule, ...] = (
    CapabilityModule(
        key="quality",
        name="Quality",
        description="Quality management: scorecards, review items, calibration batches and appeals",
    ),
    CapabilityModule(
        key="insights",
        name="Insights",
        description="Issue trends and the workspace role views over analysed conversations",
    ),
    CapabilityModule(
        key="copilot",
        name="Copilot",
        description="The AI copilot for conversations (no API entry points yet)",
    ),
    CapabilityModule(
        key="voice",
        name="Voice",
        description="The live voice call runtime: admission, sessions and capacity",
    ),
    CapabilityModule(
        key="voicemail",
        name="Voicemail",
        description="Voicemail import, triage and follow-up",
    ),
    CapabilityModule(
        key="integrations",
        name="Integrations",
        description="CRM mapping profiles, inbound records and outbound webhooks",
    ),
)

_MODULES_BY_KEY = {module.key: module for module in CAPABILITY_MODULES}


def lookup_module(key: str) -> CapabilityModule | None:
    """The module for an exact key, or ``None``: ``"Voice"`` and ``" voice"`` are unknown."""
    if not isinstance(key, str):
        return None
    return _MODULES_BY_KEY.get(key)


def parse_disabled_modules(raw: str) -> frozenset[str]:
    """``DISABLED_MODULES``: a comma-separated list of module keys.

    Whitespace around each key is trimmed and empty items are ignored.  An
    unknown key raises ``ValueError`` so a typo cannot silently leave a module
    on; settings validation turns that into a startup failure.
    """
    keys = frozenset(item.strip() for item in raw.split(",") if item.strip())
    unknown = sorted(key for key in keys if key not in _MODULES_BY_KEY)
    if unknown:
        raise ValueError(
            "DISABLED_MODULES holds unknown module key(s): "
            + ", ".join(repr(key) for key in unknown)
            + f" (known: {', '.join(sorted(_MODULES_BY_KEY))})"
        )
    return keys


def licensed_module_keys(entitlements_json: Mapping | None) -> frozenset[str] | None:
    """The licensed module keys of a plan version's entitlements.

    ``None`` means the entitlements license every module (a JSON object
    without a ``"modules"`` key).  Entitlements that are not a JSON object
    (``null``, a list, a string) license nothing, like every other malformed
    value: fail closed.  A present ``"modules"`` key must be a JSON list of
    strings: only the registry keys it names are licensed, unknown strings are
    ignored and an empty list licenses nothing.  Any other shape of the value
    (a string, an object, a list holding a number) also licenses nothing.
    """
    if not isinstance(entitlements_json, Mapping):
        return frozenset()
    if ENTITLEMENTS_MODULES_KEY not in entitlements_json:
        return None
    value = entitlements_json[ENTITLEMENTS_MODULES_KEY]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return frozenset()
    return frozenset(key for key in value if key in _MODULES_BY_KEY)


def admin_module_enabled(settings_json: Mapping | None, key: str) -> bool:
    """The workspace admin switch for one module, read from ``settings_json``.

    No settings at all (``None``), no ``"modules"`` key, or no entry for the
    module: enabled.  An entry is read only through its ``enabled`` JSON bool.
    An unreadable entry (not an object, ``enabled`` missing or not a bool) is
    refused, and a ``"modules"`` value that is not an object refuses every
    module: fail closed.  A ``settings_json`` that is not an object at all (a
    list, a string) refuses every module the same way.
    """
    if settings_json is None:
        return True
    if not isinstance(settings_json, Mapping):
        return False
    if MODULES_SETTINGS_KEY not in settings_json:
        return True
    modules = settings_json[MODULES_SETTINGS_KEY]
    if not isinstance(modules, Mapping):
        return False
    if key not in modules:
        return True
    entry = modules[key]
    if not isinstance(entry, Mapping):
        return False
    enabled = entry.get("enabled")
    if type(enabled) is not bool:
        return False
    return enabled
