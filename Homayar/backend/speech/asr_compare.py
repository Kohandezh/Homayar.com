"""Compare local Whisper profiles on a labelled corpus (HOM-39, the WP13 hook).

The corpus is a JSONL file, one utterance per line (see ``load_corpus``).
``compare`` transcribes every item with each candidate, scores WER/CER with
:mod:`backend.speech.benchmark`, measures speed, and checks how the language
policy treats each item in auto mode.

This tool only measures. It never records a qualification, and it does not
change a language, a registry or a manifest. ``gate_fa`` reports what the F27
gate would say on this corpus; with fewer than 200 Persian items that is
``INSUFFICIENT_DATA``.

CLI (each profile runs in a fresh process, so peak memory is per profile)::

    uv run python -m backend.speech.asr_compare --corpus corpus.jsonl \\
        --profiles cpu-base,cpu-small --out ./run --cpu-threads 8
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import multiprocessing
import os
import platform
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

from backend.providers.asr_profiles import ASR_PROFILES, get_asr_profile
from backend.providers.schemas import ASRRequest
from backend.providers.whisper import LocalWhisperProvider
from backend.speech.benchmark import Thresholds, Utterance, aggregate, gate
from backend.speech.language_policy import LanguageSource, normalize_language
from backend.speech.text_normalize import get_normalizer

_REQUIRED_KEYS = frozenset({"id", "audio", "reference", "language"})
_OPTIONAL_KEYS = frozenset({"slices", "split", "sha256"})
# F27: Persian held-out WER at most 20%.
_F27_THRESHOLDS = Thresholds(max_wer=0.20)


@dataclass(frozen=True)
class CorpusItem:
    id: str
    audio: Path
    reference: str
    language: str
    slices: dict[str, str] = field(default_factory=dict)
    split: str = "unspecified"
    sha256: str | None = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _corpus_item(data: Any, folder: Path, seen: set[str]) -> CorpusItem:
    """One parsed line; raises ``ValueError`` without the line number."""
    if not isinstance(data, dict):
        raise ValueError("a line must be a JSON object")
    missing = sorted(_REQUIRED_KEYS - set(data))
    if missing:
        raise ValueError(f"missing required key(s): {', '.join(missing)}")
    unknown = sorted(set(data) - _REQUIRED_KEYS - _OPTIONAL_KEYS)
    if unknown:
        raise ValueError(f"unknown key(s): {', '.join(unknown)}")

    item_id, reference, audio = data["id"], data["reference"], data["audio"]
    if not isinstance(item_id, str) or not item_id.strip():
        raise ValueError("'id' must be a non-empty string")
    if item_id in seen:
        raise ValueError(f"duplicate id {item_id!r}")
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError("'reference' must be a non-empty string")
    language = normalize_language(data["language"]) if isinstance(data["language"], str) else None
    if language is None:
        raise ValueError("'language' must be a language code")
    slices = data.get("slices", {})
    if not isinstance(slices, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in slices.items()
    ):
        raise ValueError("'slices' must map strings to strings")
    split = data.get("split", "unspecified")
    if not isinstance(split, str) or not split:
        raise ValueError("'split' must be a non-empty string")
    if not isinstance(audio, str) or not audio:
        raise ValueError("'audio' must be a path")
    audio_path = Path(audio)
    if not audio_path.is_absolute():
        audio_path = folder / audio_path
    audio_path = audio_path.resolve()
    if not audio_path.is_file():
        raise ValueError(f"audio file not found: {audio}")
    sha256 = data.get("sha256")
    if sha256 is not None:
        if not isinstance(sha256, str):
            raise ValueError("'sha256' must be a hex string")
        if _sha256(audio_path) != sha256.lower():
            raise ValueError(f"sha256 mismatch for {audio}")
    return CorpusItem(
        id=item_id,
        audio=audio_path,
        reference=reference,
        language=language,
        slices=dict(slices),
        split=split,
        sha256=sha256,
    )


def load_corpus(path: Path) -> list[CorpusItem]:
    """Read and check a corpus JSONL file.

    Relative audio paths resolve against the file's folder. Blank lines are
    skipped but still counted: error messages use physical 1-based line numbers.
    """
    path = Path(path)
    folder = path.resolve().parent
    items: list[CorpusItem] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                item = _corpus_item(json.loads(line), folder, seen)
            except ValueError as exc:  # json.JSONDecodeError is a ValueError
                raise ValueError(f"{path}: line {number}: {exc}") from exc
            seen.add(item.id)
            items.append(item)
    return items


def _score(
    corpus: Sequence[CorpusItem],
    provider: LocalWhisperProvider,
    clock: Callable[[], float],
    detect_language: bool,
) -> dict[str, Any]:
    async def transcribe_all() -> list[tuple[Any, float]]:
        results = []
        for item in corpus:
            request = ASRRequest(audio_path=str(item.audio), language=item.language)
            start = clock()
            result = await provider.transcribe(request)
            results.append((result, clock() - start))
        return results

    transcriptions = asyncio.run(transcribe_all())
    items: list[dict[str, Any]] = []
    utterances: list[Utterance] = []
    correct = fallbacks = 0
    for item, (result, seconds) in zip(corpus, transcriptions, strict=True):
        decision = provider.decide_language(str(item.audio), None) if detect_language else None
        if decision is not None:
            correct += decision.language == item.language
            fallbacks += decision.source is LanguageSource.FALLBACK
        utterances.append(
            Utterance(
                reference=item.reference,
                hypothesis=result.text,
                language=item.language,
                slices={**item.slices, "language": item.language},
            )
        )
        items.append(
            {
                "id": item.id,
                "language": item.language,
                "hypothesis": result.text,
                "reference": item.reference,
                "seconds": seconds,
                "audio_seconds": result.duration_seconds,
                "detected_language": decision.language if decision else None,
                "decision_source": decision.source.value if decision else None,
            }
        )

    dimensions = [
        "language",
        *sorted({key for item in corpus for key in item.slices} - {"language"}),
    ]
    slices = aggregate(utterances, dimensions)
    audio_seconds = float(sum(item["audio_seconds"] for item in items))
    processing_seconds = float(sum(item["seconds"] for item in items))
    identity = provider.identity()
    return {
        "model": identity.model,
        "compute_type": str(identity.params.get("compute_type", "")),
        "device": provider.device,
        "audio_seconds": audio_seconds,
        "processing_seconds": processing_seconds,
        "rtf": processing_seconds / audio_seconds if audio_seconds > 0 else None,
        "slices": {
            key: {"n": result.n_utterances, "wer": result.counts.wer, "cer": result.counts.cer}
            for key, result in slices.items()
        },
        "language_id": (
            {
                "n": len(items),
                "correct": correct,
                "accuracy": correct / len(items) if items else None,
                "fallbacks": fallbacks,
            }
            if detect_language
            else None
        ),
        "gate_fa": gate(slices.get("language=fa"), _F27_THRESHOLDS).value,
        "items": items,
    }


def compare(
    corpus: Sequence[CorpusItem],
    providers: Mapping[str, LocalWhisperProvider],
    *,
    clock: Callable[[], float] = time.perf_counter,
    detect_language: bool = True,
) -> dict:
    """Score every candidate on the corpus. The result is JSON-safe.

    Each item is transcribed with its own language as an explicit request.
    ``processing_seconds`` times only those calls, not the detection pass.
    """
    for item in corpus:
        try:
            get_normalizer(item.language)
        except KeyError as exc:
            raise ValueError(f"item {item.id!r}: no text normalizer for {item.language!r}") from exc
    return {
        "corpus": {"n_items": len(corpus)},
        "candidates": {
            name: _score(corpus, provider, clock, detect_language)
            for name, provider in providers.items()
        },
    }


def fleurs_to_corpus(
    tsv: Path,
    audio_dir: Path,
    out: Path,
    *,
    language: str,
    limit: int | None,
    slices: Mapping[str, str],
) -> int:
    """Write a corpus JSONL from a local Google FLEURS split; return the count.

    FLEURS TSV columns: id, file name, raw transcription, normalized
    transcription, characters, sample count, gender. The raw transcription is
    the reference (the benchmark normalizes it). Local files only.
    """
    code = normalize_language(language)
    if code is None:
        raise ValueError(f"{language!r} is not a language code")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    folder = out.resolve().parent
    suffix = "".join(f"/{key}={value}" for key, value in sorted(slices.items()))
    count = 0
    with (
        Path(tsv).open(encoding="utf-8", newline="") as source,
        out.open("w", encoding="utf-8") as target,
    ):
        for row in csv.reader(source, delimiter="\t", quoting=csv.QUOTE_NONE):
            if limit is not None and count >= limit:
                break
            if len(row) < 3:
                continue
            audio = (Path(audio_dir) / row[1]).resolve()
            if not audio.is_file():
                raise ValueError(f"FLEURS audio file not found: {audio}")
            record = {
                "id": f"fleurs/{code}/{Path(row[1]).stem}{suffix}",
                "audio": os.path.relpath(audio, folder),
                "reference": row[2].strip(),
                "language": code,
                "slices": dict(slices),
                "split": Path(tsv).stem,
                "sha256": _sha256(audio),
            }
            target.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


# --- CLI ------------------------------------------------------------------------


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return number


def _non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return number


def _profile_names(value: str) -> list[str]:
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names:
        raise argparse.ArgumentTypeError("name at least one profile")
    for name in names:
        if name not in ASR_PROFILES:
            raise argparse.ArgumentTypeError(
                f"unknown profile {name!r}; valid: {', '.join(sorted(ASR_PROFILES))}"
            )
    return names


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m backend.speech.asr_compare",
        description="Compare local Whisper profiles on a JSONL corpus. Measures only.",
    )
    parser.add_argument("--corpus", type=Path, required=True, help="corpus JSONL file")
    parser.add_argument("--profiles", type=_profile_names, required=True, help="a,b,c")
    parser.add_argument("--out", type=Path, required=True, help="folder for summary files")
    parser.add_argument("--limit", type=_positive_int, default=None, help="first N items only")
    parser.add_argument(
        "--cpu-threads",
        type=_non_negative_int,
        default=None,
        help="override every profile's CPU threads (same value for a fair run)",
    )
    parser.add_argument("--no-detect", dest="detect", action="store_false", help="skip auto pass")
    return parser.parse_args(argv)


def _peak_rss_mib() -> float | None:
    try:
        import resource
    except ImportError:  # not on this platform
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports KiB.
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def _run_profile(
    name: str,
    corpus_path: str,
    limit: int | None,
    cpu_threads: int | None,
    detect: bool,
    result_path: str,
) -> None:
    """Child process: load one profile, score it, write its JSON result."""
    profile = get_asr_profile(name)
    corpus = load_corpus(Path(corpus_path))[:limit]
    provider = LocalWhisperProvider(
        model_name=profile.model,
        device=profile.device,
        compute_type=profile.compute_type,
        device_index=profile.device_index,
        cpu_threads=profile.cpu_threads if cpu_threads is None else cpu_threads,
    )
    start = time.perf_counter()
    provider._get_model()  # load now, so load time is not counted as processing time
    load_seconds = time.perf_counter() - start
    candidate = compare(corpus, {name: provider}, detect_language=detect)["candidates"][name]
    candidate["load_seconds"] = load_seconds
    candidate["cpu_threads"] = provider.cpu_threads
    candidate["peak_rss_mib"] = _peak_rss_mib()
    Path(result_path).write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")


def _version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def _environment() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "faster_whisper": _version("faster-whisper"),
        "ctranslate2": _version("ctranslate2"),
    }


def _number(value: Any, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# ASR profile comparison",
        "",
        f"Corpus: `{summary['corpus']['path']}` ({summary['corpus']['n_items']} items, "
        f"sha256 `{summary['corpus']['sha256']}`).",
        "",
        "Measured only. Nothing here qualifies a model or a language.",
        "",
        "| profile | slice | n | WER | CER |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, candidate in summary["candidates"].items():
        for key, result in candidate.get("slices", {}).items():
            lines.append(
                f"| {name} | {key} | {result['n']} | {_number(result['wer'])} | "
                f"{_number(result['cer'])} |"
            )
    lines += [
        "",
        "| profile | model | compute | language-ID accuracy | fallbacks | RTF | load s | "
        "peak RSS MiB | gate_fa |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, candidate in summary["candidates"].items():
        if "error" in candidate:
            lines.append(f"| {name} | error: {candidate['error']} | | | | | | | |")
            continue
        language_id = candidate["language_id"] or {}
        lines.append(
            f"| {name} | {candidate['model']} | {candidate['compute_type']} | "
            f"{_number(language_id.get('accuracy'))} | {language_id.get('fallbacks', 'n/a')} | "
            f"{_number(candidate['rtf'])} | {_number(candidate['load_seconds'], 1)} | "
            f"{_number(candidate['peak_rss_mib'], 0)} | {candidate['gate_fa']} |"
        )
    lines += ["", "Environment: `" + json.dumps(summary["environment"]) + "`", ""]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    corpus = load_corpus(args.corpus)  # fail early, before any model loads
    n_items = len(corpus[: args.limit])
    args.out.mkdir(parents=True, exist_ok=True)
    context = multiprocessing.get_context("spawn")
    candidates: dict[str, Any] = {}
    failed = False
    for name in args.profiles:
        result_path = args.out / f"{name}.json"
        result_path.unlink(missing_ok=True)
        process = context.Process(
            target=_run_profile,
            args=(
                name,
                str(args.corpus),
                args.limit,
                args.cpu_threads,
                args.detect,
                str(result_path),
            ),
        )
        process.start()
        process.join()
        if process.exitcode == 0 and result_path.is_file():
            candidates[name] = json.loads(result_path.read_text(encoding="utf-8"))
        else:
            candidates[name] = {"error": f"exit code {process.exitcode}"}
            failed = True
    summary = {
        "corpus": {"n_items": n_items, "path": str(args.corpus), "sha256": _sha256(args.corpus)},
        "candidates": candidates,
        "environment": _environment(),
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.out / "summary.md").write_text(_markdown(summary), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
