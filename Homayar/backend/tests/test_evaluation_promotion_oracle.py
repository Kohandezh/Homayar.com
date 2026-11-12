"""Acceptance oracle for the promotion gate, ledger and corrections (HOM-71), from SPEC.md alone."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from backend.evaluation.loaders import KINDS, load_jsonl, parse_record

HOMAYAR_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = HOMAYAR_ROOT / "backend" / "tests" / "fixtures" / "evaluation"
CLI_ENV = {
    **os.environ,
    "PYTHONPATH": os.pathsep.join(
        p for p in (str(HOMAYAR_ROOT), os.environ.get("PYTHONPATH", "")) if p
    ),
}
ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

DECISION_KEYS = {
    "version",
    "decision",
    "criteria_id",
    "criteria_sha256",
    "candidate",
    "candidate_config_sha256",
    "candidate_report_sha256",
    "incumbent",
    "incumbent_config_sha256",
    "incumbent_report_sha256",
    "rollback_manifest_sha256",
    "gates",
    "reasons",
}
GATE_KEYS = {
    "evaluation",
    "metric",
    "slice",
    "direction",
    "threshold",
    "n_items",
    "tolerance",
    "max_regression",
    "candidate_value",
    "incumbent_value",
    "reproduction_values",
    "passed",
    "reasons",
}
PROMOTION_KEYS = {
    "type",
    "sequence",
    "at",
    "version",
    "config_sha256",
    "criteria_sha256",
    "candidate_report_sha256",
    "decision_sha256",
    "rollback_manifest",
}
ROLLBACK_KEYS = {
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

WER = ("evaluations", "asr", "slices", "all", "metrics", "wer", "value")
WER_SLICE = ("evaluations", "asr", "slices", "all")
ACC = ("evaluations", "intent", "slices", "language=fa", "metrics", "accuracy", "value")
MEAS = ("measurements", "slices", "all")
NOT_MEASURED = {
    "path": None,
    "input_sha256": None,
    "currency": None,
    "reason": "not_measured",
    "slices": {},
}
TRIGGERS = [
    {"evaluation": "asr", "metric": "wer", "slice": "all", "direction": "max", "threshold": 0.5}
]
APPROVAL = {"approved_by": "Owner", "role": "ml-lead", "approved_at": "2026-11-12T09:00:00Z"}


def dump(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def config_sha(config: dict[str, Any] | None) -> str | None:
    if config is None:
        return None
    text = json.dumps(config, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha_bytes(text.encode("utf-8"))


def cfg(version: str) -> dict[str, Any]:
    return {"model": "whisper-large-v3", "prompt": version}


def put(obj: Any, path: tuple[str, ...], value: Any) -> None:
    for key in path[:-1]:
        obj = obj[key]
    obj[path[-1]] = value


def drop(obj: Any, path: tuple[str, ...]) -> None:
    for key in path[:-1]:
        obj = obj[key]
    del obj[path[-1]]


def slice_entry(n: int, metrics: dict[str, float | None]) -> dict[str, Any]:
    return {
        "n_input_items": n,
        "insufficient_sample": False,
        "metrics": {
            name: {
                "name": name,
                "value": value,
                "abstention_rate": 0.0,
                "coverage": 1.0,
                "interval": {
                    "method": "percentile_bootstrap",
                    "low": None,
                    "high": None,
                    "reason": "bootstrap_disabled",
                },
            }
            for name, value in metrics.items()
        },
        "detail": {},
    }


def measurement_entry() -> dict[str, Any]:
    return {
        "n_requests": 4,
        "insufficient_sample": False,
        "latency_ms": {
            "n": 4,
            "n_missing": 0,
            "mean": 800.0,
            "p50": 750.0,
            "p90": 1000.0,
            "p95": 1000.0,
            "max": 1000.0,
            "reason": None,
        },
        "cost": {
            "n": 4,
            "n_missing": 0,
            "total": 0.5,
            "mean_per_request": 0.125,
            "currency": "USD",
            "reason": None,
        },
        "tokens": {
            "input_total": 40,
            "n_missing_input": 0,
            "output_total": 20,
            "n_missing_output": 0,
        },
        "providers": {"p1": 4},
        "n_missing_provider": 0,
    }


def report(version: str, wer: float, acc: float) -> dict[str, Any]:
    config = cfg(version)
    return {
        "version": "candidate-report/1",
        "evaluation_version": "evaluation/1",
        "candidate": version,
        "config": config,
        "config_sha256": config_sha(config),
        "manifest_sha256": "0" * 64,
        "slices": ["language"],
        "min_slice_size": 2,
        "bootstrap": {
            "method": "percentile_bootstrap",
            "resamples": 0,
            "seed": 0,
            "confidence": 0.95,
        },
        "evaluations": {
            "asr": {
                "version": "evaluation/1",
                "kind": "asr",
                "path": "asr.jsonl",
                "input_sha256": "1" * 64,
                "options": {},
                "slices": {
                    "all": slice_entry(4, {"wer": wer, "cer": 0.0625}),
                    "language=fa": slice_entry(2, {"wer": wer, "cer": 0.0625}),
                },
            },
            "intent": {
                "version": "evaluation/1",
                "kind": "intent",
                "path": "intent.jsonl",
                "input_sha256": "2" * 64,
                "options": {},
                "slices": {
                    "all": slice_entry(4, {"accuracy": acc}),
                    "language=fa": slice_entry(2, {"accuracy": acc, "macro_f1": acc}),
                },
            },
        },
        "measurements": {
            "path": "measurements.jsonl",
            "input_sha256": "3" * 64,
            "currency": "USD",
            "reason": None,
            "slices": {"all": measurement_entry()},
        },
        "insufficient_slices": [],
    }


def criteria() -> dict[str, Any]:
    return {
        "version": "release-criteria/1",
        "criteria_id": "asr-release-1",
        "frozen_at": "2026-11-12T00:00:00Z",
        "frozen_by": "ml-lead",
        "evaluation_version": "evaluation/1",
        "default_tolerance": 0.125,
        "min_reproductions": 1,
        "approver_roles": ["ml-lead", "product-owner"],
        "gates": [
            {
                "evaluation": "asr",
                "metric": "wer",
                "slice": "all",
                "direction": "max",
                "threshold": 0.5,
                "n_items": 4,
                "max_regression": 0.25,
                "tolerance": None,
            },
            {
                "evaluation": "intent",
                "metric": "accuracy",
                "slice": "language=fa",
                "direction": "min",
                "threshold": 0.5,
                "n_items": 2,
                "max_regression": None,
                "tolerance": 0.25,
            },
        ],
        "measurements": {
            "required": True,
            "slice": "all",
            "max_p95_latency_ms": 5000,
            "max_mean_cost_per_request": 0.25,
            "currency": "USD",
            "single_provider": True,
        },
    }


@dataclasses.dataclass
class Case:
    root: Path
    criteria: dict[str, Any]
    candidate: dict[str, Any]
    incumbent: dict[str, Any] | None
    reproductions: list[dict[str, Any]]
    criteria_text: str | None = None
    pin: str | None = None
    with_manifest: bool = True
    manifest_edit: Callable[[dict[str, Any]], Any] | None = None
    manifest_text: str | None = None
    request_edit: Callable[[dict[str, Any]], Any] | None = None

    def write(self) -> Path:
        reports = self.root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        crit = self.root / "criteria.json"
        if self.criteria_text is not None:
            crit.write_text(self.criteria_text, encoding="utf-8")
        else:
            crit.write_bytes(dump(self.criteria))
        cand = reports / "cand.json"
        cand.write_bytes(dump(self.candidate))
        inc = None
        if self.incumbent is not None:
            inc = reports / "inc.json"
            inc.write_bytes(dump(self.incumbent))
        names = []
        for i, rep in enumerate(self.reproductions, 2):
            (reports / f"cand-run{i}.json").write_bytes(dump(rep))
            names.append(f"reports/cand-run{i}.json")
        request: dict[str, Any] = {
            "candidate_report": "reports/cand.json",
            "incumbent_report": "reports/inc.json" if inc is not None else None,
            "reproduction_reports": names,
            "criteria": "criteria.json",
            "criteria_sha256": self.pin if self.pin is not None else sha_file(crit),
            "rollback_manifest": None,
        }
        if self.with_manifest:
            path = self.root / "rollback.json"
            if self.manifest_text is not None:
                path.write_text(self.manifest_text, encoding="utf-8")
            else:
                manifest = self.default_manifest(crit, cand, inc)
                if self.manifest_edit is not None:
                    manifest = self.manifest_edit(manifest)
                path.write_bytes(dump(manifest))
            request["rollback_manifest"] = "rollback.json"
        if self.request_edit is not None:
            request = self.request_edit(request)
        out = self.root / "request.json"
        out.write_bytes(dump(request))
        return out

    def default_manifest(self, crit: Path, cand: Path, inc: Path | None) -> dict[str, Any]:
        i = self.incumbent
        return {
            "version": "rollback-manifest/1",
            "promoted_version": self.candidate.get("candidate"),
            "promoted_config_sha256": self.candidate.get("config_sha256"),
            "previous_version": i.get("candidate") if i is not None else None,
            "previous_config_sha256": i.get("config_sha256") if i is not None else None,
            "previous_config": i.get("config") if i is not None else None,
            "candidate_report_sha256": sha_file(cand),
            "incumbent_report_sha256": sha_file(inc) if inc is not None else None,
            "criteria_sha256": sha_file(crit),
            "triggers": copy.deepcopy(TRIGGERS),
            "owner_role": "ml-lead",
            "verification_steps": ["re-run the held-out ASR evaluation", "check 20 live calls"],
            "approval": dict(APPROVAL),
        }


def make_case(
    root: Path,
    candidate: str = "whisper@p7",
    incumbent: str | None = "whisper@p6",
    reproductions: int = 1,
) -> Case:
    return Case(
        root=root,
        criteria=criteria(),
        candidate=report(candidate, 0.25, 0.75),
        incumbent=report(incumbent, 0.125, 0.75) if incumbent is not None else None,
        reproductions=[report(candidate, 0.25, 0.75) for _ in range(reproductions)],
    )


@pytest.fixture
def case(tmp_path: Path) -> Case:
    return make_case(tmp_path / "case")


def run_module(
    module: str, *argv: Any, cwd: Path = HOMAYAR_ROOT
) -> subprocess.CompletedProcess[bytes]:
    args = [sys.executable, "-m", module, *(str(a) for a in argv)]
    return subprocess.run(args, cwd=cwd, capture_output=True, check=False, env=CLI_ENV)  # noqa: S603 - fixed argv, the package's own CLI


def promotion(*argv: Any, cwd: Path = HOMAYAR_ROOT) -> subprocess.CompletedProcess[bytes]:
    return run_module("backend.evaluation.promotion", *argv, cwd=cwd)


def corrections(*argv: Any, cwd: Path = HOMAYAR_ROOT) -> subprocess.CompletedProcess[bytes]:
    return run_module("backend.evaluation.corrections", *argv, cwd=cwd)


def check(c: Case) -> tuple[subprocess.CompletedProcess[bytes], dict[str, Any]]:
    proc = promotion("check", c.write())
    assert proc.returncode in (0, 2), proc.stderr.decode()
    return proc, json.loads(proc.stdout)


def codes(decision: dict[str, Any]) -> set[str]:
    return {r["code"] for r in decision["reasons"]}


def assert_promotes(c: Case) -> dict[str, Any]:
    proc, decision = check(c)
    assert decision["reasons"] == [], decision["reasons"]
    assert decision["decision"] == "promote"
    assert proc.returncode == 0
    return decision


def assert_blocked_only(c: Case, code: str) -> dict[str, Any]:
    proc, decision = check(c)
    assert codes(decision) == {code}, decision["reasons"]
    assert decision["decision"] == "blocked"
    assert proc.returncode == 2
    return decision


def assert_input_error(proc: subprocess.CompletedProcess[bytes]) -> None:
    assert proc.returncode == 1, (proc.returncode, proc.stdout.decode(), proc.stderr.decode())
    assert proc.stdout == b""
    lines = [line for line in proc.stderr.decode().splitlines() if line.strip()]
    assert len(lines) == 1, lines
    assert lines[0].startswith("error: ")


def at(target: str, path: tuple[str, ...], value: Any) -> Callable[[Case], None]:
    def mutate(c: Case) -> None:
        for obj in targets(c, target):
            put(obj, path, value)

    return mutate


def without(target: str, path: tuple[str, ...]) -> Callable[[Case], None]:
    def mutate(c: Case) -> None:
        for obj in targets(c, target):
            drop(obj, path)

    return mutate


def targets(c: Case, target: str) -> list[dict[str, Any]]:
    if target == "candidate":
        return [c.candidate]
    if target == "incumbent":
        assert c.incumbent is not None
        return [c.incumbent]
    if target == "repro":
        return c.reproductions
    if target == "cr":
        return [c.candidate, *c.reproductions]
    if target == "criteria":
        return [c.criteria]
    raise AssertionError(target)


def manifest_with(**changes: Any) -> Callable[[Case], None]:
    def mutate(c: Case) -> None:
        c.manifest_edit = lambda m: {**m, **changes}

    return mutate


def manifest_without(key: str) -> Callable[[Case], None]:
    def mutate(c: Case) -> None:
        c.manifest_edit = lambda m: {k: v for k, v in m.items() if k != key}

    return mutate


def manifest_fn(fn: Callable[[dict[str, Any]], Any]) -> Callable[[Case], None]:
    def mutate(c: Case) -> None:
        c.manifest_edit = fn

    return mutate


def steps(*mutators: Callable[[Case], None]) -> Callable[[Case], None]:
    def mutate(c: Case) -> None:
        for m in mutators:
            m(c)

    return mutate


def pin_then_edit(edit: Callable[[Case], None]) -> Callable[[Case], None]:
    def mutate(c: Case) -> None:
        c.pin = sha_bytes(dump(c.criteria))
        edit(c)

    return mutate


def reformat_criteria(c: Case) -> None:
    c.criteria_text = dump(c.criteria).decode("utf-8") + "\n"


def no_incumbent(c: Case) -> None:
    c.incumbent = None


def same_as_candidate(c: Case) -> None:
    c.incumbent = copy.deepcopy(c.candidate)


def no_manifest(c: Case) -> None:
    c.with_manifest = False


def reproductions(n: int) -> Callable[[Case], None]:
    def mutate(c: Case) -> None:
        c.reproductions = [copy.deepcopy(c.candidate) for _ in range(n)]

    return mutate


def repro_config(c: Case) -> None:
    for rep in c.reproductions:
        rep["config"] = cfg("other")
        rep["config_sha256"] = config_sha(rep["config"])


def gate(i: int, **changes: Any) -> Callable[[Case], None]:
    def mutate(c: Case) -> None:
        c.criteria["gates"][i].update(changes)

    return mutate


def gate_without(i: int, key: str) -> Callable[[Case], None]:
    def mutate(c: Case) -> None:
        del c.criteria["gates"][i][key]

    return mutate


def measurements_rule(**changes: Any) -> Callable[[Case], None]:
    def mutate(c: Case) -> None:
        c.criteria["measurements"].update(changes)

    return mutate


def report_currency(currency: str) -> Callable[[Case], None]:
    return steps(
        at("candidate", ("measurements", "currency"), currency),
        at("candidate", (*MEAS, "cost", "currency"), currency),
    )


def deny(name: str, mutate: Callable[[Case], None], code: str) -> Any:
    return pytest.param(mutate, code, id=name)


DENY_CASES = [
    deny(
        "a1-criteria-edited-after-pin", pin_then_edit(gate(0, threshold=0.625)), "criteria_changed"
    ),
    deny("a1-criteria-reformatted-after-pin", pin_then_edit(reformat_criteria), "criteria_changed"),
    deny(
        "a2-candidate-report-version",
        at("candidate", ("version",), "candidate-report/2"),
        "report_version_mismatch",
    ),
    deny(
        "a2-incumbent-report-version",
        at("incumbent", ("version",), "candidate-report/2"),
        "report_version_mismatch",
    ),
    deny(
        "a2-reproduction-report-version",
        at("repro", ("version",), "candidate-report/0"),
        "report_version_mismatch",
    ),
    deny(
        "a2-candidate-evaluation-version",
        at("candidate", ("evaluation_version",), "evaluation/2"),
        "evaluation_version_mismatch",
    ),
    deny(
        "a2-incumbent-evaluation-version",
        at("incumbent", ("evaluation_version",), "evaluation/2"),
        "evaluation_version_mismatch",
    ),
    deny(
        "a2-reproduction-evaluation-version",
        at("repro", ("evaluation_version",), "evaluation/2"),
        "evaluation_version_mismatch",
    ),
    deny(
        "a2-criteria-evaluation-version",
        at("criteria", ("evaluation_version",), "evaluation/2"),
        "evaluation_version_mismatch",
    ),
    deny("a3-incumbent-equals-candidate", same_as_candidate, "no_change"),
    deny("a4-candidate-value-null", at("candidate", WER, None), "gate_missing"),
    deny("a4-candidate-metric-absent", without("candidate", WER[:-1]), "gate_missing"),
    deny("a4-candidate-slice-absent", without("candidate", WER_SLICE), "gate_missing"),
    deny(
        "a4-candidate-evaluation-absent",
        without("candidate", ("evaluations", "asr")),
        "gate_missing",
    ),
    deny("a4-candidate-min-gate-slice-absent", without("candidate", ACC[:4]), "gate_missing"),
    deny(
        "a5-candidate-slice-size",
        at("candidate", (*WER_SLICE, "n_input_items"), 5),
        "held_out_size_mismatch",
    ),
    deny(
        "a6-candidate-insufficient-sample",
        at("candidate", (*WER_SLICE, "insufficient_sample"), True),
        "insufficient_sample",
    ),
    deny(
        "a7-max-threshold-plus-1e-9",
        steps(at("incumbent", WER, 0.375), at("cr", WER, 0.5 + 1e-9)),
        "threshold_missed",
    ),
    deny("a7-min-threshold-minus-1e-9", at("cr", ACC, 0.5 - 1e-9), "threshold_missed"),
    deny("a8-incumbent-value-null", at("incumbent", WER, None), "incumbent_gate_missing"),
    deny("a8-incumbent-slice-absent", without("incumbent", WER_SLICE), "incumbent_gate_missing"),
    deny(
        "a8-incumbent-slice-size",
        at("incumbent", (*WER_SLICE, "n_input_items"), 3),
        "held_out_size_mismatch",
    ),
    deny("a9-max-regression-just-over", at("cr", WER, 0.375 + 1e-9), "regression"),
    deny(
        "a9-min-regression-just-over",
        steps(gate(1, max_regression=0.125), at("cr", ACC, 0.625 - 1e-9)),
        "regression",
    ),
    deny("a9-max-regression-zero-is-not-null", gate(0, max_regression=0), "regression"),
    deny("a10-no-reproduction", reproductions(0), "reproduction_missing"),
    deny("a10-fewer-than-min", at("criteria", ("min_reproductions",), 2), "reproduction_missing"),
    deny(
        "a11-reproduction-other-candidate",
        at("repro", ("candidate",), "whisper@p8"),
        "reproduction_mismatch",
    ),
    deny("a11-reproduction-other-config", repro_config, "reproduction_mismatch"),
    deny("a12-reproduction-value-null", at("repro", WER, None), "not_reproducible"),
    deny("a12-reproduction-metric-absent", without("repro", WER[:-1]), "not_reproducible"),
    deny(
        "a12-reproduction-slice-size",
        at("repro", (*WER_SLICE, "n_input_items"), 5),
        "not_reproducible",
    ),
    deny("a12-over-default-tolerance", at("repro", WER, 0.375 + 1e-9), "not_reproducible"),
    deny(
        "a12-under-default-but-over-gate-tolerance",
        steps(gate(0, tolerance=0.0625), at("repro", WER, 0.375)),
        "not_reproducible",
    ),
    deny(
        "a12-gate-tolerance-zero-is-not-null",
        steps(gate(0, tolerance=0), at("repro", WER, 0.3125)),
        "not_reproducible",
    ),
    deny("a12-over-gate-tolerance-min-gate", at("repro", ACC, 0.5 - 1e-9), "not_reproducible"),
    deny(
        "a13-required-not-measured",
        at("candidate", ("measurements",), copy.deepcopy(NOT_MEASURED)),
        "measurements_missing",
    ),
    deny(
        "a13-required-slice-absent", measurements_rule(slice="language=fa"), "measurements_missing"
    ),
    deny("a14-latency-over", at("candidate", (*MEAS, "latency_ms", "p95"), 5000.5), "latency_over"),
    deny("a14-latency-null", at("candidate", (*MEAS, "latency_ms", "p95"), None), "latency_over"),
    deny(
        "a15-cost-over",
        at("candidate", (*MEAS, "cost", "mean_per_request"), 0.25 + 1e-9),
        "cost_over",
    ),
    deny("a15-cost-null", at("candidate", (*MEAS, "cost", "mean_per_request"), None), "cost_over"),
    deny(
        "a15-currency-mismatch-skips-cost",
        steps(report_currency("EUR"), at("candidate", (*MEAS, "cost", "mean_per_request"), 9.0)),
        "currency_mismatch",
    ),
    deny(
        "a16-two-providers",
        at("candidate", (*MEAS, "providers"), {"p1": 2, "p2": 2}),
        "provider_mixing",
    ),
    deny(
        "a16-missing-provider", at("candidate", (*MEAS, "n_missing_provider"), 1), "provider_mixing"
    ),
    deny("a17-manifest-null", no_manifest, "rollback_manifest_missing"),
    deny(
        "a18-promoted-version",
        manifest_with(promoted_version="whisper@p9"),
        "rollback_manifest_mismatch",
    ),
    deny(
        "a18-promoted-config-sha",
        manifest_with(promoted_config_sha256="e" * 64),
        "rollback_manifest_mismatch",
    ),
    deny(
        "a18-promoted-config-sha-null",
        manifest_with(promoted_config_sha256=None),
        "rollback_manifest_mismatch",
    ),
    deny(
        "a18-previous-version",
        manifest_with(previous_version="whisper@p5"),
        "rollback_manifest_mismatch",
    ),
    deny(
        "a18-previous-version-null-with-incumbent",
        manifest_with(previous_version=None),
        "rollback_manifest_mismatch",
    ),
    deny(
        "a18-previous-config-sha",
        manifest_fn(
            lambda m: {
                **m,
                "previous_config_sha256": config_sha(cfg("x")),
                "previous_config": cfg("x"),
            }
        ),
        "rollback_manifest_mismatch",
    ),
    deny(
        "a18-previous-config-not-hashing",
        manifest_with(previous_config=cfg("whisper@p5")),
        "rollback_manifest_mismatch",
    ),
    deny(
        "a18-previous-config-null-with-sha",
        manifest_with(previous_config=None),
        "rollback_manifest_mismatch",
    ),
    deny(
        "a18-candidate-report-sha",
        manifest_with(candidate_report_sha256="e" * 64),
        "rollback_manifest_mismatch",
    ),
    deny(
        "a18-incumbent-report-sha",
        manifest_with(incumbent_report_sha256="e" * 64),
        "rollback_manifest_mismatch",
    ),
    deny(
        "a18-incumbent-report-sha-null-with-incumbent",
        manifest_with(incumbent_report_sha256=None),
        "rollback_manifest_mismatch",
    ),
    deny("a18-criteria-sha", manifest_with(criteria_sha256="e" * 64), "rollback_manifest_mismatch"),
    deny(
        "a18-no-incumbent-previous-version-set",
        steps(no_incumbent, manifest_with(previous_version="whisper@p6")),
        "rollback_manifest_mismatch",
    ),
    deny(
        "a18-no-incumbent-incumbent-sha-set",
        steps(no_incumbent, manifest_with(incumbent_report_sha256="e" * 64)),
        "rollback_manifest_mismatch",
    ),
    deny("a19-approval-null", manifest_with(approval=None), "rollback_manifest_not_approved"),
    deny(
        "a19-role-not-allowed",
        manifest_with(approval={**APPROVAL, "role": "intern"}),
        "rollback_manifest_not_approved",
    ),
    deny(
        "a19-role-other-case",
        manifest_with(approval={**APPROVAL, "role": "ML-Lead"}),
        "rollback_manifest_not_approved",
    ),
]


def test_baseline_promotes(case: Case) -> None:
    assert_promotes(case)


@pytest.mark.parametrize(("mutate", "code"), DENY_CASES)
def test_deny_from_baseline_gives_exactly_one_code(
    case: Case, mutate: Callable[[Case], None], code: str
) -> None:
    mutate(case)
    assert_blocked_only(case, code)


ALLOW_CASES = [
    pytest.param(at("cr", ACC, 0.5), id="a7-min-value-equals-threshold"),
    pytest.param(
        steps(at("incumbent", WER, 0.375), at("cr", WER, 0.5)), id="a7-max-value-equals-threshold"
    ),
    pytest.param(at("cr", WER, 0.375), id="a9-max-regression-exactly-max"),
    pytest.param(
        steps(gate(1, max_regression=0.125), at("cr", ACC, 0.625)),
        id="a9-min-regression-exactly-max",
    ),
    pytest.param(
        steps(at("incumbent", ACC, 1.0), at("cr", ACC, 0.5)), id="a9-null-max-regression-big-drop"
    ),
    pytest.param(gate(0, max_regression=None), id="a9-null-max-regression"),
    pytest.param(
        steps(gate_without(0, "max_regression"), gate_without(1, "tolerance"), at("cr", ACC, 0.75)),
        id="optional-gate-keys-absent",
    ),
    pytest.param(at("repro", WER, 0.375), id="a12-diff-equals-default-tolerance"),
    pytest.param(at("repro", WER, 0.125), id="a12-diff-equals-default-tolerance-below"),
    pytest.param(
        steps(gate(0, tolerance=0.25), at("repro", WER, 0.5)),
        id="a12-gate-tolerance-wins-over-default",
    ),
    pytest.param(at("repro", ACC, 0.5), id="a12-diff-equals-gate-tolerance"),
    pytest.param(
        steps(at("criteria", ("min_reproductions",), 2), reproductions(2)), id="a10-two-of-two"
    ),
    pytest.param(
        steps(at("criteria", ("min_reproductions",), 1), reproductions(3)), id="a10-more-than-min"
    ),
    pytest.param(
        manifest_with(approval={**APPROVAL, "role": "product-owner"}), id="a19-other-allowed-role"
    ),
    pytest.param(
        steps(no_incumbent, at("cr", ("config",), None), at("cr", ("config_sha256",), None)),
        id="a18-first-promotion-null-config",
    ),
    pytest.param(no_incumbent, id="a18-first-promotion-previous-null"),
    pytest.param(
        steps(
            at("candidate", ("measurements",), copy.deepcopy(NOT_MEASURED)),
            measurements_rule(
                required=False,
                max_p95_latency_ms=None,
                max_mean_cost_per_request=None,
                currency=None,
            ),
        ),
        id="a13-not-required-not-measured-no-limits",
    ),
    pytest.param(
        steps(
            at("candidate", ("measurements",), copy.deepcopy(NOT_MEASURED)),
            measurements_rule(required=False),
        ),
        id="a13-not-measured-skips-limits",
    ),
    pytest.param(
        measurements_rule(required=False, slice="language=fa"), id="a13-slice-absent-skips-limits"
    ),
    pytest.param(
        at("candidate", (*MEAS, "latency_ms", "p95"), 5000.0), id="a14-latency-equals-limit"
    ),
    pytest.param(
        steps(
            measurements_rule(max_p95_latency_ms=None),
            at("candidate", (*MEAS, "latency_ms", "p95"), None),
        ),
        id="a14-no-latency-limit",
    ),
    pytest.param(
        at("candidate", (*MEAS, "cost", "mean_per_request"), 0.25), id="a15-cost-equals-limit"
    ),
    pytest.param(
        steps(
            measurements_rule(max_mean_cost_per_request=None, currency=None),
            report_currency("EUR"),
            at("candidate", (*MEAS, "cost", "mean_per_request"), None),
        ),
        id="a15-no-cost-limit-any-currency",
    ),
    pytest.param(
        steps(
            measurements_rule(single_provider=False),
            at("candidate", (*MEAS, "providers"), {"p1": 2, "p2": 2}),
            at("candidate", (*MEAS, "n_missing_provider"), 1),
        ),
        id="a16-mixing-allowed",
    ),
    pytest.param(
        steps(at("incumbent", ("candidate",), "whisper@p7")), id="a3-same-name-other-config"
    ),
    pytest.param(
        steps(
            at("incumbent", ("config",), cfg("whisper@p7")),
            at("incumbent", ("config_sha256",), config_sha(cfg("whisper@p7"))),
        ),
        id="a3-same-config-other-name",
    ),
]


@pytest.mark.parametrize("mutate", ALLOW_CASES)
def test_allow_control_promotes(case: Case, mutate: Callable[[Case], None]) -> None:
    mutate(case)
    assert_promotes(case)


def test_promote_decision_content(case: Case) -> None:
    request = case.write()
    proc = promotion("check", request)
    assert proc.returncode == 0, proc.stderr.decode()
    d = json.loads(proc.stdout)
    root = case.root
    assert set(d) == DECISION_KEYS
    assert d["version"] == "promotion-decision/1"
    assert d["decision"] == "promote"
    assert d["criteria_id"] == "asr-release-1"
    assert d["criteria_sha256"] == sha_file(root / "criteria.json")
    assert d["candidate"] == "whisper@p7"
    assert d["candidate_config_sha256"] == config_sha(cfg("whisper@p7"))
    assert d["candidate_report_sha256"] == sha_file(root / "reports" / "cand.json")
    assert d["incumbent"] == "whisper@p6"
    assert d["incumbent_config_sha256"] == config_sha(cfg("whisper@p6"))
    assert d["incumbent_report_sha256"] == sha_file(root / "reports" / "inc.json")
    assert d["rollback_manifest_sha256"] == sha_file(root / "rollback.json")
    assert d["reasons"] == []
    assert [(g["evaluation"], g["metric"], g["slice"]) for g in d["gates"]] == [
        ("asr", "wer", "all"),
        ("intent", "accuracy", "language=fa"),
    ]
    g1, g2 = d["gates"]
    assert set(g1) == GATE_KEYS
    assert set(g2) == GATE_KEYS
    assert (g1["direction"], g1["threshold"], g1["n_items"]) == ("max", 0.5, 4)
    assert g1["tolerance"] == 0.125
    assert g1["max_regression"] == 0.25
    assert g1["candidate_value"] == 0.25
    assert g1["incumbent_value"] == 0.125
    assert g1["reproduction_values"] == [0.25]
    assert g1["passed"] is True
    assert g1["reasons"] == []
    assert (g2["direction"], g2["threshold"], g2["n_items"]) == ("min", 0.5, 2)
    assert g2["tolerance"] == 0.25
    assert g2["max_regression"] is None
    assert g2["candidate_value"] == 0.75
    assert g2["passed"] is True


def test_gate_tolerance_zero_is_reported_as_zero(case: Case) -> None:
    gate(0, tolerance=0)(case)
    d = assert_promotes(case)
    assert d["gates"][0]["tolerance"] == 0


def test_gate_order_follows_criteria(case: Case) -> None:
    case.criteria["gates"].reverse()
    d = assert_promotes(case)
    assert [g["evaluation"] for g in d["gates"]] == ["intent", "asr"]


def test_reproduction_values_listed(case: Case) -> None:
    steps(gate(0, tolerance=0.25), reproductions(2))(case)
    case.reproductions[1]["evaluations"]["asr"]["slices"]["all"]["metrics"]["wer"]["value"] = 0.5
    d = assert_promotes(case)
    assert sorted(d["gates"][0]["reproduction_values"]) == [0.25, 0.5]


def test_no_incumbent_and_no_manifest_fields_are_null(case: Case) -> None:
    no_incumbent(case)
    no_manifest(case)
    _, d = check(case)
    assert codes(d) == {"rollback_manifest_missing"}
    assert d["incumbent"] is None
    assert d["incumbent_config_sha256"] is None
    assert d["incumbent_report_sha256"] is None
    assert d["rollback_manifest_sha256"] is None
    assert d["gates"][0]["incumbent_value"] is None


def test_blocked_collects_every_reason_sorted(case: Case) -> None:
    steps(
        at("incumbent", WER, 0.75),
        at("cr", WER, 0.75),
        manifest_with(approval=None),
        at("candidate", (*MEAS, "providers"), {"p1": 2, "p2": 2}),
        at("candidate", (*MEAS, "latency_ms", "p95"), 9000.0),
    )(case)
    proc, d = check(case)
    assert proc.returncode == 2
    assert d["decision"] == "blocked"
    assert codes(d) == {
        "threshold_missed",
        "rollback_manifest_not_approved",
        "provider_mixing",
        "latency_over",
    }
    assert d["reasons"] == sorted(d["reasons"], key=lambda r: (r["code"], r["detail"]))
    for r in d["reasons"]:
        assert set(r) == {"code", "detail"}
        assert isinstance(r["detail"], str)
        assert r["detail"].strip()
    missed = [r for r in d["reasons"] if r["code"] == "threshold_missed"]
    assert "wer" in missed[0]["detail"]
    g1, g2 = d["gates"]
    assert g1["reasons"] == ["threshold_missed"]
    assert g1["passed"] is False
    assert g1["candidate_value"] == 0.75
    assert g2["reasons"] == []
    assert g2["passed"] is True


def test_gate_reasons_are_sorted_codes(case: Case) -> None:
    steps(
        at("cr", WER, 0.75),
        at("incumbent", WER, 0.75),
        at("candidate", (*WER_SLICE, "n_input_items"), 5),
        at("candidate", (*WER_SLICE, "insufficient_sample"), True),
    )(case)
    _, d = check(case)
    assert d["gates"][0]["reasons"] == [
        "held_out_size_mismatch",
        "insufficient_sample",
        "threshold_missed",
    ]
    assert codes(d) == {"held_out_size_mismatch", "insufficient_sample", "threshold_missed"}


def test_manifest_mismatch_one_reason_per_field(case: Case) -> None:
    manifest_with(promoted_version="whisper@p9", criteria_sha256="e" * 64)(case)
    _, d = check(case)
    assert codes(d) == {"rollback_manifest_mismatch"}
    assert len(d["reasons"]) == 2


def test_check_is_byte_identical_across_runs_and_cwd(case: Case, tmp_path: Path) -> None:
    request = case.write()
    first = promotion("check", request)
    second = promotion("check", request, cwd=tmp_path)
    assert first.returncode == 0, first.stderr.decode()
    assert first.stdout == second.stdout


def test_relative_paths_resolve_against_request_dir(case: Case, tmp_path: Path) -> None:
    request = case.write()
    other = tmp_path / "elsewhere"
    other.mkdir()
    proc = promotion("check", request, cwd=other)
    assert proc.returncode == 0, proc.stderr.decode()


def test_absolute_paths_in_request(case: Case) -> None:
    root = case.root

    def absolute(r: dict[str, Any]) -> dict[str, Any]:
        r = dict(r)
        for key in ("candidate_report", "incumbent_report", "criteria", "rollback_manifest"):
            r[key] = str(root / r[key])
        r["reproduction_reports"] = [str(root / p) for p in r["reproduction_reports"]]
        return r

    case.request_edit = absolute
    assert_promotes(case)


def real_report(tmp_path: Path) -> Path:
    proc = run_module("backend.evaluation.candidate", FIXTURES / "candidate" / "manifest.json")
    assert proc.returncode == 0, proc.stderr.decode()
    path = tmp_path / "real" / "reports" / "cand.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(proc.stdout)
    return path


@pytest.mark.parametrize(
    ("threshold", "expected"),
    [
        pytest.param(0.75, "promote", id="equal-threshold-promotes"),
        pytest.param(0.75 + 1e-9, "blocked", id="threshold-missed"),
    ],
)
def test_real_candidate_report(tmp_path: Path, threshold: float, expected: str) -> None:
    cand = real_report(tmp_path)
    data = json.loads(cand.read_bytes())
    assert data["evaluations"]["dense"]["slices"]["all"]["metrics"]["mrr"]["value"] == 0.75
    c = Case(
        root=tmp_path / "real",
        criteria={
            **criteria(),
            "gates": [
                {
                    "evaluation": "dense",
                    "metric": "mrr",
                    "slice": "all",
                    "direction": "min",
                    "threshold": threshold,
                    "n_items": 7,
                }
            ],
            "measurements": {
                "required": False,
                "slice": "all",
                "max_p95_latency_ms": None,
                "max_mean_cost_per_request": None,
                "currency": None,
                "single_provider": False,
            },
        },
        candidate=data,
        incumbent=None,
        reproductions=[data],
    )
    proc, d = check(c)
    assert d["decision"] == expected
    if expected == "promote":
        assert proc.returncode == 0
        assert d["reasons"] == []
    else:
        assert proc.returncode == 2
        assert codes(d) == {"threshold_missed"}


MANIFEST_INVALID = [
    pytest.param(manifest_with(version="rollback-manifest/2"), id="version"),
    pytest.param(manifest_with(extra=1), id="unknown-key"),
    pytest.param(manifest_without("triggers"), id="missing-triggers"),
    pytest.param(manifest_without("approval"), id="missing-approval-not-null"),
    pytest.param(manifest_without("previous_version"), id="missing-previous-version"),
    pytest.param(manifest_with(promoted_version="  "), id="promoted-version-blank"),
    pytest.param(manifest_with(previous_version=""), id="previous-version-empty"),
    pytest.param(manifest_with(triggers=[]), id="triggers-empty"),
    pytest.param(manifest_with(triggers=[{**TRIGGERS[0], "extra": 1}]), id="trigger-unknown-key"),
    pytest.param(
        manifest_with(triggers=[{k: v for k, v in TRIGGERS[0].items() if k != "slice"}]),
        id="trigger-missing-key",
    ),
    pytest.param(
        manifest_with(triggers=[{**TRIGGERS[0], "direction": "MAX"}]), id="trigger-direction-case"
    ),
    pytest.param(
        manifest_with(triggers=[{**TRIGGERS[0], "direction": "eq"}]), id="trigger-direction"
    ),
    pytest.param(
        manifest_with(triggers=[{**TRIGGERS[0], "threshold": "0.5"}]), id="trigger-threshold-str"
    ),
    pytest.param(
        manifest_with(triggers=[{**TRIGGERS[0], "threshold": True}]), id="trigger-threshold-bool"
    ),
    pytest.param(manifest_with(owner_role=""), id="owner-role-blank"),
    pytest.param(manifest_with(verification_steps=[]), id="verification-empty"),
    pytest.param(manifest_with(verification_steps=["ok", " "]), id="verification-blank-step"),
    pytest.param(
        manifest_with(approval={k: v for k, v in APPROVAL.items() if k != "approved_at"}),
        id="approval-missing-key",
    ),
    pytest.param(manifest_with(approval={**APPROVAL, "extra": 1}), id="approval-unknown-key"),
    pytest.param(manifest_with(approval={**APPROVAL, "approved_by": " "}), id="approval-by-blank"),
    pytest.param(
        manifest_with(approval={**APPROVAL, "approved_at": "2026-11-12T09:00:00+00:00"}),
        id="approval-at-offset",
    ),
    pytest.param(
        manifest_with(approval={**APPROVAL, "approved_at": "2026-11-12T09:00:00.000Z"}),
        id="approval-at-millis",
    ),
    pytest.param(
        manifest_with(approval={**APPROVAL, "approved_at": "2026-11-12 09:00:00Z"}),
        id="approval-at-space",
    ),
    pytest.param(
        manifest_fn(
            lambda m: {**m, "candidate_report_sha256": m["candidate_report_sha256"].upper()}
        ),
        id="sha-uppercase",
    ),
    pytest.param(manifest_with(criteria_sha256="abc"), id="sha-short"),
    pytest.param(manifest_with(promoted_config_sha256="g" * 64), id="config-sha-not-hex"),
    pytest.param(manifest_fn(lambda m: [m]), id="not-an-object"),
]


@pytest.mark.parametrize("mutate", MANIFEST_INVALID)
def test_manifest_invalid(case: Case, mutate: Callable[[Case], None]) -> None:
    mutate(case)
    assert_blocked_only(case, "rollback_manifest_invalid")


def test_manifest_bad_json_is_invalid_or_input_error(case: Case) -> None:
    case.manifest_text = "{not json"
    proc = promotion("check", case.write())
    if proc.returncode == 1:
        assert_input_error(proc)
    else:
        assert proc.returncode == 2
        assert codes(json.loads(proc.stdout)) == {"rollback_manifest_invalid"}


def request_without(key: str) -> Callable[[Case], None]:
    def mutate(c: Case) -> None:
        c.request_edit = lambda r: {k: v for k, v in r.items() if k != key}

    return mutate


def request_with(**changes: Any) -> Callable[[Case], None]:
    def mutate(c: Case) -> None:
        c.request_edit = lambda r: {**r, **changes}

    return mutate


def criteria_without(key: str) -> Callable[[Case], None]:
    def mutate(c: Case) -> None:
        del c.criteria[key]

    return mutate


def criteria_text(text: str) -> Callable[[Case], None]:
    def mutate(c: Case) -> None:
        c.criteria_text = text

    return mutate


def nan_threshold(c: Case) -> None:
    c.criteria_text = (
        dump(c.criteria).decode("utf-8").replace('"threshold": 0.5', '"threshold": NaN', 1)
    )


def duplicate_gate(c: Case) -> None:
    c.criteria["gates"].append({**c.criteria["gates"][0], "threshold": 0.75})


CRITERIA_TOP = [
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
]
MEASUREMENT_RULE_KEYS = [
    "required",
    "slice",
    "max_p95_latency_ms",
    "max_mean_cost_per_request",
    "currency",
    "single_provider",
]
GATE_REQUIRED = ["evaluation", "metric", "slice", "direction", "threshold", "n_items"]


def measurements_rule_without(key: str) -> Callable[[Case], None]:
    def mutate(c: Case) -> None:
        del c.criteria["measurements"][key]

    return mutate


INPUT_ERRORS = [
    *[
        pytest.param(request_without(k), id=f"request-missing-{k}")
        for k in (
            "candidate_report",
            "incumbent_report",
            "reproduction_reports",
            "criteria",
            "criteria_sha256",
            "rollback_manifest",
        )
    ],
    pytest.param(request_with(extra=1), id="request-unknown-key"),
    pytest.param(
        request_with(reproduction_reports="reports/cand-run2.json"), id="request-reproductions-str"
    ),
    pytest.param(
        request_with(candidate_report="reports/missing.json"), id="candidate-file-missing"
    ),
    pytest.param(
        request_with(incumbent_report="reports/missing.json"), id="incumbent-file-missing"
    ),
    pytest.param(
        request_with(reproduction_reports=["reports/missing.json"]), id="reproduction-file-missing"
    ),
    pytest.param(request_with(criteria="missing.json"), id="criteria-file-missing"),
    pytest.param(request_with(rollback_manifest="missing.json"), id="a17-manifest-file-missing"),
    *[pytest.param(criteria_without(k), id=f"criteria-missing-{k}") for k in CRITERIA_TOP],
    pytest.param(at("criteria", ("extra",), 1), id="criteria-unknown-key"),
    pytest.param(at("criteria", ("version",), "release-criteria/2"), id="criteria-version"),
    pytest.param(at("criteria", ("criteria_id",), " "), id="criteria-id-blank"),
    pytest.param(at("criteria", ("frozen_by",), ""), id="criteria-frozen-by-blank"),
    pytest.param(
        at("criteria", ("frozen_at",), "2026-11-12T00:00:00+00:00"), id="frozen-at-offset"
    ),
    pytest.param(at("criteria", ("frozen_at",), "2026-11-12T00:00:00"), id="frozen-at-no-z"),
    pytest.param(at("criteria", ("frozen_at",), "2026-11-12T00:00:00.5Z"), id="frozen-at-fraction"),
    pytest.param(at("criteria", ("frozen_at",), "2026-11-12"), id="frozen-at-date-only"),
    pytest.param(at("criteria", ("frozen_at",), "2026-11-12t00:00:00z"), id="frozen-at-lowercase"),
    pytest.param(at("criteria", ("default_tolerance",), -0.125), id="default-tolerance-negative"),
    pytest.param(at("criteria", ("default_tolerance",), True), id="default-tolerance-bool"),
    pytest.param(at("criteria", ("default_tolerance",), "0.1"), id="default-tolerance-str"),
    pytest.param(at("criteria", ("min_reproductions",), 0), id="min-reproductions-zero"),
    pytest.param(at("criteria", ("min_reproductions",), 1.0), id="min-reproductions-float"),
    pytest.param(at("criteria", ("min_reproductions",), True), id="min-reproductions-bool"),
    pytest.param(at("criteria", ("approver_roles",), []), id="approver-roles-empty"),
    pytest.param(at("criteria", ("approver_roles",), ["ml-lead", " "]), id="approver-roles-blank"),
    pytest.param(at("criteria", ("approver_roles",), "ml-lead"), id="approver-roles-str"),
    pytest.param(at("criteria", ("gates",), []), id="gates-empty"),
    pytest.param(duplicate_gate, id="gates-duplicate"),
    *[pytest.param(gate_without(0, k), id=f"gate-missing-{k}") for k in GATE_REQUIRED],
    pytest.param(gate(0, extra=1), id="gate-unknown-key"),
    pytest.param(gate(0, evaluation=" "), id="gate-evaluation-blank"),
    pytest.param(gate(0, direction="MAX"), id="gate-direction-case"),
    pytest.param(gate(0, direction="eq"), id="gate-direction-unknown"),
    pytest.param(gate(0, threshold=True), id="gate-threshold-bool"),
    pytest.param(gate(0, threshold="0.5"), id="gate-threshold-str"),
    pytest.param(gate(0, threshold=None), id="gate-threshold-null"),
    pytest.param(nan_threshold, id="gate-threshold-nan"),
    pytest.param(gate(0, n_items=0), id="gate-n-items-zero"),
    pytest.param(gate(0, n_items=4.0), id="gate-n-items-float"),
    pytest.param(gate(0, max_regression=-0.25), id="gate-max-regression-negative"),
    pytest.param(gate(0, tolerance=-0.25), id="gate-tolerance-negative"),
    *[
        pytest.param(measurements_rule_without(k), id=f"measurements-missing-{k}")
        for k in MEASUREMENT_RULE_KEYS
    ],
    pytest.param(measurements_rule(extra=1), id="measurements-unknown-key"),
    pytest.param(measurements_rule(required="true"), id="measurements-required-str"),
    pytest.param(measurements_rule(single_provider=None), id="measurements-single-provider-null"),
    pytest.param(measurements_rule(slice=""), id="measurements-slice-blank"),
    pytest.param(measurements_rule(max_p95_latency_ms=-1), id="measurements-latency-negative"),
    pytest.param(measurements_rule(currency=None), id="measurements-cost-limit-without-currency"),
    pytest.param(measurements_rule(currency=" "), id="measurements-currency-blank"),
    pytest.param(criteria_text("{not json"), id="criteria-bad-json"),
    pytest.param(criteria_text("[]"), id="criteria-not-object"),
    pytest.param(without("candidate", ("candidate",)), id="report-missing-candidate"),
    pytest.param(without("candidate", ("config_sha256",)), id="report-missing-config-sha"),
    pytest.param(without("candidate", ("version",)), id="report-missing-version"),
    pytest.param(
        without("candidate", ("evaluation_version",)), id="report-missing-evaluation-version"
    ),
    pytest.param(without("candidate", ("measurements",)), id="report-missing-measurements"),
    pytest.param(without("candidate", ("evaluations",)), id="report-missing-evaluations"),
    pytest.param(without("incumbent", ("candidate",)), id="incumbent-missing-candidate"),
    pytest.param(without("repro", ("config_sha256",)), id="reproduction-missing-config-sha"),
]


@pytest.mark.parametrize("mutate", INPUT_ERRORS)
def test_check_input_error(case: Case, mutate: Callable[[Case], None]) -> None:
    mutate(case)
    assert_input_error(promotion("check", case.write()))


def test_check_request_bad_json(tmp_path: Path) -> None:
    request = tmp_path / "request.json"
    request.write_text("{", encoding="utf-8")
    assert_input_error(promotion("check", request))


def test_check_request_missing(tmp_path: Path) -> None:
    assert_input_error(promotion("check", tmp_path / "absent.json"))


def test_check_report_not_object(case: Case) -> None:
    request = case.write()
    (case.root / "reports" / "cand.json").write_text("[]", encoding="utf-8")
    assert_input_error(promotion("check", request))


def ledger_manifest(version: str, previous: str | None) -> dict[str, Any]:
    return {
        "version": "rollback-manifest/1",
        "promoted_version": version,
        "promoted_config_sha256": config_sha(cfg(version)),
        "previous_version": previous,
        "previous_config_sha256": config_sha(cfg(previous)) if previous is not None else None,
        "previous_config": cfg(previous) if previous is not None else None,
        "candidate_report_sha256": "b" * 64,
        "incumbent_report_sha256": "d" * 64 if previous is not None else None,
        "criteria_sha256": "a" * 64,
        "triggers": copy.deepcopy(TRIGGERS),
        "owner_role": "ml-lead",
        "verification_steps": ["re-run the held-out ASR evaluation"],
        "approval": dict(APPROVAL),
    }


def promo_line(seq: int, version: str, previous: str | None) -> dict[str, Any]:
    return {
        "type": "promotion",
        "sequence": seq,
        "at": "2026-11-12T10:00:00Z",
        "version": version,
        "config_sha256": config_sha(cfg(version)),
        "criteria_sha256": "a" * 64,
        "candidate_report_sha256": "b" * 64,
        "decision_sha256": "c" * 64,
        "rollback_manifest": ledger_manifest(version, previous),
    }


def rollback_line(seq: int, undoes: int, from_v: str, to_v: str) -> dict[str, Any]:
    return {
        "type": "rollback",
        "sequence": seq,
        "at": "2026-11-12T11:00:00Z",
        "undoes_sequence": undoes,
        "from_version": from_v,
        "to_version": to_v,
        "to_config_sha256": config_sha(cfg(to_v)),
        "to_config": cfg(to_v),
        "reason": "wer went up",
        "by": "Owner",
    }


def write_ledger(path: Path, lines: list[Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    return path


def read_ledger(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def active(ledger: Path) -> dict[str, Any]:
    proc = promotion("active", ledger)
    assert proc.returncode == 0, proc.stderr.decode()
    return json.loads(proc.stdout)


def test_b1_record_blocked_does_not_create_ledger(case: Case, tmp_path: Path) -> None:
    manifest_with(approval=None)(case)
    request = case.write()
    ledger = tmp_path / "ledger" / "promotions.jsonl"
    ledger.parent.mkdir()
    expected = json.loads(promotion("check", request).stdout)
    proc = promotion("record", request, ledger)
    assert proc.returncode == 2
    d = json.loads(proc.stdout)
    assert d == expected
    assert d["decision"] == "blocked"
    assert not ledger.exists()


def test_b1_record_blocked_leaves_ledger_unchanged(case: Case, tmp_path: Path) -> None:
    at("cr", ACC, 0.25)(case)
    request = case.write()
    ledger = write_ledger(tmp_path / "promotions.jsonl", [promo_line(1, "whisper@p6", None)])
    before = ledger.read_bytes()
    proc = promotion("record", request, ledger)
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["decision"] == "blocked"
    assert ledger.read_bytes() == before


def test_b3_record_first_promotion(case: Case, tmp_path: Path) -> None:
    no_incumbent(case)
    request = case.write()
    ledger = tmp_path / "promotions.jsonl"
    checked = promotion("check", request)
    assert checked.returncode == 0, checked.stderr.decode()
    proc = promotion("record", request, ledger)
    assert proc.returncode == 0, proc.stderr.decode()
    entry = json.loads(proc.stdout)
    assert set(entry) == PROMOTION_KEYS
    assert entry["type"] == "promotion"
    assert entry["sequence"] == 1
    assert ISO_UTC.match(entry["at"])
    assert entry["version"] == "whisper@p7"
    assert entry["config_sha256"] == config_sha(cfg("whisper@p7"))
    assert entry["criteria_sha256"] == sha_file(case.root / "criteria.json")
    assert entry["candidate_report_sha256"] == sha_file(case.root / "reports" / "cand.json")
    assert entry["decision_sha256"] in {
        sha_bytes(checked.stdout),
        sha_bytes(checked.stdout.rstrip(b"\n")),
    }
    assert entry["rollback_manifest"] == json.loads((case.root / "rollback.json").read_bytes())
    assert read_ledger(ledger) == [entry]
    assert active(ledger) == {
        "version": "whisper@p7",
        "config_sha256": config_sha(cfg("whisper@p7")),
        "sequence": 1,
    }


def test_b3_record_into_empty_existing_ledger_skips_stale_check(case: Case, tmp_path: Path) -> None:
    ledger = tmp_path / "promotions.jsonl"
    ledger.write_bytes(b"")
    proc = promotion("record", case.write(), ledger)
    assert proc.returncode == 0, proc.stderr.decode()
    assert [e["sequence"] for e in read_ledger(ledger)] == [1]


def test_b2_record_with_matching_incumbent_appends(case: Case, tmp_path: Path) -> None:
    ledger = write_ledger(tmp_path / "promotions.jsonl", [promo_line(1, "whisper@p6", None)])
    proc = promotion("record", case.write(), ledger)
    assert proc.returncode == 0, proc.stderr.decode()
    entry = json.loads(proc.stdout)
    assert entry["sequence"] == 2
    assert entry["version"] == "whisper@p7"
    lines = read_ledger(ledger)
    assert len(lines) == 2
    assert lines[0] == promo_line(1, "whisper@p6", None)
    assert lines[1] == entry


@pytest.mark.parametrize(
    "ledger_lines",
    [
        pytest.param([promo_line(1, "whisper@p5", None)], id="other-version"),
        pytest.param(
            [{**promo_line(1, "whisper@p6", None), "config_sha256": config_sha(cfg("x"))}],
            id="same-version-other-config",
        ),
        pytest.param(
            [
                promo_line(1, "whisper@p5", None),
                promo_line(2, "whisper@p6", "whisper@p5"),
                rollback_line(3, 2, "whisper@p6", "whisper@p5"),
            ],
            id="incumbent-was-rolled-back",
        ),
    ],
)
def test_b2_record_stale_incumbent_refused(
    case: Case, tmp_path: Path, ledger_lines: list[Any]
) -> None:
    ledger = write_ledger(tmp_path / "promotions.jsonl", ledger_lines)
    before = ledger.read_bytes()
    proc = promotion("record", case.write(), ledger)
    assert proc.returncode == 2
    assert proc.stderr.decode().startswith("error:")
    assert ledger.read_bytes() == before


def test_b2_record_without_incumbent_but_active_refused(case: Case, tmp_path: Path) -> None:
    no_incumbent(case)
    ledger = write_ledger(tmp_path / "promotions.jsonl", [promo_line(1, "whisper@p6", None)])
    before = ledger.read_bytes()
    proc = promotion("record", case.write(), ledger)
    assert proc.returncode == 2
    assert proc.stderr.decode().startswith("error:")
    assert ledger.read_bytes() == before


def test_b2_record_after_rollback_matches_restored_version(case: Case, tmp_path: Path) -> None:
    ledger = write_ledger(
        tmp_path / "promotions.jsonl",
        [
            promo_line(1, "whisper@p6", None),
            promo_line(2, "whisper@p8", "whisper@p6"),
            rollback_line(3, 2, "whisper@p8", "whisper@p6"),
        ],
    )
    proc = promotion("record", case.write(), ledger)
    assert proc.returncode == 0, proc.stderr.decode()
    assert json.loads(proc.stdout)["sequence"] == 4


def test_record_malformed_ledger_is_input_error(case: Case, tmp_path: Path) -> None:
    ledger = tmp_path / "promotions.jsonl"
    ledger.write_text("not json\n", encoding="utf-8")
    proc = promotion("record", case.write(), ledger)
    assert_input_error(proc)
    assert ledger.read_text(encoding="utf-8") == "not json\n"


def test_c1_rollback_restores_previous(tmp_path: Path) -> None:
    ledger = write_ledger(
        tmp_path / "promotions.jsonl", [promo_line(1, "whisper@p7", "whisper@p6")]
    )
    proc = promotion("rollback", ledger, "--reason", "wer went up", "--by", "Owner")
    assert proc.returncode == 0, proc.stderr.decode()
    entry = json.loads(proc.stdout)
    assert set(entry) == ROLLBACK_KEYS
    assert entry["type"] == "rollback"
    assert entry["sequence"] == 2
    assert ISO_UTC.match(entry["at"])
    assert entry["undoes_sequence"] == 1
    assert entry["from_version"] == "whisper@p7"
    assert entry["to_version"] == "whisper@p6"
    assert entry["to_config_sha256"] == config_sha(cfg("whisper@p6"))
    assert entry["to_config"] == cfg("whisper@p6")
    assert entry["reason"] == "wer went up"
    assert entry["by"] == "Owner"
    lines = read_ledger(ledger)
    assert len(lines) == 2
    assert lines[1] == entry
    assert active(ledger) == {
        "version": "whisper@p6",
        "config_sha256": config_sha(cfg("whisper@p6")),
        "sequence": 2,
    }


def test_c1_rollback_undoes_top_of_stack(tmp_path: Path) -> None:
    ledger = write_ledger(
        tmp_path / "promotions.jsonl",
        [
            promo_line(1, "whisper@p6", "whisper@p5"),
            promo_line(2, "whisper@p7", "whisper@p6"),
            rollback_line(3, 2, "whisper@p7", "whisper@p6"),
            promo_line(4, "whisper@p8", "whisper@p6"),
        ],
    )
    first = promotion("rollback", ledger, "--reason", "r1", "--by", "Owner")
    assert first.returncode == 0, first.stderr.decode()
    e1 = json.loads(first.stdout)
    assert (e1["sequence"], e1["undoes_sequence"], e1["from_version"], e1["to_version"]) == (
        5,
        4,
        "whisper@p8",
        "whisper@p6",
    )
    second = promotion("rollback", ledger, "--reason", "r2", "--by", "Owner")
    assert second.returncode == 0, second.stderr.decode()
    e2 = json.loads(second.stdout)
    assert (e2["sequence"], e2["undoes_sequence"], e2["from_version"], e2["to_version"]) == (
        6,
        1,
        "whisper@p6",
        "whisper@p5",
    )
    assert active(ledger)["version"] == "whisper@p5"


@pytest.mark.parametrize(
    "lines",
    [
        pytest.param([], id="empty-ledger"),
        pytest.param([promo_line(1, "whisper@p7", None)], id="previous-null"),
        pytest.param(
            [
                promo_line(1, "whisper@p7", "whisper@p6"),
                rollback_line(2, 1, "whisper@p7", "whisper@p6"),
            ],
            id="stack-emptied",
        ),
    ],
)
def test_c2_rollback_refused(tmp_path: Path, lines: list[Any]) -> None:
    ledger = write_ledger(tmp_path / "promotions.jsonl", lines)
    before = ledger.read_bytes()
    proc = promotion("rollback", ledger, "--reason", "r", "--by", "Owner")
    assert proc.returncode == 2
    assert ledger.read_bytes() == before


def test_c2_rollback_absent_ledger_refused(tmp_path: Path) -> None:
    ledger = tmp_path / "promotions.jsonl"
    proc = promotion("rollback", ledger, "--reason", "r", "--by", "Owner")
    assert proc.returncode == 2
    assert not ledger.exists()


@pytest.mark.parametrize(
    ("reason", "by"),
    [
        pytest.param("", "Owner", id="reason-empty"),
        pytest.param("   ", "Owner", id="reason-blank"),
        pytest.param("r", "", id="by-empty"),
        pytest.param("r", " ", id="by-blank"),
    ],
)
def test_c2_rollback_blank_arguments_are_input_errors(tmp_path: Path, reason: str, by: str) -> None:
    ledger = write_ledger(
        tmp_path / "promotions.jsonl", [promo_line(1, "whisper@p7", "whisper@p6")]
    )
    before = ledger.read_bytes()
    assert_input_error(promotion("rollback", ledger, "--reason", reason, "--by", by))
    assert ledger.read_bytes() == before


def chain_case(root: Path, candidate: str, incumbent: str) -> Case:
    return make_case(root, candidate=candidate, incumbent=incumbent)


def test_c3_two_promotions_two_rollbacks(tmp_path: Path) -> None:
    ledger = tmp_path / "promotions.jsonl"
    a, b, c = "whisper@a", "whisper@b", "whisper@c"
    p1 = promotion("record", chain_case(tmp_path / "p1", b, a).write(), ledger)
    assert p1.returncode == 0, p1.stderr.decode()
    assert active(ledger) == {"version": b, "config_sha256": config_sha(cfg(b)), "sequence": 1}
    p2 = promotion("record", chain_case(tmp_path / "p2", c, b).write(), ledger)
    assert p2.returncode == 0, p2.stderr.decode()
    assert active(ledger) == {"version": c, "config_sha256": config_sha(cfg(c)), "sequence": 2}
    r1 = promotion("rollback", ledger, "--reason", "r1", "--by", "Owner")
    assert r1.returncode == 0, r1.stderr.decode()
    assert json.loads(r1.stdout)["to_config"] == cfg(b)
    assert active(ledger) == {"version": b, "config_sha256": config_sha(cfg(b)), "sequence": 3}
    r2 = promotion("rollback", ledger, "--reason", "r2", "--by", "Owner")
    assert r2.returncode == 0, r2.stderr.decode()
    assert json.loads(r2.stdout)["to_config"] == cfg(a)
    assert active(ledger) == {"version": a, "config_sha256": config_sha(cfg(a)), "sequence": 4}
    assert [e["type"] for e in read_ledger(ledger)] == [
        "promotion",
        "promotion",
        "rollback",
        "rollback",
    ]
    r3 = promotion("rollback", ledger, "--reason", "r3", "--by", "Owner")
    assert r3.returncode == 2
    assert len(read_ledger(ledger)) == 4


def test_d_active_absent_ledger(tmp_path: Path) -> None:
    ledger = tmp_path / "promotions.jsonl"
    assert active(ledger) == {"version": None, "config_sha256": None, "sequence": None}
    assert not ledger.exists()


def test_d_active_empty_ledger(tmp_path: Path) -> None:
    ledger = tmp_path / "promotions.jsonl"
    ledger.write_bytes(b"")
    assert active(ledger) == {"version": None, "config_sha256": None, "sequence": None}


def test_d_active_after_promotions(tmp_path: Path) -> None:
    ledger = write_ledger(
        tmp_path / "promotions.jsonl",
        [promo_line(1, "whisper@p6", None), promo_line(2, "whisper@p7", "whisper@p6")],
    )
    assert active(ledger) == {
        "version": "whisper@p7",
        "config_sha256": config_sha(cfg("whisper@p7")),
        "sequence": 2,
    }


@pytest.mark.parametrize(
    "lines",
    [
        pytest.param(["not json"], id="malformed-line"),
        pytest.param(
            [json.dumps({**promo_line(1, "whisper@p7", None), "type": "promote"})],
            id="unknown-type",
        ),
        pytest.param([json.dumps(promo_line(2, "whisper@p7", None))], id="sequence-starts-at-2"),
        pytest.param([json.dumps(promo_line(0, "whisper@p7", None))], id="sequence-starts-at-0"),
        pytest.param(
            [
                json.dumps(promo_line(1, "whisper@p6", None)),
                json.dumps(promo_line(3, "whisper@p7", "whisper@p6")),
            ],
            id="sequence-gap",
        ),
        pytest.param(
            [
                json.dumps(promo_line(1, "whisper@p6", None)),
                json.dumps(promo_line(1, "whisper@p7", "whisper@p6")),
            ],
            id="sequence-repeat",
        ),
        pytest.param(["[]"], id="line-not-object"),
    ],
)
def test_d_active_bad_ledger_is_input_error(tmp_path: Path, lines: list[str]) -> None:
    ledger = tmp_path / "promotions.jsonl"
    ledger.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    assert_input_error(promotion("active", ledger))


def correction(
    cid: str, kind: str = "asr", model: str = "whisper@p7", **over: Any
) -> dict[str, Any]:
    rec = {
        "correction_id": cid,
        "origin": "production_correction",
        "kind": kind,
        "model_version": model,
        "prompt_version": None,
        "workspace_ref": "ws-opaque-ref",
        "subject_ref": f"transcript_revision:{cid}",
        "corrected_at": "2026-11-12T10:00:00Z",
        "corrected_by_role": "supervisor",
        "before": {"text": "salam ali"},
        "after": {"text": "salam aly"},
    }
    rec.update(over)
    return rec


def write_records(path: Path, records: list[Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    return path


def test_e1_append_new_store(tmp_path: Path) -> None:
    records = [correction("c-1"), correction("c-2", kind="intent", prompt_version="p-3")]
    store = tmp_path / "store" / "corrections.jsonl"
    store.parent.mkdir()
    proc = corrections("append", store, write_records(tmp_path / "in.jsonl", records))
    assert proc.returncode == 0, proc.stderr.decode()
    assert json.loads(proc.stdout) == {"appended": 2, "total": 2}
    assert read_ledger(store) == records


def test_e1_append_to_existing_store_keeps_order(tmp_path: Path) -> None:
    store = write_records(tmp_path / "corrections.jsonl", [correction("c-1")])
    batch = [correction("c-3"), correction("c-2")]
    proc = corrections("append", store, write_records(tmp_path / "in.jsonl", batch))
    assert proc.returncode == 0, proc.stderr.decode()
    assert json.loads(proc.stdout) == {"appended": 2, "total": 3}
    assert [r["correction_id"] for r in read_ledger(store)] == ["c-1", "c-3", "c-2"]


@pytest.mark.parametrize("kind", list(KINDS))
def test_e1_every_evaluation_kind_is_accepted(tmp_path: Path, kind: str) -> None:
    store = tmp_path / "corrections.jsonl"
    proc = corrections(
        "append", store, write_records(tmp_path / "in.jsonl", [correction("c-1", kind=kind)])
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert json.loads(proc.stdout) == {"appended": 1, "total": 1}


CORRECTION_KEYS = [
    "correction_id",
    "origin",
    "kind",
    "model_version",
    "prompt_version",
    "workspace_ref",
    "subject_ref",
    "corrected_at",
    "corrected_by_role",
    "before",
    "after",
]
BAD_CORRECTIONS = [
    *[
        pytest.param({k: v for k, v in correction("c-9").items() if k != key}, id=f"missing-{key}")
        for key in CORRECTION_KEYS
    ],
    pytest.param(correction("c-9", extra=1), id="unknown-key"),
    pytest.param(correction("c-9", origin="gold"), id="origin-gold"),
    pytest.param(correction("c-9", origin="Production_Correction"), id="origin-case"),
    pytest.param(correction("c-9", kind="ASR"), id="kind-case"),
    pytest.param(correction("c-9", kind="summary"), id="kind-unknown"),
    pytest.param(correction(" "), id="correction-id-blank"),
    pytest.param(correction("c-9", model_version=""), id="model-version-blank"),
    pytest.param(correction("c-9", prompt_version=" "), id="prompt-version-blank"),
    pytest.param(correction("c-9", prompt_version=7), id="prompt-version-int"),
    pytest.param(correction("c-9", workspace_ref=""), id="workspace-ref-blank"),
    pytest.param(correction("c-9", subject_ref=None), id="subject-ref-null"),
    pytest.param(correction("c-9", corrected_by_role=" "), id="role-blank"),
    pytest.param(
        correction("c-9", corrected_at="2026-11-12T10:00:00+00:00"), id="corrected-at-offset"
    ),
    pytest.param(correction("c-9", corrected_at="2026-11-12T10:00Z"), id="corrected-at-no-seconds"),
    pytest.param(correction("c-9", before=["text"]), id="before-list"),
    pytest.param(correction("c-9", after=None), id="after-null"),
    pytest.param(correction("c-9", after="text"), id="after-str"),
    pytest.param([correction("c-9")], id="record-not-object"),
]


@pytest.mark.parametrize("bad", BAD_CORRECTIONS)
def test_e1_invalid_record_writes_nothing(tmp_path: Path, bad: Any) -> None:
    store = write_records(tmp_path / "corrections.jsonl", [correction("c-1")])
    before = store.read_bytes()
    proc = corrections(
        "append", store, write_records(tmp_path / "in.jsonl", [correction("c-2"), bad])
    )
    assert_input_error(proc)
    assert store.read_bytes() == before


def test_e1_invalid_record_does_not_create_store(tmp_path: Path) -> None:
    store = tmp_path / "corrections.jsonl"
    proc = corrections(
        "append", store, write_records(tmp_path / "in.jsonl", [correction("c-1", origin="gold")])
    )
    assert_input_error(proc)
    assert not store.exists() or store.read_bytes() == b""


def test_e1_duplicate_inside_input(tmp_path: Path) -> None:
    store = write_records(tmp_path / "corrections.jsonl", [correction("c-1")])
    before = store.read_bytes()
    proc = corrections(
        "append",
        store,
        write_records(tmp_path / "in.jsonl", [correction("c-2"), correction("c-2")]),
    )
    assert_input_error(proc)
    assert store.read_bytes() == before


def test_e1_duplicate_with_store(tmp_path: Path) -> None:
    store = write_records(tmp_path / "corrections.jsonl", [correction("c-1")])
    before = store.read_bytes()
    proc = corrections(
        "append",
        store,
        write_records(tmp_path / "in.jsonl", [correction("c-2"), correction("c-1")]),
    )
    assert_input_error(proc)
    assert store.read_bytes() == before


def test_e1_bad_json_input(tmp_path: Path) -> None:
    store = write_records(tmp_path / "corrections.jsonl", [correction("c-1")])
    before = store.read_bytes()
    bad = tmp_path / "in.jsonl"
    bad.write_text(json.dumps(correction("c-2")) + "\n{oops\n", encoding="utf-8")
    assert_input_error(corrections("append", store, bad))
    assert store.read_bytes() == before


def corpus_layout(tmp_path: Path) -> tuple[Path, Path]:
    corpus = tmp_path / "data" / "corpus"
    (corpus / "asr").mkdir(parents=True)
    return corpus, write_records(tmp_path / "in.jsonl", [correction("c-1")])


@pytest.mark.parametrize(
    "store_rel",
    [
        pytest.param("data/corpus/corrections.jsonl", id="directly-inside"),
        pytest.param("data/corpus/asr/corrections.jsonl", id="nested-inside"),
        pytest.param("data/other/../corpus/corrections.jsonl", id="dot-dot-into-corpus"),
    ],
)
def test_e2_store_inside_corpus_refused(tmp_path: Path, store_rel: str) -> None:
    corpus, inp = corpus_layout(tmp_path)
    (tmp_path / "data" / "other").mkdir()
    store = tmp_path / store_rel
    proc = corrections("append", store, inp, "--corpus-root", corpus)
    assert proc.returncode == 2
    assert not (corpus / "corrections.jsonl").exists()
    assert not (corpus / "asr" / "corrections.jsonl").exists()


def test_e2_existing_store_inside_corpus_unchanged(tmp_path: Path) -> None:
    corpus, inp = corpus_layout(tmp_path)
    store = write_records(corpus / "corrections.jsonl", [correction("c-0")])
    before = store.read_bytes()
    proc = corrections("append", store, inp, "--corpus-root", corpus)
    assert proc.returncode == 2
    assert store.read_bytes() == before


def test_e2_store_inside_second_corpus_root_refused(tmp_path: Path) -> None:
    corpus, inp = corpus_layout(tmp_path)
    first = tmp_path / "gold"
    first.mkdir()
    proc = corrections(
        "append", corpus / "c.jsonl", inp, "--corpus-root", first, "--corpus-root", corpus
    )
    assert proc.returncode == 2
    assert not (corpus / "c.jsonl").exists()


def test_e2_store_through_symlink_into_corpus_refused(tmp_path: Path) -> None:
    corpus, inp = corpus_layout(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(corpus, target_is_directory=True)
    proc = corrections("append", link / "c.jsonl", inp, "--corpus-root", corpus)
    assert proc.returncode == 2
    assert not (corpus / "c.jsonl").exists()


def test_e2_corpus_root_given_as_symlink_refused(tmp_path: Path) -> None:
    corpus, inp = corpus_layout(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(corpus, target_is_directory=True)
    proc = corrections("append", corpus / "c.jsonl", inp, "--corpus-root", link)
    assert proc.returncode == 2
    assert not (corpus / "c.jsonl").exists()


def test_e2_relative_corpus_root_refused(tmp_path: Path) -> None:
    corpus, inp = corpus_layout(tmp_path)
    proc = corrections(
        "append", "data/corpus/c.jsonl", inp, "--corpus-root", "data/corpus", cwd=tmp_path
    )
    assert proc.returncode == 2
    assert not (corpus / "c.jsonl").exists()


@pytest.mark.parametrize(
    "store_rel",
    [
        pytest.param("data/corrections/c.jsonl", id="sibling-dir"),
        pytest.param("data/corpus-corrections/c.jsonl", id="sibling-sharing-name-prefix"),
        pytest.param("data/c.jsonl", id="parent-dir"),
    ],
)
def test_e2_store_outside_corpus_appends(tmp_path: Path, store_rel: str) -> None:
    corpus, inp = corpus_layout(tmp_path)
    store = tmp_path / store_rel
    store.parent.mkdir(parents=True, exist_ok=True)
    proc = corrections("append", store, inp, "--corpus-root", corpus)
    assert proc.returncode == 0, proc.stderr.decode()
    assert json.loads(proc.stdout) == {"appended": 1, "total": 1}
    assert [r["correction_id"] for r in read_ledger(store)] == ["c-1"]


def test_e3_summary(tmp_path: Path) -> None:
    store = write_records(
        tmp_path / "corrections.jsonl",
        [
            correction("c-1", kind="intent", model="m-b"),
            correction("c-2", kind="asr", model="m-a"),
            correction("c-3", kind="asr", model="m-b"),
        ],
    )
    proc = corrections("summary", store)
    assert proc.returncode == 0, proc.stderr.decode()
    pairs = json.loads(proc.stdout, object_pairs_hook=list)
    as_dict = {k: dict(v) if isinstance(v, list) else v for k, v in pairs}
    assert as_dict == {
        "total": 3,
        "by_kind": {"asr": 2, "intent": 1},
        "by_model_version": {"m-a": 1, "m-b": 2},
    }
    assert [k for k, _ in pairs] == sorted(k for k, _ in pairs)
    for _, v in pairs:
        if isinstance(v, list):
            assert [k for k, _ in v] == sorted(k for k, _ in v)


def test_e3_summary_after_append(tmp_path: Path) -> None:
    store = tmp_path / "corrections.jsonl"
    inp = write_records(
        tmp_path / "in.jsonl", [correction("c-1"), correction("c-2", kind="intent")]
    )
    assert corrections("append", store, inp).returncode == 0
    proc = corrections("summary", store)
    assert proc.returncode == 0, proc.stderr.decode()
    assert json.loads(proc.stdout) == {
        "total": 2,
        "by_kind": {"asr": 1, "intent": 1},
        "by_model_version": {"whisper@p7": 2},
    }


ASR_RECORD = {"reference": "salam ali", "hypothesis": "salam aly", "slices": {"channel": "web"}}


def assert_correction_refused(exc: pytest.ExceptionInfo[ValueError]) -> None:
    assert "production correction" in str(exc.value)


@pytest.mark.parametrize("kind", list(KINDS))
def test_e4_parse_record_refuses_before_kind_parsing(kind: str) -> None:
    with pytest.raises(ValueError) as exc:
        parse_record(kind, {"origin": "production_correction"})
    assert_correction_refused(exc)


def test_e4_parse_record_refuses_valid_record_with_correction_origin() -> None:
    with pytest.raises(ValueError) as exc:
        parse_record("asr", {**ASR_RECORD, "origin": "production_correction"})
    assert_correction_refused(exc)


def test_e4_parse_record_refuses_full_correction_record() -> None:
    with pytest.raises(ValueError) as exc:
        parse_record("asr", correction("c-1"))
    assert_correction_refused(exc)


@pytest.mark.parametrize(
    "origin",
    [
        pytest.param("gold", id="gold"),
        pytest.param(None, id="null"),
        pytest.param("production", id="production"),
    ],
)
def test_e4_other_origin_loads_as_before(origin: Any) -> None:
    assert parse_record("asr", {**ASR_RECORD, "origin": origin}) == parse_record("asr", ASR_RECORD)


def test_e4_load_jsonl_refuses_correction_line(tmp_path: Path) -> None:
    path = write_records(
        tmp_path / "asr.jsonl", [ASR_RECORD, {**ASR_RECORD, "origin": "production_correction"}]
    )
    with pytest.raises(ValueError) as exc:
        load_jsonl("asr", path)
    assert_correction_refused(exc)


def test_e4_load_jsonl_refuses_corrections_store(tmp_path: Path) -> None:
    store = write_records(tmp_path / "corrections.jsonl", [correction("c-1"), correction("c-2")])
    with pytest.raises(ValueError) as exc:
        load_jsonl("asr", store)
    assert_correction_refused(exc)


def test_e4_load_jsonl_other_origin_loads_as_before(tmp_path: Path) -> None:
    plain = write_records(tmp_path / "plain.jsonl", [ASR_RECORD, ASR_RECORD])
    gold = write_records(tmp_path / "gold.jsonl", [{**ASR_RECORD, "origin": "gold"}, ASR_RECORD])
    assert load_jsonl("asr", gold) == load_jsonl("asr", plain)


def test_e4_evaluation_cli_refuses_correction(tmp_path: Path) -> None:
    path = write_records(
        tmp_path / "asr.jsonl", [ASR_RECORD, {**ASR_RECORD, "origin": "production_correction"}]
    )
    proc = run_module("backend.evaluation", "asr", path)
    assert_input_error(proc)
    assert "production correction" in proc.stderr.decode()


def candidate_copy(tmp_path: Path, name: str, origin: str | None) -> Path:
    target = tmp_path / name
    shutil.copytree(FIXTURES / "candidate", target)
    if origin is not None:
        intent = target / "intent.jsonl"
        lines = intent.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        lines[0] = json.dumps({**first, "origin": origin}, ensure_ascii=False)
        intent.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return target / "manifest.json"


def test_e4_candidate_cli_refuses_correction(tmp_path: Path) -> None:
    proc = run_module(
        "backend.evaluation.candidate", candidate_copy(tmp_path, "c", "production_correction")
    )
    assert_input_error(proc)
    assert "production correction" in proc.stderr.decode()


def test_e4_candidate_cli_other_origin_report_unchanged(tmp_path: Path) -> None:
    plain = run_module("backend.evaluation.candidate", candidate_copy(tmp_path, "plain", None))
    gold = run_module("backend.evaluation.candidate", candidate_copy(tmp_path, "gold", "gold"))
    assert plain.returncode == 0, plain.stderr.decode()
    assert gold.returncode == 0, gold.stderr.decode()
    p, g = json.loads(plain.stdout), json.loads(gold.stdout)
    for name in p["evaluations"]:
        assert g["evaluations"][name]["slices"] == p["evaluations"][name]["slices"]
    assert g["measurements"] == p["measurements"]
