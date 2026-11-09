"""The candidate report: manifest, per-slice metrics, bootstrap, measurements, CLI (HOM-70)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from backend.evaluation.__main__ import main as evaluation_main
from backend.evaluation.candidate import (
    CANDIDATE_REPORT_VERSION,
    Measurement,
    _interval,
    _Manifest,
    _nearest_rank,
    build_candidate_report,
    load_measurements,
    parse_measurement,
)
from backend.evaluation.candidate import (
    main as candidate_main,
)

FIXTURES = Path(__file__).parent / "fixtures" / "evaluation"
CAND = FIXTURES / "candidate"
HOMAYAR_ROOT = Path(__file__).resolve().parents[2]
HOM351 = HOMAYAR_ROOT.parent / "docs" / "research" / "hom-351-local-models" / "results"


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = candidate_main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def run_evaluation_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = evaluation_main(list(argv))
    out, err = capsys.readouterr()
    return code, out


def write_manifest(tmp_path: Path, manifest: dict[str, Any], name: str = "manifest.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


def write_jsonl(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def base_manifest(**extra: Any) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "candidate": "fixture-candidate",
        "evaluations": [
            {
                "name": "dense",
                "kind": "retrieval",
                "path": str(CAND / "retrieval.jsonl"),
                "args": ["--ks", "1,3"],
            },
            {"name": "intent", "kind": "intent", "path": str(CAND / "intent.jsonl")},
        ],
        "slices": ["language"],
        "min_slice_size": 2,
        "bootstrap": {"resamples": 40, "seed": 0, "confidence": 0.95},
    }
    manifest.update(extra)
    return manifest


def report_of(tmp_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return build_candidate_report(write_manifest(tmp_path, manifest))


# Public surface (SPEC 3.1).


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
    assert CANDIDATE_REPORT_VERSION == "candidate-report/1"


# Structure (AC1) and the manifest-relative fixture manifest.


def test_report_structure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest = base_manifest()
    manifest["candidate"] = "struct"
    manifest["currency"] = "USD"
    manifest["config"] = {"b": 2, "a": 1}
    manifest["measurements"] = str(CAND / "measurements.jsonl")
    manifest["slices"] = ["language", "tier"]
    path = write_manifest(tmp_path, manifest)
    code, out, err = run_cli(capsys, str(path))
    assert code == 0, err
    doc = json.loads(out)
    assert set(doc) == {
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
    assert doc["version"] == CANDIDATE_REPORT_VERSION
    assert doc["evaluation_version"] == "evaluation/1"
    assert doc["candidate"] == "struct"
    assert doc["config"] == {"a": 1, "b": 2}
    assert doc["slices"] == ["language", "tier"]
    assert doc["min_slice_size"] == 2
    assert doc["bootstrap"] == {
        "method": "percentile_bootstrap",
        "resamples": 40,
        "seed": 0,
        "confidence": 0.95,
    }
    assert set(doc["evaluations"]) == {"dense", "intent"}
    assert set(doc["evaluations"]["dense"]) == {"kind", "path", "input_sha256", "options", "slices"}
    assert doc["evaluations"]["dense"]["kind"] == "retrieval"
    assert doc["manifest_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert (
        doc["evaluations"]["dense"]["input_sha256"]
        == hashlib.sha256((CAND / "retrieval.jsonl").read_bytes()).hexdigest()
    )
    assert (
        doc["config_sha256"]
        == hashlib.sha256(
            json.dumps(
                {"a": 1, "b": 2}, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
    )
    assert set(doc["measurements"]) == {"path", "input_sha256", "currency", "reason", "slices"}
    assert doc["measurements"]["reason"] is None
    assert doc["measurements"]["currency"] == "USD"
    assert (
        doc["measurements"]["input_sha256"]
        == hashlib.sha256((CAND / "measurements.jsonl").read_bytes()).hexdigest()
    )


def test_config_null_has_no_sha256(tmp_path: Path) -> None:
    doc = report_of(tmp_path, base_manifest())
    assert doc["config"] is None
    assert doc["config_sha256"] is None


# Slices, counts and rates (AC2, AC3).


def test_slice_counts_and_metric_counts(tmp_path: Path) -> None:
    doc = report_of(tmp_path, base_manifest())
    ev = doc["evaluations"]["dense"]["slices"]
    assert set(ev) == {"all", "language=fa", "language=en"}
    assert ev["all"]["n_input_items"] == 7
    assert ev["language=fa"]["n_input_items"] == 4
    assert ev["language=en"]["n_input_items"] == 2
    mrr = ev["all"]["metrics"]["mrr"]
    assert mrr["value"] == pytest.approx(0.75)
    assert (mrr["n_items"], mrr["n_excluded"], mrr["n_abstained"]) == (6, 1, 1)
    assert mrr["abstention_rate"] == pytest.approx(1 / 6)
    assert mrr["coverage"] == pytest.approx(6 / 7)
    en = ev["language=en"]["metrics"]["mrr"]
    assert en["n_items"] == 1
    assert en["n_excluded"] == 1
    assert en["n_abstained"] == 0
    assert en["abstention_rate"] == 0.0
    assert en["coverage"] == 0.5
    intent = doc["evaluations"]["intent"]["slices"]
    assert intent["all"]["n_input_items"] == 3
    assert intent["language=fa"]["n_input_items"] == 2
    assert intent["language=en"]["n_input_items"] == 1


def test_metric_entry_fields(tmp_path: Path) -> None:
    doc = report_of(tmp_path, base_manifest())
    mrr = doc["evaluations"]["dense"]["slices"]["all"]["metrics"]["mrr"]
    assert set(mrr) == {
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
    assert set(mrr["interval"]) == {
        "method",
        "confidence",
        "resamples",
        "seed",
        "defined_resamples",
        "low",
        "high",
        "reason",
    }


def test_none_rates_for_a_metric_without_units(tmp_path: Path) -> None:
    doc = report_of(tmp_path, base_manifest())
    correct = doc["evaluations"]["intent"]["slices"]["all"]["metrics"]["correct_abstention_rate"]
    assert correct["value"] is None
    assert correct["reason"] == "no_abstention_fixtures"
    assert correct["n_items"] == 0
    assert correct["abstention_rate"] is None
    assert correct["coverage"] is None


def test_qa_agreement_metric_names(tmp_path: Path) -> None:
    manifest = base_manifest()
    manifest["evaluations"] = [
        {"name": "qa", "kind": "qa_agreement", "path": str(CAND / "qa_agreement.jsonl")}
    ]
    doc = report_of(tmp_path, manifest)
    metrics = doc["evaluations"]["qa"]["slices"]["all"]["metrics"]
    assert set(metrics) == {"kappa", "kappa[c1]", "kappa[c2]"}


# Uncertainty (AC4).


def test_nearest_rank_25_and_975_at_95() -> None:
    values = list(range(1, 1001))
    assert 1 - Fraction(str(0.95)) == Fraction(1, 20)
    assert _nearest_rank(values, Fraction(1, 40)) == 25
    assert _nearest_rank(values, Fraction(39, 40)) == 975
    assert _nearest_rank(values, Fraction(1, 2)) == 500
    assert _nearest_rank([3.0], Fraction(19, 20)) == 3.0


def test_intervals_are_ordered_and_fully_defined(tmp_path: Path) -> None:
    doc = report_of(tmp_path, base_manifest())
    seen = 0
    for evaluation in doc["evaluations"].values():
        for entry in evaluation["slices"].values():
            for metric in entry["metrics"].values():
                interval = metric["interval"]
                if interval["reason"] is None:
                    seen += 1
                    assert 0 < interval["defined_resamples"] <= 40
                    assert interval["low"] <= interval["high"]
                    assert interval["low"] is not None
    assert seen > 10


def test_bootstrap_disabled(tmp_path: Path) -> None:
    doc = report_of(tmp_path, base_manifest(bootstrap={"resamples": 0}))
    interval = doc["evaluations"]["dense"]["slices"]["all"]["metrics"]["mrr"]["interval"]
    assert interval == {
        "method": "percentile_bootstrap",
        "confidence": 0.95,
        "resamples": 0,
        "seed": 0,
        "defined_resamples": 0,
        "low": None,
        "high": None,
        "reason": "bootstrap_disabled",
    }


def test_metric_undefined(tmp_path: Path) -> None:
    manifest = base_manifest()
    manifest["evaluations"] = [
        {"name": "norel", "kind": "retrieval", "path": str(CAND / "retrieval_norel.jsonl")}
    ]
    doc = report_of(tmp_path, manifest)
    entry = doc["evaluations"]["norel"]["slices"]["all"]
    assert entry["n_input_items"] == 3
    for metric in entry["metrics"].values():
        assert metric["value"] is None
        assert metric["interval"]["reason"] == "metric_undefined"
        assert metric["interval"]["defined_resamples"] == 0
        assert metric["interval"]["low"] is None
    mrr = entry["metrics"]["mrr"]
    assert mrr["n_items"] == 0
    assert mrr["n_excluded"] == 3
    assert mrr["abstention_rate"] is None
    assert mrr["coverage"] == 0.0


def test_too_few_items(tmp_path: Path) -> None:
    doc = report_of(tmp_path, base_manifest())
    entry = doc["evaluations"]["intent"]["slices"]["language=en"]
    assert entry["n_input_items"] == 1
    for name, metric in entry["metrics"].items():
        if metric["value"] is None:
            assert name == "correct_abstention_rate"
            assert metric["interval"]["reason"] == "metric_undefined"
        else:
            assert metric["interval"]["reason"] == "too_few_items"
        assert metric["interval"]["defined_resamples"] == 0


def test_interval_reason_order_reports_zero_defined_resamples() -> None:
    manifest = _Manifest("c", (), None, None, None, (), 10, 0, 0.95, 1)
    disabled = dataclasses.replace(manifest, resamples=0)
    assert _interval(0.5, 5, [0.1, 0.2], disabled)["reason"] == "bootstrap_disabled"
    undefined = _interval(None, 5, [0.1, 0.2], manifest)
    assert undefined["reason"] == "metric_undefined"
    assert undefined["defined_resamples"] == 0
    few = _interval(0.5, 1, [0.1, 0.2], manifest)
    assert few["reason"] == "too_few_items"
    assert few["defined_resamples"] == 0
    assert _interval(0.5, 5, [], manifest)["reason"] == "no_defined_resamples"
    ok = _interval(0.5, 5, [0.2, 0.1], manifest)
    assert (ok["reason"], ok["defined_resamples"], ok["low"], ok["high"]) == (None, 2, 0.1, 0.2)


def test_no_defined_resamples(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path,
        "mix.jsonl",
        '{"query_id": "a", "relevance": {"d1": 1}, "ranked": ["d1"]}\n'
        '{"query_id": "b", "relevance": {"d2": 0}, "ranked": ["d2"]}\n',
    )
    # Seed 4 draws index 1 twice for "4:e:all", so the one resample holds only query b.
    manifest = base_manifest(bootstrap={"resamples": 1, "seed": 4})
    manifest["evaluations"] = [{"name": "e", "kind": "retrieval", "path": str(path)}]
    doc = report_of(tmp_path, manifest)
    entry = doc["evaluations"]["e"]["slices"]["all"]
    assert entry["n_input_items"] == 2
    for metric in entry["metrics"].values():
        assert metric["value"] is not None
        assert metric["interval"]["reason"] == "no_defined_resamples"
        assert metric["interval"]["defined_resamples"] == 0
        assert metric["interval"]["low"] is None
        assert metric["interval"]["high"] is None


def test_identical_items_give_a_degenerate_interval(tmp_path: Path) -> None:
    manifest = base_manifest()
    manifest["evaluations"] = [
        {
            "name": "same",
            "kind": "retrieval",
            "path": str(CAND / "retrieval_identical.jsonl"),
            "args": ["--ks", "1"],
        }
    ]
    doc = report_of(tmp_path, manifest)
    for metric in doc["evaluations"]["same"]["slices"]["all"]["metrics"].values():
        assert metric["value"] is not None
        assert metric["interval"]["reason"] is None
        assert metric["interval"]["low"] == metric["value"]
        assert metric["interval"]["high"] == metric["value"]


def test_same_seed_same_interval_different_seed_differs(tmp_path: Path) -> None:
    first = report_of(tmp_path, base_manifest(bootstrap={"resamples": 40, "seed": 7}))
    again = report_of(tmp_path, base_manifest(bootstrap={"resamples": 40, "seed": 7}))
    other = report_of(tmp_path, base_manifest(bootstrap={"resamples": 40, "seed": 8}))
    a = first["evaluations"]["dense"]["slices"]["all"]["metrics"]["mrr"]["interval"]
    b = again["evaluations"]["dense"]["slices"]["all"]["metrics"]["mrr"]["interval"]
    c = other["evaluations"]["dense"]["slices"]["all"]["metrics"]["mrr"]["interval"]
    assert a == b
    assert a != c


# Insufficient samples (AC5).


def test_insufficient_slices(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest = base_manifest()
    manifest["min_slice_size"] = 3
    manifest["measurements"] = str(CAND / "measurements.jsonl")
    manifest["currency"] = "USD"
    manifest["slices"] = ["language", "tier"]
    path = write_manifest(tmp_path, manifest)
    code, out, err = run_cli(capsys, str(path))
    assert code == 0, err
    doc = json.loads(out)
    assert doc["insufficient_slices"] == [
        "dense/language=en",
        "intent/language=en",
        "intent/language=fa",
        "measurements/language=en",
        "measurements/language=fa",
        "measurements/tier=cold",
    ]
    assert doc["evaluations"]["dense"]["slices"]["all"]["insufficient_sample"] is False
    assert doc["evaluations"]["dense"]["slices"]["language=en"]["insufficient_sample"] is True
    assert doc["measurements"]["slices"]["all"]["insufficient_sample"] is False
    assert doc["measurements"]["slices"]["tier=cold"]["insufficient_sample"] is True


# Measurements (AC6).


def test_measurement_records(tmp_path: Path) -> None:
    manifest = base_manifest()
    manifest["measurements"] = str(CAND / "measurements.jsonl")
    manifest["currency"] = "USD"
    manifest["slices"] = ["language", "tier"]
    doc = report_of(tmp_path, manifest)
    allm = doc["measurements"]["slices"]["all"]
    assert allm["n_requests"] == 4
    latency = allm["latency_ms"]
    assert latency["n"] == 3
    assert latency["n_missing"] == 1
    assert latency["mean"] == pytest.approx(200.0)
    assert latency["p50"] == 200.0
    assert latency["p90"] == 300.0
    assert latency["p95"] == 300.0
    assert latency["max"] == 300.0
    assert latency["reason"] is None
    cost = allm["cost"]
    assert cost["n"] == 2
    assert cost["n_missing"] == 2
    assert cost["total"] == pytest.approx(0.003)
    assert cost["mean_per_request"] == pytest.approx(0.0015)
    assert cost["currency"] == "USD"
    assert cost["reason"] is None
    tokens = allm["tokens"]
    assert tokens["input_total"] == 30
    assert tokens["n_missing_input"] == 2
    assert tokens["output_total"] == 12
    assert tokens["n_missing_output"] == 2
    assert allm["providers"] == {"llamacpp": 2, "openai": 1}
    assert allm["n_missing_provider"] == 1
    fa = doc["measurements"]["slices"]["language=fa"]
    assert fa["latency_ms"]["p50"] == 100.0
    assert fa["latency_ms"]["p90"] == 200.0
    assert fa["latency_ms"]["p95"] == 200.0
    assert fa["cost"]["n_missing"] == 0
    assert fa["providers"] == {"llamacpp": 2}
    cold = doc["measurements"]["slices"]["tier=cold"]
    assert cold["n_requests"] == 1
    assert cold["latency_ms"]["reason"] == "no_latency"
    assert cold["latency_ms"]["mean"] is None
    assert cold["latency_ms"]["n_missing"] == 1
    assert cold["cost"]["reason"] == "no_cost"
    assert cold["cost"]["total"] is None
    assert cold["cost"]["currency"] == "USD"
    assert cold["tokens"]["input_total"] is None
    assert cold["tokens"]["n_missing_input"] == 1
    assert cold["tokens"]["output_total"] is None
    assert cold["tokens"]["n_missing_output"] == 1
    assert cold["providers"] == {}
    assert cold["n_missing_provider"] == 1


def test_not_measured(tmp_path: Path) -> None:
    doc = report_of(tmp_path, base_manifest())
    assert doc["measurements"] == {
        "path": None,
        "input_sha256": None,
        "currency": None,
        "reason": "not_measured",
        "slices": {},
    }


def test_parse_measurement() -> None:
    minimal = parse_measurement({"request_id": "r1", "future_field": [1]})
    assert minimal == Measurement("r1")
    full = parse_measurement(
        {
            "request_id": "r1",
            "latency_ms": 5.5,
            "input_tokens": 2,
            "output_tokens": 3,
            "cost": 0,
            "provider": "llamacpp",
            "slices": {"language": "fa"},
        }
    )
    assert full == Measurement("r1", 5.5, 2, 3, 0, "llamacpp", {"language": "fa"})
    with pytest.raises(dataclasses.FrozenInstanceError):
        full.request_id = "r2"  # type: ignore[misc]


@pytest.mark.parametrize(
    "record",
    [
        {"latency_ms": 1},
        {"request_id": "  "},
        {"request_id": 5},
        {"request_id": "r", "latency_ms": -1},
        {"request_id": "r", "latency_ms": "5"},
        {"request_id": "r", "latency_ms": True},
        {"request_id": "r", "latency_ms": float("nan")},
        {"request_id": "r", "latency_ms": float("inf")},
        {"request_id": "r", "input_tokens": 1.5},
        {"request_id": "r", "input_tokens": True},
        {"request_id": "r", "output_tokens": -2},
        {"request_id": "r", "cost": -0.5},
        {"request_id": "r", "cost": "0.5"},
        {"request_id": "r", "provider": ""},
        {"request_id": "r", "provider": 5},
        {"request_id": "r", "slices": {"a": 1}},
        {"request_id": "r", "slices": ["a"]},
    ],
    ids=[
        "missing-request-id",
        "blank-request-id",
        "int-request-id",
        "negative-latency",
        "string-latency",
        "bool-latency",
        "nan-latency",
        "inf-latency",
        "float-input-tokens",
        "bool-input-tokens",
        "negative-output-tokens",
        "negative-cost",
        "string-cost",
        "blank-provider",
        "int-provider",
        "slices-not-strings",
        "slices-not-object",
    ],
)
def test_parse_measurement_refuses(record: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        parse_measurement(record)


def test_load_measurements_file_rules(tmp_path: Path) -> None:
    good = json.dumps({"request_id": "r1"})
    path = write_jsonl(tmp_path, "ok.jsonl", f"\n{good}\n   \n{good}\n")
    assert [m.request_id for m in load_measurements(path)] == ["r1", "r1"]
    path = write_jsonl(tmp_path, "bad.jsonl", f"{good}\nnot json\n")
    with pytest.raises(ValueError, match="line 2"):
        load_measurements(path)
    path = tmp_path / "utf8.jsonl"
    path.write_bytes(b'{"request_id": "r"}\n{"request_id": "\xff\xfe"}\n')
    with pytest.raises(ValueError, match="line 2"):
        load_measurements(path)


# Data errors (AC7): every deny case exits 1, one line, nothing on stdout.


def test_manifest_data_errors_exit_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    retrieval = str(CAND / "retrieval.jsonl")
    intent = str(CAND / "intent.jsonl")
    qa = str(CAND / "qa_agreement.jsonl")
    bad_jsonl = write_jsonl(tmp_path, "bad.jsonl", '{"query_id": "q"}\n')

    cases: list[tuple[str, dict[str, Any], str]] = [
        ("unknown-top-key", {"min_slice_szie": 1}, "min_slice_szie"),
        (
            "unknown-bootstrap-key",
            {"bootstrap": {"resamples": 10, "resample": 1}},
            "resample",
        ),
        (
            "unknown-evaluation-key",
            {"evaluations": [{"name": "e", "kind": "intent", "path": intent, "arg": []}]},
            "arg",
        ),
        ("blank-candidate", {"candidate": "  "}, "candidate"),
        ("empty-evaluations", {"evaluations": []}, "evaluations"),
        (
            "duplicate-names",
            {
                "evaluations": [
                    {"name": "e", "kind": "intent", "path": intent},
                    {"name": "e", "kind": "intent", "path": intent},
                ]
            },
            "duplicate",
        ),
        (
            "bad-kind",
            {"evaluations": [{"name": "e", "kind": "retrieve", "path": retrieval}]},
            "kind",
        ),
        (
            "slices-in-args",
            {
                "evaluations": [
                    {
                        "name": "dense",
                        "kind": "retrieval",
                        "path": retrieval,
                        "args": ["--slices", "language"],
                    }
                ]
            },
            "--slices",
        ),
        *[
            (
                f"slices-in-args-{i}",
                {
                    "evaluations": [
                        {"name": "dense", "kind": "retrieval", "path": retrieval, "args": args}
                    ]
                },
                "--slices",
            )
            for i, args in enumerate(
                [
                    ["--slices=language"],
                    ["--sl", "language"],
                    ["--slic", "language"],
                    ["--slices", ""],
                    ["--slices="],
                    ["--sl=language"],
                    ["--ks", "1", "--slice", "language"],
                ]
            )
        ],
        (
            "bad-ks",
            {
                "evaluations": [
                    {"name": "dense", "kind": "retrieval", "path": retrieval, "args": ["--ks", "0"]}
                ]
            },
            "ks",
        ),
        (
            "weights-without-categories",
            {
                "evaluations": [
                    {
                        "name": "qa",
                        "kind": "qa_agreement",
                        "path": qa,
                        "args": ["--weights", "linear"],
                    }
                ]
            },
            "categories",
        ),
        (
            "unknown-option",
            {
                "evaluations": [
                    {
                        "name": "dense",
                        "kind": "retrieval",
                        "path": retrieval,
                        "args": ["--top", "5"],
                    }
                ]
            },
            "--top",
        ),
        ("confidence-one", {"bootstrap": {"confidence": 1}}, "confidence"),
        ("confidence-zero", {"bootstrap": {"confidence": 0}}, "confidence"),
        ("negative-resamples", {"bootstrap": {"resamples": -1}}, "resamples"),
        ("bool-resamples", {"bootstrap": {"resamples": True}}, "resamples"),
        ("string-seed", {"bootstrap": {"seed": "x"}}, "seed"),
        ("zero-min-slice-size", {"min_slice_size": 0}, "min_slice_size"),
        ("bool-min-slice-size", {"min_slice_size": True}, "min_slice_size"),
        ("bad-config", {"config": [1]}, "config"),
        ("bad-currency", {"currency": ""}, "currency"),
        ("bad-measurements-path", {"measurements": 5}, "measurements"),
        ("bad-slices-entry", {"slices": [""]}, "slices"),
        ("repeated-slices", {"slices": ["language", "language"]}, "slices"),
        (
            "slash-in-name",
            {"evaluations": [{"name": "e/all", "kind": "intent", "path": intent}]},
            "name",
        ),
        (
            "missing-evaluation-file",
            {"evaluations": [{"name": "e", "kind": "intent", "path": "nope.jsonl"}]},
            "nope.jsonl",
        ),
        (
            "bad-jsonl-line",
            {"evaluations": [{"name": "e", "kind": "retrieval", "path": str(bad_jsonl)}]},
            "line 1",
        ),
    ]
    for label, patch, fragment in cases:
        path = write_manifest(tmp_path, base_manifest(**patch), name=f"{label}.json")
        code, out, err = run_cli(capsys, str(path))
        assert code == 1, (label, err)
        assert out == "", label
        assert err.startswith("error: "), (label, err)
        assert len(err.strip().splitlines()) == 1, (label, err)
        assert fragment in err, (label, err)

    deep = tmp_path / "deep.json"
    deep.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    code, out, err = run_cli(capsys, str(deep))
    assert code == 1
    assert out == ""
    assert err.startswith("error: ")
    assert len(err.strip().splitlines()) == 1

    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    code, out, err = run_cli(capsys, str(broken))
    assert code == 1
    assert out == ""
    assert err.startswith("error: ")
    assert len(err.strip().splitlines()) == 1

    code, out, err = run_cli(capsys, str(tmp_path / "missing-manifest.json"))
    assert code == 1
    assert out == ""
    assert err.startswith("error: ")
    assert len(err.strip().splitlines()) == 1


def test_manifest_allow_controls_load(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    retrieval = str(CAND / "retrieval.jsonl")
    intent = str(CAND / "intent.jsonl")
    qa = str(CAND / "qa_agreement.jsonl")
    cases = [
        {"min_slice_size": 1},
        {"bootstrap": {"resamples": 10}},
        {"bootstrap": {"confidence": 0.9}},
        {"bootstrap": {"resamples": 0}},
        {"candidate": "x"},
        {"slices": ["language"]},
        {"slices": ["language", "tier"]},
        {"evaluations": [{"name": "retrieval_dense", "kind": "intent", "path": intent}]},
        {"evaluations": [{"name": "rerank-all.v2", "kind": "intent", "path": intent}]},
        {"config": None},
        {"currency": None},
        {"evaluations": [{"name": "e", "kind": "intent", "path": intent}]},
        {
            "evaluations": [
                {"name": "e1", "kind": "intent", "path": intent},
                {"name": "e2", "kind": "retrieval", "path": retrieval, "args": ["--ks", "1"]},
            ]
        },
        {
            "evaluations": [
                {"name": "dense", "kind": "retrieval", "path": retrieval, "args": ["--ks", "1,3"]}
            ]
        },
        {
            "evaluations": [
                {
                    "name": "qa",
                    "kind": "qa_agreement",
                    "path": qa,
                    "args": ["--weights", "linear", "--categories", "a,b"],
                }
            ]
        },
    ]
    for i, patch in enumerate(cases):
        path = write_manifest(tmp_path, base_manifest(**patch), name=f"allow{i}.json")
        code, out, err = run_cli(capsys, str(path))
        assert code == 0, (i, err)
        json.loads(out)


def test_measurements_data_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cases: list[tuple[str, str, str]] = [
        ("cost-without-currency", '{"request_id": "r1", "cost": 0.5}\n', "currency"),
        ("negative-latency", '{"request_id": "r1", "latency_ms": -1}\n', "line 1"),
        ("string-latency", '{"request_id": "r1", "latency_ms": "5"}\n', "line 1"),
        ("bool-latency", '{"request_id": "r1", "latency_ms": true}\n', "line 1"),
        ("nan-latency", '{"request_id": "r1", "latency_ms": NaN}\n', "line 1"),
        ("float-input-tokens", '{"request_id": "r1", "input_tokens": 1.5}\n', "line 1"),
        ("missing-request-id", '{"latency_ms": 1}\n', "request_id"),
    ]
    for label, text, fragment in cases:
        measurements = write_jsonl(tmp_path, "m.jsonl", text)
        path = write_manifest(
            tmp_path, base_manifest(measurements=str(measurements)), name=f"{label}.json"
        )
        code, out, err = run_cli(capsys, str(path))
        assert code == 1, (label, err)
        assert out == "", label
        assert err.startswith("error: "), (label, err)
        assert len(err.strip().splitlines()) == 1, (label, err)
        assert fragment in err, (label, err)

    ok = write_jsonl(
        tmp_path,
        "ok.jsonl",
        '{"request_id": "r1", "latency_ms": 0, "cost": 0.25}\n'
        '{"request_id": "r2", "latency_ms": 5.5, "input_tokens": 2}\n',
    )
    path = write_manifest(tmp_path, base_manifest(measurements=str(ok), currency="USD"))
    code, out, err = run_cli(capsys, str(path))
    assert code == 0, err
    doc = json.loads(out)
    assert doc["measurements"]["reason"] is None
    assert doc["measurements"]["slices"]["all"]["n_requests"] == 2


# Detail equality with the existing CLI (AC8).


def test_detail_equals_the_cli_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run_evaluation_cli(
        capsys, "retrieval", str(CAND / "retrieval.jsonl"), "--ks", "1,3", "--slices", "language"
    )
    assert code == 0
    doc = json.loads(out)
    report = report_of(tmp_path, base_manifest())
    dense = report["evaluations"]["dense"]
    assert dense["options"] == doc["options"]
    assert set(dense["slices"]) == set(doc["slices"])
    for key, detail in doc["slices"].items():
        assert dense["slices"][key]["detail"] == detail

    code, out = run_evaluation_cli(
        capsys, "qa_agreement", str(CAND / "qa_agreement.jsonl"), "--slices", "language"
    )
    assert code == 0
    doc = json.loads(out)
    manifest = base_manifest()
    manifest["evaluations"].append(
        {"name": "qa", "kind": "qa_agreement", "path": str(CAND / "qa_agreement.jsonl")}
    )
    report = report_of(tmp_path, manifest)
    qa = report["evaluations"]["qa"]
    assert qa["options"] == doc["options"]
    assert set(qa["slices"]) == set(doc["slices"])
    for key, detail in doc["slices"].items():
        assert qa["slices"][key]["detail"] == detail


# Determinism and paths (AC9).


def test_two_runs_print_the_same_bytes(capsys: pytest.CaptureFixture[str]) -> None:
    manifest = str(CAND / "manifest.json")
    first = run_cli(capsys, manifest)
    second = run_cli(capsys, manifest)
    assert first[0] == second[0] == 0
    assert first[1] == second[1]
    doc = json.loads(first[1])
    assert doc["candidate"] == "fixture-candidate"
    assert doc["evaluations"]["dense"]["path"] == "retrieval.jsonl"
    assert doc["evaluations"]["intent"]["path"] == "intent.jsonl"


def test_cli_subprocess_is_deterministic() -> None:
    args = [sys.executable, "-m", "backend.evaluation.candidate", str(CAND / "manifest.json")]
    first = subprocess.run(  # noqa: S603 - fixed argv, the package's own CLI
        args, cwd=HOMAYAR_ROOT, capture_output=True, check=True
    )
    second = subprocess.run(  # noqa: S603 - fixed argv, the package's own CLI
        args, cwd=HOMAYAR_ROOT, capture_output=True, check=True
    )
    assert first.stdout == second.stdout


def test_evaluation_cli_still_accepts_abbreviated_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = str(CAND / "retrieval.jsonl")
    full = run_evaluation_cli(capsys, "retrieval", path, "--ks", "1,3", "--slices", "language")
    short = run_evaluation_cli(capsys, "retrieval", path, "--k", "1,3", "--sl", "language")
    assert full[0] == short[0] == 0
    assert full[1] == short[1]


def test_lazy_exports_import() -> None:
    from backend.evaluation import CANDIDATE_REPORT_VERSION as version
    from backend.evaluation import build_candidate_report as build

    assert version == "candidate-report/1"
    assert build is build_candidate_report
    with pytest.raises(AttributeError):
        import backend.evaluation as ev

        ev.no_such_name  # noqa: B018


def test_cli_subprocess_stderr(tmp_path: Path) -> None:
    ok = subprocess.run(  # noqa: S603 - fixed argv, the package's own CLI
        [sys.executable, "-m", "backend.evaluation.candidate", str(CAND / "manifest.json")],
        cwd=HOMAYAR_ROOT,
        capture_output=True,
        check=False,
    )
    assert ok.returncode == 0
    assert ok.stderr == b""
    bad = write_manifest(tmp_path, base_manifest(min_slice_szie=1))
    failed = subprocess.run(  # noqa: S603 - fixed argv, the package's own CLI
        [sys.executable, "-m", "backend.evaluation.candidate", str(bad)],
        cwd=HOMAYAR_ROOT,
        capture_output=True,
        check=False,
    )
    assert failed.returncode == 1
    assert failed.stdout == b""
    lines = failed.stderr.decode("utf-8").splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: ")
    assert "min_slice_szie" in lines[0]


def test_cli_bad_arguments_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        candidate_main([])
    assert exc.value.code == 2
    capsys.readouterr()


# The real HOM-351 examples (AC11).


def test_hom351_bge_report() -> None:
    folder = HOM351 / "bge-m3-q8_0"
    doc = json.loads((folder / "candidate-report.json").read_text(encoding="utf-8"))
    ev = doc["evaluations"]["retrieval_dense"]["slices"]
    assert set(ev) == {"all", "language=fa", "language=en"}
    assert ev["all"]["n_input_items"] == 12
    assert ev["language=fa"]["n_input_items"] == 8
    assert ev["language=en"]["n_input_items"] == 4
    assert doc["insufficient_slices"] == [
        "retrieval_dense/all",
        "retrieval_dense/language=en",
        "retrieval_dense/language=fa",
    ]
    assert doc["measurements"]["reason"] == "not_measured"
    assert doc["candidate"] == "bge-m3-q8_0"


def test_hom351_reranker_report() -> None:
    folder = HOM351 / "qwen3-reranker-0.6b-q8_0"
    doc = json.loads((folder / "candidate-report.json").read_text(encoding="utf-8"))
    assert set(doc["evaluations"]) == {"rerank_all", "rerank_dense_top10"}
    for name in doc["evaluations"]:
        ev = doc["evaluations"][name]["slices"]
        assert ev["all"]["n_input_items"] == 12
        assert ev["language=fa"]["n_input_items"] == 8
        assert ev["language=en"]["n_input_items"] == 4
