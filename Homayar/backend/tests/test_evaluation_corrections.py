"""Production corrections: the separate store, the CLI and the loader separation (HOM-71)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from backend.evaluation.corrections import main as corrections_main
from backend.evaluation.loaders import KINDS, parse_record

FIXTURES = Path(__file__).parent / "fixtures" / "evaluation"


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = corrections_main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def correction_doc(**over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "correction_id": "c-0001",
        "origin": "production_correction",
        "kind": "asr",
        "model_version": "whisper-large-v3@prompt-7",
        "prompt_version": None,
        "workspace_ref": "ws-opaque-ref",
        "subject_ref": "transcript_revision:abc",
        "corrected_at": "2026-11-12T10:00:00Z",
        "corrected_by_role": "supervisor",
        "before": {"text": "a"},
        "after": {"text": "b"},
    }
    doc.update(over)
    return doc


def write_jsonl(path: Path, docs: list[dict[str, Any]]) -> Path:
    text = "".join(json.dumps(d, sort_keys=True, ensure_ascii=False) + "\n" for d in docs)
    path.write_text(text, encoding="utf-8")
    return path


def lines_of(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def assert_input_error(code: int, out: str, err: str, fragment: str) -> None:
    assert code == 1
    assert out == ""
    assert err.startswith("error: "), err
    assert len(err.strip().splitlines()) == 1, err
    assert fragment in err


# The record format (3.6).


def test_parse_correction_accepts_the_full_shape(tmp_path: Path) -> None:
    from backend.evaluation.corrections import parse_correction

    doc = correction_doc(prompt_version="prompt-7", before={"nested": {"x": 1}}, after={})
    correction = parse_correction(doc)
    assert correction.correction_id == "c-0001"
    assert correction.kind == "asr"
    assert correction.prompt_version == "prompt-7"
    assert correction.before == {"nested": {"x": 1}}


@pytest.mark.parametrize(
    ("label", "patch", "fragment"),
    [
        ("unknown-key", {"extra": 1}, "extra"),
        ("missing-origin", {"origin": None}, "origin"),
        ("wrong-origin", {"origin": "gold"}, "origin"),
        ("origin-case", {"origin": "Production_Correction"}, "origin"),
        ("kind-unknown", {"kind": "transcription"}, "kind"),
        ("blank-correction-id", {"correction_id": " "}, "correction_id"),
        ("blank-model-version", {"model_version": ""}, "model_version"),
        ("blank-workspace-ref", {"workspace_ref": " "}, "workspace_ref"),
        ("blank-subject-ref", {"subject_ref": ""}, "subject_ref"),
        ("blank-role", {"corrected_by_role": " "}, "corrected_by_role"),
        ("blank-prompt-version", {"prompt_version": " "}, "prompt_version"),
        ("prompt-version-not-string", {"prompt_version": 7}, "prompt_version"),
        ("bad-corrected-at", {"corrected_at": "2026-11-12T10:00:00+00:00"}, "corrected_at"),
        ("corrected-at-date-only", {"corrected_at": "2026-11-12"}, "corrected_at"),
        ("before-not-object", {"before": [1]}, "before"),
        ("after-not-object", {"after": "b"}, "after"),
        ("model-version-not-string", {"model_version": 7}, "model_version"),
    ],
)
def test_parse_correction_refuses(label: str, patch: dict[str, Any], fragment: str) -> None:
    from backend.evaluation.corrections import parse_correction

    with pytest.raises(ValueError, match=fragment):
        parse_correction(correction_doc(**patch))


def test_parse_correction_refuses_non_finite() -> None:
    from backend.evaluation.corrections import parse_correction

    doc = correction_doc(before={"score": float("nan")})
    with pytest.raises(ValueError, match="non-finite"):
        parse_correction(doc)


# append (E1) and the corpus-root refusal (E2).


def test_append_creates_the_store_in_input_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "store.jsonl"
    second = correction_doc(correction_id="c-0002", kind="intent")
    inp = write_jsonl(tmp_path / "in.jsonl", [correction_doc(), second])
    code, out, err = run_cli(capsys, "append", str(store), str(inp))
    assert code == 0, err
    assert json.loads(out) == {"appended": 2, "total": 2}
    lines = lines_of(store)
    assert [line["correction_id"] for line in lines] == ["c-0001", "c-0002"]
    again = write_jsonl(tmp_path / "in2.jsonl", [correction_doc(correction_id="c-0003")])
    code, out, err = run_cli(capsys, "append", str(store), str(again))
    assert code == 0, err
    assert json.loads(out) == {"appended": 1, "total": 3}


def test_append_empty_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = tmp_path / "store.jsonl"
    inp = write_jsonl(tmp_path / "in.jsonl", [])
    code, out, err = run_cli(capsys, "append", str(store), str(inp))
    assert code == 0, err
    assert json.loads(out) == {"appended": 0, "total": 0}


def test_append_invalid_record_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "store.jsonl"
    inp = write_jsonl(tmp_path / "in.jsonl", [correction_doc(), correction_doc(kind="nope")])
    code, out, err = run_cli(capsys, "append", str(store), str(inp))
    assert_input_error(code, out, err, "kind")
    assert not store.exists()


def test_append_duplicate_inside_input_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "store.jsonl"
    inp = write_jsonl(tmp_path / "in.jsonl", [correction_doc(), correction_doc(kind="intent")])
    code, out, err = run_cli(capsys, "append", str(store), str(inp))
    assert_input_error(code, out, err, "c-0001")
    assert not store.exists()


def test_append_duplicate_against_store_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "store.jsonl"
    write_jsonl(store, [correction_doc()])
    before = store.read_bytes()
    inp = write_jsonl(tmp_path / "in.jsonl", [correction_doc(correction_id="c-0002"),
                                              correction_doc()])  # fmt: skip
    code, out, err = run_cli(capsys, "append", str(store), str(inp))
    assert_input_error(code, out, err, "c-0001")
    assert store.read_bytes() == before


def test_append_bad_input_json_is_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "store.jsonl"
    inp = tmp_path / "in.jsonl"
    inp.write_text(json.dumps(correction_doc()) + "\nnot json\n", encoding="utf-8")
    code, out, err = run_cli(capsys, "append", str(store), str(inp))
    assert_input_error(code, out, err, "line 2")
    assert not store.exists()


def test_append_missing_input_file_is_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run_cli(capsys, "append", str(tmp_path / "store.jsonl"),
                             str(tmp_path / "nope.jsonl"))  # fmt: skip
    assert_input_error(code, out, err, "nope.jsonl")


def test_append_onto_a_store_without_a_final_newline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "store.jsonl"
    store.write_bytes(
        (json.dumps(correction_doc(), sort_keys=True, ensure_ascii=False)).encode("utf-8")
    )
    inp = write_jsonl(tmp_path / "in.jsonl", [correction_doc(correction_id="c-0002")])
    code, out, err = run_cli(capsys, "append", str(store), str(inp))
    assert code == 0, err
    assert json.loads(out) == {"appended": 1, "total": 2}
    code, out, err = run_cli(capsys, "summary", str(store))
    assert code == 0, err
    assert json.loads(out)["total"] == 2
    assert len(lines_of(store)) == 2


def test_store_inside_the_root_directory_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    store = corpus / "corrections.jsonl"
    inp = write_jsonl(tmp_path / "in.jsonl", [correction_doc()])
    code, out, err = run_cli(capsys, "append", str(store), str(inp),
                             "--corpus-root", str(corpus))  # fmt: skip
    assert code == 2
    assert out == ""
    assert err.startswith("error: ") and len(err.strip().splitlines()) == 1
    assert not store.exists()


def test_store_under_the_filesystem_root_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "corrections.jsonl"
    inp = write_jsonl(tmp_path / "in.jsonl", [correction_doc()])
    code, out, err = run_cli(capsys, "append", str(store), str(inp),
                             "--corpus-root", "/")  # fmt: skip
    assert code == 2
    assert out == ""
    assert err.startswith("error: ") and len(err.strip().splitlines()) == 1
    assert not store.exists()


def _case_insensitive_fs() -> bool:
    with tempfile.TemporaryDirectory() as td:
        probe = Path(td) / "probe-dir"
        probe.mkdir()
        return (Path(td) / "PROBE-DIR").exists()


@pytest.mark.skipif(not _case_insensitive_fs(), reason="the temp file system is case-sensitive")
def test_store_case_variant_of_the_root_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    store = tmp_path / "Corpus" / "corrections.jsonl"
    inp = write_jsonl(tmp_path / "in.jsonl", [correction_doc()])
    code, out, err = run_cli(capsys, "append", str(store), str(inp),
                             "--corpus-root", str(corpus))  # fmt: skip
    assert code == 2
    assert out == ""
    assert err.startswith("error: ") and len(err.strip().splitlines()) == 1
    assert not store.exists()


def test_store_equal_to_a_corpus_root_path_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    inp = write_jsonl(tmp_path / "in.jsonl", [correction_doc()])
    code, out, err = run_cli(capsys, "append", str(corpus), str(inp),
                             "--corpus-root", str(corpus))  # fmt: skip
    assert code == 2
    assert err.startswith("error: ")


def test_store_nested_inside_a_corpus_root_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = tmp_path / "corpus"
    (corpus / "data").mkdir(parents=True)
    store = corpus / "data" / "sub" / "corrections.jsonl"
    inp = write_jsonl(tmp_path / "in.jsonl", [correction_doc()])
    code, out, err = run_cli(capsys, "append", str(store), str(inp),
                             "--corpus-root", str(corpus))  # fmt: skip
    assert code == 2
    assert not store.exists()


def test_store_reached_through_a_symlinked_root_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    real_root = tmp_path / "real-corpus"
    store_dir = real_root / "stores"
    store_dir.mkdir(parents=True)
    store = store_dir / "store.jsonl"
    link = tmp_path / "link-corpus"
    link.symlink_to(real_root)
    inp = write_jsonl(tmp_path / "in.jsonl", [correction_doc()])
    code, out, err = run_cli(capsys, "append", str(store), str(inp),
                             "--corpus-root", str(link))  # fmt: skip
    assert code == 2
    assert not store.exists()


def test_store_in_a_sibling_directory_appends(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    store = tmp_path / "corrections.jsonl"
    inp = write_jsonl(tmp_path / "in.jsonl", [correction_doc()])
    code, out, err = run_cli(capsys, "append", str(store), str(inp),
                             "--corpus-root", str(corpus))  # fmt: skip
    assert code == 0, err
    assert json.loads(out) == {"appended": 1, "total": 1}


def test_multiple_corpus_roots_any_match_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    store = corpus / "corrections.jsonl"
    inp = write_jsonl(tmp_path / "in.jsonl", [correction_doc()])
    code, out, err = run_cli(capsys, "append", str(store), str(inp),
                             "--corpus-root", str(other),
                             "--corpus-root", str(corpus))  # fmt: skip
    assert code == 2
    assert not store.exists()


# summary (E3).


def test_summary_counts_by_kind_and_model_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = write_jsonl(
        tmp_path / "store.jsonl",
        [
            correction_doc(),
            correction_doc(
                correction_id="c-0002", kind="intent", model_version="whisper-large-v3@prompt-8"
            ),  # fmt: skip
            correction_doc(correction_id="c-0003", kind="intent"),
        ],
    )
    code, out, err = run_cli(capsys, "summary", str(store))
    assert code == 0, err
    assert json.loads(out) == {
        "total": 3,
        "by_kind": {"asr": 1, "intent": 2},
        "by_model_version": {
            "whisper-large-v3@prompt-7": 2,
            "whisper-large-v3@prompt-8": 1,
        },
    }


def test_summary_of_an_empty_store(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = tmp_path / "store.jsonl"
    store.write_text("", encoding="utf-8")
    code, out, err = run_cli(capsys, "summary", str(store))
    assert code == 0, err
    assert json.loads(out) == {"total": 0, "by_kind": {}, "by_model_version": {}}


def test_summary_missing_store_is_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run_cli(capsys, "summary", str(tmp_path / "nope.jsonl"))
    assert_input_error(code, out, err, "nope.jsonl")


def test_summary_malformed_store_is_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "store.jsonl"
    store.write_text('{"correction_id": "c-1"}\n', encoding="utf-8")
    code, out, err = run_cli(capsys, "summary", str(store))
    assert_input_error(code, out, err, "line 1")


def test_cli_bad_arguments_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        corrections_main([])
    assert exc.value.code == 2
    capsys.readouterr()


# The separation guard (E4).


@pytest.mark.parametrize("kind", KINDS)
def test_parse_record_refuses_production_corrections(kind: str) -> None:
    with pytest.raises(ValueError, match="production correction"):
        parse_record(kind, correction_doc())


def test_parse_record_checks_the_kind_first() -> None:
    with pytest.raises(ValueError, match="unknown kind"):
        parse_record("bogus", [])
    with pytest.raises(ValueError, match="unknown kind"):
        parse_record("bogus", correction_doc())


def test_parse_record_refuses_before_kind_parsing() -> None:
    record: dict[str, Any] = {"origin": "production_correction"}  # not a valid asr record
    with pytest.raises(ValueError, match="production correction"):
        parse_record("asr", record)


def test_load_jsonl_refuses_a_correction_file(tmp_path: Path) -> None:
    from backend.evaluation.loaders import load_jsonl

    path = tmp_path / "mixed.jsonl"
    path.write_text(
        json.dumps({"reference": "a", "hypothesis": "b"})
        + "\n"
        + json.dumps(correction_doc())
        + "\n",  # fmt: skip
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="line 2"):
        load_jsonl("asr", path)


@pytest.mark.parametrize("origin", [None, "gold"], ids=["no-origin", "origin-gold"])
def test_evaluation_records_with_another_origin_still_load(origin: str | None) -> None:
    record: dict[str, Any] = {"reference": "a", "hypothesis": "b"}
    if origin is not None:
        record["origin"] = origin
    parsed = parse_record("asr", record)
    assert parsed.reference == "a"
    assert parsed.hypothesis == "b"


def test_candidate_report_refuses_a_correction_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from backend.evaluation.candidate import main as candidate_main

    data = tmp_path / "asr.jsonl"
    data.write_text(
        json.dumps({"reference": "a", "hypothesis": "b", "slices": {}})
        + "\n"
        + json.dumps(correction_doc())
        + "\n",  # fmt: skip
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "candidate": "c",
                "evaluations": [{"name": "asr", "kind": "asr", "path": str(data)}],
                "min_slice_size": 1,
            }
        ),
        encoding="utf-8",
    )
    code = candidate_main([str(manifest)])
    out, err = capsys.readouterr()
    assert code == 1
    assert out == ""
    assert len(err.strip().splitlines()) == 1
    assert err.startswith("error: ")
    assert "production correction" in err


def test_candidate_report_still_builds_without_corrections(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from backend.evaluation.candidate import main as candidate_main

    data = tmp_path / "asr.jsonl"
    data.write_text(
        json.dumps({"reference": "a", "hypothesis": "b", "slices": {}}) + "\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "candidate": "c",
                "evaluations": [{"name": "asr", "kind": "asr", "path": str(data)}],
                "min_slice_size": 1,
            }
        ),
        encoding="utf-8",
    )
    code = candidate_main([str(manifest)])
    out, err = capsys.readouterr()
    assert code == 0, err
    assert json.loads(out)["candidate"] == "c"


def test_subprocess_cli_smoke(tmp_path: Path) -> None:
    import subprocess

    store = tmp_path / "store.jsonl"
    inp = write_jsonl(tmp_path / "in.jsonl", [correction_doc()])
    args = [sys.executable, "-m", "backend.evaluation.corrections", "append",
            str(store), str(inp)]  # fmt: skip
    run = subprocess.run(  # noqa: S603 - fixed argv, the package's own CLI
        args, cwd=Path(__file__).resolve().parents[2], capture_output=True, check=True
    )
    assert json.loads(run.stdout) == {"appended": 1, "total": 1}
