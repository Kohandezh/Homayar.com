"""The candidate report (HOM-70): one JSON document per candidate.

``python -m backend.evaluation.candidate <manifest.json>`` prints, for one model or
prompt version under test, every evaluation of the manifest with, per slice: sample
size, a percentile bootstrap interval, abstention rate and coverage, plus a
per-request measurements section with latency, cost and tokens.  The manifest lists
the evaluations and their options exactly as the ``python -m backend.evaluation`` CLI
takes them; this module reuses that CLI's parser and evaluator builder, so a slice's
``detail`` is the object the CLI prints.  Pure and deterministic like the rest of the
package.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import random
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from backend.evaluation.__main__ import _evaluator, _parser
from backend.evaluation.common import EVALUATION_VERSION, evaluate_by_slice
from backend.evaluation.loaders import KINDS, _check_finite, _slices, load_jsonl

CANDIDATE_REPORT_VERSION = "candidate-report/1"

_TOP_KEYS = ("candidate", "evaluations", "config", "measurements", "currency", "slices",
             "bootstrap", "min_slice_size")  # fmt: skip
_EVALUATION_KEYS = ("name", "kind", "path", "args")
_BOOTSTRAP_KEYS = ("resamples", "seed", "confidence")


@dataclass(frozen=True)
class Measurement:
    """One served request: latency, tokens, cost and provider, or None when absent."""

    request_id: str
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None
    provider: str | None = None
    slices: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _Manifest:
    candidate: str
    evaluations: tuple[Mapping[str, Any], ...]
    config: dict[str, Any] | None
    measurements: str | None
    currency: str | None
    slices: tuple[str, ...]
    resamples: int
    seed: int
    confidence: float
    min_slice_size: int


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


def _names_slices(arg: str) -> bool:
    """True for ``--slices``, ``--slices=x`` and every prefix argparse would expand to it."""
    option = arg.split("=", 1)[0]
    return len(option) > 2 and "--slices".startswith(option)


def _slices_in_args(name: str) -> ValueError:
    return ValueError(
        f"--slices is not allowed in the args of evaluation {name!r}; "
        "slice dimensions come from the manifest key 'slices'")  # fmt: skip


def _parse_manifest(data: Any) -> _Manifest:
    if not isinstance(data, dict):
        raise ValueError(f"the manifest must be a JSON object, got {type(data).__name__}")
    for key in data:
        if key not in _TOP_KEYS:
            raise ValueError(f"unknown manifest key {key!r}")
    candidate = _non_blank(_required(data, "candidate"), "candidate")
    evaluations = _required(data, "evaluations")
    if not isinstance(evaluations, list) or not evaluations:
        raise ValueError(f"key 'evaluations' must be a non-empty list, got {evaluations!r}")
    seen: set[str] = set()
    parsed: list[dict[str, Any]] = []
    for i, evaluation in enumerate(evaluations):
        if not isinstance(evaluation, dict):
            raise ValueError(f"evaluations[{i}] must be an object, got {evaluation!r}")
        for key in evaluation:
            if key not in _EVALUATION_KEYS:
                raise ValueError(f"unknown key {key!r} in evaluations[{i}]")
        name = _non_blank(_required(evaluation, "name"), f"evaluations[{i}].name")
        if "/" in name:
            raise ValueError(f"evaluations[{i}].name must not contain '/', got {name!r}")
        if name in seen:
            raise ValueError(f"duplicate evaluation name {name!r}")
        seen.add(name)
        kind = _required(evaluation, "kind")
        if kind not in KINDS:
            raise ValueError(f"evaluations[{i}].kind must be one of {list(KINDS)}, got {kind!r}")
        path = _required(evaluation, "path")
        if not isinstance(path, str):
            raise ValueError(f"evaluations[{i}].path must be a string, got {path!r}")
        args = evaluation.get("args", [])
        if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
            raise ValueError(f"evaluations[{i}].args must be a list of strings, got {args!r}")
        for arg in args:
            if _names_slices(arg):
                raise _slices_in_args(name)
        parsed.append({"name": name, "kind": kind, "path": path, "args": args})
    config = data.get("config")
    if config is not None and not isinstance(config, dict):
        raise ValueError(f"key 'config' must be an object or null, got {config!r}")
    measurements = data.get("measurements")
    if measurements is not None and not isinstance(measurements, str):
        raise ValueError(f"key 'measurements' must be a string path or null, got {measurements!r}")
    currency = data.get("currency")
    if currency is not None:
        currency = _non_blank(currency, "currency")
    slices = data.get("slices", [])
    if not isinstance(slices, list):
        raise ValueError(f"key 'slices' must be a list of strings, got {slices!r}")
    for dim in slices:
        _non_blank(dim, "slices")
    if len(set(slices)) != len(slices):
        raise ValueError(f"key 'slices' must not repeat a dimension, got {slices!r}")
    bootstrap = data.get("bootstrap", {})
    if not isinstance(bootstrap, dict):
        raise ValueError(f"key 'bootstrap' must be an object, got {bootstrap!r}")
    for key in bootstrap:
        if key not in _BOOTSTRAP_KEYS:
            raise ValueError(f"unknown bootstrap key {key!r}")
    resamples = bootstrap.get("resamples", 1000)
    if not _is_int(resamples) or resamples < 0:
        raise ValueError(f"bootstrap 'resamples' must be an int >= 0, got {resamples!r}")
    seed = bootstrap.get("seed", 0)
    if not _is_int(seed):
        raise ValueError(f"bootstrap 'seed' must be an int, got {seed!r}")
    confidence = bootstrap.get("confidence", 0.95)
    if not _is_number(confidence) or not 0 < confidence < 1:
        raise ValueError(
            f"bootstrap 'confidence' must be a number with 0 < c < 1, got {confidence!r}"
        )
    min_slice_size = data.get("min_slice_size", 30)
    if not _is_int(min_slice_size) or min_slice_size < 1:
        raise ValueError(f"key 'min_slice_size' must be an int >= 1, got {min_slice_size!r}")
    return _Manifest(candidate, tuple(parsed), config, measurements, currency, tuple(slices),
                     resamples, seed, confidence, min_slice_size)  # fmt: skip


def _opt_number(record: Mapping[str, Any], key: str) -> float | None:
    if key not in record or record[key] is None:
        return None
    value = record[key]
    if not _is_number(value) or value < 0:
        raise ValueError(f"key {key!r} must be a number >= 0 or null, got {value!r}")
    return value


def _opt_int(record: Mapping[str, Any], key: str) -> int | None:
    if key not in record or record[key] is None:
        return None
    value = record[key]
    if not _is_int(value) or value < 0:
        raise ValueError(f"key {key!r} must be an int >= 0 or null, got {value!r}")
    return value


def _opt_provider(record: Mapping[str, Any], key: str) -> str | None:
    if key not in record or record[key] is None:
        return None
    return _non_blank(record[key], key)


def parse_measurement(record: Mapping[str, Any]) -> Measurement:
    """Read one measurement record; unknown keys are ignored."""
    if not isinstance(record, Mapping):
        raise ValueError(f"a record must be a JSON object, got {type(record).__name__}")
    _check_finite(dict(record))
    return Measurement(
        _non_blank(_required(record, "request_id"), "request_id"),
        _opt_number(record, "latency_ms"),
        _opt_int(record, "input_tokens"),
        _opt_int(record, "output_tokens"),
        _opt_number(record, "cost"),
        _opt_provider(record, "provider"),
        _slices(record),
    )  # fmt: skip


def load_measurements(path: str | os.PathLike[str]) -> list[Measurement]:
    """Read a measurements JSONL file, with the 1-based line number in every error."""
    out: list[Measurement] = []
    with open(path, "rb") as fh:
        for n, raw in enumerate(fh, 1):
            try:
                line = raw.decode("utf-8")
                if not line.strip():
                    continue
                out.append(parse_measurement(json.loads(line)))
            except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
                raise ValueError(f"{os.fspath(path)}: line {n}: {exc}") from exc
    return out


def _metrics_of(kind: str, detail: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The metric dicts of one slice's CLI detail, keyed by report metric name."""
    if kind == "qa_agreement":
        out: dict[str, dict[str, Any]] = {"kappa": detail["kappa"]}
        out.update({f"kappa[{c}]": m for c, m in detail["by_criterion"].items()})
        return out
    if kind == "qa_score":
        return dict(detail)
    return dict(detail["metrics"])


def _values_fn(
    kind: str, evaluate: Callable[[list[Any]], dict[str, Any]]
) -> Callable[[list[Any]], dict[str, float | None]]:
    def values(sample: list[Any]) -> dict[str, float | None]:
        return {name: m["value"] for name, m in _metrics_of(kind, evaluate(sample)).items()}

    return values


def _nearest_rank(values: Sequence[float], q: Fraction) -> float:
    ordered = sorted(values)
    rank = min(max(math.ceil(q * len(ordered)), 1), len(ordered))
    return ordered[rank - 1]


def _interval(
    value: float | None, n_input: int, defined: Sequence[float], manifest: _Manifest
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "method": "percentile_bootstrap",
        "confidence": manifest.confidence,
        "resamples": manifest.resamples,
        "seed": manifest.seed,
    }
    none = {"defined_resamples": 0, "low": None, "high": None}
    if manifest.resamples == 0:
        return {**out, **none, "reason": "bootstrap_disabled"}
    if value is None:
        return {**out, **none, "reason": "metric_undefined"}
    if n_input < 2:
        return {**out, **none, "reason": "too_few_items"}
    if not defined:
        return {**out, **none, "reason": "no_defined_resamples"}
    alpha = 1 - Fraction(str(manifest.confidence))
    return {
        **out,
        "defined_resamples": len(defined),
        "low": _nearest_rank(defined, alpha / 2),
        "high": _nearest_rank(defined, 1 - alpha / 2),
        "reason": None,
    }


def _metric_entry(
    metric: Mapping[str, Any], n_input: int, defined: Sequence[float], manifest: _Manifest
) -> dict[str, Any]:
    entry = dict(metric)
    entry["abstention_rate"] = (
        metric["n_abstained"] / metric["n_items"] if metric["n_items"] else None
    )
    units = metric["n_items"] + metric["n_excluded"]
    entry["coverage"] = metric["n_items"] / units if units else None
    entry["interval"] = _interval(metric["value"], n_input, defined, manifest)
    return entry


def _usage_message(text: str) -> str:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line:
            return line.split("error: ")[-1]
    return ""


def _evaluation_namespace(evaluation: Mapping[str, Any], full_path: str) -> argparse.Namespace:
    """Parse ``[kind, path, *args]`` with the CLI parser, as a data error when invalid."""
    parser = _parser()
    not_given = object()
    parser.set_defaults(slices=not_given)
    argv = [evaluation["kind"], full_path, *evaluation["args"]]
    err = io.StringIO()
    out = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            namespace = parser.parse_args(argv)
    except SystemExit as exc:
        message = _usage_message(err.getvalue()) or f"exit code {exc.code}"
        raise ValueError(f"invalid args for evaluation {evaluation['name']!r}: {message}") from None
    if namespace.slices is not not_given:
        raise _slices_in_args(evaluation["name"])
    if namespace.weights != "none" and namespace.categories is None:
        raise ValueError(
            f"invalid args for evaluation {evaluation['name']!r}: "
            f"--weights {namespace.weights} needs --categories"
        )
    return namespace


def _build_evaluation(
    evaluation: Mapping[str, Any], dimensions: Sequence[str], manifest: _Manifest, base: str
) -> dict[str, Any]:
    kind = evaluation["kind"]
    written = evaluation["path"]
    full = written if os.path.isabs(written) else os.path.join(base, written)
    with open(full, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    namespace = _evaluation_namespace(evaluation, full)
    options, evaluate = _evaluator(kind, namespace)
    items = load_jsonl(kind, full)
    details = evaluate_by_slice(items, evaluate, dimensions)
    groups = evaluate_by_slice(items, list, dimensions)
    values_of = _values_fn(kind, evaluate)
    slices_out: dict[str, Any] = {}
    for key, detail in details.items():
        sample_of = groups[key]
        n_input = len(sample_of)
        raw_metrics = _metrics_of(kind, detail)
        draws: dict[str, list[float]] = {name: [] for name in raw_metrics}
        any_defined = any(m["value"] is not None for m in raw_metrics.values())
        if manifest.resamples > 0 and n_input >= 2 and any_defined:
            rng = random.Random(f"{manifest.seed}:{evaluation['name']}:{key}")  # noqa: S311
            for _ in range(manifest.resamples):
                sample = [sample_of[rng.randrange(n_input)] for _ in range(n_input)]
                values = values_of(sample)
                for name in draws:
                    value = values.get(name)
                    if value is not None:
                        draws[name].append(value)
        slices_out[key] = {
            "n_input_items": n_input,
            "insufficient_sample": n_input < manifest.min_slice_size,
            "metrics": {
                name: _metric_entry(m, n_input, draws[name], manifest)
                for name, m in raw_metrics.items()
            },
            "detail": detail,
        }
    return {
        "kind": kind,
        "path": written,
        "input_sha256": digest,
        "options": {**options, "slices": list(dimensions)},
        "slices": slices_out,
    }


def _measurement_entry(records: Sequence[Measurement], manifest: _Manifest) -> dict[str, Any]:
    n_requests = len(records)
    latencies = sorted(r.latency_ms for r in records if r.latency_ms is not None)
    if latencies:
        n = len(latencies)
        latency = {
            "n": n,
            "n_missing": n_requests - n,
            "mean": sum(latencies) / n,
            "p50": _nearest_rank(latencies, Fraction(1, 2)),
            "p90": _nearest_rank(latencies, Fraction(9, 10)),
            "p95": _nearest_rank(latencies, Fraction(19, 20)),
            "max": latencies[-1],
            "reason": None,
        }
    else:
        latency = {
            "n": 0,
            "n_missing": n_requests,
            "mean": None,
            "p50": None,
            "p90": None,
            "p95": None,
            "max": None,
            "reason": "no_latency",
        }
    costs = [r.cost for r in records if r.cost is not None]
    cost = {
        "n": len(costs),
        "n_missing": n_requests - len(costs),
        "total": sum(costs) if costs else None,
        "mean_per_request": sum(costs) / len(costs) if costs else None,
        "currency": manifest.currency,
        "reason": None if costs else "no_cost",
    }
    inputs = [r.input_tokens for r in records if r.input_tokens is not None]
    outputs = [r.output_tokens for r in records if r.output_tokens is not None]
    tokens = {
        "input_total": sum(inputs) if inputs else None,
        "n_missing_input": n_requests - len(inputs),
        "output_total": sum(outputs) if outputs else None,
        "n_missing_output": n_requests - len(outputs),
    }
    providers: dict[str, int] = {}
    n_missing_provider = 0
    for record in records:
        if record.provider is None:
            n_missing_provider += 1
        else:
            providers[record.provider] = providers.get(record.provider, 0) + 1
    return {
        "n_requests": n_requests,
        "insufficient_sample": n_requests < manifest.min_slice_size,
        "latency_ms": latency,
        "cost": cost,
        "tokens": tokens,
        "providers": {k: providers[k] for k in sorted(providers)},
        "n_missing_provider": n_missing_provider,
    }


def _measurements_section(
    manifest: _Manifest, base: str, insufficient: list[str]
) -> dict[str, Any]:
    if manifest.measurements is None:
        return {
            "path": None,
            "input_sha256": None,
            "currency": manifest.currency,
            "reason": "not_measured",
            "slices": {},
        }
    written = manifest.measurements
    full = written if os.path.isabs(written) else os.path.join(base, written)
    with open(full, "rb") as fh:
        raw = fh.read()
    records = load_measurements(full)
    for record in records:
        if record.cost is not None and manifest.currency is None:
            raise ValueError(
                f"measurement {record.request_id!r} has a cost but the manifest currency is null"
            )
    slices_out = {}
    for key, group in evaluate_by_slice(records, list, manifest.slices).items():
        entry = _measurement_entry(group, manifest)
        slices_out[key] = entry
        if entry["insufficient_sample"]:
            insufficient.append(f"measurements/{key}")
    return {
        "path": written,
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "currency": manifest.currency,
        "reason": None,
        "slices": slices_out,
    }


def build_candidate_report(manifest_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Build the full candidate report of a manifest, as a JSON-safe dict."""
    with open(manifest_path, "rb") as fh:
        raw = fh.read()
    manifest = _parse_manifest(json.loads(raw.decode("utf-8")))
    base = os.path.dirname(os.path.abspath(os.fspath(manifest_path)))
    dimensions = list(manifest.slices)
    evaluations: dict[str, Any] = {}
    insufficient: list[str] = []
    for evaluation in manifest.evaluations:
        document = _build_evaluation(evaluation, dimensions, manifest, base)
        evaluations[evaluation["name"]] = document
        for key, entry in document["slices"].items():
            if entry["insufficient_sample"]:
                insufficient.append(f"{evaluation['name']}/{key}")
    return {
        "version": CANDIDATE_REPORT_VERSION,
        "evaluation_version": EVALUATION_VERSION,
        "candidate": manifest.candidate,
        "config": manifest.config,
        "config_sha256": _config_sha256(manifest.config),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "slices": dimensions,
        "min_slice_size": manifest.min_slice_size,
        "bootstrap": {
            "method": "percentile_bootstrap",
            "resamples": manifest.resamples,
            "seed": manifest.seed,
            "confidence": manifest.confidence,
        },
        "evaluations": evaluations,
        "measurements": _measurements_section(manifest, base, insufficient),
        "insufficient_slices": sorted(insufficient),
    }


def _config_sha256(config: dict[str, Any] | None) -> str | None:
    if config is None:
        return None
    text = json.dumps(config, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.evaluation.candidate",
        description="Build the candidate report of a manifest.",
    )
    parser.add_argument("manifest")
    args = parser.parse_args(argv)
    try:
        report = build_candidate_report(args.manifest)
        text = json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False)
    except (ValueError, ArithmeticError, OSError, RecursionError) as exc:
        print(f"error: {exc}".replace("\n", " "), file=sys.stderr)
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
