"""The promotion gate: criteria, decision checks, ledger and CLI (HOM-71)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from backend.evaluation.candidate import config_sha256
from backend.evaluation.promotion import main as promotion_main

HOMAYAR_ROOT = Path(__file__).resolve().parents[2]
ZERO_SHA = "0" * 64

FILE_NAMES = {
    "criteria": "criteria.json",
    "cand": "cand.json",
    "inc": "inc.json",
    "repro": "cand-run2.json",
    "rollback": "rollback.json",
    "request": "request.json",
}


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = promotion_main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_of(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def sha_text(text: str) -> str:
    return sha_bytes(text.encode("utf-8"))


def write_json(path: Path, doc: Any) -> Path:
    text = json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    return path


def measurements_doc(
    *,
    reason: str | None = None,
    p95: float | None = 900.0,
    mean: float | None = 0.001,
    currency: str | None = "USD",
    providers: dict[str, int] | None = None,
    n_missing: int = 0,
    slice_present: bool = True,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "n_requests": 4,
        "insufficient_sample": False,
        "latency_ms": {"n": 4, "p95": p95, "reason": None if p95 is not None else "no_latency"},
        "cost": {
            "n": 4,
            "mean_per_request": mean,
            "currency": currency,
            "reason": None if mean is not None else "no_cost",
        },
        "tokens": {},
        "providers": providers if providers is not None else {"llamacpp": 4},
        "n_missing_provider": n_missing,
    }
    return {
        "path": "measurements.jsonl",
        "input_sha256": ZERO_SHA,
        "currency": currency,
        "reason": reason,
        "slices": {"all": entry} if slice_present else {},
    }


def not_measured_doc() -> dict[str, Any]:
    return {"path": None, "input_sha256": None, "currency": None, "reason": "not_measured",
            "slices": {}}  # fmt: skip


def report_doc(
    candidate: str,
    config: dict[str, Any] | None,
    wer: float | None,
    *,
    n: int = 4,
    version: str = "candidate-report/1",
    evaluation_version: str = "evaluation/1",
    measurements: dict[str, Any] | None = None,
    insufficient: bool = False,
    with_measurements: bool = True,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "version": version,
        "evaluation_version": evaluation_version,
        "candidate": candidate,
        "config": config,
        "config_sha256": config_sha256(config),
        "manifest_sha256": ZERO_SHA,
        "slices": [],
        "min_slice_size": 1,
        "bootstrap": {
            "method": "percentile_bootstrap",
            "resamples": 0,
            "seed": 0,
            "confidence": 0.95,
        },  # fmt: skip
        "evaluations": {
            "asr": {
                "kind": "asr",
                "path": "asr.jsonl",
                "input_sha256": ZERO_SHA,
                "options": {},
                "slices": {
                    "all": {
                        "n_input_items": n,
                        "insufficient_sample": insufficient,
                        "metrics": {"wer": {"value": wer}},
                        "detail": {},
                    }
                },
            }
        },
        "insufficient_slices": [],
    }
    if with_measurements:
        doc["measurements"] = measurements if measurements is not None else measurements_doc()
    return doc


def set_wer(doc: dict[str, Any], wer: float | None) -> None:
    doc["evaluations"]["asr"]["slices"]["all"]["metrics"]["wer"]["value"] = wer


def criteria_doc(
    *,
    threshold: float = 0.25,
    direction: str = "max",
    n_items: int = 4,
    max_regression: float | None = 0.01,
    gate_tolerance: float | None = None,
    default_tolerance: float = 0.01,
    min_reproductions: int = 1,
    approver_roles: tuple[str, ...] = ("ml-lead", "product-owner"),
    required: bool = True,
    max_p95: float | None = 5000.0,
    max_cost: float | None = 0.002,
    currency: str | None = "USD",
    single_provider: bool = True,
    evaluation_version: str = "evaluation/1",
) -> dict[str, Any]:
    return {
        "version": "release-criteria/1",
        "criteria_id": "asr-release-1",
        "frozen_at": "2026-11-01T00:00:00Z",
        "frozen_by": "ml-lead",
        "evaluation_version": evaluation_version,
        "default_tolerance": default_tolerance,
        "min_reproductions": min_reproductions,
        "approver_roles": list(approver_roles),
        "gates": [
            {
                "evaluation": "asr",
                "metric": "wer",
                "slice": "all",
                "direction": direction,
                "threshold": threshold,
                "n_items": n_items,
                "max_regression": max_regression,
                "tolerance": gate_tolerance,
            }
        ],
        "measurements": {
            "required": required,
            "slice": "all",
            "max_p95_latency_ms": max_p95,
            "max_mean_cost_per_request": max_cost,
            "currency": currency,
            "single_provider": single_provider,
        },
    }


def rollback_doc(
    cand: dict[str, Any],
    inc: dict[str, Any] | None,
    cand_sha: str,
    inc_sha: str | None,
    criteria_sha: str,
    *,
    approval_role: str | None = "ml-lead",
) -> dict[str, Any]:
    return {
        "version": "rollback-manifest/1",
        "promoted_version": cand["candidate"],
        "promoted_config_sha256": cand["config_sha256"],
        "previous_version": inc["candidate"] if inc is not None else None,
        "previous_config_sha256": inc["config_sha256"] if inc is not None else None,
        "previous_config": inc["config"] if inc is not None else None,
        "candidate_report_sha256": cand_sha,
        "incumbent_report_sha256": inc_sha,
        "criteria_sha256": criteria_sha,
        "triggers": [
            {
                "evaluation": "asr",
                "metric": "wer",
                "slice": "all",
                "direction": "max",
                "threshold": 0.30,
            }  # fmt: skip
        ],
        "owner_role": "ml-lead",
        "verification_steps": ["re-run the held-out ASR evaluation"],
        "approval": None
        if approval_role is None
        else {"approved_by": "Sina", "role": approval_role, "approved_at": "2026-11-12T09:00:00Z"},
    }


def request_doc(
    criteria_sha: str,
    *,
    incumbent: bool = True,
    reproduction: bool = True,
    rollback: bool = True,
) -> dict[str, Any]:
    return {
        "candidate_report": "cand.json",
        "incumbent_report": "inc.json" if incumbent else None,
        "reproduction_reports": ["cand-run2.json"] if reproduction else [],
        "criteria": "criteria.json",
        "criteria_sha256": criteria_sha,
        "rollback_manifest": "rollback.json" if rollback else None,
    }


@dataclass
class Case:
    root: Path
    docs: dict[str, Any] = field(default_factory=dict)

    def path(self, name: str) -> Path:
        return self.root / FILE_NAMES[name]

    def rewrite(self, name: str) -> None:
        write_json(self.path(name), self.docs[name])

    def repin_criteria(self) -> None:
        self.rewrite("criteria")
        self.docs["request"]["criteria_sha256"] = sha_of(self.path("criteria"))
        self.docs["rollback"]["criteria_sha256"] = self.docs["request"]["criteria_sha256"]
        self.rewrite("rollback")
        self.rewrite("request")

    def sync_manifest(self) -> None:
        """Re-pin the report hashes after rewriting the cand or inc file."""
        self.docs["rollback"]["candidate_report_sha256"] = sha_of(self.path("cand"))
        self.docs["rollback"]["incumbent_report_sha256"] = (
            sha_of(self.path("inc")) if self.path("inc").exists() else None
        )
        self.rewrite("rollback")


def base_case(
    tmp_path: Path,
    *,
    incumbent: bool = True,
    reproduction: bool = True,
    rollback: bool = True,
    cand_wer: float = 0.18,
    inc_wer: float = 0.19,
    repro_wer: float = 0.181,
    measurements: dict[str, Any] | None = None,
) -> Case:
    root = tmp_path / "case"
    root.mkdir(parents=True)
    case = Case(root)
    case.docs["cand"] = report_doc("whisper-v3@p7", {"temperature": 0.0}, cand_wer,
                                   measurements=measurements)  # fmt: skip
    case.docs["inc"] = report_doc("whisper-v3@p6", {"temperature": 0.2}, inc_wer,
                                  with_measurements=False)  # fmt: skip
    case.docs["repro"] = report_doc("whisper-v3@p7", {"temperature": 0.0}, repro_wer,
                                    with_measurements=False)  # fmt: skip
    case.docs["criteria"] = criteria_doc()
    write_json(case.path("criteria"), case.docs["criteria"])
    write_json(case.path("cand"), case.docs["cand"])
    cand_sha = sha_of(case.path("cand"))
    inc_sha: str | None = None
    if incumbent:
        write_json(case.path("inc"), case.docs["inc"])
        inc_sha = sha_of(case.path("inc"))
    if reproduction:
        write_json(case.path("repro"), case.docs["repro"])
    criteria_sha = sha_of(case.path("criteria"))
    case.docs["rollback"] = rollback_doc(
        case.docs["cand"], case.docs["inc"] if incumbent else None, cand_sha, inc_sha, criteria_sha
    )
    if rollback:
        write_json(case.path("rollback"), case.docs["rollback"])
    case.docs["request"] = request_doc(
        criteria_sha, incumbent=incumbent, reproduction=reproduction, rollback=rollback
    )
    write_json(case.path("request"), case.docs["request"])
    return case


def codes_of(doc: dict[str, Any]) -> list[str]:
    return [r["code"] for r in doc["reasons"]]


def check(case: Case, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any], str]:
    code, out, err = run_cli(capsys, "check", str(case.path("request")))
    return code, json.loads(out), err


def _assert_input_error(code: int, out: str, err: str, fragment: str) -> None:
    assert code == 1
    assert out == ""
    assert err.startswith("error: "), err
    assert len(err.strip().splitlines()) == 1, err
    assert fragment in err


# The promotable baseline.


def test_baseline_promotes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path)
    code, doc, err = check(case, capsys)
    assert code == 0, (err, doc["reasons"])
    assert doc["decision"] == "promote"
    assert doc["reasons"] == []
    assert doc["criteria_id"] == "asr-release-1"
    assert doc["candidate"] == "whisper-v3@p7"
    assert doc["candidate_config_sha256"] == config_sha256({"temperature": 0.0})
    assert doc["candidate_report_sha256"] == sha_of(case.path("cand"))
    assert doc["incumbent"] == "whisper-v3@p6"
    assert doc["incumbent_config_sha256"] == config_sha256({"temperature": 0.2})
    assert doc["incumbent_report_sha256"] == sha_of(case.path("inc"))
    assert doc["rollback_manifest_sha256"] == sha_of(case.path("rollback"))
    assert doc["criteria_sha256"] == sha_of(case.path("criteria"))


def test_first_promotion_without_incumbent_promotes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, incumbent=False)
    code, doc, err = check(case, capsys)
    assert code == 0, (err, doc["reasons"])
    assert doc["incumbent"] is None
    assert doc["incumbent_config_sha256"] is None
    assert doc["incumbent_report_sha256"] is None


# Deny cases and their allow-controls (SPEC section 5), one change from a promotable base.


def test_threshold_missed_by_a_tiny_amount(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.docs["criteria"]["gates"][0]["max_regression"] = None
    case.repin_criteria()
    set_wer(case.docs["cand"], 0.25 + 1e-9)
    set_wer(case.docs["repro"], 0.25)
    case.rewrite("cand")
    case.rewrite("repro")
    case.sync_manifest()
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["threshold_missed"]
    detail = doc["reasons"][0]["detail"]
    assert "asr/wer/all" in detail and ">" in detail


def test_value_exactly_at_threshold_promotes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.docs["criteria"]["gates"][0]["max_regression"] = None
    case.repin_criteria()
    set_wer(case.docs["cand"], 0.25)
    set_wer(case.docs["repro"], 0.25)
    case.rewrite("cand")
    case.rewrite("repro")
    case.sync_manifest()
    code, doc, err = check(case, capsys)
    assert code == 0, (err, doc["reasons"])


def test_regression_just_over_max_regression(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, cand_wer=0.21, repro_wer=0.21)
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["regression"]


def test_regression_exactly_at_max_regression_promotes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, cand_wer=0.20, repro_wer=0.20)
    code, doc, err = check(case, capsys)
    assert code == 0, (err, doc["reasons"])


def test_null_max_regression_with_a_big_regression_promotes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, cand_wer=0.24, repro_wer=0.24)
    case.docs["criteria"]["gates"][0]["max_regression"] = None
    case.repin_criteria()
    code, doc, err = check(case, capsys)
    assert code == 0, (err, doc["reasons"])


def test_reproduction_differs_by_more_than_tolerance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, repro_wer=0.195)
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["not_reproducible"]


def test_reproduction_diff_exactly_at_tolerance_promotes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, cand_wer=0.1875, repro_wer=0.25)
    case.docs["criteria"]["gates"][0]["tolerance"] = 0.0625
    case.repin_criteria()
    code, doc, err = check(case, capsys)
    assert code == 0, (err, doc["reasons"])
    assert doc["gates"][0]["reproduction_values"] == [0.25]


def test_per_gate_tolerance_wins_over_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, repro_wer=0.195)
    case.docs["criteria"]["gates"][0]["tolerance"] = 0.05
    case.repin_criteria()
    code, doc, err = check(case, capsys)
    assert code == 0, (err, doc["reasons"])
    assert doc["gates"][0]["tolerance"] == 0.05


def test_approval_role_not_in_approver_roles(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.docs["rollback"]["approval"]["role"] = "intern"
    case.rewrite("rollback")
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["rollback_manifest_not_approved"]


def test_allowed_approval_role_promotes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path)
    case.docs["rollback"]["approval"]["role"] = "product-owner"
    case.rewrite("rollback")
    code, doc, err = check(case, capsys)
    assert code == 0, (err, doc["reasons"])


def test_manifest_previous_version_wrong(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.docs["rollback"]["previous_version"] = "whisper-v3@p5"
    case.rewrite("rollback")
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["rollback_manifest_mismatch"]
    assert "previous_version" in doc["reasons"][0]["detail"]


def test_criteria_pin_does_not_match_the_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.docs["request"]["criteria_sha256"] = "1" * 64
    case.rewrite("request")
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["criteria_changed"]


def test_criteria_edited_after_pinning(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path)
    case.path("criteria").write_bytes(case.path("criteria").read_bytes() + b" ")
    code, doc, err = check(case, capsys)
    assert code == 2
    assert "criteria_changed" in codes_of(doc)


def test_unedited_criteria_with_the_same_pin_promotes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, doc, err = check(base_case(tmp_path), capsys)
    assert code == 0, (err, doc["reasons"])


def test_measurements_required_and_not_measured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, measurements=not_measured_doc())
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["measurements_missing"]


def test_not_required_not_measured_and_null_limits_promote(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, measurements=not_measured_doc())
    case.docs["criteria"]["measurements"]["required"] = False
    case.docs["criteria"]["measurements"]["max_p95_latency_ms"] = None
    case.docs["criteria"]["measurements"]["max_mean_cost_per_request"] = None
    case.repin_criteria()
    code, doc, err = check(case, capsys)
    assert code == 0, (err, doc["reasons"])


def test_two_providers_with_single_provider(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(
        tmp_path, measurements=measurements_doc(providers={"llamacpp": 2, "openai": 2})
    )
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["provider_mixing"]


def test_one_provider_promotes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, doc, err = check(base_case(tmp_path), capsys)
    assert code == 0, (err, doc["reasons"])


def test_two_providers_with_single_provider_false_promotes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(
        tmp_path, measurements=measurements_doc(providers={"llamacpp": 2, "openai": 2})
    )
    case.docs["criteria"]["measurements"]["single_provider"] = False
    case.repin_criteria()
    code, doc, err = check(case, capsys)
    assert code == 0, (err, doc["reasons"])


# Report identity (A2, A3).


@pytest.mark.parametrize("role", ["cand", "inc", "repro"])
def test_report_version_mismatch(
    role: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.docs[role]["version"] = "candidate-report/2"
    case.rewrite(role)
    case.sync_manifest()
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["report_version_mismatch"]
    assert role in doc["reasons"][0]["detail"]


@pytest.mark.parametrize("role", ["cand", "inc", "repro"])
def test_evaluation_version_mismatch(
    role: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.docs[role]["evaluation_version"] = "evaluation/2"
    case.rewrite(role)
    case.sync_manifest()
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["evaluation_version_mismatch"]


def test_no_change_when_incumbent_is_the_candidate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.docs["inc"] = report_doc("whisper-v3@p7", {"temperature": 0.0}, 0.19,
                                  with_measurements=False)  # fmt: skip
    write_json(case.path("inc"), case.docs["inc"])
    inc_sha = sha_of(case.path("inc"))
    case.docs["rollback"]["previous_version"] = "whisper-v3@p7"
    case.docs["rollback"]["previous_config_sha256"] = config_sha256({"temperature": 0.0})
    case.docs["rollback"]["previous_config"] = {"temperature": 0.0}
    case.docs["rollback"]["incumbent_report_sha256"] = inc_sha
    case.rewrite("rollback")
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["no_change"]


# Gate checks on the candidate report (A4 to A7).


@pytest.mark.parametrize(
    ("where", "patch"),
    [
        ("evaluation", {"evaluation": "mt"}),
        ("slice", {"slice": "lang=fa"}),
        ("metric", {"metric": "cer"}),
    ],
    ids=["evaluation-absent", "slice-absent", "metric-absent"],
)
def test_gate_missing(
    where: str,
    patch: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = base_case(tmp_path)
    case.docs["criteria"]["gates"][0].update(patch)
    case.repin_criteria()
    code, doc, err = check(case, capsys)
    assert code == 2
    codes = codes_of(doc)
    assert "gate_missing" in codes
    detail = doc["reasons"][codes.index("gate_missing")]["detail"]
    assert where in detail
    assert "incumbent_gate_missing" in codes
    assert "not_reproducible" in codes


def test_gate_missing_when_value_is_null(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, cand_wer=None, repro_wer=None)
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["gate_missing", "not_reproducible"]
    assert "value is null" in doc["reasons"][0]["detail"]


def test_gate_missing_skips_tolerance_comparison(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, cand_wer=None, repro_wer=0.18)
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["gate_missing"]


def test_held_out_size_mismatch_on_candidate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.docs["cand"]["evaluations"]["asr"]["slices"]["all"]["n_input_items"] = 3
    case.rewrite("cand")
    case.sync_manifest()
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["held_out_size_mismatch"]


def test_insufficient_sample(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path)
    case.docs["cand"]["evaluations"]["asr"]["slices"]["all"]["insufficient_sample"] = True
    case.rewrite("cand")
    case.sync_manifest()
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["insufficient_sample"]


def test_threshold_missed_min_direction(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path)
    case.docs["criteria"]["gates"][0].update(
        {"direction": "min", "threshold": 0.2, "max_regression": None}
    )
    case.repin_criteria()
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["threshold_missed"]
    assert "<" in doc["reasons"][0]["detail"]


def test_min_direction_value_exactly_at_threshold_promotes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, cand_wer=0.18, repro_wer=0.18)
    case.docs["criteria"]["gates"][0].update(
        {"direction": "min", "threshold": 0.18, "max_regression": None}
    )
    case.repin_criteria()
    code, doc, err = check(case, capsys)
    assert code == 0, (err, doc["reasons"])


# Incumbent checks (A8, A9).


def test_incumbent_gate_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path, inc_wer=None)
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["incumbent_gate_missing"]


def test_held_out_size_mismatch_on_incumbent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.docs["inc"]["evaluations"]["asr"]["slices"]["all"]["n_input_items"] = 3
    case.rewrite("inc")
    case.sync_manifest()
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["held_out_size_mismatch"]
    assert "incumbent" in doc["reasons"][0]["detail"]


# Reproduction checks (A10 to A12).


def test_reproduction_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path, reproduction=False)
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["reproduction_missing"]


def test_reproduction_mismatch_on_candidate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.docs["repro"]["candidate"] = "whisper-v3@p7b"
    case.rewrite("repro")
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["reproduction_mismatch"]
    assert "candidate" in doc["reasons"][0]["detail"]


def test_reproduction_mismatch_on_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.docs["repro"]["config"] = {"temperature": 0.5}
    case.docs["repro"]["config_sha256"] = config_sha256({"temperature": 0.5})
    case.rewrite("repro")
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["reproduction_mismatch"]
    assert "config_sha256" in doc["reasons"][0]["detail"]


def test_not_reproducible_value_null(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path, repro_wer=None)
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["not_reproducible"]


def test_not_reproducible_size_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path)
    case.docs["repro"]["evaluations"]["asr"]["slices"]["all"]["n_input_items"] = 3
    case.rewrite("repro")
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["not_reproducible"]


# Measurements (A13 to A16).


def test_measurements_slice_absent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path, measurements=measurements_doc(slice_present=False))
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["measurements_missing"]
    assert "slice absent" in doc["reasons"][0]["detail"]


def test_latency_over_when_null(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path, measurements=measurements_doc(p95=None))
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["latency_over"]
    assert "null" in doc["reasons"][0]["detail"]


def test_latency_over(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path, measurements=measurements_doc(p95=5000.001))
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["latency_over"]


def test_cost_over_when_null(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path, measurements=measurements_doc(mean=None))
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["cost_over"]


def test_cost_over(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path, measurements=measurements_doc(mean=0.002001))
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["cost_over"]


def test_currency_mismatch_skips_cost_over(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, measurements=measurements_doc(currency="EUR", mean=9.0))
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["currency_mismatch"]


def test_provider_mixing_missing_provider(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, measurements=measurements_doc(n_missing=1))
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["provider_mixing"]


# Rollback manifest (A17 to A19).


def test_rollback_manifest_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path, rollback=False)
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["rollback_manifest_missing"]


def test_rollback_manifest_invalid(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path)
    case.docs["rollback"]["version"] = "rollback-manifest/2"
    case.rewrite("rollback")
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["rollback_manifest_invalid"]


def test_rollback_manifest_nonexistent_file_is_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.docs["request"]["rollback_manifest"] = "nope.json"
    case.rewrite("request")
    code, out, err = run_cli(capsys, "check", str(case.path("request")))
    _assert_input_error(code, out, err, "nope.json")


@pytest.mark.parametrize(
    "field_name",
    [
        "promoted_version",
        "promoted_config_sha256",
        "previous_config",
        "candidate_report_sha256",
        "incumbent_report_sha256",
        "criteria_sha256",
    ],
)
def test_rollback_manifest_mismatch_one_reason_per_wrong_field(
    field_name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    if field_name == "previous_config":
        case.docs["rollback"]["previous_config"] = {"temperature": 0.9}
    elif field_name.endswith("sha256"):
        case.docs["rollback"][field_name] = "1" * 64
    else:
        case.docs["rollback"][field_name] = "other@v1"
    case.rewrite("rollback")
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["rollback_manifest_mismatch"]
    assert field_name in doc["reasons"][0]["detail"]


def test_rollback_manifest_previous_must_be_null_without_incumbent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, incumbent=False)
    case.docs["rollback"]["previous_version"] = "whisper-v3@p5"
    case.rewrite("rollback")
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["rollback_manifest_mismatch"]
    assert "previous_version" in doc["reasons"][0]["detail"]


def test_rollback_manifest_not_approved_when_null(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.docs["rollback"]["approval"] = None
    case.rewrite("rollback")
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["rollback_manifest_not_approved"]


# The decision document (3.4).


def test_decision_shape(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path)
    code, doc, err = check(case, capsys)
    assert set(doc) == {
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
    assert doc["version"] == "promotion-decision/1"
    assert len(doc["gates"]) == 1
    gate = doc["gates"][0]
    assert set(gate) == {
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
    assert gate["evaluation"] == "asr"
    assert gate["threshold"] == 0.25
    assert gate["tolerance"] == 0.01
    assert gate["max_regression"] == 0.01
    assert gate["candidate_value"] == 0.18
    assert gate["incumbent_value"] == 0.19
    assert gate["reproduction_values"] == [0.181]
    assert gate["passed"] is True
    assert gate["reasons"] == []


def test_decision_reasons_sorted_and_every_check_collects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, cand_wer=0.26, repro_wer=0.181)
    case.docs["cand"]["evaluations"]["asr"]["slices"]["all"]["insufficient_sample"] = True
    case.rewrite("cand")
    case.sync_manifest()
    code, doc, err = check(case, capsys)
    assert code == 2
    codes = codes_of(doc)
    assert set(codes) == {
        "threshold_missed",
        "regression",
        "insufficient_sample",
        "not_reproducible",
    }
    pairs = [(r["code"], r["detail"]) for r in doc["reasons"]]
    assert pairs == sorted(pairs)
    gate = doc["gates"][0]
    assert gate["reasons"] == sorted(gate["reasons"])
    assert gate["passed"] is False
    assert gate["candidate_value"] == 0.26
    assert gate["reproduction_values"] == [0.181]


def test_two_runs_print_the_same_bytes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = base_case(tmp_path)
    first = run_cli(capsys, "check", str(case.path("request")))
    second = run_cli(capsys, "check", str(case.path("request")))
    assert first[0] == second[0] == 0
    assert first[1] == second[1]


def test_cli_subprocess_is_deterministic(tmp_path: Path) -> None:
    case = base_case(tmp_path)
    args = [sys.executable, "-m", "backend.evaluation.promotion", "check",
            str(case.path("request"))]  # fmt: skip
    runs = [
        subprocess.run(  # noqa: S603 - fixed argv, the package's own CLI
            args, cwd=HOMAYAR_ROOT, capture_output=True, check=True
        )
        for _ in range(2)
    ]
    assert runs[0].stdout == runs[1].stdout


def test_cli_bad_arguments_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        promotion_main([])
    assert exc.value.code == 2
    capsys.readouterr()


# Input errors (exit 1, one stderr line, nothing on stdout).


def _gate(**patch: Any) -> dict[str, Any]:
    gate = {
        "evaluation": "asr",
        "metric": "wer",
        "slice": "all",
        "direction": "max",
        "threshold": 0.25,
        "n_items": 4,
        "max_regression": None,
        "tolerance": None,
    }
    gate.update(patch)
    return gate


@pytest.mark.parametrize(
    ("label", "patch", "fragment"),
    [
        ("unknown-key", {"extra": 1}, "unknown key"),
        ("blank-criteria-id", {"criteria_id": " "}, "criteria_id"),
        ("bad-frozen-at", {"frozen_at": "2026-11-12 10:00:00"}, "frozen_at"),
        ("frozen-at-offset", {"frozen_at": "2026-11-12T10:00:00+00:00"}, "frozen_at"),
        ("frozen-at-millis", {"frozen_at": "2026-11-12T10:00:00.000Z"}, "frozen_at"),
        ("frozen-at-impossible", {"frozen_at": "2026-11-31T00:00:00Z"}, "frozen_at"),
        ("negative-default-tolerance", {"default_tolerance": -0.1}, "default_tolerance"),
        ("bool-default-tolerance", {"default_tolerance": True}, "default_tolerance"),
        ("zero-min-reproductions", {"min_reproductions": 0}, "min_reproductions"),
        ("empty-approver-roles", {"approver_roles": []}, "approver_roles"),
        ("blank-approver-role", {"approver_roles": ["ml-lead", " "]}, "approver_roles"),
        ("empty-gates", {"gates": []}, "gates"),
        ("duplicate-gates", {"gates": [_gate(), _gate()]}, "gates"),
        ("gate-direction", {"gates": [_gate(direction="avg")]}, "direction"),
        ("gate-zero-n-items", {"gates": [_gate(n_items=0)]}, "n_items"),
        ("gate-bool-threshold", {"gates": [_gate(threshold=True)]}, "threshold"),
        ("gate-negative-regression", {"gates": [_gate(max_regression=-1)]}, "max_regression"),
        ("gate-negative-tolerance", {"gates": [_gate(tolerance=-1)]}, "tolerance"),
        ("gate-unknown-key", {"gates": [_gate(extra=1)]}, "unknown key"),
        ("bad-version", {"version": "release-criteria/2"}, "version"),
        (
            "measurements-currency-null-with-cost",
            {
                "measurements": {
                    "required": True,
                    "slice": "all",
                    "max_p95_latency_ms": None,
                    "max_mean_cost_per_request": 0.002,
                    "currency": None,
                    "single_provider": False,
                }
            },
            "currency",
        ),  # fmt: skip
        (
            "measurements-unknown-key",
            {
                "measurements": {
                    "required": True,
                    "slice": "all",
                    "max_p95_latency_ms": None,
                    "max_mean_cost_per_request": None,
                    "currency": None,
                    "single_provider": False,
                    "extra": 1,
                }
            },
            "unknown key",
        ),  # fmt: skip
        (
            "measurements-required-not-bool",
            {
                "measurements": {
                    "required": 1,
                    "slice": "all",
                    "max_p95_latency_ms": None,
                    "max_mean_cost_per_request": None,
                    "currency": None,
                    "single_provider": False,
                }
            },
            "required",
        ),  # fmt: skip
    ],
)
def test_criteria_input_errors(
    label: str,
    patch: dict[str, Any],
    fragment: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = base_case(tmp_path)
    case.docs["criteria"].update(patch)
    case.rewrite("criteria")
    code, out, err = run_cli(capsys, "check", str(case.path("request")))
    _assert_input_error(code, out, err, fragment)


@pytest.mark.parametrize(
    ("label", "patch", "fragment"),
    [
        ("unknown-key", {"extra": 1}, "extra"),
        ("null-criteria-sha", {"criteria_sha256": None}, "criteria_sha256"),
        ("bad-sha", {"criteria_sha256": "XYZ"}, "criteria_sha256"),
        ("uppercase-sha", {"criteria_sha256": "A" * 64}, "criteria_sha256"),
        ("incumbent-not-string", {"incumbent_report": 5}, "incumbent_report"),
        ("blank-candidate-report", {"candidate_report": " "}, "candidate_report"),
        ("reproductions-not-list", {"reproduction_reports": "x"}, "reproduction_reports"),
        ("reproductions-blank-entry", {"reproduction_reports": [" "]}, "reproduction_reports"),
        ("criteria-not-string", {"criteria": 5}, "criteria"),
    ],
)
def test_request_input_errors(
    label: str,
    patch: dict[str, Any],
    fragment: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = base_case(tmp_path)
    case.docs["request"].update(patch)
    case.rewrite("request")
    code, out, err = run_cli(capsys, "check", str(case.path("request")))
    _assert_input_error(code, out, err, fragment)


@pytest.mark.parametrize(
    ("label", "patch"),
    [
        ("unknown-key", {"extra": 1}),
        ("owner-role-blank", {"owner_role": " "}),
        ("verification-steps-empty", {"verification_steps": []}),
        ("verification-steps-blank", {"verification_steps": [" "]}),
        ("triggers-empty", {"triggers": []}),
        (
            "trigger-missing-threshold",
            {
                "triggers": [
                    {"evaluation": "asr", "metric": "wer", "slice": "all", "direction": "max"}
                ]
            },
        ),
        (
            "trigger-unknown-key",
            {
                "triggers": [
                    {
                        "evaluation": "asr",
                        "metric": "wer",
                        "slice": "all",
                        "direction": "max",
                        "threshold": 0.3,
                        "extra": 1,
                    }
                ]
            },
        ),
        ("promoted-version-blank", {"promoted_version": ""}),
        ("sha-short", {"candidate_report_sha256": "zz"}),
        ("sha-uppercase", {"candidate_report_sha256": "A" * 64}),
        ("sha-wrong-type", {"candidate_report_sha256": 5}),
        ("sha-null-not-allowed", {"criteria_sha256": None}),
        ("previous-config-not-object", {"previous_config": []}),
        (
            "approval-bad-at",
            {"approval": {"approved_by": "Sina", "role": "ml-lead", "approved_at": "2026-11-12"}},
        ),
        (
            "approval-blank-by",
            {
                "approval": {
                    "approved_by": " ",
                    "role": "ml-lead",
                    "approved_at": "2026-11-12T09:00:00Z",
                }
            },
        ),
    ],
)
def test_rollback_manifest_invalid_shapes(
    label: str,
    patch: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = base_case(tmp_path)
    case.docs["rollback"].update(patch)
    case.rewrite("rollback")
    code, doc, err = check(case, capsys)
    assert code == 2, (label, err)
    assert codes_of(doc) == ["rollback_manifest_invalid"], label


def test_rollback_manifest_not_valid_json_is_invalid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.path("rollback").write_text("{", encoding="utf-8")
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["rollback_manifest_invalid"]


def test_rollback_manifest_invalid_skips_mismatch_and_approval(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.docs["rollback"]["promoted_version"] = "other@v1"
    case.docs["rollback"]["approval"] = None
    case.path("rollback").write_text("{", encoding="utf-8")
    code, doc, err = check(case, capsys)
    assert code == 2
    assert codes_of(doc) == ["rollback_manifest_invalid"]


@pytest.mark.parametrize(
    ("label", "mutate", "fragment"),
    [
        ("missing-config-sha", lambda d: d.pop("config_sha256"), "config_sha256"),
        ("missing-version", lambda d: d.pop("version"), "version"),
        ("missing-evaluation-version", lambda d: d.pop("evaluation_version"), "evaluation_version"),
        ("missing-candidate", lambda d: d.pop("candidate"), "candidate"),
        ("missing-evaluations", lambda d: d.pop("evaluations"), "evaluations"),
        ("missing-measurements", lambda d: d.pop("measurements"), "measurements"),
        ("evaluations-not-object", lambda d: d.update(evaluations=[]), "evaluations"),
        ("candidate-not-string", lambda d: d.update(candidate=5), "candidate"),
        ("config-sha-not-string", lambda d: d.update(config_sha256=5), "config_sha256"),
    ],
)
def test_report_input_errors(
    label: str,
    mutate: Any,
    fragment: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = base_case(tmp_path)
    mutate(case.docs["cand"])
    case.rewrite("cand")
    code, out, err = run_cli(capsys, "check", str(case.path("request")))
    _assert_input_error(code, out, err, fragment)


def test_missing_report_file_is_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.path("cand").unlink()
    code, out, err = run_cli(capsys, "check", str(case.path("request")))
    _assert_input_error(code, out, err, "cand.json")


def test_broken_report_json_is_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.path("cand").write_text("{", encoding="utf-8")
    code, out, err = run_cli(capsys, "check", str(case.path("request")))
    _assert_input_error(code, out, err, "")


def test_missing_criteria_file_is_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    case.path("criteria").unlink()
    code, out, err = run_cli(capsys, "check", str(case.path("request")))
    _assert_input_error(code, out, err, "criteria.json")


def test_missing_request_file_is_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run_cli(capsys, "check", str(tmp_path / "nope.json"))
    _assert_input_error(code, out, err, "nope.json")


def test_incumbent_report_may_lack_measurements(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    assert "measurements" not in case.docs["inc"]
    code, doc, err = check(case, capsys)
    assert code == 0, (err, doc["reasons"])


# The ledger (3.5): record (B), rollback (C), active (D).


def record(
    case: Case, capsys: pytest.CaptureFixture[str], ledger: Path | None = None
) -> tuple[int, dict[str, Any], str]:
    path = str(ledger or case.root / "ledger.jsonl")
    code = promotion_main(["record", str(case.path("request")), path])
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out else {}), err


def ledger_lines(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def run_active(ledger: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any], str]:
    code = promotion_main(["active", str(ledger)])
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out else {}), err


def run_rollback(
    ledger: Path,
    capsys: pytest.CaptureFixture[str],
    reason: str = "regression",
    by: str = "ml-lead",
) -> tuple[int, dict[str, Any], str]:
    code = promotion_main(["rollback", str(ledger), "--reason", reason, "--by", by])
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out else {}), err


def test_record_blocked_leaves_the_ledger_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, cand_wer=0.26, repro_wer=0.26)
    ledger = case.root / "ledger.jsonl"
    code, doc, err = record(case, capsys, ledger)
    assert code == 2
    assert doc["decision"] == "blocked"
    assert "threshold_missed" in codes_of(doc)
    assert not ledger.exists()


def test_record_appends_a_promotion_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    ledger = case.root / "ledger.jsonl"
    code, entry, err = record(case, capsys, ledger)
    assert code == 0, err
    assert entry["type"] == "promotion"
    assert entry["sequence"] == 1
    assert entry["version"] == "whisper-v3@p7"
    assert entry["config_sha256"] == config_sha256({"temperature": 0.0})
    assert entry["criteria_sha256"] == sha_of(case.path("criteria"))
    assert entry["candidate_report_sha256"] == sha_of(case.path("cand"))
    assert entry["rollback_manifest"] == case.docs["rollback"]
    assert entry["at"].endswith("Z") and len(entry["at"]) == 20
    code, decision, _ = check(case, capsys)
    assert code == 0
    text = json.dumps(decision, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False)
    assert entry["decision_sha256"] == sha_text(text)
    lines = ledger_lines(ledger)
    assert len(lines) == 1
    assert lines[0] == entry


def test_record_on_an_empty_ledger_appends_sequence_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    ledger = case.root / "ledger.jsonl"
    ledger.write_text("\n", encoding="utf-8")
    code, entry, err = record(case, capsys, ledger)
    assert code == 0, err
    assert entry["sequence"] == 1
    assert len(ledger_lines(ledger)) == 1


def test_record_with_stale_incumbent_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    ledger = case.root / "ledger.jsonl"
    code, entry, err = record(case, capsys, ledger)
    assert code == 0, err
    before = ledger.read_bytes()
    code, entry, err = record(case, capsys, ledger)
    assert code == 2
    assert entry == {}
    assert err.startswith("error: ") and len(err.strip().splitlines()) == 1
    assert ledger.read_bytes() == before


def test_record_without_incumbent_but_active_version_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = base_case(tmp_path, incumbent=False)
    ledger = first.root / "ledger.jsonl"
    code, entry, err = record(first, capsys, ledger)
    assert code == 0, err
    second = base_case(tmp_path / "again", incumbent=False)
    code, entry, err = record(second, capsys, ledger)
    assert code == 2
    assert entry == {}
    assert err.startswith("error: ")
    assert len(ledger_lines(ledger)) == 1


def _chain_case(root: Path, candidate: str, inc_doc: dict[str, Any] | None) -> Case:
    root.mkdir(exist_ok=True)
    case = Case(root)
    case.docs["cand"] = report_doc(candidate, {"temperature": 0.0}, 0.18)
    case.docs["criteria"] = criteria_doc()
    write_json(case.path("criteria"), case.docs["criteria"])
    write_json(case.path("cand"), case.docs["cand"])
    cand_sha = sha_of(case.path("cand"))
    inc_sha = None
    if inc_doc is not None:
        case.docs["inc"] = inc_doc
        write_json(case.path("inc"), inc_doc)
        inc_sha = sha_of(case.path("inc"))
    case.docs["repro"] = report_doc(candidate, {"temperature": 0.0}, 0.181,
                                    with_measurements=False)  # fmt: skip
    write_json(case.path("repro"), case.docs["repro"])
    criteria_sha = sha_of(case.path("criteria"))
    case.docs["rollback"] = rollback_doc(
        case.docs["cand"], inc_doc, cand_sha, inc_sha, criteria_sha
    )
    write_json(case.path("rollback"), case.docs["rollback"])
    case.docs["request"] = request_doc(criteria_sha, incumbent=inc_doc is not None)
    write_json(case.path("request"), case.docs["request"])
    return case


def test_two_promotions_and_two_rollbacks_c3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "chain-ledger.jsonl"
    va = report_doc("vA", {"temperature": 0.0}, 0.18, with_measurements=False)
    vb = report_doc("vB", {"temperature": 0.0}, 0.19, with_measurements=False)
    p1 = _chain_case(tmp_path / "p1", "vB", va)
    code, entry, err = record(p1, capsys, ledger)
    assert code == 0, err
    assert entry["sequence"] == 1
    code, doc, err = run_active(ledger, capsys)
    assert code == 0
    assert doc == {"version": "vB", "config_sha256": config_sha256({"temperature": 0.0}),
                   "sequence": 1}  # fmt: skip
    p2 = _chain_case(tmp_path / "p2", "vC", vb)
    code, entry, err = record(p2, capsys, ledger)
    assert code == 0, err
    assert entry["sequence"] == 2
    code, doc, err = run_active(ledger, capsys)
    assert doc["version"] == "vC" and doc["sequence"] == 2
    code, rb1, err = run_rollback(ledger, capsys)
    assert code == 0, err
    assert rb1["type"] == "rollback"
    assert rb1["sequence"] == 3
    assert rb1["undoes_sequence"] == 2
    assert rb1["from_version"] == "vC"
    assert rb1["to_version"] == "vB"
    assert rb1["to_config_sha256"] == config_sha256({"temperature": 0.0})
    assert rb1["to_config"] == {"temperature": 0.0}
    code, doc, err = run_active(ledger, capsys)
    assert doc["version"] == "vB" and doc["sequence"] == 3
    code, rb2, err = run_rollback(ledger, capsys)
    assert code == 0, err
    assert rb2["undoes_sequence"] == 1
    assert rb2["from_version"] == "vB"
    assert rb2["to_version"] == "vA"
    code, doc, err = run_active(ledger, capsys)
    assert doc == {"version": "vA", "config_sha256": config_sha256({"temperature": 0.0}),
                   "sequence": 4}  # fmt: skip
    assert len(ledger_lines(ledger)) == 4


def test_rollback_after_a_promotion_with_a_previous_version_works(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    ledger = case.root / "ledger.jsonl"
    code, entry, err = record(case, capsys, ledger)
    assert code == 0, err
    code, doc, err = run_active(ledger, capsys)
    assert doc["version"] == "whisper-v3@p7"
    code, rb, err = run_rollback(ledger, capsys)
    assert code == 0, err
    assert rb["to_version"] == "whisper-v3@p6"
    assert rb["to_config_sha256"] == config_sha256({"temperature": 0.2})
    assert rb["to_config"] == {"temperature": 0.2}
    code, doc, err = run_active(ledger, capsys)
    assert doc == {"version": "whisper-v3@p6",
                   "config_sha256": config_sha256({"temperature": 0.2}), "sequence": 2}  # fmt: skip


def test_append_onto_a_ledger_without_a_final_newline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    ledger = case.root / "ledger.jsonl"
    code, entry, err = record(case, capsys, ledger)
    assert code == 0, err
    raw = ledger.read_bytes().removesuffix(b"\n")
    ledger.write_bytes(raw)
    code, rb, err = run_rollback(ledger, capsys)
    assert code == 0, err
    assert rb["sequence"] == 2
    assert rb["to_version"] == "whisper-v3@p6"
    code, doc, err = run_active(ledger, capsys)
    assert code == 0, err
    assert doc == {"version": "whisper-v3@p6",
                   "config_sha256": config_sha256({"temperature": 0.2}), "sequence": 2}  # fmt: skip
    assert len(ledger_lines(ledger)) == 2


def test_rollback_refused_when_previous_is_null(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path, incumbent=False)
    ledger = case.root / "ledger.jsonl"
    code, entry, err = record(case, capsys, ledger)
    assert code == 0, err
    before = ledger.read_bytes()
    code, doc, err = run_rollback(ledger, capsys)
    assert code == 2
    assert doc == {}
    assert err.startswith("error: ") and len(err.strip().splitlines()) == 1
    assert ledger.read_bytes() == before


@pytest.mark.parametrize("exists", [False, True], ids=["absent", "empty-file"])
def test_rollback_refused_on_an_empty_stack(
    exists: bool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    if exists:
        ledger.write_text("", encoding="utf-8")
    code, doc, err = run_rollback(ledger, capsys)
    assert code == 2
    assert doc == {}
    assert err.startswith("error: ") and len(err.strip().splitlines()) == 1
    if exists:
        assert ledger.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("blank", ["", "  "], ids=["empty", "spaces"])
def test_rollback_blank_reason_is_an_input_error(
    blank: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    code, out, err = run_cli(capsys, "rollback", str(ledger), "--reason", blank, "--by", "x")
    _assert_input_error(code, out, err, "reason")
    assert not ledger.exists()


def test_rollback_blank_by_is_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    code, out, err = run_cli(capsys, "rollback", str(ledger), "--reason", "r", "--by", " ")
    _assert_input_error(code, out, err, "by")
    assert not ledger.exists()


def test_active_empty_or_absent_ledger(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, doc, err = run_active(tmp_path / "absent.jsonl", capsys)
    assert code == 0, err
    assert doc == {"version": None, "config_sha256": None, "sequence": None}
    ledger = tmp_path / "empty.jsonl"
    ledger.write_text("\n", encoding="utf-8")
    code, doc, err = run_active(ledger, capsys)
    assert code == 0, err
    assert doc == {"version": None, "config_sha256": None, "sequence": None}


def _rollback_line(sequence: int, **over: Any) -> str:
    entry: dict[str, Any] = {
        "type": "rollback",
        "sequence": sequence,
        "at": "2026-11-12T10:00:00Z",
        "undoes_sequence": 1,
        "from_version": "whisper-v3@p7",
        "to_version": "whisper-v3@p6",
        "to_config_sha256": None,
        "to_config": None,
        "reason": "regression",
        "by": "ml-lead",
    }
    entry.update(over)
    return json.dumps(entry)


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("bad-json", '{"type": "promotion"\n'),
        ("unknown-type", '{"type": "undo", "sequence": 1}\n'),
        (
            "broken-sequence",
            json.dumps({"type": "promotion", "sequence": 2}),
        ),
        ("missing-key", json.dumps({"type": "promotion", "sequence": 1})),
        ("rollback-with-no-promotion", _rollback_line(1)),
    ],
)
def test_malformed_ledger_is_an_input_error(
    label: str, text: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(text + "\n", encoding="utf-8")
    code, out, err = run_cli(capsys, "active", str(ledger))
    _assert_input_error(code, out, err, "")


def test_rollback_entry_undoes_the_wrong_promotion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    ledger = case.root / "ledger.jsonl"
    code, entry, err = record(case, capsys, ledger)
    assert code == 0, err
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(_rollback_line(2, undoes_sequence=2) + "\n")
    code, out, err = run_cli(capsys, "active", str(ledger))
    _assert_input_error(code, out, err, "undoes_sequence")


def test_rollback_entry_from_version_does_not_match(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = base_case(tmp_path)
    ledger = case.root / "ledger.jsonl"
    code, entry, err = record(case, capsys, ledger)
    assert code == 0, err
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(_rollback_line(2, from_version="other@v1") + "\n")
    code, out, err = run_cli(capsys, "active", str(ledger))
    _assert_input_error(code, out, err, "from_version")
