"""JSONL loaders and the ``python -m backend.evaluation`` CLI (HOM-69)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.evaluation import (
    KINDS,
    AuditedClaim,
    DiarizationItem,
    EntityItem,
    LabeledItem,
    RatingPair,
    RetrievalQuery,
    ScorePair,
    SpeakerTurn,
    SupportLabel,
    Utterance,
    load_jsonl,
    parse_record,
)
from backend.evaluation.__main__ import main

FIXTURES = Path(__file__).parent / "fixtures" / "evaluation"
HOMAYAR_ROOT = Path(__file__).resolve().parents[2]

ITEM_TYPES = {
    "asr": Utterance,
    "diarization": DiarizationItem,
    "intent": LabeledItem,
    "entities": EntityItem,
    "qa_agreement": RatingPair,
    "qa_score": ScorePair,
    "factual_support": AuditedClaim,
    "retrieval": RetrievalQuery,
}

MINIMAL = {
    "asr": {"reference": "a", "hypothesis": "a"},
    "diarization": {"recording_id": "r", "reference": [], "hypothesis": []},
    "intent": {"gold": "a", "predicted": None},
    "entities": {"gold": [], "predicted": []},
    "qa_agreement": {"rating_a": "x", "rating_b": None},
    "qa_score": {"score": 1, "reference": 2.5},
    "factual_support": {"claim_id": "c", "label": "supported"},
    "retrieval": {"query_id": "q", "relevance": {"d": 1}, "ranked": ["d"]},
}


def test_kinds() -> None:
    assert KINDS == ("asr", "diarization", "intent", "entities", "qa_agreement",
                     "qa_score", "factual_support", "retrieval")  # fmt: skip
    assert set(ITEM_TYPES) == set(KINDS) == set(MINIMAL)


@pytest.mark.parametrize("kind", KINDS)
def test_every_fixture_loads(kind: str) -> None:
    items = load_jsonl(kind, FIXTURES / f"{kind}.jsonl")
    assert items
    assert all(isinstance(i, ITEM_TYPES[kind]) for i in items)


@pytest.mark.parametrize("kind", KINDS)
def test_minimal_record_and_extra_key(kind: str) -> None:
    item = parse_record(kind, MINIMAL[kind])
    assert isinstance(item, ITEM_TYPES[kind])
    assert item.slices == {}
    with_extra = parse_record(kind, {**MINIMAL[kind], "future_field": [1, 2]})
    assert with_extra == item


@pytest.mark.parametrize("kind", KINDS)
def test_missing_required_key_is_refused(kind: str) -> None:
    for key in MINIMAL[kind]:
        record = {k: v for k, v in MINIMAL[kind].items() if k != key}
        with pytest.raises(ValueError, match=key):
            parse_record(kind, record)


def test_unknown_kind_is_refused() -> None:
    with pytest.raises(ValueError):
        parse_record("sentiment", {})
    with pytest.raises(ValueError):
        load_jsonl("sentiment", FIXTURES / "asr.jsonl")


def test_parsed_values() -> None:
    asr = parse_record("asr", {"reference": "a", "hypothesis": "b", "slices": {"d": "x"}})
    assert asr == Utterance("a", "b", "fa", {"d": "x"})
    dia = parse_record("diarization", {
        "recording_id": "r",
        "reference": [{"speaker": "A", "start_ms": 0, "end_ms": 5}],
        "hypothesis": [],
        "scored_region": [0, 5],
    })  # fmt: skip
    assert isinstance(dia, DiarizationItem)
    assert list(dia.reference) == [SpeakerTurn("A", 0, 5)]
    assert dia.scored_region == (0, 5)
    claim = parse_record("factual_support", {
        "claim_id": "c", "label": "contradicted", "severe": True,
        "citations": [{"start_ms": 0, "end_ms": 5, "revision_id": "r"}],
        "recording_duration_ms": 10,
    })  # fmt: skip
    assert isinstance(claim, AuditedClaim)
    assert claim.label is SupportLabel.CONTRADICTED
    assert claim.severe is True
    assert claim.citations[0].revision_id == "r"
    ent = parse_record("entities", {"gold": [{"type": "PER", "value": "Ali"}], "predicted": []})
    assert isinstance(ent, EntityItem)
    assert ent.gold[0].start is None


@pytest.mark.parametrize(
    ("kind", "record"),
    [
        ("asr", {"reference": 1, "hypothesis": "a"}),
        ("asr", {"reference": "a", "hypothesis": "a", "slices": {"d": 1}}),
        ("asr", {"reference": "a", "hypothesis": "a", "slices": ["d"]}),
        (
            "diarization",
            {
                "recording_id": "r",
                "reference": [{"speaker": "A", "start_ms": "0", "end_ms": 5}],
                "hypothesis": [],
            },
        ),
        (
            "diarization",
            {"recording_id": "r", "reference": [], "hypothesis": [], "scored_region": [0]},
        ),
        ("intent", {"gold": 3, "predicted": "a"}),
        ("qa_score", {"score": "3", "reference": 2}),
        ("qa_score", {"score": True, "reference": 2}),
        ("factual_support", {"claim_id": "c", "label": "maybe"}),
        ("factual_support", {"claim_id": "c", "label": "supported", "severe": "yes"}),
        ("retrieval", {"query_id": "q", "relevance": {"d": 1.5}, "ranked": []}),
        ("retrieval", {"query_id": "q", "relevance": {"d": 1}, "ranked": "d"}),
    ],
)
def test_wrong_type_is_refused(kind: str, record: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        parse_record(kind, record)


def test_load_jsonl_reports_line_numbers(tmp_path: Path) -> None:
    good = json.dumps(MINIMAL["intent"])
    path = tmp_path / "bad.jsonl"
    path.write_text(f"{good}\n\n{{not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 3"):
        load_jsonl("intent", path)
    path.write_text(f"{good}\n{json.dumps({'gold': 'a'})}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        load_jsonl("intent", path)
    path.write_text(f"{good}\n[1, 2]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        load_jsonl("intent", path)


def test_load_jsonl_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "ok.jsonl"
    line = json.dumps(MINIMAL["intent"])
    path.write_text(f"\n{line}\n   \n{line}\n", encoding="utf-8")
    assert len(load_jsonl("intent", path)) == 2


# CLI


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.mark.parametrize("kind", KINDS)
def test_cli_prints_the_documented_json(kind: str, capsys: pytest.CaptureFixture[str]) -> None:
    path = FIXTURES / f"{kind}.jsonl"
    code, out, err = run_cli(capsys, kind, str(path))
    assert code == 0, err
    doc = json.loads(out)
    assert set(doc) == {"version", "kind", "input_sha256", "options", "slices"}
    assert doc["version"] == "evaluation/1"
    assert doc["kind"] == kind
    assert doc["input_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "all" in doc["slices"]
    # Byte-identical on a second run.
    assert run_cli(capsys, kind, str(path))[1] == out


def test_cli_report_shapes(capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = run_cli(capsys, "qa_agreement", str(FIXTURES / "qa_agreement.jsonl"))
    allq = json.loads(out)["slices"]["all"]
    assert set(allq) == {"kappa", "by_criterion"}
    assert list(allq["by_criterion"]) == ["accuracy", "clarity"]
    assert allq["kappa"]["n_excluded"] == 1
    _, out, _ = run_cli(capsys, "qa_score", str(FIXTURES / "qa_score.jsonl"))
    allm = json.loads(out)["slices"]["all"]
    assert allm["score_mae"]["value"] == pytest.approx(3.5 / 3)
    _, out, _ = run_cli(capsys, "asr", str(FIXTURES / "asr.jsonl"))
    asr = json.loads(out)["slices"]["all"]
    assert asr["metrics"]["wer"]["n_excluded"] == 1


def test_cli_options(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli(
        capsys,
        "diarization",
        str(FIXTURES / "diarization.jsonl"),
        "--collar-ms",
        "250",
        "--skip-overlap",
        "--slices",
        "channel",
    )
    assert code == 0
    doc = json.loads(out)
    assert doc["options"] == {"collar_ms": 250, "skip_overlap": True, "slices": ["channel"]}
    assert set(doc["slices"]) == {"all", "channel=tel_8k", "channel=web"}

    code, out, _ = run_cli(
        capsys, "intent", str(FIXTURES / "intent.jsonl"), "--labels", "book_meeting,cancel_meeting"
    )
    doc = json.loads(out)
    assert doc["slices"]["all"]["n_out_of_taxonomy"] == 1

    code, out, _ = run_cli(
        capsys, "entities", str(FIXTURES / "entities.jsonl"), "--normalize", "fa"
    )
    ents = json.loads(out)["slices"]["all"]["per_type"]
    # Persian digits and case fold through normalize_for_matching.
    assert ents["DATE"]["tp"] == 1
    assert ents["LOC"]["tp"] == 1

    code, out, _ = run_cli(capsys, "entities", str(FIXTURES / "entities.jsonl"), "--match", "span")
    assert json.loads(out)["slices"]["all"]["per_type"]["LOC"]["tp"] == 1

    code, out, _ = run_cli(
        capsys,
        "qa_agreement",
        str(FIXTURES / "qa_agreement.jsonl"),
        "--categories",
        "low,mid,high",
        "--weights",
        "quadratic",
    )
    assert json.loads(out)["options"]["weights"] == "quadratic"

    code, out, _ = run_cli(capsys, "retrieval", str(FIXTURES / "retrieval.jsonl"), "--ks", "1,3")
    metrics = json.loads(out)["slices"]["all"]["metrics"]
    assert "ndcg@3" in metrics
    assert "ndcg@10" not in metrics


def test_cli_bad_data_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"gold": "a", "predicted": "a"}\n{"gold": "a"}\n', encoding="utf-8")
    code, out, err = run_cli(capsys, "intent", str(path))
    assert code == 1
    assert out == ""
    assert "line 2" in err
    assert len(err.strip().splitlines()) == 1


def test_cli_evaluator_error_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    # The fixture has the rating "high", which is not in these categories.
    code, _, err = run_cli(
        capsys, "qa_agreement", str(FIXTURES / "qa_agreement.jsonl"), "--categories", "low,mid"
    )
    assert code == 1
    assert err.strip()


@pytest.mark.parametrize(
    "argv",
    [
        ["sentiment", "x.jsonl"],
        ["asr"],
        ["retrieval", "x.jsonl", "--ks", "a"],
        # Turn 2, finding 3.
        ["retrieval", "x.jsonl", "--ks", "0"],
        ["retrieval", "x.jsonl", "--ks", "1,-5"],
        ["retrieval", "x.jsonl", "--ks", ""],
        ["retrieval", "x.jsonl", "--ks", ","],
        ["qa_agreement", "x.jsonl", "--weights", "linear"],
        ["qa_agreement", "x.jsonl", "--weights", "quadratic"],
    ],
)
def test_cli_bad_arguments_exit_2(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    capsys.readouterr()


def test_cli_good_arguments_close_to_the_bad_ones(capsys: pytest.CaptureFixture[str]) -> None:
    path = str(FIXTURES / "retrieval.jsonl")
    code, out, _ = run_cli(capsys, "retrieval", path, "--ks", "1,1,3")
    assert code == 0
    assert json.loads(out)["options"]["ks"] == [1, 3]
    path = str(FIXTURES / "qa_agreement.jsonl")
    code, _, _ = run_cli(capsys, "qa_agreement", path, "--weights", "linear",
                         "--categories", "low,mid,high")  # fmt: skip
    assert code == 0


# Turn 2, findings 2, 5 and 7: range errors, non-finite numbers and bad UTF-8 carry the
# line number.

TURN = {"speaker": "A", "start_ms": 0, "end_ms": 10}


@pytest.mark.parametrize(
    ("kind", "record"),
    [
        ("diarization", {"recording_id": "r", "reference": [{**TURN, "end_ms": 0}],
                         "hypothesis": []}),
        ("diarization", {"recording_id": "r", "reference": [],
                         "hypothesis": [{**TURN, "start_ms": -1}]}),
        ("diarization", {"recording_id": "r", "reference": [], "hypothesis": [],
                         "scored_region": [10, 5]}),
        ("diarization", {"recording_id": "r", "reference": [], "hypothesis": [],
                         "scored_region": [-1, 5]}),
        ("retrieval", {"query_id": "q", "relevance": {"d": -1}, "ranked": []}),
        ("entities", {"gold": [{"type": "PER", "value": "Ali", "start": "0"}],
                      "predicted": []}),
        ("entities", {"gold": [], "predicted": [{"type": "PER", "value": "Ali",
                                                 "end": 1.5}]}),
        ("qa_score", {"score": float("nan"), "reference": 1}),
        ("qa_score", {"score": 1, "reference": float("inf")}),
        ("asr", {"reference": "a", "hypothesis": "a", "extra": [float("-inf")]}),
    ],
)  # fmt: skip
def test_range_and_non_finite_errors_are_refused(kind: str, record: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        parse_record(kind, record)


def test_range_allow_controls() -> None:
    dia = {"recording_id": "r", "reference": [{**TURN, "end_ms": 1}], "hypothesis": [],
           "scored_region": [0, 1]}  # fmt: skip
    assert isinstance(parse_record("diarization", dia), DiarizationItem)
    ret = {"query_id": "q", "relevance": {"d": 0}, "ranked": []}
    assert isinstance(parse_record("retrieval", ret), RetrievalQuery)
    ent = {"gold": [{"type": "PER", "value": "Ali", "start": 0, "end": None}], "predicted": []}
    assert isinstance(parse_record("entities", ent), EntityItem)


@pytest.mark.parametrize(
    ("kind", "line"),
    [
        ("diarization", '{"recording_id": "r2", "reference": [{"speaker": "A", '
                        '"start_ms": 10, "end_ms": 10}], "hypothesis": []}'),
        ("diarization", '{"recording_id": "r2", "reference": [], "hypothesis": [], '
                        '"scored_region": [5, 1]}'),
        ("retrieval", '{"query_id": "q", "relevance": {"d": -2}, "ranked": ["d"]}'),
        ("qa_score", '{"score": NaN, "reference": 1}'),
        ("qa_score", '{"score": 1, "reference": Infinity}'),
        ("qa_score", '{"score": 1, "reference": 1e999}'),
        ("asr", '{"reference": "a", "hypothesis": "a", "x": -Infinity}'),
    ],
)  # fmt: skip
def test_load_jsonl_and_cli_give_the_line_number(
    kind: str, line: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(f"{json.dumps(MINIMAL[kind])}\n{line}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        load_jsonl(kind, path)
    code, out, err = run_cli(capsys, kind, str(path))
    assert code == 1
    assert out == ""
    assert "line 2" in err
    assert len(err.strip().splitlines()) == 1


def test_invalid_utf8_gives_the_line_number(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "bad.jsonl"
    # Line 2 holds the byte 0xff, which is never valid UTF-8.
    path.write_bytes(json.dumps(MINIMAL["asr"]).encode() + b'\n{"reference": "\xff"}\n')
    with pytest.raises(ValueError, match="line 2"):
        load_jsonl("asr", path)
    code, _, err = run_cli(capsys, "asr", str(path))
    assert code == 1
    assert "line 2" in err


def test_module_entry_point() -> None:
    path = FIXTURES / "asr.jsonl"
    args = [sys.executable, "-m", "backend.evaluation", "asr", str(path)]
    first = subprocess.run(args, cwd=HOMAYAR_ROOT, capture_output=True, check=True)  # noqa: S603
    second = subprocess.run(args, cwd=HOMAYAR_ROOT, capture_output=True, check=True)  # noqa: S603
    assert first.stdout == second.stdout
    assert json.loads(first.stdout)["kind"] == "asr"


def test_cli_overflowing_mae_prints_valid_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Turn 3 (SPEC 14): this file once crashed the CLI with a traceback.
    path = tmp_path / "big.jsonl"
    path.write_text('{"score": 1e308, "reference": -0.5}\n' * 2, encoding="utf-8")
    code, out, err = run_cli(capsys, "qa_score", str(path))
    assert code == 0, err
    mae = json.loads(out)["slices"]["all"]["score_mae"]
    assert mae["value"] is None
    assert mae["reason"] == "non_finite_result"


def test_cli_output_error_is_one_line_and_exit_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import backend.evaluation.__main__ as cli
    from backend.evaluation import MetricValue

    def nan_mae(pairs: object) -> MetricValue:
        return MetricValue("score_mae", float("nan"), 1, 1, 1)

    monkeypatch.setattr(cli, "score_mae", nan_mae)
    code, out, err = run_cli(capsys, "qa_score", str(FIXTURES / "qa_score.jsonl"))
    assert code == 1
    assert out == ""
    assert len(err.strip().splitlines()) == 1


def test_cli_huge_relevance_prints_valid_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Turn 4: this file once crashed the CLI with OverflowError.
    path = tmp_path / "huge.jsonl"
    path.write_text('{"query_id": "q", "relevance": {"a": 1100}, "ranked": ["a"]}\n')
    code, out, err = run_cli(capsys, "retrieval", str(path), "--ks", "1")
    assert code == 0, err
    metrics = json.loads(out)["slices"]["all"]["metrics"]
    assert metrics["ndcg@1"]["reason"] == "non_finite_result"
    assert metrics["mrr"]["value"] == 1.0


def test_cli_arithmetic_error_is_one_line_and_exit_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import backend.evaluation.__main__ as cli

    def broken(queries: object, ks: object) -> object:
        raise OverflowError("int too large to convert to float")

    monkeypatch.setattr(cli, "evaluate_retrieval", broken)
    code, out, err = run_cli(capsys, "retrieval", str(FIXTURES / "retrieval.jsonl"))
    assert code == 1
    assert out == ""
    assert len(err.strip().splitlines()) == 1
