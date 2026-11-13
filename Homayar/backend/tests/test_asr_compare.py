"""HOM-39: the base vs stronger ASR comparison tool (the WP13 hook).

``load_corpus`` refuses a bad corpus line with its line number. ``compare``
scores fake models with the existing WER/CER code and never qualifies a
model or a language.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.providers.whisper import LocalWhisperProvider
from backend.speech import asr_compare, languages
from backend.speech.asr_compare import CorpusItem, compare, fleurs_to_corpus, load_corpus

FA_REF = "پیگیری قرارداد انجام شود"
EN_REF = "please call me back tomorrow"


def _wav(folder: Path, name: str, content: bytes = b"RIFF....WAVEdata") -> Path:
    path = folder / name
    path.write_bytes(content)
    return path


def _write(path: Path, lines: list[Any]) -> Path:
    text = "\n".join(
        line if isinstance(line, str) else json.dumps(line, ensure_ascii=False) for line in lines
    )
    path.write_text(text + "\n", encoding="utf-8")
    return path


def _item(id: str, audio: str, reference: str = FA_REF, language: str = "fa", **extra: Any):
    return {"id": id, "audio": audio, "reference": reference, "language": language, **extra}


# --- load_corpus ----------------------------------------------------------------


def test_load_corpus_reads_a_valid_file(tmp_path: Path) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    a = _wav(audio_dir, "a.wav")
    b = _wav(tmp_path, "b.wav", b"other bytes")
    corpus = _write(
        tmp_path / "corpus.jsonl",
        [
            _item(
                "a",
                "audio/a.wav",
                language="fa-IR",
                slices={"channel": "tel_8k"},
                split="test",
                sha256=hashlib.sha256(a.read_bytes()).hexdigest(),
            ),
            "",
            "   ",
            _item("b", str(b), reference=EN_REF, language="EN"),
        ],
    )
    items = load_corpus(corpus)
    assert items == [
        CorpusItem(
            id="a",
            audio=a,
            reference=FA_REF,
            language="fa",
            slices={"channel": "tel_8k"},
            split="test",
            sha256=hashlib.sha256(a.read_bytes()).hexdigest(),
        ),
        CorpusItem(id="b", audio=b, reference=EN_REF, language="en", slices={}),
    ]
    assert items[1].split == "unspecified" and items[1].sha256 is None


@pytest.mark.parametrize(
    ("bad_line", "fragment"),
    [
        ("{not json", "line 3"),
        (_item("x", "a.wav", unknown="y"), "line 3"),
        ({"id": "x", "audio": "a.wav", "language": "fa"}, "line 3"),
        (_item("", "a.wav"), "line 3"),
        (_item("x", "a.wav", reference="  "), "line 3"),
        (_item("a", "a.wav"), "line 3"),  # duplicate id
        (_item("x", "missing.wav"), "line 3"),
        (_item("x", "a.wav", sha256="0" * 64), "line 3"),
        (_item("x", "a.wav", language="auto"), "line 3"),
        (_item("x", "a.wav", slices={"channel": 8}), "line 3"),
        ([1, 2], "line 3"),
    ],
)
def test_load_corpus_rejects_bad_lines_with_the_physical_line_number(
    tmp_path: Path, bad_line: Any, fragment: str
) -> None:
    _wav(tmp_path, "a.wav")
    # Line 2 is blank: it still counts, so the bad line is line 3.
    corpus = _write(tmp_path / "corpus.jsonl", [_item("a", "a.wav"), "", bad_line])
    with pytest.raises(ValueError) as exc:
        load_corpus(corpus)
    assert fragment in str(exc.value)


# --- compare --------------------------------------------------------------------


class _Model:
    """Fake faster-whisper: text and duration per audio file name."""

    def __init__(self, texts: dict[str, str], detected: dict[str, tuple[str, float]]) -> None:
        self.texts = texts
        self.detected = detected
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, path: str, **kwargs: Any):
        self.calls.append(kwargs)
        name = Path(path).name
        language = kwargs.get("language")
        if language is None:
            code, prob = self.detected[name]
            info = SimpleNamespace(
                language=code,
                language_probability=prob,
                all_language_probs=[(code, prob)],
                duration=2.0,
            )
            return iter([]), info
        segment = SimpleNamespace(start=0.0, end=2.0, text=self.texts[name])
        return iter([segment]), SimpleNamespace(language=language, duration=2.0)


def _provider(model: _Model, model_name: str = "base") -> LocalWhisperProvider:
    return LocalWhisperProvider(
        model_name=model_name, device="cpu", compute_type="int8", model=model
    )


class _Clock:
    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def _corpus(tmp_path: Path) -> list[CorpusItem]:
    a = _wav(tmp_path, "a.wav")
    b = _wav(tmp_path, "b.wav")
    c = _wav(tmp_path, "c.wav")
    return [
        CorpusItem("a", a, FA_REF, "fa", {"channel": "tel_8k"}),
        CorpusItem("b", b, FA_REF, "fa", {"channel": "wideband"}),
        CorpusItem("c", c, EN_REF, "en", {"channel": "wideband"}),
    ]


def test_compare_scores_each_candidate(tmp_path: Path, monkeypatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("compare must never record a qualification")

    monkeypatch.setattr(languages, "record_qualification", forbidden)
    corpus = _corpus(tmp_path)
    perfect = _Model(
        {"a.wav": FA_REF, "b.wav": FA_REF, "c.wav": EN_REF},
        # b: Arabic falls back to fa (correct for a Persian item).
        # c: German falls back to fa (wrong for an English item).
        {"a.wav": ("fa", 0.9), "b.wav": ("ar", 0.7), "c.wav": ("de", 0.9)},
    )
    sloppy = _Model(
        {"a.wav": "پیگیری قرارداد", "b.wav": FA_REF, "c.wav": "please call me"},
        {"a.wav": ("fa", 0.9), "b.wav": ("fa", 0.8), "c.wav": ("en", 0.95)},
    )
    report = compare(
        corpus,
        {"base": _provider(perfect), "small": _provider(sloppy, "small")},
        clock=_Clock(0.5),
    )
    json.dumps(report)  # JSON-safe

    assert report["corpus"] == {"n_items": 3}
    base = report["candidates"]["base"]
    assert (base["model"], base["compute_type"], base["device"]) == ("base", "int8", "cpu")
    assert base["audio_seconds"] == pytest.approx(6.0)
    assert base["processing_seconds"] == pytest.approx(1.5)  # 3 explicit calls x 0.5
    assert base["rtf"] == pytest.approx(0.25)
    assert set(base["slices"]) == {
        "all",
        "language=fa",
        "language=en",
        "channel=tel_8k",
        "channel=wideband",
    }
    assert base["slices"]["all"] == {"n": 3, "wer": 0.0, "cer": 0.0}
    assert base["slices"]["language=fa"]["n"] == 2
    assert base["language_id"] == {
        "n": 3,
        "correct": 2,
        "accuracy": pytest.approx(2 / 3),
        "fallbacks": 2,
    }
    assert base["gate_fa"] == "INSUFFICIENT_DATA"
    first = base["items"][0]
    assert set(first) == {
        "id",
        "language",
        "hypothesis",
        "reference",
        "seconds",
        "audio_seconds",
        "detected_language",
        "decision_source",
    }
    assert first["id"] == "a" and first["seconds"] == pytest.approx(0.5)
    assert [item["decision_source"] for item in base["items"]] == [
        "detected",
        "fallback",
        "fallback",
    ]
    assert [item["detected_language"] for item in base["items"]] == ["fa", "fa", "fa"]

    # Each item is transcribed with its own language as an explicit request.
    explicit = [call["language"] for call in perfect.calls if "language" in call]
    assert explicit == ["fa", "fa", "en"]

    small = report["candidates"]["small"]
    assert small["slices"]["all"]["wer"] > 0
    assert small["slices"]["channel=tel_8k"]["wer"] == pytest.approx(0.5)
    assert small["language_id"]["correct"] == 3


def test_compare_without_detection(tmp_path: Path) -> None:
    model = _Model({"a.wav": FA_REF, "b.wav": FA_REF, "c.wav": EN_REF}, {})
    report = compare(_corpus(tmp_path), {"base": _provider(model)}, detect_language=False)
    base = report["candidates"]["base"]
    assert base["language_id"] is None
    assert all(item["detected_language"] is None for item in base["items"])
    assert all(item["decision_source"] is None for item in base["items"])
    assert all("language" in call for call in model.calls)


def test_compare_rtf_is_none_without_audio(tmp_path: Path) -> None:
    class Silent(_Model):
        def transcribe(self, path: str, **kwargs: Any):
            return iter([]), SimpleNamespace(language=kwargs.get("language"), duration=0.0)

    report = compare(
        _corpus(tmp_path)[:1], {"base": _provider(Silent({}, {}))}, detect_language=False
    )
    base = report["candidates"]["base"]
    assert base["audio_seconds"] == 0.0
    assert base["rtf"] is None


def test_gate_uses_the_f27_bound(tmp_path: Path) -> None:
    audio = _wav(tmp_path, "a.wav")
    long_ref = " ".join(["کلمه"] * 6)
    corpus = [CorpusItem(f"fa-{i}", audio, long_ref, "fa", {}) for i in range(200)]
    good = _Model({"a.wav": long_ref}, {})
    bad = _Model({"a.wav": " ".join(["کلمه"] * 4)}, {})  # WER 1/3, above 0.20
    report = compare(
        corpus, {"good": _provider(good), "bad": _provider(bad)}, detect_language=False
    )
    assert report["candidates"]["good"]["gate_fa"] == "PASS"
    assert report["candidates"]["bad"]["gate_fa"] == "FAIL"


# --- CLI and converter ----------------------------------------------------------


def test_cli_arguments() -> None:
    args = asr_compare.parse_args(
        [
            "--corpus",
            "c.jsonl",
            "--profiles",
            "cpu-base,cpu-small",
            "--out",
            "out",
            "--limit",
            "5",
            "--cpu-threads",
            "4",
            "--no-detect",
        ]
    )
    assert args.corpus == Path("c.jsonl")
    assert args.profiles == ["cpu-base", "cpu-small"]
    assert args.out == Path("out")
    assert args.limit == 5 and args.cpu_threads == 4 and args.detect is False

    defaults = asr_compare.parse_args(["--corpus", "c", "--profiles", "cpu-base", "--out", "o"])
    assert defaults.limit is None and defaults.cpu_threads is None and defaults.detect is True

    for argv in (
        ["--profiles", "cpu-base", "--out", "o"],
        ["--corpus", "c", "--out", "o"],
        ["--corpus", "c", "--profiles", "cpu-base"],
        ["--corpus", "c", "--profiles", "nope", "--out", "o"],
        ["--corpus", "c", "--profiles", "cpu-base", "--out", "o", "--limit", "0"],
        ["--corpus", "c", "--profiles", "cpu-base", "--out", "o", "--cpu-threads", "-1"],
    ):
        with pytest.raises(SystemExit):
            asr_compare.parse_args(argv)


def test_fleurs_to_corpus(tmp_path: Path) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    _wav(audio_dir, "1001.wav", b"one")
    _wav(audio_dir, "1002.wav", b"two")
    _wav(audio_dir, "1003.wav", b"three")
    tsv = tmp_path / "test.tsv"
    tsv.write_text(
        "10\t1001.wav\tسلام دنیا\tسلام دنیا\ts a l a m\t16000\tFEMALE\n"
        "11\t1002.wav\tروز خوب\tروز خوب\tr o z\t32000\tMALE\n"
        "12\t1003.wav\tشب\tشب\ts h a b\t8000\tMALE\n",
        encoding="utf-8",
    )
    out = tmp_path / "corpus" / "fa.jsonl"
    count = fleurs_to_corpus(
        tsv,
        audio_dir,
        out,
        language="fa_ir",
        limit=2,
        slices={"channel": "tel_8k", "source": "fleurs"},
    )
    assert count == 2
    items = load_corpus(out)
    assert [item.reference for item in items] == ["سلام دنیا", "روز خوب"]
    assert {item.language for item in items} == {"fa"}
    assert items[0].slices == {"channel": "tel_8k", "source": "fleurs"}
    assert items[0].audio == (audio_dir / "1001.wav").resolve()
    assert items[0].sha256 == hashlib.sha256(b"one").hexdigest()
    assert len({item.id for item in items}) == 2

    assert (
        fleurs_to_corpus(
            tsv, audio_dir, tmp_path / "all.jsonl", language="fa", limit=None, slices={}
        )
        == 3
    )


_NETWORK_MODULES = frozenset(
    {
        "socket",
        "ssl",
        "http",
        "urllib",
        "urllib3",
        "requests",
        "httpx",
        "aiohttp",
        "ftplib",
        "smtplib",
        "huggingface_hub",
    }
)
_NETWORK_CALLS = frozenset({"open_connection", "start_server", "create_connection"})


def _network_uses(source: str) -> list[str]:
    """Network imports and asyncio connection calls found in the syntax tree."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
            found += [
                f"from {node.module} import {a.name}"
                for a in node.names
                if a.name in _NETWORK_CALLS
            ]
        else:
            modules = []
        found += [m for m in modules if m.split(".")[0] in _NETWORK_MODULES]
        if isinstance(node, ast.Attribute) and node.attr in _NETWORK_CALLS:
            found.append(f".{node.attr}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"__import__", "import_module"}:
                found.append(f"{node.func.id}(...)")
    return found


def test_the_network_check_finds_network_use() -> None:
    sample = (
        "import socket\nimport urllib.request as u\nfrom http import client\n"
        "from huggingface_hub import hf_hub_download\nimport asyncio\n"
        "asyncio.open_connection('h', 1)\nfrom asyncio import open_connection\n"
        "__import__('requests')\n"
    )
    assert len(_network_uses(sample)) == 7
    assert _network_uses("import json\nfrom pathlib import Path\n") == []


def test_no_network_code_in_the_tool() -> None:
    source = Path(asr_compare.__file__).read_text(encoding="utf-8")
    assert _network_uses(source) == []
