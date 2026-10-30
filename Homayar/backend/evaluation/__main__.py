"""Command line: ``python -m backend.evaluation <kind> <path.jsonl> [options]`` (HOM-69).

Run from ``Homayar/``.  Prints one JSON document on stdout.  Bad data gives one line on
stderr and exit code 1; bad arguments give an argparse error and exit code 2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable, Sequence
from typing import Any

from backend.evaluation.agreement import cohen_kappa, kappa_by_criterion, score_mae
from backend.evaluation.asr import evaluate_asr
from backend.evaluation.classification import evaluate_classification
from backend.evaluation.common import EVALUATION_VERSION, evaluate_by_slice
from backend.evaluation.diarization import evaluate_diarization
from backend.evaluation.entities import evaluate_entities
from backend.evaluation.factual_support import evaluate_factual_support
from backend.evaluation.loaders import KINDS, load_jsonl
from backend.evaluation.retrieval import evaluate_retrieval
from backend.speech.text_normalize import normalize_for_matching


def _csv(value: str) -> list[str]:
    return [p.strip() for p in value.split(",") if p.strip()]


def _ks(value: str) -> list[int]:
    try:
        ks = [int(p) for p in _csv(value)]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected comma separated ints: {value!r}") from exc
    if not ks or min(ks) < 1:
        raise argparse.ArgumentTypeError(f"expected one or more ints >= 1: {value!r}")
    return list(dict.fromkeys(ks))


def _non_negative(value: str) -> int:
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected an int: {value!r}") from exc
    if n < 0:
        raise argparse.ArgumentTypeError(f"expected an int >= 0: {value!r}")
    return n


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m backend.evaluation",
                                description="Run an evaluator on a JSONL file.")  # fmt: skip
    p.add_argument("kind", choices=KINDS)
    p.add_argument("path")
    p.add_argument("--slices", type=_csv, default=[], help="slice dimensions: dim1,dim2")
    p.add_argument("--collar-ms", type=_non_negative, default=0, help="diarization")
    p.add_argument("--skip-overlap", action="store_true", help="diarization")
    p.add_argument("--labels", type=_csv, default=None, help="intent taxonomy: a,b,c")
    p.add_argument("--match", choices=("value", "span"), default="value", help="entities")
    p.add_argument("--normalize", choices=("none", "fa", "en"), default="none", help="entities")
    p.add_argument("--categories", type=_csv, default=None, help="qa_agreement: a,b,c")
    p.add_argument("--weights", choices=("none", "linear", "quadratic"), default="none",
                   help="qa_agreement")  # fmt: skip
    p.add_argument("--ks", type=_ks, default=[1, 5, 10], help="retrieval: 1,5,10")
    return p


def _evaluator(
    kind: str, a: argparse.Namespace
) -> tuple[dict[str, Any], Callable[[list[Any]], Any]]:
    """The effective options of ``kind`` and a function from items to a JSON-safe dict."""
    if kind == "asr":
        return {}, lambda xs: evaluate_asr(xs).as_dict()
    if kind == "diarization":
        opts = {"collar_ms": a.collar_ms, "skip_overlap": a.skip_overlap}
        return opts, lambda xs: evaluate_diarization(xs, **opts).as_dict()
    if kind == "intent":
        return {"labels": a.labels}, lambda xs: evaluate_classification(
            xs, labels=a.labels).as_dict()  # fmt: skip
    if kind == "entities":
        lang = a.normalize
        norm = None if lang == "none" else (lambda v: normalize_for_matching(v, lang))
        return {"match": a.match, "normalize": lang}, lambda xs: evaluate_entities(
            xs, match=a.match, normalize=norm).as_dict()  # fmt: skip
    if kind == "qa_agreement":
        opts = {"categories": a.categories, "weights": a.weights}
        return opts, lambda xs: {
            "kappa": cohen_kappa(xs, **opts).as_dict(),
            "by_criterion": {k: m.as_dict() for k, m in kappa_by_criterion(xs, **opts).items()},
        }
    if kind == "qa_score":
        return {}, lambda xs: {"score_mae": score_mae(xs).as_dict()}
    if kind == "factual_support":
        return {}, lambda xs: evaluate_factual_support(xs).as_dict()
    return {"ks": a.ks}, lambda xs: evaluate_retrieval(xs, ks=a.ks).as_dict()


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    a = parser.parse_args(argv)
    if a.weights != "none" and a.categories is None:
        parser.error(f"--weights {a.weights} needs --categories")
    options, evaluate = _evaluator(a.kind, a)
    options = {**options, "slices": a.slices}
    try:
        with open(a.path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        items = load_jsonl(a.kind, a.path)
        slices = evaluate_by_slice(items, evaluate, a.slices)
        doc = {
            "version": EVALUATION_VERSION,
            "kind": a.kind,
            "input_sha256": digest,
            "options": options,
            "slices": slices,
        }
        text = json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False)
    except (ValueError, ArithmeticError, OSError) as exc:
        print(f"error: {exc}".replace("\n", " "), file=sys.stderr)
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
