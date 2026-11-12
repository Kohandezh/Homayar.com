"""Production corrections, kept strictly separate from evaluation data (HOM-71).

A production correction is a JSON object per line (``production-correction/1``): the
before and after state of one supervised fix in production.  ``append`` validates every
input record first and refuses to write anything on any error, and refuses (exit 2) to
place the store inside a corpus root, so corrections can never sit next to, let less
inside, the gold corpora.  ``summary`` counts the store.  ``loaders.parse_record``
refuses these records, so a correction can never enter an evaluation corpus or a
candidate report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from backend.evaluation.loaders import KINDS, _check_finite
from backend.evaluation.promotion_formats import PolicyRefused, _iso_utc, _non_blank

CORRECTION_ORIGIN = "production_correction"

_CORRECTION_KEYS = {"correction_id", "origin", "kind", "model_version", "prompt_version",
                    "workspace_ref", "subject_ref", "corrected_at", "corrected_by_role",
                    "before", "after"}  # fmt: skip


@dataclass(frozen=True)
class Correction:
    """One supervised fix of production output; never loadable as an evaluation record."""

    correction_id: str
    kind: str
    model_version: str
    prompt_version: str | None
    workspace_ref: str
    subject_ref: str
    corrected_at: str
    corrected_by_role: str
    before: dict[str, Any]
    after: dict[str, Any]


def parse_correction(record: Any) -> Correction:
    """Read one ``production-correction/1`` record; unknown keys and wrong shapes raise."""
    if not isinstance(record, dict):
        raise ValueError(f"a correction must be a JSON object, got {type(record).__name__}")
    _check_finite(record)
    for key in record:
        if key not in _CORRECTION_KEYS:
            raise ValueError(f"unknown key {key!r} in a correction record")
    if _req(record, "origin") != CORRECTION_ORIGIN:
        raise ValueError(f"key 'origin' must be {CORRECTION_ORIGIN!r}, got {record['origin']!r}")
    kind = _req(record, "kind")
    if kind not in KINDS:
        raise ValueError(f"key 'kind' must be one of {list(KINDS)}, got {kind!r}")
    correction_id = _non_blank(_req(record, "correction_id"), "correction_id")
    model_version = _non_blank(_req(record, "model_version"), "model_version")
    prompt_version = _req(record, "prompt_version")
    if prompt_version is not None:
        prompt_version = _non_blank(prompt_version, "prompt_version")
    workspace_ref = _non_blank(_req(record, "workspace_ref"), "workspace_ref")
    subject_ref = _non_blank(_req(record, "subject_ref"), "subject_ref")
    corrected_at = _iso_utc(_req(record, "corrected_at"), "corrected_at")
    corrected_by_role = _non_blank(_req(record, "corrected_by_role"), "corrected_by_role")
    before = _req(record, "before")
    if not isinstance(before, dict):
        raise ValueError(f"key 'before' must be an object, got {before!r}")
    after = _req(record, "after")
    if not isinstance(after, dict):
        raise ValueError(f"key 'after' must be an object, got {after!r}")
    return Correction(correction_id, kind, model_version, prompt_version, workspace_ref,
                      subject_ref, corrected_at, corrected_by_role, before, after)  # fmt: skip


def _req(record: Mapping[str, Any], key: str) -> Any:
    if key not in record:
        raise ValueError(f"missing required key {key!r} in a correction record")
    return record[key]


def _read_lines(path: str | os.PathLike[str]) -> list[Any]:
    docs: list[Any] = []
    with open(path, "rb") as fh:
        for n, raw in enumerate(fh, 1):
            try:
                line = raw.decode("utf-8")
                if not line.strip():
                    continue
                docs.append(json.loads(line))
            except ValueError as exc:
                raise ValueError(f"{os.fspath(path)}: line {n}: {exc}") from exc
    return docs


def _parse_store(path: str | os.PathLike[str]) -> list[Correction]:
    corrections: list[Correction] = []
    for n, doc in enumerate(_read_lines(path), 1):
        try:
            corrections.append(parse_correction(doc))
        except ValueError as exc:
            raise ValueError(f"{os.fspath(path)}: line {n}: {exc}") from exc
    return corrections


def _refuse_if_inside_roots(store: str | os.PathLike[str], roots: Sequence[str]) -> None:
    real_store = os.path.realpath(os.fspath(store))
    for root in roots:
        real_root = os.path.realpath(os.fspath(root))
        if real_store == real_root or os.path.commonpath([real_store, real_root]) == real_root:
            raise PolicyRefused(
                f"the corrections store {real_store!r} is inside the corpus root {real_root!r}"
            )
        if os.path.isdir(real_root):
            parent = os.path.dirname(real_store)
            while parent and parent != os.sep:
                if os.path.isdir(parent) and os.path.samefile(parent, real_root):
                    raise PolicyRefused(
                        f"the corrections store {real_store!r} is inside the corpus root "
                        f"{real_root!r}"
                    )
                parent = os.path.dirname(parent)


def _append_lines(path: str | os.PathLike[str], records: Sequence[Correction]) -> None:
    with open(path, "a+b") as fh:
        fh.seek(0, os.SEEK_END)
        if fh.tell():
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                fh.write(b"\n")
        for record in records:
            line = json.dumps(record_to_doc(record), sort_keys=True, ensure_ascii=False,
                              separators=(",", ":"), allow_nan=False)  # fmt: skip
            fh.write(line.encode("utf-8") + b"\n")


def append_corrections(
    store: str | os.PathLike[str],
    inp: str | os.PathLike[str],
    corpus_roots: Sequence[str] = (),
) -> dict[str, Any]:
    """Validate every input record, then append them in input order to the store."""
    records = [parse_correction(doc) for doc in _read_lines(inp)]
    seen: set[str] = set()
    for record in records:
        if record.correction_id in seen:
            raise ValueError(f"duplicate correction_id {record.correction_id!r} in the input")
        seen.add(record.correction_id)
    _refuse_if_inside_roots(store, corpus_roots)
    existing = _parse_store(store) if os.path.exists(store) else []
    for record in existing:
        if record.correction_id in seen:
            raise ValueError(f"correction_id {record.correction_id!r} is already in the store")
        seen.add(record.correction_id)
    _append_lines(store, records)
    return {"appended": len(records), "total": len(existing) + len(records)}


def record_to_doc(record: Correction) -> dict[str, Any]:
    return {
        "correction_id": record.correction_id,
        "origin": CORRECTION_ORIGIN,
        "kind": record.kind,
        "model_version": record.model_version,
        "prompt_version": record.prompt_version,
        "workspace_ref": record.workspace_ref,
        "subject_ref": record.subject_ref,
        "corrected_at": record.corrected_at,
        "corrected_by_role": record.corrected_by_role,
        "before": record.before,
        "after": record.after,
    }


def summarize(store: str | os.PathLike[str]) -> dict[str, Any]:
    """Count the corrections in a store by kind and by model version."""
    records = _parse_store(store)
    by_kind: dict[str, int] = {}
    by_model: dict[str, int] = {}
    for record in records:
        by_kind[record.kind] = by_kind.get(record.kind, 0) + 1
        by_model[record.model_version] = by_model.get(record.model_version, 0) + 1
    return {
        "total": len(records),
        "by_kind": dict(sorted(by_kind.items())),
        "by_model_version": dict(sorted(by_model.items())),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.evaluation.corrections",
        description="Hold production corrections outside the evaluation corpora.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_append = sub.add_parser("append", help="append validated records to a corrections store")
    p_append.add_argument("store")
    p_append.add_argument("input")
    p_append.add_argument("--corpus-root", action="append", default=[],
                          help="a directory the store must not live in (repeatable)")  # fmt: skip
    p_summary = sub.add_parser("summary", help="count the corrections in a store")
    p_summary.add_argument("store")
    args = parser.parse_args(argv)
    try:
        if args.command == "append":
            doc = append_corrections(args.store, args.input, args.corpus_root)
        else:
            doc = summarize(args.store)
    except PolicyRefused as exc:
        print(f"error: {exc}".replace("\n", " "), file=sys.stderr)
        return 2
    except (ValueError, ArithmeticError, OSError, RecursionError) as exc:
        print(f"error: {exc}".replace("\n", " "), file=sys.stderr)
        return 1
    print(json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
