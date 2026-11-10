"""Acceptance oracle for the candidate report (HOM-70), written from SPEC.md alone."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from backend.evaluation import (
    EVALUATION_VERSION,
    cohen_kappa,
    evaluate_by_slice,
    evaluate_classification,
    evaluate_retrieval,
    kappa_by_criterion,
    load_jsonl,
    score_mae,
)

HOMAYAR_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = HOMAYAR_ROOT.parent
HOM351 = REPO_ROOT / "docs" / "research" / "hom-351-local-models" / "results"

TOP_KEYS = {
    "version",
    "evaluation_version",
    "candidate",
    "config",
    "config_sha256",
    "manifest_sha256",
    "slices",
    "min_slice_size",
    "bootstrap",
    "evaluations",
    "measurements",
    "insufficient_slices",
}
METRIC_KEYS = {
    "name",
    "value",
    "numerator",
    "denominator",
    "n_items",
    "n_excluded",
    "n_abstained",
    "reason",
    "abstention_rate",
    "coverage",
    "interval",
}
INTERVAL_KEYS = {
    "method",
    "confidence",
    "resamples",
    "seed",
    "defined_resamples",
    "low",
    "high",
    "reason",
}

RETRIEVAL = [
    {"query_id": "r1", "relevance": {"d1": 2, "d2": 1}, "ranked": ["d1", "d3", "d2"],
     "slices": {"lang": "fa"}},
    {"query_id": "r2", "relevance": {"d4": 1}, "ranked": ["d3", "d4"], "slices": {"lang": "fa"}},
    {"query_id": "r3", "relevance": {"d5": 0}, "ranked": ["d5"], "slices": {"lang": "fa"}},
    {"query_id": "r4", "relevance": {"d1": 1}, "ranked": [], "slices": {"lang": "en"}},
    {"query_id": "r5", "relevance": {"d2": 3, "d6": 1}, "ranked": ["d6", "d2"],
     "slices": {"lang": "en"}},
    {"query_id": "r6", "relevance": {"d7": 1}, "ranked": ["d8", "d9", "d7"]},
]  # fmt: skip
INTENT = [
    {"gold": "a", "predicted": "a", "slices": {"lang": "fa"}},
    {"gold": "a", "predicted": None, "slices": {"lang": "fa"}},
    {"gold": "b", "predicted": "a", "slices": {"lang": "fa"}},
    {"gold": None, "predicted": None, "slices": {"lang": "en"}},
    {"gold": "b", "predicted": "b", "slices": {"lang": "en"}},
]
AGREEMENT = [
    {"rating_a": "a", "rating_b": "a", "criterion": "c1", "slices": {"lang": "fa"}},
    {"rating_a": "a", "rating_b": "b", "criterion": "c1", "slices": {"lang": "fa"}},
    {"rating_a": "b", "rating_b": "b", "criterion": "c1", "slices": {"lang": "en"}},
    {"rating_a": "c", "rating_b": "c", "criterion": "c2", "slices": {"lang": "en"}},
    {"rating_a": "b", "rating_b": "c", "criterion": "c2", "slices": {"lang": "fa"}},
    {"rating_a": None, "rating_b": "a", "criterion": "c2", "slices": {"lang": "en"}},
]
MEASUREMENTS = [
    {"request_id": "m1", "latency_ms": 40, "input_tokens": 10, "output_tokens": 5, "cost": 0.25,
     "provider": "p1", "slices": {"lang": "fa"}},
    {"request_id": "m2", "latency_ms": 10, "input_tokens": None, "output_tokens": 7,
     "cost": None, "provider": "p2", "slices": {"lang": "fa"}},
    {"request_id": "m3", "latency_ms": None, "input_tokens": 3, "output_tokens": None,
     "cost": 0.5, "provider": "p1", "slices": {"lang": "en"}},
    {"request_id": "m4", "latency_ms": 20, "provider": None, "slices": {"lang": "en"}},
    {"request_id": "m5", "latency_ms": 30.5, "input_tokens": 2, "output_tokens": 1, "cost": 1.25,
     "provider": "p1", "note": "unknown keys are ignored"},
    {"request_id": "m6", "slices": {"lang": "de"}},
]  # fmt: skip

RET_KS = [1, 3]
AGR_OPTS: dict[str, Any] = {"categories": ["a", "b", "c"], "weights": "linear"}


def ret_metrics(xs: list[Any]) -> dict[str, Any]:
    return dict(evaluate_retrieval(xs, ks=RET_KS).metrics)


def default_ret_metrics(xs: list[Any]) -> dict[str, Any]:
    return dict(evaluate_retrieval(xs, ks=[1, 5, 10]).metrics)


def cls_metrics(xs: list[Any]) -> dict[str, Any]:
    return dict(evaluate_classification(xs, labels=None).metrics)


def agr_metrics(xs: list[Any]) -> dict[str, Any]:
    out = {"kappa": cohen_kappa(xs, **AGR_OPTS)}
    for k, m in kappa_by_criterion(xs, **AGR_OPTS).items():
        out[f"kappa[{k}]"] = m
    return out


def write_jsonl(path: Path, records: list[Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    path.write_text(text, encoding="utf-8")
    return path


def write_lines(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


def write_manifest(directory: Path, manifest: Any, name: str = "candidate.json") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_candidate(*argv: Any, cwd: Path = HOMAYAR_ROOT) -> subprocess.CompletedProcess[bytes]:
    args = [sys.executable, "-m", "backend.evaluation.candidate", *(str(a) for a in argv)]
    return subprocess.run(args, cwd=cwd, capture_output=True, check=False)  # noqa: S603 - fixed argv, the package's own CLI


def report_of(manifest_path: Path) -> dict[str, Any]:
    proc = run_candidate(manifest_path)
    assert proc.returncode == 0, proc.stderr.decode()
    return json.loads(proc.stdout)


def run_evaluation_cli(kind: str, path: Path, args: list[str], dims: list[str]) -> dict[str, Any]:
    argv = [sys.executable, "-m", "backend.evaluation", kind, str(path), *args]
    if dims:
        argv += ["--slices", ",".join(dims)]
    proc = subprocess.run(argv, cwd=HOMAYAR_ROOT, capture_output=True, check=False)  # noqa: S603 - fixed argv, the package's own CLI
    assert proc.returncode == 0, proc.stderr.decode()
    return json.loads(proc.stdout)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def nearest_rank(values: list[float], q: Fraction) -> float:
    m = len(values)
    rank = min(max(math.ceil(q * m), 1), m)
    return values[rank - 1]


def reference_intervals(
    metric_fn: Callable[[list[Any]], dict[str, Any]],
    items: list[Any],
    eval_name: str,
    key: str,
    resamples: int,
    seed: int,
    confidence: float,
) -> dict[str, dict[str, Any]]:
    full = metric_fn(items)
    n = len(items)
    samples: dict[str, list[float]] = {name: [] for name in full}
    if resamples and n >= 2:
        rng = random.Random(f"{seed}:{eval_name}:{key}")  # noqa: S311 - seeded statistical bootstrap, not crypto
        for _ in range(resamples):
            sample = [items[rng.randrange(n)] for _ in range(n)]
            got = metric_fn(sample)
            for name in full:
                mv = got.get(name)
                if mv is not None and mv.value is not None:
                    samples[name].append(mv.value)
    out = {}
    for name, mv in full.items():
        iv: dict[str, Any] = {
            "method": "percentile_bootstrap",
            "confidence": confidence,
            "resamples": resamples,
            "seed": seed,
            "defined_resamples": 0,
            "low": None,
            "high": None,
        }
        if resamples == 0:
            iv["reason"] = "bootstrap_disabled"
        elif mv.value is None:
            iv["reason"] = "metric_undefined"
        elif n < 2:
            iv["reason"] = "too_few_items"
        elif not samples[name]:
            iv["reason"] = "no_defined_resamples"
        else:
            vals = sorted(samples[name])
            alpha = 1 - Fraction(str(confidence))
            iv["defined_resamples"] = len(vals)
            iv["low"] = nearest_rank(vals, alpha / 2)
            iv["high"] = nearest_rank(vals, 1 - alpha / 2)
            iv["reason"] = None
        out[name] = iv
    return out


def reference_metric_entry(mv: Any, interval: dict[str, Any]) -> dict[str, Any]:
    d = mv.as_dict()
    total = mv.n_items + mv.n_excluded
    d["abstention_rate"] = mv.n_abstained / mv.n_items if mv.n_items else None
    d["coverage"] = mv.n_items / total if total else None
    d["interval"] = interval
    return d


def reference_slices(
    metric_fn: Callable[[list[Any]], dict[str, Any]],
    items: list[Any],
    eval_name: str,
    dims: list[str],
    boot: tuple[int, int, float],
    min_size: int,
    cli_slices: dict[str, Any],
) -> dict[str, Any]:
    resamples, seed, confidence = boot
    out = {}
    for key, xs in evaluate_by_slice(items, list, dims).items():
        full = metric_fn(xs)
        ivs = reference_intervals(metric_fn, xs, eval_name, key, resamples, seed, confidence)
        out[key] = {
            "n_input_items": len(xs),
            "insufficient_sample": len(xs) < min_size,
            "metrics": {k: reference_metric_entry(mv, ivs[k]) for k, mv in full.items()},
            "detail": cli_slices[key],
        }
    return out


def main_manifest() -> dict[str, Any]:
    return {
        "candidate": "cand-a",
        "config": {"model": "m", "temperature": 0.0, "note": "فارسی"},
        "evaluations": [
            {"name": "ret", "kind": "retrieval", "path": "data/retrieval.jsonl",
             "args": ["--ks", "1,3"]},
            {"name": "cls", "kind": "intent", "path": "data/intent.jsonl"},
            {"name": "agr", "kind": "qa_agreement", "path": "data/agreement.jsonl",
             "args": ["--weights", "linear", "--categories", "a,b,c"]},
        ],
        "measurements": "data/measurements.jsonl",
        "currency": "USD",
        "slices": ["lang"],
        "bootstrap": {"resamples": 40, "seed": 7, "confidence": 0.9},
        "min_slice_size": 3,
    }  # fmt: skip


def write_main(directory: Path) -> Path:
    write_jsonl(directory / "data" / "retrieval.jsonl", RETRIEVAL)
    write_jsonl(directory / "data" / "intent.jsonl", INTENT)
    write_jsonl(directory / "data" / "agreement.jsonl", AGREEMENT)
    write_jsonl(directory / "data" / "measurements.jsonl", MEASUREMENTS)
    return write_manifest(directory, main_manifest())


@pytest.fixture
def main_case(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    path = write_main(tmp_path / "case")
    return path, report_of(path)


def test_public_exports() -> None:
    import backend.evaluation as ev

    for name in (
        "CANDIDATE_REPORT_VERSION",
        "Measurement",
        "load_measurements",
        "parse_measurement",
        "build_candidate_report",
    ):
        assert hasattr(ev, name), name
        assert name in ev.__all__, name
    assert ev.CANDIDATE_REPORT_VERSION == "candidate-report/1"


def test_ac1_top_level_structure(main_case: tuple[Path, dict[str, Any]]) -> None:
    path, report = main_case
    manifest = main_manifest()
    assert set(report) == TOP_KEYS
    assert report["version"] == "candidate-report/1"
    assert report["evaluation_version"] == EVALUATION_VERSION
    assert report["candidate"] == "cand-a"
    assert report["config"] == manifest["config"]
    config_bytes = json.dumps(manifest["config"], sort_keys=True, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")  # fmt: skip
    assert report["config_sha256"] == hashlib.sha256(config_bytes).hexdigest()
    assert report["manifest_sha256"] == sha256_file(path)
    assert report["slices"] == ["lang"]
    assert report["min_slice_size"] == 3
    assert report["bootstrap"] == {
        "method": "percentile_bootstrap",
        "resamples": 40,
        "seed": 7,
        "confidence": 0.9,
    }
    assert set(report["evaluations"]) == {"ret", "cls", "agr"}
    data = path.parent / "data"
    for name, kind, rel, file in (
        ("ret", "retrieval", "data/retrieval.jsonl", "retrieval.jsonl"),
        ("cls", "intent", "data/intent.jsonl", "intent.jsonl"),
        ("agr", "qa_agreement", "data/agreement.jsonl", "agreement.jsonl"),
    ):
        ev = report["evaluations"][name]
        assert set(ev) == {"kind", "path", "input_sha256", "options", "slices"}
        assert ev["kind"] == kind
        assert ev["path"] == rel
        assert ev["input_sha256"] == sha256_file(data / file)
        assert list(ev["slices"]) == ["all", "lang=en", "lang=fa"]
        for entry in ev["slices"].values():
            assert set(entry) == {"n_input_items", "insufficient_sample", "metrics", "detail"}
            for metric in entry["metrics"].values():
                assert set(metric) == METRIC_KEYS
                assert set(metric["interval"]) == INTERVAL_KEYS
    meas = report["measurements"]
    assert set(meas) == {"path", "input_sha256", "currency", "reason", "slices"}
    assert meas["path"] == "data/measurements.jsonl"
    assert meas["input_sha256"] == sha256_file(data / "measurements.jsonl")
    assert meas["currency"] == "USD"
    assert meas["reason"] is None


def test_ac1_build_candidate_report_equals_cli(main_case: tuple[Path, dict[str, Any]]) -> None:
    from backend.evaluation import build_candidate_report

    path, report = main_case
    built = build_candidate_report(path)
    assert json.loads(json.dumps(built, allow_nan=False)) == report


def test_ac1_cli_output_format(tmp_path: Path) -> None:
    path = write_main(tmp_path / "case")
    proc = run_candidate(path)
    assert proc.returncode == 0, proc.stderr.decode()
    doc = json.loads(proc.stdout)
    expected = json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False)
    assert proc.stdout.decode("utf-8") == expected + "\n"
    assert "فارسی" in proc.stdout.decode("utf-8")


def test_ac1_defaults(tmp_path: Path) -> None:
    d = tmp_path / "case"
    write_jsonl(d / "r.jsonl", RETRIEVAL)
    path = write_manifest(
        d,
        {"candidate": "x", "evaluations": [{"name": "e", "kind": "retrieval", "path": "r.jsonl"}]},
    )
    report = report_of(path)
    assert report["config"] is None
    assert report["config_sha256"] is None
    assert report["slices"] == []
    assert report["min_slice_size"] == 30
    assert report["bootstrap"] == {
        "method": "percentile_bootstrap",
        "resamples": 1000,
        "seed": 0,
        "confidence": 0.95,
    }
    assert report["measurements"] == {
        "path": None,
        "input_sha256": None,
        "currency": None,
        "reason": "not_measured",
        "slices": {},
    }
    ev = report["evaluations"]["e"]
    assert list(ev["slices"]) == ["all"]
    assert ev["options"] == {"ks": [1, 5, 10], "slices": []}
    mrr = ev["slices"]["all"]["metrics"]["mrr"]["interval"]
    assert (mrr["resamples"], mrr["seed"], mrr["confidence"]) == (1000, 0, 0.95)
    assert report["insufficient_slices"] == ["e/all"]


@pytest.mark.parametrize(
    ("name", "kind", "records", "args", "metric_fn"),
    [
        ("ret", "retrieval", RETRIEVAL, ["--ks", "1,3"], ret_metrics),
        ("cls", "intent", INTENT, [], cls_metrics),
        ("agr", "qa_agreement", AGREEMENT,
         ["--weights", "linear", "--categories", "a,b,c"], agr_metrics),
    ],
    ids=["retrieval", "intent", "qa_agreement"],
)  # fmt: skip
def test_ac2_ac3_ac4_ac8_entry_matches_reference(
    main_case: tuple[Path, dict[str, Any]],
    name: str,
    kind: str,
    records: list[Any],
    args: list[str],
    metric_fn: Callable[[list[Any]], dict[str, Any]],
) -> None:
    path, report = main_case
    file = path.parent / report["evaluations"][name]["path"]
    items = load_jsonl(kind, file)
    cli = run_evaluation_cli(kind, file, args, ["lang"])
    expected = reference_slices(metric_fn, items, name, ["lang"], (40, 7, 0.9), 3, cli["slices"])
    ev = report["evaluations"][name]
    assert ev["options"] == cli["options"]
    assert list(ev["slices"]) == list(expected)
    for key in expected:
        assert ev["slices"][key] == expected[key], key


def test_ac2_counts_and_slice_keys(main_case: tuple[Path, dict[str, Any]]) -> None:
    _, report = main_case
    ret = report["evaluations"]["ret"]["slices"]
    assert list(ret) == ["all", "lang=en", "lang=fa"]
    assert [ret[k]["n_input_items"] for k in ret] == [6, 2, 3]
    mrr = {k: ret[k]["metrics"]["mrr"] for k in ret}
    assert (mrr["all"]["n_items"], mrr["all"]["n_excluded"], mrr["all"]["n_abstained"]) == (5, 1, 1)
    assert (mrr["lang=fa"]["n_items"], mrr["lang=fa"]["n_excluded"]) == (2, 1)
    assert mrr["lang=fa"]["n_abstained"] == 0
    assert (mrr["lang=en"]["n_items"], mrr["lang=en"]["n_excluded"]) == (2, 0)
    assert mrr["lang=en"]["n_abstained"] == 1
    assert set(ret["all"]["metrics"]) == {
        "mrr", "recall@1", "recall@3", "precision@1", "precision@3",
        "hit@1", "hit@3", "ndcg@1", "ndcg@3",
    }  # fmt: skip
    cls = report["evaluations"]["cls"]["slices"]
    assert [cls[k]["n_input_items"] for k in cls] == [5, 2, 3]
    acc = cls["all"]["metrics"]["accuracy"]
    assert (acc["n_items"], acc["n_excluded"], acc["n_abstained"]) == (4, 1, 1)
    agr = report["evaluations"]["agr"]["slices"]
    assert [agr[k]["n_input_items"] for k in agr] == [6, 3, 3]
    assert set(agr["all"]["metrics"]) == {"kappa", "kappa[c1]", "kappa[c2]"}
    kappa = agr["all"]["metrics"]["kappa"]
    assert (kappa["n_items"], kappa["n_excluded"]) == (5, 1)
    assert agr["all"]["metrics"]["kappa[c2]"]["n_items"] == 2


MINIMAL_BY_KIND = {
    "asr": {"reference": "سلام دنیا", "hypothesis": "سلام"},
    "diarization": {"recording_id": "rec", "reference": [{"speaker": "a", "start_ms": 0,
                    "end_ms": 1000}], "hypothesis": [{"speaker": "x", "start_ms": 0,
                    "end_ms": 900}]},
    "intent": {"gold": "a", "predicted": "a"},
    "entities": {"gold": [{"type": "t", "value": "v"}], "predicted": [{"type": "t", "value": "v"}]},
    "qa_agreement": {"rating_a": "a", "rating_b": "b", "criterion": "c1"},
    "qa_score": {"score": 3, "reference": 4},
    "factual_support": {"claim_id": "c", "label": "supported"},
    "retrieval": {"query_id": "q", "relevance": {"d": 1}, "ranked": ["d"]},
}  # fmt: skip


def expected_metric_names(kind: str, file: Path) -> set[str]:
    cli = run_evaluation_cli(kind, file, [], [])["slices"]["all"]
    if kind == "qa_agreement":
        return {"kappa", *(f"kappa[{k}]" for k in cli["by_criterion"])}
    if kind == "qa_score":
        return {"score_mae"}
    return set(cli["metrics"])


def test_metric_names_for_every_kind(tmp_path: Path) -> None:
    d = tmp_path / "case"
    evaluations = []
    for kind, record in MINIMAL_BY_KIND.items():
        write_jsonl(d / f"{kind}.jsonl", [record, record])
        evaluations.append({"name": f"e_{kind}", "kind": kind, "path": f"{kind}.jsonl"})
    report = report_of(
        write_manifest(d, {"candidate": "x", "evaluations": evaluations,
                           "bootstrap": {"resamples": 3}})
    )  # fmt: skip
    for kind in MINIMAL_BY_KIND:
        ev = report["evaluations"][f"e_{kind}"]
        assert ev["kind"] == kind
        got = set(ev["slices"]["all"]["metrics"])
        assert got == expected_metric_names(kind, d / f"{kind}.jsonl"), kind


def test_ac3_abstention_and_coverage_values(main_case: tuple[Path, dict[str, Any]]) -> None:
    _, report = main_case
    ret = report["evaluations"]["ret"]["slices"]
    assert ret["all"]["metrics"]["mrr"]["abstention_rate"] == 1 / 5
    assert ret["all"]["metrics"]["mrr"]["coverage"] == 5 / 6
    assert ret["lang=fa"]["metrics"]["mrr"]["abstention_rate"] == 0.0
    assert ret["lang=fa"]["metrics"]["mrr"]["coverage"] == 2 / 3
    assert ret["lang=en"]["metrics"]["mrr"]["abstention_rate"] == 0.5
    assert ret["lang=en"]["metrics"]["mrr"]["coverage"] == 1.0
    acc = report["evaluations"]["cls"]["slices"]["all"]["metrics"]["accuracy"]
    assert acc["abstention_rate"] == 0.25
    assert acc["coverage"] == 0.8


def test_ac3_none_cases(tmp_path: Path) -> None:
    d = tmp_path / "case"
    write_jsonl(d / "excluded.jsonl", [
        {"query_id": "a", "relevance": {}, "ranked": ["x"]},
        {"query_id": "b", "relevance": {"y": 0}, "ranked": ["y"]},
    ])  # fmt: skip
    write_lines(d / "empty.jsonl", [""])
    report = report_of(write_manifest(d, {
        "candidate": "x",
        "evaluations": [
            {"name": "excl", "kind": "retrieval", "path": "excluded.jsonl"},
            {"name": "empty", "kind": "retrieval", "path": "empty.jsonl"},
        ],
        "bootstrap": {"resamples": 5},
    }))  # fmt: skip
    excl = report["evaluations"]["excl"]["slices"]["all"]
    assert excl["n_input_items"] == 2
    mrr = excl["metrics"]["mrr"]
    assert (mrr["n_items"], mrr["n_excluded"]) == (0, 2)
    assert mrr["abstention_rate"] is None
    assert mrr["coverage"] == 0.0
    assert mrr["value"] is None
    assert mrr["interval"]["reason"] == "metric_undefined"
    empty = report["evaluations"]["empty"]["slices"]
    assert list(empty) == ["all"]
    assert empty["all"]["n_input_items"] == 0
    mrr = empty["all"]["metrics"]["mrr"]
    assert (mrr["n_items"], mrr["n_excluded"]) == (0, 0)
    assert mrr["abstention_rate"] is None
    assert mrr["coverage"] is None
    assert mrr["interval"]["reason"] == "metric_undefined"


SCORABLE = {"query_id": "s", "relevance": {"d1": 1}, "ranked": ["d2", "d1"]}
UNSCORABLE = {"query_id": "u", "relevance": {"d1": 0}, "ranked": ["d1"]}


def single_eval_report(
    tmp_path: Path, records: list[Any], bootstrap: dict[str, Any], name: str = "ev"
) -> dict[str, Any]:
    d = tmp_path / "case"
    write_jsonl(d / "r.jsonl", records)
    manifest = {
        "candidate": "x",
        "evaluations": [{"name": name, "kind": "retrieval", "path": "r.jsonl"}],
        "bootstrap": bootstrap,
    }
    return report_of(write_manifest(d, manifest))


@pytest.mark.parametrize(
    ("records", "resamples", "reason"),
    [
        ([UNSCORABLE], 0, "bootstrap_disabled"),
        ([SCORABLE, SCORABLE], 0, "bootstrap_disabled"),
        ([UNSCORABLE], 10, "metric_undefined"),
        ([UNSCORABLE, UNSCORABLE], 10, "metric_undefined"),
        ([SCORABLE], 10, "too_few_items"),
        ([SCORABLE, SCORABLE], 10, None),
    ],
    ids=[
        "disabled_before_undefined",
        "disabled_with_defined_value",
        "undefined_before_too_few",
        "undefined_with_two_items",
        "too_few_items",
        "two_items_resample",
    ],
)
def test_ac4_interval_reason_order(
    tmp_path: Path, records: list[Any], resamples: int, reason: str | None
) -> None:
    report = single_eval_report(tmp_path, records, {"resamples": resamples})
    iv = report["evaluations"]["ev"]["slices"]["all"]["metrics"]["mrr"]["interval"]
    assert iv["reason"] == reason
    assert iv["resamples"] == resamples
    if reason is None:
        assert iv["defined_resamples"] == resamples
        assert iv["low"] == iv["high"] == 0.5
    else:
        assert (iv["low"], iv["high"], iv["defined_resamples"]) == (None, None, 0)


def seed_where_draws(pick: Callable[[list[int]], bool]) -> int:
    for seed in range(1000):
        rng = random.Random(f"{seed}:ev:all")  # noqa: S311 - seeded statistical bootstrap, not crypto
        if pick([rng.randrange(2), rng.randrange(2)]):
            return seed
    raise AssertionError("no seed found")


def test_ac4_no_defined_resamples(tmp_path: Path) -> None:
    seed = seed_where_draws(lambda draws: draws == [1, 1])
    report = single_eval_report(tmp_path, [SCORABLE, UNSCORABLE], {"resamples": 1, "seed": seed})
    mrr = report["evaluations"]["ev"]["slices"]["all"]["metrics"]["mrr"]
    assert mrr["value"] == 0.5
    iv = mrr["interval"]
    assert iv["reason"] == "no_defined_resamples"
    assert (iv["low"], iv["high"], iv["defined_resamples"]) == (None, None, 0)


def test_ac4_one_defined_resample_control(tmp_path: Path) -> None:
    seed = seed_where_draws(lambda draws: 0 in draws)
    report = single_eval_report(tmp_path, [SCORABLE, UNSCORABLE], {"resamples": 1, "seed": seed})
    iv = report["evaluations"]["ev"]["slices"]["all"]["metrics"]["mrr"]["interval"]
    assert iv["reason"] is None
    assert iv["defined_resamples"] == 1
    assert iv["low"] == iv["high"] == 0.5


def test_ac4_partial_defined_resamples_still_reported(tmp_path: Path) -> None:
    records = [SCORABLE, UNSCORABLE]
    report = single_eval_report(tmp_path, records, {"resamples": 50, "seed": 3})
    metrics = report["evaluations"]["ev"]["slices"]["all"]["metrics"]
    items = load_jsonl("retrieval", tmp_path / "case" / "r.jsonl")
    expected = reference_intervals(default_ret_metrics, items, "ev", "all", 50, 3, 0.95)
    iv = metrics["mrr"]["interval"]
    assert 0 < iv["defined_resamples"] < 50
    assert iv["low"] is not None
    assert iv["high"] is not None
    for name, metric in metrics.items():
        assert metric["interval"] == expected[name], name


def varied_queries(n: int) -> list[dict[str, Any]]:
    out = []
    for k in range(n):
        pos = k % 4
        ranked = [f"x{k}{j}" for j in range(pos)] + [f"d{k}"]
        out.append({"query_id": f"q{k}", "relevance": {f"d{k}": 1}, "ranked": ranked})
    return out


def test_ac4_low_le_high_and_matches_reference(tmp_path: Path) -> None:
    report = single_eval_report(tmp_path, varied_queries(12), {"resamples": 200, "seed": 11})
    items = load_jsonl("retrieval", tmp_path / "case" / "r.jsonl")
    expected = reference_intervals(default_ret_metrics, items, "ev", "all", 200, 11, 0.95)
    metrics = report["evaluations"]["ev"]["slices"]["all"]["metrics"]
    for name, metric in metrics.items():
        iv = metric["interval"]
        assert iv["low"] <= iv["high"], name
        assert iv == expected[name], name
    assert metrics["mrr"]["interval"]["low"] < metrics["mrr"]["interval"]["high"]


def test_ac4_identical_items_give_point_interval(tmp_path: Path) -> None:
    report = single_eval_report(tmp_path, [SCORABLE] * 5, {"resamples": 30})
    metrics = report["evaluations"]["ev"]["slices"]["all"]["metrics"]
    for name, metric in metrics.items():
        iv = metric["interval"]
        assert iv["low"] == iv["high"] == metric["value"], name
        assert iv["defined_resamples"] == 30


def test_ac4_same_manifest_same_interval(tmp_path: Path) -> None:
    first = single_eval_report(tmp_path / "a", varied_queries(12), {"resamples": 30, "seed": 5})
    second = single_eval_report(tmp_path / "b", varied_queries(12), {"resamples": 30, "seed": 5})
    assert first["evaluations"] == second["evaluations"]


def test_ac4_different_seed_changes_interval(tmp_path: Path) -> None:
    seeds = (0, 1)
    reports = [
        single_eval_report(tmp_path / str(s), varied_queries(12), {"resamples": 30, "seed": s})
        for s in seeds
    ]
    items = load_jsonl("retrieval", tmp_path / "0" / "case" / "r.jsonl")
    refs = [reference_intervals(default_ret_metrics, items, "ev", "all", 30, s, 0.95)
            for s in seeds]  # fmt: skip
    assert refs[0] != refs[1]
    got = [r["evaluations"]["ev"]["slices"]["all"]["metrics"] for r in reports]
    for g, ref in zip(got, refs, strict=True):
        assert {k: m["interval"] for k, m in g.items()} == ref
    assert any(got[0][k]["interval"] != got[1][k]["interval"] for k in got[0])


def test_ac4_seed_string_includes_name_and_slice(tmp_path: Path) -> None:
    report = single_eval_report(
        tmp_path, varied_queries(12), {"resamples": 30, "seed": 0}, name="other"
    )
    items = load_jsonl("retrieval", tmp_path / "case" / "r.jsonl")
    expected = reference_intervals(default_ret_metrics, items, "other", "all", 30, 0, 0.95)
    metrics = report["evaluations"]["other"]["slices"]["all"]["metrics"]
    assert {k: m["interval"] for k, m in metrics.items()} == expected


@pytest.mark.parametrize(
    ("confidence", "low_rank", "high_rank"),
    [(0.95, 25, 975), (0.9, 50, 950), (0.99, 5, 995)],
    ids=["c095", "c090", "c099"],
)
def test_ac4_nearest_rank_with_1000_resamples(
    tmp_path: Path, confidence: float, low_rank: int, high_rank: int
) -> None:
    boot = {"resamples": 1000, "seed": 2, "confidence": confidence}
    report = single_eval_report(tmp_path, varied_queries(12), boot)
    items = load_jsonl("retrieval", tmp_path / "case" / "r.jsonl")
    rng = random.Random("2:ev:all")  # noqa: S311 - seeded statistical bootstrap, not crypto
    values = []
    for _ in range(1000):
        sample = [items[rng.randrange(12)] for _ in range(12)]
        values.append(evaluate_retrieval(sample, ks=[1, 5, 10]).metrics["mrr"].value)
    values.sort()
    iv = report["evaluations"]["ev"]["slices"]["all"]["metrics"]["mrr"]["interval"]
    assert iv["defined_resamples"] == 1000
    assert iv["confidence"] == confidence
    assert iv["low"] == values[low_rank - 1]
    assert iv["high"] == values[high_rank - 1]


def test_ac5_insufficient_flags(main_case: tuple[Path, dict[str, Any]]) -> None:
    _, report = main_case
    ret = report["evaluations"]["ret"]["slices"]
    assert ret["all"]["insufficient_sample"] is False
    assert ret["lang=fa"]["insufficient_sample"] is False
    assert ret["lang=en"]["insufficient_sample"] is True
    meas = report["measurements"]["slices"]
    assert meas["all"]["insufficient_sample"] is False
    assert meas["lang=fa"]["insufficient_sample"] is True
    assert report["insufficient_slices"] == [
        "cls/lang=en",
        "measurements/lang=de",
        "measurements/lang=en",
        "measurements/lang=fa",
        "ret/lang=en",
    ]


def test_ac5_min_slice_size_one_flags_nothing(tmp_path: Path) -> None:
    d = tmp_path / "case"
    write_main(d)
    manifest = main_manifest()
    manifest["min_slice_size"] = 1
    report = report_of(write_manifest(d, manifest))
    assert report["insufficient_slices"] == []
    assert report["min_slice_size"] == 1


def test_ac5_default_min_slice_size_flags_everything(tmp_path: Path) -> None:
    d = tmp_path / "case"
    write_main(d)
    manifest = main_manifest()
    del manifest["min_slice_size"]
    report = report_of(write_manifest(d, manifest))
    expected = sorted(
        [f"{e}/{k}" for e in ("ret", "cls", "agr") for k in ("all", "lang=en", "lang=fa")]
        + [f"measurements/{k}" for k in ("all", "lang=de", "lang=en", "lang=fa")]
    )
    assert report["insufficient_slices"] == expected


def test_ac6_measurement_entries(main_case: tuple[Path, dict[str, Any]]) -> None:
    _, report = main_case
    slices = report["measurements"]["slices"]
    assert list(slices) == ["all", "lang=de", "lang=en", "lang=fa"]
    assert slices["all"] == {
        "n_requests": 6,
        "insufficient_sample": False,
        "latency_ms": {"n": 4, "n_missing": 2, "mean": 25.125, "p50": 20, "p90": 40,
                       "p95": 40, "max": 40, "reason": None},
        "cost": {"n": 3, "n_missing": 3, "total": 2.0, "mean_per_request": 2.0 / 3,
                 "currency": "USD", "reason": None},
        "tokens": {"input_total": 15, "n_missing_input": 3, "output_total": 13,
                   "n_missing_output": 3},
        "providers": {"p1": 3, "p2": 1},
        "n_missing_provider": 2,
    }  # fmt: skip
    assert slices["lang=fa"] == {
        "n_requests": 2,
        "insufficient_sample": True,
        "latency_ms": {"n": 2, "n_missing": 0, "mean": 25, "p50": 10, "p90": 40, "p95": 40,
                       "max": 40, "reason": None},
        "cost": {"n": 1, "n_missing": 1, "total": 0.25, "mean_per_request": 0.25,
                 "currency": "USD", "reason": None},
        "tokens": {"input_total": 10, "n_missing_input": 1, "output_total": 12,
                   "n_missing_output": 0},
        "providers": {"p1": 1, "p2": 1},
        "n_missing_provider": 0,
    }  # fmt: skip
    assert slices["lang=en"] == {
        "n_requests": 2,
        "insufficient_sample": True,
        "latency_ms": {"n": 1, "n_missing": 1, "mean": 20, "p50": 20, "p90": 20, "p95": 20,
                       "max": 20, "reason": None},
        "cost": {"n": 1, "n_missing": 1, "total": 0.5, "mean_per_request": 0.5,
                 "currency": "USD", "reason": None},
        "tokens": {"input_total": 3, "n_missing_input": 1, "output_total": None,
                   "n_missing_output": 2},
        "providers": {"p1": 1},
        "n_missing_provider": 1,
    }  # fmt: skip
    assert slices["lang=de"] == {
        "n_requests": 1,
        "insufficient_sample": True,
        "latency_ms": {"n": 0, "n_missing": 1, "mean": None, "p50": None, "p90": None,
                       "p95": None, "max": None, "reason": "no_latency"},
        "cost": {"n": 0, "n_missing": 1, "total": None, "mean_per_request": None,
                 "currency": "USD", "reason": "no_cost"},
        "tokens": {"input_total": None, "n_missing_input": 1, "output_total": None,
                   "n_missing_output": 1},
        "providers": {},
        "n_missing_provider": 1,
    }  # fmt: skip


def measurement_report(
    tmp_path: Path, records: list[Any], currency: str | None = "USD"
) -> dict[str, Any]:
    d = tmp_path / "case"
    write_jsonl(d / "r.jsonl", [SCORABLE])
    write_jsonl(d / "m.jsonl", records)
    manifest: dict[str, Any] = {
        "candidate": "x",
        "evaluations": [{"name": "ev", "kind": "retrieval", "path": "r.jsonl"}],
        "measurements": "m.jsonl",
        "bootstrap": {"resamples": 0},
    }
    if currency is not None:
        manifest["currency"] = currency
    return report_of(write_manifest(d, manifest))


@pytest.mark.parametrize(
    ("latencies", "expected"),
    [
        ([7, 3, 20, 1, 15, 9, 12, 2, 18, 5, 11, 4, 19, 6, 14, 8, 16, 10, 13, 17],
         {"mean": 10.5, "p50": 10, "p90": 18, "p95": 19, "max": 20}),
        ([70, 10, 50, 30, 60, 20, 40],
         {"mean": 40, "p50": 40, "p90": 70, "p95": 70, "max": 70}),
        ([2.5], {"mean": 2.5, "p50": 2.5, "p90": 2.5, "p95": 2.5, "max": 2.5}),
    ],
    ids=["twenty", "seven", "one"],
)  # fmt: skip
def test_ac6_latency_nearest_rank(
    tmp_path: Path, latencies: list[float], expected: dict[str, float]
) -> None:
    records = [{"request_id": f"r{i}", "latency_ms": v} for i, v in enumerate(latencies)]
    report = measurement_report(tmp_path, records)
    lat = report["measurements"]["slices"]["all"]["latency_ms"]
    assert lat == {"n": len(latencies), "n_missing": 0, **expected, "reason": None}


def test_ac6_empty_measurements_file(tmp_path: Path) -> None:
    report = measurement_report(tmp_path, [])
    meas = report["measurements"]
    assert meas["reason"] is None
    assert meas["path"] == "m.jsonl"
    assert list(meas["slices"]) == ["all"]
    assert meas["slices"]["all"] == {
        "n_requests": 0,
        "insufficient_sample": True,
        "latency_ms": {"n": 0, "n_missing": 0, "mean": None, "p50": None, "p90": None,
                       "p95": None, "max": None, "reason": "no_latency"},
        "cost": {"n": 0, "n_missing": 0, "total": None, "mean_per_request": None,
                 "currency": "USD", "reason": "no_cost"},
        "tokens": {"input_total": None, "n_missing_input": 0, "output_total": None,
                   "n_missing_output": 0},
        "providers": {},
        "n_missing_provider": 0,
    }  # fmt: skip
    assert report["insufficient_slices"] == ["ev/all", "measurements/all"]


def test_ac6_no_cost_without_currency(tmp_path: Path) -> None:
    report = measurement_report(tmp_path, [{"request_id": "a", "latency_ms": 5}], currency=None)
    meas = report["measurements"]
    assert meas["currency"] is None
    cost = meas["slices"]["all"]["cost"]
    assert cost == {"n": 0, "n_missing": 1, "total": None, "mean_per_request": None,
                    "currency": None, "reason": "no_cost"}  # fmt: skip


def test_ac6_not_measured(tmp_path: Path) -> None:
    report = single_eval_report(tmp_path, [SCORABLE], {"resamples": 0})
    assert report["measurements"] == {
        "path": None,
        "input_sha256": None,
        "currency": None,
        "reason": "not_measured",
        "slices": {},
    }
    assert report["insufficient_slices"] == ["ev/all"]


def test_measurement_parse_and_dataclass() -> None:
    from backend.evaluation import Measurement, parse_measurement

    m = parse_measurement({"request_id": "a"})
    assert isinstance(m, Measurement)
    assert (m.request_id, m.latency_ms, m.input_tokens, m.output_tokens) == ("a", None, None, None)
    assert (m.cost, m.provider) == (None, None)
    assert dict(m.slices) == {}
    full = parse_measurement({"request_id": "b", "latency_ms": 5.5, "input_tokens": 2,
                              "output_tokens": 0, "cost": 0, "provider": "p",
                              "slices": {"lang": "fa"}, "extra": [1]})  # fmt: skip
    assert (full.latency_ms, full.input_tokens, full.output_tokens) == (5.5, 2, 0)
    assert (full.cost, full.provider, dict(full.slices)) == (0, "p", {"lang": "fa"})
    assert dataclasses.is_dataclass(full)
    with pytest.raises(dataclasses.FrozenInstanceError):
        full.request_id = "c"  # type: ignore[misc]


@pytest.mark.parametrize(
    "record",
    [
        {},
        {"request_id": "  "},
        {"request_id": 5},
        {"request_id": "a", "latency_ms": -1},
        {"request_id": "a", "latency_ms": "5"},
        {"request_id": "a", "latency_ms": True},
        {"request_id": "a", "latency_ms": float("nan")},
        {"request_id": "a", "latency_ms": float("inf")},
        {"request_id": "a", "input_tokens": 1.5},
        {"request_id": "a", "input_tokens": True},
        {"request_id": "a", "output_tokens": -1},
        {"request_id": "a", "cost": -0.5},
        {"request_id": "a", "cost": False},
        {"request_id": "a", "provider": " "},
        {"request_id": "a", "provider": 3},
        {"request_id": "a", "slices": {"lang": 1}},
        {"request_id": "a", "slices": ["fa"]},
    ],
    ids=[
        "no_request_id", "blank_request_id", "int_request_id", "negative_latency",
        "string_latency", "bool_latency", "nan_latency", "inf_latency", "float_input_tokens",
        "bool_input_tokens", "negative_output_tokens", "negative_cost", "bool_cost",
        "blank_provider", "int_provider", "non_string_slice_value", "list_slices",
    ],
)  # fmt: skip
def test_parse_measurement_rejects(record: dict[str, Any]) -> None:
    from backend.evaluation import parse_measurement

    with pytest.raises(ValueError):
        parse_measurement(record)


def test_load_measurements_file_rules(tmp_path: Path) -> None:
    from backend.evaluation import load_measurements

    good = write_lines(tmp_path / "good.jsonl", [
        '{"request_id": "a", "latency_ms": 0}', "", "   ",
        '{"request_id": "b", "latency_ms": 5.5, "unknown": 1}',
    ])  # fmt: skip
    loaded = load_measurements(good)
    assert [m.request_id for m in loaded] == ["a", "b"]
    assert [m.latency_ms for m in loaded] == [0, 5.5]
    bad_lines = {
        "nan": '{"request_id": "b", "latency_ms": NaN}',
        "json": '{"request_id": ',
        "missing": '{"latency_ms": 1}',
    }
    for label, line in bad_lines.items():
        path = write_lines(tmp_path / f"{label}.jsonl", ['{"request_id": "a"}', "", line])
        with pytest.raises(ValueError, match="line 3"):
            load_measurements(path)
    utf8 = tmp_path / "utf8.jsonl"
    utf8.write_bytes(b'{"request_id": "a"}\n{"request_id": "\xff"}\n')
    with pytest.raises(ValueError, match="line 2"):
        load_measurements(utf8)


def base_case_dir(d: Path) -> None:
    write_jsonl(d / "data" / "retrieval.jsonl", RETRIEVAL[:3])
    write_jsonl(d / "data" / "agreement.jsonl", AGREEMENT[:3])
    write_jsonl(d / "data" / "good.jsonl", RETRIEVAL[3:])


def base_case_manifest() -> dict[str, Any]:
    return {
        "candidate": "x",
        "evaluations": [{"name": "ret", "kind": "retrieval", "path": "data/retrieval.jsonl"}],
        "slices": ["lang"],
        "bootstrap": {"resamples": 5},
    }


Mutation = Callable[[dict[str, Any], Path], None]


def top(key: str, value: Any) -> Mutation:
    def apply(m: dict[str, Any], d: Path) -> None:
        m[key] = value

    return apply


def drop(key: str) -> Mutation:
    def apply(m: dict[str, Any], d: Path) -> None:
        m.pop(key, None)

    return apply


def boot(key: str, value: Any) -> Mutation:
    def apply(m: dict[str, Any], d: Path) -> None:
        m["bootstrap"] = {key: value}

    return apply


def ev(key: str, value: Any) -> Mutation:
    def apply(m: dict[str, Any], d: Path) -> None:
        m["evaluations"][0][key] = value

    return apply


def ev_drop(key: str) -> Mutation:
    def apply(m: dict[str, Any], d: Path) -> None:
        m["evaluations"][0].pop(key)

    return apply


def agreement_args(args: list[str]) -> Mutation:
    def apply(m: dict[str, Any], d: Path) -> None:
        m["evaluations"] = [
            {"name": "agr", "kind": "qa_agreement", "path": "data/agreement.jsonl", "args": args}
        ]

    return apply


def second_eval(name: str) -> Mutation:
    def apply(m: dict[str, Any], d: Path) -> None:
        m["evaluations"].append({"name": name, "kind": "retrieval", "path": "data/good.jsonl"})

    return apply


def meas_lines(lines: list[str], currency: str | None = "USD") -> Mutation:
    def apply(m: dict[str, Any], d: Path) -> None:
        write_lines(d / "data" / "m.jsonl", lines)
        m["measurements"] = "data/m.jsonl"
        if currency is None:
            m.pop("currency", None)
        else:
            m["currency"] = currency

    return apply


def eval_file_lines(lines: list[str]) -> Mutation:
    def apply(m: dict[str, Any], d: Path) -> None:
        write_lines(d / "data" / "e.jsonl", lines)
        m["evaluations"][0]["path"] = "data/e.jsonl"

    return apply


def both(*mutations: Mutation) -> Mutation:
    def apply(m: dict[str, Any], d: Path) -> None:
        for mut in mutations:
            mut(m, d)

    return apply


OK_LINE = '{"request_id": "a"}'
GOOD_RET = json.dumps(RETRIEVAL[0])

CASES: list[tuple[str, Mutation, Mutation, str | None]] = [
    ("top_unknown_key", top("min_slice_szie", 5), top("min_slice_size", 5), "min_slice_szie"),
    ("bootstrap_unknown_key", boot("resample", 5), boot("resamples", 5), "resample"),
    ("evaluation_unknown_key", ev("arg", ["--ks", "1"]), ev("args", ["--ks", "1"]), "arg"),
    ("candidate_blank", top("candidate", "  "), top("candidate", "x"), "candidate"),
    ("candidate_int", top("candidate", 5), top("candidate", "5"), "candidate"),
    ("candidate_missing", drop("candidate"), top("candidate", "x"), "candidate"),
    ("evaluations_empty", top("evaluations", []), second_eval("other"), "evaluations"),
    ("evaluations_missing", drop("evaluations"), top("slices", []), "evaluations"),
    ("evaluations_object", top("evaluations", {"name": "ret"}), top("slices", []), "evaluations"),
    ("duplicate_name", second_eval("ret"), second_eval("ret2"), None),
    ("kind_unknown", ev("kind", "retrieve"), ev("kind", "retrieval"), "retrieve"),
    ("kind_missing", ev_drop("kind"), ev("kind", "retrieval"), "kind"),
    ("name_blank", ev("name", "  "), ev("name", "r"), "name"),
    ("name_missing", ev_drop("name"), ev("name", "r"), "name"),
    ("path_missing", ev_drop("path"), ev("path", "data/good.jsonl"), "path"),
    ("path_int", ev("path", 5), ev("path", "data/good.jsonl"), "path"),
    ("args_slices", ev("args", ["--slices", "lang"]), ev("args", ["--ks", "1,3"]), "slices"),
    ("args_slices_equals", ev("args", ["--slices=lang"]), ev("args", ["--ks=1,3"]), "slices"),
    ("args_slices_prefix", ev("args", ["--sl", "lang"]), ev("args", ["--ks", "1,3"]), None),
    ("args_ks_zero", ev("args", ["--ks", "0"]), ev("args", ["--ks", "1"]), None),
    ("args_ks_text", ev("args", ["--ks", "x"]), ev("args", ["--ks", "1"]), None),
    ("args_ks_empty", ev("args", ["--ks", ""]), ev("args", ["--ks", "1"]), None),
    ("args_unknown_option", ev("args", ["--bogus"]), ev("args", ["--ks", "1"]), None),
    ("args_positional", ev("args", ["extra"]), ev("args", []), None),
    ("args_not_list", ev("args", "--ks 1"), ev("args", ["--ks", "1"]), "args"),
    ("args_non_string", ev("args", ["--ks", 1]), ev("args", ["--ks", "1"]), "args"),
    ("weights_linear_no_categories", agreement_args(["--weights", "linear"]),
     agreement_args(["--weights", "linear", "--categories", "a,b,c"]), None),
    ("weights_quadratic_no_categories", agreement_args(["--weights", "quadratic"]),
     agreement_args(["--weights", "quadratic", "--categories", "a,b,c"]), None),
    ("confidence_one", boot("confidence", 1), boot("confidence", 0.9), "confidence"),
    ("confidence_zero", boot("confidence", 0), boot("confidence", 0.9), "confidence"),
    ("confidence_above_one", boot("confidence", 1.5), boot("confidence", 0.9), "confidence"),
    ("confidence_true", boot("confidence", True), boot("confidence", 0.9), "confidence"),
    ("confidence_string", boot("confidence", "0.9"), boot("confidence", 0.9), "confidence"),
    ("resamples_negative", boot("resamples", -1), boot("resamples", 0), "resamples"),
    ("resamples_true", boot("resamples", True), boot("resamples", 0), "resamples"),
    ("resamples_float", boot("resamples", 1.5), boot("resamples", 0), "resamples"),
    ("seed_true", boot("seed", True), boot("seed", 3), "seed"),
    ("seed_float", boot("seed", 1.5), boot("seed", 3), "seed"),
    ("bootstrap_list", top("bootstrap", []), top("bootstrap", {}), "bootstrap"),
    ("min_slice_size_zero", top("min_slice_size", 0), top("min_slice_size", 1), "min_slice_size"),
    ("min_slice_size_true", top("min_slice_size", True), top("min_slice_size", 1),
     "min_slice_size"),
    ("min_slice_size_float", top("min_slice_size", 2.5), top("min_slice_size", 2),
     "min_slice_size"),
    ("slices_not_list", top("slices", "lang"), top("slices", ["lang"]), "slices"),
    ("slices_blank_entry", top("slices", ["  "]), top("slices", ["lang"]), "slices"),
    ("slices_int_entry", top("slices", [1]), top("slices", ["lang"]), "slices"),
    ("config_list", top("config", [1]), top("config", {"a": 1}), "config"),
    ("config_string", top("config", "x"), top("config", None), "config"),
    ("currency_blank", top("currency", "  "), top("currency", "USD"), "currency"),
    ("currency_int", top("currency", 5), top("currency", None), "currency"),
    ("measurements_int", top("measurements", 5), meas_lines([OK_LINE]), "measurements"),
    ("measurements_missing_file", top("measurements", "data/none.jsonl"),
     meas_lines([OK_LINE]), None),
    ("cost_without_currency", meas_lines(['{"request_id": "a", "cost": 1.5}'], currency=None),
     meas_lines(['{"request_id": "a", "cost": 1.5}'], currency="USD"), None),
    ("latency_negative", meas_lines([OK_LINE, '{"request_id": "b", "latency_ms": -1}']),
     meas_lines(['{"request_id": "a", "latency_ms": 0}', '{"request_id": "b", "latency_ms": 5.5}']),
     "line 2"),
    ("latency_string", meas_lines([OK_LINE, '{"request_id": "b", "latency_ms": "5"}']),
     meas_lines([OK_LINE, '{"request_id": "b", "latency_ms": 5}']), "line 2"),
    ("latency_true", meas_lines([OK_LINE, '{"request_id": "b", "latency_ms": true}']),
     meas_lines([OK_LINE, '{"request_id": "b", "latency_ms": 5}']), "line 2"),
    ("latency_nan", meas_lines([OK_LINE, '{"request_id": "b", "latency_ms": NaN}']),
     meas_lines([OK_LINE, '{"request_id": "b", "latency_ms": 5.5}']), "line 2"),
    ("latency_infinity", meas_lines([OK_LINE, '{"request_id": "b", "latency_ms": Infinity}']),
     meas_lines([OK_LINE, '{"request_id": "b", "latency_ms": 5.5}']), "line 2"),
    ("input_tokens_float", meas_lines([OK_LINE, '{"request_id": "b", "input_tokens": 1.5}']),
     meas_lines([OK_LINE, '{"request_id": "b", "input_tokens": 2}']), "line 2"),
    ("output_tokens_negative", meas_lines([OK_LINE, '{"request_id": "b", "output_tokens": -1}']),
     meas_lines([OK_LINE, '{"request_id": "b", "output_tokens": 0}']), "line 2"),
    ("cost_negative", meas_lines([OK_LINE, '{"request_id": "b", "cost": -0.5}']),
     meas_lines([OK_LINE, '{"request_id": "b", "cost": 0}']), "line 2"),
    ("request_id_missing", meas_lines([OK_LINE, '{"latency_ms": 1}']),
     meas_lines([OK_LINE, '{"request_id": "b", "latency_ms": 1}']), "line 2"),
    ("request_id_blank", meas_lines([OK_LINE, '{"request_id": " "}']),
     meas_lines([OK_LINE, '{"request_id": "b"}']), "line 2"),
    ("provider_blank", meas_lines([OK_LINE, '{"request_id": "b", "provider": ""}']),
     meas_lines([OK_LINE, '{"request_id": "b", "provider": "p"}']), "line 2"),
    ("measurement_slices_non_string",
     meas_lines([OK_LINE, '{"request_id": "b", "slices": {"lang": 1}}']),
     meas_lines([OK_LINE, '{"request_id": "b", "slices": {"lang": "fa"}}']), "line 2"),
    ("measurement_bad_json", meas_lines([OK_LINE, '{"request_id": ']),
     meas_lines([OK_LINE, "", '{"request_id": "b"}']), "line 2"),
    ("evaluation_missing_file", ev("path", "data/nope.jsonl"), ev("path", "data/good.jsonl"),
     None),
    ("evaluation_bad_jsonl", eval_file_lines([GOOD_RET, '{"query_id": "q"}']),
     eval_file_lines([GOOD_RET, "", GOOD_RET]), "line 2"),
]  # fmt: skip

CASE_IDS = [c[0] for c in CASES]


def build_case(tmp_path: Path, mutation: Mutation) -> Path:
    d = tmp_path / "case"
    base_case_dir(d)
    manifest = base_case_manifest()
    mutation(manifest, d)
    return write_manifest(d, manifest)


def assert_data_error(proc: subprocess.CompletedProcess[bytes], needle: str | None) -> None:
    err = proc.stderr.decode("utf-8")
    assert proc.returncode == 1, (proc.returncode, err)
    assert proc.stdout == b""
    lines = err.splitlines()
    assert len(lines) == 1, err
    assert lines[0].startswith("error: "), err
    assert "Traceback" not in err
    assert "usage:" not in err
    if needle is not None:
        assert needle in lines[0], err


@pytest.mark.parametrize(("case_id", "deny", "allow", "needle"), CASES, ids=CASE_IDS)
def test_ac7_deny(
    tmp_path: Path, case_id: str, deny: Mutation, allow: Mutation, needle: str | None
) -> None:
    proc = run_candidate(build_case(tmp_path, deny))
    assert_data_error(proc, needle)


@pytest.mark.parametrize(("case_id", "deny", "allow", "needle"), CASES, ids=CASE_IDS)
def test_ac7_allow(
    tmp_path: Path, case_id: str, deny: Mutation, allow: Mutation, needle: str | None
) -> None:
    proc = run_candidate(build_case(tmp_path, allow))
    assert proc.returncode == 0, proc.stderr.decode()
    report = json.loads(proc.stdout)
    assert report["version"] == "candidate-report/1"


def test_ac7_allow_cost_with_currency_counts(tmp_path: Path) -> None:
    proc = run_candidate(build_case(tmp_path, meas_lines(['{"request_id": "a", "cost": 1.5}'])))
    assert proc.returncode == 0, proc.stderr.decode()
    cost = json.loads(proc.stdout)["measurements"]["slices"]["all"]["cost"]
    assert (cost["n"], cost["total"], cost["currency"]) == (1, 1.5, "USD")


@pytest.mark.parametrize(
    ("label", "content"),
    [
        ("not_json", "{"),
        ("list", "[]"),
        ("string", '"x"'),
        ("bad_utf8", None),
    ],
    ids=["not_json", "list", "string", "bad_utf8"],
)
def test_ac7_bad_manifest_file(tmp_path: Path, label: str, content: str | None) -> None:
    path = tmp_path / f"{label}.json"
    if content is None:
        path.write_bytes(b'{"candidate": "\xff"}')
    else:
        path.write_text(content, encoding="utf-8")
    assert_data_error(run_candidate(path), None)


def test_ac7_missing_manifest_file(tmp_path: Path) -> None:
    assert_data_error(run_candidate(tmp_path / "absent.json"), None)
    assert run_candidate(build_case(tmp_path, top("slices", []))).returncode == 0


@pytest.mark.parametrize(
    "argv",
    [[], ["a.json", "b.json"], ["--bogus", "a.json"]],
    ids=["no_args", "two_args", "unknown_flag"],
)
def test_cli_bad_arguments_exit_2(argv: list[str]) -> None:
    proc = run_candidate(*argv)
    assert proc.returncode == 2
    assert proc.stdout == b""


def test_ac9_determinism_and_paths_as_written(tmp_path: Path) -> None:
    first_dir = tmp_path / "one" / "case"
    second_dir = tmp_path / "two" / "deeper" / "case"
    path = write_main(first_dir)
    manifest = main_manifest()
    manifest["measurements"] = "./data/measurements.jsonl"
    manifest["evaluations"][1]["path"] = "./data/intent.jsonl"
    path = write_manifest(first_dir, manifest)
    shutil.copytree(first_dir, second_dir)
    a = run_candidate(path)
    b = run_candidate(path)
    c = run_candidate(second_dir / "candidate.json")
    assert a.returncode == 0, a.stderr.decode()
    assert a.stdout == b.stdout
    assert a.stdout == c.stdout
    text = a.stdout.decode("utf-8")
    assert str(tmp_path) not in text
    assert str(HOMAYAR_ROOT) not in text
    report = json.loads(text)
    assert report["measurements"]["path"] == "./data/measurements.jsonl"
    assert report["evaluations"]["cls"]["path"] == "./data/intent.jsonl"
    assert report["evaluations"]["ret"]["path"] == "data/retrieval.jsonl"


def test_ac9_relative_manifest_argument(tmp_path: Path) -> None:
    path = write_main(tmp_path / "case")
    rel = os.path.relpath(path.resolve(), HOMAYAR_ROOT)
    absolute = run_candidate(path)
    relative = run_candidate(rel)
    assert relative.returncode == 0, relative.stderr.decode()
    assert absolute.stdout == relative.stdout


@pytest.mark.parametrize(
    ("kind", "records", "args", "evaluate"),
    [
        ("retrieval", RETRIEVAL, ["--ks", "1,3"],
         lambda xs: evaluate_retrieval(xs, ks=[1, 3]).as_dict()),
        ("intent", INTENT, [], lambda xs: evaluate_classification(xs, labels=None).as_dict()),
        ("qa_agreement", AGREEMENT, ["--weights", "linear", "--categories", "a,b,c"],
         lambda xs: {"kappa": cohen_kappa(xs, **AGR_OPTS).as_dict(),
                     "by_criterion": {k: m.as_dict()
                                      for k, m in kappa_by_criterion(xs, **AGR_OPTS).items()}}),
        ("qa_score", [{"score": 1, "reference": 2.5, "slices": {"lang": "fa"}}], [],
         lambda xs: {"score_mae": score_mae(xs).as_dict()}),
    ],
    ids=["retrieval", "intent", "qa_agreement", "qa_score"],
)  # fmt: skip
def test_ac10_existing_cli_unchanged(
    tmp_path: Path,
    kind: str,
    records: list[Any],
    args: list[str],
    evaluate: Callable[[list[Any]], Any],
) -> None:
    path = write_jsonl(tmp_path / f"{kind}.jsonl", records)
    argv = [sys.executable, "-m", "backend.evaluation", kind, str(path), *args, "--slices", "lang"]
    proc = subprocess.run(argv, cwd=HOMAYAR_ROOT, capture_output=True, check=False)  # noqa: S603 - fixed argv, the package's own CLI
    assert proc.returncode == 0, proc.stderr.decode()
    options = {
        "retrieval": {"ks": [1, 3]},
        "intent": {"labels": None},
        "qa_agreement": dict(AGR_OPTS),
        "qa_score": {},
    }[kind]
    doc = {
        "version": EVALUATION_VERSION,
        "kind": kind,
        "input_sha256": sha256_file(path),
        "options": {**options, "slices": ["lang"]},
        "slices": evaluate_by_slice(load_jsonl(kind, path), evaluate, ["lang"]),
    }
    expected = json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False)
    assert proc.stdout == (expected + "\n").encode("utf-8")


@pytest.mark.parametrize(
    "argv",
    [["retrieval", "x.jsonl", "--ks", "0"], ["qa_agreement", "x.jsonl", "--weights", "linear"]],
    ids=["ks_zero", "weights_without_categories"],
)
def test_ac10_existing_cli_bad_arguments_still_exit_2(argv: list[str]) -> None:
    proc = subprocess.run([sys.executable, "-m", "backend.evaluation", *argv],  # noqa: S603 - fixed argv, the package's own CLI
                          cwd=HOMAYAR_ROOT, capture_output=True, check=False)  # fmt: skip
    assert proc.returncode == 2
    assert b"usage:" in proc.stderr


HOM351_FOLDERS = {
    "bge-m3-q8_0": {"retrieval_dense": "retrieval_dense.jsonl"},
    "qwen3-reranker-0.6b-q8_0": {
        "rerank_all": "retrieval_rerank_all.jsonl",
        "rerank_dense_top10": "retrieval_rerank_dense_top10.jsonl",
    },
}


@pytest.mark.parametrize("folder", sorted(HOM351_FOLDERS), ids=sorted(HOM351_FOLDERS))
def test_ac11_hom351_report_matches_fresh_run(folder: str) -> None:
    manifest = HOM351 / folder / "candidate.json"
    committed = HOM351 / folder / "candidate-report.json"
    assert manifest.is_file(), manifest
    assert committed.is_file(), committed
    rel = Path("..") / manifest.relative_to(REPO_ROOT)
    proc = run_candidate(rel)
    assert proc.returncode == 0, proc.stderr.decode()
    assert proc.stdout == committed.read_bytes()


@pytest.mark.parametrize("folder", sorted(HOM351_FOLDERS), ids=sorted(HOM351_FOLDERS))
def test_ac11_hom351_manifest_contents(folder: str) -> None:
    manifest_path = HOM351 / folder / "candidate.json"
    assert manifest_path.is_file(), manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result = json.loads((HOM351 / folder / "result.json").read_text(encoding="utf-8"))
    assert manifest["config"] == {
        k: result[k] for k in ("label", "mode", "note", "harness_version")
    }
    assert manifest["slices"] == ["language"]
    assert manifest.get("measurements") is None
    for key in ("bootstrap", "min_slice_size", "currency"):
        assert key not in manifest, key
    names = {e["name"]: e for e in manifest["evaluations"]}
    assert list(names) == list(HOM351_FOLDERS[folder])
    report = json.loads((HOM351 / folder / "candidate-report.json").read_text(encoding="utf-8"))
    for name, file in HOM351_FOLDERS[folder].items():
        assert names[name]["kind"] == "retrieval"
        assert names[name]["args"] == ["--ks", "1,3,5"]
        assert report["evaluations"][name]["input_sha256"] == sha256_file(HOM351 / folder / file)
    assert report["measurements"]["reason"] == "not_measured"


def test_ac11_bge_counts() -> None:
    path = HOM351 / "bge-m3-q8_0" / "candidate-report.json"
    assert path.is_file(), path
    report = json.loads(path.read_text(encoding="utf-8"))
    slices = report["evaluations"]["retrieval_dense"]["slices"]
    assert slices["all"]["n_input_items"] == 12
    assert slices["language=fa"]["n_input_items"] == 8
    assert slices["language=en"]["n_input_items"] == 4
    for key in ("all", "language=en", "language=fa"):
        assert f"retrieval_dense/{key}" in report["insufficient_slices"]
    assert report["measurements"]["reason"] == "not_measured"
    assert report["candidate"]
