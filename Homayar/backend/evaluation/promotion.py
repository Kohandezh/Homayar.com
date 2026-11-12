"""The promotion gate: frozen release criteria, the promotion decision and the ledger (HOM-71).

``python -m backend.evaluation.promotion check <request.json>`` reads a candidate report,
an optional incumbent report, repeated-run reports, a frozen release-criteria file and a
rollback manifest, then prints a ``promotion-decision/1`` document: ``promote`` when every
check passes, else ``blocked`` with every reason.  ``record`` appends a promotion to the
append-only JSONL ledger only after the gate says ``promote``; ``rollback`` restores the
previous version named in the last promotion's manifest; ``active`` prints the version in
effect.  Pure and deterministic like the rest of the package; the CLI stamps
``datetime.now(UTC)``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.evaluation.candidate import CANDIDATE_REPORT_VERSION, config_sha256
from backend.evaluation.loaders import _check_finite
from backend.evaluation.promotion_formats import (
    PROMOTION_DECISION_VERSION,
    Gate,
    PolicyRefused,
    RollbackManifest,
    _dumps,
    _is_int,
    _is_number,
    _iso_utc,
    _keys_exactly,
    _non_blank,
    _required,
    _sha_hex,
    parse_promotion_request,
    parse_release_criteria,
    parse_rollback_manifest,
)


def _read_json_file(path: str) -> tuple[Any, bytes]:
    with open(path, "rb") as fh:
        raw = fh.read()
    return json.loads(raw.decode("utf-8")), raw


_REPORT_KEYS = ("version", "evaluation_version", "candidate", "config_sha256", "evaluations")


def _validate_report(doc: Any, role: str, path: str, needs_measurements: bool) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise ValueError(f"the {role} report {path!r} must be a JSON object")
    _check_finite(doc)
    for key in _REPORT_KEYS:
        if key not in doc:
            raise ValueError(f"the {role} report is missing the required key {key!r}")
    if not isinstance(doc["candidate"], str):
        raise ValueError(f"the {role} report key 'candidate' must be a string")
    if doc["config_sha256"] is not None and not isinstance(doc["config_sha256"], str):
        raise ValueError(f"the {role} report key 'config_sha256' must be a string or null")
    evaluations = doc["evaluations"]
    if not isinstance(evaluations, dict) or not all(
        isinstance(entry, dict) for entry in evaluations.values()
    ):
        raise ValueError(f"the {role} report key 'evaluations' must map names to objects")
    if needs_measurements:
        if "measurements" not in doc:
            raise ValueError("the candidate report is missing the required key 'measurements'")
        measurements = doc["measurements"]
        if not isinstance(measurements, dict):
            raise ValueError("the candidate report key 'measurements' must be an object")
        if "reason" not in measurements:
            raise ValueError("the candidate report key 'measurements' is missing 'reason'")
        if measurements.get("reason") != "not_measured" and not isinstance(
            measurements.get("slices"), dict
        ):
            raise ValueError("the candidate report measurements must map 'slices' to an object")
    return doc


@dataclass(frozen=True)
class _GateView:
    value: float | None
    n_input_items: int
    insufficient_sample: bool
    missing: str | None  # why the value is unavailable: metric absent, value is null


def _gate_view(report: Mapping[str, Any], gate: Gate, role: str) -> _GateView | None:
    evaluation = report["evaluations"].get(gate.evaluation)
    if not isinstance(evaluation, dict):
        return None
    slices = evaluation.get("slices")
    if slices is None:
        return None
    if not isinstance(slices, dict):
        raise ValueError(
            f"the {role} evaluation {gate.evaluation!r} key 'slices' must be an object"
        )
    entry = slices.get(gate.slice)
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise ValueError(f"the {role} slice {gate.evaluation}/{gate.slice} must be an object")
    n_input_items = entry.get("n_input_items")
    if not _is_int(n_input_items):
        raise ValueError(
            f"the {role} slice {gate.evaluation}/{gate.slice} key 'n_input_items' must be an int"
        )
    insufficient = entry.get("insufficient_sample")
    if not isinstance(insufficient, bool):
        raise ValueError(
            f"the {role} slice {gate.evaluation}/{gate.slice} key 'insufficient_sample' "
            "must be a boolean"
        )
    metrics = entry.get("metrics")
    if metrics is not None and not isinstance(metrics, dict):
        raise ValueError(
            f"the {role} slice {gate.evaluation}/{gate.slice} key 'metrics' must be an object"
        )
    value: float | None = None
    missing: str | None = None
    metric = metrics.get(gate.metric) if metrics is not None else None
    if metric is None:
        missing = "metric absent"
    elif not isinstance(metric, dict):
        raise ValueError(
            f"the {role} metric {gate.evaluation}/{gate.metric}/{gate.slice} must be an object"
        )
    else:
        raw_value = metric.get("value")
        if raw_value is None:
            missing = "value is null"
        elif not _is_number(raw_value):
            raise ValueError(
                f"the {role} metric value {gate.evaluation}/{gate.metric}/{gate.slice} "
                "must be a number or null"
            )
        else:
            value = raw_value
    return _GateView(value, n_input_items, insufficient, missing)


@dataclass(frozen=True)
class _CheckResult:
    decision: dict[str, Any]
    manifest_doc: dict[str, Any] | None
    criteria_sha256: str
    decision_text: str
    incumbent_candidate: str | None
    incumbent_config_sha256: str | None


def _run_checks(request_path: str) -> _CheckResult:
    raw_request, _ = _read_json_file(request_path)
    request = parse_promotion_request(raw_request)
    base = os.path.dirname(os.path.abspath(request_path))

    def full(written: str) -> str:
        return written if os.path.isabs(written) else os.path.join(base, written)

    criteria_doc, criteria_bytes = _read_json_file(full(request.criteria))
    criteria = parse_release_criteria(criteria_doc)
    criteria_sha = hashlib.sha256(criteria_bytes).hexdigest()
    candidate_doc, candidate_bytes = _read_json_file(full(request.candidate_report))
    candidate = _validate_report(candidate_doc, "candidate", full(request.candidate_report), True)
    candidate_sha = hashlib.sha256(candidate_bytes).hexdigest()
    incumbent: dict[str, Any] | None = None
    incumbent_sha: str | None = None
    if request.incumbent_report is not None:
        incumbent_doc, incumbent_bytes = _read_json_file(full(request.incumbent_report))
        incumbent = _validate_report(
            incumbent_doc, "incumbent", full(request.incumbent_report), False
        )
        incumbent_sha = hashlib.sha256(incumbent_bytes).hexdigest()
    reproductions: list[dict[str, Any]] = []
    for written in request.reproduction_reports:
        doc, _ = _read_json_file(full(written))
        reproductions.append(_validate_report(doc, "reproduction", full(written), False))
    manifest: RollbackManifest | None = None
    manifest_doc: dict[str, Any] | None = None
    manifest_sha: str | None = None
    manifest_invalid: str | None = None
    if request.rollback_manifest is not None:
        try:
            manifest_doc, manifest_bytes = _read_json_file(full(request.rollback_manifest))
            manifest = parse_rollback_manifest(manifest_doc)
            manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        except ValueError as exc:
            manifest_invalid = str(exc)

    reasons: list[dict[str, str]] = []

    def add(code: str, detail: str) -> None:
        reasons.append({"code": code, "detail": detail})

    if criteria_sha != request.criteria_sha256:
        add("criteria_changed",
            f"{criteria.criteria_id}: file sha256 {criteria_sha} != pinned "
            f"{request.criteria_sha256}")  # fmt: skip
    named: list[tuple[str, dict[str, Any]]] = [("candidate", candidate)]
    if incumbent is not None:
        named.append(("incumbent", incumbent))
    named.extend((f"reproduction {i}", doc) for i, doc in enumerate(reproductions))
    for name, report in named:
        if report["version"] != CANDIDATE_REPORT_VERSION:
            add(
                "report_version_mismatch",
                f"{name}: version {report['version']!r} != {CANDIDATE_REPORT_VERSION!r}",
            )
        if report["evaluation_version"] != criteria.evaluation_version:
            add("evaluation_version_mismatch",
                f"{name}: evaluation_version {report['evaluation_version']!r} != "
                f"{criteria.evaluation_version!r}")  # fmt: skip
    if (
        incumbent is not None
        and incumbent["candidate"] == candidate["candidate"]
        and incumbent["config_sha256"] == candidate["config_sha256"]
    ):
        add("no_change", f"the incumbent is the candidate: {candidate['candidate']!r}")
    if len(reproductions) < criteria.min_reproductions:
        add("reproduction_missing", f"{len(reproductions)} < {criteria.min_reproductions}")
    for i, report in enumerate(reproductions):
        if report["candidate"] != candidate["candidate"]:
            add("reproduction_mismatch",
                f"reproduction {i}: candidate {report['candidate']!r} != "
                f"{candidate['candidate']!r}")  # fmt: skip
        if report["config_sha256"] != candidate["config_sha256"]:
            add("reproduction_mismatch",
                f"reproduction {i}: config_sha256 {report['config_sha256']!r} != "
                f"{candidate['config_sha256']!r}")  # fmt: skip

    gates_out: list[dict[str, Any]] = []
    for gate in criteria.gates:
        codes: list[str] = []
        where = f"{gate.evaluation}/{gate.metric}/{gate.slice}"

        def gadd(code: str, detail: str, _codes: list[str] = codes) -> None:
            _codes.append(code)
            add(code, detail)

        tolerance = gate.tolerance if gate.tolerance is not None else criteria.default_tolerance
        view = _gate_view(candidate, gate, "candidate")
        candidate_value: float | None = None
        if view is None:
            gadd("gate_missing", f"{where}: evaluation or slice absent")
        else:
            candidate_value = view.value
            if view.missing is not None:
                gadd("gate_missing", f"{where}: {view.missing}")
            elif gate.direction == "max" and candidate_value > gate.threshold:
                gadd("threshold_missed", f"{where}: {candidate_value!r} > {gate.threshold!r}")
            elif gate.direction == "min" and candidate_value < gate.threshold:
                gadd("threshold_missed", f"{where}: {candidate_value!r} < {gate.threshold!r}")
            if view.n_input_items != gate.n_items:
                gadd(
                    "held_out_size_mismatch",
                    f"{where}: n_input_items {view.n_input_items} != {gate.n_items}",
                )
            if view.insufficient_sample:
                gadd("insufficient_sample", f"{where}: insufficient_sample is true")
        incumbent_value: float | None = None
        if incumbent is not None:
            inc_view = _gate_view(incumbent, gate, "incumbent")
            if inc_view is None:
                gadd("incumbent_gate_missing", f"incumbent {where}: evaluation or slice absent")
            else:
                incumbent_value = inc_view.value
                if inc_view.missing is not None:
                    gadd("incumbent_gate_missing", f"incumbent {where}: {inc_view.missing}")
                if inc_view.n_input_items != gate.n_items:
                    gadd("held_out_size_mismatch",
                         f"incumbent {where}: n_input_items {inc_view.n_input_items} != "
                         f"{gate.n_items}")  # fmt: skip
                if (
                    gate.max_regression is not None
                    and candidate_value is not None
                    and incumbent_value is not None
                ):
                    if (
                        gate.direction == "max"
                        and candidate_value > incumbent_value + gate.max_regression
                    ):
                        gadd("regression",
                             f"{where}: {candidate_value!r} > {incumbent_value!r} + "
                             f"{gate.max_regression!r}")  # fmt: skip
                    elif (
                        gate.direction == "min"
                        and candidate_value < incumbent_value - gate.max_regression
                    ):
                        gadd("regression",
                             f"{where}: {candidate_value!r} < {incumbent_value!r} - "
                             f"{gate.max_regression!r}")  # fmt: skip
        reproduction_values: list[float | None] = []
        for i, report in enumerate(reproductions):
            r_view = _gate_view(report, gate, f"reproduction {i}")
            value: float | None = r_view.value if r_view is not None else None
            if r_view is None:
                gadd("not_reproducible", f"reproduction {i} {where}: evaluation or slice absent")
            elif r_view.missing is not None:
                gadd("not_reproducible", f"reproduction {i} {where}: {r_view.missing}")
            elif r_view.n_input_items != gate.n_items:
                gadd("not_reproducible",
                     f"reproduction {i} {where}: n_input_items {r_view.n_input_items} != "
                     f"{gate.n_items}")  # fmt: skip
            elif candidate_value is not None and abs(r_view.value - candidate_value) > tolerance:
                gadd("not_reproducible",
                     f"reproduction {i} {where}: |{r_view.value!r} - {candidate_value!r}| = "
                     f"{abs(r_view.value - candidate_value)!r} > {tolerance!r}")  # fmt: skip
            reproduction_values.append(value)
        gates_out.append(
            {
                "evaluation": gate.evaluation,
                "metric": gate.metric,
                "slice": gate.slice,
                "direction": gate.direction,
                "threshold": gate.threshold,
                "n_items": gate.n_items,
                "tolerance": tolerance,
                "max_regression": gate.max_regression,
                "candidate_value": candidate_value,
                "incumbent_value": incumbent_value,
                "reproduction_values": reproduction_values,
                "passed": not codes,
                "reasons": sorted(codes),
            }
        )

    mc = criteria.measurements
    measurements = candidate["measurements"]
    entry: dict[str, Any] | None = None
    if measurements.get("reason") == "not_measured":
        if mc.required:
            add("measurements_missing", f"measurements/{mc.slice}: not_measured")
    else:
        raw_entry = measurements["slices"].get(mc.slice)
        if raw_entry is None:
            if mc.required:
                add("measurements_missing", f"measurements/{mc.slice}: slice absent")
        elif not isinstance(raw_entry, dict):
            raise ValueError(
                f"the candidate report measurement slice {mc.slice!r} must be an object"
            )
        else:
            entry = raw_entry
    if entry is not None:
        where = f"measurements/{mc.slice}"
        latency = entry.get("latency_ms")
        if not isinstance(latency, dict):
            raise ValueError(f"{where}: 'latency_ms' must be an object")
        cost = entry.get("cost")
        if not isinstance(cost, dict):
            raise ValueError(f"{where}: 'cost' must be an object")
        providers = entry.get("providers")
        if not isinstance(providers, dict):
            raise ValueError(f"{where}: 'providers' must be an object")
        n_missing_provider = entry.get("n_missing_provider")
        if not _is_int(n_missing_provider):
            raise ValueError(f"{where}: 'n_missing_provider' must be an int")
        if mc.max_p95_latency_ms is not None:
            p95 = latency.get("p95")
            if p95 is not None and not _is_number(p95):
                raise ValueError(f"{where}: 'latency_ms.p95' must be a number or null")
            if p95 is None:
                add("latency_over", f"{where}: p95 is null")
            elif p95 > mc.max_p95_latency_ms:
                add("latency_over", f"{where}: p95 {p95!r} > {mc.max_p95_latency_ms!r}")
        if mc.max_mean_cost_per_request is not None:
            if measurements.get("currency") != mc.currency:
                add(
                    "currency_mismatch",
                    f"{where}: currency {measurements.get('currency')!r} != {mc.currency!r}",
                )
            else:
                mean = cost.get("mean_per_request")
                if mean is not None and not _is_number(mean):
                    raise ValueError(f"{where}: 'cost.mean_per_request' must be a number or null")
                if mean is None:
                    add("cost_over", f"{where}: mean_per_request is null")
                elif mean > mc.max_mean_cost_per_request:
                    add("cost_over",
                        f"{where}: mean_per_request {mean!r} > "
                        f"{mc.max_mean_cost_per_request!r}")  # fmt: skip
        if mc.single_provider and (len(providers) > 1 or n_missing_provider > 0):
            if len(providers) > 1:
                add("provider_mixing", f"{where}: providers {sorted(providers)}")
            else:
                add("provider_mixing", f"{where}: n_missing_provider {n_missing_provider} > 0")

    if request.rollback_manifest is None:
        add("rollback_manifest_missing", "rollback_manifest is null")
    elif manifest is None:
        add("rollback_manifest_invalid", manifest_invalid or "rollback manifest is invalid")
    else:

        def field(name: str, got: Any, expected: Any) -> None:
            if got != expected:
                add("rollback_manifest_mismatch", f"{name}: {got!r} != {expected!r}")

        field("promoted_version", manifest.promoted_version, candidate["candidate"])
        field("promoted_config_sha256", manifest.promoted_config_sha256, candidate["config_sha256"])
        if incumbent is not None:
            field("previous_version", manifest.previous_version, incumbent["candidate"])
            field(
                "previous_config_sha256",
                manifest.previous_config_sha256,
                incumbent["config_sha256"],
            )
        else:
            field("previous_version", manifest.previous_version, None)
            field("previous_config_sha256", manifest.previous_config_sha256, None)
        field(
            "previous_config",
            config_sha256(manifest.previous_config),
            manifest.previous_config_sha256,
        )
        field("candidate_report_sha256", manifest.candidate_report_sha256, candidate_sha)
        field("incumbent_report_sha256", manifest.incumbent_report_sha256, incumbent_sha)
        field("criteria_sha256", manifest.criteria_sha256, criteria_sha)
        if manifest.approval is None:
            add("rollback_manifest_not_approved", "approval is null")
        elif manifest.approval.role not in criteria.approver_roles:
            add("rollback_manifest_not_approved",
                f"approval role {manifest.approval.role!r} not in "
                f"{sorted(criteria.approver_roles)}")  # fmt: skip

    top = sorted(reasons, key=lambda reason: (reason["code"], reason["detail"]))
    decision = {
        "version": PROMOTION_DECISION_VERSION,
        "decision": "promote" if not top else "blocked",
        "criteria_id": criteria.criteria_id,
        "criteria_sha256": criteria_sha,
        "candidate": candidate["candidate"],
        "candidate_config_sha256": candidate["config_sha256"],
        "candidate_report_sha256": candidate_sha,
        "incumbent": incumbent["candidate"] if incumbent is not None else None,
        "incumbent_config_sha256": incumbent["config_sha256"] if incumbent is not None else None,
        "incumbent_report_sha256": incumbent_sha,
        "rollback_manifest_sha256": manifest_sha,
        "gates": gates_out,
        "reasons": top,
    }
    return _CheckResult(decision, manifest_doc, criteria_sha, _dumps(decision),
                        incumbent["candidate"] if incumbent is not None else None,
                        incumbent["config_sha256"] if incumbent is not None else None)  # fmt: skip


def check_promotion(request_path: str | os.PathLike[str]) -> dict[str, Any]:
    """The promotion decision of a request file (SPEC 3.4), as a JSON-safe dict."""
    return _run_checks(os.fspath(request_path)).decision


_PROMOTION_ENTRY_KEYS = {"type", "sequence", "at", "version", "config_sha256",
                         "criteria_sha256", "candidate_report_sha256", "decision_sha256",
                         "rollback_manifest"}  # fmt: skip
_ROLLBACK_ENTRY_KEYS = {
    "type",
    "sequence",
    "at",
    "undoes_sequence",
    "from_version",
    "to_version",
    "to_config_sha256",
    "to_config",
    "reason",
    "by",
}


def _iso_now(now: datetime) -> str:
    aware = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_ledger_entry(data: Any, expected_sequence: int) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(f"a ledger entry must be a JSON object, got {type(data).__name__}")
    kind = data.get("type")
    if kind not in ("promotion", "rollback"):
        raise ValueError(f"unknown ledger entry type {kind!r}")
    _keys_exactly(data, _PROMOTION_ENTRY_KEYS if kind == "promotion" else _ROLLBACK_ENTRY_KEYS,
                  f"a {kind} entry")  # fmt: skip
    sequence = _required(data, "sequence")
    if not _is_int(sequence) or sequence != expected_sequence:
        raise ValueError(
            f"the sequence of entry {expected_sequence} must be the int {expected_sequence}, "
            f"got {sequence!r}"
        )
    _iso_utc(_required(data, "at"), "at")
    if kind == "promotion":
        _non_blank(_required(data, "version"), "version")
        if _required(data, "config_sha256") is not None:
            _sha_hex(data["config_sha256"], "config_sha256")
        _sha_hex(_required(data, "criteria_sha256"), "criteria_sha256")
        _sha_hex(_required(data, "candidate_report_sha256"), "candidate_report_sha256")
        _sha_hex(_required(data, "decision_sha256"), "decision_sha256")
        manifest = _required(data, "rollback_manifest")
        parse_rollback_manifest(manifest)
        return data
    undoes = _required(data, "undoes_sequence")
    if not _is_int(undoes) or undoes < 1:
        raise ValueError(f"key 'undoes_sequence' must be an int >= 1, got {undoes!r}")
    _non_blank(_required(data, "from_version"), "from_version")
    if _required(data, "to_version") is not None:
        _non_blank(data["to_version"], "to_version")
    if _required(data, "to_config_sha256") is not None:
        _sha_hex(data["to_config_sha256"], "to_config_sha256")
    if _required(data, "to_config") is not None and not isinstance(data["to_config"], dict):
        raise ValueError(f"key 'to_config' must be an object or null, got {data['to_config']!r}")
    _non_blank(_required(data, "reason"), "reason")
    _non_blank(_required(data, "by"), "by")
    return data


def read_ledger(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Read and validate a promotion ledger; a malformed line is an input error."""
    entries: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    with open(path, "rb") as fh:
        for n, raw in enumerate(fh, 1):
            try:
                line = raw.decode("utf-8")
                if not line.strip():
                    continue
                entry = _validate_ledger_entry(json.loads(line), len(entries) + 1)
                if entry["type"] == "promotion":
                    stack.append(entry)
                elif not stack:
                    raise ValueError("a rollback entry with no promotion to undo")
                elif entry["undoes_sequence"] != stack[-1]["sequence"]:
                    raise ValueError(
                        f"the rollback undoes_sequence {entry['undoes_sequence']!r} != "
                        f"the top promotion's sequence {stack[-1]['sequence']!r}"
                    )
                elif entry["from_version"] != stack[-1]["version"]:
                    raise ValueError(
                        f"the rollback from_version {entry['from_version']!r} != "
                        f"the top promotion's version {stack[-1]['version']!r}"
                    )
                else:
                    stack.pop()
                entries.append(entry)
            except ValueError as exc:
                raise ValueError(f"{os.fspath(path)}: line {n}: {exc}") from exc
    return entries


@dataclass(frozen=True)
class LedgerState:
    """Promotions not yet undone, and the active version and config sha."""

    stack: tuple[dict[str, Any], ...]
    active_version: str | None
    active_config_sha256: str | None


def ledger_state(entries: Sequence[dict[str, Any]]) -> LedgerState:
    stack: list[dict[str, Any]] = []
    version: str | None = None
    config_sha: str | None = None
    for entry in entries:
        if entry["type"] == "promotion":
            stack.append(entry)
            version = entry["version"]
            config_sha = entry["config_sha256"]
        elif stack:
            manifest = stack.pop()["rollback_manifest"]
            version = manifest["previous_version"]
            config_sha = manifest["previous_config_sha256"]
    return LedgerState(tuple(stack), version, config_sha)


def _entries_or_empty(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    return read_ledger(path)


def _append_entry(path: str | os.PathLike[str], entry: dict[str, Any]) -> None:
    line = json.dumps(entry, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"))  # fmt: skip
    with open(path, "a+b") as fh:
        fh.seek(0, os.SEEK_END)
        if fh.tell():
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                fh.write(b"\n")
        fh.write(line.encode("utf-8") + b"\n")


def _next_sequence(entries: Sequence[dict[str, Any]]) -> int:
    return entries[-1]["sequence"] + 1 if entries else 1


def record_promotion(
    request_path: str | os.PathLike[str], ledger_path: str | os.PathLike[str], now: datetime
) -> tuple[int, dict[str, Any]]:
    """Run the gate, then append a promotion entry; returns the exit code and document."""
    request_path = os.fspath(request_path)
    result = _run_checks(request_path)
    if result.decision["decision"] != "promote":
        return 2, result.decision
    entries = _entries_or_empty(ledger_path)
    state = ledger_state(entries)
    if state.active_version is not None or state.stack:
        if result.incumbent_candidate is None:
            raise PolicyRefused(
                f"stale incumbent: the ledger active version is {state.active_version!r} "
                "but the request has no incumbent report"
            )
        if (
            result.incumbent_candidate != state.active_version
            or result.incumbent_config_sha256 != state.active_config_sha256
        ):
            raise PolicyRefused(
                f"stale incumbent: the ledger active version is {state.active_version!r} "
                f"with config_sha256 {state.active_config_sha256!r}, the request incumbent "
                f"is {result.incumbent_candidate!r} with {result.incumbent_config_sha256!r}"
            )
    entry = {
        "type": "promotion",
        "sequence": _next_sequence(entries),
        "at": _iso_now(now),
        "version": result.decision["candidate"],
        "config_sha256": result.decision["candidate_config_sha256"],
        "criteria_sha256": result.criteria_sha256,
        "candidate_report_sha256": result.decision["candidate_report_sha256"],
        "decision_sha256": hashlib.sha256(result.decision_text.encode("utf-8")).hexdigest(),
        "rollback_manifest": result.manifest_doc,
    }
    _append_entry(ledger_path, entry)
    return 0, entry


def rollback_promotion(
    ledger_path: str | os.PathLike[str], reason: str, by: str, now: datetime
) -> dict[str, Any]:
    """Append a rollback entry undoing the top promotion of the stack."""
    reason = _non_blank(reason, "--reason")
    by = _non_blank(by, "--by")
    entries = _entries_or_empty(ledger_path)
    state = ledger_state(entries)
    if not state.stack:
        raise PolicyRefused("the ledger has no promotion to roll back")
    top = state.stack[-1]
    manifest = top["rollback_manifest"]
    if manifest.get("previous_version") is None:
        raise PolicyRefused(f"promotion {top['sequence']} has no previous version to restore")
    entry = {
        "type": "rollback",
        "sequence": _next_sequence(entries),
        "at": _iso_now(now),
        "undoes_sequence": top["sequence"],
        "from_version": top["version"],
        "to_version": manifest["previous_version"],
        "to_config_sha256": manifest["previous_config_sha256"],
        "to_config": manifest["previous_config"],
        "reason": reason,
        "by": by,
    }
    _append_entry(ledger_path, entry)
    return entry


def active_version(ledger_path: str | os.PathLike[str]) -> dict[str, Any]:
    """The active version, its config sha, and the last entry's sequence."""
    entries = _entries_or_empty(ledger_path)
    state = ledger_state(entries)
    return {
        "version": state.active_version,
        "config_sha256": state.active_config_sha256,
        "sequence": entries[-1]["sequence"] if entries else None,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.evaluation.promotion",
        description="Gate model and prompt promotion on frozen release criteria.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_check = sub.add_parser("check", help="print the promotion decision of a request")
    p_check.add_argument("request")
    p_record = sub.add_parser("record", help="record a promotion in the ledger")
    p_record.add_argument("request")
    p_record.add_argument("ledger")
    p_rollback = sub.add_parser("rollback", help="undo the last promotion")
    p_rollback.add_argument("ledger")
    p_rollback.add_argument("--reason", required=True)
    p_rollback.add_argument("--by", required=True)
    p_active = sub.add_parser("active", help="print the active version")
    p_active.add_argument("ledger")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            decision = check_promotion(args.request)
            print(_dumps(decision))
            return 0 if decision["decision"] == "promote" else 2
        if args.command == "record":
            code, doc = record_promotion(args.request, args.ledger, datetime.now(UTC))
            print(_dumps(doc))
            return code
        if args.command == "rollback":
            entry = rollback_promotion(args.ledger, args.reason, args.by, datetime.now(UTC))
            print(_dumps(entry))
            return 0
        print(_dumps(active_version(args.ledger)))
        return 0
    except PolicyRefused as exc:
        print(f"error: {exc}".replace("\n", " "), file=sys.stderr)
        return 2
    except (ValueError, ArithmeticError, OSError, RecursionError) as exc:
        print(f"error: {exc}".replace("\n", " "), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
