"""One side's measurements, run inside that side's tree (HOM-321).

The runner starts this file as ``[sys.executable, "-P", <this file>, ...]``
with ``PYTHONPATH`` and the working directory set to the side root, so
``import backend`` resolves inside that side only.  ``-P`` keeps this file's
own folder off ``sys.path``.  The module path guard below fails the run when
the side's modules did not load from the side root: without it, both sides
could silently run the same code and every gate would pass.

The worker imports the standard library, the side's ``backend.privacy``
modules (with whatever they import) and the working tree's
``backend/tests/_cost.py`` loaded by file path.  It never imports
``backend.tests.*`` by module name: that name would resolve inside the base
tree, where this tool does not exist.  The class lists, the export class
values and the cost workload names are defined here once; ``gates.py``
imports them, and this file cannot import its siblings.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import sys
from pathlib import Path

# The class lists of SPEC section 3.4, fixed for both sides.
CLASS_LISTS = {
    "all": ["email", "phone", "ir_national_id", "bank_card", "iban", "ir_postal_code"],
    "speech": ["phone", "ir_national_id", "email"],
    "export": ["email", "phone", "ir_national_id", "bank_card", "iban"],
    "phone": ["phone"],
    "id": ["ir_national_id"],
    "card": ["bank_card"],
    "postal": ["ir_postal_code"],
    "phone+card": ["phone", "bank_card"],
}
EXPORT_CLASS_VALUES = CLASS_LISTS["export"]
COST_WORKLOADS = ("detect-all-dense", "detect-export-dense", "detect-all-words", "export-dsar")
COST_MODULES = (
    "backend.privacy.text_pii",
    "backend.privacy.subject_export",
    "backend.speech.text_normalize",
)


def _fail(message: str) -> int:
    print(f"worker: {message}", file=sys.stderr)
    return 3


def _load_cost_module(path: str):
    spec = importlib.util.spec_from_file_location("privacy_gates_cost", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load cost module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description="Privacy gate worker (internal, HOM-321).")
    parser.add_argument("--side", required=True, choices=("base", "patch"))
    parser.add_argument("--side-root", required=True)
    parser.add_argument("--corpus-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--cost-rows", type=int, required=True)
    parser.add_argument("--cost-module", required=True)
    args = parser.parse_args()
    side_root = Path(args.side_root).resolve()

    try:
        import backend.privacy.subject_export as subject_export
        import backend.privacy.text_pii as text_pii
    except Exception as exc:  # reported as one line for the runner
        return _fail(f"cannot import backend.privacy from {side_root}: {exc}")

    # Module path guard: the code under test must come from the side root.
    for module in (text_pii, subject_export):
        loaded = Path(module.__file__).resolve()
        if not loaded.is_relative_to(side_root):
            return _fail(f"{module.__name__} loaded from {loaded}, expected inside {side_root}")

    try:
        cost = _load_cost_module(args.cost_module)
    except Exception as exc:  # reported as one line for the runner
        return _fail(f"cannot load the cost module {args.cost_module}: {exc}")

    corpora: dict[str, list[dict]] = {}
    for path in sorted(Path(args.corpus_dir).glob("*.jsonl")):
        corpora[path.stem] = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        ]
    for name in ("dense", "words", "dsar", "prices"):
        if not corpora.get(name):
            return _fail(f"corpus {name} is missing or empty in {args.corpus_dir}")

    # Spans of detect(text, classes) for every row and every class list.
    try:
        class_sets = {
            list_name: [text_pii.PiiClass(value) for value in values]
            for list_name, values in CLASS_LISTS.items()
        }
    except ValueError as exc:
        return _fail(f"unknown PII class in the class lists: {exc}")
    detect: dict[str, dict[str, dict[str, list]]] = {}
    for name in sorted(corpora):
        detect[name] = {}
        for row in corpora[name]:
            found = {}
            for list_name, classes in class_sets.items():
                spans = text_pii.detect(row["text"], classes)
                found[list_name] = [[s.start, s.end, s.pii_class.value, s.value] for s in spans]
            detect[name][row["id"]] = found

    # Subject export: the matcher classes of every dsar row.
    identifiers_by_row: dict[str, list] = {}
    exports: dict[str, list[str]] = {}
    rejected = 0
    for row in corpora["dsar"]:
        identifiers = []
        for raw in row["subject"]:
            try:
                identifiers.append(subject_export.parse_subject_identifier(raw))
            except (TypeError, ValueError):
                rejected += 1
        identifiers_by_row[row["id"]] = identifiers
        exports[row["id"]] = subject_export._Matcher(identifiers).classes(row["text"])

    # Cost: lines run per workload, one lines_run call per workload that loops
    # over all of the workload's rows.  Matchers are built before counting.
    all_classes = frozenset(text_pii.PiiClass)
    export_classes = frozenset(text_pii.PiiClass(value) for value in EXPORT_CLASS_VALUES)
    dense = corpora["dense"][: args.cost_rows]
    words = corpora["words"][: args.cost_rows]
    dsar = corpora["dsar"][: args.cost_rows]
    matchers = [subject_export._Matcher(identifiers_by_row[row["id"]]) for row in dsar]
    counts = {
        "detect-all-dense": cost.lines_run(
            COST_MODULES, lambda: [text_pii.detect(r["text"], all_classes) for r in dense]
        )[0],
        "detect-export-dense": cost.lines_run(
            COST_MODULES, lambda: [text_pii.detect(r["text"], export_classes) for r in dense]
        )[0],
        "detect-all-words": cost.lines_run(
            COST_MODULES, lambda: [text_pii.detect(r["text"], all_classes) for r in words]
        )[0],
        "export-dsar": cost.lines_run(
            COST_MODULES,
            lambda: [m.classes(r["text"]) for m, r in zip(matchers, dsar, strict=True)],
        )[0],
    }
    if sorted(counts) != sorted(COST_WORKLOADS):
        return _fail("the cost workloads do not match COST_WORKLOADS")
    cost_counts = {name: counts[name] for name in COST_WORKLOADS}

    results = {
        "meta": {
            "side": args.side,
            "python": platform.python_version(),
            "detector_version": getattr(text_pii, "DETECTOR_VERSION", None),
            "text_pii": str(Path(text_pii.__file__).resolve()),
            "subject_export": str(Path(subject_export.__file__).resolve()),
            "rejected_subject_identifiers": rejected,
        },
        "detect": detect,
        "export": exports,
        "cost": cost_counts,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(results, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
