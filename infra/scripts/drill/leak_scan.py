"""Diagnostics leakage scan of a restore drill (HOM-118) — drill hosts only.

Searches the drill's diagnostics (service logs, console output, tool
outputs, reports, manifest, timelines) for every secret the drill generated
and for the transcript text the demo seed writes. The result names only the
file and the category of a match, never the matched value.

    python leak_scan.py --root "$DRILL_DIR" --env drill.env --key backup.key \\
        --demo-seed Homayar/backend/database/demo_seed.py \\
        --exclude drill.env backup.key evidence/tenants.json \\
        --out evidence/leakage-scan.json logs evidence restore-backups/restores

Categories: each secret of drill.env, ``backup_key`` (base64 and hex of the
key bytes), ``offsite_secret`` ($HOMAYAR_BACKUP_S3_SECRET_KEY),
``demo_password`` and ``transcript`` (every scripted turn, summary, action
and decision of ``demo_seed.SCRIPTS``, whole and as a 20-character prefix,
raw and JSON-escaped). Exit 1 when anything matched.

``--expect-logs DIR PREFIX...`` asserts the log set is complete: for each
prefix, ``DIR/PREFIX.services`` (written by the drill's collect_logs) must
list at least one service and ``DIR/PREFIX-<service>.log`` must exist for
each; ``--expect-files`` names further files that must exist. A missing log
is reported (``missing``) and also exits 1: an unscanned log is not clean.
"""

from __future__ import annotations

import argparse
import ast
import base64
import json
import os
import sys
import time
from pathlib import Path

ENV_SECRETS = (
    "POSTGRES_PASSWORD",
    "REDIS_PASSWORD",
    "JWT_SECRET",
    "TOKEN_PEPPER",
    "MINIO_ROOT_PASSWORD",
    "STORAGE_SECRET_KEY",
)
DEMO_PASSWORD = "Homayar-Demo-1405"  # noqa: S105 - the public demo credential
MIN_SECRET = 8
MIN_PHRASE = 12
PREFIX = 20


def _env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip()
    return values


def _phrases(demo_seed: Path) -> list[str]:
    """String constants of ``SCRIPTS`` long enough to be transcript content."""
    tree = ast.parse(demo_seed.read_text(encoding="utf-8"))
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "SCRIPTS" and node.value is not None:
            found = [
                n.value
                for n in ast.walk(node.value)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
            return sorted(
                {p for p in found if len(p) >= MIN_PHRASE and not p.startswith("SPEAKER_")}
            )
    raise SystemExit("SCRIPTS not found in the demo seed")


def _variants(text: str) -> set[str]:
    forms = {text, json.dumps(text)[1:-1]}
    if len(text) > PREFIX:
        forms |= {text[:PREFIX], json.dumps(text[:PREFIX])[1:-1]}
    return forms


def _patterns(args, root: Path) -> dict[str, set[str]]:
    env = _env(root / args.env)
    patterns: dict[str, set[str]] = {}
    for name in ENV_SECRETS:
        value = env.get(name, "")
        if len(value) < MIN_SECRET:
            raise SystemExit(f"{name} is missing or too short in {args.env}")
        patterns[name] = {value}
    material = (root / args.key).read_text().strip().partition(":")[2]
    raw = base64.b64decode(material)
    patterns["backup_key"] = {material, raw.hex(), base64.urlsafe_b64encode(raw).decode()}
    offsite = os.environ.get("HOMAYAR_BACKUP_S3_SECRET_KEY", "")
    if len(offsite) < MIN_SECRET:
        raise SystemExit("HOMAYAR_BACKUP_S3_SECRET_KEY (off-site secret) is not set")
    patterns["offsite_secret"] = {offsite}
    patterns["demo_password"] = {DEMO_PASSWORD}
    patterns["transcript"] = set().union(*(_variants(p) for p in _phrases(Path(args.demo_seed))))
    return patterns


def _files(root: Path, targets: list[str], excluded: set[Path]) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        path = root / target
        candidates = sorted(path.rglob("*")) if path.is_dir() else [path]
        for candidate in candidates:
            if candidate.is_file() and candidate.resolve() not in excluded:
                files.append(candidate)
    return files


def _missing(root: Path, expect_logs: list[str], expect_files: list[str]) -> list[str]:
    missing = [f for f in expect_files if not (root / f).is_file()]
    if expect_logs:
        directory, prefixes = expect_logs[0], expect_logs[1:]
        if not prefixes:
            raise SystemExit("--expect-logs needs a directory and at least one prefix")
        for prefix in prefixes:
            listing = root / directory / f"{prefix}.services"
            services = listing.read_text().split() if listing.is_file() else []
            if not services:
                missing.append(str(listing.relative_to(root)))
            missing += [
                f"{directory}/{prefix}-{service}.log"
                for service in services
                if not (root / directory / f"{prefix}-{service}.log").is_file()
            ]
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--env", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--demo-seed", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--exclude", nargs="*", default=[])
    parser.add_argument("--expect-logs", nargs="*", default=[])
    parser.add_argument("--expect-files", nargs="*", default=[])
    parser.add_argument("targets", nargs="+")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    out = (root / args.out).resolve()
    patterns = _patterns(args, root)
    excluded = {(root / e).resolve() for e in args.exclude} | {out}
    totals = {category: 0 for category in patterns}
    matches = []
    scanned = []
    for path in _files(root, args.targets, excluded):
        text = path.read_bytes().decode("utf-8", errors="replace")
        lowered = text.lower()
        relative = str(path.relative_to(root))
        scanned.append({"file": relative, "bytes": path.stat().st_size})
        for category, forms in patterns.items():
            # JSON escapes (ک) may come in either letter case.
            count = sum(
                (lowered.count(form.lower()) if "\\u" in form else text.count(form))
                for form in forms
            )
            if count:
                totals[category] += count
                matches.append({"file": relative, "category": category, "count": count})
    missing = _missing(root, args.expect_logs, args.expect_files)
    result = {
        "kind": "homayar-drill-leakage-scan/1",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "clean": not matches and not missing,
        "missing": missing,
        "categories": totals,
        "patterns_per_category": {category: len(forms) for category, forms in patterns.items()},
        "transcript_phrases": len(_phrases(Path(args.demo_seed))),
        "files_scanned": len(scanned),
        "bytes_scanned": sum(f["bytes"] for f in scanned),
        "excluded": sorted(args.exclude),
        "matches": matches,
        "files": scanned,
    }
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("clean", "categories", "files_scanned", "missing", "matches")}))
    return 0 if result["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
