"""The frozen formats of the promotion gate (HOM-71).

Shared validators for ``release-criteria/1``, ``rollback-manifest/1`` and the promotion
request, plus the small helpers both the gate (``promotion``) and the corrections store
(``corrections``) use.  Pure: no file input or output here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.evaluation.loaders import _check_finite

RELEASE_CRITERIA_VERSION = "release-criteria/1"
ROLLBACK_MANIFEST_VERSION = "rollback-manifest/1"
PROMOTION_DECISION_VERSION = "promotion-decision/1"

_ISO_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class PolicyRefused(Exception):
    """A refused action (exit 2): a blocked promotion, a stale incumbent, no rollback."""


def _dumps(doc: Any) -> str:
    return json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _required(record: Mapping[str, Any], key: str) -> Any:
    if key not in record:
        raise ValueError(f"missing required key {key!r}")
    return record[key]


def _non_blank(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"key {key!r} must be a non-blank string, got {value!r}")
    return value


def _keys_exactly(record: Mapping[str, Any], allowed: set[str], what: str) -> None:
    for key in record:
        if key not in allowed:
            raise ValueError(f"unknown key {key!r} in {what}")


def _iso_utc(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _ISO_UTC.fullmatch(value):
        raise ValueError(
            f"key {key!r} must be an ISO UTC string YYYY-MM-DDTHH:MM:SSZ, got {value!r}"
        )
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ValueError(f"key {key!r} is not a valid UTC datetime: {value!r}") from None
    return value


def _sha_hex(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"key {key!r} must be 64 lowercase hex characters, got {value!r}")
    return value


def _opt_sha_hex(value: Any, key: str) -> str | None:
    if value is None:
        return None
    return _sha_hex(value, key)


def _non_negative_number(value: Any, key: str) -> float:
    if not _is_number(value) or value < 0:
        raise ValueError(f"key {key!r} must be a number >= 0, got {value!r}")
    return value


def _opt_non_negative_number(value: Any, key: str) -> float | None:
    if value is None:
        return None
    return _non_negative_number(value, key)


def _str_list(value: Any, key: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"key {key!r} must be a non-empty list, got {value!r}")
    return [_non_blank(item, f"{key}[{i}]") for i, item in enumerate(value)]


@dataclass(frozen=True)
class Gate:
    """One held-out gate: the metric value of ``slice`` must clear ``threshold``."""

    evaluation: str
    metric: str
    slice: str
    direction: str
    threshold: float
    n_items: int
    max_regression: float | None
    tolerance: float | None


@dataclass(frozen=True)
class MeasurementsCriteria:
    required: bool
    slice: str
    max_p95_latency_ms: float | None
    max_mean_cost_per_request: float | None
    currency: str | None
    single_provider: bool


@dataclass(frozen=True)
class ReleaseCriteria:
    criteria_id: str
    frozen_at: str
    frozen_by: str
    evaluation_version: str
    default_tolerance: float
    min_reproductions: int
    approver_roles: tuple[str, ...]
    gates: tuple[Gate, ...]
    measurements: MeasurementsCriteria


@dataclass(frozen=True)
class Approval:
    approved_by: str
    role: str
    approved_at: str


@dataclass(frozen=True)
class RollbackManifest:
    promoted_version: str
    promoted_config_sha256: str | None
    previous_version: str | None
    previous_config_sha256: str | None
    previous_config: dict[str, Any] | None
    candidate_report_sha256: str
    incumbent_report_sha256: str | None
    criteria_sha256: str
    triggers: tuple[Mapping[str, Any], ...]
    owner_role: str
    verification_steps: tuple[str, ...]
    approval: Approval | None


@dataclass(frozen=True)
class PromotionRequest:
    candidate_report: str
    incumbent_report: str | None
    reproduction_reports: tuple[str, ...]
    criteria: str
    criteria_sha256: str
    rollback_manifest: str | None


def _parse_gate(data: Any, index: int) -> Gate:
    where = f"gates[{index}]"
    if not isinstance(data, dict):
        raise ValueError(f"key '{where}' must be an object, got {data!r}")
    _keys_exactly(
        data,
        {
            "evaluation",
            "metric",
            "slice",
            "direction",
            "threshold",
            "n_items",
            "max_regression",
            "tolerance",
        },  # fmt: skip
        where,
    )
    evaluation = _non_blank(_required(data, "evaluation"), f"{where}.evaluation")
    metric = _non_blank(_required(data, "metric"), f"{where}.metric")
    slice_key = _non_blank(_required(data, "slice"), f"{where}.slice")
    direction = _required(data, "direction")
    if direction not in ("min", "max"):
        raise ValueError(f"key '{where}.direction' must be 'min' or 'max', got {direction!r}")
    threshold = _required(data, "threshold")
    if not _is_number(threshold):
        raise ValueError(f"key '{where}.threshold' must be a number, got {threshold!r}")
    n_items = _required(data, "n_items")
    if not _is_int(n_items) or n_items < 1:
        raise ValueError(f"key '{where}.n_items' must be an int >= 1, got {n_items!r}")
    max_regression = _opt_non_negative_number(data.get("max_regression"), f"{where}.max_regression")
    tolerance = _opt_non_negative_number(data.get("tolerance"), f"{where}.tolerance")
    return Gate(evaluation, metric, slice_key, direction, threshold, n_items, max_regression,
                tolerance)  # fmt: skip


def _parse_measurements(data: Any) -> MeasurementsCriteria:
    if not isinstance(data, dict):
        raise ValueError(f"key 'measurements' must be an object, got {data!r}")
    _keys_exactly(
        data,
        {
            "required",
            "slice",
            "max_p95_latency_ms",
            "max_mean_cost_per_request",
            "currency",
            "single_provider",
        },  # fmt: skip
        "measurements",
    )
    required = _required(data, "required")
    if not isinstance(required, bool):
        raise ValueError(f"key 'measurements.required' must be a boolean, got {required!r}")
    slice_key = _non_blank(_required(data, "slice"), "measurements.slice")
    max_p95 = _opt_non_negative_number(
        _required(data, "max_p95_latency_ms"), "measurements.max_p95_latency_ms"
    )
    max_cost = _opt_non_negative_number(
        _required(data, "max_mean_cost_per_request"), "measurements.max_mean_cost_per_request"
    )
    currency = _required(data, "currency")
    if currency is not None:
        currency = _non_blank(currency, "measurements.currency")
    if max_cost is not None and currency is None:
        raise ValueError(
            "key 'measurements.currency' must be a non-null string when "
            "'max_mean_cost_per_request' is not null"
        )
    single_provider = _required(data, "single_provider")
    if not isinstance(single_provider, bool):
        raise ValueError(
            f"key 'measurements.single_provider' must be a boolean, got {single_provider!r}"
        )
    return MeasurementsCriteria(required, slice_key, max_p95, max_cost, currency, single_provider)


def parse_release_criteria(data: Any) -> ReleaseCriteria:
    """Read a ``release-criteria/1`` document; unknown keys and wrong shapes raise."""
    if not isinstance(data, dict):
        raise ValueError(f"the release criteria must be a JSON object, got {type(data).__name__}")
    _check_finite(data)
    _keys_exactly(
        data,
        {
            "version",
            "criteria_id",
            "frozen_at",
            "frozen_by",
            "evaluation_version",
            "default_tolerance",
            "min_reproductions",
            "approver_roles",
            "gates",
            "measurements",
        },
        "release criteria",  # fmt: skip
    )
    if _required(data, "version") != RELEASE_CRITERIA_VERSION:
        raise ValueError(f"key 'version' must be {RELEASE_CRITERIA_VERSION!r}, "
                         f"got {data['version']!r}")  # fmt: skip
    criteria_id = _non_blank(_required(data, "criteria_id"), "criteria_id")
    frozen_at = _iso_utc(_required(data, "frozen_at"), "frozen_at")
    frozen_by = _non_blank(_required(data, "frozen_by"), "frozen_by")
    evaluation_version = _non_blank(_required(data, "evaluation_version"), "evaluation_version")
    default_tolerance = _non_negative_number(
        _required(data, "default_tolerance"), "default_tolerance"
    )
    min_reproductions = _required(data, "min_reproductions")
    if not _is_int(min_reproductions) or min_reproductions < 1:
        raise ValueError(f"key 'min_reproductions' must be an int >= 1, got {min_reproductions!r}")
    approver_roles = tuple(_str_list(_required(data, "approver_roles"), "approver_roles"))
    raw_gates = _required(data, "gates")
    if not isinstance(raw_gates, list) or not raw_gates:
        raise ValueError(f"key 'gates' must be a non-empty list, got {raw_gates!r}")
    gates = tuple(_parse_gate(gate, i) for i, gate in enumerate(raw_gates))
    seen: set[tuple[str, str, str]] = set()
    for gate in gates:
        key = (gate.evaluation, gate.metric, gate.slice)
        if key in seen:
            raise ValueError(f"two gates in 'gates' share {key}")
        seen.add(key)
    measurements = _parse_measurements(_required(data, "measurements"))
    return ReleaseCriteria(criteria_id, frozen_at, frozen_by, evaluation_version,
                           default_tolerance, min_reproductions, approver_roles, gates,
                           measurements)  # fmt: skip


def _parse_approval(data: Any) -> Approval | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError(f"key 'approval' must be an object or null, got {data!r}")
    _keys_exactly(data, {"approved_by", "role", "approved_at"}, "approval")
    approved_by = _non_blank(_required(data, "approved_by"), "approval.approved_by")
    role = _non_blank(_required(data, "role"), "approval.role")
    approved_at = _iso_utc(_required(data, "approved_at"), "approval.approved_at")
    return Approval(approved_by, role, approved_at)


def parse_rollback_manifest(data: Any) -> RollbackManifest:
    """Read a ``rollback-manifest/1`` document; unknown keys and wrong shapes raise."""
    if not isinstance(data, dict):
        raise ValueError(f"the rollback manifest must be a JSON object, got {type(data).__name__}")
    _check_finite(data)
    _keys_exactly(
        data,
        {
            "version",
            "promoted_version",
            "promoted_config_sha256",
            "previous_version",
            "previous_config_sha256",
            "previous_config",
            "candidate_report_sha256",
            "incumbent_report_sha256",
            "criteria_sha256",
            "triggers",
            "owner_role",
            "verification_steps",
            "approval",
        },  # fmt: skip
        "rollback manifest",
    )
    if _required(data, "version") != ROLLBACK_MANIFEST_VERSION:
        raise ValueError(f"key 'version' must be {ROLLBACK_MANIFEST_VERSION!r}, "
                         f"got {data['version']!r}")  # fmt: skip
    promoted_version = _non_blank(_required(data, "promoted_version"), "promoted_version")
    promoted_config_sha256 = _opt_sha_hex(
        _required(data, "promoted_config_sha256"), "promoted_config_sha256"
    )
    previous_version = _required(data, "previous_version")
    if previous_version is not None:
        previous_version = _non_blank(previous_version, "previous_version")
    previous_config_sha256 = _opt_sha_hex(
        _required(data, "previous_config_sha256"), "previous_config_sha256"
    )
    previous_config = _required(data, "previous_config")
    if previous_config is not None and not isinstance(previous_config, dict):
        raise ValueError(
            f"key 'previous_config' must be an object or null, got {previous_config!r}"
        )
    candidate_report_sha256 = _sha_hex(
        _required(data, "candidate_report_sha256"), "candidate_report_sha256"
    )
    incumbent_report_sha256 = _opt_sha_hex(
        _required(data, "incumbent_report_sha256"), "incumbent_report_sha256"
    )
    criteria_sha256 = _sha_hex(_required(data, "criteria_sha256"), "criteria_sha256")
    raw_triggers = _required(data, "triggers")
    if not isinstance(raw_triggers, list) or not raw_triggers:
        raise ValueError(f"key 'triggers' must be a non-empty list, got {raw_triggers!r}")
    triggers: list[Mapping[str, Any]] = []
    for i, trigger in enumerate(raw_triggers):
        where = f"triggers[{i}]"
        if not isinstance(trigger, dict):
            raise ValueError(f"key '{where}' must be an object, got {trigger!r}")
        _keys_exactly(trigger, {"evaluation", "metric", "slice", "direction", "threshold"}, where)
        evaluation = _non_blank(_required(trigger, "evaluation"), f"{where}.evaluation")
        metric = _non_blank(_required(trigger, "metric"), f"{where}.metric")
        slice_key = _non_blank(_required(trigger, "slice"), f"{where}.slice")
        direction = _required(trigger, "direction")
        if direction not in ("min", "max"):
            raise ValueError(f"key '{where}.direction' must be 'min' or 'max', got {direction!r}")
        threshold = _required(trigger, "threshold")
        if not _is_number(threshold):
            raise ValueError(f"key '{where}.threshold' must be a number, got {threshold!r}")
        triggers.append(
            {
                "evaluation": evaluation,
                "metric": metric,
                "slice": slice_key,
                "direction": direction,
                "threshold": threshold,
            }  # fmt: skip
        )
    owner_role = _non_blank(_required(data, "owner_role"), "owner_role")
    verification_steps = tuple(
        _str_list(_required(data, "verification_steps"), "verification_steps")
    )
    approval = _parse_approval(_required(data, "approval"))
    return RollbackManifest(promoted_version, promoted_config_sha256, previous_version,
                            previous_config_sha256, previous_config, candidate_report_sha256,
                            incumbent_report_sha256, criteria_sha256, tuple(triggers),
                            owner_role, verification_steps, approval)  # fmt: skip


def parse_promotion_request(data: Any) -> PromotionRequest:
    """Read a promotion request; paths stay as written and resolve against its directory."""
    if not isinstance(data, dict):
        raise ValueError(f"the promotion request must be a JSON object, got {type(data).__name__}")
    _keys_exactly(
        data,
        {
            "candidate_report",
            "incumbent_report",
            "reproduction_reports",
            "criteria",
            "criteria_sha256",
            "rollback_manifest",
        },  # fmt: skip
        "promotion request",
    )
    candidate_report = _non_blank(_required(data, "candidate_report"), "candidate_report")
    incumbent_report = _required(data, "incumbent_report")
    if incumbent_report is not None:
        incumbent_report = _non_blank(incumbent_report, "incumbent_report")
    raw_reproductions = _required(data, "reproduction_reports")
    if not isinstance(raw_reproductions, list):
        raise ValueError(
            f"key 'reproduction_reports' must be a list of strings, got {raw_reproductions!r}"
        )
    reproduction_reports = tuple(
        _non_blank(p, f"reproduction_reports[{i}]") for i, p in enumerate(raw_reproductions)
    )
    criteria = _non_blank(_required(data, "criteria"), "criteria")
    criteria_sha256 = _sha_hex(_required(data, "criteria_sha256"), "criteria_sha256")
    rollback_manifest = _required(data, "rollback_manifest")
    if rollback_manifest is not None:
        rollback_manifest = _non_blank(rollback_manifest, "rollback_manifest")
    return PromotionRequest(candidate_report, incumbent_report, reproduction_reports, criteria,
                            criteria_sha256, rollback_manifest)  # fmt: skip
